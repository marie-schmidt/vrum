"""The Board's attention strip against the running containers, driven in a real headless browser
and checked against a real MCP client (see ./check.sh). Home is not a page any more: what needs
attention is the strip above the Board (`#attention`). Every number in it is compared with what the
tools give for the same question, and following a number is checked to land on exactly those
items. Titles carry a unique word, and the numbers that cover the whole project are compared at
one moment, with nothing else writing."""

import re
from typing import Any

import anyio
import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_helpers import (
    SLOW,
    board_url,
    browser_page,
    cards_are,
    made,
)
from tests.helpers import (
    all_nodes,
    connect,
    day,
    make_rule,
    ok,
    the_token,
    unique_word,
    whole_coverage_report,
)
from tests.world import make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

FREE = "kind:story,task status:ready -claimed"
TILES = ("free", "blocked", "overdue", "lapsed", "unplaced", "bare")


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


async def tile(page: Page, key: str) -> int:
    """The number on one tile of the strip."""
    link = page.locator(f"#attention a.attention-item[data-key={key}]")
    await expect(link).to_be_visible(timeout=SLOW)
    return int(await link.get_attribute("data-value") or "-1")


async def asked(client: Client) -> dict[str, int]:
    """What the tools say for each question the strip answers, asked of the server directly."""
    coverage = await ok(client, "coverage_report", show="both", limit=1)

    async def total(query: str) -> int:
        return int((await ok(client, "list_nodes", query=query, limit=1))["total"])

    return {
        "free": await total(FREE),
        "blocked": await total("status:blocked"),
        "overdue": await total("is:overdue"),
        "lapsed": await total("is:lapsed"),
        "unplaced": coverage["unplaced_rule_count"],
        "bare": coverage["story_without_rules_count"],
    }


async def busy_project(client: Client) -> dict[str, Any]:
    """Work in every state the strip counts, a requirement in no story, a story with no
    requirement, in an epic of its own."""
    word = unique_word()
    epic = await made(client, "epic", f"Counted {word}")
    story = await made(client, "story", f"Story {word}", epic)
    tasks = {
        name: await made(client, "task", f"{name} {word}", story, **more)
        for name, more in (
            ("late", {"target_on": day(-3)}),
            ("stuck", {}),
            ("going", {}),
            ("finished", {}),
            ("dropped", {}),
            ("held", {}),
            ("lapsing", {}),
            ("free", {}),
            ("waiting", {}),
        )
    }
    for name, status in (
        ("stuck", "blocked"),
        ("going", "in_progress"),
        ("finished", "done"),
        ("dropped", "dropped"),
    ):
        await ok(
            client, "update_node", actor="a", node_id=tasks[name], expected_version=1, status=status
        )
    await ok(
        client,
        "add_link",
        actor="a",
        source_id=tasks["waiting"],
        target_id=tasks["free"],
        link_type="needs_first",
    )
    await ok(client, "claim_node", actor=f"holder-{word}", node_id=tasks["held"], lease_seconds=600)
    await ok(
        client, "claim_node", actor=f"quitter-{word}", node_id=tasks["lapsing"], lease_seconds=1
    )
    await make_rule(client, f"Unplaced {word}")
    placed = await made(client, "story", f"Has a requirement {word}", epic)
    await make_rule(client, f"Placed {word}", story_id=placed)
    await anyio.sleep(1.5)  # a claim of one second has lapsed
    return {"word": word, "epic": epic, "story": story, "placed": placed, "tasks": tasks}


# -- 1: every number is what the tools say, and following it shows those items -------------------


async def test_the_strip_is_the_first_thing_on_the_board_and_every_number_is_what_the_tools_say(
    client: Client, board: str
) -> None:
    await busy_project(client)
    expected = await asked(client)
    assert all(expected[key] >= 1 for key in TILES), expected
    # "Ready work nobody holds" is what the tool for ready work counts, too.
    assert expected["free"] == (await ok(client, "ready_work", limit=1))["total"]

    async with browser_page(board, token=the_token()) as w:
        # The landing view is the Board, and the strip is above it, with no other page needed.
        await w.open("")
        assert w.page.url.endswith("#/board")
        await expect(w.page.locator("h1")).to_have_text("Board")
        strip = w.page.locator("section#attention")
        await expect(strip).to_have_attribute("aria-label", "What needs attention")
        await expect(strip.locator("h2")).to_have_text("Needs attention")
        shown = {key: await tile(w.page, key) for key in TILES}
        assert shown == expected
        assert await strip.locator("a.attention-item").count() == len(TILES)
        # A number says in words what it counts, and a number that is zero is marked so.
        await expect(strip.locator("a[data-key=free]")).to_contain_text("ready work nobody holds")
        await expect(strip.locator("a[data-key=lapsed]")).to_contain_text(
            "claims that ran out with no handoff note"
        )
        await expect(strip.locator("a[data-key=unplaced]")).to_contain_text(
            "requirements without a story"
        )
        await expect(strip.locator("a[data-key=bare]")).to_contain_text(
            "stories with no requirement"
        )
        assert w.problems == []


async def test_following_a_number_lands_on_the_view_that_shows_exactly_those_items(
    client: Client, board: str
) -> None:
    world = await busy_project(client)
    expected = await asked(client)
    everything = {n["id"]: n for n in await all_nodes(client)}

    def lane_of(node_id: int) -> int:
        node = everything[node_id]
        while node["kind"] != "epic":
            node = everything[node["parent_id"]]
        return int(node["id"])

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/board")
        await tile(page, "free")

        async def follow(key: str) -> None:
            await w.go("#/board")
            link = page.locator(f"#attention a.attention-item[data-key={key}]")
            await expect(link).to_be_visible(timeout=SLOW)
            await link.click()

        # The work: the Board, filtered by the very question, shows the same count of items, and
        # exactly those cards, each in the lane of its epic.
        for key, query in (
            ("free", FREE),
            ("blocked", "status:blocked"),
            ("overdue", "is:overdue"),
            ("lapsed", "is:lapsed"),
        ):
            await follow(key)
            matched = await all_nodes(client, query=query)
            assert len(matched) == expected[key], key
            await expect(page.locator("#filter")).to_have_value(query, timeout=SLOW)
            assert (await page.evaluate("location.hash")).startswith(
                f"#/board?q={query.replace(' ', '+')}"
            )
            await cards_are(page, {n["id"] for n in matched if n["kind"] != "epic"})
            lanes = set(
                await page.locator("section.lane").evaluate_all(
                    "els => els.map(e => Number(e.dataset.epic))"
                )
            )
            assert lanes == {lane_of(n["id"]) for n in matched}, key
        # The work of this test is among them.
        await follow("blocked")
        await expect(page.locator(f".card[data-id='{world['tasks']['stuck']}']")).to_be_visible()

        # The requirements in no story: the list of requirements in Graph's context view says the
        # same number, and holds exactly those requirements.
        report = await whole_coverage_report(client)
        await follow("unplaced")
        assert (await page.evaluate("location.hash")).startswith("#/graph?view=context&as=list")
        summary = page.locator("#requirements-summary")
        await expect(summary).to_have_attribute("data-unplaced", str(expected["unplaced"]))
        await expect(summary).to_have_attribute("data-stories-without", str(expected["bare"]))
        unplaced = page.locator("#unplaced-group li[data-kind=rule]")
        await expect(unplaced).to_have_count(expected["unplaced"], timeout=SLOW)
        assert {int(await row.get_attribute("data-id") or 0) for row in await unplaced.all()} == {
            rule["id"] for rule in report["unplaced_rules"]
        }

        # The stories with no requirement: the coverage report in the panel lists those stories.
        await follow("bare")
        assert (await page.evaluate("location.hash")).startswith("#/board?panel=coverage")
        part = page.locator("#panel [data-part=stories-without-requirements]")
        await expect(part.locator("h3")).to_have_text(
            f"Stories with no requirement ({expected['bare']})", timeout=SLOW
        )
        assert expected["bare"] <= 200
        assert {
            int(await row.get_attribute("data-story") or 0)
            for row in await part.locator("li").all()
        } == {story["id"] for story in report["stories_without_rules"]}
        assert world["story"] in {story["id"] for story in report["stories_without_rules"]}
        assert world["placed"] not in {story["id"] for story in report["stories_without_rules"]}
        assert w.problems == []


# -- the strip is live ---------------------------------------------------------------------


async def test_the_strip_follows_what_agents_do_with_no_reload_and_a_claim_that_runs_out(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Live {word}")
    story = await made(client, "story", f"Story {word}", epic)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/board")
        before = {key: await tile(page, key) for key in TILES}
        await page.evaluate("window.__same = 'same page'")

        # A task that an agent blocks, and a requirement it writes, change two numbers.
        task = await made(client, "task", f"Stuck {word}", story)
        await ok(
            client, "update_node", actor="a", node_id=task, expected_version=1, status="blocked"
        )
        await make_rule(client, f"Loose {word}")
        link = page.locator("#attention a[data-key=blocked]")
        await expect(link).to_have_attribute("data-value", str(before["blocked"] + 1), timeout=SLOW)
        await expect(page.locator("#attention a[data-key=unplaced]")).to_have_attribute(
            "data-value", str(before["unplaced"] + 1), timeout=SLOW
        )
        assert await page.evaluate("window.__same") == "same page"

        # A claim that runs out changes nothing that is written, so the strip asks again by itself.
        quitter = await made(client, "task", f"Quits {word}", story)
        await ok(client, "claim_node", actor=f"quitter-{word}", node_id=quitter, lease_seconds=1)
        lapsed = page.locator("#attention a[data-key=lapsed]")
        await expect(lapsed).to_have_attribute(
            "data-value", str(before["lapsed"] + 1), timeout=40_000
        )
        assert (await asked(client)) == {key: await tile(page, key) for key in TILES}
        assert w.problems == []


async def test_a_project_with_nothing_in_it_says_so_and_every_number_is_zero() -> None:
    project = make_project(register())
    word = unique_word()
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key) as w,
    ):
        await w.open("#/board")
        await expect(w.page.locator("#board-empty")).to_contain_text("Nothing here yet")
        for key in TILES:
            assert await tile(w.page, key) == 0, key
            await expect(w.page.locator(f"#attention a[data-key={key}]")).to_have_class(
                re.compile(r"\bnone\b")
            )
        # An epic with nothing in it has a lane of its own and says so with its zero counts.
        epic = await made(agent, "epic", f"Empty {word}")
        lane = w.page.locator(f"section.lane[data-epic='{epic}']")
        await expect(lane).to_contain_text("0 of 0 tasks done", timeout=SLOW)
        assert await lane.locator(".card").count() == 0
        await expect(w.page.locator("#board-empty")).to_have_count(0)
        assert (await asked(agent)) == {key: 0 for key in TILES}
        assert w.problems == []
