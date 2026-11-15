"""Decisions and principles. This module owns recording them, linking them to the nodes and rules
they affect, superseding them, and recalling them."""

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
    snapshot,
    split_page,
)
from vrum.db import Database
from vrum.graph import EVENT_COLUMNS, Event, EventPage

DECISION_KINDS = ("decision", "principle")
DECISION_STATUSES = ("active", "superseded")
RECALL_LISTS = ("all", "active_decisions", "active_principles", "superseded_decisions")

_COLUMNS = (
    "id, kind, title, text, reason, decided_by, decided_on, status, superseded_by_id, "
    "supersedes_ids, node_ids, rule_ids, recorded_at"
)
_RECORDED_FIELDS = ("kind", "title", "text", "reason", "decided_by", "decided_on")

# Where a decision's links to each kind of thing are kept. Fixed here, never from a caller.
_LINK_TABLES = {"node": ("decision_nodes", "node_id"), "rule": ("decision_rules", "rule_id")}


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
    node_ids: list[int] = Field(description="The epics, stories and tasks it affects.")
    rule_ids: list[int] = Field(description="The rules it affects.")
    recorded_at: datetime


class DecisionPage(BaseModel):
    items: list[Decision]
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )


class Recall(BaseModel):
    active_decisions: list[Decision] = Field(
        description="Active decisions linked to this item. Only what is linked, nothing inferred."
    )
    active_principles: list[Decision] = Field(
        description="Every active principle, linked to this item or not."
    )
    superseded_decisions: list[Decision] = Field(
        description="Decisions and principles linked to this item that a newer decision has "
        "replaced. Shown so you can see what changed; they are no longer in force."
    )
    active_decision_count: int = Field(description="How many there are in all, across pages.")
    active_principle_count: int
    superseded_decision_count: int
    next_active_decisions_after_id: int | None = Field(
        description="Pass as after_id with show=active_decisions for the next page."
    )
    next_active_principles_after_id: int | None = Field(
        description="Pass as after_id with show=active_principles for the next page."
    )
    next_superseded_decisions_after_id: int | None = Field(
        description="Pass as after_id with show=superseded_decisions for the next page."
    )


def _required_text(value: str, refusal: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise Refusal(refusal)
    return cleaned


def _clean_fields(title: str, text: str, reason: str, decided_by: str) -> tuple[str, ...]:
    return (
        _required_text(title, "A decision needs a title: title cannot be empty."),
        _required_text(
            text, "A decision needs its text: text cannot be empty. Say what was decided."
        ),
        _required_text(reason, "A decision needs a reason: reason cannot be empty. Say why."),
        _required_text(
            decided_by,
            "A decision needs the person who made it: decided_by must be the name of a person. "
            "(You are recorded separately, as actor.)",
        ),
    )


def _unique(ids: list[int] | None) -> list[int]:
    return list(dict.fromkeys(ids or []))


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


async def _require_targets(conn: AsyncConnection, node_ids: list[int], rule_ids: list[int]) -> None:
    for what, table, ids, finder in (
        ("node", "nodes", node_ids, "list_nodes"),
        ("rule", "rules", rule_ids, "list_rules"),
    ):
        if not ids:
            continue
        found = {
            row["id"]
            for row in await fetch_all(conn, f"SELECT id FROM {table} WHERE id = ANY(%s)", (ids,))
        }
        missing = [str(item) for item in ids if item not in found]
        if missing:
            raise Refusal(
                f"There is no {what} with id {', '.join(missing)}, so the decision cannot be "
                f"linked to it. Use {finder} to find the id you want."
            )


async def _add_links(
    conn: AsyncConnection,
    decision_id: int,
    actor: str,
    node_ids: list[int],
    rule_ids: list[int],
) -> None:
    for what, ids in (("node", node_ids), ("rule", rule_ids)):
        table, column = _LINK_TABLES[what]
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


async def _insert_decision(
    conn: AsyncConnection,
    *,
    actor: str,
    kind: str,
    fields: tuple[str, ...],
    decided_on: date | None,
    replaces: int | None,
) -> int:
    title, text, reason, decided_by = fields
    row = await fetch_one(
        conn,
        "INSERT INTO decisions (kind, title, text, reason, decided_by, decided_on) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "RETURNING id, kind, title, text, reason, decided_by, decided_on",
        (kind, title, text, reason, decided_by, decided_on or datetime.now(UTC).date()),
    )
    assert row is not None
    after = snapshot(row, _RECORDED_FIELDS)
    if replaces is not None:
        after["supersedes_id"] = replaces
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
        fields = _clean_fields(title, text, reason, decided_by)
        nodes, rules = _unique(node_ids), _unique(rule_ids)
        async with self._database.transaction() as conn:
            await _require_targets(conn, nodes, rules)
            decision_id = await _insert_decision(
                conn, actor=actor, kind=kind, fields=fields, decided_on=decided_on, replaces=None
            )
            await _add_links(conn, decision_id, actor, nodes, rules)
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
        fields = _clean_fields(title, text, reason, decided_by)
        extra_nodes, extra_rules = _unique(node_ids), _unique(rule_ids)
        async with self._database.transaction() as conn:
            old = await require_decision(conn, decision_id, lock=True)
            if old["status"] == "superseded":
                raise Refusal(
                    f"Decision {decision_id} was already superseded by decision "
                    f"{old['superseded_by_id']}. If that one is changing too, supersede "
                    f"decision {old['superseded_by_id']} instead."
                )
            await _require_targets(conn, extra_nodes, extra_rules)
            nodes = _unique(old["node_ids"] + extra_nodes) if keep_links else extra_nodes
            rules = _unique(old["rule_ids"] + extra_rules) if keep_links else extra_rules
            new_id = await _insert_decision(
                conn,
                actor=actor,
                kind=kind or old["kind"],
                fields=fields,
                decided_on=decided_on,
                replaces=decision_id,
            )
            await _add_links(conn, new_id, actor, nodes, rules)
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
            page, next_after_id = await _page(
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
        nodes, rules = _unique(node_ids), _unique(rule_ids)
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
                await _require_targets(conn, nodes, rules)
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
                await _add_links(conn, decision_id, actor, nodes, rules)
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

    async def recall_decisions(
        self,
        *,
        node_id: int | None,
        rule_id: int | None,
        show: str,
        limit: int,
        after_id: int | None,
    ) -> Recall:
        """What has been decided about one node or rule, by exact links only: its active
        decisions, every active principle, and the linked decisions that have been replaced."""
        limit = page_size(limit)
        check_choice("show", show, RECALL_LISTS)
        if (node_id is None) == (rule_id is None):
            raise Refusal(
                "Give exactly one of node_id or rule_id: the thing to recall decisions for."
            )
        if after_id is not None and show == "all":
            raise Refusal(
                "after_id only works for one list at a time: set show to active_decisions, "
                "active_principles or superseded_decisions."
            )
        what, thing = ("node", node_id) if node_id is not None else ("rule", rule_id)
        table, column = _LINK_TABLES[what]
        async with self._database.transaction() as conn:
            found = await fetch_one(
                conn,
                f"SELECT 1 AS found FROM {'nodes' if what == 'node' else 'rules'} WHERE id = %s",
                (thing,),
            )
            if found is None:
                finder = "list_nodes" if what == "node" else "list_rules"
                raise Refusal(
                    f"There is no {what} with id {thing}. Use {finder} to find the id you want."
                )
            linked = f"id IN (SELECT decision_id FROM {table} WHERE {column} = %(thing)s)"
            lists = {
                "active_decisions": (
                    f"{linked} AND kind = 'decision' AND status = 'active'",
                    {"thing": thing},
                ),
                "active_principles": ("kind = 'principle' AND status = 'active'", {}),
                "superseded_decisions": (f"{linked} AND status = 'superseded'", {"thing": thing}),
            }
            pages: dict[str, tuple[list[Decision], int | None]] = {}
            counts: dict[str, int] = {}
            for name, (where, params) in lists.items():
                counts[name] = await _count(conn, where, params)
                pages[name] = (
                    await _page(conn, where, params, limit, after_id)
                    if show in ("all", name)
                    else ([], None)
                )
        return Recall(
            active_decisions=pages["active_decisions"][0],
            active_principles=pages["active_principles"][0],
            superseded_decisions=pages["superseded_decisions"][0],
            active_decision_count=counts["active_decisions"],
            active_principle_count=counts["active_principles"],
            superseded_decision_count=counts["superseded_decisions"],
            next_active_decisions_after_id=pages["active_decisions"][1],
            next_active_principles_after_id=pages["active_principles"][1],
            next_superseded_decisions_after_id=pages["superseded_decisions"][1],
        )


async def _remove_links(
    conn: AsyncConnection, decision_id: int, actor: str, node_ids: list[int], rule_ids: list[int]
) -> None:
    for what, ids in (("node", node_ids), ("rule", rule_ids)):
        table, column = _LINK_TABLES[what]
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


async def _page(
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


async def _count(conn: AsyncConnection, where: str, params: dict[str, Any]) -> int:
    row = await fetch_one(
        conn, f"SELECT count(*) AS total FROM decision_state WHERE {where}", params
    )
    assert row is not None
    return int(row["total"])
