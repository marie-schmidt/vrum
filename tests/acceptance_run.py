"""Chapter 13 of the acceptance scenario: Home, Activity, Tools and Status on the board (M14),
driven in a real headless browser against the real containers, with what a page shows compared
with what a real MCP client gets from the same tools. Home's numbers and where they lead, an
agent's work and a person's claim in the feed, the Tools page against the server's own list, and
the Status page, the export and the secrets that are never shown."""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp import Client
from playwright.async_api import Error as BrowserError
from playwright.async_api import Page, expect

from tests.acceptance_scenario import NO_MODEL_URL, URL, Failed, chapter, clip, proof
from tests.board_helpers import (
    SLOW,
    board_url,
    browser_page,
    every_overlap,
    http,
    made,
)
from tests.board_m14 import (
    HOME_TILES,
    busy_project,
    events_of,
    every_item,
    feed_ids,
    feed_row,
    home_asked,
    home_tile,
    ids_of,
    spoken,
)
from tests.helpers import (
    REPO_ROOT,
    connect,
    day,
    embeddings_stopped,
    export_everything,
    make_decision,
    ok,
    reindexed,
    the_token,
    unique_word,
)

ADA = "Ada Lovelace"
NEWS = 15_000


async def running() -> None:
    chapter("Running it and seeing it all: Home, Activity, Tools and Status")
    page_url = board_url(URL)
    try:
        async with connect(URL, the_token()) as client:
            await home(client, page_url)
            await activity(client, page_url)
            await filters(client, page_url)
            await tools(client, page_url)
            await status(client, page_url)
    except BrowserError as error:
        raise Failed(
            f"M14: the board did not do what a proof needs: {clip(str(error), 220)}"
        ) from error


# -- 1 --------------------------------------------------------------------------------------------


async def home(client: Client, page_url: str) -> None:
    world = await busy_project(client)
    expected = await home_asked(client)
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("")
        first = await page.locator("h1").inner_text()
        shown = {key: await home_tile(page, key) for key in HOME_TILES}
        health = await page.locator("a.tile[data-key=health]").get_attribute("data-value")
        proof(
            "M14",
            "Home is the first page after signing in, and every number on it equals what the "
            "tools say for the same question (list_nodes with filters, ready_work, "
            "coverage_report, the decisions, the beliefs, the overlap finder, the index)",
            first == "Home" and shown == expected and health == "ok",
            ", ".join(f"{k} {v}" for k, v in shown.items()),
        )

        async def follow(key: str) -> None:
            await w.go("#/home")
            await page.locator(f"a.tile[data-key={key}]").click()

        landed = []
        for key, query in (
            ("blocked", "status:blocked"),
            ("overdue", "is:overdue"),
            ("lapsed", "is:lapsed"),
            ("free", "kind:story,task status:ready -claimed"),
        ):
            await follow(key)
            total = expected[key]
            await expect(page.locator("#count")).to_contain_text(
                re.compile(rf"^{total} items? match this filter"), timeout=SLOW
            )
            there = set(
                await page.locator("tr.item").evaluate_all(
                    "els => els.map(e => Number(e.dataset.id))"
                )
            )
            wanted = set(await ids_of(client, query))
            landed.append(there == wanted if total <= 300 else there <= wanted)
        proof(
            "M14",
            "following the number of blocked, overdue, lapsed or free work lands on the Table "
            "filtered by that very question, showing the same count and the same items",
            all(landed),
            "blocked, overdue, lapsed, free",
        )

        await follow("unplaced")
        await expect(page.locator("#unplaced-rules h3")).to_have_text(
            f"Rules in no story ({expected['unplaced']})", timeout=SLOW
        )
        await expect(page.locator("#stories-without-rules h3")).to_have_text(
            f"Stories with no rule ({expected['no-rules']})", timeout=SLOW
        )
        stale = {
            m["id"] for m in await every_item(client, "list_memories", type="belief", stale=True)
        }
        await follow("stale")
        await expect(page.locator("li.entry.belief")).to_have_count(len(stale), timeout=SLOW)
        on_memory = {
            int(await e.get_attribute("data-memory") or 0)
            for e in await page.locator("li.entry.belief").all()
        }
        pairs = {
            (p["first"]["id"], p["second"]["id"])
            for p in (await every_overlap(client))["items"]
            if not p["already_linked"]
        }
        await follow("overlaps")
        await expect(page.locator("#count")).to_contain_text(
            re.compile(rf"^{expected['overlaps']} pairs?"), timeout=SLOW
        )
        for _ in range(len(pairs) // 20 + 5):
            more = page.locator("#show-more")
            if await more.count() == 0:
                break
            before = await page.locator("li.pair").count()
            await more.click()
            await expect(page.locator("li.pair")).not_to_have_count(before, timeout=SLOW)
        on_overlaps = {
            (int(a), int(b))
            for a, b in await page.locator("li.pair").evaluate_all(
                "els => els.map(e => [e.dataset.first, e.dataset.second])"
            )
        }
        proof(
            "M14",
            "the rule numbers land on the Rules page with the same two counts, the stale beliefs "
            "on the Memory page with exactly those beliefs, and the overlap candidates on the "
            "Overlaps page with exactly the pairs nobody has marked",
            on_memory == stale and on_overlaps == pairs,
            f"{len(stale)} stale beliefs, {len(pairs)} pairs",
        )

        await w.go("#/home")
        counts_hold = True
        for status in ("new", "ready", "in_progress", "blocked", "done", "dropped"):
            wanted_ids = await ids_of(
                client, f"epic:{world['epic']} kind:story,task status:{status}"
            )
            link = page.locator(f"a.status-link[data-epic='{world['epic']}'][data-status={status}]")
            await expect(link).to_have_attribute("data-value", str(len(wanted_ids)), timeout=SLOW)
            if wanted_ids:
                await link.click()
                await expect(page.locator("#count")).to_contain_text(
                    re.compile(rf"^{len(wanted_ids)} items? match this filter"), timeout=SLOW
                )
                got = await page.locator("tr.item").evaluate_all(
                    "els => els.map(e => Number(e.dataset.id))"
                )
                counts_hold = counts_hold and set(got) == set(wanted_ids)
                await w.go("#/home")
        proof(
            "M14",
            "an epic's count of each status equals list_nodes for it, and following a count "
            "shows exactly those items on the Table",
            counts_hold,
            f"epic #{world['epic']}",
        )


# -- 2 --------------------------------------------------------------------------------------------


async def activity(client: Client, page_url: str) -> None:
    word = unique_word()
    agent = f"agent-{word}"
    epic = await made(client, "epic", f"Epic {word}")
    story = await made(client, "story", f"Story {word}", epic)
    task = await made(client, "task", f"Work {word}", story)
    rule = await ok(client, "create_rule", actor="a", title=f"Rule {word}", story_id=story)
    note = f"Built the whole thing for {word}. Nothing is left, but check the logs."
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open(f"#/activity?actor={agent}")
        await expect(page.locator("#count")).to_have_text("Nothing has happened yet.", timeout=SLOW)
        await page.evaluate("window.__same = 'same page'")
        taken = await ok(client, "next_work", actor=agent, within_node_id=epic)
        await ok(
            client,
            "update_rule",
            actor=agent,
            rule_id=rule["id"],
            expected_version=1,
            title=f"Renamed {word}",
        )
        decided = await make_decision(client, f"Decided {word}", actor=agent, node_ids=[story])
        claim = page.locator(f"#claims-live li.claim[data-node='{task}']")
        await expect(claim).to_contain_text(f"held by {agent}", timeout=NEWS)
        working = await page.locator(f"#agents li.agent[data-actor='{agent}']").get_attribute(
            "data-state"
        )
        counting = await claim.locator(".countdown").inner_text()
        await ok(client, "finish_work", actor=agent, node_id=task, outcome="done", note=note)
        done = feed_row(page, f"finished their work on task #{task}")
        await expect(done).to_contain_text(agent, timeout=NEWS)
        rows = {
            "took": await feed_row(page, f"took task #{task}").inner_text(),
            "rule": await feed_row(page, f"changed rule {rule['id']}").inner_text(),
            "decision": await feed_row(page, f"recorded decision {decided['id']}").inner_text(),
        }
        await expect(claim).to_have_count(0, timeout=NEWS)
        agent_state = await page.locator(f"#agents li.agent[data-actor='{agent}']").get_attribute(
            "data-state"
        )
        proof(
            "M14",
            "through MCP an agent takes work with next_work, edits a rule, records a decision and "
            "finishes with a handoff note; within seconds, with no reload, Activity shows each in "
            "plain words with the actor, the handoff note in full, and the claim in Who is "
            "working on what (with a countdown) until it is finished",
            taken["found"]
            and all(agent in text for text in rows.values())
            and f"“Rule {word}” → “Renamed {word}”" in rows["rule"]
            and f"“Decided {word}”, decided by Marie" in rows["decision"]
            and spoken(await done.locator(".handoff-note").inner_text()) == note
            and working == "working"
            and "left" in counting
            and agent_state == "finished"
            and await page.evaluate("window.__same") == "same page",
            f'"{clip(rows["took"], 60)}"',
        )

    person = f"Lee {word}"
    mine = await made(client, "task", f"Mine {word}", story)
    short = await made(client, "task", f"Short {word}", story)
    async with browser_page(page_url, token=the_token(), name=person) as w:
        page = w.page
        await w.open("#/activity")
        await page.locator("#lease").fill("60")
        await page.locator("#claim-id").fill(f"#{mine}")
        await page.locator("#claim-by-id-go").click()
        held = page.locator(f"#claims-live li.claim[data-node='{mine}']")
        await expect(held).to_contain_text(f"held by {person}", timeout=NEWS)
        claimed_by = (await ok(client, "get_node", node_id=mine))["claimed_by"]
        before = (await ok(client, "get_node", node_id=mine))["claim_expires_at"]
        await held.locator("button.renew").click()
        await expect(feed_row(page, f"{person} kept task #{mine}")).to_be_visible(timeout=NEWS)
        after = (await ok(client, "get_node", node_id=mine))["claim_expires_at"]
        await held.locator("button.release").click()
        dialog = page.locator("dialog.modal")
        await dialog.locator("#release-note").fill(f"Stopped here for {word}.")
        await dialog.get_by_role("button", name="Release it").click()
        await expect(dialog).to_have_count(0, timeout=SLOW)
        released = await ok(client, "get_node", node_id=mine)
        proof(
            "M14",
            "in the browser a person claims a task with a typed name, keeps it with Renew (a "
            "longer lease), and lets it go with a note, each the same operation as the agent's",
            claimed_by == person
            and after > before
            and released["claimed_by"] is None
            and released["last_handoff"]["actor"] == person
            and released["last_handoff"]["note"] == f"Stopped here for {word}.",
            f"#{mine} as {person}",
        )

        await page.locator("#lease").fill("3")
        await page.locator("#claim-id").fill(str(short))
        await page.locator("#claim-by-id-go").click()
        live = page.locator(f"#claims-live li.claim[data-node='{short}']")
        await expect(live).to_be_visible(timeout=NEWS)
        lapsed = page.locator(f"#claims-lapsed li.claim[data-node='{short}']")
        await expect(lapsed).to_be_visible(timeout=NEWS)
        state = await page.locator(f"#agents li.agent[data-actor='{person}']").get_attribute(
            "data-state"
        )
        node = await ok(client, "get_node", node_id=short)
        who = {a["actor"]: a for a in (await ok(client, "who_is_working"))["agents"]}
        proof(
            "M14",
            "a claim that runs out moves by itself to lapsed, and the holder's state follows; the "
            "server says the same through get_node and who_is_working",
            state == "lapsed"
            and node["claimed_by"] is None
            and node["lapsed_claim_by"] == person
            and who[person]["state"] == "lapsed",
            f"#{short}",
        )


async def filters(client: Client, page_url: str) -> None:
    word = unique_word()
    ann, bob = f"ann-{word}", f"bob-{word}"
    epic = await ok(client, "create_node", actor=ann, kind="epic", title=f"Epic {word}")
    one = await ok(
        client, "create_node", actor=ann, kind="story", title=f"One {word}", parent_id=epic["id"]
    )
    two = await ok(
        client, "create_node", actor=bob, kind="story", title=f"Two {word}", parent_id=epic["id"]
    )
    rule = await ok(client, "create_rule", actor=ann, title=f"Rule {word}", story_id=one["id"])
    await ok(
        client,
        "move_rule",
        actor=ann,
        rule_id=rule["id"],
        expected_version=1,
        new_story_id=two["id"],
    )
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/home")
        held = []
        for address, wanted in (
            (f"#/activity?actor={ann}", await events_of(client, actor=ann)),
            (f"#/activity?item=rule:{rule['id']}", await events_of(client, rule_id=rule["id"])),
            (f"#/activity?item=%23{one['id']}", await events_of(client, node_id=one["id"])),
        ):
            await w.go(address)
            ids = [e["id"] for e in reversed(wanted)]
            for _ in range(150):
                if await feed_ids(page) == ids:
                    break
                await page.wait_for_timeout(100)
            held.append(await feed_ids(page) == ids)
        await w.go(f"#/activity?actor={ann}&kind=rules")
        await expect(feed_row(page, f"{ann} moved rule {rule['id']}")).to_contain_text(
            f"from story #{one['id']} “One {word}” to story #{two['id']} “Two {word}”",
            timeout=SLOW,
        )
        rules_only = [
            e["id"]
            for e in reversed(await events_of(client, actor=ann))
            if e["action"].startswith("rule_")
        ]
        kind_holds = await feed_ids(page) == rules_only
        await w.go(f"#/activity?actor={ann}&from={day(1)}")
        await expect(page.locator("#count")).to_contain_text(
            "Nothing read so far matches", timeout=SLOW
        )
        await w.go(f"#/activity?actor={ann}&from={day()}&to={day()}")
        today = [e["id"] for e in reversed(await events_of(client, actor=ann))]
        for _ in range(150):
            if await feed_ids(page) == today:
                break
            await page.wait_for_timeout(100)
        proof(
            "M14",
            "Activity is filterable by actor, kind of change, item and time, each equal to "
            "changes_since with the same filter, newest first, in plain words such as 'moved rule "
            "N from story A to story B'",
            all(held) and kind_holds and await feed_ids(page) == today,
            f"{len(rules_only)} rule changes",
        )


# -- 3 --------------------------------------------------------------------------------------------


def classification(dropping: str | None) -> subprocess.CompletedProcess[str]:
    """Run the test that every tool is on the board or marked as for agents only, as it is, or with
    one tool taken out of the map."""
    env = {**os.environ, **({"VRUM_TEST_DROP_FROM_MAP": dropping} if dropping else {})}
    return subprocess.run(
        [
            "uv",
            "run",
            "pytest",
            "tests/test_board.py",
            "-k",
            "every_tool_the_server_has_is_on_the_board",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


async def tools(client: Client, page_url: str) -> None:
    # The map of where each tool is on the board is the board's own source; the walk reads it
    # from there, as the scripts do, since it runs from the checkout.
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from vrum.board.tools import TOOL_PLACES, AgentsOnly

    listed = (await client.list_tools()).tools
    reads = {t.name for t in listed if t.annotations and t.annotations.read_only_hint}
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/tools")
        await expect(page.locator("#tool-list article.tool").first).to_be_visible(timeout=SLOW)
        names: list[str] = await page.locator("#tool-list article.tool").evaluate_all(
            "els => els.map(e => e.dataset.name)"
        )
        placed = await page.locator(".where a, .where.agents").count()
        agents: list[str] = await page.locator("article.tool[data-agents='1']").evaluate_all(
            "els => els.map(e => e.dataset.name)"
        )
        described = 0
        for tool in listed:
            entry = page.locator(f"#tool-entry-{tool.name}")
            first = (tool.description or "").split("\n\n")[0]
            fields: list[str] = await entry.locator("tr[data-field]").evaluate_all(
                "els => els.map(e => e.dataset.field)"
            )
            if spoken(await entry.locator("p.first").inner_text()) == spoken(
                first
            ) and fields == list(tool.input_schema.get("properties", {})):
                described += 1
        proof(
            "M14",
            "the Tools page lists exactly the tools the server lists (same count, same names), "
            "each with its description and fields, and each on the board or marked for agents "
            "only with the reason",
            sorted(names) == sorted(t.name for t in listed)
            and described == len(listed)
            and placed == len(listed)
            and sorted(agents)
            == sorted(n for n, p in TOOL_PLACES.items() if isinstance(p.place, AgentsOnly)),
            f"{len(listed)} tools, {len(agents)} for agents only",
        )

        word = unique_word()
        epic = await made(client, "epic", f"Tried {word}")
        entry = page.locator("#tool-entry-list_nodes")
        await entry.scroll_into_view_if_needed()
        await entry.locator("details.try summary").click()
        await entry.locator("[name=kind]").fill("epic")
        await entry.locator("[name=limit]").fill("3")
        await entry.locator("[name=query]").fill(f"epic:{epic}")
        await entry.locator(".try-run").click()
        await expect(entry.locator("pre.result")).to_be_visible(timeout=SLOW)
        shown = json.loads(await entry.locator("pre.result").inner_text())
        mcp = await ok(client, "list_nodes", kind="epic", limit=3, query=f"epic:{epic}")
        changing = page.locator("article.tool[data-changes='1']")
        status, _, text = http(
            "POST",
            page_url + "/api/try/create_node",
            token=the_token(),
            body={"actor": "x", "kind": "epic", "title": "Must not exist"},
        )
        proof(
            "M14",
            "Try it on list_nodes returns what MCP returns, no tool that changes anything has a "
            "Try it, and the server turns a try of one away",
            shown == mcp
            and await changing.count() > 20
            and await changing.locator("details.try").count() == 0
            and {n for n in names if await page.locator(f"#tool-entry-{n} details.try").count()}
            == reads
            and status == 403
            and "Must not exist" not in text,
            f"{len(reads)} read, {await changing.count()} change something",
        )
        assert w.problems == [], w.problems

    dropped = classification("reindex")
    restored = classification(None)
    proof(
        "M14",
        "the test that every tool is on the board or marked for agents only fails when reindex "
        "is taken out of the map, naming it, and passes again with it put back",
        dropped.returncode != 0
        and "reindex is neither on the board nor marked as for agents only"
        in dropped.stdout + dropped.stderr
        and restored.returncode == 0,
        "shown once, then restored",
    )


# -- 4 --------------------------------------------------------------------------------------------


async def status(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Status {word}")
    await reindexed(client)

    async def row(page: Page, name: str) -> str:
        return (
            await page.locator(f"#status-body tr[data-name={name}] .value").inner_text()
        ).strip()

    secrets = {
        "the token": the_token(),
        "the database password": os.environ["VRUM_DB_PASSWORD"],
    }
    async with browser_page(page_url, token=the_token(), name=ADA, keep_bodies=True) as w:
        page = w.page
        await w.open("#/status")
        await expect(page.locator("#versions")).to_be_visible(timeout=SLOW)
        said = await ok(client, "server_status")
        index = (await ok(client, "search", query=word, limit=1))["index"]
        equal = (
            await row(page, "vrum_version") == said["vrum_version"]
            and await row(page, "postgres_version") == said["postgres_version"]
            and await row(page, "pgvector_version") == said["pgvector_version"]
            and all([await row(page, name) == str(count) for name, count in said["counts"].items()])
            and await row(page, "waiting") == str(index["waiting_to_index"])
            and await row(page, "indexed") == str(said["index"]["indexed"])
            and await row(page, "model") == said["index"]["embedding_model"]
            and await row(page, "vector_size") == str(said["index"]["vector_size"])
        )
        proof(
            "M14",
            "Status equals server_status (versions, the size of each kind of item, the index, its "
            "model and vector size) and the index state a search reports",
            equal and said["index"]["meaning"] == index["meaning"],
            f"{said['counts']['events']} changes in the history",
        )
        with embeddings_stopped():
            await made(client, "story", f"Not indexed {word}", epic)
            await w.go("#/home")
            await w.go("#/status")
            await expect(page.locator("#status-body tr[data-name=service] .value")).to_have_text(
                "Not answering", timeout=SLOW
            )
            seen = (await ok(client, "server_status"))["index"]
            stopped = await row(page, "service") == "Not answering"
            waiting = await row(page, "waiting") == str(seen["waiting_to_index"])
        await reindexed(client)
        proof(
            "M14",
            "with the embedding service stopped Status says it is not answering and counts the "
            "item waiting, as server_status does",
            stopped and waiting and seen["embedding_service"] == "unreachable",
            f"{seen['waiting_to_index']} waiting",
        )
        await w.go("#/home")
        await w.go("#/status")
        async with page.expect_download() as waiting_for:
            await page.locator("#download-export").click()
        download = await waiting_for.value
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "export.json"
            await download.save_as(path)
            got: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        proof(
            "M14",
            "the downloaded export is identical to the MCP export, every page of it",
            got == await export_everything(client) and word in json.dumps(got),
            f"{got['counts']['nodes']} nodes, {got['counts']['events']} events",
        )
        for name in ("home", "activity", "tools", "search", "memory"):
            await w.go(f"#/{name}")
            await page.wait_for_load_state("networkidle")
        texts = await page.locator("body").inner_text()
        proof(
            "M14",
            "the token and the database password are in no response the board's pages received "
            "(searched for in every one), no page, no address and no downloaded file",
            len(w.bodies) > 20
            and all(
                not [u for u, body in w.bodies if secret in body]
                and secret not in texts
                and not [u for u in w.requests if secret in u]
                and secret not in json.dumps(got)
                for secret in secrets.values()
            ),
            f"{len(w.bodies)} responses searched",
        )
    async with browser_page(board_url(NO_MODEL_URL), token=the_token(), name=ADA) as w:
        await w.open("#/status")
        await expect(w.page.locator("#language-model")).to_contain_text(
            "None is set up", timeout=SLOW
        )
        async with connect(NO_MODEL_URL, the_token()) as bare:
            bare_status = await ok(bare, "server_status")
        proof(
            "M14",
            "on a server with no language model Status says so and names none, as server_status "
            "does",
            bare_status["language_model"] is None
            and "scripted-model" not in await w.page.locator("#language-model").inner_text(),
            "second server",
        )
