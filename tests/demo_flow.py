"""The later part of the sample project of ./demo.sh (M42 to M53), loaded by tests/demo_world.py
after the plan, the notes, the claims and the checks, through the same real tools and the board's
own calls: work that arrived loose from calls and notes and waits in an Inbox epic, a steward whose
proposals say where it belongs (the real `suggest_parent` on the real embedding service, and the
real scripts/steward.py), a person who accepted one proposal and one who rejected another, a third
proposal about two tasks that a person accepted in part (one moved, one left out), gates
on work (a timer and approvals, one of them refused), a plan that waits for approval, and two plan
templates with a plan that was made from one and approved. Two drafted plans have a conversation
(M51, M53): the owner asked for changes to the winter plan, with comments on three lines (one asks
for a task to be dropped) and four items approved as they are, and its agent sent revision 2, which
removes that task and changes the two other lines, and which a person reads next; the owner asked
for changes to a small spring plan and its agent has not revised it yet. The description is in
tests/demo_data.py. Nothing here knows which epic a loose story belongs to: it uses what the
server says, and it stops with a plain message if the model is sure of too few."""

import asyncio
import datetime
import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from tests import demo_data as data

if TYPE_CHECKING:
    from tests.demo_world import Demo

ROOT = Path(__file__).resolve().parent.parent
# The fewest proposals to existing epics that the demo must show, so that one can be accepted, one
# rejected and one left waiting (the proposal that groups stories under a new epic waits too).
FEWEST_SURE = 2


def said(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class Stopped(SystemExit):
    """Something is wrong with where this was pointed, or with the data, said in plain words."""


def kinds_of(items: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, int]:
    counts = {"epic": 0, "story": 0, "task": 0}
    for item in items:
        counts[str(item["kind"])] += 1
    return counts


def later_counts() -> dict[str, int]:
    """How much the later part adds to each count that server_status gives: the Inbox and its
    loose stories, the drafted winter plan (the task that revision 2 takes out is dropped, and a
    dropped task still counts), the drafted spring plan that waits for its agent, and the plan made
    from the station launch template (the winter and spring plans hang under an epic that exists
    and the launch template's epic is named by a value, so none of them adds an epic of its own)."""
    winter = kinds_of(data.WINTER_ITEMS)
    spring = kinds_of(data.SPRING_ITEMS)
    launch_template = data.TEMPLATES[data.STATION_LAUNCH]
    launch = kinds_of(launch_template["items"])
    return {
        "epics": 1 + winter["epic"] + spring["epic"] + launch["epic"],
        "stories": len(data.LOOSE) + winter["story"] + spring["story"] + launch["story"],
        "tasks": winter["task"] + spring["task"] + launch["task"],
        "links": len(data.WINTER_LINKS) + len(launch_template["links"]),
        "rules": len(data.WINTER_CRITERIA) + len(launch_template.get("rules", [])),
    }


def winter_plan(today: datetime.date, ref: Callable[[str], str]) -> dict[str, Any]:
    """The arguments of `create_plan` for the drafted winter plan. A name the plan does not use as
    a key of its own is the description's key for an existing item (the epic it hangs under, the
    story it relates to), and `ref` gives the reference the server gave that item. The story with
    dates has them counted from the day the demo is made."""
    own = {str(item["key"]) for item in data.WINTER_ITEMS}

    def named(key: str) -> str:
        return key if key in own else ref(key)

    items: list[dict[str, Any]] = []
    for item in data.WINTER_ITEMS:
        made: dict[str, Any] = dict(item)
        if "parent" in made:
            made["parent"] = named(str(made["parent"]))
        if item["key"] in data.WINTER_DAYS:
            start, target = data.WINTER_DAYS[str(item["key"])]
            made["start_on"] = (today + datetime.timedelta(days=start)).isoformat()
            made["target_on"] = (today + datetime.timedelta(days=target)).isoformat()
        items.append(made)
    return {
        "items": items,
        "links": [
            {"source": named(source), "target": named(target), "link_type": kind}
            for source, target, kind in data.WINTER_LINKS
        ],
        "rules": [{"title": title, "node": named(node)} for node, title in data.WINTER_CRITERIA],
    }


def winter_revision(
    today: datetime.date, ref: Callable[[str], str], refs: dict[str, str]
) -> list[dict[str, Any]]:
    """The items of `revise_plan` for revision 2 of the winter plan: the whole plan again, each
    item continuing the one it was (`refs` gives the reference the server gave each key), the
    two parts that were asked for in new words and every other part as it was drafted, and the
    items that revision 2 takes out left out. Links and criteria are left out of the call, which
    keeps them."""
    return [
        {**item, **data.WINTER_REVISED.get(str(item["key"]), {}), "ref": refs[str(item["key"])]}
        for item in winter_plan(today, ref)["items"]
        if item["key"] not in data.WINTER_REMOVED
    ]


def spring_plan(ref: Callable[[str], str]) -> dict[str, Any]:
    """The arguments of `create_plan` for the drafted spring plan: one story under the epic that
    exists (`ref` gives its reference) and two tasks under it."""
    own = {str(item["key"]) for item in data.SPRING_ITEMS}
    return {
        "items": [
            {**item, "parent": item["parent"] if item["parent"] in own else ref(item["parent"])}
            for item in data.SPRING_ITEMS
        ]
    }


def in_order(refs: Iterable[str]) -> list[str]:
    """References in the order the server lists them: epics, stories, then tasks, each by number."""
    return sorted(
        refs,
        key=lambda ref: (
            ("epic", "story", "task").index(ref.split(":")[0]),
            int(ref.split(":")[1]),
        ),
    )


def comments_on(
    refs: dict[str, str], feedback: tuple[tuple[str, str | None, str], ...]
) -> list[dict[str, str]]:
    """What a person says on the lines of a plan, as `request_plan_changes` takes it: a part of
    the item, or the whole item when no part is named."""
    return [
        {"item": refs[key], **({} if part is None else {"field": part}), "text": text}
        for key, part, text in feedback
    ]


class Flow:
    """One run of the later part. Each step leaves what the next reads on the instance."""

    def __init__(self, demo: "Demo") -> None:
        self.demo = demo
        self.inbox = ""
        self.loose: dict[str, str] = {}
        self.grouped_proposal = 0
        self.checked_proposal = 0
        self.partial_proposal = 0
        self.accepted: dict[str, Any] = {}
        self.partial: dict[str, Any] = {}
        self.rejected: dict[str, Any] = {}
        self.waiting_ids: list[int] = []
        self.gate_ids: dict[str, dict[str, Any]] = {}
        self.winter = 0
        self.winter_refs: dict[str, str] = {}
        self.spring = 0
        self.spring_refs: dict[str, str] = {}
        self.launch = 0

    def by_hand(self) -> set[int]:
        """The proposals the steward made itself and its script did not: the one that groups two
        stories under a new epic, the one a person should look at twice and the one about two
        tasks that a person accepts in part."""
        return {self.grouped_proposal, self.checked_proposal, self.partial_proposal}

    async def call(self, tool: str, actor: str | None = None, **arguments: Any) -> dict[str, Any]:
        return await self.demo.call(tool, actor, **arguments)

    # -- work that arrived loose ---------------------------------------------------------------

    async def file_the_inbox(self) -> None:
        """The intake agent files what came in from calls and notes, as stories in an epic called
        Inbox, which is only a convention: the epic has that name."""
        made = await self.call(
            "create_node", data.INTAKE, kind="epic", title=data.INBOX, body=data.INBOX_BODY
        )
        self.inbox = made["ref"]
        for loose in data.LOOSE:
            story = await self.call(
                "create_node",
                data.INTAKE,
                kind="story",
                title=loose.title,
                body=loose.body,
                parent=self.inbox,
            )
            self.loose[loose.title] = story["ref"]

    async def steward_proposes(self) -> None:
        """The steward groups two stories under a new epic (its own proposal: naming a group is for
        an agent or a person), then scripts/steward.py proposes the rest only where the model is
        sure, which it says first with --dry-run. Fewer than FEWEST_SURE sure stories stops the
        run: the loose stories then need other words."""
        probe = await self.demo.clients[data.STEWARD].call_tool(
            "suggest_parent", {"node": self.loose[data.LOOSE[0].title], "limit": 1}
        )
        if probe.is_error:
            told = " ".join(getattr(block, "text", "") for block in probe.content)
            raise_stopped(
                "The steward cannot ask where work belongs, which needs search by meaning, and "
                f"the demo shows it on the real embedding service. The server said: {told}"
            )
        grouped = [self.loose[title] for title in data.GROUPED.stories]
        made = await self.call(
            "propose_placement",
            data.STEWARD,
            items=grouped,
            new_epic={"title": data.GROUPED.epic_title, "body": data.GROUPED.epic_body},
            reason=data.GROUPED.reason,
        )
        self.grouped_proposal = made["id"]
        # By hand, before the script: two stories under the epic nearest to the first. Work in a
        # waiting proposal is left alone by the script, so it does not make a proposal of its own
        # for them.
        asked = await self.call(
            "suggest_parent", data.STEWARD, node=self.loose[data.CHECKED_STORIES[0]], limit=1
        )
        if not asked["suggestions"]:
            raise_stopped("The server has no parent to suggest for the first of CHECKED_STORIES.")
        by_hand = await self.call(
            "propose_placement",
            data.STEWARD,
            items=[self.loose[title] for title in data.CHECKED_STORIES],
            parent=asked["suggestions"][0]["ref"],
            reason=data.CHECKED_REASON,
        )
        self.checked_proposal = by_hand["id"]
        # About two tasks that are not in the Inbox: a person will accept it in part.
        in_part = await self.call(
            "propose_placement",
            data.STEWARD,
            items=[self.demo.ref(key) for key in data.PARTIAL_ITEMS],
            parent=self.demo.ref(data.PARTIAL_PARENT),
            reason=data.PARTIAL_REASON,
        )
        self.partial_proposal = in_part["id"]
        key = self.demo.keys[data.STEWARD]
        for extra, what in (("--dry-run", "would"), ("", "did")):
            arguments = [sys.executable, str(ROOT / "scripts" / "steward.py"), self.demo.url]
            if extra:
                arguments.append(extra)
            ran = await asyncio.to_thread(
                subprocess.run,
                arguments,
                cwd=ROOT,
                env={**os.environ, "VRUM_AGENT_KEY": key},
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
            )
            if ran.returncode != 0:
                raise_stopped(f"scripts/steward.py failed: {ran.stderr or ran.stdout}")
            last = ran.stdout.strip().splitlines()[-1]
            said(f"    the steward {what}: {last}")
        waiting = await self.call("list_placements", data.STEWARD, status="proposed", limit=50)
        by_the_script = [p for p in waiting["items"] if p["id"] not in self.by_hand()]
        if len(by_the_script) < FEWEST_SURE:
            raise Stopped(
                f"The model is sure about only {len(by_the_script)} of the loose stories, and the "
                f"demo needs at least {FEWEST_SURE} so that one can be accepted and one rejected. "
                "The model of meaning does not agree with how the loose stories in "
                "tests/demo_data.py (LOOSE) are worded: reword them."
            )

    async def people_decide(self) -> None:
        """The planner accepts the first proposal the steward's script made (the story moves, and
        the history says she moved it), and the owner rejects the second, with a reason. The planner
        also accepts the proposal about two tasks IN PART (M53): the first moves, the second stays
        where it is and the record says she left it out."""
        waiting = await self.call("list_placements", data.STEWARD, status="proposed", limit=50)
        made = sorted(
            (p for p in waiting["items"] if p["id"] not in self.by_hand()), key=lambda p: p["id"]
        )
        first, second = made[0], made[1]
        done = await self.demo.as_person(
            "decide_placement", {"placement": first["id"], "accepted": True}, data.PLANNER
        )
        no = await self.demo.as_person(
            "decide_placement",
            {"placement": second["id"], "accepted": False, "reason": data.SUGGESTION_REJECTED},
            data.OWNER,
        )
        wanted = [item["ref"] for item in first["items"]]
        if done["state"] != "accepted" or done["applied"] != wanted or done["skipped"]:
            raise_stopped(f"The accepted proposal did not move what it proposed: {done}")
        if no["state"] != "rejected" or no["decision_reason"] != data.SUGGESTION_REJECTED:
            raise_stopped(f"The rejected proposal was not recorded as a rejection: {no}")
        moves = [self.demo.ref(key) for key in data.PARTIAL_ITEMS if key != data.PARTIAL_LEFT_IN]
        stays = self.demo.ref(data.PARTIAL_LEFT_IN)
        in_part = await self.demo.as_person(
            "decide_placement",
            {"placement": self.partial_proposal, "accepted": True, "items": moves},
            data.PLANNER,
        )
        if (
            in_part["state"] != "accepted"
            or in_part["applied"] != moves
            or in_part["skipped"] != [{"ref": stays, "why": f"{data.PLANNER} left it out"}]
        ):
            raise_stopped(
                f"The proposal accepted in part did not move only what was named: {in_part}"
            )
        self.accepted, self.rejected, self.partial = done, no, in_part
        self.waiting_ids = [p["id"] for p in made[2:]] + sorted(
            self.by_hand() - {self.partial_proposal}
        )

    # -- gates ---------------------------------------------------------------------------------

    async def gate_work(self) -> None:
        """Agents hold work (a timer, and approvals that ask a person), and the people who have an
        account answer: one yes, which opens its gate, and one no, which leaves it refused."""
        for gate in data.GATES:
            ref = self.demo.ref(gate.key)
            if gate.kind == "timer":
                assert gate.days is not None
                opens = (self.demo.today + datetime.timedelta(days=gate.days)).isoformat()
                made = await self.call(
                    "add_gate", gate.actor, node=ref, kind="timer", not_before=opens
                )
            else:
                made = await self.call(
                    "add_gate", gate.actor, node=ref, kind="approval", question=gate.question
                )
            if gate.answer is not None:
                words: dict[str, Any] = {"ask": made["ask"], "answer": gate.answer}
                if gate.comment:
                    words["comment"] = gate.comment
                await self.demo.as_person("answer_ask", words, gate.by)
            self.gate_ids[gate.key] = made

    # -- plans and templates ----------------------------------------------------------------------

    async def plans_and_templates(self) -> None:
        """The planning agent saves two templates, the operations agent makes a plan from one (a
        draft, so a person has to approve it) and the planner approves it, the planning agent
        drafts the winter plan, which waits, and the operations agent drafts the small spring
        plan."""
        for name, template in data.TEMPLATES.items():
            await self.call("save_plan_template", data.RECORDER, name=name, template=template)
        values = {**data.LAUNCH_VALUES, "epic": self.demo.ref(data.LAUNCH_EPIC)}
        launched = await self.call(
            "create_plan",
            data.LAUNCH_BY,
            template=data.STATION_LAUNCH,
            values=values,
            draft=True,
            title=data.LAUNCH_TITLE,
        )
        self.launch = launched["plan"]
        await self.demo.as_person(
            "approve_plan", {"plan": self.launch, "approved": True}, data.PLANNER
        )
        winter = await self.call(
            "create_plan",
            data.RECORDER,
            **winter_plan(self.demo.today, self.demo.ref),
            draft=True,
            title=data.WINTER_TITLE,
        )
        self.winter = winter["plan"]
        self.winter_refs = {made["key"]: made["ref"] for made in winter["created"]}
        spring = await self.call(
            "create_plan",
            data.SPRING_BY,
            **spring_plan(self.demo.ref),
            draft=True,
            title=data.SPRING_TITLE,
        )
        self.spring = spring["plan"]
        self.spring_refs = {made["key"]: made["ref"] for made in spring["created"]}

    async def a_conversation_about_plans(self) -> None:
        """The owner reads the drafted winter plan and asks for changes, with a summary, comments
        on the title of a story, on the details of a task and on a task she wants dropped, and
        four items approved as they are. The planning agent reads what was said and sends revision
        2 of the same plan, which changes the two parts, takes out the task and leaves the
        approved items alone, and says so. The owner also asks for changes to the spring plan,
        whose agent has not revised it yet: it is the agent's turn, so it is not a row on Needs a
        person."""
        for plan, refs, summary, feedback, approved in (
            (
                self.winter,
                self.winter_refs,
                data.WINTER_FEEDBACK_SUMMARY,
                data.WINTER_FEEDBACK,
                data.WINTER_APPROVED,
            ),
            (
                self.spring,
                self.spring_refs,
                data.SPRING_FEEDBACK_SUMMARY,
                data.SPRING_FEEDBACK,
                (),
            ),
        ):
            asked = await self.demo.as_person(
                "request_plan_changes",
                {
                    "plan": plan,
                    "revision": 1,
                    "summary": summary,
                    "comments": comments_on(refs, feedback),
                    "approved": [refs[key] for key in approved],
                },
                data.OWNER,
            )
            if asked["state"] != "changes_requested":
                raise_stopped(f"Asking for changes to plan {plan} did not hold it: {asked}.")
        # The agent learns what was asked from the plan itself, and answers it.
        read = (await self.call("list_plans", data.RECORDER, plan=self.winter))["items"][0]
        if len(read["feedback"]) != 1 or read["state"] != "changes_requested":
            raise_stopped(f"The winter plan does not carry the feedback it was given: {read}.")
        revised = await self.call(
            "revise_plan",
            data.RECORDER,
            plan=self.winter,
            items=winter_revision(self.demo.today, self.demo.ref, self.winter_refs),
            note=data.WINTER_REVISION_NOTE,
        )
        gone = [self.winter_refs[key] for key in data.WINTER_REMOVED]
        if revised["plan"] != self.winter or revised["revision"] != 2 or revised["removed"] != gone:
            raise_stopped(f"The winter plan was not revised as described: {revised}.")

    async def check_the_winter_plan(self) -> None:
        """The drafted plan, read whole as a person reviews it (list_plans with the plan's number):
        every item with its size, details, links and criteria, the one task left unsized on
        purpose, and as context the epic it hangs under and the story it relates to."""
        whole = (await self.call("list_plans", plan=self.winter))["items"][0]
        tree = whole["tree"]
        under = self.demo.ref(data.WINTER_UNDER)
        context = [(found["ref"], found["why"]) for found in whole["context"]]
        reaches = {
            self.demo.ref(target) for _, target, kind in data.WINTER_LINKS if kind == "relates_to"
        }
        unsized = [item["title"] for item in tree if item["size"] is None]
        if len(tree) != len(data.WINTER_NOW) or whole["epics"] != 0:
            raise_stopped(f"The winter plan reads {len(tree)} items, not {len(data.WINTER_NOW)}.")
        if context != [(under, "parent"), *((ref, "linked") for ref in sorted(reaches))]:
            raise_stopped(f"The winter plan does not hang under {under} as described: {context}.")
        if len(unsized) != 1 or not all(item["body"] for item in tree if item["kind"] == "story"):
            raise_stopped(
                f"The winter plan should leave one item unsized and say what each story is, "
                f"and it leaves out {unsized}."
            )
        if sum(len(item["criteria"]) for item in tree) != len(data.WINTER_CRITERIA):
            raise_stopped("The winter plan does not carry the acceptance criteria described.")

    async def check_the_conversation(self) -> str:
        """The two plans that have a conversation, read whole as a person and an agent read them.
        The winter plan is at revision 2 and waits for a person: what the owner said is kept with
        the words each comment was about, the revision changes the two parts asked for and
        nothing else, revision 1 still reads as it was drafted, and nothing was changed outside a
        revision. The spring plan waits for its agent at revision 1, with its feedback, and is
        held. Neither of them is a row on Needs a person except the winter one, which says it is
        at revision 2."""
        winter = (await self.call("list_plans", plan=self.winter))["items"][0]
        first = (await self.call("list_plans", plan=self.winter, revision=1))["items"][0]
        spring = (await self.call("list_plans", plan=self.spring))["items"][0]
        refs = self.winter_refs
        drafted = {str(item["key"]): item for item in data.WINTER_ITEMS}
        if (winter["state"], winter["revision"], winter["revised_by"]) != (
            "proposed",
            2,
            data.RECORDER,
        ):
            raise_stopped(f"The winter plan should wait for a person at revision 2: {winter}.")
        if [r["revision"] for r in winter["revisions"]] != [1, 2]:
            raise_stopped(f"The winter plan should have two revisions: {winter['revisions']}.")
        if winter["revisions"][1]["note"] != data.WINTER_REVISION_NOTE:
            raise_stopped("Revision 2 of the winter plan does not carry the agent's note.")
        (given,) = winter["feedback"]
        words = [(c["item"], c["field"], c["text"]) for c in given["comments"]]
        asked = [(refs[k], part, text) for k, part, text in data.WINTER_FEEDBACK]
        quoted = [c["quoted"] for c in given["comments"]]
        was = [
            drafted["studded"]["title"],
            drafted["fit-tyres"]["body"],
            drafted["chains"]["title"],
        ]
        if (given["by"], given["by_kind"], given["revision"]) != (data.OWNER, "person", 1):
            raise_stopped(f"The winter plan's feedback is not the owner's on revision 1: {given}.")
        if given["summary"] != data.WINTER_FEEDBACK_SUMMARY or words != asked or quoted != was:
            raise_stopped(f"The winter plan's feedback is not as described: {given}.")
        if given["approved"] != in_order(refs[key] for key in data.WINTER_APPROVED):
            raise_stopped(f"The winter plan's feedback does not carry what was approved: {given}.")
        if winter["revisions"][0]["feedback"] != given["id"]:
            raise_stopped("Revision 1 of the winter plan does not point at its feedback.")
        changes = [(c["ref"], c["change"], c["fields"]) for c in winter["changes"]]
        wanted = [
            (refs["studded"], "changed", ["title"]),
            (refs["fit-tyres"], "changed", ["body"]),
            (refs["chains"], "removed", []),
        ]
        if sorted(changes) != sorted(wanted):
            raise_stopped(
                f"Revision 2 should change two parts and take out a task, not: {changes}."
            )
        before = {c["ref"]: c["before"] for c in winter["changes"]}
        if before != {
            refs["studded"]: {"title": was[0]},
            refs["fit-tyres"]: {"body": was[1]},
            refs["chains"]: {},
        }:
            raise_stopped(f"Revision 2 does not say what the two parts were: {before}.")
        old = {item["ref"]: item for item in first["tree"]}
        if old[refs["studded"]]["title"] != was[0] or old[refs["fit-tyres"]]["body"] != was[1]:
            raise_stopped("Revision 1 of the winter plan no longer reads as it was drafted.")
        if len(first["tree"]) != len(data.WINTER_ITEMS) or refs["chains"] not in old:
            raise_stopped("Revision 1 of the winter plan should still hold the task taken out.")
        removed = await self.call("get_node", node=refs["chains"])
        if removed["status"] != "dropped" or removed["held_by"]:
            raise_stopped(f"The task revision 2 took out should be dropped and free: {removed}.")
        if winter["drift"] != [] or winter["checks"] is None:
            raise_stopped(f"The winter plan was changed outside a revision: {winter['drift']}.")
        blamed = self.check_the_blame(winter, first, given)
        (waiting,) = spring["feedback"]
        spring_words = [(c["item"], c["field"], c["text"]) for c in waiting["comments"]]
        spring_asked = [(self.spring_refs[k], p, t) for k, p, t in data.SPRING_FEEDBACK]
        written = {str(item["key"]): item for item in data.SPRING_ITEMS}
        spring_quoted = [written["spring-check"]["body"], written["spring-replace"]["size"]]
        if (spring["state"], spring["revision"], spring["checks"]) != (
            "changes_requested",
            1,
            None,
        ):
            raise_stopped(f"The spring plan should wait for its agent at revision 1: {spring}.")
        if spring_words != spring_asked or [c["quoted"] for c in waiting["comments"]] != (
            spring_quoted
        ):
            raise_stopped(f"The spring plan's feedback is not as described: {waiting}.")
        held = [
            (await self.call("get_node", node=ref))["held_by"] for ref in self.spring_refs.values()
        ]
        if held != [[f"plan:{self.spring}"]] * len(data.SPRING_ITEMS):
            raise_stopped(f"The spring plan's items are not held by the plan: {held}.")
        waiting_for_agent = await self.call("list_plans", status="changes_requested")
        if [p["id"] for p in waiting_for_agent["items"]] != [self.spring]:
            raise_stopped("Only the spring plan should be waiting for its agent.")
        return (
            f"the winter plan is at revision 2 after {len(given['comments'])} comments and "
            f"{len(given['approved'])} approved items, {len(changes)} items changed or taken out "
            f"({blamed}); the spring plan waits for its agent at revision 1 "
            f"after {len(waiting['comments'])} comments"
        )

    def check_the_blame(
        self, winter: dict[str, Any], first: dict[str, Any], given: dict[str, Any]
    ) -> str:
        """What the plan says about who changed each line and what the owner said of it (M53,
        `blame` of list_plans with the plan's number). At revision 2: the items the owner approved
        and revision 2 did not touch read approved by her, on revision 1; the two she commented on
        that revision 2 changed read changed; the one the revision took out reads removed by
        revision 2 with the agent's note and no word of hers; the one nobody said anything about
        reads none. At revision 1 the same lines read as they were: added by the agent, the
        approved ones approved and the three she commented on asked."""
        refs = self.winter_refs
        approved = {refs[key] for key in data.WINTER_APPROVED}
        commented = {item["item"] for item in given["comments"]}
        changed = {refs[key] for key in data.WINTER_REVISED}
        gone = {refs[key] for key in data.WINTER_REMOVED}
        now = {line["ref"]: line for line in winter["blame"]}
        was = {line["ref"]: line for line in first["blame"]}
        everything = {refs[str(item["key"])] for item in data.WINTER_ITEMS}
        if set(now) != everything or set(was) != everything:
            raise_stopped(f"The blame does not name each line of both revisions: {sorted(now)}.")
        for ref, line in now.items():
            if ref in gone:
                how, revision, note = "removed", 2, data.WINTER_REVISION_NOTE
            elif ref in changed:
                how, revision, note = "changed", 2, data.WINTER_REVISION_NOTE
            else:
                how, revision, note = "added", 1, None
            review = line["review"]
            said_of: tuple[Any, ...]
            if ref in approved:
                said_of = ("approved", 1, data.OWNER, given["id"])
            elif ref in changed:
                said_of = ("changed", 1, data.OWNER, given["id"])
            else:
                said_of = ("none", None, None, None)
            if (line["change"], line["revision"], line["by"], line["by_kind"]) != (
                how,
                revision,
                data.RECORDER,
                "agent",
            ) or (review["state"], review["revision"], review["by"], review["feedback"]) != said_of:
                raise_stopped(f"The blame of {ref} at revision 2 is not as described: {line}.")
            if revision == 2 and line["note"] != note:
                raise_stopped(f"The blame of {ref} does not carry the note of its revision.")
        for ref, line in was.items():
            review = line["review"]
            word = "approved" if ref in approved else "asked" if ref in commented else "none"
            if (line["change"], line["revision"]) != ("added", 1) or review["state"] != word:
                raise_stopped(f"The blame of {ref} at revision 1 is not as described: {line}.")
        return (
            f"{len(approved)} approved and unchanged, {len(changed)} changed since she looked, "
            f"{len(gone)} removed"
        )

    async def read_the_winter_checks(self) -> str:
        """What the review checks say of the drafted plan (list_plans with the plan's number): no
        circle, the one task left unsized and nothing else, and one story that repeats the
        existing "Battery swap schedule" with a similarity that reads as "may repeat". Stops with a
        plain message when meaning search cannot be used or the wording needs changing."""
        whole = (await self.call("list_plans", plan=self.winter))["items"][0]
        checks = {one["kind"]: one for one in whole["checks"] or []}
        if list(checks) != ["circle", "unsized", "duplicate"]:
            raise_stopped(f"The winter plan has these checks, not the three described: {checks}.")
        tree = whole["tree"]
        unsized = [item["ref"] for item in tree if item["size"] is None]
        repeats = [item["ref"] for item in tree if item["title"] == data.WINTER_REPEATS]
        duplicate = checks["duplicate"]
        if duplicate["state"] == "unavailable":
            raise_stopped(
                "The check for work that already exists could not be made, and the demo shows it "
                f"on the real embedding service. It said: {duplicate['words']}"
            )
        if checks["circle"]["state"] != "ok":
            raise_stopped(f"The winter plan makes a circle of waiting: {checks['circle']}.")
        found = [(f["items"], f["related"]) for f in checks["unsized"]["findings"]]
        if checks["unsized"]["state"] != "warning" or found != [([ref], []) for ref in unsized]:
            raise_stopped(f"The check for work with no size does not name {unsized}: {found}.")
        findings = duplicate["findings"]
        battery = self.demo.ref("battery")
        if (
            duplicate["state"] != "warning"
            or [f["items"] for f in findings] != [repeats]
            or findings[0]["related"][0]["ref"] != battery
        ):
            raise_stopped(
                f"The winter story {data.WINTER_REPEATS!r} should read as repeating {battery}, and "
                "nothing else in the plan should look like work that exists, but the server says: "
                f"{duplicate['words']} The model of meaning does not agree with how the stories "
                "in tests/demo_data.py (WINTER_ITEMS) are worded: reword them."
            )
        similarity = findings[0]["related"][0]["similarity"]
        if not 0.55 <= similarity < 0.95:
            raise_stopped(f"The repeated story reads {similarity:.2f} like {battery}: reword it.")
        return (
            f"circle {checks['circle']['state']}, unsized {checks['unsized']['state']} "
            f"({len(unsized)}), duplicate {duplicate['state']} ({len(findings)}, "
            f"{round(similarity * 100)}% like S-{battery.split(':')[1]} Battery swap schedule)"
        )

    async def read_the_moves(self) -> str:
        """What the review says of the proposals that wait (list_placements, status proposed):
        every one says whether it is routine; at least one is, with every score ahead and nothing
        to weigh; the one the steward made by hand is not, with a score well below the other's and
        hardly ahead of the next epic; and the one that makes a new epic has no scores, said so."""
        waiting = (await self.call("list_placements", status="proposed", limit=50))["items"]
        if any(p["routine"] is None or p["routine_why"] is None for p in waiting):
            raise_stopped("A waiting proposal does not say whether it is routine.")
        routine = [p for p in waiting if p["routine"]]
        if not routine:
            raise_stopped(
                "No waiting proposal is routine, and the demo needs one: the model is not clearly "
                "sure about the loose stories in tests/demo_data.py (LOOSE): reword them."
            )
        for one in routine:
            if any(s["warning"] or s["note"] or s["rank"] != 1 for s in one["scores"]):
                raise_stopped(f"Proposal {one['id']} is routine with something to weigh.")
        by_hand = next(p for p in waiting if p["id"] == self.checked_proposal)
        weak, strong = by_hand["scores"]
        if by_hand["routine"] or weak["similarity"] is None or strong["similarity"] is None:
            raise_stopped(
                f"The steward's proposal for a second look is not as described: {by_hand}."
            )
        if strong["similarity"] - weak["similarity"] < 0.05 or weak["sure"]:
            raise_stopped(
                "The deposit story should score well below the credit story and not be clearly "
                f"ahead of the next epic ({weak['similarity']:.2f}, {strong['similarity']:.2f}, "
                f"{by_hand['routine_why']}): reword them in tests/demo_data.py (LOOSE)."
            )
        grouped = next(p for p in waiting if p["id"] == self.grouped_proposal)
        if grouped["routine"] or grouped["scores"] or not grouped["scores_note"]:
            raise_stopped(f"The proposal that makes a new epic should have no scores: {grouped}.")
        return (
            f"{len(routine)} of {len(waiting)} waiting moves are routine; the deposit story "
            f"scores {round(weak['similarity'] * 100)}% and the credit story "
            f"{round(strong['similarity'] * 100)}%"
        )

    # -- what every row of the list says about itself (M52) --------------------------------------

    async def epic_of(self, ref: str) -> str:
        """The epic a story or task is under, by climbing the parents the server reports."""
        node = await self.call("get_node", node=ref)
        while node["kind"] != "epic":
            node = await self.call("get_node", node=node["parent"])
        return str(node["ref"])

    async def read_the_standard_review(self, needs: dict[str, Any]) -> str:
        """The level, the decision and the topic the server gives each row of what needs a person,
        which the board only regroups by: every row says what it is (a plan, a move of stories or
        of tasks, a question, a check, notes, a refused gate or a claim that ran out), says what a
        person is asked to do, and has a topic. A topic is one thing whatever row says it (the
        same label); one named by an epic is that epic, by its reference and title (meaning can
        name a cluster by the epic most of it is under, while a row that is only under that epic
        says "epic"), and a row that is only under an epic is under it; one that came from
        meaning alone has a number and is named by one of its rows' work. Which rows meaning puts
        together depends on the real model, so only these relations are held, never the labels."""
        rows = [item for group in needs["groups"] for item in group["items"]]
        for item in rows:
            kind = item["kind"]
            decision = {"ask": "answer", "gate": "look_into", "lapse": "look_into"}.get(
                kind, "approve"
            )
            if kind in ("plan", "note"):
                level = kind
            elif kind == "placement":
                moved = [one["ref"] for one in item["placement"]["items"]]
                level = "story" if any(ref.startswith("story:") for ref in moved) else "task"
            else:
                level = str(item["node"]).split(":")[0]
            if (item["level"], item["decision"]) != (level, decision):
                raise_stopped(
                    f"The {kind} row about {item['node']} says {item['level']} and "
                    f"{item['decision']}, not {level} and {decision}."
                )
        seen: dict[str, str] = {}
        bases: dict[str, set[str]] = {}
        for item in rows:
            topic = item["topic"]
            if topic is None:
                raise_stopped(f"The {item['kind']} row about {item['node']} has no topic.")
            if seen.setdefault(topic["id"], topic["label"]) != topic["label"]:
                raise_stopped(f"The topic {topic['id']} is called two things.")
            bases.setdefault(topic["id"], set()).add(topic["basis"])
        by_meaning = 0
        for topic_id, label in seen.items():
            if topic_id.startswith("epic:"):
                epic = await self.call("get_node", node=topic_id)
                if epic["title"] != label:
                    raise_stopped(f"The topic {topic_id} is called {label}, not {epic['title']}.")
            elif bases[topic_id] != {"meaning"} or not topic_id.startswith("topic:"):
                raise_stopped(f"The topic {topic_id} is not an epic and not one of meaning.")
            by_meaning += "meaning" in bases[topic_id]
        for item in rows:
            topic = item["topic"]
            if topic["basis"] != "epic":
                continue
            if item["placement"] is not None:
                anchor = item["placement"]["parent"] or item["placement"]["items"][0]["ref"]
            elif item["plan"] is not None:
                anchor = item["plan"]["items"][0]
            else:
                anchor = item["node"]
            if await self.epic_of(anchor) != topic["id"]:
                raise_stopped(f"The row about {item['node']} is under another epic than its topic.")
        by_level: dict[str, int] = {}
        for item in rows:
            by_level[item["level"]] = by_level.get(item["level"], 0) + 1
        levels = ", ".join(f"{count} {level}" for level, count in sorted(by_level.items()))
        return (
            f"{len(rows)} rows by level ({levels}) and by topic ({len(seen)} topics, "
            f"{by_meaning} put together by meaning)"
        )

    # -- what the server says it all came to ----------------------------------------------------

    async def check(self) -> None:
        """Everything above, as the server reports it: the Inbox and what is left in it, the
        proposals and how each ended, the gates and the work they hold, the plans and the
        templates, the links that say where work was found, the rows of the Needs a person list by
        kind, and the viewer, who is refused deciding."""
        in_inbox = await self.call("list_nodes", kind="story", parent=self.inbox, limit=50)
        moved_to = self.accepted["parent"]
        left = len(data.LOOSE) - len(self.accepted["applied"])
        proposals = await self.call("list_placements", limit=50)
        states = sorted(p["state"] for p in proposals["items"])
        waiting_moves = states.count("proposed")
        if in_inbox["total"] != left or moved_to is None:
            raise_stopped(
                f"The Inbox holds {in_inbox['total']} stories, not the {left} that are left."
            )
        if states.count("accepted") != 2 or states.count("rejected") != 1 or waiting_moves < 1:
            raise_stopped(
                "The proposals are not two accepted (one in part), one rejected and some waiting: "
                f"{states}."
            )
        moves = [
            e
            for e in (await self.call("node_history", node=self.accepted["applied"][0]))["items"]
            if e["action"] == "move"
        ]
        if not moves or (moves[-1]["actor"], moves[-1]["actor_kind"]) != (data.PLANNER, "person"):
            raise_stopped("The accepted move is not recorded as the planner's own.")
        (moved,) = self.partial["applied"]
        (stayed,) = [one["ref"] for one in self.partial["skipped"]]
        under = self.demo.ref(data.PARTIAL_PARENT)
        parents = [(await self.call("get_node", node=ref))["parent"] for ref in (moved, stayed)]
        left_alone = [
            e
            for e in (await self.call("node_history", node=stayed))["items"]
            if e["action"] == "move"
        ]
        if parents[0] != under or parents[1] == under or left_alone:
            raise_stopped(f"The proposal accepted in part moved the wrong items: {parents}.")

        held: dict[str, list[str]] = {}
        for gate in data.GATES:
            node = await self.call("get_node", node=self.demo.ref(gate.key))
            held[gate.key] = node["held_by"]
            state = node["gates"][0]["state"]
            wanted = {None: "waiting", "yes": "open", "no": "refused"}[gate.answer]
            if state != wanted:
                raise_stopped(f"The gate on {gate.key} is {state}, not {wanted}.")
            if (wanted == "open") != (not node["held_by"]):
                raise_stopped(f"The work behind the gate on {gate.key} is held or free wrongly.")
        held_now = sum(1 for by in held.values() if by)
        held_all = (await self.call("list_nodes", query="is:held", limit=200))["total"]
        drafted = len(data.WINTER_NOW) + len(data.SPRING_ITEMS)
        if held_all != held_now + drafted:
            raise_stopped(
                f"{held_all} items are held, not the {held_now} gated and {drafted} drafted."
            )

        plans = await self.call("list_plans", limit=50)
        by_state = {p["title"]: p["state"] for p in plans["items"]}
        if by_state != {
            data.WINTER_TITLE: "proposed",
            data.LAUNCH_TITLE: "approved",
            data.SPRING_TITLE: "changes_requested",
        }:
            raise_stopped(f"The plans are not as described: {by_state}.")
        await self.check_the_winter_plan()
        conversation = await self.check_the_conversation()
        templates = await self.call("list_plan_templates")
        if sorted(t["name"] for t in templates["items"]) != sorted(data.TEMPLATES):
            raise_stopped("The plan templates are not the two described.")
        for link in data.LINKS:
            if link.kind != data.DISCOVERED:
                continue
            source = await self.call("get_node", node=self.demo.ref(link.source))
            seen = await self.call("list_links", node=source["ref"], limit=50)
            if not any(
                found["link_type"] == data.DISCOVERED and found["source"] == source["ref"]
                for found in seen["items"]
            ):
                raise_stopped(f"The link that says where {link.source} was found is missing.")

        needs = await self.call("needs_a_person")
        rows: dict[str, int] = {}
        for group in needs["groups"]:
            for item in group["items"]:
                rows[item["kind"]] = rows.get(item["kind"], 0) + 1
        titles = {g["title"] for g in needs["groups"] if g["story"] is None}
        wanted_titles = {"Plans waiting for approval", "Suggested moves"}
        if (rows.get("plan"), rows.get("placement"), rows.get("gate")) != (1, waiting_moves, 1):
            raise_stopped(f"The Needs a person list does not hold the new rows: {rows}.")
        plan_row = next(i for g in needs["groups"] for i in g["items"] if i["kind"] == "plan")
        if plan_row["plan"]["id"] != self.winter or plan_row["plan"]["revision"] != 2:
            raise_stopped(
                f"The plan on the Needs a person list is not the winter plan: {plan_row}."
            )
        if not wanted_titles <= titles:
            raise_stopped(f"The Needs a person list has no groups called {sorted(wanted_titles)}.")
        path = f"/board/api/projects/{self.demo.project_id}/tool"
        steward = self.demo.clients[data.STEWARD]
        offered = {tool.name for tool in (await steward.list_tools()).tools}
        by_the_steward = await steward.call_tool(
            "decide_placement", {"placement": self.waiting_ids[0], "accepted": True}
        )
        if offered != set(data.STEWARD_TOOLS) or not by_the_steward.is_error:
            raise_stopped("The steward's key may use more than the four tools it was given.")
        viewer = self.demo.people[data.VIEWER]
        tried = await asyncio.to_thread(
            viewer.call,
            "POST",
            f"{path}/approve_plan",
            {"plan": self.winter, "approved": True},
        )
        still = await self.call("list_plans", status="proposed")
        if tried.status not in (400, 403) or still["total"] != 1:
            raise_stopped(f"The viewer should be refused approving a plan ({tried.status}).")
        verdict = (
            f"{await self.read_the_moves()}; the winter plan: {await self.read_the_winter_checks()}"
        )
        said(
            f"    {len(data.LOOSE)} loose stories filed, 1 proposal accepted and 1 rejected, "
            f"1 more accepted in part (1 moved, 1 left out), "
            f"{waiting_moves} waiting; {len(data.GATES)} gates, 3 plans, "
            f"{len(data.TEMPLATES)} plan templates; {sum(rows.values())} things need a person "
            f"({', '.join(f'{count} {kind}' for kind, count in sorted(rows.items()))})"
        )
        said(f"    review: {verdict}")
        said(f"    conversation: {conversation}; {held_all} items are held")
        said(f"    standard review: {await self.read_the_standard_review(needs)}")


def raise_stopped(message: str) -> NoReturn:
    raise Stopped(message)
