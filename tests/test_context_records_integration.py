"""M16's proof for the backend, through real MCP clients against the running containers: a fact
with its source and a decision with its reason and decider are found with the same ids and fields
by every way of reading; confirmation and lifecycle are two separate things and the history keeps
both; blank sources and reasons and unauthorised changes are refused without leaving part of a
write behind; and a project-wide decision is in its own project's briefings and recall and no
other project's."""

import json
import urllib.error
import urllib.request
from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    confirm,
    connect,
    make_decision,
    make_fact,
    make_rule,
    make_stories,
    make_tree,
    ok,
    refused,
    unique_word,
)
from tests.world import make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def ids(items: list[dict[str, Any]]) -> list[int]:
    return [item["id"] for item in items]


async def every(client: Client, tool: str, **filters: Any) -> list[dict[str, Any]]:
    """Every item a list tool gives, following its pages."""
    found: list[dict[str, Any]] = []
    after_id = None
    while True:
        page = await ok(client, tool, limit=200, after_id=after_id, **filters)
        found.extend(page["items"])
        after_id = page["next_after_id"]
        if after_id is None:
            return found


async def everything_known(client: Client) -> dict[str, Any]:
    """All the facts, decisions and events of the project, so that two moments can be compared."""
    counts = (await ok(client, "server_status"))["counts"]
    return {
        "facts": await every(client, "list_facts"),
        "decisions": await every(client, "list_decisions"),
        "counts": counts,
    }


async def briefing(client: Client, epic: dict[str, Any]) -> dict[str, Any]:
    """The context an agent would be given for the free work under this epic, looked at only."""
    answer = await ok(client, "next_work", actor="reader", within_node=epic["ref"], peek=True)
    assert answer["found"] and answer["claim"] is None, answer["message"]
    return dict(answer["context"])


async def mentions(client: Client, ref: str) -> list[dict[str, Any]]:
    """Every mention of one epic, story or task (all of them fit one page here)."""
    page = await ok(client, "find_mentions", node=ref, limit=200)
    assert page["next_offset"] is None
    return list(page["items"])


def shared(record: dict[str, Any], other: dict[str, Any]) -> bool:
    """Whether every field that both of two shapes of one record have holds the same value."""
    return all(other[name] == value for name, value in record.items() if name in other)


# -- (a) the same ids and fields wherever a fact or a decision is read -----------------------------


async def test_a_fact_and_a_decision_have_the_same_ids_and_fields_in_every_read(
    client: Client,
) -> None:
    word = unique_word()
    epic, story, task = await make_tree(client)
    rule = await make_rule(client, story=story["ref"])
    decision = await ok(
        client,
        "record_decision",
        actor="agent-1",
        title=f"Keep one database {word}",
        text=f"One PostgreSQL database holds everything {word}",
        reason="Fewer moving parts to run",
        decided_by="Marie",
        decided_on="2026-11-01",
        nodes=[story["ref"]],
        rule_ids=[rule["id"]],
    )
    fact = await ok(
        client,
        "retain_fact",
        actor="agent-2",
        text=f"The build takes four minutes {word}",
        source="Timed on Marie's laptop",
        nodes=[task["ref"]],
        rule_ids=[rule["id"]],
        decision_ids=[decision["id"]],
    )
    # Said as it was recorded: the source, the reason and the named decider are there.
    assert (fact["source"], fact["recorded_by"]) == ("Timed on Marie's laptop", "agent-2")
    assert (decision["reason"], decision["decided_by"], decision["recorded_at"] != "") == (
        "Fewer moving parts to run",
        "Marie",
        True,
    )

    # The fact: get, list (by each thing it concerns and by area), recall and the briefing.
    assert await ok(client, "get_fact", fact_id=fact["id"]) == fact
    for filters in (
        {"node": task["ref"]},
        {"rule_id": rule["id"]},
        {"decision_id": decision["id"]},
        {"within_node": epic["ref"]},
        {"within_node": story["ref"]},
    ):
        assert await every(client, "list_facts", **filters) == [fact], filters
    for target in ({"node": task["ref"]}, {"rule_id": rule["id"]}):
        recalled = (await ok(client, "recall", **target))["facts"]["items"]
        assert [entry["fact"] for entry in recalled] == [fact], target
        assert recalled[0]["why"].startswith("linked directly to this")
    for target in ({"node": story["ref"]},):
        assert (await ok(client, "recall", **target))["facts"]["items"] == []

    # The decision: the same.
    assert await ok(client, "get_decision", decision_id=decision["id"]) == decision
    listed = await every(client, "list_decisions")
    assert [d for d in listed if d["id"] == decision["id"]] == [decision]
    for target in ({"node": story["ref"]}, {"rule_id": rule["id"]}):
        recalled = (await ok(client, "recall", **target))["active_decisions"]["items"]
        assert [entry["decision"] for entry in recalled] == [decision], target
    # What the decision says about its facts: the fact names the decision, not the other way round.
    assert (await ok(client, "get_decision", decision_id=decision["id"]))["nodes"] == [story["ref"]]
    assert await every(client, "list_facts", decision_id=decision["id"]) == [fact]

    # The briefing an agent is given for the task holds both, with the same fields.
    taken = await ok(client, "next_work", actor="ann", within_node=epic["ref"])
    assert taken["work"]["ref"] == task["ref"]
    context = taken["context"]
    (in_briefing,) = [d for d in context["active_decisions"] if d["id"] == decision["id"]]
    assert shared(decision, in_briefing) and in_briefing["linked_to"] == [story["ref"]]
    assert {"title", "text", "reason", "decided_by", "decided_on", "confirmation"} <= set(
        in_briefing
    )
    (fact_in_briefing,) = context["facts"]
    assert shared(fact, fact_in_briefing) and fact_in_briefing["linked_to"] == [task["ref"]]
    assert {"text", "source", "recorded_by", "confirmation"} <= set(fact_in_briefing)

    # Search by exact words finds the same ids, and says what each is.
    found = await ok(client, "search", query=word, mode="words", limit=50)
    by_type = {(item["item_type"], item["id"]): item for item in found["items"]}
    assert set(by_type) == {("decision", decision["id"]), ("fact", fact["id"])}
    assert by_type[("fact", fact["id"])]["confirmation"] == "unconfirmed"
    assert by_type[("decision", decision["id"])]["confirmation"] == "unconfirmed"
    assert by_type[("fact", fact["id"])]["label"] == fact["label"]
    assert by_type[("decision", decision["id"])]["label"] == decision["label"]

    # A reference to either from other text finds its way back to it.
    await make_decision(
        client,
        f"Leans on both {word}",
        text=f"See [[fact:{fact['id']}]] and [[decision:{decision['id']}]]",
    )
    for target in ({"fact_id": fact["id"]}, {"decision_id": decision["id"]}):
        mentions = (await ok(client, "find_mentions", **target))["items"]
        assert [(m["source"]["item_type"], m["fields"]) for m in mentions] == [
            ("decision", ["text"])
        ]


# -- (b) confirmation and lifecycle are two things, and the history keeps both ---------------------


async def test_confirmation_and_lifecycle_are_told_apart_and_the_history_keeps_both(
    client: Client,
) -> None:
    epic, story, _ = await make_tree(client)
    on_story = {"nodes": [story["ref"]]}

    async def a_fact(text: str, confirmed_by: str | None, ending: str | None) -> dict[str, Any]:
        fact = await make_fact(client, text, **on_story)
        if confirmed_by:
            await confirm(client, "fact", fact, confirmed_by, quote="Looked at it")
        word = {"superseded": "supersede_fact", "withdrawn": "withdraw_fact"}.get(ending or "")
        person = {"said_by": confirmed_by, "quote": "That is how it is"} if confirmed_by else {}
        if word == "supersede_fact":
            await ok(
                client,
                word,
                actor="agent-9",
                replaces=[fact["id"]],
                reason="It changed",
                text=f"{text}, newer",
                source="The newer source",
                keep_links=False,
                **person,
            )
        elif word == "withdraw_fact":
            await ok(client, word, actor="agent-9", fact_ids=[fact["id"]], reason="Wrong", **person)
        return await ok(client, "get_fact", fact_id=fact["id"])

    async def a_decision(
        title: str, confirmed_by: str | None, ending: str | None
    ) -> dict[str, Any]:
        decision = await make_decision(client, title, **on_story)
        if confirmed_by:
            await confirm(client, "decision", decision, confirmed_by, quote="Looked at it")
        person = {"said_by": confirmed_by, "quote": "That is how it is"} if confirmed_by else {}
        if ending == "superseded":
            await ok(
                client,
                "supersede_decision",
                actor="agent-9",
                decision_id=decision["id"],
                title=f"{title}, newer",
                text="t",
                reason="It changed",
                decided_by="Ann",
                keep_links=False,
                **person,
            )
        elif ending == "withdrawn":
            await ok(
                client,
                "withdraw_decision",
                actor="agent-9",
                decision_id=decision["id"],
                reason="Wrong",
                **person,
            )
        return await ok(client, "get_decision", decision_id=decision["id"])

    cases = [
        ("active", None),
        ("active", "Marie"),
        ("superseded", None),
        ("superseded", "Ann"),
        ("withdrawn", None),
        ("withdrawn", "Bob"),
    ]
    facts = [
        await a_fact(f"Fact {number}", who, ending) for number, (ending, who) in enumerate(cases)
    ]
    decisions = [
        await a_decision(f"Decision {number}", who, ending)
        for number, (ending, who) in enumerate(cases)
    ]

    for records, noun in ((facts, "fact"), (decisions, "decision")):
        # Each record says both where it stands in its life and whether a person confirmed it.
        for record, (ending, who) in zip(records, cases, strict=True):
            assert record["status"] == (ending or "active")
            assert record["confirmation"] == ("confirmed" if who else "unconfirmed")
            assert record["confirmed_by"] == who and bool(record["confirmed_at"]) == bool(who)
        # The six are told apart in words, by anyone who reads only the label.
        labels = [record["label"].split(". Source")[0] for record in records]
        assert len(set(labels)) == 6, labels
        assert labels[0] == f"{noun.capitalize()}, not confirmed by a person"
        assert labels[2].startswith(f"Superseded {noun}, not confirmed by a person")
        assert labels[3].startswith(f"Superseded {noun}, confirmed by Ann on ")
        assert labels[5].startswith(f"Withdrawn {noun}, confirmed by Bob on ")
        # The lists can ask for either side on its own, or both.
        tool = "list_facts" if noun == "fact" else "list_decisions"
        mine = set(ids(records))
        for ending in ("active", "superseded", "withdrawn"):
            for confirmation in ("confirmed", "unconfirmed"):
                found = await every(client, tool, status=ending, confirmation=confirmation)
                expected = [
                    r["id"]
                    for r, (e, w) in zip(records, cases, strict=True)
                    if e == ending and bool(w) == (confirmation == "confirmed")
                ]
                assert [i for i in ids(found) if i in mine] == expected, (ending, confirmation)

    # Recall gives the ones in force and the ones that are not in different sections, each saying
    # both things.
    recalled = await ok(client, "recall", node=story["ref"], limit=200)
    for section, noun, records in (
        ("facts", "fact", facts),
        ("past_facts", "fact", facts),
        ("active_decisions", "decision", decisions),
        ("past_decisions", "decision", decisions),
    ):
        shown = {entry[noun]["id"]: entry[noun] for entry in recalled[section]["items"]}
        past = section.startswith("past")
        for record, (ending, who) in zip(records, cases, strict=True):
            if (ending != "active") == past:
                assert shown[record["id"]]["status"] == (ending or "active")
                assert shown[record["id"]]["confirmed_by"] == who
            else:
                assert record["id"] not in shown
    # The briefing has only what is in force, with the confirmation of each.
    context = await briefing(client, epic)
    in_briefing = {d["id"]: d for d in context["active_decisions"]}
    assert in_briefing[decisions[0]["id"]]["confirmation"] == "unconfirmed"
    assert in_briefing[decisions[1]["id"]]["confirmed_by"] == "Marie"
    assert {d["id"] for d in context["past_decisions"]} >= set(ids(decisions[2:]))
    assert all(d["status"] != "active" for d in context["past_decisions"])
    facts_in_briefing = {f["id"]: f for f in context["facts"]}
    assert facts_in_briefing[facts[1]["id"]]["confirmation"] == "confirmed"
    assert facts_in_briefing[facts[0]["id"]]["confirmation"] == "unconfirmed"
    for ended in facts[2:]:
        assert ended["id"] not in facts_in_briefing

    # What was confirmed before a record ended is in its history, with who and when, next to the
    # ending and the person whose word ended it.
    for record, noun, tool in (
        (facts[3], "fact", "fact_history"),
        (decisions[5], "decision", "decision_history"),
    ):
        events = (await ok(client, tool, **{f"{noun}_id": record["id"]}))["items"]
        actions = [e["action"] for e in events]
        assert actions[:1] == [f"{noun}_recorded"] and actions[-2:] == [
            f"{noun}_confirmed",
            actions[-1],
        ]
        confirmed_event, ended_event = events[-2:]
        assert confirmed_event["after"]["confirmed_by"] == record["confirmed_by"]
        assert confirmed_event["after"]["confirmed_at"]
        assert confirmed_event["actor"] == "agent-1"
        assert ended_event["actor"] == "agent-9"
        assert ended_event["after"]["said_by"] == record["confirmed_by"]
        assert ended_event["after"]["quote"] == "That is how it is"
        assert ended_event["at"] >= confirmed_event["at"]
        # And the feed of changes shows them in the same order.
        feed = await ok(client, "changes_since", **{f"{noun}_id": record["id"]})
        assert ids(feed["items"]) == ids(events)


# -- (c) refusals leave nothing half done ----------------------------------------------------------


def raw_tool_call(
    url: str, key: str | None, tool: str, arguments: dict[str, Any]
) -> tuple[int, str]:
    """A tool call as plain HTTP, so that what a caller with no key, or a wrong one, is told is
    seen as it is sent."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
    ).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    call = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(call, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


async def test_blank_sources_and_reasons_and_unauthorised_changes_leave_nothing_half_done() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as client:
        (story,) = await make_stories(client, 1)
        plain_fact = await make_fact(client, "Not looked at", nodes=[story["ref"]])
        reviewed_fact = await make_fact(client, "Looked at by Marie", nodes=[story["ref"]])
        await confirm(client, "fact", reviewed_fact, "Marie")
        ended_fact = await make_fact(client, "Gone")
        await ok(client, "withdraw_fact", actor="a", fact_ids=[ended_fact["id"]], reason="Wrong")
        plain_decision = await make_decision(client, "Not looked at", nodes=[story["ref"]])
        reviewed_decision = await make_decision(client, "Looked at by Marie", project_wide=True)
        await confirm(client, "decision", reviewed_decision, "Marie")
        ended_decision = await make_decision(client, "Gone")
        await ok(
            client, "withdraw_decision", actor="a", decision_id=ended_decision["id"], reason="Wrong"
        )
        before = await everything_known(client)
        assert before["counts"]["facts"] == 3 and before["counts"]["decisions"] == 3

        a_person = {"said_by": "Marie", "quote": "In the call"}
        fact_text = {"text": "New", "source": "A source"}
        decision_text = {"title": "T", "text": "t", "reason": "r", "decided_by": "Ann"}
        battery: list[tuple[str, dict[str, Any]]] = [
            # A blank source, reason, text, title or decider, one at a time.
            ("retain_fact", {"actor": "a", "text": "x", "source": " "}),
            ("retain_fact", {"actor": "a", "text": " ", "source": "s"}),
            ("retain_fact", {"actor": "a", "text": "x"}),
            ("record_decision", {"actor": "a", **decision_text, "reason": "\n\t "}),
            ("record_decision", {"actor": "a", **decision_text, "title": " "}),
            ("record_decision", {"actor": "a", **decision_text, "text": " "}),
            ("record_decision", {"actor": "a", **decision_text, "decided_by": " "}),
            ("record_decision", {"actor": "a", "title": "t", "text": "t", "decided_by": "Ann"}),
            (
                "supersede_fact",
                {
                    "actor": "a",
                    "replaces": [plain_fact["id"]],
                    "reason": "r",
                    **fact_text,
                    "source": " ",
                },
            ),
            (
                "supersede_fact",
                {"actor": "a", "replaces": [plain_fact["id"]], "reason": " ", **fact_text},
            ),
            ("withdraw_fact", {"actor": "a", "fact_ids": [plain_fact["id"]], "reason": " "}),
            (
                "supersede_decision",
                {"actor": "a", "decision_id": plain_decision["id"], **decision_text, "reason": " "},
            ),
            (
                "withdraw_decision",
                {"actor": "a", "decision_id": plain_decision["id"], "reason": " "},
            ),
            # A change to what a person confirmed, with no person's word.
            (
                "supersede_fact",
                {"actor": "agent-2", "replaces": [reviewed_fact["id"]], "reason": "r", **fact_text},
            ),
            (
                "supersede_fact",
                {"actor": "Marie", "replaces": [reviewed_fact["id"]], "reason": "r", **fact_text},
            ),
            (
                "withdraw_fact",
                {"actor": "agent-2", "fact_ids": [reviewed_fact["id"]], "reason": "r"},
            ),
            (
                "withdraw_fact",
                {"actor": "Marie", "fact_ids": [reviewed_fact["id"]], "reason": "r"},
            ),
            (
                "supersede_decision",
                {
                    "actor": "agent-2",
                    "decision_id": reviewed_decision["id"],
                    **decision_text,
                },
            ),
            (
                "withdraw_decision",
                {"actor": "Marie", "decision_id": reviewed_decision["id"], "reason": "r"},
            ),
            # The person's name without their words, or their words without their name.
            (
                "withdraw_fact",
                {
                    "actor": "a",
                    "fact_ids": [reviewed_fact["id"]],
                    "reason": "r",
                    "said_by": "Marie",
                },
            ),
            (
                "withdraw_decision",
                {
                    "actor": "a",
                    "decision_id": reviewed_decision["id"],
                    "reason": "r",
                    "quote": "In the call",
                },
            ),
            # One confirmed among several: the whole call is refused, the others stay as they were.
            (
                "withdraw_fact",
                {"actor": "a", "fact_ids": [plain_fact["id"], reviewed_fact["id"]], "reason": "r"},
            ),
            (
                "supersede_fact",
                {
                    "actor": "a",
                    "replaces": [plain_fact["id"], reviewed_fact["id"]],
                    "reason": "r",
                    **fact_text,
                },
            ),
            (
                "withdraw_fact",
                {"actor": "a", "fact_ids": [plain_fact["id"], ended_fact["id"]], "reason": "r"},
            ),
            (
                "withdraw_fact",
                {"actor": "a", "fact_ids": [plain_fact["id"], 987654321], "reason": "r"},
            ),
            # Confirmation without a person, twice, of an ended one, or taken away without words.
            (
                "set_confirmation",
                {
                    "actor": "a",
                    "kind": "fact",
                    "record_id": plain_fact["id"],
                    "confirmation": "confirmed",
                    "person": " ",
                },
            ),
            (
                "set_confirmation",
                {
                    "actor": "a",
                    "kind": "fact",
                    "record_id": reviewed_fact["id"],
                    "confirmation": "confirmed",
                    "person": "Ann",
                },
            ),
            (
                "set_confirmation",
                {
                    "actor": "a",
                    "kind": "fact",
                    "record_id": ended_fact["id"],
                    "confirmation": "confirmed",
                    "person": "Ann",
                },
            ),
            (
                "set_confirmation",
                {
                    "actor": "a",
                    "kind": "decision",
                    "record_id": ended_decision["id"],
                    "confirmation": "confirmed",
                    "person": "Ann",
                },
            ),
            (
                "set_confirmation",
                {
                    "actor": "a",
                    "kind": "fact",
                    "record_id": reviewed_fact["id"],
                    "confirmation": "unconfirmed",
                    "person": "Marie",
                },
            ),
            (
                "set_confirmation",
                {
                    "actor": "a",
                    "kind": "decision",
                    "record_id": reviewed_decision["id"],
                    "confirmation": "unconfirmed",
                    "person": " ",
                    "quote": "No longer",
                },
            ),
            (
                "set_confirmation",
                {
                    "actor": "a",
                    "kind": "decision",
                    "record_id": plain_decision["id"],
                    "confirmation": "unconfirmed",
                    "person": "Marie",
                    "quote": "No longer",
                },
            ),
            # A record that does not exist, and one half of whose links are wrong.
            (
                "set_confirmation",
                {
                    "actor": "a",
                    "kind": "fact",
                    "record_id": 987654321,
                    "confirmation": "confirmed",
                    "person": "Marie",
                },
            ),
            (
                "link_fact",
                {
                    "actor": "a",
                    "fact_id": plain_fact["id"],
                    "nodes": [story["parent"]],
                    "rule_ids": [987654321],
                },
            ),
            (
                "retain_fact",
                {
                    "actor": "a",
                    "text": "Half right",
                    "source": "s",
                    "nodes": [story["ref"]],
                    "decision_ids": [987654321],
                },
            ),
        ]
        for tool, arguments in battery:
            message = await refused(client, tool, **arguments)
            assert message, (tool, arguments)
            assert await everything_known(client) == before, f"{tool} {arguments}: {message}"

        # With the person's word and a reason, the very same changes are accepted, and the history
        # keeps the person and the agent that passed it on.
        change = await ok(
            client,
            "withdraw_fact",
            actor="agent-2",
            fact_ids=[plain_fact["id"], reviewed_fact["id"]],
            reason="Marie says it is wrong",
            **a_person,
        )
        assert change["withdrawn_ids"] == [plain_fact["id"], reviewed_fact["id"]]
        ended = (await ok(client, "fact_history", fact_id=reviewed_fact["id"]))["items"][-1]
        assert (ended["action"], ended["actor"]) == ("fact_withdrawn", "agent-2")
        assert (ended["after"]["said_by"], ended["after"]["quote"]) == ("Marie", "In the call")

        # A caller with another project's key can reach none of it, and one with no key or a key
        # that is wrong or revoked is not let in at all.
        other_owner = register()
        other = make_project(other_owner)
        snapshot = await everything_known(client)
        async with connect(other.mcp_url, other.key) as stranger:
            for tool, arguments in (
                ("withdraw_fact", {"actor": "a", "fact_ids": [plain_fact["id"]], "reason": "r"}),
                (
                    "supersede_decision",
                    {
                        "actor": "a",
                        "decision_id": reviewed_decision["id"],
                        **decision_text,
                        "said_by": "Marie",
                        "quote": "q",
                    },
                ),
                (
                    "set_confirmation",
                    {
                        "actor": "Marie",
                        "kind": "decision",
                        "record_id": reviewed_decision["id"],
                        "confirmation": "unconfirmed",
                        "person": "Marie",
                        "quote": "I say so",
                    },
                ),
                (
                    "link_decision",
                    {"actor": "a", "decision_id": plain_decision["id"], "nodes": [story["ref"]]},
                ),
            ):
                assert "There is no" in await refused(stranger, tool, **arguments)
            assert (await ok(stranger, "list_facts"))["total"] == 0
            assert (await ok(stranger, "list_decisions"))["total"] == 0
        spare = other_owner.call("POST", f"/board/api/projects/{other.id}/keys", {"label": "spare"})
        spare_secret, spare_id = spare.body["secret"], spare.body["key"]["id"]
        other_owner.call("DELETE", f"/board/api/projects/{other.id}/keys/{spare_id}")
        attempt = {"actor": "a", "fact_ids": [plain_fact["id"]], "reason": "r"}
        for key, expected in (
            (None, "needs an agent key for a project, and none was sent"),
            ("vrk_wrong-" + unique_word(), "The agent key is not valid"),
            (spare_secret, "The agent key is not valid"),
        ):
            status, body = raw_tool_call(project.mcp_url, key, "withdraw_fact", attempt)
            assert status == 401 and expected in body, (key, status, body)
            assert spare_secret not in body
        assert await everything_known(client) == snapshot


# -- (d) a project-wide decision belongs to its project ---------------------------------------


async def test_a_project_wide_decision_is_in_its_projects_briefing_and_recall_and_no_others() -> (
    None
):
    owner = register()
    mine, theirs = make_project(owner, "Mine"), make_project(owner, "Theirs")
    word = unique_word()
    async with (
        connect(mine.mcp_url, mine.key) as ours,
        connect(theirs.mcp_url, theirs.key) as others,
    ):
        for client, name in ((ours, "mine"), (others, "theirs")):
            await make_decision(client, f"An ordinary decision of {name}")
        wide = await make_decision(
            ours,
            f"Everything stays plain {word}",
            text=f"Applies to all of mine {word}",
            project_wide=True,
        )
        # Both projects number from 1, so the other project's own first decision has the same id
        # as an ordinary decision here, and nothing of one is found by the other.
        assert wide["id"] == 2
        assert (await ok(others, "list_decisions"))["total"] == 1

        epic, _, _ = await make_tree(ours)
        other_epic, _, other_task = await make_tree(others)

        # Ours: in the briefing of work, and in the recall of an item nothing is linked to.
        context = await briefing(ours, epic)
        assert ids(context["project_wide_decisions"]) == [wide["id"]]
        assert context["project_wide_decisions"][0]["title"] == wide["title"]
        assert context["project_wide_decisions"][0]["confirmation"] == "unconfirmed"
        assert context["active_decisions"] == []
        recalled = await ok(ours, "recall", node=epic["ref"])
        assert [e["decision"] for e in recalled["project_wide_decisions"]["items"]] == [wide]
        assert recalled["project_wide_decisions"]["items"][0]["why"] == (
            "a project-wide decision, so it applies throughout the project"
        )
        assert recalled["active_decisions"]["items"] == []

        # Theirs has none: nothing of ours is in their briefing, their recall, their lists or
        # their search, and ours is not theirs to read, confirm or end.
        their_context = await briefing(others, other_epic)
        assert their_context["project_wide_decisions"] == []
        their_recall = await ok(others, "recall", node=other_task["ref"])
        assert their_recall["project_wide_decisions"]["items"] == []
        assert (await ok(others, "list_decisions", project_wide=True))["total"] == 0
        assert (await ok(others, "search", query=word, mode="words"))["items"] == []
        assert "There is no decision with id 2" in await refused(
            others, "get_decision", decision_id=wide["id"]
        )
        assert "There is no decision with id 2" in await refused(
            others,
            "set_confirmation",
            actor="Marie",
            kind="decision",
            record_id=wide["id"],
            confirmation="confirmed",
            person="Marie",
        )
        assert (await ok(ours, "get_decision", decision_id=wide["id"]))["confirmation"] == (
            "unconfirmed"
        )

        # Theirs has its own, and it is theirs alone, though it has the same number.
        own = await make_decision(others, f"Theirs stays plain {word}", project_wide=True)
        assert own["id"] == 2 and own["title"] != wide["title"]
        assert ids((await briefing(others, other_epic))["project_wide_decisions"]) == [own["id"]]
        assert (await briefing(others, other_epic))["project_wide_decisions"][0]["title"] == (
            own["title"]
        )
        assert (await briefing(ours, epic))["project_wide_decisions"][0]["title"] == wide["title"]

        # Ending ours takes it out of ours and changes nothing of theirs.
        await ok(ours, "withdraw_decision", actor="a", decision_id=wide["id"], reason="No longer")
        assert (await briefing(ours, epic))["project_wide_decisions"] == []
        assert (await ok(ours, "recall", node=epic["ref"]))["project_wide_decisions"]["items"] == []
        assert ids((await briefing(others, other_epic))["project_wide_decisions"]) == [own["id"]]


# -- (e) epics, stories and tasks are numbered on their own, so records must not be mixed up -------


async def test_records_linked_to_items_with_the_same_number_stay_with_their_own_item() -> None:
    """In a new project the first epic, the first story and the first task are epic:1, story:1 and
    task:1: three items. What is linked to one of them, or written about it, is not found for
    another."""
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        epic, story, task = await make_tree(client)
        items = {"epic": epic, "story": story, "task": task}
        assert [item["ref"] for item in items.values()] == ["epic:1", "story:1", "task:1"]
        assert [item["number"] for item in items.values()] == [1, 1, 1]
        decisions = {
            kind: await make_decision(client, f"About the {kind}", nodes=[item["ref"]])
            for kind, item in items.items()
        }
        facts = {
            kind: await make_fact(client, f"A fact about the {kind}", nodes=[item["ref"]])
            for kind, item in items.items()
        }
        for kind, item in items.items():
            assert decisions[kind]["nodes"] == facts[kind]["nodes"] == [item["ref"]]
            recalled = await ok(client, "recall", node=item["ref"])
            assert [e["decision"]["id"] for e in recalled["active_decisions"]["items"]] == [
                decisions[kind]["id"]
            ]
            assert [e["fact"]["id"] for e in recalled["facts"]["items"]] == [facts[kind]["id"]]
            assert await every(client, "list_facts", node=item["ref"]) == [facts[kind]]
            history = (await ok(client, "node_history", node=item["ref"]))["items"]
            assert [e["decision_id"] for e in history if e["action"] == "decision_linked"] == [
                decisions[kind]["id"]
            ]
            assert [e["fact_id"] for e in history if e["action"] == "fact_linked"] == [
                facts[kind]["id"]
            ]
        # Everything under an item is its own area: the epic holds all three, the task only its own.
        for within, kinds in (
            ("epic:1", ["epic", "story", "task"]),
            ("story:1", ["story", "task"]),
            ("task:1", ["task"]),
        ):
            found = await every(client, "list_facts", within_node=within)
            assert ids(found) == [facts[kind]["id"] for kind in kinds], within
        # A text that names one of them is a mention of that one only.
        await make_fact(client, "See [[task:1]] and nothing else")
        assert [m["source"]["item_type"] for m in await mentions(client, "task:1")] == ["fact"]
        assert await mentions(client, "story:1") == []
        assert await mentions(client, "epic:1") == []
