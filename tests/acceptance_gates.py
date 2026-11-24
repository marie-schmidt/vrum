"""Chapter of the acceptance scenario for gates, plan approval and plan templates (M47), and for the
link that says where work was found (M44), told through a real MCP client and the board's own calls
as a person. A gate holds work until a time or a person's yes; a drafted plan is held until a
person approves it as one decision, or rejects it and all of it is dropped with their reason; a
plan template is saved data that makes the plan writing it out would; and `discovered_from` never
makes anything wait. Each part works in a project of its own."""

import asyncio
import copy
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp import Client

from tests.acceptance_scenario import chapter, clip, proof
from tests.helpers import REPO_ROOT, connect, export_everything, make_tree, ok, refused
from tests.test_checks_integration import as_person
from tests.test_gates_plans_integration import PLAN, RELEASE, far, shape, soon
from tests.world import Person, Project, join_project, make_key, make_project, register


def words(refusal: str) -> str:
    return refusal.split(": ", 1)[1] if refusal.startswith("Error executing tool") else refusal


def person(project: Project, who: Person, tool: str, **arguments: Any) -> Any:
    """What the board does when a person presses a button: the tool, as that person."""
    return as_person(who, project, tool, **arguments)


async def a_task(client: Client) -> dict[str, Any]:
    _, _, task = await make_tree(client)
    return task


async def timer_gates() -> None:
    """A timer gate holds work until its time and opens by itself; the work is not offered while it
    is held, and a past, odd or far time is refused."""
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        ready_before = (await ok(agent, "get_node", node=task["ref"]))["status"]
        gate = await ok(agent, "add_gate", node=task["ref"], kind="timer", not_before=soon(10))
        held = await ok(agent, "get_node", node=task["ref"])
        listed = (await ok(agent, "ready_work"))["items"]
        peeked = await ok(agent, "next_work", peek=True)
        claim = words(await refused(agent, "claim_node", node=task["ref"]))
        found = (await ok(agent, "list_nodes", query="is:held"))["total"]
        lift = words(await refused(agent, "remove_gate", gate=gate["id"]))
        said = [
            words(await refused(agent, "add_gate", node=task["ref"], kind="timer", not_before=bad))
            for bad in ("2001-01-01", "next week", "2999-01-01")
        ]
        proof(
            "M47",
            "a timer gate holds a story or task until a time: the work is not ready and not in "
            "ready_work, next_work does not give it and says it is held by a gate, claim_node "
            "refuses it in words that point to get_node, the filter is:held finds it, an agent "
            "cannot lift the gate, and a time in the past, in no form it reads or more than three "
            "years ahead is refused with nothing changed",
            ready_before == "ready"
            and gate["state"] == "waiting"
            and gate["words"].startswith("Waits until ")
            and held["held_by"] == [f"gate:{gate['id']}"]
            and held["status"] == "new"
            and all(item["ref"] != task["ref"] for item in listed)
            and peeked["found"] is False
            and "held by a gate" in peeked["message"]
            and "is held (gate:" in claim
            and "get_node shows what it waits for" in claim
            and found == 1
            and "not allowed to use" in lift
            and "already passed" in said[0]
            and "YYYY-MM-DD" in said[1]
            and "three years" in said[2]
            and all("Nothing was changed" in message for message in said),
            clip(gate["words"], 70),
        )
        await asyncio.sleep(11)
        opened = await ok(agent, "get_node", node=task["ref"])
        taken = await ok(agent, "next_work", within_node=task["ref"])
        proof(
            "M47",
            "a timer gate opens by itself when its time comes: the work is held no longer, ready "
            "again, the gate says when it opened, and next_work gives it",
            opened["held_by"] == []
            and opened["status"] == "ready"
            and opened["gates"][0]["state"] == "open"
            and opened["gates"][0]["words"].startswith("Opened at")
            and taken["found"]
            and taken["work"]["ref"] == task["ref"],
            clip(opened["gates"][0]["words"], 60),
        )


async def approval_gates() -> None:
    """An approval gate raises a question for a person; only a person's yes opens it, a no leaves
    it refused and still holding, and a person can lift it."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        gate = await ok(
            agent, "add_gate", node=task["ref"], kind="approval", question="May we touch billing?"
        )
        needs = await ok(agent, "needs_a_person")
        (row,) = [i for g in needs["groups"] for i in g["items"] if i["kind"] == "ask"]
        by_agent = words(await refused(agent, "answer_ask", ask=gate["ask"], answer="yes"))
        no = person(
            project, owner, "answer_ask", ask=gate["ask"], answer="no", comment="Not this week"
        )
        refused_gate = (await ok(agent, "get_node", node=task["ref"]))["gates"][0]
        still_held = (await ok(agent, "get_node", node=task["ref"]))["held_by"]
        needs_after = await ok(agent, "needs_a_person")
        lifted = person(project, owner, "remove_gate", gate=gate["id"], reason="Asked again")
        free = (await ok(agent, "get_node", node=task["ref"]))["status"]
        again = await ok(
            agent, "add_gate", node=task["ref"], kind="approval", question="Billing again?"
        )
        yes = person(project, owner, "answer_ask", ask=again["ask"], answer="Yes")
        opened = await ok(agent, "get_node", node=task["ref"])
        twice = person(project, owner, "remove_gate", gate=gate["id"])
        proof(
            "M47",
            "an approval gate is a question to a person (a row on the Needs a person list and an "
            "ask with the effect gates) that an agent cannot answer; a person's no leaves the "
            "gate refused with the work still held and a row of its own on the list; a person "
            "lifts it and the work is ready; a new gate opens only on a person's yes, who is "
            "named; and a gate can be lifted only once",
            gate["state"] == "waiting"
            and row["ask"]["effect"] == "gates"
            and row["ask"]["choices"] == ["yes", "no"]
            and row["reason"] == "Approval needed before it can start: May we touch billing?"
            and "not allowed to use" in by_agent
            and no.status == 200
            and refused_gate["state"] == "refused"
            and "refused by" in refused_gate["words"]
            and still_held == [f"gate:{gate['id']}"]
            and [i["kind"] for g in needs_after["groups"] for i in g["items"]] == ["gate"]
            and lifted.status == 200
            and lifted.body["state"] == "removed"
            and free == "ready"
            and yes.status == 200
            and opened["held_by"] == []
            and opened["gates"][1]["words"].startswith("Approved by ")
            and twice.status == 400
            and "already lifted" in twice.body["message"],
            f"Q-{gate['ask']}: {clip(refused_gate['words'], 60)}",
        )

        other = await a_task(agent)
        waiting = await ok(agent, "add_gate", node=other["ref"], kind="approval", question="Go?")
        open_before = (await ok(agent, "list_asks", status="open"))["total"]
        lift = person(project, owner, "remove_gate", gate=waiting["id"], reason="Not needed")
        open_after = (await ok(agent, "list_asks", status="open"))["total"]
        answered = (await ok(agent, "list_asks", status="answered", limit=50))["items"]
        closed = next(a for a in answered if a["id"] == waiting["ask"])
        proof(
            "M47",
            "lifting an approval gate that nobody answered closes its question (answered "
            '"Gate lifted" by the person) and takes its row off the Needs a person list',
            lift.status == 200
            and open_before == 1
            and open_after == 0
            and (closed["answer"], closed["answered_by"]) == ("Gate lifted", owner.name)
            and (await ok(agent, "needs_a_person"))["total"] == 0
            and (await ok(agent, "get_node", node=other["ref"]))["held_by"] == [],
            f"{closed['answered_by']} lifted it",
        )

        epic, _, story_task = await make_tree(agent)
        dropped = await ok(
            agent, "create_node", kind="task", title="To drop", parent=story_task["parent"]
        )
        await ok(agent, "claim_node", node=dropped["ref"])
        await ok(agent, "finish_work", node=dropped["ref"], outcome="dropped", note="No.")
        tried = [
            words(
                await refused(
                    agent, "add_gate", node=epic["ref"], kind="timer", not_before=soon(60)
                )
            ),
            words(await refused(agent, "add_gate", node=story_task["ref"], kind="timer")),
            words(
                await refused(
                    agent,
                    "add_gate",
                    node=story_task["ref"],
                    kind="timer",
                    not_before=soon(60),
                    question="x",
                )
            ),
            words(await refused(agent, "add_gate", node=story_task["ref"], kind="approval")),
            words(
                await refused(
                    agent,
                    "add_gate",
                    node=story_task["ref"],
                    kind="approval",
                    question="Use vrk_" + "a" * 40 + "?",
                )
            ),
            words(
                await refused(
                    agent, "add_gate", node=dropped["ref"], kind="timer", not_before=soon(60)
                )
            ),
        ]
        proof(
            "M47",
            "a gate is refused where it makes no sense, in words: on an epic, a timer with no "
            "time, a timer with a question, an approval with no question, a secret in the "
            "question (never stored, never repeated) and on work that is finished",
            "is an epic" in tried[0]
            and "needs not_before" in tried[1]
            and "asks nobody" in tried[2]
            and "needs question" in tried[3]
            and "looks like" in tried[4]
            and "a" * 40 not in tried[4]
            and "dropped" in tried[5],
            clip(tried[0]),
        )


async def draft_plans() -> None:
    """A drafted plan is held, listed as one row, and a person approves or rejects it as one
    decision."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        made = await ok(agent, "create_plan", items=PLAN, draft=True, title="Checkout plan")
        refs = [c["ref"] for c in made["created"]]
        nodes = [await ok(agent, "get_node", node=ref) for ref in refs]
        task = next(c["ref"] for c in made["created"] if c["kind"] == "task")
        peeked = await ok(agent, "next_work", peek=True)
        ready = (await ok(agent, "ready_work"))["items"]
        claim = words(await refused(agent, "claim_node", node=task))
        needs = await ok(agent, "needs_a_person")
        (row,) = needs["groups"][0]["items"]
        by_agent = words(await refused(agent, "approve_plan", plan=made["plan"], approved=True))
        proof(
            "M47",
            "create_plan with draft: true makes the whole plan and holds every item of it by the "
            "plan: no agent is given any of it, none is ready, none can be claimed; the plan is "
            "ONE row of the Needs a person list, ahead of everything else, with its title and "
            "the number of items and who proposed it; and an agent cannot approve it",
            made["draft"] is True
            and made["counts"]["task"] == 2
            and all(n["held_by"] == [f"plan:{made['plan']}"] for n in nodes)
            and peeked["found"] is False
            and ready == []
            and "held" in claim
            and needs["total"] == 1
            and needs["groups"][0]["story"] is None
            and needs["groups"][0]["title"] == "Plans waiting for approval"
            and (row["kind"], row["title"], row["count"]) == ("plan", "Checkout plan", 4)
            and row["plan"]["id"] == made["plan"]
            and "not allowed to use" in by_agent,
            f"Plan {made['plan']}: 4 items, 1 row",
        )
        approved = person(project, owner, "approve_plan", plan=made["plan"], approved=True)
        free = [(await ok(agent, "get_node", node=ref))["held_by"] for ref in refs]
        taken = await ok(agent, "next_work")
        again = person(
            project, owner, "approve_plan", plan=made["plan"], approved=False, reason="x"
        )
        proof(
            "M47",
            "a person approves the plan as one decision (named, final): every item of it is free "
            "at once, next_work gives a task of it, its row is gone, and the decision cannot be "
            "taken again",
            approved.status == 200
            and approved.body["state"] == "approved"
            and approved.body["decided_by"] == owner.name
            and free == [[]] * 4
            and taken["found"]
            and taken["work"]["kind"] == "task"
            and (await ok(agent, "needs_a_person"))["total"] == 0
            and again.status == 400
            and "already approved" in again.body["message"],
            f"decided by {owner.name}",
        )
        second = await ok(agent, "create_plan", items=PLAN, draft=True)
        second_refs = [c["ref"] for c in second["created"]]
        blank = person(project, owner, "approve_plan", plan=second["plan"], approved=False)
        rejected = person(
            project,
            owner,
            "approve_plan",
            plan=second["plan"],
            approved=False,
            reason="Wrong scope",
        )
        gone = [await ok(agent, "get_node", node=ref) for ref in second_refs]
        proof(
            "M47",
            "a rejection needs a reason, and a rejected plan is dropped whole: every item is "
            "dropped and carries a handoff note from the person that says the plan was rejected "
            "and why, the plan is listed as rejected with the reason, and nothing waits any more",
            blank.status == 400
            and "needs a reason" in blank.body["message"]
            and rejected.status == 200
            and (rejected.body["state"], rejected.body["reason"]) == ("rejected", "Wrong scope")
            and all(
                n["status"] == "dropped"
                and n["last_handoff"]["note"] == "The plan was rejected: Wrong scope"
                and n["last_handoff"]["actor"] == owner.name
                for n in gone
            )
            and (await ok(agent, "list_plans", status="rejected"))["total"] == 1
            and (await ok(agent, "needs_a_person"))["total"] == 0,
            "4 items dropped with the reason",
        )
        plain = await ok(agent, "create_plan", items=PLAN)
        titled = words(await refused(agent, "create_plan", items=PLAN, title="A title"))
        proof(
            "M47",
            "a plan that is not a draft is as before (no plan number, nothing held), and a title "
            "goes only with a draft",
            plain["draft"] is False
            and plain["plan"] is None
            and (await ok(agent, "get_node", node=plain["created"][0]["ref"]))["held_by"] == []
            and "title is for a draft plan" in titled,
            "no plan, nothing held",
        )


async def templates() -> None:
    """A template is saved data: it makes the plan writing it out would, a bad one is refused with
    every problem, and values are checked."""
    project = make_project(register())
    other = make_project(register())
    async with (
        connect(project.mcp_url, project.key) as agent,
        connect(other.mcp_url, other.key) as by_hand,
    ):
        saved = await ok(agent, "save_plan_template", name="release", template=RELEASE)
        made = await ok(agent, "create_plan", template="release", values={"version": "2.1"})
        written = [
            {"key": "rel", "kind": "epic", "title": "Release 2.1"},
            {"key": "prep", "kind": "story", "title": "Prepare 2.1", "parent": "rel", "size": "m"},
            {"key": "tag", "kind": "task", "title": "Tag 2.1", "parent": "prep", "size": "xs"},
            {"key": "ship", "kind": "story", "title": "Ship to stable", "parent": "rel"},
        ]
        await ok(
            by_hand,
            "create_plan",
            items=written,
            links=[{"source": "ship", "target": "prep", "link_type": "needs_first"}],
            rules=[{"title": "Notes name every change in 2.1", "node": "prep"}],
        )
        proof(
            "M47",
            "a plan template is saved data (variables, items with {{placeholders}}, links and "
            "acceptance criteria, counted when saved) and create_plan with a template and values "
            "makes exactly the plan that writing it out by hand makes: the same items, parents, "
            "sizes, links and criteria",
            (saved["name"], saved["version"]) == ("release", 1)
            and saved["counts"]
            == {"epics": 1, "stories": 2, "tasks": 1, "links": 1, "acceptance criteria": 1}
            and made["draft"] is False
            and await shape(agent) == await shape(by_hand),
            "the same plan as the one written out",
        )
        newer = {**RELEASE, "description": "Release, second edition"}
        second = await ok(agent, "save_plan_template", name="release", template=newer)
        listing = await ok(agent, "list_plan_templates")
        whole = await ok(agent, "list_plan_templates", name="release")
        draft = await ok(
            agent,
            "create_plan",
            template="release",
            values={"version": "3.0", "channel": "beta"},
            draft=True,
        )
        deleted = await ok(agent, "delete_plan_template", name="release")
        gone = words(
            await refused(agent, "create_plan", template="release", values={"version": "1"})
        )
        proof(
            "M47",
            "a template is saved again as a new version and every save stays in the history, it "
            "is listed with its variables and read whole by name, it can make a draft plan "
            "(titled from its first item) that waits for a person, and once deleted it makes "
            "nothing, in words",
            second["version"] == 2
            and listing["total"] == 1
            and listing["items"][0]["template"] is None
            and listing["items"][0]["variables"]["channel"]["choices"] == ["stable", "beta"]
            and whole["items"][0]["template"]["description"] == "Release, second edition"
            and draft["draft"]
            and (await ok(agent, "list_plans"))["items"][0]["title"] == "Release 3.0"
            and deleted["name"] == "release"
            and (await ok(agent, "list_plan_templates"))["total"] == 0
            and "no plan template called 'release'" in gone,
            "version 2, a draft, then deleted",
        )

        bad = {
            "variables": {"v": {"pattern": "(a+)+$"}},
            "items": [
                {"key": "a", "kind": "story", "title": "{{missing}}", "parent": "nope"},
                {"key": "b", "kind": "task", "title": "T", "parent": "a"},
                {"key": "b", "kind": "task", "title": "Again", "parent": "a"},
            ],
        }
        said = words(await refused(agent, "save_plan_template", name="bad", template=bad))
        names = [
            words(await refused(agent, "save_plan_template", name=name, template=RELEASE))
            for name in ("Bad Name", "a/b")
        ]
        await ok(agent, "save_plan_template", name="release", template=RELEASE)
        values = words(
            await refused(
                agent, "create_plan", template="release", values={"channel": "gamma", "x": "1"}
            )
        )
        both = words(await refused(agent, "create_plan", template="release", items=PLAN))
        proof(
            "M47",
            "a template with a pattern that could run away, an unknown placeholder, a parent that "
            "does not exist or a key used twice is refused with every problem listed at once and "
            "nothing saved; a name that is not a plain name is refused; values that are missing, "
            "not one of the choices or not a variable are refused together, in words, with "
            "nothing created; and a template and a hand-written plan cannot be mixed",
            "The template was not saved, because of" in said
            and "Nothing was saved" in said
            and "pattern" in said
            and "{{missing}}" in said
            and all("template's name" in message for message in names)
            and "The plan was not created, because of 3 problems" in values
            and "version" in values
            and "one of stable, beta" in values
            and "'x' is not a variable" in values
            and "either a template" in both,
            clip(said, 90),
        )

        epic, _, _ = await make_tree(agent)
        template = {
            "variables": {"epic": {"type": "ref", "kind": "epic"}, "name": {}},
            "items": [
                {
                    "key": "s",
                    "kind": "story",
                    "title": "Checklist for {{name}}",
                    "parent": "{{epic}}",
                },
                {"key": "t", "kind": "task", "title": "Review {{name}}", "parent": "s"},
            ],
        }
        await ok(agent, "save_plan_template", name="checklist", template=template)
        hung = await ok(
            agent,
            "create_plan",
            template="checklist",
            values={"epic": epic["ref"], "name": "billing"},
        )
        story = next(c for c in hung["created"] if c["kind"] == "story")
        wrong_kind = words(
            await refused(
                agent, "create_plan", template="checklist", values={"epic": "story:1", "name": "x"}
            )
        )
        missing = words(
            await refused(
                agent,
                "create_plan",
                template="checklist",
                values={"epic": "epic:9999", "name": "x"},
            )
        )
        proof(
            "M47",
            "a template variable can name an epic that exists, and the plan hangs under it; an "
            "item of the wrong kind or one that is not there is refused and nothing is created",
            (await ok(agent, "get_node", node=story["ref"]))["parent"] == epic["ref"]
            and "must be a epic" in wrong_kind
            and "no epic 9999" in missing
            and "Nothing was created" in missing,
            f"{story['ref']} under {epic['ref']}",
        )


async def travelling() -> None:
    """Everything new travels through an export and an import, and a file that says something that
    does not fit is refused whole."""
    owner = register()
    source = make_project(owner)
    async with connect(source.mcp_url, source.key) as agent:
        _, story, task = await make_tree(agent)
        found = await ok(agent, "create_node", kind="task", title="Found it", parent=story["ref"])
        await ok(
            agent, "add_link", source=found["ref"], target=task["ref"], link_type="discovered_from"
        )
        timer = await ok(agent, "add_gate", node=task["ref"], kind="timer", not_before=far())
        approval = await ok(
            agent, "add_gate", node=found["ref"], kind="approval", question="Ship it?"
        )
        await ok(agent, "save_plan_template", name="release", template=RELEASE)
        draft = await ok(agent, "create_plan", items=PLAN, draft=True, title="Kept as draft")
        decided = await ok(
            agent,
            "create_plan",
            items=[{"key": "x", "kind": "epic", "title": "Decided"}],
            draft=True,
        )
        person(source, owner, "approve_plan", plan=decided["plan"], approved=True)
        person(source, owner, "answer_ask", ask=approval["ask"], answer="yes")
        file = await export_everything(agent)
    kinds = {e["action"] for e in file["events"]}
    there = register()
    target = make_project(there)
    async with connect(target.mcp_url, target.key) as client:
        await ok(client, "import_graph", document=file)
        titles = {n["title"]: n for n in (await ok(client, "list_nodes", limit=200))["items"]}
        gates = (await ok(client, "get_node", node=titles["Found it"]["ref"]))["gates"]
        template = await ok(client, "list_plan_templates", name="release")
        links = (await ok(client, "list_links", node=titles["Found it"]["ref"]))["items"]
        waiting_there = (await ok(client, "list_plans", status="proposed"))["total"]
        approved_there = person(target, there, "approve_plan", plan=draft["plan"], approved=True)
        freed = (await ok(client, "get_node", node=titles["Launch the checkout"]["ref"]))["held_by"]
        proof(
            "M47",
            "gates, drafts, decided plans, plan templates and the link of M44 all travel through "
            "an export and an import: the timer still holds its task, the approved gate is "
            "open, the draft is still held by its plan and a person there can still decide it "
            "(and frees it), the decided plan is approved, the template is whole, and the history "
            "says so",
            {"gate_added", "plan_proposed", "plan_approved", "plan_template_saved", "ask_answered"}
            <= kinds
            and titles["A task"]["held_by"] == [f"gate:{timer['id']}"]
            and titles["Found it"]["held_by"] == []
            and (gates[0]["id"], gates[0]["state"]) == (approval["id"], "open")
            and titles["Launch the checkout"]["held_by"] == [f"plan:{draft['plan']}"]
            and titles["Decided"]["held_by"] == []
            and waiting_there == 1
            and approved_there.status == 200
            and freed == []
            and (await ok(client, "list_plans", status="approved"))["total"] == 2
            and template["items"][0]["variables"]["version"]["pattern"] == "^[0-9.]+$"
            and [link["link_type"] for link in links] == ["discovered_from"],
            "gates, plans, a template and a link, imported",
        )
    scratch = make_project(register())
    bad = copy.deepcopy(file)
    next(e for e in bad["events"] if e["action"] == "gate_added")["after"]["kind"] = "sometime"
    async with connect(scratch.mcp_url, scratch.key) as client:
        said = words(await refused(client, "import_graph", document=bad))
        nothing = (await ok(client, "list_nodes"))["items"] == []
    proof(
        "M47",
        "a file whose gate is neither a timer nor an approval is refused whole by the import, in "
        "words, and the project stays empty",
        "kind must be timer or approval" in said and nothing,
        clip(said),
    )


async def on_the_board() -> None:
    """What a person does on the board with all of it, and what a viewer may not."""
    owner = register()
    project = make_project(owner)
    editor = join_project(project, "editor")
    viewer = join_project(project, "viewer")
    async with connect(project.mcp_url, make_key(project, "agent-two")) as agent:
        task = await a_task(agent)
        await ok(agent, "save_plan_template", name="release", template=RELEASE)
    made = person(
        project, editor, "create_plan", template="release", values={"version": "1.0"}, draft=True
    )
    gate = person(project, editor, "add_gate", node=task["ref"], kind="timer", not_before=far())
    lifted = person(project, editor, "remove_gate", gate=gate.body["id"], reason="Not needed")
    approved = person(project, editor, "approve_plan", plan=made.body["plan"], approved=True)
    viewed = person(project, viewer, "list_plan_templates")
    tried = [
        person(project, viewer, "save_plan_template", name="x", template=RELEASE),
        person(project, viewer, "add_gate", node=task["ref"], kind="timer", not_before=far()),
        person(project, viewer, "approve_plan", plan=made.body["plan"], approved=True),
    ]
    deleted = person(project, owner, "delete_plan_template", name="release")
    proof(
        "M47",
        "on the board a person who may edit makes a plan from a template as a draft, adds and "
        "lifts a gate and approves the plan, an owner deletes the template, a viewer reads the "
        "templates and is refused saving, adding a gate and approving, and the refusals change "
        "nothing",
        made.status == 200
        and gate.status == 200
        and gate.body["state"] == "waiting"
        and lifted.status == 200
        and approved.status == 200
        and viewed.status == 200
        and all(reply.status in (400, 403) for reply in tried)
        and deleted.status == 200
        and person(project, owner, "list_plans").body["total"] == 1,
        "an editor acts, a viewer is refused",
    )


async def gates() -> None:
    chapter("Gates, plan approval and plan templates (M47)")
    await timer_gates()
    await approval_gates()
    await draft_plans()
    await templates()
    await travelling()
    await on_the_board()


async def discovered() -> None:
    """M44: a link that says where work was found, which never makes anything wait."""
    chapter("Where work was found: the discovered_from link (M44)")
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        _, story, task = await make_tree(agent)
        found = await ok(
            agent, "create_node", kind="task", title="A bug we met", parent=story["ref"]
        )
        link = await ok(
            agent, "add_link", source=found["ref"], target=task["ref"], link_type="discovered_from"
        )
        at_source = (await ok(agent, "list_links", node=found["ref"]))["items"]
        at_target = (await ok(agent, "list_links", node=task["ref"]))["items"]
        twice = words(
            await refused(
                agent,
                "add_link",
                source=found["ref"],
                target=task["ref"],
                link_type="discovered_from",
            )
        )
        await ok(
            agent, "add_link", source=task["ref"], target=found["ref"], link_type="discovered_from"
        )
        after = await ok(agent, "get_node", node=found["ref"])
        taken = await ok(agent, "next_work", within_node=found["ref"])
        proof(
            "M44",
            "a discovered_from link says the new work was found while doing the other: it is made "
            "with add_link, listed from both ends, one way (the other direction is another link, "
            "a repeat is refused), and it never makes anything wait: the item is ready, waits "
            "for nothing, and the briefing that next_work gives says where it was found",
            (link["source"], link["target"], link["link_type"])
            == (found["ref"], task["ref"], "discovered_from")
            and [x["link_type"] for x in at_source] == ["discovered_from"]
            and [x["link_type"] for x in at_target] == ["discovered_from"]
            and "already" in twice.lower()
            and after["waiting_on"] == []
            and after["status"] == "ready"
            and taken["found"]
            and [b["ref"] for b in taken["context"]["discovered_from"]] == [task["ref"]],
            f"{found['ref']} found while doing {task['ref']}",
        )
        await ok(
            agent, "add_link", source=found["ref"], target=task["ref"], link_type="needs_first"
        )
        waiting = await ok(agent, "get_node", node=found["ref"])
        bad = [
            words(
                await refused(
                    agent, "add_link", source=found["ref"], target=story["ref"], link_type=kind
                )
            )
            for kind in ("discovered", "found_in")
        ]
        proof(
            "M44",
            "the five link types are told apart: a needs_first link between the same two items "
            "does make the item wait for the other (and says what it waits for), while "
            "discovered_from does not, and a name that is not one of the five is refused in words "
            "that list them",
            waiting["waiting_on"] == [task["ref"]]
            and all(
                "link_type must be one of" in message and "discovered_from" in message
                for message in bad
            ),
            clip(bad[0]),
        )
        file = await export_everything(agent)
    target = make_project(register())
    async with connect(target.mcp_url, target.key) as client:
        await ok(client, "import_graph", document=file)
        titles = {n["title"]: n for n in (await ok(client, "list_nodes", limit=200))["items"]}
        links = (await ok(client, "list_links", node=titles["A bug we met"]["ref"], limit=50))[
            "items"
        ]
        kinds = sorted(link["link_type"] for link in links)
    with tempfile.TemporaryDirectory() as folder:
        made = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "export_markdown.py"),
                project.mcp_url,
                folder,
            ],
            cwd=REPO_ROOT,
            env={**os.environ, "VRUM_AGENT_KEY": project.key},
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        texts = {p.name: p.read_text(encoding="utf-8") for p in Path(folder).rglob("*.md")}
    bug = next(t for name, t in texts.items() if name.startswith("task-2-"))
    first = next(t for name, t in texts.items() if name.startswith("task-1-"))
    proof(
        "M44",
        "the link travels through an export and an import (both links, and the one that makes "
        "work wait, come back as they were), and the Markdown copy names it in words at both "
        "ends: the new item says discovered from and the one it was found in says led to",
        kinds == ["discovered_from", "discovered_from", "needs_first"]
        and made.returncode == 0
        and "discovered from" in bug
        and "led to" in first,
        "3 links imported, named in the copy",
    )
