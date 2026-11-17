"""M16 against the running containers: decisions (a choice, its reason and the person who made it),
project-wide decisions, links, superseding, withdrawing, confirmation by a named person and recall,
through a real MCP client (see ./check.sh)."""

from datetime import datetime
from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    confirm,
    day,
    make_decision,
    make_fact,
    make_rule,
    make_stories,
    make_tree,
    ok,
    race_mixed,
    race_results,
    ref_order,
    refused,
    run_sql,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

DECISION_FIELDS = {
    "id",
    "label",
    "title",
    "text",
    "reason",
    "decided_by",
    "decided_on",
    "project_wide",
    "status",
    "superseded_by_id",
    "supersedes_ids",
    "status_reason",
    "confirmation",
    "confirmed_by",
    "confirmed_at",
    "nodes",
    "rule_ids",
    "recorded_at",
}


async def recall(client: Client, **arguments: Any) -> dict[str, Any]:
    """What `recall` says about decisions, in the flat shape these tests read: each section's
    decisions, with its count and its next page."""
    result = await ok(client, "recall", limit=200, **arguments)
    flat: dict[str, Any] = {}
    for name in (
        "active_decisions",
        "project_wide_decisions",
        "past_decisions",
    ):
        section = result[name]
        flat[name] = [entry["decision"] for entry in section["items"]]
        flat[f"{name}_why"] = {entry["decision"]["id"]: entry["why"] for entry in section["items"]}
        flat[f"{name}_count"] = section["count"]
        flat[f"next_{name}_after_id"] = section["next_after_id"]
    return flat


def ids(decisions: list[dict[str, Any]]) -> list[int]:
    return [decision["id"] for decision in decisions]


async def history(client: Client, decision: dict[str, Any]) -> list[dict[str, Any]]:
    return list((await ok(client, "decision_history", decision_id=decision["id"]))["items"])


async def get(client: Client, decision: dict[str, Any]) -> dict[str, Any]:
    return await ok(client, "get_decision", decision_id=decision["id"])


async def every(client: Client, **filters: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    after_id = None
    while True:
        page = await ok(client, "list_decisions", limit=200, after_id=after_id, **filters)
        found.extend(page["items"])
        after_id = page["next_after_id"]
        if after_id is None:
            return found


async def tally(client: Client) -> tuple[int, int, int]:
    """How many facts, decisions and events the project holds: a refused call must leave all
    three as they were."""
    counts = (await ok(client, "server_status"))["counts"]
    return counts["facts"], counts["decisions"], counts["events"]


def instant(text: str) -> datetime:
    return datetime.fromisoformat(text)


async def replace(client: Client, decision: dict[str, Any], **arguments: Any) -> dict[str, Any]:
    """Supersede the decision with a new one. Returns the new decision."""
    fields: dict[str, Any] = {
        "actor": "agent-2",
        "decision_id": decision["id"],
        "title": "Changed",
        "text": "What is decided now",
        "reason": "Things changed",
        "decided_by": "Ann",
    }
    return await ok(client, "supersede_decision", **{**fields, **arguments})


async def withdraw(client: Client, decision: dict[str, Any], **arguments: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "actor": "agent-1",
        "decision_id": decision["id"],
        "reason": "Not needed after all",
    }
    return await ok(client, "withdraw_decision", **{**fields, **arguments})


async def test_a_decision_is_recorded_with_who_decided_it_and_what_it_affects(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, story=story["ref"])

    decision = await ok(
        client,
        "record_decision",
        actor="agent-1",
        title="Keep one database",
        text="The graph and the index live in one PostgreSQL database.",
        reason="Fewer moving parts to run.",
        decided_by="Marie",
        decided_on="2026-11-01",
        nodes=[story["ref"], story["parent"]],
        rule_ids=[rule["id"]],
    )
    assert set(decision) == DECISION_FIELDS
    assert (decision["status"], decision["decided_by"]) == ("active", "Marie")
    assert decision["reason"] == "Fewer moving parts to run."
    assert decision["decided_on"] == "2026-11-01"
    assert decision["project_wide"] is False
    assert decision["nodes"] == sorted([story["ref"], story["parent"]], key=ref_order)
    assert decision["rule_ids"] == [rule["id"]]
    assert (decision["superseded_by_id"], decision["supersedes_ids"]) == (None, [])
    assert decision["status_reason"] is None
    # Recording is not confirming: the agent that recorded it is not the person who decided it.
    assert (decision["confirmation"], decision["confirmed_by"], decision["confirmed_at"]) == (
        "unconfirmed",
        None,
        None,
    )
    assert decision["label"] == "Decision, not confirmed by a person"
    assert await ok(client, "get_decision", decision_id=decision["id"]) == decision

    today = await make_decision(client, "Dated today")
    assert day(-1) <= today["decided_on"] <= day(1)

    events = await history(client, decision)
    assert [(e["action"], e["actor"]) for e in events] == [
        ("decision_recorded", "agent-1"),
        ("decision_linked", "agent-1"),
        ("decision_linked", "agent-1"),
        ("decision_linked", "agent-1"),
    ]
    assert events[0]["before"] is None and events[0]["decision_id"] == decision["id"]
    assert events[0]["after"] == {
        "title": "Keep one database",
        "text": "The graph and the index live in one PostgreSQL database.",
        "reason": "Fewer moving parts to run.",
        "decided_by": "Marie",
        "decided_on": "2026-11-01",
        "project_wide": False,
    }
    # The history of what it affects shows the link too.
    story_events = (await ok(client, "node_history", node=story["ref"]))["items"]
    assert [e["decision_id"] for e in story_events if e["action"] == "decision_linked"] == [
        decision["id"]
    ]
    rule_events = (await ok(client, "rule_history", rule_id=rule["id"]))["items"]
    assert [e["decision_id"] for e in rule_events if e["action"] == "decision_linked"] == [
        decision["id"]
    ]


async def test_recalling_a_story_returns_its_linked_active_decisions_and_the_project_wide_ones(
    client: Client,
) -> None:
    story, other_story = await make_stories(client, 2)
    mine = await make_decision(client, "About this story", nodes=[story["ref"]])
    also_mine = await make_decision(
        client, "Also this story", nodes=[story["ref"], other_story["ref"]]
    )
    elsewhere = await make_decision(client, "About the other story", nodes=[other_story["ref"]])
    unlinked = await make_decision(client, "About nothing in particular")
    linked_wide = await make_decision(
        client, "A linked project-wide one", project_wide=True, nodes=[story["ref"]]
    )
    free_wide = await make_decision(client, "A free project-wide one", project_wide=True)

    result = await recall(client, node=story["ref"])

    assert ids(result["active_decisions"]) == [mine["id"], also_mine["id"]]
    for left_out in (elsewhere, unlinked, linked_wide, free_wide):
        assert left_out["id"] not in ids(result["active_decisions"])
    assert all(
        d["status"] == "active" and not d["project_wide"] for d in result["active_decisions"]
    )
    assert result["active_decisions_count"] == 2
    assert set(result["active_decisions_why"].values()) == {"linked directly to this node"}

    # Every project-wide decision in force is there, linked to the story or not.
    wide = await every(client, project_wide=True, status="active")
    assert ids(result["project_wide_decisions"]) == ids(wide)
    assert {linked_wide["id"], free_wide["id"]} <= set(ids(result["project_wide_decisions"]))
    assert result["project_wide_decisions_count"] == len(wide)
    assert result["past_decisions"] == [] and result["past_decisions_count"] == 0
    assert set(result["project_wide_decisions_why"].values()) == {
        "a project-wide decision, so it applies throughout the project"
    }
    assert result["project_wide_decisions"][0]["label"].startswith("Project-wide decision, ")

    # Another story gets its own decisions but the same project-wide ones.
    other = await recall(client, node=other_story["ref"])
    assert ids(other["active_decisions"]) == [also_mine["id"], elsewhere["id"]]
    assert ids(other["project_wide_decisions"]) == ids(result["project_wide_decisions"])


async def test_nothing_is_invented_for_an_item_with_no_decisions(client: Client) -> None:
    epic, story, task = await make_tree(client)
    await make_decision(client, "About the epic only", nodes=[epic["ref"]])

    for node in (story, task):
        result = await recall(client, node=node["ref"])
        # Decisions linked to its parent are not inferred: recall is by exact link.
        assert result["active_decisions"] == [] and result["past_decisions"] == []
        assert (result["active_decisions_count"], result["past_decisions_count"]) == (0, 0)
        assert all(d["project_wide"] for d in result["project_wide_decisions"])


async def test_a_project_wide_decision_is_in_every_recall_while_it_is_in_force(
    client: Client,
) -> None:
    story, other_story = await make_stories(client, 2)
    rule = await make_rule(client, story=story["ref"])
    wide = await make_decision(client, "Applies everywhere", project_wide=True)
    assert wide["project_wide"] is True
    assert wide["label"] == "Project-wide decision, not confirmed by a person"
    assert (await get(client, wide)) == wide

    async def where_it_shows() -> list[bool]:
        shown = []
        for arguments in (
            {"node": story["ref"]},
            {"node": other_story["ref"]},
            {"node": story["parent"]},
            {"rule_id": rule["id"]},
        ):
            section = (await recall(client, **arguments))["project_wide_decisions"]
            shown.append(wide["id"] in ids(section))
        return shown

    assert await where_it_shows() == [True, True, True, True]
    assert wide["id"] in ids(await every(client, project_wide=True, status="active"))
    assert wide["id"] not in ids(await every(client, project_wide=False))

    # Replacing it keeps it project-wide unless it is told otherwise; the old one is a record.
    newer = await replace(client, wide, title="Applies everywhere, differently")
    assert newer["project_wide"] is True
    assert await where_it_shows() == [False] * 4
    for_story = (await recall(client, node=story["ref"]))["project_wide_decisions"]
    assert newer["id"] in ids(for_story) and wide["id"] not in ids(for_story)
    assert (await get(client, wide))["project_wide"] is True
    assert (await get(client, wide))["status"] == "superseded"

    narrower = await replace(client, newer, project_wide=False, nodes=[story["ref"]])
    assert narrower["project_wide"] is False and narrower["nodes"] == [story["ref"]]
    for_story = await recall(client, node=story["ref"])
    assert narrower["id"] in ids(for_story["active_decisions"])
    assert narrower["id"] not in ids(for_story["project_wide_decisions"])
    assert newer["id"] not in ids(for_story["project_wide_decisions"])
    for_other = await recall(client, node=other_story["ref"])
    assert narrower["id"] not in ids(for_other["active_decisions"])

    broader = await replace(client, narrower, project_wide=True)
    assert broader["project_wide"] is True and broader["nodes"] == [story["ref"]]
    for_story = await recall(client, node=story["ref"])
    assert broader["id"] in ids(for_story["project_wide_decisions"])
    assert broader["id"] not in ids(for_story["active_decisions"])
    # What the narrower one was linked to still shows it as a past decision there.
    assert ids([d for d in for_story["past_decisions"] if d["id"] == narrower["id"]]) == [
        narrower["id"]
    ]

    # A project-wide decision that is withdrawn applies nowhere any more.
    await withdraw(client, broader, reason="No longer true everywhere")
    for_story = await recall(client, node=story["ref"])
    assert broader["id"] not in ids(for_story["project_wide_decisions"])
    assert broader["id"] not in ids(await every(client, project_wide=True, status="active"))
    assert broader["id"] in ids(await every(client, project_wide=True, status="withdrawn"))
    # It was linked to the story, so the story still shows it, no longer in force.
    assert for_story["past_decisions_why"][broader["id"]] == (
        "linked directly to this node; withdrawn: No longer true everywhere"
    )


async def test_a_superseded_decision_is_shown_as_superseded_and_never_as_active(
    client: Client,
) -> None:
    story, other_story = await make_stories(client, 2)
    old = await make_decision(client, "Use a queue", nodes=[story["ref"]])
    await make_decision(client, "Unrelated", nodes=[story["ref"]])

    new = await ok(
        client,
        "supersede_decision",
        actor="agent-2",
        decision_id=old["id"],
        title="Use a table instead of a queue",
        text="A table, polled by the worker.",
        reason="The queue was more than we need.",
        decided_by="Ann",
    )
    assert (new["status"], new["supersedes_ids"], new["decided_by"]) == (
        "active",
        [old["id"]],
        "Ann",
    )
    assert new["confirmation"] == "unconfirmed" and new["reason"] == (
        "The queue was more than we need."
    )

    replaced = await get(client, old)
    assert (replaced["status"], replaced["superseded_by_id"]) == ("superseded", new["id"])
    assert replaced["status_reason"] is None
    assert replaced["label"] == "Superseded decision, not confirmed by a person"
    # What was decided and why is kept exactly as it was.
    assert (replaced["title"], replaced["text"], replaced["reason"], replaced["decided_by"]) == (
        old["title"],
        old["text"],
        old["reason"],
        old["decided_by"],
    )

    result = await recall(client, node=story["ref"])
    assert new["id"] in ids(result["active_decisions"])
    assert old["id"] not in ids(result["active_decisions"])
    assert [(d["id"], d["status"], d["superseded_by_id"]) for d in result["past_decisions"]] == [
        (old["id"], "superseded", new["id"])
    ]
    assert result["past_decisions_why"][old["id"]] == (
        f"linked directly to this node; replaced by decision {new['id']}"
    )
    assert result["past_decisions_count"] == 1
    assert old["id"] not in [d["id"] for d in await every(client, status="active")]
    assert old["id"] in ids(await every(client, status="superseded"))
    assert (await recall(client, node=other_story["ref"]))["past_decisions"] == []

    old_events = await history(client, old)
    assert [(e["action"], e["actor"]) for e in old_events][-1] == ("decision_superseded", "agent-2")
    assert old_events[-1]["before"] == {"status": "active", "superseded_by_id": None}
    assert old_events[-1]["after"] == {"status": "superseded", "superseded_by_id": new["id"]}
    assert (await history(client, new))[0]["after"]["supersedes_id"] == old["id"]


async def test_a_replacement_keeps_what_the_old_decision_affected_unless_told_otherwise(
    client: Client,
) -> None:
    first, second, third = await make_stories(client, 3)
    rule = await make_rule(client, story=first["ref"])
    original = await make_decision(
        client, "Original", nodes=[first["ref"], second["ref"]], rule_ids=[rule["id"]]
    )

    kept = await replace(client, original, nodes=[third["ref"]])
    assert kept["nodes"] == [first["ref"], second["ref"], third["ref"]]
    assert kept["rule_ids"] == [rule["id"]]
    # The old one is still linked as it was, as a record.
    assert (await get(client, original))["nodes"] == [first["ref"], second["ref"]]
    assert kept["id"] in ids((await recall(client, node=third["ref"]))["active_decisions"])

    fresh = await replace(client, kept, keep_links=False, nodes=[first["ref"]])
    assert (fresh["nodes"], fresh["rule_ids"]) == ([first["ref"]], [])
    assert fresh["id"] not in ids((await recall(client, node=second["ref"]))["active_decisions"])

    # The new decision says who made it and on what day: it is not the old one's.
    dated = await replace(client, fresh, decided_by="Bob", decided_on="2026-12-24")
    assert (dated["decided_by"], dated["decided_on"]) == ("Bob", "2026-12-24")


async def test_a_decision_cannot_be_superseded_twice_and_only_one_of_several_racers_wins(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    for _ in range(6):
        old = await make_decision(client, "Contested", nodes=[story["ref"]])
        results = await race_results(
            client,
            "supersede_decision",
            [
                {
                    "actor": f"agent-{number}",
                    "decision_id": old["id"],
                    "title": f"Replacement {number}",
                    "text": "t",
                    "reason": "r",
                    "decided_by": "Ann",
                }
                for number in range(6)
            ],
        )
        assert [accepted for accepted, _ in results].count(True) == 1, results
        # Whoever loses is told so in plain words, not given a database error.
        for accepted, said in results:
            assert accepted or "already superseded by decision" in said, said
        replacements = [
            d for d in await every(client, status="active") if d["supersedes_ids"] == [old["id"]]
        ]
        assert len(replacements) == 1
        assert (await get(client, old))["superseded_by_id"] == replacements[0]["id"]

    message = await refused(
        client,
        "supersede_decision",
        actor="a",
        decision_id=old["id"],
        title="Late",
        text="t",
        reason="r",
        decided_by="Ann",
    )
    assert f"already superseded by decision {replacements[0]['id']}" in message
    assert f"supersede decision {replacements[0]['id']} instead" in message


async def test_withdrawing_a_decision_needs_a_reason_and_keeps_the_record(client: Client) -> None:
    story, other_story = await make_stories(client, 2)
    rule = await make_rule(client, story=story["ref"])
    decision = await make_decision(
        client, "Use a cache", nodes=[story["ref"]], rule_ids=[rule["id"]]
    )
    kept = await make_decision(client, "Stays", nodes=[story["ref"]])
    before = await tally(client)

    for arguments, expected in (
        ({"reason": " "}, "A withdrawal needs a reason"),
        ({"reason": ""}, "A withdrawal needs a reason"),
        ({"actor": " "}, "Say who is making this change"),
        ({"said_by": "Marie"}, "Give both said_by"),
        ({"quote": "She said so"}, "Give both said_by"),
        ({"decision_id": 987654321}, "There is no decision with id 987654321"),
    ):
        message = await refused(
            client,
            "withdraw_decision",
            **{"actor": "a", "decision_id": decision["id"], "reason": "r", **arguments},
        )
        assert expected in message, message
        assert (await get(client, decision))["status"] == "active"
        assert await tally(client) == before

    gone = await withdraw(client, decision, actor="agent-3", reason="The cache hides bugs")
    assert (gone["status"], gone["status_reason"], gone["superseded_by_id"]) == (
        "withdrawn",
        "The cache hides bugs",
        None,
    )
    # Nothing was deleted or rewritten, and it keeps the links it had.
    assert (gone["title"], gone["text"], gone["reason"], gone["decided_by"]) == (
        decision["title"],
        decision["text"],
        decision["reason"],
        decision["decided_by"],
    )
    assert gone["nodes"] == [story["ref"]] and gone["rule_ids"] == [rule["id"]]
    assert gone["label"] == "Withdrawn decision, not confirmed by a person"
    assert await get(client, decision) == gone
    events = await history(client, decision)
    assert (events[-1]["action"], events[-1]["actor"]) == ("decision_withdrawn", "agent-3")
    assert events[-1]["before"] == {"status": "active"}
    assert events[-1]["after"] == {"status": "withdrawn", "reason": "The cache hides bugs"}

    result = await recall(client, node=story["ref"])
    assert ids(result["active_decisions"]) == [kept["id"]]
    assert result["past_decisions_why"] == {
        decision["id"]: "linked directly to this node; withdrawn: The cache hides bugs"
    }
    assert ids(await every(client, status="withdrawn"))[-1] == decision["id"]
    assert (await recall(client, node=other_story["ref"]))["past_decisions"] == []
    assert ids((await recall(client, rule_id=rule["id"]))["past_decisions"]) == [decision["id"]]

    # A decision that is no longer in force cannot be given up, replaced or relinked.
    replaced = await make_decision(client, "Will be replaced")
    await replace(client, replaced)
    before = await tally(client)
    for target, expected in (
        (decision, f"Decision {decision['id']} was withdrawn (The cache hides bugs)"),
        (replaced, f"Decision {replaced['id']} was already superseded by decision"),
    ):
        message = await refused(
            client, "withdraw_decision", actor="a", decision_id=target["id"], reason="Again"
        )
        assert expected in message and "so it cannot be withdrawn" in message, message
    message = await refused(
        client,
        "supersede_decision",
        actor="a",
        decision_id=decision["id"],
        title="T",
        text="t",
        reason="r",
        decided_by="Ann",
    )
    assert "was withdrawn (The cache hides bugs), so it cannot be superseded" in message
    assert await tally(client) == before


async def test_links_can_be_added_and_removed_from_an_active_decision(client: Client) -> None:
    first, second = await make_stories(client, 2)
    rule = await make_rule(client, story=first["ref"])
    decision = await make_decision(client, "Movable", nodes=[first["ref"]])

    linked = await ok(
        client,
        "link_decision",
        actor="ann",
        decision_id=decision["id"],
        nodes=[second["ref"]],
        rule_ids=[rule["id"]],
    )
    assert (linked["nodes"], linked["rule_ids"]) == ([first["ref"], second["ref"]], [rule["id"]])
    assert decision["id"] in ids((await recall(client, node=second["ref"]))["active_decisions"])
    assert decision["id"] in ids((await recall(client, rule_id=rule["id"]))["active_decisions"])

    unlinked = await ok(
        client, "unlink_decision", actor="bob", decision_id=decision["id"], nodes=[first["ref"]]
    )
    assert unlinked["nodes"] == [second["ref"]]
    assert decision["id"] not in ids((await recall(client, node=first["ref"]))["active_decisions"])

    events = [(e["action"], e["actor"]) for e in await history(client, decision)]
    assert events == [
        ("decision_recorded", "agent-1"),
        ("decision_linked", "agent-1"),
        ("decision_linked", "ann"),
        ("decision_linked", "ann"),
        ("decision_unlinked", "bob"),
    ]
    node_events = (await ok(client, "node_history", node=first["ref"]))["items"]
    assert [e["action"] for e in node_events if e["decision_id"] == decision["id"]] == [
        "decision_linked",
        "decision_unlinked",
    ]

    # A fact that concerns the decision does not make the decision concern anything else.
    fact = await make_fact(client, "About the decision", decision_ids=[decision["id"]])
    assert (await get(client, decision))["nodes"] == [second["ref"]]
    assert fact["decision_ids"] == [decision["id"]]


async def test_link_changes_that_make_no_sense_change_nothing(client: Client) -> None:
    first, second = await make_stories(client, 2)
    decision = await make_decision(client, "Careful", nodes=[first["ref"]])
    replaced = await make_decision(client, "Replaced", nodes=[first["ref"]])
    newer = await replace(client, replaced, actor="a")
    withdrawn = await make_decision(client, "Withdrawn", nodes=[first["ref"]])
    await withdraw(client, withdrawn, reason="Gone")
    link = {"actor": "ann", "decision_id": decision["id"]}
    before = await tally(client)

    assert f"already linked to {first['ref']}" in await refused(
        client, "link_decision", nodes=[second["ref"], first["ref"]], **link
    )
    assert f"is not linked to {second['ref']}" in await refused(
        client, "unlink_decision", nodes=[second["ref"]], **link
    )
    assert "There is no story 987654321 in this project" in await refused(
        client, "link_decision", nodes=["story:987654321"], **link
    )
    assert "There is no rule with id 987654321" in await refused(
        client, "link_decision", rule_ids=[987654321], **link
    )
    assert "Nothing to link" in await refused(client, "link_decision", **link)
    assert "actor must be the name" in await refused(
        client, "link_decision", nodes=[second["ref"]], actor=" ", decision_id=decision["id"]
    )
    message = await refused(
        client, "link_decision", actor="ann", decision_id=replaced["id"], nodes=[second["ref"]]
    )
    assert f"superseded by decision {newer['id']}" in message
    assert "keeps the links it had" in message
    assert "keeps the links it had" in await refused(
        client, "unlink_decision", actor="ann", decision_id=replaced["id"], nodes=[first["ref"]]
    )
    assert "It is withdrawn, so its links stay as they were" in await refused(
        client, "link_decision", actor="ann", decision_id=withdrawn["id"], nodes=[second["ref"]]
    )
    assert "There is no decision with id 987654321" in await refused(
        client, "link_decision", actor="a", decision_id=987654321, nodes=[first["ref"]]
    )

    # The failed attempts changed nothing, not even the part that was fine.
    assert (await get(client, decision))["nodes"] == [first["ref"]]
    assert await tally(client) == before


async def test_decisions_can_be_recalled_for_a_rule(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, story=story["ref"])
    about_rule = await make_decision(client, "About the rule", rule_ids=[rule["id"]])
    about_story = await make_decision(client, "About the story", nodes=[story["ref"]])

    result = await recall(client, rule_id=rule["id"])
    assert ids(result["active_decisions"]) == [about_rule["id"]]
    assert about_story["id"] not in ids(result["active_decisions"])
    assert "There is no rule with id 987654321" in await refused(
        client, "recall", rule_id=987654321
    )


async def test_each_recall_list_can_be_paged_on_its_own(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    made = [
        (await make_decision(client, f"Linked {n}", nodes=[story["ref"]]))["id"] for n in range(3)
    ]
    for number in range(2):
        await make_decision(client, f"Wide {number}", project_wide=True)

    both = await ok(client, "recall", node=story["ref"], limit=2)
    assert [e["decision"]["id"] for e in both["active_decisions"]["items"]] == made[:2]
    assert both["active_decisions"]["next_after_id"] == made[1]
    assert both["active_decisions"]["count"] == 3

    rest = await ok(
        client,
        "recall",
        node=story["ref"],
        show="active_decisions",
        limit=2,
        after_id=both["active_decisions"]["next_after_id"],
    )
    assert [e["decision"]["id"] for e in rest["active_decisions"]["items"]] == made[2:]
    assert rest["active_decisions"]["next_after_id"] is None
    assert rest["project_wide_decisions"]["items"] == []
    assert rest["project_wide_decisions"]["count"] >= 2

    wide = await ok(client, "recall", node=story["ref"], show="project_wide_decisions", limit=1)
    assert len(wide["project_wide_decisions"]["items"]) == 1
    assert wide["active_decisions"]["items"] == []
    assert wide["project_wide_decisions"]["next_after_id"] is not None

    assert "after_id only works for one section" in await refused(
        client, "recall", node=story["ref"], after_id=1
    )
    assert "show must be one of all, active_decisions, project_wide_decisions" in await refused(
        client, "recall", node=story["ref"], show="everything"
    )
    assert "show must be one of" in await refused(
        client, "recall", node=story["ref"], show="active_principles"
    )
    assert "Give exactly one of node (an epic, story or task" in await refused(client, "recall")
    assert "Give exactly one of node (an epic, story or task" in await refused(
        client, "recall", node=story["ref"], rule_id=1
    )
    assert "There is no story 987654321 in this project" in await refused(
        client, "recall", node="story:987654321"
    )
    assert "limit must be a whole number" in await refused(
        client, "recall", node=story["ref"], limit=0
    )


async def test_decisions_can_be_listed_filtered_and_paged(client: Client) -> None:
    made = [
        (await make_decision(client, f"Listed {n}", project_wide=bool(n % 2)))["id"]
        for n in range(4)
    ]
    first = await ok(client, "list_decisions", limit=2, after_id=made[0] - 1)
    assert ids(first["items"]) == made[:2]
    second = await ok(client, "list_decisions", limit=2, after_id=first["next_after_id"])
    assert ids(second["items"]) == made[2:4]
    assert first["total"] == second["total"] and first["total"] >= 4
    assert ids((await every(client, project_wide=True, status="active"))[-2:]) == [
        made[1],
        made[3],
    ]
    assert ids((await every(client, project_wide=False, status="active"))[-2:]) == [
        made[0],
        made[2],
    ]

    # Status and confirmation, alone and together with the others.
    await withdraw(client, {"id": made[0]})
    await replace(client, {"id": made[1]})
    await confirm(client, "decision", {"id": made[2]}, "Marie")
    await confirm(client, "decision", {"id": made[3]}, "Ann")
    await withdraw(client, {"id": made[3]}, said_by="Ann", quote="Drop it")
    mine = set(made)

    def only_mine(decisions: list[dict[str, Any]]) -> list[int]:
        return [d["id"] for d in decisions if d["id"] in mine]

    assert only_mine(await every(client, status="withdrawn")) == [made[0], made[3]]
    assert only_mine(await every(client, status="superseded")) == [made[1]]
    assert only_mine(await every(client, confirmation="confirmed")) == [made[2], made[3]]
    assert only_mine(await every(client, confirmation="confirmed", status="active")) == [made[2]]
    assert only_mine(await every(client, confirmation="confirmed", status="withdrawn")) == [made[3]]
    assert only_mine(await every(client, confirmation="unconfirmed", status="withdrawn")) == [
        made[0]
    ]
    assert only_mine(await every(client, project_wide=True, status="superseded")) == [made[1]]
    total = (await ok(client, "list_decisions", status="withdrawn", limit=1))["total"]
    assert total == len(await every(client, status="withdrawn"))

    assert "status must be one of active, superseded, withdrawn" in await refused(
        client, "list_decisions", status="open"
    )
    assert "confirmation must be one of confirmed, unconfirmed" in await refused(
        client, "list_decisions", confirmation="maybe"
    )
    assert "limit must be a whole number" in await refused(client, "list_decisions", limit=201)
    assert "There is no decision with id 987654321" in await refused(
        client, "get_decision", decision_id=987654321
    )
    assert "There is no decision with id 987654321" in await refused(
        client, "decision_history", decision_id=987654321
    )


async def test_a_decision_with_something_missing_is_refused_in_plain_words(client: Client) -> None:
    good = {
        "actor": "agent-1",
        "title": "T",
        "text": "What",
        "reason": "Why",
        "decided_by": "Marie",
    }
    before = await tally(client)

    for field, expected in (
        ("title", "A decision needs a title"),
        ("text", "A decision needs its text"),
        ("reason", "A decision needs a reason"),
        ("decided_by", "decided_by must be the name of a person"),
        ("actor", "actor must be the name of a person or an agent"),
    ):
        for blank in ("  ", ""):
            assert expected in await refused(client, "record_decision", **{**good, field: blank})
    assert "There is no story 987654321 in this project" in await refused(
        client, "record_decision", nodes=["story:987654321"], **good
    )
    assert "There is no rule with id 987654321" in await refused(
        client, "record_decision", rule_ids=[987654321], **good
    )
    existing = await make_decision(client, "Existing")
    for field, expected in (
        ("title", "A decision needs a title"),
        ("text", "A decision needs its text"),
        ("reason", "A decision needs a reason"),
        ("decided_by", "decided_by must be the name of a person"),
    ):
        message = await refused(
            client,
            "supersede_decision",
            decision_id=existing["id"],
            **{**good, "decided_by": "Ann", field: " "},
        )
        assert expected in message
    assert "There is no decision with id 987654321" in await refused(
        client, "supersede_decision", decision_id=987654321, **good
    )
    assert "Give both said_by" in await refused(
        client, "supersede_decision", decision_id=existing["id"], said_by="Marie", **good
    )
    assert (await get(client, existing))["status"] == "active"
    # Only the decision that was made for this check was added.
    assert await tally(client) == (before[0], before[1] + 1, before[2] + 1)


async def test_confirming_a_decision_names_the_person_and_the_time_and_logs_who_did_it(
    client: Client,
) -> None:
    decision = await make_decision(client, "Needs a look")
    before = await tally(client)

    for arguments, expected in (
        ({"person": " "}, "A confirmation needs the person who reviewed the record"),
        ({"record_id": 987654321}, "There is no decision with id 987654321"),
        ({"confirmation": "maybe"}, "confirmation must be one of confirmed, unconfirmed"),
        ({"confirmation": "unconfirmed"}, "is not confirmed, so there is nothing to take away"),
    ):
        message = await refused(
            client,
            "set_confirmation",
            **{
                "actor": "agent-1",
                "kind": "decision",
                "record_id": decision["id"],
                "confirmation": "confirmed",
                "person": "Marie",
                **arguments,
            },
        )
        assert expected in message, message
    assert (await get(client, decision))["confirmation"] == "unconfirmed"
    assert await tally(client) == before

    answer = await ok(
        client,
        "set_confirmation",
        actor="agent-1",
        kind="decision",
        record_id=decision["id"],
        confirmation="confirmed",
        person="Marie",
        quote="Agreed in the call",
    )
    reviewed = answer["decision"]
    assert answer["kind"] == "decision" and answer["fact"] is None
    assert (reviewed["confirmation"], reviewed["confirmed_by"]) == ("confirmed", "Marie")
    assert reviewed["confirmed_at"] and reviewed["status"] == "active"
    shown_day = instant(reviewed["confirmed_at"]).date().isoformat()
    assert reviewed["label"] == f"Decision, confirmed by Marie on {shown_day}"
    # Who decided it is not who confirmed it: that stays what it was.
    assert reviewed["decided_by"] == decision["decided_by"]
    assert await get(client, decision) == reviewed

    event = (await history(client, decision))[-1]
    assert (event["action"], event["actor"]) == ("decision_confirmed", "agent-1")
    assert event["before"] == {"confirmed_by": None}
    assert event["after"]["confirmed_by"] == "Marie"
    assert event["after"]["quote"] == "Agreed in the call"
    assert instant(event["after"]["confirmed_at"]) == instant(reviewed["confirmed_at"])
    assert instant(event["at"]) == instant(reviewed["confirmed_at"])

    # Once confirmed, not again; and taking it away needs the person and their words.
    assert f"Decision {decision['id']} was already confirmed by Marie" in await refused(
        client,
        "set_confirmation",
        actor="a",
        kind="decision",
        record_id=decision["id"],
        confirmation="confirmed",
        person="Ann",
    )
    away: dict[str, Any] = {
        "actor": "agent-2",
        "kind": "decision",
        "record_id": decision["id"],
        "confirmation": "unconfirmed",
    }
    assert "needs what the person said" in await refused(
        client, "set_confirmation", person="Marie", **away
    )
    assert "needs the person who says so" in await refused(
        client, "set_confirmation", person=" ", quote="No longer", **away
    )
    assert await get(client, decision) == reviewed
    opened = await ok(
        client, "set_confirmation", person="Marie", quote="I am no longer sure", **away
    )
    assert opened["decision"]["confirmation"] == "unconfirmed"
    assert opened["decision"]["confirmed_by"] is None
    event = (await history(client, decision))[-1]
    assert (event["action"], event["actor"]) == ("decision_unconfirmed", "agent-2")
    assert event["before"]["confirmed_by"] == "Marie"
    assert event["after"] == {
        "confirmed_by": None,
        "said_by": "Marie",
        "quote": "I am no longer sure",
    }
    assert (await confirm(client, "decision", decision, "Ann"))["confirmed_by"] == "Ann"
    assert [e["action"] for e in await history(client, decision)] == [
        "decision_recorded",
        "decision_confirmed",
        "decision_unconfirmed",
        "decision_confirmed",
    ]


async def test_an_ended_decision_keeps_its_confirmation_and_cannot_be_confirmed_again(
    client: Client,
) -> None:
    replaced = await make_decision(client, "Confirmed, then replaced")
    withdrawn = await make_decision(client, "Confirmed, then withdrawn")
    plain = await make_decision(client, "Never looked at, then withdrawn")
    reviewed = await confirm(client, "decision", replaced, "Marie")
    await confirm(client, "decision", withdrawn, "Ann")
    newer = await replace(client, replaced, said_by="Marie", quote="I changed my mind")
    await withdraw(client, withdrawn, said_by="Ann", quote="No longer", reason="Dropped")
    await withdraw(client, plain, reason="Dropped too")

    # The new decision is a new choice nobody has looked at; the old one keeps what it had.
    assert (newer["confirmation"], newer["confirmed_by"]) == ("unconfirmed", None)
    old = await get(client, replaced)
    assert (old["status"], old["confirmation"], old["confirmed_by"]) == (
        "superseded",
        "confirmed",
        "Marie",
    )
    assert old["confirmed_at"] == reviewed["confirmed_at"]
    assert old["label"].startswith("Superseded decision, confirmed by Marie on ")
    gone = await get(client, withdrawn)
    assert (gone["status"], gone["confirmation"], gone["confirmed_by"]) == (
        "withdrawn",
        "confirmed",
        "Ann",
    )
    assert gone["label"].startswith("Withdrawn decision, confirmed by Ann on ")
    assert "decision_confirmed" in [e["action"] for e in await history(client, withdrawn)]
    assert replaced["id"] in ids(await every(client, status="superseded", confirmation="confirmed"))
    assert withdrawn["id"] not in ids(
        await every(client, status="active", confirmation="confirmed")
    )

    # What an ended record was confirmed as stays: it cannot be confirmed or unconfirmed again.
    before = await tally(client)
    for target, expected in (
        (replaced, "was already superseded by decision"),
        (withdrawn, "was withdrawn (Dropped)"),
        (plain, "was withdrawn (Dropped too)"),
    ):
        for confirmation, person in (("confirmed", "Marie"), ("unconfirmed", "Ann")):
            message = await refused(
                client,
                "set_confirmation",
                actor="a",
                kind="decision",
                record_id=target["id"],
                confirmation=confirmation,
                person=person,
                quote="Whatever",
            )
            assert expected in message and "its confirmation cannot change" in message, message
    assert await tally(client) == before
    assert await get(client, replaced) == old and await get(client, withdrawn) == gone


async def test_a_confirmed_decision_can_only_be_changed_with_that_persons_word(
    client: Client,
) -> None:
    story, _ = await make_stories(client, 2)
    decision = await make_decision(
        client, "Confirmed choice", project_wide=True, nodes=[story["ref"]]
    )
    reviewed = await confirm(client, "decision", decision, "Marie")
    before = await tally(client)
    events_before = await history(client, decision)

    attempts: list[tuple[str, dict[str, Any]]] = [
        ("supersede_decision", {"title": "T", "text": "t", "decided_by": "Ann"}),
        ("withdraw_decision", {}),
    ]
    for tool, arguments in attempts:
        # Not even an agent that goes by the person's own name: a name alone grants nothing.
        for actor in ("agent-2", "Marie"):
            message = await refused(
                client, tool, actor=actor, decision_id=decision["id"], reason="r", **arguments
            )
            assert f"decision {decision['id']} (confirmed by Marie)" in message
            assert "needs that person's word" in message
            assert "said_by" in message and "quote" in message and "Nothing was changed" in message
        for partial in ({"said_by": "Marie"}, {"quote": "She said so"}):
            assert "Give both said_by" in await refused(
                client,
                tool,
                actor="a",
                decision_id=decision["id"],
                reason="r",
                **arguments,
                **partial,
            )
        assert await get(client, decision) == reviewed
        assert await history(client, decision) == events_before
        assert await tally(client) == before

    # With the person's name and words it goes through, and the history keeps both people.
    new = await ok(
        client,
        "supersede_decision",
        actor="agent-2",
        decision_id=decision["id"],
        title="The revised choice",
        text="t",
        reason="Marie changed her mind",
        decided_by="Marie",
        said_by="Marie",
        quote="In the planning call: 'that is no longer how we think about it'",
    )
    event = (await history(client, decision))[-1]
    assert (event["action"], event["actor"]) == ("decision_superseded", "agent-2")
    assert event["after"]["said_by"] == "Marie"
    assert event["after"]["quote"].startswith("In the planning call")
    assert new["confirmation"] == "unconfirmed" and new["project_wide"] is True
    assert new["nodes"] == [story["ref"]]
    assert (await get(client, decision))["confirmed_by"] == "Marie"

    # A decision nobody confirmed can be withdrawn by an agent alone.
    unconfirmed = await make_decision(client, "Just an idea")
    await withdraw(client, unconfirmed, actor="agent-3")
    assert "said_by" not in (await history(client, unconfirmed))[-1]["after"]

    # And a confirmed one by the person's word, which the history keeps with the agent's name.
    third = await make_decision(client, "Another confirmed one")
    await confirm(client, "decision", third, "Ann")
    await withdraw(client, third, actor="agent-3", said_by="Ann", quote="It was never right")
    event = (await history(client, third))[-1]
    assert (event["action"], event["actor"], event["after"]["said_by"]) == (
        "decision_withdrawn",
        "agent-3",
        "Ann",
    )
    assert event["after"]["quote"] == "It was never right"


async def test_a_secret_in_what_a_decision_says_is_refused_and_nothing_is_stored(
    client: Client,
) -> None:
    secret = "ghp_" + "a1B2c3D4" * 5
    decision = await make_decision(client, "Calm decision")
    before = await tally(client)
    good = {"actor": "a", "title": "T", "text": "t", "reason": "r", "decided_by": "Ann"}

    cases: list[tuple[str, dict[str, Any]]] = [
        ("record_decision", {**good, "title": f"Key {secret}"}),
        ("record_decision", {**good, "text": f"The key is {secret}"}),
        ("record_decision", {**good, "reason": f"Because {secret}"}),
        ("record_decision", {**good, "decided_by": secret}),
        ("supersede_decision", {**good, "decision_id": decision["id"], "reason": secret}),
        ("withdraw_decision", {"actor": "a", "decision_id": decision["id"], "reason": secret}),
        (
            "set_confirmation",
            {
                "actor": "a",
                "kind": "decision",
                "record_id": decision["id"],
                "confirmation": "confirmed",
                "person": "Marie",
                "quote": secret,
            },
        ),
    ]
    for tool, arguments in cases:
        message = await refused(client, tool, **arguments)
        assert "contains what looks like a GitHub token" in message, message
        assert secret not in message and secret[:12] not in message
    assert await tally(client) == before
    assert await get(client, decision) == decision
    for column in ("title", "text", "reason", "decided_by"):
        found = run_sql(
            f"SELECT count(*) FROM decisions WHERE {column} LIKE '%{secret[:12]}%'", plain=True
        )
        assert found.stdout.strip() == "0", found.stderr


async def test_confirming_and_withdrawing_a_decision_at_once_never_leave_it_withdrawn_confirmed(
    client: Client,
) -> None:
    for _ in range(6):
        decision = await make_decision(client, "Raced")
        results = await race_mixed(
            client,
            [
                (
                    "set_confirmation",
                    {
                        "actor": "agent-1",
                        "kind": "decision",
                        "record_id": decision["id"],
                        "confirmation": "confirmed",
                        "person": "Marie",
                    },
                ),
                (
                    "withdraw_decision",
                    {"actor": "agent-2", "decision_id": decision["id"], "reason": "No"},
                ),
            ],
        )
        # Either the person's confirmation lands first and an agent alone can no longer withdraw
        # it, or the withdrawal lands first and there is nothing left to confirm.
        assert [accepted for accepted, _ in results].count(True) == 1, results
        now = await get(client, decision)
        assert (now["status"], now["confirmation"]) in {
            ("active", "confirmed"),
            ("withdrawn", "unconfirmed"),
        }
