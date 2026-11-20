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
what it prints, signs in with a real browser, makes an agent key on the Agent access page, and
connects a real MCP client with that key. On every screen it then compares what the page shows with
what the tools say about the sample data."""

import os
import re
import socket
import subprocess
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from mcp import Client
from playwright.async_api import Error as BrowserError
from playwright.async_api import expect

from tests.acceptance_pages import STATUS_WORDS, read_the_whole_feed, visit, words
from tests.acceptance_scenario import Failed, chapter, clip, http_request, proof
from tests.board_helpers import SLOW, Watched, browser_page
from tests.board_m14 import events_of, every_item, ids_of
from tests.helpers import REPO_ROOT, all_nodes, connect, day, ok
from tests.world import api

DEMO_EMAIL = "demo@example.com"
SCRIPT = REPO_ROOT / "demo.sh"
THE_ADDRESS = re.compile(r"Open the board:\s+(\S+)")
THE_SIGN_IN = re.compile(r"Sign in with:\s+(\S+)\s+and password\s+(\S+)")


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
    """A key made the way a person makes one: on the Agent access page."""
    page = w.page
    await w.go("#/access")
    await page.locator("#key-label").fill("The walk's own agent")
    await page.locator("#create-key").click()
    await expect(page.locator("#key-secret")).to_contain_text("vrk_", timeout=SLOW)
    return (await page.locator("#key-secret").inner_text()).strip()


async def what_the_tools_hold(client: Client) -> dict[str, Any]:
    """The sample project as an agent reads it: every node, acceptance criterion and note, who is
    working, the status counts."""
    nodes = await all_nodes(client)
    return {
        "nodes": nodes,
        "rules": await every_item(client, "list_rules"),
        "notes": await every_item(client, "list_notes"),
        "working": await ok(client, "who_is_working"),
        "status": await ok(client, "server_status"),
        "coverage": await ok(client, "coverage_report", limit=1),
        "overdue": await ids_of(client, "is:overdue"),
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
    }


async def number_in(page: Any, selector: str) -> int:
    return int((await page.locator(selector).first.inner_text()).strip())


async def every_screen_shows_it(w: Watched, client: Client, held: dict[str, Any]) -> None:
    """Each screen, against the tools: the numbers and the rows are the tools'."""
    page = w.page
    nodes: list[dict[str, Any]] = held["nodes"]
    work = [n for n in nodes if n["kind"] != "epic"]
    counts = held["status"]["counts"]
    differences: list[str] = []

    # The Board: a card for each story and task, by status, and the overdue ones.
    await visit(w, "#/board", fresh=True)
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
    if await number_in(page, "#main-nav a[data-page=board] .nav-count") != len(work):
        differences.append("Board count in the sidebar")
    await visit(w, "#/board?q=is:overdue", fresh=True)
    overdue_cards = await page.locator("article.card").evaluate_all(
        "els => els.map((e) => e.dataset.ref)"
    )
    if sorted(overdue_cards) != sorted(r for r in held["overdue"] if not r.startswith("epic:")):
        differences.append("the overdue cards")

    # The Graph: it opens, and its sidebar number counts what is in force.
    await visit(w, "#/graph", fresh=True)
    await expect(page.locator("#graph-explore")).to_be_visible(timeout=SLOW)
    in_force = (
        len(nodes)
        + sum(1 for r in held["rules"] if r["status"] != "dropped")
        + sum(1 for n in held["notes"] if n["status"] in ("unconfirmed", "confirmed"))
    )
    await expect(page.locator("#main-nav a[data-page=graph] .nav-count")).to_have_text(
        str(in_force), timeout=SLOW
    )

    # The Roadmap: what has dates is on the timeline.
    dated = sum(1 for n in nodes if n["span_start"] or n["span_end"])
    await visit(w, "#/roadmap", fresh=True)
    await expect(page.locator("#count")).to_contain_text(
        f"{dated} items on the timeline", timeout=SLOW
    )
    await expect(page.locator("#main-nav a[data-page=roadmap] .nav-count")).to_have_text(str(dated))

    # Activity: who is working on what, and the feed.
    agents = held["working"]["agents"]
    live = sum(len(a["holding"]) for a in agents)
    lapsed = sum(len(a["lapsed"]) for a in agents)
    handoffs = [e for e in await events_of(client) if e["action"] == "handoff"]
    await visit(w, "#/activity", fresh=True)
    await expect(page.locator("#claims-live li.claim")).to_have_count(live, timeout=SLOW)
    if await page.locator("#claims-lapsed li.claim").count() != lapsed:
        differences.append("the claims that ran out")
    await expect(page.locator("#sidebar-activity-count")).to_have_text(str(live))
    await read_the_whole_feed(page)
    if await page.locator("#feed li.event").count() != counts["events"]:
        differences.append(f"the feed has {await page.locator('#feed li.event').count()} changes")
    if await page.locator("#feed li.event .handoff-note").count() != len(handoffs):
        differences.append("the handoff notes")

    # Coverage: the acceptance criteria placed nowhere, and the stories without one.
    await visit(w, "#/coverage", fresh=True)
    summary = page.locator("#coverage-summary")
    await expect(summary).to_have_attribute(
        "data-unplaced", str(held["coverage"]["unplaced_rule_count"]), timeout=SLOW
    )
    if await summary.get_attribute("data-stories-without") != str(
        held["coverage"]["story_without_rules_count"]
    ):
        differences.append("the stories without an acceptance criterion")

    # Status: how much the project holds.
    await visit(w, "#/status", fresh=True)
    for name, wanted_count in counts.items():
        cell = page.locator(f"#sizes tr[data-name='{name}'] td.value")
        if await cell.count() and (await cell.inner_text()).strip() != str(wanted_count):
            differences.append(f"status {name}")

    # Search: a word from the data finds what the tool finds.
    word = max(re.findall(r"[A-Za-z]{6,}", work[0]["title"]) or ["bike"], key=len)
    found = await ok(client, "search", query=word, limit=200)
    await visit(w, f"#/search?search={word}", fresh=True)
    await expect(page.locator("#search-count")).to_have_text(
        re.compile(rf"^{found['total']} results?\b"), timeout=SLOW
    )
    proof(
        "M20",
        "in a browser signed in as the demo's account, the Board has a card for each of the "
        f"{len(work)} stories and tasks, by status and overdue as the tools count them, the "
        f"Graph opens and its number is the {in_force} in force, the "
        f"Roadmap has the {dated} dated items, Activity has the {live} claims in force, the "
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
        await visit(w, f"#/item?item={ref}&from=board", fresh=True)
        await expect(page.locator("#item-page")).to_have_attribute("data-ref", ref, timeout=SLOW)
        shown = words(await page.locator("#item-page").inner_text())
        sure = f"Confirmed by {record['confirmed_by']}" if record["status"] == "confirmed" else ""
        if STATUS_WORDS[record["status"]] not in shown or sure not in shown:
            wrong.append(ref)
    # Each epic's page says as many notes as recall does; one has no stories.
    epics = [n for n in held["nodes"] if n["kind"] == "epic"]
    bare = None
    for epic in epics:
        recalled = await ok(client, "recall", node=epic["ref"])
        await visit(w, f"#/item?item={epic['ref']}&from=board", fresh=True)
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
            await expect(page.locator("#item-empty")).to_contain_text("No stories yet")
    proof(
        "M20",
        f"the pages of {len(picks)} notes in every state (unconfirmed, confirmed, project-wide, "
        "decided by someone or not, replaced, withdrawn, linked) say what the tools say, and "
        f"each of the {len(epics)} epics' pages holds as many notes as recall gives it; the "
        "epic with no stories says so",
        not wrong and bare is not None,
        f"{len(picks)} notes, {len(epics)} epics" + (f"; wrong: {wrong[:3]}" if wrong else ""),
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
                    "a real MCP client with a key made on the demo's Agent access page reads "
                    f"sample data with all of it in it: {', '.join(said)}",
                    not missing,
                    f"{held['status']['counts']['epics']} epics, "
                    f"{held['status']['counts']['tasks']} tasks"
                    + (f"; missing: {missing}" if missing else ""),
                )
                await every_screen_shows_it(w, client, held)
                await records_of_every_state(w, client, held)
                marker = f"Marker {port}"
                await ok(client, "create_node", actor="walk", kind="epic", title=marker)
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
                {"label": "the second look"}, cookie=new_password.cookie,
                csrf=new_password.body["csrf"],
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
