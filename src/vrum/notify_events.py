"""What a webhook or an email nudge can be told about (M48): a closed list of kinds of news, each
worked out from the events of the append-only log (or, for a lapsed claim, from the claims), and
what a message about it may say.

A message says what kind of news it is, which project, the number of the event that made it and
the reference of the story or task it is about. It never says what anything is called or what
anyone wrote: a receiver that wants more reads it from Vrum, with a key. The change feed stays the
source of truth; this is only a nudge to wake someone up."""

from typing import Any

# What can be asked for, and what each means.
EVENT_TYPES: dict[str, str] = {
    "ask.raised": "An agent put a question or an approval to a person.",
    "ask.answered": "A person answered a question or an approval.",
    "plan.proposed": "A drafted plan, or a new revision of one, is waiting for a person.",
    "plan.decided": "A person approved or rejected a drafted plan, or asked for changes to it.",
    "placement.proposed": "A proposal to move work is waiting for a person.",
    "placement.decided": "A person accepted or rejected a proposal to move work.",
    "work.blocked": "A story or task was blocked.",
    "work.done": "A story or task was finished.",
    "work.available": "Work that may be ready to take changed (at most one message every ten "
    "seconds): read ready_work.",
    "claim.lapsed": "A claim ran out with nobody renewing or releasing it.",
}
# What a person can ask to be mailed about: what needs a person, and work that stopped.
NUDGE_TYPES = (
    "ask.raised",
    "plan.proposed",
    "placement.proposed",
    "work.blocked",
    "claim.lapsed",
)
# Kinds that are coalesced: at most one message each this many seconds for a webhook.
COALESCED = {"work.available": 10}
MOST_WEBHOOKS = 10


def _status(before: dict[str, Any] | None, after: dict[str, Any]) -> str | None:
    """The status an update moved work to, when it changed one."""
    if "status" not in after:
        return None
    if before is not None and before.get("status") == after.get("status"):
        return None
    return str(after["status"])


def types_of(action: str, before: dict[str, Any] | None, after: dict[str, Any]) -> list[str]:
    """The kinds of news one event is, in the order they are sent: none for most events."""
    found: list[str] = []
    if action == "ask_raised":
        found.append("ask.raised")
    elif action == "ask_answered":
        found.extend(["ask.answered", "work.available"])
    elif action in ("plan_proposed", "plan_revised"):
        found.append("plan.proposed")
    elif action == "plan_changes_requested":
        found.append("plan.decided")
    elif action in ("plan_approved", "plan_rejected"):
        found.extend(["plan.decided", "work.available"])
    elif action == "placement_proposed":
        found.append("placement.proposed")
    elif action in ("placement_accepted", "placement_rejected"):
        found.append("placement.decided")
    elif action == "update":
        status = _status(before, after)
        if status == "blocked":
            found.append("work.blocked")
        elif status == "done":
            found.extend(["work.done", "work.available"])
        elif status in ("dropped", "new"):
            found.append("work.available")
    elif action == "handoff":
        # A handoff that changes the status comes with an update, which is the news. One that
        # leaves the work as it was (released) is only work that is free to take again.
        if after.get("outcome") == "released":
            found.append("work.available")
    elif action == "create":
        if after.get("kind") in ("story", "task"):
            found.append("work.available")
    elif action in ("link_removed", "gate_removed", "claim_released"):
        found.append("work.available")
    return found


def object_of(action: str, event_id: int, after: dict[str, Any]) -> str | None:
    """What an ask, a plan or a proposal is called, by its number: ask:12, plan:3, placement:7."""
    if action == "ask_raised":
        return f"ask:{event_id}"
    if action == "ask_answered":
        return f"ask:{after.get('ask')}"
    if action in ("plan_proposed",):
        return f"plan:{event_id}"
    if action in ("plan_revised", "plan_changes_requested", "plan_approved", "plan_rejected"):
        return f"plan:{after.get('plan')}"
    if action == "placement_proposed":
        return f"placement:{event_id}"
    if action in ("placement_accepted", "placement_rejected"):
        return f"placement:{after.get('placement')}"
    return None
