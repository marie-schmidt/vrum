"""The browser proofs of the M51 chapter (tests/acceptance_revising.py), as the pages are since M52:
the review page of a plan is the review conversation. A person collects comments on the lines of a
plan and asks for changes; the plan leaves the list and waits for its agent; after the agent's
revision the page shows what changed since the revision before, with the person's earlier comments
as threads under the lines they were about; a decision names the revision the person read; and every
revision can be read as it was submitted. Each part works in a project of its own, with a headless
browser signed in as the owner or a viewer of it. The proofs of what the tools say are in
tests/acceptance_revising.py, which this file builds on."""

from typing import Any

from playwright.async_api import Locator, Page, expect

from tests.acceptance_review import (
    ITEM_LINES,
    lines_of,
    open_review,
    plan_card,
    review_page,
    the_diff_is_before_the_decision,
    whole_plan,
)
from tests.acceptance_revising import (
    an_epic,
    drafted,
    person,
    read,
    revision,
    shown_changes,
)
from tests.acceptance_scenario import clip, proof
from tests.board_helpers import SLOW, Watched, browser_page, go, hold_back_news
from tests.helpers import connect, ok
from tests.world import Person, Project, join_project, make_project, register

NARROW = {"width": 390, "height": 900}
CHANGED_LINES = ".diff-line[data-sign='~'][data-ref]:not([data-part]):visible"
# An item taken out, not a part of a changed item that was taken out.
REMOVED_LINES = ".diff-line[data-sign='-'][data-ref]:not([data-part]):visible"
THE_PAGE = "body"
BUTTONS_THAT_DECIDE = "button.verdict"
WRITING = "button.diff-comment, form.comment-form, .diff-comment-pending, button.decision-comment"
NEW_TITLE = "Studded tyres for the river path and the hill route"
NEW_BODY = "At the depot.\nFour hours each, from 8 until noon."

# The part lines of a diff as (the item, the sign, the part, the words): a line that names no item
# of its own belongs to the item line above it.
PART_LINES = """els => els.map(e => {
    let ref = e.dataset.ref;
    if (!ref) {
        let p = e.previousElementSibling;
        while (p && !(p.matches('.diff-line') && p.dataset.ref && !p.dataset.part)) {
            p = p.previousElementSibling;
        }
        ref = p ? p.dataset.ref : null;
    }
    return [ref, e.dataset.sign, e.dataset.part, e.textContent];
})"""
# Each thread of an earlier comment with the item line it sits under.
THREADS = """els => els.map(e => {
    let p = e.previousElementSibling;
    while (p && !(p.matches('.diff-line') && p.dataset.ref && !p.dataset.part)) {
        p = p.previousElementSibling;
    }
    if (!p) { p = e.closest('.diff-line[data-ref]'); }
    return [e.dataset.feedback, p ? p.dataset.ref : null, p ? p.dataset.sign : null, e.textContent];
})"""


def spoken(text: str) -> str:
    """What a person reads: any run of spaces and line breaks is one space."""
    return " ".join(text.split())


LAST_FAILED: list[int] = []


def holds(*clauses: object) -> bool:
    """Every clause holds. Which ones did not (by position) is kept for `failing`, so that a proof
    that stops says what it could not find on the page."""
    LAST_FAILED[:] = [number for number, clause in enumerate(clauses, 1) if not clause]
    return not LAST_FAILED


def failing() -> str:
    return f"; clauses that did not hold: {LAST_FAILED}" if LAST_FAILED else ""


async def the_needs_page(w: Watched) -> None:
    await go(w.page, w.board, "#/needs")


async def overflows(page: Page) -> bool:
    return bool(
        await page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
    )


async def pending_of(scope: Locator) -> list[tuple[str, str, str]]:
    """The comments waiting to be sent, as (item, part, words), in the order they are listed."""
    found: list[list[str]] = await scope.locator(".diff-comment-pending").evaluate_all(
        "els => els.map(e => [e.dataset.ref, e.dataset.field || '', e.textContent])"
    )
    return [(ref, part, text) for ref, part, text in found]


async def comment_on(scope: Locator, ref: str, label: str | None, text: str) -> None:
    """Press the comment button on an item's line, fill the composer under it and add the
    comment."""
    pending = scope.locator(f".diff-comment-pending[data-ref='{ref}']")
    before = await pending.count()
    await scope.locator(f"button.diff-comment[data-ref='{ref}']").click()
    form = scope.locator(f"form.comment-form[data-ref='{ref}']")
    await expect(form).to_be_visible(timeout=SLOW)
    if label is not None:
        await form.locator("select.comment-field").select_option(label=label)
    await form.locator("textarea.comment-text").fill(text)
    await form.locator("button[type=submit]").click()
    await expect(pending).to_have_count(before + 1, timeout=SLOW)


def second_revision(
    epic: str, refs: dict[str, str], measure_size: str = "s"
) -> list[dict[str, Any]]:
    """What the agent sends as revision 2 (and, with `measure_size` changed, as revision 3): the
    title and the details that were asked about, the covers task taken out and a measuring task
    added (continuing itself once it has a reference)."""
    items = revision(
        epic, refs, leave_out=("order",), tyres={"title": NEW_TITLE}, fit={"body": NEW_BODY}
    )
    items.append(
        {
            "key": "measure",
            "kind": "task",
            "title": "Measure the stations",
            "parent": "covers",
            "size": measure_size,
            **({"ref": refs["measure"]} if "measure" in refs else {}),
        }
    )
    return items


def part_words(parts: list[list[str]], ref: str, sign: str, name: str) -> str:
    return " ".join(spoken(t) for r, s, p, t in parts if (r, s, p) == (ref, sign, name))


async def told(page: Page, fragment: str) -> None:
    """The page says these words somewhere a person can read them (beside the bar or in a
    dialog)."""
    await expect(page.locator(THE_PAGE)).to_contain_text(fragment, timeout=SLOW)


async def revision_fact(scope: Locator) -> str:
    return spoken(await scope.locator("[data-fact='revision']").inner_text())


async def the_conversation() -> None:
    """From a drafted plan to its approval, all on its review page: comments pending and then
    sent, the plan waiting for its agent, the revision read as what changed with the earlier
    comments as threads, the whole plan, the comments that were lost, Viewed reset, every
    revision read as it was, the decision, and the sentences of the history."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        first = await read(agent, number)
    change = f"plan:{number}"
    said_1 = "Say which tyres."
    said_2 = "How many hours is a bike off the street? One morning is not enough for the rota."
    said_3 = "This belongs to another project."
    summary = "Name the tyres, give the hours and drop the covers task."
    old_words = {i["ref"]: i for i in first["tree"]}
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await open_review(w, change)
        scope = review_page(page)
        await lines_of(scope, ITEM_LINES, len(first["tree"]))

        # -- comments on lines, kept in the page until they are sent ------------------------
        await scope.locator(f"button.diff-comment[data-ref='{refs['tyres']}']").click()
        opened = scope.locator(f"form.comment-form[data-ref='{refs['tyres']}']")
        await expect(opened).to_be_visible(timeout=SLOW)
        empty = (
            await opened.locator("textarea.comment-text").input_value() == ""
            and await opened.locator("select.comment-field").input_value() == ""
        )
        await opened.get_by_role("button", name="Cancel").click()
        await expect(opened).to_have_count(0, timeout=SLOW)
        await comment_on(scope, refs["tyres"], "Title", said_1)
        await comment_on(scope, refs["fit"], "Details", "Hours?")
        await comment_on(scope, refs["order"], None, said_3)
        await comment_on(scope, refs["pick"], "Size", "Is s right?")
        edit = scope.locator(f".diff-comment-pending[data-ref='{refs['fit']}']")
        await edit.get_by_role("button", name="Edit").click()
        form = scope.locator(f"form.comment-form[data-ref='{refs['fit']}']")
        filled = await form.locator("textarea.comment-text").input_value() == "Hours?"
        await form.locator("textarea.comment-text").fill(said_2)
        await form.locator("button[type=submit]").click()
        await expect(edit).to_contain_text(said_2, timeout=SLOW)
        await (
            scope.locator(f".diff-comment-pending[data-ref='{refs['pick']}']")
            .get_by_role("button", name="Remove")
            .click()
        )
        await expect(scope.locator(".diff-comment-pending")).to_have_count(3, timeout=SLOW)
        pending = await pending_of(scope)
        by_ref = {ref: (part, spoken(text)) for ref, part, text in pending}
        in_the_bar = spoken(await scope.locator("span.decision-state").inner_text())
        in_the_list = spoken(await scope.locator("details.change-unsent summary").inner_text())

        # Not sent: the server has not heard of any of it. Leaving the page and coming back, a
        # redraw from news (another plan arrives) and Viewed set and taken off keep it.
        async with connect(project.mcp_url, project.key) as agent:
            quiet = await read(agent, number)
            feed = (await ok(agent, "changes_since", limit=200))["items"]
            await drafted(agent, epic, "Another plan")
        await page.evaluate("location.hash = '#/coverage'")
        await expect(page.locator("h1")).to_contain_text("Coverage", timeout=SLOW)
        await page.evaluate(f"location.hash = '#/review?change={change}'")
        await expect(review_page(page)).to_have_attribute("data-change", change, timeout=SLOW)
        scope = review_page(page)
        await expect(scope.locator(".diff-comment-pending")).to_have_count(3, timeout=SLOW)
        kept = await pending_of(scope)
        viewed = scope.locator("input.change-viewed")
        await viewed.check()
        await viewed.uncheck()
        again = await pending_of(scope)
        proof(
            "M51",
            "on the review page of a plan a person comments on the lines: the composer "
            "opens empty under the line, a comment is on the whole item or on a part, it waits as "
            "'Not sent yet' with the words it is about, can be edited and removed, and is kept in "
            "the page across leaving it, a redraw from news and setting Viewed, "
            "while the server has heard of none of it (no feedback, no event, the plan still "
            "waiting for a person); the bar says how many are unsent (the sentence of the foot "
            "is gone: replaced by M52)",
            empty
            and filled
            and len(pending) == 3
            and by_ref[refs["tyres"]][0] == "title"
            and "Not sent yet" in by_ref[refs["tyres"]][1]
            and old_words[refs["tyres"]]["title"] in by_ref[refs["tyres"]][1]
            and said_1 in by_ref[refs["tyres"]][1]
            and by_ref[refs["fit"]][0] == "body"
            and "One morning each." in by_ref[refs["fit"]][1]
            and said_2 in by_ref[refs["fit"]][1]
            and by_ref[refs["order"]][0] == ""
            and old_words[refs["order"]]["title"] in by_ref[refs["order"]][1]
            and refs["pick"] not in by_ref
            and "3 unsent" in in_the_bar
            and "3 unsent comments" in in_the_list
            and quiet["feedback"] == []
            and quiet["state"] == "proposed"
            and not [e for e in feed if e["action"] == "plan_changes_requested"]
            and kept == pending == again,
            f"{len(pending)} comments, {in_the_bar}",
        )

        # -- Request changes sends exactly those comments with the revision -----------------
        await scope.locator("button.verdict[data-verb='changes']").click()
        dialog = page.locator("#changes-dialog")
        await expect(dialog).to_be_visible(timeout=SLOW)
        blank = await dialog.locator("textarea#changes-summary").input_value() == ""
        listed = spoken(await dialog.locator("ul.changes-comments").inner_text())
        await dialog.locator("button[type=submit]").click()
        await expect(dialog).to_contain_text("Say in summary what you want changed", timeout=SLOW)
        await dialog.get_by_role("button", name="Cancel").click()
        await expect(dialog).not_to_be_visible(timeout=SLOW)
        after_cancel = await pending_of(scope)
        async with connect(project.mcp_url, project.key) as agent:
            unsent = await read(agent, number)
        await scope.locator("button.verdict[data-verb='changes']").click()
        await dialog.locator("textarea#changes-summary").fill(summary)
        await dialog.locator("button[type=submit]").click()
        # A decision takes the page on to the next change (another plan waits): go to the list.
        await expect(dialog).to_have_count(0, timeout=SLOW)
        await the_needs_page(w)
        waiting = page.locator(
            f"section.needs-group[data-group='agent'] li.waiting-row[data-plan='{number}']"
        )
        await expect(waiting).to_have_count(1, timeout=SLOW)
        await expect(plan_card(page, number)).to_have_count(0, timeout=SLOW)
        async with connect(project.mcp_url, project.key) as agent:
            sent = await read(agent, number)
            feed = (await ok(agent, "changes_since", limit=200))["items"]
        (given,) = sent["feedback"]
        the_link = waiting.locator("a[href*='review']")
        link_target = await the_link.first.get_attribute("href") or ""
        proof(
            "M51",
            "Request changes opens a dialog that is empty, lists the unsent comments read-only, "
            "refuses a blank summary in the server's words inside the dialog and sends nothing, "
            "Cancel keeps every comment, and Send asks for changes as the signed-in person: the "
            "feedback on the server is exactly the typed summary and the comments that were "
            "pending, each with the words it was about as the server quotes them, for revision 1, "
            "the plan stays held and waits for its agent, it leaves the list, and the list shows "
            "it in the closed group of plans that wait for their agent, as a line that links to "
            "its review page (the one line that named it is gone: replaced by M52)",
            blank
            and all(said in listed for said in (said_1, said_2, said_3))
            and after_cancel == pending
            and unsent["feedback"] == []
            and (given["by"], given["by_kind"], given["revision"], given["summary"])
            == (owner.name, "person", 1, summary)
            and {(c["item"], c["field"], c["text"]) for c in given["comments"]}
            == {
                (refs["tyres"], "title", said_1),
                (refs["fit"], "body", said_2),
                (refs["order"], None, said_3),
            }
            and all(
                c["quoted"] == old_words[c["item"]]["body" if c["field"] == "body" else "title"]
                for c in given["comments"]
            )
            and sent["state"] == "changes_requested"
            and await the_link.count() == 1
            and f"change=plan:{number}" in link_target.replace("%3A", ":")
            and [e["actor"] for e in feed if e["action"] == "plan_changes_requested"]
            == [owner.name],
            f"{len(given['comments'])} comments, {clip(await waiting.text_content() or '', 70)}",
        )

        # -- the agent revises: a change, a removal and a new item ---------------------------
        async with connect(project.mcp_url, project.key) as agent:
            revised = await ok(
                agent,
                "revise_plan",
                plan=number,
                items=second_revision(epic, refs),
                note="Named the routes, gave the hours and took out the covers task.",
            )
            refs["measure"] = revised["created"][0]
            now = await read(agent, number)
            earlier = await read(agent, number, revision=1)
        changes = shown_changes(now)
        await expect(waiting).to_have_count(0, timeout=SLOW)
        await open_review(w, change)
        scope = review_page(page)
        await expect(scope.locator("[data-fact='revision']")).to_have_text(
            "Revision 2", timeout=SLOW
        )
        await expect(scope.locator(".change-diff[data-mode='delta']")).to_have_count(
            1, timeout=SLOW
        )
        await expect(scope.locator(CHANGED_LINES)).to_have_count(2, timeout=SLOW)
        added = await lines_of(scope, ITEM_LINES)
        removed = await lines_of(scope, REMOVED_LINES)
        changed_lines = await lines_of(scope, CHANGED_LINES)
        parts = await scope.locator(".change-diff .diff-line[data-part]").evaluate_all(PART_LINES)
        header = spoken(await scope.locator(".diff-hunk").first.inner_text()).lower()
        fold = spoken(await scope.locator(".change-diff .diff-fold").first.inner_text())
        note = spoken(await scope.locator("p.plan-revision-note").last.inner_text())
        unchanged = len(now["tree"]) - sum(1 for c in changes.values() if c[0] != "removed")
        proof(
            "M51",
            "after the agent's revision the page says Revision 2 with the agent's note and "
            "draws what changed since revision 1 by default: one added line, one removed line "
            "and a changed line for each changed item, equal to what the server says changed "
            "(the added and removed items by reference; each changed part as a line taken out "
            "with its old words and a line put in with the new ones), the hunk row counts "
            "them in words, one fold says how many items are unchanged, and the bar comes "
            "after the last line",
            holds(
                added == [r for r, c in changes.items() if c[0] == "added"] == [refs["measure"]],
                removed == [r for r, c in changes.items() if c[0] == "removed"] == [refs["order"]],
                sorted(changed_lines)
                == sorted(r for r, c in changes.items() if c[0] == "changed")
                == sorted([refs["tyres"], refs["fit"]]),
                now["revisions"][1]["note"] in note,
                "revision 2" in header,
                "1 added" in header,
                "2 changed" in header,
                "1 removed" in header,
                "@@" not in header,
                "unchanged" in fold,
                str(unchanged) in fold,
                old_words[refs["tyres"]]["title"] in part_words(parts, refs["tyres"], "-", "title"),
                "river path" in part_words(parts, refs["tyres"], "+", "title"),
                "One morning each." in part_words(parts, refs["fit"], "-", "body"),
                "Four hours each" in part_words(parts, refs["fit"], "+", "body"),
                "Four hours each" not in part_words(parts, refs["fit"], "-", "body"),
                earlier["tree"] == first["tree"],
                await the_diff_is_before_the_decision(scope),
            ),
            f"{header}; {fold}{failing()}",
        )

        # -- the earlier comments are threads under the lines they were about -----------------
        threads = await scope.locator(".diff-thread").evaluate_all(THREADS)
        under = {ref: (sign, spoken(text)) for _, ref, sign, text in threads}
        details = scope.locator("section.change-feedback details")
        shown = spoken(await details.first.inner_text())
        opened_by_default = await details.first.get_attribute("open") is not None
        proof(
            "M51",
            "the comments the person sent on revision 1 are drawn as threads under the lines they "
            "were about, so they can check that the agent answered them: the title comment under "
            "the changed story, the details comment under the changed task and the comment on "
            "the removed task under its removed line, each with the words it was about, the "
            "comment and who said it on which revision; the feedback itself is one section above "
            "the diff, open by default, with the summary in full and how many comments it has",
            len(threads) == 3
            and under[refs["tyres"]][0] == "~"
            and said_1 in under[refs["tyres"]][1]
            and "Studded tyres" in under[refs["tyres"]][1]
            and under[refs["fit"]][0] == "~"
            and said_2 in under[refs["fit"]][1]
            and under[refs["order"]][0] == "-"
            and said_3 in under[refs["order"]][1]
            and all("revision 1" in text for _, text in under.values())
            and all("You" in text or owner.name in text for _, text in under.values())
            and await details.count() == 1
            and opened_by_default
            and summary in shown
            and "3 comments" in shown,
            f"{len(threads)} threads",
        )

        # -- Whole plan is every line, with the removed item gone -----------------------------
        switch = page.locator("#review-mode")
        await whole_plan(page)
        shown_refs = await lines_of(scope, ITEM_LINES, len(now["tree"]))
        whole_removed = await scope.locator(REMOVED_LINES).count()
        whole_threads = await scope.locator(".diff-thread").count()
        pressed = [
            await switch.locator("button[data-mode='whole']").get_attribute("aria-pressed"),
            await switch.locator("button[data-mode='delta']").get_attribute("aria-pressed"),
        ]
        await switch.locator("button[data-mode='delta']").click()
        await expect(scope.locator(".change-diff[data-mode='delta']")).to_have_count(
            1, timeout=SLOW
        )
        proof(
            "M51",
            "Whole plan, one press on the review page, draws every item of revision 2 as the "
            "server's tree gives them, in its order and without the item that was removed, with "
            "the comments of the earlier feedback still under the lines they were about, and "
            "Changes brings back what changed",
            shown_refs == [i["ref"] for i in now["tree"]]
            and refs["order"] not in shown_refs
            and refs["measure"] in shown_refs
            and whole_removed == 0
            and whole_threads >= 2
            and pressed == ["true", "false"],
            f"{len(shown_refs)} lines",
        )

        # -- unsent comments of a revision that is replaced are said to be lost ----------------
        await comment_on(scope, refs["measure"], "Title", "Which stations?")
        await scope.locator("input.change-viewed").check()
        async with connect(project.mcp_url, project.key) as agent:
            await ok(
                agent,
                "revise_plan",
                plan=number,
                items=second_revision(epic, refs, measure_size="m"),
                note="Sized the measuring task.",
            )
            third = await read(agent, number)
        await expect(scope.locator("[data-fact='revision']")).to_have_text(
            "Revision 3", timeout=SLOW
        )
        lost = scope.locator("p.change-lost")
        await expect(lost).to_be_visible(timeout=SLOW)
        lost_words = spoken(await lost.inner_text())
        everything = spoken(await scope.text_content() or "")
        still = await scope.locator(".diff-comment-pending").count()
        checkbox = await scope.locator("input.change-viewed").is_checked()
        proof(
            "M51",
            "when a newer revision arrives under a person, the comments they had not sent were "
            "about the old one and are not kept: the page says once that revision 3 arrived and "
            "how many unsent comments were not kept (as a label), keeps no pending comment and the "
            "words of the lost one can still be read to write it again, and Viewed is reset (the "
            "box is clear), while the page now reads Revision 3 from the server",
            "Revision 3 arrived" in lost_words
            and "Not kept: 1 unsent comment" in lost_words
            and "Which stations?" in everything
            and still == 0
            and not checkbox
            and third["revision"] == 3,
            clip(lost_words, 100),
        )

        # -- every revision read as it was submitted, read only ---------------------------------
        async with connect(project.mcp_url, project.key) as agent:
            each = {k: await read(agent, number, revision=k) for k in (1, 2, 3)}
        choose = page.locator("select#review-revision")
        options = await choose.locator("option").all_inner_texts()
        title_words = spoken(await page.locator("h1.review-title").inner_text())
        seen: dict[int, dict[str, Any]] = {}
        for k in (1, 2, 3):
            await choose.select_option(index=k - 1)
            await expect(page.locator(f"#review-diff[data-revision='{k}']")).to_have_count(
                1, timeout=SLOW
            )
            address = await page.evaluate("location.hash")
            if k > 1:
                # The choice of how to read is kept for the plan, not for each revision.
                delta = page.locator("#review-mode button[data-mode='delta']")
                if await delta.get_attribute("aria-pressed") != "true":
                    await delta.click()
                await expect(page.locator(".change-diff[data-mode='delta']")).to_have_count(
                    1, timeout=SLOW
                )
                delta_added = await lines_of(page.locator(THE_PAGE), ITEM_LINES)
                delta_removed = await lines_of(page.locator(THE_PAGE), REMOVED_LINES)
                delta_changed = await lines_of(page.locator(THE_PAGE), CHANGED_LINES)
                await page.locator("#review-mode button[data-mode='whole']").click()
            else:
                delta_added = delta_removed = delta_changed = []
            await expect(page.locator(".change-diff[data-mode='whole']")).to_have_count(
                1, timeout=SLOW
            )
            whole = await lines_of(page.locator(THE_PAGE), ITEM_LINES, len(each[k]["tree"]))
            text = spoken(await page.locator(THE_PAGE).inner_text())
            seen[k] = {
                "address": address,
                "added": delta_added,
                "removed": delta_removed,
                "changed": delta_changed,
                "whole": whole,
                "text": text,
                "threads": await page.locator(".diff-thread").count(),
                "controls": await page.locator(f"{BUTTONS_THAT_DECIDE}, {WRITING}").count(),
            }
        wanted = {
            k: shown_changes(each[k]) if each[k]["changes"] is not None else {} for k in (1, 2, 3)
        }
        proof(
            "M51",
            "the review page of a plan reads every revision as it was submitted (the revision "
            "is chosen in its address): its title and code, a choice of the three revisions with "
            "the current one named, who made each and the agent's note, the feedback given on "
            "revision 1 as threads under its lines, for each later revision what changed since "
            "the one before (added, removed and changed lines equal to list_plans with that "
            "revision) and, with Whole plan, every item equal to that revision's tree; an "
            "earlier revision has no button to decide or comment (the plan's own page is gone: "
            "replaced by M52)",
            title_words == "Winter tyres"
            and len(options) == 3
            and "current" in options[2]
            and "current" not in options[0]
            and all(seen[k]["controls"] == 0 for k in (1, 2))
            and all(f"revision={k}" in seen[k]["address"] for k in (1, 2))
            and each[1]["revisions"][0]["by"] in seen[1]["text"]
            and each[2]["revisions"][1]["by"] in seen[2]["text"]
            and each[2]["revisions"][1]["note"] in seen[2]["text"]
            and each[3]["revisions"][2]["note"] in seen[3]["text"]
            and summary in seen[1]["text"]
            and seen[1]["threads"] == 3
            and seen[2]["threads"] == 0
            and all(
                seen[k]["added"] == [r for r, c in wanted[k].items() if c[0] == "added"]
                and seen[k]["removed"] == [r for r, c in wanted[k].items() if c[0] == "removed"]
                and sorted(seen[k]["changed"])
                == sorted(r for r, c in wanted[k].items() if c[0] == "changed")
                for k in (2, 3)
            )
            and all(seen[k]["whole"] == [i["ref"] for i in each[k]["tree"]] for k in (1, 2, 3)),
            f"{len(options)} revisions, {[len(seen[k]['whole']) for k in (1, 2, 3)]} lines",
        )

        # -- the decision names the revision that was read ---------------------------------------
        await open_review(w, change)
        scope = review_page(page)
        await expect(scope.locator("[data-fact='revision']")).to_have_text(
            "Revision 3", timeout=SLOW
        )
        hovers: dict[str, str] = {}
        for verb in ("approve", "changes", "reject"):
            button = scope.locator(f"button.verdict[data-verb='{verb}']")
            await button.focus()
            card = page.locator(".hovercard:visible")
            await expect(card).to_be_visible(timeout=SLOW)
            hovers[verb] = spoken(await card.first.inner_text())
        order = await scope.locator("div.decision-bar button.verdict").evaluate_all(
            "els => els.map(e => e.dataset.verb)"
        )
        await scope.locator("button.verdict[data-verb='approve']").click()
        # A decision takes the page on: it leaves this plan's review page.
        await expect(page.locator(f"#review-page[data-change='{change}']")).to_have_count(
            0, timeout=SLOW
        )
        async with connect(project.mcp_url, project.key) as agent:
            decided = await read(agent, number)
            ready = {n["ref"] for n in (await ok(agent, "ready_work", limit=200))["items"]}
            gone = await ok(agent, "get_node", node=refs["order"])
            held = [(await ok(agent, "get_node", node=r))["held_by"] for r in refs.values()]
        feed = (await ok_feed(project))["items"]
        proof(
            "M51",
            "the hover card of each verdict on the bar says what it does for revision 3 "
            "(approving frees its five items, asking for changes keeps the plan held, rejecting "
            "drops all of it) with the three buttons in order (the foot's sentences are gone: "
            "replaced by M52); pressing Approve approves revision 3 as the signed-in person, "
            "which frees what is left of the plan, while the task that revision 2 took out stays "
            "dropped and is not ready",
            "5 items" in hovers["approve"]
            and "frees" in hovers["approve"].lower()
            and "held" in hovers["changes"].lower()
            and "drops" in hovers["reject"].lower()
            and order == ["approve", "changes", "reject"]
            and decided["state"] == "approved"
            and decided["revision"] == 3
            and decided["decided_by"] == owner.name
            and gone["status"] == "dropped"
            and refs["order"] not in ready
            and refs["pick"] in ready
            and [by for r, by in zip(refs, held, strict=True) if r != "order"] == [[]] * 5
            and any(e["action"] == "plan_approved" and e["after"]["revision"] == 3 for e in feed),
            f"approved revision {decided['revision']} as {decided['decided_by']}",
        )

        # -- the sentences of the history ----------------------------------------------------
        await go(page, w.board, "#/activity")
        await expect(page.locator("#feed li.event").first).to_be_visible(timeout=SLOW)
        rows = page.locator("#feed li.event")
        for _ in range(40):
            older = page.locator("#show-older")
            if not await older.count():
                break
            seen_rows = await rows.count()
            await older.click()
            await page.wait_for_function(
                "(n) => document.querySelectorAll('#feed li.event').length > n"
                " || document.getElementById('show-older') === null",
                arg=seen_rows,
                timeout=SLOW,
            )
        history = spoken(await page.locator("#feed").inner_text())
        plan_links = await page.locator(
            f"#feed a[href*='review?change=plan:{number}'], "
            f"#feed a[href*='review?change=plan%3A{number}']"
        ).count()
        agent_name = now["revisions"][1]["by"]
        proof(
            "M51",
            "the history says it in plain words: the agent sent revision 2 of the plan with its "
            "note whole, the person asked for changes to revision 1 of the plan with the summary "
            "and how many comments, the plan was approved at revision 3, and the plan's code is "
            "a link to its review page",
            holds(
                f"{agent_name} sent revision 2 of Plan {number}" in history,
                now["revisions"][1]["note"] in history,
                f"{owner.name} asked for changes to revision 1 of Plan {number}" in history,
                # The sentence follows the summary with its count: a full stop is left out.
                summary.rstrip(".") in history,
                "3 comments" in history,
                f"approved revision 3 of Plan {number}" in history,
                plan_links >= 1,
            ),
            f"{clip(history, 160)}{failing()}",
        )


async def ok_feed(project: Project) -> dict[str, Any]:
    async with connect(project.mcp_url, project.key) as agent:
        return await ok(agent, "changes_since", limit=200)


async def a_stale_decision_is_refused_and_the_page_reads_again() -> None:
    """The page was not told of the agent's revision (its news is held back, so that the few
    seconds between a change and the page's next look are made certain): Approve and Request
    changes name the revision the person read, are refused in the server's words and decide
    nothing, and the page then reads the plan again at once."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await open_review(w, f"plan:{number}")
        scope = review_page(page)
        await lines_of(scope, ITEM_LINES, 5)
        await hold_back_news(page)
        async with connect(project.mcp_url, project.key) as agent:
            await ok(
                agent,
                "revise_plan",
                plan=number,
                items=revision(epic, refs, tyres={"title": NEW_TITLE}),
                note="Named the routes.",
            )
        before = await scope.locator("[data-fact='revision']").count()
        await scope.locator("button.verdict[data-verb='approve']").click()
        await told(page, "is at revision 2 now, and you reviewed revision 1")
        await expect(scope.locator("[data-fact='revision']")).to_have_text(
            "Revision 2", timeout=SLOW
        )
        async with connect(project.mcp_url, project.key) as agent:
            after_approve = await read(agent, number)
            await ok(
                agent,
                "revise_plan",
                plan=number,
                items=revision(epic, refs, tyres={"title": NEW_TITLE + " and the bridges"}),
                note="Added the bridges.",
            )
        await scope.locator("button.verdict[data-verb='changes']").click()
        dialog = page.locator("#changes-dialog")
        await expect(dialog).to_be_visible(timeout=SLOW)
        await dialog.locator("textarea#changes-summary").fill("Smaller, please.")
        await dialog.locator("button[type=submit]").click()
        await told(page, "is at revision 3 now, and you reviewed revision 2")
        if await dialog.is_visible():
            await dialog.get_by_role("button", name="Cancel").click()
        await expect(scope.locator("[data-fact='revision']")).to_have_text(
            "Revision 3", timeout=SLOW
        )
        async with connect(project.mcp_url, project.key) as agent:
            after = await read(agent, number)
        proof(
            "M51",
            "a decision made on a page that was not told of the agent's revision is refused in "
            "the server's words, beside the bar for Approve and in the dialog for Request "
            "changes, decides nothing (the plan is still waiting for a person with no feedback "
            "and nothing is free), and the page reads the plan again at once, without waiting "
            "for news, and shows the revision the server has now",
            before == 0
            and (after_approve["state"], after_approve["revision"]) == ("proposed", 2)
            and (after["state"], after["revision"], after["feedback"]) == ("proposed", 3, [])
            and after["revisions"][2]["note"] == "Added the bridges.",
            f"page now at revision {after['revision']}",
        )


async def drift_is_shown_and_approval_is_refused_in_words() -> None:
    """A plan whose item was changed outside a revision says so above its diff, as a warning with
    the item as a link, and the Approve button stays pressable and is refused in the server's
    words."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        version = (await ok(agent, "get_node", node=refs["pick"]))["version"]
        await ok(
            agent,
            "update_node",
            node=refs["pick"],
            expected_version=version,
            title="Choose another supplier",
        )
    code = f"T-{refs['pick'].split(':')[1]}"
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await open_review(w, f"plan:{number}")
        scope = review_page(page)
        await expect(scope.locator("section.change-drift")).to_be_visible(timeout=SLOW)
        drift = scope.locator("section.change-drift")
        text = spoken(await drift.inner_text())
        role = await drift.get_attribute("role")
        links = await drift.locator("a").count()
        before_the_diff = bool(
            await scope.evaluate(
                "el => !!(el.querySelector('section.change-drift').compareDocumentPosition("
                "el.querySelector('.change-diff')) & Node.DOCUMENT_POSITION_FOLLOWING)"
            )
        )
        enabled = await scope.locator("button.verdict[data-verb='approve']").is_enabled()
        await scope.locator("button.verdict[data-verb='approve']").click()
        await told(page, "was changed after revision 1 was submitted")
        async with connect(project.mcp_url, project.key) as agent:
            still = await read(agent, number)
        proof(
            "M51",
            "a plan that was changed outside a revision shows a warning that is always visible "
            "above its diff, naming the item and the part that changed as a link (the advice "
            "what to do is gone: replaced by M52), Approve is still there to press, and the "
            "server's refusal is shown in its own words with the plan unchanged",
            role == "alert"
            and f"Changed since revision 1, outside a revision: {code} (title)" in text
            and "Ask for changes" not in text
            and links >= 1
            and before_the_diff
            and enabled
            and still["state"] == "proposed"
            and still["drift"] == [{"ref": refs["pick"], "fields": ["title"]}],
            clip(text, 100),
        )


async def a_plan_with_a_conversation(
    project: Project, owner: Person
) -> tuple[int, dict[str, str], dict[str, Any]]:
    """A plan at revision 2: the owner asked for changes with two comments and the agent revised.
    Returns its number, its items and what the plan reads now."""
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        asked = person(
            project,
            owner,
            "request_plan_changes",
            plan=number,
            revision=1,
            summary="Name the routes and give the hours.",
            comments=[
                {"item": refs["tyres"], "field": "title", "text": "Say which routes."},
                {"item": refs["fit"], "field": "body", "text": "How many hours?"},
            ],
        )
        assert asked.status == 200, asked.body
        await ok(
            agent,
            "revise_plan",
            plan=number,
            items=revision(epic, refs, tyres={"title": NEW_TITLE}, fit={"body": NEW_BODY}),
            note="Named the routes and gave the hours.",
        )
        return number, refs, await read(agent, number)


async def a_viewer_reads_the_conversation_and_cannot_write_in_it() -> None:
    """A viewer sees the revision, what changed, the earlier comments as threads, the feedback and
    every revision, and none of the controls that comment or decide."""
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer")
    number, refs, now = await a_plan_with_a_conversation(project, owner)
    changes = shown_changes(now)
    async with browser_page(project.address, token=None, person=viewer, name=None) as w:
        page = w.page
        await the_needs_page(w)
        card = plan_card(page, number)
        await expect(card).to_have_count(1, timeout=SLOW)
        await expect(card.locator("[data-fact='revision']")).to_have_text(
            "Revision 2", timeout=SLOW
        )
        on_the_list = await page.locator(f"{BUTTONS_THAT_DECIDE}, {WRITING}").count()
        await open_review(w, f"plan:{number}")
        scope = review_page(page)
        await expect(scope.locator(CHANGED_LINES)).to_have_count(2, timeout=SLOW)
        changed = await lines_of(scope, CHANGED_LINES)
        threads = await scope.locator(".diff-thread").count()
        feedback = spoken(await scope.locator("section.change-feedback").inner_text())
        writing = await page.locator(f"{BUTTONS_THAT_DECIDE}, {WRITING}").count()
        options = await page.locator("select#review-revision option").count()
        await page.locator("select#review-revision").select_option(index=0)
        await expect(page.locator("#review-diff[data-revision='1']")).to_have_count(1, timeout=SLOW)
        on_the_first = await page.locator(f"{BUTTONS_THAT_DECIDE}, {WRITING}").count()
        proof(
            "M51",
            "a viewer reads the conversation: the list says Revision 2, the review page shows "
            "what changed and the earlier comments as threads and the feedback with its summary, "
            "and reads both revisions, with no button to comment, to ask for changes, to approve "
            "or to reject anywhere on either page",
            sorted(changed) == sorted(r for r, c in changes.items() if c[0] == "changed")
            and threads == 2
            and "Name the routes and give the hours." in feedback
            and on_the_list == 0
            and writing == 0
            and on_the_first == 0
            and options == 2,
            f"{threads} threads, {on_the_list + writing + on_the_first} controls",
        )


async def a_narrow_window_has_no_sideways_scroll() -> None:
    """At 390 px the review page in Changes mode, the composer under a line and the dialog fit the
    window."""
    owner = register()
    project = make_project(owner)
    number, refs, now = await a_plan_with_a_conversation(project, owner)
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await page.set_viewport_size(NARROW)  # type: ignore[arg-type]
        await the_needs_page(w)
        listed = await overflows(page)
        await open_review(w, f"plan:{number}")
        scope = review_page(page)
        await expect(scope.locator(CHANGED_LINES)).to_have_count(2, timeout=SLOW)
        delta = await overflows(page)
        await scope.locator(f"button.diff-comment[data-ref='{refs['fit']}']").click()
        composer = scope.locator(f"form.comment-form[data-ref='{refs['fit']}']")
        await expect(composer).to_be_visible(timeout=SLOW)
        box = await composer.bounding_box()
        in_window = box is not None and box["x"] >= 0 and box["x"] + box["width"] <= 390
        with_composer = await overflows(page)
        await scope.locator("button.verdict[data-verb='changes']").click()
        dialog = page.locator("#changes-dialog")
        await expect(dialog).to_be_visible(timeout=SLOW)
        in_dialog = await overflows(page)
        await dialog.get_by_role("button", name="Cancel").click()
        await whole_plan(page)
        in_whole = await overflows(page)
        proof(
            "M51",
            "at 390 px the list, the review page in Changes mode, the composer open under a "
            "line, the dialog for asking for changes and the whole plan each fit the window with "
            "no sideways scroll, and the composer is inside the window",
            not listed
            and not delta
            and not with_composer
            and not in_dialog
            and not in_whole
            and in_window,
            "no overflow",
        )


async def revising_on_the_page() -> None:
    await the_conversation()
    await a_stale_decision_is_refused_and_the_page_reads_again()
    await drift_is_shown_and_approval_is_refused_in_words()
    await a_viewer_reads_the_conversation_and_cannot_write_in_it()
    await a_narrow_window_has_no_sideways_scroll()
