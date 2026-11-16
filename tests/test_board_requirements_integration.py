"""M16's requirements (rules, as the tools call them) against the running containers: written,
edited, placed in a story and moved to another from the Graph's context list and from the coverage
report in the shared detail panel, in a real headless browser, and checked against a real MCP client
(`get_rule`, `rule_history`, `coverage_report`). Every refusal is compared word for word with what
the same attempt gets through MCP. Each test works in a project of its own, so that the coverage
report is small enough to compare in full."""

import re
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Locator, Page, expect

from tests.board_context import (
    ADA,
    coverage_numbers,
    dialog,
    fresh_project,
    graph_open,
    listed,
    panel,
    refusal_in,
)
from tests.board_helpers import SLOW, browser_page, hold_back_news, made, plain
from tests.helpers import ok, refused, whole_coverage_report
from tests.world import Project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

LIST = "#/graph?view=context&as=list"
# The tools call a requirement a rule; the board does not.
RULE_WORD = re.compile(r"\brules?\b", re.I)


def rule_row(page: Page, rule: int) -> Locator:
    return listed(page, f"rule:{rule}")


def _browser(board: str, project: Project) -> Any:
    return browser_page(board, token=project.key, name=ADA)


def group(page: Page, story: int) -> Locator:
    return page.locator(f"#requirement-groups section[data-group='{story}']")


async def rule_in(client: Client, title: str, story: int | None = None, **more: Any) -> int:
    fields: dict[str, Any] = {"actor": "agent-1", "title": title, **more}
    if story:
        fields["story_id"] = story
    return int((await ok(client, "create_rule", **fields))["id"])


async def history(client: Client, rule: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = (await ok(client, "rule_history", rule_id=rule, limit=200))[
        "items"
    ]
    return items


async def summary_says(page: Page, client: Client) -> tuple[int, int]:
    """The sentence above the requirements gives the numbers the coverage report gives."""
    unplaced, bare = await coverage_numbers(client)
    summary = page.locator("#requirements-summary")
    await expect(summary).to_have_attribute("data-unplaced", str(unplaced), timeout=SLOW)
    await expect(summary).to_have_attribute("data-stories-without", str(bare))
    one = "requirement is" if unplaced == 1 else "requirements are"
    await expect(summary).to_contain_text(f"{unplaced} {one} in no story.")
    await expect(summary).to_contain_text(
        f"{bare} {'story has' if bare == 1 else 'stories have'} no"
    )
    return unplaced, bare


async def move_by_drag(page: Page, source: Locator, target: Locator) -> None:
    """Drag a requirement onto a story with the browser's own drag events, so that a long page
    cannot stop the mouse from reaching it."""
    transfer = await page.evaluate_handle("new DataTransfer()")
    await source.dispatch_event("dragstart", {"dataTransfer": transfer})
    await target.dispatch_event("dragover", {"dataTransfer": transfer})
    await target.dispatch_event("drop", {"dataTransfer": transfer})
    await source.dispatch_event("dragend", {"dataTransfer": transfer})


# -- an unassigned requirement: written, placed in a story, moved to another -----------------------


async def test_a_new_requirement_is_unassigned_until_it_is_placed_and_then_moved_in_one_step(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Requirements")
        first = await made(client, "story", "First story", epic)
        second = await made(client, "story", "Second story", epic)
        third = await made(client, "story", "Third story", epic)
        anchor = await rule_in(client, "Anchor", second)
        async with _browser(board, project) as w:
            page = w.page
            await graph_open(w, LIST)
            await summary_says(page, client)
            # The words are Requirements, here, in the buttons and in the rows.
            await expect(page.locator("#ctx-requirements-heading")).to_have_text("Requirements")
            await expect(page.locator("#new-requirement")).to_have_text("New requirement")
            wording = await page.locator("#ctx-requirements").text_content() or ""
            assert not RULE_WORD.search(wording), RULE_WORD.findall(wording)

            await page.locator("#new-requirement").click()
            box = dialog(page)
            await expect(box.locator("h2")).to_have_text("New requirement")
            assert not RULE_WORD.search(await box.text_content() or "")
            await box.locator("#requirement-title").fill("Created in the browser")
            await box.locator("#requirement-body").fill("Typed in the browser.")
            await box.get_by_role("button", name="Create the requirement").click()
            newest = page.locator("#unplaced-group li.requirement").first
            await expect(newest).to_contain_text("Created in the browser", timeout=SLOW)
            rule = int(await newest.get_attribute("data-id") or 0)
            made_rule = await ok(client, "get_rule", rule_id=rule)
            assert (made_rule["story_id"], made_rule["status"], made_rule["version"]) == (
                None,
                "held",
                1,
            )
            assert made_rule["body"] == "Typed in the browser."
            await expect(newest.locator(".chip.kind")).to_contain_text(f"Requirement #{rule}")
            await expect(newest).to_have_attribute("data-status", "held")
            # Unassigned comes first: above the groups of stories, and above the stories that have
            # no requirement.
            order = await page.evaluate(
                "[...document.querySelectorAll('#unplaced-group, #requirement-groups,"
                " #stories-without-requirements')].map(e => e.id)"
            )
            assert order == ["unplaced-group", "requirement-groups", "stories-without-requirements"]
            assert await summary_says(page, client) == (1, 2)
            assert (
                "no requirement"
                in (
                    await page.locator("#stories-without-requirements summary").inner_text()
                ).lower()
            )

            # Place it in the first story: it leaves the unassigned ones and the numbers follow.
            await newest.get_by_role("button", name="Put in a story…").click()
            box = dialog(page)
            assert not RULE_WORD.search(await box.text_content() or "")
            assert await box.locator("#requirement-target").evaluate(
                "e => document.activeElement === e"
            )
            await box.locator("#requirement-target").select_option(str(first))
            await box.get_by_role("button", name="Put in the story").click()
            await expect(rule_row(page, rule)).to_have_attribute(
                "data-story", str(first), timeout=SLOW
            )
            await expect(group(page, first).locator(f"li[data-id='{rule}']")).to_be_visible()
            await expect(page.locator(f"#unplaced-group li[data-id='{rule}']")).to_have_count(0)
            assert await summary_says(page, client) == (0, 1)
            placed = await ok(client, "get_rule", rule_id=rule)
            assert (placed["story_id"], placed["version"]) == (first, 2)

            # A requirement in a story is moved, from a dialog, in one step, to another story.
            await rule_row(page, rule).get_by_role("button", name="Move to another story…").click()
            box = dialog(page)
            await box.locator("#requirement-target").select_option(str(second))
            await box.get_by_role("button", name="Move to the story").click()
            await expect(group(page, second).locator(f"li[data-id='{rule}']")).to_be_visible(
                timeout=SLOW
            )
            await expect(group(page, first)).to_have_count(0)
            assert await summary_says(page, client) == (0, 2)
            # And again by dragging it onto a story that has nothing yet.
            await page.locator("#stories-without-requirements summary").click()
            bare = page.locator(f"#stories-without-requirements li[data-story='{first}']")
            await expect(bare).to_be_visible()
            await move_by_drag(page, rule_row(page, rule), bare)
            await expect(group(page, first).locator(f"li[data-id='{rule}']")).to_be_visible(
                timeout=SLOW
            )
            assert w.problems == [], w.problems

        moved = await ok(client, "get_rule", rule_id=rule)
        assert (moved["story_id"], moved["version"]) == (first, 4)
        events = await history(client, rule)
        assert [(e["action"], e["actor"]) for e in events] == [
            ("rule_created", ADA),
            ("rule_placed", ADA),
            ("rule_moved", ADA),
            ("rule_moved", ADA),
        ]
        assert events[2]["before"] == {"story_id": first, "version": 2}
        assert events[2]["after"] == {"story_id": second, "version": 3}
        for story in (first, second):
            story_events = (await ok(client, "node_history", node_id=story, limit=200))["items"]
            assert events[2]["id"] in [e["id"] for e in story_events]
        in_first = (await ok(client, "list_rules", story_id=first))["items"]
        assert [r["id"] for r in in_first] == [rule]
        assert sorted(
            r["id"] for r in (await ok(client, "list_rules", story_id=second))["items"]
        ) == [anchor]
        assert third and (await coverage_numbers(client)) == (0, 1)


# -- editing, stale and blank changes, dropped requirements and the keyboard --------------


async def test_a_requirement_is_edited_with_its_version_and_stale_or_blank_changes_are_refused(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Edits")
        story = await made(client, "story", "Edited story", epic)
        other = await made(client, "story", "Other story", epic)
        rule = await rule_in(client, "Original", story, body="First words.")
        loose = await rule_in(client, "Loose")
        gone = await rule_in(client, "Gone", story, status="dropped")
        async with _browser(board, project) as w:
            page = w.page
            await graph_open(w, LIST)
            row = rule_row(page, rule)
            await expect(row).to_contain_text("First words.", timeout=SLOW)

            # A change with nothing changed says so; a changed one is saved at the version read.
            await row.get_by_role("button", name="Edit", exact=True).click()
            box = dialog(page)
            await box.get_by_role("button", name="Save changes").click()
            await expect(refusal_in(page)).to_contain_text("Nothing has been changed")
            await box.locator("#requirement-title").fill("Better")
            await box.locator("#requirement-body").fill("Second words.")
            await box.locator("#requirement-status").select_option("settled")
            await box.get_by_role("button", name="Save changes").click()
            await expect(box).to_have_count(0, timeout=SLOW)
            await expect(row).to_contain_text("Better")
            await expect(row).to_have_attribute("data-status", "settled")
            await expect(row.locator(".rule-status")).to_have_text("Settled")
            saved = await ok(client, "get_rule", rule_id=rule)
            assert (saved["title"], saved["body"], saved["status"], saved["version"]) == (
                "Better",
                "Second words.",
                "settled",
                2,
            )
            (_, update) = await history(client, rule)
            assert (update["action"], update["actor"]) == ("rule_updated", ADA)
            assert update["before"]["title"] == "Original" and update["after"]["version"] == 2

            # A blank title is refused as an agent's is, and writes nothing.
            total = (await ok(client, "list_rules", limit=1))["total"]
            blank = await refused(
                client,
                "create_rule",
                actor="x",
                title="",
                body="",
                status="held",
                story_id=None,
            )
            await page.locator("#new-requirement").click()
            box = dialog(page)
            await box.locator("#requirement-body").fill("A body with no title")
            await box.get_by_role("button", name="Create the requirement").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(await refusal_in(page).inner_text(), blank)
            assert await box.locator("#requirement-body").input_value() == "A body with no title"
            assert (await ok(client, "list_rules", limit=1))["total"] == total
            await box.get_by_role("button", name="Cancel").click()

            # Moving it to the story it is already in is refused in the server's words.
            same = await refused(
                client, "move_rule", actor="x", rule_id=rule, expected_version=2, new_story_id=story
            )
            await row.get_by_role("button", name="Move to another story…").click()
            box = dialog(page)
            await box.locator("#requirement-target").select_option(str(story))
            await box.get_by_role("button", name="Move to the story").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(await refusal_in(page).inner_text(), same)
            await box.get_by_role("button", name="Cancel").click()
            assert (await ok(client, "get_rule", rule_id=rule))["version"] == 2

            # Dropped requirements are left out until asked for; the lists say what list_rules says.
            await expect(rule_row(page, gone)).to_have_count(0)
            await page.locator("#ctx-requirements-status").select_option("dropped")
            await expect(rule_row(page, gone)).to_have_attribute("data-status", "dropped")
            await expect(rule_row(page, gone).locator(".rule-status")).to_have_text("Dropped")
            await expect(rule_row(page, rule)).to_have_count(0)
            await page.locator("#ctx-requirements-status").select_option("all")
            in_story = (
                await group(page, story)
                .locator("li.requirement")
                .evaluate_all("els => els.map(e => Number(e.dataset.id))")
            )
            listed_by_server = (await ok(client, "list_rules", story_id=story))["items"]
            assert sorted(in_story) == sorted(r["id"] for r in listed_by_server) == [rule, gone]
            await page.locator("#ctx-requirements-placement").select_option("unplaced")
            await expect(rule_row(page, rule)).to_have_count(0)
            await expect(rule_row(page, loose)).to_be_visible()
            await page.locator("#ctx-requirements-placement").select_option("")
            await page.locator("#ctx-requirements-status").select_option("live")

            # From the keyboard: the dialog opens with the story list focused, and Escape gives
            # the focus back to the button that opened it.
            button = rule_row(page, loose).get_by_role("button", name="Put in a story…")
            await button.focus()
            await page.keyboard.press("Enter")
            await expect(dialog(page)).to_be_visible()
            assert await page.evaluate("document.activeElement.id") == "requirement-target"
            await page.keyboard.press("Escape")
            await expect(dialog(page)).to_have_count(0)
            await expect(button).to_be_focused()

            # An agent changes things the board has not heard of: each change from the board is
            # refused in the words of MCP, and the dialog keeps what was typed.
            await hold_back_news(page)
            await ok(
                client,
                "update_rule",
                actor="agent-1",
                rule_id=rule,
                expected_version=2,
                title="Agent's title",
            )
            stale = await refused(
                client, "update_rule", actor="x", rule_id=rule, expected_version=2, title="x"
            )
            await row.get_by_role("button", name="Edit", exact=True).click()
            box = dialog(page)
            await box.locator("#requirement-title").fill("Mine")
            await box.get_by_role("button", name="Save changes").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(await refusal_in(page).inner_text(), stale)
            assert await box.locator("#requirement-title").input_value() == "Mine"
            assert (await ok(client, "get_rule", rule_id=rule))["title"] == "Agent's title"
            await box.get_by_role("button", name="Cancel").click()

            await ok(
                client,
                "place_rule",
                actor="agent-1",
                rule_id=loose,
                expected_version=1,
                story_id=other,
            )
            already = await refused(
                client, "place_rule", actor="x", rule_id=loose, expected_version=1, story_id=story
            )
            await button.click()
            box = dialog(page)
            await box.locator("#requirement-target").select_option(str(story))
            await box.get_by_role("button", name="Put in the story").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(await refusal_in(page).inner_text(), already)
            await box.get_by_role("button", name="Cancel").click()
            assert (await ok(client, "get_rule", rule_id=loose))["story_id"] == other
            assert w.problems == [], w.problems


# -- coverage: the report in the panel, and a drop on one of its stories ---------------------------


async def test_the_coverage_report_equals_coverage_report_and_a_drop_on_a_story_places_or_moves(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic_a = await made(client, "epic", "Coverage A")
        epic_b = await made(client, "epic", "Coverage B")
        s1 = await made(client, "story", "Story one", epic_a)
        s2 = await made(client, "story", "Story two", epic_a)
        s3 = await made(client, "story", "Story three", epic_a)
        s4 = await made(client, "story", "Story four", epic_b)
        placed = await rule_in(client, "Placed", s1)
        await rule_in(client, "Dropped", s2, status="dropped")
        loose_a = await rule_in(client, "Loose A")
        loose_b = await rule_in(client, "Loose B")
        async with _browser(board, project) as w:
            page = w.page
            # Scoped to one epic, the requirements in no story are still listed (nothing else would
            # lead a person to them), and the sentence above them says what the whole project says.
            await graph_open(w, f"{LIST}&epic={epic_b}")
            await expect(rule_row(page, loose_a)).to_be_visible(timeout=SLOW)
            await expect(rule_row(page, loose_b)).to_be_visible()
            await expect(rule_row(page, placed)).to_have_count(0)
            assert await summary_says(page, client) == (2, 3)

            # The coverage report, in the shared detail panel, is the server's report.
            await page.locator("#show-coverage").click()
            lists = panel(page).locator(".coverage-lists")
            await expect(lists.locator("[data-part=unplaced-requirements]")).to_be_visible(
                timeout=SLOW
            )
            report = await whole_coverage_report(client)
            assert (report["unplaced_rule_count"], report["story_without_rules_count"]) == (2, 3)
            await expect(lists.locator("[data-part=unplaced-requirements] h3")).to_have_text(
                "Requirements in no story (2)"
            )
            await expect(lists.locator("[data-part=stories-without-requirements] h3")).to_have_text(
                "Stories with no requirement (3)"
            )
            unplaced_ids = await lists.locator("li[data-rule]").evaluate_all(
                "els => els.map(e => Number(e.dataset.rule))"
            )
            story_ids = await lists.locator("li[data-story]").evaluate_all(
                "els => els.map(e => Number(e.dataset.story))"
            )
            assert unplaced_ids == [r["id"] for r in report["unplaced_rules"]]
            assert story_ids == [s["id"] for s in report["stories_without_rules"]]
            assert sorted(story_ids) == [s2, s3, s4]  # a story whose only requirement is dropped

            # Dragging a requirement that is in no story onto a story of the report places it.
            await w.go(f"{LIST}&epic={epic_a}&panel=coverage")
            await expect(rule_row(page, loose_a)).to_be_visible(timeout=SLOW)
            await expect(lists.locator(f"li[data-story='{s3}']")).to_have_count(1, timeout=SLOW)
            source = f"#graph-list [data-key='rule:{loose_a}']"
            await page.drag_and_drop(source, f"#panel li[data-story='{s3}']")
            await expect(rule_row(page, loose_a)).to_have_attribute(
                "data-story", str(s3), timeout=SLOW
            )
            placed_now = await ok(client, "get_rule", rule_id=loose_a)
            assert (placed_now["story_id"], placed_now["version"]) == (s3, 2)
            assert await summary_says(page, client) == (1, 2)
            await expect(lists.locator("[data-part=unplaced-requirements] h3")).to_have_text(
                "Requirements in no story (1)", timeout=SLOW
            )
            await expect(lists.locator(f"li[data-story='{s3}']")).to_have_count(0)

            # Dragging one that is in a story moves it, in one step, and the report follows.
            await move_by_drag(
                page, rule_row(page, loose_a), lists.locator(f"li[data-story='{s2}']")
            )
            await expect(rule_row(page, loose_a)).to_have_attribute(
                "data-story", str(s2), timeout=SLOW
            )
            moved = await ok(client, "get_rule", rule_id=loose_a)
            assert (moved["story_id"], moved["version"]) == (s2, 3)
            assert await summary_says(page, client) == (1, 2)
            await expect(lists.locator(f"li[data-story='{s3}']")).to_have_count(1, timeout=SLOW)
            await expect(lists.locator(f"li[data-story='{s2}']")).to_have_count(0)
            # A drop on the story it is already in changes nothing.
            await move_by_drag(
                page,
                rule_row(page, loose_a),
                page.locator(f"#requirement-groups section[data-group='{s2}'] h3[data-drop-story]"),
            )
            await page.wait_for_load_state("networkidle")
            assert (await ok(client, "get_rule", rule_id=loose_a))["version"] == 3
            assert w.problems == [], w.problems
        events = await history(client, loose_a)
        assert [(e["action"], e["actor"]) for e in events] == [
            ("rule_created", "agent-1"),
            ("rule_placed", ADA),
            ("rule_moved", ADA),
        ]
        assert (await coverage_numbers(client)) == (1, 2)
