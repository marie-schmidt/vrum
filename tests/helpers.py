"""Small helpers for tests that talk to the running server through a real MCP client."""

from typing import Any

from mcp import Client
from mcp.types import CallToolResult, TextContent


def _text(result: CallToolResult) -> str:
    return " ".join(block.text for block in result.content if isinstance(block, TextContent))


async def ok(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    """Call a tool that must succeed, and return what it returned."""
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, f"{tool} was refused: {_text(result)}"
    assert result.structured_content is not None
    return dict(result.structured_content)


async def refused(client: Client, tool: str, **arguments: Any) -> str:
    """Call a tool that must be refused, and return the refusal as the agent reads it."""
    result = await client.call_tool(tool, arguments)
    assert result.is_error, (
        f"{tool} should have been refused but returned {result.structured_content}"
    )
    return _text(result)


async def all_nodes(client: Client, **filters: Any) -> list[dict[str, Any]]:
    """Every node matching the filters, following the pages."""
    found: list[dict[str, Any]] = []
    after_id = None
    while True:
        page = await ok(client, "list_nodes", limit=200, after_id=after_id, **filters)
        found.extend(page["items"])
        after_id = page["next_after_id"]
        if after_id is None:
            return found


async def make_tree(client: Client) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """An epic, a story under it and a task under that, made by different actors."""
    epic = await ok(client, "create_node", actor="marie", kind="epic", title="An epic")
    story = await ok(
        client, "create_node", actor="marie", kind="story", title="A story", parent_id=epic["id"]
    )
    task = await ok(
        client, "create_node", actor="agent-1", kind="task", title="A task", parent_id=story["id"]
    )
    return epic, story, task


async def make_tasks(client: Client, count: int) -> list[dict[str, Any]]:
    """`count` tasks under one new story, so each test works on nodes of its own."""
    epic = await ok(client, "create_node", actor="marie", kind="epic", title="An epic")
    story = await ok(
        client, "create_node", actor="marie", kind="story", title="A story", parent_id=epic["id"]
    )
    return [
        await ok(
            client,
            "create_node",
            actor="marie",
            kind="task",
            title=f"Task {number}",
            parent_id=story["id"],
        )
        for number in range(count)
    ]


async def needs_first(client: Client, waiting: dict[str, Any], first: dict[str, Any]) -> None:
    await ok(
        client,
        "add_link",
        actor="marie",
        source_id=waiting["id"],
        target_id=first["id"],
        link_type="needs_first",
    )


async def set_status(client: Client, node: dict[str, Any], status: str) -> dict[str, Any]:
    current = await ok(client, "get_node", node_id=node["id"])
    return await ok(
        client,
        "update_node",
        actor="marie",
        node_id=node["id"],
        expected_version=current["version"],
        status=status,
    )
