"""The filter language of people and agents: one text that says which epics, stories and tasks to
list. This module owns it: it reads the text and turns it into a condition on the `node_state`
view, so that the board's filter box and `list_nodes` (as `query`) give the same answer because
they are the same code.

The language: words and `field:value` terms.
  - Words match part of a title or body, ignoring case. "Two words" in quotes is one phrase.
  - `field:a,b` means any of these values. A leading `-` leaves the matches out.
  - Terms that are next to each other must all hold. `OR` between groups of terms means either
    group.
  - `no:field` finds the items where it is empty. `claimed` and `waiting` on their own find the
    items that have one, and `-claimed` the ones that do not.
  - `start:` and `target:` take a day (2026-11-30), a range (2026-11-01..2026-12-31, either end
    may be left off) or `none`. `is:overdue` finds the overdue ones.
Everything a person typed is passed to the database as a value, never as part of the statement."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from vrum.common import Refusal

# What a question can be about. A test checks that each of these is handled.
KINDS = ("epic", "story", "task")
STATUSES = ("new", "ready", "in_progress", "blocked", "done", "dropped")
PRIORITIES = ("low", "normal", "high", "urgent")
FIELDS = (
    "kind",
    "status",
    "priority",
    "claimed",
    "parent",
    "epic",
    "story",
    "id",
    "start",
    "target",
    "is",
    "no",
)
IS_FLAGS = ("overdue", "conflict", "waiting", "claimed", "lapsed")
NO_FIELDS = ("start", "target", "claimed", "parent", "body", "waiting")
# Words that mean "has one" on their own, and "has none" after a minus.
BARE_FLAGS = ("claimed", "waiting")
MAX_QUERY_CHARS = 2000

_HELP = (
    "Use words, and field:value terms such as kind:task, status:ready,in_progress, priority:high, "
    "claimed:ann, target:2026-11-01..2026-12-31 or is:overdue; a leading - leaves matches out, "
    "no:field finds the empty ones, and OR joins alternatives. Put a word in quotes to search for "
    "it as it is."
)

# The condition for each way a node can be found, on the node_state view.
_EPIC = (
    "(CASE kind WHEN 'epic' THEN id WHEN 'story' THEN parent_id ELSE "
    "(SELECT p.parent_id FROM nodes p WHERE p.id = node_state.parent_id) END)"
)
_STORY = "(CASE kind WHEN 'story' THEN id WHEN 'task' THEN parent_id END)"
_FLAG_SQL = {
    "overdue": "overdue",
    "conflict": "cardinality(schedule_conflicts) > 0",
    "waiting": "cardinality(waiting_on) > 0",
    "claimed": "claimed_by IS NOT NULL",
    "lapsed": "lapsed_claim_by IS NOT NULL",
}
_EMPTY_SQL = {
    "start": "start_on IS NULL",
    "target": "target_on IS NULL",
    "claimed": "claimed_by IS NULL",
    "parent": "parent_id IS NULL",
    "body": "btrim(body) = ''",
    "waiting": "cardinality(waiting_on) = 0",
}


@dataclass(frozen=True)
class Term:
    """One thing to hold: a word, or a field with its values. `negated` leaves matches out."""

    field: str | None  # None for a word
    values: tuple[str, ...]
    negated: bool
    text: str  # as it was typed, for messages


def _refuse(problem: str) -> Refusal:
    return Refusal(f"The query was not understood: {problem} Nothing was listed. {_HELP}")


def _scan(text: str) -> list[list[Term]]:
    """Read the text into groups of terms (the groups are what OR separates)."""
    groups: list[list[Term]] = [[]]
    position, length = 0, len(text)
    while position < length:
        if text[position].isspace():
            position += 1
            continue
        start = position
        negated = (
            text[position] == "-" and position + 1 < length and not text[position + 1].isspace()
        )
        if negated:
            position += 1
        if text[position] == '"':
            phrase, position = _quoted(text, position)
            _end_of_term(text, position, start)
            groups[-1].append(Term(None, (phrase,), negated, text[start:position]))
            continue
        found = re.compile(r"([A-Za-z_]+):").match(text, position)
        if found is not None:
            name = found.group(1).lower()
            values: list[str] = []
            position = found.end()
            while True:
                if position < length and text[position] == '"':
                    value, position = _quoted(text, position)
                else:
                    value_start = position
                    while (
                        position < length and not text[position].isspace() and text[position] != ","
                    ):
                        position += 1
                    value = text[value_start:position]
                values.append(value)
                if position < length and text[position] == ",":
                    position += 1
                    continue
                break
            _end_of_term(text, position, start)
            groups[-1].append(Term(name, tuple(values), negated, text[start:position]))
            continue
        while position < length and not text[position].isspace():
            position += 1
        word = text[start + (1 if negated else 0) : position]
        if word == "OR" and not negated:
            if not groups[-1]:
                raise _refuse("OR needs something before it.")
            groups.append([])
            continue
        groups[-1].append(Term(None, (word,), negated, text[start:position]))
    if len(groups) > 1 and not groups[-1]:
        raise _refuse("OR needs something after it.")
    return [group for group in groups if group]


def _quoted(text: str, position: int) -> tuple[str, int]:
    closing = text.find('"', position + 1)
    if closing < 0:
        raise _refuse(f"the quote that opens at character {position + 1} is never closed.")
    return text[position + 1 : closing], closing + 1


def _end_of_term(text: str, position: int, start: int) -> None:
    if position < len(text) and not text[position].isspace():
        raise _refuse(
            f"{text[start : position + 1]!r} is followed by more text with no space. Put a space "
            "between terms."
        )


def _choice(term: Term, label: str, allowed: tuple[str, ...]) -> list[str]:
    values = [value.strip().lower() for value in term.values]
    for value in values:
        if value not in allowed:
            raise _refuse(
                f"in {term.text!r}, {label} must be one of {', '.join(allowed)}, but one value is "
                f"{value!r}."
            )
    return values


def _whole_numbers(term: Term) -> list[int]:
    numbers = []
    for value in term.values:
        cleaned = value.strip().removeprefix("#")
        if not cleaned.isdecimal():
            raise _refuse(
                f"in {term.text!r}, {term.field} needs a whole number such as {term.field}:12, "
                f"but one value is {value!r}."
            )
        numbers.append(int(cleaned))
    return numbers


def _day(term: Term, value: str) -> date:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise _refuse(
        f"in {term.text!r}, {value!r} is not a day. Write it as YYYY-MM-DD, such as 2026-11-30."
    )


class _Params:
    """Collects the values of a query under names that cannot clash."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def add(self, value: Any) -> str:
        name = f"q{len(self.values)}"
        self.values[name] = value
        return f"%({name})s"


def _dates(term: Term, column: str, params: _Params) -> str:
    alternatives = []
    for raw in term.values:
        value = raw.strip().lower()
        if value == "none":
            alternatives.append(f"{column} IS NULL")
        elif ".." in value:
            low_text, _, high_text = value.partition("..")
            if not low_text and not high_text:
                raise _refuse(
                    f"in {term.text!r}, a range needs at least one day, such as 2026-11-01.."
                )
            low = _day(term, low_text) if low_text else None
            high = _day(term, high_text) if high_text else None
            if low is not None and high is not None and low > high:
                raise _refuse(f"in {term.text!r}, the range starts after it ends.")
            parts = []
            if low is not None:
                parts.append(f"{column} >= {params.add(low)}")
            if high is not None:
                parts.append(f"{column} <= {params.add(high)}")
            alternatives.append("(" + " AND ".join(parts) + ")")
        else:
            alternatives.append(f"{column} = {params.add(_day(term, value))}")
    return "(" + " OR ".join(alternatives) + ")"


def _escape_like(word: str) -> str:
    return word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _condition(term: Term, params: _Params) -> str:
    """The SQL that is true for a node this term finds (the caller leaves out the unknowns)."""
    if term.field is None:
        word = term.values[0]
        if word.lower() in BARE_FLAGS and not term.text.lstrip("-").startswith('"'):
            return _FLAG_SQL[word.lower()]
        if not word.strip():
            raise _refuse("a quoted phrase has nothing in it.")
        pattern = params.add(f"%{_escape_like(word)}%")
        return f"(title ILIKE {pattern} ESCAPE '\\' OR body ILIKE {pattern} ESCAPE '\\')"
    field = term.field
    if field not in FIELDS:
        raise _refuse(
            f"there is no field called {field!r} (in {term.text!r}). The fields are "
            f"{', '.join(FIELDS)}. To search for text that has a colon in it, put it in quotes."
        )
    if any(not value.strip() for value in term.values):
        raise _refuse(f"{field}: needs a value, such as {field}:{_example(field)}.")
    if field == "kind":
        return f"kind = ANY({params.add(_choice(term, 'kind', KINDS))})"
    if field == "status":
        return f"status = ANY({params.add(_choice(term, 'status', STATUSES))})"
    if field == "priority":
        return f"priority = ANY({params.add(_choice(term, 'priority', PRIORITIES))})"
    if field == "claimed":
        names = [value.strip().lower() for value in term.values]
        return f"lower(claimed_by) = ANY({params.add(names)})"
    if field in ("parent", "id", "epic", "story"):
        expression = {"parent": "parent_id", "id": "id", "epic": _EPIC, "story": _STORY}[field]
        return f"{expression} = ANY({params.add(_whole_numbers(term))})"
    if field in ("start", "target"):
        return _dates(term, f"{field}_on", params)
    if field == "is":
        flags = _choice(term, "is", IS_FLAGS)
        return "(" + " OR ".join(_FLAG_SQL[flag] for flag in flags) + ")"
    empties = _choice(term, "no", NO_FIELDS)
    return "(" + " OR ".join(_EMPTY_SQL[name] for name in empties) + ")"


def _example(field: str) -> str:
    return {
        "kind": "task",
        "status": "ready",
        "priority": "high",
        "claimed": "ann",
        "is": "overdue",
        "no": "claimed",
        "start": "2026-11-01..2026-12-31",
        "target": "2026-11-01..2026-12-31",
    }.get(field, "12")


def compile_query(text: str | None) -> tuple[str, dict[str, Any]]:
    """The condition on `node_state` that the query asks for, and the values it refers to by name
    (psycopg style, `%(q0)s`). An empty query asks for everything: the condition is `true`. A
    query that cannot be understood is refused in plain words."""
    if text is None or not text.strip():
        return "true", {}
    if len(text) > MAX_QUERY_CHARS:
        raise _refuse(
            f"it is {len(text):,} characters long, and the longest allowed is {MAX_QUERY_CHARS:,}."
        )
    params = _Params()
    groups = []
    for group in _scan(text):
        conditions = []
        for term in group:
            sql = f"COALESCE({_condition(term, params)}, false)"
            conditions.append(f"NOT {sql}" if term.negated else sql)
        groups.append("(" + " AND ".join(conditions) + ")")
    return "(" + " OR ".join(groups) + ")", params.values
