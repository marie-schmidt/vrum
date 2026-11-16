"""Claims with leases. A claim says "I am working on this" for a limited time. This module owns
taking, renewing and releasing them."""

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import (
    Refusal,
    clean_actor,
    fetch_all,
    fetch_one,
    record_event,
    required_text,
    snapshot,
    when,
)
from vrum.db import Database
from vrum.graph import Node, change_status, node_state, require_node

DEFAULT_LEASE_SECONDS = 900
MAX_LEASE_SECONDS = 86_400
HANDOFF_OUTCOMES = ("done", "blocked", "dropped", "released")
_CLAIM_COLUMNS = "node_id, actor, claimed_at, expires_at"


class Claim(BaseModel):
    node_id: int
    actor: str = Field(description="Who holds the claim.")
    claimed_at: datetime
    expires_at: datetime = Field(
        description="When the claim lapses on its own, unless its holder renews it."
    )


def lease_seconds_ok(seconds: int) -> int:
    if not 1 <= seconds <= MAX_LEASE_SECONDS:
        raise Refusal(
            f"lease_seconds must be a whole number from 1 to {MAX_LEASE_SECONDS} (24 hours), but "
            f"it is {seconds}."
        )
    return seconds


def clean_note(note: str) -> str:
    return required_text(
        note,
        "A handoff note is needed: note cannot be empty. Say what was done and what the next "
        "person must know, so that whoever picks this up does not start from nothing.",
    )


def refuse_unless_claimable(node: Node) -> None:
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


async def current_claim(conn: AsyncConnection, node_id: int) -> dict[str, Any] | None:
    """The claim row on a node, live or lapsed, or None. `live` says if it is still in force."""
    return await fetch_one(
        conn,
        f"SELECT {_CLAIM_COLUMNS}, expires_at > clock_timestamp() AS live FROM claims "
        "WHERE node_id = %s",
        (node_id,),
    )


async def take_claim(
    conn: AsyncConnection,
    *,
    actor: str,
    node_id: int,
    lease_seconds: int,
    current: dict[str, Any] | None,
) -> dict[str, Any]:
    """Take the claim on a node the caller has locked and found free (`current` is the row from
    current_claim). The database settles a tie: only an expired claim can be taken over."""
    row = await fetch_one(
        conn,
        "INSERT INTO claims (node_id, node_kind, actor, claimed_at, expires_at) "
        "SELECT id, kind, %(actor)s, clock_timestamp(), "
        "clock_timestamp() + make_interval(secs => %(lease)s) FROM nodes "
        "WHERE id = %(node)s "
        "ON CONFLICT (project_id, node_id) DO UPDATE SET actor = EXCLUDED.actor, "
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
    return row


async def release_in(
    conn: AsyncConnection, *, actor: str, node_id: int, outcome: str, note: str, verb: str
) -> dict[str, Any]:
    """End the holder's claim on a node the caller has locked, and leave the handoff note. Only the
    holder can, whether the claim is still in force or has lapsed with nobody taking it over.
    `verb` is what the caller is doing ("release", "finish"), for the refusals. Returns the claim
    that ended."""
    current = await current_claim(conn, node_id)
    if current is None or (not current["live"] and current["actor"] != actor):
        raise Refusal(f"Nobody holds a claim on node {node_id}, so there is nothing to {verb}.")
    if current["actor"] != actor:
        raise Refusal(
            f"Node {node_id} is claimed by {current['actor']}, not by you, so you cannot {verb} "
            f"it. Their claim ends on its own at {when(current['expires_at'])}."
        )
    row = await fetch_one(
        conn, f"DELETE FROM claims WHERE node_id = %s RETURNING {_CLAIM_COLUMNS}", (node_id,)
    )
    assert row is not None
    await record_event(
        conn,
        node_id=node_id,
        action="handoff",
        actor=actor,
        before=None,
        after={"outcome": outcome, "note": note},
    )
    await record_event(
        conn,
        node_id=node_id,
        action="claim_released",
        actor=actor,
        before=snapshot(row, ("actor", "expires_at")),
        after={},
    )
    return row


class HeldWork(BaseModel):
    node_id: int
    kind: str = Field(description="story or task.")
    title: str
    expires_at: datetime = Field(description="When the claim lapses, or lapsed.")


class LastHandoff(BaseModel):
    node_id: int | None
    title: str | None
    at: datetime
    outcome: str = Field(description="done, blocked, dropped or released.")
    note: str


class AgentState(BaseModel):
    actor: str = Field(description="The person or agent, as they gave their name.")
    state: str = Field(
        description="working (holds a claim that is in force), lapsed (holds one that ran out with "
        "no handoff note) or finished (holds none, and the last thing they did was leave a "
        "handoff note)."
    )
    holding: list[HeldWork] = Field(description="Claims in force, soonest to lapse first.")
    lapsed: list[HeldWork] = Field(description="Claims that ran out, longest ago first.")
    last_handoff: LastHandoff | None = Field(description="The last handoff note they left.")


class WhoIsWorking(BaseModel):
    as_of: datetime = Field(
        description="The server's time for this answer, so that a countdown to a lease does not "
        "depend on the clock of whoever reads it."
    )
    agents: list[AgentState] = Field(
        description="Everyone who has taken work, by name: working first, then lapsed, then "
        "finished."
    )


_STATE_ORDER = {"working": 0, "lapsed": 1, "finished": 2}


class Claims:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def who_is_working(self) -> WhoIsWorking:
        """Every actor who has held a claim or left a handoff note, with the state they are in:
        what they hold, what ran out on them, and the last note they left."""
        async with self._database.transaction() as conn:
            held = await fetch_all(
                conn,
                "SELECT c.actor, c.node_id, n.kind, n.title, c.expires_at, "
                "c.expires_at > clock_timestamp() AS live "
                "FROM claims c JOIN nodes n ON n.id = c.node_id ORDER BY c.expires_at, c.node_id",
            )
            notes = await fetch_all(
                conn,
                "SELECT DISTINCT ON (e.actor) e.actor, e.node_id, n.title, e.at, "
                "e.after ->> 'outcome' AS outcome, e.after ->> 'note' AS note "
                "FROM events e LEFT JOIN nodes n ON n.id = e.node_id "
                "WHERE e.action = 'handoff' ORDER BY e.actor, e.at DESC, e.id DESC",
            )
            now = await fetch_one(conn, "SELECT clock_timestamp() AS now")
        assert now is not None
        agents: dict[str, AgentState] = {}

        def state_of(actor: str) -> AgentState:
            if actor not in agents:
                agents[actor] = AgentState(
                    actor=actor, state="finished", holding=[], lapsed=[], last_handoff=None
                )
            return agents[actor]

        for row in held:
            work = HeldWork(
                node_id=row["node_id"],
                kind=row["kind"],
                title=row["title"],
                expires_at=row["expires_at"],
            )
            agent = state_of(row["actor"])
            (agent.holding if row["live"] else agent.lapsed).append(work)
        for row in notes:
            state_of(row["actor"]).last_handoff = LastHandoff(
                node_id=row["node_id"],
                title=row["title"],
                at=row["at"],
                outcome=row["outcome"],
                note=row["note"],
            )
        for agent in agents.values():
            agent.lapsed.reverse()
            agent.state = "working" if agent.holding else "lapsed" if agent.lapsed else "finished"
        ordered = sorted(agents.values(), key=lambda a: (_STATE_ORDER[a.state], a.actor.lower()))
        return WhoIsWorking(as_of=now["now"], agents=ordered)

    async def claim_node(self, *, actor: str, node_id: int, lease_seconds: int) -> Claim:
        actor = clean_actor(actor)
        lease_seconds = lease_seconds_ok(lease_seconds)
        async with self._database.transaction() as conn:
            # Holding the node's row makes claims, edits and moves of it happen one at a time.
            await require_node(conn, node_id, lock=True)
            refuse_unless_claimable(await node_state(conn, node_id))
            current = await current_claim(conn, node_id)
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
            row = await take_claim(
                conn, actor=actor, node_id=node_id, lease_seconds=lease_seconds, current=current
            )
        return Claim.model_validate(row)

    async def renew_claim(self, *, actor: str, node_id: int, lease_seconds: int) -> Claim:
        actor = clean_actor(actor)
        lease_seconds = lease_seconds_ok(lease_seconds)
        async with self._database.transaction() as conn:
            await require_node(conn, node_id, lock=True)
            current = await current_claim(conn, node_id)
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

    async def release_claim(self, *, actor: str, node_id: int, note: str) -> Claim:
        """Give up a claim, leaving a handoff note. Work that was in progress is free to take
        again, so it goes back to not started."""
        actor = clean_actor(actor)
        note = clean_note(note)
        async with self._database.transaction() as conn:
            current = await require_node(conn, node_id, lock=True)
            row = await release_in(
                conn, actor=actor, node_id=node_id, outcome="released", note=note, verb="release"
            )
            if current["status"] == "in_progress":
                await change_status(conn, actor, current, "new")
        return Claim.model_validate(row)
