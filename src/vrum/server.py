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
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from vrum.accounts import Accounts
from vrum.board import add_board
from vrum.claims import DEFAULT_LEASE_SECONDS, Claim, Claims, WhoIsWorking
from vrum.common import (
    Ref,
    Refusal,
    check_choice,
    clean_actor,
    parse_day,
    parse_node_ref,
    parse_node_refs,
)
from vrum.config import DEFAULT_MIN_SIMILARITY, DEFAULT_OVERLAP_THRESHOLD, Settings
from vrum.db import Database, current_project
from vrum.decisions import Decision, DecisionPage, Decisions
from vrum.embeddings import EmbeddingUnavailable
from vrum.exchange import Exchange, ExportPage, ImportResult
from vrum.facts import Fact, FactChange, FactPage, Facts
from vrum.graph import EventPage, Graph, Node, NodeDetail, NodePage
from vrum.guard import GuardedServer
from vrum.indexing import Indexer, IndexReport
from vrum.links import Link, LinkPage, Links
from vrum.logs import event
from vrum.recall import Recall, Recaller
from vrum.references import MentionPage, References
from vrum.rules import CoverageReport, Rule, RulePage, Rules
from vrum.search import OverlapPage, Searcher, SearchPage
from vrum.status import ServerStatus, Status
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


class RecordConfirmation(BaseModel):
    """The answer of set_confirmation: the record that changed, whichever kind it is."""

    kind: str = Field(description="fact or decision.")
    fact: Fact | None = Field(default=None, description="The fact, when it was a fact.")
    decision: Decision | None = Field(
        default=None, description="The decision, when it was a decision."
    )


# What any agent is told when it connects: the working loop, in a few lines. A test checks that it
# names only tools that exist.
INSTRUCTIONS = """\
Vrum is a project graph. Epics hold stories, stories hold tasks, and requirements, decisions and \
facts are linked to them. An epic, story or task is named by its kind and its number within that \
kind: `epic:3`, `story:12`, `task:40` (each kind is numbered from 1 on its own, so epic:3, \
story:3 and task:3 are three items). Every tool that names one takes and returns that reference, \
never a bare number. A decision is what a named person decided; a fact is a statement with its \
source. Each is either unconfirmed or confirmed by a named person, and either in force or \
superseded or withdrawn: read both before you rely on it. Every write needs an actor: your own \
name.

The working loop:
1. `next_work` takes the most urgent free story or task and returns it with its requirements, \
decisions (project-wide ones included), facts, earlier handoff notes and similar items, all in \
one call. Keep the claim with `renew_claim` while you work.
2. Before you decide something that matters, `recall` what is known about the item. If you do not \
know where to look, use `search`.
3. Record what is decided as it happens with `record_decision` (name the person who decided in \
decided_by), and what you learn with `retain_fact`. What you record starts unconfirmed: only a \
named person's review makes it confirmed (`set_confirmation`).
4. End with `finish_work`: say whether it is done, blocked, dropped or released, and leave a \
handoff note for whoever comes next. A blank note is refused.

Also: `create_plan` creates a whole plan at once or nothing (its items are named by keys that are \
not references, and it refers to existing items by reference), `update_nodes` changes many nodes, \
`changes_since` shows only what is new since an event, and `ready_work` lists what is free. \
`list_nodes` takes a filter text as its query argument, such as \
`kind:task status:ready -claimed OR is:overdue` or `epic:3` for epic 3 and everything under it. \
`[[story:12]]` in any text refers to an item (also `[[epic:3]]`, `[[task:40]]`, `[[rule:3]]`, \
`[[decision:7]]`, `[[fact:5]]`); `find_mentions` lists who refers to one. Lists leave out bodies \
unless you ask for detail. Refusals say what is wrong and what to do next. Anything holding a \
well-known secret format is refused in every field and never stored: leave secrets out of what \
you write. Vrum stores, retrieves and coordinates; it does not generate answers: you do the \
reasoning and record what you find.
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
_Node = Annotated[
    Ref,
    Field(
        description="The epic, story or task, by its reference: its kind and number, such as "
        "story:12."
    ),
]
_NodeRefs = Annotated[
    list[Ref] | None,
    Field(
        description="References of the epics, stories or tasks it affects, such as story:12 or "
        "task:40."
    ),
]
_RuleIds = Annotated[list[int] | None, Field(description="Ids of the rules it affects.")]
_ConcernedNodeRefs = Annotated[
    list[Ref] | None,
    Field(
        description="References of the epics, stories or tasks it concerns, such as story:12 or "
        "task:40."
    ),
]
_ConcernedRuleIds = Annotated[list[int] | None, Field(description="Ids of the rules it concerns.")]
_ConcernedDecisionIds = Annotated[
    list[int] | None, Field(description="Ids of the decisions it concerns.")
]
_SaidBy = Annotated[
    str | None,
    Field(
        description="The person who changed their mind. Needed, with quote, to change a record a "
        "person has confirmed. If you are passing on what a person told you, give their name here "
        "and your own in actor."
    ),
]
_Quote = Annotated[
    str | None,
    Field(description="What that person said, or where they said it. Needed with said_by."),
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
_AfterNode = Annotated[
    Ref | None,
    Field(
        description="Leave empty for the first page; pass next_after (the reference of the last "
        "node of the page) for the next."
    ),
]

# Tools that only read: they change nothing. The Tools page of the board says so from this, and
# lets a person try exactly these.
_READS = ToolAnnotations(read_only_hint=True)


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
    accounts = Accounts(database)
    graph = Graph(database)
    links = Links(database)
    claims = Claims(database)
    rules = Rules(database)
    decisions = Decisions(database)
    facts = Facts(database)
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
    status = Status(database, indexer, searcher)
    references = References(database)
    workflow = Workflow(database, graph, searcher)

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
    mcp.accounts = accounts

    @mcp.tool(annotations=_READS)
    async def server_status() -> ServerStatus:
        """Check that this server is running and can reach its database, and see how it is.

        Returns the server version and the versions of PostgreSQL and the pgvector extension, how
        many of each kind of item it holds, and the state of the index: how many items are
        indexed and waiting, whether the embedding service answers just now, its model and vector
        size, and how the last reindex went. Call
        this first when connecting, or when something seems wrong.
        """
        with _database_errors():
            return await status.server_status(version("vrum"))

    @mcp.tool()
    async def create_node(
        actor: _Actor,
        kind: Annotated[str, Field(description="epic, story or task.")],
        title: str,
        parent: Annotated[
            Ref | None,
            Field(
                description="Required for a story (the reference of its epic, such as epic:3) and "
                "for a task (the reference of its story, such as story:12). An epic has no "
                "parent: leave it empty."
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
        version 1. Each kind is numbered from 1 on its own, and the new node's reference (ref) is
        its kind and number, such as story:12: that is how every tool names it. Dates are
        optional; a start date after the target date is refused.

        For an epic or a story it also says which existing ones look like the same thing
        (similar). It is created anyway: decide yourself whether it is a duplicate.
        """
        with _database_errors():
            return await workflow.create_node(
                actor=actor,
                kind=kind,
                title=title,
                parent=None if parent is None else parse_node_ref(parent, "parent"),
                body=body,
                priority=priority,
                start_on=parse_day("start_on", start_on),
                target_on=parse_day("target_on", target_on),
            )

    @mcp.tool(annotations=_READS)
    async def get_node(node: _Node) -> NodeDetail:
        """Read one epic, story or task in full, including the version you need to change it, how
        many handoff notes it has and the latest one."""
        with _database_errors():
            return await graph.get_node(parse_node_ref(node, "node"))

    @mcp.tool(annotations=_READS)
    async def list_nodes(
        kind: Annotated[str | None, Field(description="Only epic, story or task.")] = None,
        parent: Annotated[
            Ref | None,
            Field(
                description="Only the direct children of this node (its reference, such as epic:3)."
            ),
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
        after: _AfterNode = None,
        query: Annotated[
            str | None,
            Field(
                description="A filter in words and field:value terms, such as: kind:task "
                "status:ready,in_progress -claimed priority:high OR kind:story. Words match "
                "part of a title or body. field:a,b means any of these; a leading - leaves "
                "matches out; terms side by side must all hold; OR joins alternatives. Fields: "
                "kind, status, priority, claimed (who holds it), epic (epic:3 is epic 3 and "
                "everything under it), story (story:12 is story 12 and its tasks), parent "
                "(parent:epic:3, a reference), number (number:7, with kind:), start "
                "and target (a day, a range such as 2026-11-01..2026-12-31, or none), is "
                "(overdue, conflict, waiting, claimed, lapsed) and no (start, target, claimed, "
                "parent, body, waiting). claimed and waiting on their own find the items that "
                "have one, and -claimed those that have none. Combines with the other filters."
            ),
        ] = None,
    ) -> NodePage:
        """List nodes, epics first, then stories, then tasks, each by number, a page at a time.
        Every filter is optional and they combine.

        status can also be ready: a node nobody has started whose prerequisites are all done. The
        date filters look at each node's own dates, so a node without a date never matches one.
        Bodies are left out unless you ask for detail: see body_preview, or use get_node. For
        anything the other filters cannot say, use query: it is the same filter language the
        board's Table and Roadmap use.
        """
        with _database_errors():
            return await graph.list_nodes(
                kind=kind,
                parent=None if parent is None else parse_node_ref(parent, "parent"),
                status=status,
                start_from=parse_day("start_from", start_from),
                start_to=parse_day("start_to", start_to),
                target_from=parse_day("target_from", target_from),
                target_to=parse_day("target_to", target_to),
                overdue=overdue,
                detail=detail,
                limit=limit,
                after=None if after is None else parse_node_ref(after, "after"),
                query=query,
            )

    @mcp.tool()
    async def update_node(
        actor: _Actor,
        node: _Node,
        expected_version: _Version,
        title: str | None = None,
        body: str | None = None,
        status: Annotated[
            str | None, Field(description="new, in_progress, blocked, done or dropped.")
        ] = None,
        priority: Annotated[str | None, Field(description="low, normal, high or urgent.")] = None,
        parent: Annotated[
            Ref | None,
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
                node=parse_node_ref(node, "node"),
                expected_version=expected_version,
                title=title,
                body=body,
                status=status,
                priority=priority,
                parent=None if parent is None else parse_node_ref(parent, "parent"),
                start_on=parse_day("start_on", start_on),
                target_on=parse_day("target_on", target_on),
                clear_start_on=clear_start_on,
                clear_target_on=clear_target_on,
            )

    @mcp.tool()
    async def move_node(
        actor: _Actor,
        node: _Node,
        expected_version: _Version,
        new_parent: Annotated[
            Ref,
            Field(
                description="The epic to move a story to, or the story to move a task to, by "
                "reference (such as epic:3)."
            ),
        ],
    ) -> Node:
        """Move a story to another epic, or a task to another story, in one step.

        A node always has exactly one parent, so moving replaces the old parent. An epic cannot be
        moved. The move is recorded in the node's history.
        """
        with _database_errors():
            return await graph.move_node(
                actor=actor,
                node=parse_node_ref(node, "node"),
                expected_version=expected_version,
                new_parent=parse_node_ref(new_parent, "new_parent"),
            )

    @mcp.tool(annotations=_READS)
    async def node_history(node: _Node, limit: _Limit = 50, after_id: _After = None) -> EventPage:
        """Read everything that was ever done to one node, oldest first, a page at a time.

        Each event says who did it, when, what kind of change it was, and the changed fields as they
        were and as they became.
        """
        with _database_errors():
            return await graph.node_history(
                node=parse_node_ref(node, "node"), limit=limit, after_id=after_id
            )

    @mcp.tool()
    async def add_link(
        actor: _Actor,
        source: Annotated[
            Ref,
            Field(description="The node the link starts from (its reference, such as story:12)."),
        ],
        target: Annotated[
            Ref, Field(description="The node the link points to (its reference, such as task:4).")
        ],
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
                actor=actor,
                source=parse_node_ref(source, "source"),
                target=parse_node_ref(target, "target"),
                link_type=link_type,
            )

    @mcp.tool()
    async def remove_link(
        actor: _Actor,
        source: Annotated[
            Ref,
            Field(description="The node the link starts from (its reference, such as story:12)."),
        ],
        target: Annotated[
            Ref, Field(description="The node the link points to (its reference, such as task:4).")
        ],
        link_type: Annotated[
            str, Field(description="needs_first, supersedes, duplicates or relates_to.")
        ],
    ) -> Link:
        """Remove one link, for example when what it said no longer applies. The removal is
        recorded in the history of both nodes."""
        with _database_errors():
            return await links.remove_link(
                actor=actor,
                source=parse_node_ref(source, "source"),
                target=parse_node_ref(target, "target"),
                link_type=link_type,
            )

    @mcp.tool(annotations=_READS)
    async def list_links(
        node: _Node,
        link_type: Annotated[
            str | None, Field(description="Only needs_first, supersedes, duplicates or relates_to.")
        ] = None,
        limit: _Limit = 50,
        after_id: _After = None,
    ) -> LinkPage:
        """List every link that starts or ends at a node, a page at a time.

        For a needs_first link, source is the node that waits and target is the node it
        waits for.
        """
        with _database_errors():
            return await links.list_links(
                node=parse_node_ref(node, "node"),
                link_type=link_type,
                limit=limit,
                after_id=after_id,
            )

    @mcp.tool(annotations=_READS)
    async def ready_work(
        kind: Annotated[
            str | None,
            Field(description="Only epic, story or task. Leave empty for stories and tasks."),
        ] = None,
        detail: _Detail = False,
        limit: _Limit = 50,
        after: _AfterNode = None,
    ) -> NodePage:
        """List work that can be started now: not started, everything it needs first is done, and
        nobody holds a claim on it. Stories first, then tasks, each by number, a page at a time. To
        be handed the most urgent piece with everything you need to know, use next_work;
        claim_node takes a specific one."""
        with _database_errors():
            return await graph.ready_work(
                kind=kind,
                detail=detail,
                limit=limit,
                after=None if after is None else parse_node_ref(after, "after"),
            )

    @mcp.tool()
    async def claim_node(
        actor: _Actor, node: _Node, lease_seconds: _Lease = DEFAULT_LEASE_SECONDS
    ) -> Claim:
        """Say you are working on a story or task, so that nobody else picks it up.

        Only one actor can hold a claim at a time, and exactly one wins if several ask together.
        The claim lapses on its own after lease_seconds, so work that is abandoned becomes free
        again; renew it with renew_claim to keep it. Work that is not ready, done, dropped or
        blocked cannot be claimed.
        """
        with _database_errors():
            return await claims.claim_node(
                actor=actor, node=parse_node_ref(node, "node"), lease_seconds=lease_seconds
            )

    @mcp.tool()
    async def renew_claim(
        actor: _Actor, node: _Node, lease_seconds: _Lease = DEFAULT_LEASE_SECONDS
    ) -> Claim:
        """Keep your claim: it now lasts lease_seconds from now. Only the holder can renew, and
        only while the claim has not lapsed."""
        with _database_errors():
            return await claims.renew_claim(
                actor=actor, node=parse_node_ref(node, "node"), lease_seconds=lease_seconds
            )

    @mcp.tool()
    async def release_claim(
        actor: _Actor,
        node: _Node,
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
            return await claims.release_claim(
                actor=actor, node=parse_node_ref(node, "node"), note=note
            )

    @mcp.tool(annotations=_READS)
    async def who_is_working() -> WhoIsWorking:
        """See who is working on what: every person or agent who has taken work, with the claims
        they hold and how long each has to run, the claims that ran out on them with no handoff
        note, and the last handoff note they left. Working first, then lapsed, then finished.
        Use it before taking work, to see who else is on the project and what they hold."""
        with _database_errors():
            return await claims.who_is_working()

    @mcp.tool()
    async def next_work(
        actor: _Actor,
        within_node: Annotated[
            Ref | None,
            Field(
                description="Only work inside this epic or story (its reference, such as "
                "epic:3). Leave empty for anywhere."
            ),
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

        The context holds its epic and story, the requirements of its story, the decisions linked
        to it or its parents (past ones kept apart), every project-wide decision, the facts linked
        to it or its parents (each saying whether a person confirmed it), notes left by earlier
        holders, what it waits for, what waits for it, and similar items that are only
        suggestions. When nothing is free this is not an error: found is false and message
        says how much work is held, waiting or blocked.
        """
        with _database_errors():
            return await workflow.next_work(
                actor=actor,
                within_node=None
                if within_node is None
                else parse_node_ref(within_node, "within_node"),
                peek=peek,
                lease_seconds=lease_seconds,
                context_chars=context_chars,
            )

    @mcp.tool()
    async def finish_work(
        actor: _Actor,
        node: _Node,
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
                actor=actor, node=parse_node_ref(node, "node"), outcome=outcome, note=note
            )

    @mcp.tool()
    async def create_plan(
        actor: _Actor,
        items: Annotated[
            list[PlanItem],
            Field(
                description="The epics, stories and tasks to create. Each has a key (a name only "
                "this plan uses, never written like a reference) so that parents, links and "
                "rules can refer to it."
            ),
        ],
        links: Annotated[
            list[PlanLink] | None,
            Field(
                description="Links between items, by key or by the reference of an existing node."
            ),
        ] = None,
        rules: Annotated[
            list[PlanRule] | None,
            Field(
                description="Rules to record, each in a story by key or by the reference of an "
                "existing story."
            ),
        ] = None,
    ) -> PlanResult:
        """Create a whole plan at once: epics, stories, tasks, links and rules, or nothing.

        It is all checked first. If anything is wrong, nothing is created and every problem is
        listed, each naming its item. There is no limit on how many items a plan has. Each kind is
        numbered in the order the plan lists it, and every item created is returned with its key and
        its reference (such as story:12). For each new epic or story that looks like something
        that already exists, similar says so; it was created anyway.
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
            Field(
                description="The changes. Each names its node (by reference) and the version you "
                "last read."
            ),
        ],
    ) -> UpdateNodesResult:
        """Change many nodes in one call. Each change is checked and applied on its own, like
        update_node, so one stale or refused change never stops the others. The result says, for
        every change, whether it was applied or why not. A reference that is not written as one
        (such as 12) refuses the whole call before anything is changed."""
        with _database_errors():
            return await workflow.update_nodes(actor=actor, changes=changes)

    @mcp.tool(annotations=_READS)
    async def changes_since(
        after_event_id: Annotated[
            int,
            Field(description="Pass 0 for the beginning, then the latest_event_id you got last."),
        ] = 0,
        actor: Annotated[
            str | None, Field(description="Only changes made by this person or agent.")
        ] = None,
        node: Annotated[
            Ref | None,
            Field(description="Only changes to this node (its reference, such as story:12)."),
        ] = None,
        rule_id: Annotated[int | None, Field(description="Only changes to this rule.")] = None,
        decision_id: Annotated[
            int | None, Field(description="Only changes to this decision.")
        ] = None,
        fact_id: Annotated[int | None, Field(description="Only changes to this fact.")] = None,
        limit: _Limit = 100,
        newest: Annotated[
            bool,
            Field(
                description="True: show the newest changes (up to limit) instead of the oldest, "
                "still oldest first among them, to see what has just been going on. Use "
                "next_before_id to read the ones before."
            ),
        ] = False,
        before_event_id: Annotated[
            int | None,
            Field(
                description="Walk backwards: only the changes before this event (it implies "
                "newest). Pass the next_before_id of the last answer."
            ),
        ] = None,
    ) -> ChangesPage:
        """Show what changed after an event, oldest first, so you read only what is new.

        Keep latest_event_id and pass it next time (page with next_after_id until it is empty).
        Every event after the one you give is shown, in order, and none is skipped even when
        another change was still being written. Events are ordered by when their change began,
        which for two changes at the very same moment can differ from their ids. Give at most
        one of node, rule_id, decision_id and fact_id. To see only the newest changes, ask
        for newest, and walk back with before_event_id.
        """
        with _database_errors():
            return await workflow.changes_since(
                after_event_id=after_event_id,
                actor=actor,
                node=None if node is None else parse_node_ref(node, "node"),
                rule_id=rule_id,
                decision_id=decision_id,
                fact_id=fact_id,
                limit=limit,
                newest=newest,
                before_event_id=before_event_id,
            )

    @mcp.tool()
    async def create_rule(
        actor: _Actor,
        title: Annotated[str, Field(description="The rule, stated briefly.")],
        body: Annotated[
            str, Field(description="Plain text or Markdown: the detail, or where it came from.")
        ] = "",
        status: Annotated[str, Field(description="held, settled or dropped.")] = "held",
        story: Annotated[
            Ref | None,
            Field(
                description="The story that owns the rule (its reference, such as story:12). "
                "Leave empty to place it later."
            ),
        ] = None,
    ) -> Rule:
        """Record a rule (a requirement).

        A rule belongs to at most one story. One with no story is unplaced: the coverage report
        lists it until someone places it. Rules have their own ids, separate from the epics,
        stories and tasks, which are named by reference.
        """
        with _database_errors():
            return await rules.create_rule(
                actor=actor,
                title=title,
                body=body,
                status=status,
                story=None if story is None else parse_node_ref(story, "story"),
            )

    @mcp.tool(annotations=_READS)
    async def get_rule(rule_id: int) -> Rule:
        """Read one rule, including the version you need to change it."""
        with _database_errors():
            return await rules.get_rule(rule_id)

    @mcp.tool(annotations=_READS)
    async def list_rules(
        story: Annotated[
            Ref | None,
            Field(description="Only the rules in this story (its reference, such as story:12)."),
        ] = None,
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
                story=None if story is None else parse_node_ref(story, "story"),
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
        story: Annotated[
            Ref,
            Field(
                description="The story that will own the rule (its reference, such as story:12)."
            ),
        ],
    ) -> Rule:
        """Put an unplaced rule in a story.

        A rule belongs to at most one story, so placing a rule that is already in a story is
        refused: use move_rule to take it to another one.
        """
        with _database_errors():
            return await rules.place_rule(
                actor=actor,
                rule_id=rule_id,
                expected_version=expected_version,
                story=parse_node_ref(story, "story"),
            )

    @mcp.tool()
    async def move_rule(
        actor: _Actor,
        rule_id: int,
        expected_version: _Version,
        new_story: Annotated[
            Ref, Field(description="The story the rule moves to (its reference, such as story:12).")
        ],
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
                new_story=parse_node_ref(new_story, "new_story"),
            )

    @mcp.tool(annotations=_READS)
    async def rule_history(rule_id: int, limit: _Limit = 50, after_id: _After = None) -> EventPage:
        """Read everything that was ever done to one rule, oldest first, a page at a time: who did
        it, when, and the changed fields as they were and as they became."""
        with _database_errors():
            return await rules.rule_history(rule_id=rule_id, limit=limit, after_id=after_id)

    @mcp.tool(annotations=_READS)
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
        stories_after: Annotated[
            Ref | None,
            Field(
                description="Leave empty for the first page of stories without rules; pass "
                "next_stories_after for the next."
            ),
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
                stories_after=None
                if stories_after is None
                else parse_node_ref(stories_after, "stories_after"),
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
        project_wide: Annotated[
            bool,
            Field(
                description="True when it applies throughout the project, not to particular "
                "work: every recall and briefing of the project then includes it while it is "
                "active."
            ),
        ] = False,
        decided_on: _DecidedOn = None,
        nodes: _NodeRefs = None,
        rule_ids: _RuleIds = None,
    ) -> Decision:
        """Record a decision, and the epics, stories, tasks and rules it affects.

        Decisions are a record and are never edited. To change one, supersede it; to give it up,
        withdraw it. It starts unconfirmed: confirming is a separate step that a named person
        takes (set_confirmation). Link a decision to what it affects (nodes, by reference, and
        rule_ids) so that recall finds it for those items.
        """
        with _database_errors():
            return await decisions.record_decision(
                actor=actor,
                project_wide=project_wide,
                title=title,
                text=text,
                reason=reason,
                decided_by=decided_by,
                decided_on=decided_on,
                nodes=parse_node_refs(nodes, "nodes"),
                rule_ids=rule_ids,
            )

    @mcp.tool(annotations=_READS)
    async def get_decision(decision_id: int) -> Decision:
        """Read one decision, with its status, whether a person confirmed it, what replaced it or
        what it replaced, and the nodes and rules it is linked to."""
        with _database_errors():
            return await decisions.get_decision(decision_id)

    @mcp.tool(annotations=_READS)
    async def list_decisions(
        status: Annotated[
            str | None, Field(description="Only active, superseded or withdrawn.")
        ] = None,
        project_wide: Annotated[
            bool | None,
            Field(description="True: only project-wide decisions. False: only the others."),
        ] = None,
        confirmation: Annotated[
            str | None, Field(description="Only confirmed or unconfirmed.")
        ] = None,
        limit: _Limit = 50,
        after_id: _After = None,
    ) -> DecisionPage:
        """List decisions, oldest first, a page at a time. Every filter is optional. For the
        project-wide decisions in force, ask for project_wide=true and status=active."""
        with _database_errors():
            return await decisions.list_decisions(
                status=status,
                project_wide=project_wide,
                confirmation=confirmation,
                limit=limit,
                after_id=after_id,
            )

    @mcp.tool()
    async def link_decision(
        actor: _Actor, decision_id: int, nodes: _NodeRefs = None, rule_ids: _RuleIds = None
    ) -> Decision:
        """Say that an active decision also affects more nodes or rules. Nothing is changed if any
        reference or id is wrong or already linked. A decision that is not active keeps the links
        it had."""
        with _database_errors():
            return await decisions.link_decision(
                actor=actor,
                decision_id=decision_id,
                nodes=parse_node_refs(nodes, "nodes"),
                rule_ids=rule_ids,
            )

    @mcp.tool()
    async def unlink_decision(
        actor: _Actor, decision_id: int, nodes: _NodeRefs = None, rule_ids: _RuleIds = None
    ) -> Decision:
        """Say that an active decision no longer affects some nodes or rules. Nothing is changed if
        any of them is not linked. A decision that is not active keeps the links it had."""
        with _database_errors():
            return await decisions.unlink_decision(
                actor=actor,
                decision_id=decision_id,
                nodes=parse_node_refs(nodes, "nodes"),
                rule_ids=rule_ids,
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
        project_wide: Annotated[
            bool | None,
            Field(
                description="Whether the new decision is project-wide. Leave empty to keep the "
                "old one's."
            ),
        ] = None,
        decided_on: _DecidedOn = None,
        keep_links: Annotated[
            bool,
            Field(
                description="True: the new decision affects what the old one did. False: it starts "
                "with only the references and ids you give."
            ),
        ] = True,
        nodes: _NodeRefs = None,
        rule_ids: _RuleIds = None,
        said_by: _SaidBy = None,
        quote: _Quote = None,
    ) -> Decision:
        """Replace an active decision with a new one, in one step.

        The old decision is kept, marked superseded, and points to the new one, so nothing is lost
        or overwritten. Recall shows the old one as superseded, never as active. Returns the new
        decision, which starts unconfirmed. A decision that is already superseded cannot be
        superseded again: supersede its replacement instead. A decision a person confirmed can be
        replaced only with said_by and quote.
        """
        with _database_errors():
            return await decisions.supersede_decision(
                actor=actor,
                decision_id=decision_id,
                project_wide=project_wide,
                title=title,
                text=text,
                reason=reason,
                decided_by=decided_by,
                decided_on=decided_on,
                keep_links=keep_links,
                nodes=parse_node_refs(nodes, "nodes"),
                rule_ids=rule_ids,
                said_by=said_by,
                quote=quote,
            )

    @mcp.tool()
    async def withdraw_decision(
        actor: _Actor,
        decision_id: Annotated[int, Field(description="The active decision being given up.")],
        reason: Annotated[str, Field(description="Why it is given up.")],
        said_by: _SaidBy = None,
        quote: _Quote = None,
    ) -> Decision:
        """Give an active decision up, with the reason, without replacing it. Nothing is deleted:
        it stays as a record, marked withdrawn, and recall shows it as no longer in force. A
        decision a person confirmed can be withdrawn only with said_by and quote."""
        with _database_errors():
            return await decisions.withdraw_decision(
                actor=actor, decision_id=decision_id, reason=reason, said_by=said_by, quote=quote
            )

    @mcp.tool(annotations=_READS)
    async def recall(
        node: Annotated[
            Ref | None,
            Field(
                description="The epic, story or task to recall what is known for (its reference, "
                "such as story:12)."
            ),
        ] = None,
        rule_id: Annotated[
            int | None, Field(description="Or the rule to recall what is known for.")
        ] = None,
        show: Annotated[
            str,
            Field(
                description="all, or one of active_decisions, project_wide_decisions, facts, "
                "past_decisions or past_facts to page through just that section."
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
                description="True: also return decisions and facts that are alike in meaning but "
                "not linked, as suggestions in a section of their own."
            ),
        ] = False,
    ) -> Recall:
        """Recall everything known that matters for one node or rule, in separate sections.

        Decisions are what people decided; project-wide ones apply throughout the project. Facts
        say where they came from. Every entry says whether a named person confirmed it (an
        unconfirmed record is not yet reviewed) and whether it is still in force: past decisions
        and past facts were superseded or withdrawn and are not. Every entry says why it is there.
        Only exact links are followed: nothing is inferred, and what is linked to the parent or
        children of this item is not included, so recall those too if they matter. With
        with_similar, a last section adds what is alike in meaning but not linked, as suggestions.
        Call this before starting work.
        """
        with _database_errors():
            return await recaller.recall(
                node=None if node is None else parse_node_ref(node, "node"),
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
        nodes: _ConcernedNodeRefs = None,
        rule_ids: _ConcernedRuleIds = None,
        decision_ids: _ConcernedDecisionIds = None,
    ) -> Fact:
        """Record a fact, with where it came from and what it concerns.

        A fact is a record and is never edited. When it changes, replace it with supersede_fact;
        when it was wrong, withdraw it with withdraw_fact. A fact is not a decision, and it starts
        unconfirmed: confirming is a separate step that a named person takes (set_confirmation),
        so recording a fact yourself never counts as a person's review. Link it to what it
        concerns (nodes, by reference, rule_ids and decision_ids) so that recall finds it for
        those items.
        """
        with _database_errors():
            return await facts.retain_fact(
                actor=actor,
                text=text,
                source=source,
                nodes=parse_node_refs(nodes, "nodes"),
                rule_ids=rule_ids,
                decision_ids=decision_ids,
            )

    @mcp.tool()
    async def supersede_fact(
        actor: _Actor,
        replaces: Annotated[
            list[int],
            Field(
                description="The ids of the active facts that the new fact replaces. Any number."
            ),
        ],
        reason: Annotated[str, Field(description="Why they are being replaced.")],
        text: Annotated[str, Field(description="The new fact.")],
        source: Annotated[str, Field(description="Where the new fact came from. Never blank.")],
        keep_links: Annotated[
            bool,
            Field(
                description="True: the new fact concerns what the old ones did. False: it starts "
                "with only the references and ids you give."
            ),
        ] = True,
        nodes: _ConcernedNodeRefs = None,
        rule_ids: _ConcernedRuleIds = None,
        decision_ids: _ConcernedDecisionIds = None,
        said_by: _SaidBy = None,
        quote: _Quote = None,
    ) -> FactChange:
        """Replace one or more facts with a newer one, in one step.

        Use this when something is no longer true as it was recorded: list every old fact in
        replaces. The old ones are kept, marked superseded, and point to the new one, which starts
        unconfirmed. If any of them does not exist or is not active, nothing is changed. A fact a
        person confirmed can be replaced only with said_by and quote.
        """
        with _database_errors():
            return await facts.supersede_fact(
                actor=actor,
                replaces=replaces,
                reason=reason,
                text=text,
                source=source,
                keep_links=keep_links,
                nodes=parse_node_refs(nodes, "nodes"),
                rule_ids=rule_ids,
                decision_ids=decision_ids,
                said_by=said_by,
                quote=quote,
            )

    @mcp.tool()
    async def withdraw_fact(
        actor: _Actor,
        fact_ids: Annotated[
            list[int], Field(description="The ids of the active facts that are wrong.")
        ],
        reason: Annotated[str, Field(description="Why they are wrong.")],
        said_by: _SaidBy = None,
        quote: _Quote = None,
    ) -> FactChange:
        """Mark one or more facts as wrong, in one step. Nothing is deleted: each stays as a
        record, marked withdrawn, with the reason.

        If any of them does not exist or is not active, nothing is changed. A fact a person
        confirmed can be withdrawn only with said_by and quote.
        """
        with _database_errors():
            return await facts.withdraw_fact(
                actor=actor, fact_ids=fact_ids, reason=reason, said_by=said_by, quote=quote
            )

    @mcp.tool()
    async def set_confirmation(
        actor: _Actor,
        kind: Annotated[str, Field(description="fact or decision.")],
        record_id: Annotated[int, Field(description="The id of that fact or decision.")],
        confirmation: Annotated[
            str,
            Field(
                description="confirmed: a named person reviewed it. unconfirmed: take that "
                "confirmation away."
            ),
        ],
        person: Annotated[
            str,
            Field(
                description="The person: who reviewed it (to confirm), or who says the "
                "confirmation no longer stands (to unconfirm). Not you: your name goes in actor."
            ),
        ],
        quote: Annotated[
            str | None,
            Field(
                description="What the person said, or where. Optional to confirm; needed to "
                "unconfirm."
            ),
        ] = None,
    ) -> RecordConfirmation:
        """Record that a named person reviewed a fact or decision, or take that back.

        Confirmation says a person looked at the record, not that Vrum proved it true. A record
        starts unconfirmed, and recording one yourself is never a person's confirmation. Only an
        active record's confirmation changes: what a superseded or withdrawn record was confirmed
        as stays in its history. The change is logged with who made it.
        """
        check_choice("kind", kind, ("fact", "decision"))
        with _database_errors():
            if kind == "fact":
                return RecordConfirmation(
                    kind=kind,
                    fact=await facts.set_confirmation(
                        actor=actor,
                        fact_id=record_id,
                        confirmation=confirmation,
                        person=person,
                        quote=quote,
                    ),
                )
            return RecordConfirmation(
                kind=kind,
                decision=await decisions.set_confirmation(
                    actor=actor,
                    decision_id=record_id,
                    confirmation=confirmation,
                    person=person,
                    quote=quote,
                ),
            )

    @mcp.tool()
    async def link_fact(
        actor: _Actor,
        fact_id: int,
        nodes: _ConcernedNodeRefs = None,
        rule_ids: _ConcernedRuleIds = None,
        decision_ids: _ConcernedDecisionIds = None,
    ) -> Fact:
        """Say that an active fact also concerns more nodes, rules or decisions. Nothing is
        changed if any reference or id is wrong or already linked."""
        with _database_errors():
            return await facts.link_fact(
                actor=actor,
                fact_id=fact_id,
                nodes=parse_node_refs(nodes, "nodes"),
                rule_ids=rule_ids,
                decision_ids=decision_ids,
            )

    @mcp.tool()
    async def unlink_fact(
        actor: _Actor,
        fact_id: int,
        nodes: _ConcernedNodeRefs = None,
        rule_ids: _ConcernedRuleIds = None,
        decision_ids: _ConcernedDecisionIds = None,
    ) -> Fact:
        """Say that an active fact no longer concerns some nodes, rules or decisions. Nothing is
        changed if any of them is not linked."""
        with _database_errors():
            return await facts.unlink_fact(
                actor=actor,
                fact_id=fact_id,
                nodes=parse_node_refs(nodes, "nodes"),
                rule_ids=rule_ids,
                decision_ids=decision_ids,
            )

    @mcp.tool(annotations=_READS)
    async def get_fact(fact_id: int) -> Fact:
        """Read one fact: its source, its status, whether and by whom it was confirmed, what
        replaced it, and what it concerns."""
        with _database_errors():
            return await facts.get_fact(fact_id)

    @mcp.tool(annotations=_READS)
    async def list_facts(
        status: Annotated[
            str | None, Field(description="Only active, superseded or withdrawn.")
        ] = None,
        confirmation: Annotated[
            str | None, Field(description="Only confirmed or unconfirmed.")
        ] = None,
        node: Annotated[
            Ref | None,
            Field(
                description="Only what concerns exactly this node (its reference, such as "
                "story:12)."
            ),
        ] = None,
        rule_id: Annotated[
            int | None, Field(description="Only what concerns exactly this rule.")
        ] = None,
        decision_id: Annotated[
            int | None, Field(description="Only what concerns this decision.")
        ] = None,
        within_node: Annotated[
            Ref | None,
            Field(
                description="Only what concerns this epic, story or task (its reference, such as "
                "epic:3), or anything under it, or a rule in one of those stories. One walk over "
                "a whole area."
            ),
        ] = None,
        limit: _Limit = 50,
        after_id: _After = None,
    ) -> FactPage:
        """List facts, oldest first, a page at a time. Every filter is optional.

        The answer says how many match in all, so you can walk every page and check that none was
        skipped. For one area of the project, use within_node.
        """
        with _database_errors():
            return await facts.list_facts(
                status=status,
                confirmation=confirmation,
                node=None if node is None else parse_node_ref(node, "node"),
                rule_id=rule_id,
                decision_id=decision_id,
                within_node=None
                if within_node is None
                else parse_node_ref(within_node, "within_node"),
                limit=limit,
                after_id=after_id,
            )

    @mcp.tool(annotations=_READS)
    async def fact_history(fact_id: int, limit: _Limit = 50, after_id: _After = None) -> EventPage:
        """Read everything that was ever done to one fact, oldest first, a page at a time: who
        recorded, linked, confirmed, replaced or withdrew it, and when. A person who changed their
        mind, and the agent that passed it on, are both in it."""
        with _database_errors():
            return await facts.fact_history(fact_id=fact_id, limit=limit, after_id=after_id)

    @mcp.tool(annotations=_READS)
    async def decision_history(
        decision_id: int, limit: _Limit = 50, after_id: _After = None
    ) -> EventPage:
        """Read everything that was ever done to one decision, oldest first, a page at a time: who
        recorded, linked, unlinked or superseded it, and when."""
        with _database_errors():
            return await decisions.decision_history(
                decision_id=decision_id, limit=limit, after_id=after_id
            )

    @mcp.tool(annotations=_READS)
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
            Field(description="Only these types: epic, story, task, rule, decision, fact."),
        ] = None,
        statuses: Annotated[
            list[str] | None,
            Field(description="Only these statuses, such as ready, done, active or withdrawn."),
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
        """Search epics, stories, tasks, requirements, decisions and facts.

        Each result says how it matched (words, meaning or both), why, and shows a snippet with
        the matched words between « and ». Matches by meaning alone are suggestions. A decision
        or fact says whether a person confirmed it and whether it is still in force. The answer
        counts the matches by type and by status. Exact words are found the moment something is
        saved, and keep working when the
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

    @mcp.tool(annotations=_READS)
    async def similar_items(
        node: Annotated[
            Ref | None,
            Field(description="An epic, story or task (its reference, such as story:12)."),
        ] = None,
        rule_id: Annotated[int | None, Field(description="Or a rule.")] = None,
        decision_id: Annotated[int | None, Field(description="Or a decision.")] = None,
        fact_id: Annotated[int | None, Field(description="Or a fact.")] = None,
        text: Annotated[str | None, Field(description="Or a piece of text.")] = None,
        types: Annotated[
            list[str] | None,
            Field(description="Only these types, such as decision or fact."),
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

        Give exactly one of node, rule_id, decision_id, fact_id or text. The item itself is
        left out. Every result is a suggestion. It needs the embedding service and the index; if
        either is not ready it says so and changes nothing.
        """
        with _database_errors():
            return await searcher.similar_items(
                node=None if node is None else parse_node_ref(node, "node"),
                rule_id=rule_id,
                decision_id=decision_id,
                fact_id=fact_id,
                text=text,
                types=types,
                min_similarity=min_similarity,
                limit=limit,
                offset=offset,
            )

    @mcp.tool(annotations=_READS)
    async def find_overlaps(
        item_type: Annotated[
            str, Field(description="story (the default), epic or rule: what to compare.")
        ] = "story",
        epics: Annotated[
            list[Ref] | None,
            Field(
                description="Only pairs where at least one of the two is in one of these epics "
                "(their references, such as epic:3). Leave empty to look at everything."
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
                epics=parse_node_refs(epics, "epics"),
                min_similarity=min_similarity,
                include_dropped=include_dropped,
                limit=limit,
                offset=offset,
            )

    @mcp.tool(annotations=_READS)
    async def find_mentions(
        node: Annotated[
            Ref | None,
            Field(
                description="An epic, story or task (its reference, such as story:12): who "
                "refers to it."
            ),
        ] = None,
        rule_id: Annotated[int | None, Field(description="Or a rule.")] = None,
        decision_id: Annotated[int | None, Field(description="Or a decision.")] = None,
        fact_id: Annotated[int | None, Field(description="Or a fact.")] = None,
        limit: _Limit = 50,
        offset: Annotated[
            int, Field(description="How many to skip. Pass next_offset for the next page.")
        ] = 0,
    ) -> MentionPage:
        """List the items whose text refers to one item, or, with no item given, every reference
        written anywhere in the graph.

        A reference is [[epic:3]], [[story:12]], [[task:40]], [[rule:3]], [[decision:7]] or
        [[fact:5]] in the details of an epic, story, task or requirement, the text or reason of a
        decision, or the text or source of a fact. Titles are not read. It is only
        text: it creates no link, changes no readiness and no order, and nothing is stored for it.
        This reads the text of everything when asked. Each mention says which item holds it, where
        and a snippet around it, and, for every reference, whether the item it names exists: one
        that does not is plainly missing. An item that refers to itself is not listed. For a
        decision, a rule or any item, use this to see what leans on it before changing it.
        """
        with _database_errors():
            return await references.find_mentions(
                node=None if node is None else parse_node_ref(node, "node"),
                rule_id=rule_id,
                decision_id=decision_id,
                fact_id=fact_id,
                limit=limit,
                offset=offset,
            )

    @mcp.tool()
    async def reindex() -> IndexReport:
        """Bring the semantic index up to date now: build it if it does not exist, then embed
        every epic, story, requirement, decision and fact that is new or changed.

        The server also does this by itself within seconds, so you rarely need it. This is the only
        thing that writes to the index, which holds nothing that is not in the graph and can be
        dropped and rebuilt at any time. If the embedding service cannot be reached, nothing is
        changed and exact-words search still works.
        """
        with _database_errors():
            try:
                return await indexer.reindex(current_project())
            except EmbeddingUnavailable as error:
                raise Refusal(
                    f"{error} Nothing was indexed. Exact-words search still works; try again "
                    "when the service is back."
                ) from error

    @mcp.tool(annotations=_READS)
    async def export_graph(
        after: Annotated[
            str | None,
            Field(description="Leave empty for the first page; pass next_after for the next."),
        ] = None,
        limit: _Limit = 200,
    ) -> ExportPage:
        """Read the whole graph as data, one page at a time: every epic, story and task (with
        dates), link, rule, decision (with what each affects) and fact (with its source, status,
        confirmation and what it concerns), and the event log.

        Each page holds items of one list (nodes, links, rules, decisions, facts or events), in
        a fixed order (nodes as epics, then stories, then tasks, each by number; the rest by id).
        Keep calling with next_after until it is empty; the first page also says how
        many items each list holds in all, to check you got them all. Dates and times are text.
        Not included: claims (leases that run out) and what is worked out from the rest (what is
        ready, the search and semantic indexes).

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
                "lists nodes, links, rules, decisions, facts and events, as export_graph "
                "pages give them."
            ),
        ],
    ) -> ImportResult:
        """Load a whole export into this server, which must be empty. Every number, id, date,
        status, confirmation and event is kept, so what is exported afterwards is identical to the
        file, and the next new epic, story or task is numbered after the highest of its own kind.

        It is all or nothing: the file is checked first and every problem in it is listed at once,
        and the database checks everything again as it is loaded. A server that already holds
        anything is refused, because the numbers in the file would clash with what is there: load
        it into a fresh one, or restore a backup. A file can be as big as one request may be (see
        VRUM_MAX_REQUEST_BYTES). scripts/import_graph.py sends a file for you. If an embedding
        service is set up, the server indexes the imported items by itself afterwards.
        """
        actor = clean_actor(actor)
        with _database_errors():
            result = await exchange.import_document(document)
        event(log, logging.INFO, "import_finished", actor=actor[:100], counts=result.counts)
        return result

    @mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def health(request: Request) -> Response:
        try:
            await database.ping()
        except psycopg.Error:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    add_board(mcp, settings=settings, searcher=searcher, accounts=accounts)
    return mcp
