"""Browser proofs of the M53 chapter (tests/acceptance_granular.py): saying yes to a line of a plan
as well as asking for a change to it, what a line carries from what a person said before (approved,
asked, changed since), the blame chip on every line, Hide approved, the card of a revised plan that
previews what changed, and a suggested move accepted in part. Each proof compares what the page
shows with what a real MCP client reads from the tools (`list_plans`, `list_placements`,
`get_node`), in a project of its own, with a headless browser signed in as the owner or a viewer of
it. What the server records is proved in tests/acceptance_granular.py, which this file builds on."""

import asyncio
import re
from typing import Any

from playwright.async_api import Locator, Page, expect

from tests.acceptance_granular import (
    a_conversation,
    ask,
    drafted,
    revised,
)
from tests.acceptance_review import open_review, plan_card, review_page, the_needs_page
from tests.acceptance_revising import an_epic, person, read
from tests.acceptance_revising_page import NARROW, failing, holds, overflows, spoken
from tests.acceptance_scenario import clip, proof
from tests.acceptance_standard_decide import CONTRAST
from tests.board_helpers import SLOW, browser_page, go, sentences
from tests.demo_flow import in_order
from tests.helpers import code_of, connect, ok
from tests.world import join_project, make_project, register

WIDE = {"width": 1280, "height": 1000}
# Each item line of a diff (not a part line of a changed item), with what is beside and at the end
# of it: the approve and comment buttons and the blame chip of its slot, the chip that says what
# is carried or waiting to be sent, and the fold or the hidden state it is in.
ITEMS = ".change-diff .diff-line[data-ref]:not([data-part]):not([data-sign='.'])"
LINES = """els => els.filter(line => line.checkVisibility()).map(line => {
    const slot = line.nextElementSibling && line.nextElementSibling.matches('.diff-comment-slot')
        ? line.nextElementSibling : null;
    const chip = line.querySelector('.line-review');
    const approve = slot && slot.querySelector('button.diff-approve');
    const comment = slot && slot.querySelector('button.diff-comment');
    const blame = slot && slot.querySelector('button.blame');
    return {
        ref: line.dataset.ref,
        sign: line.dataset.sign,
        state: chip ? chip.dataset.state : null,
        pending: chip ? chip.dataset.pending === 'true' : false,
        word: chip ? chip.textContent.trim() : null,
        pressed: approve ? approve.getAttribute('aria-pressed') : null,
        disabled: approve ? approve.disabled : null,
        label: approve ? approve.getAttribute('aria-label') : null,
        comment: comment ? comment.getAttribute('aria-label') : null,
        revision: blame ? blame.dataset.revision : null,
        change: blame ? blame.dataset.change : null,
        said: blame ? blame.textContent.trim() : null,
    };
})"""
# A button or something that behaves as one that holds another: an element nobody can press right.
NESTED = """() => [...document.querySelectorAll('button, [role=button]')]
    .filter(e => e.querySelector('button, [role=button]'))
    .map(e => (e.getAttribute('aria-label') || e.textContent || '').trim().slice(0, 30))"""


def read_of(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """What the page should say of each item, from the server's own `blame`, by reference."""
    return {line["ref"]: line for line in plan["blame"]}


async def lines(scope: Locator) -> dict[str, dict[str, Any]]:
    """What the page shows beside and on every item line that is on show, by reference (a split
    layout draws an item once on each side, so the first of its lines is the one read)."""
    found: list[dict[str, Any]] = await scope.locator(ITEMS).evaluate_all(LINES)
    seen: dict[str, dict[str, Any]] = {}
    for one in found:
        seen.setdefault(one["ref"], one)
    return seen


async def counted(scope: Locator, count: int) -> None:
    """Wait until this many item lines are on show."""
    await expect(scope.locator(ITEMS).locator("visible=true")).to_have_count(count, timeout=SLOW)


async def mode_and_view(page: Page, mode: str, view: str) -> None:
    """Pick how the plan is read (`delta` is Changes, `whole` the whole plan) and how its lines are
    laid out (`unified` or `split`), as a person does with the two choices on the review page."""
    choice = page.locator(f"#review-mode button[data-mode='{mode}']")
    if (
        await page.locator("#review-mode").count()
        and await choice.get_attribute("aria-pressed") != "true"
    ):
        await choice.click()
    pick = page.locator(f"#review-view button[data-view='{view}']")
    if await pick.get_attribute("aria-pressed") != "true":
        await pick.click()
    await expect(page.locator(f".change-diff[data-view='{view}']")).to_have_count(1, timeout=SLOW)
    await expect(page.locator(f".change-diff[data-mode='{mode}']")).to_have_count(1, timeout=SLOW)
    await expect(page.locator(".change-diff .diff-line[data-ref]").first).to_be_visible(
        timeout=SLOW
    )
    await expect(
        page.locator(".change-diff .diff-comment-slot, .change-diff .blame").first
    ).to_be_attached(timeout=SLOW)


async def facts_of(scope: Locator) -> dict[str, str]:
    """The fact chips of a change, by what they are about."""
    found: list[list[str]] = await scope.locator("ul.change-facts > li[data-fact]").evaluate_all(
        "els => els.map(e => [e.dataset.fact, e.textContent])"
    )
    return {name: spoken(text) for name, text in found}


async def pressing(scope: Locator, ref: str) -> None:
    """Press the approve button of an item's line."""
    await scope.locator(f"button.diff-approve[data-ref='{ref}']").first.click()


async def pending_comment(scope: Locator, ref: str, text: str, label: str | None = None) -> None:
    """Write a comment on an item through the box under its line."""
    count = await scope.locator(f".diff-comment-pending[data-ref='{ref}']").count()
    await scope.locator(f"button.diff-comment[data-ref='{ref}']").first.click()
    form = scope.locator(f"form.comment-form[data-ref='{ref}']")
    await expect(form).to_be_visible(timeout=SLOW)
    if label is not None:
        await form.locator("select.comment-field").select_option(label=label)
    await form.locator("textarea.comment-text").fill(text)
    await form.locator("button[type=submit]").click()
    await expect(scope.locator(f".diff-comment-pending[data-ref='{ref}']")).to_have_count(
        count + 1, timeout=SLOW
    )


async def verdicts_wait_in_the_page_until_they_are_sent() -> None:
    """On a plan's review page each item line has a button to approve it and one to ask for a
    change; an approval waits in the page like a comment (pressed, shown on the line as approved
    and not sent, counted in the progress fact and in the bar), an item cannot have both (a
    comment turns an approval off and an item with a comment cannot be approved, and says why),
    nothing reaches the server until Request changes, and what it sends is exactly what waited."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        first = await read(agent, number)
    codes = {ref: code_of(ref) for ref in refs.values()}
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await open_review(w, f"plan:{number}")
        scope = review_page(page)
        await counted(scope, len(first["tree"]))
        before = await lines(scope)
        none_yet = await facts_of(scope)
        await pressing(scope, refs["pick"])
        await pressing(scope, refs["order"])
        await pressing(scope, refs["covers"])
        await pressing(scope, refs["covers"])
        after = await lines(scope)
        two = await facts_of(scope)
        in_the_bar = spoken(await scope.locator("span.decision-state").first.inner_text())
        # An approval is turned off by a comment on the same item, and an item with a comment
        # cannot be approved.
        await pending_comment(scope, refs["pick"], "Is s right for this?", "Size")
        turned_off = (await lines(scope))[refs["pick"]]
        await pending_comment(scope, refs["fit"], "Hours, please.", "Details")
        blocked = (await lines(scope))[refs["fit"]]
        approve_fit = scope.locator(f"button.diff-approve[data-ref='{refs['fit']}']").first
        await approve_fit.locator("xpath=..").hover()
        asking = page.locator(".hovercard:visible", has_text="comment")
        await expect(asking.first).to_be_visible(timeout=SLOW)
        reason = spoken(await asking.first.inner_text())
        await page.mouse.move(0, 0)
        one = await facts_of(scope)
        # Taking the comment away lets the item be approved again.
        await (
            scope.locator(f".diff-comment-pending[data-ref='{refs['fit']}']")
            .get_by_role("button", name="Remove")
            .click()
        )
        await expect(approve_fit).to_be_enabled(timeout=SLOW)
        await pressing(scope, refs["fit"])
        again = await lines(scope)
        async with connect(project.mcp_url, project.key) as agent:
            quiet = await read(agent, number)
            feed = (await ok(agent, "changes_since", limit=200))["items"]
        pending_refs = [refs["order"], refs["fit"]]
        bar_now = spoken(await scope.locator("span.decision-state").first.inner_text())
        proof(
            "M53",
            "on the review page of a plan every item line has a button to approve it and one to "
            "ask for a change, both named for the item, none pressed and no state on any line at "
            "first, and no progress fact while nothing is approved; pressing approve toggles a "
            "pending approval (pressed, the line says approved with the pending mark, the "
            "progress fact says how many of the plan's items, the bar says how many approved); "
            "a comment on an approved item turns its approval off, an item with a comment cannot "
            "be approved (the button is disabled and its hover card says why) until the comment "
            "is removed; and the server has heard of none of it",
            holds(
                len(before) == len(first["tree"]),
                all(
                    one_line["pressed"] == "false"
                    and one_line["state"] is None
                    and one_line["disabled"] is False
                    and one_line["label"] == f"Approve {codes[ref]}"
                    and one_line["comment"] == f"Comment on {codes[ref]}"
                    for ref, one_line in before.items()
                ),
                "review" not in none_yet,
                all(
                    (after[ref]["pressed"], after[ref]["state"], after[ref]["pending"])
                    == ("true", "approved", True)
                    for ref in (refs["pick"], refs["order"])
                ),
                after[refs["pick"]]["word"] == "Approved",
                (after[refs["covers"]]["pressed"], after[refs["covers"]]["state"])
                == ("false", None),
                two.get("review") == f"2 of {len(first['tree'])} approved",
                "2 approved" in in_the_bar,
                (turned_off["pressed"], turned_off["state"], turned_off["disabled"])
                == ("false", None, True),
                (blocked["pressed"], blocked["disabled"]) == ("false", True),
                "comment" in reason.lower(),
                one.get("review") == f"1 of {len(first['tree'])} approved",
                (again[refs["fit"]]["pressed"], again[refs["fit"]]["state"])
                == ("true", "approved"),
                sorted(r for r, v in again.items() if v["pressed"] == "true")
                == sorted(pending_refs),
                quiet["feedback"] == [],
                quiet["state"] == "proposed",
                not [e for e in feed if e["action"] == "plan_changes_requested"],
            ),
            f"{in_the_bar} -> {bar_now}; reason {reason!r}{failing()}",
        )

        # -- Request changes sends exactly what waited ------------------------------------------
        await scope.locator("button.verdict[data-verb='changes']").click()
        dialog = page.locator("#changes-dialog")
        await expect(dialog).to_be_visible(timeout=SLOW)
        approved_listed = spoken(await dialog.locator("ul.changes-approved").inner_text())
        comments_listed = spoken(await dialog.locator("ul.changes-comments").inner_text())
        blank = await dialog.locator("textarea#changes-summary").input_value() == ""
        await dialog.locator("button[type=submit]").click()
        await expect(dialog).to_contain_text("Say in summary what you want changed", timeout=SLOW)
        await dialog.locator("textarea#changes-summary").fill("Smaller, and the size of the pick.")
        await dialog.locator("button[type=submit]").click()
        await expect(dialog).to_have_count(0, timeout=SLOW)
        await the_needs_page(w)
        await expect(page.locator(f"li.waiting-row[data-plan='{number}']")).to_have_count(
            1, timeout=SLOW
        )
        async with connect(project.mcp_url, project.key) as agent:
            sent = await read(agent, number)
        (given,) = sent["feedback"]
        proof(
            "M53",
            "Request changes lists what waits as two lists (the approved items by their codes "
            "and the comments), refuses a blank summary in the server's words and sends nothing, "
            "and sends exactly what waited: the feedback on the server has the approved items "
            "(epics first, each once) and the comments, nothing else, as the signed-in person, "
            "and the plan waits for its agent",
            holds(
                blank,
                all(codes[ref] in approved_listed for ref in pending_refs),
                "Is s right for this?" in comments_listed,
                given["approved"] == in_order(pending_refs),
                [(c["item"], c["field"], c["text"]) for c in given["comments"]]
                == [(refs["pick"], "size", "Is s right for this?")],
                (given["by"], given["by_kind"], given["revision"]) == (owner.name, "person", 1),
                sent["state"] == "changes_requested",
            ),
            f"{len(given['approved'])} approved, {len(given['comments'])} comment{failing()}",
        )


STATE_WORDS = {"approved": "Approved", "asked": "Asked", "changed": "Changed"}


def disagreements(shown: dict[str, dict[str, Any]], plan: dict[str, Any], where: str) -> list[str]:
    """What the page says of each item line that is on show, held against the plan's `blame`:
    the state of what a person said (none draws nothing), its word, the revision on the blame
    chip and what that revision did to the line."""
    wanted = read_of(plan)
    wrong: list[str] = []
    for ref, line in shown.items():
        if line["sign"] not in ("+", "-", "changed"):
            continue
        one = wanted.get(ref)
        if one is None:
            wrong.append(f"{where}: {ref} is not in the blame")
            continue
        state = one["review"]["state"]
        expected = None if state == "none" else state
        said = (
            f"removed in r{one['revision']}"
            if one["change"] == "removed"
            else f"r{one['revision']}"
        )
        if line["state"] != expected or line["pending"]:
            wrong.append(f"{where}: {ref} says {line['state']}, not {expected}")
        if expected and line["word"] != STATE_WORDS[expected]:
            wrong.append(f"{where}: {ref} has the word {line['word']}")
        if (line["revision"], line["change"], line["said"]) != (
            str(one["revision"]),
            one["change"],
            said,
        ):
            wrong.append(f"{where}: {ref} blame says {line['revision']} {line['said']}")
    return wrong


async def hovered(page: Page, target: Locator) -> str:
    """What the hover card of a control says, read by pointing at it, as a person does."""
    await page.mouse.move(0, 0)
    await page.evaluate("document.activeElement && document.activeElement.blur()")
    await expect(page.locator(".hovercard:visible")).to_have_count(0, timeout=SLOW)
    await target.hover()
    card = page.locator(".hovercard:visible").first
    await expect(card).to_be_visible(timeout=SLOW)
    words = spoken(await card.inner_text())
    await page.mouse.move(0, 0)
    return words


async def every_line_carries_what_the_tools_say() -> None:
    """On a plan that has had three revisions and two requests, the review page says on every
    line what the server's blame says: which revision last changed it (a chip that reads the plan
    as of that revision when it is pressed) and what a person said about it that still stands,
    in the Changes and in the Whole plan read, in the unified and the split layout, at the
    current revision and as it was at an earlier one (where the removed line is red)."""
    found = await a_conversation()
    number, refs = found.number, found.refs
    rev3, rev2, rev1 = found.reads["rev3"], found.reads["again_2"], found.reads["again_1"]
    wrong: list[str] = []
    drawn = 0
    async with browser_page(found.project.address, token=found.project.key, name=None) as w:
        page = w.page
        await open_review(w, f"plan:{number}")
        scope = review_page(page)
        for mode in ("whole", "delta"):
            for view in ("unified", "split"):
                await mode_and_view(page, mode, view)
                shown = await lines(scope)
                drawn += len(shown)
                wrong += disagreements(shown, rev3, f"revision 3 {mode} {view}")
                if mode == "whole" and sorted(shown) != sorted(
                    item["ref"] for item in rev3["tree"]
                ):
                    wrong.append(f"revision 3 whole {view}: the lines are {sorted(shown)}")
        changed_only = await lines(scope)
        await mode_and_view(page, "whole", "unified")
        in_whole = await lines(scope)
        # The two chips that tell most: who last changed a line and what a person said about it.
        sample = in_whole[refs["pick"]]
        blame_words = await hovered(
            page, scope.locator(f"button.blame[data-ref='{refs['pick']}']").first
        )
        agent_name = rev3["revisions"][2]["by"]
        approved_words = await hovered(
            page, scope.locator(f".line-review[data-ref='{refs['order']}']").first
        )
        # Pressing the chip reads the plan as of that revision.
        await scope.locator(f"button.blame[data-ref='{refs['tyres']}']").first.click()
        await expect(page).to_have_url(re.compile(r"revision=2"), timeout=SLOW)
        await expect(page.locator("#review-diff[data-revision='2']")).to_have_count(1, timeout=SLOW)
        await open_review(w, f"plan:{number}", revision="2")
        await mode_and_view(page, "whole", "unified")
        at_two = await lines(scope)
        await mode_and_view(page, "delta", "unified")
        removed_line = await lines(scope)
        drawn += len(at_two) + len(removed_line)
        wrong += disagreements(at_two, rev2, "revision 2 whole")
        wrong += disagreements(removed_line, rev2, "revision 2 changes")
        await open_review(w, f"plan:{number}", revision="1")
        await mode_and_view(page, "whole", "unified")
        at_one = await lines(scope)
        drawn += len(at_one)
        wrong += disagreements(at_one, rev1, "revision 1")
    gone = refs["chains"]
    proof(
        "M53",
        "every item line of the review page says what the server's blame says of it: the chip of "
        "what a person said that still stands (approved, asked or changed, in an icon and a "
        "word, nothing for an item nobody spoke of), and the blame chip with the revision that "
        "last changed the line ('r2', 'removed in r2' for a line the revision took out), in the "
        "Whole plan and the Changes read and in the unified and the split layout at the current "
        "revision, and as it was at revision 2 (where the removed task is a red line the owner "
        "agreed to) and at revision 1; the blame chip's hover card names the revision's author "
        "and says its note whole, the chip of an approval names who approved and on which "
        "revision, and pressing the chip of a line reads the plan as of that revision",
        holds(
            not wrong,
            drawn >= 25,
            sorted(in_whole) == sorted(item["ref"] for item in rev3["tree"]),
            all(refs[key] not in changed_only for key in ("tyres", "order", "battery")),
            sample["state"] == "changed" and sample["said"] == "r3",
            f"Revision 3 by {agent_name}" in blame_words,
            found.owner.name in approved_words and "revision 1" in approved_words,
            gone in removed_line
            and removed_line[gone]["said"] == "removed in r2"
            and removed_line[gone]["state"] == "approved",
        ),
        f"{drawn} lines read{failing()}"
        + (f"; wrong: {wrong[:3]}" if wrong else "")
        + f"; blame {blame_words!r}; approved {approved_words!r}",
    )


ALL_LINES = """els => els.map(e => [e.dataset.sign || '', e.dataset.ref || '', e.dataset.part || '',
    e.checkVisibility()])"""


async def every_line(scope: Locator) -> list[tuple[str, str, str, bool]]:
    """Every line of the diff, on show or folded: its sign, item, part and whether it is on show."""
    found: list[list[Any]] = await scope.locator(".change-diff .diff-line").evaluate_all(ALL_LINES)
    return [(sign, ref, part, bool(shown)) for sign, ref, part, shown in found]


def runs_of(order: list[str], approved: set[str]) -> list[int]:
    """How long each run of approved items is in the order the lines are drawn in."""
    runs: list[int] = []
    run = 0
    for ref in order:
        if ref in approved:
            run += 1
        elif run:
            runs.append(run)
            run = 0
    if run:
        runs.append(run)
    return runs


async def hide_approved_hides_exactly_the_approved_lines() -> None:
    """Hide approved is there only when some item is approved, is off by default and is kept in the
    address; with it on, the lines of the approved items (carried from before or waiting to be
    sent) are folded into one row for each run that says how many it holds, nothing is dropped
    (the lines are the same lines, folded), and the fold shows them again."""
    found = await a_conversation()
    number, refs = found.number, found.refs
    plan = found.reads["rev3"]
    carried = sorted(r for r, v in read_of(plan).items() if v["review"]["state"] == "approved")
    async with browser_page(found.project.address, token=found.project.key, name=None) as w:
        page = w.page
        await open_review(w, f"plan:{number}")
        scope = review_page(page)
        await mode_and_view(page, "whole", "unified")
        toggle = page.locator("button#review-hide-approved")
        await expect(toggle).to_have_count(1, timeout=SLOW)
        off = await toggle.get_attribute("aria-pressed")
        unhidden = await every_line(scope)
        shown_before = [
            one for one in unhidden if one[3] and not one[2] and one[0] in ("+", "-", "changed")
        ]
        order = [
            ref for sign, ref, part, _ in unhidden if not part and sign in ("+", "-", "changed")
        ]
        runs_carried = runs_of(order, set(carried))
        runs_pending = runs_of(order, {*carried, refs["battery"]})
        facts = await facts_of(scope)
        await toggle.click()
        await expect(toggle).to_have_attribute("aria-pressed", "true", timeout=SLOW)
        rows = scope.locator(".diff-foldrow[data-fold='approved']")
        await expect(rows).to_have_count(len(runs_carried), timeout=SLOW)
        hidden = await every_line(scope)
        counts = [
            int(re.search(r"\d+", spoken(text)).group())  # type: ignore[union-attr]
            for text in await rows.all_inner_texts()
        ]
        address = page.url
        folded_refs = sorted({ref for _, ref, part, shown in hidden if not shown and not part})
        # A pending approval is hidden as well, and the count says so.
        await toggle.click()
        await pressing(scope, refs["battery"])
        await toggle.click()
        await expect(rows).to_have_count(len(runs_pending), timeout=SLOW)
        counts_with_pending = [
            int(re.search(r"\d+", spoken(text)).group())  # type: ignore[union-attr]
            for text in await rows.all_inner_texts()
        ]
        facts_now = await facts_of(scope)
        # The fold shows its lines again, and the choice comes back with the address.
        await rows.first.locator("button.diff-fold").click()
        reopened = await every_line(scope)
        await open_review(w, f"plan:{number}", hide="approved")
        await mode_and_view(page, "whole", "unified")
        kept = await page.locator("button#review-hide-approved").get_attribute("aria-pressed")
    proof(
        "M53",
        "Hide approved is a choice on the review page of a plan that has an approved item, off "
        "until it is pressed and kept in the address (hide=approved); with it on, the lines of "
        "the approved items, the ones carried from an earlier request and the one that waits to "
        "be sent, are folded into one row for each run of them that says how many it holds "
        "(never a line left out without saying so), the lines are the same lines as before "
        "(every sign, item and part is still there, folded), the counts add up to the approved "
        "items, the progress fact counts both kinds, and a fold shows its lines again",
        holds(
            off == "false",
            len(shown_before) == len(plan["tree"]),
            facts.get("review") == f"{len(carried)} of {len(plan['tree'])} approved",
            [(s, r, p) for s, r, p, _ in hidden] == [(s, r, p) for s, r, p, _ in unhidden],
            folded_refs == carried,
            counts == runs_carried,
            sum(counts) == len(carried),
            "hide=approved" in address,
            counts_with_pending == runs_pending,
            sum(counts_with_pending) == len(carried) + 1,
            facts_now.get("review") == f"{len(carried) + 1} of {len(plan['tree'])} approved",
            len([one for one in reopened if one[3]]) > len([one for one in hidden if one[3]]),
            kept == "true",
        ),
        f"{counts} then {counts_with_pending} hidden{failing()}",
    )


async def a_plan_at_revision_two(
    project: Any, owner: Any, change_all: bool = False
) -> tuple[int, dict[str, str], str]:
    """A plan that waits for a person at revision 2: the owner asked about the title, the details
    and a task she wants dropped and approved two items, and the agent revised, taking that task
    out and changing the two parts (and, with `change_all`, the size of every other item). Returns
    its number, its items and the epic it hangs under."""
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        sent = ask(
            project,
            owner,
            number,
            1,
            "Name the tyres, and the staff bikes do not need spare chains.",
            comments=[
                {"item": refs["tyres"], "field": "title", "text": "Say which tyres."},
                {"item": refs["fit"], "field": "body", "text": "How many hours is a bike off?"},
                {"item": refs["chains"], "text": "Staff bikes do not need spare chains."},
            ],
            approved=[refs["pick"], refs["order"]],
        )
        assert sent.status == 200, sent.body
        sizes: dict[str, dict[str, Any]] = {}
        if change_all:
            sizes = {key: {"size": "xl"} for key in ("covers", "battery", "pick", "order")}
        await ok(
            agent,
            "revise_plan",
            plan=number,
            note="Named the tyres, gave the hours and took out the spare chains.",
            items=revised(
                epic,
                refs,
                leave_out=("chains",),
                tyres={"title": "Studded tyres for the river path and the hill route"},
                fit={"body": "At the depot.\nFour hours each, from 8 until noon."},
                **sizes,
            ),
        )
        return number, refs, epic


async def pending_verdicts_are_lost_when_a_newer_revision_arrives() -> None:
    """Approvals and comments that were not sent are about the revision the person was reading:
    when the agent sends a newer one they are not kept, and the page says so with the numbers; the
    person then asks for changes with approvals only, which is allowed."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await open_review(w, f"plan:{number}")
        scope = review_page(page)
        await counted(scope, 7)
        await pressing(scope, refs["pick"])
        await pressing(scope, refs["order"])
        await pending_comment(scope, refs["fit"], "Hours, please.", "Details")
        waiting = spoken(await scope.locator("span.decision-state").first.inner_text())
        async with connect(project.mcp_url, project.key) as agent:
            await ok(
                agent,
                "revise_plan",
                plan=number,
                note="Added a size.",
                items=revised(epic, refs, tyres={"size": "xl"}),
            )
        await expect(scope.locator("[data-fact='revision']")).to_have_text(
            "Revision 2", timeout=SLOW
        )
        lost = scope.locator("p.change-lost")
        await expect(lost).to_be_visible(timeout=SLOW)
        lost_words = spoken(await lost.inner_text())
        everything = await lines(scope)
        still = await scope.locator(".diff-comment-pending").count()
        after = await facts_of(scope)
        state = scope.locator("span.decision-state")
        bar = spoken(await state.first.inner_text()) if await state.count() else ""
        # Approvals alone are a request: the dialog lists them and no comments.
        await mode_and_view(page, "whole", "unified")
        await pressing(scope, refs["covers"])
        await pressing(scope, refs["battery"])
        await scope.locator("button.verdict[data-verb='changes']").click()
        dialog = page.locator("#changes-dialog")
        await expect(dialog).to_be_visible(timeout=SLOW)
        listed = spoken(await dialog.locator("ul.changes-approved").inner_text())
        no_comments = await dialog.locator("ul.changes-comments li").count()
        await dialog.locator("textarea#changes-summary").fill("These two are fine, the rest not.")
        await dialog.locator("button[type=submit]").click()
        await expect(dialog).to_have_count(0, timeout=SLOW)
        await the_needs_page(w)
        await expect(page.locator(f"li.waiting-row[data-plan='{number}']")).to_have_count(
            1, timeout=SLOW
        )
        async with connect(project.mcp_url, project.key) as agent:
            sent = await read(agent, number)
    (given,) = sent["feedback"]
    proof(
        "M53",
        "approvals and comments that were not sent are lost when a newer revision arrives under a "
        "person: the page says once that revision 2 arrived and how many approvals and comments "
        "were not kept, keeps none pending (no approve button pressed, no comment, nothing in the "
        "bar or the progress fact), and the server never heard of them; the person then asks "
        "for changes with two approvals and no comment, the dialog lists the two approved items "
        "and no comments, and the feedback on the server is exactly those two",
        holds(
            "2 approved" in waiting and "1 unsent" in waiting,
            "Revision 2" in lost_words,
            "2" in lost_words and "approv" in lost_words.lower(),
            "comment" in lost_words.lower(),
            still == 0,
            all(
                v["pressed"] in (None, "false") and v["state"] is None for v in everything.values()
            ),
            "review" not in after,
            "approved" not in bar,
            all(code_of(refs[key]) in listed for key in ("covers", "battery")),
            no_comments == 0,
            given["approved"] == in_order([refs["covers"], refs["battery"]]),
            given["comments"] == [],
            given["revision"] == 2,
            sent["state"] == "changes_requested",
        ),
        f"{clip(lost_words, 60)}{failing()}",
    )


async def removed_lines_take_a_verdict() -> None:
    """The red line of a task a revision took out can be agreed to (a button named for it) or
    commented on to ask for it back (the box offers only the whole item), and neither is counted
    among the revision's own items; both are sent and recorded like any other line's."""
    owner = register()
    project = make_project(owner)
    number, refs, _ = await a_plan_at_revision_two(project, owner)
    async with connect(project.mcp_url, project.key) as agent:
        plan = await read(agent, number)
    gone = refs["chains"]
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await open_review(w, f"plan:{number}")
        scope = review_page(page)
        await expect(scope.locator(".change-diff .diff-line[data-sign='-']").first).to_be_visible(
            timeout=SLOW
        )
        red = (await lines(scope))[gone]
        before = await facts_of(scope)
        await pressing(scope, gone)
        agreed = (await lines(scope))[gone]
        after_agree = await facts_of(scope)
        await scope.locator(f"button.diff-comment[data-ref='{gone}']").first.click()
        form = scope.locator(f"form.comment-form[data-ref='{gone}']")
        await expect(form).to_be_visible(timeout=SLOW)
        offered = await form.locator("select.comment-field option").all_inner_texts()
        await form.locator("textarea.comment-text").fill("Put it back after all.")
        await form.locator("button[type=submit]").click()
        await expect(scope.locator(f".diff-comment-pending[data-ref='{gone}']")).to_have_count(
            1, timeout=SLOW
        )
        turned_off = (await lines(scope))[gone]
        await scope.locator("button.verdict[data-verb='changes']").click()
        dialog = page.locator("#changes-dialog")
        await expect(dialog).to_be_visible(timeout=SLOW)
        await dialog.locator("textarea#changes-summary").fill("I want the chains back.")
        await dialog.locator("button[type=submit]").click()
        await expect(dialog).to_have_count(0, timeout=SLOW)
        async with connect(project.mcp_url, project.key) as agent:
            sent = await read(agent, number)
    (_, given) = sent["feedback"]
    proof(
        "M53",
        "the red line of the task revision 2 took out has an approve button named for agreeing "
        "with the removal and a comment button; pressing approve marks the line as approved "
        "(pending) without counting it among the revision's own items; its comment box offers "
        "only the whole item; a comment turns the agreement off like on any line; and the "
        "request sends the comment on the removed task with the task's title as it was, which the "
        "server records",
        holds(
            red["sign"] == "-",
            red["label"] == f"Agree with removing {code_of(gone)}",
            red["comment"] == f"Comment on {code_of(gone)}",
            (agreed["pressed"], agreed["state"], agreed["pending"]) == ("true", "approved", True),
            before.get("review") == "2 of 6 approved",
            after_agree.get("review") == "2 of 6 approved",
            len(offered) == 1 and "whole item" in offered[0].lower(),
            (turned_off["pressed"], turned_off["state"]) == ("false", None),
            given["approved"] == [],
            [(c["item"], c["field"], c["quoted"], c["text"]) for c in given["comments"]]
            == [(gone, None, "Buy spare chains for staff bikes", "Put it back after all.")],
            plan["state"] == "proposed",
        ),
        f"{code_of(gone)} agreed to, then asked for back{failing()}",
    )


async def lines_of_sign(scope: Locator, sign: str) -> list[str]:
    """The items whose line has this sign, in the order drawn (a part line names no item)."""
    found: list[str] = await scope.locator(
        f".change-diff .diff-line[data-sign='{sign}'][data-ref]:not([data-part])"
    ).evaluate_all("els => els.map(e => e.dataset.ref)")
    return found


async def stories_in(agent: Any, parent: str, titles: list[str]) -> list[str]:
    """Stories made under an epic, by title."""
    return [
        (await ok(agent, "create_node", kind="story", title=title, parent=parent))["ref"]
        for title in titles
    ]


async def three_moves(project: Any) -> tuple[list[int], dict[str, list[str]], str, str]:
    """An epic with nothing in it and another with seven loose stories, and three proposals to
    move stories from the second: three of them, three more, and one. Returns the proposals'
    numbers, the stories of each by name, and the two epics."""
    async with connect(project.mcp_url, project.key) as agent:
        checkout = (await ok(agent, "create_node", kind="epic", title="Checkout"))["ref"]
        misc = (await ok(agent, "create_node", kind="epic", title="Miscellaneous"))["ref"]
        made = {
            "part": await stories_in(
                agent,
                misc,
                [
                    "Show the deposit on the receipt",
                    "Let support refund a single ride",
                    "Retry a payment the provider turned down",
                ],
            ),
            "none": await stories_in(
                agent,
                misc,
                [
                    "Pay with a saved card",
                    "Split a ride between two people",
                    "Print an invoice for the company",
                ],
            ),
            "all": await stories_in(agent, misc, ["Send the receipt by email"]),
        }
        numbers = [
            (
                await ok(
                    agent,
                    "propose_placement",
                    items=made[name],
                    parent=checkout,
                    reason="They belong with the checkout.",
                )
            )["id"]
            for name in ("part", "none", "all")
        ]
    return numbers, made, checkout, misc


async def a_move_is_accepted_in_part_on_the_page() -> None:
    """On the review page of a suggested move every moved item's new line has a control that is
    pressed while the item moves; leaving one out is kept in the page (and on the list's card),
    the bar says how many move, Approve then moves only those and the page says which item stayed
    where it is and who left it out; with every item left out there is nothing to approve (and
    the reason is in the hover card), with all of them in the proposal is accepted whole."""
    owner = register()
    project = make_project(owner)
    numbers, made, checkout, misc = await three_moves(project)
    part, none, whole = numbers
    stays = made["part"][1]
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await open_review(w, f"placement:{part}")
        scope = review_page(page)
        controls = await scope.locator("button.diff-include").evaluate_all(
            "els => els.map(e => [e.dataset.ref, e.getAttribute('aria-pressed'),"
            " e.getAttribute('aria-label')])"
        )
        plus_lines = await lines_of_sign(scope, "+")
        bar_all = await scope.locator("span.decision-state").count()
        await scope.locator(f"button.diff-include[data-ref='{stays}']").first.click()
        left = scope.locator(f"button.diff-include[data-ref='{stays}']").first
        await expect(left).to_have_attribute("aria-pressed", "false", timeout=SLOW)
        label_left = await left.get_attribute("aria-label")
        bar = spoken(await scope.locator("span.decision-state").first.inner_text())
        old_line = scope.locator(f".diff-line[data-sign='-'][data-ref='{stays}']").first
        quiet_old = await old_line.get_attribute("data-included")
        # The choice is in the page's memory: leaving for the list and coming back keeps it, and
        # the card on the list shows it too (a page load would forget it, as it forgets comments).
        await page.evaluate("location.hash = '#/needs'")
        card = page.locator(f"li.needs-row[data-change='placement:{part}']")
        await expect(card).to_have_count(1, timeout=SLOW)
        await expect(card.locator("button.diff-include")).to_have_count(3, timeout=SLOW)
        on_the_card = await card.locator("button.diff-include").evaluate_all(
            "els => els.map(e => [e.dataset.ref, e.getAttribute('aria-pressed')])"
        )
        await page.evaluate(f"location.hash = '#/review?change=placement:{part}'")
        await expect(review_page(page)).to_have_attribute(
            "data-change", f"placement:{part}", timeout=SLOW
        )
        kept = await scope.locator(f"button.diff-include[data-ref='{stays}']").first.get_attribute(
            "aria-pressed"
        )
        # Approve on the card sends only the items that are included.
        await page.evaluate("location.hash = '#/needs'")
        await expect(card).to_have_count(1, timeout=SLOW)
        await card.locator("button.verdict[data-verb='approve']").click()
        told = page.locator("#needs-said .needs-said")
        await expect(told).to_contain_text(f"{code_of(stays)} left where it is", timeout=SLOW)
        said = spoken(await told.inner_text())
        async with connect(project.mcp_url, project.key) as agent:
            done = (await ok(agent, "list_placements", status="accepted"))["items"]
            parents = {
                ref: (await ok(agent, "get_node", node=ref))["parent"] for ref in made["part"]
            }
        # Every item left out: nothing to approve, and the reason is in the hover card.
        await open_review(w, f"placement:{none}")
        for ref in made["none"]:
            await scope.locator(f"button.diff-include[data-ref='{ref}']").first.click()
        approve = scope.locator("button.verdict[data-verb='approve']")
        await expect(approve).to_be_disabled(timeout=SLOW)
        reject = scope.locator("button.verdict[data-verb='reject']")
        nothing = await hovered(
            page, scope.locator("span.verdict-holder:has(button.verdict[data-verb='approve'])")
        )
        reject_works = await reject.is_enabled()
        await scope.locator(f"button.diff-include[data-ref='{made['none'][0]}']").first.click()
        await expect(approve).to_be_enabled(timeout=SLOW)
        # Nothing left out: the proposal is accepted whole.
        await open_review(w, f"placement:{whole}")
        await scope.locator("button.verdict[data-verb='approve']").click()
        decided: dict[int, Any] = {}
        for _ in range(60):
            async with connect(project.mcp_url, project.key) as agent:
                decided = {
                    p["id"]: p
                    for p in (await ok(agent, "list_placements", status="accepted"))["items"]
                }
                still = (await ok(agent, "list_placements", status="proposed"))["items"]
            if whole in decided:
                break
            await asyncio.sleep(0.5)
    (in_part,) = [p for p in done if p["id"] == part]
    all_in = decided.get(whole, {"applied": None, "skipped": None})
    proof(
        "M53",
        "on the review page of a suggested move every moved item's new line has a control "
        "(pressed, named 'Move S-n') and the bar says nothing about it while every item moves; "
        "pressing one leaves that item out (named 'Leave S-n where it is', its old line quieter, "
        "the bar says '2 of 3 move'), the choice is kept in the page when it is left and shows "
        "on the list's card; Approve accepts the proposal naming only the included items, the "
        "page says which item stayed where it is and who left it out (the server's words), the "
        "server moved only those and says the same; with every item left out Approve is "
        "disabled (its hover card says there is nothing to move) and Reject is not, and one "
        "included again enables it; a proposal with every item included is accepted whole",
        holds(
            sorted(ref for ref, *_ in controls) == sorted(made["part"]),
            all(
                pressed == "true" and label == f"Move {code_of(ref)}"
                for ref, pressed, label in controls
            ),
            sorted(plus_lines) == sorted(made["part"]),
            bar_all == 0,
            label_left == f"Leave {code_of(stays)} where it is",
            bar == "2 of 3 move",
            quiet_old == "false",
            [ref for ref, pressed in on_the_card if pressed == "false"] == [stays],
            kept == "false",
            f"{code_of(stays)} left where it is" in said,
            owner.name in said,
            in_part["applied"] == [r for r in made["part"] if r != stays],
            in_part["skipped"] == [{"ref": stays, "why": f"{owner.name} left it out"}],
            parents[stays] == misc,
            all(parents[r] == checkout for r in made["part"] if r != stays),
            "nothing" in nothing.lower(),
            reject_works,
            all_in["skipped"] == [],
            all_in["applied"] == made["all"],
            [p["id"] for p in still] == [none],
        ),
        f"{bar}; {clip(said, 70)}; card {on_the_card}{failing()}",
    )


CARD_LINES = (
    """els => els.filter(e => e.checkVisibility()).map(e => [e.dataset.sign, e.dataset.ref])"""
)
# The item lines of a diff or of a card's preview of one (a card has no `.change-diff`).
PREVIEW_ITEMS = ".diff-line[data-ref]:not([data-part]):not([data-sign='.'])"


async def preview_of(scope: Locator) -> dict[str, Any]:
    """What a diff shows at the top: the hunk label, the item lines on show (sign and item, parts
    left out) and what the folds say."""
    hunk = scope.locator(".diff-hunk").first
    folds = scope.locator("button.diff-fold")
    found: list[list[str]] = await scope.locator(PREVIEW_ITEMS).evaluate_all(CARD_LINES)
    return {
        "hunk": spoken(await hunk.inner_text()) if await hunk.count() else "",
        "lines": [(sign, ref) for sign, ref in found],
        "folds": [spoken(text) for text in await folds.all_inner_texts()],
    }


async def a_revised_plan_previews_what_changed_on_its_card() -> None:
    """The card of a plan that was revised does not draw the whole plan as added lines: it says
    which revision it is and previews what changed, in the same order and with the same signs as
    the review page's Changes read (the red line of what was taken out among them), at most six
    lines and then one fold that says how much is behind it; the card of a plan at revision 1
    previews the plan as it always did."""
    owner = register()
    project = make_project(owner)
    number, refs, _ = await a_plan_at_revision_two(project, owner, change_all=True)
    async with connect(project.mcp_url, project.key) as agent:
        other, other_refs = await drafted(agent, await an_epic(agent), "Another plan")
        plan = await read(agent, number)
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_needs_page(w)
        revised_card = plan_card(page, number)
        first_card = plan_card(page, other)
        await expect(revised_card.locator("[data-fact='revision']")).to_have_text(
            "Revision 2", timeout=SLOW
        )
        await expect(first_card.locator(PREVIEW_ITEMS).first).to_be_visible(timeout=SLOW)
        await expect(revised_card.locator(PREVIEW_ITEMS).first).to_be_visible(timeout=SLOW)
        await expect(revised_card.locator(".diff-hunk").first).to_be_visible(timeout=SLOW)
        await expect(revised_card.locator("button.diff-fold").first).to_be_visible(timeout=SLOW)
        on_the_card = await preview_of(revised_card)
        at_first = await preview_of(first_card)
        await open_review(w, f"plan:{number}")
        on_the_page = await preview_of(review_page(page))
    changes = {c["ref"]: c["change"] for c in plan["changes"]}
    signs = {"changed": "changed", "added": "+", "removed": "-"}
    wanted = [(signs[changes[ref]], ref) for ref in in_order(changes)]
    proof(
        "M53",
        "the card of a plan at revision 2 says which revision it is and previews what changed "
        "instead of the whole plan as added lines: its hunk label is the review page's, its lines "
        "are the lines of the page's Changes read in the same order with the same signs (changed "
        "and the one taken out, which is red), at most six of them, with one fold that says how "
        "much is behind it; the card of a plan at revision 1 previews the plan as added lines",
        holds(
            on_the_card["hunk"] == on_the_page["hunk"],
            on_the_card["hunk"].startswith("Revision 2"),
            on_the_page["lines"] == wanted,
            len(wanted) == 7,
            on_the_card["lines"] == wanted[:6],
            any(sign == "-" for sign, _ in on_the_card["lines"]),
            len(on_the_card["folds"]) == 1 and "1" in on_the_card["folds"][0],
            at_first["lines"]
            and all(sign == "+" for sign, _ in at_first["lines"])
            and len(at_first["lines"]) <= 6,
            sorted(ref for _, ref in at_first["lines"])
            == sorted(other_refs.values())[: len(at_first["lines"])]
            or len(at_first["lines"]) == 6,
        ),
        f"{on_the_card['hunk']}: {len(on_the_card['lines'])} lines, folds {on_the_card['folds']}"
        f"{failing()}",
    )


WRITING = (
    "button.diff-approve, button.diff-comment, button.diff-include, button.verdict, "
    "button.decision-comment, form.comment-form, .diff-comment-pending"
)


async def a_viewer_reads_the_states_and_has_no_control() -> None:
    """A viewer sees what is carried on every line, the blame chips, the progress fact and Hide
    approved, and none of the controls that approve, ask for a change, include a moved item or
    decide, on the list's card and on the review page of a plan and of a move; the blame chip
    still reads the plan as of its revision."""
    found = await a_conversation()
    viewer = join_project(found.project, "viewer")
    number, refs = found.number, found.refs
    rev3 = found.reads["rev3"]
    other = register()
    second = make_project(other)
    moves, made, *_ = await three_moves(second)
    seer = join_project(second, "viewer")
    async with browser_page(found.project.address, token=None, person=viewer, name=None) as w:
        page = w.page
        await the_needs_page(w)
        card = plan_card(page, number)
        await expect(card.locator("[data-fact='revision']")).to_have_text(
            "Revision 3", timeout=SLOW
        )
        on_the_card = await page.locator(WRITING).count()
        await open_review(w, f"plan:{number}")
        scope = review_page(page)
        await mode_and_view(page, "whole", "unified")
        shown = await lines(scope)
        facts = await facts_of(scope)
        controls = await page.locator(WRITING).count()
        toggle = page.locator("button#review-hide-approved")
        await expect(toggle).to_have_count(1, timeout=SLOW)
        await toggle.click()
        rows = scope.locator(".diff-foldrow[data-fold='approved']")
        await expect(rows).to_have_count(3, timeout=SLOW)
        folds = await rows.count()
        await toggle.click()
        await scope.locator(f"button.blame[data-ref='{refs['tyres']}']").first.click()
        await expect(page).to_have_url(re.compile(r"revision=2"), timeout=SLOW)
        await expect(page.locator("#review-diff[data-revision='2']")).to_have_count(1, timeout=SLOW)
        reads_two = await page.locator("#review-diff[data-revision='2']").count()
    async with browser_page(second.address, token=None, person=seer, name=None) as w:
        page = w.page
        await open_review(w, f"placement:{moves[0]}")
        move_page = review_page(page)
        await expect(move_page.locator(".diff-line[data-sign='+']").first).to_be_visible(
            timeout=SLOW
        )
        move_controls = await page.locator(WRITING).count()
        move_lines = len(await lines_of_sign(move_page, "+"))
    proof(
        "M53",
        "a viewer reads all of it and changes none of it: on the list's card and on the review "
        "page of a plan the carried state of every line and its blame chip are what the server's "
        "blame says, the progress fact is there and Hide approved works (a choice of what to "
        "read), pressing a blame chip reads the plan as of that revision, and there is no button "
        "that approves, comments, decides or includes anywhere; on the review page of a move the "
        "lines are there and no include control",
        holds(
            on_the_card == 0,
            not disagreements(shown, rev3, "viewer"),
            len(shown) == len(rev3["tree"]),
            all(line["revision"] is not None for line in shown.values()),
            all(line["pressed"] is None and line["comment"] is None for line in shown.values()),
            "review" in facts,
            controls == 0,
            folds == 3,
            reads_two == 1,
            move_controls == 0,
            move_lines == 3,
        ),
        f"{len(shown)} lines, {controls + on_the_card + move_controls} controls{failing()}",
    )


async def pages_have_no_button_in_a_button_and_no_sentence() -> None:
    """With the new controls on the page (pending approvals and comments, Hide approved, the
    blame chips, the dialog that lists what is sent, a move with an item left out) no button or
    button-like element holds another, and the sentence scan finds no text of six words or more
    that is not somebody's own words, a refusal or a hover card, in any of those states, in
    either layout."""
    found = await a_conversation()
    number, refs = found.number, found.refs
    moves, made, *_ = await three_moves(found.project)
    nested: list[str] = []
    said: list[str] = []
    places = 0

    async def look(page: Page, where: str) -> None:
        nonlocal places
        places += 1
        nested.extend(f"{where}: {one}" for one in await page.evaluate(NESTED))
        said.extend(f"{where}: {one}" for one in await sentences(page))

    async with browser_page(found.project.address, token=found.project.key, name=None) as w:
        page = w.page
        await the_needs_page(w)
        await expect(plan_card(page, number).locator(PREVIEW_ITEMS).first).to_be_visible(
            timeout=SLOW
        )
        await expect(page.locator("li.needs-row button.diff-include").first).to_be_visible(
            timeout=SLOW
        )
        await look(page, "the list")
        await open_review(w, f"plan:{number}")
        scope = review_page(page)
        for mode in ("whole", "delta"):
            for view in ("unified", "split"):
                await mode_and_view(page, mode, view)
                if mode == "whole":
                    await pressing(scope, refs["battery"])
                    await pending_comment(scope, refs["covers"], "Still not sure.", "Size")
                await look(page, f"the review page, {mode}, {view}")
                if mode == "whole":
                    await page.locator("button#review-hide-approved").click()
                    await look(page, f"hidden approved, {mode}, {view}")
                    await page.locator("button#review-hide-approved").click()
        await mode_and_view(page, "whole", "unified")
        await scope.locator("button.verdict[data-verb='changes']").click()
        await expect(page.locator("#changes-dialog")).to_be_visible(timeout=SLOW)
        await look(page, "the dialog that sends")
        await page.locator("#changes-dialog").get_by_role("button", name="Cancel").click()
        await open_review(w, f"placement:{moves[0]}")
        await scope.locator(f"button.diff-include[data-ref='{made['part'][0]}']").first.click()
        for view in ("unified", "split"):
            await page.locator(f"#review-view button[data-view='{view}']").click()
            await expect(page.locator(f".change-diff[data-view='{view}']")).to_have_count(
                1, timeout=SLOW
            )
            await look(page, f"a move with one item left out, {view}")
    proof(
        "M53",
        f"in {places} states of the new controls (the list's card with its include controls, the "
        "review page of a plan with a pending approval and comment in each read and layout, Hide "
        "approved, the dialog that sends and a move with an item left out in each layout) no "
        "button or button-like element holds another (the approve, comment, include and blame "
        "buttons are siblings of their line) and the sentence scan finds no text of six words or "
        "more that is not somebody's own words, a refusal or a hover card",
        holds(not nested, not said, places >= 9),
        f"{places} states"
        + (f"; nested {nested[:3]}" if nested else "")
        + (f"; sentences {sorted({one.split(': ', 1)[1] for one in said})[:4]}" if said else "")
        + failing(),
    )


async def the_notice_of_what_a_revision_did_not_keep_is_no_sentence() -> None:
    """When the agent sends a newer revision under a person who had approvals and a comment that
    were not sent, the page says once that it arrived and how many of each were not kept: as
    facts, with no text of six words or more that is not somebody's own words (the notice is one
    more state of the page the sentence scan reads), and with no button in a button."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        small, small_refs = await drafted(agent, epic, "A plan to lose its pending verdicts")
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await open_review(w, f"plan:{small}")
        scope = review_page(page)
        await counted(scope, 7)
        await pressing(scope, small_refs["pick"])
        await pressing(scope, small_refs["order"])
        await pending_comment(scope, small_refs["fit"], "Hours, please.", "Details")
        async with connect(project.mcp_url, project.key) as agent:
            await ok(
                agent,
                "revise_plan",
                plan=small,
                note="Added a size.",
                items=revised(epic, small_refs, tyres={"size": "xl"}),
            )
        await expect(scope.locator("p.change-lost")).to_be_visible(timeout=SLOW)
        notice = spoken(await scope.locator("p.change-lost").inner_text())
        nested = await page.evaluate(NESTED)
        said = await sentences(page)
    proof(
        "M53",
        "the notice of what a newer revision did not keep (revision 2 arrived, how many unsent "
        "approvals and comments were not kept) is no sentence: the sentence scan finds no text "
        "of six words or more on the page while it is drawn, and no button holds another",
        holds("Revision 2" in notice, not nested, not said),
        f"{notice}"
        + (f"; sentences {sorted(said)[:3]}" if said else "")
        + (f"; nested {nested[:3]}" if nested else "")
        + failing(),
    )


async def narrow_windows_and_both_themes_read_the_new_controls() -> None:
    """In the light and the dark theme, at 1280 px and at 390 px, the review page of a plan with a
    pending approval and Hide approved on, the list's card of a plan, and the review page of a
    move with an item left out have no sideways scroll, every approve, include and blame button
    is inside the window and has a name, and the chips and facts they draw read at 4.5 to 1 or
    more."""
    found = await a_conversation()
    number, refs = found.number, found.refs
    moves, made, *_ = await three_moves(found.project)
    wrong: list[str] = []
    drawn = 0
    async with browser_page(found.project.address, token=found.project.key, name=None) as w:
        page = w.page
        await the_needs_page(w)
        for theme in ("light", "dark"):
            await page.evaluate(f"localStorage.setItem('vrum.theme', '{theme}')")
            for size in (WIDE, NARROW):
                await page.set_viewport_size(size)  # type: ignore[arg-type]
                for where in ("card", "plan", "move"):
                    if where == "card":
                        await the_needs_page(w)
                        await expect(
                            plan_card(page, number).locator(PREVIEW_ITEMS).first
                        ).to_be_visible(timeout=SLOW)
                    elif where == "plan":
                        await open_review(w, f"plan:{number}", hide="approved")
                        await mode_and_view(page, "whole", "unified")
                        await pressing(review_page(page), refs["battery"])
                    else:
                        await open_review(w, f"placement:{moves[0]}")
                        await expect(page.locator("button.diff-include").first).to_be_visible(
                            timeout=SLOW
                        )
                        await page.locator("button.diff-include").first.click()
                    drawn += 1
                    where_it = f"{theme} {size['width']} px {where}"
                    applied = await page.evaluate("document.documentElement.dataset.theme")
                    if applied != theme:
                        wrong.append(f"{where_it}: theme {applied}")
                    if await overflows(page):
                        wrong.append(f"{where_it}: sideways scroll")
                    controls = await page.locator(
                        "button.diff-approve, button.diff-include, button.blame, "
                        "button#review-hide-approved, button.diff-comment"
                    ).evaluate_all(
                        "(els, w) => els.filter(e => e.checkVisibility()).map(e => {"
                        " const r = e.getBoundingClientRect();"
                        " const name = e.getAttribute('aria-label') || e.textContent.trim();"
                        " return [name, r.left, r.right, w]; })",
                        size["width"],
                    )
                    wrong += [
                        f"{where_it}: {n or 'a button'} outside"
                        for n, left, right, wide in controls
                        if left < -1 or right > wide + 1
                    ]
                    wrong += [f"{where_it}: a button without a name" for n, *_ in controls if not n]
                    low = await page.evaluate(
                        CONTRAST,
                        "button.blame, .line-review, .change-facts li, .decision-state, "
                        ".diff-foldrow button",
                    )
                    wrong += [f"{where_it}: {t!r} {c:.1f}" for t, c in low if c < 4.5]
    proof(
        "M53",
        "in the light and the dark theme, at 1280 px and at 390 px, the list's card of a plan, the "
        "review page of a plan with Hide approved on and the review page of a move with an item "
        "left out have no sideways scroll, every approve, include, comment, blame and Hide "
        "approved button that is on show is inside the window and has a name, and the text of the "
        "chips, facts and folds they draw reads at 4.5 to 1 or more",
        not wrong and drawn == 12,
        f"{drawn} pages" + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )


async def the_history_says_what_was_approved_and_what_stayed() -> None:
    """The Activity page says in words what a person approved with a request for changes (how many
    comments and how many items) and which items of a move were left where they are and who left
    them out, as the tools' own events say it."""
    found = await a_conversation()
    owner = found.owner
    moves, made, checkout, misc = await three_moves(found.project)
    stays = made["part"][1]
    in_part = person(
        found.project,
        owner,
        "decide_placement",
        placement=moves[0],
        accepted=True,
        items=[made["part"][0], made["part"][2]],
    )
    assert in_part.status == 200, in_part.body
    async with connect(found.project.mcp_url, found.project.key) as agent:
        feed = (await ok(agent, "changes_since", limit=200))["items"]
    asked = [e for e in feed if e["action"] == "plan_changes_requested"]
    (accepted,) = [e for e in feed if e["action"] == "placement_accepted"]
    async with browser_page(found.project.address, token=found.project.key, name=None) as w:
        page = w.page
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
    words: list[str] = []
    for event in asked:
        after = event["after"]
        counts = []
        if after["comments"]:
            n = len(after["comments"])
            counts.append(f"{n} comment{'s' if n != 1 else ''}")
        if after["approved"]:
            counts.append(f"{len(after['approved'])} approved")
        words.append(
            f"asked for changes to revision {after['revision']} of Plan {after['plan']}: "
            f"{after['summary'].rstrip('.')} ({', '.join(counts)})"
        )
    proof(
        "M53",
        "the history says what a request for changes carried and what a move left alone, in plain "
        "words: each request names its revision, its plan, its summary and how many comments and "
        "how many approved items it had (a count of nothing is left out), and the move accepted "
        "in part says how many moved and how many were left out, which item stayed where it is "
        "and who left it out, as the events of the change feed say it",
        holds(
            len(asked) == 2,
            all(spoken(one) in history for one in words),
            "(4 comments, 2 approved)" in history,
            "(1 comment, 3 approved)" in history,
            f"accepted Proposal {moves[0]} (2 moved, 1 left out)" in history,
            re.search(
                rf"left \w+ {code_of(stays)} “[^”]+” where it is "
                rf"\({re.escape(owner.name)} left it out\)",
                history,
            )
            is not None,
            [one["ref"] for one in accepted["after"]["skipped"]] == [stays],
        ),
        f"{clip(words[0], 90)}{failing()}",
    )


async def granular_on_the_page() -> None:
    await verdicts_wait_in_the_page_until_they_are_sent()
    await every_line_carries_what_the_tools_say()
    await hide_approved_hides_exactly_the_approved_lines()
    await pending_verdicts_are_lost_when_a_newer_revision_arrives()
    await removed_lines_take_a_verdict()
    await a_move_is_accepted_in_part_on_the_page()
    await a_revised_plan_previews_what_changed_on_its_card()
    await a_viewer_reads_the_states_and_has_no_control()
    await pages_have_no_button_in_a_button_and_no_sentence()
    await narrow_windows_and_both_themes_read_the_new_controls()
    await the_history_says_what_was_approved_and_what_stayed()
    await the_notice_of_what_a_revision_did_not_keep_is_no_sentence()
