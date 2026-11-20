"""Pieces shared by the graph, links and claims modules: plain refusals, checks on what a caller
sent, and the small helpers for talking to the database."""

import re
from datetime import UTC, date, datetime
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BeforeValidator

from vrum.identity import current_identity
from vrum.secret_formats import find_secret_in

MAX_PAGE_SIZE = 200


class Refusal(ToolError):
    """A request that will not be carried out. The message says what was wrong and what to do."""


def refuse_secrets(value: object) -> None:
    """Refuse text that holds a secret in a well-known format, anywhere inside `value` (a text, or
    a dictionary or list of them). The refusal names the field and the kind of secret, never the
    secret itself, so it can be shown and logged."""
    found = find_secret_in(value)
    if found is not None:
        path, kind = found
        field = f"The {path} field" if path else "The text"
        raise Refusal(
            f"{field} contains what looks like {kind}, so it was not stored, was not sent to "
            "any other service, and nothing was changed. Take the secret out and try again."
        )


def check_choice(label: str, value: str, allowed: tuple[str, ...], extra: str = "") -> str:
    if value not in allowed:
        raise Refusal(f"{label} must be one of {', '.join(allowed)}, but it is {value!r}{extra}.")
    return value


def required_text(value: str, refusal: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise Refusal(refusal)
    return cleaned


def unique_ids(ids: list[int] | None) -> list[int]:
    return list(dict.fromkeys(ids or []))


# An epic, story or task is named by its kind and its number within that kind in its project:
# `epic:3`, `story:12`, `task:40`. There is no other identity for them (rules and notes
# keep their own numbers; links and events keep theirs).
NODE_KINDS = ("epic", "story", "task")
_NODE_REF = re.compile(r"(epic|story|task):([1-9][0-9]{0,17})")


def node_ref(kind: str, number: int) -> str:
    return f"{kind}:{number}"


def split_node_ref(ref: str) -> tuple[str, int]:
    """The kind and number of a reference that is already known to be valid."""
    kind, _, number = ref.partition(":")
    return kind, int(number)


def node_order(ref: str) -> tuple[int, int]:
    """Where a reference sorts: epics, then stories, then tasks, each by number."""
    kind, number = split_node_ref(ref)
    return NODE_KINDS.index(kind), number


def kind_rank(kind: str) -> int:
    """The `kind_rank` the database gives a kind: epics 1, stories 2, tasks 3."""
    return NODE_KINDS.index(kind) + 1


def is_node_ref(value: str) -> bool:
    """Whether a text is written as the reference of an epic, story or task."""
    return _NODE_REF.fullmatch(value.strip().lower()) is not None


def node_words(ref: str) -> str:
    """A reference in words, such as 'story 12', for a sentence."""
    kind, _, number = ref.partition(":")
    return f"{kind} {number}"


def missing_node(ref: str) -> Refusal:
    return Refusal(
        f"There is no {node_words(ref)} in this project. Use list_nodes to find the one you want."
    )


def _as_text(value: object) -> object:
    """A number sent where a reference belongs is read as the text it was written as, so that
    `parse_node_ref` can say what is wrong with it instead of a type error."""
    return str(value) if isinstance(value, int | float) else value


# The type of every argument or field that holds a node reference as the caller wrote it.
Ref = Annotated[str, BeforeValidator(_as_text)]


def parse_node_ref(value: object, what: str = "node") -> str:
    """A node reference as the caller wrote it, in its one written form (lower case, no spaces),
    or a plain refusal. `what` names the argument in the refusal."""
    text = value.strip().lower() if isinstance(value, str) else ""
    if not _NODE_REF.fullmatch(text):
        shown = repr(value) if len(repr(value)) <= 60 else repr(str(value)[:50]) + " (cut short)"
        raise Refusal(
            f"{what} must name an epic, story or task by its kind and number, such as story:12 "
            f"(epic, story or task, a colon, then the number), but it is {shown}."
        )
    return text


def parse_node_refs(values: list[str] | None, what: str = "nodes") -> list[str]:
    """Each reference checked, repeats dropped, in the order given."""
    return list(dict.fromkeys(parse_node_ref(value, what) for value in values or []))


def page_size(limit: int) -> int:
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise Refusal(f"limit must be a whole number from 1 to {MAX_PAGE_SIZE}, but it is {limit}.")
    return limit


def split_page(
    rows: list[dict[str, Any]], limit: int, key: str = "id"
) -> tuple[list[dict[str, Any]], Any]:
    """Cut the extra row that was fetched to learn whether another page exists, and give the `key`
    of the last row kept (its id, or its ref for a page of nodes) to continue after, if there is
    one."""
    page = rows[:limit]
    return page, page[-1][key] if len(rows) > limit else None


def stale_version(
    label: str, reader: str, item_id: int | str, current: int, expected: int
) -> Refusal:
    """`item_id` is a number for a rule or note (named with `label`), or the reference
    of an epic, story or task, which names itself."""
    name = item_id if isinstance(item_id, str) else f"{label} {item_id}"
    return Refusal(
        f"{name} is at version {current}, but you sent version {expected}. "
        f"Someone else changed it after you read it. Read it again with {reader}, check that your "
        f"change still makes sense, then repeat it with expected_version {current}."
    )


def when(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def parse_day(label: str, value: str | None) -> date | None:
    """A day written as YYYY-MM-DD, or nothing. Anything else is refused in plain words, because
    an agent is as likely to write 'next tuesday' or a time as a proper date."""
    if value is None:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise Refusal(
        f"{label} must be a day written as YYYY-MM-DD, such as 2026-11-30 (no time of day), "
        f"but it is {value!r}."
    )


async def fetch_one(conn: AsyncConnection, sql: str, params: Any = None) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(sql, params)
        return await cursor.fetchone()


async def fetch_all(conn: AsyncConnection, sql: str, params: Any = None) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(sql, params)
        return await cursor.fetchall()


def snapshot(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """The named fields of a row, ready to be stored in an event (dates and times become text)."""
    return {
        field: row[field].isoformat() if isinstance(row[field], date) else row[field]
        for field in fields
    }


async def require_link_targets(
    conn: AsyncConnection, nodes: list[str], rule_ids: list[int], subject: str
) -> None:
    """Refuse in plain words unless every node (by reference) and rule exists, so that `subject`
    (such as "the note") can be linked to them."""
    if nodes:
        found = {
            row["ref"]
            for row in await fetch_all(conn, "SELECT ref FROM nodes WHERE ref = ANY(%s)", (nodes,))
        }
        missing = [node_words(ref) for ref in nodes if ref not in found]
        if missing:
            raise Refusal(
                f"There is no {' or '.join(missing)} in this project, so {subject} cannot be "
                f"linked to {'it' if len(missing) == 1 else 'them'}. Use list_nodes to find the "
                "one you want."
            )
    if rule_ids:
        found_rules = {
            row["id"]
            for row in await fetch_all(conn, "SELECT id FROM rules WHERE id = ANY(%s)", (rule_ids,))
        }
        missing_rules = [str(item) for item in rule_ids if item not in found_rules]
        if missing_rules:
            raise Refusal(
                f"There is no acceptance criterion with id {', '.join(missing_rules)}, so "
                f"{subject} cannot be linked to it. Use list_rules to find the id you want."
            )


async def save_changes(
    conn: AsyncConnection,
    table: str,
    row_id: int | str,
    changes: dict[str, Any],
    columns: str,
    key: str = "id",
) -> dict[str, Any]:
    """Write the changes to one row (found by its `key` column: `id`, or `ref` for a node) and
    raise its version by one. The caller holds the row's lock and has checked the version it was
    read at. `table`, `key` and the keys of `changes` come from the code, never from a caller."""
    assignments = ", ".join(f"{field} = %({field})s" for field in changes)
    row = await fetch_one(
        conn,
        f"UPDATE {table} SET {assignments}, version = version + 1 "
        f"WHERE {key} = %(row_key)s RETURNING {columns}",
        {**changes, "row_key": row_id},
    )
    assert row is not None
    return row


async def record_event(
    conn: AsyncConnection,
    *,
    action: str,
    actor: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    node: str | None = None,
    other_node: str | None = None,
    rule_id: int | None = None,
    note_id: int | None = None,
    other_note_id: int | None = None,
) -> None:
    """Append to the event log. It shows in the history of every node, rule and note it names:
    `other_node` is the second node of a link, or the story a rule has just left, and
    `other_note_id` is the note a note has just been linked to or unlinked from.

    `actor` is the name of whoever is making the change (the server passes the name of the call's
    identity, see identity.py), and the kind, person or agent, is read from that same identity.

    Every write records an event with the text it wrote, so this is where text with a secret in a
    well-known format is refused whatever path it came by. The refusal rolls the whole write
    back."""
    refuse_secrets({"actor": actor, **after})
    await conn.execute(
        "INSERT INTO events (node, other_node, rule_id, note_id, other_note_id, action, "
        "actor, actor_kind, before, after) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            node,
            other_node,
            rule_id,
            note_id,
            other_note_id,
            action,
            actor,
            current_identity().kind,
            None if before is None else Jsonb(before),
            Jsonb(after),
        ),
    )
