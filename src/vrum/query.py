"""The filter language of people and agents: one text that says which epics, stories and tasks to
list. This module owns it: it reads the text and turns it into a condition on the `node_state`
view, so that the board's filter box and `list_nodes` (as `query`) give the same answer because
they are the same code.

The language: words and `field:value` terms.
  - Words match part of a title or body, ignoring case. "Two words" in quotes is one phrase.
  - `field:a,b` means any of these values. A leading `-` leaves the matches out.
  - Terms that are next to each other must all hold. `OR` between groups of terms means either
    group.
  - `size:m,l` finds the stories and tasks of those sizes (xs, s, m, l, xl), and `no:size` the ones
    that still need a size: a story or task with none of its own, apart from a story that has
    tasks, which adds up theirs. An epic has no size, so it is never found by either.
  - `no:field` finds the items where it is empty (`no:body` the ones with no details, `no:criteria`
    the stories and tasks with no acceptance criteria of their own). `claimed` and `waiting` on
    their own find the items that have one, and `-claimed` the ones that do not.
  - `epic:3` finds epic 3 and everything under it, `story:12` story 12 and its tasks, `number:7`
    the items with that number (with `kind:` to say which kind), and `parent:epic:3` the items
    directly under epic:3 (a parent is written as a reference, a kind and a number).
  - `start:` and `target:` take a day (2026-11-30), a range (2026-11-01..2026-12-31, either end
    may be left off) or `none`. `is:overdue` finds the overdue ones.
Everything a person typed is passed to the database as a value, never as part of the statement."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from vrum.common import Refusal, is_node_ref

# What a question can be about. A test checks that each of these is handled.
KINDS = ("epic", "story", "task")
STATUSES = ("new", "ready", "in_progress", "blocked", "done", "dropped")
PRIORITIES = ("low", "normal", "high", "urgent")
SIZES = ("xs", "s", "m", "l", "xl")
FIELDS = (
    "kind",
    "status",
    "priority",
    "size",
    "claimed",
    "parent",
    "epic",
    "story",
    "number",
    "start",
    "target",
    "is",
    "no",
)
IS_FLAGS = ("overdue", "conflict", "waiting", "claimed", "lapsed")
NO_FIELDS = ("start", "target", "claimed", "parent", "body", "waiting", "criteria", "size")
# Words that mean "has one" on their own, and "has none" after a minus.
BARE_FLAGS = ("claimed", "waiting")
MAX_QUERY_CHARS = 2000

_HELP = (
    "Use words, and field:value terms such as kind:task, status:ready,in_progress, priority:high, "
    "size:m,l, epic:3, parent:story:12, claimed:ann, target:2026-11-01..2026-12-31 or is:overdue; "
    "a leading - leaves matches out, no:field finds the empty ones, and OR joins alternatives. "
    "Put a word in quotes to search for it as it is."
)

# The condition for each way a node can be found, on the node_state view. An epic or a story is
# named by its number as the person types it (`epic:3`), and the database knows it by its
# reference (`epic:3`), so the two meet here.
_EPIC = (
    "(CASE kind WHEN 'epic' THEN ref WHEN 'story' THEN parent ELSE "
    "(SELECT p.parent FROM nodes p WHERE p.ref = node_state.parent) END)"
)
_STORY = "(CASE kind WHEN 'story' THEN ref WHEN 'task' THEN parent END)"
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
    "parent": "parent IS NULL",
    "body": "btrim(body) = ''",
    "waiting": "cardinality(waiting_on) = 0",
    # Only a story or task can hold acceptance criteria, so an epic is never found by this.
    "criteria": "(kind <> 'epic' AND criteria_total = 0)",
    # A story with tasks adds up their sizes, so it is not one that needs a size of its own.
    "size": (
        "(kind <> 'epic' AND size IS NULL AND NOT EXISTS (SELECT 1 FROM nodes k "
        "WHERE k.parent = node_state.ref AND k.status <> 'dropped'))"
    ),
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
        cleaned = value.strip()
        # At most 18 digits, as in a reference, so that it always fits the database's number.
        if not cleaned.isdecimal() or len(cleaned) > 18:
            raise _refuse(
                f"in {term.text!r}, {term.field} needs a whole number such as {term.field}:12, "
                f"but one value is {value!r}."
            )
        numbers.append(int(cleaned))
    return numbers


def _references(term: Term) -> list[str]:
    """The references a `parent:` term names, such as epic:3, each in its one written form."""
    found = []
    for value in term.values:
        cleaned = value.strip().lower()
        if not is_node_ref(cleaned):
            raise _refuse(
                f"in {term.text!r}, {term.field} needs the reference of an epic or a story, a kind "
                f"and a number, such as {term.field}:epic:3, but one value is {value!r}."
            )
        found.append(cleaned)
    return found


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
    if field == "size":
        return f"size = ANY({params.add(_choice(term, 'size', SIZES))})"
    if field == "claimed":
        names = [value.strip().lower() for value in term.values]
        return f"lower(claimed_by) = ANY({params.add(names)})"
    if field == "number":
        return f"number = ANY({params.add(_whole_numbers(term))})"
    if field == "parent":
        return f"parent = ANY({params.add(_references(term))})"
    if field in ("epic", "story"):
        refs = [f"{field}:{number}" for number in _whole_numbers(term)]
        expression = _EPIC if field == "epic" else _STORY
        return f"{expression} = ANY({params.add(refs)})"
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
        "size": "m",
        "claimed": "ann",
        "parent": "epic:3",
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
