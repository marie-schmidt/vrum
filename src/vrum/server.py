"""The MCP server: its tools, its health check, and how it is wired to the database."""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import date
from importlib.metadata import version
from typing import Annotated, Any
from urllib.parse import urlparse

import psycopg
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from vrum.accounts import Accounts
from vrum.ask_book import AskBook, AskPage
from vrum.asks import AskInput, AskState, clean_ask
from vrum.board import add_board
from vrum.checks import Checks, NodeChecks
from vrum.claims import DEFAULT_LEASE_SECONDS, Claim, Claims, WhoIsWorking
from vrum.code_mode import (
    DEFAULT_TIMEOUT,
    MOST_TIMEOUT,
    CodeMode,
    Explored,
    RunResult,
    failure_words,
)
from vrum.code_mode import explore as describe_functions
from vrum.common import (
    Ref,
    Refusal,
    parse_day,
    parse_node_ref,
    parse_node_refs,
)
from vrum.config import DEFAULT_MIN_SIMILARITY, DEFAULT_OVERLAP_THRESHOLD, Settings
from vrum.db import Database, current_project
from vrum.definition_of_done import DefinitionOfDone, DefinitionOfDoneBook
from vrum.embeddings import EmbeddingUnavailable
from vrum.exchange import Exchange, ExportPage, ImportResult
from vrum.gate_reads import GateState
from vrum.gates import GateBook, PlanBook, PlanPage, PlanState
from vrum.graph import EventPage, Graph, Node, NodeDetail, NodePage
from vrum.guard import GuardedServer, current_caller
from vrum.identity import current_identity
from vrum.indexing import Indexer, IndexReport
from vrum.links import Link, LinkPage, Links
from vrum.logs import event
from vrum.mail import Mailer
from vrum.needs import Needs, NeedsAPerson
from vrum.needs_app import needs_apps, summary, view_meta
from vrum.notes import Note, NoteChange, NotePage, Notes
from vrum.placement import (
    DEFAULT_SUGGESTIONS,
    NewEpic,
    ParentSuggestions,
    Placement,
    PlacementBook,
    PlacementPage,
    PlacementState,
)
from vrum.plan_revise import PlanRevised, PlanReviser, RevisionItem, RevisionRule
from vrum.plan_revisions import PlanCommentInput
from vrum.plan_templates import PlanTemplateInfo, PlanTemplatePage
from vrum.recall import DEFAULT_RECALL_CHARS, Recall, Recaller
from vrum.references import MentionPage, References
from vrum.rules import CoverageReport, Rule, RulePage, Rules
from vrum.search import OverlapPage, Searcher, SearchPage
from vrum.sso import Sso
from vrum.status import ServerStatus, Status
from vrum.webhooks import Dispatcher, WebhookBook
from vrum.whereabouts import clean_whereabouts
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
Vrum is a project graph. Epics hold stories and stories hold tasks; acceptance criteria and notes \
belong to or concern them. An epic, story or task is named by its kind and number: `epic:3`, \
`story:12`, `task:40`. Tools take that reference, never a bare number. A note records what is \
known or decided, with why; only unconfirmed and confirmed (a person reviewed it) ones are in \
force: check its status before relying on it. Your agent key names you: never send a name.

The working loop:
1. `next_work` takes the most urgent free story or task and returns it with its acceptance \
criteria, the definition of done and its checks, notes and handoffs. Keep the claim with \
`renew_claim`; say where the work is (branch, pull_request) when you finish.
2. Before deciding something that matters, `recall` what is known, or `search`.
3. Record what is decided or learned with `add_note`: say why, and name the person in decided_by \
when someone decided it. Only a person confirms it.
4. Before finishing, meet the acceptance criteria, do what each check says (run the tests) and \
`set_check` it, saying how you checked: done is refused while one is unticked. Leave a check \
marked person_only for a person: finish as blocked, saying what to look at. End with \
`finish_work`: done, blocked, dropped or released, and a handoff note.

Also: `create_plan` makes a whole plan at once or nothing; give every story and task a size, xs to \
xl (split anything bigger). `changes_since` shows only what \
is new, and `list_nodes` takes a filter as its query, such as `kind:task status:ready -claimed OR \
is:overdue`. `[[story:12]]` in any text refers to an item (also `[[epic:3]]`, `[[task:40]]`, \
`[[rule:3]]`, `[[note:7]]`). A person does the rest on the board. With only \
`explore` and `run`, call each tool as `vrum.<name>(...)` inside `run`.
"""

_Detail = Annotated[
    bool,
    Field(
        description="True: include whole bodies. By default a list leaves them out and gives each "
        "item's length and the first characters instead, to keep the answer small."
    ),
]

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
_Repository = Annotated[
    str | None, Field(description="Optional: the repository's https address, not checked.")
]
_Branch = Annotated[str | None, Field(description="Optional: the branch the work is on.")]
_Commit = Annotated[str | None, Field(description="Optional: the commit reached, by its hash.")]
_PullRequest = Annotated[
    str | None, Field(description="Optional: the pull request's https address.")
]
_RunLabel = Annotated[
    str | None,
    Field(description="Optional: a label for this run. A label only, never who you are."),
]
_ClientName = Annotated[
    str | None, Field(description="Optional: what you run in, such as claude-code. A label only.")
]
_Ask = Annotated[
    AskInput | None,
    Field(description="Optional: a question for a person. With blocked, it blocks until answered."),
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
        description="References of the epics, stories or tasks it concerns, such as story:12 or "
        "task:40."
    ),
]
_RuleIds = Annotated[
    list[int] | None, Field(description="Ids of the acceptance criteria (rules) it concerns.")
]
_NoteIds = Annotated[list[int] | None, Field(description="Ids of the notes it concerns.")]
_SaidBy = Annotated[
    str | None,
    Field(
        description="The person who changed their mind. Needed, with quote, to change a record a "
        "person has confirmed. If you are passing on what a person told you, give their name here: "
        "you are recorded separately, by your key."
    ),
]
_Quote = Annotated[
    str | None,
    Field(description="What that person said, or where they said it. Needed with said_by."),
]
_DecidedOn = Annotated[
    date | None,
    Field(
        description="The day it was decided, as YYYY-MM-DD. Only with decided_by; leave empty "
        "for today."
    ),
]
_Size = Annotated[
    str | None,
    Field(
        description="xs, s, m, l or xl: how big a story or task is, effort and context together "
        "(1, 2, 3, 5 and 8 points). An epic has no size of its own: it adds up. If something is "
        "bigger than xl, split it."
    ),
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


def _me() -> str:
    """The name this call's changes are recorded under: the name of the call's identity (the agent
    its key is named for, or the person signed in on the board). The guard decides it before the
    tool runs, and it is never an argument."""
    return current_identity().name


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
    mailer = (
        Mailer(settings.mail, hello=urlparse(settings.public_url).hostname or "localhost")
        if settings.mail
        else None
    )
    accounts = Accounts(database, mailer=mailer, public_url=settings.public_url)
    sso = Sso(settings.sso, public_url=settings.public_url, accounts=accounts)
    graph = Graph(database)
    links = Links(database)
    claims = Claims(database)
    rules = Rules(database)
    definition_of_done = DefinitionOfDoneBook(database)
    checks = Checks(database)
    notes = Notes(database)
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
    workflow = Workflow(database, graph, searcher, settings.board_poll_seconds)
    ask_book = AskBook(database)
    gates = GateBook(database)
    plans = PlanBook(database, searcher)
    revisions = PlanReviser(database)
    webhook_book = WebhookBook(database, accounts, settings.webhooks, mailer)
    dispatcher = Dispatcher(database, settings.webhooks, mailer, settings.public_url)
    placement = Placement(database, searcher)
    placements = PlacementBook(database, placement)
    needs = Needs(database)

    @asynccontextmanager
    async def lifespan(server: MCPServer[Database]) -> AsyncIterator[Database]:
        await database.open()
        # The reindex job keeps the semantic index up to date. It only runs when an embedding
        # service is set up.
        job = asyncio.create_task(indexer.run_forever()) if embedding else None
        # The webhook job sends the signed messages and the nudge emails that people asked for.
        # Nothing waits for it: a tool call only writes its event.
        news = asyncio.create_task(dispatcher.run_forever())
        try:
            yield database
        finally:
            for running in (job, news):
                if running is not None:
                    running.cancel()
                    with suppress(asyncio.CancelledError):
                        await running
            await database.close()

    mcp = GuardedServer(
        "vrum",
        version=version("vrum"),
        lifespan=lifespan,
        instructions=INSTRUCTIONS,
        extensions=[needs_apps()],
    )
    mcp.accounts = accounts
    code_mode = CodeMode(settings.sandbox_socket, mcp)

    @mcp.tool(annotations=_READS)
    async def server_status() -> ServerStatus:
        """Check that this server is running and can reach its database, and see how it is.

        Returns the server version and the versions of PostgreSQL and the pgvector extension, how
        many of each kind of item it holds, and the state of the index: how many items are
        indexed and waiting, whether the embedding service answers just now, its model and vector
        size, and how the last reindex went. Call
        this first when connecting, or when something seems wrong. Everything you change from now
        on is recorded under the name of the key you connected with, as an agent.
        """
        with _database_errors():
            return await status.server_status(version("vrum"))

    @mcp.tool()
    async def create_node(
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
        size: _Size = None,
        start_on: _StartOn = None,
        target_on: _TargetOn = None,
    ) -> CreatedNode:
        """Create an epic, a story or a task.

        Work is an exact hierarchy: an epic has no parent, a story belongs to one epic, and a task
        belongs to one story. Any other shape is refused. A new node starts with status new and
        version 1. Each kind is numbered from 1 on its own, and the new node's reference (ref) is
        its kind and number, such as story:12: that is how every tool names it. Dates are
        optional; a start date after the target date is refused. Give a story or task a size
        (xs to xl): how much effort and how much context it will take, so that work can be
        planned in runs.

        For an epic or a story it also says which existing ones look like the same thing
        (similar). It is created anyway: decide yourself whether it is a duplicate.
        """
        with _database_errors():
            return await workflow.create_node(
                actor=_me(),
                kind=kind,
                title=title,
                parent=None if parent is None else parse_node_ref(parent, "parent"),
                body=body,
                priority=priority,
                size=size,
                start_on=parse_day("start_on", start_on),
                target_on=parse_day("target_on", target_on),
            )

    @mcp.tool(annotations=_READS)
    async def get_node(node: _Node) -> NodeDetail:
        """Read one epic, story or task in full, including the version you need to change it, how
        many handoff notes it has and the latest one. For a story or task it also lists its
        checks: each item of the definition of done, ticked or not, with who ticked it, when and
        how it was checked."""
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
                "kind, status, priority, size (xs, s, m, l, xl), claimed (who holds it), epic "
                "(epic:3 is epic 3 and "
                "everything under it), story (story:12 is story 12 and its tasks), parent "
                "(parent:epic:3, a reference), number (number:7, with kind:), start "
                "and target (a day, a range such as 2026-11-01..2026-12-31, or none), is "
                "(overdue, conflict, waiting, held: by a gate or an unapproved plan, claimed, "
                "lapsed, unverified: blocked work nobody "
                "holds that has a check only a verifier may tick) and no (start, target, claimed, "
                "parent, body, waiting, criteria, size). claimed and waiting on their own find "
                "the items that have one, and -claimed those that have none. Combines with the "
                "other filters."
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
        node: _Node,
        expected_version: _Version,
        title: str | None = None,
        body: str | None = None,
        status: Annotated[
            str | None, Field(description="new, in_progress, blocked, done or dropped.")
        ] = None,
        priority: Annotated[str | None, Field(description="low, normal, high or urgent.")] = None,
        size: _Size = None,
        parent: Annotated[
            Ref | None,
            Field(
                description="Do not use this to change the parent: a node has exactly one parent, "
                "and moving it is done on the board."
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
        clear_size: Annotated[
            bool, Field(description="True to remove the size. Do not also give size.")
        ] = False,
        why: Annotated[
            str | None,
            Field(
                description="Why it is blocked or dropped. Saved as a handoff note on the item. "
                "Only with status blocked or dropped."
            ),
        ] = None,
        ask: _Ask = None,
    ) -> Node:
        """Change a node's title, body, status, priority, size or dates. Leave out what should
        stay.

        You must send the version you last read. If someone else changed the node since, the change
        is refused so nothing is overwritten silently: read the node again and repeat the change.
        Whether a node is ready is worked out by the server, so you cannot set it: new here means
        "not started", and the node shows as ready when everything it needs first is done. A date
        or a size is removed only by asking for it (clear_start_on, clear_target_on, clear_size),
        and a start date after the target date is refused. An epic has no size. As an agent you
        cannot set the status done while a check of the definition of done is unticked on the
        item: tick them first with set_check. When you set the status to blocked or dropped you
        can say why: it is saved as a handoff note on the item, in the same step. Blocked because
        you need a person? Add an ask: it is recorded for a person to answer, and the answer
        comes back in get_node and next_work.
        """
        with _database_errors():
            return await graph.update_node(
                actor=_me(),
                node=parse_node_ref(node, "node"),
                expected_version=expected_version,
                title=title,
                body=body,
                status=status,
                priority=priority,
                size=size,
                parent=None if parent is None else parse_node_ref(parent, "parent"),
                start_on=parse_day("start_on", start_on),
                target_on=parse_day("target_on", target_on),
                clear_start_on=clear_start_on,
                clear_target_on=clear_target_on,
                clear_size=clear_size,
                why=why,
                ask=None if ask is None else clean_ask(ask),
            )

    @mcp.tool()
    async def move_node(
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
                actor=_me(),
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
                "work. relates_to: the two are connected. discovered_from: the source was found "
                "while doing the target (it never makes anything wait)."
            ),
        ],
    ) -> Link:
        """Link two nodes.

        needs_first is how order is expressed: while the target is not done, the source is not
        ready and cannot be claimed. A needs_first link that would make nodes wait on each other in
        a circle is refused. A node cannot be linked to itself. duplicates and relates_to work in
        both directions. Found something while working? Create it and link it discovered_from the
        work you were on.
        """
        with _database_errors():
            return await links.add_link(
                actor=_me(),
                source=parse_node_ref(source, "source"),
                target=parse_node_ref(target, "target"),
                link_type=link_type,
            )

    @mcp.tool()
    async def remove_link(
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
                description="needs_first, supersedes, duplicates, relates_to or discovered_from."
            ),
        ],
    ) -> Link:
        """Remove one link, for example when what it said no longer applies. The removal is
        recorded in the history of both nodes."""
        with _database_errors():
            return await links.remove_link(
                actor=_me(),
                source=parse_node_ref(source, "source"),
                target=parse_node_ref(target, "target"),
                link_type=link_type,
            )

    @mcp.tool(annotations=_READS)
    async def list_links(
        node: _Node,
        link_type: Annotated[
            str | None,
            Field(
                description="Only needs_first, supersedes, duplicates, relates_to or "
                "discovered_from."
            ),
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
        node: _Node,
        lease_seconds: _Lease = DEFAULT_LEASE_SECONDS,
        run_label: _RunLabel = None,
        client_name: _ClientName = None,
        repository: _Repository = None,
        branch: _Branch = None,
        commit: _Commit = None,
        pull_request: _PullRequest = None,
    ) -> Claim:
        """Say you are working on a story or task, so that nobody else picks it up.

        Only one person or agent can hold a claim at a time, and exactly one wins if several ask
        together. The claim is held by you, as the agent your key names, and only you can renew or
        release it. It lapses on its own after lease_seconds, so work that is abandoned becomes
        free again; renew it with renew_claim to keep it. Work that is not ready, done, dropped or
        blocked cannot be claimed. You may say where the work is (repository, branch, commit,
        pull_request) and which run you are (run_label, client_name): what you say is kept with
        the claim and your handoff note, and is not checked.
        """
        with _database_errors():
            return await claims.claim_node(
                actor=_me(),
                node=parse_node_ref(node, "node"),
                lease_seconds=lease_seconds,
                where=clean_whereabouts(
                    repository=repository,
                    branch=branch,
                    commit=commit,
                    pull_request=pull_request,
                    run_label=run_label,
                    client_name=client_name,
                ),
            )

    @mcp.tool()
    async def renew_claim(
        node: _Node,
        lease_seconds: _Lease = DEFAULT_LEASE_SECONDS,
        repository: _Repository = None,
        branch: _Branch = None,
        commit: _Commit = None,
        pull_request: _PullRequest = None,
    ) -> Claim:
        """Keep your claim: it now lasts lease_seconds from now. Only the holder can renew, and
        only while the claim has not lapsed. Say where the work is now (branch, commit,
        pull_request) to change what the claim shows; what you leave out stays as it was."""
        with _database_errors():
            return await claims.renew_claim(
                actor=_me(),
                node=parse_node_ref(node, "node"),
                lease_seconds=lease_seconds,
                where=clean_whereabouts(
                    repository=repository,
                    branch=branch,
                    commit=commit,
                    pull_request=pull_request,
                ),
            )

    @mcp.tool()
    async def release_claim(
        node: _Node,
        note: Annotated[
            str,
            Field(
                description="What the next person must know: how far you got, what is left, what "
                "you found. A blank note is refused."
            ),
        ],
        repository: _Repository = None,
        branch: _Branch = None,
        commit: _Commit = None,
        pull_request: _PullRequest = None,
    ) -> Claim:
        """Give up your claim so someone else can take the work, and leave a handoff note.

        Only the holder can release. Work that was in progress goes back to not started, so it is
        free again; use finish_work to mark it done, blocked or dropped instead. Say where the
        work is (branch, commit, pull_request) and it is kept with the note for whoever takes it.
        """
        with _database_errors():
            return await claims.release_claim(
                actor=_me(),
                node=parse_node_ref(node, "node"),
                note=note,
                where=clean_whereabouts(
                    repository=repository,
                    branch=branch,
                    commit=commit,
                    pull_request=pull_request,
                ),
            )

    @mcp.tool(annotations=_READS)
    async def who_is_working() -> WhoIsWorking:
        """See who is working on what: every person or agent who has taken work or left a handoff
        note (each with its kind, person or agent), with the claims they hold and how long each
        has to run, the claims that ran out on them with no handoff note, and the last handoff
        note they left. Working first, then lapsed, then finished. Use it before taking work, to
        see who else is on the project and what they hold."""
        with _database_errors():
            return await claims.who_is_working()

    @mcp.tool()
    async def next_work(
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
        run_label: _RunLabel = None,
        client_name: _ClientName = None,
        repository: _Repository = None,
        branch: _Branch = None,
        commit: _Commit = None,
        pull_request: _PullRequest = None,
    ) -> NextWork:
        """Take the most urgent free story or task, and get everything you need to do it.

        Free work is a story or task that nobody holds, that is not waiting for anything, and that
        is ready (or whose earlier holder let their claim run out). A story whose tasks are still
        open is worked through its tasks. Urgent comes first, then the earliest target date, then
        the oldest. It is claimed for you in the same step, and exactly one agent gets each item
        however many ask at once.

        The context holds its epic and story, its acceptance criteria (its own and, for a task,
        its story's, each saying which item it is on), the project's definition of done with its
        checks (the items to tick on this one, each with its key), the
        notes linked to it or its parents (those a person decided first, past ones kept apart),
        every project-wide note (each with its status, so you can see whether a person confirmed
        it), the handoffs left by earlier holders, what it waits for, what waits for it, and
        similar items that are only suggestions. The item itself says how big it is (size). Finish
        it only when its acceptance criteria are met and the definition of done holds: do what
        each check says (run the tests, and so on), then tick each with set_check, saying how you
        checked. When nothing is free this is not an error: found
        is false and message says how much work is held, waiting or blocked.
        """
        with _database_errors():
            return await workflow.next_work(
                actor=_me(),
                within_node=None
                if within_node is None
                else parse_node_ref(within_node, "within_node"),
                peek=peek,
                lease_seconds=lease_seconds,
                context_chars=context_chars,
                where=clean_whereabouts(
                    repository=repository,
                    branch=branch,
                    commit=commit,
                    pull_request=pull_request,
                    run_label=run_label,
                    client_name=client_name,
                ),
            )

    @mcp.tool()
    async def finish_work(
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
        repository: _Repository = None,
        branch: _Branch = None,
        commit: _Commit = None,
        pull_request: _PullRequest = None,
        ask: _Ask = None,
    ) -> FinishResult:
        """End your work on a story or task: set where it stands and leave a handoff note.

        Say done only when its acceptance criteria are met and the project's definition of done
        holds: do what each of its checks says (run the tests, and so on), then tick each one with
        set_check, saying how you checked. Saying done while a check is unticked is refused, and
        the refusal names them. A check marked person_only is for a person to judge and you cannot
        tick it: finish as blocked and say in the note what the person should look at. One marked
        verifier_only is for a verifier that did not do the work (you cannot tick it if you did
        or are not one): finish as blocked and say in the note that it is ready for a verifier.
        Only the
        holder of the claim can finish. The claim is released and
        the note is kept with the item, and shown to whoever works on it, or on what waited for
        it, next. unblocked lists the work that became ready because of this. Say where the work
        is (branch, commit, pull_request): it is kept with the handoff note, and a verifier
        checks the pull request. Need a person to decide something? Add an ask with outcome
        blocked (the work waits) or released (it stays free): the answer comes back in next_work.
        """
        with _database_errors():
            return await workflow.finish_work(
                actor=_me(),
                node=parse_node_ref(node, "node"),
                outcome=outcome,
                note=note,
                where=clean_whereabouts(
                    repository=repository,
                    branch=branch,
                    commit=commit,
                    pull_request=pull_request,
                ),
                ask=None if ask is None else clean_ask(ask),
            )

    @mcp.tool()
    async def create_plan(
        items: Annotated[
            list[PlanItem] | None,
            Field(
                description="The epics, stories and tasks to create. Each has a key (a name only "
                "this plan uses, never written like a reference) so that parents, links and "
                "acceptance criteria can refer to it."
            ),
        ] = None,
        links: Annotated[
            list[PlanLink] | None,
            Field(
                description="Links between items, by key or by the reference of an existing node."
            ),
        ] = None,
        rules: Annotated[
            list[PlanRule] | None,
            Field(
                description="Acceptance criteria to record, each on a story or task by key or by "
                "the reference of an existing one."
            ),
        ] = None,
        draft: Annotated[
            bool,
            Field(
                description="True: propose it. Its work is held until a person approves the plan."
            ),
        ] = False,
        title: Annotated[
            str | None, Field(description="A draft's name, as a person sees it when deciding.")
        ] = None,
        template: Annotated[
            str | None, Field(description="Make the plan from this saved template, not items.")
        ] = None,
        values: Annotated[
            dict[str, str] | None, Field(description="The template's variables, by name.")
        ] = None,
    ) -> PlanResult:
        """Create a whole plan at once: epics, stories, tasks, links and acceptance criteria, or
        nothing.

        It is all checked first. If anything is wrong, nothing is created and every problem is
        listed, each naming its item. There is no limit on how many items a plan has. Give each
        story and task a size (xs to xl) as you plan it. Each kind is
        numbered in the order the plan lists it, and every item created is returned with its key and
        its reference (such as story:12). For each new epic or story that looks like something
        that already exists, similar says so; it was created anyway. With draft the plan is only
        proposed: nothing in it can be claimed until a person approves it (one decision for the
        whole plan), and if they reject it all of it is dropped with their reason. With template
        (and values) the plan is made from a saved template instead of items.
        """
        with _database_errors():
            return await workflow.create_plan(
                actor=_me(),
                items=items or [],
                links=links or [],
                rules=rules or [],
                draft=draft,
                title=title,
                template=template,
                values=values,
            )

    @mcp.tool()
    async def add_gate(
        node: _Node,
        kind: Annotated[
            str, Field(description="timer (opens at not_before) or approval (a person says yes).")
        ],
        not_before: Annotated[
            str | None, Field(description="For a timer: YYYY-MM-DD or YYYY-MM-DDTHH:MM, UTC.")
        ] = None,
        question: Annotated[
            str | None, Field(description="For an approval: what the person is asked to approve.")
        ] = None,
    ) -> GateState:
        """Hold a story or task until something outside the graph happens.

        A held item is never ready, next_work does not hand it out and it cannot be claimed. A
        timer opens by itself at its time. An approval opens only when a person answers yes (it
        is an ask: it shows on the Needs a person page); no agent can open a gate except a
        timer, and a person can lift any gate. get_node lists an item's gates.
        """
        with _database_errors():
            return await gates.add_gate(
                actor=_me(),
                node=parse_node_ref(node, "node"),
                kind=kind,
                question=question,
                not_before=not_before,
            )

    @mcp.tool()
    async def remove_gate(
        gate: Annotated[int, Field(description="The gate's number, from get_node.")],
        reason: Annotated[str | None, Field(description="Why it is lifted. Optional.")] = None,
    ) -> GateState:
        """Lift a gate so that the work is free again. Only a person can: the board calls this for
        the person who is signed in, and an agent's call is refused."""
        with _database_errors():
            return await gates.remove_gate(actor=_me(), gate=gate, reason=reason)

    @mcp.tool(annotations=_READS)
    async def list_plans(
        status: Annotated[
            str | None,
            Field(
                description="proposed (waits for a person), changes_requested (waits for the "
                "agent to revise it), approved or rejected. Leave out for all."
            ),
        ] = None,
        plan: Annotated[
            int | None,
            Field(description="One plan's number: read only it, whole, with every item it made."),
        ] = None,
        revision: Annotated[
            int | None,
            Field(description="With plan: an earlier revision to read as it was submitted."),
        ] = None,
        limit: _Limit = 100,
        after_id: _After = None,
    ) -> PlanPage:
        """List the plans that were proposed with draft, oldest first, a page at a time: who
        proposed each, what it made and where it stands. With plan, read that one whole: every
        item with its details, links and criteria, its revisions and the feedback on them."""
        with _database_errors():
            return await plans.list_plans(
                status=status, limit=limit, after_id=after_id or 0, plan=plan, revision=revision
            )

    @mcp.tool()
    async def approve_plan(
        plan: Annotated[int, Field(description="The plan's number, from list_plans.")],
        approved: Annotated[
            bool, Field(description="True to approve it, false to reject it and drop its work.")
        ],
        reason: Annotated[
            str | None, Field(description="Why. Needed to reject; optional to approve.")
        ] = None,
        revision: Annotated[
            int | None,
            Field(
                description="The revision you reviewed. A plan that has a newer one is refused, "
                "not decided."
            ),
        ] = None,
    ) -> PlanState:
        """Approve or reject a proposed plan as one decision. Only a person can: the board calls
        this for the person who is signed in, and an agent's call is refused. Approving frees all
        its work to be taken; rejecting drops all of it, with the reason on each item."""
        with _database_errors():
            return await plans.decide(
                actor=_me(), plan=plan, approved=approved, reason=reason, revision=revision
            )

    @mcp.tool()
    async def request_plan_changes(
        plan: Annotated[int, Field(description="The plan's number, from list_plans.")],
        revision: Annotated[int, Field(description="The revision you reviewed.")],
        summary: Annotated[str, Field(description="What you want changed, in a few words.")],
        comments: Annotated[
            list[PlanCommentInput] | None,
            Field(description="Comments on items of that revision, each on the item or a part."),
        ] = None,
    ) -> PlanState:
        """Ask for changes to a plan, with a summary and comments on its lines. Only a person can:
        the board calls this for the person who is signed in, and an agent's call is refused. The
        plan stays held, nothing is dropped, and the agent revises it for another review."""
        with _database_errors():
            return await plans.request_changes(
                actor=_me(), plan=plan, revision=revision, summary=summary, comments=comments
            )

    @mcp.tool()
    async def revise_plan(
        plan: Annotated[int, Field(description="The plan's number, from list_plans.")],
        items: Annotated[
            list[RevisionItem],
            Field(
                description="The whole plan as it should be now. An item that continues one the "
                "plan has gives its ref; one without a ref is new; one the plan has that no entry "
                "continues is removed (dropped)."
            ),
        ],
        links: Annotated[
            list[PlanLink] | None,
            Field(
                description="All the links of these items, by key or reference. Leave out to keep "
                "the links the kept items have."
            ),
        ] = None,
        rules: Annotated[
            list[RevisionRule] | None,
            Field(
                description="All the acceptance criteria, each with the id of the one it "
                "continues, or none for a new one. Leave out to keep the ones the kept items have."
            ),
        ] = None,
        note: Annotated[
            str | None, Field(description="What changed since the revision a person read.")
        ] = None,
    ) -> PlanRevised:
        """Revise a drafted plan that a person has read: send the whole plan as it should be now.
        It is checked as a whole and changes nothing if it is refused. The plan stays held, with
        the same number, and a person reads the new revision before any of its work can be
        taken."""
        with _database_errors():
            return await revisions.revise(
                actor=_me(), plan=plan, items=items, links=links, rules=rules, note=note
            )

    @mcp.tool(annotations=_READS)
    async def suggest_parent(
        node: Annotated[
            Ref | None,
            Field(description="A story or task that is saved: where else might it belong?"),
        ] = None,
        kind: Annotated[
            str | None,
            Field(description="For work that is not saved yet (title): story or task."),
        ] = None,
        title: Annotated[
            str | None, Field(description="The title of work that is not saved yet.")
        ] = None,
        body: Annotated[
            str | None, Field(description="The details of work that is not saved yet.")
        ] = None,
        limit: Annotated[
            int, Field(description="How many parents to give, from 1 to 10.")
        ] = DEFAULT_SUGGESTIONS,
    ) -> ParentSuggestions:
        """Say which epic a story, or which story a task, most likely belongs under, by meaning.

        Give node (saved work) or title with kind (work not saved yet). It ranks the epics (or
        stories) nearest first, each with a score and the items under it that the work most
        resembles, which is the reason. sure is true when the best is ahead of the second by at
        least 0.05. Every answer is a suggestion; to act on one, move_node, or
        propose_placement to ask a person. Needs search by meaning. An epic called Inbox is where
        unsorted work waits and is never offered.
        """
        with _database_errors():
            return await placement.suggest_parent(
                node=node, kind=kind, title=title, body=body, limit=limit
            )

    @mcp.tool()
    async def propose_placement(
        items: Annotated[
            list[Ref], Field(description="The stories or tasks (all of one kind), 1 to 20.")
        ],
        reason: Annotated[
            str, Field(description="Why they belong there, in words: a person reads it.")
        ],
        parent: Annotated[
            Ref | None,
            Field(description="The epic (for stories) or story (for tasks) that exists."),
        ] = None,
        new_epic: Annotated[
            NewEpic | None,
            Field(description="Or a new epic to group these stories under: title, and its body."),
        ] = None,
    ) -> PlacementState:
        """Propose moving stories or tasks under another parent, or grouping stories under a new
        epic. Nothing moves: a person accepts or rejects it on the board (the new epic is made only
        then). Give parent or new_epic, not both. Use suggest_parent to choose a parent. You may
        have at most 25 proposals waiting."""
        with _database_errors():
            return await placements.propose(
                actor=_me(),
                items=items,
                parent=parent,
                new_epic=new_epic,
                reason=reason,
            )

    @mcp.tool(annotations=_READS)
    async def list_placements(
        status: Annotated[
            str | None, Field(description="proposed, accepted or rejected. Leave out for all.")
        ] = None,
        limit: _Limit = 100,
        after_id: _After = None,
    ) -> PlacementPage:
        """List the proposals to move or group work, oldest first, a page at a time: who made
        each, what it would move and where it stands."""
        with _database_errors():
            return await placements.list_placements(
                status=status, limit=limit, after_id=after_id or 0
            )

    @mcp.tool()
    async def decide_placement(
        placement: Annotated[
            int, Field(description="The proposal's number, from list_placements.")
        ],
        accepted: Annotated[
            bool, Field(description="True to make the moves, false to reject the proposal.")
        ],
        reason: Annotated[
            str | None, Field(description="Why. Needed to reject; optional to accept.")
        ] = None,
    ) -> PlacementState:
        """Accept or reject a proposal to move work, as one decision. Only a person can: the board
        calls this for the person who is signed in, and an agent's call is refused. Accepting makes
        the moves as that person and leaves out an item that has moved or finished since."""
        with _database_errors():
            return await placements.decide(
                actor=_me(), placement=placement, accepted=accepted, reason=reason
            )

    @mcp.tool()
    async def save_plan_template(
        name: Annotated[str, Field(description="Lower case words, such as release-checklist.")],
        template: Annotated[
            dict[str, Any],
            Field(
                description="description, variables (each: description, type text or ref, kind, "
                "required, default, choices, pattern, max_length), items, links and rules written "
                "as create_plan takes them, with {{variable}} wherever a value goes."
            ),
        ],
    ) -> PlanTemplateInfo:
        """Save a plan template: a plan with variables, to make again with create_plan.

        A template is data, never code. It is checked whole (its shape, its variables and the plan
        it makes) and every problem is listed. Saving a name again replaces the template; every
        version stays in the history.
        """
        with _database_errors():
            return await workflow.save_template(actor=_me(), name=name, template=template)

    @mcp.tool(annotations=_READS)
    async def list_plan_templates(
        name: Annotated[
            str | None, Field(description="One template, with its whole text. Leave out for all.")
        ] = None,
        detail: Annotated[bool, Field(description="True: include each whole template.")] = False,
    ) -> PlanTemplatePage:
        """List the project's plan templates: each with its variables and how big the plan it
        makes is. Name one to read it whole."""
        with _database_errors():
            return await workflow.list_templates(name=name, detail=detail)

    @mcp.tool()
    async def delete_plan_template(
        name: Annotated[str, Field(description="The template's name.")],
    ) -> PlanTemplateInfo:
        """Delete a plan template (what was made from it stays). Its versions stay in the
        history."""
        with _database_errors():
            return await workflow.delete_template(actor=_me(), name=name)

    @mcp.tool()
    async def update_nodes(
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
            return await workflow.update_nodes(actor=_me(), changes=changes)

    @mcp.tool(annotations=_READS)
    async def changes_since(
        after_event_id: Annotated[
            int,
            Field(description="Pass 0 for the beginning, then the latest_event_id you got last."),
        ] = 0,
        actor: Annotated[
            str | None,
            Field(
                description="Only changes made by the person or agent with this name (this only "
                "filters what you read: who makes a change is never something you send)."
            ),
        ] = None,
        node: Annotated[
            Ref | None,
            Field(description="Only changes to this node (its reference, such as story:12)."),
        ] = None,
        rule_id: Annotated[
            int | None, Field(description="Only changes to this acceptance criterion (rule).")
        ] = None,
        note_id: Annotated[int | None, Field(description="Only changes to this note.")] = None,
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
        cursor: Annotated[
            str | None,
            Field(
                description="Instead of after_event_id: the cursor of the last answer, unchanged."
            ),
        ] = None,
    ) -> ChangesPage:
        """Show what changed after an event, oldest first, so you read only what is new.

        Keep the cursor of the answer (or latest_event_id) and pass it next time; while truncated
        is true ask again at once, and when it is false wait poll_after_seconds.
        Every event after the one you give is shown, in order, and none is skipped even when
        another change was still being written. Events are ordered by when their change began,
        which for two changes at the very same moment can differ from their ids. Give at most
        one of node, rule_id and note_id. To see only the newest changes, ask
        for newest, and walk back with before_event_id.
        """
        with _database_errors():
            return await workflow.changes_since(
                after_event_id=after_event_id,
                actor=actor,
                node=None if node is None else parse_node_ref(node, "node"),
                rule_id=rule_id,
                note_id=note_id,
                limit=limit,
                newest=newest,
                before_event_id=before_event_id,
                cursor=cursor,
            )

    @mcp.tool()
    async def create_rule(
        title: Annotated[str, Field(description="The acceptance criterion, stated briefly.")],
        body: Annotated[
            str, Field(description="Plain text or Markdown: the detail, or where it came from.")
        ] = "",
        status: Annotated[
            str, Field(description="open (still to be met), met or dropped. Open when left out.")
        ] = "open",
        node: Annotated[
            Ref | None,
            Field(
                description="The story or task it belongs to (its reference, such as story:12 or "
                "task:40; not an epic). Leave empty to place it later."
            ),
        ] = None,
    ) -> Rule:
        """Record an acceptance criterion (a rule): something that must hold for a story or a
        task to be done.

        It belongs to at most one story or task. One placed nowhere is unplaced: the coverage
        report lists it until someone places it. Acceptance criteria have their own ids, separate
        from the epics, stories and tasks, which are named by reference. When someone takes the
        work, the briefing holds the acceptance criteria of the item and, for a task, of its story.
        """
        with _database_errors():
            return await rules.create_rule(
                actor=_me(),
                title=title,
                body=body,
                status=status,
                node=None if node is None else parse_node_ref(node, "node"),
            )

    @mcp.tool(annotations=_READS)
    async def get_rule(rule_id: int) -> Rule:
        """Read one acceptance criterion, including the version you need to change it."""
        with _database_errors():
            return await rules.get_rule(rule_id)

    @mcp.tool(annotations=_READS)
    async def list_rules(
        node: Annotated[
            Ref | None,
            Field(
                description="Only the acceptance criteria placed directly on this story or task "
                "(its reference, such as story:12). Those on a story's tasks are not included."
            ),
        ] = None,
        unplaced: Annotated[
            bool, Field(description="Only the acceptance criteria placed nowhere yet.")
        ] = False,
        status: Annotated[str | None, Field(description="Only open, met or dropped.")] = None,
        detail: _Detail = False,
        limit: _Limit = 50,
        after_id: _After = None,
    ) -> RulePage:
        """List acceptance criteria, oldest first, a page at a time. Every filter is optional.
        Bodies are left out unless you ask for detail: see body_preview, or ask for detail."""
        with _database_errors():
            return await rules.list_rules(
                node=None if node is None else parse_node_ref(node, "node"),
                unplaced=unplaced,
                status=status,
                detail=detail,
                limit=limit,
                after_id=after_id,
            )

    @mcp.tool()
    async def update_rule(
        rule_id: int,
        expected_version: _Version,
        title: str | None = None,
        body: str | None = None,
        status: Annotated[
            str | None,
            Field(description="open (still to be met), met or dropped. Leave out to keep it."),
        ] = None,
    ) -> Rule:
        """Change an acceptance criterion's title, body or status. Leave out what should stay as
        it is.

        You must send the version you last read, so that nothing is overwritten silently. To change
        which story or task it belongs to, use place_rule.
        """
        with _database_errors():
            return await rules.update_rule(
                actor=_me(),
                rule_id=rule_id,
                expected_version=expected_version,
                title=title,
                body=body,
                status=status,
            )

    @mcp.tool()
    async def place_rule(
        rule_id: int,
        expected_version: _Version,
        node: Annotated[
            Ref,
            Field(
                description="The story or task that will hold the acceptance criterion (its "
                "reference, such as story:12 or task:40; not an epic)."
            ),
        ],
    ) -> Rule:
        """Put an acceptance criterion on a story or a task, or move it to another.

        It belongs to at most one story or task. One that is placed nowhere is put on this one;
        one that is already on another is taken off it and put on this one, as a single step, so
        it is never on two and never on none. The move is recorded in the history of the
        criterion and of both items. Placing it on the item it is already on is refused.
        """
        with _database_errors():
            return await rules.place_rule(
                actor=_me(),
                rule_id=rule_id,
                expected_version=expected_version,
                node=parse_node_ref(node, "node"),
            )

    @mcp.tool(annotations=_READS)
    async def rule_history(rule_id: int, limit: _Limit = 50, after_id: _After = None) -> EventPage:
        """Read everything that was ever done to one acceptance criterion, oldest first, a page
        at a time: who did it, when, and the changed fields as they were and as they became."""
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
            Field(description="Leave empty for the first page of unplaced acceptance criteria."),
        ] = None,
        stories_after: Annotated[
            Ref | None,
            Field(
                description="Leave empty for the first page of stories without acceptance "
                "criteria; pass next_stories_after for the next."
            ),
        ] = None,
        detail: _Detail = False,
    ) -> CoverageReport:
        """Show what is not matched up yet: the acceptance criteria placed nowhere, and the
        stories that have none (on the story or on any of its tasks). Dropped acceptance criteria
        and dropped stories are left out. Each list is a page; the counts cover all of it. A list
        you did not ask for comes back empty. Bodies are left out unless you ask for detail."""
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

    @mcp.tool(annotations=_READS)
    async def get_definition_of_done() -> DefinitionOfDone:
        """Read the project's definition of done: one text, set by a person, that says what every
        story or task must satisfy to be done, on top of its own acceptance criteria. Empty
        until someone sets it. Its list lines (starting with a dash or an asterisk) are its
        checks, which can be ticked on each story and task: items lists them, each with its key,
        whether only a person may tick it (person_only, for a line marked [person]), whether
        only a verifier may (verifier_only, for [verifier] or [github]), whether its evidence
        starts with a GitHub pull request address (github) and on how
        many items it is ticked. The answer holds the version you need to change it,
        and who changed it last and when. `next_work` already returns it to an agent."""
        with _database_errors():
            return await definition_of_done.get()

    @mcp.tool()
    async def set_definition_of_done(
        body: Annotated[
            str,
            Field(
                description="The whole definition of done, as plain text or Markdown. Write one "
                "line starting with a dash for each check, such as '- All tests pass'; start the "
                "words with [person] for a check only a person can tick, such as '- [person] "
                "Looks right on a phone', with [verifier] for one a verifier that did not do the "
                "work (or a person) ticks, or with [github] for the same with a GitHub pull "
                "request address as the start of its evidence, such as '- [github] The pull "
                "request is merged and its checks passed'. Other lines are context and cannot be "
                "ticked. Leave it empty to clear it."
            ),
        ],
        expected_version: Annotated[
            int,
            Field(
                description="The version you last read (from get_definition_of_done or the last "
                "change). Nothing set yet is version 1."
            ),
        ],
    ) -> DefinitionOfDone:
        """Replace the project's definition of done. Only a person may: an agent's call is
        refused, and a person does it on the board, under Settings, Definition of done.

        You must send the version you last read, so that nothing is overwritten silently. The
        change is recorded in the history with the text before and after. A tick of a check
        whose line is changed or taken out stays in the history, and no longer counts.
        """
        with _database_errors():
            return await definition_of_done.set(body=body, expected_version=expected_version)

    @mcp.tool()
    async def set_check(
        node: Annotated[
            Ref,
            Field(
                description="The story or task, by its reference, such as task:40. An epic has no "
                "checks of its own."
            ),
        ],
        item: Annotated[
            str,
            Field(
                description="The check: its key (8 characters, as get_node lists them with the "
                "item's checks, and next_work with its definition_checks) or its exact words."
            ),
        ],
        checked: Annotated[bool, Field(description="True to tick it, false to untick it.")] = True,
        evidence: Annotated[
            str | None,
            Field(
                description="How you checked it, in a short plain text, such as '212 tests pass' "
                "or 'tried it on a phone'. Needed to tick as an agent; a person may leave it "
                "out. When unticking, it may say why."
            ),
        ] = None,
    ) -> NodeChecks:
        """Tick or untick one check of the project's definition of done on a story or task.

        The checks are the list lines of the definition of done, which a person writes; next_work
        returns the ones for the item you hold and get_node lists them with who ticked each and
        when. Before you finish an item as done, do what each check says (run the tests, and so
        on), then tick each one, saying in evidence how you checked: an agent cannot tick
        without it, and cannot finish an item as done while a check is unticked. Ticking is
        recorded in the item's history under your name, and does not change the item's version.
        Ticking a check that is already ticked with the same evidence, or unticking one that is
        not ticked, is refused as nothing to change. A check marked person_only is for a person to
        judge: you may neither tick nor untick it, so leave it, finish the item as blocked, and
        say in the handoff note what the person should look at. A check marked verifier_only is
        for a person or a verifier (an agent whose key the owner marked as one) that did not
        do the work on the item, meaning it never claimed or handed it off: any other agent may
        neither tick nor untick it. A verifier's tick of a check marked github starts its
        evidence with the address of the GitHub pull request, then says what was seen there;
        Vrum does not ask GitHub, so look with gh on your own machine (scripts/verify_pr.py
        does it). The answer lists all the item's checks.
        """
        with _database_errors():
            return await checks.set_check(
                actor=_me(),
                node=parse_node_ref(node, "node"),
                item=item,
                checked=checked,
                evidence=evidence,
            )

    @mcp.tool()
    async def add_note(
        text: Annotated[
            str,
            Field(
                description="What is known or decided, in plain words. Its first line is its "
                "headline in every list: a note has no title."
            ),
        ],
        why: Annotated[
            str,
            Field(
                description="Why it is so or why it was chosen: where it came from, who said it, "
                "what was observed, or the reason. Never blank."
            ),
        ],
        decided_by: Annotated[
            str | None,
            Field(
                description="Only when a person decided it: the name of that person. This is "
                "not you: you are recorded separately, by your key. Leave empty for a note "
                "nobody decided."
            ),
        ] = None,
        decided_on: _DecidedOn = None,
        project_wide: Annotated[
            bool,
            Field(
                description="True when it applies throughout the project, not to particular "
                "work: every recall and briefing of the project then includes it while it is "
                "in force."
            ),
        ] = False,
        nodes: _NodeRefs = None,
        rule_ids: _RuleIds = None,
        note_ids: _NoteIds = None,
    ) -> Note:
        """Record a note: what is known or decided, with why, and what it concerns.

        A note is a record and is never edited. To change one, supersede it; to give it up,
        withdraw it. When a person decided it, name them in decided_by: recall lists such notes
        first. It starts unconfirmed: confirming is a separate step that only a person can take,
        on the board, so recording a note yourself never counts as a person's review. Link it to
        what it concerns (nodes, by reference, rule_ids and note_ids) so that recall finds it
        for those items.
        """
        with _database_errors():
            return await notes.add_note(
                actor=_me(),
                text=text,
                why=why,
                decided_by=decided_by,
                decided_on=decided_on,
                project_wide=project_wide,
                nodes=parse_node_refs(nodes, "nodes"),
                rule_ids=rule_ids,
                note_ids=note_ids,
            )

    @mcp.tool(annotations=_READS)
    async def get_note(note_id: int) -> Note:
        """Read one note: its text and why, who decided it if anyone did, its status (and who
        confirmed it), what replaced it or what it replaced, and what it concerns."""
        with _database_errors():
            return await notes.get_note(note_id)

    @mcp.tool(annotations=_READS)
    async def list_notes(
        status: Annotated[
            str | None,
            Field(
                description="Only unconfirmed, confirmed, superseded or withdrawn, or in_force "
                "for unconfirmed and confirmed together."
            ),
        ] = None,
        project_wide: Annotated[
            bool | None,
            Field(description="True: only project-wide notes. False: only the others."),
        ] = None,
        decided: Annotated[
            bool | None,
            Field(description="True: only notes a person decided. False: only the others."),
        ] = None,
        node: Annotated[
            Ref | None,
            Field(
                description="Only what concerns exactly this node (its reference, such as "
                "story:12)."
            ),
        ] = None,
        rule_id: Annotated[
            int | None,
            Field(description="Only what concerns exactly this acceptance criterion (rule)."),
        ] = None,
        note_id: Annotated[
            int | None, Field(description="Only what concerns exactly this note.")
        ] = None,
        within_node: Annotated[
            Ref | None,
            Field(
                description="Only what concerns this epic, story or task (its reference, such as "
                "epic:3), or anything under it, or an acceptance criterion on one of those. One "
                "walk over a whole area."
            ),
        ] = None,
        limit: _Limit = 50,
        after_id: _After = None,
    ) -> NotePage:
        """List notes, oldest first, a page at a time. Every filter is optional. For the
        project-wide notes a person decided that are in force, ask for project_wide=true,
        decided=true and status=in_force.

        The answer says how many match in all, so you can walk every page and check that none was
        skipped. For one area of the project, use within_node.
        """
        with _database_errors():
            return await notes.list_notes(
                status=status,
                project_wide=project_wide,
                decided=decided,
                node=None if node is None else parse_node_ref(node, "node"),
                rule_id=rule_id,
                note_id=note_id,
                within_node=None
                if within_node is None
                else parse_node_ref(within_node, "within_node"),
                limit=limit,
                after_id=after_id,
            )

    @mcp.tool()
    async def link_note(
        note_id: int, nodes: _NodeRefs = None, rule_ids: _RuleIds = None, note_ids: _NoteIds = None
    ) -> Note:
        """Say that a note in force also concerns more nodes, acceptance criteria or notes. Nothing
        is changed if any reference or id is wrong or already linked. A superseded or withdrawn
        note keeps the links it had."""
        with _database_errors():
            return await notes.link_note(
                actor=_me(),
                note_id=note_id,
                nodes=parse_node_refs(nodes, "nodes"),
                rule_ids=rule_ids,
                note_ids=note_ids,
            )

    @mcp.tool()
    async def unlink_note(
        note_id: int, nodes: _NodeRefs = None, rule_ids: _RuleIds = None, note_ids: _NoteIds = None
    ) -> Note:
        """Say that a note in force no longer concerns some nodes, acceptance criteria or notes.
        Nothing is changed if any of them is not linked. A superseded or withdrawn note keeps the
        links it had."""
        with _database_errors():
            return await notes.unlink_note(
                actor=_me(),
                note_id=note_id,
                nodes=parse_node_refs(nodes, "nodes"),
                rule_ids=rule_ids,
                note_ids=note_ids,
            )

    @mcp.tool()
    async def supersede_note(
        replaces: Annotated[
            list[int],
            Field(
                description="The ids of the notes in force that the new note replaces. Any number."
            ),
        ],
        text: Annotated[str, Field(description="The new note: what is known or decided now.")],
        why: Annotated[str, Field(description="Why it changed or why it is so. Never blank.")],
        decided_by: Annotated[
            str | None,
            Field(
                description="Only when a person decided the new note: the name of that person. "
                "Not you: you are recorded separately, by your key."
            ),
        ] = None,
        decided_on: _DecidedOn = None,
        project_wide: Annotated[
            bool | None,
            Field(
                description="Whether the new note is project-wide. Leave empty to keep it "
                "project-wide when any note it replaces was."
            ),
        ] = None,
        keep_links: Annotated[
            bool,
            Field(
                description="True: the new note concerns what the old ones did. False: it starts "
                "with only the references and ids you give."
            ),
        ] = True,
        nodes: _NodeRefs = None,
        rule_ids: _RuleIds = None,
        note_ids: _NoteIds = None,
        said_by: _SaidBy = None,
        quote: _Quote = None,
    ) -> NoteChange:
        """Replace one or more notes with a newer one, in one step.

        Use this when something is no longer so as it was recorded, or when what was decided
        changes: list every old note in replaces. The old ones are kept, marked superseded, and
        point to the new one, so nothing is lost or overwritten. Recall shows them as past notes,
        never as in force. The new note starts unconfirmed. If any of them does not exist or is
        not in force, nothing is changed. A note a person confirmed can be replaced only with
        said_by and quote.
        """
        with _database_errors():
            return await notes.supersede_note(
                actor=_me(),
                replaces=replaces,
                text=text,
                why=why,
                decided_by=decided_by,
                decided_on=decided_on,
                project_wide=project_wide,
                keep_links=keep_links,
                nodes=parse_node_refs(nodes, "nodes"),
                rule_ids=rule_ids,
                note_ids=note_ids,
                said_by=said_by,
                quote=quote,
            )

    @mcp.tool()
    async def withdraw_note(
        note_ids: Annotated[
            list[int], Field(description="The ids of the notes in force that are given up.")
        ],
        reason: Annotated[str, Field(description="Why they are given up. Never blank.")],
        said_by: _SaidBy = None,
        quote: _Quote = None,
    ) -> NoteChange:
        """Give one or more notes up, in one step, without replacing them. Nothing is deleted:
        each stays as a record, marked withdrawn, with the reason, and recall shows it as no
        longer in force.

        If any of them does not exist or is not in force, nothing is changed. A note a person
        confirmed can be withdrawn only with said_by and quote.
        """
        with _database_errors():
            return await notes.withdraw_note(
                actor=_me(), note_ids=note_ids, reason=reason, said_by=said_by, quote=quote
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
            int | None,
            Field(description="Or the acceptance criterion (rule) to recall what is known for."),
        ] = None,
        show: Annotated[
            str,
            Field(
                description="all, or one of notes, project_wide_notes, past_notes or "
                "acceptance_criteria to page through just that section."
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
                description="True: also return notes that are alike in meaning but not linked, "
                "as suggestions in a section of their own."
            ),
        ] = False,
        max_chars: Annotated[
            int,
            Field(
                description="The most characters of entries one answer holds, from 1000 to "
                "1000000. Entries that do not fit are left out: read on with show and after_id."
            ),
        ] = DEFAULT_RECALL_CHARS,
    ) -> Recall:
        """Recall everything known that matters for one node or acceptance criterion, in separate
        sections.

        Notes are what is known or decided, each with why: a note a person decided comes before
        the others in a section. Project-wide notes apply throughout the project.
        Acceptance criteria are what the item must satisfy: its own, and for a task its story's
        too (each says which item it is on). Every
        note has a status: unconfirmed (not yet reviewed), confirmed (a named person reviewed
        it), or, for past notes, superseded or withdrawn, which are no longer in force. Every
        entry says why it is there. Only exact links are followed, nothing is inferred by
        meaning: what is linked to this item and to the story and epic it belongs to is included
        (each entry says which, and `next_work` returns the same), and what is linked to its
        children is not, so recall those too if they matter. With with_similar, a last section
        adds what is alike in meaning but not linked, as suggestions.
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
                max_chars=max_chars,
            )

    @mcp.tool(annotations=_READS)
    async def list_asks(
        status: Annotated[
            str | None,
            Field(description="open (not yet answered) or answered. Leave out for all of them."),
        ] = None,
        node: Annotated[
            Ref | None, Field(description="Only the asks about this story or task.")
        ] = None,
        limit: _Limit = 100,
        after_id: _After = None,
    ) -> AskPage:
        """List the questions and approvals that were put to a person, oldest first, a page at a
        time: what was asked, by whom, and the answer when there is one. An agent that put an ask
        reads the answer in get_node and next_work; this lists them across the project."""
        with _database_errors():
            return await ask_book.list_asks(
                status=status,
                node=None if node is None else parse_node_ref(node, "node"),
                limit=limit,
                after_id=after_id or 0,
            )

    @mcp.tool()
    async def answer_ask(
        ask: Annotated[int, Field(description="The ask's number (Q-12 is 12).")],
        answer: Annotated[
            str,
            Field(
                description="Yes or no for an approval, one of the choices for a question that "
                "offers some, otherwise your answer in words."
            ),
        ],
        comment: Annotated[
            str | None, Field(description="Anything to add to the answer. Optional.")
        ] = None,
    ) -> AskState:
        """Answer an ask. Only a person can: the board calls this for the person who is signed
        in, and an agent's call is refused. The answer is final and is never edited. If the ask
        blocked the work, the work is free to take again once no other ask blocks it."""
        with _database_errors():
            return await ask_book.answer_ask(actor=_me(), ask=ask, answer=answer, comment=comment)

    @mcp.tool(annotations=_READS, meta=view_meta())
    async def needs_a_person() -> Annotated[CallToolResult, NeedsAPerson]:
        """Everything that is waiting for a person, in one list by story: open asks, checks that
        only a person can tick on work that is otherwise finished, notes waiting for confirmation
        on work in progress, and work whose claim ran out twice. Each row says why it is here,
        how long it has waited and who is waiting. A host that draws interactive views shows it
        as a list; it only reads, and a person answers on the board."""
        with _database_errors():
            found = await needs.needs_a_person()
        project = current_project()
        if project is not None:
            found.board_address = f"{settings.public_url}/board/p/{project}"
        return CallToolResult(
            content=[TextContent(text=summary(found))],
            structured_content=found.model_dump(mode="json", by_alias=True),
        )

    @mcp.tool()
    async def set_confirmation(
        note_id: int,
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
                "confirmation no longer stands (to unconfirm)."
            ),
        ],
        quote: Annotated[
            str | None,
            Field(
                description="What the person said, or where. Optional to confirm; needed to "
                "unconfirm."
            ),
        ] = None,
    ) -> Note:
        """Record that a named person reviewed a note, or take that back. Only a person can: the
        board calls this for the person who is signed in, and an agent's call is refused,
        whatever name it gives. Ask a person to confirm on the board.

        Confirmation says a person looked at the note, not that Vrum proved it right: it is the
        note's status, confirmed instead of unconfirmed. A note starts unconfirmed, and recording
        one yourself is never a person's confirmation. Only the status of a note in force
        changes: who confirmed a superseded or withdrawn note, and when, stays in its history.
        The change is logged with who made it.
        """
        with _database_errors():
            return await notes.set_confirmation(
                actor=_me(),
                note_id=note_id,
                confirmation=confirmation,
                person=person,
                quote=quote,
            )

    @mcp.tool(annotations=_READS)
    async def note_history(note_id: int, limit: _Limit = 50, after_id: _After = None) -> EventPage:
        """Read everything that was ever done to one note, oldest first, a page at a time: who
        recorded, linked, confirmed, replaced or withdrew it, and when. A person who changed their
        mind, and the agent that passed it on, are both in it."""
        with _database_errors():
            return await notes.note_history(note_id=note_id, limit=limit, after_id=after_id)

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
            Field(
                description="Only these types: epic, story, task, rule (acceptance criteria), note."
            ),
        ] = None,
        statuses: Annotated[
            list[str] | None,
            Field(
                description="Only these statuses, such as ready, done, open, met, confirmed or "
                "withdrawn. An acceptance criterion is open, met or dropped, and a note "
                "unconfirmed, confirmed, superseded or withdrawn."
            ),
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
        """Search epics, stories, tasks, acceptance criteria and notes.

        Each result says how it matched (words, meaning or both), why, and shows a snippet with
        the matched words between « and ». Matches by meaning alone are suggestions. A note's
        status says whether it is unconfirmed or confirmed (in force) or superseded or withdrawn
        (not). The answer counts the matches by type and by status. Exact words are found the
        moment something is saved, and keep working when the
        embedding service is down; the answer says when meaning could not be used.
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
        rule_id: Annotated[
            int | None, Field(description="Or an acceptance criterion (rule).")
        ] = None,
        note_id: Annotated[int | None, Field(description="Or a note.")] = None,
        text: Annotated[str | None, Field(description="Or a piece of text.")] = None,
        types: Annotated[
            list[str] | None,
            Field(description="Only these types, such as note or story."),
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

        Give exactly one of node, rule_id, note_id or text. The item itself is
        left out. Every result is a suggestion. It needs the embedding service and the index; if
        either is not ready it says so and changes nothing.
        """
        with _database_errors():
            return await searcher.similar_items(
                node=None if node is None else parse_node_ref(node, "node"),
                rule_id=rule_id,
                note_id=note_id,
                text=text,
                types=types,
                min_similarity=min_similarity,
                limit=limit,
                offset=offset,
            )

    @mcp.tool(annotations=_READS)
    async def find_overlaps(
        item_type: Annotated[
            str,
            Field(
                description="story (the default), epic, task or rule (acceptance criteria): what "
                "to compare."
            ),
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
            bool,
            Field(
                description="True: also compare dropped stories, epics, tasks and acceptance "
                "criteria."
            ),
        ] = False,
        limit: Annotated[int, Field(description="How many pairs to return, from 1 to 200.")] = 20,
        offset: Annotated[
            int, Field(description="How many pairs to skip. Pass next_offset for the next page.")
        ] = 0,
    ) -> OverlapPage:
        """Find pairs of stories, epics, tasks or acceptance criteria in different epics that look
        like the same thing.

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
        rule_id: Annotated[
            int | None, Field(description="Or an acceptance criterion (rule).")
        ] = None,
        note_id: Annotated[int | None, Field(description="Or a note.")] = None,
        limit: _Limit = 50,
        offset: Annotated[
            int, Field(description="How many to skip. Pass next_offset for the next page.")
        ] = 0,
    ) -> MentionPage:
        """List the items whose text refers to one item, or, with no item given, every reference
        written anywhere in the graph.

        A reference is [[epic:3]], [[story:12]], [[task:40]], [[rule:3]] or [[note:7]] in the
        details of an epic, story, task or acceptance criterion, or the text or why of a note.
        Titles are not read. It is only
        text: it creates no link, changes no readiness and no order, and nothing is stored for it.
        This reads the text of everything when asked. Each mention says which item holds it, where
        and a snippet around it, and, for every reference, whether the item it names exists: one
        that does not is plainly missing. An item that refers to itself is not listed. For a
        note, an acceptance criterion or any item, use this to see what leans on it before
        changing it.
        """
        with _database_errors():
            return await references.find_mentions(
                node=None if node is None else parse_node_ref(node, "node"),
                rule_id=rule_id,
                note_id=note_id,
                limit=limit,
                offset=offset,
            )

    @mcp.tool()
    async def reindex() -> IndexReport:
        """Bring the semantic index up to date now: build it if it does not exist, then embed
        every epic, story, acceptance criterion and note that is new or changed.

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
        dates and size), link, rule (acceptance criterion), note (with its why, who decided it, its
        status and who confirmed it, and what it concerns), the definition of done, the ticks of
        its checks, and the event log.

        Each page holds items of one list (nodes, links, rules, notes, definition_of_done, checks
        or events), in a fixed order (nodes as epics, then stories, then tasks, each by number;
        the checks by node and item; the rest by id; the definition of done is at most one item).
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
        document: Annotated[
            dict[str, Any],
            Field(
                description="The whole export, as one object with format, version, counts and the "
                "lists nodes, links, rules, notes, definition_of_done, checks and events, as "
                "export_graph pages give them."
            ),
        ],
    ) -> ImportResult:
        """Load a whole export into this server, which must be empty. Every number, id, date,
        status, confirmation and event is kept, so what is exported afterwards is identical to the
        file, and the next new epic, story or task is numbered after the highest of its own kind.
        A file made by an older version is loaded too: one of version 5 has no sizes and no checks;
        one of version 4 has the story of each
        acceptance criterion read as the node it is on, and no definition of done; one of version
        3 has its active notes become confirmed or unconfirmed (as a person had confirmed them or
        not) and its held and settled acceptance criteria become open and met; one of version 1 or
        2 has its decisions and then its facts become notes, numbered from 1 in that order, and
        their links, confirmations and history go with them. A file made before names were
        verified (version 1) says as what its notes and events were made as unknown.

        It is all or nothing: the file is checked first and every problem in it is listed at once,
        and the database checks everything again as it is loaded. A server that already holds
        anything is refused, because the numbers in the file would clash with what is there: load
        it into a fresh one, or restore a backup. A file can be as big as one request may be (see
        VRUM_MAX_REQUEST_BYTES). scripts/import_graph.py sends a file for you. If an embedding
        service is set up, the server indexes the imported items by itself afterwards.
        """
        with _database_errors():
            result = await exchange.import_document(document)
        event(log, logging.INFO, "import_finished", actor=_me()[:100], counts=result.counts)
        return result

    @mcp.tool(annotations=_READS)
    async def explore(
        names: Annotated[
            list[str] | None,
            Field(
                description="The functions to describe, such as the ones you are about to use. "
                "Leave empty to list every function."
            ),
        ] = None,
        query: Annotated[
            str | None,
            Field(
                description="Words to look for in the functions' names and descriptions, such as "
                "note or claim. It gives the ten that fit best."
            ),
        ] = None,
    ) -> Explored:
        """See what `vrum` can do inside `run`, for a key that calls Vrum by running code.

        With nothing asked it lists the functions this key's scripts have, with a line on each and
        whether it only reads. With names, or with words to look for, it also gives the TypeScript
        of those functions in full: their descriptions and the exact types of their arguments and
        results, which is what a script is checked against before it runs. It changes nothing.
        """
        caller = current_caller()
        return describe_functions(mcp.sdk, mcp.functions_for(caller.tools), names, query)

    @mcp.tool()
    async def run(
        code: Annotated[
            str,
            Field(
                description="The body of an async function, written in TypeScript: `vrum` and "
                "`console` are in scope and `await` works. Return a JSON value to get it back."
            ),
        ],
        timeout_seconds: Annotated[
            int,
            Field(
                description=f"How long the script may run, in seconds, from 1 to {MOST_TIMEOUT}."
            ),
        ] = DEFAULT_TIMEOUT,
    ) -> RunResult:
        """Run a TypeScript script that calls Vrum's functions, for a key that calls Vrum by
        running code.

        Each function of `vrum` is the tool of that name: the same arguments, results and checks,
        recorded under this key's name, so one script can do what many calls would. The script is
        type checked first, then run in a sandbox with no network, files or imports. A call Vrum
        refuses throws a VrumError with the tool's own sentence. Every call is its own step and
        nothing is rolled back, so a script that stops half way says which calls ran and whether
        anything changed. It gives back what the script returned, what it wrote with console.log
        and the calls it made. This key's own functions are listed where this tool is described to
        it, and `explore` gives their types.
        """
        outcome = await code_mode.run(current_caller(), code, timeout_seconds)
        if not outcome.ok:
            raise Refusal(failure_words(outcome))
        return RunResult(result=outcome.result, logs=outcome.logs, calls=outcome.calls)

    @mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def health(request: Request) -> Response:
        try:
            await database.ping()
        except psycopg.Error:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    add_board(
        mcp,
        settings=settings,
        searcher=searcher,
        accounts=accounts,
        sso=sso,
        webhook_book=webhook_book,
    )
    return mcp
