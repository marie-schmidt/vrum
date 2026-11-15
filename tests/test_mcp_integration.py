"""Tests against the running containers, through a real MCP client (see ./check.sh)."""

import urllib.request

import pytest
from mcp import Client

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_the_client_lists_the_tools(client: Client) -> None:
    tools = (await client.list_tools()).tools
    assert {tool.name for tool in tools} == {
        "server_status",
        "create_node",
        "get_node",
        "list_nodes",
        "update_node",
        "move_node",
        "node_history",
    }
    assert all(tool.description for tool in tools)


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
