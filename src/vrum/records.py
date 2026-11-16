"""What facts and decisions share: confirmation, and the plain words for where a record stands.

Confirmation is a field of the record, not a record of its own. It says that a named person
looked at it, never that Vrum proved it true. It only changes while the record is active: once a
record is superseded or withdrawn, the confirmation it had stays as it was. Whoever records a
statement is not thereby a person confirming it, and changing a confirmed record needs that
person's word."""

from typing import Any

from psycopg import AsyncConnection

from vrum.common import Refusal, check_choice, fetch_one, record_event, required_text

CONFIRMATIONS = ("confirmed", "unconfirmed")

# The tables confirmation is kept in. Fixed here, never from a caller.
_TABLES = {"fact": ("facts", "fact_id"), "decision": ("decisions", "decision_id")}


def confirmation_words(row: dict[str, Any]) -> str:
    if row["confirmed_by"]:
        return f"confirmed by {row['confirmed_by']} on {row['confirmed_at'].date().isoformat()}"
    return "not confirmed by a person"


def label_of(noun: str, row: dict[str, Any]) -> str:
    """Where a record stands, in plain words: its kind, whether it still counts, and whether a
    person confirmed it, so that no record is mistaken for another or for a confirmed one."""
    if row["status"] != "active":
        kind = f"{row['status'].capitalize()} {noun}"
    elif noun == "decision" and row.get("project_wide"):
        kind = "Project-wide decision"
    else:
        kind = noun.capitalize()
    return f"{kind}, {confirmation_words(row)}"


def ended_words(noun: str, row: dict[str, Any]) -> str:
    """A sentence saying why a record no longer counts, such as 'Fact 4 was withdrawn (why)'."""
    what = f"{noun.capitalize()} {row['id']}"
    if row["status"] == "withdrawn":
        return f"{what} was withdrawn ({row['status_reason']})"
    return f"{what} was already superseded by {noun} {row['superseded_by_id']}" + (
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


def require_a_person_for_confirmed(
    noun: str, rows: list[dict[str, Any]], said_words: dict[str, str]
) -> None:
    """A person looked at a confirmed record, so changing it needs that person's word."""
    confirmed = [row for row in rows if row["confirmed_by"]]
    if confirmed and not said_words:
        names = ", ".join(
            f"{noun} {row['id']} (confirmed by {row['confirmed_by']})" for row in confirmed
        )
        raise Refusal(
            f"A person confirmed {names}, so changing it needs that person's word: give "
            "said_by (who changed their mind) and quote (what they said, or where). Nothing was "
            "changed."
        )


async def apply_confirmation(
    conn: AsyncConnection,
    *,
    noun: str,
    row: dict[str, Any],
    actor: str,
    confirmation: str,
    person: str,
    quote: str | None,
) -> None:
    """Confirm or unconfirm the record `row` (read with its row locked), and record who and why.

    Confirming names the person who reviewed it (the actor is recorded separately). Unconfirming
    needs the person who says so and their words, since it takes away what a person gave."""
    check_choice("confirmation", confirmation, CONFIRMATIONS)
    table, column = _TABLES[noun]
    name = f"{noun.capitalize()} {row['id']}"
    if row["status"] != "active":
        raise Refusal(
            f"{ended_words(noun, row)}, so its confirmation cannot change: what it was confirmed "
            "as stays in its history. Nothing was changed."
        )
    words = (quote or "").strip()
    if confirmation == "confirmed":
        reviewer = required_text(
            person,
            "A confirmation needs the person who reviewed the record: person must be the name of "
            "a person. (You are recorded separately, as actor.)",
        )
        if row["confirmed_by"]:
            raise Refusal(
                f"{name} was already confirmed by {row['confirmed_by']}. Nothing was changed. "
                "To take that confirmation away, set it to unconfirmed with that person's word."
            )
        changed = await fetch_one(
            conn,
            f"UPDATE {table} SET confirmed_by = %s, confirmed_at = now() WHERE id = %s "
            "RETURNING confirmed_by, confirmed_at",
            (reviewer, row["id"]),
        )
        assert changed is not None
        await record_event(
            conn,
            action=f"{noun}_confirmed",
            actor=actor,
            before={"confirmed_by": None},
            after={
                "confirmed_by": changed["confirmed_by"],
                "confirmed_at": changed["confirmed_at"].isoformat(),
                **({"quote": words} if words else {}),
            },
            **{column: row["id"]},
        )
        return
    if not row["confirmed_by"]:
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
        f"UPDATE {table} SET confirmed_by = NULL, confirmed_at = NULL WHERE id = %s", (row["id"],)
    )
    await record_event(
        conn,
        action=f"{noun}_unconfirmed",
        actor=actor,
        before={
            "confirmed_by": row["confirmed_by"],
            "confirmed_at": row["confirmed_at"].isoformat(),
        },
        after={"confirmed_by": None, "said_by": who, "quote": words},
        **{column: row["id"]},
    )
