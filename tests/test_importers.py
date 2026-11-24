"""The importers (M42) with no server: a Beads export, a GitHub issue list and a Spec Kit tasks.md
become one plan, a file that does not fit is refused with every problem listed, and the agent setup
script writes valid configuration that holds no key."""

import importlib.util
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from tests.importer_samples import BEADS, GITHUB, SPEC_KIT
from vrum.importers import ImportProblems, from_beads, from_github_issues, from_spec_kit

SCRIPTS = Path(__file__).parent.parent / "scripts"


def titles(imported: Any, kind: str) -> list[str]:
    return [item["title"] for item in imported.items if item["kind"] == kind]


def under(imported: Any) -> dict[str, str]:
    """Each item's title mapped to its parent's."""
    by_key = {item["key"]: item["title"] for item in imported.items}
    return {item["title"]: by_key[item["parent"]] for item in imported.items if item.get("parent")}


def linked(imported: Any) -> set[tuple[str, str, str]]:
    by_key = {item["key"]: item["title"] for item in imported.items}
    return {
        (by_key[link["source"]], link["link_type"], by_key[link["target"]])
        for link in imported.links
    }


def test_a_beads_export_becomes_epics_stories_tasks_links_and_criteria() -> None:
    imported = from_beads(BEADS, skip_not_work=True)
    assert titles(imported, "epic") == ["Inbox", "Checkout"]
    assert titles(imported, "story") == ["Card payments", "Fix the rounding bug"]
    assert titles(imported, "task") == ["Build the card form", "Call the provider"]
    placed = under(imported)
    assert placed["Card payments"] == "Checkout" and placed["Fix the rounding bug"] == "Inbox"
    assert placed["Build the card form"] == "Card payments"
    assert "Checkout" not in placed  # an epic has no parent
    assert linked(imported) == {
        ("Call the provider", "needs_first", "Build the card form"),
        ("Fix the rounding bug", "discovered_from", "Build the card form"),
        ("Fix the rounding bug", "relates_to", "Call the provider"),
    }
    by_title = {item["title"]: item for item in imported.items}
    assert by_title["Call the provider"]["priority"] == "urgent"
    assert by_title["Fix the rounding bug"]["priority"] == "low"
    assert "Imported from Beads: bd-3" in by_title["Build the card form"]["body"]
    assert (
        "Let people pay." in by_title["Checkout"]["body"]
        and "payments" in by_title["Card payments"]["body"]
    )
    statuses = {by_key["title"]: imported.statuses.get(by_key["key"]) for by_key in imported.items}
    assert statuses["Card payments"] == "in_progress" and statuses["Build the card form"] == "done"
    assert statuses["Call the provider"] is None
    assert [r["title"] for r in imported.rules] == [
        "A card is charged once",
        "A failed charge shows a reason",
    ]
    assert imported.left_out and "1 issues that are not work" in imported.left_out[0]
    keys = [item["key"] for item in imported.items]
    assert len(set(keys)) == len(keys) and not any(":" in key for key in keys)


def test_a_beads_gate_is_a_problem_unless_it_is_skipped() -> None:
    with pytest.raises(ImportProblems) as refused:
        from_beads(BEADS)
    assert refused.value.problems == [
        "bd-6 is a gate, not work. Run again with --skip-not-work to leave those out."
    ]


@pytest.mark.parametrize(
    ("lines", "wanted"),
    [
        (["not json"], "Line 1 is not JSON."),
        (['{"title": "no id"}'], "Line 1 is not an issue with an id."),
        (
            ['{"id":"a","title":"A","status":"someday"}'],
            "a has the status 'someday', which has no Vrum equivalent.",
        ),
        (
            ['{"id":"a","title":"A","priority":9}'],
            "a has the priority 9; Beads uses 0 to 4.",
        ),
        (
            ['{"id":"a","title":"A","parent":"zzz"}'],
            "a is under zzz, which is not in the file.",
        ),
        (
            [
                '{"id":"a","title":"A","parent":"b"}',
                '{"id":"b","title":"B","parent":"a"}',
            ],
            "is its own ancestor",
        ),
        (
            [
                '{"id":"e","title":"E","issue_type":"epic"}',
                '{"id":"s","title":"S","parent":"e"}',
                '{"id":"t","title":"T","parent":"s"}',
                '{"id":"u","title":"U","parent":"t"}',
            ],
            "u is under t, which is itself a task",
        ),
        (
            [
                '{"id":"e","title":"E","issue_type":"epic"}',
                '{"id":"f","title":"F","issue_type":"epic","parent":"e"}',
            ],
            "f is an epic under e; an epic cannot be inside anything.",
        ),
        (
            [
                '{"id":"a","title":"A","dependencies":[{"issue_id":"a","depends_on_id":"b",'
                '"type":"tracks"}]}',
                '{"id":"b","title":"B"}',
            ],
            "a has a 'tracks' dependency on b, which has no Vrum link type.",
        ),
        (
            ['{"id":"a","title":"A","dependencies":[{"issue_id":"a","depends_on_id":"nope"}]}'],
            "a has a blocks dependency on nope, which is not in the file.",
        ),
        (['{"id":"a","title":""}', '{"id":"a","title":"A"}'], "a appears more than once."),
        (['{"id":"a","title":"  "}'], "a has no title."),
    ],
)
def test_a_beads_file_that_does_not_fit_is_refused_and_every_problem_is_listed(
    lines: list[str], wanted: str
) -> None:
    with pytest.raises(ImportProblems) as refused:
        from_beads("\n".join(lines))
    assert any(wanted in problem for problem in refused.value.problems), refused.value.problems


def test_all_the_problems_of_a_file_are_listed_at_once() -> None:
    with pytest.raises(ImportProblems) as refused:
        from_beads(
            "\n".join(
                [
                    "not json",
                    '{"id":"a","title":"A","status":"someday"}',
                    '{"id":"b","title":"B","priority":9}',
                    '{"id":"c","title":"C","parent":"zzz"}',
                ]
            )
        )
    assert len(refused.value.problems) == 4


def test_a_long_title_is_cut_and_the_whole_text_kept_in_the_details() -> None:
    long = "word " * 100
    imported = from_beads(json.dumps({"id": "a", "title": long, "issue_type": "epic"}))
    item = imported.items[0]
    assert len(item["title"]) <= 200 and item["title"].endswith("\u2026")
    assert long.strip() in item["body"]


def test_a_github_issue_list_becomes_milestones_issues_and_tracked_tasks() -> None:
    imported = from_github_issues(GITHUB)
    assert titles(imported, "epic") == ["Inbox", "v1"]
    assert titles(imported, "story") == ["Tracking: sign in", "Dark mode"]
    assert titles(imported, "task") == ["Login form", "Password reset"]
    placed = under(imported)
    assert placed["Tracking: sign in"] == "v1" and placed["Dark mode"] == "Inbox"
    assert placed["Login form"] == "Tracking: sign in"
    assert placed["Password reset"] == "Tracking: sign in"
    by_title = {item["title"]: item for item in imported.items}
    assert by_title["Tracking: sign in"]["priority"] == "high"
    assert by_title["Password reset"]["priority"] == "low"
    assert (
        "Imported from GitHub: issue #2 (https://github.com/acme/app/issues/2)"
        in (by_title["Login form"]["body"])
    )
    statuses = {item["title"]: imported.statuses.get(item["key"]) for item in imported.items}
    assert statuses["Login form"] == "done" and statuses["Password reset"] == "dropped"
    assert statuses["Dark mode"] is None
    assert imported.links == [] and "1 pull requests were left out" in imported.left_out[0]


@pytest.mark.parametrize(
    ("text", "wanted"),
    [
        ("nope", "The file is not JSON."),
        ('{"a": 1}', "The file must be a JSON list of issues."),
        ('[{"title": "no number"}]', "An entry is not an issue with a number."),
    ],
)
def test_a_github_file_that_is_not_a_list_of_issues_is_refused(text: str, wanted: str) -> None:
    with pytest.raises(ImportProblems) as refused:
        from_github_issues(text)
    assert wanted in refused.value.problems


def test_a_spec_kit_tasks_file_becomes_an_epic_phases_as_stories_and_tasks_with_their_needs() -> (
    None
):
    imported = from_spec_kit(SPEC_KIT)
    assert titles(imported, "epic") == ["Photo albums"]
    assert titles(imported, "story") == [
        "Phase 1: Setup",
        "Phase 2: Foundational (Blocking Prerequisites)",
        "Phase 3: User Story 1 - Browse albums (Priority: P1)",
        "Phase 4: User Story 2 - Share an album (Priority: P2)",
    ]
    assert titles(imported, "task") == [
        "Create project structure per the plan",
        "Configure linting",
        "Set up the database schema",
        "Create the Album model in src/models/album.py",
        "Build the album list",
        "Create share links",
    ]
    placed = under(imported)
    assert placed["Build the album list"].startswith("Phase 3")
    assert placed["Create share links"].startswith("Phase 4")
    assert placed["Configure linting"] == "Phase 1: Setup"
    assert linked(imported) == {
        ("Set up the database schema", "needs_first", "Create project structure per the plan"),
        ("Build the album list", "needs_first", "Set up the database schema"),
        ("Build the album list", "needs_first", "Create the Album model in src/models/album.py"),
    }
    statuses = {item["title"]: imported.statuses.get(item["key"]) for item in imported.items}
    assert statuses["Create project structure per the plan"] == "done"
    assert statuses["Configure linting"] is None
    assert "T999" not in json.dumps(imported.items) and "Commit after each task" not in json.dumps(
        imported.items
    )
    by_title = {item["title"]: item for item in imported.items}
    assert "Imported from Spec Kit: T005" in by_title["Build the album list"]["body"]


def test_a_user_story_label_puts_a_task_under_that_stories_phase() -> None:
    imported = from_spec_kit(
        "# Tasks: X\n\n## Phase 1: Setup\n- [ ] T001 A\n\n"
        "## Phase 2: User Story 1 - One\n- [ ] T002 B\n\n"
        "## Phase 3: Polish\n- [ ] T003 [US1] Belongs to one\n"
    )
    assert under(imported)["Belongs to one"] == "Phase 2: User Story 1 - One"


def test_a_spec_kit_file_with_no_tasks_or_a_missing_dependency_is_refused() -> None:
    with pytest.raises(ImportProblems) as refused:
        from_spec_kit("# Tasks: X\n\n## Phase 1\n\nNothing here.\n")
    assert "No task lines were found" in refused.value.problems[0]
    with pytest.raises(ImportProblems) as missing:
        from_spec_kit("# Tasks: X\n\n## Phase 1\n- [ ] T001 A (depends on T009)\n")
    assert missing.value.problems == ["T001 depends on T009, which is not in the file."]
    with pytest.raises(ImportProblems) as twice:
        from_spec_kit("# Tasks: X\n\n## Phase 1\n- [ ] T001 A\n- [ ] T001 B\n")
    assert twice.value.problems == ["T001 appears more than once."]


# -- the agent setup -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def setup() -> Any:
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("setup_agent", SCRIPTS / "setup_agent.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


URL = "http://127.0.0.1:8000/mcp"


@pytest.mark.parametrize("client", ["claude", "codex", "cursor", "copilot"])
def test_the_setup_writes_valid_configuration_and_a_guide_and_never_a_key(
    setup: Any, client: str, tmp_path: Path
) -> None:
    assert setup.main([client, URL, "--dir", str(tmp_path)]) == 0
    config = tmp_path / setup.CONFIG_FILE[client]
    text = config.read_text(encoding="utf-8")
    assert "vrk_" not in text and "VRUM_AGENT_KEY" in text
    if client == "codex":
        entry = tomllib.loads(text)["mcp_servers"]["vrum"]
        assert entry == {"url": URL, "bearer_token_env_var": "VRUM_AGENT_KEY"}
    else:
        entry = json.loads(text)["mcpServers"]["vrum"]
        assert entry["url"] == URL
        header = entry["headers"]["Authorization"]
        assert header == (
            "Bearer ${env:VRUM_AGENT_KEY}" if client == "cursor" else "Bearer ${VRUM_AGENT_KEY}"
        )
        assert ("tools" in entry) == (client == "copilot")
    guide = (tmp_path / setup.GUIDE_FILE[client]).read_text(encoding="utf-8")
    assert guide.startswith(setup.START) and guide.rstrip().endswith(setup.END)
    assert "next_work" in guide and "vrk_" not in guide
    assert "\u2014" not in guide and "\u2013" not in guide


def test_the_setup_keeps_what_was_there_and_running_it_again_changes_nothing(
    setup: Any, tmp_path: Path
) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"url": "http://other/mcp"}}, "extra": 1}),
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("# My notes\n\nKeep this.\n", encoding="utf-8")
    assert setup.main(["claude", URL, "--dir", str(tmp_path)]) == 0
    first = {
        p.name: p.read_text(encoding="utf-8")
        for p in (tmp_path / ".mcp.json", tmp_path / "CLAUDE.md")
    }
    document = json.loads(first[".mcp.json"])
    assert set(document["mcpServers"]) == {"other", "vrum"} and document["extra"] == 1
    assert first["CLAUDE.md"].startswith("# My notes\n\nKeep this.\n")
    assert setup.main(["claude", "http://127.0.0.1:9000/mcp", "--dir", str(tmp_path)]) == 0
    second = (tmp_path / ".mcp.json").read_text(encoding="utf-8")
    assert json.loads(second)["mcpServers"]["vrum"]["url"] == "http://127.0.0.1:9000/mcp"
    again = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert again == first["CLAUDE.md"] and again.count(setup.START) == 1


def test_the_setup_refuses_what_it_should_not_touch(setup: Any, tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit) as bad:
        setup.main(["claude", URL, "--dir", str(tmp_path)])
    assert "left alone" in str(bad.value)
    assert (tmp_path / ".mcp.json").read_text(encoding="utf-8") == "{ not json"
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        '[mcp_servers.vrum]\nurl = "http://mine/mcp"\n', encoding="utf-8"
    )
    with pytest.raises(SystemExit) as mine:
        setup.main(["codex", URL, "--dir", str(tmp_path)])
    assert "already has" in str(mine.value)
    for url in (
        "ftp://x/mcp",
        "http://x/other",
        "http://user:pw@x/mcp",
        "http://x/mcp?key=1",
        "http://x/mcp/vrk_abcdefghij",
    ):
        with pytest.raises(SystemExit):
            setup.main(["claude", url, "--dir", str(tmp_path)])
    with pytest.raises(SystemExit):
        setup.main(["claude", URL, "--dir", str(tmp_path / "missing")])
