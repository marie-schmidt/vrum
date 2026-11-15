"""How the scripts in this folder connect to a running Vrum: with the token, read from the
VRUM_TOKEN environment variable (the same one the server's .env holds)."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client


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
