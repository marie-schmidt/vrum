"""Chapter of the acceptance scenario for one standard review (M52): what the Needs a person list
says about each of its rows, told from outside through a real MCP client and the board's own calls
as the people of a project, and then (tests/acceptance_standard_page.py) a headless browser on the
list and the review page.

Every row of `needs_a_person` carries three fields the server works out: `level` (plan, epic,
story, task or note: how strategic the row is), `decision` (approve, answer or look_into: what a
person is asked to do) and `topic` (what it is about, so that rows about the same thing are settled
together: an epic, or a group of rows whose work is alike in meaning, across epics, from the vectors
the index already holds). The board only regroups the flat rows by these fields. The scripted
embedding service of the stack stands in for the model: texts that use words of one concept group
have equal vectors, so the answers are the same every run. Each part works in a project of its own.
"""

import asyncio
import re
from typing import Any

from mcp import Client

from tests.acceptance_scenario import chapter, clip, proof
from tests.helpers import REPO_ROOT, connect, embeddings_stopped, ok, reindexed
from tests.test_checks_integration import as_person, define
from tests.world import Person, Project, join_project, make_key, make_project, register

DOCS = REPO_ROOT / "docs" / "agents.md"
THE_FIRST_LINE_IN_THE_DOCS = "// What waits for a person, by topic"
TESTS = "Tests pass"
LOOKED = "A person has looked at it on a phone"


def words(refusal: str) -> str:
    return refusal.split(": ", 1)[1] if refusal.startswith("Error executing tool") else refusal


def rows_of(needs: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for group in needs["groups"] for item in group["items"]]


async def make(
    agent: Client, kind: str, title: str, parent: str | None = None, **more: Any
) -> dict[str, Any]:
    arguments: dict[str, Any] = {"kind": kind, "title": title, **more}
    if parent is not None:
        arguments["parent"] = parent
    return await ok(agent, "create_node", **arguments)


async def ask(agent: Client, task: dict[str, Any], question: str) -> None:
    """The agent claims the task and leaves it blocked with a question for a person."""
    await ok(agent, "claim_node", node=task["ref"])
    await ok(
        agent,
        "finish_work",
        node=task["ref"],
        outcome="blocked",
        note="I need a decision before I can go on.",
        ask={"question": question, "choices": ["Yes", "No"]},
    )


def which(row: dict[str, Any]) -> str:
    """The name of a row for this chapter: its kind, told apart where one kind holds two."""
    if row["kind"] == "placement":
        moved = row["placement"]["items"][0]["ref"]
        return "move of stories" if moved.startswith("story:") else "move of tasks"
    if row["kind"] == "ask":
        return "ask on " + str(row["node"]).split(":")[0]
    return str(row["kind"])


# What each kind of row asks of a person, and how strategic it is.
EXPECTED = {
    "plan": ("plan", "approve"),
    "move of stories": ("story", "approve"),
    "move of tasks": ("task", "approve"),
    "ask on task": ("task", "answer"),
    "ask on story": ("story", "answer"),
    "check": ("task", "approve"),
    "note": ("note", "approve"),
    "gate": ("task", "look_into"),
    "lapse": ("task", "look_into"),
}


async def every_kind(owner: Person, project: Project, agent: Client) -> dict[str, Any]:
    """One row of each of the eight kinds (an ask on a story as well as on a task, and a move of
    stories as well as one of tasks), a second plan that waits for its agent (in no row), and a
    second agent for the claim that runs out. Returns what it made, by name."""
    define(owner, project, f"- {TESTS}\n- [person] {LOOKED}\n")
    shop = await make(agent, "epic", "Everything a rider pays for at checkout")
    pay = await make(agent, "story", "Pay for a ride with a card on the phone", shop["ref"])
    quiet = await make(agent, "story", "Quiet", shop["ref"])
    currency = await make(agent, "story", "Pick a currency", shop["ref"])
    misc = await make(agent, "epic", "Misc")
    loose = [
        await make(agent, "story", name, misc["ref"]) for name in ("Refund screen", "Refund email")
    ]
    asked = await make(agent, "task", "Choose which payment provider the shop will use", pay["ref"])
    checked = await make(agent, "task", "Build the form", pay["ref"], size="s")
    noted = await make(agent, "task", "Wire the webhook", pay["ref"])
    lapsed = await make(agent, "task", "Retry the call", quiet["ref"])
    gated = await make(agent, "task", "Touch billing", quiet["ref"])
    sorting = [
        await make(agent, "task", name, quiet["ref"])
        for name in ("Sort the receipts", "Sort the statements")
    ]

    await ask(agent, asked, "Which payment provider should the shop use for cards?")
    await ok(
        agent,
        "update_node",
        node=currency["ref"],
        expected_version=currency["version"],
        status="blocked",
        ask={
            "question": "Which currency does the checkout show first?",
            "choices": ["The euro, for the whole city", "The dollar, as the airport asks"],
        },
    )
    await ok(agent, "claim_node", node=checked["ref"])
    await ok(agent, "set_check", node=checked["ref"], item=TESTS, evidence="Ran them")
    await ok(
        agent,
        "finish_work",
        node=checked["ref"],
        outcome="blocked",
        note="Built. A person should look at it on a phone.",
    )
    await ok(agent, "claim_node", node=noted["ref"])
    await ok(
        agent,
        "add_note",
        text="The webhook retries for a day",
        why="Read it in the provider's docs",
        nodes=[noted["ref"]],
    )
    gate = await ok(
        agent, "add_gate", node=gated["ref"], kind="approval", question="May we touch billing?"
    )
    refused_by = as_person(
        owner, project, "answer_ask", ask=gate["ask"], answer="no", comment="Not this week"
    )
    assert refused_by.status == 200, refused_by.body
    plan = await ok(
        agent,
        "create_plan",
        draft=True,
        title="Retries for payments that the provider turned down",
        items=[
            {
                "key": "s",
                "kind": "story",
                "title": "Retry a payment that the provider turned down",
                "body": "Try again twice, then tell the rider that the payment did not go through.",
                "parent": shop["ref"],
                "size": "m",
            },
            {
                "key": "t",
                "kind": "task",
                "title": "Retry the call again after a short wait",
                "parent": "s",
                "size": "s",
            },
        ],
    )
    later = await ok(
        agent,
        "create_plan",
        draft=True,
        title="Tyres later",
        items=[
            {"key": "s", "kind": "story", "title": "Tyres for the winter", "parent": shop["ref"]}
        ],
    )
    asked_for_changes = as_person(
        owner, project, "request_plan_changes", plan=later["plan"], revision=1, summary="Smaller."
    )
    assert asked_for_changes.status == 200, asked_for_changes.body
    stories_moved = await ok(
        agent,
        "propose_placement",
        items=[one["ref"] for one in loose],
        parent=shop["ref"],
        reason="Refunds belong with the checkout.",
    )
    tasks_moved = await ok(
        agent,
        "propose_placement",
        items=[one["ref"] for one in sorting],
        parent=pay["ref"],
        reason="Sorting is a part of paying.",
    )
    # A claim that ran out twice with nothing said: taken over once, and lapsed again.
    await ok(agent, "claim_node", node=lapsed["ref"], lease_seconds=1)
    await asyncio.sleep(1.5)
    async with connect(project.mcp_url, make_key(project, "second-agent")) as second:
        await ok(second, "claim_node", node=lapsed["ref"], lease_seconds=1)
    await asyncio.sleep(1.5)
    return {
        "shop": shop,
        "pay": pay,
        "quiet": quiet,
        "currency": currency,
        "misc": misc,
        "loose": loose,
        "asked": asked,
        "checked": checked,
        "noted": noted,
        "lapsed": lapsed,
        "gated": gated,
        "sorting": sorting,
        "gate": gate,
        "plan": plan["plan"],
        "later": later["plan"],
        "stories_moved": stories_moved["id"],
        "tasks_moved": tasks_moved["id"],
    }


async def the_rows_say_what_they_are() -> None:
    """Each of the eight kinds of row says its level and its decision, and carries a topic."""
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer")
    async with connect(project.mcp_url, project.key) as agent:
        await every_kind(owner, project, agent)
        await reindexed(agent)
        needs = await ok(agent, "needs_a_person")
        rows = rows_of(needs)
        found = {which(row): row for row in rows}
        said = {name: (row["level"], row["decision"]) for name, row in found.items()}
        complete = all(
            "level" in row and "decision" in row and "topic" in row for row in rows
        ) and all(
            row["topic"] is None or set(row["topic"]) == {"id", "label", "basis"} for row in rows
        )

        def fields(body: dict[str, Any]) -> dict[tuple[str, str], tuple[Any, ...]]:
            return {
                (row["kind"], row["node"]): (row["level"], row["decision"], row["topic"])
                for row in rows_of(body)
            }

        board = as_person(owner, project, "needs_a_person")
        seen_by_a_viewer = as_person(viewer, project, "needs_a_person")
        titles = [group["title"] for group in needs["groups"]]
        proof(
            "M52",
            "every row of needs_a_person carries its level, its decision and its topic: each of "
            "the eight kinds says what it is (a plan is a plan to approve, a suggested move is a "
            "story or a task to approve according to what it moves, an ask is a story or a task "
            "to answer, a check and unconfirmed notes are to approve (the notes are of level "
            "note), a refused gate and a claim that ran out twice are a task to look into), the "
            "list still comes by story with the plans and the suggested moves in groups of their "
            "own and its total counts every row, and the board's own route and a viewer read the "
            "very same fields",
            said == EXPECTED
            and len(rows) == len(EXPECTED) == needs["total"]
            and complete
            and all(row["topic"] is not None for row in rows)
            and titles[:2] == ["Plans waiting for approval", "Suggested moves"]
            and board.status == 200
            and fields(board.body) == fields(needs)
            and seen_by_a_viewer.status == 200
            and fields(seen_by_a_viewer.body) == fields(needs),
            f"{len(rows)} rows: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in said.items()),
        )


async def an_ask_about(agent: Client, epic: str, story: str, task: str) -> dict[str, Any]:
    """An epic, a story under it and a task under that, with a question put to a person."""
    made_epic = await make(agent, "epic", epic)
    made_story = await make(agent, "story", story, made_epic["ref"])
    made_task = await make(agent, "task", task, made_story["ref"])
    await ask(agent, made_task, f"Go ahead with {task}?")
    return {"epic": made_epic, "story": made_story, "task": made_task}


def by_node(needs: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["node"]: row for row in rows_of(needs)}


def named_rightly(topic: dict[str, Any], epics: dict[str, str], titles: set[str]) -> bool:
    """A topic of meaning is called by an epic (its reference and its title) or by a piece of work
    that is among the rows (topic:N and its title)."""
    if topic["id"] in epics:
        return bool(topic["label"] == epics[topic["id"]])
    return bool(re.fullmatch(r"topic:\d+", topic["id"]) and topic["label"] in titles)


async def alike_work_is_one_topic() -> Project:
    """Two tasks about alike work under different epics are one topic by meaning, a row alone in
    its meaning is under its epic, and two unalike rows under one epic are found together there.
    Returns the project, for the docs example, which reads the same rows."""
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        charge = await an_ask_about(
            agent, "Billing", "Invoice payment", "Charge the invoice payment"
        )
        bill = await an_ask_about(
            agent, "Accounts", "Subscription charge", "Bill the payment charge"
        )
        night = await an_ask_about(agent, "Appearance", "Dark theme", "Night theme switch")
        photo = await make(agent, "task", "Photo avatar upload", night["story"]["ref"])
        await ask(agent, photo, "Go ahead with the photo upload?")
        await reindexed(agent)
        rows = by_node(await ok(agent, "needs_a_person"))
        topics = {
            name: rows[one["task"]["ref"]]["topic"]
            for name, one in (("charge", charge), ("bill", bill), ("night", night))
        }
        epics = {one["epic"]["ref"]: one["epic"]["title"] for one in (charge, bill, night)}
        works = {rows[one["task"]["ref"]]["title"] for one in (charge, bill)}
        proof(
            "M52",
            "two tasks about alike work under different epics (charging an invoice payment under "
            "Billing and billing a payment charge under Accounts) are ONE topic whose basis is "
            "meaning and that is named by an epic or by one of the two tasks, a task that is "
            "alike nothing else is under its own epic (basis epic, the epic's reference and "
            "title), and a second task under that epic, about something else, has the very same "
            "topic, so rows under one epic are always found together",
            topics["charge"] == topics["bill"]
            and topics["charge"]["basis"] == "meaning"
            and named_rightly(topics["charge"], epics, works)
            and topics["night"]
            == {"id": night["epic"]["ref"], "label": "Appearance", "basis": "epic"}
            and rows[photo["ref"]]["topic"] == topics["night"]
            and topics["night"] != topics["charge"],
            f"{topics['charge']['id']} ({topics['charge']['label']}), {topics['night']['id']}",
        )
    return project


async def with_no_index_every_row_takes_its_epic() -> None:
    """With the embedding service stopped while the work is made, nothing has a vector: every row
    takes its epic, and two rows about alike work are not put together by a guess. Once the index
    has caught up they are one topic by meaning."""
    with embeddings_stopped():
        project = make_project(register())
        async with connect(project.mcp_url, project.key) as agent:
            one = await an_ask_about(
                agent, "Billing", "Invoice payment", "Charge the invoice payment"
            )
            two = await an_ask_about(
                agent, "Accounts", "Subscription charge", "Bill the payment charge"
            )
            needs = await ok(agent, "needs_a_person")
            rows = by_node(needs)
            first, second = rows[one["task"]["ref"]], rows[two["task"]["ref"]]
            without = (
                first["topic"] == {"id": one["epic"]["ref"], "label": "Billing", "basis": "epic"}
                and second["topic"]
                == {"id": two["epic"]["ref"], "label": "Accounts", "basis": "epic"}
                and len(rows) == needs["total"] == 2
            )
    async with connect(project.mcp_url, project.key) as agent:
        await reindexed(agent)
        again = by_node(await ok(agent, "needs_a_person"))
    after = again[one["task"]["ref"]]["topic"]
    proof(
        "M52",
        "with no index (the embedding service stopped while the work was made) every row takes "
        "its epic as its topic, with the basis epic, and two rows about alike work under "
        "different epics are NOT put together by a guess; once the index has caught up the same "
        "two rows are one topic by meaning",
        without and after == again[two["task"]["ref"]]["topic"] and after["basis"] == "meaning",
        f"{first['topic']['id']} and {second['topic']['id']}, then {after['id']}",
    )


async def the_example_in_the_docs(project: Project) -> None:
    """The TypeScript example in docs/agents.md, as written, runs in code mode and groups the rows
    by their topic, as the tool's own answer does."""
    text = DOCS.read_text(encoding="utf-8")
    blocks = re.findall(r"```ts\n(.*?)```", text, flags=re.S)
    (script,) = [block for block in blocks if block.startswith(THE_FIRST_LINE_IN_THE_DOCS)]
    async with connect(project.mcp_url, project.key) as agent:
        needs = await ok(agent, "needs_a_person")
    wanted: dict[str, dict[str, Any]] = {}
    for row in rows_of(needs):
        topic = row["topic"] or {"id": "other", "label": "Other", "basis": "epic"}
        found = wanted.setdefault(
            topic["id"],
            {"id": topic["id"], "label": topic["label"], "basis": topic["basis"], "rows": []},
        )
        found["rows"].append(f"{row['level']} to {row['decision']}: {row['node']}")
    async with connect(project.mcp_url, make_key(project, "coder", code_mode=True)) as coder:
        ran = await ok(coder, "run", code=script)
    result = ran["result"]
    proof(
        "M52",
        "the example in docs/agents.md, run as it is written in code mode, groups the rows of "
        "needs_a_person by topic.id: the same topics, labels, bases and rows as the tool's own "
        "answer, in the same order",
        result == list(wanted.values()) and len(result) == 2,
        clip(str(result), 120),
    )


async def standard() -> None:
    chapter("One standard review: what each row says about itself (M52)")
    await the_rows_say_what_they_are()
    project = await alike_work_is_one_topic()
    await with_no_index_every_row_takes_its_epic()
    await the_example_in_the_docs(project)
    # The page (tests/acceptance_standard_page.py and tests/acceptance_standard_decide.py) builds
    # on this file, so it is imported where it is run.
    from tests.acceptance_standard_page import standard_on_the_page  # noqa: PLC0415

    await standard_on_the_page()
