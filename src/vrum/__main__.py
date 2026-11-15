"""Start the server: `python -m vrum`."""

import os
import sys

from mcp.server.transport_security import TransportSecuritySettings

from vrum.config import ConfigError, Settings
from vrum.server import create_server


def main() -> int:
    try:
        settings = Settings.from_env(os.environ)
    except ConfigError as error:
        print(f"vrum cannot start: {error}", file=sys.stderr)
        return 2

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
