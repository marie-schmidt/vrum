"""The filter language, read without a database: what each kind of term means, what is refused and
how it is said, and that nothing a person typed ever becomes part of the SQL text (the semantics
against real data are checked in test_query_integration.py)."""

import datetime

import pytest

from vrum.common import Refusal
from vrum.query import BARE_FLAGS, FIELDS, IS_FLAGS, NO_FIELDS, _scan, compile_query


def sql_of(text: str) -> tuple[str, dict[str, object]]:
    return compile_query(text)


def test_an_empty_query_asks_for_everything() -> None:
    assert compile_query(None) == ("true", {})
    assert compile_query("   ") == ("true", {})


def test_the_example_of_the_spec_is_two_groups_of_terms() -> None:
    groups = _scan("kind:task status:ready,in_progress -claimed priority:high OR kind:story")
    assert [[(t.field, t.values, t.negated) for t in group] for group in groups] == [
        [
            ("kind", ("task",), False),
            ("status", ("ready", "in_progress"), False),
            (None, ("claimed",), True),
            ("priority", ("high",), False),
        ],
        [("kind", ("story",), False)],
    ]
    sql, params = sql_of("kind:task status:ready,in_progress -claimed priority:high OR kind:story")
    assert sql.count(" OR ") == 1 and "NOT COALESCE(claimed_by IS NOT NULL" in sql
    assert params["q0"] == ["task"] and params["q1"] == ["ready", "in_progress"]


def test_quotes_make_words_and_values_with_spaces() -> None:
    (group,) = _scan('"two words" claimed:"Ada Lovelace",bob -"not this"')
    assert [(t.field, t.values, t.negated) for t in group] == [
        (None, ("two words",), False),
        ("claimed", ("Ada Lovelace", "bob"), False),
        (None, ("not this",), True),
    ]


def test_a_quoted_claimed_is_a_word_and_a_bare_one_is_a_flag() -> None:
    bare, _ = sql_of("claimed")
    quoted, params = sql_of('"claimed"')
    assert "claimed_by IS NOT NULL" in bare and "ILIKE" not in bare
    assert "ILIKE" in quoted and params == {"q0": "%claimed%"}
    assert set(BARE_FLAGS) == {"claimed", "waiting"}


def test_lowercase_or_is_a_word_and_only_uppercase_or_joins_groups() -> None:
    assert len(_scan("fix or break")[0]) == 3
    assert len(_scan("fix OR break")) == 2


def test_ranges_may_be_open_and_none_means_empty() -> None:
    sql, params = sql_of("target:2026-11-01..2026-12-31")
    assert "target_on >= %(q0)s" in sql and "target_on <= %(q1)s" in sql
    assert params == {"q0": datetime.date(2026, 11, 1), "q1": datetime.date(2026, 12, 31)}
    assert sql_of("start:2026-11-01..")[1] == {"q0": datetime.date(2026, 11, 1)}
    assert sql_of("start:..2026-11-01")[1] == {"q0": datetime.date(2026, 11, 1)}
    assert "start_on IS NULL" in sql_of("start:none")[0]
    assert "start_on IS NULL" in sql_of("-start:none")[0]


def test_every_field_the_language_names_can_be_used() -> None:
    examples = {
        "kind": "kind:task",
        "status": "status:ready",
        "priority": "priority:high",
        "claimed": "claimed:ann",
        "parent": "parent:3",
        "epic": "epic:#3",
        "story": "story:3",
        "id": "id:3,4",
        "start": "start:2026-11-01",
        "target": "target:none",
        "is": "is:overdue",
        "no": "no:claimed",
    }
    assert set(examples) == set(FIELDS)
    for example in examples.values():
        assert compile_query(example)[0] != "true"
    for flag in IS_FLAGS:
        compile_query(f"is:{flag}")
    for empty in NO_FIELDS:
        compile_query(f"no:{empty}")


def test_nothing_that_was_typed_becomes_part_of_the_sql() -> None:
    hostile = "x'; DROP TABLE nodes; --"
    for text, kept in (
        (hostile, "DROP"),
        (f'"{hostile}"', "DROP TABLE nodes"),
        (f'claimed:"{hostile}"', "drop table nodes"),
        ("100%_done", "100"),
        ("back\\slash", "slash"),
    ):
        sql, params = sql_of(text)
        assert "DROP" not in sql and "x'" not in sql and "slash" not in sql and "100" not in sql
        assert kept.lower() in " ".join(str(value) for value in params.values()).lower()
    # A percent sign or underscore in a word is matched as itself.
    assert sql_of("100%_done")[1] == {"q0": "%100\\%\\_done%"}


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("colour:red", "there is no field called 'colour'"),
        ("kind:tsak", "kind must be one of epic, story, task"),
        ("status:finished", "status must be one of new, ready, in_progress"),
        ("priority:", "priority: needs a value"),
        ("parent:abc", "parent needs a whole number"),
        ("target:next-week", "is not a day"),
        ("target:2026-02-30", "is not a day"),
        ("target:2026-12-31..2026-11-01", "starts after it ends"),
        ("target:..", "needs at least one day"),
        ("is:late", "is must be one of overdue, conflict"),
        ("no:colour", "no must be one of start, target, claimed"),
        ("OR kind:task", "OR needs something before it"),
        ("kind:task OR", "OR needs something after it"),
        ('"never closed', "never closed"),
        ('""', "a quoted phrase has nothing in it"),
        ('"a"b', "followed by more text"),
        ("http://example.com", "there is no field called 'http'"),
    ],
)
def test_what_cannot_be_understood_is_refused_in_plain_words(text: str, says: str) -> None:
    with pytest.raises(Refusal) as refusal:
        compile_query(text)
    message = str(refusal.value)
    assert says in message
    assert message.startswith("The query was not understood: ")
    assert "Nothing was listed." in message and "field:value" in message


def test_a_query_that_is_too_long_is_refused() -> None:
    with pytest.raises(Refusal, match="longest allowed is 2,000"):
        compile_query("a " * 1500)
