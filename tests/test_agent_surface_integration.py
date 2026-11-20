"""M28's proof on the real stack, through a real MCP client: an agent key is offered a chosen set of
at most 25 tools and nothing else, the board still reaches every tool as the signed-in person, an
owner can name more tools for one key, and a requirement is placed or moved with one tool."""

from typing import Any

import pytest
from mcp import Client

from tests.helpers import connect, make_rule, make_tree, ok, refused, unique_word
from tests.world import Project, make_limited_key, make_project, register
from vrum.surface import AGENT_SURFACE, AGENT_TOOLS, MOST_AGENT_TOOLS

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def names_of(client: Client) -> set[str]:
    return {tool.name for tool in (await client.list_tools()).tools}


def standard_key(project: Project) -> str:
    """A key with no list of its own: the key an owner gets by leaving the choice alone."""
    return make_limited_key(project, f"standard-{unique_word()}", None)


async def test_a_standard_key_is_offered_the_agent_set_and_nothing_else() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, standard_key(project)) as agent:
        offered = await names_of(agent)
    assert offered == AGENT_SURFACE and len(offered) == len(AGENT_TOOLS) <= MOST_AGENT_TOOLS
    # What a person does on the board is not in it.
    assert {
        "set_confirmation",
        "node_history",
        "update_nodes",
        "export_graph",
        "get_definition_of_done",
        "set_definition_of_done",
    }.isdisjoint(offered)


async def test_a_tool_outside_the_set_is_refused_in_plain_words_and_changes_nothing() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, standard_key(project)) as agent:
        epic, story, task = await make_tree(agent)
        for tool, arguments in (
            ("node_history", {"node": task["ref"]}),
            ("update_nodes", {"changes": [{"node": task["ref"], "status": "done"}]}),
            ("move_node", {"node": task["ref"], "parent": story["ref"], "expected_version": 1}),
            ("set_confirmation", {"note_id": 1, "confirmation": "confirmed", "person": "Marie"}),
            ("export_graph", {}),
            ("reindex", {}),
        ):
            words = await refused(agent, tool, **arguments)
            assert f"{tool} is not one of the tools Vrum offers agents" in words, words
            assert "done on the board, by a person" in words and "Users page" in words
        # Nothing was changed by any of them.
        assert (await ok(agent, "get_node", node=task["ref"]))["version"] == task["version"]
        assert epic["ref"] != story["ref"]


async def test_the_board_still_reaches_every_tool_as_the_signed_in_person() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, standard_key(project)) as agent:
        _epic, _story, task = await make_tree(agent)
    # The same tools an agent key is refused, called from the board by the owner.
    history = owner.call(
        "POST", f"/board/api/projects/{project.id}/tool/node_history", {"node": task["ref"]}
    )
    assert history.status == 200, history.body
    status = owner.call("POST", f"/board/api/projects/{project.id}/tool/server_status", {})
    assert status.status == 200, status.body


async def test_the_tools_page_says_which_tools_an_agent_is_offered() -> None:
    owner = register()
    reply = owner.call("GET", "/board/api/tools")
    assert reply.status == 200 and reply.body["most_agent_tools"] == MOST_AGENT_TOOLS
    flagged = {tool["name"] for tool in reply.body["tools"] if tool["offered_to_agents"]}
    assert flagged == AGENT_SURFACE
    assert all(tool["name"] != "move_rule" for tool in reply.body["tools"])


async def test_an_owner_can_name_tools_outside_the_set_for_one_key() -> None:
    owner = register()
    project = make_project(owner)
    wanted = ["list_nodes", "export_graph", "node_history"]
    key = make_limited_key(project, "backup-agent", wanted)
    async with connect(project.mcp_url, key) as backup:
        assert await names_of(backup) == set(wanted)
        exported = await ok(backup, "export_graph", limit=10)
        assert exported["version"] == 6
        assert "create_node" in await refused(backup, "create_node", kind="epic", title="x")
    # A key the owner left standard is still offered only the set.
    async with connect(project.mcp_url, standard_key(project)) as standard:
        assert await names_of(standard) == AGENT_SURFACE


async def test_the_owner_may_not_give_a_key_a_tool_only_a_person_can_use() -> None:
    owner = register()
    project = make_project(owner)
    reply = owner.call(
        "POST",
        f"/board/api/projects/{project.id}/keys",
        {"name": "sneaky", "tools": ["list_nodes", "set_confirmation"]},
    )
    assert reply.status == 400 and "Only a person can use set_confirmation" in str(reply.body)
    reply = owner.call(
        "POST",
        f"/board/api/projects/{project.id}/keys",
        {"name": "sneaky-too", "tools": ["list_nodes", "set_definition_of_done"]},
    )
    assert reply.status == 400, reply.body
    assert "Only a person can use set_definition_of_done" in str(reply.body)


async def test_place_rule_places_a_rule_and_moves_a_placed_one_in_one_step() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, standard_key(project)) as agent:
        epic, story, _task = await make_tree(agent)
        other = await ok(agent, "create_node", kind="story", title="Other", parent=epic["ref"])
        rule = await make_rule(agent, "Cards are never stored")
        assert rule["node"] is None

        placed = await ok(
            agent, "place_rule", rule_id=rule["id"], expected_version=1, node=story["ref"]
        )
        assert placed["node"] == story["ref"] and placed["version"] == 2

        moved = await ok(
            agent, "place_rule", rule_id=rule["id"], expected_version=2, node=other["ref"]
        )
        assert moved["node"] == other["ref"] and moved["version"] == 3

        same = await refused(
            agent, "place_rule", rule_id=rule["id"], expected_version=3, node=other["ref"]
        )
        assert "already on" in same and "nothing moved" in same
        stale = await refused(
            agent, "place_rule", rule_id=rule["id"], expected_version=1, node=story["ref"]
        )
        assert "list_rules" in stale and "get_rule" not in stale

        # The history keeps the difference: it was placed, then moved, and both stories heard.
    actions = owner.call(
        "POST", f"/board/api/projects/{project.id}/tool/rule_history", {"rule_id": rule["id"]}
    )
    assert actions.status == 200, actions.body
    found: list[dict[str, Any]] = actions.body["items"]
    assert [event["action"] for event in found][-2:] == ["rule_placed", "rule_moved"]
