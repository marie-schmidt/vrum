"""M7 against the running containers, part one: the export, and the files made from it. A real MCP
client reads the whole graph, and the scripts in scripts/ (export_graph.py, export_markdown.py) are
run as a person would run them (see ./check.sh). Loading a file into an empty server, and backing
up and restoring a database, destroy a database, so they are in test_exchange_second_stack.py."""

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    REPO_ROOT,
    confirm,
    export_everything,
    make_decision,
    make_fact,
    ok,
    refused,
    rich_world,
    run_sql,
    the_token,
    unique_word,
)
from tests.world import make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SECTIONS = ("nodes", "links", "rules", "decisions", "facts", "events")


def counts_in_the_database() -> dict[str, int]:
    found: dict[str, int] = {}
    for section in SECTIONS:
        result = run_sql(f"SELECT count(*) FROM {section}", plain=True, as_project=True)
        assert result.returncode == 0, result.stderr
        found[section] = int(result.stdout.strip())
    return found


def by_id(items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {item["id"]: item for item in items}


def the_same_moment(left: str, right: str) -> bool:
    from datetime import datetime

    return datetime.fromisoformat(left) == datetime.fromisoformat(right)


# -- what the export holds -----------------------------------------------------------------------


async def test_the_export_holds_what_the_tools_show_and_nothing_that_is_worked_out(
    client: Client,
) -> None:
    world = await rich_world(client)

    document = await export_everything(client)

    assert document["format"] == "vrum-export" and document["version"] == 1
    assert set(document) == {"format", "version", "counts", *SECTIONS}
    assert document["counts"] == counts_in_the_database()
    assert document["counts"] == {name: len(document[name]) for name in SECTIONS}
    nodes, rules = by_id(document["nodes"]), by_id(document["rules"])
    decisions, facts = by_id(document["decisions"]), by_id(document["facts"])
    # Items are in id order, and nothing the server works out is in them.
    for section in SECTIONS:
        ids = [item["id"] for item in document[section]]
        assert ids == sorted(set(ids)), section
    assert set(nodes[world["epic"]["id"]]) == {
        "id",
        "kind",
        "parent_id",
        "title",
        "body",
        "status",
        "priority",
        "start_on",
        "target_on",
        "version",
        "created_at",
        "updated_at",
    }
    # Every field is what the tool that reads it says.
    for key in ("epic", "story1", "story2", "task1", "task2", "task3"):
        shown = await ok(client, "get_node", node_id=world[key]["id"])
        exported = nodes[world[key]["id"]]
        for name in exported:
            if name in ("created_at", "updated_at"):
                assert the_same_moment(exported[name], shown[name]), (key, name)
            else:
                assert exported[name] == shown[name], (key, name, exported[name], shown[name])
    for key in ("rule1", "rule2"):
        shown = await ok(client, "get_rule", rule_id=world[key]["id"])
        for name, value in rules[world[key]["id"]].items():
            if name in ("created_at", "updated_at"):
                assert the_same_moment(value, shown[name])
            else:
                assert value == shown[name], (key, name)
    # A decision and a fact each carry their lifecycle, confirmation and links, and no field that
    # is worked out (the label and the confirmation word are said again by the tools).
    assert set(decisions[world["wide"]["id"]]) == {
        "id",
        "title",
        "text",
        "reason",
        "decided_by",
        "decided_on",
        "project_wide",
        "status",
        "superseded_by_id",
        "status_reason",
        "confirmed_by",
        "confirmed_at",
        "recorded_at",
        "node_ids",
        "rule_ids",
    }
    assert set(facts[world["fact1"]["id"]]) == {
        "id",
        "text",
        "source",
        "recorded_by",
        "recorded_at",
        "status",
        "superseded_by_id",
        "status_reason",
        "confirmed_by",
        "confirmed_at",
        "node_ids",
        "rule_ids",
        "decision_ids",
    }
    for key in ("decision1", "decision2", "decision3", "wide"):
        shown = await ok(client, "get_decision", decision_id=world[key]["id"])
        for name, value in decisions[world[key]["id"]].items():
            if name in ("recorded_at", "confirmed_at"):
                assert value is None or the_same_moment(value, shown[name]), (key, name)
            else:
                assert value == shown[name], (key, name)
    for key in ("fact1", "fact2", "fact3", "fact4", "fact5", "fact6"):
        shown = await ok(client, "get_fact", fact_id=world[key]["id"])
        for name, value in facts[world[key]["id"]].items():
            if name in ("recorded_at", "confirmed_at"):
                assert value is None or the_same_moment(value, shown[name]), (key, name)
            else:
                assert value == shown[name], (key, name, value, shown[name])


async def test_the_export_keeps_dates_statuses_links_replacements_confirmation_and_history(
    client: Client,
) -> None:
    world = await rich_world(client)
    ids = {name: item["id"] for name, item in world.items()}

    document = await export_everything(client)

    nodes, rules = by_id(document["nodes"]), by_id(document["rules"])
    decisions, facts = by_id(document["decisions"]), by_id(document["facts"])
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
    links = {(x["source_id"], x["target_id"], x["link_type"]) for x in document["links"]}
    assert {
        (ids["task2"], ids["task1"], "needs_first"),
        (ids["task3"], ids["task2"], "duplicates"),
        (ids["story2"], ids["story1"], "supersedes"),
    } <= links
    assert rules[ids["rule1"]]["story_id"] == ids["story1"]
    assert rules[ids["rule2"]]["story_id"] is None and rules[ids["rule2"]]["status"] == "settled"

    # A decision that was confirmed and then replaced keeps what it was confirmed as, and points at
    # what replaced it; the replacement starts unconfirmed.
    replaced, replacement = decisions[ids["decision1"]], decisions[ids["decision2"]]
    assert (replaced["status"], replaced["superseded_by_id"]) == ("superseded", ids["decision2"])
    assert (replaced["node_ids"], replaced["rule_ids"]) == ([ids["story1"]], [ids["rule1"]])
    assert replaced["confirmed_by"] == "Marie" and replaced["confirmed_at"] is not None
    assert replaced["status_reason"] is None and replaced["project_wide"] is False
    assert (replacement["status"], replacement["confirmed_by"]) == ("active", None)
    assert replacement["confirmed_at"] is None
    withdrawn = decisions[ids["decision3"]]
    assert (withdrawn["status"], withdrawn["status_reason"]) == (
        "withdrawn",
        "Not needed after all.",
    )
    wide = decisions[ids["wide"]]
    assert (wide["project_wide"], wide["status"], wide["confirmed_by"]) == (True, "active", "Marie")
    assert (wide["node_ids"], wide["rule_ids"]) == ([], [])
    # Facts: what they concern, replacement, withdrawal, and confirmation in each state.
    first = facts[ids["fact1"]]
    assert (first["node_ids"], first["rule_ids"]) == ([ids["story1"]], [ids["rule1"]])
    assert first["decision_ids"] == [ids["decision2"]]
    assert (first["confirmed_by"], first["status"]) == ("Marie", "active")
    assert (facts[ids["fact2"]]["confirmed_by"], facts[ids["fact2"]]["status"]) == (None, "active")
    assert facts[ids["fact3"]]["status"] == "superseded"
    assert facts[ids["fact3"]]["superseded_by_id"] == ids["fact4"]
    assert facts[ids["fact3"]]["status_reason"] == "Measured again."
    assert facts[ids["fact4"]]["source"] == "A measurement" and facts[ids["fact4"]]["status"] == (
        "active"
    )
    assert facts[ids["fact5"]]["status"] == "withdrawn"
    assert facts[ids["fact5"]]["status_reason"] == "It is a cat."
    gone = facts[ids["fact6"]]
    assert (gone["status"], gone["confirmed_by"]) == ("withdrawn", "Marie")
    assert gone["status_reason"] == "They expire in an hour."
    # The history is in it, handoff note included, and a claim is only history, not a lease.
    events = [event for event in document["events"] if event["node_id"] == ids["task2"]]
    handoff = next(event for event in events if event["action"] == "handoff")
    assert handoff["after"]["note"] == "Waiting for the design of the form."
    assert "claimed" in {event["action"] for event in events}
    assert "claims" not in document
    # Who confirmed, who changed their mind and what they said are in the history of the records.
    by_action = {
        (event["action"], event["fact_id"] or event["decision_id"]): event
        for event in document["events"]
        if event["action"].startswith(("decision_", "fact_"))
        and not event["action"].endswith("linked")
    }
    confirmed = by_action["decision_confirmed", ids["wide"]]
    assert (confirmed["actor"], confirmed["after"]["confirmed_by"]) == ("ann", "Marie")
    assert confirmed["after"]["quote"] == "Yes, keep it simple."
    assert confirmed["before"] == {"confirmed_by": None}
    assert by_action["decision_superseded", ids["decision1"]]["after"]["said_by"] == "Marie"
    ended = by_action["fact_withdrawn", ids["fact6"]]
    assert (ended["after"]["said_by"], ended["after"]["quote"]) == ("Marie", "No, they expire.")
    assert by_action["fact_confirmed", ids["fact6"]]["after"]["confirmed_by"] == "Marie"
    assert by_action["fact_superseded", ids["fact3"]]["after"]["superseded_by_id"] == ids["fact4"]


async def test_the_export_says_when_a_person_took_back_a_confirmation(client: Client) -> None:
    world = await rich_world(client)
    fact = world["fact1"]
    await ok(
        client,
        "set_confirmation",
        actor="ann",
        kind="fact",
        record_id=fact["id"],
        confirmation="unconfirmed",
        person="Marie",
        quote="I am no longer sure.",
    )

    document = await export_everything(client)

    exported = by_id(document["facts"])[fact["id"]]
    assert (exported["confirmed_by"], exported["confirmed_at"]) == (None, None)
    undone = next(
        event
        for event in document["events"]
        if event["action"] == "fact_unconfirmed" and event["fact_id"] == fact["id"]
    )
    assert undone["before"]["confirmed_by"] == "Marie" and undone["after"]["quote"] == (
        "I am no longer sure."
    )


async def test_a_record_an_agent_wrote_is_exported_unconfirmed_until_a_person_confirms_it(
    client: Client,
) -> None:
    word = unique_word()
    fact = await make_fact(client, f"A statement {word}", actor="agent-x")
    decision = await make_decision(client, f"A choice {word}", actor="agent-x")

    before = await export_everything(client)

    exported = by_id(before["facts"])[fact["id"]], by_id(before["decisions"])[decision["id"]]
    assert all(one["confirmed_by"] is None and one["confirmed_at"] is None for one in exported)
    assert exported[0]["recorded_by"] == "agent-x" and exported[1]["decided_by"] == "Marie"
    # The agent that passes on a person's confirmation is in the history; the person is the one the
    # record says confirmed it.
    await confirm(client, "fact", fact, actor="agent-x", person="Marie")
    after = await export_everything(client)
    confirmed = by_id(after["facts"])[fact["id"]]
    assert confirmed["confirmed_by"] == "Marie" and confirmed["recorded_by"] == "agent-x"
    event = next(
        e for e in after["events"] if e["action"] == "fact_confirmed" and e["fact_id"] == fact["id"]
    )
    assert event["actor"] == "agent-x" and event["after"]["confirmed_by"] == "Marie"
    assert by_id(after["decisions"])[decision["id"]]["confirmed_by"] is None


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
    assert first["next_after"] == f"nodes:{first['items'][-1]['id']}"
    second = await ok(client, "export_graph", after=first["next_after"], limit=2)
    assert second["counts"] is None
    assert second["items"][0]["id"] > first["items"][-1]["id"]
    # One past the end of a list goes on to the next list; one past the last list is empty.
    last = await ok(client, "export_graph", after="events:999999999", limit=5)
    assert (last["section"], last["items"], last["next_after"]) == (None, [], None)
    section_end = await ok(client, "export_graph", after=f"links:{10**9}", limit=5)
    assert section_end["section"] in ("rules", "decisions", "facts", "events")
    for bad in ("nodes", "nodes:x", "nodes:-1", "things:3", ":5", "nodes:1:2"):
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


def the_file_of(files: dict[str, str], prefix: str, item_id: int) -> str:
    """The name (without .md) of the one file that is this item."""
    found = [
        Path(path).stem for path in files if Path(path).stem.startswith(f"{prefix}-{item_id}-")
    ]
    assert len(found) == 1, (prefix, item_id, found)
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
    ids = {name: item["id"] for name, item in world.items()}
    document = await export_everything(client)

    result = run_script("export_markdown.py", tmp_path / "notes")

    assert result.returncode == 0, result.stdout + result.stderr
    files = read_files(tmp_path / "notes")
    # One file per epic, story, task, requirement, decision and fact, and nothing else.
    expected = sum(len(document[name]) for name in ("nodes", "rules", "decisions", "facts"))
    assert len(files) == expected
    assert f"Wrote {expected} files" in result.stdout
    stems = [Path(path).stem for path in files]
    assert len(set(stems)) == len(stems)
    folders = {Path(path).parts[0] for path in files}
    assert folders <= {"epics", "stories", "tasks", "rules", "decisions", "facts"}

    def file_of(kind: str, item_id: int) -> str:
        return the_file_of(files, kind, item_id)

    def text_of(kind: str, item_id: int) -> str:
        name = file_of(kind, item_id)
        return next(text for path, text in files.items() if Path(path).stem == name)

    node_prefix = {node["id"]: node["kind"] for node in document["nodes"]}

    def refs(kind: str, item_id: int) -> set[str]:
        return set(references_in(text_of(kind, item_id)))

    connections: list[tuple[tuple[str, int], tuple[str, int]]] = []
    for node in document["nodes"]:
        if node["parent_id"] is not None:
            connections.append(
                ((node["kind"], node["id"]), (node_prefix[node["parent_id"]], node["parent_id"]))
            )
    for link in document["links"]:
        connections.append(
            (
                (node_prefix[link["source_id"]], link["source_id"]),
                (node_prefix[link["target_id"]], link["target_id"]),
            )
        )
    for rule in document["rules"]:
        if rule["story_id"] is not None:
            connections.append((("rule", rule["id"]), ("story", rule["story_id"])))
    for decision in document["decisions"]:
        here = ("decision", decision["id"])
        connections += [(here, (node_prefix[n], n)) for n in decision["node_ids"]]
        connections += [(here, ("rule", r)) for r in decision["rule_ids"]]
        if decision["superseded_by_id"] is not None:
            connections.append((here, ("decision", decision["superseded_by_id"])))
    for fact in document["facts"]:
        here = ("fact", fact["id"])
        connections += [(here, (node_prefix[n], n)) for n in fact["node_ids"]]
        connections += [(here, ("rule", r)) for r in fact["rule_ids"]]
        connections += [(here, ("decision", d)) for d in fact["decision_ids"]]
        if fact["superseded_by_id"] is not None:
            connections.append((here, ("fact", fact["superseded_by_id"])))
    assert len(connections) > 30
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
        assert not re.search(r"\[\[(node|rule|decision|fact):[1-9][0-9]{0,17}\]\]", text), path

    # Fields at the top, in the form a person or Obsidian reads.
    epic = front_matter(text_of("epic", ids["epic"]))
    assert epic["type"] == "epic" and epic["id"] == str(ids["epic"])
    assert epic["title"] == json.dumps(world["epic"]["title"], ensure_ascii=False)
    assert (epic["start_on"], epic["target_on"]) == ("2026-12-01", "2026-12-31")
    assert epic["status"] == "ready" and epic["priority"] == "normal"
    fact = front_matter(text_of("fact", ids["fact1"]))
    assert (fact["type"], fact["status"], fact["confirmed_by"]) == ("fact", "active", '"Marie"')
    assert "source" not in fact and "confirmed_at" in fact
    decision = front_matter(text_of("decision", ids["decision2"]))
    assert decision["decided_by"] == '"Marie"' and decision["status"] == "active"
    assert decision["project_wide"] == "false" and "confirmed_by" not in decision
    wide = front_matter(text_of("decision", ids["wide"]))
    assert wide["project_wide"] == "true" and wide["confirmed_by"] == '"Marie"'
    assert "- needs first: [[" + file_of("task", ids["task1"]) + "]]" in text_of(
        "task", ids["task2"]
    )
    assert "## Reason\n\nMarie changed her mind." in text_of("decision", ids["decision2"])
    assert "Everything about\nlogging in." in text_of("epic", ids["epic"])

    # What a decision or fact is, whether it still counts and whether a person confirmed it are
    # said in plain words in its file, and a replacement is a link to the file that replaced it.
    first = text_of("fact", ids["fact1"])
    assert "- Kind: fact\n- Status: active\n- Confirmation: confirmed by Marie on " in first
    assert "## Source\n\nThe team call" in first
    assert "- concerns: [[" + file_of("decision", ids["decision2"]) + "]]" in first
    assert "- Confirmation: not confirmed by a person" in text_of("fact", ids["fact2"])
    old = text_of("fact", ids["fact3"])
    assert (
        f"- Status: superseded by [[{file_of('fact', ids['fact4'])}]]. Why: Measured again." in old
    )
    assert "- replaced by: [[" + file_of("fact", ids["fact4"]) + "]]" in old
    assert "- replaces: [[" + file_of("fact", ids["fact3"]) + "]]" in text_of("fact", ids["fact4"])
    withdrawn = text_of("fact", ids["fact6"])
    assert "- Status: withdrawn. Why: They expire in an hour." in withdrawn
    assert "- Confirmation: confirmed by Marie on " in withdrawn
    assert ", as it was when it ended" in withdrawn
    old_decision = text_of("decision", ids["decision1"])
    assert f"- Status: superseded by [[{file_of('decision', ids['decision2'])}]]" in old_decision
    assert ", as it was when it ended" in old_decision
    assert "- Status: withdrawn. Why: Not needed after all." in text_of(
        "decision", ids["decision3"]
    )
    assert "- Kind: project-wide decision" in text_of("decision", ids["wide"])
    assert "- Kind: decision\n" in text_of("decision", ids["decision2"])


async def test_references_written_in_texts_become_links_to_the_files_of_what_they_name(
    client: Client, tmp_path: Path
) -> None:
    word = unique_word()
    rule = await ok(client, "create_rule", actor="ann", title=f"Rule {word}")
    decision = await make_decision(client, f"Decision {word}")
    fact = await make_fact(client, f"Fact {word}")
    epic = await ok(
        client,
        "create_node",
        actor="ann",
        kind="epic",
        title=f"Refs {word}",
        body=(
            f"See [[fact:{fact['id']}]], [[decision:{decision['id']}]] and [[rule:{rule['id']}]]; "
            f"not [[fact:987654321]] and not [[belief:{fact['id']}]]."
        ),
    )

    result = run_script("export_markdown.py", tmp_path / "notes")

    assert result.returncode == 0, result.stdout + result.stderr
    files = read_files(tmp_path / "notes")
    text = next(text for path, text in files.items() if f"epic-{epic['id']}-" in path)
    assert f"[[{the_file_of(files, 'fact', fact['id'])}]]" in text
    assert f"[[{the_file_of(files, 'decision', decision['id'])}]]" in text
    assert f"[[{the_file_of(files, 'rule', rule['id'])}]]" in text
    # A reference to nothing is written as plain words, and a kind that is gone is only text.
    assert "fact:987654321 (missing)" in text and "[[belief:" in text


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
    old_name = the_file_of(first, "task", world["task3"]["id"])
    await ok(
        client,
        "update_node",
        actor="ann",
        node_id=world["task3"]["id"],
        expected_version=world["task3"]["version"],
        title="Send the code by text message",
    )
    assert run_script("export_markdown.py", target).returncode == 0
    files = read_files(target)
    assert (target / "my-notes.md").read_text(encoding="utf-8") == "mine\n"
    assert all(Path(path).stem != old_name for path in files)
    new_name = the_file_of(files, "task", world["task3"]["id"])
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
    assert all(not other_file[name] for name in SECTIONS)
