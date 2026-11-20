"""M11's Roadmap against the running containers, driven in a real headless browser and checked
against a real MCP client (see ./check.sh). Bars are checked at the pixels their dates give, every
change a person makes is read back through get_node and node_history, and refusals are compared
word for word with what the same change gets through MCP. Every filter starts with
`epic:<number>` so that a test sees only the items it made. Items are named by their refs
(`task:40`): the bars and labels carry them as `data-ref`, and the page writes them as
codes (T-40)."""

import datetime
import re
from collections import Counter
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_helpers import (
    SLOW,
    api_path,
    apply_filter,
    bar_of,
    board_url,
    browser_page,
    crumbs,
    database_footprint,
    hold_back_news,
    item_page,
    made,
    no_panel_or_drawer,
    on,
    on_page,
    open_editor,
    open_item,
    plain,
    signed_in_name,
    timeline,
)
from tests.board_helpers import drag_bar as drag
from tests.board_helpers import roadmap_ids as shown
from tests.helpers import (
    all_nodes,
    day,
    make_note,
    ok,
    refused,
    reindexed,
    the_token,
    unique_word,
)
from tests.helpers import code_of as code
from tests.helpers import number_of as number

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

# The name of the agent key the tests use: who the history says made a change through MCP.
AGENT = "tests"


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


async def hash_of(page: Page) -> str:
    """The address after the #, as the board holds it now."""
    return str(await page.evaluate("location.hash"))


async def updates(client: Client, node: str) -> list[dict[str, Any]]:
    history = await ok(client, "node_history", node=node)
    return [event for event in history["items"] if event["action"] == "update"]


async def dates(client: Client, node: str) -> tuple[str | None, str | None]:
    found = await ok(client, "get_node", node=node)
    return found["start_on"], found["target_on"]


async def window(client: Client) -> tuple[str, str, str, str]:
    """An epic, a story and two tasks, to be given dates. The refs come back as one tuple."""
    word = unique_word()
    epic = await made(client, "epic", f"Plan {word}")
    story = await made(client, "story", f"Part {word}", epic)
    first = await made(client, "task", f"First {word}", story, start_on=day(3), target_on=day(12))
    second = await made(client, "task", f"Second {word}", story, start_on=day(4), target_on=day(14))
    return epic, story, first, second


# -- 4. the bars are where the dates say -----------------------------------------------------------


async def test_the_roadmap_draws_only_dated_work_spans_arrows_marks_and_filters_like_mcp(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Roadmap {word}")
    story = await made(client, "story", f"Delivery {word}", epic)
    dated = {
        "a": (5, 12),  # the prerequisite of b, due after b starts
        "b": (8, 15),  # a schedule conflict
        "c": (-20, -10),  # overdue
        "d": (20, 30),
        "e": (25, 26),
        "f": (-3, 1),
    }
    ids: dict[str, str] = {}
    for name, (start, end) in dated.items():
        ids[name] = await made(
            client,
            "task",
            f"Task {name} {word}",
            story,
            start_on=day(start),
            target_on=day(end),
        )
    ids["u1"] = await made(client, "task", f"Undated one {word}", story)
    ids["u2"] = await made(client, "task", f"Undated two {word}", story)
    await ok(
        client,
        "add_link",
        actor="a",
        source=ids["b"],
        target=ids["a"],
        link_type="needs_first",
    )

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{number(epic)}")
        # The two items with no dates are not on the timeline, and the count says so, with the way
        # to them in the Lists; there is no tray for them.
        assert await shown(page, 8) == {epic, story, *(ids[name] for name in dated)}
        for undated in (ids["u1"], ids["u2"]):
            assert await page.locator(f"#rm-scroll [data-ref='{undated}']").count() == 0
        assert await page.locator("#tray").count() == 0
        count = " ".join((await page.locator("#count").inner_text()).split())
        assert count.endswith("2 have no dates and are not drawn: see them in Lists."), count
        await page.locator("#rm-undated").click()
        await expect(page.locator(".lists-page")).to_be_visible(timeout=SLOW)
        assert "no:start" in (await page.evaluate("location.hash")).replace("+", " ")
        await w.open(f"#/roadmap?q=epic:{number(epic)}")
        await shown(page, 8)
        line = await timeline(page)
        assert line["today"] == on(day(0)) and line["ppd"] == 22

        # Each bar is at the pixels its dates give: where it starts and how long it is.
        for name, (start, end) in dated.items():
            el = bar_of(page, ids[name])
            assert await el.get_attribute("data-start") == day(start)
            assert await el.get_attribute("data-end") == day(end)
            assert await el.get_attribute("data-worked-out") == "false"
            left = await el.evaluate("e => parseFloat(e.style.left)")
            width = await el.evaluate("e => parseFloat(e.style.width)")
            assert abs(left - (on(day(start)) - line["origin"]).days * line["ppd"]) < 1.5, name
            assert abs(width - (end - start + 1) * line["ppd"]) < 1.5, name

        # The epic and the story have no dates of their own: their bars are worked out from the
        # tasks under them, and are marked so in words.
        first, last = min(s for s, _ in dated.values()), max(e for _, e in dated.values())
        for container in (epic, story):
            el = bar_of(page, container)
            assert await el.get_attribute("data-worked-out") == "true"
            assert await el.get_attribute("data-start") == day(first)
            assert await el.get_attribute("data-end") == day(last)
            assert "Worked out from what is under it" in (
                await el.get_attribute("aria-label") or ""
            )

        # The arrow runs from what is needed first to the item that waits, in a warning colour.
        arrows = page.locator(".rm-deps path.dep")
        assert await arrows.count() == 1
        arrow = arrows.first
        assert await arrow.get_attribute("data-from") == ids["a"]
        assert await arrow.get_attribute("data-to") == ids["b"]
        assert "conflict" in (await arrow.get_attribute("class") or "")

        # The schedule conflict is on the item that starts too early, and only there; the overdue
        # mark is on the past item, and only there. Both are in words.
        conflict = page.locator(".rm-label .chip.conflict")
        assert await conflict.count() == 1
        await expect(
            page.locator(f".rm-label[data-ref='{ids['b']}'] .chip.conflict")
        ).to_contain_text("Schedule conflict")
        overdue = page.locator(".rm-label .chip.overdue")
        assert await overdue.count() == 1
        await expect(
            page.locator(f".rm-label[data-ref='{ids['c']}'] .chip.overdue")
        ).to_contain_text("Overdue")
        assert "Schedule conflict" in (
            await bar_of(page, ids["b"]).get_attribute("aria-label") or ""
        )
        assert "10 days late" in (await bar_of(page, ids["c"]).get_attribute("aria-label") or "")
        # Late work has a tail from the end of its bar through today, a cell for each day it is
        # late, and only the past item has one.
        tails = page.locator(".rm-rows .rm-late")
        assert await tails.count() == 1
        assert await tails.first.get_attribute("data-ref") == ids["c"]
        c_bar = bar_of(page, ids["c"])
        c_end = await c_bar.evaluate("e => parseFloat(e.style.left) + parseFloat(e.style.width)")
        assert await tails.first.evaluate("e => parseFloat(e.style.left)") == c_end
        tail_width = await tails.first.evaluate("e => parseFloat(e.style.width)")
        assert tail_width == 10 * line["ppd"]
        todays_left = (on(day(0)) - line["origin"]).days * line["ppd"]
        assert c_end + tail_width == todays_left + line["ppd"]  # through the end of today's cell
        await expect(page.locator(".rm-note.overdue")).to_have_text("10 days late")
        # What the page marks is what the graph says.
        graph = {n["ref"]: n for n in await all_nodes(client, query=f"epic:{number(epic)}")}
        assert graph[ids["b"]]["schedule_conflicts"] == [ids["a"]]
        assert graph[ids["c"]]["overdue"] is True
        assert [ref for ref, n in graph.items() if n["overdue"]] == [ids["c"]]

        # A date filter gives the same refs as the same text through MCP, leaving out what has no
        # dates, which the Roadmap does not draw.
        epic_n = number(epic)
        for text, expected in (
            (f"epic:{epic_n} target:{day(-30)}..{day(16)}", 4),
            (f"epic:{epic_n} is:overdue", 1),
            (f"epic:{epic_n} target:none kind:task", 0),
            (f"epic:{epic_n} start:{day(0)}..{day(30)} -kind:epic", 4),
        ):
            found = await all_nodes(client, query=text)
            wanted = {n["ref"] for n in found if n["span_start"] or n["span_end"]}
            assert len(wanted) == expected, (text, wanted)
            await apply_filter(page, text)
            assert await shown(page, expected) == wanted, text
        assert w.problems == [], w.problems


# -- 5. changing dates by dragging -----------------------------------------------------------------


async def test_dragging_a_bar_or_an_end_changes_the_dates_as_a_versioned_change_by_the_person(
    client: Client, board: str
) -> None:
    epic, story, moved, ended = await window(client)
    started = await made(client, "task", "Started late", story, start_on=day(6), target_on=day(20))
    actor = signed_in_name(board)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{number(epic)}")
        await shown(page, 5)

        # Both dates move together.
        await drag(page, moved, 5)
        await expect(bar_of(page, moved)).to_have_attribute("data-start", day(8), timeout=SLOW)
        assert await dates(client, moved) == (day(8), day(17))
        (change,) = await updates(client, moved)
        assert (change["actor"], change["before"], change["after"]) == (
            actor,
            {"start_on": day(3), "target_on": day(12), "version": 1},
            {"start_on": day(8), "target_on": day(17), "version": 2},
        )
        await expect(page.locator(".notice.done").last).to_contain_text(f"Moved {code(moved)}")

        # The right end moves the target date alone, and the left end the start date alone.
        await drag(page, ended, -3, "end")
        await expect(bar_of(page, ended)).to_have_attribute("data-end", day(11), timeout=SLOW)
        assert await dates(client, ended) == (day(4), day(11))
        (change,) = await updates(client, ended)
        assert (change["actor"], change["before"], change["after"]) == (
            actor,
            {"target_on": day(14), "version": 1},
            {"target_on": day(11), "version": 2},
        )
        await drag(page, started, 2, "start")
        await expect(bar_of(page, started)).to_have_attribute("data-start", day(8), timeout=SLOW)
        assert await dates(client, started) == (day(8), day(20))
        (change,) = await updates(client, started)
        assert (change["after"], change["actor"]) == (
            {"start_on": day(8), "version": 2},
            actor,
        )

        # The epic and story bars follow the new dates, and cannot be dragged (they are worked out).
        await expect(bar_of(page, epic)).to_have_attribute("data-start", day(4))
        await expect(bar_of(page, epic)).to_have_attribute("data-end", day(20))
        before = await dates(client, story)
        await drag(page, story, 4)
        await page.wait_for_timeout(500)
        assert await dates(client, story) == before == (None, None)
        assert w.problems == [], w.problems


async def test_a_bar_an_agent_changed_meanwhile_is_refused_and_a_start_after_the_target_is_refused(
    client: Client, board: str
) -> None:
    epic, _, stale, plain_one = await window(client)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{number(epic)}")
        await shown(page, 4)

        # An agent moves a bar that the board has not heard of: dragging it is refused, with the
        # words MCP uses, and the bar shows what the graph holds.
        await hold_back_news(page)
        await ok(
            client,
            "update_node",
            actor="agent-1",
            node=stale,
            expected_version=1,
            start_on=day(1),
            target_on=day(2),
        )
        said = await refused(
            client,
            "update_node",
            actor="x",
            node=stale,
            expected_version=1,
            start_on=day(8),
            target_on=day(17),
        )
        assert "version 2" in said and "version 1" in said
        await drag(page, stale, 5)
        refusal = page.locator(".notice.refusal .notice-text").first
        await expect(refusal).to_be_visible(timeout=SLOW)
        plain(await refusal.inner_text(), said)
        await expect(bar_of(page, stale)).to_have_attribute("data-start", day(1), timeout=SLOW)
        assert await dates(client, stale) == (day(1), day(2))
        assert [e["actor"] for e in await updates(client, stale)] == [AGENT]

        # A left end dragged past the target date: the server refuses, in its own words.
        said = await refused(
            client,
            "update_node",
            actor="x",
            node=plain_one,
            expected_version=1,
            start_on=day(24),
        )
        await drag(page, plain_one, 20, "start")
        late = page.locator(".notice.refusal .notice-text", has_text="Work cannot start after")
        await expect(late).to_be_visible(timeout=SLOW)
        plain(await late.inner_text(), said)
        await expect(bar_of(page, plain_one)).to_have_attribute("data-start", day(4), timeout=SLOW)
        assert await dates(client, plain_one) == (day(4), day(14))
        assert await updates(client, plain_one) == []
        assert w.problems == [], w.problems


# -- 5. the keyboard, several bars at once, zoom and groups -------------------------------------


async def test_keys_shift_dates_by_a_day_or_a_week_and_chosen_bars_move_together_in_one_request(
    client: Client, board: str
) -> None:
    epic, _, first, second = await window(client)
    actor = signed_in_name(board)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{number(epic)}")
        await shown(page, 4)
        keyed = bar_of(page, first)

        # Three presses add up and are sent as one change.
        await keyed.focus()
        for _ in range(3):
            await page.keyboard.press("ArrowRight")
        await expect(keyed).to_have_attribute("data-start", day(6), timeout=SLOW)
        assert await dates(client, first) == (day(6), day(15))
        (change,) = await updates(client, first)
        assert (change["actor"], change["after"]["version"]) == (actor, 2)

        # Shift is a week, Alt moves the start date only and Control the target date only.
        await keyed.focus()
        await page.keyboard.press("Shift+ArrowLeft")
        await expect(keyed).to_have_attribute("data-start", day(-1), timeout=SLOW)
        assert await dates(client, first) == (day(-1), day(8))
        await keyed.focus()
        await page.keyboard.press("Alt+ArrowRight")
        await expect(keyed).to_have_attribute("data-start", day(0), timeout=SLOW)
        assert await dates(client, first) == (day(0), day(8))
        await keyed.focus()
        await page.keyboard.press("Control+ArrowRight")
        await expect(keyed).to_have_attribute("data-end", day(9), timeout=SLOW)
        assert await dates(client, first) == (day(0), day(9))
        history = await updates(client, first)
        assert [e["after"]["version"] for e in history] == [2, 3, 4, 5]
        assert {e["actor"] for e in history} == {actor}

        # Choose two bars (Shift-click), then move one: both move, through update_nodes.
        await bar_of(page, first).click(modifiers=["Shift"], position={"x": 30, "y": 9})
        await bar_of(page, second).click(modifiers=["Shift"], position={"x": 30, "y": 9})
        await expect(page.locator("#count")).to_contain_text("2 chosen to move together")
        await expect(bar_of(page, first)).to_have_attribute("aria-pressed", "true")
        await drag(page, second, 4)
        await expect(bar_of(page, second)).to_have_attribute("data-start", day(8), timeout=SLOW)
        assert await dates(client, second) == (day(8), day(18))
        assert await dates(client, first) == (day(4), day(13))
        assert any(url.endswith(f"{api_path(board)}/tool/update_nodes") for url in w.requests)
        for node, version in ((first, 6), (second, 2)):
            last = (await updates(client, node))[-1]
            assert (last["actor"], last["after"]["version"]) == (actor, version)
        assert w.problems == [], w.problems


async def test_the_roadmap_zooms_groups_and_marks_today(client: Client, board: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Zoom {word}")
    story = await made(client, "story", f"Part {word}", epic)
    held = await made(client, "task", f"Held {word}", story, start_on=day(2), target_on=day(9))
    await made(client, "task", f"Free {word}", story, start_on=day(3), target_on=day(8))
    finished = await made(
        client, "task", f"Free two {word}", story, start_on=day(4), target_on=day(7)
    )
    await ok(client, "claim_node", actor="ann", node=held)
    await ok(client, "update_node", actor="a", node=held, expected_version=1, status="in_progress")
    await ok(client, "update_node", actor="a", node=finished, expected_version=1, status="done")
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{number(epic)}")
        await shown(page, 5)
        track = page.locator(".rm-track")

        # The line for today is where today's date puts it, at every zoom.
        for zoom, per_day in (("quarter", 7.0), ("year", 2.0), ("month", 22.0)):
            await page.locator(f"[data-zoom={zoom}]").click()
            await expect(track).to_have_attribute("data-zoom", zoom)
            line = await timeline(page)
            assert line["ppd"] == per_day and line["today"] == on(day(0))
            left = await page.locator(".rm-today").evaluate("e => parseFloat(e.style.left)")
            assert abs(left - (line["today"] - line["origin"]).days * per_day) < 1.5
            el = bar_of(page, held)
            start_left = await el.evaluate("e => parseFloat(e.style.left)")
            assert abs(start_left - (on(day(2)) - line["origin"]).days * per_day) < 1.5
        assert "zoom=" not in page.url

        # Grouped by who holds them: one group for the agent that claimed one, one for nobody.
        await page.locator("#group").select_option("claimed")
        headings = await page.locator(".rm-group").all_inner_texts()
        assert sorted(headings) == sorted([f"Held by {AGENT} (1)", "Held by nobody (4)"])
        await page.locator("#group").select_option("status")
        counts = [
            int(m)
            for text in await page.locator(".rm-group").all_inner_texts()
            for m in re.findall(r"\((\d+)\)$", text)
        ]
        by_status = Counter(
            n["status"] for n in await all_nodes(client, query=f"epic:{number(epic)}")
        )
        assert sorted(counts) == sorted(by_status.values()) and len(counts) >= 3
        assert "group=status" in page.url
        await page.locator("#group").select_option("epic")
        assert "group=" not in page.url
        assert w.problems == [], w.problems


# -- the ruler, the grid and late work ------------------------------------------------------------


def said_day(offset: int) -> str:
    """A day as a bar's words say it: '3 Dec', with the year when it is not this one."""
    today, when = on(day(0)), on(day(offset))
    return f"{when.day} {when.strftime('%b')}" + (
        f" {when.year}" if when.year != today.year else ""
    )


def cell_of(offset: int) -> str:
    """The ruler's cell of a day, `offset` days from today."""
    return f".rm-day[data-day='{day(offset)}']"


def days_of(pixels: float, per_day: float) -> datetime.timedelta:
    return datetime.timedelta(days=round(pixels / per_day))


async def across(page: Page, selector: str) -> tuple[float, float]:
    """Where an element starts and ends from the left edge of the window, as it is drawn."""
    box = await page.locator(selector).first.evaluate(
        "e => { const b = e.getBoundingClientRect(); return [b.left, b.right]; }"
    )
    return float(box[0]), float(box[1])


async def test_the_ruler_has_a_cell_for_each_day_and_a_bar_fills_whole_cells(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Cells {word}")
    story = await made(client, "story", f"Part {word}", epic)
    one = await made(client, "task", f"One day {word}", story, start_on=day(5), target_on=day(5))
    five = await made(client, "task", f"Five days {word}", story, start_on=day(3), target_on=day(7))
    late = await made(client, "task", f"Late {word}", story, start_on=day(-8), target_on=day(-4))
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{number(epic)}")
        await shown(page, 5)
        line = await timeline(page)
        assert line["ppd"] == 22

        # A cell for each day of the timeline, a day wide, with its number; today's is the pill.
        cells = page.locator(".rm-day")
        track_width = await page.locator(".rm-track").evaluate(
            "e => e.getBoundingClientRect().width"
        )
        assert await cells.count() == round(track_width / line["ppd"])
        first = cells.first
        assert await first.get_attribute("data-day") == line["origin"].isoformat()
        assert (await first.evaluate("e => e.getBoundingClientRect().width")) == line["ppd"]
        assert await first.inner_text() == "1"
        pill = page.locator(".rm-day.today")
        assert await pill.count() == 1
        assert await pill.get_attribute("data-day") == day(0)
        assert await pill.inner_text() == str(on(day(0)).day)

        # A bar covers its start day through its target day, cell for cell: a one-day task fills
        # exactly one cell, and its words count the days.
        one_bar = bar_of(page, one)
        assert await one_bar.evaluate("e => e.getBoundingClientRect().width") == line["ppd"]
        left, _ = await across(page, cell_of(5))
        assert abs(left - (await across(page, f".rm-bar[data-ref='{one}']"))[0]) < 1
        assert await one_bar.get_attribute("title") == (
            f"Task {number(one)}: One day {word}. 1 day: {said_day(5)}. Ready."
        )
        five_bar = f".rm-bar[data-ref='{five}']"
        assert abs((await across(page, cell_of(3)))[0] - (await across(page, five_bar))[0]) < 1
        assert abs((await across(page, cell_of(7)))[1] - (await across(page, five_bar))[1]) < 1
        assert f"5 days: {said_day(3)} to {said_day(7)}." in (
            await bar_of(page, five).get_attribute("aria-label") or ""
        )

        # Late work has a hatched tail from the end of its bar through today's cell, a cell for each
        # day, and says how many.
        tail = f".rm-late[data-ref='{late}']"
        assert await page.locator(".rm-rows .rm-late").count() == 1
        assert await page.locator(tail).get_attribute("data-late") == "4"
        tail_left, tail_right = await across(page, tail)
        late_bar = f".rm-bar[data-ref='{late}']"
        assert abs(tail_left - (await across(page, late_bar))[1]) < 1
        assert abs(tail_left - (await across(page, cell_of(-3)))[0]) < 1
        assert abs(tail_right - (await across(page, cell_of(0)))[1]) < 1
        assert abs((tail_right - tail_left) - 4 * line["ppd"]) < 1
        await expect(page.locator(".rm-note.overdue")).to_have_text("4 days late")
        assert "4 days late" in (await bar_of(page, late).get_attribute("aria-label") or "")
        graph = {n["ref"]: n for n in await all_nodes(client, query=f"epic:{number(epic)}")}
        assert [ref for ref, n in graph.items() if n["overdue"]] == [late]

        # The grid behind the rows is one day wide and one item high: its cell sizes are the day's
        # width and a row's height, drawn by the style sheet and not by elements.
        rows = page.locator(".rm-rows")
        sizes = await rows.evaluate("e => getComputedStyle(e).backgroundSize")
        label_height = await page.locator(".rm-label").first.evaluate(
            "e => e.getBoundingClientRect().height"
        )
        assert f"{line['ppd']:g}px 100%" in sizes and f"{label_height:g}px" in sizes, sizes

        # Dragging the end of the late bar later makes it less late; into the future, not late.
        await drag(page, late, 3, "end")
        await expect(page.locator(tail)).to_have_attribute("data-late", "1", timeout=SLOW)
        await drag(page, late, 4, "end")
        await expect(page.locator(".rm-rows .rm-late")).to_have_count(0, timeout=SLOW)
        await expect(bar_of(page, late)).to_have_attribute("data-late", "0")
        assert (await ok(client, "get_node", node=late))["target_on"] == day(3)
        assert w.problems == [], w.problems


async def test_the_ruler_ticks_each_week_at_quarter_and_each_month_at_year_and_a_name_stays_in_view(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Ticks {word}")
    story = await made(client, "story", f"Part {word}", epic)
    await made(client, "task", f"Soon {word}", story, start_on=day(2), target_on=day(30))
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{number(epic)}")
        await shown(page, 3)
        track = page.locator(".rm-track")

        # Quarter: no cell for each day, a number only on a Monday, at the Monday's own place.
        await page.locator("[data-zoom=quarter]").click()
        await expect(track).to_have_attribute("data-zoom", "quarter")
        assert await page.locator(".rm-day").count() == 0
        line = await timeline(page)
        numbers = await page.locator(".rm-days .rm-tick").evaluate_all(
            "els => els.map(e => [e.textContent, parseFloat(e.style.left)])"
        )
        assert len(numbers) > 8
        for text, left in numbers:
            monday = line["origin"] + days_of(left - 3, line["ppd"])
            assert monday.weekday() == 0 and text == str(monday.day), (text, left)
        today_label = page.locator(".rm-today-label")
        assert await today_label.inner_text() == str(on(day(0)).day)

        # Year: ticks for the months, whose names are in the day tier, and the years above.
        await page.locator("[data-zoom=year]").click()
        await expect(track).to_have_attribute("data-zoom", "year")
        names = await page.locator(".rm-days .rm-tick").evaluate_all(
            "els => els.map(e => [e.textContent, parseFloat(e.style.left)])"
        )
        assert len(names) >= 6
        for text, left in names:
            first = line["origin"] + days_of(left - 3, 2)
            assert first.day == 1 and first.strftime("%b") == text, (text, left)
        assert await page.locator(".rm-today-label").inner_text() == "Today"
        assert await page.locator(".rm-period-name").first.inner_text() == str(on(day(0)).year)

        # Month: the name of the month in view stays beside the labels while its days are
        # scrolled through, however far the month began to the left.
        await page.locator("[data-zoom=month]").click()
        await expect(track).to_have_attribute("data-zoom", "month")
        await page.locator("#rm-scroll").evaluate("e => { e.scrollLeft = 40 * 22; }")
        in_view = await page.locator(".rm-period-name").evaluate_all(
            "els => { const edge = document.querySelector('.rm-labels')"
            ".getBoundingClientRect().right;"
            " return els.filter(e => e.getBoundingClientRect().right > edge + 1)"
            ".map(e => [e.textContent, e.getBoundingClientRect().left - edge]); }"
        )
        at_edge = line["origin"] + datetime.timedelta(days=40)
        assert at_edge.strftime("%b %Y") in [text for text, _ in in_view], in_view
        assert w.problems == [], w.problems


async def test_the_key_opens_from_the_shared_key_button_and_says_what_the_ruler_and_the_tail_mean(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Keyed {word}")
    story = await made(client, "story", f"Part {word}", epic)
    await made(client, "task", f"Past {word}", story, start_on=day(-5), target_on=day(-2))
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{number(epic)}")
        await shown(page, 3)
        button = page.locator("#rm-key-button")
        assert "key-button" in (await button.get_attribute("class") or "")
        assert await page.locator(".key-holder #rm-key-button").count() == 1
        await button.hover()
        card = page.locator(".hovercard:visible")
        await expect(card).to_contain_text("One cell is one day", timeout=SLOW)
        await expect(card).to_contain_text("Weekend")
        await expect(card).to_contain_text("Late: a hatched cell for each day since the due day")
        assert "Overdue" not in await card.inner_text()
        # The tail in the card is the real one, so the key cannot drift from the timeline.
        assert await card.locator(".rm-late").count() == 1
        await page.keyboard.press("Escape")
        await expect(page.locator(".hovercard:visible")).to_have_count(0)
        # The keyboard reaches it too.
        await button.focus()
        await expect(page.locator(".hovercard:visible")).to_contain_text("Status", timeout=SLOW)
        assert w.problems == [], w.problems


async def test_a_timeline_with_one_item_has_a_ruler_and_a_grid_too(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Alone {word}")
    story = await made(client, "story", f"Part {word}", epic)
    only = await made(client, "task", f"Only {word}", story, start_on=day(1), target_on=day(1))
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{number(epic)}+kind:task")
        assert await shown(page, 1) == {only}
        assert await page.locator(".rm-day").count() > 100
        assert await bar_of(page, only).evaluate("e => e.getBoundingClientRect().width") == 22
        assert await page.locator(".rm-rows .rm-late").count() == 0
        # The timeline keeps its rows, so it is not a sliver, and nothing scrolls the page sideways.
        assert (
            await page.locator(".rm-rows").evaluate("e => e.getBoundingClientRect().height")
            >= 4 * 38
        )
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert w.problems == [], w.problems


# -- the record's page, and the way back -----------------------------------------------------------


async def test_a_label_a_bar_or_a_key_opens_the_records_page_and_the_way_back_is_the_roadmap(
    client: Client, board: str
) -> None:
    epic, story, first, second = await window(client)
    word = unique_word()
    note = await make_note(client, f"Planned with care {word}", "Said in planning", nodes=[first])
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        roadmap = f"#/roadmap?q=epic:{number(epic)}&group=status&zoom=quarter"
        await w.open(roadmap)
        await shown(page, 4)
        await expect(page.locator(".rm-track")).to_have_attribute("data-zoom", "quarter")

        async def back_on_the_roadmap() -> None:
            """The Roadmap's own crumb leads back to the view as it was left: its filter, its
            grouping and its zoom, with every bar there again."""
            await page.locator("nav.crumbs li.crumb-view a").click()
            await expect(page.locator(".rm-track")).to_be_visible(timeout=SLOW)
            await on_page(page, "roadmap")
            assert await page.locator("#filter").input_value() == f"epic:{number(epic)}"
            assert await page.locator("#group").input_value() == "status"
            await expect(page.locator(".rm-track")).to_have_attribute("data-zoom", "quarter")
            await shown(page, 4)

        # Shift-click chooses bars to move together and opens nothing; Escape gives them up.
        await bar_of(page, first).click(modifiers=["Shift"], position={"x": 30, "y": 9})
        await bar_of(page, second).click(modifiers=["Shift"], position={"x": 30, "y": 9})
        await expect(page.locator("#count")).to_contain_text("2 chosen to move together")
        await expect(bar_of(page, first)).to_have_attribute("aria-pressed", "true")
        assert "#/roadmap" in page.url and "item=" not in page.url
        await bar_of(page, second).focus()
        await page.keyboard.press("Escape")
        await expect(page.locator("#count")).not_to_contain_text("chosen")
        await expect(page.locator("#item-page")).to_have_count(0)

        # A label opens the item's page, from the Roadmap, and the path says so.
        await page.locator(f".rm-label[data-ref='{story}'] a.rm-open").click()
        await expect(page.locator("#item-title")).to_contain_text(f"{code(story)} ", timeout=SLOW)
        assert (await hash_of(page)).startswith(f"#/item?item={story}&from=roadmap")
        assert (await crumbs(page))[2] == "Roadmap"
        assert (await crumbs(page))[-1] == code(story)
        await expect(page.locator("#main-nav a[data-page=roadmap]")).to_have_attribute(
            "aria-current", "page"
        )
        await no_panel_or_drawer(page)
        await back_on_the_roadmap()

        # A key on a bar does the same, and the page shows what concerns the item, as recall says.
        await bar_of(page, first).focus()
        await page.keyboard.press("Enter")
        await expect(page.locator("#item-title")).to_contain_text(f"{code(first)} ", timeout=SLOW)
        assert (await hash_of(page)).startswith(f"#/item?item={first}&from=roadmap")
        row = item_page(page).locator(f"tr.record-line[data-ref='note:{note['id']}']")
        await expect(row).to_contain_text(f"Planned with care {word}", timeout=SLOW)
        await row.locator("a").first.click()
        await expect(page.locator("#item-title")).to_contain_text(f"Planned with care {word}")
        # A note reached from the Roadmap is still in the Roadmap, and the way back is the same.
        assert (await hash_of(page)).endswith("from=roadmap")
        assert (await crumbs(page))[2] == "Roadmap"
        await back_on_the_roadmap()

        # A plain click on a bar opens its page too.
        await bar_of(page, second).click(position={"x": 30, "y": 9})
        await expect(page.locator("#item-title")).to_contain_text(f"{code(second)} ", timeout=SLOW)
        assert (await hash_of(page)).startswith(f"#/item?item={second}&from=roadmap")
        await back_on_the_roadmap()
        assert w.problems == [], w.problems


async def test_an_undated_item_is_scheduled_from_its_page_and_a_late_start_is_refused_there(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Page dates {word}")
    story = await made(client, "story", f"Part {word}", epic)
    await made(client, "task", f"Dated {word}", story, start_on=day(1), target_on=day(5))
    undated = await made(client, "task", f"Undated {word}", story)
    said = await refused(
        client,
        "update_node",
        actor="x",
        node=undated,
        expected_version=1,
        start_on=day(20),
        target_on=day(12),
    )
    actor = signed_in_name(board)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        roadmap = f"#/roadmap?q=epic:{number(epic)}"
        await w.open(roadmap)
        # The item with no dates is not on the timeline: the story and the dated task are.
        await shown(page, 3)
        assert await bar_of(page, undated).count() == 0
        await open_item(w, undated, "roadmap")

        # Dates that start after they end are refused by the server, in its words, on the page.
        await open_editor(page)
        await page.locator("#edit-start").fill(day(20))
        await page.locator("#edit-target").fill(day(12))
        await page.get_by_role("button", name="Save changes").click()
        problem = item_page(page).locator(".edit .problem .refusal-text")
        await expect(problem).to_be_visible(timeout=SLOW)
        plain(await problem.inner_text(), said)
        assert (await ok(client, "get_node", node=undated))["start_on"] is None

        # Good dates are one versioned change by the person, and the page shows them.
        await page.locator("#edit-start").fill(day(6))
        await page.locator("#edit-target").fill(day(11))
        await page.get_by_role("button", name="Save changes").click()
        await expect(item_page(page).locator("dd.start")).to_have_text(day(6), timeout=SLOW)
        await expect(item_page(page).locator("dd.target")).to_have_text(day(11))
        assert await dates(client, undated) == (day(6), day(11))
        (change,) = await updates(client, undated)
        assert (change["actor"], change["before"], change["after"]) == (
            actor,
            {"start_on": None, "target_on": None, "version": 1},
            {"start_on": day(6), "target_on": day(11), "version": 2},
        )

        # Back on the Roadmap, the item is a bar at the pixels its dates give.
        await w.open(roadmap)
        await expect(bar_of(page, undated)).to_have_attribute("data-start", day(6), timeout=SLOW)
        await expect(bar_of(page, undated)).to_have_attribute("data-end", day(11))
        assert await shown(page, 4) >= {undated}
        line = await timeline(page)
        left = await bar_of(page, undated).evaluate("e => parseFloat(e.style.left)")
        assert abs(left - (on(day(6)) - line["origin"]).days * line["ppd"]) < 1.5
        assert w.problems == [] or all("status of 400" in p for p in w.problems), w.problems


async def test_a_roadmaps_address_opens_the_same_state_in_a_second_browser_and_stores_nothing(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Kept {word}")
    story = await made(client, "story", f"Kept story {word}", epic)
    task = await made(client, "task", f"Kept task {word}", story, start_on=day(1), target_on=day(5))
    await make_note(client, f"About the kept task {word}", "Said in review", nodes=[task])
    await reindexed(client)
    async with browser_page(board, token=the_token()) as first:
        await first.open(f"#/roadmap?q=epic:{number(epic)}")
        await shown(first.page, 3)
        await first.page.locator("[data-zoom=quarter]").click()
        await expect(first.page.locator(".rm-track")).to_have_attribute("data-zoom", "quarter")
        await first.page.locator("#group").select_option("status")
        await expect(first.page.locator(".rm-group").first).to_be_visible(timeout=SLOW)
        roadmap = await hash_of(first.page)
        for part in ("#/roadmap?", f"q=epic:{number(epic)}", "zoom=quarter", "group=status"):
            assert part in roadmap, (part, roadmap)

        # The task's page, from the Roadmap, and then a search of the box in the top bar: each is
        # a page with an address of its own, and neither is a part of the Roadmap's address.
        await first.page.locator(f".rm-label[data-ref='{task}'] a.rm-open").click()
        await expect(first.page.locator("#item-title")).to_contain_text(
            f"{code(task)} ", timeout=SLOW
        )
        record = await hash_of(first.page)
        assert record.startswith(f"#/item?item={task}&from=roadmap")
        await first.page.locator("#global-search").fill(f"kept task {word}")
        await first.page.locator("#global-search-go").click()
        await expect(first.page.locator("#search-results tr.result").first).to_be_visible(
            timeout=SLOW
        )
        searched = await hash_of(first.page)
        assert searched.startswith("#/search?") and "search=" in searched
        for gone in ("item=", "zoom=", "group=", "drawer=", "sel="):
            assert gone not in searched, (gone, searched)
        assert the_token() not in first.page.url
        results = await first.page.locator("#search-results tr.result").evaluate_all(
            "els => els.map(e => e.dataset.ref)"
        )
        before = database_footprint()

        # A second browser, which has never seen any of this, opens each address and sees the same.
        async with browser_page(board, token=the_token(), name="Someone else") as second:
            await second.page.goto(f"{board}{roadmap}")
            await shown(second.page, 3)
            await expect(second.page.locator(".rm-track")).to_have_attribute(
                "data-zoom", "quarter", timeout=SLOW
            )
            assert await second.page.locator("#filter").input_value() == f"epic:{number(epic)}"
            assert await second.page.locator("#group").input_value() == "status"
            await second.page.goto(f"{board}?cold=1{record}")
            await expect(second.page.locator("#item-title")).to_contain_text(
                f"{code(task)} ", timeout=SLOW
            )
            assert (await crumbs(second.page))[2] == "Roadmap"
            await expect(second.page.locator("#main-nav a[data-page=roadmap]")).to_have_attribute(
                "aria-current", "page"
            )
            await second.page.goto(f"{board}?cold=2{searched}")
            await expect(second.page.locator("#search-results tr.result").first).to_be_visible(
                timeout=SLOW
            )
            assert sorted(
                await second.page.locator("#search-results tr.result").evaluate_all(
                    "els => els.map(e => e.dataset.ref)"
                )
            ) == sorted(results)
            assert second.problems == [], second.problems
        assert database_footprint() == before
