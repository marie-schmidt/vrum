"""The database refuses bad data on its own, even if the server's own checks were bypassed.

These run SQL directly inside the real database container (see ./check.sh, which names the compose
project and password through the environment).
"""

import os
import subprocess
from pathlib import Path

import pytest
from mcp import Client

from tests.helpers import make_tasks, make_tree, needs_first, ok

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_sql(sql: str) -> subprocess.CompletedProcess[str]:
    if not os.environ.get("COMPOSE_PROJECT_NAME"):
        pytest.fail(
            "COMPOSE_PROJECT_NAME is not set. Run the integration tests through ./check.sh."
        )
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "vrum", "-d", "vrum"]
        + ["-v", "ON_ERROR_STOP=1", "-c", sql],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_database_can_be_reached_directly() -> None:
    result = run_sql("SELECT count(*) FROM nodes")
    assert result.returncode == 0, result.stderr


async def test_the_database_refuses_a_hierarchy_that_is_not_epic_story_task(client: Client) -> None:
    epic, story, _ = await make_tree(client)
    attempts = {
        "a story with no parent": (
            "INSERT INTO nodes (kind, title) VALUES ('story', 'x')",
            "node_has_a_parent_unless_epic",
        ),
        "a task with no parent": (
            "INSERT INTO nodes (kind, title) VALUES ('task', 'x')",
            "node_has_a_parent_unless_epic",
        ),
        "an epic with a parent": (
            f"INSERT INTO nodes (kind, parent_id, title) VALUES ('epic', {epic['id']}, 'x')",
            "node_has_a_parent_unless_epic",
        ),
        "a task under an epic": (
            f"INSERT INTO nodes (kind, parent_id, title) VALUES ('task', {epic['id']}, 'x')",
            "node_parent_has_the_right_kind",
        ),
        "a story under a story": (
            f"INSERT INTO nodes (kind, parent_id, title) VALUES ('story', {story['id']}, 'x')",
            "node_parent_has_the_right_kind",
        ),
        "a story under nothing that exists": (
            "INSERT INTO nodes (kind, parent_id, title) VALUES ('story', 987654321, 'x')",
            "node_parent_has_the_right_kind",
        ),
        "an unknown kind": (
            f"INSERT INTO nodes (kind, parent_id, title) VALUES ('saga', {epic['id']}, 'x')",
            "node_kind_is_known",
        ),
        "a blank title": (
            "INSERT INTO nodes (kind, title) VALUES ('epic', '  ')",
            "node_title_is_not_blank",
        ),
        "the stored status ready": (
            f"UPDATE nodes SET status = 'ready', version = version + 1 WHERE id = {story['id']}",
            "node_status_is_known",
        ),
        "a story made its own parent": (
            f"UPDATE nodes SET parent_id = {story['id']}, version = version + 1 "
            f"WHERE id = {story['id']}",
            "node_parent_has_the_right_kind",
        ),
    }
    for what, (sql, expected) in attempts.items():
        result = run_sql(sql)
        assert result.returncode != 0, f"The database accepted {what}."
        assert expected in result.stderr, f"{what}: {result.stderr}"

    after = await ok(client, "get_node", node_id=story["id"])
    assert (after["status"], after["parent_id"], after["version"]) == ("ready", epic["id"], 1)


async def test_the_database_refuses_a_kind_change_and_a_change_without_a_new_version(
    client: Client,
) -> None:
    _, story, _ = await make_tree(client)
    result = run_sql(f"UPDATE nodes SET kind = 'epic', parent_id = NULL WHERE id = {story['id']}")
    assert "cannot change its kind" in result.stderr
    result = run_sql(f"UPDATE nodes SET title = 'quiet edit' WHERE id = {story['id']}")
    assert "raise its version by exactly one" in result.stderr
    result = run_sql(f"UPDATE nodes SET title = 'x', version = 5 WHERE id = {story['id']}")
    assert "raise its version by exactly one" in result.stderr
    assert (await ok(client, "get_node", node_id=story["id"]))["title"] == "A story"


async def test_the_event_log_cannot_be_rewritten_and_nodes_with_history_cannot_be_deleted(
    client: Client,
) -> None:
    epic, story, _ = await make_tree(client)
    assert (
        "append-only"
        in run_sql(f"UPDATE events SET actor = 'someone' WHERE node_id = {epic['id']}").stderr
    )
    assert "append-only" in run_sql(f"DELETE FROM events WHERE node_id = {epic['id']}").stderr
    assert (
        "violates foreign key constraint"
        in run_sql(f"DELETE FROM nodes WHERE id = {story['id']}").stderr
    )
    assert (
        "violates check constraint"
        in run_sql(
            f"INSERT INTO events (node_id, action, actor, after) "
            f"VALUES ({epic['id']}, 'create', ' ', '{{}}')"
        ).stderr
    )

    history = (await ok(client, "node_history", node_id=story["id"]))["items"]
    assert [event["actor"] for event in history] == ["marie"]


async def test_the_database_refuses_bad_links(client: Client) -> None:
    first, second = await make_tasks(client, 2)
    a, b = first["id"], second["id"]
    await needs_first(client, first, second)
    await ok(client, "add_link", actor="marie", source_id=a, target_id=b, link_type="relates_to")

    def link(source: int, target: int, link_type: str) -> str:
        return (
            "INSERT INTO links (source_id, target_id, link_type) "
            f"VALUES ({source}, {target}, '{link_type}')"
        )

    attempts = {
        "a link to the same node": (link(a, a, "relates_to"), "link_is_not_to_the_same_node"),
        "waiting for the same node": (link(a, a, "needs_first"), "link_is_not_to_the_same_node"),
        "an unknown link type": (link(a, b, "blocks"), "link_type_is_known"),
        "a link to a node that does not exist": (
            link(a, 987654321, "needs_first"),
            "violates foreign key constraint",
        ),
        "a circle of waiting": (link(b, a, "needs_first"), "circle of waiting"),
        "the same link twice": (link(a, b, "needs_first"), "link_is_not_repeated"),
        "a two-way link again in the other direction": (
            link(b, a, "relates_to"),
            "link_two_way_is_not_repeated",
        ),
    }
    for what, (sql, expected) in attempts.items():
        result = run_sql(sql)
        assert result.returncode != 0, f"The database accepted {what}."
        assert expected in result.stderr, f"{what}: {result.stderr}"

    links = (await ok(client, "list_links", node_id=a))["items"]
    assert sorted(link["link_type"] for link in links) == ["needs_first", "relates_to"]


async def test_the_database_refuses_bad_claims_and_a_second_holder(client: Client) -> None:
    epic, story, task = await make_tree(client)
    await ok(client, "claim_node", actor="ann", node_id=task["id"])

    def claim(node: int, kind: str, actor: str, expires: str) -> str:
        return (
            "INSERT INTO claims (node_id, node_kind, actor, expires_at) "
            f"VALUES ({node}, '{kind}', '{actor}', now() + interval '{expires}')"
        )

    attempts = {
        "a claim on an epic": (
            claim(epic["id"], "epic", "bob", "1 hour"),
            "claim_is_for_a_story_or_task",
        ),
        "a claim that names the wrong kind": (
            claim(story["id"], "task", "bob", "1 hour"),
            "claim_is_on_a_real_node",
        ),
        "a claim with no holder": (
            claim(story["id"], "story", " ", "1 hour"),
            "claim_actor_is_not_blank",
        ),
        "a claim that is over before it starts": (
            claim(story["id"], "story", "bob", "-1 hour"),
            "claim_lease_is_in_the_future",
        ),
        "a second holder of one task": (
            claim(task["id"], "task", "bob", "1 hour"),
            'duplicate key value violates unique constraint "claims_pkey"',
        ),
    }
    for what, (sql, expected) in attempts.items():
        result = run_sql(sql)
        assert result.returncode != 0, f"The database accepted {what}."
        assert expected in result.stderr, f"{what}: {result.stderr}"

    assert (await ok(client, "get_node", node_id=task["id"]))["claimed_by"] == "ann"
    assert (await ok(client, "get_node", node_id=story["id"]))["claimed_by"] is None
    result = run_sql(
        "INSERT INTO events (node_id, action, actor, after) "
        f"VALUES ({task['id']}, 'delete', 'x', '{{}}')"
    )
    assert "event_action_is_known" in result.stderr
