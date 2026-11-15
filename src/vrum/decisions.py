"""Decisions and principles. This module owns recording them, linking them to the nodes and rules
they affect, and superseding them."""

from datetime import UTC, date, datetime
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
from vrum.graph import EVENT_COLUMNS, Event, EventPage

DECISION_KINDS = ("decision", "principle")
DECISION_STATUSES = ("active", "superseded")

_COLUMNS = (
    "id, kind, title, text, reason, decided_by, decided_on, status, superseded_by_id, "
    "supersedes_ids, promoted_from_id, node_ids, rule_ids, recorded_at"
)
_RECORDED_FIELDS = ("kind", "title", "text", "reason", "decided_by", "decided_on")

# Where a decision's links to each kind of thing are kept. Fixed here, never from a caller.
LINK_TABLES = {"node": ("decision_nodes", "node_id"), "rule": ("decision_rules", "rule_id")}


class Decision(BaseModel):
    id: int = Field(description="The decision's id. Decisions have their own ids.")
    kind: str = Field(
        description="decision: something decided about this work. principle: a standing guideline "
        "that applies everywhere, so recalling anything also returns every active principle."
    )
    title: str
    text: str = Field(description="What was decided.")
    reason: str = Field(description="Why.")
    decided_by: str = Field(description="The person who made the decision.")
    decided_on: date = Field(description="The day it was decided.")
    status: str = Field(
        description="active, or superseded once a newer decision has replaced it. A superseded "
        "decision is kept as a record and is no longer in force."
    )
    superseded_by_id: int | None = Field(
        description="The decision that replaced this one. Empty while it is active."
    )
    supersedes_ids: list[int] = Field(description="The decisions this one replaced, if any.")
    promoted_from_id: int | None = Field(
        description="The belief a person promoted to this decision, if it began as one."
    )
    node_ids: list[int] = Field(description="The epics, stories and tasks it affects.")
    rule_ids: list[int] = Field(description="The rules it affects.")
    recorded_at: datetime


class DecisionPage(BaseModel):
    items: list[Decision]
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )


def clean_fields(title: str, text: str, reason: str, decided_by: str) -> tuple[str, ...]:
    return (
        required_text(title, "A decision needs a title: title cannot be empty."),
        required_text(
            text, "A decision needs its text: text cannot be empty. Say what was decided."
        ),
        required_text(reason, "A decision needs a reason: reason cannot be empty. Say why."),
        required_text(
            decided_by,
            "A decision needs the person who made it: decided_by must be the name of a person. "
            "(You are recorded separately, as actor.)",
        ),
    )


async def require_decision(
    conn: AsyncConnection, decision_id: int, *, lock: bool = False
) -> dict[str, Any]:
    """The decision as read, or a plain refusal. `lock` holds its row until the transaction ends,
    so that supersessions and link changes of it happen one at a time."""
    if lock:
        await conn.execute("SELECT 1 FROM decisions WHERE id = %s FOR UPDATE", (decision_id,))
    row = await fetch_one(
        conn, f"SELECT {_COLUMNS} FROM decision_state WHERE id = %s", (decision_id,)
    )
    if row is None:
        raise Refusal(
            f"There is no decision with id {decision_id}. Use list_decisions to find the id you "
            "want."
        )
    return row


async def add_links(
    conn: AsyncConnection,
    decision_id: int,
    actor: str,
    node_ids: list[int],
    rule_ids: list[int],
) -> None:
    for what, ids in (("node", node_ids), ("rule", rule_ids)):
        table, column = LINK_TABLES[what]
        for item in ids:
            await conn.execute(
                f"INSERT INTO {table} (decision_id, {column}) VALUES (%s, %s)", (decision_id, item)
            )
            await record_event(
                conn,
                decision_id=decision_id,
                action="decision_linked",
                actor=actor,
                before=None,
                after={"decision_id": decision_id},
                **{column: item},
            )


async def insert_decision(
    conn: AsyncConnection,
    *,
    actor: str,
    kind: str,
    fields: tuple[str, ...],
    decided_on: date | None,
    replaces: int | None = None,
    promoted_from: int | None = None,
) -> int:
    title, text, reason, decided_by = fields
    row = await fetch_one(
        conn,
        "INSERT INTO decisions (kind, title, text, reason, decided_by, decided_on, "
        "promoted_from_id) VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id, kind, title, text, reason, decided_by, decided_on",
        (
            kind,
            title,
            text,
            reason,
            decided_by,
            decided_on or datetime.now(UTC).date(),
            promoted_from,
        ),
    )
    assert row is not None
    after = snapshot(row, _RECORDED_FIELDS)
    if replaces is not None:
        after["supersedes_id"] = replaces
    if promoted_from is not None:
        after["promoted_from_id"] = promoted_from
    await record_event(
        conn,
        decision_id=row["id"],
        action="decision_recorded",
        actor=actor,
        before=None,
        after=after,
    )
    return int(row["id"])


class Decisions:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def record_decision(
        self,
        *,
        actor: str,
        kind: str,
        title: str,
        text: str,
        reason: str,
        decided_by: str,
        decided_on: date | None,
        node_ids: list[int] | None,
        rule_ids: list[int] | None,
    ) -> Decision:
        actor = clean_actor(actor)
        check_choice("kind", kind, DECISION_KINDS)
        fields = clean_fields(title, text, reason, decided_by)
        nodes, rules = unique_ids(node_ids), unique_ids(rule_ids)
        async with self._database.transaction() as conn:
            await require_link_targets(conn, nodes, rules, "the decision")
            decision_id = await insert_decision(
                conn, actor=actor, kind=kind, fields=fields, decided_on=decided_on
            )
            await add_links(conn, decision_id, actor, nodes, rules)
            return Decision.model_validate(await require_decision(conn, decision_id))

    async def supersede_decision(
        self,
        *,
        actor: str,
        decision_id: int,
        kind: str | None,
        title: str,
        text: str,
        reason: str,
        decided_by: str,
        decided_on: date | None,
        keep_links: bool,
        node_ids: list[int] | None,
        rule_ids: list[int] | None,
    ) -> Decision:
        """Record a new decision that replaces an active one, and mark the old one superseded, in
        one step."""
        actor = clean_actor(actor)
        if kind is not None:
            check_choice("kind", kind, DECISION_KINDS)
        fields = clean_fields(title, text, reason, decided_by)
        extra_nodes, extra_rules = unique_ids(node_ids), unique_ids(rule_ids)
        async with self._database.transaction() as conn:
            old = await require_decision(conn, decision_id, lock=True)
            if old["status"] == "superseded":
                raise Refusal(
                    f"Decision {decision_id} was already superseded by decision "
                    f"{old['superseded_by_id']}. If that one is changing too, supersede "
                    f"decision {old['superseded_by_id']} instead."
                )
            await require_link_targets(conn, extra_nodes, extra_rules, "the decision")
            nodes = unique_ids(old["node_ids"] + extra_nodes) if keep_links else extra_nodes
            rules = unique_ids(old["rule_ids"] + extra_rules) if keep_links else extra_rules
            new_id = await insert_decision(
                conn,
                actor=actor,
                kind=kind or old["kind"],
                fields=fields,
                decided_on=decided_on,
                replaces=decision_id,
            )
            await add_links(conn, new_id, actor, nodes, rules)
            await conn.execute(
                "UPDATE decisions SET status = 'superseded', superseded_by_id = %s WHERE id = %s",
                (new_id, decision_id),
            )
            await record_event(
                conn,
                decision_id=decision_id,
                action="decision_superseded",
                actor=actor,
                before={"status": "active", "superseded_by_id": None},
                after={"status": "superseded", "superseded_by_id": new_id},
            )
            return Decision.model_validate(await require_decision(conn, new_id))

    async def get_decision(self, decision_id: int) -> Decision:
        async with self._database.transaction() as conn:
            return Decision.model_validate(await require_decision(conn, decision_id))

    async def list_decisions(
        self, *, kind: str | None, status: str | None, limit: int, after_id: int | None
    ) -> DecisionPage:
        limit = page_size(limit)
        if kind is not None:
            check_choice("kind", kind, DECISION_KINDS)
        if status is not None:
            check_choice("status", status, DECISION_STATUSES)
        async with self._database.transaction() as conn:
            page, next_after_id = await page_decisions(
                conn,
                "(%(kind)s::text IS NULL OR kind = %(kind)s) "
                "AND (%(status)s::text IS NULL OR status = %(status)s)",
                {"kind": kind, "status": status},
                limit,
                after_id,
            )
        return DecisionPage(items=page, next_after_id=next_after_id)

    async def link_decision(
        self,
        *,
        actor: str,
        decision_id: int,
        node_ids: list[int] | None,
        rule_ids: list[int] | None,
    ) -> Decision:
        return await self._change_links(
            actor=actor, decision_id=decision_id, node_ids=node_ids, rule_ids=rule_ids, add=True
        )

    async def unlink_decision(
        self,
        *,
        actor: str,
        decision_id: int,
        node_ids: list[int] | None,
        rule_ids: list[int] | None,
    ) -> Decision:
        return await self._change_links(
            actor=actor, decision_id=decision_id, node_ids=node_ids, rule_ids=rule_ids, add=False
        )

    async def _change_links(
        self,
        *,
        actor: str,
        decision_id: int,
        node_ids: list[int] | None,
        rule_ids: list[int] | None,
        add: bool,
    ) -> Decision:
        actor = clean_actor(actor)
        nodes, rules = unique_ids(node_ids), unique_ids(rule_ids)
        if not nodes and not rules:
            raise Refusal("Nothing to link: give at least one id in node_ids or rule_ids.")
        async with self._database.transaction() as conn:
            decision = await require_decision(conn, decision_id, lock=True)
            if decision["status"] == "superseded":
                raise Refusal(
                    f"Decision {decision_id} was superseded by decision "
                    f"{decision['superseded_by_id']}, and a superseded decision keeps the links it "
                    f"had. Change the links of decision {decision['superseded_by_id']} instead."
                )
            if add:
                await require_link_targets(conn, nodes, rules, "the decision")
            for what, ids, current in (
                ("node", nodes, decision["node_ids"]),
                ("rule", rules, decision["rule_ids"]),
            ):
                wrong = [str(item) for item in ids if (item in current) == add]
                if wrong:
                    raise Refusal(
                        f"Decision {decision_id} is "
                        f"{'already' if add else 'not'} linked to {what} {', '.join(wrong)}, so "
                        "nothing was changed."
                    )
            if add:
                await add_links(conn, decision_id, actor, nodes, rules)
            else:
                await _remove_links(conn, decision_id, actor, nodes, rules)
            return Decision.model_validate(await require_decision(conn, decision_id))

    async def decision_history(
        self, *, decision_id: int, limit: int, after_id: int | None
    ) -> EventPage:
        limit = page_size(limit)
        async with self._database.transaction() as conn:
            await require_decision(conn, decision_id)
            rows = await fetch_all(
                conn,
                f"SELECT {EVENT_COLUMNS} FROM events "
                "WHERE decision_id = %s AND id > %s ORDER BY id LIMIT %s",
                (decision_id, after_id or 0, limit + 1),
            )
        page, next_after_id = split_page(rows, limit)
        return EventPage(
            items=[Event.model_validate(row) for row in page], next_after_id=next_after_id
        )


async def _remove_links(
    conn: AsyncConnection, decision_id: int, actor: str, node_ids: list[int], rule_ids: list[int]
) -> None:
    for what, ids in (("node", node_ids), ("rule", rule_ids)):
        table, column = LINK_TABLES[what]
        for item in ids:
            await conn.execute(
                f"DELETE FROM {table} WHERE decision_id = %s AND {column} = %s",
                (decision_id, item),
            )
            await record_event(
                conn,
                decision_id=decision_id,
                action="decision_unlinked",
                actor=actor,
                before={"decision_id": decision_id},
                after={},
                **{column: item},
            )


async def page_decisions(
    conn: AsyncConnection, where: str, params: dict[str, Any], limit: int, after_id: int | None
) -> tuple[list[Decision], int | None]:
    rows = await fetch_all(
        conn,
        f"SELECT {_COLUMNS} FROM decision_state WHERE {where} AND id > %(after)s "
        "ORDER BY id LIMIT %(fetch)s",
        {**params, "after": after_id or 0, "fetch": limit + 1},
    )
    page, next_after_id = split_page(rows, limit)
    return [Decision.model_validate(row) for row in page], next_after_id


async def count_decisions(conn: AsyncConnection, where: str, params: dict[str, Any]) -> int:
    row = await fetch_one(
        conn, f"SELECT count(*) AS total FROM decision_state WHERE {where}", params
    )
    assert row is not None
    return int(row["total"])
