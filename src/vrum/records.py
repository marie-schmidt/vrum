"""What every note shares: its status words, its headline and how a person confirms it.

A note has one status: unconfirmed or confirmed while it is in force, then superseded or withdrawn
once it has ended. Confirmed says that a named person looked at it, never that Vrum proved it true.
It only changes while the note is in force: once a note is superseded or withdrawn, who confirmed
it and when stay as they were. Whoever records a note is not thereby a person confirming it, and
changing a confirmed note needs that person's word."""

from typing import Any

from psycopg import AsyncConnection

from vrum.common import Refusal, check_choice, fetch_one, record_event, required_text
from vrum.identity import current_identity

CONFIRMATIONS = ("confirmed", "unconfirmed")

# The statuses of a note. In force is unconfirmed or confirmed; ended is superseded or withdrawn.
IN_FORCE = ("unconfirmed", "confirmed")
ENDED = ("superseded", "withdrawn")
NOTE_STATUSES = IN_FORCE + ENDED

# The tools only a person can use: `require_person` refuses an agent that calls one. No agent key
# can be given them, and the Tools page says so. The one place that names them.
PEOPLE_ONLY_TOOLS = frozenset(
    {
        "set_confirmation",
        "set_definition_of_done",
        "answer_ask",
        "approve_plan",
        "request_plan_changes",
        "remove_gate",
        "decide_placement",
    }
)

HEADLINE_CHARS = 120


def headline_of(text: str) -> str:
    """What a list shows for a note: its first line that is not blank, cut to at most 120
    characters with an ellipsis when it is cut. A note has no title, so this is the one place that
    works one out."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if len(first) <= HEADLINE_CHARS:
        return first
    return first[: HEADLINE_CHARS - 1].rstrip() + "\u2026"


def require_person(record: str, action: str = "confirm or un-confirm") -> None:
    """Confirming, taking a confirmation away and changing the definition of done are for people:
    an agent's call is refused. The refusal says where on the board a person does it."""
    if current_identity().kind != "person":
        raise Refusal(
            f"Only a person can {action}. Ask a person to do it on the board, on {record}. "
            "Nothing was changed."
        )


def ended_words(row: dict[str, Any]) -> str:
    """A sentence saying why a note no longer counts, such as 'Note 4 was withdrawn (why)'."""
    what = f"Note {row['id']}"
    if row["status"] == "withdrawn":
        return f"{what} was withdrawn ({row['status_reason']})"
    return f"{what} was already superseded by note {row['superseded_by_id']}" + (
        f" ({row['status_reason']})" if row.get("status_reason") else ""
    )


def said(said_by: str | None, quote: str | None) -> dict[str, str]:
    """Who changed their mind and what they said, for the history. Both or neither."""
    person, words = (said_by or "").strip(), (quote or "").strip()
    if bool(person) != bool(words):
        raise Refusal(
            "Give both said_by (the person who changed their mind) and quote (what they said, "
            "or where), or neither."
        )
    return {"said_by": person, "quote": words} if person else {}


def require_a_person_for_confirmed(rows: list[dict[str, Any]], said_words: dict[str, str]) -> None:
    """A person looked at a confirmed note, so changing it needs that person's word."""
    confirmed = [row for row in rows if row["status"] == "confirmed"]
    if confirmed and not said_words:
        names = ", ".join(
            f"note {row['id']} (confirmed by {row['confirmed_by']})" for row in confirmed
        )
        raise Refusal(
            f"A person confirmed {names}, so changing it needs that person's word: give "
            "said_by (who changed their mind) and quote (what they said, or where). Nothing was "
            "changed."
        )


async def apply_confirmation(
    conn: AsyncConnection,
    *,
    row: dict[str, Any],
    actor: str,
    confirmation: str,
    person: str,
    quote: str | None,
) -> None:
    """Confirm or unconfirm the note `row` (read with its row locked), and record who and why.

    Only a person may. Confirming names the person who reviewed it (the person signed in records
    the change separately, as the actor). Unconfirming needs the person who says so and their
    words, since it takes away what a person gave."""
    check_choice("confirmation", confirmation, CONFIRMATIONS)
    name = f"Note {row['id']}"
    require_person(name)
    if row["status"] not in IN_FORCE:
        raise Refusal(
            f"{ended_words(row)}, so its confirmation cannot change: who confirmed it and when "
            "stay in its history. Nothing was changed."
        )
    words = (quote or "").strip()
    if confirmation == "confirmed":
        reviewer = required_text(
            person,
            "A confirmation needs the person who reviewed the note: person must be the name of "
            "a person. (Who you are is recorded separately, from how you are signed in.)",
        )
        if row["status"] == "confirmed":
            raise Refusal(
                f"{name} was already confirmed by {row['confirmed_by']}. Nothing was changed. "
                "To take that confirmation away, set it to unconfirmed with that person's word."
            )
        changed = await fetch_one(
            conn,
            "UPDATE notes SET status = 'confirmed', confirmed_by = %s, confirmed_at = now() "
            "WHERE id = %s RETURNING confirmed_by, confirmed_at",
            (reviewer, row["id"]),
        )
        assert changed is not None
        await record_event(
            conn,
            action="note_confirmed",
            actor=actor,
            before={"status": "unconfirmed", "confirmed_by": None},
            after={
                "status": "confirmed",
                "confirmed_by": changed["confirmed_by"],
                "confirmed_at": changed["confirmed_at"].isoformat(),
                **({"quote": words} if words else {}),
            },
            note_id=row["id"],
        )
        return
    if row["status"] != "confirmed":
        raise Refusal(f"{name} is not confirmed, so there is nothing to take away. No change made.")
    who = required_text(
        person,
        "Taking a confirmation away needs the person who says so: person must be the name of a "
        "person, and quote what they said, or where.",
    )
    if not words:
        raise Refusal(
            "Taking a confirmation away needs what the person said: give quote (their words, or "
            "where they said it). Nothing was changed."
        )
    await conn.execute(
        "UPDATE notes SET status = 'unconfirmed', confirmed_by = NULL, confirmed_at = NULL "
        "WHERE id = %s",
        (row["id"],),
    )
    await record_event(
        conn,
        action="note_unconfirmed",
        actor=actor,
        before={
            "status": "confirmed",
            "confirmed_by": row["confirmed_by"],
            "confirmed_at": row["confirmed_at"].isoformat(),
        },
        after={"status": "unconfirmed", "confirmed_by": None, "said_by": who, "quote": words},
        note_id=row["id"],
    )
