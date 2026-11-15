"""Write the whole graph of a running Vrum to one JSON file (every item, with its ids, dates,
origins and history), to keep or to load into an empty server with import_graph.py.

    VRUM_TOKEN=<the token in .env> uv run python scripts/export_graph.py \\
        http://127.0.0.1:8000/mcp vrum-export.json

The file holds everything in the graph, so it is written readable only by you."""

import asyncio
import json
import os
import sys
from pathlib import Path

from vrum_client import connect, fetch_export


async def main(url: str, target: Path) -> None:
    async with connect(url) as client:
        document = await fetch_export(client)
    os.umask(0o077)
    partial = target.with_name(target.name + ".partial")
    partial.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    partial.replace(target)
    counts = ", ".join(f"{count} {name}" for name, count in document["counts"].items())
    print(f"Wrote {target}: {counts}.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: export_graph.py <server url, ending in /mcp> <file to write>")
    asyncio.run(main(sys.argv[1], Path(sys.argv[2])))
