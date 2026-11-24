"""The password hash, the failed-attempt counter and the checks on what a person typed, with no
containers."""

from datetime import UTC, date, datetime, timedelta

import pytest

from vrum.accounts import (
    AccessRefusal,
    clean_agent_name,
    clean_display_name,
    clean_email,
    clean_expiry,
    digest_of,
    hash_password,
    key_expired_words,
    password_matches,
)
from vrum.ratelimit import AttemptLimiter

A_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2


def test_an_agent_key_is_named_for_its_agent_in_one_to_a_hundred_characters() -> None:
    assert clean_agent_name("  api-agent ") == "api-agent"
    assert clean_agent_name("x" * 100) == "x" * 100
    for typed in ("", "   ", "x" * 101):
        with pytest.raises(AccessRefusal, match="Give the key the name of the agent"):
            clean_agent_name(typed)


def test_a_name_that_is_a_secret_is_refused_without_repeating_it() -> None:
    with pytest.raises(AccessRefusal, match="looks like a secret") as agent:
        clean_agent_name(A_TOKEN)
    with pytest.raises(AccessRefusal, match="looks like a secret") as person:
        clean_display_name(f"Ada {A_TOKEN}")
    assert A_TOKEN not in str(agent.value) and A_TOKEN not in str(person.value)


def test_a_key_expiry_is_a_day_that_has_not_passed_or_nothing_for_a_key_that_never_expires() -> (
    None
):
    today = datetime.now(UTC).date()
    assert clean_expiry(None) is None and clean_expiry("") is None and clean_expiry("  ") is None
    assert clean_expiry(today.isoformat()) == today
    assert clean_expiry(f" {(today + timedelta(days=90)).isoformat()} ") == today + timedelta(
        days=90
    )
    yesterday = (today - timedelta(days=1)).isoformat()
    for typed, said in (
        (yesterday, "already passed"),
        ("next week", "YYYY-MM-DD"),
        ("2030-02-30", "YYYY-MM-DD"),
        ("2030-12-31T10:00", "YYYY-MM-DD"),
        (20301231, "YYYY-MM-DD"),
        (True, "YYYY-MM-DD"),
    ):
        with pytest.raises(AccessRefusal) as refused:
            clean_expiry(typed)
        assert said in str(refused.value) and "Nothing was changed" in str(refused.value)
        assert refused.value.status == 400


def test_an_expired_key_is_told_when_it_ran_out_and_who_can_fix_it() -> None:
    words = key_expired_words(date(2026, 11, 30))
    assert "expired after 2026-11-30" in words and "Users, then Agents" in words
    assert "\u2014" not in words and "\u2013" not in words


def test_a_person_is_shown_by_a_name_of_one_to_a_hundred_characters() -> None:
    assert clean_display_name("  Lena Okafor ") == "Lena Okafor"
    for typed in ("", "  ", "n" * 101):
        with pytest.raises(AccessRefusal, match="Say what to call you"):
            clean_display_name(typed)


def test_a_password_is_kept_as_a_salted_slow_hash_that_only_the_password_matches() -> None:
    first, second = hash_password("a long enough password"), hash_password("a long enough password")

    # Two hashes of one password differ, because each has its own salt.
    assert first != second
    scheme, cost, block, parallel, salt, digest = first.split("$")
    assert (scheme, cost, block, parallel) == ("scrypt", "32768", "8", "1")
    assert salt and digest and "a long enough password" not in first
    assert password_matches("a long enough password", first)
    assert password_matches("a long enough password", second)
    assert not password_matches("A long enough password", first)
    assert not password_matches("", first)


@pytest.mark.parametrize(
    "stored", ["", "plain", "scrypt$1$2", "scrypt$x$8$1$AAAA$AAAA", "md5$a$b$c$d$e"]
)
def test_a_stored_value_that_is_not_one_of_ours_never_matches(stored: str) -> None:
    assert not password_matches("a long enough password", stored)


def test_what_is_kept_of_a_session_or_a_key_is_a_hash_of_it() -> None:
    assert digest_of("vrk_example") == digest_of("vrk_example")
    assert digest_of("vrk_example") != digest_of("vrk_example2")
    assert "vrk_example" not in digest_of("vrk_example") and len(digest_of("x")) == 64


@pytest.mark.parametrize(
    "typed", ["ada@example.com", "  Ada@Example.COM ", "a.b+c@sub.example.org"]
)
def test_an_address_is_kept_in_lower_case_without_the_spaces_round_it(typed: str) -> None:
    assert clean_email(typed) == typed.strip().lower()


@pytest.mark.parametrize(
    "typed", ["", "ada", "ada@", "@example.com", "ada@example", "a b@c.d", "a@b@c.d"]
)
def test_what_does_not_look_like_an_address_is_refused_in_plain_words(typed: str) -> None:
    with pytest.raises(AccessRefusal, match="does not look like an email address"):
        clean_email(typed)


def test_failures_are_counted_in_a_window_and_a_good_attempt_forgives_them() -> None:
    now = [1000.0]
    limiter = AttemptLimiter(3, 60, lambda: now[0])

    assert limiter.seconds_to_wait("ada") == 0
    for _ in range(3):
        limiter.failed("ada")
    # Blocked until the oldest failure leaves the window, and only for that key.
    assert 55 <= limiter.seconds_to_wait("ada") <= 61
    assert limiter.seconds_to_wait("grace") == 0
    now[0] += 30
    assert limiter.seconds_to_wait("ada") >= 25
    now[0] += 31
    assert limiter.seconds_to_wait("ada") == 0

    for _ in range(3):
        limiter.failed("ada")
    limiter.succeeded("ada")
    assert limiter.seconds_to_wait("ada") == 0


def test_the_counter_forgets_old_failures_and_never_holds_more_keys_than_it_is_allowed() -> None:
    now = [0.0]
    limiter = AttemptLimiter(2, 10, lambda: now[0], most_keys=50)
    # Old failures are swept away as new ones arrive.
    for number in range(500):
        limiter.failed(f"old-{number}")
        now[0] += 11
    assert len(limiter._failures) <= 50
    # A flood of different keys inside one window is held to the same number.
    for number in range(500):
        limiter.failed(f"flood-{number}")
        now[0] += 0.001
    assert len(limiter._failures) <= 50
    # The newest are the ones kept.
    assert "flood-499" in limiter._failures and "flood-0" not in limiter._failures
