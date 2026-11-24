"""Notes against the running containers, in a real headless browser, compared with what a real MCP
client reads (`get_note`, `list_notes`, `recall`, `note_history`, `next_work`). A note is one kind
of record: what is known or decided, and why. It has one status: Unconfirmed, Confirmed, Replaced
or Withdrawn. Someone may have decided it (then a quiet detail says who and when); that is not
another kind of note. Notes are added from the page of what they concern or from the
Lists view's Notes list, confirmed and un-confirmed by a person (the menu of the status chip in a
row or on the note's page), replaced and withdrawn from that menu or from the note's own page, and
changed in what they concern on their own page. They are shown on the page of an epic, story, task
or requirement as tables across the page that open the note's page, and in the Graph's Around. What
an epic or a story holds comes down to the stories and tasks under it and says where from. Every
refusal is compared word for word with what the same attempt gets through MCP, and a refused change
is checked to have written nothing. Each test makes a project of its own (its owner is Ada Lovelace,
who is who a change made on its board is recorded as; the agent is "tests"), so a count is a whole
count. Work is named by its reference (`story:12`, `S-12` on the page); the Lists address names an
epic by its number (`epic=3`)."""

import re

import pytest
from playwright.async_api import Locator, Page, expect

from tests.board_context import (
    ADA,
    board_refusal,
    choose_in_menu,
    context_world,
    dialog,
    fresh_project,
    history_actions,
    recall_matches,
    refusal_in,
    tally,
    whole,
)
from tests.board_helpers import (
    SLOW,
    browser_page,
    crumbs,
    current_address,
    detail,
    disclosure,
    go,
    item_loaded,
    made,
    no_panel_or_drawer,
    open_item,
    part,
    plain,
    record_row,
    record_rows,
)
from tests.board_m14 import spoken
from tests.helpers import code_of as code
from tests.helpers import (
    confirm_on_board,
    make_decision,
    make_note,
    ok,
    refused,
    unique_word,
)
from tests.helpers import number_of as number
from tests.world import join_project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

MARIE = "Marie Curie"


def context_list(epic: str | None = None) -> str:
    """The Lists view's Notes list, scoped to one epic (named in the address by its number)."""
    return "#/lists?view=notes" + (f"&epic={number(epic)}" if epic else "")


async def notes_open(page: Page, board: str, address: str) -> None:
    """Load the Notes list afresh and wait until it knows how many notes there are."""
    await go(page, board, address)
    await expect(page.locator("#ctx-notes-count")).to_have_attribute(
        "data-total", re.compile(r"^\d+$"), timeout=SLOW
    )


def listed(page: Page, ref: str) -> Locator:
    """The row of one note in the Notes list."""
    return page.locator(f"#ctx-notes-list tr.record-line[data-ref='{ref}']")


async def listed_refs(page: Page) -> list[str]:
    """The notes the list draws now, top to bottom."""
    refs: list[str] = await page.locator("#ctx-notes-list tr.record-line").evaluate_all(
        "els => els.map(e => e.dataset.ref)"
    )
    return refs


async def counted(page: Page) -> tuple[int, int]:
    """What the list says it shows: how many rows the filters keep, out of how many are in scope."""
    count = page.locator("#ctx-notes-count")
    return int(str(await count.get_attribute("data-shown"))), int(
        str(await count.get_attribute("data-total"))
    )


async def status_filter(page: Page, value: str) -> None:
    """The Status filter of the Notes list: all, in_force, unconfirmed, confirmed, superseded or
    withdrawn."""
    await page.locator("#ctx-notes-status").select_option(value)


def ref_of_note(record: dict[str, object]) -> str:
    return f"note:{record['id']}"


async def chosen_picker(page: Page, *refs: str) -> None:
    """Name what a note concerns in the dialog's picker: type each and press Add (the picker's
    own button, which is not the one of a dialog that has an Add of its own)."""
    box = dialog(page)
    for ref in refs:
        await box.locator(".picker input[type=text]").fill(ref)
        await box.locator(".picker-row").get_by_role("button", name="Add", exact=True).click()


async def history_on_page(page: Page) -> list[str]:
    """The actions in the history section of a note's page, oldest first (the page shows the
    newest first)."""
    actions: list[str] = (
        await part(page, "history")
        .locator("li[data-action]")
        .evaluate_all("els => els.map(e => e.dataset.action)")
    )
    return list(reversed(actions))


async def strip(page: Page) -> dict[str, str]:
    """The strip in the head of a note's page (Status, Scope and, for a decided note, "Decided
    by"): each label and what it says."""
    found: list[list[str]] = await page.locator("#item-page dl.state-strip > div").evaluate_all(
        "els => els.map(e => [e.querySelector('dt').textContent.trim(),"
        " e.querySelector('dd').textContent.replace(/\\s+/g, ' ').trim()])"
    )
    return {label: words for label, words in found}


def status_chip(page: Page) -> Locator:
    """The chip in the strip of a note's page that says where the note stands."""
    return page.locator("#item-page .card-summary dd.status .chip.note-status")


async def says_in_the_path(page: Page, view: str, code_: str, headline: str) -> None:
    """The path above a note's page: the view it was reached from, then the note itself, by its
    code and its headline ("N-7 Use short codes"); the whole of it is the step's tooltip."""
    name = f"{code_} {headline}"
    await expect(page.locator("#crumb-page")).to_have_attribute("title", name, timeout=SLOW)
    assert await crumbs(page) == [view, name], await crumbs(page)


# -- 1. a note with and without a decider, added on the board, are the same records everywhere ---


async def test_a_note_with_and_without_a_decider_added_on_the_board_are_the_same_records_everywhere(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Context {word}")
        story = await made(client, "story", f"Concerned story {word}", epic)
        rule = (await ok(client, "create_rule", title=f"Concerned {word}", node=story))["id"]

        async with browser_page(board, token=project.key) as w:
            page = w.page
            # A note nobody decided, with where it came from, added from the page of the story it
            # is about: the dialog starts with the story chosen and a criterion is added to it.
            # Who decided it and when are two fields of their own, empty and optional, not a switch.
            await open_item(w, story, "lists")
            await part(page, "context").locator("button.add-note").click()
            box = dialog(page)
            await expect(box.locator(f".picker li[data-ref='{story}']")).to_be_visible()
            await expect(box.get_by_label("Decided by (optional)")).to_be_visible()
            await expect(box.locator("#add-note-by")).to_have_value("")
            await expect(box.locator("#add-note-on")).to_have_value("")
            await expect(box.locator("#add-note-decided")).to_have_count(0)
            await box.locator("#add-note-text").fill(f"Note about {word}\n\nThe rest of it")
            await box.locator("#add-note-why").fill(f"The call with Marie {word}")
            await chosen_picker(page, f"criterion {rule}")
            await box.get_by_role("button", name="Add the note").click()
            await expect(box).to_have_count(0, timeout=SLOW)
            # The story's page has it in its table of notes, without being loaded again.
            await expect(page.locator("#item-page table.notes-table tr.record-line")).to_have_count(
                1, timeout=SLOW
            )

            # A note someone decided, with the person who decided it and the day, about the story,
            # from the Notes list: the two optional fields are filled in.
            await notes_open(page, board, context_list(epic))
            await expect(page.locator("#ctx-notes-count")).to_have_attribute("data-total", "1")
            await page.locator("#add-note").click()
            box = dialog(page)
            await box.locator("#add-note-text").fill(f"Decided {word}\n\nWhat was decided {word}")
            await box.locator("#add-note-why").fill(f"Because of {word}")
            await box.locator("#add-note-by").fill(MARIE)
            await box.locator("#add-note-on").fill("2026-11-02")
            await chosen_picker(page, code(story))
            await box.get_by_role("button", name="Add the note").click()
            await expect(box).to_have_count(0, timeout=SLOW)

            notes = (await ok(client, "list_notes", node=story))["items"]
            assert len(notes) == 2, notes
            plain_note = next(one for one in notes if not one["decided_by"])
            decision = next(one for one in notes if one["decided_by"])
            full_plain = await ok(client, "get_note", note_id=plain_note["id"])
            full_decision = await ok(client, "get_note", note_id=decision["id"])
            assert (full_plain["text"], full_plain["why"], full_plain["recorded_by"]) == (
                f"Note about {word}\n\nThe rest of it",
                f"The call with Marie {word}",
                ADA,
            )
            assert (full_plain["recorded_by_kind"], full_plain["decided_by"]) == ("person", None)
            assert full_plain["status"] == "unconfirmed"
            assert full_plain["confirmed_by"] is None and full_plain["project_wide"] is False
            assert (full_plain["nodes"], full_plain["rule_ids"], full_plain["note_ids"]) == (
                [story],
                [rule],
                [],
            )
            assert (full_decision["text"], full_decision["why"]) == (
                f"Decided {word}\n\nWhat was decided {word}",
                f"Because of {word}",
            )
            assert (full_decision["decided_by"], full_decision["decided_on"]) == (
                MARIE,
                "2026-11-02",
            )
            assert (full_decision["project_wide"], full_decision["status"]) == (
                False,
                "unconfirmed",
            )
            assert full_decision["nodes"] == [story]
            note_key, decision_key = ref_of_note(full_plain), ref_of_note(full_decision)
            for record, links in ((decision, 1), (plain_note, 2)):
                events = (await ok(client, "note_history", note_id=record["id"]))["items"]
                assert [(e["action"], e["actor"]) for e in events] == [
                    ("note_recorded", ADA),
                    *[("note_linked", ADA)] * links,
                ]

            # The list: the same ids and fields, one status chip that says Unconfirmed, and the
            # decider as a quiet detail of its own, which a note nobody decided leaves empty.
            for key, record in ((note_key, full_plain), (decision_key, full_decision)):
                row = listed(page, key)
                await expect(row).to_be_visible(timeout=SLOW)
                await expect(row).to_have_attribute("data-id", str(record["id"]))
                await expect(row).to_have_attribute("data-status", "unconfirmed")
                await expect(row).to_have_attribute("data-project-wide", "false")
                await expect(row.locator(".chip.note-status")).to_have_text("Unconfirmed")
                await expect(row.locator("td.cell-applies")).to_contain_text(code(story))
            await expect(listed(page, note_key).locator("td.cell-decided")).to_have_text("")
            await expect(listed(page, note_key)).to_contain_text(f"Note about {word}")
            await expect(listed(page, decision_key)).to_contain_text(f"Decided {word}")
            await expect(listed(page, decision_key).locator("td.cell-decided")).to_have_text(
                f"{MARIE}, 2026-11-02"
            )
            await expect(
                listed(page, decision_key).locator("td.cell-decided .clamp")
            ).to_have_attribute("title", f"Decided by {MARIE} on 2026-11-02")
            await expect(listed(page, note_key).locator("td.cell-applies")).to_contain_text(
                f"AC-{rule}"
            )
            # A row's title goes to the note's own page, in the view it was reached from.
            await listed(page, note_key).locator("a.record-title").click()
            await item_loaded(page, note_key)
            assert (await current_address(page))[1]["from"] == "lists"
            await page.go_back()
            await expect(listed(page, note_key)).to_be_visible(timeout=SLOW)

            # The Graph's Around draws the same two notes for the story, in recall's order.
            await go(page, board, f"#/graph?tab=around&focus={story}")
            await expect(page.locator(f".graph-around[data-focus='{story}']")).to_be_visible(
                timeout=SLOW
            )
            drawn = await page.locator(
                "section[data-group=notes] button.graph-node.n-note"
            ).evaluate_all("els => els.map(e => e.dataset.ref)")
            recalled = await ok(client, "recall", node=story)
            assert drawn == [ref_of_note(i["note"]) for i in recalled["notes"]["items"]]
            assert drawn == [decision_key, note_key]

            # The note's page: every field the server holds, and what it concerns as a table.
            await open_item(w, note_key, "lists")
            shown = detail(page, note_key)
            await expect(shown).to_have_attribute("data-status", "unconfirmed")
            await expect(page.locator("#item-title")).to_contain_text(f"N-{full_plain['id']} ")
            await expect(page.locator("#item-title")).to_contain_text(f"Note about {word}")
            await expect(part(page, "why")).to_contain_text(f"The call with Marie {word}")
            assert (await strip(page)).get("Recorded by") is None
            # Who recorded it is in its history now, the last row, and not in the strip.
            await expect(
                part(page, "history").locator("li[data-action=note_recorded]")
            ).to_contain_text(ADA)
            assert "Decided by" not in await page.locator("#item-page").inner_text()
            await expect(status_chip(page)).to_have_text("Unconfirmed")
            assert [r["ref"] for r in await record_rows(page, "concerns")] == [
                story,
                f"rule:{rule}",
            ]
            assert await history_on_page(page) == await history_actions(
                client, "note_history", "note_id", full_plain["id"]
            )
            await says_in_the_path(page, "Lists", f"N-{full_plain['id']}", f"Note about {word}")
            await expect(page.locator("#crumb-page")).to_have_attribute(
                "title", re.compile(f"Note about {word}")
            )
            await no_panel_or_drawer(page)
            # What the concerns table names leads on to that page.
            await record_row(page, story).locator("a.record-title").click()
            await item_loaded(page, story)
            assert (await current_address(page))[1]["from"] == "lists"

            # The page of the note someone decided: its text, its reason, and in the strip who
            # decided it and when.
            await open_item(w, decision_key, "lists")
            await expect(page.locator("#item-title")).to_contain_text(f"N-{full_decision['id']} ")
            await expect(part(page, "record").locator(".record-text")).to_contain_text(
                f"What was decided {word}"
            )
            await expect(part(page, "why")).to_contain_text(f"Because of {word}")
            await expect(page.locator("#item-page .card-summary dd.decided")).to_have_text(
                f"{MARIE} on 2026-11-02"
            )
            assert [r["ref"] for r in await record_rows(page, "concerns")] == [story]
            assert await history_on_page(page) == ["note_recorded", "note_linked"]

            # Exact recall, section by section, for the story and for the acceptance criterion: the
            # story's page has both notes as rows of its table.
            await open_item(w, story, "lists")
            await recall_matches(client, page, node=story)
            assert [i["note"]["id"] for i in recalled["notes"]["items"]] == [
                decision["id"],
                plain_note["id"],
            ]
            assert [r["id"] for r in await record_rows(page, "notes")] == [
                decision["id"],
                plain_note["id"],
            ]
            await open_item(w, f"rule:{rule}", "lists")
            await recall_matches(client, page, rule_id=rule)
            # What is linked to the story a criterion is placed on comes down to it, and says so.
            assert [
                i["note"]["id"]
                for i in (await ok(client, "recall", rule_id=rule))["notes"]["items"]
            ] == [decision["id"], plain_note["id"]]
            came = {r["id"]: r["from_ref"] for r in await record_rows(page, "notes")}
            assert came == {decision["id"]: story, plain_note["id"]: None}
            assert w.problems == [], w.problems


# -- 2. confirming and un-confirming ---------------------------------------------------------------


async def test_confirming_asks_for_the_person_and_shows_who_and_when_and_unconfirming_needs_words(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Confirming {word}")
        story = await made(client, "story", f"Reviewed story {word}", epic)
        note = await make_note(client, f"Reviewed note {word}", "Seen on the call", nodes=[story])
        decision = await make_decision(client, f"Reviewed decision {word}", nodes=[story])
        note_key, decision_key = ref_of_note(note), ref_of_note(decision)
        # An agent recording a statement is not a person's review.
        assert (note["status"], decision["status"]) == ("unconfirmed", "unconfirmed")

        async with browser_page(board, token=project.key) as w:
            page = w.page
            await open_item(w, note_key, "lists")
            record = detail(page, note_key)
            chip = status_chip(page)
            await expect(chip).to_have_attribute("data-status", "unconfirmed", timeout=SLOW)
            # One chip says where the note stands. Its menu offers to confirm it (it is not
            # confirmed yet), to replace it, to withdraw it and to change what it concerns.
            await chip.click()
            menu = page.locator("div.vmenu[data-menu='note-status']")
            assert await menu.locator(".vmenu-option").evaluate_all(
                "els => els.map(e => e.dataset.value)"
            ) == ["confirm", "replace", "withdraw", "concerns"]
            await page.keyboard.press("Escape")

            # Confirming asks who reviewed it, and the board offers the name it is signed in with.
            # Blank it: the refusal reads as it does for the board's own call (only a person can
            # confirm), nothing is written and what was typed stays.
            asks = {"note_id": note["id"]}
            nobody = board_refusal(
                project, "set_confirmation", confirmation="confirmed", person="", **asks
            )
            await choose_in_menu(page, chip, "confirm")
            box = dialog(page)
            await expect(box.locator("#confirm-person")).to_have_value(ADA)
            await box.locator("#confirm-person").fill("")
            await box.locator("#confirm-quote").fill(f"I read it {word}")
            await box.get_by_role("button", name="Confirm", exact=True).click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(spoken(await refusal_in(page).inner_text()), spoken(nobody))
            assert await box.locator("#confirm-quote").input_value() == f"I read it {word}"
            assert (await ok(client, "get_note", note_id=note["id"]))["status"] == "unconfirmed"
            await box.get_by_role("button", name="Cancel").click()
            await expect(box).to_have_count(0)
            await expect(chip).to_have_attribute("data-status", "unconfirmed")

            # With the person's name it is confirmed, and who and when are shown and read back.
            await choose_in_menu(page, chip, "confirm")
            box = dialog(page)
            await box.locator("#confirm-person").fill(MARIE)
            await box.locator("#confirm-quote").fill(f"I read it twice {word}")
            await box.get_by_role("button", name="Confirm", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            await expect(record).to_have_attribute("data-status", "confirmed", timeout=SLOW)
            reviewed = await ok(client, "get_note", note_id=note["id"])
            assert (reviewed["status"], reviewed["confirmed_by"]) == ("confirmed", MARIE)
            assert reviewed["confirmed_at"] and reviewed["recorded_by"] == "tests"
            assert reviewed["text"] == note["text"]
            await expect(chip).to_have_text("Confirmed")
            await expect(chip).to_have_attribute(
                "title", f"Confirmed by {MARIE}, {reviewed['confirmed_at'][:10]}"
            )
            await expect(part(page, "confirmation")).to_contain_text(f"Confirmed by {MARIE}")
            # What the person said is kept, and shown whole where the confirmation is and in the
            # history.
            await expect(part(page, "confirmation").locator("blockquote")).to_have_text(
                f"I read it twice {word}"
            )
            events = await history_actions(client, "note_history", "note_id", note["id"])
            assert events[-1] == "note_confirmed"
            await expect(
                part(page, "history").locator("li[data-action=note_confirmed]")
            ).to_contain_text(ADA, timeout=SLOW)
            await expect(
                part(page, "history").locator("li[data-action=note_confirmed]")
            ).to_contain_text(f"saying: “I read it twice {word}”")
            assert await history_on_page(page) == events

            # The list row, the node in Around and the recall entry on the story's page agree.
            await notes_open(page, board, context_list(epic))
            await expect(listed(page, note_key)).to_have_attribute("data-status", "confirmed")
            await expect(listed(page, note_key).locator(".chip.note-status")).to_have_attribute(
                "title", re.compile(rf"Confirmed by {MARIE}")
            )
            await go(page, board, f"#/graph?tab=around&focus={story}")
            around = page.locator("section[data-group=notes] button.graph-node.n-note")
            await expect(around).to_have_count(2, timeout=SLOW)
            await expect(around.filter(has=page.locator("css=.n-badge.ok"))).to_have_attribute(
                "data-ref", note_key
            )
            await expect(
                page.locator(f"button.graph-node[data-ref='{decision_key}']")
            ).to_have_class(re.compile(r"\bis-unconfirmed\b"))
            await open_item(w, story, "lists")
            await recall_matches(client, page, node=story)
            await expect(record_row(page, note_key)).to_have_attribute("data-status", "confirmed")
            await expect(record_row(page, decision_key)).to_have_attribute(
                "data-status", "unconfirmed"
            )

            # A note someone decided is confirmed from its row of the list, by the same chip, with
            # the words the person said.
            await notes_open(page, board, context_list(epic))
            await choose_in_menu(
                page, listed(page, decision_key).locator(".chip.note-status"), "confirm"
            )
            box = dialog(page)
            await box.locator("#confirm-person").fill(MARIE)
            await box.locator("#confirm-quote").fill(f"Yes, that is what we decided {word}")
            await box.get_by_role("button", name="Confirm", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            await expect(listed(page, decision_key)).to_have_attribute(
                "data-status", "confirmed", timeout=SLOW
            )
            decided = await ok(client, "get_note", note_id=decision["id"])
            assert (decided["status"], decided["confirmed_by"]) == ("confirmed", MARIE)
            assert decided["confirmed_at"] and decided["decided_by"] == "Marie"
            assert (await history_actions(client, "note_history", "note_id", decision["id"]))[
                -1
            ] == "note_confirmed"
            # Its own page says who and when, and the history names the person who made the change.
            await open_item(w, decision_key, "lists")
            await expect(detail(page, decision_key)).to_have_attribute(
                "data-status", "confirmed", timeout=SLOW
            )
            await expect(status_chip(page)).to_have_text("Confirmed")
            await expect(part(page, "confirmation")).to_contain_text(f"Confirmed by {MARIE}")
            await expect(
                part(page, "history").locator("li[data-action=note_confirmed]")
            ).to_contain_text(ADA)

            # Un-confirming needs the person who says so and their words: both are refused when
            # blank, each time in the words of the tool, and nothing is changed until both are
            # given. The menu of a confirmed note offers to un-confirm it.
            await open_item(w, note_key, "lists")
            await expect(chip).to_have_attribute("data-status", "confirmed", timeout=SLOW)
            await chip.click()
            assert await menu.locator(".vmenu-option").evaluate_all(
                "els => els.map(e => e.dataset.value)"
            ) == ["unconfirm", "replace", "withdraw", "concerns"]
            await page.keyboard.press("Escape")
            nobody = board_refusal(
                project,
                "set_confirmation",
                confirmation="unconfirmed",
                person="",
                quote="",
                **asks,
            )
            silent = board_refusal(
                project,
                "set_confirmation",
                confirmation="unconfirmed",
                person=MARIE,
                quote="",
                **asks,
            )
            assert nobody != silent
            await choose_in_menu(page, chip, "unconfirm")
            box = dialog(page)
            await box.locator("#confirm-person").fill("")
            await box.get_by_role("button", name="Un-confirm", exact=True).click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(spoken(await refusal_in(page).inner_text()), spoken(nobody))
            await box.locator("#confirm-person").fill(MARIE)
            await box.get_by_role("button", name="Un-confirm", exact=True).click()
            await expect(refusal_in(page)).to_contain_text("needs what the person said")
            plain(spoken(await refusal_in(page).inner_text()), spoken(silent))
            assert await box.locator("#confirm-person").input_value() == MARIE
            assert (await ok(client, "get_note", note_id=note["id"]))["status"] == "confirmed"
            await box.locator("#confirm-quote").fill(f"On second thought, no {word}")
            await box.get_by_role("button", name="Un-confirm", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            await expect(record).to_have_attribute("data-status", "unconfirmed", timeout=SLOW)
            again = await ok(client, "get_note", note_id=note["id"])
            assert (again["status"], again["confirmed_by"], again["confirmed_at"]) == (
                "unconfirmed",
                None,
                None,
            )
            # Taking it back is said, with who and what they said, not "no person has reviewed it".
            await expect(part(page, "confirmation")).to_contain_text(
                f"Confirmation was taken back by {MARIE}", timeout=SLOW
            )
            await expect(part(page, "confirmation").locator("blockquote")).to_have_text(
                f"On second thought, no {word}"
            )
            # The earlier confirmation stays in its history, with who did each.
            assert (await history_actions(client, "note_history", "note_id", note["id"]))[-2:] == [
                "note_confirmed",
                "note_unconfirmed",
            ]
            history = part(page, "history").locator("li")
            await expect(history.filter(has_text=ADA).first).to_be_visible()
            await expect(
                part(page, "history").locator("li[data-action=note_confirmed]")
            ).to_have_count(1)
            await expect(
                part(page, "history").locator("li[data-action=note_unconfirmed]")
            ).to_have_count(1)
            assert w.problems == [], w.problems


# -- 3. replacing and withdrawing: a note that ended keeps the confirmation it had ----------------


async def test_replacing_and_withdrawing_a_note_keeps_it_and_its_confirmation_and_needs_a_word(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Ending {word}")
        story = await made(client, "story", f"Ended story {word}", epic)
        old = await make_decision(client, f"Old decision {word}", nodes=[story])
        confirm_on_board("note", old, MARIE, project=project)
        wrong = await make_note(client, f"Wrong note {word}", "Heard it", nodes=[story])
        confirm_on_board("note", wrong, MARIE, project=project)
        slip = await make_note(client, f"Slip {word}", "Heard it", nodes=[story])
        drop = await make_decision(client, f"Dropped decision {word}", nodes=[story])
        old_key, wrong_key, slip_key, drop_key = (
            ref_of_note(one) for one in (old, wrong, slip, drop)
        )

        async with browser_page(board, token=project.key) as w:
            page = w.page
            await notes_open(page, board, context_list(epic))
            before = await tally(client)
            history_before = await history_actions(client, "note_history", "note_id", old["id"])

            # Replacing a confirmed note, from the menu of its status chip in its row: the dialog
            # says a person confirmed it, starts from its words and asks for that person's word.
            # The note was decided by someone, so the dialog offers the person replacing it as the
            # one who decides now, in the field that is there for it.
            await choose_in_menu(
                page, listed(page, old_key).locator(".chip.note-status"), "replace"
            )
            box = dialog(page)
            await expect(box.locator("details.said-by")).to_have_attribute("open", "")
            assert await box.locator("#supersede-text").input_value() == old["text"]
            assert await box.locator("#supersede-by").input_value() == ADA
            new_text = f"New decision {word}\n\nWhat is decided now {word}"
            await box.locator("#supersede-text").fill(new_text)
            attempt = {
                "replaces": [old["id"]],
                "text": new_text,
                "decided_by": ADA,
                "project_wide": False,
                "keep_links": True,
            }
            no_why = await refused(client, "supersede_note", why="", **attempt)
            no_person = await refused(client, "supersede_note", why="We learned more", **attempt)
            assert "needs that person's word" in no_person and no_why != no_person
            await box.get_by_role("button", name="Replace with this note").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(spoken(await refusal_in(page).inner_text()), spoken(no_why))
            await box.locator("#supersede-why").fill("We learned more")
            await box.get_by_role("button", name="Replace with this note").click()
            await expect(refusal_in(page)).to_contain_text("needs that person's word")
            plain(spoken(await refusal_in(page).inner_text()), spoken(no_person))
            # Nothing was written, and what was typed is still there.
            assert await tally(client) == before
            assert (await ok(client, "get_note", note_id=old["id"]))["status"] == "confirmed"
            assert (
                await history_actions(client, "note_history", "note_id", old["id"])
                == history_before
            )
            assert await box.locator("#supersede-text").input_value() == new_text
            assert await box.locator("#supersede-why").input_value() == "We learned more"
            # With who changed their mind and what they said, it goes through.
            await box.locator("#supersede-said-by").fill(MARIE)
            await box.locator("#supersede-quote").fill(f"Yes, change it {word}")
            await box.get_by_role("button", name="Replace with this note").click()
            await expect(box).to_have_count(0, timeout=SLOW)

            ended = await ok(client, "get_note", note_id=old["id"])
            new = await ok(client, "get_note", note_id=ended["superseded_by_id"])
            new_key = ref_of_note(new)
            assert (ended["status"], ended["status_reason"]) == ("superseded", None)
            # The earlier confirmation stays on the replaced note, and the new one has none.
            assert (ended["status"], ended["confirmed_by"]) == ("superseded", MARIE)
            assert (new["status"], new["decided_by"], new["supersedes_ids"]) == (
                "unconfirmed",
                ADA,
                [old["id"]],
            )
            assert new["nodes"] == [story] and new["why"] == "We learned more"
            events = (await ok(client, "note_history", note_id=old["id"]))["items"]
            assert (events[-1]["action"], events[-1]["actor"]) == ("note_superseded", ADA)
            assert "Yes, change it" in str(events[-1])

            # In the list: in force by default; Replaced when asked for, with the confirmation it
            # had kept in its words, and nothing on its row can change it any more.
            await expect(listed(page, old_key)).to_have_count(0)
            await expect(listed(page, new_key)).to_have_attribute("data-status", "unconfirmed")
            await status_filter(page, "superseded")
            gone = listed(page, old_key)
            await expect(gone).to_have_attribute("data-status", "superseded")
            await expect(gone.locator(".chip.note-status")).to_have_text("Replaced")
            await expect(gone).to_have_class(re.compile(r"\bended\b"))
            await expect(gone.locator("span.chip.note-status")).to_have_count(1)
            await expect(gone.locator("button.chip")).to_have_count(0)
            await gone.locator("a.record-title").click()
            await item_loaded(page, old_key)
            # The page of the replaced note says it was confirmed and that it is no longer in
            # force. It cannot be changed any more, and leads to what replaced it.
            ended_page = detail(page, old_key)
            await expect(ended_page).to_have_attribute("data-status", "superseded", timeout=SLOW)
            await expect(page.locator("#record-ended")).to_be_visible()
            await expect(status_chip(page)).to_have_text("Replaced")
            await expect(part(page, "confirmation")).to_contain_text(f"It was confirmed by {MARIE}")
            await expect(
                page.locator(
                    "#item-page button.supersede, #item-page button.withdraw,"
                    " #item-page button.change-concerns"
                )
            ).to_have_count(0)
            [replaced_by] = await record_rows(page, "replaces")
            assert (replaced_by["relation"], replaced_by["id"]) == ("Replaced by", new["id"])
            await record_row(page, new_key).locator("a.record-title").click()
            await item_loaded(page, new_key)
            await expect(detail(page, new_key)).to_have_attribute("data-status", "unconfirmed")
            [replaces] = await record_rows(page, "replaces")
            assert (replaces["relation"], replaces["id"]) == ("Replaces", old["id"])
            await expect(part(page, "why")).to_contain_text("We learned more")
            assert (await current_address(page))[1]["from"] == "lists"

            # Recall: the replaced note is a past note (in the "Earlier" section of the story's
            # page, open since it holds so few), the new one is in force.
            await open_item(w, story, "lists")
            await recall_matches(client, page, node=story)
            past = (await ok(client, "recall", node=story))["past_notes"]["items"]
            assert [(i["note"]["id"], i["note"]["status"]) for i in past] == [
                (old["id"], "superseded")
            ]
            assert await disclosure(page, "past_notes") == {"open": True, "count": 1}
            [earlier] = await record_rows(page, "notes", section="past_notes")
            assert (earlier["id"], earlier["status"]) == (old["id"], "superseded")
            assert earlier["visible"]

            # Withdrawing a confirmed note, from its page, also needs the person's word; a reason
            # is always needed.
            before = await tally(client)
            await open_item(w, wrong_key, "lists")
            await page.locator("#item-page button.withdraw").click()
            box = dialog(page)
            asks = {"note_ids": [wrong["id"]]}
            none_given = await refused(client, "withdraw_note", reason="", **asks)
            reason_only = await refused(client, "withdraw_note", reason="It is wrong", **asks)
            await box.get_by_role("button", name="Withdraw", exact=True).click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(spoken(await refusal_in(page).inner_text()), spoken(none_given))
            await box.locator("#withdraw-reason").fill("It is wrong")
            await box.get_by_role("button", name="Withdraw", exact=True).click()
            await expect(refusal_in(page)).to_contain_text("needs that person's word")
            plain(spoken(await refusal_in(page).inner_text()), spoken(reason_only))
            assert (await ok(client, "get_note", note_id=wrong["id"]))["status"] == "confirmed"
            assert await tally(client) == before
            await box.locator("#withdraw-said-by").fill(MARIE)
            await box.locator("#withdraw-quote").fill("No, that is wrong")
            await box.get_by_role("button", name="Withdraw", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            withdrawn = await ok(client, "get_note", note_id=wrong["id"])
            assert (withdrawn["status"], withdrawn["status_reason"]) == ("withdrawn", "It is wrong")
            assert withdrawn["confirmed_by"] == MARIE
            await expect(
                page.locator("#item-page [data-part=lifecycle] .ended-reason")
            ).to_contain_text("Withdrawn: It is wrong", timeout=SLOW)
            await expect(page.locator("#item-page button.withdraw")).to_have_count(0)

            # An unconfirmed note needs only the reason: from the menu of its status chip this time.
            await notes_open(page, board, context_list(epic))
            await choose_in_menu(
                page, listed(page, slip_key).locator(".chip.note-status"), "withdraw"
            )
            box = dialog(page)
            await expect(box.locator("details.said-by")).not_to_have_attribute("open", "")
            await box.locator("#withdraw-reason").fill("A slip of the tongue")
            await box.get_by_role("button", name="Withdraw", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            assert (await ok(client, "get_note", note_id=slip["id"]))["status"] == "withdrawn"
            # So does a note nobody confirmed, from its page, and a blank reason is refused.
            await open_item(w, drop_key, "lists")
            await page.locator("#item-page button.withdraw").click()
            box = dialog(page)
            blank = await refused(client, "withdraw_note", note_ids=[drop["id"]], reason="")
            await box.get_by_role("button", name="Withdraw", exact=True).click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(spoken(await refusal_in(page).inner_text()), spoken(blank))
            assert (await ok(client, "get_note", note_id=drop["id"]))["status"] == "unconfirmed"
            await box.locator("#withdraw-reason").fill("No longer needed")
            await box.get_by_role("button", name="Withdraw", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            dropped = await ok(client, "get_note", note_id=drop["id"])
            assert (dropped["status"], dropped["status_reason"]) == (
                "withdrawn",
                "No longer needed",
            )
            assert dropped["confirmed_by"] is None
            await expect(status_chip(page)).to_have_text("Withdrawn")

            await notes_open(page, board, context_list(epic))
            await status_filter(page, "withdrawn")
            for key, confirmer in ((wrong_key, MARIE), (slip_key, None), (drop_key, None)):
                row = listed(page, key)
                await expect(row).to_have_attribute("data-status", "withdrawn", timeout=SLOW)
                await expect(row.locator(".chip.note-status")).to_have_text("Withdrawn")
                note_now = await ok(client, "get_note", note_id=int(key.split(":")[1]))
                assert note_now["confirmed_by"] == confirmer
            # "All" lists every status together, each told apart by its own chip.
            await status_filter(page, "all")
            await expect(listed(page, new_key)).to_have_attribute("data-status", "unconfirmed")
            await expect(listed(page, old_key)).to_have_attribute("data-status", "superseded")
            await expect(listed(page, wrong_key)).to_have_attribute("data-status", "withdrawn")
            # The story's recall now holds the new note, and four earlier ones (open, as there are
            # fewer than five).
            await open_item(w, story, "lists")
            await recall_matches(client, page, node=story)
            assert await disclosure(page, "past_notes") == {"open": True, "count": 4}
            assert [r["id"] for r in await record_rows(page, "notes", section="notes")] == [
                new["id"]
            ]
            assert w.problems == [], w.problems


# -- 4. blanks and what is not a person's: refused with nothing written, and nothing typed lost ----


async def test_a_blank_text_reason_or_person_is_refused_in_the_servers_words_and_writes_nothing(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Blanks {word}")
        story = await made(client, "story", f"Blank story {word}", epic)
        kept = await make_note(client, f"Kept note {word}", "Seen", nodes=[story])
        kept_key = ref_of_note(kept)
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await notes_open(page, board, context_list(epic))
            before = await tally(client)

            # A note with no text, then one with no reason.
            await page.locator("#add-note").click()
            box = dialog(page)
            await box.locator("#add-note-why").fill(f"Because {word}")
            await chosen_picker(page, code(story))
            base = {"project_wide": False, "nodes": [story]}
            blank_text = await refused(client, "add_note", text="", why=f"Because {word}", **base)
            await box.get_by_role("button", name="Add the note").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(spoken(await refusal_in(page).inner_text()), spoken(blank_text))
            assert await box.locator("#add-note-why").input_value() == f"Because {word}"
            await expect(box.locator(f".picker li[data-ref='{story}']")).to_be_visible()
            assert await tally(client) == before
            await box.locator("#add-note-text").fill(f"No reason {word}")
            await box.locator("#add-note-why").fill("")
            blank_why = await refused(client, "add_note", text=f"No reason {word}", why="", **base)
            await box.get_by_role("button", name="Add the note").click()
            await expect(refusal_in(page)).to_contain_text("why", timeout=SLOW)
            plain(spoken(await refusal_in(page).inner_text()), spoken(blank_why))
            assert blank_text != blank_why
            assert await box.locator("#add-note-text").input_value() == f"No reason {word}"
            assert await tally(client) == before

            # A day with nobody who decided it.
            await box.locator("#add-note-why").fill(f"Because {word}")
            await box.locator("#add-note-by").fill("")
            await box.locator("#add-note-on").fill("2026-11-02")
            no_person = await refused(
                client,
                "add_note",
                text=f"No reason {word}",
                why=f"Because {word}",
                decided_by="",
                decided_on="2026-11-02",
                **base,
            )
            assert "decided_on needs decided_by" in no_person
            await box.get_by_role("button", name="Add the note").click()
            await expect(refusal_in(page)).to_contain_text("decided_on needs decided_by")
            plain(spoken(await refusal_in(page).inner_text()), spoken(no_person))
            for name, value in (
                ("#add-note-text", f"No reason {word}"),
                ("#add-note-why", f"Because {word}"),
                ("#add-note-on", "2026-11-02"),
            ):
                assert await box.locator(name).input_value() == value
            assert await tally(client) == before
            await box.get_by_role("button", name="Cancel").click()

            # Confirming with no person: the chip goes back to what is true, and nothing changes.
            chip = listed(page, kept_key).locator(".chip.note-status")
            await choose_in_menu(page, chip, "confirm")
            box = dialog(page)
            await box.locator("#confirm-person").fill("  ")
            await box.get_by_role("button", name="Confirm", exact=True).click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            await box.get_by_role("button", name="Cancel").click()
            await expect(listed(page, kept_key)).to_have_attribute("data-status", "unconfirmed")
            assert (await ok(client, "get_note", note_id=kept["id"]))["status"] == "unconfirmed"
            assert await tally(client) == before

            # The same blanks on the note's own page: a replacement with no text, and one with no
            # reason, are refused in the same words and write nothing.
            await open_item(w, kept_key, "lists")
            attempt = {"replaces": [kept["id"]], "project_wide": False, "keep_links": True}
            blank_replacement = await refused(
                client, "supersede_note", text="", why="Changed", **attempt
            )
            await page.locator("#item-page button.supersede").click()
            box = dialog(page)
            await box.locator("#supersede-text").fill("")
            await box.locator("#supersede-why").fill("Changed")
            await box.get_by_role("button", name="Replace with this note").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(spoken(await refusal_in(page).inner_text()), spoken(blank_replacement))
            assert await box.locator("#supersede-why").input_value() == "Changed"
            await box.locator("#supersede-text").fill(f"Changed {word}")
            await box.locator("#supersede-why").fill("")
            blank_reason = await refused(
                client, "supersede_note", text=f"Changed {word}", why="", **attempt
            )
            await box.get_by_role("button", name="Replace with this note").click()
            await expect(refusal_in(page)).to_contain_text("why", timeout=SLOW)
            plain(spoken(await refusal_in(page).inner_text()), spoken(blank_reason))
            assert blank_replacement != blank_reason
            assert await box.locator("#supersede-text").input_value() == f"Changed {word}"
            assert await tally(client) == before
            await box.get_by_role("button", name="Cancel").click()
            assert (await ok(client, "get_note", note_id=kept["id"]))["status"] == "unconfirmed"
            assert w.problems == [], w.problems


# -- 5. what a note concerns, changed on its page ------------------------------------------------


async def test_what_a_note_concerns_is_changed_on_its_page_and_recall_follows_it(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Concerning {word}")
        first = await made(client, "story", f"First concerned {word}", epic)
        second = await made(client, "story", f"Second concerned {word}", epic)
        decision = await make_decision(client, f"Linked decision {word}", nodes=[first])
        note = await make_note(client, f"Moving note {word}", "Seen", nodes=[first])
        note_key, decision_key = ref_of_note(note), ref_of_note(decision)

        async with browser_page(board, token=project.key) as w:
            page = w.page
            await open_item(w, note_key, "board")
            assert [r["ref"] for r in await record_rows(page, "concerns")] == [first]
            await part(page, "concerns").locator("button.change-concerns").click()
            box = dialog(page)
            assert await box.locator(".concerns-list li[data-ref]").evaluate_all(
                "els => els.map(e => e.dataset.ref)"
            ) == [first]
            # Nothing chosen asks for something to add, and writes nothing.
            before = await ok(client, "get_note", note_id=note["id"])
            await box.locator("#concerns-add").click()
            await expect(box).to_contain_text("Choose what to add first")
            # A story and a note are added, one after the other, in the same dialog.
            await box.locator(".picker input[type=text]").fill(code(second))
            await box.locator(".picker input[type=text]").press("Enter")
            await box.locator(".picker input[type=text]").fill(f"note {decision['id']}")
            await box.locator("#concerns-add").click()
            await expect(box.locator(".concerns-list li[data-ref]")).to_have_count(3, timeout=SLOW)
            added = await ok(client, "get_note", note_id=note["id"])
            assert sorted(added["nodes"]) == sorted([first, second])
            assert added["note_ids"] == [decision["id"]]
            assert before["nodes"] == [first] and before["note_ids"] == []
            # The first story is taken out.
            await box.locator(f".concerns-list li[data-ref='{first}'] button.unlink").click()
            await expect(box.locator(".concerns-list li[data-ref]")).to_have_count(2, timeout=SLOW)
            await box.locator("#concerns-done").click()
            await expect(box).to_have_count(0)
            moved = await ok(client, "get_note", note_id=note["id"])
            assert (moved["nodes"], moved["note_ids"], moved["status"]) == (
                [second],
                [decision["id"]],
                "unconfirmed",
            )
            # The page's table follows, with a link to each, and the history says what was done.
            await expect(
                page.locator("#item-page table.concerns-table tr.record-line")
            ).to_have_count(2, timeout=SLOW)
            assert [r["ref"] for r in await record_rows(page, "concerns")] == [second, decision_key]
            actions = await history_on_page(page)
            assert actions == await history_actions(client, "note_history", "note_id", note["id"])
            assert "note_linked" in actions and "note_unlinked" in actions

            # Recall follows: the note is no longer on the first story's page and is on the
            # second's.
            await open_item(w, first, "board")
            await recall_matches(client, page, node=first)
            assert note["id"] not in [r["id"] for r in await record_rows(page, "notes")]
            await open_item(w, second, "board")
            await recall_matches(client, page, node=second)
            assert [r["id"] for r in await record_rows(page, "notes")] == [note["id"]]
            # What is about the other note is read from the tools; its own concerns are unchanged.
            assert [
                n["id"] for n in (await ok(client, "list_notes", note_id=decision["id"]))["items"]
            ] == [note["id"]]
            await open_item(w, decision_key, "board")
            assert [r["ref"] for r in await record_rows(page, "concerns")] == [first]
            # The table on the note's page leads to the other note's.
            await open_item(w, note_key, "board")
            await record_row(page, decision_key).locator("a.record-title").click()
            await item_loaded(page, decision_key)
            assert w.problems == [], w.problems


# -- 6. project-wide notes: in recall and the briefing of their project, never another's -----------


async def test_a_project_wide_note_is_in_its_projects_recall_and_briefing_and_never_anothers(
    server_url: str,
) -> None:
    word = unique_word()
    wide_a, wide_b = f"Wide A {word}", f"Wide B {word}"
    async with (
        fresh_project(server_url) as (a, board_a, first),
        fresh_project(server_url) as (b, board_b, second),
    ):
        trees = {}
        for client, project in ((a, first), (b, second)):
            epic = await made(client, "epic", f"Epic {word}")
            story = await made(client, "story", f"Story {word}", epic)
            await made(client, "task", f"Task {word}", story)
            trees[project.id] = (epic, story)
        epic_a, story_a = trees[first.id]
        epic_b, story_b = trees[second.id]
        other = await make_decision(b, wide_b, project_wide=True)
        assert other["project_wide"] is True

        async with browser_page(board_a, token=first.key) as w:
            page = w.page
            await notes_open(page, board_a, context_list(epic_a))
            await expect(page.locator("#ctx-notes-count")).to_have_attribute("data-total", "0")
            await expect(page.locator("#ctx-notes-list .ctx-none")).to_have_text("None yet.")
            await page.locator("#add-note").click()
            box = dialog(page)
            await box.locator("#add-note-text").fill(f"{wide_a}\n\nEverything is metric {word}")
            await box.locator("#add-note-why").fill(f"One system only {word}")
            await box.locator("#add-note-by").fill(MARIE)
            await box.locator("#add-note-project-wide").check()
            await box.get_by_role("button", name="Add the note").click()
            await expect(box).to_have_count(0, timeout=SLOW)

            mine = (await ok(a, "list_notes", project_wide=True))["items"]
            assert [(n["headline"], n["nodes"], n["rule_ids"]) for n in mine] == [(wide_a, [], [])]
            key = ref_of_note(mine[0])
            # It is in the list although it is linked to nothing, and says it is project-wide.
            row = listed(page, key)
            await expect(row).to_have_attribute("data-project-wide", "true", timeout=SLOW)
            await expect(row.locator("td.cell-applies")).to_contain_text("The whole project")
            await expect(row.locator(".chip.note-status")).to_have_text("Unconfirmed")

            # Recall of any item of the project carries it, flagged as unconfirmed; so does the
            # briefing for work, and the story's page shows what recall says: nothing is linked to
            # the story, so the project-wide section is open, with the note in it.
            recalled = await ok(a, "recall", node=story_a)
            wide_items = recalled["project_wide_notes"]["items"]
            assert [i["note"]["headline"] for i in wide_items] == [wide_a]
            assert wide_items[0]["note"]["status"] == "unconfirmed"
            assert recalled["notes"]["items"] == []
            brief = await ok(a, "next_work", within_node=epic_a, peek=True)
            wide_brief = brief["context"]["project_wide_notes"]
            assert [(n["text"].split("\n")[0], n["status"]) for n in wide_brief] == [
                (wide_a, "unconfirmed")
            ]
            await open_item(w, story_a, "lists")
            await recall_matches(a, page, node=story_a)
            assert await disclosure(page, "project_wide_notes") == {"open": True, "count": 1}
            entry = record_row(page, key)
            await expect(entry).to_have_attribute("data-project-wide", "true")
            await expect(entry).to_have_attribute("data-status", "unconfirmed")
            await expect(entry).to_be_visible()
            # Its own page says it applies throughout the project, with the whole project as the
            # first thing it applies to.
            await entry.locator("a.record-title").click()
            await item_loaded(page, key)
            [whole] = await record_rows(page, "concerns")
            assert whole["kind"] == "project" and whole["ref"] == "project"
            assert "Every epic, story and task in the project" in whole["text"]
            await expect(
                page.locator("#item-page .card-summary .chip.project-wide")
            ).to_be_visible()

            # Once a person confirms it, recall and the briefing say so, and who confirmed it.
            await notes_open(page, board_a, context_list(epic_a))
            await choose_in_menu(page, listed(page, key).locator(".chip.note-status"), "confirm")
            box = dialog(page)
            await box.locator("#confirm-person").fill(MARIE)
            await box.get_by_role("button", name="Confirm", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            recalled = await ok(a, "recall", node=story_a)
            confirmed = recalled["project_wide_notes"]["items"][0]["note"]
            assert (confirmed["status"], confirmed["confirmed_by"]) == ("confirmed", MARIE)
            brief = await ok(a, "next_work", within_node=epic_a, peek=True)
            [wide_brief] = brief["context"]["project_wide_notes"]
            assert (wide_brief["status"], wide_brief["confirmed_by"]) == ("confirmed", MARIE)
            await open_item(w, story_a, "lists")
            await expect(record_row(page, key)).to_have_attribute(
                "data-status", "confirmed", timeout=SLOW
            )
            assert w.problems == [], w.problems

        # The other project hears nothing of it: not in recall, the briefing, its list or its page.
        recalled_b = await ok(b, "recall", node=story_b)
        assert [i["note"]["headline"] for i in recalled_b["project_wide_notes"]["items"]] == [
            wide_b
        ]
        brief_b = await ok(b, "next_work", within_node=epic_b, peek=True)
        assert [n["text"].split("\n")[0] for n in brief_b["context"]["project_wide_notes"]] == [
            wide_b
        ]
        assert [n["headline"] for n in (await ok(b, "list_notes"))["items"]] == [wide_b]
        async with browser_page(board_b, token=second.key, keep_bodies=True) as w:
            page = w.page
            await notes_open(page, board_b, context_list(epic_b))
            await expect(page.locator("#ctx-notes-count")).to_have_attribute(
                "data-total", "1", timeout=SLOW
            )
            await expect(page.locator("#ctx-notes-list")).to_contain_text(wide_b)
            await open_item(w, story_b, "lists")
            await expect(part(page, "context")).to_contain_text(wide_b)
            shown = await page.locator("body").inner_text()
            assert wide_a not in shown and f"Everything is metric {word}" not in shown
            assert all(wide_a not in body for _, body in w.bodies)
            assert w.problems == [], w.problems


# -- 7. the tables of notes on a page ----------------------------------------------------------


async def test_project_wide_notes_are_closed_when_something_is_linked_and_open_when_not(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Wide {word}")
        linked = await made(client, "story", f"Has linked entries {word}", epic)
        bare = await made(client, "story", f"Has none {word}", epic)
        wide = [
            await make_decision(
                client,
                f"Wide {index} {word}",
                project_wide=True,
                text=f"Everything follows rule {index} " + "and then some more " * 12,
            )
            for index in range(3)
        ]
        direct = await make_decision(client, f"Direct {word}", nodes=[linked])
        async with browser_page(board, token=project.key) as w:
            page = w.page
            # Nothing is linked here, so what applies to everything is the page's first table, open.
            await open_item(w, bare, "board")
            assert await disclosure(page, "project_wide_notes") == {"open": True, "count": 3}
            rows = await record_rows(page, "notes", section="project_wide_notes")
            assert [r["id"] for r in rows] == sorted(d["id"] for d in wide)
            assert all(r["visible"] and r["project_wide"] for r in rows)
            await recall_matches(client, page, node=bare)

            # Something is linked here, so the linked one is first, and the few that merely apply
            # (fewer than five) are in a section that starts open.
            await open_item(w, linked, "board")
            assert await disclosure(page, "project_wide_notes") == {"open": True, "count": 3}
            [one] = await record_rows(page, "notes", section="notes")
            assert one["id"] == direct["id"] and one["visible"] and not one["project_wide"]
            rows = await record_rows(page, "notes", section="project_wide_notes")
            assert len(rows) == 3 and all(r["visible"] for r in rows)
            await recall_matches(client, page, node=linked)

            # Closing it is the person's choice, and news (an agent records a note here) keeps it.
            summary = page.locator("details[data-disclosure=project_wide_notes] summary")
            await summary.click()
            assert await disclosure(page, "project_wide_notes") == {"open": False, "count": 3}
            await make_note(client, f"A new note {word}", "Seen", nodes=[linked])
            await expect(
                page.locator("#item-page .recall-section[data-section=notes] tr.record-line")
            ).to_have_count(2, timeout=SLOW)
            assert await disclosure(page, "project_wide_notes") == {"open": False, "count": 3}
            # Closed, it says what is in it: the first line of each.
            await expect(
                page.locator("details[data-disclosure=project_wide_notes] .disclosure-line")
            ).to_have_count(3)
            await summary.click()
            await expect(record_row(page, ref_of_note(wide[0]))).to_be_visible()
            assert await disclosure(page, "project_wide_notes") == {"open": True, "count": 3}
            assert w.problems == [], w.problems


async def test_notes_are_tables_of_one_line_a_row_that_open_the_notes_page(
    server_url: str,
) -> None:
    word = unique_word()
    long_words = "that goes on and on and on " * 14
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Tables {word}")
        story = await made(client, "story", f"Long notes {word}", epic)
        decision = await make_decision(
            client,
            f"A decision with a long text {word}",
            text=f"A statement {long_words}{word}",
            reason=f"A reason {long_words}",
            nodes=[story],
        )
        note = await make_note(
            client,
            f"A note {long_words}{word}",
            f"A source {long_words}",
            nodes=[story],
        )
        old = await make_decision(client, f"Old decision {word}", nodes=[story])
        await ok(
            client,
            "supersede_note",
            replaces=[old["id"]],
            text=f"Newer decision {word}\n\nChanged.",
            why="Learned more.",
            decided_by="Marie",
            keep_links=True,
        )
        gone = await make_note(client, f"Gone note {word}", "Heard it", nodes=[story])
        await ok(client, "withdraw_note", note_ids=[gone["id"]], reason="A slip")
        decision_key, note_key = ref_of_note(decision), ref_of_note(note)
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await open_item(w, story, "roadmap")
            await recall_matches(client, page, node=story)
            recalled = await ok(client, "recall", node=story, limit=50)
            # Each section of recall is one table of rows, each row one note, in the server's
            # order, with the reason recall gives for it.
            for name in ("notes", "past_notes"):
                rows = await record_rows(page, "notes", section=name)
                assert [r["id"] for r in rows] == [i["note"]["id"] for i in recalled[name]["items"]]
                assert all(r["why"] for r in rows), (name, rows)
            assert [r["status"] for r in await record_rows(page, "notes", section="notes")] == [
                "unconfirmed",
                "unconfirmed",
                "unconfirmed",
            ]
            # What is not in force is in an "Earlier" section of its table (open: it holds two).
            assert await disclosure(page, "past_notes") == {"open": True, "count": 2}

            # A row's words wrap to at most three lines: what does not fit is behind "Show whole
            # text", which puts all of it in the cell, and all of it is also the cell's title.
            for key in (decision_key, note_key):
                row = record_row(page, key)
                metrics = await row.locator("td.cell-title .clamp").first.evaluate(
                    """e => ({cut: e.scrollHeight > e.clientHeight,
                        lines: getComputedStyle(e).webkitLineClamp,
                        title: e.title})"""
                )
                assert metrics["cut"] and metrics["lines"] == "3", (key, metrics)
                assert long_words.strip() in metrics["title"], (key, metrics)
                box = await row.bounding_box()
                assert box is not None and box["height"] < 130, (key, box)
                toggle = row.locator("td.cell-title .clamp-toggle")
                await expect(toggle).to_have_text("Show whole text")
                await toggle.click()
                await expect(row.locator("td.cell-title .clamp-whole")).to_contain_text(
                    long_words.strip()
                )
                await toggle.click()
                await expect(row.locator("td.cell-title .clamp-whole")).to_have_count(0)

            # A click on a row, away from its links and chips, opens the note's page, in the same
            # view.
            await record_row(page, decision_key).locator("td.cell-decided").click()
            await item_loaded(page, decision_key)
            assert (await current_address(page))[1]["from"] == "roadmap"
            assert (await crumbs(page))[0] == "Roadmap"
            await expect(part(page, "record").locator(".record-text")).to_contain_text(
                f"A statement {long_words.strip()}"
            )
            await expect(part(page, "why")).to_contain_text(f"A reason {long_words.strip()}")
            await page.go_back()
            await item_loaded(page, story)
            await record_row(page, note_key).locator("td.cell-recorded").click()
            await item_loaded(page, note_key)
            await expect(part(page, "why")).to_contain_text(f"A source {long_words.strip()}")
            await no_panel_or_drawer(page)
            assert w.problems == [], w.problems


# -- 8. the Notes list: one status each, and a filter that counts what the tools count ------------


async def test_the_notes_list_filters_by_one_status_and_its_filters_count_what_the_tools_count(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        world = await context_world(client, word, project)
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await notes_open(page, board, "#/lists?view=notes")

            async def server(**filters: object) -> list[str]:
                """What list_notes says, newest first (the list's order), as note references."""
                found = await whole(client, "list_notes", **filters)
                return [ref_of_note(one) for one in reversed(found)]

            everything = (await ok(client, "list_notes", limit=1))["total"]
            # One filter says where a note stands: All, In force (unconfirmed and confirmed
            # together), Unconfirmed, Confirmed, Replaced and Withdrawn. There is no switch between
            # kinds of note and no second filter for the confirmation.
            assert await page.locator("#ctx-notes-status option").all_text_contents() == [
                "All",
                "In force",
                "Unconfirmed",
                "Confirmed",
                "Replaced",
                "Withdrawn",
            ]
            await expect(page.locator("#ctx-notes-kind")).to_have_count(0)
            await expect(page.locator("#ctx-notes-confirmation")).to_have_count(0)
            assert await page.locator(
                "#ctx-notes-list table.notes-table thead th"
            ).all_text_contents() == [
                "Note",
                "What it says",
                "Status",
                "Applies to",
                "Decided by",
                "Recorded",
            ]
            # In force by default: the newest on top, the count says how many of how many.
            in_force = await server(status="in_force")
            assert await listed_refs(page) == in_force
            assert await counted(page) == (len(in_force), everything)

            # Each choice lists what the tool lists for the same status, every row says the status
            # it has, and the count follows.
            for value, wanted in (
                ("unconfirmed", "unconfirmed"),
                ("confirmed", "confirmed"),
                ("superseded", "superseded"),
                ("withdrawn", "withdrawn"),
                ("in_force", "in_force"),
            ):
                await status_filter(page, value)
                refs = await server(status=wanted)
                assert await listed_refs(page) == refs, value
                assert await counted(page) == (len(refs), everything), value
                if wanted != "in_force":
                    for ref in refs:
                        await expect(listed(page, ref)).to_have_attribute("data-status", wanted)
            await status_filter(page, "all")
            assert await listed_refs(page) == await server()
            assert await counted(page) == (everything, everything)
            await status_filter(page, "confirmed")
            assert set(await listed_refs(page)) == {
                f"note:{world['d1']}",
                f"note:{world['n1']}",
            }
            await status_filter(page, "in_force")

            # Words: every word typed must be in the note, its reason or who is named in it.
            find = page.locator("#ctx-notes-text")
            await find.fill(f"n3 {word}")
            assert await listed_refs(page) == [f"note:{world['n3']}"]
            await find.fill("zzzz nothing matches this")
            await expect(page.locator("#ctx-notes-list .ctx-none")).to_contain_text(
                "None match the filters"
            )
            assert await counted(page) == (0, everything)
            await find.fill("")
            assert await listed_refs(page) == in_force

            # Scoped to an epic: what concerns its work, what concerns nothing yet and what applies
            # throughout the project; nothing that is about another epic's work.
            await notes_open(page, board, context_list(world["E"]))
            inside = set(await listed_refs(page))
            in_scope = {
                ref_of_note(one)
                for one in await whole(
                    client, "list_notes", within_node=world["E"], status="in_force"
                )
            }
            assert in_scope <= inside
            assert {f"note:{world['dw']}", f"note:{world['n3']}"} <= inside
            assert not {f"note:{world['d9']}", f"note:{world['n9']}"} & inside
            await notes_open(page, board, context_list(world["E2"]))
            assert {f"note:{world['d9']}", f"note:{world['n9']}"} <= set(await listed_refs(page))
            assert f"note:{world['d1']}" not in await listed_refs(page)
            assert w.problems == [], w.problems


# -- 9. a viewer sees every note and has no control that changes one ----------------------------


async def test_a_viewer_sees_the_notes_and_has_no_control_to_change_one(server_url: str) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Viewed {word}")
        story = await made(client, "story", f"Viewed story {word}", epic)
        note = await make_note(client, f"Looked at note {word}", "Seen", nodes=[story])
        decision = await make_decision(client, f"Looked at decision {word}", nodes=[story])
        confirm_on_board("note", decision, MARIE, project=project)
        viewer = join_project(project, "viewer")
        note_key, decision_key = ref_of_note(note), ref_of_note(decision)
        before = await tally(client)

        async with browser_page(board, token=None, person=viewer) as w:
            page = w.page
            await notes_open(page, board, context_list(epic))
            await expect(page.locator("#view-only")).to_be_visible()
            assert await listed_refs(page) == [decision_key, note_key]
            await expect(page.locator("#add-note")).to_have_count(0)
            # Every chip says where the note stands and is only a chip: no menu to change it.
            for key in (note_key, decision_key):
                await expect(listed(page, key).locator("button")).to_have_count(0)
                await expect(listed(page, key).locator(".chip[data-menu]")).to_have_count(0)
            await expect(listed(page, decision_key).locator(".chip.note-status")).to_have_text(
                "Confirmed"
            )
            await expect(listed(page, note_key).locator(".chip.note-status")).to_have_text(
                "Unconfirmed"
            )

            # The note's own page, and the story's: the notes, and nothing to add, replace,
            # withdraw, link or confirm.
            for key in (note_key, decision_key):
                await open_item(w, key, "lists")
                await expect(status_chip(page)).to_have_count(1)
                await expect(
                    page.locator(
                        "#item-page .card-summary button, #item-page button.add-note, "
                        "#item-page button.supersede, #item-page button.withdraw, "
                        "#item-page button.change-concerns"
                    )
                ).to_have_count(0)
                await expect(page.locator("div.vmenu")).to_have_count(0)
            await open_item(w, story, "lists")
            await recall_matches(client, page, node=story)
            await expect(page.locator("#item-page button.add-note")).to_have_count(0)
            await expect(record_row(page, decision_key)).to_have_attribute(
                "data-status", "confirmed"
            )
            assert w.problems == [], w.problems

        # The server refuses the same changes, whatever a page offers.
        refusal = viewer.call(
            "POST",
            f"/board/api/projects/{project.id}/tool/add_note",
            {"text": f"A viewer's note {word}", "why": "Wanted to"},
        )
        assert refusal.status in (400, 403), (refusal.status, refusal.body)
        assert await tally(client) == before


# -- 10. a note about an epic or a story comes down to what is under it and says where from --------


async def test_a_note_that_comes_down_from_an_epic_or_story_says_where_from(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Checkout {word}")
        story = await made(client, "story", f"Pay {word}", epic)
        task = await made(client, "task", f"Take the card {word}", story)
        down_epic = await make_decision(client, f"Epic wide decision {word}", nodes=[epic])
        down_story = await make_note(client, f"Story wide note {word}", "Seen", nodes=[story])
        own = await make_note(client, f"Task note {word}", "Seen", nodes=[task])
        keys = {n["id"]: ref_of_note(n) for n in (down_epic, down_story, own)}
        # Exact links only in the list tools; recall brings down what is above.
        assert [n["id"] for n in (await ok(client, "list_notes", node=task))["items"]] == [
            own["id"]
        ]

        async with browser_page(board, token=project.key) as w:
            page = w.page
            await open_item(w, task, "board")
            await recall_matches(client, page, node=task)
            rows = {r["id"]: r for r in await record_rows(page, "notes", section="notes")}
            assert set(rows) == {down_epic["id"], down_story["id"], own["id"]}
            assert rows[down_epic["id"]]["from_ref"] == epic
            assert rows[down_story["id"]]["from_ref"] == story
            assert rows[own["id"]]["from_ref"] is None
            assert epic in rows[down_epic["id"]]["why"] and story in rows[down_story["id"]]["why"]
            await expect(
                record_row(page, keys[down_epic["id"]]).locator(".record-from")
            ).to_contain_text(f"From {code(epic)} Checkout {word}")
            await expect(
                record_row(page, keys[down_story["id"]]).locator(".record-from")
            ).to_contain_text(f"From {code(story)}")
            await expect(record_row(page, keys[own["id"]]).locator(".record-from")).to_have_count(0)
            # The words that say where it comes from lead to that item's page.
            await record_row(page, keys[down_epic["id"]]).locator(".record-from a").click()
            await item_loaded(page, epic)

            # The story's page: the epic's note comes down to it too; the epic's own page has only
            # what is linked to it.
            await open_item(w, story, "board")
            await recall_matches(client, page, node=story)
            rows = {r["id"]: r for r in await record_rows(page, "notes", section="notes")}
            assert rows[down_epic["id"]]["from_ref"] == epic
            assert rows[down_story["id"]]["from_ref"] is None
            assert own["id"] not in rows
            await open_item(w, epic, "board")
            await recall_matches(client, page, node=epic)
            rows = {r["id"]: r for r in await record_rows(page, "notes", section="notes")}
            assert set(rows) == {down_epic["id"]} and rows[down_epic["id"]]["from_ref"] is None

            # In the Graph's Around the same notes are drawn, and the ones that came down say
            # through what.
            await go(page, board, f"#/graph?tab=around&focus={task}")
            await expect(page.locator(f".graph-around[data-focus='{task}']")).to_be_visible(
                timeout=SLOW
            )
            recalled = await ok(client, "recall", node=task)
            drawn = await page.locator(
                "section[data-group=notes] button.graph-node.n-note"
            ).evaluate_all("els => els.map(e => e.dataset.ref)")
            assert drawn == [ref_of_note(i["note"]) for i in recalled["notes"]["items"]]
            via = page.locator("section[data-group=notes] button.graph-node.is-via")
            await expect(via).to_have_count(2)
            await expect(
                page.locator(f"button.graph-node[data-ref='{keys[down_epic['id']]}'] .n-via")
            ).to_have_text(f"via {code(epic)} Checkout {word}")
            await expect(
                page.locator(f"button.graph-node[data-ref='{keys[down_story['id']]}'] .n-via")
            ).to_have_text(f"via {code(story)} Pay {word}")
            await expect(
                page.locator(f"button.graph-node[data-ref='{keys[own['id']]}']")
            ).not_to_have_class(re.compile(r"\bis-via\b"))
            assert w.problems == [], w.problems


# -- 11. Around draws a few notes of a group and opens the rest, in recall's order ----------------


async def test_around_draws_three_notes_and_show_more_opens_the_rest_in_recalls_order(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Many notes {word}")
        story = await made(client, "story", f"Holds seven notes {word}", epic)
        for number_ in range(1, 8):
            await make_note(client, f"Note {number_} {word}", "Seen", nodes=[story])
        recalled = await ok(client, "recall", node=story)
        every = [ref_of_note(i["note"]) for i in recalled["notes"]["items"]]
        assert len(every) == 7

        async with browser_page(board, token=project.key) as w:
            page = w.page
            await go(page, board, f"#/graph?tab=around&focus={story}")
            await expect(page.locator(f".graph-around[data-focus='{story}']")).to_be_visible(
                timeout=SLOW
            )
            drawn = page.locator("section[data-group=notes] button.graph-node.n-note")

            async def refs() -> list[str]:
                found: list[str] = await drawn.evaluate_all("els => els.map(e => e.dataset.ref)")
                return found

            # Three notes and a button for the other four, the three being recall's first three.
            await expect(drawn).to_have_count(3, timeout=SLOW)
            assert await refs() == every[:3]
            more = page.locator("section[data-group=notes] button.graph-more")
            await expect(more).to_have_text("Show 4 more")
            await expect(more).to_have_attribute("aria-expanded", "false")
            # Opening it lists every note in recall's order, and the same button closes it again.
            await more.click()
            await expect(drawn).to_have_count(7)
            assert await refs() == every
            await expect(more).to_have_text("Show fewer")
            await expect(more).to_have_attribute("aria-expanded", "true")
            await more.click()
            await expect(drawn).to_have_count(3)
            assert await refs() == every[:3]
            # Escape closes an open group too.
            await more.click()
            await expect(drawn).to_have_count(7)
            await page.keyboard.press("Escape")
            await expect(drawn).to_have_count(3)
            assert await refs() == every[:3]
            assert w.problems == [], w.problems
