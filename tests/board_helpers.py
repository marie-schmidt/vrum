"""Helpers for the tests that drive the board in a real headless browser, against the real
containers (see ./check.sh). The browser is Playwright's Chromium, a development dependency only."""

import contextlib
import datetime
import json
import re
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from mcp import Client
from playwright.async_api import (
    Dialog,
    Locator,
    Page,
    Request,
    Response,
    Route,
    async_playwright,
    expect,
)

from tests.helpers import ok, ref_from_code, reindexed, run_sql, unique_word
from tests.world import SESSION_COOKIE, Person, Project, Reply, project_at, the_project

# Every request of the board has to go to the server it came from.
VIEWPORT = {"width": 1500, "height": 1500}
SLOW = 15_000  # milliseconds a page may take to show what a change should bring
# Every page of the board, as its address names it: the three views of the work, and the pages
# that the Settings menu opens. Search and Activity are drawers (`drawer=search|activity`).
VIEWS = ["board", "graph", "roadmap"]
SETTINGS = ["access", "tools", "status"]
PAGES = [*VIEWS, *SETTINGS]
# What the board's old addresses named, which are not pages any more (and have no alias).
GONE = ["home", "table", "outline", "rules", "memory", "overlaps", "ask", "search", "activity"]


def board_url(mcp_url: str, project: Project | None = None) -> str:
    """The address of a project's board (by default the shared test project's) on the server whose
    MCP endpoint is `mcp_url`."""
    one = project or the_project()
    return f"{mcp_url.removesuffix('/mcp')}/board/p/{one.id}"


async def made(
    client: Client, kind: str, title: str, parent: str | None = None, **more: Any
) -> str:
    """Make an epic, story or task through MCP as an agent would, and give its reference
    (`parent` is the reference of its epic or story)."""
    fields: dict[str, Any] = {"actor": "agent-1", "kind": kind, "title": title, **more}
    if parent:
        fields["parent"] = parent
    return str((await ok(client, "create_node", **fields))["ref"])


def plain(board_words: str, mcp_words: str) -> None:
    """The board said what the tool says: the same words, without the protocol's prefix."""
    assert board_words.strip() and not board_words.startswith("Error executing tool")
    assert mcp_words.endswith(board_words.strip()), (board_words, mcp_words)


@dataclass
class Watched:
    """A page, and what it did that a test may want to check."""

    page: Page
    board: str
    requests: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)
    # Every response the server sent, as (address, text), when the page was asked to keep them.
    bodies: list[tuple[str, str]] = field(default_factory=list)

    @property
    def origin(self) -> str:
        """The server's own address, such as http://127.0.0.1:18000."""
        return self.board.split("/board")[0]

    async def open(self, address: str = "") -> None:
        """Go to a page of the board, such as '#/graph?scope=all', and wait for it to load. It
        waits for the shell, not for the sidebar's links: on a narrow screen the sidebar is
        off-canvas and so not visible until the menu button opens it."""
        await self.page.goto(f"{self.board}{address}")
        await self.page.wait_for_selector("#app[data-shell=project], .entry", timeout=15_000)

    async def go(self, address: str) -> None:
        """Change the address after the # without loading the page again, as a link does."""
        await self.page.evaluate("(hash) => { location.hash = hash; }", address)


@asynccontextmanager
async def browser_page(
    board: str, *, token: str | None, name: str | None = "Tester", keep_bodies: bool = False
) -> AsyncIterator[Watched]:
    """A headless browser on a project's board (`board`, from board_url). With a `token`, which is
    the shared test project's agent key from the_token(), it starts signed in as that project's
    owner, with a session of its own, as a person who has used the board before would be; with
    none it is signed out and shows the sign-in. `name` is what the board records changes with.
    `keep_bodies` keeps the text of every response, for a test that searches them all."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context(viewport=VIEWPORT)  # type: ignore[arg-type]
            if token is not None:
                person = project_at(board).owner.sign_in_again()
                await context.add_cookies(
                    [
                        {
                            "name": SESSION_COOKIE,
                            "value": person.cookie,
                            "domain": urlparse(board).hostname or "127.0.0.1",
                            "path": "/board",
                            "httpOnly": True,
                            "sameSite": "Strict",
                        }
                    ]
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

            async def on_response(response: Response) -> None:
                # A body that is gone (the page moved on) cannot be read, and is not needed.
                with contextlib.suppress(Exception):
                    watched.bodies.append((response.url, await response.text()))

            page.on("request", on_request)
            page.on("dialog", on_dialog)
            if keep_bodies:
                page.on("response", on_response)
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

    await page.route("**/board/api/projects/*/tool/changes_since", nothing_new)
    await page.wait_for_load_state("networkidle")


# -- the work views: Board and Roadmap ---------------------------------------------------------


async def apply_filter(page: Page, text: str) -> None:
    """Type a filter into the box that the Board, the Graph and the Roadmap share, and apply it."""
    await page.locator("#filter").fill(text)
    await page.locator("form.filter button[type=submit]").click()


def ref_slug(ref: str) -> str:
    """What the board puts in an element's id for a reference: the colon becomes a dash."""
    return ref.replace(":", "-")


def card(page: Page, ref: str) -> Locator:
    """The Board's card of a story or task."""
    return page.locator(f".card[data-ref='{ref}']")


def lane(page: Page, epic: str) -> Locator:
    """The Board's lane of one epic."""
    return page.locator(f".lane[data-ref='{epic}']")


def cell(page: Page, epic: str, status: str) -> Locator:
    """The column of one status in the lane of one epic of the Board."""
    return page.locator(f".cell[data-epic='{epic}'][data-status='{status}']")


async def cards_are(page: Page, wanted: set[str]) -> None:
    """Wait until the Board shows exactly these cards (references of stories and tasks), and no
    others."""
    await page.wait_for_function(
        """wanted => {
            const refs = [...document.querySelectorAll('.card')].map(e => e.dataset.ref).sort();
            return refs.length === wanted.length && refs.every((ref, at) => ref === wanted[at]);
        }""",
        arg=sorted(wanted),
        timeout=SLOW,
    )


async def shown_cards(page: Page, count: int) -> list[str]:
    """The references of the cards the Board shows (stories and tasks), in the order shown, once
    there are `count` of them."""
    await expect(page.locator(".card")).to_have_count(count, timeout=SLOW)
    refs: list[str] = await page.locator(".card").evaluate_all("els => els.map(e => e.dataset.ref)")
    return refs


async def create_in_browser(
    page: Page, kind: str, title: str, parent: str | None = None, *, body: str = ""
) -> str:
    """Make an epic, story or task with the Board's own form (New item), and give its reference
    (`parent` is the reference of its epic or story): the form opens the new item in the detail
    panel, whose title starts with its code, such as S-12."""
    await page.locator("#new-item").click()
    dialog = page.locator("dialog.modal")
    await dialog.locator("#new-kind").select_option(kind)
    if parent is not None:
        await dialog.locator("#new-parent").select_option(parent)
    await dialog.locator("#new-title").fill(title)
    if body:
        await dialog.locator("#new-body").fill(body)
    await dialog.get_by_role("button", name="Create").click()
    heading = page.locator("#card-title")
    await expect(heading).to_contain_text(title, timeout=SLOW)
    found = re.match(r"([EST]-\d+) ", await heading.inner_text())
    assert found, await heading.inner_text()
    return ref_from_code(found.group(1))


def on(text: str) -> datetime.date:
    return datetime.date.fromisoformat(text)


def database_footprint() -> tuple[str, str]:
    """Every table of the database, and how many rows each holds that a view could add to: a
    view is its address, so opening one must add none."""
    tables = run_sql(
        "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables "
        "WHERE table_schema = 'public'",
        plain=True,
    ).stdout.strip()
    rows = run_sql(
        "SELECT (SELECT count(*) FROM events) || ',' || (SELECT count(*) FROM nodes) || ',' || "
        "(SELECT count(*) FROM facts) || ',' || (SELECT count(*) FROM decisions) || ',' || "
        "(SELECT count(*) FROM claims)",
        plain=True,
    ).stdout.strip()
    return tables, rows


def bar_of(page: Page, ref: str) -> Locator:
    """The Roadmap's bar of an epic, story or task."""
    return page.locator(f".rm-bar[data-ref='{ref}']")


async def timeline(page: Page) -> dict[str, Any]:
    """Where the Roadmap starts, how many pixels a day takes, and today, as the page draws them."""
    track = page.locator(".rm-track")
    return {
        "origin": on(await track.get_attribute("data-origin") or ""),
        "ppd": float(await track.get_attribute("data-ppd") or "0"),
        "today": on(await track.get_attribute("data-today") or ""),
    }


async def roadmap_ids(page: Page, dated: int, undated: int) -> set[str]:
    """The references on the Roadmap's timeline and in its tray, once the page says how many of
    each."""
    await expect(page.locator("#count")).to_contain_text(
        re.compile(rf"^{dated} items? on the timeline, {undated} undated"), timeout=SLOW
    )
    bars: list[str] = await page.locator(".rm-bar").evaluate_all(
        "els => els.map(e => e.dataset.ref)"
    )
    tray: list[str] = await page.locator(".tray-item").evaluate_all(
        "els => els.map(e => e.dataset.ref)"
    )
    assert len(bars) == dated and len(tray) == undated
    return set(bars) | set(tray)


async def drag_bar(page: Page, ref: str, days: float, grip: str | None = None) -> None:
    """Drag a Roadmap bar (or one of its ends: grip 'start' or 'end') sideways by some days."""
    ppd = (await timeline(page))["ppd"]
    part = bar_of(page, ref)
    if grip:
        part = part.locator(f".rm-handle.{grip}")
    # The timeline pans inside its own region, and an open detail panel narrows it: a bar may be
    # out of sight until it is scrolled to.
    await part.scroll_into_view_if_needed()
    box = await part.bounding_box()
    assert box is not None
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + days * ppd / 2, y, steps=4)
    await page.mouse.move(x + days * ppd, y, steps=4)
    await page.mouse.up()


# -- the page of one epic, story or task ---------------------------------------------------------


def item_address(ref: str) -> str:
    """The address of the page of an epic, story or task: '#/item?item=story:12'."""
    return f"#/item?item={ref}"


async def open_item(w: Watched, ref: str) -> None:
    """Open the page of an epic, story or task and wait until it shows that item."""
    await w.open(item_address(ref))
    await expect(w.page.locator("#item-page")).to_have_attribute("data-ref", ref, timeout=SLOW)


def item_row(page: Page, ref: str) -> Locator:
    """The row of the item page's table that holds a story or a task."""
    return page.locator(f"#item-table tr#row-{ref_slug(ref)}")


async def item_rows(page: Page) -> list[str]:
    """The references in the item page's table, in the order shown."""
    refs: list[str] = await page.locator("#item-table tbody tr").evaluate_all(
        "els => els.map(e => e.dataset.ref)"
    )
    return refs


# -- Search and possible duplicates (the search drawer) and links -----------------------------


async def asked(client: Client, query: str, mode: str = "both") -> list[tuple[str, str, str]]:
    """What `search` gives for this, every page of it: (type, reference, how it matched)."""
    found: list[tuple[str, str, str]] = []
    offset = 0
    while True:
        page = await ok(client, "search", query=query, mode=mode, limit=200, offset=offset)
        found.extend((item["item_type"], item["ref"], item["matched"]) for item in page["items"])
        if page["next_offset"] is None:
            return found
        offset = page["next_offset"]


async def search_for(page: Page, query: str, mode: str = "both") -> None:
    """Type a search into the search drawer and press its button. With the drawer closed, the box
    of the top bar opens it first, as a person would."""
    if not await page.locator("#search-box").count():
        await page.locator("#global-search").fill(query)
        await page.locator("#global-search-go").click()
        await expect(page.locator("#search-box")).to_be_visible(timeout=SLOW)
    await page.locator("#search-box").fill(query)
    await page.locator("#search-mode").select_option(mode)
    await page.locator("#search-go").click()


async def listed(page: Page, count: int) -> list[tuple[str, str, str]]:
    """The results the search drawer lists, in the order shown, once it says how many there are:
    (type, reference, how it matched)."""
    await expect(page.locator("#search-count")).to_have_text(
        re.compile(rf"^{count} results?\b"), timeout=SLOW
    )
    rows: list[list[str]] = await page.locator("#search-results li.result").evaluate_all(
        "els => els.map(e => [e.dataset.type, e.dataset.ref, e.dataset.matched])"
    )
    return [(kind, ref, how) for kind, ref, how in rows]


async def facet(page: Page, name: str) -> dict[str, int]:
    """One group of filter buttons of the search drawer: the label of each and its count."""
    texts = await page.locator(f"fieldset.facet[data-facet={name}] button").all_inner_texts()
    found: dict[str, int] = {}
    for text in texts:
        label, count = re.fullmatch(r"(.*) \((\d+)\)", text.strip()).groups()  # type: ignore[union-attr]
        found[label] = int(count)
    return found


async def twin_epics(
    client: Client, pairs: list[tuple[str, str]], body: str = ""
) -> tuple[str, str, list[str], list[str]]:
    """Two epics holding the stories of each pair, one side in each, and the index up to date.
    Gives the references of the two epics and of the stories of each side, in the order of the
    pairs."""
    word = unique_word()
    epic_a = await made(client, "epic", f"Overlap A {word}")
    epic_b = await made(client, "epic", f"Overlap B {word}")
    firsts = [await made(client, "story", one, epic_a, body=body) for one, _ in pairs]
    seconds = [await made(client, "story", other, epic_b, body=body) for _, other in pairs]
    await reindexed(client)
    return epic_a, epic_b, firsts, seconds


async def every_overlap(client: Client, **arguments: Any) -> dict[str, Any]:
    """`find_overlaps`, every page of it, put together."""
    page = await ok(client, "find_overlaps", limit=200, **arguments)
    items = list(page["items"])
    while page["next_offset"] is not None:
        page = await ok(client, "find_overlaps", limit=200, offset=page["next_offset"], **arguments)
        items.extend(page["items"])
    return {**page, "items": items}


async def pairs_on(page: Page) -> list[tuple[str, str, float]]:
    """Every pair the possible-duplicates view of the search drawer lists, in the order shown,
    asking for more until no more: (reference, reference, how alike)."""
    await expect(page.locator("#dup-count")).to_contain_text(re.compile(r"pairs?\b"), timeout=SLOW)
    for _ in range(100):
        more = page.locator("#dup-more")
        if await more.count() == 0:
            break
        count = await page.locator("li.pair").count()
        await more.click()
        await expect(page.locator("li.pair")).not_to_have_count(count, timeout=SLOW)
    rows: list[list[str]] = await page.locator("li.pair").evaluate_all(
        "els => els.map(e => [e.dataset.first, e.dataset.second, e.dataset.similarity])"
    )
    return [(first, second, float(score)) for first, second, score in rows]


async def links_of(client: Client, *nodes: str) -> dict[str, list[tuple[str, str, str]]]:
    """Every link at each node (by reference), as (source, target, type), to compare before and
    after."""
    found: dict[str, list[tuple[str, str, str]]] = {}
    for node in nodes:
        page = await ok(client, "list_links", node=node, limit=200)
        found[node] = sorted(
            (link["source"], link["target"], link["link_type"]) for link in page["items"]
        )
    return found


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


def api_path(board: str, rest: str = "") -> str:
    """The board's API address for the project whose board is `board`: for '/tool/list_nodes' it
    is /board/api/projects/<id>/tool/list_nodes."""
    return f"/board/api/projects/{project_at(board).id}{rest}"


def board_call(
    board: str,
    method: str,
    path: str,
    body: Any = None,
    *,
    person: Person | None = None,
    **more: Any,
) -> Reply:
    """A request to the board's API (a whole path, see api_path) as a person who is signed in: by
    default the owner of the project whose board is `board`, with a session of its own, carrying
    the session's secret and the server's own address as the origin, as the board's page does."""
    who = person or project_at(board).owner.sign_in_again()
    return who.call(method, path, body, **more)
