"""A read-only Markdown copy of the graph (M7): one file per epic, story, task, rule, decision,
principle, fact and belief, with its fields at the top and everything it is connected to written as
`[[file name]]` references, so a person can read it in Obsidian or keep it in git.

It is made from the export (see exchange.py) and from nothing else, so it can never say anything
the export does not. The same graph always gives the same files, byte for byte: nothing in them
says when they were written, so a copy kept in git only changes when the graph does."""

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# The folders the export owns. A tool that writes it replaces these and touches nothing else.
FOLDERS = ("epics", "stories", "tasks", "rules", "decisions", "facts", "beliefs")

_LONGEST_SLUG = 50
# Fields written as they are, not as quoted text: whole numbers, days and times, and the words
# that come from a fixed list (none of them is one YAML would read as something else).
_BARE = {
    "id",
    "version",
    "type",
    "kind",
    "status",
    "priority",
    "origin",
    "start_on",
    "target_on",
    "happened_on",
    "decided_on",
    "created_at",
    "updated_at",
    "recorded_at",
    "confirmed_at",
}
_NODE_FIELDS = (
    "type",
    "id",
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
_DECISION_FIELDS = ("type", "id", "title", "status", "decided_by", "decided_on", "recorded_at")
_MEMORY_FIELDS = (
    "type",
    "id",
    "kind",
    "source",
    "happened_on",
    "origin",
    "recorded_by",
    "recorded_at",
    "status",
    "status_reason",
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
    ("story", "rules"),
    ("affects", "decisions that affect it"),
    ("replaced by", "replaces"),
    ("promoted from", "promoted to"),
    ("concerns", "facts and beliefs about it"),
    ("rests on", "evidence for"),
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
    """Where a file sorts among others: by kind, then by id as a number (a file is called
    kind-id-title)."""
    kind, number, _ = stem.split("-", 2)
    return kind, int(number)


def render(document: Mapping[str, Any]) -> dict[str, str]:
    """The files of the copy: path (such as `stories/story-12-login.md`) to what is in it. The
    reference `[[story-12-login]]` points at that file, whatever folder it is in."""
    entries: dict[tuple[str, int], _Entry] = {}

    def add(key: tuple[str, int], entry: _Entry) -> None:
        entries[key] = entry

    for node in document["nodes"]:
        kind = node["kind"]
        body = [node["body"]] if node["body"].strip() else []
        add(
            ("node", node["id"]),
            _Entry(
                f"{kind}-{node['id']}-{_slug(node['title'])}",
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
        kind = decision["kind"]
        add(
            ("decision", decision["id"]),
            _Entry(
                f"{kind}-{decision['id']}-{_slug(decision['title'])}",
                "decisions",
                _DECISION_FIELDS,
                {**decision, "type": kind},
                decision["title"],
                [decision["text"], f"## Reason\n\n{decision['reason']}"],
            ),
        )
    for memory in document["memories"]:
        kind = memory["type"]
        add(
            ("memory", memory["id"]),
            _Entry(
                f"{kind}-{memory['id']}-{_slug(memory['text'])}",
                "facts" if kind == "fact" else "beliefs",
                _MEMORY_FIELDS,
                memory,
                f"{kind.capitalize()} {memory['id']}",
                [memory["text"]],
            ),
        )

    def connect(source: tuple[str, int], target: tuple[str, int], label: str) -> None:
        if source not in entries or target not in entries:
            return
        outgoing, incoming = next(pair for pair in _LABELS if pair[0] == label)
        entries[source].links.append((outgoing, entries[target].stem))
        entries[target].links.append((incoming, entries[source].stem))

    for node in document["nodes"]:
        if node["parent_id"] is not None:
            connect(("node", node["id"]), ("node", node["parent_id"]), "parent")
    for link in document["links"]:
        label = {
            "needs_first": "needs first",
            "supersedes": "supersedes",
            "duplicates": "duplicates",
            "relates_to": "relates to",
        }[link["link_type"]]
        connect(("node", link["source_id"]), ("node", link["target_id"]), label)
    for rule in document["rules"]:
        if rule["story_id"] is not None:
            connect(("rule", rule["id"]), ("node", rule["story_id"]), "story")
    for decision in document["decisions"]:
        here = ("decision", decision["id"])
        for node_id in decision["node_ids"]:
            connect(here, ("node", node_id), "affects")
        for rule_id in decision["rule_ids"]:
            connect(here, ("rule", rule_id), "affects")
        if decision["superseded_by_id"] is not None:
            connect(here, ("decision", decision["superseded_by_id"]), "replaced by")
        if decision["promoted_from_id"] is not None:
            connect(here, ("memory", decision["promoted_from_id"]), "promoted from")
    for memory in document["memories"]:
        here = ("memory", memory["id"])
        for node_id in memory["node_ids"]:
            connect(here, ("node", node_id), "concerns")
        for rule_id in memory["rule_ids"]:
            connect(here, ("rule", rule_id), "concerns")
        for decision_id in memory["decision_ids"]:
            connect(here, ("decision", decision_id), "concerns")
        if memory["superseded_by_id"] is not None:
            connect(here, ("memory", memory["superseded_by_id"]), "replaced by")
        for evidence in memory["evidence"]:
            kind = "memory" if evidence["type"] == "fact" else "decision"
            connect(here, (kind, evidence["id"]), "rests on")

    files: dict[str, str] = {}
    for entry in entries.values():
        entry.links.sort(key=lambda link: (_ORDER[link[0]], *_rank(link[1])))
        files[f"{entry.folder}/{entry.stem}.md"] = _text(entry)
    return files
