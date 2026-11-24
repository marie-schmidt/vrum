"""M5B against the running containers: the agent workflow (next_work, finish_work, create_plan,
update_nodes, changes_since), compact lists, the check for similar items on create, and the server
instructions, through a real MCP client (see ./check.sh)."""

import json
import re
import threading
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Any

import anyio
import pytest
from mcp import Client

from tests.helpers import (
    all_nodes,
    confirm_on_board,
    connect_as,
    connect_each,
    day,
    embeddings_stopped,
    make_decision,
    make_fact,
    make_rule,
    needs_first,
    ok,
    race_agents,
    refused,
    reindexed,
    run_sql,
    set_status,
    unique_word,
)
from tests.world import HARNESS_AGENT, the_project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


class Agents:
    """The agents of one test. Who an agent is comes from its key, so each name given here is a
    key of its own (the name with a word that only this test uses), connected the first time it is
    asked for: `await agents("ann")` is a client of that agent, and `agents.named("ann")` is the
    name the history and the claims say."""

    def __init__(self, stack: AsyncExitStack) -> None:
        self.word = unique_word()
        self._stack = stack
        self._clients: dict[str, Client] = {}

    def named(self, name: str) -> str:
        return f"{name}-{self.word}"

    async def __call__(self, name: str) -> Client:
        if name not in self._clients:
            self._clients[name] = await self._stack.enter_async_context(
                connect_as(self.named(name))
            )
        return self._clients[name]


@pytest.fixture
async def agents() -> AsyncIterator[Agents]:
    async with AsyncExitStack() as stack:
        yield Agents(stack)


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
        parent=epic["ref"],
        **story_fields,
    )
    made = [
        await ok(
            client,
            "create_node",
            actor="marie",
            kind="task",
            title=f"Task {number}",
            parent=story["ref"],
        )
        for number in range(tasks)
    ]
    return epic, story, made


async def take(agent: Client, **arguments: Any) -> dict[str, Any]:
    return await ok(agent, "next_work", **arguments)


async def get(client: Client, node: dict[str, Any]) -> dict[str, Any]:
    return await ok(client, "get_node", node=node["ref"])


async def history(client: Client, node: dict[str, Any]) -> list[dict[str, Any]]:
    return list((await ok(client, "node_history", node=node["ref"], limit=200))["items"])


def ids(items: list[dict[str, Any]]) -> list[int]:
    """The ids of rules, decisions, facts and events."""
    return [item["id"] for item in items]


def refs(items: list[dict[str, Any]]) -> list[str]:
    """The references of epics, stories and tasks."""
    return [item["ref"] for item in items]


def count_rows(table: str) -> int:
    result = run_sql(f"SELECT count(*) FROM {table}", plain=True, as_project=True)
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

    # Ten agents, each with a key of its own, ask at the same moment.
    word = unique_word()
    names = [f"agent-{number}-{word}" for number in range(10)]
    async with connect_each(*names) as clients:
        results = await race_agents(clients, "next_work", {"within_node": epic["ref"]})

        assert all(accepted for accepted, _ in results)
        answers = [json.loads(text) for _, text in results]
        found = [answer for answer in answers if answer["found"]]
        told_nothing = [answer for answer in answers if not answer["found"]]
        assert len(found) == 5 and len(told_nothing) == 5
        # Five different tasks, every one of them handed out, none of them twice.
        assert sorted(answer["work"]["ref"] for answer in found) == sorted(refs(tasks))
        assert len({answer["claim"]["actor"] for answer in found}) == 5
        assert {answer["claim"]["actor"] for answer in found} <= set(names)
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
            claimed = [
                event for event in await history(client, task) if event["action"] == "claimed"
            ]
            assert len(claimed) == 1
            node = await get(client, task)
            assert node["status"] == "in_progress" and node["claimed_by"] == claimed[0]["actor"]
        # Asking again changes nothing: still nothing free, and nobody gets a task twice.
        again = await race_agents(clients, "next_work", {"within_node": epic["ref"]})
        assert not any(json.loads(text)["found"] for _, text in again)
        assert all(
            "5 being worked on by someone" in json.loads(text)["message"] for _, text in again
        )


async def test_the_most_urgent_work_comes_first_and_a_story_with_open_tasks_does_not(
    client: Client, agents: Agents
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
            parent=story["ref"],
            priority=priority,
        )
        priorities[task["ref"]] = priority
    lone = await ok(
        client,
        "create_node",
        actor="marie",
        kind="story",
        title="A story with no tasks",
        parent=epic["ref"],
        priority="low",
    )

    order = []
    for number in range(5):
        answer = await take(await agents(f"agent-{number}"), within_node=epic["ref"])
        assert answer["found"]
        order.append(priorities.get(answer["work"]["ref"], "lone story"))
    # Urgent first, then high, normal and low. The lone story is low too but newer than the low
    # task. The story that has tasks is never handed out: it is worked through its tasks.
    assert order == ["urgent", "high", "normal", "low", "lone story"]
    assert (await get(client, lone))["claimed_by"] == agents.named("agent-4")
    assert (await get(client, story))["claimed_by"] is None
    assert (await take(await agents("agent-5"), within_node=epic["ref"]))["found"] is False


async def test_peeking_shows_the_work_without_taking_it(client: Client, agents: Agents) -> None:
    epic, _, (task,) = await project(client, 1)
    ann, bob = await agents("ann"), await agents("bob")

    peeked = await take(ann, within_node=epic["ref"], peek=True)

    assert peeked["found"] and peeked["peeked"] and peeked["claim"] is None
    assert peeked["work"]["ref"] == task["ref"] and "without claiming" in peeked["message"]
    assert (await get(client, task))["claimed_by"] is None
    assert [e["action"] for e in await history(client, task)] == ["create"]
    taken = await take(bob, within_node=epic["ref"])
    assert taken["work"]["ref"] == task["ref"]
    assert taken["claim"]["actor"] == agents.named("bob")
    # Looking when nothing is free says so too, and does not refuse.
    nothing = await take(ann, within_node=epic["ref"], peek=True)
    assert nothing["found"] is False and "1 being worked on by someone" in nothing["message"]


async def test_next_work_refuses_bad_asks_and_says_what_is_waiting_when_nothing_is_free(
    client: Client, agents: Agents
) -> None:
    epic, _, (first, second, third) = await project(client, 3)
    await needs_first(client, second, first)
    blocked = await get(client, third)
    await ok(
        client,
        "update_node",
        actor="marie",
        node=third["ref"],
        expected_version=blocked["version"],
        status="blocked",
    )
    await take(await agents("ann"), within_node=epic["ref"])

    nothing = await take(await agents("bob"), within_node=epic["ref"])

    assert nothing["found"] is False
    assert "1 being worked on by someone" in nothing["message"]
    assert "1 waiting for other work to be done" in nothing["message"]
    assert "1 marked blocked" in nothing["message"]
    assert "context_chars" in await refused(
        client, "next_work", within_node=epic["ref"], context_chars=999
    )
    assert "There is no epic 999999999 in this project" in await refused(
        client, "next_work", within_node="epic:999999999"
    )


async def test_the_context_holds_exactly_what_the_work_needs(client: Client) -> None:
    epic, story, (first, second) = await project(client, 2, body="The story in more words.")
    other_epic, other_story, _ = await project(client, 1)
    await needs_first(client, second, first)
    kept = await make_rule(
        client, "Invoices carry a number", body="Each has its own number.", node=story["ref"]
    )
    await make_rule(client, "A dropped rule", node=story["ref"], status="dropped")
    await make_rule(client, "A rule of another story", node=other_story["ref"])
    on_first = await make_rule(client, "The form checks the number", node=first["ref"])
    on_epic = await make_decision(client, "Decided on the epic", nodes=[epic["ref"]])
    confirm_on_board("decision", on_epic, "Marie")
    await make_decision(client, "Decided on the second task", nodes=[second["ref"]])
    await make_decision(client, "Decided elsewhere", nodes=[other_story["ref"]])
    old = await make_decision(client, "The old way", nodes=[story["ref"]])
    new = (
        await ok(
            client,
            "supersede_note",
            replaces=[old["id"]],
            text="The new way\n\nDo it the new way",
            why="It changed",
            decided_by="Marie",
        )
    )["replacement"]
    given_up = await make_decision(client, "Given up", nodes=[first["ref"]])
    await ok(client, "withdraw_note", note_ids=[given_up["id"]], reason="No need")
    free_wide = await make_decision(client, "Applies everywhere", project_wide=True)
    linked_wide = await make_decision(
        client, "Applies everywhere, and to this story", project_wide=True, nodes=[story["ref"]]
    )
    fact = await make_fact(client, "Invoices are sent as PDFs", nodes=[story["ref"]])
    parent_fact = await make_fact(client, "Customers are in Europe", nodes=[epic["ref"]])
    confirm_on_board("fact", parent_fact, "Ann")
    gone = await make_fact(client, "Invoices are sent as faxes", nodes=[story["ref"]])
    await ok(client, "withdraw_note", note_ids=[gone["id"]], reason="They are not")
    outdated = await make_fact(client, "Invoices are sent by post", nodes=[story["ref"]])
    await ok(
        client,
        "supersede_note",
        replaces=[outdated["id"]],
        text="Invoices are sent by e-mail",
        why="The owner",
        keep_links=False,
    )
    await make_fact(client, "A note about somewhere else", nodes=[other_story["ref"]])

    answer = await take(client, within_node=epic["ref"], context_chars=1_000_000)

    assert answer["work"]["ref"] == first["ref"]
    context = answer["context"]
    assert set(context) == {
        "parents",
        "acceptance_criteria",
        "definition_of_done",
        "definition_of_done_chars",
        "definition_checks",
        "asks",
        "handoffs",
        "prerequisites",
        "discovered_from",
        "notes",
        "project_wide_notes",
        "past_notes",
        "unblocks",
        "similar",
        "similar_note",
        "characters",
        "max_characters",
        "left_out",
        "cut_note",
    }
    assert [(p["kind"], p["ref"]) for p in context["parents"]] == [
        ("epic", epic["ref"]),
        ("story", story["ref"]),
    ]
    assert context["parents"][1]["body_preview"] == "The story in more words."
    # A task has its own acceptance criteria first, then its story's, each saying which item it is
    # on; a dropped one and one of another story are not there.
    assert ids(context["acceptance_criteria"]) == [on_first["id"], kept["id"]]
    assert [c["on"] for c in context["acceptance_criteria"]] == [first["ref"], story["ref"]]
    assert context["acceptance_criteria"][1]["body"] == "Each has its own number."
    # The definition of done comes whole when there is room for it (the room here is a million
    # characters), with its full length.
    assert context["definition_of_done_chars"] == len(context["definition_of_done"])

    # Notes in force that are linked to this item or its parents: the ones somebody decided first,
    # then the others, each with its status and why it is so.
    assert ids(context["notes"]) == [on_epic["id"], new["id"], fact["id"], parent_fact["id"]]
    assert all(n["status"] in ("unconfirmed", "confirmed") for n in context["notes"])
    assert all(not n["project_wide"] for n in context["notes"])
    assert [n["decided"] for n in context["notes"]] == [True, True, False, False]
    by_id = {n["id"]: n for n in context["notes"]}
    assert all("label" not in n and "confirmation" not in n for n in context["notes"])
    assert (by_id[on_epic["id"]]["status"], by_id[on_epic["id"]]["confirmed_by"]) == (
        "confirmed",
        "Marie",
    )
    assert by_id[on_epic["id"]]["linked_to"] == [epic["ref"]]
    assert by_id[on_epic["id"]]["decided_by"] == "Marie"
    assert (by_id[new["id"]]["status"], by_id[new["id"]]["confirmed_by"]) == (
        "unconfirmed",
        None,
    )
    assert by_id[new["id"]]["linked_to"] == [story["ref"]]
    for field in ("text", "why", "decided_by", "decided_on"):
        assert by_id[new["id"]][field] == new[field]
    assert [by_id[i]["text"] for i in (fact["id"], parent_fact["id"])] == [
        "Invoices are sent as PDFs",
        "Customers are in Europe",
    ]
    assert (by_id[fact["id"]]["status"], by_id[fact["id"]]["confirmed_by"]) == (
        "unconfirmed",
        None,
    )
    assert by_id[fact["id"]]["linked_to"] == [story["ref"]]
    assert by_id[fact["id"]]["why"] == "The team call"
    assert (
        by_id[parent_fact["id"]]["status"],
        by_id[parent_fact["id"]]["confirmed_by"],
    ) == ("confirmed", "Ann")
    assert by_id[parent_fact["id"]]["linked_to"] == [epic["ref"]]

    # What is no longer in force is shown apart, with its state, so it cannot be mistaken.
    assert ids(context["past_notes"]) == sorted(
        [old["id"], given_up["id"], gone["id"], outdated["id"]]
    )
    past = {n["id"]: n for n in context["past_notes"]}
    assert (past[old["id"]]["status"], past[old["id"]]["superseded_by_id"]) == (
        "superseded",
        new["id"],
    )
    assert (past[given_up["id"]]["status"], past[given_up["id"]]["superseded_by_id"]) == (
        "withdrawn",
        None,
    )
    assert past[given_up["id"]]["status_reason"] == "No need"
    assert past[given_up["id"]]["decided_by"] == "Marie"
    assert past[gone["id"]]["status"] == "withdrawn"
    assert past[outdated["id"]]["status"] == "superseded"

    # Every project-wide note in force, wherever it is linked and also when it is not.
    every_wide = await ok(client, "list_notes", project_wide=True, status="in_force", limit=200)
    assert every_wide["next_after_id"] is None
    assert set(ids(context["project_wide_notes"])) == set(ids(every_wide["items"]))
    wide = {n["id"]: n for n in context["project_wide_notes"]}
    assert {free_wide["id"], linked_wide["id"]} <= set(wide)
    assert wide[free_wide["id"]]["linked_to"] == []
    assert wide[linked_wide["id"]]["linked_to"] == [story["ref"]]
    assert all(
        n["project_wide"] and n["status"] in ("unconfirmed", "confirmed") for n in wide.values()
    )
    assert wide[free_wide["id"]]["status"] == "unconfirmed"
    assert wide[free_wide["id"]]["decided_by"] == "Marie"
    # The ones somebody decided among them come first.
    flags = [n["decided"] for n in context["project_wide_notes"]]
    assert flags == sorted(flags, reverse=True)
    assert not set(wide) & set(by_id) and not set(wide) & set(past)

    assert [(d["node"]["ref"], d["becomes_ready_when_done"]) for d in context["unblocks"]] == [
        (second["ref"], True)
    ]
    assert context["prerequisites"] == [] and context["handoffs"] == []
    assert context["left_out"] == {} and context["cut_note"] is None
    assert context["characters"] <= context["max_characters"]
    assert other_epic["ref"] not in [p["ref"] for p in context["parents"]]


async def test_a_project_wide_decision_is_in_every_briefing_until_it_ends(client: Client) -> None:
    epic_a, story_a, _ = await project(client, 1)
    epic_b, _, _ = await project(client, 1)
    wide = await make_decision(client, "Applies to all the work", project_wide=True)

    async def briefing(epic: dict[str, Any]) -> dict[str, Any]:
        answer = await take(client, within_node=epic["ref"], peek=True)
        assert answer["found"] and answer["peeked"] and answer["claim"] is None
        return dict(answer["context"])

    for epic in (epic_a, epic_b):
        context = await briefing(epic)
        assert wide["id"] in ids(context["project_wide_notes"])
        assert wide["id"] not in ids(context["notes"])

    # What a person confirms is shown as confirmed in the very next briefing.
    confirm_on_board("decision", wide, "Marie")
    shown = {n["id"]: n for n in (await briefing(epic_b))["project_wide_notes"]}
    assert (shown[wide["id"]]["status"], shown[wide["id"]]["confirmed_by"]) == (
        "confirmed",
        "Marie",
    )

    # Replaced (by the person's word), it stays project-wide: the new one is in, the old is not.
    newer = (
        await ok(
            client,
            "supersede_note",
            replaces=[wide["id"]],
            text="Applies to all the work, differently\n\nt",
            why="r",
            decided_by="Marie",
            said_by="Marie",
            quote="Changed it",
        )
    )["replacement"]
    for epic in (epic_a, epic_b):
        context = await briefing(epic)
        assert newer["id"] in ids(context["project_wide_notes"])
        assert wide["id"] not in ids(context["project_wide_notes"])
        assert wide["id"] not in ids(context["past_notes"])

    # One that is linked to this very story and then withdrawn is a past note there, and in
    # force nowhere.
    linked = await make_decision(
        client, "Applies everywhere and here", project_wide=True, nodes=[story_a["ref"]]
    )
    await ok(client, "withdraw_note", note_ids=[linked["id"]], reason="Not any more")
    for epic in (epic_a, epic_b):
        context = await briefing(epic)
        assert linked["id"] not in ids(context["project_wide_notes"])
    assert linked["id"] in ids((await briefing(epic_a))["past_notes"])
    assert linked["id"] not in ids((await briefing(epic_b))["past_notes"])
    withdrawn = await ok(client, "get_note", note_id=linked["id"])
    assert (withdrawn["status"], withdrawn["project_wide"]) == ("withdrawn", True)


async def test_the_context_keeps_to_its_size_and_says_what_it_left_out(client: Client) -> None:
    epic, _, (first, second) = await project(client, 2)
    await needs_first(client, second, first)
    story_ref = first["parent"]
    for number in range(30):
        await make_rule(client, f"Rule {number}", body="Detail. " * 90, node=story_ref)

    small = await take(client, within_node=epic["ref"], peek=True, context_chars=3000)
    large = await take(client, within_node=epic["ref"], peek=True, context_chars=1_000_000)

    context = small["context"]
    assert small["work"]["title"] == "Task 0"
    assert context["characters"] <= 3000 and context["max_characters"] == 3000
    assert 0 < len(context["acceptance_criteria"]) < 30
    assert context["left_out"]["acceptance_criteria"] == 30 - len(context["acceptance_criteria"])
    assert "left out" in context["cut_note"] and "larger context_chars" in context["cut_note"]
    # What is cut is the end: the item that waits for this one comes after the acceptance
    # criteria, so it is cut as well, and counted.
    assert context["unblocks"] == [] and context["left_out"]["unblocks"] == 1
    assert len(large["context"]["acceptance_criteria"]) == 30
    assert large["context"]["left_out"] == {}
    assert refs([d["node"] for d in large["context"]["unblocks"]]) == [second["ref"]]


# -- finish_work and the handoff note ------------------------------------------------------------


async def test_finishing_needs_a_note_and_hands_it_to_the_next_agent(
    client: Client, agents: Agents
) -> None:
    epic, _, (first, second) = await project(client, 2)
    await needs_first(client, second, first)
    ann, bob = await agents("ann"), await agents("bob")
    assert (await take(ann, within_node=epic["ref"]))["work"]["ref"] == first["ref"]
    before = await get(client, first)
    events_before = await history(client, first)

    for blank in ("", "   ", "\n\t "):
        message = await refused(ann, "finish_work", node=first["ref"], outcome="done", note=blank)
        assert "A handoff note is needed" in message
    assert "outcome must be one of" in await refused(
        ann, "finish_work", node=first["ref"], outcome="finished", note="x"
    )
    assert f"claimed by {agents.named('ann')}, not by you" in await refused(
        bob, "finish_work", node=first["ref"], outcome="done", note="Mine now"
    )
    # None of that changed anything.
    assert await get(client, first) == before
    assert await history(client, first) == events_before
    assert (await get(client, second))["status"] == "new"

    finished = await ok(
        ann,
        "finish_work",
        node=first["ref"],
        outcome="done",
        note="Used the invoice library; the numbering is in numbering.py.",
    )

    assert finished["node"]["status"] == "done" and finished["node"]["claimed_by"] is None
    assert finished["handoff"]["actor"] == agents.named("ann")
    assert finished["handoff"]["outcome"] == "done"
    assert [(n["ref"], n["status"]) for n in finished["unblocked"]] == [(second["ref"], "ready")]
    done = await get(client, first)
    assert done["handoff_count"] == 1
    assert done["last_handoff"]["note"].startswith("Used the invoice library")
    assert [e["action"] for e in await history(client, first)][-3:] == [
        "handoff",
        "claim_released",
        "update",
    ]

    nxt = await take(bob, within_node=epic["ref"])
    assert nxt["work"]["ref"] == second["ref"]
    (prerequisite,) = nxt["context"]["prerequisites"]
    assert prerequisite["node"]["ref"] == first["ref"]
    assert prerequisite["handoffs"][0]["note"].startswith("Used the invoice library")
    assert prerequisite["handoffs"][0]["actor"] == agents.named("ann")


async def test_every_way_of_leaving_work_leaves_a_note_and_a_status(
    client: Client, agents: Agents
) -> None:
    epic, story, tasks = await project(client, 4)
    ann, bob = await agents("ann"), await agents("bob")
    outcomes = [
        ("blocked", "blocked"),
        ("dropped", "dropped"),
        ("done", "done"),
        ("released", "ready"),
    ]
    for task, (outcome, status) in zip(tasks, outcomes, strict=True):
        taken = await take(ann, within_node=story["ref"])
        assert taken["work"]["ref"] == task["ref"]
        finished = await ok(
            ann,
            "finish_work",
            node=task["ref"],
            outcome=outcome,
            note=f"Left it {outcome}.",
        )
        assert finished["node"]["status"] == status, outcome
        assert finished["handoff"]["outcome"] == outcome
    # Released work is free again and carries its note to the next agent.
    again = await take(bob, within_node=epic["ref"])
    assert again["work"]["ref"] == tasks[3]["ref"]
    assert [h["note"] for h in again["context"]["handoffs"]] == ["Left it released."]


async def test_releasing_a_claim_needs_a_note_too_and_keeps_it(
    client: Client, agents: Agents
) -> None:
    epic, _, (task,) = await project(client, 1)
    ann, bob = await agents("ann"), await agents("bob")
    await take(ann, within_node=epic["ref"])

    assert "A handoff note is needed" in await refused(
        ann, "release_claim", node=task["ref"], note="  "
    )
    assert (await get(client, task))["claimed_by"] == agents.named("ann")
    await ok(
        ann,
        "release_claim",
        node=task["ref"],
        note="Stopped halfway: the form is built, the sending is not.",
    )

    node = await get(client, task)
    assert node["claimed_by"] is None and node["handoff_count"] == 1
    assert node["last_handoff"]["outcome"] == "released"
    assert node["status"] == "ready"
    answer = await take(bob, within_node=epic["ref"])
    assert answer["work"]["ref"] == task["ref"]
    assert answer["context"]["handoffs"][0]["note"].startswith("Stopped halfway")


async def test_work_left_to_lapse_shows_as_lapsed_with_no_handoff_and_can_be_taken_over(
    client: Client, agents: Agents
) -> None:
    epic, _, (task,) = await project(client, 1)
    ann, bob = await agents("ann"), await agents("bob")
    await take(ann, within_node=epic["ref"], lease_seconds=2)
    await anyio.sleep(2.6)

    lapsed = await get(client, task)
    assert lapsed["claimed_by"] is None and lapsed["claim_expires_at"] is None
    assert lapsed["lapsed_claim_by"] == agents.named("ann")
    assert lapsed["lapsed_claim_at"] is not None
    assert lapsed["status"] == "in_progress"
    assert lapsed["handoff_count"] == 0 and lapsed["last_handoff"] is None

    taken = await take(bob, within_node=epic["ref"])
    assert taken["work"]["ref"] == task["ref"] and taken["claim"]["actor"] == agents.named("bob")
    assert taken["taken_over_from"] == agents.named("ann")
    assert f"{agents.named('ann')} claimed it earlier" in taken["message"]
    held = await get(client, task)
    assert held["claimed_by"] == agents.named("bob") and held["lapsed_claim_by"] is None
    assert f"claimed by {agents.named('bob')}, not by you" in await refused(
        ann, "finish_work", node=task["ref"], outcome="done", note="Done"
    )


async def test_the_one_who_let_a_claim_lapse_can_still_finish_until_someone_takes_over(
    client: Client, agents: Agents
) -> None:
    epic, _, (task,) = await project(client, 1)
    ann = await agents("ann")
    await take(ann, within_node=epic["ref"], lease_seconds=2)
    await anyio.sleep(2.6)

    finished = await ok(
        ann,
        "finish_work",
        node=task["ref"],
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
            "INSERT INTO events (node, action, actor, actor_kind, after) "
            f"VALUES ('{task['ref']}', 'handoff', 'someone', 'agent', '{after}')"
        )
        assert result.returncode != 0
        assert "handoff_has_an_outcome_and_a_note" in result.stderr

    assert count_rows("events") == before


# -- create_plan ---------------------------------------------------------------------------------


def a_plan() -> dict[str, Any]:
    """1 epic, 3 stories, 8 tasks, 5 links and 5 acceptance criteria (rules)."""
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
        {"title": "Every invoice has a number", "node": "s0", "status": "met"},
        {"title": "Totals are in euros", "node": "s0"},
        {"title": "Credit notes keep the old number", "node": "s1", "body": "See finance."},
        {"title": "Shown on the paper copy", "node": "t0"},
        {"title": "Not placed yet"},
    ]
    return {"items": items, "links": links, "rules": rules}


async def test_a_plan_creates_everything_in_one_call(client: Client) -> None:
    plan = a_plan()

    result = await ok(client, "create_plan", actor="ann", **plan)

    assert result["counts"] == {"epic": 1, "story": 3, "task": 8, "links": 5, "rules": 5}
    assert [item["key"] for item in result["created"]] == [i["key"] for i in plan["items"]]
    new = {item["key"]: item for item in result["created"]}
    assert len(result["link_ids"]) == 5 and len(result["rule_ids"]) == 5
    # Each new item is named by its kind and its own number, and the stories and the tasks of
    # the plan are numbered in the order the plan gave them.
    assert all(item["ref"].startswith(f"{item['kind']}:") for item in result["created"])
    numbers: dict[str, list[int]] = {kind: [] for kind in ("epic", "story", "task")}
    for item in result["created"]:
        numbers[item["kind"]].append(int(item["ref"].partition(":")[2]))
    assert all(
        found == sorted(found) and len(set(found)) == len(found) for found in numbers.values()
    )
    epic_ref = new["epic"]["ref"]
    stories = await all_nodes(client, parent=epic_ref)
    assert refs(stories) == [new[f"s{n}"]["ref"] for n in range(3)]
    # Each task is in the story the plan named, and the order and dates were kept.
    for number in range(8):
        task = await ok(client, "get_node", node=new[f"t{number}"]["ref"])
        assert task["parent"] == new[f"s{number % 3}"]["ref"]
        assert task["priority"] == ("high" if number == 0 else "normal")
        assert task["target_on"] == day(30)
    # The links are real: t3 waits for t0, t6 for t3, and so t6 is not ready.
    t3 = await ok(client, "get_node", node=new["t3"]["ref"])
    assert t3["waiting_on"] == [new["t0"]["ref"]]
    t6 = await ok(client, "get_node", node=new["t6"]["ref"])
    assert t6["waiting_on"] == [new["t3"]["ref"]]
    relates = await ok(client, "list_links", node=new["s1"]["ref"], link_type="relates_to")
    assert relates["total"] == 1
    # The acceptance criteria are on their stories and tasks, and the last one is unplaced.
    first_story_rules = await ok(client, "list_rules", node=new["s0"]["ref"])
    assert [r["status"] for r in first_story_rules["items"]] == ["met", "open"]
    assert [r["id"] for r in first_story_rules["items"]] == result["rule_ids"][:2]
    on_a_task = await ok(client, "get_rule", rule_id=result["rule_ids"][3])
    assert on_a_task["node"] == new["t0"]["ref"]
    assert (await ok(client, "get_node", node=new["t0"]["ref"]))["criteria_total"] == 1
    loose = await ok(client, "get_rule", rule_id=result["rule_ids"][4])
    assert loose["node"] is None
    # Everything is in the history with its actor.
    for item in result["created"]:
        created = [e for e in await history(client, item) if e["action"] == "create"]
        assert [e["actor"] for e in created] == [HARNESS_AGENT]


async def test_a_plan_can_hang_from_what_already_exists(client: Client) -> None:
    epic, story, (task,) = await project(client, 1)

    result = await ok(
        client,
        "create_plan",
        actor="ann",
        items=[
            {"key": "extra", "kind": "task", "title": "Extra task", "parent": story["ref"]},
            {"key": "later", "kind": "story", "title": "Later story", "parent": epic["ref"]},
        ],
        links=[{"source": "extra", "target": task["ref"], "link_type": "needs_first"}],
        rules=[
            {"title": "A rule for the old story", "node": story["ref"]},
            {"title": "A rule for the old task", "node": task["ref"]},
        ],
    )

    extra = result["created"][0]
    node = await ok(client, "get_node", node=extra["ref"])
    assert node["parent"] == story["ref"] and node["waiting_on"] == [task["ref"]]
    assert (await ok(client, "get_rule", rule_id=result["rule_ids"][0]))["node"] == story["ref"]
    assert (await ok(client, "get_rule", rule_id=result["rule_ids"][1]))["node"] == task["ref"]


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
                "parent": story["ref"],
                "priority": "now",
            },
            {
                "key": "h",
                "kind": "task",
                "title": "Dates the wrong way",
                "parent": story["ref"],
                "start_on": day(9),
                "target_on": day(1),
            },
            {
                "key": "i",
                "kind": "task",
                "title": "Parent that is not there",
                "parent": "story:999999999",
            },
            {"key": "j", "kind": "epic", "title": "An epic with a parent", "parent": epic["ref"]},
            {"key": "story:12", "kind": "story", "title": "A key like a ref", "parent": "a"},
            {"key": "k", "kind": "task", "title": "Under an old epic", "parent": epic["ref"]},
        ],
        links=[
            {"source": "a", "target": "nowhere", "link_type": "needs_first"},
            {"source": "a", "target": "a", "link_type": "relates_to"},
            {"source": "a", "target": task["ref"], "link_type": "friends_with"},
            {"source": "task:999999999", "target": task["ref"], "link_type": "relates_to"},
            # The plan makes this a circle with a link that exists already.
            {"source": task["ref"], "target": other["ref"], "link_type": "needs_first"},
        ],
        rules=[
            {"title": "Rule for nowhere", "node": "missing"},
            {"title": " "},
            {"title": "Rule for an epic", "node": epic["ref"]},
        ],
    )

    problems = [line for line in message.splitlines() if re.match(r"\d+\. ", line)]
    assert "The plan was not created, because of" in message
    assert "Nothing was created" in message
    for expected in (
        "is used by more than one item",
        "the key looks like the reference of an existing item",
        "kind must be one of",
        "title cannot be empty",
        "needs a parent story",
        "belongs under a story",
        f"{epic['ref']} is an epic, but a task belongs under a story",
        "there is no task 999999999 in this project for the source",
        f"{epic['ref']} is an epic, and an acceptance criterion belongs to a story or a task",
        "is not the key of any item in this plan",
        "Item 8 (key 'g'",
        "priority must be one of",
        "Item 9 (key 'h'",
        "there is no story 999999999 in this project",
        "an epic has no parent",
        "nowhere",
        "link_type must be one of",
        "cannot be linked to itself",
        "circle of waiting",
        "Acceptance criterion 1",
        "Acceptance criterion 2",
        "Acceptance criterion 3",
    ):
        assert any(expected in problem for problem in problems), (expected, message)
    assert len(problems) >= 18
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
    epic_ref = result["created"][0]["ref"]
    page = await ok(client, "list_nodes", parent=epic_ref, limit=200)
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


async def test_five_changes_with_one_stale_apply_four_and_refuse_one(
    client: Client, agents: Agents
) -> None:
    _, story, tasks = await project(client, 5)
    versions = {task["ref"]: task["version"] for task in tasks}
    ann, bob = await agents("ann"), await agents("bob")
    # Someone else changes the third task after it was read.
    third = tasks[2]
    await ok(
        bob,
        "update_node",
        node=third["ref"],
        expected_version=1,
        title="Changed under you",
    )

    result = await ok(
        ann,
        "update_nodes",
        changes=[
            {"node": task["ref"], "expected_version": versions[task["ref"]], "priority": "high"}
            for task in tasks
        ],
    )

    assert (result["applied"], result["refused"]) == (4, 1)
    assert [r["node"] for r in result["results"]] == refs(tasks)
    assert [r["applied"] for r in result["results"]] == [True, True, False, True, True]
    stale = result["results"][2]
    assert (
        stale["version"] is None and "is at version 2, but you sent version 1" in stale["refusal"]
    )
    for task, report in zip(tasks, result["results"], strict=True):
        events = await history(client, task)
        mine = [e for e in events if e["actor"] == agents.named("ann")]
        node = await get(client, task)
        if report["applied"]:
            assert [e["action"] for e in mine] == ["update"]
            assert mine[0]["after"] == {"priority": "high", "version": 2}
            assert (node["priority"], node["version"]) == ("high", 2)
            assert report["version"] == 2
        else:
            assert mine == [] and node["priority"] == "normal"
            assert node["title"] == "Changed under you"
    assert story["ref"]


async def test_a_hundred_and_twenty_changes_report_each_one(client: Client) -> None:
    _, _, tasks = await project(client, 120)
    changes: list[dict[str, Any]] = []
    for number, task in enumerate(tasks):
        change: dict[str, Any] = {
            "node": task["ref"],
            "expected_version": 1,
            "title": f"Renamed {number}",
        }
        if number % 10 == 0:
            change["status"] = "finished"  # not a status: refused
        changes.append(change)
    changes.append({"node": "task:999999999", "expected_version": 1, "title": "Nobody"})

    result = await ok(client, "update_nodes", actor="ann", changes=changes)

    assert len(result["results"]) == 121
    assert (result["applied"], result["refused"]) == (108, 13)
    for number, report in enumerate(result["results"][:120]):
        assert report["node"] == tasks[number]["ref"]
        assert report["applied"] is (number % 10 != 0)
        assert (report["refusal"] is None) is report["applied"]
    assert "status must be one of" in result["results"][0]["refusal"]
    assert "There is no task 999999999 in this project" in result["results"][120]["refusal"]
    assert (await get(client, tasks[1]))["title"] == "Renamed 1"
    assert (await get(client, tasks[0]))["title"] == "Task 0"
    assert "nothing to change" in await refused(client, "update_nodes", actor="ann", changes=[])


# -- changes_since -------------------------------------------------------------------------------


async def event_ids_after(first: int) -> list[int]:
    result = run_sql(
        f"SELECT id FROM events WHERE id > {first} ORDER BY id", plain=True, as_project=True
    )
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
    await make_rule(client, "A rule", node=story["ref"])
    await make_decision(client, "A decision", nodes=[epic["ref"]])
    await make_fact(client, "A fact", nodes=[story["ref"]])
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
    await ok(client, "claim_node", actor="cy", node=tasks[2]["ref"])
    after_that = await event_ids_after(expected[-1])
    assert len(after_that) == 1
    await caught_up(client, after_that[0])
    assert (await feed(client, expected[-1], 5))[0] == after_that


async def test_the_feed_can_be_narrowed_to_one_item_or_one_actor(client: Client) -> None:
    start = (await ok(client, "changes_since", after_event_id=0, limit=1))["latest_event_id"]
    _, story, (task,) = await project(client, 1)
    rule = await make_rule(client, "A rule", node=story["ref"])
    decision = await make_decision(client, "A decision", nodes=[task["ref"]])
    fact = await make_fact(client, "A fact", nodes=[task["ref"]])
    zed, xi = f"zed-{unique_word()}", f"xi-{unique_word()}"
    async with connect_as(zed) as by_zed_client:
        await ok(by_zed_client, "update_node", node=task["ref"], expected_version=1, title="New")
    await caught_up(client, (await event_ids_after(start))[-1])

    by_zed = await ok(client, "changes_since", after_event_id=start, actor=zed)
    assert [(e["action"], e["actor"]) for e in by_zed["items"]] == [("update", zed)]
    for filters, action in (
        ({"rule_id": rule["id"]}, "rule_created"),
        ({"note_id": decision["id"]}, "note_recorded"),
        ({"note_id": fact["id"]}, "note_recorded"),
    ):
        page = await ok(client, "changes_since", after_event_id=start, **filters)
        actions = [e["action"] for e in page["items"]]
        assert actions[0] == action and page["items"]
        assert all(e["actor"] != zed for e in page["items"])
    # One note's feed holds everything done to that note and nothing done to another. A person
    # confirms it on the board, and an agent that heard them say it is wrong withdraws it.
    confirmed = confirm_on_board("fact", fact, "Marie")
    person = the_project().owner.name
    other_fact = await make_fact(client, "Another note", nodes=[task["ref"]])
    async with connect_as(xi) as by_xi:
        await ok(
            by_xi,
            "withdraw_note",
            note_ids=[fact["id"]],
            reason="Wrong",
            said_by="Marie",
            quote="It is wrong",
        )
    assert confirmed["confirmed_by"] == "Marie"
    await caught_up(client, (await event_ids_after(start))[-1])
    of_note = await ok(client, "changes_since", after_event_id=start, note_id=fact["id"])
    assert [(e["action"], e["actor"]) for e in of_note["items"]] == [
        ("note_recorded", HARNESS_AGENT),
        ("note_linked", HARNESS_AGENT),
        ("note_confirmed", person),
        ("note_withdrawn", xi),
    ]
    assert all(e["note_id"] == fact["id"] for e in of_note["items"])
    assert other_fact["id"] not in {e["note_id"] for e in of_note["items"]}
    of_decision = await ok(client, "changes_since", after_event_id=start, note_id=decision["id"])
    assert all(e["note_id"] == decision["id"] for e in of_decision["items"])
    # The note's link to the task is in the task's feed.
    on_task = await ok(client, "changes_since", after_event_id=start, node=task["ref"])
    assert {"create", "update", "note_linked"} <= {e["action"] for e in on_task["items"]}
    assert all(task["ref"] in (e["node"], e["other_node"]) for e in on_task["items"])
    assert "at most one of" in await refused(
        client, "changes_since", node=task["ref"], rule_id=rule["id"]
    )
    assert "at most one of" in await refused(
        client, "changes_since", note_id=decision["id"], rule_id=rule["id"]
    )
    assert "There is no note with id 987654321" in await refused(
        client, "changes_since", note_id=987654321
    )
    assert "no event with id 999999999" in await refused(
        client, "changes_since", after_event_id=999999999
    )
    assert "There is no task 999999999 in this project" in await refused(
        client, "changes_since", node="task:999999999"
    )
    assert "0 or more" in await refused(client, "changes_since", after_event_id=-1)


async def test_the_newest_changes_can_be_read_and_walked_back_without_reading_them_all(
    client: Client,
) -> None:
    start = (await ok(client, "changes_since", after_event_id=0, limit=1))["latest_event_id"]
    _, story, tasks = await project(client, 4)
    await set_status(client, tasks[0], "blocked")
    await make_rule(client, "A rule", node=story["ref"])
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
        client, "changes_since", node=tasks[0]["ref"], newest=True, limit=50, after_event_id=start
    )
    assert for_task["next_before_id"] is None
    assert all(tasks[0]["ref"] in (e["node"], e["other_node"]) for e in for_task["items"])
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
            "INSERT INTO events (node, action, actor, actor_kind, after) "
            f"VALUES ('{task['ref']}', 'update', '{actor}', 'agent', '{{\"title\": \"{actor}\"}}')"
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
        run_sql(
            f"SELECT id FROM events WHERE actor = '{slow}'", plain=True, as_project=True
        ).stdout.strip()
    )
    began_later = run_sql(
        f"SELECT (SELECT xid FROM events WHERE id = {slow_id}) "
        f"> (SELECT xid FROM events WHERE id = {fast_id})",
        plain=True,
        as_project=True,
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
        rules=[{"title": f"Rule {n}", "body": body, "node": "s0"} for n in range(50)],
    )
    epic_ref = result["created"][0]["ref"]

    compact = await ok(client, "list_nodes", parent=epic_ref, limit=50)
    full = await ok(client, "list_nodes", parent=epic_ref, limit=50, detail=True)

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

    story_ref = result["created"][1]["ref"]
    rules = await ok(client, "list_rules", node=story_ref, limit=50)
    rules_full = await ok(client, "list_rules", node=story_ref, limit=50, detail=True)
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
        parent=epic["ref"],
    )
    assert existing["similar"] == [] and existing["similar_checked"] is True
    await reindexed(client)

    again = await ok(
        client, "create_node", actor="ann", kind="story", title=title, parent=epic["ref"]
    )

    assert again["ref"] != existing["ref"] and again["version"] == 1
    assert again["similar_checked"] is True and again["similar_note"] is None
    (match,) = again["similar"]
    assert (match["item_type"], match["ref"]) == ("story", existing["ref"])
    assert match["suggestion"] is True and match["similarity"] >= 0.55
    # It was created, and nothing was linked or merged.
    assert (await get(client, again))["ref"] == again["ref"]
    assert (await ok(client, "list_links", node=again["ref"]))["total"] == 0
    # Something unrelated is not flagged, and neither is a task (tasks are not checked).
    other = await ok(
        client,
        "create_node",
        actor="ann",
        kind="story",
        title=distinct(),
        parent=epic["ref"],
    )
    assert other["similar"] == [] and other["similar_checked"] is True
    task = await ok(
        client, "create_node", actor="ann", kind="task", title=title, parent=other["ref"]
    )
    assert task["similar"] == [] and task["similar_checked"] is False


async def test_an_epic_that_repeats_an_existing_epic_is_flagged_too(client: Client) -> None:
    title = distinct()
    first = await ok(client, "create_node", actor="marie", kind="epic", title=title)
    await reindexed(client)

    second = await ok(client, "create_node", actor="marie", kind="epic", title=title)

    assert [(m["item_type"], m["ref"]) for m in second["similar"]] == [("epic", first["ref"])]


async def test_with_the_embedding_service_stopped_creating_still_works_and_says_it_did_not_check(
    client: Client,
) -> None:
    title = distinct()
    epic = await ok(client, "create_node", actor="marie", kind="epic", title="Invoicing")
    await ok(client, "create_node", actor="marie", kind="story", title=title, parent=epic["ref"])
    await reindexed(client)

    with embeddings_stopped():
        story = await ok(
            client, "create_node", actor="ann", kind="story", title=title, parent=epic["ref"]
        )
        planned = await ok(
            client,
            "create_plan",
            actor="ann",
            items=[{"key": "s", "kind": "story", "title": title, "parent": epic["ref"]}],
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
        client, "create_node", actor="marie", kind="story", title=title, parent=epic["ref"]
    )
    await reindexed(client)

    result = await ok(
        client,
        "create_plan",
        actor="ann",
        items=[
            {"key": "s", "kind": "story", "title": title, "parent": epic["ref"]},
            {
                "key": "u",
                "kind": "story",
                "title": distinct(),
                "parent": epic["ref"],
            },
            {"key": "t", "kind": "task", "title": title, "parent": "s"},
        ],
    )

    assert result["similar_note"] is None
    (flagged,) = result["similar"]
    created = {item["key"]: item["ref"] for item in result["created"]}
    assert flagged["key"] == "s" and flagged["ref"] == created["s"]
    assert [(m["item_type"], m["ref"]) for m in flagged["similar"]] == [("story", existing["ref"])]


# -- what the server tells an agent when it connects ----------------------------------------------


async def test_the_server_instructions_name_only_tools_that_exist(client: Client) -> None:
    instructions = client.instructions
    assert instructions
    tools = {tool.name for tool in (await client.list_tools()).tools}
    # The instructions also tell a key that runs code what it is given instead: a key that calls
    # one tool at a time is never offered these two (tests/test_code_mode_integration.py).
    tools |= {"explore", "run"}

    named = set(re.findall(r"`([a-z_]+)`", instructions))

    assert named, "the instructions should name the tools of the working loop"
    assert named <= tools, f"names no tool: {sorted(named - tools)}"
    assert {"next_work", "recall", "add_note", "finish_work"} <= named
    # The working loop in a few lines: it has to stay short enough that an agent reads all of it.
    assert len(instructions.split()) < 300
