"""What the tests of the Activity page, the Tools and Status pages and the acceptance walk share:
reading every page of a list tool, and reading the Activity feed. All of it goes through a real MCP
client against the real containers; none of it reaches into the page's own code."""

import contextlib
import re
from typing import Any

from mcp import Client
from playwright.async_api import Locator, Page, expect
from playwright.async_api import TimeoutError as PlaywrightTimeout

from tests.board_helpers import SLOW
from tests.helpers import NODE_PAGES, ok


async def every_item(client: Client, tool: str, **arguments: Any) -> list[dict[str, Any]]:
    """Every item of a list tool, following its pages (a page of nodes is continued by `after`,
    a reference; every other list by `after_id`)."""
    start, more = ("after", "next_after") if tool in NODE_PAGES else ("after_id", "next_after_id")
    items: list[dict[str, Any]] = []
    after: int | str | None = None
    while True:
        page = await ok(
            client, tool, limit=200, **({} if after is None else {start: after}), **arguments
        )
        items.extend(page["items"])
        after = page.get(more)
        if after is None:
            return items


async def ids_of(client: Client, query: str) -> list[str]:
    """The references of the nodes a filter finds, in the order the server gives them."""
    return [node["ref"] for node in await every_item(client, "list_nodes", query=query)]


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


async def feed_becomes(page: Page, wanted: list[int]) -> list[int]:
    """Wait until the feed lists exactly these events, newest first, and give what it lists then
    (what it still lists when the wait runs out, if it never does). A changed filter is read again
    while the rows it had stay on show, so a row that is in both the old and the new list is no
    sign that the new one has arrived."""
    with contextlib.suppress(PlaywrightTimeout):
        await page.wait_for_function(
            """wanted => {
                const ids = [...document.querySelectorAll('#feed li.event')]
                    .map(e => Number(e.dataset.event));
                return ids.length === wanted.length && ids.every((id, at) => id === wanted[at]);
            }""",
            arg=wanted,
            timeout=SLOW,
        )
    return await feed_ids(page)


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


# -- the Activity page's three views (a toggle: Changes, Working, Free to take) ------------------


async def show_tab(page: Page, tab: str) -> None:
    """Choose one of the Activity page's three views (changes, working or free) with the toggle,
    unless it is the one shown, and wait until it is the pressed one. Only the chosen view is on the
    page, so what another view holds is not there until it is chosen."""
    button = page.locator(f"#activity-tab-{tab}")
    if await button.get_attribute("aria-pressed") != "true":
        await button.click()
    await expect(button).to_have_attribute("aria-pressed", "true")


async def open_working(page: Page) -> None:
    """Show "Who is working on what" on the Activity page (the Working view), and wait for its
    list of people and agents."""
    await show_tab(page, "working")
    await expect(page.locator("#agents")).to_be_visible(timeout=SLOW)


async def open_free(page: Page) -> None:
    """Show the work that is free to take on the Activity page (the Free to take view), and wait
    until it has been read."""
    await show_tab(page, "free")
    await expect(page.locator("#free-body .free-wrap")).to_be_visible(timeout=SLOW)


async def open_activity_filter(page: Page) -> None:
    """Open the Activity page's filter (the Filter button above the changes) when it is closed,
    showing the changes first if another view is shown. It opens by itself when the address
    already carries a filter."""
    await show_tab(page, "changes")
    form = page.locator("#activity-filter")
    if await form.is_hidden():
        await page.locator("#activity-filter-toggle").click()
    await expect(form).to_be_visible()


async def claim_rows(page: Page) -> list[dict[str, Any]]:
    """The claims the shown view of the Activity page lists now, in the order drawn: node (the
    reference), actor (who holds it; None for work that is free to take), live (False for a claim
    that ran out; None for work that is free) and section (claims-live and claims-lapsed for the
    claims listed under each person or agent, by whether the claim is still in force, or free).
    The claims of every person are in one list, so the order is by person and then in force before
    lapsed. Working lists the claims held, Free to take the work nobody holds."""
    rows: list[dict[str, Any]] = await page.locator("#activity-view li.claim").evaluate_all(
        """els => els.map(e => ({
            node: e.dataset.node,
            actor: e.dataset.actor ?? null,
            live: e.dataset.live === undefined ? null : e.dataset.live === '1',
            section: e.closest('#free') ? 'free'
                : (e.dataset.live === '1' ? 'claims-live' : 'claims-lapsed'),
        }))"""
    )
    return rows
