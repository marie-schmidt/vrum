"""Chapter 9 of the acceptance scenario: moving the whole graph out and back (M7). It is last
because it destroys the database it started with, twice: once to restore a backup into a fresh
container, and once to load an export into a fresh, empty server."""

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

from tests.acceptance_scenario import URL, chapter, clip, proof
from tests.helpers import REPO_ROOT, connect, export_everything, ok, refused, rich_world, the_token
from tests.world import make_project, register, server_base

SECTIONS = ("nodes", "links", "rules", "decisions", "memories", "events")


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


def check_markdown(folder: Path, document: dict[str, Any]) -> tuple[int, int, int]:
    """One file per item, and every connection in the graph a reference at both ends, to a file
    that exists. Returns (files, references, connections)."""
    files = {p.stem: p.read_text(encoding="utf-8") for p in folder.rglob("*.md")}
    items = sum(len(document[name]) for name in ("nodes", "rules", "decisions", "memories"))
    assert len(files) == items, f"{len(files)} files for {items} items"
    stem_of: dict[tuple[str, int], str] = {}
    for stem in files:
        kind, number, _ = stem.split("-", 2)
        stem_of[(kind, int(number))] = stem
    node_kind = {n["id"]: n["kind"] for n in document["nodes"]}
    memory_kind = {m["id"]: m["type"] for m in document["memories"]}
    decision_kind = {d["id"]: d["kind"] for d in document["decisions"]}
    pairs: list[tuple[tuple[str, int], tuple[str, int]]] = []
    for n in document["nodes"]:
        if n["parent_id"] is not None:
            pairs.append(((n["kind"], n["id"]), (node_kind[n["parent_id"]], n["parent_id"])))
    for link in document["links"]:
        a, b = link["source_id"], link["target_id"]
        pairs.append(((node_kind[a], a), (node_kind[b], b)))
    for rule in document["rules"]:
        if rule["story_id"] is not None:
            pairs.append((("rule", rule["id"]), ("story", rule["story_id"])))
    for d in document["decisions"]:
        here = (d["kind"], d["id"])
        pairs += [(here, (node_kind[x], x)) for x in d["node_ids"]]
        pairs += [(here, ("rule", x)) for x in d["rule_ids"]]
        if d["superseded_by_id"] is not None:
            pairs.append((here, (decision_kind[d["superseded_by_id"]], d["superseded_by_id"])))
        if d["promoted_from_id"] is not None:
            pairs.append((here, (memory_kind[d["promoted_from_id"]], d["promoted_from_id"])))
    for m in document["memories"]:
        here = (m["type"], m["id"])
        pairs += [(here, (node_kind[x], x)) for x in m["node_ids"]]
        pairs += [(here, ("rule", x)) for x in m["rule_ids"]]
        pairs += [(here, (decision_kind[x], x)) for x in m["decision_ids"]]
        if m["superseded_by_id"] is not None:
            pairs.append((here, (memory_kind[m["superseded_by_id"]], m["superseded_by_id"])))
        for e in m["evidence"]:
            there = ("fact", e["id"]) if e["type"] == "fact" else (decision_kind[e["id"]], e["id"])
            pairs.append((here, there))
    for source, target in pairs:
        assert stem_of[target] in references_in(files[stem_of[source]]), (source, target)
        assert stem_of[source] in references_in(files[stem_of[target]]), (target, source)
    total = 0
    for stem, text in files.items():
        for reference in references_in(text):
            total += 1
            assert reference in files, f"{stem} points at {reference}, which has no file"
    return len(files), total, len(pairs)


async def moving() -> None:
    chapter("Moving the data: a Markdown copy, a backup, an export, and a fresh server")
    work = Path(tempfile.mkdtemp(prefix="vrum-acceptance-"))
    async with connect(URL, the_token()) as client:
        await rich_world(client)
        original = await export_everything(client)
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
        "in the Markdown export there is one file per item, and every link, rule placement, "
        "decision link and belief evidence appears as a reference, at both ends, to a file that "
        "exists; made again, the same files",
        same and files > 0 and connections > 0,
        f"{files} files for {files} items, {references} references, {connections} connections "
        f"checked at both ends. {clip(made.stdout, 40)}",
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
        and [e["node_id"] for e in feed["items"]] == [new["id"]],
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
        bad["nodes"] = [n for n in bad["nodes"] if n["id"] != gone["id"]]
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
        top = max(n["id"] for n in original["nodes"])
        new = await ok(client, "create_node", actor="ann", kind="epic", title="After the import")
        second = await refused(client, "import_graph", actor="ann", document=original)
    proof(
        "M7",
        "an export loaded into an empty project comes back identical, with new ids continuing "
        "after the highest one, and a second import into it is refused",
        after == original
        and new["id"] == top + 1
        and "already holds data" in second
        and "Imported" in loaded.stdout,
        f"{counts(original)}; {exported.stat().st_size:,} bytes; next node {new['id']} after {top}",
    )
    shutil.rmtree(work, ignore_errors=True)
