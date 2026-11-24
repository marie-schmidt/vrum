"""Start the server: `python -m vrum`."""

import asyncio
import logging
import os
import sys

import psycopg
import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from vrum.access import OPEN_PATHS, AccessGuard
from vrum.board import open_paths as board_open_paths
from vrum.config import ConfigError, Settings
from vrum.db import SchemaOutOfDate, prepare_schema
from vrum.logs import configure_logging, event
from vrum.server import create_server


def build_app(settings: Settings) -> Starlette:
    """The web application: the MCP endpoint and the health check, behind the access guard."""
    # The server listens on every address inside its container, so the SDK's automatic
    # localhost-only check is off; allow only requests addressed to localhost explicitly.
    local_hosts = [
        f"localhost:{settings.port}",
        f"127.0.0.1:{settings.port}",
        f"[::1]:{settings.port}",
    ]
    security = TransportSecuritySettings(
        allowed_hosts=local_hosts,
        allowed_origins=[f"http://{host}" for host in local_hosts],
    )
    server = create_server(settings)
    app = server.streamable_http_app(
        transport_security=security,
        host=settings.host,
        max_request_body_size=settings.max_request_bytes,
    )
    app.add_middleware(
        AccessGuard,
        keys=server.accounts,
        max_request_bytes=settings.max_request_bytes,
        open_paths={**OPEN_PATHS, **board_open_paths()},
    )
    return app


def main() -> int:
    try:
        settings = Settings.from_env(os.environ)
    except ConfigError as error:
        print(f"vrum cannot start: {error}", file=sys.stderr)
        return 2

    try:
        asyncio.run(prepare_schema(settings.database_conninfo()))
    except SchemaOutOfDate as error:
        print(f"vrum cannot start: {error}", file=sys.stderr)
        return 2
    except psycopg.OperationalError as error:
        print(f"vrum cannot start: it cannot reach its database ({error}).", file=sys.stderr)
        return 1

    app = build_app(settings)
    configure_logging(settings.secrets())
    event(
        logging.getLogger("vrum"),
        logging.INFO,
        "server_started",
        host=settings.host,
        port=settings.port,
        max_request_bytes=settings.max_request_bytes,
        embedding_model=settings.embedding.model if settings.embedding else None,
        email=settings.mail is not None,
        sign_in=[one.id for one in settings.sso.providers()],
    )
    for name, words in settings.sso.warnings():
        event(logging.getLogger("vrum"), logging.WARNING, name, message=words)
    # The server writes its own log lines (see logs.py), so the web server's are off. An idle
    # connection is kept for a minute and a quarter, not for uvicorn's five seconds: an agent that
    # thinks for about that long between two calls would otherwise send its next request just as the
    # server closes the connection, and be told "Server disconnected without sending a response".
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
        timeout_keep_alive=75,
    )
    asyncio.run(uvicorn.Server(config).serve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
