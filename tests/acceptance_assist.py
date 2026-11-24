"""Chapter of the acceptance scenario for review assistance (M50): what a person is shown before
deciding, told from outside through a real MCP client, the board's own calls as the people of a
project, and then a headless browser on the Needs a person page.

The server works three things out when a proposal is read, and never stores them: the checks of a
drafted plan (whether it makes a circle of waiting, which stories and tasks have no size, which
epics and stories look like work that already exists), the similarity of each story or task in a
suggested move to the parent it is proposed for (the very score `suggest_parent` gives), and
whether a move is routine. None of it decides anything: a warning does not stop an approval and a
score is evidence, never approval. The scripted embedding service of the stack stands in for the
model, so the answers are the same every run. Each part works in a project of its own."""

import re
from typing import Any

from mcp import Client

from tests.acceptance_scenario import chapter, clip, proof
from tests.helpers import (
    REPO_ROOT,
    connect,
    embeddings_stopped,
    make_tree,
    ok,
    refused,
    reindexed,
    unique_word,
)
from tests.test_placement_integration import a_sorted_project
from tests.world import Person, Project, join_project, make_key, make_project, register

DOCS = REPO_ROOT / "docs" / "agents.md"


def words(refusal: str) -> str:
    return refusal.split(": ", 1)[1] if refusal.startswith("Error executing tool") else refusal


def person(project: Project, who: Person, tool: str, **arguments: Any) -> Any:
    """What the board does when a person presses a button: the tool, as that person. (The board's
    own route, so that an argument may be called `person`, as set_confirmation's is.)"""
    return who.call("POST", f"/board/api/projects/{project.id}/tool/{tool}", arguments)


def four_words() -> str:
    """A title that means nothing else in the project: the scripted service takes a word that is
    not in one of its groups to stand for itself."""
    return " ".join(unique_word() for _ in range(4))


async def the_whole_plan(agent: Client, number: int) -> dict[str, Any]:
    (plan,) = (await ok(agent, "list_plans", plan=number))["items"]
    return dict(plan)


def by_kind(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {check["kind"]: check for check in plan["checks"]}


async def a_plan_that_needs_a_look(
    agent: Client, epic: dict[str, Any], repeats: str
) -> dict[str, Any]:
    """A drafted plan with a story that repeats the title of work that exists (`repeats`), another
    story, a sized task, two tasks with no size (one of them waits for the sized one) and an
    acceptance criterion. Returns what create_plan said."""
    return await ok(
        agent,
        "create_plan",
        draft=True,
        title="Needs a look",
        items=[
            {"key": "dup", "kind": "story", "title": repeats, "parent": epic["ref"], "size": "m"},
            {
                "key": "own",
                "kind": "story",
                "title": four_words(),
                "parent": epic["ref"],
                "size": "m",
            },
            {"key": "sized", "kind": "task", "title": four_words(), "parent": "own", "size": "s"},
            {"key": "loose1", "kind": "task", "title": four_words(), "parent": "own"},
            {"key": "loose2", "kind": "task", "title": four_words(), "parent": "dup"},
        ],
        links=[{"source": "loose1", "target": "sized", "link_type": "needs_first"}],
        rules=[{"title": "It works for a rider", "node": "own"}],
    )


async def the_checks_of_a_plan() -> None:
    """The three checks of a drafted plan agree with the graph and the similarity read, and
    nothing was sized or decided by them."""
    owner = register()
    project = make_project(owner)
    repeats = four_words()
    async with connect(project.mcp_url, project.key) as agent:
        epic, story, _ = await make_tree(agent)
        existing = await ok(
            agent, "create_node", actor="x", kind="story", title=repeats, parent=epic["ref"]
        )
        await reindexed(agent)
        made = await a_plan_that_needs_a_look(agent, epic, repeats)
        refs = {c["key"]: c["ref"] for c in made["created"]}
        plan = await the_whole_plan(agent, made["plan"])
        checks = by_kind(plan)
        in_the_plan = set(refs.values())
        graph = await ok(agent, "list_nodes", query="no:size", limit=200)
        unsized_by_the_graph = sorted(n["ref"] for n in graph["items"] if n["ref"] in in_the_plan)
        unsized_by_the_check = sorted(i for f in checks["unsized"]["findings"] for i in f["items"])
        similar = await ok(
            agent,
            "similar_items",
            text=repeats,
            types=["story"],
            limit=10,
        )
        sizes = [(await ok(agent, "get_node", node=ref))["size"] for ref in refs.values()]
        (finding,) = checks["duplicate"]["findings"]
        near = finding["related"][0]
        proof(
            "M50",
            "a drafted plan is read with three checks in order, circle, unsized and duplicate: no "
            "circle of waiting; the stories and tasks with no size are exactly the ones the "
            "filter no:size finds among the plan's items, each named by its reference, and none "
            "was given a size; and the story that repeats existing work names that story, the "
            "very one a search for similar items names for the same words",
            [c["kind"] for c in plan["checks"]] == ["circle", "unsized", "duplicate"]
            and checks["circle"]["state"] == "ok"
            and checks["unsized"]["state"] == "warning"
            and unsized_by_the_check
            == unsized_by_the_graph
            == sorted([refs["loose1"], refs["loose2"]])
            and sizes.count(None) == 2
            and checks["duplicate"]["state"] == "warning"
            and finding["items"] == [refs["dup"]]
            and near["ref"] == existing["ref"]
            and near["title"] == repeats
            and near["similarity"] is not None
            and near["similarity"] >= 0.55
            and existing["ref"] in [r["ref"] for r in similar["items"]]
            and story["ref"]
            not in [r["ref"] for f in checks["duplicate"]["findings"] for r in f["related"]],
            f"unsized {unsized_by_the_check}, repeats {finding['items']} like {near['ref']}",
        )

        # Sizing what the check names makes that check quiet: it reads the graph, not a record.
        for ref in (refs["loose1"], refs["loose2"]):
            node = await ok(agent, "get_node", node=ref)
            await ok(agent, "update_node", node=ref, size="xs", expected_version=node["version"])
        after = by_kind(await the_whole_plan(agent, made["plan"]))
        proof(
            "M50",
            "a check reads the graph as it is now and is not a stored verdict: once the two "
            "tasks are sized by a person or an agent, the check for work with no size is ok and "
            "names nothing, and the one for repeated work still names the story",
            after["unsized"]["state"] == "ok"
            and after["unsized"]["findings"] == []
            and after["duplicate"]["state"] == "warning",
            after["unsized"]["words"],
        )

        # Circles of waiting stay refused, by the same rule, and the check says there are none.
        in_a_circle = words(
            await refused(
                agent,
                "add_link",
                source=refs["sized"],
                target=refs["loose1"],
                link_type="needs_first",
            )
        )
        through_what_exists = [
            await ok(
                agent,
                "add_link",
                source=story["ref"],
                target=refs["sized"],
                link_type="needs_first",
            ),
            words(
                await refused(
                    agent,
                    "add_link",
                    source=refs["sized"],
                    target=story["ref"],
                    link_type="needs_first",
                )
            ),
        ]
        written_with_one = words(
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
        still = by_kind(await the_whole_plan(agent, made["plan"]))["circle"]
        proof(
            "M50",
            "a circle of waiting stays refused whatever the review says: add_link refuses a "
            "link that would close one between two items of the plan and one through work that "
            "already exists, create_plan refuses a plan that writes one, each in words that say "
            "so, and the plan's circle check still says nothing waits for itself",
            "circle of waiting" in in_a_circle
            and "circle of waiting" in through_what_exists[1]
            and "circle of waiting" in written_with_one
            and still["state"] == "ok"
            and still["findings"] == [],
            clip(in_a_circle),
        )


async def the_check_that_cannot_look() -> None:
    """With meaning search down, the duplicate check says it did not look. It is never read as
    "no duplicates"."""
    project = make_project(register())
    repeats = four_words()
    async with connect(project.mcp_url, project.key) as agent:
        epic, _, _ = await make_tree(agent)
        await ok(agent, "create_node", actor="x", kind="story", title=repeats, parent=epic["ref"])
        await reindexed(agent)
        made = await a_plan_that_needs_a_look(agent, epic, repeats)
        looking = by_kind(await the_whole_plan(agent, made["plan"]))["duplicate"]
        with embeddings_stopped():
            blind = by_kind(await the_whole_plan(agent, made["plan"]))
        later = by_kind(await the_whole_plan(agent, made["plan"]))["duplicate"]
        proof(
            "M50",
            "with the embedding service stopped the check for repeated work is unavailable, not "
            "ok: it says in words that it did not look, names nothing and is not the sentence "
            "for no duplicates, the other two checks are unaffected, and once the service is "
            "back it finds the repeat again",
            looking["state"] == "warning"
            and blind["duplicate"]["state"] == "unavailable"
            and blind["duplicate"]["findings"] == []
            and "Not checked" in blind["duplicate"]["words"]
            and "No indexed" not in blind["duplicate"]["words"]
            and blind["circle"]["state"] == "ok"
            and blind["unsized"]["state"] == "warning"
            and later["state"] == "warning",
            clip(blind["duplicate"]["words"]),
        )


async def checks_only_for_a_plan_that_waits() -> None:
    """Checks are for a plan that still waits; they never stop a decision; a viewer reads them
    and cannot decide; an agent cannot decide either."""
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer")
    async with connect(project.mcp_url, project.key) as agent:
        epic, _, _ = await make_tree(agent)
        made = await a_plan_that_needs_a_look(agent, epic, four_words())
        listed = (await ok(agent, "list_plans"))["items"][0]
        seen = person(project, viewer, "list_plans", plan=made["plan"])
        tried = person(project, viewer, "approve_plan", plan=made["plan"], approved=True)
        by_an_agent = words(await refused(agent, "approve_plan", plan=made["plan"], approved=True))
        approved = person(project, owner, "approve_plan", plan=made["plan"], approved=True)
        after = await the_whole_plan(agent, made["plan"])
        free = [(await ok(agent, "get_node", node=c["ref"]))["held_by"] for c in made["created"]]
        second = await a_plan_that_needs_a_look(agent, epic, four_words())
        rejected = person(
            project, owner, "approve_plan", plan=second["plan"], approved=False, reason="Too wide"
        )
        proof(
            "M50",
            "checks are only for a plan that still waits (a list of plans, an approved plan and a "
            "rejected plan carry none), a viewer reads them on the board and is refused deciding "
            "the plan, an agent is refused too, and a person approves a plan whose checks warn: "
            "the warnings change nothing about what the decision does",
            listed["checks"] is None
            and seen.status == 200
            and [c["kind"] for c in seen.body["items"][0]["checks"]]
            == ["circle", "unsized", "duplicate"]
            and tried.status in (400, 403)
            and "not allowed to use" in by_an_agent
            and approved.status == 200
            and approved.body["checks"] is None
            and after["state"] == "approved"
            and after["checks"] is None
            and free == [[]] * len(made["created"])
            and rejected.status == 200
            and (await the_whole_plan(agent, second["plan"]))["checks"] is None,
            f"approved by {owner.name} with warnings",
        )


async def moves_score_like_suggest_parent() -> None:
    """Each moved item scores as suggest_parent scores it for the proposed parent, and a proposal
    is routine only when all of it agrees."""
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer")
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        fine = (
            await ok(
                agent,
                "propose_placement",
                items=[refs["loose_pay"]],
                parent=refs["billing"],
                reason="About money.",
            )
        )["id"]
        mixed = (
            await ok(
                agent,
                "propose_placement",
                items=[refs["loose_mail"], refs["loose_login"]],
                parent=refs["billing"],
                reason="Both about people.",
            )
        )["id"]
        held = (
            await ok(
                agent,
                "propose_placement",
                items=[refs["loose_story"]],
                parent=refs["billing"],
                reason="Loose tasks.",
            )
        )["id"]
        await ok(agent, "claim_node", node=refs["loose_story"])
        waiting = {
            p["id"]: p for p in (await ok(agent, "list_placements", status="proposed"))["items"]
        }
        wrong: list[str] = []
        for number in (fine, mixed):
            for score in waiting[number]["scores"]:
                asked = await ok(agent, "suggest_parent", node=score["ref"], limit=10)
                ranked = [c["ref"] for c in asked["suggestions"]]
                mine = asked["suggestions"][ranked.index(refs["billing"])]
                if (
                    abs(score["similarity"] - mine["score"]) > 1e-9
                    or score["rank"] != ranked.index(refs["billing"]) + 1
                    or score["best"] != asked["suggestions"][0]["ref"]
                    or score["sure"] is not asked["sure"]
                ):
                    wrong.append(score["ref"])
        needs = await ok(agent, "needs_a_person")
        rows = [i for g in needs["groups"] for i in g["items"] if i["kind"] == "placement"]
        as_viewer = person(project, viewer, "list_placements", status="proposed")
        decided_by_the_viewer = person(
            project, viewer, "decide_placement", placement=fine, accepted=True
        )
        by_an_agent = words(await refused(agent, "decide_placement", placement=fine, accepted=True))
        proof(
            "M50",
            "in a suggested move each item's similarity, rank, best parent and sureness are "
            "exactly what suggest_parent says for that item and that parent; a move is routine "
            "only when every item ranks first and is clearly ahead (the one about money is, "
            "and the server says so in a sentence), a move where an item ranks second or is "
            "hardly ahead is not, and says which; the Needs a person list carries none of it, a "
            "viewer reads it and is refused deciding, and nothing moved",
            not wrong
            and waiting[fine]["routine"] is True
            and "clearly best parent" in waiting[fine]["routine_why"]
            and waiting[mixed]["routine"] is False
            and (
                "ranks" in waiting[mixed]["routine_why"]
                or "not clearly" in waiting[mixed]["routine_why"]
            )
            and len(rows) == 3
            and all(
                r["placement"]["scores"] is None and r["placement"]["routine"] is None for r in rows
            )
            and as_viewer.status == 200
            and {p["id"]: p["routine"] for p in as_viewer.body["items"]}[fine] is True
            and decided_by_the_viewer.status in (400, 403)
            and "not allowed to use" in by_an_agent
            and (await ok(agent, "get_node", node=refs["loose_pay"]))["parent"] == refs["inbox"],
            f"{len(waiting)} waiting, routine {[n for n, p in waiting.items() if p['routine']]}",
        )

        weighed = waiting[held]
        (score,) = weighed["scores"]
        proof(
            "M50",
            "a moved item that is held by an agent is a warning, in words, with its score still "
            "shown, and a proposal with a warning is never routine, whatever its score",
            score["warning"] is not None
            and "is held by" in score["warning"]
            and score["similarity"] is not None
            and weighed["routine"] is False
            and weighed["routine_why"] == score["warning"],
            clip(score["warning"]),
        )

        rejected = person(
            project, owner, "decide_placement", placement=mixed, accepted=False, reason="No."
        )
        grouped = (
            await ok(
                agent,
                "propose_placement",
                items=[refs["loose_mail"], refs["loose_login"]],
                new_epic={"title": "People", "body": ""},
                reason="Two themes.",
            )
        )["id"]
        new = next(
            p
            for p in (await ok(agent, "list_placements", status="proposed"))["items"]
            if p["id"] == grouped
        )
        history = (await ok(agent, "list_placements", status="rejected"))["items"]
        proof(
            "M50",
            "a move that makes a new epic has no parent to score against, says so in words and "
            "is never routine, and a proposal that was decided carries no scores and no routine "
            "verdict at all",
            rejected.status == 200
            and new["scores"] == []
            and new["scores_note"]
            and new["routine"] is False
            and "new epic" in new["routine_why"]
            and history[0]["scores"] is None
            and history[0]["routine"] is None,
            clip(new["scores_note"] or ""),
        )


async def the_examples_in_the_docs() -> None:
    """The two TypeScript examples that docs/agents.md gives for the checks of a plan and for the
    proposals to look at twice run in code mode as written (with the plan's number put in) and say
    what the tools say."""
    blocks = re.findall(r"```ts\n(.*?)```", DOCS.read_text(encoding="utf-8"), flags=re.S)
    (for_checks,) = [b for b in blocks if b.startswith("// Before asking a person to approve")]
    (for_moves,) = [b for b in blocks if b.startswith("// The proposals a person would have")]
    owner = register()
    project = make_project(owner)
    repeats = four_words()
    async with connect(project.mcp_url, project.key) as agent:
        epic, _, _ = await make_tree(agent)
        await ok(agent, "create_node", actor="x", kind="story", title=repeats, parent=epic["ref"])
        await reindexed(agent)
        made = await a_plan_that_needs_a_look(agent, epic, repeats)
        refs = {c["key"]: c["ref"] for c in made["created"]}
        # Everything indexed first, so that the sentence of the duplicate check is the same when
        # the example reads it again later.
        await reindexed(agent)
        plan = await the_whole_plan(agent, made["plan"])
        sorted_in = await a_sorted_project(agent)
        mixed = (
            await ok(
                agent,
                "propose_placement",
                items=[sorted_in["loose_mail"], sorted_in["loose_login"]],
                parent=sorted_in["billing"],
                reason="Both about people.",
            )
        )["id"]
        await ok(
            agent,
            "propose_placement",
            items=[sorted_in["loose_pay"]],
            parent=sorted_in["billing"],
            reason="About money.",
        )
        waiting = (await ok(agent, "list_placements", status="proposed"))["items"]
        scores = next(p for p in waiting if p["id"] == mixed)["scores"]
    async with connect(project.mcp_url, make_key(project, "coder", code_mode=True)) as coder:
        said = await ok(
            coder,
            "run",
            code=for_checks.replace("plan: 1402", f"plan: {made['plan']}"),
        )
        moves = await ok(coder, "run", code=for_moves)
    non_ok = [c for c in plan["checks"] if c["state"] != "ok"]
    answer = said["result"]
    proof(
        "M50",
        "the example in docs/agents.md for the checks of a plan, run as written in code mode, "
        "reads the plan and says what is not ok: the check for no size names the two tasks and "
        "the one for repeated work names the story and the existing work it looks like, each "
        "exactly as the tool says",
        [(a["check"], a["state"], a["words"]) for a in answer]
        == [(c["kind"], c["state"], c["words"]) for c in non_ok]
        and [a["check"] for a in answer] == ["unsized", "duplicate"]
        and sorted(answer[0]["items"]) == sorted([refs["loose1"], refs["loose2"]])
        and answer[1]["items"] == [refs["dup"]]
        and len(answer[1]["like"]) == 1
        and repeats in answer[1]["like"][0],
        clip(str(answer), 100),
    )
    (result,) = moves["result"]
    proof(
        "M50",
        "the example in docs/agents.md for proposals to look at twice, run as written in code "
        "mode, lists only the proposal that is not routine with its sentence and its lowest "
        "similarity, which is the lowest of the scores the tool gives",
        result["proposal"] == mixed
        and result["why"] == next(p for p in waiting if p["id"] == mixed)["routine_why"]
        and abs(result["lowest"] - min(s["similarity"] for s in scores)) < 1e-9
        and result["weigh"] == [],
        clip(str(result), 100),
    )


async def assist() -> None:
    chapter("Review assistance: checks, scores and what is routine (M50)")
    await the_checks_of_a_plan()
    await the_check_that_cannot_look()
    await checks_only_for_a_plan_that_waits()
    await moves_score_like_suggest_parent()
    await the_examples_in_the_docs()
    # The page (tests/acceptance_assist_page.py) is imported where it is run, since it builds on
    # this file.
    from tests.acceptance_assist_page import assist_on_the_page  # noqa: PLC0415

    await assist_on_the_page()
