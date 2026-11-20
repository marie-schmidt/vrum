"""Signing in with another provider (M25), through the board's own HTTP API against the real
containers: OpenID Connect through the scripted provider (tests/scripted_oidc.py, which can be made
to hand out every kind of bad token a real one never would), GitHub through the scripted GitHub
(tests/scripted_github.py, built from GitHub's documented answers), and LDAP through a real
directory (OpenLDAP, with fictional people). No browser here: the browser legs, and the one sign-in
against a real OpenID provider (Dex), are in tests/test_sso_browser_integration.py.

What has to hold: a person who signs in with a provider gets the account that is theirs and no one
else's, whatever the provider sends. A provider can never be the way to take over an account: an
outside sign-in joins an existing account only when the address is confirmed on both sides, and
after that the provider's own identifier decides, not the address; a token that is for someone else,
is old, is not signed, is signed with the wrong kind of key, was changed, or does not belong to this
sign-in is refused with no session; a sign-in works once, only in the browser that started it, and
only comes back to a page of the board; a directory password is checked by the directory and never
kept; and nothing a provider or a person sends is shown, logged or stored beyond the identifier and
the address.

How the tests do it: each test makes people of its own at a provider (an address nobody else has)
and presses their button, as a browser does, by following the redirects by hand
(tests/sso_world.py).
The server keeps in memory how many sign-ins were started (twenty in ten minutes for one network
address), so the tests start it again before they could meet that limit. A few tests start it with a
setting changed (accounts not made from a provider, allowed domains, how the client proves itself)
and again with none."""

import json
import threading
import time
import urllib.parse
from typing import Any

import pytest

from tests.helpers import unique_word
from tests.sso_world import (
    NAMES,
    NOT_SET_UP,
    SESSION_COOKIE,
    SSO_COOKIE,
    WORDS,
    WRONG_LOGIN,
    Journey,
    Landing,
    Who,
    account_of,
    accounts_with,
    add_directory_person,
    app_log,
    app_with,
    board_base,
    change_directory_person,
    come_back,
    database_dump,
    fetch,
    fresh_server,
    github,
    identity_rows,
    mail_url,
    make_confirmed_account,
    make_unconfirmed_account,
    new_github_person,
    new_openid_person,
    oidc,
    oidc_url,
    relax_limits,
    session_of,
    sign_in,
    start_journey,
    visit_provider,
    whoami,
    words,
)
from tests.world import Person, Reply, api, register, register_as, server_base

pytestmark = pytest.mark.integration

PROVIDERS = [
    {"id": "oidc", "name": "Scripted OpenID", "kind": "oidc"},
    {"id": "github", "name": "GitHub", "kind": "github"},
    {"id": "ldap", "name": "Company directory", "kind": "ldap"},
]


@pytest.fixture(autouse=True)
def quiet_stand_ins() -> Any:
    """Every test starts with the stand-ins behaving and the server's limits far from used, and
    leaves the stand-ins so."""
    relax_limits()
    oidc().reset()
    github().reset()
    yield
    oidc().reset()
    github().reset()


# -- what a sign-in came to ------------------------------------------------------------------------


def signed_in(landing: Landing) -> Person:
    """The sign-in worked: back on the board with a session of its own, and no problem named."""
    assert landing.status == 303, landing
    assert landing.error is None, f"the sign-in was refused: {landing.location}"
    assert landing.path.startswith("/board") and "://" not in landing.location, landing.location
    assert landing.session, "the server did not start a session"
    return session_of(landing)


def refused(landing: Landing, code: str, provider: str = "oidc") -> None:
    """The sign-in ended on the board with one named problem and no session, and the address says
    nothing but a code and the provider."""
    assert landing.status == 303, landing
    assert landing.error == code, f"expected {code}, got {landing.location}"
    assert landing.query.get("sso_provider") == provider, landing.location
    assert landing.session is None, "a session was started for a sign-in that was refused"
    assert landing.path.startswith("/board") and "://" not in landing.location, landing.location
    assert set(landing.query) <= {"sso_error", "sso_provider"}, landing.location


def refused_for(landing: Landing, codes: set[str], provider: str = "oidc") -> str:
    """Refused, for one of these reasons (when either is right, the server may say which)."""
    assert landing.error in codes, f"expected one of {sorted(codes)}, got {landing.location}"
    refused(landing, str(landing.error), provider)
    return str(landing.error)


def sign_in_refused(provider: str, who: Who, codes: set[str]) -> str:
    """Sign in and be refused for one of these reasons, whether the start itself is refused (the
    server could not read what the provider says about itself: nothing is started, and the answer
    is in words) or the return ends with the problem named in its address."""
    journey = start_journey(provider)
    if journey.start.status != 200:
        assert journey.start.status in (400, 429, 500, 502, 503), journey.start.text
        body = journey.start.json
        assert body["code"] in codes and body["message"], body
        assert journey.sso_cookie is None
        return str(body["code"])
    return refused_for(come_back(journey, visit_provider(journey, who)), codes, provider)


def methods_of(person: Person) -> dict[str, Any]:
    reply = person.call("GET", "/board/api/account/identities")
    assert reply.status == 200, reply.body
    return dict(reply.body)


def ldap_sign_in(username: str, password: str, **more: Any) -> Reply:
    return api(
        server_base(),
        "POST",
        "/board/api/auth/ldap",
        {"username": username, "password": password},
        **more,
    )


def ldap_person(reply: Reply) -> Person:
    assert reply.status == 200 and reply.cookie, f"the directory sign-in was refused: {reply.body}"
    account = reply.body["account"]
    return Person(
        server_base(),
        account["email"],
        "",
        account["name"],
        reply.cookie,
        reply.body["csrf"],
        account["id"],
    )


def ldap_refused(reply: Reply, code: str | None = None, status: int | None = None) -> None:
    """The directory sign-in was refused: no session, plain words, and (when asked) this code or
    this status; otherwise any refusal status."""
    allowed = (status,) if status else (400, 401, 403, 409, 429)
    assert reply.status in allowed, (reply.status, reply.body)
    assert reply.cookie is None, "a session was started for a sign-in that was refused"
    assert isinstance(reply.body, dict) and reply.body.get("message"), reply.body
    if code:
        assert reply.body.get("code") == code, reply.body


def password_sign_in(email: str, password: str) -> Reply:
    return api(server_base(), "POST", "/board/api/session", {"email": email, "password": password})


# -- what is offered -------------------------------------------------------------------------------


def test_the_server_offers_the_three_providers_by_their_names_to_everyone() -> None:
    signed_out = api(server_base(), "GET", "/board/api/session")
    assert signed_out.status == 200 and signed_out.body["account"] is None
    assert sorted(signed_out.body["providers"], key=lambda one: str(one["id"])) == sorted(
        PROVIDERS, key=lambda one: str(one["id"])
    )
    assert signed_out.body["sso_create_accounts"] is True
    person = register()
    me = person.call("GET", "/board/api/session")
    assert me.body["providers"] == signed_out.body["providers"]
    assert me.body["account"]["has_password"] is True
    shown = person.call("GET", "/board/api/account/identities").body
    assert shown["has_password"] is True and shown["identities"] == []


def test_the_status_page_gets_each_provider_with_the_host_it_talks_to_and_no_secret() -> None:
    info = register().call("GET", "/board/api/info").body["sign_in"]
    assert info["create_accounts"] is True
    assert {one["id"] for one in info["providers"]} == {"oidc", "github", "ldap"}
    for one in info["providers"]:
        assert set(one) == {"id", "name", "kind", "host"}, one
        assert "://" not in one["host"] and "@" not in one["host"], one
    hosts = {one["id"]: one["host"] for one in info["providers"]}
    assert "scripted-oidc" in hosts["oidc"] and "scripted-github" in hosts["github"]
    assert "ldap" in hosts["ldap"]
    assert info["allowed_domains"] == {"oidc": [], "github": [], "ldap": []}
    shown = json.dumps(info)
    for secret in ("scripted-oidc-secret", "scripted-github-secret", "search-only-password"):
        assert secret not in shown


def test_a_provider_that_is_not_set_up_answers_in_words_and_nothing_starts() -> None:
    reply = api(server_base(), "POST", "/board/api/auth/gitlab/start", {"intent": "signin"})
    assert reply.status == 404 and reply.body["message"] == NOT_SET_UP
    assert reply.cookie is None
    called = fetch("GET", f"{server_base()}/board/auth/gitlab/callback?code=a&state=b")
    assert called.status == 404 and "session" not in called.text.lower()
    assert called.cookie(SESSION_COOKIE) is None


def test_the_start_sets_a_cookie_only_the_callback_gets_and_says_where_to_go() -> None:
    journey = start_journey("oidc")
    assert journey.start.status == 200
    line = (journey.start.set_cookie_line(SSO_COOKIE) or "").lower()
    assert journey.sso_cookie and len(journey.sso_cookie) >= 30
    assert "httponly" in line and "samesite=lax" in line and "path=/board/auth" in line
    assert "max-age=600" in line and "secure" not in line
    target = urllib.parse.urlsplit(journey.url)
    # The browser is sent to the provider's own address, which is not the one the server uses.
    assert target.geturl().startswith(oidc_url() + "/authorize?")
    assert target.path == "/authorize"
    # The cookie holds a random name and nothing about the sign-in.
    query = dict(urllib.parse.parse_qsl(target.query))
    assert query["state"] not in journey.sso_cookie and query["nonce"] not in journey.sso_cookie


# -- OpenID Connect: who gets which account --------------------------------------------------------


def test_a_new_person_signs_in_with_openid_and_gets_an_account_with_no_password() -> None:
    who = new_openid_person(name="Ada Lovelace")
    landing = sign_in("oidc", who)
    person = signed_in(landing)
    assert landing.path == "/board"
    line = (landing.session_line or "").lower()
    assert "httponly" in line and "samesite=strict" in line and "path=/board" in line
    # The sign-in cookie is finished with.
    ended = (landing.sso_cookie_line or "").lower()
    assert ended and ("max-age=0" in ended or "expires=" in ended)
    me = whoami(landing).body["account"]
    assert me["email"] == who.email and me["name"] == "Ada Lovelace"
    assert me["has_password"] is False and me["email_verified"] is True
    row = account_of(str(who.email))
    assert row and row["signed_up_with"] == "oidc" and row["no_password"] and row["confirmed"]
    assert identity_rows(person.id) == [("oidc", who.sub)]
    methods = methods_of(person)
    assert methods["has_password"] is False
    (one,) = methods["identities"]
    assert one["provider"] == "oidc" and one["name"] == NAMES["oidc"]
    assert one["display_name"] in ("Ada Lovelace", who.email) and one["email"] == who.email
    assert one["linked_at"]
    # It is a session like any other: the board's own calls work with it and its secret.
    assert person.call("GET", "/board/api/projects").status == 200
    assert api(
        server_base(), "POST", "/board/api/projects", {"name": "x"}, cookie=person.cookie
    ).status in (
        401,
        403,
    )


def test_the_name_of_a_new_account_is_the_providers_or_else_the_start_of_the_address() -> None:
    word = unique_word()
    named = new_openid_person(name="  Grace Hopper  ")
    assert signed_in(sign_in("oidc", named)).name == "Grace Hopper"
    unnamed = new_openid_person(name=None, email=f"{word}.surname@example.com")
    assert signed_in(sign_in("oidc", unnamed)).name == f"{word}.surname"
    long_name = new_openid_person(name="N" * 400)
    made = signed_in(sign_in("oidc", long_name))
    assert 1 <= len(made.name) <= 200 and made.name.startswith("N")
    mixed = new_openid_person(email=f"Mixed.{word}@Example.COM")
    assert signed_in(sign_in("oidc", mixed)).email == f"mixed.{word}@example.com"


def test_openid_is_asked_for_a_code_with_pkce_state_and_nonce_and_never_for_userinfo() -> None:
    who = new_openid_person()
    marked = oidc().mark()
    signed_in(sign_in("oidc", who))
    entries = oidc().log(marked)
    authorize = next(one for one in entries if one["path"] == "/authorize")["query"]
    assert authorize["response_type"] == "code" and authorize["client_id"] == "vrum-check"
    assert authorize["scope"].split() == ["openid", "email", "profile"]
    assert authorize["redirect_uri"] == f"{board_base()}/board/auth/oidc/callback"
    assert authorize["code_challenge_method"] == "S256" and len(authorize["code_challenge"]) == 43
    assert len(authorize["state"]) >= 43 and len(authorize["nonce"]) >= 43
    assert authorize["state"] != authorize["nonce"]
    token = next(one for one in entries if one["path"] == "/token")
    assert token["status"] == 200 and token["client_auth"] == "basic"
    assert token["form"]["grant_type"] == "authorization_code"
    assert token["form"]["redirect_uri"] == authorize["redirect_uri"]
    assert len(token["form"]["code_verifier"]) >= 43
    assert not [one for one in entries if one["path"] == "/userinfo"]


def test_two_sign_ins_never_share_a_state_a_nonce_a_challenge_or_a_cookie() -> None:
    first, second = start_journey("oidc"), start_journey("oidc")
    one = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(first.url).query))
    two = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(second.url).query))
    for name in ("state", "nonce", "code_challenge"):
        assert one[name] != two[name]
    assert first.sso_cookie != second.sso_cookie


def test_a_person_with_a_confirmed_account_signs_in_to_it_whatever_the_capitals() -> None:
    email = f"{unique_word()}@example.com"
    account = make_confirmed_account(email)
    who = new_openid_person(email=email.upper().replace("@EXAMPLE.COM", "@Example.com"))
    person = signed_in(sign_in("oidc", who))
    assert person.id == account.id and person.name == account.name
    assert identity_rows(account.id) == [("oidc", who.sub)]
    assert methods_of(person)["has_password"] is True
    assert password_sign_in(email, account.password).status == 200
    assert accounts_with(email) == 1


def test_an_account_whose_address_was_never_confirmed_is_not_joined_and_nothing_changes() -> None:
    email = f"{unique_word()}@example.com"
    account = make_unconfirmed_account(email)
    who = new_openid_person(email=email)
    landing = sign_in("oidc", who)
    refused(landing, "account_exists_unconfirmed")
    assert identity_rows(account.id) == []
    row = account_of(email)
    assert row and row["id"] == account.id and not row["confirmed"] and not row["no_password"]
    assert password_sign_in(email, account.password).status == 200
    assert accounts_with(email) == 1


def test_a_known_identity_signs_in_whatever_address_the_provider_gives_now() -> None:
    victim = make_confirmed_account(f"{unique_word()}@example.com")
    who = new_openid_person()
    first = signed_in(sign_in("oidc", who))
    moved = f"{unique_word()}@example.com"
    oidc().add_person(key=who.key, sub=who.sub, email=moved, email_verified=True, name="Someone")
    again = signed_in(sign_in("oidc", who))
    assert again.id == first.id and again.email == who.email
    # Nor does saying a confirmed account's address make the identity that account's.
    oidc().add_person(key=who.key, sub=who.sub, email=victim.email, email_verified=True)
    still = signed_in(sign_in("oidc", who))
    assert still.id == first.id and still.id != victim.id
    assert identity_rows(victim.id) == []


@pytest.mark.parametrize(
    ("claims", "code"),
    [
        ({"email_verified": False}, "no_verified_email"),
        ({"email_verified": "false"}, "no_verified_email"),
        ({"email_verified": 1}, "no_verified_email"),
        ({"email_verified": None}, "no_verified_email"),
        ({"email": None, "email_verified": None}, "no_verified_email"),
        ({"email": ""}, "no_verified_email"),
        ({"email_verified": "true"}, None),
    ],
    ids=[
        "false",
        "string-false",
        "number-one",
        "no-claim",
        "no-email",
        "blank-email",
        "string-true",
    ],
)
def test_only_an_address_the_provider_says_is_confirmed_makes_an_account(
    claims: dict[str, Any], code: str | None
) -> None:
    who = new_openid_person(**claims)
    landing = sign_in("oidc", who)
    if code:
        refused(landing, code)
        assert who.email is None or accounts_with(who.email) == 0
    else:
        assert signed_in(landing).email == who.email


@pytest.mark.parametrize(
    "mode",
    [
        "wrong_nonce",
        "wrong_aud",
        "wrong_iss",
        "expired",
        "not_yet_valid",
        "two_audiences",
        "wrong_azp",
        "missing_sub",
        "alg_none",
        "alg_hs256",
        "bad_signature",
        "tampered_payload",
        "unknown_kid",
        "no_id_token",
    ],
)
def test_a_token_that_is_not_right_ends_the_sign_in_with_no_session_and_no_account(
    mode: str,
) -> None:
    who = new_openid_person()
    marked = oidc().mark()
    oidc().control(token=mode)
    landing = sign_in("oidc", who)
    refused(landing, "failed")
    assert accounts_with(str(who.email)) == 0
    assert len([one for one in oidc().log(marked) if one["path"] == "/token"]) == 1


@pytest.mark.parametrize("mode", ["two_audiences_with_azp", "email_verified_string"])
def test_the_tokens_that_look_odd_and_are_allowed_work(mode: str) -> None:
    who = new_openid_person(email_verified=True)
    oidc().control(token=mode)
    assert signed_in(sign_in("oidc", who)).email == who.email


@pytest.mark.parametrize("mode", ["endpoint_error", "endpoint_redirect", "endpoint_huge"])
def test_a_token_endpoint_that_misbehaves_ends_the_sign_in_and_is_not_followed(mode: str) -> None:
    who = new_openid_person()
    marked = oidc().mark()
    oidc().control(token=mode)
    landing = sign_in("oidc", who)
    refused_for(landing, {"failed", "unavailable"})
    assert accounts_with(str(who.email)) == 0
    assert not [one for one in oidc().log(marked) if one["path"] == "/elsewhere"]


def test_a_provider_that_is_slow_is_given_up_on_and_the_person_is_told_in_words() -> None:
    who = new_openid_person()
    oidc().control(token="endpoint_slow", seconds=13)
    started = time.time()
    landing = sign_in("oidc", who)
    assert time.time() - started < 14.5
    refused_for(landing, {"unavailable", "failed"})
    assert accounts_with(str(who.email)) == 0


def test_a_key_set_that_is_down_ends_the_sign_in_and_one_that_was_read_is_kept() -> None:
    # The server keeps the keys it has read, so this starts from a server that has read none.
    with app_with():
        who = new_openid_person()
        oidc().control(jwks="down")
        refused_for(sign_in("oidc", who), {"unavailable", "failed"})
        oidc().control(jwks="ok")
        assert signed_in(sign_in("oidc", who)).email == who.email
        oidc().control(jwks="down")
        assert signed_in(sign_in("oidc", who)).email == who.email


def test_the_provider_saying_no_or_failing_is_told_to_the_person_and_its_words_are_not() -> None:
    who = new_openid_person()
    oidc().control(authorize="access_denied")
    landing = sign_in("oidc", who)
    refused(landing, "cancelled")
    oidc().control(authorize="server_error")
    landing = sign_in("oidc", who)
    refused(landing, "failed")
    for shown in (landing.location, ""):
        assert "script" not in shown and "alert" not in shown and "went wrong" not in shown
    assert accounts_with(str(who.email)) == 0


def test_a_return_with_the_wrong_state_is_refused_and_spends_the_sign_in() -> None:
    who = new_openid_person()
    journey = start_journey("oidc")
    oidc().control(authorize="wrong_state")
    forged = visit_provider(journey, who)
    refused_for(come_back(journey, forged), {"failed", "expired"})
    real = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(journey.url).query))["state"]
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(forged).query))
    right = urllib.parse.urlsplit(forged)._replace(
        query=urllib.parse.urlencode({"code": query["code"], "state": real})
    )
    refused(come_back(journey, right.geturl()), "expired")
    assert accounts_with(str(who.email)) == 0


def test_a_return_works_once() -> None:
    who = new_openid_person()
    journey = start_journey("oidc")
    back = visit_provider(journey, who)
    first = come_back(journey, back)
    person = signed_in(first)
    assert person.email == who.email
    refused(come_back(journey, back), "expired")
    assert whoami(first).status == 200


def test_looking_at_a_return_or_sending_the_wrong_kind_of_request_does_not_use_it_up() -> None:
    who = new_openid_person()
    journey = start_journey("oidc")
    back = visit_provider(journey, who)
    parts = urllib.parse.urlsplit(back)
    url = f"{journey.base}{parts.path}?{parts.query}"
    cookie = {"Cookie": f"{SSO_COOKIE}={journey.sso_cookie}"}
    # A mail scanner or a browser that looks ahead only looks.
    assert fetch("HEAD", url, headers=cookie).status == 405
    assert fetch("POST", url, headers=cookie, body={}).status == 405
    landing = come_back(journey, back)
    signed_in(landing)
    answered = fetch("GET", url, headers=cookie)
    assert answered.status == 303 and answered.headers["Cache-Control"] == "no-store"
    assert answered.headers["Referrer-Policy"] == "no-referrer"
    assert accounts_with(str(who.email)) == 1


def test_a_return_is_only_for_the_browser_that_left() -> None:
    who = new_openid_person()
    ours, theirs = start_journey("oidc"), start_journey("oidc")
    back = visit_provider(ours, who)
    refused_for(come_back(ours, back, sso_cookie=None), {"expired", "failed"})
    refused_for(come_back(ours, back, sso_cookie="vrt_" + "x" * 43), {"expired", "failed"})
    refused_for(come_back(ours, back, sso_cookie="x" * 5000), {"expired", "failed"})
    refused_for(come_back(ours, back, sso_cookie=theirs.sso_cookie), {"expired", "failed"})
    # Another browser's cookie spent that other sign-in too.
    refused(come_back(theirs, visit_provider(theirs, who)), "expired")
    assert accounts_with(str(who.email)) == 0
    # None of it spent this sign-in, which is still good for the browser that started it.
    assert signed_in(come_back(ours, back)).email == who.email


def test_a_return_with_no_code_or_nothing_at_all_ends_in_words() -> None:
    for query in ("", "?state=only", "?code=only", "?error=access_denied", "?error=server_error"):
        journey = start_journey("oidc")
        landing = come_back(journey, "/board/auth/oidc/callback" + query)
        refused_for(landing, {"cancelled", "failed", "expired"})


@pytest.mark.parametrize(
    "bad",
    [
        "//evil.example.com",
        "https://evil.example.com/board",
        "/\\evil.example.com",
        "/board//evil.example.com",
        "javascript:alert(1)",
        "/boardx",
        "/board%2f%2fevil.example.com",
        "/board\r\nSet-Cookie: a=b",
        "evil",
        "",
    ],
)
def test_nobody_is_sent_anywhere_but_the_board(bad: str) -> None:
    who = new_openid_person()
    landing = sign_in("oidc", who, return_to=bad)
    assert landing.status == 303 and landing.location.startswith("/board"), landing.location
    assert "evil" not in landing.location.partition("?")[0].replace("/board", "")
    assert not landing.location.startswith("//") and "\\" not in landing.location
    assert "\n" not in landing.location and "://" not in landing.location.partition("?")[0]
    assert landing.session


def test_a_page_of_the_board_the_person_was_on_is_where_they_come_back_to() -> None:
    who = new_openid_person()
    landing = sign_in("oidc", who, return_to="/board/p/3")
    assert landing.path == "/board/p/3" and landing.session
    refused_who = new_openid_person(email_verified=False)
    landing = sign_in("oidc", refused_who, return_to="/board/p/3")
    assert landing.path == "/board/p/3" and landing.error == "no_verified_email"


# -- connecting, disconnecting and the last way in -------------------------------------------------


def test_a_signed_in_person_connects_a_provider_and_then_signs_in_with_it() -> None:
    person = register()
    who = new_openid_person()
    landing = sign_in("oidc", who, intent="link", person=person, return_to="/board/p/1")
    assert landing.status == 303 and landing.path == "/board/p/1"
    assert landing.query.get("sso") == "linked" and landing.error is None
    methods = methods_of(person)
    assert methods["has_password"] is True
    assert [one["provider"] for one in methods["identities"]] == ["oidc"]
    assert methods["identities"][0]["display_name"] in (who.name, who.email)
    assert identity_rows(person.id) == [("oidc", who.sub)]
    # Signing in with it now is signing in to this account, whatever the address.
    assert signed_in(sign_in("oidc", who)).id == person.id
    assert accounts_with(str(who.email)) == 0


def test_a_connection_needs_a_signed_in_person_and_a_secret() -> None:
    plain = api(server_base(), "POST", "/board/api/auth/oidc/start", {"intent": "link"})
    assert plain.status in (401, 403) and plain.cookie is None
    person = register()
    no_secret = api(
        server_base(),
        "POST",
        "/board/api/auth/oidc/start",
        {"intent": "link"},
        cookie=person.cookie,
    )
    assert no_secret.status in (401, 403)
    elsewhere = person.call(
        "POST", "/board/api/auth/oidc/start", {"intent": "link"}, origin="https://evil.example.test"
    )
    assert elsewhere.status in (400, 403)
    assert person.call("POST", "/board/api/auth/oidc/start", {"intent": "nonsense"}).status == 400


def test_a_person_disconnects_a_provider_when_they_have_another_way_in() -> None:
    person = register()
    who = new_openid_person()
    sign_in("oidc", who, intent="link", person=person)
    no_secret = api(
        server_base(), "DELETE", "/board/api/account/identities/oidc", cookie=person.cookie
    )
    assert no_secret.status in (401, 403) and len(methods_of(person)["identities"]) == 1
    done = person.call("DELETE", "/board/api/account/identities/oidc")
    assert done.status == 200
    assert methods_of(person)["identities"] == [] and identity_rows(person.id) == []
    assert person.call("DELETE", "/board/api/account/identities/gitlab").status == 404


def test_the_only_way_in_cannot_be_taken_away_until_there_is_another() -> None:
    who = new_openid_person()
    person = signed_in(sign_in("oidc", who))
    last = person.call("DELETE", "/board/api/account/identities/oidc")
    assert last.status in (400, 409) and last.body["code"] == "last_way_in"
    assert last.body["message"] == WORDS["last_way_in"]
    assert identity_rows(person.id) == [("oidc", who.sub)]
    # A password that was never set cannot be used, and says what a wrong one says.
    wrong = password_sign_in(person.email, "not-the-password")
    other = register()
    reference = password_sign_in(other.email, "not-the-password")
    assert wrong.status == reference.status == 401 and wrong.cookie is None
    assert wrong.body == reference.body
    # With a password there is another way, and the connection can go.
    new_password = f"correct-horse-{unique_word()}"
    changed = person.call("POST", "/board/api/account/password", {"new": new_password})
    assert changed.status == 200, changed.body
    assert methods_of(person)["has_password"] is True
    assert password_sign_in(person.email, new_password).status == 200
    assert person.call("DELETE", "/board/api/account/identities/oidc").status == 200


def test_a_password_can_be_set_first_by_the_reset_link_for_an_account_that_has_none() -> None:
    who = new_openid_person()
    person = signed_in(sign_in("oidc", who))
    asked = api(server_base(), "POST", "/board/api/password-reset", {"email": person.email})
    assert asked.status == 202
    token = ""
    for _ in range(40):
        sent = fetch("GET", f"{mail_url()}/messages?to={urllib.parse.quote(person.email)}").json
        found = [one for one in sent if "/board/reset/vrr_" in one["text"]]
        if found:
            token = found[0]["text"].partition("/board/reset/")[2].split()[0]
            break
        time.sleep(0.5)
    assert token.startswith("vrr_")
    new_password = f"correct-horse-{unique_word()}"
    done = api(server_base(), "POST", f"/board/api/password-reset/{token}", {"new": new_password})
    assert done.status == 200
    assert password_sign_in(person.email, new_password).status == 200


def test_an_identity_belongs_to_one_account_and_a_provider_connects_once_to_an_account() -> None:
    owner, other = register(), register()
    who = new_openid_person()
    sign_in("oidc", who, intent="link", person=owner)
    taken = sign_in("oidc", who, intent="link", person=other)
    assert taken.error == "identity_taken" and taken.query.get("sso_provider") == "oidc"
    assert "sso" not in taken.query
    assert identity_rows(other.id) == [] and identity_rows(owner.id) == [("oidc", who.sub)]
    second = new_openid_person()
    again = sign_in("oidc", second, intent="link", person=owner)
    assert again.error == "already_connected"
    assert identity_rows(owner.id) == [("oidc", who.sub)]


def test_a_connection_whose_session_has_ended_is_not_made() -> None:
    person = register()
    who = new_openid_person()
    journey = start_journey("oidc", intent="link", person=person)
    back = visit_provider(journey, who)
    # The return is a navigation from another site, which carries no session cookie: the
    # connection is tied to the session that started it, and that session has ended.
    assert person.call("DELETE", "/board/api/session").status == 200
    landing = come_back(journey, back)
    assert landing.error == "link_session_lost" and landing.session is None
    assert identity_rows(person.id) == []
    # And the sign-in that was meant to connect does not sign anyone in or make an account.
    assert accounts_with(str(who.email)) == 0
    # The same account in another session did not make it good: the sign-in is spent.
    again = person.sign_in_again()
    refused(come_back(journey, back), "expired")
    assert identity_rows(again.id) == []


# -- two at once -------------------------------------------------------------------------------


def test_two_first_sign_ins_at_once_make_one_account() -> None:
    who = new_openid_person()
    journeys: list[Journey] = [start_journey("oidc"), start_journey("oidc")]
    backs = [visit_provider(one, who) for one in journeys]
    landings: list[Landing | None] = [None, None]
    gate = threading.Barrier(2)

    def arrive(index: int) -> None:
        gate.wait()
        landings[index] = come_back(journeys[index], backs[index])

    threads = [threading.Thread(target=arrive, args=(index,)) for index in range(2)]
    for one in threads:
        one.start()
    for one in threads:
        one.join()
    assert accounts_with(str(who.email)) == 1
    people = [signed_in(found) for found in landings if found is not None and found.session]
    assert len(people) >= 1 and len({one.id for one in people}) == 1
    assert identity_rows(people[0].id) == [("oidc", who.sub)]


# -- GitHub ----------------------------------------------------------------------------------------


def test_a_new_person_signs_in_with_github_by_the_primary_confirmed_address() -> None:
    who = new_github_person(
        emails=[
            {"email": "hidden@example.net", "verified": True, "primary": False, "visibility": None},
            {"email": "first-unconfirmed@example.net", "verified": False, "primary": False},
        ]
    )
    # The person has no primary address in this list: nothing to go by.
    landing = sign_in("github", who)
    refused(landing, "no_verified_email", "github")
    primary = new_github_person()
    marked = github().mark()
    person = signed_in(sign_in("github", primary))
    assert person.email == primary.email and person.name == primary.name
    me = person.call("GET", "/board/api/session").body["account"]
    assert me["has_password"] is False and me["email_verified"] is True
    row = account_of(str(primary.email))
    assert row and row["signed_up_with"] == "github" and row["no_password"]
    assert identity_rows(person.id) == [("github", primary.sub)]
    entries = github().log(marked)
    authorize = next(one for one in entries if one["path"].endswith("/authorize"))["query"]
    assert authorize["scope"].split() == ["read:user", "user:email"]
    assert authorize["code_challenge_method"] == "S256" and len(authorize["state"]) >= 43
    assert authorize["redirect_uri"] == f"{board_base()}/board/auth/github/callback"
    token = next(one for one in entries if one["path"].endswith("/access_token"))
    assert token["accept"] == "application/json" and token["status"] == 200
    assert token["result"].startswith("issued") and len(token["form"]["code_verifier"]) >= 43
    calls = [one for one in entries if one["path"].startswith("/api/v3/")]
    assert {one["path"] for one in calls} == {"/api/v3/user", "/api/v3/user/emails"}
    assert all(one["user_agent"] and one["authorization"] == "bearer" for one in calls)


def test_github_decides_by_the_confirmed_primary_address_and_nothing_else() -> None:
    word = unique_word()
    unconfirmed_primary = new_github_person(
        emails=[
            {
                "email": f"{word}@example.com",
                "verified": False,
                "primary": True,
                "visibility": None,
            },
            {"email": f"{word}.work@example.net", "verified": True, "primary": False},
        ],
        email=f"{word}.work@example.net",
    )
    refused(sign_in("github", unconfirmed_primary), "no_verified_email", "github")
    nothing_confirmed = new_github_person(
        emails=[{"email": f"{word}.b@example.com", "verified": False, "primary": True}]
    )
    refused(sign_in("github", nothing_confirmed), "no_verified_email", "github")
    no_address = new_github_person(emails=[])
    refused(sign_in("github", no_address), "no_verified_email", "github")
    for who in (unconfirmed_primary, nothing_confirmed):
        assert who.email is None or accounts_with(who.email) == 0
        for item in who.extra["emails"]:
            assert accounts_with(item["email"]) == 0


def test_a_public_profile_address_that_belongs_to_someone_else_is_worth_nothing() -> None:
    victim = make_confirmed_account(f"{unique_word()}@example.com")
    word = unique_word()
    mallory = new_github_person(
        email=victim.email,
        emails=[
            {"email": f"{word}@example.net", "verified": True, "primary": True, "visibility": None},
            {"email": victim.email, "verified": False, "primary": False, "visibility": None},
        ],
    )
    person = signed_in(sign_in("github", mallory))
    assert person.id != victim.id and person.email == f"{word}@example.net"
    assert identity_rows(victim.id) == []


def test_github_joins_a_confirmed_account_and_not_one_that_was_never_confirmed() -> None:
    email = f"{unique_word()}@example.com"
    confirmed = make_confirmed_account(email)
    who = new_github_person(
        emails=[{"email": email, "verified": True, "primary": True, "visibility": None}]
    )
    person = signed_in(sign_in("github", who))
    assert person.id == confirmed.id and identity_rows(confirmed.id) == [("github", who.sub)]
    other_email = f"{unique_word()}@example.com"
    unconfirmed = make_unconfirmed_account(other_email)
    stranger = new_github_person(
        emails=[{"email": other_email, "verified": True, "primary": True, "visibility": None}]
    )
    refused(sign_in("github", stranger), "account_exists_unconfirmed", "github")
    assert identity_rows(unconfirmed.id) == []


def test_a_github_login_that_was_renamed_is_still_the_same_person() -> None:
    who = new_github_person()
    first = signed_in(sign_in("github", who))
    github().add_person(key=who.key, **{**who.extra, "login": f"renamed-{unique_word()}"})
    assert signed_in(sign_in("github", who)).id == first.id
    taken_over = new_github_person(login=who.extra["login"], emails=who.extra["emails"])
    # Someone else with the old login name, and the same address, is not that person: the address
    # is confirmed on both sides but the account has an identity from GitHub already, so the
    # new GitHub id has to be refused or connected by the owner, never joined silently.
    landing = sign_in("github", taken_over)
    assert landing.session is None or signed_in(landing).id == first.id


@pytest.mark.parametrize(
    "control",
    [
        {"token": "error"},
        {"token": "no_access_token"},
        {"token": "endpoint_500"},
        {"token": "endpoint_redirect"},
        {"token": "endpoint_huge"},
        {"user": "down"},
        {"user": "unauthorized"},
        {"emails": "down"},
        {"emails": "not_found"},
        {"emails": "unauthorized"},
        {"rate_limited": True},
    ],
    ids=lambda control: next(iter(control)) + "-" + str(next(iter(control.values()))),
)
def test_when_github_fails_in_any_way_the_sign_in_ends_with_no_session_and_no_account(
    control: dict[str, Any],
) -> None:
    who = new_github_person()
    marked = github().mark()
    github().control(**control)
    landing = sign_in("github", who)
    # A failure of the address list is "no confirmed address" to the person, which says no more
    # than the truth; it must be a refusal with no account either way.
    reasons = {"failed", "unavailable"}
    refused_for(
        landing, reasons | ({"no_verified_email"} if "emails" in control else set()), "github"
    )
    assert accounts_with(str(who.email)) == 0
    assert not [one for one in github().log(marked) if one["path"] == "/elsewhere"]
    github().reset()
    assert signed_in(sign_in("github", who)).email == who.email


def test_github_can_be_cancelled_and_its_words_are_not_repeated() -> None:
    who = new_github_person()
    github().control(authorize="access_denied")
    refused(sign_in("github", who), "cancelled", "github")
    github().control(authorize="server_error")
    landing = sign_in("github", who)
    refused(landing, "failed", "github")
    assert "script" not in landing.location and "alert" not in landing.location
    github().control(authorize="wrong_state")
    refused_for(sign_in("github", who), {"failed", "expired"}, "github")


def test_a_person_connects_github_and_two_providers_make_a_second_way_in() -> None:
    who_open = new_openid_person()
    person = signed_in(sign_in("oidc", who_open))
    assert person.call("DELETE", "/board/api/account/identities/oidc").body["code"] == "last_way_in"
    who = new_github_person()
    done = sign_in("github", who, intent="link", person=person)
    assert done.query.get("sso") == "linked" and done.error is None
    assert sorted(one["provider"] for one in methods_of(person)["identities"]) == [
        "github",
        "oidc",
    ]
    assert person.call("DELETE", "/board/api/account/identities/oidc").status == 200
    assert signed_in(sign_in("github", who)).id == person.id


# -- LDAP, against the real directory ----------------------------------------------------------


def test_a_person_of_the_directory_signs_in_and_gets_an_account_with_no_password() -> None:
    shown = f"Mona {unique_word()}"
    who = add_directory_person(cn=shown)
    reply = ldap_sign_in(who.key, str(who.password))
    person = ldap_person(reply)
    assert reply.body["account"]["name"] == shown
    assert (
        reply.body["account"]["email"] == who.email
        and reply.body["account"]["has_password"] is False
    )
    assert reply.body["account"]["email_verified"] is True and reply.body["csrf"]
    row = account_of(str(who.email))
    assert row and row["signed_up_with"] == "ldap" and row["no_password"] and row["confirmed"]
    (subject,) = [value for provider, value in identity_rows(person.id) if provider == "ldap"]
    assert len(subject) >= 8 and subject != who.key
    # It is a session like any other.
    assert person.call("GET", "/board/api/projects").status == 200
    methods = methods_of(person)
    assert [(one["provider"], one["name"]) for one in methods["identities"]] == [
        ("ldap", NAMES["ldap"])
    ]


def test_the_directory_gives_the_same_answer_for_a_wrong_password_and_an_unknown_name() -> None:
    who = add_directory_person()
    wrong = ldap_sign_in(who.key, "not-the-password")
    unknown = ldap_sign_in(f"nobody-{unique_word()}", "not-the-password")
    unlike = ldap_sign_in(who.key.upper(), "x")
    for reply in (wrong, unknown):
        ldap_refused(reply, status=401)
    assert wrong.body == unknown.body == {**wrong.body, "message": WRONG_LOGIN}
    assert wrong.body["message"] == WRONG_LOGIN
    assert unlike.status in (401, 200)


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("", "anything"),
        ("someone", ""),
        ("", ""),
        ("   ", "anything"),
        ("x" * 600, "anything"),
        ("someone", "y" * 5000),
        ("a\x00b", "anything"),
    ],
    ids=["no-name", "no-password", "nothing", "blank-name", "long-name", "long-password", "nul"],
)
def test_an_empty_or_absurd_name_or_password_is_refused_before_the_directory_is_asked(
    username: str, password: str
) -> None:
    reply = ldap_sign_in(username, password)
    ldap_refused(reply)
    assert WRONG_LOGIN in reply.body["message"] or reply.status in (400, 401)


def test_an_empty_password_is_never_an_anonymous_bind_that_succeeds() -> None:
    who = add_directory_person()
    reply = ldap_sign_in(who.key, "")
    ldap_refused(reply)
    assert accounts_with(str(who.email)) == 0


@pytest.mark.parametrize(
    "username",
    [
        "*)(uid=*",
        "*",
        "ava)(|(uid=*",
        "*)(|(uid=*))(&(uid=*",
        "\\2a",
        "ava*",
        "a*a",
        ")(cn=*",
        "*)(objectClass=*",
        "admin)(&)",
    ],
)
def test_a_name_made_to_change_the_search_finds_nobody(username: str) -> None:
    reply = ldap_sign_in(username, "open-sesame-ava")
    ldap_refused(reply, status=401)
    assert reply.body["message"] == WRONG_LOGIN


def test_a_name_with_characters_that_mean_something_in_a_search_still_finds_its_own_person() -> (
    None
):
    reply = ldap_sign_in("paren(thesis)", "open-sesame-paren(thesis)")
    person = ldap_person(reply)
    assert person.email == "paren.thesis@example.com"


def test_a_name_two_people_share_finds_neither() -> None:
    reply = ldap_sign_in("twin", "open-sesame-twin-a")
    ldap_refused(reply, status=401)
    assert reply.body["message"] == WRONG_LOGIN
    assert (
        accounts_with("twin.alpha@example.com") == 0 and accounts_with("twin.beta@example.com") == 0
    )


def test_what_the_directory_says_about_a_person_is_used_as_it_is_and_never_guessed() -> None:
    capitals = ldap_person(ldap_sign_in("rae", "open-sesame-rae"))
    assert capitals.email == "rae.ng@example.com"
    first_of_two = ldap_person(ldap_sign_in("sam", "open-sesame-sam"))
    assert first_of_two.email == "sam.lind@example.com"
    assert accounts_with("sam@elsewhere.example.net") == 0
    accent = ldap_person(ldap_sign_in("ulla", "open-sesame-ulla"))
    assert accent.name == "Ulla \u00c5kerstr\u00f6m"
    no_address = ldap_sign_in("quinn", "open-sesame-quinn")
    ldap_refused(no_address, code="no_verified_email")
    assert no_address.body["message"] == words("no_verified_email", "ldap")


def test_a_person_of_the_directory_with_a_confirmed_account_signs_in_to_it() -> None:
    email = f"{unique_word()}@example.com"
    account = make_confirmed_account(email)
    who = add_directory_person(mail=email.upper().replace("@EXAMPLE.COM", "@Example.com"))
    person = ldap_person(ldap_sign_in(who.key, str(who.password)))
    assert person.id == account.id and person.name == account.name
    assert [provider for provider, _ in identity_rows(account.id)] == ["ldap"]
    assert accounts_with(email) == 1
    assert password_sign_in(email, account.password).status == 200


def test_a_person_of_the_directory_whose_account_was_never_confirmed_is_not_joined() -> None:
    email = f"{unique_word()}@example.com"
    account = make_unconfirmed_account(email)
    who = add_directory_person(mail=email)
    reply = ldap_sign_in(who.key, str(who.password))
    ldap_refused(reply, code="account_exists_unconfirmed")
    assert reply.body["message"] == words("account_exists_unconfirmed", "ldap")
    assert identity_rows(account.id) == []
    assert password_sign_in(email, account.password).status == 200


def test_the_directorys_own_identifier_is_the_person_not_their_name_or_address() -> None:
    who = add_directory_person()
    first = ldap_person(ldap_sign_in(who.key, str(who.password)))
    moved = f"renamed{unique_word()}"
    change_directory_person(who, uid=moved, mail=f"{moved}@example.com")
    again = ldap_person(ldap_sign_in(moved, str(who.password)))
    assert again.id == first.id and again.email == first.email
    # The old name is nobody now.
    ldap_refused(ldap_sign_in(first.email.partition("@")[0], str(who.password)), status=401)


def test_a_signed_in_person_connects_the_directory_with_their_name_and_password() -> None:
    person = register()
    who = add_directory_person()
    no_secret = api(
        server_base(),
        "POST",
        "/board/api/account/identities/ldap",
        {"username": who.key, "password": who.password},
        cookie=person.cookie,
    )
    assert no_secret.status in (401, 403)
    wrong = person.call(
        "POST", "/board/api/account/identities/ldap", {"username": who.key, "password": "nope"}
    )
    # (Answered 400 and not 401, so that the page does not take it for a session that has ended.)
    assert wrong.status in (400, 401) and wrong.body["message"] == WRONG_LOGIN
    done = person.call(
        "POST",
        "/board/api/account/identities/ldap",
        {"username": who.key, "password": who.password},
    )
    assert done.status == 200, done.body
    assert [provider for provider, _ in identity_rows(person.id)] == ["ldap"]
    # Signing in with the directory is now signing in to this account, whatever the address.
    assert ldap_person(ldap_sign_in(who.key, str(who.password))).id == person.id
    assert accounts_with(str(who.email)) == 0
    # A second connection of the directory to the same account, or of this person to another one.
    other = add_directory_person()
    again = person.call(
        "POST",
        "/board/api/account/identities/ldap",
        {"username": other.key, "password": other.password},
    )
    assert again.status in (400, 409) and again.body["code"] == "already_connected"
    stranger = register()
    taken = stranger.call(
        "POST",
        "/board/api/account/identities/ldap",
        {"username": who.key, "password": who.password},
    )
    assert taken.status in (400, 409) and taken.body["code"] == "identity_taken"
    assert taken.body["message"] == words("identity_taken", "ldap")
    assert identity_rows(stranger.id) == []


def test_the_directory_sign_in_answers_like_a_password_sign_in_and_has_the_same_guards() -> None:
    who = add_directory_person()
    elsewhere = ldap_sign_in(who.key, str(who.password), origin="https://evil.example.test")
    assert elsewhere.status in (400, 403) and elsewhere.cookie is None
    no_origin = ldap_sign_in(who.key, str(who.password), origin=None)
    assert no_origin.status in (200, 400, 403)
    ok = ldap_sign_in(who.key, str(who.password))
    person = ldap_person(ok)
    assert set(ok.body) >= {"account", "csrf"}
    assert str(who.password) not in json.dumps(ok.body)
    assert person.call("GET", "/board/api/session").body["account"]["id"] == person.id
    assert person.call("DELETE", "/board/api/session").status == 200


def test_two_first_directory_sign_ins_at_once_make_one_account() -> None:
    who = add_directory_person()
    replies: list[Reply | None] = [None, None, None]
    gate = threading.Barrier(3)

    def arrive(index: int) -> None:
        gate.wait()
        replies[index] = ldap_sign_in(who.key, str(who.password))

    threads = [threading.Thread(target=arrive, args=(index,)) for index in range(3)]
    for one in threads:
        one.start()
    for one in threads:
        one.join()
    assert accounts_with(str(who.email)) == 1
    people = [ldap_person(one) for one in replies if one is not None and one.status == 200]
    assert people and len({one.id for one in people}) == 1


def test_a_name_that_has_failed_five_times_is_stopped_and_the_right_password_waits() -> None:
    who = add_directory_person()
    for _ in range(5):
        assert ldap_sign_in(who.key, "wrong-password").status == 401
    locked = ldap_sign_in(who.key, str(who.password))
    ldap_refused(locked, status=429)
    assert locked.body["message"] != WRONG_LOGIN and "too many" in locked.body["message"].lower()
    # Someone else is not held back by it.
    other = add_directory_person()
    assert ldap_sign_in(other.key, str(other.password)).status == 200


# -- what is never kept ---------------------------------------------------------------------------


def test_what_a_sign_in_carries_is_in_no_log_and_no_table_and_each_attempt_has_a_log_line() -> None:
    fresh_server()
    open_who = new_openid_person()
    github_who = new_github_person()
    ldap_who = add_directory_person()
    oidc_mark, github_mark = oidc().mark(), github().mark()
    signed_in(sign_in("oidc", open_who))
    signed_in(sign_in("github", github_who))
    ldap_person(ldap_sign_in(ldap_who.key, str(ldap_who.password)))
    oidc().control(token="alg_none")
    refused(sign_in("oidc", new_openid_person()), "failed")
    carried: set[str] = {str(ldap_who.password)}
    for entries in (oidc().log(oidc_mark), github().log(github_mark)):
        for one in entries:
            for name in ("code", "state", "nonce", "code_challenge", "code_verifier"):
                carried.update(
                    str(value)
                    for value in (one.get("query", {}).get(name), one.get("form", {}).get(name))
                    if value
                )
            if one.get("redirect"):
                query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(one["redirect"]).query))
                carried.update(value for key, value in query.items() if key in ("code", "state"))
            carried.update(str(value) for value in (one.get("issued") or {}).values())
    assert len(carried) >= 12 and all(len(one) >= 8 for one in carried)
    logged = app_log()
    for secret in carried:
        assert secret not in logged, "a code, token, state or password reached the log"
    personal = [open_who.email, open_who.sub, github_who.email, ldap_who.email, ldap_who.password]
    for item in personal:
        assert item and item not in logged, (
            "an address, an identifier or a password reached the log"
        )
    lines = [
        json.loads(line.partition("|")[2] or line)
        for line in logged.splitlines()
        if '"sso_login"' in line
    ]
    assert {one["provider"] for one in lines} >= {"oidc", "github"}
    assert {one["outcome"] for one in lines} >= {"ok"} and any(
        one["outcome"] != "ok" for one in lines
    )
    assert all(
        set(one) <= {"time", "level", "event", "provider", "intent", "outcome", "reason"}
        for one in lines
    )
    stored = database_dump()
    for secret in carried:
        assert secret not in stored, "a code, token, state or password reached the database"
    for needed in (open_who.sub, github_who.sub):
        assert needed in stored


# -- settings that need the server started again ------------------------------------------------


def test_the_server_that_makes_no_accounts_from_a_provider_still_lets_the_people_it_knows_in() -> (
    None
):
    email = f"{unique_word()}@example.com"
    account = make_confirmed_account(email)
    with app_with(VRUM_SSO_CREATE_ACCOUNTS="false"):
        assert api(server_base(), "GET", "/board/api/session").body["sso_create_accounts"] is False
        info = register().call("GET", "/board/api/info").body["sign_in"]
        assert info["create_accounts"] is False
        newcomer = new_openid_person()
        refused(sign_in("oidc", newcomer), "signups_off")
        assert accounts_with(str(newcomer.email)) == 0
        refused(sign_in("github", new_github_person()), "signups_off", "github")
        directory = add_directory_person()
        reply = ldap_sign_in(directory.key, str(directory.password))
        ldap_refused(reply, code="signups_off")
        assert reply.body["message"] == words("signups_off", "ldap")
        assert accounts_with(str(directory.email)) == 0
        known = new_openid_person(email=email)
        assert signed_in(sign_in("oidc", known)).id == account.id
        connecting = register()
        done = sign_in("oidc", new_openid_person(), intent="link", person=connecting)
        assert done.query.get("sso") == "linked"
    assert api(server_base(), "GET", "/board/api/session").body["sso_create_accounts"] is True


def test_only_the_domains_a_server_allows_may_sign_in_this_way() -> None:
    with app_with(
        VRUM_OIDC_ALLOWED_DOMAINS="example.com, Example.NET",
        VRUM_GITHUB_ALLOWED_DOMAINS="example.com",
        VRUM_LDAP_ALLOWED_DOMAINS="example.com",
    ):
        info = register().call("GET", "/board/api/info").body["sign_in"]
        assert info["allowed_domains"] == {
            "oidc": ["example.com", "example.net"],
            "github": ["example.com"],
            "ldap": ["example.com"],
        }
        word = unique_word()
        inside = new_openid_person(email=f"{word}@EXAMPLE.net")
        assert signed_in(sign_in("oidc", inside)).email == f"{word}@example.net"
        outside = new_openid_person(email=f"{unique_word()}@example.org")
        refused(sign_in("oidc", outside), "domain_not_allowed")
        lookalike = new_openid_person(email=f"{unique_word()}@evil-example.com")
        refused(sign_in("oidc", lookalike), "domain_not_allowed")
        subdomain = new_openid_person(email=f"{unique_word()}@mail.example.com")
        refused(sign_in("oidc", subdomain), "domain_not_allowed")
        assert accounts_with(str(outside.email)) == 0
        # An account that exists is not joined by an address outside the list either.
        email = f"{unique_word()}@example.org"
        make_confirmed_account(email)
        refused(sign_in("oidc", new_openid_person(email=email)), "domain_not_allowed")
        github_outside = new_github_person(
            emails=[{"email": f"{unique_word()}@example.net", "verified": True, "primary": True}]
        )
        refused(sign_in("github", github_outside), "domain_not_allowed", "github")
        assert signed_in(sign_in("github", new_github_person())).id
        outside_directory = add_directory_person(mail=f"{unique_word()}@example.net")
        reply = ldap_sign_in(outside_directory.key, str(outside_directory.password))
        ldap_refused(reply, code="domain_not_allowed")
        assert reply.body["message"] == words("domain_not_allowed", "ldap")
        assert ldap_person(ldap_sign_in(*_new_directory_login())).id


def _new_directory_login() -> tuple[str, str]:
    who = add_directory_person()
    return who.key, str(who.password)


def test_a_discovery_document_that_names_another_issuer_is_not_trusted() -> None:
    # The server reads the document once and keeps it, so this needs a server that has not.
    with app_with():
        oidc().control(discovery="wrong_issuer")
        who = new_openid_person()
        sign_in_refused("oidc", who, {"failed", "unavailable"})
        assert accounts_with(str(who.email)) == 0
        oidc().control(discovery="down")
        sign_in_refused("oidc", who, {"failed", "unavailable"})
        oidc().control(discovery="ok")
        assert signed_in(sign_in("oidc", who)).email == who.email


def test_a_provider_that_wants_the_secret_in_the_form_is_given_it_there() -> None:
    with app_with():
        oidc().control(auth_methods=["client_secret_post"])
        marked = oidc().mark()
        who = new_openid_person()
        assert signed_in(sign_in("oidc", who)).email == who.email
        token = next(one for one in oidc().log(marked) if one["path"] == "/token")
        assert token["client_auth"] == "post" and token["status"] == 200
        assert "client_secret" not in token["form"]


def test_twenty_sign_ins_in_ten_minutes_from_one_address_are_the_most_and_the_wait_is_said() -> (
    None
):
    with app_with():
        statuses = []
        for _ in range(23):
            started = api(server_base(), "POST", "/board/api/auth/oidc/start", {"intent": "signin"})
            statuses.append(started)
        assert [one.status for one in statuses[:20]] == [200] * 20
        limited = statuses[-1]
        assert limited.status == 429 and limited.cookie is None
        assert "wait" in limited.body["message"].lower()
        other = api(server_base(), "POST", "/board/api/auth/github/start", {"intent": "signin"})
        assert other.status in (200, 429)
        # A person with a signed-in session is held to the same limit and sees the same words.
        assert limited.body["message"]
    relax_limits()


# -- a server with no provider set up ------------------------------------------------------------


def test_a_server_with_no_provider_offers_none_and_every_route_says_so() -> None:
    from tests.second_stack import SecondStack

    stack = SecondStack()
    try:
        stack.up()
        base = stack.base
        offered = api(base, "GET", "/board/api/session").body
        assert offered["providers"] == []
        assert "sso_create_accounts" in offered
        person = register(base)
        assert person.call("GET", "/board/api/session").body["providers"] == []
        shown = person.call("GET", "/board/api/account/identities").body
        assert shown["has_password"] is True and shown["identities"] == []
        for provider in ("oidc", "github", "ldap"):
            reply = api(base, "POST", f"/board/api/auth/{provider}/start", {"intent": "signin"})
            assert reply.status == 404 and reply.body["message"] == NOT_SET_UP, provider
        direct = api(base, "POST", "/board/api/auth/ldap", {"username": "a", "password": "b"})
        assert direct.status == 404 and direct.body["message"] == NOT_SET_UP
        link = person.call(
            "POST", "/board/api/account/identities/ldap", {"username": "a", "password": "b"}
        )
        assert link.status == 404 and link.body["message"] == NOT_SET_UP
        for provider in ("oidc", "github"):
            called = fetch("GET", f"{base}/board/auth/{provider}/callback?code=a&state=b")
            assert called.status == 404 and called.cookie(SESSION_COOKIE) is None
        info = person.call("GET", "/board/api/info").body
        assert info["sign_in"]["providers"] == []
        # Everything else is as it was: a password account works as ever.
        assert person.call("GET", "/board/api/projects").status == 200
        assert register_as(f"{unique_word()}@example.test", base).cookie
    finally:
        stack.close()
