"""MCP tools for agent workflow, plans, gates, placements, templates, and the change feed."""

from typing import Annotated, Any

from pydantic import Field

from vrum.asks import clean_ask
from vrum.claims import DEFAULT_LEASE_SECONDS
from vrum.common import Ref, parse_node_ref
from vrum.gate_reads import GateState
from vrum.gates import GateBook, PlanBook, PlanPage, PlanState
from vrum.guard import GuardedServer
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
from vrum.tools import (
    _READS,
    _After,
    _Ask,
    _Branch,
    _ClientName,
    _Commit,
    _Lease,
    _Limit,
    _me,
    _Node,
    _PullRequest,
    _Repository,
    _RunLabel,
)
from vrum.whereabouts import clean_whereabouts
from vrum.workflow import (
    DEFAULT_CONTEXT_CHARS,
    ChangesPage,
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


def register_workflow_tools(
    mcp: GuardedServer,
    *,
    workflow: Workflow,
    gates: GateBook,
    plans: PlanBook,
    revisions: PlanReviser,
    placement: Placement,
    placements: PlacementBook,
) -> None:
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
        return await workflow.next_work(
            actor=_me(),
            within_node=None if within_node is None else parse_node_ref(within_node, "within_node"),
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
        return await placements.list_placements(status=status, limit=limit, after_id=after_id or 0)

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
        return await workflow.list_templates(name=name, detail=detail)

    @mcp.tool()
    async def delete_plan_template(
        name: Annotated[str, Field(description="The template's name.")],
    ) -> PlanTemplateInfo:
        """Delete a plan template (what was made from it stays). Its versions stay in the
        history."""
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
