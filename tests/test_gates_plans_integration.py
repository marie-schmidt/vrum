"""M47 and the link of M44, against the running containers, through a real MCP client and the
board's HTTP API.

Gates hold a story or task until a time comes or a person approves; a drafted plan is held until a
person approves it as one decision, or rejects it and all of it is dropped with their reason; plan
templates are saved data that makes the same plan writing it out would; and `discovered_from` says
where work was found without ever making anything wait. Each test works in a project of its own."""

import copy
from collections.abc import Callable
from typing import Any

import anyio
import pytest
from mcp import Client

from tests.helpers import (
    connect,
    embeddings_stopped,
    export_everything,
    make_tree,
    ok,
    refused,
    reindexed,
    run_sql,
    unique_word,
)
from tests.test_checks_integration import A_TOKEN, as_person, sql_rows, words_of
from tests.world import (
    HARNESS_AGENT,
    Person,
    Project,
    join_project,
    make_key,
    make_project,
    register,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def person_call(project: Project, who: Person, tool: str, **arguments: Any) -> Any:
    return as_person(who, project, tool, **arguments)


def soon(seconds: int) -> str:
    """A moment a few seconds ahead, in the form a timer takes."""
    out = run_sql(
        f"SELECT to_char((now() AT TIME ZONE 'UTC') + interval '{seconds} seconds', "
        "'YYYY-MM-DD\"T\"HH24:MI:SS')",
        plain=True,
        project=None,
    )
    return out.stdout.strip().splitlines()[-1]


def far() -> str:
    """A time far ahead that a timer still takes (it must be within three years)."""
    return soon(86400 * 200)


async def a_task(client: Client) -> dict[str, Any]:
    _, _, task = await make_tree(client)
    return task


# -- gates -----------------------------------------------------------------------------------------


async def test_a_timer_gate_holds_the_work_until_its_time_and_then_opens_by_itself() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        assert (await ok(agent, "get_node", node=task["ref"]))["status"] == "ready"
        gate = await ok(agent, "add_gate", node=task["ref"], kind="timer", not_before=soon(4))
        assert (gate["kind"], gate["state"]) == ("timer", "waiting")
        assert gate["words"].startswith("Waits until ")
        held = await ok(agent, "get_node", node=task["ref"])
        assert held["held_by"] == [f"gate:{gate['id']}"] and held["status"] == "new"
        assert [g["id"] for g in held["gates"]] == [gate["id"]]
        # Held: not ready, not given, not claimable, and the answer says why.
        assert all(i["ref"] != task["ref"] for i in (await ok(agent, "ready_work"))["items"])
        nothing = await ok(agent, "next_work", peek=True)
        assert nothing["found"] is False and "held by a gate" in nothing["message"]
        words = words_of(await refused(agent, "claim_node", node=task["ref"]))
        assert "is held (gate:" in words and "get_node shows what it waits for" in words
        assert (await ok(agent, "list_nodes", query="is:held"))["total"] == 1
        # An agent cannot lift it, whatever it says; and a past or odd time is refused.
        assert "not allowed to use" in words_of(
            await refused(agent, "remove_gate", gate=gate["id"])
        )
        for bad, wanted in (
            ("2001-01-01", "already passed"),
            ("next week", "YYYY-MM-DD"),
            ("2999-01-01", "three years"),
        ):
            said = words_of(
                await refused(agent, "add_gate", node=task["ref"], kind="timer", not_before=bad)
            )
            assert wanted in said and "Nothing was changed" in said

        await anyio.sleep(5)
        opened = await ok(agent, "get_node", node=task["ref"])
        assert opened["held_by"] == [] and opened["status"] == "ready"
        assert opened["gates"][0]["state"] == "open" and opened["gates"][0]["words"].startswith(
            "Opened at"
        )
        taken = await ok(agent, "next_work", within_node=task["ref"])
        assert taken["found"] and taken["work"]["ref"] == task["ref"]


async def test_an_approval_gate_opens_only_when_a_person_says_yes() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        gate = await ok(
            agent, "add_gate", node=task["ref"], kind="approval", question="May we touch billing?"
        )
        assert (gate["state"], gate["ask"]) == ("waiting", gate["ask"]) and gate["ask"]
        assert gate["words"] == "Waiting for a person to approve: May we touch billing?"
        # It is an ask: it shows on the Needs a person list and in the asks, with the effect gates.
        needs = await ok(agent, "needs_a_person")
        (row,) = [i for g in needs["groups"] for i in g["items"] if i["kind"] == "ask"]
        assert row["ask"]["effect"] == "gates" and row["ask"]["choices"] == ["yes", "no"]
        assert row["reason"] == "Approval needed before it can start: May we touch billing?"
        assert (await ok(agent, "get_node", node=task["ref"]))["status"] == "new"
        # An agent cannot answer; a person says no first: refused, still held, and a row of its own.
        assert "not allowed to use" in words_of(
            await refused(agent, "answer_ask", ask=gate["ask"], answer="yes")
        )
        no = person_call(
            project, owner, "answer_ask", ask=gate["ask"], answer="no", comment="Not this week"
        )
        assert no.status == 200, no.body
        refused_gate = (await ok(agent, "get_node", node=task["ref"]))["gates"][0]
        assert refused_gate["state"] == "refused" and "refused by" in refused_gate["words"]
        assert (await ok(agent, "get_node", node=task["ref"]))["held_by"] == [f"gate:{gate['id']}"]
        needs = await ok(agent, "needs_a_person")
        assert [i["kind"] for g in needs["groups"] for i in g["items"]] == ["gate"]
        [row] = [i for g in needs["groups"] for i in g["items"]]
        assert row["gate"]["id"] == gate["id"] and row["gate"]["kind"] == "approval"
        assert row["gate"]["state"] == "refused" and "refused by" in row["gate"]["words"]
        # A new gate for a second try; yes opens it, the first refused one lifted by a person.
        lifted = person_call(project, owner, "remove_gate", gate=gate["id"], reason="Asked again")
        assert lifted.status == 200 and lifted.body["state"] == "removed"
        assert (await ok(agent, "get_node", node=task["ref"]))["status"] == "ready"
        again = await ok(
            agent, "add_gate", node=task["ref"], kind="approval", question="Billing again?"
        )
        assert (await ok(agent, "get_node", node=task["ref"]))["held_by"] == [f"gate:{again['id']}"]
        assert (
            person_call(project, owner, "answer_ask", ask=again["ask"], answer="Yes").status == 200
        )
        opened = await ok(agent, "get_node", node=task["ref"])
        assert opened["held_by"] == [] and opened["status"] == "ready"
        assert [g["state"] for g in opened["gates"]] == ["removed", "open"]
        assert opened["gates"][1]["words"].startswith("Approved by ")
        again_lift = person_call(project, owner, "remove_gate", gate=gate["id"])
        assert again_lift.status == 400 and "already lifted" in again_lift.body["message"]


async def test_lifting_an_approval_gate_that_is_still_waiting_closes_its_ask() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        gate = await ok(agent, "add_gate", node=task["ref"], kind="approval", question="Go?")
        assert (await ok(agent, "list_asks", status="open"))["total"] == 1
        lifted = person_call(project, owner, "remove_gate", gate=gate["id"], reason="Not needed")
        assert lifted.status == 200
        assert (await ok(agent, "list_asks", status="open"))["total"] == 0
        closed = (await ok(agent, "list_asks", status="answered"))["items"][0]
        assert (closed["answer"], closed["answered_by"]) == ("Gate lifted", owner.name)
        assert (await ok(agent, "needs_a_person"))["total"] == 0
        assert (await ok(agent, "get_node", node=task["ref"]))["held_by"] == []


async def test_a_gate_is_refused_where_it_makes_no_sense_and_a_secret_is_never_stored() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        epic, _, task = await make_tree(agent)
        for arguments, wanted in (
            ({"node": epic["ref"], "kind": "timer", "not_before": soon(60)}, "is an epic"),
            ({"node": task["ref"], "kind": "sometime"}, "kind must be one of"),
            ({"node": task["ref"], "kind": "timer"}, "needs not_before"),
            (
                {"node": task["ref"], "kind": "timer", "not_before": soon(60), "question": "x"},
                "asks nobody",
            ),
            ({"node": task["ref"], "kind": "approval"}, "needs question"),
            (
                {"node": task["ref"], "kind": "approval", "question": "q", "not_before": soon(60)},
                "not at a time",
            ),
            (
                {"node": task["ref"], "kind": "approval", "question": f"Use {A_TOKEN}?"},
                "looks like",
            ),
            ({"node": "task:99999", "kind": "timer", "not_before": soon(60)}, "no task 99999"),
        ):
            said = words_of(await refused(agent, "add_gate", **arguments))
            assert wanted in said, (arguments, said)
            assert A_TOKEN not in said
        await ok(agent, "claim_node", node=task["ref"])
        await ok(agent, "finish_work", node=task["ref"], outcome="dropped", note="No.")
        said = words_of(
            await refused(agent, "add_gate", node=task["ref"], kind="timer", not_before=soon(60))
        )
        assert "dropped" in said
        assert sql_rows(project, "SELECT 1 FROM events WHERE action = 'gate_added'") == []


async def test_the_database_refuses_a_malformed_gate_plan_or_template_event() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
    base = (
        "INSERT INTO events (project_id, node, action, actor, actor_kind, after) "
        f"VALUES ({project.id}, "
    )
    cases = (
        (
            f"'{task['ref']}', 'gate_added', 'a', 'agent', '{{\"kind\": \"timer\"}}'",
            "gate_added_has_its_words",
        ),
        (
            f"'{task['ref']}', 'gate_added', 'a', 'agent', '{{\"kind\": \"approval\"}}'",
            "gate_added_has_its_words",
        ),
        (f"'{task['ref']}', 'gate_removed', 'a', 'person', '{{}}'", "gate_removed_names_its_gate"),
        (
            "NULL, 'plan_proposed', 'a', 'agent', '{\"title\": \" \", \"items\": []}'",
            "plan_proposed_has_its_words",
        ),
        ("NULL, 'plan_approved', 'a', 'agent', '{\"plan\": 1}'", "plan_decided_names_its_plan"),
        (
            "NULL, 'plan_template_saved', 'a', 'agent', "
            '\'{"name": "Bad Name", "template": {}}\'',
            "plan_template_has_its_words",
        ),
        (
            "NULL, 'plan_template_saved', 'a', 'agent', '{\"name\": \"ok\", \"template\": 3}'",
            "plan_template_saved_has_a_template",
        ),
        (
            f"'{task['ref']}', 'plan_proposed', 'a', 'agent', "
            '\'{"title": "t", "items": []}\'',
            "event_names_what_it_is_about",
        ),
    )
    for values, constraint in cases:
        result = run_sql(f"{base}{values})", plain=True, project=project.id)
        assert result.returncode != 0 and constraint in result.stderr, (constraint, result.stderr)


# -- plans -----------------------------------------------------------------------------------------

PLAN = [
    {"key": "e", "kind": "epic", "title": "Launch the checkout"},
    {"key": "s", "kind": "story", "title": "Pay by card", "parent": "e", "size": "m"},
    {"key": "t1", "kind": "task", "title": "Build the form", "parent": "s", "size": "s"},
    {"key": "t2", "kind": "task", "title": "Call the provider", "parent": "s", "size": "s"},
]


async def test_a_drafted_plan_is_held_until_a_person_approves_it_as_one_decision() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        made = await ok(agent, "create_plan", items=PLAN, draft=True, title="Checkout plan")
        assert made["draft"] is True and made["plan"] and made["counts"]["task"] == 2
        refs = [c["ref"] for c in made["created"]]
        for ref in refs:
            node = await ok(agent, "get_node", node=ref)
            assert node["held_by"] == [f"plan:{made['plan']}"] and node["status"] in (
                "new",
                "blocked",
            )
        task = next(c["ref"] for c in made["created"] if c["kind"] == "task")
        assert (await ok(agent, "next_work", peek=True))["found"] is False
        assert (await ok(agent, "ready_work"))["items"] == []
        assert "held" in words_of(await refused(agent, "claim_node", node=task))
        # One row for the whole plan, ahead of everything else, counted once.
        needs = await ok(agent, "needs_a_person")
        assert needs["total"] == 1 and needs["groups"][0]["story"] is None
        (row,) = needs["groups"][0]["items"]
        assert (row["kind"], row["title"], row["count"]) == ("plan", "Checkout plan", 4)
        assert row["plan"]["id"] == made["plan"] and row["plan"]["tasks"] == 2
        assert row["waiting"] == HARNESS_AGENT
        plans = await ok(agent, "list_plans", status="proposed")
        assert plans["total"] == 1 and plans["items"][0]["items"][0].startswith("epic:")
        # An agent cannot decide it.
        assert "not allowed to use" in words_of(
            await refused(agent, "approve_plan", plan=made["plan"], approved=True)
        )
        approved = person_call(project, owner, "approve_plan", plan=made["plan"], approved=True)
        assert approved.status == 200 and approved.body["state"] == "approved"
        assert approved.body["decided_by"] == owner.name
        for ref in refs:
            assert (await ok(agent, "get_node", node=ref))["held_by"] == []
        taken = await ok(agent, "next_work")
        assert taken["found"] and taken["work"]["kind"] == "task"
        assert (await ok(agent, "needs_a_person"))["total"] == 0
        again = person_call(
            project, owner, "approve_plan", plan=made["plan"], approved=False, reason="x"
        )
        assert again.status == 400 and "already approved" in again.body["message"]


async def test_a_rejected_plan_is_dropped_whole_with_the_reason_on_each_item() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        made = await ok(agent, "create_plan", items=PLAN, draft=True)
        refs = [c["ref"] for c in made["created"]]
        plan = (await ok(agent, "list_plans"))["items"][0]
        assert plan["title"] == "Launch the checkout"
        blank = person_call(project, owner, "approve_plan", plan=made["plan"], approved=False)
        assert blank.status == 400 and "needs a reason" in blank.body["message"]
        rejected = person_call(
            project, owner, "approve_plan", plan=made["plan"], approved=False, reason="Wrong scope"
        )
        assert rejected.status == 200, rejected.body
        assert (rejected.body["state"], rejected.body["reason"]) == ("rejected", "Wrong scope")
        for ref in refs:
            node = await ok(agent, "get_node", node=ref)
            assert node["status"] == "dropped"
            assert node["last_handoff"]["note"] == "The plan was rejected: Wrong scope"
            assert node["last_handoff"]["actor"] == owner.name
        assert (await ok(agent, "needs_a_person"))["total"] == 0
        assert (await ok(agent, "list_plans", status="rejected"))["total"] == 1
        assert (await ok(agent, "list_plans", status="proposed"))["total"] == 0


async def test_a_plan_that_is_not_a_draft_is_as_before_and_a_draft_title_goes_with_a_draft() -> (
    None
):
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        made = await ok(agent, "create_plan", items=PLAN)
        assert made["draft"] is False and made["plan"] is None
        assert (await ok(agent, "get_node", node=made["created"][0]["ref"]))["held_by"] == []
        said = words_of(await refused(agent, "create_plan", items=PLAN, title="A title"))
        assert "title is for a draft plan" in said


async def test_a_plan_cannot_be_decided_twice_at_once_and_a_missing_plan_is_said_so() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        made = await ok(agent, "create_plan", items=PLAN, draft=True)
    first = person_call(project, owner, "approve_plan", plan=made["plan"], approved=True)
    assert first.status == 200
    missing = person_call(project, owner, "approve_plan", plan=999, approved=True)
    assert missing.status == 400 and "no plan 999" in missing.body["message"]
    viewer = join_project(project, "viewer")
    refusal = person_call(project, viewer, "approve_plan", plan=made["plan"], approved=True)
    assert refusal.status in (400, 403)


async def test_one_plan_is_read_whole_with_its_details_links_criteria_and_what_it_hangs_under() -> (
    None
):
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic, story, _ = await make_tree(agent)
        items = [
            {
                "key": "s",
                "kind": "story",
                "title": "Pay by card",
                "parent": epic["ref"],
                "size": "m",
                "priority": "high",
                "body": "Cards only.\nNo wallets.",
                "start_on": "2030-01-01",
                "target_on": "2030-02-01",
            },
            {"key": "t1", "kind": "task", "title": "Build the form", "parent": "s", "size": "s"},
            {"key": "t2", "kind": "task", "title": "Call the provider", "parent": "s"},
        ]
        links = [
            {"source": "t2", "target": "t1", "link_type": "needs_first"},
            {"source": "t1", "target": story["ref"], "link_type": "relates_to"},
        ]
        rules = [{"title": "A declined card says why", "node": "t1"}, {"title": "Placed later"}]
        made = await ok(
            agent, "create_plan", items=items, links=links, rules=rules, draft=True, title="Cards"
        )
        number = made["plan"]
        by_title = {c["title"]: c["ref"] for c in made["created"]}

        page = await ok(agent, "list_plans", plan=number)
        assert page["total"] == 1 and page["next_after_id"] is None
        (plan,) = page["items"]
        assert [one["title"] for one in plan["tree"]] == [
            "Pay by card",
            "Build the form",
            "Call the provider",
        ]
        pay, form, call = plan["tree"]
        assert (pay["ref"], pay["kind"], pay["parent"]) == (
            by_title["Pay by card"],
            "story",
            epic["ref"],
        )
        assert (pay["body"], pay["priority"], pay["size"]) == (
            "Cards only.\nNo wallets.",
            "high",
            "m",
        )
        assert (pay["start_on"], pay["target_on"]) == ("2030-01-01", "2030-02-01")
        assert form["parent"] == pay["ref"] and form["size"] == "s" and call["size"] is None
        assert call["links"] == [{"link_type": "needs_first", "target": form["ref"]}]
        assert form["links"] == [{"link_type": "relates_to", "target": story["ref"]}]
        assert [(c["title"], c["status"]) for c in form["criteria"]] == [
            ("A declined card says why", "open")
        ]
        assert pay["criteria"] == [] and call["criteria"] == []
        # What it hangs under and what it points at, which it did not make, epics first.
        assert [(c["ref"], c["why"]) for c in plan["context"]] == [
            (epic["ref"], "parent"),
            (story["ref"], "linked"),
        ]

        # A list carries no tree, and a plan that is not there is said so.
        listed = (await ok(agent, "list_plans"))["items"][0]
        assert listed["tree"] == [] and listed["context"] == []
        assert "no plan 999" in words_of(await refused(agent, "list_plans", plan=999))

        # It reads the same once it is decided.
        approved = person_call(project, owner, "approve_plan", plan=number, approved=True)
        assert approved.status == 200 and approved.body["tree"] == []
        after = (await ok(agent, "list_plans", plan=number))["items"][0]
        assert after["state"] == "approved"
        assert [one["ref"] for one in after["tree"]] == [one["ref"] for one in plan["tree"]]


async def test_a_plan_of_sixty_items_lists_fifty_references_and_reads_all_sixty() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        epic, _, _ = await make_tree(agent)
        items: list[dict[str, Any]] = [
            {"key": "s", "kind": "story", "title": "Everything", "parent": epic["ref"]}
        ]
        items += [
            {"key": f"t{n}", "kind": "task", "title": f"Step {n}", "parent": "s"} for n in range(59)
        ]
        made = await ok(agent, "create_plan", items=items, draft=True)
        (plan,) = (await ok(agent, "list_plans", plan=made["plan"]))["items"]
        assert (plan["stories"], plan["tasks"]) == (1, 59) and len(plan["items"]) == 50
        assert len(plan["tree"]) == 60 and plan["tree"][0]["kind"] == "story"
        assert [one["title"] for one in plan["tree"][1:4]] == ["Step 0", "Step 1", "Step 2"]


# -- what to know before deciding on a plan (M50) -------------------------------------------------


def checks_of(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {check["kind"]: check for check in plan["checks"]}


async def test_a_drafted_plan_names_its_unsized_work_and_never_guesses_a_size() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        epic, _, _ = await make_tree(agent)
        made = await ok(
            agent,
            "create_plan",
            draft=True,
            items=[
                {"key": "s", "kind": "story", "title": "Pay", "parent": epic["ref"]},
                {"key": "a", "kind": "task", "title": "Sized", "parent": "s", "size": "s"},
                {"key": "b", "kind": "task", "title": "Not sized", "parent": "s"},
                {"key": "c", "kind": "task", "title": "Also not", "parent": "s"},
            ],
        )
        refs = {c["title"]: c["ref"] for c in made["created"]}
        (plan,) = (await ok(agent, "list_plans", plan=made["plan"]))["items"]
        unsized = checks_of(plan)["unsized"]
        assert unsized["state"] == "warning"
        assert [f["items"] for f in unsized["findings"]] == [
            [refs["Not sized"]],
            [refs["Also not"]],
        ]
        assert "2 tasks with no size" in unsized["words"]
        assert refs["Not sized"].split(":")[1] in unsized["words"].replace("T-", "")
        # The same answer as the filter language gives, and nothing was sized.
        asked = await ok(agent, "list_nodes", query="no:size", limit=100)
        assert {n["ref"] for n in asked["items"]} & set(refs.values()) == {
            refs["Not sized"],
            refs["Also not"],
        }
        for ref in (refs["Not sized"], refs["Also not"]):
            assert (await ok(agent, "get_node", node=ref))["size"] is None
        # Size them and the check goes quiet.
        for ref in (refs["Not sized"], refs["Also not"]):
            node = await ok(agent, "get_node", node=ref)
            await ok(agent, "update_node", node=ref, size="m", expected_version=node["version"])
        (again,) = (await ok(agent, "list_plans", plan=made["plan"]))["items"]
        assert checks_of(again)["unsized"]["state"] == "ok"
        assert checks_of(again)["unsized"]["findings"] == []


async def test_circles_of_waiting_stay_refused_and_the_plan_check_says_there_are_none() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        epic, story, _ = await make_tree(agent)
        made = await ok(
            agent,
            "create_plan",
            draft=True,
            items=[
                {"key": "s", "kind": "story", "title": "Pay", "parent": epic["ref"], "size": "m"},
                {"key": "a", "kind": "task", "title": "First", "parent": "s", "size": "s"},
                {"key": "b", "kind": "task", "title": "Second", "parent": "s", "size": "s"},
            ],
            links=[{"source": "b", "target": "a", "link_type": "needs_first"}],
        )
        a, b = (
            next(c["ref"] for c in made["created"] if c["title"] == t) for t in ("First", "Second")
        )
        said = words_of(
            await refused(agent, "add_link", source=a, target=b, link_type="needs_first")
        )
        assert "circle of waiting" in said
        # A link through work that already exists is refused the same way.
        await ok(agent, "add_link", source=story["ref"], target=a, link_type="needs_first")
        said = words_of(
            await refused(agent, "add_link", source=a, target=story["ref"], link_type="needs_first")
        )
        assert "circle of waiting" in said
        (plan,) = (await ok(agent, "list_plans", plan=made["plan"]))["items"]
        circle = checks_of(plan)["circle"]
        assert circle["state"] == "ok" and circle["findings"] == []
        assert "for itself" in circle["words"]
        # A plan written with a circle is refused whole by create_plan, by the same rule.
        said = words_of(
            await refused(
                agent,
                "create_plan",
                items=[
                    {"key": "s", "kind": "story", "title": "S", "parent": epic["ref"]},
                    {"key": "x", "kind": "task", "title": "X", "parent": "s"},
                    {"key": "y", "kind": "task", "title": "Y", "parent": "s"},
                ],
                links=[
                    {"source": "x", "target": "y", "link_type": "needs_first"},
                    {"source": "y", "target": "x", "link_type": "needs_first"},
                ],
            )
        )
        assert "circle of waiting" in said


async def test_a_plan_names_existing_work_it_may_repeat_and_says_when_it_cannot_look() -> None:
    project = make_project(register())
    title = " ".join(unique_word() for _ in range(4))
    async with connect(project.mcp_url, project.key) as agent:
        epic, story, _ = await make_tree(agent)
        existing = await ok(
            agent, "create_node", actor="x", kind="story", title=title, parent=epic["ref"]
        )
        await reindexed(agent)
        made = await ok(
            agent,
            "create_plan",
            draft=True,
            items=[
                {"key": "s1", "kind": "story", "title": title, "parent": epic["ref"], "size": "m"},
                {
                    "key": "s2",
                    "kind": "story",
                    "title": " ".join(unique_word() for _ in range(4)),
                    "parent": epic["ref"],
                    "size": "m",
                },
                {"key": "t", "kind": "task", "title": title, "parent": "s2", "size": "s"},
            ],
            # What the plan itself adds and has not been indexed yet is not "existing work".
            rules=[{"title": "A criterion", "node": "s1"}, {"title": "Another", "node": "t"}],
        )
        refs = {c["key"]: c["ref"] for c in made["created"]}
        (plan,) = (await ok(agent, "list_plans", plan=made["plan"]))["items"]
        duplicate = checks_of(plan)["duplicate"]
        assert duplicate["state"] == "warning"
        (finding,) = duplicate["findings"]
        assert finding["items"] == [refs["s1"]]
        (near,) = [r for r in finding["related"] if r["ref"] == existing["ref"]]
        assert near["kind"] == "story" and near["title"] == title and near["similarity"] >= 0.55
        # The plan's own items are never "existing work", and a task is not looked at.
        assert refs["s1"] not in [r["ref"] for r in finding["related"]]
        assert refs["t"] not in [i for f in duplicate["findings"] for i in f["items"]]
        assert f"S-{refs['s1'].split(':')[1]}" in duplicate["words"]
        assert "not indexed" not in duplicate["words"]
        # The same neighbours as the similar-items read gives for the same text.
        similar = await ok(
            agent, "similar_items", node=refs["s1"], types=["story"], min_similarity=0.55
        )
        assert existing["ref"] in [r["ref"] for r in similar["items"]]
        # With the service gone it is not reported as "no duplicates": it says it did not look.
        with embeddings_stopped():
            (blind,) = (await ok(agent, "list_plans", plan=made["plan"]))["items"]
        said = checks_of(blind)["duplicate"]
        assert said["state"] == "unavailable" and said["findings"] == []
        assert "Not checked" in said["words"] and "cannot be reached" in said["words"]
        # The other checks do not depend on it.
        assert checks_of(blind)["circle"]["state"] == "ok"
        assert story["ref"]


async def test_checks_are_only_for_a_plan_that_waits_and_never_stop_a_decision() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic, _, _ = await make_tree(agent)
        made = await ok(
            agent,
            "create_plan",
            draft=True,
            items=[
                {"key": "s", "kind": "story", "title": "Pay", "parent": epic["ref"]},
                {"key": "a", "kind": "task", "title": "Unsized", "parent": "s"},
            ],
        )
        listed = (await ok(agent, "list_plans"))["items"][0]
        assert listed["checks"] is None
        (plan,) = (await ok(agent, "list_plans", plan=made["plan"]))["items"]
        assert checks_of(plan)["unsized"]["state"] == "warning"
        # A warning does not hold back the person's decision.
        approved = person_call(project, owner, "approve_plan", plan=made["plan"], approved=True)
        assert approved.status == 200 and approved.body["checks"] is None
        (after,) = (await ok(agent, "list_plans", plan=made["plan"]))["items"]
        assert after["state"] == "approved" and after["checks"] is None


# -- templates -------------------------------------------------------------------------------------

RELEASE: dict[str, Any] = {
    "description": "Everything a release needs",
    "variables": {
        "version": {"description": "The version", "pattern": "^[0-9.]+$", "max_length": 20},
        "channel": {"choices": ["stable", "beta"], "default": "stable"},
    },
    "items": [
        {"key": "rel", "kind": "epic", "title": "Release {{version}}"},
        {
            "key": "prep",
            "kind": "story",
            "title": "Prepare {{version}}",
            "parent": "rel",
            "size": "m",
        },
        {"key": "tag", "kind": "task", "title": "Tag {{version}}", "parent": "prep", "size": "xs"},
        {"key": "ship", "kind": "story", "title": "Ship to {{channel}}", "parent": "rel"},
    ],
    "links": [{"source": "ship", "target": "prep", "link_type": "needs_first"}],
    "rules": [{"title": "Notes name every change in {{version}}", "node": "prep"}],
}


async def shape(client: Client) -> dict[str, Any]:
    """What a project holds, by titles: each node's kind, parent, size, the links and criteria."""
    nodes = (await ok(client, "list_nodes", limit=200, detail=True))["items"]
    title = {n["ref"]: n["title"] for n in nodes}
    links = {}
    for ref in title:
        for link in (await ok(client, "list_links", node=ref, limit=200))["items"]:
            links[(link["source"], link["link_type"], link["target"])] = link
    rules = (await ok(client, "list_rules", limit=200))["items"]
    return {
        "nodes": {n["title"]: (n["kind"], title.get(n["parent"]), n["size"]) for n in nodes},
        "links": {(title[s], kind, title[t]) for (s, kind, t) in links},
        "rules": {(r["title"], title.get(r["node"])) for r in rules},
    }


async def test_a_template_makes_the_plan_that_writing_it_out_would() -> None:
    project = make_project(register())
    other = make_project(register())
    async with (
        connect(project.mcp_url, project.key) as agent,
        connect(other.mcp_url, other.key) as by_hand,
    ):
        saved = await ok(agent, "save_plan_template", name="release", template=RELEASE)
        assert (saved["name"], saved["version"], saved["description"]) == (
            "release",
            1,
            "Everything a release needs",
        )
        assert saved["counts"] == {
            "epics": 1,
            "stories": 2,
            "tasks": 1,
            "links": 1,
            "acceptance criteria": 1,
        }
        assert saved["template"]["items"][0]["title"] == "Release {{version}}"
        made = await ok(agent, "create_plan", template="release", values={"version": "2.1"})
        assert made["counts"]["epic"] == 1 and made["draft"] is False

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
        assert await shape(agent) == await shape(by_hand)

        # Listed, read whole, saved again (a second version), used as a draft, deleted.
        listing = await ok(agent, "list_plan_templates")
        assert listing["total"] == 1 and listing["items"][0]["template"] is None
        assert listing["items"][0]["variables"]["channel"]["choices"] == ["stable", "beta"]
        whole = await ok(agent, "list_plan_templates", name="release")
        assert whole["items"][0]["template"]["description"] == "Everything a release needs"
        newer = {**RELEASE, "description": "Release, second edition"}
        assert (await ok(agent, "save_plan_template", name="release", template=newer))[
            "version"
        ] == 2
        draft = await ok(
            agent,
            "create_plan",
            template="release",
            values={"version": "3.0", "channel": "beta"},
            draft=True,
        )
        assert draft["draft"] and draft["plan"]
        plan = (await ok(agent, "list_plans"))["items"][0]
        assert plan["title"] == "Release 3.0"
        assert (await ok(agent, "delete_plan_template", name="release"))["name"] == "release"
        assert (await ok(agent, "list_plan_templates"))["total"] == 0
        said = words_of(
            await refused(agent, "create_plan", template="release", values={"version": "1"})
        )
        assert "no plan template called 'release'" in said
        # Every version stays in the history of the project.
        saves = [
            e
            for e in (await export_everything(agent))["events"]
            if e["action"].startswith("plan_template")
        ]
        assert [e["action"] for e in saves] == [
            "plan_template_saved",
            "plan_template_saved",
            "plan_template_deleted",
        ]


async def test_a_template_or_its_values_that_do_not_fit_are_refused_with_every_problem() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        bad = {
            "variables": {"v": {"pattern": "(a+)+$"}},
            "items": [
                {"key": "a", "kind": "story", "title": "{{missing}}", "parent": "nope"},
                {"key": "b", "kind": "task", "title": "T", "parent": "a"},
                {"key": "b", "kind": "task", "title": "Again", "parent": "a"},
            ],
        }
        said = words_of(await refused(agent, "save_plan_template", name="bad", template=bad))
        assert "The template was not saved, because of" in said and "Nothing was saved" in said
        assert "pattern" in said and "{{missing}}" in said
        # The plan it makes is checked too: a story needs an epic, the key is used twice.
        plan_only = {
            "items": [
                {"key": "a", "kind": "story", "title": "S"},
                {"key": "b", "kind": "task", "title": "T", "parent": "a"},
                {"key": "b", "kind": "task", "title": "U", "parent": "a"},
            ]
        }
        said = words_of(
            await refused(agent, "save_plan_template", name="plan-only", template=plan_only)
        )
        assert "needs a parent" in said or "parent" in said
        assert "more than once" in said or "twice" in said or "used" in said
        for name in ("Bad Name", "", "a/b"):
            assert "template's name" in words_of(
                await refused(agent, "save_plan_template", name=name, template=RELEASE)
            )
        said = await refused(
            agent, "save_plan_template", name="secret", template={**RELEASE, "description": A_TOKEN}
        )
        assert A_TOKEN not in said
        assert (await ok(agent, "list_plan_templates"))["total"] == 0

        await ok(agent, "save_plan_template", name="release", template=RELEASE)
        said = words_of(
            await refused(
                agent, "create_plan", template="release", values={"channel": "gamma", "x": "1"}
            )
        )
        assert "The plan was not created, because of 3 problems" in said
        assert (
            "version" in said and "one of stable, beta" in said and "'x' is not a variable" in said
        )
        both = words_of(await refused(agent, "create_plan", template="release", items=PLAN))
        assert "either a template" in both
        loose = words_of(await refused(agent, "create_plan", items=PLAN, values={"a": "b"}))
        assert "values go with a template" in loose
        assert (await ok(agent, "list_nodes"))["items"] == []


async def test_a_template_can_hang_its_plan_under_an_epic_that_exists() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
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
        made = await ok(
            agent,
            "create_plan",
            template="checklist",
            values={"epic": epic["ref"], "name": "billing"},
        )
        story = next(c for c in made["created"] if c["kind"] == "story")
        assert (await ok(agent, "get_node", node=story["ref"]))["parent"] == epic["ref"]
        said = words_of(
            await refused(
                agent, "create_plan", template="checklist", values={"epic": "story:1", "name": "x"}
            )
        )
        assert "must be a epic" in said
        said = words_of(
            await refused(
                agent,
                "create_plan",
                template="checklist",
                values={"epic": "epic:9999", "name": "x"},
            )
        )
        assert "no epic 9999" in said and "Nothing was created" in said


# -- the link of M44, and everything travelling ----------------------------------------------


async def test_discovered_from_says_where_work_was_found_and_never_makes_it_wait() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        _, story, task = await make_tree(agent)
        found = await ok(
            agent, "create_node", actor="x", kind="task", title="A bug we met", parent=story["ref"]
        )
        link = await ok(
            agent, "add_link", source=found["ref"], target=task["ref"], link_type="discovered_from"
        )
        assert (link["source"], link["target"], link["link_type"]) == (
            found["ref"],
            task["ref"],
            "discovered_from",
        )
        # It is one way, so the other direction is another link; a repeat is refused.
        assert (await ok(agent, "list_links", node=task["ref"]))["total"] == 1
        assert (
            "already"
            in words_of(
                await refused(
                    agent,
                    "add_link",
                    source=found["ref"],
                    target=task["ref"],
                    link_type="discovered_from",
                )
            ).lower()
        )
        await ok(
            agent, "add_link", source=task["ref"], target=found["ref"], link_type="discovered_from"
        )
        after = await ok(agent, "get_node", node=found["ref"])
        assert after["waiting_on"] == [] and after["status"] == "ready"
        taken = await ok(agent, "next_work", within_node=found["ref"])
        assert [b["ref"] for b in taken["context"]["discovered_from"]] == [task["ref"]]
        # A discovered_from link and a needs_first one are told apart.
        await ok(
            agent, "add_link", source=found["ref"], target=task["ref"], link_type="needs_first"
        )
        assert (await ok(agent, "get_node", node=found["ref"]))["waiting_on"] == [task["ref"]]
        for bad in ("discovered", "found_in"):
            assert "link_type must be one of" in words_of(
                await refused(
                    agent, "add_link", source=found["ref"], target=story["ref"], link_type=bad
                )
            )


async def test_everything_new_travels_through_an_export_and_an_import() -> None:
    owner = register()
    source = make_project(owner)
    async with connect(source.mcp_url, source.key) as agent:
        _, story, task = await make_tree(agent)
        found = await ok(
            agent, "create_node", actor="x", kind="task", title="Found it", parent=story["ref"]
        )
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
        assert (
            person_call(source, owner, "approve_plan", plan=decided["plan"], approved=True).status
            == 200
        )
        assert (
            person_call(source, owner, "answer_ask", ask=approval["ask"], answer="yes").status
            == 200
        )
        file = await export_everything(agent)
    kinds = {e["action"] for e in file["events"]}
    assert {
        "gate_added",
        "plan_proposed",
        "plan_approved",
        "plan_template_saved",
        "ask_raised",
        "ask_answered",
    } <= kinds

    target = make_project(register())
    async with connect(target.mcp_url, target.key) as client:
        await ok(client, "import_graph", document=file)
        titles = {n["title"]: n for n in (await ok(client, "list_nodes", limit=200))["items"]}
        # The timer gate still holds, the approved approval gate is open, the draft is still held.
        assert titles["A task"]["held_by"] == [f"gate:{timer['id']}"]
        assert titles["Found it"]["held_by"] == []
        assert titles["Launch the checkout"]["held_by"] == [f"plan:{draft['plan']}"]
        assert titles["Decided"]["held_by"] == []
        gates = (await ok(client, "get_node", node=titles["Found it"]["ref"]))["gates"]
        assert (gates[0]["id"], gates[0]["state"]) == (approval["id"], "open")
        assert (await ok(client, "list_plans", status="proposed"))["total"] == 1
        assert (await ok(client, "list_plans", status="approved"))["total"] == 1
        template = await ok(client, "list_plan_templates", name="release")
        assert (
            template["items"][0]["template"]
            == (await ok(client, "list_plan_templates", name="release"))["items"][0]["template"]
        )
        assert template["items"][0]["variables"]["version"]["pattern"] == "^[0-9.]+$"
        links = (await ok(client, "list_links", node=titles["Found it"]["ref"]))["items"]
        assert [link["link_type"] for link in links] == ["discovered_from"]
        # What a file says about a gate or plan has to fit, and every problem is listed.
        again = await export_everything(client)
        assert [e["after"] for e in again["events"] if e["action"] == "gate_added"] == [
            e["after"] for e in file["events"] if e["action"] == "gate_added"
        ]
    tampers: list[tuple[str, Callable[[dict[str, Any]], object], str]] = [
        (
            "gate_added",
            lambda e: e["after"].__setitem__("kind", "sometime"),
            "kind must be timer or approval",
        ),
        (
            "gate_removed",
            lambda e: e["after"].__setitem__("gate", 9999),
            "is not a gate in the file",
        ),
        ("plan_approved", lambda e: e.__setitem__("actor_kind", "agent"), "decided by a person"),
        (
            "plan_approved",
            lambda e: e["after"].__setitem__("plan", 9999),
            "is not a plan in the file",
        ),
        (
            "plan_template_saved",
            lambda e: e["after"].__setitem__("name", "Bad Name"),
            "template's name",
        ),
    ]
    for action, tamper, wanted in tampers:
        bad = copy.deepcopy(file)
        if action == "gate_removed":
            template_event = next(e for e in bad["events"] if e["action"] == "gate_added")
            bad["events"].append(
                {
                    **template_event,
                    "id": max(e["id"] for e in bad["events"]) + 1,
                    "action": "gate_removed",
                    "after": {"gate": 1},
                }
            )
        hit = next(e for e in bad["events"] if e["action"] == action)
        tamper(hit)
        scratch = make_project(register())
        async with connect(scratch.mcp_url, scratch.key) as client:
            said = words_of(await refused(client, "import_graph", document=bad))
            assert wanted in said, (action, said)
            assert (await ok(client, "list_nodes"))["items"] == []


async def test_the_board_can_read_and_use_all_of_it_as_a_person() -> None:
    owner = register()
    project = make_project(owner)
    editor = join_project(project, "editor")
    async with connect(project.mcp_url, make_key(project, "agent-two")) as agent:
        task = await a_task(agent)
        await ok(agent, "save_plan_template", name="release", template=RELEASE)
    # The board's person makes a plan from the template, adds and lifts a gate, and decides a plan.
    made = person_call(
        project, editor, "create_plan", template="release", values={"version": "1.0"}, draft=True
    )
    assert made.status == 200, made.body
    gate = person_call(
        project, editor, "add_gate", node=task["ref"], kind="timer", not_before=far()
    )
    assert gate.status == 200 and gate.body["state"] == "waiting"
    assert (
        person_call(
            project, editor, "remove_gate", gate=gate.body["id"], reason="Not needed"
        ).status
        == 200
    )
    assert (
        person_call(project, editor, "approve_plan", plan=made.body["plan"], approved=True).status
        == 200
    )
    assert person_call(project, owner, "list_plans").body["total"] == 1
    assert person_call(project, owner, "list_plan_templates").body["total"] == 1
    assert person_call(project, owner, "delete_plan_template", name="release").status == 200
    viewer = join_project(project, "viewer")
    assert person_call(project, viewer, "list_plan_templates").status == 200
    for tool, arguments in (
        ("save_plan_template", {"name": "x", "template": RELEASE}),
        ("add_gate", {"node": task["ref"], "kind": "timer", "not_before": far()}),
    ):
        assert person_call(project, viewer, tool, **arguments).status in (400, 403), tool
