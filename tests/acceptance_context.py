"""Chapter 10 of the acceptance scenario: what the project knows, on the board (M4A, M12, M16, M20
and M30). A note is one kind of record: it is added from the page of a story or from Lists >
Notes with its why and, when a person decided it, who and when (two optional fields), linked to
work, confirmed by a named person through its one status chip, replaced and withdrawn, and a note
that applies throughout its project is in that project's recall and briefings and no other's. Its
status is one of Unconfirmed, Confirmed, Replaced and Withdrawn, said in words and an icon and
never by colour alone, and the Notes list has one Status filter. Every note has a full page of its
own (nothing opens at the side). An acceptance criterion is written in Lists, is placed nowhere
until a person puts it on a story or task, and is moved in one step; the sentence above the list
says what the coverage report says. Every figure is compared with what a real MCP client reads
(`get_note`, `list_notes`, `recall`, the histories), and every refusal is compared word for word
with what the same attempt gets through MCP. Each part works in a project of its own, so that
nothing but what it made is on the page."""

import re

from playwright.async_api import Error as BrowserError
from playwright.async_api import expect

from tests.acceptance_scenario import URL, Failed, chapter, clip, proof
from tests.board_context import (
    ADA,
    board_refusal,
    choose_in_menu,
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
    detail,
    item_loaded,
    item_page,
    made,
    open_item,
    part,
    plain,
    record_rows,
)
from tests.board_m14 import spoken
from tests.helpers import code_of, confirm_on_board, make_note, ok, refused, unique_word
from tests.test_board_context_integration import (
    MARIE,
    chosen_picker,
    context_list,
    listed,
    listed_refs,
    notes_open,
    ref_of_note,
    status_chip,
    status_filter,
    strip,
)
from tests.test_board_requirements_integration import (
    LIST,
    choose_place,
    group,
    history,
    move_by_drag,
    page_says,
    rule_in,
    rule_row,
    summary_says,
)
from tests.test_board_requirements_integration import graph_open as criteria_open


async def context() -> None:
    chapter("What the project knows: notes, added, confirmed, replaced and withdrawn")
    try:
        await adding()
        await confirming()
        await ending()
        await filtering()
        await project_wide()
        await criteria()
    except BrowserError as error:
        raise Failed(
            f"M30: the board did not do what a proof needs: {clip(str(error), 220)}"
        ) from error


# -- a note, added on the board, is the same record everywhere -----------------------------------


async def adding() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Context {word}")
        story = await made(client, "story", f"Concerned story {word}", epic)
        rule = (await ok(client, "create_rule", title=f"Concerned {word}", node=story))["id"]
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            # From the page of the story: the story is already chosen, and who decided it and
            # when are two optional fields, empty here.
            await open_item(w, story, "lists")
            await part(page, "context").locator("button.add-note").click()
            box = dialog(page)
            await expect(box.get_by_label("Decided by (optional)")).to_have_value("")
            await box.locator("#add-note-text").fill(f"Note about {word}")
            await box.locator("#add-note-why").fill(f"The call with Marie {word}")
            await chosen_picker(page, f"criterion {rule}")
            await box.get_by_role("button", name="Add the note").click()
            await expect(box).to_have_count(0, timeout=SLOW)
            # From the Notes list, with a person who decided it and the day.
            await notes_open(page, board, context_list(epic))
            await page.locator("#add-note").click()
            box = dialog(page)
            await box.locator("#add-note-text").fill(f"Decided {word}\n\nWhat was decided")
            await box.locator("#add-note-why").fill(f"Because of {word}")
            await box.locator("#add-note-by").fill(MARIE)
            await box.locator("#add-note-on").fill("2026-11-02")
            await chosen_picker(page, code_of(story))
            await box.get_by_role("button", name="Add the note").click()
            await expect(box).to_have_count(0, timeout=SLOW)

            notes = (await ok(client, "list_notes", node=story))["items"]
            assert len(notes) == 2, notes
            plain_note = next(n for n in notes if not n["decided_by"])
            decided = next(n for n in notes if n["decided_by"])
            first = await ok(client, "get_note", note_id=plain_note["id"])
            second = await ok(client, "get_note", note_id=decided["id"])
            fields_hold = (
                (first["text"], first["why"], first["recorded_by"], first["status"])
                == (f"Note about {word}", f"The call with Marie {word}", ADA, "unconfirmed")
                and (first["nodes"], first["rule_ids"]) == ([story], [rule])
                and (second["decided_by"], second["decided_on"], second["status"])
                == (MARIE, "2026-11-02", "unconfirmed")
            )
            # The list says each note's status in one chip, and who decided it in a column of
            # its own that a note nobody decided leaves empty.
            for record in (first, second):
                row = listed(page, ref_of_note(record))
                await expect(row).to_have_attribute("data-status", "unconfirmed", timeout=SLOW)
                await expect(row.locator(".chip.note-status")).to_have_text("Unconfirmed")
            await expect(listed(page, ref_of_note(first)).locator("td.cell-decided")).to_have_text(
                ""
            )
            await expect(listed(page, ref_of_note(second)).locator("td.cell-decided")).to_have_text(
                f"{MARIE}, 2026-11-02"
            )
            # The page of a note someone decided says so in a quiet line, and the history is the
            # server's.
            await open_item(w, ref_of_note(second), "lists")
            await expect(page.locator("#item-title")).to_contain_text(
                f"N-{second['id']} Decided {word}"
            )
            decided_by = (await strip(page)).get("Decided by")
            history = await history_actions(client, "note_history", "note_id", second["id"])
            # Recall, section by section, as the story's page draws it: the note someone decided
            # comes first.
            await open_item(w, story, "lists")
            await recall_matches(client, page, node=story)
            in_recall = [
                i["note"]["id"] for i in (await ok(client, "recall", node=story))["notes"]["items"]
            ]
            shown = [r["id"] for r in await record_rows(page, "notes")]
            assert w.problems == [], w.problems
    proof(
        "M30",
        "a note added on the board, from the page of a story and from the Notes list, with its "
        "why and, when a person decided it, who and when in two optional fields, is the same "
        "record everywhere: the fields of get_note, one Unconfirmed chip in the list, who decided "
        "it and when on its page, and exact recall of the story with the decided note "
        "first",
        fields_hold
        and decided_by == f"{MARIE} on 2026-11-02"
        and history == ["note_recorded", "note_linked"]
        and in_recall == shown == [decided["id"], plain_note["id"]],
        f"notes {first['id']} and {second['id']}, added by {ADA}",
    )


# -- confirming: who and when, and what un-confirming needs ---------------------------------------


async def confirming() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Confirming {word}")
        story = await made(client, "story", f"Reviewed story {word}", epic)
        note = await make_note(client, f"Reviewed note {word}", "Seen on the call", nodes=[story])
        key = ref_of_note(note)
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await open_item(w, key, "lists")
            chip = status_chip(page)
            await expect(chip).to_have_attribute("data-status", "unconfirmed", timeout=SLOW)
            # The chip's menu: confirm, replace, withdraw, change what it concerns.
            await chip.click()
            offered = await page.locator(
                "div.vmenu[data-menu='note-status'] .vmenu-option"
            ).evaluate_all("els => els.map(e => e.dataset.value)")
            await page.keyboard.press("Escape")
            # A blank person is refused in the words of the tool, and nothing is written.
            asks = {"note_id": note["id"]}
            nobody = board_refusal(
                project, "set_confirmation", confirmation="confirmed", person="", **asks
            )
            await choose_in_menu(page, chip, "confirm")
            box = dialog(page)
            await box.locator("#confirm-person").fill("")
            await box.locator("#confirm-quote").fill(f"I read it {word}")
            await box.get_by_role("button", name="Confirm", exact=True).click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            refusal_words = spoken(await refusal_in(page).inner_text())
            plain(refusal_words, spoken(nobody))
            typed_kept = await box.locator("#confirm-quote").input_value() == f"I read it {word}"
            still = (await ok(client, "get_note", note_id=note["id"]))["status"]
            await box.locator("#confirm-person").fill(MARIE)
            await box.get_by_role("button", name="Confirm", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            await expect(detail(page, key)).to_have_attribute(
                "data-status", "confirmed", timeout=SLOW
            )
            reviewed = await ok(client, "get_note", note_id=note["id"])
            await expect(chip).to_have_text("Confirmed")
            await expect(part(page, "confirmation")).to_contain_text(f"Confirmed by {MARIE}")
            # Un-confirming needs the person and their words, and both are given.
            silent = board_refusal(
                project,
                "set_confirmation",
                confirmation="unconfirmed",
                person=MARIE,
                quote="",
                **asks,
            )
            await choose_in_menu(page, chip, "unconfirm")
            box = dialog(page)
            await box.locator("#confirm-person").fill(MARIE)
            await box.get_by_role("button", name="Un-confirm", exact=True).click()
            await expect(refusal_in(page)).to_contain_text("needs what the person said")
            plain(spoken(await refusal_in(page).inner_text()), spoken(silent))
            await box.locator("#confirm-quote").fill(f"On second thought, no {word}")
            await box.get_by_role("button", name="Un-confirm", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            await expect(detail(page, key)).to_have_attribute(
                "data-status", "unconfirmed", timeout=SLOW
            )
            again = await ok(client, "get_note", note_id=note["id"])
            events = await history_actions(client, "note_history", "note_id", note["id"])
            assert w.problems == [], w.problems
    proof(
        "M30",
        "a note has one status chip: its menu offers Confirm, Replace, Withdraw and a change of "
        "what it concerns; a confirmation with no person is refused in the tool's words and "
        "writes nothing, with what was typed kept; a person confirms it and the page says who "
        "and when; un-confirming needs the person and their words, and the earlier confirmation "
        "stays in the history",
        offered == ["confirm", "replace", "withdraw", "concerns"]
        and typed_kept
        and still == "unconfirmed"
        and (reviewed["status"], reviewed["confirmed_by"]) == ("confirmed", MARIE)
        and (again["status"], again["confirmed_by"], again["confirmed_at"])
        == ("unconfirmed", None, None)
        and events[-2:] == ["note_confirmed", "note_unconfirmed"],
        f"note {note['id']}",
    )


# -- replacing and withdrawing: a note that ended keeps the confirmation it had -------------------


async def ending() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Ending {word}")
        story = await made(client, "story", f"Ended story {word}", epic)
        old = await make_note(client, f"Old note {word}", "Heard it", nodes=[story])
        confirm_on_board("note", old, MARIE, project=project)
        wrong = await make_note(client, f"Wrong note {word}", "Heard it", nodes=[story])
        old_key, wrong_key = ref_of_note(old), ref_of_note(wrong)
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await notes_open(page, board, context_list(epic))
            before = await tally(client)
            await choose_in_menu(
                page, listed(page, old_key).locator(".chip.note-status"), "replace"
            )
            box = dialog(page)
            new_text = f"New note {word}\n\nWhat is true now"
            await box.locator("#supersede-text").fill(new_text)
            attempt = {"replaces": [old["id"]], "text": new_text, "keep_links": True}
            no_why = await refused(client, "supersede_note", why="", **attempt)
            await box.get_by_role("button", name="Replace with this note").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(spoken(await refusal_in(page).inner_text()), spoken(no_why))
            nothing_written = await tally(client) == before
            await box.locator("#supersede-why").fill("We learned more")
            await box.locator("#supersede-said-by").fill(MARIE)
            await box.locator("#supersede-quote").fill(f"Yes, change it {word}")
            await box.get_by_role("button", name="Replace with this note").click()
            await expect(box).to_have_count(0, timeout=SLOW)

            ended = await ok(client, "get_note", note_id=old["id"])
            new = await ok(client, "get_note", note_id=ended["superseded_by_id"])
            # The replaced note leaves the default list (notes in force), shows under Replaced
            # with a plain chip that has no menu, and keeps the confirmation it had.
            await expect(listed(page, old_key)).to_have_count(0)
            await status_filter(page, "superseded")
            gone = listed(page, old_key)
            await expect(gone.locator(".chip.note-status")).to_have_text("Replaced", timeout=SLOW)
            no_menu = await gone.locator("button.chip").count() == 0
            await gone.locator("a.record-title").click()
            await item_loaded(page, old_key)
            await expect(part(page, "confirmation")).to_contain_text(f"It was confirmed by {MARIE}")
            ended_page = await page.locator("#record-ended").is_visible()
            [replaced_by] = await record_rows(page, "replaces")

            # Withdrawing needs a reason, and nothing more for a note nobody confirmed.
            await open_item(w, wrong_key, "lists")
            await page.locator("#item-page button.withdraw").click()
            box = dialog(page)
            blank = await refused(client, "withdraw_note", note_ids=[wrong["id"]], reason="")
            await box.get_by_role("button", name="Withdraw", exact=True).click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            plain(spoken(await refusal_in(page).inner_text()), spoken(blank))
            await box.locator("#withdraw-reason").fill("It is wrong")
            await box.get_by_role("button", name="Withdraw", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            withdrawn = await ok(client, "get_note", note_id=wrong["id"])
            await expect(status_chip(page)).to_have_text("Withdrawn", timeout=SLOW)
            assert w.problems == [], w.problems
    proof(
        "M30",
        "replacing a confirmed note asks for the reason, and a blank one is refused in the "
        "tool's words with nothing written; the replaced note is Replaced, keeps the confirmation "
        "it had in its words, has a plain chip with no menu and leads to what replaced it; "
        "withdrawing needs a reason and leaves the note Withdrawn",
        no_why != ""
        and nothing_written
        and (ended["status"], ended["confirmed_by"]) == ("superseded", MARIE)
        and (new["status"], new["supersedes_ids"]) == ("unconfirmed", [old["id"]])
        and no_menu
        and ended_page
        and (replaced_by["relation"], replaced_by["id"]) == ("Replaced by", new["id"])
        and (withdrawn["status"], withdrawn["status_reason"]) == ("withdrawn", "It is wrong"),
        f"note {old['id']} replaced by {new['id']}, note {wrong['id']} withdrawn",
    )


# -- the Notes list has one Status filter ---------------------------------------------------------


async def filtering() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Filtering {word}")
        story = await made(client, "story", f"Filtered story {word}", epic)
        plain_note = await make_note(client, f"Plain {word}", "A", nodes=[story])
        confirmed = await make_note(client, f"Confirmed {word}", "B", nodes=[story])
        confirm_on_board("note", confirmed, MARIE, project=project)
        old = await make_note(client, f"Old {word}", "C", nodes=[story])
        await ok(client, "supersede_note", replaces=[old["id"]], text=f"Newer {word}", why="D")
        slip = await make_note(client, f"Slip {word}", "E", nodes=[story])
        await ok(client, "withdraw_note", note_ids=[slip["id"]], reason="A slip")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await notes_open(page, board, context_list(epic))
            options = await page.locator("#ctx-notes-status option").all_text_contents()
            equal = []
            for value, arguments in (
                ("in_force", {"status": "in_force"}),
                ("unconfirmed", {"status": "unconfirmed"}),
                ("confirmed", {"status": "confirmed"}),
                ("superseded", {"status": "superseded"}),
                ("withdrawn", {"status": "withdrawn"}),
                ("all", {}),
            ):
                await status_filter(page, value)
                wanted = [
                    ref_of_note(n) for n in reversed(await whole(client, "list_notes", **arguments))
                ]
                equal.append(await listed_refs(page) == wanted)
            assert w.problems == [], w.problems
    proof(
        "M30",
        "the Notes list has one Status filter with All, In force, Unconfirmed, Confirmed, "
        "Replaced and Withdrawn (In force until a person chooses another), and each choice lists "
        "exactly what list_notes lists for the same status",
        options == ["All", "In force", "Unconfirmed", "Confirmed", "Replaced", "Withdrawn"]
        and all(equal)
        and plain_note["status"] == "unconfirmed",
        f"{len(equal)} choices compared",
    )


# -- a project-wide note: in its project's recall and briefings, and never another's --------------


async def project_wide() -> None:
    word = unique_word()
    wide_a, wide_b = f"Wide A {word}", f"Wide B {word}"
    async with (
        fresh_project(URL) as (a, board_a, first),
        fresh_project(URL) as (b, board_b, second),
    ):
        epic_a = await made(a, "epic", f"Epic {word}")
        story_a = await made(a, "story", f"Story {word}", epic_a)
        await made(a, "task", f"Task {word}", story_a)
        epic_b = await made(b, "epic", f"Epic {word}")
        story_b = await made(b, "story", f"Story {word}", epic_b)
        await made(b, "task", f"Task {word}", story_b)
        await make_note(b, f"{wide_b}\n\nOnly in the other project", project_wide=True)
        async with browser_page(board_a, token=first.key) as w:
            page = w.page
            await notes_open(page, board_a, context_list(epic_a))
            await page.locator("#add-note").click()
            box = dialog(page)
            await box.locator("#add-note-text").fill(f"{wide_a}\n\nEverything is metric {word}")
            await box.locator("#add-note-why").fill(f"One system only {word}")
            await box.locator("#add-note-project-wide").check()
            await box.get_by_role("button", name="Add the note").click()
            await expect(box).to_have_count(0, timeout=SLOW)
            mine = (await ok(a, "list_notes", project_wide=True))["items"]
            key = ref_of_note(mine[0])
            await expect(listed(page, key)).to_have_attribute(
                "data-project-wide", "true", timeout=SLOW
            )
            await expect(listed(page, key).locator("td.cell-applies")).to_contain_text(
                "The whole project"
            )
            recalled = await ok(a, "recall", node=story_a)
            in_recall = [i["note"]["headline"] for i in recalled["project_wide_notes"]["items"]]
            brief = await ok(a, "next_work", within_node=epic_a, peek=True)
            in_brief = [n["text"].split("\n")[0] for n in brief["context"]["project_wide_notes"]]
            await open_item(w, story_a, "lists")
            await recall_matches(a, page, node=story_a)
            assert w.problems == [], w.problems
        async with browser_page(board_b, token=second.key, keep_bodies=True) as w:
            page = w.page
            await open_item(w, story_b, "lists")
            await expect(part(page, "context")).to_contain_text(wide_b)
            shown = await page.locator("body").inner_text()
            elsewhere = wide_a not in shown and all(wide_a not in body for _, body in w.bodies)
            others = [
                i["note"]["headline"]
                for i in (await ok(b, "recall", node=story_b))["project_wide_notes"]["items"]
            ]
    proof(
        "M30",
        "a note that applies throughout its project is in the recall and the briefing of any "
        "item of that project, flagged Unconfirmed, and in no other project's recall, briefing, "
        "list or page",
        in_recall == [wide_a]
        and in_brief == [wide_a]
        and elsewhere
        and others == [wide_b]
        and re.fullmatch(r"note:\d+", key) is not None,
        f"{key} in one project, none in the other",
    )


# -- an acceptance criterion: written, placed on a story, moved, refused when blank ---------------


async def criteria() -> None:
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", "Acceptance criteria")
        first = await made(client, "story", "First story", epic)
        second = await made(client, "story", "Second story", epic)
        await made(client, "story", "Third story", epic)
        await rule_in(client, "Anchor", second)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await criteria_open(w, LIST)
            start = await summary_says(page, client)
            # Written in the browser, a criterion is placed nowhere: it opens on a page of its own
            # that says so.
            await page.locator("#new-requirement").click()
            box = dialog(page)
            await box.locator("#requirement-title").fill("Created in the browser")
            await box.get_by_role("button", name="Add the acceptance criterion").click()
            await expect(item_page(page)).to_have_attribute("data-kind", "rule", timeout=SLOW)
            ref = await item_page(page).get_attribute("data-ref") or ""
            rule = int(ref.split(":")[1])
            loose = await page_says(page, client, rule)
            # Back on the list it is the first of those placed nowhere, and the sentence above the
            # list says what the coverage report says.
            await page.go_back()
            newest = page.locator("#unplaced-group li.requirement").first
            await expect(newest).to_contain_text("Created in the browser", timeout=SLOW)
            after_new = await summary_says(page, client)
            # Placed on the first story from its own page, and moved to the second, each in one
            # step.
            await (
                newest.locator("a, button").filter(has_text="Created in the browser").first.click()
            )
            await item_loaded(page, ref)
            await page.locator("#item-page button.place-requirement").click()
            box = dialog(page)
            await choose_place(box, first)
            await box.get_by_role("button", name="Put it there").click()
            await expect(box).to_have_count(0, timeout=SLOW)
            placed = await page_says(page, client, rule)
            await page.locator("#item-page button.place-requirement").click()
            box = dialog(page)
            await choose_place(box, second)
            await box.get_by_role("button", name="Move it there").click()
            await expect(box).to_have_count(0, timeout=SLOW)
            moved_once = await page_says(page, client, rule)
            # The list follows it, and a drag onto a story that has nothing moves it again.
            await criteria_open(w, LIST)
            await expect(group(page, second).locator(f"li[data-id='{rule}']")).to_be_visible(
                timeout=SLOW
            )
            after_move = await summary_says(page, client)
            await page.locator("#stories-without-requirements summary").click()
            bare = page.locator(f"#stories-without-requirements li[data-story='{first}']")
            await expect(bare).to_be_visible()
            await move_by_drag(page, rule_row(page, rule), bare)
            await expect(group(page, first).locator(f"li[data-id='{rule}']")).to_be_visible(
                timeout=SLOW
            )
            after_drag = await summary_says(page, client)
            moved = await ok(client, "get_rule", rule_id=rule)
            # A blank title is refused as an agent's is, and writes nothing; so is a move to the
            # story it is already on.
            total = (await ok(client, "list_rules", limit=1))["total"]
            blank = await refused(client, "create_rule", title="", body="", status="open")
            await page.locator("#new-requirement").click()
            box = dialog(page)
            await box.locator("#requirement-body").fill("A body with no title")
            await box.get_by_role("button", name="Add the acceptance criterion").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            blank_words = await refusal_in(page).inner_text()
            kept = await box.locator("#requirement-body").input_value()
            await box.get_by_role("button", name="Cancel").click()
            same = await refused(
                client, "place_rule", rule_id=rule, expected_version=moved["version"], node=first
            )
            await (
                rule_row(page, rule)
                .get_by_role("button", name="Move to another story or task…")
                .click()
            )
            box = dialog(page)
            await choose_place(box, first)
            await box.get_by_role("button", name="Move it there").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            same_words = await refusal_in(page).inner_text()
            await box.get_by_role("button", name="Cancel").click()
            assert w.problems == [], w.problems
        events = await history(client, rule)
        plain(blank_words, blank)
        plain(same_words, same)
        proof(
            "M30",
            "an acceptance criterion written in Lists is placed nowhere, opens on a page of its "
            "own that says so and is first among those placed nowhere when the list is back, is "
            "put on a story from a dialog, moved to another and dragged to a story that had "
            "none, each in one step and each time the sentence above the list says what "
            "coverage_report says; its history names the person",
            loose["node"] is None
            and after_new == (start[0] + 1, start[1])
            and (placed["node"], placed["version"]) == (first, 2)
            and (moved_once["node"], moved_once["version"]) == (second, 3)
            and after_move == start
            and after_drag == (start[0], start[1] - 1)
            and (moved["node"], moved["version"]) == (first, 4)
            and [(e["action"], e["actor"]) for e in events]
            == [
                ("rule_created", ADA),
                ("rule_placed", ADA),
                ("rule_moved", ADA),
                ("rule_moved", ADA),
            ],
            f"placed nowhere {start[0]} -> {after_new[0]} -> {after_move[0]}, "
            f"stories without {start[1]} -> {after_drag[1]}",
        )
        proof(
            "M30",
            "a blank acceptance criterion title and a move to the story it is already on are "
            "refused in the words of the tools that own them, what was typed stays, and nothing "
            "is written",
            kept == "A body with no title"
            and (await ok(client, "list_rules", limit=1))["total"] == total,
            f'"{clip(same_words, 70)}"',
        )
