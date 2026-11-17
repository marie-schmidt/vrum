"""A read-only Markdown copy of the graph (M7): one file per epic, story, task, requirement (the
rules of the database), decision and fact, with its fields at the top and everything it is
connected to written as `[[file name]]` references, so a person can read it in Obsidian or keep it
in git. A decision or fact says in plain words what it is, whether it still counts (active,
superseded by another, or withdrawn and why) and whether a person confirmed it. A reference a
person wrote in a text (`[[story:12]]`, see references.py) becomes the same kind of link to that
item's file.

It is made from the export (see exchange.py) and from nothing else, so it can never say anything
the export does not. The same graph always gives the same files, byte for byte: nothing in them
says when they were written, so a copy kept in git only changes when the graph does."""

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from vrum.common import split_node_ref
from vrum.references import REFERENCE_PATTERN

_REFERENCE = re.compile(REFERENCE_PATTERN)

# The folders the export owns. A tool that writes it replaces these and touches nothing else.
FOLDERS = ("epics", "stories", "tasks", "rules", "decisions", "facts")

_LONGEST_SLUG = 50
# Fields written as they are, not as quoted text: whole numbers, days and times, and the words
# that come from a fixed list (none of them is one YAML would read as something else).
_BARE = {
    "id",
    "number",
    "version",
    "type",
    "status",
    "priority",
    "start_on",
    "target_on",
    "decided_on",
    "created_at",
    "updated_at",
    "recorded_at",
    "confirmed_at",
}
_NODE_FIELDS = (
    "type",
    "number",
    "title",
    "status",
    "priority",
    "start_on",
    "target_on",
    "version",
    "created_at",
    "updated_at",
)
_RULE_FIELDS = ("type", "id", "title", "status", "version", "created_at", "updated_at")
_DECISION_FIELDS = (
    "type",
    "id",
    "title",
    "status",
    "status_reason",
    "project_wide",
    "decided_by",
    "decided_on",
    "confirmed_by",
    "confirmed_at",
    "recorded_at",
)
_FACT_FIELDS = (
    "type",
    "id",
    "status",
    "status_reason",
    "recorded_by",
    "recorded_at",
    "confirmed_by",
    "confirmed_at",
)
# What each kind of connection is called at the end it starts from and at the end it arrives at.
# A connection that reads the same both ways has the same name at both.
_LABELS = (
    ("parent", "children"),
    ("needs first", "needed first by"),
    ("supersedes", "superseded by"),
    ("duplicates", "duplicates"),
    ("relates to", "relates to"),
    ("story", "requirements"),
    ("affects", "decisions that affect it"),
    ("replaced by", "replaces"),
    ("concerns", "facts about it"),
)
_ORDER: dict[str, int] = {}
for _position, (_outgoing, _incoming) in enumerate(_LABELS):
    _ORDER.setdefault(_outgoing, 2 * _position)
    _ORDER.setdefault(_incoming, 2 * _position + 1)


@dataclass
class _Entry:
    stem: str
    folder: str
    fields: tuple[str, ...]
    item: Mapping[str, Any]
    heading: str
    body: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)


def _slug(text: str) -> str:
    folded = unicodedata.normalize("NFC", text).lower()
    words = re.sub(r"[\W_]+", "-", folded).strip("-")
    return words[:_LONGEST_SLUG].strip("-") or "untitled"


def _value(name: str, value: Any) -> str:
    if name in _BARE:
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _standing(noun: str, item: Mapping[str, Any]) -> str:
    """Where a decision or fact stands, in plain words: what it is, whether it still counts, and
    whether a person confirmed it. A replacement is written as a reference, so that it becomes a
    link to the file that replaced it. An ended record keeps the confirmation it had."""
    kind = noun
    if item.get("project_wide"):
        kind = "project-wide decision (it applies throughout the project)"
    status = item["status"]
    reason = _one_line(item.get("status_reason") or "")
    if status == "superseded":
        replacement = item["superseded_by_id"]
        lifecycle = (
            "superseded" if replacement is None else f"superseded by [[{noun}:{replacement}]]"
        )
    elif status == "withdrawn":
        lifecycle = "withdrawn"
    else:
        lifecycle = "active"
    if reason and status != "active":
        lifecycle += f". Why: {reason}"
    if item["confirmed_by"]:
        confirmation = (
            f"confirmed by {_one_line(item['confirmed_by'])} on {item['confirmed_at'][:10]}"
        )
        if status != "active":
            confirmation += ", as it was when it ended"
    else:
        confirmation = "not confirmed by a person"
    return "\n".join(
        [f"- Kind: {kind}", f"- Status: {lifecycle}", f"- Confirmation: {confirmation}"]
    )


def _front_matter(entry: _Entry) -> list[str]:
    lines = ["---"]
    for name in entry.fields:
        value = entry.item.get(name)
        if value is not None:
            lines.append(f"{name}: {_value(name, value)}")
    lines.append("---")
    return lines


def _text(entry: _Entry) -> str:
    parts = [*_front_matter(entry), "", f"# {' '.join(entry.heading.split())}"]
    for block in entry.body:
        parts += ["", block]
    if entry.links:
        parts += ["", "## Links", ""]
        parts += [f"- {label}: [[{stem}]]" for label, stem in entry.links]
    return "\n".join(parts) + "\n"


def _rank(stem: str) -> tuple[str, int]:
    """Where a file sorts among others: by kind, then by number (a file is called
    kind-number-title)."""
    kind, number, _ = stem.split("-", 2)
    return kind, int(number)


def _link_references(text: str, stems: Mapping[tuple[str, int], str]) -> str:
    """A reference a person wrote in a text (`[[story:12]]`) becomes a link to that item's file, as
    Obsidian reads it. One that names nothing, or the wrong kind of thing, is written as plain
    words with no brackets, so that every link in the copy points at a file."""

    def replace(found: re.Match[str]) -> str:
        kind, number = found.group(1), int(found.group(2))
        stem = stems.get((kind, number))
        return f"[[{stem}]]" if stem else f"{kind}:{number} (missing)"

    return _REFERENCE.sub(replace, text)


def render(document: Mapping[str, Any]) -> dict[str, str]:
    """The files of the copy: path (such as `stories/story-12-login.md`) to what is in it. The
    reference `[[story-12-login]]` points at that file, whatever folder it is in. Every item is
    keyed by its type and its number (an epic, story or task) or id, which is also how a
    reference written in a text names it."""
    entries: dict[tuple[str, int], _Entry] = {}

    def add(key: tuple[str, int], entry: _Entry) -> None:
        entries[key] = entry

    for node in document["nodes"]:
        kind = node["kind"]
        body = [node["body"]] if node["body"].strip() else []
        add(
            (kind, node["number"]),
            _Entry(
                f"{kind}-{node['number']}-{_slug(node['title'])}",
                {"epic": "epics", "story": "stories", "task": "tasks"}[kind],
                _NODE_FIELDS,
                {**node, "type": kind},
                node["title"],
                body,
            ),
        )
    for rule in document["rules"]:
        body = [rule["body"]] if rule["body"].strip() else []
        add(
            ("rule", rule["id"]),
            _Entry(
                f"rule-{rule['id']}-{_slug(rule['title'])}",
                "rules",
                _RULE_FIELDS,
                {**rule, "type": "rule"},
                rule["title"],
                body,
            ),
        )
    for decision in document["decisions"]:
        add(
            ("decision", decision["id"]),
            _Entry(
                f"decision-{decision['id']}-{_slug(decision['title'])}",
                "decisions",
                _DECISION_FIELDS,
                {**decision, "type": "decision"},
                decision["title"],
                [
                    _standing("decision", decision),
                    decision["text"],
                    f"## Reason\n\n{decision['reason']}",
                ],
            ),
        )
    for fact in document["facts"]:
        add(
            ("fact", fact["id"]),
            _Entry(
                f"fact-{fact['id']}-{_slug(fact['text'])}",
                "facts",
                _FACT_FIELDS,
                {**fact, "type": "fact"},
                f"Fact {fact['id']}",
                [_standing("fact", fact), fact["text"], f"## Source\n\n{fact['source']}"],
            ),
        )

    def connect(source: tuple[str, int], target: tuple[str, int], label: str) -> None:
        if source not in entries or target not in entries:
            return
        outgoing, incoming = next(pair for pair in _LABELS if pair[0] == label)
        entries[source].links.append((outgoing, entries[target].stem))
        entries[target].links.append((incoming, entries[source].stem))

    for node in document["nodes"]:
        if node["parent"] is not None:
            connect((node["kind"], node["number"]), split_node_ref(node["parent"]), "parent")
    for link in document["links"]:
        label = {
            "needs_first": "needs first",
            "supersedes": "supersedes",
            "duplicates": "duplicates",
            "relates_to": "relates to",
        }[link["link_type"]]
        connect(split_node_ref(link["source"]), split_node_ref(link["target"]), label)
    for rule in document["rules"]:
        if rule["story"] is not None:
            connect(("rule", rule["id"]), split_node_ref(rule["story"]), "story")
    for decision in document["decisions"]:
        here = ("decision", decision["id"])
        for ref in decision["nodes"]:
            connect(here, split_node_ref(ref), "affects")
        for rule_id in decision["rule_ids"]:
            connect(here, ("rule", rule_id), "affects")
        if decision["superseded_by_id"] is not None:
            connect(here, ("decision", decision["superseded_by_id"]), "replaced by")
    for fact in document["facts"]:
        here = ("fact", fact["id"])
        for ref in fact["nodes"]:
            connect(here, split_node_ref(ref), "concerns")
        for rule_id in fact["rule_ids"]:
            connect(here, ("rule", rule_id), "concerns")
        for decision_id in fact["decision_ids"]:
            connect(here, ("decision", decision_id), "concerns")
        if fact["superseded_by_id"] is not None:
            connect(here, ("fact", fact["superseded_by_id"]), "replaced by")

    # How a reference to each item is written (`[[fact:5]]`) is the kind it is keyed by.
    stems = {key: entry.stem for key, entry in entries.items()}
    files: dict[str, str] = {}
    for entry in entries.values():
        entry.links.sort(key=lambda link: (_ORDER[link[0]], *_rank(link[1])))
        files[f"{entry.folder}/{entry.stem}.md"] = _link_references(_text(entry), stems)
    return files
