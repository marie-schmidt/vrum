"""Links between nodes. This module owns adding, removing and reading them."""

from datetime import datetime
from typing import Any

import psycopg
from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import (
    Refusal,
    check_choice,
    fetch_all,
    fetch_one,
    page_size,
    record_event,
    snapshot,
    split_page,
)
from vrum.db import Database
from vrum.graph import require_node

LINK_TYPES = ("needs_first", "supersedes", "duplicates", "relates_to", "discovered_from")
# These two mean the same thing in either direction, so A to B is the same link as B to A.
_TWO_WAY = ("duplicates", "relates_to")
_LINK_COLUMNS = "id, source, target, link_type, created_at"
_LINK_FIELDS = ("source", "target", "link_type")


class Link(BaseModel):
    id: int
    source: str = Field(
        description="The reference of the node the link starts from, such as story:12. For "
        "needs_first, the node that has to wait. For discovered_from, the new work."
    )
    target: str = Field(
        description="The reference of the node the link points to. For needs_first, the node that "
        "must be done first. For discovered_from, the work it was found while doing."
    )
    link_type: str = Field(
        description="needs_first, supersedes, duplicates, relates_to or discovered_from."
    )
    created_at: datetime


class LinkPage(BaseModel):
    items: list[Link]
    total: int = Field(description="How many links match in all, across pages.")
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )


async def insert_link(
    conn: AsyncConnection, *, actor: str, source: str, target: str, link_type: str
) -> dict[str, Any]:
    """Insert a link that has been checked, and record it in the history of both nodes. The
    database refuses a circle of waiting and a repeat. Returns the stored row."""
    row = await fetch_one(
        conn,
        "INSERT INTO links (source, target, link_type) VALUES (%s, %s, %s) "
        f"RETURNING {_LINK_COLUMNS}",
        (source, target, link_type),
    )
    assert row is not None
    await record_event(
        conn,
        node=source,
        other_node=target,
        action="link_added",
        actor=actor,
        before=None,
        after=snapshot(row, _LINK_FIELDS),
    )
    return row


async def delete_link(conn: AsyncConnection, *, actor: str, link_id: int) -> dict[str, Any]:
    """Delete a link by its id, and record it in the history of both nodes. Returns the row."""
    row = await fetch_one(
        conn, f"DELETE FROM links WHERE id = %s RETURNING {_LINK_COLUMNS}", (link_id,)
    )
    assert row is not None
    await record_event(
        conn,
        node=row["source"],
        other_node=row["target"],
        action="link_removed",
        actor=actor,
        before=snapshot(row, _LINK_FIELDS),
        after={},
    )
    return row


class Links:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add_link(self, *, actor: str, source: str, target: str, link_type: str) -> Link:
        check_choice("link_type", link_type, LINK_TYPES)
        if source == target:
            raise Refusal(
                f"A node cannot be linked to itself (both ends are {source}). Give two "
                "different nodes as source and target."
            )
        try:
            async with self._database.transaction() as conn:
                await require_node(conn, source)
                await require_node(conn, target)
                existing = await fetch_one(
                    conn,
                    "SELECT source, target FROM links WHERE link_type = %(type)s AND "
                    "((source = %(source)s AND target = %(target)s) OR "
                    "(%(two_way)s AND source = %(target)s AND target = %(source)s))",
                    {
                        "type": link_type,
                        "source": source,
                        "target": target,
                        "two_way": link_type in _TWO_WAY,
                    },
                )
                if existing is not None:
                    raise Refusal(
                        f"{existing['source']} is already linked to {existing['target']} with "
                        f"{link_type}, so nothing was added."
                    )
                row = await insert_link(
                    conn, actor=actor, source=source, target=target, link_type=link_type
                )
        except psycopg.errors.RaiseException as error:
            # The database's own plain refusal, such as a circle of waiting.
            raise Refusal(error.diag.message_primary or "The database refused that link.") from None
        except psycopg.errors.UniqueViolation:
            raise Refusal(
                f"{source} is already linked to {target} with {link_type}, so nothing was added."
            ) from None
        return Link.model_validate(row)

    async def remove_link(self, *, actor: str, source: str, target: str, link_type: str) -> Link:
        check_choice("link_type", link_type, LINK_TYPES)
        async with self._database.transaction() as conn:
            row = await fetch_one(
                conn,
                "DELETE FROM links WHERE link_type = %(type)s AND "
                "((source = %(source)s AND target = %(target)s) OR "
                "(%(two_way)s AND source = %(target)s AND target = %(source)s)) "
                f"RETURNING {_LINK_COLUMNS}",
                {
                    "type": link_type,
                    "source": source,
                    "target": target,
                    "two_way": link_type in _TWO_WAY,
                },
            )
            if row is None:
                raise Refusal(
                    f"There is no {link_type} link from {source} to {target}. "
                    "Use list_links to see the links a node has."
                )
            await record_event(
                conn,
                node=row["source"],
                other_node=row["target"],
                action="link_removed",
                actor=actor,
                before=snapshot(row, _LINK_FIELDS),
                after={},
            )
        return Link.model_validate(row)

    async def list_links(
        self, *, node: str, link_type: str | None, limit: int, after_id: int | None
    ) -> LinkPage:
        limit = page_size(limit)
        if link_type is not None:
            check_choice("link_type", link_type, LINK_TYPES)
        async with self._database.transaction() as conn:
            await require_node(conn, node)
            where = (
                "(source = %(node)s OR target = %(node)s) "
                "AND (%(type)s::text IS NULL OR link_type = %(type)s)"
            )
            counted = await fetch_one(
                conn,
                f"SELECT count(*) AS total FROM links WHERE {where}",
                {"node": node, "type": link_type},
            )
            assert counted is not None
            rows = await fetch_all(
                conn,
                f"SELECT {_LINK_COLUMNS} FROM links WHERE {where} "
                "AND id > %(after)s ORDER BY id LIMIT %(fetch)s",
                {"node": node, "type": link_type, "after": after_id or 0, "fetch": limit + 1},
            )
        page, next_after_id = split_page(rows, limit)
        return LinkPage(
            items=[Link.model_validate(row) for row in page],
            total=int(counted["total"]),
            next_after_id=next_after_id,
        )
