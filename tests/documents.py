"""Small builders for export files (the data export_graph gives and import_graph takes), for the
tests that need no database. `a_file` is a file of the current version (6: a node has a `size`,
and the file holds the ticks of the definition of done's checks). `a_version_5_file` is one of
version 5, whose nodes have no size and which has no checks. `a_version_4_file` is one of version
4, whose rules are on a `story` and which has no definition of done. `a_version_3_file` is one of
version 3, whose notes are `active` and whose rules are `held` or `settled`. `a_legacy_file` is one
of version 2, which holds decisions and facts instead, as the servers before notes wrote it. A rule
is built once, with `rule`, in the current form: the builders of the older files give it the
`story` those files had, and a node the size those files did not have."""

from typing import Any

WHEN = "2026-11-15T10:00:00.000000Z"


def node(kind: str, number: int, parent: str | None = None, **more: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "number": number,
        "parent": parent,
        "title": f"{kind} {number}",
        "body": "",
        "status": "new",
        "priority": "normal",
        "size": None,
        "start_on": None,
        "target_on": None,
        "version": 1,
        "created_at": WHEN,
        "updated_at": WHEN,
        **more,
    }


def link(link_id: int, source: str, target: str, link_type: str) -> dict[str, Any]:
    return {
        "id": link_id,
        "source": source,
        "target": target,
        "link_type": link_type,
        "created_at": WHEN,
    }


def rule(rule_id: int, node: str | None, **more: Any) -> dict[str, Any]:
    return {
        "id": rule_id,
        "node": node,
        "title": f"rule {rule_id}",
        "body": "",
        "status": "open",
        "version": 1,
        "created_at": WHEN,
        "updated_at": WHEN,
        **more,
    }


def definition(version: int = 2, **more: Any) -> dict[str, Any]:
    """The definition of done as a file of version 5 or 6 holds it."""
    return {
        "body": "- Tests pass",
        "version": version,
        "updated_at": WHEN,
        "updated_by": "Marie",
        "updated_by_kind": "person",
        **more,
    }


def check(node: str, item: str, **more: Any) -> dict[str, Any]:
    """A tick of one check of the definition of done, as a file of version 6 holds it."""
    return {
        "node": node,
        "item": item,
        "text": "Tests pass",
        "checked_by": "ann",
        "checked_by_kind": "agent",
        "checked_at": WHEN,
        "evidence": "212 tests pass",
        **more,
    }


def _on_a_story(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rules as the files before version 5 wrote them: on a `story`, not on a `node`."""
    return [
        {("story" if name == "node" else name): value for name, value in item.items()}
        for item in rules
    ]


def _without_size(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nodes as the files before version 6 wrote them: with no `size`."""
    return [{name: value for name, value in item.items() if name != "size"} for item in nodes]


def note(note_id: int, **more: Any) -> dict[str, Any]:
    """A note of the current version: unconfirmed, or confirmed when it names who confirmed it."""
    return {
        "id": note_id,
        "text": f"note {note_id}",
        "why": "a chat",
        "decided_by": None,
        "decided_on": None,
        "project_wide": False,
        "recorded_by": "ann",
        "recorded_by_kind": "agent",
        "recorded_at": WHEN,
        "status": "confirmed" if more.get("confirmed_by") else "unconfirmed",
        "superseded_by_id": None,
        "supersedes_ids": [],
        "status_reason": None,
        "confirmed_by": None,
        "confirmed_at": None,
        "nodes": [],
        "rule_ids": [],
        "note_ids": [],
        **more,
    }


def event(event_id: int, **more: Any) -> dict[str, Any]:
    return {
        "id": event_id,
        "node": None,
        "other_node": None,
        "rule_id": None,
        "note_id": None,
        "other_note_id": None,
        "action": "create",
        "actor": "ann",
        "actor_kind": "agent",
        "at": WHEN,
        "before": None,
        "after": {},
        **more,
    }


def a_file(**sections: list[dict[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {"format": "vrum-export", "version": 6}
    for name in ("nodes", "links", "rules", "notes", "definition_of_done", "checks", "events"):
        document[name] = sections.get(name, [])
    return document


def a_version_5_file(**sections: list[dict[str, Any]]) -> dict[str, Any]:
    """A file as the servers wrote it before a node could have a size and a check could be
    ticked: its nodes have no `size` and it has no `checks`. Nodes are given in the current form."""
    document = a_file(**sections)
    del document["checks"]
    document["version"] = 5
    document["nodes"] = _without_size(document["nodes"])
    return document


def a_version_4_file(**sections: list[dict[str, Any]]) -> dict[str, Any]:
    """A file as the servers wrote it before a rule could be on a task: its rules are on a
    `story` and it has no definition of done. Rules are given in the current form."""
    document = a_version_5_file(**sections)
    del document["definition_of_done"]
    document["version"] = 4
    document["rules"] = _on_a_story(document["rules"])
    return document


def a_version_3_file(**sections: list[dict[str, Any]]) -> dict[str, Any]:
    """A file as the servers wrote it before a note had one status. Its notes are `active` (or
    ended) with the confirmation kept apart, and its rules are `held` or `settled`: give each one
    that status, since the builders above make the current words."""
    return {**a_version_4_file(**sections), "version": 3}


# -- what the servers before notes wrote -------------------------------------------------------


def fact(fact_id: int, **more: Any) -> dict[str, Any]:
    return {
        "id": fact_id,
        "text": f"fact {fact_id}",
        "source": "a chat",
        "recorded_by": "ann",
        "recorded_by_kind": "agent",
        "recorded_at": WHEN,
        "status": "active",
        "superseded_by_id": None,
        "status_reason": None,
        "confirmed_by": None,
        "confirmed_at": None,
        "nodes": [],
        "rule_ids": [],
        "decision_ids": [],
        **more,
    }


def decision(decision_id: int, **more: Any) -> dict[str, Any]:
    return {
        "id": decision_id,
        "title": "A decision",
        "text": "What was decided",
        "reason": "Because",
        "decided_by": "Marie",
        "decided_on": "2026-11-15",
        "project_wide": False,
        "status": "active",
        "superseded_by_id": None,
        "status_reason": None,
        "confirmed_by": None,
        "confirmed_at": None,
        "recorded_at": WHEN,
        "nodes": [],
        "rule_ids": [],
        **more,
    }


def legacy_event(event_id: int, **more: Any) -> dict[str, Any]:
    return {
        "id": event_id,
        "node": None,
        "other_node": None,
        "rule_id": None,
        "decision_id": None,
        "fact_id": None,
        "action": "create",
        "actor": "ann",
        "actor_kind": "agent",
        "at": WHEN,
        "before": None,
        "after": {},
        **more,
    }


def a_legacy_file(**sections: list[dict[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {"format": "vrum-export", "version": 2}
    for name in ("nodes", "links", "rules", "decisions", "facts", "events"):
        document[name] = sections.get(name, [])
    document["nodes"] = _without_size(document["nodes"])
    document["rules"] = _on_a_story(document["rules"])
    return document


def as_version_1(document: dict[str, Any]) -> dict[str, Any]:
    """The same file as an older server wrote it: before names were verified, a fact and an event
    did not say as what (a person or an agent) they were made."""
    older = {**document, "version": 1}
    older["facts"] = [
        {name: value for name, value in item.items() if name != "recorded_by_kind"}
        for item in document["facts"]
    ]
    older["events"] = [
        {name: value for name, value in item.items() if name != "actor_kind"}
        for item in document["events"]
    ]
    return older
