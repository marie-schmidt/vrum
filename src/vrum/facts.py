"""Facts. This module owns recording them, what they concern, replacing and withdrawing them, and
confirming them.

A fact is a statement with its source, and a record: it is never edited. A change is a newer fact
that replaces it, or a withdrawal. The server never decides by itself that something is
contradicted: a caller names what is replaced. Whether a person confirmed a fact is a field of the
fact (see records.py), and recording a fact never confirms it."""

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
    parse_node_ref,
    parse_node_refs,
    record_event,
    require_link_targets,
    required_text,
    snapshot,
    split_page,
    unique_ids,
)
from vrum.db import Database
from vrum.decisions import require_decision
from vrum.graph import EVENT_COLUMNS, Event, EventPage, require_node
from vrum.records import (
    CONFIRMATIONS,
    apply_confirmation,
    ended_words,
    label_of,
    require_a_person_for_confirmed,
    said,
)
from vrum.rules import require_rule

FACT_STATUSES = ("active", "superseded", "withdrawn")

FACT_COLUMNS = (
    "id, text, source, recorded_by, recorded_at, status, superseded_by_id, supersedes_ids, "
    "status_reason, confirmed_by, confirmed_at, confirmation, nodes, rule_ids, decision_ids"
)
_RECORDED_FIELDS = ("text", "source")

# Where a fact's links to each kind of thing are kept. Fixed here, never from a caller.
_LINK_TABLES = {
    "node": ("fact_nodes", "node"),
    "rule": ("fact_rules", "rule_id"),
    "decision": ("fact_decisions", "decision_id"),
}


class Fact(BaseModel):
    id: int = Field(description="The id of this fact. Facts have their own ids.")
    label: str = Field(
        description="What this is, in plain words: whether it is in force and whether a person "
        "confirmed it."
    )
    text: str = Field(description="What is known.")
    source: str = Field(description="Where it came from, in plain words.")
    recorded_by: str = Field(description="The person or agent that recorded it.")
    recorded_at: datetime
    status: str = Field(
        description="active, superseded (replaced by a newer fact) or withdrawn (found to be "
        "wrong, with a reason). It is never edited."
    )
    superseded_by_id: int | None = Field(description="What replaced it. Empty while it is active.")
    supersedes_ids: list[int] = Field(description="What this fact replaced, if anything.")
    status_reason: str | None = Field(
        description="Why it was superseded or withdrawn. Empty while it is active."
    )
    confirmation: str = Field(
        description="confirmed once a named person has reviewed it, otherwise unconfirmed. It "
        "says a person looked, not that the fact is true."
    )
    confirmed_by: str | None = Field(description="The person who confirmed it.")
    confirmed_at: datetime | None
    nodes: list[str] = Field(
        description="The epics, stories and tasks it concerns, as refs such as story:12."
    )
    rule_ids: list[int] = Field(description="The requirements it concerns.")
    decision_ids: list[int] = Field(description="The decisions it concerns.")


class FactPage(BaseModel):
    items: list[Fact]
    total: int = Field(
        description="How many facts match in all, across pages, so a walk through every page can "
        "be checked against it."
    )
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )


class FactChange(BaseModel):
    replacement: Fact | None = Field(
        description="The newer fact that replaced the others. Empty when facts were withdrawn."
    )
    superseded_ids: list[int] = Field(description="The facts now marked superseded.")
    withdrawn_ids: list[int] = Field(description="The facts now marked withdrawn.")


def to_fact(row: dict[str, Any]) -> Fact:
    return Fact.model_validate(
        {**row, "label": f"{label_of('fact', row)}. Source: {row['source']}"}
    )


async def require_fact(
    conn: AsyncConnection, fact_id: int, *, lock: bool = False
) -> dict[str, Any]:
    """The fact as read, or a plain refusal. `lock` holds its row until the transaction ends, so
    that changes to it happen one at a time."""
    if lock:
        await conn.execute("SELECT 1 FROM facts WHERE id = %s FOR UPDATE", (fact_id,))
    row = await fetch_one(conn, f"SELECT {FACT_COLUMNS} FROM fact_state WHERE id = %s", (fact_id,))
    if row is None:
        raise Refusal(
            f"There is no fact with id {fact_id}. Use list_facts to find the id you want."
        )
    return row


async def _lock(conn: AsyncConnection, fact_ids: list[int]) -> None:
    """Hold these rows until the transaction ends so that they change one call at a time. Always
    taken in id order, so two calls that overlap wait for each other and never on each other."""
    for fact_id in sorted(fact_ids):
        await conn.execute("SELECT 1 FROM facts WHERE id = %s FOR UPDATE", (fact_id,))


async def _require_found(conn: AsyncConnection, fact_ids: list[int]) -> list[dict[str, Any]]:
    rows = await fetch_all(
        conn, f"SELECT {FACT_COLUMNS} FROM fact_state WHERE id = ANY(%s)", (fact_ids,)
    )
    found = {row["id"] for row in rows}
    missing = [str(item) for item in fact_ids if item not in found]
    if missing:
        raise Refusal(
            f"There is no fact with id {', '.join(missing)}. Use list_facts to find the id you "
            "want. Nothing was changed."
        )
    return rows


async def _require_active(
    conn: AsyncConnection, fact_ids: list[int], verb: str
) -> list[dict[str, Any]]:
    """The facts as read now that they are locked, refused in plain words unless each is active."""
    rows = {row["id"]: row for row in await _require_found(conn, fact_ids)}
    for fact_id in fact_ids:
        row = rows[fact_id]
        if row["status"] != "active":
            raise Refusal(
                f"{ended_words('fact', row)}, so it cannot be {verb}. Nothing was changed."
            )
    return [rows[fact_id] for fact_id in fact_ids]


async def _require_decisions(conn: AsyncConnection, decision_ids: list[int]) -> None:
    for decision_id in decision_ids:
        await require_decision(conn, decision_id)


async def _insert_fact(
    conn: AsyncConnection, *, actor: str, text: str, source: str, supersedes: list[int]
) -> int:
    row = await fetch_one(
        conn,
        "INSERT INTO facts (text, source, recorded_by) VALUES (%s, %s, %s) "
        "RETURNING id, text, source",
        (text, source, actor),
    )
    assert row is not None
    after = snapshot(row, _RECORDED_FIELDS)
    if supersedes:
        after["supersedes_ids"] = supersedes
    await record_event(
        conn, fact_id=row["id"], action="fact_recorded", actor=actor, before=None, after=after
    )
    return int(row["id"])


def _ends(
    nodes: list[str], rule_ids: list[int], decision_ids: list[int]
) -> list[tuple[str, list[Any]]]:
    """What a fact is linked to, by kind: nodes by their refs, the others by their ids."""
    return [("node", nodes), ("rule", rule_ids), ("decision", decision_ids)]


async def _add_links(
    conn: AsyncConnection,
    fact_id: int,
    actor: str,
    nodes: list[str],
    rule_ids: list[int],
    decision_ids: list[int],
) -> None:
    for what, ends in _ends(nodes, rule_ids, decision_ids):
        table, column = _LINK_TABLES[what]
        for item in ends:
            await conn.execute(
                f"INSERT INTO {table} (fact_id, {column}) VALUES (%s, %s)", (fact_id, item)
            )
            await record_event(
                conn,
                fact_id=fact_id,
                action="fact_linked",
                actor=actor,
                before=None,
                after={"fact_id": fact_id},
                **{column: item},
            )


async def _remove_links(
    conn: AsyncConnection,
    fact_id: int,
    actor: str,
    nodes: list[str],
    rule_ids: list[int],
    decision_ids: list[int],
) -> None:
    for what, ends in _ends(nodes, rule_ids, decision_ids):
        table, column = _LINK_TABLES[what]
        for item in ends:
            await conn.execute(
                f"DELETE FROM {table} WHERE fact_id = %s AND {column} = %s", (fact_id, item)
            )
            await record_event(
                conn,
                fact_id=fact_id,
                action="fact_unlinked",
                actor=actor,
                before={"fact_id": fact_id},
                after={},
                **{column: item},
            )


class Facts:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def retain_fact(
        self,
        *,
        actor: str,
        text: str,
        source: str,
        nodes: list[str] | None,
        rule_ids: list[int] | None,
        decision_ids: list[int] | None,
    ) -> Fact:
        actor = clean_actor(actor)
        text = required_text(
            text, "A fact needs its text: text cannot be empty. Say what is known."
        )
        source = required_text(
            source,
            "A fact needs its source: source cannot be empty. Say where it came from, in plain "
            "words (who said it, which document, what was observed).",
        )
        concerned, rules, decisions = (
            parse_node_refs(nodes),
            unique_ids(rule_ids),
            unique_ids(decision_ids),
        )
        async with self._database.transaction() as conn:
            await require_link_targets(conn, concerned, rules, "the fact")
            await _require_decisions(conn, decisions)
            fact_id = await _insert_fact(conn, actor=actor, text=text, source=source, supersedes=[])
            await _add_links(conn, fact_id, actor, concerned, rules, decisions)
            return to_fact(await require_fact(conn, fact_id))

    async def supersede_fact(
        self,
        *,
        actor: str,
        replaces: list[int],
        reason: str,
        text: str,
        source: str,
        keep_links: bool,
        nodes: list[str] | None,
        rule_ids: list[int] | None,
        decision_ids: list[int] | None,
        said_by: str | None,
        quote: str | None,
    ) -> FactChange:
        """Record a newer fact that replaces one or more active ones, and mark each of them
        superseded, in one step."""
        actor = clean_actor(actor)
        old_ids = unique_ids(replaces)
        if not old_ids:
            raise Refusal("Say what is being replaced: replaces needs at least one id.")
        reason = required_text(
            reason, "A replacement needs a reason: reason cannot be empty. Say why it changed."
        )
        text = required_text(text, "The new fact needs its text: text cannot be empty.")
        source = required_text(
            source,
            "The new fact needs its source: source cannot be empty. Say where it came from.",
        )
        said_words = said(said_by, quote)
        extra_nodes, extra_rules = parse_node_refs(nodes), unique_ids(rule_ids)
        extra_decisions = unique_ids(decision_ids)
        async with self._database.transaction() as conn:
            await _require_found(conn, old_ids)
            await _lock(conn, old_ids)
            olds = await _require_active(conn, old_ids, "replaced")
            require_a_person_for_confirmed("fact", olds, said_words)
            await require_link_targets(conn, extra_nodes, extra_rules, "the new fact")
            await _require_decisions(conn, extra_decisions)
            carried_nodes = [item for row in olds for item in row["nodes"]] if keep_links else []
            carried_rules = [item for row in olds for item in row["rule_ids"]] if keep_links else []
            carried_decisions = (
                [item for row in olds for item in row["decision_ids"]] if keep_links else []
            )
            new_id = await _insert_fact(
                conn, actor=actor, text=text, source=source, supersedes=old_ids
            )
            await _add_links(
                conn,
                new_id,
                actor,
                list(dict.fromkeys(carried_nodes + extra_nodes)),
                unique_ids(carried_rules + extra_rules),
                unique_ids(carried_decisions + extra_decisions),
            )
            await conn.execute(
                "UPDATE facts SET status = 'superseded', superseded_by_id = %s, "
                "status_reason = %s WHERE id = ANY(%s)",
                (new_id, reason, old_ids),
            )
            for old_id in old_ids:
                await record_event(
                    conn,
                    fact_id=old_id,
                    action="fact_superseded",
                    actor=actor,
                    before={"status": "active", "superseded_by_id": None},
                    after={
                        "status": "superseded",
                        "superseded_by_id": new_id,
                        "reason": reason,
                        **said_words,
                    },
                )
            return FactChange(
                replacement=to_fact(await require_fact(conn, new_id)),
                superseded_ids=old_ids,
                withdrawn_ids=[],
            )

    async def withdraw_fact(
        self,
        *,
        actor: str,
        fact_ids: list[int],
        reason: str,
        said_by: str | None,
        quote: str | None,
    ) -> FactChange:
        """Mark one or more active facts as wrong, in one step. Nothing is deleted."""
        actor = clean_actor(actor)
        ids = unique_ids(fact_ids)
        if not ids:
            raise Refusal("Say what is being withdrawn: fact_ids needs at least one id.")
        reason = required_text(
            reason, "A withdrawal needs a reason: reason cannot be empty. Say why it is wrong."
        )
        said_words = said(said_by, quote)
        async with self._database.transaction() as conn:
            await _require_found(conn, ids)
            await _lock(conn, ids)
            rows = await _require_active(conn, ids, "withdrawn")
            require_a_person_for_confirmed("fact", rows, said_words)
            await conn.execute(
                "UPDATE facts SET status = 'withdrawn', status_reason = %s WHERE id = ANY(%s)",
                (reason, ids),
            )
            for fact_id in ids:
                await record_event(
                    conn,
                    fact_id=fact_id,
                    action="fact_withdrawn",
                    actor=actor,
                    before={"status": "active"},
                    after={"status": "withdrawn", "reason": reason, **said_words},
                )
            return FactChange(replacement=None, superseded_ids=[], withdrawn_ids=ids)

    async def set_confirmation(
        self,
        *,
        actor: str,
        fact_id: int,
        confirmation: str,
        person: str,
        quote: str | None,
    ) -> Fact:
        actor = clean_actor(actor)
        check_choice("confirmation", confirmation, CONFIRMATIONS)
        async with self._database.transaction() as conn:
            row = await require_fact(conn, fact_id, lock=True)
            await apply_confirmation(
                conn,
                noun="fact",
                row=row,
                actor=actor,
                confirmation=confirmation,
                person=person,
                quote=quote,
            )
            return to_fact(await require_fact(conn, fact_id))

    async def link_fact(
        self,
        *,
        actor: str,
        fact_id: int,
        nodes: list[str] | None,
        rule_ids: list[int] | None,
        decision_ids: list[int] | None,
    ) -> Fact:
        return await self._change_links(
            actor=actor,
            fact_id=fact_id,
            nodes=nodes,
            rule_ids=rule_ids,
            decision_ids=decision_ids,
            add=True,
        )

    async def unlink_fact(
        self,
        *,
        actor: str,
        fact_id: int,
        nodes: list[str] | None,
        rule_ids: list[int] | None,
        decision_ids: list[int] | None,
    ) -> Fact:
        return await self._change_links(
            actor=actor,
            fact_id=fact_id,
            nodes=nodes,
            rule_ids=rule_ids,
            decision_ids=decision_ids,
            add=False,
        )

    async def _change_links(
        self,
        *,
        actor: str,
        fact_id: int,
        nodes: list[str] | None,
        rule_ids: list[int] | None,
        decision_ids: list[int] | None,
        add: bool,
    ) -> Fact:
        actor = clean_actor(actor)
        concerned, rules, decisions = (
            parse_node_refs(nodes),
            unique_ids(rule_ids),
            unique_ids(decision_ids),
        )
        if not concerned and not rules and not decisions:
            raise Refusal(
                "Nothing to link: give at least one ref in nodes (such as story:12), or one id in "
                "rule_ids or decision_ids."
            )
        async with self._database.transaction() as conn:
            row = await require_fact(conn, fact_id, lock=True)
            if row["status"] != "active":
                where = (
                    "Change the links of the fact that replaced it instead."
                    if row["status"] == "superseded"
                    else "It is withdrawn, so its links stay as they were."
                )
                raise Refusal(
                    f"{ended_words('fact', row)}, and an ended fact keeps the links it had. {where}"
                )
            if add:
                await require_link_targets(conn, concerned, rules, f"fact {fact_id}")
                await _require_decisions(conn, decisions)
            for what, ends, current in (
                ("node", concerned, row["nodes"]),
                ("rule", rules, row["rule_ids"]),
                ("decision", decisions, row["decision_ids"]),
            ):
                # A node's ref already says what it is; the others are named by their ids.
                wrong = [
                    str(item) if what == "node" else f"{what} {item}"
                    for item in ends
                    if (item in current) == add
                ]
                if wrong:
                    raise Refusal(
                        f"Fact {fact_id} is {'already' if add else 'not'} linked to "
                        f"{', '.join(wrong)}, so nothing was changed."
                    )
            if add:
                await _add_links(conn, fact_id, actor, concerned, rules, decisions)
            else:
                await _remove_links(conn, fact_id, actor, concerned, rules, decisions)
            return to_fact(await require_fact(conn, fact_id))

    async def get_fact(self, fact_id: int) -> Fact:
        async with self._database.transaction() as conn:
            return to_fact(await require_fact(conn, fact_id))

    async def list_facts(
        self,
        *,
        status: str | None,
        confirmation: str | None,
        node: str | None,
        rule_id: int | None,
        decision_id: int | None,
        within_node: str | None,
        limit: int,
        after_id: int | None,
    ) -> FactPage:
        limit = page_size(limit)
        if status is not None:
            check_choice("status", status, FACT_STATUSES)
        if confirmation is not None:
            check_choice("confirmation", confirmation, CONFIRMATIONS)
        if node is not None:
            node = parse_node_ref(node, "node")
        if within_node is not None:
            within_node = parse_node_ref(within_node, "within_node")
        conditions: list[str] = []
        params: dict[str, Any] = {}
        for column, wanted in (("status", status), ("confirmation", confirmation)):
            if wanted is not None:
                conditions.append(f"f.{column} = %({column})s")
                params[column] = wanted
        prefix = ""
        async with self._database.transaction() as conn:
            for ref in (node, within_node):
                if ref is not None:
                    await require_node(conn, ref)
            if rule_id is not None:
                await require_rule(conn, rule_id)
            if decision_id is not None:
                await require_decision(conn, decision_id)
            for column, table, linked in (
                ("node", "fact_nodes", node),
                ("rule_id", "fact_rules", rule_id),
                ("decision_id", "fact_decisions", decision_id),
            ):
                if linked is not None:
                    conditions.append(
                        f"EXISTS (SELECT 1 FROM {table} l WHERE l.fact_id = f.id "
                        f"AND l.{column} = %({column})s)"
                    )
                    params[column] = linked
            if within_node is not None:
                # The item, everything under it, and the rules placed in any of those stories.
                prefix = (
                    "WITH RECURSIVE area(ref) AS (SELECT ref FROM nodes WHERE ref = %(within)s "
                    "UNION ALL SELECT n.ref FROM nodes n JOIN area a ON n.parent = a.ref) "
                )
                conditions.append(
                    "(EXISTS (SELECT 1 FROM fact_nodes l WHERE l.fact_id = f.id "
                    "AND l.node IN (SELECT ref FROM area)) "
                    "OR EXISTS (SELECT 1 FROM fact_rules l JOIN rules r ON r.id = l.rule_id "
                    "WHERE l.fact_id = f.id AND r.story IN (SELECT ref FROM area)))"
                )
                params["within"] = within_node
            where = " AND ".join(conditions) or "true"
            counted = await fetch_one(
                conn, f"{prefix}SELECT count(*) AS total FROM fact_state f WHERE {where}", params
            )
            assert counted is not None
            rows = await fetch_all(
                conn,
                f"{prefix}SELECT f.* FROM fact_state f WHERE {where} AND f.id > %(after)s "
                "ORDER BY f.id LIMIT %(fetch)s",
                {**params, "after": after_id or 0, "fetch": limit + 1},
            )
        page, next_after_id = split_page(rows, limit)
        return FactPage(
            items=[to_fact(row) for row in page],
            total=int(counted["total"]),
            next_after_id=next_after_id,
        )

    async def fact_history(self, *, fact_id: int, limit: int, after_id: int | None) -> EventPage:
        limit = page_size(limit)
        async with self._database.transaction() as conn:
            await require_fact(conn, fact_id)
            rows = await fetch_all(
                conn,
                f"SELECT {EVENT_COLUMNS} FROM events "
                "WHERE fact_id = %s AND id > %s ORDER BY id LIMIT %s",
                (fact_id, after_id or 0, limit + 1),
            )
        page, next_after_id = split_page(rows, limit)
        return EventPage(
            items=[Event.model_validate(row) for row in page], next_after_id=next_after_id
        )
