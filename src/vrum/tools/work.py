"""MCP tools for work items (nodes), links, claims, and server status."""

from typing import Annotated

from pydantic import Field

from vrum.asks import clean_ask
from vrum.claims import DEFAULT_LEASE_SECONDS, Claim, Claims, WhoIsWorking
from vrum.common import Ref, parse_day, parse_node_ref
from vrum.graph import EventPage, Graph, Node, NodeDetail, NodePage
from vrum.guard import GuardedServer
from vrum.links import Link, LinkPage, Links
from vrum.status import ServerStatus, Status
from vrum.tools import (
    _READS,
    _After,
    _AfterNode,
    _Ask,
    _Branch,
    _ClientName,
    _Commit,
    _Detail,
    _Lease,
    _Limit,
    _me,
    _Node,
    _PullRequest,
    _Repository,
    _RunLabel,
    _Size,
    _StartOn,
    _TargetOn,
    _Version,
)
from vrum.whereabouts import clean_whereabouts
from vrum.workflow import CreatedNode, Workflow


def register_work_tools(
    mcp: GuardedServer,
    *,
    app_version: str,
    status: Status,
    graph: Graph,
    links: Links,
    claims: Claims,
    workflow: Workflow,
) -> None:
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
        return await status.server_status(app_version)

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
        return await claims.who_is_working()
