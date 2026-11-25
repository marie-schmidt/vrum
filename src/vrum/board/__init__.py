"""The board's server side: the page files, accounts and projects, and the one way the pages reach
a project's graph."""

import hashlib
from collections.abc import Mapping
from pathlib import Path

from vrum.accounts import Accounts
from vrum.board.http import SECURITY_HEADERS as SECURITY_HEADERS
from vrum.board.routes_auth import register_auth_routes
from vrum.board.routes_projects import register_project_routes
from vrum.board.routes_tools import register_tool_routes
from vrum.config import Settings
from vrum.guard import GuardedServer
from vrum.search import Searcher
from vrum.sso import Sso
from vrum.webhooks import WebhookBook

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
    "get_definition_of_done",
    "list_notes",
    "get_note",
    "note_history",
    "recall",
    "search",
    "similar_items",
    "find_overlaps",
    "find_mentions",
    "export_graph",
    "changes_since",
    "ready_work",
    "who_is_working",
    "list_asks",
    "needs_a_person",
    "list_plans",
    "list_plan_templates",
    "suggest_parent",
    "list_placements",
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
    "set_definition_of_done",
    "add_note",
    "supersede_note",
    "withdraw_note",
    "link_note",
    "unlink_note",
    "set_confirmation",
    "set_check",
    "answer_ask",
    "add_gate",
    "remove_gate",
    "approve_plan",
    "request_plan_changes",
    "decide_placement",
    "save_plan_template",
    "delete_plan_template",
    "create_plan",
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

# Stylesheets concatenated into /board/board-bundle.css in exact cascade order (tokens.css stays
# separate right after theme.js so theme tokens are loaded first). Individual files remain served
# for direct links and tests.
BUNDLE_CSS = (
    "base.css",
    "controls.css",
    "board.css",
    "kanban.css",
    "roadmap.css",
    "pages.css",
    "users.css",
    "records.css",
    "lists.css",
    "graph-explore.css",
    "graph-focus.css",
    "graph-explore-2.css",
    "graph-forces.css",
    "item.css",
    "agent-view.css",
    "status-menu.css",
    "datepicker.css",
    "hovercard.css",
    "key-tools.css",
    "whereabouts.css",
    "needs.css",
    "diff.css",
    "change-dialog.css",
    "plan-review.css",
    "review.css",
    "gates.css",
    "templates.css",
    "mail.css",
    "sso.css",
)


def _page_files() -> dict[str, tuple[bytes, str, str]]:
    """Every page file as it is served: its address, its bytes, its type and its ETag. The home
    of the board is also served at /board and /board/, and the stylesheets after tokens.css are
    concatenated once at startup into /board/board-bundle.css."""
    files: dict[str, tuple[bytes, str, str]] = {}
    for path in sorted(STATIC.rglob("*")):
        content_type = _CONTENT_TYPES.get(path.suffix)
        if path.is_file() and content_type is not None:
            body = path.read_bytes()
            etag = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
            address = "/board/" + path.relative_to(STATIC).as_posix()
            files[address] = (body, content_type, etag)
    bundle = b"\n".join(files[f"/board/{name}"][0] for name in BUNDLE_CSS)
    bundle_etag = f'"{hashlib.sha256(bundle).hexdigest()[:16]}"'
    files["/board/board-bundle.css"] = (bundle, _CONTENT_TYPES[".css"], bundle_etag)
    index = files.get("/board/index.html")
    if index is not None:
        files["/board"] = files["/board/"] = index
    return files


def _matches_etag(header: str | None, etag: str) -> bool:
    """Whether an If-None-Match header matches `etag`."""
    if not header:
        return False
    for raw in header.split(","):
        token = raw.strip()
        if token == "*" or token == etag or token.removeprefix("W/") == etag:
            return True
    return False


_FILES = _page_files()

# The addresses a link in an email opens, followed by its token. Each serves the board's one page.
_LINK_PAGES = ("/board/invite/", "/board/reset/", "/board/verify/")


def open_paths() -> Mapping[str, str]:
    """The addresses that need no sign-in (each answers GET only): the page files, which hold no
    data. The data calls under /board/api are not among them."""
    return dict.fromkeys(_FILES, "GET")


def add_board(
    mcp: GuardedServer,
    *,
    settings: Settings,
    searcher: Searcher,
    accounts: Accounts,
    sso: Sso,
    webhook_book: WebhookBook,
) -> None:
    """Add the board's routes to the server."""
    read_only: dict[str, bool] = {}

    async def only_reads(name: str) -> bool | None:
        if not read_only:
            for tool in await mcp.list_tools():
                read_only[tool.name] = bool(tool.annotations and tool.annotations.read_only_hint)
        return read_only.get(name)

    async def tool_names() -> frozenset[str]:
        await only_reads("")
        return frozenset(read_only)

    register_auth_routes(mcp, settings=settings, accounts=accounts, sso=sso)
    register_project_routes(
        mcp,
        settings=settings,
        accounts=accounts,
        webhook_book=webhook_book,
        tool_names=tool_names,
    )
    register_tool_routes(
        mcp,
        settings=settings,
        searcher=searcher,
        accounts=accounts,
        sso=sso,
        files=_FILES,
        link_pages=_LINK_PAGES,
        matches_etag=_matches_etag,
        board_tools=BOARD_TOOLS,
        only_reads=only_reads,
    )
