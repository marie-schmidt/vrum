"""The walls between projects (M15), proved through real MCP clients and the database itself.

Three projects are made, two by one account and one by another, each filled with one of everything
the graph holds (the same helper makes all of them, so their ids are the same numbers) and the
third given more, so that some of its ids exist nowhere else. Then: every tool shows only its own
project; an id that exists only elsewhere is refused and nothing there is touched; arguments that
claim to be someone else grant nothing; search by words and by meaning, similar items, overlaps and
recall stay inside; claims, feeds, counts and the index are each project's own; the database itself
refuses a reference from one project to another, even to its owner, and a project's role cannot
read or write another's rows or any account; and one project's export loads into another."""

import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    all_nodes,
    confirm_on_board,
    connect,
    connect_as,
    export_everything,
    make_decision,
    make_fact,
    ok,
    refused,
    reindexed,
    rich_world,
    run_sql,
    unique_word,
)
from tests.world import HARNESS_AGENT, Project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@dataclass
class Side:
    """One project, a client that holds its key, and what was made in it."""

    project: Project
    client: Client
    world: dict[str, dict[str, Any]]

    @property
    def word(self) -> str:
        """The made-up word that is in every title this side's world made (and nowhere else)."""
        return str(self.world["epic"]["title"]).split()[-1]


@dataclass
class OnlyThere:
    """What the third project has that no other project has: the extras it was given. Epics,
    stories and tasks are named by reference (such as task:13); the other kinds by id (a decision
    and a plain note are both notes, so they count their ids together)."""

    epic: str
    story: str
    node: str
    rule: int
    decision: int
    fact: int


def nowhere(ref: str) -> str:
    """What a tool says of an epic, story or task that is not in the project."""
    kind, _, number = ref.partition(":")
    return f"There is no {kind} {number} in this project"


def on_the_board(project: Project, tool: str, **arguments: Any) -> tuple[int, Any]:
    """What the board answers when its person (the project's owner) presses a button that calls
    this tool: the status and the body, whether it was done or refused."""
    reply = project.owner.call("POST", f"/board/api/projects/{project.id}/tool/{tool}", arguments)
    return reply.status, reply.body


async def with_more(client: Client) -> OnlyThere:
    epic = await ok(client, "create_node", actor="ann", kind="epic", title="Only here epic")
    story = await ok(
        client,
        "create_node",
        actor="ann",
        kind="story",
        title="Only here story",
        parent=epic["ref"],
    )
    task = story
    for number in range(10):
        task = await ok(
            client,
            "create_node",
            actor="ann",
            kind="task",
            title=f"Only here task {number}",
            parent=story["ref"],
        )
    rule = await ok(client, "create_rule", actor="ann", title="Only here rule")
    for number in range(3):
        rule = await ok(client, "create_rule", actor="ann", title=f"Only here rule {number}")
    decision = await make_decision(client, "Only here decision")
    for number in range(3):
        decision = await make_decision(client, f"Only here decision {number}")
    note = await make_fact(client, "Only here note")
    for number in range(12):
        note = await make_fact(client, f"Only here note {number}")
    return OnlyThere(
        epic=epic["ref"],
        story=story["ref"],
        node=task["ref"],
        rule=rule["id"],
        decision=decision["id"],
        fact=note["id"],
    )


@asynccontextmanager
async def three_projects() -> AsyncIterator[tuple[Side, Side, Side, OnlyThere]]:
    """Two projects of one account and one of another, each with a world of its own."""
    first, second = register(), register()
    made = [
        make_project(first, "One"),
        make_project(first, "Two"),
        make_project(second, "Three"),
    ]
    async with AsyncExitStack() as stack:
        sides = []
        for project in made:
            client = await stack.enter_async_context(connect(project.mcp_url, project.key))
            sides.append(Side(project, client, await rich_world(client, project)))
        only_there = await with_more(sides[2].client)
        yield sides[0], sides[1], sides[2], only_there


def owns(side: Side, others: list[Side], found: Any) -> None:
    """What was found names this side's word, and no other side's."""
    text = json.dumps(found)
    assert side.word in text
    for other in others:
        assert other.word not in text, f"{other.word} (another project) was in what was found"


# -- every tool shows only its own project -------------------------------------------------------


async def test_each_project_holds_only_its_own_records_though_their_references_are_the_same() -> (
    None
):
    async with three_projects() as (a1, a2, b1, _):
        sides = [a1, a2, b1]
        for side in sides:
            others = [other for other in sides if other is not side]
            nodes = await all_nodes(side.client)
            assert nodes and all(
                side.word in str(node["title"]) or "Only here" in node["title"] for node in nodes
            )
            # Each project counts each kind from 1 on its own.
            assert {"epic:1", "story:1", "story:2", "task:1", "task:2", "task:3"} <= {
                node["ref"] for node in nodes
            }
            for tool in ("list_rules", "list_notes"):
                found = await ok(side.client, tool, limit=200)
                owns(side, others, found["items"])
            owns(side, others, await ok(side.client, "changes_since", after_event_id=0, limit=200))
            owns(side, others, await ok(side.client, "coverage_report", limit=200))
            exported = await export_everything(side.client)
            owns(side, others, exported)
            # Each project has a definition of done of its own, and the same version number in
            # two projects is two different texts.
            code, definition = on_the_board(side.project, "get_definition_of_done")
            assert code == 200 and side.word in definition["body"]
            assert [d["body"] for d in exported["definition_of_done"]] == [definition["body"]]
            status = await ok(side.client, "server_status")
            assert {name: len(exported[name]) for name in exported["counts"]} == exported["counts"]
            counts = status["counts"]
            assert counts["events"] == exported["counts"]["events"]
            assert counts["links"] == exported["counts"]["links"]
            assert counts["rules"] == exported["counts"]["rules"]
            assert counts["notes"] == exported["counts"]["notes"]
            assert counts["epics"] + counts["stories"] + counts["tasks"] == len(exported["nodes"])
        # The same reference names a different record in each project.
        for ref in ("epic:1", "story:1", "task:1"):
            titles = {
                side.project.id: (await ok(side.client, "get_node", node=ref))["title"]
                for side in sides
            }
            assert len(set(titles.values())) == 3, ref
        # The third has more than the others, and the others know nothing of those.
        assert len(await all_nodes(b1.client)) > len(await all_nodes(a1.client))


async def test_a_project_wide_decision_applies_throughout_its_project_and_no_other() -> None:
    async with three_projects() as (a1, a2, b1, _):
        sides = [a1, a2, b1]
        for side in sides:
            others = [other for other in sides if other is not side]
            wide = side.world["wide"]
            # A task that no note names: the project-wide decision of the project still applies
            # to it, in its recall.
            recalled = await ok(side.client, "recall", node=side.world["task3"]["ref"])
            assert [item["note"]["id"] for item in recalled["project_wide_notes"]["items"]] == [
                wide["id"]
            ]
            owns(side, others, recalled)
            # The same in the briefing of work: a free task of its own, looked at and not taken.
            epic = await ok(
                side.client, "create_node", actor="ann", kind="epic", title=f"Free {side.word}"
            )
            await ok(
                side.client,
                "create_node",
                actor="ann",
                kind="story",
                title=f"A free story {side.word}",
                parent=epic["ref"],
            )
            seen = await ok(side.client, "next_work", within_node=epic["ref"], peek=True)
            assert seen["found"] and seen["peeked"]
            briefing = seen["context"]["project_wide_notes"]
            assert [item["id"] for item in briefing] == [wide["id"]]
            assert briefing[0]["status"] == "confirmed"
            owns(side, others, seen["context"])
            # It is listed, found and exported by its own project only.
            listed = await ok(
                side.client, "list_notes", project_wide=True, status="in_force", limit=200
            )
            assert [item["id"] for item in listed["items"]] == [wide["id"]]
            found = await ok(
                side.client, "search", query="keep it simple", mode="words", types=["note"]
            )
            assert [(item["id"], item["status"]) for item in found["items"]] == [
                (wide["id"], "confirmed")
            ]
            exported = await export_everything(side.client)
            assert [n["id"] for n in exported["notes"] if n["project_wide"]] == [wide["id"]]
            owns(side, others, exported["notes"])


# -- a reference or an id that exists only elsewhere


async def test_a_guessed_reference_or_id_from_another_project_is_refused_and_nothing_moves() -> (
    None
):
    async with three_projects() as (a1, a2, b1, there):
        before_a1 = await export_everything(a1.client)
        before_b1 = await export_everything(b1.client)
        mine = a1.world
        guesses: list[tuple[str, dict[str, Any], str]] = [
            ("get_node", {"node": there.node}, nowhere(there.node)),
            ("node_history", {"node": there.node}, nowhere(there.node)),
            (
                "update_node",
                {"actor": "x", "node": there.node, "expected_version": 1, "title": "hijacked"},
                nowhere(there.node),
            ),
            ("claim_node", {"actor": "x", "node": there.node}, nowhere(there.node)),
            (
                "create_node",
                {"actor": "x", "kind": "story", "title": "orphan", "parent": there.epic},
                nowhere(there.epic),
            ),
            (
                "add_link",
                {
                    "actor": "x",
                    "source": mine["story1"]["ref"],
                    "target": there.node,
                    "link_type": "relates_to",
                },
                nowhere(there.node),
            ),
            ("get_rule", {"rule_id": there.rule}, f"no acceptance criterion with id {there.rule}"),
            (
                "update_rule",
                {"actor": "x", "rule_id": there.rule, "expected_version": 1, "title": "hijacked"},
                f"no acceptance criterion with id {there.rule}",
            ),
            (
                "create_rule",
                {"actor": "x", "title": "in a foreign story", "node": there.story},
                nowhere(there.story),
            ),
            ("get_note", {"note_id": there.decision}, f"no note with id {there.decision}"),
            ("get_note", {"note_id": there.fact}, f"no note with id {there.fact}"),
            ("note_history", {"note_id": there.fact}, f"no note with id {there.fact}"),
            (
                "list_notes",
                {"note_id": there.decision},
                f"no note with id {there.decision}",
            ),
            (
                "supersede_note",
                {
                    "replaces": [there.decision],
                    "text": "t",
                    "why": "r",
                    "decided_by": "x",
                },
                f"no note with id {there.decision}",
            ),
            (
                "add_note",
                {
                    "text": "t",
                    "why": "r",
                    "decided_by": "x",
                    "nodes": [there.node],
                },
                nowhere(there.node),
            ),
            (
                "withdraw_note",
                {"note_ids": [there.fact], "reason": "r"},
                f"no note with id {there.fact}",
            ),
            (
                "supersede_note",
                {"replaces": [there.fact], "text": "t", "why": "s"},
                f"no note with id {there.fact}",
            ),
            (
                "link_note",
                {"note_id": mine["fact2"]["id"], "nodes": [there.node]},
                nowhere(there.node),
            ),
            (
                "link_note",
                {"note_id": mine["wide"]["id"], "nodes": [there.node]},
                nowhere(there.node),
            ),
            (
                "link_note",
                {"note_id": mine["wide"]["id"], "note_ids": [there.fact]},
                f"no note with id {there.fact}",
            ),
            (
                "add_note",
                {"text": "t", "why": "s", "note_ids": [there.decision]},
                f"no note with id {there.decision}",
            ),
            (
                "add_note",
                {"text": "t", "why": "s", "rule_ids": [there.rule]},
                f"no acceptance criterion with id {there.rule}",
            ),
            (
                "finish_work",
                {"actor": "x", "node": there.node, "outcome": "done", "note": "n"},
                nowhere(there.node),
            ),
        ]
        for tool, arguments, words in guesses:
            said = await refused(a1.client, tool, **arguments)
            assert words in said, (tool, said)
        # A person on the board, who is in the first project and in no other, is refused in the
        # same words when asking to confirm what only the third project has (an agent key cannot
        # ask: it may not use this tool at all).
        for confirmation, extra in (("confirmed", {}), ("unconfirmed", {"quote": "Not so."})):
            for note_id in (there.fact, there.decision):
                status, body = on_the_board(
                    a1.project,
                    "set_confirmation",
                    note_id=note_id,
                    confirmation=confirmation,
                    person="Ada",
                    **extra,
                )
                assert status == 400 and f"no note with id {note_id}" in body["message"]
        # The sibling project of the same account is no different.
        assert nowhere(there.node) in await refused(a2.client, "get_node", node=there.node)
        # Nothing was created or changed, in either project.
        assert await export_everything(a1.client) == before_a1
        assert await export_everything(b1.client) == before_b1
        assert (await ok(b1.client, "get_node", node=there.node))["version"] == 1


async def test_arguments_and_names_that_claim_to_be_someone_else_grant_nothing() -> None:
    async with three_projects() as (a1, a2, b1, _):
        before = await export_everything(b1.client)
        forged = {
            "project_id": b1.project.id,
            "account_id": 1,
            "owner": "the owner of the third project",
            "project": b1.project.name,
        }
        listed = await a1.client.call_tool("list_nodes", {"limit": 200, **forged})
        if not listed.is_error:
            owns(a1, [a2, b1], listed.structured_content)
        made = await a1.client.call_tool(
            "create_node",
            {
                "actor": "the owner of the third project",
                "kind": "epic",
                "title": "Forged",
                **forged,
            },
        )
        # Whether the extra arguments were refused or ignored, the epic can only be in the project
        # the key belongs to.
        titles = [node["title"] for node in await all_nodes(a1.client)]
        assert ("Forged" in titles) == (not made.is_error)
        assert await export_everything(b1.client) == before
        assert "Forged" not in json.dumps(await all_nodes(b1.client))
        # A name given as the actor is ignored, not even attribution: who it was is the agent the
        # key belongs to, and it opens nothing.
        node = (
            next(n for n in await all_nodes(a1.client) if n["title"] == "Forged")
            if not made.is_error
            else None
        )
        if node is not None:
            history = await ok(a1.client, "node_history", node=node["ref"])
            assert history["items"][0]["actor"] == HARNESS_AGENT
        assert "There is no task 10000 in this project" in await refused(
            b1.client, "get_node", node="task:10000"
        )


# -- search, similar items, overlaps and recall


async def test_search_by_words_and_by_meaning_similar_items_overlaps_and_recall_stay_inside() -> (
    None
):
    async with three_projects() as (a1, a2, b1, _):
        sides = [a1, a2, b1]
        reports = [await reindexed(side.client) for side in sides]
        for side, report in zip(sides, reports, strict=True):
            others = [other for other in sides if other is not side]
            # Exact words: a word that is only in another project finds nothing here.
            for other in others:
                nothing = await ok(side.client, "search", query=other.word, mode="words")
                assert nothing["total"] == 0 and nothing["items"] == []
            mine = await ok(side.client, "search", query=side.word, mode="words", limit=200)
            assert mine["total"] > 0
            owns(side, others, mine["items"])
            # Meaning: the same question is put to every project, and each answers from its own.
            by_meaning = await ok(
                side.client, "search", query="logging in with an email", mode="meaning", limit=200
            )
            assert by_meaning["total"] > 0
            owns(side, others, by_meaning["items"])
            similar = await ok(side.client, "similar_items", text="log in with email", limit=200)
            assert similar["items"]
            owns(side, others, similar["items"])
            overlaps = await ok(side.client, "find_overlaps", min_similarity=0.0)
            for other in others:
                assert other.word not in json.dumps(overlaps)
            task_overlaps = await ok(
                side.client, "find_overlaps", item_type="task", min_similarity=0.0
            )
            for other in others:
                assert other.word not in json.dumps(task_overlaps)
            owns(side, others, await ok(side.client, "recall", node=side.world["story1"]["ref"]))
            # The index of a project holds exactly that project's own embeddable items.
            counts = (await ok(side.client, "server_status"))["counts"]
            embeddable = sum(
                counts[name] for name in ("epics", "stories", "tasks", "rules", "notes")
            )
            assert report["indexed_total"] == embeddable
            assert (await ok(side.client, "server_status"))["index"]["indexed"] == embeddable


# -- claims, handoffs, feeds


async def test_claims_who_is_working_ready_work_and_the_change_feed_are_each_projects_own() -> None:
    async with three_projects() as (a1, a2, b1, _):
        sides = [a1, a2, b1]
        for side in sides:
            others = [other for other in sides if other is not side]
            working = await ok(side.client, "who_is_working")
            owns(side, others, working)
            assert HARNESS_AGENT in json.dumps(working)
            owns(side, others, await ok(side.client, "ready_work", limit=200))
        # An agent takes work in the first project: only that project shows it.
        free = await ok(
            a1.client,
            "create_node",
            actor="ann",
            kind="story",
            title=f"A free story {a1.word}",
            parent=a1.world["epic"]["ref"],
        )
        zed = f"agent-zed-{unique_word()}"
        async with connect_as(zed, a1.project) as agent:
            taken = await ok(agent, "next_work")
            assert taken["work"]["ref"] == free["ref"] and taken["claim"] is not None
            assert a1.word in json.dumps(taken)
            for side in (a2, b1):
                assert zed not in json.dumps(await ok(side.client, "who_is_working"))
                assert zed not in json.dumps(
                    await ok(side.client, "changes_since", after_event_id=0, limit=200)
                )
            assert zed in json.dumps(await ok(a1.client, "who_is_working"))
            await ok(
                agent,
                "finish_work",
                node=taken["work"]["ref"],
                outcome="released",
                note="Nothing done.",
            )


# -- the database itself


def sql_refused(sql: str, *, project: int | None = None, as_project: bool = False) -> str:
    """Run SQL that the database must refuse, and give what it said."""
    result = run_sql(sql, project=project, as_project=as_project)
    assert result.returncode != 0, f"The database accepted: {sql}"
    return str(result.stderr)


async def test_the_database_refuses_a_reference_from_one_project_to_another_even_to_its_owner() -> (
    None
):
    async with three_projects() as (a1, a2, b1, there):
        before_a1 = await export_everything(a1.client)
        before_b1 = await export_everything(b1.client)
        one, three = a1.project.id, b1.project.id
        mine = a1.world
        # The owner of the database, who is not held back by the row filter, still cannot join
        # what is not joined: the project is part of every key.
        assert "node_parent_exists" in sql_refused(
            "INSERT INTO nodes (project_id, kind, number, parent, title) "
            f"VALUES ({one}, 'story', 9001, '{there.epic}', 'x')"
        )
        assert "link_ends_at_a_node" in sql_refused(
            "INSERT INTO links (project_id, source, target, link_type) "
            f"VALUES ({one}, '{mine['story1']['ref']}', '{there.node}', 'relates_to')",
            project=one,
        )
        assert "claim_is_on_a_real_node" in sql_refused(
            "INSERT INTO claims (project_id, node, actor, actor_kind, expires_at) "
            f"VALUES ({one}, '{there.node}', 'x', 'agent', now() + interval '1 hour')"
        )
        assert "violates foreign key constraint" in sql_refused(
            "INSERT INTO note_nodes (project_id, note_id, node) "
            f"VALUES ({one}, {mine['wide']['id']}, '{there.node}')"
        )
        assert "violates foreign key constraint" in sql_refused(
            "INSERT INTO note_rules (project_id, note_id, rule_id) "
            f"VALUES ({one}, {mine['fact2']['id']}, {there.rule})"
        )
        assert "violates foreign key constraint" in sql_refused(
            "INSERT INTO note_notes (project_id, note_id, about_id) "
            f"VALUES ({one}, {mine['fact2']['id']}, {there.decision})"
        )
        assert "violates foreign key constraint" in sql_refused(
            "INSERT INTO events (project_id, id, node, action, actor, actor_kind, after) "
            f"VALUES ({one}, 999999, '{there.node}', 'update', 'x', 'agent', '{{}}')"
        )
        # Replacement cannot cross either, whether the note replaced was decided or not.
        for replaced, replacement in (
            (mine["fact2"]["id"], there.fact),
            (mine["wide"]["id"], there.decision),
        ):
            assert "violates foreign key constraint" in sql_refused(
                f"UPDATE notes SET status = 'superseded', superseded_by_id = {replacement} "
                f"WHERE project_id = {one} AND id = {replaced}"
            )
        assert "rule_belongs_to_a_node" in sql_refused(
            f"UPDATE rules SET node = '{there.story}', version = version + 1 "
            f"WHERE project_id = {one} AND id = {mine['rule2']['id']}"
        )
        # A row cannot be moved to another project.
        assert "cannot move to another project" in sql_refused(
            f"UPDATE nodes SET project_id = {three}, version = version + 1 "
            f"WHERE project_id = {one} AND ref = '{mine['task1']['ref']}'"
        )
        assert await export_everything(a1.client) == before_a1
        assert await export_everything(b1.client) == before_b1


async def test_a_projects_database_role_sees_and_writes_only_its_own_rows_and_no_account() -> None:
    async with three_projects() as (a1, a2, b1, there):
        before_b1 = await export_everything(b1.client)
        one, three = a1.project.id, b1.project.id

        def count(sql: str, project: int | None) -> str:
            result = run_sql(sql, plain=True, project=project, as_project=True)
            assert result.returncode == 0, result.stderr
            return str(result.stdout.strip())

        mine = len(await all_nodes(a1.client))
        assert count("SELECT count(*) FROM nodes", one) == str(mine)
        assert count(f"SELECT count(*) FROM nodes WHERE project_id = {three}", one) == "0"
        # A reference every project has names only this project's own item.
        assert count("SELECT count(*) FROM nodes WHERE ref = 'epic:1'", one) == "1"
        assert count("SELECT count(*) FROM node_state WHERE ref = 'epic:1'", one) == "1"
        # One that only the third project has is not seen, even when asked for by its project.
        for table in ("nodes", "node_state"):
            assert (
                count(
                    f"SELECT count(*) FROM {table} "
                    f"WHERE project_id = {three} AND ref = '{there.node}'",
                    one,
                )
                == "0"
            )
        # With no project named at all, nothing is seen and nothing can be written.
        assert count("SELECT count(*) FROM nodes", None) == "0"
        assert count("SELECT count(*) FROM events", None) == "0"
        assert "No project is named" in sql_refused(
            "INSERT INTO nodes (kind, number, title) VALUES ('epic', next_id('epic'), 'nowhere')",
            as_project=True,
        )
        # Another project's number written into a row is refused by the row filter.
        forged = sql_refused(
            "INSERT INTO nodes (project_id, kind, number, title) "
            f"VALUES ({three}, 'epic', next_id('epic'), 'forged')",
            project=one,
            as_project=True,
        )
        assert "row-level security" in forged
        # A row of another project cannot be changed or deleted: it is not there to be touched.
        changed = run_sql(
            f"UPDATE nodes SET title = 'x', version = version + 1 "
            f"WHERE project_id = {three} AND ref = '{there.node}' RETURNING ref",
            plain=True,
            project=one,
            as_project=True,
        )
        assert changed.returncode == 0 and changed.stdout.strip() == "", changed.stderr
        # Nor can the confirmation of another project's note, decided or not.
        for record in (there.fact, there.decision):
            confirmed = run_sql(
                "UPDATE notes SET confirmed_by = 'Mallory', confirmed_at = now() "
                f"WHERE id = {record} RETURNING id",
                plain=True,
                project=one,
                as_project=True,
            )
            assert confirmed.returncode == 0 and confirmed.stdout.strip() == "", confirmed.stderr
        # No account, session, key or project is readable by it.
        for table in ("accounts", "sessions", "agent_keys", "projects"):
            assert "permission denied" in sql_refused(
                f"SELECT count(*) FROM {table}", project=one, as_project=True
            )
        assert await export_everything(b1.client) == before_b1
        assert (await ok(b1.client, "get_node", node=there.node))["title"] != "x"
        for record in (there.fact, there.decision):
            assert (await ok(b1.client, "get_note", note_id=record))["confirmed_by"] is None


# -- confirmation is each project's own


async def test_confirmation_is_each_projects_own_and_a_foreign_record_cannot_be_confirmed() -> None:
    async with three_projects() as (a1, a2, b1, there):
        before_b1 = await export_everything(b1.client)
        # Every project's world has a plain note and a decision of the same numbers, none
        # confirmed.
        note, decision = a1.world["fact2"], a1.world["decision2"]
        assert note["id"] == a2.world["fact2"]["id"] == b1.world["fact2"]["id"]
        assert decision["id"] == a2.world["decision2"]["id"] == b1.world["decision2"]["id"]

        # Only a person confirms, on the board: the first project's owner does.
        confirmed_note = confirm_on_board("fact", note, "Ada", project=a1.project)
        confirmed_decision = confirm_on_board("decision", decision, "Ada", project=a1.project)

        assert confirmed_note["status"] == confirmed_decision["status"] == "confirmed"
        for side in (a2, b1):
            for record_id in (note["id"], decision["id"]):
                other = await ok(side.client, "get_note", note_id=record_id)
                assert other["status"] == "unconfirmed" and other["confirmed_by"] is None
        assert await export_everything(b1.client) == before_b1
        # A note that exists only in another project is refused, and the other project keeps
        # what it had.
        for record_id in (there.fact, there.decision):
            status, body = on_the_board(
                a1.project,
                "set_confirmation",
                note_id=record_id,
                confirmation="confirmed",
                person="Ada",
            )
            assert status == 400 and f"no note with id {record_id}" in body["message"]
        assert await export_everything(b1.client) == before_b1
        # The history of the one that was confirmed says so, in its own project only.
        history = await ok(a1.client, "note_history", note_id=note["id"])
        assert [item["action"] for item in history["items"]] == ["note_recorded", "note_confirmed"]
        other_history = await ok(a2.client, "note_history", note_id=note["id"])
        assert [item["action"] for item in other_history["items"]] == ["note_recorded"]


# -- one project's export loads into another


async def test_a_projects_export_loads_into_an_empty_project_of_the_same_person_unchanged() -> None:
    async with three_projects() as (a1, a2, b1, _):
        owner = a1.project.owner
        before_a2 = await export_everything(a2.client)
        before_b1 = await export_everything(b1.client)
        document = await export_everything(a1.client)

        fresh = make_project(owner, "Loaded")
        async with connect(fresh.mcp_url, fresh.key) as loaded:
            result = await ok(loaded, "import_graph", actor="marie", document=document)
            assert result["counts"] == document["counts"]
            # Everything is as it was, ids and all: the same export comes back.
            assert await export_everything(loaded) == document
            # A project that holds something refuses a second import, and says why.
            said = await refused(loaded, "import_graph", actor="marie", document=document)
            assert "already holds data" in said and "Nothing was imported" in said
            # New work continues after the highest number of its own kind in the file.
            for kind in ("epic", "story", "task"):
                highest = max(node["number"] for node in document["nodes"] if node["kind"] == kind)
                parent = None
                if kind != "epic":
                    parent_kind = "epic" if kind == "story" else "story"
                    parent = next(
                        f"{parent_kind}:{node['number']}"
                        for node in document["nodes"]
                        if node["kind"] == parent_kind
                    )
                made = await ok(
                    loaded,
                    "create_node",
                    actor="ann",
                    kind=kind,
                    title="After the import",
                    parent=parent,
                )
                assert made["ref"] == f"{kind}:{highest + 1}"
            status = await ok(loaded, "server_status")
            assert (
                status["counts"]["epics"]
                == sum(1 for node in document["nodes"] if node["kind"] == "epic") + 1
            )
        # The project it was exported from and the others are as they were.
        assert await export_everything(a1.client) == document
        assert await export_everything(a2.client) == before_a2
        assert await export_everything(b1.client) == before_b1
        # Loading it into a project that holds something is refused, and changes nothing there.
        said = await refused(a2.client, "import_graph", actor="marie", document=document)
        assert "already holds data" in said
        assert await export_everything(a2.client) == before_a2
        # The file holds the graph and nothing of any account.
        text = json.dumps(document)
        for secret in (owner.email, owner.password, owner.cookie, a1.project.key):
            assert secret not in text
        assert not {"accounts", "sessions", "agent_keys", "password_hash"} & set(document)
