"""Moving the whole graph out of one server and into another as data (M7).

`export_page` reads everything a page at a time, in a fixed order, as plain data. `import_document`
loads such a file into a server that holds nothing yet, keeping every number, id, date,
confirmation and event, in one step: either all of it is loaded or none of it. Backing up and
restoring the whole database is separate (scripts/backup.sh and scripts/restore.sh); this is the
portable, readable form.

An epic, story or task is in the file by its kind and its number within its kind (`kind` and
`number`), and everything that points at one holds its ref (`story:12`): a node's `parent`, a
link's `source` and `target`, a requirement's `story`, a decision's or fact's `nodes`, an event's
`node` and `other_node`. A requirement, decision, fact, link or event has its own `id`.

Facts and decisions are exported as they stand: their lifecycle (active, superseded or withdrawn,
what replaced them, why), who confirmed them and when (an ended record keeps the confirmation it
had), and what they are linked to.

Left out on purpose: claims (a claim is a lease that runs out, not part of the graph; the events
that recorded it are exported) and everything that is worked out from the rest (what is ready, the
search columns and the semantic index, which the server rebuilds)."""

import re
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

import psycopg
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

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

FORMAT = "vrum-export"
FORMAT_VERSION = 1
# The order everything is written in, and the order an import needs: a node before what points to
# it, decisions before the facts that concern them, events last.
SECTIONS = ("nodes", "links", "rules", "decisions", "facts", "events")
# How many problems in a file are listed before "and N more".
_MOST_PROBLEMS = 20
_A = {"epic": "an epic", "story": "a story"}

_QUERIES = {
    "nodes": (
        "SELECT kind, number, parent, title, body, status, priority, start_on, target_on, "
        "version, created_at, updated_at FROM node_state"
    ),
    "links": "SELECT id, source, target, link_type, created_at FROM links",
    "rules": "SELECT id, story, title, body, status, version, created_at, updated_at FROM rules",
    "decisions": (
        "SELECT id, title, text, reason, decided_by, decided_on, project_wide, status, "
        "superseded_by_id, status_reason, confirmed_by, confirmed_at, recorded_at, nodes, "
        "rule_ids FROM decision_state"
    ),
    "facts": (
        "SELECT id, text, source, recorded_by, recorded_at, status, superseded_by_id, "
        "status_reason, confirmed_by, confirmed_at, nodes, rule_ids, decision_ids "
        "FROM fact_state"
    ),
    "events": (
        "SELECT id, node, other_node, rule_id, decision_id, fact_id, action, actor, at, "
        "before, after FROM events"
    ),
}


class ExportPage(BaseModel):
    format: str = Field(description=f'Always "{FORMAT}".')
    version: int = Field(description="The version of this file format, now 1.")
    section: str | None = Field(
        description="Which list `items` belongs to: nodes, links, rules, decisions, facts or "
        "events. Empty when there is nothing at all to export."
    )
    items: list[dict[str, Any]] = Field(
        description="The items of that list, in a fixed order: nodes as epics, then stories, then "
        "tasks, each by number; the others by id. A page never mixes lists."
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


def _cursor(after: str | None) -> tuple[str, tuple[int, ...]]:
    """Where a page starts: its list, and the last item of the one before it. A node is placed by
    its kind (counted from 1: epic, story, task) and number, any other item by its id."""
    if after is None:
        return SECTIONS[0], (0, 0)
    section, _, last = after.partition(":")
    if section == "nodes":
        try:
            kind, number = split_node_ref(parse_node_ref(last))
        except Refusal:
            pass
        else:
            return section, (NODE_KINDS.index(kind) + 1, number)
    elif section in SECTIONS and last.isdecimal():
        return section, (int(last),)
    raise Refusal(
        f"after must be left empty for the first page, or be the next_after of the previous "
        f"page (such as nodes:story:12 or links:200), but it is {after[:40]!r}."
    )


def _after_clause(section: str) -> tuple[str, str]:
    """The test for items after the last one of the previous page, and the order that makes it
    work."""
    if section == "nodes":
        return "(kind_rank, number) > (%s, %s)", "kind_rank, number"
    return "id > %s", "id"


def _cursor_after(section: str, item: dict[str, Any]) -> str:
    if section == "nodes":
        return f"nodes:{node_ref(item['kind'], item['number'])}"
    return f"{section}:{item['id']}"


async def _counts(conn: AsyncConnection) -> dict[str, int]:
    row = await fetch_one(
        conn,
        "SELECT " + ", ".join(f"(SELECT count(*) FROM {name}) AS {name}" for name in SECTIONS),
    )
    assert row is not None
    return {name: int(row[name]) for name in SECTIONS}


# -- reading a file that is to be imported -------------------------------------------------------


def _day(value: Any) -> date:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError("must be a real day written as YYYY-MM-DD")


def _moment(value: Any) -> datetime:
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        moment = None
    if moment is None:
        raise ValueError("must be a date and time such as 2026-11-15T20:02:24.859168Z")
    if moment.tzinfo is None:
        raise ValueError("must say its time zone (end it with Z for UTC)")
    return moment


Day = Annotated[date, BeforeValidator(_day)]
Moment = Annotated[datetime, BeforeValidator(_moment)]
Id = Annotated[int, Field(strict=True, gt=0)]


def _a_node_ref(value: Any) -> str:
    """A ref in its one written form (`story:12`: lower case, no spaces), or a plain complaint."""
    try:
        if parse_node_ref(value) == value:
            return str(value)
    except Refusal:
        pass
    raise ValueError("must name an epic, story or task by its kind and number, such as story:12")


NodeRef = Annotated[str, BeforeValidator(_a_node_ref)]


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NodeItem(_Item):
    kind: Literal["epic", "story", "task"]
    number: Id
    parent: NodeRef | None
    title: str
    body: str
    # `ready` is what a node nobody has started shows when nothing it needs first is open. It is
    # worked out, so it is kept as `new`.
    status: Literal["new", "ready", "in_progress", "blocked", "done", "dropped"]
    priority: Literal["low", "normal", "high", "urgent"]
    start_on: Day | None
    target_on: Day | None
    version: Id
    created_at: Moment
    updated_at: Moment


class LinkItem(_Item):
    id: Id
    source: NodeRef
    target: NodeRef
    link_type: Literal["needs_first", "supersedes", "duplicates", "relates_to"]
    created_at: Moment


class RuleItem(_Item):
    id: Id
    story: NodeRef | None
    title: str
    body: str
    status: Literal["held", "settled", "dropped"]
    version: Id
    created_at: Moment
    updated_at: Moment


Status = Literal["active", "superseded", "withdrawn"]


class DecisionItem(_Item):
    id: Id
    title: str
    text: str
    reason: str
    decided_by: str
    decided_on: Day
    project_wide: bool
    status: Status
    superseded_by_id: Id | None
    status_reason: str | None
    confirmed_by: str | None
    confirmed_at: Moment | None
    recorded_at: Moment
    nodes: list[NodeRef]
    rule_ids: list[Id]


class FactItem(_Item):
    id: Id
    text: str
    source: str
    recorded_by: str
    recorded_at: Moment
    status: Status
    superseded_by_id: Id | None
    status_reason: str | None
    confirmed_by: str | None
    confirmed_at: Moment | None
    nodes: list[NodeRef]
    rule_ids: list[Id]
    decision_ids: list[Id]


class EventItem(_Item):
    id: Id
    node: NodeRef | None
    other_node: NodeRef | None
    rule_id: Id | None
    decision_id: Id | None
    fact_id: Id | None
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
        "decision_recorded",
        "decision_superseded",
        "decision_withdrawn",
        "decision_linked",
        "decision_unlinked",
        "decision_confirmed",
        "decision_unconfirmed",
        "fact_recorded",
        "fact_superseded",
        "fact_withdrawn",
        "fact_linked",
        "fact_unlinked",
        "fact_confirmed",
        "fact_unconfirmed",
    ]
    actor: str
    at: Moment
    before: dict[str, Any] | None
    after: dict[str, Any]


class Document(_Item):
    format: Literal["vrum-export"]
    version: Literal[1]
    counts: dict[str, int] | None = None
    nodes: list[NodeItem]
    links: list[LinkItem]
    rules: list[RuleItem]
    decisions: list[DecisionItem]
    facts: list[FactItem]
    events: list[EventItem]


def _where(location: Iterable[int | str]) -> str:
    text = ""
    for part in location:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += f".{part}" if text else str(part)
    return text or "the file"


def _problems_in(error: ValidationError) -> list[str]:
    """What is wrong with the shape of a file, one line per place, without the values found there
    (they could be anything, a secret included)."""
    seen: set[str] = set()
    problems: list[str] = []
    for item in error.errors():
        place = _where(item["loc"])
        if place in seen:
            continue
        seen.add(place)
        message = item["msg"].removeprefix("Value error, ")
        problems.append(f"{place}: {message}")
    return problems


def _refuse_file(problems: list[str]) -> Refusal:
    shown = "; ".join(problems[:_MOST_PROBLEMS])
    more = f"; and {len(problems) - _MOST_PROBLEMS} more" if len(problems) > _MOST_PROBLEMS else ""
    return Refusal(
        f"This is not a file that import_graph can load, and nothing was imported. "
        f"What is wrong: {shown}{more}. Use a file written by export_graph."
    )


def _lifecycle_problems(
    noun: str, place: str, record: DecisionItem | FactItem, found: dict[int, Any]
) -> list[str]:
    """Where a fact or decision stands has to hang together, as the database requires: a
    superseded one names the later one that replaced it, a withdrawn one does not, the reason it
    ended is there exactly when one is needed (a fact says why however it ended; a decision only
    says why it was withdrawn), and a confirmation names a person and a time or neither."""
    problems: list[str] = []
    replacement = record.superseded_by_id
    if record.status == "superseded":
        if replacement is None:
            problems.append(
                f"{place}: a superseded {noun} needs superseded_by_id, the {noun} that replaced it"
            )
        elif replacement not in found:
            problems.append(f"{place}: superseded_by_id={replacement} is not a {noun} in the file")
        elif replacement <= record.id:
            problems.append(
                f"{place}: superseded_by_id={replacement} must be a later id than {record.id}: a "
                f"{noun} is replaced by one recorded after it"
            )
    elif replacement is not None:
        problems.append(
            f"{place}: superseded_by_id is only for a superseded {noun}, and this one is "
            f"{record.status}"
        )
    needs_reason = record.status == "withdrawn" or (noun == "fact" and record.status != "active")
    if needs_reason:
        if record.status_reason is None or not record.status_reason.strip():
            problems.append(f"{place}: a {record.status} {noun} needs status_reason, why it ended")
    elif record.status_reason is not None:
        only_for = "a fact that has ended" if noun == "fact" else "a withdrawn decision"
        problems.append(
            f"{place}: status_reason is only for {only_for}, and this one is {record.status}"
        )
    if (record.confirmed_by is None) != (record.confirmed_at is None):
        problems.append(
            f"{place}: confirmed_by and confirmed_at go together: give both, or neither when no "
            "person has confirmed it"
        )
    elif record.confirmed_by is not None and not record.confirmed_by.strip():
        problems.append(f"{place}: confirmed_by must be the name of a person")
    return problems


def _event_problems(place: str, event: EventItem) -> list[str]:
    """An event says what it is about, as the database requires: a rule event names its rule and
    no decision or fact, a decision event names its decision and no fact, a fact event names its
    fact, and any other event names its node and nothing else."""
    action = event.action
    if action.startswith("rule_"):
        wrong = event.rule_id is None or event.decision_id is not None or event.fact_id is not None
        needs = "rule_id, and no decision_id or fact_id"
    elif action.startswith("decision_"):
        wrong = event.decision_id is None or event.fact_id is not None
        needs = "decision_id, and no fact_id"
    elif action.startswith("fact_"):
        wrong = event.fact_id is None
        needs = "fact_id"
    else:
        wrong = (
            event.node is None
            or event.rule_id is not None
            or event.decision_id is not None
            or event.fact_id is not None
        )
        needs = "node, and no rule_id, decision_id or fact_id"
    return [f"{place}: the action '{action}' needs {needs}"] if wrong else []


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
    for section in SECTIONS[1:]:
        found: dict[int, Any] = {}
        for index, item in enumerate(getattr(document, section)):
            if item.id in found:
                problems.append(f"{section}[{index}]: id {item.id} is used twice")
            found[item.id] = item
        ids[section] = found
    rules, decisions, facts = ids["rules"], ids["decisions"], ids["facts"]

    def need_node(place: str, label: str, ref: str | None, accept: str = "") -> None:
        if ref is None:
            return
        if accept and not ref.startswith(f"{accept}:"):
            problems.append(f"{place}: {label}={ref} must be {_A[accept]}")
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
            need_node(place, "parent", node.parent, parent_kind[node.kind])
    for i, link in enumerate(document.links):
        place = f"links[{i}] (id {link.id})"
        need_node(place, "source", link.source)
        need_node(place, "target", link.target)
    for i, rule in enumerate(document.rules):
        need_node(f"rules[{i}] (id {rule.id})", "story", rule.story, "story")
    for i, decision in enumerate(document.decisions):
        place = f"decisions[{i}] (id {decision.id})"
        for ref in decision.nodes:
            need_node(place, "nodes", ref)
        for rule_id in decision.rule_ids:
            need(place, "rule_ids", rules, rule_id)
        problems.extend(_lifecycle_problems("decision", place, decision, decisions))
    for i, fact in enumerate(document.facts):
        place = f"facts[{i}] (id {fact.id})"
        for ref in fact.nodes:
            need_node(place, "nodes", ref)
        for rule_id in fact.rule_ids:
            need(place, "rule_ids", rules, rule_id)
        for decision_id in fact.decision_ids:
            need(place, "decision_ids", decisions, decision_id)
        problems.extend(_lifecycle_problems("fact", place, fact, facts))
    for i, event in enumerate(document.events):
        place = f"events[{i}] (id {event.id})"
        need_node(place, "node", event.node)
        need_node(place, "other_node", event.other_node)
        need(place, "rule_id", rules, event.rule_id)
        need(place, "decision_id", decisions, event.decision_id)
        need(place, "fact_id", facts, event.fact_id)
        problems.extend(_event_problems(place, event))
    return problems


def check_document(document: dict[str, Any]) -> Document:
    """Read a file that is to be imported, or refuse it in plain words, listing everything that is
    wrong with it at once. Nothing is written."""
    refuse_secrets(document)
    try:
        parsed = Document.model_validate(document)
    except ValidationError as error:
        raise _refuse_file(_problems_in(error)) from None
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
        raise _refuse_file(problems)
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
                    (*(last if position == start else (0,)), limit + 1),
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
        what = ", ".join(f"{count} {name}" for name, count in held.items())
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
    """Write a checked file. Facts and decisions are written as they were first made (active, not
    confirmed, not replaced), with all their links; then confirmations are applied, then the
    endings last, so that the database's own rules, which stop anything from changing once it has
    ended, are never bypassed."""
    rank = {kind: position for position, kind in enumerate(NODE_KINDS)}
    await _insert(
        conn,
        "INSERT INTO nodes (kind, number, parent, title, body, status, priority, start_on, "
        "target_on, version, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            (
                n.kind,
                n.number,
                n.parent,
                n.title,
                n.body,
                _stored_status(n.status),
                n.priority,
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
        "INSERT INTO rules (id, story, title, body, status, version, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            (r.id, r.story, r.title, r.body, r.status, r.version, r.created_at, r.updated_at)
            for r in doc.rules
        ),
    )
    await _insert(
        conn,
        "INSERT INTO decisions (id, title, text, reason, decided_by, decided_on, project_wide, "
        "recorded_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            (
                d.id,
                d.title,
                d.text,
                d.reason,
                d.decided_by,
                d.decided_on,
                d.project_wide,
                d.recorded_at,
            )
            for d in doc.decisions
        ),
    )
    await _insert(
        conn,
        "INSERT INTO facts (id, text, source, recorded_by, recorded_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        ((f.id, f.text, f.source, f.recorded_by, f.recorded_at) for f in doc.facts),
    )
    # Every link is written while its decision or fact is still active: the database keeps the
    # links of one that has ended as they were.
    await _insert(
        conn,
        "INSERT INTO decision_nodes (decision_id, node) VALUES (%s, %s)",
        ((d.id, ref) for d in doc.decisions for ref in d.nodes),
    )
    await _insert(
        conn,
        "INSERT INTO decision_rules (decision_id, rule_id) VALUES (%s, %s)",
        ((d.id, rule_id) for d in doc.decisions for rule_id in d.rule_ids),
    )
    await _insert(
        conn,
        "INSERT INTO fact_nodes (fact_id, node) VALUES (%s, %s)",
        ((f.id, ref) for f in doc.facts for ref in f.nodes),
    )
    await _insert(
        conn,
        "INSERT INTO fact_rules (fact_id, rule_id) VALUES (%s, %s)",
        ((f.id, rule_id) for f in doc.facts for rule_id in f.rule_ids),
    )
    await _insert(
        conn,
        "INSERT INTO fact_decisions (fact_id, decision_id) VALUES (%s, %s)",
        ((f.id, decision_id) for f in doc.facts for decision_id in f.decision_ids),
    )
    # A confirmation is made while the record is still active, and not in the same step as its
    # ending: the database refuses both at once. An ended record keeps the confirmation it had.
    await _insert(
        conn,
        "UPDATE decisions SET confirmed_by = %s, confirmed_at = %s WHERE id = %s",
        ((d.confirmed_by, d.confirmed_at, d.id) for d in doc.decisions if d.confirmed_by),
    )
    await _insert(
        conn,
        "UPDATE facts SET confirmed_by = %s, confirmed_at = %s WHERE id = %s",
        ((f.confirmed_by, f.confirmed_at, f.id) for f in doc.facts if f.confirmed_by),
    )
    await _insert(
        conn,
        "UPDATE decisions SET status = %s, superseded_by_id = %s, status_reason = %s WHERE id = %s",
        (
            (d.status, d.superseded_by_id, d.status_reason, d.id)
            for d in doc.decisions
            if d.status != "active"
        ),
    )
    await _insert(
        conn,
        "UPDATE facts SET status = %s, superseded_by_id = %s, status_reason = %s WHERE id = %s",
        (
            (f.status, f.superseded_by_id, f.status_reason, f.id)
            for f in doc.facts
            if f.status != "active"
        ),
    )
    await _insert(
        conn,
        "INSERT INTO events (id, node, other_node, rule_id, decision_id, fact_id, "
        "action, actor, at, before, after) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            (
                e.id,
                e.node,
                e.other_node,
                e.rule_id,
                e.decision_id,
                e.fact_id,
                e.action,
                e.actor,
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
    for table in SECTIONS[1:]:
        await conn.execute(f"SELECT continue_ids_after('{table}', (SELECT max(id) FROM {table}))")
