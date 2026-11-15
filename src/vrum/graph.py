"""The epics, stories and tasks. This module owns every question and change on nodes; the MCP
tools only call it."""

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import (
    Refusal,
    check_choice,
    clean_actor,
    fetch_all,
    fetch_one,
    page_size,
    record_event,
    save_changes,
    snapshot,
    split_page,
    stale_version,
)
from vrum.db import Database

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
    "id, kind, parent_id, title, body, status, priority, version, created_at, updated_at"
)
STATE_COLUMNS = _STORED_COLUMNS + ", waiting_on, claimed_by, claim_expires_at"
EVENT_COLUMNS = "id, node_id, other_node_id, rule_id, decision_id, action, actor, at, before, after"


class Node(BaseModel):
    id: int = Field(description="The node's id.")
    kind: str = Field(description="epic, story or task.")
    parent_id: int | None = Field(
        description="The epic a story belongs to, or the story a task belongs to. Empty for an "
        "epic."
    )
    title: str
    body: str = Field(description="Plain text or Markdown.")
    status: str = Field(
        description="new, ready, in_progress, blocked, done or dropped. A node nobody has started "
        "shows as ready when everything it needs first is done (or it needs nothing), and as new "
        "while something it needs first is still open. ready is worked out, never set."
    )
    priority: str = Field(description="low, normal, high or urgent.")
    version: int = Field(
        description="Starts at 1 and rises by one with every change. Send it back as "
        "expected_version when you change the node."
    )
    created_at: datetime
    updated_at: datetime
    waiting_on: list[int] = Field(
        description="The ids of the nodes it needs first (needs_first links) that are not done "
        "yet. While there are any, it cannot be started."
    )
    claimed_by: str | None = Field(description="Who holds a claim on it right now, if anyone.")
    claim_expires_at: datetime | None = Field(
        description="When that claim lapses on its own unless it is renewed."
    )


class NodePage(BaseModel):
    items: list[Node]
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )


class Event(BaseModel):
    id: int = Field(description="The event's id. Events are listed oldest first.")
    node_id: int | None = Field(
        description="The node the event is about. For a rule, the story it is in after the event."
    )
    other_node_id: int | None = Field(
        description="For a link, the node at its other end. For a rule that moved, the story it "
        "left. The event shows in the history of both nodes."
    )
    rule_id: int | None = Field(description="The rule the event is about, if it is about a rule.")
    decision_id: int | None = Field(
        description="The decision the event is about, if it is about a decision."
    )
    action: str = Field(
        description="create, update, move, link_added, link_removed, claimed, claim_renewed, "
        "claim_released, rule_created, rule_updated, rule_placed, rule_moved, decision_recorded, "
        "decision_superseded, decision_linked or decision_unlinked."
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


def _clean_title(title: str) -> str:
    cleaned = title.strip()
    if not cleaned:
        raise Refusal("A node needs a title: title cannot be empty.")
    return cleaned


async def require_node(
    conn: AsyncConnection, node_id: int, *, lock: bool = False
) -> dict[str, Any]:
    """The node as stored, or a plain refusal. `lock` holds the row until the transaction ends."""
    row = await fetch_one(
        conn,
        f"SELECT {_STORED_COLUMNS} FROM nodes WHERE id = %s" + (" FOR UPDATE" if lock else ""),
        (node_id,),
    )
    if row is None:
        raise Refusal(
            f"There is no node with id {node_id}. Use list_nodes to find the id you want."
        )
    return row


async def node_state(conn: AsyncConnection, node_id: int) -> Node:
    """The node as it is read: with `ready` worked out, what it waits for, and who holds it."""
    row = await fetch_one(conn, f"SELECT {STATE_COLUMNS} FROM node_state WHERE id = %s", (node_id,))
    if row is None:
        raise Refusal(
            f"There is no node with id {node_id}. Use list_nodes to find the id you want."
        )
    return Node.model_validate(row)


async def _require_parent(conn: AsyncConnection, kind: str, parent_id: int | None) -> None:
    """Refuse a parent that would break the hierarchy, in words that say how to fix it."""
    if kind == "epic":
        if parent_id is not None:
            raise Refusal("An epic has no parent. Leave parent_id out when you create an epic.")
        return
    wanted = _PARENT_KIND[kind]
    if parent_id is None:
        raise Refusal(
            f"{_a(kind).capitalize()} needs a parent {wanted}. Give parent_id, the id of the "
            f"{wanted} it belongs to. Use list_nodes with kind={wanted} to find one, or create "
            "one first."
        )
    parent = await fetch_one(conn, "SELECT kind FROM nodes WHERE id = %s", (parent_id,))
    if parent is None:
        raise Refusal(
            f"There is no node with id {parent_id}, so it cannot be the parent. "
            f"Use list_nodes with kind={wanted} to find the id you want."
        )
    if parent["kind"] != wanted:
        raise Refusal(
            f"{_a(kind).capitalize()} belongs under {_a(wanted)}, but node {parent_id} is "
            f"{_a(parent['kind'])}. Use the id of {_a(wanted)} instead."
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
        parent_id: int | None,
        body: str,
        priority: str,
    ) -> Node:
        actor = clean_actor(actor)
        check_choice("kind", kind, KINDS)
        title = _clean_title(title)
        check_choice("priority", priority, PRIORITIES)
        async with self._database.transaction() as conn:
            await _require_parent(conn, kind, parent_id)
            row = await fetch_one(
                conn,
                "INSERT INTO nodes (kind, parent_id, title, body, priority) "
                f"VALUES (%s, %s, %s, %s, %s) RETURNING {_STORED_COLUMNS}",
                (kind, parent_id, title, body, priority),
            )
            assert row is not None
            await record_event(
                conn,
                node_id=row["id"],
                action="create",
                actor=actor,
                before=None,
                after=snapshot(
                    row, ("kind", "parent_id", "title", "body", "status", "priority", "version")
                ),
            )
            return await node_state(conn, row["id"])

    async def get_node(self, node_id: int) -> Node:
        async with self._database.transaction() as conn:
            return await node_state(conn, node_id)

    async def list_nodes(
        self,
        *,
        kind: str | None,
        parent_id: int | None,
        status: str | None,
        limit: int,
        after_id: int | None,
    ) -> NodePage:
        limit = page_size(limit)
        if kind is not None:
            check_choice("kind", kind, KINDS)
        if status is not None:
            check_choice("status", status, SHOWN_STATUSES)
        async with self._database.transaction() as conn:
            if parent_id is not None:
                await require_node(conn, parent_id)
            rows = await fetch_all(
                conn,
                f"SELECT {STATE_COLUMNS} FROM node_state "
                "WHERE (%(kind)s::text IS NULL OR kind = %(kind)s) "
                "AND (%(parent_id)s::bigint IS NULL OR parent_id = %(parent_id)s) "
                "AND (%(status)s::text IS NULL OR status = %(status)s) "
                "AND id > %(after_id)s ORDER BY id LIMIT %(fetch)s",
                {
                    "kind": kind,
                    "parent_id": parent_id,
                    "status": status,
                    "after_id": after_id or 0,
                    "fetch": limit + 1,
                },
            )
        page, next_after_id = split_page(rows, limit)
        return NodePage(
            items=[Node.model_validate(row) for row in page], next_after_id=next_after_id
        )

    async def ready_work(self, *, kind: str | None, limit: int, after_id: int | None) -> NodePage:
        """Stories and tasks (or the one kind asked for) that can be started now and that nobody
        holds a claim on."""
        limit = page_size(limit)
        if kind is not None:
            check_choice("kind", kind, KINDS)
        kinds = list(WORKABLE_KINDS) if kind is None else [kind]
        async with self._database.transaction() as conn:
            rows = await fetch_all(
                conn,
                f"SELECT {STATE_COLUMNS} FROM node_state "
                "WHERE status = 'ready' AND claimed_by IS NULL AND kind = ANY(%s) "
                "AND id > %s ORDER BY id LIMIT %s",
                (kinds, after_id or 0, limit + 1),
            )
        page, next_after_id = split_page(rows, limit)
        return NodePage(
            items=[Node.model_validate(row) for row in page], next_after_id=next_after_id
        )

    async def update_node(
        self,
        *,
        actor: str,
        node_id: int,
        expected_version: int,
        title: str | None,
        body: str | None,
        status: str | None,
        priority: str | None,
        parent_id: int | None,
    ) -> Node:
        actor = clean_actor(actor)
        if title is not None:
            title = _clean_title(title)
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
        async with self._database.transaction() as conn:
            current = await require_node(conn, node_id, lock=True)
            if current["version"] != expected_version:
                raise stale_version(
                    "Node", "get_node", node_id, current["version"], expected_version
                )
            if parent_id is not None:
                if current["kind"] == "epic":
                    raise Refusal("An epic has no parent. Leave parent_id out.")
                if parent_id != current["parent_id"]:
                    raise Refusal(
                        f"Node {node_id} already belongs to node {current['parent_id']}, and a "
                        f"node has exactly one parent. To put it under node {parent_id} instead, "
                        "use move_node."
                    )
            wanted = {"title": title, "body": body, "status": status, "priority": priority}
            changes = {
                field: value
                for field, value in wanted.items()
                if value is not None and value != current[field]
            }
            if not changes:
                raise Refusal(
                    f"Nothing to change: node {node_id} already has the values you gave, or you "
                    "gave none. Give at least one of title, body, status or priority with a new "
                    "value."
                )
            row = await save_changes(conn, "nodes", node_id, changes, _STORED_COLUMNS)
            await record_event(
                conn,
                node_id=node_id,
                action="update",
                actor=actor,
                before={**snapshot(current, tuple(changes)), "version": current["version"]},
                after={**changes, "version": row["version"]},
            )
            return await node_state(conn, node_id)

    async def move_node(
        self, *, actor: str, node_id: int, expected_version: int, new_parent_id: int
    ) -> Node:
        actor = clean_actor(actor)
        async with self._database.transaction() as conn:
            current = await require_node(conn, node_id, lock=True)
            if current["version"] != expected_version:
                raise stale_version(
                    "Node", "get_node", node_id, current["version"], expected_version
                )
            if current["kind"] == "epic":
                raise Refusal(
                    f"Node {node_id} is an epic. An epic has no parent, so it cannot be moved."
                )
            await _require_parent(conn, current["kind"], new_parent_id)
            if new_parent_id == current["parent_id"]:
                raise Refusal(
                    f"Node {node_id} is already under node {new_parent_id}, so nothing moved."
                )
            row = await save_changes(
                conn, "nodes", node_id, {"parent_id": new_parent_id}, _STORED_COLUMNS
            )
            await record_event(
                conn,
                node_id=node_id,
                action="move",
                actor=actor,
                before={"parent_id": current["parent_id"], "version": current["version"]},
                after={"parent_id": new_parent_id, "version": row["version"]},
            )
            return await node_state(conn, node_id)

    async def node_history(self, *, node_id: int, limit: int, after_id: int | None) -> EventPage:
        limit = page_size(limit)
        async with self._database.transaction() as conn:
            await require_node(conn, node_id)
            rows = await fetch_all(
                conn,
                f"SELECT {EVENT_COLUMNS} FROM events "
                "WHERE (node_id = %(node)s OR other_node_id = %(node)s) AND id > %(after)s "
                "ORDER BY id LIMIT %(fetch)s",
                {"node": node_id, "after": after_id or 0, "fetch": limit + 1},
            )
        page, next_after_id = split_page(rows, limit)
        return EventPage(
            items=[Event.model_validate(row) for row in page], next_after_id=next_after_id
        )
