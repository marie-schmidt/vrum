"""The graph of epics, stories and tasks. This module owns every question and change on nodes;
the MCP tools only call it."""

from datetime import datetime
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from vrum.db import Database

KINDS = ("epic", "story", "task")
# `ready` is not here on purpose: it is worked out from the links, never set by a person.
STATUSES = ("new", "in_progress", "blocked", "done", "dropped")
PRIORITIES = ("low", "normal", "high", "urgent")

_PARENT_KIND = {"story": "epic", "task": "story"}
_NODE_COLUMNS = (
    "id, kind, parent_id, title, body, status, priority, version, created_at, updated_at"
)
_MAX_PAGE_SIZE = 200


class Refusal(ToolError):
    """A request the graph will not carry out. The message says what was wrong and what to do."""


class Node(BaseModel):
    id: int = Field(description="The node's id.")
    kind: str = Field(description="epic, story or task.")
    parent_id: int | None = Field(
        description="The epic a story belongs to, or the story a task belongs to. Empty for an "
        "epic."
    )
    title: str
    body: str = Field(description="Plain text or Markdown.")
    status: str = Field(description="new, in_progress, blocked, done or dropped.")
    priority: str = Field(description="low, normal, high or urgent.")
    version: int = Field(
        description="Starts at 1 and rises by one with every change. Send it back as "
        "expected_version when you change the node."
    )
    created_at: datetime
    updated_at: datetime


class NodePage(BaseModel):
    items: list[Node]
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )


class NodeEvent(BaseModel):
    id: int = Field(description="The event's id. Events are listed oldest first.")
    node_id: int
    action: str = Field(description="create, update or move.")
    actor: str = Field(description="The person or agent that made the change.")
    at: datetime
    before: dict[str, Any] | None = Field(
        description="The changed fields as they were, with the version. Empty for a create."
    )
    after: dict[str, Any] = Field(
        description="The changed fields as they became, with the version. A create lists all."
    )


class EventPage(BaseModel):
    items: list[NodeEvent]
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )


def _a(kind: str) -> str:
    return f"an {kind}" if kind == "epic" else f"a {kind}"


def _clean_actor(actor: str) -> str:
    cleaned = actor.strip()
    if not cleaned:
        raise Refusal(
            "Say who is making this change: actor must be the name of a person or an agent."
        )
    return cleaned


def _clean_title(title: str) -> str:
    cleaned = title.strip()
    if not cleaned:
        raise Refusal("A node needs a title: title cannot be empty.")
    return cleaned


def _check_choice(label: str, value: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        extra = (
            " (ready is worked out by the server, so you cannot choose it)"
            if value == "ready"
            else ""
        )
        raise Refusal(f"{label} must be one of {', '.join(allowed)}, but it is {value!r}{extra}.")
    return value


def _page_size(limit: int) -> int:
    if not 1 <= limit <= _MAX_PAGE_SIZE:
        raise Refusal(
            f"limit must be a whole number from 1 to {_MAX_PAGE_SIZE}, but it is {limit}."
        )
    return limit


def _stale(node_id: int, current: int, expected: int) -> Refusal:
    return Refusal(
        f"Node {node_id} is at version {current}, but you sent version {expected}. "
        "Someone else changed it after you read it. Read it again with get_node, check that your "
        f"change still makes sense, then repeat it with expected_version {current}."
    )


async def _fetch_one(conn: AsyncConnection, sql: str, params: Any = None) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(sql, params)
        return await cursor.fetchone()


async def _fetch_all(conn: AsyncConnection, sql: str, params: Any = None) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(sql, params)
        return await cursor.fetchall()


def _snapshot(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: row[field] for field in fields}


async def _require_node(
    conn: AsyncConnection, node_id: int, *, lock: bool = False
) -> dict[str, Any]:
    row = await _fetch_one(
        conn,
        f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id = %s" + (" FOR UPDATE" if lock else ""),
        (node_id,),
    )
    if row is None:
        raise Refusal(
            f"There is no node with id {node_id}. Use list_nodes to find the id you want."
        )
    return row


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
    parent = await _fetch_one(conn, "SELECT kind FROM nodes WHERE id = %s", (parent_id,))
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


async def _record(
    conn: AsyncConnection,
    *,
    node_id: int,
    action: str,
    actor: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    await conn.execute(
        "INSERT INTO events (node_id, action, actor, before, after) VALUES (%s, %s, %s, %s, %s)",
        (node_id, action, actor, None if before is None else Jsonb(before), Jsonb(after)),
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
        actor = _clean_actor(actor)
        _check_choice("kind", kind, KINDS)
        title = _clean_title(title)
        _check_choice("priority", priority, PRIORITIES)
        async with self._database.transaction() as conn:
            await _require_parent(conn, kind, parent_id)
            row = await _fetch_one(
                conn,
                "INSERT INTO nodes (kind, parent_id, title, body, priority) "
                f"VALUES (%s, %s, %s, %s, %s) RETURNING {_NODE_COLUMNS}",
                (kind, parent_id, title, body, priority),
            )
            assert row is not None
            await _record(
                conn,
                node_id=row["id"],
                action="create",
                actor=actor,
                before=None,
                after=_snapshot(
                    row, ("kind", "parent_id", "title", "body", "status", "priority", "version")
                ),
            )
        return Node.model_validate(row)

    async def get_node(self, node_id: int) -> Node:
        async with self._database.transaction() as conn:
            return Node.model_validate(await _require_node(conn, node_id))

    async def list_nodes(
        self,
        *,
        kind: str | None,
        parent_id: int | None,
        status: str | None,
        limit: int,
        after_id: int | None,
    ) -> NodePage:
        limit = _page_size(limit)
        if kind is not None:
            _check_choice("kind", kind, KINDS)
        if status is not None:
            _check_choice("status", status, STATUSES)
        async with self._database.transaction() as conn:
            if parent_id is not None:
                await _require_node(conn, parent_id)
            rows = await _fetch_all(
                conn,
                f"SELECT {_NODE_COLUMNS} FROM nodes "
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
        page = rows[:limit]
        return NodePage(
            items=[Node.model_validate(row) for row in page],
            next_after_id=page[-1]["id"] if len(rows) > limit else None,
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
        actor = _clean_actor(actor)
        if title is not None:
            title = _clean_title(title)
        if status is not None:
            _check_choice("status", status, STATUSES)
        if priority is not None:
            _check_choice("priority", priority, PRIORITIES)
        async with self._database.transaction() as conn:
            current = await _require_node(conn, node_id, lock=True)
            if current["version"] != expected_version:
                raise _stale(node_id, current["version"], expected_version)
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
            row = await self._save(conn, current, changes)
            await _record(
                conn,
                node_id=node_id,
                action="update",
                actor=actor,
                before={**_snapshot(current, tuple(changes)), "version": current["version"]},
                after={**changes, "version": row["version"]},
            )
        return Node.model_validate(row)

    async def move_node(
        self, *, actor: str, node_id: int, expected_version: int, new_parent_id: int
    ) -> Node:
        actor = _clean_actor(actor)
        async with self._database.transaction() as conn:
            current = await _require_node(conn, node_id, lock=True)
            if current["version"] != expected_version:
                raise _stale(node_id, current["version"], expected_version)
            if current["kind"] == "epic":
                raise Refusal(
                    f"Node {node_id} is an epic. An epic has no parent, so it cannot be moved."
                )
            await _require_parent(conn, current["kind"], new_parent_id)
            if new_parent_id == current["parent_id"]:
                raise Refusal(
                    f"Node {node_id} is already under node {new_parent_id}, so nothing moved."
                )
            row = await self._save(conn, current, {"parent_id": new_parent_id})
            await _record(
                conn,
                node_id=node_id,
                action="move",
                actor=actor,
                before={"parent_id": current["parent_id"], "version": current["version"]},
                after={"parent_id": new_parent_id, "version": row["version"]},
            )
        return Node.model_validate(row)

    async def node_history(self, *, node_id: int, limit: int, after_id: int | None) -> EventPage:
        limit = _page_size(limit)
        async with self._database.transaction() as conn:
            await _require_node(conn, node_id)
            rows = await _fetch_all(
                conn,
                "SELECT id, node_id, action, actor, at, before, after FROM events "
                "WHERE node_id = %s AND id > %s ORDER BY id LIMIT %s",
                (node_id, after_id or 0, limit + 1),
            )
        page = rows[:limit]
        return EventPage(
            items=[NodeEvent.model_validate(row) for row in page],
            next_after_id=page[-1]["id"] if len(rows) > limit else None,
        )

    @staticmethod
    async def _save(
        conn: AsyncConnection, current: dict[str, Any], changes: dict[str, Any]
    ) -> dict[str, Any]:
        """Write the changes and raise the version. The row is locked and its version checked."""
        assignments = ", ".join(f"{field} = %({field})s" for field in changes)
        row = await _fetch_one(
            conn,
            f"UPDATE nodes SET {assignments}, version = version + 1 "
            f"WHERE id = %(id)s RETURNING {_NODE_COLUMNS}",
            {**changes, "id": current["id"]},
        )
        assert row is not None
        return row
