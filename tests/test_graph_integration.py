"""M1 against the running containers: the graph core, through a real MCP client (see ./check.sh)."""

import anyio
import pytest
from mcp import Client

from tests.helpers import all_nodes, make_tree, node_core, ok, refused

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_a_valid_epic_story_and_task_are_created_and_their_history_is_readable(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)

    assert (epic["kind"], epic["parent"], epic["status"], epic["version"]) == (
        "epic",
        None,
        "ready",
        1,
    )
    assert (story["kind"], story["parent"]) == ("story", epic["ref"])
    assert (task["kind"], task["parent"], task["priority"]) == ("task", story["ref"], "normal")
    # A node is named by its kind and its number within that kind, and by nothing else.
    for node in (epic, story, task):
        assert node["ref"] == f"{node['kind']}:{node['number']}"
        assert node["number"] >= 1
        assert not {"id", "parent_id"} & set(node)
    assert node_core(await ok(client, "get_node", node=task["ref"])) == node_core(task)

    changed = await ok(
        client,
        "update_node",
        actor="agent-1",
        node=task["ref"],
        expected_version=1,
        status="in_progress",
        title="A better task title",
    )
    assert (changed["version"], changed["status"]) == (2, "in_progress")

    history = (await ok(client, "node_history", node=task["ref"]))["items"]
    assert [(event["action"], event["actor"]) for event in history] == [
        ("create", "agent-1"),
        ("update", "agent-1"),
    ]
    assert all(event["node"] == task["ref"] for event in history)
    assert history[0]["before"] is None
    assert history[0]["after"]["parent"] == story["ref"]
    assert history[0]["after"]["version"] == 1
    assert history[1]["before"] == {"status": "new", "title": "A task", "version": 1}
    assert history[1]["after"] == {
        "status": "in_progress",
        "title": "A better task title",
        "version": 2,
    }
    assert history[0]["at"] <= history[1]["at"]


async def test_a_story_without_an_epic_is_refused(client: Client) -> None:
    before = await all_nodes(client)
    message = await refused(client, "create_node", actor="marie", kind="story", title="Orphan")
    assert "A story needs a parent epic" in message
    assert "parent" in message
    assert await all_nodes(client) == before


async def test_a_task_under_an_epic_is_refused(client: Client) -> None:
    epic, story, _ = await make_tree(client)
    before = await all_nodes(client)
    message = await refused(
        client, "create_node", actor="marie", kind="task", title="Misplaced", parent=epic["ref"]
    )
    assert f"A task belongs under a story, but {epic['ref']} is an epic" in message
    message = await refused(
        client, "create_node", actor="marie", kind="story", title="Misplaced", parent=story["ref"]
    )
    assert f"A story belongs under an epic, but {story['ref']} is a story" in message
    assert await all_nodes(client) == before


async def test_an_epic_cannot_have_a_parent_and_a_parent_must_exist(client: Client) -> None:
    epic, _, _ = await make_tree(client)
    message = await refused(
        client, "create_node", actor="marie", kind="epic", title="Nested", parent=epic["ref"]
    )
    assert "An epic has no parent" in message
    message = await refused(
        client, "create_node", actor="marie", kind="story", title="Lost", parent="epic:987654321"
    )
    assert "There is no epic 987654321 in this project" in message


async def test_a_node_cannot_be_given_a_second_parent(client: Client) -> None:
    epic, story, _ = await make_tree(client)
    other_epic = await ok(client, "create_node", actor="marie", kind="epic", title="Another epic")

    message = await refused(
        client,
        "update_node",
        actor="marie",
        node=story["ref"],
        expected_version=1,
        parent=other_epic["ref"],
    )
    assert "exactly one parent" in message
    assert "move_node" in message
    after = await ok(client, "get_node", node=story["ref"])
    assert (after["parent"], after["version"]) == (epic["ref"], 1)


async def test_a_stale_version_is_refused_and_nothing_is_overwritten(client: Client) -> None:
    _, story, _ = await make_tree(client)
    await ok(
        client,
        "update_node",
        actor="ann",
        node=story["ref"],
        expected_version=1,
        title="Ann's title",
    )

    message = await refused(
        client,
        "update_node",
        actor="bob",
        node=story["ref"],
        expected_version=1,
        title="Bob's title",
    )
    assert f"{story['ref']} is at version 2, but you sent version 1" in message
    assert "get_node" in message

    current = await ok(client, "get_node", node=story["ref"])
    assert (current["title"], current["version"]) == ("Ann's title", 2)
    actions = [
        event["action"] for event in (await ok(client, "node_history", node=story["ref"]))["items"]
    ]
    assert actions == ["create", "update"]

    mended = await ok(
        client,
        "update_node",
        actor="bob",
        node=story["ref"],
        expected_version=2,
        title="Bob's title",
    )
    assert mended["version"] == 3


async def test_parallel_edits_from_the_same_version_have_exactly_one_winner(client: Client) -> None:
    _, story, _ = await make_tree(client)
    outcomes: list[bool] = []

    async def edit(number: int) -> None:
        result = await client.call_tool(
            "update_node",
            {
                "actor": f"agent-{number}",
                "node": story["ref"],
                "expected_version": 1,
                "title": f"Title from agent {number}",
            },
        )
        outcomes.append(not result.is_error)

    async with anyio.create_task_group() as group:
        for number in range(8):
            group.start_soon(edit, number)

    assert outcomes.count(True) == 1
    assert (await ok(client, "get_node", node=story["ref"]))["version"] == 2
    history = (await ok(client, "node_history", node=story["ref"]))["items"]
    assert [event["action"] for event in history] == ["create", "update"]


async def test_moving_a_node_replaces_its_parent_in_one_logged_step(client: Client) -> None:
    epic, story, task = await make_tree(client)
    other_epic = await ok(client, "create_node", actor="marie", kind="epic", title="Another epic")
    other_story = await ok(
        client,
        "create_node",
        actor="marie",
        kind="story",
        title="Another story",
        parent=epic["ref"],
    )

    moved = await ok(
        client,
        "move_node",
        actor="ann",
        node=story["ref"],
        expected_version=1,
        new_parent=other_epic["ref"],
    )
    assert (moved["parent"], moved["version"]) == (other_epic["ref"], 2)
    assert (await ok(client, "get_node", node=task["ref"]))["parent"] == story["ref"]
    assert [n["ref"] for n in await all_nodes(client, parent=epic["ref"])] == [other_story["ref"]]

    await ok(
        client,
        "move_node",
        actor="bob",
        node=task["ref"],
        expected_version=1,
        new_parent=other_story["ref"],
    )

    history = (await ok(client, "node_history", node=story["ref"]))["items"]
    assert (history[1]["action"], history[1]["actor"]) == ("move", "ann")
    assert history[1]["before"] == {"parent": epic["ref"], "version": 1}
    assert history[1]["after"] == {"parent": other_epic["ref"], "version": 2}


async def test_moves_that_break_the_hierarchy_or_use_an_old_version_are_refused(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    other_epic = await ok(client, "create_node", actor="marie", kind="epic", title="Another epic")

    message = await refused(
        client,
        "move_node",
        actor="a",
        node=epic["ref"],
        expected_version=1,
        new_parent=story["ref"],
    )
    assert "An epic has no parent, so it cannot be moved" in message
    message = await refused(
        client,
        "move_node",
        actor="a",
        node=task["ref"],
        expected_version=1,
        new_parent=epic["ref"],
    )
    assert "A task belongs under a story" in message
    message = await refused(
        client,
        "move_node",
        actor="a",
        node=story["ref"],
        expected_version=1,
        new_parent=epic["ref"],
    )
    assert f"already under {epic['ref']}" in message
    message = await refused(
        client,
        "move_node",
        actor="a",
        node=story["ref"],
        expected_version=7,
        new_parent=other_epic["ref"],
    )
    assert "at version 1, but you sent version 7" in message

    unchanged = await ok(client, "get_node", node=story["ref"])
    assert (unchanged["parent"], unchanged["version"]) == (epic["ref"], 1)


async def test_changes_that_make_no_sense_are_refused_in_plain_words(client: Client) -> None:
    _, story, _ = await make_tree(client)
    edit = {"node": story["ref"], "expected_version": 1}

    message = await refused(client, "update_node", actor="a", status="ready", **edit)
    assert "ready is worked out by the server" in message
    message = await refused(client, "update_node", actor="a", priority="whenever", **edit)
    assert "priority must be one of low, normal, high, urgent" in message
    message = await refused(client, "update_node", actor="a", title="   ", **edit)
    assert "title cannot be empty" in message
    message = await refused(client, "update_node", actor=" ", title="x", **edit)
    assert "actor must be the name of a person or an agent" in message
    message = await refused(client, "update_node", actor="a", title="A story", **edit)
    assert "Nothing to change" in message
    message = await refused(
        client, "update_node", actor="a", node="story:987654321", expected_version=1, title="x"
    )
    assert "There is no story 987654321 in this project" in message
    message = await refused(client, "create_node", actor="a", kind="saga", title="x")
    assert "kind must be one of epic, story, task" in message

    assert (await ok(client, "get_node", node=story["ref"]))["version"] == 1


async def test_lists_are_paged_and_can_be_filtered(client: Client) -> None:
    epic = await ok(client, "create_node", actor="marie", kind="epic", title="A busy epic")
    story_refs = [
        (
            await ok(
                client,
                "create_node",
                actor="marie",
                kind="story",
                title=f"Story {number}",
                parent=epic["ref"],
            )
        )["ref"]
        for number in range(5)
    ]
    await ok(
        client,
        "update_node",
        actor="marie",
        node=story_refs[3],
        expected_version=1,
        status="done",
    )

    seen: list[str] = []
    after = None
    page_sizes = []
    while True:
        page = await ok(client, "list_nodes", parent=epic["ref"], limit=2, after=after)
        page_sizes.append(len(page["items"]))
        seen.extend(item["ref"] for item in page["items"])
        after = page["next_after"]
        if after is None:
            break
    assert page_sizes == [2, 2, 1]
    assert seen == story_refs

    done = await all_nodes(client, parent=epic["ref"], status="done")
    assert [node["ref"] for node in done] == [story_refs[3]]
    assert await all_nodes(client, parent=epic["ref"], kind="task") == []

    assert "limit must be a whole number from 1 to 200" in await refused(
        client, "list_nodes", limit=0
    )
    assert "limit must be a whole number from 1 to 200" in await refused(
        client, "list_nodes", limit=201
    )
    assert "kind must be one of" in await refused(client, "list_nodes", kind="saga")
    assert "There is no story 987654321 in this project" in await refused(
        client, "list_nodes", parent="story:987654321"
    )


async def test_nodes_are_listed_epics_then_stories_then_tasks_each_by_number(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    later_epic = await ok(client, "create_node", actor="marie", kind="epic", title="Later epic")

    nodes = await all_nodes(client, query=f"epic:{epic['number']} OR epic:{later_epic['number']}")

    assert [node["ref"] for node in nodes] == [
        epic["ref"],
        later_epic["ref"],
        story["ref"],
        task["ref"],
    ]
    # Read in pages of one, it is the same order and nothing is skipped or repeated.
    seen: list[str] = []
    after = None
    while True:
        page = await ok(
            client,
            "list_nodes",
            query=f"epic:{epic['number']} OR epic:{later_epic['number']}",
            limit=1,
            after=after,
        )
        seen.extend(item["ref"] for item in page["items"])
        after = page["next_after"]
        if after is None:
            break
    assert seen == [node["ref"] for node in nodes]


async def test_history_is_paged_oldest_first(client: Client) -> None:
    _, story, _ = await make_tree(client)
    for version in range(1, 4):
        await ok(
            client,
            "update_node",
            actor="marie",
            node=story["ref"],
            expected_version=version,
            body=f"Draft {version}",
        )

    first = await ok(client, "node_history", node=story["ref"], limit=3)
    assert [event["action"] for event in first["items"]] == ["create", "update", "update"]
    assert first["next_after_id"] == first["items"][-1]["id"]
    second = await ok(
        client, "node_history", node=story["ref"], limit=3, after_id=first["next_after_id"]
    )
    assert [event["after"]["version"] for event in second["items"]] == [4]
    assert second["next_after_id"] is None
    assert "There is no story 987654321 in this project" in await refused(
        client, "node_history", node="story:987654321"
    )
