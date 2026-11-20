"""What `server_status` and `who_is_working` say, through a real MCP client against the running
containers (see ./check.sh): the sizes equal what the list tools count, the index state equals
what search says about it, the embedding service is seen answering and then not, how the last
reindex went is kept, and nothing secret is ever in the answer."""

import os

import anyio
import pytest
from mcp import Client

from tests.helpers import (
    connect_as,
    embeddings_stopped,
    make_decision,
    make_fact,
    make_tree,
    ok,
    refused,
    reindexed,
    the_token,
    unique_word,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_the_sizes_are_what_the_list_tools_count(client: Client) -> None:
    _, story, _ = await make_tree(client)
    await ok(client, "create_rule", actor="marie", title="A rule", node=story["ref"])
    await make_decision(client, "A decision")
    await make_decision(client, "A project-wide decision", project_wide=True)
    await make_fact(client, "A fact")
    await reindexed(client)

    status = await ok(client, "server_status")

    counts = status["counts"]
    assert set(counts) == {
        "epics",
        "stories",
        "tasks",
        "links",
        "rules",
        "notes",
        "events",
    }
    for name, kind in (("epics", "epic"), ("stories", "story"), ("tasks", "task")):
        assert counts[name] == (await ok(client, "list_nodes", kind=kind, limit=1))["total"], name
    assert counts["rules"] == (await ok(client, "list_rules", limit=1))["total"]
    assert counts["notes"] == (await ok(client, "list_notes", limit=1))["total"]
    # Decisions and facts are counted together: they are both notes.
    assert counts["notes"] >= 3
    exported = (await ok(client, "export_graph", limit=1))["counts"]
    assert counts["links"] == exported["links"] and counts["events"] == exported["events"]
    assert counts["epics"] + counts["stories"] + counts["tasks"] == exported["nodes"]
    assert counts["notes"] == exported["notes"]


async def test_the_index_state_equals_what_search_says_and_is_never_a_secret(
    client: Client,
) -> None:
    await reindexed(client)
    status = await ok(client, "server_status")
    index = status["index"]
    said = (await ok(client, "search", query="anything", limit=1))["index"]

    assert (index["meaning"], index["waiting_to_index"]) == (
        said["meaning"],
        said["waiting_to_index"],
    )
    assert index["meaning"] == "used" and index["waiting_to_index"] == 0
    assert index["embedding_service"] == "answering"
    assert index["embedding_model"] == "scripted-embedding"
    assert index["vector_size"] == 1024 and index["indexed"] > 0
    # There is no language model to name: Vrum stores and retrieves, it does not generate.
    assert set(status) == {
        "vrum_version",
        "postgres_version",
        "pgvector_version",
        "counts",
        "index",
        "checked_at",
    }
    # What the last reindex did is kept, with its result.
    last = index["last_reindex"]
    assert last["error"] is None and last["waiting_after"] == 0 and last["failed_total"] == 0
    # Nothing secret is in it, in any field.
    everything = str(status)
    for secret in (the_token(), os.environ.get("VRUM_DB_PASSWORD", "no-such-password")):
        assert secret not in everything


async def test_a_stopped_embedding_service_is_seen_and_the_failed_reindex_is_kept(
    client: Client,
) -> None:
    await reindexed(client)
    with embeddings_stopped():
        status = await ok(client, "server_status")
        assert status["index"]["embedding_service"] == "unreachable"
        assert "Nothing was indexed" in await refused(client, "reindex")
        after = await ok(client, "server_status")
        last = after["index"]["last_reindex"]
        assert last["error"] and last["embedded"] == 0
    # Back again: it answers, and the next reindex clears the error.
    again = await ok(client, "server_status")
    assert again["index"]["embedding_service"] == "answering"
    await reindexed(client)
    assert (await ok(client, "server_status"))["index"]["last_reindex"]["error"] is None


async def test_who_is_working_says_who_holds_what_who_lapsed_and_who_finished(
    client: Client,
) -> None:
    epic = await ok(client, "create_node", actor="marie", kind="epic", title="Who")
    story = await ok(
        client, "create_node", actor="marie", kind="story", title="Who story", parent=epic["ref"]
    )
    tasks = [
        await ok(
            client,
            "create_node",
            actor="marie",
            kind="task",
            title=f"Who task {number}",
            parent=story["ref"],
        )
        for number in range(3)
    ]
    # Names that only this test uses: other tests' agents are in the same list.
    # Who an agent is comes from its key, so each of the three has a key named for them.
    word = unique_word()
    working, lapsing, finishing = (f"{name}-{word}" for name in ("ann", "bob", "cy"))
    async with (
        connect_as(working) as ann,
        connect_as(lapsing) as bob,
        connect_as(finishing) as cy,
    ):
        await ok(ann, "claim_node", node=tasks[0]["ref"], lease_seconds=600)
        await ok(bob, "claim_node", node=tasks[1]["ref"], lease_seconds=1)
        await ok(cy, "claim_node", node=tasks[2]["ref"])
        await ok(
            cy,
            "finish_work",
            node=tasks[2]["ref"],
            outcome="done",
            note="All of it is done.\nSecond line.",
        )
    await anyio.sleep(1.6)

    result = await ok(client, "who_is_working")

    ours = {
        agent["actor"]: agent for agent in result["agents"] if agent["actor"].endswith(f"-{word}")
    }
    assert set(ours) == {working, lapsing, finishing}
    assert ours[working]["state"] == "working"
    assert [held["node"] for held in ours[working]["holding"]] == [tasks[0]["ref"]]
    assert ours[working]["holding"][0]["title"] == "Who task 0"
    assert ours[lapsing]["state"] == "lapsed" and ours[lapsing]["holding"] == []
    assert [held["node"] for held in ours[lapsing]["lapsed"]] == [tasks[1]["ref"]]
    assert ours[finishing]["state"] == "finished"
    note = ours[finishing]["last_handoff"]
    assert note["note"] == "All of it is done.\nSecond line." and note["outcome"] == "done"
    assert note["node"] == tasks[2]["ref"]
    # Working first, then lapsed, then finished.
    order = [agent["state"] for agent in result["agents"]]
    assert order == sorted(order, key=["working", "lapsed", "finished"].index)
    # The server's own time, for the countdown.
    claim_ends = ours[working]["holding"][0]["expires_at"]
    assert claim_ends > result["as_of"]
