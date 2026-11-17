"""M13's references and find_mentions through a real MCP client against the real containers (see
./check.sh): what a reference is, who mentions whom, what a missing one looks like, and that a
reference is only text. A reference names an epic, story or task by its kind and number
(`[[story:12]]`) and a requirement, decision or fact by its kind and id (`[[rule:3]]`). Titles carry
a unique word, and each test looks only at what it made, since all tests share one database."""

from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    confirm,
    make_decision,
    make_fact,
    make_rule,
    ok,
    refused,
    unique_word,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def node(
    client: Client, kind: str, title: str, parent: str | None = None, **more: Any
) -> str:
    """Make an epic, story or task. Returns its reference."""
    fields: dict[str, Any] = {"actor": "agent-1", "kind": kind, "title": title, **more}
    if parent:
        fields["parent"] = parent
    return str((await ok(client, "create_node", **fields))["ref"])


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


def who(mentions: list[dict[str, Any]]) -> list[str]:
    """The reference of the item that holds each mention (`story:12`, `fact:5`)."""
    return [m["source"]["ref"] for m in mentions]


async def test_every_kind_of_text_can_hold_a_reference_to_every_kind_of_item(
    client: Client,
) -> None:
    word = unique_word()
    epic = await node(client, "epic", f"Refs {word}")
    story = await node(client, "story", f"Story {word}", epic)
    task = await node(client, "task", f"Task {word}", story)
    rule = (await make_rule(client, f"Rule {word}", story=story))["id"]
    fact = (await make_fact(client, f"Fact {word}", nodes=[story]))["id"]
    decision = (await make_decision(client, f"Decision {word}"))["id"]

    mentioners = {
        "task": await node(
            client, "task", f"Mentioner {word}", story, body=f"Look at [[{story}]] {word}"
        ),
        "story": await node(
            client,
            "story",
            f"Other {word}",
            epic,
            body=f"See [[rule:{rule}]] and [[{story}]].",
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
            reason=f"And [[rule:{rule}]], and [[{task}]].",
        )
    )["id"]
    fact_two = (await make_fact(client, f"Fact two {word}", source=f"[[decision:{decision}]]"))[
        "id"
    ]
    fact_three = (await make_fact(client, f"Fact three [[{story}]] {word}"))["id"]
    # A fact that is withdrawn, or replaced, is still text that names things, and says what it is.
    gone = await make_fact(client, f"Gone [[{story}]] {word}")
    await ok(client, "withdraw_fact", actor="a", fact_ids=[gone["id"]], reason="It was wrong.")

    # Who mentions the story: a story's body, a task's body and two facts' texts, in item order
    # (epics, stories, tasks, requirements, decisions, facts; each by number).
    by_story = {m["source"]["ref"]: m for m in await everything(client, node=story)}
    assert who(await everything(client, node=story)) == [
        mentioners["story"],
        mentioners["task"],
        f"fact:{fact_three}",
        f"fact:{gone['id']}",
    ]
    assert by_story[mentioners["task"]]["fields"] == ["details"]
    assert by_story[mentioners["story"]]["fields"] == ["details"]
    assert by_story[f"fact:{fact_three}"]["fields"] == ["text"]
    assert by_story[f"fact:{fact_three}"]["source"]["status"] == "active"
    assert by_story[f"fact:{gone['id']}"]["source"]["status"] == "withdrawn"
    assert f"[[{story}]]" in by_story[mentioners["story"]]["snippet"]
    assert by_story[mentioners["task"]]["source"]["status"]
    # An epic, story or task is named by its ref alone and has no id; a fact has both.
    assert "id" not in by_story[mentioners["task"]]["source"]
    assert by_story[f"fact:{fact_three}"]["source"]["id"] == fact_three
    assert all(
        m["target"]
        == {"item_type": "story", "ref": story, "exists": True, "title": f"Story {word}"}
        for m in by_story.values()
    )

    # Each other kind of item, each from another kind of text.
    assert who(await everything(client, rule_id=rule)) == [
        mentioners["story"],
        f"decision:{decision_two}",
    ]
    assert who(await everything(client, decision_id=decision)) == [
        f"rule:{rule_two}",
        f"fact:{fact_two}",
    ]
    assert who(await everything(client, fact_id=fact)) == [f"decision:{decision_two}"]
    assert who(await everything(client, node=task)) == [f"decision:{decision_two}"]
    (reason,) = await everything(client, node=task)
    assert reason["fields"] == ["reason"] and reason["source"]["title"] == f"Decision two {word}"
    # A fact's source is a text too.
    (in_source,) = [
        m
        for m in await everything(client, decision_id=decision)
        if m["source"]["ref"] == f"fact:{fact_two}"
    ]
    assert in_source["fields"] == ["source"]
    # Nobody mentions the epic.
    assert await everything(client, node=epic) == []


async def test_a_replaced_decision_still_names_what_it_named_and_says_it_was_replaced(
    client: Client,
) -> None:
    word = unique_word()
    epic = await node(client, "epic", f"E {word}")
    story = await node(client, "story", f"Story {word}", epic)
    first = await make_decision(client, f"First {word}", text=f"About [[{story}]].", nodes=[story])
    await confirm(client, "decision", first)
    second = await ok(
        client,
        "supersede_decision",
        actor="a",
        decision_id=first["id"],
        title=f"Second {word}",
        text="Something else now.",
        reason="Changed.",
        decided_by="Marie",
        said_by="Marie",
        quote="Change it.",
    )

    mentions = await everything(client, node=story)

    # The replaced decision is still text that names the story, and is shown as what it now is.
    assert who(mentions) == [f"decision:{first['id']}"]
    assert mentions[0]["source"]["status"] == "superseded"
    assert who(await everything(client, decision_id=second["id"])) == []


async def test_a_missing_item_and_a_kind_that_is_gone_are_plainly_not_links_and_nothing_is_linked(
    client: Client,
) -> None:
    word = unique_word()
    epic = await node(client, "epic", f"Missing {word}")
    story = await node(client, "story", f"Story {word}", epic)
    fact = (await make_fact(client, f"Fact {word}"))["id"]
    links_before = (await ok(client, "list_links", node=story))["items"]
    number = story.partition(":")[2]
    holder = await node(
        client,
        "task",
        f"Holder {word}",
        story,
        body=f"Gone [[story:987654321]] and [[decision:987654321]], fine [[fact:{fact}]], "
        f"and not references: [[node:{number}]] [[belief:{fact}]] [[principle:{fact}]] "
        f"[[memory:{fact}]] [[story: {number}]] [[story:0{number}]] [[story:0]] "
        f"[[ story:{number} ]] [[stories:{number}]] [[Story:{number}]] [story:{number}] "
        f"[[story:{number}].",
    )
    mine = [m for m in await everything(client) if m["source"]["ref"] == holder]
    by_target = {m["target"]["ref"]: m["target"] for m in mine}
    assert set(by_target) == {"story:987654321", "decision:987654321", f"fact:{fact}"}
    assert by_target["story:987654321"] == {
        "item_type": "story", "ref": "story:987654321", "exists": False, "title": None,
    }  # fmt: skip
    assert by_target["decision:987654321"]["exists"] is False
    assert by_target[f"fact:{fact}"]["exists"] is True
    assert by_target[f"fact:{fact}"]["item_type"] == "fact"
    assert by_target[f"fact:{fact}"]["id"] == fact
    # A reference is only text: no link, and the story's own state is as it was.
    assert (await ok(client, "list_links", node=story))["items"] == links_before
    assert (await ok(client, "list_links", node=holder))["items"] == []
    assert (await ok(client, "get_node", node=holder))["status"] in ("new", "ready")


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
        node=story,
        expected_version=1,
        body=f"Itself [[{story}]], and the other [[{other}]] and [[{other}]] again.",
    )
    assert await everything(client, node=story) == []
    (mention,) = await everything(client, node=other)
    assert mention["source"]["ref"] == story and mention["fields"] == ["details"]
    # It follows a rename: the answer gives the title now.
    await ok(
        client,
        "update_node",
        actor="agent-1",
        node=other,
        expected_version=1,
        title=f"Renamed {word}",
    )
    (mention,) = await everything(client, node=other)
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
            node=story,
            expected_version=1,
            body=f"[[{stories[(n + 1) % 3]}]] [[{stories[(n + 2) % 3]}]]",
        )
    everyone = [m for m in await everything(client) if m["source"]["ref"] in stories]
    assert len(everyone) == 6
    # Taken one item at a time, each item's mentioners are the sources of the references to it.
    for story in stories:
        wanted = sorted(m["source"]["ref"] for m in everyone if m["target"]["ref"] == story)
        assert sorted(who(await everything(client, node=story))) == wanted
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
    said = await refused(client, "find_mentions", node=story, rule_id=1)
    assert "Give at most one of node, rule_id, decision_id or fact_id" in said
    assert "There is no story 987654321 in this project" in await refused(
        client, "find_mentions", node="story:987654321"
    )
    assert "There is no rule with id" in await refused(client, "find_mentions", rule_id=987654321)
    assert "There is no decision with id" in await refused(
        client, "find_mentions", decision_id=987654321
    )
    assert "There is no fact with id" in await refused(client, "find_mentions", fact_id=987654321)
    assert "offset must be 0 or more" in await refused(client, "find_mentions", offset=-1)
    assert "limit must be a whole number from 1 to 200" in await refused(
        client, "find_mentions", limit=0
    )
