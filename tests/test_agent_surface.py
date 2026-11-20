"""M28: the tools Vrum offers an agent are a small chosen set (surface.py), a key with no list of
its own gets exactly that set, a key with a list gets exactly its list, and nothing an agent is
told points it at a tool it cannot see."""

import asyncio
import re
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from vrum.accounts import KeyAccess, key_not_allowed, tool_not_offered
from vrum.config import Settings
from vrum.guard import tools_of
from vrum.records import PEOPLE_ONLY_TOOLS
from vrum.server import INSTRUCTIONS, create_server
from vrum.surface import AGENT_SURFACE, AGENT_TOOLS, MOST_AGENT_TOOLS

ENV = {"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "s3cret"}


def server_tools() -> dict[str, Any]:
    server = create_server(Settings.from_env(ENV))
    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


def test_the_agent_tools_are_few_and_all_exist_and_none_is_for_people_only() -> None:
    everything = server_tools()
    assert len(AGENT_TOOLS) == len(AGENT_SURFACE) <= MOST_AGENT_TOOLS == 25
    assert [name for name in AGENT_TOOLS if name not in everything] == []
    assert AGENT_SURFACE.isdisjoint(PEOPLE_ONLY_TOOLS)
    # The rest are many more than the set, so the set really is a choice.
    assert len(everything) > len(AGENT_TOOLS)


def test_the_work_loop_is_offered_whole() -> None:
    assert {"next_work", "claim_node", "renew_claim", "finish_work", "recall", "search"} <= (
        AGENT_SURFACE
    )
    assert {"add_note", "supersede_note", "withdraw_note"} <= AGENT_SURFACE


def test_what_a_person_does_on_the_board_is_not_offered() -> None:
    for name in ("set_confirmation", "reindex", "import_graph", "export_graph", "node_history"):
        assert name not in AGENT_SURFACE


def words_of(tool: Any) -> str:
    """Everything an agent reads about one tool: its description and each argument's."""
    schema = tool.input_schema or {}
    arguments = [
        str(spec.get("description", "")) for spec in (schema.get("properties") or {}).values()
    ]
    return " ".join([tool.description or "", *arguments])


def test_no_offered_tool_tells_an_agent_to_use_a_tool_it_is_not_offered() -> None:
    everything = server_tools()
    others = [name for name in everything if name not in AGENT_SURFACE]
    problems = []
    for name in AGENT_TOOLS:
        text = words_of(everything[name])
        for other in others:
            if re.search(rf"(?<![A-Za-z0-9_]){other}(?![A-Za-z0-9_])", text):
                problems.append(f"{name} mentions {other}")
    assert problems == []


def test_the_server_instructions_name_only_tools_an_agent_is_offered() -> None:
    everything = server_tools()
    named = set(re.findall(r"`([a-z]+_[a-z_]+)`", INSTRUCTIONS)) & set(everything)
    assert named and named <= AGENT_SURFACE, sorted(named - AGENT_SURFACE)


def test_a_key_with_no_list_gets_the_agent_set_and_a_key_with_a_list_gets_exactly_it() -> None:
    assert tools_of(KeyAccess(1, 1, "api-agent", None)) == AGENT_SURFACE
    own = frozenset({"list_nodes", "export_graph"})
    assert tools_of(KeyAccess(1, 2, "backup", own)) == own


class Accounts:
    def __init__(self, **held: frozenset[str] | None) -> None:
        self.held = {
            f"vrk_{name}": KeyAccess(7, index, name, tools)
            for index, (name, tools) in enumerate(held.items())
        }

    async def project_for_key(self, key: str) -> KeyAccess | None:
        return self.held.get(key)


def server_with(**held: frozenset[str] | None) -> Any:
    server: Any = create_server(Settings.from_env(ENV))
    server.accounts = Accounts(**held)
    return server


def sending(key: str) -> Any:
    headers = {"authorization": f"Bearer {key}"}
    return SimpleNamespace(headers=headers, request=SimpleNamespace(headers=headers))


def test_a_standard_key_lists_the_agent_set_and_a_listed_key_lists_its_own_tools() -> None:
    server = server_with(standard=None, backup=frozenset({"list_nodes", "export_graph"}))
    standard = asyncio.run(server._handle_list_tools(sending("vrk_standard"), None))
    backup = asyncio.run(server._handle_list_tools(sending("vrk_backup"), None))
    assert {tool.name for tool in standard.tools} == AGENT_SURFACE
    assert {tool.name for tool in backup.tools} == {"list_nodes", "export_graph"}


def test_a_standard_key_is_refused_a_tool_outside_the_set_and_told_where_it_is_done() -> None:
    server = server_with(standard=None)
    for tool in ("update_nodes", "node_history", "set_confirmation", "reindex"):
        with pytest.raises(ToolError) as refused:
            asyncio.run(server.call_tool(tool, {}, sending("vrk_standard")))
        assert str(refused.value) == (
            f"Error executing tool {tool}: " + tool_not_offered("standard", tool)
        )
    assert "done on the board, by a person" in tool_not_offered("standard", "update_nodes")
    assert "Users page" in tool_not_offered("standard", "update_nodes")


def test_a_key_with_its_own_list_is_still_told_in_the_old_words() -> None:
    server = server_with(reader=frozenset({"list_nodes"}))
    with pytest.raises(ToolError) as refused:
        asyncio.run(server.call_tool("create_node", {}, sending("vrk_reader")))
    assert str(refused.value) == (
        "Error executing tool create_node: " + key_not_allowed("reader", "create_node")
    )
