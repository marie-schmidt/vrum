"""M28: the tools Vrum offers an agent are a small chosen set (surface.py), a key with no list of
its own gets exactly that set, a key with a list gets exactly its list, and nothing an agent is
told points it at a tool it cannot see.

M37: a key that calls Vrum by running code is shown exactly two tools, explore and run, and the set
or list of tools it has becomes the functions of its scripts. Each way of calling refuses the
other's tools in words, and a script's own calls go through the same guard."""

import asyncio
import inspect
import logging
import re
import sys
from datetime import date
from functools import cache
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from vrum.accounts import (
    RUN_IS_NOT_FOR_THE_BOARD,
    SCRIPT_KEY_NOW_TOOLS,
    SCRIPT_KEY_REVOKED,
    AccessRefusal,
    KeyAccess,
    KeyExpired,
    clean_allowance,
    clean_code_mode,
    code_tool_for_a_tools_key,
    key_not_allowed,
    no_such_function,
    script_key_expired_words,
    tool_by_name_for_a_code_key,
    tool_not_offered,
)
from vrum.board import BOARD_TOOLS
from vrum.board.tools import AREAS, TOOL_PLACES, AgentsOnly, OnTheBoard
from vrum.checks import Checks
from vrum.claims import Claims
from vrum.code_mode import KeyStopped, RunTag
from vrum.common import Refusal
from vrum.config import Settings
from vrum.db import current_project, working_for
from vrum.definition_of_done import DefinitionOfDoneBook
from vrum.exchange import Exchange
from vrum.graph import Graph
from vrum.guard import Caller, GuardedServer, current_caller, tools_of
from vrum.identity import agent, current_identity, person, working_as
from vrum.indexing import Indexer
from vrum.links import Links
from vrum.notes import Notes
from vrum.placement import Placement, PlacementBook
from vrum.recall import Recaller
from vrum.records import PEOPLE_ONLY_TOOLS
from vrum.references import References
from vrum.rules import Rules
from vrum.search import Searcher
from vrum.server import INSTRUCTIONS, create_server
from vrum.status import Status
from vrum.surface import AGENT_SURFACE, AGENT_TOOLS, CODE_TOOLS, MOST_AGENT_TOOLS, in_order
from vrum.workflow import Workflow

ENV = {"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "s3cret"}


@cache
def the_server() -> GuardedServer:
    return create_server(Settings.from_env(ENV))


def the_server_tools() -> list[Any]:
    """The server's tools as it holds them, with the function each runs."""
    return list(the_server()._tool_manager.list_tools())


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
    # explore and run are ordinary English words too, and are only for keys that run code: the
    # next test is the one that keeps them out of what the offered tools say.
    others = [name for name in everything if name not in AGENT_SURFACE and name not in CODE_TOOLS]
    problems = []
    for name in AGENT_TOOLS:
        text = words_of(everything[name])
        for other in others:
            if re.search(rf"(?<![A-Za-z0-9_]){other}(?![A-Za-z0-9_])", text):
                problems.append(f"{name} mentions {other}")
    assert problems == []


def test_no_offered_tool_names_explore_or_run_as_a_tool() -> None:
    everything = server_tools()
    for name in AGENT_TOOLS:
        assert not re.search(r"`(explore|run)`", words_of(everything[name])), name


def test_the_two_tools_of_code_mode_exist_are_not_offered_and_only_explore_reads() -> None:
    everything = server_tools()
    assert CODE_TOOLS == ("explore", "run") and set(CODE_TOOLS) <= set(everything)
    assert AGENT_SURFACE.isdisjoint(CODE_TOOLS) and PEOPLE_ONLY_TOOLS.isdisjoint(CODE_TOOLS)
    assert everything["explore"].annotations.read_only_hint is True
    assert not everything["run"].annotations or not everything["run"].annotations.read_only_hint
    # The board does not call them, so its pages are not a way to run a script. A person can try
    # explore on the Tools page, and run is marked as for agents only.
    assert BOARD_TOOLS.isdisjoint(CODE_TOOLS)
    assert "Running code" in AREAS
    assert TOOL_PLACES["explore"].area == TOOL_PLACES["run"].area == "Running code"
    assert isinstance(TOOL_PLACES["explore"].place, OnTheBoard)
    assert isinstance(TOOL_PLACES["run"].place, AgentsOnly)
    assert in_order(["zzz", "search", "next_work", "aaa"]) == ["next_work", "search", "aaa", "zzz"]


def test_the_words_a_code_key_is_told_name_only_tools_that_exist_in_backticks() -> None:
    everything = set(server_tools())
    for words in (
        code_tool_for_a_tools_key("bot", "run"),
        tool_by_name_for_a_code_key("bot", "list_nodes"),
        RUN_IS_NOT_FOR_THE_BOARD,
        no_such_function("bot", "x"),
    ):
        assert "\u2014" not in words and "\u2013" not in words
        assert set(re.findall(r"`([a-z_]+)`", words)) <= everything
        assert words.endswith("Nothing was changed.")


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


# -- M37: keys that call Vrum by running code -------------------------------------------------


class KeyList:
    """Keys by name: how each calls Vrum, and what it may call."""

    def __init__(self, **held: tuple[frozenset[str] | None, bool]) -> None:
        self.held = {
            f"vrk_{name}": KeyAccess(7, index, name, tools, by_code)
            for index, (name, (tools, by_code)) in enumerate(held.items())
        }

    async def project_for_key(self, key: str) -> KeyAccess | None:
        return self.held.get(key)


def server_with_keys(**held: tuple[frozenset[str] | None, bool]) -> Any:
    server: Any = create_server(Settings.from_env(ENV))
    server.accounts = KeyList(**held)
    return server


def listed(server: Any, key: str) -> list[Any]:
    found: list[Any] = asyncio.run(server._handle_list_tools(sending(key), None)).tools
    return found


def called(server: Any, key: str, tool: str, arguments: dict[str, Any] | None = None) -> Any:
    return asyncio.run(server.call_tool(tool, arguments or {}, sending(key)))


def test_a_key_that_runs_code_lists_exactly_explore_and_run_whatever_it_may_call() -> None:
    server = server_with_keys(
        standard=(None, True),
        own=(frozenset({"list_nodes", "add_note"}), True),
        tools=(None, False),
    )
    assert [tool.name for tool in listed(server, "vrk_standard")] == ["explore", "run"]
    assert [tool.name for tool in listed(server, "vrk_own")] == ["explore", "run"]
    # A key that calls one tool at a time sees what it saw before, and never the two.
    assert {tool.name for tool in listed(server, "vrk_tools")} == AGENT_SURFACE


def test_a_tools_key_never_lists_explore_or_run_even_if_its_stored_list_names_them() -> None:
    server = server_with_keys(odd=(frozenset({"list_nodes", "run", "explore"}), False))
    assert [tool.name for tool in listed(server, "vrk_odd")] == ["list_nodes"]


def test_run_is_described_for_the_key_with_its_own_functions_and_explore_is_the_same_for_all() -> (
    None
):
    server = server_with_keys(
        standard=(None, True), small=(frozenset({"list_nodes", "add_note"}), True)
    )
    standard = {tool.name: tool for tool in listed(server, "vrk_standard")}
    small = {tool.name: tool for tool in listed(server, "vrk_small")}
    assert standard["explore"].description == small["explore"].description
    assert standard["explore"].annotations.read_only_hint is True
    big, little = standard["run"].description, small["run"].description
    assert "Functions that only read: changes_since, get_node, list_nodes" in big
    assert "next_work" in big and "Signatures:" in big
    assert "Functions that only read: list_nodes." in little
    assert "Functions that change something: add_note." in little
    assert re.findall(r"^  (\w+)\(", little, re.M) == ["list_nodes", "add_note"]
    assert "next_work" not in little and "export_graph" not in little
    assert little.index("Signatures:") < len(little) and len(little) < len(big)
    # The tool's own description (what the board shows) is general and names no key's functions.
    general = server_tools()["run"].description
    assert "list_nodes" not in general and "next_work" not in general


def test_each_way_of_calling_is_refused_the_others_tools_in_words() -> None:
    server = server_with_keys(runner=(None, True), plain=(None, False))
    for tool in ("explore", "run"):
        with pytest.raises(ToolError) as refused:
            called(server, "vrk_plain", tool)
        assert str(refused.value) == (
            f"Error executing tool {tool}: " + code_tool_for_a_tools_key("plain", tool)
        )
    assert "calls one tool at a time" in code_tool_for_a_tools_key("plain", "run")
    assert "Users page" in code_tool_for_a_tools_key("plain", "run")
    for tool in ("list_nodes", "next_work"):
        with pytest.raises(ToolError) as refused:
            called(server, "vrk_runner", tool)
        assert str(refused.value) == (
            f"Error executing tool {tool}: " + tool_by_name_for_a_code_key("runner", tool)
        )
    assert "call it as vrum.list_nodes(...) in run" in tool_by_name_for_a_code_key(
        "r", "list_nodes"
    )


def test_a_key_that_runs_code_is_still_refused_what_its_allowance_does_not_give_it() -> None:
    server = server_with_keys(standard=(None, True), reader=(frozenset({"list_nodes"}), True))
    with pytest.raises(ToolError) as refused:
        called(server, "vrk_standard", "update_nodes")
    assert str(refused.value) == "Error executing tool update_nodes: " + tool_not_offered(
        "standard", "update_nodes"
    )
    with pytest.raises(ToolError) as refused:
        called(server, "vrk_reader", "create_node")
    assert str(refused.value) == "Error executing tool create_node: " + key_not_allowed(
        "reader", "create_node"
    )
    with pytest.raises(ToolError) as unknown:
        called(server, "vrk_reader", "no_such_tool")
    assert "not allowed" not in str(unknown.value) and "no_such_tool" in str(unknown.value)


def test_a_key_that_runs_code_can_explore_and_gets_only_its_own_functions() -> None:
    server = server_with_keys(
        small=(frozenset({"list_nodes", "add_note"}), True), standard=(None, True)
    )
    everything = called(server, "vrk_small", "explore").structured_content
    assert [line["name"] for line in everything["functions"]] == ["list_nodes", "add_note"]
    assert everything["declarations"] == ""
    asked = called(server, "vrk_small", "explore", {"names": ["add_note", "get_node"]})
    content = asked.structured_content
    assert [line["name"] for line in content["functions"]] == ["add_note"]
    assert content["not_functions"] == ["get_node"]
    assert (
        "add_note(args: {" in content["declarations"] and "get_node" not in content["declarations"]
    )
    standard = called(server, "vrk_standard", "explore").structured_content
    assert [line["name"] for line in standard["functions"]] == in_order(AGENT_SURFACE)


def test_the_board_can_try_explore_as_a_person_and_cannot_run() -> None:
    server = server_with_keys()
    with working_for(7), working_as(person("Sam")):
        found = asyncio.run(server.call_tool("explore", {})).structured_content
        with pytest.raises(ToolError) as refused:
            asyncio.run(server.call_tool("run", {"code": "return 1"}))
    assert [line["name"] for line in found["functions"]] == in_order(AGENT_SURFACE)
    assert str(refused.value) == "Error executing tool run: " + RUN_IS_NOT_FOR_THE_BOARD


def test_a_key_that_is_changed_to_run_code_is_a_different_kind_of_caller_on_its_next_call() -> None:
    server = server_with_keys(flip=(None, False))
    assert {tool.name for tool in listed(server, "vrk_flip")} == AGENT_SURFACE
    server.accounts.held["vrk_flip"] = KeyAccess(7, 0, "flip", None, True)
    assert [tool.name for tool in listed(server, "vrk_flip")] == ["explore", "run"]
    server.accounts.held["vrk_flip"] = KeyAccess(7, 0, "flip", None, False)
    assert {tool.name for tool in listed(server, "vrk_flip")} == AGENT_SURFACE


# -- the guard's own call for a script ---------------------------------------------------------


def server_with_functions() -> tuple[Any, dict[str, int]]:
    """A server with a few small tools of its own, none of which needs the database, so that the
    guard's way of answering a script's call can be tried alone."""
    server: Any = create_server(Settings.from_env(ENV))
    ran = {"shout": 0}

    async def echo_list(n: int) -> list[str]:
        """Give a list: the library wraps it as a result."""
        return ["a"] * n

    async def shout(text: str) -> dict[str, str]:
        """Say a text loudly."""
        ran["shout"] += 1
        return {"text": text.upper()}

    async def refuses() -> dict[str, int]:
        """Always refuses."""
        raise Refusal("Nobody may do this. Nothing was changed.")

    async def crashes() -> dict[str, int]:
        """Always breaks."""
        raise RuntimeError("secret inner detail")

    async def whoami() -> dict[str, Any]:
        """Say who is asking, for which project."""
        who = current_identity()
        return {
            "name": who.name,
            "kind": who.kind,
            "project": current_project(),
            "caller": current_caller().who.name,
        }

    for tool in (echo_list, shout, refuses, crashes, whoami):
        server.add_tool(tool)
    return server, ran


def a_scripts_caller(*tools: str, standard: bool = False) -> Caller:
    return Caller(7, agent("bot"), frozenset(tools), standard, True, 5)


TAG = RunTag("abc123def456", 3, "0123456789ab")


class Unchanged:
    """The key of a script's caller as it was when the script started: nothing has been changed."""

    def __init__(self, caller: Caller) -> None:
        self.caller = caller

    async def access_of_key(self, key_id: int) -> KeyAccess | None:
        found = self.caller
        tools = None if found.standard else found.tools
        return KeyAccess(found.project, key_id, found.who.name, tools, True)


def inside(server: Any, caller: Caller, name: str, arguments: dict[str, Any] | None = None) -> Any:
    server.accounts = Unchanged(caller)
    return asyncio.run(server.call_inner(caller, name, arguments or {}, TAG))


def test_a_scripts_call_gets_the_plain_result_with_the_librarys_wrapper_taken_off() -> None:
    server, _ = server_with_functions()
    caller = a_scripts_caller("echo_list", "shout", "whoami")
    assert inside(server, caller, "echo_list", {"n": 2}) == ["a", "a"]
    assert inside(server, caller, "shout", {"text": "hi"}) == {"text": "HI"}
    # It is the key's project and the key's name that the tool sees, and nothing else.
    assert inside(server, caller, "whoami") == {
        "name": "bot",
        "kind": "agent",
        "project": 7,
        "caller": "bot",
    }
    assert server.sdk.has("echo_list") and not server.sdk.has("explore")


def test_a_scripts_call_is_refused_in_the_tools_own_sentence_and_a_crash_is_not_explained() -> None:
    server, _ = server_with_functions()
    caller = a_scripts_caller("refuses", "crashes", "shout")
    with pytest.raises(ToolError) as refused:
        inside(server, caller, "refuses")
    assert (
        str(refused.value)
        == "Error executing tool refuses: Nobody may do this. Nothing was changed."
    )
    with pytest.raises(UnexpectedToolError) as crashed:
        inside(server, caller, "crashes")
    assert str(crashed.value) == "Error executing tool crashes" and "secret" not in str(
        crashed.value
    )
    with pytest.raises(ToolError) as invalid:
        inside(server, caller, "shout", {"text": 5, "other": "x"})
    assert "the arguments are not valid (text:" in str(invalid.value)
    assert "Nothing was changed" in str(invalid.value) and "input_value" not in str(invalid.value)


def test_a_scripts_call_is_checked_against_the_keys_allowance_by_the_usual_words() -> None:
    server, ran = server_with_functions()
    limited = a_scripts_caller("echo_list")
    with pytest.raises(ToolError) as refused:
        inside(server, limited, "shout", {"text": "x"})
    assert str(refused.value) == "Error executing tool shout: " + key_not_allowed("bot", "shout")
    standard = a_scripts_caller("echo_list", standard=True)
    with pytest.raises(ToolError) as refused:
        inside(server, standard, "shout", {"text": "x"})
    assert str(refused.value) == "Error executing tool shout: " + tool_not_offered("bot", "shout")
    assert ran["shout"] == 0


def test_a_script_cannot_call_explore_run_a_person_only_tool_or_a_made_up_name() -> None:
    server, _ = server_with_functions()
    caller = a_scripts_caller("echo_list", "explore", "run", "set_confirmation")
    for name in ("explore", "run", "no_such_function"):
        with pytest.raises(ToolError) as refused:
            inside(server, caller, name)
        assert str(refused.value) == f"Error executing tool {name}: " + no_such_function(
            "bot", name
        )
    with pytest.raises(ToolError) as person_only:
        inside(server, caller, "set_confirmation", {"note_id": 1, "confirmed": True})
    assert str(person_only.value) == "Error executing tool set_confirmation: " + key_not_allowed(
        "bot", "set_confirmation"
    )
    # A name that does not look like a tool's is never repeated, not even in the prefix.
    with pytest.raises(ToolError) as odd:
        inside(server, caller, "x; drop table nodes -- <b>")
    assert "drop table" not in str(odd.value) and "that call" in str(odd.value)
    assert "that name" in str(odd.value)


def test_only_a_key_that_runs_code_has_functions_not_a_tools_key_and_not_the_board() -> None:
    server, _ = server_with_functions()
    plain = Caller(7, agent("bot"), frozenset({"echo_list"}), False, False, 5)
    board = Caller(7, person("Sam"), None, False, False)
    # A caller that runs code but has no key number is not a script's either.
    nameless = Caller(7, agent("bot"), frozenset({"echo_list"}), False, True)
    for caller in (plain, board, nameless):
        with pytest.raises(ToolError) as refused:
            inside(server, caller, "echo_list", {"n": 1})
        assert str(refused.value) == "Error executing tool echo_list: " + no_such_function(
            caller.who.name, "echo_list"
        )


class Moving:
    """The keys of a project as the owner changes them while a script runs: what the key's
    number gives now, and how many times it was asked."""

    def __init__(self, now: KeyAccess | KeyExpired | None) -> None:
        self.now = now
        self.asked = 0

    async def access_of_key(self, key_id: int) -> KeyAccess | None:
        self.asked += 1
        if isinstance(self.now, KeyExpired):
            raise self.now
        return self.now


def key_now(**more: Any) -> KeyAccess:
    return KeyAccess(7, 5, "bot", more.get("tools"), more.get("code", True))


def test_the_key_is_read_again_at_every_call_and_a_function_it_lost_is_refused() -> None:
    server, ran = server_with_functions()
    caller = a_scripts_caller("echo_list", "shout")
    keys = Moving(key_now(tools=frozenset({"echo_list", "shout"})))
    server.accounts = keys
    for _ in range(3):
        assert asyncio.run(server.call_inner(caller, "echo_list", {"n": 1}, TAG)) == ["a"]
    assert keys.asked == 3
    # The owner takes shout away while the script runs: that call is refused in the usual words,
    # the script is not stopped, and a function it still has works.
    keys.now = key_now(tools=frozenset({"echo_list"}))
    with pytest.raises(ToolError) as refused:
        asyncio.run(server.call_inner(caller, "shout", {"text": "x"}, TAG))
    assert str(refused.value) == "Error executing tool shout: " + key_not_allowed("bot", "shout")
    assert not isinstance(refused.value, KeyStopped) and ran["shout"] == 0
    assert asyncio.run(server.call_inner(caller, "echo_list", {"n": 2}, TAG)) == ["a", "a"]
    # Back to the standard tools (no list of its own): the words are the standard ones.
    keys.now = key_now(tools=None)
    with pytest.raises(ToolError) as standard:
        asyncio.run(server.call_inner(caller, "shout", {"text": "x"}, TAG))
    assert str(standard.value) == "Error executing tool shout: " + tool_not_offered("bot", "shout")


def test_a_function_the_owner_adds_while_a_script_runs_is_not_one_of_its_functions() -> None:
    server, ran = server_with_functions()
    caller = a_scripts_caller("echo_list")
    server.accounts = Moving(key_now(tools=frozenset({"echo_list", "shout"})))
    with pytest.raises(ToolError) as refused:
        asyncio.run(server.call_inner(caller, "shout", {"text": "x"}, TAG))
    assert str(refused.value) == "Error executing tool shout: " + key_not_allowed("bot", "shout")
    assert ran["shout"] == 0


def test_a_revoked_key_or_one_changed_to_tools_stops_the_script_before_its_call_runs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server, ran = server_with_functions()
    caplog.set_level(logging.INFO, logger="vrum.tools")
    caller = a_scripts_caller("shout")
    for now, words in (
        (None, SCRIPT_KEY_REVOKED),
        (KeyAccess(8, 5, "bot", None, True), SCRIPT_KEY_REVOKED),
        (key_now(tools=frozenset({"shout"}), code=False), SCRIPT_KEY_NOW_TOOLS),
    ):
        server.accounts = Moving(now)
        with pytest.raises(KeyStopped) as stopped:
            asyncio.run(server.call_inner(caller, "shout", {"text": "x"}, TAG))
        assert str(stopped.value) == words
    assert ran["shout"] == 0
    lines = [record.__dict__["fields"] for record in caplog.records if hasattr(record, "fields")]
    assert [(line["outcome"], line["reason"], line["via"]) for line in lines] == [
        ("refused", "key_ended", "run")
    ] * 3
    assert "vrk_" not in str(lines)
    # The words say the key is not valid and that the script was stopped, and what to do.
    assert "revoked while its script was running" in SCRIPT_KEY_REVOKED
    assert "Users, then Agents" in SCRIPT_KEY_REVOKED and "stays done" in SCRIPT_KEY_REVOKED
    assert "one tool at a time" in SCRIPT_KEY_NOW_TOOLS and "Users page" in SCRIPT_KEY_NOW_TOOLS
    for words in (SCRIPT_KEY_REVOKED, SCRIPT_KEY_NOW_TOOLS):
        assert "\u2014" not in words and "\u2013" not in words


def test_a_key_whose_last_day_passes_stops_the_script_in_words_before_its_call_runs() -> None:
    server, ran = server_with_functions()
    caller = a_scripts_caller("shout")
    server.accounts = Moving(KeyExpired(date(2026, 11, 30)))
    with pytest.raises(KeyStopped) as stopped:
        asyncio.run(server.call_inner(caller, "shout", {"text": "x"}, TAG))
    words = str(stopped.value)
    assert words == script_key_expired_words(date(2026, 11, 30))
    assert "expired after 2026-11-30" in words and "stays done" in words
    assert "Users, then Agents" in words and "\u2014" not in words and "\u2013" not in words
    assert ran["shout"] == 0


def test_the_key_is_read_by_its_number_and_never_by_the_key() -> None:
    caller = a_scripts_caller("shout")
    assert caller.key_id == 5
    assert "vrk_" not in repr(caller)


def test_a_script_is_not_stopped_by_a_key_that_is_not_one_it_runs_for() -> None:
    # A caller that does not run code is told it has no functions, and the keys are not asked.
    server, _ = server_with_functions()
    keys = Moving(None)
    server.accounts = keys
    plain = Caller(7, agent("bot"), frozenset({"echo_list"}), False, False, 5)
    with pytest.raises(ToolError) as refused:
        asyncio.run(server.call_inner(plain, "echo_list", {"n": 1}, TAG))
    assert not isinstance(refused.value, KeyStopped) and keys.asked == 0


def test_a_secret_in_a_scripts_call_is_refused_before_the_tool_runs() -> None:
    server, ran = server_with_functions()
    caller = a_scripts_caller("shout")
    with pytest.raises(ToolError) as refused:
        inside(server, caller, "shout", {"text": "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2})
    assert str(refused.value).startswith("Error executing tool shout: The text field contains")
    assert "ghp_" not in str(refused.value) and ran["shout"] == 0


def test_a_scripts_call_leaves_a_line_with_the_run_and_the_call_and_nothing_that_was_said(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server, _ = server_with_functions()
    caplog.set_level(logging.INFO, logger="vrum.tools")
    caller = a_scripts_caller("shout", "refuses")
    inside(server, caller, "shout", {"text": "private-words-in-the-arguments"})
    with pytest.raises(ToolError):
        inside(server, caller, "refuses")
    with pytest.raises(ToolError):
        inside(server, caller, "set_confirmation")
    lines = [record.fields for record in caplog.records if hasattr(record, "fields")]
    assert [(line["tool"], line["outcome"]) for line in lines] == [
        ("shout", "ok"),
        ("refuses", "refused"),
        ("set_confirmation", "refused"),
    ]
    first = lines[0]
    assert (first["via"], first["run_id"], first["seq"], first["script"]) == (
        "run",
        "abc123def456",
        3,
        "0123456789ab",
    )
    assert (first["actor"], first["actor_kind"], first["project"]) == ("bot", "agent", 7)
    assert lines[2]["reason"] == "not_allowed"
    assert "private-words" not in str(lines) and "HIDDEN" not in str(lines)
    # A call over the network has no run, so its line is as it always was.
    assert "via" not in lines[0] or lines[0]["via"] == "run"


# -- what a key is and may be given -----------------------------------------------------------


def test_a_key_is_made_to_run_code_with_a_true_or_false_and_nothing_else_is_taken_for_either() -> (
    None
):
    assert clean_code_mode(True) is True and clean_code_mode(False) is False
    wrong: Any
    for wrong in (None, 1, 0, "true", "yes", [], {}):
        with pytest.raises(AccessRefusal) as refused:
            clean_code_mode(wrong)
        assert str(refused.value) == (
            "How a key calls Vrum has to be true (it runs code) or false (one tool at a time). "
            "Nothing was changed."
        )


def test_explore_and_run_are_not_ticked_one_by_one_for_a_key() -> None:
    known = {"list_nodes", "explore", "run"}
    for tools in (["explore"], ["list_nodes", "run"], ["run", "explore"]):
        with pytest.raises(AccessRefusal) as refused:
            clean_allowance(tools, known)
        assert str(refused.value).startswith("explore and run are what a key that calls Vrum by")
        assert "Nothing was changed." in str(refused.value)
    assert clean_allowance(["list_nodes"], known) == ["list_nodes"]
    assert KeyAccess(1, 1, "a", None).code_mode is False
    assert tools_of(KeyAccess(1, 1, "a", None, True)) == AGENT_SURFACE


# -- the tools marked as reads really only read -------------------------------------------------
# A script's calls to a tool marked read only skip the write lock and the cap on writes, and the
# board lets a viewer call exactly these, so a tool that is marked and writes would get round both.
# The list below is what was read through, one tool at a time, on 2026-11-21: a tool that is newly
# marked as a read fails the first test until someone reads it through and adds it, and the second
# follows every call each one makes into the code that does the work and fails on a write.

REVIEWED_READS = frozenset(
    {
        "server_status",
        "get_node",
        "list_nodes",
        "node_history",
        "list_links",
        "ready_work",
        "who_is_working",
        "list_asks",
        "needs_a_person",
        "list_plans",
        "list_plan_templates",
        "suggest_parent",
        "list_placements",
        "changes_since",
        "get_rule",
        "list_rules",
        "rule_history",
        "coverage_report",
        "get_definition_of_done",
        "get_note",
        "list_notes",
        "recall",
        "note_history",
        "search",
        "similar_items",
        "find_overlaps",
        "find_mentions",
        "export_graph",
        "explore",
    }
)

# What the server's tools call to do their work, by the name each is held under in create_server.
OWNERS: dict[str, type] = {
    "graph": Graph,
    "links": Links,
    "claims": Claims,
    "rules": Rules,
    "definition_of_done": DefinitionOfDoneBook,
    "checks": Checks,
    "notes": Notes,
    "exchange": Exchange,
    "indexer": Indexer,
    "searcher": Searcher,
    "recaller": Recaller,
    "status": Status,
    "references": References,
    "workflow": Workflow,
    "placement": Placement,
    "placements": PlacementBook,
}
# What writes: SQL that changes rows, the event log, the counters, a lock taken to change a row (a
# `FOR UPDATE` is only written where a function is told `lock=True`, so that is what is looked for).
A_WRITE = re.compile(
    r"\b(INSERT|DELETE|TRUNCATE|DROP|ALTER|CREATE|COPY)\b|(?<!FOR )\bUPDATE\b|record_event|next_id"
    r"|nextval|setval|executemany|lock=True|\.reindex\("
)


def writes_reached_by(tool: Any) -> list[str]:
    """Where in the code a tool's calls lead to something that writes, as a path of names, found
    by following every call it makes to the owners of the graph and the functions they use."""
    found: list[str] = []
    seen: set[Any] = set()

    def visit(function: Any, path: list[str]) -> None:
        function = inspect.unwrap(function)
        if function in seen:
            return
        seen.add(function)
        try:
            source = inspect.getsource(function)
        except (OSError, TypeError):
            return
        where = " > ".join(path)
        found.extend(f"{where}: {hit.group(0)}" for hit in A_WRITE.finditer(source))
        module = sys.modules.get(function.__module__)
        for word in set(re.findall(r"\b[A-Za-z_]\w*\b", source)):
            value = getattr(module, word, None)
            if isinstance(value, str) and len(value) > 20:
                found.extend(f"{where} > {word}: {hit.group(0)}" for hit in A_WRITE.finditer(value))
            elif inspect.isfunction(value) and value.__module__.startswith("vrum."):
                visit(value, [*path, word])
        for owner, method in re.findall(r"\b(\w+)\.(\w+)\(", source):
            if owner in OWNERS:
                classes = [OWNERS[owner]]
            else:
                # self.method(...) in the same class, or self._other.method(...) in another owner
                classes = [one for one in OWNERS.values() if method in one.__dict__]
            for owning in classes:
                if method in owning.__dict__:
                    visit(owning.__dict__[method], [*path, f"{owning.__name__}.{method}"])

    visit(tool.fn, [tool.name])
    return found


def test_the_tools_marked_as_reads_are_exactly_the_ones_that_were_read_through() -> None:
    tools = {tool.name: tool for tool in the_server_tools()}
    marked = {
        name for name, tool in tools.items() if tool.annotations and tool.annotations.read_only_hint
    }
    assert marked == REVIEWED_READS, (
        f"marked as a read and not reviewed: {sorted(marked - REVIEWED_READS)}; "
        f"reviewed and no longer marked: {sorted(REVIEWED_READS - marked)}"
    )
    for name in marked:
        assert not tools[name].annotations.destructive_hint, name
    # A read is a function of a script that goes without the write lock and the write cap, and
    # the guard's own idea of a read is the tool's mark and nothing else.
    sdk = the_server().sdk
    assert {name for name in marked if name != "explore"} == {
        name for name in sdk.names if sdk.reads_only(name)
    }
    # And nothing only a person may do is among them.
    assert marked.isdisjoint(PEOPLE_ONLY_TOOLS)


def test_no_tool_marked_as_a_read_reaches_code_that_writes() -> None:
    tools = {tool.name: tool for tool in the_server_tools()}
    problems = {}
    for name in sorted(REVIEWED_READS - {"explore"}):
        hits = writes_reached_by(tools[name])
        if hits:
            problems[name] = hits[:3]
    assert problems == {}
    # explore is not a graph tool: it builds text from the tools' schemas and touches no owner.
    explore_source = inspect.getsource(tools["explore"].fn)
    assert not re.findall(r"\b(graph|links|claims|rules|notes|exchange|workflow)\.", explore_source)
    # The scan can see a write: a tool that is not a read is found to reach one.
    assert writes_reached_by(tools["create_node"]) and writes_reached_by(tools["add_note"])
    assert writes_reached_by(tools["claim_node"]) and writes_reached_by(tools["set_check"])
