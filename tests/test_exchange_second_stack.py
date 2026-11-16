"""M7 against the running containers, part two: loading a file into an empty server, and backing up
and restoring a database. Both destroy a database, so they run on a second, separate copy of Vrum
(tests/second_stack.py: its own compose project, database volume, port, account, project and agent
key) that each test
starts empty and removes afterwards (see ./check.sh).

The graph they move is built through the tools (tests.helpers.rich_world), so it is the kind of
data the server really makes, and what comes out the other end is compared with what went in, whole,
through the export."""

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from tests.helpers import REPO_ROOT, export_everything, ok, refused, rich_world
from tests.second_stack import SecondStack

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SECTIONS = ("nodes", "links", "rules", "decisions", "facts", "events")


@pytest.fixture
def stack() -> Iterator[SecondStack]:
    second = SecondStack()
    try:
        yield second
    finally:
        second.close()


def nothing_in(document: dict[str, Any]) -> bool:
    return all(not document[name] for name in SECTIONS)


async def distinct_transactions(stack: SecondStack) -> int:
    result = stack.compose(
        "exec", "-T", "db", "psql", "-U", "vrum", "-d", "vrum", "-tA", "-c",
        "SELECT count(DISTINCT xid) FROM events",
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    return int(result.stdout.strip())


async def every_mention(client: Client) -> list[dict[str, Any]]:
    """Every reference written anywhere in the project, following the pages."""
    found: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await ok(client, "find_mentions", limit=200, offset=offset)
        found.extend(page["items"])
        if page["next_offset"] is None:
            return found
        offset = page["next_offset"]


async def a_world_with_references(client: Client) -> dict[str, dict[str, Any]]:
    """The rich world, and an epic whose text names a fact, a decision, a rule and a story."""
    world = await rich_world(client)
    await ok(
        client,
        "create_node",
        actor="ann",
        kind="epic",
        title="Mentions",
        body=(
            f"See [[fact:{world['fact1']['id']}]], [[decision:{world['wide']['id']}]], "
            f"[[rule:{world['rule1']['id']}]] and [[node:{world['story1']['id']}]]."
        ),
    )
    return world


# -- loading a file ------------------------------------------------------------------------------


async def test_a_graph_exported_and_loaded_into_an_empty_server_comes_back_exactly(
    stack: SecondStack, tmp_path: Path
) -> None:
    stack.up()
    async with stack.client() as client:
        world = await a_world_with_references(client)
        original = await export_everything(client)
        mentions = await every_mention(client)
        recalled = await ok(client, "recall", node_id=world["task3"]["id"])
        briefed = await ok(client, "recall", node_id=world["story1"]["id"])
    assert mentions and recalled["project_wide_decisions"]["count"] == 1
    exported = tmp_path / "vrum-export.json"
    result = stack.python("export_graph.py", stack.url, exported)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(exported.read_text(encoding="utf-8")) == original
    # Nothing about accounts or access is part of a project's export.
    assert stack.project is not None
    for secret in (stack.project.owner.email, stack.project.owner.password, stack.key):
        assert secret not in exported.read_text(encoding="utf-8")

    # A new, empty server: the volume is deleted and everything starts again.
    stack.destroy()
    stack.up()
    async with stack.client() as client:
        assert nothing_in(await export_everything(client))
        await refuses_bad_files_and_loads_nothing(client, original)
    loaded = stack.python("import_graph.py", stack.url, exported)

    assert loaded.returncode == 0, loaded.stdout + loaded.stderr
    assert f"Imported {sum(original['counts'].values())} items" in loaded.stdout
    async with stack.client() as client:
        assert await export_everything(client) == original
        top = {name: max(item["id"] for item in original[name]) for name in SECTIONS}
        # What is loaded works like anything else: it can be read, searched and followed ...
        epic = await ok(client, "get_node", node_id=world["epic"]["id"])
        assert epic["title"] == world["epic"]["title"] and epic["status"] == "ready"
        word = str(world["epic"]["title"]).split()[-1]
        found = await ok(client, "search", query=word, mode="words", limit=200)
        assert {(i["item_type"], i["id"]) for i in found["items"]} >= {
            ("epic", world["epic"]["id"]),
            ("fact", world["fact1"]["id"]),
            ("decision", world["wide"]["id"]),
        }
        # ... what a person confirmed is still confirmed, by whom and when, and what ended keeps
        # what it was confirmed as ...
        confirmed = await ok(client, "get_fact", fact_id=world["fact1"]["id"])
        assert (confirmed["confirmation"], confirmed["confirmed_by"]) == ("confirmed", "Marie")
        ended = await ok(client, "get_fact", fact_id=world["fact6"]["id"])
        assert (ended["status"], ended["confirmation"]) == ("withdrawn", "confirmed")
        replaced = await ok(client, "get_decision", decision_id=world["decision1"]["id"])
        assert (replaced["status"], replaced["superseded_by_id"]) == (
            "superseded",
            world["decision2"]["id"],
        )
        assert replaced["confirmation"] == "confirmed" and replaced["supersedes_ids"] == []
        # ... a project-wide decision still applies throughout the project, and what recall and
        # find_mentions answer is what they answered before ...
        assert await ok(client, "recall", node_id=world["task3"]["id"]) == recalled
        assert await ok(client, "recall", node_id=world["story1"]["id"]) == briefed
        assert await every_mention(client) == mentions
        # ... new items carry on from the highest id in the file, their changes after the loaded
        # history ...
        new = await ok(client, "create_node", actor="ann", kind="epic", title="After the import")
        assert new["id"] == top["nodes"] + 1
        changes = await ok(client, "changes_since", after_event_id=top["events"], limit=50)
        assert [event["node_id"] for event in changes["items"]] == [new["id"]]
        assert changes["items"][0]["id"] == top["events"] + 1
        # ... confirmation can be changed after the import, by a person, and only while active ...
        again = await ok(
            client,
            "set_confirmation",
            actor="ann",
            kind="fact",
            record_id=world["fact2"]["id"],
            confirmation="confirmed",
            person="Ada",
        )
        assert again["fact"]["confirmed_by"] == "Ada"
        assert "already superseded" in await refused(
            client,
            "set_confirmation",
            actor="ann",
            kind="decision",
            record_id=world["decision1"]["id"],
            confirmation="unconfirmed",
            person="Marie",
            quote="No.",
        )
        # ... and facts and decisions are numbered on from the file too.
        next_fact = await ok(
            client, "retain_fact", actor="ann", text="After the import", source="Here"
        )
        next_decision = await ok(
            client,
            "record_decision",
            actor="ann",
            title="After the import",
            text="Carry on.",
            reason="Because",
            decided_by="Marie",
        )
        assert (next_fact["id"], next_decision["id"]) == (top["facts"] + 1, top["decisions"] + 1)
        assert next_fact["confirmation"] == next_decision["confirmation"] == "unconfirmed"
        history = await ok(client, "changes_since", after_event_id=0, limit=200)
        assert [event["id"] for event in history["items"][:3]] == [
            event["id"] for event in original["events"][:3]
        ]
        # A server that holds anything is not loaded into again.
        again_message = await refused(client, "import_graph", actor="ann", document=original)
        assert "already holds data" in again_message
        assert "nothing was imported" in again_message.lower()


async def refuses_bad_files_and_loads_nothing(client: Client, good: dict[str, Any]) -> None:
    """Each of these is refused in plain words, and the server is still empty afterwards."""

    async def refusal(change: Any) -> str:
        bad = copy.deepcopy(good)
        change(bad)
        message = await refused(client, "import_graph", actor="ann", document=bad)
        assert nothing_in(await export_everything(client)), message
        return message

    epic = next(n for n in good["nodes"] if n["kind"] == "epic")
    task = next(n for n in good["nodes"] if n["kind"] == "task")
    other = next(n for n in good["nodes"] if n["kind"] == "task" and n["id"] != task["id"])
    confirmed_fact = next(f for f in good["facts"] if f["confirmed_by"] and f["status"] == "active")
    replaced_fact = next(f for f in good["facts"] if f["status"] == "superseded")
    withdrawn_fact = next(f for f in good["facts"] if f["status"] == "withdrawn")
    linked_fact = next(f for f in good["facts"] if f["decision_ids"])

    def lose_the_epic(bad: dict[str, Any]) -> None:
        bad["nodes"] = [n for n in bad["nodes"] if n["id"] != epic["id"]]
        bad["counts"]["nodes"] -= 1

    message = await refusal(lose_the_epic)
    assert "nothing was imported" in message and "is not in the file" in message
    assert f"parent_id={epic['id']}" in message

    def nameless(bad: dict[str, Any]) -> None:
        del bad["nodes"][0]["title"]
        bad["nodes"][1]["id"] = "seven"
        bad["nodes"][2]["status"] = "finished"
        bad["links"][0]["extra"] = 1

    message = await refusal(nameless)
    assert "nodes[0].title: Field required" in message
    assert "nodes[1].id: Input should be a valid integer" in message
    assert "nodes[2].status: Input should be" in message
    assert "links[0].extra: Extra inputs are not permitted" in message

    message = await refusal(lambda bad: bad.__setitem__("format", "something-else"))
    assert "format:" in message and "vrum-export" in message
    message = await refusal(lambda bad: bad["links"].pop())
    assert "counts.links says" in message and "cut short" in message
    message = await refusal(lambda bad: bad["nodes"].append(dict(bad["nodes"][0])))
    assert "is used twice" in message

    def with_a_secret(bad: dict[str, Any]) -> None:
        bad["nodes"][3]["body"] = "deploy with ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fG3hJ5"

    message = await refusal(with_a_secret)
    assert "document.nodes[3].body field contains what looks like a GitHub token" in message
    assert "ghp_" not in message.replace("looks like a GitHub token", "")

    # Facts and decisions have to hang together: what a fact concerns is in the file, a confirmation
    # names a person and a time, and a record that ended says how and why.
    def lose_a_decision(bad: dict[str, Any]) -> None:
        bad["decisions"] = [
            d for d in bad["decisions"] if d["id"] not in linked_fact["decision_ids"]
        ]
        bad["counts"]["decisions"] = len(bad["decisions"])

    message = await refusal(lose_a_decision)
    assert "decision_ids=" in message and "is not in the file" in message

    def confirmed_by_nobody_at_no_time(bad: dict[str, Any]) -> None:
        one = next(f for f in bad["facts"] if f["id"] == confirmed_fact["id"])
        one["confirmed_at"] = None

    message = await refusal(confirmed_by_nobody_at_no_time)
    assert "confirmed_by and confirmed_at go together" in message

    def replaced_by_nothing(bad: dict[str, Any]) -> None:
        one = next(f for f in bad["facts"] if f["id"] == replaced_fact["id"])
        one["superseded_by_id"] = None

    message = await refusal(replaced_by_nothing)
    assert "a superseded fact needs superseded_by_id" in message

    def withdrawn_for_no_reason(bad: dict[str, Any]) -> None:
        one = next(f for f in bad["facts"] if f["id"] == withdrawn_fact["id"])
        one["status_reason"] = None

    message = await refusal(withdrawn_for_no_reason)
    assert "a withdrawn fact needs status_reason" in message

    def wide_by_word_of_mouth(bad: dict[str, Any]) -> None:
        bad["decisions"][0]["project_wide"] = "yes"

    message = await refusal(wide_by_word_of_mouth)
    assert "decisions[0].project_wide: Input should be a valid boolean" in message

    def no_source(bad: dict[str, Any]) -> None:
        bad["facts"][0]["source"] = "  "

    message = await refusal(no_source)
    assert "The database refused this file, so nothing was imported" in message
    assert "fact source is not blank" in message

    # The database checks what the file says, whatever the file claims: a circle of waiting, and a
    # start date after the target date.
    def circle(bad: dict[str, Any]) -> None:
        top = max(link["id"] for link in bad["links"]) + 1
        for number, (source, target) in enumerate(
            ((task["id"], other["id"]), (other["id"], task["id"]))
        ):
            bad["links"].append(
                {
                    "id": top + number,
                    "source_id": source,
                    "target_id": target,
                    "link_type": "needs_first",
                    "created_at": "2026-11-15T10:00:00.000000Z",
                }
            )
        bad["counts"]["links"] += 2

    message = await refusal(circle)
    assert "The database refused this file, so nothing was imported" in message
    assert "circle of waiting" in message

    def backwards(bad: dict[str, Any]) -> None:
        bad["nodes"][0]["start_on"], bad["nodes"][0]["target_on"] = "2027-01-02", "2027-01-01"

    message = await refusal(backwards)
    assert "node start is not after target" in message


# -- backing up and restoring --------------------------------------------------------------------


async def test_a_backup_restores_into_a_fresh_container_and_the_export_is_identical(
    stack: SecondStack, tmp_path: Path
) -> None:
    stack.up()
    async with stack.client() as client:
        world = await a_world_with_references(client)
        original = await export_everything(client)
    assert await distinct_transactions(stack) > 1
    backup = tmp_path / "vrum.dump"

    made = stack.run(REPO_ROOT / "scripts" / "backup.sh", backup)

    assert made.returncode == 0, made.stdout + made.stderr
    assert "Backed up to" in made.stdout and backup.stat().st_size > 10_000
    assert backup.stat().st_mode & 0o077 == 0
    assert not (tmp_path / "vrum.dump.partial").exists()
    # A database that holds data is not replaced unless you say so, and nothing is changed.
    refusal = stack.run(REPO_ROOT / "scripts" / "restore.sh", backup)
    assert refusal.returncode == 1
    assert "already holds data" in refusal.stderr and "--replace" in refusal.stderr
    async with stack.client() as client:
        assert await export_everything(client) == original

    # The volume is destroyed. A fresh container is restored from the file alone.
    stack.destroy()
    restored = stack.run(REPO_ROOT / "scripts" / "restore.sh", backup)

    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert "Restored from" in restored.stdout
    # The backup holds the whole install: the same person signs in again with the same password
    # and finds the project, and the project's agent key still works.
    assert stack.project is not None
    again = stack.project.owner.sign_in_again()
    listed = again.call("GET", "/board/api/projects")
    assert listed.status == 200
    assert [one["id"] for one in listed.body["projects"]] == [stack.project.id]
    async with stack.client() as client:
        assert await export_everything(client) == original
        # The event log reads in order, and what is written after the restore comes after it.
        assert await distinct_transactions(stack) == 1
        last = original["events"][-1]["id"]
        new = await ok(client, "create_node", actor="ann", kind="epic", title="After the restore")
        assert new["id"] == max(node["id"] for node in original["nodes"]) + 1
        changes = await ok(client, "changes_since", after_event_id=last, limit=50)
        assert [event["node_id"] for event in changes["items"]] == [new["id"]]
        # What a person confirmed is still confirmed, and the numbers of facts and decisions carry
        # on after the restore as they do after an import.
        confirmed = await ok(client, "get_fact", fact_id=world["fact1"]["id"])
        assert (confirmed["confirmation"], confirmed["confirmed_by"]) == ("confirmed", "Marie")
        recalled = await ok(client, "recall", node_id=world["task3"]["id"])
        assert [i["decision"]["id"] for i in recalled["project_wide_decisions"]["items"]] == [
            world["wide"]["id"]
        ]
        fact = await ok(client, "retain_fact", actor="ann", text="After the restore", source="Here")
        assert fact["id"] == max(item["id"] for item in original["facts"]) + 1
        decision = await ok(
            client,
            "record_decision",
            actor="ann",
            title="After the restore",
            text="Carry on.",
            reason="Because",
            decided_by="Marie",
        )
        assert decision["id"] == max(item["id"] for item in original["decisions"]) + 1
        everything = await ok(client, "changes_since", after_event_id=0, limit=200)
        assert [event["id"] for event in everything["items"][:3]] == [
            event["id"] for event in original["events"][:3]
        ]

    # --replace throws away what is there, which is now one node more than the backup.
    replaced = stack.run(REPO_ROOT / "scripts" / "restore.sh", backup, "--replace")
    assert replaced.returncode == 0, replaced.stdout + replaced.stderr
    async with stack.client() as client:
        assert await export_everything(client) == original

    # Something that is not a backup costs nothing.
    junk = tmp_path / "junk.dump"
    junk.write_bytes(b"this is not a database backup")
    for source in (junk, tmp_path / "missing.dump"):
        failed = stack.run(REPO_ROOT / "scripts" / "restore.sh", source, "--replace")
        assert failed.returncode == 1, source
        assert "not a backup this database can read" in failed.stderr or "no backup file" in (
            failed.stderr
        )
    async with stack.client() as client:
        assert await export_everything(client) == original
    assert stack.run(REPO_ROOT / "scripts" / "restore.sh").returncode == 2


async def test_a_backup_made_without_a_file_name_goes_to_the_backups_folder(
    stack: SecondStack,
) -> None:
    stack.up()
    folder = REPO_ROOT / "backups"
    existed = folder.exists()
    before = set(folder.glob("*.dump")) if existed else set()
    try:
        made = stack.run(REPO_ROOT / "scripts" / "backup.sh")

        assert made.returncode == 0, made.stdout + made.stderr
        (created,) = set(folder.glob("*.dump")) - before
        assert created.name.startswith("vrum-") and created.stat().st_mode & 0o077 == 0
    finally:
        for path in set(folder.glob("*.dump")) - before:
            path.unlink()
        if not existed and folder.exists():
            folder.rmdir()
