"""How an epic, story or task is named (`epic:3`, `story:12`, `task:40`), without any containers:
what is accepted, what is refused and how it is said, the order references sort in, and how a plan
tells the key of one of its own items from the reference of one that exists."""

import pytest
from pydantic import TypeAdapter

from vrum.common import (
    Ref,
    Refusal,
    is_node_ref,
    kind_rank,
    missing_node,
    node_order,
    parse_node_ref,
    parse_node_refs,
    split_page,
    stale_version,
)
from vrum.graph import after_position
from vrum.workflow import PlanItem, PlanLink, PlanRule, _plan_problems


def test_a_reference_is_a_kind_a_colon_and_a_number_in_one_written_form() -> None:
    assert parse_node_ref("story:12", "node") == "story:12"
    assert parse_node_ref("  Epic:3 ", "node") == "epic:3"
    assert parse_node_refs(["task:1", "TASK:1", "epic:2", "task:1"], "nodes") == [
        "task:1",
        "epic:2",
    ]
    assert parse_node_refs(None, "nodes") == []


@pytest.mark.parametrize(
    "written",
    ["12", 12, "story:0", "story:012", "story:-1", "sprint:3", "story", "story:", ":3", "", None],
)
def test_anything_else_is_refused_in_plain_words_naming_the_argument(written: object) -> None:
    with pytest.raises(Refusal) as refusal:
        parse_node_ref(written, "parent")
    message = str(refusal.value)
    assert message.startswith("parent must name an epic, story or task by its kind and number")
    assert "such as story:12" in message


def test_a_number_sent_as_a_reference_is_read_as_text_so_it_is_refused_by_the_same_words() -> None:
    adapter = TypeAdapter(Ref)
    assert adapter.validate_python("story:12") == "story:12"
    assert adapter.validate_python(12) == "12"
    with pytest.raises(Refusal, match="kind and number"):
        parse_node_ref(adapter.validate_python(12), "node")
    # Something that is not a number or text is still a type error, not read as text.
    with pytest.raises(ValueError, match="valid string"):
        adapter.validate_python({"a": 1})


def test_epics_sort_before_stories_and_stories_before_tasks_each_by_number() -> None:
    refs = ["task:1", "story:10", "epic:2", "story:9", "epic:10", "task:2"]
    assert sorted(refs, key=node_order) == [
        "epic:2",
        "epic:10",
        "story:9",
        "story:10",
        "task:1",
        "task:2",
    ]
    assert [kind_rank(kind) for kind in ("epic", "story", "task")] == [1, 2, 3]


def test_a_page_of_nodes_continues_after_the_position_of_the_last_one() -> None:
    assert after_position(None) == {"after_rank": 0, "after_number": 0}
    assert after_position("story:12") == {"after_rank": 2, "after_number": 12}
    rows = [{"ref": f"story:{n}", "id": n} for n in (1, 2, 3)]
    assert split_page(rows, 2, key="ref") == (rows[:2], "story:2")
    assert split_page(rows[:2], 2, key="ref") == (rows[:2], None)
    assert split_page(rows, 2) == (rows[:2], 2)


def test_what_names_nothing_and_a_stale_version_say_which_item() -> None:
    assert str(missing_node("story:12")) == (
        "There is no story 12 in this project. Use list_nodes to find the one you want."
    )
    assert str(stale_version("Node", "get_node", "task:4", 3, 2)).startswith(
        "task:4 is at version 3, but you sent version 2."
    )
    assert str(stale_version("Rule", "get_rule", 7, 3, 2)).startswith(
        "Rule 7 is at version 3, but you sent version 2."
    )


def item(key: str, kind: str, parent: str | None = None) -> PlanItem:
    return PlanItem(key=key, kind=kind, title=f"{key} title", parent=parent)


def test_a_plan_names_its_own_items_by_keys_and_existing_ones_by_reference() -> None:
    items = [item("e", "epic"), item("s", "story", "e"), item("t", "task", "story:7")]
    links = [PlanLink(source="t", target="Task:2", link_type="needs_first")]
    rules = [
        PlanRule(title="A rule", node="s"),
        PlanRule(title="On a task", node="t"),
        PlanRule(title="Another", node="story:7"),
        PlanRule(title="On an old task", node="task:2"),
    ]
    assert _plan_problems(items, links, rules, {"story:7", "task:2"}, set()) == []


def test_a_plan_key_that_looks_like_a_reference_is_refused() -> None:
    assert is_node_ref("Story:12") and not is_node_ref("login-story")
    problems = _plan_problems([item("story:12", "epic")], [], [], set(), set())
    assert len(problems) == 1
    assert "looks like the reference of an existing item" in problems[0]


def test_a_plan_says_what_is_wrong_with_each_reference_in_plain_words() -> None:
    items = [
        item("s", "story", "story:7"),
        item("t", "task", "epic:3"),
        item("u", "task", "nowhere"),
        item("v", "task", "story:99"),
        item("w", "task", None),
    ]
    links = [
        PlanLink(source="t", target="task:99", link_type="needs_first"),
        PlanLink(source="12", target="t", link_type="relates_to"),
    ]
    rules = [PlanRule(title="R", node="epic:3"), PlanRule(title="S", node="story:99")]
    problems = _plan_problems(items, links, rules, {"story:7", "epic:3"}, set())
    text = "\n".join(problems)
    assert "story:7 is a story, but a story belongs under an epic" in text
    assert "epic:3 is an epic, but a task belongs under a story" in text
    assert "parent 'nowhere' is not the key of any item in this plan" in text
    assert "there is no story 99 in this project to be its parent" in text
    assert "A task needs a parent story" in text
    assert "there is no task 99 in this project for the target" in text
    assert "the source '12' is not the key of any item in this plan" in text
    assert "epic:3 is an epic, and an acceptance criterion belongs to a story or a task" in text
    assert "there is no story 99 in this project to hold it" in text


def test_a_circle_of_waiting_through_an_existing_item_is_found_before_anything_is_created() -> None:
    items = [item("e", "epic"), item("s", "story", "e")]
    # story:7 already needs the new story, so the new story needing story:7 closes a circle.
    links = [PlanLink(source="s", target="story:7", link_type="needs_first")]
    edges = {("story:7", "s")}
    (problem,) = _plan_problems(items, links, [], {"story:7"}, edges)
    assert "circle of waiting" in problem
    assert "the key 's' needs existing story:7, which needs the key 's'" in problem
