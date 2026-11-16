"""Recall: everything known about one node or rule, by exact links only, in separate labelled
sections, each saying why an entry is there."""

from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import Refusal, check_choice, fetch_all, fetch_one, page_size, split_page
from vrum.db import Database
from vrum.decisions import LINK_TABLES, Decision, count_decisions, page_decisions
from vrum.memories import MEMORY_COLUMNS, Memory, to_memory
from vrum.search import IndexNote, Searcher, SearchResult

RECALL_LISTS = (
    "all",
    "active_decisions",
    "active_principles",
    "facts",
    "beliefs",
    "stale_beliefs",
    "superseded_decisions",
    "past_memories",
)

_MEMORY_LINK_TABLES = {"node": "memory_nodes", "rule": "memory_rules"}
_LINK_COLUMNS = {"node": "node_id", "rule": "rule_id"}


class RecalledDecision(BaseModel):
    why: str = Field(description="Why this decision is in the recall.")
    decision: Decision


class RecalledMemory(BaseModel):
    why: str = Field(description="Why this fact or belief is in the recall.")
    memory: Memory


class DecisionSection(BaseModel):
    items: list[RecalledDecision]
    count: int = Field(description="How many there are in all, across pages.")
    next_after_id: int | None = Field(
        description="Pass as after_id, with show set to this section, for the next page."
    )


class MemorySection(BaseModel):
    items: list[RecalledMemory]
    count: int = Field(description="How many there are in all, across pages.")
    next_after_id: int | None = Field(
        description="Pass as after_id, with show set to this section, for the next page."
    )


class SimilarSection(BaseModel):
    items: list[SearchResult] = Field(
        description="Active decisions, facts and beliefs that are alike in meaning but not linked "
        "to this item. Suggestions only: nothing here was linked by anyone."
    )
    count: int = Field(description="How many there are in all.")
    index: IndexNote


class Recall(BaseModel):
    active_decisions: DecisionSection = Field(
        description="Active decisions linked to this item. Only what is linked, nothing inferred."
    )
    active_principles: DecisionSection = Field(
        description="Every active principle, linked to this item or not."
    )
    facts: MemorySection = Field(description="Active facts linked to this item.")
    beliefs: MemorySection = Field(
        description="Active beliefs linked to this item, or resting on evidence linked to it. "
        "Every belief is a suggestion, never a decision."
    )
    stale_beliefs: MemorySection = Field(
        description="Beliefs that were linked or relevant, but whose evidence has changed. They "
        "are not in force: read what changed in each one, then revise or retract it."
    )
    superseded_decisions: DecisionSection = Field(
        description="Decisions and principles linked to this item that a newer decision has "
        "replaced. Shown so you can see what changed; they are no longer in force."
    )
    past_memories: MemorySection = Field(
        description="Facts and beliefs linked to this item that were superseded or retracted, with "
        "what replaced them or why they were wrong."
    )
    similar: SimilarSection | None = Field(
        description="Only when you ask for it with with_similar: what is alike in meaning but "
        "not linked. Everything else here is exact links only."
    )


def _decision_why(decision: Decision, what: str) -> str:
    if decision.kind == "principle":
        return "a principle, so it applies everywhere"
    base = f"linked directly to this {what}"
    if decision.status == "superseded":
        return f"{base}; replaced by decision {decision.superseded_by_id}"
    return base


def _memory_why(memory: Memory, what: str, thing: int) -> str:
    linked = thing in (memory.node_ids if what == "node" else memory.rule_ids)
    base = (
        f"linked directly to this {what}" if linked else f"rests on evidence linked to this {what}"
    )
    if memory.status == "superseded":
        return f"{base}; replaced by {memory.type} {memory.superseded_by_id}"
    if memory.status == "retracted":
        return f"{base}; retracted: {memory.status_reason}"
    return base


async def _count_memories(conn: AsyncConnection, where: str, params: dict[str, Any]) -> int:
    row = await fetch_one(conn, f"SELECT count(*) AS total FROM memory_state WHERE {where}", params)
    assert row is not None
    return int(row["total"])


async def _page_memories(
    conn: AsyncConnection, where: str, params: dict[str, Any], limit: int, after_id: int | None
) -> tuple[list[Memory], int | None]:
    rows = await fetch_all(
        conn,
        f"SELECT {MEMORY_COLUMNS} FROM memory_state WHERE {where} AND id > %(after)s "
        "ORDER BY id LIMIT %(fetch)s",
        {**params, "after": after_id or 0, "fetch": limit + 1},
    )
    page, next_after_id = split_page(rows, limit)
    return [to_memory(row) for row in page], next_after_id


class Recaller:
    def __init__(self, database: Database, searcher: Searcher) -> None:
        self._database = database
        self._searcher = searcher

    async def recall(
        self,
        *,
        node_id: int | None,
        rule_id: int | None,
        show: str,
        limit: int,
        after_id: int | None,
        with_similar: bool,
    ) -> Recall:
        """What is known about one node or rule, by exact links, and, if asked, what is alike."""
        limit = page_size(limit)
        check_choice("show", show, RECALL_LISTS)
        if (node_id is None) == (rule_id is None):
            raise Refusal("Give exactly one of node_id or rule_id: the thing to recall for.")
        if after_id is not None and show == "all":
            raise Refusal(
                "after_id only works for one section at a time: set show to one of "
                f"{', '.join(RECALL_LISTS[1:])}."
            )
        what, thing = ("node", node_id) if node_id is not None else ("rule", rule_id)
        assert thing is not None
        column = _LINK_COLUMNS[what]
        decision_table = LINK_TABLES[what][0]
        memory_table = _MEMORY_LINK_TABLES[what]
        params = {"thing": thing}
        linked_decision = (
            f"id IN (SELECT decision_id FROM {decision_table} WHERE {column} = %(thing)s)"
        )
        linked = f"id IN (SELECT memory_id FROM {memory_table} WHERE {column} = %(thing)s)"
        # A belief also counts when what it rests on is linked here.
        relevant_belief = (
            "(type = 'belief' AND id IN (SELECT be.belief_id FROM belief_evidence be "
            f"WHERE be.fact_id IN (SELECT memory_id FROM {memory_table} "
            f"WHERE {column} = %(thing)s) "
            f"OR be.decision_id IN (SELECT decision_id FROM {decision_table} "
            f"WHERE {column} = %(thing)s)))"
        )
        relevant = f"({linked} OR {relevant_belief})"
        decision_lists = {
            "active_decisions": f"{linked_decision} AND kind = 'decision' AND status = 'active'",
            "active_principles": "kind = 'principle' AND status = 'active'",
            "superseded_decisions": f"{linked_decision} AND status = 'superseded'",
        }
        memory_lists = {
            "facts": f"type = 'fact' AND status = 'active' AND {linked}",
            "beliefs": f"type = 'belief' AND status = 'active' AND NOT stale AND {relevant}",
            "stale_beliefs": f"stale AND {relevant}",
            "past_memories": f"status <> 'active' AND {relevant}",
        }
        async with self._database.transaction() as conn:
            found = await fetch_one(
                conn,
                f"SELECT 1 AS found FROM {'nodes' if what == 'node' else 'rules'} WHERE id = %s",
                (thing,),
            )
            if found is None:
                finder = "list_nodes" if what == "node" else "list_rules"
                raise Refusal(
                    f"There is no {what} with id {thing}. Use {finder} to find the id you want."
                )
            decisions: dict[str, DecisionSection] = {}
            for name, where in decision_lists.items():
                found_decisions, next_decision_id = (
                    await page_decisions(conn, where, params, limit, after_id)
                    if show in ("all", name)
                    else ([], None)
                )
                decisions[name] = DecisionSection(
                    items=[
                        RecalledDecision(why=_decision_why(item, what), decision=item)
                        for item in found_decisions
                    ],
                    count=await count_decisions(conn, where, params),
                    next_after_id=next_decision_id,
                )
            memories: dict[str, MemorySection] = {}
            for name, where in memory_lists.items():
                found_memories, next_memory_id = (
                    await _page_memories(conn, where, params, limit, after_id)
                    if show in ("all", name)
                    else ([], None)
                )
                memories[name] = MemorySection(
                    items=[
                        RecalledMemory(why=_memory_why(item, what, thing), memory=item)
                        for item in found_memories
                    ],
                    count=await _count_memories(conn, where, params),
                    next_after_id=next_memory_id,
                )
            exclude: set[tuple[str, int]] = set()
            if with_similar:
                # Whatever is linked or already shown is not "similar": it is known.
                for row in await fetch_all(
                    conn, f"SELECT kind, id FROM decision_state WHERE {linked_decision}", params
                ):
                    exclude.add((row["kind"], row["id"]))
                for row in await fetch_all(
                    conn, f"SELECT type, id FROM memory_state WHERE {relevant}", params
                ):
                    exclude.add((row["type"], row["id"]))
        similar: SimilarSection | None = None
        if with_similar:
            similar_items, count, note = await self._searcher.similar(
                node_id=node_id,
                rule_id=rule_id,
                wanted_types=["decision", "fact", "belief"],
                exclude=exclude,
                limit=limit,
            )
            similar = SimilarSection(items=similar_items, count=count, index=note)
        return Recall(
            active_decisions=decisions["active_decisions"],
            active_principles=decisions["active_principles"],
            facts=memories["facts"],
            beliefs=memories["beliefs"],
            stale_beliefs=memories["stale_beliefs"],
            superseded_decisions=decisions["superseded_decisions"],
            past_memories=memories["past_memories"],
            similar=similar,
        )
