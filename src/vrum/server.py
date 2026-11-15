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

from vrum.claims import DEFAULT_LEASE_SECONDS, Claim, Claims
from vrum.config import Settings
from vrum.db import Database
from vrum.graph import EventPage, Graph, Node, NodePage
from vrum.links import Link, LinkPage, Links
from vrum.rules import CoverageReport, Rule, RulePage, Rules

_Actor = Annotated[str, Field(description="Your name: the person or agent making this change.")]
_Version = Annotated[
    int, Field(description="The version you last read (from get_node or the last change).")
]
_Lease = Annotated[
    int,
    Field(
        description="How long the claim lasts before it lapses on its own, in seconds, from 1 to "
        "86400 (24 hours). Renew it to keep it longer."
    ),
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
    links = Links(database)
    claims = Claims(database)
    rules = Rules(database)

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
            str | None,
            Field(description="Only new, ready, in_progress, blocked, done or dropped."),
        ] = None,
        limit: _Limit = 50,
        after_id: _After = None,
    ) -> NodePage:
        """List nodes, oldest first, a page at a time. Every filter is optional and they combine.

        status can also be ready: a node nobody has started whose prerequisites are all done.
        """
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
        Whether a node is ready is worked out by the server, so you cannot set it: new here means
        "not started", and the node shows as ready when everything it needs first is done.
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

    @mcp.tool()
    async def add_link(
        actor: _Actor,
        source_id: Annotated[int, Field(description="The node the link starts from.")],
        target_id: Annotated[int, Field(description="The node the link points to.")],
        link_type: Annotated[
            str,
            Field(
                description="needs_first: the source cannot start before the target is done. "
                "supersedes: the source replaces the target. duplicates: the two are the same "
                "work. relates_to: the two are connected."
            ),
        ],
    ) -> Link:
        """Link two nodes.

        needs_first is how order is expressed: while the target is not done, the source is not
        ready and cannot be claimed. A needs_first link that would make nodes wait on each other in
        a circle is refused. A node cannot be linked to itself. duplicates and relates_to work in
        both directions.
        """
        with _database_errors():
            return await links.add_link(
                actor=actor, source_id=source_id, target_id=target_id, link_type=link_type
            )

    @mcp.tool()
    async def remove_link(
        actor: _Actor,
        source_id: int,
        target_id: int,
        link_type: Annotated[
            str, Field(description="needs_first, supersedes, duplicates or relates_to.")
        ],
    ) -> Link:
        """Remove one link, for example when what it said no longer applies. The removal is
        recorded in the history of both nodes."""
        with _database_errors():
            return await links.remove_link(
                actor=actor, source_id=source_id, target_id=target_id, link_type=link_type
            )

    @mcp.tool()
    async def list_links(
        node_id: int,
        link_type: Annotated[
            str | None, Field(description="Only needs_first, supersedes, duplicates or relates_to.")
        ] = None,
        limit: _Limit = 50,
        after_id: _After = None,
    ) -> LinkPage:
        """List every link that starts or ends at a node, a page at a time.

        For a needs_first link, source_id is the node that waits and target_id is the node it
        waits for.
        """
        with _database_errors():
            return await links.list_links(
                node_id=node_id, link_type=link_type, limit=limit, after_id=after_id
            )

    @mcp.tool()
    async def ready_work(
        kind: Annotated[
            str | None,
            Field(description="Only epic, story or task. Leave empty for stories and tasks."),
        ] = None,
        limit: _Limit = 50,
        after_id: _After = None,
    ) -> NodePage:
        """List work that can be started now: not started, everything it needs first is done, and
        nobody holds a claim on it. Oldest first, a page at a time. Take one with claim_node."""
        with _database_errors():
            return await graph.ready_work(kind=kind, limit=limit, after_id=after_id)

    @mcp.tool()
    async def claim_node(
        actor: _Actor, node_id: int, lease_seconds: _Lease = DEFAULT_LEASE_SECONDS
    ) -> Claim:
        """Say you are working on a story or task, so that nobody else picks it up.

        Only one actor can hold a claim at a time, and exactly one wins if several ask together.
        The claim lapses on its own after lease_seconds, so work that is abandoned becomes free
        again; renew it with renew_claim to keep it. Work that is not ready, done, dropped or
        blocked cannot be claimed.
        """
        with _database_errors():
            return await claims.claim_node(
                actor=actor, node_id=node_id, lease_seconds=lease_seconds
            )

    @mcp.tool()
    async def renew_claim(
        actor: _Actor, node_id: int, lease_seconds: _Lease = DEFAULT_LEASE_SECONDS
    ) -> Claim:
        """Keep your claim: it now lasts lease_seconds from now. Only the holder can renew, and
        only while the claim has not lapsed."""
        with _database_errors():
            return await claims.renew_claim(
                actor=actor, node_id=node_id, lease_seconds=lease_seconds
            )

    @mcp.tool()
    async def release_claim(actor: _Actor, node_id: int) -> Claim:
        """Give up your claim so someone else can take the work. Only the holder can release."""
        with _database_errors():
            return await claims.release_claim(actor=actor, node_id=node_id)

    @mcp.tool()
    async def create_rule(
        actor: _Actor,
        title: Annotated[str, Field(description="The rule, stated briefly.")],
        body: Annotated[
            str, Field(description="Plain text or Markdown: the detail, or where it came from.")
        ] = "",
        status: Annotated[str, Field(description="held, settled or dropped.")] = "held",
        story_id: Annotated[
            int | None,
            Field(description="The story that owns the rule. Leave empty to place it later."),
        ] = None,
    ) -> Rule:
        """Record a rule (a requirement).

        A rule belongs to at most one story. One with no story is unplaced: the coverage report
        lists it until someone places it. Rules have their own ids, separate from node ids.
        """
        with _database_errors():
            return await rules.create_rule(
                actor=actor, title=title, body=body, status=status, story_id=story_id
            )

    @mcp.tool()
    async def get_rule(rule_id: int) -> Rule:
        """Read one rule, including the version you need to change it."""
        with _database_errors():
            return await rules.get_rule(rule_id)

    @mcp.tool()
    async def list_rules(
        story_id: Annotated[int | None, Field(description="Only the rules in this story.")] = None,
        unplaced: Annotated[
            bool, Field(description="Only the rules that are in no story yet.")
        ] = False,
        status: Annotated[str | None, Field(description="Only held, settled or dropped.")] = None,
        limit: _Limit = 50,
        after_id: _After = None,
    ) -> RulePage:
        """List rules, oldest first, a page at a time. Every filter is optional."""
        with _database_errors():
            return await rules.list_rules(
                story_id=story_id, unplaced=unplaced, status=status, limit=limit, after_id=after_id
            )

    @mcp.tool()
    async def update_rule(
        actor: _Actor,
        rule_id: int,
        expected_version: _Version,
        title: str | None = None,
        body: str | None = None,
        status: Annotated[str | None, Field(description="held, settled or dropped.")] = None,
    ) -> Rule:
        """Change a rule's title, body or status. Leave out what should stay as it is.

        You must send the version you last read, so that nothing is overwritten silently. To change
        which story a rule is in, use place_rule or move_rule.
        """
        with _database_errors():
            return await rules.update_rule(
                actor=actor,
                rule_id=rule_id,
                expected_version=expected_version,
                title=title,
                body=body,
                status=status,
            )

    @mcp.tool()
    async def place_rule(
        actor: _Actor,
        rule_id: int,
        expected_version: _Version,
        story_id: Annotated[int, Field(description="The story that will own the rule.")],
    ) -> Rule:
        """Put an unplaced rule in a story.

        A rule belongs to at most one story, so placing a rule that is already in a story is
        refused: use move_rule to take it to another one.
        """
        with _database_errors():
            return await rules.place_rule(
                actor=actor, rule_id=rule_id, expected_version=expected_version, story_id=story_id
            )

    @mcp.tool()
    async def move_rule(
        actor: _Actor,
        rule_id: int,
        expected_version: _Version,
        new_story_id: Annotated[int, Field(description="The story the rule moves to.")],
    ) -> Rule:
        """Move a rule from its story to another story, in one step.

        The rule is never in two stories, and never in none, along the way. The move is recorded
        in the history of the rule and of both stories.
        """
        with _database_errors():
            return await rules.move_rule(
                actor=actor,
                rule_id=rule_id,
                expected_version=expected_version,
                new_story_id=new_story_id,
            )

    @mcp.tool()
    async def rule_history(rule_id: int, limit: _Limit = 50, after_id: _After = None) -> EventPage:
        """Read everything that was ever done to one rule, oldest first, a page at a time: who did
        it, when, and the changed fields as they were and as they became."""
        with _database_errors():
            return await rules.rule_history(rule_id=rule_id, limit=limit, after_id=after_id)

    @mcp.tool()
    async def coverage_report(
        show: Annotated[
            str,
            Field(
                description="both, unplaced_rules or stories_without_rules. To page through one "
                "list, ask for just that one."
            ),
        ] = "both",
        limit: Annotated[
            int, Field(description="How many of each list to return, from 1 to 200.")
        ] = 50,
        unplaced_after_id: Annotated[
            int | None,
            Field(description="Leave empty for the first page of unplaced rules."),
        ] = None,
        stories_after_id: Annotated[
            int | None,
            Field(description="Leave empty for the first page of stories without rules."),
        ] = None,
    ) -> CoverageReport:
        """Show what is not matched up yet: the rules that are in no story, and the stories that
        have no rule. Dropped rules and dropped stories are left out. Each list is a page; the
        counts cover all of it. A list you did not ask for comes back empty."""
        with _database_errors():
            return await rules.coverage_report(
                show=show,
                limit=limit,
                unplaced_after_id=unplaced_after_id,
                stories_after_id=stories_after_id,
            )

    @mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def health(request: Request) -> Response:
        try:
            await database.ping()
        except psycopg.Error:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    return mcp
