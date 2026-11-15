"""The MCP server: its tools, its health check, and how it is wired to the database."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from importlib.metadata import version
from typing import Annotated

import psycopg
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from vrum.claims import DEFAULT_LEASE_SECONDS, Claim, Claims
from vrum.common import parse_day
from vrum.config import Settings
from vrum.db import Database
from vrum.decisions import Decision, DecisionPage, Decisions
from vrum.graph import EventPage, Graph, Node, NodePage
from vrum.links import Link, LinkPage, Links
from vrum.memories import Memories, Memory, MemoryChange, MemoryPage
from vrum.recall import Recall, Recaller
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
_NodeIds = Annotated[
    list[int] | None, Field(description="Ids of the epics, stories or tasks it affects.")
]
_RuleIds = Annotated[list[int] | None, Field(description="Ids of the rules it affects.")]
_ConcernedNodeIds = Annotated[
    list[int] | None, Field(description="Ids of the epics, stories or tasks it concerns.")
]
_ConcernedRuleIds = Annotated[list[int] | None, Field(description="Ids of the rules it concerns.")]
_ConcernedDecisionIds = Annotated[
    list[int] | None, Field(description="Ids of the decisions it concerns.")
]
_EvidenceFactIds = Annotated[
    list[int] | None,
    Field(description="Ids of the active facts it rests on. Only facts, never other beliefs."),
]
_EvidenceDecisionIds = Annotated[
    list[int] | None, Field(description="Ids of the active decisions it rests on.")
]
_SaidBy = Annotated[
    str | None,
    Field(
        description="The person who changed their mind. Needed, with quote, to change a belief a "
        "person has confirmed. If you are passing on what a person told you, give their name here "
        "and your own in actor."
    ),
]
_Quote = Annotated[
    str | None,
    Field(description="What that person said, or where they said it. Needed with said_by."),
]
_HappenedOn = Annotated[
    str | None,
    Field(description="The day it was true or happened, as YYYY-MM-DD, if known."),
]
_DecidedOn = Annotated[
    date | None,
    Field(description="The day it was decided, as YYYY-MM-DD. Leave empty for today."),
]
_StartOn = Annotated[
    str | None,
    Field(description="The day work is planned to start, as YYYY-MM-DD. Never after target_on."),
]
_TargetOn = Annotated[
    str | None,
    Field(description="The day it is planned to be done by, as YYYY-MM-DD."),
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
    decisions = Decisions(database)
    memories = Memories(database)
    recaller = Recaller(database)

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
        start_on: _StartOn = None,
        target_on: _TargetOn = None,
    ) -> Node:
        """Create an epic, a story or a task.

        Work is an exact hierarchy: an epic has no parent, a story belongs to one epic, and a task
        belongs to one story. Any other shape is refused. A new node starts with status new and
        version 1. Dates are optional; a start date after the target date is refused.
        """
        with _database_errors():
            return await graph.create_node(
                actor=actor,
                kind=kind,
                title=title,
                parent_id=parent_id,
                body=body,
                priority=priority,
                start_on=parse_day("start_on", start_on),
                target_on=parse_day("target_on", target_on),
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
        start_from: Annotated[
            str | None,
            Field(description="Only nodes whose start_on is on or after this day (YYYY-MM-DD)."),
        ] = None,
        start_to: Annotated[
            str | None,
            Field(description="Only nodes whose start_on is on or before this day (YYYY-MM-DD)."),
        ] = None,
        target_from: Annotated[
            str | None,
            Field(description="Only nodes whose target_on is on or after this day (YYYY-MM-DD)."),
        ] = None,
        target_to: Annotated[
            str | None,
            Field(description="Only nodes whose target_on is on or before this day (YYYY-MM-DD)."),
        ] = None,
        overdue: Annotated[
            bool | None,
            Field(
                description="True: only nodes whose target date has passed and are not done or "
                "dropped. False: only the others."
            ),
        ] = None,
        limit: _Limit = 50,
        after_id: _After = None,
    ) -> NodePage:
        """List nodes, oldest first, a page at a time. Every filter is optional and they combine.

        status can also be ready: a node nobody has started whose prerequisites are all done. The
        date filters look at each node's own dates, so a node without a date never matches one.
        """
        with _database_errors():
            return await graph.list_nodes(
                kind=kind,
                parent_id=parent_id,
                status=status,
                start_from=parse_day("start_from", start_from),
                start_to=parse_day("start_to", start_to),
                target_from=parse_day("target_from", target_from),
                target_to=parse_day("target_to", target_to),
                overdue=overdue,
                limit=limit,
                after_id=after_id,
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
        start_on: _StartOn = None,
        target_on: _TargetOn = None,
        clear_start_on: Annotated[
            bool, Field(description="True to remove the start date. Do not also give start_on.")
        ] = False,
        clear_target_on: Annotated[
            bool, Field(description="True to remove the target date. Do not also give target_on.")
        ] = False,
    ) -> Node:
        """Change a node's title, body, status, priority or dates. Leave out what should stay.

        You must send the version you last read. If someone else changed the node since, the change
        is refused so nothing is overwritten silently: read the node again and repeat the change.
        Whether a node is ready is worked out by the server, so you cannot set it: new here means
        "not started", and the node shows as ready when everything it needs first is done. A date
        is removed only by asking for it (clear_start_on, clear_target_on), and a start date after
        the target date is refused.
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
                start_on=parse_day("start_on", start_on),
                target_on=parse_day("target_on", target_on),
                clear_start_on=clear_start_on,
                clear_target_on=clear_target_on,
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

    @mcp.tool()
    async def record_decision(
        actor: _Actor,
        title: str,
        text: Annotated[str, Field(description="What was decided.")],
        reason: Annotated[str, Field(description="Why it was decided.")],
        decided_by: Annotated[
            str,
            Field(
                description="The name of the person who made the decision. This is not you: "
                "your own name goes in actor."
            ),
        ],
        kind: Annotated[
            str,
            Field(
                description="decision: something decided about this work. principle: a standing "
                "guideline that applies everywhere."
            ),
        ] = "decision",
        decided_on: _DecidedOn = None,
        node_ids: _NodeIds = None,
        rule_ids: _RuleIds = None,
    ) -> Decision:
        """Record a decision or a principle, and the epics, stories, tasks and rules it affects.

        Decisions are a record and are never edited. To change one, supersede it. Link a decision
        to what it affects so that recall finds it for those items. A principle applies
        everywhere, so every recall also returns every active principle.
        """
        with _database_errors():
            return await decisions.record_decision(
                actor=actor,
                kind=kind,
                title=title,
                text=text,
                reason=reason,
                decided_by=decided_by,
                decided_on=decided_on,
                node_ids=node_ids,
                rule_ids=rule_ids,
            )

    @mcp.tool()
    async def get_decision(decision_id: int) -> Decision:
        """Read one decision, with its status, what replaced it or what it replaced, and the nodes
        and rules it is linked to."""
        with _database_errors():
            return await decisions.get_decision(decision_id)

    @mcp.tool()
    async def list_decisions(
        kind: Annotated[str | None, Field(description="Only decision or principle.")] = None,
        status: Annotated[str | None, Field(description="Only active or superseded.")] = None,
        limit: _Limit = 50,
        after_id: _After = None,
    ) -> DecisionPage:
        """List decisions and principles, oldest first, a page at a time. Every filter is optional.
        For the principles in force, ask for kind=principle and status=active."""
        with _database_errors():
            return await decisions.list_decisions(
                kind=kind, status=status, limit=limit, after_id=after_id
            )

    @mcp.tool()
    async def link_decision(
        actor: _Actor, decision_id: int, node_ids: _NodeIds = None, rule_ids: _RuleIds = None
    ) -> Decision:
        """Say that an active decision also affects more nodes or rules. Nothing is changed if any
        id is wrong or already linked. A superseded decision keeps the links it had."""
        with _database_errors():
            return await decisions.link_decision(
                actor=actor, decision_id=decision_id, node_ids=node_ids, rule_ids=rule_ids
            )

    @mcp.tool()
    async def unlink_decision(
        actor: _Actor, decision_id: int, node_ids: _NodeIds = None, rule_ids: _RuleIds = None
    ) -> Decision:
        """Say that an active decision no longer affects some nodes or rules. Nothing is changed if
        any of them is not linked. A superseded decision keeps the links it had."""
        with _database_errors():
            return await decisions.unlink_decision(
                actor=actor, decision_id=decision_id, node_ids=node_ids, rule_ids=rule_ids
            )

    @mcp.tool()
    async def supersede_decision(
        actor: _Actor,
        decision_id: Annotated[int, Field(description="The active decision being replaced.")],
        title: str,
        text: Annotated[str, Field(description="What is decided now.")],
        reason: Annotated[str, Field(description="Why it changed.")],
        decided_by: Annotated[
            str, Field(description="The person who made the new decision. Not you: that is actor.")
        ],
        kind: Annotated[
            str | None,
            Field(description="decision or principle. Leave empty to keep the old one's kind."),
        ] = None,
        decided_on: _DecidedOn = None,
        keep_links: Annotated[
            bool,
            Field(
                description="True: the new decision affects what the old one did. False: it starts "
                "with only the ids you give."
            ),
        ] = True,
        node_ids: _NodeIds = None,
        rule_ids: _RuleIds = None,
    ) -> Decision:
        """Replace an active decision with a new one, in one step.

        The old decision is kept, marked superseded, and points to the new one, so nothing is lost
        or overwritten. Recall shows the old one as superseded, never as active. Returns the new
        decision. A decision that is already superseded cannot be superseded again: supersede its
        replacement instead.
        """
        with _database_errors():
            return await decisions.supersede_decision(
                actor=actor,
                decision_id=decision_id,
                kind=kind,
                title=title,
                text=text,
                reason=reason,
                decided_by=decided_by,
                decided_on=decided_on,
                keep_links=keep_links,
                node_ids=node_ids,
                rule_ids=rule_ids,
            )

    @mcp.tool()
    async def recall(
        node_id: Annotated[
            int | None, Field(description="The epic, story or task to recall what is known for.")
        ] = None,
        rule_id: Annotated[
            int | None, Field(description="Or the rule to recall what is known for.")
        ] = None,
        show: Annotated[
            str,
            Field(
                description="all, or one of active_decisions, active_principles, facts, beliefs, "
                "stale_beliefs, superseded_decisions or past_memories to page through just that "
                "section."
            ),
        ] = "all",
        limit: Annotated[int, Field(description="How many of each section, from 1 to 200.")] = 50,
        after_id: Annotated[
            int | None,
            Field(description="With show set to one section: pass its next_after_id."),
        ] = None,
    ) -> Recall:
        """Recall everything known that matters for one node or rule, in separate sections.

        Decisions are what people decided, and are authoritative. Principles apply everywhere.
        Facts say where they came from. Beliefs are conclusions drawn from evidence and are only
        suggestions: weigh them, do not obey them. Stale beliefs rest on evidence that has since
        changed, and past memories were replaced or found wrong; neither is in force. Every entry
        says why it is there. Only exact links are followed: nothing is inferred, and what is
        linked to the parent or children of this item is not included, so recall those too if
        they matter. Call this before starting work.
        """
        with _database_errors():
            return await recaller.recall(
                node_id=node_id, rule_id=rule_id, show=show, limit=limit, after_id=after_id
            )

    @mcp.tool()
    async def retain_fact(
        actor: _Actor,
        text: Annotated[str, Field(description="What is known, in plain words.")],
        source: Annotated[
            str,
            Field(
                description="Where it came from, in plain words: who said it, which document, "
                "what was observed. Never blank."
            ),
        ],
        kind: Annotated[
            str,
            Field(
                description="world: true about the project, its tools or its domain. experience: "
                "something that was done or observed."
            ),
        ] = "world",
        happened_on: _HappenedOn = None,
        node_ids: _ConcernedNodeIds = None,
        rule_ids: _ConcernedRuleIds = None,
        decision_ids: _ConcernedDecisionIds = None,
    ) -> Memory:
        """Record a fact, with where it came from and what it concerns.

        A fact is a record and is never edited. When it changes, replace it with supersede_memory;
        when it was wrong, retract it with retract_memory. A fact is not a decision. Link it to
        what it concerns so that recall finds it for those items.
        """
        with _database_errors():
            return await memories.retain_fact(
                actor=actor,
                text=text,
                source=source,
                kind=kind,
                happened_on=parse_day("happened_on", happened_on),
                node_ids=node_ids,
                rule_ids=rule_ids,
                decision_ids=decision_ids,
            )

    @mcp.tool()
    async def record_belief(
        actor: _Actor,
        text: Annotated[str, Field(description="The conclusion, in plain words.")],
        evidence_fact_ids: _EvidenceFactIds = None,
        evidence_decision_ids: _EvidenceDecisionIds = None,
        node_ids: _ConcernedNodeIds = None,
        rule_ids: _ConcernedRuleIds = None,
    ) -> Memory:
        """Record a belief: a conclusion drawn from evidence.

        Give at least one active fact or decision it rests on, and never another belief. A belief
        is only ever a suggestion: it is not a decision and changes nothing in the graph. It goes
        stale on its own when any of its evidence stops being active. It is never edited: replace
        it with supersede_memory or retract it with retract_memory.
        """
        with _database_errors():
            return await memories.record_belief(
                actor=actor,
                text=text,
                evidence_fact_ids=evidence_fact_ids,
                evidence_decision_ids=evidence_decision_ids,
                node_ids=node_ids,
                rule_ids=rule_ids,
            )

    @mcp.tool()
    async def supersede_memory(
        actor: _Actor,
        replaces: Annotated[
            list[int],
            Field(
                description="The ids of the active facts, or the active beliefs, that the new "
                "item replaces. Any number, all of one type."
            ),
        ],
        reason: Annotated[str, Field(description="Why they are being replaced.")],
        text: Annotated[str, Field(description="The new fact or belief.")],
        source: Annotated[
            str | None, Field(description="For a new fact: where it came from. Never blank.")
        ] = None,
        kind: Annotated[
            str | None,
            Field(
                description="For a new fact: world or experience. Leave empty to keep the old kind."
            ),
        ] = None,
        happened_on: _HappenedOn = None,
        evidence_fact_ids: _EvidenceFactIds = None,
        evidence_decision_ids: _EvidenceDecisionIds = None,
        keep_links: Annotated[
            bool,
            Field(
                description="True: the new item concerns what the old ones did. False: it starts "
                "with only the ids you give."
            ),
        ] = True,
        node_ids: _ConcernedNodeIds = None,
        rule_ids: _ConcernedRuleIds = None,
        decision_ids: _ConcernedDecisionIds = None,
        said_by: _SaidBy = None,
        quote: _Quote = None,
    ) -> MemoryChange:
        """Replace one or more facts, or one or more beliefs, with a newer one, in one step.

        Use this when something is no longer true, for example when a person shares a belief that
        contradicts older ones: list them all in replaces. The old ones are kept, marked
        superseded, and point to the new one. If any of them does not exist or is not active,
        nothing is changed. A new belief still needs its own evidence, and a new fact its source.
        The answer lists the beliefs that went stale because of the change and the decisions that
        were made from a belief you replaced; neither is changed. A belief a person has confirmed
        can be replaced only with said_by and quote.
        """
        with _database_errors():
            return await memories.supersede_memory(
                actor=actor,
                replaces=replaces,
                reason=reason,
                text=text,
                source=source,
                kind=kind,
                happened_on=parse_day("happened_on", happened_on),
                evidence_fact_ids=evidence_fact_ids,
                evidence_decision_ids=evidence_decision_ids,
                keep_links=keep_links,
                node_ids=node_ids,
                rule_ids=rule_ids,
                decision_ids=decision_ids,
                said_by=said_by,
                quote=quote,
            )

    @mcp.tool()
    async def retract_memory(
        actor: _Actor,
        memory_ids: Annotated[
            list[int], Field(description="The ids of the active facts or beliefs that are wrong.")
        ],
        reason: Annotated[str, Field(description="Why they are wrong.")],
        with_dependent_beliefs: Annotated[
            bool,
            Field(
                description="True: also retract every active belief that rests on a fact you are "
                "retracting, with the same reason. False: those beliefs are only shown as stale, "
                "because a belief with other evidence may still hold."
            ),
        ] = False,
        said_by: _SaidBy = None,
        quote: _Quote = None,
    ) -> MemoryChange:
        """Mark one or more facts or beliefs as wrong, in one step. Nothing is deleted.

        If any of them does not exist or is not active, nothing is changed. The answer lists the
        beliefs that went stale because of it and the decisions that were made from a belief you
        retracted; neither is changed. A belief a person has confirmed can be retracted only with
        said_by and quote.
        """
        with _database_errors():
            return await memories.retract_memory(
                actor=actor,
                memory_ids=memory_ids,
                reason=reason,
                with_dependent_beliefs=with_dependent_beliefs,
                said_by=said_by,
                quote=quote,
            )

    @mcp.tool()
    async def confirm_belief(
        actor: _Actor,
        memory_id: Annotated[int, Field(description="The active belief that was looked at.")],
        confirmed_by: Annotated[
            str,
            Field(
                description="The person who looked at the belief. Not you: your name goes in actor."
            ),
        ],
    ) -> Memory:
        """Record that a named person has looked at a belief. It stays a suggestion.

        A stale belief cannot be confirmed. A belief that is already confirmed stays as it is.
        """
        with _database_errors():
            return await memories.confirm_belief(
                actor=actor, memory_id=memory_id, confirmed_by=confirmed_by
            )

    @mcp.tool()
    async def promote_belief(
        actor: _Actor,
        memory_id: Annotated[int, Field(description="The active belief to turn into a decision.")],
        title: str,
        reason: Annotated[str, Field(description="Why the person decided this.")],
        decided_by: Annotated[
            str,
            Field(
                description="The person who made the decision. Not you: your name goes in actor."
            ),
        ],
        text: Annotated[
            str | None, Field(description="What is decided. Leave empty to use the belief's text.")
        ] = None,
        kind: Annotated[
            str, Field(description="decision, or principle for a standing guideline.")
        ] = "decision",
        decided_on: _DecidedOn = None,
    ) -> Decision:
        """Turn an active belief into a decision made by a named person.

        The new decision concerns what the belief concerned and keeps a link back to it. The belief
        itself is not changed. A stale belief cannot be promoted.
        """
        with _database_errors():
            return await memories.promote_belief(
                actor=actor,
                memory_id=memory_id,
                decided_by=decided_by,
                title=title,
                reason=reason,
                text=text,
                kind=kind,
                decided_on=decided_on,
            )

    @mcp.tool()
    async def link_memory(
        actor: _Actor,
        memory_id: int,
        node_ids: _ConcernedNodeIds = None,
        rule_ids: _ConcernedRuleIds = None,
        decision_ids: _ConcernedDecisionIds = None,
    ) -> Memory:
        """Say that an active fact or belief also concerns more nodes or rules (a fact also
        decisions). Nothing is changed if any id is wrong or already linked."""
        with _database_errors():
            return await memories.link_memory(
                actor=actor,
                memory_id=memory_id,
                node_ids=node_ids,
                rule_ids=rule_ids,
                decision_ids=decision_ids,
            )

    @mcp.tool()
    async def unlink_memory(
        actor: _Actor,
        memory_id: int,
        node_ids: _ConcernedNodeIds = None,
        rule_ids: _ConcernedRuleIds = None,
        decision_ids: _ConcernedDecisionIds = None,
    ) -> Memory:
        """Say that an active fact or belief no longer concerns some nodes, rules or decisions.
        Nothing is changed if any of them is not linked."""
        with _database_errors():
            return await memories.unlink_memory(
                actor=actor,
                memory_id=memory_id,
                node_ids=node_ids,
                rule_ids=rule_ids,
                decision_ids=decision_ids,
            )

    @mcp.tool()
    async def get_memory(memory_id: int) -> Memory:
        """Read one fact or belief: its source or evidence, its status, who recorded and confirmed
        it, what replaced it, and what it concerns."""
        with _database_errors():
            return await memories.get_memory(memory_id)

    @mcp.tool()
    async def list_memories(
        type: Annotated[str | None, Field(description="Only fact or belief.")] = None,
        kind: Annotated[
            str | None, Field(description="Only world or experience (facts have one).")
        ] = None,
        status: Annotated[
            str | None, Field(description="Only active, superseded or retracted.")
        ] = None,
        origin: Annotated[
            str | None, Field(description="Only stated, extracted or derived.")
        ] = None,
        stale: Annotated[
            bool | None,
            Field(description="True: only beliefs that are stale. False: only those that are not."),
        ] = None,
        node_id: Annotated[
            int | None, Field(description="Only what concerns exactly this node.")
        ] = None,
        rule_id: Annotated[
            int | None, Field(description="Only what concerns exactly this rule.")
        ] = None,
        within_node_id: Annotated[
            int | None,
            Field(
                description="Only what concerns this epic, story or task, or anything under it, "
                "or a rule in one of those stories. One walk over a whole area."
            ),
        ] = None,
        rests_on_memory_id: Annotated[
            int | None, Field(description="Only beliefs that rest on this fact.")
        ] = None,
        rests_on_decision_id: Annotated[
            int | None, Field(description="Only beliefs that rest on this decision.")
        ] = None,
        limit: _Limit = 50,
        after_id: _After = None,
    ) -> MemoryPage:
        """List facts and beliefs, oldest first, a page at a time. Every filter is optional.

        The answer says how many match in all, so you can walk every page and check that none was
        skipped. For one area of the project, use within_node_id.
        """
        with _database_errors():
            return await memories.list_memories(
                memory_type=type,
                kind=kind,
                status=status,
                origin=origin,
                stale=stale,
                node_id=node_id,
                rule_id=rule_id,
                within_node_id=within_node_id,
                rests_on_memory_id=rests_on_memory_id,
                rests_on_decision_id=rests_on_decision_id,
                limit=limit,
                after_id=after_id,
            )

    @mcp.tool()
    async def memory_history(
        memory_id: int, limit: _Limit = 50, after_id: _After = None
    ) -> EventPage:
        """Read everything that was ever done to one fact or belief, oldest first, a page at a
        time: who recorded, linked, confirmed, replaced or retracted it, and when. A person who
        changed their mind, and the agent that passed it on, are both in it."""
        with _database_errors():
            return await memories.memory_history(
                memory_id=memory_id, limit=limit, after_id=after_id
            )

    @mcp.tool()
    async def decision_history(
        decision_id: int, limit: _Limit = 50, after_id: _After = None
    ) -> EventPage:
        """Read everything that was ever done to one decision, oldest first, a page at a time: who
        recorded, linked, unlinked or superseded it, and when."""
        with _database_errors():
            return await decisions.decision_history(
                decision_id=decision_id, limit=limit, after_id=after_id
            )

    @mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def health(request: Request) -> Response:
        try:
            await database.ping()
        except psycopg.Error:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    return mcp
