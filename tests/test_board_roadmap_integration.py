"""M11's Roadmap against the running containers, driven in a real headless browser and checked
against a real MCP client (see ./check.sh). Bars are checked at the pixels their dates give, every
change a person makes is read back through get_node and node_history, and refusals are compared
word for word with what the same change gets through MCP. Every filter starts with
`epic:<number>` so that a test sees only the items it made. Items are named by their refs
(`task:40`): the bars, labels and tray items carry them as `data-ref`, and the page writes them as
codes (T-40)."""

import re
from collections import Counter
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import expect

from tests.board_helpers import (
    SLOW,
    api_path,
    apply_filter,
    bar_of,
    board_url,
    browser_page,
    database_footprint,
    hold_back_news,
    made,
    on,
    plain,
    timeline,
)
from tests.board_helpers import drag_bar as drag
from tests.board_helpers import roadmap_ids as shown
from tests.helpers import (
    all_nodes,
    day,
    make_fact,
    ok,
    refused,
    reindexed,
    the_token,
    unique_word,
)
from tests.helpers import code_of as code
from tests.helpers import number_of as number

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

ACTOR = "Ada Lovelace"


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


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


async def test_the_roadmap_draws_bars_tray_worked_out_spans_arrows_and_marks_and_filters_like_mcp(
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
        assert await shown(page, 8, 2) == {epic, story, *ids.values()}
        line = await timeline(page)
        assert line["today"] == on(day(0)) and line["ppd"] == 10

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

        # The tray holds the two items with no dates, and nothing else.
        assert await page.locator(".tray-item").evaluate_all(
            "els => els.map(e => e.dataset.ref)"
        ) == [ids["u1"], ids["u2"]]

        # The arrow runs from what is needed first to the item that waits, in a warning colour.
        arrows = page.locator("path.dep")
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
        assert "Overdue" in (await bar_of(page, ids["c"]).get_attribute("aria-label") or "")
        # What the page marks is what the graph says.
        graph = {n["ref"]: n for n in await all_nodes(client, query=f"epic:{number(epic)}")}
        assert graph[ids["b"]]["schedule_conflicts"] == [ids["a"]]
        assert graph[ids["c"]]["overdue"] is True
        assert [ref for ref, n in graph.items() if n["overdue"]] == [ids["c"]]

        # A date filter gives the same refs as the same text through MCP.
        epic_n = number(epic)
        for text, expected_dated in (
            (f"epic:{epic_n} target:{day(-30)}..{day(16)}", 4),
            (f"epic:{epic_n} is:overdue", 1),
            (f"epic:{epic_n} target:none kind:task", 0),
            (f"epic:{epic_n} start:{day(0)}..{day(30)} -kind:epic", 4),
        ):
            wanted = {n["ref"] for n in await all_nodes(client, query=text)}
            undated_count = 2 if "target:none" in text else 0
            assert len(wanted) == expected_dated + undated_count, (text, wanted)
            await apply_filter(page, text)
            assert await shown(page, expected_dated, undated_count) == wanted, text
        assert w.problems == [], w.problems


# -- 5. changing dates by dragging -----------------------------------------------------------------


async def test_dragging_a_bar_or_an_end_changes_the_dates_as_a_versioned_change_by_the_person(
    client: Client, board: str
) -> None:
    epic, story, moved, ended = await window(client)
    started = await made(client, "task", "Started late", story, start_on=day(6), target_on=day(20))
    async with browser_page(board, token=the_token(), name=ACTOR) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{number(epic)}")
        await shown(page, 5, 0)

        # Both dates move together.
        await drag(page, moved, 5)
        await expect(bar_of(page, moved)).to_have_attribute("data-start", day(8), timeout=SLOW)
        assert await dates(client, moved) == (day(8), day(17))
        (change,) = await updates(client, moved)
        assert (change["actor"], change["before"], change["after"]) == (
            ACTOR,
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
            ACTOR,
            {"target_on": day(14), "version": 1},
            {"target_on": day(11), "version": 2},
        )
        await drag(page, started, 2, "start")
        await expect(bar_of(page, started)).to_have_attribute("data-start", day(8), timeout=SLOW)
        assert await dates(client, started) == (day(8), day(20))
        (change,) = await updates(client, started)
        assert (change["after"], change["actor"]) == (
            {"start_on": day(8), "version": 2},
            ACTOR,
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
    async with browser_page(board, token=the_token(), name=ACTOR) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{number(epic)}")
        await shown(page, 4, 0)

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
        assert [e["actor"] for e in await updates(client, stale)] == ["agent-1"]

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


async def test_an_undated_item_dropped_on_the_timeline_gets_both_dates_and_the_tray_has_date_fields(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Tray {word}")
    story = await made(client, "story", f"Part {word}", epic)
    dropped = await made(client, "task", f"Dropped {word}", story)
    typed = await made(client, "task", f"Typed {word}", story)
    await made(client, "task", f"Dated {word}", story, start_on=day(1), target_on=day(9))
    async with browser_page(board, token=the_token(), name=ACTOR) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{number(epic)}")
        await shown(page, 3, 2)
        line = await timeline(page)

        # Dropped two days after today, it gets that day and the six after it.
        at = ((line["today"] - line["origin"]).days + 2) * line["ppd"] + line["ppd"] / 2
        await page.drag_and_drop(
            f".tray-item[data-ref='{dropped}'] .tray-drag",
            ".rm-rows",
            target_position={"x": at, "y": 60},
        )
        await expect(bar_of(page, dropped)).to_be_visible(timeout=SLOW)
        assert await dates(client, dropped) == (day(2), day(8))
        (change,) = await updates(client, dropped)
        assert (change["actor"], change["before"], change["after"]) == (
            ACTOR,
            {"start_on": None, "target_on": None, "version": 1},
            {"start_on": day(2), "target_on": day(8), "version": 2},
        )
        assert await shown(page, 4, 1) >= {dropped, typed}
        assert await page.locator(f".tray-item[data-ref='{dropped}']").count() == 0

        # The fields in the tray do the same, for people who do not drag.
        form = page.locator(f".tray-item[data-ref='{typed}'] form")
        await form.locator("input").first.fill(day(10))
        await form.locator("input").last.fill(day(16))
        await form.locator("button").click()
        await expect(bar_of(page, typed)).to_have_attribute("data-start", day(10), timeout=SLOW)
        assert await dates(client, typed) == (day(10), day(16))
        assert (await updates(client, typed))[0]["actor"] == ACTOR
        assert w.problems == [], w.problems


# -- 5. the keyboard, several bars at once, zoom and groups -------------------------------------


async def test_keys_shift_dates_by_a_day_or_a_week_and_chosen_bars_move_together_in_one_request(
    client: Client, board: str
) -> None:
    epic, _, first, second = await window(client)
    async with browser_page(board, token=the_token(), name=ACTOR) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{number(epic)}")
        await shown(page, 4, 0)
        keyed = bar_of(page, first)

        # Three presses add up and are sent as one change.
        await keyed.focus()
        for _ in range(3):
            await page.keyboard.press("ArrowRight")
        await expect(keyed).to_have_attribute("data-start", day(6), timeout=SLOW)
        assert await dates(client, first) == (day(6), day(15))
        (change,) = await updates(client, first)
        assert (change["actor"], change["after"]["version"]) == (ACTOR, 2)

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
        assert {e["actor"] for e in history} == {ACTOR}

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
            assert (last["actor"], last["after"]["version"]) == (ACTOR, version)
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
        await shown(page, 5, 0)
        track = page.locator(".rm-track")

        # The line for today is where today's date puts it, at every zoom.
        for zoom, per_day in (("quarter", 4.0), ("year", 1.2), ("month", 10.0)):
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

        # Grouped by who holds them: one group for ann, one for nobody.
        await page.locator("#group").select_option("claimed")
        headings = await page.locator(".rm-group").all_inner_texts()
        assert sorted(headings) == ["Held by ann (1)", "Held by nobody (4)"]
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


# -- the shared detail panel, the drawers and the address ----------------------------------------


async def test_a_label_or_a_key_opens_the_shared_panel_and_the_timeline_stays_as_it_was(
    client: Client, board: str
) -> None:
    epic, story, first, second = await window(client)
    word = unique_word()
    fact = await make_fact(client, f"Planned with care {word}", nodes=[first])
    async with browser_page(board, token=the_token(), name=ACTOR) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{number(epic)}&group=status")
        await shown(page, 4, 0)
        # Two bars are chosen to move together, and the timeline is scrolled a little.
        await bar_of(page, first).click(modifiers=["Shift"], position={"x": 30, "y": 9})
        await bar_of(page, second).click(modifiers=["Shift"], position={"x": 30, "y": 9})
        await expect(page.locator("#count")).to_contain_text("2 chosen to move together")
        scrolled = await page.evaluate(
            "() => { const e = document.getElementById('rm-scroll'); e.scrollLeft = 37;"
            " return e.scrollLeft; }"
        )

        async def unchanged() -> None:
            await expect(page.locator("#count")).to_contain_text("2 chosen to move together")
            await expect(bar_of(page, first)).to_have_attribute("aria-pressed", "true")
            await expect(bar_of(page, second)).to_have_attribute("aria-pressed", "true")
            assert await page.locator("#filter").input_value() == f"epic:{number(epic)}"
            assert await page.locator("#group").input_value() == "status"
            assert (
                await page.evaluate("document.getElementById('rm-scroll').scrollLeft") == scrolled
            )

        # A label opens the item in the panel, named in the address as sel=story:<number>.
        await page.locator(f".rm-label[data-ref='{story}'] button").click()
        await expect(page.locator("#card-title")).to_contain_text(f"{code(story)} ", timeout=SLOW)
        assert f"sel={story}" in page.url and f"q=epic:{number(epic)}" in page.url
        await unchanged()

        # A key on a bar does the same, and the panel shows what concerns it, as recall says.
        await bar_of(page, first).focus()
        await page.keyboard.press("Enter")
        await expect(page.locator("#card-title")).to_contain_text(f"{code(first)} ", timeout=SLOW)
        assert f"sel={first}" in page.url
        row = page.locator(f"#panel li.record[data-record='fact:{fact['id']}']")
        await expect(row).to_contain_text(f"Planned with care {word}")
        await unchanged()

        # Opening a drawer (activity, then search) leaves the bars, the choice and the scroll alone,
        # and the panel stays.
        await page.locator("#activity-open").click()
        await expect(page.locator("#drawer[data-drawer=activity]")).to_be_visible()
        await unchanged()
        await page.locator("#global-search").fill(f"Planned with care {word}")
        await page.locator("#global-search-go").click()
        await expect(page.locator("#drawer[data-drawer=search]")).to_be_visible()
        await expect(page.locator("#card-title")).to_contain_text(f"{code(first)} ")
        await unchanged()
        assert "drawer=search" in page.url and f"sel={first}" in page.url

        # Escape gives up the choice, and a click on a bar opens that item.
        await bar_of(page, second).focus()
        await page.keyboard.press("Escape")
        await expect(page.locator("#count")).not_to_contain_text("chosen")
        await bar_of(page, second).click(position={"x": 30, "y": 9})
        await expect(page.locator("#card-title")).to_contain_text(f"{code(second)} ", timeout=SLOW)
        assert f"sel={second}" in page.url
        # Closing the panel takes sel out of the address and leaves the Roadmap where it was.
        await page.get_by_role("button", name="Close the details").click()
        await expect(page.locator("#panel")).to_be_hidden()
        assert "sel=" not in page.url and f"q=epic:{number(epic)}" in page.url
        await shown(page, 4, 0)
        assert w.problems == [], w.problems


async def test_an_undated_item_is_scheduled_from_the_panel_and_a_late_start_is_refused_there(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Panel dates {word}")
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
    async with browser_page(board, token=the_token(), name=ACTOR) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{number(epic)}&sel={undated}")
        await shown(page, 3, 1)
        await expect(page.locator("#card-title")).to_contain_text(f"{code(undated)} ", timeout=SLOW)

        # Dates that start after they end are refused by the server, in its words, in the panel.
        await page.locator("#edit-start").fill(day(20))
        await page.locator("#edit-target").fill(day(12))
        await page.get_by_role("button", name="Save changes").click()
        problem = page.locator("#panel .edit .problem .refusal-text")
        await expect(problem).to_be_visible(timeout=SLOW)
        plain(await problem.inner_text(), said)
        assert (await ok(client, "get_node", node=undated))["start_on"] is None
        await shown(page, 3, 1)

        # Good dates put it on the timeline, at the pixels they give, as one versioned change.
        await page.locator("#edit-start").fill(day(6))
        await page.locator("#edit-target").fill(day(11))
        await page.get_by_role("button", name="Save changes").click()
        await expect(bar_of(page, undated)).to_have_attribute("data-start", day(6), timeout=SLOW)
        await expect(bar_of(page, undated)).to_have_attribute("data-end", day(11))
        assert await shown(page, 4, 0) >= {undated}
        line = await timeline(page)
        left = await bar_of(page, undated).evaluate("e => parseFloat(e.style.left)")
        assert abs(left - (on(day(6)) - line["origin"]).days * line["ppd"]) < 1.5
        assert await dates(client, undated) == (day(6), day(11))
        (change,) = await updates(client, undated)
        assert (change["actor"], change["before"], change["after"]) == (
            ACTOR,
            {"start_on": None, "target_on": None, "version": 1},
            {"start_on": day(6), "target_on": day(11), "version": 2},
        )
        # The panel is still the item's, with its new dates, and the address still names it.
        await expect(page.locator("#panel .card-detail .dates")).to_contain_text(
            f"Start {day(6)}. Target {day(11)}."
        )
        assert f"sel={undated}" in page.url
        assert w.problems == [] or all("status of 400" in p for p in w.problems), w.problems


async def test_a_roadmaps_address_opens_the_same_state_in_a_second_browser_and_stores_nothing(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Kept {word}")
    story = await made(client, "story", f"Kept story {word}", epic)
    task = await made(client, "task", f"Kept task {word}", story, start_on=day(1), target_on=day(5))
    await made(client, "task", f"Undated {word}", story)
    await make_fact(client, f"About the kept task {word}", nodes=[task])
    await reindexed(client)
    async with browser_page(board, token=the_token()) as first:
        await first.open(f"#/roadmap?q=epic:{number(epic)}")
        await shown(first.page, 3, 1)
        await first.page.locator("[data-zoom=quarter]").click()
        await expect(first.page.locator(".rm-track")).to_have_attribute("data-zoom", "quarter")
        await first.page.locator("#group").select_option("status")
        await expect(first.page.locator(".rm-group").first).to_be_visible(timeout=SLOW)
        await first.page.locator(f".rm-label[data-ref='{task}'] button").click()
        await expect(first.page.locator("#card-title")).to_contain_text(
            f"{code(task)} ", timeout=SLOW
        )
        await first.page.locator("#global-search").fill(f"kept task {word}")
        await first.page.locator("#global-search-go").click()
        await expect(first.page.locator("#search-results li.result").first).to_be_visible(
            timeout=SLOW
        )
        address = first.page.url
        for part in ("#/roadmap?", f"q=epic:{number(epic)}", "zoom=quarter", "group=status"):
            assert part in address, (part, address)
        assert f"sel={task}" in address and "drawer=search" in address
        assert the_token() not in address
        results = await first.page.locator("#search-results li.result").evaluate_all(
            "els => els.map(e => e.dataset.ref)"
        )
        before = database_footprint()

        # A second browser, which has never seen this view, opens the address and sees the same.
        async with browser_page(board, token=the_token(), name="Someone else") as second:
            await second.page.goto(address)
            await shown(second.page, 3, 1)
            await expect(second.page.locator(".rm-track")).to_have_attribute(
                "data-zoom", "quarter", timeout=SLOW
            )
            assert await second.page.locator("#filter").input_value() == f"epic:{number(epic)}"
            assert await second.page.locator("#group").input_value() == "status"
            await expect(second.page.locator("#card-title")).to_contain_text(
                f"{code(task)} ", timeout=SLOW
            )
            await expect(second.page.locator("#drawer[data-drawer=search]")).to_be_visible()
            await expect(second.page.locator("#search-results li.result").first).to_be_visible(
                timeout=SLOW
            )
            assert sorted(
                await second.page.locator("#search-results li.result").evaluate_all(
                    "els => els.map(e => e.dataset.ref)"
                )
            ) == sorted(results)
            assert second.problems == [], second.problems
        assert database_footprint() == before
