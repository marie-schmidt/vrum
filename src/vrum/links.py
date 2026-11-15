"""Links between nodes. This module owns adding, removing and reading them."""

from datetime import datetime

import psycopg
from pydantic import BaseModel, Field

from vrum.common import (
    Refusal,
    check_choice,
    clean_actor,
    fetch_all,
    fetch_one,
    page_size,
    record_event,
    snapshot,
    split_page,
)
from vrum.db import Database
from vrum.graph import require_node

LINK_TYPES = ("needs_first", "supersedes", "duplicates", "relates_to")
# These two mean the same thing in either direction, so A to B is the same link as B to A.
_TWO_WAY = ("duplicates", "relates_to")
_LINK_COLUMNS = "id, source_id, target_id, link_type, created_at"
_LINK_FIELDS = ("source_id", "target_id", "link_type")


class Link(BaseModel):
    id: int
    source_id: int = Field(
        description="The node the link starts from. For needs_first, the node that has to wait."
    )
    target_id: int = Field(
        description="The node the link points to. For needs_first, the node that must be done "
        "first."
    )
    link_type: str = Field(description="needs_first, supersedes, duplicates or relates_to.")
    created_at: datetime


class LinkPage(BaseModel):
    items: list[Link]
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )


class Links:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add_link(self, *, actor: str, source_id: int, target_id: int, link_type: str) -> Link:
        actor = clean_actor(actor)
        check_choice("link_type", link_type, LINK_TYPES)
        if source_id == target_id:
            raise Refusal(
                f"A node cannot be linked to itself (both ends are node {source_id}). Give two "
                "different nodes as source_id and target_id."
            )
        try:
            async with self._database.transaction() as conn:
                await require_node(conn, source_id)
                await require_node(conn, target_id)
                existing = await fetch_one(
                    conn,
                    "SELECT source_id, target_id FROM links WHERE link_type = %(type)s AND "
                    "((source_id = %(source)s AND target_id = %(target)s) OR "
                    "(%(two_way)s AND source_id = %(target)s AND target_id = %(source)s))",
                    {
                        "type": link_type,
                        "source": source_id,
                        "target": target_id,
                        "two_way": link_type in _TWO_WAY,
                    },
                )
                if existing is not None:
                    raise Refusal(
                        f"Node {existing['source_id']} is already linked to node "
                        f"{existing['target_id']} with {link_type}, so nothing was added."
                    )
                row = await fetch_one(
                    conn,
                    "INSERT INTO links (source_id, target_id, link_type) VALUES (%s, %s, %s) "
                    f"RETURNING {_LINK_COLUMNS}",
                    (source_id, target_id, link_type),
                )
                assert row is not None
                await record_event(
                    conn,
                    node_id=source_id,
                    other_node_id=target_id,
                    action="link_added",
                    actor=actor,
                    before=None,
                    after=snapshot(row, _LINK_FIELDS),
                )
        except psycopg.errors.RaiseException as error:
            # The database's own plain refusal, such as a circle of waiting.
            raise Refusal(error.diag.message_primary or "The database refused that link.") from None
        except psycopg.errors.UniqueViolation:
            raise Refusal(
                f"Node {source_id} is already linked to node {target_id} with {link_type}, so "
                "nothing was added."
            ) from None
        return Link.model_validate(row)

    async def remove_link(
        self, *, actor: str, source_id: int, target_id: int, link_type: str
    ) -> Link:
        actor = clean_actor(actor)
        check_choice("link_type", link_type, LINK_TYPES)
        async with self._database.transaction() as conn:
            row = await fetch_one(
                conn,
                "DELETE FROM links WHERE link_type = %(type)s AND "
                "((source_id = %(source)s AND target_id = %(target)s) OR "
                "(%(two_way)s AND source_id = %(target)s AND target_id = %(source)s)) "
                f"RETURNING {_LINK_COLUMNS}",
                {
                    "type": link_type,
                    "source": source_id,
                    "target": target_id,
                    "two_way": link_type in _TWO_WAY,
                },
            )
            if row is None:
                raise Refusal(
                    f"There is no {link_type} link from node {source_id} to node {target_id}. "
                    "Use list_links to see the links a node has."
                )
            await record_event(
                conn,
                node_id=row["source_id"],
                other_node_id=row["target_id"],
                action="link_removed",
                actor=actor,
                before=snapshot(row, _LINK_FIELDS),
                after={},
            )
        return Link.model_validate(row)

    async def list_links(
        self, *, node_id: int, link_type: str | None, limit: int, after_id: int | None
    ) -> LinkPage:
        limit = page_size(limit)
        if link_type is not None:
            check_choice("link_type", link_type, LINK_TYPES)
        async with self._database.transaction() as conn:
            await require_node(conn, node_id)
            rows = await fetch_all(
                conn,
                f"SELECT {_LINK_COLUMNS} FROM links "
                "WHERE (source_id = %(node)s OR target_id = %(node)s) "
                "AND (%(type)s::text IS NULL OR link_type = %(type)s) "
                "AND id > %(after)s ORDER BY id LIMIT %(fetch)s",
                {"node": node_id, "type": link_type, "after": after_id or 0, "fetch": limit + 1},
            )
        page, next_after_id = split_page(rows, limit)
        return LinkPage(
            items=[Link.model_validate(row) for row in page], next_after_id=next_after_id
        )
