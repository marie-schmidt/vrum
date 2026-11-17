"""M19 against the running containers: an epic, a story and a task are numbered on their own and
named only by their kind and number (`epic:3`, `story:12`, `task:40`). Nothing here is a mock: a
real MCP client, the real database (read directly to prove what no answer shows) and, for the
export, a second, separate copy of Vrum.

- the numbers: each kind counts from 1 in each project, `epic:1`, `story:1` and `task:1` are three
  items, and no integer node id is in any answer or any database column;
- the references: every tool that names a node refuses a bad one, one that names nothing and one of
  the wrong kind, in plain words, with nothing changed;
- the text and the filter: `[[epic:1]]`, `[[story:1]]` and `[[task:1]]` are three mentions, a
  rename is followed, and the filter language finds by kind, epic, story, parent and number;
- moving them: an export loads into another project with every number, and the next item of each
  kind continues after the highest of its own kind; the Markdown copy has one file per item named
  by kind and number."""

import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    REPO_ROOT,
    all_nodes,
    connect,
    export_everything,
    kind_of,
    number_of,
    ok,
    ref_order,
    refused,
    reindexed,
    rich_world,
    run_sql,
    unique_word,
)
from tests.second_stack import SecondStack
from tests.world import Project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

KINDS = ("epic", "story", "task")


@asynccontextmanager
async def fresh_project() -> AsyncIterator[tuple[Project, Client]]:
    """A project of its own (made by a new account, so that nothing else's numbers are in it) and
    a client of it: the numbers of every kind start at 1."""
    project = make_project(register(), f"Numbering {unique_word()}")
    async with connect(project.mcp_url, project.key) as client:
        yield project, client


async def create(client: Client, kind: str, title: str, parent: str | None = None) -> str:
    fields: dict[str, Any] = {"actor": "tester", "kind": kind, "title": title}
    if parent is not None:
        fields["parent"] = parent
    return str((await ok(client, "create_node", **fields))["ref"])


# Three epics, four stories and five tasks, made in an order that mixes the kinds, so that a
# counter shared by two kinds could not look right by accident: (kind, parent) in the order they
# are made, and the reference each must be given.
NUMBERED = [
    ("epic", None, "epic:1"),
    ("story", "epic:1", "story:1"),
    ("task", "story:1", "task:1"),
    ("epic", None, "epic:2"),
    ("story", "epic:2", "story:2"),
    ("task", "story:2", "task:2"),
    ("epic", None, "epic:3"),
    ("story", "epic:3", "story:3"),
    ("task", "story:3", "task:3"),
    ("story", "epic:1", "story:4"),
    ("task", "story:4", "task:4"),
    ("task", "story:1", "task:5"),
]
PARENT = {ref: parent for _, parent, ref in NUMBERED if parent}
EVERYTHING = sorted((ref for _, _, ref in NUMBERED), key=ref_order)


async def numbered_project(client: Client, word: str) -> list[str]:
    """Make the twelve items above, each titled by its place in the order, and give the references
    the server gave them."""
    return [
        await create(client, kind, f"Item {place} {kind} {word}", parent)
        for place, (kind, parent, _) in enumerate(NUMBERED, start=1)
    ]


def sql(query: str, project: Project) -> str:
    """What the database says, as its owner, who sees every project: the query names the project
    it is about."""
    result = run_sql(query, plain=True, project=project.id)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def claims_of(project: Project) -> str:
    """The claims of a project, which a whole export leaves out."""
    return sql(
        "SELECT coalesce(string_agg(node || '=' || actor || '@' || expires_at, ',' "
        f"ORDER BY node), '') FROM claims WHERE project_id = {project.id}",
        project,
    )


async def whole_project(client: Client, project: Project) -> dict[str, Any]:
    """Everything a refused request must not change: the whole export (every node, link, rule,
    decision, fact and event) and the claims."""
    return {"export": await export_everything(client), "claims": claims_of(project)}


async def newest_event(client: Client) -> int:
    """The newest event of the project: a refused request writes none."""
    return int((await ok(client, "changes_since", limit=1, newest=True))["latest_event_id"])


# -- 1. the numbers ------------------------------------------------------------------------------


async def test_each_kind_is_numbered_from_one_on_its_own_and_a_second_project_starts_again() -> (
    None
):
    async with fresh_project() as (first, client):
        word = unique_word()
        assert await numbered_project(client, word) == [ref for _, _, ref in NUMBERED]

        # epic:1, story:1 and task:1 are three different items, each with the number 1.
        three = [await ok(client, "get_node", node=ref) for ref in ("epic:1", "story:1", "task:1")]
        assert [(n["ref"], n["kind"], n["number"]) for n in three] == [
            ("epic:1", "epic", 1),
            ("story:1", "story", 1),
            ("task:1", "task", 1),
        ]
        assert len({n["title"] for n in three}) == 3
        assert [n["parent"] for n in three] == [None, "epic:1", "story:1"]
        for ref, parent in PARENT.items():
            assert (await ok(client, "get_node", node=ref))["parent"] == parent

        # Listed epics first, then stories, then tasks, each by number, and a page continues
        # after the reference of the last item of the one before.
        assert [n["ref"] for n in await all_nodes(client)] == EVERYTHING
        page = await ok(client, "list_nodes", limit=5)
        assert [n["ref"] for n in page["items"]] == EVERYTHING[:5]
        assert page["next_after"] == "story:2" and page["total"] == 12
        then = await ok(client, "list_nodes", limit=5, after=page["next_after"])
        assert [n["ref"] for n in then["items"]] == EVERYTHING[5:10]
        for kind, count in (("epic", 3), ("story", 4), ("task", 5)):
            assert (await ok(client, "list_nodes", kind=kind, limit=1))["total"] == count

        # The database numbers them the same way, with one counter for each kind.
        rows = sql(
            "SELECT kind || ':' || number || ' ' || ref FROM nodes "
            f"WHERE project_id = {first.id} ORDER BY kind_rank, number",
            first,
        )
        assert rows.splitlines() == [f"{ref} {ref}" for ref in EVERYTHING]
        for kind, last in (("epic", 3), ("story", 4), ("task", 5)):
            assert sql(f"SELECT last_value FROM {kind}_ids_{first.id}", first) == str(last)
        assert sql(f"SELECT to_regclass('nodes_ids_{first.id}') IS NULL", first) == "t"

        # A second project of someone else starts again at 1, and cannot see the first one's.
        async with fresh_project() as (second, other):
            assert await create(other, "epic", f"Other epic {word}") == "epic:1"
            assert await create(other, "story", f"Other story {word}", "epic:1") == "story:1"
            assert await create(other, "task", f"Other task {word}", "story:1") == "task:1"
            assert (await ok(other, "get_node", node="story:1"))["title"] == f"Other story {word}"
            assert (await ok(client, "get_node", node="story:1"))["title"] == (
                f"Item 2 story {word}"
            )
            said = await refused(other, "get_node", node="story:4")
            assert "There is no story 4 in this project" in said
            assert sql(f"SELECT last_value FROM story_ids_{second.id}", second) == "1"
        # What the second project did moved none of the first one's counters.
        assert await create(client, "epic", f"Fourth epic {word}") == "epic:4"
        assert await create(client, "story", f"Fifth story {word}", "epic:4") == "story:5"
        assert await create(client, "task", f"Sixth task {word}", "story:5") == "task:6"


async def test_a_plan_numbers_each_kind_on_its_own_and_a_refused_plan_leaves_nothing() -> None:
    async with fresh_project() as (project, client):
        word = unique_word()
        await numbered_project(client, word)
        before = await whole_project(client, project)

        # One problem makes the whole plan fail, however much of it was fine: nothing is created.
        said = await refused(
            client,
            "create_plan",
            actor="tester",
            items=[
                {"key": "e", "kind": "epic", "title": f"Plan epic {word}"},
                {"key": "s", "kind": "story", "title": "Plan story", "parent": "e"},
                {"key": "t", "kind": "task", "title": "Plan task", "parent": "s"},
                {"key": "lost", "kind": "task", "title": "Plan task", "parent": "ghost"},
            ],
            links=[{"source": "t", "target": "task:1", "link_type": "needs_first"}],
            rules=[{"title": "Plan rule", "story": "s"}],
        )
        assert "The plan was not created" in said and "'ghost'" in said
        assert await whole_project(client, project) == before

        # Numbers may be skipped after a failure, but are never reused or shared.
        epic = await create(client, "epic", f"After the failure {word}")
        assert kind_of(epic) == "epic" and number_of(epic) >= 4

        # A good plan is numbered kind by kind, in the order it lists them, and refers to what
        # exists by reference.
        done = await ok(
            client,
            "create_plan",
            actor="tester",
            items=[
                {"key": "e", "kind": "epic", "title": f"Plan epic {word}"},
                {"key": "s1", "kind": "story", "title": "Plan story one", "parent": "e"},
                {"key": "s2", "kind": "story", "title": "Plan story two", "parent": epic},
                {"key": "t1", "kind": "task", "title": "Plan task one", "parent": "s1"},
                {"key": "t2", "kind": "task", "title": "Plan task two", "parent": "story:1"},
            ],
            links=[{"source": "t1", "target": "task:1", "link_type": "needs_first"}],
            rules=[{"title": "Plan rule", "story": "s2"}],
        )
        made = {item["key"]: item["ref"] for item in done["created"]}
        assert [kind_of(made[key]) for key in ("e", "s1", "s2", "t1", "t2")] == [
            "epic", "story", "story", "task", "task",
        ]  # fmt: skip
        assert number_of(made["s2"]) == number_of(made["s1"]) + 1
        assert number_of(made["t2"]) == number_of(made["t1"]) + 1
        assert number_of(made["e"]) > number_of(epic)
        assert number_of(made["s1"]) > 4 and number_of(made["t1"]) > 5
        assert (await ok(client, "get_node", node=made["s2"]))["parent"] == epic
        assert (await ok(client, "get_node", node=made["t2"]))["parent"] == "story:1"
        links = (await ok(client, "list_links", node=made["t1"]))["items"]
        assert [(i["source"], i["target"]) for i in links] == [(made["t1"], "task:1")]


# -- 1. no integer id anywhere -------------------------------------------------------------------

# What no answer may contain any more: a node is named only by its reference.
GONE_NAMES = {
    "parent_id", "node_id", "other_node_id", "source_id", "target_id", "story_id", "new_story_id",
    "node_ids", "within_node_id", "epic_ids", "stories_after_id", "after_node_id",
}  # fmt: skip
# Where a node is named, the name is a reference (or empty, or a list of them), never a number.
REFERENCE_NAMES = {"node", "parent", "other_node", "waiting_on", "schedule_conflicts", "nodes"}
A_REFERENCE = re.compile(r"(epic|story|task):[1-9][0-9]*")


def a_reference_or_none(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return A_REFERENCE.fullmatch(value) is not None
    if isinstance(value, list):
        return all(isinstance(one, str) and A_REFERENCE.fullmatch(one) for one in value)
    return isinstance(value, dict)  # an object that names it in full is walked on its own


def id_problems(tool: str, value: Any, where: str = "") -> list[str]:
    """Everything in an answer that still names a node by a number: a key that was renamed, a node
    (anything that says it is an epic, story or task) that has an `id`, or a place that names a
    node with anything but a reference."""
    problems: list[str] = []
    if isinstance(value, dict):
        says_node = (
            value.get("kind") in KINDS
            or value.get("item_type") in KINDS
            or str(value.get("ref", "")).partition(":")[0] in KINDS
        )
        if says_node and "id" in value:
            problems.append(f"{tool} {where}: a node has an id")
        problems += [f"{tool} {where}: {name}" for name in GONE_NAMES & value.keys()]
        names = set(REFERENCE_NAMES) | {"next_stories_after"}
        if "link_type" in value:  # a link's ends (a fact's `source` is plain words)
            names |= {"source", "target"}
        if tool != "export_graph":  # its pages are continued by `nodes:story:12`
            names.add("next_after")
        problems += [
            f"{tool} {where}: {name} is {value[name]!r}"
            for name in names & value.keys()
            if not a_reference_or_none(value[name])
        ]
        for name, inner in value.items():
            if name != "counts":  # how many of each list there are: `nodes` is a number there
                problems += id_problems(tool, inner, f"{where}.{name}")
    elif isinstance(value, list):
        for position, inner in enumerate(value):
            problems += id_problems(tool, inner, f"{where}[{position}]")
    return problems


def test_the_walk_that_looks_for_integer_ids_would_find_one() -> None:
    """The walk below proves nothing if it cannot see: it is shown the old shapes."""
    node = {"ref": "story:1", "kind": "story", "number": 1, "parent": "epic:1"}
    assert id_problems("t", {"items": [node]}) == []
    assert id_problems("t", {"items": [{**node, "id": 12}]})
    assert id_problems("t", {"items": [{**node, "parent_id": 3}]})
    assert id_problems("t", {"items": [{**node, "parent": 3}]})
    assert id_problems("t", {"node": 12}) and id_problems("t", {"waiting_on": [4]})
    assert id_problems("t", {"item_type": "task", "ref": "task:1", "id": 1})
    assert id_problems("t", {"link_type": "relates_to", "source": 4, "target": "task:1"})
    assert id_problems("t", {"id": 3, "text": "a fact", "source": "The call"}) == []


# Every tool that reads or writes a node, a list of them or something that holds them.
ASKED_FOR_NODES = {
    "create_node", "get_node", "list_nodes", "update_node", "move_node", "ready_work", "next_work",
    "finish_work", "coverage_report", "search", "similar_items", "find_overlaps", "find_mentions",
    "create_plan",
}  # fmt: skip


def nodes_named(value: Any) -> int:
    """How many nodes an answer names in full: each object that has a reference of a node."""
    if isinstance(value, dict):
        here = isinstance(value.get("ref"), str) and A_REFERENCE.fullmatch(value["ref"]) is not None
        return int(here) + sum(nodes_named(inner) for inner in value.values())
    if isinstance(value, list):
        return sum(nodes_named(inner) for inner in value)
    return 0


NODE_TOOLS = {
    "create_node", "get_node", "list_nodes", "update_node", "move_node", "node_history",
    "add_link", "remove_link", "list_links", "ready_work", "claim_node", "renew_claim",
    "release_claim", "who_is_working", "next_work", "finish_work", "create_plan", "update_nodes",
    "changes_since", "coverage_report", "recall", "search", "similar_items", "find_overlaps",
    "find_mentions", "export_graph", "list_rules", "get_rule", "rule_history", "place_rule",
    "move_rule", "list_decisions", "get_decision", "link_decision", "decision_history",
    "list_facts", "get_fact", "link_fact", "fact_history", "create_rule", "record_decision",
    "retain_fact",
}  # fmt: skip
NAMES_A_NODE = (
    "SELECT table_name || '.' || column_name || ' ' || udt_name FROM information_schema.columns "
    "WHERE table_schema = 'public' AND (table_name, column_name) IN (('nodes', 'parent'), "
    "('node_state', 'parent'), ('links', 'source'), ('links', 'target'), ('claims', 'node'), "
    "('rules', 'story'), ('decision_nodes', 'node'), ('fact_nodes', 'node'), ('events', 'node'), "
    "('events', 'other_node'), ('node_state', 'waiting_on'), ('node_state', 'schedule_conflicts'), "
    "('decision_state', 'nodes'), ('fact_state', 'nodes')) ORDER BY 1"
)


async def test_no_answer_and_no_database_column_holds_an_integer_node_id() -> None:
    async with fresh_project() as (project, client):
        answers: list[tuple[str, Any]] = []

        async def ask(tool: str, **arguments: Any) -> dict[str, Any]:
            answer = await ok(client, tool, **arguments)
            answers.append((tool, answer))
            return answer

        world = await rich_world(client)
        epic, story1, story2 = (world[n]["ref"] for n in ("epic", "story1", "story2"))
        task1, task2, task3 = (world[n]["ref"] for n in ("task1", "task2", "task3"))
        rule1, rule2 = world["rule1"]["id"], world["rule2"]["id"]
        decision, fact = world["decision2"]["id"], world["fact1"]["id"]
        word = unique_word()

        # Reading and changing nodes.
        made = await ask(
            "create_node", actor="ann", kind="story", title=f"Fresh {word}", parent=epic
        )
        fresh = made["ref"]
        await ask("get_node", node=fresh)
        await ask("list_nodes", detail=True, limit=3)
        await ask("list_nodes", query=f"epic:{number_of(epic)} kind:task")
        await ask("list_nodes", parent=story1)
        body = f"See [[{task1}]] and [[{story1}]]."
        updated = await ask("update_node", actor="ann", node=fresh, expected_version=1, body=body)
        await ask("move_node", actor="ann", node=task3, expected_version=1, new_parent=story1)
        await ask("node_history", node=task3)
        link = await ask(
            "add_link", actor="ann", source=fresh, target=task1, link_type="relates_to"
        )
        assert (link["source"], link["target"]) == (fresh, task1)
        await ask("list_links", node=fresh)
        await ask("remove_link", actor="ann", source=fresh, target=task1, link_type="relates_to")
        await ask("ready_work")
        bulk = await ask(
            "update_nodes",
            actor="ann",
            changes=[
                {"node": fresh, "expected_version": updated["version"], "priority": "high"},
                {"node": task3, "expected_version": 1, "title": "Stale on purpose"},
            ],
        )
        assert [(r["node"], r["applied"]) for r in bulk["results"]] == [
            (fresh, True),
            (task3, False),
        ]

        # Claims, handoffs and the agent's day.
        claimed = await ask("claim_node", actor="cara", node=fresh)
        assert claimed["node"] == fresh
        await ask("renew_claim", actor="cara", node=fresh)
        working = await ask("who_is_working")
        assert fresh in {h["node"] for a in working["agents"] for h in a["holding"]}
        await ask("release_claim", actor="cara", node=fresh, note="Back in the pool.")
        peeked = await ask("next_work", actor="dev", peek=True)
        taken = await ask("next_work", actor="dev")
        assert peeked["found"] and peeked["work"]["ref"] == taken["work"]["ref"]
        finished = await ask(
            "finish_work", actor="dev", node=taken["work"]["ref"], outcome="done", note="Done."
        )
        assert finished["node"]["ref"] == taken["work"]["ref"]

        # A plan, the change feed, the rules and their coverage.
        planned = await ask(
            "create_plan",
            actor="ann",
            items=[
                {"key": "e", "kind": "epic", "title": f"Planned epic {word}"},
                {"key": "s", "kind": "story", "title": f"Planned story {word}", "parent": "e"},
                {"key": "t", "kind": "task", "title": f"Planned task {word}", "parent": "s"},
            ],
            links=[{"source": "t", "target": task1, "link_type": "needs_first"}],
            rules=[{"title": f"Planned rule {word}", "story": "s"}],
        )
        assert all(re.fullmatch(r"(epic|story|task):\d+", i["ref"]) for i in planned["created"])
        await ask("changes_since", limit=200)
        newest = await ask("changes_since", newest=True, limit=20)
        assert all(e["node"] is None or A_REFERENCE.fullmatch(e["node"]) for e in newest["items"])
        feed = await ask("changes_since", node=story1)
        assert feed["items"] and all(story1 in (e["node"], e["other_node"]) for e in feed["items"])
        await ask("coverage_report", limit=5)
        await ask("list_rules", story=story1)
        await ask("list_rules", unplaced=True)
        await ask("get_rule", rule_id=rule1)
        await ask("rule_history", rule_id=rule1)
        await ask("place_rule", actor="ann", rule_id=rule2, expected_version=2, story=story2)
        await ask("move_rule", actor="ann", rule_id=rule1, expected_version=1, new_story=story2)
        await ask("create_rule", actor="ann", title=f"A rule {word}", story=story1)

        # Decisions and facts.
        await ask("list_decisions")
        await ask("get_decision", decision_id=decision)
        await ask("link_decision", actor="ann", decision_id=decision, nodes=[task2])
        await ask("decision_history", decision_id=decision)
        await ask(
            "record_decision", actor="ann", title=f"Decide {word}", text="Yes", reason="Because",
            decided_by="Marie", nodes=[fresh],
        )  # fmt: skip
        await ask("list_facts")
        await ask("get_fact", fact_id=fact)
        await ask("link_fact", actor="ann", fact_id=fact, nodes=[task2])
        await ask("fact_history", fact_id=fact)
        await ask(
            "retain_fact", actor="ann", text=f"A fact {word}", source="The call", nodes=[story2]
        )

        # Finding: recall, search, similar items, overlaps and mentions.
        await reindexed(client)
        recalled = await ask("recall", node=story1, with_similar=True)
        assert recalled["active_decisions"]["count"] >= 1
        await ask("recall", rule_id=rule1)
        found = await ask("search", query=word, mode="both", limit=50)
        assert {"epic", "story", "task"} & {i["item_type"] for i in found["items"]}
        await ask("search", query="log in", mode="words", limit=50)
        await ask("similar_items", node=story1, min_similarity=0.0)
        await ask("find_overlaps", epics=[epic], min_similarity=0.0)
        await ask("find_overlaps", min_similarity=0.0)
        mentions = await ask("find_mentions", node=task1)
        assert [m["source"]["ref"] for m in mentions["items"]] == [fresh]
        await ask("find_mentions")

        # The whole export, every page of it.
        after, sections = None, set()
        while True:
            page = await ask("export_graph", after=after, limit=7)
            sections.add(page["section"])
            after = page["next_after"]
            if after is None:
                break
        assert sections == {"nodes", "links", "rules", "decisions", "facts", "events"}

        problems = [p for tool, answer in answers for p in id_problems(tool, answer)]
        assert problems == [], problems[:10]
        tools = {tool for tool, _ in answers}
        assert tools >= NODE_TOOLS, NODE_TOOLS - tools
        # The walk saw nodes in every answer that has some, so that it is not passing because it
        # looked at nothing.
        seen = {
            tool: sum(nodes_named(answer) for t, answer in answers if t == tool) for tool in tools
        }
        silent = {tool for tool in ASKED_FOR_NODES if not seen[tool]}
        assert not silent, (silent, seen)

        # No column of any table or view names a node by a number, and every one that names a
        # node holds its reference (text, or a list of text).
        gone = ", ".join(f"'{name}'" for name in sorted(GONE_NAMES))
        columns = run_sql(
            "SELECT table_name || '.' || column_name FROM information_schema.columns "
            f"WHERE table_schema = 'public' AND column_name IN ({gone})",
            plain=True,
            project=None,
        )
        assert columns.returncode == 0 and columns.stdout.strip() == "", columns.stdout
        has_id = run_sql(
            "SELECT table_name FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name IN ('nodes', 'node_state') AND column_name = 'id'",
            plain=True,
            project=None,
        )
        assert has_id.returncode == 0 and has_id.stdout.strip() == "", has_id.stdout
        named = run_sql(NAMES_A_NODE, plain=True, project=None)
        assert named.returncode == 0, named.stderr
        types = dict(line.rsplit(" ", 1) for line in named.stdout.strip().splitlines())
        assert len(types) == 14, types
        lists = {name for name, udt in types.items() if udt == "_text"}
        assert lists == {
            "node_state.waiting_on",
            "node_state.schedule_conflicts",
            "decision_state.nodes",
            "fact_state.nodes",
        }
        assert {udt for name, udt in types.items() if name not in lists} == {"text"}
        # And the events this project made name nodes only by reference.
        pattern = "'^(epic|story|task):[1-9][0-9]*$'"
        counts = sql(
            "SELECT count(*) FILTER (WHERE node IS NOT NULL OR other_node IS NOT NULL), "
            f"count(*) FILTER (WHERE node !~ {pattern} OR other_node !~ {pattern}) "
            f"FROM events WHERE project_id = {project.id}",
            project,
        )
        named_events, wrong = (int(part) for part in counts.split("|"))
        assert named_events > 20 and wrong == 0


# -- 2. a bad reference is refused in plain words, and nothing changes ---------------------------

# What a caller might write where a reference belongs, and which is not one.
NOT_REFERENCES: list[Any] = [
    "12", 12, "story:0", "sprint:3", "story:01", "", "[[story:1]]", "node:12", "story:1:2",
    "story 12", "story:", ":12",
]  # fmt: skip
HERE = object()
W = "tester"
LINK = "relates_to"
DECIDED = {"title": "D", "text": "T", "reason": "R", "decided_by": "Marie"}
NEWER = {"actor": W, "replaces": [1], "reason": "R", "text": "Newer", "source": "S"}
CHANGE = {"expected_version": 1, "title": "N"}


def use(
    tool: str, name: str, expects: str, **arguments: Any
) -> tuple[str, str, str, dict[str, Any]]:
    """A call with a node named in it (where `HERE` is), the name of that argument as a refusal
    says it, and the kind the argument should name."""
    return tool, name, expects, arguments


# Every place in every tool where a node is named. Rule 1 is in story:1, rule 2 is in no story,
# and decision 1 and fact 1 exist.
OLD = {"actor": W, "expected_version": 1}
PLACES = [
    use("get_node", "node", "story", node=HERE),
    use("update_node", "node", "story", **OLD, node=HERE, title="N"),
    use("update_node", "parent", "epic", **OLD, node="story:1", title="N", parent=HERE),
    use("move_node", "node", "story", **OLD, node=HERE, new_parent="epic:2"),
    use("move_node", "new_parent", "epic", **OLD, node="story:1", new_parent=HERE),
    use("node_history", "node", "task", node=HERE),
    use("add_link", "source", "story", actor=W, source=HERE, target="story:2", link_type=LINK),
    use("add_link", "target", "task", actor=W, source="story:1", target=HERE, link_type=LINK),
    use("remove_link", "source", "story", actor=W, source=HERE, target="story:2", link_type=LINK),
    use("remove_link", "target", "task", actor=W, source="story:1", target=HERE, link_type=LINK),
    use("list_links", "node", "epic", node=HERE),
    use("claim_node", "node", "story", actor=W, node=HERE),
    use("renew_claim", "node", "task", actor=W, node=HERE),
    use("release_claim", "node", "task", actor=W, node=HERE, note="A note"),
    use("next_work", "within_node", "epic", actor=W, within_node=HERE),
    use("finish_work", "node", "task", actor=W, node=HERE, outcome="done", note="A note"),
    use("create_node", "parent", "epic", actor=W, kind="story", title="N", parent=HERE),
    use("create_node", "parent", "story", actor=W, kind="task", title="N", parent=HERE),
    use("list_nodes", "parent", "epic", parent=HERE),
    use("list_nodes", "after", "story", after=HERE),
    use("ready_work", "after", "task", after=HERE),
    use("changes_since", "node", "story", node=HERE),
    use("create_rule", "story", "story", actor=W, title="A rule", story=HERE),
    use("list_rules", "story", "story", story=HERE),
    use("place_rule", "story", "story", **OLD, rule_id=2, story=HERE),
    use("move_rule", "new_story", "story", **OLD, rule_id=1, new_story=HERE),
    use("coverage_report", "stories_after", "story", stories_after=HERE),
    use("record_decision", "nodes", "story", actor=W, **DECIDED, nodes=[HERE]),
    use("link_decision", "nodes", "task", actor=W, decision_id=1, nodes=[HERE]),
    use("unlink_decision", "nodes", "story", actor=W, decision_id=1, nodes=[HERE]),
    use("supersede_decision", "nodes", "story", actor=W, decision_id=1, **DECIDED, nodes=[HERE]),
    use("retain_fact", "nodes", "story", actor=W, text="A fact", source="The call", nodes=[HERE]),
    use("link_fact", "nodes", "task", actor=W, fact_id=1, nodes=[HERE]),
    use("unlink_fact", "nodes", "story", actor=W, fact_id=1, nodes=[HERE]),
    use("supersede_fact", "nodes", "story", **NEWER, nodes=[HERE]),
    use("recall", "node", "story", node=HERE),
    use("similar_items", "node", "story", node=HERE),
    use("find_overlaps", "epics", "epic", epics=[HERE]),
    use("find_mentions", "node", "task", node=HERE),
    use("update_nodes", "node", "story", actor=W, changes=[{**CHANGE, "node": HERE}]),
]


def put(value: Any, arguments: Any) -> Any:
    """The arguments of a call with `value` where `HERE` is."""
    if arguments is HERE:
        return value
    if isinstance(arguments, dict):
        return {name: put(value, inner) for name, inner in arguments.items()}
    if isinstance(arguments, list):
        return [put(value, inner) for inner in arguments]
    return arguments


# Where a reference that names nothing is not refused as "There is no story 9999 in this project":
# a page cursor is only a position, one change in a bulk update says why on its own, what a link
# or a decision is linked to is checked against what is linked, and a rule's story is looked at
# after the rule.
OTHER_WORDS = {
    ("list_nodes", "after"), ("ready_work", "after"), ("coverage_report", "stories_after"),
    ("update_nodes", "node"), ("remove_link", "source"), ("remove_link", "target"),
    ("unlink_decision", "nodes"), ("unlink_fact", "nodes"), ("update_node", "parent"),
}  # fmt: skip


NEW_EPIC = {"actor": W, "kind": "epic", "title": "N", "parent": "epic:1"}
NEW_STORY = {"actor": W, "kind": "story", "title": "N", "parent": "story:1"}
NEW_TASK = {"actor": W, "kind": "task", "title": "N", "parent": "epic:1"}
PLANNED = {"key": "e", "kind": "epic", "title": "N"}
RULE = {"actor": W, "title": "N"}
RULE_IN_A_STORY = "A rule belongs to a story, but "


def case(tool: str, arguments: dict[str, Any], says: str) -> tuple[str, dict[str, Any], str]:
    """A call that must be refused, and what the refusal says."""
    return tool, arguments, says


async def a_project_to_refuse_in(client: Client, word: str) -> None:
    """epic:1 and epic:2, story:1 and story:2 in epic:1, story:3 in epic:2, task:1 and task:2 in
    story:1, task:3 in story:2, a rule in story:1 and one in no story, a decision and a fact."""
    await create(client, "epic", f"First epic {word}")
    await create(client, "epic", f"Second epic {word}")
    for title, parent in (("First", "epic:1"), ("Second", "epic:1"), ("Third", "epic:2")):
        await create(client, "story", f"{title} story {word}", parent)
    for title, parent in (("First", "story:1"), ("Second", "story:1"), ("Third", "story:2")):
        await create(client, "task", f"{title} task {word}", parent)
    await ok(client, "create_rule", actor=W, title=f"Placed {word}", story="story:1")
    await ok(client, "create_rule", actor=W, title=f"Unplaced {word}")
    await ok(client, "record_decision", actor=W, nodes=["story:1"], **{**DECIDED, "title": word})
    await ok(
        client, "retain_fact", actor=W, text=f"Fact {word}", source="The call", nodes=["task:1"]
    )


async def test_every_tool_refuses_what_is_not_a_reference_and_changes_nothing() -> None:
    async with fresh_project() as (project, client):
        await a_project_to_refuse_in(client, unique_word())
        before = await whole_project(client, project)
        latest = await newest_event(client)
        tried = 0
        for value in NOT_REFERENCES:
            for tool, name, _, template in PLACES:
                said = await refused(client, tool, **put(value, template))
                where = (tool, name, value, said)
                assert f"{name} must name an epic, story or task by its kind and number" in said, (
                    where
                )
                assert "such as story:12" in said and f"but it is {str(value)!r}" in said, where
                assert "validation error" not in said and "Traceback" not in said, where
                assert await newest_event(client) == latest, where
                tried += 1
        assert tried == len(NOT_REFERENCES) * len(PLACES) and len(PLACES) >= 40
        assert await whole_project(client, project) == before


async def test_a_reference_that_names_nothing_is_refused_with_its_kind_and_number() -> None:
    async with fresh_project() as (project, client):
        await a_project_to_refuse_in(client, unique_word())
        before = await whole_project(client, project)
        latest = await newest_event(client)
        for tool, name, kind, template in PLACES:
            if (tool, name) in OTHER_WORDS:
                continue
            # The kind that fits the place, so that it is the number that is missing.
            said = await refused(client, tool, **put(f"{kind}:9999", template))
            assert f"There is no {kind} 9999 in this project" in said, (tool, name, said)
            assert await newest_event(client) == latest, (tool, name)
        assert await whole_project(client, project) == before

        # One bulk change that names nothing says so, and the others are made on their own.
        mixed = await ok(
            client,
            "update_nodes",
            actor=W,
            changes=[
                {"node": "story:9999", "expected_version": 1, "title": "Nowhere"},
                {"node": "story:3", "expected_version": 1, "title": "Somewhere"},
            ],
        )
        assert [(r["node"], r["applied"]) for r in mixed["results"]] == [
            ("story:9999", False),
            ("story:3", True),
        ]
        assert "There is no story 9999 in this project" in mixed["results"][0]["refusal"]
        assert (await ok(client, "get_node", node="story:3"))["title"] == "Somewhere"


async def test_a_reference_of_the_wrong_kind_is_refused_and_changes_nothing() -> None:
    async with fresh_project() as (project, client):
        await a_project_to_refuse_in(client, unique_word())
        before = await whole_project(client, project)
        latest = await newest_event(client)
        wrong: list[tuple[str, dict[str, Any], str]] = [
            case("create_node", NEW_STORY, "A story belongs under an epic, but story:1 is a story"),
            case("create_node", NEW_TASK, "A task belongs under a story, but epic:1 is an epic"),
            case(
                "create_node",
                {**NEW_TASK, "parent": "task:1"},
                "A task belongs under a story, but task:1 is a task",
            ),
            case("create_node", NEW_EPIC, "An epic has no parent"),
            case(
                "move_node",
                {**OLD, "node": "story:1", "new_parent": "story:2"},
                "A story belongs under an epic, but story:2 is a story",
            ),
            case(
                "move_node",
                {**OLD, "node": "task:1", "new_parent": "epic:1"},
                "A task belongs under a story, but epic:1 is an epic",
            ),
            case(
                "move_node",
                {**OLD, "node": "epic:1", "new_parent": "epic:2"},
                "epic:1 is an epic. An epic has no parent, so it cannot be moved",
            ),
            case(
                "update_node",
                {**OLD, "node": "story:1", "title": "Changed", "parent": "epic:2"},
                "move_node",
            ),
            case(
                "claim_node",
                {"actor": W, "node": "epic:1"},
                "epic:1 is an epic. Only stories and tasks can be claimed",
            ),
            case("create_rule", {**RULE, "story": "task:1"}, RULE_IN_A_STORY + "task:1 is a task"),
            case("create_rule", {**RULE, "story": "epic:1"}, RULE_IN_A_STORY + "epic:1 is an epic"),
            case(
                "place_rule",
                {**OLD, "rule_id": 2, "story": "epic:1"},
                RULE_IN_A_STORY + "epic:1 is an epic",
            ),
            case(
                "move_rule",
                {**OLD, "rule_id": 1, "new_story": "task:1"},
                RULE_IN_A_STORY + "task:1 is a task",
            ),
            case("list_rules", {"story": "epic:2"}, RULE_IN_A_STORY + "epic:2 is an epic"),
            case(
                "find_overlaps",
                {"epics": ["story:1"]},
                "epics must name epics, such as epic:3, but story:1 is not an epic",
            ),
            case(
                "add_link",
                {"actor": W, "source": "story:1", "target": "story:1", "link_type": LINK},
                "A node cannot be linked to itself (both ends are story:1)",
            ),
            case(
                "create_plan",
                {"actor": W, "items": [{**PLANNED, "kind": "story", "parent": "task:1"}]},
                "task:1 is a task, but a story belongs under an epic",
            ),
            case(
                "create_plan",
                {
                    "actor": W,
                    "items": [PLANNED, {**PLANNED, "key": "t", "kind": "task", "parent": "e"}],
                },
                "its parent 'e' is an epic, but a task belongs under a story",
            ),
            case(
                "create_plan",
                {"actor": W, "items": [PLANNED], "rules": [{"title": "N", "story": "task:1"}]},
                "task:1 is not a story, and a rule belongs to a story",
            ),
        ]
        for tool, arguments, says in wrong:
            said = await refused(client, tool, **arguments)
            assert says in said, (tool, says, said)
            assert await newest_event(client) == latest, (tool, arguments)
        assert await whole_project(client, project) == before

        # A circle of waiting is refused, in words naming the references, and the links stay.
        for waiting, first in (("task:2", "task:1"), ("task:3", "task:2")):
            ends = {"source": waiting, "target": first, "link_type": "needs_first"}
            await ok(client, "add_link", actor=W, **ends)
        before = await whole_project(client, project)
        for source, target in (("task:1", "task:2"), ("task:1", "task:3"), ("task:2", "task:3")):
            ends = {"source": source, "target": target, "link_type": "needs_first"}
            said = await refused(client, "add_link", actor=W, **ends)
            assert "circle of waiting" in said and source in said and target in said, said
        assert await whole_project(client, project) == before
        # So is a plan whose link would make one among items that exist: nothing is created.
        said = await refused(
            client,
            "create_plan",
            actor=W,
            items=[{"key": "t", "kind": "task", "title": "N", "parent": "story:1"}],
            links=[{"source": "task:1", "target": "task:3", "link_type": "needs_first"}],
        )
        assert "circle" in said, said
        assert await whole_project(client, project) == before


async def test_a_reference_may_be_written_in_capitals_with_spaces_and_is_stored_as_one() -> None:
    async with fresh_project() as (project, client):
        await a_project_to_refuse_in(client, unique_word())
        assert (await ok(client, "get_node", node=" Story:1 "))["ref"] == "story:1"
        assert (await ok(client, "get_node", node="TASK:2"))["ref"] == "task:2"
        link = await ok(
            client, "add_link", actor=W, source=" Task:1", target="TASK:3", link_type=LINK
        )
        assert (link["source"], link["target"]) == ("task:1", "task:3")
        stored = sql(
            f"SELECT source || '>' || target FROM links WHERE project_id = {project.id}", project
        )
        assert stored == "task:1>task:3"
        assert (await ok(client, "list_links", node="STORY:2"))["items"] == []
        listed = (await ok(client, "list_links", node="Task:3"))["items"]
        assert [(i["source"], i["target"]) for i in listed] == [("task:1", "task:3")]
        moved = await ok(
            client, "move_node", actor=W, node=" Task:2 ", expected_version=1, new_parent=" STORY:3"
        )
        assert moved["parent"] == "story:3"
        history = (await ok(client, "node_history", node="task:2"))["items"]
        assert [(e["action"], e["node"]) for e in history] == [
            ("create", "task:2"),
            ("move", "task:2"),
        ]


# -- 3. text that names a node, and the filter language ------------------------------------------


async def test_three_references_with_one_number_are_three_mentions_and_a_rename_is_followed() -> (
    None
):
    async with fresh_project() as (project, client):
        word = unique_word()
        epic = await create(client, "epic", f"Alpha epic {word}")
        story = await create(client, "story", f"Beta story {word}", epic)
        task = await create(client, "task", f"Gamma task {word}", story)
        assert (epic, story, task) == ("epic:1", "story:1", "task:1")
        text = (
            "Names [[epic:1]], [[story:1]] and [[task:1]]. Not names: [[node:1]], [[story:01]], "
            "[[Story:1]], [[story:1 ]] and [[story:x]]. A missing one: [[story:99]]."
        )
        holder = await ok(
            client, "create_node", actor=W, kind="epic", title=f"Holder {word}", body=text
        )
        assert holder["ref"] == "epic:2"

        everything = await ok(client, "find_mentions", limit=200)
        assert everything["total"] == 4
        assert [
            (m["source"]["ref"], m["target"]["ref"], m["target"]["exists"])
            for m in everything["items"]
        ] == [
            ("epic:2", "epic:1", True),
            ("epic:2", "story:1", True),
            ("epic:2", "story:99", False),
            ("epic:2", "task:1", True),
        ]
        assert {m["target"]["ref"]: m["target"]["title"] for m in everything["items"]} == {
            "epic:1": f"Alpha epic {word}",
            "story:1": f"Beta story {word}",
            "task:1": f"Gamma task {word}",
            "story:99": None,
        }
        for ref in ("epic:1", "story:1", "task:1"):
            who = (await ok(client, "find_mentions", node=ref))["items"]
            assert [(m["source"]["ref"], m["target"]["ref"]) for m in who] == [("epic:2", ref)]
            assert who[0]["source"]["item_type"] == "epic" and who[0]["fields"] == ["details"]
            assert "id" not in who[0]["source"] and "id" not in who[0]["target"]

        # A rename of what is named, and of what names it, is followed the next time it is asked.
        beta = await ok(client, "get_node", node="story:1")
        await ok(
            client, "update_node", actor=W, node="story:1", expected_version=beta["version"],
            title=f"Beta renamed {word}",
        )  # fmt: skip
        renamed = (await ok(client, "find_mentions", node="story:1"))["items"]
        assert renamed[0]["target"]["title"] == f"Beta renamed {word}"
        await ok(
            client, "update_node", actor=W, node="epic:2", expected_version=1,
            title=f"Holder renamed {word}",
        )  # fmt: skip
        renamed = (await ok(client, "find_mentions", node="task:1"))["items"]
        assert renamed[0]["source"]["title"] == f"Holder renamed {word}"
        alpha = (await ok(client, "find_mentions", node="epic:1"))["items"]
        assert alpha[0]["target"]["title"] == f"Alpha epic {word}"

        # Only the item with that kind is named: story:1 is not epic:1, whoever else says epic:1.
        third = await ok(
            client, "create_node", actor=W, kind="epic", title="Third", body="Only [[epic:1]]."
        )
        assert third["ref"] == "epic:3"
        epic_one = await ok(client, "find_mentions", node="epic:1")
        assert [m["source"]["ref"] for m in epic_one["items"]] == ["epic:2", "epic:3"]
        story_one = await ok(client, "find_mentions", node="story:1")
        assert [m["source"]["ref"] for m in story_one["items"]] == ["epic:2"]
        # A reference is only text: it made no link.
        assert sql(f"SELECT count(*) FROM links WHERE project_id = {project.id}", project) == "0"


async def refs(client: Client, query: str) -> set[str]:
    return {n["ref"] for n in await all_nodes(client, query=query)}


async def test_the_filter_language_finds_by_kind_epic_story_parent_and_number() -> None:
    async with fresh_project() as (_, client):
        await numbered_project(client, unique_word())
        stories = {f"story:{n}" for n in (1, 2, 3, 4)}
        tasks = {f"task:{n}" for n in (1, 2, 3, 4, 5)}
        epics = {"epic:1", "epic:2", "epic:3"}

        assert await refs(client, "kind:epic") == epics
        assert await refs(client, "kind:story") == stories
        assert await refs(client, "kind:epic,task") == epics | tasks
        # epic:N and story:N take the number as it is typed, and find everything under it.
        assert await refs(client, "epic:1") == {
            "epic:1", "story:1", "story:4", "task:1", "task:4", "task:5",
        }  # fmt: skip
        assert await refs(client, "epic:3") == {"epic:3", "story:3", "task:3"}
        assert await refs(client, "story:1") == {"story:1", "task:1", "task:5"}
        # story:3 is story 3 and its task, not epic 3, though both are 3.
        assert await refs(client, "story:3") == {"story:3", "task:3"}
        assert await refs(client, "epic:1,2 kind:task") == {"task:1", "task:2", "task:4", "task:5"}
        assert await refs(client, "epic:2 OR story:4") == {
            "epic:2", "story:2", "task:2", "story:4", "task:4",
        }  # fmt: skip
        assert await refs(client, "kind:task -story:1") == {"task:2", "task:3", "task:4"}
        # parent: takes a reference and finds what is directly under it.
        assert await refs(client, "parent:epic:1") == {"story:1", "story:4"}
        assert await refs(client, "parent:story:1") == {"task:1", "task:5"}
        assert await refs(client, "parent:epic:1,epic:3") == {"story:1", "story:4", "story:3"}
        for ref in ("epic:1", "story:1"):
            direct = {n["ref"] for n in await all_nodes(client, parent=ref)}
            assert await refs(client, f"parent:{ref}") == direct
        # number: finds the items with that number, of any kind unless kind: says which.
        assert await refs(client, "number:3") == {"epic:3", "story:3", "task:3"}
        assert await refs(client, "number:3 kind:story") == {"story:3"}
        assert await refs(client, "number:4,5 kind:task") == {"task:4", "task:5"}
        assert await refs(client, "number:5") == {"task:5"}
        assert await refs(client, "no:parent") == epics
        assert await refs(client, "-no:parent kind:story") == stories
        # Through the pages of a list, the same text gives the same items in order.
        page = await ok(client, "list_nodes", query="epic:1", limit=2)
        assert [n["ref"] for n in page["items"]] == ["epic:1", "story:1"] and page["total"] == 6
        rest = await ok(client, "list_nodes", query="epic:1", limit=10, after=page["next_after"])
        assert [n["ref"] for n in rest["items"]] == ["story:4", "task:1", "task:4", "task:5"]

        # What is not in the language, or is not a reference, is refused in words.
        parent = "parent needs the reference of an epic or a story"
        for query, says in [
            ("id:3", "there is no field called 'id'"),
            ("ref:story:1", "there is no field called 'ref'"),
            ("parent:3", parent),
            ("parent:epic", parent),
            ("parent:Sprint:3", parent),
            ("epic:epic:1", "epic needs a whole number such as epic:12"),
            ("story:one", "story needs a whole number such as story:12"),
            ("number:3.5", "number needs a whole number such as number:12"),
            ("number:", "number: needs a value"),
        ]:
            said = await refused(client, "list_nodes", query=query)
            assert "The query was not understood" in said and says in said, (query, said)
            assert "Nothing was listed" in said


# -- 4. moving a project: export, import and the Markdown copy -----------------------------------


@pytest.fixture
def stack() -> Iterator[SecondStack]:
    second = SecondStack()
    try:
        yield second
    finally:
        second.close()


def script(name: str, key: str, *arguments: str | Path) -> subprocess.CompletedProcess[str]:
    """A script of the repository, aimed at one project by its agent key."""
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / name), *map(str, arguments)],
        cwd=REPO_ROOT,
        env={**os.environ, "VRUM_AGENT_KEY": key},
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def files_of(folder: Path) -> dict[str, str]:
    """Every file of a Markdown copy, by its path under the folder."""
    return {
        str(path.relative_to(folder)): path.read_text(encoding="utf-8")
        for path in sorted(folder.rglob("*.md"))
    }


def next_numbers(document: dict[str, Any]) -> dict[str, int]:
    """The number each kind goes on from: after the highest of its own kind."""
    return {
        kind: max((n["number"] for n in document["nodes"] if n["kind"] == kind), default=0) + 1
        for kind in KINDS
    }


async def a_project_with_uneven_numbers(client: Client) -> dict[str, dict[str, Any]]:
    """The rich world (1 epic, 2 stories, 3 tasks), then more of each kind, so that no two kinds
    end on the same number: 3 epics, 4 stories and 8 tasks."""
    world = await rich_world(client)
    story1, story2 = world["story1"]["ref"], world["story2"]["ref"]
    await ok(
        client,
        "create_plan",
        actor="ann",
        items=[
            {"key": "e1", "kind": "epic", "title": "Second epic"},
            {"key": "e2", "kind": "epic", "title": "Third epic", "body": f"See [[{story2}]]."},
            {"key": "s1", "kind": "story", "title": "Third story", "parent": "e1"},
            {"key": "s2", "kind": "story", "title": "Fourth story", "parent": "e2"},
            {"key": "t1", "kind": "task", "title": "Fourth task", "parent": "s1"},
            {"key": "t2", "kind": "task", "title": "Fifth task", "parent": "s1"},
            {"key": "t3", "kind": "task", "title": "Sixth task", "parent": "s2"},
            {"key": "t4", "kind": "task", "title": "Seventh task", "parent": story1},
            {"key": "t5", "kind": "task", "title": "Eighth task", "parent": "s2"},
        ],
        links=[{"source": "t3", "target": world["task1"]["ref"], "link_type": "needs_first"}],
        rules=[{"title": "A planned rule", "story": "s2"}],
    )
    return world


async def test_an_export_loads_into_another_project_with_every_number_and_the_next_item_continues(
    stack: SecondStack, tmp_path: Path
) -> None:
    async with fresh_project() as (first, client):
        world = await a_project_with_uneven_numbers(client)
        original = await export_everything(client)
        assert next_numbers(original) == {"epic": 4, "story": 5, "task": 9}

        # In the file a node is its kind and number, and everything else names it by reference.
        assert all(set(n) >= {"kind", "number", "parent"} for n in original["nodes"])
        assert all("id" not in n for n in original["nodes"])
        assert all({"id", "source", "target"} <= set(link) for link in original["links"])
        assert all(set(rule) >= {"id", "story"} for rule in original["rules"])
        assert all("nodes" in d and "node_ids" not in d for d in original["decisions"])
        assert all("nodes" in f and "node_ids" not in f for f in original["facts"])
        assert all({"node", "other_node"} <= set(e) for e in original["events"])
        assert claims_of(first) != "", "the world holds a claim, which a file does not"

        exported = tmp_path / "export.json"
        result = script("export_graph.py", first.key, first.mcp_url, exported)
        assert result.returncode == 0, result.stdout + result.stderr
        copy = tmp_path / "markdown-first"
        result = script("export_markdown.py", first.key, first.mcp_url, copy)
        assert result.returncode == 0, result.stdout + result.stderr

        stack.up()
        async with stack.client() as second:
            loaded = stack.python("import_graph.py", stack.url, exported)
            assert loaded.returncode == 0, loaded.stdout + loaded.stderr
            assert f"Imported {sum(original['counts'].values())} items" in loaded.stdout

            # Every number, link, decision, fact and event is as it was.
            assert await export_everything(second) == original
            for node in original["nodes"]:
                ref = f"{node['kind']}:{node['number']}"
                here = await ok(client, "get_node", node=ref)
                there = await ok(second, "get_node", node=ref)
                worked_out = ("waiting_on", "schedule_conflicts", "progress_done", "progress_total")
                for name in ("ref", "parent", "title", "body", "status", "version", *worked_out):
                    assert there[name] == here[name], (ref, name)
            # A file holds no claim: the new project has none.
            working = (await ok(second, "who_is_working"))["agents"]
            assert [held for a in working for held in a["holding"] + a["lapsed"]] == []
            assert (await ok(second, "get_node", node=world["task3"]["ref"]))["claimed_by"] is None

            # The Markdown copy of the loaded project is the first one's, file for file.
            again = tmp_path / "markdown-second"
            result = stack.python("export_markdown.py", stack.url, again)
            assert result.returncode == 0, result.stdout + result.stderr
            assert files_of(again) == files_of(copy)

            # The next item of each kind continues after the highest of its own kind.
            want = next_numbers(original)
            assert await create(second, "epic", "New epic") == f"epic:{want['epic']}"
            made = await create(second, "story", "New story", f"epic:{want['epic']}")
            assert made == f"story:{want['story']}"
            assert await create(second, "task", "New task", made) == f"task:{want['task']}"
            assert await create(second, "task", "More", "story:1") == f"task:{want['task'] + 1}"
            # What is loaded works: the reference in a text is still the same story.
            mentions = await ok(second, "find_mentions", node=world["story2"]["ref"])
            assert [m["source"]["ref"] for m in mentions["items"]] == ["epic:3"]

    # The Markdown copy has one file per item, named by kind and number.
    files = files_of(copy)
    items = sum(len(original[section]) for section in ("nodes", "rules", "decisions", "facts"))
    assert len(files) == items
    for node in original["nodes"]:
        folder = {"epic": "epics", "story": "stories", "task": "tasks"}[node["kind"]]
        prefix = f"{folder}/{node['kind']}-{node['number']}-"
        matching = [name for name in files if name.startswith(prefix)]
        assert len(matching) == 1, (prefix, matching)
        head = files[matching[0]].split("---")[1]
        assert f"\ntype: {node['kind']}\n" in head and f"\nnumber: {node['number']}\n" in head
        assert "\nid:" not in head
    assert all(
        re.match(r"(epics/epic|stories/story|tasks/task)-[1-9][0-9]*-", name)
        for name in files
        if name.startswith(("epics/", "stories/", "tasks/"))
    )
    stems = {Path(name).stem for name in files}
    for name, text in files.items():
        for target in re.findall(r"\[\[([^\]]+)\]\]", text):
            assert target in stems, (name, target)
