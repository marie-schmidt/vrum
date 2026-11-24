"""M19's and M20's page of an epic, story or task (#/item?item=story:12&from=board), as M35 made
it calmer, driven in a real headless browser against the real containers and checked against a real
MCP client: what a person reads in the page and in its table is compared with what the server says
(`list_nodes`, `get_node`, `recall`, `list_notes`, `get_rule`, `list_links`), and what a person does
there is read back through the tools.

A story's page has all of its details (edited with the same version checks as an agent's change),
ONE strip of its properties in the header and, under them, a table of its tasks and its Links: an
epic's page lists its stories with their progress, and the path in the top bar names the epic and
story a task is in. The table sorts by every column, is only its heading and the way to add one when
it is empty, follows an agent's work while it is open, and works by keyboard and at 390 pixels in
both themes. Each page has previous and next among its siblings and no list of its sections. What
concerns an item (criteria and notes) is a table across the page, and an acceptance criterion
(AC-3) and a note (N-7) have a page of their own with a strip that holds only what they have:
nothing opens beside the page, since there is no panel and no drawer."""

import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_context import fresh_project
from tests.board_helpers import (
    SLOW,
    Watched,
    board_url,
    browser_page,
    crumbs,
    item_address,
    item_loaded,
    item_name,
    item_row,
    item_rows,
    made,
    no_panel_or_drawer,
    nothing_open,
    open_editor,
    open_item,
    pager,
    pager_go,
    part,
    plain,
    record_rows,
    siblings,
    strip_labels,
)
from tests.board_layout import frames, tab_to
from tests.board_search import DUPLICATES, twin_epics
from tests.board_theme import policy_violations, themed_page
from tests.helpers import (
    all_nodes,
    code_of,
    connect,
    day,
    make_decision,
    make_note,
    number_of,
    ok,
    ref_order,
    refused,
    reindexed,
    unique_word,
)
from tests.world import Project, join_project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

STATUS_ORDER = ["new", "ready", "in_progress", "blocked", "done", "dropped"]
PRIORITY_ORDER = ["urgent", "high", "normal", "low"]  # urgent first
# The sections of a recall, whose counts the page's "Notes" adds up.
RECALLED = ["notes", "past_notes", "project_wide_notes"]
# What the history says for a change the tests make through MCP: the name of the harness key.
AGENT = "tests"


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
    with none, one with two done), and an epic with nothing in it, a criterion, two notes (one of
    them decided by a person) and a link on the first story."""
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
        ("form", "Task 10 build the form", {"size": "m"}),
        ("api", "Task 2 write the API", {"priority": "urgent", "target_on": day(-3), "size": "xl"}),
        ("copy", "task 3 copy the words", {"priority": "low", "size": "s"}),
        ("deploy", "Task 4 deploy", {"priority": "high", "size": "m"}),
        ("docs", "Task 5 write the docs", {"target_on": day(5)}),
        ("polish", "Task 6 polish", {"priority": "low", "target_on": day(10), "size": "xs"}),
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
    await ok(client, "create_rule", actor="agent-1", title=f"Fast {word}", node=refs["story"])
    await make_decision(client, f"Use REST {word}", nodes=[refs["story"]])
    await make_note(
        client, f"Orders are small {word}", "Counted in the last quarter", nodes=[refs["story"]]
    )
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
    """Open the page of an item, reached from the Board, and wait until its details and what is
    under it are drawn."""
    await open_item(w, ref)


async def children_of(client: Client, ref: str) -> list[dict[str, Any]]:
    return await all_nodes(client, parent=ref)


async def heard_what_it_did(page: Page) -> None:
    """Wait until the board has asked what changed twice: a save of its own is then no longer news
    to it, so that the next change it hears of, while a draft is typed, is somebody else's."""
    for _ in range(2):
        async with page.expect_response(
            lambda response: response.url.endswith("/tool/changes_since"), timeout=SLOW
        ):
            pass


async def read_again(page: Page) -> None:
    """After a change the page saved (the chip already shows it), wait until the page has read the
    item again and drawn its strip anew: the next change it makes is then at the version it holds
    now. (A change made within the moment the page takes to ask what else changed is refused as
    stale, which only a program is quick enough to do.)"""
    strip = page.locator("#item-page .card-summary dl.state-strip")
    await strip.evaluate("e => e.setAttribute('data-drawn-before', '1')")
    await expect(page.locator("[data-drawn-before]")).to_have_count(0, timeout=SLOW)


def cell_text(text: str) -> str:
    return " ".join(text.split())


async def cells_of(page: Page, ref: str) -> dict[str, str]:
    """What each column of a row says, by the name of its column. A cell with nothing to say is
    blank; the priority is the one a chip names (none for Normal, which draws nothing)."""
    row = item_row(page, ref)
    out: dict[str, str] = {}
    for name in (
        "item",
        "title",
        "status",
        "progress",
        "size",
        "held",
        "due",
        "waiting",
    ):
        place = row.locator(f".cell-{name}")
        if await place.count():
            out[name] = cell_text(await place.inner_text())
    chip = row.locator(".cell-priority [data-priority]")
    out["priority"] = (
        (await chip.get_attribute("data-priority") or "") if await chip.count() else ""
    )
    return out


def status_words(status: str) -> str:
    return status.replace("_", " ").capitalize()


# -- 1. a story's page: its details, and exactly its tasks ---------------------------------------


async def section_counts(page: Page) -> dict[str, str]:
    """The number each section says beside its heading ("Notes 13", "Links 2", "1 of 3 met"), by
    the section's name. A section that is not drawn is not in the answer; one with nothing to count
    says ''."""
    found: dict[str, str] = await page.evaluate(
        """() => {
            const places = {
                table: '#item-children', links: '#item-links',
                requirements: '[data-part=requirements]', context: '[data-part=context]',
                history: '[data-part=history]',
            };
            const out = {};
            for (const [key, selector] of Object.entries(places)) {
                const section = document.querySelector('#item-page ' + selector);
                if (!section || section.hidden) continue;
                const count = section.querySelector('.heading-count, .item-count');
                out[key] = count ? count.textContent.trim() : '';
            }
            return out;
        }"""
    )
    return found


async def test_a_storys_page_has_every_detail_the_server_has_and_a_table_of_exactly_its_tasks(
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
            # The title is the heading, with the code; nothing sits under it, and the epic it
            # is in is the link in the path.
            assert cell_text(await page.locator("h1#item-title").inner_text()) == (
                f"{code} Place an order {world.word}"
            )
            assert await page.locator("nav.crumbs li.crumb-item a").get_attribute(
                "href"
            ) == item_address(world["epic"], "board")
            assert (
                await page.title()
                == f"{code} Place an order {world.word} · {world.project.name} · Vrum"
            )
            assert await page.locator("#item-page").get_attribute("data-kind") == "story"
            # The way back: the view it came from / the epic / the story, each record as its code
            # and its name (the name is clipped when there is no room, whole in the tooltip).
            epic_name = await item_name(client, world["epic"])
            assert await crumbs(page) == ["Board", epic_name, await item_name(client, story)]
            assert await page.locator("nav.crumbs li a").evaluate_all(
                "els => els.map(e => e.getAttribute('href'))"
            ) == ["#/board", item_address(world["epic"], "board")]
            assert await page.locator("nav.crumbs li.crumb-item a").get_attribute("title") == (
                epic_name
            )
            await no_panel_or_drawer(page)

            # Every detail the server has is on the page: how it stands in the header's one strip
            # (a story with sized tasks adds up their points), the words and what surrounds them in
            # the details.
            details = page.locator("#item-details")
            text = cell_text(await page.locator("#item-page").inner_text())
            assert "High" in text and status_words(server["status"]) in text
            assert f"{server['points_done']} of {server['points_total']} points" in text
            assert await details.locator(".body-text em").inner_text() == "story"
            assert await details.locator(".body-text li").all_inner_texts() == [
                "first point",
                "second point",
            ]
            # What concerns it is a table of each kind, as the server holds it: its acceptance
            # criterion, its notes (each with the one status the server gives it) and its link, each
            # as many as the number beside the section's heading counts.
            rules = (await ok(client, "list_rules", node=story))["items"]
            notes = (await ok(client, "list_notes", node=story))["items"]
            assert [(r["id"], r["status"]) for r in await record_rows(page, "requirements")] == [
                (r["id"], r["status"]) for r in rules
            ]
            assert [(r["id"], r["status"]) for r in await record_rows(page, "notes")] == [
                (n["id"], n["status"]) for n in notes
            ]
            assert [n["status"] for n in notes] == ["unconfirmed", "unconfirmed"]
            recalled = await ok(client, "recall", node=story)
            history = (await ok(client, "node_history", node=story, limit=200))["items"]
            links = (await ok(client, "list_links", node=story))["items"]
            assert await section_counts(page) == {
                "table": "7",
                "requirements": "0 of 1 met",
                "context": str(sum(recalled[name]["count"] for name in RECALLED)),
                "links": str(len(links)),
                "history": str(len(history)),
            }
            # No definition of done is written in the shared project: the section is an empty
            # field with the way to Settings.
            await expect(part(page, "definition").locator(".details-empty")).to_be_visible()
            # The words are read as text first: the form is not on the page until Edit is pressed.
            assert await details.locator("form.edit").count() == 0
            await open_editor(page, "#item-page")
            assert await details.locator(".body-text").count() == 0
            assert f"Fast {world.word}" in text
            assert f"Use REST {world.word}" in text and f"Orders are small {world.word}" in text
            assert f"{code_of(world['track'])} Track it {world.word}" in text  # the link
            for field in ("title", "body", "status", "priority", "start", "target"):
                assert await details.locator(f"#edit-{field}").count() == 1
            assert await details.locator("#edit-title").input_value() == server["title"]
            assert await details.locator("#edit-priority").input_value() == "high"

            # The table lists exactly the story's tasks, as the server lists them.
            await see_page(w, story)
            assert await item_rows(page) == [t["ref"] for t in tasks]
            assert cell_text(await page.locator("#item-count").inner_text()) == "7"
            assert cell_text(await page.locator("#item-table caption").inner_text()) == (
                f"Tasks in {await item_name(client, story)}"
            )
            assert await page.locator("#item-children-title").inner_text() == "Tasks"
            assert await page.locator("#item-table thead th button.sort").evaluate_all(
                "els => els.map(e => e.dataset.sort)"
            ) == ["item", "title", "status", "size", "priority", "held", "due", "waiting"]
            assert (
                await page.locator("#item-table th.col-item").get_attribute("aria-sort")
                == "ascending"
            )
            # None of another story's tasks is there.
            for other in ("announce", "celebrate"):
                assert await item_row(page, world[other]).count() == 0
            for task in tasks:
                ref = task["ref"]
                cells = await cells_of(page, ref)
                assert cells["item"] == code_of(ref)
                assert await item_row(page, ref).locator(".cell-item a").get_attribute("href") == (
                    item_address(ref, "board")
                )
                assert cells["title"] == task["title"]
                assert cells["status"].lower() == status_words(task["status"]).lower()
                assert cells["size"] == (task["size"].upper() if task["size"] else "Not set")
                # A priority that is Normal draws nothing; any other is a sign with its word.
                assert cells["priority"] == (
                    "" if task["priority"] == "normal" else task["priority"]
                )
                assert cells["held"] == (task["claimed_by"] or "")
                # A day that is set is ONE chip, red when the work is late; none draws nothing.
                late = item_row(page, ref).locator(".cell-due .chip.due.danger.overdue")
                if task["target_on"]:
                    assert cells["due"] == f"Due {task['target_on']}"
                    assert await late.count() == int(task["overdue"])
                    assert await item_row(page, ref).locator(".cell-due .chip").count() == 1
                else:
                    assert cells["due"] == ""
                # What it waits for is a list, one line to each, the code and then the name.
                waiting = item_row(page, ref).locator(
                    ".cell-waiting ul.waiting-list a.waiting-link"
                )
                assert [cell_text(t) for t in await waiting.all_inner_texts()] == [
                    await item_name(client, r) for r in task["waiting_on"]
                ]
                assert await waiting.evaluate_all(
                    "els => els.map(e => e.getAttribute('href'))"
                ) == [item_address(r, "board") for r in task["waiting_on"]]
                if not task["waiting_on"]:
                    assert cells["waiting"] == ""
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
    "size": (lambda r: r["points_total"] or 0, lambda r: not r["points_total"]),
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
            await expect(w.page.locator("#item-table th.col-title")).to_have_attribute(
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
                **{
                    name: TASK_COLUMNS[name]
                    for name in ("item", "title", "status", "size", "priority")
                },
                "progress": (lambda r: share[r["ref"]], lambda r: not r["progress_total"]),
            }
            await sort_both_ways(w.page, stories, columns)
            # A column nobody has a value in (here: held by, due, waiting for) is not drawn.
            assert await w.page.locator("#item-table button.sort").evaluate_all(
                "els => els.map(e => e.dataset.sort)"
            ) == [
                "item",
                "title",
                "status",
                "progress",
                "size",
                "priority",
            ]
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
            # With nothing under it the section is its heading and the way to add one: no count,
            # no table and no box that says there is nothing.
            assert cell_text(await page.locator("#item-count").inner_text()) == ""
            await expect(page.locator("#item-children")).to_be_visible(timeout=SLOW)
            assert await page.locator("#item-children-title").inner_text() == "Tasks"
            assert await page.locator("#item-table").count() == 0
            assert await page.locator("#item-region").is_hidden()
            assert await page.locator("#item-empty").count() == 0
            assert await page.locator("#new-child").inner_text() == "New task"

            # New task starts the form on a task under this story, and nothing else.
            await page.locator("#new-child").click()
            dialog = page.locator("dialog.modal")
            assert await dialog.locator("#new-kind").input_value() == "task"
            assert await dialog.locator("#new-parent").input_value() == pay
            await dialog.locator("#new-title").fill(f"First task of it {world.word}")
            await dialog.get_by_role("button", name="Create").click()
            # Saving it goes to the new task's own page.
            await expect(page.locator("#item-title")).to_contain_text(
                f"First task of it {world.word}", timeout=SLOW
            )
            made_tasks = await children_of(client, pay)
            assert [t["title"] for t in made_tasks] == [f"First task of it {world.word}"]
            task = made_tasks[0]["ref"]
            assert made_tasks[0]["parent"] == pay and made_tasks[0]["kind"] == "task"
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", task, timeout=SLOW
            )
            assert cell_text(await page.locator("#item-title").inner_text()) == (
                f"{code_of(task)} First task of it {world.word}"
            )
            assert f"item={task}" in page.url and "from=board" in page.url
            await no_panel_or_drawer(page)

            # The way back: the epic and the story it is part of are the path in the top bar, each
            # a link, and there is no card on the page that says it a second time.
            assert await crumbs(page) == [
                "Board",
                await item_name(client, world["epic"]),
                await item_name(client, pay),
                await item_name(client, task),
            ]
            assert await page.locator("nav.crumbs li a").evaluate_all(
                "els => els.map(e => e.getAttribute('href'))"
            ) == [
                "#/board",
                item_address(world["epic"], "board"),
                item_address(pay, "board"),
            ]
            await page.locator(f"nav.crumbs li a[href='{item_address(pay, 'board')}']").click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", pay, timeout=SLOW
            )
            # It is in the story's table, which now has a count and no empty state.
            await expect(item_row(page, task)).to_be_visible(timeout=SLOW)
            assert await item_rows(page) == [task]
            assert cell_text(await page.locator("#item-count").inner_text()) == "1"
            await page.go_back()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", task, timeout=SLOW
            )

            # An epic with no stories is the same, and adds a story under it, which opens on its
            # own page; the epic's table has it when the person goes back up.
            empty = world["empty_epic"]
            await see_page(w, empty)
            assert await page.locator("#item-table").count() == 0
            assert await page.locator("#item-children-title").inner_text() == "Stories"
            assert await page.locator("#new-child").inner_text() == "New story"
            await page.locator("#new-child").click()
            dialog = page.locator("dialog.modal")
            assert await dialog.locator("#new-kind").input_value() == "story"
            assert await dialog.locator("#new-parent").input_value() == empty
            await dialog.locator("#new-title").fill(f"A story of it {world.word}")
            await dialog.get_by_role("button", name="Create").click()
            # Saving it goes to the new story's own page: wait for that before asking the server.
            await expect(page.locator("#item-title")).to_contain_text(
                f"A story of it {world.word}", timeout=SLOW
            )
            stories = await children_of(client, empty)
            assert [s["title"] for s in stories] == [f"A story of it {world.word}"]
            story = stories[0]["ref"]
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", story, timeout=SLOW
            )
            await page.locator("nav.crumbs li.crumb-item a").click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", empty, timeout=SLOW
            )
            await expect(page.locator("#item-table tbody tr")).to_have_count(1, timeout=SLOW)
            assert await item_rows(page) == [story]
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
            assert await page.locator("#item-page").get_attribute("data-kind") == "epic"
            assert await item_rows(page) == [s["ref"] for s in stories]
            assert cell_text(await page.locator("#item-count").inner_text()) == "3"
            assert await page.locator("#item-children-title").inner_text() == "Stories"
            assert await page.locator("#item-details").inner_text()
            # The epic's one strip says how far it has got: its stories' sizes add up to points.
            assert f"{server['points_done']} of {server['points_total']} points" in (
                cell_text(await page.locator("#item-page .card-summary").inner_text())
            )
            for story in stories:
                cells = await cells_of(page, story["ref"])
                done, total = story["progress_done"], story["progress_total"]
                # A story with no task has nothing to say about progress, so its cell is blank.
                assert cells["progress"] == (f"{done} of {total} done" if total else "")
                assert cells["title"] == story["title"]
                meter = item_row(page, story["ref"]).locator(".cell-progress .meter span")
                if total:
                    width = await meter.evaluate("e => e.style.width")
                    assert width == f"{round(done / total * 100)}%", (story["ref"], width)
                else:
                    assert await meter.count() == 0
            # A row opens the story's own page, which says which epic it is in (in the path) and
            # keeps the view the person came from.
            await item_row(page, world["track"]).locator(".cell-title a").click()
            await item_loaded(page, world["track"])
            assert "from=board" in page.url and "sel=" not in page.url
            assert await crumbs(page) == [
                "Board",
                await item_name(client, epic),
                await item_name(client, world["track"]),
            ]
            assert await item_rows(page) == [world["announce"], world["celebrate"]]
            await no_panel_or_drawer(page)
            assert w.problems == [], w.problems


# -- 5. a task's page, and the three pages have one structure ------------------------------------

SKELETON = """() => {
    const name = (e) => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '');
    const page = document.querySelector('#item-page');
    const children = document.querySelector('#item-children');
    const links = document.querySelector('#item-links');
    const card = page.querySelector('#item-details > article');
    return {
        page: [...page.children].map(name),
        head: [...page.querySelector('header').children].map((e) => e.className),
        tools: [...page.querySelector('.item-tools').children].map(name),
        inside: [children, links].filter(Boolean).every((e) => e.closest('#item-details')),
        title: [...page.querySelectorAll('h1')].length,
        card: [...page.querySelectorAll('#item-details > article')].map((e) => e.className),
        summary: [...page.querySelectorAll('header .card-summary > *')].map((e) => e.className),
        strips: page.querySelectorAll('dl.state-strip').length,
        columns: page.querySelectorAll('.card-columns, .card-side').length,
        children: children ? [...children.children].map(name) : null,
        links: [...links.children].map(name),
        sections: [...card.children].map((e) => e.dataset.part || e.id || e.className),
        mains: document.querySelectorAll('main').length,
    };
}"""


async def test_a_tasks_page_is_named_by_its_path_lists_its_links_and_the_pages_share_one_shape(
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
            # Which story and epic it is part of is the path in the top bar, each a link with its
            # title as the tooltip, and no card on the page says it a second time.
            assert await crumbs(page) == [
                "Board",
                await item_name(client, world["epic"]),
                await item_name(client, world["story"]),
                await item_name(client, task),
            ]
            ups = page.locator("nav.crumbs li.crumb-item a")
            assert await ups.evaluate_all("els => els.map(e => e.getAttribute('href'))") == [
                item_address(world["epic"], "board"),
                item_address(world["story"], "board"),
            ]
            assert await ups.evaluate_all("els => els.map(e => e.getAttribute('title'))") == [
                await item_name(client, world["epic"]),
                await item_name(client, world["story"]),
            ]
            assert await page.locator("#item-part, .item-part").count() == 0
            # It has no children table, no count and no way to add something under it.
            for gone in (
                "#item-table",
                "#item-count",
                "#new-child",
                "#item-region",
                "#item-children",
            ):
                assert await page.locator(gone).count() == 0, gone
            # Its one strip says how it stands: urgent, and late, in red, with how many days.
            strip = page.locator("#item-page .card-summary dl.state-strip")
            await expect(strip.locator("dd.priority .chip")).to_have_attribute(
                "data-priority", "urgent"
            )
            late = strip.locator("dd.target .chip.due.danger.overdue")
            await expect(late).to_have_attribute("data-late", "3")
            assert cell_text(await late.inner_text()) == "3 days late"
            assert await strip.locator("dd.size").inner_text() == "Extra large, 8 points"
            await open_editor(page, "#item-page")
            assert await page.locator("#item-details #edit-title").input_value() == server["title"]
            # What it needs first and what needs it are its Links, both directions, as the server
            # lists them: it needs the form task first, and the polish task needs it.
            links = (await ok(client, "list_links", node=task))["items"]
            assert {(link["source"], link["target"]) for link in links} == {
                (task, world["form"]),
                (world["polish"], task),
            }
            assert cell_text(await page.locator("#links-count").inner_text()) == "2"
            relation = page.locator("#links-table tbody tr")
            assert await relation.count() == 2
            assert await relation.locator(".cell-relation").all_inner_texts() == [
                "Needs first",
                "Needed by",
            ]
            assert await relation.evaluate_all(
                "els => els.map(e => [e.dataset.type, e.dataset.direction, e.dataset.ref])"
            ) == [
                ["needs_first", "out", world["form"]],
                ["needs_first", "in", world["polish"]],
            ]
            assert cell_text(await relation.first.locator(".cell-code").inner_text()) == code_of(
                world["form"]
            )
            # Following the crumb to the story goes to its page, with the task in its table.
            await ups.last.click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", world["story"], timeout=SLOW
            )
            await expect(item_row(page, task)).to_be_visible(timeout=SLOW)

            # One shape: the same head, one strip of properties, one column of sections and no
            # list of them; an epic's and a story's pages have a table of what is under them, a
            # task's has none, and all three have their Links.
            epic_shape, story_shape, task_shape = shapes["epic"], shapes["story"], shapes["api"]
            for shape in shapes.values():
                assert shape["page"] == ["header", "h2#item-details-title", "section#item-details"]
                assert shape["head"] == ["page-title", "item-tools", "card-summary"]
                # The way to the neighbours comes before the actions, on every page.
                assert shape["tools"] == ["nav#item-pager", "div"], shape["tools"]
                assert shape["title"] == 1 and shape["mains"] == 1
                assert shape["card"] == ["card-detail detail card-page"]
                assert shape["strips"] == 1 and shape["summary"][0] == "state-strip", shape
                assert shape["columns"] == 0
                assert shape["links"] == ["header", "div#links-region"]
                # What is under the item is a table of the page's width inside its details.
                assert shape["inside"] is True
            assert (
                epic_shape["children"] == story_shape["children"] == ["header", "div#item-region"]
            )
            assert task_shape["children"] is None
            # Similar is there only when there is something alike, so it is left out here. A person
            # who can write also has the Gates section (Add a gate) and the button that asks where a
            # story or task could go, which an epic has neither of.
            order = {
                name: [one for one in shape["sections"] if one != "similar"]
                for name, shape in shapes.items()
            }
            assert order["epic"] == ["card-main", "item-children", "links", "context", "history"]
            assert order["story"] == [
                "card-main",
                "gates",
                "item-children",
                "links",
                "suggest",
                "requirements",
                "definition",
                "context",
                "history",
            ]
            assert order["api"] == [
                "card-main",
                "gates",
                "links",
                "suggest",
                "requirements",
                "definition",
                "context",
                "history",
            ]
            assert w.problems == [], w.problems


# -- 5b. previous and next ----------------------------------------------------------------------


async def test_previous_and_next_walk_through_exactly_the_siblings_the_server_lists(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        note = (await ok(client, "list_notes", limit=1))["items"][0]["id"]
        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page
            # Epics across the project, the stories of an epic, the tasks of a story: the pager of
            # each page moves through what the server lists, in order, from one end to the other
            # and back, says where it is, and is disabled at the ends.
            for start in (world["epic"], world["story"], world["api"]):
                wanted = await siblings(client, start)
                noun = start.split(":")[0]
                assert len(wanted) >= 2 and start in wanted
                await see_page(w, wanted[0])
                first = await pager(page)
                assert first["previous"] == {
                    "disabled": True,
                    "label": f"No previous {noun}",
                    "code": None,
                }, first
                assert (first["at"], first["of"]) == (1, len(wanted))
                # The pager says which siblings it moves between, by the name of what holds them.
                plural = {"epic": "epics", "story": "stories", "task": "tasks"}[noun]
                parent = (await ok(client, "get_node", node=start))["parent"]
                holder = await item_name(client, parent) if parent else "this project"
                assert await page.locator("#item-pager").get_attribute("aria-label") == (
                    f"Move between the {plural} in {holder}"
                )
                walked = [wanted[0]]
                while not (await pager(page))["next"]["disabled"]:
                    here = await pager(page)
                    after = wanted[len(walked)]
                    assert here["next"]["code"] == code_of(after), here
                    # The button is the code and the name; its label and tooltip say them whole.
                    assert here["next"]["label"] == f"Next {noun}: {await item_name(client, after)}"
                    assert await page.locator("#item-next").get_attribute("title") == (
                        f"{here['next']['label']} (])"
                    )
                    after_title = (await ok(client, "get_node", node=after))["title"]
                    assert cell_text(
                        await page.locator("#item-next .pager-name").text_content() or ""
                    ) == (after_title)
                    walked.append(await pager_go(page, "next"))
                    assert (await pager(page))["at"] == len(walked)
                assert walked == wanted
                last = await pager(page)
                assert (last["at"], last["of"]) == (len(wanted), len(wanted))
                assert last["next"] == {"disabled": True, "label": f"No next {noun}", "code": None}
                # And back, to the first.
                for before in reversed(wanted[:-1]):
                    assert await pager_go(page, "previous") == before
                assert (await pager(page))["previous"]["disabled"]
                # Moving on keeps the view the person came from.
                assert "from=board" in page.url and (await crumbs(page))[0] == "Board"
            # A requirement or a note has no neighbours to move between.
            await open_item(w, f"note:{note}")
            assert await page.locator("#item-pager, #item-prev, #item-next").count() == 0
            assert w.problems == [], w.problems


# -- 6. how a person reaches the page ------------------------------------------------------------


async def test_a_card_a_work_list_title_and_a_row_reach_the_page_and_keep_the_view_they_came_from(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, _):
        epic, story, task = world["epic"], world["story"], world["api"]
        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page
            # The Board's card: its title is a link to the page, which keeps the Board as the view.
            # (The Board shows tasks only unless it is asked for stories too.)
            await w.open(f"#/board?epic={number_of(epic)}&show=both")
            card = page.locator(f".card[data-ref='{story}']")
            await expect(card).to_be_visible(timeout=SLOW)
            title = card.locator("h3.card-title a")
            assert await title.get_attribute("href") == item_address(story, "board")
            await title.click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", story, timeout=SLOW
            )
            assert "sel=" not in page.url and "from=board" in page.url
            await no_panel_or_drawer(page)
            assert (await crumbs(page))[0] == "Board"
            # The way back returns to the Board as it was left, with its epic.
            await page.locator("nav.crumbs li.crumb-view a").click()
            await expect(card).to_be_visible(timeout=SLOW)
            assert f"epic={number_of(epic)}" in page.url
            # A task's card opens a page with no table of children.
            await page.locator(f".card[data-ref='{task}'] h3.card-title a").click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", task, timeout=SLOW
            )
            assert await page.locator("#item-table").count() == 0

            # The Lists view of Work: every title, an epic's, a story's and a task's, is a link to
            # its page, which says it came from Lists.
            await w.open(f"#/lists?view=work&epic={number_of(epic)}")
            tree = page.locator("#work-tree")
            await expect(tree.locator(f"li[data-key='{story}']")).to_be_visible(timeout=SLOW)
            for ref in (epic, story, task):
                title = tree.locator(f"li[data-key='{ref}'] > .gv-row a.gv-title")
                assert await title.get_attribute("href") == item_address(ref, "lists")
                await title.click()
                await expect(page.locator("#item-page")).to_have_attribute(
                    "data-ref", ref, timeout=SLOW
                )
                assert (await crumbs(page))[0] == "Lists"
                await expect(page.locator("#main-nav a[data-page=lists]")).to_have_attribute(
                    "aria-current", "page"
                )
                await no_panel_or_drawer(page)
                await page.go_back()
                await expect(tree.locator(f"li[data-key='{story}']")).to_be_visible(timeout=SLOW)
            # Nothing of the old panel is left in the list.
            assert await tree.locator(".gv-quick, .quick-look").count() == 0
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

            # An item that does not exist: the server's own words, and the way back. Every kind
            # of page says the same thing when what it names is not there.
            for ref, tool, arguments in (
                ("story:9999", "get_node", {"node": "story:9999"}),
                ("rule:9999", "get_rule", {"rule_id": 9999}),
                ("note:9999", "get_note", {"note_id": 9999}),
            ):
                said = await refused(client, tool, **arguments)
                await w.go(item_address(ref, "board"))
                problem = page.locator("#item-page .notice.refusal")
                await expect(problem).to_be_visible(timeout=SLOW)
                plain(cell_text(await problem.inner_text()), said)
                assert await page.locator("#item-table").count() == 0
                assert await page.locator("#item-title").inner_text() == "Nothing to show here"
            await page.locator("#item-back").click()
            await expect(page.locator("#main-nav [aria-current=page]")).to_be_visible(timeout=SLOW)
            assert page.url.endswith("#/board")

            # What is not a reference at all, and no item at all.
            await w.go("#/item?item=12")
            await expect(page.locator("#item-page")).to_contain_text(
                "That address does not name an epic, story, task, acceptance criterion or note",
                timeout=SLOW,
            )
            await w.go("#/item")
            await expect(page.locator("#item-page")).to_contain_text(
                "This page needs an item", timeout=SLOW
            )
            # The addresses of the old panel and drawers mean nothing now: the page opens without
            # their keys, shows nothing beside itself and says nothing odd.
            await w.go(f"#/board?sel={story}&drawer=search&panel=coverage")
            await expect(page.locator("#board")).to_be_visible(timeout=SLOW)
            await page.wait_for_function("() => location.hash === '#/board'", timeout=SLOW)
            await no_panel_or_drawer(page)
            await w.go(f"{item_address(story, 'board')}&sel={world['api']}&drawer=activity")
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", story, timeout=SLOW
            )
            await page.wait_for_function(
                "(want) => location.hash === want", arg=item_address(story, "board"), timeout=SLOW
            )
            await no_panel_or_drawer(page)
            for text in await page.locator("#page").all_inner_texts():
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
            await open_editor(page, "#item-page")
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
            await expect(page.locator("#crumb-page")).to_have_attribute(
                "title", re.compile("better"), timeout=SLOW
            )
            assert await item_rows(page) == [t["ref"] for t in await children_of(client, story)]

            # Someone else changes it while a draft is typed: nothing typed is overwritten.
            await heard_what_it_did(page)
            await open_editor(page, "#item-page")
            title = page.locator("#edit-title")
            await title.fill(f"My draft {word}")
            await ok(
                client, "update_node", actor="agent-1", node=story, expected_version=2,
                title=f"Agent title {word}",
            )  # fmt: skip
            changed = page.locator("#item-details .notice.changed")
            await expect(changed).to_be_visible(timeout=SLOW)
            await expect(changed).to_contain_text(AGENT)
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


# -- 7b. what an agent wrote is on the page: empty details, every change, every handoff note -----


async def test_empty_details_are_a_field_with_a_button_and_a_change_to_them_is_read_whole(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        task, word = world["form"], world.word
        async with browser_page(world.board, token=world.project.key) as w:
            await see_page(w, task)
            page = w.page
            details = page.locator("#item-details")
            # Nothing is written, and the page says so as a field: a box that says Empty, with the
            # button that fills it, and the list of sections says so too.
            empty = details.locator("[data-part=edit] .details-empty")
            await expect(empty).to_be_visible(timeout=SLOW)
            await expect(empty).to_contain_text("Empty")
            await empty.get_by_role("button", name="Add details").click()
            body = page.locator("#edit-body")
            await expect(body).to_be_focused()
            words = f"First line {word}\n\n- a point {word}"
            await body.fill(words)
            await page.get_by_role("button", name="Save changes").click()
            await expect(details.locator(".body-text")).to_contain_text(
                f"First line {word}", timeout=SLOW
            )
            assert await details.locator("[data-part=edit] .details-empty").count() == 0
            assert (await ok(client, "get_node", node=task))["body"] == words

            # The change is in the history with "Show what changed": the details before and after,
            # whole and as written, with their lines.
            history = page.locator("#item-page [data-part=history]")
            row = history.locator("li[data-action=update]").first
            await row.locator(".what-changed summary").click()
            field = row.locator(".change-field[data-field=body]")
            await expect(field.locator(".before")).to_contain_text("Empty")
            assert await field.locator(".after .change-text").inner_text() == words

            # A long title is cut in the sentence and whole in the change.
            before = (await ok(client, "get_node", node=task))["title"]
            long_title = f"A title that goes on well past what a sentence says of it {word}"
            more = f"Second version {word}\n\n1. one\n2. two"
            await ok(
                client, "update_node", node=task, expected_version=2, title=long_title, body=more
            )
            row = history.locator("li[data-action=update]").first
            await expect(page.locator("h1#item-title")).to_contain_text(
                "A title that goes on", timeout=SLOW
            )
            # The history has the new change at its top, not only the heading the news has reached.
            await expect(history.locator("li[data-action=update]")).to_have_count(2, timeout=SLOW)
            await expect(row.locator(".what-changed")).to_be_visible(timeout=SLOW)
            await expect(row.locator(".event-words")).not_to_contain_text(long_title)
            await row.locator(".what-changed summary").click()
            title = row.locator(".change-field[data-field=title]")
            assert await title.locator(".before .change-text").inner_text() == before
            assert await title.locator(".after .change-text").inner_text() == long_title
            assert (
                await row.locator(".change-field[data-field=body] .after .change-text").inner_text()
                == more
            )
            assert w.problems == [], w.problems

        # Someone who can only look sees the field, and that it is empty, but not the button.
        viewer = join_project(world.project, "viewer")
        async with browser_page(world.board, token=None, person=viewer) as v:
            await see_page(v, world["copy"])
            await expect(
                v.page.locator("#item-details [data-part=edit] .details-empty")
            ).to_contain_text("Empty", timeout=SLOW)
            await expect(v.page.locator("#item-details .add-details")).to_have_count(0)
            assert v.problems == [], v.problems


async def test_every_handoff_note_is_listed_with_who_made_the_item_and_those_of_what_it_needs_first(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        word = world.word
        first = await made(client, "task", f"Hand over {word}", world["track"])
        second = await made(client, "task", f"Needs the hand over {word}", world["track"])
        await ok(client, "add_link", source=second, target=first, link_type="needs_first")
        for n in range(2):
            await ok(client, "claim_node", node=first)
            await ok(client, "release_claim", node=first, note=f"Handed back {n} {word}")
        await ok(client, "claim_node", node=first)
        await ok(client, "finish_work", node=first, outcome="done", note=f"Finished {word}")
        server = await ok(client, "get_node", node=first)
        assert server["handoff_count"] == 3
        async with browser_page(world.board, token=world.project.key) as w:
            await see_page(w, first)
            page = w.page
            notes = page.locator("#item-page [data-part=handoff]")
            await expect(notes.locator("h3")).to_contain_text("Handoff notes", timeout=SLOW)
            await expect(notes.locator("h3 .heading-count")).to_have_text("3")
            items = notes.locator("li.handoff-item")
            assert await items.count() == 3
            texts = [cell_text(t) for t in await items.all_inner_texts()]
            assert [t.split(": ", 1)[1] for t in texts] == [
                f"Finished {word}",
                f"Handed back 1 {word}",
                f"Handed back 0 {word}",
            ]
            assert [await items.nth(n).get_attribute("data-outcome") for n in range(3)] == [
                "done",
                "released",
                "released",
            ]
            assert all(AGENT in t for t in texts)
            # The panel the notes sit in has room round its words: 16 pixels above and below,
            # 24 at each side.
            assert await notes.evaluate(
                """e => { const s = getComputedStyle(e);
                    return [s.paddingTop, s.paddingRight, s.paddingBottom, s.paddingLeft]; }"""
            ) == ["16px", "24px", "16px", "24px"]
            # Who made it is in its history, last, with the version it began at: the header has no
            # cell for it.
            made_by = page.locator("#item-page [data-part=history] li[data-action=create]")
            assert AGENT in cell_text(await made_by.inner_text())
            assert "Version 1" in cell_text(await made_by.inner_text())
            # A name opens the card of who it is, on hover (and on focus or a click), and has no
            # tooltip of its own; Escape closes the card.
            who = made_by.locator(".actor.has-card").first
            assert await who.get_attribute("title") is None
            await who.hover()
            card = page.locator(".hovercard:visible")
            await expect(card).to_contain_text("An agent: it works through its key", timeout=SLOW)
            await expect(card.locator(".actor-card-head")).to_contain_text(AGENT)
            await page.keyboard.press("Escape")
            await expect(card).to_have_count(0)

            # What it needs first is handed over too: the last note of each.
            await see_page(w, second)
            theirs = page.locator("#item-page [data-part=needs-handoff]")
            await expect(theirs).to_contain_text(f"Finished {word}", timeout=SLOW)
            await expect(theirs).to_contain_text("3 notes in all")

            # The Board card of the work that has a handoff note says so, with a sign and no word:
            # the words are the sign's label.
            await w.go("#/board")
            sign = page.locator(f".card[data-ref='{first}'] .handoff-sign")
            await expect(sign).to_have_attribute("aria-label", "Handoff", timeout=SLOW)
            assert w.problems == [], w.problems


async def test_start_and_target_are_chosen_in_vrums_calendar_and_saved_at_once(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        task = world["ship"]
        viewer = join_project(world.project, "viewer")
        async with browser_page(world.board, token=world.project.key) as w:
            await see_page(w, task)
            page = w.page
            summary = page.locator("#item-page .card-summary")
            start = summary.locator("button.date-value[data-field=start_on]")
            target = summary.locator("button.date-value[data-field=target_on]")
            calendar = page.locator(".dp")
            clear = calendar.locator("button.dp-clear")

            # A click on Start opens the calendar on today, with today ringed and nothing chosen.
            await expect(start).to_have_text("Not set", timeout=SLOW)
            await expect(target).to_have_text("Not set")
            await start.click()
            await expect(calendar).to_be_visible(timeout=SLOW)
            await expect(calendar).to_have_attribute("role", "dialog")
            await expect(calendar.locator("[aria-current=date]")).to_have_attribute(
                "data-day", day()
            )
            await expect(calendar.locator("[aria-selected=true]")).to_have_count(0)
            await expect(clear).to_be_disabled()
            # Picking a day closes it, puts the focus back on Start and saves at once: an agent
            # reads the new date through the tools.
            await calendar.locator(f"button.dp-day[data-day='{day(3)}']").click()
            await expect(calendar).to_have_count(0)
            await expect(start).to_have_text(day(3), timeout=SLOW)
            await expect(start).to_be_focused()
            assert (await ok(client, "get_node", node=task))["start_on"] == day(3)
            await read_again(page)

            # A target before the start is refused in the server's own words, and the date stays.
            version = (await ok(client, "get_node", node=task))["version"]
            said = await refused(
                client,
                "update_node",
                actor="x",
                node=task,
                expected_version=version,
                target_on=day(1),
            )
            await target.click()
            await calendar.locator(f"button.dp-day[data-day='{day(1)}']").click()
            problem = page.locator("#notices .notice.refusal .notice-text")
            await expect(problem).to_be_visible(timeout=SLOW)
            plain(cell_text(await problem.inner_text()), cell_text(said))
            await expect(target).to_have_text("Not set")
            assert (await ok(client, "get_node", node=task))["target_on"] is None

            # With the keyboard alone: Enter opens it, Escape closes it with nothing changed, and
            # the arrows and Enter pick (down is a week on).
            await target.focus()
            await page.keyboard.press("Enter")
            await expect(calendar).to_be_visible(timeout=SLOW)
            await expect(calendar.locator("button.dp-day[tabindex='0']")).to_be_focused()
            await page.keyboard.press("Escape")
            await expect(calendar).to_have_count(0)
            await expect(target).to_be_focused()
            await page.keyboard.press("Enter")
            await expect(calendar).to_be_visible(timeout=SLOW)
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            await expect(target).to_have_text(day(7), timeout=SLOW)
            assert (await ok(client, "get_node", node=task))["target_on"] == day(7)
            await read_again(page)

            # Clear takes the start away, and there is nothing to clear on a date that is not set.
            await start.click()
            await expect(calendar.locator("[aria-selected=true] button")).to_have_attribute(
                "data-day", day(3)
            )
            await clear.click()
            await expect(start).to_have_text("Not set", timeout=SLOW)
            assert (await ok(client, "get_node", node=task))["start_on"] is None
            await start.click()
            await expect(clear).to_be_disabled()
            await page.keyboard.press("Escape")
            assert w.problems == [], w.problems

        # Someone who can only look sees the dates as plain text: no button, no calendar.
        async with browser_page(world.board, token=None, person=viewer) as v:
            await see_page(v, task)
            seen = v.page.locator("#item-page .card-summary")
            await expect(seen.locator("dd.target")).to_have_text(day(7), timeout=SLOW)
            # A start that is not set is left out of the strip, for someone who cannot set it.
            await expect(seen.locator("dd.start")).to_have_count(0)
            await expect(seen.locator("button.date-value")).to_have_count(0)
            assert v.problems == [], v.problems


async def test_an_agent_adding_a_task_updates_the_open_page_and_a_draft_typed_there_survives(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        story, word = world["story"], world.word
        async with browser_page(world.board, token=world.project.key) as w:
            await see_page(w, story)
            page = w.page
            before = await item_rows(page)
            assert cell_text(await page.locator("#item-count").inner_text()) == "7"
            await open_editor(page, "#item-page")
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
            assert cell_text(await page.locator("#item-count").inner_text()) == "8"
            cells = await cells_of(page, added)
            assert cells["title"] == f"Added by an agent {word}" and cells["priority"] == "urgent"
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


# -- 8. a row opens its own page, and nothing opens beside the page ------------------------------


async def test_a_row_opens_the_tasks_own_page_by_its_link_or_a_click_and_ids_are_never_repeated(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        story, task, other = world["story"], world["copy"], world["deploy"]
        async with browser_page(world.board, token=world.project.key) as w:
            await see_page(w, story)
            page = w.page
            # A row has the link and the chips that change a status or a priority, and nothing
            # else beside it: there is no quick look.
            link = item_row(page, task).locator(".cell-item a")
            assert await link.get_attribute("href") == item_address(task, "board")
            assert await item_row(page, task).locator("button:not([data-menu])").count() == 0
            assert await page.locator(".quick-look, .gv-quick").count() == 0

            # A click on the row, away from its links and chips, opens the page in the same window.
            await item_row(page, task).locator(".cell-due").click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", task, timeout=SLOW
            )
            assert "from=board" in page.url and "sel=" not in page.url
            assert cell_text(await page.locator("#item-title").inner_text()) == (
                f"{code_of(task)} task 3 copy the words {world.word}"
            )
            await no_panel_or_drawer(page)
            # The page has one heading and no id twice, with its form open too.
            assert await page.locator("h1#item-title").count() == 1
            await open_editor(page, "#item-page")
            ids = await page.evaluate(
                "() => { const all = [...document.querySelectorAll('[id]')].map((e) => e.id);"
                " return all.filter((id, at) => all.indexOf(id) !== at); }"
            )
            assert ids == [], ids
            assert (
                await page.locator("#item-details #edit-title").input_value()
                == ((await ok(client, "get_node", node=task))["title"])
            )

            # The way back is the story's page with its table; another row's link goes to its own.
            await page.go_back()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", story, timeout=SLOW
            )
            await expect(item_row(page, other)).to_be_visible(timeout=SLOW)
            await item_row(page, other).locator(".cell-title a").click()
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", other, timeout=SLOW
            )
            assert await page.locator("#item-page").get_attribute("data-kind") == "task"
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
            assert await region.get_attribute("aria-label") == (
                f"Tasks in {await item_name(client, story)}"
            )
            await frames(page)

            # Tab reaches the first column header; Enter sorts, and the focus stays on it.
            await tab_to(page, "#item-table button.sort[data-sort=item]")
            await tab_to(page, "#item-table button.sort[data-sort=title]")
            await page.keyboard.press("Enter")
            await expect(page.locator("#item-table th.col-title")).to_have_attribute(
                "aria-sort", "ascending", timeout=SLOW
            )
            assert await item_rows(page) == sorted_as_shown(tasks, *TASK_COLUMNS["title"], False)
            assert await page.evaluate("document.activeElement.dataset.sort") == "title"
            await page.keyboard.press("Enter")
            await expect(page.locator("#item-table th.col-title")).to_have_attribute(
                "aria-sort", "descending", timeout=SLOW
            )
            assert await item_rows(page) == sorted_as_shown(tasks, *TASK_COLUMNS["title"], True)
            assert await page.evaluate("document.activeElement.dataset.sort") == "title"
            # Space does the same on the next header.
            await page.keyboard.press("Tab")
            assert await page.evaluate("document.activeElement.dataset.sort") == "status"
            await page.keyboard.press("Space")
            await expect(page.locator("#item-table th.col-status")).to_have_attribute(
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
            # ... and the pager, by its own keys: ] goes to the next story of the epic and [ to the
            # one before; the button that was used keeps the focus, so Enter carries on. (The page
            # puts the focus on its title once its details are in: wait for that first.)
            await expect(page.locator("#item-details .card-detail")).to_be_visible(timeout=SLOW)
            await expect(page.locator("#item-title")).to_be_focused(timeout=SLOW)
            await frames(page)
            stories = [story, world["pay"], world["track"]]
            assert [s["ref"] for s in await children_of(client, world["epic"])] == stories
            await expect(page.locator("#item-position")).to_have_text("1 of 3")
            assert await page.locator("#item-prev").is_disabled()
            await page.keyboard.press("]")
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", stories[1], timeout=SLOW
            )
            await expect(page.locator("#item-position")).to_have_text("2 of 3", timeout=SLOW)
            await expect(page.locator("#item-next")).to_be_focused(timeout=SLOW)
            await page.keyboard.press("Enter")
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", stories[2], timeout=SLOW
            )
            await expect(page.locator("#item-position")).to_have_text("3 of 3", timeout=SLOW)
            assert await page.locator("#item-next").is_disabled()
            await frames(page)
            await page.keyboard.press("]")  # nothing after the last one
            await page.keyboard.press("[")
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", stories[1], timeout=SLOW
            )
            assert w.problems == [], w.problems


# -- 9b. one strip, no list of sections, the Links of an item, and Similar when there is any ------

GONE_FROM_THE_PAGE = (
    ".item-nav, .item-jump, [data-jump], #item-what, #item-part, .item-part, .card-summary .chips"
)


async def test_every_kind_of_page_has_one_strip_of_what_it_has_and_no_list_of_sections(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        rule = (await ok(client, "list_rules", node=world["story"]))["items"][0]
        notes = (await ok(client, "list_notes", node=world["story"]))["items"]
        decided = next(n for n in notes if n["decided_by"])
        undecided = next(n for n in notes if not n["decided_by"])
        # What each page says about itself in its one strip, in order, and nothing more: no
        # "created by", "last changed", version, "waiting for" or dates row. A story that adds up
        # sized tasks shows points, one with only unsized tasks shows its progress, a task its own
        # size and who holds it; an acceptance criterion its status alone; a note its status and,
        # when somebody decided it, who.
        wanted = [
            (world["epic"], ["Status", "Priority", "Points", "Start", "Target"]),
            (world["story"], ["Status", "Priority", "Points", "Start", "Target"]),
            (world["track"], ["Status", "Priority", "Progress", "Start", "Target"]),
            (world["form"], ["Status", "Priority", "Size", "Start", "Target", "Held by"]),
            (f"rule:{rule['id']}", ["Status"]),
            (f"note:{decided['id']}", ["Status", "Decided by"]),
            (f"note:{undecided['id']}", ["Status"]),
        ]
        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page
            for ref, labels in wanted:
                await see_page(w, ref)
                assert await page.locator("#item-page dl.state-strip").count() == 1, ref
                assert await strip_labels(page) == labels, ref
                # No list of the sections at the top, no line under the title, no card above the
                # details: the only navigation on the page is the pager of work.
                assert await page.locator(GONE_FROM_THE_PAGE).count() == 0, ref
                assert await page.locator("#item-page nav:not(.item-pager)").count() == 0, ref
            # A criterion and a note are named by their own codes, in the heading and in the path.
            await see_page(w, f"rule:{rule['id']}")
            assert (await page.locator("#item-title").inner_text()).startswith(f"AC-{rule['id']} ")
            await see_page(w, f"note:{decided['id']}")
            assert (await page.locator("#item-title").inner_text()).startswith(
                f"N-{decided['id']} "
            )
            assert (await crumbs(page))[-1] == await item_name(client, f"note:{decided['id']}")
            assert w.problems == [], w.problems


async def test_a_tasks_links_are_listed_both_ways_removed_in_the_row_and_added_in_a_dialog(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        task, form, polish, pay = world["api"], world["form"], world["polish"], world["pay"]
        viewer = join_project(world.project, "viewer")

        async def links_on_server() -> set[tuple[str, str, str]]:
            found = (await ok(client, "list_links", node=task))["items"]
            return {(one["source"], one["target"], one["link_type"]) for one in found}

        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page
            await see_page(w, task)
            rows = page.locator("#links-table tbody tr")
            await expect(rows).to_have_count(2, timeout=SLOW)
            # Both ways: what it needs first, then what needs it, each with its other end's state.
            assert await rows.evaluate_all(
                "els => els.map(e => [e.dataset.type, e.dataset.direction, e.dataset.ref])"
            ) == [["needs_first", "out", form], ["needs_first", "in", polish]]
            assert await links_on_server() == {
                (task, form, "needs_first"),
                (polish, task, "needs_first"),
            }
            assert await rows.locator(".cell-status .chip").count() == 2
            # There is no form on the page: adding is a button, and removing a button in the row.
            assert await page.locator("form.add-link, #link-target, #link-relation").count() == 0
            remove = page.locator(f"#links-table tr[data-ref='{polish}'] button.remove-link")
            polish_title = (await ok(client, "get_node", node=polish))["title"]
            await expect(remove).to_have_attribute(
                "aria-label", f"Remove the link: is needed first by “{polish_title}”"
            )
            await remove.click()
            await expect(rows).to_have_count(1, timeout=SLOW)
            assert await links_on_server() == {(task, form, "needs_first")}
            assert cell_text(await page.locator("#links-count").inner_text()) == "1"

            # "Add link…" opens a dialog that decides nothing for the person: the way the items
            # are linked is empty ("Choose how it is linked"), the other item's box has the focus
            # and no list of suggestions, and nothing is open. It is named by the item's name.
            await page.get_by_role("button", name="Add link…").click()
            box = page.locator("dialog.modal")
            title = (await ok(client, "get_node", node=task))["title"]
            await expect(box).to_contain_text(f"Add a link to “{title}”", timeout=SLOW)
            relation = box.locator("#link-relation")
            assert await relation.input_value() == ""
            assert (
                cell_text(await relation.locator("option:checked").inner_text())
                == "Choose how it is linked"
            )
            assert await box.locator("label[for=link-relation]").inner_text() == "How it is linked"
            await expect(box.locator("#link-target")).to_be_focused()
            assert await box.locator("#link-target").get_attribute("list") is None
            await nothing_open(page)
            # Sending it with nothing chosen is refused in words, in the dialog, and keeps what was
            # typed; once the person types, the other item's suggestions are offered.
            await box.locator("#link-target").fill(code_of(polish))
            assert await box.locator("#link-target").get_attribute("list") == "link-options"
            await box.get_by_role("button", name="Add the link").click()
            problem = box.locator(".problem-text")
            await expect(problem).to_have_text("Choose how it is linked.", timeout=SLOW)
            assert await box.locator("#link-target").input_value() == code_of(polish)
            assert await links_on_server() == {(task, form, "needs_first")}
            # What the server refuses is said in the dialog too, which stays open.
            await relation.select_option("needs_first:out")
            await box.locator("#link-target").fill(code_of(task))
            said = await refused(
                client, "add_link", source=task, target=task, link_type="needs_first"
            )
            await box.get_by_role("button", name="Add the link").click()
            await expect(problem).not_to_have_text("Choose how it is linked.", timeout=SLOW)
            plain(cell_text(await problem.inner_text()), cell_text(said))
            assert await box.locator("#link-target").input_value() == code_of(task)
            assert await links_on_server() == {(task, form, "needs_first")}
            # Put right, it is added, the dialog closes and the table has it, in its group.
            await relation.select_option("needs_first:in")
            await box.locator("#link-target").fill(code_of(polish))
            await box.get_by_role("button", name="Add the link").click()
            await expect(box).to_have_count(0, timeout=SLOW)
            await expect(rows).to_have_count(2, timeout=SLOW)
            assert await item_refs_of_links(page) == [form, polish]
            assert await links_on_server() == {
                (task, form, "needs_first"),
                (polish, task, "needs_first"),
            }

            # With no link at all the section is its heading and the way to add one.
            await see_page(w, pay)
            await expect(page.locator("#item-links")).to_be_visible(timeout=SLOW)
            assert await page.locator("#add-link").count() == 1
            assert await page.locator("#links-table").count() == 0
            assert cell_text(await page.locator("#links-count").inner_text()) == ""
            assert w.problems == [], w.problems

        # Someone who can only look reads the links, and has neither Add nor Remove; with none to
        # read, the section is not there at all.
        async with browser_page(world.board, token=None, person=viewer) as v:
            await see_page(v, task)
            await expect(v.page.locator("#links-table tbody tr")).to_have_count(2, timeout=SLOW)
            assert await v.page.locator("#add-link, button.remove-link, th.col-remove").count() == 0
            await see_page(v, pay)
            await expect(v.page.locator("#item-links")).to_be_hidden()
            assert v.problems == [], v.problems


async def item_refs_of_links(page: Page) -> list[str]:
    """The other ends of the links in the Links section of the page, in the order drawn."""
    refs: list[str] = await page.locator("#links-table tbody tr").evaluate_all(
        "els => els.map(e => e.dataset.ref)"
    )
    return refs


async def test_similar_is_a_section_only_when_something_is_alike_and_leaves_out_what_is_shown(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic_a, _, (first,), (second,) = await twin_epics(client, [DUPLICATES[0]])
        lone = await made(client, "story", f"Quartz kiln {unique_word()}", epic_a)
        await reindexed(client)
        server = await ok(client, "similar_items", node=first, limit=8)
        alike = {one["ref"] for one in server["items"]}
        assert second in alike and lone not in alike
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await open_item(w, first)
            similar = part(page, "similar")
            await expect(similar).to_be_visible(timeout=SLOW)
            shown = await similar.locator("li.similar-item").evaluate_all(
                "els => els.map(e => e.dataset.similar)"
            )
            # Every suggestion is one the server gives, the twin is the first, each is a link to
            # its page and says how alike it is, and the heading counts them.
            assert second in shown and set(shown) <= alike
            assert shown[0] == second
            assert await similar.get_attribute("data-count") == str(len(shown))
            await expect(similar.locator("h3 .heading-count")).to_have_text(str(len(shown)))
            row = similar.locator(f"li.similar-item[data-similar='{second}']")
            await expect(row).to_contain_text("alike")
            link = row.locator("a.item-link")
            assert await link.get_attribute("href") == item_address(second, "board")
            # Its words are the twin's code and name, and its tooltip says the same.
            assert cell_text(await link.inner_text()) == await item_name(client, second)
            assert await link.get_attribute("title") == await item_name(client, second)
            # Once the twin is linked the page shows it already, so it is not suggested: with
            # nothing else alike the section goes, whole, and the page follows the news.
            await ok(
                client,
                "add_link",
                actor="agent-1",
                source=first,
                target=second,
                link_type="duplicates",
            )
            await expect(part(page, "similar")).to_have_count(0, timeout=SLOW)
            await expect(part(page, "links")).to_be_visible()
            # A story that nothing is alike to never had one.
            await open_item(w, lone)
            assert await part(page, "similar").count() == 0
            assert w.problems == [], w.problems


# -- 9c. forms open empty, what the page says it did names the item, no number words ------------

# What the pages never write any more: "#3", "story 12", "criterion 3", "note 7", "story:12".
OLD_WORDS = (
    r"(?<![\w&])#\d+\b|\b(?:story|task|epic|note|criterion|rule) \d+\b"
    r"|\b(?:epic|story|task|rule|note):\d+\b"
)
SCAN = """(pattern) => {
    const bad = new RegExp(pattern, 'i');
    const found = [];
    for (const line of document.body.innerText.split('\\n')) {
        if (bad.test(line)) found.push('text: ' + line.trim());
    }
    for (const e of document.querySelectorAll('[title], [aria-label], [placeholder]')) {
        for (const name of ['title', 'aria-label', 'placeholder']) {
            const value = e.getAttribute(name);
            if (value && bad.test(value)) found.push(name + ': ' + value);
        }
    }
    for (const e of document.querySelectorAll('datalist option')) {
        if (e.value && bad.test(e.value)) found.push('suggestion: ' + e.value);
    }
    for (const e of document.querySelectorAll('select option')) {
        if (bad.test(e.textContent)) found.push('option: ' + e.textContent);
    }
    return found;
}"""


async def no_old_words(page: Page, where: str) -> None:
    """Nothing the person can read on the page (its text, tooltips, labels, placeholders and the
    options of a list) writes an item as '#3', 'story 12', 'criterion 3' or 'story:12'."""
    found = await page.evaluate(SCAN, OLD_WORDS)
    assert found == [], (where, found)


async def test_the_move_form_starts_empty_refuses_a_missing_choice_and_says_the_move_by_name(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, client):
        pay, announce, other = world["pay"], world["announce"], world["empty_epic"]
        pay_title = (await ok(client, "get_node", node=pay))["title"]
        other_title = (await ok(client, "get_node", node=other))["title"]
        async with browser_page(world.board, token=world.project.key) as w:
            page = w.page
            move = page.locator("#item-page form.move")
            choice = move.locator("#move-parent")
            # A story is moved to an epic and a task to a story, and in both the form has chosen
            # nothing: where the item is now is the path above the page, not the answer.
            for ref, label, words in (
                (pay, "Move to epic", "Choose an epic"),
                (announce, "Move to story", "Choose a story"),
            ):
                await see_page(w, ref)
                parent = (await ok(client, "get_node", node=ref))["parent"]
                assert await move.locator("label").inner_text() == label
                assert await choice.input_value() == ""
                assert cell_text(await choice.locator("option:checked").inner_text()) == words
                await nothing_open(page, "#item-page form.move")
                await move.get_by_role("button", name="Move", exact=True).click()
                await expect(move.locator(".problem-text")).to_have_text(f"{words}.", timeout=SLOW)
                assert (await ok(client, "get_node", node=ref))["parent"] == parent
            # A move that is chosen is said by the names of the item and of where it went, and the
            # form is empty again afterwards.
            await see_page(w, pay)
            await choice.select_option(value=other)
            await move.get_by_role("button", name="Move", exact=True).click()
            said = page.locator("#notices .notice .notice-text")
            expected = f"Moved “{pay_title}” under “{other_title}”."
            await expect(said.filter(has_text=expected)).to_have_text(expected, timeout=SLOW)
            assert (await ok(client, "get_node", node=pay))["parent"] == other
            await expect(choice).to_have_value("", timeout=SLOW)
            assert (await crumbs(page))[1] == await item_name(client, other)
            assert w.problems == [], w.problems


async def test_what_the_page_says_it_did_names_the_item_in_curly_quotes_and_never_by_a_code(
    server_url: str,
) -> None:
    async with ordering(server_url) as (world, _):
        story, word = world["story"], world.word
        title = f"Place an order, said by name {word}"
        async with browser_page(world.board, token=world.project.key) as w:
            await see_page(w, story)
            page = w.page
            said = page.locator("#notices .notice .notice-text")

            async def says(sentence: str) -> None:
                await expect(said.filter(has_text=sentence)).to_have_text(sentence, timeout=SLOW)
                for shown in await said.all_inner_texts():
                    assert not re.search(OLD_WORDS + r"|\b(?:AC|[ESTN])-\d+\b", shown), shown

            await open_editor(page, "#item-page")
            await page.locator("#edit-title").fill(title)
            await page.get_by_role("button", name="Save changes").click()
            await says(f"Saved “{title}”.")
            await expect(page.locator("h1#item-title")).to_contain_text("said by name")

            # An acceptance criterion put on it: the dialog says whose it is by name, and so does
            # the notice.
            await page.locator("#item-page button.add-requirement").click()
            box = page.locator("dialog.modal")
            await expect(box.locator("#requirement-on")).to_have_text(
                f"It goes on “{title}”.", timeout=SLOW
            )
            await box.locator("#requirement-title").fill(f"Quick {word}")
            await box.get_by_role("button", name="Add the acceptance criterion").click()
            await says(f"Added the acceptance criterion “Quick {word}”.")

            # A note about it.
            await page.locator("#item-page button.add-note").click()
            await page.locator("dialog.modal #add-note-text").fill(f"Orders are tiny {word}")
            await page.locator("dialog.modal #add-note-why").fill("Counted last week")
            await page.locator("dialog.modal").get_by_role("button", name="Add the note").click()
            await says(
                f"Added the note “Orders are tiny {word}”. It is unconfirmed until a person "
                "confirms it."
            )
            assert w.problems == [], w.problems


async def dialog_opens_empty(page: Page, opener: str, where: str) -> None:
    """Press the button that opens a dialog and check how it starts: nothing is open in it (no
    list, menu, calendar or fold), the focus is in a text box and never on a select, nothing the
    person can read writes an item as '#3' or 'story 12', and, once they press Down in its first
    text box, whatever list it has opens and writes none either. Escape closes it (the first
    one only closes a list that Down opened), or Cancel does."""
    await page.locator(opener).click()
    await expect(page.locator("dialog.modal")).to_be_visible(timeout=SLOW)
    await nothing_open(page)
    focused = await page.evaluate("() => document.activeElement.tagName.toLowerCase()")
    assert focused in ("input", "textarea"), (where, focused)
    await no_old_words(page, where)
    await page.locator("dialog.modal input[type=text]").first.press("ArrowDown")
    await no_old_words(page, f"{where}, its list open")
    await page.keyboard.press("Escape")
    box = page.locator("dialog.modal")
    try:
        await expect(box).to_have_count(0, timeout=1500)
    except AssertionError:
        await box.get_by_role("button", name="Cancel").click()
        await expect(box).to_have_count(0)


async def test_item_page_dialogs_open_empty_and_nothing_writes_a_number_the_old_way(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        word = unique_word()
        epic = await made(client, "epic", f"Platform {word}")
        story = await made(client, "story", f"Slot picker {word}", epic, body=f"See [[{epic}]].")
        task = await made(client, "task", f"Write the picker {word}", story)
        rule = await ok(client, "create_rule", title=f"A rider picks a slot {word}", node=story)
        note = await make_note(client, f"Slots are short {word}", "Measured", nodes=[story])
        await ok(client, "claim_node", node=task)
        refs = [epic, story, task, f"rule:{rule['id']}", f"note:{note['id']}"]
        async with browser_page(board, token=project.key) as w:
            page = w.page
            for ref in refs:
                await open_item(w, ref)
                await no_old_words(page, ref)

            # The forms and dialogs of a story's page.
            await open_item(w, story)
            await open_editor(page, "#item-page")
            await page.locator("#edit-body").click()
            await page.keyboard.type("[[slot")
            await expect(page.locator(".ref-picker")).to_be_visible(timeout=SLOW)
            await no_old_words(page, "the [[ list")
            await page.keyboard.press("Escape")
            await page.get_by_role("button", name="Cancel").click()
            for opener, name in (
                ("#add-link", "Add link"),
                ("#item-page button.add-note", "Add a note"),
                ("#item-page button.add-requirement", "Add an acceptance criterion"),
            ):
                await dialog_opens_empty(page, opener, name)

            # A criterion's page, with the dialog that puts it somewhere else, and a note's page
            # with the dialogs that replace and withdraw it.
            await open_item(w, f"rule:{rule['id']}")
            await dialog_opens_empty(page, "button.place-requirement", "place the criterion")
            await open_item(w, f"note:{note['id']}")
            for opener in ("button.supersede", "button.withdraw", "button.change-concerns"):
                await dialog_opens_empty(page, opener, opener)
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


async def narrow_audit(w: Watched, world: World, scheme: str, client: Client) -> None:
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
        # What can be reached can be used: the pager's buttons, a column's sort button and a whole
        # row (which opens the page when it is pressed) are real targets (24 pixels, as the layout
        # tests of M17 count it, WCAG 2.5.8).
        row = item_row(page, world["ship" if name == "story" else "pay"])
        targets = [
            page.locator("#item-next"),
            page.locator("#item-prev"),
            page.locator("#item-table button.sort").first,
            row,
        ]
        for target in targets:
            box = await target.bounding_box()
            assert box and box["width"] >= 24 and box["height"] >= 24, (where, box)
        assert await page.locator(".quick-look").count() == 0, where
        # Below 1500 pixels the pager keeps to the code (the name is in its label), so that it
        # fits beside the buttons.
        assert await page.locator("#item-next .pager-name").is_hidden(), where
    # An acceptance criterion's header keeps its buttons on one row: the button that moves it
    # keeps to its first word, and its full words are its name for a screen reader.
    rule = (await ok(client, "list_rules", node=world["story"]))["items"][0]
    await see_page(w, f"rule:{rule['id']}")
    place = page.locator("button.place-requirement")
    await expect(place.locator(".short-label")).to_be_visible()
    await expect(place.locator(".long-label")).to_be_hidden()
    assert await place.get_attribute("aria-label") == "Move to another story or task…"
    tops = await page.locator("#item-page .item-actions button").evaluate_all(
        "els => els.map(e => Math.round(e.getBoundingClientRect().top))"
    )
    assert len(tops) == 3 and len(set(tops)) == 1, (scheme, tops)
    found = await page.evaluate(OVERFLOW)
    assert found["doc"] <= found["width"] and found["offenders"] == [], (scheme, found)
    assert w.problems == [], w.problems
    assert await policy_violations(page) == []


@pytest.mark.parametrize("scheme", ["light", "dark"])
async def test_at_390_pixels_in_both_themes_the_page_does_not_scroll_and_the_table_does(
    server_url: str, scheme: str
) -> None:
    async with (
        ordering(server_url, long_title=True) as (world, client),
        themed_page(world.board, signed_in=True, scheme=scheme, viewport=NARROW) as w,
    ):
        await narrow_audit(w, world, scheme, client)
