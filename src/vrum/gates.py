"""Gates and plan approval (M47): work that waits for something outside the graph.

A **gate** holds a story or task until something happens that the graph cannot see: a time comes
(a *timer*), or a person says yes to an *approval* (an ask of effect `gates`, see asks.py). While a
gate is waiting, or its approval was refused, the item is held: it is never ready, `next_work` does
not hand it out and it cannot be claimed (`node_state.held_by`). A timer opens by itself when its
time comes; an approval opens only when a person answers yes, so no agent can open a gate except a
timer. A gate is an event (`gate_added`) and lifting it is another (`gate_removed`, a person's: for
work that no longer needs it), so a gate is part of the history, travels through an export, and is
never edited. `gate_state` (schema.sql) reads them.

A **plan** made with `draft` is proposed: it creates its epics, stories and tasks as usual (one
`plan_proposed` event names them), but they are held until a person approves the whole plan as one
decision, or rejects it, which drops all of them with the person's reason. `plan_state` reads the
events. Plan templates are workflow.py's and plan_templates.py's."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from vrum.asks import ASK_ANSWERED, AskInput, clean_ask, raise_ask
from vrum.common import (
    Refusal,
    fetch_all,
    fetch_one,
    node_order,
    page_size,
    record_event,
    refuse_secrets,
    split_page,
)
from vrum.db import Database
from vrum.gate_reads import GATE_COLUMNS, GateState, gate_from
from vrum.graph import change_status, require_node
from vrum.plan_checks import PlanCheck, duplicate_check, local_checks, unindexed_elsewhere
from vrum.plan_revisions import (
    PlanComment,
    PlanCommentInput,
    PlanContext,
    PlanCriterion,
    PlanDrift,
    PlanFeedback,
    PlanItemChange,
    PlanItemLink,
    PlanItemRead,
    PlanRevisionInfo,
    clean_comments,
    clean_summary,
    diff_revisions,
    drift_between,
)
from vrum.records import require_person
from vrum.search import Searcher

GATE_ADDED = "gate_added"
GATE_REMOVED = "gate_removed"
PLAN_PROPOSED = "plan_proposed"
PLAN_APPROVED = "plan_approved"
PLAN_REJECTED = "plan_rejected"
PLAN_REVISED = "plan_revised"
PLAN_CHANGES_REQUESTED = "plan_changes_requested"
GATE_KINDS = ("approval", "timer")
PLAN_STATES = ("proposed", "changes_requested", "approved", "rejected")
MOST_REASON_CHARS = 1000
MOST_TITLE_CHARS = 200
# The furthest ahead a timer may be set, so that a typo does not hold work for centuries.
MOST_DAYS_AHEAD = 3 * 366
# How many of a plan's items a reading lists (the count is always whole).
MOST_ITEMS_SHOWN = 50

PLAN_COLUMNS = (
    "id, title, state, items, revision, proposed_by, proposed_by_kind, proposed_at, revised_by, "
    "revised_by_kind, revised_at, decided_by, decided_at, reason"
)
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_MOMENT = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:Z|\+00:00)?")


class PlanState(BaseModel):
    id: int = Field(description="The plan's number: the id of the event that proposed it.")
    title: str
    state: str = Field(
        description="proposed (its current revision waits for a person), changes_requested (a "
        "person asked for changes to it and the agent has not revised it yet), approved or "
        "rejected."
    )
    revision: int = Field(
        description="The current revision: 1 is the plan as it was drafted, and each revision an "
        "agent sends after a request for changes is the next."
    )
    epics: int
    stories: int
    tasks: int
    items: list[str] = Field(
        description=f"The references of what the current revision holds, at most "
        f"{MOST_ITEMS_SHOWN}: epics first."
    )
    proposed_by: str
    proposed_by_kind: str
    proposed_at: datetime
    revised_by: str = Field(description="Who made the current revision (the proposer for 1).")
    revised_by_kind: str
    revised_at: datetime = Field(description="When the current revision was made.")
    decided_by: str | None = Field(description="The person who approved or rejected it.")
    decided_at: datetime | None
    reason: str | None = Field(description="Why it was rejected, or the person's comment.")
    shown: int | None = Field(
        description="The revision that tree, context and changes are about. Only when one plan is "
        "asked for (list_plans with plan, and revision to read an earlier one); null in a list."
    )
    tree: list[PlanItemRead] = Field(
        description="Every item of the revision shown, with its details, links and criteria, "
        "parents first. Only when one plan is asked for (list_plans with plan); empty in a list. "
        "A plan that is still waiting shows what its items are now; a decided plan, and an "
        "earlier revision, show what was submitted.",
    )
    context: list[PlanContext] = Field(
        description="Existing items the tree hangs under or links to. Only with plan.",
    )
    checks: list[PlanCheck] | None = Field(
        description="What to know before deciding: circles of waiting, items with no size and "
        "existing work it may duplicate, each ok, warning, failed or unavailable. Only for the "
        "current revision of a plan that waits for a person, read with plan; null otherwise. A "
        "check never stops or makes a decision."
    )
    revisions: list[PlanRevisionInfo] | None = Field(
        description="Every revision, oldest first, with who made it, when, what it says changed "
        "and the feedback a person gave on it. Only with plan; null in a list."
    )
    feedback: list[PlanFeedback] | None = Field(
        description="Everything a person said when asking for changes, oldest first, each with "
        "the revision it is about. Only with plan; null in a list."
    )
    changes: list[PlanItemChange] | None = Field(
        description="How the revision shown differs from the one before it: items added, "
        "removed and changed. Null for the first revision, and in a list."
    )
    drift: list[PlanDrift] | None = Field(
        description="Only for the current revision of a plan that is still held: the items that "
        "have been changed since it was submitted, outside a revision. While there are any it "
        "cannot be approved. Empty when none; null otherwise."
    )


class PlanPage(BaseModel):
    items: list[PlanState]
    total: int = Field(description="How many plans match in all, across pages.")
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )


def plan_from(row: dict[str, Any]) -> PlanState:
    refs: list[str] = list(row["items"] or [])
    kinds = [ref.split(":", 1)[0] for ref in refs]
    ordered = sorted(refs, key=lambda ref: ("epic", "story", "task").index(ref.split(":", 1)[0]))
    return PlanState(
        id=row["id"],
        title=row["title"],
        state=row["state"],
        revision=row["revision"],
        epics=kinds.count("epic"),
        stories=kinds.count("story"),
        tasks=kinds.count("task"),
        items=ordered[:MOST_ITEMS_SHOWN],
        proposed_by=row["proposed_by"],
        proposed_by_kind=row["proposed_by_kind"],
        proposed_at=row["proposed_at"],
        revised_by=row["revised_by"],
        revised_by_kind=row["revised_by_kind"],
        revised_at=row["revised_at"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        reason=row["reason"],
        shown=None,
        tree=[],
        context=[],
        checks=None,
        revisions=None,
        feedback=None,
        changes=None,
        drift=None,
    )


def _depth_first(items: list[PlanItemRead]) -> list[PlanItemRead]:
    """Parents before their children, each level in epic, story, task and number order."""
    inside = {one.ref for one in items}
    children: dict[str | None, list[PlanItemRead]] = {}
    for one in items:
        children.setdefault(one.parent if one.parent in inside else None, []).append(one)
    for group in children.values():
        group.sort(key=lambda one: node_order(one.ref))
    ordered: list[PlanItemRead] = []

    def walk(parent: str | None) -> None:
        for one in children.get(parent, []):
            ordered.append(one)
            walk(one.ref)

    walk(None)
    return ordered


async def live_items(conn: Any, refs: list[str]) -> list[PlanItemRead]:
    """The items as they are now, each with its details, its links that start at it and its
    acceptance criteria that are not dropped, parents first."""
    nodes = await fetch_all(
        conn,
        "SELECT ref, kind, parent, title, body, priority, size, start_on, target_on "
        "FROM node_state WHERE ref = ANY(%s)",
        (refs,),
    )
    links = await fetch_all(
        conn,
        "SELECT source, target, link_type FROM links WHERE source = ANY(%s) ORDER BY id",
        (refs,),
    )
    rules = await fetch_all(
        conn,
        "SELECT id, node, title, body, status FROM rules "
        "WHERE node = ANY(%s) AND status <> 'dropped' ORDER BY id",
        (refs,),
    )
    return _depth_first(
        [
            PlanItemRead(
                **row,
                links=[
                    PlanItemLink(link_type=one["link_type"], target=one["target"])
                    for one in links
                    if one["source"] == row["ref"]
                ],
                criteria=[
                    PlanCriterion(
                        id=one["id"], title=one["title"], body=one["body"], status=one["status"]
                    )
                    for one in rules
                    if one["node"] == row["ref"]
                ],
            )
            for row in nodes
        ]
    )


async def context_of(conn: Any, tree: list[PlanItemRead]) -> list[PlanContext]:
    """The existing items a tree hangs under or links to, which it did not make, epics first."""
    made = {one.ref for one in tree}
    outside: dict[str, str] = {}
    for item in tree:
        if item.parent and item.parent not in made:
            outside[item.parent] = "parent"
        for link in item.links:
            if link.target not in made:
                outside.setdefault(link.target, "linked")
    named = (
        await fetch_all(
            conn,
            "SELECT ref, kind, title FROM node_state WHERE ref = ANY(%s)",
            (list(outside),),
        )
        if outside
        else []
    )
    return sorted(
        (PlanContext(**row, why=outside[row["ref"]]) for row in named),
        key=lambda one: node_order(one.ref),
    )


async def _plan_tree(conn: Any, refs: list[str]) -> tuple[list[PlanItemRead], list[PlanContext]]:
    """What a plan holds, read whole: each item with its details, its links and its acceptance
    criteria, and the existing items the tree hangs under or links to."""
    tree = await live_items(conn, refs)
    return tree, await context_of(conn, tree)


async def plan_snapshot(conn: Any, refs: list[str]) -> list[dict[str, Any]]:
    """What a plan holds now, as it is kept in the event of a proposal or a revision."""
    return [one.model_dump(mode="json") for one in await live_items(conn, refs)]


async def _revision_events(conn: Any, plan: int) -> list[dict[str, Any]]:
    """The events of a plan's proposal, revisions and requests for changes, minus snapshots."""
    return await fetch_all(
        conn,
        "SELECT id, action, actor, actor_kind, at, after - 'snapshot' AS after FROM events "
        "WHERE (action = 'plan_proposed' AND id = %(plan)s) "
        "OR (action IN ('plan_revised', 'plan_changes_requested') "
        "AND (after->>'plan')::bigint = %(plan)s) ORDER BY id",
        {"plan": plan},
    )


async def snapshot_of(conn: Any, plan: int, revision: int) -> list[dict[str, Any]]:
    """What a plan held in one of its revisions, as it was submitted."""
    row = await fetch_one(
        conn,
        "SELECT after->'snapshot' AS snapshot FROM events WHERE "
        "(%(revision)s = 1 AND action = 'plan_proposed' AND id = %(plan)s) OR "
        "(action = 'plan_revised' AND (after->>'plan')::bigint = %(plan)s "
        "AND (after->>'revision')::integer = %(revision)s)",
        {"plan": plan, "revision": revision},
    )
    snapshot: list[dict[str, Any]] = [] if row is None else list(row["snapshot"] or [])
    return snapshot


def _history(
    events: list[dict[str, Any]],
) -> tuple[list[PlanRevisionInfo], list[PlanFeedback]]:
    revisions: list[PlanRevisionInfo] = []
    feedback: list[PlanFeedback] = []
    for event in events:
        after = event["after"]
        if event["action"] in ("plan_proposed", "plan_revised"):
            revisions.append(
                PlanRevisionInfo(
                    revision=1 if event["action"] == "plan_proposed" else int(after["revision"]),
                    by=event["actor"],
                    by_kind=event["actor_kind"],
                    at=event["at"],
                    note=after.get("note"),
                    items=len(after.get("items") or []),
                    removed=list(after.get("removed") or []),
                    feedback=None,
                )
            )
        else:
            feedback.append(
                PlanFeedback(
                    id=event["id"],
                    revision=int(after["revision"]),
                    by=event["actor"],
                    by_kind=event["actor_kind"],
                    at=event["at"],
                    summary=after["summary"],
                    comments=[PlanComment.model_validate(one) for one in after["comments"]],
                )
            )
    asked = {one.revision: one.id for one in feedback}
    return [
        one.model_copy(update={"feedback": asked.get(one.revision)}) for one in revisions
    ], feedback


async def _drift(conn: Any, refs: list[str], reviewed: list[dict[str, Any]]) -> list[PlanDrift]:
    """What has been changed in a held plan's items since the revision that was reviewed."""
    now = [one.model_dump(mode="json") for one in await live_items(conn, refs)]
    dropped = {
        row["ref"]
        for row in await fetch_all(
            conn, "SELECT ref FROM nodes WHERE ref = ANY(%s) AND status = 'dropped'", (refs,)
        )
    }
    return drift_between(now, reviewed, dropped)


def _drift_words(plan: int, revision: int, drift: list[PlanDrift]) -> str:
    shown = "; ".join(
        f"{one.ref.split(':')[0][0].upper()}-{one.ref.split(':')[1]} ({', '.join(one.fields)})"
        for one in drift[:6]
    )
    more = f" and {len(drift) - 6} more" if len(drift) > 6 else ""
    return (
        f"Plan {plan} was changed after revision {revision} was submitted, outside a revision: "
        f"{shown}{more}. What a person approves is what an agent submitted, so ask for changes, "
        "so that the agent sends a revision that says what the plan should be, or reject the "
        "plan. Nothing was changed."
    )


def _check_revision(plan: int, row: dict[str, Any], revision: int | None) -> None:
    """Refuse a decision about a revision that is not the current one."""
    if revision is not None and revision != row["revision"]:
        raise Refusal(
            f"Plan {plan} is at revision {row['revision']} now, and you reviewed revision "
            f"{revision}: it changed while you read it. Read revision {row['revision']}, then "
            "decide again. Nothing was changed."
        )


def _ended(plan: int, row: dict[str, Any]) -> Refusal | None:
    if row["state"] in ("approved", "rejected"):
        return Refusal(
            f"Plan {plan} was already {row['state']} by {row['decided_by']}. A plan is decided "
            "once."
        )
    return None


def clean_not_before(text: str) -> datetime:
    """A moment for a timer, as a UTC time: a day (YYYY-MM-DD, which means its start) or a day and a
    time (YYYY-MM-DDTHH:MM, optionally with seconds and Z). Anything else, a time in the past or
    one very far ahead, is refused in words."""
    cleaned = text.strip()
    parsed: datetime | None = None
    try:
        if _DAY.fullmatch(cleaned):
            parsed = datetime.fromisoformat(cleaned).replace(tzinfo=UTC)
        elif (found := _MOMENT.fullmatch(cleaned)) is not None:
            day, hour, minute, second = found.groups()
            parsed = datetime.fromisoformat(f"{day}T{hour}:{minute}:{second or '00'}").replace(
                tzinfo=UTC
            )
    except ValueError:
        parsed = None
    if parsed is None:
        raise Refusal(
            "not_before must be a day written as YYYY-MM-DD or a day and time such as "
            "2026-12-01T09:30 (UTC), but it is not one. Nothing was changed."
        )
    now = datetime.now(UTC)
    if parsed <= now:
        raise Refusal(
            f"not_before is {parsed:%Y-%m-%d %H:%M} UTC, which has already passed: a timer gate "
            "needs a time that is still to come. Nothing was changed."
        )
    if parsed > now + timedelta(days=MOST_DAYS_AHEAD):
        raise Refusal("not_before is more than three years away. Nothing was changed.")
    return parsed


def clean_reason(reason: str | None, what: str) -> str | None:
    if reason is None or not reason.strip():
        return None
    cleaned = reason.strip()
    if len(cleaned) > MOST_REASON_CHARS:
        raise Refusal(
            f"{what} may be at most {MOST_REASON_CHARS:,} characters. Nothing was changed."
        )
    refuse_secrets({what: cleaned})
    return cleaned


class GateBook:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add_gate(
        self,
        *,
        actor: str,
        node: str,
        kind: str,
        question: str | None,
        not_before: str | None,
    ) -> GateState:
        """Hold a story or task until a time comes (timer) or a person approves (approval)."""
        if kind not in GATE_KINDS:
            raise Refusal(f"kind must be one of {', '.join(GATE_KINDS)}, but it is {kind!r}.")
        if kind == "timer":
            if not_before is None or not_before.strip() == "":
                raise Refusal(
                    "A timer gate needs not_before: the time it opens. Nothing was changed."
                )
            if question is not None and question.strip():
                raise Refusal("A timer gate asks nobody: leave out question. Nothing was changed.")
            moment = clean_not_before(not_before)
            asked = None
        else:
            if not_before is not None and not_before.strip():
                raise Refusal(
                    "An approval gate opens when a person says yes, not at a time: leave out "
                    "not_before. Nothing was changed."
                )
            if question is None or not question.strip():
                raise Refusal(
                    "An approval gate needs question: what the person is asked to approve. "
                    "Nothing was changed."
                )
            asked = clean_ask(AskInput(question=question, approval=True))
            moment = None
        async with self._database.transaction() as conn:
            current = await require_node(conn, node, lock=True)
            if current["kind"] == "epic":
                raise Refusal(
                    f"{node} is an epic. A gate holds a story or task: put it on the one that has "
                    "to wait. Nothing was changed."
                )
            if current["status"] in ("done", "dropped"):
                raise Refusal(
                    f"{node} is {current['status']}, so there is nothing left to hold. Nothing "
                    "was changed."
                )
            if moment is not None:
                gate_id = await record_event(
                    conn,
                    node=node,
                    action=GATE_ADDED,
                    actor=actor,
                    before=None,
                    after={"kind": "timer", "not_before": moment.strftime("%Y-%m-%dT%H:%M:%SZ")},
                )
            else:
                assert asked is not None
                ask_id = await raise_ask(conn, actor=actor, node=node, ask=asked, effect="gates")
                gate_id = await record_event(
                    conn,
                    node=node,
                    action=GATE_ADDED,
                    actor=actor,
                    before=None,
                    after={"kind": "approval", "ask": ask_id},
                )
            row = await fetch_one(
                conn, f"SELECT {GATE_COLUMNS} FROM gate_state WHERE id = %s", (gate_id,)
            )
        assert row is not None
        return gate_from(row)

    async def remove_gate(self, *, actor: str, gate: int, reason: str | None) -> GateState:
        """Lift a gate, as the person calling: the work is free again (if nothing else holds it).
        The approval a gate was waiting for is closed with it."""
        require_person(f"Gate {gate}", "lift a gate")
        why = clean_reason(reason, "reason")
        async with self._database.transaction() as conn:
            found = await fetch_one(
                conn, f"SELECT {GATE_COLUMNS} FROM gate_state WHERE id = %s", (gate,)
            )
            if found is None:
                raise Refusal(
                    f"There is no gate {gate} in this project. get_node shows the gates of a "
                    "story or task."
                )
            await require_node(conn, found["node"], lock=True)
            again = await fetch_one(
                conn, f"SELECT {GATE_COLUMNS} FROM gate_state WHERE id = %s", (gate,)
            )
            assert again is not None
            if again["state"] == "removed":
                raise Refusal(f"Gate {gate} was already lifted by {again['removed_by']}.")
            await record_event(
                conn,
                node=again["node"],
                action=GATE_REMOVED,
                actor=actor,
                before=None,
                after={"gate": gate, **({"reason": why} if why else {})},
            )
            if again["kind"] == "approval" and again["answer"] is None:
                await record_event(
                    conn,
                    node=again["node"],
                    action=ASK_ANSWERED,
                    actor=actor,
                    before=None,
                    after={
                        "ask": again["ask"],
                        "answer": "Gate lifted",
                        "comment": why or "The gate was lifted.",
                    },
                )
            done = await fetch_one(
                conn, f"SELECT {GATE_COLUMNS} FROM gate_state WHERE id = %s", (gate,)
            )
        assert done is not None
        return gate_from(done)


class PlanBook:
    def __init__(self, database: Database, searcher: Searcher) -> None:
        self._database = database
        self._searcher = searcher

    async def list_plans(
        self,
        *,
        status: str | None,
        limit: int,
        after_id: int,
        plan: int | None = None,
        revision: int | None = None,
    ) -> PlanPage:
        """The plans of the project that were proposed for approval, oldest first. With `plan`,
        only that one, read whole (what its revision holds, with details, links and criteria, its
        revisions and the feedback on them); `revision` reads an earlier revision of it."""
        if revision is not None and plan is None:
            raise Refusal("revision goes with plan: give the plan whose revision you want to read.")
        if plan is not None:
            return await self._one_plan(plan, revision)
        limit = page_size(limit)
        if status is not None and status not in PLAN_STATES:
            raise Refusal(f"status must be one of {', '.join(PLAN_STATES)}, but it is {status!r}.")
        if after_id < 0:
            raise Refusal(f"after_id must be 0 or more, but it is {after_id}.")
        where = "(%(status)s::text IS NULL OR state = %(status)s)"
        async with self._database.transaction() as conn:
            rows = await fetch_all(
                conn,
                f"SELECT {PLAN_COLUMNS} FROM plan_state WHERE {where} AND id > %(after)s "
                "ORDER BY id LIMIT %(fetch)s",
                {"status": status, "after": after_id, "fetch": limit + 1},
            )
            counted = await fetch_one(
                conn, f"SELECT count(*) AS total FROM plan_state WHERE {where}", {"status": status}
            )
        assert counted is not None
        page, next_after_id = split_page(rows, limit)
        return PlanPage(
            items=[plan_from(row) for row in page],
            total=int(counted["total"]),
            next_after_id=next_after_id,
        )

    async def _one_plan(self, plan: int, revision: int | None) -> PlanPage:
        async with self._database.transaction() as conn:
            found = await fetch_one(
                conn, f"SELECT {PLAN_COLUMNS} FROM plan_state WHERE id = %s", (plan,)
            )
            if found is None:
                raise Refusal(f"There is no plan {plan} in this project. list_plans shows them.")
            current = int(found["revision"])
            shown = current if revision is None else revision
            if shown < 1 or shown > current:
                raise Refusal(
                    f"Plan {plan} has revisions 1 to {current}, so there is no revision {shown}."
                )
            revisions, feedback = _history(await _revision_events(conn, plan))
            refs = list(found["items"] or [])
            held = found["state"] in ("proposed", "changes_requested")
            live = shown == current and held
            reviewed = await snapshot_of(conn, plan, shown)
            previous = await snapshot_of(conn, plan, shown - 1) if shown > 1 else None
            drift: list[PlanDrift] | None = None
            if live:
                tree, context = await _plan_tree(conn, refs)
                drift = await _drift(conn, refs, reviewed)
            else:
                tree = [PlanItemRead.model_validate(one) for one in reviewed]
                context = await context_of(conn, tree)
            waiting = found["state"] == "proposed" and shown == current
            checks = await local_checks(conn, [one.ref for one in tree]) if waiting else None
            behind = await unindexed_elsewhere(conn, [one.ref for one in tree]) if waiting else 0
        if checks is not None:
            checks.append(
                await duplicate_check(
                    self._searcher,
                    [(one.ref, one.kind, one.title, one.body) for one in tree],
                    behind,
                )
            )
        read = plan_from(found).model_copy(
            update={
                "shown": shown,
                "tree": tree,
                "context": context,
                "checks": checks,
                "revisions": revisions,
                "feedback": feedback,
                "changes": None if previous is None else diff_revisions(previous, reviewed),
                "drift": drift,
            }
        )
        return PlanPage(items=[read], total=1, next_after_id=None)

    async def _lock(self, conn: Any, locked: dict[str, dict[str, Any]], refs: list[str]) -> None:
        """Hold the items' rows, in one order so that two people deciding one plan, or a decision
        and a revision, take turns. What is held already is not asked for again."""
        for ref in sorted(refs):
            if ref not in locked:
                locked[ref] = await require_node(conn, ref, lock=True)

    async def decide(
        self,
        *,
        actor: str,
        plan: int,
        approved: bool,
        reason: str | None,
        revision: int | None = None,
    ) -> PlanState:
        """Approve a proposed plan, so that its work is free to take, or reject it, which drops all
        of it with the person's reason. Only a person can; a plan is decided once. `revision` is
        the one the person reviewed: a plan that has a newer one is refused, not decided."""
        require_person(f"Plan {plan}", "approve or reject a plan")
        why = clean_reason(reason, "reason")
        if not approved and why is None:
            raise Refusal(
                "A rejected plan needs a reason: say why it is dropped, so that whoever "
                "proposed it knows. Nothing was changed."
            )
        async with self._database.transaction() as conn:
            found = await fetch_one(
                conn, f"SELECT {PLAN_COLUMNS} FROM plan_state WHERE id = %s", (plan,)
            )
            if found is None:
                raise Refusal(f"There is no plan {plan} in this project. list_plans shows them.")
            _check_revision(plan, found, revision)
            locked: dict[str, dict[str, Any]] = {}
            await self._lock(conn, locked, list(found["items"] or []))
            again = await fetch_one(
                conn, f"SELECT {PLAN_COLUMNS} FROM plan_state WHERE id = %s", (plan,)
            )
            assert again is not None
            refs: list[str] = list(again["items"] or [])
            await self._lock(conn, locked, refs)
            ended = _ended(plan, again)
            if ended is not None:
                raise ended
            _check_revision(plan, again, revision)
            if approved and again["state"] == "changes_requested":
                raise Refusal(
                    f"Changes were asked for to revision {again['revision']} of Plan {plan}, and "
                    "the agent has not sent a new revision yet, so there is nothing to approve. "
                    "Reject the plan to end it. Nothing was changed."
                )
            if approved:
                drift = await _drift(
                    conn, refs, await snapshot_of(conn, plan, int(again["revision"]))
                )
                if drift:
                    raise Refusal(_drift_words(plan, int(again["revision"]), drift))
            await record_event(
                conn,
                action=PLAN_APPROVED if approved else PLAN_REJECTED,
                actor=actor,
                before=None,
                after={
                    "plan": plan,
                    "revision": int(again["revision"]),
                    **({"reason": why} if why else {}),
                },
            )
            if not approved:
                note = f"The plan was rejected: {why}"
                for ref in sorted(refs):
                    current = locked[ref]
                    if current["status"] != "dropped":
                        await change_status(conn, actor, current, "dropped")
                    await record_event(
                        conn,
                        node=current["ref"],
                        action="handoff",
                        actor=actor,
                        before=None,
                        after={"outcome": "dropped", "note": note},
                    )
            done = await fetch_one(
                conn, f"SELECT {PLAN_COLUMNS} FROM plan_state WHERE id = %s", (plan,)
            )
        assert done is not None
        return plan_from(done)

    async def request_changes(
        self,
        *,
        actor: str,
        plan: int,
        revision: int,
        summary: str,
        comments: list[PlanCommentInput] | None,
    ) -> PlanState:
        """A person's feedback on one revision of a plan: a summary and comments on its lines. The
        plan stays held, and the agent revises it; nothing is dropped and nothing becomes free.
        Only a person can, and only for the current revision, once."""
        require_person(f"Plan {plan}", "ask for changes to a plan")
        said = clean_summary(summary)
        async with self._database.transaction() as conn:
            found = await fetch_one(
                conn, f"SELECT {PLAN_COLUMNS} FROM plan_state WHERE id = %s", (plan,)
            )
            if found is None:
                raise Refusal(f"There is no plan {plan} in this project. list_plans shows them.")
            _check_revision(plan, found, revision)
            locked: dict[str, dict[str, Any]] = {}
            await self._lock(conn, locked, list(found["items"] or []))
            again = await fetch_one(
                conn, f"SELECT {PLAN_COLUMNS} FROM plan_state WHERE id = %s", (plan,)
            )
            assert again is not None
            await self._lock(conn, locked, list(again["items"] or []))
            ended = _ended(plan, again)
            if ended is not None:
                raise ended
            _check_revision(plan, again, revision)
            if again["state"] == "changes_requested":
                raise Refusal(
                    f"Changes were already asked for to revision {revision} of Plan {plan}, and "
                    "the agent has not sent a new revision yet. Nothing was changed."
                )
            reviewed = await snapshot_of(conn, plan, revision)
            made = clean_comments(comments, reviewed)
            await record_event(
                conn,
                action=PLAN_CHANGES_REQUESTED,
                actor=actor,
                before=None,
                after={
                    "plan": plan,
                    "revision": revision,
                    "summary": said,
                    "comments": [one.model_dump() for one in made],
                },
            )
            done = await fetch_one(
                conn, f"SELECT {PLAN_COLUMNS} FROM plan_state WHERE id = %s", (plan,)
            )
            history = _history(await _revision_events(conn, plan))
        assert done is not None
        return plan_from(done).model_copy(update={"feedback": history[1][-1:]})
