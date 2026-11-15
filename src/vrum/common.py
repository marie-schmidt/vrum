"""Pieces shared by the graph, links and claims modules: plain refusals, checks on what a caller
sent, and the small helpers for talking to the database."""

from datetime import UTC, datetime
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
    """The named fields of a row, ready to be stored in an event (times become text)."""
    return {
        field: row[field].isoformat() if isinstance(row[field], datetime) else row[field]
        for field in fields
    }


async def record_event(
    conn: AsyncConnection,
    *,
    node_id: int,
    action: str,
    actor: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    other_node_id: int | None = None,
) -> None:
    """Append to the event log. `other_node_id` is the second node of a link, so the event shows
    in the history of both."""
    await conn.execute(
        "INSERT INTO events (node_id, other_node_id, action, actor, before, after) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (
            node_id,
            other_node_id,
            action,
            actor,
            None if before is None else Jsonb(before),
            Jsonb(after),
        ),
    )
