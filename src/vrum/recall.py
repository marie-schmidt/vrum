"""Recall: everything known about one node or rule, by exact links only, in separate labelled
sections, each saying why an entry is there. Every entry carries its confirmation and its lifecycle,
so an unconfirmed record is never presented as confirmed and an ended one never as in force."""

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
    split_page,
)
from vrum.db import Database
from vrum.decisions import LINK_TABLES, Decision, count_decisions, page_decisions
from vrum.facts import FACT_COLUMNS, Fact, to_fact
from vrum.graph import require_node
from vrum.rules import require_rule
from vrum.search import IndexNote, Searcher, SearchResult

RECALL_LISTS = (
    "all",
    "active_decisions",
    "project_wide_decisions",
    "facts",
    "past_decisions",
    "past_facts",
)

_FACT_LINK_TABLES = {"node": "fact_nodes", "rule": "fact_rules"}
# The column of each link table that holds the thing recalled: a node's ref or a rule's id.
_LINK_COLUMNS = {"node": "node", "rule": "rule_id"}


class RecalledDecision(BaseModel):
    why: str = Field(description="Why this decision is in the recall.")
    decision: Decision


class RecalledFact(BaseModel):
    why: str = Field(description="Why this fact is in the recall.")
    fact: Fact


class DecisionSection(BaseModel):
    items: list[RecalledDecision]
    count: int = Field(description="How many there are in all, across pages.")
    next_after_id: int | None = Field(
        description="Pass as after_id, with show set to this section, for the next page."
    )


class FactSection(BaseModel):
    items: list[RecalledFact]
    count: int = Field(description="How many there are in all, across pages.")
    next_after_id: int | None = Field(
        description="Pass as after_id, with show set to this section, for the next page."
    )


class SimilarSection(BaseModel):
    items: list[SearchResult] = Field(
        description="Active decisions and facts that are alike in meaning but not linked to this "
        "item. Suggestions only: nothing here was linked by anyone."
    )
    count: int = Field(description="How many there are in all.")
    index: IndexNote


class Recall(BaseModel):
    active_decisions: DecisionSection = Field(
        description="Active decisions linked to this item, other than project-wide ones. Only "
        "what is linked, nothing inferred. Each says whether a person confirmed it."
    )
    project_wide_decisions: DecisionSection = Field(
        description="Every active project-wide decision, linked to this item or not: they apply "
        "throughout the project."
    )
    facts: FactSection = Field(
        description="Active facts linked to this item. Each says whether a person confirmed it."
    )
    past_decisions: DecisionSection = Field(
        description="Decisions linked to this item that were superseded or withdrawn. Shown so "
        "you can see what changed; they are no longer in force."
    )
    past_facts: FactSection = Field(
        description="Facts linked to this item that were superseded or withdrawn, with what "
        "replaced them or why they were wrong."
    )
    similar: SimilarSection | None = Field(
        description="Only when you ask for it with with_similar: what is alike in meaning but "
        "not linked. Everything else here is exact links only."
    )


def _decision_why(decision: Decision, what: str) -> str:
    if decision.project_wide and decision.status == "active":
        return "a project-wide decision, so it applies throughout the project"
    base = f"linked directly to this {what}"
    if decision.status == "superseded":
        return f"{base}; replaced by decision {decision.superseded_by_id}"
    if decision.status == "withdrawn":
        return f"{base}; withdrawn: {decision.status_reason}"
    return base


def _fact_why(fact: Fact, what: str) -> str:
    base = f"linked directly to this {what}"
    if fact.status == "superseded":
        return f"{base}; replaced by fact {fact.superseded_by_id}"
    if fact.status == "withdrawn":
        return f"{base}; withdrawn: {fact.status_reason}"
    return base


async def _count_facts(conn: AsyncConnection, where: str, params: dict[str, Any]) -> int:
    row = await fetch_one(conn, f"SELECT count(*) AS total FROM fact_state WHERE {where}", params)
    assert row is not None
    return int(row["total"])


async def _page_facts(
    conn: AsyncConnection, where: str, params: dict[str, Any], limit: int, after_id: int | None
) -> tuple[list[Fact], int | None]:
    rows = await fetch_all(
        conn,
        f"SELECT {FACT_COLUMNS} FROM fact_state WHERE {where} AND id > %(after)s "
        "ORDER BY id LIMIT %(fetch)s",
        {**params, "after": after_id or 0, "fetch": limit + 1},
    )
    page, next_after_id = split_page(rows, limit)
    return [to_fact(row) for row in page], next_after_id


class Recaller:
    def __init__(self, database: Database, searcher: Searcher) -> None:
        self._database = database
        self._searcher = searcher

    async def recall(
        self,
        *,
        node: str | None,
        rule_id: int | None,
        show: str,
        limit: int,
        after_id: int | None,
        with_similar: bool,
    ) -> Recall:
        """What is known about one node or rule, by exact links, and, if asked, what is alike."""
        limit = page_size(limit)
        check_choice("show", show, RECALL_LISTS)
        if (node is None) == (rule_id is None):
            raise Refusal(
                "Give exactly one of node (an epic, story or task, such as story:12) or rule_id: "
                "the thing to recall for."
            )
        if after_id is not None and show == "all":
            raise Refusal(
                "after_id only works for one section at a time: set show to one of "
                f"{', '.join(RECALL_LISTS[1:])}."
            )
        thing: str | int
        if node is not None:
            node = parse_node_ref(node, "node")
            what, thing = "node", node
        else:
            assert rule_id is not None
            what, thing = "rule", rule_id
        column = _LINK_COLUMNS[what]
        decision_table = LINK_TABLES[what][0]
        fact_table = _FACT_LINK_TABLES[what]
        params = {"thing": thing}
        linked_decision = (
            f"id IN (SELECT decision_id FROM {decision_table} WHERE {column} = %(thing)s)"
        )
        linked_fact = f"id IN (SELECT fact_id FROM {fact_table} WHERE {column} = %(thing)s)"
        decision_lists = {
            "active_decisions": f"{linked_decision} AND status = 'active' AND NOT project_wide",
            "project_wide_decisions": "project_wide AND status = 'active'",
            "past_decisions": f"{linked_decision} AND status <> 'active'",
        }
        fact_lists = {
            "facts": f"status = 'active' AND {linked_fact}",
            "past_facts": f"status <> 'active' AND {linked_fact}",
        }
        async with self._database.transaction() as conn:
            if isinstance(thing, str):
                await require_node(conn, thing)
            else:
                await require_rule(conn, thing)
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
            facts: dict[str, FactSection] = {}
            for name, where in fact_lists.items():
                found_facts, next_fact_id = (
                    await _page_facts(conn, where, params, limit, after_id)
                    if show in ("all", name)
                    else ([], None)
                )
                facts[name] = FactSection(
                    items=[
                        RecalledFact(why=_fact_why(item, what), fact=item) for item in found_facts
                    ],
                    count=await _count_facts(conn, where, params),
                    next_after_id=next_fact_id,
                )
            exclude: set[tuple[str, int]] = set()
            if with_similar:
                # Whatever is linked or already shown is not "similar": it is known.
                for row in await fetch_all(
                    conn,
                    f"SELECT id FROM decision_state WHERE {linked_decision} "
                    "OR (project_wide AND status = 'active')",
                    params,
                ):
                    exclude.add(("decision", row["id"]))
                for row in await fetch_all(
                    conn, f"SELECT id FROM fact_state WHERE {linked_fact}", params
                ):
                    exclude.add(("fact", row["id"]))
        similar: SimilarSection | None = None
        if with_similar:
            similar_items, count, note = await self._searcher.similar(
                node=node,
                rule_id=rule_id,
                wanted_types=["decision", "fact"],
                exclude=exclude,
                limit=limit,
            )
            similar = SimilarSection(items=similar_items, count=count, index=note)
        return Recall(
            active_decisions=decisions["active_decisions"],
            project_wide_decisions=decisions["project_wide_decisions"],
            facts=facts["facts"],
            past_decisions=decisions["past_decisions"],
            past_facts=facts["past_facts"],
            similar=similar,
        )
