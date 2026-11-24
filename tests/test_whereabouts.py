"""M41, with no containers: the words a holder says about where the work is and which run it is
(whereabouts.py), and that the claims table repeats the patterns, so that the database refuses what
this module would."""

from pathlib import Path

import pytest

from vrum import whereabouts
from vrum.common import Refusal
from vrum.whereabouts import clean_whereabouts, pull_request_address, whereabouts_in

A_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2
SCHEMA = (Path(__file__).parent.parent / "src" / "vrum" / "schema.sql").read_text()


def test_good_words_are_kept_tidied_and_only_the_ones_given() -> None:
    assert clean_whereabouts() == {}
    assert clean_whereabouts(branch="  ", run_label="", client_name=None) == {}
    assert clean_whereabouts(
        repository=" https://github.com/acme/widgets.git ",
        branch="feature/m41-where_the.work+is=1,#2@x",
        commit="0123ABCDEF0",
        pull_request="https://git.example.com/acme/widgets/pull/7",
        run_label="run-2026-11-22-a (second try)",
        client_name="claude-code 2.1",
    ) == {
        "repository": "https://github.com/acme/widgets.git",
        "branch": "feature/m41-where_the.work+is=1,#2@x",
        "commit": "0123abcdef0",
        "pull_request": "https://git.example.com/acme/widgets/pull/7",
        "run_label": "run-2026-11-22-a (second try)",
        "client_name": "claude-code 2.1",
    }


BAD = [
    ("repository", "http://github.com/acme/widgets"),
    ("repository", "https://user:hunter2@github.com/acme/widgets"),
    ("repository", "https://github.com/acme/widgets?tab=1"),
    ("repository", "https://github.com/acme/widgets#readme"),
    ("repository", "https://github.com/acme wid"),
    ("repository", "https://github.com/" + "a" * 300),
    ("branch", "-rf"),
    ("branch", "--upload-pack=evil"),
    ("branch", "/leading"),
    ("branch", "trailing/"),
    ("branch", "ends."),
    ("branch", "has space"),
    ("branch", "a..b"),
    ("branch", "a//b"),
    ("branch", "x/.hidden"),
    ("branch", "feature.lock"),
    ("branch", "x/y.lock/z"),
    ("branch", "quote'd"),
    ("branch", "$(id)"),
    ("branch", "a;b"),
    ("branch", "a" * 201),
    ("commit", "abc123"),
    ("commit", "g" * 40),
    ("commit", "0" * 65),
    ("repository", "https://github.com/../widgets"),
    ("repository", "https://github.com/acme/.."),
    ("repository", "https://.evil.example/acme/widgets"),
    ("pull_request", "https://github.com/../widgets/pull/5"),
    ("pull_request", "https://github.com/acme/./pull/5"),
    ("pull_request", "https://github.com/acme/widgets"),
    ("pull_request", "https://github.com/acme/widgets/pull/0"),
    ("pull_request", "https://github.com/acme/widgets/pull/12/files"),
    ("pull_request", "http://github.com/acme/widgets/pull/12"),
    ("pull_request", "https://u:p@github.com/acme/widgets/pull/12"),
    ("run_label", "-starts-with-dash"),
    ("run_label", "line\nbreak"),
    ("run_label", "x" * 81),
    ("run_label", "<b>bold</b>"),
    ("client_name", "$(whoami)"),
    ("client_name", "x" * 61),
]


@pytest.mark.parametrize(("field", "value"), BAD)
def test_a_value_that_is_not_what_it_should_be_is_refused_without_being_repeated(
    field: str, value: str
) -> None:
    with pytest.raises(Refusal) as refused:
        clean_whereabouts(**{field: value})
    words = str(refused.value)
    assert words.startswith(f"{field} must be ") and words.endswith("Nothing was changed.")
    assert value not in words
    assert "\u2014" not in words and "\u2013" not in words


@pytest.mark.parametrize("field", ["repository", "branch", "run_label", "client_name"])
def test_a_secret_is_refused_by_field_and_never_repeated(field: str) -> None:
    with pytest.raises(Refusal) as refused:
        clean_whereabouts(**{field: A_TOKEN})
    assert A_TOKEN not in str(refused.value) and field in str(refused.value)


def test_a_row_or_an_event_gives_back_only_what_it_says() -> None:
    assert whereabouts_in({"branch": "b", "commit": None, "note": "x", "run_label": ""}) == {
        "branch": "b"
    }


def test_a_pull_request_address_is_read_off_the_start_of_some_words() -> None:
    assert pull_request_address("https://github.com/a/b/pull/3 merged, checks passed") == (
        "https://github.com/a/b/pull/3"
    )
    assert pull_request_address("merged https://github.com/a/b/pull/3") is None


@pytest.mark.parametrize(
    "pattern",
    [
        whereabouts._REPOSITORY,
        whereabouts._BRANCH,
        whereabouts._COMMIT,
        whereabouts._PULL_REQUEST_WHOLE,
        whereabouts._LABEL,
    ],
)
def test_the_claims_table_repeats_every_pattern_byte_for_byte(pattern: object) -> None:
    assert f"'^{pattern.pattern}$'" in SCHEMA  # type: ignore[attr-defined]


def test_the_claims_table_repeats_the_dot_segment_rule() -> None:
    assert f"!~ '{whereabouts._DOT_SEGMENT.pattern}'" in SCHEMA
    assert SCHEMA.count(f"!~ '{whereabouts._DOT_SEGMENT.pattern}'") == 2


def test_the_claims_table_repeats_the_lengths() -> None:
    for column, most in (
        ("repository", whereabouts.MOST_REPOSITORY_CHARS),
        ("branch", whereabouts.MOST_BRANCH_CHARS),
        ("run_label", whereabouts.MOST_RUN_LABEL_CHARS),
        ("client_name", whereabouts.MOST_CLIENT_NAME_CHARS),
    ):
        assert f"length({column}) <= {most}" in SCHEMA
