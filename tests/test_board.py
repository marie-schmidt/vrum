"""The board's server side, without any containers: which tools its pages may call, what is open
without signing in, what the page files do and do not contain, and its one setting."""

import asyncio
import os
import re
from collections.abc import Mapping
from typing import Any

import pytest

from vrum.board import BOARD_TOOLS, READ_TOOLS, SECURITY_HEADERS, STATIC, WRITE_TOOLS, open_paths
from vrum.board.tools import AREAS, TOOL_PLACES, AgentsOnly, OnTheBoard, ToolPlace
from vrum.config import ConfigError, Settings
from vrum.server import create_server

ENV = {"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "s3cret-value"}
PAGE_FILES = [path for path in STATIC.rglob("*") if path.is_file()]
SCRIPTS = [path for path in PAGE_FILES if path.suffix == ".js"]


def test_every_tool_the_board_may_call_is_a_tool_the_server_has() -> None:
    server = create_server(Settings.from_env(ENV))
    tools = {tool.name for tool in asyncio.run(server.list_tools())}
    assert tools >= BOARD_TOOLS
    assert set(READ_TOOLS).isdisjoint(WRITE_TOOLS)


def test_the_board_cannot_write_to_the_index_or_load_a_whole_graph() -> None:
    assert not BOARD_TOOLS & {"reindex", "import_graph"}


def test_every_tool_a_page_script_names_is_one_the_board_may_call() -> None:
    # A page that names a tool the server would turn away would fail only when a person clicked
    # it, so the names in the scripts are checked here against the list. A name with an
    # underscore is a tool wherever it is quoted; a one-word name (recall, search) only counts as
    # one where it is the first argument of a call, because `search` is also an HTML role.
    server = create_server(Settings.from_env(ENV))
    tools = {tool.name for tool in asyncio.run(server.list_tools())}
    for path in SCRIPTS:
        text = path.read_text(encoding="utf-8")
        named = {
            name
            for name in tools
            if re.search(
                rf"['\"`]{name}['\"`]"
                if "_" in name
                else rf"\b(?:call|callAll)\(\s*['\"`]{name}['\"`]",
                text,
            )
        }
        assert named <= BOARD_TOOLS, f"{path.name} names {sorted(named - BOARD_TOOLS)}"


def test_only_the_page_files_are_open_and_the_data_calls_are_not() -> None:
    opened = open_paths()
    assert set(opened.values()) == {"GET"}
    assert all(path.startswith("/board") for path in opened)
    assert not [path for path in opened if path.startswith("/board/api/")]
    assert {"/board", "/board/", "/board/app.js", "/board/board.css"} <= set(opened)
    served = {"/board/" + path.relative_to(STATIC).as_posix() for path in PAGE_FILES}
    assert served <= set(opened)


def test_the_page_files_load_nothing_from_anywhere_else() -> None:
    namespace = "http://www.w3.org/2000/svg"
    for path in PAGE_FILES:
        if path.suffix == ".png":
            continue
        text = path.read_text(encoding="utf-8")
        outside = [
            found for found in re.findall(r"https?://[^\s'\")<]+", text) if found != namespace
        ]
        assert outside == [], f"{path.name} names an address outside the server: {outside}"
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert not re.findall(r"<script(?![^>]*\ssrc=)", index), "an inline script"
    assert " style=" not in index and "<style" not in index


def test_nothing_is_put_on_a_page_as_html() -> None:
    # Text is always put in as text, so what a title or a note holds can never run.
    banned = (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
    )
    for path in SCRIPTS:
        text = path.read_text(encoding="utf-8")
        assert [word for word in banned if word in text] == [], path.name


def test_nothing_is_styled_with_a_style_attribute() -> None:
    # The security policy turns style attributes away without a word, so a bar or a column
    # that needs a size sets it on the element (the `css` property of `h`) and nothing else.
    for path in SCRIPTS:
        text = path.read_text(encoding="utf-8")
        found = re.findall(r"\bstyle\s*:|setAttribute\(\s*['\"]style|cssText", text)
        assert found == [], f"{path.name} uses a style attribute: {found}"


def test_the_security_policy_allows_only_the_servers_own_files_and_calls() -> None:
    policy = SECURITY_HEADERS["Content-Security-Policy"]
    assert "default-src 'none'" in policy and "script-src 'self'" in policy
    assert "connect-src 'self'" in policy and "frame-ancestors 'none'" in policy
    assert "unsafe" not in policy and "http" not in policy and "*" not in policy


def test_no_page_script_keeps_a_session_or_an_agent_key_in_the_browser() -> None:
    # The session is a cookie the page cannot read, and its secret lives in memory only. The one
    # thing kept in the browser is the name a person's changes are recorded with, and every call
    # to the server goes through api.js, which puts the project in the address and sends no key.
    for path in SCRIPTS:
        text = path.read_text(encoding="utf-8")
        assert [w for w in ("document.cookie", "sessionStorage", "indexedDB") if w in text] == []
        assert "VRUM_TOKEN" not in text, path.name
        if path.name != "api.js":
            assert not re.search(r"\bfetch\(|XMLHttpRequest|sendBeacon|WebSocket", text), path.name
        if path.name not in ("ctx.js", "theme.js"):
            assert "localStorage" not in text, path.name
    api_text = (STATIC / "api.js").read_text(encoding="utf-8")
    assert "Authorization" not in api_text
    ctx_text = (STATIC / "ctx.js").read_text(encoding="utf-8")
    assert re.findall(r"localStorage\.\w+\((\w+)", ctx_text) == ["NAME_KEY"] * 2
    assert "const NAME_KEY = 'vrum.actor'" in ctx_text
    # The other thing kept is the choice of appearance (Light, Dark or System), and nothing else.
    theme_text = (STATIC / "theme.js").read_text(encoding="utf-8")
    assert set(re.findall(r"localStorage\.\w+\((\w+)", theme_text)) == {"KEY"}
    assert "var KEY = 'vrum.theme'" in theme_text
    assert "localStorage.clear" not in "".join(path.read_text(encoding="utf-8") for path in SCRIPTS)


def test_the_board_asks_for_news_every_five_seconds_unless_told_otherwise() -> None:
    assert Settings.from_env(ENV).board_poll_seconds == 5.0
    assert Settings.from_env({**ENV, "VRUM_BOARD_POLL_SECONDS": "2"}).board_poll_seconds == 2.0
    with pytest.raises(ConfigError, match="VRUM_BOARD_POLL_SECONDS must be a number of seconds"):
        Settings.from_env({**ENV, "VRUM_BOARD_POLL_SECONDS": "0.1"})


# -- the Tools page: every tool is on the board or marked as for agents only ----------------------


def live_tools() -> dict[str, Any]:
    server = create_server(Settings.from_env(ENV))
    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


def classification_problems(places: Mapping[str, ToolPlace], live: set[str]) -> list[str]:
    """What is wrong with the map of tools: one the server has that is not in it, one in it that
    the server does not have, one with no area, and one the board calls but marks as agents only."""
    problems = [
        f"{name} is neither on the board nor marked as for agents only"
        for name in sorted(live - set(places))
    ]
    problems += [
        f"{name} is in the map but the server has no such tool"
        for name in sorted(set(places) - live)
    ]
    for name, place in sorted(places.items()):
        if place.area not in AREAS:
            problems.append(f"{name} is in an area that does not exist: {place.area}")
        if isinstance(place.place, AgentsOnly) and name in BOARD_TOOLS:
            problems.append(f"{name} is marked as for agents only, but the board calls it")
        if isinstance(place.place, AgentsOnly) and not place.place.why.strip():
            problems.append(f"{name} is for agents only and does not say why")
        if isinstance(place.place, OnTheBoard) and not place.place.where.strip():
            problems.append(f"{name} is on the board and does not say where")
    return problems


def test_every_tool_the_server_has_is_on_the_board_or_marked_as_for_agents_only() -> None:
    # ./acceptance.sh runs this twice, to show that it fails when one tool is taken out of the map
    # and passes again once it is back: VRUM_TEST_DROP_FROM_MAP names the tool to leave out.
    places = dict(TOOL_PLACES)
    places.pop(os.environ.get("VRUM_TEST_DROP_FROM_MAP", ""), None)
    assert classification_problems(places, set(live_tools())) == []


def test_that_check_fails_when_a_tool_is_missing_from_the_map_or_made_up() -> None:
    # The check has teeth: take one tool out of the map and it is named; add one that is not a
    # tool and that is named too.
    live = set(live_tools())
    smaller = {name: place for name, place in TOOL_PLACES.items() if name != "reindex"}
    assert classification_problems(smaller, live) == [
        "reindex is neither on the board nor marked as for agents only"
    ]
    larger = {**TOOL_PLACES, "no_such_tool": TOOL_PLACES["reindex"]}
    assert classification_problems(larger, live) == [
        "no_such_tool is in the map but the server has no such tool"
    ]
    calls_it = {**TOOL_PLACES, "search": ToolPlace("Server", AgentsOnly("A reason."))}
    assert classification_problems(calls_it, live) == [
        "search is marked as for agents only, but the board calls it"
    ]


def test_the_tools_that_only_read_say_so_and_are_the_ones_the_board_reads_with() -> None:
    tools = live_tools()
    reads = {
        name for name, tool in tools.items() if tool.annotations and tool.annotations.read_only_hint
    }
    # Everything the board reads with is a read, and nothing the board writes with is.
    assert set(READ_TOOLS) <= reads
    assert reads.isdisjoint(WRITE_TOOLS)
    # What changes something (the graph, the index or the log) is never marked as a read.
    for name in (
        "create_node",
        "claim_node",
        "next_work",
        "reindex",
        "import_graph",
        "retain_fact",
        "record_decision",
        "set_confirmation",
    ):
        assert name not in reads


# -- M16: three views, no generation, no removed pages --------------------------------------------

GENERATION = {"extract_facts", "consolidate_memory", "find_contradictions", "reflect"}


def test_the_board_is_three_views_and_the_settings_pages_and_the_old_pages_have_no_script() -> None:
    ctx = (STATIC / "ctx.js").read_text(encoding="utf-8")
    views = re.search(r"export const VIEWS = \[(.*?)\]", ctx)
    settings = re.search(r"export const SETTINGS = \[(.*?)\]", ctx)
    assert views and settings
    assert re.findall(r"'(\w+)'", views.group(1)) == ["board", "graph", "roadmap"]
    assert re.findall(r"'(\w+)'", settings.group(1)) == ["access", "tools", "status"]
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    pages = re.search(r"const PAGES = \{(.*?)\n\};", app, re.S)
    assert pages
    assert re.findall(r"^\s{2}(\w+): \{ title", pages.group(1), re.M) == [
        "board",
        "graph",
        "roadmap",
        "access",
        "tools",
        "status",
    ]
    # Home, Table, Outline, Rules, Memory, Overlaps and Ask are gone, scripts and all: Search and
    # Activity are drawers, and requirements, facts and decisions live in the panel and the Graph.
    gone = {"home", "table", "outline", "rules", "memory", "memory-forms", "overlaps", "ask"}
    assert not [path.name for path in SCRIPTS if path.stem in gone]


def test_no_generation_tool_exists_and_the_board_neither_calls_nor_names_one() -> None:
    tools = set(live_tools())
    assert not tools & GENERATION
    assert not BOARD_TOOLS & GENERATION
    assert not {name for name in tools if "belief" in name or "principle" in name}
    # The board's own words never name a language model or an Ask box.
    for path in SCRIPTS:
        text = path.read_text(encoding="utf-8").lower()
        assert "language model" not in text and "language_model" not in text, path.name
        assert "reflect" not in text.replace("reflection", ""), path.name
    # Facts and decisions are written, withdrawn and confirmed through the board's operations.
    assert {"retain_fact", "record_decision", "withdraw_fact", "withdraw_decision"} <= set(
        WRITE_TOOLS
    )
    assert "set_confirmation" in WRITE_TOOLS
