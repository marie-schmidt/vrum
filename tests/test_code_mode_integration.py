"""M37's proof on the real stack, through a real MCP client: an agent key can call Vrum by running
code. Its tool list is `explore` and `run`, every function of a script is one of the tools of its
allowance and goes back through the guard, the sandbox service holds, and what goes wrong is told
in plain words. The sandbox's own attacks are in tests/sandbox_attacks.py, which one test here runs
against the stack's sandbox container; the rest prove the path through the app.

Every test that writes works in a project of its own."""

import asyncio
import json
import os
import random
import re
import string
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import cache
from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent

from tests.helpers import (
    REPO_ROOT,
    all_nodes,
    compose,
    connect,
    export_everything,
    node_core,
    ok,
    refused,
    rich_world,
    set_definition_on_board,
    unique_word,
)
from tests.sandbox_attacks import Target, run_script
from tests.world import Project, make_key, make_limited_key, make_project, register
from vrum.accounts import SCRIPT_KEY_NOW_TOOLS, SCRIPT_KEY_REVOKED, key_not_allowed
from vrum.config import Settings
from vrum.guard import GuardedServer
from vrum.server import create_server
from vrum.surface import AGENT_SURFACE

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

ENV = {"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "unused"}

READS = ["list_nodes", "get_node", "list_links", "recall"]


@cache
def the_server() -> GuardedServer:
    """The server's own tools, built here (nothing is started), for the declarations of all of
    them."""
    return create_server(Settings.from_env(ENV))


async def run_ok(client: Client, code: str, **more: Any) -> dict[str, Any]:
    """A script that must finish: `run`'s answer (result, logs and calls)."""
    return await ok(client, "run", code=code, **more)


async def run_refused(client: Client, code: str, **more: Any) -> str:
    """A script that must not finish, in the words the agent reads."""
    return await refused(client, "run", code=code, **more)


async def names_of(client: Client) -> set[str]:
    return {tool.name for tool in (await client.list_tools()).tools}


@asynccontextmanager
async def runner(
    project: Project, name: str = "coder", tools: list[str] | None | bool = True
) -> AsyncIterator[Client]:
    """A client that is a code key of the project: allowed every tool a key can have (True), the
    standard tools (None) or exactly the tools named."""
    if tools is True:
        key = make_key(project, name, code_mode=True)
    else:
        assert tools is None or isinstance(tools, list)
        key = make_limited_key(project, name, tools, code_mode=True)
    async with connect(project.mcp_url, key) as client:
        yield client


def key_row(project: Project, name: str) -> dict[str, Any]:
    """The Users page's row of the key in force with this name, as the board's API gives it."""
    reply = project.owner.call("GET", f"/board/api/projects/{project.id}/keys")
    assert reply.status == 200, reply.body
    rows = [key for key in reply.body["keys"] if key["name"] == name and not key["revoked_at"]]
    assert len(rows) == 1, rows
    return dict(rows[0])


def change_key(project: Project, name: str, changes: dict[str, Any]) -> Any:
    return project.owner.call(
        "PATCH", f"/board/api/projects/{project.id}/keys/{key_row(project, name)['id']}", changes
    )


# -- 1: the listing, and the two ways refusing each other -------------------------------------


@pytest.mark.parametrize(
    "allowance", [True, None, ["list_nodes", "get_node"]], ids=["every-tool", "standard", "two"]
)
async def test_a_code_key_lists_exactly_explore_and_run_whatever_it_may_use(
    allowance: list[str] | None | bool,
) -> None:
    project = make_project(register())
    async with runner(project, tools=allowance) as client:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(listed) == {"explore", "run"}
    explore, run = listed["explore"], listed["run"]
    assert explore.annotations is not None and explore.annotations.read_only_hint is True
    assert run.annotations is None or not run.annotations.read_only_hint
    assert explore.description and run.description


async def test_a_tools_key_lists_what_it_listed_before_and_never_explore_or_run() -> None:
    project = make_project(register())
    standard = make_limited_key(project, "standard", None)
    narrow = make_limited_key(project, "narrow", ["list_nodes", "node_history", "export_graph"])
    async with connect(project.mcp_url, standard) as agent:
        assert await names_of(agent) == AGENT_SURFACE
    async with connect(project.mcp_url, narrow) as agent:
        assert await names_of(agent) == {"list_nodes", "node_history", "export_graph"}
    async with connect(project.mcp_url, project.key) as everything:
        names = await names_of(everything)
    assert len(names) > 25 and not {"explore", "run"} & names


async def test_each_way_of_calling_refuses_the_others_calls_in_words() -> None:
    project = make_project(register())
    tools_key = make_limited_key(project, "caller", None)
    code_key = make_limited_key(project, "runner", None, code_mode=True)
    async with connect(project.mcp_url, tools_key) as agent:
        for tool, arguments in (("explore", {}), ("run", {"code": "return 1"})):
            words = await refused(agent, tool, **arguments)
            assert f"{tool} is for agent keys that call Vrum by running code" in words, words
            assert "this agent key (caller) calls one tool at a time" in words
            assert "Users page" in words and "Nothing was changed." in words
    async with connect(project.mcp_url, code_key) as agent:
        for tool, arguments in (
            ("list_nodes", {}),
            ("create_node", {"kind": "epic", "title": "Not made"}),
        ):
            words = await refused(agent, tool, **arguments)
            assert "This agent key (runner) calls Vrum by running code" in words, words
            assert f"call it as vrum.{tool}(...) in run" in words
            assert "Use explore to see what the functions take and return" in words
        # The refused create changed nothing, and the same function inside a script works.
        found = await run_ok(agent, "return (await vrum.list_nodes({ kind: 'epic' })).items")
        assert found["result"] == []


async def test_a_key_can_be_switched_between_ways_and_an_open_client_sees_it_at_once() -> None:
    project = make_project(register())
    key = make_limited_key(project, "switcher", None)
    assert key_row(project, "switcher")["code_mode"] is False
    async with connect(project.mcp_url, key) as agent:
        assert await names_of(agent) == AGENT_SURFACE

        changed = change_key(project, "switcher", {"code_mode": True})
        assert changed.status == 200 and changed.body["key"]["code_mode"] is True
        assert key_row(project, "switcher")["code_mode"] is True
        assert await names_of(agent) == {"explore", "run"}
        assert "call it as vrum.list_nodes(...) in run" in await refused(agent, "list_nodes")
        assert (await run_ok(agent, "return 6 * 7"))["result"] == 42

        changed = change_key(project, "switcher", {"code_mode": False})
        assert changed.status == 200 and changed.body["key"]["code_mode"] is False
        assert await names_of(agent) == AGENT_SURFACE
        words = await refused(agent, "run", code="return 1")
        assert "run is for agent keys that call Vrum by running code" in words
        assert (await ok(agent, "list_nodes"))["items"] == []

    # The way is changed on its own, from the owner, and a bad request changes nothing.
    wrong = change_key(project, "switcher", {"code_mode": "yes"})
    assert wrong.status == 400 and "true (it runs code) or false" in str(wrong.body)
    nothing = change_key(project, "switcher", {})
    assert nothing.status == 400 and "Say what to change" in str(nothing.body)
    named = change_key(project, "switcher", {"tools": ["list_nodes", "explore"]})
    assert named.status == 400 and "explore and run are what a key that calls Vrum" in str(
        named.body
    )
    row = key_row(project, "switcher")
    assert row["code_mode"] is False and row["tools"] is None


async def test_a_code_key_with_a_read_only_allowance_has_only_read_functions() -> None:
    project = make_project(register())
    async with runner(project, "reader", READS) as reader:
        found = await ok(reader, "explore")
        assert {line["name"] for line in found["functions"]} == set(READS)
        assert all(line["only_reads"] for line in found["functions"])

        words = await run_refused(reader, "await vrum.create_node({ kind: 'epic', title: 'x' });")
        assert "does not match the types of the functions it calls" in words
        assert "create_node" in words and "Nothing was changed." in words

        # Past the type check, the function is still not there to call.
        words = await run_refused(
            reader, "await (vrum as any).create_node({ kind: 'epic', title: 'x' });"
        )
        assert "create_node is not a function" in words
        assert "It made no calls, so nothing was changed." in words

        # And naming it as a tool is refused for the allowance, not for the way.
        words = await refused(reader, "create_node", kind="epic", title="x")
        assert "is not allowed to use create_node" in words
        assert (await run_ok(reader, "return (await vrum.list_nodes({})).items.length"))[
            "result"
        ] == 0


# -- 2: explore, and the declarations --------------------------------------------------------


@pytest.mark.parametrize(
    "allowance",
    [None, ["list_nodes", "get_node", "create_node", "claim_node", "finish_work", "add_note"]],
    ids=["standard", "six"],
)
async def test_explore_with_nothing_asked_lists_exactly_the_functions_the_key_has(
    allowance: list[str] | None,
) -> None:
    project = make_project(register())
    tools_key = make_limited_key(project, "plain", allowance)
    async with connect(project.mcp_url, tools_key) as agent:
        listed = {tool.name: tool for tool in (await agent.list_tools()).tools}
    reads = {
        name: bool(tool.annotations and tool.annotations.read_only_hint)
        for name, tool in listed.items()
    }
    if allowance is None:
        assert set(reads) == AGENT_SURFACE
    async with runner(project, "coder", allowance) as coder:
        found = await ok(coder, "explore")
    assert {line["name"]: line["only_reads"] for line in found["functions"]} == reads
    assert all(line["summary"] for line in found["functions"])
    assert found["declarations"] == "" and found["not_functions"] == []


def declared_fields(text: str, type_name: str) -> dict[str, bool]:
    """The fields a declared type has, each with whether it is optional."""
    body = re.search(rf"^type {type_name} = \{{\n(.*?)\n\}};$", text, re.S | re.M)
    assert body, f"{type_name} is not declared"
    return {
        match.group(1): bool(match.group(2))
        for match in re.finditer(r"^ {2}([A-Za-z_]\w*)(\?)?:", body.group(1), re.M)
    }


def result_type(text: str, function: str) -> str:
    found = re.search(rf"\n {{2}}{function}\(.*?\): Promise<(\w+)>;", text, re.S)
    assert found, f"{function} has no declared result"
    return found.group(1)


async def test_explore_gives_the_declarations_of_names_and_they_match_real_results() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        epic = await ok(agent, "create_node", kind="epic", title="An epic")
        real = {
            "get_node": await ok(agent, "get_node", node=epic["ref"]),
            "list_nodes": await ok(agent, "list_nodes"),
            "who_is_working": await ok(agent, "who_is_working"),
        }
    async with runner(project) as coder:
        found = await ok(
            coder, "explore", names=["get_node", "list_nodes", "who_is_working", "nope", "x" * 100]
        )
        assert {line["name"] for line in found["functions"]} == set(real)
        # A name that is not a function is said so, and a long odd one is not repeated.
        assert found["not_functions"] == ["nope", "(not a function)"]
        text = found["declarations"]
        assert text.count("declare const vrum: {") == 1
        for function, result in real.items():
            declared = declared_fields(text, result_type(text, function))
            # Every field a result has is declared, and every field not marked optional is there.
            assert set(result) <= set(declared), (function, set(result) - set(declared))
            assert {name for name, optional in declared.items() if not optional} <= set(result)
        # The full text keeps the whole description of a function, not its first sentence.
        assert "Plain text or Markdown" in text

        asked = await ok(coder, "explore", query="claim")
        names = {line["name"] for line in asked["functions"]}
        assert {"claim_node", "renew_claim"} <= names and 0 < len(names) <= 10
        assert all(f"\n  {name}(" in asked["declarations"] for name in names)
        silent = await ok(coder, "explore", query="zzzqxv")
        assert silent["functions"] == [] and silent["declarations"] == ""

        # A tool only a person may use is not a function of any key.
        people = await ok(coder, "explore", names=["set_confirmation", "set_definition_of_done"])
        assert people["functions"] == []
        assert people["not_functions"] == ["set_confirmation", "set_definition_of_done"]


async def run_description(client: Client) -> str:
    """What the tool `run` says about itself to this client's key."""
    described = {tool.name: tool.description for tool in (await client.list_tools()).tools}
    description = described["run"]
    assert description
    return description


def functions_named_in(description: str) -> tuple[set[str], set[str]]:
    """The functions `run`'s description names as only reading and as changing something."""
    named: dict[str, set[str]] = {}
    for sort, start in (
        ("reads", "Functions that only read: "),
        ("changes", "Functions that change something: "),
    ):
        lines = [line for line in description.splitlines() if line.startswith(start)]
        listed = lines[0].removeprefix(start).removesuffix(".").split(", ") if lines else []
        named[sort] = set(listed)
    return named["reads"], named["changes"]


async def test_run_describes_itself_with_the_keys_own_functions_and_compact_signatures() -> None:
    project = make_project(register())
    async with runner(project, "standard", None) as standard:
        description = await run_description(standard)
    guide, _, signatures = description.partition("Signatures:")
    assert signatures.count("declare const vrum: {") == 1
    assert set(re.findall(r"^ {2}([a-z_]+)\(", signatures, re.M)) == AGENT_SURFACE
    reads, changes = functions_named_in(description)
    assert reads | changes == AGENT_SURFACE and not reads & changes
    assert "list_nodes" in reads and "next_work" in changes
    assert "node_history" not in guide

    async with runner(project, "narrow", ["list_nodes", "add_note"]) as narrow:
        short = await run_description(narrow)
    assert set(re.findall(r"^ {2}([a-z_]+)\(", short, re.M)) == {"list_nodes", "add_note"}
    assert functions_named_in(short) == ({"list_nodes"}, {"add_note"})
    assert "next_work" not in short.partition("Signatures:")[0]


@cache
def sandbox_id() -> str:
    found = compose("ps", "-aq", "sandbox").stdout.split()
    assert len(found) == 1, f"the stack should have one sandbox container, it has {found}"
    return found[0]


async def in_the_sandbox(code: str, declarations: str, functions: list[str]) -> dict[str, Any]:
    """A script sent straight to the sandbox's supervisor (through `docker exec`), with no app
    involved: what the sandbox says about it. A call it makes is answered with a canned reply."""
    outcome = await run_script(
        Target(None, sandbox_id()), code, declarations=declarations, functions=functions
    )
    return outcome.final


async def test_the_declarations_of_every_tool_the_server_has_pass_deno_check_in_the_sandbox() -> (
    None
):
    sdk = the_server().sdk
    names = list(sdk.names)
    assert len(names) >= 47
    full = sdk.declarations(names, compact=False)
    compact = sdk.declarations(names, compact=True)

    # Both texts are valid TypeScript for the real checker, together, with a call of one function.
    for text in (full, compact):
        final = await in_the_sandbox("return await vrum.list_nodes({});", text, names)
        assert final["op"] == "done", final

    # A misspelt function and a wrong argument are failures of the check, with the line.
    final = await in_the_sandbox("return await vrum.list_nodez({});", compact, names)
    assert final["stage"] == "types" and final["line"] == 1
    assert "Property 'list_nodez' does not exist" in final["message"]
    assert "Did you mean 'list_nodes'?" in final["message"]
    final = await in_the_sandbox(
        "const first = 1;\nreturn await vrum.list_nodes({ limit: 'ten' });", compact, names
    )
    assert final["stage"] == "types" and final["line"] == 2, final
    assert "Type 'string' is not assignable to type 'number'" in final["message"]

    # The checker skips errors inside declaration files, so a name the declarations use and do not
    # declare would quietly become `any`. For each function, a wrong argument and a result that is
    # not a { __no: never } must both be errors: neither type is `any`.
    gate = asyncio.Semaphore(2)

    async def scan(name: str) -> tuple[str, dict[str, Any]]:
        script = (
            f"await vrum.{name}(12345);\n"
            f"const r: {{ __no: never }} = await vrum.{name}({{}} as never);"
        )
        async with gate:
            return name, await in_the_sandbox(script, compact, names)

    for name, final in await asyncio.gather(*(scan(name) for name in names)):
        message = final.get("message", "")
        assert final.get("stage") == "types", (name, final)
        assert "Line 1:" in message, (name, message)
        assert "Line 2:" in message and "{ __no: never; }" in message, (name, message)


# -- 3: a whole day's work, by script and by single calls, leaves the same project -----------

DEFINITION = "- Tests pass\n- Docs are updated"

THE_DAY = """
const tick = async (node: string, how: string) => {
  const detail = await vrum.get_node({ node });
  for (const check of detail.checks) {
    await vrum.set_check({ node, item: check.item, evidence: `${how}: ${check.text}` });
  }
};
const taken = await vrum.next_work({});
if (!taken.found || !taken.work) throw new Error("nothing was free");
const first = taken.work.ref;
await vrum.renew_claim({ node: first, lease_seconds: 600 });
await vrum.add_note({
  text: "Slots are shown in local time",
  why: `Found while doing ${first}`,
  nodes: [first],
});
await tick(first, "first");
await vrum.finish_work({ node: first, outcome: "done", note: "Showed the free slots." });

const ready = await vrum.list_nodes({ query: "kind:task status:ready", limit: 2 });
const finished = [first];
for (const item of ready.items) {
  await vrum.claim_node({ node: item.ref, lease_seconds: 600 });
  if (item.ref === ready.items[0].ref) {
    await tick(item.ref, "loop");
    await vrum.finish_work({ node: item.ref, outcome: "done", note: "Done in the loop." });
  } else {
    await vrum.finish_work({ node: item.ref, outcome: "released", note: "Put back, not started." });
  }
  finished.push(item.ref);
}

const titles = ["Write the docs", "Tell the team", "Close the ticket"];
const plan = await vrum.create_plan({
  items: titles.map((title, index) => ({
    key: `follow-${index}`,
    kind: "task",
    title,
    parent: taken.work!.parent,
    size: "xs",
  })),
  links: titles.slice(1).map((_, index) => ({
    source: `follow-${index + 1}`,
    target: `follow-${index}`,
    link_type: "needs_first",
  })),
});
const done = await vrum.list_nodes({ query: "kind:task status:done" });
return {
  first,
  finished,
  created: plan.created.map((made) => made.ref),
  done: done.items.map((item) => item.title),
};
"""


async def seed_a_day(project: Project) -> None:
    """The same small project every time: a definition of done with two checks (a person sets it,
    on the board), an epic, a story and four tasks."""
    set_definition_on_board(DEFINITION, project=project)
    async with connect(project.mcp_url, project.key) as setup:
        epic = await ok(setup, "create_node", kind="epic", title="Booking")
        story = await ok(setup, "create_node", kind="story", title="Slots", parent=epic["ref"])
        for title, priority in (
            ("Show free slots", "high"),
            ("Hold a slot", "normal"),
            ("Release a slot", "normal"),
            ("Print a slot", "low"),
        ):
            await ok(
                setup,
                "create_node",
                kind="task",
                title=title,
                parent=story["ref"],
                priority=priority,
                size="s",
            )


async def the_day_by_single_calls(agent: Client) -> tuple[dict[str, Any], list[str]]:
    """THE_DAY, one tool call at a time: what it returns and the tools it called, in order."""
    made: list[str] = []

    async def call(tool: str, **arguments: Any) -> dict[str, Any]:
        made.append(tool)
        return await ok(agent, tool, **arguments)

    async def tick(node: str, how: str) -> None:
        for check in (await call("get_node", node=node))["checks"]:
            await call(
                "set_check", node=node, item=check["item"], evidence=f"{how}: {check['text']}"
            )

    taken = await call("next_work")
    assert taken["found"] and taken["work"]
    first = taken["work"]["ref"]
    await call("renew_claim", node=first, lease_seconds=600)
    await call(
        "add_note",
        text="Slots are shown in local time",
        why=f"Found while doing {first}",
        nodes=[first],
    )
    await tick(first, "first")
    await call("finish_work", node=first, outcome="done", note="Showed the free slots.")

    ready = await call("list_nodes", query="kind:task status:ready", limit=2)
    finished = [first]
    for item in ready["items"]:
        await call("claim_node", node=item["ref"], lease_seconds=600)
        if item["ref"] == ready["items"][0]["ref"]:
            await tick(item["ref"], "loop")
            await call("finish_work", node=item["ref"], outcome="done", note="Done in the loop.")
        else:
            await call(
                "finish_work", node=item["ref"], outcome="released", note="Put back, not started."
            )
        finished.append(item["ref"])

    titles = ["Write the docs", "Tell the team", "Close the ticket"]
    plan = await call(
        "create_plan",
        items=[
            {
                "key": f"follow-{index}",
                "kind": "task",
                "title": title,
                "parent": taken["work"]["parent"],
                "size": "xs",
            }
            for index, title in enumerate(titles)
        ],
        links=[
            {
                "source": f"follow-{index + 1}",
                "target": f"follow-{index}",
                "link_type": "needs_first",
            }
            for index in range(len(titles) - 1)
        ],
    )
    done = await call("list_nodes", query="kind:task status:done")
    result = {
        "first": first,
        "finished": finished,
        "created": [made_one["ref"] for made_one in plan["created"]],
        "done": [item["title"] for item in done["items"]],
    }
    return result, made


def without_times(value: Any) -> Any:
    """Data with every time taken out (a field called at, or ending in _at), so that two projects
    that were worked in at different moments can be compared."""
    if isinstance(value, dict):
        return {
            name: without_times(item)
            for name, item in value.items()
            if name != "at" and not name.endswith("_at")
        }
    if isinstance(value, list):
        return [without_times(item) for item in value]
    return value


async def test_a_script_does_a_days_work_and_leaves_what_the_same_calls_one_by_one_leave() -> None:
    owner = register()
    by_script, by_calls = make_project(owner), make_project(owner)
    await seed_a_day(by_script)
    await seed_a_day(by_calls)

    async with runner(by_script, "coder") as coder:
        ran = await run_ok(coder, THE_DAY)
    async with connect(by_calls.mcp_url, make_key(by_calls, "coder")) as agent:
        returned, made = await the_day_by_single_calls(agent)

    # One script composed all the calls (a loop, a filter, a plan built from a list), and what it
    # returned is what the single calls returned.
    assert ran["result"] == returned
    assert returned["first"] == "task:1" and returned["finished"] == ["task:1", "task:2", "task:3"]
    assert returned["created"] == ["task:5", "task:6", "task:7"]
    assert returned["done"] == ["Show free slots", "Hold a slot"]
    assert [call["tool"] for call in ran["calls"]] == made and len(made) >= 15
    assert {call["state"] for call in ran["calls"]} == {"done"}
    assert {
        "next_work",
        "renew_claim",
        "claim_node",
        "add_note",
        "set_check",
        "finish_work",
    } <= set(made)
    assert {"create_plan", "list_nodes", "get_node"} <= set(made)

    # The two projects now hold the same graph, notes, ticks and history: every event the day
    # made is stamped with the key's name, and its kind is the same.
    async with connect(by_script.mcp_url, by_script.key) as one:
        script_export = await export_everything(one)
        script_nodes = [without_times(node_core(node)) for node in await all_nodes(one)]
        script_working = await ok(one, "who_is_working")
    async with connect(by_calls.mcp_url, by_calls.key) as other:
        calls_export = await export_everything(other)
        calls_nodes = [without_times(node_core(node)) for node in await all_nodes(other)]
        calls_working = await ok(other, "who_is_working")
    assert script_nodes == calls_nodes and len(script_nodes) == 9
    assert without_times(script_export) == without_times(calls_export)
    assert len(script_export["events"]) > 20 and len(script_export["checks"]) == 4
    days = [event for event in script_export["events"] if event["actor"] == "coder"]
    assert days and {(event["actor"], event["actor_kind"]) for event in days} == {
        ("coder", "agent")
    }
    assert {event["action"] for event in days} >= {
        "claimed",
        "claim_renewed",
        "claim_released",
        "handoff",
        "check_ticked",
        "note_recorded",
        "create",
        "link_added",
        "update",
    }
    assert [event["action"] for event in days] == [
        event["action"] for event in calls_export["events"] if event["actor"] == "coder"
    ]
    # Every handoff is kept, and no claim is still held.
    handoffs = [event["after"] for event in days if event["action"] == "handoff"]
    assert [handoff["note"] for handoff in handoffs] == [
        "Showed the free slots.",
        "Done in the loop.",
        "Put back, not started.",
    ]
    for working in (script_working, calls_working):
        coder = next(agent for agent in working["agents"] if agent["actor"] == "coder")
        assert coder["holding"] == [] and coder["state"] == "finished"


# -- 4: the guards hold inside a script -------------------------------------------------------


async def test_a_function_outside_the_allowance_is_not_on_vrum() -> None:
    project = make_project(register())
    async with runner(project, "limited", ["list_nodes", "create_node"]) as client:
        found = await run_ok(client, "return Object.keys(vrum).sort();")
        assert found["result"] == ["create_node", "list_nodes"]
        words = await run_refused(
            client, "await vrum.update_node({ node: 'epic:1', expected_version: 1, title: 'x' });"
        )
        assert "Property 'update_node' does not exist" in words and "Nothing was changed." in words
        words = await run_refused(client, "await (vrum as any).node_history({ node: 'epic:1' });")
        assert "node_history is not a function" in words and "no calls" in words
    # A key with no list of its own has no function that only a person uses either.
    async with runner(project, "standard", None) as client:
        found = await run_ok(client, "return Object.keys(vrum).sort();")
        assert set(found["result"]) == AGENT_SURFACE
        words = await run_refused(client, "await (vrum as any).set_confirmation({});")
        assert "set_confirmation is not a function" in words


async def test_a_secret_in_the_script_or_in_a_calls_arguments_is_refused() -> None:
    project = make_project(register())
    secret = "ghp_" + "".join(random.choices(string.ascii_letters + string.digits, k=36))
    async with runner(project, "leaky") as client:
        words = await run_refused(client, f"return '{secret}';")
        assert "The code field contains what looks like a GitHub token" in words
        assert "nothing was changed" in words and secret not in words

        # Put together while it runs, the secret is not in the script's text, only in the call.
        ran = await run_ok(
            client,
            f"""
            const secret = '{secret[:20]}' + '{secret[20:]}';
            try {{
              await vrum.add_note({{ text: secret, why: 'a test' }});
              return 'stored';
            }} catch (error) {{
              return (error as Error).message;
            }}
            """,
        )
        assert "contains what looks like a GitHub token" in ran["result"], ran
        assert "nothing was changed" in ran["result"] and secret not in str(ran)
        assert [(call["tool"], call["state"]) for call in ran["calls"]] == [("add_note", "refused")]
    async with connect(project.mcp_url, project.key) as agent:
        assert (await ok(agent, "list_notes"))["items"] == []


async def test_a_stale_version_and_another_agents_claim_are_refused_in_the_tools_own_words() -> (
    None
):
    project = make_project(register())
    other = make_key(project, "other")
    third = make_key(project, "third")
    async with (
        connect(project.mcp_url, project.key) as setup,
        connect(project.mcp_url, other) as holder,
        connect(project.mcp_url, third) as asker,
    ):
        epic = await ok(setup, "create_node", kind="epic", title="An epic")
        story = await ok(setup, "create_node", kind="story", title="A story", parent=epic["ref"])
        task = await ok(setup, "create_node", kind="task", title="A task", parent=story["ref"])
        await ok(holder, "claim_node", node=task["ref"])
        renamed = await ok(setup, "update_node", node=story["ref"], expected_version=1, title="B")
        assert renamed["version"] == 2
        held = await refused(asker, "claim_node", node=task["ref"])
        stale = await refused(
            asker, "update_node", node=story["ref"], expected_version=1, title="C"
        )
    assert "is claimed by other" in held and "version" in stale

    async with runner(project, "coder") as coder:
        caught = await run_ok(
            coder,
            f"""
            const said: string[] = [];
            try {{ await vrum.claim_node({{ node: '{task["ref"]}' }}); said.push('claimed'); }}
            catch (error) {{ said.push((error as Error).name + ': ' + (error as Error).message); }}
            try {{
              await vrum.update_node({{ node: '{story["ref"]}', expected_version: 1, title: 'C' }});
              said.push('changed');
            }} catch (error) {{ said.push((error as Error).message); }}
            return said;
            """,
        )
        assert caught["result"] == [f"VrumError: {held}", stale], caught
        assert [(call["tool"], call["state"]) for call in caught["calls"]] == [
            ("claim_node", "refused"),
            ("update_node", "refused"),
        ]

        # Not caught, the same refusal stops the script, in the tool's sentence, with what ran.
        words = await run_refused(
            coder,
            f"await vrum.list_nodes({{}});\nawait vrum.claim_node({{ node: '{task['ref']}' }});",
        )
        assert "A call that Vrum refused stopped the script at line 2" in words
        assert held in words
        assert "Calls it made, in order: list_nodes (done), claim_node (refused)." in words
        assert "None of the calls changed anything." in words
    async with connect(project.mcp_url, project.key) as setup:
        assert (await ok(setup, "get_node", node=task["ref"]))["claimed_by"] == "other"
        assert (await ok(setup, "get_node", node=story["ref"]))["title"] == "B"


async def test_a_key_cannot_reach_another_project_even_naming_its_references() -> None:
    owner = register()
    mine, theirs = make_project(owner), make_project(owner)
    word = unique_word()
    async with connect(mine.mcp_url, mine.key) as own:
        await ok(own, "create_node", kind="epic", title="My only epic")
    async with connect(theirs.mcp_url, theirs.key) as other:
        for number in range(3):
            await ok(other, "create_node", kind="epic", title=f"Hidden {word} {number}")
        story = await ok(other, "create_node", kind="story", title="Hidden story", parent="epic:1")
        await ok(other, "create_node", kind="task", title="Hidden task", parent=story["ref"])
        before = await export_everything(other)

        async with runner(mine, "coder") as coder:
            ran = await run_ok(
                coder,
                f"""
                const said: string[] = [];
                for (const node of ['epic:3', 'story:1', 'task:1']) {{
                  for (const attempt of [
                    () => vrum.get_node({{ node }}),
                    () => vrum.update_node({{ node, expected_version: 1, title: 'Taken over' }}),
                    () => vrum.claim_node({{ node }}),
                    () => vrum.list_links({{ node }}),
                  ]) {{
                    try {{ await attempt(); said.push('reached'); }}
                    catch (error) {{ said.push((error as Error).message); }}
                  }}
                }}
                const found = await vrum.list_nodes({{ query: '{word}' }});
                const everything = await vrum.list_nodes({{ limit: 50 }});
                return {{
                  said,
                  found: found.items.length,
                  mine: everything.items.map((i) => i.ref),
                }};
                """,
            )
            outcome = ran["result"]
            assert len(outcome["said"]) == 12 and "reached" not in outcome["said"], outcome
            assert all("There is no" in said for said in outcome["said"]), outcome["said"]
            assert outcome["found"] == 0 and outcome["mine"] == ["epic:1"]

            # A reference that exists in both is the caller's own: it changes mine and not theirs.
            await run_ok(
                coder,
                "await vrum.update_node({ node: 'epic:1', expected_version: 1, title: 'Mine' });",
            )
        assert await export_everything(other) == before
        assert (await ok(other, "get_node", node="epic:1"))["title"] == f"Hidden {word} 0"
    async with connect(mine.mcp_url, mine.key) as own:
        assert (await ok(own, "get_node", node="epic:1"))["title"] == "Mine"


def app_log() -> tuple[list[dict[str, Any]], str]:
    """The app container's log: its JSON lines, and all of its text."""
    result = compose("logs", "--no-log-prefix", "--no-color", "app")
    assert result.returncode == 0, result.stderr
    text = result.stdout + result.stderr
    lines: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("{"):
            try:
                lines.append(json.loads(line))
            except ValueError:
                continue
    return lines, text


async def test_the_log_has_a_line_for_each_inner_call_and_none_of_the_script_or_its_data() -> None:
    project = make_project(register())
    name = f"logged-{unique_word()}"
    marks = {part: unique_word() for part in ("script", "title", "body", "result")}
    key = make_key(project, name, code_mode=True)
    async with connect(project.mcp_url, key) as coder:
        ran = await run_ok(
            coder,
            f"""
            // {marks["script"]}
            const made = await vrum.create_node({{
              kind: 'epic', title: '{marks["title"]}', body: '{marks["body"]}',
            }});
            const read = await vrum.get_node({{ node: made.ref }});
            try {{
              await vrum.get_node({{ node: 'epic:99' }});
            }} catch (error) {{
              read.title += '!';
            }}
            await vrum.list_nodes({{}});
            return '{marks["result"]}' + read.title;
            """,
        )
        assert ran["result"] == marks["result"] + marks["title"] + "!"
        refusing = await run_ok(
            coder,
            """
            for (let attempt = 0; attempt < 3; attempt++) {
              try { await vrum.get_node({ node: 'epic:99' }); } catch (error) {}
            }
            return 'done';
            """,
        )
        assert [call["state"] for call in refusing["calls"]] == ["refused"] * 3
    await asyncio.sleep(0.5)

    lines, text = app_log()
    inner = [line for line in lines if line.get("via") == "run" and line.get("actor") == name]
    first, second = inner[:4], inner[4:]
    assert [(line["tool"], line["outcome"], line["seq"]) for line in first] == [
        ("create_node", "ok", 1),
        ("get_node", "ok", 2),
        ("get_node", "refused", 3),
        ("list_nodes", "ok", 4),
    ]
    assert [(line["tool"], line["outcome"], line["seq"]) for line in second] == [
        ("get_node", "refused", number) for number in (1, 2, 3)
    ]
    ids = {line["run_id"] for line in first}, {line["run_id"] for line in second}
    assert len(ids[0]) == 1 and len(ids[1]) == 1 and ids[0] != ids[1]
    for line in inner:
        assert re.fullmatch(r"[0-9a-f]{12}", line["run_id"]) and line["event"] == "tool_call"
        assert line["project"] == project.id and line["actor_kind"] == "agent"
        assert re.fullmatch(r"[0-9a-f]{12}", line["script"]) and line["ms"] >= 0
    assert len({line["script"] for line in first}) == 1
    assert {line["script"] for line in first} != {line["script"] for line in second}

    finished = {
        line["run_id"]: line
        for line in lines
        if line.get("event") == "run_finished" and line.get("actor") == name
    }
    run_id = next(iter(ids[0]))
    assert finished[run_id]["outcome"] == "ok" and finished[run_id]["calls"] == 4
    assert finished[run_id]["refused_calls"] == 1
    # Three refusals in one run are noted once more, as a sign of something wrong.
    warned = [line for line in lines if line.get("event") == "run_refused_calls"]
    assert [line["count"] for line in warned if line["actor"] == name] == [3]
    assert [line["run_id"] for line in warned if line["actor"] == name] == [next(iter(ids[1]))]
    # The run itself is one call of the tool run, with no script in its line.
    outer = [line for line in lines if line.get("tool") == "run" and line.get("actor") == name]
    assert [line["outcome"] for line in outer] == ["ok", "ok"] and "via" not in outer[0]

    for forbidden in (*marks.values(), key, "Bearer"):
        assert forbidden not in text, f"the log holds {forbidden[:8]}..."


# -- 5: the sandbox, through run on the real stack --------------------------------------------

LOCKED_OUT = "A script has no files, network or system access and cannot import anything"


@pytest.mark.parametrize(
    ("code", "words"),
    [
        (
            "return (globalThis as any).Deno.readTextFileSync('/etc/passwd');",
            "Cannot read properties of undefined (reading 'readTextFileSync')",
        ),
        (
            "return (globalThis as any).Deno.writeTextFileSync('/tmp/x', 'x');",
            "Cannot read properties of undefined (reading 'writeTextFileSync')",
        ),
        (
            "return await (globalThis as any).fetch('http://example.com');",
            "globalThis.fetch is not a function",
        ),
        (
            "return await (globalThis as any).Deno.connect({ hostname: '1.1.1.1', port: 80 });",
            "Cannot read properties of undefined (reading 'connect')",
        ),
        (
            "return (globalThis as any).Deno.env.toObject();",
            "Cannot read properties of undefined (reading 'env')",
        ),
        (
            "return (globalThis as any).process.env;",
            "Cannot read properties of undefined (reading 'env')",
        ),
        (
            "return new (globalThis as any).Deno.Command('ls').outputSync();",
            "Cannot read properties of undefined (reading 'Command')",
        ),
        ("return await import('https://example.com/module.js');", LOCKED_OUT),
        ("return await import('file:///etc/passwd');", LOCKED_OUT),
        (
            "(globalThis as any).Deno.exit(3);\nreturn 1;",
            "Cannot read properties of undefined (reading 'exit')",
        ),
        (
            "(globalThis as any).process.exit(3);\nreturn 1;",
            "Cannot read properties of undefined (reading 'exit')",
        ),
    ],
    ids=[
        "read-a-file",
        "write-a-file",
        "fetch",
        "connect",
        "environment-of-deno",
        "environment-of-process",
        "run-a-program",
        "import-a-remote-module",
        "import-a-file",
        "deno-exit",
        "process-exit",
    ],
)
async def test_a_script_has_no_files_network_environment_or_programs(code: str, words: str) -> None:
    project = make_project(register())
    async with runner(project) as coder:
        said = await run_refused(coder, code)
        assert words in said, said
        assert "root:" not in said and "It made no calls, so nothing was changed." in said
        assert (await run_ok(coder, "return 'still here'"))["result"] == "still here"


async def test_typed_scripts_cannot_even_name_deno_fetch_or_an_import() -> None:
    project = make_project(register())
    async with runner(project) as coder:
        for code, words in (
            ("return Deno.readTextFileSync('/etc/passwd');", "Cannot find name 'Deno'"),
            ("return await fetch('http://example.com');", "Cannot find name 'fetch'"),
            ("return process.env;", "Cannot find name 'process'"),
            ("const z = await import('node:zlib');\nreturn z;", LOCKED_OUT),
        ):
            said = await run_refused(coder, code)
            assert "does not match the types of the functions it calls" in said, said
            assert words in said and said.endswith("Nothing was changed.")
        said = await run_refused(coder, "import fs from 'node:fs';\nreturn 1;")
        assert "has a syntax error at line 1" in said
        assert "`import` and `export` are not available" in said


async def test_what_is_left_of_the_language_has_no_way_out_even_past_the_type_check() -> None:
    project = make_project(register())
    async with runner(project) as coder:
        found = await run_ok(
            coder,
            """
            const names = [
              'Deno', 'process', 'require', 'fetch', 'Worker', 'WebAssembly', 'Buffer',
            ];
            const global: any = Function('return this')();
            const constructor: any = (async () => {}).constructor;
            return {
              byName: names.map((name) => typeof (globalThis as any)[name]),
              viaFunction: names.map((name) => typeof global[name]),
              viaAsync: await constructor('return typeof Deno + typeof process')(),
              viaPrototype: await Object.getPrototypeOf(async function () {})
                .constructor('return typeof require')(),
              viaEval: eval('typeof Deno'),
              indirect: (0, eval)('typeof process'),
            };
            """,
        )
        assert found["result"] == {
            "byName": ["undefined"] * 7,
            "viaFunction": ["undefined"] * 7,
            "viaAsync": "undefinedundefined",
            "viaPrototype": "undefined",
            "viaEval": "undefined",
            "indirect": "undefined",
        }
        # The node: modules are there to import, and empty.
        found = await run_ok(
            coder,
            """
            const kept: Record<string, number> = {};
            const builtins = ['zlib', 'vm', 'child_process', 'fs', 'os', 'worker_threads', 'net'];
            for (const name of builtins) {
              // @ts-ignore: the type check would stop this before it ran
              const module: any = await import('node:' + name);
              kept[name] = Object.keys(module).length;
            }
            return kept;
            """,
        )
        assert set(found["result"].values()) == {0}, found["result"]


async def test_an_endless_loop_is_stopped_at_the_time_limit_and_the_sandbox_serves_on() -> None:
    project = make_project(register())
    async with runner(project) as coder:
        for code in (
            "while (true) {}",
            "for (;;) { await new Promise((resolve) => setTimeout(resolve, 5)); }",
        ):
            started = time.monotonic()
            words = await run_refused(coder, code, timeout_seconds=2)
            elapsed = time.monotonic() - started
            assert "The script ran out of time (2 seconds) and was stopped." in words, words
            assert "It made no calls, so nothing was changed." in words
            assert 1.9 <= elapsed < 9, elapsed
            assert (await run_ok(coder, "return 1 + 1"))["result"] == 2
            assert (await ok(coder, "explore"))["functions"]


@pytest.mark.parametrize(
    "code",
    [
        "const bag: string[][] = [];\n"
        "while (true) bag.push(new Array(100000).fill('x'.repeat(10)));",
        "return new Uint8Array(1.5e9).length;",
    ],
    ids=["heap", "off-heap"],
)
async def test_a_memory_bomb_is_stopped_and_the_sandbox_serves_on(code: str) -> None:
    project = make_project(register())
    async with runner(project) as coder:
        words = await run_refused(coder, code)
        assert "The script used more memory than a script may, so it was stopped." in words
        assert "It made no calls, so nothing was changed." in words
        assert (await run_ok(coder, "return 'alive'"))["result"] == "alive"


async def test_a_script_that_kills_its_own_runner_leaves_the_server_and_sandbox_serving() -> None:
    project = make_project(register())
    async with runner(project) as coder:
        words = await run_refused(coder, "function f(): number { return f() + 1; }\nreturn f();")
        assert "stopped with an error at line 1" in words and "call stack" in words
        # The runner is killed for memory after a change that had finished, which stays done.
        words = await run_refused(
            coder,
            "await vrum.create_node({ kind: 'epic', title: 'Made first' });\n"
            "const bag: string[][] = [];\n"
            "while (true) bag.push(new Array(100000).fill('x'.repeat(10)));",
        )
        assert "used more memory than a script may" in words
        assert "Calls that change something finished, or may have: create_node." in words
        assert (await run_ok(coder, "return (await vrum.list_nodes({})).items.length"))[
            "result"
        ] == 1
        assert (await ok(coder, "explore"))["functions"]
    async with connect(project.mcp_url, project.key) as agent:
        assert await ok(agent, "server_status")


async def test_what_is_over_a_limit_is_refused_or_cut_in_words() -> None:
    project = make_project(register())
    async with runner(project) as coder:
        # A result over 256 KB is not returned, one under it is.
        assert len((await run_ok(coder, "return 'x'.repeat(100000);"))["result"]) == 100000
        words = await run_refused(coder, "return 'x'.repeat(300000);")
        assert "The result is 293 KB, and a script may return at most 256 KB." in words

        # Past 200 calls a script is told so, and can catch it.
        found = await run_ok(
            coder,
            """
            let made = 0;
            for (let attempt = 0; attempt < 205; attempt++) {
              try { await vrum.list_nodes({ limit: 1 }); made++; }
              catch (error) { return { made, said: (error as Error).message }; }
            }
            return { made };
            """,
        )
        assert found["result"] == {
            "made": 200,
            "said": "This script has already made its 200 calls. Do the rest in another run.",
        }
        assert len(found["calls"]) == 200

        # Past 100 changes too, and only the hundred were made.
        found = await run_ok(
            coder,
            """
            let made = 0;
            for (let attempt = 0; attempt < 105; attempt++) {
              try { await vrum.create_node({ kind: 'epic', title: `Epic ${attempt}` }); made++; }
              catch (error) { return { made, said: (error as Error).message }; }
            }
            return { made };
            """,
        )
        assert found["result"]["made"] == 100
        assert "This script has made 100 calls that change something" in found["result"]["said"]
        assert "This call was not made." in found["result"]["said"]
        async with connect(project.mcp_url, project.key) as agent:
            assert len(await all_nodes(agent, kind="epic")) == 100

        # Twenty kilobytes of log are cut to sixteen, and the answer says so.
        found = await run_ok(
            coder,
            "for (let line = 0; line < 400; line++) {\n"
            "  console.log(`line ${line} ${'x'.repeat(60)}`);\n"
            "}\n"
            "return 'done';",
        )
        logs = found["logs"]
        assert found["result"] == "done" and logs[0].startswith("line 0 ")
        assert logs[-1] == "(more was written than the 16 KB that are kept, so the rest was cut)"
        assert sum(len(line) + 1 for line in logs[:-1]) <= 16 * 1024

        # A script over a megabyte is not run.
        words = await run_refused(coder, "// " + "x" * (1024 * 1024 + 10) + "\nreturn 1;")
        assert words.endswith(
            "The script is larger than 1 MB. Split it into smaller scripts. Nothing was run."
        )

        # A time limit that is not one, and nothing to run.
        for bad in (0, 121):
            words = await run_refused(coder, "return 1", timeout_seconds=bad)
            assert "timeout_seconds has to be a whole number of seconds from 1 to 120." in words
        assert "There is no script to run." in await run_refused(coder, "   ")


async def attempt(project: Project, key: str, code: str) -> tuple[bool, str, float]:
    """One `run` on a client of its own: was it refused, what it said, and how long it took."""
    started = time.monotonic()
    async with connect(project.mcp_url, key) as client:
        result = await client.call_tool("run", {"code": code})
    text = " ".join(block.text for block in result.content if isinstance(block, TextContent))
    return bool(result.is_error), text, time.monotonic() - started


async def test_six_scripts_at_once_four_run_and_the_others_are_refused_at_once() -> None:
    project = make_project(register())
    keys = [make_key(project, f"coder-{number}", code_mode=True) for number in range(6)]
    wait = "await new Promise((resolve) => setTimeout(resolve, 6000));\nreturn 'finished';"
    answers = await asyncio.gather(*(attempt(project, key, wait) for key in keys))
    finished = [one for one in answers if not one[0]]
    turned_away = [one for one in answers if one[0]]
    assert len(finished) == 4 and len(turned_away) == 2, answers
    for _, words, seconds in turned_away:
        assert "Scripts are busy: this server is already running as many as it allows." in words
        assert "Try again in a moment. Nothing was run." in words
        assert seconds < 5, seconds
    assert all("finished" in text for _, text, _ in finished)
    # When they are done, there is room again.
    assert not (await attempt(project, keys[0], "return 'room'"))[0]


def in_the_sandbox_container(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", sandbox_id(), *argv],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_the_sandbox_container_has_no_network_no_secret_and_no_root() -> None:
    interfaces = in_the_sandbox_container(
        "python",
        "-c",
        "import os; base = '/sys/class/net'; "
        "print(sorted(n for n in os.listdir(base) if os.path.isdir(f'{base}/{n}') "
        "and open(f'{base}/{n}/operstate').read().strip() != 'down'))",
    )
    assert interfaces.stdout.strip() == "['lo']", interfaces
    routes = in_the_sandbox_container("cat", "/proc/net/route").stdout.splitlines()
    assert len(routes) == 1, routes
    reach = in_the_sandbox_container(
        "python",
        "-c",
        "import socket; s = socket.socket(); s.settimeout(3); s.connect(('1.1.1.1', 53))",
    )
    assert reach.returncode != 0 and "Network is unreachable" in reach.stderr, reach.stderr

    assert in_the_sandbox_container("id", "-u").stdout.strip() == "10002"
    status = in_the_sandbox_container("cat", "/proc/self/status").stdout
    assert re.search(r"^CapEff:\s+0+$", status, re.M) and re.search(
        r"^NoNewPrivs:\s+1$", status, re.M
    )
    assert in_the_sandbox_container("touch", "/not-allowed").returncode != 0

    environment = in_the_sandbox_container("env").stdout
    assert "VRUM" not in environment and os.environ["VRUM_DB_PASSWORD"] not in environment
    for name in ("PASSWORD", "SECRET", "TOKEN"):
        assert name not in environment
    assert in_the_sandbox_container("ls", "/secrets", "/run/secrets").returncode != 0
    assert in_the_sandbox_container("ls", "/run/sandbox").stdout.split() == ["run.sock"]


def wait_until_healthy(seconds: int = 90) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", sandbox_id()],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if state == "healthy":
            return
        time.sleep(1)
    pytest.fail("the sandbox did not become healthy again")


async def test_without_the_sandbox_run_says_so_explore_still_works_and_start_brings_it_back() -> (
    None
):
    project = make_project(register())
    async with runner(project) as coder:
        assert (await run_ok(coder, "return 1"))["result"] == 1
        try:
            assert compose("stop", "sandbox").returncode == 0
            words = await run_refused(coder, "return 1")
            assert "Running code is not available: the sandbox service is not running." in words
            assert "Nothing was run and nothing was changed." in words
            assert "explore still works." in words
            assert await names_of(coder) == {"explore", "run"}
            assert (await ok(coder, "explore", names=["list_nodes"]))["declarations"]
        finally:
            assert compose("start", "sandbox").returncode == 0
            wait_until_healthy()
        assert (await run_ok(coder, "return 2"))["result"] == 2


async def test_the_sandbox_attacks_all_fail_against_the_stacks_own_sandbox() -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.sandbox_attacks",
        "--container",
        sandbox_id(),
        cwd=REPO_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await asyncio.wait_for(process.communicate(), 900)
    lines = output.decode(errors="replace").splitlines()
    text = "\n".join(lines)
    assert process.returncode == 0, text[-4000:]
    assert not [line for line in lines if line.startswith("FAIL")], text[-4000:]
    summary = re.fullmatch(r"(\d+) passed, 0 failed", [line for line in lines if line.strip()][-1])
    assert summary and int(summary.group(1)) >= 100, text[-1000:]


# -- 6: errors are plain words, and a failed run says what it had done -------------------------


async def test_a_failed_run_says_where_why_which_calls_ran_and_whether_anything_changed() -> None:
    project = make_project(register())
    async with runner(project) as coder:
        words = await run_refused(coder, "const a = 1;\nconst b = 2;\nconst c = (;\nreturn a;")
        assert "has a syntax error at line 3." in words and words.endswith("Nothing was changed.")

        words = await run_refused(coder, "const a = 1;\nreturn await vrum.list_nodez({});")
        assert "does not match the types of the functions it calls" in words
        assert "Line 2: Property 'list_nodez' does not exist" in words
        assert "Did you mean 'list_nodes'?" in words and words.endswith("Nothing was changed.")

        words = await run_refused(coder, "await vrum.get_node({ node: 'task:999' });")
        assert "A call that Vrum refused stopped the script at line 1:" in words
        assert (
            "There is no task 999 in this project. Use list_nodes to find the one you want."
            in words
        )
        assert "Calls it made, in order: get_node (refused)." in words
        assert "None of the calls changed anything." in words

        # Reads, then a throw: nothing changed. Writes, then a throw: it says so, and what.
        words = await run_refused(
            coder,
            "await vrum.list_nodes({});\nawait vrum.list_nodes({});\nthrow new Error('stop');",
        )
        assert "stopped with an error at line 3: stop." in words
        assert "Calls it made, in order: list_nodes (done, 2 times)." in words
        assert "None of the calls changed anything." in words
        words = await run_refused(
            coder,
            "await vrum.create_node({ kind: 'epic', title: 'One' });\n"
            "await vrum.create_node({ kind: 'epic', title: 'Two' });\n"
            "throw new Error('stop here');",
        )
        assert "stopped with an error at line 3: stop here." in words
        assert "Calls it made, in order: create_node (done, 2 times)." in words
        assert (
            "Calls that change something finished, or may have: create_node (2 times). "
            "Look at what is there now before you run it again" in words
        )
        words = await run_refused(
            coder,
            "await vrum.create_node({ kind: 'epic', title: 'Three' });\nwhile (true) {}",
            timeout_seconds=2,
        )
        assert "ran out of time (2 seconds)" in words
        assert "Calls that change something finished, or may have: create_node." in words

        # What it had written is told too.
        words = await run_refused(coder, "console.log('halfway');\nthrow new Error('late');")
        assert "What it wrote before it stopped: halfway" in words
    async with connect(project.mcp_url, project.key) as agent:
        titles = [node["title"] for node in await all_nodes(agent, kind="epic")]
    assert titles == ["One", "Two", "Three"]


# -- 7: a key that is changed while its script runs --------------------------------------------

# The script writes, waits three seconds inside the sandbox (so that the owner has time to change
# the key through the board, as a person does on the Users page), and writes again.
WAIT_THEN_WRITE = """
const before = await vrum.create_node({ kind: "epic", title: "written before the change" });
await new Promise((resolve) => setTimeout(resolve, 3000));
const after = await vrum.create_node({ kind: "epic", title: "written after the change" });
return [before.ref, after.ref];
"""


async def changed_meanwhile(project: Project, name: str, change: Any) -> None:
    """Wait a moment, then do `change` to the key called `name` through the board."""
    await asyncio.sleep(1.5)
    change(key_row(project, name)["id"])


async def epic_titles(project: Project) -> list[str]:
    async with connect(project.mcp_url, project.key) as agent:
        return [node["title"] for node in await all_nodes(agent, kind="epic")]


async def test_a_key_revoked_while_its_script_waits_is_refused_at_its_next_write() -> None:
    project = make_project(register())

    def revoke(key_id: int) -> None:
        reply = project.owner.call("DELETE", f"/board/api/projects/{project.id}/keys/{key_id}")
        assert reply.status == 200, reply.body

    async with runner(project, "sleeper") as client:
        meanwhile = asyncio.create_task(changed_meanwhile(project, "sleeper", revoke))
        started = time.monotonic()
        words = await run_refused(client, WAIT_THEN_WRITE)
        await meanwhile
    # It was stopped at its second write, in the words a revoked key gets, and the run did not
    # wait for its own time limit.
    assert time.monotonic() - started < 20
    assert SCRIPT_KEY_REVOKED in words
    assert "Calls it made, in order: create_node (done), create_node (refused)." in words
    assert "Calls that change something finished, or may have: create_node." in words
    # What the first write made is there; the second write was never made.
    assert await epic_titles(project) == ["written before the change"]


async def test_a_key_changed_to_one_tool_at_a_time_while_its_script_waits_stops_the_script() -> (
    None
):
    project = make_project(register())

    def to_tools(key_id: int) -> None:
        reply = project.owner.call(
            "PATCH", f"/board/api/projects/{project.id}/keys/{key_id}", {"code_mode": False}
        )
        assert reply.status == 200, reply.body

    async with runner(project, "sleeper") as client:
        meanwhile = asyncio.create_task(changed_meanwhile(project, "sleeper", to_tools))
        words = await run_refused(client, WAIT_THEN_WRITE)
        await meanwhile
    assert SCRIPT_KEY_NOW_TOOLS in words
    assert await epic_titles(project) == ["written before the change"]


async def test_a_function_taken_from_a_key_while_its_script_waits_is_refused_and_it_goes_on() -> (
    None
):
    project = make_project(register())
    script = """
await vrum.create_node({ kind: "epic", title: "first" });
await new Promise((resolve) => setTimeout(resolve, 3000));
let said = "not refused";
try {
  await vrum.create_node({ kind: "epic", title: "second" });
} catch (error) {
  said = (error as Error).message;
}
const listed = await vrum.list_nodes({ kind: "epic" });
return { said, titles: listed.items.map((item) => item.title) };
"""

    def take_create_away(key_id: int) -> None:
        reply = project.owner.call(
            "PATCH",
            f"/board/api/projects/{project.id}/keys/{key_id}",
            {"tools": ["list_nodes", "get_node"]},
        )
        assert reply.status == 200, reply.body

    async with runner(project, "sleeper") as client:
        meanwhile = asyncio.create_task(changed_meanwhile(project, "sleeper", take_create_away))
        answer = await run_ok(client, script)
        await meanwhile
    assert answer["result"]["said"] == "Error executing tool create_node: " + key_not_allowed(
        "sleeper", "create_node"
    )
    assert answer["result"]["titles"] == ["first"]
    assert [call["state"] for call in answer["calls"]] == ["done", "refused", "done"]
    assert await epic_titles(project) == ["first"]


# -- 8: the tools marked as reads change nothing -------------------------------------------------


async def test_every_tool_marked_as_a_read_changes_nothing_when_a_read_only_key_calls_it() -> None:
    project = make_project(register())
    reads = sorted(
        tool.name
        for tool in the_server()._tool_manager.list_tools()
        if tool.annotations and tool.annotations.read_only_hint and tool.name != "explore"
    )
    assert len(reads) == 28
    async with connect(project.mcp_url, project.key) as admin:
        world = await rich_world(admin, project)
        epic, story, task = world["epic"]["ref"], world["story1"]["ref"], world["task2"]["ref"]
        rule, note = world["rule1"]["id"], world["decision2"]["id"]
        arguments: dict[str, list[dict[str, Any]]] = {
            "server_status": [{}],
            "get_node": [{"node": epic}, {"node": task}],
            "list_nodes": [{}, {"detail": True}, {"query": "kind:task status:blocked"}],
            "node_history": [{"node": story}],
            "list_links": [{"node": story}, {"node": task}],
            "ready_work": [{}],
            "who_is_working": [{}],
            "list_asks": [{}, {"status": "open"}],
            "needs_a_person": [{}],
            "list_plans": [{}],
            "list_plan_templates": [{}],
            "suggest_parent": [{"node": story}, {"title": "Log in by email", "kind": "story"}],
            "list_placements": [{}],
            "changes_since": [{}, {"newest": True}],
            "get_rule": [{"rule_id": rule}],
            "list_rules": [{}],
            "rule_history": [{"rule_id": rule}],
            "coverage_report": [{}],
            "get_definition_of_done": [{}],
            "get_note": [{"note_id": note}],
            "list_notes": [{}, {"status": "in_force"}],
            "note_history": [{"note_id": note}],
            "recall": [
                {"node": story},
                {"node": task, "with_similar": True},
                {"rule_id": rule},
            ],
            "search": [{"query": "log in"}, {"query": "log in", "mode": "words"}],
            "similar_items": [{"node": story}, {"text": "log in with an email"}],
            "find_overlaps": [{}],
            "find_mentions": [{"node": story}, {"rule_id": rule}],
            "export_graph": [{}],
        }
        assert sorted(arguments) == reads
        before = [await export_everything(admin), await ok(admin, "who_is_working")]
        key = make_limited_key(project, "read-only", reads)
        async with connect(project.mcp_url, key) as reader:
            listed = {tool.name for tool in (await reader.list_tools()).tools}
            assert listed == set(reads)
            for name in reads:
                for each in arguments[name]:
                    await ok(reader, name, **each)
        after = [await export_everything(admin), await ok(admin, "who_is_working")]
    # The same events (none new), the same versions, claims and records.
    for old, new in zip(before, after, strict=True):
        old.pop("as_of", None)
        new.pop("as_of", None)
    assert after == before
