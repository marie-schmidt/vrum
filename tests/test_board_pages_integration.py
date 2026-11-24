"""M20's proofs 1 to 7 as tests: there is no detail panel and no drawer; every epic, story, task,
acceptance criterion and note has a page of its own, reached from every place that names it, in the
view the person came from; the pages move through the content (previous and next, the sidebar's
tree, the links); what concerns an item is a table across the whole page; Search, Possible
duplicates, Activity and the coverage report are pages that say what the tools say; the Graph's map
chooses a dot before it opens it and the Roadmap's bars still drag; and none of it scrolls sideways
at any width in either theme.

Driven in a real headless browser against the real containers and compared with a real MCP client.
The other board tests (test_board_*_integration.py) prove each page in detail; these prove M20's
statements across pages, and depend on no sample data: each test makes a project of its own."""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import anyio
import pytest
from mcp import Client
from playwright.async_api import Locator, Page, expect

from tests.board_context import board_refusal, choose_in_menu
from tests.board_helpers import (
    KINDS,
    NOTE_STATUS_WORDS,
    SLOW,
    Watched,
    apply_filter,
    board_url,
    browser_page,
    coverage_rows,
    crumbs,
    disclosure,
    drag_bar,
    go,
    item_address,
    item_loaded,
    item_name,
    item_row,
    made,
    no_panel_or_drawer,
    on_page,
    open_editor,
    open_item,
    open_sidebar,
    pager,
    pager_go,
    part,
    plain,
    record_rows,
    record_text,
    search_address,
    siblings,
    sidebar_current,
    strip_labels,
    tree_expand,
    tree_item,
    tree_open,
    tree_rows,
    view_crumb,
)
from tests.board_layout import focus_ring, start_of_page, structure, tab_to
from tests.board_m14 import claim_rows, events_of, feed_ids, open_working, spoken
from tests.board_search import (
    DUPLICATES,
    asked,
    every_overlap,
    facet,
    link_count,
    links_of,
    pair_triples,
    pairs_on,
    shown_triples,
    triples,
    twin_epics,
    whole_list,
)
from tests.board_theme import themed_page
from tests.helpers import (
    all_nodes,
    code_of,
    confirm_on_board,
    connect,
    day,
    make_decision,
    make_fact,
    number_of,
    ok,
    refused,
    set_status,
    unique_word,
    whole_coverage_report,
)
from tests.world import Project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

MARIE = "Marie Schmidt"
WIDE = (
    "Every service reads its settings from the environment and never from a file that is checked "
    "in, so that one copy of the code can run anywhere and nothing secret is ever kept in it."
)


@dataclass
class World:
    project: Project
    board: str
    word: str
    refs: dict[str, str]
    ids: dict[str, int]

    def __getitem__(self, name: str) -> str:
        return self.refs[name]

    def record(self, name: str) -> str:
        """The reference of an acceptance criterion or a note of the world (rule:3, note:7)."""
        kind = "rule" if name.startswith("rule") else "note"
        return f"{kind}:{self.ids[name]}"


async def build(
    client: Client, word: str, project: Project
) -> tuple[dict[str, str], dict[str, int]]:
    """The work of a small shop and what concerns it, with something of every kind: three epics
    (the last has no story), stories with and without tasks, tasks that are held, done, overdue and
    waiting for another, two criteria (one placed, one not), nine long project-wide decisions,
    decisions and facts that concern the first epic (one confirmed, one replaced, one withdrawn of
    each), and texts that name other items."""
    refs: dict[str, str] = {}
    ids: dict[str, int] = {}

    async def node(
        name: str, kind: str, title: str, parent: str | None = None, **more: Any
    ) -> None:
        refs[name] = await made(client, kind, f"{title} {word}", parent, **more)

    await node("epic", "epic", "Ordering", body="The **whole** ordering flow.")
    await node(
        "story",
        "story",
        "Place an order",
        refs["epic"],
        body=f"A *story* that belongs to [[{refs['epic']}]].",
        priority="high",
    )
    await node("form", "task", "Build the form", refs["story"], start_on=day(-2), target_on=day(5))
    await node("api", "task", "Write the API", refs["story"], start_on=day(3), target_on=day(9))
    await node("words", "task", "Polish the words", refs["story"], target_on=day(-6))
    await node("pay", "story", "Pay for it", refs["epic"])
    await node("card", "task", "Take the card", refs["pay"], target_on=day(-4))
    await node("track", "story", "Track it", refs["epic"])
    await node("billing", "epic", "Billing")
    await node("invoice", "story", "Invoice", refs["billing"])
    await node("send", "task", "Send the invoice", refs["invoice"])
    await node("empty", "epic", "Nothing yet")
    # The epic's words name its story, so that a reference and "Mentioned in" lead to a page.
    epic = await ok(client, "get_node", node=refs["epic"])
    await ok(
        client,
        "update_node",
        actor="agent-1",
        node=refs["epic"],
        expected_version=epic["version"],
        body=f"The **whole** ordering flow, which starts at [[{refs['story']}]].",
    )

    await ok(client, "claim_node", node=refs["form"], lease_seconds=86_400)
    await set_status(client, {"ref": refs["words"]}, "done")
    await ok(
        client,
        "add_link",
        actor="agent-1",
        source=refs["api"],
        target=refs["form"],
        link_type="needs_first",
    )

    placed = await ok(
        client, "create_rule", actor="agent-1", title=f"Fast {word}", node=refs["story"]
    )
    loose = await ok(client, "create_rule", actor="agent-1", title=f"Unplaced {word}")
    ids["rule_placed"], ids["rule_loose"] = placed["id"], loose["id"]

    for at in range(9):
        wide = await make_decision(
            client,
            f"Project-wide {at}: {WIDE} {word}",
            text=f"{WIDE} ({at})",
            reason=WIDE,
            project_wide=True,
        )
        ids[f"decision_wide{at}"] = wide["id"]

    epic_ref = refs["epic"]
    kept = await make_decision(client, f"Use REST {word}", nodes=[epic_ref])
    old = await make_decision(client, f"Use SOAP {word}", nodes=[epic_ref])
    wrong = await make_decision(client, f"Use fax {word}", nodes=[epic_ref, refs["story"]])
    ids["decision_kept"], ids["decision_old"], ids["decision_wrong"] = (
        kept["id"],
        old["id"],
        wrong["id"],
    )
    confirm_on_board("note", kept, project=project)
    await ok(
        client,
        "supersede_note",
        replaces=[old["id"]],
        text=f"Use SOAP 2 {word}\n\nSOAP, once more",
        why="We learned more",
        decided_by="Marie",
        keep_links=True,
    )
    await ok(client, "withdraw_note", note_ids=[wrong["id"]], reason="It was a joke")
    story_one = await make_decision(
        client, f"Keep orders small {word}", nodes=[refs["story"]], rule_ids=[placed["id"]]
    )
    ids["decision_story"] = story_one["id"]

    sure = await make_fact(client, f"Orders are small {word}: {WIDE}", nodes=[epic_ref])
    stale = await make_fact(client, f"Orders are huge {word}", nodes=[epic_ref])
    mistaken = await make_fact(client, f"Orders are free {word}", nodes=[epic_ref])
    ids["fact_sure"], ids["fact_stale"], ids["fact_mistaken"] = (
        sure["id"],
        stale["id"],
        mistaken["id"],
    )
    confirm_on_board("note", sure, project=project)
    await ok(
        client,
        "supersede_note",
        replaces=[stale["id"]],
        text=f"Orders are medium {word}",
        why="A measurement",
        keep_links=True,
    )
    await ok(client, "withdraw_note", note_ids=[mistaken["id"]], reason="Not true")
    task_fact = await make_fact(client, f"The form is slow {word}", nodes=[refs["form"]])
    ids["fact_task"] = task_fact["id"]
    return refs, ids


@asynccontextmanager
async def a_world(server_url: str) -> AsyncIterator[tuple[World, Client]]:
    """A project of its own with the work above, and a client of it."""
    project = make_project(register(name=MARIE), f"Pages {unique_word()}")
    word = unique_word()
    async with connect(project.mcp_url, project.key) as client:
        refs, ids = await build(client, word, project)
        yield World(project, board_url(server_url, project), word, refs, ids), client


# -- what every test asks of a page --------------------------------------------------------------

VIEW_NAMES = {
    "board": "Board",
    "graph": "Graph",
    "roadmap": "Roadmap",
    "lists": "Lists",
    "search": "Search",
    "activity": "Activity",
    "coverage": "Coverage",
}


async def ready(page: Page) -> None:
    """A page has drawn: it has its heading, and nothing says it is still loading."""
    await expect(page.locator("#page .loading:visible")).to_have_count(0, timeout=SLOW)
    await expect(page.locator("#page h1").first).to_be_visible(timeout=SLOW)


def parse_hash(text: str) -> tuple[str, dict[str, str]]:
    """The page and choices of an address such as #/search?search=a+b (a space may be + or %20)."""
    parsed = urlparse(text.removeprefix("#"))
    return parsed.path.removeprefix("/"), {
        key: values[0] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
    }


async def hash_of(page: Page) -> tuple[str, dict[str, str]]:
    """The page and the choices of the address after the #."""
    text: str = await page.evaluate("() => location.hash")
    return parse_hash(text)


async def changed_to(page: Page, wanted: str) -> None:
    """Wait until the address says this page with these choices (it is handled a moment after a
    click, and the same words may be spelled with a plus or with %20)."""
    expected = parse_hash(wanted)
    for _ in range(SLOW // 100):
        if await hash_of(page) == expected:
            return
        await anyio.sleep(0.1)
    assert await hash_of(page) == expected


async def nothing_beside_the_page(page: Page, where: str = "") -> None:
    """No element of a detail panel, a drawer, a quick look or a selected card, nothing in the shell
    but the page, and no link or address that names a drawer or a panel."""
    found: dict[str, Any] = await page.evaluate(
        r"""() => {
          const named = [...document.querySelectorAll('*')].filter((e) =>
            !e.closest('#graph-explore, .key-tools') &&
            /(^|[-_ ])(panel|drawer|quick-look|quicklook|sheet)s?([-_ ]|$)/i.test(
              (e.id || '') + ' ' + (typeof e.className === 'string' ? e.className : '')));
          const extra = [...document.querySelectorAll(
            '#panel, #drawer, #card-title, #record-title, #sheet-scrim, .quick-look, .gv-quick, ' +
            '.card.selected, .lane.selected, .with-panel, .with-drawer')];
          const links = [...document.querySelectorAll('a[href]')]
            .map((a) => a.getAttribute('href'))
            .filter((href) => /[?&](drawer|panel)=/.test(href));
          const hash = new URLSearchParams(location.hash.split('?')[1] || '');
          return {
            elements: [...new Set([...named, ...extra])]
              .map((e) => e.tagName + '#' + e.id + '.' + e.className),
            links,
            keys: ['drawer', 'panel'].filter((key) => hash.has(key)),
            layout: [...document.querySelectorAll('#layout > *')].map((e) => e.id),
            open: [...document.querySelectorAll('dialog[open]')].length,
          };
        }"""
    )
    assert found["elements"] == [], (where, found)
    assert found["links"] == [] and found["keys"] == [], (where, found)
    assert found["layout"] == ["page"], (where, found)
    await no_panel_or_drawer(page)


def titled(world: World, ref: str, title: str) -> str:
    """A piece of work as the path shows it: its code, then its name (`title`, then the world's
    unique word): 'S-1 Place an order zq'."""
    return f"{code_of(ref)} {title} {world.word}"


async def on_record_page(
    w: Watched,
    world: World,
    ref: str,
    view: str,
    trail: list[str],
    came_from: str | None = None,
) -> None:
    """The page of a record has opened, in the view the person came from: the address says which,
    the path starts with that view and goes down to the record, the sidebar keeps the view marked
    (Search and the coverage report have no entry there, so nothing is), and nothing is beside the
    page."""
    page = w.page
    await item_loaded(page, ref)
    await expect(page.locator("#page .loading:visible")).to_have_count(0, timeout=SLOW)
    name, choices = await hash_of(page)
    assert (name, choices) == ("item", {"item": ref, "from": view}), (name, choices)
    path = await crumbs(page)
    assert path[0] == VIEW_NAMES[view], path
    assert path[1:-1] == trail[:-1] and path[-1] == trail[-1], (path, trail)
    # The page's own step is its code and its name ("AC-3 A rider...", "N-7 headline"), which is
    # also its tooltip, whole.
    assert await page.locator("#crumb-page").get_attribute("title") == trail[-1], (path, trail)
    kept = await view_crumb(page).get_attribute("href")
    assert kept is not None and kept.split("?")[0] == f"#/{view}", kept
    # The back arrow at the left of the bar goes one step back, to the page the person was on, as
    # they left it: the view the record was reached from, or the record they came from.
    back = page.locator("#back-to-view")
    await expect(back).to_be_visible()
    arrow = await back.get_attribute("href")
    wanted = came_from or kept
    assert arrow is not None and parse_hash(arrow) == parse_hash(wanted), (arrow, wanted)
    marks = {
        "board": ["board"],
        "graph": ["graph"],
        "roadmap": ["roadmap"],
        "lists": ["lists"],
        "activity": ["activity"],
    }
    assert await sidebar_current(page) == marks.get(view, []), view
    # Every link to a record that the page makes keeps the view the person is in.
    foreign: list[str] = await page.evaluate(
        r"""(view) => [...document.querySelectorAll(
              '#page a[href*="item="], #sidebar a[href*="item="]')]
          .map((a) => a.getAttribute('href')).filter((href) => !href.endsWith('&from=' + view))""",
        view,
    )
    assert foreign == [], foreign
    await nothing_beside_the_page(page, f"{ref} from {view}")
    assert w.problems == [], w.problems


async def way_back(w: Watched, address: str) -> None:
    """The back arrow at the left of the bar returns to the view as it was left, with its filter and
    choices."""
    await w.page.locator("#back-to-view").click()
    await changed_to(w.page, address)
    await ready(w.page)


# -- 1. no panel and no drawer anywhere ------------------------------------------------------


async def test_no_page_has_a_panel_or_a_drawer_and_old_addresses_open_the_page_without_them(
    server_url: str,
) -> None:
    async with a_world(server_url) as (world, _):
        word = world.word
        every_page = [
            "#/board",
            "#/graph",
            "#/graph?as=list",
            "#/graph?view=context&as=list",
            "#/roadmap",
            f"#/search?search={word}",
            f"#/search?search={word}&dups=1",
            "#/activity",
            "#/coverage",
            *(f"#/item?item={ref}" for ref in (world["epic"], world["story"], world["form"])),
            f"#/item?item={world.record('rule_placed')}",
            f"#/item?item={world.record('decision_kept')}",
            f"#/item?item={world.record('fact_sure')}",
            "#/lists?view=notes",
            "#/lists?view=requirements",
            "#/graph?tab=around",
            "#/graph?tab=order",
            "#/users",
            "#/tools",
            "#/status",
        ]
        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page
            for address in every_page:
                await w.open(address)
                await ready(page)
                await nothing_beside_the_page(page, address)
            # A page of a record is the page and nothing beside it, whatever its kind.
            assert await page.locator("#layout > *").count() == 1

            # The old side panel's and drawer's addresses mean nothing: the page opens as it would
            # without them, they are taken out of the address, and nothing opens beside it.
            story, epic = world["story"], world["epic"]
            old_addresses = [
                (f"#/board?sel={story}", "board", {}),
                (f"#/board?sel={story}&drawer=search&q={word}", "board", {"q": word}),
                (f"#/roadmap?sel={story}&drawer=activity", "roadmap", {}),
                (
                    f"#/item?item={epic}&sel={story}&drawer=activity&panel=coverage",
                    "item",
                    {"item": epic},
                ),
                ("#/coverage?panel=coverage&drawer=search", "coverage", {}),
                (f"#/search?search={word}&drawer=search&sel={story}", "search", {"search": word}),
                ("#/activity?drawer=activity&panel=coverage", "activity", {}),
                ("#/status?sel=story:1&drawer=search", "status", {}),
                # On the Graph and in Lists `sel` is still the chosen record; the rest is dropped.
                (f"#/graph?sel={story}&drawer=search&panel=coverage", "graph", {}),
                (
                    f"#/lists?sel={story}&drawer=search&panel=coverage",
                    "lists",
                    {"sel": story},
                ),
            ]
            for number, (address, name, kept) in enumerate(old_addresses):
                # A real load of the old address (the query makes it a new document).
                await page.goto(f"{world.board}?load={number}{address}")
                await page.wait_for_selector("#app[data-shell=project]", timeout=3 * SLOW)
                await ready(page)
                assert await hash_of(page) == (name, kept), (address, await hash_of(page))
                await nothing_beside_the_page(page, address)
            assert w.problems == [], w.problems


# -- 2. a click on a record, from every place, lands on its page, in the view it came from -----


async def hash_now(page: Page) -> str:
    text: str = await page.evaluate("() => location.hash")
    return text


async def test_from_the_board_the_graph_and_the_roadmap_a_click_lands_on_the_records_page(
    server_url: str,
) -> None:
    async with a_world(server_url) as (world, client):
        word = world.word
        epic, story, form = world["epic"], world["story"], world["form"]
        trail_epic = [titled(world, epic, "Ordering")]
        trail_story = [*trail_epic, titled(world, story, "Place an order")]
        trail_form = [*trail_story, titled(world, form, "Build the form")]
        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page

            # The Board, with a filter: a lane's title, a card's title and a click on the card
            # itself each open the page, and the way back is the Board as it was left.
            await w.open("#/board?show=both")
            await apply_filter(page, word)
            await page.wait_for_function("() => location.hash.includes('q=')", timeout=SLOW)
            board = await hash_now(page)
            assert await page.locator("#filter").input_value() == word
            await page.locator(f"a.lane-title[data-focus-key='{epic}']").click()
            await on_record_page(w, world, epic, "board", trail_epic)
            await way_back(w, board)
            assert await page.locator("#filter").input_value() == word
            await page.locator(f"article.card[data-ref='{story}'] h3.card-title a").click()
            await on_record_page(w, world, story, "board", trail_story)
            await way_back(w, board)
            await page.locator(f"article.card[data-ref='{form}']").click(position={"x": 6, "y": 6})
            await on_record_page(w, world, form, "board", trail_form)
            await way_back(w, board)
            await expect(page.locator("article.card")).to_have_count(
                len(await all_nodes(client, kind="story"))
                + len(await all_nodes(client, kind="task")),
                timeout=SLOW,
            )

            # The Graph's Around: the chosen item's own button opens its page.
            await w.open(f"#/graph?tab=around&focus={story}")
            await expect(page.locator(f".graph-around[data-focus='{story}']")).to_be_visible(
                timeout=SLOW
            )
            graph = await hash_now(page)
            await page.locator("#graph-hub-open").click()
            await on_record_page(w, world, story, "graph", trail_story)
            await way_back(w, graph)
            await expect(page.locator(f".graph-around[data-focus='{story}']")).to_be_visible(
                timeout=SLOW
            )

            # The work list of Lists: a title opens the page, and so does a click on the row.
            await w.go("#/lists?view=work")
            await expect(page.locator("#work-tree")).to_be_visible(timeout=SLOW)
            work = await hash_now(page)
            row = page.locator(f"#work-tree li[data-key='{epic}'] > .gv-row")
            await row.locator("a.gv-title").click()
            await on_record_page(w, world, epic, "lists", trail_epic)
            await way_back(w, work)
            task_row = page.locator(f"#work-tree li[data-key='{form}'] > .gv-row")
            for above in (epic, story):
                branch = page.locator(f"#work-tree li[data-key='{above}']")
                if await branch.get_attribute("aria-expanded") == "false":
                    await branch.locator("> .gv-row > .gv-twisty").click()
            await expect(task_row).to_be_visible(timeout=SLOW)
            await task_row.click(position={"x": 4, "y": 4})
            await on_record_page(w, world, form, "lists", trail_form)
            await way_back(w, work)

            # The acceptance criteria and notes of Lists: each opens its page.
            rule, decision, fact = (
                world.record("rule_placed"),
                world.record("decision_kept"),
                world.record("fact_sure"),
            )
            await w.go("#/lists?view=requirements")
            await expect(page.locator("#requirements-summary")).to_be_visible(timeout=SLOW)
            requirements = await hash_now(page)
            rule_row = page.locator(f"#lists-list .gv-record[data-key='{rule}']")
            await rule_row.locator("button.gv-open").click()
            await on_record_page(
                w,
                world,
                rule,
                "lists",
                [*trail_story, await item_name(client, rule)],
            )
            await way_back(w, requirements)
            await w.go("#/lists?view=notes")
            await expect(page.locator("#ctx-notes-list tr.record-line").first).to_be_visible(
                timeout=SLOW
            )
            notes = await hash_now(page)
            await page.locator(f"#ctx-notes-list tr[data-ref='{decision}'] a.record-title").click()
            await on_record_page(w, world, decision, "lists", [await item_name(client, decision)])
            await way_back(w, notes)
            await page.locator(f"#ctx-notes-list tr[data-ref='{fact}'] a.record-title").click()
            await on_record_page(w, world, fact, "lists", [await item_name(client, fact)])
            await way_back(w, notes)

            # The Roadmap: a bar opens the page of what it draws.
            await w.go("#/roadmap")
            await expect(page.locator(f".rm-bar[data-ref='{form}']")).to_be_visible(timeout=SLOW)
            roadmap = await hash_now(page)
            await page.locator(f".rm-bar[data-ref='{form}']").click()
            await on_record_page(w, world, form, "roadmap", trail_form)
            await way_back(w, roadmap)
            await page.locator(f".rm-bar[data-ref='{story}']").click()
            await on_record_page(w, world, story, "roadmap", trail_story)
            assert w.problems == [], w.problems


async def test_from_search_activity_texts_history_the_switcher_and_coverage_a_click_lands_on_a_page(
    server_url: str,
) -> None:
    async with a_world(server_url) as (world, client):
        word = world.word
        epic, story, form, pay, card = (
            world["epic"],
            world["story"],
            world["form"],
            world["pay"],
            world["card"],
        )
        ids = world.ids
        rule, decision, fact = (
            world.record("rule_placed"),
            world.record("decision_kept"),
            world.record("fact_sure"),
        )
        trail_epic = [titled(world, epic, "Ordering")]
        trail_story = [*trail_epic, titled(world, story, "Place an order")]
        trail_form = [*trail_story, titled(world, form, "Build the form")]
        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page

            # Search: a result of each of the six kinds opens its page, and the way back is the
            # search as it was left.
            found = {
                epic: (f"Ordering {word}", trail_epic),
                story: (f"Place an order {word}", trail_story),
                form: (f"Build the form {word}", trail_form),
                rule: (
                    f"Fast {word}",
                    [*trail_story, await item_name(client, rule)],
                ),
                decision: (f"Use REST {word}", [await item_name(client, decision)]),
                fact: (f"Orders are small {word}", [await item_name(client, fact)]),
            }
            for ref, (query, trail) in found.items():
                await w.open(f"#/search?search={quote(query)}")
                await ready(page)
                result = page.locator(f"tr.result[data-ref='{ref}']")
                await expect(result).to_be_visible(timeout=SLOW)
                search = await hash_now(page)
                await result.locator("a.result-title").click()
                await on_record_page(w, world, ref, "search", trail)
                await way_back(w, search)
                await expect(result).to_be_visible(timeout=SLOW)

            # Activity: a line of the feed and a held item each open the page of the work they name.
            await w.open("#/activity")
            await ready(page)
            await expect(page.locator("#feed li.event").first).to_be_visible(timeout=SLOW)
            await page.locator(f"#feed li.event a.item-link[href*='item={form}&']").first.click()
            await on_record_page(w, world, form, "activity", trail_form)
            await way_back(w, "#/activity")
            # A held item is in the Working view, which is a choice of its own on the page.
            await open_working(page)
            working = await hash_now(page)
            await page.locator(f"#agents li.claim[data-node='{form}'] a").first.click()
            await on_record_page(w, world, form, "activity", trail_form)
            await way_back(w, working)

            # A reference in a text and a line of a history, from a page reached from Lists.
            await w.open("#/lists?view=work")
            await expect(page.locator("#work-tree")).to_be_visible(timeout=SLOW)
            await page.locator(f"#work-tree li[data-key='{epic}'] > .gv-row a.gv-title").click()
            await on_record_page(w, world, epic, "lists", trail_epic)
            await page.locator("#item-details .body-text a.reference").click()
            await on_record_page(
                w, world, story, "lists", trail_story, came_from=item_address(epic, "lists")
            )
            await page.locator(
                "[data-part=history] li[data-action=note_linked] a[href*='item=note:']"
            ).first.click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-kind", "note", timeout=SLOW
            )
            name, choices = await hash_of(page)
            assert name == "item" and choices["from"] == "lists", (name, choices)
            assert (await crumbs(page))[0] == "Lists"
            # One step back from the note is the story it was reached from, not the list.
            await way_back(w, item_address(story, "lists"))

            # The quick switcher, on the Roadmap: its result opens the page, in the Roadmap.
            await w.go("#/roadmap")
            await expect(page.locator(f".rm-bar[data-ref='{form}']")).to_be_visible(timeout=SLOW)
            await page.keyboard.press("Control+k")
            await page.locator("#switcher-input").fill(f"Take the card {word}")
            await expect(page.locator(f"#switcher [role=option][data-ref='{card}']")).to_be_visible(
                timeout=SLOW
            )
            await page.keyboard.press("Enter")
            await on_record_page(
                w,
                world,
                card,
                "roadmap",
                [
                    titled(world, epic, "Ordering"),
                    titled(world, pay, "Pay for it"),
                    titled(world, card, "Take the card"),
                ],
            )

            # The coverage report: an acceptance criterion placed nowhere and a story with none.
            await w.open("#/coverage")
            await ready(page)
            loose = ids["rule_loose"]
            await expect(page.locator(f"#unplaced-rules tr[data-rule='{loose}']")).to_be_visible(
                timeout=SLOW
            )
            await page.locator(f"#unplaced-rules tr[data-rule='{loose}'] a.record-title").click()
            await on_record_page(
                w, world, f"rule:{loose}", "coverage", [await item_name(client, f"rule:{loose}")]
            )
            await way_back(w, "#/coverage")
            await page.locator(
                f"#stories-without-rules tr[data-story='{pay}'] a.record-title"
            ).click()
            await on_record_page(
                w, world, pay, "coverage", [*trail_epic, titled(world, pay, "Pay for it")]
            )
            assert w.problems == [], w.problems


async def refs_in(page: Page, selector: str, attribute: str = "data-ref") -> list[str]:
    values: list[str] = await page.locator(selector).evaluate_all(
        "(els, name) => els.map((e) => e.getAttribute(name))", attribute
    )
    return values


async def recall_rows(page: Page, section: str) -> list[str]:
    """The references of the rows of one section of what concerns the item, as the page draws."""
    return sorted(
        await refs_in(page, f".recall-section[data-section='{section}'] tr.record-line[data-ref]")
    )


def recalled(recall: dict[str, Any], section: str) -> list[str]:
    return sorted(f"note:{entry['note']['id']}" for entry in recall[section]["items"])


async def concern_rows(page: Page, client: Client, **target: Any) -> None:
    """The tables of what concerns an item are exactly what `recall` says, section by section, and
    each note's "Applies to" cell lists what the note concerns."""
    recall = await ok(client, "recall", **target)
    for section in ("notes", "project_wide_notes", "past_notes"):
        assert await recall_rows(page, section) == recalled(recall, section), section
        counted = page.locator(f".recall-section[data-section='{section}']")
        if recall[section]["count"]:
            assert await counted.get_attribute("data-count") == str(recall[section]["count"])
    for row in await record_rows(page, "notes"):
        note = await ok(client, "get_note", note_id=row["id"])
        refs = [
            *note["nodes"],
            *(f"rule:{one}" for one in note["rule_ids"]),
            *(f"note:{one}" for one in note["note_ids"]),
        ]
        lines = [*(["The whole project"] if note["project_wide"] else [])]
        lines += [await item_name(client, ref) for ref in refs]
        cell = page.locator(
            f"#item-page table.notes-table tr.record-line[data-ref='note:{row['id']}'] "
            "td.cell-applies"
        ).first
        if not lines:
            assert " ".join((await cell.text_content() or "").split()) == "Nothing yet"
            continue
        # One item to a line, code and name, three lines at most and then "and 2 more"; the whole
        # list is the cell's tooltip.
        listing = cell.locator("div.applies-list")
        assert await listing.get_attribute("title") == "\n".join(lines), row["ref"]
        shown = [
            " ".join(text.split())
            for text in await listing.locator(".applies-name").all_text_contents()
        ]
        assert shown == lines[:3], row["ref"]
        more = listing.locator(".applies-more")
        if len(lines) > 3:
            assert " ".join((await more.text_content() or "").split()) == (
                f"and {len(lines) - 3} more"
            )
        else:
            assert await more.count() == 0


def words(status: str) -> str:
    return status.replace("_", " ").capitalize()


def note_chip(page: Page) -> Locator:
    """The chip in a note page's strip that says where the note stands (and, for a person, the
    menu that changes it)."""
    return page.locator("#item-page .state-strip dd.status .chip.note-status")


async def test_each_record_page_shows_the_same_fields_as_the_tools(server_url: str) -> None:
    async with a_world(server_url) as (world, client):
        ids = world.ids
        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page

            # An epic, a story, a held task and a task that waits: heading, line under it, state,
            # dates, what is under it, what concerns it.
            for name in ("epic", "story", "form", "api", "empty"):
                ref = world[name]
                node = await ok(client, "get_node", node=ref)
                await open_item(w, ref)
                text = await record_text(page)
                assert await page.locator("#item-title").inner_text() == (
                    f"{code_of(ref)} {node['title']}"
                ), name
                assert await page.locator("#item-page").get_attribute("data-kind") == node["kind"]
                assert words(node["status"]) in text and node["priority"].capitalize() in text
                if node["claimed_by"]:
                    assert node["claimed_by"] in text
                for date in (node["start_on"], node["target_on"]):
                    if date:
                        assert date in text, (name, date)
                if node["progress_total"]:
                    assert f"{node['progress_done']} of {node['progress_total']}" in text
                if node["parent"]:
                    parent_link = page.locator("nav.crumbs li.crumb-item a").last
                    assert code_of(node["parent"]) in await parent_link.inner_text()
                    assert await parent_link.get_attribute("href") == (
                        f"#/item?item={node['parent']}&from=board"
                    )
                if node["body"]:
                    body = await page.locator("#item-details .body-text").inner_text()
                    assert (
                        re.sub(r"\[\[[a-z]+:\d+\]\]", "", node["body"].replace("*", "")).split()[0]
                        in body
                    )
                if node["kind"] != "task":
                    below = await all_nodes(client, parent=ref)
                    assert await refs_in(page, "#item-table tbody tr") == [n["ref"] for n in below]
                # What it is linked to, in both directions, is the page's Links section, whatever
                # the kind: the other end of every link the server lists.
                links = (await ok(client, "list_links", node=ref))["items"]
                assert sorted(await refs_in(page, "#links-table tbody tr")) == sorted(
                    link["target"] if link["source"] == ref else link["source"] for link in links
                )
                if node["kind"] == "story":
                    rules = (await ok(client, "list_rules", node=ref))["items"]
                    assert sorted(
                        await refs_in(page, "table.requirements-table tr.record-line")
                    ) == sorted(f"rule:{rule['id']}" for rule in rules)
                await concern_rows(page, client, node=ref)

            # An acceptance criterion: its words, its state, the story it is on and its concerns.
            rule_id = ids["rule_placed"]
            rule = await ok(client, "get_rule", rule_id=rule_id)
            await open_item(w, f"rule:{rule_id}")
            text = await record_text(page)
            assert await page.locator("#item-title").inner_text() == (
                f"AC-{rule_id} {rule['title']}"
            )
            assert words(rule["status"]) in text
            # The story it is on and the epic above it are the path above the page, and its strip
            # holds its status alone.
            holder = await ok(client, "get_node", node=rule["node"])
            assert await crumbs(page) == [
                "Board",
                await item_name(client, holder["parent"]),
                await item_name(client, rule["node"]),
                await item_name(client, f"rule:{rule_id}"),
            ]
            assert await strip_labels(page) == ["Status"]
            await concern_rows(page, client, rule_id=rule_id)
            loose = await ok(client, "get_rule", rule_id=ids["rule_loose"])
            await open_item(w, f"rule:{loose['id']}")
            assert loose["node"] is None
            assert await page.locator("#item-page [data-part=lifecycle]").count() == 0
            # One placed nowhere says so in its strip, and its path is its own code and name.
            assert await strip_labels(page) == [
                "Status",
                "On",
            ]
            await expect(page.locator("#item-page .state-strip dd.in-story")).to_have_text(
                "Not placed yet"
            )
            assert await crumbs(page) == [
                "Board",
                await item_name(client, f"rule:{loose['id']}"),
            ]

            # Notes: unconfirmed, confirmed, replaced, withdrawn, project-wide, decided or not.
            for name in (
                "decision_kept",
                "decision_old",
                "decision_wrong",
                "decision_wide0",
                "fact_sure",
                "fact_stale",
                "fact_mistaken",
                "fact_task",
            ):
                note = await ok(client, "get_note", note_id=ids[name])
                await open_item(w, f"note:{note['id']}")
                text = await record_text(page)
                heading = await page.locator("#item-title").inner_text()
                assert heading.startswith(f"N-{note['id']} ")
                assert note["headline"].rstrip("…")[:40] in heading, (name, heading)
                for field in ("why", "decided_by"):
                    if note[field]:
                        assert note[field] in text, (name, field)
                if note["decided_by"]:
                    await expect(page.locator("#item-page .state-strip dd.decided")).to_have_text(
                        f"{note['decided_by']} on {note['decided_on']}"
                    )
                # The strip holds only what the note has: its status, its scope when it is
                # project-wide and who decided it when somebody did.
                assert await strip_labels(page) == [
                    "Status",
                    *(["Scope"] if note["project_wide"] else []),
                    *(["Decided by"] if note["decided_by"] else []),
                ], name
                detail = page.locator("article.record-detail")
                assert await detail.get_attribute("data-status") == note["status"], name
                assert (
                    await detail.get_attribute("data-project-wide")
                    == str(note["project_wide"]).lower()
                )
                # One chip says where it stands, in the stored word and the word a person reads.
                chip = note_chip(page)
                assert await chip.get_attribute("data-status") == note["status"]
                assert (await chip.inner_text()).strip() == NOTE_STATUS_WORDS[note["status"]]
                # The Confirmation card is there only for a note somebody confirmed.
                assert await part(page, "confirmation").count() == int(bool(note["confirmed_by"]))
                if note["status"] == "confirmed":
                    assert f"Confirmed by {note['confirmed_by']}" in text
                concerns = {f"rule:{one}" for one in note["rule_ids"]} | set(note["nodes"])
                concerns |= {f"note:{one}" for one in note["note_ids"]}
                if note["project_wide"]:
                    concerns.add("project")
                assert set(await refs_in(page, "[data-part=concerns] tr.record-line")) == concerns
                replaces = {
                    f"note:{one}"
                    for one in [*note["supersedes_ids"], note["superseded_by_id"]]
                    if one
                }
                assert set(await refs_in(page, "[data-part=replaces] tr.record-line")) == replaces
                if note["status"] in ("superseded", "withdrawn"):
                    assert note["status_reason"] is None or note["status_reason"] in text
                    await expect(page.locator("button.supersede, button.withdraw")).to_have_count(0)
                    await expect(chip).not_to_have_attribute("aria-haspopup", "menu")
            # The replacement of a note is a page too, one click from the old one.
            old = await ok(client, "get_note", note_id=ids["decision_old"])
            await open_item(w, f"note:{old['id']}")
            replaced_by = f"note:{old['superseded_by_id']}"
            await page.locator(
                f"[data-part=replaces] tr.record-line[data-ref='{replaced_by}'] a.record-title"
            ).click()
            await item_loaded(page, replaced_by)
            assert await refs_in(page, "[data-part=replaces] tr.record-line") == [
                f"note:{old['id']}"
            ]
            assert w.problems == [], w.problems


# -- 3. moving through the content ----------------------------------------------------------


async def test_previous_and_next_move_through_exactly_the_siblings_the_server_lists(
    server_url: str,
) -> None:
    async with a_world(server_url) as (world, client):
        groups = {
            "epic": [one["ref"] for one in await all_nodes(client, kind="epic")],
            "story": [one["ref"] for one in await all_nodes(client, parent=world["epic"])],
            "task": [one["ref"] for one in await all_nodes(client, parent=world["story"])],
        }
        assert [len(group) for group in groups.values()] == [3, 3, 3]
        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page
            for noun, group in groups.items():
                assert await siblings(client, group[0]) == group
                await open_item(w, group[0], "graph")
                last = len(group) - 1
                for at, ref in enumerate(group):
                    state = await pager(page)
                    assert (state["at"], state["of"]) == (at + 1, len(group)), (noun, at)
                    assert state["previous"]["disabled"] is (at == 0), (noun, at)
                    assert state["next"]["disabled"] is (at == last), (noun, at)
                    for side, near in (("previous", at - 1), ("next", at + 1)):
                        label = state[side]["label"]
                        if 0 <= near <= last:
                            assert state[side]["code"] == code_of(group[near])
                            title = (await ok(client, "get_node", node=group[near]))["title"]
                            assert (
                                label
                                == f"{side.capitalize()} {noun}: {code_of(group[near])} {title}"
                            )
                        else:
                            assert state[side]["code"] is None and label == f"No {side} {noun}"
                    # The page stays in the view it was reached from while it moves.
                    assert (await hash_of(page))[1] == {"item": ref, "from": "graph"}
                    if at < last:
                        assert await pager_go(page, "next") == group[at + 1]
                await expect(page.locator("#item-next")).to_be_disabled()
                for ref in reversed(group[:-1]):
                    assert await pager_go(page, "previous") == ref
                await expect(page.locator("#item-prev")).to_be_disabled()
                # The keys [ and ] do the same as the buttons.
                await page.keyboard.press("]")
                await item_loaded(page, group[1])
                await page.keyboard.press("[")
                await item_loaded(page, group[0])
                await expect(page.locator("#item-prev")).to_be_disabled()
            # An item that is not work has no pager.
            await open_item(w, world.record("decision_kept"))
            await expect(page.locator("#item-pager")).to_have_count(0)
            assert w.problems == [], w.problems


async def test_the_back_arrow_goes_exactly_one_step_back_from_wherever_the_person_came(
    server_url: str,
) -> None:
    """The arrow of a record's page goes to the page the person was on just before, as they left
    it: another record, a filtered view, a search, a settings page; it survives a reload, and with
    no step before it (the address was loaded fresh) it goes to the view the record was reached
    from."""
    async with a_world(server_url) as (world, client):
        form, api, story = world["form"], world["api"], world["story"]
        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page
            arrow = page.locator("#back-to-view")

            async def step_back(address: str) -> None:
                await arrow.click()
                await changed_to(page, address)

            # A record, then another, then another: each arrow click is one record back, and the
            # last one lands on the view as it was left, with its filter.
            await w.open("#/board?q=status:new")
            await ready(page)
            await open_item(w, form, "board")
            await expect(arrow).to_have_attribute("aria-label", "Back to Board")
            await w.go(item_address(api, "board"))
            await item_loaded(page, api)
            await expect(arrow).to_have_attribute(
                "aria-label", f"Back to {await item_name(client, form)}"
            )
            await w.go(item_address(story, "board"))
            await item_loaded(page, story)
            await step_back(item_address(api, "board"))
            await step_back(item_address(form, "board"))
            await step_back("#/board?q=status:new")

            # From a search and from a settings page, then after a reload of the record's page.
            await w.go("#/search?search=ordering")
            await w.go(item_address(story, "search"))
            await item_loaded(page, story)
            await expect(arrow).to_have_attribute("aria-label", "Back to Search")
            await step_back("#/search?search=ordering")
            await w.go("#/users")
            await w.go(item_address(form, "board"))
            await item_loaded(page, form)
            await expect(arrow).to_have_attribute("aria-label", "Back to Users")
            await page.reload()
            await item_loaded(page, form)
            await step_back("#/users")

            # Loaded fresh, a record has no step before it: the arrow goes to its view.
            await w.open(f"?again={unique_word()}{item_address(form, 'graph')}")
            await item_loaded(page, form)
            await expect(arrow).to_have_attribute("data-step", "view")
            await step_back("#/graph")
            assert w.problems == [], w.problems


async def expected_tree(client: Client) -> list[tuple[str, int]]:
    """The tree the sidebar draws with everything open, from the server's lists: every epic, under
    each its stories, under each its tasks, each by number: (reference, level)."""
    nodes = await all_nodes(client)
    below: dict[str | None, list[str]] = {}
    for node in nodes:
        below.setdefault(node["parent"], []).append(node["ref"])
    found: list[tuple[str, int]] = []

    def walk(ref: str, level: int) -> None:
        found.append((ref, level))
        for child in sorted(below.get(ref, []), key=number_of):
            walk(child, level + 1)

    for epic in sorted(below[None], key=number_of):
        walk(epic, 1)
    return found


async def test_from_an_epic_to_a_task_and_back_and_to_another_epic_by_links_and_the_tree(
    server_url: str,
) -> None:
    async with a_world(server_url) as (world, client):
        epic, story, form = world["epic"], world["story"], world["form"]
        billing, invoice, send = world["billing"], world["invoice"], world["send"]
        trail_epic = [titled(world, epic, "Ordering")]
        trail_story = [*trail_epic, titled(world, story, "Place an order")]
        trail_form = [*trail_story, titled(world, form, "Build the form")]
        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page
            await open_item(w, epic, "board")

            # Down, by the rows of the tables: epic, story, task. Each arrow is one step back.
            await item_row(page, story).locator(".cell-item a").click()
            await on_record_page(
                w, world, story, "board", trail_story, came_from=item_address(epic, "board")
            )
            await item_row(page, form).locator(".cell-item a").click()
            await on_record_page(
                w, world, form, "board", trail_form, came_from=item_address(story, "board")
            )
            # Up, by the links in the path above the page: the story it is part of, then the epic
            # it is in.
            await page.locator(
                f"nav.crumbs li.crumb-item a[href='{item_address(story, 'board')}']"
            ).click()
            await on_record_page(
                w, world, story, "board", trail_story, came_from=item_address(form, "board")
            )
            await page.locator("nav.crumbs li.crumb-item a").click()
            await on_record_page(
                w, world, epic, "board", trail_epic, came_from=item_address(story, "board")
            )

            # To another epic, and down to its task, by the sidebar's tree alone.
            await tree_open(page, billing)
            await on_record_page(
                w,
                world,
                billing,
                "board",
                [titled(world, billing, "Billing")],
                came_from=item_address(epic, "board"),
            )
            await tree_expand(page, billing)
            await tree_expand(page, invoice)
            await tree_open(page, send)
            await on_record_page(
                w,
                world,
                send,
                "board",
                [
                    titled(world, billing, "Billing"),
                    titled(world, invoice, "Invoice"),
                    titled(world, send, "Send the invoice"),
                ],
                came_from=item_address(billing, "board"),
            )
            rows = await tree_rows(page)
            assert [row["ref"] for row in rows if row["current"]] == [send]
            for above in (billing, invoice):
                assert await tree_item(page, above).get_attribute("aria-expanded") == "true"

            # With everything open the tree is exactly the server's work, by number.
            for _ in range(20):
                shut = [row["ref"] for row in await tree_rows(page) if row["expanded"] is False]
                if not shut:
                    break
                await tree_expand(page, shut[0])
            rows = await tree_rows(page)
            want = await expected_tree(client)
            assert [(row["ref"], row["level"]) for row in rows] == want
            nodes = {one["ref"]: one for one in await all_nodes(client)}
            for row in rows:
                assert row["code"] == code_of(row["ref"])
                assert row["title"] == nodes[row["ref"]]["title"]
                size = sum(
                    1
                    for ref, level in want
                    if level == row["level"] and nodes[ref]["parent"] == nodes[row["ref"]]["parent"]
                )
                assert row["size"] == size

            # By keyboard: the arrows move and open and close branches, Enter opens the page and
            # puts the focus on its title.
            await tree_expand(page, story, False)
            await tree_expand(page, epic, False)
            await tree_item(page, epic).focus()
            await page.keyboard.press("ArrowRight")
            await expect(tree_item(page, epic)).to_have_attribute("aria-expanded", "true")
            await page.keyboard.press("ArrowDown")
            await expect(tree_item(page, story)).to_be_focused()
            await page.keyboard.press("ArrowRight")
            await expect(tree_item(page, story)).to_have_attribute("aria-expanded", "true")
            await page.keyboard.press("ArrowDown")
            await expect(tree_item(page, form)).to_be_focused()
            await page.keyboard.press("ArrowLeft")
            await expect(tree_item(page, story)).to_be_focused()
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            await on_record_page(
                w, world, form, "board", trail_form, came_from=item_address(send, "board")
            )
            await expect(page.locator("#item-title")).to_be_focused(timeout=SLOW)
            assert w.problems == [], w.problems


# -- 4. what concerns an item is a table across the page; a record's page works -------------

MEASURE_TABLES = r"""() => {
  const box = (e) => e.getBoundingClientRect();
  const shown = (e) => e.getClientRects().length > 0;
  const content = box(document.getElementById('item-page'));
  const tables = [...document.querySelectorAll('#item-page table.record-table')].filter(shown);
  return {
    content: { left: content.left, right: content.right, width: content.width },
    page: { scroll: document.documentElement.scrollWidth,
            client: document.documentElement.clientWidth },
    tables: tables.map((table) => {
      const rows = [...table.querySelectorAll('tbody tr')];
      const heads = [...table.querySelectorAll('thead th')];
      const region = table.parentElement;
      return {
        name: table.className,
        left: box(table).left, right: box(table).right, width: box(table).width,
        region: { scroll: region.scrollWidth, client: region.clientWidth },
        rows: rows.map((row) => box(row).height),
        columns: heads.map((head, at) => ({
          label: head.textContent.trim(),
          width: box(head).width,
          filled: rows.some((row) => {
            const cell = row.children[at];
            return Boolean(cell) && (cell.textContent.trim() !== '' ||
              Boolean(cell.querySelector('svg, a, button, select, input')));
          }),
        })),
      };
    }),
  };
}"""


async def check_tables_span_the_page(page: Page, where: str, rows_wanted: int) -> None:
    """Every table of what concerns the item that is on show spans the content width (left edge to
    right edge of the page's content), keeps its rows to three lines (none is taller than three
    lines of the shortest row of any table of the page, button included), has no column without
    anything in it, and does not scroll sideways inside its own box or the page."""
    found: dict[str, Any] = await page.evaluate(MEASURE_TABLES)
    content, tables = found["content"], found["tables"]
    assert tables, where
    assert found["page"]["scroll"] <= found["page"]["client"], (where, found["page"])
    line = min(height for table in tables for height in table["rows"])
    seen = 0
    for table in tables:
        name = f"{where}: {table['name']}"
        assert abs(table["left"] - content["left"]) <= 4, (name, table, content)
        assert abs(table["right"] - content["right"]) <= 4, (name, table, content)
        assert table["width"] >= content["width"] - 8, (name, table["width"], content["width"])
        assert table["region"]["scroll"] <= table["region"]["client"] + 1, (name, table["region"])
        for height in table["rows"]:
            assert height <= line * 3.2, (name, table["rows"], line)
        for column in table["columns"]:
            assert column["filled"] and column["width"] >= 40, (name, column)
        seen += len(table["rows"])
    assert seen >= rows_wanted, (where, seen, rows_wanted)


async def test_an_epic_with_nine_long_notes_has_tables_across_the_page_and_three_lines_to_a_row(
    server_url: str,
) -> None:
    async with a_world(server_url) as (world, client):
        epic, empty = world["epic"], world["empty"]
        wide = [world.record(f"decision_wide{at}") for at in range(9)]
        recall = await ok(client, "recall", node=epic)
        assert recall["project_wide_notes"]["count"] == 9
        long_ones = {
            f"note:{item['note']['id']}": item["note"]
            for item in recall["project_wide_notes"]["items"]
        }
        assert sorted(long_ones) == sorted(wide)
        assert all(len(one["text"]) > 150 for one in long_ones.values())
        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page
            for width in (1280, 1600):
                await page.set_viewport_size({"width": width, "height": 1000})

                # An epic that has linked notes: the project-wide ones that merely apply are in a
                # section that starts closed, and the ended ones in another.
                await open_item(w, epic)
                assert await disclosure(page, "project_wide_notes") == {
                    "open": False,
                    "count": 9,
                }
                assert await disclosure(page, "past_notes") == {"open": True, "count": 4}
                await page.locator(
                    "details.recall-disclosure[data-disclosure='project_wide_notes'] > summary"
                ).click()
                assert (await disclosure(page, "project_wide_notes") or {})["open"] is True
                await check_tables_span_the_page(page, f"epic at {width}", rows_wanted=9 + 4 + 4)
                rows = await record_rows(page, "notes", "project_wide_notes")
                assert [row["ref"] for row in rows] == sorted(wide, key=number_of_record)
                assert all(row["visible"] for row in rows)
                # A long text wraps to three lines, and the whole text is in the cell's title.
                titles: list[str] = await page.locator(
                    ".recall-section[data-section=project_wide_notes] td.cell-title .clamp"
                ).evaluate_all("els => els.map(e => e.title)")
                for shown, ref in zip(titles, sorted(wide, key=number_of_record), strict=True):
                    assert long_ones[ref]["text"] in shown and long_ones[ref]["why"] in shown

                # The same where nothing is linked: the project-wide section starts open.
                await open_item(w, empty)
                assert await disclosure(page, "project_wide_notes") == {
                    "open": True,
                    "count": 9,
                }
                await check_tables_span_the_page(page, f"empty epic at {width}", rows_wanted=9)

            # A row opens the record's own page, wherever on the row it is pressed.
            await open_item(w, epic)
            row = page.locator("#item-page table.notes-table tr.record-line").first
            ref = str(await row.get_attribute("data-ref"))
            await row.locator("td").last.click()
            await item_loaded(page, ref)
            assert await hash_of(page) == ("item", {"item": ref, "from": "board"})
            assert w.problems == [], w.problems


def number_of_record(ref: str) -> int:
    return int(ref.split(":")[1])


ADA = "Ada Lovelace"


def dialog(page: Page) -> Locator:
    return page.locator("dialog.modal")


def refusal_in(page: Page) -> Locator:
    """The refusal a dialog shows, under its own fields."""
    return page.locator("dialog.modal form > .problem")


def said(text: str) -> str:
    return " ".join(text.split())


async def test_a_criterion_page_is_edited_without_losing_a_draft_and_refuses_a_stale_save(
    server_url: str,
) -> None:
    async with a_world(server_url) as (world, client):
        rule_id, word = world.ids["rule_placed"], world.word
        ref = f"rule:{rule_id}"
        async with browser_page(world.board, token=world.project.key, name=ADA) as w:
            page = w.page
            await open_item(w, ref)
            await open_editor(page, "#item-page")
            title = page.locator("#edit-title")
            await title.fill(f"My draft {word}")
            # Somebody else changes it while the draft is typed: nothing typed is overwritten.
            await ok(
                client, "update_rule", actor="agent-1", rule_id=rule_id, expected_version=1,
                title=f"Agent title {word}",
            )  # fmt: skip
            changed = page.locator("#item-details .notice.changed")
            await expect(changed).to_be_visible(timeout=SLOW)
            await expect(changed).to_contain_text("tests")
            assert await title.input_value() == f"My draft {word}"
            # Saving it is refused in the words the tool uses, and what was typed is kept.
            stale = await refused(
                client, "update_rule", actor="x", rule_id=rule_id, expected_version=1, title="no"
            )
            await page.get_by_role("button", name="Save changes").click()
            problem = page.locator("#item-details .edit .problem .refusal-text")
            await expect(problem).to_be_visible(timeout=SLOW)
            plain(said(await problem.inner_text()), said(stale))
            assert await title.input_value() == f"My draft {word}"
            assert (await ok(client, "get_rule", rule_id=rule_id))["title"] == f"Agent title {word}"
            # The current version replaces the draft when the person asks for it, and a save of a
            # fresh edit goes through, at the version they now see.
            await page.locator("#item-details .load-current").first.click()
            await expect(title).to_have_value(f"Agent title {word}", timeout=SLOW)
            await title.fill(f"Saved at last {word}")
            await page.get_by_role("button", name="Save changes").click()
            await expect(page.locator("#item-title")).to_contain_text("Saved at last", timeout=SLOW)
            saved = await ok(client, "get_rule", rule_id=rule_id)
            assert (saved["title"], saved["version"]) == (f"Saved at last {word}", 3)
            assert (await crumbs(page))[-1] == await item_name(client, f"rule:{rule_id}")
            await expect(page.locator("#crumb-page")).to_have_attribute(
                "title", re.compile(f"Saved at last {word}")
            )
            assert w.problems == [], w.problems


async def test_a_note_page_confirms_replaces_withdraws_and_changes_what_it_concerns(
    server_url: str,
) -> None:
    async with a_world(server_url) as (world, client):
        word, ids = world.word, world.ids
        story, form = world["story"], world["form"]
        async with browser_page(world.board, token=world.project.key, name=ADA) as w:
            page = w.page

            # Confirming asks who reviewed it; a blank is refused in the tool's words, nothing is
            # written and what was typed stays; un-confirming needs the person and their words.
            # (Only a person can: the words are the ones the board's own call gets.)
            unsure = world.record("decision_story")
            await open_item(w, unsure)
            detail = page.locator(f"article.record-detail[data-record='{unsure}']")
            chip = note_chip(page)
            await expect(chip).to_have_attribute("data-status", "unconfirmed")
            # Nobody has confirmed it: there is no Confirmation card, only the chip.
            await expect(part(page, "confirmation")).to_have_count(0)
            asks = {"note_id": ids["decision_story"]}
            nobody = board_refusal(
                world.project, "set_confirmation", confirmation="confirmed", person="", **asks
            )
            await choose_in_menu(page, chip, "confirm")
            box = dialog(page)
            await expect(box.locator("#confirm-person")).to_have_value(MARIE)
            await box.locator("#confirm-person").fill("")
            await box.locator("#confirm-quote").fill(f"I read it {word}")
            await box.get_by_role("button", name="Confirm", exact=True).click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(said(await refusal_in(page).inner_text()), said(nobody))
            assert await box.locator("#confirm-quote").input_value() == f"I read it {word}"
            note = await ok(client, "get_note", note_id=ids["decision_story"])
            assert note["status"] == "unconfirmed"
            await box.get_by_role("button", name="Cancel").click()
            await expect(chip).to_have_attribute("data-status", "unconfirmed")
            await choose_in_menu(page, chip, "confirm")
            await box.locator("#confirm-person").fill(MARIE)
            await box.get_by_role("button", name="Confirm", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            await expect(detail).to_have_attribute("data-status", "confirmed", timeout=SLOW)
            note = await ok(client, "get_note", note_id=ids["decision_story"])
            assert (note["status"], note["confirmed_by"]) == ("confirmed", MARIE)
            assert note["recorded_at"]
            await expect(chip).to_have_text("Confirmed")
            await expect(chip).to_have_attribute("title", re.compile(f"Confirmed by {MARIE}"))
            await expect(page.locator("[data-part=confirmation]")).to_contain_text(
                f"Confirmed by {MARIE}"
            )
            no_person = board_refusal(
                world.project,
                "set_confirmation",
                confirmation="unconfirmed",
                person="",
                quote="",
                **asks,
            )
            no_words = board_refusal(
                world.project,
                "set_confirmation",
                confirmation="unconfirmed",
                person=MARIE,
                quote="",
                **asks,
            )
            await choose_in_menu(page, chip, "unconfirm")
            await box.locator("#confirm-person").fill("")
            await box.get_by_role("button", name="Un-confirm", exact=True).click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(said(await refusal_in(page).inner_text()), said(no_person))
            await box.locator("#confirm-person").fill(MARIE)
            await box.get_by_role("button", name="Un-confirm", exact=True).click()
            await expect(refusal_in(page)).to_contain_text("needs what the person said")
            plain(said(await refusal_in(page).inner_text()), said(no_words))
            assert (await ok(client, "get_note", note_id=ids["decision_story"]))[
                "status"
            ] == "confirmed"
            await box.locator("#confirm-quote").fill(f"On second thought, no {word}")
            await box.get_by_role("button", name="Un-confirm", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            await expect(detail).to_have_attribute("data-status", "unconfirmed", timeout=SLOW)

            # Replacing a note a person confirmed needs that person's word; the old one is kept,
            # ended, and the page of the new one is one click away in its table of replacements.
            kept = world.record("decision_kept")
            await open_item(w, kept)
            await page.locator("button.supersede").click()
            box = dialog(page)
            await expect(box.locator("details.said-by")).to_have_attribute("open", "")
            old_note = await ok(client, "get_note", note_id=ids["decision_kept"])
            assert await box.locator("#supersede-text").input_value() == old_note["text"]
            # Who decided the old note is offered again, in a field of its own (never a switch).
            assert await box.locator("#supersede-by").input_value() == MARIE
            await box.locator("#supersede-text").fill(f"Use GraphQL {word}\n\nGraphQL it is")
            await box.locator("#supersede-why").fill("Fewer round trips")
            attempt = {
                "replaces": [ids["decision_kept"]],
                "text": f"Use GraphQL {word}\n\nGraphQL it is",
                "why": "Fewer round trips",
                "decided_by": MARIE,
                "keep_links": True,
            }
            unwitnessed = await refused(client, "supersede_note", **attempt)
            await box.get_by_role("button", name=re.compile(r"^Replace with this note")).click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(said(await refusal_in(page).inner_text()), said(unwitnessed))
            assert (await ok(client, "get_note", note_id=ids["decision_kept"]))[
                "status"
            ] == "confirmed"
            await box.locator("#supersede-said-by").fill(MARIE)
            await box.locator("#supersede-quote").fill(f"Yes, change it {word}")
            await box.get_by_role("button", name=re.compile(r"^Replace with this note")).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            old = await ok(client, "get_note", note_id=ids["decision_kept"])
            # It ended, and keeps the confirmation it had.
            assert old["status"] == "superseded" and old["confirmed_by"] == "Marie"
            new = await ok(client, "get_note", note_id=old["superseded_by_id"])
            assert (new["supersedes_ids"], new["status"], new["decided_by"]) == (
                [old["id"]],
                "unconfirmed",
                MARIE,
            )
            await expect(page.locator("#record-ended")).to_be_visible(timeout=SLOW)
            await expect(page.locator("button.supersede, button.withdraw")).to_have_count(0)
            new_ref = f"note:{new['id']}"
            await page.locator(
                f"[data-part=replaces] tr.record-line[data-ref='{new_ref}'] a.record-title"
            ).click()
            await item_loaded(page, new_ref)
            assert [r["ref"] for r in await record_rows(page, "replaces")] == [kept]
            assert (await hash_of(page))[1] == {"item": new_ref, "from": "board"}

            # What a note concerns is changed from its page, and the table links onward.
            fact = world.record("fact_task")
            await open_item(w, fact)
            assert [r["ref"] for r in await record_rows(page, "concerns")] == [form]
            await page.locator("button.change-concerns").click()
            box = dialog(page)
            await box.locator(".picker input[type=text]").fill(code_of(story))
            await box.locator("#concerns-add").click()
            await expect(box.locator(".concerns-list")).to_contain_text(
                f"{code_of(story)} Place an order", timeout=SLOW
            )
            await box.locator("#concerns-done").click()
            await expect(box).to_have_count(0, timeout=SLOW)
            assert sorted((await ok(client, "get_note", note_id=ids["fact_task"]))["nodes"]) == (
                sorted([form, story])
            )
            await expect(page.locator("[data-part=concerns] tr.record-line")).to_have_count(
                2, timeout=SLOW
            )
            await page.locator(
                f"[data-part=concerns] tr.record-line[data-ref='{story}'] a.record-title"
            ).click()
            await item_loaded(page, story)
            assert fact in [r["ref"] for r in await record_rows(page, "notes")]

            # Withdrawing needs a reason, in the tool's words; the note stays, marked withdrawn.
            await open_item(w, fact)
            await page.locator("button.withdraw").click()
            box = dialog(page)
            no_reason = await refused(
                client, "withdraw_note", note_ids=[ids["fact_task"]], reason=""
            )
            await box.get_by_role("button", name="Withdraw", exact=True).click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(said(await refusal_in(page).inner_text()), said(no_reason))
            await box.locator("#withdraw-reason").fill(f"Not true {word}")
            await box.get_by_role("button", name="Withdraw", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            gone = await ok(client, "get_note", note_id=ids["fact_task"])
            assert (gone["status"], gone["status_reason"]) == ("withdrawn", f"Not true {word}")
            await expect(page.locator("[data-part=lifecycle] .ended-reason")).to_contain_text(
                f"Not true {word}", timeout=SLOW
            )
            await expect(page.locator("article.record-detail")).to_have_attribute(
                "data-status", "withdrawn"
            )
            assert w.problems == [], w.problems


# -- 5. Search, Possible duplicates, Activity and the coverage report say what the tools say ----

HANDOFF = (
    "I took the card form apart and found that the payment provider wants the amount in cents, "
    "not in euros. The tests for it are in the shop folder. Next: add the currency to the form, "
    "then ask the provider's sandbox for a test charge. Nothing else is blocked on this."
)


async def test_the_search_duplicates_activity_and_coverage_pages_say_what_the_tools_say(
    server_url: str,
) -> None:
    async with a_world(server_url) as (world, client):
        word, card, form = world.word, world["card"], world["form"]
        epic_a, epic_b, (first,), (second,) = await twin_epics(client, [DUPLICATES[0]])
        # An agent who has finished, with a note that is read in full; another still holds work.
        await ok(client, "claim_node", node=card, lease_seconds=86_400)
        await ok(client, "finish_work", node=card, outcome="released", note=HANDOFF)
        async with browser_page(world.board, token=world.project.key, name=ADA) as w:
            page = w.page

            # Search: the same results, in the same order, with the same counts, as the tool gives
            # for the same words (it asks for words and meaning together), and a type chosen in the
            # page's filter is the same results the tool gives of that type.
            await go(page, world.board, search_address({"search": word}))
            server = await asked(client, word)
            assert len(server["items"]) > 20
            rows = await whole_list(page, len(server["items"]))
            assert shown_triples(rows) == triples(server["items"])
            assert await facet(page, "type") == server["counts_by_type"]
            assert await facet(page, "status") == server["counts_by_status"]
            assert {kind for kind, _, _ in triples(server["items"])} == set(KINDS)
            await go(page, world.board, search_address({"search": word, "search_type": "note"}))
            notes = [t for t in triples(server["items"]) if t[0] == "note"]
            assert shown_triples(await whole_list(page, len(notes))) == notes

            # Possible duplicates: the server's pairs and scores, searching links nothing, and one
            # click on a Mark button makes exactly one link.
            both = f"{number_of(epic_a)},{number_of(epic_b)}"
            await go(page, world.board, search_address({"dups": "1", "dup_epics": both}))
            overlaps = await every_overlap(client, epics=[epic_a, epic_b])
            shown = await pairs_on(page)
            assert shown == pair_triples(overlaps) and (first, second) in {
                (a, b) for a, b, _ in shown
            }
            before = (await links_of(client, first, second), await link_count(client))
            assert before[0] == {first: [], second: []}
            row = page.locator(f"tr.pair[data-first='{first}'][data-second='{second}']")
            names = f"“{DUPLICATES[0][0]}” and “{DUPLICATES[0][1]}”"
            await row.get_by_role("button", name=f"Mark as duplicates: {names}").click()
            await expect(
                row.get_by_role("button", name=f"Marked as duplicates: {names}")
            ).to_be_disabled(timeout=SLOW)
            assert await links_of(client, first, second) == {
                first: [(first, second, "duplicates")],
                second: [(first, second, "duplicates")],
            }
            assert await link_count(client) == before[1] + 1

            # Activity: every change the server lists, newest first, a handoff note in full, and the
            # claims as the server says them, with the time each runs out.
            await go(page, world.board, "#/activity")
            changes = [event["id"] for event in reversed(await events_of(client))]
            for _ in range(100):
                older = page.locator("#show-older")
                if await older.count() == 0:
                    break
                seen = len(await feed_ids(page))
                await older.click()
                await expect(page.locator("#feed li.event")).not_to_have_count(seen, timeout=SLOW)
            assert await feed_ids(page) == changes
            note = page.locator("#feed li.event .handoff-note", has_text="payment provider")
            assert spoken(await note.first.inner_text()).endswith(spoken(HANDOFF))
            working = await ok(client, "who_is_working")
            held = {
                (work["node"], agent["actor"]): work["expires_at"]
                for agent in working["agents"]
                for work in agent["holding"]
            }
            assert (form, "tests") in held
            await open_working(page)
            rows = await claim_rows(page)
            assert {(r["node"], r["actor"]) for r in rows if r["section"] == "claims-live"} == set(
                held
            )
            countdown = page.locator(
                f"#agents li.claim[data-live='1'][data-node='{form}'] .countdown"
            )
            assert datetime.fromisoformat(
                str(await countdown.get_attribute("data-expires"))
            ) == datetime.fromisoformat(held[(form, "tests")])
            handoff = next(a for a in working["agents"] if a["actor"] == "tests")["last_handoff"]
            people = spoken(await page.locator("#agents").inner_text())
            assert "tests" in people
            assert f"Last handoff: {handoff['outcome']} on {code_of(handoff['node'])}" in people

            # The coverage report: the acceptance criteria placed nowhere and the stories with none.
            await go(page, world.board, "#/coverage")
            report = await whole_coverage_report(client)
            seen_rows = await coverage_rows(page)
            assert seen_rows["unplaced"] == [
                f"rule:{rule['id']}" for rule in report["unplaced_rules"]
            ]
            assert seen_rows["bare"] == [story["ref"] for story in report["stories_without_rules"]]
            summary = page.locator("#coverage-summary")
            assert await summary.get_attribute("data-unplaced") == str(
                report["unplaced_rule_count"]
            )
            assert await summary.get_attribute("data-stories-without") == str(
                report["story_without_rules_count"]
            )
            assert w.problems == [], w.problems


# -- 6. a node of the Graph's map opens its page; a Roadmap bar opens but still drags --------------


def dot(page: Page, key: str) -> Locator:
    return page.locator(f"svg.graph-st-svg g.graph-st-node[data-ref='{key}']")


async def map_at_rest(page: Page) -> None:
    """The whole-project map is a live layout: it is still while nothing touches it, and a click
    on a dot that is still moving is a click on where the dot was."""
    await expect(page.locator("svg.graph-st-svg")).to_have_attribute(
        "data-resting", "true", timeout=SLOW
    )


async def test_the_maps_dot_opens_its_page_and_a_roadmap_bar_opens_but_still_drags(
    server_url: str,
) -> None:
    async with a_world(server_url) as (world, client):
        epic, story, form = world["epic"], world["story"], world["form"]
        trail_epic = [titled(world, epic, "Ordering")]
        trail_story = [*trail_epic, titled(world, story, "Place an order")]
        trail_form = [*trail_story, titled(world, form, "Build the form")]
        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page
            await w.open("#/graph")
            await expect(page.locator("svg.graph-st-svg")).to_be_visible(timeout=SLOW)
            await map_at_rest(page)
            # Nothing is beside the map and no node is chosen by pointing at it: a click opens the
            # page of the node, in the Graph, and the way back is the Graph as it was left.
            await expect(page.locator(".graph-x-side")).to_have_count(0)
            assert await page.get_by_role("button", name="Open page").count() == 0
            graph = await page.evaluate("() => location.hash")
            await dot(page, story).click()
            await on_record_page(w, world, story, "graph", trail_story)
            await way_back(w, graph)
            await expect(dot(page, story)).to_be_visible(timeout=SLOW)
            await map_at_rest(page)
            assert await hash_of(page) == ("graph", {})

            # By keyboard: Enter on a node opens its page.
            await dot(page, form).focus()
            await page.keyboard.press("Enter")
            await on_record_page(w, world, form, "graph", trail_form)
            await way_back(w, graph)

            # One epic is looked at through the Look at box, and its centre opens its own page.
            await page.select_option("#graph-st-scope", epic)
            await expect(page.locator("#graph-st-title")).to_contain_text(
                f"Focus on {code_of(epic)}", timeout=SLOW
            )
            assert (await hash_of(page))[1].get("scope") == epic
            await expect(page.locator(".graph-x-side")).to_have_count(0)
            focused = await page.evaluate("() => location.hash")
            await page.locator(f"svg.graph-rd-svg g.graph-rd-epic[data-ref='{epic}']").click()
            await on_record_page(w, world, epic, "graph", trail_epic)
            await way_back(w, focused)
            await page.select_option("#graph-st-scope", "")
            await expect(page.locator("svg.graph-st-svg")).to_be_visible(timeout=SLOW)
            await map_at_rest(page)
            assert "scope" not in (await hash_of(page))[1]

            # The Roadmap: a drag still moves a bar, and changes only its dates; a click and Enter
            # open the page of what it draws.
            await w.go("#/roadmap")
            bar = page.locator(f".rm-bar[data-ref='{form}']")
            await expect(bar).to_be_visible(timeout=SLOW)
            before = await ok(client, "get_node", node=form)
            assert (before["start_on"], before["target_on"]) == (day(-2), day(5))
            await drag_bar(page, form, 2)
            for _ in range(SLOW // 250):
                moved = await ok(client, "get_node", node=form)
                if moved["start_on"] != before["start_on"]:
                    break
                await anyio.sleep(0.25)
            assert (moved["start_on"], moved["target_on"]) == (day(0), day(7))
            assert (moved["title"], moved["status"]) == (before["title"], before["status"])
            assert await hash_of(page) == ("roadmap", {})
            await expect(bar).to_be_visible()
            await bar.click()
            await on_record_page(w, world, form, "roadmap", trail_form)
            await way_back(w, "#/roadmap")
            await bar.focus()
            await page.keyboard.press("Enter")
            await on_record_page(w, world, form, "roadmap", trail_form)
            assert w.problems == [], w.problems


# -- 7. every page at every width, in both themes ------------------------------------------------

SCREENS = [(390, 844), (768, 1024), (1280, 900), (1600, 1000)]


async def to_page(w: Watched, name: str, address: str, ref: str | None = None) -> None:
    """Go to a page by its address, in the page that is open, and wait until it has drawn."""
    await w.go(address)
    if ref is None:
        await on_page(w.page, name)
        await ready(w.page)
    else:
        await item_loaded(w.page, ref)


async def test_no_page_scrolls_sideways_every_control_is_named_and_it_all_works_by_keyboard(
    server_url: str,
) -> None:
    async with a_world(server_url) as (world, _):
        word = world.word
        pages: list[tuple[str, str, str | None]] = [
            ("board", "#/board", None),
            ("graph", "#/graph", None),
            ("graph", "#/graph?tab=around", None),
            ("graph", "#/graph?tab=order", None),
            ("lists", "#/lists?view=notes", None),
            ("lists", "#/lists?view=requirements", None),
            ("roadmap", "#/roadmap", None),
            ("search", f"#/search?search={word}", None),
            ("search", f"#/search?search={word}&dups=1", None),
            ("activity", "#/activity", None),
            ("coverage", "#/coverage", None),
            ("users", "#/users", None),
            ("tools", "#/tools", None),
            ("status", "#/status", None),
        ]
        for record in (
            world["epic"],
            world["story"],
            world["form"],
            world.record("rule_placed"),
            world.record("decision_kept"),
            world.record("fact_sure"),
        ):
            pages.append(("item", f"#/item?item={record}&from=board", record))
        for theme in ("light", "dark"):
            async with themed_page(
                world.board,
                signed_in=True,
                scheme=theme,
                viewport={"width": SCREENS[0][0], "height": SCREENS[0][1]},
            ) as w:
                page = w.page
                await w.open("#/board")
                await ready(page)
                for width, height in SCREENS:
                    await page.set_viewport_size({"width": width, "height": height})
                    for name, address, ref in pages:
                        where = f"{theme} {width}px {address}"
                        await to_page(w, name, address, ref)
                        assert (
                            await page.evaluate("document.documentElement.dataset.theme") == theme
                        )
                        found = await structure(page, quick=True)
                        overflow = found["overflow"]
                        assert overflow["doc"] <= width and overflow["body"] <= width, (
                            where,
                            overflow,
                        )
                        assert overflow["offenders"] == [], (where, overflow["offenders"])
                        if name == "roadmap":
                            # The timeline's box fills the rest of the window and the page itself
                            # does not scroll.
                            box = await page.locator("#rm-scroll").bounding_box()
                            assert box is not None, where
                            scroll_height = await page.evaluate(
                                "document.documentElement.scrollHeight"
                            )
                            assert scroll_height <= height, (where, scroll_height)
                            assert height - (box["y"] + box["height"]) <= 20, (where, box)
                        for audit in ("unnamed", "unlabelled", "icons", "glyphs"):
                            assert found[audit] == [], (where, audit, found[audit])
                        assert found["mains"] == 1, where
                        await no_panel_or_drawer(page)

                    # By keyboard alone: the pager of an epic, and the sidebar's tree.
                    epic, next_epic = world["epic"], world["billing"]
                    await to_page(w, "item", f"#/item?item={epic}&from=board", epic)
                    await start_of_page(page)
                    await tab_to(page, "#item-next")
                    ring = await focus_ring(page)
                    assert ring["ok"], (width, theme, ring)
                    await page.keyboard.press("Enter")
                    await item_loaded(page, next_epic)
                    await open_sidebar(page)
                    await start_of_page(page)
                    await tab_to(page, "#epic-list li[role=treeitem][tabindex='0']")
                    ring = await focus_ring(page)
                    assert ring["ok"], (width, theme, ring)
                    await page.keyboard.press("ArrowDown")
                    target = await page.evaluate("document.activeElement.dataset.key")
                    await page.keyboard.press("Enter")
                    await item_loaded(page, target)
                    assert (await hash_of(page))[1]["item"] == target
                    # Choosing a record closes a sidebar that covers the page, and the page's title
                    # takes the focus.
                    await expect(page.locator("#sidebar.open")).to_have_count(0, timeout=SLOW)
                    if width < 1100:
                        # It slides away for a moment before it is hidden: audit the page after.
                        await expect(page.locator("#sidebar")).to_be_hidden(timeout=SLOW)
                    await expect(
                        page.locator("#item-title"), f"{theme} {width}px: the title has the focus"
                    ).to_be_focused(timeout=SLOW)
                    assert (await page.evaluate("document.documentElement.scrollWidth")) <= width
                assert w.problems == [], (theme, w.problems)
