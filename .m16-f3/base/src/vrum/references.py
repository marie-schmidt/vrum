"""References between items, and who mentions whom.

Any text can name another item by writing `[[node:12]]`, `[[rule:3]]`, `[[decision:7]]`,
`[[fact:5]]` or `[[belief:2]]` (a principle is a decision) in what it says: an epic's, story's or
task's details, a rule's details, a decision's text or reason, a fact's or belief's text, or a
fact's source. (A title is a name, and is not read.) A reference is only text: it creates no
link, changes no readiness and no order, and nothing is stored for it. `find_mentions` works out,
when asked, which items refer to one item, or every reference in the graph, by reading the text
of everything. This module owns what a reference is; the board's renderer has the same pattern
(`board/static/references.js`), and a test keeps the two equal."""

from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import Refusal, fetch_all, fetch_one, page_size
from vrum.db import Database
from vrum.decisions import require_decision
from vrum.graph import require_node
from vrum.memories import require_memory
from vrum.rules import require_rule

REFERENCE_KINDS = ("node", "rule", "decision", "fact", "belief")
# `[[kind:id]]`, with no spaces and no leading zero in the id (an id is a whole number). Up to 18
# digits, so that it always fits the database's number.
REFERENCE_PATTERN = r"\[\[(node|rule|decision|fact|belief):([1-9][0-9]{0,17})\]\]"
_SNIPPET_BEFORE = 60
_SNIPPET_AFTER = 60

# Every text that can hold a reference, as rows of (what it is, its fields). `ref_kind` is how a
# reference to the item is written; `item_type` is the kind it shows as (epic, story, task, rule,
# decision, principle, fact or belief). Fixed here, never from a caller.
_TEXTS = """
    SELECT 'node' AS ref_kind, kind AS item_type, id, title AS item_title, status AS item_status,
           1 AS ord, 'details' AS field, body AS content
    FROM node_state
    UNION ALL
    SELECT 'rule', 'rule', id, title, status, 1, 'details', body FROM rules
    UNION ALL
    SELECT 'decision', kind, id, title, status, f.ord, f.field, f.content
    FROM decisions,
         LATERAL (VALUES (1, 'text', text), (2, 'reason', reason)) AS f(ord, field, content)
    UNION ALL
    SELECT type, type, id, left(text, 80), status, f.ord, f.field, f.content
    FROM memories,
         LATERAL (VALUES (1, 'text', text), (2, 'source', coalesce(source, '')))
             AS f(ord, field, content)
"""

# What a reference can point at, so that one to something that is not there is told apart.
_TARGETS = """
    SELECT 'node' AS target_kind, id AS target_id, kind AS target_type, title AS target_title
    FROM nodes
    UNION ALL SELECT 'rule', id, 'rule', title FROM rules
    UNION ALL SELECT 'decision', id, kind, title FROM decisions
    UNION ALL SELECT type, id, type, left(text, 80) FROM memories
"""

_ORDER = (
    "CASE item_type WHEN 'epic' THEN 1 WHEN 'story' THEN 2 WHEN 'task' THEN 3 WHEN 'rule' THEN 4 "
    "WHEN 'decision' THEN 5 WHEN 'principle' THEN 6 WHEN 'fact' THEN 7 ELSE 8 END, id, "
    "target_kind, target_id"
)


class MentionItem(BaseModel):
    item_type: str = Field(
        description="epic, story, task, rule, decision, principle, fact or belief."
    )
    id: int = Field(description="Its id, in the id space of its kind (rules have their own).")
    title: str = Field(description="Its title now. For a fact or a belief, the start of its text.")
    status: str = Field(description="Its status now.")


class MentionTarget(BaseModel):
    kind: str = Field(
        description="node, rule, decision, fact or belief: how the reference writes it."
    )
    id: int
    exists: bool = Field(
        description="False when nothing of that kind has that id (including a fact written as a "
        "belief or the other way round). The reference is then plainly missing."
    )
    item_type: str | None = Field(
        description="What it is: epic, story, task, rule, decision, principle, fact or belief. "
        "Empty when it is missing."
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
    node_id: int | None,
    rule_id: int | None,
    decision_id: int | None,
    memory_id: int | None,
) -> tuple[str, int] | None:
    """The one item to find mentions of, as (how a reference writes it, its id), after checking it
    exists. None when none was given: every reference."""
    given = [
        name
        for name, value in (
            ("node_id", node_id),
            ("rule_id", rule_id),
            ("decision_id", decision_id),
            ("memory_id", memory_id),
        )
        if value is not None
    ]
    if len(given) > 1:
        raise Refusal(
            "Give at most one of node_id, rule_id, decision_id or memory_id: the one item to find "
            "mentions of. Leave them all out to list every reference written anywhere."
        )
    if node_id is not None:
        await require_node(conn, node_id)
        return "node", node_id
    if rule_id is not None:
        await require_rule(conn, rule_id)
        return "rule", rule_id
    if decision_id is not None:
        await require_decision(conn, decision_id)
        return "decision", decision_id
    if memory_id is not None:
        memory = await require_memory(conn, memory_id)
        return str(memory["type"]), memory_id
    return None


class References:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def find_mentions(
        self,
        *,
        node_id: int | None,
        rule_id: int | None,
        decision_id: int | None,
        memory_id: int | None,
        limit: int,
        offset: int,
    ) -> MentionPage:
        limit = page_size(limit)
        if offset < 0:
            raise Refusal(f"offset must be 0 or more, but it is {offset}.")
        async with self._database.transaction() as conn:
            target = await _target_of(conn, node_id, rule_id, decision_id, memory_id)
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
                    SELECT t.ref_kind, t.item_type, t.id, t.item_title, t.item_status, t.ord,
                           t.field, m.parts[2] AS target_kind, m.parts[3]::bigint AS target_id,
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
                    SELECT ref_kind, item_type, id, item_title, item_status, ord, field,
                           target_kind, target_id, (array_agg(snippet ORDER BY n))[1] AS snippet
                    FROM found
                    WHERE NOT (target_kind = ref_kind AND target_id = id)
                      AND (%(kind)s::text IS NULL
                           OR (target_kind = %(kind)s::text AND target_id = %(id)s::bigint))
                    GROUP BY ref_kind, item_type, id, item_title, item_status, ord, field,
                             target_kind, target_id
                )
                SELECT f.item_type, f.id, f.item_title, f.item_status, f.target_kind, f.target_id,
                       array_agg(f.field ORDER BY f.ord) AS fields,
                       (array_agg(f.snippet ORDER BY f.ord))[1] AS snippet,
                       g.target_type, g.target_title, (g.target_id IS NOT NULL) AS target_exists
                FROM per_field f
                LEFT JOIN targets g
                       ON g.target_kind = f.target_kind AND g.target_id = f.target_id
                GROUP BY f.item_type, f.id, f.item_title, f.item_status, f.target_kind,
                         f.target_id, g.target_type, g.target_title, g.target_id
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
            item_type=row["item_type"],
            id=row["id"],
            title=row["item_title"],
            status=row["item_status"],
        ),
        target=MentionTarget(
            kind=row["target_kind"],
            id=row["target_id"],
            exists=row["target_exists"],
            item_type=row["target_type"],
            title=row["target_title"],
        ),
        fields=list(row["fields"]),
        snippet=row["snippet"],
    )
