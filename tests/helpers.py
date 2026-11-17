"""Small helpers for tests that talk to the running containers: through a real MCP client, or
straight to the database."""

import os
import random
import subprocess
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import CallToolResult, TextContent


@asynccontextmanager
async def connect(url: str, token: str | None) -> AsyncIterator[Client]:
    """A client of the server at `url` that sends this agent key (or none)."""
    headers = None if token is None else {"Authorization": f"Bearer {token}"}
    async with (
        create_mcp_http_client(headers=headers) as http,
        Client(streamable_http_client(url, http_client=http)) as client,
    ):
        yield client


def the_token() -> str:
    """The agent key of the project almost every test works in (see tests/world.py): it reaches
    that project on the check stack and nothing else."""
    from tests.world import the_project

    return the_project().key


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

# The list tools that page over nodes: they are continued by `after` (the reference of the last
# node of a page) and say `next_after`. Every other list is continued by `after_id`.
NODE_PAGES = {"list_nodes", "ready_work"}

_CODE_LETTER = {"epic": "E", "story": "S", "task": "T"}
_LETTER_KIND = {letter: kind for kind, letter in _CODE_LETTER.items()}


def ref_of(kind: str, number: int) -> str:
    """The reference of an epic, story or task, as every tool and address names it: story:12. Any
    of the six kinds makes the key the board uses for it (rule:3, decision:7, fact:5)."""
    return f"{kind}:{number}"


def kind_of(ref: str) -> str:
    """The kind in a reference: story:12 is a story."""
    return ref.partition(":")[0]


def number_of(ref: str) -> int:
    """The number in a reference (or in the key of any record): story:12 is 12."""
    return int(ref.partition(":")[2])


def code_of(ref: str) -> str:
    """What a person reads for the reference of an epic, story or task: story:12 is S-12."""
    return f"{_CODE_LETTER[kind_of(ref)]}-{number_of(ref)}"


def ref_from_code(code: str) -> str:
    """The reference of what a person reads as a code: S-12 is story:12."""
    letter, _, number = code.strip().partition("-")
    assert letter in _LETTER_KIND and number.isdigit(), code
    return ref_of(_LETTER_KIND[letter], int(number))


def ref_order(ref: str) -> tuple[int, int]:
    """Where a reference sorts: epics, then stories, then tasks, each by number."""
    return list(_CODE_LETTER).index(kind_of(ref)), number_of(ref)


def node_core(node: dict[str, Any]) -> dict[str, Any]:
    """A node without what only some tools add (the similar items of a new one, the handoff notes
    of a read one), so that what create_node and get_node say about it can be compared."""
    return {name: value for name, value in node.items() if name not in _NODE_EXTRAS}


async def all_nodes(client: Client, **filters: Any) -> list[dict[str, Any]]:
    """Every node matching the filters, following the pages (by `after`, a reference)."""
    found: list[dict[str, Any]] = []
    after = None
    while True:
        page = await ok(client, "list_nodes", limit=200, after=after, **filters)
        found.extend(page["items"])
        after = page["next_after"]
        if after is None:
            return found


async def make_tree(client: Client) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """An epic, a story under it and a task under that, made by different actors."""
    epic = await ok(client, "create_node", actor="marie", kind="epic", title="An epic")
    story = await ok(
        client, "create_node", actor="marie", kind="story", title="A story", parent=epic["ref"]
    )
    task = await ok(
        client, "create_node", actor="agent-1", kind="task", title="A task", parent=story["ref"]
    )
    return epic, story, task


async def make_tasks(client: Client, count: int) -> list[dict[str, Any]]:
    """`count` tasks under one new story, so each test works on nodes of its own."""
    epic = await ok(client, "create_node", actor="marie", kind="epic", title="An epic")
    story = await ok(
        client, "create_node", actor="marie", kind="story", title="A story", parent=epic["ref"]
    )
    return [
        await ok(
            client,
            "create_node",
            actor="marie",
            kind="task",
            title=f"Task {number}",
            parent=story["ref"],
        )
        for number in range(count)
    ]


async def needs_first(client: Client, waiting: dict[str, Any], first: dict[str, Any]) -> None:
    await ok(
        client,
        "add_link",
        actor="marie",
        source=waiting["ref"],
        target=first["ref"],
        link_type="needs_first",
    )


async def set_status(client: Client, node: dict[str, Any], status: str) -> dict[str, Any]:
    current = await ok(client, "get_node", node=node["ref"])
    return await ok(
        client,
        "update_node",
        actor="marie",
        node=node["ref"],
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
            parent=epic["ref"],
        )
        for number in range(count)
    ]


async def make_rule(client: Client, title: str = "A rule", **arguments: Any) -> dict[str, Any]:
    """A rule (pass `story=` with the reference of a story to place it)."""
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
    while report["next_stories_after"] is not None:
        report = await ok(
            client,
            "coverage_report",
            show="stories_without_rules",
            limit=200,
            stories_after=report["next_stories_after"],
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


def run_sql(
    sql: str,
    *,
    plain: bool = False,
    project: int | None | bool = True,
    as_project: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run SQL directly inside the real database, as its owner, who is not held back by the walls
    between projects. `plain` prints bare values, nothing else. `project` names the project that
    new rows go to when the SQL does not say (by default the shared test project, made if need be;
    a number for another; None or False for none, so that nothing is defaulted). With
    `as_project` the SQL runs as the database role a project's work runs as: it sees that
    project's rows only, as the server's own queries do."""
    flags = ["-tAq"] if plain else []
    if project is True:
        from tests.world import the_project

        project = the_project().id
    prefix = (
        [] if project is None or project is False else ["-c", f"SET vrum.project_id = {project}"]
    )
    if as_project:
        prefix += ["-c", "SET ROLE vrum_project"]
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
        *prefix,
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


async def confirm(
    client: Client, kind: str, record: dict[str, Any], person: str = "Marie", **arguments: Any
) -> dict[str, Any]:
    """A named person confirms a fact or a decision. Returns the record as it is afterwards."""
    answer = await ok(
        client,
        "set_confirmation",
        **{
            "actor": "agent-1",
            "kind": kind,
            "record_id": record["id"],
            "confirmation": "confirmed",
            "person": person,
            **arguments,
        },
    )
    changed: dict[str, Any] = answer[kind]
    return changed


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


async def export_everything(client: Client, limit: int = 200) -> dict[str, Any]:
    """The whole export: every page of export_graph, put together."""
    document: dict[str, Any] = {"format": None, "version": None, "counts": None}
    for section in ("nodes", "links", "rules", "decisions", "facts", "events"):
        document[section] = []
    after: str | None = None
    while True:
        page = await ok(client, "export_graph", after=after, limit=limit)
        document["format"], document["version"] = page["format"], page["version"]
        if page["counts"] is not None:
            document["counts"] = page["counts"]
        if page["section"] is not None:
            document[page["section"]].extend(page["items"])
        after = page["next_after"]
        if after is None:
            return document


async def rich_world(client: Client) -> dict[str, dict[str, Any]]:
    """One of everything the graph holds, made through the tools: all three kinds of node with
    dates, every kind of link, a rule placed and one not, a decision that was replaced, one that
    was withdrawn and a project-wide one, facts that were replaced, withdrawn and confirmed, a
    handoff, and a claim. Returns each by a short name (a node has `ref`; a rule, decision or fact
    has `id`)."""
    word = unique_word()
    epic = await ok(
        client,
        "create_node",
        actor="ann",
        kind="epic",
        title=f'Épic: log in "safely" {word}',
        body="Everything about\nlogging in.",
        start_on="2026-12-01",
        target_on="2026-12-31",
    )

    async def node(kind: str, title: str, parent: dict[str, Any], **more: Any) -> dict[str, Any]:
        return await ok(
            client,
            "create_node",
            actor="ann",
            kind=kind,
            title=f"{title} {word}",
            parent=parent["ref"],
            **more,
        )

    story1 = await node("story", "Log in with email", epic, body="Body of story one.")
    story2 = await node("story", "Log in with a code", epic, priority="high")
    task1 = await node("task", "Build the form", story1, start_on="2026-12-02")
    task2 = await node("task", "Check the password", story1, target_on="2026-12-10")
    task3 = await node("task", "Send the code", story2)
    await ok(
        client, "update_node", actor="ann", node=task1["ref"], expected_version=1, status="done"
    )
    for source, target, link_type in (
        (task2, task1, "needs_first"),
        (story1, story2, "relates_to"),
        (task3, task2, "duplicates"),
        (story2, story1, "supersedes"),
    ):
        await ok(
            client,
            "add_link",
            actor="ann",
            source=source["ref"],
            target=target["ref"],
            link_type=link_type,
        )
    await ok(client, "claim_node", actor="ann", node=task2["ref"])
    await ok(
        client,
        "finish_work",
        actor="ann",
        node=task2["ref"],
        outcome="blocked",
        note="Waiting for the design of the form.",
    )
    await ok(client, "claim_node", actor="bob", node=task3["ref"])

    rule1 = await ok(
        client, "create_rule", actor="ann", title=f"Rule one {word}", story=story1["ref"]
    )
    rule2 = await ok(
        client, "create_rule", actor="ann", title=f"Rule two {word}", body="Not placed."
    )
    await ok(
        client,
        "update_rule",
        actor="ann",
        rule_id=rule2["id"],
        expected_version=1,
        status="settled",
    )

    decision1 = await make_decision(
        client, f"Use email {word}", nodes=[story1["ref"]], rule_ids=[rule1["id"]]
    )
    await ok(
        client,
        "set_confirmation",
        actor="ann",
        kind="decision",
        record_id=decision1["id"],
        confirmation="confirmed",
        person="Marie",
    )
    decision2 = await ok(
        client,
        "supersede_decision",
        actor="ann",
        decision_id=decision1["id"],
        title=f"Use email and a code {word}",
        text="Both ways of logging in.",
        reason="Marie changed her mind.",
        decided_by="Marie",
        said_by="Marie",
        quote="Let us have both.",
    )
    decision3 = await make_decision(client, f"Use a password manager {word}")
    decision3 = await ok(
        client,
        "withdraw_decision",
        actor="ann",
        decision_id=decision3["id"],
        reason="Not needed after all.",
    )
    wide = await make_decision(client, f"Keep it simple {word}", project_wide=True)
    await ok(
        client,
        "set_confirmation",
        actor="ann",
        kind="decision",
        record_id=wide["id"],
        confirmation="confirmed",
        person="Marie",
        quote="Yes, keep it simple.",
    )

    fact1 = await make_fact(
        client,
        f"People log in on phones {word}",
        nodes=[story1["ref"]],
        rule_ids=[rule1["id"]],
        decision_ids=[decision2["id"]],
    )
    await ok(
        client,
        "set_confirmation",
        actor="ann",
        kind="fact",
        record_id=fact1["id"],
        confirmation="confirmed",
        person="Marie",
    )
    fact2 = await make_fact(client, f"Codes arrive in 5 seconds {word}")
    fact3 = await make_fact(client, f"Codes arrive in 10 seconds {word}")
    fact4 = (
        await ok(
            client,
            "supersede_fact",
            actor="ann",
            replaces=[fact3["id"]],
            reason="Measured again.",
            text=f"Codes arrive in 3 seconds {word}",
            source="A measurement",
        )
    )["replacement"]
    fact5 = await make_fact(client, f"The office has a dog {word}")
    await ok(client, "withdraw_fact", actor="ann", fact_ids=[fact5["id"]], reason="It is a cat.")
    fact6 = await make_fact(client, f"Codes never expire {word}")
    await ok(
        client,
        "set_confirmation",
        actor="ann",
        kind="fact",
        record_id=fact6["id"],
        confirmation="confirmed",
        person="Marie",
    )
    await ok(
        client,
        "withdraw_fact",
        actor="ann",
        fact_ids=[fact6["id"]],
        reason="They expire in an hour.",
        said_by="Marie",
        quote="No, they expire.",
    )
    return {
        "epic": epic,
        "story1": story1,
        "story2": story2,
        "task1": task1,
        "task2": task2,
        "task3": task3,
        "rule1": rule1,
        "rule2": rule2,
        "decision1": decision1,
        "decision2": decision2,
        "decision3": decision3,
        "wide": wide,
        "fact1": fact1,
        "fact2": fact2,
        "fact3": fact3,
        "fact4": fact4,
        "fact5": fact5,
        "fact6": fact6,
    }


M19_HELPERS_READY = True
