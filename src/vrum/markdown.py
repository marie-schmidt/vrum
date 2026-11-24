"""A read-only Markdown copy of the graph (M7): one file per epic, story, task, acceptance criterion
(the rules of the database) and note, with its fields at the top and everything it is connected to
written as `[[file name]]` references, so a person can read it in Obsidian or keep it in git. A
note says in plain words where it stands (unconfirmed, confirmed by a person and when,
superseded by another, or withdrawn and why), and who decided it and when, if somebody did. A
note has no title: its first line is the file's title. A reference a person wrote in a text
(`[[story:12]]`, see references.py) becomes the same kind of link to that item's file. The
project's definition of done is one more file at the top of the copy (`definition-of-done.md`). A
story or task that the definition of done has checks for lists them as a task list, ticked or
not, with who ticked each one, when, and how it was checked. A node's size is in its fields. A story
or task whose holders said where the work was (its repository, branch, commit and pull request) and
which run it was says so in a section of its own, from the latest claim or handoff that did. A
story or task that agents asked a person about lists each ask as Q-12 with what was asked, who
asked, and the answer, who gave it and when, when there is one.

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
from vrum.definition_of_done import DefinitionItem, definition_items
from vrum.records import headline_of
from vrum.references import REFERENCE_PATTERN
from vrum.whereabouts import WHERE_FIELDS

_REFERENCE = re.compile(REFERENCE_PATTERN)

# The folders the export owns. A tool that writes it replaces these and touches nothing else.
FOLDERS = ("epics", "stories", "tasks", "rules", "notes")
# The one file at the top of the copy: the project's definition of done. The tool that writes the
# copy replaces it as well.
DEFINITION_FILE = "definition-of-done.md"

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
    "size",
    "start_on",
    "target_on",
    "decided_on",
    "created_at",
    "updated_at",
    "recorded_at",
    "recorded_by_kind",
    "confirmed_at",
}
_NODE_FIELDS = (
    "type",
    "number",
    "title",
    "status",
    "priority",
    "size",
    "start_on",
    "target_on",
    "version",
    "created_at",
    "updated_at",
)
_RULE_FIELDS = ("type", "id", "title", "status", "version", "created_at", "updated_at")
_DEFINITION_FIELDS = ("type", "version", "updated_at", "updated_by")
_NOTE_FIELDS = (
    "type",
    "id",
    "status",
    "status_reason",
    "project_wide",
    "decided_by",
    "decided_on",
    "recorded_by",
    "recorded_by_kind",
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
    ("discovered from", "led to"),
    ("on", "acceptance criteria"),
    ("concerns", "notes about it"),
    ("replaced by", "replaces"),
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


def _checklist(
    node: str, items: list[DefinitionItem], ticks: Mapping[tuple[str, str], Mapping[str, Any]]
) -> str:
    """The checks of a story or task as a task list: an item that is ticked says by whom and when,
    and how it was checked. A check keeps the tags that say who may tick it: [person], and
    [github] or [verifier]."""
    lines = []
    for item in items:
        tick = ticks.get((node, item.item))
        tags = "".join(
            f"[{name}] "
            for name, on in (
                ("person", item.person_only),
                ("github", item.github),
                ("verifier", item.verifier_only and not item.github),
            )
            if on
        )
        words = f"{tags}{item.text}"
        if tick is None:
            lines.append(f"- [ ] {words}")
            continue
        how = f"by {_one_line(tick['checked_by'])}, {tick['checked_at'][:10]}"
        if tick["evidence"]:
            how += f": {_one_line(tick['evidence'])}"
        lines.append(f"- [x] {words} ({how})")
    return "## Definition of done\n\n" + "\n".join(lines)


_WHERE_LABELS = (
    ("repository", "Repository"),
    ("branch", "Branch"),
    ("commit", "Commit"),
    ("pull_request", "Pull request"),
)
# The events that carry the words a holder said about where the work is.
_WHERE_ACTIONS = ("claimed", "claim_renewed", "handoff")


def _where_sections(events: list[Mapping[str, Any]]) -> dict[str, str]:
    """For each story or task, what its latest claim or handoff said about where the work is and
    which run it was, as a section; none for one that never said."""
    latest: dict[str, Mapping[str, Any]] = {}
    for event in events:
        if event["action"] in _WHERE_ACTIONS and any(f in event["after"] for f in WHERE_FIELDS):
            latest[event["node"]] = event
    sections = {}
    for node, event in latest.items():
        said = event["after"]
        lines = [f"- {label}: {said[name]}" for name, label in _WHERE_LABELS if name in said]
        run = " ".join(
            f"{said[name]}" if name == "run_label" else f"({said[name]})"
            for name in ("run_label", "client_name")
            if name in said
        )
        if run:
            lines.append(f"- Run: {run}")
        lines.append(f"- Said by: {_one_line(event['actor'])}, {event['at'][:10]}")
        sections[node] = "## Where the work is\n\n" + "\n".join(lines)
    return sections


def _ask_sections(events: list[Mapping[str, Any]]) -> dict[str, str]:
    """For each story or task that has asks, a section with a line for each: what was asked and
    by whom, whether the work waited for the answer, and the answer when there is one."""
    answers = {e["after"]["ask"]: e for e in events if e["action"] == "ask_answered"}
    lines: dict[str, list[str]] = {}
    for event in events:
        if event["action"] != "ask_raised":
            continue
        said = event["after"]
        waited = "work waited" if said["effect"] == "blocks" else "work stayed free"
        line = f"- Q-{event['id']} ({waited}): {_one_line(said['question'])}"
        if said.get("choices"):
            line += f" Choices: {', '.join(_one_line(c) for c in said['choices'])}."
        line += f" Asked by {_one_line(event['actor'])}, {event['at'][:10]}."
        answer = answers.get(event["id"])
        if answer is None:
            line += " Not answered yet."
        else:
            given = answer["after"]
            line += (
                f" Answer: {_one_line(given['answer'])}, by {_one_line(answer['actor'])}, "
                f"{answer['at'][:10]}."
            )
            if given.get("comment"):
                line += f" Comment: {_one_line(given['comment'])}"
        lines.setdefault(event["node"], []).append(line)
    return {node: "## Questions\n\n" + "\n".join(found) for node, found in lines.items()}


def _standing(item: Mapping[str, Any]) -> str:
    """Where a note stands, in plain words: its status, and when somebody decided it or it applies
    throughout the project. A replacement is written as a reference, so that it becomes a link to
    the file that replaced it. An ended note keeps the confirmation it had."""
    status = item["status"]
    reason = _one_line(item.get("status_reason") or "")
    by = ""
    if item["confirmed_by"]:
        by = f"by {_one_line(item['confirmed_by'])} on {item['confirmed_at'][:10]}"
    if status == "superseded":
        replacement = item["superseded_by_id"]
        words = "superseded" if replacement is None else f"superseded by [[note:{replacement}]]"
    elif status == "withdrawn":
        words = "withdrawn"
    else:
        words = f"confirmed {by}" if by else "unconfirmed"
    if reason and status in ("superseded", "withdrawn"):
        words += f". Why: {reason}"
    lines = [f"- Status: {words}"]
    if by and status in ("superseded", "withdrawn"):
        lines.append(f"- Confirmed: {by}, as it was when it ended")
    if item.get("decided_by"):
        lines.append(f"- Decided: by {_one_line(item['decided_by'])} on {item['decided_on']}")
    if item.get("project_wide"):
        lines.append("- Applies: throughout the project")
    return "\n".join(lines)


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

    definition = document["definition_of_done"]
    checks = definition_items(definition[0]["body"]) if definition else []
    ticks = {(tick["node"], tick["item"]): tick for tick in document["checks"]}
    where = _where_sections(document["events"])
    asked = _ask_sections(document["events"])
    for node in document["nodes"]:
        kind = node["kind"]
        body = [node["body"]] if node["body"].strip() else []
        if f"{kind}:{node['number']}" in where:
            body.append(where[f"{kind}:{node['number']}"])
        if f"{kind}:{node['number']}" in asked:
            body.append(asked[f"{kind}:{node['number']}"])
        if kind != "epic" and checks:
            body.append(_checklist(f"{kind}:{node['number']}", checks, ticks))
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
    for note in document["notes"]:
        headline = headline_of(note["text"])
        add(
            ("note", note["id"]),
            _Entry(
                f"note-{note['id']}-{_slug(headline)}",
                "notes",
                _NOTE_FIELDS,
                {**note, "type": "note"},
                headline,
                [_standing(note), note["text"], f"## Why\n\n{note['why']}"],
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
            "discovered_from": "discovered from",
        }[link["link_type"]]
        connect(split_node_ref(link["source"]), split_node_ref(link["target"]), label)
    for rule in document["rules"]:
        if rule["node"] is not None:
            connect(("rule", rule["id"]), split_node_ref(rule["node"]), "on")
    for note in document["notes"]:
        here = ("note", note["id"])
        for ref in note["nodes"]:
            connect(here, split_node_ref(ref), "concerns")
        for rule_id in note["rule_ids"]:
            connect(here, ("rule", rule_id), "concerns")
        for about in note["note_ids"]:
            connect(here, ("note", about), "concerns")
        if note["superseded_by_id"] is not None:
            connect(here, ("note", note["superseded_by_id"]), "replaced by")

    # How a reference to each item is written (`[[note:5]]`) is the kind it is keyed by.
    stems = {key: entry.stem for key, entry in entries.items()}
    files: dict[str, str] = {}
    for entry in entries.values():
        entry.links.sort(key=lambda link: (_ORDER[link[0]], *_rank(link[1])))
        files[f"{entry.folder}/{entry.stem}.md"] = _link_references(_text(entry), stems)
    files[DEFINITION_FILE] = _link_references(
        _text(
            _Entry(
                "definition-of-done",
                "",
                _DEFINITION_FIELDS,
                {"type": "definition_of_done", **(definition[0] if definition else {})},
                "Definition of done",
                [definition[0]["body"]]
                if definition and definition[0]["body"].strip()
                else ["Nobody has set a definition of done yet."],
            )
        ),
        stems,
    )
    return files
