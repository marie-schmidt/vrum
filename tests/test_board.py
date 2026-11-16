"""The board's server side, without any containers: which tools its pages may call, what is open
without a token, what the page files do and do not contain, and its one setting."""

import asyncio
import re

import pytest

from vrum.board import BOARD_TOOLS, READ_TOOLS, SECURITY_HEADERS, STATIC, WRITE_TOOLS, open_paths
from vrum.config import ConfigError, Settings
from vrum.server import create_server

ENV = {"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "s3cret-value", "VRUM_TOKEN": "t" * 24}
PAGE_FILES = [path for path in STATIC.rglob("*") if path.is_file()]
SCRIPTS = [path for path in PAGE_FILES if path.suffix == ".js"]


def test_every_tool_the_board_may_call_is_a_tool_the_server_has() -> None:
    server = create_server(Settings.from_env(ENV))
    tools = {tool.name for tool in asyncio.run(server.list_tools())}
    assert tools >= BOARD_TOOLS
    assert set(READ_TOOLS).isdisjoint(WRITE_TOOLS)


def test_the_board_cannot_write_to_the_index_or_load_a_whole_graph() -> None:
    assert not BOARD_TOOLS & {"reindex", "import_graph"}


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


def test_the_board_asks_for_news_every_five_seconds_unless_told_otherwise() -> None:
    assert Settings.from_env(ENV).board_poll_seconds == 5.0
    assert Settings.from_env({**ENV, "VRUM_BOARD_POLL_SECONDS": "2"}).board_poll_seconds == 2.0
    with pytest.raises(ConfigError, match="VRUM_BOARD_POLL_SECONDS must be a number of seconds"):
        Settings.from_env({**ENV, "VRUM_BOARD_POLL_SECONDS": "0.1"})
