"""M16's Tools page, one of the settings pages, against the running containers, driven in a real
headless browser and checked against a real MCP client (see ./check.sh): it is reached from the
Settings menu with the keyboard, it lists exactly the 53 tools the server lists (and none that
generate, or that belong to beliefs or principles), each with its description, fields and place on
the board (or why it is for agents only), "Try it" returns what MCP returns and exists only for the
tools that read, and the server turns away a try of anything that changes something."""

import json
import re
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_helpers import SLOW, api_path, board_url, browser_page, made, plain
from tests.board_m14 import spoken
from tests.board_search import drawer, open_settings
from tests.helpers import make_decision, make_fact, ok, refused, the_token, unique_word
from tests.world import SESSION_COOKIE, api, project_at, register
from vrum.board.tools import TOOL_PLACES, AgentsOnly

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

# The tools that wrote text with a language model, and what words belong to the old model of
# memory: none of it is on the server, and none of it is on the page.
GONE = {"extract_facts", "consolidate_memory", "find_contradictions", "reflect"}
OLD_WORDS = re.compile(r"belief|principle|language model|memor(y|ies)", re.IGNORECASE)
DRAWERS = {"search", "activity"}


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


async def listed(page: Page) -> None:
    await expect(page.locator("#tool-list article.tool").first).to_be_visible(timeout=SLOW)


# -- the page is the server's own tool list --------------------------------------------------------


async def test_the_page_lists_exactly_the_53_tools_the_server_lists_with_a_place_for_each(
    client: Client, board: str
) -> None:
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert len(tools) == 53 == len(TOOL_PLACES)
    assert GONE.isdisjoint(tools)
    assert not [name for name in tools if OLD_WORDS.search(name)]
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/board")
        # It is reached from the Settings menu, with the keyboard.
        await open_settings(page, "tools")
        await listed(page)
        names: list[str] = await page.locator("#tool-list article.tool").evaluate_all(
            "els => els.map(e => e.dataset.name)"
        )
        assert sorted(names) == sorted(tools) and len(names) == len(set(names)) == 53
        await expect(page.locator("#count")).to_contain_text("53 tools:")

        reads = {
            name
            for name, tool in tools.items()
            if tool.annotations and tool.annotations.read_only_hint
        }
        assert {"search", "recall", "list_facts", "get_decision", "find_overlaps"} <= reads
        assert {"set_confirmation", "retain_fact", "record_decision", "withdraw_fact"}.isdisjoint(
            reads
        )
        for name, tool in tools.items():
            entry = page.locator(f"#tool-entry-{name}")
            # The description, whole: its first paragraph and then the rest.
            paragraphs = [p for p in (tool.description or "").split("\n\n") if p.strip()]
            assert spoken(await entry.locator("p.first").inner_text()) == spoken(paragraphs[0])
            for extra in paragraphs[1:]:
                assert spoken(extra) in spoken(
                    await entry.locator("details.more").text_content() or ""
                )
            # The fields, with their types and descriptions, as the server's own schema has them.
            properties = tool.input_schema.get("properties", {})
            fields: list[str] = await entry.locator("tr[data-field]").evaluate_all(
                "els => els.map(e => e.dataset.field)"
            )
            assert fields == list(properties), name
            for field_name, spec in properties.items():
                if spec.get("description"):
                    cell = entry.locator(f"tr[data-field='{field_name}'] td").last
                    assert spoken(spec["description"]) in spoken(await cell.text_content() or "")
            # Whether it changes anything, from the server's own annotation.
            await expect(entry).to_have_attribute("data-changes", "0" if name in reads else "1")
            # Where on the board, or why not: one or the other, never neither. A drawer opens over
            # the page the person is on, which is this one.
            place = TOOL_PLACES[name].place
            if isinstance(place, AgentsOnly):
                await expect(entry.locator(".where.agents")).to_contain_text(place.why)
                assert await entry.locator(".where a").count() == 0
            else:
                link = entry.locator(".where a")
                assert spoken(await link.inner_text()) == place.where
                expected = (
                    f"#/tools?drawer={place.page}" if place.page in DRAWERS else f"#/{place.page}"
                )
                assert await link.get_attribute("href") == expected
            # A try form for the tools that only read, and for no other.
            assert (await entry.locator("details.try").count() == 1) == (name in reads), name
        assert await page.locator(".where.problem").count() == 0
        assert await page.locator("section.tool-area[data-area=unclassified]").count() == 0
        # Reindex and import are for agents only, and say why.
        for name in ("reindex", "import_graph"):
            await expect(page.locator(f"#tool-entry-{name} .where.agents")).to_be_visible()
        # Nothing on the page belongs to what was taken out, and no tool of it is listed.
        assert not OLD_WORDS.search(await page.locator("#page").inner_text())
        assert not [name for name in GONE if name in await page.locator("#page").inner_text()]
        assert await page.locator("#page").get_by_text("Ask", exact=True).count() == 0
        # A place that is a drawer opens the drawer over the Tools page.
        await page.locator("#tool-entry-find_overlaps .where a").click()
        await expect(drawer(page, "search")).to_be_visible(timeout=SLOW)
        assert page.url.endswith("#/tools?drawer=search")
        await expect(page.locator("#tool-list article.tool").first).to_be_visible()
        assert w.problems == []


async def test_the_settings_menu_is_a_menu_for_the_keyboard(client: Client, board: str) -> None:
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/graph")
        menu, items = page.locator("#settings-menu"), page.locator("#settings-list [role=menuitem]")
        await menu.focus()
        await page.keyboard.press("ArrowUp")
        await expect(page.locator("#settings-status")).to_be_focused()
        assert await items.evaluate_all("els => els.map(e => e.id)") == [
            "settings-access",
            "settings-tools",
            "settings-status",
        ]
        await page.keyboard.press("ArrowDown")
        await expect(page.locator("#settings-access")).to_be_focused()
        await page.keyboard.press("End")
        await expect(page.locator("#settings-status")).to_be_focused()
        await page.keyboard.press("Home")
        await expect(page.locator("#settings-access")).to_be_focused()
        # Escape closes it and gives the focus back; nothing was chosen, so the view stays.
        await page.keyboard.press("Escape")
        await expect(page.locator("#settings-list")).to_be_hidden()
        await expect(menu).to_be_focused()
        assert page.url.endswith("#/graph")
        # Space chooses an item as Enter does.
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Space")
        await expect(page).to_have_url(re.compile(r"#/tools$"), timeout=SLOW)
        await expect(menu).to_have_attribute("aria-current", "page")
        assert w.problems == []


async def test_the_page_says_how_an_agent_connects_and_never_shows_a_key_or_a_session(
    client: Client, board: str, server_url: str
) -> None:
    project = project_at(board)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/tools")
        await listed(page)
        assert await page.locator("#endpoint").inner_text() == server_url
        # It describes the agent key: sent as a bearer header, read from the environment, made on
        # the Agent access page, and never one that is shown here.
        connecting = await page.locator("#connecting").inner_text()
        assert "agent key" in connecting and "Authorization: Bearer" in connecting
        assert "shown only once" in connecting
        assert await page.locator("#connecting a[href='#/access']").count() == 1
        await page.locator("#example summary").click()
        example = await page.locator("#example").text_content() or ""
        assert server_url in example and "Authorization" in example
        assert "VRUM_AGENT_KEY" in example and "VRUM_TOKEN" not in example
        # Neither an agent key nor the session cookie (which a page cannot read) is in anything
        # the page shows or asked for. (The session's other secret is checked on the Status page.)
        cookies = [c for c in await page.context.cookies() if c["name"] == SESSION_COOKIE]
        everything = " ".join(
            [
                await page.locator("body").inner_text(),
                await page.evaluate("document.documentElement.outerHTML"),
                example,
                page.url,
                *w.requests,
            ]
        )
        for secret in (project.key, cookies[0]["value"]):
            assert secret not in everything
        # The search narrows the list by what is in the names, areas, descriptions and places.
        await page.locator("#tool-search").fill("overlaps")
        await page.locator("#page form.filter button[type=submit]").click()
        await expect(page.locator("#count")).to_contain_text("match", timeout=SLOW)
        shown: list[str] = await page.locator("#tool-list article.tool").evaluate_all(
            "els => els.map(e => e.dataset.name)"
        )
        assert "find_overlaps" in shown and "create_node" not in shown
        assert 1 <= len(shown) < len((await client.list_tools()).tools)
        assert page.url.endswith("#/tools?q=overlaps")
        await page.locator("#clear-tool-search").click()
        await expect(page.locator("#tool-list article.tool").nth(10)).to_be_visible(timeout=SLOW)
        assert w.problems == []


# -- try it ----------------------------------------------------------------------------------------


async def try_it(page: Page, name: str, **typed: str) -> Any:
    """Open the try form of a tool, type into its fields, run it, and give what it showed."""
    entry = page.locator(f"#tool-entry-{name}")
    await entry.scroll_into_view_if_needed()
    form = entry.locator("details.try")
    if not await form.evaluate("e => e.open"):
        await entry.locator("details.try summary").click()
    for field_name, text in typed.items():
        await entry.locator(f"[name={field_name}]").fill(text)
    await entry.locator("pre.result").evaluate("e => { e.hidden = true; e.textContent = ''; }")
    await entry.locator(".try-form .problem").evaluate("e => { e.textContent = ''; }")
    await entry.locator(".try-run").click()
    result = entry.locator("pre.result")
    problem = entry.locator(".try-form .problem")
    for _ in range(100):
        if await result.is_visible() and (await result.inner_text()).strip():
            return json.loads(await result.inner_text())
        if (await problem.inner_text()).strip():
            return {"refused": (await problem.inner_text()).strip()}
        await page.wait_for_timeout(100)
    pytest.fail(f"{name} showed nothing")


async def test_try_it_returns_what_mcp_returns_and_a_refusal_in_the_same_words(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Tried {word}")
    story = await made(client, "story", f"Try story {word}", epic)
    decision = await make_decision(client, f"Try decision {word}", node_ids=[story])
    fact = await make_fact(client, f"Try fact {word}", node_ids=[story])
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/tools")
        await listed(page)
        # A list with filters of several kinds: a word, a whole number and a list of ids.
        shown = await try_it(page, "list_nodes", kind="story", limit="3", query=f"id:{story}")
        assert shown == await ok(client, "list_nodes", kind="story", limit=3, query=f"id:{story}")
        assert [n["id"] for n in shown["items"]] == [story]
        shown = await try_it(page, "get_node", node_id=str(epic))
        assert shown == await ok(client, "get_node", node_id=epic)
        # A tool with no fields, and one with a list as a field.
        shown = await try_it(page, "who_is_working")
        assert {a["actor"] for a in shown["agents"]} == {
            a["actor"] for a in (await ok(client, "who_is_working"))["agents"]
        }
        shown = await try_it(page, "search", query=word, types="epic, story", limit="5")
        assert shown == await ok(client, "search", query=word, types=["epic", "story"], limit=5)
        assert {(i["item_type"], i["id"]) for i in shown["items"]} == {
            ("epic", epic),
            ("story", story),
        }
        # The two kinds of context record, and what is known about the work they concern.
        shown = await try_it(page, "get_decision", decision_id=str(decision["id"]))
        assert shown == await ok(client, "get_decision", decision_id=decision["id"])
        assert shown["title"] == f"Try decision {word}" and shown["confirmation"] == "unconfirmed"
        shown = await try_it(page, "list_facts", node_id=str(story))
        assert shown == await ok(client, "list_facts", node_id=story)
        assert [f["id"] for f in shown["items"]] == [fact["id"]]
        shown = await try_it(page, "recall", node_id=str(story))
        assert shown == await ok(client, "recall", node_id=story)
        assert [i["decision"]["id"] for i in shown["active_decisions"]["items"]] == [decision["id"]]
        # A refusal is the server's own words.
        refusal = await try_it(page, "get_node", node_id="999999999")
        plain(refusal["refused"], await refused(client, "get_node", node_id=999999999))
        refusal = await try_it(page, "get_fact", fact_id="999999999")
        plain(refusal["refused"], await refused(client, "get_fact", fact_id=999999999))
        assert w.problems == []


async def test_no_tool_that_changes_anything_has_a_try_it_and_the_server_turns_a_try_away(
    client: Client, board: str
) -> None:
    token = the_token()
    owner = project_at(board).owner.sign_in_again()
    origin = board.split("/board")[0]
    before = (await ok(client, "list_nodes", limit=1))["total"]
    facts_before = (await ok(client, "list_facts", limit=1))["total"]
    made_up = {"actor": "x", "kind": "epic", "title": "Must not exist"}
    refusal = owner.call("POST", api_path(board, "/try/create_node"), made_up)
    assert refusal.status == 403 and "changes something" in refusal.body["message"]
    assert "Must not exist" not in json.dumps(refusal.body)
    assert (await ok(client, "list_nodes", limit=1))["total"] == before
    for name in (
        "claim_node",
        "reindex",
        "import_graph",
        "next_work",
        "retain_fact",
        "record_decision",
        "set_confirmation",
        "withdraw_fact",
    ):
        assert owner.call("POST", api_path(board, f"/try/{name}"), {}).status == 403, name
    recorded = owner.call(
        "POST",
        api_path(board, "/try/retain_fact"),
        {"actor": "x", "text": "Must not exist", "source": "x"},
    )
    assert recorded.status == 403
    assert (await ok(client, "list_facts", limit=1))["total"] == facts_before
    # The tools that were taken out are not there to be tried, or to be called.
    for name in (*GONE, "no_such_tool"):
        nothing = owner.call("POST", api_path(board, f"/try/{name}"), {})
        assert nothing.status == 404 and f"no tool called {name}" in nothing.body["message"], name
        assert owner.call("POST", api_path(board, f"/tool/{name}"), {}).status in {403, 404}, name
    # A try needs a signed-in owner like every other call (an agent key is not one), and a read
    # goes through the same checks.
    for cookie, key in ((None, None), (None, token)):
        unsigned = api(
            origin, "POST", api_path(board, "/try/list_nodes"), {}, cookie=cookie,
            extra={"Authorization": f"Bearer {key}"} if key else None,
        )  # fmt: skip
        assert unsigned.status == 401 and "not signed in" in unsigned.body["message"]
    stranger = register(origin)
    theirs = stranger.call("POST", api_path(board, "/try/list_nodes"), {})
    assert theirs.status == 404 and "no project" in theirs.body["message"]
    limit = owner.call("POST", api_path(board, "/try/list_nodes"), {"limit": 0})
    assert limit.status == 400
    assert "limit must be a whole number from 1 to 200" in limit.body["message"]
    assert api(origin, "GET", "/board/api/tools").status == 401
    assert (await ok(client, "list_nodes", limit=1))["total"] == before
    # The page offers a try for no tool that changes something.
    async with browser_page(board, token=token) as w:
        await w.open("#/tools")
        await listed(w.page)
        changing = w.page.locator("#tool-list article.tool[data-changes='1']")
        assert await changing.count() > 20
        assert await changing.locator("details.try").count() == 0
        assert w.problems == []
