"""M20's navigation against the running containers, driven in a real headless browser: three primary
destinations (Board, Graph and Roadmap) with the Board as the landing view, Search, Activity and the
coverage report as pages of their own, a Settings menu, the quick switcher, the path (the
breadcrumb) that keeps the page a record was reached from, the sidebar's tree of epics, stories and
tasks, old addresses (and the old `sel`, `drawer` and `panel` keys) that mean nothing and have no
alias, drafts that survive everything that is not leaving the page, and a project switch that shows
nothing of the project left even when its answers arrive late. Nothing opens at the side of a
page."""

import asyncio
import contextlib
import re

import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_helpers import (
    FROM_PAGES,
    GONE,
    HEADINGS,
    PAGES,
    SETTINGS,
    SLOW,
    VIEWS,
    board_url,
    browser_page,
    crumb_codes,
    crumb_links,
    crumbs,
    item_address,
    item_loaded,
    made,
    no_panel_or_drawer,
    on_page,
    open_editor,
    open_item,
    sidebar_current,
    tree_expand,
    tree_item,
    tree_open,
    tree_rows,
    view_crumb,
)
from tests.helpers import (
    code_of,
    connect,
    connect_as,
    make_decision,
    make_note,
    make_rule,
    number_of,
    ok,
    the_token,
    unique_word,
)
from tests.world import make_project, project_at, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

TITLES = {
    "board": "Board",
    "graph": "Graph",
    "roadmap": "Roadmap",
    "lists": "Lists",
    "search": "Search",
    "activity": "Activity",
    "needs": "Needs a person",
    "coverage": "Coverage",
}


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


async def hash_of(page: Page) -> str:
    return str(await page.evaluate("location.hash"))


async def becomes(page: Page, address: str) -> None:
    """Wait until the address after the # is this one (the page takes it a moment after it is set,
    and drops what means nothing there)."""
    await page.wait_for_function("hash => location.hash === hash", arg=address, timeout=SLOW)


# -- three destinations, and the Board is where a project opens ---------------------------------


async def test_there_are_four_views_and_the_board_is_the_landing_view(
    board: str,
) -> None:
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        # No page in the address: the Board, and the address says so.
        await w.open("")
        await on_page(page, "board")
        assert page.url.endswith("#/board")

        links = page.locator("#main-nav a")
        assert await links.locator(".side-label").all_inner_texts() == [
            "Board",
            "Graph",
            "Roadmap",
            "Lists",
        ]
        # A view's link is its name and nothing else: no row of numbers beside the four views.
        assert await links.all_inner_texts() == ["Board", "Graph", "Roadmap", "Lists"]
        await expect(page.locator("#main-nav .nav-count")).to_have_count(0)
        assert await links.evaluate_all("els => els.map(e => e.dataset.page)") == VIEWS
        assert await page.locator("#main-nav").get_attribute("aria-label") == "Views"
        # Nothing else in the shell leads to a page of the work: the rest are the project switcher,
        # the tree of epics (each title a link to a record's page), the search box, the link to the
        # Activity page, the Settings menu and the account menu (whose one link is Account).
        top_links = await page.locator("#sidebar a[href^='#/'], #top a[href^='#/']").evaluate_all(
            "els => els.map(e => e.getAttribute('href').split('?')[0].replace('#/', ''))"
        )
        assert set(top_links) <= {*VIEWS, *SETTINGS, "activity", "needs", "item", "account"}
        assert not set(top_links) & set(GONE)
        await expect(page.locator("#project-switcher")).to_be_visible()
        await expect(page.locator("#global-search")).to_be_visible()
        # The box has no button beside it: Enter asks.
        await expect(page.locator("#global-search-go")).to_have_count(0)
        await expect(page.locator("#settings-menu")).to_be_visible()
        await expect(page.locator("#account")).to_be_visible()
        activity = page.locator("a#activity-open")
        await expect(activity).to_be_visible()
        assert await activity.get_attribute("href") == "#/activity"

        # Each destination is a click away, and the one on show is marked.
        for name in ("graph", "roadmap", "lists", "board"):
            await page.locator(f"#main-nav a[data-page={name}]").click()
            await on_page(page, name)
            assert await sidebar_current(page) == [name]
            await no_panel_or_drawer(page)
        # Activity is a page of its own, marked in the sidebar while it is on show.
        await activity.click()
        await on_page(page, "activity")
        assert await sidebar_current(page) == ["activity"]
        # The logo goes home, which is the Board.
        await page.locator("#sidebar a.brand").click()
        await on_page(page, "board")
        assert w.problems == [], w.problems


async def test_the_quick_switcher_offers_the_views_and_pages_and_every_jump_is_a_page(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Jump target {word}")
    story = await made(client, "story", f"Jumped story {word}", epic)
    note = await make_note(client, f"A note to go to {word}", "Said today", nodes=[story])
    decision = await make_decision(client, f"A decision to go to {word}", nodes=[story])
    rule = await make_rule(client, f"An acceptance criterion to go to {word}", node=story)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/roadmap")
        await on_page(page, "roadmap")

        async def jump(words: str) -> None:
            await page.keyboard.press("Control+k")
            await expect(page.locator("#switcher")).to_be_visible()
            await page.locator("#switcher-input").fill(words)
            await page.keyboard.press("Enter")
            await expect(page.locator("#switcher")).to_have_count(0)

        await page.keyboard.press("Control+k")
        await expect(page.locator("#switcher")).to_be_visible()
        # It opens empty: only the box, and the list of places comes with the first Down.
        await expect(page.locator("#switcher-list")).to_be_hidden()
        assert await page.locator("#switcher-input").get_attribute("aria-expanded") == "false"
        await page.keyboard.press("ArrowDown")
        await expect(page.locator("#switcher-list")).to_be_visible()
        titles = await page.locator("#switcher-list li[data-page]").all_inner_texts()
        assert [re.sub(r"^View ", "", text).strip() for text in titles] == [
            "Board",
            "Graph",
            "Roadmap",
            "Lists",
            "Search",
            "Activity",
            "Needs a person",
            "Coverage",
            "Users",
            "Definition of done",
            "Plan templates",
            "Tools",
            "Status and export",
        ]
        await page.keyboard.press("Escape")
        await expect(page.locator("#switcher")).to_have_count(0)

        # It only navigates, and every place it goes to is a page: a record opens its own page, in
        # the page the person is in, whatever the kind of record. A note someone decided is a
        # decision, and either word finds it; a criterion and a note are found by their codes.
        for words, ref in (
            (f"Jump target {word}", epic),
            (code_of(story), story),
            (f"decision {decision['id']}", f"note:{decision['id']}"),
            (f"note {decision['id']}", f"note:{decision['id']}"),
            (f"note {note['id']}", f"note:{note['id']}"),
            (f"N-{note['id']}", f"note:{note['id']}"),
            (f"criterion {rule['id']}", f"rule:{rule['id']}"),
            (f"AC-{rule['id']}", f"rule:{rule['id']}"),
        ):
            await jump(words)
            await item_loaded(page, ref)
            assert page.url.endswith(f"#/item?item={ref}&from=roadmap"), (words, page.url)
            # The path starts at the view the jump was made from: no project, no Projects.
            assert (await crumbs(page))[0] == "Roadmap"
            assert await sidebar_current(page) == ["roadmap"]  # the view it was reached from
            await no_panel_or_drawer(page)
        # A criterion and a note are listed by their codes, with no kind word before the title.
        await page.keyboard.press("Control+k")
        await page.locator("#switcher-input").fill(f"AC-{rule['id']}")
        option = page.locator("#switcher-list li[data-ref]")
        await expect(option).to_have_count(1)
        assert await option.inner_text() == f"AC-{rule['id']} {rule['title']}"
        await page.keyboard.press("Escape")
        await expect(page.locator("#switcher")).to_have_count(0)
        # The views, Search, Activity and the coverage report are jumps too.
        for words, name in (
            ("activity", "activity"),
            ("coverage", "coverage"),
            ("search", "search"),
            ("graph", "graph"),
            ("lists", "lists"),
        ):
            await jump(words)
            await on_page(page, name)
            await no_panel_or_drawer(page)
        assert w.problems == [], w.problems


async def test_the_quick_switcher_opens_with_only_its_box_and_lists_places_when_asked(
    board: str,
) -> None:
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/board")
        await on_page(page, "board")
        box, listing = page.locator("#switcher-input"), page.locator("#switcher-list")
        rows = page.locator("#switcher-list li.switcher-option")

        async def opened() -> None:
            await page.keyboard.press("Control+k")
            await expect(page.locator("#switcher")).to_be_visible()
            await expect(box).to_be_focused()
            # Only the box: no list, no row, and nothing says a list is open.
            await expect(listing).to_be_hidden()
            assert await page.locator("#switcher-list li").count() == 0
            assert await box.get_attribute("aria-expanded") == "false"

        # Typing opens the list, with what the words find.
        await opened()
        await box.fill("gra")
        await expect(listing).to_be_visible()
        assert await box.get_attribute("aria-expanded") == "true"
        assert await page.locator("#switcher-list li[data-page=graph]").count() == 1
        await page.keyboard.press("Escape")
        await expect(page.locator("#switcher")).to_have_count(0)

        # The first Down only opens the list (the first place stays the active one); the next
        # moves down it.
        await opened()
        await page.keyboard.press("ArrowDown")
        await expect(listing).to_be_visible()
        await expect(rows.first).to_have_attribute("aria-selected", "true")
        await page.keyboard.press("ArrowDown")
        await expect(rows.nth(1)).to_have_attribute("aria-selected", "true")
        await expect(rows.first).to_have_attribute("aria-selected", "false")
        await page.keyboard.press("Escape")
        await expect(page.locator("#switcher")).to_have_count(0)

        # A click on the field opens it too; and opened again it is empty again.
        await opened()
        await box.click()
        await expect(listing).to_be_visible()
        await page.keyboard.press("Escape")
        await expect(page.locator("#switcher")).to_have_count(0)
        await opened()
        assert await box.input_value() == ""
        assert w.problems == [], w.problems


# -- the Settings menu --------------------------------------------------------------------------


async def test_the_settings_menu_works_from_the_keyboard_and_reaches_its_five_pages(
    board: str,
) -> None:
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/board")
        button = page.locator("#settings-menu")
        menu = page.locator("#settings-list")
        items = page.locator("#settings-list [role=menuitem]")
        await expect(button).to_be_visible()
        await expect(menu).to_be_hidden()
        assert await button.get_attribute("aria-haspopup") == "menu"
        assert await button.get_attribute("aria-expanded") == "false"

        # Enter opens it, with the first choice focused, and the arrows move round it.
        await button.focus()
        await page.keyboard.press("Enter")
        await expect(menu).to_be_visible()
        assert await button.get_attribute("aria-expanded") == "true"
        assert await items.all_inner_texts() == [
            "Users",
            "Definition of done",
            "Plan templates",
            "Tools",
            "Status and export",
        ]
        await expect(page.locator("#settings-users")).to_be_focused()
        for key, focused in (
            ("ArrowDown", "definition"),
            ("ArrowDown", "templates"),
            ("ArrowDown", "tools"),
            ("ArrowDown", "status"),
            ("ArrowDown", "users"),
            ("ArrowUp", "status"),
            ("Home", "users"),
            ("End", "status"),
        ):
            await page.keyboard.press(key)
            await expect(page.locator(f"#settings-{focused}")).to_be_focused()

        # Escape closes it and gives the focus back to the button.
        await page.keyboard.press("Escape")
        await expect(menu).to_be_hidden()
        await expect(button).to_be_focused()
        assert await button.get_attribute("aria-expanded") == "false"
        # An arrow up from the button opens it on the last choice, and a click elsewhere closes it.
        await page.keyboard.press("ArrowUp")
        await expect(page.locator("#settings-status")).to_be_focused()
        await page.locator("main#page").click(position={"x": 4, "y": 4})
        await expect(menu).to_be_hidden()

        # Each page is reached with the keyboard alone, and the menu's own button is marked while
        # a settings page is open.
        for steps, name in enumerate(SETTINGS):
            await w.go("#/board")
            await on_page(page, "board")
            await button.focus()
            await page.keyboard.press("Enter")
            for _ in range(steps):
                await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            await on_page(page, name)
            await expect(menu).to_be_hidden()
            assert await button.get_attribute("aria-current") == "page"
            assert await sidebar_current(page) == []
        # The pages are the board's own: the live tool list and the status, no model anywhere.
        assert "#/status" in page.url
        await expect(page.locator("#status-body")).to_be_visible(timeout=SLOW)
        assert "language model" not in (await page.locator("main#page").inner_text()).lower()
        assert w.problems == [], w.problems


async def test_every_view_is_one_click_from_every_page_and_each_page_marks_where_you_are(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Always reachable {word}")
    story = await made(client, "story", f"Reachable story {word}", epic)
    # Where each page leaves the marks of the sidebar: a view, Activity, or nothing.
    marked = {
        "search": [],
        "activity": ["activity"],
        "coverage": [],
        "users": [],
        "definition": [],
        "templates": [],
        "tools": [],
        "status": [],
        "account": [],
        f"item?item={story}&from=roadmap": ["roadmap"],
        f"item?item={story}&from=activity": ["activity"],
    }
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/board")
        await on_page(page, "board")
        for start, marks in marked.items():
            await w.go(f"#/{start}")
            await on_page(page, start.split("?")[0])
            assert await sidebar_current(page) == marks, start
            await no_panel_or_drawer(page)
            for name in VIEWS:
                await page.locator(f"#main-nav a[data-page={name}]").click()
                await on_page(page, name)
                assert await sidebar_current(page) == [name], (start, name)
                await w.go(f"#/{start}")
                await on_page(page, start.split("?")[0])
        assert w.problems == [], w.problems


async def test_the_path_is_the_page_you_are_in_and_the_epic_tree_is_for_work_pages_only(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Tree and path {word}")
    story = await made(client, "story", f"Path of a story {word}", epic)
    # What the path says on each page: only the page, there is no Projects and no project in it.
    titles = {name: HEADINGS[name] for name in (*PAGES, "account")}
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        project = project_at(board)
        await w.open("#/board")
        await on_page(page, "board")
        for name in (*PAGES, "account"):
            await w.go(f"#/{name}")
            await on_page(page, name)
            assert await crumbs(page) == [titles[name]], name
            await expect(page.locator("#crumb-page")).to_have_text(titles[name])
            assert await page.locator("nav.crumbs").count() == 1
            assert project.name not in await page.locator("nav.crumbs").inner_text()
            assert "Projects" not in await page.locator("nav.crumbs").inner_text()
            # The tree of epics is beside the work, and out of the way of the settings pages and
            # the account page (where it would have nothing to do with the page).
            tree = page.locator("#epic-list")
            if name in (*SETTINGS, "account"):
                await expect(tree).to_be_hidden()
                await expect(page.locator("#epics-heading")).to_be_hidden()
            else:
                await expect(tree).to_be_visible()
                await expect(tree_item(page, epic)).to_be_visible(timeout=SLOW)
        # The page of a record is a work page: its path ends with the record's code and its name.
        await w.go(item_address(story))
        await item_loaded(page, story)
        await expect(page.locator("#epic-list")).to_be_visible()
        assert await crumbs(page) == [
            "Board",
            f"{code_of(epic)} Tree and path {word}",
            f"{code_of(story)} Path of a story {word}",
        ]
        assert await crumb_codes(page) == ["Board", code_of(epic), code_of(story)]
        # Back on a work page, the tree is there again, with what it showed before.
        await w.go("#/roadmap")
        await on_page(page, "roadmap")
        await expect(tree_item(page, epic)).to_be_visible(timeout=SLOW)
        assert w.problems == [], w.problems


async def test_no_page_repeats_the_title_the_top_bar_gives_it_in_a_heading_of_its_own(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"One title {word}")
    story = await made(client, "story", f"One title story {word}", epic)
    # A heading is on show when it has a box bigger than the 1 px by 1 px a screen-reader-only
    # heading is clipped to.
    seen = """() => {
        const shown = (e) => {
            const box = e.getBoundingClientRect();
            return box.width > 2 && box.height > 2 && getComputedStyle(e).visibility !== 'hidden';
        };
        return {
            h1: [...document.querySelectorAll('#page h1')].map((e) => ({
                text: e.textContent.trim(), srOnly: e.classList.contains('sr-only'),
                shown: shown(e),
            })),
            headings: [...document.querySelectorAll('#page h1, #page h2, #page .page-title')]
                .filter(shown).map((e) => e.textContent.trim()),
            old: document.querySelectorAll('#page .page-title').length,
            inHeader: [...document.querySelectorAll(
                '#page header.page-header h1, #page header.page-header h2'
            )].filter(shown).length,
        };
    }"""
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/board")
        for name in (*PAGES, "account"):
            await w.go(f"#/{name}")
            await on_page(page, name)
            title = HEADINGS[name]
            # The top bar names the page; the page has one h1 with the same words, for a screen
            # reader only, and no heading on show says them again.
            await expect(page.locator("#crumb-page")).to_have_text(title)
            found = await page.evaluate(seen)
            assert found["h1"] == [{"text": title, "srOnly": True, "shown": False}], (name, found)
            assert title not in found["headings"], (name, found)
            assert found["old"] == 0, name
            # Where a page has actions of its own they sit in a header row that holds no title.
            assert found["inHeader"] == 0, (name, found)
        # The page of a record keeps its own title, on show: it is the record, not a page's name.
        await w.go(item_address(story))
        await item_loaded(page, story)
        found = await page.evaluate(seen)
        assert [(one["srOnly"], one["shown"]) for one in found["h1"]] == [(False, True)]
        assert f"One title story {word}" in found["h1"][0]["text"]
        assert w.problems == [], w.problems


# -- old addresses ------------------------------------------------------------------------------


async def test_the_addresses_of_pages_that_are_gone_land_on_the_board_and_nothing_is_aliased(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Not opened by an old address {word}")
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/board")
        await on_page(page, "board")
        for name in GONE:
            # Followed from inside the page, with and without the choices the page used to take.
            for tail in ("", "?epic=1&sort=-priority&card=2"):
                await w.go("#/graph")
                await on_page(page, "graph")
                await w.go(f"#/{name}{tail}")
                await on_page(page, "board")
                assert page.url.endswith("#/board"), (name, tail, page.url)
                await no_panel_or_drawer(page)
        # An address that names nothing at all is the Board too.
        for nonsense in ("#/", "#/no-such-page", "#nothing"):
            await w.go("#/roadmap")
            await on_page(page, "roadmap")
            await w.go(nonsense)
            await on_page(page, "board")
        # Opened as a bookmark would be, in a fresh load, and with the old choice for an item.
        for name in GONE:
            await page.goto(f"{board}#/{name}?card={number_of(epic)}")
            await on_page(page, "board")
            assert page.url.endswith("#/board"), (name, page.url)
            await no_panel_or_drawer(page)
        # The pages that remain are the only ones with a heading of their own.
        for name in PAGES:
            await w.go(f"#/{name}")
            await on_page(page, name)
        assert w.problems == [], w.problems


async def test_the_keys_of_the_old_panel_and_drawers_are_dropped_from_any_address(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Old keys {word}")
    story = await made(client, "story", f"Old keys story {word}", epic)
    scope = number_of(epic)
    # What is opened, and the address it settles on: `drawer` and `panel` mean nothing anywhere,
    # and `sel` means something only in Lists, where it is the record that is marked.
    cases = [
        ("#/board?drawer=activity", "#/board", "board"),
        (f"#/board?epic={scope}&sel={story}", f"#/board?epic={scope}", "board"),
        ("#/roadmap?panel=coverage&drawer=search", "#/roadmap", "roadmap"),
        (f"#/roadmap?sel={story}", "#/roadmap", "roadmap"),
        (
            f"#/item?item={story}&from=roadmap&sel={epic}&drawer=activity&panel=coverage",
            f"#/item?item={story}&from=roadmap",
            "item",
        ),
        ("#/search?search=old&drawer=search", "#/search?search=old", "search"),
        ("#/activity?drawer=activity&panel=x", "#/activity", "activity"),
        (f"#/coverage?sel={story}&panel=coverage", "#/coverage", "coverage"),
        (f"#/graph?sel={story}&drawer=activity&panel=coverage", "#/graph", "graph"),
        (f"#/lists?sel={story}&drawer=activity&panel=coverage", f"#/lists?sel={story}", "lists"),
    ]
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/board")
        await on_page(page, "board")
        for address, settled, name in cases:
            # Followed from inside the page, as a link would, and opened afresh, as a bookmark.
            await w.go("#/status")
            await on_page(page, "status")
            await w.go(address)
            await becomes(page, settled)
            await on_page(page, name)
            await no_panel_or_drawer(page)
            await page.goto(f"{board}?again={unique_word()}{address}")
            await becomes(page, settled)
            await on_page(page, name)
            await no_panel_or_drawer(page)
        assert w.problems == [], w.problems


# -- the search box and the Activity link are on every view -------------------------------------


async def test_the_search_box_is_on_every_view_and_goes_to_the_search_page_with_those_words(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Searchable {word}")
    story = await made(client, "story", f"Findable story {word}", epic)
    words = f"findable story {word}"
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        for name in VIEWS:
            await w.open(f"#/{name}")
            await on_page(page, name)
            box = page.locator("#global-search")
            await expect(box).to_be_visible()
            assert await page.locator("#global-search-form").get_attribute("role") == "search"
            await box.fill(words)
            await box.press("Enter")
            # The Search page replaces the view, and the box of the top bar shows what was asked.
            await on_page(page, "search")
            assert "search=" in page.url and "drawer" not in page.url
            await expect(page.locator("#search-box")).to_have_value(words)
            await expect(box).to_have_value(words)
            await no_panel_or_drawer(page)
            # The words are found, with how they matched, and a result opens the record's own page,
            # which is still "in" Search.
            row = page.locator(f"#search-results tr.result[data-type=story][data-ref='{story}']")
            await expect(row).to_be_visible(timeout=SLOW)
            assert await row.get_attribute("data-matched") in {"words", "both"}
            await row.locator("a.result-title").click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", story, timeout=SLOW
            )
            assert page.url.endswith(f"#/item?item={story}&from=search")
            assert (await crumbs(page))[0] == "Search"
            await no_panel_or_drawer(page)
            # The way back is the search as it was left, with its words and its result.
            await view_crumb(page).click()
            await on_page(page, "search")
            await expect(page.locator("#search-box")).to_have_value(words)
            await expect(row).to_be_visible(timeout=SLOW)
        assert w.problems == [], w.problems


async def test_what_is_typed_in_the_search_box_stays_while_the_view_changes(board: str) -> None:
    word = unique_word()
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/board")
        await on_page(page, "board")
        await page.locator("#global-search").fill(f"not yet asked {word}")
        for name in ("graph", "roadmap", "board"):
            await page.locator(f"#main-nav a[data-page={name}]").click()
            await on_page(page, name)
            assert await page.locator("#global-search").input_value() == f"not yet asked {word}"
        # Nothing was searched for, so the person is still on a view and nothing opened beside it.
        assert (await hash_of(page)).startswith("#/board")
        await no_panel_or_drawer(page)
        assert w.problems == [], w.problems


async def test_the_activity_link_leads_to_the_activity_page_from_every_view_and_by_keyboard(
    client: Client, board: str
) -> None:
    word = unique_word()
    await made(client, "epic", f"Activity {word}")
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        for name in VIEWS:
            await w.open(f"#/{name}")
            await on_page(page, name)
            link = page.locator("a#activity-open")
            await expect(link).to_be_visible()
            assert await sidebar_current(page) == [name]
            await link.click()
            await on_page(page, "activity")
            assert await hash_of(page) == "#/activity"
            assert await sidebar_current(page) == ["activity"]
            # The page opens on Changes, the first of its three views, and who is working is one
            # click away on the same page, which is no longer a view.
            await expect(page.locator("#feed li.event").first).to_be_visible(timeout=SLOW)
            await expect(page.locator("#activity-tab-changes")).to_have_attribute(
                "aria-pressed", "true"
            )
            await page.locator("#activity-tab-working").click()
            await expect(page.locator("#working")).to_be_visible(timeout=SLOW)
            await no_panel_or_drawer(page)
            # A view is a click away again, and the Activity link is no longer marked.
            await page.locator(f"#main-nav a[data-page={name}]").click()
            await on_page(page, name)
            assert await sidebar_current(page) == [name]

        # With the keyboard: Enter on the link opens the page.
        await w.open("#/graph")
        await on_page(page, "graph")
        await page.locator("a#activity-open").focus()
        await page.keyboard.press("Enter")
        await on_page(page, "activity")
        # Opened by address, the page shows the newest work first.
        await w.go("#/roadmap")
        await on_page(page, "roadmap")
        await w.go("#/activity")
        await expect(
            page.locator("#feed li.event", has_text=f"Activity {word}").first
        ).to_be_visible(timeout=SLOW)
        assert w.problems == [], w.problems


# -- the path keeps the page a record was reached from ---------------------------------------------


async def test_a_record_page_keeps_the_page_it_was_reached_from_in_its_path_sidebar_and_links(
    client: Client, board: str
) -> None:
    word = unique_word()
    actor = f"walker-{word}"
    epic = await made(client, "epic", f"Path epic {word}", actor=actor)
    story = await made(client, "story", f"Path story {word}", epic, actor=actor)
    after = await made(client, "story", f"Path next story {word}", epic, actor=actor)
    task = await made(client, "task", f"Path task {word}", story, actor=actor)
    project = project_at(board)
    # Each record in the path is its code and its name, the code never alone.
    path = [
        "",
        f"{code_of(epic)} Path epic {word}",
        f"{code_of(story)} Path story {word}",
        f"{code_of(task)} Path task {word}",
    ]
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        for from_page in FROM_PAGES:
            await w.open(item_address(task, from_page))
            await page.reload()
            await item_loaded(page, task)
            # The path starts with the page the person is in (there is no Projects and no project
            # in it: the project is in the sidebar), then the epic and story above.
            path[0] = TITLES[from_page]
            assert await crumbs(page) == path, from_page
            assert project.name not in " ".join(await crumbs(page))
            links = await crumb_links(page)
            assert [href is None for _, href in links] == [False, False, False, True]
            # After a load nothing says which choices the page had, so the way back is the page.
            assert links[0][1] == f"#/{from_page}"
            # The sidebar keeps the view highlighted (and Activity, when that is where it is from).
            assert await sidebar_current(page) == (
                [from_page] if from_page in (*VIEWS, "activity", "needs") else []
            ), from_page
            # Every link to a record, in the page, the path and the tree, keeps the same page.
            hrefs = await page.locator(
                "#item-page a[href^='#/item'], nav.crumbs a[href^='#/item'], "
                "#epic-list a[href^='#/item']"
            ).evaluate_all("els => els.map(e => e.getAttribute('href'))")
            assert hrefs and all(href.endswith(f"&from={from_page}") for href in hrefs), hrefs
            await no_panel_or_drawer(page)
            # The tab and the tooltip of a step name the record by its code and its name.
            assert (await page.title()).startswith(f"{path[3]} · "), await page.title()
            first_step = page.locator("nav.crumbs li.crumb-item a").first
            assert await first_step.get_attribute("title") == path[1]
            assert await first_step.get_attribute("aria-label") is None

            # From the story above, the next story, the epic and the way back all stay in it.
            await page.locator("nav.crumbs li.crumb-item a").nth(1).click()
            await item_loaded(page, story)
            assert page.url.endswith(f"#/item?item={story}&from={from_page}")
            # The arrow at the left of the bar goes one step back, and says where in words.
            arrow = page.locator("#back-to-view")
            await expect(arrow).to_have_attribute("aria-label", f"Back to {path[3]}")
            assert await arrow.get_attribute("title") == f"Back to {path[3]}"
            await page.locator("#item-next").click()
            await item_loaded(page, after)
            assert page.url.endswith(f"#/item?item={after}&from={from_page}")
            await page.locator("nav.crumbs li.crumb-item a").click()
            await item_loaded(page, epic)
            assert page.url.endswith(f"#/item?item={epic}&from={from_page}")
            await view_crumb(page).click()
            await on_page(page, from_page)
        assert w.problems == [], w.problems


async def test_the_way_back_is_the_view_as_it_was_left_with_its_filter_and_choices(
    board: str,
) -> None:
    word = unique_word()
    actor = f"left-{word}"
    async with connect_as(actor) as client:
        epic = await made(client, "epic", f"Left as it was {word}")
        story = await made(client, "story", f"Left story {word}", epic)
    scope = number_of(epic)
    views = {
        "board": f"#/board?q=epic:{scope}&epic={scope}",
        "graph": f"#/graph?tab=order&focus={epic}",
        "roadmap": f"#/roadmap?q=epic:{scope}",
        "lists": f"#/lists?view=notes&epic={scope}",
        "search": f"#/search?search=left+story+{word}",
        "activity": f"#/activity?act_actor={actor}",
    }
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        for name, address in views.items():
            await w.open(address)
            await on_page(page, name)
            assert await hash_of(page) == address, name
            # Open a record from it, as a click on one of its links would (a link's address is the
            # record's page and the page it is in).
            await w.go(item_address(story, name))
            await item_loaded(page, story)
            assert await view_crumb(page).get_attribute("href") == address, name
            await view_crumb(page).click()
            await on_page(page, name)
            assert await hash_of(page) == address, name
        # The Activity page shows only that person's work, as it was left.
        await expect(page.locator("#a-actor")).to_have_value(actor)
        events = page.locator("#feed li.event")
        await expect(events.first).to_be_visible(timeout=SLOW)
        actors = await events.evaluate_all("els => [...new Set(els.map(e => e.dataset.actor))]")
        assert actors == [actor]
        assert w.problems == [], w.problems


# -- the sidebar's tree of epics, stories and tasks ----------------------------------------------


async def test_the_sidebars_tree_matches_the_work_opens_pages_and_works_from_the_keyboard() -> None:
    word = unique_word()
    project = make_project(register(), f"Tree {word}")
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key) as w,
    ):
        page = w.page
        first = await made(agent, "epic", f"First epic {word}")
        second = await made(agent, "epic", f"Second epic {word}")
        one = await made(agent, "story", f"One {word}", first)
        two = await made(agent, "story", f"Two {word}", first)
        tasks = [await made(agent, "task", f"Task {n} {word}", one) for n in (1, 2, 3)]
        await w.open("#/board")
        await on_page(page, "board")

        def shape(rows: list[dict]) -> list[tuple]:  # type: ignore[type-arg]
            return [(r["ref"], r["level"], r["expanded"], r["current"]) for r in rows]

        # Only epics are drawn at first, closed where they have stories, with their code and title.
        rows = await tree_rows(page)
        assert shape(rows) == [(first, 1, False, False), (second, 1, None, False)]
        assert [(r["code"], r["title"]) for r in rows] == [
            (code_of(first), f"First epic {word}"),
            (code_of(second), f"Second epic {word}"),
        ]
        # A chevron opens a branch, and what is under it is what list_nodes says, by number.
        await tree_expand(page, first)
        under = [n["ref"] for n in (await ok(agent, "list_nodes", parent=first))["items"]]
        assert under == [one, two]
        assert shape(await tree_rows(page)) == [
            (first, 1, True, False),
            (one, 2, False, False),
            (two, 2, None, False),
            (second, 1, None, False),
        ]
        await tree_expand(page, one)
        rows = await tree_rows(page)
        assert [r["ref"] for r in rows] == [first, one, *tasks, two, second]
        assert [(r["position"], r["size"]) for r in rows if r["level"] == 3] == [
            (1, 3),
            (2, 3),
            (3, 3),
        ]

        # A title opens the page of the record, in the page the person is in, and marks it.
        await tree_open(page, tasks[1])
        assert page.url.endswith(f"#/item?item={tasks[1]}&from=board")
        assert [r["ref"] for r in await tree_rows(page) if r["current"]] == [tasks[1]]
        await expect(page.locator("#epic-list a[aria-current=page]")).to_have_count(1)
        # Its neighbour in the page is its neighbour in the tree.
        await page.locator("#item-next").click()
        await item_loaded(page, tasks[2])
        assert [r["ref"] for r in await tree_rows(page) if r["current"]] == [tasks[2]]

        # Closing the branch it is in hides it from the tree and leaves the page alone; another
        # page of something inside opens the branches above it again.
        await tree_expand(page, first, False)
        assert shape(await tree_rows(page)) == [(first, 1, False, False), (second, 1, None, False)]
        await item_loaded(page, tasks[2])
        await w.go(item_address(tasks[0]))
        await item_loaded(page, tasks[0])
        rows = await tree_rows(page)
        assert [r["ref"] for r in rows] == [first, one, *tasks, two, second]
        assert [r["ref"] for r in rows if r["expanded"]] == [first, one]
        assert [r["ref"] for r in rows if r["current"]] == [tasks[0]]

        # The keyboard: arrows move, Right opens, Left closes (or goes up), Enter opens the page.
        await tree_expand(page, one, False)
        await tree_expand(page, first, False)
        stop = tree_item(page, first)
        await stop.focus()
        await page.keyboard.press("ArrowRight")  # opens
        await expect(stop).to_have_attribute("aria-expanded", "true")
        await page.keyboard.press("ArrowDown")
        await expect(tree_item(page, one)).to_be_focused()
        await page.keyboard.press("ArrowRight")  # opens the story
        await expect(tree_item(page, one)).to_have_attribute("aria-expanded", "true")
        await page.keyboard.press("ArrowRight")  # goes in
        await expect(tree_item(page, tasks[0])).to_be_focused()
        await page.keyboard.press("ArrowLeft")  # up to its story
        await expect(tree_item(page, one)).to_be_focused()
        await page.keyboard.press("ArrowLeft")  # closes it
        await expect(tree_item(page, one)).to_have_attribute("aria-expanded", "false")
        await page.keyboard.press("End")
        await expect(tree_item(page, second)).to_be_focused()
        await page.keyboard.press("Home")
        await expect(tree_item(page, first)).to_be_focused()
        assert await page.locator("#epic-list li[role=treeitem][tabindex='0']").count() == 1
        await page.keyboard.press("End")
        await page.keyboard.press("Enter")
        await item_loaded(page, second)
        assert page.url.endswith(f"#/item?item={second}&from=board")
        await expect(page.locator("#item-title")).to_be_focused()

        # It follows the work: a story made by an agent under the empty epic, and a new title.
        late = await made(agent, "story", f"Arrived {word}", second)
        await expect(tree_item(page, second)).to_have_attribute(
            "aria-expanded", "false", timeout=SLOW
        )
        await ok(
            agent, "update_node", actor="a", node=two, expected_version=1, title=f"Renamed {word}"
        )
        await expect(tree_item(page, two).locator(".tree-title")).to_have_text(
            f"Renamed {word}", timeout=SLOW
        )
        await tree_expand(page, second)
        assert [r["ref"] for r in await tree_rows(page)][-1] == late
        assert w.problems == [], w.problems


# -- a draft is never discarded ------------------------------------------------------------------


async def test_a_draft_on_a_record_page_survives_everything_that_is_not_leaving_the_page(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Drafted {word}")
    story = await made(client, "story", f"Story {word}", epic)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await open_item(w, story)
        await open_editor(page)
        await page.locator("#edit-title").fill(f"My draft {word}")
        await page.locator("#edit-body").fill(f"Half a thought {word}")
        await page.evaluate("window.__draft = document.getElementById('edit-title'); true")

        async def draft_is_kept() -> None:
            assert await page.locator("#edit-title").input_value() == f"My draft {word}"
            assert await page.locator("#edit-body").input_value() == f"Half a thought {word}"
            assert await page.evaluate("document.getElementById('edit-title') === window.__draft")

        # The quick switcher opens and closes without going anywhere.
        await page.keyboard.press("Control+k")
        await expect(page.locator("#switcher")).to_be_visible()
        await page.locator("#switcher-input").fill("nothing in particular")
        await page.keyboard.press("Escape")
        await expect(page.locator("#switcher")).to_have_count(0)
        await draft_is_kept()
        # Words typed in the search box and not asked, and the menu of settings opened and closed.
        await page.locator("#global-search").fill(word)
        await page.locator("#settings-menu").click()
        await expect(page.locator("#settings-list")).to_be_visible()
        await page.keyboard.press("Escape")
        await expect(page.locator("#settings-list")).to_be_hidden()
        await draft_is_kept()
        # The tree of epics, opened and closed beside the page.
        await tree_expand(page, epic)
        await tree_expand(page, epic, False)
        await draft_is_kept()
        # News about other work arrives (the tree shows it) and the page is not drawn over.
        other = await made(client, "epic", f"Arrived meanwhile {word}")
        await expect(tree_item(page, other)).to_be_visible(timeout=SLOW)
        await draft_is_kept()
        # Nothing was saved by any of it.
        assert (await ok(client, "get_node", node=story))["version"] == 1
        assert w.problems == [], w.problems


# -- project switching --------------------------------------------------------------------------


async def test_switching_project_with_late_answers_shows_nothing_of_the_first_in_any_page() -> None:
    word = unique_word()
    owner = register(name=f"Switcher {word}")
    first = make_project(owner, f"First {word}")
    second = make_project(owner, f"Second {word}")
    async with (
        connect(first.mcp_url, first.key) as one,
        connect(second.mcp_url, second.key) as two,
    ):
        epic_one = await made(one, "epic", f"Only in the first {word}")
        story_one = await made(one, "story", f"First story {word}", epic_one)
        fact_one = await make_note(one, f"A first note {word}", "The first", nodes=[story_one])
        await made(two, "epic", f"Only in the second {word}")

        async with browser_page(first.address, token=first.key, name=None, keep_bodies=True) as w:
            page = w.page
            await w.open(f"#/search?search=first+{word}")
            await expect(page.locator("#search-results tr.result").first).to_be_visible(
                timeout=SLOW
            )
            await page.locator(
                f"#search-results tr.result[data-ref='{story_one}'] a.result-title"
            ).click()
            await item_loaded(page, story_one)
            await expect(
                page.locator(f"#item-page tr.record-line[data-ref='note:{fact_one['id']}']")
            ).to_be_visible(timeout=SLOW)
            # From now on the first project's answers (the poll, the lists, the search and the
            # page's) are held back, and an agent changes the first project so that there is news
            # to hold back.
            released = asyncio.Event()
            held: list[str] = []

            async def hold(route):  # type: ignore[no-untyped-def]
                held.append(route.request.url)
                await released.wait()
                with contextlib.suppress(Exception):
                    await route.continue_()

            await page.route(f"**/board/api/projects/{first.id}/tool/**", hold)
            await made(one, "epic", f"News for the first {word}")
            for _ in range(200):
                if held:
                    break
                await asyncio.sleep(0.1)
            assert held, "no poll of the first project was held back"

            # Switch to the second project from the sidebar while they are held back.
            before = len(w.requests)
            await page.locator("#project-switcher").select_option(label=f"Second {word}")
            await expect(page.locator("#project-switcher option:checked")).to_have_text(
                f"Second {word}", timeout=SLOW
            )
            assert f"/board/p/{second.id}" in page.url
            await on_page(page, "board")
            await expect(
                page.get_by_role("region", name=f"Epic Only in the second {word}")
            ).to_be_visible(timeout=SLOW)
            # The first project's search and record did not come along.
            await no_panel_or_drawer(page)
            assert await page.locator("#global-search").input_value() == ""
            assert "item=" not in page.url and "search=" not in page.url
            released.set()

            # Every page: nothing of the first project, however late its answers are.
            for address in (
                "#/board",
                "#/graph?tab=around",
                "#/lists?view=notes",
                "#/lists?view=everything",
                "#/roadmap",
                "#/activity",
                "#/coverage",
                f"#/search?search=first+{word}",
                f"#/item?item={story_one}&from=board",
                f"#/item?item=note:{fact_one['id']}&from=search",
            ):
                await w.go(address)
                await asyncio.sleep(1)
                shown = await page.locator("body").inner_text()
                for gone in (f"Only in the first {word}", f"News for the first {word}"):
                    assert gone not in shown, (address, gone)
                assert f"A first note {word}" not in shown and f"First story {word}" not in shown
            await asyncio.sleep(2)
            # After the switch the page asked only about the second project, and no answer it
            # received held the first project's words.
            later = w.requests[before:]
            assert not [url for url in later if f"/projects/{first.id}/" in url], later
            after = [text for url, text in w.bodies if f"/projects/{second.id}/" in url]
            assert after
            for text in after:
                for gone in (
                    f"Only in the first {word}",
                    f"News for the first {word}",
                    f"First story {word}",
                    f"A first note {word}",
                ):
                    assert gone not in text, gone


async def test_two_choices_made_one_right_after_the_other_both_reach_the_address(
    board: str, client: Client
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Choices {word}")
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/lists")
        await expect(page.locator("#lists-page")).to_be_visible(timeout=SLOW)
        # No pause between the changes: the address is handled a moment after it is set, and each
        # change has to build on the one before it, not on the address as it was.
        await page.locator("#lists-view-notes").click()
        await page.locator("#lists-epic").select_option(str(number_of(epic)))
        await expect(page.locator("#lists-page")).to_have_attribute("data-view", "notes")
        address = await page.evaluate("location.hash")
        assert "view=notes" in address
        assert f"epic={number_of(epic)}" in address
