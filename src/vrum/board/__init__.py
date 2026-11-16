"""The board's server side: the page files, and the one way the pages reach the graph.

The page files (src/vrum/board/static) are plain HTML, CSS and JavaScript that hold no data, so
they are open and a browser can load them before it asks for the token. Everything the pages show
or change goes through `POST /board/api/tool/<name>`, which calls the MCP tool of that name on the
same server, so the board is subject to exactly the same checks as an agent: the same refusals in
the same words, the same versions, claims and event log, the same scan for secrets. There is
nothing else the board can do: the tools it may call are listed here, and every data call needs
the token like any other request."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from vrum.config import Settings
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
    "coverage_report",
    "list_decisions",
    "list_memories",
    "recall",
    "similar_items",
    "find_overlaps",
    "export_graph",
    "changes_since",
)
# find_contradictions only proposes (it changes nothing in the graph), but it asks the language
# model and writes a record that it did, as a person's change does, so it takes a name too.
WRITE_TOOLS = (
    "create_node",
    "update_node",
    "update_nodes",
    "move_node",
    "add_link",
    "remove_link",
    "create_rule",
    "update_rule",
    "place_rule",
    "move_rule",
    "record_decision",
    "supersede_decision",
    "link_decision",
    "unlink_decision",
    "retain_fact",
    "record_belief",
    "supersede_memory",
    "retract_memory",
    "confirm_belief",
    "promote_belief",
    "link_memory",
    "unlink_memory",
    "find_contradictions",
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
    """The addresses that need no token (each answers GET only): the page files, which hold no
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


def add_board(mcp: GuardedServer, *, settings: Settings, searcher: Searcher) -> None:
    """Add the board's routes to the server."""

    async def page(request: Request) -> Response:
        found = _FILES.get(request.url.path)
        if found is None:
            return Response("There is no such page file.", status_code=404, media_type="text/plain")
        body, content_type = found
        return Response(
            body,
            media_type=content_type,
            headers={**SECURITY_HEADERS, "Cache-Control": "no-cache"},
        )

    async def call_tool(request: Request) -> Response:
        name = request.path_params["name"]
        if name not in BOARD_TOOLS:
            return _json({"message": f"The board does not use a tool called {name}."}, 404)
        try:
            arguments = await request.json()
        except ValueError:
            arguments = None
        if not isinstance(arguments, dict):
            return _json({"message": "The request has to be a JSON object of arguments."}, 400)
        try:
            result = await mcp.call_tool(name, arguments)
        except UnexpectedToolError:
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

    async def info(request: Request) -> Response:
        return _json(
            {
                "poll_seconds": settings.board_poll_seconds,
                "language_model": settings.llm.model if settings.llm else None,
            }
        )

    async def index(request: Request) -> Response:
        note = await searcher.index_state()
        return _json(note.model_dump())

    # The specific routes come first: the first that matches is the one that answers.
    mcp.custom_route("/board/api/tool/{name}", methods=["POST"])(call_tool)
    mcp.custom_route("/board/api/info", methods=["GET"])(info)
    mcp.custom_route("/board/api/index", methods=["GET"])(index)
    for path in ("/board", "/board/"):
        mcp.custom_route(path, methods=["GET"])(page)
    mcp.custom_route("/board/{name:path}", methods=["GET"])(page)
