"""Helpers for the tests that drive the board in a real headless browser, against the real
containers (see ./check.sh). The browser is Playwright's Chromium, a development dependency only."""

import datetime
import json
import re
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Dialog, Locator, Page, Request, Route, async_playwright, expect

# Every request of the board has to go to the server it came from.
VIEWPORT = {"width": 1500, "height": 1500}
SLOW = 15_000  # milliseconds a page may take to show what a change should bring


def board_url(mcp_url: str) -> str:
    """The address of the board of the server whose MCP endpoint is `mcp_url`."""
    return mcp_url.removesuffix("/mcp") + "/board"


@dataclass
class Watched:
    """A page, and what it did that a test may want to check."""

    page: Page
    board: str
    requests: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)

    @property
    def origin(self) -> str:
        return self.board.removesuffix("/board")

    async def open(self, address: str = "") -> None:
        """Go to a page of the board, such as '#/graph?scope=all', and wait for it to load."""
        await self.page.goto(f"{self.board}{address}")
        await self.page.wait_for_selector("#top nav, dialog.modal", timeout=15_000)

    async def go(self, address: str) -> None:
        """Change the address after the # without loading the page again, as a link does."""
        await self.page.evaluate("(hash) => { location.hash = hash; }", address)


@asynccontextmanager
async def browser_page(
    board: str, *, token: str | None, name: str | None = "Tester"
) -> AsyncIterator[Watched]:
    """A headless browser on the board. With a token and a name it starts signed in, as a person
    who has used the board before would be; with none it shows the sign-in."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context(viewport=VIEWPORT)  # type: ignore[arg-type]
            if token is not None:
                await context.add_init_script(
                    f"sessionStorage.setItem('vrum.token', {json.dumps(token)});"
                )
            if name is not None:
                await context.add_init_script(
                    f"localStorage.setItem('vrum.actor', {json.dumps(name)});"
                )
            page = await context.new_page()
            watched = Watched(page=page, board=board)

            def on_request(request: Request) -> None:
                watched.requests.append(request.url)

            async def on_dialog(dialog: Dialog) -> None:
                watched.dialogs.append(dialog.message)
                await dialog.dismiss()

            page.on("request", on_request)
            page.on("dialog", on_dialog)
            page.on("pageerror", lambda error: watched.problems.append(f"page error: {error}"))
            page.on(
                "console",
                lambda message: (
                    watched.problems.append(f"console: {message.text}")
                    # A refusal the server sends (a 400) is the board working, not a fault; the
                    # browser logs it as a failed load all the same.
                    if message.type == "error" and "status of 400" not in message.text
                    else None
                ),
            )
            yield watched
        finally:
            await browser.close()


async def hold_back_news(page: Page) -> None:
    """The board hears of nothing new from now on: its next look at what changed is told that
    nothing did. This is what happens in the few seconds between an agent's change and the board's
    next look, made certain, so that a test can change something the board has not heard of.
    It returns once the page is quiet, so that a load already under way (after the person's own
    last change, say) cannot pick up the agent's change that comes next."""

    async def nothing_new(route: Route) -> None:
        await route.fulfill(json={"items": [], "next_after_id": None, "latest_event_id": 0})

    await page.route("**/board/api/tool/changes_since", nothing_new)
    await page.wait_for_load_state("networkidle")


# -- the views of M11: Table, Outline and Roadmap -------------------------------------------------


async def apply_filter(page: Page, text: str) -> None:
    """Type a filter into the box that the Table and the Roadmap share, and apply it."""
    await page.locator("#filter").fill(text)
    await page.locator("form.filter button[type=submit]").click()


async def shown_ids(page: Page, count: int) -> list[int]:
    """The ids of the Table's rows, in the order shown, once it says it holds `count` items."""
    await expect(page.locator("#count")).to_contain_text(f"{count} item", timeout=SLOW)
    ids: list[int] = await page.locator("tr.item").evaluate_all(
        "els => els.map(e => Number(e.dataset.id))"
    )
    return ids


async def sort_by(page: Page, column: str, becomes: str) -> None:
    """Click a column heading of the Table, and wait until it says it is sorted that way."""
    await page.locator(f"th[data-column={column}] button").click()
    await expect(page.locator(f"th[data-column={column}]")).to_have_attribute("aria-sort", becomes)


async def groups(page: Page) -> dict[str, list[int]]:
    """The Table's groups: the key of each, and the ids in it in the order shown."""
    found: dict[str, list[int]] = await page.locator("tbody.group").evaluate_all(
        """els => Object.fromEntries(els.map(g => [
            g.dataset.group,
            [...g.querySelectorAll('tr.item')].map(r => Number(r.dataset.id)),
        ]))"""
    )
    return found


def on(text: str) -> datetime.date:
    return datetime.date.fromisoformat(text)


def bar_of(page: Page, node_id: int) -> Locator:
    return page.locator(f".rm-bar[data-id='{node_id}']")


async def timeline(page: Page) -> dict[str, Any]:
    """Where the Roadmap starts, how many pixels a day takes, and today, as the page draws them."""
    track = page.locator(".rm-track")
    return {
        "origin": on(await track.get_attribute("data-origin") or ""),
        "ppd": float(await track.get_attribute("data-ppd") or "0"),
        "today": on(await track.get_attribute("data-today") or ""),
    }


async def roadmap_ids(page: Page, dated: int, undated: int) -> set[int]:
    """The ids on the Roadmap's timeline and in its tray, once the page says how many of each."""
    await expect(page.locator("#count")).to_contain_text(
        re.compile(rf"^{dated} items? on the timeline, {undated} undated"), timeout=SLOW
    )
    bars: list[int] = await page.locator(".rm-bar").evaluate_all(
        "els => els.map(e => Number(e.dataset.id))"
    )
    tray: list[int] = await page.locator(".tray-item").evaluate_all(
        "els => els.map(e => Number(e.dataset.id))"
    )
    assert len(bars) == dated and len(tray) == undated
    return set(bars) | set(tray)


async def drag_bar(page: Page, node_id: int, days: float, grip: str | None = None) -> None:
    """Drag a Roadmap bar (or one of its ends: grip 'start' or 'end') sideways by some days."""
    ppd = (await timeline(page))["ppd"]
    part = bar_of(page, node_id)
    if grip:
        part = part.locator(f".rm-handle.{grip}")
    box = await part.bounding_box()
    assert box is not None
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + days * ppd / 2, y, steps=4)
    await page.mouse.move(x + days * ppd, y, steps=4)
    await page.mouse.up()


def http(
    method: str, url: str, *, token: str | None = None, body: Any = None
) -> tuple[int, dict[str, str], str]:
    """A plain HTTP request, for what a caller that is not the board's own page sees: the status,
    the headers (in lower case) and the text."""
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return (
                response.status,
                {key.lower(): value for key, value in response.headers.items()},
                response.read().decode(),
            )
    except urllib.error.HTTPError as error:
        return (
            error.code,
            {key.lower(): value for key, value in error.headers.items()},
            error.read().decode(),
        )
