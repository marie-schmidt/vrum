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
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import AsyncExitStack, contextmanager
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.shared.exceptions import MCPError

from tests.helpers import (
    REPO_ROOT,
    compose,
    confirm_on_board,
    connect,
    connect_as,
    day,
    export_everything,
    make_decision,
    make_fact,
    make_note,
    ok,
    on_board,
    race_agents,
    refused,
    run_sql,
    set_definition_on_board,
    the_token,
    unique_word,
    whole_coverage_report,
)
from tests.world import (
    HARNESS_AGENT,
    Person,
    Project,
    Reply,
    make_key,
    make_limited_key,
    make_project,
    register,
    the_project,
)

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
    """The ids of rules, decisions, facts or events (an epic, story or task has a reference)."""
    return [item["id"] for item in items]


def refs(items: list[dict[str, Any]]) -> list[str]:
    """The references of epics, stories or tasks: story:12."""
    return [item["ref"] for item in items]


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
    epic = await ok(client, "create_node", kind="epic", title="An epic")
    made = await ok(
        client, "create_node", kind="story", title="A story", parent=epic["ref"], **story
    )
    found = [
        await ok(client, "create_node", kind="task", title=f"Task {n}", parent=made["ref"])
        for n in range(tasks)
    ]
    return (epic, made, *found)


async def get(client: Client, node: dict[str, Any]) -> dict[str, Any]:
    return await ok(client, "get_node", node=node["ref"])


async def history(client: Client, node: dict[str, Any]) -> list[dict[str, Any]]:
    return list((await ok(client, "node_history", node=node["ref"], limit=200))["items"])


async def set_status(client: Client, node: dict[str, Any], status: str) -> dict[str, Any]:
    current = await get(client, node)
    return await ok(
        client, "update_node", node=node["ref"], expected_version=current["version"], status=status
    )


class Agents:
    """The agents of one chapter. Who an agent is comes from its key, so each name given here is a
    key of its own (the name with a word that only this chapter uses), connected the first time it
    is asked for: `await agents("ann")` is a client of that agent, and `agents.named("ann")` is the
    name the history and the claims say. Use it as `async with Agents() as agents:`."""

    def __init__(self) -> None:
        self.word = unique_word()
        self._stack = AsyncExitStack()
        self._clients: dict[str, Client] = {}

    async def __aenter__(self) -> "Agents":
        await self._stack.__aenter__()
        return self

    async def __aexit__(self, *error: Any) -> bool | None:
        return await self._stack.__aexit__(*error)

    def named(self, name: str) -> str:
        return f"{name}-{self.word}"

    async def __call__(self, name: str) -> Client:
        if name not in self._clients:
            self._clients[name] = await self._stack.enter_async_context(
                connect_as(self.named(name))
            )
        return self._clients[name]

    async def several(self, count: int, name: str = "agent") -> list[Client]:
        """`count` agents of their own, such as ten that ask for work at the same moment."""
        return [await self(f"{name}-{n}") for n in range(count)]


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
        "is the database, the server, the embedding service, the mail and sign-in stand-ins and "
        "the code-mode sandbox, and the server's environment sets no model",
        services
        == {
            "app",
            "app-dex",
            "db",
            "dex",
            "ldap",
            "sandbox",
            "scripted-embeddings",
            "scripted-github",
            "scripted-mail",
            "scripted-oidc",
            "scripted-webhooks",
        }
        and not settings
        and environment,
        ", ".join(sorted(services)),
    )
    proof(
        "M16",
        "the live tool list has no generation tool and no belief, principle or memory tool, and "
        "neither the instructions agents are given nor server_status say anything of a model",
        tools
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
    known = the_project().owner.call("GET", "/board/api/tools")
    every = known.body["tools"]
    async with connect(URL, the_token()) as client:
        tools = [tool.name for tool in (await client.list_tools()).tools]
        usable = sorted(t["name"] for t in every if not t["people_only"] and not t["code_mode"])
        proof(
            "M0",
            "a real MCP client lists the tools: all the server has except the ones only a person "
            "can use and the two of code mode, which this key does not call by",
            sorted(tools) == usable,
            f"{len(tools)} of the server's {len(every)} tools",
        )
        server = await ok(client, "server_status")
        proof(
            "M0",
            "the server reports its database and vector extension",
            server["postgres_version"].startswith("17") and server["pgvector_version"],
            f"PostgreSQL {server['postgres_version']}, pgvector {server['pgvector_version']}",
        )
        standard = make_limited_key(the_project(), f"standard-{unique_word()}", None)
        async with connect(URL, standard) as agent:
            listed_tools = (await agent.list_tools()).tools
            offered = {tool.name for tool in listed_tools}
            takes_an_ask = {
                tool.name
                for tool in listed_tools
                if "ask" in tool.input_schema.get("properties", {})
            }
            named = set(re.findall(r"`([a-z_]+)`", agent.instructions or ""))
        flagged = {t["name"] for t in every if t["offered_to_agents"]}
        proof(
            "M28",
            "an agent key made with no list of its own is offered only the standard set, no more "
            "than the most clients take, and nothing a person does on the board",
            offered == flagged
            and 0 < len(offered) <= known.body["most_agent_tools"]
            and not offered & {"set_confirmation", "set_definition_of_done", "export_graph"},
            f"{len(offered)} of {len(every)} tools",
        )
        proof(
            "M45",
            "a standard agent key is still offered at most 25 tools, and none of the three that "
            "belong to asks (list_asks, needs_a_person, and answer_ask, which only a person can "
            "use and which no key can be given): an agent asks with the optional ask of "
            "finish_work and update_node, which it already has, and a person answers on the board",
            len(offered) <= known.body["most_agent_tools"]
            and not offered & {"list_asks", "needs_a_person", "answer_ask"}
            and takes_an_ask == {"finish_work", "update_node"} <= offered
            and next(t for t in every if t["name"] == "answer_ask")["people_only"]
            and not next(t for t in every if t["name"] == "list_asks")["offered_to_agents"],
            f"{len(offered)} tools offered",
        )
        kept_for_people = {"approve_plan", "remove_gate", "decide_placement"}
        for_agents_one_by_one = {
            "add_gate",
            "save_plan_template",
            "delete_plan_template",
            "propose_placement",
        }
        reads = {"suggest_parent", "list_plans", "list_plan_templates", "list_placements"}
        by_name = {tool.name: tool for tool in (await client.list_tools()).tools}
        by_server = {t["name"]: t for t in every}
        people = {t["name"] for t in every if t["people_only"]}
        saying_so = {
            name
            for name in reads
            if by_name[name].annotations is not None and by_name[name].annotations.read_only_hint  # type: ignore[union-attr]
        }
        schema_of_get_node = by_name["get_node"].output_schema or {}
        proof(
            "M47",
            "the tools of gates, plan approval, plan templates and moving work are on the server "
            "as the server lists them: the ones that decide for a person (approve_plan, "
            "remove_gate, decide_placement) are for people only and no key can have them, the "
            "ones that only read (suggest_parent, list_plans, list_plan_templates, "
            "list_placements) say so, the standard agent set offers none of them and is still no "
            "longer than the most clients take, get_node tells which gates hold an item and "
            "what holds it, and add_link names discovered_from",
            kept_for_people <= people
            and not for_agents_one_by_one & people
            and (kept_for_people | for_agents_one_by_one | reads) <= set(by_server)
            and saying_so == reads
            and not (kept_for_people | for_agents_one_by_one | reads) & offered
            and len(offered) <= known.body["most_agent_tools"]
            and {"gates", "held_by"} <= set(schema_of_get_node.get("properties", {}))
            and "discovered_from" in json.dumps(by_name["add_link"].input_schema),
            f"{len(every)} tools, {len(people)} for people only",
        )
        proof(
            "M5B",
            "the instructions an agent is shown name only tools it is offered, and the two of "
            "code mode, which a key that runs code is offered instead",
            named and named <= offered | {"explore", "run"},
            f"{len(named)} tools named",
        )
        async with connect(
            URL, make_key(the_project(), f"coder-{unique_word()}", code_mode=True)
        ) as coder:
            by_code = {tool.name for tool in (await coder.list_tools()).tools}
        proof(
            "M37",
            "a key made to call Vrum by running code is offered exactly explore and run, which the "
            "instructions name",
            by_code == {"explore", "run"} <= named,
            ", ".join(sorted(by_code)),
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
        "the Python example in docs/agents.md connects with an agent key and gets a list of work",
        ran.returncode == 0 and "is_error=False" in ran.stdout and "'items'" in ran.stdout,
        f"the example printed {len(ran.stdout):,} characters"
        if ran.returncode == 0
        else clip(ran.stderr, 200),
    )


# ---------------------------------------------------------------------------------------------
# 2. Planning: a plan, the hierarchy, and dates (M5B, M1, M1A)
# ---------------------------------------------------------------------------------------------


def a_plan() -> dict[str, Any]:
    """1 epic, 3 stories, 8 tasks, 5 links and 5 acceptance criteria (rules): four on stories and
    tasks, one of them already met, and one not placed yet."""
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
        {"title": "Every invoice has a number", "node": "s0", "status": "met"},
        {"title": "Totals are in euros", "node": "s0"},
        {"title": "Credit notes keep the old number", "node": "s1", "body": "See finance."},
        {"title": "Shown on the paper copy", "node": "t0"},
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
    async with Agents() as agents:
        client, ann, bob = await agents("marie"), await agents("ann"), await agents("bob")
        # -- M5B: create_plan
        plan = a_plan()
        result = await ok(ann, "create_plan", **plan)
        proof(
            "M5B",
            "one create_plan call makes 1 epic, 3 stories, 8 tasks, 5 links and 5 acceptance "
            "criteria",
            result["counts"] == {"epic": 1, "story": 3, "task": 8, "links": 5, "rules": 5},
            str(result["counts"]),
        )
        new = {item["key"]: item for item in result["created"]}
        t6 = await get(client, new["t6"])
        proof(
            "M5B",
            "the links in it are real: t6 waits for t3, which waits for t0",
            t6["waiting_on"] == [new["t3"]["ref"]] and t6["status"] == "new",
        )
        on_task = await ok(client, "get_rule", rule_id=result["rule_ids"][3])
        loose = await ok(client, "get_rule", rule_id=result["rule_ids"][4])
        proof(
            "M30",
            "the acceptance criteria in it are placed as written: on stories and on a task, one "
            "already met, and the last one on nothing yet",
            on_task["node"] == new["t0"]["ref"]
            and (await get(client, new["t0"]))["criteria_total"] == 1
            and loose["node"] is None
            and (await ok(client, "list_rules", node=new["s0"]["ref"]))["total"] == 2,
            f"{on_task['node']} holds one, acceptance criterion {loose['id']} is not placed",
        )
        before = rows("nodes", "links", "rules", "events")
        bad = copy.deepcopy(plan)
        bad["items"].append({"key": "bad", "kind": "task", "title": "Hangs", "parent": "epic"})
        message = await refused(ann, "create_plan", **bad)
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
        message = await refused(ann, "create_plan", **circle)
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
        big = await ok(ann, "create_plan", items=items, links=links)
        seconds = time.monotonic() - started
        proof(
            "M5B",
            "a plan of 500 items is created in one call",
            len(items) == 500 and len(big["created"]) == 500 and seconds < 60,
            f"{len(links)} links too, {seconds:.1f} seconds",
        )

        # -- M1: the hierarchy and versions
        epic = await ok(client, "create_node", kind="epic", title="Checkout")
        nodes_before = rows("nodes")
        await refusal(
            client, "M1", "a story without an epic is refused", "create_node",
            "A story needs a parent epic", kind="story", title="Orphan",
        )  # fmt: skip
        await refusal(
            client, "M1", "a task under an epic is refused", "create_node",
            f"A task belongs under a story, but {epic['ref']} is an epic",
            kind="task", title="Misplaced", parent=epic["ref"],
        )  # fmt: skip
        story = await ok(
            client, "create_node", kind="story", title="Pay by card", parent=epic["ref"]
        )
        task = await ok(client, "create_node", kind="task", title="Card form", parent=story["ref"])
        proof(
            "M1",
            "a valid epic, story and task are created",
            (epic["kind"], story["kind"], task["kind"]) == ("epic", "story", "task")
            and rows("nodes") == (nodes_before[0] + 2,),
        )
        other = await ok(client, "create_node", kind="epic", title="Refunds")
        await refusal(
            client, "M1", "a second parent is refused, and move_node is offered", "update_node",
            "exactly one parent", node=story["ref"], expected_version=1, parent=other["ref"],
        )  # fmt: skip
        await ok(
            ann, "update_node", node=task["ref"], expected_version=1,
            title="Card form (Ann)", status="in_progress",
        )  # fmt: skip
        await refusal(
            bob, "M1", "a stale version is refused and overwrites nothing", "update_node",
            f"{task['ref']} is at version 2, but you sent version 1",
            node=task["ref"], expected_version=1, title="Card form (Bob)",
        )  # fmt: skip
        events = await history(client, task)
        proof(
            "M1",
            "the history of a task is readable, with who and before and after",
            [(e["action"], e["actor"]) for e in events]
            == [("create", agents.named("marie")), ("update", agents.named("ann"))]
            and events[1]["before"] == {"status": "new", "title": "Card form", "version": 1}
            and events[1]["after"]["title"] == "Card form (Ann)",
        )

        # -- M1A: dates
        late = await ok(
            client, "create_node", kind="task", title="Late job",
            parent=story["ref"], target_on=day(-3),
        )  # fmt: skip
        message = await refused(
            client, "create_node", kind="task", title="Backwards",
            parent=story["ref"], start_on=day(9), target_on=day(3),
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
            f"WHERE {mine()} AND ref = '{task['ref']}'"
        )
        proof(
            "M1A",
            "the database refuses it on its own too",
            attempt.returncode != 0 and "node_start_is_not_after_target" in attempt.stderr,
            "node_start_is_not_after_target",
        )
        dated = await ok(
            ann, "update_node", node=task["ref"], expected_version=2,
            start_on=day(2), target_on=day(10),
        )  # fmt: skip
        cleared = await ok(
            ann, "update_node", node=task["ref"], expected_version=3, clear_start_on=True
        )
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
            bob, "M1A", "a stale version is still refused for a date change", "update_node",
            "is at version 4, but you sent version 3", node=task["ref"],
            expected_version=3, target_on=day(20),
        )  # fmt: skip
        was = await get(client, late)
        done = await set_status(client, late, "done")
        proof(
            "M1A",
            "a story or task whose target date has passed is overdue until it is done",
            was["overdue"] is True and done["overdue"] is False,
        )
        epic2 = await ok(client, "create_node", kind="epic", title="Dated work")
        s_dated = await ok(
            client, "create_node", kind="story", title="Dated", parent=epic2["ref"],
            start_on=day(40), target_on=day(60),
        )  # fmt: skip
        s_plain = await ok(client, "create_node", kind="story", title="Plain", parent=epic2["ref"])
        for name, start, target in (("Early", 5, 8), ("Late", 7, 30)):
            await ok(
                client, "create_node", kind="task", title=name,
                parent=s_plain["ref"], start_on=day(start), target_on=day(target),
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
            client, "create_node", kind="task", title="First",
            parent=s_dated["ref"], start_on=day(1), target_on=day(20),
        )  # fmt: skip
        second = await ok(
            client, "create_node", kind="task", title="Second",
            parent=s_dated["ref"], start_on=day(10), target_on=day(30),
        )  # fmt: skip
        await ok(
            client, "add_link", source=second["ref"], target=first["ref"], link_type="needs_first"
        )
        conflict = await get(client, second)
        fixed = await ok(
            client, "update_node", node=second["ref"],
            expected_version=conflict["version"], start_on=day(25), target_on=day(35),
        )  # fmt: skip
        proof(
            "M1A",
            "a dependent that starts before its prerequisite is due shows a schedule conflict, "
            "which goes away when a date changes",
            conflict["schedule_conflicts"] == [first["ref"]] and fixed["schedule_conflicts"] == [],
        )


# ---------------------------------------------------------------------------------------------
# 3. Working together: claims, next_work, handoffs, bulk changes, the feed (M2, M5B)
# ---------------------------------------------------------------------------------------------


async def working_together() -> None:
    chapter("Working together: ten agents, one set of tasks")
    async with Agents() as agents:
        client, ann, bob = await agents("marie"), await agents("ann"), await agents("bob")
        # -- M2
        _, _, a, b, c = await tree(client, 3)
        await ok(client, "add_link", source=a["ref"], target=b["ref"], link_type="needs_first")
        await ok(client, "add_link", source=b["ref"], target=c["ref"], link_type="needs_first")
        await refusal(
            client, "M2", "a link that would make a circle of waiting is refused", "add_link",
            "circle of waiting", source=c["ref"], target=a["ref"], link_type="needs_first",
        )  # fmt: skip
        waiting = await get(client, b)
        await set_status(client, c, "done")
        released = await get(client, b)
        proof(
            "M2",
            "finishing a prerequisite makes its dependent ready",
            (waiting["status"], waiting["waiting_on"]) == ("new", [c["ref"]])
            and (released["status"], released["waiting_on"]) == ("ready", []),
        )
        _, _, solo = await tree(client, 1)
        claimers = await agents.several(10, "claimer")
        results = await race_agents(claimers, "claim_node", {"node": solo["ref"]})
        winners = [text for accepted, text in results if accepted]
        held = await get(client, solo)
        proof(
            "M2",
            "ten parallel claimers on one ready task produce exactly one winner",
            len(results) == 10 and len(winners) == 1 and held["claimed_by"] is not None,
            f"winner: {held['claimed_by']}",
        )
        _, _, lease = await tree(client, 1)
        await ok(ann, "claim_node", node=lease["ref"], lease_seconds=2)
        message = await refused(bob, "claim_node", node=lease["ref"])
        await asyncio.sleep(2.6)
        taken = await ok(bob, "claim_node", node=lease["ref"], lease_seconds=60)
        proof(
            "M2",
            "an expired lease can be claimed by another actor",
            f"claimed by {agents.named('ann')} until" in message
            and taken["actor"] == agents.named("bob"),
            f'while it ran: "{clip(message, 60)}"',
        )

        # -- M5B: next_work
        epic, story, *five = await tree(client, 5)
        askers = await agents.several(10, "asker")
        results = await race_agents(askers, "next_work", {"within_node": epic["ref"]})
        answers = [json.loads(text) for _, text in results]
        found = [a for a in answers if a["found"]]
        nothing = [a for a in answers if not a["found"]]
        proof(
            "M5B",
            "ten agents ask for work at once on five ready tasks: five each get a different task, "
            "none twice, the rest are told plainly that nothing is free",
            len(found) == 5
            and len(nothing) == 5
            and sorted(a["work"]["ref"] for a in found) == sorted(refs(five))
            and len({a["claim"]["actor"] for a in found}) == 5
            and all(a["message"].startswith("Nothing is free right now") for a in nothing),
            nothing[0]["message"],
        )

        epic, story, first, second = await tree(client, 2, body="The story in more words.")
        await ok(
            client, "add_link", source=second["ref"], target=first["ref"], link_type="needs_first"
        )
        rule = await ok(client, "create_rule", title="Invoices carry a number", node=story["ref"])
        on_epic = await make_decision(client, "Decided on the epic", nodes=[epic["ref"]])
        confirm_on_board("note", on_epic, "Marie")
        old = await make_decision(client, "The old way", nodes=[story["ref"]])
        new_way = (
            await ok(
                client, "supersede_note", replaces=[old["id"]],
                text="The new way\n\nDo it the new way", why="It changed", decided_by="Marie",
            )
        )["replacement"]  # fmt: skip
        wide = await make_decision(client, "Always be careful", project_wide=True)
        known = await make_fact(client, "Invoices are sent by email", nodes=[story["ref"]])
        take = await ok(ann, "next_work", within_node=epic["ref"], context_chars=1_000_000)
        context = take["context"]
        project_wide = await ok(
            client, "list_notes", project_wide=True, status="in_force", limit=200
        )
        notes = {n["id"]: n for n in context["notes"]}
        proof(
            "M5B",
            "the context holds exactly the parent chain, the acceptance criteria that apply, the "
            "notes in force linked to the work or what it belongs to (the ones a person decided "
            "first, each saying whether it was confirmed), a replaced note only in its own "
            "section, and every project-wide note in force",
            take["work"]["ref"] == first["ref"]
            and [(p["kind"], p["ref"]) for p in context["parents"]]
            == [("epic", epic["ref"]), ("story", story["ref"])]
            and ids(context["acceptance_criteria"]) == [rule["id"]]
            and ids(context["notes"]) == [on_epic["id"], new_way["id"], known["id"]]
            and [n["decided"] for n in context["notes"]] == [True, True, False]
            and (notes[on_epic["id"]]["status"], notes[on_epic["id"]]["confirmed_by"])
            == ("confirmed", "Marie")
            and (notes[new_way["id"]]["status"], notes[known["id"]]["status"])
            == ("unconfirmed", "unconfirmed")
            and ids(context["past_notes"]) == [old["id"]]
            and set(ids(context["project_wide_notes"])) == set(ids(project_wide["items"]))
            and wide["id"] in ids(context["project_wide_notes"]),
            f"{len(context['notes'])} notes, {len(context['project_wide_notes'])} project-wide",
        )
        events_before = await history(client, first)
        for blank in ("", "   "):
            message = await refused(
                ann, "finish_work", node=first["ref"], outcome="done", note=blank
            )
        proof(
            "M5B",
            "finish_work with a blank note is refused and changes nothing",
            "A handoff note is needed" in message and await history(client, first) == events_before,
            f'"{clip(message, 60)}"',
        )
        finished = await ok(
            ann, "finish_work", node=first["ref"], outcome="done",
            note="Used the invoice library; the numbering is in numbering.py.",
        )  # fmt: skip
        nxt = await ok(bob, "next_work", within_node=epic["ref"])
        (prerequisite,) = nxt["context"]["prerequisites"]
        proof(
            "M5B",
            "with a note the work is done, its dependent becomes ready, and the next agent's "
            "next_work shows the note",
            finished["node"]["status"] == "done"
            and [(n["ref"], n["status"]) for n in finished["unblocked"]]
            == [(second["ref"], "ready")]
            and nxt["work"]["ref"] == second["ref"]
            and prerequisite["handoffs"][0]["note"].startswith("Used the invoice library")
            and prerequisite["handoffs"][0]["actor"] == agents.named("ann"),
        )
        epic, _, lapsing = await tree(client, 1)
        await ok(ann, "next_work", within_node=epic["ref"], lease_seconds=2)
        await asyncio.sleep(2.6)
        lapsed = await get(client, lapsing)
        proof(
            "M5B",
            "a claim that ran out shows as lapsed, with no handoff note",
            lapsed["lapsed_claim_by"] == agents.named("ann")
            and lapsed["claimed_by"] is None
            and lapsed["handoff_count"] == 0,
        )

        # -- M31 and M32: done means the project's own checks are ticked, with how they were made
        own = make_project(register(), "Checks")
        set_definition_on_board("- Tests pass\n- Docs are updated", project=own)
        async with connect(own.mcp_url, own.key) as lone:
            _, _, unfinished = await tree(lone, 1)
            await ok(lone, "claim_node", node=unfinished["ref"])
            said = await refused(
                lone, "finish_work", node=unfinished["ref"], outcome="done", note="Done."
            )
            await ok(lone, "set_check", node=unfinished["ref"], item="Tests pass", evidence="212")
            await ok(
                lone, "set_check", node=unfinished["ref"], item="Docs are updated",
                evidence="Read it through",
            )  # fmt: skip
            finished = await ok(
                lone, "finish_work", node=unfinished["ref"], outcome="done", note="Done."
            )
        proof(
            "M32",
            "an agent cannot finish work as done while a check of the project's definition of "
            "done is unticked, the refusal names the checks, and once each is ticked with how it "
            "was checked, done goes through",
            "Tick these checks first" in said
            and "'Tests pass'" in said
            and "'Docs are updated'" in said
            and finished["node"]["status"] == "done"
            and (finished["node"]["checks_total"], finished["node"]["checks_ticked"]) == (2, 2),
            "2 of 2 checks ticked",
        )
        await work_that_needs_a_verifier()
        await where_the_work_is()
        await asks_and_what_needs_a_person()

        # -- M5B: update_nodes
        _, _, *tasks = await tree(client, 5)
        await ok(
            bob, "update_node", node=tasks[2]["ref"], expected_version=1,
            title="Changed under you",
        )  # fmt: skip
        result = await ok(
            ann, "update_nodes",
            changes=[{"node": t["ref"], "expected_version": 1, "priority": "high"}
                     for t in tasks],
        )  # fmt: skip
        stale = result["results"][2]
        mine = [
            e for t in tasks for e in await history(client, t) if e["actor"] == agents.named("ann")
        ]
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
            {"node": t["ref"], "expected_version": 1,
             "title": f"Renamed {n}", **({"status": "finished"} if n % 10 == 0 else {})}
            for n, t in enumerate(many)
        ]  # fmt: skip
        changes.append({"node": "task:999999999", "expected_version": 1, "title": "Nobody"})
        report = await ok(ann, "update_nodes", changes=changes)
        proof(
            "M5B",
            "a call with 120 changes reports each one",
            len(report["results"]) == 121 and (report["applied"], report["refused"]) == (108, 13),
            "108 applied, 13 refused, each with its reason",
        )

        # -- M5B: the feed
        start = (await ok(client, "changes_since", after_event_id=0, limit=1))["latest_event_id"]
        epic, story, t1, t2, t3 = await tree(client, 3)
        await ok(client, "add_link", source=t2["ref"], target=t1["ref"], link_type="needs_first")
        await set_status(client, t1, "blocked")
        await ok(client, "create_rule", title="A rule", node=story["ref"])
        await make_decision(client, "A decision", nodes=[epic["ref"]])
        await make_fact(client, "A fact", nodes=[story["ref"]])
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
            client, "create_plan",
            items=[{"key": "e", "kind": "epic", "title": "Long bodies"},
                   *({"key": f"s{n}", "kind": "story", "title": f"Story {n}", "parent": "e",
                      "body": body} for n in range(50))],
        )  # fmt: skip
        compact = await ok(client, "list_nodes", parent=made["created"][0]["ref"], limit=50)
        full = await ok(
            client, "list_nodes", parent=made["created"][0]["ref"], limit=50, detail=True
        )
        small, big = len(json.dumps(compact)), len(json.dumps(full))
        proof(
            "M5B",
            "a list of 50 items with long bodies is at least five times smaller by default than "
            "with the detail option",
            len(compact["items"]) == 50 and big >= 5 * small,
            f"{small:,} characters by default, {big:,} with detail ({big / small:.1f} times)",
        )


NOT_A_VERIFIER = "only a verifier can judge"
PULL_REQUEST = "https://github.com/example-org/riverside/pull/7"
SEEN_ON_GITHUB = (
    f"{PULL_REQUEST} merged 2026-11-22T17:30:00Z, merge commit 0123456789, checks passed 2 of 2"
)
# What `gh pr view` prints for a pull request that is merged with two passing checks. The stand-in
# gh of the walk prints what the walk tells it to, so that no real GitHub is ever asked.
MERGED_PULL_REQUEST = {
    "url": PULL_REQUEST,
    "state": "MERGED",
    "isDraft": False,
    "mergedAt": "2026-11-22T17:30:00Z",
    "mergeCommit": {"oid": "0123456789abcdef0123"},
    "headRefOid": "fedcba9876543210fedc",
    "statusCheckRollup": [
        {"__typename": "CheckRun", "name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "SKIPPED"},
    ],
}
STAND_IN_GH = f"""#!{sys.executable}
import os, sys
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
print(os.environ["FAKE_GH_VIEW"])
"""


async def run_verify_pr(
    key: str, node: str, shown: dict[str, Any], scratch: Path, address: str | None = PULL_REQUEST
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """scripts/verify_pr.py as a verifier runs it on its own machine, with the walk's stand-in `gh`
    first on the PATH, which says `shown` about the pull request. With no `address` the script is
    given none, and uses the one the item says it is in. Returns what it printed and what gh was
    asked."""
    log = scratch / "gh.log"
    done = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "verify_pr.py"),
            URL,
            node,
            *([] if address is None else [address]),
        ],
        cwd=REPO_ROOT,
        env={
            **{k: v for k, v in os.environ.items() if k not in ("VRUM_AGENT_KEY", "PATH")},
            "VRUM_AGENT_KEY": key,
            "PATH": f"{scratch / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_VIEW": json.dumps(shown),
            "FAKE_GH_LOG": str(log),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    return done, log.read_text(encoding="utf-8").splitlines() if log.exists() else []


async def work_that_needs_a_verifier() -> None:
    """M40: a check of the definition of done can be marked for a verifier, an agent that did not
    do the work, so that work is not marked done only on its own word. A project of its own."""
    words = (
        "Tests pass",
        "Reviewed by someone else",
        "The pull request is merged and its checks passed",
        "Looks right on a phone",
    )
    untagged = "\n".join(f"- {line}" for line in words)
    tags = ("", "[verifier] ", "[github] ", "[person] ")
    tagged = "\n".join(f"- {tag}{line}" for tag, line in zip(tags, words, strict=True))
    own = make_project(register(), "Verified work")
    reviewer = make_key(own, "reviewer-agent", verifier=True)
    before = set_definition_on_board(untagged, project=own)["items"]
    read = set_definition_on_board(tagged, project=own)["items"]
    async with (
        connect(own.mcp_url, own.key) as worker,
        connect(own.mcp_url, reviewer) as verifier,
    ):
        exported = (await export_everything(worker))["definition_of_done"]
        proof(
            "M40",
            "the owner marks lines of the definition of done [verifier], [github] and [person] on "
            "the board: a tag is read apart from the words, so writing one keeps the line's key "
            "(and every tick of it), and the export carries the text with its tags",
            [(i["item"], i["text"]) for i in read] == [(i["item"], i["text"]) for i in before]
            and [(i["person_only"], i["verifier_only"], i["github"]) for i in read]
            == [
                (False, False, False),
                (False, True, False),
                (False, True, True),
                (True, False, False),
            ]
            and not any("[" in i["text"] for i in read)
            and [item["body"].strip() for item in exported] == [tagged],
            "4 lines, 4 keys unchanged",
        )

        _, _, work = await tree(worker, 1)
        ref = work["ref"]
        await ok(worker, "claim_node", node=ref)
        await ok(worker, "set_check", node=ref, item=words[0], evidence="212 tests pass")
        ticked_says = await refused(
            worker, "set_check", node=ref, item=words[1], evidence="Read it"
        )
        pr_says = await refused(
            worker, "set_check", node=ref, item=words[2], evidence=SEEN_ON_GITHUB
        )
        done_says = await refused(worker, "finish_work", node=ref, outcome="done", note="Done.")
        await ok(
            worker, "finish_work", node=ref, outcome="blocked",
            note="Everything is built. Ready for a verifier.",
        )  # fmt: skip
        handed = await ok(worker, "get_node", node=ref)
        proof(
            "M40",
            "an agent whose key is not a verifier's, which did the work, is refused both the tick "
            "and done for a check marked for a verifier, in plain words that say to finish as "
            "blocked and leave it for one, and it does: the work is blocked and nobody holds it",
            NOT_A_VERIFIER in ticked_says
            and "[verifier]" in ticked_says
            and "finish the item as blocked" in ticked_says
            and "[github]" in pr_says
            and "needs a verifier" in done_says
            and "Nothing was changed." in done_says
            and (handed["status"], handed["claimed_by"]) == ("blocked", None),
            f'"{clip(ticked_says, 90)}"',
        )

        _, _, new_work = await tree(worker, 1)
        _, _, held_work = await tree(worker, 1)
        await ok(worker, "claim_node", node=held_work["ref"])
        by_tool = refs(
            (await ok(verifier, "list_nodes", query="is:unverified", limit=200))["items"]
        )
        on_the_board = refs(
            on_board("list_nodes", project=own, query="is:unverified", limit=200)["items"]
        )
        proof(
            "M40",
            "a verifier finds the work waiting for it with is:unverified, which finds only blocked "
            "work nobody holds with such a check open (not new work, not work someone holds, not "
            "an epic), and the board's filter says the same as the tool",
            by_tool == on_the_board == [ref],
            ref,
        )

        wrong = ("It is merged", f"merged {PULL_REQUEST}", PULL_REQUEST.replace("https", "http"))
        asked = [
            await refused(verifier, "set_check", node=ref, item=words[2], evidence=text)
            for text in wrong
        ]
        await ok(verifier, "set_check", node=ref, item=words[1], evidence="Read the diff, ran it")
        accepted = await ok(verifier, "set_check", node=ref, item=words[2], evidence=SEEN_ON_GITHUB)
        mine = {c["text"]: c for c in accepted["checks"]}
        on_board("set_check", project=own, node=ref, item=words[3], evidence="Tried it on my phone")
        current = await ok(verifier, "get_node", node=ref)
        finished = await ok(
            verifier, "update_node", node=ref, expected_version=current["version"], status="done"
        )
        again = refs((await ok(verifier, "list_nodes", query="is:unverified", limit=200))["items"])
        stamped = [
            (e["actor"], e["actor_kind"])
            for e in await history(verifier, work)
            if e["action"] == "check_ticked" and e["actor"] == "reviewer-agent"
        ]
        proof(
            "M40",
            "a verifier's [github] tick is refused without a pull request address first (three "
            "wrong ways) and accepted with one and what was seen, the tick is its own, the owner "
            "ticks the [person] line on the board, and the verifier, which never claimed the work, "
            "sets it done, after which is:unverified no longer finds it",
            all("[github] check" in text and "scripts/verify_pr.py" in text for text in asked)
            and mine[words[2]]["evidence"] == SEEN_ON_GITHUB
            and (mine[words[2]]["checked_by"], mine[words[2]]["checked_by_kind"])
            == ("reviewer-agent", "agent")
            and finished["status"] == "done"
            and again == []
            and stamped == [("reviewer-agent", "agent")] * 2,
            "reviewer-agent, agent: 2 ticks, then done",
        )

        _, _, own_work = await tree(verifier, 1)
        await ok(verifier, "claim_node", node=own_work["ref"])
        await ok(verifier, "release_claim", node=own_work["ref"], note="Not mine after all.")
        mine_says = await refused(
            verifier, "set_check", node=own_work["ref"], item=words[1], evidence="Mine"
        )
        proof(
            "M40",
            "a verifier that claimed the work, even if it let the claim go, has done the work and "
            "cannot check it: it is told another verifier or a person must",
            "your own work" in mine_says and "Another verifier or a person must" in mine_says,
            f'"{clip(mine_says, 90)}"',
        )

        _, _, third = await tree(worker, 1)
        careful = make_key(own, "careful-agent")
        keys = f"/board/api/projects/{own.id}/keys"
        key_id = next(k["id"] for k in own.owner.call("GET", keys).body["keys"]
                      if k["name"] == "careful-agent")  # fmt: skip
        async with connect(own.mcp_url, careful) as agent:
            before_flag = await refused(
                agent, "set_check", node=third["ref"], item=words[1], evidence="Read it"
            )
            marked = own.owner.call("PATCH", f"{keys}/{key_id}", {"verifier": True})
            ticks_now = await ok(
                agent, "set_check", node=third["ref"], item=words[1], evidence="Read it"
            )
            cleared = own.owner.call("PATCH", f"{keys}/{key_id}", {"verifier": False})
            after_flag = await refused(
                agent, "set_check", node=third["ref"], item=words[1], checked=False, evidence="No"
            )
        odd = own.owner.call("PATCH", f"{keys}/{key_id}", {"verifier": "yes"})
        listed = {k["name"]: k["verifier"] for k in own.owner.call("GET", keys).body["keys"]}
        proof(
            "M40",
            "the owner marks a key a verifier and clears it on the board, and it applies at the "
            "very next call of a connection that stayed open (refused, then allowed, then "
            "refused again); anything but true or false is refused and changes nothing",
            NOT_A_VERIFIER in before_flag
            and marked.status == 200
            and marked.body["key"]["verifier"] is True
            and any(c["text"] == words[1] and c["checked"] for c in ticks_now["checks"])
            and cleared.status == 200
            and NOT_A_VERIFIER in after_flag
            and odd.status == 400
            and "true or false" in odd.body["message"]
            and listed == {HARNESS_AGENT: False, "reviewer-agent": True, "careful-agent": False},
            f'"{clip(odd.body["message"], 70)}"',
        )

    # The verifier uses gh on its own machine: Vrum holds no GitHub login and never asks GitHub.
    scratch_folder = REPO_ROOT / "backups"
    made_scratch = not scratch_folder.exists()
    scratch_folder.mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=scratch_folder, prefix="walk-gh-") as folder:
            scratch = Path(folder)
            (scratch / "bin").mkdir()
            stand_in = scratch / "bin" / "gh"
            stand_in.write_text(STAND_IN_GH, encoding="utf-8")
            stand_in.chmod(0o755)
            async with (
                connect(own.mcp_url, own.key) as worker,
                connect(own.mcp_url, reviewer) as verifier,
            ):
                _, _, last = await tree(worker, 1)
                node = last["ref"]
                await ok(worker, "claim_node", node=node)
                await ok(worker, "set_check", node=node, item=words[0], evidence="212 tests")
                await ok(
                    worker, "finish_work", node=node, outcome="blocked", note="Ready to verify."
                )
                failing = {
                    **MERGED_PULL_REQUEST,
                    "statusCheckRollup": [
                        {
                            "__typename": "CheckRun",
                            "name": "tests",
                            "status": "COMPLETED",
                            "conclusion": "FAILURE",
                        }  # fmt: skip
                    ],
                }
                not_good = []
                for view in ({**MERGED_PULL_REQUEST, "state": "OPEN", "mergedAt": None}, failing):
                    result, _ = await run_verify_pr(reviewer, node, view, scratch)
                    not_good.append(result)
                untouched = {
                    c["text"]: c for c in (await ok(verifier, "get_node", node=node))["checks"]
                }
                good, asked_gh = await run_verify_pr(reviewer, node, MERGED_PULL_REQUEST, scratch)
                ticked = {
                    c["text"]: c for c in (await ok(verifier, "get_node", node=node))["checks"]
                }
        proof(
            "M40",
            "scripts/verify_pr.py, run by a verifier with a stand-in gh (no real GitHub is asked), "
            "ticks nothing for a pull request that is still open or has a failing check and says "
            "why, and for a merged one with passing checks ticks only the [github] check, with "
            "what it saw in one fixed form that starts with the pull request's address",
            [r.returncode for r in not_good] == [1, 1]
            and "is still open, not merged" in not_good[0].stderr
            and "did not pass: tests" in not_good[1].stderr
            and untouched[words[2]]["checked"] is False
            and good.returncode == 0
            and good.stdout.strip() == f"Ticked {words[2]!r} on {node}."
            and ticked[words[2]]["evidence"] == SEEN_ON_GITHUB
            and ticked[words[2]]["checked_by"] == "reviewer-agent"
            and ticked[words[1]]["checked"] is False
            and ticked[words[3]]["checked"] is False
            and len(asked_gh) == 3
            and all(line.startswith(f"pr view {PULL_REQUEST} --json ") for line in asked_gh),
            f"gh asked {len(asked_gh)} times, on this machine",
        )
    finally:
        if made_scratch:
            scratch_folder.rmdir()


WORK_REPOSITORY = "https://github.com/example-org/riverside"
WORK_BRANCH = "feature/slot-picker"
WORK_COMMIT = "0123456789abcdef0123456789abcdef01234567"
NEWER_COMMIT = "89abcdef0123456789abcdef0123456789abcdef"
RUN_LABEL = "run-2026-11-22-a"
CLIENT_NAME = "claude-code"
WHERE_FIELDS = ("repository", "branch", "commit", "pull_request", "run_label", "client_name")
GITHUB_CHECK = "The pull request is merged and its checks passed"
A_SECRET = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2


def said_about(row: dict[str, Any] | None) -> dict[str, Any]:
    """What a claim, a handoff note, an event or `where` says about where the work is and which run
    it is: only the words that were said."""
    return {field: row[field] for field in WHERE_FIELDS if row and row.get(field)}


@contextmanager
def stand_in_gh() -> Iterator[Path]:
    """A folder with the walk's stand-in `gh` in its `bin`, for a verifier's run of
    scripts/verify_pr.py (see `run_verify_pr`): no real GitHub is ever asked."""
    scratch_folder = REPO_ROOT / "backups"
    made_scratch = not scratch_folder.exists()
    scratch_folder.mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=scratch_folder, prefix="walk-gh-") as folder:
            scratch = Path(folder)
            (scratch / "bin").mkdir()
            stand_in = scratch / "bin" / "gh"
            stand_in.write_text(STAND_IN_GH, encoding="utf-8")
            stand_in.chmod(0o755)
            yield scratch
    finally:
        if made_scratch:
            scratch_folder.rmdir()


async def where_the_work_is() -> None:
    """M41: an agent says where its work is (repository, branch, commit, pull request) and which
    run it is (a run label and a client name), and Vrum keeps it with the claim and the handoff
    note and shows it, without ever looking at a checkout or asking GitHub. A project of its own."""
    own = make_project(register(), "Where the work is")
    reviewer = make_key(own, "reviewer-agent", verifier=True)
    other = make_key(own, "other-agent")
    set_definition_on_board(f"- Tests pass\n- [github] {GITHUB_CHECK}", project=own)
    async with (
        connect(own.mcp_url, own.key) as worker,
        connect(own.mcp_url, own.key) as twin,
        connect(own.mcp_url, other) as stranger,
    ):
        _, _, shipped, plain, first, second = await tree(worker, 4)
        said = {
            "repository": WORK_REPOSITORY,
            "branch": WORK_BRANCH,
            "commit": WORK_COMMIT,
            "run_label": RUN_LABEL,
            "client_name": CLIENT_NAME,
        }
        claim = await ok(worker, "claim_node", node=shipped["ref"], **said)
        holding = (await ok(worker, "who_is_working"))["agents"][0]["holding"][0]
        detail = await ok(worker, "get_node", node=shipped["ref"])
        renewed = await ok(worker, "renew_claim", node=shipped["ref"], commit=NEWER_COMMIT)
        proof(
            "M41",
            "an agent that claims work says where it is (repository, branch, commit) and which run "
            "it is (a run label and a client name): the claim, who_is_working and get_node show "
            "exactly that, and renewing with a newer commit changes the commit and leaves the "
            "rest as it was",
            said_about(claim) == said_about(holding) == said_about(detail["where"]) == said
            and said_about(renewed) == {**said, "commit": NEWER_COMMIT},
            f"{WORK_BRANCH}, {RUN_LABEL} ({CLIENT_NAME}), then commit {NEWER_COMMIT[:7]}",
        )

        finished = await ok(
            worker, "finish_work", node=shipped["ref"], outcome="blocked",
            note="Everything is built. Ready for a verifier.", pull_request=PULL_REQUEST,
        )  # fmt: skip
        after = await ok(worker, "get_node", node=shipped["ref"])
        agent = (await ok(worker, "who_is_working"))["agents"][0]
        events = {
            e["action"]: e
            for e in await history(worker, shipped)
            if e["action"] in ("claimed", "claim_renewed", "handoff")
        }
        final = {**said_about(renewed), "pull_request": PULL_REQUEST}
        proof(
            "M41",
            "finishing as blocked with the pull request leaves a handoff note that keeps what the "
            "claim said, with what was said last on top, and get_node and who_is_working show it "
            "after nobody holds the work any more; the item's history carries the words in its "
            "claimed, renewed and handoff events, all under the key's name",
            said_about(finished["handoff"]) == final
            and said_about(after["last_handoff"]) == final
            and said_about(after["where"]) == final
            and said_about(agent["last_handoff"]) == final
            and agent["holding"] == []
            and after["claimed_by"] is None
            and sorted(events) == ["claim_renewed", "claimed", "handoff"]
            and said_about(events["claimed"]["after"]) == said_about(claim)
            and said_about(events["claim_renewed"]["after"]) == said_about(renewed)
            and said_about(events["handoff"]["after"]) == final
            and {(e["actor"], e["actor_kind"]) for e in events.values()}
            == {(HARNESS_AGENT, "agent")},
            f"the pull request is {PULL_REQUEST}",
        )

        await ok(worker, "claim_node", node=plain["ref"])
        await ok(worker, "renew_claim", node=plain["ref"])
        quiet = await ok(worker, "release_claim", node=plain["ref"], note="Not now.")
        quiet_events = [
            e
            for e in await history(worker, plain)
            if e["action"] in ("claimed", "claim_renewed", "handoff", "claim_released")
        ]
        proof(
            "M41",
            "work claimed, renewed and released with nothing said is exactly as before: no word "
            "about where it is anywhere, none in its history (not even an empty one), and "
            "get_node has no `where`",
            said_about(quiet) == {}
            and len(quiet_events) >= 3
            and not any(
                field in ((e.get("after") or {}) | (e.get("before") or {}))
                for e in quiet_events
                for field in WHERE_FIELDS
            )
            and (await get(worker, plain))["where"] is None,
            f"{len(quiet_events)} events with nothing in them",
        )

        wrong = (
            ("branch", "-rf"),
            ("branch", "feature branch"),
            ("commit", "not-a-hash"),
            ("pull_request", "javascript:alert(1)"),
            ("pull_request", WORK_REPOSITORY),
            ("repository", "https://user:hunter2@github.com/example-org/riverside"),
            ("run_label", "run\nlabel"),
        )
        said_back = [
            await refused(worker, "claim_node", node=first["ref"], **{field: value})
            for field, value in wrong
        ]
        secrets = [
            await refused(worker, "claim_node", node=first["ref"], **{field: A_SECRET})
            for field in ("branch", "run_label", "pull_request")
        ]
        kept = sql_value(f"SELECT count(*) FROM claims WHERE project_id = {own.id}")
        proof(
            "M41",
            "a branch that could be taken for an option or holds a space, a commit that is not a "
            "hash, a pull request that is not an https address of the form HOST/OWNER/REPO/pull/N "
            "(a javascript address, or a repository's), a repository with a password in it and a "
            "label with a line break are each refused in plain words that name the field, never "
            "repeat the value and say nothing was changed; a secret in any of them is refused and "
            "never repeated; and nothing was claimed",
            all(
                field in text and "Nothing was changed." in text and value not in text
                for (field, value), text in zip(wrong, said_back, strict=True)
            )
            and "hunter2" not in " ".join(said_back)
            and all(A_SECRET not in text for text in secrets)
            and kept == "0",
            f'"{clip(said_back[0], 80)}"',
        )

        one = await ok(
            worker, "claim_node", node=first["ref"], run_label=RUN_LABEL, client_name=CLIENT_NAME,
            branch="feature/one",
        )  # fmt: skip
        two = await ok(
            twin, "claim_node", node=second["ref"], run_label="marie", client_name="person",
            branch="feature/two",
        )  # fmt: skip
        agents = (await ok(worker, "who_is_working"))["agents"]
        turned_away = await refused(stranger, "claim_node", node=first["ref"], run_label=RUN_LABEL)
        stolen = await refused(stranger, "release_claim", node=second["ref"], note="Mine now")
        claimers = [
            (e["actor"], e["actor_kind"])
            for node in (first, second)
            for e in await history(worker, node)
            if e["action"] == "claimed"
        ]
        proof(
            "M41",
            "two instances of one key that hold different work under different run labels are one "
            "agent: who_is_working lists the key's name once with both claims and their labels, "
            "the history says that name for both (a label that looks like a person's name changes "
            "nothing), and another agent that quotes a label cannot take or release what is held",
            [a["actor"] for a in agents] == [HARNESS_AGENT]
            and {(h["node"], h["run_label"]) for h in agents[0]["holding"]}
            == {(first["ref"], RUN_LABEL), (second["ref"], "marie")}
            and said_about(one)["branch"] != said_about(two)["branch"]
            and claimers == [(HARNESS_AGENT, "agent")] * 2
            and HARNESS_AGENT in turned_away
            and HARNESS_AGENT in stolen,
            f"{HARNESS_AGENT} holds {first['ref']} as {RUN_LABEL} and {second['ref']} as marie",
        )

    # The verifier starts from the pull request the worker said, so it needs no address typed.
    with stand_in_gh() as scratch:
        nothing, asked_nothing = await run_verify_pr(
            reviewer, plain["ref"], MERGED_PULL_REQUEST, scratch, address=None
        )
        good, asked = await run_verify_pr(
            reviewer, shipped["ref"], MERGED_PULL_REQUEST, scratch, address=None
        )
    async with connect(own.mcp_url, reviewer) as verifier:
        checks = {
            c["text"]: c for c in (await ok(verifier, "get_node", node=shipped["ref"]))["checks"]
        }
    proof(
        "M41",
        "scripts/verify_pr.py given no address uses the pull request the item says it is in (the "
        "one `finish_work` was given) and ticks the [github] check with what it saw, asking gh "
        "about exactly that address; for an item that says none it asks for an address and does "
        "not ask gh anything",
        nothing.returncode == 1
        and "does not say which pull request" in nothing.stderr
        and asked_nothing == []
        and good.returncode == 0
        and good.stdout.strip() == f"Ticked {GITHUB_CHECK!r} on {shipped['ref']}."
        and checks[GITHUB_CHECK]["evidence"] == SEEN_ON_GITHUB
        and checks[GITHUB_CHECK]["checked_by"] == "reviewer-agent"
        and checks["Tests pass"]["checked"] is False
        and len(asked) == 1
        and asked[0].startswith(f"pr view {PULL_REQUEST} --json "),
        f"gh asked once, about {PULL_REQUEST}",
    )


ASK_QUESTION = "Which payment provider should the checkout use?"
ASK_CHOICES = ["Stripe", "Adyen", "Neither"]
APPROVAL_QUESTION = "May we drop the old export format?"
PERSON_CHECK = "Looks right on a phone"


def answer_on_board(project: Project, who: Person, ask: int, answer: str, **more: Any) -> Reply:
    """A person's answer to an ask, as the board sends it: the tool, as the person signed in."""
    return who.call(
        "POST",
        f"/board/api/projects/{project.id}/tool/answer_ask",
        {"ask": ask, "answer": answer, **more},
    )


async def asks_and_what_needs_a_person() -> None:
    """M45: an agent that needs a person asks, and a person answers: the ask blocks the work or
    leaves it free, is a record that is never edited, comes back to the agent in its briefing, and
    is answered once, by a person. Beside the asks, one list holds what else only a person can
    settle, and the change feed has a cursor. A project of its own."""
    owner = register()
    own = make_project(owner, "Asks")
    set_definition_on_board(f"- Tests pass\n- [person] {PERSON_CHECK}", project=own)
    second_key = make_key(own, "second-agent")
    async with connect(own.mcp_url, own.key) as agent, connect(own.mcp_url, second_key) as second:
        epic = await ok(agent, "create_node", kind="epic", title="Checkout")
        pay = await ok(agent, "create_node", kind="story", title="Pay", parent=epic["ref"])
        quiet = await ok(agent, "create_node", kind="story", title="Quiet", parent=epic["ref"])

        async def task(story: dict[str, Any], title: str, **more: Any) -> dict[str, Any]:
            return await ok(
                agent, "create_node", kind="task", title=title, parent=story["ref"], **more
            )

        choose = await task(pay, "Choose a provider", priority="urgent")
        parked = await task(pay, "Drop the old export format")
        looked = await task(pay, "Build the form")
        noted = await task(pay, "Wire the webhook")
        lapsing = await task(quiet, "Retry the call")

        # -- an ask blocks the work or leaves it free
        await ok(agent, "claim_node", node=choose["ref"])
        await ok(
            agent, "finish_work", node=choose["ref"], outcome="blocked",
            note="I need a decision before I can go on.",
            ask={"question": ASK_QUESTION, "choices": ASK_CHOICES},
        )  # fmt: skip
        await ok(agent, "claim_node", node=parked["ref"])
        await ok(
            agent, "finish_work", node=parked["ref"], outcome="released",
            note="Parked until a person says it is fine.",
            ask={"question": APPROVAL_QUESTION, "approval": True},
        )  # fmt: skip
        waiting_work, free_work = await get(agent, choose), await get(agent, parked)
        (question,), (approval,) = waiting_work["asks"], free_work["asks"]
        raised = [e for e in await history(agent, choose) if e["action"] == "ask_raised"]
        peeked = await ok(agent, "next_work", within_node=pay["ref"], peek=True)
        proof(
            "M45",
            "an agent that needs a person puts an ask on its work with the optional ask of "
            "finish_work: with choices and the outcome blocked the work is blocked and next_work "
            "does not give it, as a yes or no approval with the outcome released the work stays "
            "free to take; each ask says who asked and what it did to the work, and is the event "
            "ask_raised whose id is its number",
            (question["kind"], question["choices"], question["effect"], question["answered"])
            == ("question", ASK_CHOICES, "blocks", False)
            and (question["asked_by"], question["asked_by_kind"]) == (HARNESS_AGENT, "agent")
            and (waiting_work["status"], waiting_work["claimed_by"]) == ("blocked", None)
            and peeked["work"]["ref"] != choose["ref"]
            and (approval["kind"], approval["choices"], approval["effect"])
            == ("approval", ["yes", "no"], "parks")
            and (free_work["status"], free_work["claimed_by"]) == ("ready", None)
            and [(e["id"], e["after"]["effect"]) for e in raised] == [(question["id"], "blocks")],
            f"Q-{question['id']} blocks, Q-{approval['id']} parks",
        )

        # -- what else only a person can settle, and the one list that holds it all
        await ok(agent, "claim_node", node=looked["ref"])
        await ok(agent, "set_check", node=looked["ref"], item="Tests pass", evidence="Ran them")
        await ok(
            agent, "finish_work", node=looked["ref"], outcome="blocked",
            note="Everything is built. A person should look at it on a phone.",
        )  # fmt: skip
        await ok(agent, "claim_node", node=noted["ref"])
        note = await ok(
            agent, "add_note", text="The webhook retries for a day",
            why="Read it in the provider's documentation", nodes=[noted["ref"]],
        )  # fmt: skip
        await ok(agent, "claim_node", node=lapsing["ref"], lease_seconds=1)
        await asyncio.sleep(1.6)
        await ok(second, "claim_node", node=lapsing["ref"], lease_seconds=1)
        await asyncio.sleep(1.6)
        needs = await ok(agent, "needs_a_person")
        shown = on_board("needs_a_person", project=own)

        def rows_of(answer: dict[str, Any]) -> list[tuple[str, str, str]]:
            return [
                (item["kind"], item["node"], group["story"])
                for group in answer["groups"]
                for item in group["items"]
            ]

        found = {(kind, node) for kind, node, _ in rows_of(needs)}
        items = {(i["kind"], i["node"]): i for group in needs["groups"] for i in group["items"]}
        by_story = {g["story"]: [i["kind"] for i in g["items"]] for g in needs["groups"]}
        ask_row = items[("ask", choose["ref"])]
        since = [i["waiting_since"] for g in needs["groups"] for i in g["items"]]
        proof(
            "M45",
            "what needs a person is one list worked out from the rest: an open ask, work finished "
            "but for a check only a person can tick, a note waiting for confirmation on work in "
            "progress and a claim that ran out twice, grouped by story, each row with why it is "
            "there, who is waiting and since when (the longest first), and the board's own call "
            "gives the same rows and total as the tool",
            set(found) == {("ask", choose["ref"]), ("ask", parked["ref"]),
                           ("check", looked["ref"]), ("note", noted["ref"]),
                           ("lapse", lapsing["ref"])}
            and (needs["total"], needs["left_out"]) == (5, 0)
            and by_story == {pay["ref"]: ["ask", "ask", "check", "note"], quiet["ref"]: ["lapse"]}
            and since == sorted(since)
            and ask_row["ask"]["id"] == question["id"]
            and ask_row["reason"] == ASK_QUESTION
            and (ask_row["waiting"], ask_row["waiting_kind"]) == (HARNESS_AGENT, "agent")
            and PERSON_CHECK in items[("check", looked["ref"])]["reason"]
            and items[("note", noted["ref"])]["notes"] == [note["id"]]
            and items[("lapse", lapsing["ref"])]["count"] == 2
            and rows_of(shown) == rows_of(needs)
            and shown["total"] == needs["total"],
            "5 rows under 2 stories: an ask twice, a check, a note and a lapse",
        )  # fmt: skip

        # -- only a person answers, once, and the agent reads the answer
        number = question["id"]
        by_an_agent = await refused(agent, "answer_ask", ask=number, answer="Stripe")
        not_a_choice = answer_on_board(own, owner, number, "PayPal")
        given = answer_on_board(own, owner, number, "adyen", comment="They cover our markets.")
        twice = answer_on_board(own, owner, number, "Stripe")
        freed = await get(agent, choose)
        answered = next(e for e in await history(agent, choose) if e["action"] == "ask_answered")
        taken = await ok(agent, "next_work", within_node=pay["ref"])
        briefed = taken["context"]["asks"]
        proof(
            "M45",
            "an agent cannot answer an ask, whatever it says; a person answers on the board with "
            "one of the choices (in any case, kept as offered) and a comment, is the one recorded "
            "and cannot answer twice, which is refused saying who answered; the work the ask "
            "blocked is free again, the next agent that takes it reads the question and the "
            "answer in its briefing, and the row is gone from what needs a person",
            ("person" in by_an_agent or "answer_ask" in by_an_agent)
            and not_a_choice.status == 400
            and "Stripe, Adyen, Neither" in not_a_choice.body["message"]
            and given.status == 200
            and (given.body["answer"], given.body["answered_by"]) == ("Adyen", owner.name)
            and twice.status == 400
            and "already answered" in twice.body["message"]
            and owner.name in twice.body["message"]
            and freed["status"] == "ready"
            and (answered["actor"], answered["actor_kind"]) == (owner.name, "person")
            and answered["after"]
            == {"ask": number, "answer": "Adyen", "comment": "They cover our markets."}
            and taken["work"]["ref"] == choose["ref"]
            and [(a["id"], a["answer"], a["answered_by"], a["comment"]) for a in briefed]
            == [(number, "Adyen", owner.name, "They cover our markets.")]
            and ("ask", choose["ref"]) not in {
                (k, n) for k, n, _ in rows_of(await ok(agent, "needs_a_person"))
            },
            f"Q-{number} answered by {owner.name}: Adyen",
        )  # fmt: skip

        # -- an approval is yes or no, and each row goes as a person settles it
        maybe = answer_on_board(own, owner, approval["id"], "maybe")
        said_no = answer_on_board(own, owner, approval["id"], "No")
        left = (await ok(agent, "needs_a_person"))["total"]
        on_board("set_check", project=own, node=looked["ref"], item=PERSON_CHECK,
                 evidence="Tried it on my phone")  # fmt: skip
        after_check = (await ok(agent, "needs_a_person"))["total"]
        confirm_on_board("note", note, "Marie", project=own)
        after_note = (await ok(agent, "needs_a_person"))["total"]
        await ok(agent, "claim_node", node=lapsing["ref"])
        after_claim = (await ok(agent, "needs_a_person"))["total"]
        answered_all = await ok(agent, "list_asks", status="answered", limit=50)
        parked_now = await get(agent, parked)
        proof(
            "M45",
            "an approval is answered yes or no only (a person's answer, in any case), answering "
            "it leaves its parked work as it was, and what needs a person shrinks as each thing "
            "is settled: the approval, then the check ticked by a person, the note confirmed by "
            "one, and the claim taken again, until nothing is waiting",
            maybe.status == 400
            and "yes, no" in maybe.body["message"]
            and said_no.status == 200
            and said_no.body["answer"] == "no"
            and (parked_now["status"], parked_now["claimed_by"]) == ("ready", None)
            and [a["id"] for a in answered_all["items"]] == [number, approval["id"]]
            and (left, after_check, after_note, after_claim) == (3, 2, 1, 0),
            "3, 2, 1, 0 left",
        )

        # -- the change feed: a cursor, a truncated flag and when to ask again
        everything = await ok(agent, "changes_since", limit=200)
        seen: list[int] = []
        flags: list[bool] = []
        cursor: str | None = None
        while True:
            page = await ok(
                agent, "changes_since", limit=5, **({"cursor": cursor} if cursor else {})
            )
            seen += ids(page["items"])
            flags.append(page["truncated"])
            cursor = page["cursor"]
            if not page["truncated"]:
                break
        nothing = await ok(agent, "changes_since", cursor=cursor)
        new_task = await task(quiet, "Something new")
        news = await ok(agent, "changes_since", cursor=nothing["cursor"])
        not_ours = await refused(agent, "changes_since", cursor="not-a-cursor")
        both = await refused(agent, "changes_since", cursor=cursor, after_event_id=1)
        actions = {e["action"]: e for e in everything["items"]}
        proof(
            "M45",
            "the change feed carries an opaque cursor, a truncated flag and a suggested wait: "
            "read five at a time by the cursor alone it gives exactly the whole feed, truncated "
            "is true on every page but the last, the last cursor continues from the end (nothing, "
            "then the one new change), a cursor that is not the server's, or given with an "
            "event id, is refused in words, and the asks and the answer are in it, the answer "
            "as the person's",
            seen == ids(everything["items"])
            and len(seen) > 10
            and all(flags[:-1])
            and not flags[-1]
            and everything["next_after_id"] is None
            and everything["poll_after_seconds"] > 0
            and nothing["items"] == []
            and [e["node"] for e in news["items"]] == [new_task["ref"]]
            and "not one this server gave" in not_ours
            and "not both" in both
            and {"ask_raised", "ask_answered"} <= set(actions)
            and actions["ask_answered"]["actor_kind"] == "person",
            f"{len(seen)} changes in {len(flags)} pages of five, wait "
            f"{everything['poll_after_seconds']} s",
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
            "INSERT INTO events (node, action, actor, actor_kind, after) "
            f"VALUES ('{task['ref']}', 'update', '{actor}', 'agent', '{{\"title\": \"{actor}\"}}')"
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
    early = await ok(client, "changes_since", after_event_id=start, limit=50)
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
    by_cursor = await ok(client, "changes_since", cursor=early["cursor"])
    proof(
        "M45",
        "the cursor of an answer that was read while a slow change was still being written "
        "continues past that change too: read later, it gives the event of the slow change that "
        "finished after a later one, and nothing else",
        still_writing
        and ids(early["items"]) == [fast_id]
        and early["truncated"] is False
        and ids(by_cursor["items"]) == [slow_id],
        f"cursor read after {fast_id} gives {slow_id}",
    )


# ---------------------------------------------------------------------------------------------
# 4. Requirements and decisions (M3, M4)
# ---------------------------------------------------------------------------------------------


def entries(section: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """The items of one section of a recall, which wraps each one with why it is there."""
    return [entry[key] for entry in section["items"]]


async def whole_coverage(client: Client) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The unplaced rules and the stories without rules, each list read to its end."""
    report = await whole_coverage_report(client)
    return report["unplaced_rules"], report["stories_without_rules"]


async def requirements_and_decisions() -> None:
    chapter(
        "Requirements and decisions: an acceptance criterion in exactly one place, "
        "decisions you can recall"
    )
    async with Agents() as agents:
        client, ann, bob = await agents("marie"), await agents("ann"), await agents("bob")
        # -- M3 (and M30: an acceptance criterion is on one story or task)
        epic, first, second, bare = await stories(client, 3)
        rule = await ok(ann, "create_rule", title="Every invoice has a number")
        placed = await ok(
            ann, "place_rule", rule_id=rule["id"], expected_version=1, node=first["ref"]
        )
        moved = await ok(
            bob, "place_rule", rule_id=rule["id"], expected_version=2, node=second["ref"]
        )
        in_first = await ok(client, "list_rules", node=first["ref"])
        in_second = await ok(client, "list_rules", node=second["ref"])
        message = await refused(
            bob, "place_rule", rule_id=rule["id"], expected_version=3, node=second["ref"]
        )
        proof(
            "M3",
            "an acceptance criterion is on one story or task at a time: placing it on a second "
            "moves it in one step, and it is no longer on the first",
            placed["node"] == first["ref"]
            and moved["node"] == second["ref"]
            and in_first["items"] == []
            and ids(in_second["items"]) == [rule["id"]]
            and f"already on {second['ref']}, so nothing moved" in message,
            f'"{clip(message, 90)}"',
        )
        racers = [await agents(f"racer-{n}") for n in range(6)]
        contenders = await race_agents(
            racers, "place_rule",
            {"rule_id": rule["id"], "expected_version": 3, "node": first["ref"]},
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
            and [e["action"] for e in events]
            == ["rule_created", "rule_placed", "rule_moved", "rule_moved"]
            and move["before"]["node"] == second["ref"]
            and move["after"]["node"] == first["ref"]
            and move["id"] in in_first_history
            and move["id"] in in_second_history,
            f"moved by {move['actor']}",
        )
        before = rows("events")
        message = await refused(
            client, "place_rule", rule_id=rule["id"], expected_version=4, node="story:987654321"
        )
        proof(
            "M3",
            "a move that cannot happen is refused, changes nothing and logs nothing",
            "There is no story 987654321 in this project" in message and rows("events") == before,
            f'"{clip(message, 90)}"',
        )
        loose = await ok(client, "create_rule", title="Not in any story yet")
        unplaced, without = await whole_coverage(client)
        proof(
            "M3",
            "the coverage report lists unplaced rules and stories with none",
            loose["id"] in ids(unplaced) and bare["ref"] in refs(without)
            and rule["id"] not in ids(unplaced) and first["ref"] not in refs(without),
            f"{len(unplaced)} unplaced rules, {len(without)} stories without rules",
        )  # fmt: skip
        await ok(client, "place_rule", rule_id=loose["id"], expected_version=1, node=bare["ref"])
        unplaced, without = await whole_coverage(client)
        proof(
            "M3",
            "placing the loose rule in the bare story closes both gaps",
            loose["id"] not in ids(unplaced) and bare["ref"] not in refs(without),
        )
        _, covered, task = await tree(client, 1)
        await ok(client, "create_rule", title="Works on a phone", node=task["ref"])
        _, without = await whole_coverage(client)
        proof(
            "M30",
            "a story is covered when one of its tasks holds an acceptance criterion",
            covered["ref"] not in refs(without)
            and (await get(client, task))["criteria_total"] == 1,
        )

        # -- M4
        epic, story, other = await stories(client, 2)
        mine = await make_decision(client, "About this story", nodes=[story["ref"]])
        elsewhere = await make_decision(client, "About the other story", nodes=[other["ref"]])
        unlinked = await make_decision(client, "About nothing in particular")
        linked_wide = await make_decision(
            client, "A linked project-wide one", project_wide=True, nodes=[story["ref"]]
        )
        free_wide = await make_decision(client, "A free project-wide one", project_wide=True)
        recalled = await ok(client, "recall", node=story["ref"])
        every_wide = await ok(client, "list_notes", project_wide=True, status="in_force", limit=200)
        linked = ids(entries(recalled["notes"], "note"))
        proof(
            "M4",
            "recalling a story returns the notes in force linked to it and every project-wide "
            "note in force, linked or not",
            linked == [mine["id"]]
            and elsewhere["id"] not in linked
            and unlinked["id"] not in linked
            and set(ids(entries(recalled["project_wide_notes"], "note")))
            == set(ids(every_wide["items"]))
            and {linked_wide["id"], free_wide["id"]} <= set(ids(every_wide["items"])),
            f"{len(every_wide['items'])} project-wide notes in force",
        )  # fmt: skip
        new = (
            await ok(
                ann, "supersede_note", replaces=[mine["id"]],
                text="Use a table instead of a queue\n\nA table, polled by the worker.",
                why="The queue was more than we need.", decided_by="Ann",
            )
        )["replacement"]  # fmt: skip
        after = await ok(client, "recall", node=story["ref"])
        old = await ok(client, "get_note", note_id=mine["id"])
        superseded = entries(after["past_notes"], "note")
        proof(
            "M4",
            "a replaced note is shown as superseded and not as in force, with what replaced it, "
            "and what it said is kept",
            ids(entries(after["notes"], "note")) == [new["id"]]
            and [(n["id"], n["status"], n["superseded_by_id"]) for n in superseded]
            == [(mine["id"], "superseded", new["id"])]
            and old["text"] == mine["text"]
            and old["decided_by"] == mine["decided_by"],
        )  # fmt: skip
        quiet = await ok(client, "recall", node=await new_story(client))
        proof(
            "M4",
            "nothing is invented: a story with no notes of its own gets none, and nothing but "
            "what applies throughout the project",
            quiet["notes"]["count"] == 0
            and quiet["past_notes"]["count"] == 0
            and set(ids(entries(quiet["project_wide_notes"], "note")))
            == set(ids(every_wide["items"])),
        )


async def stories(client: Client, count: int) -> tuple[dict[str, Any], ...]:
    """A new epic and `count` stories under it."""
    epic = await ok(client, "create_node", kind="epic", title="An epic")
    return (
        epic,
        *[
            await ok(client, "create_node", kind="story", title=f"Story {n}", parent=epic["ref"])
            for n in range(count)
        ],
    )


async def new_story(client: Client) -> str:
    """The reference of a story in a new epic."""
    (_, story) = await stories(client, 1)
    return str(story["ref"])


# ---------------------------------------------------------------------------------------------
# 5. Remembering: notes with their why, who reviewed them, and what happens when one is wrong
#    (M27, which made decisions and facts one kind of record, and what M4A and M16 proved)
# ---------------------------------------------------------------------------------------------


def database_refuses(sql: str, says: str) -> bool:
    attempt = run_sql(sql)
    return attempt.returncode != 0 and says in attempt.stderr


def mine() -> str:
    """What a statement run as the database's owner must say to touch the shared test project and
    nothing else: each project numbers its own records, so ids collide between projects."""
    return f"project_id = {the_project().id}"


def by_a_person(tool: str, **arguments: Any) -> tuple[int, dict[str, Any]]:
    """What the board answers when the person signed in (the owner of the shared project) calls a
    tool: the HTTP status, and what the tool returned or the message that refused it."""
    project = the_project()
    reply = project.owner.call("POST", f"/board/api/projects/{project.id}/tool/{tool}", arguments)
    return reply.status, dict(reply.body)


async def remembering() -> None:
    chapter(
        "Remembering: notes with their why, who reviewed them, and what happens when one is wrong"
    )
    async with Agents() as agents:
        client = await agents("scribe")
        epic, story, other = await stories(client, 2)
        task = await ok(client, "create_node", kind="task", title="A task", parent=story["ref"])
        rule = await ok(client, "create_rule", title="A rule", node=story["ref"])

        # -- a note nobody decided
        fact = await make_fact(
            client, "The tests take an hour.", nodes=[story["ref"]], source="The Monday call"
        )
        namesake = await agents("Marie")
        by_namesake = await make_note(
            namesake, "Said by Marie herself", "Marie, in the call",
            confirmed_by="Marie", status="confirmed",
        )  # fmt: skip
        recalled = await ok(client, "recall", node=story["ref"])
        shown = entries(recalled["notes"], "note")
        proof(
            "M27",
            "a note recorded against a story is in its recall with its why, recorder and time, "
            "unconfirmed, and it is not a decision because nobody decided it",
            ids(shown) == [fact["id"]]
            and (shown[0]["why"], shown[0]["recorded_by"], shown[0]["status"])
            == ("The Monday call", agents.named("scribe"), "unconfirmed")
            and shown[0]["recorded_at"]
            and (shown[0]["decided"], shown[0]["decided_by"]) == (False, None),
            f'"{shown[0]["headline"]}", recorded {shown[0]["recorded_at"][:10]}',
        )
        proof(
            "M16",
            "recording a note never confirms it, even when the agent that records it is called by "
            "a person's name and the call says it is confirmed",
            (by_namesake["recorded_by"], by_namesake["status"], by_namesake["confirmed_by"])
            == (agents.named("Marie"), "unconfirmed", None),
        )
        await refusal(
            client, "M27", "a note with a blank why is refused", "add_note",
            "A note needs its reason: why cannot be empty", text="Something", why="  ",
        )  # fmt: skip

        # -- who reviewed it, and when
        message = await refused(
            namesake, "set_confirmation", note_id=fact["id"], confirmation="confirmed",
            person="Marie",
        )  # fmt: skip
        status, blank = by_a_person(
            "set_confirmation", note_id=fact["id"], confirmation="confirmed", person=" "
        )
        proof(
            "M16",
            "only a person confirms, on the board, and names who reviewed the note: an agent's "
            "own call is refused, and so is a confirmation that names nobody",
            "is not allowed to use set_confirmation" in message
            and "Nothing was changed" in message
            and status == 400
            and "A confirmation needs the person who reviewed the note" in blank["message"]
            and (await ok(client, "get_note", note_id=fact["id"]))["status"] == "unconfirmed",
            f'"{clip(message, 70)}"',
        )
        reviewed = confirm_on_board("note", fact, "Marie", quote="I read the timing")
        recalled = await ok(client, "recall", node=story["ref"])
        (now,) = entries(recalled["notes"], "note")
        history_of = (await ok(client, "note_history", note_id=fact["id"]))["items"]
        proof(
            "M27",
            "confirming a note records who reviewed it and when, and recall then shows it; the "
            "person signed in who made the change is kept apart from the person who reviewed it",
            (reviewed["status"], reviewed["confirmed_by"]) == ("confirmed", "Marie")
            and reviewed["confirmed_at"]
            and (now["status"], now["confirmed_by"], now["confirmed_at"])
            == ("confirmed", "Marie", reviewed["confirmed_at"])
            and (history_of[-1]["action"], history_of[-1]["actor_kind"])
            == ("note_confirmed", "person")
            and history_of[-1]["actor"] == the_project().owner.name
            and history_of[-1]["after"]["confirmed_by"] == "Marie",
            f"by Marie at {reviewed['confirmed_at'][:16]}",
        )

        # -- the exact links, and nothing else
        elsewhere = await make_fact(client, "Linked to the other story", nodes=[other["ref"]])
        on_task = await make_fact(client, "Linked to the task", nodes=[task["ref"]])
        on_rule = await make_fact(client, "Linked to the rule", rule_ids=[rule["id"]])
        loose = await make_fact(client, "Linked to nothing")
        recalled = await ok(client, "recall", node=story["ref"])
        seen = set(ids(entries(recalled["notes"], "note")))
        proof(
            "M27",
            "the recall holds exactly what the exact links give and nothing else",
            fact["id"] in seen
            and not {elsewhere["id"], on_task["id"], on_rule["id"], loose["id"]} & seen,
            f"{len(seen)} note linked to this story",
        )

        # -- replaced and withdrawn
        outdated = await make_fact(client, "The suite has 400 tests.", nodes=[story["ref"]])
        wrong = await make_fact(client, "The suite is the slow part.", nodes=[story["ref"]])
        change = await ok(
            client, "supersede_note", replaces=[outdated["id"]],
            text="The suite has 450 tests.", why="A recount",
        )  # fmt: skip
        await ok(
            client, "withdraw_note", note_ids=[wrong["id"]],
            reason="The benchmark ran on the wrong branch",
        )  # fmt: skip
        old = await ok(client, "get_note", note_id=outdated["id"])
        gone = await ok(client, "get_note", note_id=wrong["id"])
        recalled = await ok(client, "recall", node=story["ref"])
        past = {e["note"]["id"]: e for e in recalled["past_notes"]["items"]}
        in_force = set(ids(entries(recalled["notes"], "note")))
        proof(
            "M27",
            "a replaced note shows as superseded with its replacement and a withdrawn one as "
            "withdrawn with its reason, among the past notes and never among those in force",
            (old["status"], old["superseded_by_id"], old["text"])
            == ("superseded", change["replacement"]["id"], outdated["text"])
            and (gone["status"], gone["status_reason"])
            == ("withdrawn", "The benchmark ran on the wrong branch")
            and set(past) == {outdated["id"], wrong["id"]}
            and not set(past) & in_force
            and change["replacement"]["id"] in in_force
            and f"replaced by note {change['replacement']['id']}" in past[outdated["id"]]["why"]
            and "withdrawn: The benchmark ran on the wrong branch" in past[wrong["id"]]["why"],
            f"{past[outdated['id']]['why']}; {past[wrong['id']]['why']}",
        )

        # -- replacing several at once
        one, two, three = [await make_fact(client, f"Note {n}") for n in range(3)]
        both = await ok(
            client, "supersede_note", replaces=[one["id"], two["id"]],
            why="Marie says it is the other way round, in the planning call",
            text="The opposite of the first two",
        )  # fmt: skip
        new = both["replacement"]
        states = [
            (await ok(client, "get_note", note_id=n["id"]))["status"] for n in (one, two, three)
        ]
        proof(
            "M27",
            "a new note that contradicts two of three replaces both in one supersede_note call, "
            "each old one shows as superseded by it, and the third is untouched",
            both["superseded_ids"] == [one["id"], two["id"]]
            and states == ["superseded", "superseded", "unconfirmed"]
            and (await ok(client, "get_note", note_id=one["id"]))["superseded_by_id"] == new["id"],
        )
        four, five = await make_fact(client, "Four"), await make_fact(client, "Five")
        await ok(client, "withdraw_note", note_ids=[five["id"]], reason="It was wrong")
        before = rows("notes", "events")
        message = await refused(
            client, "supersede_note", replaces=[four["id"], five["id"]], why="r",
            text="Replacement",
        )  # fmt: skip
        proof(
            "M27",
            "a listed note that is already withdrawn refuses the whole call and changes nothing",
            f"Note {five['id']} was withdrawn (It was wrong), so it cannot be replaced" in message
            and "Nothing was changed" in message
            and (await ok(client, "get_note", note_id=four["id"]))["status"] == "unconfirmed"
            and rows("notes", "events") == before,
            f'"{clip(message, 80)}"',
        )
        thirty = [await make_fact(client, f"Thirty {n}") for n in range(30)]
        many = await ok(
            client, "supersede_note", replaces=ids(thirty), why="All of them",
            text="One note instead",
        )  # fmt: skip
        proof(
            "M27",
            "a call that replaces 30 notes at once works",
            many["superseded_ids"] == ids(thirty),
        )

        # -- listing
        e2, s2, t2 = await tree(client, 1)
        r2 = await ok(client, "create_rule", title="R", node=s2["ref"])
        e3, s3, *_ = await tree(client, 0)
        for n in range(20):
            await make_fact(client, f"On a task {n}", nodes=[t2["ref"]])
            await make_fact(client, f"On a story {n}", nodes=[s2["ref"]])
            await make_fact(client, f"On a rule {n}", rule_ids=[r2["id"]])
        for n in range(5):
            await make_fact(client, f"Elsewhere {n}", nodes=[s3["ref"]])
        walked: list[int] = []
        cursor = None
        pages = 0
        first_total = None
        while True:
            page = await ok(client, "list_notes", within_node=e2["ref"], limit=25, after_id=cursor)
            first_total = page["total"] if first_total is None else first_total
            walked += ids(page["items"])
            pages += 1
            cursor = page["next_after_id"]
            if cursor is None:
                break
        everything = await ok(client, "list_notes", within_node=e2["ref"], limit=200)
        proof(
            "M27",
            "list_notes over 60 notes, in pages, returns every one exactly once and a count that "
            "matches; within an epic it returns what concerns its stories, tasks and acceptance "
            "criteria and nothing else",
            len(walked) == len(set(walked)) == first_total == 60 == len(everything["items"])
            and pages == 3,
            f"{pages} pages of 25",
        )

        # -- a person's word, for what a person confirmed
        agent_two, agent_three = await agents("agent-2"), await agents("agent-3")
        sure = await make_fact(client, "A statement Marie confirmed")
        confirm_on_board("note", sure, "Marie")
        message = await refused(agent_two, "withdraw_note", note_ids=[sure["id"]], reason="r")
        under_her_name = await refused(namesake, "withdraw_note", note_ids=[sure["id"]], reason="r")
        await ok(
            agent_two, "withdraw_note", note_ids=[sure["id"]], reason="She changed her mind",
            said_by="Marie", quote="In the planning call: 'no longer how we think about it'",
        )  # fmt: skip
        last = (await ok(client, "note_history", note_id=sure["id"]))["items"][-1]
        ended = await ok(client, "get_note", note_id=sure["id"])
        idea = await make_fact(client, "Just an idea")
        alone = await ok(agent_three, "withdraw_note", note_ids=[idea["id"]], reason="r")
        proof(
            "M27",
            "replacing or withdrawing a confirmed note needs said_by and quote, and then works "
            "with both the person and the agent in the history; an agent alone can withdraw a "
            "note nobody confirmed",
            f"note {sure['id']} (confirmed by Marie)" in message
            and "needs that person's word" in message
            and "said_by" in message
            and (last["actor"], last["after"]["said_by"]) == (agents.named("agent-2"), "Marie")
            and alone["withdrawn_ids"] == [idea["id"]],
            f'"{clip(message, 80)}"',
        )
        proof(
            "M16",
            "a note keeps the confirmation it had when it ends: a confirmed note that is "
            "withdrawn is shown as withdrawn and still confirmed by Marie, and an agent called by "
            "her name got no further than any other",
            (ended["status"], ended["confirmed_by"]) == ("withdrawn", "Marie")
            and ended["confirmed_at"]
            and f"note {sure['id']} (confirmed by Marie)" in under_her_name
            and "needs that person's word" in under_her_name,
            f"withdrawn, confirmed by {ended['confirmed_by']}",
        )

        # -- the database on its own
        target = (await make_fact(client, "Kept as a record"))["id"]
        decided = (await make_decision(client, "Kept as a record too"))["id"]
        proof(
            "M27",
            "the database refuses, on its own, edits and deletes of notes, whether somebody "
            "decided them or not",
            database_refuses(
                f"UPDATE notes SET text = 'x' WHERE {mine()} AND id = {target}",
                "cannot be edited",
            )
            and database_refuses(
                f"DELETE FROM notes WHERE {mine()} AND id = {target}", "cannot be deleted"
            )
            and database_refuses(
                f"UPDATE notes SET why = 'x' WHERE {mine()} AND id = {decided}",
                "cannot be edited",
            )
            and database_refuses(
                f"DELETE FROM notes WHERE {mine()} AND id = {decided}", "cannot be deleted"
            ),
        )

        # -- M16: a decision, its why and who decided it, found the same everywhere
        choice = await make_decision(
            client, "Use a table, not a queue", nodes=[story["ref"]], rule_ids=[rule["id"]],
            reason="The queue was more than we need.", decided_by="Ann",
        )  # fmt: skip
        in_list = (await ok(client, "list_notes", node=story["ref"], limit=200))["items"]
        recalled = await ok(client, "recall", node=story["ref"])
        in_recall = {n["id"]: n for n in entries(recalled["notes"], "note")}
        on_the_rule = entries((await ok(client, "recall", rule_id=rule["id"]))["notes"], "note")
        checks = {
            "its why and decider": (choice["why"], choice["decided_by"], choice["decided"])
            == ("The queue was more than we need.", "Ann", True)
            and bool(choice["recorded_at"]),
            "what it concerns": (choice["nodes"], choice["rule_ids"])
            == ([story["ref"]], [rule["id"]]),
            "get": await ok(client, "get_note", note_id=choice["id"]) == choice,
            "list": [n for n in in_list if n["id"] == choice["id"]] == [choice],
            "recall of the story": in_recall.get(choice["id"]) == choice,
            "recall of the acceptance criterion": [
                n for n in on_the_rule if n["id"] == choice["id"]
            ]
            == [choice],
            "the note nobody decided": in_recall.get(fact["id"])
            == await ok(client, "get_note", note_id=fact["id"]),
        }
        wrong_ones = [name for name, held in checks.items() if not held]
        proof(
            "M16",
            "a note with its why, and a decision with its why and named decider, linked to work, "
            "are found with the same ids and fields by get, by list and by exact recall",
            not wrong_ones,
            f"differs: {', '.join(wrong_ones)}"
            if wrong_ones
            else f"note {choice['id']} decided by Ann, note {fact['id']} from {fact['why']}",
        )
        decided_ok = confirm_on_board("note", choice, "Marie", quote="Yes, that was it")
        events = (await ok(client, "note_history", note_id=choice["id"]))["items"]
        proof(
            "M16",
            "a decision is confirmed through the same operation, with who confirmed it and when, "
            "attributed to the person signed in who made the change, and logged",
            (decided_ok["status"], decided_ok["confirmed_by"]) == ("confirmed", "Marie")
            and decided_ok["confirmed_at"]
            and (events[-1]["action"], events[-1]["actor"])
            == ("note_confirmed", the_project().owner.name)
            and events[-1]["at"],
            f"confirmed by Marie at {decided_ok['confirmed_at'][:16]}",
        )
        no_word = await refused(
            namesake, "supersede_note", replaces=[choice["id"]],
            text="Use a queue after all\n\nA queue.", why="It scales.", decided_by="Ann",
        )  # fmt: skip
        before = rows("notes", "events")
        blank_why = await refused(
            client, "add_note", text="No reason\n\nx", why="  ", decided_by="Marie",
            nodes=[story["ref"]],
        )  # fmt: skip
        about_nothing = await refused(
            client, "add_note", text="x", why="Because", nodes=[story["ref"]],
            note_ids=[987654321],
        )  # fmt: skip
        proof(
            "M16",
            "a blank why, a link to a note that is not there, and a change to a confirmed decision "
            "under a person's name alone, are refused in plain words and write nothing, not even "
            "a link",
            "A note needs its reason" in blank_why
            and "There is no note with id 987654321" in about_nothing
            and f"note {choice['id']} (confirmed by Marie)" in no_word
            and "needs that person's word" in no_word
            and rows("notes", "events") == before
            and (await ok(client, "get_note", note_id=choice["id"]))["status"] == "confirmed",
            f'"{clip(no_word, 80)}"',
        )
        changed = (
            await ok(
                agent_two, "supersede_note", replaces=[choice["id"]],
                text="Use a queue after all\n\nA queue, as first thought.",
                why="It scales better.", decided_by="Ann",
                said_by="Marie", quote="Let us use a queue",
            )
        )["replacement"]  # fmt: skip
        dropped = await make_decision(client, "A dropped idea", nodes=[story["ref"]])
        await ok(agent_two, "withdraw_note", note_ids=[dropped["id"]], reason="No longer needed")
        first = await ok(client, "get_note", note_id=choice["id"])
        second_one = await ok(client, "get_note", note_id=dropped["id"])
        recalled = await ok(client, "recall", node=story["ref"])
        past_notes = {n["id"]: n for n in entries(recalled["past_notes"], "note")}
        proof(
            "M16",
            "replacing and withdrawing keep the old notes as records: the replaced one is "
            "superseded but still confirmed by Marie, the new one starts unconfirmed, and the "
            "withdrawn one says why",
            (first["status"], first["confirmed_by"]) == ("superseded", "Marie")
            and (changed["status"], changed["confirmed_by"], changed["supersedes_ids"])
            == ("unconfirmed", None, [choice["id"]])
            and (second_one["status"], second_one["status_reason"])
            == ("withdrawn", "No longer needed")
            and {choice["id"], dropped["id"]} <= set(past_notes)
            and not {choice["id"], dropped["id"]} & set(ids(entries(recalled["notes"], "note")))
            and past_notes[choice["id"]]["confirmed_by"] == "Marie"
            and changed["id"] in ids(entries(recalled["notes"], "note")),
            f"note {choice['id']} superseded by note {first['superseded_by_id']}, "
            "still confirmed by Marie",
        )

        # -- M16: a note that applies throughout a project, in that project's briefings only
        wide = await make_decision(client, "Everything is in euros", project_wide=True)
        stranger = make_project(register())
        async with connect(URL, stranger.key) as theirs:
            their_epic, their_story = await stories(theirs, 1)
            await ok(theirs, "create_node", kind="task", title="Theirs", parent=their_story["ref"])
            their_brief = await ok(theirs, "next_work", within_node=their_epic["ref"], peek=True)
            their_recall = await ok(theirs, "recall", node=their_story["ref"])
            their_list = await ok(theirs, "list_notes", limit=200)
        brief = await ok(client, "next_work", within_node=epic["ref"], peek=True)
        recalled = await ok(client, "recall", node=story["ref"])
        recalled_other = await ok(client, "recall", node=other["ref"])
        proof(
            "M16",
            "a project-wide note is in every recall and every next_work briefing of its "
            "project, linked to nothing, and in no other project's",
            wide["project_wide"] is True
            and (wide["nodes"], wide["rule_ids"]) == ([], [])
            and wide["id"] in ids(brief["context"]["project_wide_notes"])
            and wide["id"] in ids(entries(recalled["project_wide_notes"], "note"))
            and wide["id"] in ids(entries(recalled_other["project_wide_notes"], "note"))
            and wide["id"] not in ids(entries(recalled["notes"], "note"))
            and their_brief["context"]["project_wide_notes"] == []
            and their_recall["project_wide_notes"]["count"] == 0
            and their_list["items"] == [],
            "yours holds it, theirs holds none",
        )
        assert epic and rule
