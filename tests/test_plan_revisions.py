"""M51, the parts that need no containers: how two revisions of a plan differ, how items have
drifted from the revision a person reviewed, and how the comments of a request for changes are
checked and given the words they are about."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from vrum.common import Refusal
from vrum.notify_events import object_of, types_of
from vrum.plan_revisions import (
    PlanComment,
    PlanCommentInput,
    PlanFeedback,
    PlanRevisionInfo,
    blame_of,
    clean_approved,
    clean_comments,
    clean_summary,
    diff_revisions,
    drift_between,
    quoted_of,
)


def item(ref: str, **more: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ref": ref,
        "kind": ref.split(":")[0],
        "parent": None if ref.startswith("epic") else "epic:1",
        "title": f"Title of {ref}",
        "body": "",
        "priority": "normal",
        "size": None,
        "start_on": None,
        "target_on": None,
        "links": [],
        "criteria": [],
    }
    return {**base, **more}


def test_two_revisions_differ_by_items_added_removed_and_the_parts_that_changed() -> None:
    old = [item("epic:1"), item("story:2", size="m"), item("task:3"), item("task:4")]
    new = [
        item("epic:1"),
        item("story:2", size="l", title="A new title"),
        item("task:3", links=[{"link_type": "needs_first", "target": "task:5"}]),
        item("task:5"),
    ]
    found = {change.ref: change for change in diff_revisions(old, new)}
    assert set(found) == {"story:2", "task:3", "task:4", "task:5"}
    assert (found["task:4"].change, found["task:5"].change) == ("removed", "added")
    story = found["story:2"]
    assert story.change == "changed" and story.fields == ["title", "size"]
    assert story.before == {"title": "Title of story:2", "size": "m"}
    assert found["task:3"].fields == ["links"] and found["task:3"].before == {"links": []}
    assert diff_revisions(old, old) == []


def test_changes_are_listed_epics_first_and_by_number_not_alphabetically() -> None:
    old: list[dict[str, Any]] = []
    new = [item("task:10"), item("epic:2"), item("task:9"), item("story:1")]
    assert [c.ref for c in diff_revisions(old, new)] == [
        "epic:2",
        "story:1",
        "task:9",
        "task:10",
    ]


def test_a_criterion_counts_by_its_words_and_its_status_alone_is_not_a_change() -> None:
    met = {"id": 4, "title": "Passes the test", "body": "", "status": "met"}
    open_ = {**met, "status": "open"}
    same = diff_revisions([item("task:3", criteria=[open_])], [item("task:3", criteria=[met])])
    assert same == []
    reworded = diff_revisions(
        [item("task:3", criteria=[open_])],
        [item("task:3", criteria=[{**open_, "title": "Passes the other test"}])],
    )
    assert [c.fields for c in reworded] == [["criteria"]]


def test_drift_is_what_changed_outside_a_revision_and_ignores_links_that_make_nothing_wait() -> (
    None
):
    reviewed = [item("epic:1"), item("story:2"), item("task:3"), item("task:4")]
    live = [
        item("epic:1"),
        item("story:2", title="Renamed"),
        item("task:3", links=[{"link_type": "relates_to", "target": "story:9"}]),
        item("task:4", links=[{"link_type": "needs_first", "target": "task:3"}]),
    ]
    found = {d.ref: d.fields for d in drift_between(live, reviewed, {"epic:1"})}
    assert found == {"epic:1": ["dropped"], "story:2": ["title"], "task:4": ["links"]}
    assert drift_between(reviewed, reviewed, set()) == []
    assert drift_between(live[:1], reviewed[:2], set())[0].fields == ["missing"]


def test_a_comment_keeps_the_words_it_was_about() -> None:
    story = item(
        "story:2",
        size="m",
        links=[{"link_type": "needs_first", "target": "story:1"}],
        criteria=[{"id": 1, "title": "It works", "body": "", "status": "open"}],
    )
    assert quoted_of(story, None) == "Title of story:2"
    assert quoted_of(story, "size") == "m"
    assert quoted_of(story, "start_on") == "none"
    assert quoted_of(story, "links") == "needs_first story:1"
    assert quoted_of(story, "criteria") == "It works"
    assert quoted_of(item("task:3"), "links") == "no links"
    made = clean_comments(
        [PlanCommentInput(item="STORY:2", field="size", text="  Too big.  ")], [story]
    )
    assert [(c.item, c.field, c.text, c.quoted) for c in made] == [
        ("story:2", "size", "Too big.", "m")
    ]
    assert clean_comments(None, [story]) == []


def test_every_problem_of_a_request_is_listed_and_nothing_is_kept() -> None:
    reviewed = [item("story:2")]
    wanted = [
        PlanCommentInput(item="story:9", text="Where is this?"),
        PlanCommentInput(item="story:2", field="colour", text="Blue."),
        PlanCommentInput(item="story:2", text="   "),
        PlanCommentInput(item="story:2", text="x" * 2001),
    ]
    with pytest.raises(Refusal) as refusal:
        clean_comments(wanted, reviewed)
    said = str(refusal.value)
    assert "because of 4 problems" in said and "Nothing was changed." in said
    assert "story:9 is not an item of the revision reviewed" in said
    assert "field must be one of" in said and "say what you want" in said
    assert "at most 2,000 characters" in said
    with pytest.raises(Refusal, match="at most 100 comments"):
        clean_comments([PlanCommentInput(item="story:2", text="a")] * 101, reviewed)


def test_a_summary_is_needed_and_not_too_long() -> None:
    assert clean_summary("  Cover the busiest stations first.  ") == (
        "Cover the busiest stations first."
    )
    with pytest.raises(Refusal, match="Say in summary"):
        clean_summary("   ")
    with pytest.raises(Refusal, match="at most 2,000"):
        clean_summary("x" * 2001)


def test_a_revision_is_news_like_a_proposal_and_a_request_like_a_decision() -> None:
    assert types_of("plan_revised", None, {"plan": 3, "revision": 2}) == ["plan.proposed"]
    assert types_of("plan_changes_requested", None, {"plan": 3}) == ["plan.decided"]
    assert object_of("plan_revised", 9, {"plan": 3}) == "plan:3"
    assert object_of("plan_changes_requested", 9, {"plan": 3}) == "plan:3"


NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def revision(number: int, by: str = "planner", note: str | None = None) -> PlanRevisionInfo:
    return PlanRevisionInfo(
        revision=number,
        by=by,
        by_kind="agent",
        at=NOW + timedelta(hours=number),
        note=note,
        items=0,
        removed=[],
        feedback=None,
    )


def feedback(
    number: int, on: int, approved: list[str] | None = None, asked: list[str] | None = None
) -> PlanFeedback:
    return PlanFeedback(
        id=number,
        revision=on,
        by="Priya",
        by_kind="person",
        at=NOW + timedelta(hours=on, minutes=30),
        summary="Look again.",
        comments=[
            PlanComment(item=ref, field=None, text="Change it.", quoted="x") for ref in asked or []
        ],
        approved=approved or [],
    )


def test_each_line_says_who_last_changed_it_and_in_which_revision() -> None:
    first = [item("epic:1"), item("story:2", size="m"), item("task:3")]
    second = [item("epic:1"), item("story:2", size="l"), item("task:3"), item("task:4")]
    third = [item("epic:1"), item("story:2", size="l"), item("task:4", title="Renamed")]
    rows = [revision(1, "drafter"), revision(2, "reviser", "Sized it"), revision(3, "third")]
    found = {one.ref: one for one in blame_of({1: first, 2: second, 3: third}, rows, [], 3)}
    assert [(r, found[r].change, found[r].revision, found[r].by) for r in found] == [
        ("epic:1", "added", 1, "drafter"),
        ("story:2", "changed", 2, "reviser"),
        ("task:3", "removed", 3, "third"),
        ("task:4", "changed", 3, "third"),
    ]
    assert found["story:2"].note == "Sized it"
    assert all(one.review.state == "none" for one in found.values())


def test_an_approval_stands_while_the_item_is_as_it_was_and_goes_stale_when_it_changes() -> None:
    first = [item("epic:1"), item("story:2"), item("task:3"), item("task:4")]
    second = [item("epic:1"), item("story:2"), item("task:3", size="s"), item("task:4")]
    rows = [revision(1), revision(2)]
    said = [feedback(10, 1, approved=["story:2", "task:3"], asked=["task:4"])]
    found = {one.ref: one.review for one in blame_of({1: first, 2: second}, rows, said, 2)}
    assert found["story:2"].state == "approved" and found["story:2"].revision == 1
    assert found["story:2"].by == "Priya" and found["story:2"].feedback == 10
    # Changed since it was approved: it needs a fresh look.
    assert found["task:3"].state == "changed" and found["task:3"].revision == 1
    # Asked for a change and not changed yet.
    assert found["task:4"].state == "asked"
    assert found["epic:1"].state == "none"


def test_the_latest_word_about_an_item_is_the_one_that_counts() -> None:
    items = [item("epic:1"), item("task:2")]
    rows = [revision(1), revision(2)]
    said = [feedback(10, 1, asked=["task:2"]), feedback(11, 2, approved=["task:2"])]
    found = blame_of({1: items, 2: items}, rows, said, 2)
    assert {one.ref: one.review.state for one in found}["task:2"] == "approved"
    later = [feedback(10, 1, approved=["task:2"]), feedback(11, 2, asked=["task:2"])]
    found = blame_of({1: items, 2: items}, rows, later, 2)
    assert {one.ref: one.review.state for one in found}["task:2"] == "asked"


def test_an_item_a_revision_took_out_can_be_agreed_to_or_asked_to_come_back() -> None:
    first = [item("epic:1"), item("task:2"), item("task:3")]
    second = [item("epic:1")]
    rows = [revision(1), revision(2, note="Took out two")]
    said = [
        feedback(10, 1, asked=["task:2"]),
        feedback(11, 2, approved=["task:3"]),
    ]
    found = {one.ref: one for one in blame_of({1: first, 2: second}, rows, said, 2)}
    assert found["task:3"].change == "removed" and found["task:3"].review.state == "approved"
    # A word from before the removal is about the item as it was: it no longer stands.
    assert found["task:2"].change == "removed" and found["task:2"].review.state == "none"
    wants_it_back = [feedback(12, 2, asked=["task:2"])]
    again = {one.ref: one for one in blame_of({1: first, 2: second}, rows, wants_it_back, 2)}
    assert again["task:2"].review.state == "asked"


def test_the_approved_items_are_checked_with_the_comments_and_each_is_kept_once() -> None:
    reviewed = [item("epic:1"), item("story:2"), item("task:3")]
    gone = [item("task:9")]
    asked = [PlanComment(item="task:3", field=None, text="Size it", quoted="x")]
    assert clean_approved(["task:9", " Story:2 ", "story:2", "epic:1"], asked, reviewed, gone) == [
        "epic:1",
        "story:2",
        "task:9",
    ]
    assert clean_approved(None, asked, reviewed) == []
    with pytest.raises(Refusal) as raised:
        clean_approved(["task:3", "task:77"], asked, reviewed, gone)
    words = str(raised.value)
    assert "2 problems" in words and "task:77" in words
    assert "task:3 is approved and also has a comment" in words
    assert "Nothing was changed." in words


def test_a_comment_can_be_about_an_item_the_revision_took_out() -> None:
    reviewed = [item("epic:1")]
    gone = [item("task:9", title="Buy a tarp")]
    made = clean_comments(
        [PlanCommentInput(item="task:9", field=None, text="Keep it.")], reviewed, gone
    )
    assert made[0].quoted == "Buy a tarp"
    with pytest.raises(Refusal):
        clean_comments([PlanCommentInput(item="task:9", field=None, text="Keep it.")], reviewed)
