"""M16's navigation against the running containers, driven in a real headless browser: three
primary destinations (Board, Graph and Roadmap), a Settings menu, the Board as the landing view,
old addresses that land on the Board with no alias, one search box and one Activity button on
every view that open drawers, drafts that survive drawers and views, and a project switch that
shows nothing of the project left even when its answers arrive late."""

import asyncio
import contextlib
import re

import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_helpers import GONE, PAGES, SETTINGS, SLOW, VIEWS, board_url, browser_page, made
from tests.helpers import code_of, connect, number_of, ok, the_token, unique_word
from tests.world import make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

HEADINGS = {
    "board": "Board",
    "graph": "Graph",
    "roadmap": "Roadmap",
    "access": "Agent access",
    "tools": "Tools",
    "status": "Status and export",
}


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


async def on_page(page: Page, name: str) -> None:
    """Wait until the page the address names is the one on show."""
    await expect(page.locator("main#page h1").first).to_have_text(HEADINGS[name], timeout=SLOW)
    assert (await page.evaluate("location.hash")).startswith(f"#/{name}")


# -- three destinations, and the Board is where a project opens ---------------------------------


async def test_there_are_exactly_three_primary_destinations_and_the_board_is_the_landing_view(
    board: str,
) -> None:
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        # No page in the address: the Board, and the address says so.
        await w.open("")
        await on_page(page, "board")
        assert page.url.endswith("#/board")

        links = page.locator("#main-nav a")
        assert await links.locator(".side-label").all_inner_texts() == ["Board", "Graph", "Roadmap"]
        assert await links.evaluate_all("els => els.map(e => e.dataset.page)") == VIEWS
        assert await page.locator("#main-nav").get_attribute("aria-label") == "Views"
        # Nothing else in the shell leads to a page of the work: the rest are the project switcher,
        # the epics (each a link to the Board for that epic), the search box, the Activity button
        # and the Settings menu.
        top_links = await page.locator("#sidebar a[href^='#/'], #top a[href^='#/']").evaluate_all(
            "els => els.map(e => e.getAttribute('href').split('?')[0].replace('#/', ''))"
        )
        assert set(top_links) <= {*VIEWS, *SETTINGS}
        assert not set(top_links) & set(GONE)
        await expect(page.locator("#project-switcher")).to_be_visible()
        await expect(page.locator("#global-search")).to_be_visible()
        await expect(page.locator("#activity-open")).to_be_visible()
        await expect(page.locator("#settings-menu")).to_be_visible()

        # Each destination is a click away, and the one on show is marked.
        for name in ("graph", "roadmap", "board"):
            await page.locator(f"#main-nav a[data-page={name}]").click()
            await on_page(page, name)
            current = await links.evaluate_all(
                "els => els.filter(e => e.getAttribute('aria-current') === 'page')"
                ".map(e => e.dataset.page)"
            )
            assert current == [name]
        # The logo goes home, which is the Board.
        await page.locator("#sidebar a.brand").click()
        await on_page(page, "board")
        assert w.problems == [], w.problems


async def test_the_quick_switcher_offers_the_three_views_the_drawers_and_the_settings_pages(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Jump target {word}")
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/roadmap")
        await on_page(page, "roadmap")
        await page.keyboard.press("Control+k")
        await expect(page.locator("#switcher")).to_be_visible()
        titles = await page.locator("#switcher-list li[data-page]").all_inner_texts()
        assert [re.sub(r"^View ", "", text).strip() for text in titles] == [
            "Board",
            "Graph",
            "Roadmap",
            "Search",
            "Activity",
            "Agent access",
            "Tools",
            "Status and export",
        ]
        # It only navigates: an item opens in the panel of the view that is open, and a drawer
        # opens by name.
        await page.locator("#switcher-input").fill(word)
        await page.keyboard.press("Enter")
        await expect(page.locator("#card-title")).to_contain_text(f"{code_of(epic)} ", timeout=SLOW)
        assert page.url.split("#")[1].startswith("/roadmap") and f"sel={epic}" in page.url
        await page.keyboard.press("Control+k")
        await page.locator("#switcher-input").fill("activity")
        await page.keyboard.press("Enter")
        await expect(page.locator("#drawer[data-drawer=activity]")).to_be_visible()
        assert w.problems == [], w.problems


# -- the Settings menu --------------------------------------------------------------------------


async def test_the_settings_menu_works_from_the_keyboard_and_reaches_agent_access_tools_and_status(
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
        assert await items.all_inner_texts() == ["Agent access", "Tools", "Status and export"]
        await expect(page.locator("#settings-access")).to_be_focused()
        for key, focused in (
            ("ArrowDown", "tools"),
            ("ArrowDown", "status"),
            ("ArrowDown", "access"),
            ("ArrowUp", "status"),
            ("Home", "access"),
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
        await page.locator("main#page h1").click()
        await expect(menu).to_be_hidden()

        # Each page is reached with the keyboard alone, and the menu's own button is marked while
        # a settings page is open.
        for steps, name in ((0, "access"), (1, "tools"), (2, "status")):
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
            current = await page.locator("#main-nav a").evaluate_all(
                "els => els.filter(e => e.getAttribute('aria-current') === 'page').length"
            )
            assert current == 0
        # The pages are the board's own: the live tool list and the status, no model anywhere.
        assert "#/status" in page.url
        await expect(page.locator("#status-body")).to_be_visible(timeout=SLOW)
        assert "language model" not in (await page.locator("main#page").inner_text()).lower()
        assert w.problems == [], w.problems


async def test_a_drawer_stays_open_on_a_settings_page_and_the_views_are_a_click_away_again(
    board: str,
) -> None:
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/board?drawer=activity")
        await expect(page.locator("#drawer[data-drawer=activity]")).to_be_visible(timeout=SLOW)
        await page.locator("#settings-menu").click()
        await page.locator("#settings-tools").click()
        await on_page(page, "tools")
        await expect(page.locator("#drawer[data-drawer=activity]")).to_be_visible()
        assert "drawer=activity" in page.url
        for name in VIEWS:
            await page.locator(f"#main-nav a[data-page={name}]").click()
            await on_page(page, name)
            await expect(page.locator("#drawer[data-drawer=activity]")).to_be_visible()
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
                await expect(page.locator("#panel")).to_be_hidden()
                await expect(page.locator("#drawer")).to_be_hidden()
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
            await expect(page.locator("#panel")).to_be_hidden()
        # The pages that remain are the only ones with a heading of their own.
        for name in PAGES:
            await w.go(f"#/{name}")
            await on_page(page, name)
        assert w.problems == [], w.problems


# -- the search box and the Activity button are on every view -----------------------------------


async def test_the_search_box_is_on_every_view_and_opens_the_search_drawer_with_those_words(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Searchable {word}")
    story = await made(client, "story", f"Findable story {word}", epic)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        for name in VIEWS:
            await w.open(f"#/{name}")
            await on_page(page, name)
            box = page.locator("#global-search")
            await expect(box).to_be_visible()
            assert await page.locator("#global-search-form").get_attribute("role") == "search"
            await expect(page.locator("#drawer")).to_be_hidden()
            await box.fill(f"findable story {word}")
            await box.press("Enter")
            drawer = page.locator("#drawer[data-drawer=search]")
            await expect(drawer).to_be_visible(timeout=SLOW)
            await expect(page.locator("#search-box")).to_have_value(f"findable story {word}")
            assert "drawer=search" in page.url and "search=" in page.url
            assert page.url.split("#")[1].startswith(f"/{name}")
            # The words are found, with how they matched, and a result opens the ordinary detail
            # panel on this view without closing the drawer.
            row = page.locator(f"#search-results li.result[data-type=story][data-ref='{story}']")
            await expect(row).to_be_visible(timeout=SLOW)
            assert await row.get_attribute("data-matched") in {"words", "both"}
            await row.locator("a.result-title").click()
            await expect(page.locator("#card-title")).to_contain_text(
                f"{code_of(story)} ", timeout=SLOW
            )
            assert f"sel={story}" in page.url
            assert page.url.split("#")[1].startswith(f"/{name}")
            await expect(drawer).to_be_visible()
            await page.locator("#drawer-close").click()
            await expect(drawer).to_be_hidden()
            assert "drawer=" not in page.url
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
        # Nothing was searched for, so no drawer opened.
        await expect(page.locator("#drawer")).to_be_hidden()
        assert w.problems == [], w.problems


async def test_the_activity_button_opens_the_drawer_from_every_view_and_works_from_the_keyboard(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Activity {word}")
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        for name in VIEWS:
            await w.open(f"#/{name}")
            await on_page(page, name)
            button = page.locator("#activity-open")
            await expect(button).to_be_visible()
            assert await button.get_attribute("aria-expanded") == "false"
            await button.click()
            drawer = page.locator("#drawer[data-drawer=activity]")
            await expect(drawer).to_be_visible(timeout=SLOW)
            await expect(page.locator("#drawer-title")).to_have_text("Activity")
            assert await button.get_attribute("aria-expanded") == "true"
            assert "drawer=activity" in page.url
            assert page.url.split("#")[1].startswith(f"/{name}")
            # The feed and who is working are in it, and the page behind it is still the view.
            await expect(page.locator("#feed li.event").first).to_be_visible(timeout=SLOW)
            await expect(page.locator("#working")).to_be_visible()
            await expect(page.locator("main#page h1").first).to_have_text(HEADINGS[name])
            await button.click()
            await expect(drawer).to_be_hidden()
            assert "drawer=" not in page.url

        # With the keyboard: Enter opens it and puts the focus in it, Escape closes it and gives
        # the focus back.
        await w.open("#/graph")
        await on_page(page, "graph")
        await page.locator("#activity-open").focus()
        await page.keyboard.press("Enter")
        await expect(page.locator("#drawer[data-drawer=activity]")).to_be_visible(timeout=SLOW)
        await expect(page.locator("#drawer-title")).to_be_focused(timeout=SLOW)
        await page.keyboard.press("Escape")
        await expect(page.locator("#drawer")).to_be_hidden()
        await expect(page.locator("#activity-open")).to_be_focused()
        # Opened by address, the same drawer shows the newest work first.
        await w.go("#/roadmap?drawer=activity")
        await expect(
            page.locator("#feed li.event", has_text=f"Activity {word}").first
        ).to_be_visible(timeout=SLOW)
        assert epic.startswith("epic:")
        assert w.problems == [], w.problems


# -- a draft is never discarded -----------------------------------------------------------------


async def test_a_draft_in_the_panel_survives_opening_drawers_and_changing_views(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Drafted {word}")
    story = await made(client, "story", f"Story {word}", epic)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/board?epic={number_of(epic)}&sel={story}")
        await expect(page.locator("#card-title")).to_contain_text(
            f"{code_of(story)} ", timeout=SLOW
        )
        await page.locator("#edit-title").fill(f"My draft {word}")
        await page.locator("#edit-body").fill(f"Half a thought {word}")
        await page.evaluate("window.__panel = document.getElementById('edit-title'); true")

        async def draft_is_kept() -> None:
            assert await page.locator("#edit-title").input_value() == f"My draft {word}"
            assert await page.locator("#edit-body").input_value() == f"Half a thought {word}"
            assert await page.evaluate("document.getElementById('edit-title') === window.__panel")

        # A drawer opens and closes, with search, and again with activity.
        await page.locator("#global-search").fill(word)
        await page.locator("#global-search-go").click()
        await expect(page.locator("#search-results li.result").first).to_be_visible(timeout=SLOW)
        await draft_is_kept()
        await page.locator("#activity-open").click()
        await expect(page.locator("#drawer[data-drawer=activity]")).to_be_visible()
        await draft_is_kept()
        await page.locator("#drawer-close").click()
        await draft_is_kept()
        # The views change, and Graph's presentation and preset too: the selection goes with the
        # person, and so does what was typed.
        for name in ("graph", "roadmap", "board", "graph"):
            await page.locator(f"#main-nav a[data-page={name}]").click()
            await on_page(page, name)
            assert f"sel={story}" in page.url
            await draft_is_kept()
        await page.locator("#graph-as-list").click()
        await page.locator("#graph-view-everything").click()
        await expect(page.locator("#graph-page")).to_have_attribute("data-as", "list")
        await expect(page.locator("#graph-page")).to_have_attribute("data-view", "everything")
        assert "as=list" in page.url and f"sel={story}" in page.url
        await draft_is_kept()
        # Nothing was saved by any of it.
        assert (await ok(client, "get_node", node=story))["version"] == 1
        assert w.problems == [], w.problems


# -- project switching --------------------------------------------------------------------------


async def test_switching_project_with_late_answers_shows_nothing_of_the_first_in_any_view() -> None:
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
        await ok(one, "retain_fact", actor="a", text=f"A first fact {word}", source="The first")
        await made(two, "epic", f"Only in the second {word}")

        async with browser_page(first.address, token=first.key, name=None, keep_bodies=True) as w:
            page = w.page
            await w.open(f"#/board?drawer=search&search=first+{word}&sel={story_one}")
            await expect(page.locator("#card-title")).to_contain_text(
                f"{code_of(story_one)} ", timeout=SLOW
            )
            await expect(page.locator("#search-results li.result").first).to_be_visible(
                timeout=SLOW
            )
            # From now on the first project's answers (the poll, the lists, the search and the
            # panel's) are held back, and an agent changes the first project so that there is
            # news to hold back.
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
            # The first project's drawer, panel and search did not come along.
            await expect(page.locator("#drawer")).to_be_hidden()
            await expect(page.locator("#panel")).to_be_hidden()
            released.set()

            # Every view, the drawers and the panel: nothing of the first project, however late
            # its answers are.
            for address in (
                "#/board",
                "#/graph?view=everything",
                "#/graph?view=context&as=list",
                "#/roadmap",
                "#/board?drawer=activity",
                f"#/board?drawer=search&search=first+{word}",
                f"#/board?sel={story_one}",
            ):
                await w.go(address)
                await asyncio.sleep(1)
                shown = await page.locator("body").inner_text()
                for gone in (f"Only in the first {word}", f"News for the first {word}"):
                    assert gone not in shown, (address, gone)
                assert f"A first fact {word}" not in shown and f"First story {word}" not in shown
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
                    f"A first fact {word}",
                ):
                    assert gone not in text, gone


async def test_two_choices_made_one_right_after_the_other_both_reach_the_address(
    board: str, client: Client
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Choices {word}")
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/graph")
        await expect(page.locator("#graph-page")).to_be_visible(timeout=SLOW)
        # No pause between the three changes: the address is handled a moment after it is set,
        # and each change has to build on the one before it, not on the address as it was.
        await page.locator("#graph-view-context").click()
        await page.locator("#graph-as-list").click()
        await page.locator("#graph-epic").select_option(str(number_of(epic)))
        await expect(page.locator("#graph-page")).to_have_attribute("data-view", "context")
        await expect(page.locator("#graph-page")).to_have_attribute("data-as", "list")
        address = await page.evaluate("location.hash")
        assert "view=context" in address and "as=list" in address
        assert f"epic={number_of(epic)}" in address
