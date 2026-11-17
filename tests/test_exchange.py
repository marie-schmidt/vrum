"""Reading a file that is to be imported (vrum.exchange.check_document): what is accepted, and how
what is not is refused. No database: the loading itself is proved against the real containers in
test_exchange_second_stack.py."""

import copy
from typing import Any

import pytest

from tests.documents import WHEN, a_file, decision, event, fact, link, node, rule
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
            node("story", 1, "epic:1"),
            node("task", 1, "story:1"),
        ],
        links=[link(1, "task:1", "story:1", "relates_to")],
        rules=[rule(1, "story:1")],
        decisions=[
            decision(
                1,
                nodes=["story:1"],
                rule_ids=[1],
                project_wide=True,
                confirmed_by="Marie",
                confirmed_at=WHEN,
            ),
            decision(2, status="withdrawn", status_reason="No longer true"),
            decision(3, status="superseded", superseded_by_id=4),
            decision(4),
        ],
        facts=[
            fact(1, nodes=["story:1", "epic:1"], rule_ids=[1], decision_ids=[1]),
            fact(
                2,
                status="superseded",
                superseded_by_id=3,
                status_reason="Newer",
                confirmed_by="Marie",
                confirmed_at=WHEN,
            ),
            fact(3),
            fact(4, status="withdrawn", status_reason="Was wrong"),
        ],
        events=[
            event(1, node="epic:1", after={"title": "epic 1"}),
            event(2, action="fact_recorded", fact_id=1),
            event(3, action="fact_linked", fact_id=1, node="story:1"),
            event(4, action="decision_withdrawn", decision_id=2),
            event(5, action="rule_created", rule_id=1, node="story:1"),
            event(6, action="link_added", node="task:1", other_node="story:1"),
        ],
    )
    whole["counts"] = {
        "nodes": 3,
        "links": 1,
        "rules": 1,
        "decisions": 4,
        "facts": 4,
        "events": 6,
    }

    parsed = check_document(whole)

    assert [n.status for n in parsed.nodes] == ["ready", "new", "new"]
    assert [(n.kind, n.number, n.parent) for n in parsed.nodes] == [
        ("epic", 1, None),
        ("story", 1, "epic:1"),
        ("task", 1, "story:1"),
    ]
    assert (parsed.links[0].source, parsed.links[0].target) == ("task:1", "story:1")
    assert parsed.rules[0].story == "story:1"
    assert parsed.decisions[0].nodes == ["story:1"] and parsed.facts[0].nodes == [
        "story:1",
        "epic:1",
    ]
    assert (parsed.events[5].node, parsed.events[5].other_node) == ("task:1", "story:1")
    assert str(parsed.nodes[0].start_on) == "2026-12-01"
    assert parsed.nodes[0].created_at.tzinfo is not None
    assert parsed.decisions[0].project_wide and parsed.decisions[1].status_reason is not None
    assert parsed.decisions[1].status == "withdrawn" and not parsed.decisions[3].project_wide
    # What a person confirmed stays visible after the fact it was confirmed on was replaced.
    assert (parsed.facts[1].status, parsed.facts[1].confirmed_by) == ("superseded", "Marie")
    assert parsed.facts[1].confirmed_at is not None
    assert parsed.facts[0].decision_ids == [1]


def test_a_file_in_an_old_form_is_refused_by_name() -> None:
    old = a_file()
    old["memories"] = old.pop("facts")

    message = the_refusal(old)

    assert "facts: Field required" in message
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
    bad["version"] = 2

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
        "version: Input should be 1",
    ):
        assert problem in message, (problem, message)
    assert "seven-secret-value" not in message and "next week" not in message
    assert "Use a file written by export_graph" in message


def test_every_place_that_holds_a_ref_wants_a_real_one() -> None:
    bad = a_file(
        links=[link(1, "story:0", "sprint:3", "relates_to")],
        rules=[rule(1, "12")],
        decisions=[decision(1, nodes=["task:01"])],
        facts=[fact(1, nodes=["story:12 "])],
        events=[event(1, node="node:1", other_node=7)],
    )

    message = the_refusal(bad)

    for place in (
        "links[0].source",
        "links[0].target",
        "rules[0].story",
        "decisions[0].nodes[0]",
        "facts[0].nodes[0]",
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
            "rules[0] (id 1): story=epic:1 must be a story",
        ),
        (
            a_file(rules=[rule(1, "story:1")], nodes=[node("epic", 1)]),
            "rules[0] (id 1): story=story:1 is not in the file",
        ),
        (
            a_file(decisions=[decision(1, nodes=["story:9"], rule_ids=[8])]),
            "nodes=story:9 is not in the file",
        ),
        (
            a_file(decisions=[decision(1, nodes=["story:9"], rule_ids=[8])]),
            "rule_ids=8 is not in the file",
        ),
        (
            a_file(facts=[fact(1, nodes=["task:9"], rule_ids=[8], decision_ids=[7])]),
            "facts[0] (id 1): decision_ids=7 is not in the file",
        ),
        (
            a_file(facts=[fact(1, rule_ids=[8])]),
            "facts[0] (id 1): rule_ids=8 is not in the file",
        ),
        (
            a_file(events=[event(1, node="epic:3")]),
            "events[0] (id 1): node=epic:3 is not in the file",
        ),
        (
            a_file(events=[event(1, node="epic:1", other_node="task:1")], nodes=[node("epic", 1)]),
            "events[0] (id 1): other_node=task:1 is not in the file",
        ),
        (
            a_file(events=[event(1, action="fact_recorded", fact_id=3)]),
            "events[0] (id 1): fact_id=3 is not in the file",
        ),
    ]
    for document, expected in cases:
        assert expected in the_refusal(document), expected


def test_a_replacement_is_a_later_record_of_the_same_kind() -> None:
    cases: list[tuple[dict[str, Any], str]] = [
        (
            a_file(
                facts=[fact(1), fact(2, status="superseded", superseded_by_id=1, status_reason="x")]
            ),
            "facts[1] (id 2): superseded_by_id=1 must be a later id than 2",
        ),
        (
            a_file(facts=[fact(1, status="superseded", superseded_by_id=1, status_reason="x")]),
            "superseded_by_id=1 must be a later id than 1",
        ),
        (
            a_file(facts=[fact(1, status="superseded", superseded_by_id=5, status_reason="x")]),
            "facts[0] (id 1): superseded_by_id=5 is not a fact in the file",
        ),
        (
            # Fact and decision ids are separate lists: decision 2 does not replace a fact.
            a_file(
                facts=[fact(1, status="superseded", superseded_by_id=2, status_reason="x")],
                decisions=[decision(2)],
            ),
            "superseded_by_id=2 is not a fact in the file",
        ),
        (
            a_file(
                decisions=[decision(1, status="superseded", superseded_by_id=2)], facts=[fact(2)]
            ),
            "decisions[0] (id 1): superseded_by_id=2 is not a decision in the file",
        ),
        (
            a_file(decisions=[decision(3, status="superseded", superseded_by_id=2), decision(2)]),
            "decisions[0] (id 3): superseded_by_id=2 must be a later id than 3",
        ),
        (
            a_file(facts=[fact(1, status="superseded", status_reason="x")]),
            "a superseded fact needs superseded_by_id",
        ),
        (
            a_file(decisions=[decision(1, status="superseded")]),
            "a superseded decision needs superseded_by_id",
        ),
        (
            a_file(facts=[fact(1, superseded_by_id=2), fact(2)]),
            "superseded_by_id is only for a superseded fact, and this one is active",
        ),
        (
            a_file(
                decisions=[
                    decision(1, status="withdrawn", status_reason="x", superseded_by_id=2),
                    decision(2),
                ]
            ),
            "superseded_by_id is only for a superseded decision, and this one is withdrawn",
        ),
    ]
    for document, expected in cases:
        assert expected in the_refusal(document), expected


def test_the_reason_a_record_ended_is_there_exactly_when_it_is_needed() -> None:
    bad = a_file(
        facts=[
            fact(1, status="superseded", superseded_by_id=2),
            fact(2, status="withdrawn"),
            fact(3, status="withdrawn", status_reason="   "),
            fact(4, status_reason="Why"),
        ],
        decisions=[
            decision(1, status="withdrawn"),
            decision(2, status="superseded", superseded_by_id=3, status_reason="Why"),
            decision(3, status_reason="Why"),
            # A decision that was superseded needs no reason of its own: its replacement says why.
            decision(4, status="superseded", superseded_by_id=5),
            decision(5),
        ],
    )

    message = the_refusal(bad)

    for problem in (
        "facts[0] (id 1): a superseded fact needs status_reason, why it ended",
        "facts[1] (id 2): a withdrawn fact needs status_reason, why it ended",
        "facts[2] (id 3): a withdrawn fact needs status_reason, why it ended",
        "facts[3] (id 4): status_reason is only for a fact that has ended, and this one is active",
        "decisions[0] (id 1): a withdrawn decision needs status_reason, why it ended",
        "decisions[1] (id 2): status_reason is only for a withdrawn decision, and this one is "
        "superseded",
        "decisions[2] (id 3): status_reason is only for a withdrawn decision, and this one is "
        "active",
    ):
        assert problem in message, (problem, message)
    assert "decisions[3]" not in message and "decisions[4]" not in message


def test_a_confirmation_names_a_person_and_a_time_or_neither() -> None:
    bad = a_file(
        facts=[
            fact(1, confirmed_by="Marie"),
            fact(2, confirmed_at=WHEN),
            fact(3, confirmed_by="  ", confirmed_at=WHEN),
        ],
        decisions=[
            decision(1, confirmed_by="Marie"),
            decision(2, confirmed_by="Marie", confirmed_at=WHEN),
        ],
    )

    message = the_refusal(bad)

    together = "confirmed_by and confirmed_at go together"
    assert f"facts[0] (id 1): {together}" in message
    assert f"facts[1] (id 2): {together}" in message
    assert "facts[2] (id 3): confirmed_by must be the name of a person" in message
    assert f"decisions[0] (id 1): {together}" in message
    assert "decisions[1]" not in message


def test_an_event_has_a_known_action_and_names_what_it_is_about() -> None:
    unknown = the_refusal(a_file(events=[event(1, node="epic:1", action="belief_confirmed")]))
    assert "events[0].action: Input should be" in unknown

    bad = a_file(
        nodes=[node("epic", 1)],
        rules=[rule(1, None)],
        decisions=[decision(1)],
        facts=[fact(1)],
        events=[
            event(1, action="fact_recorded"),
            event(2, action="decision_recorded", decision_id=1, fact_id=1),
            event(3, action="rule_updated", rule_id=1, fact_id=1),
            event(4, action="update", node="epic:1", fact_id=1),
            event(5, action="claimed"),
        ],
    )

    message = the_refusal(bad)

    for problem in (
        "events[0] (id 1): the action 'fact_recorded' needs fact_id",
        "events[1] (id 2): the action 'decision_recorded' needs decision_id, and no fact_id",
        "events[2] (id 3): the action 'rule_updated' needs rule_id, and no decision_id or fact_id",
        "events[3] (id 4): the action 'update' needs node, and no rule_id, decision_id or fact_id",
        "events[4] (id 5): the action 'claimed' needs node, and no rule_id, decision_id or fact_id",
    ):
        assert problem in message, (problem, message)


def test_a_file_cut_short_or_changed_says_so_by_its_own_counts() -> None:
    cut = a_file(nodes=[node("epic", 1)])
    cut["counts"] = {"nodes": 2, "links": 0, "rules": 0, "decisions": 0, "facts": 0}

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
        (a_file(facts=[fact(1, source=secret)]), "facts[0].source"),
        (a_file(decisions=[decision(1, status_reason=secret)]), "decisions[0].status_reason"),
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
    assert _cursor("events:315") == ("events", (315,))
    assert _cursor_after("nodes", node("story", 12)) == "nodes:story:12"
    assert _cursor_after("rules", rule(5, None)) == "rules:5"
    for wrong in ("nodes:200", "nodes:story:0", "nodes:", "rules:story:1", "belief:3", "links:x"):
        with pytest.raises(Refusal) as refusal:
            _cursor(wrong)
        assert "after must be left empty for the first page" in str(refusal.value), wrong
