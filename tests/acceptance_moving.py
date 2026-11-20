"""Chapter 14 of the acceptance scenario: moving the whole graph out and back (M7, and M16's proof
that facts, decisions, their confirmation and their history survive the trip). It is last because
it destroys the database it started with, twice: once to restore a backup into a fresh container,
and once to load an export into a fresh, empty server."""

import copy
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from tests.acceptance_scenario import URL, chapter, clip, proof, tree
from tests.helpers import (
    REPO_ROOT,
    confirm,
    connect,
    export_everything,
    kind_of,
    make_fact,
    number_of,
    ok,
    refused,
    rich_world,
    the_token,
)
from tests.world import make_project, register, server_base

SECTIONS = ("nodes", "links", "rules", "decisions", "facts", "events")
# What an agent is briefed with before it starts work, and what M16 took out of it.
BRIEFING = ("active_decisions", "project_wide_decisions", "facts", "past_decisions")
GONE_FROM_BRIEFINGS = {
    "beliefs",
    "stale_beliefs_left_out",
    "active_principles",
    "superseded_decisions",
}


def run(
    *command: str | Path, check: bool = True, key: str | None = None
) -> subprocess.CompletedProcess[str]:
    """A command of this repository, with this stack's environment (the compose project, ports,
    password ./acceptance.sh set) and the agent key of the project the scripts are to work on
    (the shared project's, unless `key` names another)."""
    result = subprocess.run(
        [str(part) for part in command],
        cwd=REPO_ROOT,
        env={**os.environ, "VRUM_AGENT_KEY": key or the_token()},
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"{command[0]} failed:\n{result.stdout}\n{result.stderr}")
    return result


async def wait_until_up() -> None:
    """Wait for the server to answer its health check: after a fresh start there is no account
    and so no key to ask anything else with."""
    for _ in range(90):
        try:
            with urllib.request.urlopen(f"{server_base()}/health", timeout=3) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise AssertionError("the server did not come back")


def counts(document: dict[str, Any]) -> str:
    return ", ".join(f"{len(document[name])} {name}" for name in SECTIONS)


def references_in(text: str) -> list[str]:
    return re.findall(r"\[\[([^\]]+)\]\]", text)


def key_of(ref: str) -> tuple[str, int]:
    """What a file is called after: the kind and the number in a reference (story:12)."""
    return kind_of(ref), number_of(ref)


def check_markdown(folder: Path, document: dict[str, Any]) -> tuple[int, int, int]:
    """One file per item, and every connection in the graph a reference at both ends, to a file
    that exists. Returns (files, references, connections)."""
    files = {p.stem: p.read_text(encoding="utf-8") for p in folder.rglob("*.md")}
    items = sum(len(document[name]) for name in ("nodes", "rules", "decisions", "facts"))
    assert len(files) == items, f"{len(files)} files for {items} items"
    stem_of: dict[tuple[str, int], str] = {}
    for stem in files:
        kind, number, _ = stem.split("-", 2)
        stem_of[(kind, int(number))] = stem
    pairs: list[tuple[tuple[str, int], tuple[str, int]]] = []
    for n in document["nodes"]:
        if n["parent"] is not None:
            pairs.append(((n["kind"], n["number"]), key_of(n["parent"])))
    for link in document["links"]:
        pairs.append((key_of(link["source"]), key_of(link["target"])))
    for rule in document["rules"]:
        if rule["node"] is not None:
            pairs.append((("rule", rule["id"]), key_of(rule["node"])))
    for d in document["decisions"]:
        here = ("decision", d["id"])
        pairs += [(here, key_of(x)) for x in d["nodes"]]
        pairs += [(here, ("rule", x)) for x in d["rule_ids"]]
        if d["superseded_by_id"] is not None:
            pairs.append((here, ("decision", d["superseded_by_id"])))
    for f in document["facts"]:
        here = ("fact", f["id"])
        pairs += [(here, key_of(x)) for x in f["nodes"]]
        pairs += [(here, ("rule", x)) for x in f["rule_ids"]]
        pairs += [(here, ("decision", x)) for x in f["decision_ids"]]
        if f["superseded_by_id"] is not None:
            pairs.append((here, ("fact", f["superseded_by_id"])))
    for source, target in pairs:
        assert stem_of[target] in references_in(files[stem_of[source]]), (source, target)
        assert stem_of[source] in references_in(files[stem_of[target]]), (target, source)
    total = 0
    for stem, text in files.items():
        for reference in references_in(text):
            total += 1
            assert reference in files, f"{stem} points at {reference}, which has no file"
    return len(files), total, len(pairs)


def reads_as_two_records(folder: Path, world: dict[str, dict[str, Any]], mention: int) -> bool:
    """The Markdown copy tells the two records apart in plain words: a fact and a decision (one
    kind of each, with the project-wide flag and no principle), whether a person confirmed them,
    and where they stand, and it follows what a text refers to."""
    files = {p.stem: p.read_text(encoding="utf-8") for p in folder.rglob("*.md")}

    def text_of(kind: str, number: int) -> str:
        return next(t for s, t in files.items() if s.startswith(f"{kind}-{number}-"))

    def stem_of(kind: str, number: int) -> str:
        return next(s for s in files if s.startswith(f"{kind}-{number}-"))

    return (
        {p.name for p in folder.iterdir() if p.is_dir()}
        <= {"epics", "stories", "tasks", "rules", "decisions", "facts"}
        and "- Confirmation: confirmed by Marie on " in text_of("fact", world["fact1"]["id"])
        and "- Confirmation: not confirmed by a person" in text_of("fact", world["fact2"]["id"])
        and f"- Status: superseded by [[{stem_of('fact', world['fact4']['id'])}]]. Why: Measured"
        in text_of("fact", world["fact3"]["id"])
        and "- Status: withdrawn. Why: They expire in an hour."
        in text_of("fact", world["fact6"]["id"])
        and ", as it was when it ended" in text_of("fact", world["fact6"]["id"])
        and "- Kind: project-wide decision" in text_of("decision", world["wide"]["id"])
        and "## Reason" in text_of("decision", world["decision2"]["id"])
        and f"[[{stem_of('story', number_of(world['story1']['ref']))}]]" in text_of("fact", mention)
        and f"[[{stem_of('decision', world['wide']['id'])}]]" in text_of("fact", mention)
        and not re.search(r"belief|principle", "\n".join(files.values()), re.IGNORECASE)
    )


async def knowledge(
    client: Any, world: dict[str, dict[str, Any]], story: dict[str, Any], epic: dict[str, Any]
) -> dict[str, Any]:
    """What a project tells an agent: who mentions what, what is recalled about a story and what
    the briefing for the next work holds. Everything in it comes from the records themselves."""
    mentions = {
        name: (await ok(client, "find_mentions", limit=200, **target))["items"]
        for name, target in (
            ("story", {"node": world["story1"]["ref"]}),
            ("project-wide decision", {"decision_id": world["wide"]["id"]}),
        )
    }
    recall = await ok(client, "recall", node=story["ref"], limit=50)
    briefing = await ok(client, "next_work", actor="briefing", within_node=epic["ref"], peek=True)
    return {
        "mentions": mentions,
        "recall": recall,
        "briefing": {name: briefing["context"][name] for name in BRIEFING},
        "keys": sorted(briefing["context"]),
    }


async def histories(client: Any, world: dict[str, dict[str, Any]]) -> dict[str, list[Any]]:
    """The history of each record that ended or was confirmed, as the tools tell it."""
    found: dict[str, list[Any]] = {}
    for name in ("fact1", "fact3", "fact6", "fact5"):
        found[name] = (await ok(client, "fact_history", fact_id=world[name]["id"]))["items"]
    for name in ("decision1", "decision2", "decision3", "wide"):
        found[name] = (await ok(client, "decision_history", decision_id=world[name]["id"]))["items"]
    return found


async def moving() -> None:
    chapter("Moving the data: a Markdown copy, a backup, an export, and a fresh server")
    work = Path(tempfile.mkdtemp(prefix="vrum-acceptance-"))
    async with connect(URL, the_token()) as client:
        world = await rich_world(client)
        epic, story, _ = await tree(client, 1)
        mention = await make_fact(
            client,
            f"Follows [[{world['story1']['ref']}]] and [[decision:{world['wide']['id']}]]",
            nodes=[story["ref"]],
            source="The design review",
        )
        await confirm(client, "fact", mention, "Marie")
        original = await export_everything(client)
        told = await knowledge(client, world, story, epic)
        past = await histories(client, world)
    summary = counts(original)

    # -- the Markdown copy
    notes = work / "notes"
    made = run(sys.executable, REPO_ROOT / "scripts" / "export_markdown.py", URL, notes)
    files, references, connections = check_markdown(notes, original)
    again = work / "notes-again"
    run(sys.executable, REPO_ROOT / "scripts" / "export_markdown.py", URL, again)
    same = all(
        (notes / p.relative_to(again)).read_text() == p.read_text() for p in again.rglob("*.md")
    )
    proof(
        "M7",
        "in the Markdown export there is one file per item, and every link, requirement "
        "placement and decision and fact link appears as a reference, at both ends, to a file "
        "that exists; made again, the same files",
        same and files > 0 and connections > 0,
        f"{files} files for {files} items, {references} references, {connections} connections "
        f"checked at both ends. {clip(made.stdout, 40)}",
    )
    proof(
        "M16",
        "the Markdown copy reflects the two records: facts and decisions in plain words, each "
        "saying whether a person confirmed it (an ended one as it was when it ended), a "
        "project-wide decision and a replacement said so, a reference in a text is a link, and "
        "no belief or principle appears",
        reads_as_two_records(notes, world, mention["id"]),
        "folders: " + ", ".join(sorted(p.name for p in notes.iterdir() if p.is_dir())),
    )

    # -- backup, destroy the volume, restore into a fresh container
    backup = work / "vrum.dump"
    started = time.monotonic()
    made = run(REPO_ROOT / "scripts" / "backup.sh", backup)
    refusal = run(REPO_ROOT / "scripts" / "restore.sh", backup, check=False)
    run("docker", "compose", "down", "--volumes", "--remove-orphans")
    volumes = run("docker", "volume", "ls", "--format", "{{.Name}}").stdout.split()
    project = os.environ["COMPOSE_PROJECT_NAME"]
    destroyed = not [v for v in volumes if v.startswith(project)]
    restored = run(REPO_ROOT / "scripts" / "restore.sh", backup)
    await wait_until_up()
    async with connect(URL, the_token()) as client:
        after = await export_everything(client)
        last = original["events"][-1]["id"]
        new = await ok(client, "create_node", actor="ann", kind="epic", title="After the restore")
        feed = await ok(client, "changes_since", after_event_id=last, limit=10)
    proof(
        "M7",
        "backup, destroy the volume, restore into a fresh container, and the exported graph is "
        "identical to the one taken before",
        after == original
        and destroyed
        and "Backed up to" in made.stdout
        and backup.stat().st_mode & 0o077 == 0
        and refusal.returncode == 1
        and "already holds data" in refusal.stderr
        and "Restored from" in restored.stdout
        and [e["node"] for e in feed["items"]] == [new["ref"]],
        f"{summary}; {backup.stat().st_size:,} bytes in {time.monotonic() - started:.0f} seconds; "
        "a restore over live data was refused; the change feed works after the restore",
    )

    # -- export, a fresh empty server, import
    exported = work / "vrum-export.json"
    async with connect(URL, the_token()) as client:
        original = await export_everything(client)
    run(sys.executable, REPO_ROOT / "scripts" / "export_graph.py", URL, exported)
    run("docker", "compose", "down", "--volumes", "--remove-orphans")
    run("docker", "compose", "up", "--detach", "--build", "--wait", "--wait-timeout", "240")
    await wait_until_up()
    # A fresh install has no account and no project: a person registers, makes one, and makes a
    # key for it, exactly as on the board, and the file is loaded into that empty project.
    empty_project = make_project(register(server_base(), "Second install owner"), "Fresh")
    async with connect(URL, empty_project.key) as client:
        empty = await export_everything(client)
        bad = copy.deepcopy(original)
        gone = next(n for n in bad["nodes"] if n["kind"] == "epic")
        bad["nodes"] = [
            n for n in bad["nodes"] if (n["kind"], n["number"]) != ("epic", gone["number"])
        ]
        bad["counts"]["nodes"] -= 1
        message = await refused(client, "import_graph", actor="ann", document=bad)
        fresh = await export_everything(client)
        still_empty = not any(fresh[n] for n in SECTIONS)
    proof(
        "M7",
        "a bad file is refused in plain words, every problem listed at once, and the fresh "
        "server is still empty",
        not any(empty[n] for n in SECTIONS)
        and "nothing was imported" in message
        and "is not in the file" in message
        and still_empty,
        f'"{clip(message, 90)}"',
    )
    loaded = run(
        sys.executable,
        REPO_ROOT / "scripts" / "import_graph.py",
        URL,
        exported,
        key=empty_project.key,
    )
    async with connect(URL, empty_project.key) as client:
        after = await export_everything(client)
        highest = {
            kind: max(n["number"] for n in original["nodes"] if n["kind"] == kind)
            for kind in ("epic", "story", "task")
        }
        now_told = await knowledge(client, world, story, epic)
        now_past = await histories(client, world)
        new_epic, new_story, new_task = await tree(client, 1)
        second = await refused(client, "import_graph", actor="ann", document=original)
    proof(
        "M7",
        "an export loaded into an empty project comes back identical, with epics, stories and "
        "tasks each going on after the highest number of their own kind, and a second import "
        "into it is refused",
        after == original
        and [new_epic["number"], new_story["number"], new_task["number"]]
        == [highest["epic"] + 1, highest["story"] + 1, highest["task"] + 1]
        and "already holds data" in second
        and "Imported" in loaded.stdout,
        f"{counts(original)}; {exported.stat().st_size:,} bytes; the next epic, story and task "
        f"are {new_epic['ref']}, {new_story['ref']} and {new_task['ref']}, after "
        f"{highest['epic']}, {highest['story']} and {highest['task']}",
    )
    actions = {e["action"] for e in original["events"]}
    wide = next(d for d in after["decisions"] if d["id"] == world["wide"]["id"])
    ended = next(f for f in after["facts"] if f["id"] == world["fact6"]["id"])
    superseded = next(f for f in after["facts"] if f["id"] == world["fact3"]["id"])
    proof(
        "M16",
        "a project's facts, decisions, confirmations, replacements and withdrawals, links and "
        "references go through export and import into a fresh project and come back as they "
        "were: the records, their histories, and what the tools say of who mentions what",
        {
            "fact_confirmed",
            "fact_superseded",
            "fact_withdrawn",
            "decision_confirmed",
            "decision_superseded",
            "decision_withdrawn",
            "fact_linked",
        }
        <= actions
        and (wide["project_wide"], wide["confirmed_by"]) == (True, "Marie")
        and (ended["status"], ended["confirmed_by"]) == ("withdrawn", "Marie")
        and (superseded["status"], superseded["superseded_by_id"])
        == ("superseded", world["fact4"]["id"])
        and now_past == past
        and now_told["mentions"] == told["mentions"]
        and any(m["source"]["item_type"] == "fact" for m in told["mentions"]["story"])
        and now_told["recall"] == told["recall"],
        f"{len(after['facts'])} facts and {len(after['decisions'])} decisions, "
        f"{sum(len(h) for h in past.values())} history entries compared",
    )
    proof(
        "M16",
        "the agent briefing in the fresh project is the one of the project it came from, made of "
        "the two records only: active decisions, project-wide decisions, facts and past "
        "decisions, each saying whether a person confirmed it",
        now_told["briefing"] == told["briefing"]
        and set(BRIEFING) <= set(now_told["keys"])
        and not GONE_FROM_BRIEFINGS & set(now_told["keys"])
        and world["wide"]["id"] in [d["id"] for d in now_told["briefing"]["project_wide_decisions"]]
        and next(
            d
            for d in now_told["briefing"]["project_wide_decisions"]
            if d["id"] == world["wide"]["id"]
        )["confirmed_by"]
        == "Marie"
        and [f["confirmed_by"] for f in now_told["briefing"]["facts"]] == ["Marie"],
        f"{len(now_told['keys'])} parts, none of the old ones",
    )
    shutil.rmtree(work, ignore_errors=True)
