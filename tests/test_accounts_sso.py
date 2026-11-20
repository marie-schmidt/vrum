"""What a provider's answer means for an account, as far as that can be decided with no database:
the decision itself, the address and name that come from a provider, the words of every problem,
and that an account with no password is never signed in by guessing one. What the database does
with those decisions (one account for two sign-ins at once, the last way in) is proved against the
real containers in test_sso_integration.py."""

import pytest

from vrum.accounts import (
    PROVIDERS,
    SSO_WORDS,
    AccessRefusal,
    ProviderPerson,
    SsoRefusal,
    first_sign_in_outcome,
    name_for_new_account,
    usable_address,
    vouched_address,
)


def person(**changes: object) -> ProviderPerson:
    values: dict[str, object] = {
        "provider": "oidc",
        "subject": "s-1",
        "email": "Ada@Example.com",
        "email_confirmed": True,
        "name": "Ada Lovelace",
        "shown": "ada",
    }
    values.update(changes)
    return ProviderPerson(**values)  # type: ignore[arg-type]


# -- the decision ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exists", "confirmed", "has_one_here", "create", "outcome"),
    [
        # An account with that address, confirmed here: the provider is joined to it.
        (True, True, False, True, "link"),
        (True, True, False, False, "link"),
        # Its address was never confirmed: nobody is signed in and nothing changes.
        (True, False, False, True, "account_exists_unconfirmed"),
        (True, False, False, False, "account_exists_unconfirmed"),
        (True, False, True, True, "account_exists_unconfirmed"),
        # It is already connected to another person at this provider: not swapped by address.
        (True, True, True, True, "already_connected"),
        # No account: one is made, when that is allowed.
        (False, False, False, True, "create"),
        (False, False, False, False, "signups_off"),
    ],
)
def test_a_new_provider_identity_is_joined_made_or_refused(
    exists: bool, confirmed: bool, has_one_here: bool, create: bool, outcome: str
) -> None:
    assert (
        first_sign_in_outcome(
            exists=exists, confirmed=confirmed, has_one_here=has_one_here, create_accounts=create
        )
        == outcome
    )


def test_an_unconfirmed_account_is_never_joined_whatever_else_is_true() -> None:
    for has_one_here in (True, False):
        for create in (True, False):
            outcome = first_sign_in_outcome(
                exists=True, confirmed=False, has_one_here=has_one_here, create_accounts=create
            )
            assert outcome == "account_exists_unconfirmed"


# -- the address and the name ---------------------------------------------------------------


def test_an_address_is_vouched_for_only_when_the_provider_confirms_it() -> None:
    assert vouched_address(person()) == "ada@example.com"
    assert vouched_address(person(email_confirmed=False)) is None
    assert vouched_address(person(email=None)) is None


@pytest.mark.parametrize(
    "bad", ["", "ada", "ada@", "@example.com", "ada@example", "a b@example.com", "a@@b.co", None]
)
def test_something_that_is_not_an_address_is_not_one(bad: str | None) -> None:
    assert usable_address(bad) is None


def test_an_address_is_kept_the_way_accounts_keep_them() -> None:
    assert usable_address("  Ada@Example.COM ") == "ada@example.com"
    assert usable_address("a" * 250 + "@b.co") is None
    assert usable_address("ada\x00@example.com") is None
    assert usable_address(42) is None  # type: ignore[arg-type]


def test_a_new_accounts_name_is_the_providers_name_or_the_start_of_the_address() -> None:
    assert name_for_new_account(person(), "ada@example.com") == "Ada Lovelace"
    assert name_for_new_account(person(name=None), "ada.l@example.com") == "ada.l"
    assert name_for_new_account(person(name="   "), "ada@example.com") == "ada"
    assert name_for_new_account(person(name="Ada\n\tLovelace\x00"), "x@y.co") == "Ada Lovelace"
    assert len(name_for_new_account(person(name="n" * 500), "x@y.co")) == 100


def test_a_name_that_looks_like_a_secret_is_not_used() -> None:
    token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2
    assert name_for_new_account(person(name=token), "ada@example.com") == "ada"
    assert name_for_new_account(person(name=token), token + "@example.com") == "New person"


# -- the words --------------------------------------------------------------------------------


def test_every_problem_has_the_words_the_pages_show() -> None:
    said = {code: SsoRefusal(code, "Example login") for code in SSO_WORDS}
    assert (
        str(said["cancelled"])
        == "Signing in with Example login was cancelled. Nothing was changed."
    )
    assert str(said["failed"]) == (
        "Signing in with Example login did not work. Nothing was changed. Try again, and if it "
        "keeps happening ask whoever runs this server."
    )
    assert str(said["expired"]) == "That sign-in took too long or was already used. Start again."
    assert str(said["unavailable"]) == "Vrum could not reach Example login. Try again in a moment."
    assert str(said["no_verified_email"]) == (
        "Example login did not give a confirmed email address, so Vrum cannot tell who you are. "
        "Confirm your email address there, or sign in another way."
    )
    assert str(said["domain_not_allowed"]) == (
        "Accounts with that email address may not sign in this way on this server."
    )
    assert str(said["account_exists_unconfirmed"]) == (
        "An account with your email address already exists here, but its address was never "
        "confirmed, so Vrum will not join the two. Sign in with its password, then connect "
        "Example login from the Users page."
    )
    assert str(said["signups_off"]) == (
        "This server does not make new accounts from Example login sign-in. Sign in with an "
        "account you already have, or ask whoever runs it."
    )
    assert str(said["identity_taken"]) == (
        "That Example login account is already connected to another Vrum account."
    )
    assert str(said["already_connected"]) == (
        "Example login is already connected to this account. Disconnect it first."
    )
    assert str(said["link_session_lost"]) == (
        "You were signed out while connecting Example login. Sign in and try again."
    )
    assert str(said["last_way_in"]) == (
        "That is the only way you can sign in. Set a password or connect another provider first. "
        "Nothing was changed."
    )
    assert str(said["bad_credentials"]) == "The username or password is not right."
    assert str(said["not_set_up"]) == "Signing in with that provider is not set up on this server."
    for code, refusal in said.items():
        assert "{" not in str(refusal), code
        assert refusal.code == code and isinstance(refusal, AccessRefusal)


def test_a_wrong_name_or_password_is_a_401_and_a_provider_that_is_not_set_up_a_404() -> None:
    assert SsoRefusal("bad_credentials").status == 401
    assert SsoRefusal("bad_credentials", status=400).status == 400
    assert SsoRefusal("not_set_up").status == 404


def test_there_are_three_providers() -> None:
    assert PROVIDERS == ("oidc", "github", "ldap")


def test_the_words_never_hold_a_dash_that_a_person_would_stumble_on() -> None:
    for words in SSO_WORDS.values():
        assert "\u2014" not in words and "\u2013" not in words
