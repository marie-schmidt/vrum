"""Answering and listing asks (M45). What an ask is, and how one is raised, is asks.py.

Only a person answers (the tool is in `records.PEOPLE_ONLY_TOOLS`, and the database refuses an
answer that is not a person's). The answer is final: it is one event that is never edited, and an
ask can be answered once; if something changes, the agent asks again. If the ask blocked the work
and it is still blocked, the answer returns it to not started (the server then shows it as ready
again), unless another open ask still blocks it: the work that waited for a person is free to
take, and the answer is in the briefing of whoever takes it."""

from datetime import UTC
from typing import Any

import psycopg
from pydantic import BaseModel, Field

from vrum.asks import (
    ASK_ANSWERED,
    ASK_COLUMNS,
    MOST_ANSWER_CHARS,
    MOST_COMMENT_CHARS,
    AskState,
    ask_from,
)
from vrum.common import (
    Refusal,
    check_choice,
    fetch_all,
    fetch_one,
    page_size,
    record_event,
    refuse_secrets,
    split_page,
)
from vrum.db import Database
from vrum.graph import change_status, require_node
from vrum.records import require_person

ASK_STATUSES = ("open", "answered")


class AskPage(BaseModel):
    items: list[AskState]
    total: int = Field(description="How many asks match in all, across pages.")
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )


def _answered_words(ask: dict[str, Any]) -> str:
    moment = ask["answered_at"].astimezone(UTC).strftime("%Y-%m-%d")
    return (
        f"Ask {ask['id']} was already answered by {ask['answered_by']} on {moment} "
        f"({ask['answer']!r}). An answer is final and is never edited: if something changed, the "
        "agent asks again."
    )


class AskBook:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def list_asks(
        self, *, status: str | None, node: str | None, limit: int, after_id: int
    ) -> AskPage:
        """The asks of the project, oldest first, a page at a time. `status` is open (not yet
        answered) or answered; `node` only the asks about that story or task."""
        limit = page_size(limit)
        if status is not None:
            check_choice("status", status, ASK_STATUSES)
        if after_id < 0:
            raise Refusal(f"after_id must be 0 or more, but it is {after_id}.")
        where = (
            "(%(status)s::text IS NULL OR (%(status)s = 'open') = (answer_event_id IS NULL)) "
            "AND (%(node)s::text IS NULL OR node = %(node)s)"
        )
        params = {"status": status, "node": node}
        async with self._database.transaction() as conn:
            if node is not None:
                await require_node(conn, node)
            rows = await fetch_all(
                conn,
                f"SELECT {ASK_COLUMNS} FROM ask_state WHERE {where} AND id > %(after)s "
                "ORDER BY id LIMIT %(fetch)s",
                {**params, "after": after_id, "fetch": limit + 1},
            )
            counted = await fetch_one(
                conn, f"SELECT count(*) AS total FROM ask_state WHERE {where}", params
            )
        assert counted is not None
        page, next_after_id = split_page(rows, limit)
        return AskPage(
            items=[ask_from(row) for row in page],
            total=int(counted["total"]),
            next_after_id=next_after_id,
        )

    async def answer_ask(
        self, *, actor: str, ask: int, answer: str, comment: str | None
    ) -> AskState:
        """Answer an ask, as the person calling. An approval is answered yes or no, a question with
        choices with one of them, any other question in words; the comment is optional."""
        require_person(f"Ask {ask}", "answer an ask")
        answer = answer.strip()
        comment = comment.strip() if comment and comment.strip() else None
        if not answer:
            raise Refusal("An answer cannot be empty: say what you decide. Nothing was changed.")
        if len(answer) > MOST_ANSWER_CHARS or (comment and len(comment) > MOST_COMMENT_CHARS):
            raise Refusal(
                f"An answer and a comment may each be at most {MOST_ANSWER_CHARS:,} characters. "
                "Nothing was changed."
            )
        refuse_secrets({"answer": answer, "comment": comment})
        async with self._database.transaction() as conn:
            found = await fetch_one(
                conn, f"SELECT {ASK_COLUMNS} FROM ask_state WHERE id = %s", (ask,)
            )
            if found is None:
                raise Refusal(
                    f"There is no ask {ask} in this project. list_asks shows the asks there are."
                )
            node = await require_node(conn, found["node"], lock=True)
            again = await fetch_one(
                conn, f"SELECT {ASK_COLUMNS} FROM ask_state WHERE id = %s", (ask,)
            )
            assert again is not None
            if again["answer_event_id"] is not None:
                raise Refusal(_answered_words(again))
            choices: list[str] | None = again["choices"]
            if choices is not None:
                match = next((c for c in choices if c.lower() == answer.lower()), None)
                if match is None:
                    raise Refusal(
                        f"Ask {ask} offers these answers: {', '.join(choices)}. Answer with one "
                        "of them, and put anything else in the comment. Nothing was changed."
                    )
                answer = match
            try:
                await record_event(
                    conn,
                    node=again["node"],
                    action=ASK_ANSWERED,
                    actor=actor,
                    before=None,
                    after={
                        "ask": ask,
                        "answer": answer,
                        **({"comment": comment} if comment else {}),
                    },
                )
            except psycopg.errors.UniqueViolation:
                raise Refusal(f"Ask {ask} was answered a moment ago by someone else.") from None
            if again["effect"] == "blocks" and node["status"] == "blocked":
                still = await fetch_one(
                    conn,
                    "SELECT count(*) AS open FROM ask_state WHERE node = %s "
                    "AND effect = 'blocks' AND answer_event_id IS NULL AND id <> %s",
                    (again["node"], ask),
                )
                assert still is not None
                if not still["open"]:
                    await change_status(conn, actor, node, "new")
            done = await fetch_one(
                conn, f"SELECT {ASK_COLUMNS} FROM ask_state WHERE id = %s", (ask,)
            )
        assert done is not None
        return ask_from(done)
