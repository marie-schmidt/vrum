"""M5B against the running containers: the agent workflow (next_work, finish_work, create_plan,
update_nodes, changes_since), compact lists, the check for similar items on create, and the server
instructions, through a real MCP client (see ./check.sh)."""

import json
import re
import threading
import time
from typing import Any

import anyio
import pytest
from mcp import Client

from tests.helpers import (
    all_nodes,
    day,
    embeddings_stopped,
    make_belief,
    make_decision,
    make_fact,
    make_rule,
    needs_first,
    ok,
    race_results,
    refused,
    reindexed,
    run_sql,
    set_status,
    unique_word,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def project(
    client: Client, tasks: int, **story_fields: Any
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """An epic, a story in it and `tasks` tasks in that, all new, made for one test."""
    epic = await ok(client, "create_node", actor="marie", kind="epic", title="An epic")
    story = await ok(
        client,
        "create_node",
        actor="marie",
        kind="story",
        title="A story",
        parent_id=epic["id"],
        **story_fields,
    )
    made = [
        await ok(
            client,
            "create_node",
            actor="marie",
            kind="task",
            title=f"Task {number}",
            parent_id=story["id"],
        )
        for number in range(tasks)
    ]
    return epic, story, made


async def take(client: Client, actor: str, **arguments: Any) -> dict[str, Any]:
    return await ok(client, "next_work", actor=actor, **arguments)


async def get(client: Client, node: dict[str, Any]) -> dict[str, Any]:
    return await ok(client, "get_node", node_id=node["id"])


async def history(client: Client, node: dict[str, Any]) -> list[dict[str, Any]]:
    return list((await ok(client, "node_history", node_id=node["id"], limit=200))["items"])


def ids(items: list[dict[str, Any]]) -> list[int]:
    return [item["id"] for item in items]


def count_rows(table: str) -> int:
    result = run_sql(f"SELECT count(*) FROM {table}", plain=True)
    assert result.returncode == 0, result.stderr
    return int(result.stdout.strip())


def counts() -> tuple[int, ...]:
    """How many nodes, links, rules and events there are: what a refused plan must not change."""
    return tuple(count_rows(table) for table in ("nodes", "links", "rules", "events"))


# -- next_work: several agents at once, and what each is told ------------------------------------


async def test_ten_agents_asking_at_once_for_five_ready_tasks_get_five_different_ones(
    client: Client,
) -> None:
    epic, _, tasks = await project(client, 5)

    results = await race_results(
        client,
        "next_work",
        [{"actor": f"agent-{number}", "within_node_id": epic["id"]} for number in range(10)],
    )

    assert all(accepted for accepted, _ in results)
    answers = [json.loads(text) for _, text in results]
    found = [answer for answer in answers if answer["found"]]
    told_nothing = [answer for answer in answers if not answer["found"]]
    assert len(found) == 5 and len(told_nothing) == 5
    # Five different tasks, every one of them handed out, none of them twice.
    assert sorted(answer["work"]["id"] for answer in found) == sorted(ids(tasks))
    assert len({answer["claim"]["actor"] for answer in found}) == 5
    assert all(answer["claim"]["actor"] == answer["work"]["claimed_by"] for answer in found)
    # The others are told plainly, not refused.
    for answer in told_nothing:
        assert answer["work"] is None and answer["claim"] is None and answer["context"] is None
        assert answer["message"].startswith("Nothing is free right now")
        assert re.search(
            r"\d+ being (worked on by someone|picked up by someone else this very moment)",
            answer["message"],
        )
        assert "no open work" not in answer["message"]
    # The database agrees: one claim on each task, and one claimed event each.
    for task in tasks:
        claimed = [event for event in await history(client, task) if event["action"] == "claimed"]
        assert len(claimed) == 1
        node = await get(client, task)
        assert node["status"] == "in_progress" and node["claimed_by"] == claimed[0]["actor"]
    # Asking again changes nothing: still nothing free, and nobody gets a task twice.
    again = await race_results(
        client,
        "next_work",
        [{"actor": f"agent-{number}", "within_node_id": epic["id"]} for number in range(10)],
    )
    assert not any(json.loads(text)["found"] for _, text in again)
    assert all("5 being worked on by someone" in json.loads(text)["message"] for _, text in again)


async def test_the_most_urgent_work_comes_first_and_a_story_with_open_tasks_does_not(
    client: Client,
) -> None:
    epic, story, _ = await project(client, 0)
    priorities = {}
    for priority in ("low", "normal", "urgent", "high"):
        task = await ok(
            client,
            "create_node",
            actor="marie",
            kind="task",
            title=f"{priority} task",
            parent_id=story["id"],
            priority=priority,
        )
        priorities[task["id"]] = priority
    lone = await ok(
        client,
        "create_node",
        actor="marie",
        kind="story",
        title="A story with no tasks",
        parent_id=epic["id"],
        priority="low",
    )

    order = []
    for number in range(5):
        answer = await take(client, f"agent-{number}", within_node_id=epic["id"])
        assert answer["found"]
        order.append(priorities.get(answer["work"]["id"], "lone story"))
    # Urgent first, then high, normal and low. The lone story is low too but newer than the low
    # task. The story that has tasks is never handed out: it is worked through its tasks.
    assert order == ["urgent", "high", "normal", "low", "lone story"]
    assert (await get(client, lone))["claimed_by"] == "agent-4"
    assert (await get(client, story))["claimed_by"] is None
    assert (await take(client, "agent-5", within_node_id=epic["id"]))["found"] is False


async def test_peeking_shows_the_work_without_taking_it(client: Client) -> None:
    epic, _, (task,) = await project(client, 1)

    peeked = await take(client, "ann", within_node_id=epic["id"], peek=True)

    assert peeked["found"] and peeked["peeked"] and peeked["claim"] is None
    assert peeked["work"]["id"] == task["id"] and "without claiming" in peeked["message"]
    assert (await get(client, task))["claimed_by"] is None
    assert [e["action"] for e in await history(client, task)] == ["create"]
    taken = await take(client, "bob", within_node_id=epic["id"])
    assert taken["work"]["id"] == task["id"] and taken["claim"]["actor"] == "bob"
    # Looking when nothing is free says so too, and does not refuse.
    nothing = await take(client, "ann", within_node_id=epic["id"], peek=True)
    assert nothing["found"] is False and "1 being worked on by someone" in nothing["message"]


async def test_next_work_refuses_bad_asks_and_says_what_is_waiting_when_nothing_is_free(
    client: Client,
) -> None:
    epic, _, (first, second, third) = await project(client, 3)
    await needs_first(client, second, first)
    blocked = await get(client, third)
    await ok(
        client,
        "update_node",
        actor="marie",
        node_id=third["id"],
        expected_version=blocked["version"],
        status="blocked",
    )
    await take(client, "ann", within_node_id=epic["id"])

    nothing = await take(client, "bob", within_node_id=epic["id"])

    assert nothing["found"] is False
    assert "1 being worked on by someone" in nothing["message"]
    assert "1 waiting for other work to be done" in nothing["message"]
    assert "1 marked blocked" in nothing["message"]
    assert "context_chars" in await refused(
        client, "next_work", actor="ann", within_node_id=epic["id"], context_chars=999
    )
    assert "no node with id" in await refused(client, "next_work", actor="ann", within_node_id=-5)
    assert "Say who is making this change" in await refused(client, "next_work", actor=" ")


async def test_the_context_holds_exactly_what_the_work_needs(client: Client) -> None:
    epic, story, (first, second) = await project(client, 2, body="The story in more words.")
    other_epic, other_story, _ = await project(client, 1)
    await needs_first(client, second, first)
    kept = await make_rule(
        client, "Invoices carry a number", body="Each has its own number.", story_id=story["id"]
    )
    await make_rule(client, "A dropped rule", story_id=story["id"], status="dropped")
    await make_rule(client, "A rule of another story", story_id=other_story["id"])
    on_epic = await make_decision(client, "Decided on the epic", node_ids=[epic["id"]])
    await make_decision(client, "Decided on the second task", node_ids=[second["id"]])
    await make_decision(client, "Decided elsewhere", node_ids=[other_story["id"]])
    old = await make_decision(client, "The old way", node_ids=[story["id"]])
    new = await ok(
        client,
        "supersede_decision",
        actor="agent-1",
        decision_id=old["id"],
        title="The new way",
        text="Do it the new way",
        reason="It changed",
        decided_by="Marie",
    )
    principle = await make_decision(client, "Always be careful", kind="principle")
    fact = await make_fact(client, "Invoices are sent as PDFs", node_ids=[story["id"]])
    belief = await make_belief(client, [fact], "PDFs are probably enough", node_ids=[story["id"]])
    await make_fact(client, "A fact about somewhere else", node_ids=[other_story["id"]])

    answer = await take(client, "ann", within_node_id=epic["id"], context_chars=1_000_000)

    assert answer["work"]["id"] == first["id"]
    context = answer["context"]
    assert [(p["kind"], p["id"]) for p in context["parents"]] == [
        ("epic", epic["id"]),
        ("story", story["id"]),
    ]
    assert context["parents"][1]["body_preview"] == "The story in more words."
    assert ids(context["rules"]) == [kept["id"]]
    assert context["rules"][0]["body"] == "Each has its own number."
    assert set(ids(context["active_decisions"])) == {on_epic["id"], new["id"]}
    assert all(d["status"] == "active" for d in context["active_decisions"])
    assert ids(context["superseded_decisions"]) == [old["id"]]
    assert context["superseded_decisions"][0]["superseded_by_id"] == new["id"]
    # Every principle in force, wherever it is linked.
    every_principle = await ok(
        client, "list_decisions", kind="principle", status="active", limit=200
    )
    assert every_principle["next_after_id"] is None
    assert set(ids(context["active_principles"])) == set(ids(every_principle["items"]))
    assert principle["id"] in ids(context["active_principles"])
    assert ids(context["facts"]) == [fact["id"]] and context["facts"][0]["suggestion"] is False
    assert ids(context["beliefs"]) == [belief["id"]] and context["beliefs"][0]["suggestion"] is True
    assert [(d["node"]["id"], d["becomes_ready_when_done"]) for d in context["unblocks"]] == [
        (second["id"], True)
    ]
    assert context["prerequisites"] == [] and context["handoffs"] == []
    assert context["left_out"] == {} and context["cut_note"] is None
    assert context["characters"] <= context["max_characters"]
    assert other_epic["id"] not in [p["id"] for p in context["parents"]]


async def test_the_context_keeps_to_its_size_and_says_what_it_left_out(client: Client) -> None:
    epic, _, (first, second) = await project(client, 2)
    await needs_first(client, second, first)
    story_id = first["parent_id"]
    for number in range(30):
        await make_rule(client, f"Rule {number}", body="Detail. " * 90, story_id=story_id)

    small = await take(client, "ann", within_node_id=epic["id"], peek=True, context_chars=3000)
    large = await take(client, "ann", within_node_id=epic["id"], peek=True, context_chars=1_000_000)

    context = small["context"]
    assert small["work"]["title"] == "Task 0"
    assert context["characters"] <= 3000 and context["max_characters"] == 3000
    assert 0 < len(context["rules"]) < 30
    assert context["left_out"]["rules"] == 30 - len(context["rules"])
    assert "left out" in context["cut_note"] and "larger context_chars" in context["cut_note"]
    # What is cut is the end: the item that waits for this one comes after the rules, so it is
    # cut as well, and counted.
    assert context["unblocks"] == [] and context["left_out"]["unblocks"] == 1
    assert len(large["context"]["rules"]) == 30 and large["context"]["left_out"] == {}
    assert ids([d["node"] for d in large["context"]["unblocks"]]) == [second["id"]]


# -- finish_work and the handoff note ------------------------------------------------------------


async def test_finishing_needs_a_note_and_hands_it_to_the_next_agent(client: Client) -> None:
    epic, _, (first, second) = await project(client, 2)
    await needs_first(client, second, first)
    assert (await take(client, "ann", within_node_id=epic["id"]))["work"]["id"] == first["id"]
    before = await get(client, first)
    events_before = await history(client, first)

    for blank in ("", "   ", "\n\t "):
        message = await refused(
            client, "finish_work", actor="ann", node_id=first["id"], outcome="done", note=blank
        )
        assert "A handoff note is needed" in message
    assert "outcome must be one of" in await refused(
        client, "finish_work", actor="ann", node_id=first["id"], outcome="finished", note="x"
    )
    assert "claimed by ann, not by you" in await refused(
        client, "finish_work", actor="bob", node_id=first["id"], outcome="done", note="Mine now"
    )
    # None of that changed anything.
    assert await get(client, first) == before
    assert await history(client, first) == events_before
    assert (await get(client, second))["status"] == "new"

    finished = await ok(
        client,
        "finish_work",
        actor="ann",
        node_id=first["id"],
        outcome="done",
        note="Used the invoice library; the numbering is in numbering.py.",
    )

    assert finished["node"]["status"] == "done" and finished["node"]["claimed_by"] is None
    assert finished["handoff"]["actor"] == "ann" and finished["handoff"]["outcome"] == "done"
    assert [(n["id"], n["status"]) for n in finished["unblocked"]] == [(second["id"], "ready")]
    done = await get(client, first)
    assert done["handoff_count"] == 1
    assert done["last_handoff"]["note"].startswith("Used the invoice library")
    assert [e["action"] for e in await history(client, first)][-3:] == [
        "handoff",
        "claim_released",
        "update",
    ]

    nxt = await take(client, "bob", within_node_id=epic["id"])
    assert nxt["work"]["id"] == second["id"]
    (prerequisite,) = nxt["context"]["prerequisites"]
    assert prerequisite["node"]["id"] == first["id"]
    assert prerequisite["handoffs"][0]["note"].startswith("Used the invoice library")
    assert prerequisite["handoffs"][0]["actor"] == "ann"


async def test_every_way_of_leaving_work_leaves_a_note_and_a_status(client: Client) -> None:
    epic, story, tasks = await project(client, 4)
    outcomes = [
        ("blocked", "blocked"),
        ("dropped", "dropped"),
        ("done", "done"),
        ("released", "ready"),
    ]
    for task, (outcome, status) in zip(tasks, outcomes, strict=True):
        taken = await take(client, "ann", within_node_id=story["id"])
        assert taken["work"]["id"] == task["id"]
        finished = await ok(
            client,
            "finish_work",
            actor="ann",
            node_id=task["id"],
            outcome=outcome,
            note=f"Left it {outcome}.",
        )
        assert finished["node"]["status"] == status, outcome
        assert finished["handoff"]["outcome"] == outcome
    # Released work is free again and carries its note to the next agent.
    again = await take(client, "bob", within_node_id=epic["id"])
    assert again["work"]["id"] == tasks[3]["id"]
    assert [h["note"] for h in again["context"]["handoffs"]] == ["Left it released."]


async def test_releasing_a_claim_needs_a_note_too_and_keeps_it(client: Client) -> None:
    epic, _, (task,) = await project(client, 1)
    await take(client, "ann", within_node_id=epic["id"])

    assert "A handoff note is needed" in await refused(
        client, "release_claim", actor="ann", node_id=task["id"], note="  "
    )
    assert (await get(client, task))["claimed_by"] == "ann"
    await ok(
        client,
        "release_claim",
        actor="ann",
        node_id=task["id"],
        note="Stopped halfway: the form is built, the sending is not.",
    )

    node = await get(client, task)
    assert node["claimed_by"] is None and node["handoff_count"] == 1
    assert node["last_handoff"]["outcome"] == "released"
    assert node["status"] == "ready"
    answer = await take(client, "bob", within_node_id=epic["id"])
    assert answer["work"]["id"] == task["id"]
    assert answer["context"]["handoffs"][0]["note"].startswith("Stopped halfway")


async def test_work_left_to_lapse_shows_as_lapsed_with_no_handoff_and_can_be_taken_over(
    client: Client,
) -> None:
    epic, _, (task,) = await project(client, 1)
    await take(client, "ann", within_node_id=epic["id"], lease_seconds=2)
    await anyio.sleep(2.6)

    lapsed = await get(client, task)
    assert lapsed["claimed_by"] is None and lapsed["claim_expires_at"] is None
    assert lapsed["lapsed_claim_by"] == "ann" and lapsed["lapsed_claim_at"] is not None
    assert lapsed["status"] == "in_progress"
    assert lapsed["handoff_count"] == 0 and lapsed["last_handoff"] is None

    taken = await take(client, "bob", within_node_id=epic["id"])
    assert taken["work"]["id"] == task["id"] and taken["claim"]["actor"] == "bob"
    assert taken["taken_over_from"] == "ann" and "ann claimed it earlier" in taken["message"]
    held = await get(client, task)
    assert held["claimed_by"] == "bob" and held["lapsed_claim_by"] is None
    assert "claimed by bob, not by you" in await refused(
        client, "finish_work", actor="ann", node_id=task["id"], outcome="done", note="Done"
    )


async def test_the_one_who_let_a_claim_lapse_can_still_finish_until_someone_takes_over(
    client: Client,
) -> None:
    epic, _, (task,) = await project(client, 1)
    await take(client, "ann", within_node_id=epic["id"], lease_seconds=2)
    await anyio.sleep(2.6)

    finished = await ok(
        client,
        "finish_work",
        actor="ann",
        node_id=task["id"],
        outcome="done",
        note="Finished it after the lease ran out.",
    )

    assert finished["node"]["status"] == "done"
    assert finished["node"]["lapsed_claim_by"] is None


async def test_the_database_refuses_a_handoff_without_a_note_on_its_own(client: Client) -> None:
    _, _, (task,) = await project(client, 1)
    before = count_rows("events")

    for after in ('{"outcome": "done", "note": "  "}', '{"outcome": "done"}', '{"note": "x"}'):
        result = run_sql(
            "INSERT INTO events (node_id, action, actor, after) "
            f"VALUES ({task['id']}, 'handoff', 'someone', '{after}')"
        )
        assert result.returncode != 0
        assert "handoff_has_an_outcome_and_a_note" in result.stderr

    assert count_rows("events") == before


# -- create_plan ---------------------------------------------------------------------------------


def a_plan() -> dict[str, Any]:
    """1 epic, 3 stories, 8 tasks, 5 links and 4 rules."""
    items: list[dict[str, Any]] = [{"key": "epic", "kind": "epic", "title": "Launch invoicing"}]
    for story in range(3):
        items.append(
            {"key": f"s{story}", "kind": "story", "title": f"Story {story}", "parent": "epic"}
        )
    for task in range(8):
        items.append(
            {
                "key": f"t{task}",
                "kind": "task",
                "title": f"Task {task}",
                "parent": f"s{task % 3}",
                "priority": "high" if task == 0 else "normal",
                "target_on": day(30),
            }
        )
    links = [
        {"source": "t3", "target": "t0", "link_type": "needs_first"},
        {"source": "t6", "target": "t3", "link_type": "needs_first"},
        {"source": "t4", "target": "t1", "link_type": "needs_first"},
        {"source": "s1", "target": "s0", "link_type": "relates_to"},
        {"source": "t7", "target": "t2", "link_type": "needs_first"},
    ]
    rules = [
        {"title": "Every invoice has a number", "story": "s0", "status": "settled"},
        {"title": "Totals are in euros", "story": "s0"},
        {"title": "Credit notes keep the old number", "story": "s1", "body": "See finance."},
        {"title": "Not placed yet"},
    ]
    return {"items": items, "links": links, "rules": rules}


async def test_a_plan_creates_everything_in_one_call(client: Client) -> None:
    plan = a_plan()

    result = await ok(client, "create_plan", actor="ann", **plan)

    assert result["counts"] == {"epic": 1, "story": 3, "task": 8, "links": 5, "rules": 4}
    assert [item["key"] for item in result["created"]] == [i["key"] for i in plan["items"]]
    new = {item["key"]: item for item in result["created"]}
    assert len(result["link_ids"]) == 5 and len(result["rule_ids"]) == 4
    epic_id = new["epic"]["id"]
    stories = await all_nodes(client, parent_id=epic_id)
    assert ids(stories) == [new[f"s{n}"]["id"] for n in range(3)]
    # Each task is in the story the plan named, and the order and dates were kept.
    for number in range(8):
        task = await ok(client, "get_node", node_id=new[f"t{number}"]["id"])
        assert task["parent_id"] == new[f"s{number % 3}"]["id"]
        assert task["priority"] == ("high" if number == 0 else "normal")
        assert task["target_on"] == day(30)
    # The links are real: t3 waits for t0, t6 for t3, and so t6 is not ready.
    t3 = await ok(client, "get_node", node_id=new["t3"]["id"])
    assert t3["waiting_on"] == [new["t0"]["id"]]
    t6 = await ok(client, "get_node", node_id=new["t6"]["id"])
    assert t6["waiting_on"] == [new["t3"]["id"]]
    relates = await ok(client, "list_links", node_id=new["s1"]["id"], link_type="relates_to")
    assert relates["total"] == 1
    # The rules are in their stories, and the last one is unplaced.
    first_story_rules = await ok(client, "list_rules", story_id=new["s0"]["id"])
    assert [r["status"] for r in first_story_rules["items"]] == ["settled", "held"]
    assert [r["id"] for r in first_story_rules["items"]] == result["rule_ids"][:2]
    loose = await ok(client, "get_rule", rule_id=result["rule_ids"][3])
    assert loose["story_id"] is None
    # Everything is in the history with its actor.
    for item in result["created"]:
        created = [e for e in await history(client, item) if e["action"] == "create"]
        assert [e["actor"] for e in created] == ["ann"]


async def test_a_plan_can_hang_from_what_already_exists(client: Client) -> None:
    epic, story, (task,) = await project(client, 1)

    result = await ok(
        client,
        "create_plan",
        actor="ann",
        items=[
            {"key": "extra", "kind": "task", "title": "Extra task", "parent": story["id"]},
            {"key": "later", "kind": "story", "title": "Later story", "parent": epic["id"]},
        ],
        links=[{"source": "extra", "target": task["id"], "link_type": "needs_first"}],
        rules=[{"title": "A rule for the old story", "story": story["id"]}],
    )

    extra = result["created"][0]
    node = await ok(client, "get_node", node_id=extra["id"])
    assert node["parent_id"] == story["id"] and node["waiting_on"] == [task["id"]]
    assert (await ok(client, "get_rule", rule_id=result["rule_ids"][0]))["story_id"] == story["id"]


async def test_a_plan_with_any_problem_creates_nothing_and_names_every_problem(
    client: Client,
) -> None:
    epic, story, (task, other) = await project(client, 2)
    await needs_first(client, other, task)
    before = counts()

    message = await refused(
        client,
        "create_plan",
        actor="ann",
        items=[
            {"key": "a", "kind": "epic", "title": "Fine epic"},
            {"key": "a", "kind": "story", "title": "Same key", "parent": "a"},
            {"key": "b", "kind": "feature", "title": "Wrong kind"},
            {"key": "c", "kind": "story", "title": "  ", "parent": "a"},
            {"key": "d", "kind": "task", "title": "No parent"},
            {"key": "e", "kind": "task", "title": "Under an epic", "parent": "a"},
            {"key": "f", "kind": "story", "title": "No such parent", "parent": "missing"},
            {
                "key": "g",
                "kind": "task",
                "title": "Bad priority",
                "parent": story["id"],
                "priority": "now",
            },
            {
                "key": "h",
                "kind": "task",
                "title": "Dates the wrong way",
                "parent": story["id"],
                "start_on": day(9),
                "target_on": day(1),
            },
            {"key": "i", "kind": "task", "title": "Parent that is not there", "parent": 999999999},
            {"key": "j", "kind": "epic", "title": "An epic with a parent", "parent": epic["id"]},
        ],
        links=[
            {"source": "a", "target": "nowhere", "link_type": "needs_first"},
            {"source": "a", "target": "a", "link_type": "relates_to"},
            {"source": "a", "target": task["id"], "link_type": "friends_with"},
            # The plan makes this a circle with a link that exists already.
            {"source": task["id"], "target": other["id"], "link_type": "needs_first"},
        ],
        rules=[{"title": "Rule for nowhere", "story": "missing"}, {"title": " "}],
    )

    problems = [line for line in message.splitlines() if re.match(r"\d+\. ", line)]
    assert "The plan was not created, because of" in message
    assert "Nothing was created" in message
    for expected in (
        "is used by more than one item",
        "kind must be one of",
        "title cannot be empty",
        "needs a parent story",
        "belongs under a story",
        "is not the key of any item in this plan",
        "Item 8 (key 'g'",
        "priority must be one of",
        "Item 9 (key 'h'",
        "no node with id 999999999",
        "an epic has no parent",
        "nowhere",
        "link_type must be one of",
        "cannot be linked to itself",
        "circle of waiting",
        "Rule 1",
        "Rule 2",
    ):
        assert any(expected in problem for problem in problems), (expected, message)
    assert len(problems) >= 14
    assert counts() == before


async def test_a_circle_of_waiting_is_named_and_creates_nothing(client: Client) -> None:
    before = counts()

    message = await refused(
        client,
        "create_plan",
        actor="ann",
        items=[
            {"key": "e", "kind": "epic", "title": "E"},
            {"key": "s", "kind": "story", "title": "S", "parent": "e"},
            {"key": "x", "kind": "task", "title": "X", "parent": "s"},
            {"key": "y", "kind": "task", "title": "Y", "parent": "s"},
            {"key": "z", "kind": "task", "title": "Z", "parent": "s"},
        ],
        links=[
            {"source": "x", "target": "y", "link_type": "needs_first"},
            {"source": "y", "target": "z", "link_type": "needs_first"},
            {"source": "z", "target": "x", "link_type": "needs_first"},
        ],
    )

    assert "the key 'x' needs the key 'y', which needs the key 'z', which needs the key 'x'" in (
        message
    )
    assert counts() == before
    assert "plan is empty" in await refused(client, "create_plan", actor="ann", items=[])


async def test_a_plan_of_five_hundred_items_is_created_in_one_call(client: Client) -> None:
    items: list[dict[str, Any]] = [{"key": "epic", "kind": "epic", "title": "A big plan"}]
    links: list[dict[str, Any]] = []
    for story in range(49):
        items.append({"key": f"s{story}", "kind": "story", "title": f"S{story}", "parent": "epic"})
        for task in range(9):
            items.append(
                {
                    "key": f"s{story}t{task}",
                    "kind": "task",
                    "title": f"T{task}",
                    "parent": f"s{story}",
                }
            )
            if task:
                links.append(
                    {
                        "source": f"s{story}t{task}",
                        "target": f"s{story}t{task - 1}",
                        "link_type": "needs_first",
                    }
                )
    assert len(items) == 1 + 49 + 49 * 9 == 491
    items.extend(
        {"key": f"extra{n}", "kind": "task", "title": f"Extra {n}", "parent": "s0"}
        for n in range(9)
    )
    assert len(items) == 500

    started = time.monotonic()
    result = await ok(client, "create_plan", actor="ann", items=items, links=links)
    seconds = time.monotonic() - started

    assert result["counts"] == {"epic": 1, "story": 49, "task": 450, "links": 392, "rules": 0}
    assert len(result["created"]) == 500
    epic_id = result["created"][0]["id"]
    page = await ok(client, "list_nodes", parent_id=epic_id, limit=200)
    assert page["total"] == 49
    assert seconds < 60, f"a plan of 500 items took {seconds:.1f} seconds"
    print(f"\nA plan of 500 items and {len(links)} links took {seconds:.1f} seconds.")


async def test_a_big_plan_with_one_bad_item_at_the_end_creates_nothing(client: Client) -> None:
    items: list[dict[str, Any]] = [{"key": "epic", "kind": "epic", "title": "Almost fine"}]
    items.extend(
        {"key": f"s{n}", "kind": "story", "title": f"S{n}", "parent": "epic"} for n in range(299)
    )
    items.append({"key": "bad", "kind": "task", "title": "Hangs from an epic", "parent": "epic"})
    before = counts()

    message = await refused(client, "create_plan", actor="ann", items=items)

    assert "Item 301" in message and "belongs under a story" in message
    assert counts() == before


# -- update_nodes --------------------------------------------------------------------------------


async def test_five_changes_with_one_stale_apply_four_and_refuse_one(client: Client) -> None:
    _, story, tasks = await project(client, 5)
    versions = {task["id"]: task["version"] for task in tasks}
    # Someone else changes the third task after it was read.
    third = tasks[2]
    await ok(
        client,
        "update_node",
        actor="bob",
        node_id=third["id"],
        expected_version=1,
        title="Changed under you",
    )

    result = await ok(
        client,
        "update_nodes",
        actor="ann",
        changes=[
            {"node_id": task["id"], "expected_version": versions[task["id"]], "priority": "high"}
            for task in tasks
        ],
    )

    assert (result["applied"], result["refused"]) == (4, 1)
    assert [r["node_id"] for r in result["results"]] == ids(tasks)
    assert [r["applied"] for r in result["results"]] == [True, True, False, True, True]
    stale = result["results"][2]
    assert (
        stale["version"] is None and "is at version 2, but you sent version 1" in stale["refusal"]
    )
    for task, report in zip(tasks, result["results"], strict=True):
        events = await history(client, task)
        mine = [e for e in events if e["actor"] == "ann"]
        node = await get(client, task)
        if report["applied"]:
            assert [e["action"] for e in mine] == ["update"]
            assert mine[0]["after"] == {"priority": "high", "version": 2}
            assert (node["priority"], node["version"]) == ("high", 2)
            assert report["version"] == 2
        else:
            assert mine == [] and node["priority"] == "normal"
            assert node["title"] == "Changed under you"
    assert story["id"]


async def test_a_hundred_and_twenty_changes_report_each_one(client: Client) -> None:
    _, _, tasks = await project(client, 120)
    changes: list[dict[str, Any]] = []
    for number, task in enumerate(tasks):
        change: dict[str, Any] = {
            "node_id": task["id"],
            "expected_version": 1,
            "title": f"Renamed {number}",
        }
        if number % 10 == 0:
            change["status"] = "finished"  # not a status: refused
        changes.append(change)
    changes.append({"node_id": 999999999, "expected_version": 1, "title": "Nobody"})

    result = await ok(client, "update_nodes", actor="ann", changes=changes)

    assert len(result["results"]) == 121
    assert (result["applied"], result["refused"]) == (108, 13)
    for number, report in enumerate(result["results"][:120]):
        assert report["node_id"] == tasks[number]["id"]
        assert report["applied"] is (number % 10 != 0)
        assert (report["refusal"] is None) is report["applied"]
    assert "status must be one of" in result["results"][0]["refusal"]
    assert "no node with id 999999999" in result["results"][120]["refusal"]
    assert (await get(client, tasks[1]))["title"] == "Renamed 1"
    assert (await get(client, tasks[0]))["title"] == "Task 0"
    assert "nothing to change" in await refused(client, "update_nodes", actor="ann", changes=[])


# -- changes_since -------------------------------------------------------------------------------


async def event_ids_after(first: int) -> list[int]:
    result = run_sql(f"SELECT id FROM events WHERE id > {first} ORDER BY id", plain=True)
    assert result.returncode == 0, result.stderr
    return [int(line) for line in result.stdout.split()]


async def feed(client: Client, after: int, limit: int, **filters: Any) -> tuple[list[int], int]:
    """Every event id after this one, following the pages, and the latest_event_id to keep."""
    found: list[int] = []
    cursor = after
    while True:
        page = await ok(client, "changes_since", after_event_id=cursor, limit=limit, **filters)
        found.extend(ids(page["items"]))
        if page["next_after_id"] is None:
            return found, int(page["latest_event_id"])
        cursor = page["next_after_id"]


async def caught_up(client: Client, last_event_id: int) -> None:
    """Wait until the feed shows this event. A change another call is still writing is held back
    a moment, so after a write the feed can briefly be a little behind the database."""
    for _ in range(100):
        page = await ok(client, "changes_since", after_event_id=0, limit=1)
        if page["latest_event_id"] >= last_event_id:
            return
        await anyio.sleep(0.1)
    pytest.fail(f"the feed never showed event {last_event_id}")


async def test_changes_since_returns_exactly_the_events_after_the_one_given(client: Client) -> None:
    start = (await ok(client, "changes_since", after_event_id=0, limit=1))["latest_event_id"]
    epic, story, tasks = await project(client, 3)
    await needs_first(client, tasks[1], tasks[0])
    await set_status(client, tasks[0], "blocked")
    await make_rule(client, "A rule", story_id=story["id"])
    await make_decision(client, "A decision", node_ids=[epic["id"]])
    await make_fact(client, "A fact", node_ids=[story["id"]])
    expected = await event_ids_after(start)
    assert len(expected) >= 10
    await caught_up(client, expected[-1])

    for limit in (1, 3, 7, 200):
        found, latest = await feed(client, start, limit)
        assert found == expected, limit
        assert latest == expected[-1]
    # Nothing before, and nothing after the last one.
    assert (await feed(client, expected[-1], 5))[0] == []
    assert (await feed(client, expected[4], 100))[0] == expected[5:]
    # Following the feed from where it ended shows only what happens next.
    await ok(client, "claim_node", actor="cy", node_id=tasks[2]["id"])
    after_that = await event_ids_after(expected[-1])
    assert len(after_that) == 1
    await caught_up(client, after_that[0])
    assert (await feed(client, expected[-1], 5))[0] == after_that


async def test_the_feed_can_be_narrowed_to_one_item_or_one_actor(client: Client) -> None:
    start = (await ok(client, "changes_since", after_event_id=0, limit=1))["latest_event_id"]
    _, story, (task,) = await project(client, 1)
    rule = await make_rule(client, "A rule", story_id=story["id"])
    decision = await make_decision(client, "A decision", node_ids=[task["id"]])
    fact = await make_fact(client, "A fact", node_ids=[task["id"]])
    await ok(
        client, "update_node", actor="zed", node_id=task["id"], expected_version=1, title="New"
    )
    await caught_up(client, (await event_ids_after(start))[-1])

    by_zed = await ok(client, "changes_since", after_event_id=start, actor="zed")
    assert [(e["action"], e["actor"]) for e in by_zed["items"]] == [("update", "zed")]
    for filters, action in (
        ({"rule_id": rule["id"]}, "rule_created"),
        ({"decision_id": decision["id"]}, "decision_recorded"),
        ({"memory_id": fact["id"]}, "memory_recorded"),
    ):
        page = await ok(client, "changes_since", after_event_id=start, **filters)
        actions = [e["action"] for e in page["items"]]
        assert actions[0] == action and page["items"]
        assert all(e["actor"] != "zed" for e in page["items"])
    on_task = await ok(client, "changes_since", after_event_id=start, node_id=task["id"])
    assert {"create", "update"} <= {e["action"] for e in on_task["items"]}
    assert all(task["id"] in (e["node_id"], e["other_node_id"]) for e in on_task["items"])
    assert "at most one of" in await refused(
        client, "changes_since", node_id=task["id"], rule_id=rule["id"]
    )
    assert "no event with id 999999999" in await refused(
        client, "changes_since", after_event_id=999999999
    )
    assert "no node with id" in await refused(client, "changes_since", node_id=-1)
    assert "0 or more" in await refused(client, "changes_since", after_event_id=-1)


async def test_the_newest_changes_can_be_read_and_walked_back_without_reading_them_all(
    client: Client,
) -> None:
    start = (await ok(client, "changes_since", after_event_id=0, limit=1))["latest_event_id"]
    _, story, tasks = await project(client, 4)
    await set_status(client, tasks[0], "blocked")
    await make_rule(client, "A rule", story_id=story["id"])
    await caught_up(client, (await event_ids_after(start))[-1])
    # The feed's own order (a change that began earlier comes first), which is what walking
    # backwards must give exactly reversed.
    expected, _ = await feed(client, start, 200)
    assert len(expected) >= 8

    # The newest few, oldest first among them, with the way back and no way forward.
    newest = await ok(client, "changes_since", after_event_id=start, newest=True, limit=4)
    assert ids(newest["items"]) == expected[-4:]
    assert newest["next_after_id"] is None and newest["next_before_id"] == expected[-4]
    assert newest["latest_event_id"] == expected[-1]
    # Walking back gives every event once, newest page first, and ends with no way back.
    walked: list[int] = []
    before: int | None = None
    while True:
        page = await ok(
            client,
            "changes_since",
            after_event_id=start,
            limit=3,
            newest=True,
            **({} if before is None else {"before_event_id": before}),
        )
        walked = ids(page["items"]) + walked
        before = page["next_before_id"]
        if before is None:
            break
    assert walked == expected
    # Before an event implies newest, and it is exclusive.
    older = await ok(client, "changes_since", before_event_id=expected[5], limit=2)
    assert ids(older["items"]) == expected[3:5] and older["next_before_id"] == expected[3]
    # The filters narrow it, and a short answer has nothing before it.
    for_task = await ok(
        client, "changes_since", node_id=tasks[0]["id"], newest=True, limit=50, after_event_id=start
    )
    assert for_task["next_before_id"] is None
    assert all(tasks[0]["id"] in (e["node_id"], e["other_node_id"]) for e in for_task["items"])
    assert "1 or more" in await refused(client, "changes_since", before_event_id=0)
    assert "no event with id 999999999" in await refused(
        client, "changes_since", before_event_id=999999999
    )
    # Reading forward is as it was: no way back is offered.
    forward = await ok(client, "changes_since", after_event_id=start, limit=3)
    assert forward["next_before_id"] is None


async def test_an_event_that_finishes_late_is_never_skipped_by_someone_following_the_feed(
    client: Client,
) -> None:
    """Two changes are written at the same moment. `fast` begins first but writes its event
    later (so it gets the higher id), and finishes first. `slow` begins second, writes its event
    first (so it gets the lower id) and finishes last. A feed that went by id alone would show
    `fast` and then never `slow`."""
    _, _, (task,) = await project(client, 1)
    start = (await ok(client, "changes_since", after_event_id=0, limit=1))["latest_event_id"]
    marker = unique_word()
    fast, slow = f"fast-{marker}", f"slow-{marker}"

    def insert(actor: str) -> str:
        return (
            "INSERT INTO events (node_id, action, actor, after) "
            f"VALUES ({task['id']}, 'update', '{actor}', '{{\"title\": \"{actor}\"}}')"
        )

    def write(sql: str) -> None:
        result = run_sql(sql)
        assert result.returncode == 0, result.stderr

    # Each of these is one transaction: psql sends a whole -c string as one.
    fast_thread = threading.Thread(
        target=write,
        args=(f"SELECT pg_current_xact_id(); SELECT pg_sleep(8); {insert(fast)}",),
        daemon=True,
    )
    slow_thread = threading.Thread(
        target=write, args=(f"{insert(slow)}; SELECT pg_sleep(12)",), daemon=True
    )
    fast_thread.start()
    # Wait until `fast` has its transaction number, and only then start `slow`.
    for _ in range(60):
        running = run_sql(
            "SELECT count(*) FROM pg_stat_activity WHERE backend_xid IS NOT NULL "
            f"AND query LIKE '%{fast}%' AND pid <> pg_backend_pid()",
            plain=True,
        )
        if running.stdout.strip() == "1":
            break
        await anyio.sleep(0.3)
    else:
        pytest.fail("the first writer never started")
    slow_thread.start()

    fast_thread.join(timeout=60)
    assert not fast_thread.is_alive()
    assert slow_thread.is_alive(), "the second writer finished too early for this test"
    # `fast` is done and `slow` is not: only `fast` can be shown, and the feed moves up to it.
    shown, latest = await feed(client, start, 50)
    assert len(shown) == 1 and latest == shown[0]
    fast_id = shown[0]

    slow_thread.join(timeout=60)
    assert not slow_thread.is_alive()
    slow_id = int(
        run_sql(f"SELECT id FROM events WHERE actor = '{slow}'", plain=True).stdout.strip()
    )
    began_later = run_sql(
        f"SELECT (SELECT xid FROM events WHERE id = {slow_id}) "
        f"> (SELECT xid FROM events WHERE id = {fast_id})",
        plain=True,
    )
    assert slow_id < fast_id and began_later.stdout.strip() == "t", (
        "the changes did not happen in the order this test needs"
    )
    await caught_up(client, slow_id)
    # Someone who followed the feed up to `fast` still gets `slow`, which has the lower id.
    later, latest = await feed(client, fast_id, 50)
    assert later == [slow_id] and latest == slow_id
    # And from the beginning both come, once each, in the order their changes began.
    assert (await feed(client, start, 1))[0] == [fast_id, slow_id]


# -- compact lists -------------------------------------------------------------------------------


async def test_a_list_of_fifty_long_items_is_at_least_five_times_smaller_by_default(
    client: Client,
) -> None:
    # About a page of requirements: 4,950 characters.
    body = "A long description of the work, with plenty of detail. " * 90
    result = await ok(
        client,
        "create_plan",
        actor="ann",
        items=[
            {"key": "e", "kind": "epic", "title": "Long bodies"},
            *(
                {
                    "key": f"s{n}",
                    "kind": "story",
                    "title": f"Story {n}",
                    "parent": "e",
                    "body": body,
                }
                for n in range(50)
            ),
        ],
        rules=[{"title": f"Rule {n}", "body": body, "story": "s0"} for n in range(50)],
    )
    epic_id = result["created"][0]["id"]

    compact = await ok(client, "list_nodes", parent_id=epic_id, limit=50)
    full = await ok(client, "list_nodes", parent_id=epic_id, limit=50, detail=True)

    assert len(compact["items"]) == len(full["items"]) == 50
    assert compact["total"] == full["total"] == 50
    assert all(item["body"] is None for item in compact["items"])
    assert all(len(item["body_preview"]) == 120 for item in compact["items"])
    assert all(item["body_chars"] == len(body) for item in compact["items"])
    assert all(item["body"] == body for item in full["items"])
    assert "Bodies are left out" in compact["left_out"] and full["left_out"] is None
    compact_size, full_size = len(json.dumps(compact)), len(json.dumps(full))
    assert full_size >= 5 * compact_size, (compact_size, full_size)
    print(f"\nlist_nodes of 50: {compact_size:,} characters by default, {full_size:,} with detail.")

    story_id = result["created"][1]["id"]
    rules = await ok(client, "list_rules", story_id=story_id, limit=50)
    rules_full = await ok(client, "list_rules", story_id=story_id, limit=50, detail=True)
    assert rules["total"] == 50
    assert all(r["body"] is None and r["body_chars"] == len(body) for r in rules["items"])
    assert all(r["body"] == body for r in rules_full["items"])
    assert len(json.dumps(rules_full)) >= 5 * len(json.dumps(rules))
    ready = await ok(client, "ready_work", limit=200)
    assert all(item["body"] is None for item in ready["items"])
    report = await ok(client, "coverage_report", show="stories_without_rules", limit=200)
    assert all(item["body"] is None for item in report["stories_without_rules"])
    assert report["left_out"]


async def test_reading_one_item_always_gives_the_whole_body(client: Client) -> None:
    body = "Every word of this matters. " * 100
    _, story, _ = await project(client, 0, body=body)
    rule = await make_rule(client, "A long rule", body=body)

    assert (await get(client, story))["body"] == body
    assert (await ok(client, "get_rule", rule_id=rule["id"]))["body"] == body
    assert story["body"] == body


# -- the check for similar items when something is created ----------------------------------------


def distinct() -> str:
    """A title made of made-up words: no other test's titles are anything like it in meaning."""
    return " ".join(unique_word() for _ in range(4))


async def test_creating_a_story_that_repeats_an_existing_one_says_so_and_still_creates_it(
    client: Client,
) -> None:
    title = distinct()
    epic = await ok(client, "create_node", actor="marie", kind="epic", title="Invoicing")
    existing = await ok(
        client,
        "create_node",
        actor="marie",
        kind="story",
        title=title,
        parent_id=epic["id"],
    )
    assert existing["similar"] == [] and existing["similar_checked"] is True
    await reindexed(client)

    again = await ok(
        client, "create_node", actor="ann", kind="story", title=title, parent_id=epic["id"]
    )

    assert again["id"] != existing["id"] and again["version"] == 1
    assert again["similar_checked"] is True and again["similar_note"] is None
    (match,) = again["similar"]
    assert (match["item_type"], match["id"]) == ("story", existing["id"])
    assert match["suggestion"] is True and match["similarity"] >= 0.55
    # It was created, and nothing was linked or merged.
    assert (await get(client, again))["id"] == again["id"]
    assert (await ok(client, "list_links", node_id=again["id"]))["total"] == 0
    # Something unrelated is not flagged, and neither is a task (tasks are not checked).
    other = await ok(
        client,
        "create_node",
        actor="ann",
        kind="story",
        title=distinct(),
        parent_id=epic["id"],
    )
    assert other["similar"] == [] and other["similar_checked"] is True
    task = await ok(
        client, "create_node", actor="ann", kind="task", title=title, parent_id=other["id"]
    )
    assert task["similar"] == [] and task["similar_checked"] is False


async def test_an_epic_that_repeats_an_existing_epic_is_flagged_too(client: Client) -> None:
    title = distinct()
    first = await ok(client, "create_node", actor="marie", kind="epic", title=title)
    await reindexed(client)

    second = await ok(client, "create_node", actor="marie", kind="epic", title=title)

    assert [(m["item_type"], m["id"]) for m in second["similar"]] == [("epic", first["id"])]


async def test_with_the_embedding_service_stopped_creating_still_works_and_says_it_did_not_check(
    client: Client,
) -> None:
    title = distinct()
    epic = await ok(client, "create_node", actor="marie", kind="epic", title="Invoicing")
    await ok(client, "create_node", actor="marie", kind="story", title=title, parent_id=epic["id"])
    await reindexed(client)

    with embeddings_stopped():
        story = await ok(
            client, "create_node", actor="ann", kind="story", title=title, parent_id=epic["id"]
        )
        planned = await ok(
            client,
            "create_plan",
            actor="ann",
            items=[{"key": "s", "kind": "story", "title": title, "parent": epic["id"]}],
        )

    assert story["similar"] == [] and story["similar_checked"] is False
    assert "cannot be reached" in story["similar_note"]
    assert (await get(client, story))["title"] == title
    assert planned["similar"] == [] and "cannot be reached" in planned["similar_note"]
    assert planned["counts"]["story"] == 1


async def test_a_plan_says_which_of_its_stories_look_like_ones_that_exist(client: Client) -> None:
    title = distinct()
    epic = await ok(client, "create_node", actor="marie", kind="epic", title="Invoicing")
    existing = await ok(
        client, "create_node", actor="marie", kind="story", title=title, parent_id=epic["id"]
    )
    await reindexed(client)

    result = await ok(
        client,
        "create_plan",
        actor="ann",
        items=[
            {"key": "s", "kind": "story", "title": title, "parent": epic["id"]},
            {
                "key": "u",
                "kind": "story",
                "title": distinct(),
                "parent": epic["id"],
            },
            {"key": "t", "kind": "task", "title": title, "parent": "s"},
        ],
    )

    assert result["similar_note"] is None
    (flagged,) = result["similar"]
    created = {item["key"]: item["id"] for item in result["created"]}
    assert flagged["key"] == "s" and flagged["id"] == created["s"]
    assert [(m["item_type"], m["id"]) for m in flagged["similar"]] == [("story", existing["id"])]


# -- what the server tells an agent when it connects ----------------------------------------------


async def test_the_server_instructions_name_only_tools_that_exist(client: Client) -> None:
    instructions = client.instructions
    assert instructions
    tools = {tool.name for tool in (await client.list_tools()).tools}

    named = set(re.findall(r"`([a-z_]+)`", instructions))

    assert named, "the instructions should name the tools of the working loop"
    assert named <= tools, f"names no tool: {sorted(named - tools)}"
    assert {"next_work", "recall", "record_decision", "finish_work"} <= named
    assert len(instructions.split()) < 300
