"""The Markdown copy (vrum.markdown.render) from a file in memory, with no server: file names,
the fields at the top, what a decision or fact says about where it stands, every connection written
as a reference at both ends, the same files every time, and nothing that would break when the text
is odd."""

import re
from pathlib import Path
from typing import Any

from tests.documents import WHEN, a_file, decision, fact, link, node, rule
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
            decision(
                1,
                node_ids=[2],
                rule_ids=[1],
                status="superseded",
                superseded_by_id=2,
                confirmed_by="Marie",
                confirmed_at="2026-11-14T09:30:00.000000Z",
            ),
            decision(
                2,
                title="Keep it simple",
                project_wide=True,
                node_ids=[1],
                confirmed_by="Marie",
                confirmed_at=WHEN,
            ),
            decision(3, status="withdrawn", status_reason="We dropped\nthe plan"),
        ],
        facts=[
            fact(1, node_ids=[3], rule_ids=[1], decision_ids=[2]),
            fact(
                2,
                status="superseded",
                superseded_by_id=4,
                status_reason="Newer",
                confirmed_by="Ada",
                confirmed_at="2026-11-12T08:00:00.000000Z",
            ),
            fact(3, status="withdrawn", status_reason="It was wrong"),
            fact(4, text="x" * 300),
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
            "decisions/decision-2-keep-it-simple.md",
            "decisions/decision-3-a-decision.md",
            "facts/fact-1-fact-1.md",
            "facts/fact-2-fact-2.md",
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
    fact_file = files["fact-1-fact-1"]
    assert fact_file.startswith(
        "---\n"
        "type: fact\n"
        "id: 1\n"
        "status: active\n"
        'recorded_by: "ann"\n'
        "recorded_at: 2026-11-15T10:00:00.000000Z\n"
        "---\n"
        "\n# Fact 1\n"
    )
    assert "## Source\n\na chat\n" in fact_file
    unicode_title = files["task-4-café-ünïcode-日本語"]
    assert 'title: "Café ünïcode — 日本語"' in unicode_title
    superseded = files["fact-2-fact-2"]
    assert 'status_reason: "Newer"' in superseded and "status: superseded" in superseded
    assert 'confirmed_by: "Ada"' in superseded
    assert "confirmed_at: 2026-11-12T08:00:00.000000Z" in superseded
    decided = files["decision-3-a-decision"]
    assert 'decided_by: "Marie"' in decided and "decided_on: 2026-11-15" in decided
    assert "project_wide: false" in decided
    assert "What was decided\n\n## Reason\n\nBecause\n" in decided


def test_a_fact_or_decision_says_where_it_stands_and_whether_a_person_confirmed_it() -> None:
    files = stems(render(a_graph()))

    def standing(stem: str) -> str:
        found = re.search(r"- Kind:.*\n- Status:.*\n- Confirmation:.*", files[stem])
        assert found is not None, stem
        return found.group(0)

    assert standing("fact-1-fact-1") == (
        "- Kind: fact\n- Status: active\n- Confirmation: not confirmed by a person"
    )
    # A confirmation is kept when the fact it was given to is replaced: it stays visible.
    assert standing("fact-2-fact-2") == (
        "- Kind: fact\n"
        "- Status: superseded by [[fact-4-" + "x" * 50 + "]]. Why: Newer\n"
        "- Confirmation: confirmed by Ada on 2026-11-12, as it was when it ended"
    )
    assert standing("fact-3-fact-3") == (
        "- Kind: fact\n- Status: withdrawn. Why: It was wrong\n"
        "- Confirmation: not confirmed by a person"
    )
    assert standing("decision-1-a-decision") == (
        "- Kind: decision\n"
        "- Status: superseded by [[decision-2-keep-it-simple]]\n"
        "- Confirmation: confirmed by Marie on 2026-11-14, as it was when it ended"
    )
    assert standing("decision-2-keep-it-simple") == (
        "- Kind: project-wide decision (it applies throughout the project)\n"
        "- Status: active\n"
        "- Confirmation: confirmed by Marie on 2026-11-15"
    )
    assert "project_wide: true" in files["decision-2-keep-it-simple"]
    assert standing("decision-3-a-decision") == (
        "- Kind: decision\n- Status: withdrawn. Why: We dropped the plan\n"
        "- Confirmation: not confirmed by a person"
    )
    assert 'status_reason: "We dropped\\nthe plan"' in files["decision-3-a-decision"]


def test_every_connection_is_a_reference_at_both_ends_under_its_own_name() -> None:
    files = stems(render(a_graph()))
    story, task3 = files["story-2-email"], files["task-3-form"]

    assert "- parent: [[epic-1-log-in-the-safe-way-1]]" in story
    assert "- children: [[task-3-form]]\n- children: [[task-4-café-ünïcode-日本語]]" in story
    assert "- requirements: [[rule-1-rules]]" in story
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
    assert "- replaced by: [[decision-2-keep-it-simple]]" in files["decision-1-a-decision"]
    assert "- replaces: [[decision-1-a-decision]]" in files["decision-2-keep-it-simple"]
    assert "- concerns: [[decision-2-keep-it-simple]]" in files["fact-1-fact-1"]
    assert "- concerns: [[rule-1-rules]]\n- concerns: [[task-3-form]]" in files["fact-1-fact-1"]
    assert "- facts about it: [[fact-1-fact-1]]" in task3
    assert "- facts about it: [[fact-1-fact-1]]" in files["decision-2-keep-it-simple"]
    assert "- facts about it: [[fact-1-fact-1]]" in files["rule-1-rules"]
    assert "- replaced by: [[fact-4-" in files["fact-2-fact-2"]
    assert "- replaces: [[fact-2-fact-2]]" in files["fact-4-" + "x" * 50]
    # The links come last, after anything the text says.
    assert files["epic-1-log-in-the-safe-way-1"].index("Second") < (
        files["epic-1-log-in-the-safe-way-1"].index("## Links")
    )
    # A file with nothing connected to it has no list.
    assert "## Links" not in files["rule-2-rule-2"]
    for text in files.values():
        for target in re.findall(r"\[\[([^\]]+)\]\]", text):
            assert target in files


def test_references_written_in_a_text_become_links_to_files_and_missing_ones_plain_words() -> None:
    graph = a_file(
        nodes=[
            node(1, "epic", None, title="Login"),
            node(2, "story", 1, title="Email", body="See [[node:1]], [[rule:1]] and [[node:99]]."),
        ],
        rules=[rule(1, 2, title="Short codes")],
        decisions=[decision(5, title="Keep it simple", text="As in [[fact:7]]")],
        facts=[
            fact(7, text="Rule [[rule:1]] holds", source="Said in [[decision:5]]"),
            fact(8, text="Not [[fact:8]] but [[belief:8]] and [[fact:9]]"),
        ],
    )

    by_stem = stems(render(graph))

    assert (
        "See [[epic-1-login]], [[rule-1-short-codes]] and node:99 (missing)."
        in by_stem["story-2-email"]
    )
    assert "As in [[fact-7-rule-rule-1-holds]]" in by_stem["decision-5-keep-it-simple"]
    fact_file = by_stem["fact-7-rule-rule-1-holds"]
    assert "Rule [[rule-1-short-codes]] holds" in fact_file
    assert "## Source\n\nSaid in [[decision-5-keep-it-simple]]" in fact_file
    # There is no such thing as a belief any more, and a fact that is not there is missing.
    other = by_stem["fact-8-not-fact-8-but-belief-8-and-fact-9"]
    assert (
        "Not [[fact-8-not-fact-8-but-belief-8-and-fact-9]] but [[belief:8]] and fact:9 (missing)"
        in other
    )
    for text in by_stem.values():
        assert not re.search(r"\[\[(node|rule|decision|fact):", text)


def test_a_replacement_that_is_not_in_the_file_is_written_as_missing() -> None:
    graph = a_file(
        facts=[fact(1, status="superseded", superseded_by_id=9, status_reason="Newer")],
    )

    files = stems(render(graph))

    assert "- Status: superseded by fact:9 (missing). Why: Newer" in files["fact-1-fact-1"]
    assert "## Links" not in files["fact-1-fact-1"]


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
