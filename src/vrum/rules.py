"""Rules, which a person reads as acceptance criteria, and the coverage report. A rule is what must
hold for one story or one task to be done. This module owns creating, changing, placing and moving
rules, and the question of which rules and stories are not yet matched up."""

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import (
    Refusal,
    check_choice,
    fetch_all,
    fetch_one,
    page_size,
    record_event,
    save_changes,
    snapshot,
    split_node_ref,
    split_page,
    stale_version,
)
from vrum.db import Database
from vrum.graph import (
    AFTER_NODE,
    BODY_PREVIEW_CHARS,
    EVENT_COLUMNS,
    NODE_ORDER,
    Event,
    EventPage,
    Node,
    after_position,
    bodies_left_out,
    list_columns,
)

RULE_STATUSES = ("open", "met", "dropped")
_RULE_COLUMNS = (
    "id, node, title, body, length(body) AS body_chars, status, version, created_at, updated_at"
)


def rule_list_columns(detail: bool) -> str:
    """The columns of a list of rules. Without `detail` the body is left out, and a short preview
    and its full length are given instead."""
    if detail:
        return _RULE_COLUMNS
    return _RULE_COLUMNS.replace(
        " body,", f" NULL::text AS body, left(body, {BODY_PREVIEW_CHARS}) AS body_preview,", 1
    )


COVERAGE_LISTS = ("both", "unplaced_rules", "stories_without_rules")
# A story is covered when it, or one of its tasks, holds an acceptance criterion that is not
# dropped.
_STORY_WITHOUT_RULES = (
    "kind = 'story' AND status <> 'dropped' AND NOT EXISTS ("
    "SELECT 1 FROM rules WHERE rules.status <> 'dropped' AND (rules.node = node_state.ref "
    "OR rules.node IN (SELECT t.ref FROM nodes t WHERE t.parent = node_state.ref)))"
)


class Rule(BaseModel):
    id: int = Field(
        description="The acceptance criterion's id. Acceptance criteria have their own ids, "
        "separate from nodes."
    )
    node: str | None = Field(
        description="The reference of the one story or task this acceptance criterion belongs to, "
        "such as story:12 or task:40. Empty while it is not placed."
    )
    title: str = Field(description="The acceptance criterion, stated briefly.")
    body: str | None = Field(
        description="Plain text or Markdown: the detail, or where it came from. Empty in a list "
        "that left bodies out: see body_preview, and ask for detail."
    )
    body_chars: int = Field(description="How many characters the body has.")
    body_preview: str | None = Field(
        default=None,
        description="Only in a list that left bodies out: the first characters of the body.",
    )
    status: str = Field(
        description="open (it stands and is still to be met), met (it has been met) or dropped "
        "(it no longer applies)."
    )
    version: int = Field(
        description="Starts at 1 and rises by one with every change. Send it back as "
        "expected_version when you change the acceptance criterion."
    )
    created_at: datetime
    updated_at: datetime


class RulePage(BaseModel):
    items: list[Rule]
    total: int = Field(description="How many acceptance criteria match in all, across pages.")
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )
    left_out: str | None = Field(
        description="Says what the list left out: bodies, unless you asked for detail. Empty when "
        "nothing was left out."
    )


class CoverageReport(BaseModel):
    unplaced_rule_count: int = Field(
        description="How many acceptance criteria are placed nowhere (on no story or task). "
        "Dropped ones are not counted."
    )
    unplaced_rules: list[Rule] = Field(
        description="One page of the acceptance criteria placed nowhere, oldest first."
    )
    next_unplaced_after_id: int | None = Field(
        description="Pass this as unplaced_after_id for the next page of unplaced acceptance "
        "criteria."
    )
    story_without_rules_count: int = Field(
        description="How many stories have no acceptance criterion that is open or met, on the "
        "story or on any of its tasks. Dropped stories are not counted."
    )
    stories_without_rules: list[Node] = Field(description="One page of them, by story number.")
    next_stories_after: str | None = Field(
        description="Pass this as stories_after for the next page of stories: the reference of "
        "the last story of this page."
    )
    left_out: str | None = Field(
        description="Says what the lists left out: bodies, unless you asked for detail. Empty "
        "when nothing was left out."
    )


async def require_rule(
    conn: AsyncConnection, rule_id: int, *, lock: bool = False
) -> dict[str, Any]:
    row = await fetch_one(
        conn,
        f"SELECT {_RULE_COLUMNS} FROM rules WHERE id = %s" + (" FOR UPDATE" if lock else ""),
        (rule_id,),
    )
    if row is None:
        raise Refusal(
            f"There is no acceptance criterion with id {rule_id}. Use list_rules to find the id "
            "you want."
        )
    return row


async def _require_holder(conn: AsyncConnection, node: str) -> None:
    """Refuse unless `node` is a story or task that exists: the only items that hold acceptance
    criteria."""
    kind, number = split_node_ref(node)
    if kind == "epic":
        raise Refusal(
            f"An acceptance criterion belongs to a story or a task, but {node} is an epic. "
            "Use list_nodes with kind=story or kind=task to find the one you want."
        )
    found = await fetch_one(conn, "SELECT 1 FROM nodes WHERE ref = %s", (node,))
    if found is None:
        raise Refusal(
            f"There is no {kind} {number} in this project, so an acceptance criterion cannot be "
            f"placed there. Use list_nodes with kind={kind} to find the one you want."
        )


def _clean_title(title: str) -> str:
    cleaned = title.strip()
    if not cleaned:
        raise Refusal("An acceptance criterion needs a title: title cannot be empty.")
    return cleaned


def _event_nodes(node: str | None, left: str | None = None) -> dict[str, Any]:
    """The items an event shows in the history of: the one the rule is on, and the one it left."""
    return {"node": node, "other_node": left if left != node else None}


async def insert_rule(
    conn: AsyncConnection,
    *,
    actor: str,
    title: str,
    body: str,
    status: str,
    node: str | None,
) -> dict[str, Any]:
    """Insert a rule that has been checked, and record it. Returns the stored row."""
    row = await fetch_one(
        conn,
        "INSERT INTO rules (node, title, body, status) VALUES (%s, %s, %s, %s) "
        f"RETURNING {_RULE_COLUMNS}",
        (node, title, body, status),
    )
    assert row is not None
    await record_event(
        conn,
        rule_id=row["id"],
        action="rule_created",
        actor=actor,
        before=None,
        after=snapshot(row, ("node", "title", "body", "status", "version")),
        **_event_nodes(node),
    )
    return row


class Rules:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_rule(
        self, *, actor: str, title: str, body: str, status: str, node: str | None
    ) -> Rule:
        title = _clean_title(title)
        check_choice("status", status, RULE_STATUSES)
        async with self._database.transaction() as conn:
            if node is not None:
                await _require_holder(conn, node)
            row = await insert_rule(
                conn, actor=actor, title=title, body=body, status=status, node=node
            )
        return Rule.model_validate(row)

    async def get_rule(self, rule_id: int) -> Rule:
        async with self._database.transaction() as conn:
            return Rule.model_validate(await require_rule(conn, rule_id))

    async def list_rules(
        self,
        *,
        node: str | None,
        unplaced: bool,
        status: str | None,
        detail: bool,
        limit: int,
        after_id: int | None,
    ) -> RulePage:
        limit = page_size(limit)
        if status is not None:
            check_choice("status", status, RULE_STATUSES)
        if unplaced and node is not None:
            raise Refusal(
                "Give node or unplaced, not both: an acceptance criterion that is on a story or "
                "task is not unplaced."
            )
        where = (
            "(%(node)s::text IS NULL OR node = %(node)s) "
            "AND (NOT %(unplaced)s OR node IS NULL) "
            "AND (%(status)s::text IS NULL OR status = %(status)s)"
        )
        params = {"node": node, "unplaced": unplaced, "status": status}
        async with self._database.transaction() as conn:
            if node is not None:
                await _require_holder(conn, node)
            counted = await fetch_one(
                conn, f"SELECT count(*) AS total FROM rules WHERE {where}", params
            )
            assert counted is not None
            rows = await fetch_all(
                conn,
                f"SELECT {rule_list_columns(detail)} FROM rules "
                f"WHERE {where} AND id > %(after)s ORDER BY id LIMIT %(fetch)s",
                {**params, "after": after_id or 0, "fetch": limit + 1},
            )
        page, next_after_id = split_page(rows, limit)
        items = [Rule.model_validate(row) for row in page]
        return RulePage(
            items=items,
            total=int(counted["total"]),
            next_after_id=next_after_id,
            left_out=None if detail else bodies_left_out(items),
        )

    async def update_rule(
        self,
        *,
        actor: str,
        rule_id: int,
        expected_version: int,
        title: str | None,
        body: str | None,
        status: str | None,
    ) -> Rule:
        if title is not None:
            title = _clean_title(title)
        if status is not None:
            check_choice("status", status, RULE_STATUSES)
        async with self._database.transaction() as conn:
            current = await require_rule(conn, rule_id, lock=True)
            if current["version"] != expected_version:
                raise stale_version(
                    "Acceptance criterion",
                    "list_rules",
                    rule_id,
                    current["version"],
                    expected_version,
                )
            wanted = {"title": title, "body": body, "status": status}
            changes = {
                field: value
                for field, value in wanted.items()
                if value is not None and value != current[field]
            }
            if not changes:
                raise Refusal(
                    f"Nothing to change: acceptance criterion {rule_id} already has the values you "
                    "gave, or you gave none. Give at least one of title, body or status with a "
                    "new value."
                )
            row = await save_changes(conn, "rules", rule_id, changes, _RULE_COLUMNS)
            await record_event(
                conn,
                rule_id=rule_id,
                action="rule_updated",
                actor=actor,
                before={**snapshot(current, tuple(changes)), "version": current["version"]},
                after={**changes, "version": row["version"]},
                **_event_nodes(current["node"]),
            )
        return Rule.model_validate(row)

    async def place_rule(
        self, *, actor: str, rule_id: int, expected_version: int, node: str
    ) -> Rule:
        """Put a rule on a story or a task. A rule that is on none is placed; one that is already
        on another is moved, as one step: it is never on two, and never on none, along the way.
        Placing it on the item it is already on is refused."""
        async with self._database.transaction() as conn:
            current = await require_rule(conn, rule_id, lock=True)
            if current["version"] != expected_version:
                raise stale_version(
                    "Acceptance criterion",
                    "list_rules",
                    rule_id,
                    current["version"],
                    expected_version,
                )
            await _require_holder(conn, node)
            was = current["node"]
            if was == node:
                raise Refusal(
                    f"Acceptance criterion {rule_id} is already on {node}, so nothing moved."
                )
            row = await save_changes(conn, "rules", rule_id, {"node": node}, _RULE_COLUMNS)
            await record_event(
                conn,
                rule_id=rule_id,
                action="rule_placed" if was is None else "rule_moved",
                actor=actor,
                before={"node": was, "version": current["version"]},
                after={"node": node, "version": row["version"]},
                **(_event_nodes(node) if was is None else _event_nodes(node, was)),
            )
        return Rule.model_validate(row)

    async def rule_history(self, *, rule_id: int, limit: int, after_id: int | None) -> EventPage:
        limit = page_size(limit)
        async with self._database.transaction() as conn:
            await require_rule(conn, rule_id)
            rows = await fetch_all(
                conn,
                f"SELECT {EVENT_COLUMNS} FROM events "
                "WHERE rule_id = %s AND id > %s ORDER BY id LIMIT %s",
                (rule_id, after_id or 0, limit + 1),
            )
        page, next_after_id = split_page(rows, limit)
        return EventPage(
            items=[Event.model_validate(row) for row in page], next_after_id=next_after_id
        )

    async def coverage_report(
        self,
        *,
        show: str,
        detail: bool,
        limit: int,
        unplaced_after_id: int | None,
        stories_after: str | None,
    ) -> CoverageReport:
        """Which acceptance criteria are placed nowhere, and which stories have none. A dropped
        criterion is not placed nowhere and does not cover a story; a dropped story needs none. A
        story is covered by a criterion on it or on one of its tasks."""
        limit = page_size(limit)
        check_choice("show", show, COVERAGE_LISTS)
        unplaced_page: list[dict[str, Any]] = []
        stories_page: list[dict[str, Any]] = []
        next_unplaced = next_stories = None
        async with self._database.transaction() as conn:
            if show in ("both", "unplaced_rules"):
                rows = await fetch_all(
                    conn,
                    f"SELECT {rule_list_columns(detail)} FROM rules "
                    "WHERE node IS NULL AND status <> 'dropped' AND id > %s "
                    "ORDER BY id LIMIT %s",
                    (unplaced_after_id or 0, limit + 1),
                )
                unplaced_page, next_unplaced = split_page(rows, limit)
            if show in ("both", "stories_without_rules"):
                rows = await fetch_all(
                    conn,
                    f"SELECT {list_columns(detail)} FROM node_state "
                    f"WHERE {_STORY_WITHOUT_RULES} AND {AFTER_NODE} "
                    f"ORDER BY {NODE_ORDER} LIMIT %(fetch)s",
                    {**after_position(stories_after), "fetch": limit + 1},
                )
                stories_page, next_stories = split_page(rows, limit, key="ref")
            unplaced_total = await fetch_one(
                conn,
                "SELECT count(*) AS total FROM rules WHERE node IS NULL AND status <> 'dropped'",
            )
            stories_total = await fetch_one(
                conn, f"SELECT count(*) AS total FROM node_state WHERE {_STORY_WITHOUT_RULES}"
            )
        assert unplaced_total is not None and stories_total is not None
        unplaced_rules = [Rule.model_validate(row) for row in unplaced_page]
        stories = [Node.model_validate(row) for row in stories_page]
        return CoverageReport(
            unplaced_rule_count=unplaced_total["total"],
            unplaced_rules=unplaced_rules,
            next_unplaced_after_id=next_unplaced,
            story_without_rules_count=stories_total["total"],
            stories_without_rules=stories,
            next_stories_after=next_stories,
            left_out=None if detail else bodies_left_out([*unplaced_rules, *stories]),
        )
