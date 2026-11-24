"""M46: the Needs a person view for hosts that draw MCP Apps, on the running containers through a
real MCP client: a key that may use `needs_a_person` is offered the tool with the view it names, the
view is served as a self-contained page, the tool answers in plain words and as data (with the
board's address from the server's own setting), only reads, and a person's answer is still only
given by a person."""

from typing import Any

import pytest

from tests.helpers import connect, refused
from tests.test_asks_integration import a_task, ask_blocking
from tests.world import make_limited_key, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

VIEW = "ui://vrum/needs-a-person.html"


async def test_the_tool_names_its_view_and_the_view_is_one_page_with_nothing_loaded() -> None:
    project = make_project(register())
    key = make_limited_key(project, "chat-host", ["needs_a_person"])
    async with connect(project.mcp_url, key) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert list(tools) == ["needs_a_person"]
        ui: dict[str, Any] = (tools["needs_a_person"].meta or {})["ui"]
        assert ui == {"resourceUri": VIEW, "visibility": ["model", "app"]}
        assert tools["needs_a_person"].annotations is not None
        assert tools["needs_a_person"].annotations.read_only_hint is True

        listed = [str(one.uri) for one in (await client.list_resources()).resources]
        assert VIEW in listed
        [page] = (await client.read_resource(VIEW)).contents
        assert page.mime_type == "text/html;profile=mcp-app"
        html = getattr(page, "text", "")
        assert html.startswith("<!DOCTYPE html>")
        assert "http://" not in html and "https://" not in html
        assert "innerHTML" not in html and "<script src" not in html


async def test_the_list_comes_in_words_and_as_data_with_the_boards_address() -> None:
    owner = register()
    project = make_project(owner)
    key = make_limited_key(project, "chat-host", ["needs_a_person"])
    async with connect(project.mcp_url, project.key) as worker:
        task = await a_task(worker)
        number = await ask_blocking(worker, task)

    async with connect(project.mcp_url, key) as client:
        result = await client.call_tool("needs_a_person", {})
        assert not result.is_error
        words = "\n".join(block.text for block in result.content if hasattr(block, "text"))
        assert "1 thing is waiting for a person." in words
        assert f"Q-{number}" in words
        assert "A person answers on the board at " in words
        assert words.endswith("/board/p/" + str(project.id) + "#/needs, under their own name.")

        data = result.structured_content
        assert data is not None
        assert data["total"] == 1
        assert data["board_address"].endswith(f"/board/p/{project.id}")
        [group] = data["groups"]
        [row] = group["items"]
        assert row["kind"] == "ask" and row["ask"]["id"] == number
        assert row["ask"]["kind"] == "question" and row["ask"]["choices"]
        assert row["node"] == task["ref"]

        # What the view shows is what the board's own page reads: the same call, as a person.
        as_person = project.owner.call(
            "POST", f"/board/api/projects/{project.id}/tool/needs_a_person", {}
        )
        assert as_person.status == 200
        assert as_person.body["total"] == data["total"]
        assert as_person.body["board_address"] == data["board_address"]


async def test_an_empty_list_says_nothing_is_waiting() -> None:
    project = make_project(register())
    key = make_limited_key(project, "chat-host", ["needs_a_person"])
    async with connect(project.mcp_url, key) as client:
        result = await client.call_tool("needs_a_person", {})
        assert not result.is_error
        assert [block.text for block in result.content if hasattr(block, "text")] == [
            "Nothing is waiting for a person."
        ]


async def test_the_key_that_holds_the_view_cannot_answer_for_a_person() -> None:
    project = make_project(register())
    key = make_limited_key(project, "chat-host", ["needs_a_person"])
    async with connect(project.mcp_url, key) as client:
        words = await refused(client, "answer_ask", ask=1, answer="yes")
        assert "answer_ask" in words
        words = await refused(client, "approve_plan", plan=1, approved=True)
        assert "approve_plan" in words


async def test_a_standard_key_is_not_offered_the_tool_and_so_not_the_view() -> None:
    project = make_project(register())
    key = make_limited_key(project, "ordinary", None)
    async with connect(project.mcp_url, key) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
        assert "needs_a_person" not in tools
        words = await refused(client, "needs_a_person")
        assert "needs_a_person" in words
