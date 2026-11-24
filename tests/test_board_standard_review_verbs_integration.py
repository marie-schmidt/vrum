"""M52 on the board: what the verbs of the standard bar do, and the choices of the review page.

Each verb records exactly what the server says, as the person who is signed in: an answer with its
comment, a yes or no, free words, a plan approved, rejected or asked to change with the revision the
person was shown, a suggested move accepted or rejected with a reason, a note confirmed or
withdrawn, a check ticked with how it was checked, a gate lifted, work dropped or blocked with its
reason. Keeping an answer as a decision records one note naming the person, and a refusal of that
note says the answer stood. Unified and Split, Changes and Whole plan each change the lines on the
review page. Previous and next follow the order of the list in every way of grouping, sizes read
"Size S", a viewer decides nothing, a name sits on the baseline of the words beside it, and the
pages fit 390 pixels and read in both themes. Every test makes an account and a project of its
own."""

import json
from collections import Counter
from typing import Any

import anyio
import pytest
from mcp import Client
from playwright.async_api import Page, Route, expect

from tests.board_helpers import (
    SLOW,
    browser_page,
    hold_back_news,
    on_page,
    open_review,
    sentences,
)
from tests.board_layout import SCREENS, Screen, blank_page, contrast, layout_context, settle
from tests.helpers import connect, ok
from tests.test_board_needs_integration import leave_waiting, no_overflow, rows_of
from tests.test_board_plan_review_integration import a_revision, asked
from tests.test_board_review_assist_integration import three_proposals
from tests.test_board_standard_review_integration import (
    NARROW,
    card,
    change_of,
    full_world,
    open_routine,
    rows,
    the_list,
)
from tests.test_checks_integration import as_person
from tests.test_plan_review_integration import drafted, read
from tests.world import join_project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def notes_of(agent: Client) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = (await ok(agent, "list_notes"))["items"]
    return found


async def asks_of(agent: Client, node: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = (await ok(agent, "get_node", node=node))["asks"]
    return found


async def dialog_submit(page: Page, dialog: str, words: str) -> None:
    box = page.locator(f"dialog{dialog}")
    await box.locator("textarea").first.fill(words)
    await box.locator("button[type=submit]").click()
    await expect(box).to_have_count(0, timeout=SLOW)


# -- questions ---------------------------------------------------------------------------------


async def test_questions_are_answered_by_choice_yes_or_no_or_words_with_the_comment() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        waiting = await leave_waiting(agent, project, owner)
        before = len(await notes_of(agent))
        page = w.page
        await the_list(page, w, agent)
        # A choice, with a comment typed in the box beside the bar.
        choice = card(page, f"ask:{waiting.numbers['choice']}")
        await choice.locator("button.decision-comment").click()
        await choice.locator("div.decision-comment-box textarea").fill("Their fees are lower.")
        await choice.locator("button.verdict[data-answer='Adyen']").click()
        await expect(choice).to_have_count(0, timeout=SLOW)
        (first,) = await asks_of(agent, waiting.choice["ref"])
        assert (first["answer"], first["comment"], first["answered_by"]) == (
            "Adyen",
            "Their fees are lower.",
            "Priya Raman",
        )
        # An approval: Reject… asks for the reason in a dialog and sends no with it.
        approval = card(page, f"ask:{waiting.numbers['approval']}")
        await approval.locator("button.verdict[data-verb=reject]").click()
        await dialog_submit(page, "#reject-dialog", "Not on a Friday.")
        await expect(approval).to_have_count(0, timeout=SLOW)
        (second,) = await asks_of(agent, waiting.approval["ref"])
        assert (second["answer"], second["comment"], second["answered_by"]) == (
            "no",
            "Not on a Friday.",
            "Priya Raman",
        )
        # Free words, from the dialog the Answer… button opens.
        free = card(page, f"ask:{waiting.numbers['free']}")
        await free.locator("button.verdict[data-verb=answer]").click()
        await page.locator("#answer-ask textarea#ask-answer").fill("Thank you for your order.")
        await page.locator("#answer-ask button[type=submit]").click()
        await expect(free).to_have_count(0, timeout=SLOW)
        (third,) = await asks_of(agent, waiting.free["ref"])
        assert (third["answer"], third["answered_by"]) == (
            "Thank you for your order.",
            "Priya Raman",
        )
        # Nothing was recorded as a decision: the box was never ticked.
        assert len(await notes_of(agent)) == before
        assert w.problems == []


async def test_an_answer_is_kept_as_a_decision_once_when_asked_and_never_otherwise() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        waiting = await leave_waiting(agent, project, owner)
        page = w.page
        await the_list(page, w, agent)
        number = waiting.numbers["choice"]
        choice = card(page, f"ask:{number}")
        # Ticked, with no comment: one note, naming the person, on the work, with a reason that
        # says where it came from.
        await choice.locator("button.decision-comment").click()
        await expect(choice.locator("input#answer-remember")).not_to_be_checked()
        await choice.locator("input#answer-remember").check()
        await choice.locator("button.verdict[data-answer='Stripe']").click()
        await expect(choice).to_have_count(0, timeout=SLOW)
        kept = [n for n in await notes_of(agent) if n["text"].startswith("Q: ")]
        assert len(kept) == 1
        (note,) = kept
        assert (
            note["text"].endswith("\nA: Stripe")
            and "Which payment provider" in note["text"].splitlines()[0]
        )
        assert note["decided_by"] == "Priya Raman" and note["nodes"] == [waiting.choice["ref"]]
        assert note["why"] == f"Answered Q-{number}." and note["status"] == "unconfirmed"
        assert note["recorded_by"] == "Priya Raman" and note["recorded_by_kind"] == "person"
        # With a comment, the comment is the reason.
        free = card(page, f"ask:{waiting.numbers['free']}")
        await free.locator("button.decision-comment").click()
        await free.locator("div.decision-comment-box textarea").fill("The legal team asked for it.")
        await free.locator("input#answer-remember").check()
        await free.locator("button.verdict[data-verb=answer]").click()
        await page.locator("#answer-ask textarea#ask-answer").fill("Receipts are final.")
        await page.locator("#answer-ask button[type=submit]").click()
        await expect(free).to_have_count(0, timeout=SLOW)
        kept = [n for n in await notes_of(agent) if n["text"].startswith("Q: ")]
        assert len(kept) == 2
        newest = max(kept, key=lambda n: n["id"])
        assert newest["why"] == "The legal team asked for it." and newest["text"].endswith(
            "A: Receipts are final."
        )
        # Not ticked: the approval is answered and no note is made.
        approval = card(page, f"ask:{waiting.numbers['approval']}")
        await approval.locator("button.verdict[data-verb=approve]").click()
        await expect(approval).to_have_count(0, timeout=SLOW)
        assert len([n for n in await notes_of(agent) if n["text"].startswith("Q: ")]) == 2
        assert w.problems == []


async def test_a_refused_note_leaves_the_answer_standing_and_says_so_in_the_servers_words() -> None:
    # The answer is taken. The note that keeps it is sent with a reason that looks like a key, as a
    # person pasting one into the comment would send it, and the server refuses it in its own words.
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        waiting = await leave_waiting(agent, project, owner)
        before = len(await notes_of(agent))
        page = w.page

        async def with_a_secret(route: Route) -> None:
            body = route.request.post_data_json or {}
            body["why"] = "vrk_" + "q" * 36
            await route.continue_(post_data=json.dumps(body))

        await page.route("**/tool/add_note", with_a_secret)
        await the_list(page, w, agent)
        number = waiting.numbers["choice"]
        choice = card(page, f"ask:{number}")
        await choice.locator("button.decision-comment").click()
        await choice.locator("input#answer-remember").check()
        await choice.locator("button.verdict[data-answer='Neither']").click()
        problem = page.locator("#needs-said p.problem")
        await expect(problem).to_have_count(1, timeout=SLOW)
        words = await problem.text_content() or ""
        assert words.startswith("The answer stood, but the decision was not recorded: "), words
        assert len(words) > len("The answer stood, but the decision was not recorded: ")
        (ask,) = await asks_of(agent, waiting.choice["ref"])
        assert ask["answer"] == "Neither" and ask["answered_by"] == "Priya Raman"
        assert len(await notes_of(agent)) == before
        # The words stay until they are dismissed.
        await page.locator("#needs-said button.notice-close").click()
        await expect(page.locator("#needs-said p.problem")).to_have_count(0)
        assert w.problems == []


# -- a plan, a move, a note and a check -------------------------------------------------


async def test_a_plan_is_approved_asked_to_change_and_rejected_as_the_signed_in_person() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        approve, refs = await drafted(agent, "To approve")
        changing, _ = await drafted(agent, "To change")
        rejecting, _ = await drafted(agent, "To reject")
        page = w.page
        await the_list(page, w, agent)
        # Approve, on the card: the whole plan is freed, and it is the person who decided it.
        await card(page, f"plan:{approve}").locator("button.verdict[data-verb=approve]").click()
        await expect(card(page, f"plan:{approve}")).to_have_count(0, timeout=SLOW)
        done = await read(agent, approve)
        assert (done["state"], done["decided_by"], done["revision"]) == (
            "approved",
            "Priya Raman",
            1,
        )
        free = {n["ref"] for n in (await ok(agent, "ready_work", limit=200))["items"]}
        assert refs["t1"] in free
        # Request changes, with the summary: the plan stays held and the agent is to revise it.
        await card(page, f"plan:{changing}").locator("button.verdict[data-verb=changes]").click()
        box = page.locator("dialog#changes-dialog")
        await box.locator("textarea#changes-summary").fill("Please size every task.")
        await box.locator("button[type=submit]").click()
        await expect(box).to_have_count(0, timeout=SLOW)
        waiting = await read(agent, changing)
        assert waiting["state"] == "changes_requested"
        assert [(f["by"], f["summary"], f["revision"]) for f in waiting["feedback"]] == [
            ("Priya Raman", "Please size every task.", 1)
        ]
        await expect(page.locator(f"li.waiting-row[data-plan='{changing}']")).to_have_count(
            1, timeout=SLOW
        )
        # Reject, with a reason the dialog needs: a blank one is refused in the server's words.
        await card(page, f"plan:{rejecting}").locator("button.verdict[data-verb=reject]").click()
        box = page.locator("dialog#reject-dialog")
        await box.locator("button[type=submit]").click()
        await expect(box.locator("p.problem")).not_to_be_empty(timeout=SLOW)
        refusal = (await box.locator("p.problem").text_content() or "").strip()
        assert refusal, refusal
        await dialog_submit(page, "#reject-dialog", "Wrong scope.")
        rejected = await read(agent, rejecting)
        assert (rejected["state"], rejected["decided_by"], rejected["reason"]) == (
            "rejected",
            "Priya Raman",
            "Wrong scope.",
        )
        assert w.problems == []


async def test_a_plan_is_decided_from_its_page_and_a_decision_on_an_old_revision_is_refused() -> (
    None
):
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent, "Winter plan")
        page = w.page
        await the_list(page, w, agent)
        await open_review(page, f"plan:{number}")
        await hold_back_news(page)
        # Somebody asks for changes and the agent revises, which the page does not hear of.
        asked(project, owner, number, 1, "Size it.")
        await a_revision(agent, number, refs, "Sized.", t1={"size": "xl"})
        await page.locator("#review-bar button.verdict[data-verb=approve]").click()
        problem = page.locator("#review-bar p.decision-problem")
        await expect(problem).not_to_be_empty(timeout=SLOW)
        words = (await problem.text_content() or "").strip()
        assert "revision" in words.lower(), words
        assert (await read(agent, number))["state"] == "proposed"
        assert w.problems == []


async def test_a_move_a_note_and_a_check_are_decided_as_the_signed_in_person() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        waiting = await leave_waiting(agent, project, owner)
        _, routine, other = await three_proposals(agent)
        page = w.page
        await the_list(page, w, agent)
        await open_routine(page)
        # Approve a move: the item goes under the new parent.
        number = routine[0]
        (move,) = [
            p
            for p in (await ok(agent, "list_placements", status="proposed"))["items"]
            if p["id"] == number
        ]
        await card(page, f"placement:{number}").locator("button.verdict[data-verb=approve]").click()
        await expect(card(page, f"placement:{number}")).to_have_count(0, timeout=SLOW)
        for one in move["items"]:
            assert (await ok(agent, "get_node", node=one["ref"]))["parent"] == move["parent"]
        # Reject a move with a reason: nothing moves and the reason is kept.
        await (
            card(page, f"placement:{other[0]}").locator("button.verdict[data-verb=reject]").click()
        )
        await dialog_submit(page, "#reject-dialog", "Mail is not money.")
        (gone,) = [
            p
            for p in (await ok(agent, "list_placements", status="rejected"))["items"]
            if p["id"] == other[0]
        ]
        assert gone["decided_by"] == "Priya Raman"
        assert gone["decision_reason"] == "Mail is not money."
        # A note: Approve confirms it as the person named in the dialog; Reject withdraws it with
        # the reason.
        node = waiting.noted["ref"]
        await card(page, f"note:{node}").locator("button.verdict[data-verb=approve]").click()
        confirm = page.locator("dialog").filter(has=page.locator("input#confirm-person"))
        await expect(confirm.locator("input#confirm-person")).to_have_value("Priya Raman")
        await confirm.locator("button[type=submit]").click()
        await expect(card(page, f"note:{node}")).to_have_count(0, timeout=SLOW)
        (confirmed,) = [n for n in await notes_of(agent) if "webhook" in n["text"]]
        assert (confirmed["status"], confirmed["confirmed_by"]) == ("confirmed", "Priya Raman")
        # A check: Approve ticks it as the person, with how it was checked.
        checked = waiting.checked["ref"]
        row = card(page, f"check:{checked}")
        await row.locator("button.decision-comment").click()
        await row.locator("div.decision-comment-box textarea").fill("Tried it on a phone.")
        await row.locator("button.verdict[data-verb=approve]").click()
        await expect(row).to_have_count(0, timeout=SLOW)
        ticks = {c["text"]: c for c in (await ok(agent, "get_node", node=checked))["checks"]}
        phone = ticks["A person has tried it on a phone"]
        assert (
            phone["checked"] is True
            and phone["checked_by"] == "Priya Raman"
            and phone["evidence"] == "Tried it on a phone."
        )
        assert w.problems == []


async def test_notes_are_withdrawn_with_a_reason_and_several_notes_have_a_bar_each() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        waiting = await leave_waiting(agent, project, owner)
        node = waiting.noted["ref"]
        await ok(
            agent,
            "add_note",
            text="The retries are capped at five",
            why="Read the code",
            nodes=[node],
        )
        await ok(
            agent, "add_note", text="Events arrive twice", why="Saw it in the logs", nodes=[node]
        )
        page = w.page
        await the_list(page, w, agent)
        row = card(page, f"note:{node}")
        # Several notes: no bar for the row, one under each note's line.
        await expect(row.locator(":scope > div.decision")).to_have_count(0)
        await expect(row.locator("li.preview-note div.decision-bar")).to_have_count(3)
        notes = await notes_of(agent)
        target = [n for n in notes if n["text"].startswith("Events arrive")][0]
        await row.locator(
            f"li.preview-note[data-note='{target['id']}'] button.verdict[data-verb=reject]"
        ).click()
        await dialog_submit(page, "#reject-dialog", "That was a test run.")
        (gone,) = [n for n in await notes_of(agent) if n["id"] == target["id"]]
        assert gone["status"] == "withdrawn" and gone["status_reason"] == "That was a test run."
        # The other two are still there, each with its bar, on the card and on the review page.
        await expect(row.locator("li.preview-note")).to_have_count(2, timeout=SLOW)
        await open_review(page, f"note:{node}")
        await expect(page.locator("#review-page .review-note")).to_have_count(2)
        await expect(page.locator("#review-page .review-note div.decision-bar")).to_have_count(2)
        await expect(page.locator("#review-bar")).to_have_count(0)
        assert w.problems == []


async def test_a_refused_approval_and_a_lapsed_claim_are_lifted_blocked_or_dropped() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic = await ok(agent, "create_node", actor="x", kind="epic", title="Pilot")
        story = await ok(
            agent, "create_node", actor="x", kind="story", title="Launch", parent=epic["ref"]
        )
        tasks = [
            await ok(
                agent, "create_node", actor="x", kind="task", title=f"Step {n}", parent=story["ref"]
            )
            for n in range(1, 5)
        ]
        gates = []
        for task in tasks[:2]:
            made = await ok(
                agent,
                "add_gate",
                node=task["ref"],
                kind="approval",
                question=f"May {task['title']} start?",
            )
            gates.append(int(made["id"]))
            ask = [
                r
                for r in rows_of(await ok(agent, "needs_a_person"))
                if r["ask"] and r["node"] == task["ref"]
            ][0]["ask"]["id"]
            assert as_person(owner, project, "answer_ask", ask=ask, answer="no").status == 200
        for task in tasks[2:]:
            await ok(agent, "claim_node", node=task["ref"], lease_seconds=1)
        await anyio.sleep(1.5)
        for task in tasks[2:]:
            from tests.world import make_key

            async with connect(
                project.mcp_url, make_key(project, f"second-{task['number']}")
            ) as second:
                await ok(second, "claim_node", node=task["ref"], lease_seconds=1)
        await anyio.sleep(1.5)
        page = w.page
        await the_list(page, w, agent)
        # Lift the first gate: the work is free again, and the reason is kept with it.
        await card(page, f"gate:{gates[0]}").locator("button.verdict[data-verb=lift]").click()
        await dialog_submit(page, "#lift-gate", "The audit is done.")
        await expect(card(page, f"gate:{gates[0]}")).to_have_count(0, timeout=SLOW)
        lifted = [
            g
            for g in (await ok(agent, "get_node", node=tasks[0]["ref"]))["gates"]
            if g["id"] == gates[0]
        ][0]
        assert (
            lifted["state"] == "removed"
            and lifted["removed_by"] == "Priya Raman"
            and lifted["removed_reason"] == "The audit is done."
        )
        # Drop the work of the second: dropped, and the reason is its handoff note.
        await card(page, f"gate:{gates[1]}").locator("button.verdict[data-verb=drop]").click()
        await dialog_submit(page, "#drop-dialog", "Not needed any more.")
        dropped = await ok(agent, "get_node", node=tasks[1]["ref"])
        assert dropped["status"] == "dropped"
        history = (await ok(agent, "node_history", node=tasks[1]["ref"]))["items"]
        notes = [e for e in history if e["action"] == "handoff"]
        assert (
            notes
            and notes[-1]["after"]["note"] == "Not needed any more."
            and notes[-1]["actor"] == "Priya Raman"
        )
        # A claim that ran out: block it with a reason (a blank one is refused in the server's
        # words), drop the other.
        row = card(page, f"lapse:{tasks[2]['ref']}")
        await row.locator("button.verdict[data-verb=block]").click()
        box = page.locator("dialog#block-dialog")
        await box.locator("button[type=submit]").click()
        await expect(box.locator("p.problem")).not_to_be_empty(timeout=SLOW)
        await dialog_submit(page, "#block-dialog", "Waiting on the supplier.")
        assert (await ok(agent, "get_node", node=tasks[2]["ref"]))["status"] == "blocked"
        await (
            card(page, f"lapse:{tasks[3]['ref']}").locator("button.verdict[data-verb=drop]").click()
        )
        await dialog_submit(page, "#drop-dialog", "Nobody wants it.")
        assert (await ok(agent, "get_node", node=tasks[3]["ref"]))["status"] == "dropped"
        assert w.problems == []


# -- the choices of the review page ---------------------------------------------------


async def lines_of(page: Page) -> Counter[str]:
    found: list[str] = await page.locator("#review-diff .diff-line").evaluate_all(
        """els => els.filter((e) => ['+', '-', '~'].includes(e.dataset.sign))
            .map((e) => [e.dataset.sign, e.dataset.ref ?? '', e.dataset.part ?? ''].join('|'))"""
    )
    return Counter(found)


async def counts(page: Page) -> tuple[int, int]:
    return (
        await page.locator("#review-diff .diff-line").count(),
        await page.locator("#review-diff .diff-side").count(),
    )


async def test_unified_split_changes_and_whole_plan_each_change_the_lines_of_a_plan() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent, "Winter plan")
        asked(project, owner, number, 1, "Size it and drop the covers.")
        await a_revision(
            agent,
            number,
            refs,
            "Sized and trimmed.",
            t1={"size": "xl"},
            s2={"title": "Weather covers (cheaper)"},
            without=["t3"],
        )
        plan = await read(agent, number)
        assert plan["revision"] == 2 and plan["changes"]
        page = w.page
        await the_list(page, w, agent)
        await open_review(page, f"plan:{number}")
        diff = page.locator("#review-diff")
        mode = page.locator("#review-mode")
        view = page.locator("#review-view")
        # A revised plan opens as what changed, unified.
        await expect(diff).to_have_attribute("data-mode", "delta")
        await expect(diff).to_have_attribute("data-view", "unified")
        await expect(mode.locator("button[data-mode=delta]")).to_have_attribute(
            "aria-pressed", "true"
        )
        await expect(view.locator("button[data-view=unified]")).to_have_attribute(
            "aria-pressed", "true"
        )
        delta_unified, delta_signed = await counts(page), await lines_of(page)
        assert delta_unified[1] == 0 and delta_unified[0] > 0 and delta_signed
        # Split: the same signed lines, now in two sides (Before and After).
        await view.locator("button[data-view=split]").click()
        await expect(diff).to_have_attribute("data-view", "split")
        assert "view=split" in await page.evaluate("location.hash")
        await expect(diff.locator(".diff-side")).to_have_count(2)
        assert await lines_of(page) == delta_signed
        old, new = (
            diff.locator(".diff-side[data-side=old]"),
            diff.locator(".diff-side[data-side=new]"),
        )
        assert {"-"} <= set(
            await old.locator(".diff-line").evaluate_all("els => els.map((e) => e.dataset.sign)")
        )
        assert "-" not in set(
            await old.locator(".diff-line[data-sign='+']").evaluate_all(
                "els => els.map((e) => e.dataset.sign)"
            )
        )
        assert (
            await old.locator(".diff-line[data-sign='+'], .diff-line[data-sign='~']").count() == 0
        )
        assert await new.locator(".diff-line[data-sign='-']").count() == 0
        # What was added has an empty old side and what was removed an empty new side.
        for added in plan["changes"]:
            if added["change"] == "added":
                assert await old.locator(f".diff-line[data-ref='{added['ref']}']").count() == 0
            if added["change"] == "removed":
                assert await new.locator(f".diff-line[data-ref='{added['ref']}']").count() == 0
                await expect(
                    old.locator(f".diff-line[data-sign='-'][data-ref='{added['ref']}']")
                ).to_have_count(1)
        # Whole plan: every item of the revision, split or unified.
        await mode.locator("button[data-mode=whole]").click()
        await expect(diff).to_have_attribute("data-mode", "whole")
        assert "mode=whole" in await page.evaluate("location.hash")
        whole_split = await counts(page)
        assert whole_split[1] == 2
        plus = await lines_of(page)
        await view.locator("button[data-view=unified]").click()
        await expect(diff).to_have_attribute("data-view", "unified")
        await expect(diff.locator(".diff-side")).to_have_count(0)
        whole_unified = await counts(page)
        assert whole_unified[1] == 0 and await lines_of(page) == plus
        assert (
            await diff.locator(".diff-line.diff-item-line[data-sign='+'][data-ref]").evaluate_all(
                "els => els.map((e) => e.dataset.ref)"
            )
        ) == [one["ref"] for one in plan["tree"]]
        # The keys do the same: m and s.
        await page.locator("#review-title").focus()
        await page.keyboard.press("m")
        await expect(diff).to_have_attribute("data-mode", "delta")
        await page.keyboard.press("s")
        await expect(diff).to_have_attribute("data-view", "split")
        await page.keyboard.press("s")
        await expect(diff).to_have_attribute("data-view", "unified")
        # The first revision has only the whole plan and no choice between the two.
        await page.locator("#review-revision").select_option("1")
        await expect(page.locator("#review-mode")).to_have_count(0)
        await expect(page.locator("#review-diff")).to_have_attribute("data-mode", "whole")
        assert w.problems == []


async def test_unified_and_split_change_the_lines_of_a_move_and_the_list_has_no_choice() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        world = await full_world(agent, project, owner)
        page = w.page
        await the_list(page, w, agent)
        await open_routine(page)
        change = f"placement:{world.other[0]}"
        await open_review(page, change)
        diff = page.locator("#review-diff")
        await expect(diff).to_have_attribute("data-view", "unified")
        await expect(diff.locator(".diff-line[data-sign='+'][data-ref]")).to_have_count(
            1, timeout=SLOW
        )
        unified, signed = await counts(page), await lines_of(page)
        assert unified[1] == 0 and signed
        await page.locator("#review-view button[data-view=split]").click()
        await expect(diff.locator(".diff-side")).to_have_count(2)
        assert await lines_of(page) == signed
        assert (
            await diff.locator(".diff-side[data-side=old] .diff-line[data-sign='+']").count() == 0
        )
        assert (
            await diff.locator(".diff-side[data-side=new] .diff-line[data-sign='-']").count() == 0
        )
        # A question has no such choice, since there is nothing to lay out.
        await w.open("#/needs")
        await open_review(page, f"ask:{world.waiting.numbers['choice']}")
        await expect(page.locator("#review-view, #review-mode")).to_have_count(0)
        # And the list has none (Unified and Split, Changes and Whole plan are on the review page).
        await w.open("#/needs")
        await on_page(page, "needs")
        await expect(
            page.locator("#needs-page [data-view], #needs-page [data-mode]")
        ).to_have_count(0)
        assert w.problems == []


# -- previous and next -----------------------------------------------------------------


async def test_previous_and_next_follow_the_order_of_the_list_in_each_way_of_grouping() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        await full_world(agent, project, owner)
        page = w.page
        for by in ("level", "topic", "decision"):
            await the_list(page, w, agent, by)
            await open_routine(page)
            order = await page.locator("#needs-body li.needs-row").evaluate_all(
                "els => els.map((e) => e.dataset.change)"
            )
            assert len(order) == len(set(order)) > 5
            await open_review(page, order[0])
            seen = [order[0]]
            await expect(page.locator("#review-position")).to_have_text(f"1 of {len(order)}")
            await expect(page.locator("span#review-prev.off")).to_have_count(1)
            while True:
                nxt = page.locator("a#review-next")
                if not await nxt.count():
                    break
                await nxt.click()
                await expect(page.locator("#review-page")).not_to_have_attribute(
                    "data-change", seen[-1]
                )
                seen.append(str(await page.locator("#review-page").get_attribute("data-change")))
                await expect(page.locator("#review-position")).to_have_text(
                    f"{len(seen)} of {len(order)}"
                )
            assert seen == order, (by, seen, order)
            # The group choice is kept in the address, so the way back is the list as it was.
            if by != "level":
                assert f"group={by}" in await page.evaluate("location.hash")
            # Backwards with the keys.
            await page.locator("#review-title").focus()
            await page.keyboard.press("[")
            await expect(page.locator("#review-page")).to_have_attribute("data-change", order[-2])
            await page.keyboard.press("k")
            await expect(page.locator("#review-page")).to_have_attribute("data-change", order[-3])
            await page.keyboard.press("]")
            await expect(page.locator("#review-page")).to_have_attribute("data-change", order[-2])
            await page.keyboard.press("u")
            await on_page(page, "needs")
            assert (await page.evaluate("location.hash")) == (
                "#/needs" if by == "level" else f"#/needs?group={by}"
            )
        assert w.problems == []


async def test_a_decision_on_the_review_page_opens_the_next_change_or_the_list() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        waiting = await leave_waiting(agent, project, owner)
        page = w.page
        await the_list(page, w, agent)
        order = await page.locator("#needs-body li.needs-row").evaluate_all(
            "els => els.map((e) => e.dataset.change)"
        )
        mine = f"ask:{waiting.numbers['approval']}"
        await open_review(page, mine)
        at = order.index(mine)
        await page.locator("#review-bar button.verdict[data-verb=approve]").click()
        await expect(page.locator("#review-page")).to_have_attribute(
            "data-change", order[at + 1] if at + 1 < len(order) else order[at - 1], timeout=SLOW
        )
        (ask,) = await asks_of(agent, waiting.approval["ref"])
        assert ask["answer"] == "yes" and ask["answered_by"] == owner.name
        assert w.problems == []


# -- sizes -----------------------------------------------------------------------------


async def test_sizes_read_size_s_with_a_hover_card_on_the_list_and_the_review_page() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic = await ok(agent, "create_node", actor="x", kind="epic", title="Sizes")
        story = await ok(
            agent, "create_node", actor="x", kind="story", title="A story", parent=epic["ref"]
        )
        task = await ok(
            agent,
            "create_node",
            actor="x",
            kind="task",
            title="A small task",
            parent=story["ref"],
            size="s",
        )
        await ok(agent, "claim_node", node=task["ref"])
        await ok(
            agent,
            "finish_work",
            node=task["ref"],
            outcome="blocked",
            note="A person?",
            ask={"question": "Is it small?", "approval": True},
        )
        (row,) = rows_of(await ok(agent, "needs_a_person"))
        change = change_of(row)
        page = w.page
        await the_list(page, w, agent)
        chip = card(page, change).locator("[data-fact=size] .chip.size")
        await expect(chip).to_have_text("Size S")
        await expect(chip).to_have_attribute("data-size", "s")
        await chip.focus()
        await expect(page.locator(".hovercard:visible")).to_have_text("Small, worth 2 points")
        await page.keyboard.press("Escape")
        await open_review(page, change)
        for place in (
            "#review-page [data-fact=size] .chip.size",
            "#review-page .state-strip .chip.size",
        ):
            await expect(page.locator(place)).to_have_text("Size S")
        texts = await page.locator(
            "#review-page .chip.size, #needs-body .chip.size"
        ).all_text_contents()
        assert texts and all(t.startswith("Size ") for t in texts), texts
        # A plan's items read the same.
        number, _ = await drafted(agent, "Winter plan")
        await w.open("#/needs")
        await open_review(page, f"plan:{number}")
        lines = await page.locator("#review-diff .diff-item-line .chip.size").all_text_contents()
        assert lines and all(t.startswith("Size ") and len(t.split()) == 2 for t in lines), lines
        assert w.problems == []


# -- a viewer --------------------------------------------------------------------------


async def test_a_viewer_reads_every_kind_and_has_no_verb_no_bar_and_no_comment() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    viewer = join_project(project, "viewer", "Vera Viewer")
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=None, person=viewer, name=None) as w,
    ):
        world = await full_world(agent, project, owner)
        served = await rows(agent)
        page = w.page
        await the_list(page, w, agent)
        await open_routine(page)
        await expect(page.locator(NO_BAR_LIST)).to_have_count(0)
        # A viewer still reads what a question asks and the answers on offer.
        choice = card(page, f"ask:{world.waiting.numbers['choice']}")
        await expect(choice.locator(".preview-question")).to_contain_text("Which payment provider")
        await expect(choice.locator("ul.ask-offered li")).to_have_text(
            ["Stripe", "Adyen", "Neither"]
        )
        for row in served:
            await w.open("#/needs")
            await open_routine(page)
            await open_review(page, change_of(row))
            await expect(page.locator("#review-page section[data-part=changes]")).to_be_visible()
            await expect(page.locator(NO_BAR_REVIEW)).to_have_count(0)
            assert await sentences(page) == []
        # The server refuses a viewer whatever the page offers.
        refused = as_person(
            viewer, project, "answer_ask", ask=world.waiting.numbers["choice"], answer="Stripe"
        )
        assert refused.status >= 400
        assert w.problems == []


# -- a name on the line ----------------------------------------------------------------

NO_BAR_LIST = (
    "#needs-page button.verdict, #needs-page .decision-bar, #needs-page button.decision-comment"
)
NO_BAR_REVIEW = (
    "#review-page button.verdict, #review-bar, button.diff-comment, button.decision-comment"
)

BASELINE = """(selector) => [...document.querySelectorAll(selector)].map((actor) => {
  const name = actor.querySelector('.actor-name') ?? actor;
  const nameBox = name.getBoundingClientRect();
  const sibling = (start, step) => {
    let node = start;
    while ((node = node[step])) {
      if (node.nodeType === 3 && node.textContent.trim()) return node;
      const text = node.nodeType === 1 && node.textContent.trim();
      if (text && !node.querySelector('.actor')) return node;
    }
    return null;
  };
  const near = sibling(actor, 'nextSibling') ?? sibling(actor, 'previousSibling');
  if (!near) return null;
  const range = document.createRange();
  range.selectNodeContents(near);
  const box = range.getClientRects()[0];
  if (!box) return null;
  const word = near.textContent.trim().slice(0, 30);
  return { name: [nameBox.top, nameBox.bottom], text: [box.top, box.bottom], word };
}).filter(Boolean)"""


async def offsets(page: Page, selector: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = await page.evaluate(BASELINE, selector)
    return found


def level_with(found: list[dict[str, Any]]) -> None:
    """The middle of the name's box is within a pixel of the middle of the words beside it."""
    assert found, "no line with a name was found"
    for one in found:
        name, text = sum(one["name"]) / 2, sum(one["text"]) / 2
        assert abs(name - text) <= 1.01, one


async def test_a_name_sits_on_the_baseline_of_the_words_beside_it() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    editor = join_project(project, "editor", "Ed Editor")
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        waiting = await leave_waiting(agent, project, owner)
        number, refs = await drafted(agent, "Winter plan")
        asked(
            project,
            editor,
            number,
            1,
            "Size it.",
            [{"item": refs["t1"], "text": "Which supplier?"}],
        )
        await a_revision(agent, number, refs, "Sized.", t1={"size": "l"})
        page = w.page
        await the_list(page, w, agent)
        # A line of text on the review page: the thread under a line, the recorder of a note,
        # and who asked.
        await open_review(page, f"plan:{number}")
        await expect(page.locator("#review-diff .diff-thread .actor")).to_have_count(
            1, timeout=SLOW
        )
        level_with(await offsets(page, "#review-diff .diff-thread .actor"))
        level_with(await offsets(page, "#review-page .plan-revision-author .actor"))
        await w.open("#/needs")
        await open_review(page, f"note:{waiting.noted['ref']}")
        level_with(await offsets(page, "#review-page .review-note .review-fact .actor"))
        # A name inside a chip is centred with the chip's own text.
        await w.open("#/needs")
        chip = page.locator("#needs-body .change-facts li[data-fact=by]").first
        await expect(chip).to_be_visible()
        centred = await page.evaluate(
            """() => [...document.querySelectorAll('#needs-body li[data-fact=by]')].map((chip) => {
                const box = chip.getBoundingClientRect();
                const name = chip.querySelector('.actor-name').getBoundingClientRect();
                return Math.abs((box.top + box.bottom) / 2 - (name.top + name.bottom) / 2);
            })"""
        )
        assert centred and max(centred) <= 1.01, centred
        # In the Activity list: a person's or agent's name beside the words of what they did.
        await w.open("#/activity")
        await expect(page.locator("#page li.event .event-actor .actor").first).to_be_visible(
            timeout=SLOW
        )
        found = await page.evaluate(
            """() => [...document.querySelectorAll('#page li.event')].slice(0, 12).map((row) => {
                const actor = row.querySelector('.event-actor .actor-name');
                const words = row.querySelector('.event-words');
                if (!actor || !words) return null;
                const range = document.createRange();
                range.selectNodeContents(words);
                const first = range.getClientRects()[0];
                const box = actor.getBoundingClientRect();
                if (!first) return null;
                const mid = (a) => (a.top + a.bottom) / 2;
                return Math.abs(mid(box) - mid(first));
            }).filter((n) => n !== null)"""
        )
        assert found and max(found) <= 1.5, found
        assert w.problems == []


# -- a narrow window and both themes ---------------------------------------------------


async def test_the_list_and_every_review_page_fit_390_pixels() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        world = await full_world(agent, project, owner)
        served = await rows(agent)
        page = w.page
        await page.set_viewport_size(NARROW)
        await the_list(page, w, agent)
        await open_routine(page)
        await no_overflow(page)
        await expect(page.locator("select#needs-rail-select")).to_be_visible()
        await expect(page.locator("nav#needs-rail")).to_be_hidden()
        for by in ("topic", "decision"):
            await the_list(page, w, agent, by)
            await no_overflow(page)
        for row in served:
            await w.open("#/needs")
            await open_routine(page)
            await open_review(page, change_of(row))
            await expect(page.locator("#review-page section[data-part=changes]")).to_be_visible()
            await no_overflow(page)
            if row["plan"] or row["placement"]:
                await page.locator("#review-view button[data-view=split]").click()
                await expect(page.locator("#review-diff .diff-side")).to_have_count(2)
                await no_overflow(page)
        # The bar may wrap on a narrow window, and still every verb is on it and reachable.
        await w.open("#/needs")
        await open_review(page, f"plan:{world.plan}")
        await expect(page.locator("#review-bar button.verdict")).to_have_count(3)
        assert w.problems == []


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("screen", [SCREENS[0], SCREENS[2]], ids=lambda screen: screen.name)
async def test_the_list_and_the_review_pages_read_in_both_themes_and_never_by_colour_alone(
    screen: Screen, theme: str
) -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        world = await full_world(agent, project, owner)
        async with layout_context(project.address, screen, theme) as (w, context):
            scratch = await blank_page(context)
            page = w.page
            await page.goto(f"{project.address}#/needs")
            await expect(page.locator("#needs-body li.needs-row").first).to_be_visible(timeout=SLOW)
            await expect(
                page.locator("#needs-body li.needs-row[data-reading='true']")
            ).to_have_count(0, timeout=SLOW)
            await open_routine(page)
            await settle(page)
            found = await contrast(w, scratch)
            assert found.checked >= 50, found.checked
            assert found.found == [], found.lines()
            for change in (
                f"plan:{world.plan}",
                f"placement:{world.other[0]}",
                f"gate:{world.gate}",
                f"ask:{world.waiting.numbers['choice']}",
            ):
                await page.goto(f"{project.address}#/needs")
                await expect(page.locator("#needs-body li.needs-row").first).to_be_visible(
                    timeout=SLOW
                )
                await open_routine(page)
                await open_review(page, change)
                if change.startswith(("plan", "placement")):
                    await page.locator("#review-view button[data-view=split]").click()
                await settle(page)
                found = await contrast(w, scratch)
                assert found.found == [], (change, found.lines())
            assert w.problems == []
