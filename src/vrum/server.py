"""The MCP server: its tools, its health check, and how it is wired to the database."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

import psycopg
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from vrum.config import Settings
from vrum.db import Database


class ServerStatus(BaseModel):
    vrum_version: str = Field(description="The version of this server.")
    postgres_version: str = Field(description="The version of the PostgreSQL database.")
    pgvector_version: str = Field(description="The version of the pgvector extension.")


def create_server(settings: Settings) -> MCPServer[Database]:
    database = Database(settings.database_conninfo())

    @asynccontextmanager
    async def lifespan(server: MCPServer[Database]) -> AsyncIterator[Database]:
        await database.open()
        try:
            yield database
        finally:
            await database.close()

    mcp = MCPServer[Database]("vrum", version=version("vrum"), lifespan=lifespan)

    @mcp.tool()
    async def server_status() -> ServerStatus:
        """Check that this server is running and can reach its database.

        Returns the server version and the versions of PostgreSQL and the pgvector extension.
        Call this first when connecting, or when something seems wrong.
        """
        try:
            status = await database.status()
        except psycopg.Error as error:
            raise ToolError(
                "The server is running but cannot reach its database. "
                "Check that the database container is healthy, then try again."
            ) from error
        return ServerStatus(
            vrum_version=version("vrum"),
            postgres_version=status.postgres_version,
            pgvector_version=status.pgvector_version,
        )

    @mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def health(request: Request) -> Response:
        try:
            await database.ping()
        except psycopg.Error:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    return mcp
