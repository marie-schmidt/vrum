"""Small builders for export files (the data export_graph gives and import_graph takes), for the
tests that need no database. `a_file` builds a file of the current version (6: a node has a `size`,
and the file holds the ticks of the definition of done's checks)."""

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
    """The definition of done as an export file holds it."""
    return {
        "body": "- Tests pass",
        "version": version,
        "updated_at": WHEN,
        "updated_by": "Marie",
        "updated_by_kind": "person",
        **more,
    }


def check(node: str, item: str, **more: Any) -> dict[str, Any]:
    """A tick of one check of the definition of done, as an export file holds it."""
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
