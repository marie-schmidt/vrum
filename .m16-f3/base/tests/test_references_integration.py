"""M13's references and find_mentions through a real MCP client against the real containers (see
./check.sh): what a reference is, who mentions whom, what a missing one looks like, and that a
reference is only text. Titles carry a unique word, and each test looks only at what it made,
since all tests share one database."""

from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    make_belief,
    make_decision,
    make_fact,
    make_rule,
    ok,
    refused,
    unique_word,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def node(
    client: Client, kind: str, title: str, parent: int | None = None, **more: Any
) -> int:
    fields: dict[str, Any] = {"actor": "agent-1", "kind": kind, "title": title, **more}
    if parent:
        fields["parent_id"] = parent
    return int((await ok(client, "create_node", **fields))["id"])


async def everything(client: Client, **target: Any) -> list[dict[str, Any]]:
    """Every mention for this target (or every reference when none), following the pages."""
    found: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await ok(client, "find_mentions", limit=200, offset=offset, **target)
        found.extend(page["items"])
        if page["next_offset"] is None:
            assert page["total"] == len(found)
            return found
        offset = page["next_offset"]


def who(mentions: list[dict[str, Any]]) -> list[tuple[str, int]]:
    return [(m["source"]["item_type"], m["source"]["id"]) for m in mentions]


async def test_every_kind_of_text_can_hold_a_reference_to_every_kind_of_item(
    client: Client,
) -> None:
    word = unique_word()
    epic = await node(client, "epic", f"Refs {word}")
    story = await node(client, "story", f"Story {word}", epic)
    task = await node(client, "task", f"Task {word}", story)
    rule = (await make_rule(client, f"Rule {word}", story_id=story))["id"]
    fact = (await make_fact(client, f"Fact {word}", node_ids=[story]))["id"]
    belief = (await make_belief(client, [{"id": fact}], f"Belief {word}"))["id"]
    decision = (await make_decision(client, f"Decision {word}"))["id"]

    mentioners = {
        "task": await node(
            client, "task", f"Mentioner {word}", story, body=f"Look at [[node:{story}]] {word}"
        ),
        "story": await node(
            client,
            "story",
            f"Other {word}",
            epic,
            body=f"See [[rule:{rule}]] and [[node:{story}]].",
        ),
    }
    rule_two = (
        await make_rule(client, f"Rule two {word}", body=f"Follows [[decision:{decision}]].")
    )["id"]
    decision_two = (
        await make_decision(
            client,
            f"Decision two {word}",
            text=f"Because of [[fact:{fact}]].",
            reason=f"And [[belief:{belief}]], and [[node:{task}]].",
        )
    )["id"]
    fact_two = (await make_fact(client, f"Fact two {word}", source=f"[[decision:{decision}]]"))[
        "id"
    ]
    belief_two = (await make_belief(client, [{"id": fact}], f"Belief two [[node:{story}]] {word}"))[
        "id"
    ]

    # Who mentions the story: a story's body, a task's body and a belief's text, in item order.
    by_story = {
        (m["source"]["item_type"], m["source"]["id"]): m
        for m in await everything(client, node_id=story)
    }
    assert who(await everything(client, node_id=story)) == [
        ("story", mentioners["story"]),
        ("task", mentioners["task"]),
        ("belief", belief_two),
    ]
    assert by_story["task", mentioners["task"]]["fields"] == ["details"]
    assert by_story["story", mentioners["story"]]["fields"] == ["details"]
    assert by_story["belief", belief_two]["fields"] == ["text"]
    assert f"[[node:{story}]]" in by_story["story", mentioners["story"]]["snippet"]
    assert by_story["task", mentioners["task"]]["source"]["status"]
    assert all(
        m["target"] == {"kind": "node", "id": story, "exists": True, "item_type": "story",
                        "title": f"Story {word}"}
        for m in by_story.values()
    )  # fmt: skip

    # Each other kind of item, each from another kind of text.
    assert who(await everything(client, rule_id=rule)) == [("story", mentioners["story"])]
    assert who(await everything(client, decision_id=decision)) == [
        ("rule", rule_two),
        ("fact", fact_two),
    ]
    assert who(await everything(client, memory_id=fact)) == [("decision", decision_two)]
    assert who(await everything(client, memory_id=belief)) == [("decision", decision_two)]
    assert who(await everything(client, node_id=task)) == [("decision", decision_two)]
    (reason,) = await everything(client, node_id=task)
    assert reason["fields"] == ["reason"] and reason["source"]["title"] == f"Decision two {word}"
    # A fact's source is a text too.
    (in_source,) = [
        m
        for m in await everything(client, decision_id=decision)
        if (m["source"]["item_type"], m["source"]["id"]) == ("fact", fact_two)
    ]
    assert in_source["fields"] == ["source"]
    # Nobody mentions the epic.
    assert await everything(client, node_id=epic) == []


async def test_a_missing_item_and_the_wrong_kind_are_plainly_missing_and_nothing_is_linked(
    client: Client,
) -> None:
    word = unique_word()
    epic = await node(client, "epic", f"Missing {word}")
    story = await node(client, "story", f"Story {word}", epic)
    fact = (await make_fact(client, f"Fact {word}"))["id"]
    links_before = (await ok(client, "list_links", node_id=story))["items"]
    holder = await node(
        client,
        "task",
        f"Holder {word}",
        story,
        body=f"Gone [[node:987654321]], wrong kind [[belief:{fact}]], fine [[fact:{fact}]], "
        "and not references: [[node: 5]] [[node:05]] [[ node:5 ]] [[nodes:5]] [node:5] [[node:5].",
    )
    mine = [
        m
        for m in await everything(client)
        if (m["source"]["item_type"], m["source"]["id"]) == ("task", holder)
    ]
    by_target = {(m["target"]["kind"], m["target"]["id"]): m["target"] for m in mine}
    assert set(by_target) == {("node", 987654321), ("belief", fact), ("fact", fact)}
    assert by_target[("node", 987654321)] == {
        "kind": "node", "id": 987654321, "exists": False, "item_type": None, "title": None,
    }  # fmt: skip
    # A belief written for a fact's id is missing, and the real fact is not.
    assert by_target[("belief", fact)]["exists"] is False
    assert by_target[("fact", fact)]["exists"] is True
    assert by_target[("fact", fact)]["item_type"] == "fact"
    # A reference is only text: no link, and the story's own state is as it was.
    assert (await ok(client, "list_links", node_id=story))["items"] == links_before
    assert (await ok(client, "list_links", node_id=holder))["items"] == []
    assert (await ok(client, "get_node", node_id=holder))["status"] in ("new", "ready")


async def test_an_item_that_names_itself_is_not_listed_and_one_that_names_something_twice_is_once(
    client: Client,
) -> None:
    word = unique_word()
    epic = await node(client, "epic", f"Self {word}")
    story = await node(client, "story", f"Story {word}", epic)
    other = await node(client, "story", f"Other {word}", epic)
    await ok(
        client,
        "update_node",
        actor="agent-1",
        node_id=story,
        expected_version=1,
        body=f"Itself [[node:{story}]], and the other [[node:{other}]] and [[node:{other}]] again.",
    )
    assert await everything(client, node_id=story) == []
    (mention,) = await everything(client, node_id=other)
    assert mention["source"]["id"] == story and mention["fields"] == ["details"]
    # It follows a rename: the answer gives the title now.
    await ok(
        client,
        "update_node",
        actor="agent-1",
        node_id=other,
        expected_version=1,
        title=f"Renamed {word}",
    )
    (mention,) = await everything(client, node_id=other)
    assert mention["target"]["title"] == f"Renamed {word}"


async def test_with_no_target_every_reference_is_listed_and_equals_the_per_item_answers(
    client: Client,
) -> None:
    word = unique_word()
    epic = await node(client, "epic", f"All {word}")
    stories = [await node(client, "story", f"S{n} {word}", epic) for n in range(3)]
    for n, story in enumerate(stories):
        await ok(
            client,
            "update_node",
            actor="agent-1",
            node_id=story,
            expected_version=1,
            body=f"[[node:{stories[(n + 1) % 3]}]] [[node:{stories[(n + 2) % 3]}]]",
        )
    everyone = [
        m
        for m in await everything(client)
        if m["source"]["item_type"] == "story" and m["source"]["id"] in stories
    ]
    assert len(everyone) == 6
    # Taken one item at a time, each item's mentioners are the sources of the references to it.
    for story in stories:
        wanted = sorted(
            m["source"]["id"]
            for m in everyone
            if m["target"]["kind"] == "node" and m["target"]["id"] == story
        )
        assert sorted(m["source"]["id"] for m in await everything(client, node_id=story)) == wanted
    # Paged: two at a time gives the same, in the same order.
    paged: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await ok(client, "find_mentions", limit=2, offset=offset)
        paged.extend(page["items"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert paged == await everything(client)


async def test_a_bad_request_is_refused_in_plain_words(client: Client) -> None:
    story = await node(client, "epic", f"Bad {unique_word()}")
    said = await refused(client, "find_mentions", node_id=story, rule_id=1)
    assert "Give at most one of node_id, rule_id, decision_id or memory_id" in said
    assert "There is no node with id 987654321" in await refused(
        client, "find_mentions", node_id=987654321
    )
    assert "There is no rule with id" in await refused(client, "find_mentions", rule_id=987654321)
    assert "offset must be 0 or more" in await refused(client, "find_mentions", offset=-1)
    assert "limit must be a whole number from 1 to 200" in await refused(
        client, "find_mentions", limit=0
    )
