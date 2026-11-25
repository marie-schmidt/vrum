"""Reading a file that is to be imported (vrum.exchange.check_document): what is accepted, and how
what is not is refused. No database: the loading itself is proved against the real containers in
test_notes_integration.py and test_exchange_second_stack.py."""

import copy
from typing import Any

import pytest

from tests.documents import (
    WHEN,
    a_file,
    check,
    definition,
    event,
    link,
    node,
    note,
    rule,
)
from vrum.common import Refusal
from vrum.exchange import Document, _cursor, _cursor_after, check_document


def the_refusal(document: dict[str, Any]) -> str:
    with pytest.raises(Refusal) as refusal:
        check_document(document)
    message = str(refusal.value)
    assert "nothing was imported" in message
    return message


def test_an_empty_file_and_a_whole_one_are_accepted() -> None:
    assert isinstance(check_document(a_file()), Document)
    # Each kind is numbered on its own: epic 1, story 1 and task 1 are three different items.
    whole = a_file(
        nodes=[
            node("epic", 1, start_on="2026-12-01", target_on="2026-12-31", status="ready"),
            node("story", 1, "epic:1", size="l"),
            node("task", 1, "story:1", size="xs"),
        ],
        links=[link(1, "task:1", "story:1", "relates_to")],
        rules=[rule(1, "story:1"), rule(2, "task:1")],
        notes=[
            note(
                1,
                nodes=["story:1"],
                rule_ids=[1],
                project_wide=True,
                decided_by="Marie",
                decided_on="2026-11-15",
                confirmed_by="Marie",
                confirmed_at=WHEN,
            ),
            note(2, status="withdrawn", status_reason="No longer true"),
            note(3, status="superseded", superseded_by_id=4),
            note(4, supersedes_ids=[3], note_ids=[1]),
            note(
                5,
                nodes=["story:1", "epic:1"],
                rule_ids=[1],
                note_ids=[1, 4],
                status="superseded",
                superseded_by_id=6,
                status_reason="Newer",
                confirmed_by="Marie",
                confirmed_at=WHEN,
            ),
            note(6, supersedes_ids=[5]),
        ],
        definition_of_done=[definition()],
        checks=[
            check("task:1", "a1b2c3d4"),
            check(
                "story:1", "a1b2c3d4", checked_by="Marie", checked_by_kind="person", evidence=None
            ),
        ],
        events=[
            event(1, node="epic:1", after={"title": "epic 1"}),
            event(2, action="note_recorded", note_id=1),
            event(3, action="note_linked", note_id=1, node="story:1"),
            event(4, action="note_linked", note_id=4, other_note_id=1),
            event(5, action="note_withdrawn", note_id=2),
            event(6, action="rule_created", rule_id=1, node="story:1"),
            event(7, action="link_added", node="task:1", other_node="story:1"),
            event(
                8,
                action="definition_of_done_changed",
                before={"body": "", "version": 1},
                after={"body": "- Tests pass", "version": 2},
            ),
            event(
                9,
                action="check_ticked",
                node="task:1",
                after={"item": "a1b2c3d4", "text": "Tests pass", "evidence": "212 tests pass"},
            ),
        ],
    )
    whole["counts"] = {
        "nodes": 3,
        "links": 1,
        "rules": 2,
        "notes": 6,
        "definition_of_done": 1,
        "checks": 2,
        "events": 9,
    }

    parsed = check_document(whole)

    assert [n.status for n in parsed.nodes] == ["ready", "new", "new"]
    assert [(n.kind, n.number, n.parent) for n in parsed.nodes] == [
        ("epic", 1, None),
        ("story", 1, "epic:1"),
        ("task", 1, "story:1"),
    ]
    assert [n.size for n in parsed.nodes] == [None, "l", "xs"]
    assert [(c.node, c.item, c.checked_by_kind, c.evidence) for c in parsed.checks] == [
        ("task:1", "a1b2c3d4", "agent", "212 tests pass"),
        ("story:1", "a1b2c3d4", "person", None),
    ]
    assert parsed.events[8].action == "check_ticked"
    assert (parsed.links[0].source, parsed.links[0].target) == ("task:1", "story:1")
    # An acceptance criterion is on a story or a task, and the definition of done is one text.
    assert [rule.node for rule in parsed.rules] == ["story:1", "task:1"]
    assert (parsed.definition_of_done[0].body, parsed.definition_of_done[0].version) == (
        "- Tests pass",
        2,
    )
    assert parsed.events[7].action == "definition_of_done_changed"
    assert parsed.notes[0].nodes == ["story:1"] and parsed.notes[4].nodes == ["story:1", "epic:1"]
    assert (parsed.events[6].node, parsed.events[6].other_node) == ("task:1", "story:1")
    assert (parsed.events[3].note_id, parsed.events[3].other_note_id) == (4, 1)
    assert str(parsed.nodes[0].start_on) == "2026-12-01"
    assert parsed.nodes[0].created_at.tzinfo is not None
    assert parsed.notes[0].project_wide and parsed.notes[1].status_reason is not None
    assert (parsed.notes[0].decided_by, str(parsed.notes[0].decided_on)) == ("Marie", "2026-11-15")
    # What a person confirmed stays visible after the note it was confirmed on was replaced.
    assert (parsed.notes[4].status, parsed.notes[4].confirmed_by) == ("superseded", "Marie")
    assert parsed.notes[4].confirmed_at is not None and parsed.notes[4].note_ids == [1, 4]
    # A replaced note may say why or not: what replaced it says it.
    assert parsed.notes[2].status_reason is None


def test_a_file_in_an_old_form_is_refused_by_name() -> None:
    old = a_file()
    old["memories"] = old.pop("notes")

    message = the_refusal(old)

    assert "notes: Field required" in message
    assert "memories: Extra inputs are not permitted" in message

    # A node that has an id of its own and a parent_id, as before epics, stories and tasks were
    # numbered each on their own.
    before = a_file(nodes=[node("epic", 1)])
    before["nodes"][0]["id"] = before["nodes"][0].pop("number")
    before["nodes"][0]["parent_id"] = before["nodes"][0].pop("parent")

    message = the_refusal(before)

    for problem in (
        "nodes[0].number: Field required",
        "nodes[0].parent: Field required",
        "nodes[0].id: Extra inputs are not permitted",
        "nodes[0].parent_id: Extra inputs are not permitted",
    ):
        assert problem in message, (problem, message)

    # Decisions and facts are not part of the format.
    mixed = a_file()
    mixed["decisions"] = [{"id": 1}]
    mixed["facts"] = [{"id": 1}]
    message = the_refusal(mixed)
    assert "decisions: Extra inputs are not permitted" in message
    assert "facts: Extra inputs are not permitted" in message

    # Older export format versions (1-5) are refused plainly.
    for old_version in (1, 2, 3, 4, 5):
        older = a_file()
        older["version"] = old_version
        message = the_refusal(older)
        assert "version: Input should be 6" in message


def test_a_file_of_the_current_version_has_to_say_as_what_each_name_was_recorded() -> None:
    current = a_file(
        nodes=[node("epic", 1)], notes=[note(1), note(2)], events=[event(1, node="epic:1")]
    )
    del current["notes"][1]["recorded_by_kind"]
    del current["events"][0]["actor_kind"]

    message = the_refusal(current)

    assert "notes[1].recorded_by_kind: Field required" in message
    assert "events[0].actor_kind: Field required" in message
    assert "notes[0]" not in message


def test_a_kind_that_is_neither_a_person_nor_an_agent_is_refused() -> None:
    robot = a_file(nodes=[node("epic", 1)], events=[event(1, node="epic:1", actor_kind="robot")])

    assert "events[0].actor_kind: Input should be 'person', 'agent' or 'unknown'" in the_refusal(
        robot
    )


def test_everything_wrong_with_the_shape_is_listed_at_once_without_the_values_found() -> None:
    bad = a_file(nodes=[node("epic", 1), node("story", 2, "epic:1"), node("task", 3, "story:2")])
    bad["nodes"][0]["number"] = "seven-secret-value"
    bad["nodes"][1]["status"] = "finished"
    bad["nodes"][1]["parent"] = "Epic:1"
    bad["nodes"][2]["parent"] = "2"
    del bad["nodes"][2]["title"]
    bad["nodes"][2]["extra"] = 1
    bad["nodes"][2]["created_at"] = "2026-11-15T10:00:00"
    bad["nodes"][2]["start_on"] = "next week"
    bad["nodes"][2]["version"] = True
    del bad["links"]
    bad["version"] = 7

    message = the_refusal(bad)

    ref_problem = "must name an epic, story or task by its kind and number, such as story:12"
    for problem in (
        "nodes[0].number: Input should be a valid integer",
        "nodes[1].status: Input should be",
        f"nodes[1].parent: {ref_problem}",
        f"nodes[2].parent: {ref_problem}",
        "nodes[2].title: Field required",
        "nodes[2].extra: Extra inputs are not permitted",
        "nodes[2].created_at: must say its time zone",
        "nodes[2].start_on: must be a real day written as YYYY-MM-DD",
        "nodes[2].version: Input should be a valid integer",
        "links: Field required",
        "version: Input should be 6",
    ):
        assert problem in message, (problem, message)
    assert "seven-secret-value" not in message and "next week" not in message
    assert "Use a file written by export_graph" in message


def test_every_place_that_holds_a_ref_wants_a_real_one() -> None:
    bad = a_file(
        links=[link(1, "story:0", "sprint:3", "relates_to")],
        rules=[rule(1, "12")],
        notes=[note(1, nodes=["task:01"])],
        events=[event(1, node="node:1", other_node=7)],
    )

    message = the_refusal(bad)

    for place in (
        "links[0].source",
        "links[0].target",
        "rules[0].node",
        "notes[0].nodes[0]",
        "events[0].node",
        "events[0].other_node",
    ):
        assert f"{place}: " in message, (place, message)


def test_a_file_that_is_not_an_export_and_days_that_are_not_days_are_refused() -> None:
    assert "format: Field required" in the_refusal({"nodes": []})
    assert "format: Input should be 'vrum-export'" in the_refusal({**a_file(), "format": "x"})
    for days in ("2026-02-30", "2026-1-5", "15/11/2026", ""):
        message = the_refusal(a_file(nodes=[node("epic", 1, start_on=days)]))
        assert "nodes[0].start_on: must be a real day written as YYYY-MM-DD" in message, days
    bad_day = the_refusal(a_file(notes=[note(1, decided_by="Marie", decided_on="next tuesday")]))
    assert "notes[0].decided_on: must be a real day written as YYYY-MM-DD" in bad_day


def test_only_the_first_twenty_problems_are_listed() -> None:
    bad = a_file(nodes=[node("epic", number, title=number) for number in range(1, 31)])

    message = the_refusal(bad)

    assert message.count("Input should be a valid string") == 20
    assert "; and 10 more" in message


def test_everything_a_file_points_at_has_to_be_in_it() -> None:
    cases: list[tuple[dict[str, Any], str]] = [
        (
            a_file(nodes=[node("story", 2, "epic:1")]),
            "nodes[0] (story:2): parent=epic:1 is not in the file",
        ),
        (
            a_file(nodes=[node("epic", 1), node("task", 2, "epic:1")]),
            "nodes[1] (task:2): parent=epic:1 must be a story",
        ),
        (
            a_file(nodes=[node("epic", 1), node("story", 2, "story:2")]),
            "nodes[1] (story:2): parent=story:2 must be an epic",
        ),
        # Epic 1 exists, and a story's parent has to be an epic that exists: story 1 is not epic 1.
        (
            a_file(nodes=[node("epic", 1), node("story", 2, "epic:3")]),
            "parent=epic:3 is not in",
        ),
        (a_file(nodes=[node("epic", 1, "epic:5")]), "an epic has no parent"),
        (a_file(nodes=[node("epic", 1), node("story", 2)]), "a story needs a parent"),
        (a_file(nodes=[node("epic", 1), node("epic", 1)]), "nodes[1]: epic:1 is used twice"),
        (
            a_file(links=[link(1, "epic:1", "epic:2", "relates_to")], nodes=[node("epic", 1)]),
            "links[0] (id 1): target=epic:2 is not in the file",
        ),
        (
            a_file(links=[link(1, "story:1", "epic:1", "relates_to")], nodes=[node("epic", 1)]),
            "links[0] (id 1): source=story:1 is not in the file",
        ),
        (
            a_file(rules=[rule(1, "epic:1")], nodes=[node("epic", 1)]),
            "rules[0] (id 1): node=epic:1 must be a story or a task",
        ),
        (
            a_file(rules=[rule(1, "story:1")], nodes=[node("epic", 1)]),
            "rules[0] (id 1): node=story:1 is not in the file",
        ),
        (
            a_file(rules=[rule(1, "task:1")], nodes=[node("epic", 1)]),
            "rules[0] (id 1): node=task:1 is not in the file",
        ),
        (
            a_file(notes=[note(1, nodes=["story:9"], rule_ids=[8])]),
            "notes[0] (id 1): nodes=story:9 is not in the file",
        ),
        (
            a_file(notes=[note(1, nodes=["story:9"], rule_ids=[8])]),
            "notes[0] (id 1): rule_ids=8 is not in the file",
        ),
        (
            a_file(notes=[note(1, nodes=["task:9"], rule_ids=[8], note_ids=[7])]),
            "notes[0] (id 1): note_ids=7 is not in the file",
        ),
        (a_file(notes=[note(1), note(1)]), "notes[1]: id 1 is used twice"),
        (
            a_file(events=[event(1, node="epic:3")]),
            "events[0] (id 1): node=epic:3 is not in the file",
        ),
        (
            a_file(events=[event(1, node="epic:1", other_node="task:1")], nodes=[node("epic", 1)]),
            "events[0] (id 1): other_node=task:1 is not in the file",
        ),
        (
            a_file(events=[event(1, action="note_recorded", note_id=3)]),
            "events[0] (id 1): note_id=3 is not in the file",
        ),
        (
            a_file(
                notes=[note(1)],
                events=[event(1, action="note_linked", note_id=1, other_note_id=3)],
            ),
            "events[0] (id 1): other_note_id=3 is not in the file",
        ),
    ]
    for document, expected in cases:
        assert expected in the_refusal(document), expected


def test_a_replacement_is_a_later_note() -> None:
    cases: list[tuple[dict[str, Any], str]] = [
        (
            a_file(notes=[note(1), note(2, status="superseded", superseded_by_id=1)]),
            "notes[1] (id 2): superseded_by_id=1 must be a later id than 2",
        ),
        (
            a_file(notes=[note(1, status="superseded", superseded_by_id=1)]),
            "superseded_by_id=1 must be a later id than 1",
        ),
        (
            a_file(notes=[note(1, status="superseded", superseded_by_id=5)]),
            "notes[0] (id 1): superseded_by_id=5 is not a note in the file",
        ),
        (
            a_file(notes=[note(1, status="superseded")]),
            "a superseded note needs superseded_by_id",
        ),
        (
            a_file(notes=[note(1, superseded_by_id=2), note(2)]),
            "superseded_by_id is only for a superseded note, and this one is unconfirmed",
        ),
        (
            a_file(
                notes=[
                    note(1, status="withdrawn", status_reason="x", superseded_by_id=2),
                    note(2),
                ]
            ),
            "superseded_by_id is only for a superseded note, and this one is withdrawn",
        ),
        # What a note replaced is derived, so it has to agree with what the others say.
        (
            a_file(notes=[note(1, status="superseded", superseded_by_id=2), note(2)]),
            "notes[1] (id 2): supersedes_ids must be the notes whose superseded_by_id is 2, [1]",
        ),
        (
            a_file(notes=[note(1, supersedes_ids=[2]), note(2)]),
            "notes[0] (id 1): supersedes_ids must be the notes whose superseded_by_id is 1, []",
        ),
    ]
    for document, expected in cases:
        assert expected in the_refusal(document), expected


def test_the_reason_a_note_was_given_up_is_there_exactly_when_it_is_needed() -> None:
    bad = a_file(
        notes=[
            note(1, status="withdrawn"),
            note(2, status="withdrawn", status_reason="   "),
            note(3, status_reason="Why"),
            note(4, status="superseded", superseded_by_id=5, status_reason="  "),
            # A note that was replaced needs no reason of its own, and may have one.
            note(5, status="superseded", superseded_by_id=6, supersedes_ids=[4]),
            note(
                6,
                status="superseded",
                superseded_by_id=7,
                supersedes_ids=[5],
                status_reason="Newer",
            ),
            note(7, supersedes_ids=[6]),
        ]
    )

    message = the_refusal(bad)

    for problem in (
        "notes[0] (id 1): a withdrawn note needs status_reason, why it was given up",
        "notes[1] (id 2): a withdrawn note needs status_reason, why it was given up",
        "notes[2] (id 3): status_reason is only for a note that has ended",
        "notes[3] (id 4): status_reason must say why, or be left empty",
    ):
        assert problem in message, (problem, message)
    assert "notes[4]" not in message and "notes[5]" not in message and "notes[6]" not in message


def test_a_confirmation_names_a_person_and_a_time_or_neither() -> None:
    bad = a_file(
        notes=[
            note(1, confirmed_by="Marie"),
            note(2, confirmed_at=WHEN),
            note(3, confirmed_by="  ", confirmed_at=WHEN),
            note(4, confirmed_by="Marie", confirmed_at=WHEN),
        ]
    )

    message = the_refusal(bad)

    together = "confirmed_by and confirmed_at go together"
    assert f"notes[0] (id 1): {together}" in message
    assert f"notes[1] (id 2): {together}" in message
    assert "notes[2] (id 3): confirmed_by must be the name of a person" in message
    assert "notes[3]" not in message


def test_a_note_in_force_is_confirmed_exactly_when_somebody_confirmed_it() -> None:
    bad = a_file(
        notes=[
            note(1, status="confirmed"),
            note(2, status="unconfirmed", confirmed_by="Marie", confirmed_at=WHEN),
            note(3, status="confirmed", confirmed_by="Marie", confirmed_at=WHEN),
            note(4),
            # A note that ended keeps who confirmed it, or has nobody: both are as it stood.
            note(5, status="withdrawn", status_reason="x", confirmed_by="Marie", confirmed_at=WHEN),
            note(6, status="withdrawn", status_reason="x"),
        ]
    )

    message = the_refusal(bad)

    assert "notes[0] (id 1): a confirmed note needs confirmed_by and confirmed_at" in message
    assert "notes[1] (id 2): an unconfirmed note has no confirmed_by or confirmed_at" in message
    for fine in range(2, 6):
        assert f"notes[{fine}]" not in message


def test_a_decision_names_who_decided_it_and_when_or_nothing_does() -> None:
    bad = a_file(
        notes=[
            note(1, decided_by="Marie"),
            note(2, decided_on="2026-11-15"),
            note(3, decided_by="  ", decided_on="2026-11-15"),
            note(4, decided_by="Marie", decided_on="2026-11-15"),
        ]
    )

    message = the_refusal(bad)

    together = "decided_by and decided_on go together"
    assert f"notes[0] (id 1): {together}" in message
    assert f"notes[1] (id 2): {together}" in message
    assert "notes[2] (id 3): decided_by must be the name of a person" in message
    assert "notes[3]" not in message


def test_a_note_says_something_and_why_and_is_not_about_itself() -> None:
    bad = a_file(
        notes=[
            note(1, text="  "),
            note(2, why=""),
            note(3, recorded_by=" "),
            note(4, note_ids=[4]),
        ]
    )

    message = the_refusal(bad)

    assert "notes[0] (id 1): text cannot be empty" in message
    assert "notes[1] (id 2): why cannot be empty" in message
    assert "notes[2] (id 3): recorded_by cannot be empty" in message
    assert "notes[3] (id 4): a note cannot be about itself" in message


def test_an_event_has_a_known_action_and_names_what_it_is_about() -> None:
    unknown = the_refusal(a_file(events=[event(1, node="epic:1", action="belief_confirmed")]))
    assert "events[0].action: Input should be" in unknown
    # The words for decisions and facts are not the words of a file of the current version.
    for old in ("decision_recorded", "fact_linked"):
        assert "events[0].action: Input should be" in the_refusal(
            a_file(events=[event(1, node="epic:1", action=old)])
        )

    bad = a_file(
        nodes=[node("epic", 1)],
        rules=[rule(1, None)],
        notes=[note(1), note(2)],
        events=[
            event(1, action="note_recorded"),
            event(2, action="rule_updated", rule_id=1, note_id=1),
            event(3, action="update", node="epic:1", note_id=1),
            event(4, action="claimed"),
            event(5, action="rule_updated", rule_id=1, other_note_id=2),
            event(6, action="update", node="epic:1", other_note_id=2),
            event(7, action="definition_of_done_changed", node="epic:1"),
        ],
    )

    message = the_refusal(bad)

    for problem in (
        "events[0] (id 1): the action 'note_recorded' needs note_id",
        "events[1] (id 2): the action 'rule_updated' needs rule_id, and no note_id or "
        "other_note_id",
        "events[2] (id 3): the action 'update' needs node, and no rule_id, note_id or "
        "other_note_id",
        "events[3] (id 4): the action 'claimed' needs node, and no rule_id, note_id or "
        "other_note_id",
        "events[4] (id 5): the action 'rule_updated' needs rule_id",
        "events[5] (id 6): the action 'update' needs node",
        "events[6] (id 7): the action 'definition_of_done_changed' needs no node, other_node, "
        "rule_id, note_id or other_note_id",
    ):
        assert problem in message, (problem, message)


def test_a_file_cut_short_or_changed_says_so_by_its_own_counts() -> None:
    cut = a_file(nodes=[node("epic", 1)])
    cut["counts"] = {"nodes": 2, "links": 0, "rules": 0, "notes": 0}

    message = the_refusal(cut)

    assert "counts.nodes says 2 but the file holds 1" in message
    assert "counts.events says None but the file holds 0" in message
    assert "cut short or was changed after it was written" in message
    # A file without counts is read as it is.
    assert check_document(copy.deepcopy(a_file(nodes=[node("epic", 1)])))


def test_a_secret_in_any_text_is_refused_by_field_and_not_repeated() -> None:
    secret = "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fG3hJ5"
    for document, field in (
        (a_file(nodes=[node("epic", 1, body=f"key {secret}")]), "nodes[0].body"),
        (a_file(notes=[note(1, why=secret)]), "notes[0].why"),
        (a_file(notes=[note(1, status_reason=secret)]), "notes[0].status_reason"),
    ):
        with pytest.raises(Refusal) as refusal:
            check_document(document)
        message = str(refusal.value)
        assert f"The {field} field contains what looks like a GitHub token" in message
        assert secret not in message and "nothing was changed" in message


def test_a_page_continues_after_the_last_item_of_the_one_before() -> None:
    # Nodes are placed by kind (epics, then stories, then tasks) and number, the rest by id.
    assert _cursor(None) == ("nodes", (0, 0))
    assert _cursor("nodes:epic:7") == ("nodes", (1, 7))
    assert _cursor("nodes:story:12") == ("nodes", (2, 12))
    assert _cursor("nodes:task:40") == ("nodes", (3, 40))
    assert _cursor("links:0") == ("links", (0,))
    assert _cursor("notes:0") == ("notes", (0,))
    assert _cursor("definition_of_done:0") == ("definition_of_done", (0,))
    assert _cursor("events:315") == ("events", (315,))
    assert _cursor_after("nodes", node("story", 12)) == "nodes:story:12"
    assert _cursor_after("rules", rule(5, None)) == "rules:5"
    assert _cursor_after("notes", note(9)) == "notes:9"
    wrong = ("nodes:200", "nodes:story:0", "nodes:", "rules:story:1", "decisions:3", "facts:1")
    for after in wrong + ("belief:3", "links:x"):
        with pytest.raises(Refusal) as refusal:
            _cursor(after)
        assert "after must be left empty for the first page" in str(refusal.value), after


def test_the_ticks_in_a_file_have_to_hang_together() -> None:
    nodes = [node("epic", 1), node("story", 1, "epic:1")]
    on_an_epic = a_file(nodes=nodes, checks=[check("epic:1", "a1b2c3d4")])
    assert "checks[0] (epic:1, item a1b2c3d4): node=epic:1 must be a story or a task" in (
        the_refusal(on_an_epic)
    )

    nowhere = a_file(nodes=nodes, checks=[check("task:9", "a1b2c3d4")])
    assert "checks[0] (task:9, item a1b2c3d4): node=task:9 is not in the file" in (
        the_refusal(nowhere)
    )

    twice = a_file(nodes=nodes, checks=[check("story:1", "a1b2c3d4"), check("story:1", "a1b2c3d4")])
    assert "checks[1] (story:1, item a1b2c3d4): this check is ticked twice" in the_refusal(twice)

    odd_key = a_file(nodes=nodes, checks=[check("story:1", "TESTS")])
    assert "item must be the check's key, 8 characters from 0-9 and a-f" in the_refusal(odd_key)

    no_how = a_file(nodes=nodes, checks=[check("story:1", "a1b2c3d4", evidence=None)])
    assert "a check ticked by an agent needs evidence" in the_refusal(no_how)

    blank = a_file(
        nodes=nodes,
        checks=[check("story:1", "a1b2c3d4", checked_by_kind="person", evidence="  ")],
    )
    assert "evidence must say how it was checked, or be left empty" in the_refusal(blank)

    counted = a_file(nodes=nodes, checks=[check("story:1", "a1b2c3d4")])
    counted["counts"] = {"nodes": 2, "links": 0, "rules": 0, "notes": 0, "events": 0}
    assert "counts.checks says None but the file holds 1" in the_refusal(counted)


def test_the_cursor_of_a_page_of_ticks_names_the_node_and_the_item() -> None:
    assert _cursor("checks:0") == ("checks", ("", ""))
    assert _cursor("checks:story:12:a1b2c3d4") == ("checks", ("story:12", "a1b2c3d4"))
    assert (
        _cursor_after("checks", {"node": "task:3", "item": "0f0f0f0f"}) == "checks:task:3:0f0f0f0f"
    )
    for wrong in ("checks:story:12", "checks:story:12:TESTS", "checks:12:a1b2c3d4"):
        with pytest.raises(Refusal):
            _cursor(wrong)


def test_the_definition_of_done_in_a_file_is_one_text_set_by_a_person() -> None:
    two = a_file(definition_of_done=[definition(), definition(3)])
    assert "a project has one definition of done, but the file holds 2" in the_refusal(two)

    robot = a_file(definition_of_done=[definition(updated_by_kind="agent")])
    assert "definition_of_done[0].updated_by_kind: Input should be 'person'" in the_refusal(robot)

    nobody = a_file(definition_of_done=[definition(updated_by=" ")])
    assert "definition_of_done[0]: updated_by must be the name of a person" in the_refusal(nobody)

    cut = a_file(definition_of_done=[definition()])
    cut["counts"] = {"nodes": 0, "links": 0, "rules": 0, "notes": 0, "events": 0}
    assert "counts.definition_of_done says None but the file holds 1" in the_refusal(cut)
