"""Start the server: `python -m vrum`."""

import asyncio
import os
import sys

import psycopg
from mcp.server.transport_security import TransportSecuritySettings

from vrum.config import ConfigError, Settings
from vrum.db import SchemaOutOfDate, prepare_schema
from vrum.server import create_server


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
    create_server(settings).run(
        "streamable-http",
        host=settings.host,
        port=settings.port,
        transport_security=security,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
