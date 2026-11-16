"""M16 against the running containers: facts (a statement and its source), what they concern,
replacing and withdrawing them, confirmation by a named person, and recall of what is known,
through a real MCP client (see ./check.sh)."""

from datetime import datetime
from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    confirm,
    make_decision,
    make_fact,
    make_rule,
    make_stories,
    make_tree,
    ok,
    race_mixed,
    race_results,
    refused,
    run_sql,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

FACT_FIELDS = {
    "id",
    "label",
    "text",
    "source",
    "recorded_by",
    "recorded_at",
    "status",
    "superseded_by_id",
    "supersedes_ids",
    "status_reason",
    "confirmation",
    "confirmed_by",
    "confirmed_at",
    "node_ids",
    "rule_ids",
    "decision_ids",
}


def ids(items: list[dict[str, Any]]) -> list[int]:
    return [item["id"] for item in items]


async def recall(client: Client, **arguments: Any) -> dict[str, Any]:
    return await ok(client, "recall", **arguments)


def facts_in(section: dict[str, Any]) -> list[dict[str, Any]]:
    """The facts in one section of a recall."""
    return [entry["fact"] for entry in section["items"]]


async def history(client: Client, fact: dict[str, Any]) -> list[dict[str, Any]]:
    return list((await ok(client, "fact_history", fact_id=fact["id"]))["items"])


async def get(client: Client, fact: dict[str, Any]) -> dict[str, Any]:
    return await ok(client, "get_fact", fact_id=fact["id"])


async def every(client: Client, **filters: Any) -> tuple[list[dict[str, Any]], int]:
    """Every fact matching the filters, in pages, with the total the first page gave."""
    found: list[dict[str, Any]] = []
    after_id = None
    total = None
    while True:
        page = await ok(client, "list_facts", limit=25, after_id=after_id, **filters)
        total = page["total"] if total is None else total
        found.extend(page["items"])
        after_id = page["next_after_id"]
        if after_id is None:
            return found, total


async def tally(client: Client) -> tuple[int, int, int]:
    """How many facts, decisions and events the project holds: a refused call must leave all
    three as they were."""
    counts = (await ok(client, "server_status"))["counts"]
    return counts["facts"], counts["decisions"], counts["events"]


async def withdraw(client: Client, *facts: dict[str, Any], **arguments: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "actor": "agent-1",
        "fact_ids": [fact["id"] for fact in facts],
        "reason": "It was wrong",
    }
    return await ok(client, "withdraw_fact", **{**fields, **arguments})


async def replace(client: Client, *facts: dict[str, Any], **arguments: Any) -> dict[str, Any]:
    """Supersede the facts with a new one. Returns the change (replacement, superseded_ids...)."""
    fields: dict[str, Any] = {
        "actor": "agent-2",
        "replaces": [fact["id"] for fact in facts],
        "reason": "It changed",
        "text": "The newer fact",
        "source": "The newer source",
    }
    return await ok(client, "supersede_fact", **{**fields, **arguments})


def instant(text: str) -> datetime:
    return datetime.fromisoformat(text)


async def test_a_fact_is_recorded_with_its_source_and_what_it_concerns(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, story_id=story["id"])
    decision = await make_decision(client, "Keep one database")

    fact = await ok(
        client,
        "retain_fact",
        actor="agent-1",
        text="The build takes four minutes.",
        source="Timed it on Marie's laptop",
        node_ids=[story["id"]],
        rule_ids=[rule["id"]],
        decision_ids=[decision["id"]],
    )

    assert set(fact) == FACT_FIELDS
    assert fact["text"] == "The build takes four minutes."
    assert fact["source"] == "Timed it on Marie's laptop"
    assert (fact["status"], fact["recorded_by"]) == ("active", "agent-1")
    assert (fact["superseded_by_id"], fact["supersedes_ids"], fact["status_reason"]) == (
        None,
        [],
        None,
    )
    assert fact["recorded_at"]
    assert fact["node_ids"] == [story["id"]] and fact["rule_ids"] == [rule["id"]]
    assert fact["decision_ids"] == [decision["id"]]
    # Recording is not confirming: nobody has looked at it yet, and the words say so.
    assert (fact["confirmation"], fact["confirmed_by"], fact["confirmed_at"]) == (
        "unconfirmed",
        None,
        None,
    )
    assert fact["label"] == "Fact, not confirmed by a person. Source: Timed it on Marie's laptop"

    assert await get(client, fact) == fact
    assert [(e["action"], e["actor"]) for e in await history(client, fact)] == [
        ("fact_recorded", "agent-1"),
        ("fact_linked", "agent-1"),
        ("fact_linked", "agent-1"),
        ("fact_linked", "agent-1"),
    ]
    recorded = (await history(client, fact))[0]
    assert recorded["before"] is None and recorded["fact_id"] == fact["id"]
    assert recorded["after"] == {
        "text": "The build takes four minutes.",
        "source": "Timed it on Marie's laptop",
    }

    # What it concerns shows in the histories of those things.
    for tool, key, thing in (
        ("node_history", "node_id", story),
        ("rule_history", "rule_id", rule),
        ("decision_history", "decision_id", decision),
    ):
        events = (await ok(client, tool, **{key: thing["id"]}))["items"]
        assert any(
            event["fact_id"] == fact["id"] and event["action"] == "fact_linked" for event in events
        ), f"{tool} does not show the fact"

    plain = await make_fact(client)
    assert (plain["node_ids"], plain["rule_ids"], plain["decision_ids"]) == ([], [], [])


async def test_recording_a_fact_never_confirms_it_whoever_records_it(client: Client) -> None:
    # A person's name as the actor is still only the name of who recorded it.
    by_a_person = await make_fact(client, "Said by Marie herself", actor="Marie")
    assert by_a_person["recorded_by"] == "Marie"
    assert (by_a_person["confirmation"], by_a_person["confirmed_by"]) == ("unconfirmed", None)

    # Nothing a caller adds to the call confirms it either.
    result = await client.call_tool(
        "retain_fact",
        {
            "actor": "Marie",
            "text": "Marie says it is confirmed",
            "source": "Marie",
            "confirmed_by": "Marie",
            "confirmation": "confirmed",
        },
    )
    if not result.is_error:
        assert result.structured_content is not None
        assert result.structured_content["confirmation"] == "unconfirmed"
        assert result.structured_content["confirmed_by"] is None
    listed, _ = await every(client, confirmation="confirmed")
    assert by_a_person["id"] not in ids(listed)

    # A replacement of an unconfirmed fact starts unconfirmed too.
    replacement = (await replace(client, by_a_person, actor="Marie"))["replacement"]
    assert replacement["confirmation"] == "unconfirmed" and replacement["confirmed_by"] is None


async def test_a_fact_that_is_blank_unsourced_or_about_nothing_is_refused(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    base: dict[str, Any] = {"actor": "agent-1", "text": "Something", "source": "Someone"}
    before = await tally(client)

    for change, expected in (
        ({"source": "  "}, "A fact needs its source"),
        ({"source": ""}, "A fact needs its source"),
        ({"text": " "}, "A fact needs its text"),
        ({"node_ids": [987654321]}, "There is no node with id 987654321, so the fact cannot be"),
        ({"rule_ids": [987654321]}, "There is no rule with id 987654321"),
        ({"decision_ids": [987654321]}, "There is no decision with id 987654321"),
        ({"actor": " "}, "Say who is making this change"),
    ):
        assert expected in await refused(client, "retain_fact", **{**base, **change})

    # A refused fact leaves nothing behind, not even a link made before the bad one was found.
    mixed = {**base, "node_ids": [story["id"]], "decision_ids": [987654321]}
    await refused(client, "retain_fact", **mixed)
    assert await tally(client) == before
    assert (await ok(client, "list_facts", node_id=story["id"]))["total"] == 0


async def test_a_fact_is_never_edited_a_replaced_one_points_at_its_replacement(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, story_id=story["id"])
    decision = await make_decision(client, "A decision")
    old = await make_fact(
        client,
        "The limit is 10.",
        node_ids=[story["id"]],
        rule_ids=[rule["id"]],
        decision_ids=[decision["id"]],
    )

    change = await replace(
        client,
        old,
        reason="The limit was raised",
        text="The limit is 20.",
        source="Release notes, version 4",
    )

    new = change["replacement"]
    assert change["superseded_ids"] == [old["id"]] and change["withdrawn_ids"] == []
    assert new["status"] == "active" and new["supersedes_ids"] == [old["id"]]
    assert (new["text"], new["source"], new["recorded_by"]) == (
        "The limit is 20.",
        "Release notes, version 4",
        "agent-2",
    )
    assert new["confirmation"] == "unconfirmed"
    # What the old one concerned carries over.
    assert new["node_ids"] == [story["id"]] and new["rule_ids"] == [rule["id"]]
    assert new["decision_ids"] == [decision["id"]]
    gone = await get(client, old)
    assert (gone["status"], gone["superseded_by_id"], gone["status_reason"]) == (
        "superseded",
        new["id"],
        "The limit was raised",
    )
    # What it said and where it came from are kept exactly as they were.
    assert (gone["text"], gone["source"]) == ("The limit is 10.", "The team call")
    assert gone["label"].startswith("Superseded fact, not confirmed by a person")
    # It keeps the links it had, as a record.
    assert gone["node_ids"] == [story["id"]] and gone["rule_ids"] == [rule["id"]]

    result = await recall(client, node_id=story["id"])
    assert ids(facts_in(result["facts"])) == [new["id"]]
    past = result["past_facts"]["items"]
    assert [(entry["fact"]["id"], entry["fact"]["status"]) for entry in past] == [
        (old["id"], "superseded")
    ]
    assert past[0]["why"] == f"linked directly to this node; replaced by fact {new['id']}"
    assert (await every(client, node_id=story["id"], status="active"))[0] == [new]

    events = await history(client, old)
    assert [event["action"] for event in events][-1] == "fact_superseded"
    assert events[-1]["before"] == {"status": "active", "superseded_by_id": None}
    assert events[-1]["after"] == {
        "status": "superseded",
        "superseded_by_id": new["id"],
        "reason": "The limit was raised",
    }
    assert events[-1]["actor"] == "agent-2"
    new_events = await history(client, new)
    assert new_events[0]["action"] == "fact_recorded"
    assert new_events[0]["after"]["supersedes_ids"] == [old["id"]]

    # The replacement can start from nothing, and can say what else it concerns.
    third = (
        await replace(
            client,
            new,
            reason="Starting over",
            text="The limit is 30.",
            source="The owner",
            keep_links=False,
            node_ids=[story["parent_id"]],
        )
    )["replacement"]
    assert (third["node_ids"], third["rule_ids"], third["decision_ids"]) == (
        [story["parent_id"]],
        [],
        [],
    )
    # With the links kept, the ids it is given are added to them.
    fourth = (await replace(client, third, node_ids=[story["id"]]))["replacement"]
    assert fourth["node_ids"] == sorted([story["id"], story["parent_id"]])


async def test_a_confirmation_stays_with_the_replaced_fact_and_the_new_one_starts_unconfirmed(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    old = await make_fact(client, "Confirmed and then replaced", node_ids=[story["id"]])
    reviewed = await confirm(client, "fact", old, "Marie", quote="I checked it")

    change = await replace(
        client,
        old,
        reason="Marie changed her mind",
        said_by="Marie",
        quote="In the planning call: 'it is different now'",
    )
    new = change["replacement"]

    # The new fact is a new statement: nobody has looked at it yet.
    assert (new["confirmation"], new["confirmed_by"], new["confirmed_at"]) == (
        "unconfirmed",
        None,
        None,
    )
    assert new["label"].startswith("Fact, not confirmed by a person")
    # The old one keeps exactly the confirmation it had, and says it is superseded.
    gone = await get(client, old)
    assert gone["status"] == "superseded"
    assert (gone["confirmation"], gone["confirmed_by"], gone["confirmed_at"]) == (
        "confirmed",
        "Marie",
        reviewed["confirmed_at"],
    )
    assert gone["label"].startswith("Superseded fact, confirmed by Marie on ")

    assert old["id"] in ids((await every(client, status="superseded", confirmation="confirmed"))[0])
    assert old["id"] not in ids((await every(client, status="active", confirmation="confirmed"))[0])
    assert new["id"] in ids((await every(client, status="active", confirmation="unconfirmed"))[0])

    result = await recall(client, node_id=story["id"])
    (current,), (past,) = facts_in(result["facts"]), facts_in(result["past_facts"])
    assert (current["id"], current["confirmation"]) == (new["id"], "unconfirmed")
    assert (past["id"], past["confirmation"], past["status"]) == (
        old["id"],
        "confirmed",
        "superseded",
    )

    # The earlier confirmation, and both people who changed it, are in the history.
    events = await history(client, old)
    assert [(e["action"], e["actor"]) for e in events] == [
        ("fact_recorded", "agent-1"),
        ("fact_linked", "agent-1"),
        ("fact_confirmed", "agent-1"),
        ("fact_superseded", "agent-2"),
    ]
    assert events[2]["after"]["confirmed_by"] == "Marie" and events[2]["after"]["quote"]
    assert events[3]["after"]["said_by"] == "Marie"
    assert events[3]["after"]["quote"].startswith("In the planning call")


async def test_one_fact_replaces_several_in_one_step(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    one, two, three = [
        await make_fact(client, f"Fact {number}", node_ids=[story["id"]]) for number in range(3)
    ]

    change = await replace(
        client,
        one,
        two,
        reason="Marie says it is the other way round",
        text="The opposite of the first two",
    )

    new = change["replacement"]
    assert change["superseded_ids"] == [one["id"], two["id"]]
    assert new["supersedes_ids"] == [one["id"], two["id"]] and new["node_ids"] == [story["id"]]
    for old in (one, two):
        replaced = await get(client, old)
        assert (replaced["status"], replaced["superseded_by_id"]) == ("superseded", new["id"])
        assert replaced["status_reason"] == "Marie says it is the other way round"
        assert (await history(client, old))[-1]["action"] == "fact_superseded"
    assert (await get(client, three))["status"] == "active"
    result = await recall(client, node_id=story["id"])
    assert ids(facts_in(result["facts"])) == [three["id"], new["id"]]
    assert ids(facts_in(result["past_facts"])) == [one["id"], two["id"]]

    # One of them already gone: the whole call is refused, and nothing changes.
    four, five = await make_fact(client, "Four"), await make_fact(client, "Five")
    await withdraw(client, five, reason="Never true")
    before = await tally(client)
    message = await refused(
        client,
        "supersede_fact",
        actor="a",
        replaces=[four["id"], five["id"]],
        reason="r",
        text="Replacement",
        source="s",
    )
    assert f"Fact {five['id']} was withdrawn (Never true), so it cannot be replaced" in message
    assert "Nothing was changed" in message
    assert (await get(client, four))["status"] == "active"
    assert await tally(client) == before


async def test_a_call_can_replace_thirty_facts_at_once(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    facts = [
        await make_fact(client, f"Fact {number}", node_ids=[story["id"]]) for number in range(30)
    ]

    change = await replace(client, *facts, reason="All of them were about the old design")

    assert change["superseded_ids"] == ids(facts)
    listed, total = await every(client, node_id=story["id"], status="superseded")
    assert ids(listed) == ids(facts) and total == 30
    listed, total = await every(client, node_id=story["id"], status="active")
    assert ids(listed) == [change["replacement"]["id"]] and total == 1


async def test_a_replacement_that_is_incomplete_or_wrong_changes_nothing(client: Client) -> None:
    fact = await make_fact(client, "A fact")
    other = await make_fact(client, "Another fact")
    base: dict[str, Any] = {
        "actor": "a",
        "replaces": [fact["id"]],
        "reason": "r",
        "text": "New",
        "source": "s",
    }
    before = await tally(client)

    for arguments, expected in (
        ({"replaces": []}, "replaces needs at least one id"),
        ({"replaces": [fact["id"], 987654321]}, "There is no fact with id 987654321"),
        ({"source": " "}, "The new fact needs its source"),
        ({"reason": " "}, "A replacement needs a reason"),
        ({"text": " "}, "The new fact needs its text"),
        ({"said_by": "Marie"}, "Give both said_by"),
        ({"quote": "She said so"}, "Give both said_by"),
        ({"decision_ids": [987654321]}, "There is no decision with id 987654321"),
        ({"node_ids": [987654321]}, "There is no node with id 987654321"),
        ({"rule_ids": [987654321]}, "There is no rule with id 987654321"),
        ({"actor": " "}, "Say who is making this change"),
        ({"replaces": [fact["id"], other["id"]], "reason": " "}, "A replacement needs a reason"),
    ):
        message = await refused(client, "supersede_fact", **{**base, **arguments})
        assert expected in message, message
        assert (await get(client, fact))["status"] == "active"
        assert (await get(client, other))["status"] == "active"
        assert await tally(client) == before


async def test_withdrawing_needs_a_reason_covers_several_and_keeps_the_record(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    one = await make_fact(client, "Wrong one", node_ids=[story["id"]])
    two = await make_fact(client, "Wrong two", node_ids=[story["id"]])
    kept = await make_fact(client, "Right", node_ids=[story["id"]])

    change = await withdraw(
        client, one, two, actor="agent-3", reason="The benchmark was run on the wrong branch"
    )

    assert change["withdrawn_ids"] == [one["id"], two["id"]]
    assert change["superseded_ids"] == [] and change["replacement"] is None
    for wrong in (one, two):
        gone = await get(client, wrong)
        assert (gone["status"], gone["status_reason"], gone["superseded_by_id"]) == (
            "withdrawn",
            "The benchmark was run on the wrong branch",
            None,
        )
        # Nothing is deleted or rewritten.
        assert (gone["text"], gone["source"], gone["node_ids"]) == (
            wrong["text"],
            wrong["source"],
            [story["id"]],
        )
        assert gone["label"].startswith("Withdrawn fact, not confirmed by a person")
        events = await history(client, wrong)
        assert (events[-1]["action"], events[-1]["actor"]) == ("fact_withdrawn", "agent-3")
        assert events[-1]["before"] == {"status": "active"}
        assert events[-1]["after"] == {
            "status": "withdrawn",
            "reason": "The benchmark was run on the wrong branch",
        }
    assert (await get(client, kept))["status"] == "active"

    result = await recall(client, node_id=story["id"])
    assert ids(facts_in(result["facts"])) == [kept["id"]]
    why = {entry["fact"]["id"]: entry["why"] for entry in result["past_facts"]["items"]}
    assert why == {
        one["id"]: "linked directly to this node; withdrawn: The benchmark was run on the wrong "
        "branch",
        two["id"]: "linked directly to this node; withdrawn: The benchmark was run on the wrong "
        "branch",
    }
    assert ids((await every(client, node_id=story["id"], status="withdrawn"))[0]) == ids([one, two])


async def test_withdrawing_refuses_blank_reasons_and_anything_not_active_and_changes_nothing(
    client: Client,
) -> None:
    kept, gone, replaced = (
        await make_fact(client, "Kept"),
        await make_fact(client, "Gone"),
        await make_fact(client, "Replaced"),
    )
    await withdraw(client, gone, reason="Because")
    newer = (await replace(client, replaced, reason="Changed"))["replacement"]
    before = await tally(client)

    for arguments, expected in (
        ({"fact_ids": [kept["id"], gone["id"]]}, f"Fact {gone['id']} was withdrawn (Because)"),
        (
            {"fact_ids": [kept["id"], replaced["id"]]},
            f"Fact {replaced['id']} was already superseded by fact {newer['id']} (Changed)",
        ),
        ({"fact_ids": [kept["id"], 987654321]}, "There is no fact with id 987654321"),
        ({"fact_ids": []}, "fact_ids needs at least one id"),
        ({"fact_ids": [kept["id"]], "reason": " "}, "A withdrawal needs a reason"),
        ({"fact_ids": [kept["id"]], "reason": ""}, "A withdrawal needs a reason"),
        ({"fact_ids": [kept["id"]], "quote": "He said so"}, "Give both said_by"),
        ({"fact_ids": [kept["id"]], "said_by": "He"}, "Give both said_by"),
        ({"fact_ids": [kept["id"]], "actor": " "}, "Say who is making this change"),
    ):
        message = await refused(
            client, "withdraw_fact", **{"actor": "a", "reason": "r", **arguments}
        )
        assert expected in message, message
        assert (await get(client, kept))["status"] == "active"
        assert await tally(client) == before
    assert "Nothing was changed" in await refused(
        client, "withdraw_fact", actor="a", fact_ids=[gone["id"]], reason="again"
    )


async def test_confirming_names_the_person_and_the_time_and_logs_who_did_it(
    client: Client,
) -> None:
    fact = await make_fact(client, "Needs a look")
    before = await tally(client)

    for arguments, expected in (
        ({"person": " "}, "A confirmation needs the person who reviewed the record"),
        ({"person": ""}, "A confirmation needs the person who reviewed the record"),
        ({"actor": " "}, "Say who is making this change"),
        ({"record_id": 987654321}, "There is no fact with id 987654321"),
        ({"kind": "rule"}, "kind must be one of fact, decision"),
        ({"confirmation": "maybe"}, "confirmation must be one of confirmed, unconfirmed"),
        ({"confirmation": "unconfirmed"}, "is not confirmed, so there is nothing to take away"),
    ):
        message = await refused(
            client,
            "set_confirmation",
            **{
                "actor": "agent-1",
                "kind": "fact",
                "record_id": fact["id"],
                "confirmation": "confirmed",
                "person": "Marie",
                **arguments,
            },
        )
        assert expected in message, message
    assert "person: Field required" in await refused(
        client,
        "set_confirmation",
        actor="a",
        kind="fact",
        record_id=fact["id"],
        confirmation="confirmed",
    )
    assert (await get(client, fact))["confirmation"] == "unconfirmed"
    assert await tally(client) == before

    answer = await ok(
        client,
        "set_confirmation",
        actor="agent-1",
        kind="fact",
        record_id=fact["id"],
        confirmation="confirmed",
        person="Marie",
        quote="I read the source",
    )
    reviewed = answer["fact"]
    assert answer["kind"] == "fact" and answer["decision"] is None
    assert (reviewed["confirmation"], reviewed["confirmed_by"]) == ("confirmed", "Marie")
    assert reviewed["confirmed_at"] and reviewed["status"] == "active"
    day = instant(reviewed["confirmed_at"]).date().isoformat()
    assert reviewed["label"] == f"Fact, confirmed by Marie on {day}. Source: The team call"
    # Everything but the confirmation is as it was.
    changed_by_confirming = {"label", "confirmation", "confirmed_by", "confirmed_at"}
    assert {k: v for k, v in reviewed.items() if k not in changed_by_confirming} == {
        k: v for k, v in fact.items() if k not in changed_by_confirming
    }
    assert await get(client, fact) == reviewed
    assert reviewed["id"] in ids((await every(client, confirmation="confirmed"))[0])
    assert reviewed["id"] not in ids((await every(client, confirmation="unconfirmed"))[0])

    # The change is logged with who made it, who the person is, what they said and when.
    event = (await history(client, fact))[-1]
    assert (event["action"], event["actor"]) == ("fact_confirmed", "agent-1")
    assert event["before"] == {"confirmed_by": None}
    assert event["after"]["confirmed_by"] == "Marie" and event["after"]["quote"] == (
        "I read the source"
    )
    assert instant(event["after"]["confirmed_at"]) == instant(reviewed["confirmed_at"])
    assert instant(event["at"]) == instant(reviewed["confirmed_at"])

    # Once confirmed, it cannot be confirmed again, by the same person or another.
    for person in ("Marie", "Ann"):
        message = await refused(
            client,
            "set_confirmation",
            actor="a",
            kind="fact",
            record_id=fact["id"],
            confirmation="confirmed",
            person=person,
        )
        assert f"Fact {fact['id']} was already confirmed by Marie" in message
    assert await get(client, fact) == reviewed

    # The quote is optional when confirming.
    plain = await confirm(client, "fact", await make_fact(client), "Ann")
    assert plain["confirmed_by"] == "Ann"
    assert "quote" not in (await history(client, plain))[-1]["after"]


async def test_taking_a_confirmation_away_needs_the_person_and_their_words(client: Client) -> None:
    fact = await make_fact(client, "Reviewed, then doubted")
    reviewed = await confirm(client, "fact", fact, "Marie")
    away: dict[str, Any] = {
        "actor": "agent-2",
        "kind": "fact",
        "record_id": fact["id"],
        "confirmation": "unconfirmed",
    }
    before = await tally(client)

    assert "needs what the person said" in await refused(
        client, "set_confirmation", person="Marie", **away
    )
    assert "needs what the person said" in await refused(
        client, "set_confirmation", person="Marie", quote="  ", **away
    )
    assert "needs the person who says so" in await refused(
        client, "set_confirmation", person=" ", quote="I no longer think so", **away
    )
    assert await get(client, fact) == reviewed and await tally(client) == before

    answer = await ok(
        client,
        "set_confirmation",
        person="Marie",
        quote="Standup: I am no longer sure",
        **away,
    )
    open_again = answer["fact"]
    assert (open_again["confirmation"], open_again["confirmed_by"], open_again["confirmed_at"]) == (
        "unconfirmed",
        None,
        None,
    )
    assert open_again["label"].startswith("Fact, not confirmed by a person")
    event = (await history(client, fact))[-1]
    assert (event["action"], event["actor"]) == ("fact_unconfirmed", "agent-2")
    assert event["before"]["confirmed_by"] == "Marie"
    assert event["before"]["confirmed_at"]
    assert event["after"] == {
        "confirmed_by": None,
        "said_by": "Marie",
        "quote": "Standup: I am no longer sure",
    }

    # Nothing left to take away; and another person can confirm it afterwards.
    assert "nothing to take away" in await refused(
        client, "set_confirmation", person="Marie", quote="again", **away
    )
    again = await confirm(client, "fact", fact, "Ann")
    assert again["confirmed_by"] == "Ann"
    assert [e["action"] for e in await history(client, fact)] == [
        "fact_recorded",
        "fact_confirmed",
        "fact_unconfirmed",
        "fact_confirmed",
    ]


async def test_an_ended_fact_keeps_the_confirmation_it_had_and_cannot_be_confirmed_again(
    client: Client,
) -> None:
    confirmed_then_withdrawn = await make_fact(client, "Confirmed, then wrong")
    confirmed_then_replaced = await make_fact(client, "Confirmed, then changed")
    plain_withdrawn = await make_fact(client, "Never looked at, then wrong")
    plain_replaced = await make_fact(client, "Never looked at, then changed")
    reviewed = {
        "withdrawn": await confirm(client, "fact", confirmed_then_withdrawn, "Marie"),
        "replaced": await confirm(client, "fact", confirmed_then_replaced, "Ann"),
    }
    await withdraw(
        client,
        confirmed_then_withdrawn,
        said_by="Marie",
        quote="No, it is wrong",
        reason="Marie found the mistake",
    )
    await replace(client, confirmed_then_replaced, said_by="Ann", quote="It changed")
    await withdraw(client, plain_withdrawn)
    await replace(client, plain_replaced)

    # Withdrawing or replacing it did not take the confirmation away: it is still there.
    gone = await get(client, confirmed_then_withdrawn)
    assert (gone["status"], gone["confirmation"], gone["confirmed_by"]) == (
        "withdrawn",
        "confirmed",
        "Marie",
    )
    assert gone["confirmed_at"] == reviewed["withdrawn"]["confirmed_at"]
    assert gone["label"].startswith("Withdrawn fact, confirmed by Marie on ")
    changed = await get(client, confirmed_then_replaced)
    assert (changed["status"], changed["confirmation"], changed["confirmed_by"]) == (
        "superseded",
        "confirmed",
        "Ann",
    )
    assert "fact_confirmed" in [
        e["action"] for e in await history(client, confirmed_then_withdrawn)
    ]
    assert (await get(client, plain_withdrawn))["confirmation"] == "unconfirmed"

    before = await tally(client)
    for fact, expected in (
        (confirmed_then_withdrawn, "was withdrawn (Marie found the mistake)"),
        (plain_withdrawn, "was withdrawn (It was wrong)"),
        (confirmed_then_replaced, "was already superseded by fact"),
        (plain_replaced, "was already superseded by fact"),
    ):
        for confirmation, person in (("confirmed", "Marie"), ("unconfirmed", "Ann")):
            message = await refused(
                client,
                "set_confirmation",
                actor="a",
                kind="fact",
                record_id=fact["id"],
                confirmation=confirmation,
                person=person,
                quote="Whatever",
            )
            assert expected in message and "its confirmation cannot change" in message, message
    assert await tally(client) == before
    assert await get(client, confirmed_then_withdrawn) == gone
    assert await get(client, confirmed_then_replaced) == changed


async def test_a_confirmed_fact_can_only_be_changed_with_that_persons_word(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    fact = await make_fact(client, "Confirmed statement", node_ids=[story["id"]])
    other = await make_fact(client, "Unconfirmed statement", node_ids=[story["id"]])
    reviewed = await confirm(client, "fact", fact, "Marie")
    before = await tally(client)

    attempts: list[tuple[str, dict[str, Any]]] = [
        ("supersede_fact", {"replaces": [fact["id"]], "text": "T", "source": "s"}),
        ("supersede_fact", {"replaces": [other["id"], fact["id"]], "text": "T", "source": "s"}),
        ("withdraw_fact", {"fact_ids": [fact["id"]]}),
        ("withdraw_fact", {"fact_ids": [other["id"], fact["id"]]}),
    ]
    for tool, arguments in attempts:
        # Not even if the agent is called by the person's own name: a name alone grants nothing.
        for actor in ("agent-2", "Marie"):
            message = await refused(client, tool, actor=actor, reason="r", **arguments)
            assert f"fact {fact['id']} (confirmed by Marie)" in message
            assert "needs that person's word" in message
            assert "said_by" in message and "quote" in message and "Nothing was changed" in message
        assert "Give both said_by" in await refused(
            client, tool, actor="a", reason="r", said_by="Marie", **arguments
        )
        assert "Give both said_by" in await refused(
            client, tool, actor="a", reason="r", quote="She said so", **arguments
        )
        # A refused call changed nothing, not even the unconfirmed fact that was in the same call.
        assert await get(client, fact) == reviewed
        assert (await get(client, other))["status"] == "active"
        assert await tally(client) == before

    # With the person's name and words it goes through, and the history keeps both people.
    change = await ok(
        client,
        "supersede_fact",
        actor="agent-2",
        replaces=[fact["id"], other["id"]],
        reason="Marie changed her mind",
        text="The revised statement",
        source="Planning call",
        said_by="Marie",
        quote="In the planning call: 'that is no longer how we think about it'",
    )
    assert change["superseded_ids"] == [fact["id"], other["id"]]
    for old in (fact, other):
        event = (await history(client, old))[-1]
        assert (event["action"], event["actor"]) == ("fact_superseded", "agent-2")
        assert event["after"]["said_by"] == "Marie"
        assert event["after"]["quote"].startswith("In the planning call")
    # The confirmation of the replaced fact is kept as it was; the replacement has none.
    assert (await get(client, fact))["confirmed_by"] == "Marie"
    assert change["replacement"]["confirmation"] == "unconfirmed"

    # A fact nobody confirmed can be withdrawn by an agent alone, with no one's word.
    suggestion = await make_fact(client, "Just heard")
    await withdraw(client, suggestion, actor="agent-3")
    assert "said_by" not in (await history(client, suggestion))[-1]["after"]

    # A withdrawal of a confirmed one by the person's word.
    third = await make_fact(client, "Another confirmed one")
    await confirm(client, "fact", third, "Ann")
    await withdraw(client, third, actor="agent-3", said_by="Ann", quote="It was never true")
    event = (await history(client, third))[-1]
    assert (event["action"], event["actor"], event["after"]["said_by"]) == (
        "fact_withdrawn",
        "agent-3",
        "Ann",
    )
    assert event["after"]["quote"] == "It was never true"
    assert (await get(client, third))["confirmed_by"] == "Ann"


async def test_what_a_fact_concerns_can_be_changed_while_it_is_active(client: Client) -> None:
    first, second = await make_stories(client, 2)
    rule = await make_rule(client, story_id=first["id"])
    decision = await make_decision(client, "A decision")
    fact = await make_fact(client, "A fact", node_ids=[first["id"]])

    linked = await ok(
        client,
        "link_fact",
        actor="agent-1",
        fact_id=fact["id"],
        node_ids=[second["id"]],
        rule_ids=[rule["id"]],
        decision_ids=[decision["id"]],
    )
    assert linked["node_ids"] == sorted([first["id"], second["id"]])
    assert linked["rule_ids"] == [rule["id"]] and linked["decision_ids"] == [decision["id"]]
    assert fact["id"] in ids((await every(client, node_id=second["id"]))[0])
    assert fact["id"] in ids(facts_in((await recall(client, node_id=second["id"]))["facts"]))
    unlinked = await ok(
        client, "unlink_fact", actor="bob", fact_id=fact["id"], node_ids=[first["id"]]
    )
    assert unlinked["node_ids"] == [second["id"]]
    assert fact["id"] not in ids(facts_in((await recall(client, node_id=first["id"]))["facts"]))
    assert [(e["action"], e["actor"]) for e in await history(client, fact)] == [
        ("fact_recorded", "agent-1"),
        ("fact_linked", "agent-1"),
        ("fact_linked", "agent-1"),
        ("fact_linked", "agent-1"),
        ("fact_linked", "agent-1"),
        ("fact_unlinked", "bob"),
    ]
    node_events = (await ok(client, "node_history", node_id=first["id"]))["items"]
    assert [e["action"] for e in node_events if e["fact_id"] == fact["id"]] == [
        "fact_linked",
        "fact_unlinked",
    ]
    decision_events = (await ok(client, "decision_history", decision_id=decision["id"]))["items"]
    assert [e["action"] for e in decision_events if e["fact_id"] == fact["id"]] == ["fact_linked"]

    # Changing what a confirmed fact concerns is not changing the fact: it needs no one's word, and
    # it keeps its confirmation.
    await confirm(client, "fact", fact, "Marie")
    relinked = await ok(client, "link_fact", actor="a", fact_id=fact["id"], node_ids=[first["id"]])
    assert (relinked["confirmed_by"], relinked["node_ids"]) == (
        "Marie",
        sorted([first["id"], second["id"]]),
    )

    arguments: dict[str, Any] = {"actor": "a", "fact_id": fact["id"]}
    before = await tally(client)
    for tool, extra, expected in (
        ("link_fact", {"node_ids": [first["id"]]}, "already linked to node"),
        ("link_fact", {"rule_ids": [rule["id"]]}, "already linked to rule"),
        ("link_fact", {"decision_ids": [decision["id"]]}, "already linked to decision"),
        ("unlink_fact", {"node_ids": [987654321]}, "is not linked to node"),
        ("unlink_fact", {"rule_ids": [987654321]}, "is not linked to rule"),
        ("link_fact", {"node_ids": [987654321]}, "There is no node with id 987654321"),
        ("link_fact", {"rule_ids": [987654321]}, "There is no rule with id 987654321"),
        ("link_fact", {"decision_ids": [987654321]}, "There is no decision with id 987654321"),
        ("link_fact", {}, "Nothing to link"),
        ("unlink_fact", {}, "Nothing to link"),
        ("link_fact", {"node_ids": [first["id"]], "actor": " "}, "Say who is making this change"),
    ):
        assert expected in await refused(client, tool, **{**arguments, **extra})
    assert "There is no fact with id 987654321" in await refused(
        client, "link_fact", actor="a", fact_id=987654321, node_ids=[first["id"]]
    )
    # A call that is half right changes nothing.
    await refused(
        client,
        "link_fact",
        actor="a",
        fact_id=fact["id"],
        node_ids=[first["id"]],
        rule_ids=[987654321],
    )
    assert await tally(client) == before

    # A fact that has ended keeps the links it had, as a record, and its replacement is the one to
    # change.
    ended = await make_fact(client, "Will end", node_ids=[first["id"]])
    await withdraw(client, ended)
    for tool in ("link_fact", "unlink_fact"):
        message = await refused(
            client, tool, actor="a", fact_id=ended["id"], node_ids=[second["id"], first["id"]]
        )
        assert (
            "keeps the links it had" in message and f"Fact {ended['id']} was withdrawn" in message
        )
    assert (await get(client, ended))["node_ids"] == [first["id"]]


async def test_a_facts_history_lists_everything_in_order_with_who_said_what(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    fact = await make_fact(client, "A long life", node_ids=[story["id"]])
    await confirm(client, "fact", fact, "Marie", quote="Looks right", actor="agent-2")
    await ok(
        client,
        "set_confirmation",
        actor="agent-3",
        kind="fact",
        record_id=fact["id"],
        confirmation="unconfirmed",
        person="Marie",
        quote="Not sure any more",
    )
    await confirm(client, "fact", fact, "Ann", actor="agent-4")
    await replace(client, fact, actor="agent-5", said_by="Ann", quote="It changed, she said")

    events = await history(client, fact)
    assert [(e["action"], e["actor"]) for e in events] == [
        ("fact_recorded", "agent-1"),
        ("fact_linked", "agent-1"),
        ("fact_confirmed", "agent-2"),
        ("fact_unconfirmed", "agent-3"),
        ("fact_confirmed", "agent-4"),
        ("fact_superseded", "agent-5"),
    ]
    assert ids(events) == sorted(ids(events))
    assert all(event["fact_id"] == fact["id"] for event in events)
    moments = [instant(event["at"]) for event in events]
    assert moments == sorted(moments)
    # Who the person was and what they said is in each event that carries a person's word.
    assert (events[2]["after"]["confirmed_by"], events[2]["after"]["quote"]) == (
        "Marie",
        "Looks right",
    )
    assert (events[3]["after"]["said_by"], events[3]["after"]["quote"]) == (
        "Marie",
        "Not sure any more",
    )
    assert events[4]["after"]["confirmed_by"] == "Ann"
    assert (events[5]["after"]["said_by"], events[5]["after"]["quote"]) == (
        "Ann",
        "It changed, she said",
    )

    # It can be read a page at a time.
    first = await ok(client, "fact_history", fact_id=fact["id"], limit=4)
    assert ids(first["items"]) == ids(events[:4]) and first["next_after_id"] == events[3]["id"]
    rest = await ok(
        client, "fact_history", fact_id=fact["id"], limit=4, after_id=first["next_after_id"]
    )
    assert ids(rest["items"]) == ids(events[4:]) and rest["next_after_id"] is None
    # And the feed of changes can be narrowed to one fact.
    feed = await ok(client, "changes_since", fact_id=fact["id"], limit=200)
    assert ids(feed["items"]) == ids(events)

    assert "There is no fact with id 987654321" in await refused(
        client, "fact_history", fact_id=987654321
    )
    assert "There is no fact with id 987654321" in await refused(
        client, "get_fact", fact_id=987654321
    )


async def test_listing_walks_every_page_once_and_the_count_matches(client: Client) -> None:
    epic, story, task = await make_tree(client)
    rule = await make_rule(client, story_id=story["id"])
    other_epic, other_story, _ = await make_tree(client)
    decision = await make_decision(client, "A linked decision")
    on_task = [await make_fact(client, f"On a task {n}", node_ids=[task["id"]]) for n in range(20)]
    on_story = [
        await make_fact(client, f"On a story {n}", node_ids=[story["id"]]) for n in range(20)
    ]
    on_rule = [await make_fact(client, f"On a rule {n}", rule_ids=[rule["id"]]) for n in range(20)]
    on_decision = [
        await make_fact(
            client, f"On a decision {n}", decision_ids=[decision["id"]], node_ids=[story["id"]]
        )
        for n in range(2)
    ]
    elsewhere = [
        await make_fact(client, f"Elsewhere {n}", node_ids=[other_story["id"]]) for n in range(5)
    ]
    everything = [*on_task, *on_story, *on_rule, *on_decision]

    # A walk in pages of 25 gives each fact once and the count the first page promised.
    found, total = await every(client, within_node_id=epic["id"])
    assert ids(found) == ids(everything) and total == 62
    assert len(set(ids(found))) == len(found)
    page = await ok(client, "list_facts", within_node_id=epic["id"], limit=25)
    assert len(page["items"]) == 25 and page["next_after_id"] == page["items"][-1]["id"]
    assert page["total"] == 62
    assert (await ok(client, "list_facts", within_node_id=epic["id"], limit=200))[
        "next_after_id"
    ] is None

    # `within` takes the item, what is under it, and the rules in its stories: and nothing else.
    for node, expected in (
        (story, [*on_task, *on_story, *on_rule, *on_decision]),
        (task, on_task),
        (other_epic, elsewhere),
    ):
        found, total = await every(client, within_node_id=node["id"])
        assert ids(found) == ids(expected) and total == len(expected)
    # Exact filters stay exact.
    assert ids((await every(client, node_id=story["id"]))[0]) == ids([*on_story, *on_decision])
    assert ids((await every(client, rule_id=rule["id"]))[0]) == ids(on_rule)
    assert ids((await every(client, decision_id=decision["id"]))[0]) == ids(on_decision)
    assert (await every(client, node_id=epic["id"]))[1] == 0

    # Status and confirmation, alone and together with the others.
    await withdraw(client, on_task[0], reason="Wrong")
    await replace(client, on_task[1], reason="Changed")
    for fact in (on_task[2], on_task[3], on_task[4]):
        await confirm(client, "fact", fact, "Marie")
    await replace(client, on_task[3], said_by="Marie", quote="Changed", reason="Changed again")
    in_task = {"node_id": task["id"]}
    assert ids((await every(client, status="withdrawn", **in_task))[0]) == [on_task[0]["id"]]
    superseded, count = await every(client, status="superseded", **in_task)
    assert ids(superseded) == [on_task[1]["id"], on_task[3]["id"]] and count == 2
    # 20 facts, one withdrawn and two replaced (by two new ones that concern the same task).
    active, active_count = await every(client, status="active", **in_task)
    assert active_count == len(active) == 19
    confirmed, confirmed_count = await every(client, confirmation="confirmed", **in_task)
    assert ids(confirmed) == ids([on_task[2], on_task[3], on_task[4]]) and confirmed_count == 3
    both, both_count = await every(client, confirmation="confirmed", status="active", **in_task)
    assert ids(both) == ids([on_task[2], on_task[4]]) and both_count == 2
    unconfirmed, _ = await every(client, confirmation="unconfirmed", status="superseded", **in_task)
    assert ids(unconfirmed) == [on_task[1]["id"]]

    # Every fact in the project, walked, is as many as the count says.
    everyone, total = await every(client)
    assert len(everyone) == total and len(set(ids(everyone))) == total
    assert ids(everyone) == sorted(ids(everyone))

    for arguments, wanted in (
        ({"status": "gone"}, "status must be one of active, superseded, withdrawn"),
        ({"confirmation": "maybe"}, "confirmation must be one of confirmed, unconfirmed"),
        ({"node_id": 987654321}, "There is no node with id 987654321"),
        ({"rule_id": 987654321}, "There is no rule with id 987654321"),
        ({"decision_id": 987654321}, "There is no decision with id 987654321"),
        ({"within_node_id": 987654321}, "There is no node with id 987654321"),
        ({"limit": 0}, "limit must be a whole number from 1 to 200"),
        ({"limit": 201}, "limit must be a whole number from 1 to 200"),
    ):
        assert wanted in await refused(client, "list_facts", **arguments)


async def test_recall_gives_what_the_exact_links_give_and_says_why_each_fact_is_there(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    other_story = (await make_stories(client, 1))[0]
    rule = await make_rule(client, story_id=story["id"])
    fact = await make_fact(client, "Linked fact", node_ids=[story["id"]])
    confirmed = await make_fact(client, "Linked and confirmed", node_ids=[story["id"]])
    await confirm(client, "fact", confirmed, "Marie")
    elsewhere = await make_fact(client, "Linked elsewhere", node_ids=[other_story["id"]])
    parent_fact = await make_fact(client, "Linked to the epic", node_ids=[epic["id"]])
    child_fact = await make_fact(client, "Linked to the task", node_ids=[task["id"]])
    rule_fact = await make_fact(client, "Linked to the rule", rule_ids=[rule["id"]])
    replaced = await make_fact(client, "Old fact", node_ids=[story["id"]])
    new = (await replace(client, replaced, reason="Changed", keep_links=False))["replacement"]
    withdrawn = await make_fact(client, "Wrong fact", node_ids=[story["id"]])
    await withdraw(client, withdrawn, reason="It was wrong")

    result = await recall(client, node_id=story["id"])

    assert ids(facts_in(result["facts"])) == [fact["id"], confirmed["id"]]
    assert [entry["why"] for entry in result["facts"]["items"]] == [
        "linked directly to this node"
    ] * 2
    # Each entry says whether a person confirmed it, and what it is, in words.
    assert [
        (entry["confirmation"], entry["confirmed_by"], entry["status"])
        for entry in facts_in(result["facts"])
    ] == [("unconfirmed", None, "active"), ("confirmed", "Marie", "active")]
    assert facts_in(result["facts"])[0]["label"].startswith("Fact, not confirmed by a person")
    assert facts_in(result["facts"])[1]["label"].startswith("Fact, confirmed by Marie on ")
    # Neither the parent's nor the children's facts are inferred.
    for left_out in (elsewhere, parent_fact, child_fact, rule_fact, new):
        assert left_out["id"] not in ids(facts_in(result["facts"]))
    past = {entry["fact"]["id"]: entry["why"] for entry in result["past_facts"]["items"]}
    assert past[replaced["id"]] == f"linked directly to this node; replaced by fact {new['id']}"
    assert past[withdrawn["id"]] == "linked directly to this node; withdrawn: It was wrong"
    assert (result["facts"]["count"], result["past_facts"]["count"]) == (2, 2)
    assert "similar" in result and result["similar"] is None

    # The same item reached as a rule gives the rule's own links.
    for_rule = await recall(client, rule_id=rule["id"])
    assert ids(facts_in(for_rule["facts"])) == [rule_fact["id"]]
    assert for_rule["active_decisions"]["count"] == 0 and for_rule["past_facts"]["count"] == 0

    for_task = await recall(client, node_id=task["id"])
    assert ids(facts_in(for_task["facts"])) == [child_fact["id"]]
    empty_story = (await make_stories(client, 1))[0]
    empty = await recall(client, node_id=empty_story["id"])
    assert all(
        empty[name]["items"] == [] and empty[name]["count"] == 0
        for name in ("active_decisions", "facts", "past_decisions", "past_facts")
    )


async def test_each_recall_section_pages_on_its_own_and_bad_asks_are_refused(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    facts = [await make_fact(client, f"Fact {n}", node_ids=[story["id"]]) for n in range(3)]
    for number in range(3):
        old = await make_fact(client, f"Old {number}", node_ids=[story["id"]])
        await withdraw(client, old)

    both = await recall(client, node_id=story["id"], limit=2)
    assert ids(facts_in(both["facts"])) == ids(facts[:2])
    assert both["facts"]["count"] == 3 and both["facts"]["next_after_id"] == facts[1]["id"]
    rest = await recall(
        client,
        node_id=story["id"],
        limit=2,
        show="facts",
        after_id=both["facts"]["next_after_id"],
    )
    assert ids(facts_in(rest["facts"])) == [facts[2]["id"]]
    assert rest["facts"]["next_after_id"] is None
    assert rest["past_facts"]["items"] == [] and rest["past_facts"]["count"] == 3

    past = await recall(client, node_id=story["id"], show="past_facts", limit=2)
    assert len(past["past_facts"]["items"]) == 2 and past["past_facts"]["next_after_id"] is not None
    assert past["facts"]["items"] == []

    assert "after_id only works for one section at a time" in await refused(
        client, "recall", node_id=story["id"], after_id=1
    )
    assert "show must be one of all, active_decisions, project_wide_decisions, facts" in (
        await refused(client, "recall", node_id=story["id"], show="beliefs")
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


async def test_a_secret_in_what_a_fact_says_is_refused_and_nothing_is_stored(
    client: Client,
) -> None:
    secret = "ghp_" + "a1B2c3D4" * 5
    fact = await make_fact(client, "Calm statement")
    before = await tally(client)

    cases: list[tuple[str, dict[str, Any]]] = [
        ("retain_fact", {"actor": "a", "text": f"The key is {secret}", "source": "s"}),
        ("retain_fact", {"actor": "a", "text": "t", "source": f"Pasted: {secret}"}),
        (
            "supersede_fact",
            {
                "actor": "a",
                "replaces": [fact["id"]],
                "reason": f"r {secret}",
                "text": "t",
                "source": "s",
            },
        ),
        (
            "supersede_fact",
            {
                "actor": "a",
                "replaces": [fact["id"]],
                "reason": "r",
                "text": f"{secret}",
                "source": "s",
            },
        ),
        (
            "supersede_fact",
            {"actor": "a", "replaces": [fact["id"]], "reason": "r", "text": "t", "source": secret},
        ),
        ("withdraw_fact", {"actor": "a", "fact_ids": [fact["id"]], "reason": f"Leaked {secret}"}),
        (
            "withdraw_fact",
            {
                "actor": "a",
                "fact_ids": [fact["id"]],
                "reason": "r",
                "said_by": "Marie",
                "quote": f"I said {secret}",
            },
        ),
        (
            "set_confirmation",
            {
                "actor": "a",
                "kind": "fact",
                "record_id": fact["id"],
                "confirmation": "confirmed",
                "person": "Marie",
                "quote": secret,
            },
        ),
    ]
    for tool, arguments in cases:
        message = await refused(client, tool, **arguments)
        assert "contains what looks like a GitHub token" in message, message
        assert "Take the secret out and try again" in message
        assert secret not in message and secret[:12] not in message

    assert await tally(client) == before
    assert await get(client, fact) == fact
    for table_and_column in ("facts WHERE text", "facts WHERE source", "facts WHERE status_reason"):
        found = run_sql(
            f"SELECT count(*) FROM {table_and_column} LIKE '%{secret[:12]}%'", plain=True
        )
        assert found.stdout.strip() == "0", found.stderr
    found = run_sql(
        f"SELECT count(*) FROM events WHERE after::text LIKE '%{secret[:12]}%'", plain=True
    )
    assert found.stdout.strip() == "0", found.stderr


async def test_two_replacements_of_the_same_fact_at_once_let_only_one_win(client: Client) -> None:
    for _ in range(6):
        fact = await make_fact(client, "Contested")
        results = await race_results(
            client,
            "supersede_fact",
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
        winner, *rest = (await ok(client, "list_facts", after_id=fact["id"], limit=10))["items"]
        assert winner["supersedes_ids"] == [fact["id"]] and rest == []


async def test_a_withdrawal_and_replacements_at_once_end_the_fact_only_once(client: Client) -> None:
    for _ in range(6):
        fact = await make_fact(client, "Contested")
        results = await race_mixed(
            client,
            [
                (
                    "supersede_fact",
                    {
                        "actor": "agent-1",
                        "replaces": [fact["id"]],
                        "reason": "Changed",
                        "text": "Replacement",
                        "source": "s",
                    },
                ),
                *(
                    (
                        "withdraw_fact",
                        {"actor": f"agent-{n}", "fact_ids": [fact["id"]], "reason": "Wrong"},
                    )
                    for n in (2, 3)
                ),
            ],
        )
        assert [accepted for accepted, _ in results].count(True) == 1, results
        for accepted, message in results:
            assert accepted or "was withdrawn" in message or "was already superseded" in message
        endings = [
            e
            for e in await history(client, fact)
            if e["action"] in ("fact_superseded", "fact_withdrawn")
        ]
        assert len(endings) == 1
        assert (await get(client, fact))["status"] in ("superseded", "withdrawn")


async def test_confirming_and_withdrawing_at_once_never_leave_a_withdrawn_fact_confirmed(
    client: Client,
) -> None:
    for _ in range(6):
        fact = await make_fact(client, "Raced")
        results = await race_mixed(
            client,
            [
                (
                    "set_confirmation",
                    {
                        "actor": "agent-1",
                        "kind": "fact",
                        "record_id": fact["id"],
                        "confirmation": "confirmed",
                        "person": "Marie",
                    },
                ),
                ("withdraw_fact", {"actor": "agent-2", "fact_ids": [fact["id"]], "reason": "No"}),
            ],
        )
        # If the person's confirmation lands first, an agent alone can no longer withdraw it; if the
        # withdrawal lands first, there is nothing left to confirm. Exactly one of them stands.
        assert [accepted for accepted, _ in results].count(True) == 1, results
        now = await get(client, fact)
        assert (now["status"], now["confirmation"]) in {
            ("active", "confirmed"),
            ("withdrawn", "unconfirmed"),
        }
        actions = [e["action"] for e in await history(client, fact)]
        assert actions in (
            ["fact_recorded", "fact_confirmed"],
            ["fact_recorded", "fact_withdrawn"],
        )


async def test_two_confirmations_at_once_are_recorded_once(client: Client) -> None:
    for _ in range(4):
        fact = await make_fact(client, "Reviewed twice at once")
        results = await race_results(
            client,
            "set_confirmation",
            [
                {
                    "actor": f"agent-{number}",
                    "kind": "fact",
                    "record_id": fact["id"],
                    "confirmation": "confirmed",
                    "person": person,
                }
                for number, person in enumerate(("Marie", "Ann", "Bob"))
            ],
        )
        assert [accepted for accepted, _ in results].count(True) == 1, results
        for accepted, message in results:
            assert accepted or "was already confirmed by" in message, message
        confirmed_events = [
            e for e in await history(client, fact) if e["action"] == "fact_confirmed"
        ]
        assert len(confirmed_events) == 1
        assert (await get(client, fact))["confirmed_by"] == confirmed_events[0]["after"][
            "confirmed_by"
        ]
