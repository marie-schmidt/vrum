"""Browser proofs of the M49 chapter (tests/acceptance_review.py), as they are since M52: the
Needs a person list is a triage inbox of compact changes and every change opens a full-screen
review page.
A plan is read whole there (the lines of its tree, above the bar that decides it), the list marks
what was viewed and what changed since, its keys move and open and never decide, and a suggested
move is lines taken out and put in. Each part works in a project of its own, with a headless
browser signed in as the owner or a viewer of it. The proofs of what the tools say are in
tests/acceptance_review.py, which this file builds on."""

import math
import re

from playwright.async_api import expect

from tests.acceptance_review import (
    CONTEXT_LINES,
    ITEM_LINES,
    REMOVED_LINES,
    a_big_plan,
    a_plan,
    lines_of,
    open_everything,
    open_review,
    plan_card,
    review_page,
    the_diff_is_before_the_decision,
    the_needs_page,
    the_whole_plan,
)
from tests.acceptance_scenario import proof
from tests.board_helpers import SLOW, browser_page
from tests.helpers import connect, make_tree, ok, reindexed
from tests.world import join_project, make_project, register


async def the_page_shows_the_whole_plan() -> None:
    """In a browser the review page of a plan draws the lines of the tree above the bar that
    decides it, for the owner and for a viewer, who has no button; the list shows the plan as a
    compact card with a short preview."""
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer")
    async with connect(project.mcp_url, project.key) as agent:
        epic, story, _ = await make_tree(agent)
        made = await a_plan(agent, epic, story, "Card payments")
        big = await a_big_plan(agent, epic, "Everything for the launch")
        plan = await the_whole_plan(agent, made["plan"])
        large = await the_whole_plan(agent, big["plan"])
    tree_refs = [one["ref"] for one in plan["tree"]]
    context_refs = [one["ref"] for one in plan["context"]]
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_needs_page(w)
        await expect(page.locator("li.needs-row")).to_have_count(2, timeout=SLOW)
        card = plan_card(page, made["plan"])
        total = plan["epics"] + plan["stories"] + plan["tasks"]
        impact = (await card.locator("[data-fact='impact']").inner_text()).strip()
        await expect(card.locator(".change-preview .diff-line").first).to_be_visible(timeout=SLOW)
        preview = await card.locator(".change-preview .diff-line").count()
        big_card = plan_card(page, big["plan"])
        await expect(big_card.locator(".change-preview .diff-line").first).to_be_visible(
            timeout=SLOW
        )
        big_preview = await big_card.locator(".change-preview .diff-line").count()
        folded = await big_card.locator(".change-preview .diff-fold").all_inner_texts()
        proof(
            "M52",
            "the list shows a plan as a compact card: what it frees as a fact (Frees N, the number "
            "of items it makes) and a short preview of its lines, at most six, with a fold that "
            "says how much is behind it for a plan of 45 items",
            impact == f"Frees {total}"
            and preview > 0
            and big_preview <= 6
            and any(re.search(r"\d+", fold) for fold in folded),
            f"{impact}, {preview} and {big_preview} lines, {folded}",
        )

        await open_review(w, f"plan:{made['plan']}")
        page_ = review_page(page)
        shown = await lines_of(page_, ITEM_LINES, len(tree_refs))
        quiet = await lines_of(page_, CONTEXT_LINES, len(context_refs))
        text = " ".join((await page_.locator(".change-diff").inner_text()).split())
        criteria = await page_.locator(
            ".change-diff .diff-line[data-part='criteria']"
        ).all_inner_texts()
        header = " ".join((await page_.locator(".diff-hunk").first.inner_text()).split())
        verdicts = await page_.locator("button.verdict").count()
        proof(
            "M49",
            "in a browser the review page of a plan shows the whole tree, one added line for "
            "each item the plan made, in the order list_plans reads them, with the title, "
            "details and acceptance criteria of each, and as quiet lines the existing epic it "
            "hangs under and the story it relates to; the hunk row counts what it adds in "
            "words; the bar with the three verdicts comes after the last line (replaced by "
            "M52: the sentence about what the decision does is the buttons' hover card)",
            shown == tree_refs
            and quiet == context_refs
            and all(one["title"] in text for one in plan["tree"])
            and "No wallets yet." in text
            and all(
                any(c["title"] in line for line in criteria)
                for one in plan["tree"]
                for c in one["criteria"]
            )
            and len(criteria) >= 2
            and "A declined card says why" in text
            and "2 stories" in header
            and "2 tasks" in header
            and "@@" not in header
            and await the_diff_is_before_the_decision(page_)
            and verdicts == 3,
            f"{len(shown)} lines, {len(quiet)} context, {header}",
        )

        # A large plan folds, says how much, and opens in one press for each fold.
        await open_review(w, f"plan:{big['plan']}")
        page_ = review_page(page)
        await expect(page_.locator(".change-diff .diff-fold").first).to_be_visible(timeout=SLOW)
        folds = await page_.locator(".change-diff .diff-fold").all_inner_texts()
        before = await lines_of(page_, ITEM_LINES)
        said = sum(int(n) for fold in folds for n in re.findall(r"\d+", fold))
        await open_everything(page_)
        opened = await lines_of(page_, ITEM_LINES)
        proof(
            "M49",
            "a plan of 45 items is folded by story and says how many items each fold holds, so "
            "that what is shown and what is folded together are the 45, and pressing the folds "
            "opens every item, in the order list_plans reads them",
            len(large["tree"]) == 45
            and folds != []
            and len(before) < 45
            and len(before) + said == 45
            and opened == [one["ref"] for one in large["tree"]],
            f"{len(before)} shown, {said} folded, {len(opened)} after",
        )

    async with browser_page(project.address, token=None, person=viewer, name=None) as w:
        page = w.page
        await open_review(w, f"plan:{made['plan']}")
        seen = await lines_of(review_page(page), ITEM_LINES, len(tree_refs))
        await the_needs_page(w)
        proof(
            "M49",
            "a viewer sees the whole diff of the plan on its review page and no button to decide "
            "it, on the page or on the list",
            seen == tree_refs
            and await page.locator("button.verdict, button.decision-comment").count() == 0,
            f"{len(seen)} lines, no button",
        )


async def viewed_and_empty() -> None:
    """Viewed folds a change's preview and is counted; the empty page says nothing is left."""
    owner = register()
    project = make_project(owner)
    async with browser_page(project.address, token=project.key, name=None) as w:
        await the_needs_page(w)
        empty = w.page.locator("#needs-empty")
        await expect(empty).to_be_visible(timeout=SLOW)
        quiet = " ".join((await empty.inner_text()).split())
        rows_when_empty = await w.page.locator("li.needs-row").count()
    async with connect(project.mcp_url, project.key) as agent:
        epic, story, _ = await make_tree(agent)
        made = await a_plan(agent, epic, story, "Card payments")
        await a_plan(agent, epic, story, "Another set")
    async with browser_page(project.address, token=project.key, name=None) as w:
        await the_needs_page(w)
        card = plan_card(w.page, made["plan"])
        await expect(card).to_have_count(1, timeout=SLOW)
        reviewed = w.page.locator("#needs-reviewed")
        await expect(card.locator(".change-preview .diff-line").first).to_be_visible(timeout=SLOW)
        before = " ".join((await reviewed.inner_text()).split())
        lines = await card.locator(".change-preview .diff-line").count()
        await card.locator("input.change-viewed").check()
        await expect(card.locator(".change-preview")).to_be_hidden(timeout=SLOW)
        bar_stays = await card.locator("div.decision-bar").is_visible()
        after = " ".join((await reviewed.inner_text()).split())
        await card.locator("input.change-viewed").uncheck()
        await expect(card.locator(".change-preview")).to_be_visible(timeout=SLOW)
        again = await card.locator(".change-preview .diff-line").count()
        back = " ".join((await reviewed.inner_text()).split())
        proof(
            "M49",
            "Viewed folds a change's preview and counts it (Reviewed 1 of 2), the bar stays, and "
            "taking the mark off opens the preview again and counts it out",
            "0 of 2" in before
            and "1 of 2" in after
            and bar_stays
            and lines > 0
            and again == lines
            and "0 of 2" in back,
            f"{before} then {after}",
        )
        # A mark set on the review page is the list's mark too.
        await open_review(w, f"plan:{made['plan']}")
        await w.page.locator("#review-viewed").check()
        await w.page.locator("#review-back").click()
        await expect(w.page.locator("#needs-reviewed")).to_contain_text("1 of 2", timeout=SLOW)
        folded = await plan_card(w.page, made["plan"]).get_attribute("data-viewed")
        proof(
            "M52",
            "a mark set with Viewed on the review page is the list's: the plan is counted as "
            "reviewed (Reviewed 1 of 2) and its card is marked viewed",
            folded == "true",
            "1 of 2",
        )
    proof(
        "M49",
        "a project with nothing waiting shows no change and says so in a few words, not a "
        "sentence (replaced by M52: Nothing to review)",
        rows_when_empty == 0 and 0 < len(quiet.split()) < 6,
        quiet,
    )


async def a_change_that_changes_is_not_viewed_any_more() -> None:
    """A change that was marked Viewed and then changes (an agent edits an item of the plan) loses
    its mark, opens again and says so."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic, story, _ = await make_tree(agent)
        made = await a_plan(agent, epic, story, "Card payments")
        form = (await the_whole_plan(agent, made["plan"]))["tree"][1]
        async with browser_page(project.address, token=project.key, name=None) as w:
            await the_needs_page(w)
            card = plan_card(w.page, made["plan"])
            await expect(card).to_have_count(1, timeout=SLOW)
            box = card.locator("input.change-viewed")
            await expect(box).to_be_enabled(timeout=SLOW)
            await box.check()
            await expect(card.locator(".change-preview")).to_be_hidden(timeout=SLOW)
            reviewed = w.page.locator("#needs-reviewed")
            await expect(reviewed).to_contain_text("1 of 1", timeout=SLOW)
            node = await ok(agent, "get_node", node=form["ref"])
            await ok(
                agent,
                "update_node",
                node=form["ref"],
                expected_version=node["version"],
                title="Build the card form with a date picker",
            )
            await expect(box).not_to_be_checked(timeout=SLOW)
            await expect(card.locator(".change-preview")).to_be_visible(timeout=SLOW)
            await expect(reviewed).to_contain_text("0 of 1", timeout=SLOW)
            chip = card.locator(".change-changed")
            await expect(chip).to_be_visible(timeout=SLOW)
            said = " ".join((await chip.inner_text()).split())
            await open_review(w, f"plan:{made['plan']}")
            on_the_page = " ".join((await review_page(w.page).inner_text()).split())
            proof(
                "M49",
                "a change marked Viewed that then changes (an agent edits one of its items) loses "
                "its mark by itself, opens again, says Changed since viewed (a label, not a "
                "sentence), shows the new words on its review page, and the count of what was "
                "reviewed goes down",
                said == "Changed since viewed"
                and "Build the card form with a date picker" in on_the_page,
                "Reviewed 1 of 1, then 0 of 1",
            )


async def keys_on_the_list_move_open_and_never_decide() -> None:
    """The keys of the list move, open the review page and mark viewed, and put the focus on the
    buttons; only a press of a button decides."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic, story, _ = await make_tree(agent)
        first = await a_plan(agent, epic, story, "Card payments")
        second = await a_big_plan(agent, epic, "Everything for the launch")
        async with browser_page(project.address, token=project.key, name=None) as w:
            await the_needs_page(w)
            page = w.page
            cards = page.locator("li.needs-row")
            await expect(cards).to_have_count(2, timeout=SLOW)
            order = await cards.evaluate_all("els => els.map(e => e.dataset.change)")
            where = "() => document.activeElement?.closest('li.needs-row')?.dataset.change ?? null"
            press = page.keyboard.press
            for number in (first["plan"], second["plan"]):
                await expect(plan_card(page, number).locator("input.change-viewed")).to_be_enabled(
                    timeout=SLOW
                )
            await press("j")
            after_j = await page.evaluate(where)
            await press("j")
            after_jj = await page.evaluate(where)
            await press("k")
            after_k = await page.evaluate(where)
            await press("j")
            await press("v")
            big = plan_card(page, second["plan"])
            await expect(big.locator("input.change-viewed")).to_be_checked(timeout=SLOW)
            await expect(page.locator("#needs-reviewed")).to_contain_text("1 of 2", timeout=SLOW)
            await press("v")
            await expect(big.locator("input.change-viewed")).not_to_be_checked(timeout=SLOW)
            await press("a")
            on_a = await page.evaluate("document.activeElement?.dataset.verb ?? ''")
            await press("r")
            on_r = await page.evaluate("document.activeElement?.dataset.verb ?? ''")
            still = await ok(agent, "list_plans", status="proposed")
            await press("o")
            await expect(page.locator("#review-page")).to_have_attribute(
                "data-change", f"plan:{second['plan']}", timeout=SLOW
            )
            opened = await page.evaluate("location.hash")
            proof(
                "M49",
                "the keys j and k move between changes, v marks one viewed (folding its "
                "preview) and counts it, a and r put the focus on its Approve and Reject "
                "buttons, o opens its review page, and none of them decides anything: both "
                "plans are still waiting (e opened every fold on the card, which has none now: "
                "replaced by M52)",
                order == [f"plan:{first['plan']}", f"plan:{second['plan']}"]
                and (after_j, after_jj, after_k) == (order[0], order[1], order[0])
                and on_a == "approve"
                and on_r == "reject"
                and still["total"] == 2
                and "review" in opened,
                f"{after_j}, {after_jj}, {after_k}",
            )

            # The keys of the review page move on and back, and never decide.
            await press("[")
            await expect(page.locator("#review-page")).not_to_have_attribute(
                "data-change", f"plan:{second['plan']}", timeout=SLOW
            )
            await press("]")
            await expect(page.locator("#review-page")).to_have_attribute(
                "data-change", f"plan:{second['plan']}", timeout=SLOW
            )
            await press("a")
            on_approve = await page.evaluate("document.activeElement?.dataset.verb ?? ''")
            await press("r")
            on_reject = await page.evaluate("document.activeElement?.dataset.verb ?? ''")
            await press("v")
            await expect(page.locator("#review-viewed")).to_be_checked(timeout=SLOW)
            await press("u")
            await expect(page.locator("#needs-reviewed")).to_contain_text("1 of 2", timeout=SLOW)
            waiting = await ok(agent, "list_plans", status="proposed")
            proof(
                "M52",
                "on the review page ] and [ go to the next and the previous change, a and r put "
                "the focus on Approve and Reject, v marks it viewed (the list counts it), u goes "
                "back to the list, and none of them decides anything",
                on_approve == "approve" and on_reject == "reject" and waiting["total"] == 2,
                f"{on_approve}, {on_reject}",
            )

            # A press of the button is the decision: the plan is approved by the person, as one
            # decision, and the page moves on to the next change.
            small = plan_card(page, first["plan"])
            await small.locator("button.verdict[data-verb='approve']").click()
            await expect(small).to_have_count(0, timeout=SLOW)
            decided = await the_whole_plan(agent, first["plan"])
            nodes = [await ok(agent, "get_node", node=one["ref"]) for one in decided["tree"]]
            proof(
                "M49",
                "pressing the Approve button on the card approves the plan as the signed-in "
                "person: every item of it is free and its change leaves the list",
                decided["state"] == "approved"
                and decided["decided_by"] == owner.name
                and all(not node["held_by"] for node in nodes),
                f"approved by {owner.name}",
            )


async def a_move_is_lines_taken_out_and_put_in() -> None:
    """A suggested move is drawn on its review page as a diff: the reason as the message, the new
    parent with what moves as added lines (and two of its own stories as context), and the old
    parent with the same work as removed lines, equal to what list_placements says."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        misc = await ok(agent, "create_node", kind="epic", title="Misc")
        billing = await ok(agent, "create_node", kind="epic", title="Billing")
        names = ("Receipts", "Invoices", "Statements")
        kept = [
            await ok(agent, "create_node", kind="story", title=name, parent=billing["ref"])
            for name in names
        ]
        loose = [
            await ok(agent, "create_node", kind="story", title=name, parent=misc["ref"])
            for name in ("Refund screen", "Refund email", "Refund limits")
        ]
        reason = "These all concern refunds on card payments."
        made = await ok(
            agent,
            "propose_placement",
            items=[one["ref"] for one in loose],
            parent=billing["ref"],
            reason=reason,
        )
        await reindexed(agent)
        (listed,) = (await ok(agent, "list_placements", status="proposed"))["items"]
    moved = [one["ref"] for one in listed["items"]]
    lowest = min(math.floor(s["similarity"] * 100) for s in listed["scores"])
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_needs_page(w)
        card = page.locator(f"li.needs-row[data-change='placement:{made['id']}']")
        await expect(card).to_have_count(1, timeout=SLOW)
        impact = (await card.locator("[data-fact='impact']").inner_text()).strip()
        await open_review(w, f"placement:{made['id']}")
        scope = review_page(page)
        added = await lines_of(scope, ITEM_LINES, len(moved))
        removed = await lines_of(scope, REMOVED_LINES, len(moved))
        context = await lines_of(scope, CONTEXT_LINES)
        written = " ".join((await scope.locator(".change-diff").inner_text()).split())
        scores = await scope.locator(".diff-score[data-ref]").all_inner_texts()
        proof(
            "M49",
            "a suggested move is drawn on its review page as a diff: the proposer's reason, the "
            "epic it goes under with two of its own stories as context and each moved story as "
            "an added line that says where it comes from, the epic it leaves with the same "
            "stories as removed lines, all equal to what list_placements says, and the bar "
            "after the last line; the card says how many it moves as a fact (Moves 3), and the "
            "lowest score is on the page as a percent, where the status letter R was "
            "(replaced by M52)",
            added == moved
            and removed == moved
            and context == [kept[0]["ref"], kept[1]["ref"]]
            and reason in written
            and all(one["from_parent"] == misc["ref"] for one in listed["items"])
            and misc["title"] in written
            and await the_diff_is_before_the_decision(scope)
            and impact == f"Moves {len(moved)}"
            and f"{lowest}%" in [" ".join(s.split()) for s in scores],
            f"{len(added)} added, {len(removed)} removed, {len(context)} context, {impact}",
        )


async def review_on_the_page() -> None:
    await the_page_shows_the_whole_plan()
    await a_move_is_lines_taken_out_and_put_in()
    await viewed_and_empty()
    await a_change_that_changes_is_not_viewed_any_more()
    await keys_on_the_list_move_open_and_never_decide()
