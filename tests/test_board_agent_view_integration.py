"""The Agent view of an item's page (M34), driven in a real headless browser and compared with
what a real MCP client reads (see ./check.sh). Every kind of page has a button for it in its header,
for a person who can only look too, and it opens a dialog with what an agent receives for the item:
the call it makes and the answer it gets, as short lines. For an epic, story, task or acceptance
criterion that is the `recall` of the item as it came; a note is read with `get_note`. The lines are
the same ones the Graph drew before M34, which has no Agent view of its own any more."""

from typing import Any

import anyio
import pytest
from playwright.async_api import Page, Route, expect

from tests.board_context import context_world as world_of_notes
from tests.board_context import fresh_project
from tests.board_helpers import SLOW, browser_page, go, item_address
from tests.helpers import code_of, ok
from tests.world import join_project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

# The fields of a note, in the order its Agent view reads them.
NOTE_FIELDS = [
    "headline",
    "text",
    "why",
    "decided_by",
    "decided_on",
    "project_wide",
    "status",
    "confirmed_by",
    "superseded_by_id",
    "supersedes_ids",
    "status_reason",
    "nodes",
    "rule_ids",
    "note_ids",
]
ONLY_THE_EXACT = (
    "Only exact links are followed. What is linked to this item and to the story and epic above "
    "it is here (each entry says which); what is linked to its children is not: an agent recalls "
    "those too if they matter."
)
READS_A_NOTE = (
    "recall reads one epic, story, task or acceptance criterion; for a note the agent reads the "
    "note itself, and what links to it."
)


def clip(text: Any, size: int) -> str:
    """A text on one line, cut at `size` characters with an ellipsis, as the Agent view cuts it."""
    one = " ".join(str(text or "").split())
    return one[: size - 1].rstrip() + "…" if len(one) > size else one


def quoted(text: Any, size: int = 80) -> str:
    return f'"{clip(text, size)}"'


def what_recall_says(asked: str, recalled: dict[str, Any]) -> list[str]:
    """The lines of the Agent view of a recall: the call, then each section with its first six
    entries and why each is there, and the sentence that closes it."""
    lines = [asked]

    def section(name: str, entries: dict[str, Any] | None, write: Any) -> None:
        if not entries or not entries["count"]:
            lines.append(f"{name}: []")
            return
        lines.append(f"{name}: ({entries['count']})")
        for entry in entries["items"][:6]:
            lines.extend(write(entry))
        if entries["count"] > 6:
            lines.append(f"… {entries['count'] - 6} more")

    def note(entry: dict[str, Any]) -> list[str]:
        record = entry["note"]
        headline = quoted(record.get("headline") or record["text"])
        return [f"- note:{record['id']} {headline} [{record['status']}]", f"why: {entry['why']}"]

    def criterion(entry: dict[str, Any]) -> list[str]:
        record = entry["criterion"]
        return [
            f"- rule:{record['id']} {quoted(record['title'])} [{record['status']}]",
            f"why: {entry['why']}",
        ]

    def alike(entry: dict[str, Any]) -> list[str]:
        score = entry.get("similarity")
        return [
            f"- {entry['ref']} {quoted(entry['title'])}"
            + ("" if score is None else f" alike={score:.2f}")
        ]

    section("notes", recalled.get("notes"), note)
    section("project_wide_notes", recalled.get("project_wide_notes"), note)
    section("past_notes", recalled.get("past_notes"), note)
    section("acceptance_criteria", recalled.get("acceptance_criteria"), criterion)
    if recalled.get("similar"):
        section("similar", recalled["similar"], alike)
    return [*lines, "", ONLY_THE_EXACT]


def what_a_note_says(record: dict[str, Any]) -> list[str]:
    """The lines of the Agent view of a note: the call, then each field it has."""
    lines = [f"get_note(note_id={record['id']})"]
    for field in NOTE_FIELDS:
        value = record.get(field)
        if value is None or (isinstance(value, list) and not value and field != "nodes"):
            continue
        if isinstance(value, bool):
            shown = "true" if value else "false"
        elif isinstance(value, str):
            shown = quoted(value, 120)
        elif isinstance(value, list):
            shown = "[" + ", ".join(str(one) for one in value) + "]"
        else:
            shown = str(value)
        lines.append(f"{field}: {shown}")
    return [*lines, "", READS_A_NOTE]


async def lines_drawn(page: Page) -> list[str]:
    """The lines of the Agent view as they are on the page, each on one line of words."""
    found: list[str] = await page.locator("#agent-view-text .ag-line").evaluate_all(
        "els => els.map(e => e.textContent.replace(/\\s+/g, ' ').trim())"
    )
    return found


async def open_agent_view(page: Page, board: str, ref: str) -> None:
    """Go to the page of an item, press Agent view and wait until the answer is drawn."""
    await go(page, board, item_address(ref, "board"))
    await expect(page.locator(f"#item-page[data-ref='{ref}']")).to_be_visible(timeout=SLOW)
    await page.locator("#item-agent-view").click()
    await expect(page.locator("dialog#agent-view[open]")).to_be_visible()
    await expect(page.locator("#agent-view-text")).to_be_visible(timeout=SLOW)


async def close_agent_view(page: Page) -> None:
    await page.locator("#agent-view-close").click()
    await expect(page.locator("#agent-view")).to_have_count(0)
    await expect(page.locator("#item-agent-view")).to_be_focused()


async def test_the_agent_view_of_an_epic_a_story_and_a_task_is_the_recall_an_agent_makes(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        world = await world_of_notes(client, "agentwork", project)
        async with browser_page(board, token=project.key) as w:
            page = w.page
            for name in ("s1", "t1", "E", "s2"):
                ref = world[name]
                await open_agent_view(page, board, ref)
                dialog = page.locator("dialog#agent-view")
                await expect(dialog.locator("h2")).to_have_text(
                    f"What an agent receives for {code_of(ref)}"
                )
                assert "wide" in (await dialog.get_attribute("class") or "")
                recalled = await ok(client, "recall", node=ref, limit=200, with_similar=True)
                asked = f'recall(node="{ref}", limit=200, with_similar=true)'
                assert await lines_drawn(page) == what_recall_says(asked, recalled), name
                await expect(page.locator("#agent-view-text")).to_have_attribute(
                    "aria-label", f"What an agent receives for {code_of(ref)}"
                )
                await close_agent_view(page)
            # The story has notes of its own, one that came down to its tasks, a criterion and a
            # project-wide note, so the answer the lines were compared with is not an empty one.
            story = await ok(client, "recall", node=world["s1"], limit=200)
            assert story["notes"]["count"] and story["acceptance_criteria"]["count"]
            assert story["project_wide_notes"]["count"] and story["past_notes"]["count"]
            assert w.problems == [], w.problems


async def test_the_agent_view_of_a_criterion_and_of_a_note_is_what_an_agent_reads_for_it(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        world = await world_of_notes(client, "agentrecords", project)
        async with browser_page(board, token=project.key) as w:
            page = w.page
            rule = f"rule:{world['r1']}"
            await open_agent_view(page, board, rule)
            await expect(page.locator("dialog#agent-view h2")).to_have_text(
                f"What an agent receives for acceptance criterion #{world['r1']}"
            )
            recalled = await ok(client, "recall", rule_id=world["r1"], limit=200, with_similar=True)
            asked = f"recall(rule_id={world['r1']}, limit=200, with_similar=true)"
            assert await lines_drawn(page) == what_recall_says(asked, recalled)
            await close_agent_view(page)
            # A note someone decided and confirmed, one nobody decided, one that was replaced and
            # one that was withdrawn: each is read as the record itself.
            for name in ("d1", "n1", "d_old", "n_gone"):
                ref = f"note:{world[name]}"
                await open_agent_view(page, board, ref)
                await expect(page.locator("dialog#agent-view h2")).to_have_text(
                    f"What an agent receives for note #{world[name]}"
                )
                record = await ok(client, "get_note", note_id=world[name])
                assert await lines_drawn(page) == what_a_note_says(record), name
                await close_agent_view(page)
            assert w.problems == [], w.problems


async def test_the_agent_view_says_it_is_reading_shows_a_refusal_and_closes_either_way(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        world = await world_of_notes(client, "agentstates", project)
        story = world["s1"]
        # What the server is made to do with the next question of the Agent view: wait to be
        # let go, or refuse.
        state: dict[str, Any] = {"refuse": False, "held": anyio.Event()}

        async def answer(route: Route) -> None:
            # Only the Agent view asks for as many as 200 entries.
            if '"limit":200' not in (route.request.post_data or ""):
                await route.continue_()
            elif state["refuse"]:
                await route.fulfill(
                    status=400,
                    content_type="application/json",
                    body='{"message": "That is not something an agent may read."}',
                )
            else:
                await state["held"].wait()
                await route.continue_()

        async with browser_page(board, token=project.key) as w:
            page = w.page
            await page.route("**/board/api/projects/*/tool/recall", answer)
            await go(page, board, item_address(story, "board"))
            await expect(page.locator(f"#item-page[data-ref='{story}']")).to_be_visible(
                timeout=SLOW
            )
            # While it reads, the dialog says so and has its Close button, which has the focus.
            await page.locator("#item-agent-view").click()
            reading = page.locator("dialog#agent-view .agent-view-body .loading")
            await expect(reading).to_have_text("Reading what an agent receives…")
            await expect(page.locator("#agent-view-text")).to_have_count(0)
            await expect(page.locator("#agent-view-close")).to_be_focused()
            state["held"].set()
            await expect(page.locator("#agent-view-text")).to_be_visible(timeout=SLOW)
            await expect(reading).to_have_count(0)
            # Escape closes it and gives the focus back to the button that opened it.
            await page.keyboard.press("Escape")
            await expect(page.locator("#agent-view")).to_have_count(0)
            await expect(page.locator("#item-agent-view")).to_be_focused()
            # A refusal is shown in the server's words, with nothing else drawn.
            state["refuse"] = True
            await page.locator("#item-agent-view").click()
            problem = page.locator("dialog#agent-view .problem-text")
            await expect(problem).to_have_text("That is not something an agent may read.")
            await expect(page.locator("dialog#agent-view [role=alert]")).to_be_visible()
            await expect(page.locator("#agent-view-text")).to_have_count(0)
            await close_agent_view(page)
            # Closed before the answer comes, nothing is drawn and nothing breaks.
            state["refuse"] = False
            state["held"] = anyio.Event()
            await page.locator("#item-agent-view").click()
            await expect(reading).to_be_visible()
            await close_agent_view(page)
            state["held"].set()
            await anyio.sleep(0.5)
            await expect(page.locator("#agent-view")).to_have_count(0)
            assert w.problems == [], w.problems


async def test_every_kind_of_page_has_the_agent_view_and_so_has_a_person_who_can_only_look(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        world = await world_of_notes(client, "agentviewer", project)
        refs = [
            world["E"],
            world["s1"],
            world["t1"],
            f"rule:{world['r1']}",
            f"note:{world['d1']}",
        ]
        viewer = join_project(project, "viewer")
        async with browser_page(board, token=None, person=viewer) as v:
            page = v.page
            for ref in refs:
                await go(page, board, item_address(ref, "board"))
                await expect(page.locator(f"#item-page[data-ref='{ref}']")).to_be_visible(
                    timeout=SLOW
                )
                button = page.locator("#item-agent-view")
                await expect(button).to_have_count(1)
                await expect(button).to_have_text("Agent view")
                assert await button.locator("svg.icon").count() == 1, ref
                # It only reads, so it is all that someone who cannot write has in the header.
                assert await page.locator("#item-page .item-actions button").count() == 1, ref
            # It opens for them and reads what an agent receives.
            await open_agent_view(page, board, world["s1"])
            recalled = await ok(client, "recall", node=world["s1"], limit=200, with_similar=True)
            asked = f'recall(node="{world["s1"]}", limit=200, with_similar=true)'
            assert await lines_drawn(page) == what_recall_says(asked, recalled)
            await close_agent_view(page)
            assert v.problems == [], v.problems
