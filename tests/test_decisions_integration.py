"""M4 against the running containers: decisions, principles, links, supersession and recall,
through a real MCP client (see ./check.sh)."""

from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    make_decision,
    make_rule,
    make_stories,
    make_tree,
    ok,
    race_results,
    refused,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def recall(client: Client, **arguments: Any) -> dict[str, Any]:
    """What `recall` says about decisions, in the flat shape these tests read: each section's
    decisions, with its count and its next page."""
    result = await ok(client, "recall", **arguments)
    flat: dict[str, Any] = {}
    for name, singular in (
        ("active_decisions", "active_decision"),
        ("active_principles", "active_principle"),
        ("superseded_decisions", "superseded_decision"),
    ):
        section = result[name]
        flat[name] = [entry["decision"] for entry in section["items"]]
        flat[f"{singular}_count"] = section["count"]
        flat[f"next_{name}_after_id"] = section["next_after_id"]
    return flat


def ids(decisions: list[dict[str, Any]]) -> list[int]:
    return [decision["id"] for decision in decisions]


async def history(client: Client, decision: dict[str, Any]) -> list[dict[str, Any]]:
    return list((await ok(client, "decision_history", decision_id=decision["id"]))["items"])


async def every(client: Client, **filters: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    after_id = None
    while True:
        page = await ok(client, "list_decisions", limit=200, after_id=after_id, **filters)
        found.extend(page["items"])
        after_id = page["next_after_id"]
        if after_id is None:
            return found


async def test_a_decision_is_recorded_with_who_decided_it_and_what_it_affects(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, story_id=story["id"])

    decision = await ok(
        client,
        "record_decision",
        actor="agent-1",
        title="Keep one database",
        text="The graph and the index live in one PostgreSQL database.",
        reason="Fewer moving parts to run.",
        decided_by="Marie",
        decided_on="2026-11-01",
        node_ids=[story["id"], story["parent_id"]],
        rule_ids=[rule["id"]],
    )
    assert (decision["kind"], decision["status"], decision["decided_by"]) == (
        "decision",
        "active",
        "Marie",
    )
    assert decision["decided_on"] == "2026-11-01"
    assert decision["node_ids"] == sorted([story["id"], story["parent_id"]])
    assert decision["rule_ids"] == [rule["id"]]
    assert (decision["superseded_by_id"], decision["supersedes_ids"]) == (None, [])
    assert await ok(client, "get_decision", decision_id=decision["id"]) == decision

    today = await make_decision(client, "Dated today")
    assert today["decided_on"] >= "2026-11-15"

    events = await history(client, decision)
    assert [(e["action"], e["actor"]) for e in events] == [
        ("decision_recorded", "agent-1"),
        ("decision_linked", "agent-1"),
        ("decision_linked", "agent-1"),
        ("decision_linked", "agent-1"),
    ]
    assert events[0]["before"] is None
    assert events[0]["after"]["decided_by"] == "Marie"
    assert events[0]["after"]["decided_on"] == "2026-11-01"
    # The history of what it affects shows the link too.
    story_events = (await ok(client, "node_history", node_id=story["id"]))["items"]
    assert [e["decision_id"] for e in story_events if e["action"] == "decision_linked"] == [
        decision["id"]
    ]
    rule_events = (await ok(client, "rule_history", rule_id=rule["id"]))["items"]
    assert [e["decision_id"] for e in rule_events if e["action"] == "decision_linked"] == [
        decision["id"]
    ]


async def test_recalling_a_story_returns_its_linked_active_decisions_and_all_active_principles(
    client: Client,
) -> None:
    story, other_story = await make_stories(client, 2)
    mine = await make_decision(client, "About this story", node_ids=[story["id"]])
    also_mine = await make_decision(
        client, "Also this story", node_ids=[story["id"], other_story["id"]]
    )
    elsewhere = await make_decision(client, "About the other story", node_ids=[other_story["id"]])
    unlinked = await make_decision(client, "About nothing in particular")
    linked_principle = await make_decision(
        client, "A linked principle", kind="principle", node_ids=[story["id"]]
    )
    free_principle = await make_decision(client, "A free principle", kind="principle")

    result = await recall(client, node_id=story["id"])

    assert ids(result["active_decisions"]) == [mine["id"], also_mine["id"]]
    for left_out in (elsewhere, unlinked):
        assert left_out["id"] not in ids(result["active_decisions"])
    assert all(
        d["kind"] == "decision" and d["status"] == "active" for d in result["active_decisions"]
    )
    assert result["active_decision_count"] == 2

    principles = await every(client, kind="principle", status="active")
    assert ids(result["active_principles"]) == ids(principles)
    assert {linked_principle["id"], free_principle["id"]} <= set(ids(result["active_principles"]))
    assert result["active_principle_count"] == len(principles)
    assert result["superseded_decisions"] == [] and result["superseded_decision_count"] == 0

    # Another story gets its own decisions but the same principles.
    other = await recall(client, node_id=other_story["id"])
    assert ids(other["active_decisions"]) == [also_mine["id"], elsewhere["id"]]
    assert ids(other["active_principles"]) == ids(result["active_principles"])


async def test_nothing_is_invented_for_an_item_with_no_decisions(client: Client) -> None:
    epic, story, task = await make_tree(client)
    await make_decision(client, "About the epic only", node_ids=[epic["id"]])

    for node in (story, task):
        result = await recall(client, node_id=node["id"])
        # Decisions linked to its parent are not inferred: recall is by exact link.
        assert result["active_decisions"] == []
        assert result["superseded_decisions"] == []
        assert (result["active_decision_count"], result["superseded_decision_count"]) == (0, 0)
        assert all(d["kind"] == "principle" for d in result["active_principles"])


async def test_a_superseded_decision_is_shown_as_superseded_and_never_as_active(
    client: Client,
) -> None:
    story, other_story = await make_stories(client, 2)
    old = await make_decision(client, "Use a queue", node_ids=[story["id"]])
    await make_decision(client, "Unrelated", node_ids=[story["id"]])

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

    replaced = await ok(client, "get_decision", decision_id=old["id"])
    assert (replaced["status"], replaced["superseded_by_id"]) == ("superseded", new["id"])
    # What was decided and why is kept exactly as it was.
    assert (replaced["title"], replaced["text"], replaced["reason"], replaced["decided_by"]) == (
        old["title"],
        old["text"],
        old["reason"],
        old["decided_by"],
    )

    result = await recall(client, node_id=story["id"])
    assert new["id"] in ids(result["active_decisions"])
    assert old["id"] not in ids(result["active_decisions"])
    assert [
        (d["id"], d["status"], d["superseded_by_id"]) for d in result["superseded_decisions"]
    ] == [(old["id"], "superseded", new["id"])]
    assert result["superseded_decision_count"] == 1
    assert old["id"] not in [d["id"] for d in await every(client, status="active")]
    assert old["id"] in ids(await every(client, status="superseded"))
    assert (await recall(client, node_id=other_story["id"]))["superseded_decisions"] == []

    old_events = await history(client, old)
    assert [(e["action"], e["actor"]) for e in old_events][-1] == ("decision_superseded", "agent-2")
    assert old_events[-1]["before"] == {"status": "active", "superseded_by_id": None}
    assert old_events[-1]["after"] == {"status": "superseded", "superseded_by_id": new["id"]}
    assert (await history(client, new))[0]["after"]["supersedes_id"] == old["id"]


async def test_a_replacement_keeps_what_the_old_decision_affected_unless_told_otherwise(
    client: Client,
) -> None:
    first, second, third = await make_stories(client, 3)
    rule = await make_rule(client, story_id=first["id"])
    original = await make_decision(
        client, "Original", node_ids=[first["id"], second["id"]], rule_ids=[rule["id"]]
    )
    change = {
        "actor": "agent-2",
        "title": "Changed",
        "text": "t",
        "reason": "r",
        "decided_by": "Ann",
    }

    kept = await ok(
        client,
        "supersede_decision",
        decision_id=original["id"],
        node_ids=[third["id"]],
        **change,
    )
    assert kept["node_ids"] == [first["id"], second["id"], third["id"]]
    assert kept["rule_ids"] == [rule["id"]]
    # The old one is still linked as it was, as a record.
    assert (await ok(client, "get_decision", decision_id=original["id"]))["node_ids"] == [
        first["id"],
        second["id"],
    ]
    assert kept["id"] in ids((await recall(client, node_id=third["id"]))["active_decisions"])

    fresh = await ok(
        client,
        "supersede_decision",
        decision_id=kept["id"],
        keep_links=False,
        node_ids=[first["id"]],
        **change,
    )
    assert (fresh["node_ids"], fresh["rule_ids"]) == ([first["id"]], [])
    assert fresh["id"] not in ids((await recall(client, node_id=second["id"]))["active_decisions"])

    # A decision can become a principle when it is replaced.
    principle = await ok(
        client, "supersede_decision", decision_id=fresh["id"], kind="principle", **change
    )
    assert principle["kind"] == "principle"
    assert principle["id"] in ids((await recall(client, node_id=second["id"]))["active_principles"])


async def test_a_decision_cannot_be_superseded_twice_and_only_one_of_several_racers_wins(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    for _ in range(6):
        old = await make_decision(client, "Contested", node_ids=[story["id"]])
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
        assert (await ok(client, "get_decision", decision_id=old["id"]))["superseded_by_id"] == (
            replacements[0]["id"]
        )

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


async def test_links_can_be_added_and_removed_from_an_active_decision(client: Client) -> None:
    first, second = await make_stories(client, 2)
    rule = await make_rule(client, story_id=first["id"])
    decision = await make_decision(client, "Movable", node_ids=[first["id"]])

    linked = await ok(
        client,
        "link_decision",
        actor="ann",
        decision_id=decision["id"],
        node_ids=[second["id"]],
        rule_ids=[rule["id"]],
    )
    assert (linked["node_ids"], linked["rule_ids"]) == ([first["id"], second["id"]], [rule["id"]])
    assert decision["id"] in ids((await recall(client, node_id=second["id"]))["active_decisions"])
    assert decision["id"] in ids((await recall(client, rule_id=rule["id"]))["active_decisions"])

    unlinked = await ok(
        client, "unlink_decision", actor="bob", decision_id=decision["id"], node_ids=[first["id"]]
    )
    assert unlinked["node_ids"] == [second["id"]]
    assert decision["id"] not in ids(
        (await recall(client, node_id=first["id"]))["active_decisions"]
    )

    events = [(e["action"], e["actor"]) for e in await history(client, decision)]
    assert events == [
        ("decision_recorded", "agent-1"),
        ("decision_linked", "agent-1"),
        ("decision_linked", "ann"),
        ("decision_linked", "ann"),
        ("decision_unlinked", "bob"),
    ]
    node_events = (await ok(client, "node_history", node_id=first["id"]))["items"]
    assert [e["action"] for e in node_events if e["decision_id"] == decision["id"]] == [
        "decision_linked",
        "decision_unlinked",
    ]


async def test_link_changes_that_make_no_sense_change_nothing(client: Client) -> None:
    first, second = await make_stories(client, 2)
    decision = await make_decision(client, "Careful", node_ids=[first["id"]])
    replaced = await make_decision(client, "Replaced", node_ids=[first["id"]])
    newer = await ok(
        client,
        "supersede_decision",
        actor="a",
        decision_id=replaced["id"],
        title="Newer",
        text="t",
        reason="r",
        decided_by="Ann",
    )
    link = {"actor": "ann", "decision_id": decision["id"]}

    assert "already linked to node" in await refused(
        client, "link_decision", node_ids=[second["id"], first["id"]], **link
    )
    assert "is not linked to node" in await refused(
        client, "unlink_decision", node_ids=[second["id"]], **link
    )
    assert "There is no node with id 987654321" in await refused(
        client, "link_decision", node_ids=[987654321], **link
    )
    assert "There is no rule with id 987654321" in await refused(
        client, "link_decision", rule_ids=[987654321], **link
    )
    assert "Nothing to link" in await refused(client, "link_decision", **link)
    assert "actor must be the name" in await refused(
        client, "link_decision", node_ids=[second["id"]], actor=" ", decision_id=decision["id"]
    )
    message = await refused(
        client, "link_decision", actor="ann", decision_id=replaced["id"], node_ids=[second["id"]]
    )
    assert f"superseded by decision {newer['id']}" in message
    assert "keeps the links it had" in message
    assert "keeps the links it had" in await refused(
        client, "unlink_decision", actor="ann", decision_id=replaced["id"], node_ids=[first["id"]]
    )
    assert "There is no decision with id 987654321" in await refused(
        client, "link_decision", actor="a", decision_id=987654321, node_ids=[first["id"]]
    )

    # The failed attempts changed nothing, not even the part that was fine.
    assert (await ok(client, "get_decision", decision_id=decision["id"]))["node_ids"] == [
        first["id"]
    ]
    assert [e["action"] for e in await history(client, decision)] == [
        "decision_recorded",
        "decision_linked",
    ]


async def test_decisions_can_be_recalled_for_a_rule(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, story_id=story["id"])
    about_rule = await make_decision(client, "About the rule", rule_ids=[rule["id"]])
    about_story = await make_decision(client, "About the story", node_ids=[story["id"]])

    result = await recall(client, rule_id=rule["id"])
    assert ids(result["active_decisions"]) == [about_rule["id"]]
    assert about_story["id"] not in ids(result["active_decisions"])
    assert "There is no rule with id 987654321" in await refused(
        client, "recall", rule_id=987654321
    )


async def test_each_recall_list_can_be_paged_on_its_own(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    made = [
        (await make_decision(client, f"Linked {n}", node_ids=[story["id"]]))["id"] for n in range(3)
    ]
    for number in range(2):
        await make_decision(client, f"Principle {number}", kind="principle")

    both = await recall(client, node_id=story["id"], limit=2)
    assert ids(both["active_decisions"]) == made[:2]
    assert both["next_active_decisions_after_id"] == made[1]
    assert both["active_decision_count"] == 3

    rest = await recall(
        client,
        node_id=story["id"],
        show="active_decisions",
        limit=2,
        after_id=both["next_active_decisions_after_id"],
    )
    assert ids(rest["active_decisions"]) == made[2:]
    assert rest["next_active_decisions_after_id"] is None
    assert rest["active_principles"] == [] and rest["active_principle_count"] >= 2

    principles = await recall(client, node_id=story["id"], show="active_principles", limit=1)
    assert len(principles["active_principles"]) == 1 and principles["active_decisions"] == []
    assert principles["next_active_principles_after_id"] is not None

    assert "after_id only works for one section" in await refused(
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
    assert "limit must be a whole number" in await refused(
        client, "recall", node_id=story["id"], limit=0
    )


async def test_decisions_can_be_listed_filtered_and_paged(client: Client) -> None:
    made = [
        (await make_decision(client, f"Listed {n}", kind="principle" if n % 2 else "decision"))[
            "id"
        ]
        for n in range(4)
    ]
    first = await ok(client, "list_decisions", limit=2, after_id=made[0] - 1)
    assert ids(first["items"]) == made[:2]
    second = await ok(client, "list_decisions", limit=2, after_id=first["next_after_id"])
    assert ids(second["items"]) == made[2:4]
    assert ids((await every(client, kind="principle", status="active"))[-2:]) == [made[1], made[3]]
    assert "kind must be one of decision, principle" in await refused(
        client, "list_decisions", kind="rule"
    )
    assert "status must be one of active, superseded" in await refused(
        client, "list_decisions", status="open"
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
    before = len(await every(client))

    for field, expected in (
        ("title", "A decision needs a title"),
        ("text", "A decision needs its text"),
        ("reason", "A decision needs a reason"),
        ("decided_by", "decided_by must be the name of a person"),
        ("actor", "actor must be the name of a person or an agent"),
    ):
        assert expected in await refused(client, "record_decision", **{**good, field: "  "})
    assert "kind must be one of decision, principle" in await refused(
        client, "record_decision", kind="rule", **good
    )
    assert "There is no node with id 987654321" in await refused(
        client, "record_decision", node_ids=[987654321], **good
    )
    assert "kind must be one of" in await refused(
        client, "supersede_decision", decision_id=1, kind="rule", **{**good, "decided_by": "Ann"}
    )
    assert "There is no decision with id 987654321" in await refused(
        client, "supersede_decision", decision_id=987654321, **good
    )
    assert len(await every(client)) == before
