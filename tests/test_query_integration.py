"""The filter language against the running containers, through a real MCP client (see ./check.sh):
what each kind of term finds in real data, that `query` gives the same answer however it is
combined with the other filters of list_nodes, and what a bad query is told.

Every query here starts with the epic's own term (`epic:3`, which is also the epic's reference), so
that each test sees only the items it made."""

from typing import Any

import pytest
from mcp import Client

from tests.helpers import all_nodes, connect_as, day, number_of, ok, refused, unique_word

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def made(
    client: Client, kind: str, title: str, parent: str | None = None, **more: Any
) -> str:
    fields: dict[str, Any] = {"actor": "agent-1", "kind": kind, "title": title, **more}
    if parent:
        fields["parent"] = parent
    return str((await ok(client, "create_node", **fields))["ref"])


async def found(client: Client, epic: str, query: str, **filters: Any) -> list[str]:
    # An epic's reference is the term that finds it and everything under it.
    nodes = await all_nodes(client, query=f"{epic} {query}".strip(), **filters)
    return [node["ref"] for node in nodes]


class World:
    """An epic with two stories and five tasks that differ in everything the language can ask."""

    epic: str
    word: str
    agent: str
    s1: str
    s2: str
    t_ready: str
    t_going: str
    t_blocked: str
    t_late: str
    t_waiting: str
    prerequisite: str


async def world(client: Client) -> World:
    w = World()
    w.word = unique_word()
    # Who holds a claim is the agent its key is named for, so the claim is made with a key of
    # its own.
    w.agent = f"Ada Lovelace {w.word}"
    w.epic = await made(client, "epic", f"Epic {w.word}", target_on=day(90))
    w.s1 = await made(client, "story", f"Billing story {w.word}", w.epic, priority="high")
    w.s2 = await made(client, "story", f"Portal story {w.word}", w.epic)
    w.t_ready = await made(
        client, "task", f"Write the invoice template {w.word}", w.s1, priority="urgent"
    )
    w.t_going = await made(
        client, "task", f"Send the mail {w.word}", w.s1, start_on=day(1), target_on=day(5)
    )
    w.t_blocked = await made(client, "task", f"100%_sure ratio {w.word}", w.s2, priority="low")
    w.t_late = await made(client, "task", f"Fix the late bug {w.word}", w.s2, target_on=day(-3))
    w.prerequisite = await made(client, "task", f"Prerequisite {w.word}", w.s2, target_on=day(30))
    w.t_waiting = await made(
        client, "task", f"Waits on it {w.word}", w.s2, start_on=day(2), target_on=day(8)
    )
    await ok(
        client,
        "update_node",
        actor="a",
        node=w.t_going,
        expected_version=1,
        status="in_progress",
    )
    async with connect_as(w.agent) as ada:
        await ok(ada, "claim_node", node=w.t_going)
    await ok(
        client, "update_node", actor="a", node=w.t_blocked, expected_version=1, status="blocked"
    )
    await ok(
        client,
        "add_link",
        actor="a",
        source=w.t_waiting,
        target=w.prerequisite,
        link_type="needs_first",
    )
    return w


async def test_each_kind_of_term_finds_what_it_says(client: Client) -> None:
    w = await world(client)
    everything = await found(client, w.epic, "")
    assert sorted(everything) == sorted(
        [
            w.epic,
            w.s1,
            w.s2,
            w.t_ready,
            w.t_going,
            w.t_blocked,
            w.t_late,
            w.prerequisite,
            w.t_waiting,
        ]
    )
    # Listed epics first, then stories, then tasks, each by number.
    assert everything == [w.epic, w.s1, w.s2] + sorted(everything[3:], key=number_of)

    async def refs(query: str) -> list[str]:
        return sorted(await found(client, w.epic, query))

    assert await refs("kind:story") == sorted([w.s1, w.s2])
    assert await refs("kind:epic,story") == sorted([w.epic, w.s1, w.s2])
    assert await refs("status:in_progress") == [w.t_going]
    assert await refs("status:blocked,in_progress") == sorted([w.t_going, w.t_blocked])
    assert await refs("kind:task status:ready") == sorted([w.t_ready, w.t_late, w.prerequisite])
    assert await refs("priority:urgent,low") == sorted([w.t_ready, w.t_blocked])
    assert await refs("claimed:ada") == []
    assert await refs(f'claimed:"{w.agent.lower()}"') == [w.t_going]
    assert await refs("claimed") == [w.t_going]
    assert await refs("kind:task -claimed") == sorted(
        [w.t_ready, w.t_blocked, w.t_late, w.prerequisite, w.t_waiting]
    )
    assert await refs("kind:task no:claimed") == await refs("kind:task -claimed")
    # `parent:` takes a reference and finds what is directly under it.
    assert await refs(f"parent:{w.s1}") == sorted([w.t_ready, w.t_going])
    assert await refs(f"parent:{w.epic}") == sorted([w.s1, w.s2])
    # `story:` takes the number, as a person types it, and finds the story and its tasks.
    assert await refs(f"story:{number_of(w.s2)}") == sorted(
        [w.s2, w.t_blocked, w.t_late, w.prerequisite, w.t_waiting]
    )
    # `number:` is the number within a kind, so it goes with `kind:`.
    both = f"number:{number_of(w.t_late)},{number_of(w.t_ready)}"
    assert await refs(f"kind:task {both}") == sorted([w.t_late, w.t_ready])
    assert await refs("is:overdue") == [w.t_late]
    assert await refs("waiting") == [w.t_waiting]
    assert await refs("is:waiting kind:task") == [w.t_waiting]
    assert await refs("no:parent") == [w.epic]
    assert await refs("no:target kind:story") == sorted([w.s1, w.s2])


async def test_epic_and_story_terms_take_the_number_of_that_kind(client: Client) -> None:
    w = await world(client)
    other = await made(client, "epic", f"Another epic {w.word}")
    twin = await made(client, "story", f"Twin story {w.word}", other)

    # Each term finds only its own kind's number, whatever the other kinds are numbered.
    by_epic = await all_nodes(client, query=f"epic:{number_of(w.epic)}")
    assert {n["ref"] for n in by_epic} >= {w.epic, w.s1, w.s2, w.t_ready}
    assert twin not in {n["ref"] for n in by_epic}
    only_other = await all_nodes(client, query=f"epic:{number_of(other)}")
    assert [n["ref"] for n in only_other] == [other, twin]
    by_story = await all_nodes(client, query=f"story:{number_of(twin)}")
    assert [n["ref"] for n in by_story] == [twin]


async def test_words_find_part_of_a_title_or_body_and_percent_signs_mean_themselves(
    client: Client,
) -> None:
    w = await world(client)
    await ok(
        client,
        "update_node",
        actor="a",
        node=w.s2,
        expected_version=1,
        body="Mentions the INVOICE too",
    )

    async def refs(query: str) -> list[str]:
        return sorted(await found(client, w.epic, query))

    assert await refs("INVOICE") == sorted([w.t_ready, w.s2])
    assert await refs('"the invoice template"') == [w.t_ready]
    assert await refs("invoice -template") == [w.s2]
    assert await refs("100%_sure") == [w.t_blocked]
    # A percent sign is not a wildcard, and an underscore is not a single letter.
    assert await refs("100%") == [w.t_blocked]
    assert await refs("1%0") == []
    assert await refs("100_sure") == []
    assert await refs("sure ratio") == [w.t_blocked]
    # The words "claimed" and "waiting" are words when they are quoted.
    assert await refs('"claimed"') == []


async def test_dates_take_a_day_a_range_an_open_range_or_none(client: Client) -> None:
    w = await world(client)

    async def refs(query: str) -> list[str]:
        return sorted(await found(client, w.epic, query))

    assert await refs(f"target:{day(5)}") == [w.t_going]
    assert await refs(f"target:{day(0)}..{day(10)}") == sorted([w.t_going, w.t_waiting])
    assert await refs(f"target:{day(-10)}..{day(-1)}") == [w.t_late]
    assert await refs(f"target:..{day(-1)}") == [w.t_late]
    assert await refs(f"target:{day(30)}..") == sorted([w.prerequisite, w.epic])
    assert await refs(f"start:{day(1)},{day(2)}") == sorted([w.t_going, w.t_waiting])
    assert await refs("kind:task start:none target:none") == sorted([w.t_ready, w.t_blocked])
    assert await refs("kind:task -start:none") == sorted([w.t_going, w.t_waiting])
    # Dates of an item's own: an epic or story whose span is only worked out does not match one.
    assert await refs(f"kind:story target:..{day(100)}") == []


async def test_or_joins_groups_and_the_minus_leaves_matches_out(client: Client) -> None:
    w = await world(client)

    async def refs(query: str) -> list[str]:
        return sorted(await found(client, w.epic, query))

    # The epic term is part of the first group only, so the second group has to carry its own.
    both = await all_nodes(
        client, query=f"{w.epic} kind:task status:in_progress OR {w.epic} kind:story"
    )
    assert sorted(n["ref"] for n in both) == sorted([w.t_going, w.s1, w.s2])
    assert await refs("kind:task -status:ready,in_progress,blocked") == [w.t_waiting]
    assert await refs("-kind:task") == sorted([w.epic, w.s1, w.s2])


async def test_query_combines_with_the_other_filters_and_pages(client: Client) -> None:
    w = await world(client)
    tasks = await found(client, w.epic, "kind:task")
    assert len(tasks) == 6
    page = await ok(client, "list_nodes", query=f"{w.epic} kind:task", limit=4)
    assert page["total"] == 6 and len(page["items"]) == 4 and page["next_after"] is not None
    rest = await ok(
        client,
        "list_nodes",
        query=f"{w.epic} kind:task",
        limit=4,
        after=page["next_after"],
    )
    assert [n["ref"] for n in page["items"] + rest["items"]] == tasks
    # The old filters still apply on top of the query.
    assert await found(client, w.epic, "kind:task", status="ready") == sorted(
        [w.t_ready, w.t_late, w.prerequisite], key=number_of
    )
    assert await found(client, w.epic, "", overdue=True) == [w.t_late]
    assert await found(client, w.epic, "kind:story", kind="task") == []
    assert await found(client, w.epic, "", parent=w.s1) == [w.t_ready, w.t_going]


async def test_a_bad_query_is_refused_in_plain_words_and_lists_nothing(client: Client) -> None:
    w = await world(client)
    for query, says in (
        ("colour:red", "there is no field called 'colour'"),
        ("id:3", "there is no field called 'id'"),
        ("kind:tsak", "kind must be one of epic, story, task"),
        ("target:soon", "is not a day"),
        ("kind:task OR", "OR needs something after it"),
        ("parent:12", "parent needs the reference of an epic or a story"),
        ("parent:task:", "parent needs the reference of an epic or a story"),
        ("story:abc", "story needs a whole number"),
        ("number:#4", "number needs a whole number"),
    ):
        message = await refused(client, "list_nodes", query=f"{w.epic} {query}")
        assert says in message and "The query was not understood" in message
        assert "Nothing was listed" in message and "field:value" in message
    # A secret in a query is refused like a secret anywhere else.
    assert "contains what looks like" in await refused(
        client, "list_nodes", query="ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    )
