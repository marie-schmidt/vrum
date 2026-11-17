"""What the tests of the search drawer, Possible duplicates, the Activity drawer and the utilities
pages share: asking the tools the same questions the drawers answer, and reading what the drawers
show. All of it goes through a real MCP client against the real containers; none of it reaches into
the page's own code."""

import re
from collections.abc import Awaitable
from typing import Any
from urllib.parse import parse_qs

from mcp import Client
from playwright.async_api import Locator, Page, expect

from tests.board_helpers import SLOW, made
from tests.helpers import ok, reindexed, unique_word

# The three views the work is seen in; everything else opens beside them.
VIEWS = ("board", "graph", "roadmap")

# Each story and its twin say the same thing in other words (the scripted embedding service gives
# them the same meaning): the pairs of M5's acceptance walk, kept here so these tests need nothing
# from it. The last four twins add a word of their own, so they are alike but not identical.
DUPLICATES = [
    (
        "Customers receive an invoice by email after payment",
        "Clients receive a bill by mail after charge",
    ),
    ("Export the monthly report as a CSV file", "Download the monthly summary as a CSV file"),
    (
        "Users sign in with a password and keep their role",
        "Customers authenticate with credentials and keep their privilege",
    ),
    (
        "Translate every image caption into the customer language",
        "Localisation of every photo caption into the client locale",
    ),
    (
        "Search results appear slow for large projects",
        "Lookup results appear sluggish for large projects today",
    ),
    (
        "Create a backup every night and restore it on demand",
        "Make a snapshot every night and recover it on demand tonight",
    ),
    ("Dark theme for the mobile app", "Night theme for the smartphone app now"),
    (
        "Cancel a subscription and keep an audit trail",
        "Terminate a membership and keep a log history for good",
    ),
]


def view_address(view: str, **choices: str | int) -> str:
    """The hash of a view with these choices, as the board writes it: '#/graph?drawer=search'."""
    query = "&".join(f"{name}={value}" for name, value in choices.items())
    return f"#/{view}{f'?{query}' if query else ''}"


async def address(page: Page) -> tuple[str, dict[str, str]]:
    """The page the address names, and its choices, as the board holds them now."""
    hash_ = await page.evaluate("location.hash")
    name, _, query = hash_.removeprefix("#/").partition("?")
    return name, {key: values[0] for key, values in parse_qs(query).items()}


async def on_view(page: Page, view: str) -> None:
    """The sidebar says this is the view that is open (and only this one)."""
    await expect(page.locator(f"#main-nav a[data-page={view}]")).to_have_attribute(
        "aria-current", "page", timeout=SLOW
    )
    await expect(page.locator("#main-nav a[aria-current=page]")).to_have_count(1)


SETTINGS = ("access", "tools", "status")


async def open_settings(page: Page, name: str) -> None:
    """Go to a settings page with the keyboard only: the Settings button is focused and opened with
    Enter, the arrow keys move to the page, and Enter goes there."""
    await page.locator("#settings-menu").focus()
    await page.keyboard.press("Enter")
    await expect(page.locator("#settings-list")).to_be_visible()
    await expect(page.locator("#settings-menu")).to_have_attribute("aria-expanded", "true")
    await expect(page.locator(f"#settings-{SETTINGS[0]}")).to_be_focused()
    for _ in range(SETTINGS.index(name)):
        await page.keyboard.press("ArrowDown")
    await expect(page.locator(f"#settings-{name}")).to_be_focused()
    await page.keyboard.press("Enter")
    await expect(page.locator("#settings-list")).to_be_hidden()
    await expect(page).to_have_url(re.compile(rf"#/{name}(\?|$)"), timeout=SLOW)
    await expect(page.locator("#settings-menu")).to_have_attribute("aria-current", "page")
    await expect(page.locator("#main-nav a[aria-current=page]")).to_have_count(0)


# -- the search drawer ---------------------------------------------------------------------------


def drawer(page: Page, name: str) -> Locator:
    return page.locator(f"#drawer[data-drawer={name}]")


async def keyboard_to_activity(page: Page) -> None:
    """Open the Activity drawer with the keyboard alone: Activity is in the sidebar, which comes
    first in the page's order, right before the Settings button, so Shift+Tab from Settings
    reaches it and Enter opens it."""
    await page.locator("#settings-menu").focus()
    await page.keyboard.press("Shift+Tab")
    assert await page.evaluate("document.activeElement.id") == "activity-open"
    await page.keyboard.press("Enter")
    await expect(drawer(page, "activity")).to_be_visible(timeout=SLOW)
    await expect(page.locator("#feed li.event").first).to_be_visible(timeout=SLOW)


async def search_in_drawer(page: Page, query: str, mode: str = "both") -> None:
    """Type a search into the drawer's own box and press its button."""
    await page.locator("#search-box").fill(query)
    await page.locator("#search-mode").select_option(mode)
    await page.locator("#search-go").click()


async def result_rows(page: Page, count: int) -> list[dict[str, Any]]:
    """The results the drawer lists, in the order shown, once it says how many there are: the type,
    reference and how each matched (and the id, for a requirement, decision or fact, which keep
    one; None for work), and the words the drawer shows for each."""
    await expect(page.locator("#search-count")).to_contain_text(
        re.compile(rf"^{count} results?\b"), timeout=SLOW
    )
    rows: list[dict[str, Any]] = await page.locator("#search-results li.result").evaluate_all(
        """els => els.map(e => ({
            type: e.dataset.type,
            ref: e.dataset.ref,
            id: e.dataset.id === undefined ? null : Number(e.dataset.id),
            matched: e.dataset.matched,
            chip: e.querySelector('.chip.matched')?.textContent.trim(),
            why: e.querySelector('.why')?.textContent.trim(),
            label: e.querySelector('.record-label')?.textContent.trim() ?? null,
            title: e.querySelector('a.result-title')?.textContent.trim(),
        }))"""
    )
    return rows


async def whole_list(page: Page, count: int) -> list[dict[str, Any]]:
    """The results, after asking the drawer for every further page of them."""
    for _ in range(100):
        more = page.locator("#show-more")
        if await more.count() == 0:
            break
        shown = await page.locator("#search-results li.result").count()
        await more.click()
        await expect(page.locator("#search-results li.result")).not_to_have_count(
            shown, timeout=SLOW
        )
    return await result_rows(page, count)


async def facet(page: Page, name: str) -> dict[str, int]:
    """One group of filter buttons of the drawer: the value of each and its count."""
    rows: list[list[str]] = await page.locator(
        f"#search-facets fieldset.facet[data-facet={name}] button"
    ).evaluate_all("els => els.map(e => [e.dataset.value, e.dataset.count])")
    return {value: int(count) for value, count in rows}


async def asked(
    client: Client, query: str, mode: str = "both", size: int = 200, **filters: Any
) -> dict[str, Any]:
    """What `search` gives for this, every page of it put together (`size` is the page size, so a
    small one walks the paging): the items, and the counts and the total of the first page."""
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await ok(
            client, "search", query=query, mode=mode, limit=size, offset=offset, **filters
        )
        if offset == 0:
            first = page
        items.extend(page["items"])
        if page["next_offset"] is None:
            return {**first, "items": items}
        offset = page["next_offset"]


def triples(items: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """What `search` found, as (type, reference, how it matched): the reference names every kind,
    story:12 as well as decision:7."""
    return [(item["item_type"], item["ref"], item["matched"]) for item in items]


def shown_triples(rows: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """What the drawer lists (from result_rows), in the same shape as triples."""
    return [(row["type"], row["ref"], row["matched"]) for row in rows]


async def epic_of_each(client: Client) -> dict[str, str | None]:
    """The epic (its reference) that every epic, story, task and requirement is in, from the list
    tools: keyed by the item's reference (story:12, rule:3), None for a requirement in no story.
    The drawer's epic filter counts by the epic's number (number_of)."""
    nodes: dict[str, dict[str, Any]] = {}
    after = None
    while True:
        page = await ok(client, "list_nodes", limit=200, after=after)
        nodes.update({node["ref"]: node for node in page["items"]})
        after = page["next_after"]
        if after is None:
            break

    def epic(ref: str) -> str:
        node = nodes[ref]
        return ref if node["kind"] == "epic" else epic(node["parent"])

    found: dict[str, str | None] = {ref: epic(ref) for ref in nodes}
    after_id = None
    while True:
        page = await ok(client, "list_rules", limit=200, after_id=after_id)
        for rule in page["items"]:
            found[f"rule:{rule['id']}"] = None if rule["story"] is None else epic(rule["story"])
        after_id = page["next_after_id"]
        if after_id is None:
            return found


# -- Possible duplicates -------------------------------------------------------------------------


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


def pair_triples(overlaps: dict[str, Any]) -> list[tuple[str, str, float]]:
    """The pairs find_overlaps gave, as (reference, reference, how alike)."""
    return [(p["first"]["ref"], p["second"]["ref"], p["similarity"]) for p in overlaps["items"]]


async def redrawn(page: Page, container: str, action: Awaitable[None]) -> None:
    """Do something that makes the drawer read and draw again, and return once what the container
    held before has been drawn over: a read that has begun is not mistaken for the one before it."""
    await page.evaluate(
        "(selector) => { window.__before = document.querySelector(`${selector} > *`); }", container
    )
    await action
    await page.wait_for_function(
        "(selector) => document.querySelector(`${selector} > *`) !== window.__before",
        arg=container,
        timeout=SLOW,
    )


async def pairs_on(page: Page) -> list[tuple[str, str, float]]:
    """Every pair the drawer lists, in the order shown, asking for more until there is no more:
    (reference, reference, how alike)."""
    await expect(page.locator("#dup-count")).to_contain_text(re.compile(r"pairs?\b"), timeout=SLOW)
    for _ in range(100):
        more = page.locator("#dup-more")
        if await more.count() == 0:
            break
        count = await page.locator("#dup-pairs li.pair").count()
        await more.click()
        await expect(page.locator("#dup-pairs li.pair")).not_to_have_count(count, timeout=SLOW)
    rows: list[list[str]] = await page.locator("#dup-pairs li.pair").evaluate_all(
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


async def link_count(client: Client) -> int:
    """How many links the project holds, as the server says it."""
    return int((await ok(client, "server_status"))["counts"]["links"])
