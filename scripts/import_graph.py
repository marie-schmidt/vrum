"""Load a file written by export_graph.py into a running Vrum that is empty, keeping every number,
id, date, confirmation and event (an epic, story or task keeps its kind and number, so `story:12`
is `story:12` in the new project too). All of it is loaded or none of it. A project that already
holds anything refuses.

    VRUM_AGENT_KEY=<the agent key of the project> uv run python scripts/import_graph.py \\
        http://127.0.0.1:8000/mcp vrum-export.json

A file can be as big as one request may be: 4 MiB unless the server's VRUM_MAX_REQUEST_BYTES is
raised. To move a bigger graph, back it up and restore it (backup.sh and restore.sh)."""

import asyncio
import json
import sys
from pathlib import Path

from mcp.shared.exceptions import MCPError
from vrum_client import connect, said


async def main(url: str, source: Path) -> None:
    document = json.loads(source.read_text(encoding="utf-8"))
    async with connect(url) as client:
        try:
            result = await client.call_tool("import_graph", {"document": document})
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
