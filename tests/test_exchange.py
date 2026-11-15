"""Reading a file that is to be imported (vrum.exchange.check_document): what is accepted, and how
what is not is refused. No database: the loading itself is proved against the real containers in
test_exchange_second_stack.py."""

import copy
from typing import Any

import pytest

from tests.documents import WHEN, a_file, decision, event, link, memory, node, rule
from vrum.common import Refusal
from vrum.exchange import Document, check_document


def the_refusal(document: dict[str, Any]) -> str:
    with pytest.raises(Refusal) as refusal:
        check_document(document)
    message = str(refusal.value)
    assert "nothing was imported" in message
    return message


def test_an_empty_file_and_a_whole_one_are_accepted() -> None:
    assert isinstance(check_document(a_file()), Document)
    whole = a_file(
        nodes=[
            node(1, "epic", None, start_on="2026-12-01", target_on="2026-12-31", status="ready"),
            node(2, "story", 1),
            node(3, "task", 2),
        ],
        links=[link(1, 3, 2, "relates_to")],
        rules=[rule(1, 2)],
        decisions=[decision(1, node_ids=[2], rule_ids=[1])],
        memories=[
            memory(1, "fact", node_ids=[2], decision_ids=[1]),
            memory(
                2,
                "belief",
                evidence=[{"type": "fact", "id": 1}, {"type": "decision", "id": 1}],
                confirmed_by="Marie",
                confirmed_at=WHEN,
            ),
        ],
        events=[event(1, node_id=1, after={"title": "epic 1"})],
    )
    whole["counts"] = {
        "nodes": 3,
        "links": 1,
        "rules": 1,
        "decisions": 1,
        "memories": 2,
        "events": 1,
    }

    parsed = check_document(whole)

    assert [n.status for n in parsed.nodes] == ["ready", "new", "new"]
    assert str(parsed.nodes[0].start_on) == "2026-12-01"
    assert parsed.nodes[0].created_at.tzinfo is not None
    assert parsed.memories[1].evidence[1].type == "decision"


def test_everything_wrong_with_the_shape_is_listed_at_once_without_the_values_found() -> None:
    bad = a_file(nodes=[node(1, "epic", None), node(2, "story", 1), node(3, "task", 2)])
    bad["nodes"][0]["id"] = "seven-secret-value"
    bad["nodes"][1]["status"] = "finished"
    del bad["nodes"][2]["title"]
    bad["nodes"][2]["extra"] = 1
    bad["nodes"][2]["created_at"] = "2026-11-15T10:00:00"
    bad["nodes"][2]["start_on"] = "next week"
    bad["nodes"][2]["version"] = True
    del bad["links"]
    bad["version"] = 2

    message = the_refusal(bad)

    for problem in (
        "nodes[0].id: Input should be a valid integer",
        "nodes[1].status: Input should be",
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


def test_a_file_that_is_not_an_export_and_days_that_are_not_days_are_refused() -> None:
    assert "format: Field required" in the_refusal({"nodes": []})
    assert "format: Input should be 'vrum-export'" in the_refusal({**a_file(), "format": "x"})
    for days in ("2026-02-30", "2026-1-5", "15/11/2026", ""):
        message = the_refusal(a_file(nodes=[node(1, "epic", None, start_on=days)]))
        assert "nodes[0].start_on: must be a real day written as YYYY-MM-DD" in message, days


def test_only_the_first_twenty_problems_are_listed() -> None:
    bad = a_file(nodes=[node(number, "epic", None, title=number) for number in range(1, 31)])

    message = the_refusal(bad)

    assert message.count("Input should be a valid string") == 20
    assert "; and 10 more" in message


def test_everything_a_file_points_at_has_to_be_in_it() -> None:
    cases: list[tuple[dict[str, Any], str]] = [
        (a_file(nodes=[node(2, "story", 1)]), "nodes[0] (id 2): parent_id=1 is not in the file"),
        (a_file(nodes=[node(1, "epic", None), node(2, "task", 1)]), "parent_id=1 must be a story"),
        (a_file(nodes=[node(1, "epic", None), node(2, "story", 3)]), "parent_id=3 is not in"),
        (a_file(nodes=[node(1, "epic", 5)]), "an epic has no parent"),
        (a_file(nodes=[node(1, "epic", None), node(2, "story", None)]), "a story needs a parent"),
        (a_file(nodes=[node(1, "epic", None), node(1, "epic", None)]), "id 1 is used twice"),
        (
            a_file(links=[link(1, 1, 2, "relates_to")], nodes=[node(1, "epic", None)]),
            "links[0] (id 1): target_id=2 is not in the file",
        ),
        (
            a_file(rules=[rule(1, 1)], nodes=[node(1, "epic", None)]),
            "rules[0] (id 1): story_id=1 must be a story",
        ),
        (
            a_file(decisions=[decision(1, node_ids=[9], rule_ids=[8], superseded_by_id=7)]),
            "node_ids=9 is not in the file",
        ),
        (
            a_file(decisions=[decision(1, promoted_from_id=1)], memories=[memory(1, "fact")]),
            "promoted_from_id=1 must be a belief",
        ),
        (
            a_file(memories=[memory(1, "fact", superseded_by_id=2), memory(2, "belief")]),
            "a fact can only be replaced by a fact",
        ),
        (
            a_file(
                memories=[
                    memory(1, "belief", evidence=[{"type": "fact", "id": 2}]),
                    memory(2, "belief"),
                ]
            ),
            "evidence fact=2 must be a fact",
        ),
        (
            a_file(memories=[memory(1, "belief", evidence=[{"type": "decision", "id": 4}])]),
            "evidence decision=4 is not in the file",
        ),
        (
            a_file(events=[event(1, node_id=3)]),
            "events[0] (id 1): node_id=3 is not in the file",
        ),
    ]
    for document, expected in cases:
        assert expected in the_refusal(document), expected


def test_a_file_cut_short_or_changed_says_so_by_its_own_counts() -> None:
    cut = a_file(nodes=[node(1, "epic", None)])
    cut["counts"] = {"nodes": 2, "links": 0, "rules": 0, "decisions": 0, "memories": 0}

    message = the_refusal(cut)

    assert "counts.nodes says 2 but the file holds 1" in message
    assert "counts.events says None but the file holds 0" in message
    assert "cut short or was changed after it was written" in message
    # A file without counts is read as it is.
    assert check_document(copy.deepcopy(a_file(nodes=[node(1, "epic", None)])))


def test_a_secret_in_any_text_is_refused_by_field_and_not_repeated() -> None:
    secret = "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fG3hJ5"
    for document, field in (
        (a_file(nodes=[node(1, "epic", None, body=f"key {secret}")]), "nodes[0].body"),
        (a_file(memories=[memory(1, "fact", source=secret)]), "memories[0].source"),
    ):
        with pytest.raises(Refusal) as refusal:
            check_document(document)
        message = str(refusal.value)
        assert f"The {field} field contains what looks like a GitHub token" in message
        assert secret not in message and "nothing was changed" in message
