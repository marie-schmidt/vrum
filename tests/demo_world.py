"""Fills a NEW Vrum with the sample project of ./demo.sh (Riverside Bike Share, described in
tests/demo_data.py). It is a developer tool, not part of the product and not a way to seed an
install:

    uv run python -m tests.demo_world http://127.0.0.1:8091/mcp

It registers the fictional account demo@example.com with a random password through the board's own
HTTP API, makes a project, and fills it through real operations: a team plans the work (with a size
on most of it), people and agents move it along, take claims and leave handoff notes, record notes
(some with the person who decided them) and confirm some of them, and the owner sets the
definition of done, whose checks are then ticked on the finished work, by the agents with how they
checked and by a person for the one only a person can judge. Two more fictional people join the
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
review-agent only the tools that read, docs-agent also the tools that change work, links, claims,
acceptance criteria and notes), so that the Users page shows what a key may do. Nobody holds
their secrets, and a check shows that a limited key is refused what it may not do.

It refuses a server that is not on this machine or that has ever held another account (the account
it makes must be number 1), so it cannot be pointed at a real install by mistake. The password is
printed on standard output and nothing else is; everything else is said on standard error. Nothing
is written anywhere.
"""

import asyncio
import datetime
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
from tests.world import Person, api

# The longest a claim may be held (24 hours), so that the claims of the demo are still held when
# someone looks at it. One that must have run out is taken for one second.
LONG_LEASE = 86_400
HANDOFF_LEASE = 3_600

# The stored statuses an item goes through on its way to the status it is described with.
STATUS_PATH = {
    "in_progress": ("in_progress",),
    "done": ("in_progress", "done"),
    "blocked": ("in_progress", "blocked"),
    "dropped": ("dropped",),
}

_REFERENCE = re.compile(r"\[\[([a-z0-9][a-z0-9-]*)\]\]")


class Stopped(SystemExit):
    """Something is wrong with where this was pointed, or with the data, said in plain words."""


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
    """How much of each kind the description holds, as server_status counts them."""
    placed = place(data.EPICS)
    return {
        "epics": sum(p.item.kind == "epic" for p in placed),
        "stories": sum(p.item.kind == "story" for p in placed),
        "tasks": sum(p.item.kind == "task" for p in placed),
        "links": len(data.LINKS),
        "rules": len(data.RULES) + len(derived_criteria(placed)),
        "notes": len(data.NOTES),
    }


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


def agents_of_description() -> list[str]:
    """Every agent the description names (a name that ends with "-agent"), each of which gets a key
    of its own. Sorted, so the keys are made in the same order every time."""
    named = {data.RECORDER, *data.WORKERS.values()}
    named |= {step.actor for step in data.HANDOFFS}
    named |= {held.actor for held in (*data.HELD, *data.LAPSED)}
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


def allowances(tools: list[dict[str, Any]]) -> dict[str, list[str] | None]:
    """What each key that stays in force may do, worked out from the server's own tool list (the
    entries of GET /board/api/tools): None for the tools Vrum offers agents, or the tools it may
    use (only ones that are offered)."""
    offered = [tool for tool in tools if tool["offered_to_agents"]]
    reads = [tool["name"] for tool in offered if not tool["changes_something"]]
    recording = [
        tool["name"]
        for tool in offered
        if tool["changes_something"]
        and not tool["people_only"]
        and tool["area"] in data.DOCS_AGENT_AREAS
    ]
    chosen: dict[str, list[str] | None] = {
        "api-agent": None,
        "review-agent": sorted(reads),
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


class Demo:
    """One run: a client for each agent, the people who have an account (each signed in on the
    board, by name), and the numbers everything has."""

    def __init__(
        self,
        clients: dict[str, Client],
        people: dict[str, Person],
        project_id: int,
        today: datetime.date,
    ) -> None:
        self.clients = clients
        self.people = people
        self.project_id = project_id
        self.today = today
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
        through the note."""
        noted = {handoff.key for handoff in data.HANDOFFS}
        holders = {held.key: held.actor for held in (*data.HELD, *data.LAPSED)}
        for placed in place(data.EPICS):
            item = placed.item
            if item.status == "new" or (item.key in noted):
                continue
            actor = holders.get(item.key or "") or data.WORKERS[placed.epic]
            for status in STATUS_PATH[item.status]:
                await self.change(actor, placed.ref, status=status)

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
        if (
            len(stored["items"]) != len(data.CHECK_EVIDENCE) + 1
            or not stored["items"][-1]["person_only"]
        ):
            raise Stopped("The definition of done does not have the checks that are described.")

    async def ticks(self) -> None:
        """Whoever finished work ticks the checks of the definition of done and says how they were
        done: all of them on what is done, the first few on what is under way, the first on what is
        blocked and none on the rest. The check marked [person], the last, is ticked on every
        finished piece of work by a person, once with no words, and never by an agent. One tick of
        an agent is taken back by a person and put right by the agent, so that the history shows
        it."""
        items = (await self.call("get_definition_of_done"))["items"]
        holders = {held.key: held.actor for held in (*data.HELD, *data.LAPSED)}
        finishers = {step.key: step.actor for step in data.HANDOFFS}
        finished = 0
        for placed in place(data.EPICS):
            item = placed.item
            spread = zlib.crc32(item.title.encode())
            how_many = {"done": len(items), "in_progress": 1 + spread % 4, "blocked": 1}.get(
                item.status, 0
            )
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
            await self.call("claim_node", actor=step.actor, node=ref, lease_seconds=HANDOFF_LEASE)
            await self.call(
                "finish_work",
                actor=step.actor,
                node=ref,
                outcome=step.outcome,
                note=self.names.text(step.note),
            )

    async def claim(self, held: data.Held, lease_seconds: int) -> None:
        ref = self.ref(held.key)
        current = await self.call("get_node", node=ref)
        if current["status"] != "in_progress":
            await self.change(held.actor, ref, status="in_progress")
        await self.call("claim_node", actor=held.actor, node=ref, lease_seconds=lease_seconds)

    async def claims(self) -> None:
        for held in data.HELD:
            await self.claim(held, LONG_LEASE)
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
            ("checking what each person may do", self.check_roles),
            ("indexing for search", self.wait_for_index),
        )
        for what, step in steps:
            said(f"  {what}")
            await step()
        status = await self.call("server_status")
        counts: dict[str, int] = status["counts"]
        for kind, wanted in expected_counts().items():
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
            return await Demo(clients, people, project_id, today).build()
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
    loader_tools = sorted(tool["name"] for tool in listing.body["tools"] if not tool["people_only"])
    try:
        for name in agents_of_description():
            key = person.call("POST", path, {"name": name, "tools": loader_tools})
            if key.status != 201:
                raise Stopped(f"The key for {name} was refused: {key.body}")
            keys[name] = key.body["secret"]
            ids[name] = key.body["key"]["id"]
        counts = await fill(url, keys, people, project_id)
        await limit_the_kept_keys(url, person, path, keys, ids)
        kept = set(data.KEPT_KEYS)
    finally:
        # A run that stopped leaves no key in force; one that finished keeps the three that show
        # what a key may do.
        for name, key_id in ids.items():
            if name not in kept:
                person.call("DELETE", f"{path}/{key_id}")
    said(
        "  filled: "
        + ", ".join(f"{counts[name]} {name}" for name in ("epics", "stories", "tasks", "links"))
        + f", {counts['rules']} acceptance criteria, {counts['notes']} notes "
        + f"({expected_note_counts()['decided']} with a person who decided them), "
        + f"{counts['events']} changes in the history "
        + f"(made by {len(keys)} agents and 2 people; {data.VIEWER} can only look; "
        + f"{len(kept)} keys stay in force, with different allowances)"
    )
    if sign_in_arranged:
        said(sign_in_arranged)
    return password


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(asyncio.run(main(sys.argv[1])))
