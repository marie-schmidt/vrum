"""M3 against the running containers: rules, placement and coverage, through a real MCP client
(see ./check.sh)."""

from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    make_rule,
    make_stories,
    make_tree,
    ok,
    race,
    refused,
    whole_coverage_report,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def history(client: Client, rule: dict[str, Any]) -> list[dict[str, Any]]:
    return list((await ok(client, "rule_history", rule_id=rule["id"]))["items"])


async def rules_in(client: Client, story: dict[str, Any]) -> list[int]:
    page = await ok(client, "list_rules", story=story["ref"], limit=200)
    return [rule["id"] for rule in page["items"]]


async def test_a_rule_can_be_recorded_unplaced_or_in_a_story_and_read_back(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    loose = await make_rule(client, "Loose rule", body="Why it exists")
    placed = await make_rule(client, "Placed rule", story=story["ref"], status="settled")

    assert (loose["story"], loose["status"], loose["version"]) == (None, "held", 1)
    assert loose["body"] == "Why it exists"
    assert (placed["story"], placed["status"]) == (story["ref"], "settled")
    assert await ok(client, "get_rule", rule_id=placed["id"]) == placed

    created = await history(client, placed)
    assert [(event["action"], event["actor"]) for event in created] == [("rule_created", "marie")]
    assert created[0]["before"] is None
    assert created[0]["after"]["story"] == story["ref"]
    assert created[0]["rule_id"] == placed["id"] and created[0]["node"] == story["ref"]


async def test_a_rule_cannot_be_placed_in_two_stories(client: Client) -> None:
    first, second = await make_stories(client, 2)
    rule = await make_rule(client)

    placed = await ok(
        client,
        "place_rule",
        actor="ann",
        rule_id=rule["id"],
        expected_version=1,
        story=first["ref"],
    )
    assert (placed["story"], placed["version"]) == (first["ref"], 2)

    message = await refused(
        client,
        "place_rule",
        actor="bob",
        rule_id=rule["id"],
        expected_version=2,
        story=second["ref"],
    )
    assert f"already placed in {first['ref']}" in message
    assert "at most one story" in message and "move_rule" in message

    assert await rules_in(client, first) == [rule["id"]]
    assert await rules_in(client, second) == []
    unchanged = await ok(client, "get_rule", rule_id=rule["id"])
    assert (unchanged["story"], unchanged["version"]) == (first["ref"], 2)
    assert [event["action"] for event in await history(client, rule)] == [
        "rule_created",
        "rule_placed",
    ]


async def test_a_rule_can_only_go_in_a_story_that_exists(client: Client) -> None:
    epic, story, task = await make_tree(client)
    rule = await make_rule(client)
    place = {"actor": "ann", "rule_id": rule["id"], "expected_version": 1}

    assert f"{epic['ref']} is an epic" in await refused(
        client, "place_rule", story=epic["ref"], **place
    )
    assert f"{task['ref']} is a task" in await refused(
        client, "place_rule", story=task["ref"], **place
    )
    assert "There is no story 987654321 in this project" in await refused(
        client, "place_rule", story="story:987654321", **place
    )
    assert "There is no rule with id 987654321" in await refused(
        client,
        "place_rule",
        actor="ann",
        rule_id=987654321,
        expected_version=1,
        story=story["ref"],
    )
    assert "belongs to a story" in await refused(
        client, "create_rule", actor="ann", title="Misplaced", story=epic["ref"]
    )

    still = await ok(client, "get_rule", rule_id=rule["id"])
    assert (still["story"], still["version"]) == (None, 1)
    assert [event["action"] for event in await history(client, rule)] == ["rule_created"]


async def test_a_move_is_one_logged_step_that_shows_in_every_history_it_touches(
    client: Client,
) -> None:
    first, second = await make_stories(client, 2)
    rule = await make_rule(client, story=first["ref"])

    moved = await ok(
        client,
        "move_rule",
        actor="ann",
        rule_id=rule["id"],
        expected_version=1,
        new_story=second["ref"],
    )
    assert (moved["story"], moved["version"]) == (second["ref"], 2)
    assert await rules_in(client, first) == []
    assert await rules_in(client, second) == [rule["id"]]

    events = await history(client, rule)
    assert [(event["action"], event["actor"]) for event in events] == [
        ("rule_created", "marie"),
        ("rule_moved", "ann"),
    ]
    move = events[1]
    assert move["before"] == {"story": first["ref"], "version": 1}
    assert move["after"] == {"story": second["ref"], "version": 2}
    assert (move["node"], move["other_node"]) == (second["ref"], first["ref"])

    for story in (first, second):
        story_events = (await ok(client, "node_history", node=story["ref"]))["items"]
        assert move["id"] in [event["id"] for event in story_events]
        assert all(event["rule_id"] == rule["id"] for event in story_events[1:])


async def test_a_move_that_cannot_happen_changes_nothing_and_logs_nothing(client: Client) -> None:
    epic, story, task = await make_tree(client)
    (other,) = await make_stories(client, 1)
    rule = await make_rule(client, story=story["ref"])
    loose = await make_rule(client, "Loose")
    move = {"actor": "ann", "rule_id": rule["id"], "expected_version": 1}

    assert "is an epic" in await refused(client, "move_rule", new_story=epic["ref"], **move)
    assert "is a task" in await refused(client, "move_rule", new_story=task["ref"], **move)
    assert "There is no story 987654321 in this project" in await refused(
        client, "move_rule", new_story="story:987654321", **move
    )
    assert f"already in {story['ref']}" in await refused(
        client, "move_rule", new_story=story["ref"], **move
    )
    assert "at version 1, but you sent version 5" in await refused(
        client, "move_rule", new_story=other["ref"], **{**move, "expected_version": 5}
    )
    assert "actor must be the name" in await refused(
        client, "move_rule", new_story=other["ref"], **{**move, "actor": " "}
    )
    message = await refused(
        client,
        "move_rule",
        actor="ann",
        rule_id=loose["id"],
        expected_version=1,
        new_story=other["ref"],
    )
    assert "not in any story yet" in message and "place_rule" in message

    unchanged = await ok(client, "get_rule", rule_id=rule["id"])
    assert (unchanged["story"], unchanged["version"]) == (story["ref"], 1)
    assert await rules_in(client, other) == []
    assert [event["action"] for event in await history(client, rule)] == ["rule_created"]
    assert [event["action"] for event in await history(client, loose)] == ["rule_created"]


async def test_moves_made_together_have_one_winner_and_a_history_that_adds_up(
    client: Client,
) -> None:
    home, *targets = await make_stories(client, 7)
    # Several rounds, because a race between moves does not show every time.
    for _ in range(6):
        rule = await make_rule(client, story=home["ref"])
        accepted = await race(
            client,
            "move_rule",
            [
                {
                    "actor": f"agent-{story['number']}",
                    "rule_id": rule["id"],
                    "expected_version": 1,
                    "new_story": story["ref"],
                }
                for story in targets
            ],
        )
        assert accepted.count(True) == 1, accepted

        final = await ok(client, "get_rule", rule_id=rule["id"])
        assert final["version"] == 2
        holders = [
            story["ref"]
            for story in [home, *targets]
            if rule["id"] in await rules_in(client, story)
        ]
        assert holders == [final["story"]]
        events = await history(client, rule)
        assert [event["action"] for event in events] == ["rule_created", "rule_moved"]
        assert events[1]["before"]["story"] == home["ref"]
        assert events[1]["after"]["story"] == final["story"]


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
                    "story": story["ref"],
                }
                for story in stories
            ],
        )
        assert accepted.count(True) == 1, accepted
        holders = [story["ref"] for story in stories if rule["id"] in await rules_in(client, story)]
        assert holders == [(await ok(client, "get_rule", rule_id=rule["id"]))["story"]]


async def test_the_coverage_report_lists_unplaced_rules_and_stories_with_none(
    client: Client,
) -> None:
    covered, bare = await make_stories(client, 2)
    loose = await make_rule(client, "Not in any story")
    held = await make_rule(client, "In a story", story=covered["ref"])

    report = await whole_coverage_report(client)
    unplaced_ids = [rule["id"] for rule in report["unplaced_rules"]]
    bare_refs = [story["ref"] for story in report["stories_without_rules"]]
    assert loose["id"] in unplaced_ids and held["id"] not in unplaced_ids
    assert bare["ref"] in bare_refs and covered["ref"] not in bare_refs
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
        story=bare["ref"],
    )
    after = await whole_coverage_report(client)
    assert loose["id"] not in [rule["id"] for rule in after["unplaced_rules"]]
    assert bare["ref"] not in [story["ref"] for story in after["stories_without_rules"]]
    assert after["unplaced_rule_count"] == report["unplaced_rule_count"] - 1
    assert after["story_without_rules_count"] == report["story_without_rules_count"] - 1


async def test_dropped_rules_and_dropped_stories_are_not_gaps(client: Client) -> None:
    only_dropped_rule, dropped_story = await make_stories(client, 2)
    dropped_rule = await make_rule(client, "No longer wanted", status="dropped")
    await make_rule(client, "Also gone", status="dropped", story=only_dropped_rule["ref"])
    await ok(
        client,
        "update_node",
        actor="marie",
        node=dropped_story["ref"],
        expected_version=1,
        status="dropped",
    )

    report = await whole_coverage_report(client)
    assert dropped_rule["id"] not in [rule["id"] for rule in report["unplaced_rules"]]
    bare_refs = [story["ref"] for story in report["stories_without_rules"]]
    assert only_dropped_rule["ref"] in bare_refs  # a dropped rule covers nothing
    assert dropped_story["ref"] not in bare_refs  # a dropped story needs no rules


async def test_the_coverage_report_is_paged_and_each_list_can_be_read_alone(client: Client) -> None:
    await make_stories(client, 3)
    for number in range(3):
        await make_rule(client, f"Loose {number}")

    both = await ok(client, "coverage_report", limit=2)
    assert len(both["unplaced_rules"]) == 2 and both["next_unplaced_after_id"] is not None
    assert len(both["stories_without_rules"]) == 2 and both["next_stories_after"] is not None
    assert both["next_stories_after"] == both["stories_without_rules"][-1]["ref"]
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
    next_stories = await ok(
        client,
        "coverage_report",
        show="stories_without_rules",
        limit=2,
        stories_after=only_stories["next_stories_after"],
    )
    assert next_stories["stories_without_rules"]
    assert not {s["ref"] for s in next_stories["stories_without_rules"]} & {
        s["ref"] for s in only_stories["stories_without_rules"]
    }
    assert "show must be one of both, unplaced_rules, stories_without_rules" in await refused(
        client, "coverage_report", show="everything"
    )
    assert "limit must be a whole number" in await refused(client, "coverage_report", limit=0)


async def test_rules_can_be_changed_with_the_right_version_and_are_refused_otherwise(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, "Draft wording", story=story["ref"])

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
    assert events[1]["node"] == story["ref"]

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
        (await make_rule(client, f"Rule {number}", story=first["ref"]))["id"] for number in range(3)
    ]
    other = await make_rule(client, "Elsewhere", story=second["ref"], status="settled")
    loose = await make_rule(client, "Loose")

    page = await ok(client, "list_rules", story=first["ref"], limit=2)
    rest = await ok(
        client, "list_rules", story=first["ref"], limit=2, after_id=page["next_after_id"]
    )
    assert [r["id"] for r in page["items"] + rest["items"]] == ids
    assert rest["next_after_id"] is None

    assert [
        r["id"]
        for r in (await ok(client, "list_rules", story=second["ref"], status="settled"))["items"]
    ] == [other["id"]]
    assert loose["id"] in [
        r["id"] for r in (await ok(client, "list_rules", unplaced=True, limit=200))["items"]
    ]
    assert "not both" in await refused(client, "list_rules", story=first["ref"], unplaced=True)
    assert "status must be one of" in await refused(client, "list_rules", status="open")
    assert "belongs to a story" in await refused(client, "list_rules", story=first["parent"])
    assert "limit must be a whole number" in await refused(client, "list_rules", limit=201)
    assert "There is no rule with id 987654321" in await refused(
        client, "get_rule", rule_id=987654321
    )
    assert "There is no rule with id 987654321" in await refused(
        client, "rule_history", rule_id=987654321
    )
