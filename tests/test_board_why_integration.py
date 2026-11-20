"""M33: a person who marks work blocked or dropped may say why, kept as a handoff note.

Driven in a real headless browser and compared with what a real MCP client reads (see ./check.sh).
Choosing Blocked or Dropped from a status menu, dropping a Board card into either column, and
changing the status in the Edit form each ask for a reason (one optional field), send it with the
change, and show it afterwards in the item's Handoff notes and in the Activity feed. Cancelling
changes nothing, an empty reason sends none, a refusal is shown under the field with the words
kept, and someone who can only look is never asked. Every project here is one of its own."""

from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_context import ADA, dialog, fresh_project, refusal_in
from tests.board_helpers import SLOW, browser_page, card, cell, go, hold_back_news, made
from tests.board_m14 import events_of, open_working, spoken
from tests.helpers import code_of, number_of, ok
from tests.world import join_project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NEWS = 20_000


async def handoffs(client: Client, ref: str) -> list[dict[str, Any]]:
    """The handoff notes an item has, oldest first."""
    return [e for e in await events_of(client, node=ref) if e["action"] == "handoff"]


async def status_of(client: Client, ref: str) -> str:
    return str((await ok(client, "get_node", node=ref))["status"])


async def pick(page: Page, value: str) -> None:
    """Open the status menu on the page of an item and choose a row."""
    await page.locator("#item-page .card-summary [data-menu=status]").click()
    await page.locator(f".vmenu[data-menu=status] .vmenu-option[data-value='{value}']").click()


async def settle(page: Page) -> None:
    """The dialog went and the page is quiet."""
    await expect(dialog(page)).to_have_count(0, timeout=SLOW)
    await page.wait_for_load_state("networkidle")


async def test_the_status_menu_asks_why_for_blocked_and_dropped(server_url: str) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Why it stopped")
        story = await made(client, "story", "A story", epic)
        first = await made(client, "task", "First task", story)
        second = await made(client, "task", "Second task", story)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await go(page, board, f"#/item?item={first}")
            chip = page.locator("#item-page .card-summary [data-menu=status]")
            was = await status_of(client, first)

            # The dialog says what it does in its title and has one field and two buttons.
            await pick(page, "blocked")
            box = dialog(page)
            await expect(box.locator("h2")).to_have_text(f"Mark {code_of(first)} as blocked")
            await expect(box.get_by_label("Why")).to_be_focused()
            assert await box.locator("textarea").count() == 1
            assert await box.locator("form > p:not(.problem)").count() == 0
            assert await box.get_by_role("button").all_inner_texts() == [
                "Cancel",
                "Mark as blocked",
            ]

            # Cancelling changes nothing and leaves nothing behind.
            await box.get_by_role("button", name="Cancel").click()
            await settle(page)
            assert await status_of(client, first) == was
            assert await handoffs(client, first) == []
            await expect(chip).to_have_attribute("data-status", was)

            # Writing a reason: it is sent with the change and kept as a handoff note.
            reason = "Waiting for the design to be signed off"
            await pick(page, "blocked")
            await dialog(page).get_by_label("Why").fill(reason)
            await dialog(page).get_by_role("button", name="Mark as blocked").click()
            await settle(page)
            await expect(chip).to_have_attribute("data-status", "blocked", timeout=SLOW)
            assert await status_of(client, first) == "blocked"
            [note] = await handoffs(client, first)
            assert (note["actor"], note["actor_kind"]) == (ADA, "person")
            assert (note["after"]["outcome"], note["after"]["note"]) == ("blocked", reason)

            # It shows in the item's Handoff notes, by the person who left it.
            part = page.locator("#item-page [data-part=handoff]")
            await expect(part.locator(".handoff-item[data-outcome=blocked]")).to_have_count(
                1, timeout=SLOW
            )
            await expect(part).to_contain_text(reason)
            await expect(part.locator(".handoff-by .actor-person")).to_contain_text(ADA)

            # The value it already has is not a change, so nothing is asked.
            await pick(page, "blocked")
            await expect(dialog(page)).to_have_count(0)
            assert len(await handoffs(client, first)) == 1

            # Left empty, the status changes and no note is made.
            await go(page, board, f"#/item?item={second}")
            await pick(page, "dropped")
            await expect(dialog(page).locator("h2")).to_have_text(
                f"Mark {code_of(second)} as dropped"
            )
            await dialog(page).get_by_role("button", name="Mark as dropped").click()
            await settle(page)
            await expect(chip).to_have_attribute("data-status", "dropped", timeout=SLOW)
            assert await status_of(client, second) == "dropped"
            assert await handoffs(client, second) == []

            # In the Activity feed the reason is told in plain words for a person, in full, and the
            # person is listed as having left a handoff note.
            await go(page, board, "#/activity")
            row = page.locator(f"li.event[data-action=handoff][data-event='{note['id']}']")
            await expect(row).to_contain_text(f"{ADA} marked task {code_of(first)}", timeout=SLOW)
            await expect(row).to_contain_text("as blocked and left this handoff note")
            assert spoken(await row.locator(".handoff-note").inner_text()) == reason
            await open_working(page)
            agent = page.locator(f"#agents li.agent[data-actor='{ADA}']")
            await expect(agent).to_have_attribute("data-actor-kind", "person", timeout=SLOW)
            await expect(agent).to_contain_text("Left a handoff note")
            await expect(agent).to_contain_text("Last handoff: blocked")
            assert w.problems == [], w.problems


async def test_a_refusal_is_shown_under_the_field_and_the_words_are_kept(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Refused")
        story = await made(client, "story", "A story", epic)
        task = await made(client, "task", "A task", story)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await go(page, board, f"#/item?item={task}")
            was = await status_of(client, task)
            # Someone else changes the task after the page last heard of it.
            await hold_back_news(page)
            version = (await ok(client, "get_node", node=task))["version"]
            await ok(client, "update_node", node=task, expected_version=version, priority="high")

            await pick(page, "dropped")
            reason = "It is covered by another task"
            await dialog(page).get_by_label("Why").fill(reason)
            await dialog(page).get_by_role("button", name="Mark as dropped").click()
            await expect(refusal_in(page)).to_contain_text("version", timeout=SLOW)
            # Nothing was written, and what was typed is still there to change or send again.
            await expect(dialog(page).get_by_label("Why")).to_have_value(reason)
            assert await status_of(client, task) == was
            assert await handoffs(client, task) == []
            await dialog(page).get_by_role("button", name="Cancel").click()
            await settle(page)
            assert w.problems == [], w.problems


async def test_dropping_a_card_into_blocked_or_dropped_asks_why(server_url: str) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Board")
        story = await made(client, "story", "A story", epic)
        blocked = await made(client, "task", "Will be blocked", story)
        kept = await made(client, "task", "Stays where it is", story)
        quiet = await made(client, "task", "Dropped with no reason", story)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await go(page, board, f"#/board?epic={number_of(epic)}")
            lane_of = f".cell[data-epic='{epic}']"
            for ref in (blocked, kept, quiet):
                await expect(card(page, ref)).to_be_visible(timeout=SLOW)

            # Into Blocked: asked, written, and the card moves once the server said yes.
            start = await status_of(client, blocked)
            await page.drag_and_drop(
                f".card[data-ref='{blocked}']", f"{lane_of}[data-status='blocked']"
            )
            await expect(dialog(page).locator("h2")).to_have_text(
                f"Mark {code_of(blocked)} as blocked"
            )
            # The card is where it was while the question is open.
            await expect(
                cell(page, epic, start).locator(f".card[data-ref='{blocked}']")
            ).to_be_visible()
            reason = "Needs the vendor's answer first"
            await dialog(page).get_by_label("Why").fill(reason)
            await dialog(page).get_by_role("button", name="Mark as blocked").click()
            await settle(page)
            await expect(
                cell(page, epic, "blocked").locator(f".card[data-ref='{blocked}']")
            ).to_be_visible(timeout=SLOW)
            [note] = await handoffs(client, blocked)
            assert (note["after"]["outcome"], note["after"]["note"]) == ("blocked", reason)
            assert note["actor_kind"] == "person"
            # Its card says it has a handoff note.
            await expect(card(page, blocked).locator(".handoff-sign")).to_be_visible(timeout=NEWS)

            # Cancelling leaves the card in its column, and the board is drawn again.
            start = await status_of(client, kept)
            await page.drag_and_drop(
                f".card[data-ref='{kept}']", f"{lane_of}[data-status='dropped']"
            )
            await expect(dialog(page).locator("h2")).to_have_text(
                f"Mark {code_of(kept)} as dropped"
            )
            await dialog(page).get_by_role("button", name="Cancel").click()
            await settle(page)
            await expect(
                cell(page, epic, start).locator(f".card[data-ref='{kept}']")
            ).to_be_visible()
            assert await cell(page, epic, "dropped").locator(".card").count() == 0
            assert await status_of(client, kept) == start
            assert await handoffs(client, kept) == []

            # Escape is a cancel too.
            await page.drag_and_drop(
                f".card[data-ref='{kept}']", f"{lane_of}[data-status='blocked']"
            )
            await expect(dialog(page)).to_be_visible()
            await page.keyboard.press("Escape")
            await settle(page)
            await expect(
                cell(page, epic, start).locator(f".card[data-ref='{kept}']")
            ).to_be_visible()
            assert await status_of(client, kept) == start

            # Left empty, the card moves and there is no note.
            await page.drag_and_drop(
                f".card[data-ref='{quiet}']", f"{lane_of}[data-status='dropped']"
            )
            await dialog(page).get_by_role("button", name="Mark as dropped").click()
            await settle(page)
            await expect(
                cell(page, epic, "dropped").locator(f".card[data-ref='{quiet}']")
            ).to_be_visible(timeout=SLOW)
            assert await status_of(client, quiet) == "dropped"
            assert await handoffs(client, quiet) == []
            assert w.problems == [], w.problems


async def test_the_edit_form_asks_why_only_while_it_changes_the_status_to_blocked_or_dropped(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Edit")
        story = await made(client, "story", "A story", epic)
        task = await made(client, "task", "A task", story)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await go(page, board, f"#/item?item={task}")
            await page.locator("#edit-open").click()
            why = page.locator("#edit-why")
            status = page.locator("#edit-status")
            await expect(why).to_be_hidden()
            await status.select_option("blocked")
            await expect(why).to_be_visible()
            await expect(page.get_by_label("Why")).to_be_visible()
            await status.select_option("in_progress")
            await expect(why).to_be_hidden()
            await status.select_option("dropped")
            await why.fill("Replaced by the new login")
            await page.get_by_role("button", name="Save changes").click()
            await expect(
                page.locator("#item-page .card-summary [data-menu=status]")
            ).to_have_attribute("data-status", "dropped", timeout=SLOW)
            [note] = await handoffs(client, task)
            assert (note["after"]["outcome"], note["after"]["note"]) == (
                "dropped",
                "Replaced by the new login",
            )
            assert note["actor"] == ADA
            part = page.locator("#item-page [data-part=handoff]")
            await expect(part).to_contain_text("Replaced by the new login", timeout=SLOW)

            # Already dropped: choosing it again is no change, so no reason is asked for.
            await page.locator("#edit-open").click()
            await expect(why).to_be_hidden()
            await status.select_option("blocked")
            await expect(why).to_be_visible()
            await status.select_option("dropped")
            await expect(why).to_be_hidden()
            assert w.problems == [], w.problems


async def test_someone_who_can_only_look_is_never_asked_why(server_url: str) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Read only")
        story = await made(client, "story", "A story", epic)
        task = await made(client, "task", "A task", story)
        viewer = join_project(project, "viewer")
        async with browser_page(board, token=None, person=viewer) as v:
            page = v.page
            await go(page, board, f"#/item?item={task}")
            chip = page.locator("#item-page .card-summary .chip.status")
            await expect(chip).to_be_visible(timeout=SLOW)
            assert await chip.evaluate("el => el.tagName") == "SPAN"
            assert await page.locator("#item-page [data-menu=status]").count() == 0
            assert await page.locator("#edit-open").count() == 0
            await go(page, board, f"#/board?epic={number_of(epic)}")
            await expect(card(page, task)).to_have_attribute("draggable", "false", timeout=SLOW)
            assert await page.locator("dialog.modal").count() == 0
            assert v.problems == [], v.problems
