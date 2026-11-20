"""Claims with leases. A claim says "I am working on this" for a limited time. This module owns
taking, renewing and releasing them."""

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import (
    Refusal,
    fetch_all,
    fetch_one,
    record_event,
    required_text,
    snapshot,
    when,
)
from vrum.db import Database
from vrum.graph import Node, change_status, node_state, require_node
from vrum.identity import current_identity

DEFAULT_LEASE_SECONDS = 900
MAX_LEASE_SECONDS = 86_400
HANDOFF_OUTCOMES = ("done", "blocked", "dropped", "released")
_CLAIM_COLUMNS = "node, actor, actor_kind, claimed_at, expires_at"
# What an event keeps of a claim: who held it (name and kind) and until when.
_CLAIM_SNAPSHOT = ("actor", "actor_kind", "expires_at")


class Claim(BaseModel):
    node: str = Field(description="The reference of the story or task, such as story:12.")
    actor: str = Field(description="Who holds the claim: the name of the person or agent.")
    actor_kind: str = Field(description="person or agent: what the holder is.")
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
            f"{node.ref} is an epic. Only stories and tasks can be claimed; claim one of the "
            "stories or tasks under it."
        )
    if node.status in ("done", "dropped"):
        raise Refusal(f"{node.ref} is {node.status}, so there is nothing left to claim.")
    if node.waiting_on:
        needs = ", ".join(node.waiting_on)
        raise Refusal(
            f"{node.ref} cannot be started yet: it needs {needs} first, and "
            f"{'that is' if len(node.waiting_on) == 1 else 'those are'} not done. Finish "
            f"{'that' if len(node.waiting_on) == 1 else 'those'} first, or remove the needs_first "
            "link if it no longer applies. next_work with peek shows what can be started now."
        )
    if node.status == "blocked":
        raise Refusal(
            f"{node.ref} is marked blocked. Once what blocks it is resolved, change its "
            "status with update_node, then claim it."
        )


def held_by_caller(claim: dict[str, Any], actor: str) -> bool:
    """Whether the claim is held by whoever is calling: the same name and the same kind, so an agent
    and a person who happen to share a name are not the same holder."""
    return bool(claim["actor"] == actor and claim["actor_kind"] == current_identity().kind)


async def current_claim(conn: AsyncConnection, node: str) -> dict[str, Any] | None:
    """The claim row on a node, live or lapsed, or None. `live` says if it is still in force."""
    return await fetch_one(
        conn,
        f"SELECT {_CLAIM_COLUMNS}, expires_at > clock_timestamp() AS live FROM claims "
        "WHERE node = %s",
        (node,),
    )


async def take_claim(
    conn: AsyncConnection,
    *,
    actor: str,
    node: str,
    lease_seconds: int,
    current: dict[str, Any] | None,
) -> dict[str, Any]:
    """Take the claim on a node the caller has locked and found free (`current` is the row from
    current_claim). The database settles a tie: only an expired claim can be taken over."""
    row = await fetch_one(
        conn,
        "INSERT INTO claims (node, actor, actor_kind, claimed_at, expires_at) "
        "SELECT ref, %(actor)s, %(kind)s, clock_timestamp(), "
        "clock_timestamp() + make_interval(secs => %(lease)s) FROM nodes "
        "WHERE ref = %(node)s "
        "ON CONFLICT (project_id, node) DO UPDATE SET actor = EXCLUDED.actor, "
        "actor_kind = EXCLUDED.actor_kind, "
        "claimed_at = EXCLUDED.claimed_at, expires_at = EXCLUDED.expires_at "
        "WHERE claims.expires_at <= clock_timestamp() "
        f"RETURNING {_CLAIM_COLUMNS}",
        {"actor": actor, "kind": current_identity().kind, "lease": lease_seconds, "node": node},
    )
    if row is None:
        raise Refusal(
            f"{node} was claimed by someone else a moment ago. Pick other work "
            "(next_work with peek shows what is free)."
        )
    await record_event(
        conn,
        node=node,
        action="claimed",
        actor=actor,
        before=None if current is None else snapshot(current, _CLAIM_SNAPSHOT),
        after=snapshot(row, _CLAIM_SNAPSHOT),
    )
    return row


async def release_in(
    conn: AsyncConnection, *, actor: str, node: str, outcome: str, note: str, verb: str
) -> dict[str, Any]:
    """End the holder's claim on a node the caller has locked, and leave the handoff note. Only the
    holder can, whether the claim is still in force or has lapsed with nobody taking it over.
    `verb` is what the caller is doing ("release", "finish"), for the refusals. Returns the claim
    that ended."""
    current = await current_claim(conn, node)
    if current is None or (not current["live"] and not held_by_caller(current, actor)):
        raise Refusal(f"Nobody holds a claim on {node}, so there is nothing to {verb}.")
    if not held_by_caller(current, actor):
        raise Refusal(
            f"{node} is claimed by {current['actor']}, not by you, so you cannot {verb} "
            f"it. Their claim ends on its own at {when(current['expires_at'])}."
        )
    row = await fetch_one(
        conn, f"DELETE FROM claims WHERE node = %s RETURNING {_CLAIM_COLUMNS}", (node,)
    )
    assert row is not None
    await record_event(
        conn,
        node=node,
        action="handoff",
        actor=actor,
        before=None,
        after={"outcome": outcome, "note": note},
    )
    await record_event(
        conn,
        node=node,
        action="claim_released",
        actor=actor,
        before=snapshot(row, _CLAIM_SNAPSHOT),
        after={},
    )
    return row


class HeldWork(BaseModel):
    node: str = Field(description="The reference of the story or task, such as story:12.")
    kind: str = Field(description="story or task.")
    title: str
    expires_at: datetime = Field(description="When the claim lapses, or lapsed.")


class LastHandoff(BaseModel):
    node: str | None = Field(description="The reference of the story or task it was left on.")
    title: str | None
    at: datetime
    outcome: str = Field(description="done, blocked, dropped or released.")
    note: str


class AgentState(BaseModel):
    actor: str = Field(description="The name of the person or agent.")
    kind: str = Field(
        description="person or agent (unknown for history written before names were verified). A "
        "person and an agent with the same name are two entries."
    )
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
        description="Everyone who has taken work or left a handoff note (a person who gave a "
        "reason for blocking or dropping work too), by name and kind: working first, then "
        "lapsed, then finished."
    )


_STATE_ORDER = {"working": 0, "lapsed": 1, "finished": 2}


class Claims:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def who_is_working(self) -> WhoIsWorking:
        """Every identity who has held a claim or left a handoff note, with the state they are in:
        what they hold, what ran out on them, and the last note they left."""
        async with self._database.transaction() as conn:
            held = await fetch_all(
                conn,
                "SELECT c.actor, c.actor_kind, c.node, n.kind, n.title, c.expires_at, "
                "c.expires_at > clock_timestamp() AS live "
                "FROM claims c JOIN nodes n ON n.ref = c.node "
                "ORDER BY c.expires_at, n.kind_rank, n.number",
            )
            notes = await fetch_all(
                conn,
                "SELECT DISTINCT ON (e.actor, e.actor_kind) e.actor, e.actor_kind, e.node, "
                "n.title, e.at, e.after ->> 'outcome' AS outcome, e.after ->> 'note' AS note "
                "FROM events e LEFT JOIN nodes n ON n.ref = e.node "
                "WHERE e.action = 'handoff' "
                "ORDER BY e.actor, e.actor_kind, e.at DESC, e.id DESC",
            )
            now = await fetch_one(conn, "SELECT clock_timestamp() AS now")
        assert now is not None
        agents: dict[tuple[str, str], AgentState] = {}

        def state_of(row: dict[str, Any]) -> AgentState:
            identity = (row["actor"], row["actor_kind"])
            if identity not in agents:
                agents[identity] = AgentState(
                    actor=row["actor"],
                    kind=row["actor_kind"],
                    state="finished",
                    holding=[],
                    lapsed=[],
                    last_handoff=None,
                )
            return agents[identity]

        for row in held:
            work = HeldWork(
                node=row["node"],
                kind=row["kind"],
                title=row["title"],
                expires_at=row["expires_at"],
            )
            agent = state_of(row)
            (agent.holding if row["live"] else agent.lapsed).append(work)
        for row in notes:
            state_of(row).last_handoff = LastHandoff(
                node=row["node"],
                title=row["title"],
                at=row["at"],
                outcome=row["outcome"],
                note=row["note"],
            )
        for agent in agents.values():
            agent.lapsed.reverse()
            agent.state = "working" if agent.holding else "lapsed" if agent.lapsed else "finished"
        ordered = sorted(
            agents.values(), key=lambda a: (_STATE_ORDER[a.state], a.actor.lower(), a.kind)
        )
        return WhoIsWorking(as_of=now["now"], agents=ordered)

    async def claim_node(self, *, actor: str, node: str, lease_seconds: int) -> Claim:
        lease_seconds = lease_seconds_ok(lease_seconds)
        async with self._database.transaction() as conn:
            # Holding the node's row makes claims, edits and moves of it happen one at a time.
            await require_node(conn, node, lock=True)
            refuse_unless_claimable(await node_state(conn, node))
            current = await current_claim(conn, node)
            if current is not None and current["live"]:
                if held_by_caller(current, actor):
                    raise Refusal(
                        f"You already hold the claim on {node} until "
                        f"{when(current['expires_at'])}. Use renew_claim to extend it."
                    )
                raise Refusal(
                    f"{node} is claimed by {current['actor']} until "
                    f"{when(current['expires_at'])}. Pick other work (next_work with peek shows "
                    "what is free), or claim it once that claim ends or is released."
                )
            row = await take_claim(
                conn, actor=actor, node=node, lease_seconds=lease_seconds, current=current
            )
        return Claim.model_validate(row)

    async def renew_claim(self, *, actor: str, node: str, lease_seconds: int) -> Claim:
        lease_seconds = lease_seconds_ok(lease_seconds)
        async with self._database.transaction() as conn:
            await require_node(conn, node, lock=True)
            current = await current_claim(conn, node)
            if current is None or not current["live"]:
                raise Refusal(
                    f"You do not hold a claim on {node}: there is none, or it has lapsed. "
                    "Use claim_node to take it."
                )
            if not held_by_caller(current, actor):
                raise Refusal(
                    f"{node} is claimed by {current['actor']}, not by you, so you cannot renew it."
                )
            row = await fetch_one(
                conn,
                "UPDATE claims SET expires_at = clock_timestamp() + make_interval(secs => %s) "
                f"WHERE node = %s RETURNING {_CLAIM_COLUMNS}",
                (lease_seconds, node),
            )
            assert row is not None
            await record_event(
                conn,
                node=node,
                action="claim_renewed",
                actor=actor,
                before=snapshot(current, _CLAIM_SNAPSHOT),
                after=snapshot(row, _CLAIM_SNAPSHOT),
            )
        return Claim.model_validate(row)

    async def release_claim(self, *, actor: str, node: str, note: str) -> Claim:
        """Give up a claim, leaving a handoff note. Work that was in progress is free to take
        again, so it goes back to not started."""
        note = clean_note(note)
        async with self._database.transaction() as conn:
            current = await require_node(conn, node, lock=True)
            row = await release_in(
                conn, actor=actor, node=node, outcome="released", note=note, verb="release"
            )
            if current["status"] == "in_progress":
                await change_status(conn, actor, current, "new")
        return Claim.model_validate(row)
