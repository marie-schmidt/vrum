"""Small builders for export files (the data export_graph gives and import_graph takes), for the
tests that need no database."""

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


def rule(rule_id: int, story: str | None, **more: Any) -> dict[str, Any]:
    return {
        "id": rule_id,
        "story": story,
        "title": f"rule {rule_id}",
        "body": "",
        "status": "held",
        "version": 1,
        "created_at": WHEN,
        "updated_at": WHEN,
        **more,
    }


def fact(fact_id: int, **more: Any) -> dict[str, Any]:
    return {
        "id": fact_id,
        "text": f"fact {fact_id}",
        "source": "a chat",
        "recorded_by": "ann",
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


def event(event_id: int, **more: Any) -> dict[str, Any]:
    return {
        "id": event_id,
        "node": None,
        "other_node": None,
        "rule_id": None,
        "decision_id": None,
        "fact_id": None,
        "action": "create",
        "actor": "ann",
        "at": WHEN,
        "before": None,
        "after": {},
        **more,
    }


def a_file(**sections: list[dict[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {"format": "vrum-export", "version": 1}
    for name in ("nodes", "links", "rules", "decisions", "facts", "events"):
        document[name] = sections.get(name, [])
    return document
