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

from vrum.common import (
    Refusal,
    check_choice,
    fetch_all,
    fetch_one,
    page_size,
    parse_node_ref,
    parse_node_refs,
    split_node_ref,
)
from vrum.db import Database
from vrum.embeddings import BATCH_SIZE, EmbeddingRejected, EmbeddingUnavailable, vector_literal
from vrum.graph import require_node
from vrum.indexing import Indexer, IndexSnapshot, snapshot
from vrum.items import NamedItem, named
from vrum.records import headline_of

ITEM_TYPES = ("epic", "story", "task", "rule", "note")
# Every status any of them can have, so a filter can be checked in plain words.
STATUSES = (
    "new",
    "ready",
    "in_progress",
    "blocked",
    "done",
    "dropped",
    "open",
    "met",
    "unconfirmed",
    "confirmed",
    "superseded",
    "withdrawn",
)
SEARCH_MODES = ("both", "words", "meaning")
OVERLAP_TYPES = ("story", "epic", "task", "rule")
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
        "or was built with another model: wait a few seconds, or ask whoever runs the server)."
    )
    note: str = Field(description="In plain words, what that means for these results.")
    waiting_to_index: int = Field(
        description="How many items are new or changed and not yet in the index. They are found "
        "by exact words at once, and by meaning once they are indexed."
    )


class SearchResult(NamedItem):
    title: str
    status: str = Field(
        description="Its status now, read from the graph. For a note: unconfirmed, confirmed, "
        "superseded or withdrawn."
    )
    parent: str | None = Field(
        description="Its parent now, as a ref: the epic of a story, the story of a task, or the "
        "story or task an acceptance criterion is on."
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
        description="True when this is a suggestion rather than an exact match: it matched by "
        "meaning only."
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


class OverlapItem(NamedItem):
    title: str
    status: str
    epic: str | None = Field(
        description="The ref of its epic (itself, for an epic). Empty for an acceptance "
        "criterion that is not placed on a story or task."
    )
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
    # An epic, story or task's number within its kind (the type says which); anything else's id.
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
        indexed: what every search says about the index, for whoever wants only that. A service
        that the background job could not reach the last time it tried counts as out of reach."""
        client = self._indexer.client
        async with self._database.transaction() as conn:
            state = await snapshot(conn, client.model if client else None)
        if client is None:
            return self._index_note(state, "not_configured")
        if not state.usable_with(client.model):
            return self._index_note(state, "index_not_ready")
        last = self._indexer.last_run
        if last is not None and last.error:
            return self._index_note(state, "service_unreachable")
        return self._index_note(state, "used")

    def _index_note(self, state: IndexSnapshot, meaning: str, note: str | None = None) -> IndexNote:
        notes = {
            "used": "Meaning was used. Matches by meaning are suggestions.",
            "not_requested": "Only exact words were searched, as you asked.",
            "not_configured": "No embedding service is set up, so only exact words were searched.",
            "service_unreachable": "The embedding service could not be reached, so only exact "
            "words were searched.",
            "index_not_ready": "The index is not ready for this model, so only exact words were "
            "searched. Wait a few seconds, or ask whoever runs the server.",
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

    async def vector_of(self, text: str) -> str | None:
        """The vector the index would hold for a text, or None when the embedding service cannot
        be used just now."""
        model = self._indexer.client.model if self._indexer.client else None
        async with self._database.transaction() as conn:
            state = await snapshot(conn, model)
        vector, _, _ = await self._vector(state, text, wanted=True)
        return vector

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
            details = await _details(conn, [(c.item_type, c.id) for c in page], text, statuses_of)
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
        node: str | None,
        rule_id: int | None,
        note_id: int | None,
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
                ("node", node),
                ("rule_id", rule_id),
                ("note_id", note_id),
                ("text", text),
            )
            if value is not None
        ]
        if len(given) != 1:
            raise Refusal(
                "Give exactly one of node, rule_id, note_id or text: the thing to find similar "
                "items for."
            )
        if node is not None:
            node = parse_node_ref(node, "node")
        wanted_types = _check_types(types)
        floor = _check_similarity(min_similarity, self._min_similarity, "min_similarity")
        found = await self._similar(
            node=node,
            rule_id=rule_id,
            note_id=note_id,
            text=_clean_query(text, "text") if text is not None else None,
            wanted_types=wanted_types,
            floor=floor,
            exclude=set(),
            in_force_only=False,
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
        node: str | None = None,
        rule_id: int | None = None,
        wanted_types: list[str],
        exclude: set[tuple[str, int]],
        limit: int,
        timeout: float | None = None,
    ) -> tuple[list[SearchResult], int, IndexNote]:
        """Active items like a node (given by its ref) or rule, for a recall or the context of
        work: what is alike apart from the exact links. Never refuses: when meaning is not
        possible, the note says so. `exclude` holds (type, number) pairs: the number of an epic,
        story or task within its kind, the id of anything else. `timeout` is how long to wait for
        the embedding service."""
        found = await self._similar(
            node=node,
            rule_id=rule_id,
            note_id=None,
            text=None,
            wanted_types=wanted_types,
            floor=self._min_similarity,
            exclude=exclude,
            in_force_only=True,
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
        node: str | None,
        rule_id: int | None,
        note_id: int | None,
        text: str | None,
        wanted_types: list[str] | None,
        floor: float,
        exclude: set[tuple[str, int]],
        in_force_only: bool,
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
            source = await _source(conn, node, rule_id, note_id, text)
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
            if in_force_only:
                in_force = await _in_force(conn, [(c.item_type, c.id) for c in candidates])
                candidates = [c for c in candidates if (c.item_type, c.id) in in_force]
            pairs = [(c.item_type, c.id) for c in candidates]
            details = await _details(conn, pairs, None)
            statuses_of = {pair: row["status"] for pair, row in details.items()}
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
        epics: list[str] | None,
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
        scoped = parse_node_refs(epics, "epics")
        not_epics = [ref for ref in scoped if not ref.startswith("epic:")]
        if not_epics:
            raise Refusal(
                f"epics must name epics, such as epic:3, but {', '.join(not_epics)} is not an epic."
            )
        client = self._indexer.client
        model = client.model if client else None
        async with self._database.transaction() as conn:
            state = await snapshot(conn, model)
            if client is None or not state.usable_with(client.model):
                note = self._index_note(
                    state, "not_configured" if client is None else "index_not_ready"
                )
                raise Refusal(f"Finding overlaps needs the index, which is not ready. {note.note}")
            for epic in scoped:
                await require_node(conn, epic)
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
            numbers = sorted({row["a_id"] for row in rows} | {row["b_id"] for row in rows})
            described = await _overlap_items(conn, item_type, numbers)
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
        wanted = [(kind, item_id) for kind, item_id in pairs if kind in kinds]
        if not wanted:
            continue
        params = {"ids": [item_id for _, item_id in wanted], "refs": _refs(wanted)}
        for row in await fetch_all(conn, sql, params):
            found[(row["item_type"], row["id"])] = row["status"]
    return found


def _refs(pairs: list[tuple[str, int]]) -> list[str]:
    """The refs of epics, stories and tasks given as (kind, number), to look them up by."""
    return [f"{kind}:{number}" for kind, number in pairs]


# Computed status of a node (same rule as node_state.status, without the unrelated lateral joins
# for rules, points, spans, checks or claims; short-circuits for non-new rows).
_NODE_STATUS_SQL = (
    "CASE WHEN {n}.status = 'new' "
    "AND NOT EXISTS (SELECT 1 FROM links l "
    "JOIN nodes t ON t.project_id = l.project_id AND t.ref = l.target "
    "WHERE l.project_id = {n}.project_id AND l.source = {n}.ref "
    "AND l.link_type = 'needs_first' AND t.status <> 'done') "
    "AND NOT EXISTS (SELECT 1 FROM gate_state g "
    "WHERE g.project_id = {n}.project_id AND g.node = {n}.ref "
    "AND g.state IN ('waiting', 'refused')) "
    "AND NOT EXISTS (SELECT 1 FROM plan_state p "
    "WHERE p.project_id = {n}.project_id AND p.state IN ('proposed', 'changes_requested') "
    "AND p.items ? {n}.ref) "
    "THEN 'ready' ELSE {n}.status END"
)

_STATUS_QUERIES = (
    (
        _NODE_TYPES,
        f"SELECT n.kind AS item_type, n.number AS id, {_NODE_STATUS_SQL.format(n='n')} AS status "
        "FROM nodes n WHERE n.ref = ANY(%(refs)s)",
    ),
    (("rule",), "SELECT 'rule' AS item_type, id, status FROM rules WHERE id = ANY(%(ids)s)"),
    (("note",), "SELECT 'note' AS item_type, id, status FROM notes WHERE id = ANY(%(ids)s)"),
)


async def _in_force(conn: AsyncConnection, pairs: list[tuple[str, int]]) -> set[tuple[str, int]]:
    """Which of these are in force: a note that is unconfirmed or confirmed, and any work or
    acceptance criterion."""
    keep: set[tuple[str, int]] = set()
    ids = [i for kind, i in pairs if kind == "note"]
    if ids:
        for row in await fetch_all(
            conn,
            "SELECT id FROM notes WHERE id = ANY(%s) AND status IN ('unconfirmed', 'confirmed')",
            (ids,),
        ):
            keep.add(("note", row["id"]))
    keep.update(pair for pair in pairs if pair[0] in ("epic", "story", "task", "rule"))
    return keep


_HEADLINE = (
    "CASE WHEN %(q)s::text IS NOT NULL AND {search} @@ websearch_to_tsquery('english', %(q)s) "
    "THEN ts_headline('english', {content}, websearch_to_tsquery('english', %(q)s), %(opts)s) END"
)


async def _details(
    conn: AsyncConnection,
    pairs: list[tuple[str, int]],
    query: str | None,
    statuses: dict[tuple[str, int], str] | None = None,
) -> dict[tuple[str, int], dict[str, Any]]:
    """What a result shows about each item, read from the graph now. When `statuses` was already
    computed (by `search`), nodes skip recomputing ready status."""
    found: dict[tuple[str, int], dict[str, Any]] = {}
    params = {"q": query, "opts": _HIGHLIGHT}
    node_query = _NODE_DETAIL_RAW if statuses is not None else _NODE_DETAIL_WITH_STATUS
    for kinds, sql in ((_NODE_TYPES, node_query), *_OTHER_DETAIL_QUERIES):
        wanted = [(kind, item_id) for kind, item_id in pairs if kind in kinds]
        if not wanted:
            continue
        rows = await fetch_all(
            conn, sql, {**params, "ids": [item_id for _, item_id in wanted], "refs": _refs(wanted)}
        )
        for row in rows:
            pair = (row["item_type"], row["id"])
            if statuses is not None and pair in statuses:
                row["status"] = statuses[pair]
            found[pair] = row
    return found


_NODE_DETAIL_BASE = (
    "SELECT n.kind AS item_type, n.number AS id, n.title, {status} AS status, n.parent, "
    "p.kind AS parent_kind, p.title AS parent_title, "
    "n.title || E'\\n' || n.body AS content, "
    + _HEADLINE.format(search="n.search", content="n.title || E'\\n' || n.body")
    + " AS headline FROM nodes n LEFT JOIN nodes p ON p.ref = n.parent WHERE n.ref = ANY(%(refs)s)"
)
_NODE_DETAIL_RAW = _NODE_DETAIL_BASE.format(status="n.status")
_NODE_DETAIL_WITH_STATUS = _NODE_DETAIL_BASE.format(status=_NODE_STATUS_SQL.format(n="n"))

_OTHER_DETAIL_QUERIES = (
    (
        ("rule",),
        "SELECT 'rule' AS item_type, r.id, r.title, r.status, r.node AS parent, "
        "p.kind AS parent_kind, p.title AS parent_title, "
        "r.title || E'\\n' || r.body AS content, "
        + _HEADLINE.format(search="r.search", content="r.title || E'\\n' || r.body")
        + " AS headline FROM rules r LEFT JOIN nodes p ON p.ref = r.node "
        "WHERE r.id = ANY(%(ids)s)",
    ),
    (
        ("note",),
        "SELECT 'note' AS item_type, n.id, n.text AS title, n.status, NULL::text AS parent, "
        "NULL::text AS parent_kind, NULL::text AS parent_title, "
        "n.text || E'\\n' || n.why AS content, "
        + _HEADLINE.format(search="n.search", content="n.text || E'\\n' || n.why")
        + " AS headline FROM notes n WHERE n.id = ANY(%(ids)s)",
    ),
)


def _result(row: dict[str, Any], candidate: _Candidate) -> SearchResult:
    headline = row.get("headline")
    marked = re.findall(r"«(.*?)»", headline or "")
    words_matched = list(dict.fromkeys(word.lower() for word in marked))
    matched = candidate.matched
    if matched != "meaning" and not headline:
        # It matched by words, but only in a part of the text that has nothing to highlight, such
        # as a note's reason.
        words_matched = []
    why_parts = []
    if matched in ("words", "both"):
        why_parts.append(
            f"exact words: {', '.join(words_matched)}" if words_matched else "exact words"
        )
    if matched in ("meaning", "both") and candidate.similarity is not None:
        why_parts.append(f"similar in meaning (score {candidate.similarity:.2f})")
    item_type = row["item_type"]
    snippet = _squash(headline) if headline else _short(row["content"], 200)
    title = headline_of(row["title"]) if item_type == "note" else row["title"]
    return SearchResult(
        **named(item_type, row["id"]),
        title=title,
        status=row["status"],
        parent=row["parent"],
        parent_kind=row["parent_kind"],
        parent_title=row["parent_title"],
        matched=matched,
        why="; ".join(why_parts),
        words_matched=words_matched,
        similarity=round(candidate.similarity, 3) if candidate.similarity is not None else None,
        suggestion=matched == "meaning",
        snippet=snippet,
    )


@dataclass
class _Source:
    item: tuple[str, int] | None
    text: str


async def _source(
    conn: AsyncConnection,
    node: str | None,
    rule_id: int | None,
    note_id: int | None,
    text: str | None,
) -> _Source:
    """The item or text that similar items are wanted for, as one text. The item is named as the
    index names it: its type and its number (of an epic, story or task within its kind) or id."""
    if text is not None:
        return _Source(None, text)
    if node is not None:
        await require_node(conn, node)
        kind, number = split_node_ref(node)
        row = await fetch_one(
            conn,
            "SELECT btrim(title || E'\\n' || body) AS content FROM nodes WHERE ref = %s",
            (node,),
        )
        assert row is not None
        return _Source((kind, number), row["content"])
    if rule_id is not None:
        row = await fetch_one(
            conn,
            "SELECT 'rule' AS item_type, btrim(title || E'\\n' || body) AS content FROM rules "
            "WHERE id = %s",
            (rule_id,),
        )
        missing = (
            f"There is no acceptance criterion with id {rule_id}. Use list_rules to find the id "
            "you want."
        )
    else:
        row = await fetch_one(
            conn,
            "SELECT 'note' AS item_type, btrim(text || E'\\n' || why) AS content FROM notes "
            "WHERE id = %s",
            (note_id,),
        )
        missing = f"There is no note with id {note_id}. Use list_notes to find the id you want."
    if row is None:
        raise Refusal(missing)
    item_id = rule_id or note_id
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
# since it was indexed is left out until it is indexed again. `id` is the number of an epic,
# story or task within its kind, or a rule's id; `epic` is the ref of the item's epic.
_OVERLAP_ITEMS = {
    "epic": (
        "SELECT i.item_id AS id, i.embedding, n.ref AS epic FROM semantic_index i "
        "JOIN index_source s ON s.project_id = i.project_id AND s.item_type = i.item_type "
        "AND s.id = i.item_id "
        "AND s.content_hash = i.content_hash JOIN nodes n ON n.project_id = i.project_id "
        "AND n.kind = 'epic' AND n.number = i.item_id "
        "WHERE i.item_type = 'epic' AND (%(dropped)s OR n.status <> 'dropped')"
    ),
    "story": (
        "SELECT i.item_id AS id, i.embedding, n.parent AS epic FROM semantic_index i "
        "JOIN index_source s ON s.project_id = i.project_id AND s.item_type = i.item_type "
        "AND s.id = i.item_id "
        "AND s.content_hash = i.content_hash JOIN nodes n ON n.project_id = i.project_id "
        "AND n.kind = 'story' AND n.number = i.item_id "
        "WHERE i.item_type = 'story' AND (%(dropped)s OR n.status <> 'dropped')"
    ),
    "task": (
        "SELECT i.item_id AS id, i.embedding, st.parent AS epic FROM semantic_index i "
        "JOIN index_source s ON s.project_id = i.project_id AND s.item_type = i.item_type "
        "AND s.id = i.item_id "
        "AND s.content_hash = i.content_hash JOIN nodes n ON n.project_id = i.project_id "
        "AND n.kind = 'task' AND n.number = i.item_id "
        "JOIN nodes st ON st.project_id = n.project_id AND st.ref = n.parent "
        "WHERE i.item_type = 'task' AND (%(dropped)s OR n.status <> 'dropped')"
    ),
    "rule": (
        "SELECT i.item_id AS id, i.embedding, "
        "CASE p.kind WHEN 'story' THEN p.parent ELSE ps.parent END AS epic FROM semantic_index i "
        "JOIN index_source s ON s.project_id = i.project_id AND s.item_type = i.item_type "
        "AND s.id = i.item_id "
        "AND s.content_hash = i.content_hash JOIN rules r ON r.project_id = i.project_id "
        "AND r.id = i.item_id "
        "LEFT JOIN nodes p ON p.project_id = r.project_id AND p.ref = r.node "
        "LEFT JOIN nodes ps ON ps.project_id = p.project_id AND ps.ref = p.parent "
        "WHERE i.item_type = 'rule' AND (%(dropped)s OR r.status <> 'dropped')"
    ),
}
_OVERLAP_PAIRS = (
    "FROM cur a JOIN cur b ON a.id < b.id AND a.epic IS DISTINCT FROM b.epic "
    "WHERE (a.embedding <=> b.embedding) <= %(max_distance)s "
    "AND (NOT %(scoped)s OR a.epic = ANY(%(epics)s) OR b.epic = ANY(%(epics)s))"
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
    conn: AsyncConnection, item_type: str, numbers: list[int]
) -> dict[int, OverlapItem]:
    """The items of one type as a result shows them, by number (a rule's id)."""
    if not numbers:
        return {}
    if item_type == "rule":
        sql = (
            "SELECT r.id, r.title, r.status, e.ref AS epic, e.title AS epic_title "
            "FROM rules r LEFT JOIN nodes p ON p.ref = r.node "
            "LEFT JOIN nodes ps ON ps.ref = p.parent "
            "LEFT JOIN nodes e ON e.ref = CASE p.kind WHEN 'story' THEN p.parent "
            "ELSE ps.parent END WHERE r.id = ANY(%(numbers)s)"
        )
    else:
        sql = (
            f"SELECT s.number AS id, s.title, {_NODE_STATUS_SQL.format(n='s')} AS status, "
            "e.ref AS epic, e.title AS epic_title "
            "FROM nodes s LEFT JOIN nodes p ON p.ref = s.parent "
            "JOIN nodes e ON e.ref = CASE s.kind WHEN 'epic' THEN s.ref "
            "WHEN 'story' THEN s.parent ELSE p.parent END "
            "WHERE s.kind = %(item_type)s AND s.number = ANY(%(numbers)s)"
        )
    return {
        row["id"]: OverlapItem(
            **named(item_type, row["id"]),
            title=row["title"],
            status=row["status"],
            epic=row["epic"],
            epic_title=row["epic_title"],
        )
        for row in await fetch_all(conn, sql, {"item_type": item_type, "numbers": numbers})
    }


async def _links_between(
    conn: AsyncConnection, item_type: str, pairs: list[tuple[int, int]]
) -> dict[tuple[int, int], list[str]]:
    """The link types already between each pair of nodes (given by their numbers, of one kind).
    Rules are not linked to each other."""
    if item_type == "rule" or not pairs:
        return {}
    refs = sorted({f"{item_type}:{number}" for pair in pairs for number in pair})
    rows = await fetch_all(
        conn,
        "SELECT source, target, link_type FROM links "
        "WHERE source = ANY(%(refs)s) AND target = ANY(%(refs)s) ORDER BY id",
        {"refs": refs},
    )
    found: dict[tuple[int, int], list[str]] = {}
    for row in rows:
        first, second = split_node_ref(row["source"])[1], split_node_ref(row["target"])[1]
        found.setdefault((min(first, second), max(first, second)), []).append(row["link_type"])
    return found
