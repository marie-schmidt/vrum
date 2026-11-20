"""What the tests of signing in with another provider (tests/test_sso_integration.py) share: the
scripted OpenID provider and the scripted GitHub of the check stack (tests/scripted_oidc.py,
tests/scripted_github.py) and the real directory (OpenLDAP) as the tests meet them, a browser's
cookies for a sign-in that goes out to a provider and comes back, the plain words the server and
the pages use for every way a sign-in can end (written here from the design, not read from the
server), and how to start the server again with another setting.

Nothing here makes an account except through the board's own API, and a person of a provider comes
back from the provider the way a real one does: through the browser's redirects."""

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from tests.helpers import REPO_ROOT, run_sql, unique_word
from tests.world import Person, Reply, api, register_as, server_base

SESSION_COOKIE = "vrum_session"
SSO_COOKIE = "vrum_sso"

NAMES = {"oidc": "Scripted OpenID", "github": "GitHub", "ldap": "Company directory"}

# The words of every way a sign-in with a provider can end, as the design gives them. <name> is
# the provider's button name.
WORDS = {
    "cancelled": "Signing in with <name> was cancelled. Nothing was changed.",
    "failed": (
        "Signing in with <name> did not work. Nothing was changed. Try again, and if it keeps "
        "happening ask whoever runs this server."
    ),
    "expired": "That sign-in took too long or was already used. Start again.",
    "unavailable": "Vrum could not reach <name>. Try again in a moment.",
    "no_verified_email": (
        "<name> did not give a confirmed email address, so Vrum cannot tell who you are. Confirm "
        "your email address there, or sign in another way."
    ),
    "domain_not_allowed": (
        "Accounts with that email address may not sign in this way on this server."
    ),
    "account_exists_unconfirmed": (
        "An account with your email address already exists here, but its address was never "
        "confirmed, so Vrum will not join the two. Sign in with its password, then connect <name> "
        "from the Users page."
    ),
    "signups_off": (
        "This server does not make new accounts from <name> sign-in. Sign in with an account you "
        "already have, or ask whoever runs it."
    ),
    "identity_taken": "That <name> account is already connected to another Vrum account.",
    "already_connected": "<name> is already connected to this account. Disconnect it first.",
    "link_session_lost": "You were signed out while connecting <name>. Sign in and try again.",
    "last_way_in": (
        "That is the only way you can sign in. Set a password or connect another provider first. "
        "Nothing was changed."
    ),
}
WRONG_LOGIN = "The username or password is not right."
NOT_SET_UP = "Signing in with that provider is not set up on this server."


def words(code: str, provider: str) -> str:
    return WORDS[code].replace("<name>", NAMES[provider])


# -- where everything is ---------------------------------------------------------------------------


def _address(url_variable: str, port_variable: str, host: str = "127.0.0.1") -> str:
    url = os.environ.get(url_variable)
    if url:
        return url.rstrip("/")
    port = os.environ.get(port_variable)
    if not port:
        pytest.fail(
            f"{url_variable} is not set. Run the integration tests through ./check.sh, or export "
            f"{url_variable} (or {port_variable}) for the stack you started."
        )
    return f"http://{host}:{port}"


def oidc_url() -> str:
    return _address("VRUM_TEST_OIDC_URL", "VRUM_OIDC_PORT")


def github_url() -> str:
    return _address("VRUM_TEST_GITHUB_URL", "VRUM_GITHUB_PORT")


def dex_url() -> str:
    return _address("VRUM_TEST_DEX_URL", "VRUM_DEX_PORT", "dex.localhost")


def dex_app_url() -> str:
    return _address("VRUM_TEST_DEX_APP_URL", "VRUM_DEX_APP_PORT", "localhost")


def mail_url() -> str:
    return _address("VRUM_TEST_MAIL_URL", "VRUM_MAIL_PORT")


def board_base() -> str:
    """The server as a browser must call it for any sign-in that goes out to a provider and comes
    back: by the name the providers are told to send people back to (localhost), which is also
    the host of the cookies. The tests' own requests use the address of VRUM_TEST_URL."""
    return server_base().replace("127.0.0.1", "localhost")


# -- plain HTTP, with no redirect followed -----------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **more: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


@dataclass
class Raw:
    status: int
    headers: Any
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    @property
    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def location(self) -> str:
        return str(self.headers.get("Location") or "")

    def cookie(self, name: str) -> str | None:
        for line in self.headers.get_all("Set-Cookie") or []:
            first = line.partition(";")[0]
            key, _, value = first.partition("=")
            if key == name:
                return str(value)
        return None

    def set_cookie_line(self, name: str) -> str | None:
        for line in self.headers.get_all("Set-Cookie") or []:
            if line.partition("=")[0] == name:
                return str(line)
        return None


def fetch(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: Any = None,
    timeout: float = 30,
) -> Raw:
    """One request, with no redirect followed and no error raised for a status."""
    sent = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        sent.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, method=method, headers=sent)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return Raw(response.status, response.headers, response.read())
    except urllib.error.HTTPError as problem:
        return Raw(problem.code, problem.headers, problem.read())


# -- the stand-ins ---------------------------------------------------------------------------------


@dataclass
class Who:
    """A person of a provider, as the provider knows them."""

    key: str
    email: str | None
    name: str | None = None
    sub: str = ""
    password: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class StandIn:
    """The scripted OpenID provider or the scripted GitHub, through their control endpoints."""

    def __init__(self, base: str) -> None:
        self.base = base

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        reply = fetch(method, self.base + path, body=body, timeout=20)
        assert reply.status in (200, 201), f"{path}: {reply.status} {reply.text[:300]}"
        return reply.json

    def control(self, **body: Any) -> dict[str, Any]:
        return dict(self._call("POST", "/_control", body))

    def reset(self) -> None:
        self.control(reset=True)

    def log(self, since: int = 0) -> list[dict[str, Any]]:
        return list(self._call("GET", f"/_log?since={since}"))

    def mark(self) -> int:
        """The id of the last log entry, to read what happens after it."""
        entries = self.log()
        return int(entries[-1]["id"]) if entries else 0

    def add_person(self, **fields: Any) -> dict[str, Any]:
        return dict(self._call("POST", "/_people", fields))


def oidc() -> StandIn:
    return StandIn(oidc_url())


def github() -> StandIn:
    return StandIn(github_url())


def new_openid_person(**claims: Any) -> Who:
    """A new person at the scripted OpenID provider, with an address nobody else has. Claims given
    replace the usual ones; a claim given as None is left out."""
    word = unique_word()
    wanted: dict[str, Any] = {
        "sub": f"oidc-{word}",
        "email": f"{word}@example.com",
        "email_verified": True,
        "name": f"Openid {word}",
        **claims,
    }
    given = {name: value for name, value in wanted.items() if value is not None}
    oidc().add_person(key=word, **given)
    return Who(word, given.get("email"), given.get("name"), str(given["sub"]), extra=given)


_next_github_id = int(time.time()) % 1_000_000 * 100


def new_github_person(**fields: Any) -> Who:
    """A new person at the scripted GitHub whose primary address is confirmed, unless `emails`
    says otherwise."""
    global _next_github_id
    _next_github_id += 1
    word = unique_word()
    address = f"{word}@example.com"
    wanted: dict[str, Any] = {
        "id": 10_000_000 + _next_github_id,
        "login": f"login-{word}",
        "name": f"Github {word}",
        "email": None,
        "emails": [{"email": address, "verified": True, "primary": True, "visibility": None}],
        **fields,
    }
    github().add_person(key=word, **wanted)
    return Who(word, address, wanted["name"], str(wanted["id"]), extra=wanted)


# -- the real directory ----------------------------------------------------------------------------

LDAP_BASE = "ou=people,dc=example,dc=org"
_LDAP_ADMIN = [
    "-x",
    "-H",
    "ldap://127.0.0.1:389",
    "-D",
    "cn=admin,dc=example,dc=org",
    "-w",
    "directory-admin-password",
]


def _directory(
    tool: str, *arguments: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    found = subprocess.run(
        ["docker", "compose", "exec", "-T", "ldap", tool, *_LDAP_ADMIN, *arguments],
        cwd=REPO_ROOT,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return found


def add_directory_person(
    *, mail: str | None | bool = True, cn: str | None = None, password: str | None = None
) -> Who:
    """A new person in the directory (added as the directory's administrator, since the product
    only reads it): `mail` is True for an address of their own, a text for that address, None
    for none."""
    word = unique_word()
    shown = cn or f"Directory {word}"
    secret = password or f"open-sesame-{word}"
    address = f"{word}@example.com" if mail is True else (mail or None)
    lines = [
        f"dn: cn={shown},{LDAP_BASE}",
        "objectClass: inetOrgPerson",
        f"uid: {word}",
        f"cn: {shown}",
        f"sn: {word}",
        *([f"mail: {address}"] if address else []),
        f"userPassword: {secret}",
    ]
    done = _directory("ldapadd", stdin="\n".join(lines) + "\n")
    assert done.returncode == 0, done.stdout + done.stderr
    return Who(word, address, shown, password=secret, extra={"cn": shown})


def change_directory_person(who: Who, **attributes: str) -> None:
    """Replace attributes of a person the directory holds (their user name, address, name)."""
    changes = "".join(
        f"replace: {name}\n{name}: {value}\n-\n" for name, value in attributes.items()
    )
    ldif = f"dn: cn={who.extra['cn']},{LDAP_BASE}\nchangetype: modify\n{changes}"
    done = _directory("ldapmodify", stdin=ldif)
    assert done.returncode == 0, done.stdout + done.stderr
    if "uid" in attributes:
        who.key = attributes["uid"]
    if "mail" in attributes:
        who.email = attributes["mail"]


# -- the server ------------------------------------------------------------------------------------


def _compose(*arguments: str, **environment: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *arguments],
        cwd=REPO_ROOT,
        env={**os.environ, **environment},
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


_ambient: dict[str, str] = {}


def restart_app(**settings: str) -> None:
    """Start the server again (the same image and database) with these settings in its
    environment, which forgets everything it kept in memory: the sign-ins in progress and every
    limit. A setting not given goes back to what the check stack sets."""
    done = _compose(
        "up",
        "--detach",
        "--no-deps",
        "--force-recreate",
        "--wait",
        "--wait-timeout",
        "120",
        "app",
        **settings,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    _ambient.clear()
    _ambient.update(settings)
    _pressure.update(starts=0, bad=0)


@contextmanager
def app_with(**settings: str) -> Iterator[None]:
    """The server run with another setting for the length of the block; it is started again
    with none of them afterwards. (A restart the tests make inside the block, to forget the
    limits, keeps the settings.)"""
    restart_app(**settings)
    try:
        yield
    finally:
        restart_app()


def app_log() -> str:
    """Everything the server has written to its log since it was last started."""
    done = _compose("logs", "--no-color", "app")
    return done.stdout + done.stderr


def database_dump() -> str:
    """The whole database as text, for looking for what must never be kept."""
    done = _compose("exec", "-T", "db", "pg_dump", "-U", "vrum", "-d", "vrum", "--data-only")
    assert done.returncode == 0, done.stderr[:500]
    return done.stdout


# -- the database, as its owner sees it ------------------------------------------------------------


def _sql_value(value: str) -> str:
    return value.replace("'", "''")


def account_of(email: str) -> dict[str, Any] | None:
    """What the database holds for the account with this address: whether it is confirmed, has no
    password, and which provider made it."""
    done = run_sql(
        "SELECT id, email_verified_at IS NOT NULL, password_hash IS NULL, "
        f"coalesce(signed_up_with, ''), display_name FROM accounts "
        f"WHERE email = '{_sql_value(email)}'",
        plain=True,
        project=None,
    )
    assert done.returncode == 0, done.stderr
    line = done.stdout.strip()
    if not line:
        return None
    number, confirmed, no_password, made_by, name = line.split("|", 4)
    return {
        "id": int(number),
        "confirmed": confirmed == "t",
        "no_password": no_password == "t",
        "signed_up_with": made_by,
        "name": name,
    }


def accounts_with(email: str) -> int:
    done = run_sql(
        f"SELECT count(*) FROM accounts WHERE lower(email) = lower('{_sql_value(email)}')",
        plain=True,
        project=None,
    )
    return int(done.stdout.strip())


def identity_rows(account_id: int) -> list[tuple[str, str]]:
    """(provider, subject) of every identity of the account, from the database."""
    done = run_sql(
        "SELECT provider, subject FROM account_identities "
        f"WHERE account_id = {account_id} ORDER BY provider",
        plain=True,
        project=None,
    )
    return [(one.split("|")[0], one.split("|", 1)[1]) for one in done.stdout.splitlines() if one]


def make_unconfirmed_account(email: str) -> Person:
    """An account with this address made by registering with a password, which leaves its address
    unconfirmed. (The server mails a link to confirm it; nobody follows it here.)"""
    return register_as(email)


def confirm_address(person: Person) -> None:
    """Confirm the address of an account the way a person does: by the link the server mailed to
    it, which the scripted mail server kept."""
    deadline = time.time() + 20
    link: re.Match[str] | None = None
    while time.time() < deadline and link is None:
        sent = fetch(
            "GET", f"{mail_url()}/messages?to={urllib.parse.quote(person.email)}", timeout=10
        ).json
        for message in sent:
            link = re.search(r"/board/verify/(vrv_[A-Za-z0-9_-]+)", message["text"])
            if link:
                break
        if link is None:
            time.sleep(0.5)
    assert link, f"no confirmation email arrived for {person.email}"
    done = api(person.base, "POST", f"/board/api/verify/{link.group(1)}")
    assert done.status == 200, done.body


def make_confirmed_account(email: str) -> Person:
    person = make_unconfirmed_account(email)
    confirm_address(person)
    return person


# -- one sign-in that goes out to a provider and comes back ---------------------------------------


@dataclass
class Landing:
    """Where a browser ends up when a sign-in comes back to the server."""

    status: int
    location: str
    session: str | None
    sso_cookie_line: str | None = None
    session_line: str | None = None

    @property
    def path(self) -> str:
        return urllib.parse.urlsplit(self.location).path

    @property
    def query(self) -> dict[str, str]:
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.location).query))

    @property
    def error(self) -> str | None:
        return self.query.get("sso_error")

    @property
    def signed_in(self) -> bool:
        return self.session is not None


# The server remembers how many sign-ins this machine has started and how many callbacks did not
# check out, for ten minutes, and refuses more than twenty of each. Before each test the tests start
# the server again if the count says the limit could be near (no test starts more than ten), so
# that they never meet it by accident.
_pressure = {"starts": 0, "bad": 0}
_MOST_BEFORE_RESTART = 8


def note_starts(count: int = 1) -> None:
    """Tell the count of one that a test started itself, with a plain request, or a browser did."""
    _pressure["starts"] += count


def relax_limits() -> None:
    """Start the server again, with the settings it has now, when the count says the limits could
    be near."""
    if max(_pressure.values()) >= _MOST_BEFORE_RESTART:
        restart_app(**_ambient)


def fresh_server() -> None:
    """Start the server again with the settings it has now: a new log and no limit used."""
    restart_app(**_ambient)


@dataclass
class Journey:
    """A sign-in as a browser makes it: the request that starts it (which answers with the address
    of the provider and sets the `vrum_sso` cookie), the visit to the provider, and the return."""

    provider: str
    start: Raw
    base: str

    @property
    def url(self) -> str:
        return str(self.start.json["url"])

    @property
    def sso_cookie(self) -> str | None:
        return self.start.cookie(SSO_COOKIE)


def start_journey(
    provider: str,
    *,
    intent: str = "signin",
    return_to: str | None = None,
    person: Person | None = None,
    base: str | None = None,
) -> Journey:
    """Start a sign-in (or, with a signed-in `person`, connecting a provider) as the board's page
    does. (The server is not started again from here: a test that holds two sign-ins at once would
    lose the first. The tests do that before each test, see `relax_limits`.)"""
    _pressure["starts"] += 1
    root = base or server_base()
    body: dict[str, Any] = {"intent": intent}
    if return_to is not None:
        body["return_to"] = return_to
    headers = {"Content-Type": "application/json", "Origin": root}
    if person is not None:
        headers["Cookie"] = f"{SESSION_COOKIE}={person.cookie}"
        headers["X-Vrum-CSRF"] = person.csrf
    started = fetch("POST", f"{root}/board/api/auth/{provider}/start", headers=headers, body=body)
    return Journey(provider, started, root)


def visit_provider(journey: Journey, who: Who) -> str:
    """The person presses their button at the provider, which sends the browser back: returns the
    address it is sent back to."""
    joined = "&" if "?" in journey.url else "?"
    arrived = fetch("GET", f"{journey.url}{joined}person={urllib.parse.quote(who.key)}")
    assert arrived.status == 303, (
        f"the provider did not send the browser back: {arrived.text[:300]}"
    )
    return arrived.location


def come_back(
    journey: Journey,
    location: str,
    *,
    sso_cookie: str | None | bool = True,
) -> Landing:
    """The browser arrives at the callback address the provider sent it to, carrying the cookie the
    start gave it (`sso_cookie` True), none (None) or another one (a text). The return is a
    navigation from another site, so the browser's session cookie (which is Strict) does not go
    with it, whoever is signed in."""
    parts = urllib.parse.urlsplit(location)
    callback = f"{journey.base}{parts.path}" + (f"?{parts.query}" if parts.query else "")
    value = journey.sso_cookie if sso_cookie is True else sso_cookie
    headers = {"Cookie": f"{SSO_COOKIE}={value}"} if value else {}
    answered = fetch("GET", callback, headers=headers)
    landing = Landing(
        answered.status,
        answered.location,
        answered.cookie(SESSION_COOKIE),
        answered.set_cookie_line(SSO_COOKIE),
        answered.set_cookie_line(SESSION_COOKIE),
    )
    if landing.status != 303 or landing.error:
        _pressure["bad"] += 1
    return landing


def sign_in(
    provider: str,
    who: Who,
    *,
    intent: str = "signin",
    return_to: str | None = None,
    person: Person | None = None,
) -> Landing:
    """The whole of it: start, visit the provider as `who`, come back."""
    journey = start_journey(provider, intent=intent, return_to=return_to, person=person)
    assert journey.start.status == 200, (
        f"start refused: {journey.start.status} {journey.start.text}"
    )
    return come_back(journey, visit_provider(journey, who))


def session_of(landing: Landing) -> Person:
    """The person a sign-in made or found, as the cookie of the landing says (the board's own
    GET /session tells who they are)."""
    assert landing.session, f"not signed in: {landing.location}"
    reply = api(server_base(), "GET", "/board/api/session", cookie=landing.session)
    assert reply.status == 200 and reply.body["account"], reply.body
    account = reply.body["account"]
    return Person(
        server_base(),
        account["email"],
        "",
        account["name"],
        landing.session,
        reply.body["csrf"],
        account["id"],
    )


def whoami(landing: Landing) -> Reply:
    assert landing.session
    return api(server_base(), "GET", "/board/api/session", cookie=landing.session)
