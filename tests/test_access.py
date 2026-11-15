"""The access guard against a bare ASGI application, so what it lets in and what it refuses (and
what it says and logs when it refuses) is seen exactly, without any server."""

import json
import logging
from typing import Any

import pytest
from starlette.types import Message, Receive, Scope, Send

from vrum.access import OPEN_PATHS, AccessGuard
from vrum.logs import Scrubber, configure_logging, event

pytestmark = pytest.mark.anyio

TOKEN = "0123456789abcdef0123456789abcdef"
LIMIT = 1000


class Recorder:
    """An application that records what reached it, and answers 200 with what it read."""

    def __init__(self) -> None:
        self.reached = 0
        self.body = b""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            return
        self.reached += 1
        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        self.body = body
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"fine"})


class Answer:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status, self.headers, self.body = status, headers, body

    @property
    def error(self) -> str:
        return str(json.loads(self.body)["error"]["message"])


async def ask(
    guard: AccessGuard,
    *,
    method: str = "POST",
    path: str = "/mcp",
    authorization: str | None = None,
    chunks: list[bytes] | None = None,
    content_length: int | None | str = "auto",
) -> Answer:
    chunks = [b'{"hello": 1}'] if chunks is None else chunks
    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    if content_length == "auto":
        headers.append((b"content-length", str(sum(map(len, chunks))).encode()))
    elif content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    scope: Scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers,
        "client": ("10.1.2.3", 5000),
    }
    queue: list[Message] = [
        {"type": "http.request", "body": chunk, "more_body": number < len(chunks) - 1}
        for number, chunk in enumerate(chunks)
    ] or [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive() -> Message:
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await guard(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return Answer(
        start["status"],
        {name.decode(): value.decode() for name, value in start["headers"]},
        body,
    )


@pytest.fixture
def inner() -> Recorder:
    return Recorder()


@pytest.fixture
def guard(inner: Recorder) -> AccessGuard:
    return AccessGuard(inner, token=TOKEN, max_request_bytes=LIMIT)


async def test_the_right_token_lets_a_request_through_with_its_body_intact(
    guard: AccessGuard, inner: Recorder
) -> None:
    answer = await ask(guard, authorization=f"Bearer {TOKEN}")

    assert (answer.status, answer.body) == (200, b"fine")
    assert inner.reached == 1 and inner.body == b'{"hello": 1}'


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
async def test_the_word_bearer_may_be_written_in_any_case(guard: AccessGuard, scheme: str) -> None:
    assert (await ask(guard, authorization=f"{scheme} {TOKEN}")).status == 200


@pytest.mark.parametrize(
    ("authorization", "expected"),
    [
        (None, "needs a token and none was sent"),
        ("", "needs a token and none was sent"),
        ("   ", "needs a token and none was sent"),
        ("Bearer wrong-token-value-1234567890", "token is not right"),
        (f"Bearer {TOKEN[:-1]}", "token is not right"),
        (f"Bearer {TOKEN}x", "token is not right"),
        ("Bearer ", "token is not right"),
        (f"Basic {TOKEN}", "not a bearer token"),
        (TOKEN, "not a bearer token"),
    ],
)
async def test_a_request_with_no_token_a_wrong_one_or_another_header_is_refused_in_plain_words(
    guard: AccessGuard, inner: Recorder, authorization: str | None, expected: str
) -> None:
    answer = await ask(guard, authorization=authorization)

    assert answer.status == 401
    assert expected in answer.error and "Authorization" in answer.error
    assert answer.headers["www-authenticate"] == 'Bearer realm="vrum"'
    assert answer.headers["content-type"] == "application/json"
    envelope = json.loads(answer.body)
    assert (envelope["jsonrpc"], envelope["id"], envelope["error"]["code"]) == ("2.0", None, -32001)
    assert inner.reached == 0
    # What was sent is not repeated back, and the real token is not given away.
    assert TOKEN not in answer.body.decode()
    if authorization and "wrong" in authorization:
        assert authorization.split()[1] not in answer.body.decode()


async def test_a_token_in_the_address_is_not_a_token(guard: AccessGuard, inner: Recorder) -> None:
    answer = await ask(guard, path=f"/mcp?token={TOKEN}", authorization=None)
    assert answer.status == 401 and inner.reached == 0


async def test_the_health_check_needs_no_token_and_nothing_else_is_open(
    guard: AccessGuard, inner: Recorder
) -> None:
    assert OPEN_PATHS == {"/health": "GET"}

    assert (await ask(guard, method="GET", path="/health", chunks=[])).status == 200
    assert inner.reached == 1
    for method, path in (("POST", "/health"), ("GET", "/mcp"), ("GET", "/"), ("GET", "/health/x")):
        assert (await ask(guard, method=method, path=path, chunks=[])).status == 401
    assert inner.reached == 1


async def test_paths_given_to_the_guard_as_open_are_open_for_their_method_only(
    inner: Recorder,
) -> None:
    guard = AccessGuard(
        inner,
        token=TOKEN,
        max_request_bytes=LIMIT,
        open_paths={**OPEN_PATHS, "/board": "GET", "/board/app.js": "GET"},
    )
    for path in ("/health", "/board", "/board/app.js"):
        assert (await ask(guard, method="GET", path=path, chunks=[])).status == 200
    assert inner.reached == 3
    for method, path in (
        ("POST", "/board"),
        ("GET", "/board/api/info"),
        ("GET", "/board/other.js"),
        ("POST", "/board/api/tool/list_nodes"),
    ):
        assert (await ask(guard, method=method, path=path, chunks=[])).status == 401
    assert inner.reached == 3


async def test_a_request_that_says_it_is_too_big_is_refused_before_its_body_is_read(
    guard: AccessGuard, inner: Recorder
) -> None:
    answer = await ask(
        guard,
        authorization=f"Bearer {TOKEN}",
        chunks=[b"x" * (LIMIT + 1)],
    )

    assert answer.status == 413
    assert f"{LIMIT + 1:,} bytes" in answer.error and f"{LIMIT:,} bytes" in answer.error
    assert "VRUM_MAX_REQUEST_BYTES" in answer.error and "Nothing was done" in answer.error
    assert inner.reached == 0


async def test_a_request_that_does_not_say_how_long_it_is_is_counted_as_it_arrives(
    guard: AccessGuard, inner: Recorder
) -> None:
    unannounced: dict[str, Any] = {"content_length": None, "authorization": f"Bearer {TOKEN}"}

    under = await ask(guard, chunks=[b"a" * 400, b"b" * 400, b"c" * 200], **unannounced)
    assert under.status == 200 and len(inner.body) == LIMIT

    over = await ask(guard, chunks=[b"a" * 600, b"b" * 401], **unannounced)
    assert over.status == 413 and "too big" in over.error and inner.reached == 1


async def test_a_request_of_exactly_the_limit_is_accepted(
    guard: AccessGuard, inner: Recorder
) -> None:
    answer = await ask(guard, authorization=f"Bearer {TOKEN}", chunks=[b"x" * LIMIT])
    assert answer.status == 200 and len(inner.body) == LIMIT


async def test_a_request_with_no_body_at_all_passes_whether_or_not_it_says_so(
    guard: AccessGuard, inner: Recorder
) -> None:
    for content_length in ("auto", None):
        answer = await ask(
            guard,
            method="GET",
            authorization=f"Bearer {TOKEN}",
            chunks=[],
            content_length=content_length,
        )
        assert answer.status == 200
    assert inner.reached == 2


async def test_it_leaves_everything_that_is_not_a_web_request_alone(inner: Recorder) -> None:
    guard = AccessGuard(inner, token=TOKEN, max_request_bytes=LIMIT)

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        raise AssertionError("nothing is sent")

    await guard({"type": "lifespan"}, receive, send)


async def test_a_refusal_is_one_log_line_with_the_reason_and_never_what_was_sent(
    guard: AccessGuard, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging([TOKEN])
    sent_token = "the-wrong-token-that-someone-tried-1234"

    await ask(guard, authorization=f"Bearer {sent_token}")
    await ask(guard, authorization=None)
    await ask(guard, authorization=f"Bearer {TOKEN}", chunks=[b"x" * (LIMIT + 5)])

    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    refused = [line for line in lines if line["event"] == "request_refused"]
    assert [(line["reason"], line["status"]) for line in refused] == [
        ("wrong_token", 401),
        ("no_token", 401),
        ("too_large", 413),
    ]
    assert refused[0]["method"] == "POST" and refused[0]["path"] == "/mcp"
    assert refused[0]["client"] == "10.1.2.3" and refused[2]["declared_bytes"] == LIMIT + 5
    everything = capsys.readouterr().err + json.dumps(lines)
    assert sent_token not in everything and TOKEN not in everything


def test_the_scrubber_removes_held_secrets_and_known_formats_but_leaves_short_ones() -> None:
    scrub = Scrubber([TOKEN, "pw"])
    github = "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fG3hJ5"

    scrubbed = scrub(f"token {TOKEN} and {github} and pw stays")

    assert scrubbed == "token [secret removed] and [a GitHub token removed] and pw stays"
    assert scrub.deep({"a": [f"x{TOKEN}"], "b": 3}) == {"a": ["x[secret removed]"], "b": 3}


def test_a_log_line_is_json_with_the_event_the_facts_and_the_scrub_applied(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = 'tricky"token\\with-quotes-1234'
    configure_logging([secret])
    log = logging.getLogger("vrum.test")

    log.info("plain message")
    event(log, logging.WARNING, "something_happened", tool="create_node", note=f"x {secret}")
    try:
        raise ValueError(f"failed with {secret} inside")
    except ValueError:
        log.exception("it broke")

    first, second, third = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert (first["event"], first["level"], first["logger"]) == (
        "plain message",
        "INFO",
        "vrum.test",
    )
    assert {"time", "level", "event"} <= first.keys()
    assert (second["event"], second["tool"], second["level"]) == (
        "something_happened",
        "create_node",
        "WARNING",
    )
    assert second["note"] == "x [secret removed]"
    assert "[secret removed]" in third["exception"] and "tricky" not in third["exception"]
