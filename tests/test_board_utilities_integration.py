"""M16's proof 6 against the running containers, driven in a real headless browser and checked
against a real MCP client (see ./check.sh): Activity, and the Tools and Status settings pages, are
opened with the keyboard alone, and their feed, claims, tool definitions, health and index data and
export match MCP; the addresses of search and of the views reopen the selected state in another
browser; opening a drawer or switching presentations and views does not lose a draft; and a
connection failure is told in the view the person is on, not only in the settings pages. The
details of each page are in the tests of that page (test_board_activity_integration.py and the
others); this is the whole, walked."""

import json
import re
from pathlib import Path

import pytest
from mcp import Client
from playwright.async_api import Page, Route, expect

from tests.board_helpers import SLOW, board_call, board_url, browser_page, made
from tests.board_m14 import events_of, feed_ids, ids_of, spoken
from tests.board_search import (
    address,
    asked,
    drawer,
    on_view,
    open_settings,
    result_rows,
    shown_triples,
    triples,
)
from tests.helpers import (
    export_everything,
    make_decision,
    make_fact,
    ok,
    reindexed,
    the_token,
    unique_word,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NEWS = 15_000


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


async def keyboard_to_activity(page: Page) -> None:
    """Open the Activity drawer with the keyboard alone: Tab from the search button, then Enter."""
    await page.locator("#global-search-go").focus()
    await page.keyboard.press("Tab")
    assert await page.evaluate("document.activeElement.id") == "activity-open"
    await page.keyboard.press("Enter")
    await expect(drawer(page, "activity")).to_be_visible(timeout=SLOW)
    await expect(page.locator("#feed li.event").first).to_be_visible(timeout=SLOW)


async def claims_on_page(page: Page, section: str) -> set[tuple[int, str]]:
    rows: list[list[str]] = await page.locator(f"#{section} li.claim").evaluate_all(
        "els => els.map(e => [e.dataset.node, e.dataset.actor])"
    )
    return {(int(node), who) for node, who in rows}


# -- Activity, Tools and Status with the keyboard only, and what they show is MCP's ----------


async def test_activity_tools_and_status_open_with_the_keyboard_and_match_mcp(
    client: Client, board: str, tmp_path: Path
) -> None:
    word = unique_word()
    agent, finisher, ann = f"agent-{word}", f"finisher-{word}", f"Ann {word}"
    epic = await made(client, "epic", f"Everything {word}")
    story = await made(client, "story", f"Story {word}", epic)
    held, finished, loose = [
        await made(client, "task", f"{name} {word}", story)
        for name in ("Held", "Finished", "Loose")
    ]
    note = f"Done for {word}.\nNothing is left."
    await ok(client, "claim_node", actor=agent, node_id=held, lease_seconds=600)
    await ok(client, "claim_node", actor=finisher, node_id=finished)
    await ok(client, "finish_work", actor=finisher, node_id=finished, outcome="done", note=note)
    await ok(client, "claim_node", actor=ann, node_id=loose, lease_seconds=900)
    await make_decision(client, f"Decided {word}", node_ids=[story])
    await make_fact(client, f"Known {word}", node_ids=[story])
    await reindexed(client)

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/roadmap")
        # Activity: the feed is the server's newest changes, newest first, a handoff note is shown
        # in full, and who is working on what is the server's own claims.
        await keyboard_to_activity(page)
        newest = (await ok(client, "changes_since", newest=True, limit=100))["items"]
        wanted = [e["id"] for e in reversed(newest)]
        await expect(page.locator("#feed li.event")).to_have_count(len(wanted), timeout=SLOW)
        assert await feed_ids(page) == wanted
        handoff = page.locator("#feed li.event[data-action=handoff]", has_text=finisher)
        assert spoken(await handoff.locator(".handoff-note").inner_text()) == spoken(note)
        who = (await ok(client, "who_is_working"))["agents"]
        live = {(m["node_id"], a["actor"]) for a in who for m in a["holding"]}
        assert {(held, agent), (loose, ann)} <= live
        assert await claims_on_page(page, "claims-live") == live
        lapsed = {(m["node_id"], a["actor"]) for a in who for m in a["lapsed"]}
        assert not lapsed or await claims_on_page(page, "claims-lapsed") == lapsed
        page_agents: list[list[str]] = await page.locator("#agents li.agent").evaluate_all(
            "els => els.map(e => [e.dataset.actor, e.dataset.state])"
        )
        assert sorted(page_agents) == sorted([a["actor"], a["state"]] for a in who)
        await page.keyboard.press("Escape")
        await expect(page.locator("#drawer")).to_be_hidden()

        # Tools: through the Settings menu, with the same 53 tools the server lists.
        await open_settings(page, "tools")
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        names: list[str] = await page.locator("#tool-list article.tool").evaluate_all(
            "els => els.map(e => e.dataset.name)"
        )
        assert sorted(names) == sorted(tools) and len(names) == 53
        for name in ("search", "find_overlaps", "set_confirmation"):
            entry = page.locator(f"#tool-entry-{name}")
            assert spoken(await entry.locator("p.first").inner_text()) == spoken(
                (tools[name].description or "").split("\n\n")[0]
            )
        # Activity opens beside a settings page too, by the same keys.
        await keyboard_to_activity(page)
        assert await feed_ids(page) == wanted
        await page.keyboard.press("Escape")

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
    await ok(client, "create_rule", actor="a", title=f"Rule {word}", story_id=story)
    decision = await make_decision(client, f"Decision {word}", node_ids=[story])
    fact = await make_fact(client, f"Fact {word}", node_ids=[story])
    await reindexed(client)
    wanted_facts = await asked(client, word, "both", types=["fact"])
    assert triples(wanted_facts["items"]) == [("fact", fact["id"], "both")]
    key = f"fact:{fact['id']}"

    # The state is made with the board's own controls...
    async with browser_page(board, token=the_token()) as first:
        page = first.page
        await first.open("#/graph")
        await page.locator("#graph-view-context").click()
        await page.locator("#graph-as-list").click()
        await page.locator("#graph-epic").select_option(str(epic))
        await page.locator("#global-search").fill(word)
        await page.keyboard.press("Enter")
        await result_rows(page, 7)
        await page.locator("#search-facets [data-facet=type] button[data-value=fact]").click()
        await result_rows(page, 1)
        await page.locator("#search-results li.result a.result-title").click()
        await expect(page.locator(f"#panel .record-detail[data-record='{key}']")).to_be_visible(
            timeout=SLOW
        )
        url = page.url
        name, choices = await address(page)
        assert name == "graph" and choices == {
            "view": "context",
            "as": "list",
            "epic": str(epic),
            "drawer": "search",
            "search": word,
            "search_type": "fact",
            "sel": key,
        }
        # ... and the browser's history walks back through them and forward again.
        await page.go_back()
        await expect(page.locator("#panel")).to_be_hidden(timeout=SLOW)
        await page.go_forward()
        await expect(page.locator(f"#panel .record-detail[data-record='{key}']")).to_be_visible(
            timeout=SLOW
        )
        assert page.url == url
        assert first.problems == []

    # ... and the same address in another browser is the same state.
    async with browser_page(board, token=the_token()) as second:
        page = second.page
        await page.goto(url)
        await page.wait_for_selector("#graph-page", timeout=SLOW)
        await expect(page.locator("#graph-page")).to_have_attribute("data-view", "context")
        await expect(page.locator("#graph-page")).to_have_attribute("data-as", "list")
        await expect(page.locator("#graph-view-context")).to_have_attribute("aria-pressed", "true")
        await expect(page.locator("#graph-as-list")).to_have_attribute("aria-pressed", "true")
        await expect(page.locator("#graph-epic")).to_have_value(str(epic))
        await expect(page.locator(f"#panel .record-detail[data-record='{key}']")).to_be_visible(
            timeout=SLOW
        )
        await expect(page.locator(f".gv-record[data-key='{key}']")).to_have_attribute(
            "aria-current", "true", timeout=SLOW
        )
        await expect(drawer(page, "search")).to_be_visible()
        await expect(page.locator("#search-box")).to_have_value(word)
        await expect(page.locator("#search-facets [data-facet=type] button.on")).to_have_attribute(
            "data-value", "fact"
        )
        rows = await result_rows(page, 1)
        assert shown_triples(rows) == triples(wanted_facts["items"])
        assert rows[0]["label"] == wanted_facts["items"][0]["label"]
        # Switching to the map keeps the record and the scope.
        await page.locator("#graph-as-map").click()
        await expect(page.locator(f".dot[data-key='{key}']")).to_have_class(
            re.compile(r"\bchosen\b")
        )
        await expect(page.locator("#graph-epic")).to_have_value(str(epic))
        assert (await address(page))[1]["sel"] == key
        await expect(page.locator(f"#panel .record-detail[data-record='{key}']")).to_be_visible()
        assert second.problems == []

    # The Board and the Roadmap hold their scope, filter, selection and drawers in the address too.
    stories = await ids_of(client, f"kind:story epic:{epic}")
    assert stories == [story]
    board_address = (
        f"#/board?epic={epic}&q=kind:story&sel=node:{story}&drawer=activity&act_actor=agent-1"
        "&act_kind=decisions"
    )
    roadmap_address = (
        f"#/roadmap?q=epic:{epic}&zoom=quarter&sel=decision:{decision['id']}&drawer=search"
        f"&dups=1&dup_min=0.95&dup_epics={epic}"
    )
    async with browser_page(board, token=the_token()) as third:
        page = third.page
        await third.open(board_address)
        await expect(page.locator("#epic-filter")).to_have_value(str(epic), timeout=SLOW)
        await expect(page.locator("#filter")).to_have_value("kind:story")
        cards: list[int] = await page.locator(".card").evaluate_all(
            "els => els.map(e => Number(e.dataset.id))"
        )
        assert cards == stories
        await expect(page.locator("#panel #card-title")).to_contain_text(f"#{story} ")
        await expect(drawer(page, "activity")).to_be_visible()
        await expect(page.locator("#a-actor")).to_have_value("agent-1")
        await expect(page.locator("#a-kind")).to_have_value("decisions")
        await on_view(page, "board")

        await third.go(roadmap_address)
        await on_view(page, "roadmap")
        await expect(page.locator("#filter")).to_have_value(f"epic:{epic}", timeout=SLOW)
        await expect(page.locator(".rm-track")).to_have_attribute(
            "data-zoom", "quarter", timeout=SLOW
        )
        await expect(
            page.locator(f"#panel .record-detail[data-record='decision:{decision['id']}']")
        ).to_be_visible(timeout=SLOW)
        await expect(page.locator("#search-tab-dups")).to_have_attribute("aria-pressed", "true")
        await expect(page.locator("#dup-min")).to_have_value("0.95")
        await expect(page.locator("#dup-epics")).to_have_value(str(epic))
        assert third.problems == []

    # A fresh load of the Roadmap's address in yet another browser says the same.
    async with browser_page(board, token=the_token()) as fourth:
        await fourth.page.goto(f"{board}{roadmap_address}")
        await fourth.page.wait_for_selector(".rm-track", timeout=SLOW)
        await expect(fourth.page.locator("#dup-min")).to_have_value("0.95", timeout=SLOW)
        await expect(fourth.page.locator("#filter")).to_have_value(f"epic:{epic}")
        assert fourth.problems == []


# -- a draft is not lost ------------------------------------------------------------------------


async def test_a_draft_survives_opening_drawers_switching_presentations_and_views(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Draft {word}")
    story = await made(client, "story", f"Story {word}", epic)
    draft = f"Typed in the panel and not saved {word}"
    unasked = f"typed in the search box {word}"
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/graph?sel=node:{story}")
        await expect(page.locator("#panel #card-title")).to_contain_text(f"#{story} ", timeout=SLOW)
        await page.locator("#edit-body").fill(draft)
        await page.evaluate("window.__body = document.querySelector('#edit-body')")

        async def kept(step: str) -> None:
            assert await page.evaluate("document.querySelector('#edit-body') === window.__body"), (
                step
            )
            assert await page.locator("#edit-body").input_value() == draft, step

        # Opening each drawer, and the other tab of the search drawer.
        await page.locator("#activity-open").click()
        await expect(drawer(page, "activity")).to_be_visible(timeout=SLOW)
        await kept("activity drawer")
        await page.locator("#global-search").fill(word)
        await page.keyboard.press("Enter")
        await expect(drawer(page, "search")).to_be_visible(timeout=SLOW)
        await result_rows(page, 2)
        await kept("search drawer")
        await page.locator("#search-tab-dups").click()
        await expect(page.locator("#drawer")).to_have_attribute("data-mode", "duplicates")
        await kept("possible duplicates")
        await page.locator("#search-tab-results").click()
        await expect(page.locator("#drawer")).to_have_attribute("data-mode", "results")
        await kept("back to the results")
        # Words typed in the search box and not asked yet are a draft too.
        await page.locator("#search-box").fill(unasked)

        # Switching the preset and the presentation of the Graph.
        for button in (
            "#graph-view-context",
            "#graph-as-list",
            "#graph-view-everything",
            "#graph-as-map",
            "#graph-view-work",
        ):
            await page.locator(button).click()
            await expect(page.locator(button)).to_have_attribute("aria-pressed", "true")
            await kept(button)
            assert await page.locator("#search-box").input_value() == unasked, button
            assert (await address(page))[1]["sel"] == f"node:{story}"
        # Going from view to view takes the selected record along.
        for view in ("roadmap", "board", "graph"):
            await page.locator(f"#top nav a[data-page={view}]").click()
            await on_view(page, view)
            await expect(page.locator("#panel #card-title")).to_be_visible()
            await kept(f"the {view} view")
            assert (await address(page))[1]["sel"] == f"node:{story}"
        # Closing the drawer leaves it alone as well; then the draft is saved as it was typed.
        await page.locator("#drawer-close").click()
        await expect(page.locator("#drawer")).to_be_hidden()
        await kept("closed drawer")
        await page.get_by_role("button", name="Save changes").click()
        await expect(page.locator("#panel .card-detail")).to_contain_text(draft, timeout=SLOW)
        assert (await ok(client, "get_node", node_id=story))["body"] == draft
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
        await expect(page.locator(f".lane[data-epic='{epic}']")).to_be_visible(timeout=SLOW)
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
            await expect(page.locator(f".lane[data-epic='{epic}']")).to_be_visible()

            # It stays when the person goes to another view, to a drawer and to a settings page.
            for view, marker in (("graph", "#graph-page"), ("roadmap", ".rm-track")):
                await page.locator(f"#top nav a[data-page={view}]").click()
                await on_view(page, view)
                await expect(page.locator(marker)).to_be_visible(timeout=SLOW)
                await expect(note).to_be_visible()
                await expect(chip).to_be_visible()
            await page.locator("#activity-open").click()
            await expect(drawer(page, "activity")).to_be_visible(timeout=SLOW)
            await expect(note).to_be_visible()
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

        # Something the person does fails in the place where they do it, in plain words.
        everything = "**/board/api/projects/*/tool/*"
        await page.route(everything, unreachable)
        await page.locator("#global-search").fill(word)
        await page.keyboard.press("Enter")
        await expect(page.locator("#search-problem")).to_contain_text(
            "The board cannot reach the server", timeout=SLOW
        )
        await page.locator("#drawer-close").click()
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
        assert (await events_of(client, node_id=epic))[0]["action"] == "create"
