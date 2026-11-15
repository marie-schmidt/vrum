"""Pieces shared by the graph, links and claims modules: plain refusals, checks on what a caller
sent, and the small helpers for talking to the database."""

from datetime import UTC, date, datetime
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

MAX_PAGE_SIZE = 200


class Refusal(ToolError):
    """A request that will not be carried out. The message says what was wrong and what to do."""


def clean_actor(actor: str) -> str:
    cleaned = actor.strip()
    if not cleaned:
        raise Refusal(
            "Say who is making this change: actor must be the name of a person or an agent."
        )
    return cleaned


def check_choice(label: str, value: str, allowed: tuple[str, ...], extra: str = "") -> str:
    if value not in allowed:
        raise Refusal(f"{label} must be one of {', '.join(allowed)}, but it is {value!r}{extra}.")
    return value


def page_size(limit: int) -> int:
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise Refusal(f"limit must be a whole number from 1 to {MAX_PAGE_SIZE}, but it is {limit}.")
    return limit


def split_page(rows: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], int | None]:
    """Cut the extra row that was fetched to learn whether another page exists."""
    page = rows[:limit]
    return page, page[-1]["id"] if len(rows) > limit else None


def stale_version(label: str, reader: str, item_id: int, current: int, expected: int) -> Refusal:
    return Refusal(
        f"{label} {item_id} is at version {current}, but you sent version {expected}. "
        f"Someone else changed it after you read it. Read it again with {reader}, check that your "
        f"change still makes sense, then repeat it with expected_version {current}."
    )


def when(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


async def fetch_one(conn: AsyncConnection, sql: str, params: Any = None) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(sql, params)
        return await cursor.fetchone()


async def fetch_all(conn: AsyncConnection, sql: str, params: Any = None) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(sql, params)
        return await cursor.fetchall()


def snapshot(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """The named fields of a row, ready to be stored in an event (dates and times become text)."""
    return {
        field: row[field].isoformat() if isinstance(row[field], date) else row[field]
        for field in fields
    }


async def save_changes(
    conn: AsyncConnection, table: str, row_id: int, changes: dict[str, Any], columns: str
) -> dict[str, Any]:
    """Write the changes to one row and raise its version by one. The caller holds the row's lock
    and has checked the version it was read at. `table` and the keys of `changes` come from the
    code, never from a caller."""
    assignments = ", ".join(f"{field} = %({field})s" for field in changes)
    row = await fetch_one(
        conn,
        f"UPDATE {table} SET {assignments}, version = version + 1 "
        f"WHERE id = %(id)s RETURNING {columns}",
        {**changes, "id": row_id},
    )
    assert row is not None
    return row


async def record_event(
    conn: AsyncConnection,
    *,
    action: str,
    actor: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    node_id: int | None = None,
    other_node_id: int | None = None,
    rule_id: int | None = None,
    decision_id: int | None = None,
) -> None:
    """Append to the event log. It shows in the history of every node, rule and decision it
    names: `other_node_id` is the second node of a link, or the story a rule has just left."""
    await conn.execute(
        "INSERT INTO events (node_id, other_node_id, rule_id, decision_id, action, actor, "
        "before, after) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            node_id,
            other_node_id,
            rule_id,
            decision_id,
            action,
            actor,
            None if before is None else Jsonb(before),
            Jsonb(after),
        ),
    )
