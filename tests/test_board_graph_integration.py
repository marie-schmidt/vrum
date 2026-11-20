"""The Graph page against the running containers, as M21 built it, M27 changed it (decisions and
facts are notes), M30 simplified it (three tabs, Structure, Around and Order, under a box that finds
what to look at) and M34 took the side panels away: no tab has a card or a list beside its picture,
and an item clicked in a picture opens its own page. Driven in a real headless browser and checked
against a real MCP client (see ./check.sh). What the page draws is compared with what the tools say:
Structure draws every epic, story and task once and joins them by the hierarchy alone, its links are
the links the tools list, Around is the very `recall` an agent gets (what comes down from the story
and epic above marked "via"), Order starts on the first epic when nothing is chosen, and notes are
never dots of the map. What an agent receives for an item is the Agent view of the item's page
(test_board_agent_view_integration.py). The tests that need the whole of a project in a known state
work in a project of their own; the "above the limit" test needs a graph of a known size, so it runs
on a second, empty copy of Vrum (tests/second_stack.py) that is made for the test and removed
after it."""

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote

import pytest
from mcp import Client
from playwright.async_api import Locator, Page, expect

from tests.board_context import context_world, fresh_project
from tests.board_helpers import SLOW, Watched, board_url, browser_page, go, made
from tests.helpers import (
    all_nodes,
    code_of,
    make_decision,
    make_note,
    ok,
)
from tests.second_stack import SecondStack

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

MAP = "#graph-view[data-tab=structure] svg.graph-st-svg"


@pytest.fixture
def stack() -> Iterator[SecondStack]:
    second = SecondStack()
    try:
        yield second
    finally:
        second.close()


async def structure_open(w: Watched, address: str = "#/graph") -> None:
    """Open the Graph at an address of its Structure tab, loaded afresh (a hash that only changes
    is handled a moment later), and wait until its map is drawn."""
    await go(w.page, w.board, address)
    await expect(w.page.locator(MAP)).to_be_visible(timeout=SLOW)


async def press_to_open(page: Page, node: Locator, key: str, opened: Locator) -> None:
    """Put the keyboard on a node and press a key until the page it opens is there. A picture that
    is drawn anew between the two (the notes arriving) replaces the node that had the keyboard, so
    the key may need pressing again."""
    for _ in range(5):
        await node.focus()
        await page.keyboard.press(key)
        try:
            await expect(opened).to_be_visible(timeout=2000)
            return
        except AssertionError:
            continue
    await expect(opened).to_be_visible(timeout=SLOW)


async def drawn(page: Page) -> list[str]:
    """The references of the items the map draws, one dot or hub for each."""
    refs: list[str] = await page.locator(f"{MAP} g.graph-st-node").evaluate_all(
        "els => els.map(e => e.dataset.ref)"
    )
    return sorted(refs)


async def lit(page: Page, name: str) -> set[str]:
    """The references of the dots that carry one of the map's marks (is-linked, is-sel, is-lit)."""
    refs: list[str] = await page.locator(f"{MAP} g.graph-st-node.{name}").evaluate_all(
        "els => els.map(e => e.dataset.ref)"
    )
    return set(refs)


async def partners_of(client: Client, ref: str) -> set[str]:
    """What a node is linked to, as the tools list it."""
    found: set[str] = set()
    for link in (await ok(client, "list_links", node=ref, limit=200))["items"]:
        found.add(link["target"] if link["source"] == ref else link["source"])
    return found


# -- the whole project, and a project above the limit ----------------------------------------


async def test_everything_shows_every_item_and_link_and_a_big_project_starts_at_its_stories(
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
    for index in range(28):
        parent = f"s{index % 3}{(index // 3) % 3}"
        forty.append(
            {"key": f"t{index}", "kind": "task", "title": f"Task {index}", "parent": parent}
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
        await ok(client, "create_plan", items=forty, links=links)
        nodes = await all_nodes(client)
        wanted = sorted(n["ref"] for n in nodes)
        partners = {ref: await partners_of(client, ref) for ref in wanted}
        known = {
            (link["link_type"], link["source"], link["target"])
            for ref in wanted
            for link in (await ok(client, "list_links", node=ref, limit=200))["items"]
        }
        assert len(nodes) == 40 and len(known) == len(links)
        linked = [ref for ref, others in partners.items() if others]
        assert len(linked) == 10

        async with browser_page(board, token=stack.key) as w:
            page = w.page
            # Structure is where the Graph opens, and it draws the whole project at once: every
            # epic, story and task is one hub or dot, and each is joined by one line to what it is
            # part of (an epic to the project), so that nothing crosses. No link is drawn yet.
            await structure_open(w)
            assert await drawn(page) == wanted
            kinds = {
                kind: await page.locator(f"{MAP} g.graph-st-node.is-{kind}").count()
                for kind in ("epic", "story", "task")
            }
            assert kinds == {"epic": 3, "story": 9, "task": 28}
            await expect(page.locator(f"{MAP} .graph-st-line")).to_have_count(40)
            await expect(page.locator(f"{MAP} .graph-st-link")).to_have_count(0)
            assert await page.locator("#graph-view").get_attribute("data-tab") == "structure"
            await expect(page.locator("#graph-tab-structure")).to_have_attribute(
                "aria-selected", "true"
            )

            # "Show links between items" draws every link there is, once, of its own kind.
            await page.locator("button[data-choice=links]").click()
            await expect(page.locator(f"{MAP} .graph-st-link")).to_have_count(len(known))
            assert "links=1" in page.url
            for kind in ("needs_first", "relates_to", "supersedes", "duplicates"):
                wanted_kind = len([1 for one in known if one[0] == kind])
                await expect(page.locator(f"{MAP} .graph-st-link.t-{kind}")).to_have_count(
                    wanted_kind
                )
            assert await drawn(page) == wanted

            # Choosing an item (the address says which) lights what the tools say it is linked to,
            # and draws exactly those links as the chosen item's own.
            for ref in linked:
                await structure_open(w, f"#/graph?focus={ref}")
                assert await lit(page, "is-sel") == {ref}
                assert await lit(page, "is-linked") == partners[ref], ref
                await expect(page.locator(f"{MAP} .graph-st-link.is-chosen")).to_have_count(
                    len([1 for one in known if ref in one[1:]])
                )

            # An item that is not linked to anything has nobody lit beside it.
            alone = next(ref for ref in wanted if not partners[ref])
            await structure_open(w, f"#/graph?focus={alone}")
            assert await lit(page, "is-linked") == set()

            # Context is never a dot of the map: a criterion on a story, a decision, and a note
            # about both that task and the decision are in the project now, and the map still
            # draws the 40 items of work and nothing else. A record in focus lights the work it
            # concerns.
            first_task = next(n for n in nodes if n["title"] == "Task 0")
            story = first_task["parent"]
            rule = (await ok(client, "create_rule", title="A rule", node=story))["id"]
            decision = await make_decision(client, "A decision", nodes=[first_task["ref"]])
            plain = await make_note(
                client,
                "A note about both",
                "Said at the review",
                nodes=[first_task["ref"]],
                note_ids=[decision["id"]],
            )
            await structure_open(w)
            assert await drawn(page) == wanted
            await structure_open(w, f"#/graph?focus=note:{plain['id']}")
            assert await drawn(page) == wanted
            assert await lit(page, "is-linked") == {first_task["ref"]}
            await structure_open(w, f"#/graph?focus=note:{decision['id']}")
            assert await lit(page, "is-linked") == {first_task["ref"]}

            # Around is where the context is: the task's notes are what `recall` says, the
            # decided one first, and its criterion is the one of its story, marked "via" it.
            await w.open(f"#/graph?tab=around&focus={first_task['ref']}")
            around = page.locator(f".graph-around[data-focus='{first_task['ref']}']")
            await expect(around).to_be_visible(timeout=SLOW)
            recalled = await ok(client, "recall", node=first_task["ref"])
            noted = [item["note"]["id"] for item in recalled["notes"]["items"]]
            assert noted == [decision["id"], plain["id"]]
            shown: list[str] = await around.locator(
                "section[data-group=notes] button.graph-node.n-note"
            ).evaluate_all("els => els.map(e => e.dataset.ref)")
            assert shown == [f"note:{one}" for one in noted]
            # A note someone decided is not a kind of its own: no "Decision" word or mark anywhere.
            await expect(around.locator(".n-badge:has-text('Decision')")).to_have_count(0)
            requirement = around.locator(
                f"section[data-group=reqs] button.graph-node[data-ref='rule:{rule}']"
            )
            await expect(requirement.locator(".n-via")).to_have_text(f"via {code_of(story)}")
            # The decided note's own Around draws the note about it among its notes.
            await w.open(f"#/graph?tab=around&focus=note:{decision['id']}")
            await expect(
                page.locator(
                    "section[data-group=notes] button.graph-node.n-note"
                    f"[data-ref='note:{plain['id']}']"
                )
            ).to_have_count(1, timeout=SLOW)

            # A project with more than fifteen hundred items starts at its stories: the tasks are
            # drawn when they are asked for.
            big: list[dict[str, Any]] = []
            for epic in range(14):
                big.append({"key": f"E{epic}", "kind": "epic", "title": f"Big epic {epic}"})
                for story_number in range(10):
                    name = f"S{epic}-{story_number}"
                    big.append({"key": name, "kind": "story", "title": name, "parent": f"E{epic}"})
                    for task in range(10):
                        big.append(
                            {
                                "key": f"{name}-{task}",
                                "kind": "task",
                                "title": f"T{epic}{story_number}{task}",
                                "parent": name,
                            }
                        )
            await ok(client, "create_plan", items=big)
            everything = (await ok(client, "list_nodes", limit=1))["total"]
            epics = (await ok(client, "list_nodes", kind="epic", limit=1))["total"]
            stories = (await ok(client, "list_nodes", kind="story", limit=1))["total"]
            assert everything == 40 + len(big) and everything > 1500
            assert (epics, stories) == (3 + 14, 9 + 14 * 10)
            await structure_open(w)
            await expect(page.locator("button[data-choice='level:stories']")).to_have_attribute(
                "aria-pressed", "true"
            )
            assert len(await drawn(page)) == epics + stories
            assert await page.locator(f"{MAP} g.graph-st-node.is-task").count() == 0
            await page.locator("button[data-choice='level:tasks']").click()
            await expect(page.locator("button[data-choice='level:tasks']")).to_have_attribute(
                "aria-pressed", "true"
            )
            assert "level=tasks" in page.url
            await expect(page.locator(f"{MAP} g.graph-st-node")).to_have_count(
                everything, timeout=SLOW
            )
            assert w.problems == [], w.problems


# -- Structure: a node opens its page, and the Look at box is the way into one epic ---------------


async def test_a_click_or_a_key_on_a_node_opens_its_page_and_nothing_is_beside_the_map(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        world = await context_world(client, "opens", project)
        epic, story, task = world["E"], world["s1"], world["t1"]
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await structure_open(w)
            # No column, card or list beside the map, and no node is "chosen" by pointing at it.
            await expect(page.locator(".graph-x-side")).to_have_count(0)
            await expect(page.locator(f"{MAP} g.graph-st-node[aria-pressed]")).to_have_count(0)
            whole = await page.evaluate("location.hash")
            node = page.locator(f"{MAP} g.graph-st-node[data-ref='{task}']")
            await expect(node).to_have_attribute(
                "aria-label", re.compile(r"Press Enter to open it\.$")
            )
            # A click on a story, a task or an epic opens its page, in the Graph; the browser's
            # way back is the map as it was left.
            for ref in (story, task, epic):
                await page.locator(f"{MAP} g.graph-st-node[data-ref='{ref}']").click()
                await expect(page.locator(f"#item-page[data-ref='{ref}']")).to_be_visible(
                    timeout=SLOW
                )
                assert unquote(page.url).endswith(f"#/item?item={ref}&from=graph"), page.url
                await page.go_back()
                await expect(page.locator(MAP)).to_be_visible(timeout=SLOW)
                assert await page.evaluate("location.hash") == whole
            # Enter and Space do the same from the keyboard.
            for key in ("Enter", "Space"):
                await press_to_open(
                    page,
                    page.locator(f"{MAP} g.graph-st-node[data-ref='{task}']"),
                    key,
                    page.locator(f"#item-page[data-ref='{task}']"),
                )
                await page.go_back()
                await expect(page.locator(MAP)).to_be_visible(timeout=SLOW)
            # The item looked at (the box at the top) is still marked on the map, with its links
            # lit, and the map can be moved to it; it opens nothing and shows no card.
            await structure_open(w, f"#/graph?focus={task}")
            assert await lit(page, "is-sel") == {task}
            assert await lit(page, "is-linked") == await partners_of(client, task)
            await expect(page.locator(f"{MAP} g.graph-st-node.is-sel")).to_have_attribute(
                "aria-current", "true"
            )
            await expect(page.locator("[data-action=zoom-chosen]")).to_be_visible()
            await expect(page.locator(".graph-x-side")).to_have_count(0)
            assert w.problems == [], w.problems


async def test_the_look_at_box_enters_one_epic_and_its_centre_and_leaves_open_their_pages(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        world = await context_world(client, "lookat", project)
        epic, other = world["E"], world["E2"]
        inside = {world[name] for name in ("s1", "s2", "t1", "t2", "t3")}
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await structure_open(w)
            select = page.locator("#graph-st-scope")
            await expect(page.locator("label[for=graph-st-scope]")).to_have_text("Look at")
            await expect(select).to_have_value("")
            options: list[list[str]] = await select.locator("option").evaluate_all(
                "els => els.map(e => [e.value, e.textContent])"
            )
            assert options[0] == ["", "Whole project"]
            assert {value for value, _ in options[1:]} == {epic, other}
            for value, text in options[1:]:
                assert text.startswith(code_of(value)), (value, text)
            # An epic chosen there is looked at on its own, with every name, and written in the
            # address without a step of history.
            history = await page.evaluate("history.length")
            await select.select_option(epic)
            await expect(page.locator("#graph-view svg.graph-rd-svg")).to_be_visible(timeout=SLOW)
            await expect(page.locator("#graph-st-title")).to_contain_text(
                f"Focus on {code_of(epic)}"
            )
            await expect(select).to_have_value(epic)
            assert f"scope={epic}" in unquote(page.url)
            assert await page.evaluate("history.length") == history
            await expect(page.locator(".graph-x-side")).to_have_count(0)
            leaves: list[str] = await page.locator("svg.graph-rd-svg [data-ref]").evaluate_all(
                "els => els.map(e => e.dataset.ref)"
            )
            assert set(leaves) == {epic, *inside}, leaves
            # The centre and the leaves open their pages, and the way back is the radial view.
            radial = await page.evaluate("location.hash")
            for ref in (epic, world["t3"], world["s1"]):
                await page.locator(f"svg.graph-rd-svg [data-ref='{ref}']").first.click()
                await expect(page.locator(f"#item-page[data-ref='{ref}']")).to_be_visible(
                    timeout=SLOW
                )
                assert unquote(page.url).endswith(f"#/item?item={ref}&from=graph"), page.url
                await page.go_back()
                await expect(page.locator("#graph-view svg.graph-rd-svg")).to_be_visible(
                    timeout=SLOW
                )
                assert await page.evaluate("location.hash") == radial
            # By keyboard: Enter on a leaf opens it.
            await press_to_open(
                page,
                page.locator(f"svg.graph-rd-svg [data-ref='{world['t1']}']"),
                "Enter",
                page.locator(f"#item-page[data-ref='{world['t1']}']"),
            )
            await page.go_back()
            # Another epic, and then the whole project again.
            await select.select_option(other)
            await expect(page.locator("#graph-st-title")).to_contain_text(
                f"Focus on {code_of(other)}", timeout=SLOW
            )
            assert f"scope={other}" in unquote(page.url)
            await select.select_option("")
            await expect(page.locator(MAP)).to_be_visible(timeout=SLOW)
            assert "scope=" not in page.url
            assert await drawn(page) == sorted(n["ref"] for n in await all_nodes(client))
            # An address that names something that is not an epic puts the whole project right.
            await structure_open(w, f"#/graph?scope={world['t1']}")
            await expect(select).to_have_value("")
            assert w.problems == [], w.problems


# -- Around: the very recall an agent gets ---------------------------------------------------------


async def around_open(w: Watched, focus: str, extra: str = "") -> None:
    """Open the Graph's Around tab on one item, loaded afresh, and wait until it is drawn."""
    await go(w.page, w.board, f"#/graph?tab=around&focus={focus}{extra}")
    await expect(w.page.locator(f".graph-around[data-focus='{focus}']")).to_be_visible(timeout=SLOW)


async def refs_in(page: Page, selector: str) -> list[str]:
    """The `data-ref` of every element a selector finds, in the order drawn."""
    refs: list[str] = await page.locator(selector).evaluate_all(
        "els => els.map(e => e.dataset.ref)"
    )
    return refs


def note_ref(entry: dict[str, Any]) -> str:
    return f"note:{entry['note']['id']}"


async def test_around_draws_the_notes_recall_returns_for_every_kind_of_item_and_says_where_from(
    server_url: str,
) -> None:
    word = "around"
    async with fresh_project(server_url) as (client, board, project):
        world = await context_world(client, word, project)
        async with browser_page(board, token=project.key) as w:
            page = w.page
            for name, target in (
                ("s1", {"node": world["s1"]}),
                ("s2", {"node": world["s2"]}),
                ("t1", {"node": world["t1"]}),
                ("E", {"node": world["E"]}),
                ("r1", {"rule_id": world["r1"]}),
            ):
                focus = f"rule:{world['r1']}" if "rule_id" in target else world[name]
                await around_open(w, focus)
                recalled = await ok(client, "recall", limit=50, **target)
                where = (name, focus)
                # The notes in force, in recall's order (the ones someone decided come first);
                # the earlier ones are in the same box, under "Earlier, kept for the reason".
                entries = recalled["notes"]["items"]
                box = "section[data-group=notes]"
                earlier = await refs_in(
                    page, f"{box} p.graph-gbox-sub + ul button.graph-node.n-note"
                )
                everything = await refs_in(page, f"{box} button.graph-node.n-note")
                assert [ref for ref in everything if ref not in earlier] == [
                    note_ref(one) for one in entries
                ], where
                assert earlier == [note_ref(one) for one in recalled["past_notes"]["items"]], where
                decided = [one["note"]["decided"] for one in entries]
                assert decided == sorted(decided, reverse=True), where
                for one in entries:
                    node = page.locator(f"button.graph-node[data-ref='{note_ref(one)}']")
                    note = one["note"]
                    confirmed = note["status"] == "confirmed"
                    await expect(node.locator(".n-badge.decided")).to_have_count(0)
                    await expect(node.locator(".n-badge.ok")).to_have_count(int(confirmed))
                    await expect(node.locator(".n-badge.warn")).to_have_count(int(not confirmed))
                    assert ("is-unconfirmed" in (await node.get_attribute("class") or "")) == (
                        not confirmed
                    ), (where, note["id"])
                    # What came down from the story or epic above says through what.
                    via = one["inherited_from"]
                    await expect(node.locator(".n-via")).to_have_count(int(bool(via)))
                    if via:
                        await expect(node.locator(".n-via")).to_have_text(f"via {code_of(via)}")
                        assert "is-via" in (await node.get_attribute("class") or ""), where
                # The picture has the whole width: one panel, and no list, card or toggle beside it.
                await expect(page.locator(".graph-around > *")).to_have_count(1)
                await expect(page.locator(".graph-around > section.graph-ego-panel")).to_have_count(
                    1
                )
                await expect(
                    page.locator(
                        "#graph-people, #graph-agent, #graph-peek, #graph-mode-people,"
                        " #graph-mode-agent, .graph-x-side"
                    )
                ).to_have_count(0)
                # The project-wide notes apply to everything, so they are one line that opens.
                wide = [note_ref(one) for one in recalled["project_wide_notes"]["items"]]
                assert wide == [f"note:{world['dw']}"], where
                toggle = page.locator("#graph-wide-toggle")
                await expect(toggle).to_have_attribute("aria-expanded", "false")
                await toggle.click()
                await expect(toggle).to_have_attribute("aria-expanded", "true")
                assert "wide=1" in page.url
                assert (
                    await refs_in(
                        page,
                        "ul[aria-label='Project-wide notes. It scrolls.'] button.graph-node.n-note",
                    )
                    == wide
                )
            assert w.problems == [], w.problems


async def test_around_at_reach_two_adds_what_is_under_the_item_and_keeps_to_one_picture(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        world = await context_world(client, "reachtwo", project)
        s1 = world["s1"]
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await around_open(w, s1)
            one_step = await refs_in(page, "section[data-group=notes] button.graph-node")
            await expect(page.locator("#graph-reach-1")).to_have_attribute("aria-pressed", "true")
            await page.locator("#graph-reach-2").click()
            await expect(page.locator("#graph-reach-2")).to_have_attribute("aria-pressed", "true")
            assert "reach=2" in page.url
            two_steps = await refs_in(page, "section[data-group=notes] button.graph-node")
            assert set(one_step) <= set(two_steps)
            await expect(page.locator(".graph-around > *")).to_have_count(1)
            assert w.problems == [], w.problems


async def test_around_a_work_neighbour_is_looked_around_and_a_note_or_a_criterion_opens_its_page(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        world = await context_world(client, "neighbours", project)
        s1, t1 = world["s1"], world["t1"]
        note, rule = f"note:{world['n1']}", f"rule:{world['r1']}"
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await around_open(w, s1)
            around = f".graph-around[data-focus='{s1}']"
            # A neighbour that is work is looked around in its turn: the address changes, and the
            # page stays in the Graph.
            task = page.locator(
                f"{around} section[data-group=down] button.graph-node[data-ref='{t1}']"
            )
            await expect(task).to_have_attribute(
                "aria-label", re.compile(r"Press Enter to look around it\.$")
            )
            await task.click()
            await expect(page.locator(f".graph-around[data-focus='{t1}']")).to_be_visible(
                timeout=SLOW
            )
            assert f"focus={t1}" in page.url.replace("%3A", ":") and "#/item" not in page.url
            # A note or an acceptance criterion is not looked around: it opens its own page, in the
            # Graph, and the way back is the picture as it was left.
            for ref, group in ((note, "notes"), (rule, "reqs")):
                await around_open(w, s1)
                left = await page.evaluate("location.hash")
                neighbour = page.locator(
                    f"{around} section[data-group={group}] button.graph-node[data-ref='{ref}']"
                )
                await expect(neighbour).to_have_attribute(
                    "aria-label", re.compile(r"Press Enter to open it\.$")
                )
                assert await neighbour.get_attribute("aria-pressed") is None
                await neighbour.click()
                await expect(page.locator(f"#item-page[data-ref='{ref}']")).to_be_visible(
                    timeout=SLOW
                )
                assert unquote(page.url).endswith(f"#/item?item={ref}&from=graph"), page.url
                await page.go_back()
                await expect(page.locator(around)).to_be_visible(timeout=SLOW)
                assert await page.evaluate("location.hash") == left
            # The same by keyboard.
            await around_open(w, s1)
            await press_to_open(
                page,
                page.locator(
                    f"{around} section[data-group=notes] button.graph-node[data-ref='{note}']"
                ),
                "Enter",
                page.locator(f"#item-page[data-ref='{note}']"),
            )
            assert w.problems == [], w.problems


async def test_around_draws_an_item_with_no_notes_and_nothing_under_it(server_url: str) -> None:
    async with fresh_project(server_url) as (client, board, project):
        alone = await made(client, "epic", "An epic with nothing in it")
        epic = await made(client, "epic", "An epic with one leaf")
        story = await made(client, "story", "A story with one leaf", epic)
        leaf = await made(client, "task", "A leaf with no notes", story)
        async with browser_page(board, token=project.key) as w:
            page = w.page
            # Nothing is joined to it: the hub, no group of work or notes, and the line that says
            # what was checked and not found.
            await around_open(w, alone)
            await expect(page.locator("#graph-hub")).to_be_visible()
            for group in ("up", "down", "needs", "blocks", "reqs", "notes"):
                await expect(page.locator(f"section[data-group={group}]")).to_have_count(0)
            await expect(page.locator(".graph-around .graph-also").first).to_contain_text(
                "Checked, nothing found: Part of (this is the top level)"
            )
            # A leaf with no notes: the hub, and what it is part of.
            await around_open(w, leaf)
            await expect(page.locator("#graph-hub")).to_have_attribute("data-ref", leaf)
            assert await refs_in(page, "section[data-group=up] button.graph-node") == [story]
            await expect(page.locator("section[data-group=notes]")).to_have_count(0)
            assert w.problems == [], w.problems


# -- the box at the top: what to look at ----------------------------------------------------------


async def options(page: Page) -> list[str]:
    """The references the box's list offers, in the order drawn."""
    return await refs_in(page, "#graph-find-popup .graph-find-option")


async def test_the_box_finds_an_item_by_a_word_or_a_code_and_shows_what_is_chosen(
    server_url: str,
) -> None:
    word = "findbox"
    async with fresh_project(server_url) as (client, board, project):
        world = await context_world(client, word, project)
        epics = [one["ref"] for one in await all_nodes(client) if one["kind"] == "epic"]
        assert len(epics) == 2
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await go(page, board, "#/graph")
            box = page.locator("#graph-find-input")
            chosen = page.locator("#graph-find-chosen")
            # A search box in the look of the filter box, with no filter language: no "i" card.
            await expect(box).to_have_attribute(
                "placeholder", "Find an epic, story, task, acceptance criterion or note to look at"
            )
            await expect(box).to_have_attribute("role", "combobox")
            await expect(page.locator("#graph-find")).to_have_class(re.compile(r"\bfilter\b"))
            await expect(page.locator("#filter-info")).to_have_count(0)
            await expect(page.locator("#graph-scope-button")).to_have_count(0)
            await expect(chosen).to_be_hidden()
            # Focused and empty, it lists the epics, in their order, so there is a way to browse.
            await box.click()
            await expect(box).to_have_attribute("aria-expanded", "true")
            assert await options(page) == epics
            # A word of a title finds one item; a code finds the item of that kind and number.
            for typed, found in (
                (f"t3 {word}", [world["t3"]]),
                (code_of(world["s2"]), [world["s2"]]),
                (f"story {world['s2'].split(':')[1]}", [world["s2"]]),
                (f"criterion {world['r1']}", [f"rule:{world['r1']}"]),
                (f"note {world['d1']}", [f"note:{world['d1']}"]),
            ):
                await box.fill(typed)
                await expect(page.locator("#graph-find-popup .graph-find-option")).to_have_count(
                    len(found)
                )
                assert await options(page) == found, typed
            # No filter syntax: field:value is only words, and nothing has them.
            await box.fill("kind:task")
            await expect(page.locator("#graph-find-none")).to_contain_text("Nothing matches")
            await expect(page.locator("#graph-find-popup .graph-find-option")).to_have_count(0)
            # Arrow keys move through the list (and wrap round), Enter chooses; the choice is in the
            # address and in the box (its sign, code and title).
            await box.fill("")
            await expect(page.locator("#graph-find-popup .graph-find-option")).to_have_count(2)
            await page.keyboard.press("ArrowDown")
            await expect(box).to_have_attribute("aria-activedescendant", "graph-find-option-1")
            await page.keyboard.press("ArrowDown")
            await expect(box).to_have_attribute("aria-activedescendant", "graph-find-option-0")
            await page.keyboard.press("ArrowUp")
            await expect(box).to_have_attribute("aria-activedescendant", "graph-find-option-1")
            await page.keyboard.press("Enter")
            target = epics[1]
            await expect(chosen).to_have_attribute("data-ref", target, timeout=SLOW)
            assert f"focus={target}" in page.url.replace("%3A", ":")
            await expect(chosen).to_contain_text(code_of(target))
            await expect(chosen).to_contain_text(f"E2 {word}")
            await expect(box).to_have_attribute("aria-expanded", "false")
            # Escape closes the list; the x goes back to the whole project.
            await box.click()
            await expect(box).to_have_attribute("aria-expanded", "true")
            await page.keyboard.press("Escape")
            await expect(box).to_have_attribute("aria-expanded", "false")
            await page.locator("#graph-focus-clear").click()
            await expect(chosen).to_be_hidden()
            assert "focus=" not in page.url
            # A narrow window: the list fits the window, under the box.
            await page.set_viewport_size({"width": 390, "height": 700})
            await box.click()
            popup = page.locator("#graph-find-popup")
            await expect(popup).to_be_visible()
            where = await popup.bounding_box()
            assert where and where["x"] >= 0 and where["x"] + where["width"] <= 390
            assert where["y"] + where["height"] <= 700
            assert w.problems == [], w.problems


async def test_an_address_from_before_the_timeline_and_the_check_up_went_opens_structure(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        await made(client, "epic", "An epic to show")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await go(page, board, "#/graph?tab=timeline&since=7&check=reqs&chains=all")
            await expect(page.locator("#graph-tab-structure")).to_have_attribute(
                "aria-selected", "true", timeout=SLOW
            )
            await expect(page.locator(MAP)).to_be_visible(timeout=SLOW)
            await expect(page.locator("#graph-tab-timeline")).to_have_count(0)
            await expect(page.locator("#graph-checkup")).to_have_count(0)
            # The address is put right, with nothing said.
            await expect(page).to_have_url(re.compile(r"#/graph$"))
            assert w.problems == [], w.problems


# -- Order: the first epic or the chosen one, in phases, in chains, and with sizes -----------------


async def order_open(w: Watched, address: str) -> None:
    """Open the Graph's Order tab at an address, loaded afresh, and wait until it is drawn."""
    await go(w.page, w.board, address)
    await expect(w.page.locator("#graph-view[data-tab=order] .graph-or")).to_be_visible(
        timeout=SLOW
    )


async def phase_world(client: Client, word: str) -> dict[str, str]:
    """One epic of nine tasks in three stories, to read as phases. b needs a, c needs b, e needs a
    and i needs h; d has no link, f is done and g is dropped. So phase 1 is a, d and h, phase 2 is
    b, e and i, and phase 3 is c; d and f are loose, and there are two chains."""
    ids: dict[str, str] = {"E": await made(client, "epic", f"Order {word}")}
    for story in ("s1", "s2", "s3"):
        ids[story] = await made(client, "story", f"Story {story} {word}", ids["E"])
    for name, story in (
        ("a", "s1"),
        ("b", "s1"),
        ("c", "s1"),
        ("d", "s1"),
        ("e", "s2"),
        ("f", "s2"),
        ("g", "s2"),
        ("h", "s3"),
        ("i", "s3"),
    ):
        ids[name] = await made(client, "task", f"Task {name} {word}", ids[story])
    for source, target in (("b", "a"), ("c", "b"), ("e", "a"), ("i", "h")):
        await ok(
            client, "add_link", source=ids[source], target=ids[target], link_type="needs_first"
        )
    for name, status in (("f", "done"), ("g", "dropped")):
        node = await ok(client, "get_node", node=ids[name])
        await ok(
            client,
            "update_node",
            actor="agent-1",
            node=ids[name],
            expected_version=node["version"],
            status=status,
        )
    return ids


async def phases_from_the_tools(client: Client) -> list[list[str]]:
    """The phases the server's own links give: the open tasks, each in the phase after the last of
    the open tasks it needs first. A task is in phase 1 when it needs nothing that is still open."""
    tasks = {n["ref"]: n for n in await all_nodes(client, kind="task")}
    open_refs = {r for r, n in tasks.items() if n["status"] not in ("done", "dropped")}
    needs: dict[str, list[str]] = {}
    for ref in open_refs:
        links = (await ok(client, "list_links", node=ref, limit=200))["items"]
        needs[ref] = [
            link["target"]
            for link in links
            if link["link_type"] == "needs_first"
            and link["source"] == ref
            and link["target"] in open_refs
        ]
    phase: dict[str, int] = {}

    def of(ref: str) -> int:
        if ref not in phase:
            phase[ref] = 1 + max((of(other) for other in needs[ref]), default=0)
        return phase[ref]

    most = max((of(r) for r in open_refs), default=0)
    return [
        sorted((r for r in open_refs if of(r) == step), key=lambda r: tasks[r]["number"])
        for step in range(1, most + 1)
    ]


async def test_order_with_nothing_chosen_is_the_first_epic_in_phases_and_says_so(
    server_url: str,
) -> None:
    word = "ordered"
    async with fresh_project(server_url) as (client, board, project):
        # A project with no epics has nothing to order.
        async with browser_page(board, token=project.key) as w:
            await go(w.page, board, "#/graph?tab=order")
            await expect(w.page.locator("#graph-view")).to_contain_text(
                "There is nothing to order yet", timeout=SLOW
            )
            assert w.problems == [], w.problems
        world = await context_world(client, word, project)
        first, second = world["E"], world["E2"]
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await go(page, board, "#/graph?tab=order")
            lead = page.locator("#graph-or-lead")
            await expect(lead).to_contain_text("This is the first epic.", timeout=SLOW)
            await expect(page.locator("#graph-or-title")).to_contain_text(
                f"Order of work in {code_of(first)} E {word}"
            )
            # Every open task of the first epic is there, the loose one too: phase 1 is the two
            # that need nothing, phase 2 the one that waits for t1.
            await expect(page.locator("#graph-or-step")).to_have_text("Phase 1 of 2")
            assert await refs_in(page, "#graph-or-phase li.graph-or-item") == [
                world["t1"],
                world["t3"],
            ]
            # What went with the chains list, and "the longest chains", is gone.
            await expect(page.get_by_text("The same chains, as a list")).to_have_count(0)
            await expect(page.locator(".graph-x-side")).to_have_count(0)
            await expect(page.locator("#graph-view")).not_to_contain_text("longest")
            # A second epic with no link has one phase with its task in it.
            await go(page, board, f"#/graph?tab=order&focus={second}")
            await expect(page.locator("#graph-or-step")).to_have_text("Phase 1 of 1", timeout=SLOW)
            assert await refs_in(page, "#graph-or-phase li.graph-or-item") == [world["t9"]]
            # A chosen task is highlighted in its epic's phase, and has its chain in Chains.
            await go(page, board, f"#/graph?tab=order&focus={world['t2']}")
            await expect(page.locator("#graph-or-step")).to_have_text("Phase 2 of 2", timeout=SLOW)
            await expect(
                page.locator(f"li.graph-or-item.is-sel[data-ref='{world['t2']}']")
            ).to_be_visible()
            await expect(page.locator("#graph-or-lead")).to_have_count(0)
            await page.locator("button[data-choice='mode:chains']").click()
            await expect(page.locator(f".graph-or-node[data-ref='{world['t1']}']")).to_be_visible(
                timeout=SLOW
            )
            await expect(page.locator("button[data-choice='only']")).to_have_count(0)
            await expect(page.locator("button[data-choice='blocked']")).to_be_visible()
            await expect(page.locator(".graph-x-side")).to_have_count(0)
            # Clearing the choice goes back to the first epic.
            await page.locator("#graph-focus-clear").click()
            await expect(lead).to_contain_text("the first epic", timeout=SLOW)
            assert w.problems == [], w.problems


async def test_order_lists_every_open_task_in_phases_that_match_the_links_one_phase_at_a_time(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        ids = await phase_world(client, "phases")
        want = await phases_from_the_tools(client)
        assert want == [
            [ids["a"], ids["d"], ids["h"]],
            [ids["b"], ids["e"], ids["i"]],
            [ids["c"]],
        ]
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await order_open(w, f"#/graph?tab=order&focus={ids['E']}")
            summary = page.locator(".graph-or-summary")
            await expect(summary).to_contain_text("7 to do")
            await expect(summary).to_contain_text("1 done")
            await expect(summary).to_contain_text("1 dropped not shown")
            # No size anywhere: no budget, no batch, only the items and the stories they are in.
            await expect(page.locator("#graph-or-budget")).to_have_count(0)
            await expect(page.locator(".graph-or-batch")).to_have_count(0)
            for step, refs in enumerate(want, start=1):
                await expect(page.locator("#graph-or-step")).to_have_text(
                    f"Phase {step} of {len(want)}"
                )
                await expect(page.locator("#graph-or-phase")).to_have_attribute(
                    "data-phase", str(step)
                )
                assert await refs_in(page, "#graph-or-phase li.graph-or-item") == refs
                await expect(page.locator("#graph-or-phase-title")).to_contain_text(
                    f"Phase {step} {len(refs)} item{'s' if len(refs) > 1 else ''}"
                )
                for ref in refs:
                    node = await ok(client, "get_node", node=ref)
                    waits = page.locator(f"li[data-ref='{ref}'] .graph-or-waits")
                    if step == 1:
                        await expect(waits).to_have_count(0)
                    else:
                        shown = " ".join((await waits.inner_text()).split())
                        assert shown == "Waits for " + ", ".join(
                            code_of(r) for r in sorted(node["waiting_on"])
                        ), (shown, node["waiting_on"])
                assert (f"phase={step}" in page.url) == (step > 1), page.url
                if step < len(want):
                    await page.locator("#graph-or-next").click()
            # The last phase has nothing after it; the arrow keys step back and forth.
            await expect(page.locator("#graph-or-next")).to_be_disabled()
            await page.locator("#graph-or-prev").focus()
            await page.keyboard.press("ArrowLeft")
            await expect(page.locator("#graph-or-step")).to_have_text("Phase 2 of 3")
            await page.keyboard.press("ArrowRight")
            await expect(page.locator("#graph-or-step")).to_have_text("Phase 3 of 3")
            assert "phase=3" in page.url
            # The address keeps the phase, and one that is not there is put right.
            await order_open(w, f"#/graph?tab=order&focus={ids['E']}&phase=2")
            await expect(page.locator("#graph-or-step")).to_have_text("Phase 2 of 3")
            await order_open(w, f"#/graph?tab=order&focus={ids['E']}&phase=9")
            await expect(page.locator("#graph-or-step")).to_have_text("Phase 3 of 3")
            await expect(page.locator("#graph-or-phase")).to_have_attribute("data-phase", "3")
            assert "phase=3" in page.url, page.url
            assert w.problems == [], w.problems


async def test_order_folds_the_done_work_counts_the_dropped_and_a_waits_for_goes_to_its_phase(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        ids = await phase_world(client, "folded")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await order_open(w, f"#/graph?tab=order&focus={ids['E']}&phase=2")
            # Done work is in a folded group, dropped work is not on the page.
            toggle = page.locator("#graph-or-done-toggle")
            await expect(toggle).to_have_text("Done (1)")
            await expect(toggle).to_have_attribute("aria-expanded", "false")
            await expect(page.locator(f"li[data-ref='{ids['f']}']")).to_have_count(0)
            await toggle.click()
            await expect(toggle).to_have_attribute("aria-expanded", "true")
            await expect(page.locator(f"#graph-or-done li[data-ref='{ids['f']}']")).to_be_visible()
            for step in (1, 2, 3):
                await order_open(w, f"#/graph?tab=order&focus={ids['E']}&phase={step}")
                await expect(page.locator(f"li[data-ref='{ids['g']}']")).to_have_count(0)
            # A "waits for" button goes to the phase of what it names and lights it.
            await order_open(w, f"#/graph?tab=order&focus={ids['E']}&phase=3")
            await page.locator(f"li[data-ref='{ids['c']}'] button.graph-or-wait").click()
            await expect(page.locator("#graph-or-step")).to_have_text("Phase 2 of 3")
            picked = page.locator(f"li.graph-or-item.is-sel[data-ref='{ids['b']}']")
            await expect(picked).to_be_visible()
            await expect(picked.locator("a.graph-or-item-link")).to_be_focused()
            assert (
                f"pick={ids['b']}".replace(":", "%3A") in page.url or f"pick={ids['b']}" in page.url
            )
            # Stepping on lets go of the light.
            await page.locator("#graph-or-prev").click()
            await expect(page.locator("#graph-or-step")).to_have_text("Phase 1 of 3")
            await expect(page.locator("li.graph-or-item.is-sel")).to_have_count(0)
            assert w.problems == [], w.problems


async def test_the_chain_view_shows_one_chain_at_a_time_and_the_loose_items_last(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        ids = await phase_world(client, "chains")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await order_open(w, f"#/graph?tab=order&focus={ids['E']}&mode=chains")
            await expect(page.locator("button[data-choice='mode:chains']")).to_have_attribute(
                "aria-pressed", "true"
            )
            await expect(page.locator("#graph-or-step")).to_have_text("Chain 1 of 2")
            # The biggest chain first, on its own: a, b, c and e.
            assert sorted(await refs_in(page, ".graph-or-node")) == sorted(
                [ids["a"], ids["b"], ids["c"], ids["e"]]
            )
            await expect(page.locator("#graph-or-phase")).to_have_count(0)
            await page.locator("#graph-or-next").click()
            await expect(page.locator("#graph-or-step")).to_have_text("Chain 2 of 2")
            assert sorted(await refs_in(page, ".graph-or-node")) == sorted([ids["h"], ids["i"]])
            assert "chain=2" in page.url
            # Last come the items that no link names, the done one too, and not the dropped one.
            await page.locator("#graph-or-next").click()
            await expect(page.locator("#graph-or-step")).to_have_text("Loose items (2)")
            await expect(page.locator("#graph-or-loose")).to_be_visible()
            assert await refs_in(page, "#graph-or-loose li.graph-or-item") == [ids["d"], ids["f"]]
            await expect(page.locator(".graph-or-node")).to_have_count(0)
            await expect(page.locator("#graph-or-next")).to_be_disabled()
            # Pointing at a box, or reaching it with the keyboard, lights the chain through it (what
            # it needs first and what waits for it) and dims the rest, with nothing drawn again;
            # leaving it puts the picture back. Nothing is beside the chains.
            await page.locator("#graph-or-prev").click()
            await page.locator("#graph-or-prev").click()
            await expect(page.locator("#graph-or-step")).to_have_text("Chain 1 of 2")
            await expect(page.locator(".graph-x-side")).to_have_count(0)
            a, b, c, e = ids["a"], ids["b"], ids["c"], ids["e"]

            async def boxes(mark: str) -> set[str]:
                return set(await refs_in(page, f".graph-or-canvas .graph-or-node.{mark}"))

            async def lines(mark: str) -> set[tuple[str, str]]:
                found: list[list[str]] = await page.locator(
                    f".graph-or-canvas path.graph-or-edge.{mark}"
                ).evaluate_all("els => els.map(e => [e.dataset.pre, e.dataset.dep])")
                return {(pre, dep) for pre, dep in found}

            box = page.locator(f".graph-or-node[data-ref='{b}']")
            await expect(box).to_have_attribute(
                "aria-label", re.compile(r"Press Enter to open it\.$")
            )
            assert await boxes("is-on-path") == set() and await boxes("is-dim") == set()
            await box.hover()
            await expect(page.locator(".graph-or-canvas .graph-or-node.is-on-path")).to_have_count(
                3
            )
            assert await boxes("is-on-path") == {a, b, c}
            assert await boxes("is-dim") == {e}
            assert await lines("is-on-path") == {(a, b), (b, c)}
            assert await lines("is-dim") == {(a, e)}
            await page.mouse.move(0, 0)
            await expect(page.locator(".graph-or-canvas .graph-or-node.is-on-path")).to_have_count(
                0
            )
            assert await boxes("is-dim") == set() and await lines("is-on-path") == set()
            await box.focus()
            await expect(page.locator(".graph-or-canvas .graph-or-node.is-on-path")).to_have_count(
                3
            )
            assert await boxes("is-on-path") == {a, b, c}
            # The arrow keys go from box to box along the chain, and the light follows.
            await page.keyboard.press("ArrowLeft")
            assert await page.evaluate("document.activeElement.dataset.ref") == a
            assert await boxes("is-on-path") == {a, b, c, e} and await boxes("is-dim") == set()
            await page.locator("#graph-or-next").focus()
            assert await boxes("is-on-path") == set()
            # A click opens the page of the item, in the Graph, and the way back is the chain; the
            # address picks nothing.
            chains = await page.evaluate("location.hash")
            assert "pick=" not in chains
            await box.click()
            await expect(page.locator(f"#item-page[data-ref='{b}']")).to_be_visible(timeout=SLOW)
            assert unquote(page.url).endswith(f"#/item?item={b}&from=graph"), page.url
            await page.go_back()
            await expect(page.locator("#graph-or-step")).to_have_text("Chain 1 of 2", timeout=SLOW)
            assert await page.evaluate("location.hash") == chains
            # A `pick` in the address (the phases' "Waits for" buttons make one) lights its chain
            # and marks the item.
            await order_open(w, f"#/graph?tab=order&focus={ids['E']}&mode=chains&pick={b}")
            await expect(page.locator(f".graph-or-node.is-sel[data-ref='{b}']")).to_have_attribute(
                "aria-current", "true"
            )
            assert await boxes("is-on-path") == {a, b, c} and await boxes("is-dim") == set()
            # Back to phases: the switch is in the address.
            await page.locator("button[data-choice='mode:phases']").click()
            await expect(page.locator("#graph-or-phase")).to_be_visible(timeout=SLOW)
            assert "mode=" not in page.url, page.url
            assert w.problems == [], w.problems


async def test_with_sizes_a_phase_is_cut_into_batches_that_fit_the_budget(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Sized")
        sizes: dict[str, list[tuple[str, str | None]]] = {
            "S1": [("t1", "s"), ("t2", "m"), ("t3", "xs")],
            "S2": [("t4", "l"), ("t5", "m"), ("t6", "xl"), ("t7", None)],
        }
        ids: dict[str, str] = {}
        for story_name, tasks in sizes.items():
            story = await made(client, "story", story_name, epic)
            for name, size in tasks:
                ids[name] = await made(
                    client, "task", f"Task {name}", story, **({"size": size} if size else {})
                )
        points = {"xs": 1, "s": 2, "m": 3, "l": 5, "xl": 8}
        sized = {n: points[s] for tasks in sizes.values() for n, s in tasks if s}
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await order_open(w, f"#/graph?tab=order&focus={epic}")
            title = page.locator("#graph-or-phase-title")
            await expect(title).to_contain_text("7 items, 22 points, 1 not sized")
            await expect(page.locator("#graph-or-budget")).to_have_value("8")
            await expect(
                page.locator("li[data-ref]", has_text="Task t4").locator(".chip.size")
            ).to_have_attribute("title", "Large, 5 points")

            async def batches() -> list[tuple[int, list[str], bool]]:
                found: list[tuple[int, list[str], bool]] = []
                for batch in await page.locator(".graph-or-batch[data-batch]").all():
                    refs = await batch.locator("li.graph-or-item").evaluate_all(
                        "els => els.map(e => e.dataset.ref)"
                    )
                    big = await batch.locator(".graph-or-big").count() == 1
                    found.append((int(await batch.get_attribute("data-points") or 0), refs, big))
                return found

            async def check(budget: int) -> None:
                found = await batches()
                every = [r for _, refs, _ in found for r in refs]
                assert sorted(every) == sorted(ids[n] for n in sized), (every, sized)
                for number, (total, refs, big) in enumerate(found, start=1):
                    names = [n for n, r in ids.items() if r in refs]
                    assert total == sum(sized[n] for n in names), (number, total, names)
                    assert total <= budget or (len(refs) == 1 and big), (budget, found)
                    heading = await page.locator(
                        f".graph-or-batch[data-batch='{number}'] h4"
                    ).first.inner_text()
                    assert heading.startswith(f"Batch {number}: {total} point"), heading
                # The work with no size is listed apart, in none of the batches.
                assert await refs_in(page, "#graph-or-unsized li.graph-or-item") == [ids["t7"]]
                assert ids["t7"] not in every

            await check(8)
            assert [total for total, _, _ in await batches()] == [6, 8, 8]
            await page.locator("#graph-or-budget").select_option("5")
            await expect(page.locator(".graph-or-batch .graph-or-big")).to_have_count(
                1, timeout=SLOW
            )
            assert "budget=5" in page.url
            await check(5)
            assert any(big and total == 8 for total, _, big in await batches())
            # The budget is kept in the address; the default is not written.
            await order_open(w, f"#/graph?tab=order&focus={epic}&budget=13")
            await expect(page.locator("#graph-or-budget")).to_have_value("13")
            await check(13)
            await page.locator("#graph-or-budget").select_option("8")
            await expect(page.locator(".graph-or-batch[data-batch]")).to_have_count(3)
            assert "budget=" not in page.url, page.url
            assert w.problems == [], w.problems


# -- the Structure map's key and the keys of the tabs ----------------------------------------------

STATUS_WORDS = {
    "new": "New",
    "ready": "Ready",
    "in_progress": "In progress",
    "blocked": "Blocked",
    "done": "Done",
    "dropped": "Dropped",
}


async def test_the_structure_key_draws_the_maps_own_marks_and_nothing_sits_beside_the_map(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        ids = await phase_world(client, "key")
        tasks = await all_nodes(client, kind="task")
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task["status"]] = counts.get(task["status"], 0) + 1
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await structure_open(w, "#/graph")
            # With nothing chosen the map has the whole width: no column beside it.
            await expect(page.locator(".graph-x-side")).to_have_count(0)
            await expect(page.locator(".graph-key")).to_have_count(0)
            await page.locator("#graph-key-button").focus()
            card = page.locator(".hovercard:visible")
            await expect(card).to_be_visible()
            chips = card.locator(".graph-key-chip", has=page.locator(".graph-key-count"))
            shown: dict[str, int] = {}
            for chip in await chips.all():
                text = " ".join((await chip.inner_text()).split())
                label, _, how_many = text.rpartition(" ")
                shown[label] = int(how_many)
            # The key says how many of the dots there are of each colour, and no more.
            assert shown == {STATUS_WORDS[s]: n for s, n in counts.items()}, (shown, counts)
            for status, number in counts.items():
                swatch = card.locator(f".graph-key-swatch g.graph-st-node.s-{status}")
                # A ring for an epic or a story, and a dot for a task, drawn with the map's classes.
                assert await swatch.count() == 2, status
                assert (
                    await page.locator(f"{MAP} g.graph-st-node.is-task.s-{status}").count()
                    == number
                )
            for words in ("Epic, with a green arc", "Story", "Task", "Amber ring", "Red ring"):
                await expect(card).to_contain_text(words)
            # It says nothing about choosing a dot or about a card: a dot opens its page.
            await expect(card).not_to_contain_text(re.compile(r"choos|card|Press Enter", re.I))
            await page.keyboard.press("Escape")
            await expect(page.locator(".hovercard:visible")).to_have_count(0)
            # A click on a dot opens the page of its item and brings no card beside the map.
            await page.locator(f"{MAP} g.graph-st-node[data-ref='{ids['a']}']").click()
            await expect(page.locator(f"#item-page[data-ref='{ids['a']}']")).to_be_visible(
                timeout=SLOW
            )
            await page.go_back()
            await expect(page.locator(MAP)).to_be_visible(timeout=SLOW)
            await expect(page.locator(".graph-x-side")).to_have_count(0)
            assert w.problems == [], w.problems


async def test_every_tab_of_the_graph_keeps_its_key_behind_a_key_button_at_every_width(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        ids = await phase_world(client, "keys")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            for width in (1280, 390):
                await page.set_viewport_size({"width": width, "height": 900})
                for address, words in (
                    ("#/graph", ("Shapes",)),
                    (f"#/graph?scope={ids['E']}&focus={ids['E']}", ("The leaves",)),
                    (f"#/graph?tab=around&focus={ids['s1']}", ("Lines",)),
                    (f"#/graph?tab=order&focus={ids['E']}", ("Phases",)),
                    (
                        f"#/graph?tab=order&focus={ids['E']}&mode=chains",
                        (
                            "Boxes and lines",
                            "Highlighted: what it needs first and what waits for it",
                        ),
                    ),
                ):
                    await go(w.page, board, address)
                    button = page.locator("#graph-key-button")
                    await expect(button).to_be_visible(timeout=SLOW)
                    await expect(page.locator(".graph-key, .graph-legend")).to_have_count(0)
                    await button.click()
                    card = page.locator(".hovercard:visible")
                    for one in words:
                        await expect(card).to_contain_text(one)
                    await page.keyboard.press("Escape")
                    await expect(page.locator(".hovercard:visible")).to_have_count(0)
                    assert (
                        await page.evaluate("[document.documentElement.scrollWidth, innerWidth]")
                    )[0] <= width, (address, width)
            assert w.problems == [], w.problems


# -- the Graph's choices live in its address -----------------------------------------------------


async def test_every_tab_of_the_graph_keeps_its_choices_in_the_address_and_restores_them(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        world = await context_world(client, "choices", project)
        s1, t1 = world["s1"], world["t1"]
        async with browser_page(board, token=project.key) as w:
            page = w.page
            pressed = "aria-pressed"
            # Structure: the level, the colour, the labels and the links, and what is chosen.
            await structure_open(
                w, f"#/graph?level=stories&colour=epic&labels=always&links=1&focus={s1}"
            )
            for choice in ("level:stories", "colour:epic", "labels:always"):
                await expect(page.locator(f"button[data-choice='{choice}']")).to_have_attribute(
                    pressed, "true"
                )
            await expect(page.locator("button[data-choice='links']")).to_have_attribute(
                pressed, "true"
            )
            await expect(page.locator(f"{MAP} g.graph-st-node.is-sel")).to_have_attribute(
                "data-ref", s1
            )
            assert await page.locator(f"{MAP} g.graph-st-node.is-task").count() == 0
            # One epic on its own is the `scope`, shown in the Look at box.
            await go(page, board, f"#/graph?scope={world['E']}")
            await expect(page.locator("#graph-view svg.graph-rd-svg")).to_be_visible(timeout=SLOW)
            await expect(page.locator("#graph-st-scope")).to_have_value(world["E"])
            # Around: the reach and the project-wide notes opened.
            await around_open(w, s1, "&reach=2&wide=1")
            await expect(page.locator("#graph-reach-2")).to_have_attribute(pressed, "true")
            await expect(page.locator("#graph-wide-toggle")).to_have_attribute(
                "aria-expanded", "true"
            )
            # The choices of the side panels that are gone (the Agent view and the quick look at a
            # note) are put right without a word.
            await around_open(w, s1, f"&agent=1&peek=note:{world['n1']}&reach=2")
            await expect(page.locator("#graph-reach-2")).to_have_attribute(pressed, "true")
            await expect(page).not_to_have_url(re.compile(r"agent=|peek="))
            assert "reach=2" in page.url, page.url
            # Order: the tab itself is the address, and the focus follows to the next.
            await go(page, board, f"#/graph?tab=order&focus={t1}")
            await expect(page.locator("#graph-tab-order")).to_have_attribute(
                "aria-selected", "true"
            )
            await expect(page.locator(".graph-or")).to_be_visible(timeout=SLOW)
            # Its phase is in the address too (the budget has no sizes to cut), and is forgotten
            # with the tab.
            await go(page, board, f"#/graph?tab=order&focus={t1}&phase=2")
            await expect(page.locator("#graph-or-step")).to_have_text("Phase 2 of 2", timeout=SLOW)
            await page.locator("#graph-tab-around").click()
            assert "tab=around" in page.url and f"focus={t1}" in page.url.replace("%3A", ":")
            assert "phase=" not in page.url, page.url
            await expect(page.locator(".graph-around")).to_be_visible(timeout=SLOW)
            # By keyboard: the arrow keys move from tab to tab, round the three.
            await page.locator("#graph-tab-around").focus()
            await page.keyboard.press("ArrowLeft")
            await expect(page.locator("#graph-tab-structure")).to_be_focused()
            await page.keyboard.press("ArrowLeft")
            await expect(page.locator("#graph-tab-order")).to_be_focused()
            assert w.problems == [], w.problems
