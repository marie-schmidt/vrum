"""Chapter of the acceptance scenario for sorting work (M43): where work belongs. A real MCP client
asks `suggest_parent` where loose stories and tasks should go, the steward script proposes the
moves it is sure about with a key that cannot move anything, and a person accepts or rejects each
proposal through the board's own calls. The scripted embedding service of the stack stands in for
the model: its words stand for concepts, so the answers are the same every run. The measurement
itself (scripts/parent_accuracy.py) needs a real embedding service and is only shown to refuse
without one. Each part works in a project of its own."""

import os
import subprocess
import sys
from typing import Any

from tests.acceptance_scenario import chapter, clip, proof
from tests.helpers import REPO_ROOT, connect, export_everything, ok, refused
from tests.test_placement_integration import a_sorted_project
from tests.world import Person, Project, make_limited_key, make_project, register

STEWARD_TOOLS = ["list_nodes", "suggest_parent", "list_placements", "propose_placement"]


def words(refusal: str) -> str:
    return refusal.split(": ", 1)[1] if refusal.startswith("Error executing tool") else refusal


def script(
    name: str, key: str, *arguments: str, **environment: str
) -> subprocess.CompletedProcess[str]:
    """A script of this checkout, run from the repository with the agent key in the environment
    variable it reads (and any other setting given)."""
    settings = {k: v for k, v in os.environ.items() if k != "VRUM_AGENT_KEY"}
    settings.update(environment)
    settings["VRUM_AGENT_KEY"] = key
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / name), *arguments],
        cwd=REPO_ROOT,
        env=settings,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def decide(project: Project, who: Person, placement: int, accepted: bool, **more: Any) -> Any:
    """A person's decision, as the board sends it: the tool, as the person signed in."""
    return who.call(
        "POST",
        f"/board/api/projects/{project.id}/tool/decide_placement",
        {"placement": placement, "accepted": accepted, **more},
    )


async def events_in(client: Any) -> int:
    return int((await ok(client, "server_status"))["counts"]["events"])


async def suggestions() -> None:
    """suggest_parent: a read that says where work belongs, with the reason and whether it is
    sure, and never offers the Inbox."""
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        refs = await a_sorted_project(client)
        before = await events_in(client)
        found = await ok(client, "suggest_parent", node=refs["loose_pay"])
        mail = await ok(client, "suggest_parent", node=refs["loose_mail"], limit=1)
        login = await ok(client, "suggest_parent", node=refs["loose_login"])
        top = found["suggestions"][0]
        under = {refs["billing1"], refs["billing2"], refs["billing3"]}
        reasons = [one["ref"] for one in top["because"]]
        reason_parents = [(await ok(client, "get_node", node=ref))["parent"] for ref in reasons]
        scores = [s["score"] for s in found["suggestions"]]
        kept_out = refs["inbox"] not in [s["ref"] for s in found["suggestions"]]
        after = await events_in(client)
        proof(
            "M43",
            "suggest_parent sends a loose story to the epic it resembles by meaning, best first, "
            "with a score for each, the look-alike items under that epic that are the reason, "
            "whether the best is clearly ahead of the second (sure) and by how much, where the "
            "story is now, and never the Inbox epic itself; it only reads, so the history did not "
            "grow",
            found["kind"] == "story"
            and found["parent_kind"] == "epic"
            and found["current_parent"] == refs["inbox"]
            and found["inbox"] == refs["inbox"]
            and kept_out
            and (top["ref"], top["title"]) == (refs["billing"], "Billing")
            and found["sure"] is True
            and found["margin"] >= 0.05
            and scores == sorted(scores, reverse=True)
            and 1 <= len(reasons) <= 2
            and all(r in under or p in under for r, p in zip(reasons, reason_parents, strict=True))
            and [s["ref"] for s in mail["suggestions"]] == [refs["mail"]]
            and login["suggestions"][0]["ref"] == refs["access"]
            and after == before,
            f"{top['title']} {top['score']:.2f}, {found['margin']:.2f} ahead",
        )
        task = await ok(client, "suggest_parent", node=refs["loose_task"], limit=5)
        fresh = await ok(
            client, "suggest_parent", title="Alert users by email", kind="story", limit=3
        )
        offered = {s["ref"] for s in task["suggestions"]}
        proof(
            "M43",
            "a task is sent to a story and not to an epic, the stories that are themselves in "
            "the Inbox are never offered, and work that is not saved yet can be asked about by "
            "its title and kind",
            task["kind"] == "task"
            and task["parent_kind"] == "story"
            and task["suggestions"][0]["ref"] == refs["billing2"]
            and not offered & {refs["loose_pay"], refs["loose_mail"], refs["loose_story"]}
            and fresh["node"] is None
            and fresh["suggestions"][0]["ref"] == refs["mail"],
            f"the task goes under {task['suggestions'][0]['title']}",
        )
        said = [
            words(await refused(client, "suggest_parent", node=refs["billing"])),
            words(await refused(client, "suggest_parent")),
            words(await refused(client, "suggest_parent", title="No kind")),
            words(await refused(client, "suggest_parent", node=refs["billing1"], limit=11)),
            words(await refused(client, "suggest_parent", node="story:99999")),
        ]
        proof(
            "M43",
            "suggest_parent says in plain words what it cannot do: an epic has no parent to "
            "suggest, it needs a node or a title with a kind, and a limit or a story that is not "
            "there",
            "is an epic" in said[0]
            and "Give either node" in said[1]
            and "Say kind" in said[2]
            and "limit must be from 1 to 10" in said[3]
            and "no story 99999" in said[4],
            clip(said[0]),
        )


async def the_steward() -> None:
    """The steward: it proposes only where the model is sure, in as many proposals as it is told
    to, with a key that cannot move anything, and everything waits for a person."""
    owner = register()
    project = make_project(owner)
    key = make_limited_key(project, "steward", STEWARD_TOOLS)
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
    dry = script("steward.py", key, project.mcp_url, "--dry-run", "--min-score", "0.1")
    async with connect(project.mcp_url, key) as steward:
        nothing_yet = (await ok(steward, "list_placements"))["total"] == 0
    proof(
        "M43",
        "the steward script, run with --dry-run, says which loose stories it would propose "
        "moving and where (only the ones the model is sure about, one proposal for each epic) "
        "and which it leaves for a person, and changes nothing",
        dry.returncode == 0
        and dry.stdout.count("would propose") == 3
        and "would make 3 proposals" in dry.stdout
        and "left 1 stories" in dry.stdout
        and nothing_yet,
        clip(dry.stdout.splitlines()[-1]),
    )
    first = script("steward.py", key, project.mcp_url, "--min-score", "0.1", "--max-proposals", "1")
    rest = script("steward.py", key, project.mcp_url, "--min-score", "0.1")
    again = script("steward.py", key, project.mcp_url, "--min-score", "0.1")
    async with connect(project.mcp_url, key) as steward:
        waiting = (await ok(steward, "list_placements", status="proposed"))["items"]
        where = {item["ref"]: one["parent"] for one in waiting for item in one["items"]}
        tried = {
            tool: words(await refused(steward, tool, **arguments))
            for tool, arguments in (
                (
                    "move_node",
                    {
                        "node": refs["loose_pay"],
                        "new_parent": refs["billing"],
                        "expected_version": 1,
                    },
                ),
                ("decide_placement", {"placement": waiting[0]["id"], "accepted": True}),
                ("set_confirmation", {"note_id": 1, "confirmation": "confirmed"}),
            )
        }
    async with connect(project.mcp_url, project.key) as agent:
        parents = [
            (await ok(agent, "get_node", node=refs[name]))["parent"]
            for name in ("loose_pay", "loose_mail", "loose_login")
        ]
        unmoved = parents == [refs["inbox"]] * 3
        needs = await ok(agent, "needs_a_person")
    group = next(g for g in needs["groups"] if g["title"] == "Suggested moves")
    proof(
        "M43",
        "the steward stops at the number of proposals it was given and says which wait for the "
        "next run, the next run makes the rest and a run after that leaves what is waiting "
        "alone; each proposal says why in the steward's own words; its key may only list, "
        "suggest and propose, so it is refused moving, deciding and confirming; nothing has "
        "moved; and every waiting proposal is one row of the Needs a person list, in a group "
        "called Suggested moves, with no story",
        first.returncode == 0
        and "made 1 proposals" in first.stdout
        and "stopped at 1 proposals" in first.stdout
        and rest.returncode == 0
        and "made 2 proposals" in rest.stdout
        and "made 0 proposals" in again.stdout
        and where
        == {
            refs["loose_pay"]: refs["billing"],
            refs["loose_mail"]: refs["mail"],
            refs["loose_login"]: refs["access"],
        }
        and {one["proposed_by"] for one in waiting} == {"steward"}
        and all(one["reason"].startswith("Nearest by meaning to ") for one in waiting)
        and all("not allowed to use" in message for message in tried.values())
        and unmoved
        and group["story"] is None
        and sorted(i["kind"] for i in group["items"]) == ["placement"] * 3
        and needs["total"] == 3,
        f"3 proposals, {clip(waiting[0]['reason'], 60)}",
    )

    # A person decides, on the board's own calls.
    one, two, three = waiting
    async with connect(project.mcp_url, project.key) as agent:
        by_an_agent = words(
            await refused(agent, "decide_placement", placement=one["id"], accepted=True)
        )
        blank = decide(project, owner, two["id"], False)
        no = decide(project, owner, two["id"], False, reason="Keep it in the Inbox for now")
        yes = decide(project, owner, one["id"], True)
        moved_ref = one["items"][0]["ref"]
        kept_ref = two["items"][0]["ref"]
        moved = await ok(agent, "get_node", node=moved_ref)
        kept = await ok(agent, "get_node", node=kept_ref)
        history = (await ok(agent, "node_history", node=moved_ref))["items"]
        move = [e for e in history if e["action"] == "move"][-1]
        twice = decide(project, owner, one["id"], False, reason="Changed my mind")
        left = await ok(agent, "needs_a_person")
    proof(
        "M43",
        "an agent cannot decide a proposal; a person who rejects one must give a reason and then "
        "nothing moves; a person who accepts one moves the work as themselves (the history says "
        "a person made the move, from the Inbox to the new place) and its row is gone; a "
        "decision is final",
        "not allowed to use" in by_an_agent
        and blank.status == 400
        and "needs a reason" in blank.body["message"]
        and no.status == 200
        and (no.body["state"], no.body["decision_reason"])
        == ("rejected", "Keep it in the Inbox for now")
        and kept["parent"] == refs["inbox"]
        and yes.status == 200
        and yes.body["state"] == "accepted"
        and yes.body["applied"] == [moved_ref]
        and moved["parent"] == one["parent"]
        and (move["actor"], move["actor_kind"]) == (owner.name, "person")
        and (move["before"]["parent"], move["after"]["parent"]) == (refs["inbox"], one["parent"])
        and twice.status == 400
        and "already accepted" in twice.body["message"]
        and [i["node"] for g in left["groups"] for i in g["items"]] == [three["items"][0]["ref"]],
        f"{moved_ref} moved by {owner.name}",
    )

    async with connect(project.mcp_url, project.key) as agent:
        file = await export_everything(agent)
    there = register()
    target = make_project(there)
    async with connect(target.mcp_url, target.key) as client:
        await ok(client, "import_graph", document=file)
        by_id = {p["id"]: p for p in (await ok(client, "list_placements", limit=50))["items"]}
        needs_there = await ok(client, "needs_a_person")
        decided_there = decide(target, there, three["id"], False, reason="Not now")
    proof(
        "M43",
        "proposals and the decisions on them travel through an export and an import: the one "
        "accepted is accepted with what it moved, the one rejected is rejected with the reason, "
        "the one waiting is still a row of the Needs a person list, and a person there can "
        "decide it",
        (by_id[one["id"]]["state"], by_id[one["id"]]["applied"]) == ("accepted", [moved_ref])
        and (by_id[two["id"]]["state"], by_id[two["id"]]["decision_reason"])
        == ("rejected", "Keep it in the Inbox for now")
        and by_id[three["id"]]["state"] == "proposed"
        and [i["kind"] for g in needs_there["groups"] for i in g["items"]] == ["placement"]
        and decided_there.status == 200,
        "3 proposals imported",
    )


async def stale_and_new() -> None:
    """A proposal that was made before someone moved or finished the work leaves that work out and
    says so, and a new epic is made only when a person accepts."""
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
        done = decide(project, owner, made["id"], True)
        skipped = {s["ref"]: s["why"] for s in done.body["skipped"]}
        proof(
            "M43",
            "work that was moved by someone else, or finished, since the proposal was made is "
            "left where it is and the person is told why (moved to which epic, or that it is "
            "dropped), and the rest of the proposal is made",
            done.status == 200
            and done.body["applied"] == [refs["loose_pay"]]
            and set(skipped) == {refs["loose_mail"], refs["loose_login"]}
            and "moved to E-" in skipped[refs["loose_mail"]]
            and skipped[refs["loose_login"]] == "it is dropped"
            and (await ok(agent, "get_node", node=refs["loose_mail"]))["parent"] == refs["mail"],
            f"left out: {clip('; '.join(skipped.values()), 60)}",
        )
        epics = (await ok(agent, "list_nodes", kind="epic", limit=50))["total"]
        grouped = await ok(
            agent,
            "propose_placement",
            items=[refs["loose_story"]],
            new_epic={"title": "Receipts", "body": "Everything about what a customer gets."},
            reason="It is about what the customer receives.",
        )
        waiting_epics = (await ok(agent, "list_nodes", kind="epic", limit=50))["total"]
        accepted = decide(project, owner, grouped["id"], True)
        epic = accepted.body["epic"]
        made_epic = await ok(agent, "get_node", node=epic)
        created = [
            e
            for e in (await ok(agent, "node_history", node=epic))["items"]
            if e["action"] == "create"
        ]
        second = await ok(
            agent,
            "propose_placement",
            items=[refs["billing3"]],
            new_epic={"title": "Never made"},
            reason="Perhaps.",
        )
        turned_down = decide(project, owner, second["id"], False, reason="Not now")
        titles = [
            n["title"] for n in (await ok(agent, "list_nodes", kind="epic", limit=50))["items"]
        ]
        proof(
            "M43",
            "a proposal to group stories under a new epic makes no epic while it waits, a "
            "person's yes makes the epic as themselves, with the title and details the proposal "
            "gave, and moves the stories under it, and a no makes no epic at all",
            grouped["new_epic"]["title"] == "Receipts"
            and waiting_epics == epics
            and accepted.status == 200
            and made_epic["title"] == "Receipts"
            and made_epic["body"].startswith("Everything about")
            and created[0]["actor_kind"] == "person"
            and (await ok(agent, "get_node", node=refs["loose_story"]))["parent"] == epic
            and turned_down.status == 200
            and "Never made" not in titles
            and (await ok(agent, "get_node", node=refs["billing3"]))["parent"] == refs["billing"],
            f"{epic} made by {created[0]['actor']}",
        )


async def the_measurement() -> None:
    """The measurement needs a real embedding service: it refuses, and says so, without one."""
    key = "vrk_not-needed-it-reads-nothing-from-the-server"
    unreachable = script(
        "parent_accuracy.py",
        key,
        "--demo",
        EMBEDDING_BASE_URL="http://127.0.0.1:9/v1",
        EMBEDDING_MODEL="all-MiniLM-L6-v2",
    )
    environment = {k: v for k, v in os.environ.items() if not k.startswith("EMBEDDING_")}
    unset = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "parent_accuracy.py"), "--demo"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    proof(
        "M43",
        "the measurement of how well meaning finds where work belongs (parent_accuracy.py) "
        "refuses without an embedding service and says so in words, whether none is set or the "
        "one that is set cannot be reached, and prints no numbers it did not measure",
        unreachable.returncode == 1
        and "cannot be reached" in unreachable.stderr
        and unset.returncode == 2
        and "Say which embedding service to use" in unset.stderr
        and "first" not in unreachable.stdout + unset.stdout,
        clip(unreachable.stderr.strip()),
    )


async def sorting() -> None:
    chapter("Sorting work: where it belongs, the steward and a person's decision (M43)")
    await suggestions()
    await the_steward()
    await stale_and_new()
    await the_measurement()
