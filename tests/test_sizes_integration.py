"""Sizes against the running containers (M32): a story or task has one size, xs to xl, worth 1, 2,
3, 5 and 8 points, and an epic adds up. What has to hold: a size is set, changed and cleared with
the tools, each change is an ordinary update with before and after, a bad size, a size on an epic
and a size given and cleared together are refused in plain words; what a task, a story and an epic
add up to (points, points done, items not sized) leaves dropped work out and counts a story's tasks
or, when it has none, the story itself; `size:` and `no:size` find the right work; a plan and
`update_nodes` take sizes; and the briefing says how big the work is. Every test works in a project
of its own, so its numbers are exact."""

from typing import Any

import pytest
from mcp import Client

from tests.helpers import connect, ok, refused
from tests.world import make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

POINTS = {"xs": 1, "s": 2, "m": 3, "l": 5, "xl": 8}


async def made(
    client: Client, kind: str, title: str, parent: dict[str, Any] | None = None, **more: Any
) -> dict[str, Any]:
    return await ok(
        client,
        "create_node",
        kind=kind,
        title=title,
        parent=None if parent is None else parent["ref"],
        **more,
    )


def adds_up(node: dict[str, Any]) -> tuple[int, int, int]:
    return node["points_total"], node["points_done"], node["unsized"]


async def read(client: Client, node: dict[str, Any]) -> dict[str, Any]:
    return await ok(client, "get_node", node=node["ref"])


async def test_a_size_is_set_changed_and_cleared_and_each_change_is_in_the_history() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", "Sizing")
        story = await made(client, "story", "A story", epic)
        task = await made(client, "task", "A task", story, size="m")
        assert (task["size"], task["points"]) == ("m", 3)
        assert (story["size"], story["points"], epic["size"], epic["points"]) == (None,) * 4

        bigger = await ok(
            client, "update_node", node=task["ref"], expected_version=1, size="xl", priority="high"
        )
        assert (bigger["size"], bigger["points"], bigger["version"]) == ("xl", 8, 2)
        cleared = await ok(
            client, "update_node", node=task["ref"], expected_version=2, clear_size=True
        )
        assert (cleared["size"], cleared["points"], cleared["version"]) == (None, None, 3)
        task = cleared
        for size, points in POINTS.items():
            task = await ok(
                client,
                "update_node",
                node=task["ref"],
                expected_version=task["version"],
                size=size,
            )
            assert (task["size"], task["points"]) == (size, points)

        # A size change is an ordinary update, with the size as it was and as it became.
        history = (await ok(client, "node_history", node=task["ref"]))["items"]
        assert history[0]["action"] == "create" and history[0]["after"]["size"] == "m"
        assert [(e["action"], e["before"], e["after"]) for e in history[1:4]] == [
            (
                "update",
                {"size": "m", "priority": "normal", "version": 1},
                {"size": "xl", "priority": "high", "version": 2},
            ),
            ("update", {"size": "xl", "version": 2}, {"size": None, "version": 3}),
            ("update", {"size": None, "version": 3}, {"size": "xs", "version": 4}),
        ]
        # The same size is shown by every way of reading it.
        listed = (await ok(client, "list_nodes", kind="task"))["items"][0]
        assert (listed["size"], listed["points"]) == ("xl", 8)


async def test_a_size_that_is_not_one_of_the_five_or_is_on_an_epic_is_refused() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", "An epic")
        story = await made(client, "story", "A story", epic)
        words = "size must be one of xs, s, m, l, xl"
        assert words in await refused(
            client, "create_node", kind="task", title="x", parent=story["ref"], size="huge"
        )
        assert words in await refused(
            client, "update_node", node=story["ref"], expected_version=1, size="M"
        )
        sentence = "An epic has no size of its own: it adds up the size of what is in it."
        assert sentence in await refused(client, "create_node", kind="epic", title="x", size="m")
        assert sentence in await refused(
            client, "update_node", node=epic["ref"], expected_version=1, size="s"
        )
        assert sentence in await refused(
            client, "update_node", node=epic["ref"], expected_version=1, clear_size=True
        )
        both = await refused(
            client, "update_node", node=story["ref"], expected_version=1, size="s", clear_size=True
        )
        assert "Give a new size or ask for it to be cleared (clear_size), not both." in both
        nothing = await refused(
            client, "update_node", node=story["ref"], expected_version=1, clear_size=True
        )
        assert "Nothing to change" in nothing and "size" in nothing
        # Nothing was changed by any of them.
        assert (await read(client, story))["version"] == 1
        assert (await read(client, epic))["version"] == 1
        assert len((await ok(client, "list_nodes"))["items"]) == 2


async def test_what_work_adds_up_to_leaves_dropped_work_out_and_counts_what_is_not_sized() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", "The epic")
        # A story with tasks adds up its tasks (its own size is not counted twice).
        busy = await made(client, "story", "Busy", epic, size="xl")
        done = await made(client, "task", "Done", busy, size="s")
        await ok(client, "update_node", node=done["ref"], expected_version=1, status="done")
        await made(client, "task", "Open", busy, size="l")
        await made(client, "task", "Not sized", busy)
        gone = await made(client, "task", "Dropped", busy, size="xl")
        await ok(client, "update_node", node=gone["ref"], expected_version=1, status="dropped")
        # A story with no tasks is its own size, and so is one whose tasks were all dropped.
        await made(client, "story", "Alone", epic, size="m")
        lonely = await made(client, "story", "Lonely", epic, size="xs")
        also_gone = await made(client, "task", "Also dropped", lonely, size="xl")
        await ok(client, "update_node", node=also_gone["ref"], expected_version=1, status="dropped")
        # A story with no size, no tasks, that is not done is one that needs a size.
        await made(client, "story", "Bare", epic)
        # A done story with no tasks counts its own size as done; a dropped one counts for nothing.
        finished = await made(client, "story", "Finished", epic, size="l")
        await ok(client, "update_node", node=finished["ref"], expected_version=1, status="done")
        dropped = await made(client, "story", "Dropped story", epic, size="xl")
        await ok(client, "update_node", node=dropped["ref"], expected_version=1, status="dropped")

        by_title = {n["title"]: n for n in (await ok(client, "list_nodes", limit=50))["items"]}
        # A task: its own points, none when dropped or not sized, and not sized when it is open.
        assert adds_up(by_title["Done"]) == (2, 2, 0)
        assert adds_up(by_title["Open"]) == (5, 0, 0)
        assert adds_up(by_title["Not sized"]) == (0, 0, 1)
        assert adds_up(by_title["Dropped"]) == (0, 0, 0)
        # A story: the sum over its tasks that are not dropped, or its own points.
        assert adds_up(by_title["Busy"]) == (7, 2, 1)
        assert adds_up(by_title["Alone"]) == (3, 0, 0)
        assert adds_up(by_title["Lonely"]) == (1, 0, 0)
        assert adds_up(by_title["Bare"]) == (0, 0, 1)
        assert adds_up(by_title["Finished"]) == (5, 5, 0)
        assert adds_up(by_title["Dropped story"]) == (0, 0, 0)
        # An epic: the sum over its stories that are not dropped.
        assert adds_up(by_title["The epic"]) == (7 + 3 + 1 + 0 + 5, 2 + 5, 2)
        shown = await read(client, epic)
        assert adds_up(shown) == adds_up(by_title["The epic"])
        # Finishing the open task moves its points to done and leaves one item not sized.
        open_task = by_title["Open"]
        await ok(client, "update_node", node=open_task["ref"], expected_version=1, status="done")
        assert adds_up(await read(client, busy)) == (7, 7, 1)
        assert adds_up(await read(client, epic)) == (16, 12, 2)
        # Sizing what was not sized takes it out of the count.
        bare = by_title["Bare"]
        await ok(client, "update_node", node=bare["ref"], expected_version=1, size="s")
        assert adds_up(await read(client, epic)) == (18, 12, 1)


async def test_size_and_no_size_find_the_work_and_never_an_epic() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", "Epic")
        busy = await made(client, "story", "Busy", epic)
        small = await made(client, "task", "Small", busy, size="xs")
        medium = await made(client, "task", "Medium", busy, size="m")
        large = await made(client, "task", "Large", busy, size="l")
        await made(client, "task", "Bare task", busy)
        sized = await made(client, "story", "Sized story", epic, size="m")
        bare = await made(client, "story", "Bare story", epic)

        async def found(query: str) -> set[str]:
            page = await ok(client, "list_nodes", query=query, limit=50)
            return {node["title"] for node in page["items"]}

        assert await found("size:m") == {medium["title"], sized["title"]}
        assert await found("size:m,l") == {medium["title"], large["title"], sized["title"]}
        assert await found("size:XS kind:task") == {small["title"]}
        assert await found("kind:task -size:m,l") == {small["title"], "Bare task"}
        # No size: the work that still needs one. A story that has tasks adds up theirs, and an
        # epic has none of its own, so neither is found.
        assert await found("no:size") == {"Bare task", bare["title"]}
        assert await found("no:size kind:task") == {"Bare task"}
        assert await found("size:xl") == set()
        said = await refused(client, "list_nodes", query="size:big")
        assert "size must be one of xs, s, m, l, xl" in said
        # A story that loses its last live task is one that needs a size of its own. (A dropped
        # task with no size is still one with no size.)
        for task in (small, medium, large):
            await ok(client, "update_node", node=task["ref"], expected_version=1, status="dropped")
        bare_task = (await ok(client, "list_nodes", query='"Bare task"'))["items"][0]
        await ok(client, "update_node", node=bare_task["ref"], expected_version=1, status="dropped")
        assert await found("no:size") == {"Busy", bare["title"], "Bare task"}


async def test_a_plan_and_update_nodes_take_sizes_and_a_bad_size_refuses_the_whole_plan() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        plan = await ok(
            client,
            "create_plan",
            items=[
                {"key": "e", "kind": "epic", "title": "Planned"},
                {"key": "s", "kind": "story", "title": "A story", "parent": "e", "size": "l"},
                {"key": "t1", "kind": "task", "title": "One", "parent": "s", "size": "xs"},
                {"key": "t2", "kind": "task", "title": "Two", "parent": "s"},
            ],
        )
        refs = {item["key"]: item["ref"] for item in plan["created"]}
        sizes = {key: (await ok(client, "get_node", node=ref))["size"] for key, ref in refs.items()}
        assert sizes == {"e": None, "s": "l", "t1": "xs", "t2": None}
        assert adds_up(await ok(client, "get_node", node=refs["e"])) == (1, 0, 1)

        bad = await refused(
            client,
            "create_plan",
            items=[
                {"key": "e", "kind": "epic", "title": "Never made", "size": "m"},
                {"key": "s", "kind": "story", "title": "Never made", "parent": "e", "size": "big"},
            ],
        )
        assert (
            "an epic has no size of its own" in bad and "size must be one of xs, s, m, l, xl" in bad
        )
        assert "Nothing was created" in bad
        titles = {n["title"] for n in (await ok(client, "list_nodes", limit=50))["items"]}
        assert "Never made" not in titles

        # update_nodes sets and clears sizes, and one bad change does not stop the others.
        two = await ok(client, "get_node", node=refs["t2"])
        one = await ok(client, "get_node", node=refs["t1"])
        changed = await ok(
            client,
            "update_nodes",
            changes=[
                {"node": refs["t2"], "expected_version": two["version"], "size": "m"},
                {"node": refs["t1"], "expected_version": one["version"], "clear_size": True},
                {"node": refs["e"], "expected_version": 1, "size": "s"},
            ],
        )
        assert (changed["applied"], changed["refused"]) == (2, 1)
        assert "no size of its own" in changed["results"][2]["refusal"]
        assert (await ok(client, "get_node", node=refs["t2"]))["size"] == "m"
        assert (await ok(client, "get_node", node=refs["t1"]))["size"] is None


async def test_the_briefing_says_how_big_the_work_is() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", "Epic")
        story = await made(client, "story", "Story", epic, size="l")
        first = await made(client, "task", "First", story, size="s", priority="urgent")
        second = await made(client, "task", "Second", story, size="m")
        await ok(
            client, "add_link", source=second["ref"], target=first["ref"], link_type="needs_first"
        )
        took = await ok(client, "next_work")
        assert took["work"]["ref"] == first["ref"] and took["work"]["size"] == "s"
        assert took["work"]["points"] == 2
        # What it belongs to says how big it is, and what waits for it says so too.
        parents = {parent["ref"]: parent["size"] for parent in took["context"]["parents"]}
        assert parents == {epic["ref"]: None, story["ref"]: "l"}
        waiting = took["context"]["unblocks"]
        assert [(item["node"]["ref"], item["node"]["size"]) for item in waiting] == [
            (second["ref"], "m")
        ]
        finished = await ok(
            client, "finish_work", node=first["ref"], outcome="done", note="Done in a go."
        )
        assert [(item["ref"], item["size"]) for item in finished["unblocked"]] == [
            (second["ref"], "m")
        ]
