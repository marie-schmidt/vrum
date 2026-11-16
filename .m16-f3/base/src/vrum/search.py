"""Search: finding things by exact words, by meaning, or both fused by rank; the items most like a
given item or text; and pairs of items in different epics that look like the same thing.

Everything here only reads. Exact words come from PostgreSQL full-text search over the graph
itself, so they are current the moment a change is saved and work without any embedding service.
Meaning comes from the semantic index, which the reindex job (vrum/indexing.py) keeps; meaning
results are suggestions, labelled as such."""

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import Refusal, check_choice, fetch_all, fetch_one, page_size
from vrum.db import Database
from vrum.embeddings import BATCH_SIZE, EmbeddingRejected, EmbeddingUnavailable, vector_literal
from vrum.indexing import Indexer, IndexSnapshot, snapshot
from vrum.memories import memory_label

ITEM_TYPES = ("epic", "story", "task", "rule", "decision", "principle", "fact", "belief")
# Every status any of them can have, so a filter can be checked in plain words.
STATUSES = (
    "new",
    "ready",
    "in_progress",
    "blocked",
    "done",
    "dropped",
    "held",
    "settled",
    "active",
    "superseded",
    "retracted",
)
SEARCH_MODES = ("both", "words", "meaning")
OVERLAP_TYPES = ("story", "epic", "rule")
# Rank fusion: an item's score is the sum of 1 / (K + its rank) over the lists it is in.
_FUSION_K = 60
_HIGHLIGHT = "StartSel=«, StopSel=», MaxWords=28, MinWords=10, MaxFragments=1, ShortWord=2"
_NODE_TYPES = ("epic", "story", "task")
# How long creating something waits for the embedding service before it goes ahead without the
# check for similar items.
_CREATE_TIMEOUT_SECONDS = 5.0


class IndexNote(BaseModel):
    meaning: str = Field(
        description="How meaning was used: used; not_requested (you asked for words only); "
        "not_configured (no embedding service is set up); service_unreachable (it could not be "
        "reached, so only exact words were used); or index_not_ready (the index is not built yet "
        "or was built with another model; run reindex)."
    )
    note: str = Field(description="In plain words, what that means for these results.")
    waiting_to_index: int = Field(
        description="How many items are new or changed and not yet in the index. They are found "
        "by exact words at once, and by meaning once they are indexed."
    )


class SearchResult(BaseModel):
    item_type: str = Field(
        description="epic, story, task, rule, decision, principle, fact or belief."
    )
    id: int = Field(
        description="Its id, in the id space of its type: a node, rule, decision, "
        "or fact or belief."
    )
    title: str
    label: str = Field(
        description="What this is, in plain words, so a belief or an extracted fact is never "
        "mistaken for a decision."
    )
    origin: str | None = Field(
        description="For a fact or belief: stated, extracted or derived. For a decision or "
        "principle: decided, meaning a named person made it. Empty for work and rules."
    )
    status: str = Field(description="Its status now, read from the graph.")
    parent_id: int | None = Field(
        description="Its parent now: the epic of a story, the story of a task or a rule."
    )
    parent_kind: str | None
    parent_title: str | None
    matched: str = Field(
        description="words (exact words matched), meaning (similar in meaning only) or both."
    )
    why: str = Field(description="Why it matched: the words, or the similarity score, or both.")
    words_matched: list[str] = Field(description="The words in its text that matched the query.")
    similarity: float | None = Field(
        description="How alike in meaning, from 0 to 1. Empty when it did not match by meaning."
    )
    suggestion: bool = Field(
        description="True when this is a suggestion rather than a record or an exact match: it "
        "matched by meaning only, or it is a belief, or a model wrote it."
    )
    snippet: str = Field(description="A piece of its text. Matched words are between « and ».")


class SearchPage(BaseModel):
    items: list[SearchResult]
    total: int = Field(description="How many results there are in all, after the filters.")
    counts_by_type: dict[str, int] = Field(
        description="How many matches there are of each type, before the type and status "
        "filters, so you can see what filtering would give."
    )
    counts_by_status: dict[str, int] = Field(
        description="How many matches have each status, before the filters."
    )
    next_offset: int | None = Field(
        description="Pass this as offset to get the next page. Empty when there are no more."
    )
    index: IndexNote


class OverlapItem(BaseModel):
    item_type: str
    id: int
    title: str
    status: str
    epic_id: int | None
    epic_title: str | None


class OverlapPair(BaseModel):
    similarity: float = Field(description="How alike in meaning, from 0 to 1.")
    first: OverlapItem
    second: OverlapItem
    already_linked: list[str] = Field(
        description="The link types already between these two nodes (such as duplicates). Empty "
        "when nobody has linked them."
    )


class OverlapPage(BaseModel):
    items: list[OverlapPair]
    total: int = Field(description="How many pairs there are in all, across pages.")
    threshold: float = Field(description="The similarity a pair had to reach to be reported.")
    next_offset: int | None = Field(
        description="Pass this as offset to get the next page. Empty when there are no more."
    )
    index: IndexNote


@dataclass
class _Candidate:
    item_type: str
    id: int
    words_rank: int | None = None
    meaning_rank: int | None = None
    similarity: float | None = None

    @property
    def score(self) -> float:
        total = 0.0
        for rank in (self.words_rank, self.meaning_rank):
            if rank is not None:
                total += 1.0 / (_FUSION_K + rank)
        return total

    @property
    def matched(self) -> str:
        if self.words_rank is not None and self.meaning_rank is not None:
            return "both"
        return "words" if self.words_rank is not None else "meaning"


def _clean_query(text: str, label: str) -> str:
    cleaned = text.replace("\x00", "").strip()
    if not cleaned:
        raise Refusal(f"{label} cannot be empty. Say what to look for.")
    return cleaned


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _short(text: str, length: int) -> str:
    text = _squash(text)
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"


def _check_types(types: list[str] | None) -> list[str] | None:
    for item_type in types or []:
        check_choice("types", item_type, ITEM_TYPES)
    return list(dict.fromkeys(types)) if types else None


def _check_statuses(statuses: list[str] | None) -> list[str] | None:
    for status in statuses or []:
        check_choice("statuses", status, STATUSES)
    return list(dict.fromkeys(statuses)) if statuses else None


def _check_similarity(value: float | None, default: float, label: str) -> float:
    chosen = default if value is None else value
    if not 0.0 <= chosen <= 1.0:
        raise Refusal(f"{label} must be a number from 0 to 1, but it is {chosen}.")
    return chosen


class Searcher:
    def __init__(self, database: Database, indexer: Indexer, min_similarity: float, overlap: float):
        self._database = database
        self._indexer = indexer
        self._min_similarity = min_similarity
        self._overlap_threshold = overlap

    # -- what the index can do right now ------------------------------------------------------

    async def index_state(self) -> IndexNote:
        """Whether search by meaning can be used right now, and how many items are waiting to be
        indexed: what every search says about the index, for whoever wants only that."""
        client = self._indexer.client
        async with self._database.transaction() as conn:
            state = await snapshot(conn, client.model if client else None)
        if client is None:
            return self._index_note(state, "not_configured")
        return self._index_note(
            state, "used" if state.usable_with(client.model) else "index_not_ready"
        )

    def _index_note(self, state: IndexSnapshot, meaning: str, note: str | None = None) -> IndexNote:
        notes = {
            "used": "Meaning was used. Matches by meaning are suggestions.",
            "not_requested": "Only exact words were searched, as you asked.",
            "not_configured": "No embedding service is set up, so only exact words were searched.",
            "service_unreachable": "The embedding service could not be reached, so only exact "
            "words were searched.",
            "index_not_ready": "The index is not ready for this model, so only exact words were "
            "searched. Run reindex, or wait a few seconds.",
        }
        text = note or notes[meaning]
        if state.waiting and meaning == "used":
            text += (
                f" {state.waiting} items are waiting to be indexed and are not searched by "
                "meaning yet."
            )
        return IndexNote(meaning=meaning, note=text, waiting_to_index=state.waiting)

    async def _vector(
        self, state: IndexSnapshot, text: str, *, wanted: bool, timeout: float | None = None
    ) -> tuple[str | None, str, str | None]:
        """The vector for a text, or why there is none: (vector, state of meaning, detail)."""
        client = self._indexer.client
        if not wanted:
            return None, "not_requested", None
        if client is None:
            return None, "not_configured", None
        if not state.usable_with(client.model):
            return None, "index_not_ready", None
        max_chars = self._indexer.max_chars
        try:
            (vector,) = await client.embed([text[:max_chars]], timeout=timeout)
        except EmbeddingUnavailable as error:
            return None, "service_unreachable", f"{error} Only exact words were searched."
        except EmbeddingRejected as error:
            return None, "service_unreachable", f"{error} Only exact words were searched."
        if len(vector) != state.dimensions:
            return None, "index_not_ready", None
        return vector_literal(vector), "used", None

    # -- searching ----------------------------------------------------------------------------

    async def search(
        self,
        *,
        query: str,
        mode: str,
        types: list[str] | None,
        statuses: list[str] | None,
        min_similarity: float | None,
        limit: int,
        offset: int,
    ) -> SearchPage:
        text = _clean_query(query, "query")
        check_choice("mode", mode, SEARCH_MODES)
        limit = page_size(limit)
        if offset < 0:
            raise Refusal(f"offset must be 0 or more, but it is {offset}.")
        wanted_types, wanted_statuses = _check_types(types), _check_statuses(statuses)
        floor = _check_similarity(min_similarity, self._min_similarity, "min_similarity")
        model = self._indexer.client.model if self._indexer.client else None
        async with self._database.transaction() as conn:
            state = await snapshot(conn, model)
        vector, meaning, detail = await self._vector(state, text, wanted=mode != "words")
        if mode == "meaning" and vector is None:
            note = self._index_note(state, meaning, detail)
            raise Refusal(f"Searching by meaning is not possible now. {note.note}")

        async with self._database.transaction() as conn:
            words: list[tuple[str, int]] = []
            if mode != "meaning":
                words = await _word_matches(conn, text)
            neighbours: list[tuple[str, int, float]] = []
            if vector is not None:
                neighbours = await _neighbours(conn, vector, floor, wanted_types=None)
            candidates = _fuse(words, neighbours)
            kept = [
                candidate
                for candidate in candidates
                if wanted_types is None or candidate.item_type in wanted_types
            ]
            statuses_of = await _statuses(conn, [(c.item_type, c.id) for c in candidates])
            counts_type = Counter(candidate.item_type for candidate in candidates)
            counts_status = Counter(
                statuses_of.get((candidate.item_type, candidate.id), "unknown")
                for candidate in candidates
            )
            if wanted_statuses is not None:
                kept = [
                    candidate
                    for candidate in kept
                    if statuses_of.get((candidate.item_type, candidate.id)) in wanted_statuses
                ]
            page = kept[offset : offset + limit]
            details = await _details(conn, [(c.item_type, c.id) for c in page], text)
            empty_words = mode != "meaning" and not words and await _query_has_no_words(conn, text)
        note = self._index_note(state, meaning, detail)
        if empty_words:
            note.note += (
                " None of the words in the query can be searched by exact words (they are too "
                "common)."
            )
        return SearchPage(
            items=[_result(details[(c.item_type, c.id)], c) for c in page],
            total=len(kept),
            counts_by_type=dict(sorted(counts_type.items())),
            counts_by_status=dict(sorted(counts_status.items())),
            next_offset=offset + limit if offset + limit < len(kept) else None,
            index=note,
        )

    # -- similar items ------------------------------------------------------------------------

    async def similar_items(
        self,
        *,
        node_id: int | None,
        rule_id: int | None,
        decision_id: int | None,
        memory_id: int | None,
        text: str | None,
        types: list[str] | None,
        min_similarity: float | None,
        limit: int,
        offset: int,
    ) -> SearchPage:
        limit = page_size(limit)
        if offset < 0:
            raise Refusal(f"offset must be 0 or more, but it is {offset}.")
        given = [
            name
            for name, value in (
                ("node_id", node_id),
                ("rule_id", rule_id),
                ("decision_id", decision_id),
                ("memory_id", memory_id),
                ("text", text),
            )
            if value is not None
        ]
        if len(given) != 1:
            raise Refusal(
                "Give exactly one of node_id, rule_id, decision_id, memory_id or text: the thing "
                "to find similar items for."
            )
        wanted_types = _check_types(types)
        floor = _check_similarity(min_similarity, self._min_similarity, "min_similarity")
        found = await self._similar(
            node_id=node_id,
            rule_id=rule_id,
            decision_id=decision_id,
            memory_id=memory_id,
            text=_clean_query(text, "text") if text is not None else None,
            wanted_types=wanted_types,
            floor=floor,
            exclude=set(),
            active_only=False,
            required=True,
        )
        candidates, details, statuses_of, note = found
        counts_type = Counter(c.item_type for c in candidates)
        counts_status = Counter(statuses_of.get((c.item_type, c.id), "unknown") for c in candidates)
        page = candidates[offset : offset + limit]
        return SearchPage(
            items=[_result(details[(c.item_type, c.id)], c) for c in page],
            total=len(candidates),
            counts_by_type=dict(sorted(counts_type.items())),
            counts_by_status=dict(sorted(counts_status.items())),
            next_offset=offset + limit if offset + limit < len(candidates) else None,
            index=note,
        )

    async def similar(
        self,
        *,
        node_id: int | None = None,
        rule_id: int | None = None,
        wanted_types: list[str],
        exclude: set[tuple[str, int]],
        limit: int,
        timeout: float | None = None,
    ) -> tuple[list[SearchResult], int, IndexNote]:
        """Active items like a node or rule, for a recall or the context of work: what is alike
        apart from the exact links. Never refuses: when meaning is not possible, the note says
        so. `timeout` is how long to wait for the embedding service."""
        found = await self._similar(
            node_id=node_id,
            rule_id=rule_id,
            decision_id=None,
            memory_id=None,
            text=None,
            wanted_types=wanted_types,
            floor=self._min_similarity,
            exclude=exclude,
            active_only=True,
            required=False,
            timeout=timeout,
        )
        candidates, details, _, note = found
        return (
            [_result(details[(c.item_type, c.id)], c) for c in candidates[:limit]],
            len(candidates),
            note,
        )

    async def _similar(
        self,
        *,
        node_id: int | None,
        rule_id: int | None,
        decision_id: int | None,
        memory_id: int | None,
        text: str | None,
        wanted_types: list[str] | None,
        floor: float,
        exclude: set[tuple[str, int]],
        active_only: bool,
        required: bool,
        timeout: float | None = None,
    ) -> tuple[
        list[_Candidate],
        dict[tuple[str, int], dict[str, Any]],
        dict[tuple[str, int], str],
        IndexNote,
    ]:
        client = self._indexer.client
        model = client.model if client else None
        async with self._database.transaction() as conn:
            state = await snapshot(conn, model)
            source = await _source(conn, node_id, rule_id, decision_id, memory_id, text)
            stored = None
            if source.item is not None and state.usable_with(model or ""):
                stored = await _stored_vector(conn, source.item)
        vector: str | None
        detail: str | None
        if stored is not None:
            vector, meaning, detail = stored, "used", None
        else:
            vector, meaning, detail = await self._vector(
                state, source.text, wanted=True, timeout=timeout
            )
        if vector is None:
            note = self._index_note(state, meaning, detail)
            if required:
                raise Refusal(
                    f"Finding similar items needs meaning, which is not possible now. {note.note}"
                )
            return [], {}, {}, note
        async with self._database.transaction() as conn:
            neighbours = await _neighbours(conn, vector, floor, wanted_types=wanted_types)
            candidates = [
                _Candidate(
                    item_type=item_type, id=item_id, meaning_rank=rank, similarity=similarity
                )
                for rank, (item_type, item_id, similarity) in enumerate(neighbours, start=1)
                if (item_type, item_id) != source.item and (item_type, item_id) not in exclude
            ]
            if active_only:
                active = await _active(conn, [(c.item_type, c.id) for c in candidates])
                candidates = [c for c in candidates if (c.item_type, c.id) in active]
            pairs = [(c.item_type, c.id) for c in candidates]
            statuses_of = await _statuses(conn, pairs)
            details = await _details(conn, pairs, None)
        return candidates, details, statuses_of, self._index_note(state, "used")

    # -- before creating ----------------------------------------------------------------------

    async def possible_duplicates(
        self,
        texts: list[str],
        *,
        types: list[str],
        exclude: set[tuple[str, int]] | None = None,
        limit: int = 3,
    ) -> tuple[list[list[SearchResult]], IndexNote]:
        """For each text, the existing items of these types that look like the same thing (at
        least as alike as the overlap threshold), nearest first, as suggestions, leaving out
        `exclude` and dropped items. It is a check before or after creating, so it never waits
        long for the embedding service and never refuses: when meaning cannot be used the note
        says so and every list is empty."""
        left_out = exclude or set()
        client = self._indexer.client
        model = client.model if client else None
        async with self._database.transaction() as conn:
            state = await snapshot(conn, model)
        empty: list[list[SearchResult]] = [[] for _ in texts]
        if client is None:
            return empty, self._index_note(state, "not_configured")
        if not state.usable_with(client.model):
            return empty, self._index_note(state, "index_not_ready")
        if not texts:
            return empty, self._index_note(state, "used")
        vectors: list[list[float]] = []
        try:
            for start in range(0, len(texts), BATCH_SIZE):
                batch = [
                    text[: self._indexer.max_chars] for text in texts[start : start + BATCH_SIZE]
                ]
                vectors.extend(await client.embed(batch, timeout=_CREATE_TIMEOUT_SECONDS))
        except (EmbeddingUnavailable, EmbeddingRejected) as error:
            return empty, self._index_note(
                state, "service_unreachable", f"{error} The check for similar items was skipped."
            )
        if any(len(vector) != state.dimensions for vector in vectors):
            return empty, self._index_note(state, "index_not_ready")
        async with self._database.transaction() as conn:
            neighbours = await _neighbours_many(
                conn, [vector_literal(vector) for vector in vectors], self._overlap_threshold, types
            )
            found = [
                [
                    _Candidate(kind, item_id, meaning_rank=rank, similarity=similarity)
                    for rank, (kind, item_id, similarity) in enumerate(nearest, start=1)
                    if (kind, item_id) not in left_out
                ][:25]
                for nearest in neighbours
            ]
            pairs = sorted({(c.item_type, c.id) for candidates in found for c in candidates})
            details = await _details(conn, pairs, None)
        results = [
            [
                _result(details[(c.item_type, c.id)], c)
                for c in candidates
                if details[(c.item_type, c.id)]["status"] != "dropped"
            ][:limit]
            for candidates in found
        ]
        return results, self._index_note(state, "used")

    # -- overlaps -----------------------------------------------------------------------------

    async def find_overlaps(
        self,
        *,
        item_type: str,
        epic_ids: list[int] | None,
        min_similarity: float | None,
        include_dropped: bool,
        limit: int,
        offset: int,
    ) -> OverlapPage:
        check_choice("item_type", item_type, OVERLAP_TYPES)
        limit = page_size(limit)
        if offset < 0:
            raise Refusal(f"offset must be 0 or more, but it is {offset}.")
        threshold = _check_similarity(min_similarity, self._overlap_threshold, "min_similarity")
        scoped = list(dict.fromkeys(epic_ids or []))
        client = self._indexer.client
        model = client.model if client else None
        async with self._database.transaction() as conn:
            state = await snapshot(conn, model)
            if client is None or not state.usable_with(client.model):
                note = self._index_note(
                    state, "not_configured" if client is None else "index_not_ready"
                )
                raise Refusal(f"Finding overlaps needs the index, which is not ready. {note.note}")
            if scoped:
                found = {
                    row["id"]
                    for row in await fetch_all(
                        conn,
                        "SELECT id FROM nodes WHERE id = ANY(%s) AND kind = 'epic'",
                        (scoped,),
                    )
                }
                missing = [str(epic) for epic in scoped if epic not in found]
                if missing:
                    raise Refusal(
                        f"There is no epic with id {', '.join(missing)}. Use list_nodes with "
                        "kind epic to find the id you want."
                    )
            params = {
                "dropped": include_dropped,
                "max_distance": 1.0 - threshold,
                "scoped": bool(scoped),
                "epics": scoped,
                "limit": limit,
                "offset": offset,
            }
            rows = await fetch_all(conn, _OVERLAPS[item_type], params)
            total = int(rows[0]["total"]) if rows else 0
            if not rows and offset > 0:
                counted = await fetch_one(conn, _OVERLAP_COUNTS[item_type], params)
                total = int(counted["total"]) if counted else 0
            ids = sorted({row["a_id"] for row in rows} | {row["b_id"] for row in rows})
            described = await _overlap_items(conn, item_type, ids)
            links = await _links_between(conn, item_type, [(r["a_id"], r["b_id"]) for r in rows])
        return OverlapPage(
            items=[
                OverlapPair(
                    similarity=round(float(row["similarity"]), 3),
                    first=described[row["a_id"]],
                    second=described[row["b_id"]],
                    already_linked=links.get((row["a_id"], row["b_id"]), []),
                )
                for row in rows
            ],
            total=total,
            threshold=threshold,
            next_offset=offset + limit if offset + limit < total else None,
            index=self._index_note(state, "used"),
        )


# -- the queries ------------------------------------------------------------------------------


async def _word_matches(conn: AsyncConnection, text: str) -> list[tuple[str, int]]:
    rows = await fetch_all(
        conn,
        "SELECT d.item_type, d.id, ts_rank(d.search, q.query) AS rank "
        "FROM search_documents d "
        "CROSS JOIN (SELECT websearch_to_tsquery('english', %(q)s) AS query) q "
        "WHERE d.search @@ q.query ORDER BY rank DESC, d.item_type, d.id",
        {"q": text},
    )
    return [(row["item_type"], row["id"]) for row in rows]


async def _query_has_no_words(conn: AsyncConnection, text: str) -> bool:
    row = await fetch_one(
        conn, "SELECT numnode(websearch_to_tsquery('english', %s)) = 0 AS empty", (text,)
    )
    return bool(row and row["empty"])


async def _neighbours(
    conn: AsyncConnection, vector: str, floor: float, *, wanted_types: list[str] | None
) -> list[tuple[str, int, float]]:
    """Indexed items whose meaning is at least `floor` like the vector, nearest first. An item
    changed since it was indexed is left out until it is indexed again."""
    rows = await fetch_all(
        conn,
        "SELECT i.item_type, i.item_id, 1 - (i.embedding <=> %(v)s::vector) AS similarity "
        "FROM semantic_index i JOIN index_source s ON s.project_id = i.project_id "
        "AND s.item_type = i.item_type "
        "AND s.id = i.item_id AND s.content_hash = i.content_hash "
        "WHERE (i.embedding <=> %(v)s::vector) <= %(max_distance)s "
        "AND (%(types)s::text[] IS NULL OR i.item_type = ANY(%(types)s)) "
        "ORDER BY i.embedding <=> %(v)s::vector, i.item_type, i.item_id",
        {"v": vector, "max_distance": 1.0 - floor, "types": wanted_types},
    )
    return [(row["item_type"], row["item_id"], float(row["similarity"])) for row in rows]


async def _neighbours_many(
    conn: AsyncConnection, vectors: list[str], floor: float, wanted_types: list[str]
) -> list[list[tuple[str, int, float]]]:
    """For each vector, the indexed items of these types at least `floor` like it, nearest
    first. One statement for all of them, so that what is current in the index is worked out
    once and not once per vector."""
    rows = await fetch_all(
        conn,
        "WITH current AS MATERIALIZED (SELECT i.item_type, i.item_id, i.embedding "
        "FROM semantic_index i JOIN index_source s ON s.project_id = i.project_id "
        "AND s.item_type = i.item_type "
        "AND s.id = i.item_id AND s.content_hash = i.content_hash "
        "WHERE i.item_type = ANY(%(types)s)) "
        "SELECT q.n, c.item_type, c.item_id, 1 - (c.embedding <=> q.v::vector) AS similarity "
        "FROM unnest(%(vectors)s::text[]) WITH ORDINALITY AS q(v, n) "
        "CROSS JOIN current c WHERE (c.embedding <=> q.v::vector) <= %(max_distance)s "
        "ORDER BY q.n, c.embedding <=> q.v::vector, c.item_type, c.item_id",
        {"types": wanted_types, "vectors": vectors, "max_distance": 1.0 - floor},
    )
    found: list[list[tuple[str, int, float]]] = [[] for _ in vectors]
    for row in rows:
        found[int(row["n"]) - 1].append(
            (row["item_type"], row["item_id"], float(row["similarity"]))
        )
    return found


def _fuse(
    words: list[tuple[str, int]], neighbours: list[tuple[str, int, float]]
) -> list[_Candidate]:
    found: dict[tuple[str, int], _Candidate] = {}
    for rank, (item_type, item_id) in enumerate(words, start=1):
        found[(item_type, item_id)] = _Candidate(item_type, item_id, words_rank=rank)
    for rank, (item_type, item_id, similarity) in enumerate(neighbours, start=1):
        candidate = found.setdefault((item_type, item_id), _Candidate(item_type, item_id))
        candidate.meaning_rank = rank
        candidate.similarity = similarity
    # Best first. Of equal scores, what matched both ways, then the lowest id, so the order is
    # always the same.
    return sorted(
        found.values(),
        key=lambda c: (-c.score, c.matched != "both", c.item_type, c.id),
    )


async def _statuses(
    conn: AsyncConnection, pairs: list[tuple[str, int]]
) -> dict[tuple[str, int], str]:
    """The status of each item now: for work, with `ready` worked out."""
    found: dict[tuple[str, int], str] = {}
    for kinds, sql in _STATUS_QUERIES:
        ids = [item_id for kind, item_id in pairs if kind in kinds]
        if not ids:
            continue
        for row in await fetch_all(conn, sql, (ids,)):
            found[(row["item_type"], row["id"])] = row["status"]
    return found


_STATUS_QUERIES = (
    (_NODE_TYPES, "SELECT kind AS item_type, id, status FROM node_state WHERE id = ANY(%s)"),
    (("rule",), "SELECT 'rule' AS item_type, id, status FROM rules WHERE id = ANY(%s)"),
    (
        ("decision", "principle"),
        "SELECT kind AS item_type, id, status FROM decisions WHERE id = ANY(%s)",
    ),
    (("fact", "belief"), "SELECT type AS item_type, id, status FROM memories WHERE id = ANY(%s)"),
)


async def _active(conn: AsyncConnection, pairs: list[tuple[str, int]]) -> set[tuple[str, int]]:
    """Which of these are in force: an active decision, an active fact, a belief that is active
    and not stale."""
    keep: set[tuple[str, int]] = set()
    decision_ids = [i for kind, i in pairs if kind in ("decision", "principle")]
    if decision_ids:
        for row in await fetch_all(
            conn,
            "SELECT kind, id FROM decisions WHERE id = ANY(%s) AND status = 'active'",
            (decision_ids,),
        ):
            keep.add((row["kind"], row["id"]))
    memory_ids = [i for kind, i in pairs if kind in ("fact", "belief")]
    if memory_ids:
        for row in await fetch_all(
            conn,
            "SELECT type, id FROM memory_state WHERE id = ANY(%s) AND status = 'active' "
            "AND NOT stale",
            (memory_ids,),
        ):
            keep.add((row["type"], row["id"]))
    keep.update(pair for pair in pairs if pair[0] in ("epic", "story", "task", "rule"))
    return keep


_HEADLINE = (
    "CASE WHEN %(q)s::text IS NOT NULL AND {search} @@ websearch_to_tsquery('english', %(q)s) "
    "THEN ts_headline('english', {content}, websearch_to_tsquery('english', %(q)s), %(opts)s) END"
)


async def _details(
    conn: AsyncConnection, pairs: list[tuple[str, int]], query: str | None
) -> dict[tuple[str, int], dict[str, Any]]:
    """What a result shows about each item, read from the graph now."""
    found: dict[tuple[str, int], dict[str, Any]] = {}
    params = {"q": query, "opts": _HIGHLIGHT}
    for kinds, sql in _DETAIL_QUERIES:
        ids = [item_id for kind, item_id in pairs if kind in kinds]
        if not ids:
            continue
        for row in await fetch_all(conn, sql, {**params, "ids": ids}):
            found[(row["item_type"], row["id"])] = row
    return found


_DETAIL_QUERIES = (
    (
        _NODE_TYPES,
        "SELECT s.kind AS item_type, s.id, s.title, s.status, s.parent_id, p.kind AS parent_kind, "
        "p.title AS parent_title, NULL::text AS origin, initcap(s.kind) AS label, "
        "s.title || E'\\n' || s.body AS content, "
        + _HEADLINE.format(search="n.search", content="s.title || E'\\n' || s.body")
        + " AS headline FROM node_state s JOIN nodes n ON n.id = s.id "
        "LEFT JOIN nodes p ON p.id = s.parent_id WHERE s.id = ANY(%(ids)s)",
    ),
    (
        ("rule",),
        "SELECT 'rule' AS item_type, r.id, r.title, r.status, r.story_id AS parent_id, "
        "'story' AS parent_kind, p.title AS parent_title, NULL::text AS origin, 'Rule' AS label, "
        "r.title || E'\\n' || r.body AS content, "
        + _HEADLINE.format(search="r.search", content="r.title || E'\\n' || r.body")
        + " AS headline FROM rules r LEFT JOIN nodes p ON p.id = r.story_id "
        "WHERE r.id = ANY(%(ids)s)",
    ),
    (
        ("decision", "principle"),
        "SELECT d.kind AS item_type, d.id, d.title, d.status, NULL::bigint AS parent_id, "
        "NULL::text AS parent_kind, NULL::text AS parent_title, 'decided' AS origin, "
        "initcap(d.kind) || ' decided by ' || d.decided_by || ' (' || d.status || ')' AS label, "
        "d.title || E'\\n' || d.text || E'\\n' || d.reason AS content, "
        + _HEADLINE.format(
            search="d.search", content="d.title || E'\\n' || d.text || E'\\n' || d.reason"
        )
        + " AS headline FROM decisions d WHERE d.id = ANY(%(ids)s)",
    ),
    (
        ("fact", "belief"),
        "SELECT m.type AS item_type, m.id, left(m.text, 90) AS title, m.status, "
        "NULL::bigint AS parent_id, NULL::text AS parent_kind, NULL::text AS parent_title, "
        "m.origin, m.type, m.source, m.text AS content, "
        + _HEADLINE.format(search="m.search", content="m.text")
        + " AS headline FROM memories m WHERE m.id = ANY(%(ids)s)",
    ),
)


def _result(row: dict[str, Any], candidate: _Candidate) -> SearchResult:
    headline = row.get("headline")
    marked = re.findall(r"«(.*?)»", headline or "")
    words_matched = list(dict.fromkeys(word.lower() for word in marked))
    matched = candidate.matched
    if matched != "meaning" and not headline:
        # It matched by words, but only in a part of the text that has nothing to highlight, such
        # as a fact's source.
        words_matched = []
    why_parts = []
    if matched in ("words", "both"):
        why_parts.append(
            f"exact words: {', '.join(words_matched)}" if words_matched else "exact words"
        )
    if matched in ("meaning", "both") and candidate.similarity is not None:
        why_parts.append(f"similar in meaning (score {candidate.similarity:.2f})")
    item_type = row["item_type"]
    label = row["label"] if "label" in row else memory_label(row)
    origin = row["origin"]
    snippet = _squash(headline) if headline else _short(row["content"], 200)
    title = _short(row["title"], 90) if item_type in ("fact", "belief") else row["title"]
    return SearchResult(
        item_type=item_type,
        id=row["id"],
        title=title,
        label=label,
        origin=origin,
        status=row["status"],
        parent_id=row["parent_id"],
        parent_kind=row["parent_kind"],
        parent_title=row["parent_title"],
        matched=matched,
        why="; ".join(why_parts),
        words_matched=words_matched,
        similarity=round(candidate.similarity, 3) if candidate.similarity is not None else None,
        suggestion=matched == "meaning"
        or item_type == "belief"
        or origin in ("extracted", "derived"),
        snippet=snippet,
    )


@dataclass
class _Source:
    item: tuple[str, int] | None
    text: str


async def _source(
    conn: AsyncConnection,
    node_id: int | None,
    rule_id: int | None,
    decision_id: int | None,
    memory_id: int | None,
    text: str | None,
) -> _Source:
    """The item or text that similar items are wanted for, as one text."""
    if text is not None:
        return _Source(None, text)
    if node_id is not None:
        row = await fetch_one(
            conn,
            "SELECT kind AS item_type, btrim(title || E'\\n' || body) AS content FROM nodes "
            "WHERE id = %s",
            (node_id,),
        )
        missing = f"There is no node with id {node_id}. Use list_nodes to find the id you want."
    elif rule_id is not None:
        row = await fetch_one(
            conn,
            "SELECT 'rule' AS item_type, btrim(title || E'\\n' || body) AS content FROM rules "
            "WHERE id = %s",
            (rule_id,),
        )
        missing = f"There is no rule with id {rule_id}. Use list_rules to find the id you want."
    elif decision_id is not None:
        row = await fetch_one(
            conn,
            "SELECT kind AS item_type, btrim(title || E'\\n' || text || E'\\n' || reason) AS "
            "content FROM decisions WHERE id = %s",
            (decision_id,),
        )
        missing = (
            f"There is no decision with id {decision_id}. Use list_decisions to find the id you "
            "want."
        )
    else:
        row = await fetch_one(
            conn,
            "SELECT type AS item_type, btrim(text) AS content FROM memories WHERE id = %s",
            (memory_id,),
        )
        missing = (
            f"There is no fact or belief with id {memory_id}. Use list_memories to find the id "
            "you want."
        )
    if row is None:
        raise Refusal(missing)
    item_id = node_id or rule_id or decision_id or memory_id
    assert item_id is not None
    return _Source((row["item_type"], item_id), row["content"])


async def _stored_vector(conn: AsyncConnection, item: tuple[str, int]) -> str | None:
    """The vector the index holds for an item, if it is up to date with the item's text."""
    row = await fetch_one(
        conn,
        "SELECT i.embedding::text AS vector FROM semantic_index i JOIN index_source s "
        "ON s.project_id = i.project_id AND s.item_type = i.item_type AND s.id = i.item_id "
        "AND s.content_hash = i.content_hash "
        "WHERE i.item_type = %s AND i.item_id = %s",
        item,
    )
    return None if row is None else str(row["vector"])


# Pairs of items of one type in different epics that are alike, from the index. An item changed
# since it was indexed is left out until it is indexed again.
_OVERLAP_ITEMS = {
    "epic": (
        "SELECT i.item_id AS id, i.embedding, n.id AS epic_id FROM semantic_index i "
        "JOIN index_source s ON s.project_id = i.project_id AND s.item_type = i.item_type "
        "AND s.id = i.item_id "
        "AND s.content_hash = i.content_hash JOIN nodes n ON n.project_id = i.project_id "
        "AND n.id = i.item_id "
        "WHERE i.item_type = 'epic' AND (%(dropped)s OR n.status <> 'dropped')"
    ),
    "story": (
        "SELECT i.item_id AS id, i.embedding, n.parent_id AS epic_id FROM semantic_index i "
        "JOIN index_source s ON s.project_id = i.project_id AND s.item_type = i.item_type "
        "AND s.id = i.item_id "
        "AND s.content_hash = i.content_hash JOIN nodes n ON n.project_id = i.project_id "
        "AND n.id = i.item_id "
        "WHERE i.item_type = 'story' AND (%(dropped)s OR n.status <> 'dropped')"
    ),
    "rule": (
        "SELECT i.item_id AS id, i.embedding, p.parent_id AS epic_id FROM semantic_index i "
        "JOIN index_source s ON s.project_id = i.project_id AND s.item_type = i.item_type "
        "AND s.id = i.item_id "
        "AND s.content_hash = i.content_hash JOIN rules r ON r.project_id = i.project_id "
        "AND r.id = i.item_id "
        "LEFT JOIN nodes p ON p.project_id = r.project_id AND p.id = r.story_id "
        "WHERE i.item_type = 'rule' AND (%(dropped)s OR r.status <> 'dropped')"
    ),
}
_OVERLAP_PAIRS = (
    "FROM cur a JOIN cur b ON a.id < b.id AND a.epic_id IS DISTINCT FROM b.epic_id "
    "WHERE (a.embedding <=> b.embedding) <= %(max_distance)s "
    "AND (NOT %(scoped)s OR a.epic_id = ANY(%(epics)s) OR b.epic_id = ANY(%(epics)s))"
)
_OVERLAPS = {
    kind: (
        f"WITH cur AS ({items}) SELECT a.id AS a_id, b.id AS b_id, "
        "1 - (a.embedding <=> b.embedding) AS similarity, count(*) OVER () AS total "
        f"{_OVERLAP_PAIRS} ORDER BY a.embedding <=> b.embedding, a.id, b.id "
        "LIMIT %(limit)s OFFSET %(offset)s"
    )
    for kind, items in _OVERLAP_ITEMS.items()
}
_OVERLAP_COUNTS = {
    kind: f"WITH cur AS ({items}) SELECT count(*) AS total {_OVERLAP_PAIRS}"
    for kind, items in _OVERLAP_ITEMS.items()
}


async def _overlap_items(
    conn: AsyncConnection, item_type: str, ids: list[int]
) -> dict[int, OverlapItem]:
    if not ids:
        return {}
    if item_type == "rule":
        sql = (
            "SELECT r.id, r.title, r.status, p.parent_id AS epic_id, e.title AS epic_title "
            "FROM rules r LEFT JOIN nodes p ON p.id = r.story_id "
            "LEFT JOIN nodes e ON e.id = p.parent_id WHERE r.id = ANY(%s)"
        )
    else:
        sql = (
            "SELECT s.id, s.title, s.status, e.id AS epic_id, e.title AS epic_title "
            "FROM node_state s JOIN nodes e ON e.id = CASE s.kind WHEN 'epic' THEN s.id "
            "ELSE s.parent_id END WHERE s.id = ANY(%s)"
        )
    return {
        row["id"]: OverlapItem(
            item_type=item_type,
            id=row["id"],
            title=row["title"],
            status=row["status"],
            epic_id=row["epic_id"],
            epic_title=row["epic_title"],
        )
        for row in await fetch_all(conn, sql, (ids,))
    }


async def _links_between(
    conn: AsyncConnection, item_type: str, pairs: list[tuple[int, int]]
) -> dict[tuple[int, int], list[str]]:
    """The link types already between each pair of nodes. Rules are not linked to each other."""
    if item_type == "rule" or not pairs:
        return {}
    ids = sorted({item for pair in pairs for item in pair})
    rows = await fetch_all(
        conn,
        "SELECT source_id, target_id, link_type FROM links "
        "WHERE source_id = ANY(%(ids)s) AND target_id = ANY(%(ids)s) ORDER BY id",
        {"ids": ids},
    )
    found: dict[tuple[int, int], list[str]] = {}
    for row in rows:
        key = (min(row["source_id"], row["target_id"]), max(row["source_id"], row["target_id"]))
        found.setdefault(key, []).append(row["link_type"])
    return found
