"""M13's Overlaps page against the running containers, driven in a real headless browser and checked
against a real MCP client (see ./check.sh). The page's pairs and scores are compared with
`find_overlaps`, and what a click creates is read back through `list_links`. The fixture is M5's:
eight stories and their reworded twins in two epics."""

import pytest
from mcp import Client
from playwright.async_api import expect

from tests.acceptance_finding import DUPLICATES
from tests.board_helpers import (
    SLOW,
    board_url,
    browser_page,
    every_overlap,
    made,
    pairs_on,
    twin_epics,
)
from tests.helpers import ok, reindexed, the_token, unique_word

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


async def test_the_page_lists_the_known_duplicates_with_mcps_scores_and_not_the_unrelated_pairs(
    client: Client, board: str
) -> None:
    epic_a, epic_b, firsts, seconds = await twin_epics(client, DUPLICATES)
    on_server = await every_overlap(client, epic_ids=[epic_a, epic_b])
    expected = [(p["first"]["id"], p["second"]["id"], p["similarity"]) for p in on_server["items"]]
    assert expected

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/overlaps?epics={epic_a},{epic_b}")
        shown = await pairs_on(page)
        # The same pairs, in the same order, with the same scores.
        assert shown == expected
        await expect(page.locator("#count")).to_contain_text(
            f"{on_server['total']} pairs at {on_server['threshold']}"
        )
        ours = {(a, b) for a, b, _ in shown if {a, b} <= {*firsts, *seconds}}
        duplicates = set(zip(firsts, seconds, strict=True))
        assert ours == duplicates
        # Each pair shows its two sides and says how alike they are.
        sample = page.locator(f"li.pair[data-first='{firsts[0]}'][data-second='{seconds[0]}']")
        await expect(sample.locator(".side")).to_have_count(2)
        score = float(await sample.locator(".score strong").inner_text())
        assert score == pytest.approx(
            next(s for a, b, s in expected if (a, b) == (firsts[0], seconds[0])), abs=0.001
        )
        await expect(sample.locator(".side").first).to_contain_text(DUPLICATES[0][0])
        await expect(sample.locator(".side").last).to_contain_text(DUPLICATES[0][1])

        # A higher threshold keeps only the pairs at least that alike, as MCP does.
        strict = await every_overlap(client, epic_ids=[epic_a, epic_b], min_similarity=0.99)
        assert 0 < len(strict["items"]) < len(expected)
        await page.locator("#overlap-min").fill("0.99")
        await page.locator("#apply-threshold").click()
        await expect(page.locator("#count")).to_contain_text("at 0.99 or more", timeout=SLOW)
        again = await pairs_on(page)
        assert again == [
            (p["first"]["id"], p["second"]["id"], p["similarity"]) for p in strict["items"]
        ]
        assert w.problems == []


async def test_nothing_is_linked_until_a_click_and_a_click_makes_exactly_one_link(
    client: Client, board: str
) -> None:
    pair = DUPLICATES[1]
    epic_a, epic_b, (first,), (second,) = await twin_epics(
        client, [pair], body="Runs **nightly**, as a list:\n\n- one\n- two"
    )
    async with browser_page(board, token=the_token(), name="Ada Lovelace") as w:
        page = w.page
        await w.open(f"#/overlaps?epics={epic_a}")
        shown = await pairs_on(page)
        assert (first, second) in {(a, b) for a, b, _ in shown}
        row = page.locator(f"li.pair[data-first='{first}'][data-second='{second}']")
        # Both sides show their details as Markdown.
        await expect(row.locator(".side strong")).to_have_count(2)
        await expect(row.locator(".side li")).to_have_count(4)
        # Looking, changing the threshold and the epics change nothing.
        await page.locator("#overlap-min").fill("0.6")
        await page.locator("#apply-threshold").click()
        await expect(page.locator("#count")).to_contain_text("at 0.6 or more", timeout=SLOW)
        await pairs_on(page)
        await expect(row).to_be_visible(timeout=SLOW)
        for node in (first, second):
            assert (await ok(client, "list_links", node_id=node))["total"] == 0

        await row.get_by_role("button", name="Mark as duplicates").click()
        await expect(row.get_by_role("button", name="Already marked: duplicates")).to_be_disabled(
            timeout=SLOW
        )
        for node in (first, second):
            links = await ok(client, "list_links", node_id=node)
            assert links["total"] == 1
            (link,) = links["items"]
            assert (link["source_id"], link["target_id"], link["link_type"]) == (
                first,
                second,
                "duplicates",
            )
        await expect(row).to_contain_text("already linked")
        assert w.problems == []

        # The other button makes the other kind of link, and only that one.
        await row.get_by_role("button", name="Mark as relates to").click()
        await expect(row.get_by_role("button", name="Already marked: relates to")).to_be_disabled(
            timeout=SLOW
        )
        kinds = sorted(
            link["link_type"] for link in (await ok(client, "list_links", node_id=first))["items"]
        )
        assert kinds == ["duplicates", "relates_to"]
        assert w.problems == []


async def test_a_pair_already_linked_says_so_and_a_bad_epic_is_refused_in_the_servers_words(
    client: Client, board: str
) -> None:
    epic_a, _, (first,), (second,) = await twin_epics(client, [DUPLICATES[2]])
    await ok(
        client, "add_link", actor="a", source_id=second, target_id=first, link_type="relates_to"
    )
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/overlaps?epics={epic_a}")
        await pairs_on(page)
        row = page.locator(f"li.pair[data-first='{first}'][data-second='{second}']")
        await expect(row.get_by_role("button", name="Already marked: relates to")).to_be_disabled()
        await expect(row.get_by_role("button", name="Mark as duplicates")).to_be_enabled()
        await w.go("#/overlaps?epics=banana")
        await expect(page.locator("#overlap-problem")).not_to_have_text("", timeout=SLOW)
        assert await page.locator("li.pair").count() == 0
        assert w.problems == []


async def test_rules_are_compared_too_and_cannot_be_linked(client: Client, board: str) -> None:
    word = unique_word()
    epic_a = await made(client, "epic", f"Rules A {word}")
    epic_b = await made(client, "epic", f"Rules B {word}")
    story_a = await made(client, "story", f"Billing A {word}", epic_a)
    story_b = await made(client, "story", f"Billing B {word}", epic_b)
    one, other = "Customers receive an invoice by email", "Clients receive a bill by mail"
    rule_a = await ok(
        client, "create_rule", actor="a", title=one, story_id=story_a, body="After payment."
    )
    rule_b = await ok(
        client, "create_rule", actor="a", title=other, story_id=story_b, body="After charge."
    )
    await reindexed(client)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/overlaps?type=rule&epics={epic_a},{epic_b}")
        shown = await pairs_on(page)
        server = await every_overlap(client, item_type="rule", epic_ids=[epic_a, epic_b])
        assert shown == [
            (p["first"]["id"], p["second"]["id"], p["similarity"]) for p in server["items"]
        ]
        row = page.locator(f"li.pair[data-first='{rule_a['id']}'][data-second='{rule_b['id']}']")
        await expect(row).to_be_visible()
        await expect(row.locator(".side").first).to_contain_text("After payment.")
        await expect(row).to_contain_text("Rules cannot be linked")
        assert await row.locator("button").count() == 0
        assert w.problems == []
