"""MCP tools for acceptance criteria, definition of done, checks, notes, recall, and asks."""

from typing import Annotated

from mcp.types import CallToolResult, TextContent
from pydantic import Field

from vrum.ask_book import AskBook, AskPage
from vrum.asks import AskState
from vrum.checks import Checks, NodeChecks
from vrum.common import Ref, parse_node_ref, parse_node_refs
from vrum.config import Settings
from vrum.db import current_project
from vrum.definition_of_done import DefinitionOfDone, DefinitionOfDoneBook
from vrum.graph import EventPage
from vrum.guard import GuardedServer
from vrum.needs import Needs, NeedsAPerson
from vrum.needs_app import summary, view_meta
from vrum.notes import Note, NoteChange, NotePage, Notes
from vrum.recall import DEFAULT_RECALL_CHARS, Recall, Recaller
from vrum.rules import CoverageReport, Rule, RulePage, Rules
from vrum.tools import (
    _READS,
    _After,
    _DecidedOn,
    _Detail,
    _Limit,
    _me,
    _NodeRefs,
    _NoteIds,
    _Quote,
    _RuleIds,
    _SaidBy,
    _Version,
)


def register_context_tools(
    mcp: GuardedServer,
    *,
    settings: Settings,
    rules: Rules,
    definition_of_done: DefinitionOfDoneBook,
    checks: Checks,
    notes: Notes,
    recaller: Recaller,
    ask_book: AskBook,
    needs: Needs,
) -> None:
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
        return await notes.list_notes(
            status=status,
            project_wide=project_wide,
            decided=decided,
            node=None if node is None else parse_node_ref(node, "node"),
            rule_id=rule_id,
            note_id=note_id,
            within_node=None if within_node is None else parse_node_ref(within_node, "within_node"),
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
        return await ask_book.answer_ask(actor=_me(), ask=ask, answer=answer, comment=comment)

    @mcp.tool(annotations=_READS, meta=view_meta())
    async def needs_a_person() -> Annotated[CallToolResult, NeedsAPerson]:
        """Everything that is waiting for a person, in one list by story: open asks, checks that
        only a person can tick on work that is otherwise finished, notes waiting for confirmation
        on work in progress, and work whose claim ran out twice. Each row says why it is here,
        how long it has waited and who is waiting. A host that draws interactive views shows it
        as a list; it only reads, and a person answers on the board."""
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
        return await notes.note_history(note_id=note_id, limit=limit, after_id=after_id)
