"""Claims with leases. A claim says "I am working on this" for a limited time. This module owns
taking, renewing and releasing them."""

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import Refusal, clean_actor, fetch_one, record_event, snapshot, when
from vrum.db import Database
from vrum.graph import Node, node_state, require_node

DEFAULT_LEASE_SECONDS = 900
MAX_LEASE_SECONDS = 86_400
_CLAIM_COLUMNS = "node_id, actor, claimed_at, expires_at"


class Claim(BaseModel):
    node_id: int
    actor: str = Field(description="Who holds the claim.")
    claimed_at: datetime
    expires_at: datetime = Field(
        description="When the claim lapses on its own, unless its holder renews it."
    )


def _lease(seconds: int) -> int:
    if not 1 <= seconds <= MAX_LEASE_SECONDS:
        raise Refusal(
            f"lease_seconds must be a whole number from 1 to {MAX_LEASE_SECONDS} (24 hours), but "
            f"it is {seconds}."
        )
    return seconds


def _refuse_unless_claimable(node: Node) -> None:
    """Work that cannot be done now is not handed out."""
    if node.kind == "epic":
        raise Refusal(
            f"Node {node.id} is an epic. Only stories and tasks can be claimed; claim one of the "
            "stories or tasks under it."
        )
    if node.status in ("done", "dropped"):
        raise Refusal(f"Node {node.id} is {node.status}, so there is nothing left to claim.")
    if node.waiting_on:
        ids = ", ".join(str(waiting) for waiting in node.waiting_on)
        raise Refusal(
            f"Node {node.id} cannot be started yet: it needs node {ids} first, and "
            f"{'that is' if len(node.waiting_on) == 1 else 'those are'} not done. Finish "
            f"{'that' if len(node.waiting_on) == 1 else 'those'} first, or remove the needs_first "
            "link if it no longer applies. ready_work lists what can be started now."
        )
    if node.status == "blocked":
        raise Refusal(
            f"Node {node.id} is marked blocked. Once what blocks it is resolved, change its "
            "status with update_node, then claim it."
        )


async def _current_claim(conn: AsyncConnection, node_id: int) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        f"SELECT {_CLAIM_COLUMNS}, expires_at > clock_timestamp() AS live FROM claims "
        "WHERE node_id = %s",
        (node_id,),
    )


class Claims:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def claim_node(self, *, actor: str, node_id: int, lease_seconds: int) -> Claim:
        actor = clean_actor(actor)
        lease_seconds = _lease(lease_seconds)
        async with self._database.transaction() as conn:
            # Holding the node's row makes claims, edits and moves of it happen one at a time.
            await require_node(conn, node_id, lock=True)
            _refuse_unless_claimable(await node_state(conn, node_id))
            current = await _current_claim(conn, node_id)
            if current is not None and current["live"]:
                if current["actor"] == actor:
                    raise Refusal(
                        f"You already hold the claim on node {node_id} until "
                        f"{when(current['expires_at'])}. Use renew_claim to extend it."
                    )
                raise Refusal(
                    f"Node {node_id} is claimed by {current['actor']} until "
                    f"{when(current['expires_at'])}. Pick other work (ready_work lists what is "
                    "free), or claim it once that claim ends or is released."
                )
            # The database settles a tie: only an expired claim can be taken over.
            row = await fetch_one(
                conn,
                "INSERT INTO claims (node_id, node_kind, actor, claimed_at, expires_at) "
                "SELECT id, kind, %(actor)s, clock_timestamp(), "
                "clock_timestamp() + make_interval(secs => %(lease)s) FROM nodes "
                "WHERE id = %(node)s "
                "ON CONFLICT (node_id) DO UPDATE SET actor = EXCLUDED.actor, "
                "claimed_at = EXCLUDED.claimed_at, expires_at = EXCLUDED.expires_at "
                "WHERE claims.expires_at <= clock_timestamp() "
                f"RETURNING {_CLAIM_COLUMNS}",
                {"actor": actor, "lease": lease_seconds, "node": node_id},
            )
            if row is None:
                raise Refusal(
                    f"Node {node_id} was claimed by someone else a moment ago. Pick other work "
                    "(ready_work lists what is free)."
                )
            await record_event(
                conn,
                node_id=node_id,
                action="claimed",
                actor=actor,
                before=None if current is None else snapshot(current, ("actor", "expires_at")),
                after=snapshot(row, ("actor", "expires_at")),
            )
        return Claim.model_validate(row)

    async def renew_claim(self, *, actor: str, node_id: int, lease_seconds: int) -> Claim:
        actor = clean_actor(actor)
        lease_seconds = _lease(lease_seconds)
        async with self._database.transaction() as conn:
            await require_node(conn, node_id, lock=True)
            current = await _current_claim(conn, node_id)
            if current is None or not current["live"]:
                raise Refusal(
                    f"You do not hold a claim on node {node_id}: there is none, or it has lapsed. "
                    "Use claim_node to take it."
                )
            if current["actor"] != actor:
                raise Refusal(
                    f"Node {node_id} is claimed by {current['actor']}, not by you, so you cannot "
                    "renew it."
                )
            row = await fetch_one(
                conn,
                "UPDATE claims SET expires_at = clock_timestamp() + make_interval(secs => %s) "
                f"WHERE node_id = %s RETURNING {_CLAIM_COLUMNS}",
                (lease_seconds, node_id),
            )
            assert row is not None
            await record_event(
                conn,
                node_id=node_id,
                action="claim_renewed",
                actor=actor,
                before=snapshot(current, ("actor", "expires_at")),
                after=snapshot(row, ("actor", "expires_at")),
            )
        return Claim.model_validate(row)

    async def release_claim(self, *, actor: str, node_id: int) -> Claim:
        actor = clean_actor(actor)
        async with self._database.transaction() as conn:
            await require_node(conn, node_id, lock=True)
            current = await _current_claim(conn, node_id)
            if current is None or (not current["live"] and current["actor"] != actor):
                raise Refusal(
                    f"Nobody holds a claim on node {node_id}, so there is nothing to release."
                )
            if current["actor"] != actor:
                raise Refusal(
                    f"Node {node_id} is claimed by {current['actor']}, not by you, so you cannot "
                    f"release it. Their claim ends on its own at {when(current['expires_at'])}."
                )
            row = await fetch_one(
                conn,
                f"DELETE FROM claims WHERE node_id = %s RETURNING {_CLAIM_COLUMNS}",
                (node_id,),
            )
            assert row is not None
            await record_event(
                conn,
                node_id=node_id,
                action="claim_released",
                actor=actor,
                before=snapshot(row, ("actor", "expires_at")),
                after={},
            )
        return Claim.model_validate(row)
