"""The definition of done: one text for the whole project that says what every story or task must
satisfy, on top of its own acceptance criteria. This module owns reading it and changing it.

It is plain text or Markdown and empty until a person sets it. Only a person changes it (an agent
is told, in its briefing, what it says). A project that never set one has no row and reads as an
empty text at version 1; the first change makes the row, and every change is an event that holds
the text before and after, so the history says who changed it and what it said.

Its list lines are a checklist: each is an item that a story or task can have ticked, by an agent
or a person (see checks.py). Every other line is context and cannot be ticked. An item's key is
the first 8 hex digits of a SHA-256 of its words, so it stays the same while the words do. A line
whose words start with `[person]` is a check only a person can judge: the tag is not part of its
words (so adding or taking it away keeps the key and every tick), and an agent may not tick it. The
items are kept in the table `definition_items`, rebuilt in the same step as the text."""

import hashlib
import re
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import Refusal, fetch_all, fetch_one, record_event, stale_version
from vrum.db import Database
from vrum.identity import current_identity
from vrum.records import require_person

DEFINITION_ACTION = "definition_of_done_changed"
_COLUMNS = "body, version, updated_at, updated_by, updated_by_kind"

# A list line: a dash or an asterisk, then its words, at any depth; `- [ ]` and `- [x]` too.
_LIST_LINE = re.compile(r"^[ \t]*[-*][ \t]+(\S.*)$")
_CHECKBOX = re.compile(r"^\[[ xX]\](?:\s+|$)")
_PERSON_TAG = re.compile(r"^\[person\](?:\s+|$)", re.IGNORECASE)
# Three or more dashes or asterisks with nothing else is a line across the page, not a list line.
_RULER = re.compile(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_FENCE = re.compile(r"^[ \t]*(?:```|~~~)")


class DefinitionItem(BaseModel):
    item: str = Field(
        description="The item's key: 8 hex characters that stay the same while its words do. "
        "Send it to set_check."
    )
    text: str = Field(description="The words of the list line, without its dash or checkbox.")
    person_only: bool = Field(
        default=False,
        description="True when the line is marked [person]: only a person can judge it, so an "
        "agent may not tick it.",
    )
    ticks: int = Field(
        default=0,
        description="On how many stories and tasks it is ticked (only when you read the "
        "definition of done itself).",
    )


def _words(text: str) -> str:
    return " ".join(_CHECKBOX.sub("", text.strip()).split())


def item_words(text: str) -> str:
    """The words of a list line as an item has them: trimmed, with a leading [ ] or [x] and then a
    [person] tag taken off and runs of white space made one space."""
    return " ".join(_PERSON_TAG.sub("", _words(text)).split())


def is_person_only(text: str) -> bool:
    """True when a list line carries the [person] tag, after its checkbox if it has one."""
    return _PERSON_TAG.match(_words(text)) is not None


def item_key(words: str) -> str:
    return hashlib.sha256(words.encode("utf-8")).hexdigest()[:8]


def definition_items(body: str) -> list[DefinitionItem]:
    """The checklist a text holds, in the order it is written. A list line inside a fenced code
    block, a line across the page, and a line with no words are not items, and two lines with the
    same words are one item, which only a person may tick if any of them is marked [person]."""
    found: dict[str, DefinitionItem] = {}
    fenced = False
    for line in body.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced or _RULER.match(line):
            continue
        match = _LIST_LINE.match(line)
        if match is None:
            continue
        words = item_words(match.group(1))
        if not words:
            continue
        key = item_key(words)
        person = is_person_only(match.group(1))
        if key in found:
            found[key].person_only = found[key].person_only or person
        else:
            found[key] = DefinitionItem(item=key, text=words, person_only=person)
    return list(found.values())


class DefinitionOfDone(BaseModel):
    body: str = Field(
        description="Plain text or Markdown: what every story or task must satisfy to be done, in "
        "addition to its own acceptance criteria. Empty until a person sets it. Its list lines "
        "(starting with a dash or an asterisk) are its checks."
    )
    version: int = Field(
        description="Starts at 1 (nothing set yet) and rises by one with every change. Send it "
        "back as expected_version when you change it."
    )
    updated_at: datetime | None = Field(
        description="When it was last changed. Empty while nothing has been set."
    )
    updated_by: str | None = Field(
        description="The name of the person who last changed it. Empty while nothing has been set."
    )
    updated_by_kind: str | None = Field(
        description="Always person when set: only a person changes it."
    )
    items: list[DefinitionItem] = Field(
        description="Its checks, one for each list line, in the order written: the key to send "
        "to set_check, the words, and whether only a person may tick it (person_only)."
    )


def _read(row: dict[str, Any] | None) -> DefinitionOfDone:
    if row is None:
        return DefinitionOfDone(
            body="", version=1, updated_at=None, updated_by=None, updated_by_kind=None, items=[]
        )
    return DefinitionOfDone.model_validate({**row, "items": definition_items(row["body"])})


async def read_definition(conn: AsyncConnection, *, lock: bool = False) -> DefinitionOfDone:
    """The project's definition of done as it stands, or the empty one when it was never set."""
    return _read(
        await fetch_one(
            conn, f"SELECT {_COLUMNS} FROM definition_of_done" + (" FOR UPDATE" if lock else "")
        )
    )


async def _with_ticks(conn: AsyncConnection, definition: DefinitionOfDone) -> DefinitionOfDone:
    """The definition with, on each item, how many stories and tasks have it ticked."""
    counts = {
        row["item_key"]: int(row["ticks"])
        for row in await fetch_all(
            conn, "SELECT item_key, count(*) AS ticks FROM definition_checks GROUP BY item_key"
        )
    }
    return definition.model_copy(
        update={
            "items": [
                item.model_copy(update={"ticks": counts.get(item.item, 0)})
                for item in definition.items
            ]
        }
    )


async def _rebuild_items(conn: AsyncConnection, items: list[DefinitionItem]) -> None:
    """Make the table of items say what the text says. A tick of an item that is no longer there
    is left where it is: it is neither counted nor shown, and the history keeps it."""
    await conn.execute("DELETE FROM definition_items")
    if items:
        async with conn.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO definition_items (item_key, position, text, person_only) "
                "VALUES (%s, %s, %s, %s)",
                [
                    (item.item, position, item.text, item.person_only)
                    for position, item in enumerate(items, 1)
                ],
            )


class DefinitionOfDoneBook:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self) -> DefinitionOfDone:
        async with self._database.transaction() as conn:
            return await _with_ticks(conn, await read_definition(conn))

    async def set(self, *, body: str, expected_version: int) -> DefinitionOfDone:
        """Replace the text. Only a person may; the version sent must be the one that is stored,
        so that nobody overwrites a change they did not see."""
        require_person(
            "the Definition of done page (Settings, Definition of done)",
            "change the definition of done",
        )
        identity = current_identity()
        async with self._database.transaction() as conn:
            current = await read_definition(conn, lock=True)
            if current.version != expected_version:
                raise _stale(current.version, expected_version)
            if body == current.body:
                raise Refusal(
                    "Nothing to change: the definition of done already says exactly that. "
                    "Nothing was changed."
                )
            row = await _write(conn, current, body, identity.name, identity.kind)
            changed = _read(row)
            await _rebuild_items(conn, changed.items)
            await record_event(
                conn,
                action=DEFINITION_ACTION,
                actor=identity.name,
                before={"body": current.body, "version": current.version},
                after={"body": row["body"], "version": row["version"]},
            )
            return await _with_ticks(conn, changed)


def _stale(current: int, expected: int) -> Refusal:
    return stale_version(
        "Definition of done", "get_definition_of_done", "The definition of done", current, expected
    )


async def _write(
    conn: AsyncConnection, current: DefinitionOfDone, body: str, name: str, kind: str
) -> dict[str, Any]:
    if current.updated_at is None:
        # Nothing was ever set, so there is no row yet. Two first changes at once: one makes the
        # row and the other finds it there and is told its version is stale.
        row = await fetch_one(
            conn,
            "INSERT INTO definition_of_done (body, version, updated_by, updated_by_kind) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (project_id) DO NOTHING "
            f"RETURNING {_COLUMNS}",
            (body, current.version + 1, name, kind),
        )
        if row is None:
            now = await read_definition(conn)
            raise _stale(now.version, current.version)
        return row
    row = await fetch_one(
        conn,
        "UPDATE definition_of_done SET body = %s, version = version + 1, updated_at = now(), "
        f"updated_by = %s, updated_by_kind = %s RETURNING {_COLUMNS}",
        (body, name, kind),
    )
    assert row is not None
    return row
