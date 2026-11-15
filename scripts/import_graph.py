"""Load a file written by export_graph.py into a running Vrum that is empty, keeping every id, date,
origin and event. All of it is loaded or none of it. A server that already holds anything refuses.

    VRUM_TOKEN=<the token in .env> uv run python scripts/import_graph.py \\
        http://127.0.0.1:8000/mcp vrum-export.json

A file can be as big as one request may be: 4 MiB unless the server's VRUM_MAX_REQUEST_BYTES is
raised. To move a bigger graph, back it up and restore it (backup.sh and restore.sh)."""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp.shared.exceptions import MCPError
from vrum_client import connect, said


async def main(url: str, source: Path) -> None:
    document = json.loads(source.read_text(encoding="utf-8"))
    actor = os.environ.get("USER") or "import_graph.py"
    async with connect(url) as client:
        try:
            result = await client.call_tool("import_graph", {"actor": actor, "document": document})
        except MCPError as error:
            raise SystemExit(error.message) from None
    if result.is_error:
        raise SystemExit(said(result))
    print(
        said(result) if result.structured_content is None else result.structured_content["message"]
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: import_graph.py <server url, ending in /mcp> <file to load>")
    asyncio.run(main(sys.argv[1], Path(sys.argv[2])))
