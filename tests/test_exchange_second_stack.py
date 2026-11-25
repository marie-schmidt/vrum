"""M7 against the running containers, part two: loading a file into an empty server, and backing up
and restoring a database. Both destroy a database, so they run on a second, separate copy of Vrum
(tests/second_stack.py: its own compose project, database volume, port, account, project and agent
key) that each test starts empty and removes afterwards (see ./check.sh).

The graph they move is built through the tools (tests.helpers.rich_world), so it is the kind of
data the server really makes, and what comes out the other end is compared with what went in, whole,
through the export. A file of decisions and facts, as the servers before notes wrote it, is loaded
there too, and comes out as notes."""

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from tests.helpers import REPO_ROOT, export_everything, ok, on_board, refused, rich_world
from tests.second_stack import SecondStack
from tests.world import Project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SECTIONS = ("nodes", "links", "rules", "notes", "definition_of_done", "events")
# The lists whose items have an id of their own.
WITH_IDS = ("links", "rules", "notes", "events")


@pytest.fixture
def stack() -> Iterator[SecondStack]:
    second = SecondStack()
    try:
        yield second
    finally:
        second.close()


def nothing_in(document: dict[str, Any]) -> bool:
    return all(not document[name] for name in SECTIONS)


def next_number(document: dict[str, Any], kind: str) -> int:
    """The number the next epic, story or task of this kind gets after the nodes of a file: each
    kind is numbered on from its own highest."""
    return max((n["number"] for n in document["nodes"] if n["kind"] == kind), default=0) + 1


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


async def a_world_with_references(client: Client, project: Project) -> dict[str, dict[str, Any]]:
    """The rich world (its notes confirmed by a person on the board of this stack's `project`), and
    an epic whose text names two notes, a rule and a story."""
    world = await rich_world(client, project)
    await ok(
        client,
        "create_node",
        kind="epic",
        title="Mentions",
        body=(
            f"See [[note:{world['fact1']['id']}]], [[note:{world['wide']['id']}]], "
            f"[[rule:{world['rule1']['id']}]] and [[{world['story1']['ref']}]]."
        ),
    )
    return world


# -- loading a file ------------------------------------------------------------------------------


async def test_a_graph_exported_and_loaded_into_an_empty_server_comes_back_exactly(
    stack: SecondStack, tmp_path: Path
) -> None:
    stack.up()
    assert stack.project is not None
    async with stack.client() as client:
        world = await a_world_with_references(client, stack.project)
        original = await export_everything(client)
        mentions = await every_mention(client)
        recalled = await ok(client, "recall", node=world["task3"]["ref"])
        briefed = await ok(client, "recall", node=world["story1"]["ref"])
    assert mentions and recalled["project_wide_notes"]["count"] == 1
    assert original["version"] == 6
    assert len(original["definition_of_done"]) == 1
    exported = tmp_path / "vrum-export.json"
    result = stack.python("export_graph.py", stack.url, exported)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(exported.read_text(encoding="utf-8")) == original
    # Nothing about accounts or access is part of a project's export.
    for secret in (stack.project.owner.email, stack.project.owner.password, stack.key):
        assert secret not in exported.read_text(encoding="utf-8")

    # A new, empty server: the volume is deleted and everything starts again.
    stack.destroy()
    stack.up()
    assert stack.project is not None
    async with stack.client() as client:
        assert nothing_in(await export_everything(client))
        await refuses_bad_files_and_loads_nothing(client, original)
    loaded = stack.python("import_graph.py", stack.url, exported)

    assert loaded.returncode == 0, loaded.stdout + loaded.stderr
    assert f"Imported {sum(original['counts'].values())} items" in loaded.stdout
    async with stack.client() as client:
        assert await export_everything(client) == original
        top = {name: max(item["id"] for item in original[name]) for name in WITH_IDS}
        # What is loaded works like anything else: it can be read, searched and followed ...
        epic = await ok(client, "get_node", node=world["epic"]["ref"])
        assert epic["title"] == world["epic"]["title"] and epic["status"] == "ready"
        word = str(world["epic"]["title"]).split()[-1]
        found = await ok(client, "search", query=word, mode="words", limit=200)
        assert {i["ref"] for i in found["items"]} >= {
            world["epic"]["ref"],
            f"note:{world['fact1']['id']}",
            f"note:{world['wide']['id']}",
        }
        # ... what a person confirmed is still confirmed, by whom and when, and what ended keeps
        # who confirmed it ...
        confirmed = await ok(client, "get_note", note_id=world["fact1"]["id"])
        assert (confirmed["status"], confirmed["confirmed_by"]) == ("confirmed", "Marie")
        ended = await ok(client, "get_note", note_id=world["fact6"]["id"])
        assert (ended["status"], ended["confirmed_by"]) == ("withdrawn", "Marie")
        replaced = await ok(client, "get_note", note_id=world["decision1"]["id"])
        assert (replaced["status"], replaced["superseded_by_id"]) == (
            "superseded",
            world["decision2"]["id"],
        )
        assert replaced["confirmed_by"] == "Marie" and replaced["supersedes_ids"] == []
        decided = await ok(client, "get_note", note_id=world["decision2"]["id"])
        assert (decided["decided"], decided["decided_by"]) == (True, "Marie")
        assert decided["supersedes_ids"] == [world["decision1"]["id"]]
        # ... a project-wide note still applies throughout the project, and what recall and
        # find_mentions answer is what they answered before ...
        assert await ok(client, "recall", node=world["task3"]["ref"]) == recalled
        assert await ok(client, "recall", node=world["story1"]["ref"]) == briefed
        assert await every_mention(client) == mentions
        # ... the definition of done is back with its version and who set it, and a person can go
        # on changing it from that version, and an acceptance criterion on a task is still there ...
        (loaded_definition,) = original["definition_of_done"]
        definition = on_board("get_definition_of_done", project=stack.project)
        assert (definition["body"], definition["version"]) == (
            loaded_definition["body"],
            loaded_definition["version"],
        )
        assert definition["updated_by"] == loaded_definition["updated_by"]
        changed = on_board(
            "set_definition_of_done",
            project=stack.project,
            body=definition["body"] + "\n- Nothing left half done",
            expected_version=definition["version"],
        )
        assert changed["version"] == definition["version"] + 1
        assert (await ok(client, "get_rule", rule_id=world["rule3"]["id"]))["node"] == (
            world["task1"]["ref"]
        )
        # ... new items carry on from the highest number of their own kind in the file, their
        # changes after the loaded history ...
        new = await ok(client, "create_node", kind="epic", title="After the import")
        assert new["ref"] == f"epic:{next_number(original, 'epic')}"
        changes = await ok(client, "changes_since", after_event_id=top["events"], limit=50)
        # The change of the definition of done above came first, and about no item.
        assert [event["node"] for event in changes["items"]] == [None, new["ref"]]
        assert changes["items"][0]["action"] == "definition_of_done_changed"
        assert changes["items"][0]["id"] == top["events"] + 1
        assert changes["items"][1]["id"] == top["events"] + 2
        story = await ok(client, "create_node", kind="story", title="Story", parent=new["ref"])
        task = await ok(client, "create_node", kind="task", title="Task", parent=story["ref"])
        assert (story["ref"], task["ref"]) == (
            f"story:{next_number(original, 'story')}",
            f"task:{next_number(original, 'task')}",
        )
        # ... confirmation can be changed after the import, by a person, and only while in force ...
        again = on_board(
            "set_confirmation",
            project=stack.project,
            note_id=world["fact2"]["id"],
            confirmation="confirmed",
            person="Ada",
        )
        assert again["confirmed_by"] == "Ada" and again["status"] == "confirmed"
        refusal = stack.project.owner.call(
            "POST",
            f"/board/api/projects/{stack.project.id}/tool/set_confirmation",
            {
                "note_id": world["decision1"]["id"],
                "confirmation": "unconfirmed",
                "person": "Marie",
                "quote": "No.",
            },
        )
        assert refusal.status == 400
        assert "so its confirmation cannot change" in refusal.body["message"]
        # ... and notes are numbered on from the file too: decisions and facts share one counter.
        next_plain = await ok(client, "add_note", text="After the import", why="Here")
        next_decided = await ok(
            client,
            "add_note",
            text="Carry on.",
            why="Because",
            decided_by="Marie",
        )
        assert (next_plain["id"], next_decided["id"]) == (top["notes"] + 1, top["notes"] + 2)
        assert next_plain["status"] == next_decided["status"] == "unconfirmed"
        history = await ok(client, "changes_since", after_event_id=0, limit=200)
        assert [event["id"] for event in history["items"][:3]] == [
            event["id"] for event in original["events"][:3]
        ]
        # A server that holds anything is not loaded into again.
        again_message = await refused(client, "import_graph", document=original)
        assert "already holds data" in again_message
        assert "nothing was imported" in again_message.lower()


async def refuses_bad_files_and_loads_nothing(client: Client, good: dict[str, Any]) -> None:
    """Each of these is refused in plain words, and the server is still empty afterwards."""

    async def refusal(change: Any) -> str:
        bad = copy.deepcopy(good)
        change(bad)
        message = await refused(client, "import_graph", document=bad)
        assert nothing_in(await export_everything(client)), message
        return message

    epic = next(n for n in good["nodes"] if n["kind"] == "epic")
    task = next(n for n in good["nodes"] if n["kind"] == "task")
    other = next(n for n in good["nodes"] if n["kind"] == "task" and n["number"] != task["number"])
    confirmed_note = next(
        n for n in good["notes"] if n["confirmed_by"] and n["status"] == "confirmed"
    )
    replaced_note = next(n for n in good["notes"] if n["status"] == "superseded")
    withdrawn_note = next(n for n in good["notes"] if n["status"] == "withdrawn")
    linked_note = next(n for n in good["notes"] if n["note_ids"])
    decided_note = next(n for n in good["notes"] if n["decided_by"])

    def lose_the_epic(bad: dict[str, Any]) -> None:
        bad["nodes"] = [
            n for n in bad["nodes"] if (n["kind"], n["number"]) != ("epic", epic["number"])
        ]
        bad["counts"]["nodes"] -= 1

    message = await refusal(lose_the_epic)
    assert "nothing was imported" in message and "is not in the file" in message
    assert f"parent=epic:{epic['number']}" in message

    def nameless(bad: dict[str, Any]) -> None:
        del bad["nodes"][0]["title"]
        bad["nodes"][1]["number"] = "seven"
        bad["nodes"][2]["status"] = "finished"
        bad["links"][0]["extra"] = 1
        bad["nodes"][4]["id"] = 7

    message = await refusal(nameless)
    assert "nodes[0].title: Field required" in message
    assert "nodes[1].number: Input should be a valid integer" in message
    assert "nodes[2].status: Input should be" in message
    assert "links[0].extra: Extra inputs are not permitted" in message
    # A node has no id: it is its kind and its number.
    assert "nodes[4].id: Extra inputs are not permitted" in message

    def parent_of_the_wrong_kind(bad: dict[str, Any]) -> None:
        stories = [n for n in bad["nodes"] if n["kind"] == "story"]
        stories[0]["parent"] = f"story:{stories[1]['number']}"

    message = await refusal(parent_of_the_wrong_kind)
    assert "must be an epic" in message and "nothing was imported" in message

    def not_refs(bad: dict[str, Any]) -> None:
        bad["nodes"][1]["parent"] = "Epic:1"
        bad["notes"][0]["nodes"] = ["12"]
        bad["events"][0]["node"] = "node:3"

    message = await refusal(not_refs)
    meant = "must name an epic, story or task by its kind and number, such as story:12"
    for place in ("nodes[1].parent", "notes[0].nodes[0]", "events[0].node"):
        assert f"{place}: {meant}" in message, (place, message)

    message = await refusal(lambda bad: bad.__setitem__("format", "something-else"))
    assert "format:" in message and "vrum-export" in message
    message = await refusal(lambda bad: bad.__setitem__("version", 7))
    assert "version:" in message
    message = await refusal(lambda bad: bad["links"].pop())
    assert "counts.links says" in message and "cut short" in message
    message = await refusal(lambda bad: bad["nodes"].append(dict(bad["nodes"][0])))
    assert "is used twice" in message

    def with_a_secret(bad: dict[str, Any]) -> None:
        bad["nodes"][3]["body"] = "deploy with ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fG3hJ5"

    message = await refusal(with_a_secret)
    assert "document.nodes[3].body field contains what looks like a GitHub token" in message
    assert "ghp_" not in message.replace("looks like a GitHub token", "")

    # The ticks of the checks have to hang together too: they are on a story or task in the file,
    # and an agent that ticked one said how it checked.
    def tick_on_nothing(bad: dict[str, Any]) -> None:
        bad["checks"][0]["node"] = "task:9999"

    message = await refusal(tick_on_nothing)
    assert "node=task:9999 is not in the file" in message

    def tick_on_the_epic(bad: dict[str, Any]) -> None:
        bad["checks"][0]["node"] = f"epic:{epic['number']}"

    message = await refusal(tick_on_the_epic)
    assert "must be a story or a task" in message

    def an_agent_that_said_nothing(bad: dict[str, Any]) -> None:
        next(c for c in bad["checks"] if c["checked_by_kind"] == "agent")["evidence"] = None

    message = await refusal(an_agent_that_said_nothing)
    assert "a check ticked by an agent needs evidence" in message

    # Notes have to hang together: what a note concerns is in the file, a confirmation names a
    # person and a time, a decision names who decided it and when, and a note that ended says how
    # and why.
    def lose_a_note(bad: dict[str, Any]) -> None:
        bad["notes"] = [n for n in bad["notes"] if n["id"] not in linked_note["note_ids"]]
        bad["counts"]["notes"] = len(bad["notes"])

    message = await refusal(lose_a_note)
    assert "note_ids=" in message and "is not in the file" in message

    def confirmed_by_nobody_at_no_time(bad: dict[str, Any]) -> None:
        one = next(n for n in bad["notes"] if n["id"] == confirmed_note["id"])
        one["confirmed_at"] = None

    message = await refusal(confirmed_by_nobody_at_no_time)
    assert "confirmed_by and confirmed_at go together" in message

    def decided_on_no_day(bad: dict[str, Any]) -> None:
        one = next(n for n in bad["notes"] if n["id"] == decided_note["id"])
        one["decided_on"] = None

    message = await refusal(decided_on_no_day)
    assert "decided_by and decided_on go together" in message

    def replaced_by_nothing(bad: dict[str, Any]) -> None:
        one = next(n for n in bad["notes"] if n["id"] == replaced_note["id"])
        one["superseded_by_id"] = None

    message = await refusal(replaced_by_nothing)
    assert "a superseded note needs superseded_by_id" in message

    def withdrawn_for_no_reason(bad: dict[str, Any]) -> None:
        one = next(n for n in bad["notes"] if n["id"] == withdrawn_note["id"])
        one["status_reason"] = None

    message = await refusal(withdrawn_for_no_reason)
    assert "a withdrawn note needs status_reason" in message

    def wide_by_word_of_mouth(bad: dict[str, Any]) -> None:
        bad["notes"][0]["project_wide"] = "yes"

    message = await refusal(wide_by_word_of_mouth)
    assert "notes[0].project_wide: Input should be a valid boolean" in message

    def no_why(bad: dict[str, Any]) -> None:
        bad["notes"][0]["why"] = "  "

    message = await refusal(no_why)
    assert "notes[0]" in message and "why cannot be empty" in message

    def the_old_names(bad: dict[str, Any]) -> None:
        bad["decisions"] = []
        bad["facts"] = []

    message = await refusal(the_old_names)
    assert "decisions: Extra inputs are not permitted" in message

    # A definition of done is a person's: a file that says an agent wrote one is refused.
    def written_by_an_agent(bad: dict[str, Any]) -> None:
        bad["definition_of_done"][0]["updated_by_kind"] = "agent"

    message = await refusal(written_by_an_agent)
    assert "definition_of_done[0].updated_by_kind: Input should be 'person'" in message

    # The database checks what the file says, whatever the file claims: a circle of waiting, and a
    # start date after the target date.
    def circle(bad: dict[str, Any]) -> None:
        top = max(link["id"] for link in bad["links"]) + 1
        for number, (source, target) in enumerate(
            (
                (f"task:{task['number']}", f"task:{other['number']}"),
                (f"task:{other['number']}", f"task:{task['number']}"),
            )
        ):
            bad["links"].append(
                {
                    "id": top + number,
                    "source": source,
                    "target": target,
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
    assert stack.project is not None
    async with stack.client() as client:
        world = await a_world_with_references(client, stack.project)
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
    assert f"{len(original['notes'])} notes" in restored.stdout
    # The backup holds the whole install: the same person signs in again with the same password
    # and finds the project, and the project's agent key still works.
    again = stack.project.owner.sign_in_again()
    listed = again.call("GET", "/board/api/projects")
    assert listed.status == 200
    assert [one["id"] for one in listed.body["projects"]] == [stack.project.id]
    async with stack.client() as client:
        assert await export_everything(client) == original
        # The event log reads in order, and what is written after the restore comes after it.
        assert await distinct_transactions(stack) == 1
        last = original["events"][-1]["id"]
        new = await ok(client, "create_node", kind="epic", title="After the restore")
        assert new["ref"] == f"epic:{next_number(original, 'epic')}"
        changes = await ok(client, "changes_since", after_event_id=last, limit=50)
        assert [event["node"] for event in changes["items"]] == [new["ref"]]
        # What a person confirmed is still confirmed, and the numbers of notes carry on after the
        # restore as they do after an import.
        confirmed = await ok(client, "get_note", note_id=world["fact1"]["id"])
        assert (confirmed["status"], confirmed["confirmed_by"]) == ("confirmed", "Marie")
        recalled = await ok(client, "recall", node=world["task3"]["ref"])
        assert [i["note"]["id"] for i in recalled["project_wide_notes"]["items"]] == [
            world["wide"]["id"]
        ]
        top = max(item["id"] for item in original["notes"])
        plain = await ok(client, "add_note", text="After the restore", why="Here")
        assert plain["id"] == top + 1
        decided = await ok(client, "add_note", text="Carry on.", why="Because", decided_by="Marie")
        assert decided["id"] == top + 2
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
