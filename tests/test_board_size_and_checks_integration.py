"""M32: the size of a story or task, and the definition of done as checks, on the board.

Driven in a real headless browser and compared with what a real MCP client reads (see ./check.sh).
A person gives, changes and clears a size from the page of an item, from its table and from the
forms, and the points that an epic and a story add up follow; an item with no size shows a visible,
editable "Not set"; a person ticks the checks of the definition of done with one click, says how
they checked, and unticks them, and what an agent ticks appears on a page that is open; the Board's
cards carry the size and the checks, and its Key button says what the signs are. Every project here
is one of its own, so the shared project's definition of done is never touched."""

import re
from typing import Any

import pytest
from playwright.async_api import Page, expect

from tests.board_context import ADA, fresh_project
from tests.board_helpers import SLOW, browser_page, card, go, made
from tests.helpers import all_nodes, ok
from tests.world import Project, join_project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NEWS = 20_000


def number(ref: str) -> int:
    return int(ref.partition(":")[2])


async def choose(page: Page, trigger_selector: str, words: str) -> None:
    """Open the menu of a chip and choose the row that starts with `words`."""
    await page.locator(trigger_selector).click()
    await page.locator(".vmenu .vmenu-option", has_text=re.compile(re.escape(words))).click()


def set_definition(project: Project, body: str) -> None:
    """The definition of done, written as its owner would (only a person can)."""
    saved = project.owner.call(
        "POST",
        f"/board/api/projects/{project.id}/tool/set_definition_of_done",
        {"body": body, "expected_version": 1},
    )
    assert saved.status == 200, saved.body


async def keys_of_checks(client: Any) -> dict[str, str]:
    """The key of each check, by its words."""
    items = (await ok(client, "get_definition_of_done"))["items"]
    return {one["text"]: one["item"] for one in items}


# -- size ---------------------------------------------------------------------------------------


async def test_a_person_gives_changes_and_clears_a_size_and_the_totals_follow(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Sizing")
        story = await made(client, "story", "A story with tasks", epic)
        first = await made(client, "task", "First task", story)
        second = await made(client, "task", "Second task", story)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            # With no size the page says so, in a control that gives one.
            await go(page, board, f"#/item?item={first}")
            size = page.locator("#item-page .card-summary [data-menu=size]")
            await expect(size).to_have_attribute("data-size", "none", timeout=SLOW)
            await expect(size).to_contain_text("Not set")
            await choose(page, "#item-page .card-summary [data-menu=size]", "Medium")
            await expect(size).to_have_attribute("data-size", "m", timeout=SLOW)
            await expect(size).to_contain_text("Medium, 3 points")
            node = await ok(client, "get_node", node=first)
            assert (node["size"], node["points"]) == ("m", 3)

            # A story that has tasks adds up theirs (what is done, of all the points), and says how
            # many are not sized.
            await go(page, board, f"#/item?item={story}")
            points = page.locator("#item-page .card-summary dd.points")
            await expect(points.locator(".points-total")).to_have_text(
                "0 of 3 points", timeout=SLOW
            )
            await expect(points.locator(".points-rollup")).to_have_attribute("data-points", "3")
            await expect(points.locator(".points-unsized")).to_have_text("1 not sized")
            assert await page.locator("#item-page .card-summary [data-menu=size]").count() == 0
            story_node = await ok(client, "get_node", node=story)
            assert (story_node["points_total"], story_node["unsized"]) == (3, 1)

            # The table of its tasks has a Size column, a menu in each row, and a total line.
            row = page.locator(f"#item-table tr[data-ref='{second}']")
            await expect(row.locator("[data-menu=size]")).to_have_text("Not set")
            await choose(page, f"#item-table tr[data-ref='{second}'] [data-menu=size]", "Large")
            await expect(row.locator("[data-menu=size]")).to_have_text("L", timeout=SLOW)
            await expect(page.locator("#item-table tfoot .item-points")).to_have_text(
                "8 points", timeout=SLOW
            )
            assert (await ok(client, "get_node", node=second))["size"] == "l"
            await expect(points.locator(".points-total")).to_have_text(
                "0 of 8 points", timeout=SLOW
            )
            assert (await ok(client, "get_node", node=story))["points_total"] == 8
            assert await points.locator(".points-unsized").count() == 0

            # The epic adds up its stories, and has no size of its own to give.
            await go(page, board, f"#/item?item={epic}")
            await expect(
                page.locator("#item-page .card-summary dd.points .points-total")
            ).to_have_text("0 of 8 points", timeout=SLOW)
            assert await page.locator("#item-page [data-menu=size]").count() == 0
            await page.locator("#edit-open").click()
            await expect(page.locator("#edit-title")).to_be_visible()
            assert await page.locator("#edit-size").count() == 0

            # The Edit form has the size, with "No size" first, and clears it too.
            await go(page, board, f"#/item?item={first}")
            await page.locator("#edit-open").click()
            assert await page.locator("#edit-size option").first.inner_text() == "No size"
            await page.locator("#edit-size").select_option("xl")
            await page.get_by_role("button", name="Save changes").click()
            await expect(size).to_have_attribute("data-size", "xl", timeout=SLOW)
            assert (await ok(client, "get_node", node=first))["size"] == "xl"
            await page.locator("#edit-open").click()
            await page.locator("#edit-size").select_option("")
            await page.get_by_role("button", name="Save changes").click()
            await expect(size).to_have_attribute("data-size", "none", timeout=SLOW)
            assert (await ok(client, "get_node", node=first))["size"] is None

            # A size is cleared with the menu too, and the totals follow it down.
            await go(page, board, f"#/item?item={second}")
            await choose(page, "#item-page .card-summary [data-menu=size]", "No size")
            await expect(
                page.locator("#item-page .card-summary [data-menu=size]")
            ).to_have_attribute("data-size", "none", timeout=SLOW)
            assert (await ok(client, "get_node", node=second))["size"] is None
            assert (await ok(client, "get_node", node=story))["points_total"] == 0

            # The New item dialog has the size for a story or task, and not for an epic.
            await go(page, board, "#/board")
            await page.locator("#new-item").click()
            await page.locator("#new-kind").select_option("epic")
            await expect(page.locator("#new-size")).to_be_hidden()
            await page.locator("#new-kind").select_option("task")
            await page.locator("#new-parent").select_option(story)
            await page.locator("#new-title").fill("Sized in the dialog")
            await page.locator("#new-size").select_option("s")
            await page.get_by_role("button", name="Create").click()
            await expect(page.locator("#item-page[data-kind=task]")).to_be_visible(timeout=SLOW)
            made_now = (await ok(client, "list_nodes", query="size:s"))["items"]
            assert [one["title"] for one in made_now] == ["Sized in the dialog"]

            # Someone who can only look sees the size, with nothing to change it with.
            viewer = join_project(project, "viewer")
        async with browser_page(board, token=None, person=viewer) as v:
            version = (await ok(client, "get_node", node=second))["version"]
            await ok(client, "update_node", node=second, expected_version=version, size="l")
            await go(v.page, board, f"#/item?item={second}")
            chip = v.page.locator("#item-page .card-summary .chip.size")
            await expect(chip).to_contain_text("Large, 5 points", timeout=SLOW)
            assert await v.page.locator("#item-page button[data-menu=size]").count() == 0


async def test_a_size_change_is_told_in_plain_words_in_the_history(server_url: str) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Words")
        story = await made(client, "story", "A story", epic)
        task = await made(client, "task", "Sized three times", story)
        await ok(client, "update_node", node=task, expected_version=1, size="m")
        await ok(client, "update_node", node=task, expected_version=2, size="l")
        await ok(client, "update_node", node=task, expected_version=3, clear_size=True)
        async with browser_page(board, token=project.key, name=ADA) as w:
            await go(w.page, board, f"#/item?item={task}")
            words = w.page.locator("#item-page [data-part=history] .history .event-words")
            code = f"task T-{number(task)} “Sized three times”"
            await expect(words).to_have_text(
                [
                    f"took the size off {code}.",
                    f"changed the size of {code} from Medium to Large.",
                    f"sized {code} as Medium.",
                    f"created {code}.",
                ],
                timeout=SLOW,
            )


# -- the definition of done as checks ----------------------------------------------------------


async def test_a_person_ticks_the_checks_and_says_how_and_an_agents_tick_appears(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        set_definition(
            project, "Before it is done:\n- Tests pass\n- A person tried it\n- Notes are written"
        )
        epic = await made(client, "epic", "Checks")
        story = await made(client, "story", "A story", epic)
        task = await made(client, "task", "A task", story)
        keys = await keys_of_checks(client)
        assert list(keys) == ["Tests pass", "A person tried it", "Notes are written"]
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await go(page, board, f"#/item?item={task}")
            part = page.locator("#item-page [data-part=definition]")
            await expect(part.locator(".check-text")).to_have_text(list(keys), timeout=SLOW)
            await expect(part.locator(".check-intro")).to_contain_text("Before it is done:")
            await expect(part.locator("h3")).to_contain_text("0 of 3 ticked")
            items = part.locator(".check-item")
            boxes = part.locator("input.check-box")
            box = await boxes.first.bounding_box()
            assert box is not None and box["width"] >= 24 and box["height"] >= 24

            # One click ticks it, as the person, with nothing asked.
            await boxes.nth(0).click()
            await expect(part.locator("h3")).to_contain_text("1 of 3 ticked", timeout=SLOW)
            await expect(items.nth(0).locator(".check-who")).to_contain_text(ADA)
            first = (await ok(client, "get_node", node=task))["checks"][0]
            assert (first["checked"], first["checked_by"], first["checked_by_kind"]) == (
                True,
                ADA,
                "person",
            )
            assert first["evidence"] is None

            # "Add how you checked" ticks it with those words, which are shown whole.
            how = "Tried it on a phone, and it worked. " * 6
            how = how.strip()
            await items.nth(1).locator(".check-how").click()
            await expect(part.locator(".check-how-field")).to_be_focused()
            await part.get_by_role("button", name="Save").click()
            await expect(part.locator(".check-form .problem")).to_contain_text(
                "Say how you checked"
            )
            # A secret is refused in the server's words, beside the field, and nothing is ticked.
            await part.locator(".check-how-field").fill("ghp_" + "a" * 36)
            await part.get_by_role("button", name="Save").click()
            await expect(part.locator(".check-form .problem-text")).to_contain_text(
                "secret", timeout=SLOW
            )
            assert (await ok(client, "get_node", node=task))["checks"][1]["checked"] is False
            await part.locator(".check-how-field").fill(how)
            await part.get_by_role("button", name="Save").click()
            await expect(items.nth(1).locator(".check-evidence")).to_have_text(how, timeout=SLOW)
            await expect(part.locator("h3")).to_contain_text("2 of 3 ticked")
            second = (await ok(client, "get_node", node=task))["checks"][1]
            assert (second["checked"], second["evidence"]) == (True, how)

            # Unticking is one click, and takes the tick off.
            await boxes.nth(0).click()
            await expect(items.nth(0)).to_have_attribute("data-checked", "false", timeout=SLOW)
            assert (await ok(client, "get_node", node=task))["checks"][0]["checked"] is False

            # An agent ticks the third with a real client, and the open page shows it.
            await ok(
                client,
                "set_check",
                node=task,
                item=keys["Notes are written"],
                evidence="All 212 tests pass",
            )
            await expect(items.nth(2)).to_have_attribute("data-checked", "true", timeout=NEWS)
            await expect(items.nth(2).locator(".check-evidence")).to_have_text("All 212 tests pass")
            await expect(part.locator("h3")).to_contain_text("2 of 3 ticked")

            # The history and the Activity feed say it in words, with what was said shown whole.
            tick = page.locator("#item-page [data-part=history] li[data-action=check_ticked]")
            await expect(tick).to_have_count(3, timeout=SLOW)
            await expect(
                tick.filter(has_text="A person tried it").locator(".event-words")
            ).to_contain_text(f"ticked “A person tried it” on task T-{number(task)}")
            long = tick.filter(has_text="A person tried it")
            await long.locator(".what-changed summary").click()
            await expect(long.locator(".change-text")).to_have_text(how)
            await go(page, board, "#/activity?act_kind=checks")
            # Two ticks in a row by the same person on the same task are one line, with the checks
            # folded inside it, each with what was said whole; the untick and the agent's tick
            # are lines of their own.
            rows = page.locator("li.event")
            await expect(rows).to_have_count(3, timeout=SLOW)
            run = page.locator("li.event.event-run")
            await expect(run).to_have_count(1)
            await expect(run).to_have_attribute("data-count", "2")
            await expect(run).to_have_attribute("data-actor", ADA)
            await expect(run.locator(".event-words")).to_contain_text(
                f"ticked 2 checks on task T-{number(task)}"
            )
            await run.locator("summary", has_text="Show the checks").click()
            await expect(run.locator("li.run-check .run-check-words")).to_have_text(
                [re.compile(r"^“Tests pass”"), re.compile(r"^“A person tried it”")]
            )
            await expect(run.locator(".run-check-evidence")).to_have_text(how)
            await expect(
                page.locator("li.event[data-action=check_unticked] .event-words")
            ).to_contain_text(f"unticked “Tests pass” on task T-{number(task)}")

            # The Board's card says how many are ticked, as the one progress sign that also holds
            # the acceptance criteria (this task has none), with the checks in its words.
            await go(page, board, "#/board")
            chip = page.locator(f".card[data-ref='{task}'] .chip.progress-sign")
            await expect(chip).to_have_text("2/3", timeout=SLOW)
            await expect(chip).to_have_attribute("aria-label", "2 of 3 checks ticked")
            assert "ok" not in (await chip.get_attribute("class") or "").split()
            assert await page.locator(f".card[data-ref='{task}'] .chip.checks").count() == 0

            # The definition's own page says on how many each check is ticked, and what rewording
            # a ticked line would cost.
            await go(page, board, "#/definition")
            await expect(page.locator("#definition-checks-title")).to_have_text("3 checks")
            await expect(page.locator(".check-ticks-count")).to_have_text(
                ["ticked on 1 story or task", "ticked on 1 story or task"]
            )
            await page.locator("#edit-definition").click()
            reworded = "Before it is done:\n- Tests pass\n- A person tried it on a bike"
            await page.locator("#definition-body").fill(f"{reworded}\n- Notes are written")
            await expect(page.locator("#definition-count")).to_have_text("3 checks")
            await expect(page.locator("#definition-lost")).to_contain_text(
                "1 line you changed or removed has 1 tick"
            )

        # Someone who can only look sees the boxes and cannot change them.
        viewer = join_project(project, "viewer")
        async with browser_page(board, token=None, person=viewer) as v:
            await go(v.page, board, f"#/item?item={task}")
            part = v.page.locator("#item-page [data-part=definition]")
            await expect(part.locator("input.check-box")).to_have_count(3, timeout=SLOW)
            assert await part.locator("input.check-box:disabled").count() == 3
            assert await part.locator(".check-how").count() == 0


async def test_the_board_shows_size_and_checks_on_cards_and_has_a_key(server_url: str) -> None:
    async with fresh_project(server_url) as (client, board, project):
        set_definition(project, "- Tests pass\n- A person tried it")
        epic = await made(client, "epic", "Cards")
        story = await made(client, "story", "A story", epic)
        medium = await made(client, "task", "Medium one", story, size="m")
        await made(client, "task", "Large one", story, size="l")
        plain = await made(client, "task", "Not sized one", story)
        keys = await keys_of_checks(client)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await go(page, board, "#/board?show=both")
            card = page.locator(f".card[data-ref='{medium}']")
            await expect(card.locator(".chip.size")).to_have_text("M", timeout=SLOW)
            await expect(card.locator(".chip.size")).to_have_attribute("title", "Medium, 3 points")
            assert await page.locator(f".card[data-ref='{plain}'] .chip.size").count() == 0
            # What a story adds up to is on its own card; the epic's lane head holds no size.
            rollup = page.locator(f".card[data-ref='{story}'] .chip.size-rollup")
            await expect(rollup).to_have_text("8 pts")
            await expect(rollup).to_have_attribute("title", "8 points, 1 not sized")
            assert await page.locator(f".lane[data-ref='{epic}'] .lane-head .chip").count() == 0
            assert await card.locator(".chip.progress-sign").count() == 0

            # An agent ticks one of the two: the card counts it, and all of them is green.
            await ok(client, "set_check", node=medium, item=keys["Tests pass"], evidence="Passed")
            chip = card.locator(".chip.progress-sign")
            await expect(chip).to_have_text("1/2", timeout=NEWS)
            await expect(chip).to_have_attribute("aria-label", "1 of 2 checks ticked")
            await ok(
                client, "set_check", node=medium, item=keys["A person tried it"], evidence="Tried"
            )
            await expect(chip).to_have_text("2/2", timeout=NEWS)
            assert "ok" in (await chip.get_attribute("class") or "").split()
            # Finished work is in its column, so the card leaves the sign out.
            version = (await ok(client, "get_node", node=medium))["version"]
            await ok(client, "update_node", node=medium, expected_version=version, status="done")
            await expect(card.locator(".chip.progress-sign")).to_have_count(0, timeout=NEWS)

            # The Key says what the statuses and the signs are: each sign is drawn the way a card
            # draws it, and has its words beside it.
            await page.locator("#board-key-button").hover()
            key = page.locator(".hovercard:visible")
            await expect(key).to_be_visible()
            for words in ("New", "Ready", "In progress", "Blocked", "Done", "Dropped"):
                await expect(
                    key.locator(".bk-group.is-status strong", has_text=words)
                ).to_have_count(1)
            for sign in (
                ".chip.priority",
                ".chip.size-m",
                ".chip.size-rollup",
                ".chip.no-size",
                ".chip.progress-sign",
                ".chip.waiting",
                ".chip.handoff-sign",
                ".chip.held:not(.lapsed)",
                ".chip.held.lapsed",
                ".chip.due.danger.overdue",
                ".chip.conflict",
            ):
                await expect(key.locator(f".bk-card {sign}").first).to_be_visible()
            await expect(key.locator(".bk-card .chip.waiting")).to_have_text("")
            await expect(key).to_contain_text("Waiting for work that is not done yet")
            await expect(key).to_contain_text("Has a handoff note")
            await expect(key).to_contain_text("Not sized yet")
            await expect(key).to_contain_text("Late: past its due day")
            await page.keyboard.press("Escape")
            await expect(page.locator(".hovercard:visible")).to_have_count(0)
            await page.locator("#board-key-button").focus()
            await expect(page.locator(".hovercard:visible")).to_be_visible()


async def test_the_board_marks_open_work_that_has_no_size_with_a_faint_chip(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Cards with no size")
        loose = await made(client, "story", "Story with no tasks", epic)
        await made(client, "story", "Sized story with no tasks", epic, size="m")
        busy = await made(client, "story", "Story with tasks", epic)
        bare = await made(client, "task", "Bare task", busy)
        sized = await made(client, "task", "Sized task", busy, size="s")
        finished = await made(client, "task", "Finished bare task", busy)
        dropped = await made(client, "task", "Dropped bare task", busy)
        orphaned = await made(client, "story", "Story whose only task was dropped", epic)
        only = await made(client, "task", "Its only task", orphaned)
        for ref, status in ((finished, "done"), (dropped, "dropped"), (only, "dropped")):
            await ok(client, "update_node", node=ref, expected_version=1, status=status)

        # The chip is the very rule of the `no:size` filter, for work that is still open.
        asked = {
            one["ref"]
            for one in await all_nodes(client, query="no:size")
            if one["status"] not in ("done", "dropped")
        }
        assert asked == {loose, bare, orphaned}
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await go(page, board, "#/board?show=both")
            await expect(card(page, bare).locator(".chip.no-size")).to_have_attribute(
                "aria-label", "No size", timeout=SLOW
            )
            wearing = await page.locator(".card .chip.no-size").evaluate_all(
                "els => els.map(e => e.closest('.card').dataset.ref)"
            )
            assert set(wearing) == asked and len(wearing) == len(asked)
            assert await page.locator(".lane-head .chip.no-size").count() == 0

            # It is a quiet sign and not a control, fainter than a size's own chip.
            chip = card(page, bare).locator(".chip.no-size")
            assert await chip.evaluate("el => el.tagName") == "SPAN"
            style = await chip.evaluate(
                "el => { const s = getComputedStyle(el);"
                " return [s.backgroundColor, s.borderTopStyle, s.fontWeight]; }"
            )
            assert style == ["rgba(0, 0, 0, 0)", "dashed", "500"]
            size = card(page, sized).locator(".chip.size")
            assert await size.evaluate("el => getComputedStyle(el).fontWeight") == "600"
            assert await card(page, sized).locator(".chip.no-size").count() == 0

            # Sizing it, finishing it or giving a story tasks takes the chip off; the news is heard.
            version = (await ok(client, "get_node", node=bare))["version"]
            await ok(client, "update_node", node=bare, expected_version=version, size="l")
            await expect(card(page, bare).locator(".chip.no-size")).to_have_count(0, timeout=NEWS)
            await expect(card(page, bare).locator(".chip.size")).to_have_text("L")
            await ok(client, "update_node", node=loose, expected_version=1, status="done")
            await expect(card(page, loose).locator(".chip.no-size")).to_have_count(0, timeout=NEWS)
            child = await made(client, "task", "A new task", orphaned)
            await expect(card(page, child).locator(".chip.no-size")).to_have_count(1, timeout=NEWS)
            await expect(card(page, orphaned).locator(".chip.no-size")).to_have_count(
                0, timeout=NEWS
            )
            assert w.problems == [], w.problems


# -- checks only a person can judge --------------------------------------------------------------


async def test_a_check_only_a_person_can_judge_has_a_chip_and_a_person_can_tick_it(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        set_definition(
            project,
            "Before it is done:\n- Tests pass\n- [person] Looks right on a phone"
            "\n- [ ] [Person] Reads well",
        )
        epic = await made(client, "epic", "Person only")
        story = await made(client, "story", "A story", epic)
        task = await made(client, "task", "A task", story)
        words = ["Tests pass", "Looks right on a phone", "Reads well"]
        items = (await ok(client, "get_definition_of_done"))["items"]
        assert [(one["text"], one["person_only"]) for one in items] == list(
            zip(words, [False, True, True], strict=True)
        )
        keys = await keys_of_checks(client)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await go(page, board, f"#/item?item={task}")
            part = page.locator("#item-page [data-part=definition]")
            # The tag is not part of the words, and the chip is on the checks that carry it.
            await expect(part.locator(".check-text")).to_have_text(words, timeout=SLOW)
            rows = part.locator(".check-item")
            for at, wanted in enumerate([0, 1, 1]):
                await expect(rows.nth(at).locator(".chip.person-only")).to_have_count(wanted)
            chip = rows.nth(1).locator(".chip.person-only")
            await expect(chip).to_have_text("Person only")
            await expect(chip.locator("svg.icon-lock")).to_have_count(1)

            # A person ticks and unticks it as they would any other.
            box = rows.nth(1).locator("input.check-box")
            assert await box.is_enabled()
            await box.click()
            await expect(part.locator("h3")).to_contain_text("1 of 3 ticked", timeout=SLOW)
            ticked = (await ok(client, "get_node", node=task))["checks"][1]
            assert (ticked["checked"], ticked["checked_by"], ticked["checked_by_kind"]) == (
                True,
                ADA,
                "person",
            )
            assert ticked["person_only"] is True
            await box.click()
            await expect(rows.nth(1)).to_have_attribute("data-checked", "false", timeout=SLOW)
            assert (await ok(client, "get_node", node=task))["checks"][1]["checked"] is False
            await box.click()
            await expect(part.locator("h3")).to_contain_text("1 of 3 ticked", timeout=SLOW)

            # The definition's own page has the same chip beside the same checks, and the way a
            # line is written is in its Key and not in a sentence on the page.
            await go(page, board, "#/definition")
            lines = page.locator(".check-ticks li")
            await expect(lines.locator(".check-ticks-text")).to_have_text(words, timeout=SLOW)
            await expect(lines.locator(".chip.person-only")).to_have_count(2)
            await expect(lines.nth(0).locator(".chip.person-only")).to_have_count(0)
            await expect(lines.nth(1).locator(".check-ticks-count")).to_have_text(
                "ticked on 1 story or task"
            )
            assert "[person]" not in await page.locator("#definition-field").inner_text()
            await page.locator("#definition-key-button").hover()
            key = page.locator(".hovercard:visible")
            await expect(key).to_contain_text("[person]")
            await expect(key.locator(".chip.person-only")).to_have_count(1)
            await page.keyboard.press("Escape")
            await expect(page.locator(".hovercard:visible")).to_have_count(0)

            # A line's tag is not part of its words: adding it, taking it off or writing it after a
            # checkbox keeps the check and its tick; rewording the line does not.
            await page.locator("#edit-definition").click()
            body = page.locator("#definition-body")
            await body.fill(
                "Before it is done:\n- Tests pass\n- [PERSON] Looks right on a phone"
                "\n- [x] [person] Reads well"
            )
            await expect(page.locator("#definition-count")).to_have_text("3 checks")
            await expect(page.locator("#definition-lost")).to_have_count(0)
            await body.fill(
                "Before it is done:\n- Tests pass\n- Looks right on a phone\n- Reads well"
            )
            await expect(page.locator("#definition-count")).to_have_text("3 checks")
            await expect(page.locator("#definition-lost")).to_have_count(0)
            await body.fill(
                "Before it is done:\n- Tests pass\n- [person] Looks fine on a phone\n- Reads well"
            )
            await expect(page.locator("#definition-lost")).to_contain_text(
                "1 line you changed or removed has 1 tick"
            )
            assert (await keys_of_checks(client)) == keys
            assert w.problems == [], w.problems

        # Someone who can only look sees the chip, and the boxes they cannot change.
        viewer = join_project(project, "viewer")
        async with browser_page(board, token=None, person=viewer) as v:
            await go(v.page, board, f"#/item?item={task}")
            part = v.page.locator("#item-page [data-part=definition]")
            await expect(part.locator(".chip.person-only")).to_have_count(2, timeout=SLOW)
            assert await part.locator("input.check-box:disabled").count() == 3
