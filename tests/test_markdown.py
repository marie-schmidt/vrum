"""The Markdown copy (vrum.markdown.render) from a file in memory, with no server: file names,
the fields at the top, every connection written as a reference at both ends, the same files every
time, and nothing that would break when the text is odd."""

import re
from pathlib import Path
from typing import Any

from tests.documents import a_file, decision, link, memory, node, rule
from vrum.markdown import FOLDERS, render


def stems(files: dict[str, str]) -> dict[str, str]:
    return {Path(path).stem: text for path, text in files.items()}


def a_graph() -> dict[str, Any]:
    return a_file(
        nodes=[
            node(1, "epic", None, title='Log in: the "safe" way # 1', body="First line\nSecond"),
            node(2, "story", 1, title="Email", start_on="2026-12-01", target_on="2026-12-31"),
            node(3, "task", 2, title="Form"),
            node(4, "task", 2, title="Café ünïcode — 日本語"),
        ],
        links=[
            link(1, 4, 3, "needs_first"),
            link(2, 3, 4, "duplicates"),
            link(3, 2, 1, "relates_to"),
            link(4, 1, 2, "supersedes"),
        ],
        rules=[rule(1, 2, title="Rules"), rule(2, None, body="Not placed.")],
        decisions=[
            decision(1, node_ids=[2], rule_ids=[1], status="superseded", superseded_by_id=2),
            decision(2, kind="principle", title="Keep it simple", node_ids=[1]),
            decision(3, promoted_from_id=2),
        ],
        memories=[
            memory(
                1, "fact", node_ids=[3], rule_ids=[1], decision_ids=[2], happened_on="2026-11-01"
            ),
            memory(
                2, "belief", evidence=[{"type": "fact", "id": 1}, {"type": "decision", "id": 1}]
            ),
            memory(3, "fact", status="superseded", superseded_by_id=4, status_reason="Newer"),
            memory(4, "fact", text="x" * 300),
        ],
    )


def test_there_is_one_file_per_item_named_by_its_kind_its_id_and_its_title() -> None:
    files = render(a_graph())

    assert sorted(files) == sorted(
        [
            "epics/epic-1-log-in-the-safe-way-1.md",
            "stories/story-2-email.md",
            "tasks/task-3-form.md",
            "tasks/task-4-café-ünïcode-日本語.md",
            "rules/rule-1-rules.md",
            "rules/rule-2-rule-2.md",
            "decisions/decision-1-a-decision.md",
            "decisions/principle-2-keep-it-simple.md",
            "decisions/decision-3-a-decision.md",
            "facts/fact-1-fact-1.md",
            "beliefs/belief-2-belief-2.md",
            "facts/fact-3-fact-3.md",
            f"facts/fact-4-{'x' * 50}.md",
        ]
    )
    assert {Path(path).parts[0] for path in files} <= set(FOLDERS)


def test_the_fields_are_at_the_top_and_odd_text_stays_one_valid_value() -> None:
    files = stems(render(a_graph()))

    epic = files["epic-1-log-in-the-safe-way-1"]
    assert epic.startswith(
        "---\n"
        "type: epic\n"
        "id: 1\n"
        'title: "Log in: the \\"safe\\" way # 1"\n'
        "status: new\n"
        "priority: normal\n"
        "version: 1\n"
        "created_at: 2026-11-15T10:00:00.000000Z\n"
        "updated_at: 2026-11-15T10:00:00.000000Z\n"
        "---\n"
        '\n# Log in: the "safe" way # 1\n'
        "\nFirst line\nSecond\n"
    )
    story = files["story-2-email"]
    assert "start_on: 2026-12-01\ntarget_on: 2026-12-31\n" in story
    fact = files["fact-1-fact-1"]
    assert 'source: "a chat"' in fact and "happened_on: 2026-11-01" in fact
    assert "kind: world" in fact and "origin: stated" in fact
    assert "# Fact 1\n\nfact 1\n" in fact
    unicode_title = files["task-4-café-ünïcode-日本語"]
    assert 'title: "Café ünïcode — 日本語"' in unicode_title
    belief = files["belief-2-belief-2"]
    assert "kind:" not in belief and "source:" not in belief
    superseded = files["fact-3-fact-3"]
    assert 'status_reason: "Newer"' in superseded and "status: superseded" in superseded
    decided = files["decision-3-a-decision"]
    assert 'decided_by: "Marie"' in decided and "decided_on: 2026-11-15" in decided
    assert "What was decided\n\n## Reason\n\nBecause\n" in decided


def test_every_connection_is_a_reference_at_both_ends_under_its_own_name() -> None:
    files = stems(render(a_graph()))
    story, task3 = files["story-2-email"], files["task-3-form"]

    assert "- parent: [[epic-1-log-in-the-safe-way-1]]" in story
    assert "- children: [[task-3-form]]\n- children: [[task-4-café-ünïcode-日本語]]" in story
    assert "- rules: [[rule-1-rules]]" in story
    assert "- decisions that affect it: [[decision-1-a-decision]]" in story
    assert "- needed first by: [[task-4-café-ünïcode-日本語]]" in task3
    assert "- duplicates: [[task-4-café-ünïcode-日本語]]" in task3
    assert "- needs first: [[task-3-form]]" in files["task-4-café-ünïcode-日本語"]
    assert "- duplicates: [[task-3-form]]" in files["task-4-café-ünïcode-日本語"]
    assert "- relates to: [[epic-1-log-in-the-safe-way-1]]" in story
    assert "- relates to: [[story-2-email]]" in files["epic-1-log-in-the-safe-way-1"]
    assert "- supersedes: [[story-2-email]]" in files["epic-1-log-in-the-safe-way-1"]
    assert "- superseded by: [[epic-1-log-in-the-safe-way-1]]" in story
    assert "- story: [[story-2-email]]" in files["rule-1-rules"]
    assert (
        "- affects: [[rule-1-rules]]\n- affects: [[story-2-email]]"
        in (files["decision-1-a-decision"])
    )
    assert "- replaced by: [[principle-2-keep-it-simple]]" in files["decision-1-a-decision"]
    assert "- replaces: [[decision-1-a-decision]]" in files["principle-2-keep-it-simple"]
    assert "- promoted from: [[belief-2-belief-2]]" in files["decision-3-a-decision"]
    assert "- promoted to: [[decision-3-a-decision]]" in files["belief-2-belief-2"]
    assert "- concerns: [[task-3-form]]" in files["fact-1-fact-1"]
    assert "- facts and beliefs about it: [[fact-1-fact-1]]" in task3
    assert (
        "- rests on: [[decision-1-a-decision]]\n- rests on: [[fact-1-fact-1]]"
        in (files["belief-2-belief-2"])
    )
    assert "- evidence for: [[belief-2-belief-2]]" in files["fact-1-fact-1"]
    assert "- replaced by: [[fact-4-" in files["fact-3-fact-3"]
    # The links come last, after anything the text says.
    assert files["epic-1-log-in-the-safe-way-1"].index("Second") < (
        files["epic-1-log-in-the-safe-way-1"].index("## Links")
    )
    # A file with nothing connected to it has no list.
    assert "## Links" not in files["rule-2-rule-2"]
    for text in files.values():
        for target in re.findall(r"\[\[([^\]]+)\]\]", text):
            assert target in files


def test_the_same_graph_gives_the_same_files_every_time() -> None:
    assert render(a_graph()) == render(a_graph())
    assert all(text.endswith("\n") and "\r" not in text for text in render(a_graph()).values())


def test_an_empty_graph_has_no_files_and_odd_names_and_dangling_ids_do_not_break_it() -> None:
    assert render(a_file()) == {}
    odd = a_file(
        nodes=[
            node(1, "epic", None, title="///", body="   "),
            node(2, "epic", None, title="a\nb\tc"),
        ],
        links=[link(1, 1, 99, "relates_to")],
    )

    files = render(odd)

    assert sorted(files) == ["epics/epic-1-untitled.md", "epics/epic-2-a-b-c.md"]
    assert "# a b c\n" in files["epics/epic-2-a-b-c.md"]
    assert "## Links" not in files["epics/epic-1-untitled.md"]
    assert files["epics/epic-1-untitled.md"].endswith("# ///\n")
