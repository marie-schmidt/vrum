"""The epics, stories and tasks. This module owns every question and change on nodes; the MCP
tools only call it."""

from datetime import date, datetime
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import (
    Refusal,
    check_choice,
    clean_actor,
    fetch_all,
    fetch_one,
    kind_rank,
    missing_node,
    page_size,
    record_event,
    save_changes,
    snapshot,
    split_node_ref,
    split_page,
    stale_version,
)
from vrum.db import Database
from vrum.query import compile_query

KINDS = ("epic", "story", "task")
# What a person or agent may set. `ready` is not here on purpose: it is worked out, never set.
STATUSES = ("new", "in_progress", "blocked", "done", "dropped")
# What a node can show, and so what a list can be filtered by.
SHOWN_STATUSES = ("new", "ready", "in_progress", "blocked", "done", "dropped")
PRIORITIES = ("low", "normal", "high", "urgent")
# The kinds that work can be handed out for: ready_work lists these unless asked for another.
WORKABLE_KINDS = ("story", "task")

_PARENT_KIND = {"story": "epic", "task": "story"}
_STORED_COLUMNS = (
    "ref, kind, number, parent, title, body, status, priority, start_on, target_on, version, "
    "created_at, updated_at"
)
STATE_COLUMNS = (
    _STORED_COLUMNS
    + ", body_chars, waiting_on, schedule_conflicts, overdue, span_start, span_end, "
    "span_worked_out, claimed_by, claim_expires_at, lapsed_claim_by, lapsed_claim_at, "
    "progress_done, progress_total"
)
# How much of a body a compact list shows.
BODY_PREVIEW_CHARS = 120
# Nodes are listed epics first, then stories, then tasks, each by number. A page continues after
# the position of the last node of the one before (see `after_position`).
NODE_ORDER = "kind_rank, number"
AFTER_NODE = "(kind_rank, number) > (%(after_rank)s, %(after_number)s)"


def after_position(after: str | None) -> dict[str, int]:
    """The values `AFTER_NODE` needs to continue after the node `after` (a reference), or to
    start from the beginning when it is empty."""
    if after is None:
        return {"after_rank": 0, "after_number": 0}
    kind, number = split_node_ref(after)
    return {"after_rank": kind_rank(kind), "after_number": number}


def list_columns(detail: bool) -> str:
    """The columns of a list of nodes. Without `detail` the body is left out (the database does
    not even send it), and a short preview and its full length are given instead."""
    if detail:
        return STATE_COLUMNS
    return STATE_COLUMNS.replace(
        " body,", f" NULL::text AS body, left(body, {BODY_PREVIEW_CHARS}) AS body_preview,", 1
    )


EVENT_COLUMNS = (
    "id, node, other_node, rule_id, decision_id, fact_id, action, actor, at, before, after"
)


class Node(BaseModel):
    ref: str = Field(
        description="The item's reference: its kind and its number within that kind, such as "
        "story:12. This is how every tool names it."
    )
    kind: str = Field(description="epic, story or task.")
    number: int = Field(
        description="Its number among the epics, the stories or the tasks of the project: each "
        "kind is numbered from 1 on its own, so epic:3, story:3 and task:3 are three items."
    )
    parent: str | None = Field(
        description="The reference of the epic a story belongs to, or of the story a task "
        "belongs to, such as epic:3. Empty for an epic."
    )
    title: str
    body: str | None = Field(
        description="Plain text or Markdown. Empty in a list that left bodies out: see "
        "body_preview, and ask for detail or use get_node to read it."
    )
    body_chars: int = Field(description="How many characters the body has.")
    body_preview: str | None = Field(
        default=None,
        description="Only in a list that left bodies out: the first characters of the body.",
    )
    status: str = Field(
        description="new, ready, in_progress, blocked, done or dropped. A node nobody has started "
        "shows as ready when everything it needs first is done (or it needs nothing), and as new "
        "while something it needs first is still open. ready is worked out, never set."
    )
    priority: str = Field(description="low, normal, high or urgent.")
    start_on: date | None = Field(
        description="The day work on it is planned to start, as YYYY-MM-DD. Empty when not set."
    )
    target_on: date | None = Field(
        description="The day it is planned to be done by, as YYYY-MM-DD. Empty when not set. "
        "The start is never after the target."
    )
    version: int = Field(
        description="Starts at 1 and rises by one with every change. Send it back as "
        "expected_version when you change the node."
    )
    created_at: datetime
    updated_at: datetime
    waiting_on: list[str] = Field(
        description="The references of the nodes it needs first (needs_first links) that are not "
        "done yet, such as ['task:4']. While there are any, it cannot be started."
    )
    schedule_conflicts: list[str] = Field(
        description="The references of the nodes it needs first, still not done, whose target date "
        "is later than this node's own start date: it is planned to start before what it needs is "
        "due. A warning only: nothing is refused."
    )
    overdue: bool = Field(
        description="True when its own target date has passed and it is not done or dropped."
    )
    span_start: date | None = Field(
        description="The first day to draw on a roadmap: its own start date, or for an epic or "
        "story with no dates of its own, the earliest start among what is under it."
    )
    span_end: date | None = Field(
        description="The last day to draw on a roadmap: its own target date, or for an epic or "
        "story with no dates of its own, the latest target among what is under it. Dropped work "
        "does not count."
    )
    span_worked_out: bool = Field(
        description="True when span_start and span_end were worked out from what is under the "
        "node because it has no dates of its own."
    )
    claimed_by: str | None = Field(description="Who holds a claim on it right now, if anyone.")
    claim_expires_at: datetime | None = Field(
        description="When that claim lapses on its own unless it is renewed."
    )
    lapsed_claim_by: str | None = Field(
        description="Set when someone's claim ran out without being renewed, released or taken "
        "over: they left it with no handoff note, so ask what state it is in."
    )
    lapsed_claim_at: datetime | None = Field(description="When that claim ran out.")
    progress_done: int | None = Field(
        description="For an epic or a story: how many of its tasks are done. For an epic, the "
        "tasks of its stories that are not dropped. Empty for a task."
    )
    progress_total: int | None = Field(
        description="For an epic or a story: how many tasks it has that are not dropped (the "
        "most progress_done can reach). Empty for a task."
    )


class Handoff(BaseModel):
    id: int = Field(description="The id of the event that recorded it.")
    actor: str = Field(description="Who left the work.")
    at: datetime
    outcome: str = Field(description="done, blocked, dropped or released.")
    note: str = Field(description="What was done, and what the next person must know.")


class NodeDetail(Node):
    handoff_count: int = Field(description="How many handoff notes the node has in its history.")
    last_handoff: Handoff | None = Field(
        description="The latest handoff note, in full. node_history has all of them."
    )


class NodePage(BaseModel):
    items: list[Node]
    total: int = Field(description="How many nodes match in all, across pages.")
    next_after: str | None = Field(
        description="Pass this as after to get the next page: the reference of the last node of "
        "this one. Empty when there are no more."
    )
    left_out: str | None = Field(
        description="Says what the list left out: bodies, unless you asked for detail. Empty when "
        "nothing was left out."
    )


class Event(BaseModel):
    id: int = Field(description="The event's id. Events are listed oldest first.")
    node: str | None = Field(
        description="The reference of the node the event is about. For a rule, the story it is in "
        "after the event."
    )
    other_node: str | None = Field(
        description="For a link, the node at its other end. For a rule that moved, the story it "
        "left. The event shows in the history of both nodes."
    )
    rule_id: int | None = Field(description="The rule the event is about, if it is about a rule.")
    decision_id: int | None = Field(
        description="The decision the event is about, if it is about a decision, or the decision "
        "a fact concerns."
    )
    fact_id: int | None = Field(description="The fact the event is about, if it is about one.")
    action: str = Field(
        description="create, update, move, link_added, link_removed, claimed, claim_renewed, "
        "claim_released, handoff, rule_created, rule_updated, rule_placed, rule_moved, "
        "decision_recorded, decision_superseded, decision_withdrawn, decision_linked, "
        "decision_unlinked, decision_confirmed, decision_unconfirmed, fact_recorded, "
        "fact_superseded, fact_withdrawn, fact_linked, fact_unlinked, fact_confirmed or "
        "fact_unconfirmed."
    )
    actor: str = Field(description="The person or agent that made the change.")
    at: datetime
    before: dict[str, Any] | None = Field(
        description="The changed fields as they were, with the version where there is one. Empty "
        "when there was nothing before."
    )
    after: dict[str, Any] = Field(
        description="The changed fields as they became. A create lists all of them."
    )


class EventPage(BaseModel):
    items: list[Event]
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )


def _a(kind: str) -> str:
    return f"an {kind}" if kind == "epic" else f"a {kind}"


def clean_title(title: str) -> str:
    cleaned = title.strip()
    if not cleaned:
        raise Refusal("A node needs a title: title cannot be empty.")
    return cleaned


def check_dates(start_on: date | None, target_on: date | None) -> None:
    if start_on is not None and target_on is not None and start_on > target_on:
        raise Refusal(
            f"The start date {start_on} is after the target date {target_on}. Work cannot start "
            "after it is due: move the start earlier or the target later."
        )


async def require_node(conn: AsyncConnection, ref: str, *, lock: bool = False) -> dict[str, Any]:
    """The node as stored, or a plain refusal. `lock` holds the row until the transaction ends."""
    row = await fetch_one(
        conn,
        f"SELECT {_STORED_COLUMNS} FROM nodes WHERE ref = %s" + (" FOR UPDATE" if lock else ""),
        (ref,),
    )
    if row is None:
        raise missing_node(ref)
    return row


async def node_state(conn: AsyncConnection, ref: str) -> Node:
    """The node as it is read: with `ready` worked out, what it waits for, and who holds it."""
    row = await fetch_one(conn, f"SELECT {STATE_COLUMNS} FROM node_state WHERE ref = %s", (ref,))
    if row is None:
        raise missing_node(ref)
    return Node.model_validate(row)


async def handoffs_of(
    conn: AsyncConnection, node: str, *, newest_first: bool = False
) -> list[Handoff]:
    """Every handoff note a node has, oldest first (or newest first)."""
    rows = await fetch_all(
        conn,
        "SELECT id, actor, at, after->>'outcome' AS outcome, after->>'note' AS note FROM events "
        "WHERE node = %s AND action = 'handoff' "
        f"ORDER BY id {'DESC' if newest_first else 'ASC'}",
        (node,),
    )
    return [Handoff.model_validate(row) for row in rows]


def bodies_left_out(nodes_or_rules: list[Any]) -> str | None:
    """What a compact list says it left out: bodies. Nothing, if it left out nothing."""
    chars = sum(item.body_chars for item in nodes_or_rules if item.body_chars)
    count = sum(1 for item in nodes_or_rules if item.body_chars)
    if not nodes_or_rules or not count:
        return None
    return (
        f"Bodies are left out ({count} of them, {chars:,} characters): each item has body_chars "
        f"and a body_preview. Ask for detail to include them."
    )


async def change_status(
    conn: AsyncConnection, actor: str, current: dict[str, Any], status: str
) -> dict[str, Any]:
    """Save a new stored status on a node the caller has locked and read, and record it as an
    update in the history. Returns the stored row."""
    row = await save_changes(
        conn, "nodes", current["ref"], {"status": status}, _STORED_COLUMNS, key="ref"
    )
    await record_event(
        conn,
        node=current["ref"],
        action="update",
        actor=actor,
        before={"status": current["status"], "version": current["version"]},
        after={"status": row["status"], "version": row["version"]},
    )
    return row


async def _require_parent(conn: AsyncConnection, kind: str, parent: str | None) -> None:
    """Refuse a parent that would break the hierarchy, in words that say how to fix it."""
    if kind == "epic":
        if parent is not None:
            raise Refusal("An epic has no parent. Leave parent out when you create an epic.")
        return
    wanted = _PARENT_KIND[kind]
    if parent is None:
        raise Refusal(
            f"{_a(kind).capitalize()} needs a parent {wanted}. Give parent, the reference of the "
            f"{wanted} it belongs to, such as {wanted}:3. Use list_nodes with kind={wanted} to "
            "find one, or create one first."
        )
    parent_kind = split_node_ref(parent)[0]
    if parent_kind != wanted:
        raise Refusal(
            f"{_a(kind).capitalize()} belongs under {_a(wanted)}, but {parent} is "
            f"{_a(parent_kind)}. Give the reference of {_a(wanted)} as parent."
        )
    found = await fetch_one(conn, "SELECT 1 FROM nodes WHERE ref = %s", (parent,))
    if found is None:
        raise Refusal(
            f"There is no {wanted} {split_node_ref(parent)[1]} in this project, so it cannot be "
            f"the parent. Use list_nodes with kind={wanted} to find the one you want."
        )


async def insert_node(
    conn: AsyncConnection,
    *,
    actor: str,
    kind: str,
    title: str,
    parent: str | None,
    body: str,
    priority: str,
    start_on: date | None,
    target_on: date | None,
) -> dict[str, Any]:
    """Insert a node whose fields and parent have been checked, and record it. It is given the
    next number of its kind. The database still refuses anything that breaks the hierarchy.
    Returns the stored row."""
    row = await fetch_one(
        conn,
        "INSERT INTO nodes (kind, number, parent, title, body, priority, start_on, target_on) "
        f"VALUES (%s, next_id(%s), %s, %s, %s, %s, %s, %s) RETURNING {_STORED_COLUMNS}",
        (kind, kind, parent, title, body, priority, start_on, target_on),
    )
    assert row is not None
    await record_event(
        conn,
        node=row["ref"],
        action="create",
        actor=actor,
        before=None,
        after=snapshot(
            row,
            (
                "kind",
                "parent",
                "title",
                "body",
                "status",
                "priority",
                "start_on",
                "target_on",
                "version",
            ),
        ),
    )
    return row


async def _node_page(
    conn: AsyncConnection,
    where: str,
    params: dict[str, Any],
    detail: bool,
    limit: int,
    after: str | None,
) -> NodePage:
    """One page of nodes matching `where` (written by the code, never by a caller), with how many
    match in all and what the page left out."""
    counted = await fetch_one(
        conn, f"SELECT count(*) AS total FROM node_state WHERE {where}", params
    )
    assert counted is not None
    rows = await fetch_all(
        conn,
        f"SELECT {list_columns(detail)} FROM node_state WHERE {where} AND {AFTER_NODE} "
        f"ORDER BY {NODE_ORDER} LIMIT %(fetch)s",
        {**params, **after_position(after), "fetch": limit + 1},
    )
    page, next_after = split_page(rows, limit, key="ref")
    items = [Node.model_validate(row) for row in page]
    return NodePage(
        items=items,
        total=int(counted["total"]),
        next_after=next_after,
        left_out=None if detail else bodies_left_out(items),
    )


class Graph:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_node(
        self,
        *,
        actor: str,
        kind: str,
        title: str,
        parent: str | None,
        body: str,
        priority: str,
        start_on: date | None,
        target_on: date | None,
    ) -> Node:
        actor = clean_actor(actor)
        check_choice("kind", kind, KINDS)
        title = clean_title(title)
        check_choice("priority", priority, PRIORITIES)
        check_dates(start_on, target_on)
        async with self._database.transaction() as conn:
            await _require_parent(conn, kind, parent)
            row = await insert_node(
                conn,
                actor=actor,
                kind=kind,
                title=title,
                parent=parent,
                body=body,
                priority=priority,
                start_on=start_on,
                target_on=target_on,
            )
            return await node_state(conn, row["ref"])

    async def get_node(self, node: str) -> NodeDetail:
        async with self._database.transaction() as conn:
            found = await node_state(conn, node)
            notes = await handoffs_of(conn, node, newest_first=True)
        return NodeDetail(
            **found.model_dump(),
            handoff_count=len(notes),
            last_handoff=notes[0] if notes else None,
        )

    async def list_nodes(
        self,
        *,
        kind: str | None,
        parent: str | None,
        status: str | None,
        start_from: date | None,
        start_to: date | None,
        target_from: date | None,
        target_to: date | None,
        overdue: bool | None,
        detail: bool,
        limit: int,
        after: str | None,
        query: str | None = None,
    ) -> NodePage:
        limit = page_size(limit)
        if kind is not None:
            check_choice("kind", kind, KINDS)
        if status is not None:
            check_choice("status", status, SHOWN_STATUSES)
        asked, asked_params = compile_query(query)
        where = (
            "(%(kind)s::text IS NULL OR kind = %(kind)s) "
            "AND (%(parent)s::text IS NULL OR parent = %(parent)s) "
            "AND (%(status)s::text IS NULL OR status = %(status)s) "
            "AND (%(start_from)s::date IS NULL OR start_on >= %(start_from)s) "
            "AND (%(start_to)s::date IS NULL OR start_on <= %(start_to)s) "
            "AND (%(target_from)s::date IS NULL OR target_on >= %(target_from)s) "
            "AND (%(target_to)s::date IS NULL OR target_on <= %(target_to)s) "
            "AND (%(overdue)s::boolean IS NULL OR overdue = %(overdue)s) "
            f"AND {asked}"
        )
        params = {
            **asked_params,
            "kind": kind,
            "parent": parent,
            "status": status,
            "start_from": start_from,
            "start_to": start_to,
            "target_from": target_from,
            "target_to": target_to,
            "overdue": overdue,
        }
        async with self._database.transaction() as conn:
            if parent is not None:
                await require_node(conn, parent)
            return await _node_page(conn, where, params, detail, limit, after)

    async def ready_work(
        self, *, kind: str | None, detail: bool, limit: int, after: str | None
    ) -> NodePage:
        """Stories and tasks (or the one kind asked for) that can be started now and that nobody
        holds a claim on."""
        limit = page_size(limit)
        if kind is not None:
            check_choice("kind", kind, KINDS)
        kinds = list(WORKABLE_KINDS) if kind is None else [kind]
        async with self._database.transaction() as conn:
            return await _node_page(
                conn,
                "status = 'ready' AND claimed_by IS NULL AND kind = ANY(%(kinds)s)",
                {"kinds": kinds},
                detail,
                limit,
                after,
            )

    async def update_node(
        self,
        *,
        actor: str,
        node: str,
        expected_version: int,
        title: str | None,
        body: str | None,
        status: str | None,
        priority: str | None,
        parent: str | None,
        start_on: date | None,
        target_on: date | None,
        clear_start_on: bool,
        clear_target_on: bool,
    ) -> Node:
        actor = clean_actor(actor)
        if title is not None:
            title = clean_title(title)
        if status is not None:
            check_choice(
                "status",
                status,
                STATUSES,
                " (ready is worked out by the server, so you cannot choose it)"
                if status == "ready"
                else "",
            )
        if priority is not None:
            check_choice("priority", priority, PRIORITIES)
        for given, clear, name in (
            (start_on, clear_start_on, "start_on"),
            (target_on, clear_target_on, "target_on"),
        ):
            if given is not None and clear:
                raise Refusal(
                    f"Give a new {name} or ask for it to be cleared (clear_{name}), not both."
                )
        async with self._database.transaction() as conn:
            current = await require_node(conn, node, lock=True)
            if current["version"] != expected_version:
                raise stale_version("Node", "get_node", node, current["version"], expected_version)
            if parent is not None:
                if current["kind"] == "epic":
                    raise Refusal("An epic has no parent. Leave parent out.")
                if parent != current["parent"]:
                    raise Refusal(
                        f"{node} already belongs to {current['parent']}, and a node has exactly "
                        f"one parent. To put it under {parent} instead, use move_node."
                    )
            wanted: dict[str, Any] = {
                "title": title,
                "body": body,
                "status": status,
                "priority": priority,
                "start_on": start_on,
                "target_on": target_on,
            }
            changes = {
                field: value
                for field, value in wanted.items()
                if value is not None and value != current[field]
            }
            for field, clear in (("start_on", clear_start_on), ("target_on", clear_target_on)):
                if clear and current[field] is not None:
                    changes[field] = None
            if not changes:
                raise Refusal(
                    f"Nothing to change: {node} already has the values you gave, or you "
                    "gave none. Give at least one of title, body, status, priority, start_on or "
                    "target_on with a new value, or ask for a date to be cleared."
                )
            check_dates(
                changes.get("start_on", current["start_on"]),
                changes.get("target_on", current["target_on"]),
            )
            row = await save_changes(conn, "nodes", node, changes, _STORED_COLUMNS, key="ref")
            await record_event(
                conn,
                node=node,
                action="update",
                actor=actor,
                before={**snapshot(current, tuple(changes)), "version": current["version"]},
                after={**snapshot(row, tuple(changes)), "version": row["version"]},
            )
            return await node_state(conn, node)

    async def move_node(
        self, *, actor: str, node: str, expected_version: int, new_parent: str
    ) -> Node:
        actor = clean_actor(actor)
        async with self._database.transaction() as conn:
            current = await require_node(conn, node, lock=True)
            if current["version"] != expected_version:
                raise stale_version("Node", "get_node", node, current["version"], expected_version)
            if current["kind"] == "epic":
                raise Refusal(f"{node} is an epic. An epic has no parent, so it cannot be moved.")
            await _require_parent(conn, current["kind"], new_parent)
            if new_parent == current["parent"]:
                raise Refusal(f"{node} is already under {new_parent}, so nothing moved.")
            row = await save_changes(
                conn, "nodes", node, {"parent": new_parent}, _STORED_COLUMNS, key="ref"
            )
            await record_event(
                conn,
                node=node,
                action="move",
                actor=actor,
                before={"parent": current["parent"], "version": current["version"]},
                after={"parent": new_parent, "version": row["version"]},
            )
            return await node_state(conn, node)

    async def node_history(self, *, node: str, limit: int, after_id: int | None) -> EventPage:
        limit = page_size(limit)
        async with self._database.transaction() as conn:
            await require_node(conn, node)
            rows = await fetch_all(
                conn,
                f"SELECT {EVENT_COLUMNS} FROM events "
                "WHERE (node = %(node)s OR other_node = %(node)s) AND id > %(after)s "
                "ORDER BY id LIMIT %(fetch)s",
                {"node": node, "after": after_id or 0, "fetch": limit + 1},
            )
        page, next_after_id = split_page(rows, limit)
        return EventPage(
            items=[Event.model_validate(row) for row in page], next_after_id=next_after_id
        )
