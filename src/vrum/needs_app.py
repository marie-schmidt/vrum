"""The Needs a person view for MCP hosts that draw interactive views (M46, MCP Apps).

An MCP host such as Claude can draw a small HTML view beside the chat when a tool names one. Vrum
names one for `needs_a_person`: the list of what waits for a person (needs.py), drawn as rows by
story. It is the same list, from the same call, that the board's Needs a person page shows; a host
that does not draw views gets the same answer as plain words (`summary`).

The view only reads. A person's answer (an ask, a plan, a check, a note) is the person's, recorded
under their own name, and an agent's key, which is what the host's chat holds, cannot make one
(`answer_ask` and the others are people-only); so each row has a button that opens the board at
that page, where the person answers. Nothing is stored in the view and nothing is loaded from
outside it: the page (`needs_view.html`) holds no address of any server and loads no script, style,
image or font, it speaks only to its host, as the MCP Apps protocol has it (JSON-RPC over
postMessage: `ui/initialize`, `ui/notifications/tool-result`, `ui/open-link`, `tools/call`), and a
text is only ever put on the page as text. The board's address comes in the tool's result, from
`VRUM_PUBLIC_URL`, never from a request.

It is offered to a key that is allowed `needs_a_person` (the standard set of tools is full, see
surface.py); a client that does not speak MCP Apps never sees it."""

from datetime import datetime
from importlib.resources import files
from typing import Any

from mcp.server.apps import Apps

from vrum.needs import NeedsAPerson, NeedsItem

NEEDS_VIEW_URI = "ui://vrum/needs-a-person.html"


def _view_html() -> str:
    return (files("vrum") / "needs_view.html").read_text(encoding="utf-8")


def _code(ref: str) -> str:
    kind, _, number = ref.partition(":")
    return f"{kind[:1].upper()}-{number}" if kind in ("epic", "story", "task") else ref


def _waited(since: datetime, now: datetime) -> str:
    seconds = max(0, int((now - since).total_seconds()))
    if seconds < 90:
        return "just now"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} minutes"
    if seconds < 36 * 3600:
        return f"{round(seconds / 3600)} hours"
    return f"{round(seconds / 86400)} days"


def _line(item: NeedsItem, now: datetime) -> str:
    target = item.title if item.kind == "plan" else f"{_code(item.node)} {item.title}"
    who = f"{item.waiting} is waiting" if item.waiting else "Waiting"
    ask = ""
    if item.ask is not None:
        choices = f", choices: {', '.join(item.ask.choices)}" if item.ask.choices else ""
        ask = f" (Q-{item.ask.id}{choices})"
    return f"- {item.reason}{ask} [{target}; {who} for {_waited(item.waiting_since, now)}]"


def summary(needs: NeedsAPerson) -> str:
    """The list as plain words, for a host that does not draw the view and for the model, which
    reads this and not the structured rows. It says what each row says and where to answer."""
    if needs.total == 0:
        return "Nothing is waiting for a person."
    noun = "thing is" if needs.total == 1 else "things are"
    lines = [f"{needs.total} {noun} waiting for a person."]
    for group in needs.groups:
        lines.append("")
        lines.append(f"{_code(group.story)} {group.title}:" if group.story else f"{group.title}:")
        lines.extend(_line(item, needs.as_of) for item in group.items)
    if needs.left_out:
        lines.extend(["", f"{needs.left_out} more are not shown."])
    where = f" at {needs.board_address}#/needs" if needs.board_address else ""
    lines.extend(["", f"A person answers on the board{where}, under their own name."])
    return "\n".join(lines)


def needs_apps() -> Apps:
    """The MCP Apps extension with the one view. The tool that shows it is bound by the server
    (server.py), which owns the tool."""
    apps = Apps()
    apps.add_html_resource(
        NEEDS_VIEW_URI,
        _view_html(),
        name="needs-a-person",
        title="Needs a person",
        description="What is waiting for a person, by story. It only reads; a person answers "
        "on the board.",
        prefers_border=True,
    )
    return apps


def view_meta() -> dict[str, Any]:
    """The `_meta` the tool carries: the view that draws its result, for the model and the app."""
    return {"ui": {"resourceUri": NEEDS_VIEW_URI, "visibility": ["model", "app"]}}
