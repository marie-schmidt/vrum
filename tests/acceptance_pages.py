# ruff: noqa: E501
# The tables of ways to open a record and of pages to visit, and the audits that run in the page
# itself, are written one entry to a line; their lines are not wrapped to Python's width.
"""Chapter 18 of the acceptance scenario: pages (M20). There is no detail panel and no drawer: every
epic, story, task, acceptance criterion and note has a full page of its own, Search, Activity and
the acceptance criteria coverage report are pages, the path keeps the view a person came from, and a
person can move through the content (previous and next, up, down, the back arrow, the sidebar's
tree) without going back to a list.

The chapter makes its own account, project and key, so it needs nothing from the chapters before
it. A real MCP client fills a small project (four epics, one of them with nine long project-wide
notes, stories, tasks, acceptance criteria placed and not, notes that are confirmed, replaced and
withdrawn, dependencies, claims held by two agents and by the person who owns the project, and a
handoff note), and a real headless browser
shows that, compared with what the tools say: no panel or drawer is on any page; every kind of
record opens its own page from every place that names it, under the view it came from; previous and
next walk exactly the siblings `list_nodes` gives, and the sidebar's tree is `list_nodes`; the
tables of what concerns an epic span the page; the Graph's Around opens the page of what it looks
at; Search, Possible duplicates, Activity (changes and who is working) and the coverage report
equal their tools; and no page scrolls sideways at 390, 768, 1280 and 1600 pixels in both themes."""

import asyncio
import contextlib
import datetime
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl

from mcp import Client
from playwright.async_api import Error as BrowserError
from playwright.async_api import Locator, Page, expect

from tests.acceptance_scenario import Failed, chapter, clip, proof
from tests.board_context import board_refusal
from tests.board_helpers import (
    SLOW,
    Watched,
    board_url,
    browser_page,
    crumbs,
    drag_bar,
    hold_back_news,
    item_name,
    open_editor,
    plain,
)
from tests.board_m14 import events_of, feed_ids, open_free, open_working, show_tab
from tests.helpers import (
    all_nodes,
    code_of,
    confirm_on_board,
    connect,
    day,
    embeddings_stopped,
    export_everything,
    make_note,
    number_of,
    ok,
    on_board,
    refused,
    set_status,
    unique_word,
    whole_coverage_report,
)
from tests.test_board_requirements_integration import choose_place
from tests.world import Project, make_key, make_project, project_at, register

# The person who owns the project and is signed in to its board. Everything else that holds or
# changes work is an agent with a key of its own, named for what it does, since the server records
# who did what from the credential and nothing can be done in somebody else's name.
ACTOR = "Pages Tester"

NINE = [
    ("Prices are shown and charged in the customer's own currency", "Maya Planner"),
    ("Card numbers never touch our servers", "Tomas Berg"),
    ("Every refund needs a reason that the customer can read", "Priya Nair"),
    ("Receipts are emailed and also kept in the account", "Maya Planner"),
    ("A declined card is retried at most twice, never silently", "Lena Okafor"),
    ("Checkout works without an account", "Priya Nair"),
    ("Taxes are worked out on the server, never in the browser", "Tomas Berg"),
    ("Every payment step is logged, without card data", "Lena Okafor"),
    ("Stock is held for ten minutes while someone pays", "Maya Planner"),
]


def long_text(headline: str, what: str) -> str:
    """A note's text or why that is long enough to be wrapped to three lines in a table."""
    return (
        f"{headline}. This holds for every screen and every service that takes part in a "
        f"checkout, including the ones that are added later, and it is {what}. Anyone who wants "
        "an exception brings it to the weekly review first, and the exception is written down "
        "here with the date and the name of the person who agreed to it, so that nobody has to "
        "guess later why one screen behaves differently from the rest."
    )


def epic_details(word: str) -> str:
    return (
        f"## What checkout {word} covers\n\n"
        "Everything between a full basket and a receipt:\n\n"
        "- the basket and its total\n- paying by card\n- receipts and refunds\n\n"
        "The card step is the risky one, so it is built first (see [[story:2]]).\n\n"
        "```\nbasket -> pay -> receipt\n```\n"
    )


@dataclass
class World:
    """What the chapter made, by the references and numbers the board will show."""

    word: str
    epics: list[str] = field(default_factory=list)
    stories: list[str] = field(default_factory=list)
    tasks: list[str] = field(default_factory=list)
    nine: list[int] = field(default_factory=list)  # the project-wide notes of the busy epic
    decided_kept: int = 0  # decided by a person, linked to epic:1, in force, confirmed
    decided_old: int = 0  # decided, replaced by decided_new
    decided_new: int = 0
    decided_gone: int = 0  # decided, withdrawn
    note_kept: int = 0  # nobody decided it; confirmed
    note_old: int = 0  # replaced by note_new
    note_new: int = 0
    note_gone: int = 0  # withdrawn
    note_wide: int = 0  # concerns an acceptance criterion and a note as well
    rules: list[int] = field(default_factory=list)
    twin_story: str = ""  # means the same as story:4 in other words (for Possible duplicates)


async def build(client: Client, word: str, project: Project) -> World:
    """A small project, made the way people and agents make one: a plan, then the work moving,
    then what is remembered."""
    world = World(word)
    plan_items: list[dict[str, Any]] = []

    def add(key: str, kind: str, title: str, parent: str | None = None, **more: Any) -> None:
        plan_items.append({"key": key, "kind": kind, "title": title, "parent": parent, **more})

    add(
        "checkout",
        "epic",
        f"Checkout {word}",
        body=epic_details(word),
        priority="high",
        start_on=day(-20),
        target_on=day(40),
    )
    add("cart", "story", "Cart review", "checkout", start_on=day(-18), target_on=day(-2))
    for key, title in (
        ("t-total", "Show the basket total"),
        ("t-remove", "Remove an item from the basket"),
        ("t-empty", "Say so when the basket is empty"),
        ("t-save", "Save a basket for later"),
        ("t-phone", "Basket on a phone"),
    ):
        add(key, "task", title, "cart")
    add("card", "story", "Pay by card", "checkout", priority="urgent", target_on=day(10))
    for key, title in (
        ("t-form", "Card form"),
        ("t-3ds", "Extra check step for the card"),
        ("t-declined", "Say what a declined card means"),
    ):
        add(key, "task", title, "card")
    add("receipts", "story", "Receipts", "checkout")
    add("refunds", "story", "Refund the customer", "checkout", target_on=day(60))
    for key, title in (("t-refund-ui", "Refund button"), ("t-refund-mail", "Refund email")):
        add(key, "task", title, "refunds")
    add("accounts", "epic", "Accounts", body="Who people are, and how they get in.")
    add("signin", "story", "Sign in", "accounts")
    for key, title in (("t-login", "Login form"), ("t-lockout", "Lock out guesses")):
        add(key, "task", title, "signin")
    add("reset", "story", "Password reset", "accounts")
    add("t-reset", "task", "Reset email", "reset")
    add("reports", "epic", "Reports", body="What the shop owner reads.")
    add("weekly", "story", "Weekly summary", "reports")
    add("t-weekly", "task", "Summary page", "weekly", target_on=day(-5))
    add("someday", "epic", "Someday ideas")
    links = [
        {"source": "t-3ds", "target": "t-form", "link_type": "needs_first"},
        {"source": "t-refund-mail", "target": "t-refund-ui", "link_type": "needs_first"},
        {"source": "t-lockout", "target": "t-login", "link_type": "needs_first"},
        {"source": "t-save", "target": "t-total", "link_type": "relates_to"},
    ]
    rules = [
        {
            "title": "The total includes tax",
            "body": "A shopper sees tax in the total.",
            "node": "cart",
        },
        {
            "title": "An empty basket cannot be paid",
            "body": "No card step with nothing in it.",
            "node": "cart",
        },
        {
            "title": "A card is charged once",
            "body": "A second click never charges twice.",
            "node": "card",
        },
        {"title": "Receipts name the shop", "body": "Not placed on a story yet.", "node": None},
        {"title": "Refunds go back to the same card", "body": "Not placed either.", "node": None},
    ]
    made = await ok(client, "create_plan", items=plan_items, links=links, rules=rules)
    by_key = {item["key"]: item["ref"] for item in made["created"]}
    world.epics = [by_key[k] for k in ("checkout", "accounts", "reports", "someday")]
    world.stories = [
        by_key[k] for k in ("cart", "card", "receipts", "refunds", "signin", "reset", "weekly")
    ]
    world.tasks = [r for k, r in by_key.items() if k.startswith("t-")]
    world.rules = list(made["rule_ids"])
    await move_work(client, world, project)
    await remember(client, world, project)
    return world


async def move_work(client: Client, world: World, project: Project) -> None:
    """Some tasks are under way, done or blocked; one is held by an agent, one by the person who
    owns the project and one ended with a handoff note from a second agent; two stories mean the
    same in other words."""
    by_title = {n["title"]: n for n in (await ok(client, "list_nodes", limit=200))["items"]}
    for title, status in (
        ("Remove an item from the basket", "in_progress"),
        ("Card form", "in_progress"),
        ("Say what a declined card means", "blocked"),
    ):
        await set_status(client, by_title[title], status)
    held = by_title["Remove an item from the basket"]["ref"]
    async with connect(project.mcp_url, make_key(project, "mobile-agent")) as mobile:
        await ok(mobile, "claim_node", node=held, lease_seconds=86_400)
    on_board(
        "claim_node",
        project=project,
        node=by_title["Card form"]["ref"],
        lease_seconds=86_400,
    )
    total = by_title["Show the basket total"]["ref"]
    async with connect(project.mcp_url, make_key(project, "api-agent")) as api:
        await ok(api, "claim_node", node=total, lease_seconds=3_600)
        await ok(
            api,
            "finish_work",
            node=total,
            outcome="done",
            note=(
                "The total now includes tax and rounds the way the card step does, so the two "
                "never differ by a cent. The one thing left open is how a basket in two "
                "currencies is summed, which Maya has to decide. The tests for it are in the "
                "same folder as the basket tests and are named after the rule they check."
            ),
        )
    world.twin_story = (
        await ok(
            client,
            "create_node",
            kind="story",
            title="Reimburse a shopper",
            parent=world.epics[2],
        )
    )["ref"]


async def remember(client: Client, world: World, project: Project) -> None:
    """Nine long project-wide notes for the busy epic, and notes that are linked, confirmed,
    replaced and withdrawn. Some were decided by a person and some were not; a person confirms
    through the board, since only a person can."""
    epic = world.epics[0]
    for title, by in NINE:
        made = await make_note(
            client,
            f"{title}\n\n{long_text(title, 'decided for the whole shop')}",
            long_text("We looked at the support tickets of the last quarter", "why"),
            decided_by=by,
            project_wide=True,
            nodes=[epic],
        )
        world.nine.append(made["id"])
    confirm_on_board(
        "note",
        {"id": world.nine[1]},
        "Tomas Berg",
        project=project,
        quote="Yes, that is how it must stay.",
    )
    kept = await make_note(
        client,
        "Use the card provider's hosted form\n\n"
        "The card form is the provider's own hosted form, shown inside our page.",
        "It keeps card numbers away from our servers and saves a security review.",
        decided_by="Tomas Berg",
        nodes=[epic],
        rule_ids=[world.rules[2]],
    )
    world.decided_kept = kept["id"]
    confirm_on_board(
        "note",
        kept,
        "Priya Nair",
        project=project,
        quote="Confirmed after reading the provider's terms.",
    )
    old = await make_note(
        client,
        "Retry a declined card twice\n\nA declined card is tried again twice before the shopper "
        "is told.",
        "Some declines are only a slow bank.",
        decided_by="Lena Okafor",
        nodes=[world.stories[1]],
    )
    world.decided_old = old["id"]
    new = await ok(
        client,
        "supersede_note",
        replaces=[old["id"]],
        text="Retry a declined card once\n\nA declined card is tried again once, and the shopper "
        "is told at once after that.",
        why="Two retries made some banks block the card.",
        decided_by="Lena Okafor",
        keep_links=True,
    )
    world.decided_new = new["replacement"]["id"]
    gone = await make_note(
        client,
        "Show prices with and without tax\n\nEvery price on the basket page is shown twice, with "
        "and without tax.",
        "A trial that was dropped.",
        decided_by="Maya Planner",
        nodes=[epic],
    )
    world.decided_gone = gone["id"]
    await ok(
        client,
        "withdraw_note",
        note_ids=[gone["id"]],
        reason="Shoppers found two prices confusing.",
    )
    fact = await make_note(
        client,
        "The card provider answers within two seconds nine times out of ten",
        "The provider's status page, read on the planning call",
        nodes=[epic],
    )
    world.note_kept = fact["id"]
    confirm_on_board("note", fact, "Tomas Berg", project=project, quote="I read the page too.")
    wide = await make_note(
        client,
        "A basket holds at most fifty lines in the provider's test shop",
        "The provider's integration guide, page 12",
        nodes=[epic],
        rule_ids=[world.rules[1]],
        note_ids=[world.decided_kept],
    )
    world.note_wide = wide["id"]
    older = await make_note(
        client,
        "Declined cards are about four in a hundred",
        "Last quarter's report",
        nodes=[epic],
    )
    world.note_old = older["id"]
    replaced = await ok(
        client,
        "supersede_note",
        replaces=[older["id"]],
        text="Declined cards are about three in a hundred",
        why="This quarter's report",
        keep_links=True,
    )
    world.note_new = replaced["replacement"]["id"]
    dropped = await make_note(
        client,
        "The provider will add a fee next year",
        "A rumour at a conference",
        nodes=[epic],
    )
    world.note_gone = dropped["id"]
    await ok(
        client,
        "withdraw_note",
        note_ids=[dropped["id"]],
        reason="The provider said it is not true.",
    )


# -- reading the page -----------------------------------------------------------------------------

# Everything the old side panel and the two drawers were made of. None of it is on any page now.
PANEL_PARTS = ", ".join(
    [
        "#panel",
        "#card-title",
        "#record-title",
        "#drawer",
        "[id^='drawer']",
        "[class*='drawer']",
        ".quick-look",
        ".gv-quick",
        ".cell-look",
        "a.open-page",
        "#sheet-scrim",
        ".with-panel",
        ".with-drawer",
        ".card.selected",
        ".lane.selected",
    ]
)

REMAINS_OF_A_PANEL = """(selector) => {
    const found = [...document.querySelectorAll(selector)]
        .map((e) => (e.id ? '#' + e.id : e.tagName.toLowerCase() + '.' + e.className));
    for (const aside of document.querySelectorAll('aside')) {
        if (aside.id !== 'sidebar') found.push('aside#' + aside.id);
    }
    const layout = document.getElementById('layout');
    if (layout.children.length !== 1 || layout.firstElementChild.id !== 'page') {
        found.push('#layout holds ' + layout.children.length + ' things');
    }
    const keys = [...new URLSearchParams(location.hash.split('?')[1] || '').keys()];
    for (const key of keys) {
        if (key === 'drawer' || key === 'panel' || (key === 'sel' && !location.hash.startsWith('#/graph'))) {
            found.push('address key ' + key);
        }
    }
    return found;
}"""

LEAVE_A_MARK = """() => {
    const page = document.getElementById('page');
    page.querySelector('#mount-sentinel')?.remove();
    page.insertAdjacentHTML('afterbegin', '<i id="mount-sentinel" hidden></i>');
}"""


def status_of(url: str) -> int:
    """What the server answers to a plain GET of this address."""
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)


async def panel_parts(page: Page) -> list[str]:
    """What is on this page of a detail panel or a drawer, and which of their address keys: none."""
    found: list[str] = await page.evaluate(REMAINS_OF_A_PANEL, PANEL_PARTS)
    return found


def hash_of(page: Page) -> str:
    return page.url.partition("#")[2]


async def pick_from(page: Page, chip: Locator, value: str) -> None:
    """Open the menu of a status chip and choose one of its rows."""
    menu_name = await chip.get_attribute("data-menu")
    await chip.click()
    await page.locator(
        f"div.vmenu[data-menu='{menu_name}'] .vmenu-option[data-value='{value}']"
    ).click()


def query_of(address: str) -> dict[str, str]:
    """The choices an address holds: '#/board?q=is:overdue&epic=1' is {'q': 'is:overdue', ...}."""
    return dict(parse_qsl(address.partition("?")[2]))


def page_of(address: str) -> str:
    """'#/graph?view=work' is 'graph'."""
    return address.removeprefix("#").removeprefix("/").partition("?")[0]


async def visit(w: Watched, address: str, *, fresh: bool = False) -> None:
    """Go to an address of the open board as a link does, and wait until its page is drawn. The
    same page with other choices (or `fresh`) is loaded from nothing, which is what a bookmark
    does and starts every part of the board over."""
    page = w.page
    now = await page.evaluate("location.hash")
    if fresh or page_of(address) == page_of(now):
        # Only the part after the # changes, which the browser takes as a move inside the same
        # document, so the page is loaded again to start the whole board over.
        await page.goto(f"{w.board}{address}")
        await page.reload()
        await page.wait_for_selector("#app[data-shell=project]", timeout=SLOW)
    else:
        await page.evaluate(LEAVE_A_MARK)
        await w.go(address)
        await expect(page.locator("#mount-sentinel")).to_have_count(0, timeout=SLOW)
    await page.wait_for_function(
        "() => document.querySelector('#page h1, #page .item-state') !== null"
        " && ![...document.querySelectorAll('#page .loading')].some((e) => e.getClientRects().length)",
        timeout=SLOW,
    )


async def highlighted(page: Page) -> str | None:
    """The page the sidebar marks as the one the person is in: a view, or Activity; None when
    nothing is marked (Search and the coverage report have no place in the sidebar)."""
    marked = page.locator("#main-nav a[aria-current=page], #activity-open[aria-current=page]")
    names: list[str] = await marked.evaluate_all("els => els.map((e) => e.dataset.page)")
    assert len(names) <= 1, names
    return names[0] if names else None


async def on_item(page: Page, ref: str) -> None:
    """Wait for the page of this record to be there with its sections drawn (the history is the
    last of them)."""
    await expect(page.locator("#item-page")).to_have_attribute("data-ref", ref, timeout=SLOW)
    await expect(page.locator("#item-page [data-part=history]").first).to_be_attached(timeout=SLOW)


async def height_of(place: Locator) -> float:
    box = await place.bounding_box()
    assert box is not None
    return box["height"]


# -- 1. no panel and no drawer, anywhere -----------------------------------------------------------


def every_page(world: World) -> list[tuple[str, str]]:
    """One address for each kind of page the board has."""
    return [
        ("the Board", "#/board"),
        ("the Board of one epic", f"#/board?epic={number_of(world.epics[0])}"),
        ("the Graph's map of the whole project", "#/graph"),
        ("the Graph's Around", "#/graph?tab=around"),
        ("the Graph's Order", "#/graph?tab=order"),
        ("the Graph looking around an epic", f"#/graph?tab=around&focus={world.epics[0]}"),
        ("the Roadmap", "#/roadmap"),
        ("Lists of work", "#/lists?view=work"),
        ("Lists of acceptance criteria", "#/lists?view=requirements"),
        ("Lists of notes", "#/lists?view=notes"),
        ("Search", "#/search?search=refund"),
        ("Possible duplicates", "#/search?dups=1"),
        ("Activity", "#/activity"),
        ("Activity, who is working", "#/activity?tab=working"),
        ("the coverage report", "#/coverage"),
        ("the page of an epic", f"#/item?item={world.epics[0]}&from=board"),
        ("the page of a story", f"#/item?item={world.stories[1]}&from=graph"),
        ("the page of a task", f"#/item?item={world.tasks[6]}&from=roadmap"),
        ("the page of an acceptance criterion", f"#/item?item=rule:{world.rules[2]}&from=board"),
        ("the page of a decided note", f"#/item?item=note:{world.decided_kept}&from=search"),
        ("the page of a note", f"#/item?item=note:{world.note_wide}&from=activity"),
        ("Users and agent keys", "#/users"),
        ("Definition of done", "#/definition"),
        ("Tools", "#/tools"),
        ("Status and export", "#/status"),
    ]


async def nothing_beside_the_page(w: Watched, world: World) -> None:
    page = w.page
    pages = every_page(world)
    leftovers: dict[str, list[str]] = {}
    for name, address in pages:
        await visit(w, address)
        found = await panel_parts(page)
        if found:
            leftovers[name] = found
    # The old addresses: a choice that means nothing any more is dropped and the page opens.
    old = [
        ("#/board?sel=story:1&drawer=search&panel=coverage", "#/board", ".card"),
        ("#/roadmap?sel=task:2&drawer=activity&panel=coverage", "#/roadmap", ".rm-bar"),
        ("#/item?item=story:1&sel=task:2&drawer=search&panel=coverage", "#/item?item=story:1", "#item-title"),
        ("#/graph?sel=story:2&drawer=activity&panel=coverage", "#/graph", "#graph-explore"),
    ]  # fmt: skip
    cleaned: list[str] = []
    for address, wanted, shows in old:
        await page.goto(f"{w.board}{address}")
        await expect(page.locator(f"#page {shows}").first).to_be_visible(timeout=SLOW)
        await page.wait_for_function(
            "(want) => location.hash.startsWith(want)", arg=wanted, timeout=SLOW
        )
        gone = await panel_parts(page)
        if gone:
            leftovers[address] = gone
        cleaned.append(hash_of(page))
    # The files of the panel and the drawers are not even served.
    served = [
        status_of(f"{w.origin}/board/{name}") for name in ("panel.js", "drawer.js", "drawers.css")
    ]
    proof(
        "M20",
        f"on {len(pages)} pages (the Board, the Graph and its tabs, the Roadmap, the Lists, Search, "
        "Possible duplicates, Activity, the coverage report, each of the five record pages and "
        "the settings pages) nothing of a detail panel or a drawer is there and the page holds "
        "only its own content; old addresses with sel=, drawer= and panel= open the page without "
        "them; the old script and style files are not served",
        not leftovers
        and cleaned[0] == "/board"
        and "drawer" not in "".join(cleaned)
        and "panel" not in "".join(cleaned)
        and cleaned[3] == "/graph"
        and served == [404, 404, 404],
        f"{len(pages)} pages and {len(old)} old addresses checked"
        + (f"; found {leftovers}" if leftovers else ""),
    )
    assert w.problems == [], w.problems


# -- 2. every kind of record opens its own page, under the view it came from ------------------------


def words(text: str) -> str:
    return " ".join(text.split())


@dataclass
class Opening:
    """One way to open a record: where the person is, what they click, and what must open."""

    place: str  # in words, for the proof line
    start: str  # the address the person starts on, with a filter of its own where the view has one
    ref: str  # the record that has to open
    click: str  # the element that is clicked (a selector in the page); "" when `keys` is used
    came_from: str  # the view the path must start with: board, graph, roadmap, lists, search, activity, coverage
    keys: tuple[str, ...] = ()  # pressed instead, for the quick switcher
    where: tuple[int, int] | None = None  # a click away from the links of a card


VIEW_WORDS = {
    "board": "Board",
    "graph": "Graph",
    "roadmap": "Roadmap",
    "lists": "Lists",
    "search": "Search",
    "activity": "Activity",
    "coverage": "Coverage",
}


def openings(world: World) -> list[Opening]:
    epic, cart, card, refunds = world.epics[0], world.stories[0], world.stories[1], world.stories[3]
    t_form, t_summary, t_refund = "task:6", "task:14", "task:9"
    rule = f"rule:{world.rules[2]}"
    decided = f"note:{world.decided_kept}"
    note = f"note:{world.note_wide}"
    on_board, around = (
        "#/board?show=both&q=is:overdue",
        "#/graph?tab=around",
    )
    roadmap = "#/roadmap?q=priority:high,urgent"
    lists_work, lists_rules, lists_notes = (
        "#/lists?view=work",
        "#/lists?view=requirements",
        "#/lists?view=notes",
    )
    activity = f"#/activity?act_item=note:{world.decided_kept}"
    refund_search, provider_search = (
        "#/search?search=refund",
        "#/search?search=provider",
    )
    return [
        Opening("an epic's title on its lane of the Board", on_board, epic, f"a.lane-title[data-focus-key='{epic}']", "board"),
        Opening("a story card's title on the Board", on_board, cart, f"article.card[data-ref='{cart}'] h3.card-title a", "board"),
        Opening("a task card's title on the Board", on_board, t_summary, f"article.card[data-ref='{t_summary}'] h3.card-title a", "board"),
        Opening("a click on the card itself, away from its links", on_board, t_summary, f"article.card[data-ref='{t_summary}']", "board", where=(6, 6)),
        *[
            Opening(f"Open page on the Graph looking around {what}", f"{around}&focus={ref}", ref, "#graph-hub-open", "graph")
            for what, ref in (("an epic", epic), ("a story", card), ("a task", t_form), ("an acceptance criterion", rule), ("a decided note", decided), ("a note", note))
        ],
        Opening("a bar of the Roadmap", roadmap, card, f".rm-bar[data-ref='{card}']", "roadmap"),
        Opening("an epic's label on the Roadmap", roadmap, epic, f".rm-label[data-ref='{epic}'] a.rm-open", "roadmap"),
        Opening("a story in the results of Search", refund_search, refunds, f"#search-results tr[data-ref='{refunds}'] a.result-title", "search"),
        Opening("a task in the results of Search", refund_search, t_refund, f"#search-results tr[data-ref='{t_refund}'] a.result-title", "search"),
        Opening("an acceptance criterion in the results of Search", refund_search, f"rule:{world.rules[4]}", f"#search-results tr[data-ref='rule:{world.rules[4]}'] a.result-title", "search"),
        Opening("a note in the results of Search", provider_search, f"note:{world.note_kept}", f"#search-results tr[data-ref='note:{world.note_kept}'] a.result-title", "search"),
        Opening("a title in the Work list of Lists", lists_work, epic, f"#work-tree li[data-key='{epic}'] > .gv-row a.gv-title", "lists"),
        Opening("an acceptance criterion in the list of Lists", lists_rules, rule, f"#lists-list .gv-record[data-key='{rule}'] button.gv-open", "lists"),
        Opening("a note in the notes list of Lists", lists_notes, decided, f"#ctx-notes-list tr[data-ref='{decided}'] a.record-title", "lists"),
        Opening("a line of the change feed on Activity", activity, decided, f"#feed li.event a.item-link[href*='item={decided}&']", "activity"),
        Opening("a claim among who is working on what", "#/activity?tab=working", "task:2", "#agents li.claim[data-node='task:2'] a.item-link", "activity"),
        Opening("an acceptance criterion in the coverage report", "#/coverage", f"rule:{world.rules[3]}", f"tr[data-rule='{world.rules[3]}'] a.record-title", "coverage"),
        Opening("a story in the coverage report", "#/coverage", world.stories[2], f"tr[data-story='{world.stories[2]}'] a.record-title", "coverage"),
        Opening("a reference in an epic's details", f"#/item?item={epic}&from=board", world.stories[1], f"#item-details .body-text a.reference[data-ref='{world.stories[1]}']", "board"),
        Opening("a line of a history", f"#/item?item={epic}&from=graph", f"note:{world.note_gone}", f"[data-part=history] a[href*='item=note:{world.note_gone}&']", "graph"),
        Opening("the quick switcher, typing a task's code", roadmap, t_form, "", "roadmap", keys=("T-6",)),
        Opening("the quick switcher, typing a note's code", roadmap, decided, "", "roadmap", keys=(f"N-{world.decided_kept}",)),
    ]  # fmt: skip


async def expected_path(client: Client, ref: str, view: str) -> list[str]:
    """The path the top bar has to show for a record: the view first, then the epic and story
    above it (an acceptance criterion is under its story or task), then the record. Each step is
    a record's code and its name: E-1 Checkout, S-4 Pay by card, T-9 Refund button, AC-3 and N-7
    for an acceptance criterion and a note (the project is named by the switcher, not here)."""
    kind, _, number = ref.partition(":")
    above: list[str] = []
    up: str | None = None
    if kind in ("epic", "story", "task"):
        up = (await ok(client, "get_node", node=ref))["parent"]
    elif kind == "rule":
        up = (await ok(client, "get_rule", rule_id=int(number)))["node"]
    while up:
        above.insert(0, await item_name(client, up))
        up = (await ok(client, "get_node", node=up))["parent"]
    return [VIEW_WORDS[view], *above, await item_name(client, ref)]


async def expected_title(client: Client, ref: str) -> str:
    """The words of a record's page heading: its code and its name, "S-12 Scan a code", "AC-3 The
    title", "N-7 The first line"."""
    return await item_name(client, ref)


def same_name(shown: str, wanted: str) -> bool:
    """A name as the page shows it is the one asked for, or, for a long first line of a note that
    the page cuts short, its first forty characters."""
    if shown == wanted:
        return True
    code = wanted.partition(" ")[0]
    return code.startswith("N-") and shown.startswith(wanted[: len(code) + 41].rstrip("…").strip())


async def open_it(page: Page, opening: Opening) -> None:
    if opening.keys:
        await page.keyboard.press("Control+k")
        box = page.locator("#switcher-input")
        await expect(box).to_be_visible(timeout=SLOW)
        await box.fill(opening.keys[0])
        await expect(page.locator("#switcher-list [role=option]").first).to_be_visible(timeout=SLOW)
        await page.keyboard.press("Enter")
    else:
        target = page.locator(opening.click).first
        await expect(target).to_be_visible(timeout=SLOW)
        if opening.where:
            await target.click(position={"x": opening.where[0], "y": opening.where[1]})
        else:
            await target.click()


async def records_open_from_everywhere(w: Watched, client: Client, world: World) -> None:
    page = w.page
    wrong: list[str] = []
    by_place: dict[str, list[Opening]] = {}
    for opening in openings(world):
        by_place.setdefault(opening.start, []).append(opening)
    for start, group in by_place.items():
        await visit(w, start)
        for opening in group:
            await open_it(page, opening)
            await on_item(page, opening.ref)
            wanted = await expected_path(client, opening.ref, opening.came_from)
            heading = await expected_title(client, opening.ref)
            # The title and the path name the record once the page has what it needs to say them.
            with contextlib.suppress(AssertionError):
                await expect(page.locator("#item-title")).to_have_text(
                    re.compile(re.escape(heading[:20])), timeout=SLOW
                )
            title = words(await page.locator("#item-title").inner_text())
            path = await crumbs(page)
            tooltip = await page.locator("#crumb-page").get_attribute("title") or ""
            marked = await highlighted(page)
            lit = (
                opening.came_from
                if opening.came_from in ("board", "graph", "roadmap", "lists", "activity")
                else None
            )
            if (
                len(path) != len(wanted)
                or not all(same_name(shown, want) for shown, want in zip(path, wanted, strict=True))
                or marked != lit
                or not same_name(title, heading)
                or not same_name(tooltip, wanted[-1])
            ):
                wrong.append(
                    f"{opening.place}: path {path}, wanted {wanted}; marked {marked}; "
                    f"title {title!r}, wanted {heading!r}; tooltip {tooltip!r}"
                )
            # The way back returns to the view as it was left, with its filter and choices.
            await page.locator("nav.crumbs li.crumb-view a").click()
            await page.wait_for_function(
                "(page) => location.hash.startsWith('#/' + page)",
                arg=opening.came_from,
                timeout=SLOW,
            )
            back = hash_of(page)
            asked = query_of(start) if page_of(start) == opening.came_from else {}
            if page_of(back) != opening.came_from or any(
                query_of(back).get(k) != v for k, v in asked.items() if k != "sel"
            ):
                wrong.append(f"{opening.place}: the way back was {back}, not {start}")
    kinds = sorted({o.ref.partition(":")[0] for o in openings(world)})
    proof(
        "M20",
        f"{len(openings(world))} ways to open a record, from the Board's cards and lanes, the "
        "Graph's Around, the Roadmap's bars and labels, the Lists, Search results, the "
        "change feed and the claims of Activity, the coverage report, a reference in a text, a "
        "line of a history and the quick switcher, each land on the page of an epic, story, task, "
        "acceptance criterion or note; the path starts with the view it came from and then runs "
        "through the epic and story above it and ends with the record (E-1, S-4, T-9, AC-3, N-7, "
        "each with its name, the last one also as the tooltip and the page's heading), the "
        "sidebar marks that same view, and the way back returns to the view with its filter",
        not wrong and kinds == ["epic", "note", "rule", "story", "task"],
        f"{len(by_place)} starting places" + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )
    assert w.problems == [], w.problems


def first_line(markdown: str) -> str:
    """The first words of a text as the page shows them, without the marks of Markdown."""
    for line in markdown.splitlines():
        plain_line = line.lstrip("#-* ").strip()
        if plain_line:
            return plain_line
    return ""


STATUS_WORDS = {
    "unconfirmed": "Unconfirmed",
    "confirmed": "Confirmed",
    "superseded": "Replaced",
    "withdrawn": "Withdrawn",
}


async def concerned(page: Page, section: str) -> set[str]:
    """What the concerns or applies-to table of a record page names, as the references of its rows."""
    refs: list[str] = await page.locator(f"[data-part={section}] tr.record-line").evaluate_all(
        "els => els.map((e) => e.dataset.ref)"
    )
    return set(refs)


async def record_pages_equal_the_tools(w: Watched, client: Client, world: World) -> None:
    """What each kind of record page says, field by field, against what the tool says."""
    page = w.page
    wrong: list[str] = []
    for ref in (world.epics[0], world.stories[0], "task:2"):
        node = await ok(client, "get_node", node=ref)
        await visit(w, f"#/item?item={ref}&from=board")
        await on_item(page, ref)
        shown = words(await page.locator("#item-page").inner_text())
        strip = page.locator("#item-page .state-strip")
        said: dict[str, str] = {
            "title": words(await page.locator("#item-title").inner_text()),
            "status": await strip.locator("dd.status .chip.status").first.get_attribute(
                "data-status"
            )
            or "",
            "priority": await strip.locator("dd.priority .chip.priority").first.get_attribute(
                "data-priority"
            )
            or "",
        }
        if said["title"] != f"{code_of(ref)} {node['title']}":
            wrong.append(f"{ref} title {said['title']}")
        if said["status"] != node["status"] or said["priority"] != node["priority"]:
            wrong.append(f"{ref} status {said['status']}, priority {said['priority']}")
        if node["parent"]:
            path = await crumbs(page)
            if await item_name(client, node["parent"]) not in path:
                wrong.append(f"{ref} parent {path}")
        for field_name, selector in (("start_on", "dd.start"), ("target_on", "dd.target")):
            if node[field_name] and node[field_name] not in words(
                await strip.locator(selector).inner_text()
            ):
                wrong.append(f"{ref} {field_name}")
        if (
            node["progress_total"]
            and f"{node['progress_done']} of {node['progress_total']}" not in shown
        ):
            wrong.append(f"{ref} progress")
        if node["claimed_by"] and node["claimed_by"] not in shown:
            wrong.append(f"{ref} held by")
        if (first_line(node["body"]) or "Empty") not in shown:
            wrong.append(f"{ref} details")
    rule = await ok(client, "get_rule", rule_id=world.rules[2])
    await visit(w, f"#/item?item=rule:{rule['id']}&from=board")
    await on_item(page, f"rule:{rule['id']}")
    shown = words(await page.locator("#item-page").inner_text())
    if (
        words(await page.locator("#item-title").inner_text())
        != await item_name(client, f"rule:{rule['id']}")
        or await item_name(client, rule["node"]) not in await crumbs(page)
        or rule["status"].title() not in shown
        or first_line(rule["body"]) not in shown
    ):
        wrong.append(f"rule {rule['id']}")
    for number in (
        world.decided_kept,
        world.nine[1],
        world.decided_old,
        world.decided_new,
        world.note_wide,
        world.note_kept,
        world.note_old,
        world.note_gone,
    ):
        note = await ok(client, "get_note", note_id=number)
        await visit(w, f"#/item?item=note:{number}&from=board")
        await on_item(page, f"note:{number}")
        shown = words(await page.locator("#item-page").inner_text())
        wanted = (
            {f"{r}" for r in note["nodes"]}
            | {f"rule:{i}" for i in note["rule_ids"]}
            | {f"note:{i}" for i in note["note_ids"]}
        )
        if note["project_wide"]:
            wanted.add("project")
        sure = f"Confirmed by {note['confirmed_by']}" if note["status"] == "confirmed" else ""
        said_by = f"{note['decided_by']} on {note['decided_on']}" if note["decided_by"] else ""
        title = words(await page.locator("#item-title").inner_text())
        missing = [
            name
            for name, text in (
                ("text", words(note["text"].partition("\n")[2])),
                ("why", words(note["why"])),
                ("recorded by", note["recorded_by"]),
                ("status", STATUS_WORDS[note["status"]]),
                ("confirmed by", sure),
                ("decided by", said_by),
                ("project-wide", "roject-wide" if note["project_wide"] else ""),
            )
            if text not in shown
        ]
        concerns = await concerned(page, "concerns")
        if not same_name(title, f"N-{number} {note['headline']}") or missing or concerns != wanted:
            wrong.append(f"note {number}: title {title!r}, missing {missing}, concerns {concerns}")
        if note["superseded_by_id"]:
            replaced_by = await concerned(page, "replaces")
            if replaced_by != {f"note:{note['superseded_by_id']}"}:
                wrong.append(f"note {number} replaced by {replaced_by}")
    proof(
        "M20",
        "compared with the tools, the page of an epic, a story, a held task, an acceptance criterion and "
        "eight notes (decided and confirmed, project-wide, replaced, replacing, withdrawn and "
        "not decided by anyone) shows the same fields: title and code, "
        "status, priority, dates, progress, who holds it, where it sits, its words, its why, who "
        "recorded it and who decided it, its one status (Unconfirmed, Confirmed, Replaced or "
        "Withdrawn) and who confirmed it, and exactly the items, acceptance criteria and notes it "
        "concerns",
        not wrong,
        f"{len(wrong)} differences: {wrong[:3]}" if wrong else "every field equal",
    )
    assert w.problems == [], w.problems


# -- 3. moving through the content ------------------------------------------------------------------


async def walk_siblings(w: Watched, siblings: list[str], noun: str) -> tuple[bool, str]:
    """Open the first of these and press Next to the end, then Previous back to the start: the
    pages, their positions and the ends are what the tool lists."""
    page = w.page
    await visit(w, f"#/item?item={siblings[0]}&from=board")
    count = len(siblings)
    there: list[str] = []
    positions: list[str] = []
    for step in range(count):
        ref = await page.locator("#item-page").get_attribute("data-ref") or ""
        there.append(ref)
        positions.append(words(await page.locator("#item-position").inner_text()))
        if step < count - 1:
            await page.locator("#item-next").click()
            await expect(page.locator("#item-page")).not_to_have_attribute("data-ref", ref)
    ends = [
        await page.locator("#item-next").is_disabled(),
        await page.locator("#item-next").get_attribute("aria-label") == f"No next {noun}",
    ]
    back: list[str] = []
    for step in range(count):
        ref = await page.locator("#item-page").get_attribute("data-ref") or ""
        back.append(ref)
        if step < count - 1:
            await page.locator("#item-prev").click()
            await expect(page.locator("#item-page")).not_to_have_attribute("data-ref", ref)
    ends += [
        await page.locator("#item-prev").is_disabled(),
        await page.locator("#item-prev").get_attribute("aria-label") == f"No previous {noun}",
    ]
    held = query_of(hash_of(page)).get("from") == "board"
    good = (
        there == siblings
        and back == siblings[::-1]
        and positions == [f"{at} of {count}" for at in range(1, count + 1)]
        and all(ends)
        and held
    )
    return good, f"{'stories' if noun == 'story' else noun + 's'}: {', '.join(there)}"


async def expand_all(page: Page) -> None:
    opener = page.locator("#epic-list button.tree-twisty[aria-label^='Expand']")
    for _ in range(80):
        if not await opener.count():
            return
        await opener.first.click()


TREE_ROWS = """() => [...document.querySelectorAll('#epic-list li[role=treeitem]')].map((e) => [
    e.dataset.key, Number(e.getAttribute('aria-level')),
    e.querySelector(':scope > .tree-row .tree-code').innerText.trim(),
    e.querySelector(':scope > .tree-row .tree-title').innerText.trim()])"""


async def moving_through_the_content(w: Watched, client: Client, world: World) -> None:
    page = w.page
    epics = [n["ref"] for n in await all_nodes(client, kind="epic")]
    in_first = [n["ref"] for n in await all_nodes(client, parent=world.epics[0])]
    under_cart = [n["ref"] for n in await all_nodes(client, parent=world.stories[0])]
    under_reset = [n["ref"] for n in await all_nodes(client, parent=world.stories[5])]
    walked = [
        await walk_siblings(w, epics, "epic"),
        await walk_siblings(w, in_first, "story"),
        await walk_siblings(w, under_cart, "task"),
        await walk_siblings(w, under_reset, "task"),
    ]
    # The keys [ and ] do the same as the buttons when nothing is being typed.
    await visit(w, f"#/item?item={epics[1]}&from=board")
    await page.keyboard.press("]")
    await on_item(page, epics[2])
    await page.keyboard.press("[")
    await on_item(page, epics[1])
    proof(
        "M20",
        f"Previous and Next on the pages of the {len(epics)} epics, of the {len(in_first)} "
        f"stories of E-1, of the {len(under_cart)} tasks of S-1 and of a story's only task walk "
        "exactly the siblings list_nodes returns, in order, say '2 of 5' where they are, are "
        "disabled and say so at both ends, keep the view the page was reached from, and the "
        "keys [ and ] do the same",
        all(good for good, _ in walked) and len(under_reset) == 1,
        "; ".join(detail for _, detail in walked[:3]),
    )

    # From an epic to a task and back to the epic, and on to another epic, by links alone.
    story, task, third = world.stories[1], "task:7", world.epics[2]
    await visit(w, f"#/item?item={world.epics[0]}&from=board")
    await page.locator(f"#item-table tr[data-ref='{story}'] a.item-code-link").click()
    await on_item(page, story)
    await page.locator(f"#item-table tr[data-ref='{task}'] a.item-code-link").click()
    await on_item(page, task)
    path = await crumbs(page)
    arrow = page.locator("#back-to-view")
    arrow_words = await arrow.get_attribute("aria-label")
    await arrow.click()
    await on_item(page, story)
    await page.locator("nav.crumbs li.crumb-item a").first.click()
    await on_item(page, world.epics[0])
    await page.locator("#item-next").click()
    await on_item(page, world.epics[1])
    await page.locator("#item-next").click()
    await on_item(page, third)
    # And through the sidebar's tree to a task of that epic.
    reports = page.locator(f"#epic-list li[data-key='{third}']")
    weekly = world.stories[6]
    await reports.locator(":scope > .tree-row button.tree-twisty").click()
    await page.locator(f"#epic-list li[data-key='{weekly}'] > .tree-row button.tree-twisty").click()
    await page.locator("#epic-list a.tree-link[data-ref='task:14']").click()
    await on_item(page, "task:14")
    current = page.locator("#epic-list a.tree-link[aria-current=page]")
    parent_open = [
        await page.locator(f"#epic-list li[data-key='{key}']").get_attribute("aria-expanded")
        for key in (third, weekly)
    ]
    proof(
        "M20",
        "from the page of an epic a person reaches a story, then a task, then back to the story "
        "with the back arrow (which names the page it goes back to) and up to the epic by the "
        "path above the page, then to the next two epics, then through the sidebar's tree to a "
        "task of the third, by clicking links and buttons only (no address typed after the "
        "first): each page is the one the click names and the path above the task reads "
        f"'{' / '.join(path[1:])}'",
        path[1:] == await expected_tail(client, task)
        and arrow_words == f"Back to {await item_name(client, story)}"
        and await current.get_attribute("data-ref") == "task:14"
        and parent_open == ["true", "true"],
        f"{len(path)} steps",
    )
    await the_sidebar_tree(w, client, world)
    assert w.problems == [], w.problems


async def expected_tail(client: Client, ref: str) -> list[str]:
    return (await expected_path(client, ref, "board"))[1:]


async def the_sidebar_tree(w: Watched, client: Client, world: World) -> None:
    page = w.page
    await page.set_viewport_size({"width": 1280, "height": 640})
    epics = await all_nodes(client, kind="epic")
    expected: list[list[Any]] = []
    for epic in epics:
        expected.append([epic["ref"], 1, code_of(epic["ref"]), epic["title"]])
        for story in await all_nodes(client, parent=epic["ref"]):
            expected.append([story["ref"], 2, code_of(story["ref"]), story["title"]])
            for task in await all_nodes(client, parent=story["ref"]):
                expected.append([task["ref"], 3, code_of(task["ref"]), task["title"]])
    await visit(w, "#/board", fresh=True)
    side = page.locator("#sidebar")
    closed_height = await height_of(side)
    closed = await page.evaluate(TREE_ROWS)
    await expand_all(page)
    rows = await page.evaluate(TREE_ROWS)
    tree = await page.evaluate(
        """() => { const t = document.getElementById('epic-list');
            return [t.scrollHeight > t.clientHeight, getComputedStyle(t).overflowY]; }"""
    )
    open_height = await height_of(side)
    # A page opened by its address has its ancestors open and itself marked.
    await visit(w, "#/item?item=task:9&from=board", fresh=True)
    opened = {
        key: await page.locator(f"#epic-list li[data-key='{key}']").get_attribute("aria-expanded")
        for key in ("epic:1", "story:4", "epic:2")
    }
    marked = await page.locator("#epic-list a.tree-link[aria-current=page]").get_attribute(
        "data-ref"
    )
    # The keyboard: arrows move, Right opens, Enter opens the page and puts the focus on it.
    await visit(w, "#/board", fresh=True)
    second = page.locator("#epic-list li[data-key='epic:2']")
    await second.focus()
    await page.keyboard.press("ArrowRight")
    await expect(second).to_have_attribute("aria-expanded", "true")
    await page.keyboard.press("ArrowDown")
    await expect(page.locator("#epic-list li[data-key='story:5']")).to_be_focused()
    await page.keyboard.press("Enter")
    await on_item(page, "story:5")
    await expect(page.locator("#item-title")).to_be_focused()
    await page.set_viewport_size({"width": 1500, "height": 1500})
    proof(
        "M20",
        f"the sidebar's tree lists every epic, then under each its stories and their tasks "
        f"({len(expected)} rows, with their codes and titles) exactly as list_nodes does once it "
        "is opened out; it starts closed, scrolls inside its own region without making the "
        "sidebar any taller, opens a page's ancestors and marks the page itself, and works with "
        "the arrow keys and Enter",
        rows == expected
        and len(closed) == len(epics)
        and tree[0]
        and tree[1] in ("auto", "scroll")
        and abs(open_height - closed_height) < 1
        and opened == {"epic:1": "true", "story:4": "true", "epic:2": "false"}
        and marked == "task:9",
        f"{len(rows)} rows",
    )


# -- 4. the nine long notes, in tables the width of the page ---------------------------------------

MEASURE = """() => {
    const box = (e) => e.getBoundingClientRect();
    const page = box(document.getElementById('item-page'));
    const tables = [...document.querySelectorAll('#item-page table.record-table')]
        .filter((table) => table.getClientRects().length > 0).map((table) => {
        const rows = [...table.querySelectorAll('tbody tr')];
        const region = table.parentElement;
        return {
            name: table.className,
            left: box(table).left, width: box(table).width, rows: rows.length,
            heights: rows.map((row) => Math.round(box(row).height)),
            columns: [...table.querySelectorAll('thead th')].map((th, at) => ({
                name: th.textContent.trim(), width: box(th).width,
                filled: rows.some((row) => {
                    const cell = row.children[at];
                    return Boolean(cell) && (cell.textContent.trim() !== ''
                        || Boolean(cell.querySelector('svg, a, button, select, input')));
                }),
            })),
            sideways: region.scrollWidth - region.clientWidth,
        };
    });
    return { page: { left: page.left, width: page.width }, tables };
}"""

SECTIONS = """() => Object.fromEntries([...document.querySelectorAll('.recall-section[data-section]')]
    .map((e) => [e.dataset.section, [...e.querySelectorAll('tr.record-line')].map((r) => Number(r.dataset.id))]))"""


async def stable(page: Page, script: str) -> Any:
    """What a script reads once the page has stopped moving (two equal reads in a row)."""
    last = None
    for _ in range(40):
        now = await page.evaluate(script)
        if now == last:
            return now
        last = now
        await page.wait_for_timeout(120)
    return last


async def open_every_disclosure(page: Page) -> None:
    closed = page.locator("details.recall-disclosure:not([open]) > summary")
    while await closed.count():
        await closed.first.click()


def table_problems(measured: dict[str, Any], width: int) -> list[str]:
    """Every table of notes spans the content of the page, does not scroll sideways, keeps a row to
    three lines (no row is taller than three of the shortest row of any table, a button included)
    and has no column with nothing in it."""
    problems: list[str] = []
    page = measured["page"]
    line = min((h for table in measured["tables"] for h in table["heights"]), default=0)
    for table in measured["tables"]:
        name = f"{table['name']} at {width}"
        if abs(table["left"] - page["left"]) > 4 or table["width"] < page["width"] - 8:
            problems.append(f"{name}: spans {table['width']:.0f} of {page['width']:.0f}")
        if table["sideways"] > 1:
            problems.append(f"{name}: scrolls sideways")
        if table["heights"] and max(table["heights"]) > line * 3.2:
            problems.append(f"{name}: rows are {table['heights']} high, a line is {line}")
        for column in table["columns"]:
            if not column["filled"] or column["width"] < 40:
                problems.append(f"{name}: dead column {column['name']}")
    return problems


async def the_nine_notes(w: Watched, client: Client, world: World) -> None:
    page = w.page
    epic = world.epics[0]
    recalled = await ok(client, "recall", node=epic)
    expected: dict[str, list[int]] = {}
    for name in ("notes", "project_wide_notes", "past_notes"):
        expected[name] = sorted(entry["note"]["id"] for entry in recalled[name]["items"])
    await visit(w, f"#/item?item={epic}&from=board", fresh=True)
    wide = page.locator("details.recall-disclosure[data-disclosure=project_wide_notes]")
    closed_with_entries = await wide.get_attribute("open") is None
    problems: list[str] = []
    seen: dict[str, list[int]] = {}
    for width in (1280, 1600):
        await page.set_viewport_size({"width": width, "height": 900})
        await open_every_disclosure(page)
        measured = await stable(page, MEASURE)
        problems += table_problems(measured, width)
        seen = {name: sorted(ids) for name, ids in (await page.evaluate(SECTIONS)).items()}
    tables = len(measured["tables"])
    heights = {
        table["name"]: max(table["heights"]) for table in measured["tables"] if table["heights"]
    }
    # On an epic with nothing linked to it the same notes apply, and the disclosure is open.
    await visit(w, f"#/item?item={world.epics[3]}&from=board", fresh=True)
    open_without = (
        await page.locator(
            "details.recall-disclosure[data-disclosure=project_wide_notes]"
        ).get_attribute("open")
        is not None
    )
    # At most three lines of a long note, and all of its words one click away.
    await visit(w, f"#/item?item={epic}&from=board", fresh=True)
    await open_every_disclosure(page)
    wide_id = world.nine[2]
    note = await ok(client, "get_note", note_id=wide_id)
    row = page.locator(f"tr.record-line[data-ref='note:{wide_id}']")
    whole = await row.locator(".clamp").first.get_attribute("title") or ""
    wraps = await row.locator("td.cell-title .clamp").first.evaluate(
        "e => getComputedStyle(e).webkitLineClamp === '3' && e.scrollHeight > e.clientHeight"
    )
    await row.locator("td.cell-title .clamp-toggle").click()
    shown = words(await row.locator("td.cell-title").inner_text())
    await row.locator("td").last.click()
    await on_item(page, f"note:{wide_id}")
    with contextlib.suppress(AssertionError):
        await expect(page.locator("#item-page")).to_contain_text(words(note["why"]), timeout=SLOW)
    page_text = words(await page.locator("#item-page").inner_text())
    checks = {
        "tables as wide as the page": not problems,
        "the ids recall gives": all(seen.get(name) == ids for name, ids in expected.items()),
        "nine project-wide notes": len(expected["project_wide_notes"]) == 9,
        "closed with linked entries": closed_with_entries,
        "open with none": open_without,
        "three tables or more": tables >= 3,
        "the whole text in the title of the cell": words(note["text"]) in words(whole),
        "three lines, and Show whole text puts every word in the cell": wraps
        and words(note["text"]) in shown,
        "the whole text on the record's page": words(note["text"].partition("\n")[2]) in page_text,
        "the whole why on the record's page": words(note["why"]) in page_text,
    }
    failed = [name for name, held in checks.items() if not held]
    proof(
        "M20",
        f"on the epic with nine long project-wide notes and linked notes, the {tables} "
        "tables of notes (in force, project-wide, earlier) are as wide as the page "
        "at 1280 and at 1600 pixels, do not scroll sideways, keep a row to three lines with no "
        "empty column, hold exactly the ids recall gives, a row opens the note's page where "
        "the whole text is, and the project-wide disclosure is closed when the epic has linked "
        "entries and open when it has none",
        not failed,
        f"{tables} tables, rows at most {max(heights.values())} px high"
        + (f"; {problems[:2]}" if problems else "")
        + (f"; not so: {failed}" if failed else ""),
    )
    assert w.problems == [], w.problems
    await page.set_viewport_size({"width": 1500, "height": 1500})


# -- 4 (continued). a record's own page does what the side panel did ---------------------------------


async def records_change_on_their_pages(w: Watched, client: Client, world: World) -> None:
    page = w.page
    word = world.word
    dialog = page.locator("dialog.modal")
    problem = page.locator("dialog.modal form > .problem")

    # Confirming: a person is named, a blank is refused in the tool's words, and nothing changes.
    target = world.nine[4]
    await visit(w, f"#/item?item=note:{target}&from=board", fresh=True)
    await on_item(page, f"note:{target}")
    chip = page.locator("#item-page .state-strip dd.status .chip.note-status")
    said = board_refusal(
        project_at(w.board), "set_confirmation", note_id=target, confirmation="confirmed", person=""
    )
    await pick_from(page, chip, "confirm")
    await dialog.locator("#confirm-person").fill("")
    await dialog.get_by_role("button", name="Confirm", exact=True).click()
    await expect(problem).to_be_visible(timeout=SLOW)
    plain((await problem.inner_text()).strip(), said)
    untouched = (await ok(client, "get_note", note_id=target))["status"]
    await dialog.locator("#confirm-person").fill("Maya Planner")
    await dialog.locator("#confirm-quote").fill(f"Reviewed on the Tuesday call {word}")
    await dialog.get_by_role("button", name="Confirm", exact=True).click()
    await expect(dialog).to_have_count(0, timeout=SLOW)
    await expect(chip).to_have_attribute("data-status", "confirmed", timeout=SLOW)
    confirmed = await ok(client, "get_note", note_id=target)
    await expect(page.locator("#item-page [data-part=confirmation]")).to_contain_text(
        "Confirmed by Maya Planner"
    )

    # Replacing: a new note that points back, and an old one that says it was replaced.
    old = world.nine[2]
    await visit(w, f"#/item?item=note:{old}&from=board", fresh=True)
    await on_item(page, f"note:{old}")
    await page.locator("#item-page button.supersede").click()
    await dialog.locator("#supersede-text").fill(
        f"Refunds always say why, in writing {word}\n\nEvery refund carries a written reason"
    )
    await dialog.locator("#supersede-why").fill("Support asked for it to be in writing")
    await dialog.locator("#supersede-by").fill("Priya Nair")
    await dialog.get_by_role("button", name="Replace with this note").click()
    await expect(dialog).to_have_count(0, timeout=SLOW)
    await expect(page.locator("#record-ended")).to_be_visible(timeout=SLOW)
    replaced = await ok(client, "get_note", note_id=old)
    new = replaced["superseded_by_id"]
    forward = page.locator("[data-part=replaces] tr.record-line[data-relation='Replaced by']")
    await forward.locator("a.record-title").click()
    await on_item(page, f"note:{new}")
    successor = await ok(client, "get_note", note_id=new)
    await expect(page.locator("[data-part=replaces] tr.record-line").first).to_be_visible(
        timeout=SLOW
    )
    onward = await concerned(page, "replaces")
    await page.locator(
        "[data-part=replaces] tr.record-line[data-relation='Replaces'] a.record-title"
    ).click()
    await on_item(page, f"note:{old}")

    # Withdrawing: kept, marked, with the reason.
    gone = world.nine[3]
    await visit(w, f"#/item?item=note:{gone}&from=board", fresh=True)
    await on_item(page, f"note:{gone}")
    await page.locator("#item-page button.withdraw").click()
    await dialog.locator("#withdraw-reason").fill(f"The team dropped it {word}")
    await dialog.get_by_role("button", name="Withdraw", exact=True).click()
    await expect(dialog).to_have_count(0, timeout=SLOW)
    await expect(page.locator("#record-ended")).to_contain_text("ithdrawn", timeout=SLOW)
    withdrawn = await ok(client, "get_note", note_id=gone)

    # What it concerns: one more story, shown in the table and said by the tool.
    linked = world.nine[5]
    await visit(w, f"#/item?item=note:{linked}&from=board", fresh=True)
    await on_item(page, f"note:{linked}")
    await expect(page.locator("[data-part=concerns] tr.record-line").first).to_be_visible(
        timeout=SLOW
    )
    before = await concerned(page, "concerns")
    await page.locator("#item-page button.change-concerns").click()
    await dialog.locator("input[type=text]").first.fill("S-2")
    await dialog.locator("#concerns-add").click()
    await expect(dialog.locator(f".concerns-list li[data-ref='{world.stories[1]}']")).to_be_visible(
        timeout=SLOW
    )
    await dialog.locator("#concerns-done").click()
    await expect(dialog).to_have_count(0)
    await page.wait_for_function(
        "(ref) => document.querySelector(`[data-part=concerns] tr[data-ref='${ref}']`) !== null",
        arg=world.stories[1],
        timeout=SLOW,
    )
    after = await concerned(page, "concerns")
    linked_now = await ok(client, "get_note", note_id=linked)

    # A save over an agent's change is refused in the server's words, and the draft is kept.
    quiet_epic = world.epics[3]
    await visit(w, f"#/item?item={quiet_epic}&from=board", fresh=True)
    await on_item(page, quiet_epic)
    await hold_back_news(page)
    await open_editor(page)
    await page.locator("#edit-title").fill("My own title")
    await page.locator("#edit-body").fill("My own words, not saved yet")
    await ok(
        client, "update_node", node=quiet_epic, expected_version=1,
        title=f"Agent title {word}", body="The agent's words",
    )  # fmt: skip
    await page.get_by_role("button", name="Save changes").click()
    stale = page.locator("#item-details form.edit .problem .problem-text")
    await expect(stale).to_be_visible(timeout=SLOW)
    plain(
        (await stale.inner_text()).strip(),
        await refused(
            client, "update_node", node=quiet_epic, expected_version=1,
            title="My own title", body="My own words, not saved yet",
        ),
    )  # fmt: skip
    draft = [
        await page.locator("#edit-title").input_value(),
        await page.locator("#edit-body").input_value(),
    ]
    agent_kept = await ok(client, "get_node", node=quiet_epic)
    await page.get_by_role("button", name=re.compile(r"^Load the current version")).click()
    await expect(page.locator("#edit-title")).to_have_value(f"Agent title {word}", timeout=SLOW)
    await page.unroute("**/board/api/projects/*/tool/changes_since")
    checks = {
        "a blank person left it unconfirmed": untouched == "unconfirmed",
        "then it was confirmed by the person named": confirmed["confirmed_by"] == "Maya Planner",
        "the old note was replaced": replaced["status"] == "superseded",
        "the new one replaces it": successor["supersedes_ids"] == [old],
        "and concerns what the old one did": successor["nodes"] == replaced["nodes"],
        "its page links back to the old one": onward == {f"note:{old}"},
        "the withdrawn one is withdrawn": withdrawn["status"] == "withdrawn",
        "the story is in the concerns table": after == before | {world.stories[1]},
        "the table is what the tool says": set(linked_now["nodes"]) == after - {"project"},
        "the draft stayed in the boxes": draft == ["My own title", "My own words, not saved yet"],
        "the agent's change was kept": agent_kept["version"] == 2
        and agent_kept["title"] == f"Agent title {word}",
    }
    failed = [name for name, held in checks.items() if not held]
    proof(
        "M20",
        "on the page of a note, its one status chip confirms it only for a person (a blank is "
        "refused in the tool's own words and nothing changes), replacing makes a new note that "
        "the old one's page and the new one's page link to each other, withdrawing keeps the "
        "note marked withdrawn with its reason, and changing what it concerns shows the added "
        "story in the table; on an epic's page a save over an agent's change is refused in the "
        "server's words and the draft stays in the boxes, as the side panel did",
        not failed,
        f"note {old} replaced by {new}, note {gone} withdrawn"
        + (f"; not so: {failed}; before {sorted(before)}, after {sorted(after)}" if failed else ""),
    )
    assert w.problems == [] or all("status of 4" in p for p in w.problems), w.problems


# -- 5. Search, Possible duplicates, Activity and Coverage are pages that equal their tools --------

RESULTS = """() => [...document.querySelectorAll('#search-results tr.result')]
    .map((e) => [e.dataset.type, e.dataset.ref, e.dataset.matched])"""
FACETS = """() => Object.fromEntries([...document.querySelectorAll('fieldset.facet')].map((f) => [
    f.dataset.facet, Object.fromEntries([...f.querySelectorAll('button.facet-button')]
        .map((b) => [b.dataset.value, Number(b.dataset.count)]))]))"""


async def search_pages(client: Client, query: str) -> dict[str, Any]:
    """Every page of `search` (words and meaning together, its default), put together, with the
    counts the first page gives."""
    first = await ok(client, "search", query=query, limit=200)
    items = list(first["items"])
    page = first
    while page["next_offset"] is not None:
        page = await ok(client, "search", query=query, limit=200, offset=page["next_offset"])
        items.extend(page["items"])
    return {**first, "items": items}


async def search_equals_the_tool(w: Watched, client: Client, world: World) -> None:
    page = w.page
    nodes = {n["ref"]: n for n in await all_nodes(client)}

    def epic_of(ref: str) -> str | None:
        node = nodes.get(ref)
        while node and node["kind"] != "epic":
            node = nodes.get(node["parent"]) if node["parent"] else None
        return node["ref"] if node else None

    differences: list[str] = []
    answer = await search_pages(client, "refund")
    await visit(w, "#/search?search=refund", fresh=True)
    await expect(page.locator("#search-count")).to_have_text(
        re.compile(rf"^{answer['total']} results?\b"), timeout=SLOW
    )
    rows = [tuple(row) for row in await page.evaluate(RESULTS)]
    wanted = [(i["item_type"], i["ref"], i["matched"]) for i in answer["items"]]
    facets = await page.evaluate(FACETS)
    by_epic: dict[str, int] = {}
    for item in answer["items"]:
        epic = epic_of(item["ref"]) if item["item_type"] in ("epic", "story", "task") else None
        if epic:
            by_epic[str(number_of(epic))] = by_epic.get(str(number_of(epic)), 0) + 1
    if rows != wanted:
        differences.append(f"rows {rows} != {wanted}")
    if facets.get("type") != answer["counts_by_type"]:
        differences.append(f"types {facets.get('type')} != {answer['counts_by_type']}")
    if facets.get("status") != answer["counts_by_status"]:
        differences.append(f"statuses {facets.get('status')} != {answer['counts_by_status']}")
    if facets.get("epic", {}) != by_epic:
        differences.append(f"epics {facets.get('epic')} != {by_epic}")
    # A story without the word that is about the same thing is found by its meaning alone.
    meaning_only = {
        i["ref"] for i in answer["items"] if i["item_type"] == "story" and i["matched"] == "meaning"
    }
    # There is no choice of how to search, and an old address that names a mode is ignored.
    no_choice = await page.locator("#search-mode").count() == 0
    await visit(w, "#/search?search=refund&search_mode=words", fresh=True)
    await expect(page.locator("#search-count")).to_have_text(
        re.compile(rf"^{answer['total']} results?\b"), timeout=SLOW
    )
    old_address_ignored = [tuple(row) for row in await page.evaluate(RESULTS)] == wanted
    await visit(w, "#/search?search=refund", fresh=True)
    await expect(page.locator("#search-count")).to_have_text(
        re.compile(rf"^{answer['total']} results?\b"), timeout=SLOW
    )
    # Narrowing by a kind keeps exactly that kind, as the counts said.
    await page.locator(
        "fieldset.facet[data-facet=type] button.facet-button[data-value=task]"
    ).click()
    await expect(page.locator("#search-results tr.result")).to_have_count(
        answer["counts_by_type"]["task"], timeout=SLOW
    )
    kinds_left = {row[0] for row in await page.evaluate(RESULTS)}

    # With the embedding service gone the page says so, in the server's words, and shows the
    # exact-words results the tool gives.
    with embeddings_stopped():
        down = await search_pages(client, "refund")
        await visit(w, "#/search?search=refund", fresh=True)
        await expect(page.locator("#search-note, #search-problem").first).to_be_visible(
            timeout=SLOW
        )
        await page.wait_for_function(
            "(note) => document.body.innerText.replace(/\\s+/g, ' ').includes(note)",
            arg=words(down["index"]["note"]),
            timeout=SLOW,
        )
        stopped_rows = [tuple(row) for row in await page.evaluate(RESULTS)]
        stopped_state = down["index"]["meaning"]
    proof(
        "M20",
        "the Search page for 'refund' lists exactly what search returns (words and meaning "
        "together, the only way the page searches: it has no choice of mode, and an old address "
        "that names one is ignored) in the same order, with the same counts by kind and status "
        "and the epic counts worked out from the results; a story that has not the word but says "
        "the same is found by meaning; narrowing by a kind keeps that kind; and with the "
        "embedding service stopped it shows the server's own note and the exact-words results",
        not differences
        and answer["total"] > 0
        and no_choice
        and old_address_ignored
        and meaning_only == {world.twin_story}
        and kinds_left == {"task"}
        and stopped_state != "used"
        and stopped_rows == [(i["item_type"], i["ref"], i["matched"]) for i in down["items"]],
        f"{answer['total']} results" + (f"; {differences[:2]}" if differences else ""),
    )
    assert w.problems == [] or all("status of 4" in p or "status of 5" in p for p in w.problems), (
        w.problems
    )


PAIRS = """() => [...document.querySelectorAll('#dup-pairs tr.pair')]
    .map((e) => [e.dataset.first, e.dataset.second])"""


async def duplicates_equal_find_overlaps(w: Watched, client: Client, world: World) -> None:
    page = w.page
    await expect_index_ready(client)
    found = await ok(client, "find_overlaps", limit=200)
    pairs = {(p["first"]["ref"], p["second"]["ref"]) for p in found["items"]}
    await visit(w, "#/search?dups=1", fresh=True)
    await expect(page.locator("#dup-pairs tr.pair").first).to_be_visible(timeout=SLOW)
    shown = {tuple(pair) for pair in await page.evaluate(PAIRS)}
    shown_count = words(await page.locator("#dup-count").inner_text())
    before = len(await export_links(client))
    twin = page.locator(
        f"tr.pair[data-first='{world.stories[3]}'][data-second='{world.twin_story}']"
    )
    await twin.locator("button.mark-relates_to").click()
    await expect(twin.locator("button.mark-relates_to")).to_be_disabled(timeout=SLOW)
    after = await export_links(client)
    made = [
        link for link in after
        if {link["source"], link["target"]} == {world.stories[3], world.twin_story}
    ]  # fmt: skip
    # Searching and looking at pairs never writes.
    quiet = len(after) == before + 1
    proof(
        "M20",
        "Possible duplicates, a tab of the Search page, lists exactly the pairs find_overlaps "
        "returns with its count, and one click on the button that marks two stories as related "
        "makes exactly one link between the two stories (relates to), and the button then says "
        "it is done",
        shown == pairs
        and len(pairs) == found["total"] == 1
        and str(found["total"]) in shown_count
        and quiet
        and len(made) == 1
        and made[0]["link_type"] == "relates_to",
        f"{shown_count}; {len(after)} links now",
    )


async def export_links(client: Client) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = (await export_everything(client))["links"]
    return links


async def expect_index_ready(client: Client) -> None:
    for _ in range(60):
        index = (await ok(client, "server_status"))["index"]
        if index["meaning"] == "used" and index["waiting_to_index"] == 0:
            return
        await asyncio.sleep(1)
    raise Failed("M20: the search index did not catch up")


LIVE_CLAIMS = """() => [...document.querySelectorAll('#agents li.claim[data-live="1"]')]
    .map((e) => [e.dataset.node, e.dataset.actor, e.querySelector('.countdown').dataset.expires,
                 e.querySelector('.countdown').innerText.trim()])"""


async def read_the_whole_feed(page: Page) -> None:
    """Press Show older changes until there are no older ones, as a person does: each press waits
    for the page it asked for (pressing again before it arrives asks for the same page twice)."""
    rows = page.locator("#feed li.event")
    for _ in range(80):
        older = page.locator("#show-older")
        if not await older.count():
            return
        before = await rows.count()
        await older.click()
        await page.wait_for_function(
            "(n) => document.querySelectorAll('#feed li.event').length > n"
            " || document.getElementById('show-older') === null",
            arg=before,
            timeout=SLOW,
        )


def minutes_left(text: str) -> int | None:
    """'23 h 58 min left' is 1438; '45 min left' is 45; None when it says something else."""
    found = re.fullmatch(r"(?:(\d+) h )?(?:(\d+) min )?left", text.strip())
    if not found or not (found.group(1) or found.group(2)):
        return None
    return int(found.group(1) or 0) * 60 + int(found.group(2) or 0)


async def who_is_working_now(client: Client) -> dict[str, Any]:
    return await ok(client, "who_is_working")


async def activity_equals_the_tools(w: Watched, client: Client, world: World) -> None:
    page = w.page
    await visit(w, "#/activity", fresh=True)
    await expect(page.locator("#feed li.event").first).to_be_visible(timeout=SLOW)
    await read_the_whole_feed(page)
    events = await events_of(client)
    shown = await feed_ids(page)
    working = await who_is_working_now(client)
    handoff = next(a["last_handoff"] for a in working["agents"] if a["last_handoff"])
    note_on_page = words(await page.locator("#feed li.event .handoff-note").first.inner_text())
    # Who is working on what is a view of its own, chosen with the toggle under the title.
    await open_working(page)
    held = {
        (hold["node"], agent["actor"], hold["expires_at"])
        for agent in working["agents"]
        for hold in agent["holding"]
    }
    live = [tuple(row) for row in await page.evaluate(LIVE_CLAIMS)]
    now = datetime.datetime.fromisoformat(working["as_of"])
    left_ok = all(
        (left := minutes_left(text)) is not None
        and abs(left - (datetime.datetime.fromisoformat(expires) - now).total_seconds() / 60) < 3
        for _, _, expires, text in live
    )
    two_holders = {actor for _, actor, _, _ in live} == {"mobile-agent", ACTOR}

    # Taking, renewing and releasing, from the buttons, as the person who is signed in.
    me = ACTOR
    await open_free(page)
    await expect(page.locator("#claiming-as")).to_contain_text(me)
    await page.locator("#claim-id").fill("T-3")
    await page.locator("#claim-by-id-go").click()
    await open_working(page)
    mine = page.locator(f"#agents li.claim[data-node='task:3'][data-actor='{me}']")
    await expect(mine).to_be_visible(timeout=SLOW)
    first_expiry = await mine.locator(".countdown").get_attribute("data-expires") or ""
    took = next(
        hold
        for agent in (await who_is_working_now(client))["agents"]
        if agent["actor"] == me
        for hold in agent["holding"]
        if hold["node"] == "task:3"
    )
    await page.wait_for_timeout(1100)
    await mine.locator("button.renew").click()
    await expect(mine.locator(".countdown")).not_to_have_attribute(
        "data-expires", first_expiry, timeout=SLOW
    )
    renewed = next(
        hold
        for agent in (await who_is_working_now(client))["agents"]
        if agent["actor"] == me
        for hold in agent["holding"]
        if hold["node"] == "task:3"
    )
    note = f"Got as far as the form; the totals are still open {world.word}"
    await mine.locator("button.release").click()
    await page.locator("#release-note").fill(note)
    await page.get_by_role("button", name="Release it").click()
    await expect(mine).to_have_count(0, timeout=SLOW)
    after = await who_is_working_now(client)
    still_held = [
        hold["node"]
        for agent in after["agents"]
        if agent["actor"] == me
        for hold in agent["holding"]
    ]
    newest = (await events_of(client))[-3:]
    await show_tab(page, "changes")
    await expect(page.locator("#feed li.event").first).to_contain_text(me, timeout=SLOW)
    released_note = words(await page.locator("#feed li.event .handoff-note").first.inner_text())
    checks = {
        "the feed is changes_since, newest first": shown
        == [event["id"] for event in reversed(events)],
        "the claims in force are who_is_working's": {(n, a, e) for n, a, e, _ in live} == held,
        "held by an agent and by the person signed in": two_holders,
        "each countdown is the time to the tool's expiry": left_ok,
        "the handoff note is in full": note_on_page == words(handoff["note"]),
        "Renew gave a later expiry": renewed["expires_at"] > took["expires_at"],
        "Release let go of the claim": "task:3" not in still_held,
        "and left a handoff event": any(
            event["action"] == "handoff" and (event["after"] or {}).get("note") == note
            for event in newest
        ),
        "with the note in full in the feed": released_note == words(note),
    }
    failed = [name for name, held_up in checks.items() if not held_up]
    proof(
        "M20",
        f"the Activity page lists exactly the {len(events)} changes changes_since gives, newest "
        "first; its Working view, chosen with the toggle, shows the claims in force that "
        "who_is_working gives, each with a countdown that is the time to the tool's expiry; the "
        "handoff note is shown in full; and Claim, Renew and Release from the page change what "
        "the tool says (a later expiry after Renew, no claim and a note in the feed after "
        "Release)",
        not failed,
        f"{len(shown)} changes, {len(live)} claims in force"
        + (f"; not so: {failed}; newest {[event['action'] for event in newest]}" if failed else ""),
    )
    assert w.problems == [], w.problems


async def coverage_equals_the_tool(w: Watched, client: Client, world: World) -> None:
    page = w.page
    report = await whole_coverage_report(client)
    await visit(w, "#/coverage", fresh=True)
    summary = page.locator("#coverage-summary")
    await expect(summary).to_have_attribute(
        "data-unplaced", str(report["unplaced_rule_count"]), timeout=SLOW
    )
    rules: list[int] = [
        int(n)
        for n in await page.locator("#unplaced-rules tr[data-rule]").evaluate_all(
            "els => els.map((e) => e.dataset.rule)"
        )
    ]
    stories: list[str] = await page.locator("#stories-without-rules tr[data-story]").evaluate_all(
        "els => els.map((e) => e.dataset.story)"
    )
    said = {
        "unplaced": await summary.get_attribute("data-unplaced"),
        "without": await summary.get_attribute("data-stories-without"),
    }
    placed = world.rules[3]
    await page.locator(f"tr[data-rule='{placed}'] button.put-requirement").click()
    await choose_place(page.locator("dialog.modal"), world.stories[2])
    await page.locator("dialog.modal").get_by_role("button", name="Put it there").click()
    await expect(page.locator("dialog.modal")).to_have_count(0, timeout=SLOW)
    await expect(summary).to_have_attribute(
        "data-unplaced", str(report["unplaced_rule_count"] - 1), timeout=SLOW
    )
    after = await whole_coverage_report(client)
    rules_after: list[int] = [
        int(n)
        for n in await page.locator("#unplaced-rules tr[data-rule]").evaluate_all(
            "els => els.map((e) => e.dataset.rule)"
        )
    ]
    stories_after: list[str] = await page.locator(
        "#stories-without-rules tr[data-story]"
    ).evaluate_all("els => els.map((e) => e.dataset.story)")
    proof(
        "M20",
        f"the coverage page says {report['unplaced_rule_count']} acceptance criteria are placed "
        f"nowhere and {report['story_without_rules_count']} stories have none, lists exactly "
        "the ones coverage_report lists, and Put on a story or task changes both the page and "
        "the report together (the story that got the acceptance criterion leaves the list)",
        said
        == {
            "unplaced": str(report["unplaced_rule_count"]),
            "without": str(report["story_without_rules_count"]),
        }
        and rules == [r["id"] for r in report["unplaced_rules"]]
        and stories == [s["ref"] for s in report["stories_without_rules"]]
        and rules_after == [r["id"] for r in after["unplaced_rules"]]
        and stories_after == [s["ref"] for s in after["stories_without_rules"]]
        and world.stories[2] not in stories_after
        and placed not in rules_after,
        f"{len(rules)} unplaced, {len(stories)} stories without",
    )


# -- 6. the Graph looks around, then opens; the Roadmap still drags and opens ------------------------


async def the_graph_looks_around_then_opens(w: Watched, client: Client, world: World) -> None:
    page = w.page
    story, task, epic = world.stories[1], "task:6", world.epics[1]
    node = await ok(client, "get_node", node=story)
    await visit(w, f"#/graph?tab=around&focus={story}", fresh=True)
    hub = page.locator("#graph-hub")
    await expect(hub).to_have_attribute("data-ref", story, timeout=SLOW)

    async def back_to_the_graph(choice: str) -> None:
        await page.locator("nav.crumbs li.crumb-view a").click()
        await expect(hub).to_have_attribute("data-ref", choice, timeout=SLOW)

    # The hub says what the focus is, with its status as the tool says, and links to its page.
    box = words(await hub.inner_text())
    chip = await hub.locator(".chip.status").first.get_attribute("data-status")
    still_on_the_graph = page_of(hash_of(page)) == "graph" and f"focus={story}" in hash_of(page)
    # Open page opens it, under the Graph, and the way back keeps the focus.
    await page.locator("#graph-hub-open").click()
    await on_item(page, story)
    by_link = (await crumbs(page))[0] == "Graph"
    await back_to_the_graph(story)
    # A piece of work around it looks around that one instead, in the address.
    neighbour = page.locator("button.graph-node.n-work[data-ref]").first
    other = await neighbour.get_attribute("data-ref") if await neighbour.count() else None
    if other:
        await neighbour.click()
        await expect(hub).to_have_attribute("data-ref", other, timeout=SLOW)
        await page.locator("#graph-hub-open").click()
        await on_item(page, other)
        await back_to_the_graph(other)
    # A task and an epic by their address, and the same page opens from each.
    opened = []
    for ref in (task, epic):
        await w.go(f"#/graph?tab=around&focus={ref}")
        await expect(hub).to_have_attribute("data-ref", ref, timeout=SLOW)
        await page.locator("#graph-hub-open").click()
        await on_item(page, ref)
        opened.append(query_of(hash_of(page)).get("from") == "graph")
    proof(
        "M20",
        "on the Graph's Around the hub says what the focus is, with its status as the tool says, "
        "and Open page opens the record's page under the Graph without losing the focus; the "
        "way back keeps it; a piece of work around it looks around that one instead; and a "
        "task and an epic open the same way",
        code_of(story) in box
        and node["title"] in box
        and chip == node["status"]
        and still_on_the_graph
        and by_link
        and other is not None
        and all(opened),
        f"{story}, {task} and {epic} looked at and opened",
    )
    assert w.problems == [], w.problems


async def the_roadmap_still_works(w: Watched, client: Client, world: World) -> None:
    page = w.page
    moved, keyed, opened = world.stories[0], world.epics[0], world.stories[1]
    before_moved = await ok(client, "get_node", node=moved)
    before_keyed = await ok(client, "get_node", node=keyed)
    await visit(w, "#/roadmap", fresh=True)
    await expect(page.locator("#rm-scroll[data-ready=true] .rm-bar").first).to_be_visible(
        timeout=SLOW
    )
    await drag_bar(page, moved, 3)
    await page.wait_for_function(
        """([ref, was]) => { const bar = document.querySelector(`.rm-bar[data-ref='${ref}']`);
            return bar !== null && bar.dataset.start !== was; }""",
        arg=[moved, before_moved["start_on"]],
        timeout=SLOW,
    )
    after_moved = await ok(client, "get_node", node=moved)
    bar = page.locator(f".rm-bar[data-ref='{keyed}']")
    await bar.focus()
    await page.keyboard.press("Shift+ArrowRight")
    await page.wait_for_timeout(1200)
    after_keyed = await ok(client, "get_node", node=keyed)
    # A bar opens its page: by Enter, and by a click.
    await page.locator(f".rm-bar[data-ref='{opened}']").focus()
    await page.keyboard.press("Enter")
    await on_item(page, opened)
    by_enter = (await crumbs(page))[0] == "Roadmap"
    await page.locator("nav.crumbs li.crumb-view a").click()
    await expect(page.locator(f".rm-bar[data-ref='{moved}']")).to_be_visible(timeout=SLOW)
    await page.locator(f".rm-bar[data-ref='{moved}']").click(position={"x": 30, "y": 8})
    await on_item(page, moved)
    by_click = (await crumbs(page))[0] == "Roadmap"

    def shifted(first: str | None, second: str | None, days: int) -> bool:
        return (
            first is not None
            and second is not None
            and (datetime.date.fromisoformat(second) - datetime.date.fromisoformat(first)).days
            == days
        )

    proof(
        "M20",
        "on the Roadmap a drag of a bar moves the story's dates by the days dragged (3), an "
        "arrow key with Shift moves an epic by a week, both as the tool reads them, and a bar "
        "opens its record's page under the Roadmap by Enter and by a click",
        shifted(before_moved["start_on"], after_moved["start_on"], 3)
        and shifted(before_moved["target_on"], after_moved["target_on"], 3)
        and shifted(before_keyed["start_on"], after_keyed["start_on"], 7)
        and shifted(before_keyed["target_on"], after_keyed["target_on"], 7)
        and by_enter
        and by_click,
        f"{moved} to {after_moved['start_on']}, {keyed} to {after_keyed['start_on']}",
    )
    assert w.problems == [], w.problems


# -- 7. no sideways scrolling, names, focus and the keyboard at four widths in both themes -----------

AUDIT = """() => {
    const doc = document.documentElement;
    const shown = (e) => {
        const box = e.getBoundingClientRect();
        const style = getComputedStyle(e);
        return (box.width > 0 || box.height > 0) && style.visibility !== 'hidden' && style.display !== 'none';
    };
    const text = (e) => (e.innerText || e.textContent || '').replace(/\\s+/g, ' ').trim();
    const named = (e) => {
        if ((e.getAttribute('aria-label') || '').trim()) return true;
        const by = e.getAttribute('aria-labelledby');
        if (by && by.split(/\\s+/).some((id) => text(document.getElementById(id) || document.body) !== '' && document.getElementById(id))) return true;
        if (e.labels && [...e.labels].some((l) => text(l))) return true;
        if (['BUTTON', 'A', 'SUMMARY'].includes(e.tagName) || e.getAttribute('role')) {
            if (text(e)) return true;
            if (e.querySelector('svg title, img[alt]:not([alt=""])')) return true;
        }
        if (e.tagName === 'INPUT' && ['submit', 'button'].includes(e.type) && e.value) return true;
        return Boolean((e.getAttribute('title') || '').trim());
    };
    const nameless = [...document.querySelectorAll(
        'button, a[href], input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab]')]
        .filter((e) => shown(e) && !named(e))
        .map((e) => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + '.' + String(e.className).slice(0, 40));
    return {
        sideways: Math.max(doc.scrollWidth, document.body.scrollWidth) - window.innerWidth,
        theme: doc.dataset.theme,
        headings: document.querySelectorAll('#page h1').length,
        nameless,
    };
}"""

FOCUS_RING = """() => {
    const e = document.activeElement;
    if (!e || e === document.body) return 'nothing is focused';
    const s = getComputedStyle(e);
    const ring = s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0;
    const shadow = s.boxShadow !== 'none' && s.boxShadow !== '';
    return ring || shadow ? '' : 'no ring on ' + e.tagName.toLowerCase() + '#' + e.id + '.' + e.className;
}"""


# The pages on which the first controls a keyboard reaches are checked for a visible focus ring.
FOCUS_PAGES = ("the Board", "an epic", "Search")


def layout_pages(world: World) -> list[tuple[str, str, str]]:
    """Every page of the board that holds content: its address, and what shows it is drawn."""
    epic, story, task = world.epics[0], world.stories[1], "task:7"
    rule = f"rule:{world.rules[2]}"
    decided, note = f"note:{world.decided_kept}", f"note:{world.note_wide}"
    return [
        ("the Board", "#/board", "article.card"),
        ("the Graph", "#/graph", "#graph-explore"),
        ("the Graph's Around", f"#/graph?tab=around&focus={epic}", "#graph-hub"),
        ("the Graph's Order", "#/graph?tab=order", "#graph-explore"),
        ("the Roadmap", "#/roadmap", "#rm-scroll[data-ready=true] .rm-bar"),
        ("Lists of work", "#/lists?view=work", "#work-tree li"),
        ("Lists of acceptance criteria", "#/lists?view=requirements", "#lists-list .gv-record"),
        ("Lists of notes", "#/lists?view=notes", "#ctx-notes-list tr.record-line"),
        ("Search", "#/search?search=refund", "#search-results tr.result"),
        ("Possible duplicates", "#/search?dups=1", "#dup-pairs tr.pair"),
        ("Activity", "#/activity", "#feed li.event"),
        ("Activity, who is working", "#/activity?tab=working", "#agents li.claim"),
        ("the coverage report", "#/coverage", "#coverage-summary"),
        ("an epic", f"#/item?item={epic}&from=board", f"#item-page[data-ref='{epic}'] #item-table"),
        ("a story", f"#/item?item={story}&from=graph", f"#item-page[data-ref='{story}'] #item-table"),
        ("a task", f"#/item?item={task}&from=roadmap", f"#item-page[data-ref='{task}'] #item-details"),
        ("an acceptance criterion", f"#/item?item={rule}&from=board", f"#item-page[data-ref='{rule}'] #item-details"),
        ("a decided note", f"#/item?item={decided}&from=search", f"#item-page[data-ref='{decided}'] #item-details"),
        ("a note", f"#/item?item={note}&from=activity", f"#item-page[data-ref='{note}'] #item-details"),
        ("Users and agent keys", "#/users", "#users-agents"),
        ("Definition of done", "#/definition", "#definition-page"),
        ("Tools", "#/tools", "#tool-list"),
        ("Status and export", "#/status", "#download-export"),
    ]  # fmt: skip


async def go_to(w: Watched, address: str, marker: str) -> None:
    """Change the address as a link does and wait for what shows that page is there."""
    await w.go(address)
    await expect(w.page.locator(marker).first).to_be_visible(timeout=SLOW)


async def keyboard_journey(w: Watched, narrow: bool) -> list[str]:
    """Use the board with the keyboard alone: open the sidebar if it is off to the side, walk its
    tree, open a record, move to the next one, and jump with Ctrl+K. Returns what went wrong."""
    page = w.page
    wrong: list[str] = []
    await visit(w, "#/board", fresh=True)
    if narrow:
        await page.locator("#sidebar-toggle").focus()
        await page.keyboard.press("Enter")
        if await page.locator("#sidebar-toggle").get_attribute("aria-expanded") != "true":
            wrong.append("the menu button did not open the sidebar")
    first = page.locator("#epic-list li[role=treeitem][tabindex='0']")
    await first.focus()
    await page.keyboard.press("ArrowRight")
    await page.keyboard.press("ArrowDown")
    await page.keyboard.press("Enter")
    await expect(page.locator("#item-page")).to_have_attribute("data-kind", "story", timeout=SLOW)
    await expect(page.locator("#item-page [data-part=history]").first).to_be_attached(timeout=SLOW)
    if not await page.locator("#item-title").evaluate("e => e === document.activeElement"):
        wrong.append("the page's title did not take the focus")
    here = await page.locator("#item-page").get_attribute("data-ref") or ""
    await page.keyboard.press("]")
    try:
        await expect(page.locator("#item-page")).not_to_have_attribute(
            "data-ref", here, timeout=SLOW
        )
    except AssertionError:
        focus = await page.evaluate("document.activeElement.outerHTML.slice(0, 100)")
        wrong.append(f"the key ] did not move on from {here} (focus is on {focus})")
        return wrong
    await page.keyboard.press("Control+k")
    await page.locator("#switcher-input").fill("Receipts")
    await expect(page.locator("#switcher-list [role=option]").first).to_be_visible(timeout=SLOW)
    await page.keyboard.press("Enter")
    await expect(page.locator("#item-title")).to_contain_text("Receipts", timeout=SLOW)
    return wrong


async def no_sideways_scroll_anywhere(w: Watched, client: Client, world: World) -> None:
    page = w.page
    pages = layout_pages(world)
    problems: list[str] = []
    checked = 0
    journeys: list[str] = []
    await visit(w, "#/board", fresh=True)
    for theme in ("light", "dark"):
        await page.emulate_media(color_scheme=theme)
        for width, height in ((390, 844), (768, 1024), (1280, 900), (1600, 1000)):
            await page.set_viewport_size({"width": width, "height": height})
            for name, address, marker in pages:
                await go_to(w, address, marker)
                audit = await page.evaluate(AUDIT)
                where = f"{name} at {width} in {theme}"
                if audit["sideways"] > 0:
                    problems.append(f"{where}: sideways by {audit['sideways']}")
                if audit["theme"] != theme:
                    problems.append(f"{where}: theme {audit['theme']}")
                if audit["nameless"]:
                    problems.append(f"{where}: no name on {audit['nameless'][:3]}")
                if audit["headings"] != 1:
                    problems.append(f"{where}: {audit['headings']} headings")
                left = await panel_parts(page)
                if left:
                    problems.append(f"{where}: {left}")
                checked += 1
            # The focus is visible on the first controls a keyboard reaches.
            for name, address, marker in [p for p in pages if p[0] in FOCUS_PAGES]:
                await go_to(w, address, marker)
                await page.evaluate("document.activeElement && document.activeElement.blur()")
                reached = 0
                for _ in range(6):
                    await page.keyboard.press("Tab")
                    ring = await page.evaluate(FOCUS_RING)
                    if ring == "nothing is focused":
                        continue
                    reached += 1
                    if ring:
                        problems.append(f"{name} at {width} in {theme}: {ring}")
                    if reached == 3:
                        break
                if reached < 3:
                    problems.append(f"{name} at {width} in {theme}: Tab reached {reached} controls")
            if theme == "light" and width in (390, 1280):
                wrong = await keyboard_journey(w, narrow=width < 1100)
                journeys += [f"{width}: {line}" for line in wrong]
    await page.emulate_media(color_scheme="light")
    await page.set_viewport_size({"width": 1500, "height": 1500})
    proof(
        "M20",
        f"at 390, 768, 1280 and 1600 pixels, in the light and the dark theme, {len(pages)} pages "
        "(the Board, the Graph and its tabs, the Roadmap, the Lists, Search, Possible duplicates, "
        "Activity and its Working view, the coverage report, a record page of each kind and the "
        "settings pages) never "
        "scroll sideways, are in the theme asked for, have one heading, give every visible "
        "control a name and hold nothing of a panel or a drawer; the first controls a keyboard "
        "reaches show a focus ring; and a journey by keyboard alone (the menu, the sidebar's "
        "tree, Enter, ], Ctrl+K) works at 390 and at 1280",
        not problems and not journeys,
        f"{checked} page views"
        + (f"; {(problems + journeys)[:3]}" if problems or journeys else ""),
    )
    assert w.problems == [], w.problems


# -- the chapter -------------------------------------------------------------------------------------


async def pages() -> None:
    chapter(
        "Pages: no panel, no drawer; every record is a page and you can move through them (M20)"
    )
    word = unique_word()
    project = make_project(register(name=ACTOR), f"Pages {word}")
    try:
        async with connect(project.mcp_url, project.key) as client:
            world = await build(client, word, project)
            async with browser_page(board_url(project.mcp_url, project), token=project.key) as w:
                await w.open("#/board")
                await nothing_beside_the_page(w, world)
                await records_open_from_everywhere(w, client, world)
                await record_pages_equal_the_tools(w, client, world)
                await moving_through_the_content(w, client, world)
                await the_nine_notes(w, client, world)
                await records_change_on_their_pages(w, client, world)
                await search_equals_the_tool(w, client, world)
                await duplicates_equal_find_overlaps(w, client, world)
                await activity_equals_the_tools(w, client, world)
                await coverage_equals_the_tool(w, client, world)
                await the_graph_looks_around_then_opens(w, client, world)
                await the_roadmap_still_works(w, client, world)
                await no_sideways_scroll_anywhere(w, client, world)
    except BrowserError as error:
        raise Failed(
            f"M20: the board did not do what a proof needs: {clip(str(error), 300)}"
        ) from error
