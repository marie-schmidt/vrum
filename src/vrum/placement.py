"""Where work belongs (M43): suggestions by meaning, and proposals that a person decides.

**`suggest_parent`** is a read. For a story or task (or text that is not saved yet) it ranks the
epics (for a story) or stories (for a task) that the work might belong under, nearest first, each
with a score and the items under it that it most resembles, so that a suggestion always shows its
reason. A parent is described the way scripts/parent_accuracy.py measured it: by what it says
about itself and by the average of what it already holds (leaving the item itself, and its own
tasks, out), added together and made one unit long (`both` there). On the invented demo project
that put the right epic first for about three stories in four, and in the first three for nine in
ten, with the built-in model; when the best parent is clearly ahead of the second (`SURE_MARGIN`)
it was right about nine times in ten. Those numbers are in DECISIONS.md, and the script is the way
to measure a project of your own. An epic called Inbox is where unsorted work is kept (a convention
only: nothing in the schema knows it) and is never offered as a parent.

**A placement** is a proposal to move stories or tasks under another parent, or to group stories
under a new epic, made by whoever noticed work in the wrong place (a steward agent, or a person)
and decided by a person. Nothing moves until a person accepts it: an agent only ever proposes. It
is an event (`placement_proposed`) and its decision is another (`placement_accepted` or
`placement_rejected`, a person's), so it is in the history, travels through an export and is never
edited; `placement_state` (schema.sql) reads them. Accepting makes the moves as that person (each
a `move` event that can be undone by moving the item back), skips an item that has moved or
finished since the proposal and says so, and makes the new epic only if something goes under it.
A proposer may have at most `MOST_OPEN` proposals waiting, so a steward cannot flood the person who
has to read them."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from vrum.common import (
    Refusal,
    fetch_all,
    fetch_one,
    missing_node,
    page_size,
    parse_node_ref,
    record_event,
    split_node_ref,
    split_page,
)
from vrum.db import Database
from vrum.gates import clean_reason
from vrum.graph import clean_title, insert_node, move_in, require_node, require_nodes
from vrum.records import require_person
from vrum.search import Searcher

# How far ahead of the second best the best parent has to be to be called sure.
SURE_MARGIN = 0.05
DEFAULT_SUGGESTIONS = 3
MOST_SUGGESTIONS = 10
MOST_LOOK_ALIKES = 2
MOST_ITEMS = 20
# How many proposals one proposer may have waiting for a person.
MOST_OPEN = 25
PLACEMENT_STATES = ("proposed", "accepted", "rejected")
MOST_BODY_CHARS = 20_000
# How many moved items one reading ranks: each is one ranking of every possible parent.
MOST_SCORED = 200

PLACEMENT_COLUMNS = (
    "id, state, items, parent, new_epic, reason, proposed_by, proposed_by_kind, proposed_at, "
    "decided_by, decided_at, decision_reason, applied, skipped, epic"
)
_PARENT_KIND = {"story": "epic", "task": "story"}
_PLURAL = {"epic": "epics", "story": "stories", "task": "tasks"}


class LookAlike(BaseModel):
    ref: str
    title: str
    similarity: float = Field(description="How alike it is to the work, from 0 to 1.")


class ParentCandidate(BaseModel):
    ref: str = Field(description="The epic or story that the work might belong under.")
    title: str
    score: float = Field(
        description="How near the work is to it, from 0 to 1, by what it says about itself and "
        "what it already holds together. Compare scores of one answer with each other."
    )
    own: float = Field(description="The same by what it says about itself alone.")
    held: float | None = Field(
        description="The same by the average of what it already holds. Empty when it holds nothing "
        "else that is indexed."
    )
    holds: int = Field(description="How many indexed stories and tasks it already holds.")
    because: list[LookAlike] = Field(
        description="The items under it that the work most resembles: the reason."
    )


class ParentSuggestions(BaseModel):
    node: str | None = Field(description="The item asked about, or empty when text was given.")
    kind: str = Field(description="story or task: what is being placed.")
    parent_kind: str = Field(description="epic (for a story) or story (for a task).")
    current_parent: str | None = Field(description="Where the item is now, when it is saved.")
    current_rank: int | None = Field(
        description="Where its present parent ranks among all the candidates (1 is the best). "
        "Empty when it is not saved, or its parent is not among those that can be ranked."
    )
    suggestions: list[ParentCandidate] = Field(description="Best first.")
    sure: bool = Field(
        description=f"True when the best is ahead of the second by at least {SURE_MARGIN}: on the "
        "project it was measured on that was right about nine times in ten. Still a suggestion."
    )
    margin: float | None = Field(
        description="How far ahead of the second the best is. Empty with fewer than two."
    )
    inbox: str | None = Field(
        description="The epic called Inbox, where unsorted work is kept by convention. It is "
        "never offered as a parent."
    )
    not_considered: int = Field(
        description="How many possible parents are not indexed yet and so were not ranked."
    )


class PlacementItem(BaseModel):
    ref: str
    title: str
    from_parent: str = Field(description="Where it was when the proposal was made.")


class NewEpic(BaseModel):
    title: str = Field(description="The new epic's title.")
    body: str = Field(default="", description="What it is about, in Markdown. May be empty.")


class SkippedItem(BaseModel):
    ref: str
    why: str


class MoveScore(BaseModel):
    """What the same ranking as suggest_parent says about one moved item and the parent it would
    go under. Evidence for a person, never an approval."""

    ref: str
    similarity: float | None = Field(
        description="The score of the proposed parent for this item, from 0 to 1, exactly as "
        "suggest_parent scores it. Empty when it could not be scored (see note)."
    )
    rank: int | None = Field(
        description="Where the proposed parent ranks among the possible parents (1 is the best)."
    )
    ranked: int | None = Field(description="How many possible parents were ranked.")
    best: str | None = Field(description="The parent that ranks first for this item.")
    best_similarity: float | None = Field(description="Its score.")
    margin: float | None = Field(
        description="How far ahead of the second the best is. Empty with fewer than two."
    )
    sure: bool = Field(
        description=f"True when the best is ahead of the second by at least {SURE_MARGIN}."
    )
    warning: str | None = Field(
        description="Something to weigh before moving it: it is in progress or held, has moved "
        "since, or is finished."
    )
    note: str | None = Field(description="Why there is no score, in plain words.")


class PlacementState(BaseModel):
    id: int = Field(description="The proposal's number: the id of the event that made it.")
    state: str = Field(description="proposed (waiting for a person), accepted or rejected.")
    items: list[PlacementItem]
    parent: str | None = Field(description="The existing parent they would go under.")
    parent_title: str | None = None
    new_epic: NewEpic | None = Field(description="Or the new epic that would be made for them.")
    reason: str = Field(description="Why the proposer thinks so, in words.")
    proposed_by: str
    proposed_by_kind: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    applied: list[str] = Field(description="What an acceptance moved.")
    skipped: list[SkippedItem] = Field(
        description="What an acceptance left, because it had moved or finished since."
    )
    epic: str | None = Field(description="The epic an acceptance made for the group.")
    scores: list[MoveScore] | None = Field(
        description="For a proposal still waiting, read with list_placements: how each item "
        "scores for the proposed parent, in the order of items. Empty (null) where it was not "
        "worked out: in needs_a_person, and for a proposal that was decided."
    )
    scores_note: str | None = Field(
        description="Why there are no scores: search by meaning is not possible now, or the "
        "proposal makes a new epic, so there is no parent to score against."
    )
    routine: bool | None = Field(
        description="True when every item was scored, none has a warning, and the proposed "
        "parent ranks first for every one of them, clearly ahead of the next. A routine "
        "proposal is still decided by a person. Null where scores were not worked out."
    )
    routine_why: str | None = Field(description="Why it is or is not routine, in plain words.")


class PlacementPage(BaseModel):
    items: list[PlacementState]
    total: int = Field(description="How many proposals match in all, across pages.")
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )


def code(ref: str) -> str:
    kind, number = split_node_ref(ref)
    return f"{kind[0].upper()}-{number}"


def placement_from(row: dict[str, Any], titles: dict[str, str]) -> PlacementState:
    items = [PlacementItem.model_validate(item) for item in (row["items"] or [])]
    epic = NewEpic.model_validate(row["new_epic"]) if row["new_epic"] else None
    return PlacementState(
        id=row["id"],
        state=row["state"],
        items=items,
        parent=row["parent"],
        parent_title=titles.get(row["parent"]) if row["parent"] else None,
        new_epic=epic,
        reason=row["reason"],
        proposed_by=row["proposed_by"],
        proposed_by_kind=row["proposed_by_kind"],
        proposed_at=row["proposed_at"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        decision_reason=row["decision_reason"],
        applied=list(row["applied"] or []),
        skipped=[SkippedItem.model_validate(one) for one in (row["skipped"] or [])],
        epic=row["epic"],
        scores=None,
        scores_note=None,
        routine=None,
        routine_why=None,
    )


def _ordinal(number: int) -> str:
    suffix = (
        "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    )
    return f"{number}{suffix}"


def routine_words(
    state: PlacementState, scores: list[MoveScore], note: str | None
) -> tuple[bool, str]:
    """Whether a proposal is routine, and why or why not. Routine means the evidence all agrees:
    every item scored, nothing to weigh, and the proposed parent clearly the best one for each."""
    if state.parent is None:
        return False, "A new epic is a bigger step than moving work under one that exists."
    if note is not None:
        return False, note
    target = code(state.parent)
    for one in scores:
        if one.warning:
            return False, one.warning
        if one.note:
            return False, one.note
        if one.rank != 1:
            where = "cannot be ranked" if one.rank is None else f"ranks {_ordinal(one.rank)}"
            return False, f"{target} {where} for {code(one.ref)}."
        if not one.sure:
            return False, f"For {code(one.ref)}, {target} is not clearly ahead of the next parent."
    many = "the item" if len(scores) == 1 else f"all {len(scores)} items"
    return True, f"{target} is the clearly best parent for {many}."


# The index rows that are up to date with their item's text, joined to the item.
_CURRENT = (
    "JOIN semantic_index {i} ON {i}.project_id = {n}.project_id AND {i}.item_type = {n}.kind "
    "AND {i}.item_id = {n}.number "
    "JOIN index_source {s} ON {s}.project_id = {i}.project_id AND {s}.item_type = {i}.item_type "
    "AND {s}.id = {i}.item_id AND {s}.content_hash = {i}.content_hash"
)

# What can be a parent: the epics or stories that are not dropped, other than an epic called Inbox
# and the stories in it (unsorted work is no place to send work).
_CANDIDATE = (
    "c.kind = %(parent_kind)s AND c.status <> 'dropped' AND lower(btrim(c.title)) <> 'inbox' "
    "AND NOT EXISTS (SELECT 1 FROM nodes pe WHERE pe.ref = c.parent AND pe.kind = 'epic' "
    "AND lower(btrim(pe.title)) = 'inbox')"
)

# Every parent of the kind that can be ranked, with how near the work is to it: by what it says
# about itself (own), by the average of what it holds (held) and by both together (score). What
# it holds is its children and their children, leaving out the work being placed and its own
# tasks. This is the same arithmetic as scripts/parent_accuracy.py (view `both`), which
# tests/test_placement_integration.py holds equal.
_RANKED = (
    "WITH q AS (SELECT %(q)s::vector AS v), "
    "cand AS (SELECT c.ref, c.kind, c.title, l2_normalize(oi.embedding) AS own "
    "FROM nodes c " + _CURRENT.format(i="oi", s="os", n="c") + " "
    "WHERE " + _CANDIDATE + "), "
    "held AS (SELECT c.ref, count(*) AS n, "
    "l2_normalize(avg(l2_normalize(di.embedding))) AS held "
    "FROM cand c JOIN nodes d ON d.parent = c.ref OR d.parent IN "
    "(SELECT g.ref FROM nodes g WHERE g.parent = c.ref) "
    + _CURRENT.format(i="di", s="ds", n="d")
    + " WHERE d.ref <> ALL(%(gone)s::text[]) GROUP BY c.ref), "
    "scored AS (SELECT c.ref, c.title, 1 - (q.v <=> c.own) AS own_score, "
    "CASE WHEN h.held IS NULL THEN NULL ELSE 1 - (q.v <=> h.held) END AS held_score, "
    "1 - (q.v <=> CASE WHEN h.held IS NULL THEN c.own ELSE l2_normalize(c.own + h.held) END) "
    "AS score, coalesce(h.n, 0) AS holds "
    "FROM cand c CROSS JOIN q LEFT JOIN held h ON h.ref = c.ref), "
    "ranked AS (SELECT s.*, row_number() OVER (ORDER BY s.score DESC, s.ref) AS rank, "
    "(count(*) OVER ())::integer AS ranked_total "
    "FROM scored s) "
)

# Batched version of _RANKED for review_many: computes candidate vectors and base descendant
# centroids ONCE for all waiting items of one parent_kind, recomputing a parent's centroid only
# for the at-most-one parent that actually holds any item in a job's `gone` exclusion list.
_RANKED_BATCH = (
    "WITH jobs AS MATERIALIZED ("
    "SELECT j.job, j.q::vector AS v, j.gone, j.target "
    "FROM jsonb_to_recordset(%(jobs)s::jsonb) AS j(job int, q text, gone text[], target text)), "
    "cand AS MATERIALIZED ("
    "SELECT c.ref, c.kind, c.title, l2_normalize(oi.embedding) AS own "
    "FROM nodes c " + _CURRENT.format(i="oi", s="os", n="c") + " "
    "WHERE " + _CANDIDATE + "), "
    "descendants AS MATERIALIZED ("
    "SELECT c.ref AS cand_ref, d.ref AS desc_ref, l2_normalize(di.embedding) AS emb "
    "FROM cand c JOIN nodes d ON d.parent = c.ref OR d.parent IN "
    "(SELECT g.ref FROM nodes g WHERE g.parent = c.ref) "
    + _CURRENT.format(i="di", s="ds", n="d")
    + "), "
    "base_held AS MATERIALIZED ("
    "SELECT cand_ref AS ref, l2_normalize(avg(emb)) AS held "
    "FROM descendants GROUP BY cand_ref), "
    "affected AS ("
    "SELECT DISTINCT j.job, d.cand_ref "
    "FROM jobs j JOIN descendants d ON d.desc_ref = ANY(j.gone)), "
    "adj_held AS ("
    "SELECT a.job, a.cand_ref AS ref, l2_normalize(avg(d.emb)) AS held "
    "FROM affected a JOIN jobs j ON j.job = a.job "
    "LEFT JOIN descendants d ON d.cand_ref = a.cand_ref AND NOT (d.desc_ref = ANY(j.gone)) "
    "GROUP BY a.job, a.cand_ref), "
    "scored AS ("
    "SELECT j.job, j.target, c.ref, c.title, "
    "1 - (j.v <=> CASE WHEN h.held IS NULL THEN c.own "
    "ELSE l2_normalize(c.own + h.held) END) AS score "
    "FROM jobs j CROSS JOIN cand c "
    "LEFT JOIN adj_held ah ON ah.job = j.job AND ah.ref = c.ref "
    "LEFT JOIN base_held bh ON ah.ref IS NULL AND bh.ref = c.ref "
    "CROSS JOIN LATERAL ("
    "SELECT CASE WHEN ah.ref IS NOT NULL THEN ah.held ELSE bh.held END AS held) h), "
    "ranked AS ("
    "SELECT s.*, row_number() OVER (PARTITION BY s.job ORDER BY s.score DESC, s.ref) AS rank "
    "FROM scored s) "
    "SELECT job, ref, title, score, rank FROM ranked "
    "WHERE rank <= 2 OR ref = target ORDER BY job, rank"
)

_BECAUSE = (
    "SELECT c.ref AS candidate, x.ref, x.title, 1 - (x.embedding <=> %(q)s::vector) AS similarity "
    "FROM unnest(%(refs)s::text[]) AS c(ref) CROSS JOIN LATERAL ("
    "SELECT d.ref, d.title, di.embedding FROM nodes d "
    + _CURRENT.format(i="di", s="ds", n="d")
    + " "
    "WHERE (d.parent = c.ref OR d.parent IN (SELECT g.ref FROM nodes g WHERE g.parent = c.ref)) "
    "AND d.ref <> ALL(%(gone)s::text[]) ORDER BY di.embedding <=> %(q)s::vector, d.ref "
    "LIMIT %(many)s) AS x"
)


@dataclass
class _Query:
    """What a ranking needs about the work: its vector and what parents must not count."""

    node: str | None
    kind: str
    parent_kind: str
    vector: str
    gone: list[str]
    current_parent: str | None


class Placement:
    def __init__(self, database: Database, searcher: Searcher) -> None:
        self._database = database
        self._searcher = searcher

    async def _prepare(
        self, *, node: str | None, kind: str | None, title: str | None, body: str | None
    ) -> _Query:
        gone: list[str] = []
        current_parent: str | None = None
        async with self._database.transaction() as conn:
            if node is not None:
                row = await require_node(conn, parse_node_ref(node, "node"))
                kind = row["kind"]
                if kind == "epic":
                    raise Refusal(f"{row['ref']} is an epic. An epic has no parent.")
                text = f"{row['title']}\n{row['body']}".strip()
                current_parent = row["parent"]
                children = await fetch_all(
                    conn, "SELECT ref FROM nodes WHERE parent = %s", (row["ref"],)
                )
                gone = [row["ref"], *[child["ref"] for child in children]]
                found = await fetch_one(
                    conn,
                    "SELECT i.embedding::text AS vector FROM semantic_index i "
                    "JOIN index_source s ON s.project_id = i.project_id "
                    "AND s.item_type = i.item_type AND s.id = i.item_id "
                    "AND s.content_hash = i.content_hash WHERE s.ref = %s",
                    (row["ref"],),
                )
                stored = None if found is None else str(found["vector"])
            else:
                assert title is not None
                if kind not in _PARENT_KIND:
                    raise Refusal("Say kind: story or task, what the work will be.")
                text = f"{clean_title(title)}\n{body or ''}".strip()
                stored = None
        vector = stored or await self._searcher.vector_of(text)
        if vector is None:
            raise Refusal(
                "The embedding service could not be reached, so nothing could be suggested. "
                "Try again in a moment."
            )
        assert kind is not None
        return _Query(
            node=node and parse_node_ref(node, "node"),
            kind=kind,
            parent_kind=_PARENT_KIND[kind],
            vector=vector,
            gone=gone,
            current_parent=current_parent,
        )

    async def _ranking_in(
        self, conn: Any, query: _Query, *, limit: int, include: str | None
    ) -> list[dict[str, Any]]:
        """The best `limit` plus one parents, with the score of `include` too, best first."""
        return await fetch_all(
            conn,
            _RANKED + "SELECT ref, title, own_score, held_score, score, holds, rank, ranked_total "
            "FROM ranked "
            "WHERE rank <= %(limit)s + 1 OR ref = %(current)s::text ORDER BY rank",
            {
                "q": query.vector,
                "parent_kind": query.parent_kind,
                "gone": query.gone,
                "limit": limit,
                "current": include,
            },
        )

    async def suggest_parent(
        self,
        *,
        node: str | None,
        kind: str | None,
        title: str | None,
        body: str | None,
        limit: int,
    ) -> ParentSuggestions:
        if limit < 1 or limit > MOST_SUGGESTIONS:
            raise Refusal(f"limit must be from 1 to {MOST_SUGGESTIONS}, but it is {limit}.")
        if (node is None) == (title is None):
            raise Refusal(
                "Give either node (a story or task that is saved) or title (work that is not "
                "saved yet, with kind), and not both."
            )
        note = await self._searcher.index_state()
        if note.meaning != "used":
            raise Refusal(
                f"Suggesting where work belongs needs search by meaning, and it is not possible "
                f"now. {note.note}"
            )
        query = await self._prepare(node=node, kind=kind, title=title, body=body)
        kind = query.kind
        parent_kind = query.parent_kind
        current_parent = query.current_parent
        gone = query.gone
        vector = query.vector
        async with self._database.transaction() as conn:
            rows = await self._ranking_in(conn, query, limit=limit, include=current_parent)
            because = await fetch_all(
                conn,
                _BECAUSE,
                {
                    "q": vector,
                    "refs": [r["ref"] for r in rows[:limit]],
                    "gone": gone,
                    "many": MOST_LOOK_ALIKES,
                },
            )
            inbox = await fetch_one(
                conn,
                "SELECT ref FROM nodes WHERE kind = 'epic' AND lower(btrim(title)) = 'inbox' "
                "AND status <> 'dropped' ORDER BY number LIMIT 1",
            )
            counted = await fetch_one(
                conn,
                "SELECT count(*) AS total FROM nodes c WHERE " + _CANDIDATE,
                {"parent_kind": parent_kind},
            )
        assert counted is not None
        ranked_total = int(rows[0]["ranked_total"]) if rows else 0
        reasons: dict[str, list[LookAlike]] = {}
        for one in because:
            reasons.setdefault(one["candidate"], []).append(
                LookAlike(ref=one["ref"], title=one["title"], similarity=float(one["similarity"]))
            )
        shown = [
            ParentCandidate(
                ref=r["ref"],
                title=r["title"],
                score=float(r["score"]),
                own=float(r["own_score"]),
                held=None if r["held_score"] is None else float(r["held_score"]),
                holds=int(r["holds"]),
                because=reasons.get(r["ref"], []),
            )
            for r in rows
            if r["rank"] <= limit
        ]
        second = rows[1]["score"] if len(rows) > 1 else None
        margin = None if second is None or not rows else float(rows[0]["score"] - second)
        rank = next((int(r["rank"]) for r in rows if r["ref"] == current_parent), None)
        return ParentSuggestions(
            node=node and parse_node_ref(node, "node"),
            kind=kind,
            parent_kind=parent_kind,
            current_parent=current_parent,
            current_rank=rank,
            suggestions=shown,
            sure=margin is not None and margin >= SURE_MARGIN,
            margin=margin,
            inbox=inbox["ref"] if inbox else None,
            not_considered=int(counted["total"]) - ranked_total,
        )

    @staticmethod
    def _warning_for(item: PlacementItem, parent: str, row: dict[str, Any] | None) -> str | None:
        """Something to weigh before moving an item, said as accepting a proposal would meet it."""
        name = code(item.ref)
        if row is None:
            return f"{name} cannot be found."
        if row["status"] in ("done", "dropped"):
            return f"{name} is {row['status']}: finished work stays where it is."
        if row["parent"] != item.from_parent:
            return f"{name} was moved to {code(row['parent'])} since this was proposed."
        if row["parent"] == parent:
            return f"{name} is already under {code(parent)}."
        if row["claimed_by"]:
            return (
                f"{name} is held by {row['claimed_by']}: moving it changes where work under way is."
            )
        if row["status"] == "in_progress":
            return f"{name} is in progress: moving it changes where work under way is."
        return None

    async def review_many(self, states: list[PlacementState], budget: int) -> list[PlacementState]:
        """Score waiting proposals together: one index_state check, one batch lookup of warnings
        and stored vectors, and at most one ranking query per parent kind."""
        if not states:
            return []
        new_epic_words = (
            "A new epic has no parent that exists, so there is nothing to score against."
        )
        with_parent = [state for state in states if state.parent is not None]
        if not with_parent:
            return [
                state.model_copy(
                    update={
                        "scores": [],
                        "scores_note": new_epic_words,
                        "routine": False,
                        "routine_why": routine_words(state, [], None)[1],
                    }
                )
                for state in states
            ]

        meaning = await self._searcher.index_state()
        base_note: str | None = None if meaning.meaning == "used" else meaning.note
        all_refs = sorted({item.ref for state in with_parent for item in state.items})

        async with self._database.transaction() as conn:
            node_rows = {
                row["ref"]: row
                for row in await fetch_all(
                    conn,
                    "SELECT n.ref, n.kind, n.title, n.body, n.status, n.parent, "
                    "CASE WHEN c.expires_at > clock_timestamp() THEN c.actor END AS claimed_by "
                    "FROM nodes n LEFT JOIN claims c "
                    "ON c.project_id = n.project_id AND c.node = n.ref "
                    "WHERE n.ref = ANY(%s)",
                    (all_refs,),
                )
            }
            children_by_parent: dict[str, list[str]] = {}
            stored_vectors: dict[str, str] = {}
            if base_note is None:
                for child in await fetch_all(
                    conn, "SELECT parent, ref FROM nodes WHERE parent = ANY(%s)", (all_refs,)
                ):
                    children_by_parent.setdefault(child["parent"], []).append(child["ref"])
                for vec in await fetch_all(
                    conn,
                    "SELECT s.ref, i.embedding::text AS vector FROM semantic_index i "
                    "JOIN index_source s ON s.project_id = i.project_id "
                    "AND s.item_type = i.item_type AND s.id = i.item_id "
                    "AND s.content_hash = i.content_hash WHERE s.ref = ANY(%s)",
                    (all_refs,),
                ):
                    stored_vectors[vec["ref"]] = str(vec["vector"])

        proposal_notes: dict[int, str | None] = {}
        item_gaps: dict[tuple[int, str], str] = {}
        jobs_by_parent_kind: dict[str, list[dict[str, Any]]] = {}
        job_index: dict[tuple[int, str], tuple[str, int]] = {}
        for state in with_parent:
            assert state.parent is not None
            note = base_note
            if note is None and len(state.items) > budget:
                note = "There are too many moved items waiting to score them all at once."
            proposal_notes[state.id] = note
            if note is not None:
                continue
            for item in state.items:
                row = node_rows.get(item.ref)
                if row is None:
                    item_gaps[(state.id, item.ref)] = str(missing_node(item.ref))
                    continue
                if row["kind"] == "epic":
                    item_gaps[(state.id, item.ref)] = (
                        f"{row['ref']} is an epic. An epic has no parent."
                    )
                    continue
                vector = stored_vectors.get(item.ref)
                if vector is None:
                    text = f"{row['title']}\n{row['body']}".strip()
                    vector = await self._searcher.vector_of(text)
                    if vector is not None:
                        stored_vectors[item.ref] = vector
                if vector is None:
                    item_gaps[(state.id, item.ref)] = (
                        "The embedding service could not be reached, so nothing could be "
                        "suggested. Try again in a moment."
                    )
                    continue
                parent_kind = _PARENT_KIND[row["kind"]]
                bucket = jobs_by_parent_kind.setdefault(parent_kind, [])
                job_id = len(bucket)
                gone = [row["ref"], *children_by_parent.get(row["ref"], ())]
                bucket.append({"job": job_id, "q": vector, "gone": gone, "target": state.parent})
                job_index[(state.id, item.ref)] = (parent_kind, job_id)
                budget -= 1

        ranked_by_job: dict[tuple[str, int], list[dict[str, Any]]] = {}
        if jobs_by_parent_kind:
            async with self._database.transaction() as conn:
                for parent_kind, jobs in jobs_by_parent_kind.items():
                    rows = await fetch_all(
                        conn,
                        _RANKED_BATCH,
                        {"jobs": json.dumps(jobs), "parent_kind": parent_kind},
                    )
                    for row in rows:
                        ranked_by_job.setdefault((parent_kind, int(row["job"])), []).append(row)

        reviewed: list[PlacementState] = []
        for state in states:
            if state.parent is None:
                ok, why = routine_words(state, [], None)
                reviewed.append(
                    state.model_copy(
                        update={
                            "scores": [],
                            "scores_note": new_epic_words,
                            "routine": ok,
                            "routine_why": why,
                        }
                    )
                )
                continue
            note = proposal_notes[state.id]
            scores: list[MoveScore] = []
            for item in state.items:
                warning = self._warning_for(item, state.parent, node_rows.get(item.ref))
                gap = item_gaps.get((state.id, item.ref))
                found: dict[str, Any] | None = None
                best: dict[str, Any] | None = None
                second: dict[str, Any] | None = None
                if note is None and gap is None:
                    rows = ranked_by_job.get(job_index[(state.id, item.ref)], [])
                    found = next((r for r in rows if r["ref"] == state.parent), None)
                    best = rows[0] if rows else None
                    second = rows[1] if len(rows) > 1 else None
                    if found is None:
                        gap = (
                            f"{code(state.parent)} cannot be ranked: it is not indexed yet, is "
                            "dropped, or is the Inbox."
                        )
                margin = (
                    None
                    if best is None or second is None
                    else float(best["score"] - second["score"])
                )
                scores.append(
                    MoveScore(
                        ref=item.ref,
                        similarity=None if found is None else float(found["score"]),
                        rank=None if found is None else int(found["rank"]),
                        ranked=None,
                        best=None if best is None else best["ref"],
                        best_similarity=None if best is None else float(best["score"]),
                        margin=margin,
                        sure=margin is not None and margin >= SURE_MARGIN,
                        warning=warning,
                        note=gap,
                    )
                )
            ok, why = routine_words(state, scores, note)
            reviewed.append(
                state.model_copy(
                    update={
                        "scores": scores,
                        "scores_note": note,
                        "routine": ok,
                        "routine_why": why,
                    }
                )
            )
        return reviewed


class PlacementBook:
    def __init__(self, database: Database, placement: Placement) -> None:
        self._database = database
        self._placement = placement

    async def _titles(self, conn: Any, rows: list[dict[str, Any]]) -> dict[str, str]:
        refs = sorted({r["parent"] for r in rows if r["parent"]})
        if not refs:
            return {}
        found = await fetch_all(conn, "SELECT ref, title FROM nodes WHERE ref = ANY(%s)", (refs,))
        return {r["ref"]: r["title"] for r in found}

    async def list_placements(
        self, *, status: str | None, limit: int, after_id: int
    ) -> PlacementPage:
        """The proposals to move work, oldest first."""
        limit = page_size(limit)
        if status is not None and status not in PLACEMENT_STATES:
            raise Refusal(
                f"status must be one of {', '.join(PLACEMENT_STATES)}, but it is {status!r}."
            )
        if after_id < 0:
            raise Refusal(f"after_id must be 0 or more, but it is {after_id}.")
        where = "(%(status)s::text IS NULL OR state = %(status)s)"
        async with self._database.transaction() as conn:
            rows = await fetch_all(
                conn,
                f"SELECT {PLACEMENT_COLUMNS} FROM placement_state WHERE {where} "
                "AND id > %(after)s ORDER BY id LIMIT %(fetch)s",
                {"status": status, "after": after_id, "fetch": limit + 1},
            )
            counted = await fetch_one(
                conn,
                f"SELECT count(*) AS total FROM placement_state WHERE {where}",
                {"status": status},
            )
            titles = await self._titles(conn, rows)
        assert counted is not None
        page, next_after_id = split_page(rows, limit)
        listed = [placement_from(row, titles) for row in page]
        proposed_indices = [index for index, one in enumerate(listed) if one.state == "proposed"]
        if proposed_indices:
            scored = await self._placement.review_many(
                [listed[index] for index in proposed_indices], MOST_SCORED
            )
            for index, updated in zip(proposed_indices, scored, strict=True):
                listed[index] = updated
        return PlacementPage(items=listed, total=int(counted["total"]), next_after_id=next_after_id)

    async def get(self, conn: Any, placement: int) -> dict[str, Any] | None:
        return await fetch_one(
            conn, f"SELECT {PLACEMENT_COLUMNS} FROM placement_state WHERE id = %s", (placement,)
        )

    async def propose(
        self,
        *,
        actor: str,
        items: list[str],
        parent: str | None,
        new_epic: NewEpic | None,
        reason: str,
    ) -> PlacementState:
        """Propose moving stories or tasks under another parent, or grouping stories under a new
        epic. Nothing moves: a person decides."""
        why = clean_reason(reason, "reason")
        if why is None:
            raise Refusal(
                "Say why in reason: the person who decides reads it. Nothing was changed."
            )
        if (parent is None) == (new_epic is None):
            raise Refusal(
                "Give either parent (an epic or story that exists) or new_epic (a title for an "
                "epic to make for these stories), and not both. Nothing was changed."
            )
        refs = [parse_node_ref(ref, "items") for ref in items]
        if not 1 <= len(refs) <= MOST_ITEMS:
            raise Refusal(f"Give from 1 to {MOST_ITEMS} items. Nothing was changed.")
        if len(set(refs)) != len(refs):
            raise Refusal("An item is named twice in items. Nothing was changed.")
        epic = None
        if new_epic is not None:
            epic = NewEpic(title=clean_title(new_epic.title), body=new_epic.body)
            if len(epic.body) > MOST_BODY_CHARS:
                raise Refusal(
                    f"The new epic's body may be at most {MOST_BODY_CHARS:,} characters. "
                    "Nothing was changed."
                )
        async with self._database.transaction() as conn:
            found_nodes = await require_nodes(conn, refs)
            nodes = [found_nodes[ref] for ref in refs]
            kinds = {n["kind"] for n in nodes}
            if len(kinds) != 1 or kinds & {"epic"}:
                raise Refusal(
                    "Items must be all stories or all tasks: an epic has no parent to change. "
                    "Nothing was changed."
                )
            kind = kinds.pop()
            wanted = _PARENT_KIND[kind]
            if epic is not None and kind != "story":
                raise Refusal(
                    "A new epic can only hold stories. To group tasks, propose a new story, or "
                    "move them under a story that exists. Nothing was changed."
                )
            if parent is not None:
                parent = parse_node_ref(parent, "parent")
                target = await require_node(conn, parent)
                if target["kind"] != wanted:
                    raise Refusal(
                        f"{code(parent)} is {'an' if target['kind'] == 'epic' else 'a'} "
                        f"{target['kind']}, but {_PLURAL[kind]} go under {_PLURAL[wanted]}. "
                        "Nothing was changed."
                    )
                if target["status"] == "dropped":
                    raise Refusal(f"{code(parent)} is dropped. Nothing was changed.")
            for n in nodes:
                if n["status"] in ("done", "dropped"):
                    raise Refusal(
                        f"{code(n['ref'])} is {n['status']}: finished work stays where it is. "
                        "Nothing was changed."
                    )
                if parent is not None and n["parent"] == parent:
                    raise Refusal(
                        f"{code(n['ref'])} is already under {code(parent)}. Nothing was changed."
                    )
            waiting = await fetch_all(
                conn,
                "SELECT id, proposed_by, items FROM placement_state WHERE state = 'proposed'",
            )
            for one in waiting:
                held = {i["ref"] for i in one["items"]}
                clash = sorted(held & set(refs))
                if clash:
                    raise Refusal(
                        f"{code(clash[0])} is already in proposal {one['id']}, which waits for a "
                        "person. Nothing was changed."
                    )
            mine = sum(1 for one in waiting if one["proposed_by"] == actor)
            if mine >= MOST_OPEN:
                raise Refusal(
                    f"You already have {mine} proposals waiting for a person. Wait for them to "
                    "be decided before proposing more. Nothing was changed."
                )
            event = await record_event(
                conn,
                action="placement_proposed",
                actor=actor,
                before=None,
                after={
                    "items": [
                        {"ref": n["ref"], "title": n["title"], "from_parent": n["parent"]}
                        for n in nodes
                    ],
                    "reason": why,
                    **({"parent": parent} if parent is not None else {}),
                    **({"new_epic": epic.model_dump()} if epic is not None else {}),
                },
            )
            row = await self.get(conn, event)
            assert row is not None
            titles = await self._titles(conn, [row])
        return placement_from(row, titles)

    async def decide(
        self,
        *,
        actor: str,
        placement: int,
        accepted: bool,
        reason: str | None,
        only: list[str] | None = None,
    ) -> PlacementState:
        """Accept a proposal, which makes its moves as this person, or reject it. Only a person
        can; a proposal is decided once. With `only` (when accepting) just those items move and
        the others are left where they are, said so in the record."""
        require_person(f"Proposal {placement}", "accept or reject a proposal to move work")
        why = clean_reason(reason, "reason")
        if only is not None and not accepted:
            raise Refusal(
                "Name the items to move only when accepting. To turn a proposal down, reject it "
                "with a reason. Nothing was changed."
            )
        if not accepted and why is None:
            raise Refusal(
                "A rejected proposal needs a reason: say why, so that whoever proposed it knows. "
                "Nothing was changed."
            )
        async with self._database.transaction() as conn:
            found = await self.get(conn, placement)
            if found is None:
                raise Refusal(
                    f"There is no proposal {placement} in this project. list_placements shows them."
                )
            refs = sorted(i["ref"] for i in found["items"])
            chosen = None if only is None else {one.strip().lower() for one in only}
            if chosen is not None:
                strangers = sorted(chosen - set(refs))
                if strangers:
                    raise Refusal(
                        f"{', '.join(code(one) for one in strangers)} "
                        f"{'is' if len(strangers) == 1 else 'are'} not in proposal {placement}, "
                        f"which moves {', '.join(code(one) for one in refs)}. Nothing was changed."
                    )
                if not chosen:
                    raise Refusal(
                        "Name at least one item to move, or reject the proposal. Nothing was "
                        "changed."
                    )
            # Holding the items serialises two people deciding the same proposal.
            locked = await require_nodes(conn, refs, lock=True)
            again = await self.get(conn, placement)
            assert again is not None
            if again["state"] != "proposed":
                raise Refusal(
                    f"Proposal {placement} was already {again['state']} by {again['decided_by']}. "
                    "A proposal is decided once."
                )
            applied: list[str] = []
            skipped: list[dict[str, str]] = []
            made: str | None = None
            if accepted:
                target = found["parent"]
                if target is not None:
                    gone = await fetch_one(
                        conn, "SELECT status FROM nodes WHERE ref = %s", (target,)
                    )
                    if gone is None or gone["status"] == "dropped":
                        raise Refusal(
                            f"{code(target)} is dropped now, so nothing can go under it. Reject "
                            "the proposal, or ask for a new one. Nothing was changed."
                        )
                for item in found["items"]:
                    current = locked[item["ref"]]
                    if chosen is not None and item["ref"] not in chosen:
                        skipped.append({"ref": item["ref"], "why": f"{actor} left it out"})
                    elif current["status"] in ("done", "dropped"):
                        skipped.append({"ref": item["ref"], "why": f"it is {current['status']}"})
                    elif current["parent"] != item["from_parent"]:
                        skipped.append(
                            {
                                "ref": item["ref"],
                                "why": f"it was moved to {code(current['parent'])} since",
                            }
                        )
                if len(skipped) == len(found["items"]):
                    raise Refusal(
                        "Every item has moved or finished since this was proposed, or was left "
                        "out, so there is nothing to do. Reject the proposal. Nothing was "
                        "changed."
                    )
                if target is None:
                    epic = NewEpic.model_validate(found["new_epic"])
                    row = await insert_node(
                        conn,
                        actor=actor,
                        kind="epic",
                        title=epic.title,
                        parent=None,
                        body=epic.body,
                        priority="normal",
                        size=None,
                        start_on=None,
                        target_on=None,
                    )
                    made = row["ref"]
                    target = made
                left = {s["ref"] for s in skipped}
                for item in found["items"]:
                    if item["ref"] in left:
                        continue
                    await move_in(conn, actor=actor, node=item["ref"], new_parent=target)
                    applied.append(item["ref"])
            await record_event(
                conn,
                action="placement_accepted" if accepted else "placement_rejected",
                actor=actor,
                before=None,
                after={
                    "placement": placement,
                    **({"reason": why} if why else {}),
                    **(
                        {"applied": applied, "skipped": skipped, **({"epic": made} if made else {})}
                        if accepted
                        else {}
                    ),
                },
            )
            done = await self.get(conn, placement)
            assert done is not None
            titles = await self._titles(conn, [done])
        return placement_from(done, titles)
