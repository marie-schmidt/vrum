"""M3 against the running containers: rules, placement and coverage, through a real MCP client
(see ./check.sh)."""

from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    all_nodes,
    connect_as,
    connect_each,
    make_rule,
    make_stories,
    make_tree,
    ok,
    race,
    refused,
    unique_word,
    whole_coverage_report,
)
from tests.world import HARNESS_AGENT

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def history(client: Client, rule: dict[str, Any]) -> list[dict[str, Any]]:
    return list((await ok(client, "rule_history", rule_id=rule["id"]))["items"])


async def rules_in(client: Client, story: dict[str, Any]) -> list[int]:
    page = await ok(client, "list_rules", node=story["ref"], limit=200)
    return [rule["id"] for rule in page["items"]]


async def test_a_rule_can_be_recorded_unplaced_or_in_a_story_and_read_back(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    loose = await make_rule(client, "Loose rule", body="Why it exists")
    placed = await make_rule(client, "Placed rule", node=story["ref"], status="met")

    assert (loose["node"], loose["status"], loose["version"]) == (None, "open", 1)
    assert loose["body"] == "Why it exists"
    assert (placed["node"], placed["status"]) == (story["ref"], "met")
    assert await ok(client, "get_rule", rule_id=placed["id"]) == placed

    created = await history(client, placed)
    assert [(event["actor"]) for event in created] == [HARNESS_AGENT]
    assert [event["action"] for event in created] == ["rule_created"]
    assert created[0]["before"] is None
    assert created[0]["after"]["node"] == story["ref"]
    assert created[0]["rule_id"] == placed["id"] and created[0]["node"] == story["ref"]


async def test_a_rule_placed_in_a_second_story_leaves_the_first_so_it_is_never_in_two(
    client: Client,
) -> None:
    first, second = await make_stories(client, 2)
    rule = await make_rule(client)
    word = unique_word()
    ann, bob = f"ann-{word}", f"bob-{word}"

    async with connect_each(ann, bob) as (by_ann, by_bob):
        placed = await ok(
            by_ann, "place_rule", rule_id=rule["id"], expected_version=1, node=first["ref"]
        )
        assert (placed["node"], placed["version"]) == (first["ref"], 2)
        assert await rules_in(client, first) == [rule["id"]]

        # An acceptance criterion belongs to at most one story or task: placing it on another
        # moves it, as one step.
        moved = await ok(
            by_bob, "place_rule", rule_id=rule["id"], expected_version=2, node=second["ref"]
        )
        assert (moved["node"], moved["version"]) == (second["ref"], 3)
        assert await rules_in(client, first) == []
        assert await rules_in(client, second) == [rule["id"]]

        message = await refused(
            by_bob, "place_rule", rule_id=rule["id"], expected_version=3, node=second["ref"]
        )
        assert f"already on {second['ref']}, so nothing moved" in message

    unchanged = await ok(client, "get_rule", rule_id=rule["id"])
    assert (unchanged["node"], unchanged["version"]) == (second["ref"], 3)
    assert [(event["action"], event["actor"]) for event in await history(client, rule)] == [
        ("rule_created", HARNESS_AGENT),
        ("rule_placed", ann),
        ("rule_moved", bob),
    ]


async def test_a_rule_can_only_go_on_a_story_or_task_that_exists(client: Client) -> None:
    epic, story, task = await make_tree(client)
    rule = await make_rule(client)
    place = {"rule_id": rule["id"], "expected_version": 1}

    assert f"{epic['ref']} is an epic" in await refused(
        client, "place_rule", node=epic["ref"], **place
    )
    assert "There is no story 987654321 in this project" in await refused(
        client, "place_rule", node="story:987654321", **place
    )
    assert "There is no task 987654321 in this project" in await refused(
        client, "place_rule", node="task:987654321", **place
    )
    assert "There is no acceptance criterion with id 987654321" in await refused(
        client, "place_rule", rule_id=987654321, expected_version=1, node=story["ref"]
    )
    assert "belongs to a story or a task" in await refused(
        client, "create_rule", title="Misplaced", node=epic["ref"]
    )

    still = await ok(client, "get_rule", rule_id=rule["id"])
    assert (still["node"], still["version"]) == (None, 1)
    assert [event["action"] for event in await history(client, rule)] == ["rule_created"]


async def test_a_rule_can_be_on_a_task_and_moves_between_a_story_and_its_tasks(
    client: Client,
) -> None:
    _, story, task = await make_tree(client)
    rule = await make_rule(client, "Works on a phone", node=task["ref"])
    ann = f"ann-{unique_word()}"

    assert rule["node"] == task["ref"]
    assert await rules_in(client, task) == [rule["id"]]
    assert await rules_in(client, story) == []

    async with connect_as(ann) as by_ann:
        moved = await ok(
            by_ann, "place_rule", rule_id=rule["id"], expected_version=1, node=story["ref"]
        )
    assert (moved["node"], moved["version"]) == (story["ref"], 2)
    assert await rules_in(client, task) == []

    move = (await history(client, rule))[1]
    assert (move["action"], move["actor"]) == ("rule_moved", ann)
    assert move["before"] == {"node": task["ref"], "version": 1}
    assert move["after"] == {"node": story["ref"], "version": 2}
    assert (move["node"], move["other_node"]) == (story["ref"], task["ref"])
    # The move shows in the history of the task it left as well as the story it came to.
    for item in (story, task):
        events = (await ok(client, "node_history", node=item["ref"]))["items"]
        assert move["id"] in [event["id"] for event in events]


async def test_a_move_is_one_logged_step_that_shows_in_every_history_it_touches(
    client: Client,
) -> None:
    first, second = await make_stories(client, 2)
    rule = await make_rule(client, node=first["ref"])
    ann = f"ann-{unique_word()}"

    async with connect_as(ann) as by_ann:
        moved = await ok(
            by_ann, "place_rule", rule_id=rule["id"], expected_version=1, node=second["ref"]
        )
    assert (moved["node"], moved["version"]) == (second["ref"], 2)
    assert await rules_in(client, first) == []
    assert await rules_in(client, second) == [rule["id"]]

    events = await history(client, rule)
    assert [(event["action"], event["actor"]) for event in events] == [
        ("rule_created", HARNESS_AGENT),
        ("rule_moved", ann),
    ]
    move = events[1]
    assert move["before"] == {"node": first["ref"], "version": 1}
    assert move["after"] == {"node": second["ref"], "version": 2}
    assert (move["node"], move["other_node"]) == (second["ref"], first["ref"])

    for story in (first, second):
        story_events = (await ok(client, "node_history", node=story["ref"]))["items"]
        assert move["id"] in [event["id"] for event in story_events]
        assert all(event["rule_id"] == rule["id"] for event in story_events[1:])


async def test_a_placement_that_cannot_happen_changes_nothing_and_logs_nothing(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    (other,) = await make_stories(client, 1)
    rule = await make_rule(client, node=story["ref"])
    move = {"rule_id": rule["id"], "expected_version": 1}

    assert "is an epic" in await refused(client, "place_rule", node=epic["ref"], **move)
    assert "There is no task 987654321 in this project" in await refused(
        client, "place_rule", node="task:987654321", **move
    )
    assert "There is no story 987654321 in this project" in await refused(
        client, "place_rule", node="story:987654321", **move
    )
    assert f"already on {story['ref']}" in await refused(
        client, "place_rule", node=story["ref"], **move
    )
    stale = await refused(
        client, "place_rule", node=other["ref"], **{**move, "expected_version": 5}
    )
    assert "at version 1, but you sent version 5" in stale
    assert "list_rules" in stale and "get_rule" not in stale

    unchanged = await ok(client, "get_rule", rule_id=rule["id"])
    assert (unchanged["node"], unchanged["version"]) == (story["ref"], 1)
    assert await rules_in(client, other) == []
    assert await rules_in(client, task) == []
    assert [event["action"] for event in await history(client, rule)] == ["rule_created"]


async def test_moves_made_together_have_one_winner_and_a_history_that_adds_up(
    client: Client,
) -> None:
    home, *targets = await make_stories(client, 7)
    # Several rounds, because a race between moves does not show every time.
    for _ in range(6):
        rule = await make_rule(client, node=home["ref"])
        accepted = await race(
            client,
            "place_rule",
            [
                {"rule_id": rule["id"], "expected_version": 1, "node": story["ref"]}
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
        assert holders == [final["node"]]
        events = await history(client, rule)
        assert [event["action"] for event in events] == ["rule_created", "rule_moved"]
        assert events[1]["before"]["node"] == home["ref"]
        assert events[1]["after"]["node"] == final["node"]


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
                    "node": story["ref"],
                }
                for story in stories
            ],
        )
        assert accepted.count(True) == 1, accepted
        holders = [story["ref"] for story in stories if rule["id"] in await rules_in(client, story)]
        assert holders == [(await ok(client, "get_rule", rule_id=rule["id"]))["node"]]


async def test_the_coverage_report_lists_unplaced_rules_and_stories_with_none(
    client: Client,
) -> None:
    covered, bare = await make_stories(client, 2)
    loose = await make_rule(client, "Not in any story")
    in_a_story = await make_rule(client, "In a story", node=covered["ref"])

    report = await whole_coverage_report(client)
    unplaced_ids = [rule["id"] for rule in report["unplaced_rules"]]
    bare_refs = [story["ref"] for story in report["stories_without_rules"]]
    assert loose["id"] in unplaced_ids and in_a_story["id"] not in unplaced_ids
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
        node=bare["ref"],
    )
    after = await whole_coverage_report(client)
    assert loose["id"] not in [rule["id"] for rule in after["unplaced_rules"]]
    assert bare["ref"] not in [story["ref"] for story in after["stories_without_rules"]]
    assert after["unplaced_rule_count"] == report["unplaced_rule_count"] - 1
    assert after["story_without_rules_count"] == report["story_without_rules_count"] - 1


async def test_a_story_is_covered_by_a_criterion_on_one_of_its_tasks(client: Client) -> None:
    _, story, task = await make_tree(client)
    (bare,) = await make_stories(client, 1)

    before = await whole_coverage_report(client)
    assert story["ref"] in [item["ref"] for item in before["stories_without_rules"]]

    rule = await make_rule(client, "Works on a phone", node=task["ref"])
    after = await whole_coverage_report(client)
    assert story["ref"] not in [item["ref"] for item in after["stories_without_rules"]]
    assert bare["ref"] in [item["ref"] for item in after["stories_without_rules"]]
    assert after["story_without_rules_count"] == before["story_without_rules_count"] - 1
    # A criterion on a task is placed, so it is not among those placed nowhere.
    assert rule["id"] not in [item["id"] for item in after["unplaced_rules"]]

    # Dropping it takes the cover away again: a dropped criterion covers nothing.
    await ok(
        client,
        "update_rule",
        actor="ann",
        rule_id=rule["id"],
        expected_version=1,
        status="dropped",
    )
    dropped = await whole_coverage_report(client)
    assert story["ref"] in [item["ref"] for item in dropped["stories_without_rules"]]


async def test_a_story_and_a_task_count_their_own_criteria_and_no_criteria_finds_the_bare_ones(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    bare_task = await ok(
        client, "create_node", actor="marie", kind="task", title="Bare task", parent=story["ref"]
    )

    async def counts(ref: str) -> tuple[int, int]:
        node = await ok(client, "get_node", node=ref)
        return node["criteria_total"], node["criteria_met"]

    assert [await counts(ref) for ref in (epic["ref"], story["ref"], task["ref"])] == [
        (0, 0),
        (0, 0),
        (0, 0),
    ]
    await make_rule(client, "Story one", node=story["ref"], status="met")
    await make_rule(client, "Story two", node=story["ref"])
    await make_rule(client, "Story gone", node=story["ref"], status="dropped")
    await make_rule(client, "Task one", node=task["ref"], status="met")
    # Only what is placed directly counts, and a dropped criterion counts for nothing.
    assert await counts(story["ref"]) == (2, 1)
    assert await counts(task["ref"]) == (1, 1)
    assert await counts(bare_task["ref"]) == (0, 0)
    assert await counts(epic["ref"]) == (0, 0)

    listed = {
        node["ref"]: (node["criteria_total"], node["criteria_met"])
        for node in await all_nodes(client, query=f"{epic['ref']}")
    }
    assert listed == {
        epic["ref"]: (0, 0),
        story["ref"]: (2, 1),
        task["ref"]: (1, 1),
        bare_task["ref"]: (0, 0),
    }
    # An epic can never hold one, so it is never one of those with none.
    bare = await all_nodes(client, query=f"{epic['ref']} no:criteria")
    assert [node["ref"] for node in bare] == [bare_task["ref"]]
    await ok(
        client,
        "update_rule",
        actor="marie",
        rule_id=(await ok(client, "list_rules", node=task["ref"]))["items"][0]["id"],
        expected_version=1,
        status="dropped",
    )
    bare = await all_nodes(client, query=f"{epic['ref']} no:criteria")
    assert {node["ref"] for node in bare} == {task["ref"], bare_task["ref"]}


async def test_dropped_rules_and_dropped_stories_are_not_gaps(client: Client) -> None:
    only_dropped_rule, dropped_story = await make_stories(client, 2)
    dropped_rule = await make_rule(client, "No longer wanted", status="dropped")
    await make_rule(client, "Also gone", status="dropped", node=only_dropped_rule["ref"])
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
    rule = await make_rule(client, "Draft wording", node=story["ref"])

    changed = await ok(
        client,
        "update_rule",
        actor="ann",
        rule_id=rule["id"],
        expected_version=1,
        title="Final wording",
        status="met",
    )
    assert (changed["title"], changed["status"], changed["version"]) == (
        "Final wording",
        "met",
        2,
    )

    events = await history(client, rule)
    assert events[1]["action"] == "rule_updated"
    assert events[1]["before"] == {"title": "Draft wording", "status": "open", "version": 1}
    assert events[1]["after"] == {"title": "Final wording", "status": "met", "version": 2}
    assert events[1]["node"] == story["ref"]

    edit = {"actor": "bob", "rule_id": rule["id"]}
    assert (
        f"Acceptance criterion {rule['id']} is at version 2, but you sent version 1"
        in await refused(client, "update_rule", expected_version=1, title="Bob's wording", **edit)
    )
    assert "title cannot be empty" in await refused(
        client, "update_rule", expected_version=2, title=" ", **edit
    )
    assert "status must be one of open, met, dropped" in await refused(
        client, "update_rule", expected_version=2, status="ready", **edit
    )
    # The words of before are no longer statuses.
    for old_word in ("held", "settled"):
        assert "status must be one of open, met, dropped" in await refused(
            client, "update_rule", expected_version=2, status=old_word, **edit
        )
    assert "Nothing to change" in await refused(
        client, "update_rule", expected_version=2, title="Final wording", **edit
    )
    assert (await ok(client, "get_rule", rule_id=rule["id"]))["title"] == "Final wording"


async def test_rules_can_be_listed_by_story_status_and_page(client: Client) -> None:
    first, second = await make_stories(client, 2)
    ids = [
        (await make_rule(client, f"Rule {number}", node=first["ref"]))["id"] for number in range(3)
    ]
    other = await make_rule(client, "Elsewhere", node=second["ref"], status="met")
    loose = await make_rule(client, "Loose")

    page = await ok(client, "list_rules", node=first["ref"], limit=2)
    rest = await ok(
        client, "list_rules", node=first["ref"], limit=2, after_id=page["next_after_id"]
    )
    assert [r["id"] for r in page["items"] + rest["items"]] == ids
    assert rest["next_after_id"] is None

    assert [
        r["id"] for r in (await ok(client, "list_rules", node=second["ref"], status="met"))["items"]
    ] == [other["id"]]
    assert loose["id"] in [
        r["id"] for r in (await ok(client, "list_rules", unplaced=True, limit=200))["items"]
    ]
    assert "not both" in await refused(client, "list_rules", node=first["ref"], unplaced=True)
    assert "status must be one of" in await refused(client, "list_rules", status="settled")
    assert "belongs to a story or a task" in await refused(
        client, "list_rules", node=first["parent"]
    )
    assert "limit must be a whole number" in await refused(client, "list_rules", limit=201)
    assert "There is no acceptance criterion with id 987654321" in await refused(
        client, "get_rule", rule_id=987654321
    )
    assert "There is no acceptance criterion with id 987654321" in await refused(
        client, "rule_history", rule_id=987654321
    )
