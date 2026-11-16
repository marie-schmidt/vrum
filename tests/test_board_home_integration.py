"""M14's Home page against the running containers, driven in a real headless browser and checked
against a real MCP client (see ./check.sh). Every number on Home is compared with what the tools
give for the same question, and following a number is checked to land on the page that shows
exactly those items. Titles carry a unique word, and each test looks only at what it made, so the
numbers that cover the whole database are compared at one moment, with nothing else writing."""

import re

import pytest
from mcp import Client
from playwright.async_api import expect

from tests.board_helpers import SLOW, board_url, browser_page, every_overlap, made
from tests.board_m14 import (
    HOME_TILES,
    busy_project,
    every_item,
    home_asked,
    home_tile,
    ids_of,
)
from tests.helpers import (
    day,
    embeddings_stopped,
    ok,
    reindexed,
    the_token,
    unique_word,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


# -- 1: every number is what the tools say, and following it shows those items -------------------


async def test_every_number_on_home_equals_what_the_tools_say(client: Client, board: str) -> None:
    await busy_project(client)
    expected = await home_asked(client)
    assert expected["blocked"] >= 1 and expected["lapsed"] >= 1 and expected["overdue"] >= 1
    assert expected["unplaced"] >= 1 and expected["stale"] >= 1 and expected["unconfirmed"] >= 1
    assert expected["decisions"] >= 1 and expected["overlaps"] >= 3 and expected["free"] >= 1

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/home")
        shown = {key: await home_tile(page, key) for key in HOME_TILES}
        assert shown == expected
        health = page.locator("a.tile[data-key=health]")
        status = await ok(client, "server_status")
        await expect(health).to_have_attribute("data-value", "ok")
        await expect(health).to_contain_text(f"Vrum {status['vrum_version']}")
        await expect(health).to_contain_text(status["pgvector_version"])
        assert w.problems == []


async def test_following_a_number_lands_on_the_page_that_shows_exactly_those_items(
    client: Client, board: str
) -> None:
    world = await busy_project(client)
    expected = await home_asked(client)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/home")
        await home_tile(page, "free")

        async def follow(key: str) -> None:
            await w.go("#/home")
            link = page.locator(f"a.tile[data-key={key}]")
            await expect(link).to_be_visible(timeout=SLOW)
            await link.click()

        # The work: the Table, filtered by the very question, shows the same count and the items.
        for key, query in (
            ("free", "kind:story,task status:ready -claimed"),
            ("blocked", "status:blocked"),
            ("overdue", "is:overdue"),
            ("lapsed", "is:lapsed"),
        ):
            await follow(key)
            total = expected[key]
            await expect(page.locator("#count")).to_contain_text(
                re.compile(rf"^{total} items? match this filter"), timeout=SLOW
            )
            assert (await page.evaluate("location.hash")).startswith(
                f"#/table?q={query.replace(' ', '+')}"
            )
            there = set(
                await page.locator("tr.item").evaluate_all(
                    "els => els.map(e => Number(e.dataset.id))"
                )
            )
            wanted = set(await ids_of(client, query))
            assert there == wanted if total <= 300 else there <= wanted, key

        # The rules: the Rules page says the same two numbers.
        await follow("unplaced")
        await expect(page.locator("#unplaced-rules h3")).to_have_text(
            f"Rules in no story ({expected['unplaced']})", timeout=SLOW
        )
        await expect(page.locator("#stories-without-rules h3")).to_have_text(
            f"Stories with no rule ({expected['no-rules']})", timeout=SLOW
        )

        # The memory: the entries shown are exactly the decisions, or the beliefs, counted.
        week = day(-6)
        decisions = {
            d["id"]
            for d in await every_item(client, "list_decisions", kind="decision")
            if d["decided_on"] >= week
        }
        await follow("decisions")
        await expect(page.locator("li.entry.decision")).to_have_count(len(decisions), timeout=SLOW)
        assert {
            int(await e.get_attribute("data-decision") or 0)
            for e in await page.locator("li.entry.decision").all()
        } == decisions
        stale = {
            m["id"] for m in await every_item(client, "list_memories", type="belief", stale=True)
        }
        await follow("stale")
        await expect(page.locator("li.entry.belief")).to_have_count(len(stale), timeout=SLOW)
        assert {
            int(await e.get_attribute("data-memory") or 0)
            for e in await page.locator("li.entry.belief").all()
        } == stale
        waiting = {
            m["id"]
            for m in await every_item(
                client, "list_memories", type="belief", status="active", stale=False
            )
            if m["confirmed_by"] is None
        }
        await follow("unconfirmed")
        await expect(page.locator("li.entry.belief")).to_have_count(len(waiting), timeout=SLOW)
        assert {
            int(await e.get_attribute("data-memory") or 0)
            for e in await page.locator("li.entry.belief").all()
        } == waiting

        # The overlaps: exactly the pairs nobody has marked.
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
        on_page = {
            (int(a), int(b))
            for a, b in await page.locator("li.pair").evaluate_all(
                "els => els.map(e => [e.dataset.first, e.dataset.second])"
            )
        }
        assert on_page == pairs

        # The index and the server: both lead to the Status page, which says the same.
        await follow("waiting")
        await expect(page.locator("#status-body tr[data-name=waiting] .value")).to_have_text(
            str(expected["waiting"]), timeout=SLOW
        )
        await follow("health")
        await expect(page.locator("h1")).to_have_text("Status")

        # The epics: every count of every status, and the Table shows exactly those items.
        await w.go("#/home")
        for status in ("new", "ready", "in_progress", "blocked", "done", "dropped"):
            query = f"epic:{world['epic']} kind:story,task status:{status}"
            wanted_ids = await ids_of(client, query)
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
                assert set(got) == set(wanted_ids), status
                await w.go("#/home")
        assert w.problems == []


# -- Home is live ---------------------------------------------------------------------------------


async def test_home_follows_what_agents_do_with_no_reload_and_says_when_the_index_waits(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Live {word}")
    story = await made(client, "story", f"Story {word}", epic)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/home")
        before = await home_tile(page, "blocked")
        await page.evaluate("window.__same = 'same page'")
        task = await made(client, "task", f"Stuck {word}", story)
        await ok(
            client, "update_node", actor="a", node_id=task, expected_version=1, status="blocked"
        )
        await expect(page.locator("a.tile[data-key=blocked]")).to_have_attribute(
            "data-value", str(before + 1), timeout=15_000
        )
        assert await page.evaluate("window.__same") == "same page"
        # The new epic has its row, with its counts, and a person can open its card from it.
        row = page.locator(f"li.epic-row[data-epic='{epic}']")
        await expect(row.locator("a.status-link[data-status=blocked]")).to_have_attribute(
            "data-value", "1", timeout=15_000
        )
        await row.get_by_role("link", name=f"#{epic} Live {word}").click()
        await expect(page.locator("#panel .card-detail")).to_contain_text(
            f"Live {word}", timeout=SLOW
        )

        # With the embedding service stopped an item waits, and Home says so.
        waiting = await home_tile(page, "waiting")
        with embeddings_stopped():
            await made(client, "story", f"Waits {word}", epic)
            await expect(page.locator("a.tile[data-key=waiting]")).to_have_attribute(
                "data-value", str(waiting + 1), timeout=15_000
            )
        await reindexed(client)
        await expect(page.locator("a.tile[data-key=waiting]")).to_have_attribute(
            "data-value", "0", timeout=15_000
        )
        assert w.problems == []


async def test_home_is_the_first_page_and_an_epic_with_nothing_in_it_says_so(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Empty {word}")
    async with browser_page(board, token=the_token()) as w:
        await w.open("")
        await expect(w.page.locator("h1")).to_have_text("Home")
        row = w.page.locator(f"li.epic-row[data-epic='{epic}']")
        await expect(row).to_contain_text("no stories or tasks yet", timeout=SLOW)
        for status in ("new", "ready", "in_progress", "blocked", "done", "dropped"):
            await expect(row.locator(f"a.status-link[data-status={status}]")).to_have_attribute(
                "data-value", "0"
            )
