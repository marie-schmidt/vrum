"""M51, against the running containers, through a real MCP client and the board's HTTP API.

A drafted plan is reviewed as revisions: a person reads one and decides, or asks for changes with a
summary and comments on its lines; the plan stays held, with the same number, and the agent sends a
revision that a person reads again. Each revision is kept whole, with the feedback on it, so it can
be read as it was; a decision applies only to the revision the person reviewed; a revision that is
refused changes nothing; and a requested removal drops the item and never frees it. Each test works
in a project of its own."""

import copy
from collections.abc import Callable
from typing import Any

import pytest
from mcp import Client

from tests.helpers import connect, export_everything, ok, refused, run_sql
from tests.test_checks_integration import as_person, words_of
from tests.world import Person, Project, join_project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SPEC: list[dict[str, Any]] = [
    {"key": "e", "kind": "epic", "title": "Winter fleet"},
    {
        "key": "s1",
        "kind": "story",
        "title": "Studded tyres",
        "parent": "e",
        "size": "l",
        "body": "Fit studded tyres.\nForty bikes.",
    },
    {"key": "t1", "kind": "task", "title": "Choose a supplier", "parent": "s1", "size": "s"},
    {"key": "t2", "kind": "task", "title": "Fit forty bikes", "parent": "s1", "size": "m"},
    {"key": "s2", "kind": "story", "title": "Weather covers", "parent": "e", "size": "m"},
    {"key": "t3", "kind": "task", "title": "Order covers", "parent": "s2", "size": "xs"},
]
LINKS = [{"source": "t2", "target": "t1", "link_type": "needs_first"}]
RULES = [{"title": "Every bike passes the grip test", "node": "t2"}]


def person_call(project: Project, who: Person, tool: str, **arguments: Any) -> Any:
    return as_person(who, project, tool, **arguments)


async def drafted(agent: Client, title: str = "Winter plan") -> tuple[int, dict[str, str]]:
    made = await ok(
        agent, "create_plan", items=SPEC, links=LINKS, rules=RULES, draft=True, title=title
    )
    return int(made["plan"]), {c["key"]: c["ref"] for c in made["created"]}


def continuing(refs: dict[str, str], **changes: dict[str, Any]) -> list[dict[str, Any]]:
    """The plan's items as a revision that changes nothing, then the changes by key."""
    items = []
    for spec in SPEC:
        entry = {**copy.deepcopy(spec), "ref": refs[spec["key"]], **changes.get(spec["key"], {})}
        items.append(entry)
    return items


async def a_revision_of(
    agent: Client, plan: int, refs: dict[str, str], **changes: dict[str, Any]
) -> dict[str, Any]:
    """The agent's revision: every item continues and the ones named in `changes` differ."""
    sent = await ok(
        agent,
        "revise_plan",
        plan=plan,
        items=continuing(refs, **changes),
        note="Named the stations.",
    )
    return dict(sent)


async def read(agent: Client, plan: int, **more: Any) -> dict[str, Any]:
    page = await ok(agent, "list_plans", plan=plan, **more)
    (one,) = page["items"]
    found: dict[str, Any] = one
    return found


async def newest_event(agent: Client) -> int:
    page = await ok(agent, "changes_since", newest=True, limit=1)
    return int(page["latest_event_id"])


async def nothing_is_free(agent: Client, refs: dict[str, str]) -> None:
    """Every item is held, so nothing of it is offered or can be claimed."""
    free = {n["ref"] for n in (await ok(agent, "ready_work", limit=200))["items"]}
    assert not free & set(refs.values())
    assert (await ok(agent, "next_work", peek=True))["found"] is False
    for ref in refs.values():
        if not ref.startswith("epic"):
            said = words_of(await refused(agent, "claim_node", node=ref))
            assert "held" in said or "needs" in said, said


async def test_asking_for_changes_keeps_the_plan_held_and_a_revision_is_read_again() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        number, refs = await drafted(agent)
        first = await read(agent, number)
        assert (first["state"], first["revision"], first["shown"]) == ("proposed", 1, 1)
        assert first["changes"] is None and first["feedback"] == []
        assert [r["revision"] for r in first["revisions"]] == [1]

        asked = person_call(
            project,
            owner,
            "request_plan_changes",
            plan=number,
            revision=1,
            summary="Cover the busiest stations first.",
            comments=[
                {"item": refs["s2"], "field": "title", "text": "Say which stations."},
                {"item": refs["t3"], "text": "Why is this the smallest?"},
            ],
        )
        assert asked.status == 200, asked.body
        assert (asked.body["state"], asked.body["revision"]) == ("changes_requested", 1)
        given = asked.body["feedback"][0]
        assert given["by"] == owner.name and given["by_kind"] == "person"
        assert [(c["item"], c["field"], c["quoted"]) for c in given["comments"]] == [
            (refs["s2"], "title", "Weather covers"),
            (refs["t3"], None, "Order covers"),
        ]

        # The agent learns of it from the change feed and from the plan, with the plan still held.
        feed = (await ok(agent, "changes_since", limit=200))["items"]
        (event,) = [e for e in feed if e["action"] == "plan_changes_requested"]
        assert event["actor"] == owner.name and event["after"]["plan"] == number
        assert event["after"]["summary"] == "Cover the busiest stations first."
        waiting = await read(agent, number)
        assert waiting["state"] == "changes_requested"
        assert waiting["feedback"][0]["id"] == given["id"]
        assert waiting["revisions"][0]["feedback"] == given["id"]
        await nothing_is_free(agent, refs)
        # It is the agent's turn, not a person's: it is not on the list of what needs a person.
        assert (await ok(agent, "needs_a_person"))["total"] == 0
        assert (await ok(agent, "list_plans", status="changes_requested"))["total"] == 1
        assert (await ok(agent, "list_plans", status="proposed"))["total"] == 0

        revised = await ok(
            agent,
            "revise_plan",
            plan=number,
            note="Named the stations and added a measuring task.",
            items=[
                *continuing(
                    refs,
                    s2={"title": "Weather covers for the four busiest stations"},
                ),
                {
                    "key": "t4",
                    "kind": "task",
                    "title": "Measure the four stations",
                    "parent": "s2",
                    "size": "s",
                },
            ],
        )
        assert (revised["plan"], revised["revision"]) == (number, 2)
        assert len(revised["created"]) == 1 and revised["removed"] == []
        assert revised["counts"]["kept"] == 6 and revised["counts"]["created"] == 1
        refs["t4"] = revised["created"][0]

        # The same plan, read again by a person, with everything held.
        again = await read(agent, number)
        assert (again["state"], again["revision"]) == ("proposed", 2)
        assert again["revised_by_kind"] == "agent" and again["proposed_by_kind"] == "agent"
        assert [r["revision"] for r in again["revisions"]] == [1, 2]
        assert again["revisions"][1]["note"] == "Named the stations and added a measuring task."
        assert again["feedback"] == waiting["feedback"]
        changes = {c["ref"]: c for c in again["changes"]}
        assert changes[refs["s2"]]["change"] == "changed" and changes[refs["s2"]]["fields"] == [
            "title"
        ]
        assert changes[refs["s2"]]["before"] == {"title": "Weather covers"}
        assert changes[refs["t4"]]["change"] == "added" and len(changes) == 2
        await nothing_is_free(agent, refs)
        row = (await ok(agent, "needs_a_person"))["groups"][0]["items"][0]
        assert row["kind"] == "plan" and row["plan"]["revision"] == 2
        assert "revision 2" in row["reason"]

        # The earlier revision is still exactly what the person read, and so are their words.
        old = await read(agent, number, revision=1)
        assert old["shown"] == 1 and old["tree"] == first["tree"] and old["changes"] is None
        assert old["checks"] is None
        assert [t["title"] for t in old["tree"]].count("Weather covers") == 1
        assert [t["title"] for t in again["tree"]].count(
            "Weather covers for the four busiest stations"
        ) == 1
        assert "there is no revision 3" in words_of(
            await refused(agent, "list_plans", plan=number, revision=3)
        )
        assert "goes with plan" in words_of(await refused(agent, "list_plans", revision=1))


async def test_a_requested_removal_drops_the_item_and_approving_never_frees_it() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        number, refs = await drafted(agent)
        asked = person_call(
            project,
            owner,
            "request_plan_changes",
            plan=number,
            revision=1,
            summary="Drop the covers task, it is a different project.",
            comments=[{"item": refs["t3"], "text": "Remove this one."}],
        )
        assert asked.status == 200
        kept = [s for s in continuing(refs) if s["key"] != "t3"]
        revised = await ok(
            agent, "revise_plan", plan=number, items=kept, note="Removed the covers task."
        )
        assert revised["removed"] == [refs["t3"]] and revised["counts"]["removed"] == 1
        gone = await ok(agent, "get_node", node=refs["t3"])
        assert gone["status"] == "dropped"
        assert gone["last_handoff"]["note"] == (
            f"Removed in revision 2 of Plan {number}: Removed the covers task."
        )
        current = await read(agent, number)
        assert refs["t3"] not in [t["ref"] for t in current["tree"]]
        assert current["tasks"] == 2
        removal = {c["ref"]: c["change"] for c in current["changes"]}
        assert removal == {refs["t3"]: "removed"}
        assert current["revisions"][1]["removed"] == [refs["t3"]]
        # Still part of the first revision, as it was.
        before = await read(agent, number, revision=1)
        assert refs["t3"] in [t["ref"] for t in before["tree"]]

        approved = person_call(
            project, owner, "approve_plan", plan=number, approved=True, revision=2
        )
        assert approved.status == 200, approved.body
        free = {n["ref"] for n in (await ok(agent, "ready_work", limit=200))["items"]}
        assert refs["t1"] in free and refs["t3"] not in free
        assert (await ok(agent, "get_node", node=refs["t3"]))["status"] == "dropped"
        await refused(agent, "claim_node", node=refs["t3"])


async def test_items_approved_one_by_one_stay_approved_until_they_change_and_a_line_says_who() -> (
    None
):
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        number, refs = await drafted(agent)
        first = await read(agent, number)
        # Nobody has said anything yet: every line is the agent's and unreviewed.
        assert {
            b["ref"]: (b["change"], b["revision"], b["review"]["state"]) for b in first["blame"]
        } == {ref: ("added", 1, "none") for ref in refs.values()}
        assert {b["by"] for b in first["blame"]} == {"tests"}

        asked = person_call(
            project,
            owner,
            "request_plan_changes",
            plan=number,
            revision=1,
            summary="The stories are fine. Size the covers and say why the grip test is needed.",
            approved=[refs["e"], refs["s1"], refs["t1"], refs["t2"]],
            comments=[{"item": refs["t3"], "text": "Why is this the smallest?"}],
        )
        assert asked.status == 200, asked.body
        given = asked.body["feedback"][0]
        assert given["approved"] == sorted(
            [refs["e"], refs["s1"], refs["t1"], refs["t2"]],
            key=lambda ref: (
                ("epic", "story", "task").index(ref.split(":")[0]),
                int(ref.split(":")[1]),
            ),
        )
        feed = (await ok(agent, "changes_since", limit=200))["items"]
        (event,) = [e for e in feed if e["action"] == "plan_changes_requested"]
        assert event["after"]["approved"] == given["approved"]
        waiting = await read(agent, number)
        states = {b["ref"]: b["review"] for b in waiting["blame"]}
        assert states[refs["s1"]]["state"] == "approved" and states[refs["s1"]]["by"] == owner.name
        assert states[refs["s1"]]["revision"] == 1 and states[refs["s1"]]["feedback"] == given["id"]
        assert states[refs["t3"]]["state"] == "asked"
        assert states[refs["s2"]]["state"] == "none"
        await nothing_is_free(agent, refs)

        # The agent changes the covers story and the supplier task, and nothing else.
        await a_revision_of(
            agent,
            number,
            refs,
            s2={"title": "Weather covers for the four busiest stations"},
            t1={"size": "m"},
        )
        current = await read(agent, number)
        blame = {b["ref"]: b for b in current["blame"]}
        assert (blame[refs["s2"]]["change"], blame[refs["s2"]]["revision"]) == ("changed", 2)
        assert blame[refs["s2"]]["note"] == "Named the stations."
        assert blame[refs["s2"]]["by"] == "tests"
        # Approved and still as it was: it stays approved, and says it was in revision 1.
        assert blame[refs["s1"]]["review"]["state"] == "approved"
        assert (blame[refs["s1"]]["change"], blame[refs["s1"]]["revision"]) == ("added", 1)
        # Approved but changed since: it needs a fresh look.
        assert (blame[refs["t1"]]["change"], blame[refs["t1"]]["revision"]) == ("changed", 2)
        assert blame[refs["t1"]]["review"]["state"] == "changed"
        # Asked for a change and not changed: still asked.
        assert blame[refs["t3"]]["review"]["state"] == "asked"
        # An earlier revision reads as it was: nothing was changed in it, and the same approvals.
        early = await read(agent, number, revision=1)
        assert {b["revision"] for b in early["blame"]} == {1}

        # The person approves what is left and asks again: the latest word counts.
        again = person_call(
            project,
            owner,
            "request_plan_changes",
            plan=number,
            revision=2,
            summary="One left.",
            approved=[refs["s2"], refs["t1"]],
            comments=[{"item": refs["t3"], "text": "Still not sized."}],
        )
        assert again.status == 200, again.body
        final = {b["ref"]: b["review"]["state"] for b in (await read(agent, number))["blame"]}
        assert final[refs["s2"]] == final[refs["t1"]] == "approved"
        assert final[refs["t3"]] == "asked"


async def test_an_item_cannot_be_approved_and_asked_about_and_every_problem_is_listed() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        number, refs = await drafted(agent)
        said = person_call(
            project,
            owner,
            "request_plan_changes",
            plan=number,
            revision=1,
            summary="Check it.",
            approved=[refs["t1"], "task:9999", refs["s1"]],
            comments=[{"item": refs["t1"], "text": "Rename."}],
        )
        assert said.status == 400
        words = said.body["message"]
        assert "2 problems" in words and "task:9999" in words
        assert f"{refs['t1']} is approved and also has a comment" in words
        assert "Nothing was changed." in words
        # Nothing was recorded: the plan still waits for a person.
        assert (await read(agent, number))["state"] == "proposed"
        # An agent cannot approve an item: the tool is for people only.
        await refused(
            agent,
            "request_plan_changes",
            plan=number,
            revision=1,
            summary="x",
            approved=[refs["t1"]],
        )


async def test_an_item_a_revision_took_out_can_be_agreed_to_or_asked_to_come_back() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        number, refs = await drafted(agent)
        person_call(
            project,
            owner,
            "request_plan_changes",
            plan=number,
            revision=1,
            summary="Drop the covers task.",
            comments=[{"item": refs["t3"], "text": "Remove this one."}],
        )
        kept = [s for s in continuing(refs) if s["key"] != "t3"]
        await ok(agent, "revise_plan", plan=number, items=kept, note="Removed the covers task.")
        removed = {b["ref"]: b for b in (await read(agent, number))["blame"]}[refs["t3"]]
        assert (removed["change"], removed["revision"]) == ("removed", 2)
        assert removed["review"]["state"] == "none"
        # The person wants it back: a comment on the removed item is about it as it was.
        asked = person_call(
            project,
            owner,
            "request_plan_changes",
            plan=number,
            revision=2,
            summary="The covers task stays.",
            comments=[{"item": refs["t3"], "text": "Keep it after all."}],
        )
        assert asked.status == 200, asked.body
        (comment,) = asked.body["feedback"][-1]["comments"]
        assert (comment["item"], comment["quoted"]) == (refs["t3"], "Order covers")
        state = {b["ref"]: b for b in (await read(agent, number))["blame"]}[refs["t3"]]
        assert state["review"]["state"] == "asked"


async def test_a_revision_that_is_refused_changes_nothing_and_says_every_problem() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        number, refs = await drafted(agent)
        before_plan = await read(agent, number)
        before_nodes = (await ok(agent, "list_nodes", limit=200, detail=True))["items"]
        before_event = await newest_event(agent)
        bad = continuing(refs, t1={"size": "xxl"}, s2={"title": "  "})
        bad = [s for s in bad if s["key"] != "s1"]
        bad[bad.index(next(s for s in bad if s["key"] == "t2"))]["parent"] = refs["s1"]
        bad.append({"key": "t9", "kind": "task", "title": "Stray", "parent": "nope"})
        bad.append(
            {"key": "t8", "kind": "task", "title": "Ghost", "ref": "task:99999", "parent": "s2"}
        )
        said = words_of(
            await refused(
                agent,
                "revise_plan",
                plan=number,
                items=bad,
                links=[
                    {"source": "t1", "target": "t2", "link_type": "needs_first"},
                    {"source": "t2", "target": "t1", "link_type": "needs_first"},
                ],
            )
        )
        assert "The plan was not revised, because of" in said and "Nothing was changed" in said
        assert "size must be one of" in said and "title cannot be empty" in said
        assert f"refers to {refs['s1']}, which this revision removes" in said
        assert "is not the key of any item in this plan" in said
        assert "task:99999 is not an item of Plan" in said
        assert "circle of waiting" in said
        # Nothing moved: not an event, not a node, not the plan.
        assert await newest_event(agent) == before_event
        assert (await ok(agent, "list_nodes", limit=200, detail=True))["items"] == before_nodes
        assert await read(agent, number) == before_plan
        # A removal that something outside the plan stands in the way of is refused too.
        outside = await ok(
            agent, "create_node", actor="x", kind="task", title="Outside", parent=refs["s1"]
        )
        await ok(
            agent,
            "add_link",
            source=outside["ref"],
            target=refs["t1"],
            link_type="needs_first",
        )
        said = words_of(
            await refused(
                agent,
                "revise_plan",
                plan=number,
                items=[s for s in continuing(refs) if s["key"] not in ("t1", "t2")],
            )
        )
        assert f"{outside['ref']} needs {refs['t1']} first" in said
        assert f"{outside['ref']} is under {refs['s1']}" not in said
        # Plain refusals: an empty revision, a missing plan, an item of another kind.
        assert "at least one item" in words_of(
            await refused(agent, "revise_plan", plan=number, items=[])
        )
        assert "no plan 999" in words_of(
            await refused(agent, "revise_plan", plan=999, items=continuing(refs))
        )
        wrong = continuing(refs, t1={"kind": "story"})
        assert "what an item is cannot change" in words_of(
            await refused(agent, "revise_plan", plan=number, items=wrong)
        )


async def test_a_decision_applies_only_to_the_revision_the_person_reviewed() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        number, refs = await drafted(agent)
        await ok(agent, "revise_plan", plan=number, items=continuing(refs, s2={"title": "Covers"}))
        for tool, arguments in (
            ("approve_plan", {"approved": True}),
            ("approve_plan", {"approved": False, "reason": "No."}),
            ("request_plan_changes", {"summary": "Not good."}),
        ):
            late = person_call(project, owner, tool, plan=number, revision=1, **arguments)
            assert late.status == 400, (tool, late.body)
            assert "is at revision 2 now, and you reviewed revision 1" in late.body["message"]
            assert "Nothing was changed" in late.body["message"]
        still = await read(agent, number)
        assert (still["state"], still["revision"], still["feedback"]) == ("proposed", 2, [])
        await nothing_is_free(agent, refs)
        approved = person_call(
            project, owner, "approve_plan", plan=number, approved=True, revision=2
        )
        assert approved.status == 200 and approved.body["revision"] == 2
        decided = await read(agent, number)
        assert decided["state"] == "approved" and decided["checks"] is None
        free = {n["ref"] for n in (await ok(agent, "ready_work", limit=200))["items"]}
        assert refs["t1"] in free and refs["s2"] in free


async def test_what_is_decided_or_waiting_for_the_agent_is_said_in_words() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        number, refs = await drafted(agent)
        asked = person_call(
            project, owner, "request_plan_changes", plan=number, revision=1, summary="Smaller."
        )
        assert asked.status == 200 and asked.body["feedback"][0]["comments"] == []
        again = person_call(
            project, owner, "request_plan_changes", plan=number, revision=1, summary="Again."
        )
        assert again.status == 400 and "already asked for" in again.body["message"]
        early = person_call(project, owner, "approve_plan", plan=number, approved=True, revision=1)
        assert early.status == 400
        assert "there is nothing to approve" in early.body["message"]
        # Rejecting is still possible while the agent has not revised, and it ends the plan.
        rejected = person_call(
            project, owner, "approve_plan", plan=number, approved=False, reason="Not now."
        )
        assert rejected.status == 200 and rejected.body["state"] == "rejected"
        assert rejected.body["revision"] == 1
        for ref in refs.values():
            node = await ok(agent, "get_node", node=ref)
            assert node["status"] == "dropped"
            assert node["last_handoff"]["note"] == "The plan was rejected: Not now."
        said = words_of(await refused(agent, "revise_plan", plan=number, items=continuing(refs)))
        assert "already rejected" in said and "final" in said
        late = person_call(
            project, owner, "request_plan_changes", plan=number, revision=1, summary="x"
        )
        assert late.status == 400 and "already rejected" in late.body["message"]


async def test_a_plan_that_was_revised_is_rejected_with_the_current_items_only() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        number, refs = await drafted(agent)
        await ok(
            agent,
            "revise_plan",
            plan=number,
            items=[s for s in continuing(refs) if s["key"] != "t3"],
        )
        rejected = person_call(
            project, owner, "approve_plan", plan=number, approved=False, reason="No.", revision=2
        )
        assert rejected.status == 200
        for key, ref in refs.items():
            node = await ok(agent, "get_node", node=ref)
            assert node["status"] == "dropped"
            rejected_note = "The plan was rejected: No."
            assert (node["last_handoff"]["note"] == rejected_note) is (key != "t3"), key


async def test_only_a_person_decides_and_a_viewer_can_read_but_not_ask() -> None:
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer")
    editor = join_project(project, "editor")
    async with connect(project.mcp_url, project.key) as agent:
        number, refs = await drafted(agent)
        said = words_of(
            await refused(
                agent, "request_plan_changes", plan=number, revision=1, summary="I would like..."
            )
        )
        assert "not allowed to use" in said
        blocked = person_call(
            project, viewer, "request_plan_changes", plan=number, revision=1, summary="Hm."
        )
        assert blocked.status in (400, 403)
        read_as_viewer = person_call(project, viewer, "list_plans", plan=number)
        assert read_as_viewer.status == 200 and read_as_viewer.body["items"][0]["revision"] == 1
        # A person does not revise: that is the agent's act, so the board has no route for it.
        by_hand = person_call(project, editor, "revise_plan", plan=number, items=continuing(refs))
        assert by_hand.status in (400, 403, 404)
        assert (await read(agent, number))["state"] == "proposed"
        ok_editor = person_call(
            project, editor, "request_plan_changes", plan=number, revision=1, summary="Smaller."
        )
        assert ok_editor.status == 200 and ok_editor.body["feedback"][0]["by"] == editor.name


async def test_the_comments_of_a_request_are_checked_against_the_revision_reviewed() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        number, refs = await drafted(agent)
        other = await ok(agent, "create_node", actor="x", kind="epic", title="Not in the plan")
        for arguments, wanted in (
            ({"summary": "  "}, "Say in summary"),
            (
                {"summary": "x", "comments": [{"item": other["ref"], "text": "Hm."}]},
                "is not an item of the revision reviewed",
            ),
            (
                {
                    "summary": "x",
                    "comments": [{"item": refs["t1"], "field": "colour", "text": "Hm."}],
                },
                "field must be one of",
            ),
            (
                {"summary": "x", "comments": [{"item": refs["t1"], "text": " "}]},
                "say what you want",
            ),
            (
                {
                    "summary": "x",
                    "comments": [{"item": refs["t1"], "text": "Ask Ann at ann@example.com."}],
                },
                None,
            ),
        ):
            sent = person_call(
                project, owner, "request_plan_changes", plan=number, revision=1, **arguments
            )
            if wanted is None:
                assert sent.status == 200
                break
            assert sent.status == 400 and wanted in sent.body["message"], sent.body
            assert "Nothing was changed" in sent.body["message"]
            assert (await read(agent, number))["state"] == "proposed"
        no_plan = person_call(
            project, owner, "request_plan_changes", plan=9999, revision=1, summary="x"
        )
        assert no_plan.status == 400 and "no plan 9999" in no_plan.body["message"]
        parts = await read(agent, number)
        (quoted,) = parts["feedback"]
        assert quoted["comments"][0]["quoted"] == "Choose a supplier"


async def test_a_change_made_outside_a_revision_cannot_be_approved_until_it_is_revised() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        number, refs = await drafted(agent)
        assert (await read(agent, number))["drift"] == []
        version = (await ok(agent, "get_node", node=refs["t1"]))["version"]
        await ok(
            agent,
            "update_node",
            node=refs["t1"],
            expected_version=version,
            title="Choose another supplier",
        )
        await ok(agent, "add_link", source=refs["t3"], target=refs["s1"], link_type="relates_to")
        drifted = await read(agent, number)
        assert drifted["drift"] == [{"ref": refs["t1"], "fields": ["title"]}]
        late = person_call(project, owner, "approve_plan", plan=number, approved=True, revision=1)
        assert late.status == 400
        assert "was changed after revision 1 was submitted" in late.body["message"]
        assert f"T-{refs['t1'].split(':')[1]} (title)" in late.body["message"]
        await nothing_is_free(agent, refs)
        # A revision says what the plan is, and a person reads it.
        revised = await ok(
            agent,
            "revise_plan",
            plan=number,
            items=continuing(refs, t1={"title": "Choose another supplier"}),
        )
        assert revised["revision"] == 2
        now = await read(agent, number)
        assert now["drift"] == []
        # What the person reads is what changed since revision 1, including the link that was
        # added in between (it did not stop an approval, but the revision says what the plan is).
        assert {c["ref"]: c["fields"] for c in now["changes"]} == {
            refs["t1"]: ["title"],
            refs["t3"]: ["links"],
        }
        ok_now = person_call(project, owner, "approve_plan", plan=number, approved=True, revision=2)
        assert ok_now.status == 200
        # Rejecting is never held back by it.
        number2, refs2 = await drafted(agent, "Second")
        version2 = (await ok(agent, "get_node", node=refs2["t2"]))["version"]
        await ok(agent, "update_node", node=refs2["t2"], expected_version=version2, title="Sneaky")
        gone = person_call(
            project, owner, "approve_plan", plan=number2, approved=False, reason="Changed."
        )
        assert gone.status == 200


async def test_a_revision_is_the_whole_plan_and_leaving_links_and_criteria_out_keeps_them() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        number, refs = await drafted(agent)
        links = (await ok(agent, "list_links", node=refs["t2"]))["items"]
        assert [(link["source"], link["target"]) for link in links] == [(refs["t2"], refs["t1"])]
        rule_id = (await ok(agent, "list_rules", limit=50))["items"][0]["id"]

        # Left out: kept. A criterion keeps its status, and a size left out is cleared.
        kept = await ok(
            agent,
            "revise_plan",
            plan=number,
            items=continuing(refs, t1={"size": None}),
            note="Cleared a size.",
        )
        assert kept["counts"]["links_added"] == 0 and kept["counts"]["links_removed"] == 0
        assert kept["counts"]["criteria_added"] == 0 and kept["counts"]["criteria_dropped"] == 0
        assert (await ok(agent, "get_node", node=refs["t1"]))["size"] is None
        assert len((await ok(agent, "list_links", node=refs["t2"]))["items"]) == 1
        assert (await ok(agent, "get_rule", rule_id=rule_id))["status"] == "open"

        # Given: the whole set. The link goes, the criterion is reworded and a new one is added.
        changed = await ok(
            agent,
            "revise_plan",
            plan=number,
            items=continuing(refs, t1={"size": None}),
            links=[],
            rules=[
                {"id": rule_id, "title": "Every bike passes the new grip test", "node": "t2"},
                {"title": "A receipt is kept", "node": "s1"},
            ],
        )
        assert changed["counts"]["links_removed"] == 1 and changed["counts"]["criteria_added"] == 1
        assert (await ok(agent, "list_links", node=refs["t2"]))["total"] == 0
        assert (await ok(agent, "get_rule", rule_id=rule_id))["title"].endswith("new grip test")
        now = await read(agent, number)
        changes = {c["ref"]: c["fields"] for c in now["changes"]}
        assert changes == {refs["t2"]: ["links", "criteria"], refs["s1"]: ["criteria"]}
        # A criterion cannot move: that is a new one on the other item.
        said = words_of(
            await refused(
                agent,
                "revise_plan",
                plan=number,
                items=continuing(refs),
                links=[],
                rules=[{"id": rule_id, "title": "Moved", "node": "t1"}],
            )
        )
        assert "a criterion stays where it is" in said
        missing = words_of(
            await refused(
                agent,
                "revise_plan",
                plan=number,
                items=continuing(refs),
                rules=[{"id": 99999, "title": "None", "node": "t1"}],
            )
        )
        assert "99999 is not an acceptance criterion of Plan" in missing


async def test_revisions_and_feedback_travel_through_an_export_and_an_import() -> None:
    owner = register()
    source = make_project(owner)
    async with connect(source.mcp_url, source.key) as agent:
        number, refs = await drafted(agent)
        asked = person_call(
            source,
            owner,
            "request_plan_changes",
            plan=number,
            revision=1,
            summary="Smaller, please.",
            comments=[{"item": refs["t3"], "text": "Drop this."}],
        )
        assert asked.status == 200
        await ok(
            agent,
            "revise_plan",
            plan=number,
            items=[s for s in continuing(refs) if s["key"] != "t3"],
            note="Dropped it.",
        )
        waiting = await read(agent, number)
        first = await read(agent, number, revision=1)
        file = await export_everything(agent)
    actions = [e["action"] for e in file["events"]]
    assert {"plan_proposed", "plan_changes_requested", "plan_revised"} <= set(actions)

    target = make_project(register())
    async with connect(target.mcp_url, target.key) as client:
        await ok(client, "import_graph", document=file)
        loaded = await read(client, number)
        assert loaded["state"] == "proposed" and loaded["revision"] == 2
        assert loaded["revisions"] == waiting["revisions"]
        assert loaded["feedback"] == waiting["feedback"]
        assert loaded["tree"] == waiting["tree"] and loaded["changes"] == waiting["changes"]
        assert (await read(client, number, revision=1))["tree"] == first["tree"]
        held = {
            n["ref"]: n["held_by"] for n in (await ok(client, "list_nodes", limit=200))["items"]
        }
        assert held[refs["t1"]] == [f"plan:{number}"]
        assert (await ok(client, "get_node", node=refs["t3"]))["status"] == "dropped"
        again = await export_everything(client)
        wanted = ("plan_proposed", "plan_revised", "plan_changes_requested")
        assert [e["after"] for e in again["events"] if e["action"] in wanted] == [
            e["after"] for e in file["events"] if e["action"] in wanted
        ]

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
    approved_file = copy.deepcopy(file)
    approved_file["events"].append(
        {
            **next(e for e in file["events"] if e["action"] == "plan_changes_requested"),
            "id": max(e["id"] for e in file["events"]) + 1,
            "action": "plan_approved",
            "after": {"plan": number, "revision": 2},
        }
    )
    for action, tamper, wanted_words in tampers:
        bad = copy.deepcopy(approved_file)
        hit = next(e for e in bad["events"] if e["action"] == action)
        tamper(hit)
        scratch = make_project(register())
        async with connect(scratch.mcp_url, scratch.key) as client:
            said = words_of(await refused(client, "import_graph", document=bad))
            assert wanted_words in said, (action, said)
            assert (await ok(client, "list_nodes"))["items"] == []
    twice = copy.deepcopy(file)
    repeat = next(e for e in twice["events"] if e["action"] == "plan_revised")
    twice["events"].append({**repeat, "id": max(e["id"] for e in twice["events"]) + 1})
    scratch = make_project(register())
    async with connect(scratch.mcp_url, scratch.key) as client:
        assert "is made twice" in words_of(await refused(client, "import_graph", document=twice))


async def test_the_database_refuses_a_malformed_revision_or_request_on_its_own() -> None:
    project = make_project(register())
    base = (
        "INSERT INTO events (project_id, node, action, actor, actor_kind, after) "
        f"VALUES ({project.id}, NULL, "
    )
    revised = "'plan_revised', 'a', 'agent', "
    asked = "'plan_changes_requested', 'a', "
    cases = (
        (
            revised + """'{"plan": 1, "revision": 1, "items": [], "snapshot": []}'""",
            "plan_revised_has_its_words",
        ),
        (revised + """'{"plan": 1, "revision": 2, "items": []}'""", "plan_revised_has_its_words"),
        (
            asked + """'agent', '{"plan": 1, "revision": 1, "summary": "x", "comments": []}'""",
            "plan_changes_requested_has_its_words",
        ),
        (
            asked + """'person', '{"plan": 1, "revision": 1, "summary": " ", "comments": []}'""",
            "plan_changes_requested_has_its_words",
        ),
        (
            asked + """'person', '{"plan": 1, "revision": 1, "summary": "x", "comments": []}'""",
            "plan_changes_requested_has_its_words",
        ),
        ("'plan_approved', 'a', 'person', '{\"plan\": 1}'", "plan_decided_names_its_plan"),
        (
            "'plan_proposed', 'a', 'agent', '{\"title\": \"t\", \"items\": []}'",
            "plan_proposed_has_its_words",
        ),
    )
    for values, constraint in cases:
        result = run_sql(f"{base}{values})", plain=True, project=project.id)
        assert result.returncode != 0 and constraint in result.stderr, (constraint, result.stderr)
