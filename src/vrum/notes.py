"""Notes. This module owns recording them, what they concern, replacing and withdrawing them, and
confirming them.

A note is what is known or decided, with why: a statement (its text, whose first line is its
headline: it has no title) and the reason it is so, where it came from or why it was chosen. It is a
record and is never edited. A change is a newer note that replaces it, or a withdrawal. A note that
somebody decided names who and when (`decided_by`, `decided_on`): that is one more detail of the
note, not another kind. One that applies throughout its project is `project_wide`: included in
every recall and briefing of that project.

A note has one status: unconfirmed or confirmed while it is in force, then superseded or withdrawn.
The server never decides by itself that something is contradicted: a caller names what is
replaced. Whether a person confirmed a note is its status (see records.py), and recording a note
never confirms it."""

from datetime import UTC, date, datetime
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import (
    Refusal,
    check_choice,
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
from vrum.graph import EVENT_COLUMNS, Event, EventPage, require_node
from vrum.identity import current_identity
from vrum.records import (
    CONFIRMATIONS,
    IN_FORCE,
    NOTE_STATUSES,
    apply_confirmation,
    ended_words,
    headline_of,
    require_a_person_for_confirmed,
    said,
)
from vrum.rules import require_rule

NOTE_COLUMNS = (
    "id, text, why, decided, decided_by, decided_on, project_wide, recorded_by, "
    "recorded_by_kind, recorded_at, status, superseded_by_id, supersedes_ids, status_reason, "
    "confirmed_by, confirmed_at, nodes, rule_ids, note_ids"
)
_RECORDED_FIELDS = ("text", "why", "decided_by", "decided_on", "project_wide")
_PAGE_COLUMNS = ", ".join(f"n.{column.strip()}" for column in NOTE_COLUMNS.split(","))

# Where a note's links to each kind of thing are kept (the table and its column), and which column
# of an event names the other end. Fixed here, never from a caller.
_LINK_TABLES = {
    "node": ("note_nodes", "node", "node"),
    "rule": ("note_rules", "rule_id", "rule_id"),
    "note": ("note_notes", "about_id", "other_note_id"),
}


class Note(BaseModel):
    id: int = Field(description="The note's id. Notes have their own ids.")
    headline: str = Field(
        description="What lists show for it: the first line of its text that is not blank, cut to "
        "120 characters. A note has no title."
    )
    text: str = Field(description="What is known or decided.")
    why: str = Field(
        description="Why it is so or why it was chosen: where it came from, who said it, what was "
        "observed, or the reason."
    )
    decided: bool = Field(
        description="True when a person decided it: decided_by and decided_on say who and when."
    )
    decided_by: str | None = Field(
        description="The person who decided it. Empty for a note nobody decided. This is not the "
        "agent or person who recorded it: that is recorded_by."
    )
    decided_on: date | None = Field(description="The day it was decided. Empty with decided_by.")
    project_wide: bool = Field(
        description="True when it applies throughout the project, not to particular work. Every "
        "recall and briefing of the project includes the ones in force."
    )
    status: str = Field(
        description="unconfirmed or confirmed (in force; confirmed means a named person has "
        "reviewed it, not that the note is right), superseded (a newer note replaced it) or "
        "withdrawn (given up or found to be wrong, with a reason). A superseded or withdrawn "
        "note is kept as a record and is not in force. It is never edited."
    )
    superseded_by_id: int | None = Field(
        description="The note that replaced this one. Empty unless it was superseded."
    )
    supersedes_ids: list[int] = Field(description="The notes this one replaced, if any.")
    status_reason: str | None = Field(
        description="Why it was withdrawn. Empty otherwise (a note that was replaced says why in "
        "the note that replaced it)."
    )
    confirmed_by: str | None = Field(
        description="The person who confirmed it. Kept after the note ends, as the record of who "
        "had confirmed it."
    )
    confirmed_at: datetime | None
    recorded_by: str = Field(description="The name of the person or agent that recorded it.")
    recorded_by_kind: str = Field(
        description="person or agent: what recorded it (unknown for a note loaded from an export "
        "made before names were verified)."
    )
    recorded_at: datetime
    nodes: list[str] = Field(
        description="The epics, stories and tasks it concerns, as refs such as story:12."
    )
    rule_ids: list[int] = Field(description="The acceptance criteria it concerns.")
    note_ids: list[int] = Field(description="The notes it concerns.")


class NotePage(BaseModel):
    items: list[Note]
    total: int = Field(
        description="How many notes match in all, across pages, so a walk through every page can "
        "be checked against it."
    )
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )


class NoteChange(BaseModel):
    replacement: Note | None = Field(
        description="The newer note that replaced the others. Empty when notes were withdrawn."
    )
    superseded_ids: list[int] = Field(description="The notes now marked superseded.")
    withdrawn_ids: list[int] = Field(description="The notes now marked withdrawn.")


def to_note(row: dict[str, Any]) -> Note:
    return Note.model_validate({**row, "headline": headline_of(row["text"])})


async def require_note(
    conn: AsyncConnection, note_id: int, *, lock: bool = False
) -> dict[str, Any]:
    """The note as read, or a plain refusal. `lock` holds its row until the transaction ends, so
    that changes to it happen one at a time."""
    if lock:
        await conn.execute("SELECT 1 FROM notes WHERE id = %s FOR UPDATE", (note_id,))
    row = await fetch_one(conn, f"SELECT {NOTE_COLUMNS} FROM note_state WHERE id = %s", (note_id,))
    if row is None:
        raise Refusal(
            f"There is no note with id {note_id}. Use list_notes to find the id you want."
        )
    return row


async def _lock(conn: AsyncConnection, note_ids: list[int]) -> None:
    """Hold these rows until the transaction ends so that they change one call at a time. Always
    taken in id order, so two calls that overlap wait for each other and never on each other."""
    for note_id in sorted(note_ids):
        await conn.execute("SELECT 1 FROM notes WHERE id = %s FOR UPDATE", (note_id,))


async def _require_found(conn: AsyncConnection, note_ids: list[int]) -> list[dict[str, Any]]:
    rows = await fetch_all(
        conn, f"SELECT {NOTE_COLUMNS} FROM note_state WHERE id = ANY(%s)", (note_ids,)
    )
    found = {row["id"] for row in rows}
    missing = [str(item) for item in note_ids if item not in found]
    if missing:
        raise Refusal(
            f"There is no note with id {', '.join(missing)}. Use list_notes to find the id you "
            "want. Nothing was changed."
        )
    return rows


async def _require_in_force(
    conn: AsyncConnection, note_ids: list[int], verb: str
) -> list[dict[str, Any]]:
    """The notes as read now that they are locked, refused in plain words unless each is in
    force."""
    rows = {row["id"]: row for row in await _require_found(conn, note_ids)}
    for note_id in note_ids:
        row = rows[note_id]
        if row["status"] not in IN_FORCE:
            raise Refusal(f"{ended_words(row)}, so it cannot be {verb}. Nothing was changed.")
    return [rows[note_id] for note_id in note_ids]


def _clean_text(text: str, why: str, *, new: bool = False) -> tuple[str, str]:
    """The text and the reason, both required. `new` words the refusal for the replacement."""
    subject = "The new note" if new else "A note"
    return (
        required_text(
            text, f"{subject} needs its text: text cannot be empty. Say what is known or decided."
        ),
        required_text(
            why,
            f"{subject} needs its reason: why cannot be empty. Say why it is so or why it was "
            "chosen: where it came from, who said it, what was observed, or the reason.",
        ),
    )


def _decided(decided_by: str | None, decided_on: date | None) -> tuple[str | None, date | None]:
    """Who decided it and when, or neither. A day with nobody to have decided it is refused, and
    a person with no day decided it today (UTC, as the database counts it)."""
    who = (decided_by or "").strip() or None
    if who is None:
        if decided_on is not None:
            raise Refusal(
                "decided_on needs decided_by: say which person decided it, or leave both out for "
                "a note that nobody decided."
            )
        return None, None
    return who, decided_on or datetime.now(UTC).date()


async def _require_notes(conn: AsyncConnection, note_ids: list[int]) -> None:
    if note_ids:
        await _require_found(conn, note_ids)


async def _insert_note(
    conn: AsyncConnection,
    *,
    actor: str,
    text: str,
    why: str,
    decided_by: str | None,
    decided_on: date | None,
    project_wide: bool,
    supersedes: list[int],
) -> int:
    row = await fetch_one(
        conn,
        "INSERT INTO notes (text, why, decided_by, decided_on, project_wide, recorded_by, "
        "recorded_by_kind) VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id, text, why, decided_by, decided_on, project_wide",
        (text, why, decided_by, decided_on, project_wide, actor, current_identity().kind),
    )
    assert row is not None
    after = snapshot(row, _RECORDED_FIELDS)
    if supersedes:
        after["supersedes_ids"] = supersedes
    await record_event(
        conn, note_id=row["id"], action="note_recorded", actor=actor, before=None, after=after
    )
    return int(row["id"])


def _ends(
    nodes: list[str], rule_ids: list[int], note_ids: list[int]
) -> list[tuple[str, list[Any]]]:
    """What a note is linked to, by kind: nodes by their refs, the others by their ids."""
    return [("node", nodes), ("rule", rule_ids), ("note", note_ids)]


async def _add_links(
    conn: AsyncConnection,
    note_id: int,
    actor: str,
    nodes: list[str],
    rule_ids: list[int],
    note_ids: list[int],
) -> None:
    for what, ends in _ends(nodes, rule_ids, note_ids):
        table, column, named = _LINK_TABLES[what]
        for item in ends:
            await conn.execute(
                f"INSERT INTO {table} (note_id, {column}) VALUES (%s, %s)", (note_id, item)
            )
            await record_event(
                conn,
                note_id=note_id,
                action="note_linked",
                actor=actor,
                before=None,
                after={"note_id": note_id},
                **{named: item},
            )


async def _remove_links(
    conn: AsyncConnection,
    note_id: int,
    actor: str,
    nodes: list[str],
    rule_ids: list[int],
    note_ids: list[int],
) -> None:
    for what, ends in _ends(nodes, rule_ids, note_ids):
        table, column, named = _LINK_TABLES[what]
        for item in ends:
            await conn.execute(
                f"DELETE FROM {table} WHERE note_id = %s AND {column} = %s", (note_id, item)
            )
            await record_event(
                conn,
                note_id=note_id,
                action="note_unlinked",
                actor=actor,
                before={"note_id": note_id},
                after={},
                **{named: item},
            )


async def page_notes(
    conn: AsyncConnection,
    where: str,
    params: dict[str, Any],
    limit: int,
    after_id: int | None,
    *,
    prefix: str = "",
    decided_first: bool = False,
) -> tuple[list[Note], int | None]:
    """One page of the notes that match `where` (a condition on `note_state n`), by id. With
    `decided_first` the notes somebody decided come before the others, each group by id, so what
    binds is read first: the page then continues after the note `after_id` in that order."""
    order = "(NOT n.decided), n.id" if decided_first else "n.id"
    if decided_first:
        cursor = (
            await fetch_one(conn, "SELECT decided FROM note_state WHERE id = %s", (after_id,))
            if after_id is not None
            else None
        )
        after_rank = 1 if cursor is not None and not cursor["decided"] else 0
        after = "((NOT n.decided)::int, n.id) > (%(after_rank)s, %(after)s)"
        params = {**params, "after_rank": after_rank}
    else:
        after = "n.id > %(after)s"
    rows = await fetch_all(
        conn,
        f"{prefix}SELECT {_PAGE_COLUMNS} "
        f"FROM note_state n WHERE {where} AND {after} ORDER BY {order} LIMIT %(fetch)s",
        {**params, "after": after_id or 0, "fetch": limit + 1},
    )
    page, next_after_id = split_page(rows, limit)
    return [to_note(row) for row in page], next_after_id


async def count_notes(
    conn: AsyncConnection, where: str, params: dict[str, Any], *, prefix: str = ""
) -> int:
    row = await fetch_one(
        conn, f"{prefix}SELECT count(*) AS total FROM note_state n WHERE {where}", params
    )
    assert row is not None
    return int(row["total"])


class Notes:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add_note(
        self,
        *,
        actor: str,
        text: str,
        why: str,
        decided_by: str | None,
        decided_on: date | None,
        project_wide: bool,
        nodes: list[str] | None,
        rule_ids: list[int] | None,
        note_ids: list[int] | None,
    ) -> Note:
        text, why = _clean_text(text, why)
        who, day = _decided(decided_by, decided_on)
        concerned, rules, notes = parse_node_refs(nodes), unique_ids(rule_ids), unique_ids(note_ids)
        async with self._database.transaction() as conn:
            await require_link_targets(conn, concerned, rules, "the note")
            await _require_notes(conn, notes)
            note_id = await _insert_note(
                conn,
                actor=actor,
                text=text,
                why=why,
                decided_by=who,
                decided_on=day,
                project_wide=project_wide,
                supersedes=[],
            )
            await _add_links(conn, note_id, actor, concerned, rules, notes)
            return to_note(await require_note(conn, note_id))

    async def supersede_note(
        self,
        *,
        actor: str,
        replaces: list[int],
        text: str,
        why: str,
        decided_by: str | None,
        decided_on: date | None,
        project_wide: bool | None,
        keep_links: bool,
        nodes: list[str] | None,
        rule_ids: list[int] | None,
        note_ids: list[int] | None,
        said_by: str | None,
        quote: str | None,
    ) -> NoteChange:
        """Record a newer note that replaces one or more notes in force, and mark each of them
        superseded, in one step."""
        old_ids = unique_ids(replaces)
        if not old_ids:
            raise Refusal("Say what is being replaced: replaces needs at least one id.")
        text, why = _clean_text(text, why, new=True)
        who, day = _decided(decided_by, decided_on)
        said_words = said(said_by, quote)
        extra_nodes, extra_rules = parse_node_refs(nodes), unique_ids(rule_ids)
        extra_notes = unique_ids(note_ids)
        async with self._database.transaction() as conn:
            await _require_found(conn, old_ids)
            await _lock(conn, old_ids)
            olds = await _require_in_force(conn, old_ids, "replaced")
            require_a_person_for_confirmed(olds, said_words)
            await require_link_targets(conn, extra_nodes, extra_rules, "the new note")
            await _require_notes(conn, extra_notes)
            carried_nodes = [item for row in olds for item in row["nodes"]] if keep_links else []
            carried_rules = [item for row in olds for item in row["rule_ids"]] if keep_links else []
            carried_notes = [item for row in olds for item in row["note_ids"]] if keep_links else []
            new_id = await _insert_note(
                conn,
                actor=actor,
                text=text,
                why=why,
                decided_by=who,
                decided_on=day,
                project_wide=any(row["project_wide"] for row in olds)
                if project_wide is None
                else project_wide,
                supersedes=old_ids,
            )
            await _add_links(
                conn,
                new_id,
                actor,
                list(dict.fromkeys(carried_nodes + extra_nodes)),
                unique_ids(carried_rules + extra_rules),
                unique_ids(carried_notes + extra_notes),
            )
            await conn.execute(
                "UPDATE notes SET status = 'superseded', superseded_by_id = %s WHERE id = ANY(%s)",
                (new_id, old_ids),
            )
            for old in olds:
                await record_event(
                    conn,
                    note_id=old["id"],
                    action="note_superseded",
                    actor=actor,
                    before={"status": old["status"], "superseded_by_id": None},
                    after={"status": "superseded", "superseded_by_id": new_id, **said_words},
                )
            return NoteChange(
                replacement=to_note(await require_note(conn, new_id)),
                superseded_ids=old_ids,
                withdrawn_ids=[],
            )

    async def withdraw_note(
        self,
        *,
        actor: str,
        note_ids: list[int],
        reason: str,
        said_by: str | None,
        quote: str | None,
    ) -> NoteChange:
        """Give one or more notes in force up, in one step. Nothing is deleted."""
        ids = unique_ids(note_ids)
        if not ids:
            raise Refusal("Say what is being withdrawn: note_ids needs at least one id.")
        reason = required_text(
            reason, "A withdrawal needs a reason: reason cannot be empty. Say why it is given up."
        )
        said_words = said(said_by, quote)
        async with self._database.transaction() as conn:
            await _require_found(conn, ids)
            await _lock(conn, ids)
            rows = await _require_in_force(conn, ids, "withdrawn")
            require_a_person_for_confirmed(rows, said_words)
            await conn.execute(
                "UPDATE notes SET status = 'withdrawn', status_reason = %s WHERE id = ANY(%s)",
                (reason, ids),
            )
            for row in rows:
                await record_event(
                    conn,
                    note_id=row["id"],
                    action="note_withdrawn",
                    actor=actor,
                    before={"status": row["status"]},
                    after={"status": "withdrawn", "reason": reason, **said_words},
                )
            return NoteChange(replacement=None, superseded_ids=[], withdrawn_ids=ids)

    async def set_confirmation(
        self,
        *,
        actor: str,
        note_id: int,
        confirmation: str,
        person: str,
        quote: str | None,
    ) -> Note:
        check_choice("confirmation", confirmation, CONFIRMATIONS)
        async with self._database.transaction() as conn:
            row = await require_note(conn, note_id, lock=True)
            await apply_confirmation(
                conn,
                row=row,
                actor=actor,
                confirmation=confirmation,
                person=person,
                quote=quote,
            )
            return to_note(await require_note(conn, note_id))

    async def link_note(
        self,
        *,
        actor: str,
        note_id: int,
        nodes: list[str] | None,
        rule_ids: list[int] | None,
        note_ids: list[int] | None,
    ) -> Note:
        return await self._change_links(
            actor=actor,
            note_id=note_id,
            nodes=nodes,
            rule_ids=rule_ids,
            note_ids=note_ids,
            add=True,
        )

    async def unlink_note(
        self,
        *,
        actor: str,
        note_id: int,
        nodes: list[str] | None,
        rule_ids: list[int] | None,
        note_ids: list[int] | None,
    ) -> Note:
        return await self._change_links(
            actor=actor,
            note_id=note_id,
            nodes=nodes,
            rule_ids=rule_ids,
            note_ids=note_ids,
            add=False,
        )

    async def _change_links(
        self,
        *,
        actor: str,
        note_id: int,
        nodes: list[str] | None,
        rule_ids: list[int] | None,
        note_ids: list[int] | None,
        add: bool,
    ) -> Note:
        concerned, rules, notes = parse_node_refs(nodes), unique_ids(rule_ids), unique_ids(note_ids)
        if not concerned and not rules and not notes:
            raise Refusal(
                "Nothing to link: give at least one ref in nodes (such as story:12), or one id in "
                "rule_ids or note_ids."
            )
        if note_id in notes:
            raise Refusal(f"Note {note_id} cannot be about itself. Nothing was changed.")
        async with self._database.transaction() as conn:
            row = await require_note(conn, note_id, lock=True)
            if row["status"] not in IN_FORCE:
                where = (
                    f"Change the links of note {row['superseded_by_id']}, which replaced it, "
                    "instead."
                    if row["status"] == "superseded"
                    else "It is withdrawn, so its links stay as they were."
                )
                raise Refusal(
                    f"{ended_words(row)}, and an ended note keeps the links it had. {where}"
                )
            if add:
                await require_link_targets(conn, concerned, rules, f"note {note_id}")
                await _require_notes(conn, notes)
            for what, ends, current in (
                ("node", concerned, row["nodes"]),
                ("rule", rules, row["rule_ids"]),
                ("note", notes, row["note_ids"]),
            ):
                # A node's ref already says what it is; the others are named by their ids.
                wrong = [
                    str(item) if what == "node" else f"{what} {item}"
                    for item in ends
                    if (item in current) == add
                ]
                if wrong:
                    raise Refusal(
                        f"Note {note_id} is {'already' if add else 'not'} linked to "
                        f"{', '.join(wrong)}, so nothing was changed."
                    )
            if add:
                await _add_links(conn, note_id, actor, concerned, rules, notes)
            else:
                await _remove_links(conn, note_id, actor, concerned, rules, notes)
            return to_note(await require_note(conn, note_id))

    async def get_note(self, note_id: int) -> Note:
        async with self._database.transaction() as conn:
            return to_note(await require_note(conn, note_id))

    async def list_notes(
        self,
        *,
        status: str | None,
        project_wide: bool | None,
        decided: bool | None,
        node: str | None,
        rule_id: int | None,
        note_id: int | None,
        within_node: str | None,
        limit: int,
        after_id: int | None,
    ) -> NotePage:
        limit = page_size(limit)
        if status is not None:
            check_choice("status", status, NOTE_STATUSES + ("in_force",))
        if node is not None:
            node = parse_node_ref(node, "node")
        if within_node is not None:
            within_node = parse_node_ref(within_node, "within_node")
        conditions: list[str] = []
        params: dict[str, Any] = {}
        if status == "in_force":
            conditions.append("n.status = ANY(%(in_force)s)")
            params["in_force"] = list(IN_FORCE)
        for column, wanted in (
            ("status", None if status == "in_force" else status),
            ("project_wide", project_wide),
            ("decided", decided),
        ):
            if wanted is not None:
                conditions.append(f"n.{column} = %({column})s")
                params[column] = wanted
        prefix = ""
        async with self._database.transaction() as conn:
            for ref in (node, within_node):
                if ref is not None:
                    await require_node(conn, ref)
            if rule_id is not None:
                await require_rule(conn, rule_id)
            if note_id is not None:
                await require_note(conn, note_id)
            for column, table, linked in (
                ("node", "note_nodes", node),
                ("rule_id", "note_rules", rule_id),
                ("about_id", "note_notes", note_id),
            ):
                if linked is not None:
                    conditions.append(
                        f"EXISTS (SELECT 1 FROM {table} l WHERE l.note_id = n.id "
                        f"AND l.{column} = %({column})s)"
                    )
                    params[column] = linked
            if within_node is not None:
                # The item, everything under it, and the rules placed on any of those.
                prefix = (
                    "WITH RECURSIVE area(ref) AS (SELECT ref FROM nodes WHERE ref = %(within)s "
                    "UNION ALL SELECT k.ref FROM nodes k JOIN area a ON k.parent = a.ref) "
                )
                conditions.append(
                    "(EXISTS (SELECT 1 FROM note_nodes l WHERE l.note_id = n.id "
                    "AND l.node IN (SELECT ref FROM area)) "
                    "OR EXISTS (SELECT 1 FROM note_rules l JOIN rules r ON r.id = l.rule_id "
                    "WHERE l.note_id = n.id AND r.node IN (SELECT ref FROM area)))"
                )
                params["within"] = within_node
            where = " AND ".join(conditions) or "true"
            total = await count_notes(conn, where, params, prefix=prefix)
            page, next_after_id = await page_notes(
                conn, where, params, limit, after_id, prefix=prefix
            )
        return NotePage(items=page, total=total, next_after_id=next_after_id)

    async def note_history(self, *, note_id: int, limit: int, after_id: int | None) -> EventPage:
        limit = page_size(limit)
        async with self._database.transaction() as conn:
            await require_note(conn, note_id)
            rows = await fetch_all(
                conn,
                f"SELECT {EVENT_COLUMNS} FROM events "
                "WHERE (note_id = %(note)s OR other_note_id = %(note)s) AND id > %(after)s "
                "ORDER BY id LIMIT %(fetch)s",
                {"note": note_id, "after": after_id or 0, "fetch": limit + 1},
            )
        page, next_after_id = split_page(rows, limit)
        return EventPage(
            items=[Event.model_validate(row) for row in page], next_after_id=next_after_id
        )
