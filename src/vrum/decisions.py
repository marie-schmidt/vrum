"""Decisions. This module owns recording them, linking them to the nodes and rules they affect,
superseding and withdrawing them, and confirming them.

A decision is a record: it is never edited. A change of mind is a newer decision that supersedes the
old one, or a withdrawal. One that applies throughout its project is `project_wide`: the same kind
of record, included in every briefing and recall of that project."""

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
from vrum.records import (
    CONFIRMATIONS,
    apply_confirmation,
    ended_words,
    label_of,
    require_a_person_for_confirmed,
    said,
)

DECISION_STATUSES = ("active", "superseded", "withdrawn")

_COLUMNS = (
    "id, title, text, reason, decided_by, decided_on, project_wide, status, superseded_by_id, "
    "supersedes_ids, status_reason, confirmed_by, confirmed_at, confirmation, node_ids, "
    "rule_ids, recorded_at"
)
_RECORDED_FIELDS = ("title", "text", "reason", "decided_by", "decided_on", "project_wide")

# Where a decision's links to each kind of thing are kept. Fixed here, never from a caller.
LINK_TABLES = {"node": ("decision_nodes", "node_id"), "rule": ("decision_rules", "rule_id")}


class Decision(BaseModel):
    id: int = Field(description="The decision's id. Decisions have their own ids.")
    label: str = Field(
        description="What this is, in plain words: whether it is in force and whether a person "
        "confirmed it."
    )
    title: str
    text: str = Field(description="What was decided.")
    reason: str = Field(description="Why.")
    decided_by: str = Field(description="The person who made the decision.")
    decided_on: date = Field(description="The day it was decided.")
    project_wide: bool = Field(
        description="True when it applies throughout the project, not to particular work. Every "
        "recall and briefing of the project includes the active ones."
    )
    status: str = Field(
        description="active, superseded (a newer decision replaced it) or withdrawn (given up "
        "with a reason). A decision that is not active is kept as a record and is not in force."
    )
    superseded_by_id: int | None = Field(
        description="The decision that replaced this one. Empty while it is active."
    )
    supersedes_ids: list[int] = Field(description="The decisions this one replaced, if any.")
    status_reason: str | None = Field(description="Why it was withdrawn. Empty otherwise.")
    confirmation: str = Field(
        description="confirmed once a named person has reviewed it, otherwise unconfirmed. It "
        "says a person looked, not that the decision is right."
    )
    confirmed_by: str | None = Field(description="The person who confirmed it.")
    confirmed_at: datetime | None
    node_ids: list[int] = Field(description="The epics, stories and tasks it affects.")
    rule_ids: list[int] = Field(description="The requirements it affects.")
    recorded_at: datetime


class DecisionPage(BaseModel):
    items: list[Decision]
    total: int = Field(description="How many decisions match in all, across pages.")
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )


def to_decision(row: dict[str, Any]) -> Decision:
    return Decision.model_validate({**row, "label": label_of("decision", row)})


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
    so that changes to it happen one at a time."""
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
    fields: tuple[str, ...],
    decided_on: date | None,
    project_wide: bool,
    replaces: int | None = None,
) -> int:
    title, text, reason, decided_by = fields
    row = await fetch_one(
        conn,
        "INSERT INTO decisions (title, text, reason, decided_by, decided_on, project_wide) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "RETURNING id, title, text, reason, decided_by, decided_on, project_wide",
        (title, text, reason, decided_by, decided_on or datetime.now(UTC).date(), project_wide),
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
        title: str,
        text: str,
        reason: str,
        decided_by: str,
        decided_on: date | None,
        project_wide: bool,
        node_ids: list[int] | None,
        rule_ids: list[int] | None,
    ) -> Decision:
        actor = clean_actor(actor)
        fields = clean_fields(title, text, reason, decided_by)
        nodes, rules = unique_ids(node_ids), unique_ids(rule_ids)
        async with self._database.transaction() as conn:
            await require_link_targets(conn, nodes, rules, "the decision")
            decision_id = await insert_decision(
                conn,
                actor=actor,
                fields=fields,
                decided_on=decided_on,
                project_wide=project_wide,
            )
            await add_links(conn, decision_id, actor, nodes, rules)
            return to_decision(await require_decision(conn, decision_id))

    async def supersede_decision(
        self,
        *,
        actor: str,
        decision_id: int,
        title: str,
        text: str,
        reason: str,
        decided_by: str,
        decided_on: date | None,
        project_wide: bool | None,
        keep_links: bool,
        node_ids: list[int] | None,
        rule_ids: list[int] | None,
        said_by: str | None,
        quote: str | None,
    ) -> Decision:
        """Record a new decision that replaces an active one, and mark the old one superseded, in
        one step."""
        actor = clean_actor(actor)
        fields = clean_fields(title, text, reason, decided_by)
        said_words = said(said_by, quote)
        extra_nodes, extra_rules = unique_ids(node_ids), unique_ids(rule_ids)
        async with self._database.transaction() as conn:
            old = await require_decision(conn, decision_id, lock=True)
            if old["status"] != "active":
                raise Refusal(
                    f"{ended_words('decision', old)}. If it was superseded and that one is "
                    f"changing too, supersede decision {old['superseded_by_id']} instead."
                    if old["status"] == "superseded"
                    else f"{ended_words('decision', old)}, so it cannot be superseded."
                )
            require_a_person_for_confirmed("decision", [old], said_words)
            await require_link_targets(conn, extra_nodes, extra_rules, "the decision")
            nodes = unique_ids(old["node_ids"] + extra_nodes) if keep_links else extra_nodes
            rules = unique_ids(old["rule_ids"] + extra_rules) if keep_links else extra_rules
            new_id = await insert_decision(
                conn,
                actor=actor,
                fields=fields,
                decided_on=decided_on,
                project_wide=old["project_wide"] if project_wide is None else project_wide,
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
                after={"status": "superseded", "superseded_by_id": new_id, **said_words},
            )
            return to_decision(await require_decision(conn, new_id))

    async def withdraw_decision(
        self,
        *,
        actor: str,
        decision_id: int,
        reason: str,
        said_by: str | None,
        quote: str | None,
    ) -> Decision:
        """Give a decision up, with the reason. Nothing is deleted: it stays as a record."""
        actor = clean_actor(actor)
        reason = required_text(
            reason, "A withdrawal needs a reason: reason cannot be empty. Say why it is given up."
        )
        said_words = said(said_by, quote)
        async with self._database.transaction() as conn:
            old = await require_decision(conn, decision_id, lock=True)
            if old["status"] != "active":
                raise Refusal(f"{ended_words('decision', old)}, so it cannot be withdrawn.")
            require_a_person_for_confirmed("decision", [old], said_words)
            await conn.execute(
                "UPDATE decisions SET status = 'withdrawn', status_reason = %s WHERE id = %s",
                (reason, decision_id),
            )
            await record_event(
                conn,
                decision_id=decision_id,
                action="decision_withdrawn",
                actor=actor,
                before={"status": "active"},
                after={"status": "withdrawn", "reason": reason, **said_words},
            )
            return to_decision(await require_decision(conn, decision_id))

    async def set_confirmation(
        self,
        *,
        actor: str,
        decision_id: int,
        confirmation: str,
        person: str,
        quote: str | None,
    ) -> Decision:
        actor = clean_actor(actor)
        check_choice("confirmation", confirmation, CONFIRMATIONS)
        async with self._database.transaction() as conn:
            row = await require_decision(conn, decision_id, lock=True)
            await apply_confirmation(
                conn,
                noun="decision",
                row=row,
                actor=actor,
                confirmation=confirmation,
                person=person,
                quote=quote,
            )
            return to_decision(await require_decision(conn, decision_id))

    async def get_decision(self, decision_id: int) -> Decision:
        async with self._database.transaction() as conn:
            return to_decision(await require_decision(conn, decision_id))

    async def list_decisions(
        self,
        *,
        status: str | None,
        project_wide: bool | None,
        confirmation: str | None,
        limit: int,
        after_id: int | None,
    ) -> DecisionPage:
        limit = page_size(limit)
        if status is not None:
            check_choice("status", status, DECISION_STATUSES)
        if confirmation is not None:
            check_choice("confirmation", confirmation, CONFIRMATIONS)
        async with self._database.transaction() as conn:
            where = (
                "(%(status)s::text IS NULL OR status = %(status)s) "
                "AND (%(project_wide)s::boolean IS NULL OR project_wide = %(project_wide)s) "
                "AND (%(confirmation)s::text IS NULL OR confirmation = %(confirmation)s)"
            )
            params = {"status": status, "project_wide": project_wide, "confirmation": confirmation}
            page, next_after_id = await page_decisions(conn, where, params, limit, after_id)
            total = await count_decisions(conn, where, params)
        return DecisionPage(items=page, total=total, next_after_id=next_after_id)

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
            if decision["status"] != "active":
                where = (
                    f"Change the links of decision {decision['superseded_by_id']} instead."
                    if decision["status"] == "superseded"
                    else "It is withdrawn, so its links stay as they were."
                )
                raise Refusal(
                    f"{ended_words('decision', decision)}, and an ended decision keeps the links "
                    f"it had. {where}"
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
            return to_decision(await require_decision(conn, decision_id))

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
    return [to_decision(row) for row in page], next_after_id


async def count_decisions(conn: AsyncConnection, where: str, params: dict[str, Any]) -> int:
    row = await fetch_one(
        conn, f"SELECT count(*) AS total FROM decision_state WHERE {where}", params
    )
    assert row is not None
    return int(row["total"])
