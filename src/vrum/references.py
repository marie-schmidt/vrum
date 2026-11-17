"""References between items, and who mentions whom.

Any text can name another item by writing `[[story:12]]` (or `[[epic:3]]`, `[[task:40]]`,
`[[rule:3]]`, `[[decision:7]]`, `[[fact:5]]`) in what it says: an epic's, story's or task's details,
a requirement's details, a decision's text or reason, or a fact's text or source. (A title is a
name, and is not read.) The number is the item's number within its kind, so `[[epic:1]]`,
`[[story:1]]` and `[[task:1]]` are three different items. A reference is only text: it creates no
link, changes no readiness and no order, and nothing is stored for it. `find_mentions` works out,
when asked, which items refer to one item, or every reference in the graph, by reading the text of
everything. This module owns what a reference is; the board's renderer has the same pattern
(`board/static/references.js`), and a test keeps the two equal."""

from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import Refusal, fetch_all, fetch_one, page_size, parse_node_ref, split_node_ref
from vrum.db import Database
from vrum.decisions import require_decision
from vrum.facts import require_fact
from vrum.graph import require_node
from vrum.items import NamedItem, named
from vrum.rules import require_rule

REFERENCE_KINDS = ("epic", "story", "task", "rule", "decision", "fact")
# `[[kind:number]]`, with no spaces and no leading zero in the number (a whole number). Up to 18
# digits, so that it always fits the database's number.
REFERENCE_PATTERN = r"\[\[(epic|story|task|rule|decision|fact):([1-9][0-9]{0,17})\]\]"
_SNIPPET_BEFORE = 60
_SNIPPET_AFTER = 60

# Every text that can hold a reference, as rows of (what it is, its fields). `item_type` is the kind
# of the item (epic, story, task, rule, decision or fact), the same word a reference to it starts
# with; `id` is the number of an epic, story or task within its kind, or the id of anything else.
# Fixed here, never from a caller.
_TEXTS = """
    SELECT kind AS item_type, number AS id, title AS item_title, status AS item_status,
           1 AS ord, 'details' AS field, body AS content
    FROM node_state
    UNION ALL
    SELECT 'rule', id, title, status, 1, 'details', body FROM rules
    UNION ALL
    SELECT 'decision', id, title, status, f.ord, f.field, f.content
    FROM decisions,
         LATERAL (VALUES (1, 'text', text), (2, 'reason', reason)) AS f(ord, field, content)
    UNION ALL
    SELECT 'fact', id, left(text, 80), status, f.ord, f.field, f.content
    FROM facts,
         LATERAL (VALUES (1, 'text', text), (2, 'source', source)) AS f(ord, field, content)
"""

# What a reference can point at, so that one to something that is not there is told apart.
_TARGETS = """
    SELECT kind AS target_type, number AS target_id, title AS target_title FROM nodes
    UNION ALL SELECT 'rule', id, title FROM rules
    UNION ALL SELECT 'decision', id, title FROM decisions
    UNION ALL SELECT 'fact', id, left(text, 80) FROM facts
"""


def _rank(column: str) -> str:
    return (
        f"CASE {column} WHEN 'epic' THEN 1 WHEN 'story' THEN 2 WHEN 'task' THEN 3 "
        "WHEN 'rule' THEN 4 WHEN 'decision' THEN 5 WHEN 'fact' THEN 6 ELSE 7 END"
    )


_ORDER = f"{_rank('item_type')}, id, {_rank('target_type')}, target_id"


class MentionItem(NamedItem):
    title: str = Field(description="Its title now. For a fact, the start of its text.")
    status: str = Field(description="Its status now.")


class MentionTarget(NamedItem):
    exists: bool = Field(
        description="False when nothing of that kind has that number. The reference is then "
        "plainly missing."
    )
    title: str | None = Field(description="Its title now. Empty when it is missing.")


class Mention(BaseModel):
    source: MentionItem = Field(description="The item whose text holds the reference.")
    target: MentionTarget = Field(description="The item the reference names.")
    fields: list[str] = Field(
        description="Where in the source it is written: details, text, reason or source."
    )
    snippet: str = Field(description="A piece of the first of those fields around the reference.")


class MentionPage(BaseModel):
    items: list[Mention]
    total: int = Field(description="How many mentions there are in all, across pages.")
    next_offset: int | None = Field(
        description="Pass this as offset to get the next page. Empty when there are no more."
    )


async def _target_of(
    conn: AsyncConnection,
    node: str | None,
    rule_id: int | None,
    decision_id: int | None,
    fact_id: int | None,
) -> tuple[str, int] | None:
    """The one item to find mentions of, as (its type, its number or id), after checking it
    exists. None when none was given: every reference."""
    given = [
        name
        for name, value in (
            ("node", node),
            ("rule_id", rule_id),
            ("decision_id", decision_id),
            ("fact_id", fact_id),
        )
        if value is not None
    ]
    if len(given) > 1:
        raise Refusal(
            "Give at most one of node, rule_id, decision_id or fact_id: the one item to find "
            "mentions of. Leave them all out to list every reference written anywhere."
        )
    if node is not None:
        node = parse_node_ref(node, "node")
        await require_node(conn, node)
        return split_node_ref(node)
    if rule_id is not None:
        await require_rule(conn, rule_id)
        return "rule", rule_id
    if decision_id is not None:
        await require_decision(conn, decision_id)
        return "decision", decision_id
    if fact_id is not None:
        await require_fact(conn, fact_id)
        return "fact", fact_id
    return None


class References:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def find_mentions(
        self,
        *,
        node: str | None,
        rule_id: int | None,
        decision_id: int | None,
        fact_id: int | None,
        limit: int,
        offset: int,
    ) -> MentionPage:
        limit = page_size(limit)
        if offset < 0:
            raise Refusal(f"offset must be 0 or more, but it is {offset}.")
        async with self._database.transaction() as conn:
            target = await _target_of(conn, node, rule_id, decision_id, fact_id)
            params: dict[str, Any] = {
                "pattern": REFERENCE_PATTERN,
                "kind": target[0] if target else None,
                "id": target[1] if target else None,
                "needle": f"[[{target[0]}:{target[1]}]]" if target else None,
                "before": _SNIPPET_BEFORE,
                "after": _SNIPPET_AFTER,
            }
            # With a target, only texts that hold its exact reference are read at all.
            grouped = f"""
                WITH texts AS ({_TEXTS}),
                targets AS ({_TARGETS}),
                found AS (
                    SELECT t.item_type, t.id, t.item_title, t.item_status, t.ord,
                           t.field, m.parts[2] AS target_type, m.parts[3]::bigint AS target_id,
                           m.n,
                           substr(
                               t.content,
                               greatest(strpos(t.content, m.parts[1]) - %(before)s::int, 1),
                               %(before)s::int + length(m.parts[1]) + %(after)s::int
                           ) AS snippet
                    FROM texts t,
                         LATERAL regexp_matches(
                             t.content,
                             '(' || %(pattern)s::text || ')',
                             'g'
                         ) WITH ORDINALITY AS m(parts, n)
                    WHERE (%(needle)s::text IS NULL OR strpos(t.content, %(needle)s::text) > 0)
                ),
                per_field AS (
                    SELECT item_type, id, item_title, item_status, ord, field,
                           target_type, target_id, (array_agg(snippet ORDER BY n))[1] AS snippet
                    FROM found
                    WHERE NOT (target_type = item_type AND target_id = id)
                      AND (%(kind)s::text IS NULL
                           OR (target_type = %(kind)s::text AND target_id = %(id)s::bigint))
                    GROUP BY item_type, id, item_title, item_status, ord, field,
                             target_type, target_id
                )
                SELECT f.item_type, f.id, f.item_title, f.item_status, f.target_type, f.target_id,
                       array_agg(f.field ORDER BY f.ord) AS fields,
                       (array_agg(f.snippet ORDER BY f.ord))[1] AS snippet,
                       g.target_title, (g.target_id IS NOT NULL) AS target_exists
                FROM per_field f
                LEFT JOIN targets g
                       ON g.target_type = f.target_type AND g.target_id = f.target_id
                GROUP BY f.item_type, f.id, f.item_title, f.item_status, f.target_type,
                         f.target_id, g.target_title, g.target_id
            """
            total = await fetch_one(conn, f"SELECT count(*) AS total FROM ({grouped}) AS g", params)
            rows = await fetch_all(
                conn,
                f"SELECT * FROM ({grouped}) AS g ORDER BY {_ORDER} "
                "LIMIT %(limit)s OFFSET %(offset)s",
                {**params, "limit": limit + 1, "offset": offset},
            )
        assert total is not None
        page = rows[:limit]
        return MentionPage(
            items=[_mention(row) for row in page],
            total=int(total["total"]),
            next_offset=offset + limit if len(rows) > limit else None,
        )


def _mention(row: dict[str, Any]) -> Mention:
    return Mention(
        source=MentionItem(
            **named(row["item_type"], row["id"]),
            title=row["item_title"],
            status=row["item_status"],
        ),
        target=MentionTarget(
            **named(row["target_type"], row["target_id"]),
            exists=row["target_exists"],
            title=row["target_title"],
        ),
        fields=list(row["fields"]),
        snippet=row["snippet"],
    )
