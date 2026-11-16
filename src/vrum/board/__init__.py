"""The board's server side: the page files, accounts and projects, and the one way the pages reach
a project's graph.

The page files (src/vrum/board/static) are plain HTML, CSS and JavaScript that hold no data, so
they are open and a browser can load them before anyone has signed in. Everything else needs a
person's session (a cookie), and everything about a project needs the signed-in person to own it,
which is checked here on every request, never in the browser. What the pages show or change goes
through `POST /board/api/projects/<id>/tool/<name>`, which calls the MCP tool of that name on the
same server for that project, so the board is subject to exactly the same checks as an agent: the
same refusals in the same words, the same versions, claims and event log, the same scan for
secrets. There is nothing else the board can do with a graph: the tools it may call are listed
here."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from vrum.access import MCP_PATH
from vrum.accounts import SESSION_HOURS, AccessRefusal, Accounts, ProjectInfo, SessionInfo
from vrum.board.session import (
    client_of,
    end_cookie,
    refuse_unless_same_site_json,
    signed_in,
    start_cookie,
)
from vrum.board.tools import AREAS, TOOL_PLACES, AgentsOnly, OnTheBoard
from vrum.config import Settings
from vrum.db import NoProject, working_for
from vrum.guard import GuardedServer
from vrum.search import Searcher

STATIC = Path(__file__).parent / "static"

# The tools the pages may call. A test checks that each is a tool the server has. Each later
# board milestone adds the tools its pages need.
READ_TOOLS = (
    "list_nodes",
    "get_node",
    "node_history",
    "list_links",
    "list_rules",
    "get_rule",
    "rule_history",
    "coverage_report",
    "list_decisions",
    "get_decision",
    "decision_history",
    "list_facts",
    "get_fact",
    "fact_history",
    "recall",
    "search",
    "similar_items",
    "find_overlaps",
    "find_mentions",
    "export_graph",
    "changes_since",
    "ready_work",
    "who_is_working",
    "server_status",
)
WRITE_TOOLS = (
    "create_node",
    "update_node",
    "update_nodes",
    "move_node",
    "add_link",
    "remove_link",
    "claim_node",
    "renew_claim",
    "release_claim",
    "create_rule",
    "update_rule",
    "place_rule",
    "move_rule",
    "record_decision",
    "supersede_decision",
    "withdraw_decision",
    "link_decision",
    "unlink_decision",
    "retain_fact",
    "supersede_fact",
    "withdraw_fact",
    "link_fact",
    "unlink_fact",
    "set_confirmation",
)
BOARD_TOOLS = frozenset(READ_TOOLS + WRITE_TOOLS)

# Where each kind of file is served from. Nothing else is, so a file dropped into the static
# folder is not served until it has a type here.
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}

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


def _page_files() -> dict[str, tuple[bytes, str]]:
    """Every page file as it is served: its address, its bytes and its type. The home of the board
    is also served at /board and /board/."""
    files: dict[str, tuple[bytes, str]] = {}
    for path in sorted(STATIC.rglob("*")):
        content_type = _CONTENT_TYPES.get(path.suffix)
        if path.is_file() and content_type is not None:
            address = "/board/" + path.relative_to(STATIC).as_posix()
            files[address] = (path.read_bytes(), content_type)
    index = files.get("/board/index.html")
    if index is not None:
        files["/board"] = files["/board/"] = index
    return files


_FILES = _page_files()


def open_paths() -> Mapping[str, str]:
    """The addresses that need no sign-in (each answers GET only): the page files, which hold no
    data. The data calls under /board/api are not among them."""
    return dict.fromkeys(_FILES, "GET")


def _json(body: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(
        body, status_code=status, headers={**SECURITY_HEADERS, "Cache-Control": "no-store"}
    )


def _plain(tool: str, error: ToolError) -> str:
    """A refusal as the board shows it: what an agent is told, without the "Error executing tool"
    the protocol puts in front."""
    prefix = f"Error executing tool {tool}: "
    message = str(error)
    return message.removeprefix(prefix)


def _refused(error: AccessRefusal) -> JSONResponse:
    return _json({"message": str(error)}, error.status)


def _session_body(session: SessionInfo) -> dict[str, Any]:
    return {
        "account": session.account.model_dump(),
        "csrf": session.csrf,
        "expires_at": session.expires_at.isoformat(),
    }


def _project_number(request: Request) -> int:
    """The project number in the address. Anything that is not a number is, like a number that
    names no project of the person's, a project that is not theirs."""
    raw = request.path_params["project_id"]
    return int(raw) if str(raw).isdecimal() and len(str(raw)) <= 18 else 0


def add_board(
    mcp: GuardedServer, *, settings: Settings, searcher: Searcher, accounts: Accounts
) -> None:
    """Add the board's routes to the server."""

    async def page(request: Request) -> Response:
        path = request.url.path
        # A project's address serves the same page as the board's own: the page asks the server
        # whether the person owns the project, and says so when not.
        if path.startswith("/board/p/") and path[len("/board/p/") :].isdecimal():
            path = "/board"
        found = _FILES.get(path)
        if found is None:
            return Response("There is no such page file.", status_code=404, media_type="text/plain")
        body, content_type = found
        return Response(
            body,
            media_type=content_type,
            headers={**SECURITY_HEADERS, "Cache-Control": "no-cache"},
        )

    # -- accounts and sessions -------------------------------------------------------------------

    async def fields_of(request: Request, *names: str) -> dict[str, str]:
        """The named text fields of a JSON body. A missing or mistyped one is an empty text, which
        the account checks then refuse in their own words."""
        refuse_unless_same_site_json(request)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise AccessRefusal("The request has to be a JSON object.")
        return {name: body[name] if isinstance(body.get(name), str) else "" for name in names}

    def with_cookie(session: Any, request: Request, signed: Any, status: int = 200) -> Response:
        response = _json(_session_body(session), status)
        start_cookie(response, request, signed)
        return response

    async def register(request: Request) -> Response:
        try:
            given = await fields_of(request, "email", "name", "password")
            signed = await accounts.register(
                given["email"], given["name"], given["password"], client_of(request)
            )
        except AccessRefusal as error:
            return _refused(error)
        return with_cookie(signed.session, request, signed, 201)

    async def sign_in(request: Request) -> Response:
        try:
            given = await fields_of(request, "email", "password")
            signed = await accounts.sign_in(given["email"], given["password"], client_of(request))
        except AccessRefusal as error:
            return _refused(error)
        return with_cookie(signed.session, request, signed)

    async def whoami(request: Request) -> Response:
        """Who this browser is signed in as. Nobody is a normal answer, not an error, so that the
        page can ask without the browser reporting a failed request."""
        try:
            session = await signed_in(accounts, request, changing=False)
        except AccessRefusal as error:
            if error.status == 401:
                return _json({"account": None, "csrf": None})
            return _refused(error)
        return _json(_session_body(session))

    async def sign_out(request: Request) -> Response:
        try:
            session = await signed_in(accounts, request, changing=True)
        except AccessRefusal as error:
            return _refused(error)
        await accounts.sign_out(session.id)
        response = _json({"signed_out": True})
        end_cookie(response)
        return response

    # -- projects --------------------------------------------------------------------------------

    async def owned(request: Request, *, changing: bool) -> tuple[SessionInfo, ProjectInfo]:
        """The session, and the project in the address once it is shown to be the session's own."""
        session = await signed_in(accounts, request, changing=changing)
        return session, await accounts.project_of(session.account.id, _project_number(request))

    async def list_projects(request: Request) -> Response:
        try:
            session = await signed_in(accounts, request, changing=False)
        except AccessRefusal as error:
            return _refused(error)
        found = await accounts.projects(session.account.id)
        return _json({"projects": [project.model_dump(mode="json") for project in found]})

    async def create_project(request: Request) -> Response:
        try:
            session = await signed_in(accounts, request, changing=True)
            try:
                body = await request.json()
            except ValueError:
                body = None
            name = body.get("name") if isinstance(body, dict) else None
            project = await accounts.create_project(
                session.account.id, name if isinstance(name, str) else ""
            )
        except AccessRefusal as error:
            return _refused(error)
        return _json(project.model_dump(mode="json"), 201)

    async def show_project(request: Request) -> Response:
        try:
            _, project = await owned(request, changing=False)
        except AccessRefusal as error:
            return _refused(error)
        return _json(project.model_dump(mode="json"))

    # -- agent keys ------------------------------------------------------------------------------

    async def list_keys(request: Request) -> Response:
        try:
            session, project = await owned(request, changing=False)
            found = await accounts.keys(session.account.id, project.id)
        except AccessRefusal as error:
            return _refused(error)
        return _json({"keys": [key.model_dump(mode="json") for key in found]})

    async def create_key(request: Request) -> Response:
        try:
            session, project = await owned(request, changing=True)
            try:
                body = await request.json()
            except ValueError:
                body = None
            label = body.get("label") if isinstance(body, dict) else None
            key, secret = await accounts.create_key(
                session.account.id, project.id, label if isinstance(label, str) else ""
            )
        except AccessRefusal as error:
            return _refused(error)
        # The only time the key itself is ever sent: only a hash of it is kept.
        return _json({"key": key.model_dump(mode="json"), "secret": secret}, 201)

    async def revoke_key(request: Request) -> Response:
        try:
            session, project = await owned(request, changing=True)
            raw = request.path_params["key_id"]
            key = await accounts.revoke_key(
                session.account.id, project.id, int(raw) if str(raw).isdecimal() else 0
            )
        except AccessRefusal as error:
            return _refused(error)
        return _json({"key": key.model_dump(mode="json")})

    # -- a project's graph -----------------------------------------------------------------------

    async def run(project: ProjectInfo, name: str, arguments: Any) -> Response:
        if not isinstance(arguments, dict):
            return _json({"message": "The request has to be a JSON object of arguments."}, 400)
        try:
            with working_for(project.id):
                result = await mcp.call_tool(name, arguments)
        except (UnexpectedToolError, NoProject):
            return _json(
                {
                    "message": "Something went wrong inside the server, and the change may not "
                    "have been made. Look at the server's log (docker compose logs app)."
                },
                500,
            )
        except ToolError as error:
            return _json({"message": _plain(name, error)}, 400)
        return _json(result.structured_content or {})

    async def arguments_of(request: Request) -> Any:
        try:
            return await request.json()
        except ValueError:
            return None

    async def call_tool(request: Request) -> Response:
        name = request.path_params["name"]
        try:
            _, project = await owned(request, changing=True)
        except AccessRefusal as error:
            return _refused(error)
        if name not in BOARD_TOOLS:
            return _json({"message": f"The board does not use a tool called {name}."}, 404)
        return await run(project, name, await arguments_of(request))

    async def try_tool(request: Request) -> Response:
        """The Tools page's "try it": any tool the server itself says only reads, and nothing else.
        The server's own list decides, so a tool that changes something can never be tried here."""
        name = request.path_params["name"]
        try:
            _, project = await owned(request, changing=True)
        except AccessRefusal as error:
            return _refused(error)
        known = {tool.name: tool for tool in await mcp.list_tools()}
        if name not in known:
            return _json({"message": f"The server has no tool called {name}."}, 404)
        annotations = known[name].annotations
        if not (annotations and annotations.read_only_hint):
            return _json(
                {
                    "message": f"{name} changes something, so the board does not let you try it "
                    "here. Use the page the tool names, or an agent."
                },
                403,
            )
        return await run(project, name, await arguments_of(request))

    async def index(request: Request) -> Response:
        try:
            _, project = await owned(request, changing=False)
        except AccessRefusal as error:
            return _refused(error)
        with working_for(project.id):
            note = await searcher.index_state()
        return _json(note.model_dump())

    async def tools(request: Request) -> Response:
        """The server's live tool list, with where on the board each is used."""
        try:
            await signed_in(accounts, request, changing=False)
        except AccessRefusal as error:
            return _refused(error)
        found = []
        for tool in await mcp.list_tools():
            place = TOOL_PLACES.get(tool.name)
            read_only = bool(tool.annotations and tool.annotations.read_only_hint)
            found.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.input_schema,
                    "changes_something": not read_only,
                    "area": place.area if place else None,
                    "page": place.place.page
                    if place and isinstance(place.place, OnTheBoard)
                    else None,
                    "where": place.place.where
                    if place and isinstance(place.place, OnTheBoard)
                    else None,
                    "agents_only": place.place.why
                    if place and isinstance(place.place, AgentsOnly)
                    else None,
                    "board_calls": tool.name in BOARD_TOOLS,
                }
            )
        return _json({"mcp_path": MCP_PATH, "areas": list(AREAS), "tools": found})

    async def info(request: Request) -> Response:
        try:
            await signed_in(accounts, request, changing=False)
        except AccessRefusal as error:
            return _refused(error)
        return _json(
            {
                "poll_seconds": settings.board_poll_seconds,
                "session_hours": SESSION_HOURS,
            }
        )

    # The specific routes come first: the first that matches is the one that answers.
    api = "/board/api"
    mcp.custom_route(f"{api}/accounts", methods=["POST"])(register)
    mcp.custom_route(f"{api}/session", methods=["POST"])(sign_in)
    mcp.custom_route(f"{api}/session", methods=["GET"])(whoami)
    mcp.custom_route(f"{api}/session", methods=["DELETE"])(sign_out)
    mcp.custom_route(f"{api}/projects", methods=["GET"])(list_projects)
    mcp.custom_route(f"{api}/projects", methods=["POST"])(create_project)
    mcp.custom_route(f"{api}/projects/{{project_id}}", methods=["GET"])(show_project)
    mcp.custom_route(f"{api}/projects/{{project_id}}/keys", methods=["GET"])(list_keys)
    mcp.custom_route(f"{api}/projects/{{project_id}}/keys", methods=["POST"])(create_key)
    mcp.custom_route(f"{api}/projects/{{project_id}}/keys/{{key_id}}", methods=["DELETE"])(
        revoke_key
    )
    mcp.custom_route(f"{api}/projects/{{project_id}}/tool/{{name}}", methods=["POST"])(call_tool)
    mcp.custom_route(f"{api}/projects/{{project_id}}/try/{{name}}", methods=["POST"])(try_tool)
    mcp.custom_route(f"{api}/projects/{{project_id}}/index", methods=["GET"])(index)
    mcp.custom_route(f"{api}/tools", methods=["GET"])(tools)
    mcp.custom_route(f"{api}/info", methods=["GET"])(info)
    for path in ("/board", "/board/"):
        mcp.custom_route(path, methods=["GET"])(page)
    mcp.custom_route("/board/p/{project_id}", methods=["GET"])(page)
    mcp.custom_route("/board/{name:path}", methods=["GET"])(page)
