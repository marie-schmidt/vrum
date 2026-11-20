"""What each agent key may do, without any containers: how a list of tools is checked and kept, and
what the guard does with a call and with a tool list when the key is limited (against a stand-in
for the accounts, so that it is the guard that is looked at)."""

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from vrum.accounts import AccessRefusal, KeyAccess, clean_allowance, key_not_allowed
from vrum.config import Settings
from vrum.records import PEOPLE_ONLY_TOOLS
from vrum.server import create_server
from vrum.surface import AGENT_SURFACE

KNOWN = {"list_nodes", "get_node", "create_node", "add_note", "set_confirmation"}


def refusal_of(tools: Any) -> str:
    with pytest.raises(AccessRefusal) as refused:
        clean_allowance(tools, KNOWN)
    return str(refused.value)


# -- the list that is kept ---------------------------------------------------------------------


def test_a_list_is_kept_sorted_and_without_repeats_and_no_list_means_the_standard_tools() -> None:
    assert clean_allowance(None, KNOWN) is None
    assert clean_allowance(["list_nodes", "create_node", "list_nodes"], KNOWN) == [
        "create_node",
        "list_nodes",
    ]


def test_an_empty_list_a_list_of_something_else_and_a_list_that_is_not_a_list_are_refused() -> None:
    assert refusal_of([]) == "Tick at least one tool, or leave the key with the standard tools."
    for wrong in ("list_nodes", {"list_nodes": True}, 5, [5], ["list_nodes", None], [["x"]]):
        assert "has to be a list of tool names" in refusal_of(wrong)


def test_a_tool_only_a_person_can_use_is_refused_by_name() -> None:
    message = refusal_of(["list_nodes", "set_confirmation"])
    assert message.startswith("Only a person can use set_confirmation, so an agent key cannot")
    assert "Nothing was changed." in message
    assert "set_confirmation" in PEOPLE_ONLY_TOOLS


def test_a_tool_the_server_does_not_have_is_refused_and_what_looks_odd_is_not_repeated() -> None:
    assert refusal_of(["list_nodes", "drop_tables"]).startswith(
        "This server has no tool called drop_tables."
    )
    assert "no tools called a_tool, b_tool" in refusal_of(["a_tool", "b_tool"])
    odd = refusal_of(["list_nodes", "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2 + " with spaces"])
    assert "ghp_" not in odd and "that name" in odd
    many = refusal_of([f"tool_{number}" for number in range(40)])
    assert "tool_0, tool_1, tool_10, tool_11, tool_12 and 35 more" in many


def test_every_people_only_tool_is_a_tool_the_server_has() -> None:
    server = create_server(Settings.from_env({"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "s3cret"}))
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert names >= PEOPLE_ONLY_TOOLS


def test_the_refusal_names_the_agent_and_the_tool_and_says_what_to_do() -> None:
    assert key_not_allowed("api-agent", "add_note") == (
        "This agent key (api-agent) is not allowed to use add_note. Ask the project's "
        "owner to change what the key may do, on the Users page. Nothing was changed."
    )


# -- the guard ---------------------------------------------------------------------------------


class Accounts:
    """The keys of one project: what each may do. A key that is not here is not valid."""

    def __init__(self, **held: frozenset[str] | None) -> None:
        self.held = {
            f"vrk_{name}": KeyAccess(7, index, name, tools)
            for index, (name, tools) in enumerate(held.items())
        }

    async def project_for_key(self, key: str) -> KeyAccess | None:
        return self.held.get(key)


def server_with(**held: frozenset[str] | None) -> Any:
    server: Any = create_server(
        Settings.from_env({"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "s3cret"})
    )
    server.accounts = Accounts(**held)
    return server


def sending(key: str | None) -> Any:
    headers = {} if key is None else {"authorization": f"Bearer {key}"}
    # A tool call is handed a context that has the headers, a tool list a request context that has
    # the request: both are shaped like the library's.
    return SimpleNamespace(headers=headers, request=SimpleNamespace(headers=headers))


def test_a_limited_key_is_refused_a_tool_it_may_not_use_before_anything_else_happens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = server_with(reader=frozenset({"list_nodes"}))
    caplog.set_level(logging.INFO, logger="vrum.tools")
    with pytest.raises(ToolError) as refused:
        asyncio.run(
            server.call_tool("create_node", {"kind": "epic", "title": "x"}, sending("vrk_reader"))
        )
    assert str(refused.value) == (
        "Error executing tool create_node: " + key_not_allowed("reader", "create_node")
    )
    fields = [record.fields for record in caplog.records if hasattr(record, "fields")]
    assert fields[-1]["outcome"] == "refused" and fields[-1]["reason"] == "not_allowed"
    assert (fields[-1]["actor"], fields[-1]["actor_kind"]) == ("reader", "agent")
    assert "vrk_reader" not in str(fields)


def test_a_name_the_server_does_not_have_gets_the_usual_unknown_tool_answer() -> None:
    server = server_with(reader=frozenset({"list_nodes"}))
    with pytest.raises(ToolError) as unknown:
        asyncio.run(server.call_tool("no_such_tool", {}, sending("vrk_reader")))
    assert "not allowed" not in str(unknown.value) and "no_such_tool" in str(unknown.value)


def test_a_tool_list_shows_a_limited_key_only_its_tools_and_a_standard_key_the_agent_set() -> None:
    server = server_with(reader=frozenset({"list_nodes", "get_node", "gone_tool"}), full=None)
    limited = asyncio.run(server._handle_list_tools(sending("vrk_reader"), None))
    standard = asyncio.run(server._handle_list_tools(sending("vrk_full"), None))
    assert {tool.name for tool in limited.tools} == {"list_nodes", "get_node"}
    assert {tool.name for tool in standard.tools} == AGENT_SURFACE


def test_a_tool_list_for_a_missing_or_wrong_key_is_an_error_in_words() -> None:
    server = server_with(reader=None)
    for key, words in ((None, "needs an agent key"), ("vrk_other", "agent key is not valid")):
        with pytest.raises(MCPError) as refused:
            asyncio.run(server._handle_list_tools(sending(key), None))
        assert words in refused.value.message and refused.value.code == -32001


def test_the_board_asks_for_the_whole_list_whatever_a_key_may_do() -> None:
    server = server_with(reader=frozenset({"list_nodes"}))
    assert len(asyncio.run(server.list_tools())) > 40
