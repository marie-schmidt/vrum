"""Small helpers for tests that talk to the running containers: through a real MCP client, or
straight to the database."""

import os
import random
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest
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


_NODE_EXTRAS = {"similar", "similar_checked", "similar_note", "handoff_count", "last_handoff"}


def node_core(node: dict[str, Any]) -> dict[str, Any]:
    """A node without what only some tools add (the similar items of a new one, the handoff notes
    of a read one), so that what create_node and get_node say about it can be compared."""
    return {name: value for name, value in node.items() if name not in _NODE_EXTRAS}


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


async def make_stories(client: Client, count: int) -> list[dict[str, Any]]:
    """`count` stories under one new epic."""
    epic = await ok(client, "create_node", actor="marie", kind="epic", title="An epic")
    return [
        await ok(
            client,
            "create_node",
            actor="marie",
            kind="story",
            title=f"Story {number}",
            parent_id=epic["id"],
        )
        for number in range(count)
    ]


async def make_rule(client: Client, title: str = "A rule", **arguments: Any) -> dict[str, Any]:
    return await ok(client, "create_rule", actor="marie", title=title, **arguments)


async def whole_coverage_report(client: Client) -> dict[str, Any]:
    """The coverage report with both of its lists read to the end."""
    report = await ok(client, "coverage_report", show="unplaced_rules", limit=200)
    unplaced = list(report["unplaced_rules"])
    while report["next_unplaced_after_id"] is not None:
        report = await ok(
            client,
            "coverage_report",
            show="unplaced_rules",
            limit=200,
            unplaced_after_id=report["next_unplaced_after_id"],
        )
        unplaced.extend(report["unplaced_rules"])
    report = await ok(client, "coverage_report", show="stories_without_rules", limit=200)
    stories = list(report["stories_without_rules"])
    while report["next_stories_after_id"] is not None:
        report = await ok(
            client,
            "coverage_report",
            show="stories_without_rules",
            limit=200,
            stories_after_id=report["next_stories_after_id"],
        )
        stories.extend(report["stories_without_rules"])
    return {
        "unplaced_rules": unplaced,
        "stories_without_rules": stories,
        "unplaced_rule_count": report["unplaced_rule_count"],
        "story_without_rules_count": report["story_without_rules_count"],
    }


REPO_ROOT = Path(__file__).resolve().parent.parent


def compose(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run docker compose against the stack ./check.sh started (it names the project)."""
    if not os.environ.get("COMPOSE_PROJECT_NAME"):
        pytest.fail(
            "COMPOSE_PROJECT_NAME is not set. Run the integration tests through ./check.sh."
        )
    return subprocess.run(
        ["docker", "compose", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


def run_sql(sql: str, *, plain: bool = False) -> subprocess.CompletedProcess[str]:
    """Run SQL directly inside the real database. `plain` prints bare values, nothing else."""
    flags = ["-tA"] if plain else []
    return compose(
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        "vrum",
        "-d",
        "vrum",
        "-v",
        "ON_ERROR_STOP=1",
        *flags,
        "-c",
        sql,
    )


async def race_mixed(
    client: Client, calls: list[tuple[str, dict[str, Any]]]
) -> list[tuple[bool, str]]:
    """Make all the calls, of any tools, at the same moment. For each: was it accepted, and what
    did it say."""
    results: list[tuple[bool, str]] = []

    async def call(tool: str, arguments: dict[str, Any]) -> None:
        result = await client.call_tool(tool, arguments)
        results.append((not result.is_error, _text(result)))

    async with anyio.create_task_group() as group:
        for tool, arguments in calls:
            group.start_soon(call, tool, arguments)
    return results


async def race_results(
    client: Client, tool: str, calls: list[dict[str, Any]]
) -> list[tuple[bool, str]]:
    """Make all the calls at the same moment. For each: was it accepted, and what did it say."""
    return await race_mixed(client, [(tool, arguments) for arguments in calls])


async def race(client: Client, tool: str, calls: list[dict[str, Any]]) -> list[bool]:
    """Make all the calls at the same moment and say which of them were accepted."""
    return [accepted for accepted, _ in await race_results(client, tool, calls)]


async def make_decision(
    client: Client, title: str = "A decision", **arguments: Any
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "actor": "agent-1",
        "title": title,
        "text": f"{title}: what was decided",
        "reason": "Because",
        "decided_by": "Marie",
    }
    return await ok(client, "record_decision", **{**fields, **arguments})


def day(offset: int = 0) -> str:
    """A day written as YYYY-MM-DD, `offset` days from today (UTC, as the database counts it)."""
    return (datetime.now(UTC).date() + timedelta(days=offset)).isoformat()


async def make_fact(client: Client, text: str = "A fact", **arguments: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {"actor": "agent-1", "text": text, "source": "The team call"}
    return await ok(client, "retain_fact", **{**fields, **arguments})


async def make_belief(
    client: Client, evidence: list[dict[str, Any]], text: str = "A belief", **arguments: Any
) -> dict[str, Any]:
    """A belief resting on the given facts."""
    fields: dict[str, Any] = {
        "actor": "agent-1",
        "text": text,
        "evidence_fact_ids": [fact["id"] for fact in evidence],
    }
    return await ok(client, "record_belief", **{**fields, **arguments})


def unique_word() -> str:
    """A made-up word that appears nowhere else, so a search for it finds only what a test made.
    Consonants only, so that neither the stemmer nor the tokenizer can change it."""
    return "zq" + "".join(random.choices("bcdfghjklmnpqrtvwxz", k=10))


@contextmanager
def embeddings_stopped() -> Iterator[None]:
    """The scripted embedding service is stopped inside the block and started again after it."""
    stopped = compose("stop", "scripted-embeddings")
    assert stopped.returncode == 0, stopped.stderr
    try:
        yield
    finally:
        started = compose("up", "--detach", "--wait", "scripted-embeddings")
        assert started.returncode == 0, started.stderr


async def reindexed(client: Client) -> dict[str, Any]:
    """Run the reindex job now, and check that nothing is left waiting."""
    report = await ok(client, "reindex")
    assert report["error"] is None and report["failed"] == [], report
    assert report["waiting_after"] == 0, report
    return report
