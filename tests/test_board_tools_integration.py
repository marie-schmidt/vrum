"""M14's Tools page against the running containers, driven in a real headless browser and checked
against a real MCP client (see ./check.sh): it lists exactly the tools the server lists, each with
its description, fields and place on the board (or why it is for agents only), "Try it" returns
what MCP returns and exists only for the tools that read, and the server turns away a try of
anything that changes something."""

import json
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_helpers import SLOW, api_path, board_url, browser_page, made, plain
from tests.board_m14 import spoken
from tests.helpers import ok, refused, the_token, unique_word
from tests.world import SESSION_COOKIE, api, project_at, register
from vrum.board.tools import TOOL_PLACES, AgentsOnly

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


async def listed(page: Page) -> None:
    await expect(page.locator("#tool-list article.tool").first).to_be_visible(timeout=SLOW)


# -- 3: the page is the server's own tool list -----------------------------------------------------


async def test_the_page_lists_exactly_the_tools_the_server_lists_with_a_place_for_each(
    client: Client, board: str
) -> None:
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/tools")
        await listed(page)
        names: list[str] = await page.locator("#tool-list article.tool").evaluate_all(
            "els => els.map(e => e.dataset.name)"
        )
        assert sorted(names) == sorted(tools) and len(names) == len(set(names)) == len(tools)
        await expect(page.locator("#count")).to_contain_text(f"{len(tools)} tools:")

        reads = {
            name
            for name, tool in tools.items()
            if tool.annotations and tool.annotations.read_only_hint
        }
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
            # Where on the board, or why not: one or the other, never neither.
            place = TOOL_PLACES[name].place
            if isinstance(place, AgentsOnly):
                await expect(entry.locator(".where.agents")).to_contain_text(place.why)
                assert await entry.locator(".where a").count() == 0
            else:
                link = entry.locator(".where a")
                assert spoken(await link.inner_text()) == place.where
                assert await link.get_attribute("href") == f"#/{place.page}"
            # A try form for the tools that only read, and for no other.
            assert (await entry.locator("details.try").count() == 1) == (name in reads), name
        assert await page.locator(".where.problem").count() == 0
        assert await page.locator("section.tool-area[data-area=unclassified]").count() == 0
        # Reindex and import are for agents only, and say why.
        for name in ("reindex", "import_graph"):
            await expect(page.locator(f"#tool-entry-{name} .where.agents")).to_be_visible()
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
        await page.get_by_role("button", name="Search", exact=True).click()
        await expect(page.locator("#count")).to_contain_text("match", timeout=SLOW)
        shown: list[str] = await page.locator("#tool-list article.tool").evaluate_all(
            "els => els.map(e => e.dataset.name)"
        )
        assert "find_overlaps" in shown and "create_node" not in shown
        assert 1 <= len(shown) < len((await client.list_tools()).tools)
        await page.locator("#clear-tool-search").click()
        await expect(page.locator("#tool-list article.tool").nth(10)).to_be_visible(timeout=SLOW)
        assert w.problems == []


# -- 3: try it -------------------------------------------------------------------------------


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
        # A refusal is the server's own words.
        refusal = await try_it(page, "get_node", node_id="999999999")
        plain(refusal["refused"], await refused(client, "get_node", node_id=999999999))
        assert w.problems == []


async def test_no_tool_that_changes_anything_has_a_try_it_and_the_server_turns_a_try_away(
    client: Client, board: str
) -> None:
    token = the_token()
    owner = project_at(board).owner.sign_in_again()
    origin = board.split("/board")[0]
    before = (await ok(client, "list_nodes", limit=1))["total"]
    made_up = {"actor": "x", "kind": "epic", "title": "Must not exist"}
    refusal = owner.call("POST", api_path(board, "/try/create_node"), made_up)
    assert refusal.status == 403 and "changes something" in refusal.body["message"]
    assert "Must not exist" not in json.dumps(refusal.body)
    assert (await ok(client, "list_nodes", limit=1))["total"] == before
    for name in ("claim_node", "reindex", "import_graph", "reflect", "next_work"):
        assert owner.call("POST", api_path(board, f"/try/{name}"), {}).status == 403, name
    nothing = owner.call("POST", api_path(board, "/try/no_such_tool"), {})
    assert nothing.status == 404 and "no tool called no_such_tool" in nothing.body["message"]
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
