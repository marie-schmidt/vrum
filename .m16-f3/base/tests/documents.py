"""Small builders for export files (the data export_graph gives and import_graph takes), for the
tests that need no database."""

from typing import Any

WHEN = "2026-11-15T10:00:00.000000Z"


def node(node_id: int, kind: str, parent: int | None, **more: Any) -> dict[str, Any]:
    return {
        "id": node_id,
        "kind": kind,
        "parent_id": parent,
        "title": f"{kind} {node_id}",
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


def link(link_id: int, source: int, target: int, link_type: str) -> dict[str, Any]:
    return {
        "id": link_id,
        "source_id": source,
        "target_id": target,
        "link_type": link_type,
        "created_at": WHEN,
    }


def rule(rule_id: int, story: int | None, **more: Any) -> dict[str, Any]:
    return {
        "id": rule_id,
        "story_id": story,
        "title": f"rule {rule_id}",
        "body": "",
        "status": "held",
        "version": 1,
        "created_at": WHEN,
        "updated_at": WHEN,
        **more,
    }


def memory(memory_id: int, kind: str, **more: Any) -> dict[str, Any]:
    fact = kind == "fact"
    return {
        "id": memory_id,
        "type": kind,
        "kind": "world" if fact else None,
        "text": f"{kind} {memory_id}",
        "source": "a chat" if fact else None,
        "happened_on": None,
        "origin": "stated",
        "recorded_by": "ann",
        "recorded_at": WHEN,
        "status": "active",
        "superseded_by_id": None,
        "status_reason": None,
        "confirmed_by": None,
        "confirmed_at": None,
        "node_ids": [],
        "rule_ids": [],
        "decision_ids": [],
        "evidence": [],
        **more,
    }


def decision(decision_id: int, **more: Any) -> dict[str, Any]:
    return {
        "id": decision_id,
        "kind": "decision",
        "title": "A decision",
        "text": "What was decided",
        "reason": "Because",
        "decided_by": "Marie",
        "decided_on": "2026-11-15",
        "status": "active",
        "superseded_by_id": None,
        "promoted_from_id": None,
        "recorded_at": WHEN,
        "node_ids": [],
        "rule_ids": [],
        **more,
    }


def event(event_id: int, **more: Any) -> dict[str, Any]:
    return {
        "id": event_id,
        "node_id": None,
        "other_node_id": None,
        "rule_id": None,
        "decision_id": None,
        "memory_id": None,
        "action": "create",
        "actor": "ann",
        "at": WHEN,
        "before": None,
        "after": {},
        **more,
    }


def a_file(**sections: list[dict[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {"format": "vrum-export", "version": 1}
    for name in ("nodes", "links", "rules", "decisions", "memories", "events"):
        document[name] = sections.get(name, [])
    return document
