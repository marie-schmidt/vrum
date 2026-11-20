"""The check for secrets in well-known formats: it finds them, and leaves ordinary text alone."""

import pytest

from vrum.secret_formats import find_secret, find_secret_in, redact_secrets


# Written out in pieces so that no real-looking secret is stored in this file.
def _tokens() -> list[tuple[str, str]]:
    return [
        ("a GitHub token", "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fG3hJ5"),
        ("a GitHub token", "github_pat_" + "11ABCDEFG0" + "abcdefghijklmnopqrstuvwxyz"),
        ("an AWS access key id", "AKIA" + "ABCDEFGHIJKLMNOP"),
        ("a Slack token", "xoxb-" + "123456789012-abcdefghijkl"),
        ("a Stripe key", "sk_live_" + "abcdefghijklmnop1234"),
        ("an API key of the sk- kind (OpenAI, Anthropic and others)", "sk-" + "a1B2c3D4" * 4),
        ("a Google API key", "AIza" + "SyA-1234567890abcdefghijklmnopqrstu"),
        ("a private key", "-----BEGIN " + "RSA PRIVATE KEY-----"),
        ("a private key", "-----BEGIN " + "OPENSSH PRIVATE KEY-----"),
        (
            "a JSON web token",
            "eyJ" + "hbGciOiJIUzI1NiJ9." + "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0.abcdefghij",
        ),
        ("a GitLab token", "glpat-" + "abcdefghij0123456789"),
        ("a Vrum invitation", "vri_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fG3hJ5"),
    ]


@pytest.mark.parametrize(("kind", "secret"), _tokens())
def test_a_secret_in_a_well_known_format_is_found_wherever_it_is(kind: str, secret: str) -> None:
    assert find_secret(secret) == kind
    assert find_secret(f"The deploy failed.\nTried with {secret} and it was refused.") == kind


def test_what_is_said_about_it_never_contains_the_secret() -> None:
    secret = "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fG3hJ5"
    assert secret not in (find_secret(secret) or "")


@pytest.mark.parametrize(
    "text",
    [
        "Users reset their password by email, and the API key is shown once in settings.",
        "The token expires after 15 minutes; send it as a bearer token.",
        "Set password = 'hunter2' in the example.",
        "The ghp_ prefix marks a GitHub personal access token.",
        "See sk-learn for machine learning, and AKIA is not enough on its own.",
        "The id is abcdef0123456789abcdef0123456789abcdef01 in the commit log.",
        "-----BEGIN CERTIFICATE----- is public, unlike a private key.",
        "",
    ],
)
def test_ordinary_text_that_merely_talks_about_secrets_is_not_one(text: str) -> None:
    assert find_secret(text) is None


GITHUB = "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fG3hJ5"


def test_a_secret_is_found_anywhere_inside_lists_and_objects_and_the_field_is_named() -> None:
    items = [{"title": "fine"}, {"title": "fine", "body": f"the key is {GITHUB}"}]

    assert find_secret_in({"actor": "ann", "items": items}) == ("items[1].body", "a GitHub token")
    assert find_secret_in({"nested": {"deeper": [["x", GITHUB]]}}) == (
        "nested.deeper[0][1]",
        "a GitHub token",
    )
    assert find_secret_in(GITHUB) == ("", "a GitHub token")
    assert find_secret_in({"a": 1, "b": None, "c": [2.5, True], "d": "plain"}) is None
    assert find_secret_in({}) is None


def test_the_answer_names_the_field_and_the_kind_and_never_the_secret() -> None:
    path, kind = find_secret_in({"body": f"x {GITHUB} y"}) or ("", "")
    assert GITHUB not in path + kind


def test_redaction_replaces_every_secret_in_a_text_with_a_note_of_what_it_was() -> None:
    text = f"first {GITHUB}, then AKIAABCDEFGHIJKLMNOP, and nothing else"

    assert redact_secrets(text) == (
        "first [a GitHub token removed], then [an AWS access key id removed], and nothing else"
    )
    assert redact_secrets("an ordinary line") == "an ordinary line"
