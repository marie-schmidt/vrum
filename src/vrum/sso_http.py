"""The one way Vrum calls a sign-in provider over the web (OpenID Connect and GitHub).

A call has a time limit, never follows a redirect (a provider that redirects a token request is
wrong, or is being impersonated), reads at most 1 MiB, and refuses an address that is not https
unless the person who runs Vrum allowed http for a provider on this machine. Nothing here logs a
URL, a header or a body: those hold codes and tokens."""

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NamedTuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

TIMEOUT_SECONDS = 10.0
# How long a whole call may take, even if the provider keeps sending a little at a time.
DEADLINE_SECONDS = 15.0
MOST_BYTES = 1024 * 1024


class ProviderUnreachable(Exception):
    """The provider could not be reached, did not answer in time, or answered with more than it
    may. It carries no detail: what a provider sent is never kept."""


class ProviderProblem(Exception):
    """Signing in with a provider did not work. `code` is which problem it is (a key of
    accounts.SSO_WORDS: 'failed' or 'unavailable') and `reason` is a short fixed word for the log,
    for whoever runs the server. It never holds anything a provider or a person sent."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(code, reason)
        self.code = code
        self.reason = reason


class Reply(NamedTuple):
    status: int
    body: bytes

    def json(self) -> Any:
        """The body as JSON, or raises ProviderUnreachable when it is not (a provider that does
        not speak JSON is as good as one that cannot be reached)."""
        try:
            return json.loads(self.body)
        except ValueError:
            raise ProviderUnreachable from None


class Call(NamedTuple):
    """One request to a provider: `form` is sent as an urlencoded body, which makes it a POST."""

    url: str
    headers: Mapping[str, str] = {}
    form: Mapping[str, str] | None = None


# What `Sso` and the provider clients call, so that a test can stand in for the web.
Fetch = Callable[[Call], Awaitable[Reply]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _fetch_now(call: Call) -> Reply:
    data = urlencode(call.form).encode() if call.form is not None else None
    headers = dict(call.headers)
    if data is not None:
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    request = urllib.request.Request(call.url, data=data, headers=headers)
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MOST_BYTES + 1)
            status = response.status
    except urllib.error.HTTPError as problem:
        # A refusal or a redirect is an answer (a token request that is refused says why).
        try:
            body, status = problem.read(MOST_BYTES + 1), problem.code
        except OSError:
            raise ProviderUnreachable from None
    except (urllib.error.URLError, OSError, ValueError):
        raise ProviderUnreachable from None
    if len(body) > MOST_BYTES:
        raise ProviderUnreachable
    return Reply(status, body)


async def fetch(call: Call) -> Reply:
    """Make the call. Raises ProviderUnreachable for anything but an answer."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(_fetch_now, call), DEADLINE_SECONDS)
    except TimeoutError:
        raise ProviderUnreachable from None


def with_query(url: str, params: Mapping[str, str]) -> str:
    """An address with more query parameters added to the ones it has."""
    parts = urlsplit(url)
    query = [*parse_qsl(parts.query, keep_blank_values=True), *params.items()]
    return urlunsplit(parts._replace(query=urlencode(query)))


def allowed_address(url: Any, *, allow_insecure_http: bool) -> bool:
    """Whether an address that came from a provider's own description (its token endpoint, its
    keys) may be used: https, with a host and no user name or password, or http when it is
    allowed."""
    if not isinstance(url, str) or not url.isprintable() or " " in url:
        return False
    parsed = urlparse(url)
    if not parsed.hostname or parsed.username is not None or parsed.fragment:
        return False
    return parsed.scheme == "https" or (parsed.scheme == "http" and allow_insecure_http)
