"""Reading an export made before the current format (version 1, 2, 3, 4 or 5).

A file of version 1 or 2 holds `decisions` and `facts`. It is read as it was written, then turned
into notes, so that what is loaded is notes and nothing else:

- The decisions come first, in id order, then the facts, in id order, numbered from 1 with no
  gaps.
- A decision's title becomes the first line of its text (the title, a blank line, then the text;
  just the text when the two are equal), its reason becomes `why`, and who decided it, and when,
  are kept. A fact's source becomes `why`, and nobody decided it.
- Who recorded a decision is read from the event that recorded it, since a decision had no field
  for it (`unknown` when the file has no such event). A fact keeps who recorded it.
- What a fact concerned among decisions becomes a link from the note to the other note. Everything
  else, the lifecycle (what replaced what, and why a note ended), the confirmation, the
  project-wide flag, the links to epics, stories, tasks and acceptance criteria, and the time
  each was recorded, is kept, with the new ids.
- Each decision or fact event becomes the matching note event on the new id. A reference written
  in a text, such as `[[decision:7]]`, is left as it was written.

A file of version 3 holds notes whose status is `active`, `superseded` or `withdrawn`, with the
confirmation kept apart, and acceptance criteria (the rules) that are `held`, `settled` or
`dropped`. A file of version 1 or 2 is first turned into one of version 3, and then every file of
version 3 is turned into one of version 4:

- An active note that a person confirmed is `confirmed` and one nobody confirmed is `unconfirmed`.
  A superseded or withdrawn note stays so, and keeps who confirmed it and when.
- A rule that was `held` is `open` and one that was `settled` is `met`.
- The events say the same: a rule's status in the new words, a note that ended as it
  stood (confirmed or not), and a confirmation or its removal as a change of status.

Every file of version 4 is then turned into one of version 5 (see below), and every file of version
5 into the current one (version 6), which differs in one more way: a node has a `size`, and the
file holds the ticks of the definition of done's checks. An older file has no sizes and no ticks,
so its nodes load with no size, and its `counts` say 0 for the ticks.

The step from version 4 to version 5 differs in three ways: a rule is on a story or a task, so the
`story` of a rule (and of a rule event's
`before` and `after`) is `node` now; the file may hold a definition of done, and an older one holds
none; and so its `counts` say 0 for it.

A file of version 1, written before names were verified, says nothing about as what facts and
events were recorded: they load as `unknown`. A file of version 2 or later must say."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from pydantic import ValidationError

from vrum.exchange_shapes import (
    ActorKind,
    Day,
    Id,
    Item,
    LegacyStatus,
    Moment,
    NodeRef,
    problems_in,
)

_TOP_LEVEL = {
    "format",
    "version",
    "counts",
    "nodes",
    "links",
    "rules",
    "decisions",
    "facts",
    "events",
}


class LegacyDecision(Item):
    id: Id
    title: str
    text: str
    reason: str
    decided_by: str
    decided_on: Day
    project_wide: bool
    status: LegacyStatus
    superseded_by_id: Id | None
    status_reason: str | None
    confirmed_by: str | None
    confirmed_at: Moment | None
    recorded_at: Moment
    nodes: list[NodeRef]
    rule_ids: list[Id]


class LegacyFact(Item):
    id: Id
    text: str
    source: str
    recorded_by: str
    recorded_by_kind: ActorKind = "unknown"
    recorded_at: Moment
    status: LegacyStatus
    superseded_by_id: Id | None
    status_reason: str | None
    confirmed_by: str | None
    confirmed_at: Moment | None
    nodes: list[NodeRef]
    rule_ids: list[Id]
    decision_ids: list[Id]


class LegacyEvent(Item):
    id: Id
    node: str | None
    other_node: str | None
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
    actor_kind: ActorKind = "unknown"
    at: Moment
    before: dict[str, Any] | None
    after: dict[str, Any]


class LegacySections(Item):
    decisions: list[LegacyDecision]
    facts: list[LegacyFact]
    events: list[LegacyEvent]


@dataclass
class Converted:
    """An old file as the current format: `document` is what exchange.py reads, `origins` says for
    each new note which decision or fact it was (for the problems it may have), and `problems` is
    everything that stopped the conversion, so that the file is refused with all of it."""

    document: dict[str, Any] = field(default_factory=dict)
    origins: dict[int, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def _the_text_of(title: str, text: str) -> str:
    heading = title.strip()
    return text if heading == text.strip() else f"{heading}\n\n{text}"


def _day(value: date) -> str:
    return value.isoformat()


def _moment(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _remapped(
    kind: str, action: str, part: dict[str, Any] | None, new_of: dict[int, int], note_id: int
) -> dict[str, Any] | None:
    """What an event recorded about a decision or a fact, as it is said about a note: the text and
    the reason under their new names, and every id as the new id."""
    if part is None:
        return None
    changed = dict(part)
    if action == "recorded":
        if kind == "decision":
            changed["text"] = _the_text_of(str(part.get("title", "")), str(part.get("text", "")))
            changed["why"] = changed.pop("reason", "")
            changed.pop("title", None)
        else:
            changed["why"] = changed.pop("source", "")
        replaced = changed.pop("supersedes_id", None)
        replaced_all = changed.pop("supersedes_ids", None)
        if isinstance(replaced, int) and replaced in new_of:
            changed["supersedes_ids"] = [new_of[replaced]]
        elif isinstance(replaced_all, list):
            changed["supersedes_ids"] = [new_of.get(item, item) for item in replaced_all]
    if isinstance(changed.get("superseded_by_id"), int):
        changed["superseded_by_id"] = new_of.get(changed["superseded_by_id"])
    if f"{kind}_id" in changed:
        changed.pop(f"{kind}_id")
        changed["note_id"] = note_id
    return changed


def convert(document: dict[str, Any]) -> Converted:
    """An export of version 1, 2, 3, 4 or 5 as one of the current version, or every problem that
    stops it. Nothing here writes anything."""
    if document.get("version") == 5:
        return _six(Converted(document=document))
    if document.get("version") == 4:
        return _six(_five(Converted(document=document)))
    if document.get("version") == 3:
        return _six(_five(_four(document, {})))
    problems = [
        f"{name}: this is not part of the format" for name in document if name not in _TOP_LEVEL
    ]
    try:
        legacy = LegacySections.model_validate(
            {name: document[name] for name in ("decisions", "facts", "events") if name in document}
        )
    except ValidationError as error:
        return Converted(problems=problems + problems_in(error))
    problems += _count_problems(document, legacy)
    if document.get("version") == 2:
        problems += [
            f"facts[{i}] (id {fact.id}): recorded_by_kind is missing"
            for i, fact in enumerate(legacy.facts)
            if "recorded_by_kind" not in fact.model_fields_set
        ]
        problems += [
            f"events[{i}] (id {event.id}): actor_kind is missing"
            for i, event in enumerate(legacy.events)
            if "actor_kind" not in event.model_fields_set
        ]
    decisions = sorted(enumerate(legacy.decisions), key=lambda pair: pair[1].id)
    facts = sorted(enumerate(legacy.facts), key=lambda pair: pair[1].id)
    problems += _twice("decisions", [(i, item.id) for i, item in decisions])
    problems += _twice("facts", [(i, item.id) for i, item in facts])
    new_of_decision = {item.id: number for number, (_, item) in enumerate(decisions, start=1)}
    new_of_fact = {
        item.id: len(decisions) + number for number, (_, item) in enumerate(facts, start=1)
    }
    recorders: dict[int, tuple[str, str]] = {}
    for event in legacy.events:
        if event.action == "decision_recorded" and event.decision_id is not None:
            recorders.setdefault(event.decision_id, (event.actor, event.actor_kind))

    notes: list[dict[str, Any]] = []
    origins: dict[int, str] = {}
    for i, decision in decisions:
        new_id = new_of_decision[decision.id]
        place = f"decisions[{i}] (id {decision.id})"
        replacement = _mapped(
            problems,
            place,
            "superseded_by_id",
            decision.superseded_by_id,
            new_of_decision,
            "decision",
        )
        recorded_by, kind = recorders.get(decision.id, ("unknown", "unknown"))
        origins[new_id] = f"decision {decision.id}"
        notes.append(
            {
                "id": new_id,
                "text": _the_text_of(decision.title, decision.text),
                "why": decision.reason,
                "decided_by": decision.decided_by,
                "decided_on": _day(decision.decided_on),
                "project_wide": decision.project_wide,
                "recorded_by": recorded_by,
                "recorded_by_kind": kind,
                "recorded_at": _moment(decision.recorded_at),
                "status": decision.status,
                "superseded_by_id": replacement,
                "supersedes_ids": [],
                "status_reason": decision.status_reason,
                "confirmed_by": decision.confirmed_by,
                "confirmed_at": _moment(decision.confirmed_at),
                "nodes": decision.nodes,
                "rule_ids": decision.rule_ids,
                "note_ids": [],
            }
        )
    for i, fact in facts:
        new_id = new_of_fact[fact.id]
        place = f"facts[{i}] (id {fact.id})"
        replacement = _mapped(
            problems, place, "superseded_by_id", fact.superseded_by_id, new_of_fact, "fact"
        )
        about = [
            _mapped(problems, place, "decision_ids", decision_id, new_of_decision, "decision")
            for decision_id in fact.decision_ids
        ]
        origins[new_id] = f"fact {fact.id}"
        notes.append(
            {
                "id": new_id,
                "text": fact.text,
                "why": fact.source,
                "decided_by": None,
                "decided_on": None,
                "project_wide": False,
                "recorded_by": fact.recorded_by,
                "recorded_by_kind": fact.recorded_by_kind,
                "recorded_at": _moment(fact.recorded_at),
                "status": fact.status,
                "superseded_by_id": replacement,
                "supersedes_ids": [],
                "status_reason": fact.status_reason,
                "confirmed_by": fact.confirmed_by,
                "confirmed_at": _moment(fact.confirmed_at),
                "nodes": fact.nodes,
                "rule_ids": fact.rule_ids,
                "note_ids": sorted(item for item in about if item is not None),
            }
        )
    by_id = {note["id"]: note for note in notes}
    for note in notes:
        if note["superseded_by_id"] is not None and note["superseded_by_id"] in by_id:
            by_id[note["superseded_by_id"]]["supersedes_ids"].append(note["id"])
    for note in notes:
        note["supersedes_ids"].sort()

    events = [
        _event(problems, i, event, new_of_decision, new_of_fact)
        for i, event in enumerate(legacy.events)
    ]
    if problems:
        return Converted(problems=problems)
    converted = {
        "format": document.get("format"),
        "version": 3,
        "counts": None
        if document.get("counts") is None
        else {
            "nodes": len(document["nodes"]),
            "links": len(document["links"]),
            "rules": len(document["rules"]),
            "notes": len(notes),
            "events": len(events),
        },
        "nodes": document.get("nodes"),
        "links": document.get("links"),
        "rules": document.get("rules"),
        "notes": notes,
        "events": events,
    }
    return _six(_five(_four(converted, origins)))


_RULE_WORDS = {"held": "open", "settled": "met", "dropped": "dropped"}


def _four(document: dict[str, Any], origins: dict[int, str]) -> Converted:
    """A file of version 3 as one of version 4: the new words for the status of a rule and
    of a note, in the lists and in the events that say how they changed. Anything that is not the
    shape of a version 3 file is left as it is, for the strict reading of exchange.py to report."""
    problems: list[str] = []
    confirmed: dict[int, bool] = {}

    def rule(i: int, item: dict[str, Any]) -> dict[str, Any]:
        status = item.get("status")
        if isinstance(status, str) and status in _RULE_WORDS:
            return {**item, "status": _RULE_WORDS[status]}
        problems.append(
            f"rules[{i}]: status must be held, settled or dropped in a file of version 3 or earlier"
        )
        return item

    def note(i: int, item: dict[str, Any]) -> dict[str, Any]:
        stood = item.get("confirmed_by") is not None
        if isinstance(item.get("id"), int):
            confirmed[item["id"]] = stood
        status = item.get("status")
        if status == "active":
            return {**item, "status": "confirmed" if stood else "unconfirmed"}
        if status not in ("superseded", "withdrawn"):
            problems.append(
                f"notes[{i}]: status must be active, superseded or withdrawn in a file of "
                "version 3 or earlier"
            )
        return item

    def event(i: int, item: dict[str, Any]) -> dict[str, Any]:
        return {
            **item,
            **{
                part: _new_words(item, part, confirmed)
                for part in ("before", "after")
                if part in item
            },
        }

    changed = {
        section: _each(document[section], change)
        for section, change in (("rules", rule), ("notes", note), ("events", event))
        if section in document
    }
    if problems:
        return Converted(problems=problems)
    return Converted(document={**document, "version": 4, **changed}, origins=origins)


def _five(converted: Converted) -> Converted:
    """A file of version 4 as one of version 5: a rule is on a story or a task, so `story` is
    `node` on every rule and in what its events say, the file holds a definition of done (an older
    file holds none) and its counts say so."""
    if converted.problems:
        return converted
    document = converted.document
    if "definition_of_done" in document:
        return Converted(
            problems=[
                "definition_of_done: this is not part of the format in a file of version 4 or "
                "earlier"
            ]
        )

    def renamed(item: dict[str, Any]) -> dict[str, Any]:
        return {("node" if key == "story" else key): value for key, value in item.items()}

    def rule(i: int, item: dict[str, Any]) -> dict[str, Any]:
        return renamed(item) if "story" in item and "node" not in item else item

    def event(i: int, item: dict[str, Any]) -> dict[str, Any]:
        action = item.get("action")
        if not (isinstance(action, str) and action.startswith("rule_")):
            return item
        return {
            **item,
            **{
                part: renamed(item[part])
                for part in ("before", "after")
                if isinstance(item.get(part), dict)
            },
        }

    changed = {
        section: _each(document[section], change)
        for section, change in (("rules", rule), ("events", event))
        if section in document
    }
    counts = document.get("counts")
    return Converted(
        document={
            **document,
            "version": 5,
            **changed,
            "definition_of_done": [],
            **({"counts": {**counts, "definition_of_done": 0}} if isinstance(counts, dict) else {}),
        },
        origins=converted.origins,
    )


def _six(converted: Converted) -> Converted:
    """A file of version 5 as one of version 6: a node has a `size` (none, in an older file), the
    file holds the ticks of the definition of done's checks (none, in an older file) and its
    counts say so."""
    if converted.problems:
        return converted
    document = converted.document
    if "checks" in document:
        return Converted(
            problems=["checks: this is not part of the format in a file of version 5 or earlier"]
        )
    problems: list[str] = []

    def node(i: int, item: dict[str, Any]) -> dict[str, Any]:
        if "size" in item:
            problems.append(
                f"nodes[{i}]: size is not part of the format in a file of version 5 or earlier"
            )
            return item
        return {**item, "size": None}

    changed = {"nodes": _each(document["nodes"], node)} if "nodes" in document else {}
    if problems:
        return Converted(problems=problems)
    counts = document.get("counts")
    return Converted(
        document={
            **document,
            "version": 6,
            **changed,
            "checks": [],
            **({"counts": {**counts, "checks": 0}} if isinstance(counts, dict) else {}),
        },
        origins=converted.origins,
    )


def _each(items: Any, change: Callable[[int, dict[str, Any]], dict[str, Any]]) -> Any:
    """A list of a file with `change` made to each of its objects. A value that is not a list of
    objects is returned as it is."""
    if not isinstance(items, list):
        return items
    return [change(i, item) if isinstance(item, dict) else item for i, item in enumerate(items)]


def _new_words(event: dict[str, Any], part: str, confirmed: dict[int, bool]) -> Any:
    """What an event says (`before` or `after`) with its status in the words of now: a
    rule's, a note that ended as it stood, and a confirmation as a change of status."""
    changes = event.get(part)
    action = event.get("action")
    if not isinstance(changes, dict) or not isinstance(action, str):
        return changes
    status = changes.get("status")
    if action.startswith("rule_") and isinstance(status, str) and status in _RULE_WORDS:
        return {**changes, "status": _RULE_WORDS[status]}
    if action in ("note_superseded", "note_withdrawn") and part == "before" and status == "active":
        note_id = event.get("note_id")
        stood = isinstance(note_id, int) and confirmed.get(note_id, False)
        return {**changes, "status": "confirmed" if stood else "unconfirmed"}
    if action == "note_confirmed":
        return {"status": "unconfirmed" if part == "before" else "confirmed", **changes}
    if action == "note_unconfirmed":
        return {"status": "confirmed" if part == "before" else "unconfirmed", **changes}
    return changes


def _twice(name: str, pairs: list[tuple[int, int]]) -> list[str]:
    """The ids used more than once in a list, given as (place in the file, id) sorted by id."""
    return [
        f"{name}[{i}] (id {item_id}): id {item_id} is used twice"
        for (i, item_id), (_, previous) in zip(pairs[1:], pairs, strict=False)
        if item_id == previous
    ]


def _mapped(
    problems: list[str], place: str, label: str, old: int | None, new_of: dict[int, int], noun: str
) -> int | None:
    if old is None:
        return None
    if old not in new_of:
        problems.append(f"{place}: {label}={old} is not a {noun} in the file")
        return None
    return new_of[old]


def _count_problems(document: dict[str, Any], legacy: LegacySections) -> list[str]:
    counts = document.get("counts")
    if counts is None:
        return []
    if not isinstance(counts, dict):
        return ["counts: must be an object saying how many items each list holds"]
    held = {
        "nodes": document.get("nodes"),
        "links": document.get("links"),
        "rules": document.get("rules"),
        "decisions": legacy.decisions,
        "facts": legacy.facts,
        "events": legacy.events,
    }
    return [
        f"counts.{name} says {counts.get(name)} but the file holds {len(items)}: the file is "
        "cut short or was changed after it was written"
        for name, items in held.items()
        if isinstance(items, list) and counts.get(name) != len(items)
    ]


def _event(
    problems: list[str],
    index: int,
    event: LegacyEvent,
    new_of_decision: dict[int, int],
    new_of_fact: dict[int, int],
) -> dict[str, Any]:
    """A decision or fact event as the event of the note it was about. Any other event as it is."""
    place = f"events[{index}] (id {event.id})"
    action = event.action
    note_id: int | None = None
    other_note_id: int | None = None
    new_action: str = action
    before, after = event.before, event.after
    if action.startswith("decision_"):
        if event.decision_id is None or event.fact_id is not None:
            problems.append(f"{place}: the action '{action}' needs decision_id, and no fact_id")
        else:
            note_id = _mapped(
                problems, place, "decision_id", event.decision_id, new_of_decision, "decision"
            )
            new_action = "note_" + action.removeprefix("decision_")
            before = _remapped(
                "decision",
                action.removeprefix("decision_"),
                event.before,
                new_of_decision,
                note_id or 0,
            )
            after = (
                _remapped(
                    "decision",
                    action.removeprefix("decision_"),
                    event.after,
                    new_of_decision,
                    note_id or 0,
                )
                or {}
            )
    elif action.startswith("fact_"):
        if event.fact_id is None:
            problems.append(f"{place}: the action '{action}' needs fact_id")
        else:
            note_id = _mapped(problems, place, "fact_id", event.fact_id, new_of_fact, "fact")
            other_note_id = _mapped(
                problems, place, "decision_id", event.decision_id, new_of_decision, "decision"
            )
            new_action = "note_" + action.removeprefix("fact_")
            before = _remapped(
                "fact", action.removeprefix("fact_"), event.before, new_of_fact, note_id or 0
            )
            after = (
                _remapped(
                    "fact", action.removeprefix("fact_"), event.after, new_of_fact, note_id or 0
                )
                or {}
            )
    elif event.decision_id is not None or event.fact_id is not None:
        problems.append(f"{place}: the action '{action}' needs no decision_id or fact_id")
    return {
        "id": event.id,
        "node": event.node,
        "other_node": event.other_node,
        "rule_id": event.rule_id,
        "note_id": note_id,
        "other_note_id": other_note_id,
        "action": new_action,
        "actor": event.actor,
        "actor_kind": event.actor_kind,
        "at": event.at.isoformat(),
        "before": before,
        "after": after,
    }
