"""M16's acceptance criteria (rules, as the tools call them) against the running containers:
written, edited, placed on a story or a task and moved to another from a criterion's own page, from
the Graph's context list and from the coverage report's page, in a real headless browser, and
checked
against a real MCP client (`get_rule`, `rule_history`, `coverage_report`). Since M20 an acceptance
criterion has a page of its own (`#/item?item=rule:3&from=graph`) and the coverage report is a page
(`#/coverage`): nothing opens beside a page. Every refusal is compared word for word with what the
same attempt gets through MCP. Each test works in a project of its own, so that the coverage report
is small enough to compare in full. A story or task is named by its reference (`story:12`): the
acceptance criterion's `node` is one, and so are the `data-node` and `data-group` of the page."""

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
    refusal_in,
)
from tests.board_helpers import (
    SLOW,
    Watched,
    browser_page,
    crumbs,
    current_address,
    detail,
    go,
    hold_back_news,
    item_loaded,
    item_page,
    made,
    no_panel_or_drawer,
    on_page,
    open_editor,
    open_item,
    plain,
    record_rows,
)
from tests.helpers import number_of as number
from tests.helpers import ok, refused, whole_coverage_report
from tests.world import Project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

LIST = "#/lists?view=requirements"
# The tools call an acceptance criterion a rule; the board does not.
RULE_WORD = re.compile(r"\brules?\b", re.I)
# The name of the agent key the tests use: who the history says made a change through MCP.
AGENT = "tests"


async def graph_open(w: Watched, address: str) -> None:
    """Open the acceptance criteria of Lists at an address, and wait until they are drawn."""
    await go(w.page, w.board, address)
    await expect(w.page.locator("#requirements-summary")).to_have_attribute(
        "data-unplaced", re.compile(r"^\d+$"), timeout=SLOW
    )


def rule_row(page: Page, rule: int) -> Locator:
    return page.locator(f"li.gv-record[data-key='rule:{rule}']")


def _browser(board: str, project: Project) -> Any:
    return browser_page(board, token=project.key, name=ADA)


def group(page: Page, story: str) -> Locator:
    return page.locator(f"#requirement-groups section[data-group='{story}']")


async def rule_in(client: Client, title: str, story: str | None = None, **more: Any) -> int:
    """Write a criterion through MCP, on a story or a task (`story` is its reference)."""
    fields: dict[str, Any] = {"actor": "agent-1", "title": title, **more}
    if story:
        fields["node"] = story
    return int((await ok(client, "create_rule", **fields))["id"])


async def history(client: Client, rule: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = (await ok(client, "rule_history", rule_id=rule, limit=200))[
        "items"
    ]
    return items


async def summary_says(page: Page, client: Client) -> tuple[int, int]:
    """The sentence above the acceptance criteria gives the numbers the coverage report gives."""
    unplaced, bare = await coverage_numbers(client)
    summary = page.locator("#requirements-summary")
    await expect(summary).to_have_attribute("data-unplaced", str(unplaced), timeout=SLOW)
    await expect(summary).to_have_attribute("data-stories-without", str(bare))
    one = "acceptance criterion is" if unplaced == 1 else "acceptance criteria are"
    await expect(summary).to_contain_text(f"{unplaced} {one} placed nowhere.")
    await expect(summary).to_contain_text(
        f"{bare} {'story has' if bare == 1 else 'stories have'} no"
    )
    return unplaced, bare


async def page_says(page: Page, client: Client, rule: int) -> dict[str, Any]:
    """The page of an acceptance criterion shows what `get_rule` says: its number and title, its
    status, the story or task it is on (or that it is on none), and the way to put it on one or
    move it. Gives what the server says."""
    server = await ok(client, "get_rule", rule_id=rule)
    await item_loaded(page, f"rule:{rule}")
    article = detail(page, f"rule:{rule}")
    await expect(article).to_have_attribute("data-status", server["status"], timeout=SLOW)
    await expect(article).to_have_attribute("data-node", server["node"] or "")
    title = page.locator("#item-title")
    await expect(title).to_contain_text(f"Acceptance criterion #{rule}")
    await expect(title).to_contain_text(server["title"])
    chip = page.locator("#item-page .chip.rule-status")
    await expect(chip).to_have_text(server["status"].capitalize())
    place = page.locator("#item-page button.place-requirement")
    if server["node"] is None:
        await expect(page.locator("#item-what")).to_have_text(
            "It is not placed on a story or task yet."
        )
        await expect(place).to_have_text("Put on a story or task…")
    else:
        holder = await ok(client, "get_node", node=server["node"])
        what = page.locator("#item-what")
        letter = "S" if holder["kind"] == "story" else "T"
        await expect(what).to_contain_text(f"On {letter}-{holder['number']} {holder['title']}")
        assert await what.locator("a").get_attribute("href") is not None
        await expect(place).to_have_text("Move to another story or task…")
    return dict(server)


async def choose_place(box: Locator, ref: str) -> None:
    """Pick the story or task in the picker of a dialog that puts an acceptance criterion
    somewhere: the list shows every story and task, grouped by epic."""
    await box.locator(f".holder-option[data-ref='{ref}']").click()
    await expect(box.locator(f".holder-option[data-ref='{ref}']")).to_have_attribute(
        "aria-selected", "true"
    )


async def move_by_drag(page: Page, source: Locator, target: Locator) -> None:
    """Drag an acceptance criterion onto a story or task with the browser's own drag events, so
    that a long page cannot stop the mouse from reaching it."""
    transfer = await page.evaluate_handle("new DataTransfer()")
    await source.dispatch_event("dragstart", {"dataTransfer": transfer})
    await target.dispatch_event("dragover", {"dataTransfer": transfer})
    await target.dispatch_event("drop", {"dataTransfer": transfer})
    await source.dispatch_event("dragend", {"dataTransfer": transfer})


# -- an unassigned criterion: written, placed on a story, moved to another -------------------------


async def test_a_new_criterion_is_unplaced_until_it_is_placed_and_then_moved_in_one_step(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Criteria")
        first = await made(client, "story", "First story", epic)
        second = await made(client, "story", "Second story", epic)
        third = await made(client, "story", "Third story", epic)
        anchor = await rule_in(client, "Anchor", second)
        async with _browser(board, project) as w:
            page = w.page
            await graph_open(w, LIST)
            await summary_says(page, client)
            # The words are Acceptance criteria, here, in the buttons and in the rows.
            await expect(page.locator("#ctx-requirements-heading")).to_have_text(
                "Acceptance criteria"
            )
            await expect(page.locator("#new-requirement")).to_have_text(
                "Add an acceptance criterion…"
            )
            wording = await page.locator("#ctx-requirements").text_content() or ""
            assert not RULE_WORD.search(wording), RULE_WORD.findall(wording)
            assert "requirement" not in wording.lower()

            # Writing one goes to its own page, in the Graph, with what was typed.
            await page.locator("#new-requirement").click()
            box = dialog(page)
            await expect(box.locator("h2")).to_have_text("Add an acceptance criterion")
            assert not RULE_WORD.search(await box.text_content() or "")
            await box.locator("#requirement-title").fill("Created in the browser")
            await box.locator("#requirement-body").fill("Typed in the browser.")
            await box.get_by_role("button", name="Add the acceptance criterion").click()
            await expect(item_page(page)).to_have_attribute("data-kind", "rule", timeout=SLOW)
            ref = await item_page(page).get_attribute("data-ref") or ""
            rule = int(ref.split(":")[1])
            await item_loaded(page, ref)
            made_rule = await ok(client, "get_rule", rule_id=rule)
            assert (made_rule["node"], made_rule["status"], made_rule["version"]) == (
                None,
                "open",
                1,
            )
            assert made_rule["body"] == "Typed in the browser."
            await page_says(page, client, rule)
            assert (await current_address(page))[1]["from"] == "lists"
            await expect(page.locator("#item-title")).to_contain_text("Created in the browser")
            await expect(detail(page, ref).locator(".body-text")).to_have_text(
                "Typed in the browser."
            )
            head = await page.locator("#item-page > header").text_content() or ""
            assert not RULE_WORD.search(head), RULE_WORD.findall(head)
            await no_panel_or_drawer(page)
            # Back on the list, it is the first of the unassigned ones: above the groups of
            # stories, and above the stories that have no acceptance criterion.
            await page.go_back()
            newest = page.locator("#unplaced-group li.requirement").first
            await expect(newest).to_contain_text("Created in the browser", timeout=SLOW)
            await expect(newest.locator(".chip.kind")).to_contain_text(f"Criterion #{rule}")
            await expect(newest).to_have_attribute("data-status", "open")
            order = await page.evaluate(
                "[...document.querySelectorAll('#unplaced-group, #requirement-groups,"
                " #stories-without-requirements')].map(e => e.id)"
            )
            assert order == ["unplaced-group", "requirement-groups", "stories-without-requirements"]
            assert await summary_says(page, client) == (1, 2)
            assert (
                "no acceptance criterion"
                in (
                    await page.locator("#stories-without-requirements summary").inner_text()
                ).lower()
            )

            # Place it on the first story from its page: the page says so as soon as it is done,
            # and the path above it grows by the epic and the story.
            await (
                newest.locator("a, button").filter(has_text="Created in the browser").first.click()
            )
            await item_loaded(page, ref)
            await page.locator("#item-page button.place-requirement").click()
            box = dialog(page)
            assert not RULE_WORD.search(await box.text_content() or "")
            assert await box.get_by_role("combobox").evaluate("e => document.activeElement === e")
            await choose_place(box, first)
            await box.get_by_role("button", name="Put it there").click()
            await expect(box).to_have_count(0, timeout=SLOW)
            placed = await page_says(page, client, rule)
            assert (placed["node"], placed["version"]) == (first, 2)
            await expect(page.locator("nav.crumbs li").last).to_have_text(
                f"AC-{rule}", timeout=SLOW
            )
            assert (await crumbs(page))[2:] == [
                "Lists",
                "E-1",
                "S-1",
                f"AC-{rule}",
            ]

            # An acceptance criterion on a story is moved, from a dialog on its page, in one step,
            # to another story.
            await page.locator("#item-page button.place-requirement").click()
            box = dialog(page)
            await choose_place(box, second)
            await box.get_by_role("button", name="Move it there").click()
            await expect(box).to_have_count(0, timeout=SLOW)
            moved_once = await page_says(page, client, rule)
            assert (moved_once["node"], moved_once["version"]) == (second, 3)

            # The list follows it. And again by dragging it, on the list, onto a story that has
            # nothing yet.
            await graph_open(w, LIST)
            await expect(group(page, second).locator(f"li[data-id='{rule}']")).to_be_visible(
                timeout=SLOW
            )
            await expect(group(page, first)).to_have_count(0)
            assert await summary_says(page, client) == (0, 2)
            await page.locator("#stories-without-requirements summary").click()
            bare = page.locator(f"#stories-without-requirements li[data-story='{first}']")
            await expect(bare).to_be_visible()
            await move_by_drag(page, rule_row(page, rule), bare)
            await expect(group(page, first).locator(f"li[data-id='{rule}']")).to_be_visible(
                timeout=SLOW
            )
            assert w.problems == [], w.problems

        moved = await ok(client, "get_rule", rule_id=rule)
        assert (moved["node"], moved["version"]) == (first, 4)
        events = await history(client, rule)
        assert [(e["action"], e["actor"]) for e in events] == [
            ("rule_created", ADA),
            ("rule_placed", ADA),
            ("rule_moved", ADA),
            ("rule_moved", ADA),
        ]
        assert events[2]["before"] == {"node": first, "version": 2}
        assert events[2]["after"] == {"node": second, "version": 3}
        for story in (first, second):
            story_events = (await ok(client, "node_history", node=story, limit=200))["items"]
            assert events[2]["id"] in [e["id"] for e in story_events]
        in_first = (await ok(client, "list_rules", node=first))["items"]
        assert [r["id"] for r in in_first] == [rule]
        assert sorted(r["id"] for r in (await ok(client, "list_rules", node=second))["items"]) == [
            anchor
        ]
        assert third and (await coverage_numbers(client)) == (0, 1)


# -- editing in the list, stale and blank changes, dropped acceptance criteria and the keyboard ---


async def test_a_criterion_is_edited_with_its_version_and_stale_or_blank_changes_are_refused(
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
            await box.locator("#requirement-status").select_option("met")
            await box.get_by_role("button", name="Save changes").click()
            await expect(box).to_have_count(0, timeout=SLOW)
            await expect(row).to_contain_text("Better")
            await expect(row).to_have_attribute("data-status", "met")
            await expect(row.locator(".rule-status")).to_have_text("Met")
            saved = await ok(client, "get_rule", rule_id=rule)
            assert (saved["title"], saved["body"], saved["status"], saved["version"]) == (
                "Better",
                "Second words.",
                "met",
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
                status="open",
                node=None,
            )
            await page.locator("#new-requirement").click()
            box = dialog(page)
            await box.locator("#requirement-body").fill("A body with no title")
            await box.get_by_role("button", name="Add the acceptance criterion").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(await refusal_in(page).inner_text(), blank)
            assert await box.locator("#requirement-body").input_value() == "A body with no title"
            assert (await ok(client, "list_rules", limit=1))["total"] == total
            # Nothing was made, so nothing opened: the list is still the page.
            assert (await current_address(page))[0] == "lists"
            await box.get_by_role("button", name="Cancel").click()

            # Moving it to the story it is already on is refused in the server's words.
            same = await refused(client, "place_rule", rule_id=rule, expected_version=2, node=story)
            await row.get_by_role("button", name="Move to another story or task…").click()
            box = dialog(page)
            await choose_place(box, story)
            await box.get_by_role("button", name="Move it there").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(await refusal_in(page).inner_text(), same)
            await box.get_by_role("button", name="Cancel").click()
            assert (await ok(client, "get_rule", rule_id=rule))["version"] == 2

            # Dropped acceptance criteria are left out until asked for; the lists say what
            # list_rules says. The filter speaks the acceptance criteria words: Open and met, Open,
            # Met, Dropped, All.
            assert await page.locator("#ctx-requirements-status option").all_text_contents() == [
                "Open and met",
                "Open",
                "Met",
                "Dropped",
                "All",
            ]
            await expect(rule_row(page, gone)).to_have_count(0)
            # What the default leaves out is said, with the button that shows it.
            left_out = page.locator("#requirements-left-out")
            await expect(left_out).to_have_text(
                "1 dropped acceptance criterion is not shown. Show it."
            )
            await left_out.get_by_role("button", name="Show it.").click()
            await expect(page.locator("#ctx-requirements-status")).to_have_value("all")
            await expect(rule_row(page, gone)).to_have_attribute("data-status", "dropped")
            await expect(left_out).to_be_hidden()
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
            listed_by_server = (await ok(client, "list_rules", node=story))["items"]
            assert sorted(in_story) == sorted(r["id"] for r in listed_by_server) == [rule, gone]
            await page.locator("#ctx-requirements-placement").select_option("unplaced")
            await expect(rule_row(page, rule)).to_have_count(0)
            await expect(rule_row(page, loose)).to_be_visible()
            await page.locator("#ctx-requirements-placement").select_option("")
            await page.locator("#ctx-requirements-status").select_option("live")
            await expect(left_out).to_be_visible()

            # From the keyboard: the dialog opens with the picker's box focused, and Escape gives
            # the focus back to the button that opened it.
            button = rule_row(page, loose).get_by_role("button", name="Put on a story or task…")
            await button.focus()
            await page.keyboard.press("Enter")
            await expect(dialog(page)).to_be_visible()
            assert await page.evaluate("document.activeElement.getAttribute('role')") == "combobox"
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
                node=other,
            )
            already = await refused(
                client, "place_rule", actor="x", rule_id=loose, expected_version=1, node=story
            )
            await button.click()
            box = dialog(page)
            await choose_place(box, story)
            await box.get_by_role("button", name="Put it there").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(await refusal_in(page).inner_text(), already)
            await box.get_by_role("button", name="Cancel").click()
            assert (await ok(client, "get_rule", rule_id=loose))["node"] == other
            assert w.problems == [], w.problems


# -- editing on the acceptance criterion's own page -----------------------------------------------


async def test_a_criterion_is_edited_on_its_page_and_a_stale_or_unchanged_save_is_refused(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Page edits")
        story = await made(client, "story", "Edited on a page", epic)
        rule = await rule_in(client, "Original", story, body="First *words*.")
        key = f"rule:{rule}"
        async with _browser(board, project) as w:
            page = w.page
            await open_item(w, key, "lists")
            await page_says(page, client, rule)
            # The words are read as text first: the form is not on the page until Edit is pressed.
            details = page.locator("#item-details")
            assert await details.locator("form.edit").count() == 0
            assert await details.locator(".body-text em").inner_text() == "words"
            await open_editor(page, "#item-page")
            assert await details.locator("#edit-title").input_value() == "Original"
            assert await details.locator("#edit-body").input_value() == "First *words*."
            assert await details.locator("#edit-status").input_value() == "open"

            # Nothing changed is said so; a change is saved at the version read, and read as text.
            await page.get_by_role("button", name="Save changes").click()
            problem = details.locator(".edit .problem")
            await expect(problem).to_contain_text("Nothing has been changed", timeout=SLOW)
            await details.locator("#edit-title").fill("Better")
            await details.locator("#edit-status").select_option("met")
            await page.get_by_role("button", name="Save changes").click()
            await expect(page.locator("#item-title")).to_contain_text("Better", timeout=SLOW)
            assert await details.locator("form.edit").count() == 0
            saved = await ok(client, "get_rule", rule_id=rule)
            assert (saved["title"], saved["status"], saved["version"]) == ("Better", "met", 2)
            await page_says(page, client, rule)
            [_, update] = await history(client, rule)
            assert (update["action"], update["actor"]) == ("rule_updated", ADA)
            await expect(page.locator("#crumb-page")).to_have_attribute(
                "title", re.compile("Better"), timeout=SLOW
            )

            # Someone else changes it while a draft is typed: nothing typed is overwritten.
            await open_editor(page, "#item-page")
            title = page.locator("#edit-title")
            await title.fill("My draft")
            await ok(
                client,
                "update_rule",
                actor="agent-1",
                rule_id=rule,
                expected_version=2,
                title="Agent title",
            )
            changed = page.locator("#item-details .notice.changed")
            await expect(changed).to_be_visible(timeout=SLOW)
            await expect(changed).to_contain_text(AGENT)
            assert await title.input_value() == "My draft"
            # Saving the draft is refused with the words the tool uses, and keeps what was typed.
            said = await refused(
                client, "update_rule", actor="x", rule_id=rule, expected_version=2, title="x"
            )
            await page.get_by_role("button", name="Save changes").click()
            refusal = page.locator("#item-details .edit .problem .refusal-text")
            await expect(refusal).to_be_visible(timeout=SLOW)
            plain(" ".join((await refusal.inner_text()).split()), " ".join(said.split()))
            assert await title.input_value() == "My draft"
            assert (await ok(client, "get_rule", rule_id=rule))["title"] == "Agent title"
            await page.locator("#item-details .load-current").first.click()
            await expect(page.locator("#item-details #edit-title")).to_have_value(
                "Agent title", timeout=SLOW
            )

            # Moving it to the story it is on is refused, from the page, in the server's words.
            await page.get_by_role("button", name="Cancel").click()
            version = (await ok(client, "get_rule", rule_id=rule))["version"]
            same = await refused(
                client,
                "place_rule",
                rule_id=rule,
                expected_version=version,
                node=story,
            )
            await page.locator("#item-page button.place-requirement").click()
            box = dialog(page)
            await choose_place(box, story)
            await box.get_by_role("button", name="Move it there").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(await refusal_in(page).inner_text(), same)
            await box.get_by_role("button", name="Cancel").click()
            assert (await ok(client, "get_rule", rule_id=rule))["version"] == version
            assert w.problems == [], w.problems


# -- a story's page lists its acceptance criteria, and an acceptance criterion's page leads back --


async def test_a_storys_acceptance_criteria_are_a_table_that_opens_each_ones_page(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Placed on")
        story = await made(client, "story", "Has criteria", epic)
        other = await made(client, "story", "Has one too", epic)
        first = await rule_in(client, "First one", story, body="Some words that are long. " * 12)
        second = await rule_in(client, "Second one", story, status="met")
        dropped = await rule_in(client, "Dropped one", story, status="dropped")
        third = await rule_in(client, "On the other story", other)
        async with _browser(board, project) as w:
            page = w.page
            # A story's table is its own acceptance criteria, in the order the server lists them,
            # with no column for the story they are on (it is this one).
            await open_item(w, story, "roadmap")
            listed_here = (await ok(client, "list_rules", node=story))["items"]
            rows = await record_rows(page, "requirements")
            assert [(r["id"], r["status"]) for r in rows] == [
                (r["id"], r["status"]) for r in listed_here
            ]
            assert [r["id"] for r in rows] == [first, second, dropped]
            heads = await page.locator("table.requirements-table thead th").all_text_contents()
            assert heads == ["Criterion", "Title", "Status"]
            await expect(page.locator("#item-page [data-part=requirements] h3")).to_have_text(
                "Acceptance criteria (3)"
            )
            # How many are met, out of how many still apply (a dropped one is not counted).
            await expect(page.locator("#item-page .criteria-met")).to_have_text(
                "1 of 2 met, 1 dropped"
            )
            # An epic holds none, so its page has no section for them.
            await open_item(w, epic, "roadmap")
            assert await page.locator("#item-page [data-part=requirements]").count() == 0
            assert await page.locator("#item-page [data-part=definition]").count() == 0
            # A row opens the acceptance criterion's page, in the same view, and the page leads
            # back to its story: a link in the line under its title, and the path above it.
            await open_item(w, other, "roadmap")
            await page.locator(f"tr.record-line[data-ref='rule:{third}'] a.record-title").click()
            await item_loaded(page, f"rule:{third}")
            assert (await current_address(page))[1]["from"] == "roadmap"
            await expect(page.locator("#item-what a")).to_have_attribute(
                "href", f"#/item?item={other}&from=roadmap"
            )
            await expect(page.locator("nav.crumbs li").last).to_have_text(
                f"AC-{third}", timeout=SLOW
            )
            assert (await crumbs(page))[2:] == [
                "Roadmap",
                "E-1",
                "S-2",
                f"AC-{third}",
            ]
            await page.locator("#item-what a").click()
            await item_loaded(page, other)
            assert [r["id"] for r in await record_rows(page, "requirements")] == [third]
            # Pressing anywhere on a row, away from its links and chips, opens the page as well.
            await open_item(w, story, "roadmap")
            await page.locator(f"tr.record-line[data-ref='rule:{first}'] .clamp-more").click()
            await item_loaded(page, f"rule:{first}")
            await expect(detail(page, f"rule:{first}")).to_have_attribute("data-status", "open")
            await no_panel_or_drawer(page)
            assert w.problems == [], w.problems


# -- a task holds acceptance criteria too, and its story's are read there -------------------------


async def test_a_task_has_its_own_acceptance_criteria_and_reads_its_storys(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Tasks hold them")
        story = await made(client, "story", "The story", epic)
        task = await made(client, "task", "The task", story)
        bare_task = await made(client, "task", "A task with none", story)
        on_story = await rule_in(client, "On the story", story, status="met")
        await rule_in(client, "Dropped on the story", story, status="dropped")
        async with _browser(board, project) as w:
            page = w.page
            # A task with none of its own has the empty field, with the way to add one, and the
            # story's acceptance criteria below it, only to be read.
            await open_item(w, bare_task, "board")
            section = page.locator("#item-page [data-part=requirements]")
            await expect(section.locator(".details-empty")).to_be_visible(timeout=SLOW)
            await expect(section.locator(".details-empty .chip")).to_have_text("Empty")
            await expect(page.locator(".item-jump[data-jump=requirements]")).to_contain_text(
                "empty"
            )
            inherited = section.locator("[data-group=from-story] tr.record-line")
            assert [await r.get_attribute("data-ref") for r in await inherited.all()] == [
                f"rule:{on_story}"
            ]
            await expect(section.locator("[data-group=from-story] h4")).to_contain_text(
                "From its story S-1 The story"
            )
            # What it has from its story cannot be changed there: its status is a plain chip.
            await expect(inherited.locator(".chip.rule-status")).to_have_count(1)
            assert await inherited.locator("[data-menu]").count() == 0

            # Writing one from the task's page puts it on the task at once, and it stays there.
            await page.locator("#item-page .details-empty .add-requirement").click()
            box = dialog(page)
            await expect(box.locator("#requirement-on")).to_have_text(
                "It goes on T-2 A task with none."
            )
            await box.locator("#requirement-title").fill("The message names the dock")
            await box.get_by_role("button", name="Add the acceptance criterion").click()
            await expect(box).to_have_count(0, timeout=SLOW)
            own = section.locator(":scope > .record-region tr.record-line")
            await expect(own).to_have_count(1, timeout=SLOW)
            await expect(own).to_contain_text("The message names the dock")
            made_here = await ok(client, "list_rules", node=bare_task)
            assert [r["title"] for r in made_here["items"]] == ["The message names the dock"]
            await expect(section.locator(".criteria-met")).to_have_text("0 of 1 met")
            await expect(page.locator(".item-jump[data-jump=requirements]")).to_contain_text("0/1")
            # The same criterion is in the list of sections' count, and the Board's card says it.
            await go(page, w.board, "#/board")
            card = page.locator(f"article.card[data-ref='{bare_task}']")
            await expect(card.locator(".chip.criteria")).to_have_text("0/1 criteria", timeout=SLOW)
            assert (
                await page.locator(f"article.card[data-ref='{task}'] .chip.criteria").count() == 0
            )
            # The count follows the work: met, and a dropped one is not counted at all.
            mine = made_here["items"][0]
            await ok(client, "update_rule", rule_id=mine["id"], expected_version=1, status="met")
            await ok(client, "create_rule", title="Dropped", node=bare_task, status="dropped")
            await expect(card.locator(".chip.criteria")).to_have_text("1/1 criteria", timeout=SLOW)
            await expect(card.locator(".chip.criteria")).to_have_class(re.compile(r"\bok\b"))

            # The picker offers stories and tasks, grouped under their epic, and finds them by
            # their code or a word of their title.
            await go(page, w.board, LIST)
            await page.locator("#new-requirement").click()
            box = dialog(page)
            await box.locator("#requirement-title").fill("Placed from the list")
            picker = box.locator(".holder-picker [role=combobox]")
            await picker.fill("T-1")
            await expect(box.locator(".holder-option")).to_have_count(2)  # its story, and the task
            await picker.fill("")
            await box.locator(f".holder-option[data-ref='{task}']").click()
            await box.get_by_role("button", name="Add the acceptance criterion").click()
            await expect(item_page(page)).to_have_attribute("data-kind", "rule", timeout=SLOW)
            placed = await ok(client, "list_rules", node=task)
            assert [r["title"] for r in placed["items"]] == ["Placed from the list"]
            assert w.problems == [], w.problems


# -- coverage: the report is a page, and the Graph's list takes a drop on one of its stories ------


async def coverage_world(client: Client) -> dict[str, Any]:
    """Two epics and four stories, an acceptance criterion on the first story, a dropped one that
    leaves the second with none that counts, and two that are placed nowhere."""
    epic_a = await made(client, "epic", "Coverage A")
    epic_b = await made(client, "epic", "Coverage B")
    s1 = await made(client, "story", "Story one", epic_a)
    s2 = await made(client, "story", "Story two", epic_a)
    s3 = await made(client, "story", "Story three", epic_a)
    s4 = await made(client, "story", "Story four", epic_b)
    return {
        "epic_a": epic_a,
        "epic_b": epic_b,
        "s1": s1,
        "s2": s2,
        "s3": s3,
        "s4": s4,
        "placed": await rule_in(client, "Placed", s1),
        "dropped": await rule_in(client, "Dropped", s2, status="dropped"),
        "loose_a": await rule_in(client, "Loose A"),
        "loose_b": await rule_in(client, "Loose B"),
    }


async def test_the_coverage_page_equals_coverage_report_and_acts_on_what_it_lists(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        world = await coverage_world(client)
        s2, s3, s4 = world["s2"], world["s3"], world["s4"]
        loose_a, loose_b = world["loose_a"], world["loose_b"]
        async with _browser(board, project) as w:
            page = w.page
            # Scoped to one epic, the acceptance criteria placed nowhere are still listed (nothing
            # else would lead a person to them), and the sentence above them says what the whole
            # project says.
            await graph_open(w, f"{LIST}&epic={number(world['epic_b'])}")
            await expect(rule_row(page, loose_a)).to_be_visible(timeout=SLOW)
            await expect(rule_row(page, loose_b)).to_be_visible()
            await expect(rule_row(page, world["placed"])).to_have_count(0)
            assert await summary_says(page, client) == (2, 3)

            # The coverage report is a page of its own, which the sentence's link opens: it is the
            # server's report, with nothing beside it.
            await page.locator("#show-coverage").click()
            await on_page(page, "coverage")
            await no_panel_or_drawer(page)
            report = await whole_coverage_report(client)
            assert (report["unplaced_rule_count"], report["story_without_rules_count"]) == (2, 3)
            summary = page.locator("#coverage-summary")
            await expect(summary).to_have_attribute("data-unplaced", "2", timeout=SLOW)
            await expect(summary).to_have_attribute("data-stories-without", "3")
            await expect(summary).to_contain_text("2 acceptance criteria are placed nowhere.")
            await expect(summary).to_contain_text("3 stories have no acceptance criterion.")
            await expect(page.locator("#unplaced-rules h2")).to_have_text(
                "Acceptance criteria placed nowhere (2)"
            )
            await expect(page.locator("#stories-without-rules h2")).to_have_text(
                "Stories with no acceptance criterion (3)"
            )
            unplaced_ids = await page.locator("#unplaced-rules tr[data-rule]").evaluate_all(
                "els => els.map(e => Number(e.dataset.rule))"
            )
            story_refs = await page.locator("#stories-without-rules tr[data-story]").evaluate_all(
                "els => els.map(e => e.dataset.story)"
            )
            assert unplaced_ids == [r["id"] for r in report["unplaced_rules"]]
            assert story_refs == [s["ref"] for s in report["stories_without_rules"]]
            # A story whose only acceptance criterion is dropped has none that count.
            assert sorted(story_refs) == sorted([s2, s3, s4])

            # What it lists opens its page, in Coverage, and the way back is the report.
            await page.locator(f"#unplaced-rules tr[data-rule='{loose_b}'] a.record-title").click()
            await item_loaded(page, f"rule:{loose_b}")
            assert (await current_address(page))[1]["from"] == "coverage"
            assert (await crumbs(page))[2] == "Coverage"
            await page.locator("nav.crumbs li.crumb-view a").click()
            await on_page(page, "coverage")
            await expect(page.locator("#unplaced-rules tr[data-rule]")).to_have_count(
                2, timeout=SLOW
            )
            await page.locator(
                f"#stories-without-rules tr[data-story='{s4}'] a.record-title"
            ).click()
            await item_loaded(page, s4)
            await page.go_back()
            await on_page(page, "coverage")

            # Putting an acceptance criterion that is placed nowhere on one of the report's stories
            # places it, from the report, which stays on screen and follows.
            row = page.locator(f"#unplaced-rules tr[data-rule='{loose_a}']")
            await expect(row).to_be_visible(timeout=SLOW)
            await row.get_by_role("button", name="Put on a story or task…").click()
            box = dialog(page)
            await choose_place(box, s3)
            await box.get_by_role("button", name="Put it there").click()
            await expect(box).to_have_count(0, timeout=SLOW)
            placed_now = await ok(client, "get_rule", rule_id=loose_a)
            assert (placed_now["node"], placed_now["version"]) == (s3, 2)
            await on_page(page, "coverage")
            await expect(page.locator("#unplaced-rules h2")).to_have_text(
                "Acceptance criteria placed nowhere (1)", timeout=SLOW
            )
            await expect(page.locator("#stories-without-rules h2")).to_have_text(
                "Stories with no acceptance criterion (2)"
            )
            await expect(
                page.locator(f"#stories-without-rules tr[data-story='{s3}']")
            ).to_have_count(0)
            assert await coverage_numbers(client) == (1, 2)
            await expect(summary).to_have_attribute("data-unplaced", "1")

            # Writing one on a story that has none puts that story out of the report, and the page
            # does not move away.
            bare = page.locator(f"#stories-without-rules tr[data-story='{s2}']")
            await bare.get_by_role("button", name="Add an acceptance criterion…").click()
            box = dialog(page)
            await expect(box.locator("#requirement-on")).to_contain_text("It goes on S-2")
            await box.locator("#requirement-title").fill("Written for story two")
            await box.get_by_role("button", name="Add the acceptance criterion").click()
            await expect(box).to_have_count(0, timeout=SLOW)
            await expect(bare).to_have_count(0, timeout=SLOW)
            await expect(page.locator("#stories-without-rules h2")).to_have_text(
                "Stories with no acceptance criterion (1)"
            )
            await on_page(page, "coverage")
            in_s2 = (await ok(client, "list_rules", node=s2))["items"]
            assert sorted(r["title"] for r in in_s2) == ["Dropped", "Written for story two"]
            assert await coverage_numbers(client) == (1, 1)
            assert w.problems == [], w.problems
        events = await history(client, loose_a)
        assert [(e["action"], e["actor"]) for e in events] == [
            ("rule_created", AGENT),
            ("rule_placed", ADA),
        ]


async def test_a_drop_on_a_story_in_the_graphs_list_places_or_moves_an_acceptance_criterion(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        world = await coverage_world(client)
        s2, s3 = world["s2"], world["s3"]
        loose_a = world["loose_a"]
        async with _browser(board, project) as w:
            page = w.page
            await graph_open(w, f"{LIST}&epic={number(world['epic_a'])}")
            await expect(rule_row(page, loose_a)).to_be_visible(timeout=SLOW)
            assert await summary_says(page, client) == (2, 3)
            await page.locator("#stories-without-requirements summary").click()
            bare_story = f"#stories-without-requirements li[data-story='{s3}']"
            target = page.locator(bare_story)
            await expect(target).to_have_count(1, timeout=SLOW)

            # Dragging an acceptance criterion that is placed nowhere onto a story that has none
            # places it.
            await page.drag_and_drop(f"#lists-list [data-key='rule:{loose_a}']", bare_story)
            await expect(rule_row(page, loose_a)).to_have_attribute("data-node", s3, timeout=SLOW)
            placed_now = await ok(client, "get_rule", rule_id=loose_a)
            assert (placed_now["node"], placed_now["version"]) == (s3, 2)
            assert await summary_says(page, client) == (1, 2)
            await expect(
                page.locator(f"#stories-without-requirements li[data-story='{s3}']")
            ).to_have_count(0, timeout=SLOW)

            # Dragging one that is on a story moves it, in one step, and the numbers follow.
            await move_by_drag(
                page,
                rule_row(page, loose_a),
                page.locator(f"#stories-without-requirements li[data-story='{s2}']"),
            )
            await expect(rule_row(page, loose_a)).to_have_attribute("data-node", s2, timeout=SLOW)
            moved = await ok(client, "get_rule", rule_id=loose_a)
            assert (moved["node"], moved["version"]) == (s2, 3)
            assert await summary_says(page, client) == (1, 2)
            await expect(
                page.locator(f"#stories-without-requirements li[data-story='{s3}']")
            ).to_have_count(1, timeout=SLOW)
            # A drop on the story it is already on changes nothing.
            await move_by_drag(
                page,
                rule_row(page, loose_a),
                page.locator(f"#requirement-groups section[data-group='{s2}'] h3[data-drop-node]"),
            )
            await page.wait_for_load_state("networkidle")
            assert (await ok(client, "get_rule", rule_id=loose_a))["version"] == 3
            assert w.problems == [], w.problems
        events = await history(client, loose_a)
        assert [(e["action"], e["actor"]) for e in events] == [
            ("rule_created", AGENT),
            ("rule_placed", ADA),
            ("rule_moved", ADA),
        ]
        assert (await coverage_numbers(client)) == (1, 2)
