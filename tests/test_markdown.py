"""The Markdown copy (vrum.markdown.render) from a file in memory, with no server: file names,
the fields at the top, what a note says about where it stands, every connection written as a
reference at both ends, the same files every time, and nothing that would break when the text is
odd."""

import re
from pathlib import Path
from typing import Any

from tests.documents import WHEN, a_file, check, definition, link, node, note, rule
from vrum.definition_of_done import item_key as item_key_of
from vrum.markdown import DEFINITION_FILE, FOLDERS, render


def stems(files: dict[str, str]) -> dict[str, str]:
    return {Path(path).stem: text for path, text in files.items()}


LONG = "note-6-" + "x" * 50


def a_graph() -> dict[str, Any]:
    # Epic 1, story 1 and task 1 are three different items, and so are their files.
    return a_file(
        nodes=[
            node("epic", 1, title='Log in: the "safe" way # 1', body="First line\nSecond"),
            node(
                "story", 1, "epic:1", title="Email", start_on="2026-12-01", target_on="2026-12-31"
            ),
            node("task", 1, "story:1", title="Form"),
            node("task", 2, "story:1", title="Café ünïcode — 日本語"),
        ],
        links=[
            link(1, "task:2", "task:1", "needs_first"),
            link(2, "task:1", "task:2", "duplicates"),
            link(3, "story:1", "epic:1", "relates_to"),
            link(4, "epic:1", "story:1", "supersedes"),
        ],
        rules=[
            rule(1, "story:1", title="Rules"),
            rule(2, None, body="Not placed."),
            rule(3, "task:1", title="Works on a phone"),
        ],
        notes=[
            note(
                1,
                text="Use email\n\nEverybody has one",
                decided_by="Marie",
                decided_on="2026-11-15",
                nodes=["story:1"],
                rule_ids=[1],
                status="superseded",
                superseded_by_id=2,
                confirmed_by="Marie",
                confirmed_at="2026-11-14T09:30:00.000000Z",
            ),
            note(
                2,
                text="Keep it simple",
                decided_by="Marie",
                decided_on="2026-11-15",
                project_wide=True,
                nodes=["epic:1"],
                supersedes_ids=[1],
                confirmed_by="Marie",
                confirmed_at=WHEN,
            ),
            note(3, status="withdrawn", status_reason="We dropped\nthe plan"),
            note(4, nodes=["task:1"], rule_ids=[1], note_ids=[2]),
            note(
                5,
                status="superseded",
                superseded_by_id=6,
                status_reason="Newer",
                confirmed_by="Ada",
                confirmed_at="2026-11-12T08:00:00.000000Z",
            ),
            note(6, text="x" * 300, supersedes_ids=[5]),
        ],
        definition_of_done=[definition(3, body="- Tests pass\n- Ready for [[story:1]]")],
    )


def test_there_is_one_file_per_item_named_by_its_kind_its_number_and_its_headline() -> None:
    files = render(a_graph())

    assert sorted(files) == sorted(
        [
            "epics/epic-1-log-in-the-safe-way-1.md",
            "stories/story-1-email.md",
            "tasks/task-1-form.md",
            "tasks/task-2-café-ünïcode-日本語.md",
            "rules/rule-1-rules.md",
            "rules/rule-2-rule-2.md",
            "rules/rule-3-works-on-a-phone.md",
            "notes/note-1-use-email.md",
            "notes/note-2-keep-it-simple.md",
            "notes/note-3-note-3.md",
            "notes/note-4-note-4.md",
            "notes/note-5-note-5.md",
            f"notes/{LONG}.md",
            DEFINITION_FILE,
        ]
    )
    assert {Path(path).parts[0] for path in files} <= {*FOLDERS, DEFINITION_FILE}
    assert "decisions" not in FOLDERS and "facts" not in FOLDERS


def test_the_fields_are_at_the_top_and_odd_text_stays_one_valid_value() -> None:
    files = stems(render(a_graph()))

    epic = files["epic-1-log-in-the-safe-way-1"]
    assert epic.startswith(
        "---\n"
        "type: epic\n"
        "number: 1\n"
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
    assert "\nid:" not in epic
    story = files["story-1-email"]
    assert "start_on: 2026-12-01\ntarget_on: 2026-12-31\n" in story
    plain = files["note-4-note-4"]
    assert plain.startswith(
        "---\n"
        "type: note\n"
        "id: 4\n"
        "status: unconfirmed\n"
        "project_wide: false\n"
        'recorded_by: "ann"\n'
        "recorded_by_kind: agent\n"
        "recorded_at: 2026-11-15T10:00:00.000000Z\n"
        "---\n"
        "\n# note 4\n"
    )
    assert "decided_by" not in plain and "title:" not in plain
    assert "\nnote 4\n\n## Why\n\na chat\n" in plain
    unicode_title = files["task-2-café-ünïcode-日本語"]
    assert 'title: "Café ünïcode — 日本語"' in unicode_title
    superseded = files["note-5-note-5"]
    assert 'status_reason: "Newer"' in superseded and "status: superseded" in superseded
    assert 'confirmed_by: "Ada"' in superseded
    assert "confirmed_at: 2026-11-12T08:00:00.000000Z" in superseded
    decided = files["note-1-use-email"]
    assert 'decided_by: "Marie"' in decided and "decided_on: 2026-11-15" in decided
    assert "project_wide: false" in decided
    # The headline is the file's title, and the text is there in full, with why after it.
    assert "\n# Use email\n" in decided
    assert "Use email\n\nEverybody has one\n\n## Why\n\na chat\n" in decided
    # A long headline is cut as everywhere else.
    assert f"\n# {'x' * 119}\u2026\n" in files[LONG]


def test_a_note_says_its_status_and_who_decided_it_and_what_a_person_confirmed() -> None:
    files = stems(render(a_graph()))

    def standing(stem: str) -> str:
        found = re.search(r"- Status:.*(\n- (?:Confirmed|Decided|Applies):.*)*", files[stem])
        assert found is not None, stem
        return found.group(0)

    assert standing("note-4-note-4") == "- Status: unconfirmed"
    # A confirmation is kept when the note it was given to is replaced: it stays visible.
    assert standing("note-5-note-5") == (
        f"- Status: superseded by [[{LONG}]]. Why: Newer\n"
        "- Confirmed: by Ada on 2026-11-12, as it was when it ended"
    )
    assert standing("note-3-note-3") == "- Status: withdrawn. Why: We dropped the plan"
    # A note somebody decided says who and when, as a detail, and not as another kind of note.
    assert standing("note-1-use-email") == (
        "- Status: superseded by [[note-2-keep-it-simple]]\n"
        "- Confirmed: by Marie on 2026-11-14, as it was when it ended\n"
        "- Decided: by Marie on 2026-11-15"
    )
    assert standing("note-2-keep-it-simple") == (
        "- Status: confirmed by Marie on 2026-11-15\n"
        "- Decided: by Marie on 2026-11-15\n"
        "- Applies: throughout the project"
    )
    assert all("- Kind:" not in text and "- Confirmation:" not in text for text in files.values())
    assert "project_wide: true" in files["note-2-keep-it-simple"]
    assert 'status_reason: "We dropped\\nthe plan"' in files["note-3-note-3"]


def test_every_connection_is_a_reference_at_both_ends_under_its_own_name() -> None:
    files = stems(render(a_graph()))
    story, task1 = files["story-1-email"], files["task-1-form"]

    assert "- parent: [[epic-1-log-in-the-safe-way-1]]" in story
    assert "- children: [[task-1-form]]\n- children: [[task-2-café-ünïcode-日本語]]" in story
    assert "- acceptance criteria: [[rule-1-rules]]" in story
    assert "- notes about it: [[note-1-use-email]]" in story
    assert "- acceptance criteria: [[rule-3-works-on-a-phone]]" in task1
    assert "- needed first by: [[task-2-café-ünïcode-日本語]]" in task1
    assert "- duplicates: [[task-2-café-ünïcode-日本語]]" in task1
    assert "- needs first: [[task-1-form]]" in files["task-2-café-ünïcode-日本語"]
    assert "- duplicates: [[task-1-form]]" in files["task-2-café-ünïcode-日本語"]
    assert "- relates to: [[epic-1-log-in-the-safe-way-1]]" in story
    assert "- relates to: [[story-1-email]]" in files["epic-1-log-in-the-safe-way-1"]
    assert "- supersedes: [[story-1-email]]" in files["epic-1-log-in-the-safe-way-1"]
    assert "- superseded by: [[epic-1-log-in-the-safe-way-1]]" in story
    assert "- on: [[story-1-email]]" in files["rule-1-rules"]
    assert "- on: [[task-1-form]]" in files["rule-3-works-on-a-phone"]
    # A note concerns nodes, acceptance criteria and other notes, all under the one name.
    assert (
        "- concerns: [[rule-1-rules]]\n- concerns: [[story-1-email]]" in files["note-1-use-email"]
    )
    assert "- concerns: [[epic-1-log-in-the-safe-way-1]]" in files["note-2-keep-it-simple"]
    assert (
        "- concerns: [[note-2-keep-it-simple]]\n"
        "- concerns: [[rule-1-rules]]\n"
        "- concerns: [[task-1-form]]"
    ) in files["note-4-note-4"]
    assert "- replaced by: [[note-2-keep-it-simple]]" in files["note-1-use-email"]
    assert "- replaces: [[note-1-use-email]]" in files["note-2-keep-it-simple"]
    assert "- notes about it: [[note-4-note-4]]" in files["note-2-keep-it-simple"]
    assert "- notes about it: [[note-4-note-4]]" in task1
    assert (
        "- notes about it: [[note-1-use-email]]\n- notes about it: [[note-4-note-4]]"
        in (files["rule-1-rules"])
    )
    assert "- replaced by: [[note-6-" in files["note-5-note-5"]
    assert "- replaces: [[note-5-note-5]]" in files[LONG]
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
            node("epic", 1, title="Login", body="Made of [[story:1]], not [[epic:2]]."),
            node(
                "story",
                1,
                "epic:1",
                title="Email",
                body="See [[epic:1]], [[task:1]], [[rule:1]] and [[task:99]].",
            ),
            node("task", 1, "story:1", title="Form"),
        ],
        rules=[rule(1, "story:1", title="Short codes")],
        notes=[
            note(5, text="Keep it simple\n\nAs in [[note:7]] and [[node:1]]"),
            note(7, text="Rule [[rule:1]] holds", why="Said in [[note:5]]"),
            note(8, text="Not [[note:8]] but [[belief:8]] and [[note:9]]"),
            note(9, text="The old [[decision:5]] and [[fact:7]] are only text"),
        ],
    )

    by_stem = stems(render(graph))

    # Epic 1, story 1 and task 1 are three different files, and a reference names the right one.
    assert "Made of [[story-1-email]], not epic:2 (missing)." in by_stem["epic-1-login"]
    assert (
        "See [[epic-1-login]], [[task-1-form]], [[rule-1-short-codes]] and task:99 (missing)."
        in by_stem["story-1-email"]
    )
    # The old way of writing a reference to an epic, story or task is only text now.
    assert "As in [[note-7-rule-rule-1-holds]] and [[node:1]]" in by_stem["note-5-keep-it-simple"]
    seven = by_stem["note-7-rule-rule-1-holds"]
    assert "Rule [[rule-1-short-codes]] holds" in seven
    assert "## Why\n\nSaid in [[note-5-keep-it-simple]]" in seven
    # There is no such thing as a belief, and a note that is not there is missing.
    other = by_stem["note-8-not-note-8-but-belief-8-and-note-9"]
    assert "Not [[note-8-not-note-8-but-belief-8-and-note-9]] but [[belief:8]] and [[" in other
    assert "(missing)" not in other
    # What was written for decisions and facts names nothing now, and is left as it was written.
    assert (
        "The old [[decision:5]] and [[fact:7]] are only text"
        in by_stem["note-9-the-old-decision-5-and-fact-7-are-only-text"]
    )
    for stem, text in by_stem.items():
        if stem != "note-9-the-old-decision-5-and-fact-7-are-only-text":
            assert not re.search(r"\[\[(epic|story|task|rule|note):", text)


def test_a_replacement_that_is_not_in_the_file_is_written_as_missing() -> None:
    graph = a_file(
        notes=[note(1, status="superseded", superseded_by_id=9, status_reason="Newer")],
    )

    files = stems(render(graph))

    assert "- Status: superseded by note:9 (missing). Why: Newer" in files["note-1-note-1"]
    assert "## Links" not in files["note-1-note-1"]


def test_the_same_graph_gives_the_same_files_every_time() -> None:
    assert render(a_graph()) == render(a_graph())
    assert all(text.endswith("\n") and "\r" not in text for text in render(a_graph()).values())


def test_the_definition_of_done_is_one_file_at_the_top_that_links_to_what_it_names() -> None:
    files = render(a_graph())

    assert files[DEFINITION_FILE] == (
        "---\n"
        "type: definition_of_done\n"
        "version: 3\n"
        f"updated_at: {WHEN}\n"
        'updated_by: "Marie"\n'
        "---\n"
        "\n"
        "# Definition of done\n"
        "\n"
        "- Tests pass\n"
        "- Ready for [[story-1-email]]\n"
    )
    # A project that never set one still has the file, which says so.
    empty = render(a_file())[DEFINITION_FILE]
    assert "Nobody has set a definition of done yet." in empty and "version" not in empty


def test_a_node_says_its_size_and_a_story_or_task_lists_its_checks_as_a_task_list() -> None:
    graph = a_file(
        nodes=[
            node("epic", 1, title="Epic"),
            node("story", 1, "epic:1", title="Story", size="l"),
            node("task", 1, "story:1", title="Task", size="xs"),
        ],
        definition_of_done=[definition(body="Done means:\n- Tests pass\n- Docs are updated")],
        checks=[
            check("task:1", item_key_of("Tests pass"), evidence="212 tests pass\nall of them"),
            check(
                "story:1",
                item_key_of("Docs are updated"),
                text="Docs are updated",
                checked_by="Marie",
                checked_by_kind="person",
                evidence=None,
            ),
            # A tick of a line that is no longer in the text is not shown.
            check("task:1", "0f0f0f0f", text="A line that was taken out"),
        ],
    )

    files = stems(render(graph))

    assert "size: xs\n" in files["task-1-task"] and "size: l\n" in files["story-1-story"]
    assert "size:" not in files["epic-1-epic"] and "Definition of done" not in files["epic-1-epic"]
    assert (
        "## Definition of done\n\n"
        "- [x] Tests pass (by ann, 2026-11-15: 212 tests pass all of them)\n"
        "- [ ] Docs are updated\n"
    ) in files["task-1-task"]
    assert (
        "## Definition of done\n\n- [ ] Tests pass\n- [x] Docs are updated (by Marie, 2026-11-15)\n"
    ) in files["story-1-story"]
    assert "taken out" not in "".join(files.values())
    # With no list lines in the definition of done there is nothing to list.
    plain = a_file(
        nodes=[node("epic", 1), node("story", 1, "epic:1")],
        definition_of_done=[definition(body="Words only.")],
    )
    assert "Definition of done" not in stems(render(plain))["story-1-story-1"]


def test_an_empty_graph_has_only_the_definition_file_and_odd_names_do_not_break_it() -> None:
    assert list(render(a_file())) == [DEFINITION_FILE]
    odd = a_file(
        nodes=[
            node("epic", 1, title="///", body="   "),
            node("epic", 2, title="a\nb\tc"),
        ],
        links=[link(1, "epic:1", "epic:99", "relates_to")],
        notes=[note(1, text="\n\n   ///   \nthen more")],
    )

    files = render(odd)

    assert sorted(files) == [
        DEFINITION_FILE,
        "epics/epic-1-untitled.md",
        "epics/epic-2-a-b-c.md",
        "notes/note-1-untitled.md",
    ]
    assert "# a b c\n" in files["epics/epic-2-a-b-c.md"]
    assert "## Links" not in files["epics/epic-1-untitled.md"]
    assert files["epics/epic-1-untitled.md"].endswith("# ///\n")
    assert "\n# ///\n" in files["notes/note-1-untitled.md"]
