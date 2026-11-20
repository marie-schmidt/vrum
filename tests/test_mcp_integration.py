"""Tests against the running containers, through a real MCP client (see ./check.sh)."""

import urllib.request
from typing import Any

import pytest
from mcp import Client

from tests.world import the_project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

# The tools of the product: work, acceptance criteria, definition of done, search, notes and
# confirmation.
WORK = {
    "server_status",
    "create_node",
    "get_node",
    "list_nodes",
    "update_node",
    "move_node",
    "node_history",
    "add_link",
    "remove_link",
    "list_links",
    "ready_work",
    "claim_node",
    "renew_claim",
    "release_claim",
    "who_is_working",
    "next_work",
    "finish_work",
    "create_plan",
    "update_nodes",
    "changes_since",
}
CRITERIA = {
    "create_rule",
    "get_rule",
    "list_rules",
    "update_rule",
    "place_rule",
    "rule_history",
    "coverage_report",
}
FINDING = {
    "search",
    "similar_items",
    "find_overlaps",
    "find_mentions",
    "reindex",
    "export_graph",
    "import_graph",
    "recall",
}
NOTES = {
    "add_note",
    "get_note",
    "list_notes",
    "link_note",
    "unlink_note",
    "supersede_note",
    "withdraw_note",
    "note_history",
}
CONFIRMATION = {"set_confirmation"}
DEFINITION = {"get_definition_of_done", "set_definition_of_done", "set_check"}
# The tools only a person may use: no key is offered them.
PEOPLE_ONLY = CONFIRMATION | {"set_definition_of_done"}

# Tools that only read: the board's Try it may run exactly these.
READS = {
    "server_status",
    "get_node",
    "list_nodes",
    "node_history",
    "list_links",
    "ready_work",
    "who_is_working",
    "changes_since",
    "get_rule",
    "list_rules",
    "rule_history",
    "coverage_report",
    "get_definition_of_done",
    "search",
    "similar_items",
    "find_overlaps",
    "find_mentions",
    "export_graph",
    "recall",
    "get_note",
    "list_notes",
    "note_history",
}

# What the product no longer has: server-side generation, beliefs, principles and the memory tools,
# decisions and facts as two kinds of record (they are notes now), and the tool that moved a
# requirement (place_rule places or moves).
GONE = {
    "extract_facts",
    "consolidate_memory",
    "find_contradictions",
    "reflect",
    "record_belief",
    "supersede_memory",
    "retract_memory",
    "confirm_belief",
    "promote_belief",
    "link_memory",
    "unlink_memory",
    "get_memory",
    "list_memories",
    "memory_history",
    "record_decision",
    "retain_fact",
    "get_decision",
    "get_fact",
    "list_decisions",
    "list_facts",
    "link_decision",
    "link_fact",
    "unlink_decision",
    "unlink_fact",
    "supersede_decision",
    "supersede_fact",
    "withdraw_decision",
    "withdraw_fact",
    "decision_history",
    "fact_history",
    "move_rule",
}

EVERY_TOOL = WORK | CRITERIA | FINDING | NOTES | CONFIRMATION | DEFINITION


def tools_on_the_board() -> list[dict[str, Any]]:
    """The server's own list of every tool it has, which the board reads: a key is never offered
    the tool only a person may use, so the client's list is one short of it."""
    reply = the_project().owner.call("GET", "/board/api/tools")
    assert reply.status == 200, reply.body
    return list(reply.body["tools"])


async def test_the_client_lists_the_tools(client: Client) -> None:
    tools = (await client.list_tools()).tools
    names = {tool.name for tool in tools}
    # The key the tests use is allowed every tool a key can have: all but the ones a person uses.
    assert names == EVERY_TOOL - PEOPLE_ONLY
    assert len(tools) == len(names) == 45
    assert not names & GONE
    assert all(tool.description for tool in tools)
    on_the_board = {tool["name"] for tool in tools_on_the_board()}
    assert on_the_board == EVERY_TOOL and len(on_the_board) == 47


async def test_exactly_the_reading_tools_are_marked_as_only_reading(client: Client) -> None:
    tools = (await client.list_tools()).tools
    marked = {
        tool.name
        for tool in tools
        if tool.annotations is not None and tool.annotations.read_only_hint
    }
    assert marked == READS


async def test_the_tools_that_record_something_ask_for_the_reason_and_the_person() -> None:
    schemas = {tool["name"]: tool["input_schema"] for tool in tools_on_the_board()}

    def required(name: str) -> set[str]:
        return set(schemas[name].get("required", []))

    def properties(name: str) -> set[str]:
        return set(schemas[name]["properties"])

    assert required("add_note") == {"text", "why"}
    assert required("supersede_note") == {"replaces", "text", "why"}
    assert required("withdraw_note") == {"note_ids", "reason"}
    assert required("set_confirmation") == {"note_id", "confirmation", "person"}
    # No tool asks who is calling (the server knows from the key), and a note has no title and no
    # source: it has its text and its why.
    for name in ("add_note", "supersede_note", "withdraw_note", "set_confirmation"):
        assert not properties(name) & {"actor", "title", "source", "kind"}
    assert "reason" not in properties("supersede_note")
    # Who a person is and what they said are only ever asked for together with the change.
    for name in ("supersede_note", "withdraw_note"):
        assert {"said_by", "quote"} <= properties(name)
        assert not {"said_by", "quote"} & required(name)
    # Recording a note has no way of saying that it is confirmed: its status is not an argument.
    for name in ("add_note", "supersede_note"):
        assert not properties(name) & {"confirmed_by", "confirmed_at", "confirmation", "status"}
    assert {"decided_by", "decided_on", "project_wide"} <= properties("add_note")
    assert {"decided_by", "decided_on", "project_wide"} <= properties("supersede_note")


async def test_server_status_reports_the_real_database_and_pgvector(client: Client) -> None:
    result = await client.call_tool("server_status", {})
    assert not result.is_error
    status = result.structured_content
    assert status is not None
    assert status["vrum_version"]
    assert status["postgres_version"].startswith("17")
    assert status["pgvector_version"][0].isdigit()


def test_the_health_check_answers_ok_and_says_nothing_else(server_url: str) -> None:
    health_url = server_url.removesuffix("/mcp") + "/health"
    with urllib.request.urlopen(health_url, timeout=5) as response:
        assert response.status == 200
        assert response.read() == b'{"status":"ok"}'
