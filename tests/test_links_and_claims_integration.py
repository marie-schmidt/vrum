"""M2 against the running containers: links, readiness and claims, through a real MCP client
(see ./check.sh)."""

import anyio
import pytest
from mcp import Client

from tests.helpers import make_tasks, make_tree, needs_first, ok, refused, set_status

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def all_ready_ids(client: Client) -> list[int]:
    """Every id ready_work offers, following the pages."""
    found: list[int] = []
    after_id = None
    while True:
        page = await ok(client, "ready_work", limit=200, after_id=after_id)
        found.extend(node["id"] for node in page["items"])
        after_id = page["next_after_id"]
        if after_id is None:
            return found


async def waiting_on(client: Client, node: dict[str, object]) -> list[int]:
    return list((await ok(client, "get_node", node_id=node["id"]))["waiting_on"])


# --- links ---------------------------------------------------------------------------------


async def test_a_circle_of_waiting_is_refused_whatever_its_length(client: Client) -> None:
    first, second, third = await make_tasks(client, 3)

    await needs_first(client, first, second)
    message = await refused(
        client,
        "add_link",
        actor="marie",
        source_id=second["id"],
        target_id=first["id"],
        link_type="needs_first",
    )
    assert "circle of waiting" in message
    assert f"node {second['id']} would need node {first['id']}" in message

    await needs_first(client, second, third)
    message = await refused(
        client,
        "add_link",
        actor="marie",
        source_id=third["id"],
        target_id=first["id"],
        link_type="needs_first",
    )
    assert "circle of waiting" in message
    assert f"node {first['id']}" in message and f"node {third['id']}" in message

    links = (await ok(client, "list_links", node_id=third["id"]))["items"]
    assert [(link["source_id"], link["target_id"]) for link in links] == [
        (second["id"], third["id"])
    ]


async def test_two_links_added_together_cannot_make_a_circle(client: Client) -> None:
    pairs = [await make_tasks(client, 2) for _ in range(5)]
    outcomes: dict[int, list[bool]] = {number: [] for number in range(5)}

    async def add(number: int, source: dict[str, object], target: dict[str, object]) -> None:
        result = await client.call_tool(
            "add_link",
            {
                "actor": f"agent-{number}",
                "source_id": source["id"],
                "target_id": target["id"],
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
    link = {"actor": "marie", "source_id": first["id"], "target_id": second["id"]}

    assert "cannot be linked to itself" in await refused(
        client,
        "add_link",
        actor="marie",
        source_id=first["id"],
        target_id=first["id"],
        link_type="relates_to",
    )
    assert "link_type must be one of needs_first, supersedes, duplicates, relates_to" in (
        await refused(client, "add_link", link_type="blocks", **link)
    )
    assert "There is no node with id 987654321" in await refused(
        client,
        "add_link",
        actor="marie",
        source_id=first["id"],
        target_id=987654321,
        link_type="relates_to",
    )
    assert "actor must be the name" in await refused(
        client, "add_link", link_type="relates_to", **{**link, "actor": " "}
    )

    await ok(client, "add_link", link_type="supersedes", **link)
    assert "already linked" in await refused(client, "add_link", link_type="supersedes", **link)
    # The same pair with another type is a different link.
    await ok(client, "add_link", link_type="needs_first", **link)


async def test_two_way_links_are_the_same_in_either_direction(client: Client) -> None:
    first, second = await make_tasks(client, 2)
    forward = {"actor": "marie", "source_id": first["id"], "target_id": second["id"]}
    backward = {"actor": "marie", "source_id": second["id"], "target_id": first["id"]}

    for link_type in ("duplicates", "relates_to"):
        await ok(client, "add_link", link_type=link_type, **forward)
        assert "already linked" in await refused(
            client, "add_link", link_type=link_type, **backward
        )

    # needs_first has a direction, so the reverse is a different link (and here a circle).
    await ok(client, "add_link", link_type="supersedes", **forward)
    await ok(client, "add_link", link_type="supersedes", **backward)

    removed = await ok(client, "remove_link", link_type="relates_to", **backward)
    assert (removed["source_id"], removed["target_id"]) == (first["id"], second["id"])
    types = [
        link["link_type"] for link in (await ok(client, "list_links", node_id=first["id"]))["items"]
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
        source_id=centre["id"],
        target_id=others[2]["id"],
        link_type="relates_to",
    )

    everything = (await ok(client, "list_links", node_id=centre["id"]))["items"]
    assert len(everything) == 3
    only_needs = (await ok(client, "list_links", node_id=centre["id"], link_type="needs_first"))[
        "items"
    ]
    assert [(item["source_id"], item["target_id"]) for item in only_needs] == [
        (centre["id"], others[0]["id"]),
        (others[1]["id"], centre["id"]),
    ]

    first_page = await ok(client, "list_links", node_id=centre["id"], limit=2)
    second_page = await ok(
        client, "list_links", node_id=centre["id"], limit=2, after_id=first_page["next_after_id"]
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
        source_id=centre["id"],
        target_id=others[0]["id"],
        link_type="needs_first",
    )
    assert removed["link_type"] == "needs_first"
    assert len((await ok(client, "list_links", node_id=centre["id"]))["items"]) == 2
    assert "There is no needs_first link from node" in await refused(
        client,
        "remove_link",
        actor="ann",
        source_id=centre["id"],
        target_id=others[0]["id"],
        link_type="needs_first",
    )
    assert "link_type must be one of" in await refused(
        client, "list_links", node_id=1, link_type="x"
    )
    assert "There is no node with id 987654321" in await refused(
        client, "list_links", node_id=987654321
    )


async def test_a_link_shows_in_the_history_of_both_nodes(client: Client) -> None:
    waiting, first = await make_tasks(client, 2)
    await needs_first(client, waiting, first)
    await ok(
        client,
        "remove_link",
        actor="ann",
        source_id=waiting["id"],
        target_id=first["id"],
        link_type="needs_first",
    )

    for node in (waiting, first):
        history = (await ok(client, "node_history", node_id=node["id"]))["items"]
        events = [e for e in history if e["action"].startswith("link_")]
        assert [(e["action"], e["actor"]) for e in events] == [
            ("link_added", "marie"),
            ("link_removed", "ann"),
        ]
        assert events[0]["node_id"] == waiting["id"] and events[0]["other_node_id"] == first["id"]
        assert events[0]["before"] is None
        assert events[0]["after"] == {
            "link_type": "needs_first",
            "source_id": waiting["id"],
            "target_id": first["id"],
        }
        assert events[1]["before"] == events[0]["after"] and events[1]["after"] == {}


# --- readiness -----------------------------------------------------------------------------


async def test_finishing_a_prerequisite_makes_its_dependent_ready(client: Client) -> None:
    prerequisite, dependent = await make_tasks(client, 2)
    assert (await ok(client, "get_node", node_id=dependent["id"]))["status"] == "ready"

    await needs_first(client, dependent, prerequisite)
    waiting = await ok(client, "get_node", node_id=dependent["id"])
    assert (waiting["status"], waiting["waiting_on"]) == ("new", [prerequisite["id"]])
    assert (await ok(client, "get_node", node_id=prerequisite["id"]))["status"] == "ready"

    # Only done counts.
    await set_status(client, prerequisite, "in_progress")
    assert (await ok(client, "get_node", node_id=dependent["id"]))["status"] == "new"
    ready_ids = await all_ready_ids(client)
    assert dependent["id"] not in ready_ids

    await set_status(client, prerequisite, "done")
    now_ready = await ok(client, "get_node", node_id=dependent["id"])
    assert (now_ready["status"], now_ready["waiting_on"]) == ("ready", [])
    assert now_ready["version"] == dependent["version"]
    ready_ids = await all_ready_ids(client)
    assert dependent["id"] in ready_ids and prerequisite["id"] not in ready_ids


async def test_a_node_with_several_prerequisites_waits_for_all_of_them(client: Client) -> None:
    first, second, dependent = await make_tasks(client, 3)
    await needs_first(client, dependent, first)
    await needs_first(client, dependent, second)

    assert await waiting_on(client, dependent) == [first["id"], second["id"]]
    await set_status(client, first, "done")
    assert await waiting_on(client, dependent) == [second["id"]]
    assert (await ok(client, "get_node", node_id=dependent["id"]))["status"] == "new"
    await set_status(client, second, "done")
    assert (await ok(client, "get_node", node_id=dependent["id"]))["status"] == "ready"


async def test_a_dropped_prerequisite_does_not_count_as_done_until_the_link_is_removed(
    client: Client,
) -> None:
    prerequisite, dependent = await make_tasks(client, 2)
    await needs_first(client, dependent, prerequisite)
    await set_status(client, prerequisite, "dropped")
    assert (await ok(client, "get_node", node_id=dependent["id"]))["status"] == "new"

    await ok(
        client,
        "remove_link",
        actor="marie",
        source_id=dependent["id"],
        target_id=prerequisite["id"],
        link_type="needs_first",
    )
    assert (await ok(client, "get_node", node_id=dependent["id"]))["status"] == "ready"


async def test_a_started_blocked_or_finished_node_is_not_shown_as_ready(client: Client) -> None:
    started, blocked, finished = await make_tasks(client, 3)
    assert (await set_status(client, started, "in_progress"))["status"] == "in_progress"
    assert (await set_status(client, blocked, "blocked"))["status"] == "blocked"
    assert (await set_status(client, finished, "done"))["status"] == "done"
    assert "ready is worked out" in await refused(
        client,
        "update_node",
        actor="marie",
        node_id=started["id"],
        expected_version=2,
        status="ready",
    )


async def test_ready_work_lists_only_free_stories_and_tasks_that_can_start_now(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    waiting, held = await make_tasks(client, 2)
    await needs_first(client, waiting, held)
    await ok(client, "claim_node", actor="ann", node_id=task["id"])

    ours = {epic["id"], story["id"], task["id"], waiting["id"], held["id"]}

    async def ready_ids(**arguments: object) -> list[int]:
        found: list[int] = []
        after_id = None
        while True:
            page = await ok(client, "ready_work", limit=50, after_id=after_id, **arguments)
            found.extend(n["id"] for n in page["items"])
            after_id = page["next_after_id"]
            if after_id is None:
                return [node_id for node_id in found if node_id in ours]

    # The claimed task, the waiting task and the epic are left out.
    assert await ready_ids() == [story["id"], held["id"]]
    assert await ready_ids(kind="epic") == [epic["id"]]
    assert "kind must be one of" in await refused(client, "ready_work", kind="saga")
    assert "limit must be a whole number" in await refused(client, "ready_work", limit=0)


# --- claims --------------------------------------------------------------------------------


async def test_ten_parallel_claimers_on_one_ready_task_produce_exactly_one_winner(
    client: Client,
) -> None:
    (task,) = await make_tasks(client, 1)
    results: dict[str, tuple[bool, str]] = {}

    async def claim(actor: str) -> None:
        result = await client.call_tool("claim_node", {"actor": actor, "node_id": task["id"]})
        text = " ".join(getattr(block, "text", "") for block in result.content)
        results[actor] = (not result.is_error, text)

    async with anyio.create_task_group() as group:
        for number in range(10):
            group.start_soon(claim, f"agent-{number}")

    winners = [actor for actor, (won, _) in results.items() if won]
    assert len(results) == 10 and len(winners) == 1
    winner = winners[0]
    for actor, (won, text) in results.items():
        if not won:
            assert f"claimed by {winner}" in text, (actor, text)

    held = await ok(client, "get_node", node_id=task["id"])
    assert held["claimed_by"] == winner and held["claim_expires_at"] is not None
    history = (await ok(client, "node_history", node_id=task["id"]))["items"]
    claimed = [e for e in history if e["action"] == "claimed"]
    assert [(e["actor"], e["before"]) for e in claimed] == [(winner, None)]


async def test_an_expired_lease_can_be_claimed_by_another_actor(client: Client) -> None:
    (task,) = await make_tasks(client, 1)
    first = await ok(client, "claim_node", actor="ann", node_id=task["id"], lease_seconds=2)

    message = await refused(client, "claim_node", actor="bob", node_id=task["id"])
    assert "claimed by ann until" in message
    await anyio.sleep(2.6)

    lapsed = await ok(client, "get_node", node_id=task["id"])
    assert (lapsed["claimed_by"], lapsed["claim_expires_at"]) == (None, None)
    assert task["id"] in await all_ready_ids(client)
    assert "lapsed" in await refused(client, "renew_claim", actor="ann", node_id=task["id"])

    second = await ok(client, "claim_node", actor="bob", node_id=task["id"], lease_seconds=60)
    assert second["actor"] == "bob" and second["expires_at"] > first["expires_at"]
    held = await ok(client, "get_node", node_id=task["id"])
    assert held["claimed_by"] == "bob"

    history = (await ok(client, "node_history", node_id=task["id"]))["items"]
    claimed = [e for e in history if e["action"] == "claimed"]
    assert [e["actor"] for e in claimed] == ["ann", "bob"]
    assert claimed[1]["before"]["actor"] == "ann"
    assert claimed[1]["after"]["actor"] == "bob"


async def test_the_holder_can_renew_and_release_and_nobody_else_can(client: Client) -> None:
    (task,) = await make_tasks(client, 1)
    claim = await ok(client, "claim_node", actor="ann", node_id=task["id"], lease_seconds=60)
    assert "already hold the claim" in await refused(
        client, "claim_node", actor="ann", node_id=task["id"]
    )

    assert "claimed by ann, not by you" in await refused(
        client, "renew_claim", actor="bob", node_id=task["id"]
    )
    assert "claimed by ann, not by you" in await refused(
        client, "release_claim", actor="bob", node_id=task["id"]
    )

    renewed = await ok(client, "renew_claim", actor="ann", node_id=task["id"], lease_seconds=3600)
    assert renewed["expires_at"] > claim["expires_at"]
    assert (await ok(client, "get_node", node_id=task["id"]))["claim_expires_at"] == renewed[
        "expires_at"
    ]

    await ok(client, "release_claim", actor="ann", node_id=task["id"])
    assert (await ok(client, "get_node", node_id=task["id"]))["claimed_by"] is None
    assert "nothing to release" in await refused(
        client, "release_claim", actor="ann", node_id=task["id"]
    )
    assert "do not hold a claim" in await refused(
        client, "renew_claim", actor="ann", node_id=task["id"]
    )
    await ok(client, "claim_node", actor="bob", node_id=task["id"])

    history = (await ok(client, "node_history", node_id=task["id"]))["items"]
    assert [(e["action"], e["actor"]) for e in history if e["action"] != "create"] == [
        ("claimed", "ann"),
        ("claim_renewed", "ann"),
        ("claim_released", "ann"),
        ("claimed", "bob"),
    ]


async def test_work_that_cannot_be_done_now_cannot_be_claimed(client: Client) -> None:
    epic, story, _ = await make_tree(client)
    prerequisite, waiting, blocked, finished = await make_tasks(client, 4)
    await needs_first(client, waiting, prerequisite)
    await set_status(client, blocked, "blocked")
    await set_status(client, finished, "done")

    assert "Only stories and tasks can be claimed" in await refused(
        client, "claim_node", actor="ann", node_id=epic["id"]
    )
    message = await refused(client, "claim_node", actor="ann", node_id=waiting["id"])
    assert f"needs node {prerequisite['id']} first" in message
    assert "is marked blocked" in await refused(
        client, "claim_node", actor="ann", node_id=blocked["id"]
    )
    assert "is done, so there is nothing left to claim" in await refused(
        client, "claim_node", actor="ann", node_id=finished["id"]
    )
    for seconds in (0, 86401):
        assert "lease_seconds must be a whole number from 1 to 86400" in await refused(
            client, "claim_node", actor="ann", node_id=story["id"], lease_seconds=seconds
        )
    assert "actor must be the name" in await refused(
        client, "claim_node", actor="", node_id=story["id"]
    )
    assert "There is no node with id 987654321" in await refused(
        client, "claim_node", actor="ann", node_id=987654321
    )

    await ok(client, "claim_node", actor="ann", node_id=story["id"])
    # Once its prerequisite is done, a waiting task can be claimed.
    await set_status(client, prerequisite, "done")
    await ok(client, "claim_node", actor="ann", node_id=waiting["id"])
