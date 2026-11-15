"""How the scripts in this folder connect to a running Vrum: with the token, read from the
VRUM_TOKEN environment variable (the same one the server's .env holds)."""

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import TextContent

# So that the scripts run from a checkout whether or not the project is installed in the
# environment (an editable install is silently skipped by some Python setups).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vrum.exchange import FORMAT, FORMAT_VERSION, SECTIONS  # noqa: E402


@asynccontextmanager
async def connect(url: str) -> AsyncIterator[Client]:
    token = os.environ.get("VRUM_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "VRUM_TOKEN is not set. Every request to the server needs the token from its .env "
            "file: run `export VRUM_TOKEN=<the token>` first."
        )
    async with (
        create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}) as http,
        Client(streamable_http_client(url, http_client=http)) as client,
    ):
        yield client


def said(result: Any) -> str:
    """What a tool's answer says, as text."""
    return " ".join(block.text for block in result.content if isinstance(block, TextContent))


async def fetch_export(client: Client) -> dict[str, Any]:
    """Every page of export_graph, put together as one file. It stops if the graph changed while it
    was being read (what was read would not fit together), saying so."""
    document: dict[str, Any] = {"format": FORMAT, "version": FORMAT_VERSION, "counts": None}
    for section in SECTIONS:
        document[section] = []
    after: str | None = None
    while True:
        result = await client.call_tool("export_graph", {"after": after, "limit": 200})
        if result.is_error or result.structured_content is None:
            raise SystemExit(f"The server refused to export: {said(result)}")
        page = result.structured_content
        if page["counts"] is not None:
            document["counts"] = page["counts"]
        if page["section"] is not None:
            document[page["section"]].extend(page["items"])
        after = page["next_after"]
        if after is None:
            break
    for section in SECTIONS:
        if len(document[section]) != document["counts"][section]:
            raise SystemExit(
                f"The graph changed while it was being read ({section}: "
                f"{document['counts'][section]} at the start, {len(document[section])} read). "
                "Run it again when nothing is writing."
            )
    return document
