"""Helpers for the tests that drive the board in a real headless browser, against the real
containers (see ./check.sh). The browser is Playwright's Chromium, a development dependency only."""

import json
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Dialog, Page, Request, async_playwright

# Every request of the board has to go to the server it came from.
VIEWPORT = {"width": 1500, "height": 1500}


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
