"""How the board knows who is asking: the session cookie, and the checks that stop another website
from making a person's browser change something.

The cookie holds a random value that only the server can match (it keeps a hash of it). It cannot
be read by a script on the page (HttpOnly), and is not sent with a request that another site
starts (SameSite=Strict). A request that changes something has to carry two more things: the
session's own secret, which only the board's page was told, in a header that another site cannot
set on a request to this one, and an Origin that is this site's own."""

import hmac

from starlette.requests import Request
from starlette.responses import Response

from vrum.accounts import (
    NOT_SIGNED_IN,
    SESSION_HOURS,
    AccessRefusal,
    Accounts,
    SessionInfo,
    SignedIn,
)
from vrum.sso import COOKIE_NAME as SSO_COOKIE

SESSION_COOKIE = "vrum_session"
CSRF_HEADER = "x-vrum-csrf"
# The cookie goes only to the board's own addresses.
COOKIE_PATH = "/board"

CROSS_SITE = (
    "This request did not come from the board's own page, so nothing was done. Open the board "
    "at this server's own address and try again."
)
NO_CSRF = (
    "This change did not carry the board's session secret, so nothing was done. Reload the "
    "board's page and try again."
)


def client_of(request: Request) -> str:
    """Who is asking, as far as the network says: for counting failed attempts."""
    return request.client.host if request.client else "unknown"


def cross_site_problem(request: Request) -> str | None:
    """Why this request looks as if another website started it, or None when it does not."""
    origin = request.headers.get("origin")
    if origin is not None and origin != str(request.base_url).rstrip("/"):
        return CROSS_SITE
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        return CROSS_SITE
    return None


def refuse_unless_same_site_json(request: Request) -> None:
    """For a request that signs in or registers, which has no session yet to carry a secret: it
    must come from the board's own page, and say it is JSON, which another site's form cannot."""
    if cross_site_problem(request) is not None:
        raise AccessRefusal(CROSS_SITE, 403)
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise AccessRefusal("Send the request as JSON (Content-Type: application/json).", 415)


async def signed_in(accounts: Accounts, request: Request, *, changing: bool) -> SessionInfo:
    """The session this request belongs to, or a refusal. A request that changes something must
    also carry the session's secret and come from the board's own page."""
    session = await accounts.session(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise AccessRefusal(NOT_SIGNED_IN, 401)
    if changing:
        if cross_site_problem(request) is not None:
            raise AccessRefusal(CROSS_SITE, 403)
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not hmac.compare_digest(sent.encode(), session.csrf.encode()):
            raise AccessRefusal(NO_CSRF, 403)
    return session


def start_cookie(response: Response, request: Request, signed: SignedIn) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        signed.cookie,
        max_age=SESSION_HOURS * 3600,
        path=COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )


def end_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path=COOKIE_PATH, httponly=True, samesite="strict")


# The cookie that ties a sign-in with another provider to the browser that started it. Unlike the
# session cookie it is SameSite=Lax, because the browser comes back from the provider by following
# a link from another site, and a Strict cookie would stay behind. It holds only the random name
# of the sign-in (which the server keeps for ten minutes and takes once), and goes only to the
# addresses the provider sends the browser back to.
SSO_COOKIE_PATH = "/board/auth"
SSO_COOKIE_SECONDS = 600


def start_sso_cookie(response: Response, name: str, *, secure: bool) -> None:
    response.set_cookie(
        SSO_COOKIE,
        name,
        max_age=SSO_COOKIE_SECONDS,
        path=SSO_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=secure,
    )


def end_sso_cookie(response: Response, *, secure: bool) -> None:
    response.delete_cookie(
        SSO_COOKIE, path=SSO_COOKIE_PATH, httponly=True, samesite="lax", secure=secure
    )
