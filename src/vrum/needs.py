"""What only a person can settle (M45): ONE list, worked out from the rest and never stored twice.

An agent that has to wait for a person leaves one of four things, and this reads them together so
that a person has one place to look:

- an open ask (asks.py): a question or approval that was put about a story or task;
- unticked `[person]` checks on work that is otherwise finished: a story or task that was handed
  over as blocked with every other check ticked, so that only a person's judgement is missing;
- notes waiting for confirmation on work in progress (in progress, or held by someone right now):
  an agent recorded what it learned and no person has reviewed it yet while the work it concerns
  goes on;
- work whose claim ran out twice with no handoff note: something is wrong with it that an agent
  does not say.

It is grouped by story (a task under its story), so a person who looks at a story sees everything
it waits for and never one line for every step, and each row says why it is here, how long it has
waited and who is waiting. Anything that is only information stays in Activity: this list asks
rarely, and nothing on it is ever answered or ticked for a person."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from vrum.asks import ASK_COLUMNS, AskState, ask_from
from vrum.common import fetch_all
from vrum.db import Database
from vrum.gate_reads import GATE_COLUMNS, GateState, gate_from
from vrum.gates import PLAN_COLUMNS, PlanState, plan_from
from vrum.needs_topics import NeedsTopic, Subject, topics_of
from vrum.placement import PLACEMENT_COLUMNS, PlacementState, placement_from

# More rows than this are counted and left out, because a page of a hundred things is not a list a
# person works through.
MOST_ROWS = 200
_REASON_CHARS = 200
_NODE = "s.ref, s.kind, s.title, s.parent, p.title AS parent_title"
_NODE_GROUP = "s.ref, s.kind, s.title, s.parent, p.title"
_FROM_NODE = "node_state s LEFT JOIN node_state p ON p.ref = s.parent"
# How many times a claim ran out on a node: each time one was taken over, and the one that is
# lapsed now.
_LAPSES = "count(e.id) + CASE WHEN s.lapsed_claim_by IS NOT NULL THEN 1 ELSE 0 END"


class NeedsItem(BaseModel):
    kind: str = Field(
        description="ask (a question or approval), check (checks only a person can tick), note "
        "(notes waiting for confirmation on work in progress), lapse (a claim that ran out twice "
        "with no handoff note), plan (a drafted plan waiting for approval: one row for the whole "
        "plan), placement (a proposal to move work under another parent, or to group it under a "
        "new epic) or gate (an approval that was refused: lift the gate or drop the work)."
    )
    node: str = Field(description="The story or task it is about, by reference.")
    title: str = Field(description="That story or task's title.")
    reason: str = Field(description="Why it is here, in plain words.")
    waiting_since: datetime = Field(description="Since when it has waited.")
    waiting: str | None = Field(
        description="Who is waiting: the agent that asked or that handed the work over. Empty "
        "when nobody in particular is."
    )
    waiting_kind: str | None = Field(description="person or agent, when waiting is set.")
    count: int = Field(description="How many checks, notes or lapsed claims this row stands for.")
    ask: AskState | None = Field(
        description="The ask itself, for an ask: answer it with answer_ask."
    )
    plan: PlanState | None = Field(
        description="The plan itself, for a plan row: approve or reject it with approve_plan."
    )
    notes: list[int] = Field(description="The notes waiting, for a note row.")
    gate: GateState | None = Field(
        default=None,
        description="The gate itself, for a gate row: lift it with remove_gate.",
    )
    placement: PlacementState | None = Field(
        default=None,
        description="The proposal itself, for a placement row: accept or reject it with "
        "decide_placement.",
    )
    level: Literal["plan", "epic", "story", "task", "note"] = Field(
        description="How strategic the row is: plan (a whole plan), epic, story or task (the work "
        "it is about, or for a move the highest kind of work it moves) or note (notes waiting "
        "for confirmation).",
    )
    decision: Literal["approve", "answer", "look_into"] = Field(
        description="What a person is asked to do: approve (a plan, a move, a note or a check), "
        "answer (an ask) or look_into (a refused gate or a claim that ran out twice).",
    )
    topic: NeedsTopic | None = Field(
        default=None,
        description="What it is about, so that rows about the same thing can be settled "
        "together: the epic it is under, or a group of rows whose work is alike in meaning "
        "(NeedsTopic). Empty for a row about nothing the project can place.",
    )


class NeedsGroup(BaseModel):
    story: str | None = Field(
        description="The story these rows are about (a task is grouped under its story), by "
        "reference."
    )
    title: str | None
    items: list[NeedsItem]


class NeedsAPerson(BaseModel):
    as_of: datetime = Field(description="The server's time for this answer.")
    total: int = Field(description="How many rows there are in all: what the sidebar counts.")
    groups: list[NeedsGroup] = Field(
        description="By story, the one that has waited longest first, each with its rows."
    )
    left_out: int = Field(description="How many rows were left out because there are too many.")
    board_address: str | None = Field(
        default=None,
        description="Where this project's board is, from the address the person who runs Vrum "
        "set. A person answers there, under their own name.",
    )


def _subject(item: NeedsItem) -> Subject:
    """The work a row is about, for the topics: a plan's items, the items a move moves (and where
    they go), or the story or task of every other kind of row."""
    if item.plan is not None:
        return Subject(refs=list(item.plan.items))
    if item.placement is not None:
        return Subject(refs=[one.ref for one in item.placement.items], under=item.placement.parent)
    return Subject(refs=[item.node])


def _cut(text: str) -> str:
    one = " ".join(text.split())
    return one if len(one) <= _REASON_CHARS else one[: _REASON_CHARS - 1].rstrip() + "\u2026"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _decision(kind: str) -> str:
    if kind == "ask":
        return "answer"
    return "look_into" if kind in ("gate", "lapse") else "approve"


def _item(row: dict[str, Any], **more: Any) -> tuple[NeedsItem, str, str | None]:
    """A row, with the story it is grouped under and that story's title."""
    story = row["ref"] if row["kind"] == "story" else row["parent"]
    story_title = row["title"] if row["kind"] == "story" else row["parent_title"]
    kind = more["kind"]
    item = NeedsItem.model_validate(
        {
            "node": row["ref"],
            "title": row["title"],
            "waiting": None,
            "waiting_kind": None,
            "count": 1,
            "ask": None,
            "plan": None,
            "notes": [],
            "level": "note" if kind == "note" else row["kind"],
            "decision": _decision(kind),
            **more,
        }
    )
    return item, story, story_title


class Needs:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def needs_a_person(self) -> NeedsAPerson:
        # The ask's own kind (question or approval) is named apart from the story or task's kind.
        asks_sql = ", ".join(
            "a.kind AS ask_kind" if name.strip() == "kind" else f"a.{name.strip()}"
            for name in ASK_COLUMNS.split(",")
        )
        async with self._database.transaction() as conn:
            asks = await fetch_all(
                conn,
                f"SELECT {asks_sql}, {_NODE} FROM ask_state a JOIN node_state s ON s.ref = a.node "
                "LEFT JOIN node_state p ON p.ref = s.parent "
                "WHERE a.answer_event_id IS NULL AND s.status NOT IN ('done', 'dropped') "
                "ORDER BY a.id",
            )
            checks = await fetch_all(
                conn,
                f"SELECT {_NODE}, "
                "count(*) FILTER (WHERE i.person_only AND c.item_key IS NULL) AS person_open, "
                "(array_agg(i.text ORDER BY i.position) "
                "FILTER (WHERE i.person_only AND c.item_key IS NULL))[1] AS first_text, "
                "h.actor, h.actor_kind, coalesce(h.at, s.updated_at) AS since "
                f"FROM {_FROM_NODE} CROSS JOIN definition_items i "
                "LEFT JOIN definition_checks c ON c.node = s.ref AND c.item_key = i.item_key "
                "LEFT JOIN LATERAL (SELECT actor, actor_kind, at FROM events "
                "WHERE node = s.ref AND action = 'handoff' ORDER BY id DESC LIMIT 1) h ON true "
                "WHERE s.kind IN ('story', 'task') AND s.status = 'blocked' "
                "AND s.claimed_by IS NULL "
                f"GROUP BY {_NODE_GROUP}, h.actor, h.actor_kind, h.at, s.updated_at "
                "HAVING count(*) FILTER (WHERE i.person_only AND c.item_key IS NULL) > 0 "
                "AND count(*) FILTER (WHERE NOT i.person_only AND c.item_key IS NULL) = 0",
            )
            notes = await fetch_all(
                conn,
                f"SELECT {_NODE}, count(*) AS waiting_notes, min(n.recorded_at) AS since, "
                "array_agg(n.id ORDER BY n.id) AS ids "
                "FROM note_state n JOIN note_nodes nn ON nn.note_id = n.id "
                "JOIN node_state s ON s.ref = nn.node "
                "LEFT JOIN node_state p ON p.ref = s.parent "
                "WHERE n.status = 'unconfirmed' "
                "AND (s.status = 'in_progress' OR s.claimed_by IS NOT NULL) "
                "AND s.kind IN ('story', 'task') "
                f"GROUP BY {_NODE_GROUP}",
            )
            lapses = await fetch_all(
                conn,
                f"SELECT {_NODE}, "
                f"{_LAPSES} AS lapses, "
                "greatest(max(e.at), s.lapsed_claim_at) AS since "
                f"FROM {_FROM_NODE} LEFT JOIN events e ON e.node = s.ref "
                "AND e.action = 'claimed' AND e.before IS NOT NULL "
                "WHERE s.kind IN ('story', 'task') AND s.status NOT IN ('done', 'dropped') "
                "AND s.claimed_by IS NULL "
                f"GROUP BY {_NODE_GROUP}, s.lapsed_claim_by, s.lapsed_claim_at "
                f"HAVING {_LAPSES} >= 2 AND NOT EXISTS (SELECT 1 FROM events h "
                "WHERE h.node = s.ref AND h.action = 'handoff' "
                "AND h.at > greatest(max(e.at), s.lapsed_claim_at))",
            )
            plans = await fetch_all(
                conn,
                f"SELECT {PLAN_COLUMNS} FROM plan_state WHERE state = 'proposed' ORDER BY id",
            )
            moves = await fetch_all(
                conn,
                f"SELECT {PLACEMENT_COLUMNS} FROM placement_state WHERE state = 'proposed' "
                "ORDER BY id",
            )
            parents = await fetch_all(
                conn,
                "SELECT ref, title FROM nodes WHERE ref = ANY(%s)",
                (sorted({m["parent"] for m in moves if m["parent"]}),),
            )
            refused = await fetch_all(
                conn,
                "SELECT "
                + ", ".join(
                    "g.kind AS gate_kind" if c.strip() == "kind" else "g." + c.strip()
                    for c in GATE_COLUMNS.split(",")
                )
                + f", {_NODE} "
                "FROM gate_state g JOIN node_state s ON s.ref = g.node "
                "LEFT JOIN node_state p ON p.ref = s.parent "
                "WHERE g.state = 'refused' AND s.status NOT IN ('done', 'dropped') ORDER BY g.id",
            )
            now = await fetch_all(conn, "SELECT clock_timestamp() AS now")
        built: list[tuple[NeedsItem, str, str | None]] = []
        for row in asks:
            ask = ask_from({**row, "kind": row["ask_kind"]})
            built.append(
                _item(
                    row,
                    kind="ask",
                    reason=_cut(
                        f"Approval needed before it can start: {ask.question}"
                        if ask.effect == "gates"
                        else ask.question
                    ),
                    waiting_since=ask.asked_at,
                    waiting=ask.asked_by,
                    waiting_kind=ask.asked_by_kind,
                    ask=ask,
                )
            )
        for row in checks:
            built.append(
                _item(
                    row,
                    kind="check",
                    reason=_cut(
                        f"Everything else on it is done. Waiting for a person to check: "
                        f"{row['first_text']}"
                        + (
                            ""
                            if row["person_open"] == 1
                            else f" (and {row['person_open'] - 1} more)"
                        )
                    ),
                    waiting_since=row["since"],
                    waiting=row["actor"],
                    waiting_kind=row["actor_kind"],
                    count=row["person_open"],
                )
            )
        for row in notes:
            built.append(
                _item(
                    row,
                    kind="note",
                    reason=(
                        f"{_plural(row['waiting_notes'], 'note')} recorded on work that is in "
                        "progress, not yet confirmed by a person."
                    ),
                    waiting_since=row["since"],
                    count=row["waiting_notes"],
                    notes=list(row["ids"]),
                )
            )
        for row in lapses:
            built.append(
                _item(
                    row,
                    kind="lapse",
                    reason=(
                        f"Its claim ran out {row['lapses']} times with no handoff note: "
                        "something may be wrong with it."
                    ),
                    waiting_since=row["since"],
                    count=row["lapses"],
                )
            )
        for row in refused:
            built.append(
                _item(
                    row,
                    kind="gate",
                    reason=_cut(
                        f"Its approval was refused by {row['answered_by']} ({row['answer']}). Lift "
                        "the gate, or drop the work."
                    ),
                    waiting_since=row["added_at"],
                    waiting=row["added_by"],
                    waiting_kind=row["added_by_kind"],
                    gate=gate_from({**row, "kind": row["gate_kind"]}),
                )
            )
        plan_rows: list[NeedsItem] = []
        for plan_row in plans:
            plan = plan_from(plan_row)
            first = plan.items[0] if plan.items else "epic:0"
            again = f" (revision {plan.revision})" if plan.revision > 1 else ""
            plan_rows.append(
                NeedsItem(
                    kind="plan",
                    node=first,
                    title=plan.title,
                    reason=_cut(
                        f"A plan of {_plural(plan.epics, 'epic')}, "
                        f"{_plural(plan.stories, 'story')} and {_plural(plan.tasks, 'task')} is "
                        f"waiting for approval{again}. "
                        "Nothing in it can be taken until a person decides."
                    ),
                    waiting_since=plan.revised_at,
                    waiting=plan.revised_by,
                    waiting_kind=plan.revised_by_kind,
                    count=plan.epics + plan.stories + plan.tasks,
                    ask=None,
                    plan=plan,
                    notes=[],
                    level="plan",
                    decision="approve",
                )
            )
        titles = {p["ref"]: p["title"] for p in parents}
        move_rows: list[NeedsItem] = []
        for move_row in moves:
            move = placement_from(move_row, titles)
            where = (
                f"under {move.parent_title or move.parent}"
                if move.parent
                else f"under a new epic, {move.new_epic.title if move.new_epic else ''}"
            )
            move_rows.append(
                NeedsItem(
                    kind="placement",
                    node=move.items[0].ref,
                    title=move.items[0].title,
                    reason=_cut(
                        f"Proposed: move {_plural(len(move.items), 'item')} {where}. {move.reason}"
                    ),
                    waiting_since=move.proposed_at,
                    waiting=move.proposed_by,
                    waiting_kind=move.proposed_by_kind,
                    count=len(move.items),
                    ask=None,
                    plan=None,
                    notes=[],
                    placement=move,
                    level="story"
                    if any(one.ref.startswith("story:") for one in move.items)
                    else "task",
                    decision="approve",
                )
            )
        built.sort(key=lambda found: found[0].waiting_since)
        # A plan or a proposal to move work is one row, listed in a group of its own and not under
        # a story.
        total_plans = len(plan_rows) + len(move_rows)
        total = len(built) + total_plans
        shown = built[: max(MOST_ROWS - total_plans, 0)]
        everything = [found[0] for found in shown] + plan_rows + move_rows
        async with self._database.transaction() as conn:
            topics = await topics_of(conn, [_subject(item) for item in everything])
        by_id = {id(item): topic for item, topic in zip(everything, topics, strict=True)}
        shown = [(item.model_copy(update={"topic": by_id[id(item)]}), s, t) for item, s, t in shown]
        plan_rows = [item.model_copy(update={"topic": by_id[id(item)]}) for item in plan_rows]
        move_rows = [item.model_copy(update={"topic": by_id[id(item)]}) for item in move_rows]
        groups: dict[str | None, NeedsGroup] = {}
        for item, story, story_title in shown:
            group = groups.setdefault(story, NeedsGroup(story=story, title=story_title, items=[]))
            group.items.append(item)
        ordered = sorted(groups.values(), key=lambda g: g.items[0].waiting_since)
        if move_rows:
            ordered.insert(0, NeedsGroup(story=None, title="Suggested moves", items=move_rows))
        if plan_rows:
            ordered.insert(
                0, NeedsGroup(story=None, title="Plans waiting for approval", items=plan_rows)
            )
        return NeedsAPerson(
            as_of=now[0]["now"],
            total=total,
            groups=ordered,
            left_out=total - len(shown) - total_plans,
        )
