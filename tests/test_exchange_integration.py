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
    ScriptedModel,
    export_everything,
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

SECTIONS = ("nodes", "links", "rules", "decisions", "memories", "events")


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
    decisions, memories = by_id(document["decisions"]), by_id(document["memories"])
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
    for key in ("decision1", "decision2", "decision3", "principle"):
        shown = await ok(client, "get_decision", decision_id=world[key]["id"])
        for name, value in decisions[world[key]["id"]].items():
            if name == "recorded_at":
                assert the_same_moment(value, shown[name])
            else:
                assert value == shown[name], (key, name)
    for key in ("fact1", "fact3", "fact4", "fact5", "belief1", "belief2", "belief3", "belief4"):
        shown = await ok(client, "get_memory", memory_id=world[key]["id"])
        for name, value in memories[world[key]["id"]].items():
            if name == "evidence":
                assert value == [{"type": e["type"], "id": e["id"]} for e in shown["evidence"]]
            elif name in ("recorded_at", "confirmed_at"):
                assert value is None or the_same_moment(value, shown[name])
            else:
                assert value == shown[name], (key, name, value, shown[name])


async def test_the_export_keeps_dates_statuses_links_replacements_evidence_and_history(
    client: Client,
) -> None:
    world = await rich_world(client)
    ids = {name: item["id"] for name, item in world.items()}

    document = await export_everything(client)

    nodes, rules = by_id(document["nodes"]), by_id(document["rules"])
    decisions, memories = by_id(document["decisions"]), by_id(document["memories"])
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
    assert decisions[ids["decision1"]]["status"] == "superseded"
    assert decisions[ids["decision1"]]["superseded_by_id"] == ids["decision2"]
    assert decisions[ids["decision1"]]["node_ids"] == [ids["story1"]]
    assert decisions[ids["decision1"]]["rule_ids"] == [ids["rule1"]]
    assert decisions[ids["principle"]]["kind"] == "principle"
    assert decisions[ids["decision3"]]["promoted_from_id"] == ids["belief5"]
    fact1, belief1 = memories[ids["fact1"]], memories[ids["belief1"]]
    assert fact1["happened_on"] == "2026-11-01" and fact1["decision_ids"] == [ids["decision2"]]
    assert (fact1["node_ids"], fact1["rule_ids"]) == ([ids["story1"]], [ids["rule1"]])
    assert memories[ids["fact3"]]["status"] == "superseded"
    assert memories[ids["fact3"]]["superseded_by_id"] == ids["fact4"]
    assert memories[ids["fact5"]]["status"] == "retracted"
    assert memories[ids["fact5"]]["status_reason"] == "It is a cat."
    assert belief1["evidence"] == [
        {"type": "fact", "id": ids["fact1"]},
        {"type": "decision", "id": ids["decision2"]},
    ]
    assert (belief1["confirmed_by"], belief1["status"]) == ("Marie", "active")
    assert memories[ids["belief2"]]["evidence"] == [{"type": "fact", "id": ids["fact2"]}]
    assert memories[ids["belief3"]]["superseded_by_id"] == ids["belief4"]
    # The history is in it, handoff note included, and a claim is only history, not a lease.
    events = [event for event in document["events"] if event["node_id"] == ids["task2"]]
    handoff = next(event for event in events if event["action"] == "handoff")
    assert handoff["after"]["note"] == "Waiting for the design of the form."
    assert "claimed" in {event["action"] for event in events}
    assert "claims" not in document


async def test_the_origin_of_every_fact_and_belief_is_in_the_export(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    text = f"The nightly export {marker} runs at 02:00."
    model.script(
        marker,
        {
            "facts": [
                {"text": f"The export {marker} runs at 02:00", "kind": "world", "passage": text}
            ]
        },
    )
    extracted = (await ok(client, "extract_facts", actor="ann", text=text, store=True))["memories"][
        0
    ]
    stated = await make_fact(client, f"A person said {marker}")
    model.script(
        marker,
        {
            "beliefs": [
                {"text": f"{marker} exports are nightly", "evidence_fact_ids": [stated["id"]]}
            ]
        },
    )
    derived = (await ok(client, "consolidate_memory", actor="ann", fact_ids=[stated["id"]]))[
        "recorded"
    ][0]

    memories = by_id((await export_everything(client))["memories"])

    assert memories[stated["id"]]["origin"] == "stated"
    assert memories[extracted["id"]]["origin"] == "extracted"
    assert memories[derived["id"]]["origin"] == "derived"
    assert memories[extracted["id"]]["source"] == text


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
    assert section_end["section"] in ("rules", "decisions", "memories", "events")
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
    # One file per epic, story, task, rule, decision, principle, fact and belief, and nothing else.
    expected = sum(len(document[name]) for name in ("nodes", "rules", "decisions", "memories"))
    assert len(files) == expected
    assert f"Wrote {expected} files" in result.stdout
    stems = [Path(path).stem for path in files]
    assert len(set(stems)) == len(stems)
    folders = {Path(path).parts[0] for path in files}
    assert folders <= {"epics", "stories", "tasks", "rules", "decisions", "facts", "beliefs"}

    def file_of(kind: str, item_id: int) -> str:
        return the_file_of(files, kind, item_id)

    def text_of(kind: str, item_id: int) -> str:
        name = file_of(kind, item_id)
        return next(text for path, text in files.items() if Path(path).stem == name)

    node_prefix = {node["id"]: node["kind"] for node in document["nodes"]}
    memory_prefix = {memory["id"]: memory["type"] for memory in document["memories"]}
    decision_prefix = {decision["id"]: decision["kind"] for decision in document["decisions"]}

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
        here = (decision["kind"], decision["id"])
        connections += [(here, (node_prefix[n], n)) for n in decision["node_ids"]]
        connections += [(here, ("rule", r)) for r in decision["rule_ids"]]
        if decision["superseded_by_id"] is not None:
            target = decision["superseded_by_id"]
            connections.append((here, (decision_prefix[target], target)))
        if decision["promoted_from_id"] is not None:
            source = decision["promoted_from_id"]
            connections.append((here, (memory_prefix[source], source)))
    for memory in document["memories"]:
        here = (memory["type"], memory["id"])
        connections += [(here, (node_prefix[n], n)) for n in memory["node_ids"]]
        connections += [(here, ("rule", r)) for r in memory["rule_ids"]]
        connections += [(here, (decision_prefix[d], d)) for d in memory["decision_ids"]]
        if memory["superseded_by_id"] is not None:
            target = memory["superseded_by_id"]
            connections.append((here, (memory_prefix[target], target)))
        for evidence in memory["evidence"]:
            if evidence["type"] == "fact":
                connections.append((here, ("fact", evidence["id"])))
            else:
                connections.append((here, (decision_prefix[evidence["id"]], evidence["id"])))
    assert len(connections) > 40
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
        assert not re.search(r"\[\[(node|rule|decision|fact|belief):[1-9][0-9]{0,17}\]\]", text), (
            path
        )

    # Fields at the top, in the form a person or Obsidian reads.
    epic = front_matter(text_of("epic", ids["epic"]))
    assert epic["type"] == "epic" and epic["id"] == str(ids["epic"])
    assert epic["title"] == json.dumps(world["epic"]["title"], ensure_ascii=False)
    assert (epic["start_on"], epic["target_on"]) == ("2026-12-01", "2026-12-31")
    assert epic["status"] == "ready" and epic["priority"] == "normal"
    belief = front_matter(text_of("belief", ids["belief1"]))
    assert (belief["origin"], belief["status"], belief["confirmed_by"]) == (
        "stated",
        "active",
        '"Marie"',
    )
    fact = front_matter(text_of("fact", ids["fact1"]))
    assert (fact["kind"], fact["happened_on"]) == ("world", "2026-11-01")
    assert fact["source"] == '"The team call"'
    decision = front_matter(text_of("decision", ids["decision2"]))
    assert decision["decided_by"] == '"Marie"' and decision["status"] == "active"
    assert front_matter(text_of("principle", ids["principle"]))["type"] == "principle"
    assert "- rests on: [[" + file_of("decision", ids["decision2"]) + "]]" in text_of(
        "belief", ids["belief1"]
    )
    assert "- needs first: [[" + file_of("task", ids["task1"]) + "]]" in text_of(
        "task", ids["task2"]
    )
    assert "## Reason\n\nMarie changed her mind." in text_of("decision", ids["decision2"])
    assert "Everything about\nlogging in." in text_of("epic", ids["epic"])


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
