"""What the tests of the Activity drawer, the Tools and Status pages and the acceptance walk share:
reading every page of a list tool, and reading the Activity feed. All of it goes through a real MCP
client against the real containers; none of it reaches into the page's own code."""

import re
from typing import Any

from mcp import Client
from playwright.async_api import Locator, Page

from tests.helpers import ok


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
