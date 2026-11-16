"""Facts and beliefs. This module owns recording them, what they concern, what a belief rests on,
replacing and retracting them, confirming a belief, and promoting a belief to a decision.

A fact or belief is a record: it is never edited. A change is a newer item that replaces it, or a
retraction. The server never decides by itself that something is contradicted: a caller names what
is replaced."""

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
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
    page_size,
    record_event,
    require_link_targets,
    required_text,
    snapshot,
    split_page,
    unique_ids,
)
from vrum.db import Database
from vrum.decisions import (
    DECISION_KINDS,
    Decision,
    clean_fields,
    insert_decision,
    require_decision,
)
from vrum.decisions import (
    add_links as add_decision_links,
)
from vrum.graph import EVENT_COLUMNS, Event, EventPage

MEMORY_TYPES = ("fact", "belief")
FACT_KINDS = ("world", "experience")
MEMORY_STATUSES = ("active", "superseded", "retracted")
MEMORY_ORIGINS = ("stated", "extracted", "derived")

MEMORY_COLUMNS = (
    "id, type, kind, text, source, happened_on, origin, recorded_by, recorded_at, status, "
    "superseded_by_id, status_reason, confirmed_by, confirmed_at, supersedes_ids, node_ids, "
    "rule_ids, decision_ids, evidence, evidence_count, stale, promoted_decision_ids"
)
_RECORDED_FIELDS = ("type", "kind", "text", "source", "happened_on", "origin")

# Where a fact's or belief's links to each kind of thing are kept. Fixed here, never from a caller.
_LINK_TABLES = {
    "node": ("memory_nodes", "node_id"),
    "rule": ("memory_rules", "rule_id"),
    "decision": ("memory_decisions", "decision_id"),
}


class Evidence(BaseModel):
    type: str = Field(description="fact or decision.")
    id: int = Field(description="Its id, in the fact or decision id space that `type` names.")
    status: str = Field(
        description="active, or superseded or retracted. A belief is stale once any of its "
        "evidence is not active."
    )
    replaced_by_id: int | None = Field(description="What replaced it, if it was superseded.")
    reason: str | None = Field(description="Why it was retracted, if it was.")


class Memory(BaseModel):
    id: int = Field(description="The id of this fact or belief. Facts and beliefs share one set.")
    type: str = Field(
        description="fact: something known, with where it came from. belief: a conclusion drawn "
        "from evidence. A belief is only ever a suggestion: it is not a decision."
    )
    label: str = Field(description="What this is, in plain words, so it is never mistaken.")
    kind: str | None = Field(
        description="For a fact: world (true about the project, its tools or its domain) or "
        "experience (something done or observed). Empty for a belief."
    )
    text: str
    source: str | None = Field(description="For a fact: where it came from. Empty for a belief.")
    happened_on: date | None = Field(
        description="For a fact: the day it was true or happened, when known."
    )
    origin: str = Field(
        description="stated: a person or agent wrote it. extracted: a language model proposed it "
        "from a quoted passage. derived: a language model drew it up from evidence."
    )
    recorded_by: str = Field(description="The person or agent that recorded it.")
    recorded_at: datetime
    status: str = Field(
        description="active, superseded (replaced by a newer item) or retracted (found to be "
        "wrong). It is never edited."
    )
    superseded_by_id: int | None = Field(description="What replaced it. Empty while it is active.")
    supersedes_ids: list[int] = Field(description="What this item replaced, if anything.")
    status_reason: str | None = Field(
        description="Why it was superseded or retracted. Empty while it is active."
    )
    confirmed_by: str | None = Field(
        description="For a belief: the person who has looked at it. Empty if nobody has."
    )
    confirmed_at: datetime | None
    node_ids: list[int] = Field(description="The epics, stories and tasks it concerns.")
    rule_ids: list[int] = Field(description="The rules it concerns.")
    decision_ids: list[int] = Field(description="For a fact: the decisions it concerns.")
    evidence: list[Evidence] = Field(
        description="For a belief: the facts and decisions it rests on, with their status now."
    )
    evidence_count: int = Field(description="How many pieces of evidence a belief rests on.")
    stale: bool = Field(
        description="True for an active belief while any of its evidence is no longer active. "
        "Worked out, never set by hand."
    )
    stale_because: list[str] = Field(description="In words, which evidence changed and why.")
    promoted_decision_ids: list[int] = Field(
        description="For a belief: the decisions a person made from it."
    )


class MemoryPage(BaseModel):
    items: list[Memory]
    total: int = Field(
        description="How many facts and beliefs match in all, across pages, so a walk through "
        "every page can be checked against it."
    )
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )


class MemoryChange(BaseModel):
    replacement: Memory | None = Field(
        description="The newer item that replaced the others. Empty when items were retracted."
    )
    superseded_ids: list[int] = Field(description="The items now marked superseded.")
    retracted_ids: list[int] = Field(description="The items now marked retracted.")
    dependent_beliefs_retracted_ids: list[int] = Field(
        description="Beliefs retracted along with them, because you asked for that."
    )
    stale_belief_ids: list[int] = Field(
        description="Active beliefs that rest on what changed and are now stale. They are not "
        "changed. Read them with list_memories (rests_on_memory_id and stale), then revise or "
        "retract each one."
    )
    promoted_decision_ids: list[int] = Field(
        description="Decisions a person made from a belief that was replaced or retracted. They "
        "are not changed: change one with supersede_decision."
    )


def memory_label(row: dict[str, Any]) -> str:
    if row["type"] == "belief":
        by = "drawn up by a language model" if row["origin"] == "derived" else "stated"
        return (
            f"Suggestion ({by}): a conclusion drawn from evidence. It is not a decision and "
            "changes nothing in the graph."
        )
    how = (
        "proposed by a language model from a quoted passage"
        if row["origin"] == "extracted"
        else "stated"
    )
    return f"Fact ({how}), from: {row['source']}"


def _stale_because(row: dict[str, Any]) -> list[str]:
    if not row["stale"]:
        return []
    reasons = []
    for item in row["evidence"]:
        if item["status"] == "active":
            continue
        what = f"{item['type']} {item['id']}"
        if item["status"] == "retracted":
            reasons.append(f"{what} was retracted: {item['reason']}")
        else:
            reasons.append(f"{what} was superseded by {item['type']} {item['replaced_by_id']}")
    return reasons


def to_memory(row: dict[str, Any]) -> Memory:
    return Memory.model_validate(
        {**row, "label": memory_label(row), "stale_because": _stale_because(row)}
    )


async def require_memory(
    conn: AsyncConnection, memory_id: int, *, lock: bool = False
) -> dict[str, Any]:
    """The fact or belief as read, or a plain refusal. `lock` holds its row until the transaction
    ends, so that changes to it happen one at a time."""
    if lock:
        await conn.execute("SELECT 1 FROM memories WHERE id = %s FOR UPDATE", (memory_id,))
    row = await fetch_one(
        conn, f"SELECT {MEMORY_COLUMNS} FROM memory_state WHERE id = %s", (memory_id,)
    )
    if row is None:
        raise Refusal(
            f"There is no fact or belief with id {memory_id}. Use list_memories to find the id "
            "you want."
        )
    return row


async def _lock(
    conn: AsyncConnection, *, exclusive: Iterable[int] = (), shared: Iterable[int] = ()
) -> None:
    """Hold these rows until the transaction ends: `exclusive` ones to change them, `shared` ones
    (the evidence of a new belief) so that they cannot be retracted underneath it. Always taken in
    id order, so two calls that overlap wait for each other and never wait on each other."""
    modes = {item: "SHARE" for item in shared} | {item: "UPDATE" for item in exclusive}
    for memory_id in sorted(modes):
        await conn.execute(
            f"SELECT 1 FROM memories WHERE id = %s FOR {modes[memory_id]}", (memory_id,)
        )


def _ended(row: dict[str, Any]) -> str:
    """A sentence saying why an item is no longer active, such as 'Fact 4 was retracted (why)'."""
    what = f"{row['type'].capitalize()} {row['id']}"
    if row["status"] == "retracted":
        return f"{what} was retracted ({row['status_reason']})"
    return (
        f"{what} was already superseded by {row['type']} {row['superseded_by_id']} "
        f"({row['status_reason']})"
    )


async def _require_found(conn: AsyncConnection, memory_ids: list[int]) -> list[dict[str, Any]]:
    rows = await fetch_all(
        conn, f"SELECT {MEMORY_COLUMNS} FROM memory_state WHERE id = ANY(%s)", (memory_ids,)
    )
    found = {row["id"] for row in rows}
    missing = [str(item) for item in memory_ids if item not in found]
    if missing:
        raise Refusal(
            f"There is no fact or belief with id {', '.join(missing)}. Use list_memories to find "
            "the id you want. Nothing was changed."
        )
    return rows


async def _require_active(
    conn: AsyncConnection, memory_ids: list[int], verb: str
) -> list[dict[str, Any]]:
    """The items as read now that they are locked, refused in plain words unless each is active."""
    rows = {row["id"]: row for row in await _require_found(conn, memory_ids)}
    for memory_id in memory_ids:
        row = rows[memory_id]
        if row["status"] != "active":
            raise Refusal(f"{_ended(row)}, so it cannot be {verb}. Nothing was changed.")
    return [rows[memory_id] for memory_id in memory_ids]


def _said(said_by: str | None, quote: str | None) -> dict[str, str]:
    """Who changed their mind and what they said, for the history. Both or neither."""
    person, words = (said_by or "").strip(), (quote or "").strip()
    if bool(person) != bool(words):
        raise Refusal(
            "Give both said_by (the person who changed their mind) and quote (what they said, "
            "or where), or neither."
        )
    return {"said_by": person, "quote": words} if person else {}


def _require_a_person_for_confirmed(rows: list[dict[str, Any]], said: dict[str, str]) -> None:
    confirmed = [row for row in rows if row["confirmed_by"]]
    if confirmed and not said:
        names = ", ".join(
            f"belief {row['id']} (confirmed by {row['confirmed_by']})" for row in confirmed
        )
        raise Refusal(
            f"{names} was confirmed by a person, so changing it needs that person's word: give "
            "said_by (who changed their mind) and quote (what they said, or where). Nothing was "
            "changed."
        )


async def _require_evidence(
    conn: AsyncConnection, fact_ids: list[int], decision_ids: list[int]
) -> None:
    if not fact_ids and not decision_ids:
        raise Refusal(
            "A belief needs evidence: give at least one active fact in evidence_fact_ids or one "
            "active decision in evidence_decision_ids that it rests on."
        )
    facts = {
        row["id"]: row
        for row in await fetch_all(
            conn,
            "SELECT id, type, status, superseded_by_id, status_reason FROM memories "
            "WHERE id = ANY(%s)",
            (fact_ids,),
        )
    }
    for fact_id in fact_ids:
        row = facts.get(fact_id)
        if row is None:
            raise Refusal(
                f"There is no fact with id {fact_id}, so a belief cannot rest on it. Use "
                "list_memories with type fact to find the id you want."
            )
        if row["type"] == "belief":
            raise Refusal(
                f"Memory {fact_id} is a belief, and a belief cannot be evidence for another "
                "belief, so that a conclusion is always one step from what it rests on. Give the "
                "facts or decisions it rests on instead."
            )
        if row["status"] != "active":
            raise Refusal(f"{_ended(row)}, so it is not evidence.")
    for decision_id in decision_ids:
        decision = await require_decision(conn, decision_id)
        if decision["status"] != "active":
            raise Refusal(
                f"Decision {decision_id} was superseded by decision "
                f"{decision['superseded_by_id']}, so it is not evidence. Use the decision that "
                "replaced it."
            )


async def _require_decisions(conn: AsyncConnection, decision_ids: list[int]) -> None:
    for decision_id in decision_ids:
        await require_decision(conn, decision_id)


async def _insert_memory(
    conn: AsyncConnection,
    *,
    actor: str,
    memory_type: str,
    kind: str | None,
    text: str,
    source: str | None,
    happened_on: date | None,
    supersedes: list[int],
    evidence: tuple[list[int], list[int]] = ([], []),
    origin: str = "stated",
) -> int:
    row = await fetch_one(
        conn,
        "INSERT INTO memories (type, kind, text, source, happened_on, origin, recorded_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id, type, kind, text, source, happened_on, origin",
        (memory_type, kind, text, source, happened_on, origin, actor),
    )
    assert row is not None
    after = snapshot(row, _RECORDED_FIELDS)
    if supersedes:
        after["supersedes_ids"] = supersedes
    fact_ids, decision_ids = evidence
    if memory_type == "belief":
        after["evidence"] = {"fact_ids": fact_ids, "decision_ids": decision_ids}
        for fact_id in fact_ids:
            await conn.execute(
                "INSERT INTO belief_evidence (belief_id, fact_id) VALUES (%s, %s)",
                (row["id"], fact_id),
            )
        for decision_id in decision_ids:
            await conn.execute(
                "INSERT INTO belief_evidence (belief_id, decision_id) VALUES (%s, %s)",
                (row["id"], decision_id),
            )
    await record_event(
        conn,
        memory_id=row["id"],
        action="memory_recorded",
        actor=actor,
        before=None,
        after=after,
    )
    return int(row["id"])


async def _add_links(
    conn: AsyncConnection,
    memory_id: int,
    actor: str,
    node_ids: list[int],
    rule_ids: list[int],
    decision_ids: list[int],
) -> None:
    for what, ids in (("node", node_ids), ("rule", rule_ids), ("decision", decision_ids)):
        table, column = _LINK_TABLES[what]
        for item in ids:
            await conn.execute(
                f"INSERT INTO {table} (memory_id, {column}) VALUES (%s, %s)", (memory_id, item)
            )
            await record_event(
                conn,
                memory_id=memory_id,
                action="memory_linked",
                actor=actor,
                before=None,
                after={"memory_id": memory_id},
                **{column: item},
            )


async def _remove_links(
    conn: AsyncConnection,
    memory_id: int,
    actor: str,
    node_ids: list[int],
    rule_ids: list[int],
    decision_ids: list[int],
) -> None:
    for what, ids in (("node", node_ids), ("rule", rule_ids), ("decision", decision_ids)):
        table, column = _LINK_TABLES[what]
        for item in ids:
            await conn.execute(
                f"DELETE FROM {table} WHERE memory_id = %s AND {column} = %s", (memory_id, item)
            )
            await record_event(
                conn,
                memory_id=memory_id,
                action="memory_unlinked",
                actor=actor,
                before={"memory_id": memory_id},
                after={},
                **{column: item},
            )


async def _dependents(conn: AsyncConnection, fact_ids: list[int]) -> list[int]:
    """The active beliefs that rest on these facts and are not among them."""
    rows = await fetch_all(
        conn,
        "SELECT DISTINCT be.belief_id AS id FROM belief_evidence be "
        "JOIN memories b ON b.id = be.belief_id "
        "WHERE be.fact_id = ANY(%(facts)s) AND b.status = 'active' "
        "AND NOT (b.id = ANY(%(facts)s)) ORDER BY 1",
        {"facts": fact_ids},
    )
    return [row["id"] for row in rows]


async def _stale_belief_ids(conn: AsyncConnection, fact_ids: list[int]) -> list[int]:
    rows = await fetch_all(
        conn,
        "SELECT id FROM memory_state WHERE type = 'belief' AND status = 'active' AND stale "
        "AND id IN (SELECT belief_id FROM belief_evidence WHERE fact_id = ANY(%s)) ORDER BY id",
        (fact_ids,),
    )
    return [row["id"] for row in rows]


async def _promoted_decision_ids(conn: AsyncConnection, belief_ids: list[int]) -> list[int]:
    rows = await fetch_all(
        conn,
        "SELECT id FROM decisions WHERE promoted_from_id = ANY(%s) ORDER BY id",
        (belief_ids,),
    )
    return [row["id"] for row in rows]


class Memories:
    def __init__(self, database: Database) -> None:
        self._database = database

    @asynccontextmanager
    async def _within(self, conn: AsyncConnection | None) -> AsyncIterator[AsyncConnection]:
        """The caller's transaction, when it has one (so that what it writes and what it records
        together are saved together or not at all), and otherwise a new one."""
        if conn is not None:
            yield conn
        else:
            async with self._database.transaction() as own:
                yield own

    async def retain_fact(
        self,
        *,
        actor: str,
        text: str,
        source: str,
        kind: str,
        happened_on: date | None,
        node_ids: list[int] | None,
        rule_ids: list[int] | None,
        decision_ids: list[int] | None,
        origin: str = "stated",
        conn: AsyncConnection | None = None,
    ) -> Memory:
        actor = clean_actor(actor)
        text = required_text(
            text, "A fact needs its text: text cannot be empty. Say what is known."
        )
        source = required_text(
            source,
            "A fact needs its source: source cannot be empty. Say where it came from, in plain "
            "words (who said it, which document, what was observed).",
        )
        check_choice("kind", kind, FACT_KINDS)
        nodes, rules, decisions = (
            unique_ids(node_ids),
            unique_ids(rule_ids),
            unique_ids(decision_ids),
        )
        async with self._within(conn) as conn:
            await require_link_targets(conn, nodes, rules, "the fact")
            await _require_decisions(conn, decisions)
            memory_id = await _insert_memory(
                conn,
                actor=actor,
                memory_type="fact",
                kind=kind,
                text=text,
                source=source,
                happened_on=happened_on,
                supersedes=[],
                origin=origin,
            )
            await _add_links(conn, memory_id, actor, nodes, rules, decisions)
            return to_memory(await require_memory(conn, memory_id))

    async def record_belief(
        self,
        *,
        actor: str,
        text: str,
        evidence_fact_ids: list[int] | None,
        evidence_decision_ids: list[int] | None,
        node_ids: list[int] | None,
        rule_ids: list[int] | None,
        origin: str = "stated",
        conn: AsyncConnection | None = None,
    ) -> Memory:
        actor = clean_actor(actor)
        text = required_text(
            text, "A belief needs its text: text cannot be empty. Say what is concluded."
        )
        facts, decided = unique_ids(evidence_fact_ids), unique_ids(evidence_decision_ids)
        nodes, rules = unique_ids(node_ids), unique_ids(rule_ids)
        async with self._within(conn) as conn:
            await _lock(conn, shared=facts)
            await _require_evidence(conn, facts, decided)
            await require_link_targets(conn, nodes, rules, "the belief")
            memory_id = await _insert_memory(
                conn,
                actor=actor,
                memory_type="belief",
                kind=None,
                text=text,
                source=None,
                happened_on=None,
                supersedes=[],
                evidence=(facts, decided),
                origin=origin,
            )
            await _add_links(conn, memory_id, actor, nodes, rules, [])
            return to_memory(await require_memory(conn, memory_id))

    async def supersede_memory(
        self,
        *,
        actor: str,
        replaces: list[int],
        reason: str,
        text: str,
        source: str | None,
        kind: str | None,
        happened_on: date | None,
        evidence_fact_ids: list[int] | None,
        evidence_decision_ids: list[int] | None,
        keep_links: bool,
        node_ids: list[int] | None,
        rule_ids: list[int] | None,
        decision_ids: list[int] | None,
        said_by: str | None,
        quote: str | None,
        origin: str = "stated",
        conn: AsyncConnection | None = None,
    ) -> MemoryChange:
        """Record a newer fact or belief that replaces one or more active ones of the same type,
        and mark each of them superseded, in one step."""
        actor = clean_actor(actor)
        old_ids = unique_ids(replaces)
        if not old_ids:
            raise Refusal("Say what is being replaced: replaces needs at least one id.")
        reason = required_text(
            reason, "A replacement needs a reason: reason cannot be empty. Say why it changed."
        )
        text = required_text(text, "The new item needs its text: text cannot be empty.")
        said = _said(said_by, quote)
        if kind is not None:
            check_choice("kind", kind, FACT_KINDS)
        facts, decided = unique_ids(evidence_fact_ids), unique_ids(evidence_decision_ids)
        extra_nodes, extra_rules = unique_ids(node_ids), unique_ids(rule_ids)
        extra_decisions = unique_ids(decision_ids)
        async with self._within(conn) as conn:
            types = {row["type"] for row in await _require_found(conn, old_ids)}
            if len(types) > 1:
                raise Refusal(
                    "These are not all the same type: a fact is replaced by a fact and a belief by "
                    "a belief. Replace the facts in one call and the beliefs in another. Nothing "
                    "was changed."
                )
            replaced_type = types.pop()
            await _lock(
                conn,
                exclusive=old_ids,
                shared=facts if replaced_type == "belief" else [],
            )
            olds = await _require_active(conn, old_ids, "replaced")
            _require_a_person_for_confirmed(olds, said)
            new_kind: str | None = None
            if replaced_type == "fact":
                if facts or decided:
                    raise Refusal(
                        "Evidence is only for beliefs. A fact says where it came from in source."
                    )
                source = required_text(
                    source or "",
                    "The new fact needs its source: source cannot be empty. Say where it came "
                    "from.",
                )
                kinds = {row["kind"] for row in olds}
                new_kind = kind or (kinds.pop() if len(kinds) == 1 else None)
                if new_kind is None:
                    raise Refusal(
                        "The facts being replaced are of different kinds (world and experience): "
                        "say which kind the new fact is, with kind."
                    )
                await _require_decisions(conn, extra_decisions)
            else:
                if source is not None or kind is not None or happened_on is not None:
                    raise Refusal(
                        "source, kind and happened_on are only for facts. A belief gives its "
                        "evidence instead."
                    )
                if extra_decisions:
                    raise Refusal(
                        "A belief concerns nodes and rules only. A decision it rests on goes in "
                        "evidence_decision_ids."
                    )
                await _require_evidence(conn, facts, decided)
            await require_link_targets(conn, extra_nodes, extra_rules, "the new item")
            carried_nodes = [item for row in olds for item in row["node_ids"]] if keep_links else []
            carried_rules = [item for row in olds for item in row["rule_ids"]] if keep_links else []
            carried_decisions = (
                [item for row in olds for item in row["decision_ids"]] if keep_links else []
            )
            new_id = await _insert_memory(
                conn,
                actor=actor,
                memory_type=replaced_type,
                kind=new_kind,
                text=text,
                source=source,
                happened_on=happened_on,
                supersedes=old_ids,
                evidence=(facts, decided),
                origin=origin,
            )
            await _add_links(
                conn,
                new_id,
                actor,
                unique_ids(carried_nodes + extra_nodes),
                unique_ids(carried_rules + extra_rules),
                unique_ids(carried_decisions + extra_decisions),
            )
            await conn.execute(
                "UPDATE memories SET status = 'superseded', superseded_by_id = %s, "
                "status_reason = %s WHERE id = ANY(%s)",
                (new_id, reason, old_ids),
            )
            for old_id in old_ids:
                await record_event(
                    conn,
                    memory_id=old_id,
                    action="memory_superseded",
                    actor=actor,
                    before={"status": "active", "superseded_by_id": None},
                    after={
                        "status": "superseded",
                        "superseded_by_id": new_id,
                        "reason": reason,
                        **said,
                    },
                )
            return MemoryChange(
                replacement=to_memory(await require_memory(conn, new_id)),
                superseded_ids=old_ids,
                retracted_ids=[],
                dependent_beliefs_retracted_ids=[],
                stale_belief_ids=await _stale_belief_ids(conn, old_ids),
                promoted_decision_ids=await _promoted_decision_ids(conn, old_ids),
            )

    async def retract_memory(
        self,
        *,
        actor: str,
        memory_ids: list[int],
        reason: str,
        with_dependent_beliefs: bool,
        said_by: str | None,
        quote: str | None,
    ) -> MemoryChange:
        """Mark one or more active facts or beliefs as wrong, in one step. Nothing is deleted."""
        actor = clean_actor(actor)
        ids = unique_ids(memory_ids)
        if not ids:
            raise Refusal("Say what is being retracted: memory_ids needs at least one id.")
        reason = required_text(
            reason, "A retraction needs a reason: reason cannot be empty. Say why it is wrong."
        )
        said = _said(said_by, quote)
        async with self._database.transaction() as conn:
            await _require_found(conn, ids)
            dependents = await _dependents(conn, ids) if with_dependent_beliefs else []
            await _lock(conn, exclusive=ids + dependents)
            rows = await _require_active(conn, ids, "retracted")
            if with_dependent_beliefs:
                # A belief recorded while this call was waiting for its locks is found now.
                while True:
                    current = await _dependents(conn, ids)
                    fresh = [item for item in current if item not in dependents]
                    if not fresh:
                        break
                    await _lock(conn, exclusive=fresh)
                    dependents = sorted({*dependents, *fresh})
                dependents = await _dependents(conn, ids)
            dependent_rows = (
                await _require_active(conn, dependents, "retracted") if dependents else []
            )
            _require_a_person_for_confirmed(rows + dependent_rows, said)
            await conn.execute(
                "UPDATE memories SET status = 'retracted', status_reason = %s WHERE id = ANY(%s)",
                (reason, ids + dependents),
            )
            for memory_id in ids:
                await record_event(
                    conn,
                    memory_id=memory_id,
                    action="memory_retracted",
                    actor=actor,
                    before={"status": "active"},
                    after={"status": "retracted", "reason": reason, **said},
                )
            for memory_id in dependents:
                await record_event(
                    conn,
                    memory_id=memory_id,
                    action="memory_retracted",
                    actor=actor,
                    before={"status": "active"},
                    after={
                        "status": "retracted",
                        "reason": reason,
                        "because_of_ids": ids,
                        **said,
                    },
                )
            return MemoryChange(
                replacement=None,
                superseded_ids=[],
                retracted_ids=ids,
                dependent_beliefs_retracted_ids=dependents,
                stale_belief_ids=await _stale_belief_ids(conn, ids),
                promoted_decision_ids=await _promoted_decision_ids(conn, ids + dependents),
            )

    async def confirm_belief(self, *, actor: str, memory_id: int, confirmed_by: str) -> Memory:
        actor = clean_actor(actor)
        person = required_text(
            confirmed_by,
            "A confirmation needs the person who looked at the belief: confirmed_by must be the "
            "name of a person. (You are recorded separately, as actor.)",
        )
        async with self._database.transaction() as conn:
            row = await require_memory(conn, memory_id, lock=True)
            _require_active_belief(row, "confirmed")
            if row["confirmed_by"]:
                raise Refusal(
                    f"Belief {memory_id} was already confirmed by {row['confirmed_by']}. "
                    "A confirmation is not changed. If the belief is wrong, retract it."
                )
            confirmed = await fetch_one(
                conn,
                "UPDATE memories SET confirmed_by = %s, confirmed_at = now() WHERE id = %s "
                "RETURNING confirmed_by, confirmed_at",
                (person, memory_id),
            )
            assert confirmed is not None
            await record_event(
                conn,
                memory_id=memory_id,
                action="memory_confirmed",
                actor=actor,
                before={"confirmed_by": None},
                after={
                    "confirmed_by": confirmed["confirmed_by"],
                    "confirmed_at": confirmed["confirmed_at"].isoformat(),
                },
            )
            return to_memory(await require_memory(conn, memory_id))

    async def promote_belief(
        self,
        *,
        actor: str,
        memory_id: int,
        decided_by: str,
        title: str,
        reason: str,
        text: str | None,
        kind: str,
        decided_on: date | None,
    ) -> Decision:
        """Record a decision, made by a named person, from an active belief, and keep the link
        back. The belief itself is not changed."""
        actor = clean_actor(actor)
        check_choice("kind", kind, DECISION_KINDS)
        async with self._database.transaction() as conn:
            row = await require_memory(conn, memory_id, lock=True)
            _require_active_belief(row, "promoted to a decision")
            existing = [
                decision_id
                for decision_id in row["promoted_decision_ids"]
                if (await require_decision(conn, decision_id))["status"] == "active"
            ]
            if existing:
                raise Refusal(
                    f"Belief {memory_id} was already promoted to decision {existing[0]}. If that "
                    "decision is changing, use supersede_decision on it."
                )
            fields = clean_fields(title, text or row["text"], reason, decided_by)
            decision_id = await insert_decision(
                conn,
                actor=actor,
                kind=kind,
                fields=fields,
                decided_on=decided_on,
                promoted_from=memory_id,
            )
            await add_decision_links(conn, decision_id, actor, row["node_ids"], row["rule_ids"])
            await record_event(
                conn,
                memory_id=memory_id,
                decision_id=decision_id,
                action="memory_promoted",
                actor=actor,
                before=None,
                after={"decision_id": decision_id, "decided_by": fields[3]},
            )
            return Decision.model_validate(await require_decision(conn, decision_id))

    async def link_memory(
        self,
        *,
        actor: str,
        memory_id: int,
        node_ids: list[int] | None,
        rule_ids: list[int] | None,
        decision_ids: list[int] | None,
    ) -> Memory:
        return await self._change_links(
            actor=actor,
            memory_id=memory_id,
            node_ids=node_ids,
            rule_ids=rule_ids,
            decision_ids=decision_ids,
            add=True,
        )

    async def unlink_memory(
        self,
        *,
        actor: str,
        memory_id: int,
        node_ids: list[int] | None,
        rule_ids: list[int] | None,
        decision_ids: list[int] | None,
    ) -> Memory:
        return await self._change_links(
            actor=actor,
            memory_id=memory_id,
            node_ids=node_ids,
            rule_ids=rule_ids,
            decision_ids=decision_ids,
            add=False,
        )

    async def _change_links(
        self,
        *,
        actor: str,
        memory_id: int,
        node_ids: list[int] | None,
        rule_ids: list[int] | None,
        decision_ids: list[int] | None,
        add: bool,
    ) -> Memory:
        actor = clean_actor(actor)
        nodes, rules, decisions = (
            unique_ids(node_ids),
            unique_ids(rule_ids),
            unique_ids(decision_ids),
        )
        if not nodes and not rules and not decisions:
            raise Refusal(
                "Nothing to link: give at least one id in node_ids, rule_ids or decision_ids."
            )
        async with self._database.transaction() as conn:
            row = await require_memory(conn, memory_id, lock=True)
            if row["status"] != "active":
                raise Refusal(
                    f"{_ended(row)}, and it keeps the links it had. Change the links "
                    "of the item that replaced it instead."
                )
            if decisions and row["type"] == "belief":
                raise Refusal(
                    "A belief concerns nodes and rules only. A decision it rests on is evidence, "
                    "given when the belief is recorded."
                )
            if add:
                await require_link_targets(conn, nodes, rules, f"{row['type']} {memory_id}")
                await _require_decisions(conn, decisions)
            for what, ids, current in (
                ("node", nodes, row["node_ids"]),
                ("rule", rules, row["rule_ids"]),
                ("decision", decisions, row["decision_ids"]),
            ):
                wrong = [str(item) for item in ids if (item in current) == add]
                if wrong:
                    raise Refusal(
                        f"{row['type'].capitalize()} {memory_id} is "
                        f"{'already' if add else 'not'} linked to {what} {', '.join(wrong)}, so "
                        "nothing was changed."
                    )
            if add:
                await _add_links(conn, memory_id, actor, nodes, rules, decisions)
            else:
                await _remove_links(conn, memory_id, actor, nodes, rules, decisions)
            return to_memory(await require_memory(conn, memory_id))

    async def get_memory(self, memory_id: int) -> Memory:
        async with self._database.transaction() as conn:
            return to_memory(await require_memory(conn, memory_id))

    async def list_memories(
        self,
        *,
        memory_type: str | None,
        kind: str | None,
        status: str | None,
        origin: str | None,
        stale: bool | None,
        node_id: int | None,
        rule_id: int | None,
        within_node_id: int | None,
        rests_on_memory_id: int | None,
        rests_on_decision_id: int | None,
        limit: int,
        after_id: int | None,
    ) -> MemoryPage:
        limit = page_size(limit)
        for label, value, allowed in (
            ("type", memory_type, MEMORY_TYPES),
            ("kind", kind, FACT_KINDS),
            ("status", status, MEMORY_STATUSES),
            ("origin", origin, MEMORY_ORIGINS),
        ):
            if value is not None:
                check_choice(label, value, allowed)
        conditions: list[str] = []
        params: dict[str, Any] = {}
        for column, wanted in (
            ("type", memory_type),
            ("kind", kind),
            ("status", status),
            ("origin", origin),
            ("stale", stale),
        ):
            if wanted is not None:
                conditions.append(f"m.{column} = %({column})s")
                params[column] = wanted
        prefix = ""
        async with self._database.transaction() as conn:
            for what, table, item_id, finder in (
                ("node", "nodes", node_id, "list_nodes"),
                ("rule", "rules", rule_id, "list_rules"),
                ("node", "nodes", within_node_id, "list_nodes"),
            ):
                if (
                    item_id is not None
                    and await fetch_one(
                        conn, f"SELECT 1 AS found FROM {table} WHERE id = %s", (item_id,)
                    )
                    is None
                ):
                    raise Refusal(
                        f"There is no {what} with id {item_id}. Use {finder} to find the id you "
                        "want."
                    )
            if node_id is not None:
                conditions.append(
                    "EXISTS (SELECT 1 FROM memory_nodes l WHERE l.memory_id = m.id "
                    "AND l.node_id = %(node_id)s)"
                )
                params["node_id"] = node_id
            if rule_id is not None:
                conditions.append(
                    "EXISTS (SELECT 1 FROM memory_rules l WHERE l.memory_id = m.id "
                    "AND l.rule_id = %(rule_id)s)"
                )
                params["rule_id"] = rule_id
            if within_node_id is not None:
                # The item, everything under it, and the rules placed in any of those stories.
                prefix = (
                    "WITH RECURSIVE area(id) AS (SELECT id FROM nodes WHERE id = %(within)s "
                    "UNION ALL SELECT n.id FROM nodes n JOIN area a ON n.parent_id = a.id) "
                )
                conditions.append(
                    "(EXISTS (SELECT 1 FROM memory_nodes l WHERE l.memory_id = m.id "
                    "AND l.node_id IN (SELECT id FROM area)) "
                    "OR EXISTS (SELECT 1 FROM memory_rules l JOIN rules r ON r.id = l.rule_id "
                    "WHERE l.memory_id = m.id AND r.story_id IN (SELECT id FROM area)))"
                )
                params["within"] = within_node_id
            if rests_on_memory_id is not None:
                found = await fetch_one(
                    conn, "SELECT type FROM memories WHERE id = %s", (rests_on_memory_id,)
                )
                if found is None or found["type"] != "fact":
                    raise Refusal(
                        f"There is no fact with id {rests_on_memory_id}. Beliefs rest on facts and "
                        "decisions, never on other beliefs. Use list_memories with type fact to "
                        "find the id you want."
                    )
                conditions.append(
                    "EXISTS (SELECT 1 FROM belief_evidence be WHERE be.belief_id = m.id "
                    "AND be.fact_id = %(rests_on_fact)s)"
                )
                params["rests_on_fact"] = rests_on_memory_id
            if rests_on_decision_id is not None:
                await require_decision(conn, rests_on_decision_id)
                conditions.append(
                    "EXISTS (SELECT 1 FROM belief_evidence be WHERE be.belief_id = m.id "
                    "AND be.decision_id = %(rests_on_decision)s)"
                )
                params["rests_on_decision"] = rests_on_decision_id
            where = " AND ".join(conditions) or "true"
            counted = await fetch_one(
                conn,
                f"{prefix}SELECT count(*) AS total FROM memory_state m WHERE {where}",
                params,
            )
            assert counted is not None
            rows = await fetch_all(
                conn,
                f"{prefix}SELECT m.* FROM memory_state m WHERE {where} AND m.id > %(after)s "
                "ORDER BY m.id LIMIT %(fetch)s",
                {**params, "after": after_id or 0, "fetch": limit + 1},
            )
        page, next_after_id = split_page(rows, limit)
        return MemoryPage(
            items=[to_memory(row) for row in page],
            total=int(counted["total"]),
            next_after_id=next_after_id,
        )

    async def memory_history(
        self, *, memory_id: int, limit: int, after_id: int | None
    ) -> EventPage:
        limit = page_size(limit)
        async with self._database.transaction() as conn:
            await require_memory(conn, memory_id)
            rows = await fetch_all(
                conn,
                f"SELECT {EVENT_COLUMNS} FROM events "
                "WHERE memory_id = %s AND id > %s ORDER BY id LIMIT %s",
                (memory_id, after_id or 0, limit + 1),
            )
        page, next_after_id = split_page(rows, limit)
        return EventPage(
            items=[Event.model_validate(row) for row in page], next_after_id=next_after_id
        )


def _require_active_belief(row: dict[str, Any], verb: str) -> None:
    memory_id = row["id"]
    if row["type"] != "belief":
        raise Refusal(f"Memory {memory_id} is a fact. Only a belief can be {verb}.")
    if row["status"] != "active":
        raise Refusal(f"{_ended(row)}, so it cannot be {verb}.")
    if row["stale"]:
        raise Refusal(
            f"Belief {memory_id} is stale ({'; '.join(_stale_because(row))}), so it cannot be "
            f"{verb}. Replace it with a revised belief first."
        )
