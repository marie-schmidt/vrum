"""The MCP server: its tools, its health check, and how it is wired to the database."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from importlib.metadata import version
from urllib.parse import urlparse

import psycopg
from mcp.server import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from vrum.accounts import Accounts
from vrum.ask_book import AskBook
from vrum.board import add_board
from vrum.checks import Checks
from vrum.claims import Claims
from vrum.code_mode import CodeMode
from vrum.config import DEFAULT_MIN_SIMILARITY, DEFAULT_OVERLAP_THRESHOLD, Settings
from vrum.db import Database
from vrum.definition_of_done import DefinitionOfDoneBook
from vrum.exchange import Exchange
from vrum.gates import GateBook, PlanBook
from vrum.graph import Graph
from vrum.guard import GuardedServer
from vrum.indexing import Indexer
from vrum.links import Links
from vrum.mail import Mailer
from vrum.needs import Needs
from vrum.needs_app import needs_apps
from vrum.notes import Notes
from vrum.placement import Placement, PlacementBook
from vrum.plan_revise import PlanReviser
from vrum.recall import Recaller
from vrum.references import References
from vrum.rules import Rules
from vrum.search import Searcher
from vrum.sso import Sso
from vrum.status import Status
from vrum.tools.context import register_context_tools
from vrum.tools.system import register_system_tools
from vrum.tools.work import register_work_tools
from vrum.tools.workflow import register_workflow_tools
from vrum.webhooks import Dispatcher, WebhookBook
from vrum.workflow import Workflow

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


def create_server(settings: Settings) -> GuardedServer:
    app_version = version("vrum")
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
        version=app_version,
        lifespan=lifespan,
        instructions=INSTRUCTIONS,
        extensions=[needs_apps()],
    )
    mcp.accounts = accounts
    code_mode = CodeMode(settings.sandbox_socket, mcp)

    register_work_tools(
        mcp,
        app_version=app_version,
        status=status,
        graph=graph,
        links=links,
        claims=claims,
        workflow=workflow,
    )
    register_workflow_tools(
        mcp,
        workflow=workflow,
        gates=gates,
        plans=plans,
        revisions=revisions,
        placement=placement,
        placements=placements,
    )
    register_context_tools(
        mcp,
        settings=settings,
        rules=rules,
        definition_of_done=definition_of_done,
        checks=checks,
        notes=notes,
        recaller=recaller,
        ask_book=ask_book,
        needs=needs,
    )
    register_system_tools(
        mcp,
        searcher=searcher,
        references=references,
        indexer=indexer,
        exchange=exchange,
        code_mode=code_mode,
    )

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
