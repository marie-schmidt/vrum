"""The filter language against the running containers, through a real MCP client (see ./check.sh):
what each kind of term finds in real data, that `query` gives the same answer however it is
combined with the other filters of list_nodes, and what a bad query is told.

Every query here starts with `epic:<id>`, so that each test sees only the items it made."""

from typing import Any

import pytest
from mcp import Client

from tests.helpers import all_nodes, day, ok, refused, unique_word

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def made(
    client: Client, kind: str, title: str, parent: int | None = None, **more: Any
) -> int:
    fields: dict[str, Any] = {"actor": "agent-1", "kind": kind, "title": title, **more}
    if parent:
        fields["parent_id"] = parent
    return int((await ok(client, "create_node", **fields))["id"])


async def found(client: Client, epic: int, query: str, **filters: Any) -> list[int]:
    nodes = await all_nodes(client, query=f"epic:{epic} {query}".strip(), **filters)
    return [node["id"] for node in nodes]


class World:
    """An epic with two stories and five tasks that differ in everything the language can ask."""

    epic: int
    word: str
    s1: int
    s2: int
    t_ready: int
    t_going: int
    t_blocked: int
    t_late: int
    t_waiting: int
    prerequisite: int


async def world(client: Client) -> World:
    w = World()
    w.word = unique_word()
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
        node_id=w.t_going,
        expected_version=1,
        status="in_progress",
    )
    await ok(client, "claim_node", actor="Ada Lovelace", node_id=w.t_going)
    await ok(
        client, "update_node", actor="a", node_id=w.t_blocked, expected_version=1, status="blocked"
    )
    await ok(
        client,
        "add_link",
        actor="a",
        source_id=w.t_waiting,
        target_id=w.prerequisite,
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

    async def ids(query: str) -> list[int]:
        return sorted(await found(client, w.epic, query))

    assert await ids("kind:story") == sorted([w.s1, w.s2])
    assert await ids("kind:epic,story") == sorted([w.epic, w.s1, w.s2])
    assert await ids("status:in_progress") == [w.t_going]
    assert await ids("status:blocked,in_progress") == sorted([w.t_going, w.t_blocked])
    assert await ids("kind:task status:ready") == sorted([w.t_ready, w.t_late, w.prerequisite])
    assert await ids("priority:urgent,low") == sorted([w.t_ready, w.t_blocked])
    assert await ids("claimed:ada") == []
    assert await ids('claimed:"ada lovelace"') == [w.t_going]
    assert await ids("claimed") == [w.t_going]
    assert await ids("kind:task -claimed") == sorted(
        [w.t_ready, w.t_blocked, w.t_late, w.prerequisite, w.t_waiting]
    )
    assert await ids("kind:task no:claimed") == await ids("kind:task -claimed")
    assert await ids(f"parent:{w.s1}") == sorted([w.t_ready, w.t_going])
    assert await ids(f"story:{w.s2}") == sorted(
        [w.s2, w.t_blocked, w.t_late, w.prerequisite, w.t_waiting]
    )
    assert await ids(f"id:{w.t_late},#{w.t_ready}") == sorted([w.t_late, w.t_ready])
    assert await ids("is:overdue") == [w.t_late]
    assert await ids("waiting") == [w.t_waiting]
    assert await ids("is:waiting kind:task") == [w.t_waiting]
    assert await ids("no:parent") == [w.epic]
    assert await ids("no:target kind:story") == sorted([w.s1, w.s2])


async def test_words_find_part_of_a_title_or_body_and_percent_signs_mean_themselves(
    client: Client,
) -> None:
    w = await world(client)
    await ok(
        client,
        "update_node",
        actor="a",
        node_id=w.s2,
        expected_version=1,
        body="Mentions the INVOICE too",
    )

    async def ids(query: str) -> list[int]:
        return sorted(await found(client, w.epic, query))

    assert await ids("INVOICE") == sorted([w.t_ready, w.s2])
    assert await ids('"the invoice template"') == [w.t_ready]
    assert await ids("invoice -template") == [w.s2]
    assert await ids("100%_sure") == [w.t_blocked]
    # A percent sign is not a wildcard, and an underscore is not a single letter.
    assert await ids("100%") == [w.t_blocked]
    assert await ids("1%0") == []
    assert await ids("100_sure") == []
    assert await ids("sure ratio") == [w.t_blocked]
    # The words "claimed" and "waiting" are words when they are quoted.
    assert await ids('"claimed"') == []


async def test_dates_take_a_day_a_range_an_open_range_or_none(client: Client) -> None:
    w = await world(client)

    async def ids(query: str) -> list[int]:
        return sorted(await found(client, w.epic, query))

    assert await ids(f"target:{day(5)}") == [w.t_going]
    assert await ids(f"target:{day(0)}..{day(10)}") == sorted([w.t_going, w.t_waiting])
    assert await ids(f"target:{day(-10)}..{day(-1)}") == [w.t_late]
    assert await ids(f"target:..{day(-1)}") == [w.t_late]
    assert await ids(f"target:{day(30)}..") == sorted([w.prerequisite, w.epic])
    assert await ids(f"start:{day(1)},{day(2)}") == sorted([w.t_going, w.t_waiting])
    assert await ids("kind:task start:none target:none") == sorted([w.t_ready, w.t_blocked])
    assert await ids("kind:task -start:none") == sorted([w.t_going, w.t_waiting])
    # Dates of an item's own: an epic or story whose span is only worked out does not match one.
    assert await ids(f"kind:story target:..{day(100)}") == []


async def test_or_joins_groups_and_the_minus_leaves_matches_out(client: Client) -> None:
    w = await world(client)

    async def ids(query: str) -> list[int]:
        return sorted(await found(client, w.epic, query))

    # `epic:N` is part of the first group only, so the second group has to carry its own.
    both = await all_nodes(
        client, query=f"epic:{w.epic} kind:task status:in_progress OR epic:{w.epic} kind:story"
    )
    assert sorted(n["id"] for n in both) == sorted([w.t_going, w.s1, w.s2])
    assert await ids("kind:task -status:ready,in_progress,blocked") == [w.t_waiting]
    assert await ids("-kind:task") == sorted([w.epic, w.s1, w.s2])


async def test_query_combines_with_the_other_filters_and_pages(client: Client) -> None:
    w = await world(client)
    tasks = await found(client, w.epic, "kind:task")
    assert len(tasks) == 6
    page = await ok(client, "list_nodes", query=f"epic:{w.epic} kind:task", limit=4)
    assert page["total"] == 6 and len(page["items"]) == 4 and page["next_after_id"] is not None
    rest = await ok(
        client,
        "list_nodes",
        query=f"epic:{w.epic} kind:task",
        limit=4,
        after_id=page["next_after_id"],
    )
    assert [n["id"] for n in page["items"] + rest["items"]] == tasks
    # The old filters still apply on top of the query.
    assert await found(client, w.epic, "kind:task", status="ready") == sorted(
        [w.t_ready, w.t_late, w.prerequisite]
    )
    assert await found(client, w.epic, "", overdue=True) == [w.t_late]
    assert await found(client, w.epic, "kind:story", kind="task") == []


async def test_a_bad_query_is_refused_in_plain_words_and_lists_nothing(client: Client) -> None:
    w = await world(client)
    for query, says in (
        ("colour:red", "there is no field called 'colour'"),
        ("kind:tsak", "kind must be one of epic, story, task"),
        ("target:soon", "is not a day"),
        ("kind:task OR", "OR needs something after it"),
    ):
        message = await refused(client, "list_nodes", query=f"epic:{w.epic} {query}")
        assert says in message and "The query was not understood" in message
        assert "Nothing was listed" in message and "field:value" in message
    # A secret in a query is refused like a secret anywhere else.
    assert "contains what looks like" in await refused(
        client, "list_nodes", query="ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    )
