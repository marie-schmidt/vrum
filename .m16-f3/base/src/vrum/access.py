"""Who may talk to the server, and how much they may send.

The MCP endpoint needs an agent key for a project, as `Authorization: Bearer <key>`. A request with
no key, a key that is wrong or revoked, or the wrong sort of header is refused before its body is
read. The health check and the board's page files hold no data and are open. The board's own calls
(under /board/api) are checked by the board against the person's session, because they carry a
cookie and not a key. Every request with more bytes than the limit is refused before it is
handled. Each refusal says in plain words what to do. Nothing a caller sent in a header is ever
repeated back or written to a log."""

import json
import logging
from collections import deque
from collections.abc import Mapping
from typing import Any, Protocol

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vrum.accounts import BAD_AGENT_KEY, NO_AGENT_KEY
from vrum.logs import event

log = logging.getLogger("vrum.access")

# The JSON-RPC error code that goes with each refusal (the range -32000 to -32099 is for servers).
_CODES = {401: -32001, 413: -32002}

# Paths that need no key, with the one method each answers. They say nothing about the data. The
# board's page files are added to these when the application is built (see vrum.board).
OPEN_PATHS = {"/health": "GET"}

# Where the MCP endpoint is, which is the SDK's own default.
MCP_PATH = "/mcp"

NOT_A_BEARER = (
    "The Authorization header is not a bearer token, so nothing was done. Send it as "
    "'Authorization: Bearer <key>', with the agent key of the project."
)


class AgentKeys(Protocol):
    async def project_for_key(self, key: str) -> int | None: ...


def bearer_key(header: str | None) -> tuple[str | None, str | None]:
    """The key in an Authorization header, or why there is none: (key, reason)."""
    if header is None or not header.strip():
        return None, "no_key"
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented.strip():
        return None, "not_bearer"
    return presented.strip(), None


def too_large(size: int | None, limit: int) -> str:
    how_big = f"This request is {size:,} bytes" if size is not None else "This request is too big"
    return (
        f"{how_big}, and the most this server accepts in one request is {limit:,} bytes. Nothing "
        "was done. Send it in smaller parts, or ask the owner to raise VRUM_MAX_REQUEST_BYTES."
    )


class AccessGuard:
    def __init__(
        self,
        app: ASGIApp,
        *,
        keys: AgentKeys,
        max_request_bytes: int,
        open_paths: Mapping[str, str] = OPEN_PATHS,
    ) -> None:
        self.app = app
        self._keys = keys
        self._limit = max_request_bytes
        self._open_paths = open_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path, method = scope["path"], scope["method"]
        if self._open_paths.get(path) == method:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if path == MCP_PATH or path.startswith(MCP_PATH + "/"):
            problem = await self._key_problem(headers.get("authorization"))
            if problem is not None:
                reason, message = problem
                await self._refuse(scope, send, 401, reason, message)
                return
        declared = _declared_size(headers)
        if declared is not None and declared > self._limit:
            await self._refuse(
                scope, send, 413, "too_large", too_large(declared, self._limit), declared
            )
            return
        if declared is None:
            body = await self._read_unannounced_body(receive)
            if body is None:
                await self._refuse(scope, send, 413, "too_large", too_large(None, self._limit))
                return
            receive = body
        await self.app(scope, receive, send)

    async def _key_problem(self, header: str | None) -> tuple[str, str] | None:
        """Why this Authorization header does not let a request in, or None when it does."""
        key, reason = bearer_key(header)
        if key is None:
            return (reason or "no_key"), NO_AGENT_KEY if reason == "no_key" else NOT_A_BEARER
        if await self._keys.project_for_key(key) is None:
            return "wrong_key", BAD_AGENT_KEY
        return None

    async def _read_unannounced_body(self, receive: Receive) -> Receive | None:
        """For a request that did not say how long it is: read it, up to the limit, and give back
        a `receive` that replays it. None if it is longer than the limit."""
        held: deque[Message] = deque()
        size = 0
        while True:
            message = await receive()
            held.append(message)
            if message["type"] != "http.request":
                break
            size += len(message.get("body", b""))
            if size > self._limit:
                return None
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            return held.popleft() if held else await receive()

        return replay

    async def _refuse(
        self,
        scope: Scope,
        send: Send,
        status: int,
        reason: str,
        message: str,
        declared_bytes: int | None = None,
    ) -> None:
        client = scope.get("client")
        event(
            log,
            logging.WARNING,
            "request_refused",
            reason=reason,
            status=status,
            method=scope["method"],
            path=scope["path"],
            client=client[0] if client else None,
            declared_bytes=declared_bytes,
        )
        # A JSON-RPC error, so that an MCP client shows the message itself and not just a status.
        body = json.dumps(
            {"jsonrpc": "2.0", "id": None, "error": {"code": _CODES[status], "message": message}}
        ).encode()
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        if status == 401:
            headers.append((b"www-authenticate", b'Bearer realm="vrum"'))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def _declared_size(headers: Headers) -> int | None:
    value: Any = headers.get("content-length")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
