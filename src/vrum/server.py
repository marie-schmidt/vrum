"""The MCP server: its tools, its health check, and how it is wired to the database."""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import date
from importlib.metadata import version
from typing import Annotated, Any

import psycopg
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from vrum.board import add_board
from vrum.claims import DEFAULT_LEASE_SECONDS, Claim, Claims
from vrum.common import Refusal, clean_actor, parse_day
from vrum.config import DEFAULT_MIN_SIMILARITY, DEFAULT_OVERLAP_THRESHOLD, Settings
from vrum.db import Database
from vrum.decisions import Decision, DecisionPage, Decisions
from vrum.embeddings import EmbeddingUnavailable
from vrum.exchange import Exchange, ExportPage, ImportResult
from vrum.graph import EventPage, Graph, Node, NodeDetail, NodePage
from vrum.guard import GuardedServer
from vrum.indexing import Indexer, IndexReport
from vrum.links import Link, LinkPage, Links
from vrum.llm import LanguageModel
from vrum.logs import event
from vrum.memories import Memories, Memory, MemoryChange, MemoryPage
from vrum.recall import Recall, Recaller
from vrum.reflection import (
    ConsolidateResult,
    ContradictionResult,
    ExtractResult,
    Reflector,
    ReflectResult,
)
from vrum.rules import CoverageReport, Rule, RulePage, Rules
from vrum.search import OverlapPage, Searcher, SearchPage
from vrum.workflow import (
    DEFAULT_CONTEXT_CHARS,
    ChangesPage,
    CreatedNode,
    FinishResult,
    NextWork,
    NodeChange,
    PlanItem,
    PlanLink,
    PlanResult,
    PlanRule,
    UpdateNodesResult,
    Workflow,
)

log = logging.getLogger("vrum.server")

# What any agent is told when it connects: the working loop, in a few lines. A test checks that it
# names only tools that exist.
INSTRUCTIONS = """\
Vrum is a project graph. Epics hold stories, stories hold tasks, and rules, decisions, facts and \
beliefs are linked to them. A decision is what a named person decided and is authoritative. A \
belief is only a suggestion. Every write needs an actor: your own name.

The working loop:
1. `next_work` takes the most urgent free story or task and returns it with its rules, decisions, \
principles, facts, earlier handoff notes and similar items, all in one call. Keep the claim with \
`renew_claim` while you work.
2. Before you decide something that matters, `recall` what is known about the item. If you do not \
know where to look, use `search`.
3. Record what is decided as it happens with `record_decision` (name the person who decided in \
decided_by), and what you learn with `retain_fact`.
4. End with `finish_work`: say whether it is done, blocked, dropped or released, and leave a \
handoff note for whoever comes next. A blank note is refused.

Also: `create_plan` creates a whole plan at once or nothing, `update_nodes` changes many nodes, \
`changes_since` shows only what is new since an event, and `ready_work` lists what is free. \
`list_nodes` takes a filter text as its query argument, such as \
`kind:task status:ready -claimed OR is:overdue`. Lists \
leave out bodies unless you ask for detail. Refusals say what is wrong and what to do next. \
Anything holding a well-known secret format (an access token, a private key) is refused in every \
field and never stored: leave secrets out of what you write. \
If the server has a language model, `extract_facts`, `consolidate_memory`, `find_contradictions` \
and `reflect` use it on the memory; what they return is proposed or generated, and is not a \
record until you record it.
"""

_Detail = Annotated[
    bool,
    Field(
        description="True: include whole bodies. By default a list leaves them out and gives each "
        "item's length and the first characters instead, to keep the answer small."
    ),
]

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
    language_model: str | None = Field(
        description="The language model this server is set up to use for extract_facts, "
        "consolidate_memory, find_contradictions and reflect. Empty when there is none: those "
        "four tools then say so, and everything else works."
    )


@contextmanager
def _database_errors() -> Iterator[None]:
    """Say so in plain words when the database cannot be reached, or will not take what was sent,
    instead of a driver message."""
    try:
        yield
    except psycopg.errors.ProgramLimitExceeded as error:
        # PostgreSQL keeps what can be searched in a text in at most about 1 MB: a huge text of
        # mostly different words does not fit. Nothing was saved, as every write is one step.
        raise ToolError(
            "A text in this request is too long to be searched: PostgreSQL cannot index more "
            "than about 1 MB of different words in one item. Nothing was changed. Split the text "
            "into several items, or shorten it."
        ) from error
    except psycopg.OperationalError as error:
        if error.sqlstate is not None:
            raise ToolError(
                "The database refused this request and nothing was changed. Try again; if it "
                "keeps happening, look at the database container's log."
            ) from error
        raise ToolError(
            "The server is running but cannot reach its database. "
            "Check that the database container is healthy, then try again."
        ) from error


def create_server(settings: Settings) -> GuardedServer:
    database = Database(settings.database_conninfo())
    graph = Graph(database)
    links = Links(database)
    claims = Claims(database)
    rules = Rules(database)
    decisions = Decisions(database)
    memories = Memories(database)
    exchange = Exchange(database)
    indexer = Indexer(database, settings.embedding)
    embedding = settings.embedding
    searcher = Searcher(
        database,
        indexer,
        embedding.min_similarity if embedding else DEFAULT_MIN_SIMILARITY,
        embedding.overlap_threshold if embedding else DEFAULT_OVERLAP_THRESHOLD,
    )
    recaller = Recaller(database, searcher)
    workflow = Workflow(database, graph, searcher)
    reflector = Reflector(
        database,
        LanguageModel(settings.llm) if settings.llm else None,
        memories,
        decisions,
        searcher,
        recaller,
    )

    @asynccontextmanager
    async def lifespan(server: MCPServer[Database]) -> AsyncIterator[Database]:
        await database.open()
        # The reindex job keeps the semantic index up to date. It only runs when an embedding
        # service is set up.
        job = asyncio.create_task(indexer.run_forever()) if embedding else None
        try:
            yield database
        finally:
            if job is not None:
                job.cancel()
                with suppress(asyncio.CancelledError):
                    await job
            await database.close()

    mcp = GuardedServer(
        "vrum", version=version("vrum"), lifespan=lifespan, instructions=INSTRUCTIONS
    )

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
            language_model=settings.llm.model if settings.llm else None,
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
    ) -> CreatedNode:
        """Create an epic, a story or a task.

        Work is an exact hierarchy: an epic has no parent, a story belongs to one epic, and a task
        belongs to one story. Any other shape is refused. A new node starts with status new and
        version 1. Dates are optional; a start date after the target date is refused.

        For an epic or a story it also says which existing ones look like the same thing
        (similar). It is created anyway: decide yourself whether it is a duplicate.
        """
        with _database_errors():
            return await workflow.create_node(
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
    async def get_node(node_id: int) -> NodeDetail:
        """Read one epic, story or task in full, including the version you need to change it, how
        many handoff notes it has and the latest one."""
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
        detail: _Detail = False,
        limit: _Limit = 50,
        after_id: _After = None,
        query: Annotated[
            str | None,
            Field(
                description="A filter in words and field:value terms, such as: kind:task "
                "status:ready,in_progress -claimed priority:high OR kind:story. Words match "
                "part of a title or body. field:a,b means any of these; a leading - leaves "
                "matches out; terms side by side must all hold; OR joins alternatives. Fields: "
                "kind, status, priority, claimed (who holds it), parent, epic, story, id, start "
                "and target (a day, a range such as 2026-11-01..2026-12-31, or none), is "
                "(overdue, conflict, waiting, claimed, lapsed) and no (start, target, claimed, "
                "parent, body, waiting). claimed and waiting on their own find the items that "
                "have one, and -claimed those that have none. Combines with the other filters."
            ),
        ] = None,
    ) -> NodePage:
        """List nodes, oldest first, a page at a time. Every filter is optional and they combine.

        status can also be ready: a node nobody has started whose prerequisites are all done. The
        date filters look at each node's own dates, so a node without a date never matches one.
        Bodies are left out unless you ask for detail: see body_preview, or use get_node. For
        anything the other filters cannot say, use query: it is the same filter language the
        board's Table and Roadmap use.
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
                detail=detail,
                limit=limit,
                after_id=after_id,
                query=query,
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
        detail: _Detail = False,
        limit: _Limit = 50,
        after_id: _After = None,
    ) -> NodePage:
        """List work that can be started now: not started, everything it needs first is done, and
        nobody holds a claim on it. Oldest first, a page at a time. To be handed the most urgent
        piece with everything you need to know, use next_work; claim_node takes a specific one."""
        with _database_errors():
            return await graph.ready_work(kind=kind, detail=detail, limit=limit, after_id=after_id)

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
    async def release_claim(
        actor: _Actor,
        node_id: int,
        note: Annotated[
            str,
            Field(
                description="What the next person must know: how far you got, what is left, what "
                "you found. A blank note is refused."
            ),
        ],
    ) -> Claim:
        """Give up your claim so someone else can take the work, and leave a handoff note.

        Only the holder can release. Work that was in progress goes back to not started, so it is
        free again; use finish_work to mark it done, blocked or dropped instead.
        """
        with _database_errors():
            return await claims.release_claim(actor=actor, node_id=node_id, note=note)

    @mcp.tool()
    async def next_work(
        actor: _Actor,
        within_node_id: Annotated[
            int | None,
            Field(description="Only work inside this epic or story. Leave empty for anywhere."),
        ] = None,
        peek: Annotated[
            bool, Field(description="True: look at what you would get, without claiming it.")
        ] = False,
        lease_seconds: _Lease = DEFAULT_LEASE_SECONDS,
        context_chars: Annotated[
            int,
            Field(
                description="How much context to return at most, in characters, from 1000 to "
                "1000000. What does not fit is counted in left_out, never silently dropped."
            ),
        ] = DEFAULT_CONTEXT_CHARS,
    ) -> NextWork:
        """Take the most urgent free story or task, and get everything you need to do it.

        Free work is a story or task that nobody holds, that is not waiting for anything, and that
        is ready (or whose earlier holder let their claim run out). A story whose tasks are still
        open is worked through its tasks. Urgent comes first, then the earliest target date, then
        the oldest. It is claimed for you in the same step, and exactly one actor gets each item
        however many ask at once.

        The context holds its epic and story, the rules of its story, the decisions linked to it
        or its parents (and replaced ones, kept apart), every principle, facts and beliefs, notes
        left by earlier holders, what it waits for, what waits for it, and similar items that are
        only suggestions. When nothing is free this is not an error: found is false and message
        says how much work is held, waiting or blocked.
        """
        with _database_errors():
            return await workflow.next_work(
                actor=actor,
                within_node_id=within_node_id,
                peek=peek,
                lease_seconds=lease_seconds,
                context_chars=context_chars,
            )

    @mcp.tool()
    async def finish_work(
        actor: _Actor,
        node_id: int,
        outcome: Annotated[
            str,
            Field(
                description="done, blocked, dropped or released (back to not started, for someone "
                "else to take)."
            ),
        ],
        note: Annotated[
            str,
            Field(
                description="The handoff: what you did, what you found, what the next person must "
                "know. A blank note is refused."
            ),
        ],
    ) -> FinishResult:
        """End your work on a story or task: set where it stands and leave a handoff note.

        Only the holder of the claim can finish. The claim is released and the note is kept with
        the item, and shown to whoever works on it, or on what waited for it, next. unblocked
        lists the work that became ready because of this.
        """
        with _database_errors():
            return await workflow.finish_work(
                actor=actor, node_id=node_id, outcome=outcome, note=note
            )

    @mcp.tool()
    async def create_plan(
        actor: _Actor,
        items: Annotated[
            list[PlanItem],
            Field(
                description="The epics, stories and tasks to create. Each has a key (a name only "
                "this plan uses) so that parents, links and rules can refer to it."
            ),
        ],
        links: Annotated[
            list[PlanLink] | None,
            Field(description="Links between items, by key or by the id of an existing node."),
        ] = None,
        rules: Annotated[
            list[PlanRule] | None,
            Field(description="Rules to record, each in a story by key or by existing id."),
        ] = None,
    ) -> PlanResult:
        """Create a whole plan at once: epics, stories, tasks, links and rules, or nothing.

        It is all checked first. If anything is wrong, nothing is created and every problem is
        listed, each naming its item. There is no limit on how many items a plan has. For each
        new epic or story that looks like something that already exists, similar says so; it was
        created anyway.
        """
        with _database_errors():
            return await workflow.create_plan(
                actor=actor, items=items, links=links or [], rules=rules or []
            )

    @mcp.tool()
    async def update_nodes(
        actor: _Actor,
        changes: Annotated[
            list[NodeChange],
            Field(description="The changes. Each names its node and the version you last read."),
        ],
    ) -> UpdateNodesResult:
        """Change many nodes in one call. Each change is checked and applied on its own, like
        update_node, so one stale or refused change never stops the others. The result says, for
        every change, whether it was applied or why not."""
        with _database_errors():
            return await workflow.update_nodes(actor=actor, changes=changes)

    @mcp.tool()
    async def changes_since(
        after_event_id: Annotated[
            int,
            Field(description="Pass 0 for the beginning, then the latest_event_id you got last."),
        ] = 0,
        actor: Annotated[
            str | None, Field(description="Only changes made by this person or agent.")
        ] = None,
        node_id: Annotated[int | None, Field(description="Only changes to this node.")] = None,
        rule_id: Annotated[int | None, Field(description="Only changes to this rule.")] = None,
        decision_id: Annotated[
            int | None, Field(description="Only changes to this decision or principle.")
        ] = None,
        memory_id: Annotated[
            int | None, Field(description="Only changes to this fact or belief.")
        ] = None,
        limit: _Limit = 100,
    ) -> ChangesPage:
        """Show what changed after an event, oldest first, so you read only what is new.

        Keep latest_event_id and pass it next time (page with next_after_id until it is empty).
        Every event after the one you give is shown, in order, and none is skipped even when
        another change was still being written. Events are ordered by when their change began,
        which for two changes at the very same moment can differ from their ids. Give at most
        one of node_id, rule_id, decision_id and memory_id.
        """
        with _database_errors():
            return await workflow.changes_since(
                after_event_id=after_event_id,
                actor=actor,
                node_id=node_id,
                rule_id=rule_id,
                decision_id=decision_id,
                memory_id=memory_id,
                limit=limit,
            )

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
        detail: _Detail = False,
        limit: _Limit = 50,
        after_id: _After = None,
    ) -> RulePage:
        """List rules, oldest first, a page at a time. Every filter is optional. Bodies are left
        out unless you ask for detail: see body_preview, or use get_rule."""
        with _database_errors():
            return await rules.list_rules(
                story_id=story_id,
                unplaced=unplaced,
                status=status,
                detail=detail,
                limit=limit,
                after_id=after_id,
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
        detail: _Detail = False,
    ) -> CoverageReport:
        """Show what is not matched up yet: the rules that are in no story, and the stories that
        have no rule. Dropped rules and dropped stories are left out. Each list is a page; the
        counts cover all of it. A list you did not ask for comes back empty. Bodies are left out
        unless you ask for detail."""
        with _database_errors():
            return await rules.coverage_report(
                show=show,
                detail=detail,
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
        with_similar: Annotated[
            bool,
            Field(
                description="True: also return decisions, facts and beliefs that are alike in "
                "meaning but not linked, as suggestions in a section of their own."
            ),
        ] = False,
    ) -> Recall:
        """Recall everything known that matters for one node or rule, in separate sections.

        Decisions are what people decided, and are authoritative. Principles apply everywhere.
        Facts say where they came from. Beliefs are conclusions drawn from evidence and are only
        suggestions: weigh them, do not obey them. Stale beliefs rest on evidence that has since
        changed, and past memories were replaced or found wrong; neither is in force. Every entry
        says why it is there. Only exact links are followed: nothing is inferred, and what is
        linked to the parent or children of this item is not included, so recall those too if
        they matter. With with_similar, a last section adds what is alike in meaning but not
        linked, as suggestions. Call this before starting work.
        """
        with _database_errors():
            return await recaller.recall(
                node_id=node_id,
                rule_id=rule_id,
                show=show,
                limit=limit,
                after_id=after_id,
                with_similar=with_similar,
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

    @mcp.tool()
    async def search(
        query: Annotated[
            str,
            Field(
                description="What to look for. Words are matched as whole words, so every word "
                "must be in the text (plurals and endings are matched). Put a phrase in double "
                "quotes, write or between alternatives, and put - before a word to leave it out. "
                "Any characters are allowed."
            ),
        ],
        mode: Annotated[
            str,
            Field(
                description="both (the default: exact words and meaning, fused by rank), words "
                "(exact words only) or meaning (similar meaning only)."
            ),
        ] = "both",
        types: Annotated[
            list[str] | None,
            Field(
                description="Only these types: epic, story, task, rule, decision, principle, "
                "fact, belief."
            ),
        ] = None,
        statuses: Annotated[
            list[str] | None,
            Field(description="Only these statuses, such as ready, done, active or retracted."),
        ] = None,
        min_similarity: Annotated[
            float | None,
            Field(
                description="How alike in meaning, from 0 to 1, an item must be to match by "
                "meaning. Leave empty for the server's setting."
            ),
        ] = None,
        limit: Annotated[int, Field(description="How many results to return, from 1 to 200.")] = 20,
        offset: Annotated[
            int, Field(description="How many results to skip. Pass next_offset for the next page.")
        ] = 0,
    ) -> SearchPage:
        """Search epics, stories, tasks, rules, decisions, principles, facts and beliefs.

        Each result says how it matched (words, meaning or both), why, and shows a snippet with
        the matched words between « and ». Matches by meaning alone are suggestions, and a belief
        or a fact a model wrote is always labelled so. The answer counts the matches by type and
        by status. Exact words are found the moment something is saved, and keep working when the
        embedding service is down; the answer says when meaning could not be used. Tasks are
        found by exact words only.
        """
        with _database_errors():
            return await searcher.search(
                query=query,
                mode=mode,
                types=types,
                statuses=statuses,
                min_similarity=min_similarity,
                limit=limit,
                offset=offset,
            )

    @mcp.tool()
    async def similar_items(
        node_id: Annotated[int | None, Field(description="An epic, story or task.")] = None,
        rule_id: Annotated[int | None, Field(description="Or a rule.")] = None,
        decision_id: Annotated[int | None, Field(description="Or a decision or principle.")] = None,
        memory_id: Annotated[int | None, Field(description="Or a fact or belief.")] = None,
        text: Annotated[str | None, Field(description="Or a piece of text.")] = None,
        types: Annotated[
            list[str] | None,
            Field(description="Only these types, such as decision, fact or belief."),
        ] = None,
        min_similarity: Annotated[
            float | None,
            Field(description="How alike, from 0 to 1. Leave empty for the server's setting."),
        ] = None,
        limit: Annotated[int, Field(description="How many results to return, from 1 to 200.")] = 20,
        offset: Annotated[
            int, Field(description="How many results to skip. Pass next_offset for the next page.")
        ] = 0,
    ) -> SearchPage:
        """Find items that are alike in meaning to one item or one piece of text, nearest first.

        Give exactly one of node_id, rule_id, decision_id, memory_id or text. The item itself is
        left out. Every result is a suggestion. It needs the embedding service and the index; if
        either is not ready it says so and changes nothing.
        """
        with _database_errors():
            return await searcher.similar_items(
                node_id=node_id,
                rule_id=rule_id,
                decision_id=decision_id,
                memory_id=memory_id,
                text=text,
                types=types,
                min_similarity=min_similarity,
                limit=limit,
                offset=offset,
            )

    @mcp.tool()
    async def find_overlaps(
        item_type: Annotated[
            str, Field(description="story (the default), epic or rule: what to compare.")
        ] = "story",
        epic_ids: Annotated[
            list[int] | None,
            Field(
                description="Only pairs where at least one of the two is in one of these epics. "
                "Leave empty to look at everything."
            ),
        ] = None,
        min_similarity: Annotated[
            float | None,
            Field(
                description="How alike, from 0 to 1, a pair must be to be reported. Leave empty "
                "for the server's setting."
            ),
        ] = None,
        include_dropped: Annotated[
            bool, Field(description="True: also compare dropped stories, epics and rules.")
        ] = False,
        limit: Annotated[int, Field(description="How many pairs to return, from 1 to 200.")] = 20,
        offset: Annotated[
            int, Field(description="How many pairs to skip. Pass next_offset for the next page.")
        ] = 0,
    ) -> OverlapPage:
        """Find pairs of stories, epics or rules in different epics that look like the same thing.

        Pairs are listed most alike first, with their scores, and say which links already exist
        between them. This only reports: it never merges, links or changes anything. A person or
        an agent decides, for example by linking two stories as duplicates. It uses the index, so
        items that are new or changed and not yet indexed are not compared yet; the answer counts
        them.
        """
        with _database_errors():
            return await searcher.find_overlaps(
                item_type=item_type,
                epic_ids=epic_ids,
                min_similarity=min_similarity,
                include_dropped=include_dropped,
                limit=limit,
                offset=offset,
            )

    @mcp.tool()
    async def reindex() -> IndexReport:
        """Bring the semantic index up to date now: build it if it does not exist, then embed
        every epic, story, rule, decision, principle, fact and belief that is new or changed.

        The server also does this by itself within seconds, so you rarely need it. This is the only
        thing that writes to the index, which holds nothing that is not in the graph and can be
        dropped and rebuilt at any time. If the embedding service cannot be reached, nothing is
        changed and exact-words search still works.
        """
        with _database_errors():
            try:
                return await indexer.reindex()
            except EmbeddingUnavailable as error:
                raise Refusal(
                    f"{error} Nothing was indexed. Exact-words search still works; try again "
                    "when the service is back."
                ) from error

    @mcp.tool()
    async def export_graph(
        after: Annotated[
            str | None,
            Field(description="Leave empty for the first page; pass next_after for the next."),
        ] = None,
        limit: _Limit = 200,
    ) -> ExportPage:
        """Read the whole graph as data, one page at a time: every epic, story and task (with
        dates), link, rule, decision and principle (with what each affects), fact and belief (with
        origin, status, what each concerns and, for a belief, its evidence), and the event log.

        Each page holds items of one list (nodes, links, rules, decisions, memories or events), in
        id order. Keep calling with next_after until it is empty; the first page also says how
        many items each list holds in all, to check you got them all. Dates and times are text.
        Not included: claims (leases that run out) and what is worked out from the rest (what is
        ready, what is stale, the search and semantic indexes).

        The file this makes can be loaded into an empty server with import_graph, and turned into
        a Markdown copy to read in Obsidian or keep in git. scripts/export_graph.py and
        scripts/export_markdown.py do the paging for you.
        """
        with _database_errors():
            return await exchange.export_page(after=after, limit=limit)

    @mcp.tool()
    async def import_graph(
        actor: _Actor,
        document: Annotated[
            dict[str, Any],
            Field(
                description="The whole export, as one object with format, version, counts and the "
                "lists nodes, links, rules, decisions, memories and events, as export_graph "
                "pages give them."
            ),
        ],
    ) -> ImportResult:
        """Load a whole export into this server, which must be empty. Every id, date, status,
        origin and event is kept, so what is exported afterwards is identical to the file.

        It is all or nothing: the file is checked first and every problem in it is listed at once,
        and the database checks everything again as it is loaded. A server that already holds
        anything is refused, because the ids in the file would clash with what is there: load it
        into a fresh one, or restore a backup. A file can be as big as one request may be (see
        VRUM_MAX_REQUEST_BYTES). scripts/import_graph.py sends a file for you. If an embedding
        service is set up, the server indexes the imported items by itself afterwards.
        """
        actor = clean_actor(actor)
        with _database_errors():
            result = await exchange.import_document(document)
        event(log, logging.INFO, "import_finished", actor=actor[:100], counts=result.counts)
        return result

    @mcp.tool()
    async def extract_facts(
        actor: _Actor,
        text: Annotated[
            str,
            Field(description="The text to find facts in: an item's body, a handoff note, notes."),
        ],
        store: Annotated[
            bool,
            Field(
                description="True: keep the facts that pass the checks, as facts with origin "
                "extracted. False (the default): only show them."
            ),
        ] = False,
        node_ids: Annotated[
            list[int] | None,
            Field(description="With store: the epics, stories or tasks the stored facts concern."),
        ] = None,
        rule_ids: Annotated[
            list[int] | None, Field(description="With store: the rules they concern.")
        ] = None,
        decision_ids: Annotated[
            list[int] | None, Field(description="With store: the decisions they concern.")
        ] = None,
    ) -> ExtractResult:
        """Ask the language model for the facts in a text. Needs a language model on the server.

        Each proposed fact comes with the exact passage it was taken from, and the server drops
        any whose passage is not in the text. By default nothing is stored. With store the facts
        are kept with origin extracted, the passage as their source and you as the one who
        recorded them, so they are never mistaken for facts a person wrote. Text that contains a
        well-known secret format is not sent.
        """
        with _database_errors():
            return await reflector.extract_facts(
                actor=actor,
                text=text,
                store=store,
                node_ids=node_ids,
                rule_ids=rule_ids,
                decision_ids=decision_ids,
            )

    @mcp.tool()
    async def consolidate_memory(
        actor: _Actor,
        fact_ids: Annotated[
            list[int] | None, Field(description="Draw conclusions from these active facts.")
        ] = None,
        node_id: Annotated[
            int | None,
            Field(description="Or from the active facts of this item and everything below it."),
        ] = None,
        query: Annotated[
            str | None, Field(description="Or from the active facts a search for this finds.")
        ] = None,
        revise_belief_id: Annotated[
            int | None,
            Field(
                description="Or: a stale belief to revise from the evidence that is still valid. "
                "The revised belief replaces it."
            ),
        ] = None,
        max_facts: Annotated[
            int, Field(description="The most facts to show the model, from 1 to 200.")
        ] = 50,
        said_by: Annotated[
            str | None,
            Field(
                description="Revising a belief a person confirmed: the person who changed their "
                "mind. Needed, with quote."
            ),
        ] = None,
        quote: Annotated[
            str | None, Field(description="What that person said, or where. Needed with said_by.")
        ] = None,
    ) -> ConsolidateResult:
        """Ask the language model for the conclusions some facts support. Needs a language model.

        Give exactly one of fact_ids, node_id, query or revise_belief_id. Each belief the model
        proposes must name the facts it rests on, and one that names anything it was not given is
        dropped. The rest are recorded as beliefs with origin derived: suggestions, never
        decisions. A belief with the same words as an active one is not recorded again. With
        revise_belief_id, a belief that went stale is replaced by a revised one drawn up from
        the evidence that is still valid; it is again only a suggestion.
        """
        with _database_errors():
            return await reflector.consolidate_memory(
                actor=actor,
                fact_ids=fact_ids,
                node_id=node_id,
                query=query,
                revise_belief_id=revise_belief_id,
                max_facts=max_facts,
                said_by=said_by,
                quote=quote,
            )

    @mcp.tool()
    async def find_contradictions(
        actor: _Actor,
        statement: Annotated[
            str | None, Field(description="A new statement, in words, to check against beliefs.")
        ] = None,
        memory_id: Annotated[
            int | None,
            Field(description="Or the id of a fact or belief that is already recorded."),
        ] = None,
        scope: Annotated[
            str,
            Field(
                description="related (the default): the beliefs it may clash with, in one call. "
                "all: every active belief, one batch per call."
            ),
        ] = "related",
        batch: Annotated[
            int, Field(description="How many beliefs to show the model per call, from 1 to 200.")
        ] = 25,
        after_id: Annotated[
            int | None,
            Field(description="Scope all: leave empty for the first batch, then next_after_id."),
        ] = None,
    ) -> ContradictionResult:
        """Ask the language model which active beliefs a new statement contradicts. Needs a
        language model.

        It returns proposals only and changes nothing. Each names a belief, why, and the clashing
        words of the belief and of the statement, which the server checked are really there.
        Applying one is your call: replace the belief with supersede_memory or retract it with
        retract_memory, giving said_by and quote for a belief a person confirmed.

        With scope related it looks at the beliefs on the same items, with shared evidence, or
        found by words or meaning, and says how many that was out of all active beliefs. With
        scope all it goes through every active belief in id order, a batch per call, and says how
        many it has looked at and how many remain: call again with next_after_id until none do.
        """
        with _database_errors():
            return await reflector.find_contradictions(
                actor=actor,
                statement=statement,
                memory_id=memory_id,
                scope=scope,
                batch=batch,
                after_id=after_id,
            )

    @mcp.tool()
    async def reflect(
        actor: _Actor,
        question: Annotated[str, Field(description="The question, in words.")],
        node_id: Annotated[
            int | None,
            Field(description="The item the question is about, so what is linked to it is used."),
        ] = None,
        rule_id: Annotated[
            int | None, Field(description="Or the rule the question is about.")
        ] = None,
        limit: Annotated[
            int, Field(description="How many items of memory to show the model, from 1 to 200.")
        ] = 12,
    ) -> ReflectResult:
        """Ask a question and get an answer in words, from the memory only. Needs a language
        model.

        It finds decisions, principles, facts and beliefs that match the question (what is linked
        to the item you name, then what matches by words and by meaning, as search does) and asks
        the model to answer using only those. The answer lists the items it relied on, and one
        that cites anything that was not found is refused. The answer is labelled generated and is
        not stored: to keep a conclusion, record it yourself as a belief with its evidence.
        """
        with _database_errors():
            return await reflector.reflect(
                actor=actor, question=question, node_id=node_id, rule_id=rule_id, limit=limit
            )

    @mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def health(request: Request) -> Response:
        try:
            await database.ping()
        except psycopg.Error:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    add_board(mcp, settings=settings, searcher=searcher)
    return mcp
