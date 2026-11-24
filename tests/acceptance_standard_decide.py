"""Browser proofs of the M52 chapter about deciding (tests/acceptance_standard.py): the one standard
bar every change ends with, in one row, with only the verbs of its kind; what each verb records as
the signed-in person; "Keep as a decision"; what a viewer sees; the sentence rule; and a name on the
line of the words around it. Each part works in a project of its own, with a headless browser signed
in as the owner, an editor or a viewer of it; what a decision recorded is read back through a real
MCP client."""

import re
from typing import Any

from playwright.async_api import Locator, Page, expect

from tests.acceptance_review import person
from tests.acceptance_scenario import clip, proof
from tests.acceptance_standard import make, rows_of
from tests.acceptance_standard_page import (
    NARROW,
    WIDE,
    Seen,
    a_busy_project,
    a_question,
    card,
    change_id,
    open_the_routine_group,
    review,
    sentences,
    the_list,
    the_review_page,
)
from tests.board_helpers import SLOW, browser_page, go, hold_back_news, plain
from tests.board_m14 import spoken
from tests.helpers import connect, ok
from tests.world import join_project, make_key, make_project, register

# Where each button of a bar is, and which verb it is.
BUTTONS = """bar => [...bar.querySelectorAll('button.verdict, button.decision-comment')].map(b => [
    b.dataset.verb ?? 'comment',
    b.dataset.answer ?? null,
    b.getBoundingClientRect().top,
    b.getBoundingClientRect().bottom,
])"""

# What each kind of change offers: the verbs, in order, and whether the comment control is there.
VERBS = {
    "plan": (["approve", "changes", "reject"], False),
    "placement": (["approve", "reject"], False),
    "note": (["approve", "reject"], False),
    "check": (["approve"], True),
    "gate": (["lift", "drop"], False),
    "lapse": (["block", "drop"], False),
}


async def bars_of(scope: Locator) -> list[list[Any]]:
    """Every button of every bar in a card or on a page: [verb, answer, top, bottom]."""
    found: list[list[Any]] = []
    for bar in await scope.locator("div.decision-bar").all():
        found += await bar.evaluate(BUTTONS)
    return found


async def the_decision_of(project: Any, number: int) -> dict[str, Any]:
    async with connect(project.mcp_url, project.key) as agent:
        asks = (await ok(agent, "list_asks", limit=100))["items"]
    return next(found for found in asks if found["id"] == number)


# -- the bar --------------------------------------------------------------------------------------


async def the_bar_is_one_row_with_the_verbs_of_its_kind() -> None:
    """At 1280 px the bar of every change, on the list and on its review page, is one row (every
    button level), and only the verbs of that kind are there; a viewer has none."""
    owner, project, viewer, made, seen = await a_busy_project()
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await page.set_viewport_size(WIDE)  # type: ignore[arg-type]
        await the_list(w, seen)
        await open_the_routine_group(page)
        wrong: list[str] = []
        listed: dict[str, list[str]] = {}
        for row in seen.rows:
            change = change_id(row)
            scope = card(page, change)
            await expect(scope.locator("div.decision-bar").first).to_be_visible(timeout=SLOW)
            found = await bars_of(scope)
            listed[change] = [f"{b[0]}:{b[1]}" if b[1] else str(b[0]) for b in found]
            tops = [b[2] for b in found]
            if max(tops) - min(tops) > 2:
                wrong.append(f"{change}: tops {tops}")
            verbs = [b[0] for b in found if b[0] != "comment"]
            kind = row["kind"]
            if kind == "ask":
                choices = row["ask"]["choices"] or []
                wanted_verbs = ["answer"] * len(choices) if choices else ["answer"]
                if row["ask"]["kind"] == "approval":
                    wanted_verbs = ["approve", "reject"]
                if verbs != wanted_verbs or "comment" not in [b[0] for b in found]:
                    wrong.append(f"{change}: verbs {verbs}")
            else:
                wanted_verbs, with_comment = VERBS[kind]
                if verbs != wanted_verbs or ("comment" in [b[0] for b in found]) != with_comment:
                    wrong.append(f"{change}: verbs {verbs}")
        for row in seen.rows:
            change = change_id(row)
            await the_review_page(w, change)
            await expect(review(page).locator("div.decision-bar").first).to_be_visible(timeout=SLOW)
            found = await bars_of(review(page))
            names = [f"{b[0]}:{b[1]}" if b[1] else str(b[0]) for b in found]
            tops = [b[2] for b in found]
            if names != listed[change]:
                wrong.append(f"{change}: the page has {names}, the card {listed[change]}")
            if max(tops) - min(tops) > 2:
                wrong.append(f"{change}: page tops {tops}")
    async with browser_page(project.address, token=None, person=viewer, name=None) as w:
        page = w.page
        await page.set_viewport_size(WIDE)  # type: ignore[arg-type]
        await the_list(w, seen)
        on_list = await page.locator("button.verdict, button.decision-comment").count()
        gone = 0
        for row in seen.rows[:4]:
            await the_review_page(w, change_id(row))
            gone += await page.locator("button.verdict, button.decision-comment").count()
        if on_list or gone:
            wrong.append(f"a viewer has {on_list + gone} buttons")
    proof(
        "M52",
        "at 1280 px the bar of every kind of change is ONE row, on the card and on the review "
        "page (every button within 2 px of the top of the others, the comment control an icon "
        "in the same row), the review page's bar is the card's bar, and only the verbs of that "
        "kind are drawn: a plan Approve, Request changes and Reject; a suggested move Approve "
        "and Reject; notes Approve and Reject; a check Approve; a question its choices (Approve "
        "and Reject for a yes or no); a refused approval Lift the gate and Drop the work; a "
        "claim that ran out Block it and Drop it; and a viewer has no button at all",
        not wrong,
        f"{len(seen.rows)} bars" + (f"; wrong: {wrong[:2]}" if wrong else ""),
    )


# -- what each verb records ---------------------------------------------------------------


async def the_dialog(page: Page, ident: str) -> Locator:
    box = page.locator(f"dialog#{ident}")
    await expect(box).to_be_visible(timeout=SLOW)
    return box


async def a_person_answers_each_kind_of_question() -> None:
    """Pressing a choice, Approve, Reject or Answer on a question records the answer as the
    signed-in person (with the comment, when there is one), frees the work, and records nothing
    else; the review page's bar does the same."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        pick, pick_ask = await a_question(
            agent, "Which provider", choices=["Stripe", "Adyen", "Neither"]
        )
        yes, yes_ask = await a_question(agent, "Drop the old format", approval=True)
        no, no_ask = await a_question(agent, "Move the settings at night", approval=True)
        free, free_ask = await a_question(agent, "Name the release")
        paged, paged_ask = await a_question(agent, "Which currency", choices=["EUR", "USD"])
        needs = await ok(agent, "needs_a_person")
    seen = Seen(needs, [], [])
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_list(w, seen)
        # A choice, with a comment.
        one = card(page, f"ask:{pick_ask}")
        await one.locator("button.decision-comment").click()
        await one.locator("div.decision-comment-box textarea").fill("Cheaper to run")
        await one.locator("button.verdict[data-answer='Adyen']").click()
        await expect(one).to_have_count(0, timeout=SLOW)
        # An approval: Approve is yes.
        two = card(page, f"ask:{yes_ask}")
        await two.locator("button.verdict[data-verb='approve']").click()
        await expect(two).to_have_count(0, timeout=SLOW)
        # An approval: Reject asks for the reason in a dialog and sends no.
        three = card(page, f"ask:{no_ask}")
        await three.locator("button.verdict[data-verb='reject']").click()
        box = await the_dialog(page, "reject-dialog")
        await box.locator("textarea").fill("Not before the launch")
        await box.get_by_role("button", name="Reject", exact=True).click()
        await expect(three).to_have_count(0, timeout=SLOW)
        # A free question: Answer opens a box for words.
        four = card(page, f"ask:{free_ask}")
        await four.locator("button.verdict[data-verb='answer']").click()
        box = await the_dialog(page, "answer-ask")
        await box.locator("#ask-answer").fill("Spring")
        await box.locator("#ask-answer-comment").fill("Short and plain")
        await box.get_by_role("button", name="Answer", exact=True).click()
        await expect(four).to_have_count(0, timeout=SLOW)
        # On the review page the same bar answers.
        await the_review_page(w, f"ask:{paged_ask}")
        await review(page).locator("button.verdict[data-answer='USD']").click()
        await expect(page.locator("#review-page button.verdict[data-answer='USD']")).to_have_count(
            0, timeout=SLOW
        )
    found = {
        number: await the_decision_of(project, number)
        for number in (pick_ask, yes_ask, no_ask, free_ask, paged_ask)
    }
    async with connect(project.mcp_url, project.key) as agent:
        states = [
            (await ok(agent, "get_node", node=t["ref"]))["status"]
            for t in (pick, yes, no, free, paged)
        ]
        notes = (await ok(agent, "list_notes", limit=5))["total"]
        open_left = (await ok(agent, "list_asks", status="open"))["total"]
    each = list(found.values())
    proof(
        "M52",
        "pressing a choice (with a comment typed in the comment box), Approve or Reject (with "
        "its reason) on a yes or no, and Answer with words on a free question records each "
        "answer as the signed-in person, as a person, with the comment, frees the blocked work, "
        "records no decision note when 'Keep as a decision' was not ticked, and the review "
        "page's bar answers the same way",
        found[pick_ask]["answer"] == "Adyen"
        and found[pick_ask]["comment"] == "Cheaper to run"
        and found[yes_ask]["answer"].lower() == "yes"
        and found[no_ask]["answer"].lower() == "no"
        and found[no_ask]["comment"] == "Not before the launch"
        and found[free_ask]["answer"] == "Spring"
        and found[free_ask]["comment"] == "Short and plain"
        and found[paged_ask]["answer"] == "USD"
        and all(f["answered"] and f["answered_by"] == owner.name for f in each)
        and states == ["ready"] * 5
        and notes == 0
        and open_left == 0,
        ", ".join(f"Q-{n} {f['answer']}" for n, f in found.items()),
    )


async def keep_as_a_decision_records_one_note_by_the_person() -> None:
    """Ticking Keep as a decision after typing a comment records the question and the answer as
    one decision note by the person, linked to the work; without a comment its reason says which
    question was answered; unticked records nothing; a refused note says the answer stood."""
    owner = register()
    project = make_project(owner)
    editor = join_project(project, "editor")
    async with connect(project.mcp_url, project.key) as agent:
        kept, kept_ask = await a_question(agent, "Who may refund", choices=["A lead", "Finance"])
        plain_one, plain_ask = await a_question(agent, "Which colour", choices=["Red", "Blue"])
        bare, bare_ask = await a_question(agent, "Which size", choices=["Small", "Large"])
        needs = await ok(agent, "needs_a_person")
    seen = Seen(needs, [], [])
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_list(w, seen)
        one = card(page, f"ask:{kept_ask}")
        await one.locator("button.decision-comment").click()
        await one.locator("div.decision-comment-box textarea").fill("A lead can decide on the spot")
        box_check = one.locator("input#answer-remember")
        starts_off = not await box_check.is_checked()
        await box_check.check()
        await one.locator("button.verdict[data-answer='A lead']").click()
        await expect(one).to_have_count(0, timeout=SLOW)
        # Answered without the tick: nothing is kept, even with a comment.
        two = card(page, f"ask:{plain_ask}")
        await two.locator("button.decision-comment").click()
        await two.locator("div.decision-comment-box textarea").fill("Blue is calmer")
        await two.locator("button.verdict[data-answer='Blue']").click()
        await expect(two).to_have_count(0, timeout=SLOW)
        # With the tick and no comment.
        await the_review_page(w, f"ask:{bare_ask}")
        await review(page).locator("button.decision-comment").click()
        await review(page).locator("input#answer-remember").check()
        await review(page).locator("button.verdict[data-answer='Large']").click()
        await expect(review(page).locator("button.verdict[data-answer='Large']")).to_have_count(
            0, timeout=SLOW
        )
    async with connect(project.mcp_url, project.key) as agent:
        notes = (await ok(agent, "list_notes", limit=20))["items"]
        recalled = await ok(agent, "recall", node=kept["ref"])
        history = (
            (await ok(agent, "note_history", note_id=notes[0]["id"]))["items"] if notes else []
        )
    by_node = {n["nodes"][0]: n for n in notes}
    decision = by_node.get(kept["ref"], {})
    again = by_node.get(bare["ref"], {})
    proof(
        "M52",
        "ticking 'Keep as a decision' (off when the box opens) after a comment records, once the "
        "answer stood, exactly ONE note by the signed-in person: the question and the answer as "
        "its two lines, the comment as why (or, with no comment, which question was answered), "
        "the person as who decided it and who recorded it, linked to the work, unconfirmed and in "
        "the work's briefing; an answer given without the tick records no note, whatever the "
        "comment said",
        starts_off
        and len(notes) == 2
        and plain_one["ref"] not in by_node
        and decision["text"] == "Q: Who may refund?\nA: A lead"
        and decision["why"] == "A lead can decide on the spot"
        and (decision["decided_by"], decision["recorded_by"], decision["recorded_by_kind"])
        == (owner.name, owner.name, "person")
        and decision["status"] == "unconfirmed"
        and decision["nodes"] == [kept["ref"]]
        and decision["id"] in [n["note"]["id"] for n in recalled["notes"]["items"]]
        and again["text"] == "Q: Which size?\nA: Large"
        and again["why"] == f"Answered Q-{bare_ask}."
        and [e["action"] for e in history][:1] == ["note_recorded"],
        f"{len(notes)} notes by {owner.name}",
    )

    # A note the server refuses after the answer stood: the person was made a viewer between the
    # two calls (the first is held back until it is so), so the answer stands and the page says
    # so in the server's words.
    async with connect(project.mcp_url, project.key) as agent:
        late, late_ask = await a_question(agent, "Which tyre", choices=["Studded", "Slick"])
        needs = await ok(agent, "needs_a_person")
    seen = Seen(needs, [], [])
    async with browser_page(project.address, token=None, person=editor, name=None) as w:
        page = w.page
        await the_review_page(w, f"ask:{late_ask}")

        async def make_a_viewer(route: Any) -> None:
            changed = project.owner.call(
                "PATCH",
                f"/board/api/projects/{project.id}/members/{editor.id}",
                {"role": "viewer"},
            )
            assert changed.status == 200, changed.body
            await route.continue_()

        await page.route("**/tool/add_note", make_a_viewer)
        await review(page).locator("button.decision-comment").click()
        await review(page).locator("input#answer-remember").check()
        await review(page).locator("button.verdict[data-answer='Studded']").click()
        problem = page.locator("p.problem").filter(has_text="The answer stood").first
        await expect(problem).to_contain_text(
            "The answer stood, but the decision was not recorded", timeout=SLOW
        )
        said = spoken(await problem.inner_text())
    refused = person(
        project, editor, "add_note", text="x", why="y", decided_by=editor.name, nodes=[late["ref"]]
    )
    answer = await the_decision_of(project, late_ask)
    async with connect(project.mcp_url, project.key) as agent:
        after = (await ok(agent, "list_notes", limit=20))["total"]
    plain(
        said.split("was not recorded: ", 1)[1],
        refused.body["message"],
    )
    proof(
        "M52",
        "when the server refuses the note after the answer stood (the person was made a viewer "
        "in between) the page says 'The answer stood, but the decision was not recorded:' and "
        "then the server's own words, the answer is as given (by that person, as a person) and "
        "no note was made",
        refused.status in (400, 403)
        and answer["answered"]
        and answer["answer"] == "Studded"
        and answer["answered_by"] == editor.name
        and after == 2,
        clip(said, 110),
    )


async def a_person_confirms_rejects_and_ticks() -> None:
    """On a row of notes each note has its own bar: Approve confirms it as the signed-in person
    (the dialog names them), Reject withdraws it with the reason; on a check only a person can
    tick, Approve ticks it with how it was checked."""
    from tests.acceptance_assist_page import (  # noqa: PLC0415
        ANOTHER_CHECK,
        PERSON_CHECK,
        what_waits_for_a_person,
    )

    owner = register()
    project = make_project(owner)
    waiting = await what_waits_for_a_person(project, owner, two_checks=True)
    tasks, notes = waiting["tasks"], waiting["notes"]
    async with connect(project.mcp_url, project.key) as agent:
        needs = await ok(agent, "needs_a_person")
    seen = Seen(needs, [], [])
    how = "Tried it on a phone: the form works"
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_list(w, seen)
        # One note on its own: its bar is the row's bar, and Approve confirms it.
        single = card(page, f"note:{tasks['noted2']['ref']}")
        await expect(single.locator("div.decision-bar")).to_have_count(1, timeout=SLOW)
        await single.locator("button.verdict[data-verb='approve']").click()
        box = page.locator("dialog.modal")
        await expect(box.locator("#confirm-person")).to_have_value(owner.name, timeout=SLOW)
        await box.locator("#confirm-quote").fill("I read it in the docs too")
        await box.get_by_role("button", name="Confirm", exact=True).click()
        await expect(single).to_have_count(0, timeout=SLOW)
        # Two notes on one task: a bar for each, and Reject withdraws the one it names.
        double = card(page, f"note:{tasks['noted']['ref']}")
        await expect(double.locator("div.decision-bar")).to_have_count(2, timeout=SLOW)
        first = notes["first"]["id"]
        await double.locator(f"button.verdict[aria-label='Reject N-{first}']").click()
        box = await the_dialog(page, "reject-dialog")
        await box.locator("textarea").fill("Not true any more")
        await box.get_by_role("button", name="Reject", exact=True).click()
        await expect(double.locator("div.decision-bar")).to_have_count(1, timeout=SLOW)
        # Two checks open on one task: each has its own bar, and the comment says how it was done.
        both = card(page, f"check:{tasks['ticked']['ref']}")
        await expect(both.locator("div.decision-bar")).to_have_count(2, timeout=SLOW)
        line = both.locator("div.decision").filter(
            has=page.locator(f"button.verdict[aria-label$='{PERSON_CHECK}']")
        )
        await line.locator("button.decision-comment").click()
        await line.locator("div.decision-comment-box textarea").fill(how)
        await line.locator("button.verdict[data-verb='approve']").click()
        await expect(both.locator("div.decision-bar")).to_have_count(1, timeout=SLOW)
        # A check on its own: Approve ticks it with nothing said.
        alone = card(page, f"check:{tasks['ticked2']['ref']}")
        await expect(alone.locator("div.decision-bar")).to_have_count(2, timeout=SLOW)
    async with connect(project.mcp_url, project.key) as agent:
        confirmed = await ok(agent, "get_note", note_id=notes["other"]["id"])
        history = (await ok(agent, "note_history", note_id=notes["other"]["id"]))["items"]
        withdrawn = await ok(agent, "get_note", note_id=first)
        left = await ok(agent, "get_note", note_id=notes["second"]["id"])
        ticks = (await ok(agent, "get_node", node=tasks["ticked"]["ref"]))["checks"]
        untouched = (await ok(agent, "get_node", node=tasks["ticked2"]["ref"]))["checks"]
        events = (await ok(agent, "node_history", node=tasks["ticked"]["ref"]))["items"]
    done = next(t for t in ticks if t["text"] == PERSON_CHECK)
    still_open = next(t for t in ticks if t["text"] == ANOTHER_CHECK)
    last = history[-1]
    proof(
        "M52",
        "on notes Approve opens the confirm dialog that names the signed-in person and records "
        "the confirmation as that person with the words they gave (one note_confirmed event by a "
        "person), Reject withdraws the note it is on with the reason and leaves the other note "
        "of the task with its own bar, and on checks only a person can tick Approve ticks the "
        "one it is on as the signed-in person with how it was checked (one check_ticked event) "
        "and leaves the other open",
        (confirmed["status"], confirmed["confirmed_by"]) == ("confirmed", owner.name)
        and (last["action"], last["actor"], last["actor_kind"])
        == ("note_confirmed", owner.name, "person")
        and last["after"].get("quote") == "I read it in the docs too"
        and (withdrawn["status"], withdrawn["status_reason"]) == ("withdrawn", "Not true any more")
        and left["status"] == "unconfirmed"
        and (done["checked"], done["checked_by"], done["checked_by_kind"], done["evidence"])
        == (True, owner.name, "person", how)
        and still_open["checked"] is False
        and [e["action"] for e in events if e["action"].startswith("check_")] == ["check_ticked"]
        and all(not t["checked"] for t in untouched if t["text"] in (PERSON_CHECK, ANOTHER_CHECK)),
        f"{owner.name}: note {notes['other']['id']} confirmed, note {first} withdrawn, "
        f"tick {clip(how, 30)}",
    )


async def handoffs_of(project: Any, ref: str) -> list[dict[str, Any]]:
    async with connect(project.mcp_url, project.key) as agent:
        events = (await ok(agent, "node_history", node=ref))["items"]
    return [e for e in events if e["action"] == "handoff"]


async def a_person_looks_into_a_refused_gate_and_a_claim_that_ran_out() -> None:
    """Lift the gate frees the work; Drop the work and Drop it mark it dropped, and Block it marks
    it blocked, each with the reason as a handoff note by the person."""
    import asyncio  # noqa: PLC0415

    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await make(agent, "epic", "Billing")
        story = await make(agent, "story", "Pay", epic["ref"])
        names = ("Touch billing", "Touch invoices", "Retry the call", "Retry the refund")
        gated, dropped_gate, lapsed, dropped_lapse = [
            await make(agent, "task", name, story["ref"]) for name in names
        ]
        for task in (gated, dropped_gate):
            gate = await ok(
                agent,
                "add_gate",
                node=task["ref"],
                kind="approval",
                question=f"May we {task['title']}?",
            )
            said_no = person(project, owner, "answer_ask", ask=gate["ask"], answer="no")
            assert said_no.status == 200, said_no.body
        for task in (lapsed, dropped_lapse):
            await ok(agent, "claim_node", node=task["ref"], lease_seconds=1)
        await asyncio.sleep(1.5)
        async with connect(project.mcp_url, make_key(project, "second-agent")) as second:
            for task in (lapsed, dropped_lapse):
                await ok(second, "claim_node", node=task["ref"], lease_seconds=1)
        await asyncio.sleep(1.5)
        needs = await ok(agent, "needs_a_person")
        gate_id = {
            r["node"]: r["gate"]["id"]
            for g in needs["groups"]
            for r in g["items"]
            if r["kind"] == "gate"
        }
    seen = Seen(needs, [], [])
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_list(w, seen)
        one = card(page, f"gate:{gate_id[gated['ref']]}")
        await one.locator("button.verdict[data-verb='lift']").click()
        box = await the_dialog(page, "lift-gate")
        await box.locator("#lift-reason").fill("Asked again")
        await box.get_by_role("button", name="Lift the gate", exact=True).click()
        await expect(one).to_have_count(0, timeout=SLOW)
        two = card(page, f"gate:{gate_id[dropped_gate['ref']]}")
        await two.locator("button.verdict[data-verb='drop']").click()
        box = await the_dialog(page, "drop-dialog")
        await box.locator("textarea").fill("Not needed after all")
        await box.get_by_role("button", name="Drop", exact=True).click()
        await expect(two).to_have_count(0, timeout=SLOW)
        three = card(page, f"lapse:{lapsed['ref']}")
        await three.locator("button.verdict[data-verb='block']").click()
        box = await the_dialog(page, "block-dialog")
        await box.locator("textarea").fill("Waiting for the vendor")
        await box.get_by_role("button", name="Block", exact=True).click()
        await expect(box).to_have_count(0, timeout=SLOW)
        await expect(three).to_have_count(0, timeout=SLOW)
        async with connect(project.mcp_url, project.key) as agent:
            still = [
                r["node"]
                for r in rows_of(await ok(agent, "needs_a_person"))
                if r["kind"] == "lapse"
            ]
        four = card(page, f"lapse:{dropped_lapse['ref']}")
        await four.locator("button.verdict[data-verb='drop']").click()
        box = await the_dialog(page, "drop-dialog")
        await box.locator("textarea").fill("Nobody wants it")
        await box.get_by_role("button", name="Drop", exact=True).click()
        await expect(four).to_have_count(0, timeout=SLOW)
    async with connect(project.mcp_url, project.key) as agent:
        nodes = {
            t["ref"]: await ok(agent, "get_node", node=t["ref"])
            for t in (gated, dropped_gate, lapsed, dropped_lapse)
        }
    notes = {ref: await handoffs_of(project, ref) for ref in nodes}

    def said(task: dict[str, Any]) -> tuple[Any, ...]:
        (note,) = notes[task["ref"]]
        return (note["actor"], note["actor_kind"], note["after"]["outcome"], note["after"]["note"])

    lifted = nodes[gated["ref"]]["gates"][0]
    proof(
        "M52",
        "on a refused approval Lift the gate (with its reason) lifts the gate as the signed-in "
        "person and frees the work, and Drop the work marks it dropped with the reason as a "
        "handoff note by that person; on a claim that ran out twice Block it marks the work "
        "blocked and takes its row away (the other claim's row stays until it is decided) and "
        "Drop it marks it dropped, each with the reason as that person's handoff note",
        lifted["state"] == "removed"
        and lifted["removed_reason"] == "Asked again"
        and nodes[gated["ref"]]["held_by"] == []
        and notes[gated["ref"]] == []
        and nodes[dropped_gate["ref"]]["status"] == "dropped"
        and said(dropped_gate) == (owner.name, "person", "dropped", "Not needed after all")
        and nodes[lapsed["ref"]]["status"] == "blocked"
        and still == [dropped_lapse["ref"]]
        and said(lapsed) == (owner.name, "person", "blocked", "Waiting for the vendor")
        and nodes[dropped_lapse["ref"]]["status"] == "dropped"
        and said(dropped_lapse) == (owner.name, "person", "dropped", "Nobody wants it"),
        f"{owner.name}: lifted, dropped, blocked, dropped",
    )


async def a_person_decides_plans_and_moves() -> None:
    """Approve, Request changes and Reject on a plan, and Approve and Reject on a suggested move,
    record what the server says as the signed-in person: a plan's decisions name the revision
    that was read, a rejection leaves its reason on every item and on the proposal."""
    from tests.acceptance_review import a_plan  # noqa: PLC0415

    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await make(agent, "epic", "Payments")
        story = await make(agent, "story", "Existing story", epic["ref"])
        misc = await make(agent, "epic", "Misc")
        loose = [await make(agent, "story", f"Loose {n}", misc["ref"]) for n in range(4)]
        approved = await a_plan(agent, epic, story, "To approve")
        rejected = await a_plan(agent, epic, story, "To reject")
        changed = await a_plan(agent, epic, story, "To ask about")
        moved = (
            await ok(
                agent,
                "propose_placement",
                items=[loose[0]["ref"], loose[1]["ref"]],
                parent=epic["ref"],
                reason="Both belong with payments.",
            )
        )["id"]
        turned_down = (
            await ok(
                agent,
                "propose_placement",
                items=[loose[2]["ref"], loose[3]["ref"]],
                parent=epic["ref"],
                reason="Also payments.",
            )
        )["id"]
        needs = await ok(agent, "needs_a_person")
    seen = Seen(needs, [], [])
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_list(w, seen)
        one = card(page, f"plan:{approved['plan']}")
        await one.locator("button.verdict[data-verb='approve']").click()
        await expect(one).to_have_count(0, timeout=SLOW)
        await the_review_page(w, f"plan:{rejected['plan']}")
        await review(page).locator("button.verdict[data-verb='reject']").click()
        box = await the_dialog(page, "reject-dialog")
        await box.get_by_role("button", name="Reject", exact=True).click()
        await expect(box.locator(".problem")).to_have_text(re.compile(r"\S"), timeout=SLOW)
        blank = spoken(await box.locator(".problem").inner_text())
        await box.locator("textarea").fill("Too wide")
        await box.get_by_role("button", name="Reject", exact=True).click()
        await expect(box).to_have_count(0, timeout=SLOW)
        await go(page, w.board, "#/needs")
        three = card(page, f"plan:{changed['plan']}")
        await expect(three).to_have_count(1, timeout=SLOW)
        await three.locator("button.verdict[data-verb='changes']").click()
        box = page.locator("#changes-dialog")
        await expect(box).to_be_visible(timeout=SLOW)
        await box.locator("textarea#changes-summary").fill("Split it in two")
        await box.locator("button[type=submit]").click()
        await expect(three).to_have_count(0, timeout=SLOW)
        four = card(page, f"placement:{moved}")
        await four.locator("button.verdict[data-verb='approve']").click()
        await expect(four).to_have_count(0, timeout=SLOW)
        five = card(page, f"placement:{turned_down}")
        await five.locator("button.verdict[data-verb='reject']").click()
        box = await the_dialog(page, "reject-dialog")
        await box.locator("textarea").fill("Leave them in Misc")
        await box.get_by_role("button", name="Reject", exact=True).click()
        await expect(five).to_have_count(0, timeout=SLOW)
    async with connect(project.mcp_url, project.key) as agent:
        plans = {
            n: (await ok(agent, "list_plans", plan=n))["items"][0]
            for n in (approved["plan"], rejected["plan"], changed["plan"])
        }
        placements = {p["id"]: p for p in (await ok(agent, "list_placements", limit=20))["items"]}
        items = [
            (await ok(agent, "get_node", node=i["ref"])) for i in plans[rejected["plan"]]["tree"]
        ]
        free = [
            (await ok(agent, "get_node", node=i["ref"]))["held_by"]
            for i in plans[approved["plan"]]["tree"]
        ]
        history = (await ok(agent, "node_history", node=loose[0]["ref"]))["items"]
    approved_plan, rejected_plan, asked_plan = (
        plans[n] for n in (approved["plan"], rejected["plan"], changed["plan"])
    )
    move = [e for e in history if e["action"] == "move"][-1]
    proof(
        "M52",
        "on plans Approve approves the plan as the signed-in person for the revision that was "
        "read and frees every item, Reject refuses a blank reason in the server's words and then "
        "rejects the plan with the reason on every item, and Request changes (with its summary) "
        "leaves the plan held and waiting for its agent; on suggested moves Approve moves the "
        "stories as that person and Reject leaves them where they were with the reason on the "
        "proposal",
        (approved_plan["state"], approved_plan["decided_by"]) == ("approved", owner.name)
        and approved_plan["revision"] == 1
        and all(by == [] for by in free)
        and (rejected_plan["state"], rejected_plan["reason"]) == ("rejected", "Too wide")
        and all(i["status"] == "dropped" for i in items)
        and "reason" in blank.lower()
        and asked_plan["state"] == "changes_requested"
        and asked_plan["feedback"][0]["summary"] == "Split it in two"
        and asked_plan["feedback"][0]["by"] == owner.name
        and placements[moved]["state"] == "accepted"
        and placements[moved]["decided_by"] == owner.name
        and (move["actor"], move["actor_kind"]) == (owner.name, "person")
        and placements[turned_down]["state"] == "rejected"
        and placements[turned_down]["decision_reason"] == "Leave them in Misc",
        clip(blank, 80),
    )


async def a_stale_decision_is_refused_in_the_servers_words() -> None:
    """A decision made on a card the page has not heard of a change to is refused in the server's
    own words beside the bar, and nothing changes: a plan that was revised, a question that was
    answered."""
    from tests.acceptance_revising import an_epic, drafted  # noqa: PLC0415
    from tests.acceptance_revising import person as rperson  # noqa: PLC0415
    from tests.acceptance_revising_page import second_revision  # noqa: PLC0415

    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        task, asked = await a_question(agent, "Which provider", choices=["Stripe", "Adyen"])
        needs = await ok(agent, "needs_a_person")
    seen = Seen(needs, [], [])
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_list(w, seen)
        await hold_back_news(page)
        async with connect(project.mcp_url, project.key) as agent:
            rperson(project, owner, "request_plan_changes", plan=number, revision=1, summary="No.")
            await ok(
                agent,
                "revise_plan",
                plan=number,
                items=second_revision(epic, refs),
                note="Revised.",
            )
            late = person(project, owner, "answer_ask", ask=asked, answer="Stripe")
            assert late.status == 200, late.body
        # The question first: a refusal of the plan makes the page read the list again, and the
        # answered question would be gone from it.
        question = card(page, f"ask:{asked}")
        await question.locator("button.verdict[data-answer='Adyen']").click()
        ask_problem = question.locator("p.problem")
        await expect(ask_problem).to_have_text(re.compile(r"\S"), timeout=SLOW)
        ask_said = spoken(await ask_problem.inner_text())
        plan_card = card(page, f"plan:{number}")
        await plan_card.locator("button.verdict[data-verb='approve']").click()
        plan_problem = plan_card.locator("p.problem")
        await expect(plan_problem).to_have_text(re.compile(r"\S"), timeout=SLOW)
        plan_said = spoken(await plan_problem.inner_text())
    tried_plan = person(project, owner, "approve_plan", plan=number, approved=True, revision=1)
    tried_ask = person(project, owner, "answer_ask", ask=asked, answer="Adyen")
    plain(plan_said, tried_plan.body["message"])
    plain(ask_said, tried_ask.body["message"])
    async with connect(project.mcp_url, project.key) as agent:
        waiting = (await ok(agent, "list_plans", plan=number))["items"][0]
    answer = await the_decision_of(project, asked)
    proof(
        "M52",
        "a decision made on a card the page had not heard a change to (a plan revised behind its "
        "back, a question answered by someone else) is refused: the page shows the server's own "
        "words beside the bar, in full, and nothing changes (the plan still waits at revision 2, "
        "the answer is the first one given)",
        tried_plan.status == 400
        and tried_ask.status == 400
        and waiting["state"] == "proposed"
        and waiting["revision"] == 2
        and answer["answer"] == "Stripe",
        clip(plan_said, 60) + " | " + clip(ask_said, 40),
    )


# -- what a viewer sees, and the sentence rule ---------------------------------------------

WRITING = (
    "button.verdict, button.decision-comment, input#answer-remember, button.diff-comment, "
    "form.comment-form, .needs-yes, .needs-no, .needs-changes, .ask-choice"
)


async def a_viewer_reads_everything_and_cannot_decide() -> None:
    """A viewer reads the list and every review page as the owner does (what the tools say is
    there) and has no control that writes; the server refuses the same decisions."""
    from tests.acceptance_standard_page import check_every_page  # noqa: PLC0415

    owner, project, viewer, made, seen = await a_busy_project()
    async with browser_page(project.address, token=None, person=viewer, name=None) as w:
        page = w.page
        await the_list(w, seen)
        on_the_list = await page.locator(WRITING).count()
        wrong = await check_every_page(w, project, made, seen)
        controls = 0
        for row in seen.rows:
            await the_review_page(w, change_id(row))
            controls += await page.locator(WRITING).count()
    tried = [
        person(project, viewer, "answer_ask", ask=made["approval"], answer="yes"),
        person(project, viewer, "approve_plan", plan=made["plan"], approved=True),
        person(project, viewer, "decide_placement", placement=made["stories_moved"], accepted=True),
    ]
    async with connect(project.mcp_url, project.key) as agent:
        still = (await ok(agent, "needs_a_person"))["total"]
    proof(
        "M52",
        "a viewer reads every change on the list and on its review page exactly as the tools "
        "say it (the same pages the owner reads) and has no button, comment control, box or "
        "field that decides or writes, and the server refuses the same decisions and nothing "
        "is decided",
        not wrong
        and on_the_list == 0
        and controls == 0
        and all(t.status in (400, 403) for t in tried)
        and still == seen.total,
        f"{len(seen.rows)} pages, {on_the_list + controls} controls, "
        f"refused {[t.status for t in tried]}",
    )


async def close_the_dialog(page: Page) -> None:
    await page.keyboard.press("Escape")
    box = page.locator("dialog[open]")
    if await box.count():
        await box.get_by_role("button", name="Cancel").click()
    await expect(page.locator("dialog[open]")).to_have_count(0, timeout=SLOW)


async def the_page_says_nothing_in_sentences() -> None:
    """No text of six words or more on the list, in its rail, on any review page or in any dialog
    they open, that is not somebody's own words, the server's refusal or a hover card."""
    _, project, _, made, seen = await a_busy_project()
    found: dict[str, list[str]] = {}
    scanned = 0
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        for mode in ("level", "topic", "decision"):
            await the_list(w, seen, mode)
            await open_the_routine_group(page)
            found[f"the list by {mode}"] = await sentences(page)
            scanned += 1
        for row in seen.rows:
            await the_review_page(w, change_id(row))
            found[f"the page of {change_id(row)}"] = await sentences(page)
            scanned += 1
        # An open dialog of each kind, and the comment box of a question.
        wanted = [
            (f"plan:{made['plan']}", "changes"),
            (f"plan:{made['plan']}", "reject"),
            (f"placement:{made['stories_moved']}", "reject"),
            (f"ask:{made['approval']}", "reject"),
            (f"ask:{made['free']}", "answer"),
        ]
        for row in seen.rows:
            if row["kind"] in ("note", "gate", "lapse"):
                verbs = {
                    "note": ("approve", "reject"),
                    "gate": ("lift", "drop"),
                    "lapse": ("block", "drop"),
                }
                wanted += [(change_id(row), verb) for verb in verbs[row["kind"]]]
        for change, verb in wanted:
            await the_review_page(w, change)
            await review(page).locator(f"button.verdict[data-verb='{verb}']").click()
            await expect(page.locator("dialog[open]")).to_be_visible(timeout=SLOW)
            found[f"the {verb} dialog of {change}"] = await sentences(page)
            scanned += 1
            await close_the_dialog(page)
        await the_review_page(w, f"ask:{made['approval']}")
        await review(page).locator("button.decision-comment").click()
        await expect(review(page).locator("div.decision-comment-box")).to_be_visible(timeout=SLOW)
        found["the comment box"] = await sentences(page)
    said = {where: texts for where, texts in found.items() if texts}
    proof(
        "M52",
        "no text of six words or more that is not somebody's own words, the server's refusal or "
        "a hover card is on the list (in each way of grouping, with the routine moves open), in "
        "its rail, on the review page of any kind of change, in the dialog of any verb that opens "
        "one (Request changes, Reject, Answer, Approve on a note, Lift the gate, Block it, Drop) "
        "or in the comment box: the scan over the live page finds none",
        not said and scanned >= 20,
        f"{scanned} places scanned" + (f"; sentences: {clip(str(said), 1500)}" if said else ""),
    )


# -- a name on the line, both themes and a narrow window -----------------------------------------

# Each name of a person or an agent under `root` and how far its glyph box is from the box of
# the words beside it (the nearest text in the same line): [name, top, bottom, centre], in pixels.
NAMES = """root => {
    const glyphs = node => {
        const range = document.createRange();
        range.selectNodeContents(node);
        return range.getClientRects()[0] || range.getBoundingClientRect();
    };
    const found = [];
    for (const name of root.querySelectorAll('.actor-name')) {
        // The glyph box of the name (not its line box, which holds the leading above and below).
        const box = glyphs(name);
        if (!box.width || !box.height || name.closest('.chip')) continue;
        const middle = (box.top + box.bottom) / 2;
        let beside = null;
        // The nearest words on the same line: text next to the name or, when the name sits in a
        // box of its own (a row of the Activity list), the first line of the words of the row.
        for (let at = name.closest('.actor'); at && at !== root && !beside; at = at.parentElement) {
            for (const node of at.parentElement ? at.parentElement.childNodes : []) {
                const text = node.nodeType === 3 ? node : null;
                const other = node.nodeType === 1 && node !== at && !node.contains(name);
                const walker = other ? document.createTreeWalker(node, NodeFilter.SHOW_TEXT) : null;
                const candidate = text || (walker && walker.nextNode());
                if (!candidate || !/[A-Za-z0-9]{2,}/.test(candidate.textContent)) continue;
                if (candidate.parentElement && candidate.parentElement.closest('.actor')) continue;
                const there = glyphs(candidate);
                if (there.width && Math.abs((there.top + there.bottom) / 2 - middle) < 12) {
                    beside = [there, getComputedStyle(candidate.parentElement)];
                    break;
                }
            }
        }
        if (beside) {
            const same = getComputedStyle(name).fontSize === beside[1].fontSize;
            const centre = Math.abs(middle - (beside[0].top + beside[0].bottom) / 2);
            found.push([
                name.textContent.trim(),
                same ? Math.abs(box.top - beside[0].top) : centre,
                same ? Math.abs(box.bottom - beside[0].bottom) : centre,
                centre,
            ]);
        }
    }
    return found;
}"""

# Each chip that holds a name and how far the glyphs of the name are from the middle of the chip:
# [name, pixels].
IN_CHIPS = """selector => [...document.querySelectorAll(selector)].flatMap(chip =>
    [...chip.querySelectorAll('.actor-name')].map(name => {
        const own = document.createRange();
        own.selectNodeContents(name);
        const a = own.getBoundingClientRect(), b = chip.getBoundingClientRect();
        return [name.textContent.trim(), Math.abs((a.top + a.bottom) / 2 - (b.top + b.bottom) / 2)];
    }))"""


async def names_on_the_line(page: Page, root: str) -> list[list[Any]]:
    found: list[list[Any]] = await page.locator(root).first.evaluate(NAMES)
    return found


async def a_name_sits_on_the_line_of_the_words_around_it() -> None:
    """In a line of text (the review page's note and a comment's thread), in a chip and in the
    Activity list the box of a name is level with the words beside it, to a pixel."""
    from tests.acceptance_standard_page import a_revised_plan  # noqa: PLC0415

    owner = register()
    project = make_project(owner)
    number, refs = await a_revised_plan(project, owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic = await make(agent, "epic", "Billing")
        story = await make(agent, "story", "Pay", epic["ref"])
        task = await make(agent, "task", "Wire the webhook", story["ref"])
        await ok(agent, "claim_node", node=task["ref"])
        await ok(
            agent,
            "add_note",
            text="The webhook retries for a day",
            why="Read it in the provider's docs",
            nodes=[task["ref"]],
        )
        needs = await ok(agent, "needs_a_person")
    seen = Seen(needs, [], [])
    note_row = next(r for r in seen.rows if r["kind"] == "note")
    wrong: list[str] = []
    measured = 0
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await page.set_viewport_size(WIDE)  # type: ignore[arg-type]
        for label, address, root in (
            ("the note on its review page", f"note:{note_row['node']}", "#review-page"),
            ("a comment's thread on a plan", f"plan:{number}", "#review-page"),
        ):
            await the_review_page(w, address)
            found = await names_on_the_line(page, root)
            measured += len(found)
            if not found:
                wrong.append(f"{label}: no name beside words")
            wrong += [f"{label}: {n} {t:.1f}/{b:.1f}" for n, t, b, _ in found if t > 1 or b > 1]
        await w.open("#/board?show=both")
        chips = page.locator(f"article.card[data-ref='{task['ref']}'] .chip.held")
        await expect(chips).to_have_count(1, timeout=SLOW)
        in_chips = await page.evaluate(IN_CHIPS, "article.card .chip.held")
        await the_review_page(w, f"plan:{number}")
        in_chips += await page.evaluate(IN_CHIPS, "#review-page .chip")
        measured += len(in_chips)
        if not in_chips:
            wrong.append("no name in a chip")
        wrong += [f"chip: {n} {d:.1f}" for n, d in in_chips if d > 1]
        await w.open("#/activity")
        await expect(page.locator("#feed li.event").first).to_be_visible(timeout=SLOW)
        in_feed = await names_on_the_line(page, "#feed")
        measured += len(in_feed)
        if not in_feed:
            wrong.append("Activity: no name beside words")
        wrong += [f"Activity: {n} {t:.1f}/{b:.1f}" for n, t, b, _ in in_feed if t > 1 or b > 1]
    proof(
        "M52",
        "a name of a person or an agent sits on the line of the words around it, measured on the "
        "page: the box of the name is within a pixel (top and bottom) of the box of the words "
        "beside it in a line of text (the note's 'Recorded by' line and a comment's thread on the "
        "review page, and the Activity list) and the name in a chip is centred in it to a pixel",
        not wrong and measured >= 6,
        f"{measured} names measured" + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )


# Whether the text of an element reads against its background: the contrast of its colour with the
# first background behind it that is not clear.
CONTRAST = """selector => [...document.querySelectorAll(selector)]
    .filter(e => e.checkVisibility()).map(e => {
    const parse = c => (c.match(/[\\d.]+/g) || []).map(Number);
    const channel = v => {
        v /= 255;
        return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    };
    const light = ([r, g, b]) => [r, g, b].map(channel)
        .reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
    let back = [255, 255, 255];
    for (let at = e; at; at = at.parentElement) {
        const c = parse(getComputedStyle(at).backgroundColor);
        if (c.length >= 3 && (c.length === 3 || c[3] > 0.95)) { back = c.slice(0, 3); break; }
    }
    const front = parse(getComputedStyle(e).color).slice(0, 3);
    const [a, b] = [light(front), light(back)];
    const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    return [(e.textContent || '').trim().slice(0, 20), ratio];
})"""


async def both_themes_and_a_narrow_window_stay_readable() -> None:
    """In the light and the dark theme, at 1280 px and at 390 px, the list and a review page
    have no sideways scroll, every button of a bar is inside the window and has a name, the
    text of the bar, the chips and the facts reads (4.5 to 1), and the keyboard reaches Approve
    and closes a dialog with Escape."""
    _, project, _, made, seen = await a_busy_project()
    wrong: list[str] = []
    drawn = 0
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_list(w, seen)
        for theme in ("light", "dark"):
            await page.evaluate(f"localStorage.setItem('vrum.theme', '{theme}')")
            for size in (WIDE, NARROW):
                await page.set_viewport_size(size)  # type: ignore[arg-type]
                for where in ("list", "plan", "ask"):
                    if where == "list":
                        await the_list(w, seen)
                    else:
                        change = (
                            f"plan:{made['plan']}" if where == "plan" else f"ask:{made['approval']}"
                        )
                        await the_review_page(w, change)
                    drawn += 1
                    applied = await page.evaluate("document.documentElement.dataset.theme")
                    sideways = await page.evaluate(
                        "document.documentElement.scrollWidth"
                        " > document.documentElement.clientWidth"
                    )
                    buttons = await page.locator("button.verdict").evaluate_all(
                        "(els, w) => els.filter(e => e.checkVisibility()).map(e => {"
                        " const r = e.getBoundingClientRect();"
                        " const name = e.getAttribute('aria-label') || e.textContent.trim();"
                        " return [name, r.left, r.right, w]; })",
                        size["width"],
                    )
                    where_it = f"{theme} {size['width']} px {where}"
                    if applied != theme:
                        wrong.append(f"{where_it}: theme {applied}")
                    if sideways:
                        wrong.append(f"{where_it}: sideways scroll")
                    wrong += [
                        f"{where_it}: {n} outside"
                        for n, left, right, wide in buttons
                        if left < -1 or right > wide + 1
                    ]
                    wrong += [f"{where_it}: a button without a name" for n, *_ in buttons if not n]
                    low = await page.evaluate(
                        CONTRAST,
                        "button.verdict, .change-kind, .level-chip, .change-facts li, "
                        "a.change-title",
                    )
                    wrong += [f"{where_it}: {t!r} {c:.1f}" for t, c in low if c < 4.5]
        # The keyboard reaches an Approve button from the top of a review page, and Escape closes
        # the dialog it opens.
        await page.set_viewport_size(WIDE)  # type: ignore[arg-type]
        await the_review_page(w, f"placement:{made['stories_moved']}")
        await page.evaluate("document.activeElement?.blur()")
        reached = False
        for _ in range(80):
            await page.keyboard.press("Tab")
            if await page.evaluate(
                "document.activeElement?.matches('button.verdict[data-verb=approve]')"
            ):
                reached = True
                break
        await page.locator("button.verdict[data-verb='reject']").focus()
        await page.keyboard.press("Enter")
        await expect(page.locator("dialog[open]")).to_be_visible(timeout=SLOW)
        await page.keyboard.press("Escape")
        await expect(page.locator("dialog[open]")).to_have_count(0, timeout=SLOW)
        if not reached:
            wrong.append("Tab never reached Approve")
    proof(
        "M52",
        "in the light and the dark theme, at 1280 px and at 390 px, the list, a plan's review "
        "page and a question's have no sideways scroll, every button of a bar is inside the "
        "window and has a name, the text of the bar, the chips and the facts reads at 4.5 to 1 "
        "or more, Tab reaches Approve on a review page and Escape closes the dialog Reject opens",
        not wrong,
        f"{drawn} pages" + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )


async def standard_decisions() -> None:
    await the_bar_is_one_row_with_the_verbs_of_its_kind()
    await a_person_answers_each_kind_of_question()
    await keep_as_a_decision_records_one_note_by_the_person()
    await a_person_confirms_rejects_and_ticks()
    await a_person_looks_into_a_refused_gate_and_a_claim_that_ran_out()
    await a_person_decides_plans_and_moves()
    await a_stale_decision_is_refused_in_the_servers_words()
    await a_viewer_reads_everything_and_cannot_decide()
    await the_page_says_nothing_in_sentences()
    await a_name_sits_on_the_line_of_the_words_around_it()
    await both_themes_and_a_narrow_window_stay_readable()
