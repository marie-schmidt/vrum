"""M7 against the running containers, part one: the export (format version 5: nodes, links, rules
(acceptance criteria), notes, the definition of done and events), and the files made from it. A real
MCP client reads the whole graph, and the scripts in scripts/ (export_graph.py, export_markdown.py)
are run as a person would run them (see ./check.sh). Loading a file into an empty server (an old
one of decisions and facts too), and backing up and restoring a database, destroy a database, so
they are in test_exchange_second_stack.py."""

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    REPO_ROOT,
    confirm_on_board,
    connect_as,
    export_everything,
    kind_of,
    make_decision,
    make_fact,
    number_of,
    ok,
    on_board,
    ref_order,
    refused,
    rich_world,
    run_sql,
    the_token,
    unique_word,
)
from tests.world import make_project, register, the_project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SECTIONS = ("nodes", "links", "rules", "notes", "definition_of_done", "checks", "events")
# The table a list is kept in, when it is not called the same.
TABLES = {"checks": "definition_checks"}
# The lists whose items have an id of their own.
WITH_IDS = ("links", "rules", "notes", "events")

# What a note is in a file: what was recorded and where it stands, and nothing that is worked out
# (the headline and whether somebody decided it are said again by the tools).
NOTE_FIELDS = {
    "id",
    "text",
    "why",
    "decided_by",
    "decided_on",
    "project_wide",
    "recorded_by",
    "recorded_by_kind",
    "recorded_at",
    "status",
    "superseded_by_id",
    "supersedes_ids",
    "status_reason",
    "confirmed_by",
    "confirmed_at",
    "nodes",
    "rule_ids",
    "note_ids",
}


def counts_in_the_database() -> dict[str, int]:
    found: dict[str, int] = {}
    for section in SECTIONS:
        result = run_sql(
            f"SELECT count(*) FROM {TABLES.get(section, section)}", plain=True, as_project=True
        )
        assert result.returncode == 0, result.stderr
        found[section] = int(result.stdout.strip())
    return found


def by_id(items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {item["id"]: item for item in items}


def ref_in_file(node: dict[str, Any]) -> str:
    """The ref of an exported epic, story or task: it is in the file by its kind and number."""
    return f"{node['kind']}:{node['number']}"


def by_ref(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {ref_in_file(node): node for node in nodes}


def key_of(item: dict[str, Any]) -> Any:
    """What names something the tools made: the ref of an epic, story or task, else its id."""
    return item["ref"] if "ref" in item else item["id"]


def the_same_moment(left: str, right: str) -> bool:
    from datetime import datetime

    return datetime.fromisoformat(left) == datetime.fromisoformat(right)


# -- what the export holds -----------------------------------------------------------------------


async def test_the_export_holds_what_the_tools_show_and_nothing_that_is_worked_out(
    client: Client,
) -> None:
    world = await rich_world(client)

    document = await export_everything(client)

    assert document["format"] == "vrum-export" and document["version"] == 6
    assert set(document) == {"format", "version", "counts", *SECTIONS}
    assert document["counts"] == counts_in_the_database()
    assert document["counts"] == {name: len(document[name]) for name in SECTIONS}
    nodes, rules, notes = (
        by_ref(document["nodes"]),
        by_id(document["rules"]),
        by_id(document["notes"]),
    )
    # Items are in order (epics, then stories, then tasks, each by number; the others by id), and
    # nothing the server works out is in them: a node is its kind and number, with no id.
    node_refs = [ref_in_file(node) for node in document["nodes"]]
    assert node_refs == sorted(set(node_refs), key=ref_order)
    for section in WITH_IDS:
        ids = [item["id"] for item in document[section]]
        assert ids == sorted(set(ids)), section
    assert set(nodes[world["epic"]["ref"]]) == {
        "kind",
        "number",
        "parent",
        "title",
        "body",
        "status",
        "priority",
        "size",
        "start_on",
        "target_on",
        "version",
        "created_at",
        "updated_at",
    }
    # Every field is what the tool that reads it says.
    for key in ("epic", "story1", "story2", "task1", "task2", "task3"):
        shown = await ok(client, "get_node", node=world[key]["ref"])
        exported = nodes[world[key]["ref"]]
        for name in exported:
            if name in ("created_at", "updated_at"):
                assert the_same_moment(exported[name], shown[name]), (key, name)
            else:
                assert exported[name] == shown[name], (key, name, exported[name], shown[name])
    for key in ("rule1", "rule2", "rule3"):
        shown = await ok(client, "get_rule", rule_id=world[key]["id"])
        for name, value in rules[world[key]["id"]].items():
            if name in ("created_at", "updated_at"):
                assert the_same_moment(value, shown[name])
            else:
                assert value == shown[name], (key, name)
    # The definition of done is one item: its text, its version and who set it and when, as the
    # tool that reads it says. A person set it, so a person is who it says.
    (definition,) = document["definition_of_done"]
    assert set(definition) == {"body", "version", "updated_at", "updated_by", "updated_by_kind"}
    shown_definition = await ok(client, "get_definition_of_done")
    for name, value in definition.items():
        if name == "updated_at":
            assert the_same_moment(value, shown_definition[name])
        else:
            assert value == shown_definition[name], name
    assert definition["updated_by_kind"] == "person" and "Tests pass" in definition["body"]
    # A note that was decided and one that was not carry the same fields, with their status,
    # who confirmed them and links.
    for key in (
        "decision1",
        "decision2",
        "decision3",
        "wide",
        "fact1",
        "fact2",
        "fact3",
        "fact4",
        "fact5",
        "fact6",
    ):
        exported = notes[world[key]["id"]]
        assert set(exported) == NOTE_FIELDS, key
        shown = await ok(client, "get_note", note_id=world[key]["id"])
        for name, value in exported.items():
            if name in ("recorded_at", "confirmed_at"):
                assert value is None or the_same_moment(value, shown[name]), (key, name)
            else:
                assert value == shown[name], (key, name, value, shown[name])
        assert (exported["decided_by"] is not None) == shown["decided"], key


async def test_the_export_keeps_dates_statuses_links_replacements_confirmation_and_history(
    client: Client,
) -> None:
    world = await rich_world(client)
    ids = {name: key_of(item) for name, item in world.items()}
    person = the_project().owner.name

    document = await export_everything(client)

    nodes, rules, notes = (
        by_ref(document["nodes"]),
        by_id(document["rules"]),
        by_id(document["notes"]),
    )
    epic = nodes[ids["epic"]]
    assert (epic["start_on"], epic["target_on"]) == ("2026-12-01", "2026-12-31")
    assert epic["title"].startswith('Épic: log in "safely"') and epic["body"].count("\n") == 1
    # Statuses are as the server shows them, ready included.
    assert nodes[ids["task1"]]["status"] == "done"
    assert nodes[ids["task2"]]["status"] == "blocked"
    assert nodes[ids["task3"]]["status"] == "ready"
    assert (nodes[ids["story2"]]["priority"], nodes[ids["task1"]]["start_on"]) == (
        "high",
        "2026-12-02",
    )
    links = {(x["source"], x["target"], x["link_type"]) for x in document["links"]}
    assert {
        (ids["task2"], ids["task1"], "needs_first"),
        (ids["task3"], ids["task2"], "duplicates"),
        (ids["story2"], ids["story1"], "supersedes"),
    } <= links
    assert rules[ids["rule1"]]["node"] == ids["story1"]
    assert rules[ids["rule2"]]["node"] is None and rules[ids["rule2"]]["status"] == "met"
    assert rules[ids["rule3"]]["node"] == ids["task1"]
    assert rules[ids["rule1"]]["status"] == "open"

    # A decision that was confirmed and then replaced keeps who confirmed it, and points at
    # what replaced it; the replacement starts unconfirmed, and says what it replaced.
    replaced, replacement = notes[ids["decision1"]], notes[ids["decision2"]]
    assert (replaced["status"], replaced["superseded_by_id"]) == ("superseded", ids["decision2"])
    assert (replaced["nodes"], replaced["rule_ids"]) == ([ids["story1"]], [ids["rule1"]])
    assert replaced["confirmed_by"] == "Marie" and replaced["confirmed_at"] is not None
    assert replaced["status_reason"] is None and replaced["project_wide"] is False
    assert (replacement["status"], replacement["confirmed_by"]) == ("unconfirmed", None)
    assert replacement["confirmed_at"] is None and replacement["decided_by"] == "Marie"
    assert replacement["supersedes_ids"] == [ids["decision1"]]
    withdrawn = notes[ids["decision3"]]
    assert (withdrawn["status"], withdrawn["status_reason"]) == (
        "withdrawn",
        "Not needed after all.",
    )
    wide = notes[ids["wide"]]
    assert (wide["project_wide"], wide["status"], wide["confirmed_by"]) == (
        True,
        "confirmed",
        "Marie",
    )
    assert (wide["nodes"], wide["rule_ids"]) == ([], [])
    # Notes nobody decided: what they concern (a requirement, a story and another note),
    # replacement, withdrawal, and confirmation in each state.
    first = notes[ids["fact1"]]
    assert (first["decided_by"], first["decided_on"]) == (None, None)
    assert (first["nodes"], first["rule_ids"]) == ([ids["story1"]], [ids["rule1"]])
    assert first["note_ids"] == [ids["decision2"]]
    assert (first["confirmed_by"], first["status"]) == ("Marie", "confirmed")
    second = notes[ids["fact2"]]
    assert (second["confirmed_by"], second["status"]) == (None, "unconfirmed")
    assert notes[ids["fact3"]]["status"] == "superseded"
    assert notes[ids["fact3"]]["superseded_by_id"] == ids["fact4"]
    assert notes[ids["fact3"]]["status_reason"] is None
    assert notes[ids["fact4"]]["why"] == "A measurement"
    assert notes[ids["fact4"]]["status"] == "unconfirmed"
    assert notes[ids["fact4"]]["supersedes_ids"] == [ids["fact3"]]
    assert notes[ids["fact5"]]["status"] == "withdrawn"
    assert notes[ids["fact5"]]["status_reason"] == "It is a cat."
    gone = notes[ids["fact6"]]
    assert (gone["status"], gone["confirmed_by"]) == ("withdrawn", "Marie")
    assert gone["status_reason"] == "They expire in an hour."
    # The history is in it, handoff note included, and a claim is only history, not a lease.
    events = [event for event in document["events"] if event["node"] == ids["task2"]]
    handoff = next(event for event in events if event["action"] == "handoff")
    assert handoff["after"]["note"] == "Waiting for the design of the form."
    assert "claimed" in {event["action"] for event in events}
    assert "claims" not in document
    # A link's event names both of its ends by their refs.
    added = next(e for e in events if e["action"] == "link_added")
    assert (added["node"], added["other_node"]) == (ids["task2"], ids["task1"])
    assert added["after"] == {
        "source": ids["task2"],
        "target": ids["task1"],
        "link_type": "needs_first",
    }
    # Who recorded, who confirmed, who changed their mind and what they said are in the history of
    # the notes: an agent made the notes, and a person confirmed them on the board.
    agent = next(
        e["actor"]
        for e in document["events"]
        if e["action"] == "create" and e["node"] == ids["epic"]
    )
    by_action = {
        (event["action"], event["note_id"]): event
        for event in document["events"]
        if event["action"].startswith("note_") and not event["action"].endswith("linked")
    }
    recorded = by_action["note_recorded", ids["wide"]]
    assert (recorded["actor"], recorded["actor_kind"]) == (agent, "agent")
    assert recorded["after"]["decided_by"] == "Marie" and recorded["after"]["project_wide"] is True
    assert by_action["note_recorded", ids["decision2"]]["after"]["supersedes_ids"] == [
        ids["decision1"]
    ]
    confirmed = by_action["note_confirmed", ids["wide"]]
    assert (confirmed["actor"], confirmed["actor_kind"]) == (person, "person")
    assert confirmed["after"]["confirmed_by"] == "Marie"
    assert confirmed["after"]["quote"] == "Yes, keep it simple."
    assert confirmed["before"] == {"status": "unconfirmed", "confirmed_by": None}
    assert confirmed["after"]["status"] == "confirmed"
    superseded = by_action["note_superseded", ids["decision1"]]
    assert superseded["after"]["said_by"] == "Marie"
    # An event says what a note was when it ended: this one had been confirmed.
    assert superseded["before"] == {"status": "confirmed", "superseded_by_id": None}
    ended = by_action["note_withdrawn", ids["fact6"]]
    assert (ended["after"]["said_by"], ended["after"]["quote"]) == ("Marie", "No, they expire.")
    assert by_action["note_confirmed", ids["fact6"]]["after"]["confirmed_by"] == "Marie"
    assert by_action["note_superseded", ids["fact3"]]["after"]["superseded_by_id"] == ids["fact4"]
    # A link to another note is in the history of both ends of it.
    links_to_notes = [
        e for e in document["events"] if e["action"] == "note_linked" and e["other_note_id"]
    ]
    assert any(
        (e["note_id"], e["other_note_id"]) == (ids["fact1"], ids["decision2"])
        for e in links_to_notes
    )


async def test_the_export_says_when_a_person_took_back_a_confirmation(client: Client) -> None:
    world = await rich_world(client)
    note = world["fact1"]
    on_board(
        "set_confirmation",
        note_id=note["id"],
        confirmation="unconfirmed",
        person="Marie",
        quote="I am no longer sure.",
    )

    document = await export_everything(client)

    exported = by_id(document["notes"])[note["id"]]
    assert (exported["confirmed_by"], exported["confirmed_at"]) == (None, None)
    undone = next(
        event
        for event in document["events"]
        if event["action"] == "note_unconfirmed" and event["note_id"] == note["id"]
    )
    assert undone["before"]["status"] == "confirmed" and undone["after"]["status"] == "unconfirmed"
    assert undone["before"]["confirmed_by"] == "Marie" and undone["after"]["quote"] == (
        "I am no longer sure."
    )
    assert exported["status"] == "unconfirmed"
    assert (undone["actor"], undone["actor_kind"]) == (the_project().owner.name, "person")


async def test_a_note_an_agent_wrote_is_exported_unconfirmed_until_a_person_confirms_it(
    client: Client,
) -> None:
    word = unique_word()
    writer = f"writer-{word}"
    async with connect_as(writer) as agent:
        fact = await make_fact(agent, f"A statement {word}")
        decision = await make_decision(agent, f"A choice {word}")

    before = await export_everything(client)

    exported = by_id(before["notes"])[fact["id"]], by_id(before["notes"])[decision["id"]]
    assert all(one["confirmed_by"] is None and one["confirmed_at"] is None for one in exported)
    assert (exported[0]["recorded_by"], exported[0]["recorded_by_kind"]) == (writer, "agent")
    assert (exported[1]["recorded_by"], exported[1]["decided_by"]) == (writer, "Marie")
    # What a person says on the board is theirs, and who passed it on is in the history.
    confirm_on_board("note", fact, "Marie")
    after = await export_everything(client)
    confirmed = by_id(after["notes"])[fact["id"]]
    assert confirmed["confirmed_by"] == "Marie" and confirmed["recorded_by"] == writer
    event = next(
        e for e in after["events"] if e["action"] == "note_confirmed" and e["note_id"] == fact["id"]
    )
    assert (event["actor"], event["actor_kind"]) == (the_project().owner.name, "person")
    assert event["after"]["confirmed_by"] == "Marie"
    assert by_id(after["notes"])[decision["id"]]["confirmed_by"] is None


async def test_the_export_is_the_same_however_it_is_paged_and_every_time(client: Client) -> None:
    await rich_world(client)

    whole = await export_everything(client, limit=200)

    assert await export_everything(client, limit=200) == whole
    assert await export_everything(client, limit=73) == whole


async def test_each_page_is_one_list_and_the_cursor_says_where_to_go_on(client: Client) -> None:
    await rich_world(client)
    counts = counts_in_the_database()

    first = await ok(client, "export_graph", limit=2)

    assert (first["section"], len(first["items"])) == ("nodes", 2)
    assert first["counts"] == counts
    assert first["next_after"] == f"nodes:{ref_in_file(first['items'][-1])}"
    second = await ok(client, "export_graph", after=first["next_after"], limit=2)
    assert second["counts"] is None
    assert ref_order(ref_in_file(second["items"][0])) > ref_order(ref_in_file(first["items"][-1]))
    # One past the end of a list goes on to the next list; one past the last list is empty.
    last = await ok(client, "export_graph", after="events:999999999", limit=5)
    assert (last["section"], last["items"], last["next_after"]) == (None, [], None)
    section_end = await ok(client, "export_graph", after=f"links:{10**9}", limit=5)
    assert section_end["section"] in ("rules", "notes", "definition_of_done", "checks", "events")
    # A page of notes is continued by the id of its last note.
    notes = await ok(client, "export_graph", after="rules:999999999", limit=2)
    assert (notes["section"], len(notes["items"])) == ("notes", 2)
    assert notes["next_after"] == f"notes:{notes['items'][-1]['id']}"
    # The definition of done is a list of one item, and its page goes on to the ticks of its
    # checks, which are continued by their node and item. This project has none, so the next
    # page is the events.
    definition = await ok(client, "export_graph", after="notes:999999999", limit=5)
    assert (definition["section"], len(definition["items"])) == ("definition_of_done", 1)
    assert definition["next_after"] == "checks:0"
    ticks = await ok(client, "export_graph", after=definition["next_after"], limit=5)
    assert (ticks["section"], ticks["items"][0]["action"]) == ("events", "create")
    for bad in (
        "nodes",
        "nodes:x",
        "nodes:-1",
        "nodes:12",
        "nodes:node:3",
        "nodes:story:0",
        "things:3",
        # The lists that decisions and facts were two of are gone.
        "decisions:3",
        "facts:3",
        ":5",
        "nodes:1:2",
        # A tick is continued by its node and the item's 8-character key.
        "checks:5",
        "checks:story:12",
        "checks:story:12:TESTS",
        "checks:12:abcdef12",
    ):
        message = await refused(client, "export_graph", after=bad)
        assert "after must be left empty for the first page" in message, bad


# -- the Markdown copy ---------------------------------------------------------------------------


def run_script(name: str, *arguments: str | Path, key: str | None = None) -> Any:
    """A script of this repository, run as a person would, with the shared project's agent key (or
    another, or none at all with an empty one)."""
    import os
    import subprocess

    url = os.environ["VRUM_TEST_URL"]
    environment = {k: v for k, v in os.environ.items() if k != "VRUM_AGENT_KEY"}
    wanted = the_token() if key is None else key
    if wanted:
        environment["VRUM_AGENT_KEY"] = wanted
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / name), url, *map(str, arguments)],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def read_files(folder: Path) -> dict[str, str]:
    return {
        str(path.relative_to(folder)): path.read_text(encoding="utf-8")
        for path in sorted(folder.rglob("*.md"))
    }


def the_file_of(files: dict[str, str], prefix: str, item: int | str) -> str:
    """The name (without .md) of the one file that is this item: given its id, or its ref (a file is
    called kind-number-title)."""
    number = number_of(item) if isinstance(item, str) else item
    found = [Path(path).stem for path in files if Path(path).stem.startswith(f"{prefix}-{number}-")]
    assert len(found) == 1, (prefix, item, found)
    return found[0]


def references_in(text: str) -> list[str]:
    return re.findall(r"\[\[([^\]]+)\]\]", text)


def front_matter(text: str) -> dict[str, str]:
    lines = text.split("\n")
    assert lines[0] == "---"
    end = lines.index("---", 1)
    return {key: value for key, _, value in (line.partition(": ") for line in lines[1:end])}


async def test_the_markdown_copy_has_one_file_per_item_and_every_connection_is_a_reference(
    client: Client, tmp_path: Path
) -> None:
    world = await rich_world(client)
    ids = {name: key_of(item) for name, item in world.items()}
    document = await export_everything(client)

    result = run_script("export_markdown.py", tmp_path / "notes")

    assert result.returncode == 0, result.stdout + result.stderr
    files = read_files(tmp_path / "notes")
    # One file per epic, story, task, acceptance criterion and note, and the definition of done.
    expected = sum(len(document[name]) for name in ("nodes", "rules", "notes")) + 1
    assert len(files) == expected
    assert f"Wrote {expected} files" in result.stdout
    stems = [Path(path).stem for path in files]
    assert len(set(stems)) == len(stems)
    folders = {Path(path).parts[0] for path in files}
    assert folders <= {"epics", "stories", "tasks", "rules", "notes", "definition-of-done.md"}
    assert "Tests pass" in files["definition-of-done.md"]

    def file_of(kind: str, item: int | str) -> str:
        return the_file_of(files, kind, item)

    def text_of(kind: str, item: int | str) -> str:
        name = file_of(kind, item)
        return next(text for path, text in files.items() if Path(path).stem == name)

    def refs(kind: str, item: int | str) -> set[str]:
        return set(references_in(text_of(kind, item)))

    def at(ref: str) -> tuple[str, int]:
        """The kind and number of a ref: how the file of an epic, story or task is named."""
        return kind_of(ref), number_of(ref)

    connections: list[tuple[tuple[str, int], tuple[str, int]]] = []
    for node in document["nodes"]:
        if node["parent"] is not None:
            connections.append(((node["kind"], node["number"]), at(node["parent"])))
    for link in document["links"]:
        connections.append((at(link["source"]), at(link["target"])))
    for rule in document["rules"]:
        if rule["node"] is not None:
            connections.append((("rule", rule["id"]), at(rule["node"])))
    for note in document["notes"]:
        here = ("note", note["id"])
        connections += [(here, at(n)) for n in note["nodes"]]
        connections += [(here, ("rule", r)) for r in note["rule_ids"]]
        connections += [(here, ("note", n)) for n in note["note_ids"]]
        if note["superseded_by_id"] is not None:
            connections.append((here, ("note", note["superseded_by_id"])))
    assert len(connections) >= 19
    for source, target in connections:
        # The reference is in the file it starts from, and in the one it arrives at, and the file
        # it points to is there.
        assert file_of(*target) in refs(*source), (source, target)
        assert file_of(*source) in refs(*target), (target, source)
    every_stem = set(stems)
    for path, text in files.items():
        # A person's own text may hold other double brackets, which the copy leaves as written.
        # Every link the copy makes (the Links section, and the references written in texts that
        # it turns into links) is to a file, and a reference to nothing has no brackets left.
        for reference in references_in(text):
            if re.fullmatch(r"[a-z]+-\d+-.*", reference):
                assert reference in every_stem, (path, reference)
        assert not re.search(r"\[\[(epic|story|task|rule|note):[1-9][0-9]{0,17}\]\]", text), path

    # Fields at the top, in the form a person or Obsidian reads.
    epic = front_matter(text_of("epic", ids["epic"]))
    assert epic["type"] == "epic" and epic["number"] == str(number_of(ids["epic"]))
    assert "id" not in epic
    assert epic["title"] == json.dumps(world["epic"]["title"], ensure_ascii=False)
    assert (epic["start_on"], epic["target_on"]) == ("2026-12-01", "2026-12-31")
    assert epic["status"] == "ready" and epic["priority"] == "normal"
    plain = front_matter(text_of("note", ids["fact1"]))
    assert (plain["type"], plain["status"], plain["confirmed_by"]) == (
        "note",
        "confirmed",
        '"Marie"',
    )
    assert "decided_by" not in plain and "decided_on" not in plain and "confirmed_at" in plain
    assert "source" not in plain and "why" not in plain and "title" not in plain
    decision = front_matter(text_of("note", ids["decision2"]))
    assert decision["decided_by"] == '"Marie"' and decision["status"] == "unconfirmed"
    assert decision["decided_on"] == world["decision2"]["decided_on"]
    assert decision["project_wide"] == "false" and "confirmed_by" not in decision
    wide = front_matter(text_of("note", ids["wide"]))
    assert wide["project_wide"] == "true" and wide["confirmed_by"] == '"Marie"'
    assert "- needs first: [[" + file_of("task", ids["task1"]) + "]]" in text_of(
        "task", ids["task2"]
    )
    assert "## Why\n\nMarie changed her mind." in text_of("note", ids["decision2"])
    assert "Everything about\nlogging in." in text_of("epic", ids["epic"])
    # A note has no title: its first line is the file's heading and the words in its file's name.
    headline = world["decision2"]["headline"]
    assert headline in text_of("note", ids["decision2"]).split("\n")
    assert "\n# " + headline + "\n" in text_of("note", ids["decision2"])
    assert file_of("note", ids["decision2"]).startswith(
        f"note-{ids['decision2']}-use-email-and-a-code"
    )

    # What a note's status is, who decided it and whether it applies throughout the project are
    # said in plain words in its file, and a replacement is a link to the file that replaced it.
    first = text_of("note", ids["fact1"])
    assert first.count("- Status: confirmed by Marie on ") == 1
    assert "- Kind:" not in first and "- Confirmation:" not in first
    assert "## Why\n\nThe team call" in first
    assert "- concerns: [[" + file_of("note", ids["decision2"]) + "]]" in first
    assert "- Status: unconfirmed\n" in text_of("note", ids["fact2"])
    old = text_of("note", ids["fact3"])
    assert f"- Status: superseded by [[{file_of('note', ids['fact4'])}]]\n" in old
    assert "- replaced by: [[" + file_of("note", ids["fact4"]) + "]]" in old
    assert "- replaces: [[" + file_of("note", ids["fact3"]) + "]]" in text_of("note", ids["fact4"])
    ended = text_of("note", ids["fact6"])
    assert "- Status: withdrawn. Why: They expire in an hour." in ended
    assert "- Confirmed: by Marie on " in ended
    assert ", as it was when it ended" in ended
    old_decision = text_of("note", ids["decision1"])
    assert f"- Status: superseded by [[{file_of('note', ids['decision2'])}]]" in old_decision
    assert ", as it was when it ended" in old_decision
    assert "- Status: withdrawn. Why: Not needed after all." in text_of("note", ids["decision3"])
    assert "- Applies: throughout the project" in text_of("note", ids["wide"])
    assert "- Decided: by Marie on " in text_of("note", ids["wide"])
    assert "- Decided: by Marie on " in text_of("note", ids["decision2"])
    assert "- Decided:" not in first and "- Applies:" not in first


async def test_a_note_that_replaced_several_says_so_in_the_export_and_in_the_copy(
    client: Client, tmp_path: Path
) -> None:
    word = unique_word()
    one = await make_fact(client, f"First {word}")
    two = await make_decision(client, f"Second {word}")
    change = await ok(
        client,
        "supersede_note",
        replaces=[one["id"], two["id"]],
        text=f"Both {word}",
        why="They say the same",
    )
    both = change["replacement"]

    notes = by_id((await export_everything(client))["notes"])

    assert notes[both["id"]]["supersedes_ids"] == [one["id"], two["id"]]
    assert {notes[one["id"]]["superseded_by_id"], notes[two["id"]]["superseded_by_id"]} == {
        both["id"]
    }
    assert run_script("export_markdown.py", tmp_path / "notes").returncode == 0
    files = read_files(tmp_path / "notes")
    newer = next(
        text
        for path, text in files.items()
        if Path(path).stem == the_file_of(files, "note", both["id"])
    )
    for earlier in (one, two):
        assert f"- replaces: [[{the_file_of(files, 'note', earlier['id'])}]]" in newer


async def test_references_written_in_texts_become_links_to_the_files_of_what_they_name(
    client: Client, tmp_path: Path
) -> None:
    word = unique_word()
    rule = await ok(client, "create_rule", title=f"Rule {word}")
    decision = await make_decision(client, f"Decision {word}")
    fact = await make_fact(client, f"Fact {word}")
    other = await ok(client, "create_node", kind="epic", title=f"Other {word}")
    story = await ok(
        client, "create_node", kind="story", title=f"Story {word}", parent=other["ref"]
    )
    task = await ok(client, "create_node", kind="task", title=f"Task {word}", parent=story["ref"])
    epic = await ok(
        client,
        "create_node",
        kind="epic",
        title=f"Refs {word}",
        body=(
            f"See [[note:{fact['id']}]], [[note:{decision['id']}]] and [[rule:{rule['id']}]]; "
            f"also [[{other['ref']}]], [[{story['ref']}]] and [[{task['ref']}]]; "
            f"not [[note:987654321]], not [[story:987654321]], not [[node:{story['number']}]] "
            f"and not [[belief:{fact['id']}]]. [[decision:{decision['id']}]] and "
            f"[[fact:{fact['id']}]] are the words of the time before notes."
        ),
    )

    result = run_script("export_markdown.py", tmp_path / "notes")

    assert result.returncode == 0, result.stdout + result.stderr
    files = read_files(tmp_path / "notes")
    stem = f"epic-{epic['number']}-"
    text = next(text for path, text in files.items() if Path(path).stem.startswith(stem))
    assert f"[[{the_file_of(files, 'note', fact['id'])}]]" in text
    assert f"[[{the_file_of(files, 'note', decision['id'])}]]" in text
    assert f"[[{the_file_of(files, 'rule', rule['id'])}]]" in text
    for item in (other, story, task):
        assert f"[[{the_file_of(files, item['kind'], item['ref'])}]]" in text
    # A reference to nothing is written as plain words, and a kind that is gone is only text:
    # decisions and facts are notes now, so those two references name nothing.
    assert "note:987654321 (missing)" in text and "story:987654321 (missing)" in text
    assert "[[belief:" in text and f"[[node:{story['number']}]]" in text
    assert f"[[decision:{decision['id']}]]" in text and f"[[fact:{fact['id']}]]" in text


async def test_the_markdown_copy_is_made_again_the_same_and_only_its_own_folders_change(
    client: Client, tmp_path: Path
) -> None:
    world = await rich_world(client)
    target = tmp_path / "notes"
    assert run_script("export_markdown.py", target).returncode == 0
    first = read_files(target)

    again = run_script("export_markdown.py", target)

    assert again.returncode == 0, again.stderr
    assert read_files(target) == first
    # What the person added next to it is left alone, and what changed in the graph is changed.
    (target / "my-notes.md").write_text("mine\n", encoding="utf-8")
    old_name = the_file_of(first, "task", world["task3"]["ref"])
    await ok(
        client,
        "update_node",
        node=world["task3"]["ref"],
        expected_version=world["task3"]["version"],
        title="Send the code by text message",
    )
    assert run_script("export_markdown.py", target).returncode == 0
    files = read_files(target)
    assert (target / "my-notes.md").read_text(encoding="utf-8") == "mine\n"
    assert all(Path(path).stem != old_name for path in files)
    new_name = the_file_of(files, "task", world["task3"]["ref"])
    assert "send-the-code-by-text-message" in new_name


async def test_the_markdown_copy_will_not_write_into_a_folder_that_holds_other_files(
    client: Client, tmp_path: Path
) -> None:
    target = tmp_path / "somebody-elses"
    (target / "epics").mkdir(parents=True)
    (target / "epics" / "precious.md").write_text("do not lose me\n", encoding="utf-8")

    result = run_script("export_markdown.py", target)

    assert result.returncode != 0
    assert "already has files in it that this script did not write" in result.stderr
    assert (target / "epics" / "precious.md").read_text(encoding="utf-8") == "do not lose me\n"
    assert [path.name for path in target.rglob("*") if path.is_file()] == ["precious.md"]


async def test_the_export_script_writes_the_same_file_as_the_pages_and_for_its_own_project_only(
    client: Client, tmp_path: Path
) -> None:
    await rich_world(client)
    target = tmp_path / "vrum-export.json"

    result = run_script("export_graph.py", target)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(target.read_text(encoding="utf-8")) == await export_everything(client)
    assert target.stat().st_mode & 0o077 == 0
    assert "nodes" in result.stdout
    # With no agent key it says what to do and writes nothing; with the key of another project it
    # writes that project's graph, which holds none of this one's.
    nothing = run_script("export_graph.py", tmp_path / "none.json", key="")
    assert nothing.returncode != 0 and "VRUM_AGENT_KEY is not set" in nothing.stderr
    assert not (tmp_path / "none.json").exists()
    elsewhere = make_project(register(), "Somewhere else")
    other = run_script("export_graph.py", tmp_path / "other.json", key=elsewhere.key)
    assert other.returncode == 0, other.stdout + other.stderr
    other_file = json.loads((tmp_path / "other.json").read_text(encoding="utf-8"))
    assert other_file["version"] == 6 and all(not other_file[name] for name in SECTIONS)
