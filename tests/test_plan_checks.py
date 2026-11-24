"""M50, the parts that need no containers: finding a circle of waiting (the one piece of code that
create_plan's check and a drafted plan's check share), the words of a check, and when a proposal
to move work is called routine."""

from datetime import UTC, datetime

from vrum.placement import MoveScore, PlacementItem, PlacementState, _ordinal, routine_words
from vrum.plan_checks import circle_through, circles_among


def test_a_circle_is_found_through_the_links_that_close_it_and_named_along_the_way() -> None:
    graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
    assert circle_through(graph, "c", "a") == ["c", "a", "b", "c"]
    assert circle_through({"a": {"b"}, "b": {"c"}}, "a", "b") is None


def test_a_circle_is_reported_once_however_many_of_its_links_are_looked_at() -> None:
    edges = {("a", "b"), ("b", "c"), ("c", "a"), ("x", "a")}
    found = circles_among(edges, {"a", "b", "c", "x"})
    assert len(found) == 1 and set(found[0]) == {"a", "b", "c"}


def test_only_the_circles_through_the_plans_own_items_are_reported() -> None:
    edges = {("a", "b"), ("b", "a"), ("p", "q")}
    assert circles_among(edges, {"p", "q"}) == []
    assert len(circles_among(edges, {"a"})) == 1
    assert circles_among(set(), {"a"}) == []


def test_two_separate_circles_are_both_reported() -> None:
    edges = {("a", "b"), ("b", "a"), ("c", "d"), ("d", "c")}
    found = circles_among(edges, {"a", "c"})
    assert sorted(set(one) for one in found) == [{"a", "b"}, {"c", "d"}]


def test_ordinals_say_second_third_and_the_teens_correctly() -> None:
    assert [_ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 103, 111)] == [
        "1st",
        "2nd",
        "3rd",
        "4th",
        "11th",
        "12th",
        "13th",
        "21st",
        "22nd",
        "103rd",
        "111th",
    ]


def a_move(parent: str | None, refs: list[str]) -> PlacementState:
    return PlacementState(
        id=1,
        state="proposed",
        items=[PlacementItem(ref=ref, title="t", from_parent="epic:9") for ref in refs],
        parent=parent,
        parent_title=None,
        new_epic=None,
        reason="why",
        proposed_by="steward",
        proposed_by_kind="agent",
        proposed_at=datetime.now(UTC),
        decided_by=None,
        decided_at=None,
        decision_reason=None,
        applied=[],
        skipped=[],
        epic=None,
        scores=None,
        scores_note=None,
        routine=None,
        routine_why=None,
    )


def a_score(ref: str, **more: object) -> MoveScore:
    fields: dict[str, object] = {
        "ref": ref,
        "similarity": 0.8,
        "rank": 1,
        "ranked": 5,
        "best": "epic:3",
        "best_similarity": 0.8,
        "margin": 0.2,
        "sure": True,
        "warning": None,
        "note": None,
    }
    return MoveScore.model_validate({**fields, **more})


def test_a_move_is_routine_only_when_every_item_agrees_and_nothing_needs_weighing() -> None:
    move = a_move("epic:3", ["story:1", "story:2"])
    scores = [a_score("story:1"), a_score("story:2")]
    assert routine_words(move, scores, None) == (
        True,
        "E-3 is the clearly best parent for all 2 items.",
    )
    assert routine_words(a_move("epic:3", ["story:1"]), scores[:1], None)[1].endswith(
        "for the item."
    )


def test_a_move_is_not_routine_and_says_the_first_reason_why() -> None:
    move = a_move("epic:3", ["story:1", "story:2"])
    good = a_score("story:1")
    cases = [
        (a_score("story:2", rank=2), "E-3 ranks 2nd for S-2."),
        (a_score("story:2", rank=None), "E-3 cannot be ranked for S-2."),
        (a_score("story:2", sure=False), "For S-2, E-3 is not clearly ahead of the next parent."),
        (a_score("story:2", warning="S-2 is in progress."), "S-2 is in progress."),
        (a_score("story:2", note="No score."), "No score."),
    ]
    for bad, words in cases:
        assert routine_words(move, [good, bad], None) == (False, words)
    assert routine_words(move, [good, good], "Search by meaning is not possible now.") == (
        False,
        "Search by meaning is not possible now.",
    )
    new_epic = a_move(None, ["story:1"])
    ok, words = routine_words(new_epic, [], None)
    assert not ok and "new epic" in words
