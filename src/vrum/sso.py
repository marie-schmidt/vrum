"""Signing in with another provider: OpenID Connect, GitHub and LDAP. Optional, and off unless a
provider is set up (see SsoSettings in config.py).

This module runs the sign-in itself: it starts one, remembers what it needs to finish it, checks
what comes back, asks the provider's client (oidc.py, github_login.py, ldap_login.py) who the
person is, and hands that to `Accounts.sign_in_with_provider` (or `link_provider`), which alone
decides what it means for an account.

What a sign-in in progress is (its state, nonce, PKCE verifier, whether it signs in or connects an
account, who is connecting it, and where to return) is kept in memory for ten minutes and works
once, like the failed-attempt counters in ratelimit.py: a restart ends the sign-ins that were in
progress, and nothing is lost but a click. A browser holds only the transaction's random name, in
the `vrum_sso` cookie, so that the one that comes back is the one that left. The cookie is
SameSite=Lax because coming back from the provider is a navigation from another site, which a
Strict cookie would not go with; it is HttpOnly, only goes to /board/auth, and expires in ten
minutes. The callback takes the transaction before it looks at anything else, so a code or a
state can be tried against it once.

Nothing a provider or a person sent is logged or shown: a log line holds the provider, what was
being done and how it ended, as one of a few fixed words."""

import base64
import hashlib
import hmac
import logging
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from vrum.accounts import (
    NOT_SIGNED_IN,
    AccessRefusal,
    Accounts,
    ProviderPerson,
    SessionInfo,
    SignedIn,
    SsoRefusal,
    waiting,
)
from vrum.config import ProviderInfo, SsoSettings
from vrum.github_login import GithubClient
from vrum.ldap_login import LdapDirectory, refused_input
from vrum.logs import event
from vrum.oidc import OidcClient
from vrum.ratelimit import AttemptLimiter
from vrum.sso_http import Fetch, ProviderProblem
from vrum.sso_http import fetch as real_fetch

log = logging.getLogger("vrum.sso")

COOKIE_NAME = "vrum_sso"
COOKIE_PREFIX = "vrt_"
TRANSACTION_SECONDS = 600
MOST_TRANSACTIONS = 10_000
# Sign-ins one network address may start in ten minutes, and callbacks it may send that do not
# check out (a wrong or used state, a bad code or token).
STARTS_PER_CLIENT = 20
BAD_CALLBACKS_PER_CLIENT = 20
# Failed directory sign-ins in ten minutes for one username from one network address, and for one
# network address: the same limits as a password.
LDAP_FAILURES_PER_LOGIN = 5
LDAP_FAILURES_PER_CLIENT = 200
WINDOW_SECONDS = 600
REDIRECT_PROVIDERS = ("oidc", "github")
INTENTS = ("signin", "link")
DEFAULT_RETURN = "/board"
_MOST_RETURN_CHARS = 512
_MOST_CODE_CHARS = 2048


def safe_return_path(raw: Any) -> str:
    """Where to send the person when a sign-in ends: only a place on this board. It has to start
    with /board, and nothing in it (as written or once decoded) may be a second slash, a
    backslash or a control character, which is how a link is made to leave the site. Anything
    else is replaced by /board."""
    if not isinstance(raw, str):
        return DEFAULT_RETURN
    path = raw.strip()
    rest = path[len(DEFAULT_RETURN) :]
    if (
        not path.startswith(DEFAULT_RETURN)
        or len(path) > _MOST_RETURN_CHARS
        or (rest and rest[0] not in "/?#")
    ):
        return DEFAULT_RETURN
    for text in (path, unquote(path), unquote(unquote(path))):
        if (
            "//" in text
            or "\\" in text
            or ":" in text.partition("?")[0]
            or any(ch.isspace() or not ch.isprintable() for ch in text)
        ):
            return DEFAULT_RETURN
    return path


def landing(return_to: str, **params: str) -> str:
    """Where a sign-in ends: the return path, with what happened added to its query."""
    parts = urlsplit(return_to)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k[:3] != "sso"]
    query = urlencode([*kept, *params.items()])
    return urlunsplit(("", "", parts.path, query, parts.fragment))


def challenge_for(verifier: str) -> str:
    """The PKCE S256 challenge for a verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _random() -> str:
    """256 bits, as 43 URL-safe characters."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Transaction:
    """A sign-in in progress."""

    id: str
    provider: str
    # 'signin' to sign someone in (making their account if need be), or 'link' to connect the
    # provider to the account of a person who is already signed in (`account_id` and
    # `session_id`, which has to still be in force when they come back).
    intent: str
    state: str
    nonce: str
    verifier: str
    return_to: str
    account_id: int | None
    session_id: int | None
    expires_at: float


class Transactions:
    """Sign-ins in progress. Each is kept for ten minutes and can be taken once."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        seconds: float = TRANSACTION_SECONDS,
        most: int = MOST_TRANSACTIONS,
    ) -> None:
        self._clock = clock
        self._seconds = seconds
        self._most = most
        self._held: dict[str, Transaction] = {}

    def begin(
        self,
        *,
        provider: str,
        intent: str,
        return_to: str,
        account_id: int | None = None,
        session_id: int | None = None,
        state: str,
        nonce: str,
        verifier: str,
    ) -> Transaction:
        now = self._clock()
        self._held = {key: one for key, one in self._held.items() if one.expires_at > now}
        while len(self._held) >= self._most:
            del self._held[next(iter(self._held))]
        one = Transaction(
            id=COOKIE_PREFIX + _random(),
            provider=provider,
            intent=intent,
            state=state,
            nonce=nonce,
            verifier=verifier,
            return_to=return_to,
            account_id=account_id,
            session_id=session_id,
            expires_at=now + self._seconds,
        )
        self._held[one.id] = one
        return one

    def take(self, transaction_id: str | None) -> Transaction | None:
        """The transaction this name stands for, removed: asking again finds nothing. None when
        there is none, it was already taken, or its ten minutes are over."""
        if not transaction_id:
            return None
        one = self._held.pop(transaction_id, None)
        if one is None or one.expires_at <= self._clock():
            return None
        return one


@dataclass(frozen=True)
class Started:
    """A sign-in that has begun: where to send the browser, and the name to put in its cookie."""

    url: str
    cookie: str


@dataclass(frozen=True)
class Landed:
    """How a sign-in ends: where to send the browser and, when someone was signed in, how."""

    location: str
    signed: SignedIn | None


class Sso:
    def __init__(
        self,
        settings: SsoSettings,
        *,
        public_url: str,
        accounts: Accounts,
        fetch: Fetch = real_fetch,
        directory: LdapDirectory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._accounts = accounts
        self._transactions = Transactions(clock)
        redirect = f"{public_url}/board/auth/%s/callback"
        self._oidc = (
            OidcClient(
                settings.oidc,
                redirect_uri=redirect % "oidc",
                allow_insecure_http=settings.allow_insecure_http,
                fetch=fetch,
                clock=clock,
            )
            if settings.oidc
            else None
        )
        self._github = (
            GithubClient(settings.github, redirect_uri=redirect % "github", fetch=fetch)
            if settings.github
            else None
        )
        self._directory = None
        if settings.ldap is not None:
            self._directory = directory or LdapDirectory(settings.ldap)
        self._starts = AttemptLimiter(STARTS_PER_CLIENT, WINDOW_SECONDS, clock)
        self._bad_callbacks = AttemptLimiter(BAD_CALLBACKS_PER_CLIENT, WINDOW_SECONDS, clock)
        self._ldap_by_login = AttemptLimiter(LDAP_FAILURES_PER_LOGIN, WINDOW_SECONDS, clock)
        self._ldap_by_client = AttemptLimiter(LDAP_FAILURES_PER_CLIENT, WINDOW_SECONDS, clock)

    # -- what the pages are told ----------------------------------------------------------------

    @property
    def providers(self) -> tuple[ProviderInfo, ...]:
        return self._settings.providers()

    @property
    def create_accounts(self) -> bool:
        return self._settings.create_accounts

    def configured(self) -> tuple[str, ...]:
        return tuple(one.id for one in self.providers)

    def name_of(self, provider: str) -> str:
        for one in self.providers:
            if one.id == provider:
                return one.name
        return ""

    def for_the_pages(self) -> list[dict[str, str]]:
        """The ways of signing in that are set up, as the sign-in page needs them."""
        return [{"id": one.id, "name": one.name, "kind": one.id} for one in self.providers]

    def about(self) -> dict[str, Any]:
        """What the Status page says about signing in: never a secret, a user or a password."""
        return {
            "providers": [
                {"id": one.id, "name": one.name, "kind": one.id, "host": one.host}
                for one in self.providers
            ],
            "create_accounts": self._settings.create_accounts,
            "allowed_domains": {
                provider: list(self._settings.allowed_domains(provider))
                for provider in ("oidc", "github", "ldap")
            },
        }

    def _the_provider(self, provider: str) -> str:
        """The name of a provider, or a refusal that it is not set up."""
        name = self.name_of(provider)
        if not name:
            raise SsoRefusal("not_set_up")
        return name

    # -- OpenID Connect and GitHub: out to the provider and back --------------------------------

    def _logged(self, provider: str, intent: str | None, outcome: str, reason: str = "") -> None:
        fields: dict[str, Any] = {"provider": provider, "intent": intent, "outcome": outcome}
        if reason:
            fields["reason"] = reason
        event(
            log,
            logging.INFO if outcome in ("ok", "started") else logging.WARNING,
            "sso_login",
            **fields,
        )

    async def start(
        self,
        provider: str,
        intent: str,
        return_to: Any,
        session: SessionInfo | None,
        client: str,
    ) -> Started:
        """Begin signing in (or connecting) at OpenID Connect or GitHub: where to send the browser
        and what its cookie holds. `session` is the signed-in person, who connecting needs."""
        if provider not in REDIRECT_PROVIDERS:
            raise SsoRefusal("not_set_up")
        name = self._the_provider(provider)
        if intent not in INTENTS:
            raise AccessRefusal(
                "Say whether this is to sign in or to connect an account (signin or link). "
                "Nothing was done."
            )
        if intent == "link" and session is None:
            raise AccessRefusal(NOT_SIGNED_IN, 401)
        wait = self._starts.seconds_to_wait(client)
        if wait:
            raise AccessRefusal(
                "Too many sign-ins were started from this network address lately, so nothing was "
                f"done. Wait about {waiting(wait)} and try again.",
                429,
            )
        self._starts.failed(client)
        state, nonce, verifier = _random(), _random(), secrets.token_urlsafe(48)
        challenge = challenge_for(verifier)
        try:
            if provider == "oidc":
                assert self._oidc is not None
                url = await self._oidc.authorization_url(
                    state=state, nonce=nonce, challenge=challenge
                )
            else:
                assert self._github is not None
                url = self._github.authorization_url(state=state, challenge=challenge)
        except ProviderProblem as problem:
            self._logged(provider, intent, problem.code, problem.reason)
            raise SsoRefusal(problem.code, name) from None
        one = self._transactions.begin(
            provider=provider,
            intent=intent,
            return_to=safe_return_path(return_to),
            account_id=session.account.id if intent == "link" and session else None,
            session_id=session.id if intent == "link" and session else None,
            state=state,
            nonce=nonce,
            verifier=verifier,
        )
        self._logged(provider, intent, "started")
        return Started(url, one.id)

    async def finish(
        self, provider: str, params: Mapping[str, str], cookie: str | None, client: str
    ) -> Landed:
        """The browser came back from the provider. Whatever happens, the person is sent
        somewhere: signed in (or with the account connected), or back with one problem. The
        transaction is taken first, so what comes back can be tried against it once."""
        name = self._the_provider(provider)
        one = self._transactions.take(cookie)
        return_to = one.return_to if one is not None else DEFAULT_RETURN
        intent = one.intent if one is not None else None
        signed: SignedIn | None = None
        code, reason = "ok", ""
        try:
            signed = await self._finish(provider, name, one, params, client)
        except SsoRefusal as problem:
            code, reason = problem.code, "refused"
        except ProviderProblem as problem:
            code, reason = problem.code, problem.reason
        except Exception as error:
            code, reason = "failed", type(error).__name__
        self._logged(provider, intent, code, reason)
        if code == "ok":
            where = landing(return_to, sso="linked") if intent == "link" else landing(return_to)
            return Landed(where, signed)
        if code in ("failed", "expired"):
            self._bad_callbacks.failed(client)
        return Landed(landing(return_to, sso_error=code, sso_provider=provider), None)

    async def _finish(
        self,
        provider: str,
        name: str,
        one: Transaction | None,
        params: Mapping[str, str],
        client: str,
    ) -> SignedIn | None:
        if self._bad_callbacks.seconds_to_wait(client):
            raise ProviderProblem("failed", "too_many_failures")
        if one is None:
            raise ProviderProblem("expired", "no_transaction")
        sent_state = params.get("state", "")
        if one.provider != provider or not hmac.compare_digest(
            sent_state.encode(), one.state.encode()
        ):
            raise ProviderProblem("failed", "state_does_not_match")
        problem = params.get("error")
        if problem is not None:
            raise ProviderProblem(
                "cancelled" if problem == "access_denied" else "failed", "provider_error"
            )
        # A provider that names itself in the answer (RFC 9207) must name the one Vrum asked.
        if (
            provider == "oidc"
            and self._settings.oidc is not None
            and params.get("iss", self._settings.oidc.issuer) != self._settings.oidc.issuer
        ):
            raise ProviderProblem("failed", "wrong_issuer")
        code = params.get("code", "")
        if not code or len(code) > _MOST_CODE_CHARS:
            raise ProviderProblem("failed", "no_code")
        person: ProviderPerson
        if provider == "oidc":
            assert self._oidc is not None
            person = await self._oidc.person(code, one.verifier, one.nonce)
        else:
            assert self._github is not None
            person = await self._github.person(
                code, one.verifier, need_email=one.intent == "signin"
            )
        return await self._conclude(person, name, one)

    async def _conclude(
        self, person: ProviderPerson, name: str, one: Transaction | None = None
    ) -> SignedIn | None:
        """What a provider's answer means for an account: connect it to the account of the person
        who is signed in, or sign someone in."""
        if one is not None and one.intent == "link":
            assert one.account_id is not None and one.session_id is not None
            await self._accounts.link_provider(
                one.account_id, one.session_id, person, provider_name=name
            )
            return None
        return await self._accounts.sign_in_with_provider(
            person,
            provider_name=name,
            create_accounts=self._settings.create_accounts,
            allowed_domains=self._settings.allowed_domains(person.provider),
        )

    # -- LDAP: the person types the directory's username and password into Vrum -----------------

    async def _directory_person(self, username: str, password: str, client: str) -> ProviderPerson:
        """Check a username and password against the directory, or refuse in the same words
        whether the name is unknown or the password is wrong. Failures are counted."""
        assert self._directory is not None
        name = self.name_of("ldap")
        login = f"{client}\n{username.strip().lower()}"
        wait = max(
            self._ldap_by_login.seconds_to_wait(login),
            self._ldap_by_client.seconds_to_wait(client),
        )
        if wait:
            raise AccessRefusal(
                "There have been too many failed attempts, so nothing was tried. Wait "
                f"{wait} seconds and try again.",
                429,
            )
        refused = refused_input(username, password)
        reason = refused or ""
        person: ProviderPerson | None = None
        if refused is None:
            try:
                result = await self._directory.authenticate(username, password)
            except ProviderProblem as problem:
                self._logged("ldap", "signin", problem.code, problem.reason)
                raise SsoRefusal(problem.code, name) from None
            person, reason = result.person, result.reason
        if person is None:
            self._ldap_by_login.failed(login)
            self._ldap_by_client.failed(client)
            self._logged("ldap", "signin", "bad_credentials", reason)
            raise SsoRefusal("bad_credentials", name)
        self._ldap_by_login.succeeded(login)
        return person

    async def ldap_sign_in(self, username: str, password: str, client: str) -> SignedIn:
        """Sign in with a directory username and password."""
        name = self._the_provider("ldap")
        person = await self._directory_person(username, password, client)
        try:
            signed = await self._conclude(person, name)
        except SsoRefusal as problem:
            self._logged("ldap", "signin", problem.code, "refused")
            raise
        assert signed is not None
        self._logged("ldap", "signin", "ok")
        return signed

    async def ldap_link(
        self, session: SessionInfo, username: str, password: str, client: str
    ) -> None:
        """Connect a directory account to the account of the person who is signed in, after
        checking its username and password the way a sign-in does (a wrong one is the same 401,
        with the code `bad_credentials`, which the page has to tell from "you are signed out")."""
        name = self._the_provider("ldap")
        person = await self._directory_person(username, password, client)
        try:
            await self._accounts.link_provider(
                session.account.id, session.id, person, provider_name=name
            )
        except SsoRefusal as problem:
            self._logged("ldap", "link", problem.code, "refused")
            raise
        self._logged("ldap", "link", "ok")
