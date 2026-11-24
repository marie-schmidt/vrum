"""Fills a NEW Vrum with the sample project of ./demo.sh (Riverside Bike Share, described in
tests/demo_data.py). It is a developer tool, not part of the product and not a way to seed an
install:

    uv run python -m tests.demo_world http://127.0.0.1:8091/mcp

It registers the fictional account demo@example.com with a random password through the board's own
HTTP API, makes a project, and fills it through real operations: a team plans the work (with a size
on most of it), people and agents move it along, take claims and leave handoff notes, record notes
(some with the person who decided them) and confirm some of them, and the owner sets the
definition of done, whose checks are then ticked on the finished work, by the agents with how they
checked and by a person for the one only a person can judge. Two of its lines are tagged for a
verifier ([verifier] and [github]): the key of review-agent is marked a verifier, it ticks them on
finished work (the [github] one with the made-up pull request in the form scripts/verify_pr.py
writes), and four tasks are blocked, held by nobody and handed over saying they are ready for a
verifier, which is:unverified finds. Agents also say where their work is (M41): the claims that
are held now and the one that ran out carry a branch, a commit, sometimes a pull request and a run
label with the client's name of the made-up repository example-org/bike-share (two instances of
one key hold work under two run labels, and one claim says nothing at all), and the handoff notes
of code work say a branch and a pull request, the very one the verifier's [github] tick names on
work that is done. The key of docs-agent has a last day, 90 days after the day the demo is made.
Agents also put asks to a person (M45): six of them, through finish_work and update_node (two
questions with choices that block work, an approval that parks a task, and three that the owner and
the planner answered through the board, so that the questions and answers of an item and its
history have something to show), and the page of what needs a person lists the other things only
a person can settle: a task finished but for the check marked [person], unconfirmed notes on work in
progress, and a task whose claim ran out twice, about eleven rows under six stories.
Then the later part of the demo (M42 to M47, tests/demo_flow.py) is made through the same tools
and the real embedding service: an agent files six loose stories in an epic called Inbox from calls
and notes, a steward (scripts/steward.py, with a key allowed four tools) proposes where the ones the
model is sure about belong and groups two under a new epic, the planner accepts one proposal (the
story moves, as she moves it) and the owner rejects another with a reason, four gates hold work (a
timer, two approvals that wait or were refused, and one that was approved), the planning agent
saves two plan templates and drafts a winter plan that waits for approval while a plan made from a
template was approved by the planner earlier, and four tasks say where they were found with the
link discovered_from. Two drafted plans have a conversation (M51): the owner asked for changes to
the winter plan with a summary and a comment on a story's title and on a task's details, and its
agent sent revision 2, which a person reads next; and the owner asked for changes to a small spring
plan, which waits for its agent to revise it and is not a row on the page of what needs a person.
The page of what needs a person then lists about seventeen rows.
Two more fictional people join the
project the way people do, through invitations
the owner makes (priya@example.com, an editor, and tom@example.com, a viewer), and have the
same password. When the server can send email Priya's invitation is emailed to her, which the
stand-in mail server shows and which makes her account a confirmed one; Tom's is not, so that his
account is never confirmed (the demo's stand-in sign-in providers show what each kind of account
does when its address signs in). Each agent has an agent key of its own, named for it, so that the
history says which agent did what; what the planner does is done through the board's own calls as
her, what the other
people of the team do as the owner, and every confirmation is a person's, since only a person can
confirm. The viewer is shown to be able to look and not to change. Nothing but real operations is
used, no SQL and no code in the server. When the project is filled the agents' keys are revoked,
except three that stay in force with different allowances (api-agent may use every tool,
review-agent the tools that read and the two a verifier needs, to tick a check and to set work
done, docs-agent also the tools that change work, links, claims,
acceptance criteria and notes), so that the Users page shows what a key may do. Nobody holds
their secrets, and a check shows that a limited key is refused what it may not do.

It refuses a server that is not on this machine or that has ever held another account (the account
it makes must be number 1), so it cannot be pointed at a real install by mistake. The password is
printed on standard output and nothing else is; everything else is said on standard error. Nothing
is written anywhere.
"""

import asyncio
import datetime
import hashlib
import re
import secrets
import sys
import zlib
from collections.abc import Iterable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import TextContent

from tests import demo_data as data
from tests.demo_flow import Flow, Stopped, later_counts
from tests.world import Person, api

# The longest a claim may be held (24 hours), so that the claims of the demo are still held when
# someone looks at it. One that must have run out is taken for one second.
LONG_LEASE = 86_400
HANDOFF_LEASE = 3_600
# What a claim says about where the work is and which run it is (the pull request comes with a
# renewal or the end of the work).
CLAIM_FIELDS = ("repository", "branch", "commit", "run_label", "client_name")

# The stored statuses an item goes through on its way to the status it is described with.
STATUS_PATH = {
    "in_progress": ("in_progress",),
    "done": ("in_progress", "done"),
    "blocked": ("in_progress", "blocked"),
    "dropped": ("dropped",),
}

_REFERENCE = re.compile(r"\[\[([a-z0-9][a-z0-9-]*)\]\]")


@dataclass(frozen=True)
class Placed:
    """An epic, story or task as it will be numbered and named in the plan."""

    item: data.Item
    ref: str
    key: str
    parent: str | None
    epic: str


def place(epics: Iterable[data.Item]) -> list[Placed]:
    """Every item in the order the plan lists it (an epic, its stories, the tasks of each), with
    the reference it will get: each kind is numbered from 1 in that order."""
    counts = {"epic": 0, "story": 0, "task": 0}
    placed: list[Placed] = []

    def walk(item: data.Item, parent: str | None, epic: str | None) -> None:
        counts[item.kind] += 1
        key = f"{item.kind[0]}{counts[item.kind]}"
        epic = epic or item.key or key
        placed.append(Placed(item, f"{item.kind}:{counts[item.kind]}", key, parent, epic))
        for child in item.children:
            walk(child, key, epic)

    for root in epics:
        walk(root, None, None)
    return placed


@dataclass(frozen=True)
class Names:
    """What every key of the description stands for: the reference of an item or the number of an
    acceptance criterion or a note, which are all known before anything is made because each kind
    is numbered from 1 in the order it is made."""

    nodes: dict[str, Placed]
    rules: dict[str, int]
    notes: dict[str, int]

    @staticmethod
    def of_description() -> "Names":
        nodes: dict[str, Placed] = {}
        for placed in place(data.EPICS):
            if placed.item.key is not None:
                if placed.item.key in nodes:
                    raise Stopped(f"The key {placed.item.key} names two items.")
                nodes[placed.item.key] = placed
        rules = {rule.key: number for number, rule in enumerate(data.RULES, start=1)}
        notes = {note.key: number for number, note in enumerate(data.NOTES, start=1)}
        names = Names(nodes, rules, notes)
        everything = [*nodes, *rules, *notes]
        if len(set(everything)) != len(everything):
            raise Stopped("A key of the description names more than one thing.")
        return names

    def reference(self, key: str) -> str:
        """The reference a text uses for this key, such as story:12 or note:7."""
        if key in self.nodes:
            return self.nodes[key].ref
        for kind, numbers in (("rule", self.rules), ("note", self.notes)):
            if key in numbers:
                return f"{kind}:{numbers[key]}"
        raise Stopped(f"The description names {key}, which is not one of its items.")

    def node(self, key: str) -> Placed:
        if key not in self.nodes:
            raise Stopped(
                f"The description names {key}, which is not one of its epics, stories or tasks."
            )
        return self.nodes[key]

    def text(self, text: str) -> str:
        """A text with every [[key]] written as the reference it stands for."""
        return _REFERENCE.sub(lambda found: f"[[{self.reference(found.group(1))}]]", text)


def day(today: datetime.date, offset: int | None) -> str | None:
    return None if offset is None else (today + datetime.timedelta(days=offset)).isoformat()


def derived_criteria(placed_items: Iterable[Placed]) -> list[dict[str, Any]]:
    """The acceptance criteria that stories and tasks carry in the description (their `accepts`),
    each as create_plan takes it, on the item by its plan key. One is met when its story or task
    is done, and open otherwise."""
    return [
        {
            "title": title,
            "body": "",
            "status": "met" if placed.item.status == "done" else "open",
            "node": placed.key,
        }
        for placed in placed_items
        for title in placed.item.accepts
    ]


def plan_arguments(names: Names, today: datetime.date) -> dict[str, Any]:
    """The arguments of the one create_plan call that makes every epic, story, task, link and
    acceptance criterion."""
    items = [
        {
            "key": placed.key,
            "kind": placed.item.kind,
            "title": placed.item.title,
            "body": names.text(placed.item.body),
            "priority": placed.item.priority,
            "size": placed.item.size,
            "start_on": day(today, placed.item.start),
            "target_on": day(today, placed.item.due),
            "parent": placed.parent,
        }
        for placed in place(data.EPICS)
    ]
    links = [
        {
            "source": names.node(link.source).key,
            "target": names.node(link.target).key,
            "link_type": link.kind,
        }
        for link in data.LINKS
    ]
    # The criteria that are named come first, so that each keeps the number its key stands for.
    rules = [
        {
            "title": rule.title,
            "body": names.text(rule.body),
            "status": rule.status,
            "node": None if rule.node is None else names.node(rule.node).key,
        }
        for rule in data.RULES
    ] + derived_criteria(place(data.EPICS))
    return {"items": items, "links": links, "rules": rules}


def expected_counts() -> dict[str, int]:
    """How much of each kind the plan holds, as server_status counts them once it is made (the
    later part of the demo, tests/demo_flow.py, adds to it: see expected_totals)."""
    placed = place(data.EPICS)
    return {
        "epics": sum(p.item.kind == "epic" for p in placed),
        "stories": sum(p.item.kind == "story" for p in placed),
        "tasks": sum(p.item.kind == "task" for p in placed),
        "links": len(data.LINKS),
        "rules": len(data.RULES) + len(derived_criteria(placed)),
        "notes": len(data.NOTES),
    }


def expected_totals() -> dict[str, int]:
    """How much of each kind the whole demo holds when it is loaded: the plan and everything the
    later part adds (an Inbox with its loose stories, a drafted plan and the plan made from a
    template)."""
    added = later_counts()
    totals = {kind: count + added.get(kind, 0) for kind, count in expected_counts().items()}
    totals["notes"] += len(kept_of_description())
    return totals


def expected_note_counts() -> dict[str, int]:
    """How many notes the description ends with in each state, as list_notes counts them: all,
    ones somebody decided, project-wide ones, confirmed ones (what the last step on each note left
    it as, if it was still in force), unconfirmed ones, the two together (in force), superseded
    ones and withdrawn ones."""
    confirmed: dict[str, bool] = {}
    for step in data.CONFIRMATIONS:
        confirmed[step.key] = step.confirmed
    replaced = {key for note in data.NOTES for key in note.replaces}
    ended = replaced | {step.key for step in data.WITHDRAWALS}
    confirmed_in_force = sum(1 for key, yes in confirmed.items() if yes and key not in ended)
    return {
        "all": len(data.NOTES),
        "decided": sum(note.by is not None for note in data.NOTES),
        "project_wide": sum(note.project_wide for note in data.NOTES),
        "confirmed": confirmed_in_force,
        "unconfirmed": len(data.NOTES) - len(ended) - confirmed_in_force,
        "in_force": len(data.NOTES) - len(ended),
        "superseded": len(replaced),
        "withdrawn": len(data.WITHDRAWALS),
    }


def unconfirmed_note_keys() -> set[str]:
    """The notes that end in force and not confirmed, as expected_note_counts counts them."""
    confirmed: dict[str, bool] = {}
    for step in data.CONFIRMATIONS:
        confirmed[step.key] = step.confirmed
    ended = {key for note in data.NOTES for key in note.replaces}
    ended |= {step.key for step in data.WITHDRAWALS}
    return {
        note.key for note in data.NOTES if note.key not in ended and not confirmed.get(note.key)
    }


def asks_of_description() -> list[tuple[str, data.Ask]]:
    """Every ask the description puts, with the key of the story or task it is about, in the order
    they are made (the ones put as work is moved along, then the ones put as it is handed over)."""
    return [
        *((asked.key, asked.ask) for asked in data.ASKED),
        *((step.key, step.ask) for step in data.HANDOFFS if step.ask is not None),
    ]


def kept_of_description() -> list[tuple[str, data.Ask]]:
    """The asks whose answer a person kept as a decision (M52), with the key of their work. They
    are put when work is handed over, so that the notes of the description keep their numbers."""
    handed = {step.key for step in data.HANDOFFS if step.ask is not None}
    return [(key, ask) for key, ask in asks_of_description() if ask.kept and key in handed]


def ask_arguments(ask: data.Ask) -> dict[str, Any]:
    """An ask as finish_work and update_node take it."""
    arguments: dict[str, Any] = {"question": ask.question}
    if ask.choices:
        arguments["choices"] = list(ask.choices)
    if ask.approval:
        arguments["approval"] = True
    return arguments


def expected_needs() -> list[tuple[str, str, str]]:
    """What the page of what needs a person should list for the description, as the server works it
    out: (kind, the story or task, the story it is grouped under). An open ask on work that is
    not finished; blocked work with only a person's check open; an unconfirmed note on a story or
    task that is in progress or held; and a claim that ran out twice."""
    names = Names.of_description()
    placed = place(data.EPICS)
    by_key = {entry.key: entry for entry in placed}

    def story_of(key: str) -> str:
        entry = names.node(key)
        return entry.ref if entry.item.kind == "story" else by_key[entry.parent or ""].ref

    held = {step.key for step in data.HELD}
    rows: list[tuple[str, str, str]] = []
    for key, ask in asks_of_description():
        if ask.answer is None and names.node(key).item.status not in ("done", "dropped"):
            rows.append(("ask", names.node(key).ref, story_of(key)))
    for key in data.WAITING_FOR_A_PERSON:
        rows.append(("check", names.node(key).ref, story_of(key)))
    waiting_notes = unconfirmed_note_keys()
    by_note = {note.key: note for note in data.NOTES}

    def linked(note: data.Note) -> set[str]:
        """What a note concerns: its own links and, as the loader replaces with keep_links, those
        of every note it replaced."""
        return set(note.nodes).union(*(linked(by_note[key]) for key in note.replaces))

    noted: set[str] = set()
    for note in data.NOTES:
        if note.key not in waiting_notes:
            continue
        for key in linked(note):
            entry = names.node(key)
            if entry.item.kind in ("story", "task") and (
                entry.item.status == "in_progress" or key in held
            ):
                noted.add(key)
    rows.extend(("note", names.node(key).ref, story_of(key)) for key in sorted(noted))
    for step in data.LAPSED:
        if step.after:
            rows.append(("lapse", names.node(step.key).ref, story_of(step.key)))
    return rows


def expected_gate_rows() -> list[tuple[str, str, str]]:
    """The rows of what needs a person that the gates add, as the server works them out: (kind, the
    task, the story it is grouped under). An approval nobody has answered is an ask of its own; one
    a person refused is a row of its own (kind gate) until a person lifts it; a timer and an
    approval that was given add none. The rows for a drafted plan and for proposals to move work
    are not here: which item comes first in them depends on what the server says (see
    tests/demo_flow.py), and they are in a group with no story."""
    names = Names.of_description()
    by_key = {entry.key: entry for entry in place(data.EPICS)}
    rows: list[tuple[str, str, str]] = []
    for gate in data.GATES:
        if gate.kind != "approval" or gate.answer == "yes":
            continue
        entry = names.node(gate.key)
        story = by_key[entry.parent or ""].ref
        rows.append(("ask" if gate.answer is None else "gate", entry.ref, story))
    return rows


def agents_of_description() -> list[str]:
    """Every agent the description names (a name that ends with "-agent"), each of which gets a key
    of its own. Sorted, so the keys are made in the same order every time."""
    named = {data.RECORDER, data.VERIFIER, data.INTAKE, *data.WORKERS.values()}
    named |= {step.actor for step in data.HANDOFFS}
    named |= {held.actor for held in (*data.HELD, *data.LAPSED)}
    named |= {held.after for held in data.LAPSED if held.after}
    named |= {edit.actor for edit in data.EDITS}
    named |= {actor for _, _, actor in (*data.RULE_PLACES, *data.RULE_MOVES)}
    named |= {edit.actor for edit in data.RULE_EDITS}
    return sorted(name for name in named if name.endswith(data.AGENT_SUFFIX))


def recorder_of(note: data.Note) -> str:
    """Who records a note: the planner, as herself, when she decided it; the person signed in for
    the notes a person records themselves; otherwise the planning agent, who writes down what a
    named person decided and what was found."""
    if note.by == data.PLANNER:
        return data.PLANNER
    return data.OWNER if note.key in data.NOTES_BY_PERSON else data.RECORDER


def confirmer_of(step: data.Confirmation) -> str:
    """Who confirms: the planner, as herself, when she is the one who reviewed the record, and
    otherwise the owner, who signs in for the reviewers that have no account."""
    return data.PLANNER if step.person == data.PLANNER else data.OWNER


def said(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def check_words(index: int, placed: Placed) -> str:
    """How one check was done on one item, in one of the wordings of the description. Which wording
    and which numbers come from the item's title, so that a run always says the same."""
    spread = zlib.crc32(placed.item.title.encode())
    text = data.CHECK_EVIDENCE[index][(spread >> index) % 3]
    return text.format(
        title=placed.item.title,
        tests=180 + spread % 60,
        added=1 + spread % 4,
        reviewer=data.PLANNER,
    )


def pull_request_number(title: str) -> int:
    """The number of the made-up pull request of a piece of work, worked out from its title, so that
    the address the verifier ticks the [github] line with and the one the agent that did the work
    said its work is in are the same one."""
    return 100 + zlib.crc32(title.encode()) % 900


def commit_of(seed: str) -> str:
    """A made-up commit hash (40 hex digits) that is always the same for the same words."""
    return hashlib.sha256(seed.encode()).hexdigest()[:40]


def whereabouts(
    where: data.Where | None, actor: str, title: str, today: datetime.date
) -> dict[str, str | None]:
    """What an agent says about where its work is, as the arguments of the tools that take it
    (None for what it does not say): the fictional repository, the branch, a commit made up from
    the branch, the pull request when there is one and, when the run has a letter, the run label
    (the day the demo is made and the letter) and the client the actor works in."""
    if where is None:
        return {}
    number = pull_request_number(title) if where.merged else where.pull_request
    return {
        "repository": data.REPOSITORY_ADDRESS,
        "branch": where.branch,
        "commit": commit_of(where.branch),
        "pull_request": None if number is None else data.PULL_REQUEST_ADDRESS.format(number=number),
        "run_label": None if where.run is None else f"run-{today:%Y-%m-%d}-{where.run}",
        "client_name": data.CLIENTS.get(actor) if where.run is not None else None,
    }


def verified_words(github: bool, placed: Placed, today: datetime.date) -> str:
    """How the verifier ticked one of the lines tagged for it on one item: its own words for a line
    tagged [verifier], and for one tagged [github] the one fixed form that scripts/verify_pr.py
    writes, with a made-up pull request. Everything is worked out from the item's title, so that a
    run always says the same."""
    spread = zlib.crc32(placed.item.title.encode())
    if not github:
        return data.VERIFIED_EVIDENCE[spread % len(data.VERIFIED_EVIDENCE)].format(
            title=placed.item.title
        )
    merged = datetime.datetime.combine(
        today - datetime.timedelta(days=1 + spread % 45),
        datetime.time(9 + spread % 9, (spread >> 5) % 60),
        tzinfo=datetime.UTC,
    )
    return data.PULL_REQUEST_SEEN.format(
        address=data.PULL_REQUEST_ADDRESS.format(number=pull_request_number(placed.item.title)),
        merged=merged.strftime("%Y-%m-%dT%H:%M:%SZ"),
        commit=hashlib.sha256(placed.item.title.encode()).hexdigest()[:10],
        passed=2 + (spread >> 3) % 4,
    )


def allowances(tools: list[dict[str, Any]]) -> dict[str, list[str] | None]:
    """What each key that stays in force may do, worked out from the server's own tool list (the
    entries of GET /board/api/tools): None for the tools Vrum offers agents, or the tools it may
    use (only ones that are offered)."""
    offered = [tool for tool in tools if tool["offered_to_agents"]]
    reads = [tool["name"] for tool in offered if not tool["changes_something"]]
    verifying = [tool["name"] for tool in offered if tool["name"] in data.VERIFIER_TOOLS]
    recording = [
        tool["name"]
        for tool in offered
        if tool["changes_something"]
        and not tool["people_only"]
        and tool["area"] in data.DOCS_AGENT_AREAS
    ]
    chosen: dict[str, list[str] | None] = {
        "api-agent": None,
        "review-agent": sorted({*reads, *verifying}),
        "docs-agent": sorted(reads + recording),
    }
    assert set(chosen) == set(data.KEPT_KEYS)
    return chosen


async def limit_the_kept_keys(
    url: str, owner: Person, path: str, keys: dict[str, str], ids: dict[str, int]
) -> None:
    """Narrow the keys that stay in force, the way their owner does on the Users page, and show
    that each key's tool list and refusals are what was chosen: the key is used, then discarded."""
    listing = owner.call("GET", "/board/api/tools")
    if listing.status != 200:
        raise Stopped(f"The server's tool list was refused: {listing.body}")
    offered = {tool["name"] for tool in listing.body["tools"] if tool["offered_to_agents"]}
    allowed = allowances(listing.body["tools"])
    for name, tools in allowed.items():
        changed = owner.call("PATCH", f"{path}/{ids[name]}", {"tools": tools})
        if changed.status != 200 or changed.body["key"]["tools"] != tools:
            raise Stopped(f"What the key of {name} may do was not set: {changed.body}")
        if changed.body["key"]["verifier"] != (name == data.VERIFIER):
            raise Stopped(f"The key of {name} is not what it should be about verifying.")
    wanted = {name: offered if tools is None else set(tools) for name, tools in allowed.items()}
    # A tool a key may not use is refused in words, whatever the arguments are: one the key's own
    # list leaves out, and, for a standard key, one that is done on the board.
    outside: dict[str, tuple[str, dict[str, Any], str]] = {
        "review-agent": (
            "create_node",
            {"kind": "epic", "title": "Not allowed"},
            "not allowed to use",
        ),
        "docs-agent": ("import_graph", {"document": {}}, "not allowed to use"),
        "api-agent": ("node_history", {"node": "epic:1"}, "is not one of the tools Vrum offers"),
    }
    try:
        for name in data.KEPT_KEYS:
            async with (
                create_mcp_http_client(headers={"Authorization": f"Bearer {keys[name]}"}) as http,
                Client(streamable_http_client(url, http_client=http)) as client,
            ):
                listed = {tool.name for tool in (await client.list_tools()).tools}
                if listed != wanted[name]:
                    raise Stopped(f"The tool list of {name} is not what its key may use.")
                if name in outside:
                    tool, arguments, wording = outside[name]
                    tried = await client.call_tool(tool, arguments)
                    said_so = " ".join(
                        block.text for block in tried.content if isinstance(block, TextContent)
                    )
                    if not tried.is_error or wording not in said_so:
                        raise Stopped(f"The key of {name} was not refused {tool}.")
    except BaseExceptionGroup as group:
        problem: BaseException = group
        while isinstance(problem, BaseExceptionGroup):
            problem = problem.exceptions[0]
        raise problem from None


async def give_a_key_a_last_day(owner: Person, path: str, ids: dict[str, int]) -> str:
    """The owner gives one of the keys that stay in force a last day, a fixed number of days after
    today (UTC, as the server counts), as they do on the Users page, so that the page shows what a
    key that runs out looks like. Returns the day."""
    today = datetime.datetime.now(datetime.UTC).date()
    last_day = (today + datetime.timedelta(days=data.KEY_EXPIRES_AFTER_DAYS)).isoformat()
    changed = owner.call("PATCH", f"{path}/{ids[data.EXPIRING_KEY]}", {"expires_on": last_day})
    if (
        changed.status != 200
        or changed.body["key"]["expires_on"] != last_day
        or changed.body["key"]["expired"]
    ):
        raise Stopped(f"The last day of the key of {data.EXPIRING_KEY} was not set: {changed.body}")
    return last_day


class Demo:
    """One run: a client for each agent, the people who have an account (each signed in on the
    board, by name), and the numbers everything has."""

    def __init__(
        self,
        clients: dict[str, Client],
        people: dict[str, Person],
        project_id: int,
        today: datetime.date,
        url: str = "",
        keys: dict[str, str] | None = None,
    ) -> None:
        self.clients = clients
        self.people = people
        self.project_id = project_id
        self.today = today
        self.url = url
        self.keys = keys or {}
        self.names = Names.of_description()

    async def call(self, tool: str, actor: str | None = None, **arguments: Any) -> dict[str, Any]:
        """Call a tool as `actor`: an agent calls it with its own key, a person who has an account
        calls it through the board as themselves, and any other name is a person of the team with
        no account here, whose calls are made through the board as the owner. With no actor it is a
        read, made with the planning agent's key."""
        sent = {name: value for name, value in arguments.items() if value is not None}
        client = self.clients.get(actor or data.RECORDER)
        if client is None:
            return await self.as_person(tool, sent, actor)
        result = await client.call_tool(tool, sent)
        if result.is_error or result.structured_content is None:
            text = " ".join(getattr(block, "text", "") for block in result.content)
            raise Stopped(f"{tool} was refused: {text}")
        return dict(result.structured_content)

    async def as_person(
        self, tool: str, arguments: dict[str, Any], name: str | None
    ) -> dict[str, Any]:
        """The board's own route for a tool: what the page makes when a person presses a button."""
        who = self.people.get(name or "", self.people[data.OWNER])
        reply = await asyncio.to_thread(
            who.call,
            "POST",
            f"/board/api/projects/{self.project_id}/tool/{tool}",
            arguments,
        )
        if reply.status != 200 or not isinstance(reply.body, dict):
            message = reply.body.get("message") if isinstance(reply.body, dict) else reply.body
            raise Stopped(f"{tool} was refused: {message}")
        return reply.body

    def ref(self, key: str) -> str:
        return self.names.node(key).ref

    def refs(self, keys: Iterable[str]) -> list[str] | None:
        return [self.ref(key) for key in keys] or None

    def rule_ids(self, keys: Iterable[str]) -> list[int] | None:
        return [self.names.rules[key] for key in keys] or None

    def note_ids(self, keys: Iterable[str]) -> list[int] | None:
        return [self.names.notes[key] for key in keys] or None

    def worker(self, key: str) -> str:
        return data.WORKERS[self.names.node(key).epic]

    # -- the plan and where each piece of work stands --------------------------------------------

    async def make_plan(self) -> None:
        made = await self.call(
            "create_plan", data.RECORDER, **plan_arguments(self.names, self.today)
        )
        predicted = [placed.ref for placed in place(data.EPICS)]
        if [item["ref"] for item in made["created"]] != predicted:
            raise Stopped("The plan was numbered differently from what the description expects.")
        if made["rule_ids"] != list(range(1, expected_counts()["rules"] + 1)):
            raise Stopped(
                "The acceptance criteria were numbered differently from what is expected."
            )

    async def change(self, actor: str, ref: str, **fields: Any) -> dict[str, Any]:
        """update_node with the version that is current now, as the board does."""
        current = await self.call("get_node", node=ref)
        return await self.call(
            "update_node", actor=actor, node=ref, expected_version=current["version"], **fields
        )

    async def move_statuses(self) -> None:
        """Every item that is not new gets to where it stands, one step at a time, by whoever holds
        it or else by whoever works on its epic. The ones that end with a handoff note get there
        through the note. Work that is marked blocked with an ask is blocked with it: an answered
        ask frees the work, which then goes on to the status it is described with."""
        noted = {handoff.key for handoff in data.HANDOFFS}
        holders = {held.key: held.actor for held in (*data.HELD, *data.LAPSED)}
        asked = {entry.key: entry.ask for entry in data.ASKED}
        for placed in place(data.EPICS):
            item = placed.item
            key = item.key or ""
            if item.status == "new" or key in noted:
                continue
            ask = asked.get(key)
            actor = holders.get(key) or data.WORKERS[placed.epic]
            if ask is not None and ask.answer is not None:
                await self.change(actor, placed.ref, status="blocked", ask=ask_arguments(ask))
                await self.answer(placed.ref, ask)
            for status in STATUS_PATH[item.status]:
                fields: dict[str, Any] = {"status": status}
                if status == "blocked" and ask is not None and ask.answer is None:
                    fields["ask"] = ask_arguments(ask)
                await self.change(actor, placed.ref, **fields)

    async def answer(self, ref: str, ask: data.Ask) -> None:
        """A person answers the ask that waits on this story or task, through the board as
        themselves: the one who has an account here that the description names."""
        waiting = await self.call("list_asks", node=ref, status="open")
        number = next(
            (found["id"] for found in waiting["items"] if found["question"] == ask.question), None
        )
        if number is None or ask.by is None or ask.answer is None:
            raise Stopped(f"{ref} has no open ask that can be answered as described.")
        words: dict[str, Any] = {"ask": number, "answer": ask.answer}
        if ask.comment:
            words["comment"] = ask.comment
        given = await self.as_person("answer_ask", words, ask.by)
        if (given["answered_by"], given["answer"]) != (ask.by, ask.answer):
            raise Stopped(f"The answer to Q-{number} was not recorded as {ask.by} gave it.")

    # -- acceptance criteria and the definition of done ------------------------------------------

    async def rules(self) -> None:
        for key, story, actor in data.RULE_PLACES:
            await self.put_on("place_rule", key, story, actor)
        for key, story, actor in data.RULE_MOVES:
            await self.put_on("place_rule", key, story, actor)
        for edit in data.RULE_EDITS:
            number = self.names.rules[edit.key]
            current = await self.call("get_rule", rule_id=number)
            await self.call(
                "update_rule",
                actor=edit.actor,
                rule_id=number,
                expected_version=current["version"],
                body=None if edit.body is None else self.names.text(edit.body),
                status=edit.status,
            )

    async def put_on(self, tool: str, key: str, node: str, actor: str) -> None:
        number = self.names.rules[key]
        current = await self.call("get_rule", rule_id=number)
        await self.call(
            tool,
            actor=actor,
            rule_id=number,
            expected_version=current["version"],
            node=self.ref(node),
        )

    async def definition_of_done(self) -> None:
        """Only a person can set it, so the owner does, through the board's own call, and the
        server is asked for it again to see that it says what was written."""
        current = await self.call("get_definition_of_done")
        await self.call(
            "set_definition_of_done",
            data.OWNER,
            body=data.DEFINITION_OF_DONE,
            expected_version=current["version"],
        )
        stored = await self.call("get_definition_of_done")
        if stored["body"] != data.DEFINITION_OF_DONE or stored["updated_by_kind"] != "person":
            raise Stopped("The definition of done is not what was written.")
        flags = [(i["verifier_only"], i["github"], i["person_only"]) for i in stored["items"]]
        ordinary = [(False, False, False)] * len(data.CHECK_EVIDENCE)
        tagged = [(True, False, False), (True, True, False), (False, False, True)]
        if flags != ordinary + tagged:
            raise Stopped("The definition of done does not have the checks that are described.")

    async def ticks(self) -> None:
        """Whoever finished work ticks the checks of the definition of done and says how they were
        done: all of them on what is done, the first few on what is under way, the first on what is
        blocked and none on the rest, except the blocked work that waits for a verifier, whose own
        checks are all ticked, and the one that waits for a person, whose checks are all ticked but
        the one that is the person's. The checks tagged [verifier] and [github] are ticked by the
        verifier
        on finished work (never by the agent that did it), and the check marked [person], the last,
        on every finished piece of work by a person, once with no words, and never by an agent.
        One tick of an agent is taken back by a person and put right by the agent, so that the
        history shows it."""
        items = (await self.call("get_definition_of_done"))["items"]
        ordinary = sum(not entry["verifier_only"] and not entry["person_only"] for entry in items)
        holders = {held.key: held.actor for held in (*data.HELD, *data.LAPSED)}
        finishers = {step.key: step.actor for step in data.HANDOFFS}
        finished = 0
        for placed in place(data.EPICS):
            item = placed.item
            spread = zlib.crc32(item.title.encode())
            how_many = {"done": len(items), "in_progress": 1 + spread % 4, "blocked": 1}.get(
                item.status, 0
            )
            if item.key in data.WAITING_FOR_VERIFIER:
                how_many = ordinary
            if item.key in data.WAITING_FOR_A_PERSON:
                how_many = len(items) - 1  # every line but the last, which only a person ticks
            if item.kind != "task" and item.status != "done":
                how_many = 0
            if item.kind == "epic" or not how_many:
                continue
            key = item.key or ""
            actor = finishers.get(key) or holders.get(key) or data.WORKERS[placed.epic]
            if item.status == "done":
                finished += 1
            for index in range(how_many):
                tick: dict[str, Any] = {"node": placed.ref, "item": items[index]["item"]}
                if items[index]["person_only"]:
                    words = (
                        None
                        if item.title == data.PERSON_TICK_WITH_NO_WORDS
                        else data.PERSON_TRIED[spread % len(data.PERSON_TRIED)]
                    )
                    await self.call("set_check", data.OWNER, evidence=words, **tick)
                elif items[index]["verifier_only"]:
                    words = verified_words(items[index]["github"], placed, self.today)
                    await self.call("set_check", data.VERIFIER, evidence=words, **tick)
                elif item.title == data.UNTICK.task and index == data.UNTICK.check:
                    await self.call("set_check", actor, evidence=data.UNTICK.first, **tick)
                    await self.call(
                        "set_check", data.OWNER, evidence=data.UNTICK.reason, checked=False, **tick
                    )
                    await self.call("set_check", actor, evidence=data.UNTICK.again, **tick)
                else:
                    await self.call("set_check", actor, evidence=check_words(index, placed), **tick)
        if finished == 0:
            raise Stopped("The description has no finished work to tick the checks of.")

    async def check_the_verifier(self) -> None:
        """What the verifier is for, as the server says it: the work that waits for a verifier is
        found by is:unverified (blocked, held by nobody), the ticks of the verifier are its own,
        and an agent whose key is not a verifier's is refused the line that is tagged for one."""
        waiting = {self.ref(key) for key in data.WAITING_FOR_VERIFIER}
        found = await self.call("list_nodes", data.VERIFIER, query="is:unverified", limit=200)
        refs = {entry["ref"] for entry in found["items"]}
        if not waiting <= refs:
            raise Stopped("The work that waits for a verifier is not what is:unverified finds.")
        for ref in refs:
            node = await self.call("get_node", node=ref)
            if node["status"] != "blocked" or node["claimed_by"] is not None:
                raise Stopped(f"is:unverified found {ref}, which is not blocked work nobody holds.")
        for ref in sorted(waiting):
            node = await self.call("get_node", node=ref)
            open_checks = [c["text"] for c in node["checks"] if not c["checked"]]
            if len(open_checks) != 3 or node["handoff_count"] < 1:
                raise Stopped(f"{ref} does not wait for a verifier as described.")
        items = (await self.call("get_definition_of_done"))["items"]
        tagged = next(entry for entry in items if entry["verifier_only"] and not entry["github"])
        refused = await self.clients["api-agent"].call_tool(
            "set_check", {"node": min(waiting), "item": tagged["item"], "evidence": "Looked."}
        )
        told = " ".join(getattr(block, "text", "") for block in refused.content)
        if not refused.is_error or "only a verifier can judge" not in told:
            raise Stopped("An agent that is not a verifier was not refused a verifier's check.")
        done = next(
            placed.ref
            for placed in place(data.EPICS)
            if placed.item.kind == "task" and placed.item.status == "done"
        )
        node = await self.call("get_node", node=done)
        ticked = [check for check in node["checks"] if check["verifier_only"]]
        if len(ticked) != 2 or any(
            (check["checked_by"], check["checked_by_kind"]) != (data.VERIFIER, "agent")
            for check in ticked
        ):
            raise Stopped("The verifier's ticks are not the verifier's own.")
        said(f"    {len(refs)} blocked items are found by is:unverified, {len(waiting)} of them")
        said("    handed over for a verifier; the rest are blocked on something else")

    async def check_the_asks(self) -> None:
        """What asks and what needs a person came to, as the server says it: every ask of the
        description is there, put about the right work, with the answer, the person who gave it and
        the comment as described (or none); an ask that blocks work and is open leaves it blocked,
        one that parks it leaves it free, and an answered one leaves nothing blocked by it; the
        list of what needs a person holds exactly the rows the description expects, by story; and
        the viewer, who can look, is refused when answering, and changes nothing."""
        wanted = asks_of_description()
        listed = await self.call("list_asks", limit=200)
        found_by_question = {found["question"]: found for found in listed["items"]}
        if listed["total"] != len(wanted) or len(found_by_question) != len(wanted):
            raise Stopped(
                f"The demo holds {listed['total']} asks, not the {len(wanted)} described."
            )
        for key, ask in wanted:
            found = found_by_question.get(ask.question)
            node = await self.call("get_node", node=self.ref(key))
            given = (ask.answer is not None, ask.by, ask.answer, ask.comment)
            if (
                found is None
                or found["node"] != self.ref(key)
                or (found["answered"], found["answered_by"], found["answer"], found["comment"])
                != given
                or (found["kind"] == "approval") != ask.approval
            ):
                raise Stopped(f"The ask about {key} is not as described.")
            held_up = found["effect"] == "blocks" and not found["answered"]
            if held_up != (node["status"] == "blocked" and node["claimed_by"] is None):
                raise Stopped(f"The work of the ask about {key} is not blocked or free as asked.")
        needs = await self.call("needs_a_person")
        rows = sorted(
            (item["kind"], item["node"], group["story"])
            for group in needs["groups"]
            for item in group["items"]
        )
        expected = expected_needs()
        if rows != sorted(expected) or needs["total"] != len(expected) or needs["left_out"]:
            raise Stopped(
                f"What needs a person is not what the description expects: {rows} and not "
                f"{sorted(expected)}."
            )
        waiting = [found for found in listed["items"] if not found["answered"]]
        path = f"/board/api/projects/{self.project_id}/tool"
        tried = await asyncio.to_thread(
            self.people[data.VIEWER].call,
            "POST",
            f"{path}/answer_ask",
            {"ask": waiting[0]["id"], "answer": (waiting[0]["choices"] or ["Go ahead"])[0]},
        )
        still = await self.call("list_asks", status="open", limit=200)
        if tried.status not in (400, 403) or still["total"] != len(waiting):
            raise Stopped(f"The viewer should be refused when answering ({tried.status}).")
        said(
            f"    {len(wanted)} asks ({len(waiting)} waiting for a person), and "
            f"{len(expected)} things that need a person under {len(needs['groups'])} stories"
        )

    async def keep_answers(self) -> None:
        """A person kept some answers as decisions, as the board's Keep as a decision does after an
        answer stood: a note by that person on the work, the question and the answer as its text and
        their comment as why, naming them, linked to the work. It is a note like any other (never
        edited, unconfirmed until a person confirms it), so the notes of the description are
        counted again with it, and it adds no row to what needs a person."""
        before = expected_note_counts()
        wanted = kept_of_description()
        for key, ask in wanted:
            ref = self.ref(key)
            answered = await self.call("list_asks", node=ref, status="answered")
            number = next(
                (found["id"] for found in answered["items"] if found["question"] == ask.question),
                None,
            )
            if number is None or ask.by is None or ask.answer is None:
                raise Stopped(f"{key} has no answered ask to keep as a decision.")
            made = await self.as_person(
                "add_note",
                {
                    "text": f"Q: {ask.question}\nA: {ask.answer}",
                    "why": ask.comment or f"Answered Q-{number}.",
                    "decided_by": ask.by,
                    "nodes": [ref],
                },
                ask.by,
            )
            if (
                made["decided_by"] != ask.by
                or made["recorded_by"] != ask.by
                or made["status"] != "unconfirmed"
                or made["nodes"] != [ref]
            ):
                raise Stopped(f"The decision kept for {key} is not recorded as {ask.by}'s.")
        asked: dict[str, dict[str, Any]] = {
            "all": {},
            "decided": {"decided": True},
            "unconfirmed": {"status": "unconfirmed"},
            "in_force": {"status": "in_force"},
        }
        for name, how in asked.items():
            found = (await self.call("list_notes", limit=1, **how))["total"]
            if found != before[name] + len(wanted):
                raise Stopped(
                    f"The demo holds {found} notes counted as {name}, not "
                    f"{before[name] + len(wanted)}."
                )

    # -- notes -----------------------------------------------------------------------------------

    async def notes(self) -> None:
        for number, note in enumerate(data.NOTES, start=1):
            common: dict[str, Any] = {
                "actor": recorder_of(note),
                "text": self.names.text(note.text),
                "why": self.names.text(note.why),
                "decided_by": note.by,
                "decided_on": None if note.ago is None else day(self.today, -note.ago),
                "nodes": self.refs(note.nodes),
                "rule_ids": self.rule_ids(note.rules),
                "note_ids": self.note_ids(note.notes),
            }
            if not note.replaces:
                made = await self.call("add_note", project_wide=note.project_wide, **common)
                made_id = made["id"]
            else:
                made = await self.call(
                    "supersede_note",
                    replaces=[self.names.notes[key] for key in note.replaces],
                    keep_links=True,
                    **common,
                )
                made_id = made["replacement"]["id"]
            if made_id != number:
                raise Stopped(f"{note.key} became note {made_id}, not {number}.")

    async def confirmations(self) -> None:
        """Only a person can confirm, so these are made through the board by a person: the planner,
        as herself, for what she reviewed, and the owner for the reviewers who have no account. The
        person who reviewed the note is named in it, whoever presses the button."""
        for step in data.CONFIRMATIONS:
            await self.call(
                "set_confirmation",
                actor=confirmer_of(step),
                note_id=self.names.notes[step.key],
                confirmation="confirmed" if step.confirmed else "unconfirmed",
                person=step.person,
                quote=step.quote or None,
            )

    async def withdrawals(self) -> None:
        for step in data.WITHDRAWALS:
            await self.call(
                "withdraw_note",
                actor=data.RECORDER,
                note_ids=[self.names.notes[step.key]],
                reason=step.reason,
            )

    async def check_notes(self) -> None:
        """What list_notes says the notes came to is what the description says they should."""
        asked: dict[str, dict[str, Any]] = {
            "all": {},
            "decided": {"decided": True},
            "project_wide": {"project_wide": True},
            "confirmed": {"status": "confirmed"},
            "unconfirmed": {"status": "unconfirmed"},
            "in_force": {"status": "in_force"},
            "superseded": {"status": "superseded"},
            "withdrawn": {"status": "withdrawn"},
        }
        for name, wanted in expected_note_counts().items():
            found = (await self.call("list_notes", limit=1, **asked[name]))["total"]
            if found != wanted:
                raise Stopped(f"The demo holds {found} notes counted as {name}, not {wanted}.")

    # -- who is working on what ------------------------------------------------------------------

    async def hand_off(self) -> None:
        for step in data.HANDOFFS:
            ref = self.ref(step.key)
            said_where = whereabouts(
                step.where, step.actor, self.names.node(step.key).item.title, self.today
            )
            await self.call(
                "claim_node",
                actor=step.actor,
                node=ref,
                lease_seconds=HANDOFF_LEASE,
                **{field: said_where.get(field) for field in CLAIM_FIELDS},
            )
            await self.call(
                "finish_work",
                actor=step.actor,
                node=ref,
                outcome=step.outcome,
                note=self.names.text(step.note),
                pull_request=said_where.get("pull_request"),
                ask=None if step.ask is None else ask_arguments(step.ask),
            )
            if step.ask is not None and step.ask.answer is not None:
                await self.answer(ref, step.ask)

    async def claim(self, held: data.Held, lease_seconds: int) -> None:
        ref = self.ref(held.key)
        said_where = whereabouts(
            held.where, held.actor, self.names.node(held.key).item.title, self.today
        )
        current = await self.call("get_node", node=ref)
        if current["status"] != "in_progress":
            await self.change(held.actor, ref, status="in_progress")
        await self.call(
            "claim_node",
            actor=held.actor,
            node=ref,
            lease_seconds=lease_seconds,
            **{field: said_where.get(field) for field in CLAIM_FIELDS},
        )
        pull_request = said_where.get("pull_request")
        if pull_request is not None:
            # The agent opened a pull request after it began, and says so as it renews its claim.
            await self.call(
                "renew_claim",
                actor=held.actor,
                node=ref,
                lease_seconds=lease_seconds,
                commit=commit_of(f"{said_where['branch']} again"),
                pull_request=pull_request,
            )

    async def claims(self) -> None:
        for held in data.HELD:
            await self.claim(held, LONG_LEASE)
        # The work that ran out twice is first taken by one agent, whose claim runs out, and then
        # by another, whose claim runs out too.
        first = [data.Held(held.key, held.after) for held in data.LAPSED if held.after]
        for held in first:
            await self.claim(held, 1)
        if first:
            await asyncio.sleep(2)
        for held in data.LAPSED:
            await self.claim(held, 1)
        if data.LAPSED:
            await asyncio.sleep(2)

    # -- later changes ---------------------------------------------------------------------------

    async def edits(self) -> None:
        for edit in data.EDITS:
            await self.change(
                edit.actor,
                self.ref(edit.key),
                title=edit.title,
                body=None if edit.body is None else self.names.text(edit.body),
                priority=edit.priority,
                start_on=day(self.today, edit.start),
                target_on=day(self.today, edit.due),
                clear_target_on=True if edit.clear_due else None,
            )

    # -- the whole run ---------------------------------------------------------------------------

    async def check_roles(self) -> None:
        """What the people of the team may do, as the server enforces it: the viewer can look and
        is refused a change, and the history says the owner and the planner each made changes, as
        people."""
        viewer = self.people[data.VIEWER]
        path = f"/board/api/projects/{self.project_id}/tool"
        looked = await asyncio.to_thread(viewer.call, "POST", f"{path}/list_nodes", {})
        tried = await asyncio.to_thread(
            viewer.call, "POST", f"{path}/create_node", {"kind": "epic", "title": "Not allowed"}
        )
        if looked.status != 200 or tried.status != 403:
            raise Stopped(
                f"The viewer should be able to look ({looked.status}) and not to change "
                f"({tried.status})."
            )
        reader = self.clients[data.RECORDER]
        for name in (data.OWNER, data.PLANNER):
            seen = await reader.call_tool("changes_since", {"actor": name, "limit": 1})
            events = (seen.structured_content or {}).get("items", [])
            if [(event["actor"], event["actor_kind"]) for event in events] != [(name, "person")]:
                raise Stopped(f"The history does not show {name} making changes, as a person.")

    async def wait_for_index(self) -> None:
        """Meaning search needs the index to have caught up. Without an embedding service there is
        nothing to wait for."""
        for _ in range(90):
            index = (await self.call("server_status"))["index"]
            if index["meaning"] == "not_configured" or (
                index["meaning"] == "used" and index["waiting_to_index"] == 0
            ):
                return
            await asyncio.sleep(1)
        said("The search index has not caught up yet. It will, in a minute or so.")

    async def build(self) -> dict[str, int]:
        flow = Flow(self)
        steps = (
            ("planning the work", self.make_plan),
            ("moving the work along", self.move_statuses),
            ("writing down acceptance criteria", self.rules),
            ("recording notes", self.notes),
            ("confirming", self.confirmations),
            ("withdrawing", self.withdrawals),
            ("checking the notes", self.check_notes),
            ("handing work over", self.hand_off),
            ("taking claims", self.claims),
            ("changing plans", self.edits),
            # Work is finished before the definition of done is set, so that agents who finish it
            # are not asked for ticks that do not exist yet; they tick afterwards.
            ("setting the definition of done", self.definition_of_done),
            ("ticking its checks on finished work", self.ticks),
            ("checking the verifier", self.check_the_verifier),
            ("checking the asks and what needs a person", self.check_the_asks),
            ("keeping an answer as a decision", self.keep_answers),
            ("checking what each person may do", self.check_roles),
            # The later part (M42 to M47) comes after the checks above, which count the asks and
            # the rows of what needs a person as the description has them. Where loose work
            # belongs is asked of the real embedding service, so the index is caught up first.
            ("filing loose work in an Inbox", flow.file_the_inbox),
            ("indexing for search", self.wait_for_index),
            ("proposing where loose work belongs, as the steward", flow.steward_proposes),
            ("accepting one proposal and rejecting another", flow.people_decide),
            ("holding work behind gates", flow.gate_work),
            ("saving plan templates and drafting plans", flow.plans_and_templates),
            ("asking for changes to two plans and revising one", flow.a_conversation_about_plans),
            ("checking gates, proposals, plans and templates", flow.check),
            ("indexing for search", self.wait_for_index),
        )
        for what, step in steps:
            said(f"  {what}")
            await step()
        status = await self.call("server_status")
        counts: dict[str, int] = status["counts"]
        for kind, wanted in expected_totals().items():
            if counts[kind] != wanted:
                raise Stopped(f"The demo holds {counts[kind]} {kind}, not the {wanted} described.")
        return counts


async def fill(
    url: str, keys: dict[str, str], people: dict[str, Person], project_id: int
) -> dict[str, int]:
    """Fill the project with the sample data: each agent with its own key (`keys` maps an agent's
    name to its key), the people through the board as themselves (`people` maps a name to the
    person signed in)."""
    today = datetime.datetime.now(datetime.UTC).date()
    try:
        async with AsyncExitStack() as stack:
            clients: dict[str, Client] = {}
            for name, key in keys.items():
                http = await stack.enter_async_context(
                    create_mcp_http_client(headers={"Authorization": f"Bearer {key}"})
                )
                clients[name] = await stack.enter_async_context(
                    Client(streamable_http_client(url, http_client=http))
                )
            return await Demo(clients, people, project_id, today, url, keys).build()
    except BaseExceptionGroup as group:
        # Each connection is inside the one before it, so a problem arrives wrapped once for each:
        # say the problem itself.
        problem: BaseException = group
        while isinstance(problem, BaseExceptionGroup):
            problem = problem.exceptions[0]
        raise problem from None


def register(base: str, email: str, name: str, password: str) -> Person:
    """A fictional account, registered and signed in through the board's own API."""
    reply = api(
        base, "POST", "/board/api/accounts", {"email": email, "name": name, "password": password}
    )
    if reply.status != 201 or not reply.cookie:
        raise Stopped(
            f"The account for {email} was refused ({reply.status}). The demo only fills a server "
            f"that was just built for it. {reply.body}"
        )
    return Person(
        base, email, password, name, reply.cookie, reply.body["csrf"], reply.body["account"]["id"]
    )


def sign_up(base: str, password: str) -> Person:
    """The owner's account. On a server that has ever held another account it is not the first,
    and the run stops before it makes anything else."""
    person = register(base, data.OWNER_EMAIL, data.OWNER, password)
    if person.id != 1:
        raise Stopped(
            f"This server already held other accounts (the new one is number {person.id}). The "
            "demo only fills a server that was just built for it."
        )
    return person


def join_the_team(owner: Person, project_id: int, password: str) -> dict[str, Person]:
    """The other two people, each of whom registers and joins the project the way people do: the
    owner makes an invitation for a role, and the person accepts it. The planner is an editor and
    the viewer can only look. They have the same password as the owner. When the server can send
    email the planner's invitation is sent to her (fictional) address, so what a mail server would
    have been sent can be looked at, and accepting an invitation that was emailed to an address
    shows the address to be hers: her account is a CONFIRMED one. The viewer's invitation is not
    sent, so that his address is never confirmed (the demo's stand-in providers show both: a
    confirmed account is joined to a person who signs in with that address, and one that was never
    confirmed is not). The link the owner is given is what is used in both cases."""
    sends_mail = bool(
        api(owner.base, "GET", "/board/api/session", cookie=owner.cookie).body["mail"]
    )
    team: dict[str, Person] = {}
    for name, email, role in (
        (data.PLANNER, data.PLANNER_EMAIL, "editor"),
        (data.VIEWER, data.VIEWER_EMAIL, "viewer"),
    ):
        emailed = sends_mail and email == data.STAND_IN_CONFIRMED_EMAIL
        wanted = {"role": role, "email": email} if emailed else {"role": role}
        invited = owner.call("POST", f"/board/api/projects/{project_id}/invites", wanted)
        if invited.status != 201:
            raise Stopped(f"The invitation for {name} was refused: {invited.body}")
        if emailed and not invited.body["emailed"]:
            raise Stopped(
                f"The invitation email for {name} was not sent: {invited.body.get('email_problem')}"
            )
        person = register(owner.base, email, name, password)
        joined = person.call("POST", f"/board/api/invites/{invited.body['token']}/accept")
        if joined.status != 200 or joined.body["project"]["role"] != role:
            raise Stopped(f"{name} could not join as {role}: {joined.body}")
        team[name] = person
    return team


def check_the_stand_in_people(team: dict[str, Person]) -> str | None:
    """When the server offers other ways to sign in, the three kinds of person the stand-in
    providers know are as the demo says: an account whose address is confirmed, one whose address
    is not, and an address with no account at all. Returns what to tell the person, or None when
    the server offers no other way to sign in."""
    providers: list[dict[str, Any]] = []
    for person in team.values():
        shown = api(person.base, "GET", "/board/api/session", cookie=person.cookie).body
        if not shown.get("providers"):
            return None
        providers = shown["providers"]
        confirmed = shown["account"]["email_verified"]
        wanted = person.email == data.STAND_IN_CONFIRMED_EMAIL
        if confirmed != wanted:
            raise Stopped(
                f"The account of {person.email} is {'' if confirmed else 'not '}confirmed, "
                f"which is not what the stand-in sign-in people need."
            )
    names = ", ".join(str(one["name"]) for one in providers)
    return (
        f"  sign in with another provider ({names}): {data.STAND_IN_CONFIRMED_EMAIL} has a "
        f"confirmed account, {data.STAND_IN_UNCONFIRMED_EMAIL} has one that is not confirmed, "
        f"and {data.STAND_IN_NEW_EMAIL} has none"
    )


async def main(url: str) -> str:
    host = urlparse(url).hostname
    if host not in ("127.0.0.1", "localhost", "::1") or not url.endswith("/mcp"):
        raise Stopped(
            "Give the MCP address of the demo's own server on this machine, such as "
            "http://127.0.0.1:8091/mcp."
        )
    base = url.removesuffix("/mcp")
    password = secrets.token_hex(10)
    person = sign_up(base, password)
    made = person.call("POST", "/board/api/projects", {"name": data.PROJECT})
    if made.status != 201:
        raise Stopped(f"The project was refused: {made.body}")
    project_id = int(made.body["id"])
    team = join_the_team(person, project_id, password)
    people = {data.OWNER: person, **team}
    sign_in_arranged = check_the_stand_in_people(team)
    path = f"/board/api/projects/{project_id}/keys"
    keys: dict[str, str] = {}
    ids: dict[str, int] = {}
    kept: set[str] = set()
    # The loader's own keys may use every tool a key can be given (it moves acceptance criteria,
    # reads them again and checks the server), which is more than the set agents are offered: the
    # three keys that stay in force are narrowed afterwards, and show what an agent really gets.
    listing = person.call("GET", "/board/api/tools")
    if listing.status != 200:
        raise Stopped(f"The server's tool list was refused: {listing.body}")
    loader_tools = sorted(
        tool["name"]
        for tool in listing.body["tools"]
        if not tool["people_only"] and not tool["code_mode"]
    )
    try:
        for name in agents_of_description():
            key = person.call(
                "POST",
                path,
                {"name": name, "tools": loader_tools, "verifier": name == data.VERIFIER},
            )
            if key.status != 201 or key.body["key"]["verifier"] != (name == data.VERIFIER):
                raise Stopped(f"The key for {name} was refused: {key.body}")
            keys[name] = key.body["secret"]
            ids[name] = key.body["key"]["id"]
        # The steward's key may use four tools and none that changes work, from the first.
        steward = person.call(
            "POST", path, {"name": data.STEWARD, "tools": list(data.STEWARD_TOOLS)}
        )
        if steward.status != 201 or sorted(steward.body["key"]["tools"]) != sorted(
            data.STEWARD_TOOLS
        ):
            raise Stopped(f"The key for {data.STEWARD} was refused: {steward.body}")
        keys[data.STEWARD] = steward.body["secret"]
        ids[data.STEWARD] = steward.body["key"]["id"]
        counts = await fill(url, keys, people, project_id)
        await limit_the_kept_keys(url, person, path, keys, ids)
        last_day = await give_a_key_a_last_day(person, path, ids)
        kept = {*data.KEPT_KEYS, data.STEWARD}
    finally:
        # A run that stopped leaves no key in force; one that finished keeps the ones that show
        # what a key may do (the steward's is one of them).
        for name, key_id in ids.items():
            if name not in kept:
                person.call("DELETE", f"{path}/{key_id}")
    said(
        "  filled: "
        + ", ".join(f"{counts[name]} {name}" for name in ("epics", "stories", "tasks", "links"))
        + f", {counts['rules']} acceptance criteria, {counts['notes']} notes "
        + f"({expected_note_counts()['decided'] + len(kept_of_description())} with a person who "
        + "decided them), "
        + f"{len(asks_of_description())} asks put to a person, "
        + f"{counts['events']} changes in the history "
        + f"(made by {len(keys)} agents and 2 people; {data.VIEWER} can only look; "
        + f"{len(kept)} keys stay in force, with different allowances, {data.VERIFIER}'s "
        + f"key is a verifier's and {data.EXPIRING_KEY}'s key runs out on {last_day})"
    )
    if sign_in_arranged:
        said(sign_in_arranged)
    return password


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(asyncio.run(main(sys.argv[1])))
