"""The password hash, the failed-attempt counter and the checks on what a person typed, with no
containers."""

import pytest

from vrum.accounts import (
    AccessRefusal,
    clean_email,
    digest_of,
    hash_password,
    password_matches,
)
from vrum.ratelimit import AttemptLimiter


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
