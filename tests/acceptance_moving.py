"""Chapter 14 of the acceptance scenario: moving the whole graph out and back (M7, and M27's proof
that notes, their confirmation and their history survive the trip). It is last because it
destroys the database it started with, twice: once to restore a backup into a fresh container, and
once to load an export into a fresh, empty server."""

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

from tests.acceptance_scenario import (
    ASK_QUESTION,
    CLIENT_NAME,
    NEWER_COMMIT,
    PULL_REQUEST,
    RUN_LABEL,
    URL,
    WORK_BRANCH,
    WORK_REPOSITORY,
    chapter,
    clip,
    proof,
    said_about,
    tree,
)
from tests.helpers import (
    REPO_ROOT,
    confirm_on_board,
    connect,
    export_everything,
    kind_of,
    make_fact,
    number_of,
    ok,
    on_board,
    refused,
    rich_world,
    the_token,
    unique_word,
)
from tests.test_gates_plans_integration import PLAN, RELEASE, far
from tests.world import make_project, register, server_base, the_project

SECTIONS = ("nodes", "links", "rules", "notes", "definition_of_done", "checks", "events")
# What an agent is briefed with before it starts work about what is known: the notes, in the three
# sections of M27, and the project's definition of done. What M27 took out of it is below.
BRIEFING = ("notes", "project_wide_notes", "past_notes", "definition_of_done")
GONE_FROM_BRIEFINGS = {
    "beliefs",
    "stale_beliefs_left_out",
    "active_principles",
    "superseded_decisions",
    "active_decisions",
    "project_wide_decisions",
    "past_decisions",
    "facts",
    "rules",
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


def markdown_files(folder: Path) -> dict[str, str]:
    """The files of the copy that are one item each (the definition of done is one more file, at
    the top, and is read apart), by file name."""
    return {
        p.stem: p.read_text(encoding="utf-8") for p in folder.rglob("*.md") if p.parent != folder
    }


def check_markdown(folder: Path, document: dict[str, Any]) -> tuple[int, int, int]:
    """One file per item, and every connection in the graph a reference at both ends, to a file
    that exists. Returns (files, references, connections)."""
    files = markdown_files(folder)
    items = sum(len(document[name]) for name in ("nodes", "rules", "notes"))
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
    for note in document["notes"]:
        here = ("note", note["id"])
        pairs += [(here, key_of(x)) for x in note["nodes"]]
        pairs += [(here, ("rule", x)) for x in note["rule_ids"]]
        pairs += [(here, ("note", x)) for x in note["note_ids"]]
        if note["superseded_by_id"] is not None:
            pairs.append((here, ("note", note["superseded_by_id"])))
    for source, target in pairs:
        assert stem_of[target] in references_in(files[stem_of[source]]), (source, target)
        assert stem_of[source] in references_in(files[stem_of[target]]), (target, source)
    total = 0
    for stem, text in files.items():
        for reference in references_in(text):
            total += 1
            assert reference in files, f"{stem} points at {reference}, which has no file"
    return len(files), total, len(pairs)


def reads_as_notes(folder: Path, world: dict[str, dict[str, Any]], mention: int) -> bool:
    """The Markdown copy says in plain words where each note stands: whether a person confirmed it
    and when, that a replaced one was replaced and by what, that a withdrawn one was withdrawn and
    why (keeping the confirmation it had), who decided it, and that it applies throughout the
    project. It follows what a text refers to, and holds the definition of done in one file."""
    files = markdown_files(folder)

    def text_of(kind: str, number: int) -> str:
        return next(t for s, t in files.items() if s.startswith(f"{kind}-{number}-"))

    def stem_of(kind: str, number: int) -> str:
        return next(s for s in files if s.startswith(f"{kind}-{number}-"))

    definition = (folder / "definition-of-done.md").read_text(encoding="utf-8")
    return (
        {p.name for p in folder.iterdir() if p.is_dir()}
        == {"epics", "stories", "tasks", "rules", "notes"}
        and "- Status: confirmed by Marie on " in text_of("note", world["fact1"]["id"])
        and "- Status: unconfirmed" in text_of("note", world["fact2"]["id"])
        and f"- Status: superseded by [[{stem_of('note', world['fact4']['id'])}]]"
        in text_of("note", world["fact3"]["id"])
        and "- Status: withdrawn. Why: They expire in an hour."
        in text_of("note", world["fact6"]["id"])
        and ", as it was when it ended" in text_of("note", world["fact6"]["id"])
        and "- Applies: throughout the project" in text_of("note", world["wide"]["id"])
        and "- Decided: by Marie on " in text_of("note", world["wide"]["id"])
        and "## Why" in text_of("note", world["decision2"]["id"])
        and f"[[{stem_of('story', number_of(world['story1']['ref']))}]]" in text_of("note", mention)
        and f"[[{stem_of('note', world['wide']['id'])}]]" in text_of("note", mention)
        and "Tests pass, and a person can try it" in definition
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
            ("project-wide note", {"note_id": world["wide"]["id"]}),
        )
    }
    recall = await ok(client, "recall", node=story["ref"], limit=50)
    briefing = await ok(client, "next_work", within_node=epic["ref"], peek=True)
    return {
        "mentions": mentions,
        "recall": recall,
        "briefing": {name: briefing["context"][name] for name in BRIEFING},
        "keys": sorted(briefing["context"]),
    }


async def histories(client: Any, world: dict[str, dict[str, Any]]) -> dict[str, list[Any]]:
    """The history of each note that ended or was confirmed, as the tools tell it."""
    found: dict[str, list[Any]] = {}
    for name in ("fact1", "fact3", "fact6", "fact5", "decision1", "decision2", "decision3", "wide"):
        found[name] = (await ok(client, "note_history", note_id=world[name]["id"]))["items"]
    return found


async def asks_of_agents(client: Any) -> list[dict[str, Any]]:
    """The questions agents put to a person with an ask (not the ones that gates raise, which are
    asks of their own with the effect gates and are shown with the gates)."""
    items = (await ok(client, "list_asks", limit=200))["items"]
    return [found for found in items if found["effect"] != "gates"]


def blame_without_times(blame: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """The blame of a plan's lines as the tools read it, without the times (a restore keeps them
    and so does an import, but they are the history's to show)."""
    return [
        (
            b["ref"],
            b["change"],
            b["revision"],
            b["by"],
            b["note"],
            b["review"]["state"],
            b["review"]["revision"],
            b["review"]["by"],
            b["review"]["feedback"],
        )
        for b in blame
    ]


async def held_and_proposed(client: Any, refs: list[str]) -> dict[str, Any]:
    """What gates, drafted plans, plan templates, proposals to move work and the link that says
    where work was found came to, as the tools say it, without the times (which a restore keeps and
    an import keeps too, but which are the history's to show)."""
    gated = {}
    for ref in refs:
        node = await ok(client, "get_node", node=ref)
        gated[ref] = (
            node["held_by"],
            [(g["id"], g["kind"], g["state"], g["words"]) for g in node["gates"]],
        )
    plans = sorted((await ok(client, "list_plans", limit=200))["items"], key=lambda p: p["id"])
    # A plan that has a conversation (M51): its revisions and the feedback on them, whole, as the
    # tools read them, with revision 1 read as it was.
    talked: dict[int, Any] = {}
    for one in plans:
        whole = (await ok(client, "list_plans", plan=one["id"]))["items"][0]
        if whole["revision"] > 1 or whole["feedback"]:
            first = (await ok(client, "list_plans", plan=one["id"], revision=1))["items"][0]
            talked[one["id"]] = {
                "state": whole["state"],
                "revision": whole["revision"],
                "revisions": [
                    (r["revision"], r["by"], r["note"], r["items"], r["removed"], r["feedback"])
                    for r in whole["revisions"]
                ],
                "feedback": [
                    (
                        f["id"],
                        f["revision"],
                        f["by"],
                        f["summary"],
                        [(c["item"], c["field"], c["text"], c["quoted"]) for c in f["comments"]],
                        f["approved"],
                    )
                    for f in whole["feedback"]
                ],
                "blame": blame_without_times(whole["blame"]),
                "first_blame": blame_without_times(first["blame"]),
                "tree": whole["tree"],
                "changes": whole["changes"],
                "first": first["tree"],
            }
    templates = (await ok(client, "list_plan_templates"))["items"]
    moves = sorted((await ok(client, "list_placements", limit=200))["items"], key=lambda m: m["id"])
    found = [
        (link["source"], link["target"])
        for ref in refs
        for link in (await ok(client, "list_links", node=ref, limit=200))["items"]
        if link["link_type"] == "discovered_from" and link["source"] == ref
    ]
    return {
        "gated": gated,
        "plans": [
            (
                p["id"],
                p["title"],
                p["state"],
                p["epics"],
                p["stories"],
                p["tasks"],
                p["items"],
                p["decided_by"],
                p["reason"],
            )
            for p in plans
        ],  # fmt: skip
        "templates": [(t["name"], t["version"], t["counts"], t["variables"]) for t in templates],
        "talked": talked,
        "moves": [
            (
                m["id"],
                m["state"],
                [i["ref"] for i in m["items"]],
                m["parent"],
                m["reason"],
                m["decided_by"],
                m["decision_reason"],
                m["applied"],
                m["skipped"],
            )
            for m in moves
        ],  # fmt: skip
        "found": sorted(found),
    }


async def moving() -> None:
    chapter("Moving the data: a Markdown copy, a backup, an export, and a fresh server")
    work = Path(tempfile.mkdtemp(prefix="vrum-acceptance-"))
    async with connect(URL, the_token()) as client:
        world = await rich_world(client)
        epic, story, quiet = await tree(client, 1)
        mention = await make_fact(
            client,
            f"Follows [[{world['story1']['ref']}]] and [[note:{world['wide']['id']}]]",
            nodes=[story["ref"]],
            source="The design review",
        )
        confirm_on_board("note", mention, "Marie")
        # An agent said where its work is (M41), and the handoff note it left keeps the words.
        shipped = (await tree(client, 1))[2]
        await ok(
            client, "claim_node", node=shipped["ref"], repository=WORK_REPOSITORY,
            branch=WORK_BRANCH, commit=NEWER_COMMIT, run_label=RUN_LABEL, client_name=CLIENT_NAME,
        )  # fmt: skip
        await ok(
            client, "finish_work", node=shipped["ref"], outcome="blocked",
            note="Ready for a verifier.", pull_request=PULL_REQUEST,
        )  # fmt: skip
        # Asks (M45): a question that blocked work, answered by a person with a comment, and one
        # that is still waiting.
        _, _, answered_task, open_task = await tree(client, 2)
        for held, ask in (
            (answered_task, {"question": ASK_QUESTION, "choices": ["Stripe", "Adyen", "Neither"]}),
            (open_task, {"question": "Is it still open?"}),
        ):
            await ok(client, "claim_node", node=held["ref"])
            await ok(
                client, "finish_work", node=held["ref"], outcome="blocked",
                note="I need a decision.", ask=ask,
            )  # fmt: skip
        answered_ask = (await ok(client, "get_node", node=answered_task["ref"]))["asks"][0]["id"]
        open_ask = (await ok(client, "get_node", node=open_task["ref"]))["asks"][0]["id"]
        on_board("answer_ask", ask=answered_ask, answer="Stripe", comment="Cheapest.")
        # Gates, plans, a template and proposals (M43, M44, M47): a task held by a timer, one whose
        # approval nobody has answered, a plan that waits for a person and one a person approved,
        # a saved template, a link that says where work was found, and two proposals to move a
        # story, one waiting and one rejected by a person.
        _, _, timed, approving = await tree(client, 2)
        word = unique_word()
        await ok(client, "add_gate", node=timed["ref"], kind="timer", not_before=far())
        await ok(
            client, "add_gate", node=approving["ref"], kind="approval", question="May we go on?"
        )
        await ok(
            client, "add_link", source=timed["ref"], target=approving["ref"],
            link_type="discovered_from",
        )  # fmt: skip
        await ok(client, "create_plan", items=PLAN, draft=True, title=f"Waiting {word}")
        decided_plan = await ok(
            client, "create_plan", items=PLAN, draft=True, title=f"Approved {word}"
        )
        on_board("approve_plan", plan=decided_plan["plan"], approved=True)
        # A plan that has a conversation (M51): a person asked for changes with a comment on a
        # line, its agent sent revision 2 that drops one task, and it waits for a person again.
        talked_plan = await ok(
            client, "create_plan", items=PLAN, draft=True, title=f"Talked about {word}"
        )
        talked_refs = {c["key"]: c["ref"] for c in talked_plan["created"]}
        on_board(
            "request_plan_changes", plan=talked_plan["plan"], revision=1,
            summary="One task too many.",
            comments=[{"item": talked_refs["t2"], "field": "title", "text": "Is this needed?"}],
            approved=[talked_refs["t1"]],
        )  # fmt: skip
        revised_items = [
            {**item, "ref": talked_refs[item["key"]]} for item in PLAN if item["key"] != "t2"
        ]
        await ok(
            client, "revise_plan", plan=talked_plan["plan"], items=revised_items,
            note="Took out the provider call.",
        )  # fmt: skip
        await ok(client, "save_plan_template", name=f"moving-{word}", template=RELEASE)
        one, two = await tree(client, 1), await tree(client, 1)
        await ok(
            client, "propose_placement", items=[one[1]["ref"]], parent=two[0]["ref"],
            reason="Looks like it belongs there.",
        )  # fmt: skip
        turned_down = await ok(
            client, "propose_placement", items=[two[1]["ref"]], parent=one[0]["ref"],
            reason="And the other way round.",
        )  # fmt: skip
        on_board(
            "decide_placement", placement=turned_down["id"], accepted=False, reason="Leave it."
        )
        # A move a person accepted in part (M53): two stories to move, one named, so one moved and
        # the other left where it is.
        source, target = await tree(client, 0), await tree(client, 0)
        second_story = await ok(
            client, "create_node", kind="story", title="Second story", parent=source[0]["ref"]
        )
        in_part = await ok(
            client, "propose_placement", items=[source[1]["ref"], second_story["ref"]],
            parent=target[0]["ref"], reason="Both look like part of the other epic.",
        )  # fmt: skip
        on_board(
            "decide_placement", placement=in_part["id"], accepted=True, items=[source[1]["ref"]]
        )
        left_out_story = second_story["ref"]
        left_out_parent = source[0]["ref"]
        gate_refs = [timed["ref"], approving["ref"]]
        held_before = await held_and_proposed(client, gate_refs)
        asked_before = await asks_of_agents(client)
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
        "in the Markdown export there is one file per item, and every link, acceptance criterion "
        "placement and note link appears as a reference, at both ends, to a file that exists; "
        "made again, the same files",
        same and files > 0 and connections > 0,
        f"{files} files for {files} items, {references} references, {connections} connections "
        f"checked at both ends. {clip(made.stdout, 40)}",
    )
    proof(
        "M27",
        "the Markdown copy reflects the notes: each says whether a person confirmed it (an ended "
        "one as it was when it ended), a replaced one says what replaced it, a withdrawn one says "
        "why, a decision says who decided it, a project-wide note says so, a reference in a text "
        "is a link, the definition of done has its own file, and no belief or principle appears",
        reads_as_notes(notes, world, mention["id"]),
        "folders: " + ", ".join(sorted(p.name for p in notes.iterdir() if p.is_dir())),
    )
    copy_of = markdown_files(notes)
    page = next(t for s, t in copy_of.items() if s.startswith(f"task-{number_of(shipped['ref'])}-"))
    bare = next(t for s, t in copy_of.items() if s.startswith(f"task-{number_of(quiet['ref'])}-"))
    proof(
        "M41",
        "the Markdown copy says where the work is, from the handoff note, in a section of the "
        "item's own file (repository, branch, commit, pull request and the run), and a file of "
        "an item that was never given any has no such section",
        "## Where the work is" in page
        and all(
            line in page
            for line in (
                f"- Repository: {WORK_REPOSITORY}",
                f"- Branch: {WORK_BRANCH}",
                f"- Commit: {NEWER_COMMIT}",
                f"- Pull request: {PULL_REQUEST}",
                f"- Run: {RUN_LABEL} ({CLIENT_NAME})",
            )
        )
        and "Where the work is" not in bare,
        f"in {shipped['ref']}'s file",
    )
    answered_page = next(
        t for s, t in copy_of.items() if s.startswith(f"task-{number_of(answered_task['ref'])}-")
    )
    open_page = next(
        t for s, t in copy_of.items() if s.startswith(f"task-{number_of(open_task['ref'])}-")
    )
    proof(
        "M45",
        "the Markdown copy lists the asks of a story or task under Questions as Q-numbers: what "
        "was asked and the choices, that the work waited, who asked, and the answer with who gave "
        "it and their comment, or that it is not answered yet",
        "## Questions" in answered_page
        and f"- Q-{answered_ask} (work waited): {ASK_QUESTION} Choices: Stripe, Adyen, Neither."
        in answered_page
        and f"Answer: Stripe, by {the_project().owner.name}," in answered_page
        and "Comment: Cheapest." in answered_page
        and f"- Q-{open_ask} (work waited): Is it still open?" in open_page
        and "Not answered yet." in open_page
        and "## Questions" not in bare,
        f"Q-{answered_ask} answered, Q-{open_ask} waiting",
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
        asked_restored = await asks_of_agents(client)
        held_restored = await held_and_proposed(client, gate_refs)
        last = original["events"][-1]["id"]
        new = await ok(client, "create_node", kind="epic", title="After the restore")
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
        message = await refused(client, "import_graph", document=bad)
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
        second = await refused(client, "import_graph", document=original)
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
    wanted = {
        "repository": WORK_REPOSITORY,
        "branch": WORK_BRANCH,
        "commit": NEWER_COMMIT,
        "pull_request": PULL_REQUEST,
        "run_label": RUN_LABEL,
        "client_name": CLIENT_NAME,
    }
    handoffs = [
        e for e in original["events"] if e["action"] == "handoff" and e["node"] == shipped["ref"]
    ]
    async with connect(URL, empty_project.key) as client:
        arrived = await ok(client, "get_node", node=shipped["ref"])
    proof(
        "M41",
        "where the work is goes through a backup and restore and through export and import into "
        "a fresh project: the handoff event of the item carries all six words in the file and in "
        "the new project, and there get_node says the same in `where` and in its last handoff",
        [said_about(e["after"]) for e in handoffs] == [wanted]
        and [
            said_about(e["after"])
            for e in after["events"]
            if e["action"] == "handoff" and e["node"] == shipped["ref"]
        ]
        == [wanted]
        and said_about(arrived["where"]) == said_about(arrived["last_handoff"]) == wanted,
        f"{shipped['ref']}: {WORK_BRANCH}, {RUN_LABEL} ({CLIENT_NAME})",
    )
    actions = {e["action"] for e in original["events"]}
    wide = next(n for n in after["notes"] if n["id"] == world["wide"]["id"])
    ended = next(n for n in after["notes"] if n["id"] == world["fact6"]["id"])
    superseded = next(n for n in after["notes"] if n["id"] == world["fact3"]["id"])
    proof(
        "M27",
        "a project's notes, confirmations, replacements and withdrawals, links and references go "
        "through export and import into a fresh project and come back as they were: the notes, "
        "their histories, and what the tools say of who mentions what",
        {
            "note_recorded",
            "note_confirmed",
            "note_superseded",
            "note_withdrawn",
            "note_linked",
        }
        <= actions
        and (wide["project_wide"], wide["confirmed_by"]) == (True, "Marie")
        and (ended["status"], ended["confirmed_by"]) == ("withdrawn", "Marie")
        and (superseded["status"], superseded["superseded_by_id"])
        == ("superseded", world["fact4"]["id"])
        and now_past == past
        and now_told["mentions"] == told["mentions"]
        and any(m["source"]["item_type"] == "note" for m in told["mentions"]["story"])
        and now_told["recall"] == told["recall"],
        f"{len(after['notes'])} notes, "
        f"{sum(len(h) for h in past.values())} history entries compared",
    )
    briefed_wide = {n["id"]: n for n in now_told["briefing"]["project_wide_notes"]}
    proof(
        "M27",
        "the agent briefing in the fresh project is the one of the project it came from, made of "
        "notes in three sections (in force, project-wide, past) and the definition of done, each "
        "note saying whether a person confirmed it",
        now_told["briefing"] == told["briefing"]
        and set(BRIEFING) <= set(now_told["keys"])
        and not GONE_FROM_BRIEFINGS & set(now_told["keys"])
        and briefed_wide[world["wide"]["id"]]["confirmed_by"] == "Marie"
        and [n["confirmed_by"] for n in now_told["briefing"]["notes"]] == ["Marie"],
        f"{len(now_told['keys'])} parts, none of the old ones",
    )
    async with connect(URL, empty_project.key) as client:
        imported = await asks_of_agents(client)
        waiting_there = [
            (item["kind"], item["node"])
            for group in (await ok(client, "needs_a_person"))["groups"]
            for item in group["items"]
        ]
        blocked_there = await ok(client, "get_node", node=open_task["ref"])
    answer_path = f"/board/api/projects/{empty_project.id}/tool/answer_ask"
    late = empty_project.owner.call("POST", answer_path, {"ask": open_ask, "answer": "Go ahead"})
    once_only = empty_project.owner.call("POST", answer_path, {"ask": open_ask, "answer": "Again"})
    async with connect(URL, empty_project.key) as client:
        freed_there = await ok(client, "get_node", node=open_task["ref"])

    def said_of(items: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        return [
            (a["id"], a["node"], a["question"], a["kind"], a["choices"], a["effect"],
             a["asked_by"], a["answered"], a["answer"], a["comment"], a["answered_by"])
            for a in items
        ]  # fmt: skip

    proof(
        "M45",
        "asks and their answers go through a backup and restore and through an export and import "
        "into a fresh project: list_asks says the same (the numbers, the questions, the answers "
        "and who gave them, to the second after the restore), the ask that was still open is "
        "still waiting there, as the work it blocked and as a row of what needs a person, and a "
        "person can still answer it there, once, which frees the work",
        len(asked_before) == 2
        and asked_restored == asked_before
        and said_of(imported) == said_of(asked_before)
        and [a["answered"] for a in imported] == [True, False]
        and imported[0]["answered_by"] == the_project().owner.name
        and blocked_there["status"] == "blocked"
        and ("ask", open_task["ref"]) in waiting_there
        and late.status == 200
        and late.body["answered_by"] == empty_project.owner.name
        and once_only.status == 400
        and "already answered" in once_only.body["message"]
        and freed_there["status"] == "ready",
        f"Q-{answered_ask} answered and Q-{open_ask} waiting, in every copy",
    )
    async with connect(URL, empty_project.key) as client:
        held_there = await held_and_proposed(client, gate_refs)
        needs_there = [
            (item["kind"], item["node"])
            for group in (await ok(client, "needs_a_person"))["groups"]
            for item in group["items"]
        ]
        waiting_plan = next(p for p in held_there["plans"] if p[2] == "proposed")
        waiting_move = next(m for m in held_there["moves"] if m[1] == "proposed")
        left_out_there = (await ok(client, "get_node", node=left_out_story))["parent"]
    plan_path = f"/board/api/projects/{empty_project.id}/tool"
    approved_there = empty_project.owner.call(
        "POST", f"{plan_path}/approve_plan", {"plan": waiting_plan[0], "approved": True}
    )
    moved_there = empty_project.owner.call(
        "POST", f"{plan_path}/decide_placement", {"placement": waiting_move[0], "accepted": True}
    )
    async with connect(URL, empty_project.key) as client:
        freed_plan = [
            (await ok(client, "get_node", node=ref))["held_by"] for ref in waiting_plan[6]
        ]
        moved_story = (await ok(client, "get_node", node=waiting_move[2][0]))["parent"]
    gates_of = {ref: held_before["gated"][ref] for ref in gate_refs}
    talked_there = next(p for p in held_there["plans"] if p[1].startswith("Talked about"))
    approved_talked = empty_project.owner.call(
        "POST",
        f"{plan_path}/approve_plan",
        {"plan": talked_there[0], "approved": True, "revision": 2},
    )
    async with connect(URL, empty_project.key) as client:
        at_there = held_there["talked"][talked_there[0]]
        now_free = [
            (await ok(client, "get_node", node=item["ref"]))["held_by"] for item in at_there["tree"]
        ]
        taken_out = [
            (await ok(client, "get_node", node=ref))["status"]
            for ref in at_there["revisions"][1][4]
        ]
    proof(
        "M47",
        "gates, drafted plans and plan templates go through a backup and restore and through an "
        "export and import into a fresh project: the task a timer holds and the task an approval "
        "holds are still held by the same gates, saying the same, the plan that waits is still "
        "held and still a row of what needs a person, the plan a person approved is still "
        "approved, the saved template is the same, and a person there can still approve the "
        "waiting plan, which frees its work",
        held_restored == held_before
        and held_there["gated"] == held_before["gated"]
        and held_there["plans"] == held_before["plans"]
        and held_there["templates"] == held_before["templates"]
        and all(by and by[0].startswith("gate:") for by, _ in gates_of.values())
        and [p[2] for p in held_there["plans"]] == ["proposed", "approved", "proposed"]
        and ("plan", waiting_plan[6][0]) in needs_there
        and ("ask", gate_refs[1]) in needs_there
        and approved_there.status == 200
        and freed_plan == [[]] * len(waiting_plan[6]),
        f"{len(held_there['plans'])} plans, {len(held_there['templates'])} template, 2 gates",
    )
    (taken_plan,) = held_before["talked"].values()
    proof(
        "M51",
        "a plan with a conversation goes through a backup and restore and through an export and "
        "import into a fresh project: it is still at revision 2 and waits for a person, with the "
        "same revisions (who made each, the agent's note, how many items, the one it took out), "
        "the same feedback with the words each comment was about, revision 1 reading as it was "
        "and what changed since it; the task that revision took out is still dropped; and a "
        "person there approves revision 2, which frees what is left and not the dropped task",
        held_restored["talked"] == held_before["talked"]
        and held_there["talked"] == held_before["talked"]
        and taken_plan["revision"] == 2
        and taken_plan["state"] == "proposed"
        and [r[0] for r in taken_plan["revisions"]] == [1, 2]
        and taken_plan["revisions"][1][2] == "Took out the provider call."
        and len(taken_plan["revisions"][1][4]) == 1
        and taken_plan["feedback"][0][3] == "One task too many."
        and taken_plan["feedback"][0][4][0][3] == "Call the provider"
        and len(taken_plan["first"]) == len(taken_plan["tree"]) + 1
        and approved_talked.status == 200
        and approved_talked.body["revision"] == 2
        and now_free == [[]] * len(at_there["tree"])
        and taken_out == ["dropped"],
        f"revision {taken_plan['revision']}, {len(taken_plan['feedback'])} feedback",
    )
    proof(
        "M43",
        "proposals to move work go through the same copies: the one that waits is still waiting "
        "(and a row of what needs a person), the one a person rejected is still rejected with "
        "the reason, and a person in the fresh project can still accept the waiting one, which "
        "moves the story as that person",
        held_restored["moves"] == held_before["moves"]
        and held_there["moves"] == held_before["moves"]
        and [m[1] for m in held_there["moves"]] == ["proposed", "rejected", "accepted"]
        and held_there["moves"][1][6] == "Leave it."
        and ("placement", waiting_move[2][0]) in needs_there
        and moved_there.status == 200
        and moved_story == waiting_move[3],
        f"{len(held_there['moves'])} proposals in every copy",
    )
    partial_move = next(m for m in held_before["moves"] if m[1] == "accepted")
    t1_ref = next(f for f in taken_plan["feedback"])[5][0]
    by_line = {b[0]: b for b in taken_plan["blame"]}
    proof(
        "M53",
        "approvals, the blame of every line and a move accepted in part go through a backup and "
        "restore and through an export and import into a fresh project: the feedback keeps the "
        "item the person approved, blame reads the same in every copy (which revision last "
        "changed each line, who made it, the agent's note, and what a person said that still "
        "stands, for revision 2 and for revision 1), the approved task reads approved and the "
        "task revision 2 took out reads removed; the move keeps what it moved and the story the "
        "person left out, who is named, which stays where it was in every copy",
        held_restored["talked"] == held_before["talked"]
        and held_there["talked"] == held_before["talked"]
        and taken_plan["feedback"][0][5] == [t1_ref]
        and by_line[t1_ref][5:8] == ("approved", 1, by_line[t1_ref][7])
        and by_line[t1_ref][1:3] == ("added", 1)
        and any(b[1] == "removed" and b[2] == 2 and b[5] == "none" for b in taken_plan["blame"])
        and all(
            b[5] == "asked" or b[5] == "approved"
            for b in taken_plan["first_blame"]
            if b[5] != "none"
        )
        and held_restored["moves"] == held_before["moves"]
        and held_there["moves"] == held_before["moves"]
        and len(partial_move[8]) == 1
        and partial_move[8][0]["ref"] == left_out_story
        and partial_move[8][0]["why"].endswith(" left it out")
        and len(partial_move[7]) == 1
        and left_out_story not in partial_move[7]
        and left_out_there == left_out_parent,
        f"{len(taken_plan['blame'])} lines, {len(partial_move[7])} moved, 1 left out",
    )
    timed_page = next(
        t for s, t in copy_of.items() if s.startswith(f"task-{number_of(timed['ref'])}-")
    )
    found_in_page = next(
        t for s, t in copy_of.items() if s.startswith(f"task-{number_of(approving['ref'])}-")
    )
    proof(
        "M44",
        "the link that says where work was found goes through them too, in the same direction, "
        "and the Markdown copy names it in words at both ends (discovered from, led to)",
        held_restored["found"] == held_before["found"] == held_there["found"]
        and held_before["found"] == [(gate_refs[0], gate_refs[1])]
        and "discovered from" in timed_page
        and "led to" in found_in_page,
        f"{gate_refs[0]} was found while doing {gate_refs[1]}",
    )
    shutil.rmtree(work, ignore_errors=True)
