"""M43: where work belongs. `suggest_parent` ranks the epics (or stories) a piece of work might go
under by meaning, each with the items that are the reason, and agrees with the arithmetic of
scripts/parent_accuracy.py that measured it; a proposal to move work (`propose_placement`) changes
nothing until a person accepts it; accepting makes the moves as that person, leaves out what moved
or finished since, and makes a new epic only then; only a person decides; a proposer cannot flood
the list; and everything travels through an export and an import. Each test works in a project of
its own, on the check stack's scripted embedding service, whose words stand for concepts."""

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    connect,
    embeddings_stopped,
    export_everything,
    ok,
    refused,
    reindexed,
    run_sql,
)
from tests.scripted_embeddings import embed
from tests.test_checks_integration import as_person, sql_rows, words_of
from tests.test_importers_integration import run_script
from tests.world import Person, Project, make_limited_key, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def parent_accuracy() -> Any:
    path = Path(__file__).resolve().parent.parent / "scripts" / "parent_accuracy.py"
    spec = importlib.util.spec_from_file_location("parent_accuracy", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["parent_accuracy"] = module
    spec.loader.exec_module(module)
    return module


async def node(
    client: Client, kind: str, title: str, parent: str | None = None, body: str = ""
) -> str:
    made = await ok(
        client, "create_node", actor="x", kind=kind, title=title, parent=parent, body=body
    )
    return str(made["ref"])


async def a_sorted_project(client: Client) -> dict[str, str]:
    """Three epics with stories and tasks, in words the scripted service tells apart, and an
    Inbox epic with loose stories. Returns the references by name."""
    refs: dict[str, str] = {}
    spec = {
        "billing": (
            "Billing",
            "invoice payment charge billing",
            ["Issue the invoice", "Charge the payment", "Refund a payment"],
        ),
        "mail": (
            "Notifications",
            "email notification alert message",
            ["Send an email", "Alert by message", "Notify the phone"],
        ),
        "access": (
            "Access",
            "login password permission role",
            ["Reset the password", "Sign in with credentials", "Grant a permission"],
        ),
    }
    for key, (title, body, stories) in spec.items():
        refs[key] = await node(client, "epic", title, body=body)
        for number, story in enumerate(stories, start=1):
            refs[f"{key}{number}"] = await node(client, "story", story, refs[key], body=body)
            await node(client, "task", f"{story} for real", refs[f"{key}{number}"], body=body)
    refs["inbox"] = await node(client, "epic", "Inbox", body="Work that is not sorted yet.")
    refs["loose_pay"] = await node(client, "story", "Pay the invoice", refs["inbox"])
    refs["loose_mail"] = await node(client, "story", "Email an alert", refs["inbox"])
    refs["loose_login"] = await node(client, "story", "Reset a password", refs["inbox"])
    refs["loose_story"] = await node(client, "story", "Loose tasks", refs["inbox"])
    refs["loose_task"] = await node(client, "task", "Charge the payment", refs["loose_story"])
    await reindexed(client)
    return refs


def person_call(project: Project, who: Person, tool: str, **arguments: Any) -> Any:
    return as_person(who, project, tool, **arguments)


# -- suggest_parent ------------------------------------------------------------------------------


async def test_a_story_is_sent_to_the_epic_it_resembles_and_the_reason_is_shown() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        refs = await a_sorted_project(client)
        found = await ok(client, "suggest_parent", node=refs["loose_pay"])
        assert found["kind"] == "story" and found["parent_kind"] == "epic"
        assert found["node"] == refs["loose_pay"] and found["current_parent"] == refs["inbox"]
        assert found["inbox"] == refs["inbox"] and found["not_considered"] == 0
        # The Inbox is where it is, never a place to suggest.
        assert refs["inbox"] not in [s["ref"] for s in found["suggestions"]]
        assert found["current_rank"] is None
        top = found["suggestions"][0]
        assert top["ref"] == refs["billing"] and top["title"] == "Billing"
        assert found["sure"] is True and found["margin"] >= 0.05
        scores = [s["score"] for s in found["suggestions"]]
        assert scores == sorted(scores, reverse=True) and len(scores) == 3
        # The reason: items under that epic, nearest first, and only ones that are under it.
        assert top["holds"] >= 3 and 1 <= len(top["because"]) <= 2
        under = {refs["billing1"], refs["billing2"], refs["billing3"]}
        stories_of = {
            n["ref"]
            for n in (await ok(client, "list_nodes", parent=refs["billing"], limit=50))["items"]
        }
        assert stories_of == under
        for one in top["because"]:
            assert (
                one["ref"] in under
                or (await ok(client, "get_node", node=one["ref"]))["parent"] in under
            )
        sims = [one["similarity"] for one in top["because"]]
        assert sims == sorted(sims, reverse=True)
        # Others go to theirs, and a limit of one still says how far ahead the best is.
        mail = await ok(client, "suggest_parent", node=refs["loose_mail"], limit=1)
        assert [s["ref"] for s in mail["suggestions"]] == [refs["mail"]]
        assert mail["margin"] is not None and mail["sure"] is True
        login = await ok(client, "suggest_parent", node=refs["loose_login"])
        assert login["suggestions"][0]["ref"] == refs["access"]


async def test_a_task_is_sent_to_a_story_and_work_not_saved_yet_can_be_asked_about() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        refs = await a_sorted_project(client)
        found = await ok(client, "suggest_parent", node=refs["loose_task"], limit=5)
        assert found["kind"] == "task" and found["parent_kind"] == "story"
        assert found["current_parent"] == refs["loose_story"]
        # Stories in the Inbox are unsorted too, so none is offered (nor the one it is in).
        assert found["suggestions"][0]["ref"] == refs["billing2"]
        offered = {s["ref"] for s in found["suggestions"]}
        assert not offered & {refs["loose_pay"], refs["loose_mail"], refs["loose_story"]}
        assert found["current_rank"] is None
        # Not saved yet: a title and what it will be.
        fresh = await ok(
            client, "suggest_parent", title="Alert users by email", kind="story", limit=3
        )
        assert fresh["node"] is None and fresh["current_parent"] is None
        assert fresh["current_rank"] is None
        assert fresh["suggestions"][0]["ref"] == refs["mail"]
        by_task = await ok(client, "suggest_parent", title="Charge a payment", kind="task")
        assert by_task["parent_kind"] == "story"
        assert by_task["suggestions"][0]["ref"] == refs["billing2"]


async def test_what_suggest_parent_scores_is_what_the_measuring_script_measures() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        refs = await a_sorted_project(client)
        found = await ok(client, "suggest_parent", node=refs["loose_pay"], limit=3)
        nodes = (await ok(client, "list_nodes", limit=200, detail=True))["items"]
    script = parent_accuracy()
    work = [
        script.Work(n["ref"], n["kind"], n["title"], n["body"], n["parent"])
        for n in nodes
        if n["ref"] != refs["inbox"]
    ]
    measure = script.Measure(work, {item.ref: embed(item.text) for item in work})
    for suggestion in found["suggestions"]:
        for view, field in (("both", "score"), ("own", "own")):
            assert (
                abs(measure.score(refs["loose_pay"], suggestion["ref"], view) - suggestion[field])
                < 1e-3
            )
        assert (
            abs(measure.score(refs["loose_pay"], suggestion["ref"], "held") - suggestion["held"])
            < 1e-3
        )


async def test_suggest_parent_says_in_plain_words_what_it_cannot_do() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        refs = await a_sorted_project(client)
        said = words_of(await refused(client, "suggest_parent", node=refs["billing"]))
        assert "is an epic" in said
        said = words_of(await refused(client, "suggest_parent"))
        assert "Give either node" in said
        said = words_of(
            await refused(client, "suggest_parent", node=refs["billing1"], title="Also this")
        )
        assert "Give either node" in said
        said = words_of(await refused(client, "suggest_parent", title="No kind"))
        assert "Say kind" in said
        for bad in (0, 11):
            said = words_of(
                await refused(client, "suggest_parent", node=refs["billing1"], limit=bad)
            )
            assert "limit must be from 1 to 10" in said
        said = words_of(await refused(client, "suggest_parent", node="story:99999"))
        assert "no story 99999" in said


async def test_an_item_that_is_not_indexed_yet_is_still_asked_about_by_its_words() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        refs = await a_sorted_project(client)
        fresh = await node(client, "story", "Charge a payment by invoice", refs["inbox"])
        found = await ok(client, "suggest_parent", node=fresh)
        assert found["suggestions"][0]["ref"] == refs["billing"]
        # An epic that is not indexed yet is not ranked, and the answer says how many.
        await node(client, "epic", "Brand new")
        again = await ok(client, "suggest_parent", node=fresh)
        assert again["not_considered"] in (0, 1)


# -- proposals -----------------------------------------------------------------------------------


async def test_a_proposal_changes_nothing_until_a_person_accepts_it() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        made = await ok(
            agent,
            "propose_placement",
            items=[refs["loose_pay"]],
            parent=refs["billing"],
            reason="Nearest by meaning: it is about paying an invoice.",
        )
        number = made["id"]
        assert made["state"] == "proposed" and made["parent"] == refs["billing"]
        assert made["parent_title"] == "Billing" and made["new_epic"] is None
        assert [i["ref"] for i in made["items"]] == [refs["loose_pay"]]
        assert made["items"][0]["from_parent"] == refs["inbox"]
        # Nothing moved, and it waits in the lists and on the Needs a person page.
        assert (await ok(agent, "get_node", node=refs["loose_pay"]))["parent"] == refs["inbox"]
        waiting = await ok(agent, "list_placements", status="proposed")
        assert waiting["total"] == 1 and waiting["items"][0]["id"] == number
        needs = await ok(agent, "needs_a_person")
        [moves] = [g for g in needs["groups"] if g["title"] == "Suggested moves"]
        [row] = moves["items"]
        assert row["kind"] == "placement" and row["placement"]["id"] == number
        assert row["node"] == refs["loose_pay"] and "Billing" in row["reason"]
        assert row["waiting"] == "tests" and row["waiting_kind"] == "agent"
        assert needs["total"] == 1
        # An agent cannot decide it: the tool is for people only.
        said = words_of(await refused(agent, "decide_placement", placement=number, accepted=True))
        assert "not allowed to use" in said
        # The person accepts: it moves, as that person, and the row is gone.
        done = person_call(project, owner, "decide_placement", placement=number, accepted=True)
        assert done.status == 200, done.body
        assert done.body["state"] == "accepted" and done.body["applied"] == [refs["loose_pay"]]
        assert done.body["skipped"] == [] and done.body["epic"] is None
        moved = await ok(agent, "get_node", node=refs["loose_pay"])
        assert moved["parent"] == refs["billing"]
        history = (await ok(agent, "node_history", node=refs["loose_pay"]))["items"]
        move = [e for e in history if e["action"] == "move"][-1]
        assert move["actor_kind"] == "person"
        assert (
            move["before"]["parent"] == refs["inbox"] and move["after"]["parent"] == refs["billing"]
        )
        assert (await ok(agent, "needs_a_person"))["total"] == 0
        again = person_call(
            project, owner, "decide_placement", placement=number, accepted=False, reason="no"
        )
        assert again.status == 400 and "already accepted" in again.body["message"]


async def test_a_rejection_needs_a_reason_and_moves_nothing() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        made = await ok(
            agent,
            "propose_placement",
            items=[refs["loose_mail"]],
            parent=refs["mail"],
            reason="It is about an alert.",
        )
        blank = person_call(
            project, owner, "decide_placement", placement=made["id"], accepted=False
        )
        assert blank.status == 400 and "needs a reason" in blank.body["message"]
        no = person_call(
            project,
            owner,
            "decide_placement",
            placement=made["id"],
            accepted=False,
            reason="Keep it",
        )
        assert no.status == 200 and no.body["state"] == "rejected"
        assert no.body["decision_reason"] == "Keep it" and no.body["applied"] == []
        assert (await ok(agent, "get_node", node=refs["loose_mail"]))["parent"] == refs["inbox"]
        assert (await ok(agent, "list_placements", status="rejected"))["total"] == 1
        # The same item can be proposed again now that the first was decided.
        await ok(
            agent,
            "propose_placement",
            items=[refs["loose_mail"]],
            parent=refs["mail"],
            reason="Asking again.",
        )
        missing = person_call(project, owner, "decide_placement", placement=99999, accepted=True)
        assert missing.status == 400 and "There is no proposal 99999" in missing.body["message"]


async def test_work_that_moved_since_is_left_out_and_said_so() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        made = await ok(
            agent,
            "propose_placement",
            items=[refs["loose_pay"], refs["loose_mail"], refs["loose_login"]],
            parent=refs["billing"],
            reason="All three look like billing.",
        )
        # Someone moves one by hand, and another is finished, before the person looks.
        version = (await ok(agent, "get_node", node=refs["loose_mail"]))["version"]
        await ok(
            agent,
            "move_node",
            node=refs["loose_mail"],
            new_parent=refs["mail"],
            expected_version=version,
        )
        await ok(agent, "claim_node", node=refs["loose_login"])
        await ok(
            agent, "finish_work", node=refs["loose_login"], outcome="dropped", note="Not needed."
        )
        done = person_call(project, owner, "decide_placement", placement=made["id"], accepted=True)
        assert done.status == 200, done.body
        assert done.body["applied"] == [refs["loose_pay"]]
        skipped = {s["ref"]: s["why"] for s in done.body["skipped"]}
        assert set(skipped) == {refs["loose_mail"], refs["loose_login"]}
        assert "moved to E-" in skipped[refs["loose_mail"]]
        assert skipped[refs["loose_login"]] == "it is dropped"
        assert (await ok(agent, "get_node", node=refs["loose_mail"]))["parent"] == refs["mail"]
        # When nothing is left to do, the person is told to reject it, and nothing changes.
        other = await ok(
            agent,
            "propose_placement",
            items=[refs["loose_story"]],
            parent=refs["billing"],
            reason="It holds a payment task.",
        )
        version = (await ok(agent, "get_node", node=refs["loose_story"]))["version"]
        await ok(
            agent,
            "move_node",
            node=refs["loose_story"],
            new_parent=refs["access"],
            expected_version=version,
        )
        stale = person_call(
            project, owner, "decide_placement", placement=other["id"], accepted=True
        )
        assert stale.status == 400 and "nothing to do" in stale.body["message"]
        assert (await ok(agent, "list_placements", status="proposed"))["total"] == 1


async def test_a_person_can_accept_some_of_the_items_and_leave_the_others_where_they_are() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        made = await ok(
            agent,
            "propose_placement",
            items=[refs["loose_pay"], refs["loose_mail"], refs["loose_login"]],
            parent=refs["billing"],
            reason="They all look like billing.",
        )
        # Naming nothing, a stranger, or items while rejecting is refused and changes nothing.
        for arguments, words in (
            ({"accepted": True, "items": []}, "Name at least one item"),
            ({"accepted": True, "items": [refs["billing"]]}, "not in proposal"),
            (
                {"accepted": False, "reason": "No", "items": [refs["loose_pay"]]},
                "only when accepting",
            ),
        ):
            said = person_call(
                project, owner, "decide_placement", placement=made["id"], **arguments
            )
            assert said.status == 400 and words in said.body["message"], said.body
        assert (await ok(agent, "list_placements", status="proposed"))["total"] == 1
        # An agent cannot decide it, whatever it names.
        await refused(
            agent,
            "decide_placement",
            placement=made["id"],
            accepted=True,
            items=[refs["loose_pay"]],
        )

        done = person_call(
            project,
            owner,
            "decide_placement",
            placement=made["id"],
            accepted=True,
            items=[refs["loose_pay"], refs["loose_login"]],
        )
        assert done.status == 200, done.body
        assert done.body["state"] == "accepted"
        assert sorted(done.body["applied"]) == sorted([refs["loose_pay"], refs["loose_login"]])
        assert done.body["skipped"] == [
            {"ref": refs["loose_mail"], "why": "Priya Raman left it out"}
        ]
        assert (await ok(agent, "get_node", node=refs["loose_pay"]))["parent"] == refs["billing"]
        assert (await ok(agent, "get_node", node=refs["loose_login"]))["parent"] == refs["billing"]
        assert (await ok(agent, "get_node", node=refs["loose_mail"]))["parent"] == refs["inbox"]
        assert (await ok(agent, "needs_a_person"))["total"] == 0


async def test_stories_are_grouped_under_a_new_epic_that_is_made_only_when_accepted() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        epics_before = (await ok(agent, "list_nodes", kind="epic", limit=50))["total"]
        made = await ok(
            agent,
            "propose_placement",
            items=[refs["loose_pay"], refs["loose_mail"]],
            new_epic={"title": "Receipts", "body": "Everything about what a customer gets."},
            reason="Both are about what the customer receives.",
        )
        assert made["parent"] is None and made["new_epic"]["title"] == "Receipts"
        assert (await ok(agent, "list_nodes", kind="epic", limit=50))["total"] == epics_before
        needs = await ok(agent, "needs_a_person")
        [row] = [i for g in needs["groups"] for i in g["items"]]
        assert "new epic, Receipts" in row["reason"] and row["count"] == 2
        done = person_call(project, owner, "decide_placement", placement=made["id"], accepted=True)
        assert done.status == 200, done.body
        epic = done.body["epic"]
        assert epic and sorted(done.body["applied"]) == sorted(
            [refs["loose_pay"], refs["loose_mail"]]
        )
        made_epic = await ok(agent, "get_node", node=epic)
        assert made_epic["title"] == "Receipts" and made_epic["body"].startswith("Everything about")
        created = [
            e
            for e in (await ok(agent, "node_history", node=epic))["items"]
            if e["action"] == "create"
        ]
        assert created[0]["actor_kind"] == "person"
        for ref in (refs["loose_pay"], refs["loose_mail"]):
            assert (await ok(agent, "get_node", node=ref))["parent"] == epic
        # Rejecting one makes no epic at all.
        second = await ok(
            agent,
            "propose_placement",
            items=[refs["loose_login"]],
            new_epic={"title": "Never made"},
            reason="Perhaps.",
        )
        person_call(
            project, owner, "decide_placement", placement=second["id"], accepted=False, reason="No"
        )
        titles = [
            n["title"] for n in (await ok(agent, "list_nodes", kind="epic", limit=50))["items"]
        ]
        assert "Never made" not in titles


async def test_a_proposal_that_does_not_make_sense_is_refused_and_stores_nothing() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        done_story = refs["billing1"]
        await ok(agent, "claim_node", node=refs["billing1"])
        task_ref = (await ok(agent, "list_nodes", kind="task", parent=refs["billing1"]))["items"][
            0
        ]["ref"]
        await ok(agent, "claim_node", node=task_ref)
        await ok(agent, "finish_work", node=task_ref, outcome="done", note="Done.")
        await ok(agent, "finish_work", node=done_story, outcome="done", note="Done.")
        base: dict[str, Any] = {
            "items": [refs["loose_pay"]],
            "parent": refs["billing"],
            "reason": "Because.",
        }
        cases: list[tuple[dict[str, Any], str]] = [
            ({"reason": "  "}, "Say why in reason"),
            ({"parent": None}, "Give either parent"),
            ({"new_epic": {"title": "X"}}, "Give either parent"),
            ({"items": []}, "Give from 1 to 20 items"),
            ({"items": [f"story:{n}" for n in range(500, 521)]}, "Give from 1 to 20 items"),
            ({"items": [refs["loose_pay"], refs["loose_pay"]]}, "named twice"),
            ({"items": [refs["billing"]]}, "all stories or all tasks"),
            ({"items": [refs["loose_pay"], refs["loose_task"]]}, "all stories or all tasks"),
            ({"parent": refs["loose_story"]}, "go under epics"),
            ({"items": [refs["loose_task"]], "parent": refs["billing"]}, "go under stories"),
            (
                {"items": [refs["loose_task"]], "parent": None, "new_epic": {"title": "X"}},
                "A new epic can only hold stories",
            ),
            ({"items": [refs["billing2"]]}, "is already under"),
            ({"items": [done_story]}, "finished work stays"),
            ({"parent": None, "new_epic": {"title": "   "}}, "needs a title"),
            ({"items": ["story:99999"]}, "no story 99999"),
            ({"parent": "epic:99999"}, "no epic 99999"),
        ]
        for change, wanted in cases:
            arguments = {**base, **change}
            arguments = {k: v for k, v in arguments.items() if v is not None}
            said = words_of(await refused(agent, "propose_placement", **arguments))
            assert wanted in said, (change, said)
        assert sql_rows(project, "SELECT 1 FROM events WHERE action = 'placement_proposed'") == []
        # A secret in the reason is refused before anything else.
        said = await refused(
            agent, "propose_placement", **{**base, "reason": "key vrk_" + "a" * 40}
        )
        assert "looks like" in said
        # An item that is already in a waiting proposal cannot be in a second one.
        await ok(agent, "propose_placement", **base)
        said = words_of(
            await refused(agent, "propose_placement", **{**base, "parent": refs["mail"]})
        )
        assert "already in proposal" in said


async def test_a_proposer_cannot_have_more_than_twenty_five_waiting() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        inbox = await node(agent, "epic", "Inbox")
        target = await node(agent, "epic", "Billing", body="invoice payment")
        stories = [await node(agent, "story", f"Loose {n}", inbox) for n in range(26)]
        for ref in stories[:25]:
            await ok(agent, "propose_placement", items=[ref], parent=target, reason="Billing.")
        said = words_of(
            await refused(
                agent, "propose_placement", items=[stories[25]], parent=target, reason="Billing."
            )
        )
        assert "already have 25 proposals waiting" in said
        assert (await ok(agent, "list_placements", status="proposed", limit=10))["total"] == 25


async def test_the_database_refuses_a_malformed_proposal_or_a_decision_by_an_agent() -> None:
    project = make_project(register())
    base = (
        "INSERT INTO events (project_id, node, action, actor, actor_kind, after) "
        f"VALUES ({project.id}, NULL, "
    )
    good_items = '[{"ref": "story:1", "title": "t", "from_parent": "epic:1"}]'
    cases = (
        ("'placement_proposed', 'a', 'agent', '{}'", "placement_proposed_has_its_words"),
        (
            f"'placement_proposed', 'a', 'agent', '{{\"items\": {good_items}, \"reason\": \"r\"}}'",
            "placement_proposed_has_its_words",
        ),
        (
            f"'placement_proposed', 'a', 'agent', '{{\"items\": {good_items}, \"reason\": \"r\", "
            '"parent": "epic:2", "new_epic": {"title": "x"}}\'',
            "placement_proposed_has_its_words",
        ),
        (
            "'placement_proposed', 'a', 'agent', '{\"items\": [], \"reason\": \"r\", "
            '"parent": "epic:2"}\'',
            "placement_proposed_has_its_words",
        ),
        (
            "'placement_accepted', 'a', 'agent', '{\"placement\": 1}'",
            "placement_decided_names_its_placement",
        ),
        (
            "'placement_rejected', 'a', 'person', '{}'",
            "placement_decided_names_its_placement",
        ),
    )
    for values, constraint in cases:
        result = run_sql(f"{base}{values})", plain=True, project=project.id)
        assert result.returncode != 0 and constraint in result.stderr, (constraint, result.stderr)
    node_row = (
        "INSERT INTO events (project_id, node, action, actor, actor_kind, after) "
        f"VALUES ({project.id}, 'epic:1', 'placement_proposed', 'a', 'agent', "
        f'\'{{"items": {good_items}, "reason": "r", "parent": "epic:2"}}\')'
    )
    result = run_sql(node_row, plain=True, project=project.id)
    assert result.returncode != 0 and "event_names_what_it_is_about" in result.stderr


async def test_proposals_and_their_decisions_travel_through_an_export_and_an_import() -> None:
    owner = register()
    source = make_project(owner)
    async with connect(source.mcp_url, source.key) as agent:
        refs = await a_sorted_project(agent)
        accepted = await ok(
            agent,
            "propose_placement",
            items=[refs["loose_pay"]],
            parent=refs["billing"],
            reason="Billing.",
        )
        rejected = await ok(
            agent,
            "propose_placement",
            items=[refs["loose_mail"]],
            parent=refs["mail"],
            reason="Mail.",
        )
        grouped = await ok(
            agent,
            "propose_placement",
            items=[refs["loose_login"]],
            new_epic={"title": "Sign in"},
            reason="A group.",
        )
        assert (
            person_call(
                source, owner, "decide_placement", placement=accepted["id"], accepted=True
            ).status
            == 200
        )
        assert (
            person_call(
                source,
                owner,
                "decide_placement",
                placement=rejected["id"],
                accepted=False,
                reason="No",
            ).status
            == 200
        )
        file = await export_everything(agent)
    assert {"placement_proposed", "placement_accepted", "placement_rejected"} <= {
        e["action"] for e in file["events"]
    }
    target = make_project(register())
    async with connect(target.mcp_url, target.key) as client:
        await ok(client, "import_graph", document=file)
        page = await ok(client, "list_placements")
        by_id = {p["id"]: p for p in page["items"]}
        assert {p["state"] for p in by_id.values()} == {"accepted", "rejected", "proposed"}
        assert by_id[accepted["id"]]["applied"] == [refs["loose_pay"]]
        assert by_id[rejected["id"]]["decision_reason"] == "No"
        assert by_id[grouped["id"]]["new_epic"]["title"] == "Sign in"
        needs = await ok(client, "needs_a_person")
        assert [i["kind"] for g in needs["groups"] for i in g["items"]] == ["placement"]
    tampers: list[tuple[str, Callable[[dict[str, Any]], object], str]] = [
        (
            "placement_proposed",
            lambda e: e["after"].__setitem__("reason", " "),
            "needs its reason",
        ),
        (
            "placement_proposed",
            lambda e: e["after"].__setitem__("new_epic", {"title": "x"}),
            "give parent or new_epic, and not both",
        ),
        (
            "placement_accepted",
            lambda e: e["after"].__setitem__("placement", 9999),
            "is not a proposal in the file",
        ),
        (
            "placement_rejected",
            lambda e: e.__setitem__("actor_kind", "agent"),
            "decided by a person",
        ),
    ]
    for action, change, wanted in tampers:
        broken = _copy(file)
        event = next(e for e in broken["events"] if e["action"] == action)
        change(event)
        fresh = make_project(register())
        async with connect(fresh.mcp_url, fresh.key) as client:
            said = await refused(client, "import_graph", document=broken)
        assert wanted in said, (action, said)


def _copy(document: dict[str, Any]) -> dict[str, Any]:
    import copy

    return copy.deepcopy(document)


async def test_the_steward_script_proposes_only_where_it_is_sure_and_cannot_move_anything() -> None:
    owner = register()
    project = make_project(owner)
    key = make_limited_key(
        project, "steward", ["list_nodes", "suggest_parent", "list_placements", "propose_placement"]
    )
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
    dry = run_script("steward.py", key, project.mcp_url, "--dry-run", "--min-score", "0.1")
    assert dry.returncode == 0, dry.stderr
    assert dry.stdout.count("would propose") == 3 and "would make 3 proposals" in dry.stdout
    assert "left 1 stories" in dry.stdout
    async with connect(project.mcp_url, key) as steward:
        assert (await ok(steward, "list_placements"))["total"] == 0
    ran = run_script("steward.py", key, project.mcp_url, "--min-score", "0.1")
    assert ran.returncode == 0, ran.stderr
    assert "made 3 proposals" in ran.stdout
    async with connect(project.mcp_url, key) as steward:
        waiting = (await ok(steward, "list_placements", status="proposed"))["items"]
        where = {item["ref"]: one["parent"] for one in waiting for item in one["items"]}
        assert where == {
            refs["loose_pay"]: refs["billing"],
            refs["loose_mail"]: refs["mail"],
            refs["loose_login"]: refs["access"],
        }
        assert {one["proposed_by"] for one in waiting} == {"steward"}
        assert all(one["reason"].startswith("Nearest by meaning to ") for one in waiting)
        # It can propose and read, and it cannot move, confirm or decide anything.
        for tool, arguments in (
            (
                "move_node",
                {"node": refs["loose_pay"], "new_parent": refs["billing"], "expected_version": 1},
            ),
            ("decide_placement", {"placement": waiting[0]["id"], "accepted": True}),
            ("set_confirmation", {"note_id": 1, "confirmation": "confirmed"}),
        ):
            assert "not allowed to use" in words_of(await refused(steward, tool, **arguments))
    # Nothing moved, and a second run leaves what is waiting alone.
    async with connect(project.mcp_url, project.key) as agent:
        for ref in (refs["loose_pay"], refs["loose_mail"], refs["loose_login"]):
            assert (await ok(agent, "get_node", node=ref))["parent"] == refs["inbox"]
    again = run_script("steward.py", key, project.mcp_url, "--min-score", "0.1")
    assert again.returncode == 0 and "made 0 proposals" in again.stdout, again.stdout
    # A person accepts all three at once on the board's own calls, and the Inbox keeps what is left.
    for one in waiting:
        done = person_call(project, owner, "decide_placement", placement=one["id"], accepted=True)
        assert done.status == 200, done.body
    async with connect(project.mcp_url, project.key) as agent:
        left = (await ok(agent, "list_nodes", kind="story", parent=refs["inbox"]))["items"]
        assert [n["ref"] for n in left] == [refs["loose_story"]]


# -- how each moved item scores, and what is routine (M50) -----------------------------------------


async def proposed(
    client: Client, items: list[str], parent: str, reason: str = "Looks alike."
) -> int:
    made = await ok(client, "propose_placement", items=items, parent=parent, reason=reason)
    return int(made["id"])


async def read_proposal(client: Client, number: int) -> dict[str, Any]:
    page = await ok(client, "list_placements", status="proposed")
    (one,) = [p for p in page["items"] if p["id"] == number]
    found: dict[str, Any] = one
    return found


async def test_each_moved_item_scores_as_suggest_parent_scores_it_for_the_proposed_parent() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        number = await proposed(
            agent, [refs["loose_pay"], refs["loose_mail"]], refs["billing"], "Both about money."
        )
        one = await read_proposal(agent, number)
        assert one["scores_note"] is None and [s["ref"] for s in one["scores"]] == [
            refs["loose_pay"],
            refs["loose_mail"],
        ]
        for score in one["scores"]:
            asked = await ok(agent, "suggest_parent", node=score["ref"], limit=10)
            ranked = [c["ref"] for c in asked["suggestions"]]
            mine = asked["suggestions"][ranked.index(refs["billing"])]
            assert score["similarity"] == pytest.approx(mine["score"], abs=1e-9)
            assert score["rank"] == ranked.index(refs["billing"]) + 1
            assert score["ranked"] is None or score["ranked"] >= 1
            assert score["best"] == asked["suggestions"][0]["ref"]
            assert score["best_similarity"] == pytest.approx(asked["suggestions"][0]["score"])
            assert score["margin"] == pytest.approx(asked["margin"])
            assert score["sure"] is asked["sure"]
            assert score["warning"] is None and score["note"] is None
        pay, mail = one["scores"]
        assert pay["rank"] == 1 and pay["sure"] is True
        assert mail["rank"] != 1 and mail["best"] == refs["mail"]
        assert pay["similarity"] > mail["similarity"]
        # A score is evidence, never an approval: nothing moved and nothing was decided.
        assert one["state"] == "proposed" and one["applied"] == []
        assert (await ok(agent, "get_node", node=refs["loose_pay"]))["parent"] == refs["inbox"]
        # It is only worked out when asked for with the list: the needs row carries none.
        row = (await ok(agent, "needs_a_person"))["groups"][0]["items"][0]
        assert row["kind"] == "placement"
        assert (row["placement"]["scores"], row["placement"]["routine"]) == (None, None)


async def test_a_proposal_is_routine_only_when_every_item_clearly_belongs_and_says_why() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        fine = await proposed(agent, [refs["loose_pay"]], refs["billing"])
        mixed = await proposed(agent, [refs["loose_mail"], refs["loose_login"]], refs["billing"])
        for number, routine in ((fine, True), (mixed, False)):
            one = await read_proposal(agent, number)
            assert one["routine"] is routine, one["routine_why"]
        yes = await read_proposal(agent, fine)
        assert yes["routine_why"] == "E-" + refs["billing"].split(":")[1] + (
            " is the clearly best parent for the item."
        )
        no = await read_proposal(agent, mixed)
        assert "ranks" in no["routine_why"] or "not clearly ahead" in no["routine_why"]
        # Both are still in the list in full, and both wait for a person.
        page = await ok(agent, "list_placements", status="proposed")
        assert {p["id"] for p in page["items"]} == {fine, mixed}


async def test_work_under_way_or_moved_since_is_a_warning_and_never_routine() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        number = await proposed(
            agent, [refs["loose_pay"], refs["loose_login"]], refs["billing"], "Pay and sign in."
        )
        await ok(agent, "claim_node", node=refs["loose_pay"])
        version = (await ok(agent, "get_node", node=refs["loose_login"]))["version"]
        await ok(
            agent,
            "move_node",
            node=refs["loose_login"],
            new_parent=refs["access"],
            expected_version=version,
        )
        one = await read_proposal(agent, number)
        pay, login = one["scores"]
        assert "is held by" in pay["warning"] and pay["similarity"] is not None
        assert "was moved to" in login["warning"]
        assert one["routine"] is False and one["routine_why"] == pay["warning"]
        for score in one["scores"]:
            assert score["warning"] and score["ref"] in (refs["loose_pay"], refs["loose_login"])


async def test_a_new_epic_has_nothing_to_score_against_and_is_never_routine() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        made = await ok(
            agent,
            "propose_placement",
            items=[refs["loose_pay"], refs["loose_mail"]],
            new_epic={"title": "Money and messages", "body": ""},
            reason="Two themes.",
        )
        one = await read_proposal(agent, made["id"])
        assert one["scores"] == [] and "no parent that exists" in one["scores_note"]
        assert one["routine"] is False and "new epic" in one["routine_why"]


async def test_an_item_that_cannot_be_embedded_says_so_and_a_decided_proposal_has_no_scores() -> (
    None
):
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        with embeddings_stopped():
            # Made while the service is away, so it is not indexed and cannot be embedded now.
            fresh = await node(agent, "story", "Pay for the late invoice", refs["inbox"])
            number = await proposed(agent, [fresh], refs["billing"])
            one = await read_proposal(agent, number)
        (score,) = one["scores"]
        why = one["scores_note"] or score["note"]
        assert score["similarity"] is None and score["rank"] is None
        assert why and "reach" in why
        assert one["routine"] is False and one["routine_why"] == why
        # Decided, the proposal is history: no scores, no routine.
        decided = person_call(
            project, owner, "decide_placement", placement=number, accepted=False, reason="No."
        )
        assert decided.status == 200 and decided.body["scores"] is None
        page = await ok(agent, "list_placements", status="rejected")
        assert page["items"][0]["scores"] is None and page["items"][0]["routine"] is None
