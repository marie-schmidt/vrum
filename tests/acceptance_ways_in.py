"""Chapter of the acceptance scenario for the ways in (M42) and the view for hosts that draw MCP
Apps (M46), told from the outside: the three importers and the setup script are run as a person
would run them, from this checkout, against the running containers, and a real MCP client reads
the view and the plain words that come with it. Each part works in a project of its own."""

import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from mcp import Client

from tests.acceptance_scenario import chapter, clip, proof
from tests.helpers import REPO_ROOT, connect, ok, refused
from tests.importer_samples import BEADS, GITHUB, SPEC_KIT
from tests.world import Project, make_limited_key, make_project, register

VIEW = "ui://vrum/needs-a-person.html"
START = "<!-- vrum-guide:start -->"
END = "<!-- vrum-guide:end -->"
BAD_BEADS = (
    '{"id":"a","title":"A","status":"someday"}\nnot json\n{"id":"c","title":"C","parent":"zzz"}\n'
)
# Two issues that wait for each other: a plan the server itself refuses.
CIRCLE_BEADS = (
    '{"id":"a","title":"A","issue_type":"epic"}\n'
    '{"id":"b","title":"B","parent":"a","dependencies":'
    '[{"issue_id":"b","depends_on_id":"c","type":"blocks"}]}\n'
    '{"id":"c","title":"C","parent":"a","dependencies":'
    '[{"issue_id":"c","depends_on_id":"b","type":"blocks"}]}\n'
)
QUESTION = "Which payment provider do we use?"
CHOICES = ["Stripe", "Adyen"]


def script(name: str, key: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """A script of this checkout, run as a person runs it: from the repository, with the agent key
    in the environment variable it reads, as the stack's own settings are."""
    environment = {k: v for k, v in os.environ.items() if k != "VRUM_AGENT_KEY"}
    environment["VRUM_AGENT_KEY"] = key
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / name), *arguments],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def words(refusal: str) -> str:
    """A refusal as the server worded it, without the client's lead."""
    return refusal.split(": ", 1)[1] if refusal.startswith("Error executing tool") else refusal


async def read_back(client: Client) -> dict[str, Any]:
    """What a project holds, by titles: each item's kind, parent, status and priority, every link
    with the titles at both ends, every acceptance criterion with its story, and the details."""
    nodes = (await ok(client, "list_nodes", limit=200, detail=True))["items"]
    title = {n["ref"]: n["title"] for n in nodes}
    found = {}
    for ref in title:
        for link in (await ok(client, "list_links", node=ref, limit=200))["items"]:
            found[(link["source"], link["link_type"], link["target"])] = link
    rules = (await ok(client, "list_rules", limit=200))["items"]
    return {
        "nodes": {
            n["title"]: (n["kind"], title.get(n["parent"]), n["status"], n["priority"])
            for n in nodes
        },
        "links": {(title[s], kind, title[t]) for (s, kind, t) in found},
        "rules": {(r["title"], title.get(r["node"])) for r in rules},
        "bodies": {n["title"]: n["body"] for n in nodes},
        "by_title": {n["title"]: n for n in nodes},
    }


async def empty(client: Client) -> bool:
    return bool((await ok(client, "list_nodes", limit=5))["items"] == [])


def a_project() -> tuple[Project, str]:
    """A new project and the key an importer is run with: an agent key allowed the two tools it
    uses (create_plan, and update_nodes for the statuses) and nothing else."""
    project = make_project(register())
    return project, make_limited_key(project, "importer-agent", ["create_plan", "update_nodes"])


async def importers(folder: Path) -> None:
    """The three importers: each loads a small hand-made file into an empty project and the tools
    read back the same hierarchy and links, a dry run changes nothing, a file that does not fit
    changes nothing and says every problem."""
    beads, issues, tasks = (
        folder / "beads.jsonl",
        folder / "issues.json",
        folder / "tasks.md",
    )
    beads.write_text(BEADS, encoding="utf-8")
    issues.write_text(GITHUB, encoding="utf-8")
    tasks.write_text(SPEC_KIT, encoding="utf-8")

    project, key = a_project()
    async with connect(project.mcp_url, project.key) as client:
        # -- a Beads export: refused whole while it holds work-less items, dry-run, then loaded
        refused_file = script("import_beads.py", key, project.mcp_url, str(beads))
        dry = script(
            "import_beads.py", key, project.mcp_url, str(beads), "--skip-not-work", "--dry-run"
        )
        still_empty = await empty(client)
        proof(
            "M42",
            "a Beads export that holds something that is not work (a gate) is refused whole, "
            "naming it, and a dry run of the same file with --skip-not-work says what it would "
            "create and creates nothing",
            refused_file.returncode == 1
            and "bd-6 is a gate" in refused_file.stderr
            and dry.returncode == 0
            and "It would create 2 epics, 2 stories, 2 tasks" in dry.stdout
            and still_empty,
            clip(dry.stdout.strip()),
        )
        done = script("import_beads.py", key, project.mcp_url, str(beads), "--skip-not-work")
        found = await read_back(client)
        shape = {t: (v[0], v[1], v[3]) for t, v in found["nodes"].items()}
        proof(
            "M42",
            "run for real, with a key allowed only create_plan and update_nodes, the Beads "
            "importer makes the file's "
            "hierarchy: an epic is an epic, what is under it a story, what is under that a task, "
            "work with no epic goes under an Inbox epic as stories, priorities carry over, "
            "statuses carry over, and the old id is kept in the details",
            done.returncode == 0
            and "Created 2 epics, 2 stories, 2 tasks, 3 links, 2 acceptance criteria from Beads."
            in done.stdout
            and shape
            == {
                "Inbox": ("epic", None, "normal"),
                "Checkout": ("epic", None, "high"),
                "Card payments": ("story", "Checkout", "high"),
                "Fix the rounding bug": ("story", "Inbox", "low"),
                "Build the card form": ("task", "Card payments", "normal"),
                "Call the provider": ("task", "Card payments", "urgent"),
            }
            and found["nodes"]["Card payments"][2] == "in_progress"
            and found["nodes"]["Build the card form"][2] == "done"
            and "Imported from Beads: bd-4" in found["bodies"]["Call the provider"],
            clip(done.stdout.splitlines()[0]),
        )
        bug = found["by_title"]["Fix the rounding bug"]
        taken = await ok(client, "next_work", within_node=bug["ref"])
        proof(
            "M42",
            "its dependencies become links the tools read back (blocks as needs_first, related "
            "as relates_to, discovered-from as discovered_from), its acceptance criteria are "
            "placed on the story they belong to, and the item that was found while working on "
            "another is free to take, with a briefing that says where it was found",
            found["links"]
            == {
                ("Call the provider", "needs_first", "Build the card form"),
                ("Fix the rounding bug", "discovered_from", "Build the card form"),
                ("Fix the rounding bug", "relates_to", "Call the provider"),
            }
            and found["rules"]
            == {
                ("A card is charged once", "Card payments"),
                ("A failed charge shows a reason", "Card payments"),
            }
            and bug["status"] == "ready"
            and bug["waiting_on"] == []
            and [b["title"] for b in taken["context"]["discovered_from"]]
            == ["Build the card form"],
            "3 links and 2 criteria read back",
        )

    project, key = a_project()
    async with connect(project.mcp_url, project.key) as client:
        dry = script("import_github.py", key, project.mcp_url, str(issues), "--dry-run")
        still_empty = await empty(client)
        done = script("import_github.py", key, project.mcp_url, str(issues))
        found = await read_back(client)
        nodes = found["nodes"]
        proof(
            "M42",
            "a GitHub issue list is read the same way: a milestone is an epic, an issue that "
            "tracks others with a checklist is a story and the issues it lists are its tasks "
            "(closed as completed is done, closed as not planned is dropped), a priority label "
            "carries over, an issue with no milestone goes under Inbox, a pull request is left "
            "out and said so, the issue's address is kept in the details, and a dry run first "
            "creates nothing",
            dry.returncode == 0
            and "It would create 2 epics, 2 stories, 2 tasks" in dry.stdout
            and still_empty
            and done.returncode == 0
            and "1 pull requests were left out" in done.stdout
            and nodes["v1"][:2] == ("epic", None)
            and nodes["Tracking: sign in"][:2] == ("story", "v1")
            and nodes["Tracking: sign in"][3] == "high"
            and nodes["Login form"][:3] == ("task", "Tracking: sign in", "done")
            and nodes["Password reset"][:3] == ("task", "Tracking: sign in", "dropped")
            and nodes["Dark mode"][:2] == ("story", "Inbox")
            and "https://github.com/acme/app/issues/2" in found["bodies"]["Login form"]
            and found["links"] == set(),
            clip(done.stdout.splitlines()[0]),
        )

    project, key = a_project()
    async with connect(project.mcp_url, project.key) as client:
        done = script("import_speckit.py", key, project.mcp_url, str(tasks))
        found = await read_back(client)
        nodes = found["nodes"]
        proof(
            "M42",
            "a Spec Kit tasks.md is read the same way: the feature is the epic, each phase a "
            "story, each checked or unchecked task a task, a (depends on T003) note a needs_first "
            "link, a task in an HTML comment is not a task, and what has to wait is not ready",
            done.returncode == 0
            and nodes["Photo albums"][:2] == ("epic", None)
            and nodes["Phase 1: Setup"][:2] == ("story", "Photo albums")
            and nodes["Create project structure per the plan"][:3]
            == ("task", "Phase 1: Setup", "done")
            and "A task in a comment is not a task" not in nodes
            and found["links"]
            == {
                (
                    "Set up the database schema",
                    "needs_first",
                    "Create project structure per the plan",
                ),
                ("Build the album list", "needs_first", "Set up the database schema"),
                (
                    "Build the album list",
                    "needs_first",
                    "Create the Album model in src/models/album.py",
                ),
            }
            and nodes["Build the album list"][2] == "new",
            clip(done.stdout.splitlines()[0]),
        )

    project, key = a_project()
    bad = folder / "bad.jsonl"
    bad.write_text(BAD_BEADS, encoding="utf-8")
    circle = folder / "circle.jsonl"
    circle.write_text(CIRCLE_BEADS, encoding="utf-8")
    async with connect(project.mcp_url, project.key) as client:
        no_fit = script("import_beads.py", key, project.mcp_url, str(bad))
        server_no = script("import_beads.py", key, project.mcp_url, str(circle))
        proof(
            "M42",
            "a file that does not fit changes nothing and says every problem at once (a status "
            "Vrum does not have, a line that is not JSON, a parent that is not in the file), and "
            "a plan the server itself refuses (two items that wait for each other) is refused "
            "whole too, with nothing created",
            no_fit.returncode == 1
            and "has 3 problems" in no_fit.stderr
            and all(f"{number}. " in no_fit.stderr for number in (1, 2, 3))
            and server_no.returncode == 1
            and "Nothing was created" in server_no.stderr
            and await empty(client),
            clip(no_fit.stderr.splitlines()[0]),
        )


def guide_of(text: str) -> str:
    """What is between the markers of a guide file."""
    return text.partition(START)[2].partition(END)[0]


def twice(text: str, piece: str) -> bool:
    return text.count(piece) == 1


async def setup(folder: Path) -> None:
    """The setup script: for each client the configuration is valid, the key is written nowhere,
    the guide is the server's own, running it again changes nothing and a file that is not valid is
    left alone."""
    project = make_project(register())
    key = make_limited_key(project, "coding-agent", None)
    url = project.mcp_url
    files: dict[str, tuple[str, str]] = {
        "claude": (".mcp.json", "CLAUDE.md"),
        "codex": (".codex/config.toml", "AGENTS.md"),
        "cursor": (".cursor/mcp.json", "AGENTS.md"),
        "copilot": (".mcp.json", ".github/copilot-instructions.md"),
    }
    async with connect(url, key) as client:
        instructions = (client.instructions or "").strip()
        tools = sorted(tool.name for tool in (await client.list_tools()).tools)
    valid: list[str] = []
    for name, (config, guide) in files.items():
        where = folder / name
        where.mkdir()
        made = script("setup_agent.py", key, name, url, "--dir", str(where))
        text = (where / config).read_text(encoding="utf-8")
        reads_the_environment = "VRUM_AGENT_KEY" in text
        if name == "codex":
            table = tomllib.loads(text)["mcp_servers"]["vrum"]
            right = table == {"url": url, "bearer_token_env_var": "VRUM_AGENT_KEY"}
        else:
            entry = json.loads(text)["mcpServers"]["vrum"]
            right = entry["url"] == url and "VRUM_AGENT_KEY" in entry["headers"]["Authorization"]
        everything = "".join(p.read_text(encoding="utf-8") for p in where.rglob("*") if p.is_file())
        guide_text = (where / guide).read_text(encoding="utf-8")
        if (
            made.returncode == 0
            and right
            and reads_the_environment
            and key not in everything + made.stdout + made.stderr
            and guide_of(guide_text).strip().endswith(instructions)
        ):
            valid.append(name)
    proof(
        "M42",
        "the setup script writes a client's MCP configuration and short guide for Claude Code, "
        "Codex, Cursor and GitHub Copilot: each configuration parses (JSON, or TOML for Codex), "
        "names the server's address and reads the key from the environment variable "
        "VRUM_AGENT_KEY, the agent key is in no file it wrote and not in what it printed, and "
        "the guide is the server's own instructions word for word",
        valid == list(files),
        ", ".join(valid),
    )

    again = folder / "again"
    (again / ".github").mkdir(parents=True)
    (again / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"url": "http://example.invalid/mcp"}}}), "utf-8"
    )
    (again / "CLAUDE.md").write_text("# My project\n\nKeep this line.\n", encoding="utf-8")
    script("setup_agent.py", key, "claude", url, "--dir", str(again))
    first = {p.name: p.read_text(encoding="utf-8") for p in again.iterdir() if p.is_file()}
    second_run = script("setup_agent.py", key, "claude", url, "--dir", str(again))
    second = {p.name: p.read_text(encoding="utf-8") for p in again.iterdir() if p.is_file()}
    servers = json.loads(second[".mcp.json"])["mcpServers"]
    proof(
        "M42",
        "run twice it does not duplicate: the guide is one block between its two markers and the "
        "files are byte for byte what the first run made, what was already in the folder (an "
        "other server in the configuration, a line of the person's own in the guide) is kept, and "
        "nothing it wrote holds the key",
        second_run.returncode == 0
        and first == second
        and twice(second["CLAUDE.md"], START)
        and twice(second["CLAUDE.md"], END)
        and second["CLAUDE.md"].startswith("# My project\n\nKeep this line.\n")
        and set(servers) == {"other", "vrum"}
        and key not in "".join(second.values()),
        "two runs, one block",
    )

    broken = folder / "broken"
    broken.mkdir()
    (broken / ".mcp.json").write_text("{ not json", encoding="utf-8")
    refusal = script("setup_agent.py", key, "claude", url, "--dir", str(broken))
    wrong = script(
        "setup_agent.py", "vrk_not-a-key", "claude", url, "--dir", str(folder / "again"), "--check"
    )
    checked = script(
        "setup_agent.py", key, "cursor", url, "--dir", str(folder / "cursor"), "--check"
    )
    listed = (
        checked.stdout.split("tools: ", 1)[1].rstrip(".\n").split(", ")
        if "tools: " in checked.stdout
        else []
    )
    proof(
        "M42",
        "a configuration file that is not valid JSON is left alone and refused in words, --check "
        "connects with the key and lists exactly the tools the key is offered, and a key the "
        "server does not know is refused",
        refusal.returncode != 0
        and "not valid JSON" in refusal.stderr
        and (broken / ".mcp.json").read_text(encoding="utf-8") == "{ not json"
        and checked.returncode == 0
        and "is accepted and offered" in checked.stdout
        and sorted(listed) == tools
        and wrong.returncode != 0,
        f"{len(tools)} tools listed",
    )


async def the_view() -> None:
    """M46: a key that may use needs_a_person is offered the tool with the view it names, the page
    is served as it is in the repository, it holds no address, the same call answers in plain words
    and as data, and what the view shows is what the board's own call reads."""
    owner = register()
    project = make_project(owner)
    key = make_limited_key(project, "chat-host", ["needs_a_person"])
    plain = make_limited_key(project, "ordinary", None)
    async with connect(project.mcp_url, project.key) as worker:
        _, _, task = await _a_tree(worker)
        await ok(worker, "claim_node", node=task["ref"])
        await ok(
            worker, "finish_work", node=task["ref"], outcome="blocked",
            note="I need a decision before I can go on.",
            ask={"question": QUESTION, "choices": CHOICES},
        )  # fmt: skip
        number = (await ok(worker, "get_node", node=task["ref"]))["asks"][0]["id"]
    async with connect(project.mcp_url, key) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        ui = (tools["needs_a_person"].meta or {}).get("ui", {})
        annotations = tools["needs_a_person"].annotations
        resources = [str(one.uri) for one in (await client.list_resources()).resources]
        [page] = (await client.read_resource(VIEW)).contents
        html = getattr(page, "text", "")
        served = (REPO_ROOT / "src" / "vrum" / "needs_view.html").read_text(encoding="utf-8")
        proof(
            "M46",
            "a real MCP client with a key that may use needs_a_person is offered that tool and "
            "it names a view (the ui:// address, for the model and the app), the view is listed as "
            "a resource and read as text/html;profile=mcp-app, it is the very page of the "
            "repository, and it holds no address, loads no script and puts nothing on the page "
            "as HTML",
            list(tools) == ["needs_a_person"]
            and ui == {"resourceUri": VIEW, "visibility": ["model", "app"]}
            and annotations is not None
            and annotations.read_only_hint is True
            and VIEW in resources
            and page.mime_type == "text/html;profile=mcp-app"
            and html == served
            and html.startswith("<!DOCTYPE html>")
            and "http://" not in html
            and "https://" not in html
            and "innerHTML" not in html
            and "<script src" not in html,
            f"{len(html)} characters, no address in it",
        )
        result = await client.call_tool("needs_a_person", {})
        text = "\n".join(b.text for b in result.content if hasattr(b, "text"))
        data = result.structured_content or {}
        [group] = data["groups"]
        [row] = group["items"]
        as_person = owner.call("POST", f"/board/api/projects/{project.id}/tool/needs_a_person", {})
        proof(
            "M46",
            "the same call answers a host that does not draw views in plain words (how many things "
            "wait, which question, who answers where) and as the structured list with the "
            "board's address from the server's own setting, and it is the list the board's own "
            "call reads for the person",
            not result.is_error
            and "1 thing is waiting for a person." in text
            and f"Q-{number}" in text
            and text.endswith(f"/board/p/{project.id}#/needs, under their own name.")
            and data["total"] == 1
            and data["board_address"].endswith(f"/board/p/{project.id}")
            and (row["kind"], row["node"], row["ask"]["id"]) == ("ask", task["ref"], number)
            and as_person.status == 200
            and as_person.body["total"] == data["total"]
            and as_person.body["board_address"] == data["board_address"],
            clip(text.splitlines()[0]),
        )
        said = [
            words(await refused(client, "answer_ask", ask=number, answer="Stripe")),
            words(await refused(client, "approve_plan", plan=1, approved=True)),
            words(await refused(client, "decide_placement", placement=1, accepted=True)),
        ]
    async with connect(project.mcp_url, plain) as ordinary:
        offered = {tool.name for tool in (await ordinary.list_tools()).tools}
        not_offered = words(await refused(ordinary, "needs_a_person"))
    async with connect(project.mcp_url, project.key) as worker:
        waiting = await ok(worker, "list_asks", status="open")
    proof(
        "M46",
        "the key that holds the view cannot answer for a person (answering a question, "
        "approving a plan and accepting a proposal are refused), nothing was answered, and a "
        "standard agent key is not offered the tool, so not the view either",
        all("not allowed to use" in message for message in said)
        and waiting["total"] == 1
        and "needs_a_person" not in offered
        and "needs_a_person" in not_offered,
        f"{len(offered)} tools offered to the standard key",
    )


async def _a_tree(client: Client) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    epic = await ok(client, "create_node", kind="epic", title="Checkout")
    story = await ok(client, "create_node", kind="story", title="Pay", parent=epic["ref"])
    task = await ok(
        client, "create_node", kind="task", title="Choose a provider", parent=story["ref"]
    )
    return epic, story, task


async def ways_in() -> None:
    chapter(
        "Ways in: importers, the setup script and the view for hosts that draw MCP Apps (M42, M46)"
    )
    with tempfile.TemporaryDirectory() as folder:
        await importers(Path(folder))
        await setup(Path(folder))
    await the_view()
