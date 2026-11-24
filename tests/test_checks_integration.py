"""The definition of done as tickable checks (M32) against the running containers. The list lines
of the project's definition of done are checks, each with a key that comes from its words, and a
story or task has each of them ticked or not, by an agent (who has to say how it checked) or a
person. What has to hold: which lines are checks; ticking and unticking, with who, when and how, in
the node's own history and in the change feed, with the node's version untouched; refusals in plain
words; what a change to the text of the definition does to ticks that exist; an agent is refused
`done` (from `finish_work`, `update_node` and `update_nodes`) while a check is unticked, naming
them, and a person never is; the briefing carries the checks of the item an agent is given; and
the export carries the ticks and loads them again. Each test works in a project of its own."""

import hashlib
import re
from typing import Any

import pytest
from mcp import Client

from tests.helpers import connect, export_everything, make_tree, ok, refused, rich_world, run_sql
from tests.world import (
    HARNESS_AGENT,
    Person,
    Project,
    Reply,
    join_project,
    make_limited_key,
    make_project,
    register,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

A_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2
TESTS, DOCS, TRIED = "Tests pass", "Docs are updated", "A person has tried it"


def key_of(words: str) -> str:
    """What the server calls a check: the first 8 hex digits of a SHA-256 of its words."""
    return hashlib.sha256(words.encode()).hexdigest()[:8]


def as_person(person: Person, project: Project, tool: str, **arguments: Any) -> Reply:
    """What the board does when a person presses a button: the tool, as that person."""
    return person.call("POST", f"/board/api/projects/{project.id}/tool/{tool}", arguments)


def define(owner: Person, project: Project, body: str) -> dict[str, Any]:
    """The owner sets the definition of done on the board, whatever version it is at."""
    current = as_person(owner, project, "get_definition_of_done")
    assert current.status == 200, current.body
    reply = as_person(
        owner,
        project,
        "set_definition_of_done",
        body=body,
        expected_version=current.body["version"],
    )
    assert reply.status == 200, reply.body
    return dict(reply.body)


def sql_rows(project: Project, sql: str) -> list[list[str]]:
    result = run_sql(sql, plain=True, project=project.id, as_project=True)
    assert result.returncode == 0, result.stderr
    return [line.split("|") for line in result.stdout.splitlines() if line]


def words_of(refusal: str) -> str:
    """A refusal as the server worded it, without the client's "Error executing tool" lead."""
    return re.sub(r"^Error executing tool \w+: ", "", refusal)


async def a_task(client: Client) -> dict[str, Any]:
    _, _, task = await make_tree(client)
    return task


async def checks_of(client: Client, node: dict[str, Any]) -> list[dict[str, Any]]:
    return list((await ok(client, "get_node", node=node["ref"]))["checks"])


async def history_of(client: Client, node: dict[str, Any]) -> list[dict[str, Any]]:
    return list((await ok(client, "node_history", node=node["ref"]))["items"])


def check_says(key: str, text: str, evidence: str | None) -> dict[str, Any]:
    """What a tick or an untick says in the history, about the check."""
    return {"item": key, "text": text, "evidence": evidence}


async def test_the_checks_are_the_list_lines_and_nothing_else() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        assert await checks_of(agent, task) == []
        body = (
            "# Done means\n\n"
            "Everything below has to hold.\n\n"
            f"- {TESTS}\n"
            f"* [ ] {TRIED}\n"
            "  - [x]   Nested   line\n"
            f"- {TESTS}\n"
            "-----\n"
            "```\n- Not a check, it is code\n```\n"
            "-\n"
            "1. Not a list line either\n"
        )
        read = define(owner, project, body)
        # Two lines with the same words are one item, in the order they were first written, and
        # the words have their checkbox and extra spaces taken off.
        words = [TESTS, TRIED, "Nested line"]
        assert [(item["item"], item["text"]) for item in read["items"]] == [
            (key_of(text), text) for text in words
        ]
        assert [item["ticks"] for item in read["items"]] == [0, 0, 0]
        assert [
            (item["item"], item["text"])
            for item in (await ok(agent, "get_definition_of_done"))["items"]
        ] == [(key_of(text), text) for text in words]
        assert sql_rows(
            project, "SELECT item_key, position, text FROM definition_items ORDER BY position"
        ) == [[key_of(text), str(number), text] for number, text in enumerate(words, 1)]

        shown = await ok(agent, "get_node", node=task["ref"])
        assert [(c["item"], c["text"], c["checked"]) for c in shown["checks"]] == [
            (key_of(text), text, False) for text in words
        ]
        assert (shown["checks_total"], shown["checks_ticked"]) == (3, 0)
        # An epic has none of its own.
        epic = await ok(agent, "get_node", node="epic:1")
        assert (epic["checks"], epic["checks_total"], epic["checks_ticked"]) == ([], 0, 0)

        # The key comes from the words, so rewording the text rebuilds the list and keeps the
        # key of a line that said the same.
        define(owner, project, f"- {DOCS}\n- {TESTS}")
        again = (await ok(agent, "get_definition_of_done"))["items"]
        assert [(item["item"], item["text"]) for item in again] == [
            (key_of(DOCS), DOCS),
            (key_of(TESTS), TESTS),
        ]
        assert sql_rows(project, "SELECT count(*) FROM definition_items") == [["2"]]

        # A text with no list lines has no checks and blocks nothing.
        define(owner, project, "Tests pass, docs are updated, and a person has tried it.")
        assert (await ok(agent, "get_definition_of_done"))["items"] == []
        assert await checks_of(agent, task) == []
        await ok(agent, "claim_node", node=task["ref"])
        done = await ok(agent, "update_node", node=task["ref"], expected_version=1, status="done")
        assert done["status"] == "done"


async def test_an_agent_and_a_person_tick_and_untick_and_every_tick_is_in_the_history() -> None:
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer")
    define(owner, project, f"- {TESTS}\n- {DOCS}")
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        first, second = key_of(TESTS), key_of(DOCS)

        # An agent has to say how it checked.
        refusal = await refused(agent, "set_check", node=task["ref"], item=first)
        assert "Say how you checked" in refusal and "evidence" in refusal
        assert "Say how you checked" in await refused(
            agent, "set_check", node=task["ref"], item=first, evidence="   "
        )
        assert [c["checked"] for c in await checks_of(agent, task)] == [False, False]

        ticked = await ok(
            agent, "set_check", node=task["ref"], item=first, evidence="212 tests pass"
        )
        assert (ticked["node"], ticked["checks_total"], ticked["checks_ticked"]) == (
            task["ref"],
            2,
            1,
        )
        one, two = ticked["checks"]
        assert (one["item"], one["checked"], one["checked_by"], one["checked_by_kind"]) == (
            first,
            True,
            HARNESS_AGENT,
            "agent",
        )
        assert one["evidence"] == "212 tests pass" and one["checked_at"]
        # One that is not ticked has nothing about a tick.
        assert (two["item"], two["checked"]) == (second, False)
        assert [two[name] for name in ("checked_by", "checked_by_kind", "checked_at")] == [None] * 3
        assert two["evidence"] is None

        # Ticking is not a change to the node: its version stays, and the counts follow.
        shown = await ok(agent, "get_node", node=task["ref"])
        assert (shown["version"], shown["checks_total"], shown["checks_ticked"]) == (1, 2, 1)
        assert shown["checks"] == ticked["checks"]
        listed = (await ok(agent, "list_nodes", kind="task"))["items"][0]
        assert (listed["checks_total"], listed["checks_ticked"]) == (2, 1)

        # A check can be named by its exact words too, and a person ticks with no evidence.
        by_words = as_person(owner, project, "set_check", node=task["ref"], item=DOCS)
        assert by_words.status == 200, by_words.body
        person_tick = by_words.body["checks"][1]
        assert (person_tick["checked_by"], person_tick["checked_by_kind"]) == (owner.name, "person")
        assert person_tick["evidence"] is None
        assert (by_words.body["checks_total"], by_words.body["checks_ticked"]) == (2, 2)

        # Ticking what is ticked is refused, unless it says how it was checked in new words.
        same = await refused(
            agent, "set_check", node=task["ref"], item=first, evidence="212 tests pass"
        )
        assert "Nothing to change" in same and "already ticked" in same
        retick = await ok(
            agent, "set_check", node=task["ref"], item=first, evidence="213 tests pass"
        )
        assert retick["checks"][0]["evidence"] == "213 tests pass"

        # Unticking is a click, and may say why. Unticking what is not ticked is refused.
        untick = await ok(
            agent, "set_check", node=task["ref"], item=second, checked=False, evidence="It broke"
        )
        assert [c["checked"] for c in untick["checks"]] == [True, False]
        assert untick["checks"][1]["evidence"] is None
        again = await refused(agent, "set_check", node=task["ref"], item=second, checked=False)
        assert "Nothing to change" in again and "not ticked" in again

        # Everything that was done is in the task's own history and in the change feed, in order,
        # with the item, its words and how it was checked.
        events = [e for e in await history_of(agent, task) if e["action"].startswith("check_")]
        assert [(e["action"], e["actor"], e["actor_kind"]) for e in events] == [
            ("check_ticked", HARNESS_AGENT, "agent"),
            ("check_ticked", owner.name, "person"),
            ("check_ticked", HARNESS_AGENT, "agent"),
            ("check_unticked", HARNESS_AGENT, "agent"),
        ]
        assert [e["node"] for e in events] == [task["ref"]] * 4
        assert [(e["before"], e["after"]) for e in events] == [
            (None, check_says(first, TESTS, "212 tests pass")),
            (None, check_says(second, DOCS, None)),
            (
                check_says(first, TESTS, "212 tests pass"),
                check_says(first, TESTS, "213 tests pass"),
            ),
            (check_says(second, DOCS, None), check_says(second, DOCS, "It broke")),
        ]
        feed = (await ok(agent, "changes_since", node=task["ref"], limit=200))["items"]
        assert [e["id"] for e in feed if e["action"].startswith("check_")] == [
            e["id"] for e in events
        ]

        # Refused in plain words, and nothing changes.
        epic_said = await refused(
            agent, "set_check", node="epic:1", item=first, evidence="Looked at it"
        )
        assert "is an epic" in epic_said and "stories and tasks" in epic_said
        assert "There is no task 99" in await refused(
            agent, "set_check", node="task:99", item=first, evidence="x"
        )
        unknown = await refused(agent, "set_check", node=task["ref"], item="ffffffff", evidence="x")
        assert "There is no check 'ffffffff'" in unknown
        assert f"{first}: 'Tests pass'" in unknown
        leaked = await refused(
            agent, "set_check", node=task["ref"], item=second, evidence=f"Used {A_TOKEN}"
        )
        assert "contains what looks like" in leaked and A_TOKEN not in leaked
        assert [c["checked"] for c in await checks_of(agent, task)] == [True, False]
        assert (
            len([e for e in await history_of(agent, task) if e["action"].startswith("check_")]) == 4
        )

    # A viewer sees the ticks and cannot change them.
    assert as_person(viewer, project, "get_node", node=task["ref"]).body["checks_ticked"] == 1
    not_allowed = as_person(viewer, project, "set_check", node=task["ref"], item=second)
    assert not_allowed.status == 403, not_allowed.body


async def test_changing_the_text_keeps_old_ticks_in_the_history_and_off_the_list() -> None:
    owner = register()
    project = make_project(owner)
    define(owner, project, f"- {TESTS}\n- {DOCS}")
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        for words in (TESTS, DOCS):
            await ok(agent, "set_check", node=task["ref"], item=key_of(words), evidence="Did it")
        assert (await ok(agent, "get_node", node=task["ref"]))["checks_ticked"] == 2
        counted = (await ok(agent, "get_definition_of_done"))["items"]
        assert [(item["text"], item["ticks"]) for item in counted] == [(TESTS, 1), (DOCS, 1)]

        # One line is kept, one is reworded and one is new.
        read = define(owner, project, f"- {TESTS}\n- Docs are all updated\n- {TRIED}")
        assert [(item["text"], item["ticks"]) for item in read["items"]] == [
            (TESTS, 1),
            ("Docs are all updated", 0),
            (TRIED, 0),
        ]
        shown = await ok(agent, "get_node", node=task["ref"])
        assert [(c["text"], c["checked"]) for c in shown["checks"]] == [
            (TESTS, True),
            ("Docs are all updated", False),
            (TRIED, False),
        ]
        assert (shown["checks_total"], shown["checks_ticked"]) == (3, 1)
        # The tick of the line that went is still stored, and still in the history.
        assert sql_rows(project, "SELECT count(*) FROM definition_checks") == [["2"]]
        ticks = [e for e in await history_of(agent, task) if e["action"] == "check_ticked"]
        assert [e["after"]["text"] for e in ticks] == [TESTS, DOCS]

        # Writing the old line back counts the old tick again: the key comes from the words.
        define(owner, project, f"- {TESTS}\n- {DOCS}")
        assert (await ok(agent, "get_node", node=task["ref"]))["checks_ticked"] == 2


async def test_an_agent_cannot_call_work_done_while_a_check_is_unticked_and_a_person_can() -> None:
    owner = register()
    project = make_project(owner)
    define(owner, project, f"- {TESTS}\n- {DOCS}")
    async with connect(project.mcp_url, project.key) as agent:
        epic, story, task = await make_tree(agent)
        await ok(agent, "claim_node", node=task["ref"])
        missing = [TESTS, DOCS]

        said = await refused(agent, "finish_work", node=task["ref"], outcome="done", note="Done.")
        assert "Tick these checks first with set_check, saying how you checked" in said
        for words in missing:
            assert f"'{words}' (item {key_of(words)})" in said
        assert "Or finish it as released or blocked" in said and "Nothing was changed." in said
        # Nothing moved: it is still the agent's, and not done.
        shown = await ok(agent, "get_node", node=task["ref"])
        assert shown["claimed_by"] == HARNESS_AGENT and shown["status"] != "done"

        # Setting the status does the same, from update_node and from update_nodes.
        stuck = await refused(
            agent, "update_node", node=task["ref"], expected_version=shown["version"], status="done"
        )
        assert "Tick these checks first" in stuck and f"'{TESTS}'" in stuck
        many = await ok(
            agent,
            "update_nodes",
            changes=[
                {"node": task["ref"], "expected_version": shown["version"], "status": "done"},
                {
                    "node": task["ref"],
                    "expected_version": shown["version"],
                    "status": "in_progress",
                },
            ],
        )
        assert [r["applied"] for r in many["results"]] == [False, True]
        assert "Tick these checks first" in many["results"][0]["refusal"]
        shown = await ok(agent, "get_node", node=task["ref"])
        assert shown["status"] == "in_progress"

        # Releasing and blocking are not done, so they are never held back; an epic has no checks.
        await ok(agent, "finish_work", node=task["ref"], outcome="released", note="Not today.")
        await ok(agent, "claim_node", node=story["ref"])
        again = await refused(agent, "finish_work", node=story["ref"], outcome="done", note="Done.")
        assert "Tick these checks first" in again and story["ref"] in again
        await ok(agent, "finish_work", node=story["ref"], outcome="blocked", note="Waiting.")
        epic_now = await ok(agent, "get_node", node=epic["ref"])
        done_epic = await ok(
            agent,
            "update_node",
            node=epic["ref"],
            expected_version=epic_now["version"],
            status="done",
        )
        assert done_epic["status"] == "done"

        # Once every check is ticked, done goes through, from finish_work as from update_node.
        await ok(agent, "claim_node", node=task["ref"])
        await ok(
            agent, "set_check", node=task["ref"], item=key_of(TESTS), evidence="212 tests pass"
        )
        half = await refused(agent, "finish_work", node=task["ref"], outcome="done", note="Done.")
        assert f"'{DOCS}'" in half and f"'{TESTS}'" not in half
        await ok(agent, "set_check", node=task["ref"], item=DOCS, evidence="Read it through")
        finished = await ok(agent, "finish_work", node=task["ref"], outcome="done", note="Done.")
        assert finished["node"]["status"] == "done"
        assert (finished["node"]["checks_total"], finished["node"]["checks_ticked"]) == (2, 2)

        # A person decides: they are never refused, ticked or not.
        current = await ok(agent, "get_node", node=story["ref"])
        by_hand = as_person(
            owner,
            project,
            "update_node",
            node=story["ref"],
            expected_version=current["version"],
            status="done",
        )
        assert by_hand.status == 200 and by_hand.body["status"] == "done", by_hand.body
        assert (by_hand.body["checks_total"], by_hand.body["checks_ticked"]) == (2, 0)


async def test_the_briefing_carries_the_checks_of_the_item_an_agent_is_given() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        before = (await ok(agent, "next_work", peek=True))["context"]
        assert before["definition_checks"] == [] and before["definition_of_done"] == ""

        define(owner, project, f"The rules.\n- {TESTS}\n- {DOCS}")
        context = (await ok(agent, "next_work", peek=True))["context"]
        assert context["definition_checks"] == [
            {
                "item": key_of(TESTS),
                "text": TESTS,
                "person_only": False,
                "verifier_only": False,
                "github": False,
                "checked": False,
            },
            {
                "item": key_of(DOCS),
                "text": DOCS,
                "person_only": False,
                "verifier_only": False,
                "github": False,
                "checked": False,
            },
        ]
        # The text is still there for the lines that are not checks.
        assert context["definition_of_done"].startswith("The rules.")

        await ok(agent, "set_check", node=task["ref"], item=key_of(DOCS), evidence="Read it")
        ticked = (await ok(agent, "next_work", peek=True))["context"]
        assert [(c["text"], c["checked"]) for c in ticked["definition_checks"]] == [
            (TESTS, False),
            (DOCS, True),
        ]
        # Taking the work gives the same list for the item that was claimed.
        taken = await ok(agent, "next_work")
        assert taken["work"]["ref"] == task["ref"]
        assert taken["context"]["definition_checks"] == ticked["definition_checks"]


async def test_a_standard_key_can_tick_checks_and_is_not_offered_the_coverage_report() -> None:
    owner = register()
    project = make_project(owner)
    define(owner, project, f"- {TESTS}")
    standard = make_limited_key(project, "standard-agent", None)
    async with connect(project.mcp_url, project.key) as maker:
        task = await a_task(maker)
    async with connect(project.mcp_url, standard) as agent:
        offered = {tool.name for tool in (await agent.list_tools()).tools}
        assert "set_check" in offered and "coverage_report" not in offered
        assert len(offered) <= 25
        ticked = await ok(
            agent, "set_check", node=task["ref"], item=key_of(TESTS), evidence="Ran it"
        )
        assert ticked["checks"][0]["checked_by"] == "standard-agent"
        said = await refused(agent, "coverage_report")
        assert "not one of the tools Vrum offers agents" in said


async def test_the_export_carries_every_tick_and_a_new_project_gives_back_the_same_file() -> None:
    source = make_project(register())
    async with connect(source.mcp_url, source.key) as client:
        world = await rich_world(client, source)
        file = await export_everything(client)
        # In pages of one tick the same file comes out: the cursor names the node and the item.
        assert await export_everything(client, limit=1) == file
        assert file["counts"]["checks"] == len(file["checks"]) == 3
        assert set(file["checks"][0]) == {
            "node",
            "item",
            "text",
            "checked_by",
            "checked_by_kind",
            "checked_at",
            "evidence",
        }
        # By node and item; an agent's ticks with how they were checked, a person's with or
        # without, and the one that was unticked is not there (the history has it).
        assert [(c["node"], c["item"]) for c in file["checks"]] == sorted(
            (c["node"], c["item"]) for c in file["checks"]
        )
        ticks = {(c["node"], c["text"]): c for c in file["checks"]}
        assert ticks[(world["task1"]["ref"], TESTS)]["checked_by_kind"] == "agent"
        assert ticks[(world["task1"]["ref"], TESTS)]["evidence"] == "212 tests pass"
        assert ticks[(world["task1"]["ref"], DOCS)]["evidence"] == "Read it through"
        assert ticks[(world["story1"]["ref"], TESTS)]["checked_by_kind"] == "person"
        assert ticks[(world["story1"]["ref"], TESTS)]["evidence"] is None
        assert not [key for key in ticks if key[1].startswith("A person can try it")]
        actions = [e["action"] for e in file["events"] if e["action"].startswith("check_")]
        assert actions == [
            "check_ticked",
            "check_ticked",
            "check_unticked",
            "check_ticked",
            "check_ticked",
        ]
        # Sizes travel with the nodes.
        sizes = {node["number"]: node["size"] for node in file["nodes"] if node["kind"] == "task"}
        assert sorted(size for size in sizes.values() if size) == ["m", "s"]

    target = make_project(register())
    async with connect(target.mcp_url, target.key) as client:
        result = await ok(client, "import_graph", document=file)
        assert result["counts"]["checks"] == 3
        assert await export_everything(client) == file
        shown = await ok(client, "get_node", node=world["task1"]["ref"])
        assert (shown["size"], shown["checks_total"], shown["checks_ticked"]) == ("s", 3, 2)
        assert [(c["text"], c["checked"]) for c in shown["checks"]][:2] == [
            (TESTS, True),
            (shown["checks"][1]["text"], False),
        ]
        # The words the checklist is made from came with the definition of done.
        assert len((await ok(client, "get_definition_of_done"))["items"]) == 3
        # An import into a project that holds ticks is refused like any other.
        assert "already holds data" in await refused(client, "import_graph", document=file)

    # A tick of a line that was taken out of the text is in the file too, and loads again.
    owner = source.owner
    define(owner, source, f"- {TESTS}")
    async with connect(source.mcp_url, source.key) as client:
        trimmed = await export_everything(client)
        assert len(trimmed["checks"]) == 3
        assert (await ok(client, "get_node", node=world["task1"]["ref"]))["checks_total"] == 1
    again = make_project(register())
    async with connect(again.mcp_url, again.key) as client:
        await ok(client, "import_graph", document=trimmed)
        assert await export_everything(client) == trimmed
        shown = await ok(client, "get_node", node=world["task1"]["ref"])
        assert (shown["checks_total"], shown["checks_ticked"]) == (1, 1)


PHONE = "Looks right on a phone"


async def test_an_agent_can_neither_tick_nor_untick_a_check_only_a_person_can_judge() -> None:
    owner = register()
    project = make_project(owner)
    read = define(owner, project, f"- {TESTS}\n- [person] {PHONE}\n- [ ] {DOCS}")
    # The tag is not part of the words, and it is kept apart from them.
    assert [(i["item"], i["text"], i["person_only"]) for i in read["items"]] == [
        (key_of(TESTS), TESTS, False),
        (key_of(PHONE), PHONE, True),
        (key_of(DOCS), DOCS, False),
    ]
    assert sql_rows(
        project, "SELECT text, person_only FROM definition_items ORDER BY position"
    ) == [
        [TESTS, "f"],
        [PHONE, "t"],
        [DOCS, "f"],
    ]
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        assert [(c["text"], c["person_only"]) for c in await checks_of(agent, task)] == [
            (TESTS, False),
            (PHONE, True),
            (DOCS, False),
        ]
        context = (await ok(agent, "next_work", peek=True))["context"]
        assert [(c["text"], c["person_only"]) for c in context["definition_checks"]] == [
            (TESTS, False),
            (PHONE, True),
            (DOCS, False),
        ]
        assert "person_only" in (await ok(agent, "get_definition_of_done"))["items"][1]

        # An agent is refused by key, by the exact words and by the words with the tag, with or
        # without evidence, and nothing changes.
        for item in (key_of(PHONE), PHONE, f"[person] {PHONE}"):
            said = await refused(agent, "set_check", node=task["ref"], item=item, evidence="Saw it")
            assert words_of(said) == (
                f"{PHONE!r} is a check only a person can judge (the definition of done marks it "
                "[person]). Leave it for a person to tick on the board, and say in your handoff "
                "note what they should look at. Nothing was changed."
            )
        await ok(agent, "set_check", node=task["ref"], item=key_of(TESTS), evidence="Ran them")
        assert [c["checked"] for c in await checks_of(agent, task)] == [True, False, False]
        assert not sql_rows(
            project, f"SELECT 1 FROM definition_checks WHERE item_key = '{key_of(PHONE)}'"
        )

        # A person is never refused, ticks with or without words, and unticks.
        ticked = as_person(owner, project, "set_check", node=task["ref"], item=key_of(PHONE))
        assert ticked.status == 200, ticked.body
        assert [(c["checked"], c["checked_by_kind"]) for c in ticked.body["checks"]] == [
            (True, "agent"),
            (True, "person"),
            (False, None),
        ]
        # ... and an agent cannot take that tick back.
        said = await refused(agent, "set_check", node=task["ref"], item=PHONE, checked=False)
        assert "only a person can judge" in said and "to untick on the board" in said
        assert [c["checked"] for c in await checks_of(agent, task)] == [True, True, False]
        untick = as_person(owner, project, "set_check", node=task["ref"], item=PHONE, checked=False)
        assert untick.status == 200, untick.body
        assert [c["checked"] for c in untick.body["checks"]] == [True, False, False]
        events = [e for e in await history_of(agent, task) if e["action"].startswith("check_")]
        assert [(e["action"], e["actor_kind"]) for e in events] == [
            ("check_ticked", "agent"),
            ("check_ticked", "person"),
            ("check_unticked", "person"),
        ]

        # Adding or taking away the tag keeps the key and every tick; only who may tick changes.
        as_person(owner, project, "set_check", node=task["ref"], item=PHONE)
        define(owner, project, f"- {TESTS}\n- {PHONE}\n- {DOCS}")
        loose = await checks_of(agent, task)
        assert [(c["checked"], c["person_only"]) for c in loose] == [
            (True, False),
            (True, False),
            (False, False),
        ]
        await ok(agent, "set_check", node=task["ref"], item=PHONE, checked=False, evidence="Redo")
        define(owner, project, f"- {TESTS}\n- [PERSON] {PHONE}\n- {DOCS}\n- {PHONE}")
        again = await checks_of(agent, task)
        assert [(c["item"], c["checked"], c["person_only"]) for c in again] == [
            (key_of(TESTS), True, False),
            (key_of(PHONE), False, True),
            (key_of(DOCS), False, False),
        ]


async def test_done_is_refused_naming_what_an_agent_can_tick_and_what_only_a_person_can() -> None:
    owner = register()
    project = make_project(owner)
    define(owner, project, f"- {TESTS}\n- [person] {PHONE}\n- {DOCS}")
    async with connect(project.mcp_url, project.key) as agent:
        _, story, task = await make_tree(agent)
        await ok(agent, "claim_node", node=task["ref"])

        said = await refused(agent, "finish_work", node=task["ref"], outcome="done", note="Done.")
        assert words_of(said).startswith(
            f"{task['ref']} cannot be done yet: its definition of done has"
        )
        assert (
            f"Tick these checks first with set_check, saying how you checked: "
            f"'{TESTS}' (item {key_of(TESTS)}); '{DOCS}' (item {key_of(DOCS)}). "
        ) in said
        assert (
            f"Only a person can judge this check (the definition of done marks it [person]), so "
            f"you cannot tick it: '{PHONE}'. Then finish it as blocked, saying in the handoff "
            "note what the person should look at; a person then judges it on the board and "
            "marks it done. Nothing was changed."
        ) in said
        assert key_of(PHONE) not in said and "Or finish it as released" not in said
        shown = await ok(agent, "get_node", node=task["ref"])
        assert shown["claimed_by"] == HARNESS_AGENT and shown["status"] != "done"

        # From update_node the advice is its own: block it and say why.
        stuck = await refused(
            agent, "update_node", node=task["ref"], expected_version=shown["version"], status="done"
        )
        assert f"'{TESTS}' (item {key_of(TESTS)})" in stuck and f"'{PHONE}'" in stuck
        assert (
            "Then set its status to blocked with update_node, giving why to say what the person "
            "should look at; a person then judges it on the board and marks it done."
        ) in stuck

        # With the rest ticked, only the person's check is named, and nothing is left to tick.
        for words in (TESTS, DOCS):
            await ok(agent, "set_check", node=task["ref"], item=words, evidence="Did it")
        only = await refused(agent, "finish_work", node=task["ref"], outcome="done", note="Done.")
        assert words_of(only) == (
            f"{task['ref']} cannot be done yet: its definition of done has checks that are not "
            f"ticked. Only a person can judge this check (the definition of done marks it "
            f"[person]), so you cannot tick it: '{PHONE}'. Finish it as blocked, saying in the "
            "handoff note what the person should look at; a person then judges it on the board "
            "and marks it done. Nothing was changed."
        )

        # So the agent hands it over blocked, with a note, and keeps nothing.
        handed = await ok(
            agent,
            "finish_work",
            node=task["ref"],
            outcome="blocked",
            note="Everything is built. Please look at the layout on a phone.",
        )
        assert handed["node"]["status"] == "blocked" and handed["node"]["claimed_by"] is None
        assert handed["handoff"]["outcome"] == "blocked"
        assert "look at the layout on a phone" in handed["handoff"]["note"]

        # A person judges it, ticks it and marks it done. An agent that is handed work whose only
        # open check was ticked by a person finishes it as done without any refusal.
        current = await ok(agent, "get_node", node=task["ref"])
        assert as_person(owner, project, "set_check", node=task["ref"], item=PHONE).status == 200
        done = as_person(
            owner,
            project,
            "update_node",
            node=task["ref"],
            expected_version=current["version"],
            status="done",
        )
        assert done.status == 200 and done.body["status"] == "done", done.body
        assert (done.body["checks_total"], done.body["checks_ticked"]) == (3, 3)

        await ok(agent, "claim_node", node=story["ref"])
        for words in (TESTS, DOCS):
            await ok(agent, "set_check", node=story["ref"], item=words, evidence="Did it")
        assert as_person(owner, project, "set_check", node=story["ref"], item=PHONE).status == 200
        finished = await ok(agent, "finish_work", node=story["ref"], outcome="done", note="Done.")
        assert finished["node"]["status"] == "done"


async def test_the_tag_travels_with_the_definition_of_done_through_an_export_and_import() -> None:
    source = make_project(register())
    define(source.owner, source, f"- {TESTS}\n- [person] {PHONE}")
    async with connect(source.mcp_url, source.key) as client:
        file = await export_everything(client)
    assert file["definition_of_done"][0]["body"] == f"- {TESTS}\n- [person] {PHONE}"
    target = make_project(register())
    async with connect(target.mcp_url, target.key) as client:
        await ok(client, "import_graph", document=file)
        items = (await ok(client, "get_definition_of_done"))["items"]
        assert [(i["text"], i["person_only"]) for i in items] == [(TESTS, False), (PHONE, True)]
    assert sql_rows(target, "SELECT person_only FROM definition_items ORDER BY position") == [
        ["f"],
        ["t"],
    ]
