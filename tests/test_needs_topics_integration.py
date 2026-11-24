"""M52: what the Needs a person list says about each row (its level, the kind of decision it asks
for and the topic it is about), against the running containers through a real MCP client.

The topics are worked out from the stored vectors of the work the rows are about (the scripted
embedding service of the check stack gives texts about one idea the same vector), so rows about
alike work are one topic even under different epics, and a row alone in its meaning takes its
epic."""

from typing import Any

import pytest
from mcp import Client

from tests.helpers import connect, ok, reindexed
from tests.world import make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def a_task(agent: Client, epic: str, story: str, task: str) -> dict[str, Any]:
    made_epic = await ok(agent, "create_node", actor="x", kind="epic", title=epic)
    made_story = await ok(
        agent, "create_node", actor="x", kind="story", title=story, parent=made_epic["ref"]
    )
    return await ok(
        agent, "create_node", actor="x", kind="task", title=task, parent=made_story["ref"]
    )


async def ask_about(agent: Client, task: dict[str, Any], question: str) -> None:
    await ok(agent, "claim_node", node=task["ref"])
    await ok(
        agent,
        "finish_work",
        node=task["ref"],
        outcome="blocked",
        note="I need a decision.",
        ask={"question": question, "choices": ["Yes", "No"]},
    )


async def rows(agent: Client) -> dict[str, dict[str, Any]]:
    """Every row of the list, by the node it is about."""
    listed = await ok(agent, "needs_a_person")
    return {item["node"]: item for group in listed["groups"] for item in group["items"]}


async def test_rows_say_their_level_and_decision_and_group_by_topic() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        charge = await a_task(agent, "Billing", "Invoice payment", "Charge the invoice payment")
        bill = await a_task(agent, "Accounts", "Subscription charge", "Bill the payment charge")
        night = await a_task(agent, "Appearance", "Dark theme", "Night theme switch")
        for task in (charge, bill, night):
            await ask_about(agent, task, f"Go ahead with {task['title']}?")
        plan = await ok(
            agent,
            "create_plan",
            draft=True,
            title="Retry plan",
            items=[
                {"key": "e", "kind": "epic", "title": "Retries"},
                {"key": "s", "kind": "story", "title": "Retry a payment", "parent": "e"},
            ],
        )

        await reindexed(agent)
        after = await rows(agent)
        for task in (charge, bill, night):
            assert (after[task["ref"]]["level"], after[task["ref"]]["decision"]) == (
                "task",
                "answer",
            )
        together = after[charge["ref"]]["topic"], after[bill["ref"]]["topic"]
        assert together[0] == together[1]
        assert together[0]["basis"] == "meaning"
        assert together[0]["id"].startswith("topic:")
        assert together[0]["label"] in {charge["title"], bill["title"]}
        # The theme row is alike nothing else, so it is found by its epic.
        apart = after[night["ref"]]["topic"]
        assert apart["basis"] == "epic" and apart["label"] == "Appearance"
        assert apart["id"] == (await ok(agent, "get_node", node=night["parent"]))["parent"]

        listed = await ok(agent, "needs_a_person")
        (plan_row,) = [
            item for group in listed["groups"] for item in group["items"] if item["kind"] == "plan"
        ]
        assert plan_row["plan"]["id"] == plan["plan"]
        assert (plan_row["level"], plan_row["decision"]) == ("plan", "approve")
        # The plan retries payments, which is what the two billing rows are about.
        assert plan_row["topic"] is not None
