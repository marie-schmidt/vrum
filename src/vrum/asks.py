"""Asks (M45): a question an agent puts to a person about a story or task.

An agent that needs a person (a choice it cannot make, an approval it needs) says so with an `ask`
when it marks work blocked (`update_node`) or leaves it (`finish_work`), instead of burying the
question in a free text that nobody collects. An ask says what is asked, may offer choices or be a
yes or no approval, and says whether the work was *blocked* until it is answered or released and
left free (*parked*). It is recorded as an event (`ask_raised`) and a person's answer as another
(`ask_answered`): neither is ever edited, so a later reader sees exactly what was asked and what was
said, which is why an ask is not a comment. The ask's number is the id of the event that raised it
(shown on the board as Q-12), and `ask_state` (schema.sql) reads both events together.

This module holds what the rest of the server shares: the shape of an ask, how an ask is checked and
raised, and how the asks of one story or task are read. Answering and listing them is `ask_book.py`,
which also needs the graph."""

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import Refusal, fetch_all, record_event, refuse_secrets

ASK_RAISED = "ask_raised"
ASK_ANSWERED = "ask_answered"
KINDS = ("question", "approval")
EFFECTS = ("blocks", "parks", "gates")
APPROVAL_CHOICES = ["yes", "no"]

MOST_QUESTION_CHARS = 1000
MOST_CHOICES = 8
MOST_CHOICE_CHARS = 100
MOST_ANSWER_CHARS = 2000
MOST_COMMENT_CHARS = 2000
# How many asks of one story or task a briefing carries: the open ones first, then the latest
# answered ones.
MOST_ASKS_SHOWN = 6

ASK_COLUMNS = (
    "id, node, question, kind, choices, effect, asked_by, asked_by_kind, asked_at, "
    "answer_event_id, answered_by, answered_at, answer, comment"
)


class AskInput(BaseModel):
    """What an agent sends to ask a person something."""

    question: str = Field(description="What you need a person to decide or tell you.")
    choices: list[str] | None = Field(
        default=None,
        description="Two to eight answers to choose from; leave out for a free answer.",
    )
    approval: bool = Field(
        default=False, description="True for a yes or no approval (then give no choices)."
    )


class AskState(BaseModel):
    id: int = Field(
        description="The ask's number (shown as Q-12): the id of the event that raised it."
    )
    node: str = Field(description="The story or task it was put about, by reference.")
    question: str
    kind: str = Field(description="question or approval.")
    choices: list[str] | None = Field(
        description="The answers offered: yes and no for an approval; empty for a free answer."
    )
    effect: str = Field(
        description="blocks: the work was blocked until this is answered; parks: the work was "
        "released and stays free to take; gates: it is the approval a gate holds the work for, "
        "and yes opens the gate."
    )
    asked_by: str = Field(description="The name of the person or agent that asked.")
    asked_by_kind: str = Field(description="person or agent.")
    asked_at: datetime
    answered: bool
    answer: str | None = Field(description="The answer: one of the choices, or free words.")
    comment: str | None = Field(description="What the person added to the answer, if anything.")
    answered_by: str | None = Field(description="The person who answered.")
    answered_at: datetime | None


def ask_from(row: dict[str, Any]) -> AskState:
    return AskState(
        id=row["id"],
        node=row["node"],
        question=row["question"],
        kind=row["kind"],
        choices=row["choices"],
        effect=row["effect"],
        asked_by=row["asked_by"],
        asked_by_kind=row["asked_by_kind"],
        asked_at=row["asked_at"],
        answered=row["answer_event_id"] is not None,
        answer=row["answer"],
        comment=row["comment"],
        answered_by=row["answered_by"],
        answered_at=row["answered_at"],
    )


def clean_ask(ask: AskInput) -> dict[str, Any]:
    """An ask as it is stored: its question, its kind and, when it offers any, its choices. Anything
    unclear is refused in words, and a secret in it is refused too."""
    question = ask.question.strip()
    if not question:
        raise Refusal(
            "An ask needs its question: say what you need a person to decide or tell you. Nothing "
            "was changed."
        )
    if len(question) > MOST_QUESTION_CHARS:
        raise Refusal(
            f"The question is {len(question):,} characters; an ask may be at most "
            f"{MOST_QUESTION_CHARS:,}. Say it shorter, and put the background in the handoff "
            "note. Nothing was changed."
        )
    cleaned: dict[str, Any] = {"question": question, "kind": "question"}
    if ask.approval:
        if ask.choices:
            raise Refusal(
                "An approval is yes or no, so give either approval or choices, not both. Nothing "
                "was changed."
            )
        cleaned["kind"] = "approval"
        cleaned["choices"] = list(APPROVAL_CHOICES)
    elif ask.choices is not None:
        choices = [choice.strip() for choice in ask.choices]
        if not MOST_CHOICES >= len(choices) >= 2:
            raise Refusal(
                f"Offer two to {MOST_CHOICES} choices, or none for a free answer; you gave "
                f"{len(choices)}. Nothing was changed."
            )
        if any(not choice or len(choice) > MOST_CHOICE_CHARS for choice in choices):
            raise Refusal(
                f"Each choice must be words of at most {MOST_CHOICE_CHARS} characters, not "
                "blank. Nothing was changed."
            )
        if len({choice.lower() for choice in choices}) != len(choices):
            raise Refusal("The choices must all be different. Nothing was changed.")
        cleaned["choices"] = choices
    refuse_secrets(cleaned)
    return cleaned


async def raise_ask(
    conn: AsyncConnection, *, actor: str, node: str, ask: dict[str, Any], effect: str
) -> int:
    """Record an ask about a story or task the caller has locked: blocking it (it was just set
    blocked) or parking it (it was just released). Returns the ask's number."""
    return await record_event(
        conn,
        node=node,
        action=ASK_RAISED,
        actor=actor,
        before=None,
        after={**ask, "effect": effect},
    )


async def asks_of(conn: AsyncConnection, node: str) -> list[AskState]:
    """The asks about one story or task, the open ones first and then the latest answered, at most
    `MOST_ASKS_SHOWN` of them."""
    rows = await fetch_all(
        conn,
        f"SELECT {ASK_COLUMNS} FROM ask_state WHERE node = %s "
        "ORDER BY (answer_event_id IS NOT NULL), id DESC LIMIT %s",
        (node, MOST_ASKS_SHOWN),
    )
    return [ask_from(row) for row in rows]
