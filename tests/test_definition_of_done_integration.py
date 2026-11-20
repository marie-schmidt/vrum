"""The definition of done (M31) against the running containers: one text for the whole project that
only a person changes, through the board's own call, and that an agent is told in its briefing.
What has to hold: a project starts with an empty one at version 1; a change needs the version that
is stored and a text that is different; an agent's call, a viewer's call and a secret are refused
and change nothing; every change is an event that holds the text before and after, about no item;
the briefing carries it, cut to its share of the briefing when it is long; and a project's
definition is its own."""

import hashlib
from typing import Any

import pytest

from tests.helpers import connect, make_tree, ok, refused
from tests.world import Person, Project, Reply, join_project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

A_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2


def key_of(words: str) -> str:
    """What the server calls a check: the first 8 hex digits of a SHA-256 of its words."""
    return hashlib.sha256(words.encode()).hexdigest()[:8]


def on_board(person: Person, project: Project, tool: str, **arguments: Any) -> Reply:
    """What the board's page does when a person presses a button: the tool, as that person."""
    return person.call("POST", f"/board/api/projects/{project.id}/tool/{tool}", arguments)


def set_it(person: Person, project: Project, body: str, version: int) -> Reply:
    return on_board(person, project, "set_definition_of_done", body=body, expected_version=version)


async def test_a_project_starts_empty_and_a_person_changes_it_with_the_version() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        start = await ok(agent, "get_definition_of_done")
        assert start == {
            "body": "",
            "version": 1,
            "updated_at": None,
            "updated_by": None,
            "updated_by_kind": None,
            "items": [],
        }

        first = set_it(owner, project, "- Tests pass\n- A person can try it", 1)
        assert first.status == 200, first.body
        assert (first.body["body"], first.body["version"]) == (
            "- Tests pass\n- A person can try it",
            2,
        )
        assert (first.body["updated_by"], first.body["updated_by_kind"]) == (owner.name, "person")
        assert first.body["updated_at"]
        # Its list lines are its checks, each with a key that comes from its words.
        assert first.body["items"] == [
            {"item": key_of("Tests pass"), "text": "Tests pass", "person_only": False, "ticks": 0},
            {
                "item": key_of("A person can try it"),
                "text": "A person can try it",
                "person_only": False,
                "ticks": 0,
            },
        ]

        second = set_it(owner, project, "- Tests pass\n- Docs updated", 2)
        assert second.status == 200 and second.body["version"] == 3
        read = await ok(agent, "get_definition_of_done")
        assert (read["body"], read["version"]) == ("- Tests pass\n- Docs updated", 3)
        assert read["updated_by"] == owner.name

        # Each change is one event, about no item, with the text as it was and as it became.
        events = (await ok(agent, "changes_since"))["items"]
        assert [event["action"] for event in events] == ["definition_of_done_changed"] * 2
        one, two = events
        assert one["before"] == {"body": "", "version": 1}
        assert one["after"] == {"body": "- Tests pass\n- A person can try it", "version": 2}
        assert two["before"] == one["after"]
        assert two["after"] == {"body": "- Tests pass\n- Docs updated", "version": 3}
        for event in events:
            assert (event["actor"], event["actor_kind"]) == (owner.name, "person")
            assert [event[field] for field in ("node", "other_node", "rule_id", "note_id")] == [
                None
            ] * 4
        # It belongs to no item, so no item's own history shows it.
        epic = await ok(agent, "create_node", kind="epic", title="An epic")
        assert (await ok(agent, "node_history", node=epic["ref"]))["items"][0]["action"] == (
            "create"
        )
        assert [
            event["id"] for event in (await ok(agent, "changes_since", node=epic["ref"]))["items"]
        ] == [event["id"] for event in (await ok(agent, "node_history", node=epic["ref"]))["items"]]


async def test_an_agent_a_viewer_a_stale_version_and_a_secret_change_nothing() -> None:
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer")
    async with connect(project.mcp_url, project.key) as agent:
        assert set_it(owner, project, "- Tests pass", 1).status == 200

        # An agent is refused whatever its key may do for other tools, and nothing changes.
        said = await refused(
            agent, "set_definition_of_done", body="- Whatever I like", expected_version=2
        )
        assert "not allowed to use set_definition_of_done" in said

        # A viewer may read it and may not change it.
        assert on_board(viewer, project, "get_definition_of_done").status == 200
        assert set_it(viewer, project, "- Whatever a viewer likes", 2).status == 403

        stale = set_it(owner, project, "- Somebody else was first", 1)
        assert stale.status != 200
        assert "at version 2, but you sent version 1" in str(stale.body)
        assert "get_definition_of_done" in str(stale.body)

        same = set_it(owner, project, "- Tests pass", 2)
        assert same.status != 200 and "Nothing to change" in str(same.body)

        leaked = set_it(owner, project, f"- Deploy with {A_TOKEN}", 2)
        assert leaked.status != 200 and "secret" in str(leaked.body).lower()
        assert A_TOKEN not in str(leaked.body)

        kept = await ok(agent, "get_definition_of_done")
        assert (kept["body"], kept["version"]) == ("- Tests pass", 2)
        events = (await ok(agent, "changes_since"))["items"]
        assert [event["after"]["version"] for event in events] == [2]


async def test_the_briefing_carries_it_and_cuts_a_long_one_to_its_share() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        await make_tree(agent)
        empty = (await ok(agent, "next_work", peek=True))["context"]
        assert (empty["definition_of_done"], empty["definition_of_done_chars"]) == ("", 0)

        short = "- Tests pass\n- A person can try it\n- Nothing is left half done"
        assert set_it(owner, project, short, 1).status == 200
        context = (await ok(agent, "next_work", peek=True))["context"]
        assert context["definition_of_done"] == short
        assert context["definition_of_done_chars"] == len(short)
        assert context["cut_note"] is None

        long = "- A line that says what done means.\n" * 200
        assert set_it(owner, project, long, 2).status == 200
        # A quarter of the room is the most it takes, and the briefing says so and how long it is.
        small = (await ok(agent, "next_work", peek=True, context_chars=1000))["context"]
        assert small["definition_of_done"] == long[:250]
        assert small["definition_of_done_chars"] == len(long)
        assert "definition of done" in small["cut_note"] and "context_chars" in small["cut_note"]
        assert small["characters"] <= small["max_characters"] == 1000
        large = (await ok(agent, "next_work", peek=True, context_chars=100_000))["context"]
        assert large["definition_of_done"] == long and large["cut_note"] is None


async def test_each_project_has_a_definition_of_its_own() -> None:
    mine, theirs = register(), register()
    first, second = make_project(mine), make_project(theirs)
    assert set_it(mine, first, "- Mine", 1).status == 200
    async with connect(second.mcp_url, second.key) as agent:
        read = await ok(agent, "get_definition_of_done")
        assert (read["body"], read["version"]) == ("", 1)
        assert set_it(theirs, second, "- Theirs", 1).status == 200
    async with connect(first.mcp_url, first.key) as agent:
        assert (await ok(agent, "get_definition_of_done"))["body"] == "- Mine"
        # Somebody who is not in the project cannot read or change it.
        assert on_board(theirs, first, "get_definition_of_done").status != 200
        assert set_it(theirs, first, "- Hijacked", 2).status != 200
