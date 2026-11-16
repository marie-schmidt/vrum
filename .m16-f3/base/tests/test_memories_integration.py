"""M4A against the running containers: facts, beliefs, evidence, staleness, replacing and
retracting, confirming, promoting, and one recall of everything known, through a real MCP client
(see ./check.sh)."""

from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    make_belief,
    make_decision,
    make_fact,
    make_rule,
    make_stories,
    make_tree,
    ok,
    race_mixed,
    race_results,
    refused,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def ids(items: list[dict[str, Any]]) -> list[int]:
    return [item["id"] for item in items]


async def recall(client: Client, **arguments: Any) -> dict[str, Any]:
    return await ok(client, "recall", **arguments)


def entries(section: dict[str, Any], what: str) -> list[dict[str, Any]]:
    """The facts or beliefs (`what` is 'memory') or decisions ('decision') in a recall section."""
    return [entry[what] for entry in section["items"]]


async def history(client: Client, memory: dict[str, Any]) -> list[dict[str, Any]]:
    return list((await ok(client, "memory_history", memory_id=memory["id"]))["items"])


async def every(client: Client, **filters: Any) -> tuple[list[dict[str, Any]], int]:
    """Every fact and belief matching the filters, in pages, with the total the first page gave."""
    found: list[dict[str, Any]] = []
    after_id = None
    total = None
    while True:
        page = await ok(client, "list_memories", limit=25, after_id=after_id, **filters)
        total = page["total"] if total is None else total
        found.extend(page["items"])
        after_id = page["next_after_id"]
        if after_id is None:
            return found, total


async def retract(client: Client, *memories: dict[str, Any], **arguments: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "actor": "agent-1",
        "memory_ids": [memory["id"] for memory in memories],
        "reason": "It was wrong",
    }
    return await ok(client, "retract_memory", **{**fields, **arguments})


async def get(client: Client, memory: dict[str, Any]) -> dict[str, Any]:
    return await ok(client, "get_memory", memory_id=memory["id"])


async def test_a_fact_is_retained_with_its_source_and_what_it_concerns(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, story_id=story["id"])
    decision = await make_decision(client, "Keep one database")

    fact = await ok(
        client,
        "retain_fact",
        actor="agent-1",
        text="The build takes four minutes.",
        source="Timed it on Marie's laptop",
        kind="experience",
        happened_on="2026-11-01",
        node_ids=[story["id"]],
        rule_ids=[rule["id"]],
        decision_ids=[decision["id"]],
    )

    assert fact["type"] == "fact" and fact["kind"] == "experience"
    assert fact["text"] == "The build takes four minutes."
    assert fact["source"] == "Timed it on Marie's laptop"
    assert (fact["origin"], fact["status"], fact["recorded_by"]) == ("stated", "active", "agent-1")
    assert fact["happened_on"] == "2026-11-01" and fact["recorded_at"]
    assert "Timed it on Marie's laptop" in fact["label"]
    assert fact["node_ids"] == [story["id"]] and fact["rule_ids"] == [rule["id"]]
    assert fact["decision_ids"] == [decision["id"]]
    assert fact["evidence"] == [] and fact["stale"] is False

    assert await get(client, fact) == fact
    by_action = [(event["action"], event["actor"]) for event in await history(client, fact)]
    assert by_action == [("memory_recorded", "agent-1")] + [("memory_linked", "agent-1")] * 3

    # What it concerns shows in the histories of those things.
    for tool, key, thing in (
        ("node_history", "node_id", story),
        ("rule_history", "rule_id", rule),
        ("decision_history", "decision_id", decision),
    ):
        events = (await ok(client, tool, **{key: thing["id"]}))["items"]
        assert any(
            event["memory_id"] == fact["id"] and event["action"] == "memory_linked"
            for event in events
        ), f"{tool} does not show the fact"

    # A fact whose kind is left out is a world fact, and the day is optional.
    plain = await make_fact(client)
    assert (plain["kind"], plain["happened_on"], plain["node_ids"]) == ("world", None, [])


async def test_a_fact_that_is_blank_unsourced_or_about_nothing_is_refused(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    base: dict[str, Any] = {"actor": "agent-1", "text": "Something", "source": "Someone"}
    before = (await ok(client, "list_memories", limit=1))["total"]

    for change, expected in (
        ({"source": "  "}, "A fact needs its source"),
        ({"text": " "}, "A fact needs its text"),
        ({"kind": "rumour"}, "kind must be one of world, experience"),
        ({"happened_on": "last week"}, "happened_on must be a day written as YYYY-MM-DD"),
        ({"node_ids": [987654321]}, "There is no node with id 987654321, so the fact cannot be"),
        ({"rule_ids": [987654321]}, "There is no rule with id 987654321"),
        ({"decision_ids": [987654321]}, "There is no decision with id 987654321"),
        ({"actor": " "}, "Say who is making this change"),
    ):
        assert expected in await refused(client, "retain_fact", **{**base, **change})

    # A refused fact leaves nothing behind, not even a link made before the bad one was found.
    mixed = {**base, "node_ids": [story["id"]], "decision_ids": [987654321]}
    await refused(client, "retain_fact", **mixed)
    assert (await ok(client, "list_memories", limit=1))["total"] == before
    assert (await ok(client, "list_memories", node_id=story["id"]))["total"] == 0


async def test_a_belief_rests_on_evidence_and_is_only_a_suggestion(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    first = await make_fact(client, "The tests take an hour.", node_ids=[story["id"]])
    second = await make_fact(client, "Most of that is one suite.")
    decision = await make_decision(client, "Run tests on every push")

    belief = await ok(
        client,
        "record_belief",
        actor="agent-2",
        text="Splitting that suite would speed up every push.",
        evidence_fact_ids=[first["id"], second["id"]],
        evidence_decision_ids=[decision["id"]],
        node_ids=[story["id"]],
    )

    assert belief["type"] == "belief" and belief["kind"] is None and belief["source"] is None
    assert (belief["origin"], belief["status"], belief["recorded_by"]) == (
        "stated",
        "active",
        "agent-2",
    )
    assert "Suggestion" in belief["label"] and "not a decision" in belief["label"]
    assert belief["evidence_count"] == 3 and belief["stale"] is False
    assert [(item["type"], item["id"], item["status"]) for item in belief["evidence"]] == [
        ("fact", first["id"], "active"),
        ("fact", second["id"], "active"),
        ("decision", decision["id"], "active"),
    ]
    assert belief["confirmed_by"] is None and belief["promoted_decision_ids"] == []

    result = await recall(client, node_id=story["id"])
    assert ids(entries(result["facts"], "memory")) == [first["id"]]
    assert ids(entries(result["beliefs"], "memory")) == [belief["id"]]
    assert belief["id"] not in ids(entries(result["active_decisions"], "decision"))
    recalled = result["beliefs"]["items"][0]
    assert recalled["memory"]["label"] == belief["label"]
    assert recalled["why"] == "linked directly to this node"

    events = await history(client, belief)
    assert events[0]["action"] == "memory_recorded"
    assert events[0]["after"]["evidence"] == {
        "fact_ids": [first["id"], second["id"]],
        "decision_ids": [decision["id"]],
    }


async def test_a_belief_without_good_evidence_is_refused(client: Client) -> None:
    fact = await make_fact(client)
    other = await make_belief(client, [fact], "A belief")
    gone = await make_fact(client, "Wrong")
    await retract(client, gone)
    old_decision = await make_decision(client, "Old")
    await ok(
        client,
        "supersede_decision",
        actor="a",
        decision_id=old_decision["id"],
        title="New",
        text="t",
        reason="r",
        decided_by="Ann",
    )
    before = (await ok(client, "list_memories", limit=1))["total"]

    for arguments, expected in (
        ({}, "A belief needs evidence"),
        ({"evidence_fact_ids": [], "evidence_decision_ids": []}, "A belief needs evidence"),
        ({"evidence_fact_ids": [987654321]}, "There is no fact with id 987654321, so a belief"),
        (
            {"evidence_fact_ids": [other["id"]]},
            "a belief cannot be evidence for another belief",
        ),
        ({"evidence_fact_ids": [gone["id"]]}, "was retracted"),
        ({"evidence_fact_ids": [fact["id"], gone["id"]]}, "so it is not evidence"),
        ({"evidence_decision_ids": [987654321]}, "There is no decision with id 987654321"),
        ({"evidence_decision_ids": [old_decision["id"]]}, "so it is not evidence"),
        ({"evidence_fact_ids": [fact["id"]], "text": " "}, "A belief needs its text"),
        ({"evidence_fact_ids": [fact["id"]], "node_ids": [987654321]}, "There is no node"),
    ):
        message = await refused(
            client, "record_belief", **{"actor": "a", "text": "Because", **arguments}
        )
        assert expected in message, message

    assert (await ok(client, "list_memories", limit=1))["total"] == before


async def test_a_belief_goes_stale_when_its_evidence_is_retracted_or_superseded(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    kept = await make_fact(client, "Stays true")
    wrong = await make_fact(client, "Turns out wrong")
    outdated = await make_fact(client, "Gets replaced")
    decision = await make_decision(client, "Will change")
    belief_a = await make_belief(
        client, [kept, wrong], "Rests on one that is wrong", node_ids=[story["id"]]
    )
    belief_b = await make_belief(
        client, [kept, outdated], "Rests on one that is replaced", node_ids=[story["id"]]
    )
    belief_c = await ok(
        client,
        "record_belief",
        actor="a",
        text="Rests on a decision that changes",
        evidence_decision_ids=[decision["id"]],
        node_ids=[story["id"]],
    )
    fine = await make_belief(client, [kept], "Rests only on what holds", node_ids=[story["id"]])

    result = await recall(client, node_id=story["id"])
    assert result["stale_beliefs"]["count"] == 0 and result["beliefs"]["count"] == 4

    await retract(client, wrong, reason="The benchmark was run on the wrong branch")
    await ok(
        client,
        "supersede_memory",
        actor="a",
        replaces=[outdated["id"]],
        reason="Re-measured",
        text="Now it is true in another way",
        source="Re-measured on Friday",
    )
    await ok(
        client,
        "supersede_decision",
        actor="a",
        decision_id=decision["id"],
        title="Changed",
        text="t",
        reason="r",
        decided_by="Ann",
    )

    stale_a, stale_b, stale_c = (
        await get(client, belief_a),
        await get(client, belief_b),
        await get(client, belief_c),
    )
    assert all(item["stale"] and item["status"] == "active" for item in (stale_a, stale_b, stale_c))
    assert stale_a["stale_because"] == [
        f"fact {wrong['id']} was retracted: The benchmark was run on the wrong branch"
    ]
    assert stale_b["stale_because"][0].startswith(f"fact {outdated['id']} was superseded by fact")
    assert stale_c["stale_because"][0].startswith(
        f"decision {decision['id']} was superseded by decision"
    )
    assert [item["status"] for item in stale_a["evidence"]] == ["active", "retracted"]
    assert (await get(client, fine))["stale"] is False

    # Shown apart, never as active.
    result = await recall(client, node_id=story["id"])
    assert ids(entries(result["beliefs"], "memory")) == [fine["id"]]
    assert ids(entries(result["stale_beliefs"], "memory")) == [
        belief_a["id"],
        belief_b["id"],
        belief_c["id"],
    ]
    assert result["stale_beliefs"]["items"][0]["memory"]["stale_because"]
    listed, total = await every(client, node_id=story["id"], stale=True)
    assert ids(listed) == [belief_a["id"], belief_b["id"], belief_c["id"]] and total == 3
    listed, _ = await every(client, node_id=story["id"], stale=False)
    assert ids(listed) == [fine["id"]]


async def test_a_fact_is_never_edited_a_replaced_one_points_at_its_replacement(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, story_id=story["id"])
    old = await make_fact(
        client, "The limit is 10.", node_ids=[story["id"]], rule_ids=[rule["id"]], kind="experience"
    )

    change = await ok(
        client,
        "supersede_memory",
        actor="agent-2",
        replaces=[old["id"]],
        reason="The limit was raised",
        text="The limit is 20.",
        source="Release notes, version 4",
    )

    new = change["replacement"]
    assert change["superseded_ids"] == [old["id"]] and change["retracted_ids"] == []
    assert new["type"] == "fact" and new["status"] == "active"
    assert new["supersedes_ids"] == [old["id"]]
    assert (new["kind"], new["recorded_by"]) == ("experience", "agent-2")
    # What the old one concerned carries over.
    assert new["node_ids"] == [story["id"]] and new["rule_ids"] == [rule["id"]]
    gone = await get(client, old)
    assert (gone["status"], gone["superseded_by_id"], gone["status_reason"]) == (
        "superseded",
        new["id"],
        "The limit was raised",
    )
    assert (gone["text"], gone["source"]) == ("The limit is 10.", "The team call")

    result = await recall(client, node_id=story["id"])
    assert ids(entries(result["facts"], "memory")) == [new["id"]]
    past = result["past_memories"]["items"]
    assert [(entry["memory"]["id"], entry["memory"]["status"]) for entry in past] == [
        (old["id"], "superseded")
    ]
    assert past[0]["why"] == f"linked directly to this node; replaced by fact {new['id']}"

    events = await history(client, old)
    assert [event["action"] for event in events][-1] == "memory_superseded"
    assert events[-1]["before"] == {"status": "active", "superseded_by_id": None}
    assert events[-1]["after"]["superseded_by_id"] == new["id"] and events[-1]["actor"] == "agent-2"

    # The replacement can start from nothing.
    third = (
        await ok(
            client,
            "supersede_memory",
            actor="a",
            replaces=[new["id"]],
            reason="Starting over",
            text="The limit is 30.",
            source="The owner",
            keep_links=False,
            node_ids=[story["parent_id"]],
            kind="world",
            happened_on="2026-11-02",
        )
    )["replacement"]
    assert (third["node_ids"], third["rule_ids"], third["kind"]) == (
        [story["parent_id"]],
        [],
        "world",
    )
    assert third["happened_on"] == "2026-11-02"


async def test_one_belief_replaces_several_in_one_step(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    fact = await make_fact(client, "The basis")
    one, two, three = [
        await make_belief(client, [fact], f"Belief {number}", node_ids=[story["id"]])
        for number in range(3)
    ]
    new_fact = await make_fact(client, "What changed")

    change = await ok(
        client,
        "supersede_memory",
        actor="agent-1",
        replaces=[one["id"], two["id"]],
        reason="Marie says it is the other way round",
        text="The opposite of the first two",
        evidence_fact_ids=[new_fact["id"]],
    )

    new = change["replacement"]
    assert change["superseded_ids"] == [one["id"], two["id"]]
    assert new["type"] == "belief" and new["supersedes_ids"] == [one["id"], two["id"]]
    assert new["node_ids"] == [story["id"]] and new["evidence_count"] == 1
    for old in (one, two):
        replaced = await get(client, old)
        assert (replaced["status"], replaced["superseded_by_id"]) == ("superseded", new["id"])
        assert replaced["status_reason"] == "Marie says it is the other way round"
        assert (await history(client, old))[-1]["action"] == "memory_superseded"
    assert (await get(client, three))["status"] == "active"

    result = await recall(client, node_id=story["id"])
    assert ids(entries(result["beliefs"], "memory")) == [three["id"], new["id"]]
    assert ids(entries(result["past_memories"], "memory")) == [one["id"], two["id"]]

    # One of them already gone: the whole call is refused, and nothing changes.
    four, five = (
        await make_belief(client, [fact], "Four"),
        await make_belief(client, [fact], "Five"),
    )
    await retract(client, five)
    message = await refused(
        client,
        "supersede_memory",
        actor="a",
        replaces=[four["id"], five["id"]],
        reason="r",
        text="Replacement",
        evidence_fact_ids=[fact["id"]],
    )
    assert f"Belief {five['id']} was retracted (It was wrong), so it cannot be replaced" in message
    assert "Nothing was changed" in message
    assert (await get(client, four))["status"] == "active"


async def test_a_call_can_replace_thirty_beliefs_at_once(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    fact = await make_fact(client, "The basis")
    beliefs = [
        await make_belief(client, [fact], f"Belief {number}", node_ids=[story["id"]])
        for number in range(30)
    ]

    change = await ok(
        client,
        "supersede_memory",
        actor="agent-1",
        replaces=ids(beliefs),
        reason="All of them were about the old design",
        text="None of that applies to the new design",
        evidence_fact_ids=[fact["id"]],
    )

    assert change["superseded_ids"] == ids(beliefs)
    listed, total = await every(client, node_id=story["id"], status="superseded")
    assert ids(listed) == ids(beliefs) and total == 30
    listed, total = await every(client, node_id=story["id"], status="active")
    assert ids(listed) == [change["replacement"]["id"]] and total == 1


async def test_a_replacement_must_be_of_the_same_type_and_complete(client: Client) -> None:
    fact = await make_fact(client, "A fact")
    belief = await make_belief(client, [fact], "A belief")
    base: dict[str, Any] = {"actor": "a", "reason": "r", "text": "New"}

    for arguments, expected in (
        (
            {"replaces": [fact["id"], belief["id"]], "source": "s"},
            "not all the same type",
        ),
        ({"replaces": []}, "replaces needs at least one id"),
        ({"replaces": [987654321], "source": "s"}, "There is no fact or belief with id 987654321"),
        ({"replaces": [fact["id"]]}, "The new fact needs its source"),
        (
            {"replaces": [fact["id"]], "source": "s", "evidence_fact_ids": [fact["id"]]},
            "Evidence is only for beliefs",
        ),
        ({"replaces": [fact["id"]], "source": "s", "reason": " "}, "A replacement needs a reason"),
        ({"replaces": [fact["id"]], "source": "s", "text": " "}, "needs its text"),
        ({"replaces": [fact["id"]], "source": "s", "kind": "rumour"}, "kind must be one of"),
        (
            {"replaces": [belief["id"]], "source": "s", "evidence_fact_ids": [fact["id"]]},
            "source, kind and happened_on are only for facts",
        ),
        ({"replaces": [belief["id"]]}, "A belief needs evidence"),
        (
            {"replaces": [belief["id"]], "evidence_fact_ids": [belief["id"]]},
            "cannot be evidence for another belief",
        ),
        (
            {"replaces": [belief["id"]], "evidence_fact_ids": [fact["id"]], "decision_ids": [1]},
            "A belief concerns nodes and rules only",
        ),
        (
            {"replaces": [fact["id"]], "source": "s", "said_by": "Marie"},
            "Give both said_by",
        ),
        (
            {"replaces": [fact["id"]], "source": "s", "decision_ids": [987654321]},
            "There is no decision with id 987654321",
        ),
    ):
        message = await refused(client, "supersede_memory", **{**base, **arguments})
        assert expected in message, message
        assert (await get(client, fact))["status"] == "active"
        assert (await get(client, belief))["status"] == "active"

    # Facts of different kinds need the new kind to be said.
    world = await make_fact(client, "A", kind="world")
    experience = await make_fact(client, "B", kind="experience")
    message = await refused(
        client,
        "supersede_memory",
        **base,
        replaces=[world["id"], experience["id"]],
        source="s",
    )
    assert "different kinds" in message
    merged = await ok(
        client,
        "supersede_memory",
        **base,
        replaces=[world["id"], experience["id"]],
        source="s",
        kind="world",
    )
    assert merged["replacement"]["kind"] == "world"


async def test_retracting_reports_what_went_stale_and_can_retract_the_beliefs_resting_on_it(
    client: Client,
) -> None:
    wrong = await make_fact(client, "Wrong")
    other = await make_fact(client, "Fine")
    only_wrong = await make_belief(client, [wrong], "Only on the wrong one")
    both = await make_belief(client, [wrong, other], "On both")
    only_other = await make_belief(client, [other], "Only on the other")

    listed, total = await every(client, rests_on_memory_id=wrong["id"])
    assert ids(listed) == [only_wrong["id"], both["id"]] and total == 2

    change = await retract(client, wrong, reason="The source was mistaken")
    assert change["retracted_ids"] == [wrong["id"]] and change["superseded_ids"] == []
    assert change["replacement"] is None and change["dependent_beliefs_retracted_ids"] == []
    assert change["stale_belief_ids"] == [only_wrong["id"], both["id"]]
    assert (await get(client, only_wrong))["stale"] and (await get(client, both))["stale"]
    assert not (await get(client, only_other))["stale"]
    gone = await get(client, wrong)
    assert (gone["status"], gone["status_reason"]) == ("retracted", "The source was mistaken")

    # Retracting a fact nothing rests on says there is nothing to look at.
    lonely = await make_fact(client, "Nothing rests on this")
    assert (await retract(client, lonely))["stale_belief_ids"] == []

    # Asking for the beliefs to go with it retracts them in the same step, for the same reason.
    wrong2 = await make_fact(client, "Wrong again")
    kept = await make_fact(client, "Not involved")
    resting = await make_belief(client, [wrong2, kept], "Rests on both")
    elsewhere = await make_belief(client, [kept], "Rests only on the other fact")
    change = await retract(client, wrong2, reason="Never happened", with_dependent_beliefs=True)
    assert change["retracted_ids"] == [wrong2["id"]]
    assert change["dependent_beliefs_retracted_ids"] == [resting["id"]]
    assert change["stale_belief_ids"] == []
    dependent = await get(client, resting)
    assert (dependent["status"], dependent["status_reason"]) == ("retracted", "Never happened")
    assert (await get(client, elsewhere))["status"] == "active"
    because = (await history(client, resting))[-1]
    assert because["action"] == "memory_retracted"
    assert because["after"]["because_of_ids"] == [wrong2["id"]]


async def test_retracting_refuses_anything_that_is_not_active_and_changes_nothing(
    client: Client,
) -> None:
    kept, gone = await make_fact(client, "Kept"), await make_fact(client, "Gone")
    await retract(client, gone, reason="Because")

    for arguments, expected in (
        ({"memory_ids": [kept["id"], gone["id"]]}, f"Fact {gone['id']} was retracted (Because)"),
        ({"memory_ids": [987654321]}, "There is no fact or belief with id 987654321"),
        ({"memory_ids": []}, "memory_ids needs at least one id"),
        ({"memory_ids": [kept["id"]], "reason": " "}, "A retraction needs a reason"),
        ({"memory_ids": [kept["id"]], "quote": "He said so"}, "Give both said_by"),
    ):
        message = await refused(
            client, "retract_memory", **{"actor": "a", "reason": "r", **arguments}
        )
        assert expected in message, message
    assert (await get(client, kept))["status"] == "active"

    newer = await ok(
        client,
        "supersede_memory",
        actor="a",
        replaces=[kept["id"]],
        reason="Replaced",
        text="Newer",
        source="s",
    )
    message = await refused(
        client, "retract_memory", actor="a", memory_ids=[kept["id"]], reason="r"
    )
    assert (
        f"Fact {kept['id']} was already superseded by fact {newer['replacement']['id']}" in message
    )


async def test_listing_walks_every_page_once_and_the_count_matches(client: Client) -> None:
    epic, story, task = await make_tree(client)
    rule = await make_rule(client, story_id=story["id"])
    other_epic, other_story, _ = await make_tree(client)
    on_task = [await make_fact(client, f"On a task {n}", node_ids=[task["id"]]) for n in range(20)]
    on_story = [
        await make_fact(client, f"On a story {n}", node_ids=[story["id"]]) for n in range(20)
    ]
    on_rule = [await make_fact(client, f"On a rule {n}", rule_ids=[rule["id"]]) for n in range(20)]
    elsewhere = [
        await make_fact(client, f"Elsewhere {n}", node_ids=[other_story["id"]]) for n in range(5)
    ]
    belief = await make_belief(client, [on_task[0]], "On the epic", node_ids=[epic["id"]])
    everything = [*on_task, *on_story, *on_rule, belief]

    # A walk in pages of 25 gives each item once and the count the first page promised.
    found, total = await every(client, within_node_id=epic["id"])
    assert ids(found) == ids(everything) and total == 61
    assert len(set(ids(found))) == len(found)
    page = await ok(client, "list_memories", within_node_id=epic["id"], limit=25)
    assert len(page["items"]) == 25 and page["next_after_id"] == page["items"][-1]["id"]
    assert (await ok(client, "list_memories", within_node_id=epic["id"], limit=200))[
        "next_after_id"
    ] is None

    # `within` takes the item, what is under it, and the rules in its stories: and nothing else.
    for node, expected in (
        (story, [*on_task, *on_story, *on_rule]),
        (task, on_task),
        (other_epic, elsewhere),
    ):
        found, total = await every(client, within_node_id=node["id"])
        assert ids(found) == ids(expected) and total == len(expected)
    # Exact filters stay exact.
    assert ids((await every(client, node_id=story["id"]))[0]) == ids(on_story)
    assert ids((await every(client, rule_id=rule["id"]))[0]) == ids(on_rule)
    assert ids((await every(client, node_id=epic["id"]))[0]) == [belief["id"]]

    # The other filters.
    found, _ = await every(client, within_node_id=epic["id"], type="belief")
    assert ids(found) == [belief["id"]]
    assert (await every(client, within_node_id=epic["id"], type="fact"))[1] == 60
    assert (await every(client, within_node_id=epic["id"], kind="experience"))[1] == 0
    assert (await every(client, within_node_id=epic["id"], origin="derived"))[1] == 0
    assert (await every(client, within_node_id=epic["id"], status="retracted"))[1] == 0

    # Every fact and belief in the database, walked, is as many as the count says.
    everyone, total = await every(client)
    assert len(everyone) == total and len(set(ids(everyone))) == total
    assert ids(everyone) == sorted(ids(everyone))

    for arguments, wanted in (
        ({"type": "rumour"}, "type must be one of fact, belief"),
        ({"kind": "rumour"}, "kind must be one of world, experience"),
        ({"status": "gone"}, "status must be one of active, superseded, retracted"),
        ({"origin": "guessed"}, "origin must be one of stated, extracted, derived"),
        ({"node_id": 987654321}, "There is no node with id 987654321"),
        ({"rule_id": 987654321}, "There is no rule with id 987654321"),
        ({"within_node_id": 987654321}, "There is no node with id 987654321"),
        ({"rests_on_memory_id": 987654321}, "There is no fact with id 987654321"),
        ({"rests_on_memory_id": belief["id"]}, "never on other beliefs"),
        ({"rests_on_decision_id": 987654321}, "There is no decision with id 987654321"),
        ({"limit": 0}, "limit must be a whole number from 1 to 200"),
    ):
        assert wanted in await refused(client, "list_memories", **arguments)


async def test_a_belief_a_person_confirmed_can_only_be_changed_with_that_persons_word(
    client: Client,
) -> None:
    fact = await make_fact(client, "The basis")
    belief = await make_belief(client, [fact], "Confirmed conclusion")
    other = await make_belief(client, [fact], "Unconfirmed conclusion")
    confirmed = await ok(
        client, "confirm_belief", actor="agent-1", memory_id=belief["id"], confirmed_by="Marie"
    )
    assert confirmed["confirmed_by"] == "Marie" and confirmed["confirmed_at"]
    assert confirmed["status"] == "active" and "Suggestion" in confirmed["label"]
    assert (await history(client, belief))[-1]["action"] == "memory_confirmed"
    assert (await history(client, belief))[-1]["after"]["confirmed_by"] == "Marie"

    replacement: dict[str, Any] = {
        "actor": "agent-2",
        "reason": "Marie changed her mind",
        "text": "The revised conclusion",
        "evidence_fact_ids": [fact["id"]],
    }
    for tool, arguments in (
        ("supersede_memory", {**replacement, "replaces": [belief["id"]]}),
        (
            "supersede_memory",
            {**replacement, "replaces": [belief["id"], other["id"]]},
        ),
        ("retract_memory", {"actor": "agent-2", "memory_ids": [belief["id"]], "reason": "r"}),
    ):
        message = await refused(client, tool, **arguments)
        assert f"belief {belief['id']} (confirmed by Marie) was confirmed by a person" in message
        assert "said_by" in message and "quote" in message and "Nothing was changed" in message
    for tool, arguments in (
        ("supersede_memory", {**replacement, "replaces": [belief["id"]]}),
        ("retract_memory", {"actor": "agent-2", "memory_ids": [belief["id"]], "reason": "r"}),
    ):
        assert "Give both said_by" in await refused(client, tool, **arguments, said_by="Marie")
        assert "Give both said_by" in await refused(client, tool, **arguments, quote="She said")
    assert (await get(client, belief))["status"] == "active"
    assert (await get(client, other))["status"] == "active"

    # With the person's name and words it goes through, and the history keeps both people.
    change = await ok(
        client,
        "supersede_memory",
        **replacement,
        replaces=[belief["id"], other["id"]],
        said_by="Marie",
        quote="In the planning call: 'that is no longer how we think about it'",
    )
    assert change["superseded_ids"] == [belief["id"], other["id"]]
    for old in (belief, other):
        event = (await history(client, old))[-1]
        assert (event["action"], event["actor"]) == ("memory_superseded", "agent-2")
        assert event["after"]["said_by"] == "Marie"
        assert event["after"]["quote"].startswith("In the planning call")
    # The confirmation of the replaced belief is kept as it was.
    assert (await get(client, belief))["confirmed_by"] == "Marie"

    # A suggestion nobody confirmed can be retracted by an agent alone.
    suggestion = await make_belief(client, [fact], "Just an idea")
    assert (await retract(client, suggestion, actor="agent-3"))["retracted_ids"] == [
        suggestion["id"]
    ]
    assert "said_by" not in (await history(client, suggestion))[-1]["after"]

    # A retraction by a person's word.
    third = await make_belief(client, [fact], "Another confirmed one")
    await ok(client, "confirm_belief", actor="a", memory_id=third["id"], confirmed_by="Ann")
    await retract(client, third, actor="agent-3", said_by="Ann", quote="It was never true")
    event = (await history(client, third))[-1]
    assert (event["action"], event["actor"], event["after"]["said_by"]) == (
        "memory_retracted",
        "agent-3",
        "Ann",
    )


async def test_retracting_a_fact_with_its_beliefs_needs_a_persons_word_for_a_confirmed_one(
    client: Client,
) -> None:
    fact = await make_fact(client, "The basis")
    plain = await make_belief(client, [fact], "Unconfirmed")
    confirmed = await make_belief(client, [fact], "Confirmed")
    await ok(client, "confirm_belief", actor="a", memory_id=confirmed["id"], confirmed_by="Marie")

    message = await refused(
        client,
        "retract_memory",
        **{
            "actor": "a",
            "memory_ids": [fact["id"]],
            "reason": "Wrong",
            "with_dependent_beliefs": True,
        },
    )
    assert f"belief {confirmed['id']} (confirmed by Marie)" in message
    assert (await get(client, fact))["status"] == "active"
    assert (await get(client, plain))["status"] == "active"

    change = await retract(
        client, fact, with_dependent_beliefs=True, said_by="Marie", quote="Yes, drop all of it"
    )
    assert change["dependent_beliefs_retracted_ids"] == [plain["id"], confirmed["id"]]


async def test_confirming_records_who_and_when_and_only_for_a_current_belief(
    client: Client,
) -> None:
    fact = await make_fact(client, "Basis")
    belief = await make_belief(client, [fact], "Conclusion")

    assert "is a fact. Only a belief can be confirmed" in await refused(
        client, "confirm_belief", actor="a", memory_id=fact["id"], confirmed_by="Marie"
    )
    assert "confirmed_by must be the name of a person" in await refused(
        client, "confirm_belief", actor="a", memory_id=belief["id"], confirmed_by=" "
    )
    assert "There is no fact or belief with id" in await refused(
        client, "confirm_belief", actor="a", memory_id=987654321, confirmed_by="Marie"
    )
    once = await ok(
        client, "confirm_belief", actor="agent-1", memory_id=belief["id"], confirmed_by="Marie"
    )
    assert "was already confirmed by Marie" in await refused(
        client, "confirm_belief", actor="a", memory_id=belief["id"], confirmed_by="Ann"
    )
    assert (await get(client, belief))["confirmed_at"] == once["confirmed_at"]
    event = (await history(client, belief))[-1]
    assert (event["actor"], event["after"]["confirmed_by"]) == ("agent-1", "Marie")

    stale = await make_belief(client, [(await make_fact(client, "Soon wrong"))])
    await retract(client, {"id": stale["evidence"][0]["id"]})
    assert "is stale" in await refused(
        client, "confirm_belief", actor="a", memory_id=stale["id"], confirmed_by="Marie"
    )
    gone = await make_belief(client, [fact], "Retracted")
    await retract(client, gone)
    assert "was retracted" in await refused(
        client, "confirm_belief", actor="a", memory_id=gone["id"], confirmed_by="Marie"
    )


async def test_promoting_a_belief_records_a_decision_by_a_person_that_links_back(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, story_id=story["id"])
    fact = await make_fact(client, "Basis")
    belief = await make_belief(
        client,
        [fact],
        "We should keep one database.",
        node_ids=[story["id"]],
        rule_ids=[rule["id"]],
    )

    decision = await ok(
        client,
        "promote_belief",
        actor="agent-1",
        memory_id=belief["id"],
        title="Keep one database",
        reason="It is simpler to run.",
        decided_by="Marie",
        decided_on="2026-11-10",
    )

    assert decision["decided_by"] == "Marie" and decision["decided_on"] == "2026-11-10"
    assert (decision["kind"], decision["status"]) == ("decision", "active")
    assert decision["text"] == "We should keep one database."
    assert decision["promoted_from_id"] == belief["id"]
    assert decision["node_ids"] == [story["id"]] and decision["rule_ids"] == [rule["id"]]
    after = await get(client, belief)
    assert after["promoted_decision_ids"] == [decision["id"]]
    assert (after["status"], after["text"]) == ("active", belief["text"])

    # It is now an authoritative decision for the story, and the belief is still a suggestion.
    result = await recall(client, node_id=story["id"])
    assert ids(entries(result["active_decisions"], "decision")) == [decision["id"]]
    assert ids(entries(result["beliefs"], "memory")) == [belief["id"]]
    events = (await ok(client, "decision_history", decision_id=decision["id"]))["items"]
    assert events[0]["action"] == "decision_recorded"
    assert events[0]["after"]["promoted_from_id"] == belief["id"]
    promoted = [e for e in await history(client, belief) if e["action"] == "memory_promoted"]
    assert len(promoted) == 1 and promoted[0]["decision_id"] == decision["id"]
    assert promoted[0]["actor"] == "agent-1" and promoted[0]["after"]["decided_by"] == "Marie"

    # Replacing the belief reports the decision and leaves it as it is.
    change = await ok(
        client,
        "supersede_memory",
        actor="a",
        replaces=[belief["id"]],
        reason="New evidence",
        text="We should split it after all.",
        evidence_fact_ids=[fact["id"]],
    )
    assert change["promoted_decision_ids"] == [decision["id"]]
    same = await ok(client, "get_decision", decision_id=decision["id"])
    assert same["status"] == "active" and same["text"] == decision["text"]
    gone = await make_belief(client, [fact], "Another")
    promoted_too = await ok(
        client,
        "promote_belief",
        actor="a",
        memory_id=gone["id"],
        title="T",
        reason="R",
        decided_by="Ann",
        text="Worded differently",
        kind="principle",
    )
    assert (promoted_too["kind"], promoted_too["text"]) == ("principle", "Worded differently")
    assert (await retract(client, gone))["promoted_decision_ids"] == [promoted_too["id"]]


async def test_only_a_current_belief_can_be_promoted_and_only_once(client: Client) -> None:
    fact = await make_fact(client, "Basis")
    belief = await make_belief(client, [fact], "Conclusion")
    arguments: dict[str, Any] = {"actor": "a", "title": "T", "reason": "R", "decided_by": "Ann"}

    for target, expected in (
        (fact["id"], "is a fact. Only a belief can be promoted to a decision"),
        (987654321, "There is no fact or belief with id"),
    ):
        assert expected in await refused(client, "promote_belief", memory_id=target, **arguments)
    for change, expected in (
        ({"decided_by": " "}, "A decision needs the person who made it"),
        ({"reason": " "}, "A decision needs a reason"),
        ({"title": " "}, "A decision needs a title"),
        ({"kind": "rule"}, "kind must be one of decision, principle"),
    ):
        message = await refused(
            client, "promote_belief", memory_id=belief["id"], **{**arguments, **change}
        )
        assert expected in message
    assert (await get(client, belief))["promoted_decision_ids"] == []

    first = await ok(client, "promote_belief", memory_id=belief["id"], **arguments)
    message = await refused(client, "promote_belief", memory_id=belief["id"], **arguments)
    assert f"already promoted to decision {first['id']}" in message

    # Once that decision is replaced, the belief can be promoted again.
    await ok(
        client,
        "supersede_decision",
        actor="a",
        decision_id=first["id"],
        title="T2",
        text="t",
        reason="r",
        decided_by="Ann",
    )
    again = await ok(client, "promote_belief", memory_id=belief["id"], **arguments)
    assert again["promoted_from_id"] == belief["id"]

    stale = await make_belief(client, [(await make_fact(client, "Soon wrong"))])
    await retract(client, {"id": stale["evidence"][0]["id"]})
    assert "is stale" in await refused(client, "promote_belief", memory_id=stale["id"], **arguments)


async def test_what_a_fact_or_belief_concerns_can_be_changed_while_it_is_active(
    client: Client,
) -> None:
    first, second = await make_stories(client, 2)
    rule = await make_rule(client, story_id=first["id"])
    decision = await make_decision(client, "A decision")
    fact = await make_fact(client, "A fact", node_ids=[first["id"]])
    belief = await make_belief(client, [fact], "A belief", node_ids=[first["id"]])

    linked = await ok(
        client,
        "link_memory",
        actor="agent-1",
        memory_id=fact["id"],
        node_ids=[second["id"]],
        rule_ids=[rule["id"]],
        decision_ids=[decision["id"]],
    )
    assert linked["node_ids"] == sorted([first["id"], second["id"]])
    assert linked["rule_ids"] == [rule["id"]] and linked["decision_ids"] == [decision["id"]]
    assert fact["id"] in ids((await every(client, node_id=second["id"]))[0])
    unlinked = await ok(
        client, "unlink_memory", actor="a", memory_id=fact["id"], node_ids=[first["id"]]
    )
    assert unlinked["node_ids"] == [second["id"]]
    assert [e["action"] for e in await history(client, fact)][-2:] == [
        "memory_linked",
        "memory_unlinked",
    ]
    node_events = (await ok(client, "node_history", node_id=first["id"]))["items"]
    assert [e["action"] for e in node_events if e["memory_id"] == fact["id"]] == [
        "memory_linked",
        "memory_unlinked",
    ]

    arguments: dict[str, Any] = {"actor": "a", "memory_id": belief["id"]}
    for tool, extra, expected in (
        ("link_memory", {"decision_ids": [decision["id"]]}, "A belief concerns nodes and rules"),
        ("link_memory", {"node_ids": [first["id"]]}, "already linked to node"),
        ("unlink_memory", {"node_ids": [second["id"]]}, "is not linked to node"),
        ("link_memory", {"node_ids": [987654321]}, "There is no node with id 987654321"),
        ("link_memory", {"rule_ids": [987654321]}, "There is no rule with id 987654321"),
        ("link_memory", {}, "Nothing to link"),
        ("unlink_memory", {}, "Nothing to link"),
    ):
        assert expected in await refused(client, tool, **arguments, **extra)
    assert "There is no fact or belief" in await refused(
        client, "link_memory", actor="a", memory_id=987654321, node_ids=[first["id"]]
    )

    # A fact that has been replaced keeps the links it had.
    await retract(client, fact)
    message = await refused(
        client, "link_memory", actor="a", memory_id=fact["id"], node_ids=[first["id"]]
    )
    assert "keeps the links it had" in message
    assert (await get(client, fact))["node_ids"] == [second["id"]]


async def test_recall_gives_what_the_exact_links_give_and_says_why_each_entry_is_there(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    other_story = (await make_stories(client, 1))[0]
    rule = await make_rule(client, story_id=story["id"])
    decision = await make_decision(client, "Linked decision", node_ids=[story["id"]])
    principle = await make_decision(client, "Standing", kind="principle")
    fact = await make_fact(client, "Linked fact", node_ids=[story["id"]])
    elsewhere = await make_fact(client, "Linked elsewhere", node_ids=[other_story["id"]])
    parent_fact = await make_fact(client, "Linked to the epic", node_ids=[epic["id"]])
    child_fact = await make_fact(client, "Linked to the task", node_ids=[task["id"]])
    rule_fact = await make_fact(client, "Linked to the rule", rule_ids=[rule["id"]])
    direct = await make_belief(client, [elsewhere], "Linked belief", node_ids=[story["id"]])
    through_fact = await make_belief(client, [fact], "Rests on a linked fact")
    through_decision = await ok(
        client,
        "record_belief",
        actor="a",
        text="Rests on a linked decision",
        evidence_decision_ids=[decision["id"]],
    )
    unrelated = await make_belief(client, [elsewhere], "Unrelated")
    replaced = await make_fact(client, "Old fact", node_ids=[story["id"]])
    await ok(
        client,
        "supersede_memory",
        actor="a",
        replaces=[replaced["id"]],
        reason="Changed",
        text="Replacement of old fact",
        source="s",
        keep_links=False,
    )
    retracted = await make_fact(client, "Wrong fact", node_ids=[story["id"]])
    await retract(client, retracted, reason="It was wrong")
    soon_stale_fact = await make_fact(client, "Soon wrong")
    stale = await make_belief(client, [soon_stale_fact], "Will go stale", node_ids=[story["id"]])
    await retract(client, soon_stale_fact)

    result = await recall(client, node_id=story["id"])

    assert ids(entries(result["active_decisions"], "decision")) == [decision["id"]]
    assert result["active_decisions"]["items"][0]["why"] == "linked directly to this node"
    assert principle["id"] in ids(entries(result["active_principles"], "decision"))
    assert result["active_principles"]["items"][0]["why"] == "a principle, so it applies everywhere"
    assert ids(entries(result["facts"], "memory")) == [fact["id"]]
    assert result["facts"]["items"][0]["why"] == "linked directly to this node"
    beliefs = {entry["memory"]["id"]: entry["why"] for entry in result["beliefs"]["items"]}
    assert beliefs == {
        direct["id"]: "linked directly to this node",
        through_fact["id"]: "rests on evidence linked to this node",
        through_decision["id"]: "rests on evidence linked to this node",
    }
    assert unrelated["id"] not in beliefs
    assert all(
        entry["memory"]["label"].startswith("Suggestion") for entry in result["beliefs"]["items"]
    )
    # Neither the parent's nor the children's facts are inferred.
    for left_out in (elsewhere, parent_fact, child_fact, rule_fact):
        assert left_out["id"] not in ids(entries(result["facts"], "memory"))
    assert ids(entries(result["stale_beliefs"], "memory")) == [stale["id"]]
    past = {entry["memory"]["id"]: entry["why"] for entry in result["past_memories"]["items"]}
    assert past[replaced["id"]].startswith("linked directly to this node; replaced by fact ")
    assert past[retracted["id"]] == "linked directly to this node; retracted: It was wrong"
    counts = {name: section["count"] for name, section in result.items() if section}
    assert counts["facts"] == 1 and counts["beliefs"] == 3 and counts["stale_beliefs"] == 1
    assert counts["past_memories"] == 2 and counts["superseded_decisions"] == 0

    # The same item reached as a rule gives the rule's own links.
    for_rule = await recall(client, rule_id=rule["id"])
    assert ids(entries(for_rule["facts"], "memory")) == [rule_fact["id"]]
    assert for_rule["active_decisions"]["count"] == 0 and for_rule["beliefs"]["count"] == 0

    # An item nothing is linked to gets the principles and nothing else.
    for_task = await recall(client, node_id=task["id"])
    assert ids(entries(for_task["facts"], "memory")) == [child_fact["id"]]
    empty_story = (await make_stories(client, 1))[0]
    empty = await recall(client, node_id=empty_story["id"])
    assert all(
        empty[name]["items"] == [] and empty[name]["count"] == 0
        for name in ("active_decisions", "facts", "beliefs", "stale_beliefs", "past_memories")
    )
    assert empty["active_principles"]["count"] >= 1


async def test_each_recall_section_pages_on_its_own_and_bad_asks_are_refused(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    facts = [await make_fact(client, f"Fact {n}", node_ids=[story["id"]]) for n in range(3)]

    both = await recall(client, node_id=story["id"], limit=2)
    assert ids(entries(both["facts"], "memory")) == ids(facts[:2])
    assert both["facts"]["count"] == 3 and both["facts"]["next_after_id"] == facts[1]["id"]
    rest = await recall(
        client,
        node_id=story["id"],
        limit=2,
        show="facts",
        after_id=both["facts"]["next_after_id"],
    )
    assert ids(entries(rest["facts"], "memory")) == [facts[2]["id"]]
    assert rest["facts"]["next_after_id"] is None
    assert rest["beliefs"]["items"] == [] and rest["active_principles"]["items"] == []

    assert "after_id only works for one section at a time" in await refused(
        client, "recall", node_id=story["id"], after_id=1
    )
    assert "show must be one of all, active_decisions" in await refused(
        client, "recall", node_id=story["id"], show="everything"
    )
    assert "Give exactly one of node_id or rule_id" in await refused(client, "recall")
    assert "Give exactly one of node_id or rule_id" in await refused(
        client, "recall", node_id=story["id"], rule_id=1
    )
    assert "There is no node with id 987654321" in await refused(
        client, "recall", node_id=987654321
    )
    assert "There is no rule with id 987654321" in await refused(
        client, "recall", rule_id=987654321
    )


async def test_getting_a_fact_or_belief_that_does_not_exist_says_so(client: Client) -> None:
    assert "There is no fact or belief with id 987654321" in await refused(
        client, "get_memory", memory_id=987654321
    )
    assert "There is no fact or belief with id 987654321" in await refused(
        client, "memory_history", memory_id=987654321
    )


async def test_two_replacements_of_the_same_fact_at_once_let_only_one_win(client: Client) -> None:
    for _ in range(6):
        fact = await make_fact(client, "Contested")
        results = await race_results(
            client,
            "supersede_memory",
            [
                {
                    "actor": f"agent-{number}",
                    "replaces": [fact["id"]],
                    "reason": "Changed",
                    "text": f"Replacement {number}",
                    "source": "s",
                }
                for number in range(2)
            ],
        )
        assert sorted(accepted for accepted, _ in results) == [False, True], results
        (message,) = [text for accepted, text in results if not accepted]
        assert f"Fact {fact['id']} was already superseded by fact" in message
        assert (await get(client, fact))["status"] == "superseded"
        winner, *rest = (await ok(client, "list_memories", after_id=fact["id"], limit=10))["items"]
        assert winner["supersedes_ids"] == [fact["id"]] and rest == []


async def test_a_retraction_and_new_beliefs_on_the_same_fact_never_leave_a_live_belief(
    client: Client,
) -> None:
    for _ in range(6):
        fact = await make_fact(client, "About to be retracted")
        await race_mixed(
            client,
            [
                (
                    "record_belief",
                    {
                        "actor": "agent-1",
                        "text": f"Belief {number}",
                        "evidence_fact_ids": [fact["id"]],
                    },
                )
                for number in range(3)
            ]
            + [
                (
                    "retract_memory",
                    {
                        "actor": "agent-2",
                        "memory_ids": [fact["id"]],
                        "reason": "Wrong",
                        "with_dependent_beliefs": True,
                    },
                )
            ],
        )
        assert (await get(client, fact))["status"] == "retracted"
        # A belief that got in first was retracted with the fact, and one that came after was
        # refused: none is left alive on a fact that is gone.
        resting = (await ok(client, "list_memories", rests_on_memory_id=fact["id"]))["items"]
        assert all(item["status"] == "retracted" for item in resting), resting
