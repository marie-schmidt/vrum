"""Chapter 16 of the acceptance scenario: the README's Getting started, walked as a newcomer
would (M18). The README and the agent guide are read from the checkout the walk runs in: the
overview and getting started come first, the commands name files that exist, and the core setup
asks for no embedding or language-model service. Then, on the real stack with its embedding
service stopped, a person registers in a real browser, sees no demo records, makes a project, adds
an epic, a story and a task with the Board's own dialog, and makes an agent key whose secret is
shown once; the Python example from docs/agents.md and a real MCP client use that key to claim the
task and finish it with a handoff note; and the Board and its Activity drawer show what the agent
did, equal to the server's own feed.

The chapter makes its own account, project and key, so it needs nothing from the chapters before
it."""

import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

from playwright.async_api import Error as BrowserError
from playwright.async_api import Page, expect

from tests.acceptance_scenario import Failed, chapter, clip, proof
from tests.board_helpers import SLOW, browser_page, create_in_browser
from tests.board_m14 import events_of, feed_becomes, feed_row, spoken
from tests.helpers import REPO_ROOT, connect, embeddings_stopped, ok, unique_word
from tests.test_board_accounts_integration import register_in_the_browser
from tests.world import Person, server_base

README = REPO_ROOT / "README.md"
GUIDE = REPO_ROOT / "docs" / "agents.md"
AGENT = "quickstart-agent"
NOTE = "Built the sign-in form. Nothing is left to do, but check the error messages."
NEWS = 20_000
# What a plain GET with no key gets at each address the README gives: the board's page and the
# health check are open, and the endpoint agents use wants a key.
ANSWERS = {"/board": 200, "/health": 200, "/mcp": 401}


async def quickstart() -> None:
    chapter("Quick start: the README's Getting started, as a newcomer follows it")
    base = server_base()
    try:
        read_the_readme(base)
        with embeddings_stopped():
            await first_minutes(base)
    except BrowserError as error:
        raise Failed(
            f"M18: the board did not do what a proof needs: {clip(str(error), 220)}"
        ) from error


# -- reading what a newcomer reads


def sections(text: str) -> dict[str, str]:
    """The `##` sections of a Markdown file by title, in order. A line in a code block that begins
    with # is not a heading."""
    found: dict[str, list[str]] = {}
    current: str | None = None
    fenced = False
    for line in text.splitlines():
        if re.match(r"^\s*```", line):
            fenced = not fenced
        heading = None if fenced else re.fullmatch(r"## (.+)", line)
        if heading:
            current = heading.group(1).strip()
            found[current] = []
        elif current is not None:
            found[current].append(line)
    return {title: "\n".join(lines) for title, lines in found.items()}


def commands(section: str) -> list[str]:
    """The command lines of a section's code blocks that run something from the checkout: those
    that begin with ./, docker compose or uv."""
    lines: list[str] = []
    for block in re.findall(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```[ \t]*$", section, re.S | re.M):
        lines += [
            line.strip()
            for line in block.splitlines()
            if line.strip().startswith(("./", "docker compose", "uv "))
        ]
    return lines


def missing(command: str) -> list[str]:
    """What a command needs that this checkout does not have: a script that is not there or not
    executable, a file it names, and the compose file (and Dockerfile) that docker compose reads."""
    parts = shlex.split(command)
    gone: list[str] = []
    if command.startswith("docker compose"):
        gone += [
            name
            for name in ("compose.yaml", *(("Dockerfile",) if "--build" in parts else ()))
            if not (REPO_ROOT / name).is_file()
        ]
    for part in parts:
        if part.startswith("-") or "://" in part or "<" in part:
            continue
        if part.startswith("./") or "/" in part or part.endswith((".py", ".sh", ".yaml")):
            path = REPO_ROOT / part.removeprefix("./")
            if not path.exists():
                gone.append(part)
            elif part.startswith("./") and not os.access(path, os.X_OK):
                gone.append(f"{part} (not executable)")
    return gone


def asked(base: str, path: str) -> tuple[int, str]:
    """What the stack answers to a plain GET of an address the README gives, with no key."""
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=30) as reply:
            return int(reply.status), reply.read().decode().strip()
    except urllib.error.HTTPError as error:
        return error.code, ""


def read_the_readme(base: str) -> None:
    text = README.read_text(encoding="utf-8")
    found = sections(text)
    titles = list(found)
    started = found.get("Getting started", "")
    prose = re.sub(r"^\s*```.*?^\s*```[ \t]*$", "", text, flags=re.S | re.M)
    guide = GUIDE.read_text(encoding="utf-8")
    proof(
        "M18",
        "the README's first two sections after its title are the Overview and Getting started, and "
        "Getting started sends a reader who wants to connect an agent to docs/agents.md, which has "
        "the Python example and the agent guide",
        re.findall(r"^# (.+)$", prose, re.M) == ["Vrum"]
        and titles[:2] == ["Overview", "Getting started"]
        and re.search(r"\]\(docs/agents\.md\)", started) is not None
        and re.search(r"```python\n.*?\n```", guide, re.S) is not None
        and "agent-guide:start" in guide,
        f"sections: {', '.join(titles[:3])}",
    )

    lines = commands(started)
    gone = [f"{line}: {', '.join(needs)}" for line in lines if (needs := missing(line))]
    named = sorted(set(re.findall(r"http://127\.0\.0\.1:8000(/[\w/-]*)", started)))
    unanswered = [path for path in named if asked(base, path)[0] != ANSWERS.get(path)]
    health_as_told = "/health" not in named or (
        asked(base, "/health")[1] == '{"status":"ok"}' and '{"status":"ok"}' in started
    )
    proof(
        "M18",
        "every command in Getting started's code blocks that runs something from the checkout "
        "names a script or file that exists (and a script that can be run), and the addresses it "
        "gives (the board, the health check) answer as it says when asked of this stack",
        len(lines) >= 2 and not gone and "/board" in named and not unanswered and health_as_told,
        clip("; ".join(gone + unanswered), 120)
        or f"{len(lines)} commands read, none run; {', '.join(named)} answered",
    )

    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    set_there = re.findall(r"^(?:EMBEDDING|LLM)_\w+=", env_example, re.M)
    mentioned = re.findall(r"EMBEDDING_|LLM_|language[- ]model|Ollama", started, re.I)
    proof(
        "M18",
        "the core setup asks for no embedding service and no language model: Getting started "
        "names no setting for either, and the .env its script writes from .env.example sets "
        "neither (the only value it must have is the database password)",
        not mentioned
        and not set_there
        and re.findall(r"^VRUM_DB_PASSWORD=$", env_example, re.M) == ["VRUM_DB_PASSWORD="]
        and not re.search(r"\bvr[ks]_[\w-]{8,}", text + guide),
        "no setting named, no key in an example",
    )


# -- the first minutes, in a browser and with an agent


async def first_minutes(base: str) -> None:
    word = unique_word()
    email, name = f"quickstart-{word}@example.test", f"Ada {word}"
    password = f"quickstart-{word}-pass"
    async with browser_page(f"{base}/board", token=None, name=None) as w:
        page = w.page
        await register_in_the_browser(w, name, email, password)
        await expect(page.locator("#no-projects")).to_be_visible(timeout=SLOW)
        no_project_yet = await page.locator("li.project").count() == 0
        person = await signed_in_person(page, base, email, password, name)
        listed_before = person.call("GET", "/board/api/projects").body["projects"]
        await page.locator("#project-name").fill(f"Quick start {word}")
        await page.locator("#create-project").click()
        await expect(page.locator("#main-nav")).to_be_visible(timeout=SLOW)
        found = re.search(r"/board/p/(\d+)", page.url)
        assert found, page.url
        mine = f"/board/api/projects/{found.group(1)}"
        await expect(page.locator("#board-empty")).to_be_visible(timeout=SLOW)
        listed = person.call("GET", "/board/api/projects").body["projects"]
        nodes = person.call("POST", f"{mine}/tool/list_nodes", {}).body
        status = person.call("POST", f"{mine}/tool/server_status", {}).body
        proof(
            "M18",
            "a newcomer registers in the browser with a name, an email address and a password, "
            "finds no project (no demo project comes with the install), makes one, and its Board "
            "is empty: no work item, requirement, decision or fact, by the server's own counts",
            no_project_yet
            and listed_before == []
            and [each["name"] for each in listed] == [f"Quick start {word}"]
            and nodes["total"] == 0
            and set(status["counts"].values()) == {0},
            f"{len(listed_before)} projects before; {len(listed)} after, {nodes['total']} items",
        )

        # New item, three times: an epic, a story in it and a task in the story.
        epic = await create_in_browser(page, "epic", f"Accounts {word}")
        story = await create_in_browser(page, "story", f"Sign in {word}", epic)
        task = await create_in_browser(page, "task", f"Login form {word}", story)
        await page.locator("#settings-menu").click()
        await page.locator("#settings-access").click()
        await page.locator("#key-label").fill("my first agent")
        await page.locator("#create-key").click()
        await expect(page.locator("#key-secret")).to_contain_text("vrk_", timeout=SLOW)
        key = (await page.locator("#key-secret").inner_text()).strip()
        await w.go("#/board")
        await expect(page.locator(f".card[data-id='{task}']")).to_be_visible(timeout=SLOW)
        await w.go("#/access")
        await expect(page.locator("#key-list")).to_contain_text("my first agent", timeout=SLOW)
        gone_from_page = key not in await page.locator("body").inner_text()
        async with connect(f"{base}/mcp", key) as agent:
            seen = {
                node["id"]: (node["kind"], node["title"])
                for node in (await ok(agent, "list_nodes"))["items"]
            }
        proof(
            "M18",
            "with New item the newcomer makes an epic, a story in it and a task in the story, then "
            "makes an agent key from Settings & tools: the secret is shown once (the page shows "
            "only its label and start afterwards), and an MCP client with it sees the same three "
            "items with the ids and titles the dialog gave",
            key.startswith("vrk_")
            and gone_from_page
            and seen
            == {
                epic: ("epic", f"Accounts {word}"),
                story: ("story", f"Sign in {word}"),
                task: ("task", f"Login form {word}"),
            },
            f"items {epic}, {story}, {task}",
        )

        await w.go("#/board")
        await expect(page.locator(f".card[data-id='{task}']")).to_be_visible(timeout=SLOW)
        await the_agent_works(base, key, task, page)
        await the_board_shows_it(base, key, task, page)


async def signed_in_person(page: Page, base: str, email: str, password: str, name: str) -> Person:
    """The person the browser is signed in as, so that what the board's own API says can be read
    next to what the page shows."""
    cookies = {each["name"]: each["value"] for each in await page.context.cookies()}
    session = await page.evaluate("fetch('/board/api/session').then((r) => r.json())")
    return Person(
        base,
        email,
        password,
        name,
        cookies["vrum_session"],
        session["csrf"],
        session["account"]["id"],
    )


async def the_agent_works(base: str, key: str, task: int, page: Page) -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    found = re.search(r"```python\n(.*?)\n```", guide, re.DOTALL)
    assert found, "docs/agents.md has no Python example"
    example = found.group(1).replace("http://127.0.0.1:8000/mcp", f"{base}/mcp")
    ran = subprocess.run(
        [sys.executable, "-c", example],
        env={**os.environ, "VRUM_AGENT_KEY": key},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    async with connect(f"{base}/mcp", key) as agent:
        taken = await ok(agent, "next_work", actor=AGENT)
        held = page.locator(f".card[data-id='{task}'] .card-note.claim")
        await expect(held).to_contain_text(f"Held by {AGENT}", timeout=NEWS)
        finished = await ok(
            agent, "finish_work", actor=AGENT, node_id=task, outcome="done", note=NOTE
        )
    proof(
        "M18",
        "the Python example from docs/agents.md, run as it is written with the key, reaches the "
        "server; a real MCP client with the same key takes the task with next_work (the Board "
        "shows it held by the agent) and ends it with finish_work and a handoff note",
        ran.returncode == 0
        and "postgres_version" in ran.stdout
        and taken["found"]
        and taken["work"]["id"] == task
        and taken["claim"]["actor"] == AGENT
        and finished["node"]["status"] == "done",
        f"the example printed {len(ran.stdout)} characters; #{task} done by {AGENT}",
    )


async def the_board_shows_it(base: str, key: str, task: int, page: Page) -> None:
    done = page.locator(f".cell[data-status='done'] .card[data-id='{task}']")
    await expect(done).to_be_visible(timeout=NEWS)
    await page.locator("#activity-open").click()
    await expect(page.locator("#drawer[data-drawer=activity]")).to_be_visible(timeout=SLOW)
    finished = feed_row(page, f"finished their work on task #{task}")
    await expect(finished).to_contain_text(AGENT, timeout=NEWS)
    async with connect(f"{base}/mcp", key) as agent:
        events = await events_of(agent)
        shown = await feed_becomes(page, [event["id"] for event in reversed(events)])
        handoffs = [event for event in events if event["action"] == "handoff"]
        claims = [event for event in events if event["action"] == "claimed"]
        status = await ok(agent, "server_status")
    claimed_row = feed_row(page, f"took task #{task}")
    said: dict[str, Any] = {
        "note": spoken(await finished.locator(".handoff-note").inner_text()),
        "actor": await finished.locator(".event-actor").inner_text(),
        "claim": await claimed_row.inner_text(),
    }
    proof(
        "M18",
        "the Board shows the task in its Done column, and the Activity drawer opened from the "
        "sidebar shows the agent's name, its claim and its handoff note in full, newest first, "
        "with the same events as the server's own change feed",
        "drawer=activity" in page.url
        and said["note"] == NOTE
        and said["actor"] == AGENT
        and AGENT in said["claim"]
        and [event["actor"] for event in handoffs] == [AGENT]
        and handoffs[0]["after"]["note"] == NOTE
        and [event["actor"] for event in claims] == [AGENT]
        and shown == sorted(shown, reverse=True)
        and len(shown) == len(events) >= 5,
        f"{len(events)} events, the last by {handoffs[0]['actor']}",
    )
    index = status["index"]
    proof(
        "M18",
        "none of this needed an embedding or a language-model service: the whole walk ran with the "
        "embedding service stopped, and server_status through the key still answers, says the "
        "service is not answering, and counts the one epic, story and task",
        status["postgres_version"]
        and index["embedding_service"] != "answering"
        and [status["counts"][kind] for kind in ("epics", "stories", "tasks")] == [1, 1, 1],
        f"embedding service: {index['embedding_service']}",
    )
