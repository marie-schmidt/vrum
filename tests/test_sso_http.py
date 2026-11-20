"""The one way Vrum calls a sign-in provider, against a real little web server on this machine: a
redirect is an answer and is never followed, too much is refused, a silent server times out, and an
address that is not https is refused unless http was allowed. No containers."""

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from vrum import sso_http
from vrum.sso_http import (
    MOST_BYTES,
    Call,
    ProviderUnreachable,
    allowed_address,
    fetch,
    with_query,
)

SEEN: list[dict[str, Any]] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _answer(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        SEEN.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers),
                "body": self.rfile.read(length).decode(),
            }
        )
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/somewhere-else")
            self.end_headers()
        elif self.path == "/refused":
            body = json.dumps({"error": "invalid_grant"}).encode()
            self.send_response(400)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/huge":
            body = b"x" * (MOST_BYTES + 10)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/slow":
            time.sleep(2)
            self.send_response(200)
            self.end_headers()
        elif self.path == "/not-json":
            body = b"<html>not json</html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    do_GET = do_POST = _answer


@pytest.fixture
def web() -> Iterator[str]:
    SEEN.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_a_get_and_a_form_post_carry_what_they_were_given(web: str) -> None:
    reply = asyncio.run(fetch(Call(f"{web}/plain", {"Accept": "application/json", "X-A": "b"})))
    assert (reply.status, reply.json()) == (200, {"ok": True})
    assert SEEN[0]["method"] == "GET" and SEEN[0]["headers"]["X-A"] == "b"
    asyncio.run(fetch(Call(f"{web}/plain", {}, {"code": "a b&c", "n": "1"})))
    assert SEEN[1]["method"] == "POST"
    assert SEEN[1]["body"] == "code=a+b%26c&n=1"
    assert SEEN[1]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"


def test_a_redirect_is_an_answer_and_is_never_followed(web: str) -> None:
    reply = asyncio.run(fetch(Call(f"{web}/redirect", {}, {"code": "secret-code"})))
    assert reply.status == 302
    assert [seen["path"] for seen in SEEN] == ["/redirect"]


def test_a_refusal_is_an_answer_with_its_body(web: str) -> None:
    reply = asyncio.run(fetch(Call(f"{web}/refused", {}, {"x": "y"})))
    assert reply.status == 400 and reply.json() == {"error": "invalid_grant"}


def test_an_answer_bigger_than_a_mebibyte_is_refused(web: str) -> None:
    with pytest.raises(ProviderUnreachable):
        asyncio.run(fetch(Call(f"{web}/huge")))


def test_an_answer_that_is_not_json_is_refused_when_it_is_read_as_json(web: str) -> None:
    reply = asyncio.run(fetch(Call(f"{web}/not-json")))
    assert reply.status == 200
    with pytest.raises(ProviderUnreachable):
        reply.json()


def test_a_provider_that_does_not_answer_in_time_is_unreachable(
    web: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sso_http, "TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(sso_http, "DEADLINE_SECONDS", 0.5)
    with pytest.raises(ProviderUnreachable):
        asyncio.run(fetch(Call(f"{web}/slow")))


def test_a_provider_that_is_not_there_is_unreachable() -> None:
    with pytest.raises(ProviderUnreachable):
        asyncio.run(fetch(Call("http://127.0.0.1:9/never")))


@pytest.mark.parametrize(
    ("url", "plain_http_allowed", "allowed"),
    [
        ("https://login.example.com/token", False, True),
        ("http://login.example.com/token", False, False),
        ("http://login.example.com/token", True, True),
        ("https://user:pw@login.example.com/token", True, False),
        ("https://login.example.com/token#frag", True, False),
        ("ftp://login.example.com/token", True, False),
        ("javascript:alert(1)", True, False),
        ("https:///token", True, False),
        ("https://login example.com/", True, False),
        ("", True, False),
        (None, True, False),
        (42, True, False),
    ],
)
def test_an_address_from_a_provider_is_used_only_when_it_is_https(
    url: object, plain_http_allowed: bool, allowed: bool
) -> None:
    assert allowed_address(url, allow_insecure_http=plain_http_allowed) is allowed


def test_more_query_is_added_to_an_address_that_has_some() -> None:
    assert with_query("https://x.example.com/a", {"b": "c d"}) == "https://x.example.com/a?b=c+d"
    assert (
        with_query("https://x.example.com/a?tenant=1", {"state": "s"})
        == "https://x.example.com/a?tenant=1&state=s"
    )
