"""Plan templates (M47) with no server: the shape, the variables, the values and the expansion."""

import re
import time
from typing import Any

import pytest

from vrum.plan_templates import (
    TemplateProblems,
    clean_name,
    expand,
    parse_template,
    sample_refs,
    sample_values,
)

A_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2

RELEASE: dict[str, Any] = {
    "description": "Everything a release needs",
    "variables": {
        "version": {"description": "The version", "pattern": "^[0-9.]+$", "max_length": 20},
        "owner": {"required": False, "default": "the team"},
        "channel": {"choices": ["stable", "beta"], "default": "stable"},
        "epic": {"type": "ref", "kind": "epic", "required": False},
    },
    "items": [
        {"key": "rel", "kind": "epic", "title": "Release {{version}}", "body": "{{owner}}"},
        {"key": "prep", "kind": "story", "title": "Prepare {{version}}", "parent": "rel"},
        {"key": "tag", "kind": "task", "title": "Tag {{version}}", "parent": "prep", "size": "xs"},
        {"key": "ship", "kind": "story", "title": "Ship to {{channel}}", "parent": "rel"},
    ],
    "links": [{"source": "ship", "target": "prep", "link_type": "needs_first"}],
    "rules": [{"title": "Notes name every change in {{version}}", "node": "prep"}],
}


def problems_of(template: Any) -> list[str]:
    with pytest.raises(TemplateProblems) as refused:
        parse_template(template)
    return refused.value.problems


def test_a_template_is_read_and_expanded_with_its_values() -> None:
    template = parse_template(RELEASE)
    items, links, rules = expand(template, {"version": "2.1"})
    assert [i["title"] for i in items] == [
        "Release 2.1",
        "Prepare 2.1",
        "Tag 2.1",
        "Ship to stable",
    ]
    assert items[0]["body"] == "the team" and items[2]["size"] == "xs"
    assert items[1]["parent"] == "rel" and "parent" not in items[0]
    assert links == [{"source": "ship", "target": "prep", "link_type": "needs_first"}]
    assert rules == [
        {"title": "Notes name every change in 2.1", "node": "prep", "body": "", "status": "open"}
    ]
    beta, _, _ = expand(template, {"version": "3.0", "channel": "beta", "owner": "Priya"})
    assert beta[3]["title"] == "Ship to beta" and beta[0]["body"] == "Priya"


def test_a_value_that_does_not_fit_is_refused_and_every_problem_is_listed() -> None:
    template = parse_template(RELEASE)
    with pytest.raises(TemplateProblems) as refused:
        expand(
            template,
            {"channel": "gamma", "nope": "x", "owner": "line\nbreak", "epic": "story:3"},
        )
    problems = refused.value.problems
    assert "'nope' is not a variable of this template." in problems
    assert "Variable 'version' is required: give a value for it." in problems
    assert any("channel" in p and "one of stable, beta" in p for p in problems)
    assert any("owner" in p and "one line" in p for p in problems)
    assert any("epic" in p and "must be a epic" in p for p in problems)
    with pytest.raises(TemplateProblems) as bad_pattern:
        expand(template, {"version": "two"})
    assert any("does not fit its pattern" in p for p in bad_pattern.value.problems)
    with pytest.raises(TemplateProblems) as long:
        expand(template, {"version": "1" * 21})
    assert any("longer than 20" in p for p in long.value.problems)
    with pytest.raises(TemplateProblems) as secret:
        expand(template, {"version": "1.0", "owner": A_TOKEN})
    assert A_TOKEN not in str(secret.value) and "looks like" in str(secret.value)


def test_a_template_that_is_not_well_made_lists_every_problem_at_once() -> None:
    bad = {
        "variables": {
            "Bad-Name": {},
            "pat": {"pattern": "(a+)+$"},
            "choice": {"choices": ["a"], "default": "b"},
            "ref": {"type": "ref", "choices": ["x"]},
            "kind": {"kind": "epic"},
        },
        "items": [{"key": "a", "kind": "epic", "title": "{{missing}} and {{Version}}"}],
    }
    problems = problems_of(bad)
    assert len(problems) >= 6
    text = "\n".join(problems)
    for wanted in (
        "Bad-Name",
        "a pattern is one character class",
        "the default is not one of its choices",
        "a reference has no choices or pattern",
        "kind is only for a variable of type ref",
        "{{missing}}",
        "{{Version}}",
    ):
        assert wanted in text, wanted
    assert problems_of("nope") == ["The template must be an object with its variables and items."]
    assert any("items" in p for p in problems_of({}))
    assert any(
        "Extra inputs" in p or "extra" in p.lower() for p in problems_of({"items": [], "run": "x"})
    )
    assert any("title" in p for p in problems_of({"items": [{"key": "a", "kind": "epic"}]}))


@pytest.mark.parametrize(
    "pattern", ["^[a-z0-9.-]+$", "[A-Z]{2,5}", "^\\d+$", "\\w*", "^[^ ]{1,20}$", "[0-9]+"]
)
def test_the_patterns_that_are_one_class_and_one_repeat_are_taken(pattern: str) -> None:
    parse_template(
        {
            "variables": {"v": {"pattern": pattern}},
            "items": [{"key": "a", "kind": "epic", "title": "{{v}}"}],
        }
    )


@pytest.mark.parametrize(
    "pattern",
    [
        "(a+)+$",
        "(a|aa)+",
        "a+a+",
        "[a-z]+[a-z]+",
        ".*.*x",
        "[a-z]+(?=x)",
        "(?:a*)*",
        "\\1",
        "[a-z]++",
        "",
    ],
)
def test_a_pattern_that_could_make_the_server_work_hard_is_refused(pattern: str) -> None:
    assert any(
        "pattern" in p
        for p in problems_of(
            {
                "variables": {"v": {"pattern": pattern}},
                "items": [{"key": "a", "kind": "epic", "title": "{{v}}"}],
            }
        )
    )


def test_a_value_is_matched_against_its_pattern_in_no_time_whatever_it_holds() -> None:
    template = parse_template(
        {
            "variables": {"v": {"pattern": "^[a-z]+$", "max_length": 500}},
            "items": [{"key": "a", "kind": "epic", "title": "{{v}}"}],
        }
    )
    started = time.monotonic()
    with pytest.raises(TemplateProblems):
        expand(template, {"v": "a" * 499 + "!"})
    assert time.monotonic() - started < 0.1


@pytest.mark.parametrize(
    "pattern",
    ["^[0-9.]+$", "[A-Z]{2,5}", "^\\d{3}$", "\\w*", "^[^ ]{1,20}$", "[-_.]+", "^[#%]{2}$"],
)
def test_a_stand_in_value_always_fits_the_pattern_so_a_template_can_be_saved(pattern: str) -> None:
    template = parse_template(
        {
            "variables": {"v": {"pattern": pattern}},
            "items": [{"key": "a", "kind": "epic", "title": "{{v}}"}],
        }
    )
    expand(template, sample_values(template))


def test_a_class_the_regular_expression_library_would_refuse_is_a_problem_not_a_crash() -> None:
    assert any(
        "pattern" in p
        for p in problems_of(
            {
                "variables": {"v": {"pattern": "[b-a]+"}},
                "items": [{"key": "a", "kind": "epic", "title": "{{v}}"}],
            }
        )
    )


def test_sample_values_make_a_template_checkable_without_a_project() -> None:
    template = parse_template(RELEASE)
    assert sample_values(template) == {
        "version": "1",
        "owner": "the team",
        "channel": "stable",
        "epic": "epic:1",
    }
    assert sample_refs(template) == {"epic:1"}


@pytest.mark.parametrize("name", ["release-checklist", "a", "x_1", "0day"])
def test_a_good_template_name_is_taken(name: str) -> None:
    assert clean_name(f"  {name} ") == name


@pytest.mark.parametrize("name", ["", "Release", "-x", "has space", "a" * 61, "a/b", "é"])
def test_a_bad_template_name_is_refused(name: str) -> None:
    with pytest.raises(Exception, match="template's name"):
        clean_name(name)


def test_a_declared_variable_may_be_left_unused_and_a_blank_optional_one_is_empty() -> None:
    template = parse_template(
        {
            "variables": {"unused": {"required": False}, "note": {"required": False}},
            "items": [{"key": "a", "kind": "epic", "title": "T", "body": "[{{note}}]"}],
        }
    )
    items, _, _ = expand(template, {})
    assert items[0]["body"] == "[]"
    assert re.search("note", "{{note}}")
