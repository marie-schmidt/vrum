"""Shared HTTP response, request-parsing, and error-handling helpers for the board's routes."""

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from vrum.accounts import AccessRefusal, Accounts, ProjectInfo, SessionInfo, SsoRefusal
from vrum.board.session import refuse_unless_same_site_json, signed_in
from vrum.guard import GuardedServer

# What a page may load and do: its own files and its own server, and nothing from anywhere else.
# No inline script or style, no frames, no forms that leave the page.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

API = "/board/api"
RouteHandler = Callable[[Request], Awaitable[Response]]


def json_response(body: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(
        body, status_code=status, headers={**SECURITY_HEADERS, "Cache-Control": "no-store"}
    )


def plain_tool_error(tool: str, error: ToolError) -> str:
    """A refusal as the board shows it: what an agent is told, without the 'Error executing tool'
    the protocol puts in front."""
    return str(error).removeprefix(f"Error executing tool {tool}: ")


def refused_response(error: AccessRefusal) -> JSONResponse:
    body = {"message": str(error)}
    if isinstance(error, SsoRefusal):
        body["code"] = error.code
    return json_response(body, error.status)


def number_param(raw: Any) -> int:
    """A number in an address. Anything that is not a number is, like a number that names nothing
    of the person's, no such thing."""
    return int(raw) if str(raw).isdecimal() and len(str(raw)) <= 18 else 0


def project_number(request: Request) -> int:
    """The project number in the address."""
    return number_param(request.path_params["project_id"])


async def json_object(request: Request) -> dict[str, Any]:
    refuse_unless_same_site_json(request)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise AccessRefusal("The request has to be a JSON object.")
    return body


async def maybe_json_dict(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def fields_of(request: Request, *names: str) -> dict[str, str]:
    """The named text fields of a JSON body. A missing or mistyped one is an empty text, which
    the account checks then refuse in their own words."""
    body = await json_object(request)
    return {name: body[name] if isinstance(body.get(name), str) else "" for name in names}


async def in_project(
    accounts: Accounts, request: Request, *, changing: bool
) -> tuple[SessionInfo, ProjectInfo]:
    """The session and the project in the address once the person is shown to be in it, checked on
    every request so a person who was just removed is refused at once."""
    session = await signed_in(accounts, request, changing=changing)
    return session, await accounts.project_of(session.account.id, project_number(request))


def guarded(handler: RouteHandler) -> RouteHandler:
    """Translate AccessRefusal into a JSON error response once for every API route."""

    async def wrapped(request: Request) -> Response:
        try:
            return await handler(request)
        except AccessRefusal as error:
            return refused_response(error)

    return wrapped


def api_route(mcp: GuardedServer, path: str, handler: RouteHandler, method: str) -> None:
    mcp.custom_route(path, methods=[method])(guarded(handler))
