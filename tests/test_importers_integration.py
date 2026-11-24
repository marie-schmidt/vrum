"""M42: the importers and the agent setup script, run as a person would, against the running
containers.

Each importer reads a small hand-made file (tests/importer_samples.py) and makes a plan in an empty
project: the tools then read back the hierarchy, the links, the statuses and the criteria. A file
that does not fit changes nothing and says every problem. The setup script's output works: the
agent key it asks for is accepted by the server (it is never written to a file)."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from tests.helpers import REPO_ROOT, connect, ok
from tests.importer_samples import BEADS, GITHUB, SPEC_KIT
from tests.world import Project, make_key, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def run_script(
    name: str, key: str, *arguments: str, folder: Path | None = None
) -> subprocess.CompletedProcess[str]:
    environment = {k: v for k, v in os.environ.items() if k != "VRUM_AGENT_KEY"}
    environment["VRUM_AGENT_KEY"] = key
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / name), *arguments],
        cwd=folder or REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def stored(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


async def read_back(client: Client) -> dict[str, Any]:
    """What the project holds: each node's title with its kind, parent title and status, and every
    link with the titles at both ends."""
    nodes = (await ok(client, "list_nodes", limit=200, detail=True))["items"]
    title = {n["ref"]: n["title"] for n in nodes}
    links = {}
    for ref in title:
        for link in (await ok(client, "list_links", node=ref, limit=200))["items"]:
            links[(link["source"], link["link_type"], link["target"])] = link
    return {
        "nodes": {
            n["title"]: (n["kind"], title.get(n["parent"]), n["status"], n["priority"])
            for n in nodes
        },
        "links": {(title[s], kind, title[t]) for (s, kind, t) in links},
        "bodies": {n["title"]: n["body"] for n in nodes},
    }


def an_empty_project() -> tuple[Project, str]:
    project = make_project(register())
    return project, make_key(project, "importer-agent")


async def test_a_beads_export_is_read_back_as_the_same_hierarchy_and_links(tmp_path: Path) -> None:
    project, key = an_empty_project()
    path = stored(tmp_path, "beads.jsonl", BEADS)
    refused = run_script("import_beads.py", key, project.mcp_url, path)
    assert refused.returncode == 1 and "bd-6 is a gate" in refused.stderr
    async with connect(project.mcp_url, project.key) as client:
        assert (await ok(client, "list_nodes"))["items"] == []

    done = run_script("import_beads.py", key, project.mcp_url, path, "--skip-not-work")
    assert done.returncode == 0, done.stderr
    assert "Created 2 epics, 2 stories, 2 tasks, 3 links, 2 acceptance criteria from Beads." in (
        done.stdout
    )
    async with connect(project.mcp_url, project.key) as client:
        found = await read_back(client)
        assert {t: (v[0], v[1], v[3]) for t, v in found["nodes"].items()} == {
            "Inbox": ("epic", None, "normal"),
            "Checkout": ("epic", None, "high"),
            "Card payments": ("story", "Checkout", "high"),
            "Fix the rounding bug": ("story", "Inbox", "low"),
            "Build the card form": ("task", "Card payments", "normal"),
            "Call the provider": ("task", "Card payments", "urgent"),
        }
        assert found["nodes"]["Card payments"][2] == "in_progress"
        assert found["nodes"]["Build the card form"][2] == "done"
        assert found["links"] == {
            ("Call the provider", "needs_first", "Build the card form"),
            ("Fix the rounding bug", "discovered_from", "Build the card form"),
            ("Fix the rounding bug", "relates_to", "Call the provider"),
        }
        assert "Imported from Beads: bd-4" in found["bodies"]["Call the provider"]
        # The criteria are acceptance criteria of the story.
        rules = (await ok(client, "list_rules", limit=50))["items"]
        assert sorted(r["title"] for r in rules) == [
            "A card is charged once",
            "A failed charge shows a reason",
        ]
        # The link that says where work was found never makes it wait: it is free to take, and
        # its briefing says where it came from.
        bug = next(
            n
            for n in (await ok(client, "list_nodes", limit=50))["items"]
            if n["title"] == "Fix the rounding bug"
        )
        assert bug["status"] == "ready" and bug["waiting_on"] == []
        taken = await ok(client, "next_work", within_node=bug["ref"])
        assert [b["title"] for b in taken["context"]["discovered_from"]] == ["Build the card form"]


async def test_a_github_issue_list_is_read_back_as_milestones_stories_and_tracked_tasks(
    tmp_path: Path,
) -> None:
    project, key = an_empty_project()
    path = stored(tmp_path, "issues.json", GITHUB)
    dry = run_script("import_github.py", key, project.mcp_url, path, "--dry-run")
    assert dry.returncode == 0 and "It would create 2 epics, 2 stories, 2 tasks" in dry.stdout
    async with connect(project.mcp_url, project.key) as client:
        assert (await ok(client, "list_nodes"))["items"] == []
    done = run_script("import_github.py", key, project.mcp_url, path)
    assert done.returncode == 0, done.stderr
    assert "1 pull requests were left out" in done.stdout
    async with connect(project.mcp_url, project.key) as client:
        found = await read_back(client)
    assert found["nodes"]["v1"][:2] == ("epic", None)
    assert found["nodes"]["Tracking: sign in"][:2] == ("story", "v1")
    assert found["nodes"]["Tracking: sign in"][3] == "high"
    assert found["nodes"]["Login form"][:3] == ("task", "Tracking: sign in", "done")
    assert found["nodes"]["Password reset"][:3] == ("task", "Tracking: sign in", "dropped")
    assert found["nodes"]["Dark mode"][:2] == ("story", "Inbox")
    assert "https://github.com/acme/app/issues/2" in found["bodies"]["Login form"]
    assert found["links"] == set()


async def test_a_spec_kit_tasks_file_is_read_back_with_its_phases_and_needs(
    tmp_path: Path,
) -> None:
    project, key = an_empty_project()
    path = stored(tmp_path, "tasks.md", SPEC_KIT)
    done = run_script("import_speckit.py", key, project.mcp_url, path)
    assert done.returncode == 0, done.stderr
    async with connect(project.mcp_url, project.key) as client:
        found = await read_back(client)
    assert found["nodes"]["Photo albums"][:2] == ("epic", None)
    assert found["nodes"]["Phase 1: Setup"][:2] == ("story", "Photo albums")
    assert found["nodes"]["Create project structure per the plan"][:3] == (
        "task",
        "Phase 1: Setup",
        "done",
    )
    assert found["nodes"]["Build the album list"][1].startswith("Phase 3")
    assert found["links"] == {
        ("Set up the database schema", "needs_first", "Create project structure per the plan"),
        ("Build the album list", "needs_first", "Set up the database schema"),
        (
            "Build the album list",
            "needs_first",
            "Create the Album model in src/models/album.py",
        ),
    }
    # Waiting is real: the album list cannot be started before what it needs is done.
    assert found["nodes"]["Build the album list"][2] == "new"


async def test_a_file_that_does_not_fit_changes_nothing_and_says_every_problem(
    tmp_path: Path,
) -> None:
    project, key = an_empty_project()
    bad = stored(
        tmp_path,
        "bad.jsonl",
        '{"id":"a","title":"A","status":"someday"}\nnot json\n'
        '{"id":"c","title":"C","parent":"zzz"}\n',
    )
    result = run_script("import_beads.py", key, project.mcp_url, bad)
    assert result.returncode == 1
    assert "has 3 problems" in result.stderr
    for line in ("1. ", "2. ", "3. "):
        assert line in result.stderr
    async with connect(project.mcp_url, project.key) as client:
        assert (await ok(client, "list_nodes"))["items"] == []
    # A plan the server itself refuses (a circle of waiting) is refused whole too.
    circle = stored(
        tmp_path,
        "circle.jsonl",
        '{"id":"a","title":"A","issue_type":"epic"}\n'
        '{"id":"b","title":"B","parent":"a","dependencies":'
        '[{"issue_id":"b","depends_on_id":"c","type":"blocks"}]}\n'
        '{"id":"c","title":"C","parent":"a","dependencies":'
        '[{"issue_id":"c","depends_on_id":"b","type":"blocks"}]}\n',
    )
    refused = run_script("import_beads.py", key, project.mcp_url, circle)
    assert refused.returncode == 1 and "Nothing was created" in refused.stderr
    async with connect(project.mcp_url, project.key) as client:
        assert (await ok(client, "list_nodes"))["items"] == []


async def test_a_status_the_project_will_not_take_is_reported_and_the_rest_is_made(
    tmp_path: Path,
) -> None:
    project, key = an_empty_project()
    # With a definition of done that has a check, an agent may not set done: the closed issue is
    # created as not started and the importer says so.
    project.owner.call(
        "POST",
        f"/board/api/projects/{project.id}/tool/set_definition_of_done",
        {"body": "- Tests pass\n", "expected_version": 1},
    )
    done = run_script(
        "import_beads.py",
        key,
        project.mcp_url,
        stored(tmp_path, "b.jsonl", BEADS),
        "--skip-not-work",
    )
    assert done.returncode == 0, done.stderr
    assert "1 status could not be set" in done.stdout
    async with connect(project.mcp_url, project.key) as client:
        found = await read_back(client)
    assert found["nodes"]["Build the card form"][2] != "done"
    assert found["nodes"]["Card payments"][2] == "in_progress"


async def test_the_setup_scripts_check_option_connects_with_the_key_and_lists_the_tools(
    tmp_path: Path,
) -> None:
    project, _ = an_empty_project()
    standard = make_key(project, "setup-agent")
    done = run_script(
        "setup_agent.py", standard, "claude", project.mcp_url, "--dir", str(tmp_path), "--check"
    )
    assert done.returncode == 0, done.stderr
    assert "is accepted and offered" in done.stdout and "next_work" in done.stdout
    written = (tmp_path / ".mcp.json").read_text(encoding="utf-8")
    assert standard not in written and standard not in done.stdout.split("offered")[0]
    assert not any(
        standard in p.read_text(encoding="utf-8") for p in tmp_path.rglob("*") if p.is_file()
    )
    wrong = run_script(
        "setup_agent.py",
        "vrk_not-a-key",
        "claude",
        project.mcp_url,
        "--dir",
        str(tmp_path),
        "--check",
    )
    assert wrong.returncode != 0
