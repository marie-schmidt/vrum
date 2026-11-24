"""What each agent key may do (M23, and M28's standard key), through a real MCP client and the
board's own HTTP API, against the real containers.

A key made without a choice may use the tools Vrum offers agents, and a key whose owner ticked
tools may use exactly those. What has to hold: a limited key's tool list shows only its tools and a
call to any other is refused in plain words with nothing changed; a change of what a key may do
applies on the agent's very next request, on a connection that is already open; what is ticked is
checked and kept (sorted, no repeats, at least one tool, no tool only a person can use, only tools
the server has); only the owner changes it; what a limited key writes is recorded under its name as
an agent; two keys of one project keep their own allowances; and the log says why a call was
refused and never holds the key."""

import json
from typing import Any

import pytest
from mcp import Client

from tests.helpers import compose, connect, ok, refused, run_sql, unique_word
from tests.world import (
    Person,
    Project,
    api,
    join_project,
    make_limited_key,
    make_project,
    register,
)
from vrum.surface import AGENT_SURFACE

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def keys_path(project: Project) -> str:
    return f"/board/api/projects/{project.id}/keys"


def live_tools(owner: Person) -> list[dict[str, Any]]:
    """The server's own tool list, as the Tools page reads it."""
    reply = owner.call("GET", "/board/api/tools")
    assert reply.status == 200, reply.body
    return list(reply.body["tools"])


def the_reads(tools: list[dict[str, Any]]) -> list[str]:
    """The tools that only read and that a key can be given one by one (`explore` only reads,
    but it comes with the way of calling, not with an allowance)."""
    return sorted(
        tool["name"] for tool in tools if not tool["changes_something"] and not tool["code_mode"]
    )


def what_a_key_can_have(tools: list[dict[str, Any]]) -> list[str]:
    """Every tool but the one only a person can use and the two of code mode: all an agent key
    may be given as an allowance."""
    return sorted(
        tool["name"] for tool in tools if not tool["people_only"] and not tool["code_mode"]
    )


async def names_of(client: Client) -> set[str]:
    return {tool.name for tool in (await client.list_tools()).tools}


def key_named(owner: Person, project: Project, name: str) -> dict[str, Any]:
    listed = owner.call("GET", keys_path(project)).body["keys"]
    return next(key for key in listed if key["name"] == name and key["revoked_at"] is None)


def refusal_for(agent: str, tool: str) -> str:
    return (
        f"Error executing tool {tool}: This agent key ({agent}) is not allowed to use {tool}. "
        "Ask the project's owner to change what the key may do, on the Users page. Nothing was "
        "changed."
    )


# -- the tool list and the refusal -------------------------------------------------------------


async def test_a_limited_key_sees_only_its_tools_and_is_refused_the_rest_with_nothing_changed() -> (
    None
):
    owner = register()
    project = make_project(owner)
    tools = live_tools(owner)
    allowed = [*the_reads(tools), "create_node"]
    key = make_limited_key(project, "scribe-agent", allowed)
    async with (
        connect(project.mcp_url, key) as scribe,
        connect(project.mcp_url, project.key) as full,
    ):
        assert await names_of(scribe) == set(allowed)
        assert await names_of(full) == set(what_a_key_can_have(tools))
        assert len(allowed) < len(tools)

        epic = await ok(scribe, "create_node", kind="epic", title="Allowed")
        assert (await ok(scribe, "get_node", node=epic["ref"]))["title"] == "Allowed"

        # Refused in plain words, whatever the arguments are, and nothing is written.
        assert await refused(scribe, "add_note", text="Not allowed", why="Here") == (
            refusal_for("scribe-agent", "add_note")
        )
        assert await refused(scribe, "update_node", node=epic["ref"], title="Not allowed") == (
            refusal_for("scribe-agent", "update_node")
        )
        assert await refused(scribe, "import_graph", document={}) == refusal_for(
            "scribe-agent", "import_graph"
        )
        assert (await ok(full, "list_notes"))["items"] == []
        assert (await ok(full, "get_node", node=epic["ref"]))["title"] == "Allowed"

        # A name the server does not have gets the usual answer for an unknown tool.
        unknown = await refused(scribe, "no_such_tool")
        assert "not allowed" not in unknown and "no_such_tool" in unknown
        # An allowed tool with arguments that do not fit is still told so in the usual words.
        assert "the arguments are not valid" in await refused(scribe, "create_node", kind="epic")

        # The board's own calls are a person's, limited by their role and never by a key.
        made = owner.call(
            "POST",
            f"/board/api/projects/{project.id}/tool/add_note",
            {"text": "A person records", "why": "Here"},
        )
        assert made.status == 200 and made.body["recorded_by_kind"] == "person"


async def test_a_change_applies_on_the_next_call_of_an_open_connection_and_null_is_standard() -> (
    None
):
    owner = register()
    project = make_project(owner)
    tools = live_tools(owner)
    reads = the_reads(tools)
    key = make_limited_key(project, "reader-agent", reads)
    key_id = key_named(owner, project, "reader-agent")["id"]
    path = f"{keys_path(project)}/{key_id}"
    async with connect(project.mcp_url, key) as reader:
        assert await names_of(reader) == set(reads)
        assert await refused(reader, "create_node", kind="epic", title="Early") == (
            refusal_for("reader-agent", "create_node")
        )

        widened = owner.call("PATCH", path, {"tools": ["create_node", *reads, "create_node"]})
        assert widened.status == 200, widened.body
        assert widened.body["key"]["tools"] == sorted({*reads, "create_node"})
        assert widened.body["key"]["name"] == "reader-agent"
        assert "create_node" in await names_of(reader)
        first = await ok(reader, "create_node", kind="epic", title="Now allowed")

        narrowed = owner.call("PATCH", path, {"tools": reads})
        assert narrowed.status == 200 and narrowed.body["key"]["tools"] == reads
        assert "create_node" not in await names_of(reader)
        assert "is not allowed to use create_node" in await refused(
            reader, "create_node", kind="epic", title="Late"
        )

        # No list of its own: the tools Vrum offers agents, and no others (node_history is one of
        # the reads, which the key was allowed a moment ago).
        standard = owner.call("PATCH", path, {"tools": None})
        assert standard.status == 200 and standard.body["key"]["tools"] is None
        assert await names_of(reader) == AGENT_SURFACE
        later = await ok(reader, "create_node", kind="epic", title="Standard again")
        titles = [node["title"] for node in (await ok(reader, "list_nodes"))["items"]]
        assert titles == [first["title"], later["title"]]
        assert "is not one of the tools Vrum offers agents" in await refused(
            reader, "node_history", node=first["ref"]
        )

        # What the owner reads in the key list is what the key may do.
        assert key_named(owner, project, "reader-agent")["tools"] is None


async def test_keys_made_without_a_choice_get_the_standard_tools_and_say_so() -> None:
    owner = register()
    project = make_project(owner)
    tools = live_tools(owner)
    assert {tool["name"] for tool in tools if tool["people_only"]} == {
        "set_confirmation",
        "set_definition_of_done",
        "answer_ask",
        "remove_gate",
        "approve_plan",
        "request_plan_changes",
        "decide_placement",
    }
    shape = {
        "id",
        "name",
        "hint",
        "created_at",
        "last_used_at",
        "revoked_at",
        "tools",
        "code_mode",
        "verifier",
        "expires_on",
        "expired",
    }

    left_out = owner.call("POST", keys_path(project), {"name": "plain-agent"})
    said_null = owner.call("POST", keys_path(project), {"name": "null-agent", "tools": None})
    assert left_out.status == said_null.status == 201
    for made in (left_out, said_null):
        assert set(made.body["key"]) == shape and made.body["key"]["tools"] is None
        assert made.body["key"]["code_mode"] is False
    # The test harness's own key was made with a list of every tool a key can have.
    everyone_else = what_a_key_can_have(tools)
    assert [key["tools"] for key in owner.call("GET", keys_path(project)).body["keys"]] == [
        everyone_else,
        None,
        None,
    ]

    for secret in (left_out.body["secret"], said_null.body["secret"]):
        async with connect(project.mcp_url, secret) as client:
            assert await names_of(client) == AGENT_SURFACE
            await ok(client, "create_node", kind="epic", title=f"By {secret[:8]}")
            assert "not one of the tools Vrum offers agents" in await refused(
                client, "server_status"
            )
    async with connect(project.mcp_url, project.key) as everything:
        assert await names_of(everything) == set(everyone_else)
        await ok(everything, "server_status")

    # Everything the server has but what only a person can use is one choice; it is stored sorted
    # and without repeats.
    made = owner.call(
        "POST", keys_path(project), {"name": "wide-agent", "tools": [*reversed(everyone_else)]}
    )
    assert made.status == 201 and made.body["key"]["tools"] == everyone_else


# -- what may be ticked ------------------------------------------------------------------------


def test_what_may_be_ticked_is_checked_and_a_refusal_changes_nothing() -> None:
    owner = register()
    project = make_project(owner)
    reads = the_reads(live_tools(owner))
    made = owner.call("POST", keys_path(project), {"name": "kept-agent", "tools": reads})
    assert made.status == 201
    path = f"{keys_path(project)}/{made.body['key']['id']}"
    before = owner.call("GET", keys_path(project)).body

    refusals = [
        ([], "Tick at least one tool, or leave the key with the standard tools."),
        (["set_confirmation"], "Only a person can use set_confirmation"),
        (["list_nodes", "set_confirmation"], "an agent key cannot be given it"),
        (["list_nodes", "drop_everything"], "This server has no tool called drop_everything."),
        ([f"tool_{number}" for number in range(8)], "tools called tool_0"),
        ("list_nodes", "has to be a list of tool names"),
        ({"list_nodes": True}, "has to be a list of tool names"),
        (7, "has to be a list of tool names"),
        (["list_nodes", 7], "has to be a list of tool names"),
        ([["list_nodes"]], "has to be a list of tool names"),
    ]
    for wrong, words in refusals:
        patched = owner.call("PATCH", path, {"tools": wrong})
        assert patched.status == 400 and words in patched.body["message"], (wrong, patched.body)
        created = owner.call("POST", keys_path(project), {"name": "never-agent", "tools": wrong})
        assert created.status == 400 and words in created.body["message"], (wrong, created.body)
    # Nothing was changed or made, and the refusal does not repeat what is odd.
    assert owner.call("GET", keys_path(project)).body == before
    odd = owner.call("PATCH", path, {"tools": ["x y z ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2]})
    assert odd.status == 400 and "ghp_" not in odd.body["message"]

    # Saying nothing at all is not a choice: the body has to say what the key may do.
    bodies: list[Any] = [{}, {"allowed": reads}, [], None]
    for body in bodies:
        reply = owner.call("PATCH", path, body)
        assert reply.status == 400 and "tools is a list of tool names" in reply.body["message"]
    assert owner.call("GET", keys_path(project)).body == before


def test_only_the_owner_changes_what_a_key_may_do_and_only_for_a_key_that_is_in_force() -> None:
    owner = register()
    project = make_project(owner)
    other = make_project(register())
    reads = the_reads(live_tools(owner))
    made = owner.call("POST", keys_path(project), {"name": "kept-agent", "tools": reads})
    path = f"{keys_path(project)}/{made.body['key']['id']}"
    before = owner.call("GET", keys_path(project)).body

    # An editor, a viewer, someone outside the project and no one signed in are all refused.
    for who in (join_project(project, "editor"), join_project(project, "viewer")):
        refused_reply = who.call("PATCH", path, {"tools": None})
        assert refused_reply.status == 403
        assert refused_reply.body["message"].startswith("Only the owner of this project can ")
        assert (
            who.call("POST", keys_path(project), {"name": "mine-agent", "tools": reads}).status
            == 403
        )
    assert other.owner.call("PATCH", path, {"tools": None}).status == 404
    assert api(owner.base, "PATCH", path, {"tools": None}).status == 401
    assert api(owner.base, "PATCH", path, {"tools": None}, cookie=owner.cookie).status == 403
    assert (
        owner.call("PATCH", path, {"tools": None}, origin="https://elsewhere.example").status == 403
    )
    assert owner.call("GET", keys_path(project)).body == before

    # A key of another project, a key that is not there and something that is not a number.
    foreign = other.owner.call("GET", keys_path(other)).body["keys"][0]["id"]
    for key_id in (foreign, 987654321, "x"):
        reply = owner.call("PATCH", f"{keys_path(project)}/{key_id}", {"tools": None})
        assert (
            reply.status == 404
            and reply.body["message"] == "There is no such key for this project."
        )

    # A key that was revoked has nothing left to change.
    assert owner.call("DELETE", path).status == 200
    gone = owner.call("PATCH", path, {"tools": None})
    assert gone.status == 409
    assert gone.body["message"] == "That key was revoked, so there is nothing to change."
    revoked = next(
        key for key in owner.call("GET", keys_path(project)).body["keys"] if key["revoked_at"]
    )
    assert revoked["tools"] == reads


# -- what a limited key does, and who it is ----------------------------------------------------


async def test_two_keys_of_one_project_keep_their_own_allowances_and_stamp_their_own_names() -> (
    None
):
    owner = register()
    project = make_project(owner)
    reads = the_reads(live_tools(owner))
    scribe_key = make_limited_key(project, "scribe-agent", [*reads, "create_node"])
    noter_key = make_limited_key(
        project, "noter-agent", [*reads, "add_note", "next_work", "finish_work"]
    )
    async with (
        connect(project.mcp_url, scribe_key) as scribe,
        connect(project.mcp_url, noter_key) as noter,
    ):
        epic = await ok(scribe, "create_node", kind="epic", title="Planned by the scribe")
        story = await ok(scribe, "create_node", kind="story", title="A story", parent=epic["ref"])
        task = await ok(scribe, "create_node", kind="task", title="A task", parent=story["ref"])
        history = (await ok(noter, "node_history", node=epic["ref"]))["items"]
        assert [(event["actor"], event["actor_kind"]) for event in history] == [
            ("scribe-agent", "agent")
        ]
        assert "is not allowed to use create_node" in await refused(
            noter, "create_node", kind="epic", title="Not the noter's"
        )

        # What each is allowed is checked tool by tool: the noter may take work and finish it, but
        # not claim or move it, and the scribe may write nodes and not notes.
        taken = await ok(noter, "next_work", within_node=epic["ref"])
        assert taken["work"]["ref"] == task["ref"]
        assert (taken["claim"]["actor"], taken["claim"]["actor_kind"]) == ("noter-agent", "agent")
        assert "is not allowed to use claim_node" in await refused(
            noter, "claim_node", node=story["ref"]
        )
        done = await ok(
            noter, "finish_work", node=task["ref"], outcome="done", note="Done by the noter."
        )
        assert (done["handoff"]["actor"], done["handoff"]["actor_kind"]) == ("noter-agent", "agent")
        note = await ok(noter, "add_note", text="The noter writes notes", why="Here")
        assert (note["recorded_by"], note["recorded_by_kind"]) == ("noter-agent", "agent")
        assert "is not allowed to use add_note" in await refused(
            scribe, "add_note", text="Not the scribe's", why="Here"
        )
        assert "is not allowed to use next_work" in await refused(scribe, "next_work")

        # What each key lists is what it may use, and nothing else.
        assert (await names_of(scribe)) == {*reads, "create_node"}
        assert (await names_of(noter)) == {*reads, "add_note", "next_work", "finish_work"}


async def test_a_tool_not_in_a_limited_keys_list_is_refused_and_a_stale_name_is_ignored() -> None:
    owner = register()
    project = make_project(owner)
    tools = live_tools(owner)
    reads = the_reads(tools)
    key = make_limited_key(project, "frozen-agent", reads)
    stale = "a_tool_a_later_version_removed"
    # The list is kept as sorted names in the database, and the database refuses an empty one.
    where = f"WHERE name = 'frozen-agent' AND project_id = {project.id}"
    stored = run_sql(
        f"SELECT allowed_tools = ARRAY[{', '.join(repr(name) for name in reads)}] "
        f"FROM agent_keys {where}",
        plain=True,
        project=None,
    )
    assert stored.stdout.strip() == "t", stored.stderr
    empty = run_sql(f"UPDATE agent_keys SET allowed_tools = '{{}}' {where}", project=None)
    assert empty.returncode != 0 and "agent_key_tools_are_not_an_empty_list" in empty.stderr
    appended = run_sql(
        f"UPDATE agent_keys SET allowed_tools = array_append(allowed_tools, '{stale}') {where}",
        project=None,
    )
    assert appended.returncode == 0, appended.stderr

    async with connect(project.mcp_url, key) as frozen:
        # A tool the server has that is not in the list, now or added by a later version of the
        # server, is refused: a list is what was ticked and nothing more.
        assert await names_of(frozen) == set(reads)
        for tool in tools:
            if tool["name"] in reads:
                continue
            result = await frozen.call_tool(tool["name"], {})
            assert result.is_error
            if tool["code_mode"]:
                # explore and run come with the way of calling, never with an allowance.
                assert "is for agent keys that call Vrum by running code" in str(result.content)
                continue
            assert refusal_for("frozen-agent", tool["name"]) in str(result.content), tool["name"]
        # A name in the list that the server no longer has is no error, now or ever.
        assert (await ok(frozen, "list_nodes"))["items"] == []
        assert key_named(owner, project, "frozen-agent")["tools"] == sorted([*reads, stale])


# -- the log -----------------------------------------------------------------------------------


async def test_the_log_says_why_a_call_was_refused_and_never_holds_the_key() -> None:
    owner = register()
    project = make_project(owner)
    agent = f"log-{unique_word()}-agent"
    key = make_limited_key(project, agent, [*the_reads(live_tools(owner))])
    async with connect(project.mcp_url, key) as client:
        await ok(client, "list_nodes")
        await refused(client, "create_node", kind="epic", title="Logged")
    logs = compose("logs", "app", "--no-color")
    assert logs.returncode == 0, logs.stderr
    lines = [
        json.loads(line[line.index("{") :])
        for line in (logs.stdout + logs.stderr).splitlines()
        if '"event"' in line and "{" in line
    ]
    mine = [
        line for line in lines if line.get("event") == "tool_call" and line.get("actor") == agent
    ]
    assert [(line["tool"], line["outcome"], line.get("reason")) for line in mine] == [
        ("list_nodes", "ok", None),
        ("create_node", "refused", "not_allowed"),
    ]
    assert all((line["actor_kind"], line["project"]) == ("agent", project.id) for line in mine)
    assert key not in logs.stdout + logs.stderr
