"""M53 on the board: approving and asking for a change line by line, and who changed each line.

A person approves an item of a drafted plan as it is, or asks for a change to it, from the control
beside its line; both wait in the page until Request changes sends them together (an item cannot be
both). The server carries an approval across revisions while the item is unchanged, so each line of
the next revision says Approved, Asked or Changed (and by whom), and a chip beside it says which
revision last changed it, like git's blame; pressing the chip reads the plan as of that revision.
Hide approved folds the approved lines, the card of a revised plan previews what changed (its red
lines too), and a suggested move can be accepted in part.

Driven in a real headless browser and compared with what a real MCP client reads (`list_plans`,
`get_node`, `list_placements`). Every test makes an account and a project of its own."""

from collections import Counter
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Locator, Page, expect

from tests.board_helpers import SLOW, browser_page, on_page, sentences
from tests.board_layout import SCREENS, Screen, blank_page, contrast, layout_context, settle
from tests.helpers import connect, ok
from tests.test_board_change_dialog_integration import nested_controls
from tests.test_board_needs_integration import no_overflow
from tests.test_board_plan_review_integration import (
    a_revision,
    code,
    in_card,
    open_needs,
    say_comment,
)
from tests.test_board_review_assist_integration import a_proposal
from tests.test_board_review_integration import card_of, item_lines, refs_of
from tests.test_checks_integration import as_person
from tests.test_placement_integration import a_sorted_project
from tests.test_plan_review_integration import drafted, read
from tests.world import Person, Project, join_project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

AGENT_NOTE = "Named the supplier, dropped the covers order and added a spare."


# -- the plan every test reads ---------------------------------------------------------------------


async def a_reviewed_plan(
    agent: Client, project: Project, owner: Person, title: str = "Winter plan"
) -> tuple[int, dict[str, str]]:
    """Revision 1 is reviewed: the epic, the first story and the second task are approved, the
    first task, the second story and the third task are asked about. The agent's revision 2 changes
    the first task, takes out the third and adds a spare. So, as of revision 2: e, s1 and t2 are
    approved (unchanged), t1 is changed, s2 is asked, t3 is removed and t4 is new with no word."""
    number, refs = await drafted(agent, title=title)
    reply = as_person(
        owner,
        project,
        "request_plan_changes",
        plan=number,
        revision=1,
        summary="Right apart from three things.",
        comments=[
            {"item": refs["t1"], "field": "title", "text": "Name the supplier."},
            {"item": refs["s2"], "field": "title", "text": "Which covers?"},
            {"item": refs["t3"], "text": "Drop this one."},
        ],
        approved=[refs["e"], refs["s1"], refs["t2"]],
    )
    assert reply.status == 200, reply.body
    sent = await a_revision(
        agent,
        number,
        refs,
        AGENT_NOTE,
        t1={"title": "Choose a supplier in Oslo"},
        without=["t3"],
        extra=[
            {
                "key": "t4",
                "kind": "task",
                "title": "Order spare covers",
                "parent": "s2",
                "size": "xs",
            }
        ],
    )
    refs["t4"] = sent["created"][0]
    return number, refs


def blames(served: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The server's blame of a read plan, by reference."""
    found: dict[str, dict[str, Any]] = {one["ref"]: one for one in served["blame"]}
    return found


async def hover_words(page: Page, target: Locator) -> str:
    """What the hover card of a control says: first leave no card open (a pointer that rests on a
    control that was just pressed, or the focus a redraw gave one, has opened its card), then rest
    the pointer on the control, read the one card that opens and move away."""
    await page.evaluate("() => document.activeElement && document.activeElement.blur()")
    await page.mouse.move(0, 0)
    await expect(page.locator(".hovercard:visible")).to_have_count(0)
    await target.hover()
    tip = page.locator(".hovercard:visible")
    await expect(tip).to_have_count(1)
    said = " ".join((await tip.text_content() or "").split())
    await page.mouse.move(0, 0)
    await expect(page.locator(".hovercard:visible")).to_have_count(0)
    return said


def recording(page: Page, tool: str) -> list[dict[str, Any]]:
    """Every call of a tool the page makes from now on, as the arguments it sent."""
    sent: list[dict[str, Any]] = []

    def note(request: Any) -> None:
        if request.method == "POST" and request.url.endswith(f"/tool/{tool}"):
            sent.append(request.post_data_json)

    page.on("request", note)
    return sent


MARKS = """(root) => Object.fromEntries(
  [...root.querySelectorAll('.diff-item-line[data-ref]')].map((line) => {
    const chip = line.querySelector('.line-review');
    const next = line.nextElementSibling;
    const slot = next && next.matches('.diff-comment-slot') ? next : null;
    const blame = slot ? slot.querySelector('button.blame') : null;
    return [line.dataset.ref, {
        sign: line.dataset.sign,
        state: chip ? chip.dataset.state : null,
        pending: chip ? (chip.dataset.pending ?? null) : null,
        word: chip ? chip.textContent.trim() : null,
        blame: blame ? blame.dataset.revision : null,
        blame_change: blame ? blame.dataset.change : null,
        blame_text: blame ? blame.textContent.trim() : null,
    }];
  }))"""


async def marks_of(scope: Locator) -> dict[str, dict[str, Any]]:
    """For each item line in the scope: its sign, the carried state at its end and the blame chip
    beside it."""
    found: dict[str, dict[str, Any]] = await scope.evaluate(MARKS)
    return found


WORDS = {"approved": "Approved", "asked": "Asked", "changed": "Changed"}


def marks_equal(marks: dict[str, dict[str, Any]], blame: dict[str, dict[str, Any]]) -> None:
    """Each line's state and blame chip are what the server's blame says of that item."""
    for ref, mark in marks.items():
        one = blame[ref]
        state = one["review"]["state"]
        assert mark["state"] == (None if state == "none" else state), (ref, mark, one)
        assert mark["word"] == (None if state == "none" else WORDS[state]), (ref, mark)
        assert mark["pending"] is None, (ref, mark)
        assert mark["blame"] == str(one["revision"]), (ref, mark, one)
        assert mark["blame_change"] == one["change"], (ref, mark, one)
        words = (
            f"removed in r{one['revision']}"
            if one["change"] == "removed"
            else f"r{one['revision']}"
        )
        assert mark["blame_text"] == words, (ref, mark)


async def review_at(w: Any, number: int, query: str = "") -> Locator:
    """Open a plan's review page by its address and give the page's root."""
    await w.open(f"#/review?change=plan:{number}{query}")
    root: Locator = w.page.locator("#review-page")
    await expect(root).to_be_visible(timeout=SLOW)
    # The lines are in the page when the diff is (a run hidden by Hide approved is not visible).
    await expect(root.locator("#review-diff")).to_be_visible(timeout=SLOW)
    await root.locator(".diff-item-line").first.wait_for(state="attached", timeout=SLOW)
    return root


async def open_needs_review(w: Any, number: int) -> Locator:
    """Press Open on the plan's card in the list, and give the review page's root."""
    await card_of(w.page, f"plan:{number}").locator("a.change-open").click()
    root: Locator = w.page.locator("#review-page")
    await expect(root).to_be_visible(timeout=SLOW)
    await expect(root.locator(".diff-item-line").first).to_be_visible(timeout=SLOW)
    return root


# -- approving a line ----------------------------------------------------------------------------


async def test_an_approval_waits_in_the_page_until_it_is_sent_and_never_goes_with_a_comment() -> (
    None
):
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent)
        page = await open_needs(w, number)
        review = in_card(page, number)
        await expect(item_lines(review)).to_have_count(6, timeout=SLOW)
        order = [one["ref"] for one in (await read(agent, number))["tree"]]

        # Every item line has an approve control beside it, none pressed, and nothing is said yet:
        # no carried state, no progress, no Hide approved, nothing waiting.
        assert await refs_of(review.locator("button.diff-approve")) == order
        await expect(review.locator("button.diff-approve[aria-pressed='true']")).to_have_count(0)
        await expect(review.locator("span.line-review")).to_have_count(0)
        await expect(review.locator("li[data-fact=review]")).to_have_count(0)
        await expect(review.locator("#review-hide-approved")).to_have_count(0)
        await expect(review.locator("span.decision-state")).to_have_count(0)
        approve = review.locator(f"button.diff-approve[data-ref='{refs['t1']}']")
        await expect(approve).to_have_attribute("aria-label", f"Approve {code(refs['t1'])}")
        assert await hover_words(page, approve) == "Approve as it is"
        # The control is beside the line, in its slot, not in it.
        slot = review.locator(f".diff-comment-slot[data-ref='{refs['t1']}']")
        await expect(slot.locator("button.diff-approve")).to_have_count(1)
        await expect(review.locator(".diff-line button.diff-approve")).to_have_count(0)

        # Pressing it approves the item as it is, in the page only.
        await approve.click()
        await expect(approve).to_have_attribute("aria-pressed", "true")
        chip = review.locator(f".diff-item-line[data-ref='{refs['t1']}'] span.line-review")
        await expect(chip).to_have_attribute("data-state", "approved")
        await expect(chip).to_have_attribute("data-pending", "true")
        await expect(chip).to_have_text("Approved")
        assert await hover_words(page, chip) == "Not sent yet"
        assert await hover_words(page, approve) == "Approved, not sent yet"
        await expect(review.locator("li[data-fact=review]")).to_have_text("1 of 6 approved")
        await expect(review.locator("span.decision-state")).to_have_text("1 approved")
        await expect(review.locator("#review-hide-approved")).to_have_attribute(
            "aria-pressed", "false"
        )
        assert (await read(agent, number))["feedback"] == []
        # Again, and it is back to nothing.
        await approve.click()
        await expect(approve).to_have_attribute("aria-pressed", "false")
        await expect(review.locator("span.line-review")).to_have_count(0)
        await expect(review.locator("li[data-fact=review]")).to_have_count(0)
        await expect(review.locator("span.decision-state")).to_have_count(0)

        # A comment takes the approval back, and while it waits the item cannot be approved.
        await approve.click()
        await say_comment(review, refs["t1"], "Name the supplier.")
        await expect(approve).to_have_attribute("aria-pressed", "false")
        await expect(approve).to_be_disabled()
        await expect(chip).to_have_count(0)
        assert await hover_words(page, approve) == "Has an unsent comment"
        await expect(review.locator("span.decision-state")).to_have_text("1 unsent")
        await review.locator(
            f"div.diff-comment-pending[data-ref='{refs['t1']}'] button.comment-remove"
        ).click()
        await expect(approve).to_be_enabled()
        # The other way round: a comment first, and the item cannot be approved.
        await say_comment(review, refs["t2"], "Why?")
        await expect(
            review.locator(f"button.diff-approve[data-ref='{refs['t2']}']")
        ).to_be_disabled()

        # What waits lives in the page: it survives drawing the page again and a trip to the
        # list (where the card says the same), and a reload forgets it.
        await approve.click()
        await review.locator("#review-view button[data-view=split]").click()
        await expect(
            review.locator(f"button.diff-approve[data-ref='{refs['t1']}']")
        ).to_have_attribute("aria-pressed", "true")
        await w.open("#/needs")
        await on_page(page, "needs")
        card = card_of(page, f"plan:{number}")
        await expect(card.locator("span.decision-state")).to_have_text("1 approved, 1 unsent")
        await expect(card.locator("li[data-fact=review]")).to_have_text("1 of 6 approved")
        await page.reload()
        await expect(card_of(page, f"plan:{number}")).to_be_visible(timeout=SLOW)
        await expect(card_of(page, f"plan:{number}").locator("span.decision-state")).to_have_count(
            0
        )
        await expect(card_of(page, f"plan:{number}").locator("li[data-fact=review]")).to_have_count(
            0
        )
        assert w.problems == []


# -- sending ----------------------------------------------------------------------------------


async def test_request_changes_sends_approvals_with_the_comments_and_the_server_keeps_both() -> (
    None
):
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent)
        page = await open_needs(w, number)
        review = in_card(page, number)
        await expect(item_lines(review)).to_have_count(6, timeout=SLOW)
        sent = recording(page, "request_plan_changes")
        for key in ("e", "s1", "t2"):
            await review.locator(f"button.diff-approve[data-ref='{refs[key]}']").click()
        await say_comment(review, refs["t1"], "Which supplier?", "Title")
        await say_comment(review, refs["t3"], "Why so small?")
        await expect(review.locator("span.decision-state")).to_have_text("3 approved, 2 unsent")
        await expect(review.locator("span.decision-state")).to_have_attribute("data-approved", "3")
        await expect(review.locator("span.decision-state")).to_have_attribute("data-unsent", "2")

        # The dialog lists what goes: the approved items and the comments, as labelled lists.
        await review.locator("button.verdict[data-verb=changes]").click()
        dialog = page.locator("#changes-dialog")
        await expect(dialog).to_be_visible()
        assert await refs_of(dialog.locator("ul.changes-approved li")) == [
            refs["e"],
            refs["s1"],
            refs["t2"],
        ]
        await expect(dialog.locator(".changes-approved-box > p")).to_have_text("Approved: 3")
        await expect(dialog.locator(".changes-comments-box > p")).to_have_text("Comments: 2")
        await expect(dialog.locator("ul.changes-approved li").first).to_contain_text("Winter fleet")
        assert await sentences(page, ["dialog[open]"]) == []
        # A request without a summary is refused in the server's words and sends nothing.
        await dialog.get_by_role("button", name="Ask for changes").click()
        await expect(dialog.locator("p.problem")).to_be_visible()
        assert (await read(agent, number))["feedback"] == []
        await dialog.locator("#changes-summary").fill("Two to look at.")
        await dialog.get_by_role("button", name="Ask for changes").click()
        await expect(dialog).to_have_count(0)

        # What the page sent is what it held, and the server kept it, by the signed-in person.
        assert sent[-1]["approved"] == [refs["e"], refs["s1"], refs["t2"]]
        assert [(c["item"], c.get("field"), c["text"]) for c in sent[-1]["comments"]] == [
            (refs["t1"], "title", "Which supplier?"),
            (refs["t3"], None, "Why so small?"),
        ]
        feedback = (await read(agent, number))["feedback"]
        assert len(feedback) == 1
        assert feedback[0]["approved"] == [refs["e"], refs["s1"], refs["t2"]]
        assert [(c["item"], c["field"], c["text"]) for c in feedback[0]["comments"]] == [
            (refs["t1"], "title", "Which supplier?"),
            (refs["t3"], None, "Why so small?"),
        ]
        assert (feedback[0]["by"], feedback[0]["by_kind"]) == ("Priya Raman", "person")
        feed = (await ok(agent, "changes_since", limit=200))["items"]
        (event,) = [e for e in feed if e["action"] == "plan_changes_requested"]
        assert event["after"]["approved"] == feedback[0]["approved"]
        # The plan leaves the list (it is the agent's turn) and nothing stays waiting in the page.
        await expect(card_of(page, f"plan:{number}")).to_have_count(0, timeout=SLOW)
        # The Activity page says it with the counts, and leaves out a count of nothing.
        await w.open("#/activity")
        await on_page(page, "activity")
        row = page.locator("li.event.action-plan_changes_requested .event-words").first
        await expect(row).to_have_text(
            f"asked for changes to revision 1 of Plan {number}: Two to look at "
            "(2 comments, 3 approved).",
            timeout=SLOW,
        )
        assert w.problems == []


async def test_a_request_may_be_only_approvals_and_approve_and_reject_ignore_what_waits() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent)
        spring, _ = await drafted(agent, title="Spring tyre check")
        page = await open_needs(w, number)
        review = in_card(page, number)
        await expect(item_lines(review)).to_have_count(6, timeout=SLOW)
        sent = recording(page, "request_plan_changes")
        await review.locator(f"button.diff-approve[data-ref='{refs['e']}']").click()
        await review.locator("button.verdict[data-verb=changes]").click()
        dialog = page.locator("#changes-dialog")
        await expect(dialog.locator(".changes-approved-box > p")).to_have_text("Approved: 1")
        await expect(dialog.locator(".changes-no-comments")).to_have_text("No comments")
        await dialog.locator("#changes-summary").fill("The rest is for you to decide.")
        await dialog.get_by_role("button", name="Ask for changes").click()
        await expect(dialog).to_have_count(0)
        assert sent[-1] == {
            "plan": number,
            "revision": 1,
            "summary": "The rest is for you to decide.",
            "comments": [],
            "approved": [refs["e"]],
        }
        feedback = (await read(agent, number))["feedback"][0]
        assert (feedback["approved"], feedback["comments"]) == ([refs["e"]], [])
        await w.open("#/activity")
        await on_page(page, "activity")
        await expect(
            page.locator("li.event.action-plan_changes_requested .event-words").first
        ).to_have_text(
            f"asked for changes to revision 1 of Plan {number}: The rest is for you to decide "
            "(1 approved).",
            timeout=SLOW,
        )

        # A pending approval is not a decision: Approve frees the plan as it did, and nothing was
        # recorded as feedback.
        second = await open_needs(w, spring)
        mine = in_card(second, spring)
        await expect(item_lines(mine)).to_have_count(6, timeout=SLOW)
        spring_refs = [one["ref"] for one in (await read(agent, spring))["tree"]]
        await mine.locator(f"button.diff-approve[data-ref='{spring_refs[0]}']").click()
        await mine.locator("button.verdict[data-verb=approve]").click()
        await expect(page.locator("#review-page")).to_have_count(0, timeout=SLOW)
        decided = await read(agent, spring)
        assert decided["state"] == "approved" and decided["feedback"] == []
        assert w.problems == []


# -- carried from earlier, and who changed each line -----------------------------------------------


async def test_every_line_says_what_is_carried_and_who_last_changed_it_in_every_read() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await a_reviewed_plan(agent, project, owner)
        served = await read(agent, number)
        blame = blames(served)
        # The server: approved where nothing changed since, changed where it did, asked where it
        # did not, nothing for the new item and nothing for the one that was taken out (the word
        # about it was on the revision before the one that removed it).
        states = {ref: one["review"]["state"] for ref, one in blame.items()}
        assert states == {
            refs["e"]: "approved",
            refs["s1"]: "approved",
            refs["t1"]: "changed",
            refs["t2"]: "approved",
            refs["s2"]: "asked",
            refs["t4"]: "none",
            refs["t3"]: "none",
        }
        assert {ref: (one["change"], one["revision"]) for ref, one in blame.items()} == {
            refs["e"]: ("added", 1),
            refs["s1"]: ("added", 1),
            refs["t1"]: ("changed", 2),
            refs["t2"]: ("added", 1),
            refs["s2"]: ("added", 1),
            refs["t4"]: ("added", 2),
            refs["t3"]: ("removed", 2),
        }
        page = w.page
        whole = [one["ref"] for one in served["tree"]]
        changes = {one["ref"] for one in served["changes"]}
        for query, drawn in (
            ("", {*changes, refs["s2"]}),
            ("&view=split", {*changes, refs["s2"]}),
            ("&mode=whole", set(whole)),
            ("&mode=whole&view=split", set(whole)),
        ):
            root = await review_at(w, number, query)
            marks = await marks_of(root)
            assert set(marks) == drawn, (query, set(marks))
            marks_equal(marks, blame)
            # A line is exactly once in the page, and its slot follows it.
            await expect(root.locator(".diff-item-line")).to_have_count(len(drawn))
            await expect(root.locator("button.blame")).to_have_count(len(drawn))
        # What a changed read draws: the signs the server's list says.
        root = await review_at(w, number)
        marks = await marks_of(root)
        assert {ref: mark["sign"] for ref, mark in marks.items()} == {
            refs["t1"]: "changed",
            refs["t3"]: "-",
            refs["t4"]: "+",
            refs["s2"]: ".",
        }
        assert marks[refs["t3"]]["blame_text"] == "removed in r2"

        # The hover of a carried state says who, which revision they read and when.
        chip = root.locator(f".diff-item-line[data-ref='{refs['s2']}'] span.line-review")
        said = await hover_words(page, chip)
        assert said.startswith("Priya Raman, on revision 1 · "), said
        # The hover of the blame chip says the revision and its author, and the note whole.
        revision = served["revisions"][1]
        await page.evaluate("() => document.activeElement && document.activeElement.blur()")
        await root.locator(f"button.blame[data-ref='{refs['t1']}']").hover()
        tip = page.locator(".hovercard:visible")
        await expect(tip).to_have_count(1)
        assert " ".join((await tip.text_content() or "").split()).startswith(
            f"Revision 2 by {revision['by']}, "
        )
        await expect(tip.locator("p.blame-note")).to_have_text(AGENT_NOTE)
        await expect(tip.locator("p.blame-note")).to_have_attribute("data-words", "theirs")
        await page.mouse.move(0, 0)
        # No sentence of the board's own is on the page because of any of it.
        assert await sentences(page) == []
        assert w.problems == []


async def test_pressing_a_blame_chip_reads_the_plan_as_of_that_revision() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await a_reviewed_plan(agent, project, owner)
        earlier = await read(agent, number, revision=1)
        page = w.page
        root = await review_at(w, number, "&mode=whole")
        # Pressing the chip of a line that came with revision 1 reads revision 1, as it was.
        await root.locator(f"button.blame[data-ref='{refs['t2']}']").click()
        await expect(root.locator("#review-diff")).to_have_attribute("data-revision", "1")
        assert "revision=1" in await page.evaluate("location.hash")
        assert await refs_of(item_lines(root)) == [one["ref"] for one in earlier["tree"]]
        marks = await marks_of(root.locator("#review-diff"))
        marks_equal(marks, blames(earlier))
        assert {mark["blame"] for mark in marks.values()} == {"1"}
        assert [mark["state"] for mark in marks.values()] == [
            "approved",
            "approved",
            "asked",
            "approved",
            "asked",
            "asked",
        ]
        # An earlier revision can be read and not decided: no control that changes anything.
        await expect(root.locator("button.diff-approve")).to_have_count(0)
        await expect(root.locator("button.diff-comment")).to_have_count(0)
        await expect(root.locator("div.decision")).to_have_count(0)
        # The select goes back to the current one (and reads what it changed); in the whole plan,
        # the chip of the current revision reads what that revision changed.
        await root.locator("#review-revision").select_option(label="Revision 2 (current)")
        await expect(root.locator("#review-diff")).to_have_attribute("data-revision", "2")
        assert "revision=" not in await page.evaluate("location.hash")
        await expect(root.locator("#review-diff")).to_have_attribute("data-mode", "delta")
        await root.locator("#review-mode button[data-mode=whole]").click()
        await expect(root.locator("#review-diff")).to_have_attribute("data-mode", "whole")
        await root.locator(f"button.blame[data-ref='{refs['t1']}']").click()
        await expect(root.locator("#review-diff")).to_have_attribute("data-mode", "delta")
        assert "mode=" not in await page.evaluate("location.hash")
        assert w.problems == []


# -- progress and hiding ----------------------------------------------------------------------


def runs_of(order: list[str], approved: set[str]) -> list[int]:
    """The sizes of the runs of approved items in the order the plan is read."""
    sizes: list[int] = []
    run = 0
    for ref in order:
        if ref in approved:
            run += 1
        elif run:
            sizes.append(run)
            run = 0
    if run:
        sizes.append(run)
    return sizes


async def line_keys(root: Locator) -> Counter[tuple[str | None, str | None, str | None]]:
    """The sign, item and part of every line in the page, hidden or not."""
    found: list[list[str | None]] = await root.locator("#review-diff").evaluate(
        """(diff) => [...diff.querySelectorAll('.diff-line')].map(
            (l) => [l.dataset.sign ?? null, l.dataset.ref ?? null, l.dataset.part ?? null])"""
    )
    return Counter((a, b, c) for a, b, c in found)


async def test_the_progress_fact_and_hide_approved_count_and_fold_exactly_the_approved_lines() -> (
    None
):
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await a_reviewed_plan(agent, project, owner)
        served = await read(agent, number)
        page = w.page
        root = await review_at(w, number, "&mode=whole")
        tree = [one["ref"] for one in served["tree"]]
        carried = {
            ref for ref, one in blames(served).items() if one["review"]["state"] == "approved"
        }
        assert carried == {refs["e"], refs["s1"], refs["t2"]}
        # Three of six are approved from earlier; the new task, approved now, makes four.
        facts = root.locator("li[data-fact=review]")
        await expect(facts).to_have_text("3 of 6 approved")
        await expect(root.locator("#review-hide-approved")).to_have_attribute(
            "aria-pressed", "false"
        )
        await expect(root.locator(".diff-foldrow[data-fold=approved]")).to_have_count(0)
        await root.locator(f"button.diff-approve[data-ref='{refs['t4']}']").click()
        await expect(facts).to_have_text("4 of 6 approved")
        await expect(facts).to_have_attribute("data-approved", "4")
        await expect(facts).to_have_attribute("data-total", "6")
        approved = {*carried, refs["t4"]}
        unhidden = await line_keys(root)

        # Hide approved: the address says so, and the approved lines are in the page and hidden,
        # behind one row for each run that says how many items it holds.
        await root.locator("#review-hide-approved").click()
        await expect(root.locator("#review-hide-approved")).to_have_attribute(
            "aria-pressed", "true"
        )
        assert "hide=approved" in await page.evaluate("location.hash")
        sizes = runs_of(tree, approved)
        assert sizes == [2, 1, 1]
        rows = root.locator(".diff-foldrow[data-fold=approved]")
        await expect(rows).to_have_count(len(sizes))
        assert [
            (await one.locator("button.diff-fold").text_content()) for one in await rows.all()
        ] == ["2 approved items", "1 approved item", "1 approved item"]
        for ref in tree:
            lines = root.locator(f"#review-diff .diff-line[data-ref='{ref}']")
            for line in await lines.all():
                if ref in approved:
                    await expect(line).to_be_hidden()
                    assert await line.get_attribute("data-closed")
                else:
                    await expect(line).to_be_visible()
        # Nothing was dropped: the same lines are in the page, shown and folded.
        assert await line_keys(root) == unhidden
        # The progress is the same, and the controls of a folded line are folded with it.
        await expect(facts).to_have_text("4 of 6 approved")
        await expect(root.locator(f"button.diff-approve[data-ref='{refs['t2']}']")).to_be_hidden()
        # One press shows the first run and only it; it stays open when the page is drawn again.
        await rows.first.locator("button.diff-fold").click()
        await expect(rows).to_have_count(len(sizes) - 1)
        await expect(root.locator(f".diff-item-line[data-ref='{refs['e']}']")).to_be_visible()
        await expect(root.locator(f".diff-item-line[data-ref='{refs['s1']}']")).to_be_visible()
        await expect(root.locator(f".diff-item-line[data-ref='{refs['t2']}']")).to_be_hidden()
        await root.locator("#review-view button[data-view=split]").click()
        await expect(root.locator(f".diff-item-line[data-ref='{refs['e']}']")).to_be_visible()
        await expect(root.locator(".diff-foldrow[data-fold=approved]")).to_have_count(2)
        assert await line_keys(root) == unhidden
        # It is an address: a page loaded again with it hides again (what waited is lost, what was
        # carried is not), and turning it off shows everything.
        await page.reload()
        root = page.locator("#review-page")
        await expect(root.locator("#review-hide-approved")).to_have_attribute(
            "aria-pressed", "true"
        )
        await expect(root.locator("#review-diff")).to_be_visible(timeout=SLOW)
        await expect(facts).to_have_text("3 of 6 approved")
        await expect(root.locator(".diff-foldrow[data-fold=approved]")).to_have_count(2)
        await expect(root.locator(f".diff-item-line[data-ref='{refs['e']}']")).to_be_hidden()
        await expect(root.locator(f".diff-item-line[data-ref='{refs['t4']}']")).to_be_visible()
        await root.locator("#review-hide-approved").click()
        await expect(root.locator(".diff-foldrow[data-fold=approved]")).to_have_count(0)
        assert "hide=" not in await page.evaluate("location.hash")
        await expect(root.locator(f".diff-item-line[data-ref='{refs['e']}']")).to_be_visible()
        assert await line_keys(root) == unhidden
        assert w.problems == []


async def test_hide_approved_folds_the_lines_of_a_changes_read_in_both_layouts() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await a_reviewed_plan(agent, project, owner)
        page = w.page
        root = await review_at(w, number)
        # The changed task, the removed one (agreed to go) and the added one are approved now.
        for key in ("t1", "t3", "t4"):
            await root.locator(f"button.diff-approve[data-ref='{refs[key]}']").click()
        unhidden = await line_keys(root)
        await root.locator("#review-hide-approved").click()
        # One run in the unified read: the three items follow each other (the asked story, which
        # is not approved, stays in view).
        folds = root.locator(".diff-foldrow[data-fold=approved] button.diff-fold")
        await expect(folds).to_have_text(["3 approved items"])
        await expect(root.locator(f".diff-item-line[data-ref='{refs['s2']}']")).to_be_visible()
        for key in ("t1", "t3", "t4"):
            await expect(root.locator(f".diff-item-line[data-ref='{refs[key]}']")).to_be_hidden()
        assert await line_keys(root) == unhidden
        # In the split layout each column folds its own lines: the old one holds the removed item
        # and what the changed one was, the new one the changed item and the added one.
        await root.locator("#review-view button[data-view=split]").click()
        old = root.locator(".diff-side[data-side=old] .diff-foldrow[data-fold=approved]")
        new = root.locator(".diff-side[data-side=new] .diff-foldrow[data-fold=approved]")
        await expect(old.locator("button.diff-fold")).to_have_text(["2 approved items"])
        await expect(new.locator("button.diff-fold")).to_have_text(["2 approved items"])
        # The split layout has one line more (the changed item as it was, for its old column), so
        # what is compared is the same layout with the lines shown.
        shown = await line_keys(root)
        await root.locator("#review-hide-approved").click()
        await expect(root.locator(".diff-foldrow[data-fold=approved]")).to_have_count(0)
        assert await line_keys(root) == shown
        await root.locator("#review-hide-approved").click()
        await expect(old.locator("button.diff-fold")).to_have_text(["2 approved items"])
        await new.locator("button.diff-fold").click()
        await expect(root.locator(f".diff-item-line[data-ref='{refs['t4']}']")).to_be_visible()
        await expect(root.locator(f".diff-item-line[data-ref='{refs['t3']}']")).to_be_hidden()
        assert "hide=approved" in await page.evaluate("location.hash")
        assert w.problems == []


async def test_hide_approved_is_there_only_when_something_is_approved_and_the_card_agrees() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent)
        page = w.page
        root = await review_at(w, number)
        await expect(root.locator("#review-hide-approved")).to_have_count(0)
        # An address that asks to hide, with nothing approved, hides nothing and draws no fold.
        root = await review_at(w, number, "&hide=approved")
        await expect(root.locator(".diff-foldrow[data-fold=approved]")).to_have_count(0)
        await expect(root.locator(".diff-item-line")).to_have_count(6)
        await root.locator(f"button.diff-approve[data-ref='{refs['t1']}']").click()
        await expect(root.locator("#review-hide-approved")).to_have_attribute(
            "aria-pressed", "true"
        )
        await expect(root.locator(".diff-foldrow[data-fold=approved]")).to_have_count(1)
        # A folded line is out of reach until it is shown: one press shows it, and it can be undone.
        await root.locator(".diff-foldrow[data-fold=approved] button.diff-fold").click()
        await root.locator(f"button.diff-approve[data-ref='{refs['t1']}']").click()
        await expect(root.locator("#review-hide-approved")).to_have_count(0)
        await expect(root.locator(".diff-foldrow[data-fold=approved]")).to_have_count(0)
        await root.locator(f"button.diff-approve[data-ref='{refs['t2']}']").click()
        await w.open("#/needs")
        await on_page(page, "needs")
        card = card_of(page, f"plan:{number}")
        await expect(card.locator("li[data-fact=review]")).to_have_text("1 of 6 approved")
        assert w.problems == []


# -- a revision arrives ---------------------------------------------------------------------------


async def test_a_revision_resets_what_waits_and_the_notice_counts_the_approvals_too() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent)
        page = await open_needs(w, number)
        review = in_card(page, number)
        await expect(item_lines(review)).to_have_count(6, timeout=SLOW)
        await review.locator(f"button.diff-approve[data-ref='{refs['t1']}']").click()
        await say_comment(review, refs["t3"], "Why so small?")
        await expect(review.locator("span.decision-state")).to_have_text("1 approved, 1 unsent")
        await a_revision(agent, number, refs, "Sized the first task.", t1={"size": "l"})
        notice = review.locator("p.change-lost")
        await expect(notice).to_contain_text("Revision 2 arrived", timeout=SLOW)
        await expect(notice).to_contain_text("Not kept: 1 unsent comment, 1 unsent approval")
        await review.locator("details.change-lost-list summary").click()
        await expect(review.locator("li.lost-approved")).to_have_text(
            f"Approved: {code(refs['t1'])}"
        )
        # Nothing of it is on the lines or in the bar, and what the server holds is unchanged.
        await expect(review.locator("button.diff-approve[aria-pressed='true']")).to_have_count(0)
        await expect(review.locator("span.line-review")).to_have_count(0)
        await expect(review.locator("span.decision-state")).to_have_count(0)
        await expect(review.locator("div.diff-comment-pending")).to_have_count(0)
        await expect(review.locator("li[data-fact=review]")).to_have_count(0)
        assert (await read(agent, number))["feedback"] == []
        await review.locator("button.notice-close").click()
        await expect(notice).to_have_count(0)
        assert w.problems == []


# -- a line that was taken out ------------------------------------------------------------------


async def test_a_removed_line_can_be_agreed_to_or_asked_for_back_and_both_are_sent() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent)
        await a_revision(agent, number, refs, "Dropped the covers.", without=["s2", "t3"])
        page = await open_needs(w, number)
        review = in_card(page, number)
        gone = review.locator(".diff-item-line[data-sign='-']")
        await expect(gone).to_have_count(2, timeout=SLOW)
        assert await refs_of(gone) == [refs["s2"], refs["t3"]]
        # A removed line has the same controls as any other, and its chip says where it went.
        for key, name in (("s2", "S-2"), ("t3", "T-3")):
            await expect(
                review.locator(f"button.diff-approve[data-ref='{refs[key]}']")
            ).to_have_attribute("aria-label", f"Agree with removing {name}")
            await expect(
                review.locator(f"button.diff-comment[data-ref='{refs[key]}']")
            ).to_have_count(1)
            chip = review.locator(f"button.blame[data-ref='{refs[key]}']")
            await expect(chip).to_have_text("removed in r2")
            await expect(chip).to_have_attribute("data-revision", "2")
        assert await hover_words(
            page, review.locator(f"button.diff-approve[data-ref='{refs['s2']}']")
        ) == ("Agree it goes")
        # Agreeing to it going is pending; commenting afterwards takes it back, as on any line.
        await review.locator(f"button.diff-approve[data-ref='{refs['t3']}']").click()
        await expect(
            review.locator(f".diff-item-line[data-ref='{refs['t3']}'] span.line-review")
        ).to_have_attribute("data-pending", "true")
        # The box for a removed item offers only the whole item.
        await review.locator(f"button.diff-comment[data-ref='{refs['t3']}']").click()
        form = review.locator(f"form.comment-form[data-ref='{refs['t3']}']")
        await expect(form.locator("select.comment-field option")).to_have_text(["The whole item"])
        await form.locator("textarea.comment-text").fill("Keep it: the covers matter.")
        await form.locator("button.comment-add").click()
        await expect(
            review.locator(f"button.diff-approve[data-ref='{refs['t3']}']")
        ).to_have_attribute("aria-pressed", "false")
        quote = review.locator(f"div.diff-comment-pending[data-ref='{refs['t3']}'] blockquote")
        await expect(quote).to_have_text("Order covers")
        await review.locator(f"button.diff-approve[data-ref='{refs['s2']}']").click()
        # Removed items are not items of the plan: they are not counted in the progress.
        await expect(review.locator("li[data-fact=review]")).to_have_count(0)
        await expect(review.locator("#review-hide-approved")).to_have_count(1)
        await expect(review.locator("span.decision-state")).to_have_text("1 approved, 1 unsent")
        await review.locator("button.verdict[data-verb=changes]").click()
        dialog = page.locator("#changes-dialog")
        await expect(dialog.locator("ul.changes-approved li")).to_have_attribute(
            "data-ref", refs["s2"]
        )
        await expect(dialog.locator("ul.changes-approved li")).to_contain_text("Weather covers")
        await dialog.locator("#changes-summary").fill("Keep the order, drop the rest.")
        await dialog.get_by_role("button", name="Ask for changes").click()
        await expect(dialog).to_have_count(0)
        feedback = (await read(agent, number))["feedback"][0]
        assert feedback["approved"] == [refs["s2"]]
        assert [(c["item"], c["field"], c["quoted"], c["text"]) for c in feedback["comments"]] == [
            (refs["t3"], None, "Order covers", "Keep it: the covers matter.")
        ]
        assert w.problems == []


# -- a viewer ------------------------------------------------------------------------------------


async def test_a_viewer_reads_every_state_and_blame_and_has_no_control_that_says_yes_or_no() -> (
    None
):
    owner = register(name="Priya Raman")
    project = make_project(owner)
    viewer = join_project(project, "viewer", "Vera Viewer")
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=None, person=viewer, name=None) as w,
    ):
        number, refs = await a_reviewed_plan(agent, project, owner)
        blame = blames(await read(agent, number))
        for query in ("", "&mode=whole"):
            root = await review_at(w, number, query)
            marks = await marks_of(root)
            marks_equal(marks, blame)
            expected = {"changed", "asked"} if not query else {"approved", "changed", "asked"}
            assert {m["state"] for m in marks.values()} - {None} == expected
            await expect(root.locator("button.blame")).to_have_count(len(marks))
            for control in (
                "button.diff-approve",
                "button.diff-comment",
                "button.verdict",
                "div.decision",
            ):
                await expect(root.locator(control)).to_have_count(0)
        # The progress and Hide approved are for reading, so they are there.
        await expect(root.locator("li[data-fact=review]")).to_have_text("3 of 6 approved")
        await root.locator("#review-hide-approved").click()
        await expect(root.locator(".diff-foldrow[data-fold=approved]")).to_have_count(2)
        await expect(root.locator(f".diff-item-line[data-ref='{refs['e']}']")).to_be_hidden()
        await expect(root.locator(f".diff-item-line[data-ref='{refs['t1']}']")).to_be_visible()
        # The card in the list says the same states and has nothing to press.
        await w.open("#/needs")
        await on_page(w.page, "needs")
        card = card_of(w.page, f"plan:{number}")
        await expect(card.locator("li[data-fact=review]")).to_have_text("3 of 6 approved")
        await expect(card.locator("span.line-review[data-state=changed]")).to_have_count(1)
        for control in ("button.diff-approve", "button.diff-comment", "button.verdict"):
            await expect(card.locator(control)).to_have_count(0)
        assert w.problems == []


# -- the card of a revised plan ------------------------------------------------------------------

REMOVE_BACK = """(line) => {
    const probe = document.createElement('div');
    probe.style.background = 'var(--diff-remove-bg)';
    document.body.append(probe);
    const wanted = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return [getComputedStyle(line.querySelector('.diff-code')).backgroundColor, wanted];
}"""


async def test_the_card_of_a_revised_plan_previews_what_changed_with_its_red_lines() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await a_reviewed_plan(agent, project, owner)
        served = await read(agent, number)
        blame = blames(served)
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        card = card_of(page, f"plan:{number}")
        await expect(card.locator(".diff-hunk")).to_have_text(
            "Revision 2: 1 added, 1 changed, 1 removed", timeout=SLOW
        )
        # Not the whole plan as additions: the items the revision changed, with their own signs.
        lines = card.locator(".diff-item-line")
        await expect(lines).to_have_count(3)
        marks = await marks_of(card)
        assert {ref: mark["sign"] for ref, mark in marks.items()} == {
            refs["t1"]: "changed",
            refs["t3"]: "-",
            refs["t4"]: "+",
        }
        assert await refs_of(lines) == [refs["t1"], refs["t3"], refs["t4"]]
        assert await refs_of(item_lines(card, "+")) == [refs["t4"]]
        # The red line is the whole line as it was, in the colour of what is removed.
        gone = card.locator(f".diff-item-line[data-ref='{refs['t3']}']")
        await expect(gone).to_have_attribute("data-sign", "-")
        await expect(gone).to_contain_text("Order covers")
        actual, wanted = await gone.evaluate(REMOVE_BACK)
        assert actual == wanted, (actual, wanted)
        # What is carried is on the lines (the card has no blame chip), and equals the server's.
        await expect(card.locator("button.blame")).to_have_count(0)
        for ref, mark in marks.items():
            state = blame[ref]["review"]["state"]
            assert mark["state"] == (None if state == "none" else state), (ref, mark)
        # The card says the same lines as the review page reads as its changes.
        root = await open_needs_review(w, number)
        read_here = await marks_of(root)
        assert [
            (mark["sign"], ref)
            for ref, mark in read_here.items()
            if mark["sign"] in ("+", "-", "changed")
        ] == [(mark["sign"], ref) for ref, mark in marks.items()]
        assert w.problems == []


async def test_a_card_that_cannot_show_every_change_says_how_many_are_behind_its_fold() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent, title="Big revision")
        extra = [
            {"key": "t4", "kind": "task", "title": "Spare covers", "parent": "s2", "size": "xs"},
            {"key": "t5", "kind": "task", "title": "Spare tyres", "parent": "s1", "size": "xs"},
        ]
        await a_revision(
            agent,
            number,
            refs,
            "Renamed everything and added two.",
            e={"title": "Winter fleet again"},
            s1={"title": "Studded tyres again"},
            s2={"title": "Weather covers again"},
            t1={"title": "Choose a supplier again"},
            t2={"title": "Fit forty bikes again"},
            t3={"title": "Order covers again"},
            extra=extra,
        )
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        card = card_of(page, f"plan:{number}")
        await expect(card.locator(".diff-hunk")).to_have_text(
            "Revision 2: 2 added, 6 changed", timeout=SLOW
        )
        await expect(card.locator(".diff-item-line")).to_have_count(6)
        fold = card.locator("button.diff-fold[data-fold=preview]")
        await expect(fold).to_have_text("2 more items")
        await fold.click()
        await expect(card.locator(".diff-item-line")).to_have_count(8)
        await expect(fold).to_have_count(0)
        assert w.problems == []


async def test_the_first_revision_previews_the_plan_and_a_card_line_can_be_approved() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent)
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        card = card_of(page, f"plan:{number}")
        await expect(card.locator(".diff-hunk")).to_have_text(
            "1 epic, 2 stories, 3 tasks added", timeout=SLOW
        )
        await expect(item_lines(card)).to_have_count(6)
        # The control of each line is on the card too, and a comment is written on the review page.
        order = [one["ref"] for one in (await read(agent, number))["tree"]]
        assert await refs_of(card.locator("button.diff-approve")) == order
        await expect(card.locator("button.diff-comment")).to_have_count(0)
        assert await hover_words(
            page, card.locator(f"button.diff-approve[data-ref='{refs['s1']}']")
        ) == ("Approve as it is")
        await card.locator(f"button.diff-approve[data-ref='{refs['s1']}']").click()
        await expect(
            card.locator(f".diff-item-line[data-ref='{refs['s1']}'] span.line-review")
        ).to_have_attribute("data-pending", "true")
        await expect(card.locator("span.decision-state")).to_have_text("1 approved")
        await expect(card.locator("li[data-fact=review]")).to_have_text("1 of 6 approved")
        # What was pressed on the card is what the review page holds.
        root = await open_needs_review(w, number)
        await expect(
            root.locator(f"button.diff-approve[data-ref='{refs['s1']}']")
        ).to_have_attribute("aria-pressed", "true")
        assert w.problems == []


# -- a suggested move, in part -------------------------------------------------------------------


async def parent_of(agent: Client, ref: str) -> str | None:
    found = await ok(agent, "get_node", node=ref)
    parent: str | None = found["parent"]
    return parent


async def a_move(agent: Client) -> tuple[dict[str, str], list[str], int]:
    """A sorted project and a proposal to move its three loose stories under Billing."""
    refs = await a_sorted_project(agent)
    items = [refs["loose_pay"], refs["loose_mail"], refs["loose_login"]]
    return (
        refs,
        items,
        await a_proposal(agent, items, refs["billing"], "All three are about money."),
    )


async def test_a_move_accepted_in_part_moves_only_the_included_and_says_who_left_the_rest() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        refs, items, number = await a_move(agent)
        page = w.page
        sent = recording(page, "decide_placement")
        await w.open(f"#/review?change=placement:{number}")
        root = page.locator("#review-page")
        await expect(root.locator(".diff-item-line[data-sign='+']")).to_have_count(3, timeout=SLOW)
        # Every moved item is included, on the line of its new place and on the line of its old one.
        assert await refs_of(root.locator("button.diff-include")) == items
        for ref in items:
            include = root.locator(f"button.diff-include[data-ref='{ref}']")
            await expect(include).to_have_attribute("aria-pressed", "true")
            await expect(include).to_have_attribute("aria-label", f"Move {code(ref)}")
            for sign in ("+", "-"):
                await expect(
                    root.locator(f".diff-item-line[data-sign='{sign}'][data-ref='{ref}']")
                ).to_have_attribute("data-included", "true")
        await expect(root.locator("span.decision-state")).to_have_count(0)
        left = items[1]
        assert await hover_words(page, root.locator(f"button.diff-include[data-ref='{left}']")) == (
            "Moves with the proposal"
        )

        # Leaving one where it is: its button, both its lines and the bar say so.
        await root.locator(f"button.diff-include[data-ref='{left}']").click()
        include = root.locator(f"button.diff-include[data-ref='{left}']")
        await expect(include).to_have_attribute("aria-pressed", "false")
        await expect(include).to_have_attribute("aria-label", f"Leave {code(left)} where it is")
        for sign in ("+", "-"):
            await expect(
                root.locator(f".diff-item-line[data-sign='{sign}'][data-ref='{left}']")
            ).to_have_attribute("data-included", "false")
        await expect(root.locator("span.decision-state")).to_have_text("2 of 3 move")
        await expect(root.locator("span.decision-state")).to_have_attribute("data-moving", "2")
        assert await hover_words(page, include) == "Stays where it is"
        # The choice lives in the page: drawing it again (Split), and the card in the list, keep it.
        await root.locator("#review-view button[data-view=split]").click()
        await expect(root.locator(f"button.diff-include[data-ref='{left}']")).to_have_attribute(
            "aria-pressed", "false"
        )
        await expect(root.locator("button.diff-include")).to_have_count(3)
        await w.open("#/needs")
        await on_page(page, "needs")
        await page.wait_for_function(
            "() => document.querySelectorAll('li.needs-row[data-reading=true]').length === 0",
            timeout=SLOW,
        )
        group = page.locator("details.routine-group")
        if await group.count() and await group.get_attribute("open") is None:
            await group.locator("summary").click()
        proposal = card_of(page, f"placement:{number}")
        await expect(proposal.locator(f"button.diff-include[data-ref='{left}']")).to_have_attribute(
            "aria-pressed", "false"
        )
        await expect(proposal.locator("span.decision-state")).to_have_text("2 of 3 move")
        # Approve moves the two that are included, and only those.
        await proposal.locator("button.verdict[data-verb=approve]").click()
        await expect(proposal).to_have_count(0, timeout=SLOW)
        assert sent[-1] == {
            "placement": number,
            "accepted": True,
            "items": [items[0], items[2]],
        }
        assert await parent_of(agent, items[0]) == refs["billing"]
        assert await parent_of(agent, items[2]) == refs["billing"]
        assert await parent_of(agent, left) == refs["inbox"]
        decided = (await ok(agent, "list_placements", status="accepted"))["items"][0]
        assert decided["applied"] == [items[0], items[2]]
        assert decided["skipped"] == [{"ref": left, "why": "Priya Raman left it out"}]
        # The server's words, once, as for any item left where it is.
        said = page.locator("#needs-said .needs-said")
        await expect(said).to_contain_text(
            f"{code(left)} left where it is: Priya Raman left it out"
        )
        # The history says how many moved and how many were left out.
        await w.open("#/activity")
        await on_page(page, "activity")
        row = page.locator("li.event.action-placement_accepted .event-words").first
        await expect(row).to_contain_text(
            f"accepted Proposal {number} (2 moved, 1 left out): moved", timeout=SLOW
        )
        await expect(row).to_contain_text("Priya Raman left it out")
        assert w.problems == []


async def test_a_whole_move_sends_no_list_and_a_move_with_nothing_included_cannot_be_approved() -> (
    None
):
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        refs = await a_sorted_project(agent)
        whole = await a_proposal(
            agent, [refs["loose_pay"], refs["loose_mail"]], refs["billing"], "Both are money."
        )
        none = await a_proposal(agent, [refs["loose_login"]], refs["access"], "A login story.")
        page = w.page
        sent = recording(page, "decide_placement")
        # Included and then left out and included again is the proposal as it was made.
        await w.open(f"#/review?change=placement:{whole}")
        root = page.locator("#review-page")
        await expect(root.locator("button.diff-include")).to_have_count(2, timeout=SLOW)
        again = root.locator(f"button.diff-include[data-ref='{refs['loose_mail']}']")
        await again.click()
        await expect(root.locator("span.decision-state")).to_have_text("1 of 2 move")
        await again.click()
        await expect(root.locator("span.decision-state")).to_have_count(0)
        await root.locator("button.verdict[data-verb=approve]").click()
        # The page goes on to the next change in the list: the other proposal.
        await expect(page.locator("#review-page")).to_have_attribute(
            "data-change", f"placement:{none}", timeout=SLOW
        )
        assert sent[-1] == {"placement": whole, "accepted": True}
        for key in ("loose_pay", "loose_mail"):
            assert await parent_of(agent, refs[key]) == refs["billing"]

        # With none included there is nothing to move: Approve cannot be pressed, and says why;
        # Reject can, and the item stays.
        root = page.locator("#review-page")
        await root.locator(f"button.diff-include[data-ref='{refs['loose_login']}']").click()
        await expect(root.locator("span.decision-state")).to_have_text("0 of 1 move")
        approve = root.locator("button.verdict[data-verb=approve]")
        await expect(approve).to_be_disabled()
        assert await hover_words(page, approve) == "Nothing to move"
        await expect(root.locator("button.verdict[data-verb=reject]")).to_be_enabled()
        await root.locator(f"button.diff-include[data-ref='{refs['loose_login']}']").click()
        await expect(approve).to_be_enabled()
        await expect(root.locator("span.decision-state")).to_have_count(0)
        assert await parent_of(agent, refs["loose_login"]) == refs["inbox"]
        assert w.problems == []


async def test_a_viewer_has_no_include_control_and_reads_a_move_as_before() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    viewer = join_project(project, "viewer", "Vera Viewer")
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=None, person=viewer, name=None) as w,
    ):
        _, items, number = await a_move(agent)
        page = w.page
        await w.open(f"#/review?change=placement:{number}")
        root = page.locator("#review-page")
        await expect(root.locator(".diff-item-line[data-sign='+']")).to_have_count(3, timeout=SLOW)
        await expect(root.locator("button.diff-include")).to_have_count(0)
        await expect(root.locator("button.verdict")).to_have_count(0)
        assert await refs_of(root.locator(".diff-item-line[data-sign='+']")) == items
        assert w.problems == []


# -- no control in a control, the tab order, no sentence ---------------------------------------


async def test_no_control_holds_a_control_tab_follows_the_line_and_no_sentence_appears() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await a_reviewed_plan(agent, project, owner)
        _, _, move = await a_move(agent)
        page = w.page
        for query in ("", "&view=split", "&mode=whole", "&mode=whole&view=split&hide=approved"):
            root = await review_at(w, number, query)
            await root.locator(f"button.diff-approve[data-ref='{refs['t4']}']").click()
            if "hide=approved" in query:
                await root.locator(
                    ".diff-foldrow[data-fold=approved] button.diff-fold"
                ).first.click()
            await root.locator(f"button.diff-comment[data-ref='{refs['t1']}']").click()
            await expect(root.locator("form.comment-form")).to_be_visible()
            assert await nested_controls(page) == [], query
            found = await sentences(page)
            assert found == [], (query, found)
            await root.locator("form.comment-form button.comment-cancel").click()
            # A run of approved lines is folded away: show it to undo the approval that is in it.
            folds = root.locator(".diff-foldrow[data-fold=approved] button.diff-fold")
            while await folds.count():
                await folds.first.click()
            await root.locator(f"button.diff-approve[data-ref='{refs['t4']}']").click()
        # The Request changes dialog, with an approval and a comment in it, is a heading and fields.
        root = await review_at(w, number)
        await root.locator(f"button.diff-approve[data-ref='{refs['t4']}']").click()
        await root.locator("button.verdict[data-verb=changes]").click()
        await expect(page.locator("#changes-dialog")).to_be_visible()
        assert await sentences(page) == []
        assert await nested_controls(page) == []
        await page.locator("#changes-dialog").get_by_role("button", name="Cancel").click()
        await root.locator(f"button.diff-approve[data-ref='{refs['t4']}']").click()
        # The card in the list, and the page of a move with an item left out.
        await w.open("#/needs")
        await on_page(page, "needs")
        await expect(
            card_of(page, f"plan:{number}").locator(".diff-item-line").first
        ).to_be_visible(timeout=SLOW)
        assert await nested_controls(page) == []
        assert await sentences(page) == []
        await w.open(f"#/review?change=placement:{move}")
        root = page.locator("#review-page")
        await expect(root.locator("button.diff-include").first).to_be_visible(timeout=SLOW)
        await root.locator("button.diff-include").first.click()
        for view in ("unified", "split"):
            await root.locator(f"#review-view button[data-view={view}]").click()
            assert await nested_controls(page) == []
            assert await sentences(page) == []

        # Tab goes from a line to its chip of who changed it, then approve, then the comment.
        root = await review_at(w, number, "&mode=whole")
        line = root.locator(f".diff-item-line[data-ref='{refs['t4']}']")
        await line.focus()
        order: list[str] = []
        for _ in range(12):
            await page.keyboard.press("Tab")
            focused: str = await page.evaluate(
                """() => {
                    const e = document.activeElement;
                    const kinds = ['blame', 'diff-approve', 'diff-comment'];
                    const kind = kinds.find((c) => e.classList.contains(c));
                    return kind ? `${kind}:${e.dataset.ref}` : '';
                }"""
            )
            if focused:
                order.append(focused)
            if len(order) == 3:
                break
        assert order == [
            f"{kind}:{refs['t4']}" for kind in ("blame", "diff-approve", "diff-comment")
        ]
        assert w.problems == []


# -- both themes, at 390 and 1280 -----------------------------------------------------------------


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("screen", [SCREENS[0], SCREENS[2]], ids=lambda screen: screen.name)
async def test_every_new_state_and_control_reads_in_both_themes_at_390_and_1280(
    screen: Screen, theme: str
) -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        number, refs = await a_reviewed_plan(agent, project, owner)
        _, items, move = await a_move(agent)
        async with layout_context(project.address, screen, theme) as (w, context):
            scratch = await blank_page(context)
            page = w.page

            async def reads(*, least: int = 20) -> None:
                await settle(page)
                found = await contrast(w, scratch)
                assert found.checked >= least, found.checked
                assert found.found == [], found.lines()
                if screen.width < 700:
                    await no_overflow(page)

            # What changed, with the carried states, the chips of who changed it, an approval and a
            # comment waiting, and the box to write one.
            root = await review_at(w, number)
            await root.locator(f"button.diff-approve[data-ref='{refs['t4']}']").click()
            await root.locator(f"button.diff-comment[data-ref='{refs['s2']}']").click()
            await expect(root.locator("form.comment-form")).to_be_visible()
            await reads()
            await root.locator("form.comment-form button.comment-cancel").click()
            # The whole plan, where every state is on a line; then Hide approved with a run opened.
            root = await review_at(w, number, "&mode=whole")
            await expect(root.locator("span.line-review")).to_have_count(6)
            await reads()
            await root.locator("#review-hide-approved").click()
            await expect(root.locator(".diff-foldrow[data-fold=approved]").first).to_be_visible()
            await reads(least=10)
            await root.locator("#review-hide-approved").click()
            # The dialog that sends it, with the approved items and the comments listed.
            await root.locator("button.verdict[data-verb=changes]").click()
            await expect(page.locator("#changes-dialog")).to_be_visible()
            await reads(least=5)
            await page.locator("#changes-dialog").get_by_role("button", name="Cancel").click()
            # An earlier revision, read as it was, and a removed line with its chip.
            await w.open(f"#/review?change=plan:{number}&revision=1&mode=whole")
            await expect(page.locator("#review-diff[data-revision='1']")).to_be_visible(
                timeout=SLOW
            )
            await reads(least=15)
            # The card of the revised plan: its red line and what is carried.
            await w.open("#/needs")
            await on_page(page, "needs")
            await expect(
                card_of(page, f"plan:{number}").locator(".diff-item-line[data-sign='-']")
            ).to_be_visible(timeout=SLOW)
            await reads()
            # A move with one item left where it is, on the page and on its card.
            await w.open(f"#/review?change=placement:{move}")
            root = page.locator("#review-page")
            await expect(root.locator("button.diff-include").first).to_be_visible(timeout=SLOW)
            await root.locator(f"button.diff-include[data-ref='{items[1]}']").click()
            await reads(least=15)
            assert w.problems == []
