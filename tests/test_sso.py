"""Signing in with another provider, with no containers: how a sign-in begins and ends, what is
refused and why, where people are sent, the limits, and that nothing a provider or a person sent is
logged. The accounts are a stand-in that records what it was asked, and the providers are stand-ins
for the web (see sso_fakes.py), so every attack on the redirect, the state, the nonce and the token
is tried against the real code that handles them."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import pytest

from tests import sso_fakes as fakes
from tests.sso_fakes import FakeWeb, claims, mint, reply
from vrum.accounts import (
    AccessRefusal,
    AccountInfo,
    Accounts,
    ProviderPerson,
    SessionInfo,
    SignedIn,
    SsoRefusal,
)
from vrum.config import Settings
from vrum.ldap_login import LdapDirectory, LdapResult
from vrum.sso import (
    COOKIE_PREFIX,
    Sso,
    Transactions,
    challenge_for,
    landing,
    safe_return_path,
)
from vrum.sso_http import ProviderProblem

BASE = {"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "a-database-password"}
OIDC = {
    "VRUM_OIDC_ISSUER": fakes.ISSUER,
    "VRUM_OIDC_CLIENT_ID": fakes.CLIENT_ID,
    "VRUM_OIDC_CLIENT_SECRET": fakes.CLIENT_SECRET,
    "VRUM_OIDC_NAME": "Example login",
}
GITHUB = {"VRUM_GITHUB_CLIENT_ID": "gh-id", "VRUM_GITHUB_CLIENT_SECRET": "gh-secret-value"}
LDAP = {"VRUM_LDAP_URL": "ldaps://ldap.example.com", "VRUM_LDAP_BASE_DN": "dc=example,dc=com"}
PUBLIC = "https://vrum.example.com"


def a_person(provider: str = "oidc", **changes: Any) -> ProviderPerson:
    values: dict[str, Any] = {
        "provider": provider,
        "subject": "s-1",
        "email": "ada@example.com",
        "email_confirmed": True,
        "name": "Ada",
        "shown": "ada",
    }
    values.update(changes)
    return ProviderPerson(**values)


class FakeAccounts:
    """Records what it was asked and says what it was told to."""

    def __init__(self) -> None:
        self.signins: list[dict[str, Any]] = []
        self.links: list[dict[str, Any]] = []
        self.refuse: str | None = None

    async def sign_in_with_provider(
        self,
        person: ProviderPerson,
        *,
        provider_name: str,
        create_accounts: bool,
        allowed_domains: Any,
    ) -> SignedIn:
        self.signins.append(
            {
                "person": person,
                "name": provider_name,
                "create": create_accounts,
                "domains": tuple(allowed_domains),
            }
        )
        if self.refuse:
            raise SsoRefusal(self.refuse, provider_name)
        return signed_in()

    async def link_provider(
        self, account_id: int, session_id: int, person: ProviderPerson, *, provider_name: str
    ) -> None:
        self.links.append({"account": account_id, "session": session_id, "person": person})
        if self.refuse:
            raise SsoRefusal(self.refuse, provider_name)


def signed_in() -> SignedIn:
    account = AccountInfo(
        id=7, email="ada@example.com", name="Ada", email_verified=True, has_password=False
    )
    when = datetime(2030, 1, 1, tzinfo=UTC)
    return SignedIn(
        session=SessionInfo(id=70, account=account, csrf="csrf", expires_at=when),
        cookie="vrs_cookie",
    )


def a_session() -> SessionInfo:
    return signed_in().session


class FakeDirectory:
    def __init__(self, result: LdapResult | Exception) -> None:
        self.result = result
        self.asked: list[tuple[str, str]] = []

    async def authenticate(self, username: str, password: str) -> LdapResult:
        self.asked.append((username, password))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class World:
    def __init__(self, *providers: dict[str, str], web: FakeWeb | None = None) -> None:
        env: dict[str, str] = dict(BASE)
        for one in providers:
            env.update(one)
        self.settings = Settings.from_env(env)
        self.accounts = FakeAccounts()
        self.web = web or FakeWeb()
        self.now = [0.0]
        self.last_nonce = ""
        self.directory = FakeDirectory(LdapResult(None, "unknown_user"))
        self.sso = Sso(
            self.settings.sso,
            public_url=PUBLIC,
            accounts=cast(Accounts, self.accounts),
            fetch=self.web,
            directory=cast(LdapDirectory, self.directory),
            clock=lambda: self.now[0],
        )

    def start(
        self,
        provider: str = "oidc",
        intent: str = "signin",
        return_to: Any = None,
        session: SessionInfo | None = None,
        client: str = "10.0.0.1",
    ) -> Any:
        return asyncio.run(self.sso.start(provider, intent, return_to, session, client))

    def finish(
        self,
        params: dict[str, str],
        cookie: str | None,
        provider: str = "oidc",
        client: str = "10.0.0.1",
    ) -> Any:
        return asyncio.run(self.sso.finish(provider, params, cookie, client))


def query_of(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def an_openid_provider(world: World, **claim_changes: Any) -> None:
    """The provider's token endpoint hands out a token for Ada that answers whichever nonce the
    sign-in was started with, once the sign-in has begun."""
    world.web.provider()
    world.web.on(
        f"{fakes.ISSUER}/token",
        lambda call: reply({"id_token": mint(claims(nonce=world.last_nonce, **claim_changes))}),
    )


def begun(world: World, **kwargs: Any) -> tuple[Any, dict[str, str]]:
    started = world.start(**kwargs)
    sent = query_of(started.url)
    world.last_nonce = sent.get("nonce", "")
    return started, sent


# -- where people are sent ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "good",
    [
        "/board",
        "/board/",
        "/board/p/3",
        "/board/p/3#/users",
        "/board/p/3?sel=story:12#/lists",
        "/board?x=1",
        "/board/invite/vri_abcdef",
    ],
)
def test_a_return_path_on_this_board_is_kept(good: str) -> None:
    assert safe_return_path(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        "//evil.example.com",
        "///evil.example.com",
        "/\\evil.example.com",
        "https://evil.example.com",
        "http://evil.example.com/board",
        "javascript:alert(1)",
        "/board//evil.example.com",
        "/board/\\evil.example.com",
        "/board/%2f%2fevil.example.com",
        "/board/%5cevil.example.com",
        "/board/%252f%252fevil.example.com",
        "/board?next=https://evil.example.com",
        "/boardx",
        "/board.evil.example.com",
        "/other",
        "/",
        "",
        "board",
        "/board/p/3\nSet-Cookie: x=y",
        "/board/\tp",
        "/board/" + "a" * 600,
        None,
        7,
        ["/board"],
    ],
)
def test_a_return_path_that_could_leave_the_board_is_replaced_by_the_board(bad: Any) -> None:
    assert safe_return_path(bad) == "/board"


def test_where_a_sign_in_ends_adds_what_happened_before_the_fragment() -> None:
    assert landing("/board/p/3#/users", sso="linked") == "/board/p/3?sso=linked#/users"
    assert (
        landing("/board", sso_error="failed", sso_provider="oidc")
        == "/board?sso_error=failed&sso_provider=oidc"
    )
    assert landing("/board?a=1&sso=old&sso_error=x") == "/board?a=1"
    assert landing("/board/p/3?a=1#/x", sso="linked") == "/board/p/3?a=1&sso=linked#/x"


def test_the_pkce_challenge_is_the_sha256_of_the_verifier_as_the_standard_says() -> None:
    # The example in RFC 7636, appendix B.
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert challenge_for(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


# -- sign-ins in progress --------------------------------------------------------------------------


def test_a_sign_in_in_progress_can_be_taken_once() -> None:
    held = Transactions(clock=lambda: 0.0)
    one = held.begin(
        provider="oidc", intent="signin", return_to="/board", state="s", nonce="n", verifier="v"
    )
    assert one.id.startswith(COOKIE_PREFIX) and len(one.id) > 40
    assert held.take(one.id) == one
    assert held.take(one.id) is None
    assert held.take(None) is None and held.take("") is None and held.take("vrt_unknown") is None


def test_a_sign_in_in_progress_ends_after_ten_minutes_and_is_taken_even_then() -> None:
    now = [0.0]
    held = Transactions(clock=lambda: now[0])
    one = held.begin(
        provider="oidc", intent="signin", return_to="/board", state="s", nonce="n", verifier="v"
    )
    now[0] = 601.0
    assert held.take(one.id) is None


def test_only_so_many_sign_ins_are_remembered_at_once() -> None:
    held = Transactions(clock=lambda: 0.0, most=3)
    ids = [
        held.begin(
            provider="oidc",
            intent="signin",
            return_to="/board",
            state=str(n),
            nonce="n",
            verifier="v",
        ).id
        for n in range(5)
    ]
    assert [held.take(one) is not None for one in ids] == [False, False, True, True, True]


def test_every_sign_in_has_a_state_a_nonce_a_verifier_and_a_name_of_its_own() -> None:
    world = World(OIDC)
    world.web.provider()
    seen = set()
    for _ in range(5):
        started = world.start()
        sent = query_of(started.url)
        seen.add((started.cookie, sent["state"], sent["nonce"]))
    assert len(seen) == 5
    assert all(len(state) >= 43 and len(nonce) >= 43 for _, state, nonce in seen)


# -- OpenID Connect ---------------------------------------------------------------------


def test_starting_asks_the_provider_for_a_code_with_pkce_and_hands_back_the_cookie_value() -> None:
    world = World(OIDC)
    world.web.provider()
    started = world.start()
    sent = query_of(started.url)
    assert started.url.startswith(f"{fakes.ISSUER}/authorize?")
    assert sent["redirect_uri"] == f"{PUBLIC}/board/auth/oidc/callback"
    assert sent["code_challenge_method"] == "S256" and sent["response_type"] == "code"
    assert started.cookie.startswith("vrt_")


def test_a_good_sign_in_signs_the_person_in_and_ends_on_the_board() -> None:
    world = World(
        OIDC, {"VRUM_OIDC_ALLOWED_DOMAINS": "example.com", "VRUM_SSO_CREATE_ACCOUNTS": "false"}
    )
    an_openid_provider(world)
    started, sent = begun(world, return_to="/board/p/3#/lists")
    landed = world.finish({"code": "c", "state": sent["state"]}, started.cookie)
    assert landed.location == "/board/p/3#/lists" and landed.signed is not None
    (asked,) = world.accounts.signins
    assert asked["person"].subject == "ada-subject-1" and asked["person"].email_confirmed is True
    assert (asked["name"], asked["create"], asked["domains"]) == (
        "Example login",
        False,
        ("example.com",),
    )
    (token_call,) = world.web.asked(f"{fakes.ISSUER}/token")
    assert token_call.form is not None
    assert challenge_for(token_call.form["code_verifier"]) == sent["code_challenge"]


def test_a_return_path_that_leaves_the_board_is_replaced() -> None:
    world = World(OIDC)
    an_openid_provider(world)
    started, sent = begun(world, return_to="//evil.example.com")
    landed = world.finish({"code": "c", "state": sent["state"]}, started.cookie)
    assert landed.location == "/board"


def test_coming_back_twice_works_only_once() -> None:
    world = World(OIDC)
    an_openid_provider(world)
    started, sent = begun(world)
    first = world.finish({"code": "c", "state": sent["state"]}, started.cookie)
    again = world.finish({"code": "c", "state": sent["state"]}, started.cookie)
    assert first.signed is not None
    assert again.signed is None
    assert again.location == "/board?sso_error=expired&sso_provider=oidc"
    assert len(world.accounts.signins) == 1


@pytest.mark.parametrize("cookie", [None, "", "vrt_" + "x" * 43, "another browser's cookie"])
def test_coming_back_without_the_cookie_that_left_is_refused(cookie: str | None) -> None:
    world = World(OIDC)
    an_openid_provider(world)
    _, sent = begun(world)
    landed = world.finish({"code": "c", "state": sent["state"]}, cookie)
    assert landed.location == "/board?sso_error=expired&sso_provider=oidc"
    assert landed.signed is None and world.accounts.signins == []


def test_a_state_that_does_not_match_is_refused_and_still_uses_up_the_sign_in() -> None:
    world = World(OIDC)
    an_openid_provider(world)
    started, sent = begun(world)
    for state in ("", "wrong", sent["state"][:-1], sent["state"] + "x"):
        refused = world.finish({"code": "c", "state": state}, started.cookie)
        assert refused.location.startswith("/board?sso_error=")
    assert world.accounts.signins == []
    # The first try took the sign-in, so the right state now finds nothing.
    right = world.finish({"code": "c", "state": sent["state"]}, started.cookie)
    assert right.location == "/board?sso_error=expired&sso_provider=oidc"


def test_the_first_wrong_state_is_a_failure_not_a_timeout() -> None:
    world = World(OIDC)
    an_openid_provider(world)
    started, _ = begun(world)
    landed = world.finish({"code": "c", "state": "wrong"}, started.cookie)
    assert landed.location == "/board?sso_error=failed&sso_provider=oidc"


def test_a_sign_in_left_for_ten_minutes_has_expired() -> None:
    world = World(OIDC)
    an_openid_provider(world)
    started, sent = begun(world)
    world.now[0] = 601.0
    landed = world.finish({"code": "c", "state": sent["state"]}, started.cookie)
    assert landed.location == "/board?sso_error=expired&sso_provider=oidc"


def test_the_provider_saying_no_is_a_cancel_or_a_failure_and_its_words_are_never_passed_on() -> (
    None
):
    for error, code in (
        ("access_denied", "cancelled"),
        ("server_error", "failed"),
        ("x", "failed"),
    ):
        world = World(OIDC)
        an_openid_provider(world)
        started, sent = begun(world)
        landed = world.finish(
            {
                "error": error,
                "error_description": "<script>alert(1)</script>",
                "state": sent["state"],
            },
            started.cookie,
        )
        assert landed.location == f"/board?sso_error={code}&sso_provider=oidc"
        assert "script" not in landed.location and landed.signed is None


def test_an_answer_that_names_another_issuer_is_refused() -> None:
    world = World(OIDC)
    an_openid_provider(world)
    started, sent = begun(world)
    landed = world.finish(
        {"code": "c", "state": sent["state"], "iss": "https://evil.example.com"}, started.cookie
    )
    assert landed.location == "/board?sso_error=failed&sso_provider=oidc"
    assert world.web.asked(f"{fakes.ISSUER}/token") == []


@pytest.mark.parametrize(
    "lie",
    [
        {"nonce": "not the one"},
        {"aud": "another-client"},
        {"iss": "https://evil.example.com"},
        {"exp": 1},
        {"sub": None},
    ],
)
def test_a_token_that_lies_ends_in_a_failure_and_signs_nobody_in(lie: dict[str, Any]) -> None:
    world = World(OIDC)
    world.web.provider()
    started, sent = begun(world)
    world.web.on(
        f"{fakes.ISSUER}/token",
        reply({"id_token": mint(claims(**{"nonce": sent["nonce"], **lie}))}),
    )
    landed = world.finish({"code": "c", "state": sent["state"]}, started.cookie)
    assert landed.location == "/board?sso_error=failed&sso_provider=oidc"
    assert landed.signed is None and world.accounts.signins == []


@pytest.mark.parametrize(
    "token",
    [
        fakes.unsigned(),
        fakes.hmac_signed_with_the_public_key(),
        mint(key=fakes.OTHER_RSA_KEY),
        "not-a-token",
    ],
)
def test_a_forged_token_ends_in_a_failure_and_signs_nobody_in(token: str) -> None:
    world = World(OIDC)
    world.web.provider()
    started, sent = begun(world)
    world.web.on(f"{fakes.ISSUER}/token", reply({"id_token": token}))
    landed = world.finish({"code": "c", "state": sent["state"]}, started.cookie)
    assert landed.location == "/board?sso_error=failed&sso_provider=oidc"
    assert landed.signed is None and world.accounts.signins == []


def test_a_provider_that_cannot_be_reached_is_said_to_be_unavailable() -> None:
    world = World(OIDC)
    world.web.provider()
    started, sent = begun(world)
    world.web.on(f"{fakes.ISSUER}/token", fakes.unreachable())
    landed = world.finish({"code": "c", "state": sent["state"]}, started.cookie)
    assert landed.location == "/board?sso_error=unavailable&sso_provider=oidc"
    world = World(OIDC)
    world.web.on(f"{fakes.ISSUER}/.well-known/openid-configuration", fakes.unreachable())
    with pytest.raises(SsoRefusal) as problem:
        world.start()
    assert problem.value.code == "unavailable" and problem.value.status == 503
    assert world.sso._transactions._held == {}  # nothing is left waiting for a start that failed


def test_what_the_accounts_say_is_where_the_person_ends_up() -> None:
    for code in (
        "no_verified_email",
        "domain_not_allowed",
        "account_exists_unconfirmed",
        "signups_off",
    ):
        world = World(OIDC)
        an_openid_provider(world)
        world.accounts.refuse = code
        started, sent = begun(world)
        landed = world.finish({"code": "c", "state": sent["state"]}, started.cookie)
        assert landed.location == f"/board?sso_error={code}&sso_provider=oidc"
        assert landed.signed is None


# -- connecting an account --------------------------------------------------------------


def test_connecting_needs_somebody_signed_in_and_remembers_who() -> None:
    world = World(OIDC)
    an_openid_provider(world)
    with pytest.raises(AccessRefusal) as nobody:
        world.start(intent="link")
    assert nobody.value.status == 401
    started, sent = begun(world, intent="link", session=a_session(), return_to="/board/p/3#/users")
    landed = world.finish({"code": "c", "state": sent["state"]}, started.cookie)
    assert landed.location == "/board/p/3?sso=linked#/users"
    assert landed.signed is None and world.accounts.signins == []
    ((link),) = world.accounts.links
    assert (link["account"], link["session"], link["person"].subject) == (7, 70, "ada-subject-1")


def test_a_problem_connecting_is_named_in_the_address_and_nothing_is_signed_in() -> None:
    for code in ("identity_taken", "already_connected", "link_session_lost"):
        world = World(OIDC)
        an_openid_provider(world)
        world.accounts.refuse = code
        started, sent = begun(world, intent="link", session=a_session())
        landed = world.finish({"code": "c", "state": sent["state"]}, started.cookie)
        assert landed.location == f"/board?sso_error={code}&sso_provider=oidc"


def test_a_start_has_to_say_what_it_is_for() -> None:
    world = World(OIDC)
    world.web.provider()
    for intent in ("", "admin", "SIGNIN", "link "):
        with pytest.raises(AccessRefusal) as problem:
            world.start(intent=intent, session=a_session())
        assert problem.value.status == 400


def test_a_provider_that_is_not_set_up_cannot_be_started_or_come_back() -> None:
    world = World(OIDC)
    for provider in ("github", "ldap", "google", "", "../oidc"):
        with pytest.raises(SsoRefusal) as problem:
            world.start(provider=provider)
        assert (problem.value.code, problem.value.status) == ("not_set_up", 404)
    with pytest.raises(SsoRefusal):
        world.finish({"code": "c", "state": "s"}, "vrt_x", provider="github")


# -- the limits -------------------------------------------------------------------------


def test_one_network_address_may_start_twenty_sign_ins_in_ten_minutes() -> None:
    world = World(OIDC)
    world.web.provider()
    for _ in range(20):
        world.start()
    with pytest.raises(AccessRefusal) as problem:
        world.start()
    assert problem.value.status == 429 and "Wait about" in str(problem.value)
    world.start(client="10.0.0.2")
    world.now[0] = 601.0
    world.start()


def test_a_flood_of_callbacks_that_do_not_check_out_is_stopped() -> None:
    world = World(OIDC)
    an_openid_provider(world)
    for _ in range(20):
        world.finish({"code": "c", "state": "wrong"}, "vrt_x")
    started, sent = begun(world)
    landed = world.finish({"code": "c", "state": sent["state"]}, started.cookie)
    assert landed.location == "/board?sso_error=failed&sso_provider=oidc"
    assert landed.signed is None
    assert world.finish({"code": "c", "state": "x"}, "vrt_y", client="10.0.0.9").signed is None


def test_cancelling_at_the_provider_does_not_count_against_anyone() -> None:
    world = World(OIDC)
    an_openid_provider(world)
    for _ in range(30):
        started, sent = begun(world, client="10.0.0.3")
        world.finish(
            {"error": "access_denied", "state": sent["state"]}, started.cookie, client="10.0.0.3"
        )
        world.now[0] += 100
    started, sent = begun(world, client="10.0.0.3")
    assert world.finish(
        {"code": "c", "state": sent["state"]}, started.cookie, client="10.0.0.3"
    ).signed


# -- GitHub -----------------------------------------------------------------------------


def a_github(world: World, emails: Any, *, user_id: Any = 583231, token: Any = None) -> None:
    web = world.web
    web.on(
        "https://github.com/login/oauth/access_token",
        reply(token or {"access_token": "gho_" + "a" * 36, "token_type": "bearer"}),
    )
    web.on(
        "https://api.github.com/user",
        reply({"id": user_id, "login": "octocat", "name": "The Octocat"}),
    )
    web.on("https://api.github.com/user/emails", reply(emails))


EMAILS = [
    {"email": "other@example.com", "primary": False, "verified": True},
    {"email": "Octo@Example.com", "primary": True, "verified": True},
]


def github_sign_in(world: World, intent: str = "signin", session: SessionInfo | None = None) -> Any:
    started = world.start("github", intent, "/board", session)
    sent = query_of(started.url)
    return sent, world.finish({"code": "c", "state": sent["state"]}, started.cookie, "github")


def test_github_sends_the_person_to_github_with_pkce_and_the_scopes_it_needs() -> None:
    world = World(GITHUB)
    started = world.start("github")
    sent = query_of(started.url)
    assert started.url.startswith("https://github.com/login/oauth/authorize?")
    assert sent["scope"] == "read:user user:email" and sent["code_challenge_method"] == "S256"
    assert sent["client_id"] == "gh-id"
    assert sent["redirect_uri"] == f"{PUBLIC}/board/auth/github/callback"
    assert "client_secret" not in sent


def test_a_github_sign_in_uses_the_numeric_id_and_the_primary_verified_address() -> None:
    world = World(GITHUB)
    a_github(world, EMAILS)
    sent, landed = github_sign_in(world)
    assert landed.signed is not None and landed.location == "/board"
    (asked,) = world.accounts.signins
    person = asked["person"]
    assert (person.provider, person.subject) == ("github", "583231")
    assert (person.email, person.email_confirmed) == ("Octo@Example.com", True)
    assert (person.name, person.shown) == ("The Octocat", "octocat")
    (token_call,) = world.web.asked("https://github.com/login/oauth/access_token")
    assert token_call.form is not None
    assert challenge_for(token_call.form["code_verifier"]) == sent["code_challenge"]
    assert token_call.headers["Accept"] == "application/json"
    user_call = world.web.asked("https://api.github.com/user")[0]
    assert user_call.headers["Authorization"].startswith("Bearer gho_")
    assert user_call.headers["User-Agent"] == "Vrum"


@pytest.mark.parametrize(
    "emails",
    [
        [{"email": "x@example.com", "primary": True, "verified": False}],
        [{"email": "x@example.com", "primary": False, "verified": True}],
        [{"email": "x@example.com", "primary": "true", "verified": "true"}],
        [],
        {"message": "Not Found"},
        "nonsense",
    ],
)
def test_an_address_github_has_not_verified_as_the_primary_one_is_never_used(emails: Any) -> None:
    world = World(GITHUB)
    a_github(world, emails)
    _, landed = github_sign_in(world)
    (asked,) = world.accounts.signins
    assert asked["person"].email is None and asked["person"].email_confirmed is False
    assert landed.signed is not None  # whether that is enough is the accounts' decision


def test_a_github_token_answer_with_an_error_is_a_failure() -> None:
    world = World(GITHUB)
    a_github(
        world, EMAILS, token={"error": "bad_verification_code", "error_description": "<b>x</b>"}
    )
    _, landed = github_sign_in(world)
    assert landed.location == "/board?sso_error=failed&sso_provider=github"
    assert world.accounts.signins == [] and world.web.asked("https://api.github.com/user") == []


@pytest.mark.parametrize("bad_id", [None, "583231", 0, -1, True, 1.5])
def test_a_github_user_without_a_numeric_id_cannot_sign_in(bad_id: Any) -> None:
    world = World(GITHUB)
    a_github(world, EMAILS, user_id=bad_id)
    _, landed = github_sign_in(world)
    assert landed.location == "/board?sso_error=failed&sso_provider=github"


def test_connecting_github_does_not_need_an_address() -> None:
    world = World(GITHUB)
    a_github(world, EMAILS)
    world.web.on("https://api.github.com/user/emails", fakes.unreachable())
    _, landed = github_sign_in(world, "link", a_session())
    assert landed.location == "/board?sso=linked"
    assert world.accounts.links[0]["person"].email is None


def test_signing_in_with_github_does_need_it_to_be_asked() -> None:
    world = World(GITHUB)
    a_github(world, EMAILS)
    world.web.on("https://api.github.com/user/emails", fakes.unreachable())
    _, landed = github_sign_in(world)
    assert landed.location == "/board?sso_error=unavailable&sso_provider=github"


# -- LDAP -------------------------------------------------------------------------------


def an_ldap(world: World, **result: Any) -> None:
    world.directory.result = LdapResult(a_person("ldap", subject="uuid-1", **result), "ok")


def test_a_directory_sign_in_checks_the_password_with_the_directory_and_signs_in() -> None:
    world = World(LDAP, {"VRUM_LDAP_NAME": "Company login"})
    an_ldap(world)
    signed = asyncio.run(world.sso.ldap_sign_in("ada", "a-password", "10.0.0.1"))
    assert signed.cookie == "vrs_cookie"
    assert world.directory.asked == [("ada", "a-password")]
    assert world.accounts.signins[0]["name"] == "Company login"
    assert world.accounts.signins[0]["person"].provider == "ldap"


def test_a_wrong_name_and_a_wrong_password_get_the_same_words_and_the_same_401() -> None:
    world = World(LDAP)
    said = []
    for reason in ("unknown_user", "wrong_password", "ambiguous_user"):
        world.directory.result = LdapResult(None, reason)
        with pytest.raises(SsoRefusal) as problem:
            asyncio.run(world.sso.ldap_sign_in("ada", "x", "10.0.0.1"))
        said.append((str(problem.value), problem.value.status, problem.value.code))
    assert said == [("The username or password is not right.", 401, "bad_credentials")] * 3


def test_an_empty_password_never_reaches_the_directory() -> None:
    world = World(LDAP)
    an_ldap(world)
    for username, password in (
        ("ada", ""),
        ("", "x"),
        ("   ", "x"),
        ("a" * 300, "x"),
        ("ada", "p" * 201),
    ):
        with pytest.raises(SsoRefusal) as problem:
            asyncio.run(world.sso.ldap_sign_in(username, password, "10.0.0.1"))
        assert problem.value.code == "bad_credentials"
    assert world.directory.asked == [] and world.accounts.signins == []


def test_five_wrong_passwords_for_one_name_stop_the_sixth_even_if_it_is_right() -> None:
    world = World(LDAP)
    for _ in range(5):
        with pytest.raises(SsoRefusal):
            asyncio.run(world.sso.ldap_sign_in("Ada", "wrong", "10.0.0.1"))
    an_ldap(world)
    with pytest.raises(AccessRefusal) as problem:
        asyncio.run(world.sso.ldap_sign_in("ada", "right", "10.0.0.1"))
    assert problem.value.status == 429
    assert str(problem.value).startswith(
        "There have been too many failed attempts, so nothing was tried."
    )
    assert len(world.directory.asked) == 5
    # Another name from the same address, and the same name from another address, are not stopped.
    assert asyncio.run(world.sso.ldap_sign_in("bob", "right", "10.0.0.1"))
    assert asyncio.run(world.sso.ldap_sign_in("ada", "right", "10.0.0.2"))


def test_two_hundred_failures_from_one_network_address_stop_that_address() -> None:
    world = World(LDAP)
    for number in range(200):
        with pytest.raises(SsoRefusal):
            asyncio.run(world.sso.ldap_sign_in(f"user{number}", "wrong", "10.0.0.1"))
    an_ldap(world)
    with pytest.raises(AccessRefusal) as problem:
        asyncio.run(world.sso.ldap_sign_in("ada", "right", "10.0.0.1"))
    assert problem.value.status == 429


def test_a_good_sign_in_forgives_the_failures_before_it() -> None:
    world = World(LDAP)
    for _ in range(4):
        with pytest.raises(SsoRefusal):
            asyncio.run(world.sso.ldap_sign_in("ada", "wrong", "10.0.0.1"))
    an_ldap(world)
    asyncio.run(world.sso.ldap_sign_in("ada", "right", "10.0.0.1"))
    world.directory.result = LdapResult(None, "wrong_password")
    for _ in range(4):
        with pytest.raises(SsoRefusal):
            asyncio.run(world.sso.ldap_sign_in("ada", "wrong", "10.0.0.1"))


def test_a_directory_that_cannot_be_reached_is_unavailable_and_is_not_the_persons_fault() -> None:
    world = World(LDAP)
    world.directory.result = ProviderProblem("unavailable", "unreachable")
    for _ in range(10):
        with pytest.raises(SsoRefusal) as problem:
            asyncio.run(world.sso.ldap_sign_in("ada", "x", "10.0.0.1"))
        assert (problem.value.code, problem.value.status) == ("unavailable", 503)
    world.directory.result = ProviderProblem("failed", "service_account_refused")
    with pytest.raises(SsoRefusal) as setup:
        asyncio.run(world.sso.ldap_sign_in("ada", "x", "10.0.0.1"))
    assert setup.value.code == "failed"


def test_connecting_a_directory_account_answers_a_wrong_password_as_a_sign_in_does() -> None:
    world = World(LDAP)
    with pytest.raises(SsoRefusal) as problem:
        asyncio.run(world.sso.ldap_link(a_session(), "ada", "wrong", "10.0.0.1"))
    assert (problem.value.code, problem.value.status) == ("bad_credentials", 401)
    an_ldap(world)
    asyncio.run(world.sso.ldap_link(a_session(), "ada", "right", "10.0.0.1"))
    assert world.accounts.links[0]["account"] == 7 and world.accounts.links[0]["session"] == 70


# -- what the pages are told ------------------------------------------------------------


def test_with_no_provider_nothing_is_offered() -> None:
    world = World()
    assert world.sso.for_the_pages() == [] and world.sso.configured() == ()
    assert world.sso.about() == {
        "providers": [],
        "create_accounts": True,
        "allowed_domains": {"oidc": [], "github": [], "ldap": []},
    }
    for provider in ("oidc", "github", "ldap"):
        assert world.sso.name_of(provider) == ""


def test_the_pages_are_told_the_providers_and_the_status_page_the_hosts_and_never_a_secret() -> (
    None
):
    world = World(
        OIDC,
        GITHUB,
        LDAP,
        {"VRUM_SSO_CREATE_ACCOUNTS": "false", "VRUM_GITHUB_ALLOWED_DOMAINS": "a.co"},
    )
    assert world.sso.for_the_pages() == [
        {"id": "oidc", "name": "Example login", "kind": "oidc"},
        {"id": "github", "name": "GitHub", "kind": "github"},
        {"id": "ldap", "name": "LDAP", "kind": "ldap"},
    ]
    about = world.sso.about()
    assert about["create_accounts"] is False
    assert [(one["id"], one["host"]) for one in about["providers"]] == [
        ("oidc", "login.example.com"),
        ("github", "github.com"),
        ("ldap", "ldap.example.com"),
    ]
    assert about["allowed_domains"] == {"oidc": [], "github": ["a.co"], "ldap": []}
    shown = repr(about) + repr(world.sso.for_the_pages())
    for secret in (fakes.CLIENT_SECRET, "gh-secret-value"):
        assert secret not in shown


# -- the log ----------------------------------------------------------------------------


def test_nothing_a_provider_or_a_person_sent_is_ever_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    world = World(OIDC, GITHUB, LDAP)
    an_openid_provider(world)
    started, sent = begun(world)
    world.finish({"code": "SECRET-CODE", "state": sent["state"]}, started.cookie)
    world.finish({"code": "SECRET-CODE", "state": "STATE-GUESS"}, "vrt_cookie-value-guess")
    started, sent = begun(world)
    world.finish(
        {"error": "access_denied", "error_description": "DESCRIPTION", "state": sent["state"]},
        started.cookie,
    )
    a_github(world, EMAILS)
    github_sign_in(world)
    for _ in range(2):
        with pytest.raises(SsoRefusal):
            asyncio.run(
                world.sso.ldap_sign_in("ada-the-user", "THE-DIRECTORY-PASSWORD", "10.0.0.1")
            )
    logged = " ".join(
        f"{record.getMessage()} {getattr(record, 'fields', '')}" for record in caplog.records
    )
    assert "sso_login" in logged
    forbidden = [
        "SECRET-CODE",
        "STATE-GUESS",
        "cookie-value-guess",
        "DESCRIPTION",
        "THE-DIRECTORY-PASSWORD",
        "ada-the-user",
        "ada@example.com",
        "Octo@Example.com",
        "ada-subject-1",
        "583231",
        "gho_",
        sent["state"],
        sent["nonce"],
        started.cookie,
        fakes.CLIENT_SECRET,
        "gh-secret-value",
        "eyJ",
    ]
    assert [one for one in forbidden if one in logged] == []
