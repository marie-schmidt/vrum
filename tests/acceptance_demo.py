"""Chapter 19 of the acceptance scenario: demo (M20). `./demo.sh` starts a separate copy of Vrum
with a made-up project in it, says where it is and how to sign in, starts again from a fresh
database when it is run again, and `./demo.sh stop` removes its containers, volumes and image and
leaves everything else alone.

The chapter needs nothing from the throwaway stack the other chapters run in: the demo is its own
Docker Compose project and port (named by VRUM_ACCEPTANCE_DEMO_PROJECT and
VRUM_ACCEPTANCE_DEMO_PORT, which ./acceptance.sh sets to the stack's own name with -demo and the
stack's port plus 4). To show that the demo leaves other things alone, it first starts a
bystander of its own (a container, labelled as another Compose project, and a volume) and,
afterwards, looks at them and at the throwaway stack the rest of the walk is using.

It runs `./demo.sh` from this checkout as a person would, reads the address and the sign-in from
what it prints, signs in with a real browser, makes an agent key in the New agent key dialog on the
Users page, and connects a real MCP client with that key. On every screen it then compares what the
page shows with what the tools say about the sample data."""

import datetime
import math
import os
import re
import socket
import subprocess
from collections import Counter
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlparse

from mcp import Client
from playwright.async_api import Error as BrowserError
from playwright.async_api import expect

from tests import demo_data
from tests.acceptance_granular_page import (
    PREVIEW_ITEMS,
    disagreements,
    facts_of,
    hovered,
    lines,
    mode_and_view,
    preview_of,
    runs_of,
)
from tests.acceptance_review import (
    CONTEXT_LINES,
    ITEM_LINES,
    lines_of,
    open_review,
    plan_card,
    review_page,
    the_diff_is_before_the_decision,
    whole_plan,
)
from tests.acceptance_revising_page import CHANGED_LINES, REMOVED_LINES, THREADS, failing, holds
from tests.acceptance_scenario import Failed, chapter, clip, http_request, proof
from tests.acceptance_standard_change import (
    each_way,
    line_of,
    lookup_of,
    open_it,
    snapshot,
    wrong_item,
)
from tests.acceptance_standard_page import (
    change_id,
    groups_wrong,
    open_the_routine_group,
    seen_by_the_tools,
    sentences,
    the_list,
)
from tests.board_helpers import NOTE_STATUS_WORDS, SLOW, Watched, browser_page, go
from tests.board_m14 import events_of, every_item, ids_of, open_working, show_tab
from tests.demo_flow import Flow, in_order
from tests.demo_world import (
    Names,
    asks_of_description,
    expected_gate_rows,
    expected_needs,
    kept_of_description,
    pull_request_number,
)
from tests.helpers import (
    REPO_ROOT,
    all_nodes,
    code_of,
    connect,
    day,
    export_everything,
    ok,
    refused,
)
from tests.world import api
from vrum.checks import pull_request_address

DEMO_EMAIL = "demo@example.com"
SCRIPT = REPO_ROOT / "demo.sh"
THE_ADDRESS = re.compile(r"Open the board:\s+(\S+)")
THE_SIGN_IN = re.compile(r"Sign in with:\s+(\S+)\s+and password\s+(\S+)")
# What a verifier says it saw at a pull request, in the one fixed form scripts/verify_pr.py writes.
SEEN_ON_GITHUB = re.compile(
    r"https://github\.com/example-org/bike-share/pull/\d+ merged "
    r"\d{4}-\d\d-\d\dT\d\d:\d\d:00Z, merge commit [0-9a-f]{10}, checks passed (\d+) of \1"
)


def words(text: str) -> str:
    """What a person reads: any run of spaces and line breaks is one space."""
    return " ".join(text.split())


async def visit(w: Watched, address: str) -> None:
    """Load a page of the demo's board from nothing, as a bookmark does, and wait until it is
    drawn."""
    await go(w.page, w.board, address)


async def read_the_whole_feed(page: Any) -> None:
    """Press Show older changes until there are no older ones, as a person does: each press waits
    for the page it asked for (pressing again before it arrives asks for the same page twice)."""
    rows = page.locator("#feed li.event")
    for _ in range(80):
        older = page.locator("#show-older")
        if not await older.count():
            return
        before = await rows.count()
        await older.click()
        await page.wait_for_function(
            "(n) => document.querySelectorAll('#feed li.event').length > n"
            " || document.getElementById('show-older') === null",
            arg=before,
            timeout=SLOW,
        )


def project_name() -> str:
    return (
        os.environ.get("VRUM_ACCEPTANCE_DEMO_PROJECT")
        or f"{os.environ['COMPOSE_PROJECT_NAME']}-demo"
    )


def port_number() -> int:
    if os.environ.get("VRUM_ACCEPTANCE_DEMO_PORT"):
        return int(os.environ["VRUM_ACCEPTANCE_DEMO_PORT"])
    return (urlparse(os.environ["VRUM_TEST_URL"]).port or 18400) + 4


def demo_sh(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run ./demo.sh as a person would, from the checkout, with the settings of this walk's own
    stack still in the environment (it has to ignore them)."""
    environment = {
        **os.environ,
        "VRUM_DEMO_PROJECT": project_name(),
        "VRUM_DEMO_PORT": str(port_number()),
    }
    return subprocess.run(
        [str(SCRIPT), *arguments],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=1500,
    )


def docker(*arguments: str) -> str:
    """What a Docker command prints (nothing when it fails: a thing that is not there)."""
    done = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, check=False, timeout=180
    )
    return done.stdout.strip()


def things_of(project: str) -> dict[str, str]:
    """Everything Docker holds for one Compose project, and the image it builds."""
    label = f"label=com.docker.compose.project={project}"
    return {
        "containers": docker("ps", "-aq", "--filter", label),
        "volumes": docker("volume", "ls", "-q", "--filter", label),
        "image": docker("images", "-q", f"{project}-app"),
    }


def answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def sign_in_with(printed: str) -> tuple[str, str, str]:
    """The address, the email and the password a person reads in what ./demo.sh prints."""
    address, signing = THE_ADDRESS.search(printed), THE_SIGN_IN.search(printed)
    if not address or not signing:
        raise Failed(
            f"M20: ./demo.sh did not say where the demo is and how to sign in: {printed[-300:]}"
        )
    return address.group(1), signing.group(1), signing.group(2)


async def sign_in_and_open(w: Watched, email: str, password: str) -> None:
    """Sign in on the page a person is given, and open the one project there is."""
    page = w.page
    await w.open("")
    await page.locator("#account-email").fill(email)
    await page.locator("#account-password").fill(password)
    await page.locator("#sign-in-submit").click()
    await expect(page.locator("li.project a.open-project")).to_have_count(1, timeout=SLOW)
    await page.locator("li.project a.open-project").click()
    await expect(page.locator("#main-nav")).to_be_visible(timeout=SLOW)
    w.board = page.url.partition("#")[0]


async def make_a_key(w: Watched) -> str:
    """A key made the way a person makes one: in the New agent key dialog on the Users page, which
    asks how it calls Vrum and what it may do before it makes anything. This one may use every tool,
    since the walk reads things an agent is not offered (who is working, the coverage report) and
    then writes one epic."""
    page = w.page
    await w.go("#/users")
    await page.locator("#key-open").click()
    await page.locator("#key-name").fill("The walk's own agent")
    await page.locator("#key-call-tools").check()
    await page.locator("#key-tools-choice-everything").click()
    await expect(page.locator("#key-tools-choice-everything")).to_have_attribute(
        "aria-checked", "true"
    )
    await page.locator("#create-key").click()
    await expect(page.locator("#key-secret")).to_contain_text("vrk_", timeout=SLOW)
    return (await page.locator("#key-secret").inner_text()).strip()


async def what_the_tools_hold(client: Client) -> dict[str, Any]:
    """The sample project as an agent reads it: every node, acceptance criterion and note, who is
    working, the status counts, and (M42 to M47) the proposals to move work, the plans, the plan
    templates, the links and what is held."""
    nodes = await all_nodes(client)
    return {
        "nodes": nodes,
        "rules": await every_item(client, "list_rules"),
        "notes": await every_item(client, "list_notes"),
        "working": await ok(client, "who_is_working"),
        "status": await ok(client, "server_status"),
        "coverage": await ok(client, "coverage_report", limit=1),
        "overdue": await ids_of(client, "is:overdue"),
        "placements": (await ok(client, "list_placements", limit=200))["items"],
        "plans": (await ok(client, "list_plans", limit=200))["items"],
        "templates": (await ok(client, "list_plan_templates"))["items"],
        "links": (await export_everything(client))["links"],
        "held": await ids_of(client, "is:held"),
    }


def the_sample_has(held: dict[str, Any]) -> dict[str, bool]:
    """Each thing the demo is meant to show, true when the tools say it is there."""
    nodes: list[dict[str, Any]] = held["nodes"]
    epics = [n for n in nodes if n["kind"] == "epic"]
    with_stories = {n["parent"] for n in nodes if n["kind"] == "story"}
    tasks = [n for n in nodes if n["kind"] == "task"]
    far = day(60)
    notes = held["notes"]
    agents = held["working"]["agents"]
    inbox = next((e for e in epics if e["title"] == demo_data.INBOX), None)
    moves = {p["state"] for p in held["placements"]}
    plans = {p["state"] for p in held["plans"]}
    return {
        "epics with stories": any(e["ref"] in with_stories for e in epics),
        "an epic with none": any(e["ref"] not in with_stories for e in epics),
        "tasks in every status": {t["status"] for t in tasks}
        >= {"ready", "in_progress", "blocked", "done", "dropped"},
        "overdue work": len(held["overdue"]) >= 5,
        "far-off dates": any((n["target_on"] or "") >= far for n in nodes),
        "undated work": any(not n["target_on"] and not n["start_on"] for n in tasks),
        "dependencies": any(n["waiting_on"] for n in nodes),
        "held claims": sum(len(a["holding"]) for a in agents) >= 4,
        "a claim that ran out": any(a["lapsed"] for a in agents),
        "a handoff note": any(a["last_handoff"] for a in agents),
        "acceptance criteria placed": any(r["node"] for r in held["rules"]),
        "acceptance criteria not placed": sum(1 for r in held["rules"] if not r["node"]) >= 5,
        "acceptance criteria open, met and dropped": {r["status"] for r in held["rules"]}
        >= {"open", "met", "dropped"},
        "project-wide notes": sum(1 for n in notes if n["project_wide"]) >= 9,
        "linked notes": any(not n["project_wide"] and n["nodes"] for n in notes),
        "notes someone decided": any(n["decided_by"] for n in notes),
        "notes nobody decided": any(not n["decided_by"] for n in notes),
        "confirmed notes": any(n["status"] == "confirmed" for n in notes),
        "unconfirmed notes": any(n["status"] == "unconfirmed" for n in notes),
        "a replaced note": any(n["status"] == "superseded" for n in notes),
        "a withdrawn note": any(n["status"] == "withdrawn" for n in notes),
        "an Inbox with loose stories": inbox is not None
        and sum(1 for n in nodes if n["parent"] == inbox["ref"]) >= 4,
        "proposals to move work, waiting, accepted and rejected": moves
        >= {"proposed", "accepted", "rejected"},
        "work held behind gates": sum(
            1 for n in nodes if any(by.startswith("gate:") for by in n["held_by"])
        )
        >= 3,
        "a plan waiting and a plan approved": plans >= {"proposed", "approved"},
        "a plan that waits for its agent to revise it": "changes_requested" in plans,
        "plan templates": len(held["templates"]) == 2,
        "work found while doing other work": sum(
            1 for link in held["links"] if link["link_type"] == "discovered_from"
        )
        >= 3,
    }


async def every_screen_shows_it(w: Watched, client: Client, held: dict[str, Any]) -> None:
    """Each screen, against the tools: the numbers and the rows are the tools'."""
    page = w.page
    nodes: list[dict[str, Any]] = held["nodes"]
    work = [n for n in nodes if n["kind"] != "epic"]
    counts = held["status"]["counts"]
    differences: list[str] = []

    # The Board: a card for each story and task (Show: Both), by status, and the overdue ones.
    await visit(w, "#/board?show=both")
    await expect(page.locator("article.card").first).to_be_visible(timeout=SLOW)
    await expect(page.locator("article.card")).to_have_count(len(work), timeout=SLOW)
    by_status = await page.locator("article.card").evaluate_all(
        "els => els.reduce((c, e) => ({...c, [e.dataset.status]: (c[e.dataset.status] || 0) + 1}),"
        " {})"
    )
    wanted: dict[str, int] = {}
    for n in work:
        wanted[n["status"]] = wanted.get(n["status"], 0) + 1
    if by_status != wanted:
        differences.append(f"Board by status {by_status} != {wanted}")
    await visit(w, "#/board?show=both&q=is:overdue")
    await expect(page.locator(".board")).to_be_visible(timeout=SLOW)
    overdue_cards = await page.locator("article.card").evaluate_all(
        "els => els.map((e) => e.dataset.ref)"
    )
    if sorted(overdue_cards) != sorted(r for r in held["overdue"] if not r.startswith("epic:")):
        differences.append("the overdue cards")

    # The Graph opens (its Structure map is drawn live, so there is no number to compare), and the
    # Lists carry the counts of what the tools hold: the work, the acceptance criteria, the notes.
    await visit(w, "#/graph")
    await expect(page.locator("#graph-explore")).to_be_visible(timeout=SLOW)
    await visit(w, "#/lists?view=work")
    for name, count in (
        ("work", len(nodes)),
        ("requirements", len(held["rules"])),
        ("notes", len(held["notes"])),
    ):
        shown_count = page.locator(f"#lists-view-{name} #lists-count-{name}")
        await expect(shown_count).to_have_attribute("data-count", str(count), timeout=SLOW)
    in_force = sum(1 for n in held["notes"] if n["status"] in ("unconfirmed", "confirmed"))

    # The Roadmap: what has dates is on the timeline.
    dated = sum(1 for n in nodes if n["span_start"] or n["span_end"])
    await visit(w, "#/roadmap")
    await expect(page.locator("#count")).to_contain_text(
        f"{dated} items on the timeline", timeout=SLOW
    )

    # Activity: who is working on what (the Working view), and the feed (the Changes view).
    agents = held["working"]["agents"]
    live = sum(len(a["holding"]) for a in agents)
    lapsed = sum(len(a["lapsed"]) for a in agents)
    handoffs = [e for e in await events_of(client) if e["action"] == "handoff"]
    await visit(w, "#/activity")
    await open_working(page)
    await expect(page.locator("#agents li.claim[data-live='1']")).to_have_count(live, timeout=SLOW)
    if await page.locator("#agents li.claim[data-live='0']").count() != lapsed:
        differences.append("the claims that ran out")
    await expect(page.locator("#sidebar-activity-count")).to_have_text(str(live))
    await show_tab(page, "changes")
    await read_the_whole_feed(page)
    # A run of check ticks by one person on one item is one row that says how many it holds.
    in_the_feed = await page.locator("#feed li.event").evaluate_all(
        "els => els.reduce((sum, e) => sum + Number(e.dataset.count || 1), 0)"
    )
    if in_the_feed != counts["events"]:
        differences.append(f"the feed has {in_the_feed} changes")
    if await page.locator("#feed li.event .handoff-note").count() != len(handoffs):
        differences.append("the handoff notes")

    # Coverage: the acceptance criteria placed nowhere, and the stories without one.
    await visit(w, "#/coverage")
    summary = page.locator("#coverage-summary")
    await expect(summary).to_have_attribute(
        "data-unplaced", str(held["coverage"]["unplaced_rule_count"]), timeout=SLOW
    )
    if await summary.get_attribute("data-stories-without") != str(
        held["coverage"]["story_without_rules_count"]
    ):
        differences.append("the stories without an acceptance criterion")

    # Status: how much the project holds, in the details behind the fold.
    await visit(w, "#/status")
    await page.locator("details#technical summary").click()
    await expect(page.locator("#sizes")).to_be_visible(timeout=SLOW)
    for name, wanted_count in counts.items():
        cell = page.locator(f"#sizes tr[data-name='{name}'] td.value")
        if await cell.count() and (await cell.inner_text()).strip() != str(wanted_count):
            differences.append(f"status {name}")

    # Search: a word from the data finds what the tool finds.
    word = max(re.findall(r"[A-Za-z]{6,}", work[0]["title"]) or ["bike"], key=len)
    found = await ok(client, "search", query=word, limit=200)
    await visit(w, f"#/search?search={word}")
    await expect(page.locator("#search-count")).to_have_text(
        re.compile(rf"^{found['total']} results?\b"), timeout=SLOW
    )
    proof(
        "M20",
        "in a browser signed in as the demo's account, the Board has a card for each of the "
        f"{len(work)} stories and tasks, by status and overdue as the tools count them, the "
        f"Graph opens, the Lists count the {len(nodes)} items of work, the {len(held['rules'])} "
        f"acceptance criteria and the {len(held['notes'])} notes ({in_force} of them in force), "
        f"the Roadmap has the {dated} dated items, Activity has the {live} claims in force, the "
        f"{lapsed} that ran out, the {len(handoffs)} handoff notes and every one of the "
        f"{counts['events']} changes, Coverage and Status say what the tools say, and Search "
        f"for '{word}' finds {found['total']}",
        not differences,
        "; ".join(differences[:3]) or "every number equal",
    )


async def records_of_every_state(w: Watched, client: Client, held: dict[str, Any]) -> None:
    """A page for each kind of state a note can be in, and an epic with no stories."""
    page = w.page
    notes = held["notes"]
    picks: list[dict[str, Any]] = []
    wanted_states: list[Callable[[dict[str, Any]], bool]] = [
        lambda n: bool(n["project_wide"]) and n["status"] in ("unconfirmed", "confirmed"),
        lambda n: n["status"] == "confirmed" and bool(n["decided_by"]),
        lambda n: n["status"] == "confirmed" and not n["decided_by"],
        lambda n: n["status"] == "unconfirmed",
        lambda n: n["status"] == "superseded",
        lambda n: n["status"] == "withdrawn",
        lambda n: not n["project_wide"] and n["status"] == "unconfirmed" and bool(n["nodes"]),
    ]
    for want in wanted_states:
        picks.append(next(n for n in notes if want(n)))
    wrong: list[str] = []
    for record in picks:
        ref = f"note:{record['id']}"
        await visit(w, f"#/item?item={ref}&from=board")
        await expect(page.locator("#item-page")).to_have_attribute("data-ref", ref, timeout=SLOW)
        shown = words(await page.locator("#item-page").inner_text())
        sure = f"Confirmed by {record['confirmed_by']}" if record["status"] == "confirmed" else ""
        if NOTE_STATUS_WORDS[record["status"]] not in shown or sure not in shown:
            wrong.append(ref)
    # Each epic's page says as many notes as recall does; one has no stories.
    epics = [n for n in held["nodes"] if n["kind"] == "epic"]
    bare = None
    for epic in epics:
        recalled = await ok(client, "recall", node=epic["ref"])
        await visit(w, f"#/item?item={epic['ref']}&from=board")
        await expect(page.locator("#item-page")).to_have_attribute(
            "data-ref", epic["ref"], timeout=SLOW
        )
        counts = await page.locator(".recall-section[data-section]").evaluate_all(
            "els => Object.fromEntries("
            "els.map((e) => [e.dataset.section, Number(e.dataset.count)]))"
        )
        for name in ("notes", "project_wide_notes", "past_notes"):
            if counts.get(name, 0) != recalled[name]["count"]:
                wrong.append(f"{epic['ref']} {name}")
        if not [n for n in held["nodes"] if n["parent"] == epic["ref"]]:
            bare = epic["ref"]
            # With no story under it the epic has no table of stories, and a way to add one.
            await expect(page.locator("#item-table")).to_have_count(0)
            await expect(page.locator("#new-child")).to_have_text("New story")
    proof(
        "M20",
        f"the pages of {len(picks)} notes in every state (unconfirmed, confirmed, project-wide, "
        "decided by someone or not, replaced, withdrawn, linked) say what the tools say, and "
        f"each of the {len(epics)} epics' pages holds as many notes as recall gives it; the "
        "epic with no stories says so",
        not wrong and bare is not None,
        f"{len(picks)} notes, {len(epics)} epics" + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )


async def the_verifier_works(
    base: str, email: str, password: str, client: Client, held: dict[str, Any]
) -> None:
    """M40: the sample has a verifier. The Users page's own API says which key is one, the work
    handed over for a verifier is found by is:unverified, and on the finished work the lines
    tagged for a verifier were ticked by that key and not by the agent that did the work."""
    signed = api(base, "POST", "/board/api/session", {"email": email, "password": password})
    keys = api(
        base, "GET", "/board/api/projects/1/keys", cookie=signed.cookie, csrf=signed.body["csrf"]
    ).body["keys"]
    verifiers = sorted(key["name"] for key in keys if key["verifier"])
    work = [n for n in held["nodes"] if n["kind"] != "epic"]
    # In the sample every blocked piece of work has its verifier checks open, so the filter finds
    # all of it that nobody holds: the work waiting for a verifier and the work blocked on
    # something else. The one task that waits for a person has them ticked, and is not found.
    names = Names.of_description()
    person_titles = {names.node(key).item.title for key in demo_data.WAITING_FOR_A_PERSON}
    blocked = sorted(
        n["ref"]
        for n in work
        if n["status"] == "blocked" and not n["claimed_by"] and n["title"] not in person_titles
    )
    found = await ids_of(client, "is:unverified")
    waiting_titles = {names.node(key).item.title for key in demo_data.WAITING_FOR_VERIFIER}
    waiting = [n for n in work if n["title"] in waiting_titles]
    wrong: list[str] = []
    for node in waiting:
        detail = await ok(client, "get_node", node=node["ref"])
        open_checks = sorted(
            (c["verifier_only"], c["github"], c["person_only"])
            for c in detail["checks"]
            if not c["checked"]
        )
        if (
            detail["status"] != "blocked"
            or detail["claimed_by"] is not None
            or open_checks != [(False, False, True), (True, False, False), (True, True, False)]
            or "ready for a verifier" not in detail["last_handoff"]["note"].lower()
        ):
            wrong.append(node["ref"])
    finished = [n for n in work if n["status"] == "done"]
    for node in finished:
        tagged = [
            c
            for c in (await ok(client, "get_node", node=node["ref"]))["checks"]
            if c["verifier_only"]
        ]
        if len(tagged) != 2 or not all(
            c["checked"]
            and (c["checked_by"], c["checked_by_kind"]) == (demo_data.VERIFIER, "agent")
            and (not c["github"] or SEEN_ON_GITHUB.fullmatch(c["evidence"] or ""))
            and (not c["github"] or pull_request_address(c["evidence"]))
            for c in tagged
        ):
            wrong.append(node["ref"])
    proof(
        "M40",
        f"the sample has a verifier: the Users page's own API says only {demo_data.VERIFIER}'s "
        f"key is a verifier's, is:unverified finds the {len(found)} blocked items nobody holds "
        "that have a check for a verifier open "
        f"(the {len(waiting)} handed over for a verifier among them, each with its own checks "
        "ticked, the three others open and a handoff note that says it is ready), and on each "
        f"of the {len(finished)} finished items the two lines tagged for a verifier were ticked "
        f"by {demo_data.VERIFIER}, an agent, the [github] one with a made-up pull request "
        "address and what was seen in the one fixed form",
        verifiers == [demo_data.VERIFIER]
        and sorted(found) == blocked
        and len(waiting) == len(demo_data.WAITING_FOR_VERIFIER)
        and {n["ref"] for n in waiting} <= set(found)
        and not wrong
        and len(finished) >= 12,
        f"{len(found)} found, {len(waiting)} waiting, {len(finished)} verified"
        + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )


async def the_work_says_where_it_is(
    base: str, email: str, password: str, client: Client, held: dict[str, Any]
) -> None:
    """M41: the sample's claims and handoff notes say where the work is (the made-up repository
    example-org/bike-share), what the tools report, and exactly one key has a last day."""
    names = Names.of_description()
    today = datetime.datetime.now(datetime.UTC).date()
    agents = held["working"]["agents"]
    now = {h["node"]: (a["actor"], h) for a in agents for h in a["holding"]}
    ran_out = {h["node"]: (a["actor"], h) for a in agents for h in a["lapsed"]}
    fields = ("repository", "branch", "commit", "pull_request", "run_label", "client_name")

    def says(entry: dict[str, Any], key: str, actor: str, where: demo_data.Where | None) -> bool:
        """Whether a claim or a handoff note says what the description says of its work."""
        if where is None:
            return not any(entry[field] for field in fields)
        number = (
            pull_request_number(names.node(key).item.title) if where.merged else where.pull_request
        )
        address = None if number is None else demo_data.PULL_REQUEST_ADDRESS.format(number=number)
        label = re.fullmatch(r"run-\d{4}-\d\d-\d\d-(\w)", entry["run_label"] or "")
        return (
            entry["repository"] == demo_data.REPOSITORY_ADDRESS
            and entry["branch"] == where.branch
            and re.fullmatch(r"[0-9a-f]{40}", entry["commit"] or "") is not None
            and entry["pull_request"] == address
            and (label is not None and label.group(1) == where.run if where.run else label is None)
            and entry["client_name"] == (demo_data.CLIENTS[actor] if where.run else None)
        )

    wrong: list[str] = []
    # A person of the team who has no account here is held for by the owner, who signs in for them,
    # so only an agent's claim names the same holder as the description.
    for step in demo_data.HELD:
        actor, entry = now[names.node(step.key).ref]
        named = actor != step.actor and step.actor.endswith(demo_data.AGENT_SUFFIX)
        if named or not says(entry, step.key, actor, step.where):
            wrong.append(f"held {step.key}")
    for step in demo_data.LAPSED:
        actor, entry = ran_out[names.node(step.key).ref]
        named = actor != step.actor and step.actor.endswith(demo_data.AGENT_SUFFIX)
        if named or not says(entry, step.key, actor, step.where):
            wrong.append(f"lapsed {step.key}")
    labels: dict[str, set[str]] = {}
    for actor, entry in now.values():
        if entry["run_label"]:
            labels.setdefault(actor, set()).add(entry["run_label"])
    said = 0
    for note in demo_data.HANDOFFS:
        placed = names.node(note.key)
        detail = await ok(client, "get_node", node=placed.ref)
        if not says(detail["last_handoff"], note.key, note.actor, note.where):
            wrong.append(f"handoff {note.key}")
        if note.where is not None:
            said += 1
        if note.where is not None and note.where.merged:
            tick = next(c for c in detail["checks"] if c["github"])
            if pull_request_address(tick["evidence"]) != detail["last_handoff"]["pull_request"]:
                wrong.append(f"the tick of {note.key} names another pull request")
    signed = api(base, "POST", "/board/api/session", {"email": email, "password": password})
    keys = api(
        base, "GET", "/board/api/projects/1/keys", cookie=signed.cookie, csrf=signed.body["csrf"]
    ).body["keys"]
    ending = [k for k in keys if k["expires_on"] is not None]
    last_day = (today + datetime.timedelta(days=demo_data.KEY_EXPIRES_AFTER_DAYS)).isoformat()
    proof(
        "M41",
        "the sample says where the work is: the claims held now and the two that ran out say a "
        "branch of the made-up repository and, for most, a commit, a pull request, a run label and "
        "the client it ran in, one says nothing, one key holds work under two run labels, every "
        "handoff note of code work says its branch and pull request (on finished work the very "
        "one the verifier's [github] tick names), and exactly one key has a last day, "
        f"{demo_data.KEY_EXPIRES_AFTER_DAYS} days after the day the demo was made, which has not "
        "passed",
        not wrong
        and any(len(found) >= 2 for found in labels.values())
        and any(step.where is None for step in demo_data.HELD)
        and said >= 10
        and [k["name"] for k in ending] == [demo_data.EXPIRING_KEY]
        and ending[0]["expires_on"] == last_day
        and ending[0]["expired"] is False
        and not any(k["expired"] for k in keys),
        f"{len(demo_data.HELD) + len(demo_data.LAPSED)} claims, {said} handoff notes, "
        f"{demo_data.EXPIRING_KEY} until {last_day}" + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )


async def the_sample_review_is_assisted(client: Client, held: dict[str, Any]) -> None:
    """M50: the plan that waits shows the three checks as the tools give them (nothing circular,
    the one task left unsized and nothing else, one story that may repeat the existing "Battery
    swap schedule", at a similarity that reads as a repeat and not as the same words), and the
    suggested moves that wait say which are routine and which a person should look at twice, each
    score equal to what suggest_parent gives for the same item and parent."""
    names = Names.of_description()
    winter = next(p for p in held["plans"] if p["title"] == demo_data.WINTER_TITLE)
    (plan,) = (await ok(client, "list_plans", plan=winter["id"]))["items"]
    checks = {c["kind"]: c for c in plan["checks"]}
    in_the_plan = {item["ref"] for item in plan["tree"]}
    no_size = [
        n["ref"] for n in (await ok(client, "list_nodes", query="no:size", limit=200))["items"]
    ]
    unsized = sorted(ref for ref in no_size if ref in in_the_plan)
    repeats = [item["ref"] for item in plan["tree"] if item["title"] == demo_data.WINTER_REPEATS]
    battery = names.node("battery").ref
    (finding,) = checks["duplicate"]["findings"]
    near = finding["related"][0]
    sized_by_a_guess = [item["ref"] for item in plan["tree"] if item["size"] is None]

    waiting = (await ok(client, "list_placements", status="proposed", limit=50))["items"]
    decided = (await ok(client, "list_placements", status="accepted", limit=50))["items"]
    rows = [
        i
        for g in (await ok(client, "needs_a_person"))["groups"]
        for i in g["items"]
        if i["kind"] == "placement"
    ]
    routine = [p for p in waiting if p["routine"]]
    careful = [p for p in waiting if p["routine"] is False and p["scores"]]
    new_epic = [p for p in waiting if p["new_epic"]]
    wrong: list[str] = []
    for proposal in waiting:
        for score in proposal["scores"]:
            if score["similarity"] is None:
                continue
            asked = await ok(client, "suggest_parent", node=score["ref"], limit=10)
            ranked = {c["ref"]: c for c in asked["suggestions"]}
            mine = ranked.get(proposal["parent"])
            if mine is None or abs(mine["score"] - score["similarity"]) > 1e-9:
                wrong.append(score["ref"])
    proof(
        "M50",
        "the plan that waits for a person is checked as the tools say: no circle of waiting, "
        "the only task with no size (the filter no:size finds it among the plan's items, and "
        "nothing was sized for it) and one story, the repeat of 'Battery swap schedule', that "
        "looks like that existing story by meaning, between the overlap threshold and the same "
        "words, with the existing story named and its status; and the suggested moves that wait "
        "say whether each is routine: at least one is, one the steward made by hand is not and "
        "its lowest score is well below the other's, the move that makes a new epic has no "
        "scores and says why, every score is what suggest_parent gives for that item and parent, "
        "the Needs a person rows carry none of it and a proposal that was decided carries none",
        [c["kind"] for c in plan["checks"]] == ["circle", "unsized", "duplicate"]
        and checks["circle"]["state"] == "ok"
        and checks["unsized"]["state"] == "warning"
        and unsized == sorted(sized_by_a_guess)
        and len(unsized) == 1
        and [f["items"] for f in checks["unsized"]["findings"]] == [[unsized[0]]]
        and checks["duplicate"]["state"] == "warning"
        and finding["items"] == repeats
        and near["ref"] == battery
        and near["status"] == "ready"
        and 0.55 <= near["similarity"] < 0.95
        and len(routine) >= 1
        and len(careful) >= 1
        and all(not any(s["warning"] for s in p["scores"]) for p in routine)
        and all(len({s["similarity"] for s in p["scores"]}) > 1 for p in careful)
        and len(new_epic) == 1
        and new_epic[0]["scores"] == []
        and new_epic[0]["scores_note"]
        and new_epic[0]["routine"] is False
        and not wrong
        and len(rows) == len(waiting)
        and all(
            r["placement"]["scores"] is None and r["placement"]["routine"] is None for r in rows
        )
        and all(p["scores"] is None and p["routine"] is None for p in decided),
        f"checks {[c['state'] for c in plan['checks']]}, {len(routine)} routine of "
        f"{len(waiting)} moves, repeat {round(near['similarity'] * 100)}% like {code_of(battery)}"
        + (f"; wrong: {wrong}" if wrong else ""),
    )


async def the_sample_plans_have_a_conversation(client: Client, held: dict[str, Any]) -> None:
    """M51: the winter plan is at revision 2 of the same plan after the owner asked for changes
    and the planning agent revised it, and the spring plan waits for its agent, all as the tools
    and the change feed say it (and as the Needs a person list counts it)."""
    names = Names.of_description()
    nodes = {n["ref"]: n for n in held["nodes"]}
    winter_row = next(p for p in held["plans"] if p["title"] == demo_data.WINTER_TITLE)
    spring_row = next(p for p in held["plans"] if p["title"] == demo_data.SPRING_TITLE)
    (winter,) = (await ok(client, "list_plans", plan=winter_row["id"]))["items"]
    (first,) = (await ok(client, "list_plans", plan=winter_row["id"], revision=1))["items"]
    (spring,) = (await ok(client, "list_plans", plan=spring_row["id"]))["items"]
    waiting = (await ok(client, "list_plans", status="changes_requested"))["items"]
    by_title = {item["title"]: item["ref"] for item in winter["tree"]}
    studded = by_title[demo_data.WINTER_REVISED["studded"]["title"]]
    fitting = by_title["Fit studded tyres on forty bikes"]
    old = {item["ref"]: item for item in first["tree"]}
    drafted = {str(item["key"]): item for item in demo_data.WINTER_ITEMS}
    chains = next(r for r, item in old.items() if item["title"] == drafted["chains"]["title"])
    approved_refs = in_order(by_title[drafted[key]["title"]] for key in demo_data.WINTER_APPROVED)
    (given,) = winter["feedback"]
    changes = {c["ref"]: c for c in winter["changes"]}
    lines = {line["ref"]: line for line in winter["blame"]}
    flow = Flow(cast(Any, None))
    flow.winter_refs = {
        key: next(r for r, item in old.items() if item["title"] == one["title"])
        for key, one in drafted.items()
    }
    try:
        flow.check_the_blame(winter, first, given)
        blame_problem = ""
    except SystemExit as stopped:
        blame_problem = str(stopped)
    needs = await ok(client, "needs_a_person")
    plan_rows = [i for g in needs["groups"] for i in g["items"] if i["kind"] == "plan"]
    by_owner = (await ok(client, "changes_since", actor=demo_data.OWNER, newest=True, limit=100))[
        "items"
    ]
    by_agent = (
        await ok(client, "changes_since", actor=demo_data.RECORDER, newest=True, limit=100)
    )["items"]
    asked = [e for e in by_owner if e["action"] == "plan_changes_requested"]
    sent = [e for e in by_agent if e["action"] == "plan_revised"]
    (waiting_for_agent,) = spring["feedback"]
    spring_story = next(i for i in spring["tree"] if i["kind"] == "story")
    spring_task = next(i for i in spring["tree"] if i["title"].startswith("Replace"))
    proof(
        "M51",
        "the sample has a plan with a conversation: the winter plan is at revision 2 of the same "
        "plan, made by the planning agent with its note after the owner asked for changes to "
        "revision 1 with a summary and a comment on a story's title, on a task's details and on "
        "a whole task she wants dropped, each keeping the words it was about; revision 2 changes "
        "the two parts (the old words are in before) and takes out the task, which is dropped, "
        "revision 1 still reads as it was drafted, nothing was changed "
        "outside a revision, and its seven items are held; it is one row on Needs a person and "
        "that row says revision 2; the change feed has the owner's request (as a person) and the "
        "agent's revision; and the spring plan waits for its agent at revision 1 with the "
        "owner's feedback and its three items held, and is in no Needs a person row",
        (winter["state"], winter["revision"], winter["revised_by"], winter["revised_by_kind"])
        == ("proposed", 2, demo_data.RECORDER, "agent")
        and winter["proposed_by"] == demo_data.RECORDER
        and [r["revision"] for r in winter["revisions"]] == [1, 2]
        and winter["revisions"][1]["note"] == demo_data.WINTER_REVISION_NOTE
        and winter["revisions"][0]["feedback"] == given["id"]
        and (given["by"], given["by_kind"], given["revision"], given["summary"])
        == (demo_data.OWNER, "person", 1, demo_data.WINTER_FEEDBACK_SUMMARY)
        and [(c["field"], c["text"]) for c in given["comments"]]
        == [(part, text) for _, part, text in demo_data.WINTER_FEEDBACK]
        and [c["item"] for c in given["comments"]] == [studded, fitting, chains]
        and [c["quoted"] for c in given["comments"]]
        == [old[studded]["title"], old[fitting]["body"], old[chains]["title"]]
        and old[studded]["title"] != demo_data.WINTER_REVISED["studded"]["title"]
        and set(changes) == {studded, fitting, chains}
        and changes[studded]["fields"] == ["title"]
        and changes[fitting]["fields"] == ["body"]
        and changes[chains]["change"] == "removed"
        and changes[studded]["before"] == {"title": old[studded]["title"]}
        and changes[fitting]["before"] == {"body": old[fitting]["body"]}
        and {c["change"] for c in winter["changes"]} == {"changed", "removed"}
        and chains not in {item["ref"] for item in winter["tree"]}
        and nodes[chains]["status"] == "dropped"
        and not nodes[chains]["held_by"]
        and winter["drift"] == []
        and first["shown"] == 1
        and first["changes"] is None
        and all(nodes[i["ref"]]["held_by"] == [f"plan:{winter['id']}"] for i in winter["tree"])
        and len(plan_rows) == 1
        and plan_rows[0]["plan"]["id"] == winter["id"]
        and plan_rows[0]["plan"]["revision"] == 2
        and [(e["actor"], e["actor_kind"]) for e in asked] == [(demo_data.OWNER, "person")] * 2
        and {e["after"]["plan"] for e in asked} == {winter["id"], spring["id"]}
        and [e["after"]["revision"] for e in sent] == [2]
        and sent[0]["after"]["plan"] == winter["id"]
        and [(p["id"], p["state"]) for p in waiting] == [(spring["id"], "changes_requested")]
        and (spring["state"], spring["revision"], spring["checks"])
        == ("changes_requested", 1, None)
        and (waiting_for_agent["by"], waiting_for_agent["by_kind"]) == (demo_data.OWNER, "person")
        and [(c["field"], c["text"]) for c in waiting_for_agent["comments"]]
        == [(part, text) for _, part, text in demo_data.SPRING_FEEDBACK]
        and [c["quoted"] for c in waiting_for_agent["comments"]]
        == [spring_story["body"], spring_task["size"]]
        and len(spring["tree"]) == 3
        and all(nodes[i["ref"]]["held_by"] == [f"plan:{spring['id']}"] for i in spring["tree"])
        and spring["id"] not in [p["plan"]["id"] for p in plan_rows]
        and names.node(demo_data.WINTER_UNDER).ref == spring["context"][0]["ref"],
        f"winter at revision {winter['revision']} after {len(given['comments'])} comments, "
        f"{len(changes)} changed; spring waits for its agent",
    )
    gone_line = lines[chains]
    proof(
        "M53",
        "the winter plan shows a removal and what the owner approved, line by line: she approved "
        "four items as they were in revision 1 (the feedback and the change feed carry them, "
        "none of them is commented on and none was changed), revision 2 left them alone, so "
        "blame says each was added in revision 1 by the planning agent and is approved by her on "
        "revision 1; the two lines she commented on that revision 2 changed read changed, with "
        "the revision and the agent's note; the task she asked to drop is removed by revision 2 "
        "(its line is in blame with the note and no word of hers counts), the story nobody said "
        "anything about reads none, and revision 1, read as it was, has every line added and the "
        "three she commented on asked; every line of both revisions is in blame in the order the "
        "server lists items",
        given["approved"] == approved_refs
        and len(approved_refs) == 4
        and not set(approved_refs) & {c["item"] for c in given["comments"]}
        and not set(approved_refs) & set(changes)
        and [e["after"]["approved"] for e in asked if e["after"]["plan"] == winter["id"]]
        == [approved_refs]
        and [e["after"]["approved"] for e in asked if e["after"]["plan"] == spring["id"]] == [[]]
        and waiting_for_agent["approved"] == []
        and [line["ref"] for line in winter["blame"]] == in_order(old)
        and [line["ref"] for line in first["blame"]] == in_order(old)
        and all(
            (lines[r]["change"], lines[r]["revision"], lines[r]["by"], lines[r]["by_kind"])
            == ("added", 1, demo_data.RECORDER, "agent")
            and (
                lines[r]["review"]["state"],
                lines[r]["review"]["by"],
                lines[r]["review"]["revision"],
                lines[r]["review"]["feedback"],
            )
            == ("approved", demo_data.OWNER, 1, given["id"])
            for r in approved_refs
        )
        and all(
            (lines[r]["change"], lines[r]["revision"], lines[r]["note"])
            == ("changed", 2, demo_data.WINTER_REVISION_NOTE)
            and lines[r]["review"]["state"] == "changed"
            and lines[r]["review"]["by"] == demo_data.OWNER
            for r in (studded, fitting)
        )
        and (gone_line["change"], gone_line["revision"], gone_line["note"])
        == ("removed", 2, demo_data.WINTER_REVISION_NOTE)
        and gone_line["review"]["state"] == "none"
        and [
            lines[r]["review"]["state"]
            for r in old
            if r not in {*approved_refs, studded, fitting, chains}
            and old[r]["title"] == demo_data.WINTER_REPEATS
        ]
        == ["none"]
        and {line["ref"]: line["review"]["state"] for line in first["blame"]}
        == {
            r: "approved"
            if r in approved_refs
            else "asked"
            if r in (studded, fitting, chains)
            else "none"
            for r in old
        }
        and all(line["change"] == "added" and line["revision"] == 1 for line in first["blame"])
        and not blame_problem,
        f"{len(approved_refs)} approved and unchanged, 2 changed, 1 removed{blame_problem}",
    )


async def the_sample_rows_say_what_they_are(client: Client, held: dict[str, Any]) -> None:
    """M52: every row of what needs a person in the sample says its level, its decision and its
    topic as the server works them out, which the board only regroups by (only relations are
    held, because which rows the real model puts together is the model's own), and the one answer
    a person kept as a decision is a note by that person on the work."""

    class Reads:
        """What the loader's check of the standard review reads, through this client."""

        async def call(self, tool: str, actor: str | None = None, **arguments: Any) -> Any:
            return await ok(client, tool, **arguments)

    needs = await ok(client, "needs_a_person")
    rows = [item for group in needs["groups"] for item in group["items"]]
    try:
        said = await Flow(cast(Any, Reads())).read_the_standard_review(needs)
        problem = ""
    except SystemExit as stopped:
        said, problem = "", str(stopped)
    by_decision = Counter(row["decision"] for row in rows)
    by_level = Counter(row["level"] for row in rows)
    names = Names.of_description()
    ((key, ask),) = kept_of_description()
    ref = names.node(key).ref
    questions = [n for n in held["notes"] if n["text"].startswith(f"Q: {ask.question}")]
    (kept,) = questions
    recalled = await ok(client, "recall", node=ref)
    in_the_briefing = [n["note"]["id"] for n in recalled["notes"]["items"]]
    statuses = Counter(n["status"] for n in held["notes"])
    proof(
        "M52",
        "every row of what needs a person in the sample says what it is: its level (one plan, "
        "the notes at the level note, the rest the story or task they are about), its decision "
        "(four to answer, two to look into, the rest to approve) and a topic that is one thing "
        "wherever it is said, is an epic by its reference and title or a group the real model "
        "put together; and the answer a person kept as a decision (the planner's, to who may "
        "refund a ride) is a note by that person with the question and the answer as its text "
        "and her comment as why, linked to the work, unconfirmed, in its briefing and in no row "
        "of what needs a person",
        not problem
        and by_decision == Counter(approve=11, answer=4, look_into=2)
        and by_level["plan"] == 1
        and by_level["note"] == 6
        and by_level["story"] + by_level["task"] == 10
        and len(rows) == needs["total"] == 17
        and kept["text"] == f"Q: {ask.question}\nA: {ask.answer}"
        and (kept["decided_by"], kept["recorded_by"], kept["recorded_by_kind"])
        == (ask.by, ask.by, "person")
        and kept["status"] == "unconfirmed"
        and kept["why"] == ask.comment
        and kept["nodes"] == [ref]
        and kept["id"] in in_the_briefing
        and not any(row["node"] == ref for row in rows)
        and len(held["notes"]) == 40
        and sum(1 for n in held["notes"] if n["decided_by"]) == 21
        and statuses["unconfirmed"] == 21,
        (problem or said) + f"; note {kept['id']} kept by {ask.by}",
    )


async def the_needs_page_shows_it(w: Watched, client: Client) -> None:
    """M45: the page of what needs a person lists exactly what the tool says, with the answers on
    offer as buttons in the bar of each open ask, the sidebar says how many, and the page of a task
    whose ask was answered says the answer in full, with nothing to press. (Since M52 the rows are
    grouped by level, which the standard chapter proves; here the rows are the tool's, in the
    groups' order, with the moves the server calls routine in their closed group.)"""
    page = w.page
    needs = await ok(client, "needs_a_person")
    waiting = (await ok(client, "list_placements", status="proposed", limit=50))["items"]
    later = [
        p["id"]
        for p in (await ok(client, "list_plans", status="changes_requested", limit=50))["items"]
    ]
    seen = await seen_by_the_tools(client, later=later)
    wanted = sorted(change_id(row) for row in seen.rows)
    await the_list(w, seen)
    rows = sorted(
        await page.locator("li.needs-row").evaluate_all("els => els.map(e => e.dataset.change)")
    )
    count = page.locator("#sidebar-needs-count")
    await expect(count).to_have_text(str(needs["total"]), timeout=SLOW)
    labelled = await count.get_attribute("aria-label")
    wrong: list[str] = []
    for item in (i for g in needs["groups"] for i in g["items"] if i["kind"] == "ask"):
        row = page.locator(f"li.needs-row[data-change='ask:{item['ask']['id']}']")
        offered = await row.locator("button.verdict").evaluate_all(
            "els => els.map(e => e.dataset.answer ?? e.dataset.verb)"
        )
        if item["ask"]["kind"] == "approval":
            wanted_buttons = ["yes", "no"]
            shown_buttons = [{"approve": "yes", "reject": "no"}.get(b, b) for b in offered]
        elif item["ask"]["choices"]:
            wanted_buttons = [c.lower() for c in item["ask"]["choices"]]
            shown_buttons = [b.lower() for b in offered]
        else:
            wanted_buttons, shown_buttons = ["answer"], offered
        if [b.lower() for b in shown_buttons] != wanted_buttons:
            wrong.append(f"the answers on offer of Q-{item['ask']['id']}")
        if item["ask"]["question"] not in await row.inner_text():
            wrong.append(f"the question of Q-{item['ask']['id']}")
    # A task whose ask was answered: the answer and the comment in full, and no button.
    answered = next(
        (
            found
            for found in (await ok(client, "list_asks", status="answered", limit=50))["items"]
            if found["comment"]
        ),
        None,
    )
    assert answered is not None
    await visit(w, f"#/item?item={answered['node']}&from=board")
    entry = page.locator(f"li.ask-item[data-ask='{answered['id']}']")
    await expect(entry).to_have_count(1, timeout=SLOW)
    shown = words(await entry.inner_text())
    if (
        answered["answer"] not in shown
        or answered["comment"] not in shown
        or answered["answered_by"] not in shown
        or await entry.locator("button").count()
    ):
        wrong.append("the answered ask on its task's page")
    proof(
        "M45",
        "in a browser, the page of what needs a person lists the rows the tool lists (the "
        "moves the server calls routine in a group of their own), each open ask with its "
        "answers on offer as buttons in its bar (nothing chosen for anyone) and its question, "
        "the sidebar says how many are waiting, and the page of a task whose ask was "
        "answered has the answer, who gave it and the comment in full, with nothing to press",
        rows == wanted
        and len(rows) == needs["total"]
        and labelled == f"{needs['total']} waiting for a person"
        and len(waiting) >= 1
        and not wrong,
        f"{needs['total']} rows" + (f"; wrong: {wrong}" if wrong else ""),
    )


async def the_waiting_plan_is_a_diff(w: Watched, client: Client) -> None:
    """M49: the plan that waits for a person is drawn on its review page as the whole of what it
    adds, above its decision; on the list it is a compact card."""
    page = w.page
    needs = await ok(client, "needs_a_person")
    row = next(i for g in needs["groups"] for i in g["items"] if i["kind"] == "plan")
    (plan,) = (await ok(client, "list_plans", plan=row["plan"]["id"]))["items"]
    await open_review(w, f"plan:{plan['id']}")
    scope = review_page(page)
    # The plan is at revision 2, which the page draws as what changed: Whole plan is every line.
    await whole_plan(page)
    shown = await lines_of(scope, ITEM_LINES, len(plan["tree"]))
    quiet = await lines_of(scope, CONTEXT_LINES, len(plan["context"]))
    written = words(await scope.locator(".change-diff").inner_text())
    criteria = await scope.locator(".change-diff .diff-line[data-part='criteria']").count()
    hunks = words(await scope.locator(".diff-hunk").first.inner_text())
    proof(
        "M49",
        "in a browser the plan that waits for a person is drawn on its review page as every "
        "line it adds, the 3 stories and 4 tasks in the order list_plans reads them, with the "
        "details and the acceptance criteria of each, the epic it hangs under and the story "
        "it relates to as quiet context lines, and the bar comes after the last line (the "
        "sentence about what approving does is its hover card: replaced by M52)",
        shown == [item["ref"] for item in plan["tree"]]
        and quiet == [found["ref"] for found in plan["context"]]
        and all(item["title"] in written for item in plan["tree"])
        and all(item["body"].splitlines()[0] in written for item in plan["tree"] if item["body"])
        and criteria == len(demo_data.WINTER_CRITERIA)
        and all(title in written for _, title in demo_data.WINTER_CRITERIA)
        and "@@" not in hunks
        and await the_diff_is_before_the_decision(scope),
        f"{len(shown)} added lines, {len(quiet)} context lines, {clip(hunks, 50)}",
    )


async def the_waiting_plans_have_a_conversation_on_the_page(w: Watched, client: Client) -> None:
    """M51: on the page, the winter plan says Revision 2 with its agent's note, draws what changed
    since revision 1 (the title of a story and the details of a task) with the owner's two comments
    as threads under those lines, and says what the owner asked; the spring plan, which waits for
    its agent, is a line in a closed group and in no row, and both plans read on their review
    page, every revision as it was."""
    page = w.page
    plans = {p["title"]: p for p in (await ok(client, "list_plans", limit=200))["items"]}
    winter_id, spring_id = (
        plans[demo_data.WINTER_TITLE]["id"],
        plans[demo_data.SPRING_TITLE]["id"],
    )
    (winter,) = (await ok(client, "list_plans", plan=winter_id))["items"]
    (first,) = (await ok(client, "list_plans", plan=winter_id, revision=1))["items"]
    (spring,) = (await ok(client, "list_plans", plan=spring_id))["items"]
    needs = await ok(client, "needs_a_person")
    by_title = {item["title"]: item["ref"] for item in winter["tree"]}
    studded = by_title[demo_data.WINTER_REVISED["studded"]["title"]]
    fitting = by_title["Fit studded tyres on forty bikes"]
    chains = next(
        item["ref"]
        for item in first["tree"]
        if item["title"] == "Buy spare studded chains for staff bikes"
    )
    await visit(w, "#/needs")
    card = page.locator(f"li.needs-row[data-change='plan:{winter_id}']")
    await expect(card).to_have_count(1, timeout=SLOW)
    await expect(card.locator("[data-fact='revision']")).to_have_text("Revision 2", timeout=SLOW)
    waiting = page.locator(
        f"section.needs-group[data-group='agent'] li.waiting-row[data-plan='{spring_id}']"
    )
    await expect(waiting).to_have_count(1, timeout=SLOW)
    line = words(await waiting.text_content() or "")
    spring_rows = await page.locator(f"li.needs-row[data-change='plan:{spring_id}']").count()
    badge = (await page.locator("#sidebar-needs-count").inner_text()).strip()
    link = await waiting.locator("a[href*='review']").count()
    await open_review(w, f"plan:{winter_id}")
    scope = review_page(page)
    await expect(scope.locator("[data-fact='revision']")).to_have_text("Revision 2", timeout=SLOW)
    await expect(scope.locator(CHANGED_LINES)).to_have_count(2, timeout=SLOW)
    await expect(scope.locator(REMOVED_LINES)).to_have_count(1, timeout=SLOW)
    changed = await lines_of(scope, CHANGED_LINES)
    removed = await lines_of(scope, REMOVED_LINES)
    note = words(await scope.locator("p.plan-revision-note").last.inner_text())
    header = words(await scope.locator(".diff-hunk").first.inner_text()).lower()
    threads = await scope.locator(".diff-thread").evaluate_all(THREADS)
    under = {ref: words(text) for _, ref, _, text in threads}
    feedback = words(await scope.locator("section.change-feedback").inner_text())
    options = await page.locator("select#review-revision option").all_inner_texts()
    winter_writes = await scope.locator("button.verdict").count()
    await open_review(w, f"plan:{spring_id}")
    scope = review_page(page)
    await expect(page.locator("h1.review-title")).to_contain_text(
        demo_data.SPRING_TITLE, timeout=SLOW
    )
    spring_state = words(await scope.locator(".plan-state").inner_text())
    spring_page = words(await scope.text_content() or "")
    spring_threads = await scope.locator(".diff-thread").count()
    spring_writes = await scope.locator("button.verdict, button.decision-comment").count()
    proof(
        "M51",
        "in a browser the winter plan's card says Revision 2 and its review page what its "
        "agent said changed, draws what changed since revision 1 (the title of a story and the "
        "details of a task, as `~` lines, and the task the owner asked to drop, as a red `-` "
        "line, so the hunk row says 2 changed and 1 removed) with the owner's three comments "
        "as threads under those lines (the one on the dropped task under its red line) and the "
        "summary of the feedback in full, and has both revisions to read and the three "
        "verdicts; the spring plan, which "
        "waits for its agent, is a line in the closed group of plans that wait for their agent "
        "(linking to its review page) and in no row, and the number on the sidebar is still "
        "the tool's; its review page reads that it waits for the agent to revise it, with the "
        "owner's comments as threads and no button to decide or comment (replaced by M52: the "
        "plan's own page and the one line under the summary are gone)",
        holds(
            sorted(changed) == sorted([studded, fitting]),
            removed == [chains],
            demo_data.WINTER_REVISION_NOTE in note,
            "revision 2" in header and "2 changed" in header and "1 removed" in header,
            sorted(under) == sorted([studded, fitting, chains]),
            all(
                text in under[ref]
                for ref, text in (
                    (studded, demo_data.WINTER_FEEDBACK[0][2]),
                    (fitting, demo_data.WINTER_FEEDBACK[1][2]),
                    (chains, demo_data.WINTER_FEEDBACK[2][2]),
                )
            ),
            demo_data.WINTER_FEEDBACK_SUMMARY in feedback and "3 comments" in feedback,
            f"Plan {spring_id}" in line and demo_data.SPRING_TITLE in line,
            link >= 1 and spring_rows == 0,
            badge == str(needs["total"]),
            "Waiting for the agent" in spring_state,
            demo_data.SPRING_FEEDBACK_SUMMARY in spring_page and spring_threads == 2,
            spring_writes == 0,
            len(options) == 2 and "current" in options[1],
            winter_writes == 3,
            spring["state"] == "changes_requested",
        ),
        f"winter {header}; {clip(line, 70)}{failing()}",
    )


async def the_waiting_review_is_assisted_on_the_page(w: Watched, client: Client) -> None:
    """M50: on the page, the winter plan says what its checks found, beside the items they are
    about, and the suggested moves are scored, the routine ones kept in a closed group of their
    own and the ones to look at twice showing how low they score."""
    page = w.page
    waiting = (await ok(client, "list_placements", status="proposed", limit=50))["items"]
    (winter,) = (await ok(client, "list_plans", status="proposed"))["items"]
    plan = (await ok(client, "list_plans", plan=winter["id"]))["items"][0]
    await open_review(w, f"plan:{plan['id']}")
    scope = review_page(page)
    await expect(scope.locator("li.check-line").first).to_be_visible(timeout=SLOW)
    await whole_plan(page)
    lines = await scope.locator("li.check-line").evaluate_all(
        "els => els.map(e => [e.dataset.check, e.dataset.state])"
    )
    flagged = await scope.locator(".change-diff .diff-line:has(.diff-flag)").evaluate_all(
        "els => els.map(e => [e.dataset.ref, [...e.querySelectorAll('.diff-flag')]"
        ".map(f => f.dataset.check)])"
    )
    near = plan["checks"][2]["findings"][0]["related"][0]
    related = await scope.locator(".check-related-item").evaluate_all(
        "els => els.map(e => e.dataset.ref)"
    )
    said = words(await scope.locator("section[data-part='checks']").inner_text())
    unsized = [i["ref"] for i in plan["tree"] if i["size"] is None]
    repeats = [i["ref"] for i in plan["tree"] if i["title"] == demo_data.WINTER_REPEATS]
    in_the_head = words(await scope.locator("[data-fact='warnings']").inner_text())

    routine = sorted(p["id"] for p in waiting if p["routine"])
    careful = [p for p in waiting if not p["routine"] and p["scores"]]
    await visit(w, "#/needs")
    group = page.locator("section.needs-group[data-group='routine']")
    await expect(group).to_have_count(1, timeout=SLOW)
    head = group.locator("summary.routine-head")
    counted = await head.get_attribute("data-count")
    closed = await group.locator("details.routine-group").get_attribute("open") is None
    before = [
        await page.locator(f"li.needs-row[data-placement='{n}']").first.is_visible()
        for n in routine
    ]
    await head.click()
    after = [
        await page.locator(f"li.needs-row[data-placement='{n}']").first.is_visible()
        for n in routine
    ]
    wrong: list[str] = []
    for one in careful:
        await open_review(w, f"placement:{one['id']}")
        move = review_page(page)
        for score in one["scores"]:
            chip = move.locator(f".diff-score[data-ref='{score['ref']}']")
            if words(await chip.inner_text()) != f"{math.floor(score['similarity'] * 100)}%":
                wrong.append(score["ref"])
        lowest = min(one["scores"], key=lambda s: s["similarity"])
        if await move.locator(f".diff-lowest[data-ref='{lowest['ref']}']").count() != 1:
            wrong.append(f"lowest of {one['id']}")
    proof(
        "M50",
        "on the page the winter plan shows its checks above the diff (no circle; the one task "
        "with no size; the story that may repeat 'Battery swap schedule', naming it), a flag on "
        "the line of the task and the story they are about, and its warnings as a fact; the "
        "suggested moves are scored as the server scores them on their review pages, the "
        "routine one is in a closed group of its own on the list with its count and opens to a "
        "card, and the one the steward made for a second look shows both scores and marks the "
        "lower",
        lines == [["unsized", "warning"], ["duplicate", "warning"]]
        and sorted(f[0] for f in flagged) == sorted([*unsized, *repeats])
        and related == [near["ref"]]
        and plan["checks"][2]["findings"][0]["words"] in said
        and "2" in in_the_head
        and len(routine) >= 1
        and closed
        and not any(before)
        and all(after)
        and counted == str(len(routine))
        and careful
        and not wrong,
        f"{len(routine)} routine of {len(waiting)} moves, {len(careful)} to look at twice, "
        f"flags on {[f[0] for f in flagged]}" + (f"; wrong: {wrong}" if wrong else ""),
    )


async def the_demo_lines_open_what_the_tools_say(w: Watched, client: Client) -> None:
    """M52: pressing a green, red or changed line of the sample's plans and moves opens the dialog
    the tools describe, with the sample's real titles and details: the winter plan at revision 2
    (the changed, added and removed items, old and new) and the plan that waits for its agent, the
    moves the steward proposed (old and new parent), read only, and no sentence in any of them."""
    page = w.page
    plans = {p["title"]: p for p in (await ok(client, "list_plans", limit=200))["items"]}
    winter_id = plans[demo_data.WINTER_TITLE]["id"]
    (winter,) = (await ok(client, "list_plans", plan=winter_id))["items"]
    (first,) = (await ok(client, "list_plans", plan=winter_id, revision=1))["items"]
    lookup = lookup_of(winter, first)
    # An item the revision took out reads as revision 1 had it, so its parent is named as that
    # revision named it (the story's title was changed by revision 2).
    lookup_as_was = lookup_of(first, winter)
    by_ref = {one["ref"]: one for one in winter["tree"]}
    gone = {one["ref"]: one for one in first["tree"]}
    wrong: list[str] = []
    opened = 0
    await open_review(w, f"plan:{winter_id}")
    scope = review_page(page)
    for entry in winter["changes"]:
        ref = entry["ref"]
        mode, sign = {
            "changed": ("changed", "changed"),
            "added": ("added", "+"),
            "removed": ("removed", "-"),
        }[entry["change"]]
        seen, trouble = await each_way(page, line_of(scope, sign, ref), ("click", "enter"))
        wrong += trouble
        for snap in seen:
            opened += 1
            item = gone[ref] if mode == "removed" else by_ref[ref]
            wrong += [
                f"{ref} {mode}: {found}"
                for found in wrong_item(
                    snap,
                    mode,
                    item,
                    lookup_as_was if mode == "removed" else lookup,
                    entry if mode == "changed" else None,
                )
            ]
    moves = (await ok(client, "list_placements", status="proposed", limit=50))["items"]
    for move in moves[:2]:
        await open_review(w, f"placement:{move['id']}")
        one = move["items"][0]
        node = await ok(client, "get_node", node=one["ref"])
        titles = {
            ref: (await ok(client, "get_node", node=ref))["title"]
            for ref in (node["parent"], move["parent"])
            if ref
        }
        await open_it(page, line_of(review_page(page), "+", one["ref"]), "click")
        snap = await snapshot(page)
        await page.keyboard.press("Escape")
        opened += 1
        parent = {f[0]: f for f in snap["fields"]}.get("parent")
        if snap["mode"] != "moved" or parent is None or parent[1] != "changed":
            wrong.append(f"{one['ref']} opens {snap['mode']}")
        elif move["parent"] and not (
            titles[node["parent"]] in parent[2][0][1] and titles[move["parent"]] in parent[2][1][1]
        ):
            wrong.append(f"{one['ref']}: the parents are not {node['parent']} and {move['parent']}")
        if snap["said"] or snap["buttons"] != ["Close"]:
            wrong.append(f"{one['ref']}: {snap['buttons']} {snap['said']}")
    proof(
        "M52",
        "pressing a changed, added or removed line of the sample's winter plan (revision 2) and "
        "the first story of two of the sample's suggested moves, with the mouse and with Enter, "
        "opens the dialog the tools describe, with the sample's real titles and details (the "
        "parts that changed old and new, every other part plain; a moved story's parent old "
        "and new), read only, with no sentence of the board's own in it",
        not wrong and opened >= len(winter["changes"]) * 2 + 2,
        f"{opened} dialogs" + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )


async def the_demo_list_is_grouped_and_every_page_is_plain(
    w: Watched, client: Client, held: dict[str, Any]
) -> None:
    """M52: the groups of each Group by choice are the server's level, decision and topic of the
    sample's rows (relations only: which rows the real model puts together is the model's own),
    every row opens its review page and no text of six words or more that is not somebody's own
    words, the server's refusal or a hover card is on the list, any page or the plan's, and the
    answer kept as a decision is on its work's own page."""
    page = w.page
    plans = {p["title"]: p for p in (await ok(client, "list_plans", limit=200))["items"]}
    later = [plans[demo_data.SPRING_TITLE]["id"]]
    seen = await seen_by_the_tools(client, later=later)
    wrong, details = await groups_wrong(w, seen)
    found: list[str] = []
    for mode in ("level", "topic", "decision"):
        await the_list(w, seen, mode)
        await open_the_routine_group(page)
        found += await sentences(page)
    opened = 0
    for row in seen.rows:
        await open_review(w, change_id(row))
        found += await sentences(page)
        found += [text for text in [await page.evaluate("document.body.innerText")] if "@@" in text]
        opened += 1
    for number in later:
        await open_review(w, f"plan:{number}")
        found += await sentences(page)
        opened += 1
    ((key, ask),) = kept_of_description()
    ref = Names.of_description().node(key).ref
    await visit(w, f"#/item?item={ref}&from=board")
    notes = words(await page.locator("#item-page").inner_text())
    proof(
        "M52",
        "the sample's list is grouped as the server says in every Group by choice (levels, "
        "decisions and topics, with counts that add up, the routine moves and the waiting "
        "plan apart), every one of its rows and the plan that waits for its agent opens its "
        "review page, none of those pages or the list in any grouping has a sentence on it "
        "that is not somebody's own words, a refusal or a hover card, and the answer the "
        "planner kept as a decision is a note on the work's own page, with the question, the "
        "answer and her name",
        not wrong
        and not found
        and opened == len(seen.rows) + len(later)
        and f"Q: {ask.question}" in notes
        and f"A: {ask.answer}" in notes
        and (ask.by or "") in notes,
        ", ".join(details)
        + f"; {opened} pages"
        + (f"; wrong: {wrong[:2]}" if wrong else "")
        + (f"; sentences: {clip(str(found), 120)}" if found else ""),
    )


async def the_sample_asks_a_person(base: str, email: str, password: str, client: Client) -> None:
    """M45: the sample has asks put by agents and answered by people, and what only a person can
    settle, as the tools and the board's own calls say it. (M47 adds the questions that gates
    raise, which are asks of their own with the effect gates, and rows for gates, a drafted plan
    and proposals to move work, which are in a group with no story.)"""
    names = Names.of_description()
    wanted = asks_of_description()
    every = (await ok(client, "list_asks", limit=200))["items"]
    listed = [found for found in every if found["effect"] != "gates"]
    gate_asks = [found for found in every if found["effect"] == "gates"]
    open_now = (await ok(client, "list_asks", status="open", limit=200))["items"]
    waiting = [found for found in open_now if found["effect"] != "gates"]
    by_question = {found["question"]: found for found in listed}
    wrong: list[str] = []
    for key, ask in wanted:
        found = by_question.get(ask.question)
        if found is None:
            wrong.append(f"{key} has no ask")
            continue
        detail = await ok(client, "get_node", node=names.node(key).ref)
        choices = ["yes", "no"] if ask.approval else (list(ask.choices) or None)
        blocked = detail["status"] == "blocked" and detail["claimed_by"] is None
        if not (
            found["node"] == names.node(key).ref
            and found["kind"] == ("approval" if ask.approval else "question")
            and found["choices"] == choices
            and found["asked_by_kind"] == "agent"
            and found["asked_by"].endswith(demo_data.AGENT_SUFFIX)
            and found["answered"] == (ask.answer is not None)
            and (found["answered_by"], found["answer"], found["comment"])
            == (ask.by, ask.answer, ask.comment)
            and found["id"] in [a["id"] for a in detail["asks"]]
            and blocked == (found["effect"] == "blocks" and not found["answered"])
        ):
            wrong.append(f"the ask about {key}")
    effects = Counter(found["effect"] for found in listed)

    # The rows the description gives, those the gates add, and the plan and proposals as the
    # lists of plans and proposals say them (the first item of each, in a group with no story).
    plans_waiting = (await ok(client, "list_plans", status="proposed", limit=50))["items"]
    moves_waiting = (await ok(client, "list_placements", status="proposed", limit=50))["items"]
    expected = sorted(
        [
            *expected_needs(),
            *expected_gate_rows(),
            *(("plan", plan["items"][0], "") for plan in plans_waiting),
            *(("placement", move["items"][0]["ref"], "") for move in moves_waiting),
        ]
    )
    needs = await ok(client, "needs_a_person")
    signed = api(base, "POST", "/board/api/session", {"email": email, "password": password})
    on_board = api(
        base,
        "POST",
        "/board/api/projects/1/tool/needs_a_person",
        {},
        cookie=signed.cookie,
        csrf=signed.body["csrf"],
    )

    def rows_of(answer: dict[str, Any]) -> list[tuple[str, str, str]]:
        return sorted(
            (item["kind"], item["node"], group["story"] or "")
            for group in answer["groups"]
            for item in group["items"]
        )

    stories = {group["story"] for group in needs["groups"] if group["story"]}
    kinds = Counter(item["kind"] for group in needs["groups"] for item in group["items"])
    tom = api(
        base, "POST", "/board/api/session", {"email": demo_data.VIEWER_EMAIL, "password": password}
    )
    tried = api(
        base,
        "POST",
        "/board/api/projects/1/tool/answer_ask",
        {"ask": waiting[0]["id"], "answer": (waiting[0]["choices"] or ["Go ahead"])[0]},
        cookie=tom.cookie,
        csrf=tom.body["csrf"],
    )
    after = (await ok(client, "list_asks", status="open", limit=200))["total"]
    offered = {tool.name for tool in (await client.list_tools()).tools}
    by_an_agent = await refused(client, "answer_ask", ask=waiting[0]["id"], answer="yes")
    proof(
        "M45",
        f"the sample asks a person: {len(wanted)} asks put by agents with finish_work and "
        f"update_node, {len(waiting)} waiting (two questions with choices that block work and an "
        f"approval that parks it) and {len(listed) - len(waiting)} answered by the owner and the "
        "planner as themselves (a question, an approval and one with a comment), each as "
        "described, the blocked work blocked and held by nobody and the answered work free again; "
        f"what needs a person lists {needs['total']} things (an ask, work finished but for a check "
        "only a person ticks, notes waiting for confirmation on work in progress, a claim that "
        "ran out twice, an approval a gate waits for, a refused gate, a plan waiting for "
        "approval and the proposals to move work that wait), under "
        f"{len(stories)} stories and two groups with no story, the same from the tool and from "
        "the board's own call; the viewer is refused when answering, and a key is not offered "
        "answer_ask and is refused it",
        not wrong
        and len(listed) == len(wanted) == 6
        and len(waiting) == 3
        and effects == Counter(blocks=4, parks=2)
        and len(gate_asks) == 3
        and rows_of(needs) == expected == rows_of(on_board.body)
        and needs["total"] == len(expected) == on_board.body["total"]
        and needs["left_out"] == 0
        and 16 <= needs["total"] <= 20
        and 6 <= len(stories) <= 9
        and set(kinds) == {"ask", "check", "note", "lapse", "gate", "plan", "placement"}
        and kinds["plan"] == 1
        and tried.status in (400, 403)
        and after == len(open_now) == len(waiting) + 1
        and "answer_ask" not in offered
        and ("person" in by_an_agent or "answer_ask" in by_an_agent),
        f"{len(listed)} asks, {needs['total']} rows under {len(stories)} stories"
        + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )


async def the_sample_sorts_and_holds(
    base: str, email: str, password: str, client: Client, held: dict[str, Any]
) -> None:
    """M42 to M47: the sample has loose stories in an Inbox and a steward's proposals (one accepted
    by the planner, one rejected by the owner with a reason, the rest waiting, one of them
    grouping two stories under a new epic), gates holding work, a plan waiting for approval and
    one made from a template and approved, two plan templates, and links that say where work was
    found, as the tools and the board's own calls say it."""
    names = Names.of_description()
    nodes = {n["ref"]: n for n in held["nodes"]}
    inbox = next(n for n in held["nodes"] if n["kind"] == "epic" and n["title"] == demo_data.INBOX)
    loose = [n for n in held["nodes"] if n["parent"] == inbox["ref"]]
    moves = {p["id"]: p for p in held["placements"]}
    by_state: dict[str, list[dict[str, Any]]] = {}
    for move in held["placements"]:
        by_state.setdefault(move["state"], []).append(move)
    wrong: list[str] = []
    decided_ones = by_state.get("accepted", [])
    story_moves = [p for p in decided_ones if p["items"][0]["ref"].startswith("story:")]
    moves_in_part = [p for p in decided_ones if p["skipped"]]
    (rejected,) = by_state.get("rejected", [None])
    grouped = [p for p in by_state.get("proposed", []) if p["new_epic"]]
    if (
        len(story_moves) != 1
        or len(moves_in_part) != 1
        or len(decided_ones) != 2
        or rejected is None
        or len(grouped) != 1
    ):
        raise Failed(
            "M43: the sample does not hold one accepted move of a story, one accepted in part, "
            "one rejected and one new epic"
        )
    (accepted,) = story_moves
    (in_part,) = moves_in_part
    moved_story = accepted["applied"][0]
    history = (await ok(client, "node_history", node=moved_story))["items"]
    move = [e for e in history if e["action"] == "move"][-1]
    steward_made = {p["proposed_by"] for p in moves.values()}
    api_keys = api(base, "POST", "/board/api/session", {"email": email, "password": password})
    keys = api(
        base,
        "GET",
        "/board/api/projects/1/keys",
        cookie=api_keys.cookie,
        csrf=api_keys.body["csrf"],
    ).body["keys"]
    steward = next((k for k in keys if k["name"] == demo_data.STEWARD), None)
    if not (
        steward
        and sorted(steward["tools"]) == sorted(demo_data.STEWARD_TOOLS)
        and steward["revoked_at"] is None
    ):
        wrong.append("the steward's key")
    proof(
        "M43",
        f"the sample has {len(loose) + len(accepted['applied'])} loose stories filed in an epic "
        f"called {demo_data.INBOX} by {demo_data.INTAKE}, and the steward (a key that may use "
        "four tools and no others) proposed where they belong, using what the server said: one "
        f"proposal was accepted by {demo_data.PLANNER}, who moved the story as herself, one was "
        "rejected by the owner with a reason and the rest wait, one of them grouping two "
        f"stories under a new epic, {demo_data.GROUPED.epic_title}, which does not exist until a "
        "person accepts",
        steward_made == {demo_data.STEWARD}
        and not wrong
        and accepted["decided_by"] == demo_data.PLANNER
        and rejected["decided_by"] == demo_data.OWNER
        and rejected["decision_reason"] == demo_data.SUGGESTION_REJECTED
        and (move["actor"], move["actor_kind"]) == (demo_data.PLANNER, "person")
        and nodes[moved_story]["parent"] != inbox["ref"]
        and grouped[0]["new_epic"]["title"] == demo_data.GROUPED.epic_title
        and not any(n["title"] == demo_data.GROUPED.epic_title for n in held["nodes"])
        and {n["title"] for n in loose} >= set(demo_data.GROUPED.stories),
        f"{len(held['placements'])} proposals: {', '.join(sorted(by_state))}",
    )

    stays, moved = (
        [one["ref"] for one in in_part["skipped"]],
        in_part["applied"],
    )
    workshop = names.node(demo_data.PARTIAL_PARENT).ref
    kept_ref = names.node(demo_data.PARTIAL_LEFT_IN).ref
    moved_history = [
        e
        for e in (await ok(client, "node_history", node=moved[0]))["items"]
        if e["action"] == "move"
    ]
    stayed_history = [
        e
        for e in (await ok(client, "node_history", node=stays[0]))["items"]
        if e["action"] == "move"
    ]
    decided_in_part = [
        e
        for e in (
            await ok(client, "changes_since", actor=demo_data.PLANNER, newest=True, limit=100)
        )["items"]
        if e["action"] == "placement_accepted" and e["after"]["placement"] == in_part["id"]
    ]
    proof(
        "M53",
        "the sample has a suggested move that a person accepted in part: the steward proposed "
        f"moving two tasks of the story Spare parts stock under {workshop}, and "
        f"{demo_data.PLANNER} accepted it naming only one, so that one moved (as herself) and the "
        "other stayed where it was and the record says she left it out, in words that name her "
        "and nothing else as the reason, which the change feed carries too",
        in_part["state"] == "accepted"
        and in_part["decided_by"] == demo_data.PLANNER
        and in_part["proposed_by"] == demo_data.STEWARD
        and [one["ref"] for one in in_part["items"]]
        == [names.node(key).ref for key in demo_data.PARTIAL_ITEMS]
        and moved
        == [
            names.node(key).ref
            for key in demo_data.PARTIAL_ITEMS
            if key != demo_data.PARTIAL_LEFT_IN
        ]
        and stays == [kept_ref]
        and in_part["skipped"] == [{"ref": kept_ref, "why": f"{demo_data.PLANNER} left it out"}]
        and nodes[moved[0]]["parent"] == workshop
        and nodes[kept_ref]["parent"] != workshop
        and [(e["actor"], e["actor_kind"]) for e in moved_history]
        == [(demo_data.PLANNER, "person")]
        and not stayed_history
        and len(decided_in_part) == 1
        and [(one["ref"], one["why"]) for one in decided_in_part[0]["after"]["skipped"]]
        == [(kept_ref, f"{demo_data.PLANNER} left it out")],
        f"{moved[0]} moved, {stays[0]} left out",
    )

    gates_seen: dict[str, Any] = {}
    for gate in demo_data.GATES:
        node = await ok(client, "get_node", node=names.node(gate.key).ref)
        gates_seen[gate.key] = node
    held_refs = set(held["held"])
    states = {
        key: (node["gates"][0]["state"], bool(node["held_by"]), node["status"])
        for key, node in gates_seen.items()
    }
    wanted_states = {
        "social-signin": ("waiting", True, "new"),
        "delete-account": ("waiting", True, "new"),
        "insurance-text": ("refused", True, "new"),
        "reserve-api": ("open", False, "ready"),
    }
    plans = {p["title"]: p for p in held["plans"]}
    winter = plans[demo_data.WINTER_TITLE]
    spring = plans[demo_data.SPRING_TITLE]
    launch = plans[demo_data.LAUNCH_TITLE]
    winter_nodes = [nodes[ref] for ref in winter["items"]]
    spring_nodes = [nodes[ref] for ref in spring["items"]]
    templates = {t["name"]: t for t in held["templates"]}
    next_up = await ok(client, "ready_work", limit=200)
    free = {item["ref"] for item in next_up["items"]}
    gated = {names.node(g.key).ref for g in demo_data.GATES if g.key != "reserve-api"}
    proof(
        "M47",
        "the sample has gates holding work: a timer, an approval nobody has answered and an "
        "approval a person refused (the work is held, so ready_work leaves it out), and one "
        "approved (open, the work free); a drafted plan of 3 stories and 4 tasks (under an epic "
        "that already exists) waits "
        "for a person (every item held by the plan, none ready) and a plan made from a template "
        "was approved by the planner, who is named; the two templates are there; a small plan "
        "of one story and two tasks is held too while it waits for its agent to revise it; and "
        "is:held finds exactly the gated work and the items of the two drafted plans",
        states == wanted_states
        and (winter["epics"], winter["stories"], winter["tasks"]) == (0, 3, 4)
        and all(n["held_by"] == [f"plan:{winter['id']}"] for n in winter_nodes)
        and winter["state"] == "proposed"
        and (spring["epics"], spring["stories"], spring["tasks"]) == (0, 1, 2)
        and all(n["held_by"] == [f"plan:{spring['id']}"] for n in spring_nodes)
        and spring["state"] == "changes_requested"
        and launch["state"] == "approved"
        and launch["decided_by"] == demo_data.PLANNER
        and sorted(templates) == sorted(demo_data.TEMPLATES)
        and held_refs == gated | {n["ref"] for n in winter_nodes} | {n["ref"] for n in spring_nodes}
        and not free & held_refs
        and names.node("reserve-api").ref in free,
        f"{len(held_refs)} items held",
    )

    pairs = {
        (names.node(link.source).ref, names.node(link.target).ref)
        for link in demo_data.LINKS
        if link.kind == demo_data.DISCOVERED
    }
    seen = {
        (link["source"], link["target"])
        for link in held["links"]
        if link["link_type"] == "discovered_from"
    }
    proof(
        "M44",
        f"{len(pairs)} tasks say where they were found with discovered_from and none of them "
        "waits for the task it was found while doing (each is as ready as before, with nothing "
        "in waiting_on beyond what it needed first)",
        seen == pairs
        and all(
            not any(
                target == names.node(link.target).ref
                for target in nodes[names.node(link.source).ref]["waiting_on"]
            )
            for link in demo_data.LINKS
            if link.kind == demo_data.DISCOVERED
        ),
        f"{len(pairs)} links",
    )


async def the_sample_plan_is_worth_reviewing(client: Client, held: dict[str, Any]) -> None:
    """M49: the plan that waits for a person reads whole (list_plans with its number), as the
    description gives it: under an epic that exists, with a story it relates to as context, sizes
    on all but one item, a high priority, details, dates, links and acceptance criteria, each as
    get_node, list_links and list_rules say."""
    names = Names.of_description()
    nodes = {n["ref"]: n for n in held["nodes"]}
    winter = next(p for p in held["plans"] if p["title"] == demo_data.WINTER_TITLE)
    (plan,) = (await ok(client, "list_plans", plan=winter["id"]))["items"]
    tree = plan["tree"]
    under = names.node(demo_data.WINTER_UNDER).ref
    reached = [
        names.node(target).ref
        for _, target, kind in demo_data.WINTER_LINKS
        if kind != "needs_first"
    ]
    # The plan as it reads now, at revision 2: the draft with the two parts the owner asked about
    # in their new words.
    now = demo_data.WINTER_NOW
    items = {item["key"]: item for item in now}
    order = [
        item["title"]
        for story in now
        if story["kind"] == "story"
        for item in [story, *(i for i in now if i.get("parent") == story["key"])]
    ]
    wrong: list[str] = []
    for item in tree:
        node = await ok(client, "get_node", node=item["ref"])
        for field in ("kind", "parent", "title", "body", "priority", "size"):
            if item[field] != node[field]:
                wrong.append(f"{item['ref']} {field}")
        seen = sorted(
            (link["link_type"], link["target"])
            for link in held["links"]
            if link["source"] == item["ref"]
        )
        if sorted((link["link_type"], link["target"]) for link in item["links"]) != seen:
            wrong.append(f"{item['ref']} links")
        rules = sorted(
            rule["title"]
            for rule in held["rules"]
            if rule["node"] == item["ref"] and rule["status"] != "dropped"
        )
        if sorted(found["title"] for found in item["criteria"]) != rules:
            wrong.append(f"{item['ref']} criteria")
    dated = [item for item in tree if item["start_on"] or item["target_on"]]
    proof(
        "M49",
        "the plan that waits for a person reads whole: its 3 stories and 4 tasks hang under an "
        f"epic that exists ({demo_data.WINTER_UNDER}) and relate to a story that exists, as "
        "context, in the order of the description, every size is set but one (left unsized on "
        "purpose), one story has a high priority and dates, the stories and two tasks have "
        "details, the stories and one task have an acceptance criterion, and every title, body, "
        "size, priority, link and criterion is what get_node, list_links and list_rules say",
        not wrong
        and [item["title"] for item in tree] == order
        and len(tree) == len(demo_data.WINTER_NOW) == 7
        and [(found["ref"], found["why"]) for found in plan["context"]]
        == [(under, "parent"), *((ref, "linked") for ref in reached)]
        and [item["title"] for item in tree if item["size"] is None]
        == [items["fit-covers"]["title"]]
        and [item["title"] for item in tree if item["priority"] == "high"]
        == [items["studded"]["title"]]
        and [item["title"] for item in dated] == [items["studded"]["title"]]
        and all(
            2 <= len(item["body"].splitlines()) <= 3 for item in tree if item["kind"] == "story"
        )
        and sum(1 for item in tree if item["kind"] == "task" and item["body"]) == 2
        and sum(len(item["criteria"]) for item in tree) == len(demo_data.WINTER_CRITERIA) == 4
        and all(item["parent"] == under for item in tree if item["kind"] == "story")
        and all(nodes[item["ref"]]["held_by"] == [f"plan:{plan['id']}"] for item in tree),
        f"{len(tree)} items, {len(plan['context'])} context lines"
        + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )


async def the_demo_shows_a_removal_and_who_approved_what(w: Watched, client: Client) -> None:
    """M53: the winter plan's card previews what changed (the two changed lines and the red line of
    the task the owner asked to drop), its review page says on every line who last changed it
    and what the owner said that still stands (four items approved and carried over, the two she
    commented on changed since, the battery story nobody spoke of), Hide approved folds the
    approved runs, and the Activity page says what the request carried and which item of a
    suggested move the planner left out."""
    page = w.page
    plans = {p["title"]: p for p in (await ok(client, "list_plans", limit=200))["items"]}
    winter_id = plans[demo_data.WINTER_TITLE]["id"]
    (winter,) = (await ok(client, "list_plans", plan=winter_id))["items"]
    (first,) = (await ok(client, "list_plans", plan=winter_id, revision=1))["items"]
    drafted = {str(item["key"]): item for item in demo_data.WINTER_ITEMS}
    by_title = {item["title"]: item["ref"] for item in first["tree"]}
    ref_of = {key: by_title[item["title"]] for key, item in drafted.items()}
    approved = sorted(ref_of[key] for key in demo_data.WINTER_APPROVED)
    changes = {c["ref"]: c["change"] for c in winter["changes"]}
    signs = {"changed": "changed", "added": "+", "removed": "-"}
    wanted = [(signs[changes[ref]], ref) for ref in in_order(changes)]
    moves = (await ok(client, "list_placements", status="accepted", limit=50))["items"]
    (in_part,) = [p for p in moves if p["skipped"]]

    await visit(w, "#/needs")
    card = plan_card(page, winter_id)
    await expect(card.locator(PREVIEW_ITEMS).first).to_be_visible(timeout=SLOW)
    await expect(card.locator(".diff-hunk").first).to_be_visible(timeout=SLOW)
    on_card = await preview_of(card)
    await open_review(w, f"plan:{winter_id}")
    scope = review_page(page)
    await mode_and_view(page, "delta", "unified")
    on_page = await preview_of(scope)
    changes_read = await lines(scope)
    gone_lines = scope.locator(".diff-line[data-sign='-']")
    gone_words = words(" ".join(await gone_lines.all_inner_texts()))
    wrong = disagreements(changes_read, winter, "the demo's changes")
    await mode_and_view(page, "whole", "unified")
    whole_read = await lines(scope)
    wrong += disagreements(whole_read, winter, "the demo's whole plan")
    facts = await facts_of(scope)
    order = [ref for ref, line in whole_read.items()]
    toggle = page.locator("button#review-hide-approved")
    await toggle.click()
    rows = scope.locator(".diff-foldrow[data-fold='approved']")
    runs = runs_of(order, set(approved))
    await expect(rows).to_have_count(len(runs), timeout=SLOW)
    counts = [int(re.search(r"\d+", words(t)).group()) for t in await rows.all_inner_texts()]  # type: ignore[union-attr]
    await toggle.click()
    carried_words = await hovered(
        page, scope.locator(f".line-review[data-ref='{approved[0]}']").first
    )
    await visit(w, "#/activity")
    await expect(page.locator("#feed li.event").first).to_be_visible(timeout=SLOW)
    await read_the_whole_feed(page)
    history = words(await page.locator("#feed").inner_text())
    asked = [
        e
        for e in (await ok(client, "changes_since", newest=True, limit=100))["items"]
        if e["action"] == "plan_changes_requested" and e["after"]["plan"] == winter_id
    ]
    (request,) = asked
    proof(
        "M53",
        "the winter plan shows a removal and who approved what: its card says Revision 2 and "
        "previews what changed (the title and details lines as changed lines and the task the "
        "owner asked to drop as a red line), the same lines in the same order as the review "
        "page's Changes read, where the red line says what the task was (its details, whole); "
        "every line of the review page, in the Changes and the Whole plan read, says what the "
        "server's blame says (the four items she approved read approved by her on revision 1 and "
        "r1, the two she commented on read changed and r2, the story nobody spoke of reads r1 "
        "and nothing else), the progress fact says 4 of 7 approved, Hide approved folds the "
        "approved runs and says how many each holds, the chip of an approval names her and the "
        "revision she read; and the Activity page says her request carried three comments and "
        "four approved items and says the suggested move was accepted with one moved and one "
        "left out, naming the item and who left it out",
        holds(
            on_card["hunk"] == on_page["hunk"],
            on_card["hunk"].startswith("Revision 2"),
            on_page["lines"] == wanted,
            on_card["lines"] == wanted,
            sum(1 for sign, _ in on_card["lines"] if sign == "-") == 1,
            demo_data.WINTER_ITEMS[5]["title"] in gone_words and "Size S" in gone_words,
            not wrong,
            len(whole_read) == len(demo_data.WINTER_NOW) == 7,
            facts.get("review") == "4 of 7 approved",
            runs and counts == runs and sum(counts) == len(approved),
            winter["blame"] and request["after"]["approved"] == approved,
            demo_data.OWNER in carried_words and "revision 1" in carried_words,
            f"asked for changes to revision 1 of Plan {winter_id}" in history,
            "(3 comments, 4 approved)" in history,
            f"accepted Proposal {in_part['id']} (1 moved, 1 left out)" in history,
            f"({demo_data.PLANNER} left it out)" in history,
        ),
        f"{on_card['hunk']}; {len(whole_read)} lines read; {counts} hidden"
        + (f"; wrong: {wrong[:3]}" if wrong else "")
        + failing(),
    )


async def demo() -> None:
    chapter("Demo: ./demo.sh makes a copy with sample data, again from fresh, and stop removes it")
    project, port = project_name(), port_number()
    stack = os.environ["COMPOSE_PROJECT_NAME"]
    if project == stack or "demo" not in project:
        raise Failed(f"M20: the demo's project ({project}) must be its own, and say demo")
    bystander = f"{project}-bystander"
    held_volume = f"{bystander}-data"
    if answers(port):
        raise Failed(f"M20: something already answers on port {port}, which the demo walk needs")
    # A leftover of an earlier walk of this same demo is removed first, and nothing else.
    if any(things_of(project).values()):
        demo_sh("stop")
    try:
        # Something that is not the demo's, and the throwaway stack the walk is in.
        docker("rm", "-f", bystander)
        docker("volume", "rm", "-f", held_volume)
        docker("volume", "create", held_volume)
        started = docker(
            "run", "-d", "--name", bystander, "--label",
            f"com.docker.compose.project={bystander}", "-v", f"{held_volume}:/data",
            "python:3.12-slim", "sleep", "3600",
        )  # fmt: skip
        if not started:
            raise Failed("M20: the bystander container could not be started")
        neighbours = docker(
            "ps", "-aq", "--filter", f"label=com.docker.compose.project={stack}"
        ).split()
        first = demo_sh()
        if first.returncode != 0:
            raise Failed(f"M20: ./demo.sh failed: {clip(first.stderr or first.stdout, 400)}")
        address, email, password = sign_in_with(first.stdout)
        listed = THE_ADDRESS.search(first.stdout)
        in_use = things_of(project)
        proof(
            "M20",
            "./demo.sh from this checkout, with another stack's settings still in its "
            f"environment, starts its own Compose project ({project}) on port {port}, makes "
            "a copy with "
            "containers, volumes and an image of its own, and prints the address and the sign-in "
            "of the demo account (and that it holds sample data only)",
            listed is not None
            and address.endswith(f":{port}/board")
            and email == DEMO_EMAIL
            and len(password) >= 16
            and "sample data only" in first.stdout
            and all(in_use.values())
            and answers(port),
            f"{address}, {len(in_use['containers'].split())} containers",
        )
        async with browser_page(address, token=None, name="Demo visitor") as w:
            await sign_in_and_open(w, email, password)
            key = await make_a_key(w)
            async with connect(f"http://127.0.0.1:{port}/mcp", key) as client:
                held = await what_the_tools_hold(client)
                said = the_sample_has(held)
                missing = [name for name, there in said.items() if not there]
                proof(
                    "M20",
                    "a real MCP client with a key made on the demo's Users page reads "
                    f"sample data with all of it in it: {', '.join(said)}",
                    not missing,
                    f"{held['status']['counts']['epics']} epics, "
                    f"{held['status']['counts']['tasks']} tasks"
                    + (f"; missing: {missing}" if missing else ""),
                )
                await every_screen_shows_it(w, client, held)
                await records_of_every_state(w, client, held)
                await the_verifier_works(f"http://127.0.0.1:{port}", email, password, client, held)
                await the_work_says_where_it_is(
                    f"http://127.0.0.1:{port}", email, password, client, held
                )
                await the_sample_asks_a_person(f"http://127.0.0.1:{port}", email, password, client)
                await the_sample_sorts_and_holds(
                    f"http://127.0.0.1:{port}", email, password, client, held
                )
                await the_sample_plan_is_worth_reviewing(client, held)
                await the_sample_review_is_assisted(client, held)
                await the_sample_plans_have_a_conversation(client, held)
                await the_sample_rows_say_what_they_are(client, held)
                await the_needs_page_shows_it(w, client)
                await the_waiting_plan_is_a_diff(w, client)
                await the_waiting_review_is_assisted_on_the_page(w, client)
                await the_waiting_plans_have_a_conversation_on_the_page(w, client)
                await the_demo_list_is_grouped_and_every_page_is_plain(w, client, held)
                await the_demo_lines_open_what_the_tools_say(w, client)
                await the_demo_shows_a_removal_and_who_approved_what(w, client)
                marker = f"Marker {port}"
                await ok(client, "create_node", kind="epic", title=marker)
                made = [n for n in await all_nodes(client) if n["title"] == marker]
            assert w.problems == [] or all("status of 4" in p for p in w.problems), w.problems

        # Running it again starts from a fresh database: the marker, the password and the key go.
        second = demo_sh()
        if second.returncode != 0:
            raise Failed(
                f"M20: the second ./demo.sh failed: {clip(second.stderr or second.stdout, 400)}"
            )
        address_2, email_2, password_2 = sign_in_with(second.stdout)
        old_password = api(f"http://127.0.0.1:{port}", "POST", "/board/api/session",
                           {"email": email, "password": password})  # fmt: skip
        new_password = api(f"http://127.0.0.1:{port}", "POST", "/board/api/session",
                           {"email": email_2, "password": password_2})  # fmt: skip
        old_key, _ = http_request(f"http://127.0.0.1:{port}/mcp", token=key)
        fresh_epics = 0
        marker_found: list[dict[str, Any]] = []
        if new_password.status == 200:
            reply = api(
                f"http://127.0.0.1:{port}", "POST", "/board/api/projects/1/keys",
                {
                    "name": "the second look",
                    "tools": ["list_nodes", "server_status"],
                    "code_mode": False,
                },
                cookie=new_password.cookie, csrf=new_password.body["csrf"],
            )  # fmt: skip
            async with connect(f"http://127.0.0.1:{port}/mcp", reply.body["secret"]) as again:
                fresh = await ok(again, "server_status")
                fresh_epics = fresh["counts"]["epics"]
                marker_found = [n for n in await all_nodes(again) if n["title"] == marker]
        proof(
            "M20",
            "running ./demo.sh a second time starts from a fresh database: the epic made in "
            "between is gone, the old password and the old key are refused, the new password "
            "signs in, and the project is the sample again",
            len(made) == 1
            and not marker_found
            and password_2 != password
            and old_password.status in (400, 401, 403)
            and new_password.status == 200
            and old_key in (401, 403)
            and fresh_epics == held["status"]["counts"]["epics"]
            and address_2 == address,
            f"old password {old_password.status}, old key {old_key}, {fresh_epics} epics",
        )

        # Stop removes the demo's own containers, volumes and image, and only those.
        stopped = demo_sh("stop")
        left = things_of(project)
        still = docker("inspect", "-f", "{{.State.Running}}", bystander)
        volume_kept = docker("volume", "inspect", "-f", "{{.Name}}", held_volume)
        stack_now = docker(
            "ps", "-aq", "--filter", f"label=com.docker.compose.project={stack}"
        ).split()
        shared_images = [
            docker("images", "-q", image)
            for image in ("python:3.12-slim", "pgvector/pgvector:pg17")
        ]
        proof(
            "M20",
            "./demo.sh stop removes the demo's containers, its volumes and its image and the "
            f"port ({port}) stops answering, while a container and a volume that are not its "
            "own, the throwaway stack the walk is running in (its containers unchanged) and the "
            "database and Python images that other stacks use are all still there",
            stopped.returncode == 0
            and "The demo is gone" in stopped.stdout
            and not any(left.values())
            and not answers(port)
            and still == "true"
            and volume_kept == held_volume
            and sorted(stack_now) == sorted(neighbours)
            and all(shared_images),
            f"left: {[name for name, there in left.items() if there] or 'nothing'}",
        )
    except BrowserError as error:
        raise Failed(
            f"M20: the board did not do what a proof needs: {clip(str(error), 300)}"
        ) from error
    finally:
        demo_sh("stop")
        docker("rm", "-f", bystander)
        docker("volume", "rm", "-f", held_volume)
