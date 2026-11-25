"""M45 on the board: the Needs a person page, its count in the sidebar and the Questions of an item.

Driven in a real headless browser and compared with what a real MCP client reads (see ./check.sh).
An agent that needs a person puts an ask on a story or task; the page of what needs a person draws
exactly what `needs_a_person` says, by story, with the action in the row: an ask is answered there
(a choice, an approval or a free answer, with an optional comment), and anything else opens the
item.
The sidebar counts the same list, draws nothing at zero and follows it live. The item's page shows
its asks as Questions (the answer in full, or the way to give it), the history says them plainly,
a viewer sees everything and cannot answer, and a refusal is shown in the server's own words.
Every test makes an account and a project of its own."""

import hashlib
import re
from dataclasses import dataclass
from typing import Any

import anyio
import pytest
from mcp import Client
from playwright.async_api import Page, ViewportSize, expect

from tests.board_helpers import (
    SLOW,
    browser_page,
    hold_back_news,
    nothing_open,
    on_page,
    open_item,
    part,
    plain,
)
from tests.helpers import connect, make_tree, ok
from tests.test_board_verifier_integration import sideways
from tests.test_checks_integration import A_TOKEN, as_person, define
from tests.world import (
    HARNESS_AGENT,
    Person,
    Project,
    join_project,
    make_key,
    make_project,
    register,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

TESTS_CHECK = "Tests pass"
PERSON_CHECK = "A person has tried it on a phone"
CHOICES = ["Stripe", "Adyen", "Neither"]
# Longer than the 200 characters the server cuts a row's reason to: the page shows all of it.
LONG_QUESTION = "Which payment provider should the checkout use? " + " ".join(
    f"Reason {n} concerns fees and the markets we sell in." for n in range(1, 11)
)
APPROVAL = "Is it fine to ship the checkout to everyone on Friday?"
FREE = "What should the receipt say at the bottom?"
NARROW: ViewportSize = {"width": 390, "height": 900}
ASK_WORD = re.compile(r"\bask \d+", re.IGNORECASE)
# What the page of nothing says: one short line.
EMPTY = "Nothing to review"
# The buttons of a change that decide something: the verbs of its bar. Every other button of a card
# folds or opens it, or writes a comment that is not sent yet.
DECIDING = "button.verdict"


@dataclass
class Waiting:
    """What an agent left waiting for a person in one project."""

    story: str
    quiet_story: str | None
    choice: dict[str, Any]
    approval: dict[str, Any]
    free: dict[str, Any]
    checked: dict[str, Any]
    noted: dict[str, Any]
    lapsed: dict[str, Any] | None
    numbers: dict[str, int]


async def task_under(agent: Client, story: str, title: str) -> dict[str, Any]:
    return dict(await ok(agent, "create_node", actor="x", kind="task", title=title, parent=story))


async def ask(agent: Client, task: dict[str, Any], outcome: str, **the_ask: Any) -> int:
    """The agent takes the task and leaves it with an ask; returns the ask's number."""
    await ok(agent, "claim_node", node=task["ref"])
    await ok(
        agent,
        "finish_work",
        node=task["ref"],
        outcome=outcome,
        note="I need a person before I can go on.",
        ask=the_ask,
    )
    found = (await ok(agent, "get_node", node=task["ref"]))["asks"]
    return int(found[0]["id"])


async def leave_waiting(
    agent: Client, project: Project, owner: Person, *, lapse: bool = False
) -> Waiting:
    """One of everything: a question with choices (blocks), an approval (parks), a free question
    (blocks), work that is finished except for a check only a person can tick, a note on work in
    progress, and, with `lapse`, a claim that ran out twice. They are under two stories."""
    define(owner, project, f"- {TESTS_CHECK}\n- [person] {PERSON_CHECK}\n")
    epic = await ok(agent, "create_node", actor="x", kind="epic", title="Checkout")
    story = await ok(agent, "create_node", actor="x", kind="story", title="Pay", parent=epic["ref"])
    choice = await task_under(agent, story["ref"], "Choose a provider")
    approval = await task_under(agent, story["ref"], "Ship the checkout")
    free = await task_under(agent, story["ref"], "Write the receipt")
    checked = await task_under(agent, story["ref"], "Build the form")
    noted = await task_under(agent, story["ref"], "Wire the webhook")
    numbers = {
        "choice": await ask(agent, choice, "blocked", question=LONG_QUESTION, choices=CHOICES),
        "approval": await ask(agent, approval, "released", question=APPROVAL, approval=True),
        "free": await ask(agent, free, "blocked", question=FREE),
    }
    await ok(agent, "claim_node", node=checked["ref"])
    await ok(agent, "set_check", node=checked["ref"], item=_key(TESTS_CHECK), evidence="Ran them")
    await ok(
        agent,
        "finish_work",
        node=checked["ref"],
        outcome="blocked",
        note="Everything is built. A person should look at it on a phone.",
    )
    await ok(agent, "claim_node", node=noted["ref"])
    await ok(
        agent,
        "add_note",
        text="The webhook retries for a day",
        why="Read it in the provider's docs",
        nodes=[noted["ref"]],
    )
    lapsed = None
    quiet_story = None
    if lapse:
        quiet = await ok(
            agent, "create_node", actor="x", kind="story", title="Quiet", parent=epic["ref"]
        )
        quiet_story = quiet["ref"]
        lapsed = await task_under(agent, quiet["ref"], "Retry the call")
        await ok(agent, "claim_node", node=lapsed["ref"], lease_seconds=1)
        await anyio.sleep(1.5)
        async with connect(project.mcp_url, make_key(project, "second-agent")) as second:
            await ok(second, "claim_node", node=lapsed["ref"], lease_seconds=1)
        await anyio.sleep(1.5)
    return Waiting(
        story["ref"], quiet_story, choice, approval, free, checked, noted, lapsed, numbers
    )


def _key(words: str) -> str:
    return hashlib.sha256(words.encode()).hexdigest()[:8]


def answer(project: Project, who: Person, ask_number: int, words: str, **more: Any) -> Any:
    return as_person(who, project, "answer_ask", ask=ask_number, answer=words, **more)


def rows_of(needs: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for group in needs["groups"] for item in group["items"]]


async def on_the_page(page: Page) -> list[dict[str, Any]]:
    """The cards of the Needs page as drawn, in the order of the page."""
    found: list[dict[str, Any]] = await page.evaluate(
        """() => [...document.querySelectorAll('#needs-body li.needs-row')].map((r) => ({
            kind: r.dataset.kind, node: r.dataset.node, ask: r.dataset.ask || null,
        }))"""
    )
    return found


async def sees_the_list(page: Page, agent: Client) -> dict[str, Any]:
    """The page draws exactly the rows the server lists, each once (the groups of the page are the
    server's `level`, so their order is the page's own)."""
    wanted = await ok(agent, "needs_a_person")
    await expect(page.locator("#needs-body li.needs-row")).to_have_count(
        len(rows_of(wanted)), timeout=SLOW
    )
    drawn = await on_the_page(page)
    assert sorted((r["kind"], r["node"]) for r in drawn) == sorted(
        (i["kind"], i["node"]) for i in rows_of(wanted)
    ), (drawn, wanted)
    asks = sorted(i["ask"]["id"] for i in rows_of(wanted) if i["ask"])
    assert sorted(int(r["ask"]) for r in drawn if r["ask"]) == asks
    return dict(wanted)


def row(page: Page, kind: str, node: str) -> Any:
    return page.locator(f"#needs-body li.needs-row[data-kind='{kind}'][data-node='{node}']")


async def open_routine(page: Page) -> None:
    """Open the group of routine moves, if the page has one: a move the server calls routine is
    drawn there, closed, once the page has read it (M50). The list is waited for first."""
    await page.wait_for_function(
        "() => document.querySelectorAll('#needs-body li.needs-row').length > 0"
        " || document.querySelector('#needs-empty')",
        timeout=SLOW,
    )
    await expect(page.locator("#needs-body li.needs-row[data-reading='true']")).to_have_count(
        0, timeout=SLOW
    )
    group = page.locator("details.routine-group")
    if await group.count() and await group.get_attribute("open") is None:
        await group.locator("summary").click()


async def no_overflow(page: Page) -> None:
    found = await sideways(page)
    assert found["doc"] <= found["width"] + 1 and found["offenders"] == [], found


async def the_page_says_no_ask_number_the_old_way(page: Page) -> None:
    text = await page.evaluate("document.body.innerText")
    assert not ASK_WORD.search(text), ASK_WORD.search(text)


# -- the page ------------------------------------------------------------------------------------


async def test_the_page_draws_exactly_what_the_server_says_waits_by_story_in_order() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        waiting = await leave_waiting(agent, project, owner, lapse=True)
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        wanted = await sees_the_list(page, agent)
        assert wanted["total"] == 6 and wanted["left_out"] == 0
        assert {g["story"] for g in wanted["groups"]} == {waiting.story, waiting.quiet_story}

        # Nothing says there is nothing, and nothing says rows are left out.
        await expect(page.locator("#needs-empty")).to_have_count(0)
        await expect(page.locator("#needs-left-out")).to_have_count(0)
        # The sidebar's link is marked, and its number is the page's.
        await expect(page.locator("#needs-open")).to_have_attribute("aria-current", "page")
        await expect(page.locator("#sidebar-needs-count")).to_have_text("6")
        await expect(page.locator("#needs-summary")).to_have_attribute("data-total", "6")
        await expect(page.locator("#needs-reviewed")).to_have_text("Reviewed 0 of 6")

        # An ask: its code in the kind chip, the whole question, who is waiting, for how long, and
        # the work it is about; the title is the work's.
        number = waiting.numbers["choice"]
        choice_row = row(page, "ask", waiting.choice["ref"])
        await expect(choice_row).to_have_attribute("data-change", f"ask:{number}")
        await expect(choice_row.locator(".change-kind .ask-code")).to_have_text(f"Q-{number}")
        await expect(choice_row.locator(".change-kind-word")).to_have_text("Question")
        await expect(choice_row.locator("a.change-title")).to_have_text("Choose a provider")
        assert await choice_row.locator(".preview-question").text_content() == LONG_QUESTION
        (served,) = [i for i in rows_of(wanted) if i["ask"] and i["ask"]["id"] == number]
        assert served["reason"] != LONG_QUESTION and len(LONG_QUESTION) > 200
        by = choice_row.locator("li[data-fact=by]")
        await expect(by.locator(".actor-name")).to_have_text(HARNESS_AGENT)
        await expect(by.locator(".actor-agent")).to_have_count(1)
        await expect(choice_row.locator("li[data-fact=waiting] .needs-time")).to_have_text(
            re.compile(r"^(less than a minute|\d+ (minute|hour|day)s?)$")
        )
        # Its choices are on offer as the verbs of its bar, and no answer is picked beforehand.
        await expect(choice_row.locator("button.verdict")).to_have_text(CHOICES)
        await expect(choice_row.locator("button.verdict[aria-pressed=true]")).to_have_count(0)

        # An approval is Approve and Reject; a question with no choices has an Answer button.
        approval_row = row(page, "ask", waiting.approval["ref"])
        await expect(approval_row.locator(".change-kind-word")).to_have_text("Approval")
        await expect(approval_row.locator("button.verdict")).to_have_text(["Approve", "Reject…"])
        free_row = row(page, "ask", waiting.free["ref"])
        await expect(free_row.locator("button.verdict")).to_have_text("Answer…")

        # A check, notes and a claim that ran out: each says what it is and has its way to the item.
        check_row = row(page, "check", waiting.checked["ref"])
        await expect(check_row.locator(".preview-check")).to_contain_text(PERSON_CHECK)
        await expect(check_row.locator("li[data-fact=by] .actor-name")).to_have_text(HARNESS_AGENT)
        note_row = row(page, "note", waiting.noted["ref"])
        await expect(note_row.locator(".preview-note")).to_contain_text("The webhook retries")
        lapse_row = row(page, "lapse", waiting.lapsed["ref"] if waiting.lapsed else "")
        await expect(lapse_row.locator(".change-kind-word")).to_have_text("Stalled claim")
        await expect(lapse_row.locator("li[data-fact=by] .actor-name")).to_have_text("second-agent")
        # Only a verb decides: a stalled claim has Block and Drop, a check one verb, notes two.
        await expect(lapse_row.locator(DECIDING)).to_have_text(["Block it…", "Drop it…"])
        await expect(check_row.locator(DECIDING)).to_have_text(["Approve"])
        await expect(note_row.locator(DECIDING)).to_have_text(["Approve", "Reject…"])
        for each in (check_row, note_row, lapse_row):
            await expect(each.locator("a.change-open")).to_have_text("Review")
        await the_page_says_no_ask_number_the_old_way(page)

        # Open goes to the review page of the change, which has the way back to this list.
        await check_row.locator("a.change-open").click()
        await expect(page.locator("#review-page")).to_be_visible(timeout=SLOW)
        await page.locator("#review-back").click()
        await on_page(page, "needs")
        await sees_the_list(page, agent)
        assert w.problems == []


async def test_an_empty_list_says_one_sentence_and_the_sidebar_says_nothing() -> None:
    project = make_project(register())
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await expect(page.locator("#needs-body")).to_have_text(EMPTY, timeout=SLOW)
        await expect(page.locator("#needs-body *")).to_have_count(1)
        await expect(page.locator("#needs-empty")).to_have_text(EMPTY)
        # A review of nothing has no summary, no count of what was viewed and no rail.
        for hidden in ("#needs-overview", "#needs-rail", "#needs-rail-select"):
            await expect(page.locator(hidden)).to_be_hidden()
        await expect(page.locator("#needs-summary")).to_have_text("Nothing waiting")
        await expect(page.locator("#sidebar-needs-count")).to_be_hidden()
        await expect(page.locator("#sidebar-needs-count")).to_have_text("")
        # The link is there all the same: it is a destination, not an alert.
        await expect(page.locator("#needs-open")).to_be_visible()
        await expect(page.locator("#needs-open")).to_have_text("Needs a person")
        assert w.problems == []


# -- answering in the row --------------------------------------------------------------------------


async def test_choosing_approving_and_answering_in_words_in_the_row_takes_it_off_the_list() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        waiting = await leave_waiting(agent, project, owner)
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await sees_the_list(page, agent)
        assert (await ok(agent, "get_node", node=waiting.choice["ref"]))["status"] == "blocked"

        # A choice, with a comment: the comment box is opened by a button, beside the bar.
        choice_row = row(page, "ask", waiting.choice["ref"])
        await expect(choice_row.locator("div.decision-comment-box")).to_have_count(0)
        await choice_row.locator("button.decision-comment").click()
        field = choice_row.locator("div.decision-comment-box textarea")
        await expect(field).to_be_focused()
        await field.fill("They cover the markets we sell in.")
        await choice_row.locator("button.verdict[data-answer='Adyen']").click()
        await expect(choice_row).to_have_count(0, timeout=SLOW)
        given = (await ok(agent, "get_node", node=waiting.choice["ref"]))["asks"][0]
        assert (given["answered"], given["answer"], given["answered_by"]) == (
            True,
            "Adyen",
            "Priya Raman",
        )
        assert given["comment"] == "They cover the markets we sell in."
        # The work the question blocked is free again, by the server's own rule, not the page's.
        assert (await ok(agent, "get_node", node=waiting.choice["ref"]))["status"] == "ready"
        await sees_the_list(page, agent)

        # An approval: Reject… is the answer no, with the reason it asks for as the comment.
        approval_row = row(page, "ask", waiting.approval["ref"])
        await approval_row.locator("button.verdict[data-verb=reject]").click()
        reject = page.locator("dialog#reject-dialog[open]")
        await reject.locator("textarea").fill("Not on a Friday.")
        await reject.locator("button[type=submit]").click()
        await expect(approval_row).to_have_count(0, timeout=SLOW)
        gave = (await ok(agent, "get_node", node=waiting.approval["ref"]))["asks"][0]
        assert (gave["answer"], gave["comment"], gave["effect"]) == (
            "no",
            "Not on a Friday.",
            "parks",
        )

        # A question with no choices: a dialog that opens empty, with a field for the answer and one
        # for a comment.
        free_row = row(page, "ask", waiting.free["ref"])
        await free_row.locator("button.verdict[data-verb=answer]").click()
        dialog = page.locator("dialog#answer-ask[open]")
        await expect(dialog).to_be_visible()
        await nothing_open(page)
        await expect(dialog.locator("h2")).to_have_text(f"Answer Q-{waiting.numbers['free']}")
        await expect(dialog.locator(".ask-question")).to_have_text(FREE)
        await expect(dialog.locator("#ask-answer")).to_have_value("")
        await expect(dialog.locator("#ask-answer-comment")).to_have_value("")
        # An empty answer is the server's to refuse, in its words, and the dialog stays.
        await dialog.locator("button[type=submit]").click()
        await expect(dialog.locator(".problem")).to_contain_text("cannot be empty")
        await dialog.locator("#ask-answer").fill("Thank you for shopping with us")
        await dialog.locator("#ask-answer-comment").fill("Keep it short.")
        await dialog.locator("button[type=submit]").click()
        await expect(page.locator("dialog#answer-ask")).to_have_count(0, timeout=SLOW)
        await expect(free_row).to_have_count(0, timeout=SLOW)
        said = (await ok(agent, "get_node", node=waiting.free["ref"]))["asks"][0]
        assert (said["answer"], said["comment"]) == (
            "Thank you for shopping with us",
            "Keep it short.",
        )

        # What is left is what still waits: the check and the note. The agent that takes the work
        # reads the answers in its briefing, from the one that was blocked and is free again.
        left = await sees_the_list(page, agent)
        assert {i["kind"] for i in rows_of(left)} == {"check", "note"}
        answered_tasks = {waiting.choice["ref"], waiting.approval["ref"], waiting.free["ref"]}
        briefed_on: set[str] = set()
        async with connect(project.mcp_url, make_key(project, "next-agent")) as other:
            for _ in range(6):
                taken = await ok(other, "next_work")
                if not taken["found"]:
                    break
                ref = taken["work"]["ref"]
                if ref in answered_tasks:
                    (briefed,) = taken["context"]["asks"]
                    assert briefed["answered"] and briefed["answered_by"] == "Priya Raman", briefed
                briefed_on.add(ref)
        assert briefed_on == answered_tasks
        await expect(page.locator("#sidebar-needs-count")).to_have_text("2")
        await the_page_says_no_ask_number_the_old_way(page)
        assert w.problems == []


async def test_an_editor_answers_and_is_the_one_recorded_and_a_viewer_cannot() -> None:
    owner = register()
    project = make_project(owner)
    editor = join_project(project, "editor", "Edith Editor")
    viewer = join_project(project, "viewer", "Vera Viewer")
    async with connect(project.mcp_url, project.key) as agent:
        waiting = await leave_waiting(agent, project, owner)
        number = waiting.numbers["choice"]

        async with browser_page(project.address, token=None, person=viewer, name=None) as w:
            page = w.page
            await w.open("#/needs")
            await on_page(page, "needs")
            await sees_the_list(page, agent)
            # Every row, the question in full and the answers on offer as words, and no button
            # that answers.
            await expect(page.locator(f"#needs-body li.needs-row {DECIDING}")).to_have_count(0)
            await expect(page.locator("#needs-body .decision-bar")).to_have_count(0)
            await expect(
                row(page, "ask", waiting.choice["ref"]).locator("ul.ask-offered li")
            ).to_have_text(CHOICES)
            await expect(
                row(page, "ask", waiting.approval["ref"]).locator("ul.ask-offered li")
            ).to_have_text(["Yes", "No"])
            await expect(page.locator("#sidebar-needs-count")).to_have_text("5")
            # The item's page shows the question and no control either.
            await open_item(w, waiting.choice["ref"], "needs")
            await expect(part(page, "asks")).to_be_visible(timeout=SLOW)
            await expect(part(page, "asks").locator("button")).to_have_count(0)
            assert w.problems == []
        # The server refuses a viewer's answer whatever the page offers.
        refused = answer(project, viewer, number, "Stripe")
        assert refused.status in (400, 403), refused.body
        assert not (await ok(agent, "get_node", node=waiting.choice["ref"]))["asks"][0]["answered"]

        async with browser_page(project.address, token=None, person=editor, name=None) as w:
            page = w.page
            await w.open("#/needs")
            await on_page(page, "needs")
            target = row(page, "ask", waiting.choice["ref"])
            await target.locator("button.verdict[data-answer='Stripe']").click()
            await expect(target).to_have_count(0, timeout=SLOW)
            assert w.problems == []
        given = (await ok(agent, "get_node", node=waiting.choice["ref"]))["asks"][0]
        assert (given["answered_by"], given["answer"]) == ("Edith Editor", "Stripe")


async def test_a_refusal_is_shown_in_the_servers_words_and_nothing_is_stored() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        waiting = await leave_waiting(agent, project, owner)
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await sees_the_list(page, agent)

        # A secret in a free answer: the dialog stays open, says why in the server's words and does
        # not repeat the secret.
        free_row = row(page, "ask", waiting.free["ref"])
        await free_row.locator("button.verdict[data-verb=answer]").click()
        dialog = page.locator("dialog#answer-ask[open]")
        await dialog.locator("#ask-answer").fill(f"Use {A_TOKEN}")
        await dialog.locator("button[type=submit]").click()
        problem = dialog.locator(".problem")
        await expect(problem).to_contain_text("looks like", timeout=SLOW)
        shown = (await problem.text_content()) or ""
        assert A_TOKEN not in shown
        said = answer(project, owner, waiting.numbers["free"], f"Use {A_TOKEN}")
        assert said.status == 400
        plain(shown, said.body["message"])
        await dialog.locator("button", has_text="Cancel").click()
        await expect(page.locator("dialog#answer-ask")).to_have_count(0)
        stored = await ok(agent, "list_asks", status="answered")
        assert stored["total"] == 0
        assert (await ok(agent, "get_node", node=waiting.free["ref"]))["asks"][0]["answer"] is None

        # A page that did not hear that someone answered meanwhile: the press is refused where the
        # buttons are, as Q-12 and not "Ask 12", and the first answer stands.
        await hold_back_news(page)
        first = answer(project, owner, waiting.numbers["choice"], "Neither")
        assert first.status == 200
        stale = row(page, "ask", waiting.choice["ref"])
        await stale.locator("button.verdict[data-answer='Stripe']").click()
        problem = stale.locator("p.decision-problem")
        number = waiting.numbers["choice"]
        await expect(problem).to_contain_text(f"Q-{number} was already answered by", timeout=SLOW)
        assert "Ask " not in ((await problem.text_content()) or "")
        for button in await stale.locator("button.verdict").all():
            await expect(button).to_be_enabled()
        kept = (await ok(agent, "get_node", node=waiting.choice["ref"]))["asks"][0]
        assert kept["answer"] == "Neither"
        assert w.problems == []


# -- the number in the sidebar ---------------------------------------------------------------------


async def test_the_sidebar_counts_what_the_page_lists_and_follows_it_live() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, story, _ = await make_tree(agent)
        page = w.page
        badge = page.locator("#sidebar-needs-count")
        await w.open("#/board")
        await expect(page.locator("#needs-open")).to_be_visible(timeout=SLOW)
        # At zero it says nothing at all.
        await expect(badge).to_be_hidden()
        assert await badge.get_attribute("aria-label") is None

        # An agent asks: it appears with no reload, named for a screen reader.
        first = await task_under(agent, story["ref"], "First")
        one = await ask(agent, first, "blocked", question="Which one?", choices=["a", "b"])
        await expect(badge).to_have_text("1", timeout=SLOW)
        await expect(badge).to_have_attribute("aria-label", "1 waiting for a person")

        # A burst of asks is one more read of the list, not one for each: counted in the requests
        # the page made.
        before = len([r for r in w.requests if r.endswith("/tool/needs_a_person")])
        burst = [await task_under(agent, story["ref"], f"Burst {n}") for n in range(6)]
        numbers = [
            await ask(agent, t, "blocked", question=f"Question {n}?") for n, t in enumerate(burst)
        ]
        await expect(badge).to_have_text("7", timeout=SLOW)
        await expect(badge).to_have_attribute("aria-label", "7 waiting for a person")
        await page.wait_for_load_state("networkidle")
        reads = len([r for r in w.requests if r.endswith("/tool/needs_a_person")]) - before
        assert 1 <= reads <= 3, reads

        # The page and the sidebar agree, and both fall when a person answers (here through the
        # board's own call).
        await w.go("#/needs")
        await on_page(page, "needs")
        await expect(page.locator("#needs-body .needs-row")).to_have_count(7, timeout=SLOW)
        await expect(page.locator("#needs-summary")).to_have_attribute("data-total", "7")
        assert answer(project, owner, one, "a").status == 200
        await expect(badge).to_have_text("6", timeout=SLOW)
        await expect(page.locator("#needs-body .needs-row")).to_have_count(6, timeout=SLOW)
        for number in numbers:
            assert answer(project, owner, number, f"Done {number}").status == 200
        await expect(badge).to_be_hidden(timeout=SLOW)
        await expect(page.locator("#needs-body")).to_have_text(EMPTY)
        assert w.problems == []


# -- the item's page -------------------------------------------------------------------------------


async def test_an_items_questions_show_the_open_ask_with_controls_and_the_answer_in_full() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, story, quiet = await make_tree(agent)
        open_task = await task_under(agent, story["ref"], "Choose a provider")
        done_task = await task_under(agent, story["ref"], "Write the receipt")
        open_number = await ask(
            agent, open_task, "blocked", question=LONG_QUESTION, choices=CHOICES
        )
        done_number = await ask(agent, done_task, "released", question=FREE)
        long_comment = (
            "Because the fees are lower and the support is in our time zone. " * 6
        ).strip()
        assert (
            answer(project, owner, done_number, "Keep it short", comment=long_comment).status == 200
        )
        page = w.page

        # An item with no ask has no Questions section at all.
        await w.open("#/board")
        await open_item(w, quiet["ref"], "board")
        await expect(part(page, "asks")).to_have_count(0)

        # The answered one: its number, the question, who asked, and the answer, who gave it and the
        # comment, all in full, and nothing that edits it.
        await open_item(w, done_task["ref"], "board")
        asks = part(page, "asks")
        await expect(asks.locator("h3")).to_have_text("Questions1")
        entry = asks.locator(f"li.ask-item[data-ask='{done_number}']")
        await expect(entry).to_have_attribute("data-status", "answered")
        await expect(entry.locator(".ask-code")).to_have_text(f"Q-{done_number}")
        await expect(entry.locator(".ask-question")).to_have_text(FREE)
        await expect(entry.locator(".ask-by").first).to_contain_text(HARNESS_AGENT)
        chip = entry.locator(".ask-by .ask-effect").first
        await expect(chip).to_have_text("Work stays free")
        await chip.focus()
        await expect(page.locator(".hovercard:visible")).to_have_text(
            "The work was released and stays free to take."
        )
        await page.keyboard.press("Escape")
        await expect(entry.locator(".ask-answer-text")).to_have_text("Keep it short")
        assert (
            await entry.locator(".ask-comment-text").text_content()
        ) == f"Comment: {long_comment}"
        await expect(entry.locator(".ask-answered .ask-by")).to_contain_text("Priya Raman")
        await expect(entry.locator("button, input, textarea")).to_have_count(0)

        # The open one: the same controls as the page of what needs a person, and answering from
        # here updates the section and, by the server's own rule, the item's state.
        await open_item(w, open_task["ref"], "board")
        entry = part(page, "asks").locator(f"li.ask-item[data-ask='{open_number}']")
        await expect(entry).to_have_attribute("data-status", "open")
        await expect(entry.locator(".ask-question")).to_have_text(LONG_QUESTION)
        await expect(entry.locator(".ask-by .ask-effect").first).to_have_text("Blocks work")
        await expect(entry.locator(".ask-choice")).to_have_text(CHOICES)
        await expect(page.locator("#item-page .chip.status")).to_contain_text("Blocked")
        await entry.locator("button.ask-choice[data-answer='Neither']").click()
        await expect(entry).to_have_attribute("data-status", "answered", timeout=SLOW)
        await expect(entry.locator(".ask-answer-text")).to_have_text("Neither")
        await expect(entry.locator("button, input")).to_have_count(0)
        await expect(entry.locator(".ask-by .ask-effect").first).to_have_text("Blocks work")
        await expect(page.locator("#item-page .chip.status")).not_to_contain_text("Blocked")
        # The status chip and the strip are the server's: the state it has after the answer.
        status = (await ok(agent, "get_node", node=open_task["ref"]))["status"]
        assert status == "ready"

        # The history says it plainly, in full.
        history = part(page, "history")
        await expect(history).to_contain_text(f"asked Q-{open_number} about task", timeout=SLOW)
        await expect(history).to_contain_text(LONG_QUESTION.rstrip("."))
        await expect(history).to_contain_text("(choices: Stripe, Adyen, Neither)")
        await expect(history).to_contain_text(f"answered Q-{open_number} about task")
        await expect(history).to_contain_text("with Neither")
        await open_item(w, done_task["ref"], "board")
        await expect(part(page, "history")).to_contain_text(f"answered Q-{done_number} about task")
        said = f"saying: “{long_comment.rstrip('.')}”"
        await expect(part(page, "history")).to_contain_text(said)

        # Questions is a kind of change on the Activity page, so it can be filtered to.
        await w.open("#/activity?act_kind=asks")
        await on_page(page, "activity")
        shown = page.locator("li.event")
        await expect(shown).to_have_count(4, timeout=SLOW)
        assert {await e.get_attribute("data-action") for e in await shown.all()} == {
            "ask_raised",
            "ask_answered",
        }
        await the_page_says_no_ask_number_the_old_way(page)
        assert w.problems == []


async def test_the_agent_view_lists_the_asks_with_their_answers() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, story, quiet = await make_tree(agent)
        task = await task_under(agent, story["ref"], "Choose a provider")
        number = await ask(agent, task, "blocked", question=LONG_QUESTION, choices=CHOICES)
        page = w.page
        await w.open("#/board")
        await open_item(w, task["ref"], "board")
        await page.locator("#item-agent-view").click()
        text = page.locator("#agent-view-text")
        await expect(text).to_be_visible(timeout=SLOW)
        await expect(text).to_contain_text("asks: (1)")
        await expect(text).to_contain_text(f'Q-{number} "{LONG_QUESTION}" [open]')
        await expect(text).to_contain_text("choices: [Stripe, Adyen, Neither]")
        assert "answer:" not in ((await text.text_content()) or "")
        await page.locator("#agent-view-close").click()
        assert answer(project, owner, number, "Adyen", comment="Cheaper.").status == 200
        await page.locator("#item-agent-view").click()
        text = page.locator("#agent-view-text")
        await expect(text).to_contain_text(f'Q-{number} "{LONG_QUESTION}" [answered]', timeout=SLOW)
        await expect(text).to_contain_text('answer: "Adyen" comment: "Cheaper." (Priya Raman)')
        await page.locator("#agent-view-close").click()
        # An item with no ask says nothing about asks.
        await open_item(w, quiet["ref"], "board")
        await page.locator("#item-agent-view").click()
        text = page.locator("#agent-view-text")
        await expect(text).to_be_visible(timeout=SLOW)
        assert "asks" not in ((await text.text_content()) or "")
        assert w.problems == []


# -- a narrow window and the keyboard ------------------------------------------------------------


async def test_on_a_narrow_window_nothing_is_wider_than_it_and_the_keyboard_answers() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        waiting = await leave_waiting(agent, project, owner)
        eight = [f"An answer long enough to wrap on a narrow window, number {n}" for n in range(8)]
        wide = await task_under(
            agent, waiting.story, "A task whose title is long enough that it has to wrap " * 3
        )
        await ask(agent, wide, "blocked", question=LONG_QUESTION, choices=eight)
        page = w.page
        await page.set_viewport_size(NARROW)
        await w.open("#/needs")
        await on_page(page, "needs")
        await sees_the_list(page, agent)
        await no_overflow(page)
        await open_item(w, wide["ref"], "needs")
        await expect(part(page, "asks")).to_be_visible(timeout=SLOW)
        await no_overflow(page)
        await w.go("#/needs")
        await on_page(page, "needs")
        await expect(page.locator("#needs-body .needs-row")).to_have_count(6, timeout=SLOW)

        # Tab reaches the buttons of a row, and Enter on one answers it.
        target = row(page, "ask", waiting.approval["ref"])
        await page.locator("#page").focus()
        reached = False
        for _ in range(150):
            await page.keyboard.press("Tab")
            focused = await page.evaluate(
                "document.activeElement.closest('.needs-row')?.dataset.node ?? null"
            )
            is_choice = await page.evaluate("document.activeElement.matches('button.verdict')")
            if focused == waiting.approval["ref"] and is_choice:
                reached = True
                break
        assert reached
        await expect(target.locator("button.verdict").first).to_be_focused()
        await page.keyboard.press("Enter")
        await expect(target).to_have_count(0, timeout=SLOW)
        answered = (await ok(agent, "get_node", node=waiting.approval["ref"]))["asks"][0]
        assert answered["answered"] and answered["answer"] == "yes"
        assert w.problems == []
