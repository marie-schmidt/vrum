"""Chapter of the acceptance scenario for granular review (M53): a person who reads a drafted plan
can say yes to a line as well as ask for a change to it, an approval stays on a line while the line
does not change, and every line says which revision last changed it, who made that and what a
person said about it since, like git blame. Suggested moves can be accepted in part. Told from
outside, through a real MCP client (the agents) and the board's own calls as the people of a
project, and then a headless browser on the Needs a person page and the review page
(tests/acceptance_granular_page.py).

Each proof works in a project of its own. The scripted embedding service of the stack stands in for
the model, so nothing here depends on a real one."""

import copy
import re
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from mcp import Client

from tests.acceptance_revising import DOCS, an_epic, held_and_untouchable, person, read, words
from tests.acceptance_revising_page import failing, holds
from tests.acceptance_scenario import chapter, clip, proof
from tests.demo_flow import in_order
from tests.helpers import code_of, connect, export_everything, ok, refused
from tests.world import (
    Person,
    Project,
    join_project,
    make_key,
    make_limited_key,
    make_project,
    register,
)

TYRES = "Studded tyres"
NEW_TYRES = "Studded tyres for the river path and the hill route"
FIT = "At the depot.\nOne morning each."
NEW_FIT = "At the depot.\nFour hours each, from 8 until noon."
NOTE_2 = "Named the tyres, said how long a bike is off the street and took out the spare chains."
NOTE_3 = "A supplier takes longer than a day to choose, so that is a medium task."


def the_plan(epic: str) -> list[dict[str, Any]]:
    """Three stories and four tasks under an epic that exists: one task is the sort a reviewer asks
    to have dropped, one story is left for the reviewer to say nothing about."""
    return [
        {
            "key": "tyres",
            "kind": "story",
            "title": TYRES,
            "parent": epic,
            "size": "l",
            "body": "Forty bikes.\nBefore the first frost.",
        },
        {
            "key": "pick",
            "kind": "task",
            "title": "Choose a supplier",
            "parent": "tyres",
            "size": "s",
        },
        {
            "key": "fit",
            "kind": "task",
            "title": "Fit forty bikes",
            "parent": "tyres",
            "size": "m",
            "body": FIT,
        },
        {
            "key": "chains",
            "kind": "task",
            "title": "Buy spare chains for staff bikes",
            "parent": "tyres",
            "size": "s",
            "body": "Two sets at the depot.",
        },
        {"key": "covers", "kind": "story", "title": "Weather covers", "parent": epic, "size": "m"},
        {"key": "order", "kind": "task", "title": "Order covers", "parent": "covers", "size": "xs"},
        {
            "key": "battery",
            "kind": "story",
            "title": "Battery swaps for the cold months",
            "parent": epic,
            "size": "m",
        },
    ]


LINKS = [{"source": "fit", "target": "pick", "link_type": "needs_first"}]
RULES = [{"title": "Every bike passes the grip test", "node": "fit"}]


async def drafted(
    agent: Client, epic: str, title: str = "Winter tyres"
) -> tuple[int, dict[str, str]]:
    made = await ok(
        agent,
        "create_plan",
        items=the_plan(epic),
        links=LINKS,
        rules=RULES,
        draft=True,
        title=title,
    )
    return int(made["plan"]), {c["key"]: c["ref"] for c in made["created"]}


def revised(
    epic: str, refs: dict[str, str], leave_out: tuple[str, ...] = (), **changes: dict[str, Any]
) -> list[dict[str, Any]]:
    """The whole plan again, each item continuing the one it was, with the changes by key and the
    items in `leave_out` taken out. Links and criteria are left out of the call, so they are
    kept."""
    return [
        {**copy.deepcopy(item), "ref": refs[item["key"]], **changes.get(item["key"], {})}
        for item in the_plan(epic)
        if item["key"] not in leave_out
    ]


def ask(
    project: Project,
    who: Person,
    plan: int,
    revision: int,
    summary: str,
    comments: list[dict[str, Any]] | None = None,
    approved: list[str] | None = None,
) -> Any:
    """What the board does when a person presses Request changes."""
    arguments: dict[str, Any] = {"plan": plan, "revision": revision, "summary": summary}
    if comments is not None:
        arguments["comments"] = comments
    if approved is not None:
        arguments["approved"] = approved
    return person(project, who, "request_plan_changes", **arguments)


def states(plan: dict[str, Any]) -> dict[str, str]:
    """What a person said of each line, by reference."""
    return {line["ref"]: line["review"]["state"] for line in plan["blame"]}


def unchecked(plan: dict[str, Any]) -> dict[str, Any]:
    """A plan as read, without its checks: the words of the check for look-alikes say how much of
    the index is still to be built, which changes between two reads."""
    return {key: value for key, value in plan.items() if key != "checks"}


def named(refs: dict[str, str], keys: list[str]) -> list[str]:
    """References in the order the server lists them."""
    return in_order(refs[key] for key in keys)


async def a_conversation() -> SimpleNamespace:
    """A plan with three rounds, read at every stage (the reads are what a proof compares): the
    owner reads revision 1 and approves two items, comments on four (one asks for a task to be
    dropped) and the agent sends revision 2, which changes two and takes out the task; the owner
    reads revision 2, approves two changed items and agrees the removal, comments on a story that
    revision 2 left alone and asks again; the agent sends revision 3, which changes one of the
    items the owner approved first."""
    owner = register()
    project = make_project(owner)
    found = SimpleNamespace(owner=owner, project=project, reads={})
    async with connect(project.mcp_url, project.key) as agent:
        found.epic = epic = await an_epic(agent)
        found.number, found.refs = await drafted(agent, epic)
        number, refs = found.number, found.refs
        found.reads["fresh"] = await read(agent, number)
        found.first = ask(
            project,
            owner,
            number,
            1,
            "Name the tyres, and the staff bikes do not need spare chains.",
            comments=[
                {"item": refs["tyres"], "field": "title", "text": "Say which tyres."},
                {"item": refs["fit"], "field": "body", "text": "How many hours is a bike off?"},
                {"item": refs["chains"], "text": "Staff bikes do not need spare chains."},
                {"item": refs["covers"], "field": "size", "text": "Is m enough?"},
            ],
            approved=[refs["pick"], refs["order"]],
        )
        found.reads["asked1"] = await read(agent, number)
        found.reads["as_of_1"] = await read(agent, number, revision=1)
        await ok(
            agent,
            "revise_plan",
            plan=number,
            note=NOTE_2,
            items=revised(
                epic, refs, leave_out=("chains",), tyres={"title": NEW_TYRES}, fit={"body": NEW_FIT}
            ),
        )
        found.reads["rev2"] = await read(agent, number)
        found.second = ask(
            project,
            owner,
            number,
            2,
            "Better. I still do not know about the covers.",
            comments=[{"item": refs["covers"], "field": "size", "text": "Still not sure about m."}],
            approved=[refs["tyres"], refs["fit"], refs["chains"]],
        )
        found.reads["asked2"] = await read(agent, number)
        found.reads["as_of_2"] = await read(agent, number, revision=2)
        await ok(
            agent,
            "revise_plan",
            plan=number,
            note=NOTE_3,
            items=revised(
                epic,
                refs,
                leave_out=("chains",),
                tyres={"title": NEW_TYRES},
                fit={"body": NEW_FIT},
                pick={"size": "m"},
            ),
        )
        found.reads["rev3"] = await read(agent, number)
        found.reads["again_1"] = await read(agent, number, revision=1)
        found.reads["again_2"] = await read(agent, number, revision=2)
    return found


async def approving_lines() -> None:
    """Requesting changes with approvals: they are recorded in the feedback, in the change feed and
    in the export, an item named twice is recorded once, and a request with approvals and no
    comments is allowed."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        other, other_refs = await drafted(agent, epic, "Another plan")
        asked = ask(
            project,
            owner,
            number,
            1,
            "Name the tyres, and I do not need spare chains.",
            comments=[
                {"item": refs["tyres"], "field": "title", "text": "Say which tyres."},
                {"item": refs["chains"], "text": "Staff bikes do not need spare chains."},
            ],
            approved=[refs["order"], refs["covers"], refs["pick"], refs["pick"]],
        )
        only_yes = ask(
            project,
            owner,
            other,
            1,
            "Fine so far, but not yet.",
            approved=[other_refs["covers"]],
        )
        feed = (await ok(agent, "changes_since", limit=200))["items"]
        events = [e for e in feed if e["action"] == "plan_changes_requested"]
        plan = await read(agent, number)
        small = await read(agent, other)
        exported = await export_everything(agent)
        in_the_file = [e for e in exported["events"] if e["action"] == "plan_changes_requested"]
        wanted = named(refs, ["covers", "pick", "order"])
        proof(
            "M53",
            "a person asks for changes to a plan with two comments and three approved items (one "
            "named twice): the answer, the plan's feedback, the change feed's event (made by the "
            "person) and the export all carry the approved items, epics first and each once; the "
            "comments are as before; the plan still waits for its agent with every item held; and "
            "a request with approvals and no comment is allowed, keeps the approval and has no "
            "comments",
            holds(
                asked.status == 200,
                asked.body["state"] == "changes_requested",
                asked.body["feedback"][0]["approved"] == wanted,
                plan["feedback"][0]["approved"] == wanted,
                [c["item"] for c in plan["feedback"][0]["comments"]]
                == [refs["tyres"], refs["chains"]],
                sorted(e["after"]["plan"] for e in events) == sorted([number, other]),
                [e["after"]["approved"] for e in events if e["after"]["plan"] == number]
                == [wanted],
                all((e["actor"], e["actor_kind"]) == (owner.name, "person") for e in events),
                sorted(e["after"]["plan"] for e in in_the_file) == sorted([number, other]),
                [e["after"]["approved"] for e in in_the_file if e["after"]["plan"] == number]
                == [wanted],
                plan["state"] == "changes_requested",
                only_yes.status == 200,
                small["feedback"][0]["approved"] == [other_refs["covers"]],
                small["feedback"][0]["comments"] == [],
                small["state"] == "changes_requested",
                await held_and_untouchable(agent, number, refs),
            ),
            f"{len(wanted)} approved by {owner.name}{failing()}",
        )


async def a_request_that_does_both_is_refused() -> None:
    """An item cannot be approved and commented on in one request: the refusal lists every problem
    and records nothing. An agent cannot approve, a viewer cannot, an editor can."""
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer")
    editor = join_project(project, "editor")
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        before = await read(agent, number)
        events = int((await ok(agent, "changes_since", newest=True, limit=1))["latest_event_id"])
        both = ask(
            project,
            owner,
            number,
            1,
            "Smaller.",
            comments=[{"item": refs["fit"], "field": "body", "text": "Hours, please."}],
            approved=[refs["fit"], refs["tyres"], "story:999", refs["tyres"]],
        )
        no_summary = ask(project, owner, number, 1, " ", approved=[refs["tyres"]])
        said = both.body.get("message", "")
        after = await read(agent, number)
        after_events = int(
            (await ok(agent, "changes_since", newest=True, limit=1))["latest_event_id"]
        )
        by_agent = words(
            await refused(
                agent,
                "request_plan_changes",
                plan=number,
                revision=1,
                summary="Yes to this.",
                approved=[refs["tyres"]],
            )
        )
        by_viewer = ask(project, viewer, number, 1, "Yes.", approved=[refs["tyres"]])
        by_editor = ask(project, editor, number, 1, "Yes.", approved=[refs["tyres"]])
        proof(
            "M53",
            "an item that is approved and also commented on is refused with every problem listed "
            "(that item, and an item that is not in the plan) and nothing is recorded, not one "
            "event or word of the plan is different afterwards, and a request with no summary is "
            "still refused; an agent is refused approving, a viewer is refused too, and an editor "
            "approves as themselves",
            holds(
                both.status == 400,
                "The request was not sent, because of 2 problems" in said,
                f"{refs['fit']} is approved and also has a comment asking for a change" in said,
                "story:999 is not an item of the revision reviewed" in said,
                "Nothing was changed" in said,
                unchecked(after) == unchecked(before),
                after["feedback"] == [],
                after_events == events,
                no_summary.status == 400,
                "summary" in no_summary.body.get("message", ""),
                "not allowed to use" in by_agent,
                by_viewer.status in (400, 403),
                by_editor.status == 200,
                (by_editor.body["feedback"][0]["by"], by_editor.body["feedback"][0]["approved"])
                == (editor.name, [refs["tyres"]]),
            ),
            clip(said, 100) + failing(),
        )


async def what_carries_over() -> None:
    """After a revision, an approval stands while the item does not change, an item changed since
    reads changed, a request that nothing answered reads asked, and the latest word counts."""
    found = await a_conversation()
    refs, number = found.refs, found.number
    rev2, asked2, rev3 = found.reads["rev2"], found.reads["asked2"], found.reads["rev3"]
    fresh, as_of_1 = found.reads["fresh"], found.reads["as_of_1"]
    gone = refs["chains"]
    lines2 = {line["ref"]: line for line in rev2["blame"]}
    one, two = asked2["feedback"][0], asked2["feedback"][1]
    proof(
        "M53",
        "before anyone has said anything every line reads none with no person; at revision 1 "
        "after the person's first request the two items she approved read approved by her on "
        "revision 1 and the four she commented on read asked; after the agent's revision 2 the "
        "approved items that did not change still read approved (by her, on revision 1, with "
        "the number of her feedback), the two she commented on that revision 2 changed read "
        "changed, the one she asked about and revision 2 left alone reads asked, the story nobody "
        "said anything about reads none, and the task revision 2 took out is in blame as removed "
        "by revision 2 with the agent's note and none of her words (they were about revision 1)",
        holds(
            set(states(fresh).values()) == {"none"},
            all(
                line["review"]["by"] is None and line["review"]["feedback"] is None
                for line in fresh["blame"]
            ),
            states(as_of_1)
            == {
                refs["tyres"]: "asked",
                refs["pick"]: "approved",
                refs["fit"]: "asked",
                refs["chains"]: "asked",
                refs["covers"]: "asked",
                refs["order"]: "approved",
                refs["battery"]: "none",
            },
            states(rev2)
            == {
                refs["tyres"]: "changed",
                refs["pick"]: "approved",
                refs["fit"]: "changed",
                refs["covers"]: "asked",
                refs["order"]: "approved",
                refs["battery"]: "none",
                gone: "none",
            },
            [
                (
                    lines2[r]["review"]["by"],
                    lines2[r]["review"]["revision"],
                    lines2[r]["review"]["feedback"],
                )
                for r in (refs["pick"], refs["order"], refs["tyres"], refs["fit"], refs["covers"])
            ]
            == [(found.owner.name, 1, one["id"])] * 5,
            [line["ref"] for line in rev2["blame"]] == in_order(lines2),
            (lines2[gone]["change"], lines2[gone]["revision"], lines2[gone]["note"])
            == ("removed", 2, NOTE_2),
            lines2[gone]["by_kind"] == "agent",
            gone not in {item["ref"] for item in rev2["tree"]},
        ),
        f"plan {number}: {sorted(set(states(rev2).values()))}{failing()}",
    )
    lines3 = {line["ref"]: line for line in rev3["blame"]}
    proof(
        "M53",
        "the latest word about a line is the one that counts: after her second request (on "
        "revision 2, which approved the two items she had asked about and the removal and "
        "commented on the story again) those two read approved on revision 2, the story reads "
        "asked from her second feedback, and the items she approved on revision 1 still read "
        "approved; the agent's revision 3 changes the size of one of those, which then reads "
        "changed (with revision 3 and its note) while the others she approved stay approved with "
        "the revision she read, and a task that revision 2 took out is not in the blame of "
        "revision 3; reading revision 1 or 2 again after all that gives what it gave before, as "
        "of then (so revision 2's removal reads approved only there)",
        holds(
            states(asked2)
            == {
                refs["tyres"]: "approved",
                refs["pick"]: "approved",
                refs["fit"]: "approved",
                refs["covers"]: "asked",
                refs["order"]: "approved",
                refs["battery"]: "none",
                gone: "approved",
            },
            asked2["blame"] == found.reads["as_of_2"]["blame"],
            asked2["feedback"][1]["id"] == two["id"],
            (
                lines3[refs["tyres"]]["review"]["revision"],
                lines3[refs["tyres"]]["review"]["feedback"],
            )
            == (2, two["id"]),
            states(rev3)
            == {
                refs["tyres"]: "approved",
                refs["pick"]: "changed",
                refs["fit"]: "approved",
                refs["covers"]: "asked",
                refs["order"]: "approved",
                refs["battery"]: "none",
            },
            (
                lines3[refs["pick"]]["change"],
                lines3[refs["pick"]]["revision"],
                lines3[refs["pick"]]["note"],
            )
            == ("changed", 3, NOTE_3),
            (
                lines3[refs["pick"]]["review"]["revision"],
                lines3[refs["pick"]]["review"]["feedback"],
            )
            == (1, one["id"]),
            (
                lines3[refs["order"]]["review"]["revision"],
                lines3[refs["order"]]["review"]["feedback"],
            )
            == (1, one["id"]),
            (lines3[refs["fit"]]["change"], lines3[refs["fit"]]["revision"]) == ("changed", 2),
            gone not in lines3,
            found.reads["again_1"]["blame"] == as_of_1["blame"],
            found.reads["again_2"]["blame"] == asked2["blame"],
            states(found.reads["again_2"])[gone] == "approved",
        ),
        clip(str(states(rev3)), 100) + failing(),
    )


async def blame_equals_the_changes() -> None:
    """For every revision, each line's `change` and `revision` is what the `changes` of the
    revisions say, worked out here from them and not from `blame`: an item last changed in
    revision R is listed in the changes of R and in none after it; who made it, when and the note
    are those of that revision."""
    found = await a_conversation()
    async with connect(found.project.mcp_url, found.project.key) as agent:
        revisions = {r: await read(agent, found.number, revision=r) for r in (1, 2, 3)}
    listed = {r: {c["ref"]: c for c in (p["changes"] or [])} for r, p in revisions.items()}
    wrong: list[str] = []
    checked = 0
    for shown, plan in revisions.items():
        in_the_tree = [item["ref"] for item in plan["tree"]]
        took_out = [ref for ref, c in listed[shown].items() if c["change"] == "removed"]
        blamed = {line["ref"]: line for line in plan["blame"]}
        if [line["ref"] for line in plan["blame"]] != in_order([*in_the_tree, *took_out]):
            wrong.append(f"revision {shown} names other lines")
        for ref in in_the_tree:
            last, how = 1, "added"
            for r in range(2, shown + 1):
                if ref in listed[r] and listed[r][ref]["change"] in ("added", "changed"):
                    last, how = r, listed[r][ref]["change"]
            line = blamed[ref]
            info = plan["revisions"][last - 1]
            checked += 1
            if (line["change"], line["revision"]) != (how, last) or (
                line["by"],
                line["by_kind"],
                line["at"],
                line["note"],
            ) != (info["by"], info["by_kind"], info["at"], info["note"]):
                wrong.append(f"{ref} at revision {shown}: {line}")
        for ref in took_out:
            line = blamed[ref]
            if (line["change"], line["revision"]) != ("removed", shown):
                wrong.append(f"{ref} removed at revision {shown}: {line}")
    changed_in = {
        r: sorted(c for c, v in listed[r].items() if v["change"] == "changed") for r in listed
    }
    proof(
        "M53",
        "blame equals the changes of every revision, worked out from them and not from blame: "
        "for revisions 1, 2 and 3 every line of the revision shown (and the task revision 2 took "
        "out) is named once in the order the server lists items; each says it was added or "
        "changed in the last revision whose changes list it and not in any after it, and who "
        "made that revision, when and with what note are that revision's; revision 3's changes "
        "name one item, so the others read the revision they were last in",
        holds(
            not wrong,
            checked == 7 + 6 + 6,
            set(listed[3]) == {found.refs["pick"]},
            changed_in[2] == sorted([found.refs["tyres"], found.refs["fit"]]),
            found.reads["again_1"]["blame"] == found.reads["as_of_1"]["blame"],
        ),
        f"{checked} lines read against {sum(len(v) for v in listed.values())} changes"
        + (f"; wrong: {wrong[:2]}" if wrong else "")
        + failing(),
    )


async def a_removed_line_can_be_agreed_to_or_asked_for() -> None:
    """An item a revision took out can be approved (the person agrees it goes) or commented on (they
    want it back, and the comment keeps its words as they were)."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        ask(
            project,
            owner,
            number,
            1,
            "Smaller.",
            comments=[{"item": refs["chains"], "text": "Drop it."}],
        )
        await ok(
            agent,
            "revise_plan",
            plan=number,
            note="Took out the chains and the order.",
            items=revised(epic, refs, leave_out=("chains", "order")),
        )
        both = ask(
            project,
            owner,
            number,
            2,
            "About the two I removed.",
            comments=[{"item": refs["order"], "text": "Put the order task back."}],
            approved=[refs["chains"], refs["order"]],
        )
        stray = ask(
            project,
            owner,
            number,
            2,
            "A task that was never there.",
            comments=[{"item": "task:99", "text": "Nope."}],
        )
        done = ask(
            project,
            owner,
            number,
            2,
            "Agreed about the chains. I want the order back.",
            comments=[{"item": refs["order"], "text": "Put the order task back."}],
            approved=[refs["chains"]],
        )
        plan = await read(agent, number)
        lines = {line["ref"]: line for line in plan["blame"]}
        node = await ok(agent, "get_node", node=refs["order"])
        feedback = plan["feedback"][1]
        proof(
            "M53",
            "an item that revision 2 took out can be agreed to or asked for: both at once on one "
            "item is refused like any other item, a task that is not in the revision and was not "
            "taken out is refused, and a request that approves one removed task and comments on "
            "the other is recorded; the comment's quoted words are the task's title as it was "
            "and the approval is in approved; blame lists both as removed by revision 2 with "
            "the agent's note, the agreed one as approved by the person on revision 2 and the "
            "one asked for as asked; the tasks stay dropped, as the revision made them",
            holds(
                both.status == 400,
                f"{refs['order']} is approved and also has a comment" in both.body["message"],
                stray.status == 400,
                "task:99 is not an item of the revision reviewed, or one it took out"
                in stray.body["message"],
                done.status == 200,
                feedback["approved"] == [refs["chains"]],
                [(c["item"], c["field"], c["quoted"]) for c in feedback["comments"]]
                == [(refs["order"], None, "Order covers")],
                (lines[refs["chains"]]["change"], lines[refs["chains"]]["revision"])
                == ("removed", 2),
                lines[refs["chains"]]["note"] == "Took out the chains and the order.",
                lines[refs["order"]]["change"] == "removed",
                lines[refs["chains"]]["review"]["state"] == "approved",
                (
                    lines[refs["chains"]]["review"]["by"],
                    lines[refs["chains"]]["review"]["revision"],
                )
                == (owner.name, 2),
                lines[refs["order"]]["review"]["state"] == "asked",
                plan["state"] == "changes_requested",
                node["status"] == "dropped",
            ),
            clip(str({r: lines[r]["review"]["state"] for r in lines}), 100) + failing(),
        )


async def a_move_accepted_in_part() -> None:
    """A person accepts a suggested move naming only some of its items: those move, the others stay
    and the record says the person left them out; the refusals change nothing."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        old = (
            await ok(agent, "create_node", kind="story", title="Spare parts stock", parent=epic)
        )["ref"]
        new = (await ok(agent, "create_node", kind="story", title="Workshop jobs", parent=epic))[
            "ref"
        ]
        tasks = [
            (await ok(agent, "create_node", kind="task", title=title, parent=old))["ref"]
            for title in ("List of parts", "Reorder level", "Parts used on each job")
        ]
        proposal = await ok(
            agent,
            "propose_placement",
            items=tasks,
            parent=new,
            reason="Parts are written against each job in the workshop story.",
        )
        number = proposal["id"]
        before = [(await ok(agent, "get_node", node=ref))["parent"] for ref in tasks]
        empty = person(
            project, owner, "decide_placement", placement=number, accepted=True, items=[]
        )
        stranger = person(
            project, owner, "decide_placement", placement=number, accepted=True, items=[old]
        )
        rejecting = person(
            project,
            owner,
            "decide_placement",
            placement=number,
            accepted=False,
            reason="No.",
            items=[tasks[0]],
        )
        by_agent = words(
            await refused(
                agent, "decide_placement", placement=number, accepted=True, items=[tasks[0]]
            )
        )
        untouched = [(await ok(agent, "get_node", node=ref))["parent"] for ref in tasks]
        still = (await ok(agent, "list_placements", status="proposed"))["items"]
        done = person(
            project,
            owner,
            "decide_placement",
            placement=number,
            accepted=True,
            items=[tasks[2], tasks[0]],
        )
        after = [(await ok(agent, "get_node", node=ref))["parent"] for ref in tasks]
        history = {
            ref: [
                e
                for e in (await ok(agent, "node_history", node=ref))["items"]
                if e["action"] == "move"
            ]
            for ref in tasks
        }
        feed = (await ok(agent, "changes_since", limit=200))["items"]
        (event,) = [e for e in feed if e["action"] == "placement_accepted"]
        (listed,) = (await ok(agent, "list_placements", status="accepted"))["items"]
        again = await ok(
            agent,
            "propose_placement",
            items=[tasks[1]],
            parent=new,
            reason="The reorder level belongs with the parts after all.",
        )
        whole = person(project, owner, "decide_placement", placement=again["id"], accepted=True)
        final = (await ok(agent, "get_node", node=tasks[1]))["parent"]
        left_out = {"ref": tasks[1], "why": f"{owner.name} left it out"}
        proof(
            "M53",
            "a person accepts a move of three tasks naming two of them: those two move (as the "
            "person) and the one not named stays under its story, the record's `applied` and "
            "`skipped` say so (the skipped one with the person's name and that they left it out, "
            "and nothing else as the reason), the proposal list and the change feed's event say "
            "the same, and the history of the one left out has no move; naming none, naming an "
            "item that is not in the proposal and naming items while rejecting are each refused "
            "in words with nothing moved and the proposal still waiting, an agent is refused "
            "deciding, and the task that was left out can be proposed again and moved whole",
            holds(
                before == [old, old, old],
                empty.status == 400,
                "Name at least one item to move, or reject the proposal" in empty.body["message"],
                stranger.status == 400,
                f"{code_of(old)} is not in proposal {number}" in stranger.body["message"],
                rejecting.status == 400,
                "Name the items to move only when accepting" in rejecting.body["message"],
                "not allowed to use" in by_agent,
                untouched == before,
                [p["id"] for p in still] == [number],
                done.status == 200,
                done.body["state"] == "accepted",
                done.body["applied"] == [tasks[0], tasks[2]],
                done.body["skipped"] == [left_out],
                after == [new, old, new],
                [(e["actor"], e["actor_kind"]) for e in history[tasks[0]]]
                == [(owner.name, "person")],
                not history[tasks[1]],
                [(e["actor"], e["actor_kind"]) for e in history[tasks[2]]]
                == [(owner.name, "person")],
                event["after"]["applied"] == [tasks[0], tasks[2]],
                event["after"]["skipped"] == [left_out],
                (listed["applied"], listed["skipped"]) == ([tasks[0], tasks[2]], [left_out]),
                whole.status == 200,
                whole.body["applied"] == [tasks[1]],
                whole.body["skipped"] == [],
                final == new,
            ),
            f"{len(done.body['applied'])} moved, {len(done.body['skipped'])} left out "
            f"by {owner.name}{failing()}",
        )


async def what_travels() -> None:
    """Approvals and a move accepted in part survive an export and an import into another project:
    the plan reads the same there, blame included, and a file with the approvals tampered with is
    refused with nothing imported."""
    found = await a_conversation()
    refs, number = found.refs, found.number
    async with connect(found.project.mcp_url, found.project.key) as agent:
        epic = found.epic
        old = (
            await ok(agent, "create_node", kind="story", title="Spare parts stock", parent=epic)
        )["ref"]
        new = (await ok(agent, "create_node", kind="story", title="Workshop jobs", parent=epic))[
            "ref"
        ]
        tasks = [
            (await ok(agent, "create_node", kind="task", title=title, parent=old))["ref"]
            for title in ("List of parts", "Reorder level")
        ]
        moved = await ok(
            agent, "propose_placement", items=tasks, parent=new, reason="Parts belong with jobs."
        )
        part = person(
            found.project,
            found.owner,
            "decide_placement",
            placement=moved["id"],
            accepted=True,
            items=[tasks[0]],
        )
        waiting = await read(agent, number)
        as_of_1 = await read(agent, number, revision=1)
        file = await export_everything(agent)
        decided = (await ok(agent, "list_placements", status="accepted"))["items"]
    target = make_project(register())
    async with connect(target.mcp_url, target.key) as client:
        await ok(client, "import_graph", document=file)
        loaded = await read(client, number)
        again = await read(client, number, revision=1)
        there = (await ok(client, "list_placements", status="accepted"))["items"]
        parents = [(await ok(client, "get_node", node=ref))["parent"] for ref in tasks]
        again_file = await export_everything(client)
        wanted = ("plan_changes_requested", "placement_accepted")
        proof(
            "M53",
            "approvals and a move accepted in part survive an export and an import into another "
            "project: the plan reads the same there (the feedback with its approved items, what "
            "changed, and blame with every state, revision, author and note, at the current "
            "revision and at revision 1), the move reads the same (what was applied and what "
            "the person left out) and the tasks are where it left them, and exporting it again "
            "gives the same events",
            holds(
                part.status == 200,
                loaded["feedback"] == waiting["feedback"],
                [f["approved"] for f in loaded["feedback"]]
                == [f["approved"] for f in waiting["feedback"]],
                loaded["blame"] == waiting["blame"],
                again["blame"] == as_of_1["blame"],
                loaded["changes"] == waiting["changes"],
                there == decided,
                there[0]["skipped"]
                == [{"ref": tasks[1], "why": f"{found.owner.name} left it out"}],
                parents == [new, old],
                [e["after"] for e in again_file["events"] if e["action"] in wanted]
                == [e["after"] for e in file["events"] if e["action"] in wanted],
            ),
            f"{len(loaded['blame'])} lines, {len(there)} move{failing()}",
        )

    tampers: list[tuple[str, Callable[[dict[str, Any]], object], str]] = [
        (
            "plan_changes_requested",
            lambda e: e["after"].pop("approved"),
            "approved must be a list of the items the person approved",
        ),
        (
            "plan_changes_requested",
            lambda e: e["after"].__setitem__("approved", "task:1"),
            "approved must be a list of the items the person approved",
        ),
        (
            "plan_changes_requested",
            lambda e: e["after"].__setitem__("approved", [3]),
            "approved must be a list of the items the person approved",
        ),
    ]
    refusals: list[tuple[str, str, int]] = []
    for action, tamper, wanted_words in tampers:
        bad = copy.deepcopy(file)
        tamper(next(e for e in bad["events"] if e["action"] == action))
        scratch = make_project(register())
        async with connect(scratch.mcp_url, scratch.key) as client:
            said = words(await refused(client, "import_graph", document=bad))
            refusals.append((wanted_words, said, len((await ok(client, "list_nodes"))["items"])))
    proof(
        "M53",
        "a file whose approvals were tampered with (missing, not a list, not references) is "
        "refused in words and imports nothing",
        all(wanted_words in said and count == 0 for wanted_words, said, count in refusals),
        f"{len(refusals)} tampered files refused for {refs['tyres']}",
    )


async def the_tools_are_as_they_were() -> None:
    """No tool was added: the tool lists, who may call what and the standard set are as before, and
    the new arguments are in the tools' own schemas."""
    owner = register()
    project = make_project(owner)
    known = owner.call("GET", "/board/api/tools")
    by_server = {t["name"]: t for t in known.body["tools"]}
    standard = make_limited_key(project, "standard", None)
    async with connect(project.mcp_url, standard) as agent:
        offered = {tool.name for tool in (await agent.list_tools()).tools}
    async with connect(project.mcp_url, project.key) as agent:
        every = {tool.name: tool for tool in (await agent.list_tools()).tools}
    asking = every.get("request_plan_changes")
    deciding = every.get("decide_placement")
    proof(
        "M53",
        "the tools did not grow: asking for changes and deciding a move are still people's "
        "(no key can have either), the standard set is still within the most clients take and "
        "holds neither, and the new arguments, `approved` on one and `items` on the other, are in "
        "the schemas the server publishes (the board calls them, and the code-mode types are made "
        "from them)",
        holds(
            by_server["request_plan_changes"]["people_only"],
            by_server["decide_placement"]["people_only"],
            by_server["request_plan_changes"]["board_calls"],
            by_server["decide_placement"]["board_calls"],
            "approved" in by_server["request_plan_changes"]["input_schema"]["properties"],
            "items" in by_server["decide_placement"]["input_schema"]["properties"],
            asking is None,
            deciding is None,
            not offered & {"request_plan_changes", "decide_placement", "revise_plan"},
            len(offered) <= known.body["most_agent_tools"],
        ),
        f"{len(by_server)} tools, {len(offered)} offered to a standard key{failing()}",
    )


async def the_example_in_the_docs() -> None:
    """The TypeScript example of docs/agents.md for reading `blame` runs in code mode as written
    (with the plan's number put in): it lists the items a person has not approved, each with what
    they said, and leaves the approved ones out."""
    blocks = re.findall(r"```ts\n(.*?)```", DOCS.read_text(encoding="utf-8"), flags=re.S)
    (example,) = [b for b in blocks if b.startswith("// Plan 1402 waits for its agent. List")]
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        ask(
            project,
            owner,
            number,
            1,
            "Name the tyres, and the staff bikes do not need spare chains.",
            comments=[
                {"item": refs["tyres"], "field": "title", "text": "Say which tyres."},
                {"item": refs["fit"], "field": "body", "text": "How many hours is a bike off?"},
                {"item": refs["chains"], "text": "Staff bikes do not need spare chains."},
                {"item": refs["covers"], "field": "size", "text": "Is m enough?"},
            ],
            approved=[refs["pick"], refs["order"]],
        )
        before = await read(agent, number)
    async with connect(project.mcp_url, make_key(project, "coder", code_mode=True)) as coder:
        result = await ok(coder, "run", code=example.replace("plan: 1402", f"plan: {number}"))
    async with connect(project.mcp_url, project.key) as agent:
        now = await read(agent, number)
    maker = before["revisions"][0]["by"]
    wanted = [
        {
            "item": refs[key],
            "state": state,
            "lastChange": f"added in revision 1 by {maker}",
            "comments": [text] if text else [],
        }
        for key, state, text in (
            ("tyres", "asked", "Say which tyres."),
            ("covers", "asked", "Is m enough?"),
            ("battery", "none", ""),
            ("fit", "asked", "How many hours is a bike off?"),
            ("chains", "asked", "Staff bikes do not need spare chains."),
        )
    ]
    proof(
        "M53",
        "the example in docs/agents.md for reading blame, run as written in code mode, lists the "
        "items of a plan that waits for its agent that a person has not approved, in the order "
        "the server lists items, each with what a person said (read from the feedback that "
        "review.feedback names), when and by whom it was last changed, and leaves the two "
        "approved items out; and it changed nothing",
        holds(
            result["result"] == wanted,
            len(result["result"]) == 5,
            refs["pick"] not in {one["item"] for one in result["result"]},
            unchecked(now) == unchecked(before),
        ),
        clip(str(result["result"]), 100) + failing(),
    )


async def granular() -> None:
    chapter("Granular review: approve line by line, blame and moves in part (M53)")
    await approving_lines()
    await a_request_that_does_both_is_refused()
    await what_carries_over()
    await blame_equals_the_changes()
    await a_removed_line_can_be_agreed_to_or_asked_for()
    await a_move_accepted_in_part()
    await what_travels()
    await the_tools_are_as_they_were()
    await the_example_in_the_docs()
    # The page (tests/acceptance_granular_page.py) is imported where it is run, since it builds on
    # this file.
    from tests.acceptance_granular_page import granular_on_the_page  # noqa: PLC0415

    await granular_on_the_page()
