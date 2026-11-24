"""The checks of the definition of done, read without a database: which lines of the text are
checks, what an item is called and when two lines are one, how a caller names a check, what an
agent is told when it ticks without saying how, and who is never held back from calling work done.
The ticking itself, against real data, is in test_checks_integration.py."""

import asyncio
import hashlib
from typing import Any, cast

import pytest

from vrum.checks import Checks, _find, _named, refuse_while_unticked
from vrum.common import Refusal
from vrum.db import Database
from vrum.definition_of_done import (
    DefinitionItem,
    definition_items,
    is_person_only,
    item_key,
    item_words,
)
from vrum.identity import agent, person, working_as
from vrum.markdown import _checklist


def words(body: str) -> list[str]:
    return [item.text for item in definition_items(body)]


def test_the_list_lines_are_the_checks_whatever_the_mark_or_the_depth() -> None:
    body = (
        "# Done means\n"
        "\n"
        "Everything below has to hold.\n"
        "- Tests pass\n"
        "* A person tried it\n"
        "   - [ ] Nested at any depth\n"
        "\t* [x] Ticked in the text itself\n"
        "- [X]   Capital X and   extra   spaces\n"
        "1. A numbered line is not one\n"
        "+ neither is a plus\n"
        "Closing words.\n"
    )
    assert words(body) == [
        "Tests pass",
        "A person tried it",
        "Nested at any depth",
        "Ticked in the text itself",
        "Capital X and extra spaces",
    ]
    assert words("") == [] and words("No list lines, only words.") == []


def test_what_is_not_a_check_is_left_out() -> None:
    body = (
        "- Real one\n"
        "-\n"
        "- [ ]\n"
        "- [x]   \n"
        "-----\n"
        "- - -\n"
        "* * *\n"
        "```\n"
        "- A line of code is not a check\n"
        "```\n"
        "~~~\n"
        "* nor is this\n"
        "~~~\n"
        "-no space after the dash\n"
        "- Another real one\n"
    )
    assert words(body) == ["Real one", "Another real one"]


def test_an_item_is_named_by_its_words_and_two_lines_with_the_same_words_are_one_item() -> None:
    items = definition_items("- Tests pass\n* tests pass\n- Tests pass\n  - [x] Tests   pass\n")
    # Capitals count: the words are what they are. The same words, in any mark or spacing, are one.
    assert [item.text for item in items] == ["Tests pass", "tests pass"]
    assert items[0].item == hashlib.sha256(b"Tests pass").hexdigest()[:8]
    assert items[0].item == item_key("Tests pass") and len(items[0].item) == 8
    assert items[0].item != items[1].item
    # The key does not move when the line around the words is rewritten.
    assert [i.item for i in definition_items("- [ ] Tests pass")] == [items[0].item]
    assert [i.item for i in definition_items("* Tests    pass  ")] == [items[0].item]
    assert item_words("  [x]  Tests pass ") == "Tests pass"


def test_a_caller_names_a_check_by_its_key_or_by_its_exact_words() -> None:
    items = definition_items("- Tests pass\n- Docs are updated")
    tests, docs = items
    assert _find(items, tests.item) is tests
    assert _find(items, f"  {docs.item.upper()} ") is docs
    assert _find(items, "Docs are updated") is docs
    assert _find(items, "[ ] Docs   are updated") is docs
    said = pytest.raises(Refusal, _find, items, "ffffffff").value
    assert "There is no check 'ffffffff' in the definition of done" in str(said)
    assert f"{tests.item}: 'Tests pass'" in str(said) and f"{docs.item}: 'Docs are updated'" in str(
        said
    )
    nothing = pytest.raises(Refusal, _find, [], "Tests pass").value
    assert "has no checks" in str(nothing) and "start" in str(nothing) and "dash" in str(nothing)


def test_a_refusal_names_a_few_checks_and_counts_the_rest() -> None:
    many = [DefinitionItem(item=f"{n:08x}", text=f"Check {n}") for n in range(12)]
    named = _named(many)
    assert "'Check 0' (item 00000000)" in named and "'Check 7'" in named
    assert "'Check 8'" not in named and named.endswith("; and 4 more")
    assert "more" not in _named(many[:8])
    long = str(pytest.raises(Refusal, _find, many, "nope").value)
    assert "; and 4 more." in long


def run_as(identity: Any, work: Any) -> Any:
    async def inside() -> Any:
        with working_as(identity):
            return await work()

    return asyncio.run(inside())


def test_an_agent_that_does_not_say_how_it_checked_is_refused_before_anything_is_read() -> None:
    checks = Checks(cast(Database, None))
    for evidence in (None, "", "   \n"):
        with pytest.raises(Refusal) as refusal:
            run_as(
                agent("ann"),
                lambda evidence=evidence: checks.set_check(
                    actor="ann", node="task:1", item="x", checked=True, evidence=evidence
                ),
            )
        assert "Say how you checked" in str(refusal.value) and "Nothing was changed" in str(
            refusal.value
        )


def test_evidence_that_is_too_long_is_refused_in_plain_words() -> None:
    checks = Checks(cast(Database, None))
    for identity in (agent("ann"), person("Marie")):
        with pytest.raises(Refusal) as refusal:
            run_as(
                identity,
                lambda: checks.set_check(
                    actor="x", node="task:1", item="x", checked=True, evidence="y" * 2001
                ),
            )
        assert "2,001 characters" in str(refusal.value) and "2,000" in str(refusal.value)


def test_a_person_is_never_held_back_and_neither_is_an_epic() -> None:
    # A person decides, and an epic has no checks: nothing is read to find that out.
    run_as(
        person("Marie"),
        lambda: refuse_while_unticked(None, "task:1", instead="x", block="y"),  # type: ignore[arg-type]
    )
    run_as(
        agent("ann"),
        lambda: refuse_while_unticked(None, "epic:1", instead="x", block="y"),  # type: ignore[arg-type]
    )


def test_a_line_that_starts_with_person_is_a_check_only_a_person_can_judge() -> None:
    body = (
        "- Tests pass\n"
        "- [person] Looks right on a phone\n"
        "- [ ] [Person]   Feels quick\n"
        "* [x] [PERSON] Reads well\n"
        "- [person]\n"
        "- [ ] [person]  \n"
        "- [person]Glued on\n"
        "- A [person] in the middle\n"
    )
    items = definition_items(body)
    assert [(item.text, item.person_only) for item in items] == [
        ("Tests pass", False),
        ("Looks right on a phone", True),
        ("Feels quick", True),
        ("Reads well", True),
        ("[person]Glued on", False),
        ("A [person] in the middle", False),
    ]
    assert item_words("[ ] [person]  Looks   right") == "Looks right"
    assert is_person_only("[x] [Person] Looks right") and not is_person_only("Looks [person]")


def test_the_tag_is_not_part_of_the_words_so_adding_or_removing_it_keeps_the_key() -> None:
    plain, tagged = (
        definition_items("- Looks right on a phone")[0],
        definition_items("- [person] Looks right on a phone")[0],
    )
    assert plain.item == tagged.item == item_key("Looks right on a phone")
    assert plain.text == tagged.text == "Looks right on a phone"
    assert (plain.person_only, tagged.person_only) == (False, True)


def test_two_lines_with_the_same_words_are_one_item_and_it_is_person_only_if_any_is_tagged() -> (
    None
):
    for body in (
        "- Looks right\n- [person] Looks right\n",
        "- [person] Looks right\n- Looks right\n",
        "- [person] Looks right\n  - [x] [PERSON]   Looks   right\n",
    ):
        (item,) = definition_items(body)
        assert (item.text, item.person_only) == ("Looks right", True)
    (item,) = definition_items("- Looks right\n- [ ] Looks right\n")
    assert item.person_only is False


def test_a_caller_names_a_person_only_check_with_or_without_its_tag() -> None:
    items = definition_items("- Tests pass\n- [person] Looks right on a phone")
    phone = items[1]
    assert _find(items, phone.item) is phone
    assert _find(items, "Looks right on a phone") is phone
    assert _find(items, "[person] Looks right on a phone") is phone
    assert _find(items, "[ ] [person]  Looks right on a phone") is phone


def test_a_refusal_names_a_person_only_check_by_its_words_alone() -> None:
    one = DefinitionItem(item="00000001", text="Looks right", person_only=True)
    assert _named([one], keys=False) == "'Looks right'"
    assert _named([one]) == "'Looks right' (item 00000001)"


def test_the_markdown_copy_keeps_the_tag_of_a_check_only_a_person_can_judge() -> None:
    items = definition_items("- Tests pass\n- [person] Looks right on a phone")
    ticks = {
        ("task:1", items[1].item): {
            "checked_by": "Marie",
            "checked_at": "2026-11-19T10:00:00Z",
            "evidence": None,
        }
    }
    text = _checklist("task:1", items, ticks)
    assert "- [ ] Tests pass\n" in text
    assert text.endswith("- [x] [person] Looks right on a phone (by Marie, 2026-11-19)")
    assert "- [ ] [person] Looks right on a phone" in _checklist("task:2", items, {})


# -- verifiers and GitHub evidence (M40) ---------------------------------------------------------


def test_the_verifier_and_github_tags_say_who_may_tick_a_line_and_are_not_part_of_its_words() -> (
    None
):
    body = (
        "- Plain\n"
        "- [verifier] Reviewed by someone else\n"
        "- [ ] [GitHub] The pull request is merged\n"
        "* [x] [github] [VERIFIER] Both tags, either order\n"
        "- [verifier] [person] A person outranks it\n"
        "- [person][verifier] Glued on\n"
    )
    flags = [(i.text, i.person_only, i.verifier_only, i.github) for i in definition_items(body)]
    assert flags == [
        ("Plain", False, False, False),
        ("Reviewed by someone else", False, True, False),
        # [github] means [verifier] too.
        ("The pull request is merged", False, True, True),
        ("Both tags, either order", False, True, True),
        ("A person outranks it", True, True, False),
        ("[person][verifier] Glued on", False, False, False),
    ]
    plain, tagged = (
        definition_items("- Reviewed")[0],
        definition_items("- [verifier] Reviewed")[0],
    )
    assert plain.item == tagged.item == item_key("Reviewed")
    assert item_words("[ ] [verifier] [github]   Reviewed   twice") == "Reviewed twice"


def test_two_lines_with_the_same_words_carry_every_tag_any_of_them_has() -> None:
    for body in (
        "- Reviewed\n- [verifier] Reviewed\n",
        "- [github] Reviewed\n- Reviewed\n",
        "- [verifier] Reviewed\n  - [x] [PERSON] Reviewed\n",
    ):
        (item,) = definition_items(body)
        assert item.verifier_only is True
    (item,) = definition_items("- Reviewed\n- [github] Reviewed\n")
    assert (item.verifier_only, item.github) == (True, True)


def test_a_github_evidence_must_start_with_a_pull_request_of_a_repository() -> None:
    from vrum.checks import pull_request_address

    good = "https://github.com/acme/widgets/pull/12"
    assert pull_request_address(good) == good
    assert pull_request_address(f"  {good} merged 2026-11-22, checks passed 3 of 3") == good
    assert pull_request_address(f"{good}/files merged") == good
    assert pull_request_address(f"{good}#issuecomment-1 merged") == good
    assert pull_request_address("https://git.example.com/acme/widgets.git/pull/7") == (
        "https://git.example.com/acme/widgets.git/pull/7"
    )
    for bad in (
        None,
        "",
        "merged https://github.com/acme/widgets/pull/12",
        "http://github.com/acme/widgets/pull/12",
        "https://github.com/acme/widgets/pull/0",
        "https://github.com/acme/widgets/pull/twelve",
        "https://github.com/acme/widgets/issues/12",
        "https://github.com/acme/widgets/pull/12x",
        "https://github.com/acme/pull/12",
        "github.com/acme/widgets/pull/12",
    ):
        assert pull_request_address(bad) is None, bad


def test_the_markdown_copy_keeps_every_tag_of_a_check() -> None:
    items = definition_items(
        "- [verifier] Reviewed\n- [github] Merged\n- [person] [verifier] Looks right"
    )
    text = _checklist("task:1", items, {})
    assert "- [ ] [verifier] Reviewed\n" in text
    assert "- [ ] [github] Merged\n" in text
    assert text.endswith("- [ ] [person] [verifier] Looks right")
