"""The MCP server: its tools, its health check, and how it is wired to the database."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from importlib.metadata import version
from typing import Annotated

import psycopg
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from vrum.config import Settings
from vrum.db import Database
from vrum.graph import EventPage, Graph, Node, NodePage

_Actor = Annotated[str, Field(description="Your name: the person or agent making this change.")]
_Version = Annotated[
    int, Field(description="The version you last read (from get_node or the last change).")
]
_Limit = Annotated[int, Field(description="How many items to return, from 1 to 200.")]
_After = Annotated[
    int | None,
    Field(description="Leave empty for the first page; pass next_after_id for the next."),
]


class ServerStatus(BaseModel):
    vrum_version: str = Field(description="The version of this server.")
    postgres_version: str = Field(description="The version of the PostgreSQL database.")
    pgvector_version: str = Field(description="The version of the pgvector extension.")


@contextmanager
def _database_errors() -> Iterator[None]:
    """Say so in plain words when the database cannot be reached, instead of a driver message."""
    try:
        yield
    except psycopg.OperationalError as error:
        raise ToolError(
            "The server is running but cannot reach its database. "
            "Check that the database container is healthy, then try again."
        ) from error


def create_server(settings: Settings) -> MCPServer[Database]:
    database = Database(settings.database_conninfo())
    graph = Graph(database)

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
        with _database_errors():
            status = await database.status()
        return ServerStatus(
            vrum_version=version("vrum"),
            postgres_version=status.postgres_version,
            pgvector_version=status.pgvector_version,
        )

    @mcp.tool()
    async def create_node(
        actor: _Actor,
        kind: Annotated[str, Field(description="epic, story or task.")],
        title: str,
        parent_id: Annotated[
            int | None,
            Field(
                description="Required for a story (the id of its epic) and for a task (the id of "
                "its story). An epic has no parent: leave it empty."
            ),
        ] = None,
        body: Annotated[str, Field(description="Plain text or Markdown.")] = "",
        priority: Annotated[str, Field(description="low, normal, high or urgent.")] = "normal",
    ) -> Node:
        """Create an epic, a story or a task.

        Work is an exact hierarchy: an epic has no parent, a story belongs to one epic, and a task
        belongs to one story. Any other shape is refused. A new node starts with status new and
        version 1.
        """
        with _database_errors():
            return await graph.create_node(
                actor=actor,
                kind=kind,
                title=title,
                parent_id=parent_id,
                body=body,
                priority=priority,
            )

    @mcp.tool()
    async def get_node(node_id: int) -> Node:
        """Read one epic, story or task, including the version you need to change it."""
        with _database_errors():
            return await graph.get_node(node_id)

    @mcp.tool()
    async def list_nodes(
        kind: Annotated[str | None, Field(description="Only epic, story or task.")] = None,
        parent_id: Annotated[
            int | None, Field(description="Only the direct children of this node.")
        ] = None,
        status: Annotated[
            str | None, Field(description="Only new, in_progress, blocked, done or dropped.")
        ] = None,
        limit: _Limit = 50,
        after_id: _After = None,
    ) -> NodePage:
        """List nodes, oldest first, a page at a time. Every filter is optional and they combine."""
        with _database_errors():
            return await graph.list_nodes(
                kind=kind, parent_id=parent_id, status=status, limit=limit, after_id=after_id
            )

    @mcp.tool()
    async def update_node(
        actor: _Actor,
        node_id: int,
        expected_version: _Version,
        title: str | None = None,
        body: str | None = None,
        status: Annotated[
            str | None, Field(description="new, in_progress, blocked, done or dropped.")
        ] = None,
        priority: Annotated[str | None, Field(description="low, normal, high or urgent.")] = None,
        parent_id: Annotated[
            int | None,
            Field(
                description="Do not use this to change the parent: a node has exactly one parent, "
                "and move_node changes it."
            ),
        ] = None,
    ) -> Node:
        """Change a node's title, body, status or priority. Leave out what should stay as it is.

        You must send the version you last read. If someone else changed the node since, the change
        is refused so nothing is overwritten silently: read the node again and repeat the change.
        Whether a node is ready is worked out by the server, so you cannot set it.
        """
        with _database_errors():
            return await graph.update_node(
                actor=actor,
                node_id=node_id,
                expected_version=expected_version,
                title=title,
                body=body,
                status=status,
                priority=priority,
                parent_id=parent_id,
            )

    @mcp.tool()
    async def move_node(
        actor: _Actor,
        node_id: int,
        expected_version: _Version,
        new_parent_id: Annotated[
            int, Field(description="The epic to move a story to, or the story to move a task to.")
        ],
    ) -> Node:
        """Move a story to another epic, or a task to another story, in one step.

        A node always has exactly one parent, so moving replaces the old parent. An epic cannot be
        moved. The move is recorded in the node's history.
        """
        with _database_errors():
            return await graph.move_node(
                actor=actor,
                node_id=node_id,
                expected_version=expected_version,
                new_parent_id=new_parent_id,
            )

    @mcp.tool()
    async def node_history(node_id: int, limit: _Limit = 50, after_id: _After = None) -> EventPage:
        """Read everything that was ever done to one node, oldest first, a page at a time.

        Each event says who did it, when, what kind of change it was, and the changed fields as they
        were and as they became.
        """
        with _database_errors():
            return await graph.node_history(node_id=node_id, limit=limit, after_id=after_id)

    @mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def health(request: Request) -> Response:
        try:
            await database.ping()
        except psycopg.Error:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    return mcp
