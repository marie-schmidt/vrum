"""What the tests of M14's pages and the acceptance walk share: asking the tools the same questions
Home answers, a project with work in every state Home counts, and reading the Activity feed. All of
it goes through a real MCP client against the real containers; none of it reaches into the page's
own code."""

import re
from typing import Any

import anyio
from mcp import Client
from playwright.async_api import Locator, Page, expect

from tests.acceptance_finding import DUPLICATES
from tests.board_helpers import SLOW, every_overlap, made, twin_epics
from tests.helpers import (
    day,
    make_belief,
    make_decision,
    make_fact,
    ok,
    reindexed,
    unique_word,
)

# The numbers on Home, by the key of their tile.
HOME_TILES = (
    "free",
    "blocked",
    "overdue",
    "lapsed",
    "unplaced",
    "no-rules",
    "decisions",
    "stale",
    "unconfirmed",
    "overlaps",
    "waiting",
)


async def home_tile(page: Page, key: str) -> int:
    """The number on one tile of Home."""
    link = page.locator(f"a.tile[data-key={key}]")
    await expect(link).to_be_visible(timeout=SLOW)
    return int(await link.get_attribute("data-value") or "-1")


async def every_item(client: Client, tool: str, **arguments: Any) -> list[dict[str, Any]]:
    """Every item of a list tool that pages by after_id."""
    items: list[dict[str, Any]] = []
    after: int | None = None
    while True:
        page = await ok(
            client, tool, limit=200, **({} if after is None else {"after_id": after}), **arguments
        )
        items.extend(page["items"])
        after = page.get("next_after_id")
        if after is None:
            return items


async def ids_of(client: Client, query: str) -> list[int]:
    return [node["id"] for node in await every_item(client, "list_nodes", query=query)]


async def home_asked(client: Client) -> dict[str, int]:
    """What the tools say for each question Home answers, asked of the server directly."""
    week_start = day(-6)
    decisions = [
        d
        for d in await every_item(client, "list_decisions", kind="decision")
        if d["decided_on"] >= week_start
    ]
    beliefs = await every_item(client, "list_memories", type="belief", status="active", stale=False)
    coverage = await ok(client, "coverage_report", show="both", limit=1)
    overlaps = await every_overlap(client)
    index = (await ok(client, "search", query="anything", limit=1))["index"]
    return {
        "free": (await ok(client, "ready_work", limit=1))["total"],
        "blocked": (await ok(client, "list_nodes", status="blocked", limit=1))["total"],
        "overdue": (await ok(client, "list_nodes", query="is:overdue", limit=1))["total"],
        "lapsed": (await ok(client, "list_nodes", query="is:lapsed", limit=1))["total"],
        "unplaced": coverage["unplaced_rule_count"],
        "no-rules": coverage["story_without_rules_count"],
        "decisions": len(decisions),
        "stale": (await ok(client, "list_memories", type="belief", stale=True, limit=1))["total"],
        "unconfirmed": len([b for b in beliefs if b["confirmed_by"] is None]),
        "overlaps": len([p for p in overlaps["items"] if not p["already_linked"]]),
        "waiting": index["waiting_to_index"],
    }


async def busy_project(client: Client) -> dict[str, Any]:
    """Work in every state Home counts, a rule in no story, a story with no rule, decisions and
    beliefs of every kind, and the M5 duplicates, all in epics of their own."""
    word = unique_word()
    epic = await made(client, "epic", f"Counted {word}")
    story = await made(client, "story", f"Story {word}", epic)
    tasks = {
        name: await made(client, "task", f"{name} {word}", story, **more)
        for name, more in (
            ("late", {"target_on": day(-3)}),
            ("stuck", {}),
            ("going", {}),
            ("finished", {}),
            ("dropped", {}),
            ("held", {}),
            ("lapsing", {}),
            ("free", {}),
            ("waiting", {}),
        )
    }
    for name, status in (
        ("stuck", "blocked"),
        ("going", "in_progress"),
        ("finished", "done"),
        ("dropped", "dropped"),
    ):
        await ok(
            client, "update_node", actor="a", node_id=tasks[name], expected_version=1, status=status
        )
    await ok(
        client,
        "add_link",
        actor="a",
        source_id=tasks["waiting"],
        target_id=tasks["free"],
        link_type="needs_first",
    )
    await ok(client, "claim_node", actor=f"holder-{word}", node_id=tasks["held"], lease_seconds=600)
    await ok(
        client, "claim_node", actor=f"quitter-{word}", node_id=tasks["lapsing"], lease_seconds=1
    )
    await ok(client, "create_rule", actor="a", title=f"Unplaced {word}")
    placed = await made(client, "story", f"Has a rule {word}", epic)
    await ok(client, "create_rule", actor="a", title=f"Placed {word}", story_id=placed)
    await make_decision(client, f"This week {word}", decided_on=day(-6))
    await make_decision(client, f"Last week {word}", decided_on=day(-7))
    await make_decision(client, f"A principle {word}", kind="principle")
    fact = await make_fact(client, f"A fact {word}")
    await make_belief(client, [fact], f"Waits for a person {word}")
    gone = await make_fact(client, f"A wrong fact {word}")
    await make_belief(client, [gone], f"Goes stale {word}")
    await ok(client, "retract_memory", actor="a", memory_ids=[gone["id"]], reason="Wrong")
    confirmed = await make_belief(client, [fact], f"Confirmed {word}")
    await ok(client, "confirm_belief", actor="a", memory_id=confirmed["id"], confirmed_by="Marie")
    await twin_epics(client, DUPLICATES[:3])
    await reindexed(client)
    await anyio.sleep(1.5)  # a claim of one second has lapsed
    return {"word": word, "epic": epic, "story": story, "tasks": tasks}


# -- the Activity feed -------------------------------------------------------------------------


def feed_row(page: Page, text: str | re.Pattern[str]) -> Locator:
    """The first row of the feed that holds these words."""
    return page.locator("#feed li.event", has_text=text).first


async def feed_ids(page: Page) -> list[int]:
    """The ids of the events the feed shows, in the order shown (newest first)."""
    ids: list[int] = await page.locator("#feed li.event").evaluate_all(
        "els => els.map(e => Number(e.dataset.event))"
    )
    return ids


async def events_of(client: Client, **filters: Any) -> list[dict[str, Any]]:
    """Every event the server gives for these filters, oldest first."""
    found: list[dict[str, Any]] = []
    after = 0
    while True:
        page = await ok(client, "changes_since", after_event_id=after, limit=200, **filters)
        found.extend(page["items"])
        if page["next_after_id"] is None:
            return found
        after = page["next_after_id"]


def spoken(text: str) -> str:
    """Words as a person reads them: any run of spaces and line breaks is one space."""
    return " ".join(text.split())
