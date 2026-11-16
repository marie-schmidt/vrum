"""Tests against the running containers, through a real MCP client (see ./check.sh)."""

import urllib.request

import pytest
from mcp import Client

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

# The tools of the product: work, requirements, search, decisions, facts and confirmation.
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
REQUIREMENTS = {
    "create_rule",
    "get_rule",
    "list_rules",
    "update_rule",
    "place_rule",
    "move_rule",
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
DECISIONS = {
    "record_decision",
    "get_decision",
    "list_decisions",
    "link_decision",
    "unlink_decision",
    "supersede_decision",
    "withdraw_decision",
    "decision_history",
}
FACTS = {
    "retain_fact",
    "get_fact",
    "list_facts",
    "link_fact",
    "unlink_fact",
    "supersede_fact",
    "withdraw_fact",
    "fact_history",
}
CONFIRMATION = {"set_confirmation"}

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
    "search",
    "similar_items",
    "find_overlaps",
    "find_mentions",
    "export_graph",
    "recall",
    "get_decision",
    "list_decisions",
    "decision_history",
    "get_fact",
    "list_facts",
    "fact_history",
}

# What the product no longer has: server-side generation, beliefs, principles and the memory tools.
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
}


async def test_the_client_lists_the_tools(client: Client) -> None:
    tools = (await client.list_tools()).tools
    names = {tool.name for tool in tools}
    assert names == WORK | REQUIREMENTS | FINDING | DECISIONS | FACTS | CONFIRMATION
    assert len(tools) == len(names) == 53
    assert not names & GONE
    assert all(tool.description for tool in tools)


async def test_exactly_the_reading_tools_are_marked_as_only_reading(client: Client) -> None:
    tools = (await client.list_tools()).tools
    marked = {
        tool.name
        for tool in tools
        if tool.annotations is not None and tool.annotations.read_only_hint
    }
    assert marked == READS


async def test_the_tools_that_record_something_ask_for_the_source_reason_and_person(
    client: Client,
) -> None:
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    def required(name: str) -> set[str]:
        return set(tools[name].input_schema.get("required", []))

    assert {"actor", "text", "source"} <= required("retain_fact")
    assert {"actor", "title", "text", "reason", "decided_by"} <= required("record_decision")
    assert {"actor", "replaces", "reason", "text", "source"} <= required("supersede_fact")
    assert {"actor", "fact_ids", "reason"} <= required("withdraw_fact")
    assert {"actor", "decision_id", "title", "text", "reason", "decided_by"} <= required(
        "supersede_decision"
    )
    assert {"actor", "decision_id", "reason"} <= required("withdraw_decision")
    assert {"actor", "kind", "record_id", "confirmation", "person"} <= required("set_confirmation")
    # Who a person is and what they said are only ever asked for together with the change.
    for name in ("supersede_fact", "withdraw_fact", "supersede_decision", "withdraw_decision"):
        assert {"said_by", "quote"} <= set(tools[name].input_schema["properties"])
        assert not {"said_by", "quote"} & required(name)
    # Recording a record has no way of saying that it is confirmed.
    for name in ("retain_fact", "record_decision"):
        properties = set(tools[name].input_schema["properties"])
        assert not properties & {"confirmed_by", "confirmed_at", "confirmation"}
    assert "project_wide" in tools["record_decision"].input_schema["properties"]
    assert "project_wide" in tools["supersede_decision"].input_schema["properties"]


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
