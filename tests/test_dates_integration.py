"""M1A against the running containers: dates on nodes, through a real MCP client (./check.sh)."""

from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    all_nodes,
    day,
    make_stories,
    make_tree,
    needs_first,
    node_core,
    ok,
    refused,
    set_status,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def get(client: Client, node: dict[str, Any]) -> dict[str, Any]:
    return await ok(client, "get_node", node=node["ref"])


async def dated_task(
    client: Client, story: dict[str, Any], title: str, **dates: Any
) -> dict[str, Any]:
    return await ok(
        client,
        "create_node",
        actor="marie",
        kind="task",
        title=title,
        parent=story["ref"],
        **dates,
    )


async def test_a_node_starts_with_no_dates_and_reports_nothing_about_them(client: Client) -> None:
    epic, story, task = await make_tree(client)
    for node in (epic, story, task):
        assert (node["start_on"], node["target_on"]) == (None, None)
        assert (node["span_start"], node["span_end"], node["span_worked_out"]) == (
            None,
            None,
            False,
        )
        assert (node["overdue"], node["schedule_conflicts"]) == (False, [])


async def test_dates_are_set_changed_cleared_and_logged_with_before_and_after(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    task = await dated_task(client, story, "Dated", start_on=day(2), target_on=day(10))
    assert (task["start_on"], task["target_on"]) == (day(2), day(10))
    assert node_core(await get(client, task)) == node_core(task)

    moved = await ok(
        client,
        "update_node",
        actor="ann",
        node=task["ref"],
        expected_version=1,
        target_on=day(20),
    )
    assert (moved["start_on"], moved["target_on"], moved["version"]) == (day(2), day(20), 2)

    cleared = await ok(
        client,
        "update_node",
        actor="bob",
        node=task["ref"],
        expected_version=2,
        clear_start_on=True,
    )
    assert (cleared["start_on"], cleared["target_on"], cleared["version"]) == (None, day(20), 3)

    history = (await ok(client, "node_history", node=task["ref"]))["items"]
    assert [(e["action"], e["actor"]) for e in history] == [
        ("create", "marie"),
        ("update", "ann"),
        ("update", "bob"),
    ]
    assert history[0]["after"]["start_on"] == day(2) and history[0]["after"]["target_on"] == day(10)
    assert history[1]["before"] == {"target_on": day(10), "version": 1}
    assert history[1]["after"] == {"target_on": day(20), "version": 2}
    assert history[2]["before"] == {"start_on": day(2), "version": 2}
    assert history[2]["after"] == {"start_on": None, "version": 3}


async def test_a_start_date_after_the_target_date_is_refused_and_changes_nothing(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    before = await all_nodes(client, parent=story["ref"])

    message = await refused(
        client,
        "create_node",
        actor="marie",
        kind="task",
        title="Backwards",
        parent=story["ref"],
        start_on=day(9),
        target_on=day(3),
    )
    assert f"The start date {day(9)} is after the target date {day(3)}" in message
    assert "move the start earlier or the target later" in message
    assert await all_nodes(client, parent=story["ref"]) == before

    task = await dated_task(client, story, "Fine", start_on=day(2), target_on=day(10))
    edit = {"actor": "ann", "node": task["ref"], "expected_version": 1}
    # Moving just one date past the other is refused too, using the date the node already has.
    assert "is after the target date" in await refused(
        client, "update_node", start_on=day(30), **edit
    )
    assert "is after the target date" in await refused(
        client, "update_node", target_on=day(1), **edit
    )
    assert "is after the target date" in await refused(
        client, "update_node", start_on=day(12), target_on=day(11), **edit
    )

    unchanged = await get(client, task)
    assert (unchanged["start_on"], unchanged["target_on"], unchanged["version"]) == (
        day(2),
        day(10),
        1,
    )
    assert len((await ok(client, "node_history", node=task["ref"]))["items"]) == 1

    # The same day is allowed: a one-day job starts and ends on one day.
    same = await ok(
        client,
        "update_node",
        actor="ann",
        node=task["ref"],
        expected_version=1,
        start_on=day(5),
        target_on=day(5),
    )
    assert (same["start_on"], same["target_on"]) == (day(5), day(5))


async def test_a_date_can_only_be_removed_by_asking_for_it(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    task = await dated_task(client, story, "Dated", start_on=day(2), target_on=day(10))
    edit = {"actor": "ann", "node": task["ref"], "expected_version": 1}

    assert "not both" in await refused(
        client, "update_node", start_on=day(3), clear_start_on=True, **edit
    )
    assert "Nothing to change" in await refused(client, "update_node", title="Dated", **edit)
    cleared = await ok(client, "update_node", clear_start_on=True, clear_target_on=True, **edit)
    assert (cleared["start_on"], cleared["target_on"]) == (None, None)
    # Clearing what is already clear is a change that changes nothing.
    assert "Nothing to change" in await refused(
        client,
        "update_node",
        actor="ann",
        node=task["ref"],
        expected_version=2,
        clear_target_on=True,
    )


async def test_a_stale_version_is_still_refused_for_a_date_change(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    task = await dated_task(client, story, "Contested", start_on=day(2), target_on=day(10))
    await ok(
        client,
        "update_node",
        actor="ann",
        node=task["ref"],
        expected_version=1,
        target_on=day(12),
    )

    message = await refused(
        client,
        "update_node",
        actor="bob",
        node=task["ref"],
        expected_version=1,
        target_on=day(15),
    )
    assert f"{task['ref']} is at version 2, but you sent version 1" in message
    assert (await get(client, task))["target_on"] == day(12)


@pytest.mark.parametrize(
    "written",
    ["next tuesday", "2026-02-30", "2026-11-30T10:00:00", "30/11/2026", "20261130", "", " "],
)
async def test_a_date_written_loosely_is_refused_in_plain_words(
    client: Client, written: str
) -> None:
    (story,) = await make_stories(client, 1)
    task = await dated_task(client, story, "Strict")
    expected = "must be a day written as YYYY-MM-DD, such as 2026-11-30 (no time of day)"

    message = await refused(
        client,
        "create_node",
        actor="a",
        kind="task",
        title="x",
        parent=story["ref"],
        start_on=written,
    )
    assert f"start_on {expected}" in message and repr(written) in message
    assert f"target_on {expected}" in await refused(
        client, "update_node", actor="a", node=task["ref"], expected_version=1, target_on=written
    )
    assert f"target_from {expected}" in await refused(client, "list_nodes", target_from=written)
    assert "ValidationError" not in message and "pydantic" not in message
    assert (await get(client, task))["version"] == 1


async def test_a_node_is_overdue_when_its_target_has_passed_until_it_is_done_or_dropped(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    late = await dated_task(client, story, "Late", target_on=day(-3))
    today = await dated_task(client, story, "Due today", target_on=day(0))
    future = await dated_task(client, story, "Later", target_on=day(3))
    undated = await dated_task(client, story, "No date")
    finished = await dated_task(client, story, "Finished late", target_on=day(-5))
    dropped = await dated_task(client, story, "Dropped late", target_on=day(-5))

    assert [(await get(client, n))["overdue"] for n in (late, today, future, undated)] == [
        True,
        False,
        False,
        False,
    ]
    assert (await set_status(client, finished, "done"))["overdue"] is False
    assert (await set_status(client, dropped, "dropped"))["overdue"] is False
    assert (await set_status(client, late, "in_progress"))["overdue"] is True

    listed = await all_nodes(client, parent=story["ref"], overdue=True)
    assert [n["ref"] for n in listed] == [late["ref"]]
    not_overdue = await all_nodes(client, parent=story["ref"], overdue=False)
    assert [n["ref"] for n in not_overdue] == [
        today["ref"],
        future["ref"],
        undated["ref"],
        finished["ref"],
        dropped["ref"],
    ]


async def test_a_story_and_an_epic_without_dates_report_the_span_of_what_is_under_them(
    client: Client,
) -> None:
    epic = await ok(client, "create_node", actor="marie", kind="epic", title="An epic")
    dated_story = await ok(
        client,
        "create_node",
        actor="marie",
        kind="story",
        title="Dated story",
        parent=epic["ref"],
        start_on=day(40),
        target_on=day(60),
    )
    plain_story = await ok(
        client,
        "create_node",
        actor="marie",
        kind="story",
        title="Plain story",
        parent=epic["ref"],
    )
    await dated_task(client, plain_story, "Early", start_on=day(5), target_on=day(8))
    await dated_task(client, plain_story, "Late", start_on=day(7), target_on=day(30))
    await dated_task(client, plain_story, "Undated")

    story = await get(client, plain_story)
    assert (story["start_on"], story["target_on"]) == (None, None)
    assert (story["span_start"], story["span_end"], story["span_worked_out"]) == (
        day(5),
        day(30),
        True,
    )

    # An epic takes a story's own dates when it has them, and the story's span when it does not.
    whole = await get(client, epic)
    assert (whole["start_on"], whole["target_on"]) == (None, None)
    assert (whole["span_start"], whole["span_end"], whole["span_worked_out"]) == (
        day(5),
        day(60),
        True,
    )

    # A node's own dates are what it reports, even when what is under it reaches further.
    own = await ok(
        client,
        "update_node",
        actor="ann",
        node=plain_story["ref"],
        expected_version=story["version"],
        start_on=day(1),
        target_on=day(2),
    )
    assert (own["span_start"], own["span_end"], own["span_worked_out"]) == (day(1), day(2), False)
    assert (await get(client, epic))["span_start"] == day(1)

    # A task never works a span out, and a dropped child does not count.
    assert (await get(client, dated_story))["span_worked_out"] is False
    lone = await ok(
        client, "create_node", actor="marie", kind="task", title="Lone", parent=dated_story["ref"]
    )
    assert (lone["span_start"], lone["span_end"], lone["span_worked_out"]) == (None, None, False)
    spare = await ok(
        client, "create_node", actor="marie", kind="story", title="Spare", parent=epic["ref"]
    )
    far = await dated_task(client, spare, "Far away", start_on=day(300), target_on=day(400))
    assert (await get(client, epic))["span_end"] == day(400)
    await set_status(client, far, "dropped")
    assert (await get(client, epic))["span_end"] == day(60)

    # With nothing dated underneath, there is no span to report.
    empty = await ok(client, "create_node", actor="marie", kind="epic", title="Empty")
    await ok(
        client, "create_node", actor="marie", kind="story", title="Nothing", parent=empty["ref"]
    )
    assert (await get(client, empty))["span_worked_out"] is False


async def test_a_node_that_starts_before_what_it_needs_is_due_shows_a_schedule_conflict(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    first = await dated_task(client, story, "First", start_on=day(1), target_on=day(20))
    second = await dated_task(client, story, "Second", start_on=day(10), target_on=day(30))
    third = await dated_task(client, story, "Third", start_on=day(25), target_on=day(35))
    await needs_first(client, second, first)
    await needs_first(client, third, first)

    # Warning only: both links were accepted, and both nodes still wait for the first.
    clash = await get(client, second)
    assert (clash["schedule_conflicts"], clash["waiting_on"], clash["status"]) == (
        [first["ref"]],
        [first["ref"]],
        "new",
    )
    assert (await get(client, third))["schedule_conflicts"] == []
    assert (await get(client, first))["schedule_conflicts"] == []

    # It goes away when the dependent starts later...
    later = await ok(
        client,
        "update_node",
        actor="ann",
        node=second["ref"],
        expected_version=clash["version"],
        start_on=day(20),
    )
    assert later["schedule_conflicts"] == []
    # ...or when what it needs is due sooner...
    await ok(
        client,
        "update_node",
        actor="ann",
        node=second["ref"],
        expected_version=later["version"],
        start_on=day(12),
    )
    assert (await get(client, second))["schedule_conflicts"] == [first["ref"]]
    await ok(
        client,
        "update_node",
        actor="ann",
        node=first["ref"],
        expected_version=1,
        target_on=day(12),
    )
    assert (await get(client, second))["schedule_conflicts"] == []
    # ...or when the dependency is gone, or what it needs is done.
    await ok(
        client,
        "update_node",
        actor="ann",
        node=first["ref"],
        expected_version=2,
        target_on=day(40),
    )
    assert (await get(client, second))["schedule_conflicts"] == [first["ref"]]
    assert (await get(client, third))["schedule_conflicts"] == [first["ref"]]
    await set_status(client, first, "done")
    assert (await get(client, second))["schedule_conflicts"] == []
    assert (await get(client, third))["schedule_conflicts"] == []


async def test_a_node_with_no_start_date_never_shows_a_schedule_conflict(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    first = await dated_task(client, story, "First", start_on=day(1), target_on=day(20))
    undated = await dated_task(client, story, "Undated")
    only_target = await dated_task(client, story, "Only a target", target_on=day(5))
    await needs_first(client, undated, first)
    await needs_first(client, only_target, first)
    assert (await get(client, undated))["schedule_conflicts"] == []
    assert (await get(client, only_target))["schedule_conflicts"] == []


async def test_nodes_can_be_listed_by_their_own_dates(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    early = await dated_task(client, story, "Early", start_on=day(1), target_on=day(5))
    middle = await dated_task(client, story, "Middle", start_on=day(10), target_on=day(15))
    late = await dated_task(client, story, "Late", start_on=day(20), target_on=day(25))
    await dated_task(client, story, "Undated")
    parent = {"parent": story["ref"]}

    async def refs(**filters: Any) -> list[str]:
        return [n["ref"] for n in await all_nodes(client, **parent, **filters)]

    assert await refs(start_from=day(10)) == [middle["ref"], late["ref"]]
    assert await refs(start_to=day(10)) == [early["ref"], middle["ref"]]
    assert await refs(target_from=day(15), target_to=day(25)) == [middle["ref"], late["ref"]]
    assert await refs(start_from=day(2), target_to=day(20)) == [middle["ref"]]
    assert await refs(start_from=day(5), start_to=day(9)) == []
    assert len(await refs()) == 4
    assert "start_from must be a day" in await refused(client, "list_nodes", start_from="soon")


async def test_dates_do_not_get_in_the_way_of_readiness_claims_or_moves(client: Client) -> None:
    _, story, task = await make_tree(client)
    (other_story,) = await make_stories(client, 1)
    dated = await ok(
        client,
        "update_node",
        actor="ann",
        node=task["ref"],
        expected_version=1,
        start_on=day(100),
        target_on=day(200),
    )
    assert dated["status"] == "ready"
    assert (await ok(client, "claim_node", actor="ann", node=task["ref"]))["actor"] == "ann"
    moved = await ok(
        client,
        "move_node",
        actor="ann",
        node=task["ref"],
        expected_version=2,
        new_parent=other_story["ref"],
    )
    assert (moved["start_on"], moved["target_on"]) == (day(100), day(200))
    # What the old story reports follows the task out, and the new story takes it in.
    assert (await get(client, other_story))["span_start"] == day(100)
    assert (await get(client, story))["span_start"] is None
