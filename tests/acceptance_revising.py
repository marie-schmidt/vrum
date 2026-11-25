"""Chapter of the acceptance scenario for review conversations and revised plans (M51): a person
reads a drafted plan and, instead of approving or rejecting it, asks for changes with a summary and
comments on its lines; the same plan stays held while its agent reads the feedback and sends a
revision; the person reads what changed and decides. Told from outside, through a real MCP client
(the agents) and the board's own calls as the people of a project, and then a headless browser on
the Needs a person page and the plan's own page (tests/acceptance_revising_page.py).

Each proof works in a project of its own. The scripted embedding service of the stack stands in for
the model, so nothing here depends on a real one."""

import copy
import re
from collections.abc import Callable
from typing import Any

from mcp import Client

from tests.acceptance_scenario import chapter, clip, proof
from tests.helpers import REPO_ROOT, connect, export_everything, ok, refused
from tests.world import (
    Person,
    Project,
    join_project,
    make_key,
    make_limited_key,
    make_project,
    register,
)

DOCS = REPO_ROOT / "docs" / "agents.md"
FIELDS = (
    "title",
    "body",
    "priority",
    "size",
    "start_on",
    "target_on",
    "parent",
    "links",
    "criteria",
)


def words(refusal: str) -> str:
    return refusal.split(": ", 1)[1] if refusal.startswith("Error executing tool") else refusal


def person(project: Project, who: Person, tool: str, **arguments: Any) -> Any:
    """What the board does when a person presses a button: the tool, as that person."""
    return who.call("POST", f"/board/api/projects/{project.id}/tool/{tool}", arguments)


def the_plan(epic: str) -> list[dict[str, Any]]:
    """Two stories under an epic that exists, three tasks, details on a story and a task."""
    return [
        {
            "key": "tyres",
            "kind": "story",
            "title": "Studded tyres",
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
            "body": "At the depot.\nOne morning each.",
        },
        {"key": "covers", "kind": "story", "title": "Weather covers", "parent": epic, "size": "m"},
        {"key": "order", "kind": "task", "title": "Order covers", "parent": "covers", "size": "xs"},
    ]


LINKS = [{"source": "fit", "target": "pick", "link_type": "needs_first"}]
RULES = [{"title": "Every bike passes the grip test", "node": "fit"}]


async def an_epic(agent: Client) -> str:
    return str((await ok(agent, "create_node", kind="epic", title="Winter fleet"))["ref"])


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


def revision(
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


async def read(agent: Client, plan: int, **more: Any) -> dict[str, Any]:
    (found,) = (await ok(agent, "list_plans", plan=plan, **more))["items"]
    return dict(found)


async def held_by_the_plan(agent: Client, plan: int, refs: dict[str, str]) -> bool:
    """Every item is held by the plan: none is offered as ready and `claim_node` refuses each one
    in words."""
    free = {n["ref"] for n in (await ok(agent, "ready_work", limit=200))["items"]}
    held = [(await ok(agent, "get_node", node=ref))["held_by"] for ref in refs.values()]
    said = [words(await refused(agent, "claim_node", node=ref)) for ref in refs.values()]
    return (
        not free & set(refs.values())
        and all(by == [f"plan:{plan}"] for by in held)
        and all("held" in one or "needs" in one for one in said)
    )


async def held_and_untouchable(agent: Client, plan: int, refs: dict[str, str]) -> bool:
    """The same, and nothing at all is offered to take: `next_work` finds nothing, which is true
    only while every plan in the project is held."""
    return (
        await held_by_the_plan(agent, plan, refs)
        and not (await ok(agent, "next_work", peek=True))["found"]
    )


def parts(item: dict[str, Any]) -> dict[str, Any]:
    """What a revision is compared by, worked out here from what `list_plans` shows of an item."""
    return {
        "title": item["title"],
        "body": item["body"],
        "priority": item["priority"],
        "size": item["size"],
        "start_on": item["start_on"],
        "target_on": item["target_on"],
        "parent": item["parent"],
        "links": sorted(f"{one['link_type']} {one['target']}" for one in item["links"]),
        "criteria": sorted([one["id"], one["title"], one["body"]] for one in item["criteria"]),
    }


def the_real_difference(
    old: list[dict[str, Any]], new: list[dict[str, Any]]
) -> dict[str, tuple[str, list[str], dict[str, Any]]]:
    """How the second list of items differs from the first, by reference: the change, which parts
    and what they were."""
    before = {one["ref"]: one for one in old}
    after = {one["ref"]: one for one in new}
    found: dict[str, tuple[str, list[str], dict[str, Any]]] = {}
    for ref in sorted({*before, *after}):
        if ref not in before:
            found[ref] = ("added", [], {})
        elif ref not in after:
            found[ref] = ("removed", [], {})
        else:
            then, now = parts(before[ref]), parts(after[ref])
            changed = [name for name in FIELDS if then[name] != now[name]]
            if changed:
                found[ref] = ("changed", changed, {name: then[name] for name in changed})
    return found


def shown_changes(plan: dict[str, Any]) -> dict[str, tuple[str, list[str], dict[str, Any]]]:
    return {c["ref"]: (c["change"], c["fields"], c["before"]) for c in plan["changes"]}


async def the_review_loop() -> None:
    """Ask for changes, read the feedback as the agent, revise the same plan, read what changed,
    refuse a stale decision and approve the revision that was read."""
    owner = register()
    project = make_project(owner)
    second_agent = make_key(project, "lead-agent")
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        other, other_refs = await drafted(agent, epic, "Another plan")
        first = await read(agent, number)
        asked = person(
            project,
            owner,
            "request_plan_changes",
            plan=number,
            revision=1,
            summary="Name the tyres and say how long a bike is off the street.",
            comments=[
                {"item": refs["tyres"], "field": "title", "text": "Say which tyres."},
                {"item": refs["fit"], "field": "body", "text": "How many hours is a bike off?"},
                {"item": refs["order"], "text": "Why is this the smallest task?"},
            ],
        )
        feed = (await ok(agent, "changes_since", limit=200))["items"]
        (event,) = [e for e in feed if e["action"] == "plan_changes_requested"]
        waiting = await read(agent, number)
        listed = await ok(agent, "list_plans", status="changes_requested")
        needs = await ok(agent, "needs_a_person")
        given = waiting["feedback"][0] if waiting["feedback"] else {}
        proof(
            "M51",
            "a person asks for changes to a drafted plan with a summary and three comments (on a "
            "story's title, on a task's details and on a whole task): the answer is the same plan "
            "now waiting for its agent, with the person's name and who they are, and each comment "
            "keeps the words it was about as they were in revision 1; the agent reads it as the "
            "plan's feedback and as an event in the change feed (made by the person, with the "
            "summary and the comments); the plan is on the list of plans waiting for a revision "
            "and in no row of what needs a person; and every item stays held by the plan, so "
            "nothing of it is ready, next_work finds nothing and claim_node refuses each item",
            asked.status == 200
            and (asked.body["state"], asked.body["revision"]) == ("changes_requested", 1)
            and (given.get("by"), given.get("by_kind")) == (owner.name, "person")
            and [(c["item"], c["field"], c["text"], c["quoted"]) for c in given["comments"]]
            == [
                (refs["tyres"], "title", "Say which tyres.", "Studded tyres"),
                (
                    refs["fit"],
                    "body",
                    "How many hours is a bike off?",
                    "At the depot.\nOne morning each.",
                ),
                (refs["order"], None, "Why is this the smallest task?", "Order covers"),
            ]
            and event["actor"] == owner.name
            and event["actor_kind"] == "person"
            and event["after"]["plan"] == number
            and event["after"]["summary"] == given["summary"]
            and event["after"]["comments"] == given["comments"]
            and waiting["state"] == "changes_requested"
            and waiting["revisions"][0]["feedback"] == given["id"]
            and [p["id"] for p in listed["items"]] == [number]
            and needs["total"] == 1
            and all(row["plan"]["id"] == other for g in needs["groups"] for row in g["items"])
            and waiting["checks"] is None
            and await held_and_untouchable(agent, number, refs),
            f"{len(given['comments'])} comments by {owner.name}",
        )

    async with connect(project.mcp_url, second_agent) as lead:
        revised = await ok(
            lead,
            "revise_plan",
            plan=number,
            note="Named the tyres and said how long a bike is off the street.",
            items=revision(
                epic,
                refs,
                tyres={"title": "Studded tyres for the river path and the hill route"},
                fit={"body": "At the depot.\nFour hours each, from 8 until noon."},
            ),
        )
    async with connect(project.mcp_url, project.key) as agent:
        now = await read(agent, number)
        old = await read(agent, number, revision=1)
        again = await ok(agent, "needs_a_person")
        (row,) = [i for g in again["groups"] for i in g["items"] if i["plan"]["id"] == number]
        proof(
            "M51",
            "another agent revises the same plan, sending the whole plan again with a note: the "
            "plan keeps its number and its items, now at revision 2 made by that agent, back to "
            "waiting for a person with its row on what needs a person saying revision 2; the "
            "first revision still reads exactly as it was read before (its items, its context, "
            "its words) and the person's feedback on it has not changed; revision 1 carries no "
            "checks and revision 2 does; and all of its items are still held, so none is ready, "
            "none can be claimed and next_work finds nothing",
            (revised["plan"], revised["revision"]) == (number, 2)
            and revised["removed"] == []
            and revised["counts"]["kept"] == 5
            and (now["state"], now["revision"], now["revised_by"], now["revised_by_kind"])
            == ("proposed", 2, "lead-agent", "agent")
            and now["proposed_by"] != now["revised_by"]
            and [r["revision"] for r in now["revisions"]] == [1, 2]
            and now["revisions"][1]["note"]
            == "Named the tyres and said how long a bike is off the street."
            and now["revisions"][0] == waiting["revisions"][0]
            and old["tree"] == first["tree"]
            and old["context"] == first["context"]
            and old["shown"] == 1
            and old["checks"] is None
            and now["checks"] is not None
            and now["feedback"] == waiting["feedback"]
            and row["plan"]["revision"] == 2
            and "revision 2" in row["reason"]
            and await held_and_untouchable(agent, number, refs),
            f"plan {number} at revision {now['revision']} by {now['revised_by']}",
        )

        difference = the_real_difference(old["tree"], now["tree"])
        shown = shown_changes(now)
        live = {ref: await ok(agent, "get_node", node=ref) for ref in shown}
        proof(
            "M51",
            "what changed since the revision before is exactly the real difference between the "
            "two revisions' items: two items changed, the title of the story and the details of "
            "the "
            "task, each with its old words, worked out here from the two lists of items and not "
            "from what the server says changed; the new words are what get_node says now, and "
            "nothing else is said to have changed",
            shown == difference
            and set(shown) == {refs["tyres"], refs["fit"]}
            and shown[refs["tyres"]][1] == ["title"]
            and shown[refs["fit"]][1] == ["body"]
            and shown[refs["tyres"]][2] == {"title": "Studded tyres"}
            and shown[refs["fit"]][2] == {"body": "At the depot.\nOne morning each."}
            and live[refs["tyres"]]["title"]
            == "Studded tyres for the river path and the hill route"
            and live[refs["fit"]]["body"] == "At the depot.\nFour hours each, from 8 until noon."
            and now["drift"] == [],
            clip(str(shown), 90),
        )

        refusals = []
        for tool, more in (
            ("approve_plan", {"approved": True}),
            ("approve_plan", {"approved": False, "reason": "No."}),
            ("request_plan_changes", {"summary": "Not good enough."}),
        ):
            late = person(project, owner, tool, plan=number, revision=1, **more)
            refusals.append((late.status, late.body["message"]))
        unchanged = await read(agent, number)
        proof(
            "M51",
            "a decision applies only to the revision the person reviewed: after the agent revised, "
            "an approval, a rejection and a request for changes that name revision 1 are each "
            "refused in words that say the plan is at revision 2 now and nothing was changed, "
            "and the plan is exactly as it was",
            all(
                status == 400
                and "is at revision 2 now, and you reviewed revision 1" in said
                and "Nothing was changed" in said
                for status, said in refusals
            )
            and (unchanged["state"], unchanged["revision"], unchanged["feedback"])
            == ("proposed", 2, waiting["feedback"])
            and await held_and_untouchable(agent, number, refs),
            clip(refusals[0][1], 90),
        )

        approved = person(project, owner, "approve_plan", plan=number, approved=True, revision=2)
        decided = await read(agent, number)
        ready = {n["ref"] for n in (await ok(agent, "ready_work", limit=200))["items"]}
        free_now = [(await ok(agent, "get_node", node=ref))["held_by"] for ref in refs.values()]
        proof(
            "M51",
            "the person approves the revision they read, as themselves: it frees the five items of "
            "that plan (none is held any more, and the ones that need nothing first are ready) "
            "and nothing of the other plan, whose items stay held and cannot be claimed; the plan "
            "reads as approved at revision 2, with the same tree as when it was read, and what "
            "was said about revision 1 is still there",
            approved.status == 200
            and (approved.body["state"], approved.body["revision"]) == ("approved", 2)
            and approved.body["decided_by"] == owner.name
            and free_now == [[]] * 5
            and {refs["pick"], refs["order"]} <= ready
            and refs["fit"] not in ready
            and decided["state"] == "approved"
            and decided["tree"] == now["tree"]
            and decided["feedback"] == waiting["feedback"]
            and decided["checks"] is None
            and await held_by_the_plan(agent, other, other_refs),
            f"decided by {approved.body['decided_by']}",
        )


async def a_requested_removal() -> None:
    """A reviewer asks for an item to go; the agent removes it in a revision; approving what is
    left never frees it."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        first = await read(agent, number)
        asked = person(
            project,
            owner,
            "request_plan_changes",
            plan=number,
            revision=1,
            summary="Drop the covers task, it is another project.",
            comments=[{"item": refs["order"], "text": "Remove this one."}],
        )
        revised = await ok(
            agent,
            "revise_plan",
            plan=number,
            items=revision(epic, refs, leave_out=("order",)),
            note="Removed the covers task.",
        )
        now = await read(agent, number)
        gone = await ok(agent, "get_node", node=refs["order"])
        before = await read(agent, number, revision=1)
        approved = person(project, owner, "approve_plan", plan=number, approved=True, revision=2)
        ready = {n["ref"] for n in (await ok(agent, "ready_work", limit=200))["items"]}
        after = await ok(agent, "get_node", node=refs["order"])
        claim = words(await refused(agent, "claim_node", node=refs["order"]))
        proof(
            "M51",
            "a reviewer asks for an item to be removed and the agent removes it: the revision says "
            "which item it took out, the item is dropped with a handoff note that names the "
            "revision and the agent's note, it is gone from the plan's items and shown as removed "
            "in what changed, and revision 1 still has it exactly as it was read; the plan "
            "counts its stories and tasks without it",
            asked.status == 200
            and revised["removed"] == [refs["order"]]
            and revised["counts"]["removed"] == 1
            and gone["status"] == "dropped"
            and gone["last_handoff"]["note"]
            == f"Removed in revision 2 of Plan {number}: Removed the covers task."
            and refs["order"] not in [i["ref"] for i in now["tree"]]
            and shown_changes(now) == {refs["order"]: ("removed", [], {})}
            and now["revisions"][1]["removed"] == [refs["order"]]
            and refs["order"] in [i["ref"] for i in before["tree"]]
            and before["tree"] == first["tree"]
            and now["tasks"] == 2,
            f"removed {revised['removed']}",
        )
        proof(
            "M51",
            "approving the remaining plan frees only what is left: the removed item stays dropped, "
            "is not ready and cannot be claimed, and no later approval can bring it back",
            approved.status == 200
            and after["status"] == "dropped"
            and refs["order"] not in ready
            and refs["pick"] in ready
            and (await ok(agent, "get_node", node=refs["pick"]))["held_by"] == [],
            clip(claim, 90),
        )


async def a_revision_that_is_refused() -> None:
    """A revision with problems is refused whole, with every problem listed, and changes nothing."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        person(project, owner, "request_plan_changes", plan=number, revision=1, summary="Smaller.")
        events = int((await ok(agent, "changes_since", newest=True, limit=1))["latest_event_id"])
        nodes = (await ok(agent, "list_nodes", limit=200, detail=True))["items"]
        plan = await read(agent, number)
        bad = revision(
            epic,
            refs,
            leave_out=("tyres",),
            pick={"size": "xxl"},
            covers={"title": " "},
            fit={"parent": refs["tyres"]},
        )
        bad.append({"key": "stray", "kind": "task", "title": "Stray", "parent": "nowhere"})
        bad.append(
            {
                "key": "ghost",
                "kind": "task",
                "title": "Ghost",
                "ref": "task:99999",
                "parent": "covers",
            }
        )
        said = words(
            await refused(
                agent,
                "revise_plan",
                plan=number,
                items=bad,
                links=[
                    {"source": "pick", "target": "order", "link_type": "needs_first"},
                    {"source": "order", "target": "pick", "link_type": "needs_first"},
                ],
            )
        )
        after_events = int(
            (await ok(agent, "changes_since", newest=True, limit=1))["latest_event_id"]
        )
        empty = words(await refused(agent, "revise_plan", plan=number, items=[]))
        missing = words(await refused(agent, "revise_plan", plan=9999, items=revision(epic, refs)))
        kind = words(
            await refused(
                agent,
                "revise_plan",
                plan=number,
                items=revision(epic, refs, pick={"kind": "story"}),
            )
        )
        proof(
            "M51",
            "a malformed revision changes nothing: it is refused in one message that lists every "
            "problem (a size that does not exist, an empty title, an item that is taken out while "
            "something still hangs under it, a key that names nothing, a reference that is not in "
            "the plan, a circle of waiting) and says nothing was changed; not one event, node or "
            "word of the plan is different afterwards, and an empty revision, a plan that does "
            "not exist and an item that changes what it is are each refused in words",
            "The plan was not revised, because of" in said
            and "Nothing was changed" in said
            and "size must be one of" in said
            and "title cannot be empty" in said
            and "which this revision removes" in said
            and "is not the key of any item in this plan" in said
            and "task:99999 is not an item of Plan" in said
            and "circle of waiting" in said
            and after_events == events
            and (await ok(agent, "list_nodes", limit=200, detail=True))["items"] == nodes
            and await read(agent, number) == plan
            and "at least one item" in empty
            and "no plan 9999" in missing
            and "what an item is cannot change" in kind,
            clip(said, 100),
        )


async def how_a_plan_ends() -> None:
    """Rejecting works whether the plan waits for a person or for its agent, and says why;
    approving a plan that waits for its agent is refused."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        plain, plain_refs = await drafted(agent, epic, "Plain")
        waiting, waiting_refs = await drafted(agent, epic, "Waiting")
        revised, revised_refs = await drafted(agent, epic, "Revised")
        person(project, owner, "request_plan_changes", plan=waiting, revision=1, summary="No.")
        early = person(project, owner, "approve_plan", plan=waiting, approved=True, revision=1)
        person(project, owner, "request_plan_changes", plan=revised, revision=1, summary="Less.")
        await ok(
            agent,
            "revise_plan",
            plan=revised,
            items=revision(epic, revised_refs, leave_out=("order",)),
            note="Less.",
        )
        ended = [
            person(project, owner, "approve_plan", plan=n, approved=False, reason=why)
            for n, why in ((plain, "Not now."), (waiting, "Wrong season."), (revised, "Too late."))
        ]
        notes = {
            n: {
                key: (await ok(agent, "get_node", node=ref))["last_handoff"]["note"]
                for key, ref in refs.items()
            }
            for n, refs in ((plain, plain_refs), (waiting, waiting_refs), (revised, revised_refs))
        }
        statuses = [
            (await ok(agent, "get_node", node=ref))["status"]
            for refs in (plain_refs, waiting_refs, revised_refs)
            for ref in refs.values()
        ]
        after = words(
            await refused(agent, "revise_plan", plan=waiting, items=revision(epic, waiting_refs))
        )
        again = person(
            project, owner, "request_plan_changes", plan=waiting, revision=1, summary="x"
        )
        proof(
            "M51",
            "approving a plan that waits for its agent is refused in words (there is nothing to "
            "approve); rejecting ends a plan in either state, with the reason on every item it "
            "holds: a plan that waits for a person, one that waits for its agent, and one that "
            "was revised, where the item the revision removed keeps its own note (it was "
            "removed, not rejected); a rejected plan cannot be revised or asked for changes",
            early.status == 400
            and "there is nothing to approve" in early.body["message"]
            and all(r.status == 200 and r.body["state"] == "rejected" for r in ended)
            and set(notes[plain].values()) == {"The plan was rejected: Not now."}
            and set(notes[waiting].values()) == {"The plan was rejected: Wrong season."}
            and {k: v for k, v in notes[revised].items() if k != "order"}
            == {k: "The plan was rejected: Too late." for k in revised_refs if k != "order"}
            and notes[revised]["order"].startswith("Removed in revision 2 of Plan")
            and set(statuses) == {"dropped"}
            and "already rejected" in after
            and again.status == 400
            and "already rejected" in again.body["message"],
            clip(after, 90),
        )


async def only_a_person_decides() -> None:
    """An agent cannot ask for changes or approve; a viewer reads every revision and its feedback
    and cannot submit; an editor can; a person cannot revise."""
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer")
    editor = join_project(project, "editor")
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        by_agent = [
            words(
                await refused(
                    agent,
                    "request_plan_changes",
                    plan=number,
                    revision=1,
                    summary="I would like...",
                )
            ),
            words(await refused(agent, "approve_plan", plan=number, approved=True, revision=1)),
        ]
        blocked = [
            person(project, viewer, "request_plan_changes", plan=number, revision=1, summary="Hm."),
            person(project, viewer, "approve_plan", plan=number, approved=True, revision=1),
        ]
        by_hand = person(project, editor, "revise_plan", plan=number, items=revision(epic, refs))
        asked = person(
            project,
            editor,
            "request_plan_changes",
            plan=number,
            revision=1,
            summary="Smaller.",
            comments=[{"item": refs["pick"], "text": "Who?"}],
        )
        await ok(
            agent,
            "revise_plan",
            plan=number,
            items=revision(epic, refs, pick={"title": "Choose a supplier in Oulu"}),
        )
        latest = person(project, viewer, "list_plans", plan=number)
        earlier = person(project, viewer, "list_plans", plan=number, revision=1)
        listed = person(project, viewer, "list_plans", status="proposed")
        waiting = await read(agent, number)
        proof(
            "M51",
            "only a person decides: an agent is refused asking for changes and approving, a viewer "
            "reads the plan, every revision and the feedback on it but is refused both decisions "
            "(and nothing changed), an editor asks for changes as themselves, and a person has "
            "no way to revise a plan: that is the agent's act and the board has no route for it",
            all("not allowed to use" in said for said in by_agent)
            and all(r.status in (400, 403) for r in blocked)
            and by_hand.status in (400, 403, 404)
            and asked.status == 200
            and (asked.body["feedback"][0]["by"], asked.body["feedback"][0]["by_kind"])
            == (editor.name, "person")
            and latest.status == 200
            and latest.body["items"][0]["revision"] == 2
            and [f["id"] for f in latest.body["items"][0]["feedback"]]
            == [f["id"] for f in waiting["feedback"]]
            and earlier.status == 200
            and earlier.body["items"][0]["shown"] == 1
            and earlier.body["items"][0]["feedback"][0]["comments"][0]["quoted"]
            == "Choose a supplier"
            and listed.status == 200
            and listed.body["items"][0]["revision"] == 2
            and waiting["state"] == "proposed",
            f"viewer {viewer.name} read revision {latest.body['items'][0]['revision']}",
        )


async def a_plan_changed_outside_a_revision() -> None:
    """A held plan whose items were changed by other tools cannot be approved until a revision
    says what it is now; rejecting is never held back by it."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        clean = (await read(agent, number))["drift"]
        version = (await ok(agent, "get_node", node=refs["pick"]))["version"]
        await ok(
            agent,
            "update_node",
            node=refs["pick"],
            expected_version=version,
            title="Choose another supplier",
        )
        await ok(
            agent, "add_link", source=refs["order"], target=refs["tyres"], link_type="relates_to"
        )
        drifted = await read(agent, number)
        late = person(project, owner, "approve_plan", plan=number, approved=True, revision=1)
        held = await held_and_untouchable(agent, number, refs)
        await ok(
            agent,
            "revise_plan",
            plan=number,
            items=revision(epic, refs, pick={"title": "Choose another supplier"}),
        )
        now = await read(agent, number)
        approved = person(project, owner, "approve_plan", plan=number, approved=True, revision=2)
        second, second_refs = await drafted(agent, epic, "Second")
        version = (await ok(agent, "get_node", node=second_refs["fit"]))["version"]
        await ok(
            agent,
            "update_node",
            node=second_refs["fit"],
            expected_version=version,
            title="Sneaky",
        )
        rejected = person(project, owner, "approve_plan", plan=second, approved=False, reason="No.")
        number_of = refs["pick"].split(":")[1]
        proof(
            "M51",
            "a held plan whose item was changed by another tool after the revision was submitted "
            "says so in `drift` (the item and which part; a link that makes nothing wait is not "
            "counted), the server refuses to approve it in words that name the item, everything "
            "stays held, a revision says what the plan is now and brings it back in line so it "
            "can be approved, and rejecting a plan that drifted is never refused",
            clean == []
            and drifted["drift"] == [{"ref": refs["pick"], "fields": ["title"]}]
            and late.status == 400
            and "was changed after revision 1 was submitted" in late.body["message"]
            and f"T-{number_of} (title)" in late.body["message"]
            and held
            and now["drift"] == []
            and {c["ref"]: c["fields"] for c in now["changes"]}
            == {refs["pick"]: ["title"], refs["order"]: ["links"]}
            and approved.status == 200
            and rejected.status == 200,
            clip(late.body["message"], 90),
        )

        # A reviewer cannot take an item out themselves and then approve the rest.
        third, third_refs = await drafted(agent, epic, "Third")
        version = (await ok(agent, "get_node", node=third_refs["order"]))["version"]
        dropped = person(
            project,
            owner,
            "update_node",
            node=third_refs["order"],
            expected_version=version,
            status="dropped",
            why="Not this one.",
        )
        remainder = person(project, owner, "approve_plan", plan=third, approved=True, revision=1)
        by_hand = await read(agent, third)
        proof(
            "M51",
            "a reviewer who takes an item out of a held plan themselves cannot then approve the "
            "rest: the plan says the item was dropped outside a revision, the server refuses the "
            "approval in words that name it, and the other items stay held, so removing an item "
            "is something the agent does in a revision, which the person then reads",
            dropped.status == 200
            and by_hand["drift"] == [{"ref": third_refs["order"], "fields": ["dropped"]}]
            and remainder.status == 400
            and "was changed after revision 1 was submitted" in remainder.body["message"]
            and "dropped" in remainder.body["message"]
            and by_hand["state"] == "proposed"
            and await held_by_the_plan(
                agent, third, {k: v for k, v in third_refs.items() if k != "order"}
            ),
            clip(remainder.body["message"], 90),
        )


async def revisions_and_feedback_travel() -> None:
    """Submitted revisions and the feedback on them survive an export and an import into another
    project, and a file that was tampered with is refused with nothing imported."""
    owner = register()
    source = make_project(owner)
    async with connect(source.mcp_url, source.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        person(
            source,
            owner,
            "request_plan_changes",
            plan=number,
            revision=1,
            summary="Smaller, please.",
            comments=[{"item": refs["order"], "text": "Drop this."}],
        )
        await ok(
            agent,
            "revise_plan",
            plan=number,
            items=revision(epic, refs, leave_out=("order",)),
            note="Dropped it.",
        )
        waiting = await read(agent, number)
        first = await read(agent, number, revision=1)
        file = await export_everything(agent)
    actions = {e["action"] for e in file["events"]}

    target = make_project(register())
    async with connect(target.mcp_url, target.key) as client:
        await ok(client, "import_graph", document=file)
        loaded = await read(client, number)
        again = await read(client, number, revision=1)
        held = {
            n["ref"]: n["held_by"] for n in (await ok(client, "list_nodes", limit=200))["items"]
        }
        removed = await ok(client, "get_node", node=refs["order"])
        wanted = ("plan_proposed", "plan_revised", "plan_changes_requested")
        again_file = await export_everything(client)
        proof(
            "M51",
            "submitted revisions and feedback survive an export and an import into another "
            "project: the plan reads the same there (state, revision, who made each revision, "
            "every note, the feedback with the words each comment was about, what changed, and "
            "revision 1 exactly as it was), it is still held, the removed item is still "
            "dropped, and exporting it again gives the same events",
            {"plan_proposed", "plan_revised", "plan_changes_requested"} <= actions
            and loaded["state"] == "proposed"
            and loaded["revision"] == 2
            and loaded["revisions"] == waiting["revisions"]
            and loaded["feedback"] == waiting["feedback"]
            and loaded["tree"] == waiting["tree"]
            and loaded["changes"] == waiting["changes"]
            and again["tree"] == first["tree"]
            and held[refs["pick"]] == [f"plan:{number}"]
            and removed["status"] == "dropped"
            and [e["after"] for e in again_file["events"] if e["action"] in wanted]
            == [e["after"] for e in file["events"] if e["action"] in wanted],
            f"{len(loaded['revisions'])} revisions, {len(loaded['feedback'])} feedback",
        )
        approved_there = person(
            target, target.owner, "approve_plan", plan=number, approved=True, revision=2
        )
        proof(
            "M51",
            "a person in the project that was imported into approves the revision they read there "
            "and its remaining work is free, so an imported conversation can be finished",
            approved_there.status == 200
            and (await ok(client, "get_node", node=refs["pick"]))["held_by"] == [],
            f"approved by {target.owner.name}",
        )

    approved_file = copy.deepcopy(file)
    approved_file["events"].append(
        {
            **next(e for e in file["events"] if e["action"] == "plan_changes_requested"),
            "id": max(e["id"] for e in file["events"]) + 1,
            "action": "plan_approved",
            "after": {"plan": number, "revision": 2},
        }
    )
    tampers: list[tuple[str, Callable[[dict[str, Any]], object], str]] = [
        ("plan_revised", lambda e: e["after"].__setitem__("revision", 1), "revision must be"),
        ("plan_revised", lambda e: e["after"].pop("snapshot"), "snapshot must list"),
        ("plan_proposed", lambda e: e["after"].pop("snapshot"), "snapshot must list"),
        ("plan_changes_requested", lambda e: e.__setitem__("actor_kind", "agent"), "only a person"),
        (
            "plan_changes_requested",
            lambda e: e["after"].__setitem__("summary", " "),
            "needs its summary",
        ),
        (
            "plan_changes_requested",
            lambda e: e["after"].__setitem__("plan", 9999),
            "is not a plan in the file",
        ),
        ("plan_approved", lambda e: e["after"].pop("revision", None), "revision must be"),
    ]
    refusals: list[tuple[str, str, int]] = []
    for action, tamper, wanted_words in tampers:
        bad = copy.deepcopy(approved_file)
        tamper(next(e for e in bad["events"] if e["action"] == action))
        scratch = make_project(register())
        async with connect(scratch.mcp_url, scratch.key) as client:
            said = words(await refused(client, "import_graph", document=bad))
            refusals.append((wanted_words, said, len((await ok(client, "list_nodes"))["items"])))
    twice = copy.deepcopy(file)
    repeat = next(e for e in twice["events"] if e["action"] == "plan_revised")
    twice["events"].append({**repeat, "id": max(e["id"] for e in twice["events"]) + 1})
    scratch = make_project(register())
    async with connect(scratch.mcp_url, scratch.key) as client:
        repeated = words(await refused(client, "import_graph", document=twice))
    proof(
        "M51",
        "a file whose conversation was tampered with is refused in words and imports nothing: a "
        "revision with the wrong number or no snapshot, a proposal with no snapshot, a request "
        "for changes made by an agent, with no summary or about a plan that is not in the file, a "
        "decision with no revision, and a revision made twice",
        all(wanted_words in said and count == 0 for wanted_words, said, count in refusals)
        and "is made twice" in repeated,
        f"{len(refusals) + 1} tampered files refused",
    )


async def the_tools_of_the_conversation() -> None:
    """The tool list: asking for changes is a person's and on the board, revising is an agent's
    and not, and neither is in the standard set."""
    owner = register()
    project = make_project(owner)
    known = owner.call("GET", "/board/api/tools")
    by_server = {t["name"]: t for t in known.body["tools"]}
    standard = make_limited_key(project, "standard", None)
    async with connect(project.mcp_url, standard) as agent:
        offered = {tool.name for tool in (await agent.list_tools()).tools}
        said = words(await refused(agent, "request_plan_changes", plan=1, revision=1, summary="x"))
    async with connect(project.mcp_url, project.key) as agent:
        every = {tool.name: tool for tool in (await agent.list_tools()).tools}
    asking, revising, listing = (
        by_server["request_plan_changes"],
        by_server["revise_plan"],
        by_server["list_plans"],
    )
    schema = every["list_plans"].input_schema["properties"]
    proof(
        "M51",
        "the tools of the conversation are on the server as the server lists them: asking for "
        "changes is for people only (no key can have it) and is called by the board, revising "
        "is an agent's tool that the board has no route for, list_plans takes the revision and "
        "the state that waits for the agent, neither of the two is in the standard set, which "
        "is still no longer than the most clients take, and a standard key that asks for changes "
        "is told it is not one of the tools it is offered",
        asking["people_only"]
        and asking["board_calls"]
        and not revising["people_only"]
        and not revising["board_calls"]
        and bool(revising["agents_only"])
        and not listing["people_only"]
        and "revision" in schema
        and "changes_requested" in schema["status"]["description"]
        and not offered & {"request_plan_changes", "revise_plan", "list_plans"}
        and len(offered) <= known.body["most_agent_tools"]
        and "revise_plan" in every
        and "request_plan_changes" not in every
        and "is not one of the tools Vrum offers" in said,
        f"{len(by_server)} tools, {len([t for t in by_server.values() if t['people_only']])} "
        "for people only",
    )


async def the_example_in_the_docs() -> None:
    """The TypeScript example of docs/agents.md for revising a plan runs in code mode as written
    (with the plan's number put in): it reads the feedback and sends a revision that continues
    every item and rewords the one title the person commented on."""
    blocks = re.findall(r"```ts\n(.*?)```", DOCS.read_text(encoding="utf-8"), flags=re.S)
    (example,) = [b for b in blocks if b.startswith("// Plan 1402 waits for its agent. Read what")]
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        person(
            project,
            owner,
            "request_plan_changes",
            plan=number,
            revision=1,
            summary="Name the stations.",
            comments=[
                {"item": refs["covers"], "field": "title", "text": "Which stations?"},
                {"item": refs["fit"], "field": "body", "text": "Hours, please."},
            ],
        )
        before = await read(agent, number)
    async with connect(project.mcp_url, make_key(project, "coder", code_mode=True)) as coder:
        result = await ok(coder, "run", code=example.replace("plan: 1402", f"plan: {number}"))
        again = await ok(coder, "run", code=example.replace("plan: 1402", f"plan: {number}"))
    async with connect(project.mcp_url, project.key) as agent:
        now = await read(agent, number)
    old = {i["ref"]: i for i in before["tree"]}
    new = {i["ref"]: i for i in now["tree"]}
    changed = shown_changes(now)
    proof(
        "M51",
        "the example in docs/agents.md for revising a plan, run as written in code mode, reads "
        "the feedback of a plan that waits for its agent and sends revision 2 of the same plan: "
        "every item is continued with the words it had, the one title a person commented on is "
        "reworded and says so in the note, nothing is removed, the links and the criteria are "
        "kept, nothing was changed outside a revision, and what changed is exactly that title; "
        "run again, it says there is nothing to revise",
        result["result"]["revision"] == 2
        and result["result"]["removed"] == []
        and (now["state"], now["revision"]) == ("proposed", 2)
        and new[refs["covers"]]["title"] == "Weather covers, with the stations named"
        and all(parts(new[ref]) == parts(old[ref]) for ref in old if ref != refs["covers"])
        and parts(new[refs["covers"]])["parent"] == parts(old[refs["covers"]])["parent"]
        and set(changed) == {refs["covers"]}
        and changed[refs["covers"]][1] == ["title"]
        and now["revisions"][1]["note"].startswith(f"Reworded the title of {refs['covers']}")
        and now["drift"] == []
        and again["result"] == "Nothing to revise.",
        clip(str(result["result"]), 100),
    )


async def revising() -> None:
    chapter("Review conversations: comments, request changes and revised plans (M51)")
    await the_review_loop()
    await a_requested_removal()
    await a_revision_that_is_refused()
    await how_a_plan_ends()
    await only_a_person_decides()
    await a_plan_changed_outside_a_revision()
    await revisions_and_feedback_travel()
    await the_tools_of_the_conversation()
    await the_example_in_the_docs()
    # The page (tests/acceptance_revising_page.py) is imported where it is run, since it builds on
    # this file.
    from tests.acceptance_revising_page import revising_on_the_page  # noqa: PLC0415

    await revising_on_the_page()
