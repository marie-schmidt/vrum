"""Helpers for the tests that drive the board in a real headless browser, against the real
containers (see ./check.sh). The browser is Playwright's Chromium, a development dependency only.

Everything a person opens is a page: the four views, Search, Activity, the coverage report, the
settings pages, and the page of each epic, story, task, acceptance criterion and note
(`#/item?item=<ref>&from=<page>`). Nothing opens beside a page, so a test goes to a page
(`open_item`, `open_search`, `open_activity`, `go`) and reads what the page shows;
`no_panel_or_drawer` proves that nothing of the old side panel or the drawers is left."""

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
from urllib.parse import quote, unquote, urlparse

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

from tests.helpers import all_nodes, ok, ref_from_code, ref_of, reindexed, run_sql, unique_word
from tests.world import SESSION_COOKIE, Person, Project, Reply, project_at, the_project

# Every request of the board has to go to the server it came from.
VIEWPORT = {"width": 1500, "height": 1500}
SLOW = 15_000  # milliseconds a page may take to show what a change should bring
# Every page of the board, as its address names it: the four views of the work, Search, Activity
# and the coverage report (which, with the views, are the pages a record page can say it was reached
# `from`), and the pages that the Settings menu opens. The page of one record is `item`: it is in no
# menu, and its address names the record.
VIEWS = ["board", "graph", "roadmap", "lists"]
SETTINGS = ["users", "definition", "tools", "status"]
FROM_PAGES = [*VIEWS, "search", "activity", "coverage"]
PAGES = [*FROM_PAGES, *SETTINGS]
# The heading (the page's only h1) of each page that has a fixed one.
HEADINGS = {
    "board": "Board",
    "graph": "Graph",
    "roadmap": "Roadmap",
    "lists": "Lists",
    "search": "Search",
    "activity": "Activity",
    "coverage": "Coverage",
    "users": "Users",
    "definition": "Definition of done",
    "tools": "Tools",
    "status": "Status and export",
}
# What the board's old addresses named, which are not pages any more (and have no alias).
GONE = ["home", "table", "outline", "rules", "memory", "overlaps", "ask", "access"]
# The five kinds of record that have a page, in the words their addresses use.
KINDS = ["epic", "story", "task", "rule", "note"]
WORK_KINDS = KINDS[:3]
# What the board writes for each stored status of a note and of an acceptance criterion. A note has
# one status (the stored word `superseded` reads Replaced); an acceptance criterion is open, met or
# dropped.
NOTE_STATUS_WORDS = {
    "unconfirmed": "Unconfirmed",
    "confirmed": "Confirmed",
    "superseded": "Replaced",
    "withdrawn": "Withdrawn",
}
REQUIREMENT_STATUS_WORDS = {"open": "Open", "met": "Met", "dropped": "Dropped"}


def board_url(mcp_url: str, project: Project | None = None) -> str:
    """The address of a project's board (by default the shared test project's) on the server whose
    MCP endpoint is `mcp_url`."""
    one = project or the_project()
    return f"{mcp_url.removesuffix('/mcp')}/board/p/{one.id}"


def signed_in_name(board: str) -> str:
    """The name `browser_page` is signed in with on this project's board: the project's owner. A
    change made on the board is recorded as that person, whatever name the page was given."""
    return project_at(board).owner.name


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
        """Go to a page of the board, such as '#/graph?scope=all', and wait for it to load. On a
        page that is already open (the part before the # is the same) this is a change of address
        inside it, as following a link is, so what the page keeps in memory (the view a record page
        goes back to) stays. It waits for the shell, not for the sidebar's links: on a narrow screen
        the sidebar is off-canvas and so not visible until the menu button opens it."""
        await self.page.goto(f"{self.board}{address}")
        await self.page.wait_for_selector("#app[data-shell=project], .entry", timeout=15_000)

    async def go(self, address: str) -> None:
        """Change the address after the # without loading the page again, as a link does."""
        await self.page.evaluate("(hash) => { location.hash = hash; }", address)


@asynccontextmanager
async def browser_page(
    board: str,
    *,
    token: str | None,
    name: str | None = "Tester",
    keep_bodies: bool = False,
    person: Person | None = None,
) -> AsyncIterator[Watched]:
    """A headless browser on a project's board (`board`, from board_url). With a `token`, which is
    the shared test project's agent key from the_token(), it starts signed in as that project's
    owner, with a session of its own, as a person who has used the board before would be; with
    `person` (an editor or a viewer who joined the project) it starts signed in as them instead;
    with neither it is signed out and shows the sign-in. `name` is what the board records changes
    with. `keep_bodies` keeps the text of every response, for a test that searches them all."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context(viewport=VIEWPORT)  # type: ignore[arg-type]
            if token is not None or person is not None:
                signed = (person or project_at(board).owner).sign_in_again()
                await context.add_cookies(
                    [
                        {
                            "name": SESSION_COOKIE,
                            "value": signed.cookie,
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
    (`parent` is the reference of its epic or story): the form goes to the page of the new item,
    whose title starts with its code, such as S-12."""
    await page.locator("#new-item").click()
    dialog = page.locator("dialog.modal")
    await dialog.locator("#new-kind").select_option(kind)
    if parent is not None:
        await dialog.locator("#new-parent").select_option(parent)
    await dialog.locator("#new-title").fill(title)
    if body:
        await dialog.locator("#new-body").fill(body)
    await dialog.get_by_role("button", name="Create").click()
    heading = page.locator("#item-title")
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
        "(SELECT count(*) FROM notes) || ',' || (SELECT count(*) FROM claims)",
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


async def roadmap_ids(page: Page, dated: int) -> set[str]:
    """The references on the Roadmap's timeline, once the page says how many items it holds."""
    await expect(page.locator("#count")).to_contain_text(
        re.compile(rf"^{dated} items? on the timeline[.,;]"), timeout=SLOW
    )
    bars: list[str] = await page.locator(".rm-bar").evaluate_all(
        "els => els.map(e => e.dataset.ref)"
    )
    assert len(bars) == dated
    return set(bars)


async def drag_bar(page: Page, ref: str, days: float, grip: str | None = None) -> None:
    """Drag a Roadmap bar (or one of its ends: grip 'start' or 'end') sideways by some days."""
    ppd = (await timeline(page))["ppd"]
    part = bar_of(page, ref)
    if grip:
        part = part.locator(f".rm-handle.{grip}")
    # The timeline pans inside its own region: a bar may be out of sight until it is scrolled to.
    await part.scroll_into_view_if_needed()
    box = await part.bounding_box()
    assert box is not None
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + days * ppd / 2, y, steps=4)
    await page.mouse.move(x + days * ppd, y, steps=4)
    await page.mouse.up()


# -- pages: going to one, and knowing which one is on show --------------------------------------


def url_choices(address: str) -> tuple[str, dict[str, str]]:
    """The page an address after the # names and the choices it holds (read as text, so `%20` is
    a space): '#/item?item=story:12' is ('item', {'item': 'story:12'})."""
    name, _, query = address.removeprefix("#/").partition("?")
    found: dict[str, str] = {}
    for pair in query.split("&") if query else []:
        key, _, value = pair.partition("=")
        found[key] = unquote(value)
    return name, found


async def current_address(page: Page) -> tuple[str, dict[str, str]]:
    """The page the address of the browser names now, and the choices it holds."""
    return url_choices(await page.evaluate("location.hash"))


async def on_page(page: Page, name: str) -> None:
    """Wait until the page the address names is the one on show: its heading says so. The page of a
    record (`item`) has the record's own heading, so for it this waits for the page itself."""
    if name == "item":
        await expect(page.locator("#item-page")).to_be_visible(timeout=SLOW)
    else:
        await expect(page.locator("main#page h1").first).to_have_text(HEADINGS[name], timeout=SLOW)
    assert (await page.evaluate("location.hash")).startswith(f"#/{name}")


async def page_drawn(page: Page) -> None:
    """Wait until nothing in the page says it is still loading."""
    await page.wait_for_function(
        "() => ![...document.querySelectorAll('#page .loading')].some(e => e.checkVisibility())",
        timeout=SLOW,
    )


async def go(page: Page, board: str, address: str) -> None:
    """Load a board address afresh, as a bookmark or a second browser tab does (a real page load,
    even when only the part after the # differs), and wait for the shell and for what the page
    asks the server to be drawn. `address` is such as '#/search?search=login'."""
    await page.goto(f"{board}?again={unique_word()}{address}")
    await page.wait_for_selector("#app[data-shell=project], .entry", timeout=SLOW)
    await page_drawn(page)


GONE_FROM_THE_PAGE = [
    "#panel",
    "#card-title",
    "#record-title",
    "#drawer",
    "[id^='drawer-']",
    "[data-drawer]",
    "[class*='drawer-']",
    ".with-panel",
    ".with-drawer",
    ".quick-look",
    ".cell-look",
    ".gv-quick",
    "#sheet-scrim",
    ".card.selected",
    ".lane.selected",
]


async def no_panel_or_drawer(page: Page) -> None:
    """The page has nothing of the old side panel or the drawers: none of their elements, no
    "Quick look" or "Open full page" button, and no `drawer` or `panel` in the address (and no `sel`
    unless the page is the Graph, where it only ever means the chosen dot)."""
    for selector in GONE_FROM_THE_PAGE:
        assert await page.locator(selector).count() == 0, f"{selector} is on the page"
    words = re.compile(r"^(Quick look|Open full page)$")
    assert await page.get_by_role("button", name=words).count() == 0
    assert await page.get_by_role("link", name=words).count() == 0
    name, choices = await current_address(page)
    for key in ("drawer", "panel") + (() if name in ("graph", "lists") else ("sel",)):
        assert key not in choices, f"{key}= is in the address {choices}"


# -- the page of one epic, story, task, acceptance criterion or note ------------------------------


def item_address(ref: str, from_page: str | None = None) -> str:
    """The address of the page of a record: '#/item?item=story:12', and with `from_page` (board,
    graph, roadmap, search, activity or coverage) '#/item?item=story:12&from=graph'. `ref` is
    epic:3, story:12, task:40, rule:3 or note:7."""
    return f"#/item?item={ref}" + (f"&from={from_page}" if from_page else "")


def item_page(page: Page) -> Locator:
    """The page of a record, as a place to look inside (`#item-page`)."""
    return page.locator("#item-page")


async def item_loaded(page: Page, ref: str) -> None:
    """Wait until the page of this record is on show and has drawn its details (an epic, story or
    task its card, an acceptance criterion or note its record) and its title."""
    await expect(item_page(page)).to_have_attribute("data-ref", ref, timeout=SLOW)
    await expect(
        page.locator(f"#item-details :is(article[data-ref='{ref}'], article[data-record='{ref}'])")
    ).to_be_visible(timeout=SLOW)
    await expect(page.locator("#item-title")).not_to_have_text("", timeout=SLOW)


async def open_item(w: Watched, ref: str, from_page: str = "board", *, loaded: bool = True) -> None:
    """Go to the page of a record, reached from `from_page` (the page the person is "in": board,
    graph, roadmap, search, activity or coverage), and wait until it has drawn its details. With
    `loaded=False` it waits only for the page itself, which is what a record that is not there (it
    says so) or one still being read needs."""
    await w.open(item_address(ref, from_page))
    if loaded:
        await item_loaded(w.page, ref)
    else:
        await expect(item_page(w.page)).to_be_visible(timeout=SLOW)


async def item_notice(page: Page) -> str:
    """What a record page says when it has nothing to show (the address names nothing, or the
    record is not there), in its words."""
    state = page.locator("#item-page.item-state")
    await expect(state).to_be_visible(timeout=SLOW)
    return " ".join((await state.inner_text()).split())


def item_row(page: Page, ref: str) -> Locator:
    """The row of the item page's table that holds a story or a task."""
    return page.locator(f"#item-table tr#row-{ref_slug(ref)}")


def part(page: Page, name: str) -> Locator:
    """A section of the record's page by its `data-part` (details, requirements (the acceptance
    criteria), definition, context, links,
    history, record, why, confirmation, concerns, replaces, mentions, lifecycle...)."""
    return page.locator(f"#item-page [data-part='{name}']")


def detail(page: Page, ref: str) -> Locator:
    """The article that draws one record's details on its page: the card of an epic, story or
    task, or the record of an acceptance criterion or note."""
    return page.locator(
        f"#item-details :is(article.card-detail[data-ref='{ref}'], article[data-record='{ref}'])"
    )


async def record_text(page: Page) -> str:
    """The words the record's page shows for the record itself (its details and sections, not the
    sidebar or the top bar), a run of white space being one space."""
    return " ".join((await page.locator("#item-page").inner_text()).split())


async def crumbs(page: Page) -> list[str]:
    """The breadcrumb, in order, as a person reads it: ['Projects', 'My project', 'Board',
    'E-3 An epic', 'S-12 A story']."""
    texts: list[str] = await page.locator("nav.crumbs li").evaluate_all(
        "els => els.map(e => e.textContent.replace(/\\s+/g, ' ').trim())"
    )
    return texts


async def crumb_links(page: Page) -> list[tuple[str, str | None]]:
    """The breadcrumb as (words, address of the link or None when it is plain text): the last one,
    the page itself, is never a link."""
    rows: list[list[str | None]] = await page.locator("nav.crumbs li").evaluate_all(
        """els => els.map(e => [e.textContent.replace(/\\s+/g, ' ').trim(),
            e.querySelector('a') ? e.querySelector('a').getAttribute('href') : null])"""
    )
    return [(str(words), href) for words, href in rows]


def view_crumb(page: Page) -> Locator:
    """The breadcrumb's link back to the view (or Search, Activity, Coverage) the record page was
    reached from."""
    return page.locator("nav.crumbs li.crumb-view a")


async def sidebar_current(page: Page) -> list[str]:
    """The views (and Activity) the sidebar marks as where the person is: normally exactly one."""
    pages: list[str] = await page.locator(
        "#main-nav a[aria-current=page], #activity-open[aria-current=page]"
    ).evaluate_all("els => els.map(e => e.dataset.page)")
    return pages


async def record_rows(
    page: Page, table_kind: str, section: str | None = None
) -> list[dict[str, Any]]:
    """The rows of one kind of table on a record's page, in the order drawn (hidden ones too, such
    as those in a closed "Earlier" or "Project-wide" section: `visible` says). `table_kind` is
    requirements (acceptance criteria; on a task, its story's too), notes, concerns (what a note
    concerns), replaces (what it replaced and what
    replaced it), children (the tasks of a story, the stories of an epic) or relations (what a
    task needs first and what needs it). `section` keeps only the rows of one recall section
    (notes, past_notes, project_wide_notes). Each row is a dict: kind, id, number, ref, status
    (a note's: unconfirmed, confirmed, superseded or withdrawn; a criterion's: open, met or
    dropped), project_wide, why (what recall says), from_ref (what a note came down from),
    relation, node, section, visible and text; what a table does not carry is None."""
    selector = {
        "requirements": "#item-page table.requirements-table tr.record-line",
        "notes": "#item-page table.notes-table tr.record-line",
        "concerns": "#item-page table.concerns-table tr.record-line",
        "replaces": "#item-page table.replacement-table tr.record-line",
        "children": "#item-page #item-table tbody tr",
        "relations": "#item-page #relations-table tbody tr",
    }[table_kind]
    rows: list[dict[str, Any]] = await page.evaluate(
        """(selector) => [...document.querySelectorAll(selector)].map((row) => {
            const d = row.dataset;
            const part = row.closest('[data-section]');
            return {
                kind: d.kind ?? null,
                id: d.id === undefined ? null : Number(d.id),
                number: d.number === undefined ? null : Number(d.number),
                ref: d.ref ?? null,
                status: d.status ?? null,
                project_wide: d.projectWide === undefined ? null : d.projectWide === 'true',
                why: d.why || null,
                from_ref: d.from || null,
                relation: d.relation ?? null,
                node: d.node ?? null,
                section: part ? part.dataset.section : null,
                visible: row.checkVisibility({ checkVisibilityCSS: true }),
                text: (row.textContent || '').replace(/\\s+/g, ' ').trim(),
            };
        })""",
        selector,
    )
    return [row for row in rows if section is None or row["section"] == section]


def record_row(page: Page, ref: str) -> Locator:
    """The row of a record in a table on the record's page (a note or criterion, or what a note
    concerns), by its reference: note:7."""
    return page.locator(f"#item-page tr.record-line[data-ref='{ref}']")


async def disclosure(page: Page, name: str) -> dict[str, Any] | None:
    """The closed-or-open section of recall that holds earlier or project-wide notes (`name` is
    past_notes or project_wide_notes): whether it is open and how many rows it holds, or None when
    the page draws none (nothing to show in it)."""
    found: dict[str, Any] | None = await page.evaluate(
        """(name) => {
            const e = document.querySelector(
                `#item-page details.recall-disclosure[data-disclosure="${name}"]`);
            if (!e) return null;
            return { open: e.open, count: e.querySelectorAll('tr.record-line').length };
        }""",
        name,
    )
    return found


async def open_record(page: Page, kind: str, number: int) -> None:
    """Go to the page of an acceptance criterion (kind `rule` or `requirement`) or a note, or of an
    epic, story or task (kind `epic`, `story` or `task`), from the page that is on show: by the
    row or link that names it when there is one in sight (as a person would), by the address
    otherwise, staying "in" the same view. It returns once the record's page has drawn."""
    kind = "rule" if kind == "requirement" else kind
    ref = ref_of(kind, number)
    row = page.locator(f"#item-page tr.record-line[data-ref='{ref}']").first
    if await row.count() and await row.is_visible():
        await row.locator("a.record-title, a.item-code-link").first.click()
    else:
        name, choices = await current_address(page)
        from_page = choices.get("from", "board") if name == "item" else name
        await page.evaluate(
            "(hash) => { location.hash = hash; }",
            item_address(ref, from_page if from_page in FROM_PAGES else "board"),
        )
    await item_loaded(page, ref)


async def pager(page: Page) -> dict[str, Any]:
    """The previous and next controls and the position of an epic, story or task page: previous
    and next are each None (no such control) or {disabled, label, code} (`code` is the sibling's
    code, None at an end), and `position` is such as '2 of 5', with `at` and `of` as numbers."""
    found: dict[str, Any] = await page.evaluate(
        """() => {
            const button = (id) => {
                const e = document.getElementById(id);
                if (!e) return null;
                const code = e.querySelector('.item-code');
                return { disabled: e.disabled, label: e.getAttribute('aria-label'),
                    code: code ? code.textContent.trim() : null };
            };
            const position = document.getElementById('item-position');
            return { previous: button('item-prev'), next: button('item-next'),
                position: position ? position.textContent.trim() : null };
        }"""
    )
    shown = re.fullmatch(r"(\d+) of (\d+)", found["position"] or "")
    found["at"], found["of"] = (int(shown.group(1)), int(shown.group(2))) if shown else (None, None)
    return found


async def pager_go(page: Page, direction: str) -> str:
    """Press the previous or next button of an epic, story or task page (`direction` is `previous`
    or `next`), wait until the neighbour's page has drawn, and give its reference."""
    before = await item_page(page).get_attribute("data-ref")
    await page.locator("#item-prev" if direction == "previous" else "#item-next").click()
    await expect(item_page(page)).not_to_have_attribute("data-ref", before or "", timeout=SLOW)
    ref = await item_page(page).get_attribute("data-ref")
    assert ref is not None
    await item_loaded(page, ref)
    return ref


async def siblings(client: Client, ref: str) -> list[str]:
    """The references the pager of this epic, story or task page moves through, as the server lists
    them: every epic of the project, the stories of its epic, or the tasks of its story."""
    node = await ok(client, "get_node", node=ref)
    found = (
        await all_nodes(client, kind="epic")
        if node["kind"] == "epic"
        else await all_nodes(client, parent=node["parent"])
    )
    return [one["ref"] for one in found]


async def item_what(page: Page) -> str:
    """The line under the title of a record's page, in its words: 'Story in E-3 An epic', 'It is not
    placed on a story or task yet.', 'Decided by Marie on 2026-11-02.'."""
    return " ".join((await page.locator("#item-what").inner_text()).split())


async def coverage_rows(page: Page) -> dict[str, list[str]]:
    """What the coverage report page lists now: `unplaced` (the references of the acceptance
    criteria placed nowhere, rule:3) and `bare` (the references of the stories with none), in the
    order
    shown."""
    found: dict[str, list[str]] = await page.evaluate(
        """() => ({
            unplaced: [...document.querySelectorAll('#unplaced-rules tr[data-rule]')]
                .map(e => e.dataset.ref),
            bare: [...document.querySelectorAll('#stories-without-rules tr[data-story]')]
                .map(e => e.dataset.ref),
        })"""
    )
    return found


async def open_card(page: Page, ref: str) -> None:
    """Open the page of a story or task from its card on the Board, with the card's title (a link),
    and wait until the page has drawn."""
    await card(page, ref).locator("h3.card-title a").click()
    await item_loaded(page, ref)


async def open_lane(page: Page, epic: str) -> None:
    """Open the page of an epic from the title of its lane on the Board, and wait until it has
    drawn."""
    await lane(page, epic).locator("a.lane-title").click()
    await item_loaded(page, epic)


async def open_bar(page: Page, ref: str) -> None:
    """Open the page of an item from its bar on the Roadmap (a click that is not a drag), and wait
    until the page has drawn."""
    bar = bar_of(page, ref)
    await bar.scroll_into_view_if_needed()
    await bar.click()
    await item_loaded(page, ref)


async def jump_to(page: Page, words: str) -> None:
    """Use the quick switcher (Ctrl+K): type these words (a title, a code such as S-12, or a view's
    name) and press Enter on the first option, as a person would. The caller waits for what the
    jump should bring (the page of a record: item_loaded)."""
    await page.keyboard.press("Control+k")
    await expect(page.locator("#switcher-input")).to_be_focused(timeout=SLOW)
    await page.locator("#switcher-input").fill(words)
    await expect(page.locator("#switcher-list .switcher-option").first).to_be_visible(timeout=SLOW)
    await page.keyboard.press("Enter")
    await expect(page.locator("#switcher")).to_have_count(0, timeout=SLOW)


async def open_editor(page: Page, where: str = "") -> None:
    """Open the details of an epic, story, task or criterion for editing, as a person does with
    the Edit button, and wait for the form. It does nothing when the form is already open. `where`
    narrows the look to one element when the page holds more than one form."""
    scope = page.locator(where) if where else page.locator("#item-page")
    button = scope.locator("button.edit-details")
    # The details may still be loading: wait for the button, or for the form if it is open.
    await expect(scope.locator("button.edit-details, form.edit").first).to_be_visible(timeout=SLOW)
    if await button.count():
        await button.first.click()
    await expect(scope.locator("form.edit input[type=text]").first).to_be_visible(timeout=SLOW)


async def item_rows(page: Page) -> list[str]:
    """The references in the item page's table, in the order shown."""
    refs: list[str] = await page.locator("#item-table tbody tr").evaluate_all(
        "els => els.map(e => e.dataset.ref)"
    )
    return refs


# -- the sidebar's navigator: the epics, their stories and their tasks as a tree -----------------


async def open_sidebar(page: Page) -> None:
    """Open the sidebar when it is behind the menu button (a window under 1100 pixels); it does
    nothing when the sidebar is a column of the page."""
    toggle = page.locator("#sidebar-toggle")
    if await toggle.is_visible() and await page.locator("#sidebar.open").count() == 0:
        await toggle.click()
        await expect(page.locator("#sidebar.open")).to_be_visible(timeout=SLOW)


def tree_item(page: Page, ref: str) -> Locator:
    """The tree item (`li[role=treeitem]`) of an epic, story or task in the sidebar."""
    return page.locator(f"#epic-list li[role=treeitem][data-key='{ref}']")


async def tree_rows(page: Page) -> list[dict[str, Any]]:
    """The rows the sidebar's tree draws now, top to bottom (only what is open is drawn): ref,
    level (1 epics, 2 stories, 3 tasks), expanded (None for a leaf), current (the page on show),
    code, title, position and size (among its siblings)."""
    rows: list[dict[str, Any]] = await page.locator("#epic-list li[role=treeitem]").evaluate_all(
        """els => els.map((li) => {
            const row = li.querySelector(':scope > .tree-row');
            const link = row.querySelector('a.tree-link');
            return {
                ref: li.dataset.key,
                level: Number(li.getAttribute('aria-level')),
                expanded: li.hasAttribute('aria-expanded')
                    ? li.getAttribute('aria-expanded') === 'true' : null,
                current: link.getAttribute('aria-current') === 'page',
                code: row.querySelector('.tree-code').textContent.trim(),
                title: row.querySelector('.tree-title').textContent.trim(),
                position: Number(li.getAttribute('aria-posinset')),
                size: Number(li.getAttribute('aria-setsize')),
            };
        })"""
    )
    return rows


async def tree_expand(page: Page, ref: str, expand: bool = True) -> None:
    """Open (or with `expand=False` close) the branch of an epic or story in the tree with its
    chevron, and wait until the tree says so. It does nothing when it is already so."""
    await open_sidebar(page)
    item = tree_item(page, ref)
    state = "true" if expand else "false"
    if await item.get_attribute("aria-expanded") != state:
        await item.locator(":scope > .tree-row > button.tree-twisty").click()
    await expect(item).to_have_attribute("aria-expanded", state, timeout=SLOW)


async def tree_open(page: Page, ref: str) -> None:
    """Open the page of an epic, story or task by its title in the sidebar's tree, and wait until
    it has drawn (the branch must be open: see tree_expand)."""
    await open_sidebar(page)
    await tree_item(page, ref).locator(":scope > .tree-row > a.tree-link").click()
    await item_loaded(page, ref)


# -- Search, possible duplicates and Activity: pages ---------------------------------------------


async def open_search(page: Page, query: str = "", **choices: str) -> None:
    """Go to the Search page (`#/search`), with these words and any other choice of its address
    (search_type, search_status, search_epic...), and wait for its box. Nothing toggles: this is a
    page, and going to it again is going to it. A board search always asks for words and meaning
    together."""
    address = {"search": query or None, **choices}
    await page.evaluate(
        "(hash) => { location.hash = hash; }", search_address({**address, "dups": None})
    )
    await expect(page.locator("#search-box")).to_be_visible(timeout=SLOW)


async def open_duplicates(page: Page, **choices: str) -> None:
    """Go to Possible duplicates, the second tab of the Search page (`#/search?dups=1`), with any
    choice of its address (dup_type, dup_min, dup_epics, dup_unlinked), and wait for its count."""
    await page.evaluate(
        "(hash) => { location.hash = hash; }", search_address({"dups": "1", **choices})
    )
    await expect(page.locator("#dup-count")).to_be_visible(timeout=SLOW)


def search_address(choices: dict[str, str | None]) -> str:
    """The address of the Search page with these choices, the ones that are None left out."""
    query = "&".join(f"{key}={quote(str(value))}" for key, value in choices.items() if value)
    return f"#/search{f'?{query}' if query else ''}"


async def open_activity(page: Page, **filters: str) -> None:
    """Go to the Activity page (`#/activity`), with any of its filters (act_actor, act_kind,
    act_item, act_from, act_to), and wait until the feed has been read."""
    query = "&".join(f"{key}={quote(str(value))}" for key, value in filters.items())
    address = f"#/activity?{query}" if query else "#/activity"
    await page.evaluate("(hash) => { location.hash = hash; }", address)
    await expect(page.locator("#activity-count")).not_to_have_text(
        re.compile(r"^(Reading the changes…)?$"), timeout=SLOW
    )


async def open_coverage(page: Page) -> None:
    """Go to the coverage report (`#/coverage`) and wait until it has drawn its numbers."""
    await page.evaluate("() => { location.hash = '#/coverage'; }")
    await expect(page.locator("#coverage-summary")).to_have_attribute(
        "data-unplaced", re.compile(r"^\d+$"), timeout=SLOW
    )


# -- Search and possible duplicates, and links -----------------------------------------------------


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


async def search_for(page: Page, query: str) -> None:
    """Type a search into the Search page and press its button. When the Search page is not on
    show, the box of the top bar takes the person there first, as it does for a person."""
    if not await page.locator("#search-box").count():
        await page.locator("#global-search").fill(query)
        await page.locator("#global-search-go").click()
        await expect(page.locator("#search-box")).to_be_visible(timeout=SLOW)
    await page.locator("#search-box").fill(query)
    await page.locator("#search-go").click()


async def listed(page: Page, count: int) -> list[tuple[str, str, str]]:
    """The results the Search page lists, in the order shown, once it says how many there are:
    (type, reference, how it matched)."""
    await expect(page.locator("#search-count")).to_have_text(
        re.compile(rf"^{count} results?\b"), timeout=SLOW
    )
    rows: list[list[str]] = await page.locator("#search-results tr.result").evaluate_all(
        "els => els.map(e => [e.dataset.type, e.dataset.ref, e.dataset.matched])"
    )
    return [(kind, ref, how) for kind, ref, how in rows]


async def facet(page: Page, name: str) -> dict[str, int]:
    """One group of filter buttons of the Search page (type, status or epic): the label of each and
    its count."""
    rows: list[list[str]] = await page.locator(
        f"#search-facets fieldset.facet[data-facet={name}] button.facet-button"
    ).evaluate_all(
        "els => els.map(e => [e.querySelector('.facet-label').textContent.trim(), e.dataset.count])"
    )
    return {label: int(count) for label, count in rows}


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
    """Every pair Possible duplicates (the second tab of the Search page) lists, in the order shown,
    asking for more until no more: (reference, reference, how alike)."""
    await expect(page.locator("#dup-count")).to_contain_text(re.compile(r"pairs?\b"), timeout=SLOW)
    for _ in range(100):
        more = page.locator("#dup-more")
        if await more.count() == 0:
            break
        count = await page.locator("#dup-pairs tr.pair").count()
        await more.click()
        await expect(page.locator("#dup-pairs tr.pair")).not_to_have_count(count, timeout=SLOW)
    rows: list[list[str]] = await page.locator("#dup-pairs tr.pair").evaluate_all(
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
