"""M16's proof 6 against the running containers, as M20 has it: driven in a real headless browser
and checked against a real MCP client (see ./check.sh). Activity, and the Tools and Status settings
pages, are opened with the keyboard alone, and their feed, claims, tool definitions, health and
index data and export match MCP; the addresses of the pages (Search with its filters and Possible
duplicates, Activity, the Graph, the Board, the Roadmap and a record's page) reopen the state they
name in another browser, and an old address with a `sel`, `panel` or `drawer` in it opens the page
without them; a draft typed on a page is not lost by what does not leave the page; and a connection
failure is told on the page the person is on, not only in the settings pages. There is no panel and
no drawer: every one of these is a page. The details of each page are in the tests of that page
(test_board_activity_integration.py and the others); this is the whole, walked."""

import json
import re
from pathlib import Path

import pytest
from mcp import Client
from playwright.async_api import Page, Route, expect

from tests.board_context import ADA, fresh_project
from tests.board_helpers import (
    SLOW,
    board_call,
    board_url,
    browser_page,
    cards_are,
    go,
    made,
    no_panel_or_drawer,
    open_editor,
)
from tests.board_m14 import events_of, feed_ids, ids_of, spoken
from tests.board_search import (
    address,
    asked,
    keyboard_to_activity,
    on_view,
    open_settings,
    result_rows,
    shown_triples,
    triples,
)
from tests.helpers import (
    connect_each,
    export_everything,
    make_note,
    number_of,
    ok,
    reindexed,
    the_token,
    unique_word,
)
from tests.world import join_project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NEWS = 15_000


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


async def claims_on_page(page: Page, section: str) -> set[tuple[str, str]]:
    rows: list[list[str]] = await page.locator(f"#{section} li.claim").evaluate_all(
        "els => els.map(e => [e.dataset.node, e.dataset.actor])"
    )
    return {(node, who) for node, who in rows}


async def to_activity_with_the_keyboard(page: Page) -> None:
    """Go to the Activity page with the keyboard alone (the sidebar's link, reached from the
    Settings button), and see that it is a page: the sidebar marks it, nothing opens beside it."""
    await keyboard_to_activity(page)
    await expect(page.locator("#activity-open")).to_have_attribute("aria-current", "page")
    await no_panel_or_drawer(page)


# -- Activity, Tools and Status with the keyboard only, and what they show is MCP's ----------


async def test_activity_tools_and_status_open_with_the_keyboard_and_match_mcp(
    client: Client, board: str, tmp_path: Path
) -> None:
    word = unique_word()
    agent, finisher, ann = f"agent-{word}", f"finisher-{word}", f"ann-{word}"
    epic = await made(client, "epic", f"Everything {word}")
    story = await made(client, "story", f"Story {word}", epic)
    held, finished, loose = [
        await made(client, "task", f"{name} {word}", story)
        for name in ("Held", "Finished", "Loose")
    ]
    note = f"Done for {word}.\nNothing is left."
    async with connect_each(agent, finisher, ann) as (by_agent, by_finisher, by_ann):
        await ok(by_agent, "claim_node", node=held, lease_seconds=600)
        await ok(by_finisher, "claim_node", node=finished)
        await ok(by_finisher, "finish_work", node=finished, outcome="done", note=note)
        await ok(by_ann, "claim_node", node=loose, lease_seconds=900)
    await make_note(client, f"Decided {word}", decided_by="Marie", nodes=[story])
    await make_note(client, f"Known {word}", nodes=[story])
    await reindexed(client)

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/roadmap")
        # Activity: the feed is the server's newest changes, newest first, a handoff note is shown
        # in full, and who is working on what is the server's own claims.
        await to_activity_with_the_keyboard(page)
        newest = (await ok(client, "changes_since", newest=True, limit=100))["items"]
        wanted = [e["id"] for e in reversed(newest)]
        await expect(page.locator("#feed li.event")).to_have_count(len(wanted), timeout=SLOW)
        assert await feed_ids(page) == wanted
        handoff = page.locator("#feed li.event[data-action=handoff]", has_text=finisher)
        assert spoken(await handoff.locator(".handoff-note").inner_text()) == spoken(note)
        who = (await ok(client, "who_is_working"))["agents"]
        live = {(m["node"], a["actor"]) for a in who for m in a["holding"]}
        assert {(held, agent), (loose, ann)} <= live
        assert await claims_on_page(page, "claims-live") == live
        lapsed = {(m["node"], a["actor"]) for a in who for m in a["lapsed"]}
        assert not lapsed or await claims_on_page(page, "claims-lapsed") == lapsed
        page_agents: list[list[str]] = await page.locator("#agents li.agent").evaluate_all(
            "els => els.map(e => [e.dataset.actor, e.dataset.state])"
        )
        assert sorted(page_agents) == sorted([a["actor"], a["state"]] for a in who)
        # It is a page like the others: nothing is left to close, and the way back is the
        # browser's, to the view the person came from.
        await page.keyboard.press("Escape")
        await expect(page.locator("#feed li.event").first).to_be_visible()
        await page.go_back()
        await on_view(page, "roadmap")

        # Tools: through the Settings menu, the server's own tools: every tool a key can have, as
        # an agent lists them, and the two that only a person can use, which the page lists too.
        await open_settings(page, "tools")
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        names: list[str] = await page.locator("#tool-list article.tool").evaluate_all(
            "els => els.map(e => e.dataset.name)"
        )
        people_only = {"set_confirmation", "set_definition_of_done"}
        assert sorted(names) == sorted({*tools, *people_only}) and len(names) == 47
        for name in ("search", "find_overlaps", "add_note"):
            entry = page.locator(f"#tool-entry-{name}")
            assert spoken(await entry.locator("p.first").inner_text()) == spoken(
                (tools[name].description or "").split("\n\n")[0]
            )
        # Activity is reached from a settings page by the same keys, and is the same page.
        await to_activity_with_the_keyboard(page)
        assert await feed_ids(page) == wanted
        await page.go_back()
        await expect(page).to_have_url(re.compile(r"#/tools$"), timeout=SLOW)
        await expect(page.locator("#tool-list article.tool").first).to_be_visible(timeout=SLOW)

        # Status: the health, the sizes and the index, and the export, which is MCP's.
        await open_settings(page, "status")
        await expect(page.locator("#versions")).to_be_visible(timeout=SLOW)
        status = await ok(client, "server_status")
        await expect(page.locator("#versions .ok")).to_contain_text("can reach its database")
        for name in ("vrum_version", "postgres_version", "pgvector_version"):
            row = page.locator(f"#status-body tr[data-name={name}] .value")
            assert (await row.inner_text()).strip() == status[name]
        for name, count in status["counts"].items():
            row = page.locator(f"#status-body tr[data-name={name}] .value")
            assert (await row.inner_text()).strip() == str(count), name
        index = status["index"]
        assert index["embedding_service"] == "answering" and index["waiting_to_index"] == 0
        for name, value in (("service", "Answering"), ("waiting", "0"), ("meaning", "In use")):
            row = page.locator(f"#status-body tr[data-name={name}] .value")
            assert (await row.inner_text()).strip() == value
        await page.locator("#download-export").focus()
        async with page.expect_download() as waiting:
            await page.keyboard.press("Enter")
        download = await waiting.value
        path = tmp_path / "export.json"
        await download.save_as(path)
        assert json.loads(path.read_text(encoding="utf-8")) == await export_everything(client)
        assert w.problems == []


# -- an address reopens the state it names ---------------------------------------------------------


async def test_search_and_view_addresses_reopen_the_selected_state_in_another_browser(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Mapped {word}")
    story = await made(client, "story", f"Story {word}", epic)
    await made(client, "story", f"Elsewhere {word}", await made(client, "epic", f"Other {word}"))
    await ok(client, "create_rule", title=f"Rule {word}", node=story)
    note = await make_note(client, f"Note {word}", nodes=[story])
    await reindexed(client)
    key = f"note:{note['id']}"
    scope = number_of(epic)
    wanted_notes = await asked(client, word, "both", types=["note"])
    assert triples(wanted_notes["items"]) == [("note", key, "both")]

    # The state is made with the board's own controls: the Lists of notes for one epic...
    async with browser_page(board, token=the_token()) as first:
        page = first.page
        await first.open("#/lists")
        await page.locator("#lists-view-notes").click()
        await page.locator("#lists-epic").select_option(str(scope))
        name, choices = await address(page)
        assert name == "lists" and choices == {"view": "notes", "epic": str(scope)}
        # ... then a search from the top bar, which is a page of its own ...
        await page.locator("#global-search").fill(word)
        await page.keyboard.press("Enter")
        await result_rows(page, 6)
        assert await address(page) == ("search", {"search": word})
        await page.locator("#search-facets [data-facet=type] button[data-value=note]").click()
        await result_rows(page, 1)
        search_url = page.url
        # ... and the record that was found, on its own page, reached from Search.
        await page.locator("#search-results tr.result a.result-title").click()
        await expect(page.locator(f"#item-page[data-ref='{key}']")).to_be_visible(timeout=SLOW)
        await expect(page.locator(f"article.record-detail[data-record='{key}']")).to_be_visible(
            timeout=SLOW
        )
        url = page.url
        assert await address(page) == ("item", {"item": key, "from": "search"})
        await no_panel_or_drawer(page)
        # The browser's history walks back through them and forward again.
        await page.go_back()
        await expect(page.locator("#search-results tr.result")).to_have_count(1, timeout=SLOW)
        assert page.url == search_url
        await page.go_forward()
        await expect(page.locator(f"#item-page[data-ref='{key}']")).to_be_visible(timeout=SLOW)
        assert page.url == url
        assert first.problems == []

    # ... and the same addresses in another browser are the same states.
    async with browser_page(board, token=the_token()) as second:
        page = second.page
        # Search: the words, the filter and the results the server gives.
        await second.open(search_url.removeprefix(board))
        await expect(page.locator("#search-box")).to_have_value(word, timeout=SLOW)
        await expect(
            page.locator("#search-facets [data-facet=type] button[aria-pressed=true]")
        ).to_have_attribute("data-value", "note")
        rows = await result_rows(page, 1)
        assert shown_triples(rows) == triples(wanted_notes["items"])
        # The record from it, and the way back to the search as it was left (filter and all).
        await page.locator("#search-results tr.result a.result-title").click()
        await expect(page.locator(f"#item-page[data-ref='{key}']")).to_be_visible(timeout=SLOW)
        await expect(page.locator("nav.crumbs .crumb-view a")).to_have_text("Search")
        await expect(page.locator("#crumb-page")).to_have_text(f"N-{note['id']}")
        await page.locator("nav.crumbs .crumb-view a").click()
        await expect(page.locator("#search-box")).to_have_value(word, timeout=SLOW)
        assert page.url == search_url
        # The record's own address, loaded afresh, is in Search too, and its way back is Search.
        await go(page, board, url.removeprefix(board))
        await expect(page.locator(f"#item-page[data-ref='{key}']")).to_be_visible(timeout=SLOW)
        crumb = page.locator("nav.crumbs .crumb-view a")
        await expect(crumb).to_have_text("Search")
        assert (await crumb.get_attribute("href")) == "#/search"

        # The Lists: the kind of list and the epic.
        await second.open(f"#/lists?view=notes&epic={scope}")
        await expect(page.locator("#lists-page")).to_have_attribute("data-view", "notes")
        await expect(page.locator("#lists-view-notes")).to_have_attribute("aria-pressed", "true")
        await expect(page.locator("#lists-epic")).to_have_value(str(scope))
        await expect(page.locator(f"#ctx-notes-list tr[data-ref='{key}']")).to_be_visible(
            timeout=SLOW
        )

        # The Graph: the way of looking and what is looked at. The Around tab holds the item in
        # the middle and what is joined to it around it.
        await second.open(f"#/graph?tab=around&focus={story}")
        await expect(page.locator("#graph-view")).to_have_attribute("data-tab", "around")
        await expect(page.locator("#graph-tab-around")).to_have_attribute("aria-selected", "true")
        await expect(page.locator("#graph-hub")).to_have_attribute("data-ref", story, timeout=SLOW)
        await expect(
            page.locator(f".graph-around button.graph-node[data-ref='{key}']")
        ).to_be_visible(timeout=SLOW)
        # Another tab keeps what is looked at, and forgets the choices of the one it left.
        await page.locator("#graph-tab-order").click()
        await expect(page.locator("#graph-view")).to_have_attribute("data-tab", "order")
        choices = (await address(page))[1]
        assert choices["tab"] == "order" and choices["focus"] == story
        await no_panel_or_drawer(page)
        assert second.problems == []

    # The Board holds its scope and filter in the address. A `sel` or a `drawer` in an old address
    # means nothing: the page opens without them, and the address says so.
    stories = await ids_of(client, f"kind:story epic:{scope}")
    assert stories == [story]
    old_board = (
        f"#/board?epic={scope}&q=kind:story&sel={story}&drawer=activity&panel=coverage"
        f"&act_actor=agent-1"
    )
    roadmap_address = f"#/roadmap?q=epic:{scope}&zoom=quarter&sel={key}&drawer=search"
    async with browser_page(board, token=the_token()) as third:
        page = third.page
        await third.open(old_board)
        await expect(page.locator("#epic-filter")).to_have_value(str(scope), timeout=SLOW)
        await expect(page.locator("#filter")).to_have_value("kind:story")
        await cards_are(page, set(stories))
        await on_view(page, "board")
        assert page.url.endswith(f"#/board?epic={scope}&q=kind:story&act_actor=agent-1")
        await no_panel_or_drawer(page)
        # A card in it is a link to its page, and nothing is "selected" on the Board.
        assert await page.locator(".card.selected, .lane.selected").count() == 0

        await third.go(roadmap_address)
        await on_view(page, "roadmap")
        await expect(page.locator("#filter")).to_have_value(f"epic:{scope}", timeout=SLOW)
        await expect(page.locator(".rm-track")).to_have_attribute(
            "data-zoom", "quarter", timeout=SLOW
        )
        choices = (await address(page))[1]
        assert choices == {"q": f"epic:{scope}", "zoom": "quarter"}
        await no_panel_or_drawer(page)

        # Possible duplicates and Activity have their own pages and their own choices.
        await third.go(f"#/search?dups=1&dup_min=0.95&dup_epics={scope}")
        await expect(page.locator("#search-tab-dups")).to_have_attribute(
            "aria-pressed", "true", timeout=SLOW
        )
        await expect(page.locator("#dup-min")).to_have_value("0.95")
        await expect(page.locator("#dup-epics")).to_have_value(str(scope))
        await third.go("#/activity?act_actor=agent-1&act_kind=notes")
        await expect(page.locator("#a-actor")).to_have_value("agent-1", timeout=SLOW)
        await expect(page.locator("#a-kind")).to_have_value("notes")
        assert third.problems == []

    # A fresh load of the Roadmap's address in yet another browser says the same.
    async with browser_page(board, token=the_token()) as fourth:
        await fourth.page.goto(f"{board}{roadmap_address}")
        await fourth.page.wait_for_selector(".rm-track", timeout=SLOW)
        await expect(fourth.page.locator("#filter")).to_have_value(f"epic:{scope}")
        await expect(fourth.page.locator(".rm-track")).to_have_attribute("data-zoom", "quarter")
        assert (await address(fourth.page))[1] == {"q": f"epic:{scope}", "zoom": "quarter"}
        assert fourth.problems == []


async def test_a_note_looked_at_in_the_graph_opens_its_page_from_the_graph(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Noted {word}")
    story = await made(client, "story", f"Story {word}", epic)
    note = await make_note(client, f"Looked at {word}", nodes=[story])
    key = f"note:{note['id']}"
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/graph?tab=around&focus={key}")
        await expect(page.locator("#graph-hub")).to_have_attribute("data-ref", key, timeout=SLOW)
        await expect(
            page.locator(f".graph-around button.graph-node[data-ref='{story}']")
        ).to_be_visible(timeout=SLOW)
        await page.locator("#graph-hub-open").click()
        await expect(page.locator(f"#item-page[data-ref='{key}']")).to_be_visible(timeout=SLOW)
        assert await address(page) == ("item", {"item": key, "from": "graph"})
        await expect(page.locator("nav.crumbs .crumb-view a")).to_have_text("Graph")
        await no_panel_or_drawer(page)
        assert w.problems == []


async def test_an_item_with_nothing_joined_to_it_is_looked_at_in_the_graph(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Alone {word}")
    story = await made(client, "story", f"Alone story {word}", epic)
    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/graph?tab=around&focus={story}")
        await expect(w.page.locator("#graph-hub")).to_have_attribute(
            "data-ref", story, timeout=SLOW
        )
        assert w.problems == []


# -- a draft is not lost ------------------------------------------------------------------------


async def test_a_draft_survives_everything_that_does_not_leave_the_page(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Draft {word}")
    story = await made(client, "story", f"Story {word}", epic)
    await made(client, "story", f"Neighbour {word}", epic)
    draft = f"Typed on the page and not saved {word}\nwith [ and ] in it"
    unasked = f"typed in a search box {word}"
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/item?item={story}&from=graph")
        await expect(page.locator("#item-page")).to_have_attribute("data-ref", story, timeout=SLOW)
        await expect(page.locator("#item-position")).to_have_text("1 of 2", timeout=SLOW)
        await open_editor(page)
        await page.locator("#edit-body").fill(draft)
        await page.evaluate("window.__body = document.querySelector('#edit-body')")
        item_url = page.url

        async def kept(step: str) -> None:
            assert await page.evaluate("document.querySelector('#edit-body') === window.__body"), (
                step
            )
            assert await page.locator("#edit-body").input_value() == draft, step
            assert page.url == item_url, step
            await no_panel_or_drawer(page)

        # The quick switcher opens over the page and closes again.
        await page.keyboard.press("Control+k")
        await expect(page.locator("#switcher")).to_be_visible()
        await page.locator("#switcher-input").fill("Neigh")
        await page.keyboard.press("Escape")
        await expect(page.locator("#switcher")).to_have_count(0)
        await kept("the quick switcher")
        # Words typed in the top bar's box and not sent are a draft too.
        await page.locator("#global-search").fill(unasked)
        await kept("the top bar's search box")
        # The sidebar's tree is opened and closed (it holds this item's own epic open).
        branch = page.locator(f"#epic-list li[data-key='{epic}']")
        await expect(branch).to_have_attribute("aria-expanded", "true")
        await branch.locator("button.tree-twisty").first.click()
        await expect(branch).to_have_attribute("aria-expanded", "false")
        await kept("closing a branch of the tree")
        await branch.locator("button.tree-twisty").first.click()
        await expect(branch).to_have_attribute("aria-expanded", "true")
        await kept("opening a branch of the tree")
        # The section list jumps within the page.
        await page.locator(".item-jump[data-jump=history]").click()
        await kept("the section list")
        # `[` and `]` go to the neighbours only when nobody is typing: typed in the draft they are
        # text, and the page stays.
        await page.locator("#edit-body").press("Control+End")
        await page.keyboard.press("]")
        await page.keyboard.press("[")
        draft += "]["
        await kept("brackets typed in the draft")
        # An agent makes another story in the epic: the page's pager and the tree are drawn again
        # (it is "1 of 3" now), and the draft is not touched, and nothing is offered over it.
        await made(client, "story", f"Third {word}", epic)
        await expect(page.locator("#item-position")).to_have_text("1 of 3", timeout=NEWS)
        await expect(
            page.locator("#epic-list a.tree-link", has_text=f"Third {word}")
        ).to_have_count(1, timeout=SLOW)
        assert await page.locator("#item-details .notice.changed").count() == 0
        await kept("news about another item")
        assert await page.locator("#global-search").input_value() == unasked
        # The draft is saved as it was typed.
        await page.get_by_role("button", name="Save changes").click()
        await expect(page.locator("#item-details")).to_contain_text(
            f"Typed on the page and not saved {word}", timeout=SLOW
        )
        assert (await ok(client, "get_node", node=story))["body"] == draft
        assert w.problems == []


async def test_what_is_typed_on_a_page_and_what_a_view_keeps_stay_while_the_page_stays(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Kept {word}")
    story = await made(client, "story", f"Kept story {word}", epic)
    await made(client, "story", f"Other kept story {word}", epic)
    await make_note(client, f"Kept note {word}", nodes=[story])
    unasked = f"typed in the search box {word}"
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        # The Search page: words typed and not asked stay when a filter changes the page.
        await w.open(f"#/search?search={word}")
        await result_rows(page, 4)
        await page.locator("#search-box").fill(unasked)
        await page.locator("#search-facets [data-facet=type] button[data-value=story]").click()
        await result_rows(page, 2)
        await expect(
            page.locator("#search-facets [data-facet=type] button[aria-pressed=true]")
        ).to_have_attribute("data-value", "story")
        assert await page.locator("#search-box").input_value() == unasked

        # The Graph keeps what is looked at through its ways of looking.
        await w.go(f"#/graph?tab=around&focus={story}")
        await expect(page.locator("#graph-hub")).to_have_attribute("data-ref", story, timeout=SLOW)
        for tab in ("order", "structure", "around"):
            await page.locator(f"#graph-tab-{tab}").click()
            await expect(page.locator(f"#graph-tab-{tab}")).to_have_attribute(
                "aria-selected", "true"
            )
            await expect(page.locator("#graph-view")).to_have_attribute("data-tab", tab)
            assert (await address(page))[1]["focus"] == story, tab
        await expect(page.locator("#graph-hub")).to_have_attribute("data-ref", story, timeout=SLOW)

        # From view to view the filter and the epic go along, and nothing else does.
        await w.go(f"#/board?epic={number_of(epic)}&q=kind:story")
        await on_view(page, "board")
        for view in ("roadmap", "lists", "graph", "board"):
            await page.locator(f"#main-nav a[data-page={view}]").click()
            await on_view(page, view)
            choices = (await address(page))[1]
            assert choices["q"] == "kind:story" and choices["epic"] == str(number_of(epic)), view
            assert "sel" not in choices and "focus" not in choices, view
            await no_panel_or_drawer(page)
        assert w.problems == []


# -- a connection failure is told where the person is -------------------------------------------


async def test_a_connection_failure_is_told_in_the_current_view_and_not_only_in_settings(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Down {word}")
    await made(client, "story", f"Story {word}", epic)
    poll = board_call(board, "GET", "/board/api/info").body["poll_seconds"]
    wait = int((3 * poll + 5) * 1000)

    async def unreachable(route: Route) -> None:
        await route.abort()

    async def broken(route: Route) -> None:
        await route.fulfill(status=502, body="Bad gateway")

    changes = "**/board/api/projects/*/tool/changes_since"
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        note, chip = page.locator("#connection-note"), page.locator("#connection")
        await w.open("#/board")
        await expect(page.locator(f".lane[data-ref='{epic}']")).to_be_visible(timeout=SLOW)
        await expect(note).to_be_hidden()
        await expect(chip).to_be_hidden()

        for failing in (unreachable, broken):
            await page.route(changes, failing)
            # The Board says so itself, above the page, while what it drew stays where it was.
            await expect(note).to_be_visible(timeout=wait)
            await expect(note).to_contain_text("cannot reach the server")
            await expect(chip).to_be_visible()
            await expect(chip).to_have_text("Cannot reach the server: trying again")
            assert await note.get_attribute("role") == "alert"
            above, main = await note.bounding_box(), await page.locator("#page").bounding_box()
            assert above and main and above["y"] + above["height"] <= main["y"] + 1
            await expect(page.locator(f".lane[data-ref='{epic}']")).to_be_visible()

            # It stays when the person goes to another view, to Activity, to Search, to the page
            # of a record and to a settings page: each is a page, and the words are above all.
            for view, marker in (("graph", "#graph-explore"), ("roadmap", ".rm-track")):
                await page.locator(f"#main-nav a[data-page={view}]").click()
                await on_view(page, view)
                await expect(page.locator(marker)).to_be_visible(timeout=SLOW)
                await expect(note).to_be_visible()
                await expect(chip).to_be_visible()
            for hash_, marker in (
                ("#/activity", "#feed-section"),
                (f"#/search?search={word}", "#search-box"),
                (f"#/item?item={epic}&from=roadmap", "#item-page"),
            ):
                await w.go(hash_)
                await expect(page.locator(marker)).to_be_visible(timeout=SLOW)
                await expect(note).to_be_visible()
                await expect(chip).to_be_visible()
            await open_settings(page, "status")
            await expect(page.locator("#versions")).to_be_visible(timeout=SLOW)
            await expect(note).to_be_visible()
            await expect(chip).to_be_visible()

            # When the server answers again the words go away, wherever the person is.
            await page.unroute(changes, failing)
            await expect(note).to_be_hidden(timeout=wait)
            await expect(chip).to_be_hidden()
            await w.go("#/board")
            await on_view(page, "board")

        # Something the person does fails in the place where they do it, in plain words: a search
        # on the Search page, and a new item on the Board.
        everything = "**/board/api/projects/*/tool/*"
        await page.route(everything, unreachable)
        await page.locator("#global-search").fill(word)
        await page.keyboard.press("Enter")
        await expect(page.locator("#search-problem")).to_contain_text(
            "The board cannot reach the server", timeout=SLOW
        )
        await w.go("#/board")
        await page.locator("#new-item").click()
        dialog = page.locator("dialog.modal")
        await dialog.locator("#new-title").fill(f"Not made {word}")
        await dialog.get_by_role("button", name="Create").click()
        await expect(dialog.locator("form > .problem")).to_contain_text(
            "The board cannot reach the server", timeout=SLOW
        )
        await dialog.get_by_role("button", name="Cancel").click()
        await page.unroute(everything, unreachable)
        assert await ids_of(client, f"Not made {word}") == []
        # Only the failed loads that were meant are in the browser's log.
        assert [p for p in w.problems if "Failed to load resource" not in p] == []
        # Nothing was lost on the server meanwhile: it is still all there.
        assert (await events_of(client, node=epic))[0]["action"] == "create"


# -- the definition of done: one text for the project, set in Settings, read on every story and task


async def test_the_definition_of_done_is_set_in_settings_and_read_on_every_story_and_task(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Defined")
        story = await made(client, "story", "A story", epic)
        task = await made(client, "task", "A task", story)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            # Nothing is written: the page is an empty field with the way to fill it, in the
            # Settings menu beside Users, Tools and Status, and so is the section of a task.
            await go(page, board, "#/definition")
            await expect(page.locator("#definition-field .details-empty")).to_contain_text(
                "Empty", timeout=SLOW
            )
            await expect(page.locator("#add-definition")).to_have_text("Add a definition of done")
            await page.locator("#settings-menu").click()
            assert await page.locator("#settings-list a").all_inner_texts() == [
                "Users",
                "Definition of done",
                "Tools",
                "Status and export",
            ]
            await go(page, board, f"#/item?item={task}")
            part = page.locator("#item-page [data-part=definition]")
            await expect(part.locator(".details-empty")).to_be_visible(timeout=SLOW)
            await expect(part.get_by_role("link", name="Set it in Settings")).to_have_attribute(
                "href", "#/definition"
            )
            await expect(page.locator(".item-jump[data-jump=definition]")).to_contain_text("empty")
            # An epic holds neither acceptance criteria nor the definition of done.
            await go(page, board, f"#/item?item={epic}")
            await expect(page.locator("#item-page[data-kind=epic]")).to_be_visible(timeout=SLOW)
            assert await page.locator("#item-page [data-part=definition]").count() == 0

            # Writing it: the first save makes version 2, says who changed it, and its list lines
            # are its checks (counted as they are typed, and with the ticks each has).
            await go(page, board, "#/definition")
            await page.locator("#add-definition").click()
            await expect(page.locator("#definition-body")).to_be_focused()
            await page.get_by_role("button", name="Save changes").click()
            await expect(page.locator(".definition-form .problem-text")).to_contain_text(
                "Nothing to change", timeout=SLOW
            )
            await page.locator("#definition-body").fill("- Tests pass\n- A person can try it")
            await expect(page.locator("#definition-count")).to_have_text("2 checks")
            await page.get_by_role("button", name="Save changes").click()
            await expect(page.locator(".check-ticks-text")).to_have_text(
                ["Tests pass", "A person can try it"], timeout=SLOW
            )
            await expect(page.locator(".check-ticks-count")).to_have_text(["not ticked yet"] * 2)
            await expect(page.locator(".definition-meta")).to_contain_text(f"Last changed by {ADA}")
            saved = await ok(client, "get_definition_of_done")
            assert (saved["body"], saved["version"], saved["updated_by"]) == (
                "- Tests pass\n- A person can try it",
                2,
                ADA,
            )
            assert saved["updated_by_kind"] == "person"

            # Every story and task reads it as boxes to tick, and the way to change it is a link
            # to Settings.
            for ref in (story, task):
                await go(page, board, f"#/item?item={ref}")
                part = page.locator("#item-page [data-part=definition]")
                await expect(part.locator(".check-text")).to_have_text(
                    ["Tests pass", "A person can try it"], timeout=SLOW
                )
                assert await part.locator(".details-empty").count() == 0
                await expect(part.get_by_role("link", name="Edit in Settings")).to_have_attribute(
                    "href", "#/definition"
                )
                await expect(page.locator(".item-jump[data-jump=definition]")).to_contain_text(
                    "0/2"
                )
            await part.get_by_role("link", name="Edit in Settings").click()
            await expect(page.locator("#definition-page")).to_be_visible(timeout=SLOW)

            # Someone else changed it while this page was open for editing: the save is refused
            # in plain words, what was typed stays, and the current version can be loaded.
            await page.locator("#edit-definition").click()
            await page.locator("#definition-body").fill("- My own change")
            behind = project.owner.call(
                "POST",
                f"/board/api/projects/{project.id}/tool/set_definition_of_done",
                {"body": "- Changed behind your back", "expected_version": 2},
            )
            assert behind.status == 200, behind.body
            await page.get_by_role("button", name="Save changes").click()
            problem = page.locator(".definition-form .problem")
            await expect(problem).to_contain_text("version 3", timeout=SLOW)
            assert await page.locator("#definition-body").input_value() == "- My own change"
            assert (await ok(client, "get_definition_of_done"))[
                "body"
            ] == "- Changed behind your back"
            await problem.locator(".load-current").click()
            await expect(page.locator("#definition-body")).to_have_value(
                "- Changed behind your back", timeout=SLOW
            )
            await page.get_by_role("button", name="Cancel").click()
            await expect(page.locator(".check-ticks-text")).to_have_text(
                ["Changed behind your back"]
            )

            # The Activity page says it in plain words, with what changed shown whole.
            await go(page, board, "#/activity?act_kind=definition")
            rows = page.locator("li.event[data-action=definition_of_done_changed]")
            await expect(rows).to_have_count(2, timeout=SLOW)
            assert await page.locator("#feed li.event").count() == 2
            await expect(rows.first.locator(".event-words")).to_have_text(
                "changed the definition of done."
            )
            await expect(rows.first.locator(".event-actor")).to_contain_text(ADA)
            await rows.first.locator(".what-changed summary").click()
            body = rows.first.locator(".change-field[data-field=body]")
            await expect(body.locator(".before .change-text")).to_contain_text("Tests pass")
            await expect(body.locator(".after .change-text")).to_have_text(
                "- Changed behind your back"
            )
            assert w.problems == [] or all("Failed to load resource" in p for p in w.problems)

        # Someone who can only look reads it, and has nothing to change it with.
        viewer = join_project(project, "viewer")
        async with browser_page(board, token=None, person=viewer) as v:
            await go(v.page, board, "#/definition")
            await expect(v.page.locator(".check-ticks-text")).to_have_text(
                ["Changed behind your back"], timeout=SLOW
            )
            assert await v.page.locator("#edit-definition, #add-definition").count() == 0
            await go(v.page, board, f"#/item?item={task}")
            part = v.page.locator("#item-page [data-part=definition]")
            await expect(part.locator(".check-text")).to_have_text(
                ["Changed behind your back"], timeout=SLOW
            )
            assert await part.locator("input.check-box:disabled").count() == 1
            assert await part.locator(".check-how").count() == 0
            assert await part.get_by_role("link", name="Edit in Settings").count() == 0


async def test_what_the_notes_list_leaves_out_is_said_with_the_way_to_see_it(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Notes that ended")
        first = await make_note(client, "Docks answer in a second", "Measured", nodes=[epic])
        second = await make_note(client, "Docks answer in three", "Measured again", nodes=[epic])
        await make_note(client, "Riders prefer one tap", "Interviews", nodes=[epic])
        await ok(
            client,
            "supersede_note",
            replaces=[first["id"]],
            text="Docks answer in two",
            why="Averaged over the month",
        )
        await ok(client, "withdraw_note", note_ids=[second["id"]], reason="A bad reading")
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await go(page, board, "#/lists?view=notes")
            # The list opens on the notes in force; what that leaves out is one quiet line.
            await expect(page.locator("#ctx-notes-status")).to_have_value("in_force", timeout=SLOW)
            left_out = page.locator("#notes-left-out")
            await expect(left_out).to_have_text(
                "2 replaced or withdrawn notes are not shown. Show them.", timeout=SLOW
            )
            await expect(page.locator("#ctx-notes-list tr.record-line")).to_have_count(2)
            # It follows the words typed in the box: only what they would show counts.
            await page.locator("#ctx-notes-text").fill("three")
            await expect(left_out).to_have_text(
                "1 replaced or withdrawn note is not shown. Show it."
            )
            await page.locator("#ctx-notes-text").fill("")
            await left_out.get_by_role("button", name="Show them.").click()
            await expect(page.locator("#ctx-notes-status")).to_have_value("all")
            await expect(page.locator("#ctx-notes-list tr.record-line")).to_have_count(4)
            await expect(left_out).to_be_hidden()
            # With nothing left out there is no sentence at all.
            await page.locator("#ctx-notes-status").select_option("in_force")
            await expect(left_out).to_be_visible()
            await page.locator("#ctx-notes-text").fill("one tap")
            await expect(left_out).to_be_hidden()
            assert w.problems == [], w.problems
