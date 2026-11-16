"""M12's Rules page against the running containers, driven in a real headless browser and checked
against a real MCP client (see ./check.sh). What a person does is read back through get_rule and the
histories, and every refusal is compared word for word with what the same attempt gets through MCP.
Titles carry a unique word, and each test looks only at the stories it made, since all tests share
one database."""

from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_helpers import SLOW, board_url, browser_page, hold_back_news
from tests.helpers import ok, refused, the_token, unique_word

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

ADA = "Ada Lovelace"


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


async def made(
    client: Client, kind: str, title: str, parent: int | None = None, **more: Any
) -> int:
    fields: dict[str, Any] = {"actor": "agent-1", "kind": kind, "title": title, **more}
    if parent:
        fields["parent_id"] = parent
    return int((await ok(client, "create_node", **fields))["id"])


async def rule_in(client: Client, title: str, story: int | None = None, **more: Any) -> int:
    fields: dict[str, Any] = {"actor": "agent-1", "title": title, **more}
    if story:
        fields["story_id"] = story
    return int((await ok(client, "create_rule", **fields))["id"])


def plain(board_words: str, mcp_words: str) -> None:
    assert board_words.strip() and not board_words.startswith("Error executing tool")
    assert mcp_words.endswith(board_words.strip()), (board_words, mcp_words)


def group(page: Page, story: int) -> Any:
    return page.locator(f"#rule-groups section[data-group='{story}']")


async def in_coverage(page: Page, rule: int) -> bool:
    """Whether the coverage report lists this unplaced rule, paging through it if it has to."""
    listed = page.locator(f"#unplaced-rules li[data-rule='{rule}']")
    for _ in range(50):
        if await listed.count():
            return True
        more = page.locator("#unplaced-rules button", has_text="Show more")
        if not await more.count():
            return False
        await more.click()
    return False


async def unplaced_in_coverage(page: Page, wanted: int) -> None:
    """Wait until the coverage report says it holds this many unplaced rules."""
    await expect(page.locator("#unplaced-rules h3")).to_have_text(
        f"Rules in no story ({wanted})", timeout=SLOW
    )


async def history(client: Client, rule: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = (await ok(client, "rule_history", rule_id=rule, limit=200))[
        "items"
    ]
    return items


# -- 1. an unplaced rule is first; placing it takes it out of both; moving it is one step ---------


async def test_a_new_rule_is_first_until_it_is_placed_and_a_second_placement_is_refused(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Rules {word}")
    first = await made(client, "story", f"First {word}", epic)
    second = await made(client, "story", f"Second {word}", epic)
    anchor = await rule_in(client, f"Anchor {word}", second)
    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/rules")
        count = (await ok(client, "coverage_report", show="unplaced_rules", limit=1))[
            "unplaced_rule_count"
        ]
        await unplaced_in_coverage(page, count)

        await page.locator("#new-rule").click()
        dialog = page.locator("dialog.modal")
        await dialog.locator("#rule-title").fill(f"Created {word}")
        await dialog.locator("#rule-body").fill("Typed in the browser.")
        await dialog.get_by_role("button", name="Create rule").click()
        newest = page.locator("#unplaced-group li.rule").first
        await expect(newest).to_contain_text(f"Created {word}", timeout=SLOW)
        rule = int(await newest.get_attribute("data-rule") or 0)
        # It is first: the unplaced group is above every story's group, and it tops that group.
        order = await page.evaluate(
            "[...document.querySelectorAll('#unplaced-group, #rule-groups, #coverage-report')]"
            ".map(e => e.id)"
        )
        assert order == ["unplaced-group", "rule-groups", "coverage-report"]
        await unplaced_in_coverage(page, count + 1)
        assert await in_coverage(page, rule)
        made_rule = await ok(client, "get_rule", rule_id=rule)
        assert (made_rule["story_id"], made_rule["status"], made_rule["version"]) == (
            None,
            "held",
            1,
        )
        assert made_rule["body"] == "Typed in the browser."

        # Place it in the first story: it leaves the unplaced group and the coverage report.
        await newest.get_by_role("button", name="Put in a story…").click()
        dialog = page.locator("dialog.modal")
        await dialog.locator("#rule-target").select_option(str(first))
        await dialog.get_by_role("button", name="Place in story").click()
        await expect(group(page, first).locator(f"li.rule[data-rule='{rule}']")).to_be_visible(
            timeout=SLOW
        )
        await expect(page.locator(f"#unplaced-group li.rule[data-rule='{rule}']")).to_have_count(0)
        await unplaced_in_coverage(page, count)
        assert not await in_coverage(page, rule)
        assert (await ok(client, "get_rule", rule_id=rule))["story_id"] == first

        # Placing it in a second story is refused, in the words of MCP, and nothing changes.
        said = await refused(
            client,
            "place_rule",
            actor="x",
            rule_id=rule,
            expected_version=2,
            story_id=second,
        )
        await (
            group(page, first)
            .locator(f"li.rule[data-rule='{rule}']")
            .get_by_role("button", name="Put in a story…")
            .click()
        )
        dialog = page.locator("dialog.modal")
        await dialog.locator("#rule-target").select_option(str(second))
        await dialog.get_by_role("button", name="Place in story").click()
        problem = dialog.locator(".problem")
        await expect(problem).to_be_visible(timeout=SLOW)
        plain(await problem.inner_text(), said)
        await dialog.get_by_role("button", name="Cancel").click()
        assert (await ok(client, "get_rule", rule_id=rule))["story_id"] == first
        assert [e["action"] for e in await history(client, rule)] == [
            "rule_created",
            "rule_placed",
        ]

        # Dragging it onto the second story moves it, in one step.
        await page.drag_and_drop(
            f"li.rule[data-rule='{rule}']",
            f"#rule-groups section[data-group='{second}'] .group-head",
        )
        await expect(group(page, second).locator(f"li.rule[data-rule='{rule}']")).to_be_visible(
            timeout=SLOW
        )
        await expect(group(page, first)).to_have_count(0)
        assert w.problems == [], w.problems

    moved = await ok(client, "get_rule", rule_id=rule)
    assert (moved["story_id"], moved["version"]) == (second, 3)
    events = await history(client, rule)
    assert [(e["action"], e["actor"]) for e in events] == [
        ("rule_created", ADA),
        ("rule_placed", ADA),
        ("rule_moved", ADA),
    ]
    move = events[2]
    assert move["before"] == {"story_id": first, "version": 2}
    assert move["after"] == {"story_id": second, "version": 3}
    for story in (first, second):
        story_events = (await ok(client, "node_history", node_id=story, limit=200))["items"]
        assert move["id"] in [e["id"] for e in story_events]
    in_second = (await ok(client, "list_rules", story_id=second))["items"]
    assert sorted(r["id"] for r in in_second) == sorted([anchor, rule])


# -- editing, stale edits, dropped rules and the keyboard -------------------------------------------


async def test_a_rule_is_edited_with_its_version_and_a_stale_edit_is_refused_keeping_what_was_typed(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Edits {word}")
    story = await made(client, "story", f"Story {word}", epic)
    rule = await rule_in(client, f"Original {word}", story, body="First words.")
    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/rules")
        row = group(page, story).locator(f"li.rule[data-rule='{rule}']")
        await expect(row).to_contain_text("First words.", timeout=SLOW)
        await row.get_by_role("button", name="Edit").click()
        dialog = page.locator("dialog.modal")
        await dialog.get_by_role("button", name="Save changes").click()
        await expect(dialog.locator(".problem")).to_contain_text("Nothing has been changed")
        await dialog.locator("#rule-title").fill(f"Better {word}")
        await dialog.locator("#rule-body").fill("Second words.")
        await dialog.locator("#rule-status").select_option("settled")
        await dialog.get_by_role("button", name="Save changes").click()
        await expect(row).to_contain_text(f"Better {word}", timeout=SLOW)
        await expect(row.locator(".rule-status")).to_have_text("Settled")
        saved = await ok(client, "get_rule", rule_id=rule)
        assert (saved["title"], saved["body"], saved["status"], saved["version"]) == (
            f"Better {word}",
            "Second words.",
            "settled",
            2,
        )
        (_, update) = await history(client, rule)
        assert (update["action"], update["actor"]) == ("rule_updated", ADA)
        assert update["before"]["title"] == f"Original {word}"
        assert update["after"]["status"] == "settled" and update["after"]["version"] == 2

        # An agent changes the rule while the board has not heard of it: the edit is refused, in
        # the words of MCP, and the dialog keeps what was typed.
        await hold_back_news(page)
        await ok(
            client,
            "update_rule",
            actor="agent-1",
            rule_id=rule,
            expected_version=2,
            title=f"Agent {word}",
        )
        said = await refused(
            client, "update_rule", actor="x", rule_id=rule, expected_version=2, title="x"
        )
        await row.get_by_role("button", name="Edit").click()
        dialog = page.locator("dialog.modal")
        await dialog.locator("#rule-title").fill(f"Mine {word}")
        await dialog.get_by_role("button", name="Save changes").click()
        problem = dialog.locator(".problem")
        await expect(problem).to_be_visible(timeout=SLOW)
        plain(await problem.inner_text(), said)
        assert await dialog.locator("#rule-title").input_value() == f"Mine {word}"
        assert (await ok(client, "get_rule", rule_id=rule))["title"] == f"Agent {word}"
        assert w.problems == [], w.problems


async def test_dropped_rules_are_hidden_until_asked_for_and_moving_an_unplaced_rule_is_refused(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Dropped {word}")
    story = await made(client, "story", f"Story {word}", epic)
    kept = await rule_in(client, f"Kept {word}", story)
    gone = await rule_in(client, f"Gone {word}", story, status="dropped")
    loose = await rule_in(client, f"Loose {word}")
    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/rules")
        await expect(group(page, story).locator("li.rule")).to_have_count(1, timeout=SLOW)
        assert await group(page, story).locator("li.rule").get_attribute("data-rule") == str(kept)
        await page.locator("#show-dropped").check()
        await expect(group(page, story).locator("li.rule")).to_have_count(2, timeout=SLOW)
        assert "dropped=1" in page.url
        dropped_row = group(page, story).locator(f"li.rule[data-rule='{gone}']")
        await expect(dropped_row.locator(".rule-status")).to_have_text("Dropped")
        # A dropped rule is not in the coverage report, and a rule in a story is not either.
        assert not await in_coverage(page, gone) and not await in_coverage(page, kept)
        assert await in_coverage(page, loose)
        # What the page shows for the story is what list_rules gives.
        listed = (await ok(client, "list_rules", story_id=story))["items"]
        assert sorted(r["id"] for r in listed) == [kept, gone]

        # Moving a rule that is in no story is refused as MCP refuses it, and nothing changes.
        said_move = await refused(
            client, "move_rule", actor="x", rule_id=loose, expected_version=1, new_story_id=story
        )
        await (
            page.locator(f"#unplaced-group li.rule[data-rule='{loose}']")
            .get_by_role("button", name="Put in a story…")
            .click()
        )
        dialog = page.locator("dialog.modal")
        await dialog.locator("#rule-target").select_option(str(story))
        await dialog.get_by_role("button", name="Move to story").click()
        problem = dialog.locator(".problem")
        await expect(problem).to_be_visible(timeout=SLOW)
        plain(await problem.inner_text(), said_move)
        assert (await ok(client, "get_rule", rule_id=loose))["story_id"] is None
        assert w.problems == [], w.problems


async def test_the_put_in_a_story_dialog_opens_from_the_keyboard_and_gives_the_focus_back(
    client: Client, board: str
) -> None:
    word = unique_word()
    loose = await rule_in(client, f"Keyboard {word}")
    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/rules")
        button = page.locator(f"#unplaced-group li.rule[data-rule='{loose}']").get_by_role(
            "button", name="Put in a story…"
        )
        await expect(button).to_be_visible(timeout=SLOW)
        await button.focus()
        await page.keyboard.press("Enter")
        dialog = page.locator("dialog.modal")
        await expect(dialog).to_be_visible()
        assert await page.evaluate("document.activeElement.id") == "rule-target"
        await page.keyboard.press("Escape")
        await expect(dialog).to_have_count(0)
        await expect(button).to_be_focused()
        assert w.problems == [], w.problems
