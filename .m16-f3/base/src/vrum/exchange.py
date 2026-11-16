"""Moving the whole graph out of one server and into another as data (M7).

`export_page` reads everything a page at a time, in a fixed order, as plain data. `import_document`
loads such a file into a server that holds nothing yet, keeping every id, date, origin and event,
in one step: either all of it is loaded or none of it. Backing up and restoring the whole database
is separate (scripts/backup.sh and scripts/restore.sh); this is the portable, readable form.

Left out on purpose: claims (a claim is a lease that runs out, not part of the graph; the events
that recorded it are exported) and everything that is worked out from the rest (what is ready, what
is stale, the search columns and the semantic index, which the server rebuilds)."""

import re
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

import psycopg
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from vrum.common import Refusal, fetch_all, fetch_one, page_size, refuse_secrets
from vrum.db import Database

FORMAT = "vrum-export"
FORMAT_VERSION = 1
# The order everything is written in, and the order an import needs: a node before what points to
# it, memories before the decisions promoted from them, events last.
SECTIONS = ("nodes", "links", "rules", "decisions", "memories", "events")
# How many problems in a file are listed before "and N more".
_MOST_PROBLEMS = 20
_A = {"epic": "an epic", "story": "a story", "belief": "a belief", "fact": "a fact"}

_QUERIES = {
    "nodes": (
        "SELECT id, kind, parent_id, title, body, status, priority, start_on, target_on, "
        "version, created_at, updated_at FROM node_state"
    ),
    "links": "SELECT id, source_id, target_id, link_type, created_at FROM links",
    "rules": (
        "SELECT id, story_id, title, body, status, version, created_at, updated_at FROM rules"
    ),
    "decisions": (
        "SELECT id, kind, title, text, reason, decided_by, decided_on, status, "
        "superseded_by_id, promoted_from_id, recorded_at, node_ids, rule_ids FROM decision_state"
    ),
    "memories": (
        "SELECT id, type, kind, text, source, happened_on, origin, recorded_by, recorded_at, "
        "status, superseded_by_id, status_reason, confirmed_by, confirmed_at, node_ids, rule_ids, "
        "decision_ids, evidence FROM memory_state"
    ),
    "events": (
        "SELECT id, node_id, other_node_id, rule_id, decision_id, memory_id, action, actor, at, "
        "before, after FROM events"
    ),
}


class ExportPage(BaseModel):
    format: str = Field(description=f'Always "{FORMAT}".')
    version: int = Field(description="The version of this file format, now 1.")
    section: str | None = Field(
        description="Which list `items` belongs to: nodes, links, rules, decisions, memories or "
        "events. Empty when there is nothing at all to export."
    )
    items: list[dict[str, Any]] = Field(
        description="The items of that list, in id order. A page never mixes lists."
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


def _exported(section: str, row: dict[str, Any]) -> dict[str, Any]:
    if section == "memories":
        # The view says more about each piece of evidence than the file needs: what it is.
        row = {
            **row,
            "evidence": [{"type": item["type"], "id": item["id"]} for item in row["evidence"]],
        }
    return {key: _plain(value) for key, value in row.items()}


def _cursor(after: str | None) -> tuple[str, int]:
    if after is None:
        return SECTIONS[0], 0
    section, _, last = after.partition(":")
    if section in SECTIONS and last.isdecimal():
        return section, int(last)
    raise Refusal(
        f"after must be left empty for the first page, or be the next_after of the previous "
        f"page (such as nodes:200), but it is {after[:40]!r}."
    )


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


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NodeItem(_Item):
    id: Id
    kind: Literal["epic", "story", "task"]
    parent_id: Id | None
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
    source_id: Id
    target_id: Id
    link_type: Literal["needs_first", "supersedes", "duplicates", "relates_to"]
    created_at: Moment


class RuleItem(_Item):
    id: Id
    story_id: Id | None
    title: str
    body: str
    status: Literal["held", "settled", "dropped"]
    version: Id
    created_at: Moment
    updated_at: Moment


class DecisionItem(_Item):
    id: Id
    kind: Literal["decision", "principle"]
    title: str
    text: str
    reason: str
    decided_by: str
    decided_on: Day
    status: Literal["active", "superseded"]
    superseded_by_id: Id | None
    promoted_from_id: Id | None
    recorded_at: Moment
    node_ids: list[Id]
    rule_ids: list[Id]


class EvidenceItem(_Item):
    type: Literal["fact", "decision"]
    id: Id


class MemoryItem(_Item):
    id: Id
    type: Literal["fact", "belief"]
    kind: Literal["world", "experience"] | None
    text: str
    source: str | None
    happened_on: Day | None
    origin: Literal["stated", "extracted", "derived"]
    recorded_by: str
    recorded_at: Moment
    status: Literal["active", "superseded", "retracted"]
    superseded_by_id: Id | None
    status_reason: str | None
    confirmed_by: str | None
    confirmed_at: Moment | None
    node_ids: list[Id]
    rule_ids: list[Id]
    decision_ids: list[Id]
    evidence: list[EvidenceItem]


class EventItem(_Item):
    id: Id
    node_id: Id | None
    other_node_id: Id | None
    rule_id: Id | None
    decision_id: Id | None
    memory_id: Id | None
    action: str
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
    memories: list[MemoryItem]
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


def _check_references(document: Document) -> list[str]:
    """Everything a file points at has to be in the file. Reported all at once, by place."""
    problems: list[str] = []
    ids: dict[str, dict[int, Any]] = {}
    for section in SECTIONS:
        found: dict[int, Any] = {}
        for index, item in enumerate(getattr(document, section)):
            if item.id in found:
                problems.append(f"{section}[{index}]: id {item.id} is used twice")
            found[item.id] = item
        ids[section] = found
    nodes, rules, decisions, memories = (
        ids["nodes"],
        ids["rules"],
        ids["decisions"],
        ids["memories"],
    )

    def need(
        place: str, label: str, found: dict[int, Any], wanted: int | None, accept: str = ""
    ) -> Any:
        if wanted is None:
            return None
        item = found.get(wanted)
        if item is None:
            problems.append(f"{place}: {label}={wanted} is not in the file")
        elif accept and item.kind != accept and getattr(item, "type", None) != accept:
            problems.append(f"{place}: {label}={wanted} must be {_A[accept]}")
        return item

    parent_kind = {"story": "epic", "task": "story"}
    for i, node in enumerate(document.nodes):
        place = f"nodes[{i}] (id {node.id})"
        if node.kind == "epic":
            if node.parent_id is not None:
                problems.append(f"{place}: an epic has no parent")
        elif node.parent_id is None:
            problems.append(f"{place}: a {node.kind} needs a parent")
        else:
            need(place, "parent_id", nodes, node.parent_id, parent_kind[node.kind])
    for i, link in enumerate(document.links):
        place = f"links[{i}] (id {link.id})"
        need(place, "source_id", nodes, link.source_id)
        need(place, "target_id", nodes, link.target_id)
    for i, rule in enumerate(document.rules):
        need(f"rules[{i}] (id {rule.id})", "story_id", nodes, rule.story_id, "story")
    for i, decision in enumerate(document.decisions):
        place = f"decisions[{i}] (id {decision.id})"
        for node_id in decision.node_ids:
            need(place, "node_ids", nodes, node_id)
        for rule_id in decision.rule_ids:
            need(place, "rule_ids", rules, rule_id)
        need(place, "superseded_by_id", decisions, decision.superseded_by_id)
        need(place, "promoted_from_id", memories, decision.promoted_from_id, "belief")
    for i, memory in enumerate(document.memories):
        place = f"memories[{i}] (id {memory.id})"
        for node_id in memory.node_ids:
            need(place, "node_ids", nodes, node_id)
        for rule_id in memory.rule_ids:
            need(place, "rule_ids", rules, rule_id)
        for decision_id in memory.decision_ids:
            need(place, "decision_ids", decisions, decision_id)
        replacement = need(place, "superseded_by_id", memories, memory.superseded_by_id)
        if replacement is not None and replacement.type != memory.type:
            problems.append(f"{place}: a {memory.type} can only be replaced by a {memory.type}")
        for evidence in memory.evidence:
            if evidence.type == "fact":
                need(place, "evidence fact", memories, evidence.id, "fact")
            else:
                need(place, "evidence decision", decisions, evidence.id)
    for i, event in enumerate(document.events):
        place = f"events[{i}] (id {event.id})"
        need(place, "node_id", nodes, event.node_id)
        need(place, "other_node_id", nodes, event.other_node_id)
        need(place, "rule_id", rules, event.rule_id)
        need(place, "decision_id", decisions, event.decision_id)
        need(place, "memory_id", memories, event.memory_id)
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
                rows = await fetch_all(
                    conn,
                    f"{_QUERIES[name]} WHERE id > %s ORDER BY id LIMIT %s",
                    (last if position == start else 0, limit + 1),
                )
                if not rows:
                    continue
                page = [_exported(name, row) for row in rows[:limit]]
                if len(rows) > limit:
                    following: str | None = f"{name}:{page[-1]['id']}"
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
                f"Imported {total} items with their ids, dates, origins and history. If an "
                "embedding service is set up, the server now indexes them for search by meaning "
                "in the background."
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
            "one, because it keeps the ids in the file and they would clash. Nothing was "
            "imported. Create a new project and import into that, or use a backup to restore "
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
    """Write a checked file. Everything is written as it was first made (active, not confirmed,
    not replaced) and the endings are applied last, so that the database's own rules, which stop
    anything from changing once it has ended, are never bypassed."""
    rank = {"epic": 0, "story": 1, "task": 2}
    await _insert(
        conn,
        "INSERT INTO nodes (id, kind, parent_id, title, body, status, priority, start_on, "
        "target_on, version, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            (
                n.id,
                n.kind,
                n.parent_id,
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
            for n in sorted(doc.nodes, key=lambda n: (rank[n.kind], n.id))
        ),
    )
    await _insert(
        conn,
        "INSERT INTO links (id, source_id, target_id, link_type, created_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        ((x.id, x.source_id, x.target_id, x.link_type, x.created_at) for x in doc.links),
    )
    await _insert(
        conn,
        "INSERT INTO rules (id, story_id, title, body, status, version, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            (r.id, r.story_id, r.title, r.body, r.status, r.version, r.created_at, r.updated_at)
            for r in doc.rules
        ),
    )
    await _insert(
        conn,
        "INSERT INTO memories (id, type, kind, text, source, happened_on, origin, recorded_by, "
        "recorded_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            (
                m.id,
                m.type,
                m.kind,
                m.text,
                m.source,
                m.happened_on,
                m.origin,
                m.recorded_by,
                m.recorded_at,
            )
            for m in doc.memories
        ),
    )
    await _insert(
        conn,
        "INSERT INTO decisions (id, kind, title, text, reason, decided_by, decided_on, "
        "promoted_from_id, recorded_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            (
                d.id,
                d.kind,
                d.title,
                d.text,
                d.reason,
                d.decided_by,
                d.decided_on,
                d.promoted_from_id,
                d.recorded_at,
            )
            for d in doc.decisions
        ),
    )
    await _insert(
        conn,
        "INSERT INTO decision_nodes (decision_id, node_id) VALUES (%s, %s)",
        ((d.id, node_id) for d in doc.decisions for node_id in d.node_ids),
    )
    await _insert(
        conn,
        "INSERT INTO decision_rules (decision_id, rule_id) VALUES (%s, %s)",
        ((d.id, rule_id) for d in doc.decisions for rule_id in d.rule_ids),
    )
    await _insert(
        conn,
        "INSERT INTO memory_nodes (memory_id, node_id) VALUES (%s, %s)",
        ((m.id, node_id) for m in doc.memories for node_id in m.node_ids),
    )
    await _insert(
        conn,
        "INSERT INTO memory_rules (memory_id, rule_id) VALUES (%s, %s)",
        ((m.id, rule_id) for m in doc.memories for rule_id in m.rule_ids),
    )
    await _insert(
        conn,
        "INSERT INTO memory_decisions (memory_id, decision_id) VALUES (%s, %s)",
        ((m.id, decision_id) for m in doc.memories for decision_id in m.decision_ids),
    )
    await _insert(
        conn,
        "INSERT INTO belief_evidence (belief_id, fact_id, decision_id) VALUES (%s, %s, %s)",
        (
            (m.id, e.id if e.type == "fact" else None, e.id if e.type == "decision" else None)
            for m in doc.memories
            for e in m.evidence
        ),
    )
    # A confirmation is made before a belief ends, and not in the same step: the database refuses
    # both at once.
    await _insert(
        conn,
        "UPDATE memories SET confirmed_by = %s, confirmed_at = %s WHERE id = %s",
        ((m.confirmed_by, m.confirmed_at, m.id) for m in doc.memories if m.confirmed_by),
    )
    await _insert(
        conn,
        "UPDATE memories SET status = %s, superseded_by_id = %s, status_reason = %s WHERE id = %s",
        (
            (m.status, m.superseded_by_id, m.status_reason, m.id)
            for m in doc.memories
            if m.status != "active"
        ),
    )
    await _insert(
        conn,
        "UPDATE decisions SET status = 'superseded', superseded_by_id = %s WHERE id = %s",
        ((d.superseded_by_id, d.id) for d in doc.decisions if d.status == "superseded"),
    )
    await _insert(
        conn,
        "INSERT INTO events (id, node_id, other_node_id, rule_id, decision_id, memory_id, "
        "action, actor, at, before, after) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            (
                e.id,
                e.node_id,
                e.other_node_id,
                e.rule_id,
                e.decision_id,
                e.memory_id,
                e.action,
                e.actor,
                e.at,
                None if e.before is None else Jsonb(e.before),
                Jsonb(e.after),
            )
            for e in doc.events
        ),
    )
    # New items continue after the highest id in the file.
    for table in SECTIONS:
        await conn.execute(f"SELECT continue_ids_after('{table}', (SELECT max(id) FROM {table}))")
