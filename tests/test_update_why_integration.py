"""Saying why work is blocked or dropped (M33) against the running containers. `update_node` takes
`why` together with a change of status to blocked or dropped, and keeps it as a handoff note on
the item in the same step, whether a person or an agent made the change. What has to hold: the
note is in the item's history, the change feed, `get_node` and the list of who is working, under
the right name and kind, written in the same step as the change; and a `why` that does not go with
such a change, a blank one and one that holds a secret are refused in plain words with nothing
changed. Each test works in a project of its own."""

from typing import Any

import pytest
from mcp import Client

from tests.helpers import connect, make_tree, ok, refused
from tests.test_checks_integration import A_TOKEN, as_person, history_of, sql_rows, words_of
from tests.world import HARNESS_AGENT, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

WHY = "The lock vendor has not sent the new firmware yet."


async def handoffs_of(client: Client, node: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in await history_of(client, node) if e["action"] == "handoff"]


async def test_a_person_says_why_and_it_is_kept_as_a_handoff_note_in_the_same_step() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic, story, task = await make_tree(agent)
        blocked = as_person(
            owner,
            project,
            "update_node",
            node=task["ref"],
            expected_version=task["version"],
            status="blocked",
            why=f"  {WHY}  ",
        )
        assert blocked.status == 200, blocked.body
        assert (blocked.body["status"], blocked.body["version"]) == ("blocked", 2)

        events = await history_of(agent, task)
        assert [e["action"] for e in events] == ["create", "update", "handoff"]
        update, handoff = events[1], events[2]
        assert (update["actor"], update["actor_kind"]) == (owner.name, "person")
        assert update["after"] == {"status": "blocked", "version": 2}
        assert (handoff["actor"], handoff["actor_kind"], handoff["node"]) == (
            owner.name,
            "person",
            task["ref"],
        )
        assert (handoff["before"], handoff["after"]) == (
            None,
            {"outcome": "blocked", "note": WHY},
        )
        # Written in the same transaction as the change.
        same_step = sql_rows(
            project,
            f"SELECT action, xid FROM events WHERE node = '{task['ref']}' "
            "AND action IN ('update', 'handoff') ORDER BY id",
        )
        assert [row[0] for row in same_step] == ["update", "handoff"]
        assert len({row[1] for row in same_step}) == 1

        # The item shows it, the feed carries it, and a person with no claim is in the list.
        shown = await ok(agent, "get_node", node=task["ref"])
        assert shown["handoff_count"] == 1
        assert (shown["last_handoff"]["outcome"], shown["last_handoff"]["note"]) == ("blocked", WHY)
        assert (shown["last_handoff"]["actor"], shown["last_handoff"]["actor_kind"]) == (
            owner.name,
            "person",
        )
        feed = (await ok(agent, "changes_since", node=task["ref"], limit=50))["items"]
        assert [e["action"] for e in feed][-2:] == ["update", "handoff"]
        working = await ok(agent, "who_is_working")
        (entry,) = [a for a in working["agents"] if a["actor"] == owner.name]
        assert (entry["kind"], entry["state"], entry["holding"], entry["lapsed"]) == (
            "person",
            "finished",
            [],
            [],
        )
        assert (entry["last_handoff"]["node"], entry["last_handoff"]["outcome"]) == (
            task["ref"],
            "blocked",
        )
        assert entry["last_handoff"]["note"] == WHY

        # Dropping works the same, along with other changes in the one call, and for an epic.
        dropped = as_person(
            owner,
            project,
            "update_node",
            node=story["ref"],
            expected_version=story["version"],
            title="A story that is no longer needed",
            status="dropped",
            why="The city cancelled it.",
        )
        assert dropped.status == 200, dropped.body
        (note,) = await handoffs_of(agent, story)
        assert note["after"] == {"outcome": "dropped", "note": "The city cancelled it."}
        update = [e for e in await history_of(agent, story) if e["action"] == "update"][-1]
        assert update["after"]["title"] == "A story that is no longer needed"
        assert update["after"]["status"] == "dropped"
        epic_blocked = as_person(
            owner,
            project,
            "update_node",
            node=epic["ref"],
            expected_version=epic["version"],
            status="blocked",
            why="Waiting for the budget.",
        )
        assert epic_blocked.status == 200, epic_blocked.body
        assert (await ok(agent, "get_node", node=epic["ref"]))["handoff_count"] == 1
        working = await ok(agent, "who_is_working")
        (entry,) = [a for a in working["agents"] if a["actor"] == owner.name]
        assert entry["last_handoff"]["node"] == epic["ref"]


async def test_an_agent_can_say_why_too_and_it_is_the_agents_handoff_note() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        _, _, task = await make_tree(agent)
        offered = {tool.name: tool for tool in (await agent.list_tools()).tools}["update_node"]
        assert offered.input_schema["properties"]["why"]["description"] == (
            "Why it is blocked or dropped. Saved as a handoff note on the item. Only with "
            "status blocked or dropped."
        )
        assert "why" not in offered.input_schema.get("required", [])
        changed = await ok(
            agent,
            "update_node",
            node=task["ref"],
            expected_version=task["version"],
            status="blocked",
            why=WHY,
        )
        assert changed["status"] == "blocked"
        (note,) = await handoffs_of(agent, task)
        assert (note["actor"], note["actor_kind"]) == (HARNESS_AGENT, "agent")
        assert note["after"] == {"outcome": "blocked", "note": WHY}
        # The next agent that works on it is told why, with the handoffs of its history.
        assert (await ok(agent, "get_node", node=task["ref"]))["last_handoff"]["note"] == WHY


async def test_a_why_that_does_not_go_with_blocked_or_dropped_is_refused_and_nothing_changes() -> (
    None
):
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        _, story, task = await make_tree(agent)
        version = task["version"]
        goes_with = (
            "why goes with a change of status to blocked or dropped. Give status blocked or "
            "dropped as well, or leave why out. Nothing was changed."
        )

        async def nothing_changed() -> None:
            shown = await ok(agent, "get_node", node=task["ref"])
            assert (shown["status"], shown["version"], shown["handoff_count"]) == (
                "ready",
                version,
                0,
            )

        # No status, or a status that is not blocked or dropped.
        for given in ({}, {"status": "in_progress"}, {"status": "done"}, {"status": "new"}):
            said = await refused(
                agent,
                "update_node",
                node=task["ref"],
                expected_version=version,
                title="Changed as well",
                why=WHY,
                **given,
            )
            assert words_of(said) == goes_with
            await nothing_changed()

        # Blank.
        for blank in ("", "   \n"):
            said = await refused(
                agent,
                "update_node",
                node=task["ref"],
                expected_version=version,
                status="blocked",
                why=blank,
            )
            assert words_of(said) == (
                "why cannot be empty. Say why it is blocked, so that whoever comes next knows, or "
                "leave why out."
            )
            await nothing_changed()

        # A secret is refused like any other text, and rolls the status change back with it.
        said = await refused(
            agent,
            "update_node",
            node=task["ref"],
            expected_version=version,
            status="dropped",
            why=f"The key is {A_TOKEN}",
        )
        assert "contains what looks like" in said and A_TOKEN not in said
        await nothing_changed()

        # A change of status is needed: a node that already is blocked has none to go with.
        await ok(agent, "update_node", node=task["ref"], expected_version=version, status="blocked")
        again = await refused(
            agent,
            "update_node",
            node=task["ref"],
            expected_version=version + 1,
            status="blocked",
            why=WHY,
        )
        assert words_of(again) == (
            f"{task['ref']} is already blocked, so there is no change of status for why to go "
            "with. Leave why out. Nothing was changed."
        )
        assert (await ok(agent, "get_node", node=task["ref"]))["handoff_count"] == 0

        # An old version is still refused first, and a handoff is never made for it.
        stale = await refused(
            agent,
            "update_node",
            node=story["ref"],
            expected_version=99,
            status="blocked",
            why=WHY,
        )
        assert "Nothing was changed" in stale or "version" in stale
        assert await handoffs_of(agent, story) == []

        # Without a why nothing is written beyond the change itself, as before.
        await ok(
            agent,
            "update_node",
            node=task["ref"],
            expected_version=version + 1,
            status="dropped",
        )
        assert await handoffs_of(agent, task) == []
