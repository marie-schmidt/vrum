"""The acceptance scenario (./acceptance.sh): one story of a small team using Vrum, told through a
real MCP client and a real headless browser against the real containers, in which every proof of
every milestone is shown to hold. It reads like the product being used: arrive, plan, work in
parallel, decide and remember, find, be careful with secrets, see it all on the board, and move
the whole graph to another place.

It is not the test suite (./check.sh runs hundreds of cases, each going deeper into one thing). It
is the shorter walk a person can read in a few minutes and trust: each line it prints is one proof,
in plain words, with the number or the refusal that shows it. The first thing that does not hold
stops it with a plain message.

The stack it runs against is the check stack: the real database and server, plus the scripted
embedding service of compose.check.yaml, so no model has to be downloaded and the answers are the
same every run. There is no language model anywhere. M8 (the real-data trial) is skipped until a
ledger path is given."""

import asyncio
import copy
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from mcp import Client
from mcp.shared.exceptions import MCPError

from tests.helpers import (
    REPO_ROOT,
    compose,
    confirm,
    connect,
    day,
    make_decision,
    make_fact,
    ok,
    race_results,
    refused,
    run_sql,
    the_token,
    unique_word,
)
from tests.world import make_project, register, the_project

URL = os.environ.get("VRUM_TEST_URL", "")
MAX_REQUEST_BYTES = 4 * 1024 * 1024

PROOFS: list[tuple[str, str]] = []


class Failed(Exception):
    """A proof that did not hold."""


def proof(milestone: str, what: str, condition: object, detail: str = "") -> None:
    """One proof: it holds, and is printed, or the scenario stops."""
    if not condition:
        raise Failed(f"{milestone}: {what}" + (f" ({detail})" if detail else ""))
    PROOFS.append((milestone, what))
    print(f"  ok  {milestone:<4} {what}" + (f"  [{detail}]" if detail else ""), flush=True)


def chapter(title: str) -> None:
    print(f"\n== {title}", flush=True)


def clip(text: str, size: int = 110) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= size else one_line[: size - 3] + "..."


async def refusal(
    client: Client, milestone: str, what: str, tool: str, says: str, **arguments: Any
) -> str:
    """A call that must be refused, in words that say `says`. The refusal is shown."""
    message = await refused(client, tool, **arguments)
    proof(milestone, what, says in message, f'"{clip(message)}"')
    return message


def rows(*tables: str) -> tuple[int, ...]:
    """How many rows these tables hold: what a refused request must not change."""
    counts = []
    for table in tables:
        result = run_sql(f"SELECT count(*) FROM {table}", plain=True)
        if result.returncode != 0:
            raise Failed(f"could not count {table}: {result.stderr}")
        counts.append(int(result.stdout.strip()))
    return tuple(counts)


def sql_value(sql: str, *, as_project: bool = False) -> str:
    """One value from the database. With `as_project`, only what the shared test project holds
    (what its agents see); otherwise everything, as the database's owner."""
    result = run_sql(sql, plain=True, as_project=as_project)
    if result.returncode != 0:
        raise Failed(f"the database said: {result.stderr}")
    return result.stdout.strip()


def ids(items: list[dict[str, Any]]) -> list[int]:
    return [item["id"] for item in items]


def http_request(
    url: str,
    *,
    method: str = "POST",
    token: str | None = None,
    body: bytes | None = b"{}",
) -> tuple[int, bytes]:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    call = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(call, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def leaves(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [leaf for part in error.exceptions for leaf in leaves(part)]
    return [error]


async def turned_away(url: str, token: str | None) -> MCPError:
    """What a real MCP client is told when it cannot get in."""
    try:
        async with connect(url, token):
            raise Failed("a client got in without a valid token")
    except BaseException as error:
        found = [leaf for leaf in leaves(error) if isinstance(leaf, MCPError)]
        if not found:
            raise
        return found[0]
    raise AssertionError("unreachable")


async def tree(client: Client, tasks: int = 0, **story: Any) -> tuple[dict[str, Any], ...]:
    """An epic, a story under it and `tasks` tasks under that."""
    epic = await ok(client, "create_node", actor="marie", kind="epic", title="An epic")
    made = await ok(
        client, "create_node", actor="marie", kind="story", title="A story", parent_id=epic["id"],
        **story,
    )  # fmt: skip
    found = [
        await ok(
            client, "create_node", actor="marie", kind="task", title=f"Task {n}",
            parent_id=made["id"],
        )
        for n in range(tasks)
    ]  # fmt: skip
    return (epic, made, *found)


async def get(client: Client, node: dict[str, Any]) -> dict[str, Any]:
    return await ok(client, "get_node", node_id=node["id"])


async def history(client: Client, node: dict[str, Any]) -> list[dict[str, Any]]:
    return list((await ok(client, "node_history", node_id=node["id"], limit=200))["items"])


async def set_status(client: Client, node: dict[str, Any], status: str) -> dict[str, Any]:
    current = await get(client, node)
    return await ok(
        client, "update_node", actor="marie", node_id=node["id"],
        expected_version=current["version"], status=status,
    )  # fmt: skip


# ---------------------------------------------------------------------------------------------
# 1. Arriving: the server is up, and only an agent key for a project gets in (M0, M6, M15, M16)
# ---------------------------------------------------------------------------------------------

# What M16 took out: server-side generation, and the belief and principle workflows around it.
GENERATION_TOOLS = re.compile(
    r"extract_facts|consolidate_memory|find_contradictions|reflect|belief|principle|memor",
    re.IGNORECASE,
)
GENERATION_WORDS = re.compile(
    r"language[ _]model|\bLLM_|extract_facts|consolidate_memory|find_contradictions|\breflect\b"
    r"|belief|principle|\bAsk (box|page)\b|ask\.js",
    re.IGNORECASE,
)


def board_files() -> dict[str, str]:
    """Every file the board's page loads, followed from the page itself: the page, its style
    sheets and its scripts, and the scripts those import."""
    base = URL.removesuffix("/mcp")
    found: dict[str, str] = {}
    todo = ["/board"]
    while todo:
        path = todo.pop()
        if path in found:
            continue
        status, body = http_request(f"{base}{path}", method="GET", body=None)
        if status != 200:
            raise Failed(f"M16: the board's own file {path} did not load ({status})")
        text = body.decode("utf-8")
        found[path] = text
        todo += re.findall(r"""(?:src|href)="(/board/[\w./-]+\.(?:js|css))\"""", text)
        todo += [f"/board/{name}" for name in re.findall(r"""from '\./([\w.-]+\.js)'""", text)]
    return found


async def no_language_model(
    tools: list[str], instructions: str, server: dict[str, Any], services: set[str]
) -> None:
    """M16's first proof: nothing in the live product generates text or asks or sets up a model."""
    environment = compose("exec", "-T", "app", "env").stdout
    settings = [line for line in environment.splitlines() if line.startswith("LLM_")]
    proof(
        "M16",
        "the stack started with no language-model configuration and no generation service: it "
        "is the database, the server and the embedding service, and the server's environment "
        "sets no model",
        services == {"app", "db", "scripted-embeddings"} and not settings and environment,
        ", ".join(sorted(services)),
    )
    proof(
        "M16",
        "the live tool list has no generation tool and no belief, principle or memory tool, and "
        "neither the instructions agents are given nor server_status say anything of a model",
        len(tools) == 53
        and not [name for name in tools if GENERATION_TOOLS.search(name)]
        and not GENERATION_WORDS.search(instructions)
        and "language_model" not in json.dumps(server),
        f"{len(tools)} tools",
    )
    info = the_project().owner.call("GET", "/board/api/info")
    files = board_files()
    scripts = {path: text for path, text in files.items() if path.endswith((".js", "board"))}
    leaks = [path for path, text in scripts.items() if GENERATION_WORDS.search(text)]
    proof(
        "M16",
        "the board has no Ask and names no language model: what it is told about the server and "
        "every page and script it loads are free of them",
        info.status == 200
        and "language" not in json.dumps(info.body)
        and not leaks
        and len(scripts) > 20,
        f"{len(scripts)} files read" + (f", but {leaks}" if leaks else ""),
    )
    docs = [REPO_ROOT / name for name in ("README.md", ".env.example", "compose.yaml")]
    docs.append(REPO_ROOT / "compose.check.yaml")
    unwanted = [
        f"{path.name}: {line.strip()[:60]}"
        for path in docs
        for line in path.read_text().splitlines()
        if (GENERATION_WORDS.search(line) and "language model" not in line.lower())
        or ("language model" in line.lower() and "never" not in line.lower())
    ]
    proof(
        "M16",
        "the configuration examples and the documented setup set up no language model and "
        "describe no Ask, belief or principle workflow",
        not unwanted and all(path.exists() for path in docs),
        "README, .env.example and both compose files read",
    )


async def arriving() -> None:
    chapter("Arriving: the server is up, and only an agent key for a project gets in")
    base = URL.removesuffix("/mcp")
    listing = compose("ps", "--format", "json")
    states = [json.loads(line) for line in listing.stdout.splitlines() if line.strip()]
    proof(
        "M0",
        "docker compose up leaves every container of the stack healthy",
        states and all(state.get("Health") == "healthy" for state in states),
        ", ".join(sorted(state["Service"] for state in states)),
    )
    status, body = http_request(f"{base}/health", method="GET", body=None)
    proof("M0", "GET /health answers ok with no key", (status, body) == (200, b'{"status":"ok"}'))
    async with connect(URL, the_token()) as client:
        tools = [tool.name for tool in (await client.list_tools()).tools]
        proof("M0", "a real MCP client lists the tools", len(tools) == 53, f"{len(tools)} tools")
        server = await ok(client, "server_status")
        proof(
            "M0",
            "the server reports its database and vector extension",
            server["postgres_version"].startswith("17") and server["pgvector_version"],
            f"PostgreSQL {server['postgres_version']}, pgvector {server['pgvector_version']}",
        )
        named = set(re.findall(r"`([a-z_]+)`", client.instructions or ""))
        proof(
            "M5B",
            "the instructions the client shows name only tools that exist",
            named and named <= set(tools),
            f"{len(named)} tools named",
        )
        await no_language_model(
            tools, client.instructions or "", server, {state["Service"] for state in states}
        )
    wrong = "vrk_a-wrong-key-" + unique_word() + "-0123456789"
    for label, key in (("no key", None), ("a wrong key", wrong)):
        status, body = http_request(URL, token=key)
        message = str(json.loads(body)["error"]["message"])
        proof(
            "M6",
            f"a request with {label} is refused, and the answer does not repeat it",
            status == 401 and wrong not in message and the_token() not in message,
            f'{status} "{clip(message, 70)}"',
        )
        told = await turned_away(URL, key)
        proof("M6", f"an MCP client with {label} is told why in plain words", told.code == -32001)
    shut = [
        http_request(URL, method="GET", body=None)[0],
        http_request(f"{base}/board/api/projects", method="GET", body=None)[0],
        http_request(f"{base}/board/api/tools", method="GET", body=None)[0],
        http_request(f"{base}/board/api/projects/1/tool/list_nodes", body=b"{}")[0],
    ]
    proof(
        "M15",
        "the endpoint needs an agent key, and the board's data calls need a signed-in person: "
        "only GET /health and the board's page files are open",
        shut == [401, 401, 401, 401]
        and http_request(f"{base}/board", method="GET", body=None)[0] == 200,
        f"{shut}",
    )
    # The client example in the agent guide, run as it is written, aimed at this stack.
    guide = (REPO_ROOT / "docs" / "agents.md").read_text()
    found = re.search(r"```python\n(.*?)\n```", guide, re.DOTALL)
    assert found, "docs/agents.md has no Python example"
    example = found.group(1).replace("http://127.0.0.1:8000/mcp", URL)
    ran = subprocess.run(
        [sys.executable, "-c", example],
        env={**os.environ, "VRUM_AGENT_KEY": the_token()},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    proof(
        "M9",
        "the Python example in docs/agents.md connects with an agent key and gets an answer",
        ran.returncode == 0 and "postgres_version" in ran.stdout,
        clip(ran.stdout or ran.stderr, 70),
    )


# ---------------------------------------------------------------------------------------------
# 2. Planning: a plan, the hierarchy, and dates (M5B, M1, M1A)
# ---------------------------------------------------------------------------------------------


def a_plan() -> dict[str, Any]:
    """1 epic, 3 stories, 8 tasks, 5 links and 4 rules."""
    items: list[dict[str, Any]] = [{"key": "epic", "kind": "epic", "title": "Launch invoicing"}]
    for story in range(3):
        items.append(
            {"key": f"s{story}", "kind": "story", "title": f"Story {story}", "parent": "epic"}
        )
    for task in range(8):
        items.append(
            {
                "key": f"t{task}",
                "kind": "task",
                "title": f"Task {task}",
                "parent": f"s{task % 3}",
                "priority": "high" if task == 0 else "normal",
                "target_on": day(30),
            }
        )
    links = [
        {"source": "t3", "target": "t0", "link_type": "needs_first"},
        {"source": "t6", "target": "t3", "link_type": "needs_first"},
        {"source": "t4", "target": "t1", "link_type": "needs_first"},
        {"source": "s1", "target": "s0", "link_type": "relates_to"},
        {"source": "t7", "target": "t2", "link_type": "needs_first"},
    ]
    rules = [
        {"title": "Every invoice has a number", "story": "s0", "status": "settled"},
        {"title": "Totals are in euros", "story": "s0"},
        {"title": "Credit notes keep the old number", "story": "s1", "body": "See finance."},
        {"title": "Not placed yet"},
    ]
    return {"items": items, "links": links, "rules": rules}


def a_plan_of_five_hundred() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """1 epic, 49 stories with 9 tasks each (waiting in a chain in every story), 9 more tasks."""
    items: list[dict[str, Any]] = [{"key": "epic", "kind": "epic", "title": "A big plan"}]
    links: list[dict[str, Any]] = []
    for s in range(49):
        items.append({"key": f"s{s}", "kind": "story", "title": f"S{s}", "parent": "epic"})
        for t in range(9):
            items.append({"key": f"s{s}t{t}", "kind": "task", "title": f"T{t}", "parent": f"s{s}"})
            if t:
                links.append(
                    {"source": f"s{s}t{t}", "target": f"s{s}t{t - 1}", "link_type": "needs_first"}
                )
    items.extend(
        {"key": f"x{n}", "kind": "task", "title": f"Extra {n}", "parent": "s0"} for n in range(9)
    )
    return items, links


async def planning() -> None:
    chapter("Planning: a whole plan in one call, an exact hierarchy, and dates")
    async with connect(URL, the_token()) as client:
        # -- M5B: create_plan
        plan = a_plan()
        result = await ok(client, "create_plan", actor="ann", **plan)
        proof(
            "M5B",
            "one create_plan call makes 1 epic, 3 stories, 8 tasks, 5 links and 4 rules",
            result["counts"] == {"epic": 1, "story": 3, "task": 8, "links": 5, "rules": 4},
            str(result["counts"]),
        )
        new = {item["key"]: item for item in result["created"]}
        t6 = await get(client, new["t6"])
        proof(
            "M5B",
            "the links in it are real: t6 waits for t3, which waits for t0",
            t6["waiting_on"] == [new["t3"]["id"]] and t6["status"] == "new",
        )
        before = rows("nodes", "links", "rules", "events")
        bad = copy.deepcopy(plan)
        bad["items"].append({"key": "bad", "kind": "task", "title": "Hangs", "parent": "epic"})
        message = await refused(client, "create_plan", actor="ann", **bad)
        proof(
            "M5B",
            "a plan with one invalid item creates nothing and names the problem",
            "belongs under a story" in message
            and rows("nodes", "links", "rules", "events") == before,
            f'"{clip(message)}"',
        )
        circle = {
            "items": [
                {"key": "e", "kind": "epic", "title": "E"},
                {"key": "s", "kind": "story", "title": "S", "parent": "e"},
                *({"key": k, "kind": "task", "title": k.upper(), "parent": "s"} for k in "xyz"),
            ],
            "links": [
                {"source": "x", "target": "y", "link_type": "needs_first"},
                {"source": "y", "target": "z", "link_type": "needs_first"},
                {"source": "z", "target": "x", "link_type": "needs_first"},
            ],
        }
        message = await refused(client, "create_plan", actor="ann", **circle)
        proof(
            "M5B",
            "a plan with a circle of waiting creates nothing and names the circle",
            "the key 'x' needs the key 'y', which needs the key 'z', which needs the key 'x'"
            in message
            and rows("nodes", "links", "rules", "events") == before,
            f'"{clip(message)}"',
        )
        items, links = a_plan_of_five_hundred()
        started = time.monotonic()
        big = await ok(client, "create_plan", actor="ann", items=items, links=links)
        seconds = time.monotonic() - started
        proof(
            "M5B",
            "a plan of 500 items is created in one call",
            len(items) == 500 and len(big["created"]) == 500 and seconds < 60,
            f"{len(links)} links too, {seconds:.1f} seconds",
        )

        # -- M1: the hierarchy and versions
        epic = await ok(client, "create_node", actor="marie", kind="epic", title="Checkout")
        nodes_before = rows("nodes")
        await refusal(
            client, "M1", "a story without an epic is refused", "create_node",
            "A story needs a parent epic", actor="marie", kind="story", title="Orphan",
        )  # fmt: skip
        await refusal(
            client, "M1", "a task under an epic is refused", "create_node",
            f"A task belongs under a story, but node {epic['id']} is an epic",
            actor="marie", kind="task", title="Misplaced", parent_id=epic["id"],
        )  # fmt: skip
        story = await ok(
            client, "create_node", actor="marie", kind="story", title="Pay by card",
            parent_id=epic["id"],
        )  # fmt: skip
        task = await ok(
            client,
            "create_node",
            actor="marie",
            kind="task",
            title="Card form",
            parent_id=story["id"],
        )
        proof(
            "M1",
            "a valid epic, story and task are created",
            (epic["kind"], story["kind"], task["kind"]) == ("epic", "story", "task")
            and rows("nodes") == (nodes_before[0] + 2,),
        )
        other = await ok(client, "create_node", actor="marie", kind="epic", title="Refunds")
        await refusal(
            client, "M1", "a second parent is refused, and move_node is offered", "update_node",
            "exactly one parent", actor="marie", node_id=story["id"], expected_version=1,
            parent_id=other["id"],
        )  # fmt: skip
        await ok(
            client, "update_node", actor="ann", node_id=task["id"], expected_version=1,
            title="Card form (Ann)", status="in_progress",
        )  # fmt: skip
        await refusal(
            client, "M1", "a stale version is refused and overwrites nothing", "update_node",
            f"Node {task['id']} is at version 2, but you sent version 1",
            actor="bob", node_id=task["id"], expected_version=1, title="Card form (Bob)",
        )  # fmt: skip
        events = await history(client, task)
        proof(
            "M1",
            "the history of a task is readable, with who and before and after",
            [(e["action"], e["actor"]) for e in events] == [("create", "marie"), ("update", "ann")]
            and events[1]["before"] == {"status": "new", "title": "Card form", "version": 1}
            and events[1]["after"]["title"] == "Card form (Ann)",
        )

        # -- M1A: dates
        late = await ok(
            client, "create_node", actor="marie", kind="task", title="Late job",
            parent_id=story["id"], target_on=day(-3),
        )  # fmt: skip
        message = await refused(
            client, "create_node", actor="marie", kind="task", title="Backwards",
            parent_id=story["id"], start_on=day(9), target_on=day(3),
        )  # fmt: skip
        proof(
            "M1A",
            "a start date after the target date is refused in plain words",
            f"The start date {day(9)} is after the target date {day(3)}" in message,
            f'"{clip(message)}"',
        )
        attempt = run_sql(
            f"UPDATE nodes SET start_on = '{day(9)}', target_on = '{day(3)}', "
            f"version = version + 1 "
            f"WHERE id = {task['id']}"
        )
        proof(
            "M1A",
            "the database refuses it on its own too",
            attempt.returncode != 0 and "node_start_is_not_after_target" in attempt.stderr,
            "node_start_is_not_after_target",
        )
        dated = await ok(
            client, "update_node", actor="ann", node_id=task["id"], expected_version=2,
            start_on=day(2), target_on=day(10),
        )  # fmt: skip
        cleared = await ok(
            client, "update_node", actor="ann", node_id=task["id"], expected_version=3,
            clear_start_on=True,
        )  # fmt: skip
        last = (await history(client, task))[-1]
        proof(
            "M1A",
            "dates set and cleared are shown by get_node and kept in the history before and after",
            (dated["start_on"], dated["target_on"]) == (day(2), day(10))
            and cleared["start_on"] is None
            and last["before"]["start_on"] == day(2)
            and last["after"]["start_on"] is None,
        )
        await refusal(
            client, "M1A", "a stale version is still refused for a date change", "update_node",
            "is at version 4, but you sent version 3", actor="bob", node_id=task["id"],
            expected_version=3, target_on=day(20),
        )  # fmt: skip
        was = await get(client, late)
        done = await set_status(client, late, "done")
        proof(
            "M1A",
            "a node whose target date has passed is overdue until it is done",
            was["overdue"] is True and done["overdue"] is False,
        )
        epic2 = await ok(client, "create_node", actor="marie", kind="epic", title="Dated work")
        s_dated = await ok(
            client, "create_node", actor="marie", kind="story", title="Dated",
            parent_id=epic2["id"],
            start_on=day(40), target_on=day(60),
        )  # fmt: skip
        s_plain = await ok(
            client, "create_node", actor="marie", kind="story", title="Plain", parent_id=epic2["id"]
        )
        for name, start, target in (("Early", 5, 8), ("Late", 7, 30)):
            await ok(
                client, "create_node", actor="marie", kind="task", title=name,
                parent_id=s_plain["id"], start_on=day(start), target_on=day(target),
            )  # fmt: skip
        span, whole = await get(client, s_plain), await get(client, epic2)
        proof(
            "M1A",
            "a story with dated children and none of its own reports their span; an epic does too",
            (span["span_start"], span["span_end"], span["span_worked_out"])
            == (day(5), day(30), True)
            and (whole["span_start"], whole["span_end"]) == (day(5), day(60)),
            f"{day(5)} to {day(30)}",
        )
        own = await get(client, s_dated)
        proof(
            "M1A",
            "one with its own dates reports those",
            (own["span_start"], own["span_end"], own["span_worked_out"])
            == (day(40), day(60), False),
        )
        first = await ok(
            client, "create_node", actor="marie", kind="task", title="First",
            parent_id=s_dated["id"], start_on=day(1), target_on=day(20),
        )  # fmt: skip
        second = await ok(
            client, "create_node", actor="marie", kind="task", title="Second",
            parent_id=s_dated["id"], start_on=day(10), target_on=day(30),
        )  # fmt: skip
        await ok(
            client, "add_link", actor="marie", source_id=second["id"], target_id=first["id"],
            link_type="needs_first",
        )  # fmt: skip
        conflict = await get(client, second)
        fixed = await ok(
            client, "update_node", actor="marie", node_id=second["id"],
            expected_version=conflict["version"], start_on=day(25), target_on=day(35),
        )  # fmt: skip
        proof(
            "M1A",
            "a dependent that starts before its prerequisite is due shows a schedule conflict, "
            "which goes away when a date changes",
            conflict["schedule_conflicts"] == [first["id"]] and fixed["schedule_conflicts"] == [],
        )


# ---------------------------------------------------------------------------------------------
# 3. Working together: claims, next_work, handoffs, bulk changes, the feed (M2, M5B)
# ---------------------------------------------------------------------------------------------


async def working_together() -> None:
    chapter("Working together: ten agents, one set of tasks")
    async with connect(URL, the_token()) as client:
        # -- M2
        _, _, a, b, c = await tree(client, 3)
        await ok(
            client,
            "add_link",
            actor="m",
            source_id=a["id"],
            target_id=b["id"],
            link_type="needs_first",
        )
        await ok(
            client,
            "add_link",
            actor="m",
            source_id=b["id"],
            target_id=c["id"],
            link_type="needs_first",
        )
        await refusal(
            client, "M2", "a link that would make a circle of waiting is refused", "add_link",
            "circle of waiting", actor="m", source_id=c["id"], target_id=a["id"],
            link_type="needs_first",
        )  # fmt: skip
        waiting = await get(client, b)
        await set_status(client, c, "done")
        released = await get(client, b)
        proof(
            "M2",
            "finishing a prerequisite makes its dependent ready",
            (waiting["status"], waiting["waiting_on"]) == ("new", [c["id"]])
            and (released["status"], released["waiting_on"]) == ("ready", []),
        )
        _, _, solo = await tree(client, 1)
        results = await race_results(
            client,
            "claim_node",
            [{"actor": f"agent-{n}", "node_id": solo["id"]} for n in range(10)],
        )
        winners = [text for accepted, text in results if accepted]
        held = await get(client, solo)
        proof(
            "M2",
            "ten parallel claimers on one ready task produce exactly one winner",
            len(results) == 10 and len(winners) == 1 and held["claimed_by"] is not None,
            f"winner: {held['claimed_by']}",
        )
        _, _, lease = await tree(client, 1)
        await ok(client, "claim_node", actor="ann", node_id=lease["id"], lease_seconds=2)
        message = await refused(client, "claim_node", actor="bob", node_id=lease["id"])
        await asyncio.sleep(2.6)
        taken = await ok(client, "claim_node", actor="bob", node_id=lease["id"], lease_seconds=60)
        proof(
            "M2",
            "an expired lease can be claimed by another actor",
            "claimed by ann until" in message and taken["actor"] == "bob",
            f'while it ran: "{clip(message, 60)}"',
        )

        # -- M5B: next_work
        epic, story, *five = await tree(client, 5)
        results = await race_results(
            client, "next_work",
            [{"actor": f"agent-{n}", "within_node_id": epic["id"]} for n in range(10)],
        )  # fmt: skip
        answers = [json.loads(text) for _, text in results]
        found = [a for a in answers if a["found"]]
        nothing = [a for a in answers if not a["found"]]
        proof(
            "M5B",
            "ten agents ask for work at once on five ready tasks: five each get a different task, "
            "none twice, the rest are told plainly that nothing is free",
            len(found) == 5
            and len(nothing) == 5
            and sorted(a["work"]["id"] for a in found) == sorted(ids(five))
            and len({a["claim"]["actor"] for a in found}) == 5
            and all(a["message"].startswith("Nothing is free right now") for a in nothing),
            nothing[0]["message"],
        )

        epic, story, first, second = await tree(client, 2, body="The story in more words.")
        await ok(
            client,
            "add_link",
            actor="m",
            source_id=second["id"],
            target_id=first["id"],
            link_type="needs_first",
        )
        rule = await ok(
            client, "create_rule", actor="m", title="Invoices carry a number", story_id=story["id"]
        )
        on_epic = await make_decision(client, "Decided on the epic", node_ids=[epic["id"]])
        old = await make_decision(client, "The old way", node_ids=[story["id"]])
        new_way = await ok(
            client, "supersede_decision", actor="m", decision_id=old["id"], title="The new way",
            text="Do it the new way", reason="It changed", decided_by="Marie",
        )  # fmt: skip
        wide = await make_decision(client, "Always be careful", project_wide=True)
        known = await make_fact(client, "Invoices are sent by email", node_ids=[story["id"]])
        take = await ok(
            client, "next_work", actor="ann", within_node_id=epic["id"], context_chars=1_000_000
        )
        context = take["context"]
        project_wide = await ok(
            client, "list_decisions", project_wide=True, status="active", limit=200
        )
        proof(
            "M5B",
            "the context holds exactly the parent chain, the story's rules, the linked active "
            "decisions and facts (a replaced decision only in its own section) and every active "
            "project-wide decision",
            take["work"]["id"] == first["id"]
            and [(p["kind"], p["id"]) for p in context["parents"]]
            == [("epic", epic["id"]), ("story", story["id"])]
            and ids(context["rules"]) == [rule["id"]]
            and set(ids(context["active_decisions"])) == {on_epic["id"], new_way["id"]}
            and ids(context["past_decisions"]) == [old["id"]]
            and ids(context["facts"]) == [known["id"]]
            and set(ids(context["project_wide_decisions"])) == set(ids(project_wide["items"]))
            and wide["id"] in ids(context["project_wide_decisions"])
            and all(
                d["confirmation"] == "unconfirmed"
                for d in (*context["active_decisions"], *context["facts"])
            ),
        )
        events_before = await history(client, first)
        for blank in ("", "   "):
            message = await refused(
                client, "finish_work", actor="ann", node_id=first["id"], outcome="done", note=blank
            )
        proof(
            "M5B",
            "finish_work with a blank note is refused and changes nothing",
            "A handoff note is needed" in message and await history(client, first) == events_before,
            f'"{clip(message, 60)}"',
        )
        finished = await ok(
            client, "finish_work", actor="ann", node_id=first["id"], outcome="done",
            note="Used the invoice library; the numbering is in numbering.py.",
        )  # fmt: skip
        nxt = await ok(client, "next_work", actor="bob", within_node_id=epic["id"])
        (prerequisite,) = nxt["context"]["prerequisites"]
        proof(
            "M5B",
            "with a note the work is done, its dependent becomes ready, and the next agent's "
            "next_work shows the note",
            finished["node"]["status"] == "done"
            and [(n["id"], n["status"]) for n in finished["unblocked"]] == [(second["id"], "ready")]
            and nxt["work"]["id"] == second["id"]
            and prerequisite["handoffs"][0]["note"].startswith("Used the invoice library"),
        )
        epic, _, lapsing = await tree(client, 1)
        await ok(client, "next_work", actor="ann", within_node_id=epic["id"], lease_seconds=2)
        await asyncio.sleep(2.6)
        lapsed = await get(client, lapsing)
        proof(
            "M5B",
            "a claim that ran out shows as lapsed, with no handoff note",
            lapsed["lapsed_claim_by"] == "ann"
            and lapsed["claimed_by"] is None
            and lapsed["handoff_count"] == 0,
        )

        # -- M5B: update_nodes
        _, _, *tasks = await tree(client, 5)
        await ok(
            client,
            "update_node",
            actor="bob",
            node_id=tasks[2]["id"],
            expected_version=1,
            title="Changed under you",
        )
        result = await ok(
            client, "update_nodes", actor="ann",
            changes=[{"node_id": t["id"], "expected_version": 1, "priority": "high"}
                     for t in tasks],
        )  # fmt: skip
        stale = result["results"][2]
        mine = [e for t in tasks for e in await history(client, t) if e["actor"] == "ann"]
        proof(
            "M5B",
            "five changes, one of them stale: four apply and are in the history with their actor, "
            "one is refused with the stale-version message and changes nothing",
            (result["applied"], result["refused"]) == (4, 1)
            and "is at version 2, but you sent version 1" in stale["refusal"]
            and len(mine) == 4,
            f'"{clip(stale["refusal"], 70)}"',
        )
        _, _, *many = await tree(client, 120)
        changes = [
            {"node_id": t["id"], "expected_version": 1,
             "title": f"Renamed {n}", **({"status": "finished"} if n % 10 == 0 else {})}
            for n, t in enumerate(many)
        ]  # fmt: skip
        changes.append({"node_id": 999999999, "expected_version": 1, "title": "Nobody"})
        report = await ok(client, "update_nodes", actor="ann", changes=changes)
        proof(
            "M5B",
            "a call with 120 changes reports each one",
            len(report["results"]) == 121 and (report["applied"], report["refused"]) == (108, 13),
            "108 applied, 13 refused, each with its reason",
        )

        # -- M5B: the feed
        start = (await ok(client, "changes_since", after_event_id=0, limit=1))["latest_event_id"]
        epic, story, t1, t2, t3 = await tree(client, 3)
        await ok(
            client,
            "add_link",
            actor="m",
            source_id=t2["id"],
            target_id=t1["id"],
            link_type="needs_first",
        )
        await set_status(client, t1, "blocked")
        await ok(client, "create_rule", actor="m", title="A rule", story_id=story["id"])
        await make_decision(client, "A decision", node_ids=[epic["id"]])
        await make_fact(client, "A fact", node_ids=[story["id"]])
        expected = [
            int(x)
            for x in sql_value(
                f"SELECT string_agg(id::text, ' ' ORDER BY id) FROM events WHERE id > {start}",
                as_project=True,
            ).split()
        ]
        for _ in range(100):
            if (await ok(client, "changes_since", after_event_id=0, limit=1))[
                "latest_event_id"
            ] >= expected[-1]:
                break
            await asyncio.sleep(0.1)
        exact = True
        for limit in (1, 7, 200):
            seen, cursor = [], start
            while cursor is not None:
                page = await ok(client, "changes_since", after_event_id=cursor, limit=limit)
                seen += ids(page["items"])
                cursor = page["next_after_id"]
            exact = exact and seen == expected
        proof(
            "M5B",
            "changes_since returns exactly the events after a given id, nothing before and nothing "
            "missing, and pages correctly",
            exact and len(expected) >= 10,
            f"{len(expected)} events, paged by 1, 7 and 200",
        )
        await late_event_is_not_skipped(client, t3)

        # -- M5B: compact lists
        body = "A long description of the work, with plenty of detail. " * 90
        made = await ok(
            client, "create_plan", actor="ann",
            items=[{"key": "e", "kind": "epic", "title": "Long bodies"},
                   *({"key": f"s{n}", "kind": "story", "title": f"Story {n}", "parent": "e",
                      "body": body} for n in range(50))],
        )  # fmt: skip
        compact = await ok(client, "list_nodes", parent_id=made["created"][0]["id"], limit=50)
        full = await ok(
            client, "list_nodes", parent_id=made["created"][0]["id"], limit=50, detail=True
        )
        small, big = len(json.dumps(compact)), len(json.dumps(full))
        proof(
            "M5B",
            "a list of 50 items with long bodies is at least five times smaller by default than "
            "with the detail option",
            len(compact["items"]) == 50 and big >= 5 * small,
            f"{small:,} characters by default, {big:,} with detail ({big / small:.1f} times)",
        )


async def late_event_is_not_skipped(client: Client, task: dict[str, Any]) -> None:
    """Two changes are written at the same moment. `fast` begins first but writes its event later
    (so it gets the higher id), and finishes first. `slow` begins second, writes its event first
    (the lower id) and finishes last. A feed that went by id alone would show `fast` and then never
    `slow`."""
    start = (await ok(client, "changes_since", after_event_id=0, limit=1))["latest_event_id"]
    marker = unique_word()
    fast, slow = f"fast-{marker}", f"slow-{marker}"

    def insert(actor: str) -> str:
        return (
            "INSERT INTO events (node_id, action, actor, after) "
            f"VALUES ({task['id']}, 'update', '{actor}', '{{\"title\": \"{actor}\"}}')"
        )

    def write(sql: str) -> None:
        result = run_sql(sql)
        if result.returncode != 0:
            raise Failed(f"the database said: {result.stderr}")

    fast_thread = threading.Thread(
        target=write,
        args=(f"SELECT pg_current_xact_id(); SELECT pg_sleep(8); {insert(fast)}",),
        daemon=True,
    )
    slow_thread = threading.Thread(
        target=write, args=(f"{insert(slow)}; SELECT pg_sleep(12)",), daemon=True
    )
    fast_thread.start()
    for _ in range(60):
        running = sql_value(
            "SELECT count(*) FROM pg_stat_activity WHERE backend_xid IS NOT NULL "
            f"AND query LIKE '%{fast}%' AND pid <> pg_backend_pid()"
        )
        if running == "1":
            break
        await asyncio.sleep(0.3)
    else:
        raise Failed("the first writer never started")
    slow_thread.start()
    fast_thread.join(timeout=60)

    async def feed(after: int) -> list[int]:
        found, cursor = [], after
        while cursor is not None:
            page = await ok(client, "changes_since", after_event_id=cursor, limit=50)
            found += ids(page["items"])
            cursor = page["next_after_id"]
        return found

    shown = await feed(start)
    still_writing = slow_thread.is_alive()
    slow_thread.join(timeout=60)
    slow_id = int(sql_value(f"SELECT id FROM events WHERE actor = '{slow}'"))
    fast_id = int(sql_value(f"SELECT id FROM events WHERE actor = '{fast}'"))
    for _ in range(100):
        if (await ok(client, "changes_since", after_event_id=0, limit=1))[
            "latest_event_id"
        ] >= slow_id:
            break
        await asyncio.sleep(0.1)
    later = await feed(fast_id)
    everything = await feed(start)
    proof(
        "M5B",
        "an event written by a slow change that finishes after a later one is still shown, once "
        "it has finished",
        still_writing
        and slow_id < fast_id
        and shown == [fast_id]
        and later == [slow_id]
        and everything == [fast_id, slow_id],
        f"the slow change has the lower id ({slow_id} < {fast_id}) and came after the fast one",
    )


# ---------------------------------------------------------------------------------------------
# 4. Requirements and decisions (M3, M4)
# ---------------------------------------------------------------------------------------------


def entries(section: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """The items of one section of a recall, which wraps each one with why it is there."""
    return [entry[key] for entry in section["items"]]


async def whole_coverage(client: Client) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The unplaced rules and the stories without rules, each list read to its end."""
    lists = {}
    for show, key, cursor in (
        ("unplaced_rules", "unplaced_rules", "unplaced_after_id"),
        ("stories_without_rules", "stories_without_rules", "stories_after_id"),
    ):
        found: list[dict[str, Any]] = []
        after = None
        while True:
            arguments: dict[str, Any] = {"show": show, "limit": 200}
            if after is not None:
                arguments[cursor] = after
            page = await ok(client, "coverage_report", **arguments)
            found += page[key]
            after = page[f"next_{cursor.removesuffix('_after_id')}_after_id"]
            if after is None:
                break
        lists[key] = found
    return lists["unplaced_rules"], lists["stories_without_rules"]


async def requirements_and_decisions() -> None:
    chapter("Requirements and decisions: a rule in exactly one story, decisions you can recall")
    async with connect(URL, the_token()) as client:
        # -- M3
        epic, first, second, bare = await stories(client, 3)
        rule = await ok(client, "create_rule", actor="ann", title="Every invoice has a number")
        placed = await ok(
            client, "place_rule", actor="ann", rule_id=rule["id"], expected_version=1,
            story_id=first["id"],
        )  # fmt: skip
        message = await refused(
            client, "place_rule", actor="bob", rule_id=rule["id"], expected_version=2,
            story_id=second["id"],
        )  # fmt: skip
        in_second = await ok(client, "list_rules", story_id=second["id"])
        proof(
            "M3",
            "a rule cannot be placed in two stories",
            placed["story_id"] == first["id"]
            and f"already placed in story {first['id']}" in message
            and "move_rule" in message
            and in_second["items"] == [],
            f'"{clip(message, 90)}"',
        )
        contenders = await race_results(
            client, "move_rule",
            [{"actor": f"agent-{n}", "rule_id": rule["id"], "expected_version": 2,
              "new_story_id": second["id"]} for n in range(6)],
        )  # fmt: skip
        events = (await ok(client, "rule_history", rule_id=rule["id"], limit=200))["items"]
        move = events[-1]
        in_first_history = ids(await history(client, first))
        in_second_history = ids(await history(client, second))
        proof(
            "M3",
            "a move is atomic and logged: six at once from the same version, one wins, and the "
            "move is in the history of the rule and of both stories",
            sum(accepted for accepted, _ in contenders) == 1
            and [e["action"] for e in events] == ["rule_created", "rule_placed", "rule_moved"]
            and move["before"]["story_id"] == first["id"]
            and move["after"]["story_id"] == second["id"]
            and move["id"] in in_first_history
            and move["id"] in in_second_history,
            f"moved by {move['actor']}",
        )
        before = rows("events")
        message = await refused(
            client, "move_rule", actor="ann", rule_id=rule["id"], expected_version=3,
            new_story_id=987654321,
        )  # fmt: skip
        proof(
            "M3",
            "a move that cannot happen is refused, changes nothing and logs nothing",
            "There is no node with id 987654321" in message and rows("events") == before,
            f'"{clip(message, 90)}"',
        )
        loose = await ok(client, "create_rule", actor="ann", title="Not in any story yet")
        unplaced, without = await whole_coverage(client)
        proof(
            "M3",
            "the coverage report lists unplaced rules and stories with none",
            loose["id"] in ids(unplaced) and bare["id"] in ids(without)
            and rule["id"] not in ids(unplaced) and second["id"] not in ids(without),
            f"{len(unplaced)} unplaced rules, {len(without)} stories without rules",
        )  # fmt: skip
        await ok(
            client, "place_rule", actor="ann", rule_id=loose["id"], expected_version=1,
            story_id=bare["id"],
        )  # fmt: skip
        unplaced, without = await whole_coverage(client)
        proof(
            "M3",
            "placing the loose rule in the bare story closes both gaps",
            loose["id"] not in ids(unplaced) and bare["id"] not in ids(without),
        )

        # -- M4
        epic, story, other = await stories(client, 2)
        mine = await make_decision(client, "About this story", node_ids=[story["id"]])
        elsewhere = await make_decision(client, "About the other story", node_ids=[other["id"]])
        unlinked = await make_decision(client, "About nothing in particular")
        linked_wide = await make_decision(
            client, "A linked project-wide one", project_wide=True, node_ids=[story["id"]]
        )
        free_wide = await make_decision(client, "A free project-wide one", project_wide=True)
        recalled = await ok(client, "recall", node_id=story["id"])
        every_wide = await ok(
            client, "list_decisions", project_wide=True, status="active", limit=200
        )
        proof(
            "M4",
            "recalling a story returns its linked active decisions and every active project-wide "
            "decision, linked or not",
            ids(entries(recalled["active_decisions"], "decision")) == [mine["id"]]
            and elsewhere["id"] not in ids(entries(recalled["active_decisions"], "decision"))
            and unlinked["id"] not in ids(entries(recalled["active_decisions"], "decision"))
            and ids(entries(recalled["project_wide_decisions"], "decision"))
            == ids(every_wide["items"])
            and {linked_wide["id"], free_wide["id"]} <= set(ids(every_wide["items"])),
            f"{len(every_wide['items'])} project-wide decisions in force",
        )  # fmt: skip
        new = await ok(
            client, "supersede_decision", actor="agent-2", decision_id=mine["id"],
            title="Use a table instead of a queue", text="A table, polled by the worker.",
            reason="The queue was more than we need.", decided_by="Ann",
        )  # fmt: skip
        after = await ok(client, "recall", node_id=story["id"])
        old = await ok(client, "get_decision", decision_id=mine["id"])
        superseded = entries(after["past_decisions"], "decision")
        proof(
            "M4",
            "a superseded decision is shown as such and not as active, with what replaced it",
            ids(entries(after["active_decisions"], "decision")) == [new["id"]]
            and [(d["id"], d["status"], d["superseded_by_id"]) for d in superseded]
            == [(mine["id"], "superseded", new["id"])]
            and old["text"] == mine["text"] and old["decided_by"] == mine["decided_by"],
        )  # fmt: skip
        quiet = await ok(client, "recall", node_id=await new_story(client))
        proof(
            "M4",
            "nothing is invented: a story with no decisions of its own gets none, and nothing but "
            "what applies throughout the project",
            quiet["active_decisions"]["count"] == 0
            and quiet["past_decisions"]["count"] == 0
            and quiet["facts"]["count"] == 0
            and ids(entries(quiet["project_wide_decisions"], "decision"))
            == ids(every_wide["items"]),
        )


async def stories(client: Client, count: int) -> tuple[dict[str, Any], ...]:
    """A new epic and `count` stories under it."""
    epic = await ok(client, "create_node", actor="marie", kind="epic", title="An epic")
    return (
        epic,
        *[
            await ok(
                client, "create_node", actor="marie", kind="story", title=f"Story {n}",
                parent_id=epic["id"],
            )
            for n in range(count)
        ],
    )  # fmt: skip


async def new_story(client: Client) -> int:
    (_, story) = await stories(client, 1)
    return int(story["id"])


# ---------------------------------------------------------------------------------------------
# 5. Remembering: facts and decisions, who reviewed them, and what happens when one is wrong
#    (M4A, and the two records of M16)
# ---------------------------------------------------------------------------------------------


def database_refuses(sql: str, says: str) -> bool:
    attempt = run_sql(sql)
    return attempt.returncode != 0 and says in attempt.stderr


def mine() -> str:
    """What a statement run as the database's owner must say to touch the shared test project and
    nothing else: each project numbers its own records, so ids collide between projects."""
    return f"project_id = {the_project().id}"


async def remembering() -> None:
    chapter(
        "Remembering: facts with sources, decisions with reasons, who reviewed them, "
        "and what happens when one is wrong"
    )
    async with connect(URL, the_token()) as client:
        epic, story, other = await stories(client, 2)
        task = await ok(
            client, "create_node", actor="m", kind="task", title="A task", parent_id=story["id"]
        )
        rule = await ok(client, "create_rule", actor="m", title="A rule", story_id=story["id"])

        # -- facts
        fact = await make_fact(
            client, "The tests take an hour.", node_ids=[story["id"]], source="The Monday call"
        )
        by_a_person = await make_fact(client, "Said by Marie herself", actor="Marie")
        recalled = await ok(client, "recall", node_id=story["id"])
        shown = entries(recalled["facts"], "fact")
        proof(
            "M4A",
            "a fact retained against a story is in its recall under facts with its source, "
            "recorder and time, shown as not confirmed by a person, and never under decisions",
            ids(shown) == [fact["id"]]
            and (shown[0]["source"], shown[0]["recorded_by"], shown[0]["confirmation"])
            == ("The Monday call", "agent-1", "unconfirmed")
            and shown[0]["recorded_at"]
            and shown[0]["label"].startswith("Fact, not confirmed by a person")
            and fact["id"] not in ids(entries(recalled["active_decisions"], "decision")),
            f'"{shown[0]["label"]}", recorded {shown[0]["recorded_at"][:10]}',
        )
        proof(
            "M16",
            "recording a fact never confirms it, even when the agent that records it is called by "
            "a person's name",
            (by_a_person["recorded_by"], by_a_person["confirmation"], by_a_person["confirmed_by"])
            == ("Marie", "unconfirmed", None),
        )
        await refusal(
            client, "M4A", "a fact with a blank source is refused", "retain_fact",
            "A fact needs its source", actor="a", text="Something", source="  ",
        )  # fmt: skip

        # -- who reviewed it, and when
        await refusal(
            client, "M16", "confirming needs the named person who reviewed it", "set_confirmation",
            "A confirmation needs the person who reviewed the record", actor="Marie", kind="fact",
            record_id=fact["id"], confirmation="confirmed", person=" ",
        )  # fmt: skip
        reviewed = await confirm(client, "fact", fact, "Marie", quote="I read the timing")
        recalled = await ok(client, "recall", node_id=story["id"])
        (now,) = entries(recalled["facts"], "fact")
        history_of = (await ok(client, "fact_history", fact_id=fact["id"]))["items"]
        proof(
            "M4A",
            "confirming a fact with set_confirmation records who and when, and recall then shows "
            "it; the agent that made the call is kept apart from the person",
            (reviewed["confirmation"], reviewed["confirmed_by"]) == ("confirmed", "Marie")
            and reviewed["confirmed_at"]
            and (now["confirmation"], now["confirmed_by"], now["confirmed_at"])
            == ("confirmed", "Marie", reviewed["confirmed_at"])
            and now["label"].startswith("Fact, confirmed by Marie on ")
            and (history_of[-1]["action"], history_of[-1]["actor"]) == ("fact_confirmed", "agent-1")
            and history_of[-1]["after"]["confirmed_by"] == "Marie",
            f"by Marie at {reviewed['confirmed_at'][:16]}",
        )

        # -- the exact links, and nothing else
        elsewhere = await make_fact(client, "Linked to the other story", node_ids=[other["id"]])
        on_task = await make_fact(client, "Linked to the task", node_ids=[task["id"]])
        on_rule = await make_fact(client, "Linked to the rule", rule_ids=[rule["id"]])
        loose = await make_fact(client, "Linked to nothing")
        recalled = await ok(client, "recall", node_id=story["id"])
        seen = set(ids(entries(recalled["facts"], "fact")))
        proof(
            "M4A",
            "the recall holds exactly what the exact links give and nothing else",
            fact["id"] in seen
            and not {elsewhere["id"], on_task["id"], on_rule["id"], loose["id"]} & seen,
            f"{len(seen)} fact linked to this story",
        )

        # -- replaced and withdrawn
        outdated = await make_fact(client, "The suite has 400 tests.", node_ids=[story["id"]])
        wrong = await make_fact(client, "The suite is the slow part.", node_ids=[story["id"]])
        change = await ok(
            client, "supersede_fact", actor="a", replaces=[outdated["id"]],
            reason="Counted again", text="The suite has 450 tests.", source="A recount",
        )  # fmt: skip
        await ok(
            client, "withdraw_fact", actor="a", fact_ids=[wrong["id"]],
            reason="The benchmark ran on the wrong branch",
        )  # fmt: skip
        old = await ok(client, "get_fact", fact_id=outdated["id"])
        gone = await ok(client, "get_fact", fact_id=wrong["id"])
        recalled = await ok(client, "recall", node_id=story["id"])
        past = {f["id"]: f for f in entries(recalled["past_facts"], "fact")}
        in_force = set(ids(entries(recalled["facts"], "fact")))
        proof(
            "M4A",
            "a superseded fact shows as superseded with its replacement and a withdrawn one as "
            "withdrawn with its reason, among the past facts and never among those in force",
            (old["status"], old["superseded_by_id"], old["text"])
            == ("superseded", change["replacement"]["id"], outdated["text"])
            and (gone["status"], gone["status_reason"])
            == ("withdrawn", "The benchmark ran on the wrong branch")
            and set(past) == {outdated["id"], wrong["id"]}
            and not set(past) & in_force
            and change["replacement"]["id"] in in_force
            and "Superseded fact" in past[outdated["id"]]["label"]
            and "Withdrawn fact" in past[wrong["id"]]["label"],
            f"{old['label']}; {gone['label']}",
        )

        # -- replacing several at once
        one, two, three = [await make_fact(client, f"Fact {n}") for n in range(3)]
        both = await ok(
            client, "supersede_fact", actor="agent-1", replaces=[one["id"], two["id"]],
            reason="Marie says it is the other way round", text="The opposite of the first two",
            source="Marie, in the planning call",
        )  # fmt: skip
        new = both["replacement"]
        states = [
            (await ok(client, "get_fact", fact_id=f["id"]))["status"] for f in (one, two, three)
        ]
        proof(
            "M4A",
            "a new fact that contradicts two of three replaces both in one supersede_fact call, "
            "each old one shows as superseded by it, and the third is untouched",
            both["superseded_ids"] == [one["id"], two["id"]]
            and states == ["superseded", "superseded", "active"]
            and (await ok(client, "get_fact", fact_id=one["id"]))["superseded_by_id"] == new["id"],
        )
        four, five = await make_fact(client, "Four"), await make_fact(client, "Five")
        await ok(client, "withdraw_fact", actor="a", fact_ids=[five["id"]], reason="It was wrong")
        before = rows("facts", "events")
        message = await refused(
            client, "supersede_fact", actor="a", replaces=[four["id"], five["id"]], reason="r",
            text="Replacement", source="s",
        )  # fmt: skip
        proof(
            "M4A",
            "a listed fact that is already withdrawn refuses the whole call and changes nothing",
            f"Fact {five['id']} was withdrawn (It was wrong), so it cannot be replaced" in message
            and "Nothing was changed" in message
            and (await ok(client, "get_fact", fact_id=four["id"]))["status"] == "active"
            and rows("facts", "events") == before,
            f'"{clip(message, 80)}"',
        )
        thirty = [await make_fact(client, f"Thirty {n}") for n in range(30)]
        many = await ok(
            client, "supersede_fact", actor="a", replaces=ids(thirty), reason="All of them",
            text="One fact instead", source="s",
        )  # fmt: skip
        proof(
            "M4A",
            "a call that replaces 30 facts at once works",
            many["superseded_ids"] == ids(thirty),
        )

        # -- listing
        e2, s2, t2 = await tree(client, 1)
        r2 = await ok(client, "create_rule", actor="m", title="R", story_id=s2["id"])
        e3, s3, *_ = await tree(client, 0)
        for n in range(20):
            await make_fact(client, f"On a task {n}", node_ids=[t2["id"]])
            await make_fact(client, f"On a story {n}", node_ids=[s2["id"]])
            await make_fact(client, f"On a rule {n}", rule_ids=[r2["id"]])
        for n in range(5):
            await make_fact(client, f"Elsewhere {n}", node_ids=[s3["id"]])
        walked: list[int] = []
        cursor = None
        pages = 0
        first_total = None
        while True:
            page = await ok(
                client, "list_facts", within_node_id=e2["id"], limit=25, after_id=cursor
            )
            first_total = page["total"] if first_total is None else first_total
            walked += ids(page["items"])
            pages += 1
            cursor = page["next_after_id"]
            if cursor is None:
                break
        everything = await ok(client, "list_facts", within_node_id=e2["id"], limit=200)
        proof(
            "M4A",
            "list_facts over 60 facts, in pages, returns every one exactly once and a count that "
            "matches; within an epic it returns what concerns its stories, tasks and rules and "
            "nothing else",
            len(walked) == len(set(walked)) == first_total == 60 == len(everything["items"])
            and pages == 3,
            f"{pages} pages of 25",
        )

        # -- a person's word, for what a person confirmed
        sure = await make_fact(client, "A statement Marie confirmed")
        await confirm(client, "fact", sure, "Marie")
        message = await refused(
            client, "withdraw_fact", actor="agent-2", fact_ids=[sure["id"]], reason="r"
        )
        under_her_name = await refused(
            client, "withdraw_fact", actor="Marie", fact_ids=[sure["id"]], reason="r"
        )
        await ok(
            client, "withdraw_fact", actor="agent-2", fact_ids=[sure["id"]],
            reason="She changed her mind",
            said_by="Marie", quote="In the planning call: 'no longer how we think about it'",
        )  # fmt: skip
        last = (await ok(client, "fact_history", fact_id=sure["id"]))["items"][-1]
        ended = await ok(client, "get_fact", fact_id=sure["id"])
        idea = await make_fact(client, "Just an idea")
        alone = await ok(
            client, "withdraw_fact", actor="agent-3", fact_ids=[idea["id"]], reason="r"
        )
        proof(
            "M4A",
            "replacing or withdrawing a confirmed fact needs said_by and quote, and then works "
            "with both the person and the agent in the history; an agent alone can withdraw a "
            "fact nobody confirmed",
            f"fact {sure['id']} (confirmed by Marie)" in message
            and "needs that person's word" in message
            and "said_by" in message
            and f"fact {sure['id']} (confirmed by Marie)" in under_her_name
            and (last["actor"], last["after"]["said_by"]) == ("agent-2", "Marie")
            and alone["withdrawn_ids"] == [idea["id"]],
            f'"{clip(message, 80)}"',
        )
        proof(
            "M16",
            "a record keeps the confirmation it had when it ends: a confirmed fact that is "
            "withdrawn is shown as withdrawn and still as confirmed by Marie, and no name alone "
            "got past the refusal",
            (ended["status"], ended["confirmation"], ended["confirmed_by"])
            == ("withdrawn", "confirmed", "Marie")
            and ended["label"].startswith("Withdrawn fact, confirmed by Marie on ")
            and f"fact {sure['id']} (confirmed by Marie)" in under_her_name,
            ended["label"],
        )

        # -- the database on its own
        target = (await make_fact(client, "Kept as a record"))["id"]
        decided = (await make_decision(client, "Kept as a record too"))["id"]
        proof(
            "M4A",
            "the database refuses, on its own, edits and deletes of facts and of decisions",
            database_refuses(
                f"UPDATE facts SET text = 'x' WHERE {mine()} AND id = {target}",
                "cannot be edited",
            )
            and database_refuses(
                f"DELETE FROM facts WHERE {mine()} AND id = {target}", "cannot be deleted"
            )
            and database_refuses(
                f"UPDATE decisions SET text = 'x' WHERE {mine()} AND id = {decided}",
                "cannot be edited",
            )
            and database_refuses(
                f"DELETE FROM decisions WHERE {mine()} AND id = {decided}", "cannot be deleted"
            ),
        )

        # -- M16: a decision, its reason and who decided it, found the same everywhere
        choice = await make_decision(
            client, "Use a table, not a queue", node_ids=[story["id"]], rule_ids=[rule["id"]],
            reason="The queue was more than we need.", decided_by="Ann",
        )  # fmt: skip
        every_decision: list[dict[str, Any]] = []
        cursor = None
        while True:
            page = await ok(client, "list_decisions", limit=200, after_id=cursor)
            every_decision += page["items"]
            cursor = page["next_after_id"]
            if cursor is None:
                break
        in_list = [d for d in every_decision if story["id"] in d["node_ids"]]
        recalled = await ok(client, "recall", node_id=story["id"])
        in_recall = entries(recalled["active_decisions"], "decision")
        on_the_rule = entries(
            (await ok(client, "recall", rule_id=rule["id"]))["active_decisions"], "decision"
        )
        facts_in_recall = {f["id"]: f for f in entries(recalled["facts"], "fact")}
        checks = {
            "its reason and decider": (choice["reason"], choice["decided_by"])
            == ("The queue was more than we need.", "Ann")
            and bool(choice["recorded_at"]),
            "what it concerns": (choice["node_ids"], choice["rule_ids"])
            == ([story["id"]], [rule["id"]]),
            "get": await ok(client, "get_decision", decision_id=choice["id"]) == choice,
            "list": in_list == [choice],
            "recall of the story": in_recall == [choice],
            "recall of the requirement": on_the_rule == [choice],
            "the fact": facts_in_recall.get(fact["id"])
            == await ok(client, "get_fact", fact_id=fact["id"]),
        }
        wrong_ones = [name for name, held in checks.items() if not held]
        proof(
            "M16",
            "a fact with its source and a decision with its reason and named decider, linked to "
            "work, are found with the same ids and fields by get, by list and by exact recall",
            not wrong_ones,
            f"differs: {', '.join(wrong_ones)}"
            if wrong_ones
            else f"decision {choice['id']} decided by Ann, fact {fact['id']} from {fact['source']}",
        )
        decided_ok = await confirm(client, "decision", choice, "Marie", quote="Yes, that was it")
        events = (await ok(client, "decision_history", decision_id=choice["id"]))["items"]
        proof(
            "M16",
            "a decision is confirmed through the same operation, with who confirmed it and when, "
            "attributed to the agent that made the call and logged",
            (decided_ok["confirmation"], decided_ok["confirmed_by"]) == ("confirmed", "Marie")
            and decided_ok["confirmed_at"]
            and (events[-1]["action"], events[-1]["actor"]) == ("decision_confirmed", "agent-1")
            and events[-1]["at"],
            f"confirmed by Marie at {decided_ok['confirmed_at'][:16]}",
        )
        no_word = await refused(
            client, "supersede_decision", actor="Marie", decision_id=choice["id"],
            title="Use a queue after all", text="A queue.", reason="It scales.", decided_by="Ann",
        )  # fmt: skip
        before = rows("decisions", "facts", "events")
        blank_reason = await refused(
            client, "record_decision", actor="a", title="No reason", text="x", reason="  ",
            decided_by="Marie", node_ids=[story["id"]],
        )  # fmt: skip
        blank_source = await refused(
            client, "retain_fact", actor="a", text="x", source="", node_ids=[story["id"]]
        )
        proof(
            "M16",
            "a blank source or reason, and a change to a confirmed decision under a person's name "
            "alone, are refused in plain words and write nothing, not even a link",
            "A decision needs a reason" in blank_reason
            and "A fact needs its source" in blank_source
            and f"decision {choice['id']} (confirmed by Marie)" in no_word
            and "needs that person's word" in no_word
            and rows("decisions", "facts", "events") == before
            and (await ok(client, "get_decision", decision_id=choice["id"]))["status"] == "active",
            f'"{clip(no_word, 80)}"',
        )
        changed = await ok(
            client, "supersede_decision", actor="agent-2", decision_id=choice["id"],
            title="Use a queue after all", text="A queue, as first thought.",
            reason="It scales better.", decided_by="Ann",
            said_by="Marie", quote="Let us use a queue",
        )  # fmt: skip
        dropped = await make_decision(client, "A dropped idea", node_ids=[story["id"]])
        await ok(
            client, "withdraw_decision", actor="agent-2", decision_id=dropped["id"],
            reason="No longer needed",
        )  # fmt: skip
        first = await ok(client, "get_decision", decision_id=choice["id"])
        second_one = await ok(client, "get_decision", decision_id=dropped["id"])
        recalled = await ok(client, "recall", node_id=story["id"])
        past_decisions = {d["id"]: d for d in entries(recalled["past_decisions"], "decision")}
        proof(
            "M16",
            "replacing and withdrawing keep the old decisions as records: the replaced one is "
            "superseded but still confirmed by Marie, the new one starts unconfirmed, and the "
            "withdrawn one says why",
            (first["status"], first["confirmation"], first["confirmed_by"])
            == ("superseded", "confirmed", "Marie")
            and (changed["status"], changed["confirmation"], changed["supersedes_ids"])
            == ("active", "unconfirmed", [choice["id"]])
            and (second_one["status"], second_one["status_reason"])
            == ("withdrawn", "No longer needed")
            and set(past_decisions) == {choice["id"], dropped["id"]}
            and "Superseded decision, confirmed by Marie" in past_decisions[choice["id"]]["label"]
            and changed["id"] in ids(entries(recalled["active_decisions"], "decision")),
            first["label"],
        )

        # -- M16: a decision that applies throughout a project, in that project's briefings only
        wide = await make_decision(client, "Everything is in euros", project_wide=True)
        stranger = make_project(register())
        async with connect(URL, stranger.key) as theirs:
            their_epic, their_story = await stories(theirs, 1)
            await ok(
                theirs, "create_node", actor="m", kind="task", title="Theirs",
                parent_id=their_story["id"],
            )  # fmt: skip
            their_brief = await ok(
                theirs, "next_work", actor="x", within_node_id=their_epic["id"], peek=True
            )
            their_recall = await ok(theirs, "recall", node_id=their_story["id"])
            their_list = await ok(theirs, "list_decisions", limit=200)
        brief = await ok(client, "next_work", actor="x", within_node_id=epic["id"], peek=True)
        recalled = await ok(client, "recall", node_id=story["id"])
        recalled_other = await ok(client, "recall", node_id=other["id"])
        proof(
            "M16",
            "a project-wide decision is in every recall and every next_work briefing of its "
            "project, linked to nothing, and in no other project's",
            wide["project_wide"] is True
            and (wide["node_ids"], wide["rule_ids"]) == ([], [])
            and wide["id"] in ids(brief["context"]["project_wide_decisions"])
            and wide["id"] in ids(entries(recalled["project_wide_decisions"], "decision"))
            and wide["id"] in ids(entries(recalled_other["project_wide_decisions"], "decision"))
            and wide["id"] not in ids(entries(recalled["active_decisions"], "decision"))
            and their_brief["context"]["project_wide_decisions"] == []
            and their_recall["project_wide_decisions"]["count"] == 0
            and their_list["items"] == [],
            "yours holds it, theirs holds none",
        )
        assert epic and rule
