"""Small helpers for tests that talk to the running containers: through a real MCP client, or
straight to the database."""

import os
import random
import subprocess
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import CallToolResult, TextContent

if TYPE_CHECKING:
    from tests.world import Project


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


@asynccontextmanager
async def connect_as(name: str, project: "Project | None" = None) -> AsyncIterator[Client]:
    """A client of the shared project (or of `project`) that is the agent called `name`: it makes
    a key with that name, which is who the server records the client as. A name is the name of one
    key in force at a time, so a test uses a name of its own."""
    from tests.world import make_key, the_project

    chosen = project or the_project()
    async with connect(chosen.mcp_url, make_key(chosen, name)) as client:
        yield client


@asynccontextmanager
async def connect_each(
    *names: str, project: "Project | None" = None
) -> AsyncIterator[list[Client]]:
    """One client for each name (see `connect_as`), connected together: `async with connect_each(
    "ann", "bob") as (ann, bob)`."""
    async with AsyncExitStack() as stack:
        yield [await stack.enter_async_context(connect_as(name, project)) for name in names]


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


_NODE_EXTRAS = {
    "similar",
    "similar_checked",
    "similar_note",
    "handoff_count",
    "last_handoff",
    "checks",
}

# The list tools that page over nodes: they are continued by `after` (the reference of the last
# node of a page) and say `next_after`. Every other list is continued by `after_id`.
NODE_PAGES = {"list_nodes", "ready_work"}

_CODE_LETTER = {"epic": "E", "story": "S", "task": "T"}
_LETTER_KIND = {letter: kind for kind, letter in _CODE_LETTER.items()}


def ref_of(kind: str, number: int) -> str:
    """The reference of an epic, story or task, as every tool and address names it: story:12. Any
    of the five kinds makes the key the board uses for it (rule:3, note:7)."""
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
    """An acceptance criterion (a rule): pass `node=` with the reference of a story or task to
    place it."""
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


async def race_agents(
    clients: list[Client], tool: str, arguments: dict[str, Any]
) -> list[tuple[bool, str]]:
    """Every client makes this call at the same moment; each is an agent of its own, so each is who
    the history says made its call. For each: was it accepted, and what did it say."""
    results: list[tuple[bool, str]] = []

    async def call(agent: Client) -> None:
        result = await agent.call_tool(tool, arguments)
        results.append((not result.is_error, _text(result)))

    async with anyio.create_task_group() as group:
        for agent in clients:
            group.start_soon(call, agent)
    return results


async def race_results(
    client: Client, tool: str, calls: list[dict[str, Any]]
) -> list[tuple[bool, str]]:
    """Make all the calls at the same moment. For each: was it accepted, and what did it say."""
    return await race_mixed(client, [(tool, arguments) for arguments in calls])


async def race(client: Client, tool: str, calls: list[dict[str, Any]]) -> list[bool]:
    """Make all the calls at the same moment and say which of them were accepted."""
    return [accepted for accepted, _ in await race_results(client, tool, calls)]


async def make_note(
    client: Client, text: str = "A note", why: str = "Because", **arguments: Any
) -> dict[str, Any]:
    """A note, recorded through add_note. A note someone decided names them in `decided_by`."""
    return await ok(client, "add_note", **{"text": text, "why": why, **arguments})


async def make_decision(
    client: Client, title: str = "A decision", **arguments: Any
) -> dict[str, Any]:
    """A note that a person decided (Marie, unless `decided_by` says otherwise). The title is the
    first line of its text, which is how a note has a headline."""
    body = arguments.pop("text", f"{title}: what was decided")
    why = arguments.pop("reason", "Because")
    return await make_note(
        client, f"{title}\n\n{body}", **{"why": why, "decided_by": "Marie", **arguments}
    )


def day(offset: int = 0) -> str:
    """A day written as YYYY-MM-DD, `offset` days from today (UTC, as the database counts it)."""
    return (datetime.now(UTC).date() + timedelta(days=offset)).isoformat()


async def make_fact(client: Client, text: str = "A fact", **arguments: Any) -> dict[str, Any]:
    """A note that nobody decided: what is known, and where it came from."""
    why = arguments.pop("source", "The team call")
    if "decision_ids" in arguments:
        arguments["note_ids"] = arguments.pop("decision_ids")
    return await make_note(client, text, why, **arguments)


async def confirm(
    client: Client, kind: str, record: dict[str, Any], person: str = "Marie", **arguments: Any
) -> dict[str, Any]:
    """A named person confirms a note. Returns the note as it is afterwards. (`kind` is what the
    note used to be called, a fact or a decision, and no longer matters.) Only a person can, so
    `client` must be one: an agent's call is refused (see `confirm_on_board`)."""
    return await ok(
        client,
        "set_confirmation",
        **{"note_id": record["id"], "confirmation": "confirmed", "person": person, **arguments},
    )


def on_board(tool: str, *, project: "Project | None" = None, **arguments: Any) -> dict[str, Any]:
    """Do what a person does on the board: call the tool as the person signed in, who owns the
    project (by default the shared test project, whose owner is "Shared tester"). The change is
    recorded as that person, and it is refused in the board's own words when the tool refuses."""
    from tests.world import the_project

    chosen = project or the_project()
    reply = chosen.owner.call("POST", f"/board/api/projects/{chosen.id}/tool/{tool}", arguments)
    assert reply.status == 200, f"{tool} was refused on the board: {reply.body}"
    return dict(reply.body)


def confirm_on_board(
    kind: str,
    record: dict[str, Any],
    person: str = "Marie",
    *,
    project: "Project | None" = None,
    **arguments: Any,
) -> dict[str, Any]:
    """A person confirms a note on the board, which is the only place it can be done: an agent's
    call to set_confirmation is refused. `person` is the name of whoever reviewed it (it needs no
    account). `kind` is what the note used to be called and no longer matters. Returns the note as
    it is afterwards."""
    return on_board(
        "set_confirmation",
        project=project,
        **{"note_id": record["id"], "confirmation": "confirmed", "person": person, **arguments},
    )


def set_definition_on_board(body: str, *, project: "Project | None" = None) -> dict[str, Any]:
    """A person sets the project's definition of done on the board, which is the only place it can
    be done: an agent's call to set_definition_of_done is refused. Returns it as it is
    afterwards."""
    current = on_board("get_definition_of_done", project=project)
    return on_board(
        "set_definition_of_done", project=project, body=body, expected_version=current["version"]
    )


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
    for section in ("nodes", "links", "rules", "notes", "definition_of_done", "checks", "events"):
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


async def rich_world(client: Client, project: "Project | None" = None) -> dict[str, dict[str, Any]]:
    """One of everything the graph holds, made through the tools: all three kinds of node with
    dates and sizes, every kind of link, a rule placed and one not, a decision that was replaced,
    one that was withdrawn and a project-wide one, notes nobody decided that were replaced,
    withdrawn and confirmed (confirmed on the board, as a person, in `project`: the shared one by
    default), a handoff, and a claim. In a project of its own (not the shared one) the definition
    of done has checks, ticked by an agent and by a person, and one unticked. Returns each by a
    short name (a node has `ref`; a rule or note has `id`)."""
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

    story1 = await node("story", "Log in with email", epic, body="Body of story one.", size="l")
    story2 = await node("story", "Log in with a code", epic, priority="high")
    task1 = await node("task", "Build the form", story1, start_on="2026-12-02", size="s")
    task2 = await node("task", "Check the password", story1, target_on="2026-12-10", size="m")
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
        client, "create_rule", actor="ann", title=f"Rule one {word}", node=story1["ref"]
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
        status="met",
    )
    rule3 = await ok(
        client, "create_rule", actor="ann", title=f"Rule three {word}", node=task1["ref"]
    )
    # The shared project (the default) gets a definition of done with no list lines: an agent
    # cannot finish work as done while a check of its project is unticked, and every test shares
    # that project. A project of its own gets checks, and some of them ticked.
    if project is None:
        set_definition_on_board(f"Tests pass, and a person can try it {word}")
    else:
        set_definition_on_board(
            f"- Tests pass\n- A person can try it {word}\n- Docs are updated", project=project
        )
        checks = (await ok(client, "get_definition_of_done"))["items"]
        tests, tried, docs = (check["item"] for check in checks)
        await ok(client, "set_check", node=task1["ref"], item=tests, evidence="212 tests pass")
        on_board("set_check", project=project, node=task1["ref"], item=tried, checked=True)
        await ok(client, "set_check", node=task1["ref"], item=tried, checked=False)
        on_board(
            "set_check",
            project=project,
            node=task1["ref"],
            item=docs,
            checked=True,
            evidence="Read it through",
        )
        on_board("set_check", project=project, node=story1["ref"], item=tests, checked=True)

    decision1 = await make_decision(
        client, f"Use email {word}", nodes=[story1["ref"]], rule_ids=[rule1["id"]]
    )
    confirm_on_board("decision", decision1, project=project)
    decision2 = (
        await ok(
            client,
            "supersede_note",
            replaces=[decision1["id"]],
            text=f"Use email and a code {word}\n\nBoth ways of logging in.",
            why="Marie changed her mind.",
            decided_by="Marie",
            said_by="Marie",
            quote="Let us have both.",
        )
    )["replacement"]
    decision3 = await make_decision(client, f"Use a password manager {word}")
    await ok(client, "withdraw_note", note_ids=[decision3["id"]], reason="Not needed after all.")
    decision3 = await ok(client, "get_note", note_id=decision3["id"])
    wide = await make_decision(client, f"Keep it simple {word}", project_wide=True)
    confirm_on_board("decision", wide, project=project, quote="Yes, keep it simple.")

    fact1 = await make_fact(
        client,
        f"People log in on phones {word}",
        nodes=[story1["ref"]],
        rule_ids=[rule1["id"]],
        decision_ids=[decision2["id"]],
    )
    confirm_on_board("fact", fact1, project=project)
    fact2 = await make_fact(client, f"Codes arrive in 5 seconds {word}")
    fact3 = await make_fact(client, f"Codes arrive in 10 seconds {word}")
    fact4 = (
        await ok(
            client,
            "supersede_note",
            replaces=[fact3["id"]],
            text=f"Codes arrive in 3 seconds {word}",
            why="A measurement",
        )
    )["replacement"]
    fact5 = await make_fact(client, f"The office has a dog {word}")
    await ok(client, "withdraw_note", note_ids=[fact5["id"]], reason="It is a cat.")
    fact6 = await make_fact(client, f"Codes never expire {word}")
    confirm_on_board("fact", fact6, project=project)
    await ok(
        client,
        "withdraw_note",
        note_ids=[fact6["id"]],
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
        "rule3": rule3,
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


M20_HELPERS_READY = True
