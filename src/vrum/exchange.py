"""Moving the whole graph out of one server and into another as data (M7).

`export_page` reads everything a page at a time, in a fixed order, as plain data. `import_document`
loads such a file into a server that holds nothing yet, keeping every number, id, date,
confirmation and event, in one step: either all of it is loaded or none of it. Backing up and
restoring the whole database is separate (scripts/backup.sh and scripts/restore.sh); this is the
portable, readable form.

An epic, story or task is in the file by its kind and its number within its kind (`kind` and
`number`), and everything that points at one holds its ref (`story:12`): a node's `parent`, a
link's `source` and `target`, a rule's `node` (the story or task it is on), a note's `nodes`, an
event's `node` and `other_node`. A rule, note, link or event has its own `id`.

Notes are exported as they stand: their status (unconfirmed, confirmed, superseded or withdrawn,
what replaced them, why), who confirmed them and when (an ended note keeps the confirmation it
had), who decided them, and what they are linked to. Notes and events say who made them and as
what (`recorded_by` with `recorded_by_kind`, `actor` with `actor_kind`: a person or an agent).

The definition of done is a list of at most one item: the text, its version, and who set it and
when. A project that never set one exports an empty list. The ticks of its checks are the list
`checks`: for each story or task, each item of the definition of done that is ticked on it, with
the item's key and words, who ticked it and when, and how it was checked. A tick of an item that
is no longer in the text is exported too. The items themselves are not: the server works them out
from the text again when it loads it.

The current export format is version 6. A claim or handoff event may say where the work was
(repository, branch, commit, pull_request) and which run it was (run_label, client_name): the words
of whereabouts.py, which the import checks.

Left out on purpose: claims (a claim is a lease that runs out, not part of the graph; the events
that recorded it are exported) and everything that is worked out from the rest (what is ready, the
search columns and the semantic index, which the server rebuilds)."""

import re
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from typing import Any, Literal

import psycopg
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, ValidationError

from vrum.asks import EFFECTS as ASK_EFFECTS
from vrum.asks import KINDS as ASK_KINDS
from vrum.common import (
    NODE_KINDS,
    Refusal,
    fetch_all,
    fetch_one,
    node_ref,
    page_size,
    parse_node_ref,
    refuse_secrets,
    split_node_ref,
)
from vrum.db import Database
from vrum.definition_of_done import definition_items
from vrum.exchange_shapes import (
    ActorKind,
    Day,
    Id,
    Item,
    Moment,
    NodeRef,
    Status,
    problems_in,
    refuse_file,
)
from vrum.whereabouts import WHERE_FIELDS, clean_whereabouts

FORMAT = "vrum-export"
# Version 6 carries the size of a story or task and the ticks of the definition of done's checks.
# Versions 1 to 5 are still read.
FORMAT_VERSION = 6
# The order everything is written in, and the order an import needs: a node before what points to
# it, a note before the notes that concern it, events last.
SECTIONS = ("nodes", "links", "rules", "notes", "definition_of_done", "checks", "events")
# The lists whose items have an `id` of their own, and so a counter of their own.
_WITH_IDS = ("links", "rules", "notes", "events")
# The table a list is kept in, when it is not called the same.
_TABLES = {"checks": "definition_checks"}
_A = {"epic": "an epic", "story": "a story", "task": "a task"}
_ITEM_KEY = re.compile(r"[0-9a-f]{8}")

_QUERIES = {
    "nodes": (
        "SELECT kind, number, parent, title, body, status, priority, size, start_on, target_on, "
        "version, created_at, updated_at FROM node_state"
    ),
    "links": "SELECT id, source, target, link_type, created_at FROM links",
    "rules": "SELECT id, node, title, body, status, version, created_at, updated_at FROM rules",
    "notes": (
        "SELECT id, text, why, decided_by, decided_on, project_wide, recorded_by, "
        "recorded_by_kind, recorded_at, status, superseded_by_id, supersedes_ids, status_reason, "
        "confirmed_by, confirmed_at, nodes, rule_ids, note_ids FROM note_state"
    ),
    "definition_of_done": (
        "SELECT body, version, updated_at, updated_by, updated_by_kind FROM definition_of_done"
    ),
    "checks": (
        "SELECT node, item_key AS item, item_text AS text, checked_by, checked_by_kind, "
        "checked_at, evidence FROM definition_checks"
    ),
    "events": (
        "SELECT id, node, other_node, rule_id, note_id, other_note_id, action, actor, "
        "actor_kind, at, before, after FROM events"
    ),
}


class ExportPage(BaseModel):
    format: str = Field(description=f'Always "{FORMAT}".')
    version: int = Field(description=f"The version of this file format, now {FORMAT_VERSION}.")
    section: str | None = Field(
        description="Which list `items` belongs to: nodes, links, rules, notes, "
        "definition_of_done, checks or events. Empty when there is nothing at all to export."
    )
    items: list[dict[str, Any]] = Field(
        description="The items of that list, in a fixed order: nodes as epics, then stories, then "
        "tasks, each by number; the checks by node and item; the others by id (the definition of "
        "done has at most one item). A page never mixes lists."
    )
    next_after: str | None = Field(
        description="Pass this as `after` to get the next page. Empty on the last page."
    )
    counts: dict[str, int] | None = Field(
        description="Only on the first page: how many items each list holds in all, so that a "
        "reader can check it got every page."
    )


class ImportResult(BaseModel):
    counts: dict[str, int] = Field(description="How many items of each list were loaded.")
    message: str


def _plain(value: Any) -> Any:
    """A value as it is written in the file: dates and times as text, in a fixed form."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _exported(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _plain(value) for key, value in row.items()}


def _cursor(after: str | None) -> tuple[str, tuple[Any, ...]]:
    """Where a page starts: its list, and the last item of the one before it. A node is placed by
    its kind (counted from 1: epic, story, task) and number, a tick of a check by its node and
    the item's key, any other item by its id."""
    if after is None:
        return SECTIONS[0], _start(SECTIONS[0])
    section, _, last = after.partition(":")
    try:
        if section == "nodes":
            kind, number = split_node_ref(parse_node_ref(last))
            return section, (NODE_KINDS.index(kind) + 1, number)
        if section == "checks":
            if last == "0":
                return section, _start(section)
            node, _, key = last.rpartition(":")
            if _ITEM_KEY.fullmatch(key):
                return section, (parse_node_ref(node), key)
        elif section in SECTIONS and last.isdecimal():
            return section, (int(last),)
    except Refusal:
        pass
    raise Refusal(
        f"after must be left empty for the first page, or be the next_after of the previous "
        f"page (such as nodes:story:12 or links:200), but it is {after[:40]!r}."
    )


def _start(section: str) -> tuple[Any, ...]:
    """What `_after_clause` is given to start a list from its first item."""
    return {"nodes": (0, 0), "checks": ("", "")}.get(section, (0,))


def _after_clause(section: str) -> tuple[str, str]:
    """The test for items after the last one of the previous page, and the order that makes it
    work."""
    if section == "nodes":
        return "(kind_rank, number) > (%s, %s)", "kind_rank, number"
    if section == "checks":
        return "(node, item_key) > (%s, %s)", "node, item_key"
    if section == "definition_of_done":
        # At most one item, so there is never a page to continue after: only the start is asked.
        return "%s = 0", "project_id"
    return "id > %s", "id"


def _cursor_after(section: str, item: dict[str, Any]) -> str:
    if section == "nodes":
        return f"nodes:{node_ref(item['kind'], item['number'])}"
    if section == "checks":
        return f"checks:{item['node']}:{item['item']}"
    return f"{section}:{item['id']}"


async def _counts(conn: AsyncConnection) -> dict[str, int]:
    row = await fetch_one(
        conn,
        "SELECT "
        + ", ".join(
            f"(SELECT count(*) FROM {_TABLES.get(name, name)}) AS {name}" for name in SECTIONS
        ),
    )
    assert row is not None
    return {name: int(row[name]) for name in SECTIONS}


# -- reading a file that is to be imported -------------------------------------------------------


class NodeItem(Item):
    kind: Literal["epic", "story", "task"]
    number: Id
    parent: NodeRef | None
    title: str
    body: str
    # `ready` is what a node nobody has started shows when nothing it needs first is open. It is
    # worked out, so it is kept as `new`.
    status: Literal["new", "ready", "in_progress", "blocked", "done", "dropped"]
    priority: Literal["low", "normal", "high", "urgent"]
    size: Literal["xs", "s", "m", "l", "xl"] | None
    start_on: Day | None
    target_on: Day | None
    version: Id
    created_at: Moment
    updated_at: Moment


class LinkItem(Item):
    id: Id
    source: NodeRef
    target: NodeRef
    link_type: Literal["needs_first", "supersedes", "duplicates", "relates_to", "discovered_from"]
    created_at: Moment


class RuleItem(Item):
    id: Id
    node: NodeRef | None
    title: str
    body: str
    status: Literal["open", "met", "dropped"]
    version: Id
    created_at: Moment
    updated_at: Moment


class DefinitionItem(Item):
    body: str
    version: Id
    updated_at: Moment
    updated_by: str
    updated_by_kind: Literal["person"]


class CheckItem(Item):
    node: NodeRef
    item: str
    text: str
    checked_by: str
    checked_by_kind: Literal["person", "agent"]
    checked_at: Moment
    evidence: str | None


class NoteItem(Item):
    id: Id
    text: str
    why: str
    decided_by: str | None
    decided_on: Day | None
    project_wide: bool
    recorded_by: str
    recorded_by_kind: ActorKind
    recorded_at: Moment
    status: Status
    superseded_by_id: Id | None
    supersedes_ids: list[Id]
    status_reason: str | None
    confirmed_by: str | None
    confirmed_at: Moment | None
    nodes: list[NodeRef]
    rule_ids: list[Id]
    note_ids: list[Id]


class EventItem(Item):
    id: Id
    node: NodeRef | None
    other_node: NodeRef | None
    rule_id: Id | None
    note_id: Id | None
    other_note_id: Id | None
    action: Literal[
        "create",
        "update",
        "move",
        "link_added",
        "link_removed",
        "claimed",
        "claim_renewed",
        "claim_released",
        "handoff",
        "rule_created",
        "rule_updated",
        "rule_placed",
        "rule_moved",
        "definition_of_done_changed",
        "check_ticked",
        "check_unticked",
        "note_recorded",
        "note_superseded",
        "note_withdrawn",
        "note_linked",
        "note_unlinked",
        "note_confirmed",
        "note_unconfirmed",
        "ask_raised",
        "ask_answered",
        "gate_added",
        "gate_removed",
        "plan_proposed",
        "plan_revised",
        "plan_changes_requested",
        "plan_approved",
        "plan_rejected",
        "plan_template_saved",
        "plan_template_deleted",
        "placement_proposed",
        "placement_accepted",
        "placement_rejected",
    ]
    actor: str
    actor_kind: ActorKind
    at: Moment
    before: dict[str, Any] | None
    after: dict[str, Any]


class Document(Item):
    format: Literal["vrum-export"]
    version: Literal[6]
    counts: dict[str, int] | None = None
    nodes: list[NodeItem]
    links: list[LinkItem]
    rules: list[RuleItem]
    notes: list[NoteItem]
    definition_of_done: list[DefinitionItem]
    checks: list[CheckItem]
    events: list[EventItem]


def _note_problems(place: str, note: NoteItem, notes: dict[int, Any]) -> list[str]:
    """Where a note stands has to hang together, as the database requires: a superseded one names
    the later note that replaced it, a withdrawn one does not, the reason it was withdrawn is there
    and no other reason is, a confirmation names a person and a time or neither (and a note in
    force is confirmed exactly when it has one), and somebody who decided it names who decided it
    and when, or nothing does."""
    problems: list[str] = []
    replacement = note.superseded_by_id
    if note.status == "superseded":
        if replacement is None:
            problems.append(
                f"{place}: a superseded note needs superseded_by_id, the note that replaced it"
            )
        elif replacement not in notes:
            problems.append(f"{place}: superseded_by_id={replacement} is not a note in the file")
        elif replacement <= note.id:
            problems.append(
                f"{place}: superseded_by_id={replacement} must be a later id than {note.id}: a "
                "note is replaced by one recorded after it"
            )
    elif replacement is not None:
        problems.append(
            f"{place}: superseded_by_id is only for a superseded note, and this one is "
            f"{note.status}"
        )
    if note.status == "withdrawn":
        if note.status_reason is None or not note.status_reason.strip():
            problems.append(f"{place}: a withdrawn note needs status_reason, why it was given up")
    elif note.status in ("unconfirmed", "confirmed") and note.status_reason is not None:
        problems.append(f"{place}: status_reason is only for a note that has ended")
    elif note.status_reason is not None and not note.status_reason.strip():
        problems.append(f"{place}: status_reason must say why, or be left empty")
    if (note.confirmed_by is None) != (note.confirmed_at is None):
        problems.append(
            f"{place}: confirmed_by and confirmed_at go together: give both, or neither when no "
            "person has confirmed it"
        )
    elif note.confirmed_by is not None and not note.confirmed_by.strip():
        problems.append(f"{place}: confirmed_by must be the name of a person")
    elif note.status == "confirmed" and note.confirmed_by is None:
        problems.append(f"{place}: a confirmed note needs confirmed_by and confirmed_at")
    elif note.status == "unconfirmed" and note.confirmed_by is not None:
        problems.append(
            f"{place}: an unconfirmed note has no confirmed_by or confirmed_at (only a note "
            "that has ended keeps the confirmation it had)"
        )
    if (note.decided_by is None) != (note.decided_on is None):
        problems.append(
            f"{place}: decided_by and decided_on go together: give both when somebody decided the "
            "note, or neither when nobody did"
        )
    elif note.decided_by is not None and not note.decided_by.strip():
        problems.append(f"{place}: decided_by must be the name of a person")
    if not note.text.strip():
        problems.append(f"{place}: text cannot be empty")
    if not note.why.strip():
        problems.append(f"{place}: why cannot be empty")
    if not note.recorded_by.strip():
        problems.append(f"{place}: recorded_by cannot be empty")
    if note.id in note.note_ids:
        problems.append(f"{place}: a note cannot be about itself")
    return problems


def _event_problems(place: str, event: EventItem) -> list[str]:
    """An event says what it is about, as the database requires: a rule event names its rule and
    no note, a note event names its note, a change of the definition of done names nothing, and
    any other event names its node and nothing else."""
    action = event.action
    if action.startswith("rule_"):
        wrong = (
            event.rule_id is None or event.note_id is not None or event.other_note_id is not None
        )
        needs = "rule_id, and no note_id or other_note_id"
    elif action.startswith("note_"):
        wrong = event.note_id is None
        needs = "note_id"
    elif action in _ABOUT_NOTHING:
        wrong = any(
            value is not None
            for value in (
                event.node,
                event.other_node,
                event.rule_id,
                event.note_id,
                event.other_note_id,
            )
        )
        needs = "no node, other_node, rule_id, note_id or other_note_id"
    else:
        wrong = (
            event.node is None
            or event.rule_id is not None
            or event.note_id is not None
            or event.other_note_id is not None
        )
        needs = "node, and no rule_id, note_id or other_note_id"
    problems = [f"{place}: the action '{action}' needs {needs}"] if wrong else []
    if action in _WHERE_ACTIONS:
        for side, data in (("before", event.before), ("after", event.after)):
            problems.extend(_where_problems(f"{place} {side}", data or {}))
    if action in _EVENT_SHAPES:
        problems.extend(_EVENT_SHAPES[action](place, event))
    if action == "ask_raised":
        problems.extend(_ask_raised_problems(place, event))
    if action == "ask_answered":
        problems.extend(_ask_answered_problems(place, event))
    return problems


# The events that are about the whole project and no one node.
_ABOUT_NOTHING = (
    "definition_of_done_changed",
    "plan_proposed",
    "plan_revised",
    "plan_changes_requested",
    "plan_approved",
    "plan_rejected",
    "plan_template_saved",
    "plan_template_deleted",
    "placement_proposed",
    "placement_accepted",
    "placement_rejected",
)
_TEMPLATE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,59}")
_MOMENT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _gate_added_problems(place: str, event: "EventItem") -> list[str]:
    after = event.after
    if after.get("kind") == "timer":
        if not (
            isinstance(after.get("not_before"), str) and _MOMENT.fullmatch(after["not_before"])
        ):
            return [f"{place}: a timer gate needs not_before as YYYY-MM-DDTHH:MM:SSZ"]
    elif after.get("kind") == "approval":
        if not _whole(after.get("ask")):
            return [f"{place}: an approval gate needs ask, the number of its ask"]
    else:
        return [f"{place}: kind must be timer or approval"]
    return []


def _gate_removed_problems(place: str, event: "EventItem") -> list[str]:
    return [] if _whole(event.after.get("gate")) else [f"{place}: gate must be a gate's number"]


def _plan_proposed_problems(place: str, event: "EventItem") -> list[str]:
    after = event.after
    problems = []
    if not _text_of(after, "title"):
        problems.append(f"{place}: a plan needs its title")
    items = after.get("items")
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        problems.append(f"{place}: items must be the references the plan made")
    if not _snapshot_ok(after.get("snapshot")):
        problems.append(f"{place}: snapshot must list what the plan held, an entry for each item")
    return problems


def _snapshot_ok(snapshot: Any) -> bool:
    return isinstance(snapshot, list) and all(
        isinstance(one, dict) and isinstance(one.get("ref"), str) for one in snapshot
    )


def _plan_revised_problems(place: str, event: "EventItem") -> list[str]:
    after = event.after
    problems = []
    if not _whole(after.get("plan")):
        problems.append(f"{place}: plan must be the number of the plan revised")
    revision = after.get("revision")
    if not _whole(revision) or int(revision or 0) < 2:
        problems.append(f"{place}: revision must be a number from 2")
    items = after.get("items")
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        problems.append(f"{place}: items must be the references the revision holds")
    if not _snapshot_ok(after.get("snapshot")):
        problems.append(
            f"{place}: snapshot must list what the revision held, an entry for each item"
        )
    return problems


def _plan_changes_requested_problems(place: str, event: "EventItem") -> list[str]:
    after = event.after
    problems = []
    if not _whole(after.get("plan")):
        problems.append(f"{place}: plan must be the number of the plan")
    if not _whole(after.get("revision")):
        problems.append(f"{place}: revision must be the number of the revision reviewed")
    if not _text_of(after, "summary"):
        problems.append(f"{place}: a request for changes needs its summary")
    comments = after.get("comments")
    if not isinstance(comments, list) or not all(
        isinstance(one, dict) and isinstance(one.get("item"), str) and _text_of(one, "text")
        for one in comments
    ):
        problems.append(f"{place}: comments must each name an item and say something")
    approved = after.get("approved")
    if not isinstance(approved, list) or not all(isinstance(one, str) for one in approved):
        problems.append(f"{place}: approved must be a list of the items the person approved")
    if event.actor_kind != "person":
        problems.append(f"{place}: only a person asks for changes to a plan")
    return problems


def _plan_decided_problems(place: str, event: "EventItem") -> list[str]:
    problems = []
    if not _whole(event.after.get("plan")):
        problems.append(f"{place}: plan must be the number of the plan decided")
    if not _whole(event.after.get("revision")):
        problems.append(f"{place}: revision must be the number of the revision decided")
    if event.actor_kind != "person":
        problems.append(f"{place}: a plan is decided by a person")
    return problems


def _placement_proposed_problems(place: str, event: "EventItem") -> list[str]:
    after = event.after
    problems = []
    items = after.get("items")
    if not (
        isinstance(items, list)
        and 1 <= len(items) <= 20
        and all(
            isinstance(i, dict) and isinstance(i.get("ref"), str) and "from_parent" in i
            for i in items
        )
    ):
        problems.append(f"{place}: items must be 1 to 20 of {{ref, title, from_parent}}")
    if not _text_of(after, "reason"):
        problems.append(f"{place}: a proposal needs its reason")
    parent, epic = after.get("parent"), after.get("new_epic")
    if (isinstance(parent, str)) == (isinstance(epic, dict)):
        problems.append(f"{place}: give parent or new_epic, and not both")
    elif isinstance(epic, dict) and not _text_of(epic, "title"):
        problems.append(f"{place}: new_epic needs its title")
    return problems


def _placement_decided_problems(place: str, event: "EventItem") -> list[str]:
    problems = []
    if not _whole(event.after.get("placement")):
        problems.append(f"{place}: placement must be the number of the proposal decided")
    if event.actor_kind != "person":
        problems.append(f"{place}: a proposal is decided by a person")
    return problems


def _template_problems(place: str, event: "EventItem") -> list[str]:
    problems = []
    name = event.after.get("name")
    if not (isinstance(name, str) and _TEMPLATE_NAME.fullmatch(name)):
        problems.append(f"{place}: name must be a template's name, such as release-checklist")
    if event.action == "plan_template_saved" and not isinstance(event.after.get("template"), dict):
        problems.append(f"{place}: a saved template needs its template")
    return problems


_EVENT_SHAPES: dict[str, Callable[[str, "EventItem"], list[str]]] = {
    "gate_added": _gate_added_problems,
    "gate_removed": _gate_removed_problems,
    "plan_proposed": _plan_proposed_problems,
    "plan_revised": _plan_revised_problems,
    "plan_changes_requested": _plan_changes_requested_problems,
    "plan_approved": _plan_decided_problems,
    "plan_rejected": _plan_decided_problems,
    "plan_template_saved": _template_problems,
    "plan_template_deleted": _template_problems,
    "placement_proposed": _placement_proposed_problems,
    "placement_accepted": _placement_decided_problems,
    "placement_rejected": _placement_decided_problems,
}


def _text_of(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    return value.strip() if isinstance(value, str) else ""


def _ask_raised_problems(place: str, event: EventItem) -> list[str]:
    """An ask says what is asked, as the database requires and as `clean_ask` writes it."""
    after = event.after
    problems = []
    if not _text_of(after, "question"):
        problems.append(f"{place}: an ask needs its question")
    if after.get("kind") not in ASK_KINDS:
        problems.append(f"{place}: kind must be {' or '.join(ASK_KINDS)}")
    if after.get("effect") not in ASK_EFFECTS:
        problems.append(f"{place}: effect must be {' or '.join(ASK_EFFECTS)}")
    choices = after.get("choices")
    if choices is not None and not (
        isinstance(choices, list) and all(isinstance(c, str) and c.strip() for c in choices)
    ):
        problems.append(f"{place}: choices must be a list of words")
    return problems


def _ask_answered_problems(place: str, event: EventItem) -> list[str]:
    after = event.after
    problems = []
    if not isinstance(after.get("ask"), int) or isinstance(after.get("ask"), bool):
        problems.append(f"{place}: ask must be the number of the ask it answers")
    if not _text_of(after, "answer"):
        problems.append(f"{place}: an answer cannot be empty")
    if event.actor_kind != "person":
        problems.append(f"{place}: an ask is answered by a person")
    return problems


def _ask_cross_problems(events: list[EventItem]) -> list[str]:
    """Every answer names an ask in the file, about the same story or task, and an ask is answered
    at most once."""
    raised = {e.id: e for e in events if e.action == "ask_raised"}
    seen: set[int] = set()
    problems = []
    for index, event in enumerate(events):
        if event.action != "ask_answered" or not isinstance(event.after.get("ask"), int):
            continue
        place = f"events[{index}] (id {event.id})"
        number = event.after["ask"]
        if number not in raised:
            problems.append(f"{place}: ask={number} is not an ask in the file")
        elif raised[number].node != event.node:
            problems.append(
                f"{place}: ask={number} is about {raised[number].node}, not {event.node}"
            )
        if number in seen:
            problems.append(f"{place}: ask {number} is answered twice")
        seen.add(number)
    return problems


def _gate_and_plan_cross_problems(events: list[EventItem]) -> list[str]:
    """An approval gate names an ask in the file (about the same item), a lifted gate names a gate
    in the file once, and a decided plan or proposal to move work names one in the file once."""
    raised = {e.id: e for e in events if e.action == "ask_raised"}
    gates = {e.id: e for e in events if e.action == "gate_added"}
    plans = {e.id for e in events if e.action == "plan_proposed"}
    placements = {e.id for e in events if e.action == "placement_proposed"}
    lifted: set[int] = set()
    decided: set[int] = set()
    placed: set[int] = set()
    revised: set[tuple[int, int]] = set()
    asked: set[tuple[int, int]] = set()
    problems = []
    for index, event in enumerate(events):
        place = f"events[{index}] (id {event.id})"
        after = event.after
        if event.action == "gate_added" and after.get("kind") == "approval":
            ask = after.get("ask")
            if _whole(ask) and ask not in raised:
                problems.append(f"{place}: ask={ask} is not an ask in the file")
            elif isinstance(ask, int) and raised[ask].node != event.node:
                problems.append(f"{place}: ask={ask} is about {raised[ask].node}, not {event.node}")
        elif event.action == "gate_removed" and _whole(after.get("gate")):
            gate = after["gate"]
            if gate not in gates:
                problems.append(f"{place}: gate={gate} is not a gate in the file")
            elif gates[gate].node != event.node:
                problems.append(
                    f"{place}: gate={gate} is about {gates[gate].node}, not {event.node}"
                )
            if gate in lifted:
                problems.append(f"{place}: gate {gate} is lifted twice")
            lifted.add(gate)
        elif event.action in ("plan_approved", "plan_rejected") and _whole(after.get("plan")):
            plan = after["plan"]
            if plan not in plans:
                problems.append(f"{place}: plan={plan} is not a plan in the file")
            if plan in decided:
                problems.append(f"{place}: plan {plan} is decided twice")
            decided.add(plan)
        elif event.action in ("plan_revised", "plan_changes_requested") and _whole(
            after.get("plan")
        ):
            plan = after["plan"]
            if plan not in plans:
                problems.append(f"{place}: plan={plan} is not a plan in the file")
            revision = after.get("revision")
            if isinstance(revision, int) and _whole(revision):
                seen = revised if event.action == "plan_revised" else asked
                if (plan, revision) in seen:
                    problems.append(
                        f"{place}: revision {revision} of plan {plan} is "
                        f"{'made' if event.action == 'plan_revised' else 'asked for changes'} twice"
                    )
                seen.add((plan, revision))
        elif event.action in ("placement_accepted", "placement_rejected") and _whole(
            after.get("placement")
        ):
            placement = after["placement"]
            if placement not in placements:
                problems.append(f"{place}: placement={placement} is not a proposal in the file")
            if placement in placed:
                problems.append(f"{place}: proposal {placement} is decided twice")
            placed.add(placement)
    return problems


# The events that may say where the work is (see whereabouts.py).
_WHERE_ACTIONS = ("claimed", "claim_renewed", "claim_released", "handoff")


def _where_problems(place: str, data: dict[str, Any]) -> list[str]:
    """What an event says about where the work is and which run it was has to be what the server
    itself writes (see `clean_whereabouts`): it is shown to people and to other agents."""
    said = {field: data[field] for field in WHERE_FIELDS if field in data}
    if not all(isinstance(value, str) for value in said.values()):
        return [f"{place}: {', '.join(said)} must be text"]
    try:
        cleaned = clean_whereabouts(**said)
    except Refusal as refusal:
        return [f"{place}: {str(refusal).removesuffix(' Nothing was changed.')}"]
    if cleaned != said:
        return [f"{place}: {', '.join(said)} must be written as the server writes them"]
    return []


def _check_references(document: Document) -> list[str]:
    """Everything a file points at has to be in the file. Reported all at once, by place."""
    problems: list[str] = []
    nodes: dict[str, NodeItem] = {}
    for index, node in enumerate(document.nodes):
        ref = node_ref(node.kind, node.number)
        if ref in nodes:
            problems.append(f"nodes[{index}]: {ref} is used twice")
        nodes[ref] = node
    ids: dict[str, dict[int, Any]] = {}
    for section in _WITH_IDS:
        found: dict[int, Any] = {}
        for index, item in enumerate(getattr(document, section)):
            if item.id in found:
                problems.append(f"{section}[{index}]: id {item.id} is used twice")
            found[item.id] = item
        ids[section] = found
    rules, notes = ids["rules"], ids["notes"]

    def need_node(place: str, label: str, ref: str | None, accept: tuple[str, ...] = ()) -> None:
        if ref is None:
            return
        if accept and not ref.startswith(tuple(f"{kind}:" for kind in accept)):
            problems.append(f"{place}: {label}={ref} must be {' or '.join(_A[k] for k in accept)}")
        elif ref not in nodes:
            problems.append(f"{place}: {label}={ref} is not in the file")

    def need(place: str, label: str, found: dict[int, Any], wanted: int | None) -> None:
        if wanted is not None and wanted not in found:
            problems.append(f"{place}: {label}={wanted} is not in the file")

    parent_kind = {"story": "epic", "task": "story"}
    for i, node in enumerate(document.nodes):
        place = f"nodes[{i}] ({node_ref(node.kind, node.number)})"
        if node.kind == "epic":
            if node.parent is not None:
                problems.append(f"{place}: an epic has no parent")
        elif node.parent is None:
            problems.append(f"{place}: a {node.kind} needs a parent")
        else:
            need_node(place, "parent", node.parent, (parent_kind[node.kind],))
    for i, link in enumerate(document.links):
        place = f"links[{i}] (id {link.id})"
        need_node(place, "source", link.source)
        need_node(place, "target", link.target)
    for i, rule in enumerate(document.rules):
        need_node(f"rules[{i}] (id {rule.id})", "node", rule.node, ("story", "task"))
    if len(document.definition_of_done) > 1:
        problems.append(
            "definition_of_done: a project has one definition of done, but the file holds "
            f"{len(document.definition_of_done)}"
        )
    for i, definition in enumerate(document.definition_of_done):
        if not definition.updated_by.strip():
            problems.append(f"definition_of_done[{i}]: updated_by must be the name of a person")
    ticked: set[tuple[str, str]] = set()
    for i, check in enumerate(document.checks):
        place = f"checks[{i}] ({check.node}, item {check.item})"
        need_node(place, "node", check.node, ("story", "task"))
        if not _ITEM_KEY.fullmatch(check.item):
            problems.append(f"{place}: item must be the check's key, 8 characters from 0-9 and a-f")
        if (check.node, check.item) in ticked:
            problems.append(f"{place}: this check is ticked twice on the same item")
        ticked.add((check.node, check.item))
        if not check.text.strip():
            problems.append(f"{place}: text cannot be empty")
        if not check.checked_by.strip():
            problems.append(f"{place}: checked_by cannot be empty")
        if check.evidence is not None and not check.evidence.strip():
            problems.append(f"{place}: evidence must say how it was checked, or be left empty")
        elif check.checked_by_kind == "agent" and check.evidence is None:
            problems.append(f"{place}: a check ticked by an agent needs evidence")
    replaced_by: dict[int, list[int]] = {}
    for note in document.notes:
        if note.superseded_by_id is not None:
            replaced_by.setdefault(note.superseded_by_id, []).append(note.id)
    for i, note in enumerate(document.notes):
        place = f"notes[{i}] (id {note.id})"
        for ref in note.nodes:
            need_node(place, "nodes", ref)
        for rule_id in note.rule_ids:
            need(place, "rule_ids", rules, rule_id)
        for note_id in note.note_ids:
            need(place, "note_ids", notes, note_id)
        problems.extend(_note_problems(place, note, notes))
        if sorted(note.supersedes_ids) != sorted(replaced_by.get(note.id, [])):
            problems.append(
                f"{place}: supersedes_ids must be the notes whose superseded_by_id is "
                f"{note.id}, {sorted(replaced_by.get(note.id, []))}"
            )
    for i, event in enumerate(document.events):
        place = f"events[{i}] (id {event.id})"
        need_node(place, "node", event.node)
        need_node(place, "other_node", event.other_node)
        need(place, "rule_id", rules, event.rule_id)
        need(place, "note_id", notes, event.note_id)
        need(place, "other_note_id", notes, event.other_note_id)
        problems.extend(_event_problems(place, event))
    problems.extend(_ask_cross_problems(document.events))
    problems.extend(_gate_and_plan_cross_problems(document.events))
    return problems


def check_document(document: dict[str, Any]) -> Document:
    """Read a file that is to be imported, or refuse it in plain words, listing everything that is
    wrong with it at once. Nothing is written."""
    refuse_secrets(document)
    try:
        parsed = Document.model_validate(document)
    except ValidationError as error:
        raise refuse_file(problems_in(error)) from None
    problems = _check_references(parsed)
    if parsed.counts is not None:
        for section in SECTIONS:
            held = len(getattr(parsed, section))
            said = parsed.counts.get(section)
            if said != held:
                problems.append(
                    f"counts.{section} says {said} but the file holds {held}: the file is "
                    "cut short or was changed after it was written"
                )
    if problems:
        raise refuse_file(problems)
    return parsed


def _refused_by_the_database(error: psycopg.Error) -> Refusal:
    """The database checks everything again, whatever the file says. Say which rule a file broke,
    without the values in it."""
    if isinstance(error, psycopg.errors.RaiseException):
        # The database's own checks speak plainly already.
        detail = f" It says: {error.diag.message_primary}"
    elif error.diag.constraint_name:
        rule = error.diag.constraint_name.replace("_", " ")
        detail = f" It broke the rule '{rule}' (table {error.diag.table_name})."
    else:
        detail = ""
    return Refusal(
        "The database refused this file, so nothing was imported." + detail + " Check the file "
        "against the export it came from."
    )


class Exchange:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def export_page(self, *, after: str | None, limit: int) -> ExportPage:
        limit = page_size(limit)
        section, last = _cursor(after)
        async with self._database.transaction(snapshot=True) as conn:
            counts = await _counts(conn) if after is None else None
            start = SECTIONS.index(section)
            for position in range(start, len(SECTIONS)):
                name = SECTIONS[position]
                after_last, order = _after_clause(name)
                rows = await fetch_all(
                    conn,
                    f"{_QUERIES[name]} WHERE {after_last} ORDER BY {order} LIMIT %s",
                    (*(last if position == start else _start(name)), limit + 1),
                )
                if not rows:
                    continue
                page = [_exported(row) for row in rows[:limit]]
                if len(rows) > limit:
                    following: str | None = _cursor_after(name, page[-1])
                elif position + 1 < len(SECTIONS):
                    following = f"{SECTIONS[position + 1]}:0"
                else:
                    following = None
                return ExportPage(
                    format=FORMAT,
                    version=FORMAT_VERSION,
                    section=name,
                    items=page,
                    next_after=following,
                    counts=counts,
                )
        return ExportPage(
            format=FORMAT,
            version=FORMAT_VERSION,
            section=None,
            items=[],
            next_after=None,
            counts=counts,
        )

    async def import_document(self, document: dict[str, Any]) -> ImportResult:
        parsed = check_document(document)
        try:
            async with self._database.transaction() as conn:
                # One import at a time into a project. Nothing is locked beyond that: another
                # project's work goes on, and what is found empty here is loaded in this one step.
                await conn.execute("SELECT pg_advisory_xact_lock(2, current_project()::integer)")
                await _require_empty(conn)
                await _load(conn, parsed)
        except (
            psycopg.errors.IntegrityError,
            psycopg.errors.DataError,
            psycopg.errors.RaiseException,
        ) as error:
            raise _refused_by_the_database(error) from None
        counts = {name: len(getattr(parsed, name)) for name in SECTIONS}
        total = sum(counts.values())
        return ImportResult(
            counts=counts,
            message=(
                f"Imported {total} items with their numbers and ids, dates, confirmations and "
                "history. If an embedding service is set up, the server now indexes them for "
                "search by meaning in the background."
            ),
        )


async def _require_empty(conn: AsyncConnection) -> None:
    counts = await _counts(conn)
    claims = await fetch_one(conn, "SELECT count(*) AS held FROM claims")
    assert claims is not None
    held = {name: count for name, count in counts.items() if count}
    if claims["held"]:
        held["claims"] = int(claims["held"])
    if held:
        what = ", ".join(f"{count} {name.replace('_', ' ')}" for name, count in held.items())
        raise Refusal(
            f"This project already holds data ({what}), and an import only goes into an empty "
            "one, because it keeps the numbers and ids in the file and they would clash. Nothing "
            "was imported. Create a new project and import into that, or use a backup to restore "
            "the whole server."
        )


def _stored_status(status: str) -> str:
    return "new" if status == "ready" else status


async def _insert(conn: AsyncConnection, statement: str, rows: Iterable[tuple[Any, ...]]) -> None:
    batch = list(rows)
    if batch:
        async with conn.cursor() as cursor:
            await cursor.executemany(statement, batch)


async def _load(conn: AsyncConnection, doc: Document) -> None:
    """Write a checked file. Notes are written as they were first made (unconfirmed, not replaced),
    with all their links; then confirmations are applied, then the endings last, so that the
    database's own rules, which stop anything from changing once it has ended, are never
    bypassed."""
    rank = {kind: position for position, kind in enumerate(NODE_KINDS)}
    await _insert(
        conn,
        "INSERT INTO nodes (kind, number, parent, title, body, status, priority, size, start_on, "
        "target_on, version, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            (
                n.kind,
                n.number,
                n.parent,
                n.title,
                n.body,
                _stored_status(n.status),
                n.priority,
                n.size,
                n.start_on,
                n.target_on,
                n.version,
                n.created_at,
                n.updated_at,
            )
            for n in sorted(doc.nodes, key=lambda n: (rank[n.kind], n.number))
        ),
    )
    await _insert(
        conn,
        "INSERT INTO links (id, source, target, link_type, created_at) VALUES (%s, %s, %s, %s, %s)",
        ((x.id, x.source, x.target, x.link_type, x.created_at) for x in doc.links),
    )
    await _insert(
        conn,
        "INSERT INTO rules (id, node, title, body, status, version, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            (r.id, r.node, r.title, r.body, r.status, r.version, r.created_at, r.updated_at)
            for r in doc.rules
        ),
    )
    await _insert(
        conn,
        "INSERT INTO definition_of_done (body, version, updated_at, updated_by, updated_by_kind) "
        "VALUES (%s, %s, %s, %s, %s)",
        (
            (d.body, d.version, d.updated_at, d.updated_by, d.updated_by_kind)
            for d in doc.definition_of_done
        ),
    )
    # The items of the checklist are worked out from the text, as when a person sets it.
    await _insert(
        conn,
        "INSERT INTO definition_items (item_key, position, text, person_only, verifier_only, "
        "github) VALUES (%s, %s, %s, %s, %s, %s)",
        (
            (
                item.item,
                position,
                item.text,
                item.person_only,
                item.verifier_only,
                item.github,
            )
            for d in doc.definition_of_done
            for position, item in enumerate(definition_items(d.body), 1)
        ),
    )
    await _insert(
        conn,
        "INSERT INTO definition_checks (node, item_key, item_text, checked_by, checked_by_kind, "
        "checked_at, evidence) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            (c.node, c.item, c.text, c.checked_by, c.checked_by_kind, c.checked_at, c.evidence)
            for c in doc.checks
        ),
    )
    await _insert(
        conn,
        "INSERT INTO notes (id, text, why, decided_by, decided_on, project_wide, recorded_by, "
        "recorded_by_kind, recorded_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            (
                n.id,
                n.text,
                n.why,
                n.decided_by,
                n.decided_on,
                n.project_wide,
                n.recorded_by,
                n.recorded_by_kind,
                n.recorded_at,
            )
            for n in doc.notes
        ),
    )
    # Every link is written while its note is still in force: the database keeps the links of one
    # that has ended as they were.
    await _insert(
        conn,
        "INSERT INTO note_nodes (note_id, node) VALUES (%s, %s)",
        ((n.id, ref) for n in doc.notes for ref in n.nodes),
    )
    await _insert(
        conn,
        "INSERT INTO note_rules (note_id, rule_id) VALUES (%s, %s)",
        ((n.id, rule_id) for n in doc.notes for rule_id in n.rule_ids),
    )
    await _insert(
        conn,
        "INSERT INTO note_notes (note_id, about_id) VALUES (%s, %s)",
        ((n.id, about) for n in doc.notes for about in n.note_ids),
    )
    # A confirmation is made while the note is still in force, and not in the same step as its
    # ending: the database refuses both at once. An ended note keeps the confirmation it had.
    await _insert(
        conn,
        "UPDATE notes SET status = 'confirmed', confirmed_by = %s, confirmed_at = %s WHERE id = %s",
        ((n.confirmed_by, n.confirmed_at, n.id) for n in doc.notes if n.confirmed_by),
    )
    await _insert(
        conn,
        "UPDATE notes SET status = %s, superseded_by_id = %s, status_reason = %s WHERE id = %s",
        (
            (n.status, n.superseded_by_id, n.status_reason, n.id)
            for n in doc.notes
            if n.status in ("superseded", "withdrawn")
        ),
    )
    await _insert(
        conn,
        "INSERT INTO events (id, node, other_node, rule_id, note_id, other_note_id, "
        "action, actor, actor_kind, at, before, after) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            (
                e.id,
                e.node,
                e.other_node,
                e.rule_id,
                e.note_id,
                e.other_note_id,
                e.action,
                e.actor,
                e.actor_kind,
                e.at,
                None if e.before is None else Jsonb(e.before),
                Jsonb(e.after),
            )
            for e in doc.events
        ),
    )
    # New items continue after the highest number or id in the file, each kind after its own.
    for kind in NODE_KINDS:
        await conn.execute(
            "SELECT continue_ids_after(%s, (SELECT max(number) FROM nodes WHERE kind = %s))",
            (kind, kind),
        )
    for table in _WITH_IDS:
        await conn.execute(f"SELECT continue_ids_after('{table}', (SELECT max(id) FROM {table}))")
