"""Reading gates (M47): what a story or task is held for, from the event log. A gate is added, and
lifted, by gates.py; this is only how one is read, so that graph.py (which shows a node's gates in
`get_node`) and gates.py can both use it."""

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import fetch_all, when

GATE_COLUMNS = (
    "id, node, kind, state, not_before, ask, question, answer, answered_by, added_by, "
    "added_by_kind, added_at, removed_by, removed_at, removed_reason"
)


class GateState(BaseModel):
    id: int = Field(description="The gate's number: the id of the event that added it.")
    node: str = Field(description="The story or task it holds, by reference.")
    kind: str = Field(
        description="timer (opens at a time) or approval (opens when a person says yes)."
    )
    state: str = Field(
        description="waiting, open, refused (its approval was answered anything but yes: the work "
        "stays held until a person lifts the gate or drops it) or removed (lifted by a person)."
    )
    words: str = Field(description="In plain words what it waits for and where it stands.")
    not_before: datetime | None = Field(description="For a timer, when it opens.")
    ask: int | None = Field(description="For an approval, the ask's number (Q-n).")
    question: str | None = Field(description="For an approval, what is asked.")
    answer: str | None = Field(description="For an approval, the answer, once there is one.")
    answered_by: str | None
    added_by: str
    added_by_kind: str
    added_at: datetime
    removed_by: str | None
    removed_at: datetime | None
    removed_reason: str | None


def gate_words(row: dict[str, Any]) -> str:
    """What a gate waits for and where it stands, in plain words."""
    if row["state"] == "removed":
        by = f"{row['removed_by']}" if row["removed_by"] else "a person"
        return f"Lifted by {by}" + (f": {row['removed_reason']}" if row["removed_reason"] else ".")
    if row["kind"] == "timer":
        moment = when(row["not_before"])
        return f"Opened at {moment}." if row["state"] == "open" else f"Waits until {moment}."
    if row["state"] == "open":
        return f"Approved by {row['answered_by']}."
    if row["state"] == "refused":
        return f"Approval was refused by {row['answered_by']} ({row['answer']})."
    return f"Waiting for a person to approve: {row['question']}"


def gate_from(row: dict[str, Any]) -> GateState:
    return GateState(
        id=row["id"],
        node=row["node"],
        kind=row["kind"],
        state=row["state"],
        words=gate_words(row),
        not_before=row["not_before"],
        ask=row["ask"],
        question=row["question"],
        answer=row["answer"],
        answered_by=row["answered_by"],
        added_by=row["added_by"],
        added_by_kind=row["added_by_kind"],
        added_at=row["added_at"],
        removed_by=row["removed_by"],
        removed_at=row["removed_at"],
        removed_reason=row["removed_reason"],
    )


async def gates_of(conn: AsyncConnection, node: str) -> list[GateState]:
    """The gates of one story or task, the ones still in the way first and then the rest."""
    rows = await fetch_all(
        conn,
        f"SELECT {GATE_COLUMNS} FROM gate_state WHERE node = %s "
        "ORDER BY (state IN ('open', 'removed')), id",
        (node,),
    )
    return [gate_from(row) for row in rows]
