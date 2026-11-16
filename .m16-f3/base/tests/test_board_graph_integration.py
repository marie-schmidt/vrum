"""M10 against the running containers: the graph view, driven in a real headless browser and checked
against a real MCP client (see ./check.sh). The one-epic, local and overlap views run on the check
stack; the "Everything" view needs a graph of a known size, so it runs on a second, empty copy of
Vrum (tests/second_stack.py) that is made for the test and removed after it."""

import re
from collections.abc import Iterator
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Locator, Page, expect

from tests.board_helpers import Watched, board_url, browser_page
from tests.helpers import (
    all_nodes,
    make_belief,
    make_decision,
    make_fact,
    make_rule,
    ok,
    reindexed,
    the_token,
    unique_word,
)
from tests.second_stack import SecondStack

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SLOW = 15_000


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


def svg(page: Page) -> Locator:
    return page.locator("svg#graph-svg")


def dot(page: Page, key: str) -> Locator:
    return svg(page).locator(f".dot[data-key='{key}']")


def edge(page: Page, kind: str, source: str, target: str) -> Locator:
    return svg(page).locator(
        f".edge[data-type='{kind}'][data-source='{source}'][data-target='{target}']"
    )


async def dot_keys(page: Page) -> list[str]:
    keys: list[str] = (
        await svg(page).locator(".dot").evaluate_all("els => els.map(e => e.dataset.key)")
    )
    return sorted(keys)


async def made(
    client: Client, kind: str, title: str, parent: int | None = None, **more: Any
) -> int:
    node = await ok(
        client,
        "create_node",
        actor="agent-1",
        kind=kind,
        title=title,
        **({"parent_id": parent} if parent else {}),
        **more,
    )
    return int(node["id"])


async def drawn(w: Watched, address: str) -> None:
    await w.open(address)
    await expect(svg(w.page)).to_have_attribute("data-ready", "true", timeout=SLOW)


# -- 7. every kind of item and line --------------------------------------------------------------


async def test_the_graph_shows_each_item_with_the_right_line_and_only_what_is_switched_on(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Billing {word}")
    other_epic = await made(client, "epic", f"Elsewhere {word}")
    s1 = await made(client, "story", f"First story {word}", epic)
    s2 = await made(client, "story", f"Second story {word}", epic)
    t1 = await made(client, "task", f"Task one {word}", s1)
    t2 = await made(client, "task", f"Task two {word}", s1)
    t3 = await made(client, "task", f"Task three {word}", s2)
    await ok(client, "add_link", actor="a", source_id=t2, target_id=t1, link_type="needs_first")
    await ok(client, "add_link", actor="a", source_id=s1, target_id=s2, link_type="relates_to")
    rule = await make_rule(client, f"A rule {word}", story_id=s1)
    decision = await make_decision(client, f"A decision {word}", node_ids=[s1])
    fact = await make_fact(client, f"A fact {word}", node_ids=[s1])
    belief = await make_belief(client, [fact], f"A belief {word}", node_ids=[s1])

    async with browser_page(board, token=the_token()) as w:
        await drawn(w, f"#/graph?epic={epic}")
        page = w.page
        for key in (epic, s1, s2, t1, t2, t3):
            await expect(dot(page, f"n:{key}")).to_have_count(1)
        for parent, child in ((epic, s1), (epic, s2), (s1, t1), (s1, t2), (s2, t3)):
            await expect(edge(page, "hierarchy", f"n:{parent}", f"n:{child}")).to_have_count(1)
        await expect(edge(page, "needs_first", f"n:{t2}", f"n:{t1}")).to_have_count(1)
        await expect(edge(page, "relates_to", f"n:{s1}", f"n:{s2}")).to_have_count(1)
        # Rules, decisions, facts and beliefs are not there until they are switched on.
        for kind in ("rule", "decision", "principle", "fact", "belief"):
            assert await svg(page).locator(f".dot[data-type='{kind}']").count() == 0

        await page.locator("[name=show-rules]").check()
        await expect(dot(page, f"r:{rule['id']}")).to_have_count(1)
        await expect(edge(page, "placed", f"r:{rule['id']}", f"n:{s1}")).to_have_count(1)
        await page.locator("[name=show-decisions]").check()
        await expect(dot(page, f"d:{decision['id']}")).to_have_count(1)
        await expect(edge(page, "concerns", f"d:{decision['id']}", f"n:{s1}")).to_have_count(1)
        await page.locator("[name=show-facts]").check()
        await expect(dot(page, f"m:{fact['id']}")).to_have_count(1)
        await expect(edge(page, "concerns", f"m:{fact['id']}", f"n:{s1}")).to_have_count(1)
        await page.locator("[name=show-beliefs]").check()
        await expect(dot(page, f"m:{belief['id']}")).to_have_count(1)
        await expect(edge(page, "concerns", f"m:{belief['id']}", f"n:{s1}")).to_have_count(1)
        await expect(edge(page, "evidence", f"m:{belief['id']}", f"m:{fact['id']}")).to_have_count(
            1
        )
        # A belief is drawn as a suggestion: its own shape, and its words say so.
        assert await dot(page, f"m:{belief['id']}").locator(".belief").count() == 1
        assert (
            "belief"
            in (await dot(page, f"m:{belief['id']}").get_attribute("aria-label") or "").lower()
        )
        assert "show=" in page.url and "decisions" in page.url  # the choice is in the address
        await page.locator("[name=show-rules]").uncheck()
        await expect(dot(page, f"r:{rule['id']}")).to_have_count(0)

        # What an agent does shows without a reload.
        await ok(client, "add_link", actor="a", source_id=t3, target_id=t1, link_type="needs_first")
        await expect(edge(page, "needs_first", f"n:{t3}", f"n:{t1}")).to_have_count(1, timeout=SLOW)

        # A story moved on the board changes its hierarchy line in the graph.
        await w.go(f"#/board?epic={epic}&card={s2}")
        await page.locator("#move-parent").select_option(str(other_epic))
        await page.get_by_role("button", name="Move", exact=True).click()
        await expect(page.locator("#panel .card-detail > p", has_text="In epic")).to_contain_text(
            f"#{other_epic} Elsewhere"
        )
        assert (await ok(client, "get_node", node_id=s2))["parent_id"] == other_epic
        await w.go(f"#/graph?epic={other_epic}&show=decisions")
        await expect(edge(page, "hierarchy", f"n:{other_epic}", f"n:{s2}")).to_have_count(1)
        await w.go(f"#/graph?epic={epic}")
        await expect(edge(page, "hierarchy", f"n:{epic}", f"n:{s2}")).to_have_count(0)
        # It is still shown, as something outside this epic, because it is linked to a story here.
        await expect(dot(page, f"n:{s2}")).to_have_attribute("data-outside", "true")
        assert w.problems == [], w.problems


async def test_a_filter_by_kind_status_and_line_is_kept_in_the_address(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Filters {word}")
    story = await made(client, "story", f"Story {word}", epic)
    done = await made(client, "task", f"Done task {word}", story)
    open_ = await made(client, "task", f"Open task {word}", story)
    await ok(client, "update_node", actor="a", node_id=done, expected_version=1, status="done")
    await ok(client, "add_link", actor="a", source_id=open_, target_id=done, link_type="relates_to")

    async with browser_page(board, token=the_token()) as w:
        await drawn(w, f"#/graph?epic={epic}")
        page = w.page
        await page.locator("#filters summary").click()
        await page.locator("[name=status-done]").uncheck()
        await expect(dot(page, f"n:{done}")).to_have_count(0)
        assert "hide_status=done" in page.url
        await page.locator("[name=kind-task]").uncheck()
        await expect(svg(page).locator(".dot[data-type='task']")).to_have_count(0)
        await page.locator("[name=kind-task]").check()
        await page.locator("[name=link-contains]").uncheck()
        await expect(svg(page).locator(".edge[data-type='hierarchy']")).to_have_count(0)
        assert "hide_link=contains" in page.url
        # Share the link: the same view comes back, with nothing but the address.
        address = page.url
        await w.go("#/board")
        await page.goto(address.replace("#", "?x=1#", 1))
        await expect(svg(page)).to_have_attribute("data-ready", "true", timeout=SLOW)
        await expect(dot(page, f"n:{done}")).to_have_count(0)
        await expect(svg(page).locator(".edge[data-type='hierarchy']")).to_have_count(0)
        assert w.problems == [], w.problems


# -- 8. choosing a dot, the local graph, the keyboard and the suggested overlaps -----------------


async def test_choosing_a_dot_opens_the_board_card_and_the_local_graph_is_the_neighbours(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Around {word}")
    story = await made(client, "story", f"Chosen story {word}", epic, target_on="2031-05-06")
    sibling = await made(client, "story", f"Sibling story {word}", epic)
    task = await made(client, "task", f"Chosen task {word}", story)
    other = await made(client, "task", f"Other task {word}", story)
    await ok(
        client, "add_link", actor="a", source_id=story, target_id=sibling, link_type="relates_to"
    )
    rule = await make_rule(client, f"A rule {word}", story_id=story)
    decision = await make_decision(client, f"A decision {word}", node_ids=[story])
    fact = await make_fact(client, f"A fact {word}", node_ids=[story])
    belief = await make_belief(client, [fact], f"A belief {word}", node_ids=[story])
    # The index has caught up, so the card says the same whenever it is opened.
    await reindexed(client)
    everything = "show=rules,decisions,facts,beliefs"

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await drawn(w, f"#/graph?epic={epic}&{everything}")
        await dot(page, f"n:{story}").click()
        panel = page.locator("#panel .card-detail")
        await expect(panel).to_contain_text(f"#{story} Chosen story")
        await expect(panel).to_contain_text("Target 2031-05-06")
        await expect(panel.locator("li.fact")).to_contain_text(f"A fact {word}")
        await expect(panel.locator("li.fact")).to_contain_text("Origin: stated")
        await expect(panel.locator("li.belief")).to_contain_text("Suggestion")
        await expect(panel.locator("li.belief")).to_contain_text("Origin: stated")
        from_graph = await panel.inner_text()
        # It is the card the board opens, word for word.
        await w.go(f"#/board?epic={epic}")
        await expect(page.locator("#panel")).to_be_hidden()
        await page.locator(f".card[data-id='{story}']").click()
        await expect(page.locator("#card-title")).to_contain_text(f"#{story} Chosen story")
        assert await page.locator("#panel .card-detail").inner_text() == from_graph

        # The local graph at one step: exactly the item and its neighbours.
        await drawn(w, f"#/graph?scope=local&card={story}&{everything}")
        wanted = {
            f"n:{story}", f"n:{epic}", f"n:{sibling}", f"n:{task}", f"n:{other}",
            f"r:{rule['id']}", f"d:{decision['id']}", f"m:{fact['id']}", f"m:{belief['id']}",
        }  # fmt: skip
        assert await dot_keys(page) == sorted(wanted)
        neighbours = page.locator("#graph-neighbours button.neighbour")
        listed: list[str] = await neighbours.evaluate_all("els => els.map(e => e.dataset.key)")
        assert sorted(listed) == sorted(wanted - {f"n:{story}"})
        # Two steps reach one further: the other story of the epic is two steps from the task.
        await w.go(f"#/graph?scope=local&card={task}&depth=2&{everything}")
        await expect(dot(page, f"n:{other}")).to_have_count(1)
        await expect(dot(page, f"n:{sibling}")).to_have_count(1)
        await w.go(f"#/graph?scope=local&card={task}&{everything}")
        await expect(dot(page, f"n:{story}")).to_have_count(1)
        assert await dot(page, f"n:{sibling}").count() == 0

        # The neighbour list offers the same choices as a click on the dot.
        await w.go(f"#/graph?scope=local&card={story}&{everything}")
        await page.locator(f"#graph-neighbours button[data-key='n:{task}']").click()
        await expect(dot(page, f"n:{task}")).to_have_class(re.compile(r"\bchosen\b"))
        await expect(page.locator("#card-title")).to_contain_text(f"#{task} Chosen task")
        await dot(page, f"n:{story}").click()
        await expect(dot(page, f"n:{story}")).to_have_class(re.compile(r"\bchosen\b"))
        await expect(page.locator("#card-title")).to_contain_text(f"#{story} Chosen story")
        assert w.problems == [], w.problems


async def test_the_whole_graph_can_be_reached_and_chosen_from_the_keyboard(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Keys {word}")
    story = await made(client, "story", f"Keyed story {word}", epic)
    task = await made(client, "task", f"Keyed task {word}", story)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await drawn(w, f"#/graph?epic={epic}")
        # Every dot is also an item of the list beside the drawing.
        listed: list[str] = await page.locator("#graph-items button.item").evaluate_all(
            "els => els.map(e => e.dataset.key)"
        )
        assert (
            sorted(listed) == await dot_keys(page) == sorted(f"n:{n}" for n in (epic, story, task))
        )
        await page.locator("#graph-find").fill("keyed task")
        items = page.locator("#graph-items button.item")
        await expect(items).to_have_count(1)
        await items.first.focus()
        await page.keyboard.press("Enter")
        await expect(page.locator("#card-title")).to_contain_text(f"#{task} Keyed task")
        # A dot takes the focus and is chosen with Enter, and pinned with P.
        await dot(page, f"n:{story}").focus()
        await page.keyboard.press("p")
        await expect(dot(page, f"n:{story}")).to_have_class(re.compile(r"\bpinned\b"))
        await page.keyboard.press("p")
        await expect(dot(page, f"n:{story}")).not_to_have_class(re.compile(r"\bpinned\b"))
        await dot(page, f"n:{story}").focus()
        await page.keyboard.press("Enter")
        await expect(page.locator("#card-title")).to_contain_text(f"#{story} Keyed story")
        assert w.problems == [], w.problems


async def test_suggested_overlaps_are_dashed_labelled_suggestions_and_change_no_link(
    client: Client, board: str
) -> None:
    title = " ".join(unique_word() for _ in range(4))
    epic = await made(client, "epic", f"One {title}")
    twin_epic = await made(client, "epic", f"Two {title}")
    story = await made(client, "story", f"Story {title}", epic)
    twin = await made(client, "story", f"Story {title}", twin_epic)
    await reindexed(client)
    before = [
        (await ok(client, "list_links", node_id=node_id))["items"] for node_id in (story, twin)
    ]

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await drawn(w, f"#/graph?epic={epic}")
        overlap = svg(page).locator(".edge[data-type='overlap']")
        assert await overlap.count() == 0
        await page.locator("[name=overlaps]").check()
        pair = svg(page).locator(
            f".edge[data-type='overlap'][data-source^='n:'][data-target^='n:']:is("
            f"[data-source='n:{story}'][data-target='n:{twin}'],"
            f"[data-source='n:{twin}'][data-target='n:{story}'])"
        )
        await expect(pair).to_have_count(1)
        assert await pair.evaluate("e => getComputedStyle(e).strokeDasharray") != "none"
        assert "suggestion" in (await pair.locator("title").text_content() or "")
        label = svg(page).locator(".edge-label", has_text="might be the same")
        assert await label.count() >= 1
        assert "suggestion from the index" in (await label.first.text_content() or "")
        await expect(dot(page, f"n:{twin}")).to_have_attribute("data-outside", "true")
        await page.locator("[name=overlaps]").uncheck()
        await expect(overlap).to_have_count(0)
    after = [
        (await ok(client, "list_links", node_id=node_id))["items"] for node_id in (story, twin)
    ]
    assert after == before


# -- 9. everything, and above the limit ----------------------------------------------------------


@pytest.fixture
def stack() -> Iterator[SecondStack]:
    second = SecondStack()
    try:
        yield second
    finally:
        second.close()


async def test_everything_shows_every_item_and_link_and_collapses_each_epic_above_the_limit(
    stack: SecondStack,
) -> None:
    stack.up()
    forty: list[dict[str, Any]] = []
    for epic in range(3):
        forty.append({"key": f"e{epic}", "kind": "epic", "title": f"Epic {epic}"})
        for story in range(3):
            name = f"s{epic}{story}"
            forty.append(
                {"key": name, "kind": "story", "title": f"Story {name}", "parent": f"e{epic}"}
            )
    for number in range(28):
        parent = f"s{number % 3}{(number // 3) % 3}"
        forty.append(
            {"key": f"t{number}", "kind": "task", "title": f"Task {number}", "parent": parent}
        )
    assert len(forty) == 40
    links = [
        {"source": "t1", "target": "t0", "link_type": "needs_first"},
        {"source": "t2", "target": "t1", "link_type": "needs_first"},
        {"source": "s01", "target": "s00", "link_type": "needs_first"},
        {"source": "s10", "target": "s00", "link_type": "relates_to"},
        {"source": "s21", "target": "s11", "link_type": "supersedes"},
        {"source": "e1", "target": "e2", "link_type": "duplicates"},
    ]
    board = board_url(stack.url, stack.project)
    async with stack.client() as client:
        await ok(client, "create_plan", actor="agent-1", items=forty, links=links)
        nodes = await all_nodes(client)
        known: dict[int, dict[str, Any]] = {}
        for node in nodes:
            for link in (await ok(client, "list_links", node_id=node["id"]))["items"]:
                known[link["id"]] = link
        assert len(nodes) == 40 and len(known) == len(links)

        async with browser_page(board, token=stack.key) as w:
            page = w.page
            await drawn(w, "#/graph?scope=all")
            await expect(svg(page)).to_have_attribute("data-dots", "40")
            assert await dot_keys(page) == sorted(f"n:{n['id']}" for n in nodes)
            for link in known.values():
                await expect(
                    edge(
                        page, link["link_type"], f"n:{link['source_id']}", f"n:{link['target_id']}"
                    )
                ).to_have_count(1)
            assert await svg(page).locator(".edge[data-type='hierarchy']").count() == 37
            assert await svg(page).locator(".edge").count() == 37 + len(known)

            # A graph with more than a thousand dots: each epic becomes one dot.
            big: list[dict[str, Any]] = []
            for epic in range(10):
                big.append({"key": f"E{epic}", "kind": "epic", "title": f"Big epic {epic}"})
                for story in range(10):
                    name = f"S{epic}-{story}"
                    big.append({"key": name, "kind": "story", "title": name, "parent": f"E{epic}"})
                    for task in range(10):
                        big.append(
                            {
                                "key": f"{name}-{task}",
                                "kind": "task",
                                "title": f"T{epic}{story}{task}",
                                "parent": name,
                            }
                        )
            planned = await ok(client, "create_plan", actor="agent-1", items=big)
            ids = {item["key"]: item["id"] for item in planned["created"]}
            collapsed = svg(page)
            await expect(collapsed).to_have_attribute("data-collapsed", "true", timeout=SLOW)
            await expect(collapsed).to_have_attribute("data-dots", "13")
            notice = await page.locator("#graph-notice").inner_text()
            assert "each epic is one dot" in notice and "1,150 items" in notice, notice
            first = dot(page, f"n:{ids['E3']}")
            await expect(first).to_have_attribute("data-collapsed", "true")
            await first.click()
            # Opening one epic shows its items.
            await expect(collapsed).to_have_attribute("data-collapsed", "false")
            expected = sorted(
                f"n:{item}" for key, item in ids.items() if key == "E3" or key.startswith("S3-")
            )
            assert len(expected) == 111
            assert await dot_keys(page) == expected
            assert w.problems == [], w.problems
