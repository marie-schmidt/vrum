"""M19's page of an epic, story or task (#/item?item=story:12), driven in a real headless browser
against the real containers and checked against a real MCP client: what a person reads in the page
and in its table is compared with what the server says (`list_nodes`, `get_node`), and what a
person does there is read back through the tools.

A story's page has all of its details (the same sections as the detail panel, edited the same way,
with the same version checks) and, under them, a table of its tasks: an epic's page lists its
stories with their progress, and a task's says which story it is part of. The table sorts by every
column, says when it is empty and adds one, follows an agent's work while it is open, and works by
keyboard and at 390 pixels in both themes. The panel's "Open full page" button and the titles in the
Graph's Work list reach the page."""

import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_helpers import (
    SLOW,
    Watched,
    board_url,
    browser_page,
    item_address,
    item_row,
    item_rows,
    made,
    open_item,
    plain,
)
from tests.board_layout import frames, tab_to
from tests.board_theme import policy_violations, themed_page
from tests.helpers import (
    all_nodes,
    code_of,
    connect,
    day,
    make_decision,
    make_fact,
    number_of,
    ok,
    ref_order,
    refused,
    unique_word,
)
from tests.world import Project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

STATUS_ORDER = ["new", "ready", "in_progress", "blocked", "done", "dropped"]
PRIORITY_ORDER = ["urgent", "high", "normal", "low"]  # urgent first
NOTHING = "—"


@dataclass
class World:
    project: Project
    board: str
    word: str
    refs: dict[str, str]

    def __getitem__(self, name: str) -> str:
        return self.refs[name]


async def status_of(client: Client, ref: str, status: str) -> None:
    node = await ok(client, "get_node", node=ref)
    await ok(
        client,
        "update_node",
        actor="agent-1",
        node=ref,
        expected_version=node["version"],
        status=status,
    )


async def build(client: Client, word: str, long_title: bool = False) -> dict[str, str]:
    """An epic with three stories (one with seven tasks of every status, priority and sort, one
    with none, one with two done), and an epic with nothing in it, a requirement, a decision, a
    fact and a link on the first story."""
    refs: dict[str, str] = {}

    async def make(name: str, kind: str, title: str, parent: str | None, **more: Any) -> None:
        refs[name] = await made(client, kind, f"{title} {word}", parent, **more)

    await make("epic", "epic", "Ordering", None, body="The **whole** ordering flow.")
    story_title = "Place an order" + (" " + "Supercalifragilistic" * 4 if long_title else "")
    await make(
        "story",
        "story",
        story_title,
        refs["epic"],
        body="A *story* about [[epic:1]]:\n\n- first point\n- second point\n",
        priority="high",
    )
    tasks: list[tuple[str, str, dict[str, Any]]] = [
        ("form", "Task 10 build the form", {}),
        ("api", "Task 2 write the API", {"priority": "urgent", "target_on": day(-3)}),
        ("copy", "task 3 copy the words", {"priority": "low"}),
        ("deploy", "Task 4 deploy", {"priority": "high"}),
        ("docs", "Task 5 write the docs", {"target_on": day(5)}),
        ("polish", "Task 6 polish", {"priority": "low", "target_on": day(10)}),
        ("ship", "Task 7 ship it" + (" " + "Unbreakable" * 8 if long_title else ""), {}),
    ]
    for name, title, more in tasks:
        await make(name, "task", title, refs["story"], **more)
    await make("pay", "story", "Pay for it", refs["epic"])
    await make("track", "story", "Track it", refs["epic"])
    await make("announce", "task", "Task 8 announce", refs["track"])
    await make("celebrate", "task", "Task 9 celebrate", refs["track"])
    await make("empty_epic", "epic", "Nothing yet", None)

    # Every status, held, waiting and due: new (waiting), ready, in progress, blocked, done.
    await ok(client, "claim_node", actor="cara", node=refs["form"])
    await status_of(client, refs["copy"], "in_progress")
    await status_of(client, refs["deploy"], "blocked")
    for done in ("docs", "announce", "celebrate"):
        await status_of(client, refs[done], "done")
    for waiting, first in (("api", "form"), ("polish", "api"), ("polish", "deploy")):
        await ok(
            client,
            "add_link",
            actor="agent-1",
            source=refs[waiting],
            target=refs[first],
            link_type="needs_first",
        )
    await ok(
        client,
        "add_link",
        actor="agent-1",
        source=refs["story"],
        target=refs["track"],
        link_type="relates_to",
    )
    await ok(client, "create_rule", actor="agent-1", title=f"Fast {word}", story=refs["story"])
    await make_decision(client, f"Use REST {word}", nodes=[refs["story"]])
    await make_fact(client, f"Orders are small {word}", nodes=[refs["story"]])
    return refs


@asynccontextmanager
async def ordering(
    server_url: str, long_title: bool = False
) -> AsyncIterator[tuple[World, Client]]:
    """A project of its own with the work above, and a client of it."""
    project = make_project(register(), f"Item page {unique_word()}")
    word = unique_word()
    async with connect(project.mcp_url, project.key) as client:
        refs = await build(client, word, long_title)
        yield World(project, board_url(server_url, project), word, refs), client


async def see_page(w: Watched, ref: str) -> None:
    """Open the page of an item and wait until its details and what is under it are drawn."""
    await open_item(w, ref)
    await expect(w.page.locator("#item-details .card-detail")).to_be_visible(timeout=SLOW)


async def children_of(client: Client, ref: str) -> list[dict[str, Any]]:
    return await all_nodes(client, parent=ref)


def cell_text(text: str) -> str:
    return " ".join(text.split())


async def cells_of(page: Page, ref: str) -> dict[str, str]:
    """What each column of a row says, by the name of its column."""
    row = item_row(page, ref)
    out: dict[str, str] = {}
    for name in ("item", "title", "status", "progress", "priority", "held", "due", "waiting"):
        place = row.locator(f".cell-{name}")
        if await place.count():
            out[name] = cell_text(await place.inner_text())
    return out


def status_words(status: str) -> str:
    return status.replace("_", " ").capitalize()


# -- 1. a story's page: its details, and exactly its tasks ---------------------------------------


async def test_a_storys_page_has_the_panels_details_and_a_table_of_exactly_its_tasks(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        story = world["story"]
        tasks = await children_of(client, story)
        assert len(tasks) == 7
        server = await ok(client, "get_node", node=story)
        async with browser_page(world.board, token=world.project.key) as w:
            await see_page(w, story)
            page = w.page
            code = code_of(story)
            # The title is the heading, with the code, and the line under it says what it is.
            assert cell_text(await page.locator("h1#item-title").inner_text()) == (
                f"{code} Place an order {world.word}"
            )
            assert cell_text(await page.locator("#item-what").inner_text()) == (
                f"Story in {code_of(world['epic'])} Ordering {world.word}"
            )
            assert await page.locator("#item-what a").get_attribute("href") == item_address(
                world["epic"]
            )
            assert (
                await page.title()
                == f"{code} Place an order {world.word} · {world.project.name} · Vrum"
            )
            assert await page.locator("#item-page").get_attribute("data-kind") == "story"
            # The way back: Projects / the project / the epic / the story.
            crumbs = page.locator("nav.crumbs li")
            assert [cell_text(t) for t in await crumbs.all_inner_texts()] == [
                "Projects",
                world.project.name,
                f"{code_of(world['epic'])} Ordering {world.word}",
                f"{code} Place an order {world.word}",
            ]
            assert await page.locator("nav.crumbs li a").evaluate_all(
                "els => els.map(e => e.getAttribute('href'))"
            ) == ["/board", "#/board", item_address(world["epic"])]

            # Every detail the server has is on the page.
            details = page.locator("#item-details")
            text = cell_text(await details.inner_text())
            assert "High" in text and status_words(server["status"]) in text
            assert f"{server['progress_done']} of {server['progress_total']} tasks done" in text
            assert await details.locator(".body-text em").inner_text() == "story"
            assert await details.locator(".body-text li").all_inner_texts() == [
                "first point",
                "second point",
            ]
            assert f"Fast {world.word}" in text
            assert f"Use REST {world.word}" in text and f"Orders are small {world.word}" in text
            assert f"{code_of(world['track'])} Track it {world.word}" in text  # the link
            for field in ("title", "body", "status", "priority", "start", "target"):
                assert await details.locator(f"#edit-{field}").count() == 1
            assert await details.locator("#edit-title").input_value() == server["title"]
            assert await details.locator("#edit-priority").input_value() == "high"
            # The panel has the same sections for the same story.
            page_sections = await page.locator("#item-details section.part").evaluate_all(
                "els => els.map(e => e.querySelector('h2, h3, h4')?.textContent.trim())"
            )
            await w.go(f"#/board?sel={story}")
            await expect(page.locator("#panel .card-detail")).to_be_visible(timeout=SLOW)
            panel_sections = await page.locator("#panel section.part").evaluate_all(
                "els => els.map(e => e.querySelector('h2, h3, h4')?.textContent.trim())"
            )
            assert page_sections and sorted(page_sections) == sorted(panel_sections)

            # The table lists exactly the story's tasks, as the server lists them.
            await see_page(w, story)
            assert await item_rows(page) == [t["ref"] for t in tasks]
            assert cell_text(await page.locator("#item-count").inner_text()) == "7 tasks"
            assert cell_text(await page.locator("#item-table caption").inner_text()) == (
                f"Tasks in {code}"
            )
            assert await page.locator("#item-children-title").inner_text() == f"Tasks in {code}"
            assert await page.locator("#item-table thead th button.sort").evaluate_all(
                "els => els.map(e => e.dataset.sort)"
            ) == ["item", "title", "status", "priority", "held", "due", "waiting"]
            assert await page.locator("th.col-item").get_attribute("aria-sort") == "ascending"
            # None of another story's tasks is there.
            for other in ("announce", "celebrate"):
                assert await item_row(page, world[other]).count() == 0
            for task in tasks:
                ref = task["ref"]
                cells = await cells_of(page, ref)
                assert cells["item"] == code_of(ref)
                assert await item_row(page, ref).locator(".cell-item a").get_attribute("href") == (
                    item_address(ref)
                )
                assert cells["title"] == task["title"]
                assert cells["status"].lower() == status_words(task["status"]).lower()
                assert cells["priority"].lower() == task["priority"]
                assert cells["held"] == (task["claimed_by"] or NOTHING)
                if task["target_on"]:
                    assert cells["due"].startswith(task["target_on"])
                    assert ("Overdue" in cells["due"]) == task["overdue"]
                else:
                    assert cells["due"] == NOTHING
                waiting = item_row(page, ref).locator(".cell-waiting a")
                assert [cell_text(t) for t in await waiting.all_inner_texts()] == [
                    code_of(r) for r in task["waiting_on"]
                ]
                assert await waiting.evaluate_all(
                    "els => els.map(e => e.getAttribute('href'))"
                ) == [item_address(r) for r in task["waiting_on"]]
                if not task["waiting_on"]:
                    assert cells["waiting"] == NOTHING
            assert {c["priority"] for c in tasks} == {"urgent", "high", "normal", "low"}
            assert {c["status"] for c in tasks} >= {
                "new",
                "ready",
                "in_progress",
                "blocked",
                "done",
            }
            assert w.problems == [], w.problems
            assert await policy_violations(page) == []


# -- 2. every column sorts, both ways ------------------------------------------------------------


def natural(text: str) -> list[Any]:
    """Words and numbers in order, so that task 2 comes before task 10, whatever the case."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text.lower())]


def sorted_as_shown(
    rows: list[dict[str, Any]],
    key: Callable[[dict[str, Any]], Any],
    empty: Callable[[dict[str, Any]], bool],
    descending: bool,
) -> list[str]:
    """What the table must show: by the column, the other way for descending, rows with nothing in
    the column always last, and rows that tie in the order of their numbers."""
    by_number = sorted(rows, key=lambda r: ref_order(r["ref"]))
    present = sorted((r for r in by_number if not empty(r)), key=key, reverse=descending)
    return [r["ref"] for r in present] + [r["ref"] for r in by_number if empty(r)]


TASK_COLUMNS: dict[
    str, tuple[Callable[[dict[str, Any]], Any], Callable[[dict[str, Any]], bool]]
] = {
    "item": (lambda r: ref_order(r["ref"]), lambda r: False),
    "title": (lambda r: natural(r["title"]), lambda r: False),
    "status": (lambda r: STATUS_ORDER.index(r["status"]), lambda r: False),
    "priority": (lambda r: PRIORITY_ORDER.index(r["priority"]), lambda r: False),
    "held": (lambda r: str(r["claimed_by"]).lower(), lambda r: not r["claimed_by"]),
    "due": (lambda r: r["target_on"], lambda r: not r["target_on"]),
    "waiting": (lambda r: ref_order(r["waiting_on"][0]), lambda r: not r["waiting_on"]),
}


async def sort_both_ways(
    page: Page,
    rows: list[dict[str, Any]],
    columns: dict[str, tuple[Callable[[dict[str, Any]], Any], Callable[[dict[str, Any]], bool]]],
) -> None:
    """Click each column's header: the rows come in its order and the header says so; click it
    again and they come in the other order; no other header says anything. (A column that is
    already the sorted one is turned round by the first click, as the page starts sorted by Item.)
    """
    for name, (key, empty) in columns.items():
        button = page.locator(f"#item-table button.sort[data-sort={name}]")
        header = page.locator(f"#item-table th.col-{name}")
        starts_ascending = await header.get_attribute("aria-sort") == "ascending"
        for turn, descending in enumerate((starts_ascending, not starts_ascending)):
            await button.click()
            wanted = "descending" if descending else "ascending"
            await expect(header).to_have_attribute("aria-sort", wanted, timeout=SLOW)
            assert await item_rows(page) == sorted_as_shown(rows, key, empty, descending), (
                name,
                wanted,
                turn,
            )
            others = await page.locator("#item-table thead th[aria-sort]").count()
            assert others == 1, (name, others)


async def test_the_table_sorts_by_every_column_in_both_directions(server_url: str) -> None:
    async with ordering(server_url) as (world, client):
        tasks = await children_of(client, world["story"])
        stories = await children_of(client, world["epic"])
        async with browser_page(world.board, token=world.project.key) as w:
            await see_page(w, world["story"])
            await sort_both_ways(w.page, tasks, TASK_COLUMNS)
            # The order does not change when the same page is shown again after news.
            rows = await item_rows(w.page)
            await w.page.locator("#item-table button.sort[data-sort=title]").click()
            await w.page.locator("#item-table button.sort[data-sort=title]").click()
            await expect(w.page.locator("th.col-title")).to_have_attribute(
                "aria-sort", "descending", timeout=SLOW
            )
            assert await item_rows(w.page) == sorted_as_shown(tasks, *TASK_COLUMNS["title"], True)
            assert rows != await item_rows(w.page)

            # An epic's table has the same columns and a Progress column.
            await see_page(w, world["epic"])
            share = {
                s["ref"]: (s["progress_done"] / s["progress_total"] if s["progress_total"] else 0)
                for s in stories
            }
            columns = {
                **{name: TASK_COLUMNS[name] for name in ("item", "title", "status", "priority")},
                "progress": (lambda r: share[r["ref"]], lambda r: not r["progress_total"]),
            }
            await sort_both_ways(w.page, stories, columns)
            assert await w.page.locator("#item-table button.sort").evaluate_all(
                "els => els.map(e => e.dataset.sort)"
            ) == ["item", "title", "status", "progress", "priority", "held", "due", "waiting"]
            assert w.problems == [], w.problems


# -- 3. nothing under it yet, and a way to add one -----------------------------------------------


async def test_the_empty_state_and_new_task_make_a_task_that_appears_with_a_way_back(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        pay = world["pay"]
        async with browser_page(world.board, token=world.project.key) as w:
            await see_page(w, pay)
            page = w.page
            assert await children_of(client, pay) == []
            assert cell_text(await page.locator("#item-count").inner_text()) == "0 tasks"
            await expect(page.locator("#item-empty")).to_be_visible(timeout=SLOW)
            assert "No tasks yet." in await page.locator("#item-empty").inner_text()
            assert await page.locator("#item-table").count() == 0
            assert await page.locator("#item-region").is_hidden()
            assert await page.locator("#new-child").inner_text() == "New task"

            # New task starts the form on a task under this story, and nothing else.
            await page.locator("#new-child").click()
            dialog = page.locator("dialog.modal")
            assert await dialog.locator("#new-kind").input_value() == "task"
            assert await dialog.locator("#new-parent").input_value() == pay
            await dialog.locator("#new-title").fill(f"First task of it {world.word}")
            await dialog.get_by_role("button", name="Create").click()
            await expect(page.locator("#card-title")).to_contain_text(
                f"First task of it {world.word}", timeout=SLOW
            )
            made_tasks = await children_of(client, pay)
            assert [t["title"] for t in made_tasks] == [f"First task of it {world.word}"]
            task = made_tasks[0]["ref"]
            assert made_tasks[0]["parent"] == pay and made_tasks[0]["kind"] == "task"
            # It appears in the table, which now has no empty state, and the panel shows it.
            await expect(item_row(page, task)).to_be_visible(timeout=SLOW)
            assert await item_rows(page) == [task]
            assert cell_text(await page.locator("#item-count").inner_text()) == "1 task"
            assert await page.locator("#item-empty").is_hidden()
            assert cell_text(await page.locator("#card-title").inner_text()) == (
                f"{code_of(task)} First task of it {world.word}"
            )
            assert f"sel={task}" in page.url and f"item={pay}" in page.url

            # The way back: from its page, the story it is part of, in the page and in the crumbs.
            await item_row(page, task).locator(".cell-item a").click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", task, timeout=SLOW
            )
            await expect(page.locator("#item-part")).to_be_visible(timeout=SLOW)
            part = page.locator("#item-part-title")
            assert (
                cell_text(await part.inner_text())
                == f"Part of {code_of(pay)} Pay for it {world.word}"
            )
            assert await part.locator("a").get_attribute("href") == item_address(pay)
            assert await page.locator("nav.crumbs li a").evaluate_all(
                "els => els.map(e => e.getAttribute('href'))"
            ) == ["/board", "#/board", item_address(world["epic"]), item_address(pay)]
            await page.locator("nav.crumbs li a[href='" + item_address(pay) + "']").click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", pay, timeout=SLOW
            )
            await expect(item_row(page, task)).to_be_visible(timeout=SLOW)
            await page.go_back()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", task, timeout=SLOW
            )

            # An epic with no stories says so in the same way and adds a story under it.
            empty = world["empty_epic"]
            await see_page(w, empty)
            assert "No stories yet." in await page.locator("#item-empty").inner_text()
            assert await page.locator("#new-child").inner_text() == "New story"
            await page.locator("#new-child").click()
            dialog = page.locator("dialog.modal")
            assert await dialog.locator("#new-kind").input_value() == "story"
            assert await dialog.locator("#new-parent").input_value() == empty
            await dialog.locator("#new-title").fill(f"A story of it {world.word}")
            await dialog.get_by_role("button", name="Create").click()
            await expect(page.locator("#item-table tbody tr")).to_have_count(1, timeout=SLOW)
            stories = await children_of(client, empty)
            assert [s["title"] for s in stories] == [f"A story of it {world.word}"]
            assert await item_rows(page) == [stories[0]["ref"]]
            assert w.problems == [], w.problems


# -- 4. an epic's page: its stories, with their progress -----------------------------------------


async def test_an_epics_page_lists_its_stories_with_the_progress_the_server_gives(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        epic = world["epic"]
        stories = await children_of(client, epic)
        assert [s["ref"] for s in stories] == [world["story"], world["pay"], world["track"]]
        server = await ok(client, "get_node", node=epic)
        # Done and not dropped: 1 of 7 in the first story, 2 of 2 in the third, none in the second.
        assert [(s["progress_done"], s["progress_total"]) for s in stories] == [
            (1, 7),
            (0, 0),
            (2, 2),
        ]
        async with browser_page(world.board, token=world.project.key) as w:
            await see_page(w, epic)
            page = w.page
            assert cell_text(await page.locator("#item-what").inner_text()) == "Epic with 3 stories"
            assert await page.locator("#item-page").get_attribute("data-kind") == "epic"
            assert await item_rows(page) == [s["ref"] for s in stories]
            assert cell_text(await page.locator("#item-count").inner_text()) == "3 stories"
            assert await page.locator("#item-children-title").inner_text() == (
                f"Stories in {code_of(epic)}"
            )
            assert await page.locator("#item-details").inner_text()
            assert f"{server['progress_done']} of {server['progress_total']} tasks done" in (
                cell_text(await page.locator("#item-details").inner_text())
            )
            for story in stories:
                cells = await cells_of(page, story["ref"])
                done, total = story["progress_done"], story["progress_total"]
                assert cells["progress"] == (f"{done} of {total} done" if total else "No tasks")
                assert cells["title"] == story["title"]
                meter = item_row(page, story["ref"]).locator(".cell-progress .meter span")
                if total:
                    width = await meter.evaluate("e => e.style.width")
                    assert width == f"{round(done / total * 100)}%", (story["ref"], width)
                else:
                    assert await meter.count() == 0
            # A story's own page names its epic and what the quick look and the link do.
            await item_row(page, world["track"]).locator(".quick-look").click()
            await expect(page.locator("#card-title")).to_contain_text(
                f"{code_of(world['track'])} Track it", timeout=SLOW
            )
            assert f"item={epic}" in page.url and f"sel={world['track']}" in page.url
            await item_row(page, world["track"]).locator(".cell-item a").click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", world["track"], timeout=SLOW
            )
            assert "sel=" not in page.url  # going to a page leaves the quick look behind
            assert await item_rows(page) == [world["announce"], world["celebrate"]]
            assert w.problems == [], w.problems


# -- 5. a task's page, and the three pages have one structure ------------------------------------

SKELETON = """() => {
    const name = (e) => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '');
    const page = document.querySelector('#item-page');
    const children = document.querySelector('#item-children');
    return {
        page: [...page.children].map(name),
        head: [...page.querySelector('header').children].map(name),
        title: [...page.querySelectorAll('h1')].length,
        card: [...page.querySelectorAll('#item-details > article')].map((e) => e.className),
        summary: [...page.querySelectorAll('#item-details .card-summary > *')]
            .map((e) => e.className),
        columns: [...page.querySelectorAll('#item-details .card-columns > *')]
            .map((e) => e.className),
        children: children ? [...children.children].map(name) : null,
        mains: document.querySelectorAll('main').length,
    };
}"""


async def test_a_tasks_page_says_which_story_it_is_part_of_and_the_three_pages_share_a_structure(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        task = world["api"]
        server = await ok(client, "get_node", node=task)
        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page
            shapes: dict[str, Any] = {}
            for name in ("epic", "story", "api"):
                await see_page(w, world[name])
                shapes[name] = await page.evaluate(SKELETON)
            await see_page(w, task)
            assert cell_text(await page.locator("#item-title").inner_text()) == (
                f"{code_of(task)} {server['title']}"
            )
            assert cell_text(await page.locator("#item-what").inner_text()) == (
                f"Task in {code_of(world['story'])} Place an order {world.word}"
            )
            part = page.locator("#item-part")
            assert cell_text(await part.locator("h2").inner_text()) == (
                f"Part of {code_of(world['story'])} Place an order {world.word}"
            )
            assert await part.locator("h2 a").get_attribute("href") == item_address(world["story"])
            note = cell_text(await part.locator(".item-part-note").inner_text())
            epic_code = f"In {code_of(world['epic'])} Ordering {world.word}."
            assert note == f"{epic_code} 1 of 7 tasks in the story are done."
            # It has no table, no count and no way to add something under it.
            for gone in ("#item-table", "#item-count", "#new-child", "#item-region", "#item-empty"):
                assert await page.locator(gone).count() == 0, gone
            # Its details: waiting for T-1, urgent, overdue, and the edit form.
            details = cell_text(await page.locator("#item-details").inner_text())
            assert f"Waiting for {code_of(world['form'])}" in details and "Urgent" in details
            assert "Overdue" in details and "Might be the same as" not in details
            assert await page.locator("#item-details #edit-title").input_value() == server["title"]
            # Following the link goes to the story's page, with the task in its table.
            await part.locator("h2 a").click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", world["story"], timeout=SLOW
            )
            await expect(item_row(page, task)).to_be_visible(timeout=SLOW)

            # One structure: an epic's and a story's pages differ only by what is in the table;
            # a task's page has the same head and details, and says "Part of" where they have a
            # table.
            epic_shape, story_shape, task_shape = shapes["epic"], shapes["story"], shapes["api"]
            assert epic_shape == story_shape
            assert (
                epic_shape["page"][0] == "header" and "section#item-details" in epic_shape["page"]
            )
            assert epic_shape["page"][-1] == "section#item-children"
            assert epic_shape["children"] == ["header", "div#item-region", "div#item-empty"]
            assert task_shape["page"][:-1] == epic_shape["page"][:-1]
            assert task_shape["page"][-1] == "section#item-part"
            for shape in shapes.values():
                assert shape["head"] == ["div"] and shape["title"] == 1 and shape["mains"] == 1
                assert shape["card"] == ["card-detail detail card-page"]
                assert shape["columns"] == ["card-main", "card-side"]
            assert task_shape["summary"] == story_shape["summary"]
            assert w.problems == [], w.problems


# -- 6. how a person reaches the page ------------------------------------------------------------


async def test_the_panels_button_the_work_lists_titles_and_a_quick_look_reach_the_page(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, _):
        epic, story, task = world["epic"], world["story"], world["api"]
        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page
            # The panel's button, from the Board.
            await w.open(f"#/board?epic={number_of(epic)}&sel={story}")
            await expect(page.locator("#panel #card-title")).to_contain_text(
                f"{code_of(story)} Place an order", timeout=SLOW
            )
            button = page.locator("#panel a.open-page")
            assert await button.get_attribute("href") == item_address(story)
            assert await button.get_attribute("aria-label") == "Open full page"
            await button.click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", story, timeout=SLOW
            )
            assert "sel=" not in page.url and await page.locator("#panel").is_hidden()
            # A task's panel has the button too, and it leads to a page with no table.
            await w.go(f"#/board?epic={number_of(epic)}&sel={task}")
            await page.locator("#panel a.open-page").click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", task, timeout=SLOW
            )
            assert await page.locator("#item-table").count() == 0

            # The Graph's Work list: an epic's and a story's title is a link to its page ...
            await w.open(f"#/graph?view=work&as=list&epic={number_of(epic)}")
            tree = page.locator("#work-tree")
            await expect(tree.locator(f"li[data-key='{story}']")).to_be_visible(timeout=SLOW)
            title = tree.locator(f"li[data-key='{epic}'] > .gv-row a.gv-title")
            assert await title.get_attribute("href") == item_address(epic)
            await title.click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", epic, timeout=SLOW
            )
            await page.go_back()
            await expect(tree.locator(f"li[data-key='{story}']")).to_be_visible(timeout=SLOW)
            title = tree.locator(f"li[data-key='{story}'] > .gv-row a.gv-title")
            assert await title.get_attribute("href") == item_address(story)
            await title.click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", story, timeout=SLOW
            )
            await page.go_back()
            # ... with a Quick look beside it that opens the panel and stays on the Graph ...
            quick = tree.locator(f"li[data-key='{story}'] > .gv-row button.gv-quick")
            assert await quick.get_attribute("aria-label") == f"Quick look at {code_of(story)}"
            await quick.click()
            await expect(page.locator("#panel #card-title")).to_contain_text(
                f"{code_of(story)} Place an order", timeout=SLOW
            )
            assert f"sel={story}" in page.url and "#/graph" in page.url
            assert await page.locator("#item-page").count() == 0
            # ... and a task's title still opens the panel: it has no page-link or quick look.
            row = tree.locator(f"li[data-key='{task}'] > .gv-row")
            assert await row.locator("a.gv-title, button.gv-quick").count() == 0
            await row.locator(".gv-label").click()
            await expect(page.locator("#panel #card-title")).to_contain_text(
                f"{code_of(task)} Task 2", timeout=SLOW
            )
            assert f"sel={task}" in page.url and "#/graph" in page.url
            assert w.problems == [], w.problems


async def test_the_page_opens_by_its_address_and_an_address_that_names_nothing_says_so_plainly(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        story = world["story"]
        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page
            # By its address, on the first load, and again after a reload.
            await see_page(w, story)
            assert await item_rows(page) == [t["ref"] for t in await children_of(client, story)]
            await page.reload()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", story, timeout=SLOW
            )
            await expect(item_row(page, world["api"])).to_be_visible(timeout=SLOW)
            assert "item=" + story in page.url

            # An item that does not exist: the server's own words, and the way back.
            said = await refused(client, "get_node", node="story:9999")
            await w.go(item_address("story:9999"))
            problem = page.locator("#item-page .notice.refusal")
            await expect(problem).to_be_visible(timeout=SLOW)
            plain(cell_text(await problem.inner_text()), said)
            assert await page.locator("#item-table").count() == 0
            await page.locator("#item-back").click()
            await expect(page.locator("#main-nav [aria-current=page]")).to_be_visible(timeout=SLOW)
            assert page.url.endswith("#/board")

            # What is not a reference at all, and no item at all.
            await w.go("#/item?item=12")
            await expect(page.locator("#item-page")).to_contain_text(
                "That address does not name an epic, story or task", timeout=SLOW
            )
            await w.go("#/item")
            await expect(page.locator("#item-page")).to_contain_text(
                "This page needs an item", timeout=SLOW
            )
            # The address of the old panel, which named an item by a bare number, says so too.
            await w.go("#/board?sel=node:12")
            await expect(page.locator("#panel")).to_contain_text(
                "That address does not name something the board can show", timeout=SLOW
            )
            assert await page.locator("#panel .card-detail").count() == 0
            for text in await page.locator("#panel, #page").all_inner_texts():
                assert "undefined" not in text and "[object" not in text and "NaN" not in text
            assert w.problems == [], w.problems


# -- 7. editing: saved, refused when stale, and never drawn over ---------------------------------


async def test_an_edit_on_the_page_is_saved_and_a_stale_one_is_refused_in_the_servers_words(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        story, word = world["story"], world.word
        async with browser_page(world.board, token=world.project.key) as w:
            await see_page(w, story)
            page = w.page
            await page.locator("#edit-title").fill(f"Place an order, better {word}")
            await page.locator("#edit-priority").select_option("urgent")
            await page.get_by_role("button", name="Save changes").click()
            await expect(page.locator("h1#item-title")).to_contain_text("better", timeout=SLOW)
            saved = await ok(client, "get_node", node=story)
            assert (saved["title"], saved["priority"], saved["version"]) == (
                f"Place an order, better {word}",
                "urgent",
                2,
            )
            # The heading, the tab and the way back follow, and the table is as it was.
            assert (
                await page.title()
                == f"{code_of(story)} Place an order, better {word} · {world.project.name} · Vrum"
            )
            await expect(page.locator("nav.crumbs li").last).to_contain_text("better", timeout=SLOW)
            assert await item_rows(page) == [t["ref"] for t in await children_of(client, story)]

            # Someone else changes it while a draft is typed: nothing typed is overwritten.
            title = page.locator("#edit-title")
            await title.fill(f"My draft {word}")
            await ok(
                client, "update_node", actor="agent-1", node=story, expected_version=2,
                title=f"Agent title {word}",
            )  # fmt: skip
            changed = page.locator("#item-details .notice.changed")
            await expect(changed).to_be_visible(timeout=SLOW)
            await expect(changed).to_contain_text("agent-1")
            assert await title.input_value() == f"My draft {word}"
            await expect(page.locator("h1#item-title")).to_contain_text("Agent title", timeout=SLOW)

            # Saving the draft is refused with the words the tool uses, and keeps what was typed.
            said = await refused(
                client, "update_node", actor="x", node=story, expected_version=2, title="whatever"
            )
            await page.get_by_role("button", name="Save changes").click()
            problem = page.locator("#item-details .edit .problem .refusal-text")
            await expect(problem).to_be_visible(timeout=SLOW)
            plain(cell_text(await problem.inner_text()), cell_text(said))
            assert await title.input_value() == f"My draft {word}"
            assert (await ok(client, "get_node", node=story))["title"] == f"Agent title {word}"

            await page.locator("#item-details .load-current").first.click()
            await expect(page.locator("#item-details #edit-title")).to_have_value(
                f"Agent title {word}", timeout=SLOW
            )
            assert w.problems == [], w.problems


async def test_an_agent_adding_a_task_updates_the_open_page_and_a_draft_typed_there_survives(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        story, word = world["story"], world.word
        async with browser_page(world.board, token=world.project.key) as w:
            await see_page(w, story)
            page = w.page
            before = await item_rows(page)
            assert cell_text(await page.locator("#item-count").inner_text()) == "7 tasks"
            draft = page.locator("#edit-body")
            await draft.fill(f"A draft that is not saved {word}")
            title = page.locator("#edit-title")
            await title.focus()

            # An agent adds a task and changes another one, with the page open.
            added = await made(
                client, "task", f"Added by an agent {word}", story, priority="urgent"
            )
            await status_of(client, world["ship"], "in_progress")
            await ok(client, "claim_node", actor="dana", node=world["pay"])
            await expect(item_row(page, added)).to_be_visible(timeout=SLOW)
            assert await item_rows(page) == [*before, added]
            assert cell_text(await page.locator("#item-count").inner_text()) == "8 tasks"
            cells = await cells_of(page, added)
            assert cells["title"] == f"Added by an agent {word}" and cells["priority"] == "Urgent"
            await expect(item_row(page, world["ship"]).locator(".cell-status")).to_contain_text(
                "In progress", timeout=SLOW
            )
            # The draft, and the focus, were not touched.
            assert await draft.input_value() == f"A draft that is not saved {word}"
            assert await page.evaluate("document.activeElement.id") == "edit-title"

            # A rename of a task reaches the row, and the sorted order is kept.
            await page.locator("#item-table button.sort[data-sort=title]").click()
            ship = await ok(client, "get_node", node=world["ship"])
            await ok(
                client, "update_node", actor="agent-1", node=world["ship"],
                expected_version=ship["version"], title=f"Task 1 shipped first {word}",
            )  # fmt: skip
            await expect(item_row(page, world["ship"]).locator(".cell-title")).to_contain_text(
                "shipped first", timeout=SLOW
            )
            tasks = await children_of(client, story)
            assert await item_rows(page) == sorted_as_shown(tasks, *TASK_COLUMNS["title"], False)
            assert await draft.input_value() == f"A draft that is not saved {word}"
            assert w.problems == [], w.problems


# -- 8. the quick look, in the panel beside the page ---------------------------------------------


async def test_a_rows_quick_look_opens_the_panel_beside_the_page_and_no_id_is_used_twice(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        story, task = world["story"], world["copy"]
        async with browser_page(world.board, token=world.project.key) as w:
            await see_page(w, story)
            page = w.page
            button = item_row(page, task).locator("button.quick-look")
            assert await button.get_attribute("aria-label") == f"Quick look at {code_of(task)}"
            await button.click()
            await expect(page.locator("#panel #card-title")).to_contain_text(
                f"{code_of(task)} task 3", timeout=SLOW
            )
            assert f"item={story}" in page.url and f"sel={task}" in page.url
            await expect(item_row(page, task)).to_have_class(
                re.compile(r"\bselected\b"), timeout=SLOW
            )
            assert (
                await item_row(page, task).locator(".item-code-link").get_attribute("aria-current")
                == "true"
            )
            # The page is still the story's, with both cards on it, each with its own ids.
            assert await page.locator("h1#item-title").count() == 1
            ids = await page.evaluate(
                "() => { const all = [...document.querySelectorAll('[id]')].map((e) => e.id);"
                " return all.filter((id, at) => all.indexOf(id) !== at); }"
            )
            assert ids == [], ids
            assert (
                await page.locator("#panel #panel-edit-title").input_value()
                == ((await ok(client, "get_node", node=task))["title"])
            )
            assert (
                await page.locator("#item-details #edit-title").input_value()
                == ((await ok(client, "get_node", node=story))["title"])
            )
            # Choosing another row moves the mark, closing the panel clears it, the page stays.
            await item_row(page, world["deploy"]).locator("button.quick-look").click()
            await expect(page.locator("#panel #card-title")).to_contain_text(
                code_of(world["deploy"]), timeout=SLOW
            )
            assert await page.locator("#item-table tr.selected").count() == 1
            await page.locator("#panel").get_by_role("button", name="Close the details").click()
            await expect(page.locator("#panel")).to_be_hidden(timeout=SLOW)
            assert await page.locator("#item-table tr.selected").count() == 0
            assert await page.locator("#item-page").get_attribute("data-ref") == story
            assert w.problems == [], w.problems


# -- 9. by keyboard alone ------------------------------------------------------------------------


async def test_the_table_is_used_by_keyboard_alone(server_url: str) -> None:
    async with ordering(server_url) as (world, client):
        story = world["story"]
        tasks = await children_of(client, story)
        async with browser_page(world.board, token=world.project.key) as w:
            await see_page(w, story)
            page = w.page
            await expect(page.locator("#item-title")).to_be_focused(timeout=SLOW)
            region = page.locator("#item-region")
            assert await region.get_attribute("tabindex") == "0"
            assert await region.get_attribute("role") == "region"
            assert await region.get_attribute("aria-label") == f"Tasks in {code_of(story)}"
            await frames(page)

            # Tab reaches the first column header; Enter sorts, and the focus stays on it.
            await tab_to(page, "#item-table button.sort[data-sort=item]")
            await tab_to(page, "#item-table button.sort[data-sort=title]")
            await page.keyboard.press("Enter")
            await expect(page.locator("th.col-title")).to_have_attribute(
                "aria-sort", "ascending", timeout=SLOW
            )
            assert await item_rows(page) == sorted_as_shown(tasks, *TASK_COLUMNS["title"], False)
            assert await page.evaluate("document.activeElement.dataset.sort") == "title"
            await page.keyboard.press("Enter")
            await expect(page.locator("th.col-title")).to_have_attribute(
                "aria-sort", "descending", timeout=SLOW
            )
            assert await item_rows(page) == sorted_as_shown(tasks, *TASK_COLUMNS["title"], True)
            assert await page.evaluate("document.activeElement.dataset.sort") == "title"
            # Space does the same on the next header.
            await page.keyboard.press("Tab")
            assert await page.evaluate("document.activeElement.dataset.sort") == "status"
            await page.keyboard.press("Space")
            await expect(page.locator("th.col-status")).to_have_attribute(
                "aria-sort", "ascending", timeout=SLOW
            )
            assert await item_rows(page) == sorted_as_shown(tasks, *TASK_COLUMNS["status"], False)
            assert await page.evaluate("document.activeElement.dataset.sort") == "status"
            # The announcement for a screen reader says what happened.
            assert await page.locator("#live").inner_text() == "Sorted by Status, ascending."

            # Tab goes on into the rows: an item's link, which Enter opens as a page ...
            row = f"#row-{world['api'].replace(':', '-')}"
            await tab_to(page, f"{row} .item-code-link", limit=400)
            await page.keyboard.press("Enter")
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", world["api"], timeout=SLOW
            )
            await page.go_back()
            await expect(item_row(page, world["api"])).to_be_visible(timeout=SLOW)
            # ... and the quick look, which Enter opens in the panel beside the page (the page
            # puts the focus on its title once its details are in: wait for that, then Tab).
            await expect(page.locator("#item-details .card-detail")).to_be_visible(timeout=SLOW)
            await expect(page.locator("#item-title")).to_be_focused(timeout=SLOW)
            await frames(page)
            await tab_to(page, f"{row} button.quick-look", limit=400)
            await page.keyboard.press("Enter")
            await expect(page.locator("#panel #card-title")).to_contain_text(
                code_of(world["api"]), timeout=SLOW
            )
            assert f"sel={world['api']}" in page.url and f"item={story}" in page.url
            assert w.problems == [], w.problems


# -- 10. a narrow window, in both themes ---------------------------------------------------------

NARROW = {"width": 390, "height": 900}
OVERFLOW = """() => {
    const width = window.innerWidth;
    const region = document.querySelector('#item-region');
    const inside = (e) => e.closest('.table-scroll, .gv-tree, svg');
    const offenders = [...document.querySelectorAll('body *')]
        .filter((e) => !inside(e) && e.getBoundingClientRect().width > 0
            && e.getBoundingClientRect().right > width + 1)
        .map((e) => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + '.' + e.className);
    return {
        width,
        doc: document.documentElement.scrollWidth,
        body: document.body.scrollWidth,
        scrollX: window.scrollX,
        offenders: offenders.slice(0, 8),
        region: region ? { scroll: region.scrollWidth, client: region.clientWidth } : null,
        theme: document.documentElement.getAttribute('data-theme'),
    };
}"""


async def narrow_audit(w: Watched, world: World, scheme: str) -> None:
    """Every page of the world at 390 pixels: the window never scrolls sideways, and a table that
    is wider than the window scrolls in a region of its own that a keyboard can reach."""
    page = w.page
    for name in ("story", "epic", "api", "pay", "empty_epic"):
        await see_page(w, world[name])
        await frames(page)
        found = await page.evaluate(OVERFLOW)
        where = (scheme, name, found)
        assert found["theme"] == scheme, where
        assert found["doc"] <= found["width"] and found["body"] <= found["width"], where
        assert found["offenders"] == [], where
        if name not in ("story", "epic"):
            continue
        region = found["region"]
        # The table is wider than the window and scrolls inside its own region.
        assert region and region["scroll"] > region["client"], where
        await page.evaluate("document.querySelector('#item-region').scrollLeft = 300")
        after = await page.evaluate(OVERFLOW)
        assert after["scrollX"] == 0 and after["doc"] <= after["width"], where
        moved = await page.evaluate("document.querySelector('#item-region').scrollLeft")
        assert moved > 0, where
        assert await page.locator("#item-region").get_attribute("tabindex") == "0"
        # What can be reached can be used: the quick look button is a real target (24 pixels, as
        # the layout tests of M17 count it, WCAG 2.5.8).
        row = item_row(page, world["ship" if name == "story" else "pay"])
        box = await row.locator("button.quick-look").bounding_box()
        assert box and box["width"] >= 24 and box["height"] >= 24, where
    assert w.problems == [], w.problems
    assert await policy_violations(page) == []


@pytest.mark.parametrize("scheme", ["light", "dark"])
async def test_at_390_pixels_in_both_themes_the_page_does_not_scroll_and_the_table_does(
    server_url: str, scheme: str
) -> None:
    async with (
        ordering(server_url, long_title=True) as (world, _),
        themed_page(world.board, signed_in=True, scheme=scheme, viewport=NARROW) as w,
    ):
        await narrow_audit(w, world, scheme)
