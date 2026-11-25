"""Board HTTP routes for calling MCP tools, index status, tool metadata, server info, and pages."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from starlette.requests import Request
from starlette.responses import Response

from vrum.access import MCP_PATH
from vrum.accounts import SESSION_HOURS, VIEWER_CANNOT_CHANGE, Accounts, ProjectInfo
from vrum.board.http import (
    API,
    SECURITY_HEADERS,
    api_route,
    in_project,
    json_response,
    plain_tool_error,
)
from vrum.board.session import signed_in
from vrum.board.tools import AREAS, TOOL_PLACES, AgentsOnly, OnTheBoard
from vrum.config import Settings
from vrum.db import NoProject, working_for
from vrum.guard import GuardedServer
from vrum.identity import NoIdentity, person, working_as
from vrum.records import PEOPLE_ONLY_TOOLS
from vrum.search import Searcher
from vrum.sso import Sso
from vrum.surface import AGENT_SURFACE, CODE_TOOLS, MOST_AGENT_TOOLS


def register_tool_routes(
    mcp: GuardedServer,
    *,
    settings: Settings,
    searcher: Searcher,
    accounts: Accounts,
    sso: Sso,
    files: Mapping[str, tuple[bytes, str, str]],
    link_pages: tuple[str, ...],
    matches_etag: Callable[[str | None, str], bool],
    board_tools: frozenset[str],
    only_reads: Callable[[str], Awaitable[bool | None]],
) -> None:
    async def page(request: Request) -> Response:
        path = request.url.path
        is_a_project = path.startswith("/board/p/") and path[len("/board/p/") :].isdecimal()
        is_a_link = any(
            path.startswith(prefix) and path[len(prefix) :] and "/" not in path[len(prefix) :]
            for prefix in link_pages
        )
        if is_a_project or is_a_link:
            path = "/board"
        found = files.get(path)
        if found is None:
            return Response("There is no such page file.", status_code=404, media_type="text/plain")
        body, content_type, etag = found
        headers = {**SECURITY_HEADERS, "Cache-Control": "no-cache", "ETag": etag}
        if matches_etag(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        return Response(body, media_type=content_type, headers=headers)

    async def run(project: ProjectInfo, who: str, name: str, arguments: Any) -> Response:
        if project.role == "viewer" and await only_reads(name) is not True:
            return json_response({"message": VIEWER_CANNOT_CHANGE}, 403)
        if not isinstance(arguments, dict):
            return json_response(
                {"message": "The request has to be a JSON object of arguments."}, 400
            )
        try:
            with working_for(project.id), working_as(person(who)):
                result = await mcp.call_tool(name, arguments)
        except (UnexpectedToolError, NoProject, NoIdentity):
            return json_response(
                {
                    "message": "Something went wrong inside the server, and the change may not "
                    "have been made. Look at the server's log (docker compose logs app)."
                },
                500,
            )
        except ToolError as error:
            return json_response({"message": plain_tool_error(name, error)}, 400)
        return json_response(result.structured_content or {})

    async def arguments_of(request: Request) -> Any:
        try:
            return await request.json()
        except ValueError:
            return None

    async def call_tool(request: Request) -> Response:
        name = request.path_params["name"]
        session, project = await in_project(accounts, request, changing=True)
        if name not in board_tools:
            return json_response({"message": f"The board does not use a tool called {name}."}, 404)
        return await run(project, session.account.name, name, await arguments_of(request))

    async def try_tool(request: Request) -> Response:
        name = request.path_params["name"]
        session, project = await in_project(accounts, request, changing=True)
        reads = await only_reads(name)
        if reads is None:
            return json_response({"message": f"The server has no tool called {name}."}, 404)
        if not reads:
            return json_response(
                {
                    "message": f"{name} changes something, so the board does not let you try it "
                    "here. Use the page the tool names, or an agent."
                },
                403,
            )
        return await run(project, session.account.name, name, await arguments_of(request))

    async def index(request: Request) -> Response:
        _, project = await in_project(accounts, request, changing=False)
        with working_for(project.id):
            note = await searcher.index_state()
        return json_response(note.model_dump())

    tools_payload: dict[str, Any] | None = None

    async def tools(request: Request) -> Response:
        nonlocal tools_payload
        await signed_in(accounts, request, changing=False)
        if tools_payload is None:
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
                        "board_calls": tool.name in board_tools,
                        "people_only": tool.name in PEOPLE_ONLY_TOOLS,
                        "offered_to_agents": tool.name in AGENT_SURFACE,
                        "code_mode": tool.name in CODE_TOOLS,
                    }
                )
            tools_payload = {
                "mcp_path": MCP_PATH,
                "areas": list(AREAS),
                "most_agent_tools": MOST_AGENT_TOOLS,
                "tools": found,
            }
        return json_response(tools_payload)

    async def info(request: Request) -> Response:
        await signed_in(accounts, request, changing=False)
        return json_response(
            {
                "poll_seconds": settings.board_poll_seconds,
                "session_hours": SESSION_HOURS,
                "mail": {
                    "enabled": settings.mail is not None,
                    "from": settings.mail.sender if settings.mail else None,
                    "host": settings.mail.host if settings.mail else None,
                },
                "sign_in": sso.about(),
            }
        )

    api_route(mcp, f"{API}/projects/{{project_id}}/tool/{{name}}", call_tool, "POST")
    api_route(mcp, f"{API}/projects/{{project_id}}/try/{{name}}", try_tool, "POST")
    api_route(mcp, f"{API}/projects/{{project_id}}/index", index, "GET")
    api_route(mcp, f"{API}/tools", tools, "GET")
    api_route(mcp, f"{API}/info", info, "GET")
    for path in ("/board", "/board/"):
        mcp.custom_route(path, methods=["GET"])(page)
    mcp.custom_route("/board/p/{project_id}", methods=["GET"])(page)
    mcp.custom_route("/board/invite/{token}", methods=["GET"])(page)
    mcp.custom_route("/board/reset/{token}", methods=["GET"])(page)
    mcp.custom_route("/board/verify/{token}", methods=["GET"])(page)
    mcp.custom_route("/board/{name:path}", methods=["GET"])(page)
