"""M2 against the running containers: links, readiness and claims, through a real MCP client
(see ./check.sh)."""

import anyio
import pytest
from mcp import Client

from tests.helpers import (
    connect_as,
    connect_each,
    make_tasks,
    make_tree,
    needs_first,
    ok,
    refused,
    set_status,
    unique_word,
)
from tests.world import HARNESS_AGENT

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def all_ready_refs(client: Client) -> list[str]:
    """Every reference ready_work offers, following the pages."""
    found: list[str] = []
    after = None
    while True:
        page = await ok(client, "ready_work", limit=200, after=after)
        found.extend(node["ref"] for node in page["items"])
        after = page["next_after"]
        if after is None:
            return found


async def waiting_on(client: Client, node: dict[str, object]) -> list[str]:
    return list((await ok(client, "get_node", node=node["ref"]))["waiting_on"])


# --- links ---------------------------------------------------------------------------------


async def test_a_circle_of_waiting_is_refused_whatever_its_length(client: Client) -> None:
    first, second, third = await make_tasks(client, 3)

    await needs_first(client, first, second)
    message = await refused(
        client,
        "add_link",
        actor="marie",
        source=second["ref"],
        target=first["ref"],
        link_type="needs_first",
    )
    assert "circle of waiting" in message
    assert f"{second['ref']} would need {first['ref']}" in message

    await needs_first(client, second, third)
    message = await refused(
        client,
        "add_link",
        actor="marie",
        source=third["ref"],
        target=first["ref"],
        link_type="needs_first",
    )
    assert "circle of waiting" in message
    assert first["ref"] in message and third["ref"] in message

    links = (await ok(client, "list_links", node=third["ref"]))["items"]
    assert [(link["source"], link["target"]) for link in links] == [(second["ref"], third["ref"])]


async def test_two_links_added_together_cannot_make_a_circle(client: Client) -> None:
    pairs = [await make_tasks(client, 2) for _ in range(5)]
    outcomes: dict[int, list[bool]] = {number: [] for number in range(5)}

    async def add(number: int, source: dict[str, object], target: dict[str, object]) -> None:
        result = await client.call_tool(
            "add_link",
            {
                "actor": f"agent-{number}",
                "source": source["ref"],
                "target": target["ref"],
                "link_type": "needs_first",
            },
        )
        outcomes[number].append(not result.is_error)

    async with anyio.create_task_group() as group:
        for number, (first, second) in enumerate(pairs):
            group.start_soon(add, number, first, second)
            group.start_soon(add, number, second, first)

    assert all(results.count(True) == 1 for results in outcomes.values()), outcomes


async def test_links_that_make_no_sense_are_refused_in_plain_words(client: Client) -> None:
    first, second = await make_tasks(client, 2)
    link = {"actor": "marie", "source": first["ref"], "target": second["ref"]}

    assert "cannot be linked to itself" in await refused(
        client,
        "add_link",
        actor="marie",
        source=first["ref"],
        target=first["ref"],
        link_type="relates_to",
    )
    assert "link_type must be one of needs_first, supersedes, duplicates, relates_to" in (
        await refused(client, "add_link", link_type="blocks", **link)
    )
    assert "There is no task 987654321 in this project" in await refused(
        client,
        "add_link",
        actor="marie",
        source=first["ref"],
        target="task:987654321",
        link_type="relates_to",
    )

    await ok(client, "add_link", link_type="supersedes", **link)
    assert "already linked" in await refused(client, "add_link", link_type="supersedes", **link)
    # The same pair with another type is a different link.
    await ok(client, "add_link", link_type="needs_first", **link)


async def test_two_way_links_are_the_same_in_either_direction(client: Client) -> None:
    first, second = await make_tasks(client, 2)
    forward = {"actor": "marie", "source": first["ref"], "target": second["ref"]}
    backward = {"actor": "marie", "source": second["ref"], "target": first["ref"]}

    for link_type in ("duplicates", "relates_to"):
        await ok(client, "add_link", link_type=link_type, **forward)
        assert "already linked" in await refused(
            client, "add_link", link_type=link_type, **backward
        )

    # needs_first has a direction, so the reverse is a different link (and here a circle).
    await ok(client, "add_link", link_type="supersedes", **forward)
    await ok(client, "add_link", link_type="supersedes", **backward)

    removed = await ok(client, "remove_link", link_type="relates_to", **backward)
    assert (removed["source"], removed["target"]) == (first["ref"], second["ref"])
    types = [
        link["link_type"] for link in (await ok(client, "list_links", node=first["ref"]))["items"]
    ]
    assert sorted(types) == ["duplicates", "supersedes", "supersedes"]


async def test_links_can_be_listed_filtered_paged_and_removed(client: Client) -> None:
    centre, *others = await make_tasks(client, 4)
    await needs_first(client, centre, others[0])
    await needs_first(client, others[1], centre)
    await ok(
        client,
        "add_link",
        actor="marie",
        source=centre["ref"],
        target=others[2]["ref"],
        link_type="relates_to",
    )

    everything = (await ok(client, "list_links", node=centre["ref"]))["items"]
    assert len(everything) == 3
    only_needs = (await ok(client, "list_links", node=centre["ref"], link_type="needs_first"))[
        "items"
    ]
    assert [(item["source"], item["target"]) for item in only_needs] == [
        (centre["ref"], others[0]["ref"]),
        (others[1]["ref"], centre["ref"]),
    ]

    first_page = await ok(client, "list_links", node=centre["ref"], limit=2)
    second_page = await ok(
        client, "list_links", node=centre["ref"], limit=2, after_id=first_page["next_after_id"]
    )
    assert [len(first_page["items"]), len(second_page["items"])] == [2, 1]
    assert second_page["next_after_id"] is None
    assert [item["id"] for item in first_page["items"] + second_page["items"]] == [
        item["id"] for item in everything
    ]

    removed = await ok(
        client,
        "remove_link",
        actor="ann",
        source=centre["ref"],
        target=others[0]["ref"],
        link_type="needs_first",
    )
    assert removed["link_type"] == "needs_first"
    assert len((await ok(client, "list_links", node=centre["ref"]))["items"]) == 2
    assert (
        f"There is no needs_first link from {centre['ref']} to {others[0]['ref']}"
        in await refused(
            client,
            "remove_link",
            actor="ann",
            source=centre["ref"],
            target=others[0]["ref"],
            link_type="needs_first",
        )
    )
    assert "link_type must be one of" in await refused(
        client, "list_links", node=centre["ref"], link_type="x"
    )
    assert "There is no task 987654321 in this project" in await refused(
        client, "list_links", node="task:987654321"
    )


async def test_a_link_shows_in_the_history_of_both_nodes(client: Client) -> None:
    waiting, first = await make_tasks(client, 2)
    await needs_first(client, waiting, first)
    ann = f"ann-{unique_word()}"
    async with connect_as(ann) as by_ann:
        await ok(
            by_ann,
            "remove_link",
            source=waiting["ref"],
            target=first["ref"],
            link_type="needs_first",
        )

    for node in (waiting, first):
        history = (await ok(client, "node_history", node=node["ref"]))["items"]
        events = [e for e in history if e["action"].startswith("link_")]
        assert [(e["action"], e["actor"]) for e in events] == [
            ("link_added", HARNESS_AGENT),
            ("link_removed", ann),
        ]
        assert events[0]["node"] == waiting["ref"] and events[0]["other_node"] == first["ref"]
        assert events[0]["before"] is None
        assert events[0]["after"] == {
            "link_type": "needs_first",
            "source": waiting["ref"],
            "target": first["ref"],
        }
        assert events[1]["before"] == events[0]["after"] and events[1]["after"] == {}


# --- readiness -----------------------------------------------------------------------------


async def test_finishing_a_prerequisite_makes_its_dependent_ready(client: Client) -> None:
    prerequisite, dependent = await make_tasks(client, 2)
    assert (await ok(client, "get_node", node=dependent["ref"]))["status"] == "ready"

    await needs_first(client, dependent, prerequisite)
    waiting = await ok(client, "get_node", node=dependent["ref"])
    assert (waiting["status"], waiting["waiting_on"]) == ("new", [prerequisite["ref"]])
    assert (await ok(client, "get_node", node=prerequisite["ref"]))["status"] == "ready"

    # Only done counts.
    await set_status(client, prerequisite, "in_progress")
    assert (await ok(client, "get_node", node=dependent["ref"]))["status"] == "new"
    ready = await all_ready_refs(client)
    assert dependent["ref"] not in ready

    await set_status(client, prerequisite, "done")
    now_ready = await ok(client, "get_node", node=dependent["ref"])
    assert (now_ready["status"], now_ready["waiting_on"]) == ("ready", [])
    assert now_ready["version"] == dependent["version"]
    ready = await all_ready_refs(client)
    assert dependent["ref"] in ready and prerequisite["ref"] not in ready


async def test_a_node_with_several_prerequisites_waits_for_all_of_them(client: Client) -> None:
    first, second, dependent = await make_tasks(client, 3)
    await needs_first(client, dependent, first)
    await needs_first(client, dependent, second)

    assert await waiting_on(client, dependent) == [first["ref"], second["ref"]]
    await set_status(client, first, "done")
    assert await waiting_on(client, dependent) == [second["ref"]]
    assert (await ok(client, "get_node", node=dependent["ref"]))["status"] == "new"
    await set_status(client, second, "done")
    assert (await ok(client, "get_node", node=dependent["ref"]))["status"] == "ready"


async def test_what_a_node_waits_for_is_listed_epics_then_stories_then_tasks_each_by_number(
    client: Client,
) -> None:
    epic, story, _ = await make_tree(client)
    early, late, waiting = await make_tasks(client, 3)
    for first in (late, early, story, epic):
        await needs_first(client, waiting, first)

    assert await waiting_on(client, waiting) == [
        epic["ref"],
        story["ref"],
        early["ref"],
        late["ref"],
    ]


async def test_a_dropped_prerequisite_does_not_count_as_done_until_the_link_is_removed(
    client: Client,
) -> None:
    prerequisite, dependent = await make_tasks(client, 2)
    await needs_first(client, dependent, prerequisite)
    await set_status(client, prerequisite, "dropped")
    assert (await ok(client, "get_node", node=dependent["ref"]))["status"] == "new"

    await ok(
        client,
        "remove_link",
        actor="marie",
        source=dependent["ref"],
        target=prerequisite["ref"],
        link_type="needs_first",
    )
    assert (await ok(client, "get_node", node=dependent["ref"]))["status"] == "ready"


async def test_a_started_blocked_or_finished_node_is_not_shown_as_ready(client: Client) -> None:
    started, blocked, finished = await make_tasks(client, 3)
    assert (await set_status(client, started, "in_progress"))["status"] == "in_progress"
    assert (await set_status(client, blocked, "blocked"))["status"] == "blocked"
    assert (await set_status(client, finished, "done"))["status"] == "done"
    assert "ready is worked out" in await refused(
        client,
        "update_node",
        actor="marie",
        node=started["ref"],
        expected_version=2,
        status="ready",
    )


async def test_ready_work_lists_only_free_stories_and_tasks_that_can_start_now(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    waiting, held = await make_tasks(client, 2)
    await needs_first(client, waiting, held)
    await ok(client, "claim_node", actor="ann", node=task["ref"])

    ours = {epic["ref"], story["ref"], task["ref"], waiting["ref"], held["ref"]}

    async def ready_refs(**arguments: object) -> list[str]:
        found: list[str] = []
        after = None
        while True:
            page = await ok(client, "ready_work", limit=50, after=after, **arguments)
            found.extend(n["ref"] for n in page["items"])
            after = page["next_after"]
            if after is None:
                return [ref for ref in found if ref in ours]

    # The claimed task, the waiting task and the epic are left out.
    assert await ready_refs() == [story["ref"], held["ref"]]
    assert await ready_refs(kind="epic") == [epic["ref"]]
    assert "kind must be one of" in await refused(client, "ready_work", kind="saga")
    assert "limit must be a whole number" in await refused(client, "ready_work", limit=0)


# --- claims --------------------------------------------------------------------------------


async def test_ten_parallel_claimers_on_one_ready_task_produce_exactly_one_winner(
    client: Client,
) -> None:
    (task,) = await make_tasks(client, 1)
    results: dict[str, tuple[bool, str]] = {}
    word = unique_word()

    # Each claimer is an agent of its own: a claim is held by whoever its key is named for.
    async with connect_each(*(f"agent-{number}-{word}" for number in range(10))) as agents:

        async def claim(actor: str, agent: Client) -> None:
            result = await agent.call_tool("claim_node", {"node": task["ref"]})
            text = " ".join(getattr(block, "text", "") for block in result.content)
            results[actor] = (not result.is_error, text)

        async with anyio.create_task_group() as group:
            for number, agent in enumerate(agents):
                group.start_soon(claim, f"agent-{number}-{word}", agent)

    winners = [actor for actor, (won, _) in results.items() if won]
    assert len(results) == 10 and len(winners) == 1
    winner = winners[0]
    for actor, (won, text) in results.items():
        if not won:
            assert f"claimed by {winner}" in text, (actor, text)

    held = await ok(client, "get_node", node=task["ref"])
    assert held["claimed_by"] == winner and held["claim_expires_at"] is not None
    history = (await ok(client, "node_history", node=task["ref"]))["items"]
    claimed = [e for e in history if e["action"] == "claimed"]
    assert [(e["actor"], e["before"]) for e in claimed] == [(winner, None)]


async def test_an_expired_lease_can_be_claimed_by_another_actor(client: Client) -> None:
    (task,) = await make_tasks(client, 1)
    ann, bob = f"ann-{unique_word()}", f"bob-{unique_word()}"
    async with connect_each(ann, bob) as (by_ann, by_bob):
        first = await ok(by_ann, "claim_node", node=task["ref"], lease_seconds=2)
        assert first["node"] == task["ref"]

        message = await refused(by_bob, "claim_node", node=task["ref"])
        assert f"claimed by {ann} until" in message
        await anyio.sleep(2.6)

        lapsed = await ok(client, "get_node", node=task["ref"])
        assert (lapsed["claimed_by"], lapsed["claim_expires_at"]) == (None, None)
        assert task["ref"] in await all_ready_refs(client)
        assert "lapsed" in await refused(by_ann, "renew_claim", node=task["ref"])

        second = await ok(by_bob, "claim_node", node=task["ref"], lease_seconds=60)
        assert second["actor"] == bob and second["expires_at"] > first["expires_at"]
        held = await ok(client, "get_node", node=task["ref"])
        assert held["claimed_by"] == bob

    history = (await ok(client, "node_history", node=task["ref"]))["items"]
    claimed = [e for e in history if e["action"] == "claimed"]
    assert [e["actor"] for e in claimed] == [ann, bob]
    assert claimed[1]["before"]["actor"] == ann
    assert claimed[1]["after"]["actor"] == bob


async def test_the_holder_can_renew_and_release_and_nobody_else_can(client: Client) -> None:
    (task,) = await make_tasks(client, 1)
    ann, bob = f"ann-{unique_word()}", f"bob-{unique_word()}"
    async with connect_each(ann, bob) as (by_ann, by_bob):
        claim = await ok(by_ann, "claim_node", node=task["ref"], lease_seconds=60)
        assert "already hold the claim" in await refused(by_ann, "claim_node", node=task["ref"])

        assert f"claimed by {ann}, not by you" in await refused(
            by_bob, "renew_claim", node=task["ref"]
        )
        assert f"claimed by {ann}, not by you" in await refused(
            by_bob, "release_claim", node=task["ref"], note="Not mine to release."
        )

        renewed = await ok(by_ann, "renew_claim", node=task["ref"], lease_seconds=3600)
        assert renewed["expires_at"] > claim["expires_at"]
        assert (await ok(client, "get_node", node=task["ref"]))["claim_expires_at"] == renewed[
            "expires_at"
        ]

        await ok(by_ann, "release_claim", node=task["ref"], note="Paused: back to the pool.")
        assert (await ok(client, "get_node", node=task["ref"]))["claimed_by"] is None
        assert "nothing to release" in await refused(
            by_ann, "release_claim", node=task["ref"], note="Again."
        )
        assert "do not hold a claim" in await refused(by_ann, "renew_claim", node=task["ref"])
        await ok(by_bob, "claim_node", node=task["ref"])

    history = (await ok(client, "node_history", node=task["ref"]))["items"]
    assert [(e["action"], e["actor"]) for e in history if e["action"] != "create"] == [
        ("claimed", ann),
        ("claim_renewed", ann),
        ("handoff", ann),
        ("claim_released", ann),
        ("claimed", bob),
    ]


async def test_work_that_cannot_be_done_now_cannot_be_claimed(client: Client) -> None:
    epic, story, _ = await make_tree(client)
    prerequisite, waiting, blocked, finished = await make_tasks(client, 4)
    await needs_first(client, waiting, prerequisite)
    await set_status(client, blocked, "blocked")
    await set_status(client, finished, "done")

    assert "Only stories and tasks can be claimed" in await refused(
        client, "claim_node", node=epic["ref"]
    )
    message = await refused(client, "claim_node", node=waiting["ref"])
    assert f"needs {prerequisite['ref']} first" in message
    assert "is marked blocked" in await refused(client, "claim_node", node=blocked["ref"])
    assert "is done, so there is nothing left to claim" in await refused(
        client, "claim_node", node=finished["ref"]
    )
    for seconds in (0, 86401):
        assert "lease_seconds must be a whole number from 1 to 86400" in await refused(
            client, "claim_node", node=story["ref"], lease_seconds=seconds
        )
    assert "There is no task 987654321 in this project" in await refused(
        client, "claim_node", node="task:987654321"
    )

    await ok(client, "claim_node", node=story["ref"])
    # Once its prerequisite is done, a waiting task can be claimed.
    await set_status(client, prerequisite, "done")
    await ok(client, "claim_node", node=waiting["ref"])
