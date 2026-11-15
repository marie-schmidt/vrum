"""M3 against the running containers: rules, placement and coverage, through a real MCP client
(see ./check.sh)."""

from typing import Any

import anyio
import pytest
from mcp import Client

from tests.helpers import (
    make_rule,
    make_stories,
    make_tree,
    ok,
    refused,
    whole_coverage_report,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def history(client: Client, rule: dict[str, Any]) -> list[dict[str, Any]]:
    return list((await ok(client, "rule_history", rule_id=rule["id"]))["items"])


async def rules_in(client: Client, story: dict[str, Any]) -> list[int]:
    page = await ok(client, "list_rules", story_id=story["id"], limit=200)
    return [rule["id"] for rule in page["items"]]


async def test_a_rule_can_be_recorded_unplaced_or_in_a_story_and_read_back(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    loose = await make_rule(client, "Loose rule", body="Why it exists")
    placed = await make_rule(client, "Placed rule", story_id=story["id"], status="settled")

    assert (loose["story_id"], loose["status"], loose["version"]) == (None, "held", 1)
    assert loose["body"] == "Why it exists"
    assert (placed["story_id"], placed["status"]) == (story["id"], "settled")
    assert await ok(client, "get_rule", rule_id=placed["id"]) == placed

    created = await history(client, placed)
    assert [(event["action"], event["actor"]) for event in created] == [("rule_created", "marie")]
    assert created[0]["before"] is None
    assert created[0]["after"]["story_id"] == story["id"]
    assert created[0]["rule_id"] == placed["id"] and created[0]["node_id"] == story["id"]


async def test_a_rule_cannot_be_placed_in_two_stories(client: Client) -> None:
    first, second = await make_stories(client, 2)
    rule = await make_rule(client)

    placed = await ok(
        client,
        "place_rule",
        actor="ann",
        rule_id=rule["id"],
        expected_version=1,
        story_id=first["id"],
    )
    assert (placed["story_id"], placed["version"]) == (first["id"], 2)

    message = await refused(
        client,
        "place_rule",
        actor="bob",
        rule_id=rule["id"],
        expected_version=2,
        story_id=second["id"],
    )
    assert f"already placed in story {first['id']}" in message
    assert "at most one story" in message and "move_rule" in message

    assert await rules_in(client, first) == [rule["id"]]
    assert await rules_in(client, second) == []
    unchanged = await ok(client, "get_rule", rule_id=rule["id"])
    assert (unchanged["story_id"], unchanged["version"]) == (first["id"], 2)
    assert [event["action"] for event in await history(client, rule)] == [
        "rule_created",
        "rule_placed",
    ]


async def test_a_rule_can_only_go_in_a_story_that_exists(client: Client) -> None:
    epic, story, task = await make_tree(client)
    rule = await make_rule(client)
    place = {"actor": "ann", "rule_id": rule["id"], "expected_version": 1}

    assert f"node {epic['id']} is an epic" in await refused(
        client, "place_rule", story_id=epic["id"], **place
    )
    assert f"node {task['id']} is a task" in await refused(
        client, "place_rule", story_id=task["id"], **place
    )
    assert "There is no node with id 987654321" in await refused(
        client, "place_rule", story_id=987654321, **place
    )
    assert "There is no rule with id 987654321" in await refused(
        client,
        "place_rule",
        actor="ann",
        rule_id=987654321,
        expected_version=1,
        story_id=story["id"],
    )
    assert "belongs to a story" in await refused(
        client, "create_rule", actor="ann", title="Misplaced", story_id=epic["id"]
    )

    still = await ok(client, "get_rule", rule_id=rule["id"])
    assert (still["story_id"], still["version"]) == (None, 1)
    assert [event["action"] for event in await history(client, rule)] == ["rule_created"]


async def test_a_move_is_one_logged_step_that_shows_in_every_history_it_touches(
    client: Client,
) -> None:
    first, second = await make_stories(client, 2)
    rule = await make_rule(client, story_id=first["id"])

    moved = await ok(
        client,
        "move_rule",
        actor="ann",
        rule_id=rule["id"],
        expected_version=1,
        new_story_id=second["id"],
    )
    assert (moved["story_id"], moved["version"]) == (second["id"], 2)
    assert await rules_in(client, first) == []
    assert await rules_in(client, second) == [rule["id"]]

    events = await history(client, rule)
    assert [(event["action"], event["actor"]) for event in events] == [
        ("rule_created", "marie"),
        ("rule_moved", "ann"),
    ]
    move = events[1]
    assert move["before"] == {"story_id": first["id"], "version": 1}
    assert move["after"] == {"story_id": second["id"], "version": 2}
    assert (move["node_id"], move["other_node_id"]) == (second["id"], first["id"])

    for story in (first, second):
        story_events = (await ok(client, "node_history", node_id=story["id"]))["items"]
        assert move["id"] in [event["id"] for event in story_events]
        assert all(event["rule_id"] == rule["id"] for event in story_events[1:])


async def test_a_move_that_cannot_happen_changes_nothing_and_logs_nothing(client: Client) -> None:
    epic, story, task = await make_tree(client)
    (other,) = await make_stories(client, 1)
    rule = await make_rule(client, story_id=story["id"])
    loose = await make_rule(client, "Loose")
    move = {"actor": "ann", "rule_id": rule["id"], "expected_version": 1}

    assert "is an epic" in await refused(client, "move_rule", new_story_id=epic["id"], **move)
    assert "is a task" in await refused(client, "move_rule", new_story_id=task["id"], **move)
    assert "There is no node with id" in await refused(
        client, "move_rule", new_story_id=987654321, **move
    )
    assert "already in story" in await refused(
        client, "move_rule", new_story_id=story["id"], **move
    )
    assert "at version 1, but you sent version 5" in await refused(
        client, "move_rule", new_story_id=other["id"], **{**move, "expected_version": 5}
    )
    assert "actor must be the name" in await refused(
        client, "move_rule", new_story_id=other["id"], **{**move, "actor": " "}
    )
    message = await refused(
        client,
        "move_rule",
        actor="ann",
        rule_id=loose["id"],
        expected_version=1,
        new_story_id=other["id"],
    )
    assert "not in any story yet" in message and "place_rule" in message

    unchanged = await ok(client, "get_rule", rule_id=rule["id"])
    assert (unchanged["story_id"], unchanged["version"]) == (story["id"], 1)
    assert await rules_in(client, other) == []
    assert [event["action"] for event in await history(client, rule)] == ["rule_created"]
    assert [event["action"] for event in await history(client, loose)] == ["rule_created"]


async def race(client: Client, tool: str, calls: list[dict[str, Any]]) -> list[bool]:
    """Make all the calls at the same moment and say which of them were accepted."""
    accepted: list[bool] = []

    async def call(arguments: dict[str, Any]) -> None:
        accepted.append(not (await client.call_tool(tool, arguments)).is_error)

    async with anyio.create_task_group() as group:
        for arguments in calls:
            group.start_soon(call, arguments)
    return accepted


async def test_moves_made_together_have_one_winner_and_a_history_that_adds_up(
    client: Client,
) -> None:
    home, *targets = await make_stories(client, 7)
    # Several rounds, because a race between moves does not show every time.
    for _ in range(6):
        rule = await make_rule(client, story_id=home["id"])
        accepted = await race(
            client,
            "move_rule",
            [
                {
                    "actor": f"agent-{story['id']}",
                    "rule_id": rule["id"],
                    "expected_version": 1,
                    "new_story_id": story["id"],
                }
                for story in targets
            ],
        )
        assert accepted.count(True) == 1, accepted

        final = await ok(client, "get_rule", rule_id=rule["id"])
        assert final["version"] == 2
        holders = [
            story["id"] for story in [home, *targets] if rule["id"] in await rules_in(client, story)
        ]
        assert holders == [final["story_id"]]
        events = await history(client, rule)
        assert [event["action"] for event in events] == ["rule_created", "rule_moved"]
        assert events[1]["before"]["story_id"] == home["id"]
        assert events[1]["after"]["story_id"] == final["story_id"]


async def test_rules_placed_together_end_up_in_exactly_one_story(client: Client) -> None:
    stories = await make_stories(client, 6)
    for _ in range(6):
        rule = await make_rule(client)
        accepted = await race(
            client,
            "place_rule",
            [
                {
                    "actor": "agent",
                    "rule_id": rule["id"],
                    "expected_version": 1,
                    "story_id": story["id"],
                }
                for story in stories
            ],
        )
        assert accepted.count(True) == 1, accepted
        holders = [story["id"] for story in stories if rule["id"] in await rules_in(client, story)]
        assert holders == [(await ok(client, "get_rule", rule_id=rule["id"]))["story_id"]]


async def test_the_coverage_report_lists_unplaced_rules_and_stories_with_none(
    client: Client,
) -> None:
    covered, bare = await make_stories(client, 2)
    loose = await make_rule(client, "Not in any story")
    held = await make_rule(client, "In a story", story_id=covered["id"])

    report = await whole_coverage_report(client)
    unplaced_ids = [rule["id"] for rule in report["unplaced_rules"]]
    bare_ids = [story["id"] for story in report["stories_without_rules"]]
    assert loose["id"] in unplaced_ids and held["id"] not in unplaced_ids
    assert bare["id"] in bare_ids and covered["id"] not in bare_ids
    assert report["unplaced_rule_count"] == len(report["unplaced_rules"])
    assert report["story_without_rules_count"] == len(report["stories_without_rules"])
    assert all(story["kind"] == "story" for story in report["stories_without_rules"])

    # Placing the loose rule in the bare story closes both gaps at once.
    await ok(
        client,
        "place_rule",
        actor="ann",
        rule_id=loose["id"],
        expected_version=1,
        story_id=bare["id"],
    )
    after = await whole_coverage_report(client)
    assert loose["id"] not in [rule["id"] for rule in after["unplaced_rules"]]
    assert bare["id"] not in [story["id"] for story in after["stories_without_rules"]]
    assert after["unplaced_rule_count"] == report["unplaced_rule_count"] - 1
    assert after["story_without_rules_count"] == report["story_without_rules_count"] - 1


async def test_dropped_rules_and_dropped_stories_are_not_gaps(client: Client) -> None:
    only_dropped_rule, dropped_story = await make_stories(client, 2)
    dropped_rule = await make_rule(client, "No longer wanted", status="dropped")
    await make_rule(client, "Also gone", status="dropped", story_id=only_dropped_rule["id"])
    await ok(
        client,
        "update_node",
        actor="marie",
        node_id=dropped_story["id"],
        expected_version=1,
        status="dropped",
    )

    report = await whole_coverage_report(client)
    assert dropped_rule["id"] not in [rule["id"] for rule in report["unplaced_rules"]]
    bare_ids = [story["id"] for story in report["stories_without_rules"]]
    assert only_dropped_rule["id"] in bare_ids  # a dropped rule covers nothing
    assert dropped_story["id"] not in bare_ids  # a dropped story needs no rules


async def test_the_coverage_report_is_paged_and_each_list_can_be_read_alone(client: Client) -> None:
    await make_stories(client, 3)
    for number in range(3):
        await make_rule(client, f"Loose {number}")

    both = await ok(client, "coverage_report", limit=2)
    assert len(both["unplaced_rules"]) == 2 and both["next_unplaced_after_id"] is not None
    assert len(both["stories_without_rules"]) == 2 and both["next_stories_after_id"] is not None
    assert both["unplaced_rule_count"] >= 3 and both["story_without_rules_count"] >= 3

    only_rules = await ok(client, "coverage_report", show="unplaced_rules", limit=2)
    assert only_rules["stories_without_rules"] == []
    assert only_rules["story_without_rules_count"] == both["story_without_rules_count"]
    next_rules = await ok(
        client,
        "coverage_report",
        show="unplaced_rules",
        limit=2,
        unplaced_after_id=only_rules["next_unplaced_after_id"],
    )
    assert next_rules["unplaced_rules"][0]["id"] > only_rules["unplaced_rules"][-1]["id"]

    only_stories = await ok(client, "coverage_report", show="stories_without_rules", limit=2)
    assert only_stories["unplaced_rules"] == []
    assert "show must be one of both, unplaced_rules, stories_without_rules" in await refused(
        client, "coverage_report", show="everything"
    )
    assert "limit must be a whole number" in await refused(client, "coverage_report", limit=0)


async def test_rules_can_be_changed_with_the_right_version_and_are_refused_otherwise(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, "Draft wording", story_id=story["id"])

    changed = await ok(
        client,
        "update_rule",
        actor="ann",
        rule_id=rule["id"],
        expected_version=1,
        title="Final wording",
        status="settled",
    )
    assert (changed["title"], changed["status"], changed["version"]) == (
        "Final wording",
        "settled",
        2,
    )

    events = await history(client, rule)
    assert events[1]["action"] == "rule_updated"
    assert events[1]["before"] == {"title": "Draft wording", "status": "held", "version": 1}
    assert events[1]["after"] == {"title": "Final wording", "status": "settled", "version": 2}
    assert events[1]["node_id"] == story["id"]

    edit = {"actor": "bob", "rule_id": rule["id"]}
    assert f"Rule {rule['id']} is at version 2, but you sent version 1" in await refused(
        client, "update_rule", expected_version=1, title="Bob's wording", **edit
    )
    assert "title cannot be empty" in await refused(
        client, "update_rule", expected_version=2, title=" ", **edit
    )
    assert "status must be one of held, settled, dropped" in await refused(
        client, "update_rule", expected_version=2, status="ready", **edit
    )
    assert "Nothing to change" in await refused(
        client, "update_rule", expected_version=2, title="Final wording", **edit
    )
    assert (await ok(client, "get_rule", rule_id=rule["id"]))["title"] == "Final wording"


async def test_rules_can_be_listed_by_story_status_and_page(client: Client) -> None:
    first, second = await make_stories(client, 2)
    ids = [
        (await make_rule(client, f"Rule {number}", story_id=first["id"]))["id"]
        for number in range(3)
    ]
    other = await make_rule(client, "Elsewhere", story_id=second["id"], status="settled")
    loose = await make_rule(client, "Loose")

    page = await ok(client, "list_rules", story_id=first["id"], limit=2)
    rest = await ok(
        client, "list_rules", story_id=first["id"], limit=2, after_id=page["next_after_id"]
    )
    assert [r["id"] for r in page["items"] + rest["items"]] == ids
    assert rest["next_after_id"] is None

    assert [
        r["id"]
        for r in (await ok(client, "list_rules", story_id=second["id"], status="settled"))["items"]
    ] == [other["id"]]
    assert loose["id"] in [
        r["id"] for r in (await ok(client, "list_rules", unplaced=True, limit=200))["items"]
    ]
    assert "not both" in await refused(client, "list_rules", story_id=first["id"], unplaced=True)
    assert "status must be one of" in await refused(client, "list_rules", status="open")
    assert "belongs to a story" in await refused(client, "list_rules", story_id=first["parent_id"])
    assert "limit must be a whole number" in await refused(client, "list_rules", limit=201)
    assert "There is no rule with id 987654321" in await refused(
        client, "get_rule", rule_id=987654321
    )
    assert "There is no rule with id 987654321" in await refused(
        client, "rule_history", rule_id=987654321
    )
