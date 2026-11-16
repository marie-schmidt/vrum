"""The container health check: `python -m vrum.healthcheck` exits 0 when the server is healthy."""

import os
import sys
import urllib.request


def main() -> int:
    port = os.environ.get("VRUM_PORT", "8000")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            return 0 if response.status == 200 else 1
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
