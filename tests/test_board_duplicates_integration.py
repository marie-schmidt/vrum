"""M16's Possible duplicates, a mode of the search drawer, against the running containers, driven in
a real headless browser and checked against a real MCP client (see ./check.sh). The drawer's pairs
and scores are compared with `find_overlaps` (threshold, epics and paging included), and what a
click creates is read back through `list_links` and the server's own count of links. The fixture
is M5's: eight stories and their reworded twins in two epics, and more twins to make more than
one page."""

import pytest
from mcp import Client
from playwright.async_api import expect

from tests.board_helpers import SLOW, board_url, browser_page, made, plain
from tests.board_search import (
    DUPLICATES,
    address,
    drawer,
    every_overlap,
    link_count,
    links_of,
    pair_triples,
    pairs_on,
    redrawn,
    search_in_drawer,
    twin_epics,
)
from tests.helpers import ok, refused, reindexed, the_token, unique_word

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


def more_twins(count: int) -> list[tuple[str, str]]:
    """Pairs that say the same thing in other words (a customer and a client stand for the same
    idea) and nothing else alike, so each pair is alike and no two pairs are."""
    pairs = []
    for _ in range(count):
        one, other = unique_word(), unique_word()
        pairs.append((f"Customer {one} {other}", f"Client {one} {other}"))
    return pairs


# -- the pairs, their scores, the threshold, the epics and the paging ---------------------------


async def test_the_drawer_lists_mcps_pairs_with_its_scores_threshold_epics_and_paging(
    client: Client, board: str
) -> None:
    pairs = [*DUPLICATES, *more_twins(15)]
    epic_a, epic_b, firsts, seconds = await twin_epics(client, pairs)
    on_server = await every_overlap(client, epic_ids=[epic_a, epic_b])
    expected = pair_triples(on_server)
    assert len(expected) > 20
    default = on_server["threshold"]

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/graph")
        # Possible duplicates is a mode of the search drawer, opened from the top bar.
        await page.locator("#global-search-go").click()
        await expect(drawer(page, "search")).to_be_visible(timeout=SLOW)
        await page.locator("#search-tab-dups").click()
        await expect(page.locator("#drawer")).to_have_attribute("data-mode", "duplicates")
        assert (await address(page))[1]["dups"] == "1"
        await expect(page.locator("#dup-min")).to_have_value(str(default), timeout=SLOW)
        await page.locator("#dup-epics").fill(f"{epic_a}, {epic_b}")
        await page.locator("#dup-apply").click()
        assert (await address(page))[1]["dup_epics"] == f"{epic_a}, {epic_b}"

        # The first page is twenty pairs, and the count says how many there are in all.
        await expect(page.locator("#dup-pairs li.pair")).to_have_count(20, timeout=SLOW)
        await expect(page.locator("#dup-count")).to_have_text(
            f"{on_server['total']} pairs at {default} or more alike; 20 shown."
        )
        # Asking for more reads the next pages: the same pairs, in the same order, with the same
        # scores, as the server's paging gives them.
        shown = await pairs_on(page)
        assert shown == expected
        assert [round(s, 6) for _, _, s in shown] == sorted(
            [round(s, 6) for _, _, s in shown], reverse=True
        )
        small: list[tuple[int, int, float]] = []
        offset = 0
        while offset is not None:
            page_of = await ok(
                client,
                "find_overlaps",
                epic_ids=[epic_a, epic_b],
                limit=7,
                offset=offset,
            )
            small.extend(pair_triples(page_of))
            offset = page_of["next_offset"]
        assert small == expected
        await expect(page.locator("#dup-count")).to_have_text(
            f"{on_server['total']} pairs at {default} or more alike."
        )
        ours = {(a, b) for a, b, _ in shown if {a, b} <= {*firsts, *seconds}}
        assert ours == set(zip(firsts, seconds, strict=True))
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
        await page.locator("#dup-min").fill("0.99")
        await page.locator("#dup-apply").click()
        await expect(page.locator("#dup-count")).to_contain_text("at 0.99 or more", timeout=SLOW)
        assert await pairs_on(page) == pair_triples(strict)
        assert (await address(page))[1]["dup_min"] == "0.99"
        # Only one epic keeps the pairs that have something in it, which here is every pair.
        one_epic = await every_overlap(client, epic_ids=[epic_b], min_similarity=0.99)
        await page.locator("#dup-epics").fill(str(epic_b))
        await redrawn(page, "#dup-pairs", page.locator("#dup-apply").click())
        assert await pairs_on(page) == pair_triples(one_epic)
        # Starting again takes the choices off and goes back to the server's threshold.
        await redrawn(page, "#dup-pairs", page.locator("#dup-reset").click())
        await expect(page.locator("#dup-min")).to_have_value(str(default), timeout=SLOW)
        assert {"dup_min", "dup_epics"}.isdisjoint((await address(page))[1])
        # Moving from the drawer's other tab back keeps the mode in the address.
        await page.locator("#search-tab-results").click()
        await expect(page.locator("#drawer")).to_have_attribute("data-mode", "results")
        assert "dups" not in (await address(page))[1]
        assert w.problems == []


# -- nothing is linked until a click, and a click makes exactly one link ----------------------


async def test_searching_links_nothing_and_a_click_on_a_pair_makes_exactly_one_link(
    client: Client, board: str
) -> None:
    pair = DUPLICATES[1]
    epic_a, epic_b, (first,), (second,) = await twin_epics(
        client, [pair], body="Runs **nightly**, as a list:\n\n- one\n- two"
    )
    async with browser_page(board, token=the_token(), name="Ada Lovelace") as w:
        page = w.page
        await w.open(f"#/roadmap?drawer=search&dups=1&dup_epics={epic_a}")
        shown = await pairs_on(page)
        assert (first, second) in {(a, b) for a, b, _ in shown}
        row = page.locator(f"li.pair[data-first='{first}'][data-second='{second}']")
        # Both sides show their details as Markdown.
        await expect(row.locator(".side strong")).to_have_count(2)
        await expect(row.locator(".side li")).to_have_count(4)

        before = {
            "links": await link_count(client),
            "at": await links_of(client, first, second),
            "latest": (await ok(client, "changes_since", after_event_id=0, limit=1))[
                "latest_event_id"
            ],
        }
        assert before["at"] == {first: [], second: []}
        # Looking, changing the threshold and the epics, and searching, change nothing.
        await page.locator("#dup-min").fill("0.6")
        await page.locator("#dup-apply").click()
        await expect(page.locator("#dup-count")).to_contain_text("at 0.6 or more", timeout=SLOW)
        await pairs_on(page)
        await expect(row).to_be_visible(timeout=SLOW)
        await page.locator("#search-tab-results").click()
        await search_in_drawer(page, pair[0], "both")
        await expect(page.locator("#search-results li.result").first).to_be_visible(timeout=SLOW)
        await page.locator("#search-tab-dups").click()
        await pairs_on(page)
        await page.wait_for_timeout(1500)
        assert await link_count(client) == before["links"]
        assert await links_of(client, first, second) == before["at"]
        assert (await ok(client, "changes_since", after_event_id=0, limit=1))[
            "latest_event_id"
        ] == before["latest"]

        await row.get_by_role("button", name="Mark as duplicates").click()
        await expect(row.get_by_role("button", name="Already marked as duplicates")).to_be_disabled(
            timeout=SLOW
        )
        assert await links_of(client, first, second) == {
            first: [(first, second, "duplicates")],
            second: [(first, second, "duplicates")],
        }
        assert await link_count(client) == before["links"] + 1
        link = (await ok(client, "list_links", node_id=first))["items"][0]
        assert link["link_type"] == "duplicates"
        await expect(row).to_contain_text("already linked: duplicates")
        await expect(page.locator("#notices .notice")).to_contain_text(
            f"Marked #{first} and #{second} as duplicates."
        )
        assert w.problems == []

        # The other button makes the other kind of link, and only that one.
        await row.get_by_role("button", name="Mark as related").click()
        await expect(row.get_by_role("button", name="Already marked as related")).to_be_disabled(
            timeout=SLOW
        )
        assert await links_of(client, first) == {
            first: [(first, second, "duplicates"), (first, second, "relates_to")]
        }
        assert await link_count(client) == before["links"] + 2
        assert w.problems == []


async def test_a_linked_pair_says_so_and_can_be_left_out_and_a_bad_epic_is_refused(
    client: Client, board: str
) -> None:
    epic_a, epic_b, firsts, seconds = await twin_epics(client, DUPLICATES[:3])
    await ok(
        client,
        "add_link",
        actor="a",
        source_id=seconds[2],
        target_id=firsts[2],
        link_type="relates_to",
    )
    on_server = await every_overlap(client, epic_ids=[epic_a, epic_b])
    unmarked = [p for p in on_server["items"] if not p["already_linked"]]
    assert 0 < len(unmarked) < len(on_server["items"])
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/board?drawer=search&dups=1&dup_epics={epic_a},{epic_b}")
        assert await pairs_on(page) == pair_triples(on_server)
        row = page.locator(f"li.pair[data-first='{firsts[2]}'][data-second='{seconds[2]}']")
        await expect(row.get_by_role("button", name="Already marked as related")).to_be_disabled()
        await expect(row.get_by_role("button", name="Mark as duplicates")).to_be_enabled()
        await expect(row).to_contain_text("already linked: related")
        # Leaving out the pairs somebody has marked gives the server's pairs without a link.
        await redrawn(page, "#dup-pairs", page.locator("#dup-unlinked").check())
        assert (await address(page))[1]["dup_unlinked"] == "1"
        assert await pairs_on(page) == pair_triples({"items": unmarked})
        await expect(page.locator("#dup-count")).to_contain_text(
            f"{len(unmarked)} pairs at {on_server['threshold']} or more alike, "
            "none of them marked yet"
        )
        await expect(row).to_have_count(0)
        await redrawn(page, "#dup-pairs", page.locator("#dup-unlinked").uncheck())
        assert await pairs_on(page) == pair_triples(on_server)

        # An epic that is not a number is refused in the server's words, and nothing is listed.
        await page.locator("#dup-epics").fill("banana")
        await page.locator("#dup-apply").click()
        await expect(page.locator("#dup-problem")).not_to_have_text("", timeout=SLOW)
        plain(
            await page.locator("#dup-problem").inner_text(),
            await refused(client, "find_overlaps", epic_ids=["banana"]),
        )
        assert await page.locator("#dup-pairs li.pair").count() == 0
        assert w.problems == []


async def test_requirements_are_compared_too_and_cannot_be_linked(
    client: Client, board: str
) -> None:
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
    before = await link_count(client)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/board?drawer=search&dups=1&dup_type=rule&dup_epics={epic_a},{epic_b}")
        shown = await pairs_on(page)
        server = await every_overlap(client, item_type="rule", epic_ids=[epic_a, epic_b])
        assert shown == pair_triples(server) and shown
        row = page.locator(f"li.pair[data-first='{rule_a['id']}'][data-second='{rule_b['id']}']")
        await expect(row).to_be_visible()
        await expect(row.locator(".side").first).to_contain_text("After payment.")
        await expect(row.locator(".side .chip").first).to_contain_text("Requirement")
        await expect(row).to_contain_text("Requirements cannot be linked")
        assert await row.locator("button").count() == 0
        assert await link_count(client) == before
        assert w.problems == []
