"""M46: the Needs a person view for hosts that draw MCP Apps (needs_app.py, needs_view.html)."""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from importlib.resources import files

from vrum.asks import AskState
from vrum.config import Settings
from vrum.needs import NeedsAPerson, NeedsGroup, NeedsItem
from vrum.needs_app import NEEDS_VIEW_URI, summary
from vrum.server import create_server

ENV = {"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "s3cret"}
NOW = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)


def a_row(kind: str, **more: object) -> NeedsItem:
    fields: dict[str, object] = {
        "kind": kind,
        "node": "task:4",
        "title": "Send the receipt",
        "reason": "Which provider should it use?",
        "waiting_since": NOW - timedelta(hours=3),
        "waiting": "build-agent",
        "waiting_kind": "agent",
        "count": 1,
        "ask": None,
        "plan": None,
        "notes": [],
        "level": "plan" if kind == "plan" else "task",
        "decision": "answer" if kind == "ask" else "approve",
    }
    fields.update(more)
    return NeedsItem.model_validate(fields)


def an_ask() -> AskState:
    return AskState.model_validate(
        {
            "id": 12,
            "node": "task:4",
            "question": "Which provider should it use?",
            "kind": "question",
            "choices": ["Postmark", "SES"],
            "effect": "blocks",
            "asked_by": "build-agent",
            "asked_by_kind": "agent",
            "asked_at": NOW - timedelta(hours=3),
            "answered": False,
            "answer": None,
            "comment": None,
            "answered_by": None,
            "answered_at": None,
        }
    )


def test_an_empty_list_says_so() -> None:
    empty = NeedsAPerson(as_of=NOW, total=0, groups=[], left_out=0)
    assert summary(empty) == "Nothing is waiting for a person."


def test_the_words_say_each_row_who_waits_and_where_a_person_answers() -> None:
    needs = NeedsAPerson(
        as_of=NOW,
        total=1,
        groups=[NeedsGroup(story="story:2", title="Receipts", items=[a_row("ask", ask=an_ask())])],
        left_out=3,
        board_address="http://vrum.example:8000/board/p/7",
    )
    words = summary(needs)
    assert "1 thing is waiting for a person." in words
    assert "S-2 Receipts:" in words
    assert "Which provider should it use? (Q-12, choices: Postmark, SES)" in words
    assert "T-4 Send the receipt; build-agent is waiting for 3 hours" in words
    assert "3 more are not shown." in words
    assert "A person answers on the board at http://vrum.example:8000/board/p/7#/needs" in words
    assert "under their own name" in words


def test_a_list_with_no_address_still_says_a_person_answers_on_the_board() -> None:
    needs = NeedsAPerson(
        as_of=NOW,
        total=2,
        groups=[NeedsGroup(story=None, title="Plans waiting for approval", items=[a_row("plan")])],
        left_out=0,
    )
    words = summary(needs)
    assert "2 things are waiting for a person." in words
    assert "Plans waiting for approval:" in words
    assert words.endswith("A person answers on the board, under their own name.")


def test_the_view_is_one_self_contained_page_that_loads_and_sends_nothing_outside() -> None:
    html = (files("vrum") / "needs_view.html").read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert not re.search(r"https?://|//[a-z]", html), "no address of any server"
    for outside in (
        "<script src",
        "<link",
        "<img",
        "<iframe",
        "@import",
        "url(",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "sendBeacon",
        "eval(",
        "new Function",
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "localStorage",
        "cookie",
    ):
        assert outside not in html, outside
    # It speaks to its host in the MCP Apps words, and only reads: no answer is sent from it.
    for words in ("ui/initialize", "ui/notifications/tool-result", "ui/open-link"):
        assert words in html
    assert "answer_ask" not in html and "approve_plan" not in html


def test_the_server_offers_the_view_and_the_one_tool_that_shows_it() -> None:
    server = create_server(Settings.from_env(ENV))
    resources = asyncio.run(server.list_resources())
    [view] = [one for one in resources if str(one.uri) == NEEDS_VIEW_URI]
    assert view.mime_type == "text/html;profile=mcp-app"
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    shown_by = [
        name for name, tool in tools.items() if (tool.meta or {}).get("ui", {}).get("resourceUri")
    ]
    assert shown_by == ["needs_a_person"]
    assert tools["needs_a_person"].meta == {
        "ui": {"resourceUri": NEEDS_VIEW_URI, "visibility": ["model", "app"]}
    }
    assert tools["needs_a_person"].annotations is not None
    assert tools["needs_a_person"].annotations.read_only_hint is True
