"""M45: asks (a question an agent puts to a person), the Needs a person list and the stricter
change feed (against the running containers, through a real MCP client and the board's HTTP API).

What has to hold: an agent asks with choices or as an approval when it blocks or leaves work, and
the work is blocked or parked as asked; the ask and the answer are events that are never edited, the
answer reaches the agent in `get_node`, `next_work` and the feed, and only a person gives it, once,
as one of the choices; a bad or secret ask is refused and stores nothing; the database refuses an
answer that is not a person's or a second answer; everything travels through an export and an
import; `needs_a_person` lists exactly what waits for a person, by story, and shrinks as it is
settled; and the feed's cursor, truncated flag and poll interval are right. Each test works in a
project of its own."""

import copy
import hashlib
from collections.abc import Callable
from typing import Any

import anyio
import pytest
from mcp import Client

from tests.helpers import connect, export_everything, make_tree, ok, refused, run_sql
from tests.test_checks_integration import A_TOKEN, as_person, define, sql_rows, words_of
from tests.world import (
    HARNESS_AGENT,
    Person,
    Project,
    join_project,
    make_key,
    make_limited_key,
    make_project,
    register,
)
from vrum.markdown import render

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

CHOICES = ["Stripe", "Adyen", "Neither"]
QUESTION = "Which payment provider should the checkout use?"


async def a_task(client: Client) -> dict[str, Any]:
    _, _, task = await make_tree(client)
    return task


async def another_task(client: Client, story: str, title: str) -> dict[str, Any]:
    return await ok(client, "create_node", actor="x", kind="task", title=title, parent=story)


def answer(project: Project, who: Person, ask: int, words: str, **more: Any) -> Any:
    return as_person(who, project, "answer_ask", ask=ask, answer=words, **more)


async def ask_blocking(agent: Client, task: dict[str, Any], **ask: Any) -> int:
    """The agent claims the task and leaves it blocked with an ask; returns the ask's number."""
    await ok(agent, "claim_node", node=task["ref"])
    await ok(
        agent,
        "finish_work",
        node=task["ref"],
        outcome="blocked",
        note="I need a decision before I can go on.",
        ask={"question": QUESTION, "choices": CHOICES, **ask},
    )
    asks = (await ok(agent, "get_node", node=task["ref"]))["asks"]
    return int(asks[0]["id"])


async def test_an_agent_asks_blocked_and_a_person_answers_once_and_the_work_is_free_again() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        number = await ask_blocking(agent, task)

        detail = await ok(agent, "get_node", node=task["ref"])
        assert detail["status"] == "blocked"
        (open_ask,) = detail["asks"]
        assert (open_ask["question"], open_ask["kind"], open_ask["choices"]) == (
            QUESTION,
            "question",
            CHOICES,
        )
        assert (open_ask["effect"], open_ask["answered"], open_ask["answer"]) == (
            "blocks",
            False,
            None,
        )
        assert (open_ask["asked_by"], open_ask["asked_by_kind"]) == (HARNESS_AGENT, "agent")
        listed = await ok(agent, "list_asks", status="open")
        assert listed["total"] == 1 and listed["items"][0]["id"] == number
        assert (await ok(agent, "list_asks", status="answered"))["total"] == 0

        # It is in the feed, as an event about the task, and its id is the ask's number.
        feed = await ok(agent, "changes_since", node=task["ref"])
        raised = next(e for e in feed["items"] if e["action"] == "ask_raised")
        assert raised["id"] == number and raised["after"]["effect"] == "blocks"
        assert raised["actor"] == HARNESS_AGENT

        # An agent cannot answer, whatever it says.
        said = words_of(await refused(agent, "answer_ask", ask=number, answer="Stripe"))
        assert "answer_ask" in said or "person" in said

        # A wrong answer is refused with the choices; the right one is taken in any case.
        bad = answer(project, owner, number, "PayPal")
        assert bad.status == 400 and "Stripe, Adyen, Neither" in bad.body["message"]
        given = answer(project, owner, number, "adyen", comment="They cover the markets we need.")
        assert given.status == 200, given.body
        assert given.body["answered"] and given.body["answer"] == "Adyen"
        assert given.body["answered_by"] == owner.name
        assert given.body["comment"] == "They cover the markets we need."
        # Final: a second answer is refused and says who answered.
        again = answer(project, owner, number, "Stripe")
        assert again.status == 400 and "already answered" in again.body["message"]
        assert owner.name in again.body["message"]

        # The work was blocked for it and is free again, by the person's own change.
        freed = await ok(agent, "get_node", node=task["ref"])
        assert freed["status"] == "ready"
        history = (await ok(agent, "node_history", node=task["ref"]))["items"]
        answered = next(e for e in history if e["action"] == "ask_answered")
        assert (answered["actor"], answered["actor_kind"]) == (owner.name, "person")
        assert answered["after"] == {
            "ask": number,
            "answer": "Adyen",
            "comment": "They cover the markets we need.",
        }
        status_change = history[-1]
        assert status_change["action"] == "update" and status_change["actor"] == owner.name

        # The next agent that takes the work reads the question and the answer in its briefing.
        taken = await ok(agent, "next_work")
        (briefed,) = taken["context"]["asks"]
        assert (briefed["id"], briefed["answer"], briefed["answered_by"]) == (
            number,
            "Adyen",
            owner.name,
        )


async def test_an_approval_parks_the_work_and_leaves_it_free_to_take() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        await ok(agent, "claim_node", node=task["ref"])
        await ok(
            agent,
            "finish_work",
            node=task["ref"],
            outcome="released",
            note="Parked while a person decides.",
            ask={"question": "Is it fine to drop the old export format?", "approval": True},
        )
        (parked,) = (await ok(agent, "get_node", node=task["ref"]))["asks"]
        assert (parked["kind"], parked["effect"], parked["choices"]) == (
            "approval",
            "parks",
            ["yes", "no"],
        )
        # Not blocked: someone else can take it while the ask waits.
        assert (await ok(agent, "get_node", node=task["ref"]))["status"] == "ready"
        await ok(agent, "claim_node", node=task["ref"])
        await ok(agent, "release_claim", node=task["ref"], note="Back.")
        bad = answer(project, owner, parked["id"], "maybe")
        assert bad.status == 400 and "yes, no" in bad.body["message"]
        assert answer(project, owner, parked["id"], "No").status == 200
        # Answering a parked ask changes nothing about the work.
        assert (await ok(agent, "get_node", node=task["ref"]))["status"] == "ready"
        assert (await ok(agent, "list_asks", status="answered"))["items"][0]["answer"] == "no"


async def test_two_blocking_asks_free_the_work_only_when_both_are_answered() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        version = task["version"]
        blocked = await ok(
            agent,
            "update_node",
            node=task["ref"],
            expected_version=version,
            status="blocked",
            ask={"question": "First thing?"},
        )
        assert blocked["status"] == "blocked"
        # A second blocking ask on work that is already blocked, by finish_work from a holder.
        other = make_key(project, "second-agent")
        async with connect(project.mcp_url, other) as second:
            await ok(
                second,
                "update_node",
                node=task["ref"],
                expected_version=blocked["version"],
                status="new",
            )
            await ok(second, "claim_node", node=task["ref"])
            await ok(
                second,
                "finish_work",
                node=task["ref"],
                outcome="blocked",
                note="Another thing.",
                ask={"question": "Second thing?"},
            )
        asks = (await ok(agent, "get_node", node=task["ref"]))["asks"]
        assert [a["question"] for a in asks] == ["Second thing?", "First thing?"]
        first, second_ask = asks[1]["id"], asks[0]["id"]
        assert answer(project, owner, first, "Go ahead").status == 200
        assert (await ok(agent, "get_node", node=task["ref"]))["status"] == "blocked"
        assert answer(project, owner, second_ask, "And this too").status == 200
        assert (await ok(agent, "get_node", node=task["ref"]))["status"] == "ready"


async def test_a_misplaced_or_badly_made_ask_is_refused_and_stores_nothing() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        epic, _, task = await make_tree(agent)
        fresh = await ok(agent, "get_node", node=task["ref"])
        ask = {"question": QUESTION}
        for tool, arguments, wanted in (
            (
                "update_node",
                {"node": task["ref"], "expected_version": fresh["version"], "priority": "high"},
                "goes with marking the work blocked",
            ),
            (
                "update_node",
                {"node": epic["ref"], "expected_version": epic["version"], "status": "blocked"},
                "is an epic",
            ),
        ):
            words = words_of(await refused(agent, tool, ask=ask, **arguments))
            assert wanted in words and "Nothing was changed" in words, words
        await ok(agent, "claim_node", node=task["ref"])
        words = words_of(
            await refused(agent, "finish_work", node=task["ref"], outcome="done", note="x", ask=ask)
        )
        assert "not finished" in words and "Nothing was changed" in words
        # Badly made.
        for bad, wanted in (
            ({"question": "   "}, "needs its question"),
            ({"question": "x" * 1001}, "at most 1,000"),
            ({"question": QUESTION, "choices": ["only one"]}, "two to 8"),
            ({"question": QUESTION, "choices": [str(n) for n in range(9)]}, "two to 8"),
            ({"question": QUESTION, "choices": ["a", "A"]}, "different"),
            ({"question": QUESTION, "choices": ["a", " "]}, "not blank"),
            ({"question": QUESTION, "approval": True, "choices": ["a", "b"]}, "not both"),
        ):
            words = words_of(
                await refused(
                    agent,
                    "finish_work",
                    node=task["ref"],
                    outcome="blocked",
                    note="n",
                    ask=bad,
                )
            )
            assert wanted.lower() in words.lower(), (bad, words)
        # A secret in the question is refused and never repeated.
        said = await refused(
            agent,
            "finish_work",
            node=task["ref"],
            outcome="blocked",
            note="n",
            ask={"question": f"Use {A_TOKEN}?"},
        )
        assert A_TOKEN not in said
        # Nothing was stored, and the work is still held and not blocked.
        assert sql_rows(project, "SELECT 1 FROM events WHERE action = 'ask_raised'") == []
        held = await ok(agent, "get_node", node=task["ref"])
        assert held["status"] != "blocked" and held["claimed_by"] == HARNESS_AGENT


async def test_only_a_person_answers_and_a_viewer_cannot_and_a_secret_is_refused() -> None:
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer")
    editor = join_project(project, "editor")
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        number = await ask_blocking(agent, task)
    refusal = answer(project, viewer, number, "Stripe")
    assert refusal.status in (400, 403), refusal.body
    assert sql_rows(project, "SELECT 1 FROM events WHERE action = 'ask_answered'") == []
    # A viewer can read what is waiting; an agent never answers (its key cannot even call it).
    assert as_person(viewer, project, "list_asks").status == 200
    assert as_person(viewer, project, "needs_a_person").status == 200
    secret = answer(project, editor, number, f"Use {A_TOKEN}")
    assert secret.status == 400 and A_TOKEN not in secret.body["message"]
    blank = answer(project, editor, number, "  ")
    assert blank.status == 400 and "cannot be empty" in blank.body["message"]
    done = answer(project, editor, number, "Stripe")
    assert done.status == 200 and done.body["answered_by"] == editor.name


async def test_the_database_refuses_an_answer_that_is_not_a_persons_and_a_second_answer() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        number = await ask_blocking(agent, task)

    def insert(kind: str, ask: int) -> Any:
        return run_sql(
            "INSERT INTO events (project_id, node, action, actor, actor_kind, after) VALUES "
            f"({project.id}, '{task['ref']}', 'ask_answered', 'someone', '{kind}', "
            f'\'{{"ask": {ask}, "answer": "Stripe"}}\'::jsonb)',
            plain=True,
            project=project.id,
        )

    agent_answer = insert("agent", number)
    assert agent_answer.returncode != 0 and "ask_answered_has_its_words" in agent_answer.stderr
    assert insert("person", number).returncode == 0
    twice = insert("person", number)
    assert twice.returncode != 0 and "ask_is_answered_once" in twice.stderr
    blank = run_sql(
        "INSERT INTO events (project_id, node, action, actor, actor_kind, after) VALUES "
        f"({project.id}, '{task['ref']}', 'ask_raised', 'a', 'agent', "
        '\'{"question": " ", "kind": "question", "effect": "blocks"}\'::jsonb)',
        plain=True,
        project=project.id,
    )
    assert blank.returncode != 0 and "ask_raised_has_its_words" in blank.stderr


async def test_asks_travel_through_an_export_an_import_and_the_markdown_copy() -> None:
    owner = register()
    source = make_project(owner)
    async with connect(source.mcp_url, source.key) as agent:
        task = await a_task(agent)
        first = await ask_blocking(agent, task)
        answer(source, owner, first, "Stripe", comment="Cheapest.")
        other = await a_task(agent)
        second = await ask_blocking(agent, other, question="Still open?", choices=None)
        file = await export_everything(agent)
    raised = [e for e in file["events"] if e["action"] == "ask_raised"]
    assert [e["id"] for e in raised] == [first, second]

    target = make_project(register())
    async with connect(target.mcp_url, target.key) as client:
        await ok(client, "import_graph", document=file)
        everything = await ok(client, "list_asks")
        assert [(a["id"], a["answered"]) for a in everything["items"]] == [
            (first, True),
            (second, False),
        ]
        assert everything["items"][0]["answer"] == "Stripe"
        assert everything["items"][0]["comment"] == "Cheapest."

    text = "".join(render(file).values())
    assert "## Questions" in text
    assert f"- Q-{first} (work waited): {QUESTION} Choices: Stripe, Adyen, Neither." in text
    assert "Answer: Stripe, by " in text and "Comment: Cheapest." in text
    assert f"- Q-{second} (work waited): Still open? Asked by " in text
    assert "Not answered yet." in text

    # A file that answers an ask that is not in it, answers twice, or answers as an agent.
    answers = [e for e in file["events"] if e["action"] == "ask_answered"]
    assert len(answers) == 1
    tampers: list[tuple[str, Callable[[dict[str, Any]], object], str]] = [
        ("missing ask", lambda e: e["after"].__setitem__("ask", 9999), "is not an ask in the file"),
        ("an agent", lambda e: e.__setitem__("actor_kind", "agent"), "answered by a person"),
        ("blank", lambda e: e["after"].__setitem__("answer", " "), "cannot be empty"),
    ]
    for name, tamper, wanted in tampers:
        bad = copy.deepcopy(file)
        for event in bad["events"]:
            if event["action"] == "ask_answered":
                tamper(event)
        scratch = make_project(register())
        async with connect(scratch.mcp_url, scratch.key) as client:
            words = words_of(await refused(client, "import_graph", document=bad))
            assert wanted in words, (name, words)
            assert (await ok(client, "list_nodes"))["items"] == []
    twice = copy.deepcopy(file)
    dup = copy.deepcopy(answers[0])
    dup["id"] = max(e["id"] for e in twice["events"]) + 1
    twice["events"].append(dup)
    scratch = make_project(register())
    async with connect(scratch.mcp_url, scratch.key) as client:
        assert "answered twice" in words_of(await refused(client, "import_graph", document=twice))


# -- what waits for a person ---------------------------------------------------------------------

TESTS_CHECK = "Tests pass"
PERSON_CHECK = "A person has looked at it on a phone"


def rows(needs: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for group in needs["groups"] for item in group["items"]]


async def test_needs_a_person_lists_exactly_what_waits_by_story_and_shrinks_as_it_is_settled() -> (
    None
):
    owner = register()
    project = make_project(owner)
    define(owner, project, f"- {TESTS_CHECK}\n- [person] {PERSON_CHECK}\n")
    async with connect(project.mcp_url, project.key) as agent:
        assert (await ok(agent, "needs_a_person"))["total"] == 0
        epic = await ok(agent, "create_node", actor="x", kind="epic", title="Checkout")
        story = await ok(
            agent, "create_node", actor="x", kind="story", title="Pay", parent=epic["ref"]
        )
        quiet_story = await ok(
            agent, "create_node", actor="x", kind="story", title="Quiet", parent=epic["ref"]
        )
        asked = await another_task(agent, story["ref"], "Choose a provider")
        checked = await another_task(agent, story["ref"], "Build the form")
        noted = await another_task(agent, story["ref"], "Wire the webhook")
        lapsed = await another_task(agent, quiet_story["ref"], "Retry the call")
        await another_task(agent, quiet_story["ref"], "Nothing wrong here")

        # An open ask (blocking).
        number = await ask_blocking(agent, asked)
        # Work finished except for a person's check.
        await ok(agent, "claim_node", node=checked["ref"])
        await ok(agent, "set_check", node=checked["ref"], item=TESTS_CHECK, evidence="Ran them")
        await ok(
            agent,
            "finish_work",
            node=checked["ref"],
            outcome="blocked",
            note="Everything is built. A person should look at it on a phone.",
        )
        # An unconfirmed note on work that is in progress.
        await ok(agent, "claim_node", node=noted["ref"])
        note = await ok(
            agent,
            "add_note",
            text="The webhook retries for a day",
            why="Read it in the provider's docs",
            nodes=[noted["ref"]],
        )
        # A claim that ran out twice with nothing said: taken over once, lapsed again.
        await ok(agent, "claim_node", node=lapsed["ref"], lease_seconds=1)
        await anyio.sleep(1.5)
        other = make_key(project, "second-agent")
        async with connect(project.mcp_url, other) as second:
            await ok(second, "claim_node", node=lapsed["ref"], lease_seconds=1)
        await anyio.sleep(1.5)

        needs = await ok(agent, "needs_a_person")
        found = {(item["kind"], item["node"]): item for item in rows(needs)}
        assert set(found) == {
            ("ask", asked["ref"]),
            ("check", checked["ref"]),
            ("note", noted["ref"]),
            ("lapse", lapsed["ref"]),
        }, found.keys()
        assert needs["total"] == 4 and needs["left_out"] == 0
        # By story: the three under Pay together, the lapse under Quiet apart.
        by_story = {g["story"]: {(i["kind"]) for i in g["items"]} for g in needs["groups"]}
        assert by_story == {
            story["ref"]: {"ask", "check", "note"},
            quiet_story["ref"]: {"lapse"},
        }
        titles = {g["story"]: g["title"] for g in needs["groups"]}
        assert titles == {story["ref"]: "Pay", quiet_story["ref"]: "Quiet"}
        # Why it is there, how long, who is waiting.
        ask_row = found[("ask", asked["ref"])]
        assert ask_row["ask"]["id"] == number and ask_row["reason"] == QUESTION
        assert (ask_row["waiting"], ask_row["waiting_kind"]) == (HARNESS_AGENT, "agent")
        check_row = found[("check", checked["ref"])]
        assert PERSON_CHECK in check_row["reason"] and check_row["count"] == 1
        assert check_row["waiting"] == HARNESS_AGENT
        note_row = found[("note", noted["ref"])]
        assert note_row["notes"] == [note["id"]] and "not yet confirmed" in note_row["reason"]
        lapse_row = found[("lapse", lapsed["ref"])]
        assert lapse_row["count"] == 2 and "ran out 2 times" in lapse_row["reason"]
        # Longest waiting first, and nothing is told to the agent that a person must do.
        assert all(
            rows(needs)[i]["waiting_since"] <= rows(needs)[i + 1]["waiting_since"]
            for i in range(len(rows(needs)) - 1)
        )

        # The board reads the same list.
        board = as_person(owner, project, "needs_a_person")
        assert board.status == 200 and board.body["total"] == 4

        # Settling each one makes its row go.
        assert answer(project, owner, number, "Stripe").status == 200
        assert (await ok(agent, "needs_a_person"))["total"] == 3
        assert (
            as_person(
                owner, project, "set_check", node=checked["ref"], item=_key(PERSON_CHECK)
            ).status
            == 200
        )
        assert (await ok(agent, "needs_a_person"))["total"] == 2
        confirm = owner.call(
            "POST",
            f"/board/api/projects/{project.id}/tool/set_confirmation",
            {"note_id": note["id"], "confirmation": "confirmed", "person": owner.name},
        )
        assert confirm.status == 200, confirm.body
        after = await ok(agent, "needs_a_person")
        assert [(i["kind"], i["node"]) for i in rows(after)] == [("lapse", lapsed["ref"])]
        # Taken again, and it is no longer waiting for anyone.
        await ok(agent, "claim_node", node=lapsed["ref"])
        assert (await ok(agent, "needs_a_person"))["total"] == 0


def _key(words: str) -> str:
    return hashlib.sha256(words.encode()).hexdigest()[:8]


async def test_a_handoff_note_left_after_the_claims_ran_out_takes_the_work_off_the_list() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        await ok(agent, "claim_node", node=task["ref"], lease_seconds=1)
        await anyio.sleep(1.5)
        other = make_key(project, "second-agent")
        async with connect(project.mcp_url, other) as second:
            await ok(second, "claim_node", node=task["ref"], lease_seconds=1)
        await anyio.sleep(1.5)
        listed = await ok(agent, "needs_a_person")
        assert [(row["kind"], row["node"]) for row in rows(listed)] == [("lapse", task["ref"])]

        # A person looks into it and blocks it with a reason: that is a handoff note, so the claim
        # that ran out is no longer something nobody said anything about.
        version = (await ok(agent, "get_node", node=task["ref"]))["version"]
        await ok(
            agent,
            "update_node",
            node=task["ref"],
            expected_version=version,
            status="blocked",
            why="The provider's sandbox is down.",
        )
        assert (await ok(agent, "needs_a_person"))["total"] == 0


async def test_a_default_agent_key_is_not_offered_the_asks_tools_and_cannot_answer() -> None:
    project = make_project(register())
    plain = make_limited_key(project, "standard-agent", None)
    async with connect(project.mcp_url, plain) as client:
        offered = {tool.name for tool in (await client.list_tools()).tools}
        assert {"list_asks", "answer_ask", "needs_a_person"}.isdisjoint(offered)
        assert len(offered) <= 25
        # The ask itself is an argument of tools it already has.
        by_name = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert "ask" in by_name["finish_work"].input_schema["properties"]
        assert "ask" in by_name["update_node"].input_schema["properties"]


# -- the change feed -----------------------------------------------------------------------------


async def test_the_feed_gives_an_opaque_cursor_a_truncated_flag_and_a_poll_interval() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        _, _, task = await make_tree(agent)
        for n in range(4):
            await ok(agent, "claim_node", node=task["ref"])
            await ok(agent, "release_claim", node=task["ref"], note=f"Round {n}")
        everything = await ok(agent, "changes_since", limit=200)
        ids = [e["id"] for e in everything["items"]]
        assert everything["truncated"] is False and everything["poll_after_seconds"] == 5.0
        assert everything["next_after_id"] is None and len(ids) > 6

        # Paged by the cursor, three at a time: truncated until the last page.
        seen: list[int] = []
        cursor = None
        flags = []
        while True:
            page = await ok(
                agent, "changes_since", limit=3, **({"cursor": cursor} if cursor else {})
            )
            seen.extend(e["id"] for e in page["items"])
            flags.append(page["truncated"])
            assert page["cursor"] and page["cursor"] != str(page["latest_event_id"])
            cursor = page["cursor"]
            if not page["truncated"]:
                break
        assert seen == ids and flags[-1] is False and all(flags[:-1])
        # The cursor of the last page continues from the end: nothing new, then one new thing.
        quiet = await ok(agent, "changes_since", cursor=cursor)
        assert quiet["items"] == [] and quiet["truncated"] is False
        await ok(agent, "claim_node", node=task["ref"])
        news = await ok(agent, "changes_since", cursor=quiet["cursor"])
        assert [e["action"] for e in news["items"]] == ["claimed"]

        # It means what after_event_id means, and says so when it is not one of ours or both.
        assert (await ok(agent, "changes_since", after_event_id=ids[-1]))["items"][0]["action"] == (
            "claimed"
        )
        words = words_of(await refused(agent, "changes_since", cursor="not-a-cursor"))
        assert "not one this server gave" in words
        words = words_of(
            await refused(agent, "changes_since", cursor=cursor, after_event_id=ids[0])
        )
        assert "not both" in words
        # Newest first mode also gives a cursor to read forward from now.
        newest = await ok(agent, "changes_since", newest=True, limit=2)
        assert len(newest["items"]) == 2 and newest["truncated"] is True
        assert (await ok(agent, "changes_since", cursor=newest["cursor"]))["items"] == []
