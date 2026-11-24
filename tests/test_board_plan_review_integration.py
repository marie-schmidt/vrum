"""M51 on the board: the review conversation on the page of what needs a person, and a plan's page.

A person reads a drafted plan as a diff, writes comments on its lines (kept in the page until they
are sent), asks for changes with a summary, and the plan stays held, with the same number, while the
agent revises it. The revision comes back to the same page as `Revision 2`, read as what changed
since the revision before (`+`, `-` and `~` lines), with what the person asked for before under the
lines they were about. A decision names the revision the person reviewed, and one made on an old
revision is refused in the server's words. The plan has a page of its own where every revision is
read as it was submitted.

Driven in a real headless browser and compared with what a real MCP client reads (`list_plans`): the
lines of Changes equal `changes` and `tree`, the comments sent equal `feedback`, a viewer reads all
of it and has no write control. Every test makes an account and a project of its own."""

import re
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Locator, Page, ViewportSize, expect

from tests.board_helpers import (
    SLOW,
    browser_page,
    crumbs,
    hold_back_news,
    on_page,
    open_item,
    open_review,
    plain,
)
from tests.board_layout import SCREENS, Screen, blank_page, contrast, layout_context, settle
from tests.helpers import connect, ok, refused
from tests.test_board_needs_integration import DECIDING, no_overflow
from tests.test_board_review_integration import card_of, item_lines, refs_of
from tests.test_checks_integration import as_person
from tests.test_plan_review_integration import SPEC, continuing, drafted, read
from tests.world import Person, Project, join_project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NARROW: ViewportSize = {"width": 390, "height": 900}
FIELD_OPTIONS = [
    "The whole item",
    "Title",
    "Details",
    "Size",
    "Priority",
    "Start day",
    "Due day",
    "Where it hangs",
    "Links",
    "Acceptance criteria",
]
SIGN_WORDS = re.compile(r"^(added|removed|changed|existing): ")


def code(ref: str) -> str:
    kind, number = ref.split(":")
    return f"{kind[0].upper()}-{number}"


def asked(
    project: Project,
    who: Person,
    plan: int,
    revision: int,
    summary: str,
    comments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """What a person's press of Request changes does, through the board's own call."""
    reply = as_person(
        who,
        project,
        "request_plan_changes",
        plan=plan,
        revision=revision,
        summary=summary,
        comments=comments or [],
    )
    assert reply.status == 200, reply.body
    body: dict[str, Any] = reply.body
    return body


async def a_revision(
    agent: Client, number: int, refs: dict[str, str], note: str | None, **changes: Any
) -> dict[str, Any]:
    """The agent's revision: every item continues, the ones named in `changes` differ. `without`
    leaves items out (they are removed), `extra` adds new ones, `links` and `rules` are the whole
    set of each when given."""
    skipped: set[str] = set(changes.pop("without", ()))
    extra: list[dict[str, Any]] = changes.pop("extra", [])
    more = {name: changes.pop(name) for name in ("links", "rules") if name in changes}
    items = [one for one in continuing(refs, **changes) if one["key"] not in skipped]
    sent = await ok(
        agent,
        "revise_plan",
        plan=number,
        items=[*items, *extra],
        **more,
        **({"note": note} if note else {}),
    )
    return dict(sent)


def in_card(page: Page, number: int) -> Locator:
    """The plan's review page: the whole plan, its conversation and its bar are all on it."""
    return page.locator("#review-page")


def sign_lines(card: Locator, sign: str) -> Locator:
    """The lines of items (not their details, criteria or parts) with this sign."""
    return card.locator(f".diff-line.diff-item-line[data-sign='{sign}']")


async def open_needs(w: Any, number: int | None = None) -> Page:
    """Open the review page of the plan that waits for a person (the first, or `number`), from the
    list, as a person does."""
    page: Page = w.page
    await w.open("#/needs")
    await on_page(page, "needs")
    which = f"[data-plan='{number}']" if number else ""
    await (
        page.locator(f"#needs-body li.needs-row[data-kind='plan']{which}")
        .first.locator("a.change-open")
        .click()
    )
    await expect(page.locator("#review-page")).to_be_visible(timeout=SLOW)
    return page


async def say_comment(card: Locator, ref: str, text: str, field: str | None = None) -> None:
    """Write one comment under an item's line, as a person does."""
    await card.locator(f"button.diff-comment[data-ref='{ref}']").click()
    form = card.locator(f"form.comment-form[data-ref='{ref}']")
    await expect(form).to_be_visible()
    if field:
        await form.locator("select.comment-field").select_option(label=field)
    await form.locator("textarea.comment-text").fill(text)
    await form.locator("button.comment-add").click()
    await expect(form).to_have_count(0)


def pending(card: Locator, ref: str | None = None) -> Locator:
    return card.locator(
        "div.diff-comment-pending" + (f"[data-ref='{ref}']" if ref else ""),
    )


async def texts(locator: Locator) -> list[str]:
    return [(await one.text_content() or "").strip() for one in await locator.all()]


# -- writing comments ------------------------------------------------------------------


async def test_comments_are_written_changed_and_removed_and_not_sent_until_the_dialog() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent)
        whole = await read(agent, number)
        page = await open_needs(w)
        card = in_card(page, number)
        await expect(item_lines(card)).to_have_count(len(SPEC), timeout=SLOW)
        order = [one["ref"] for one in whole["tree"]]

        # Every item line has a button to comment on it; details and criteria lines have none.
        assert await refs_of(card.locator("button.diff-comment")) == order
        await expect(card.locator(".diff-text button.diff-comment")).to_have_count(0)
        await expect(card.locator(".diff-criterion button.diff-comment")).to_have_count(0)
        button = card.locator(f"button.diff-comment[data-ref='{refs['t2']}']")
        await expect(button).to_have_attribute("aria-label", f"Comment on {code(refs['t2'])}")
        # Nothing is written, no box is open and nothing is said to be unsent.
        await expect(card.locator("form.comment-form")).to_have_count(0)
        await expect(pending(card)).to_have_count(0)
        await expect(card.locator("details.change-unsent")).to_have_count(0)
        # The first revision says nothing about revisions, and has no way to read what changed.
        await expect(card.locator(".chip.change-revision")).to_have_count(0)
        await expect(card.locator("#review-mode")).to_have_count(0)
        await expect(card.locator(".change-diff")).to_have_attribute("data-mode", "whole")

        # The box opens under the line, empty, with the parts of an item to choose from.
        await button.click()
        form = card.locator(f"form.comment-form[data-ref='{refs['t2']}']")
        await expect(form).to_be_visible()
        assert await form.locator("textarea.comment-text").input_value() == ""
        assert await form.locator("select.comment-field").input_value() == ""
        assert await texts(form.locator("select.comment-field option")) == FIELD_OPTIONS
        line = card.locator(f".diff-line.diff-item-line[data-ref='{refs['t2']}']")
        follows = await form.evaluate(
            """(f, ref) => {
                const row = f.closest('.diff-annot');
                const line = document.querySelector(
                    `#review-diff .diff-item-line[data-ref="${ref}"]`);
                let next = line.nextElementSibling;
                while (next && !next.classList.contains('diff-annot')) {
                    if (next.classList.contains('diff-item-line')) return false;
                    next = next.nextElementSibling;
                }
                return next === row;
            }""",
            refs["t2"],
        )
        assert follows, "the box is under its own line"
        await expect(line).to_be_visible()
        # An empty comment is not added, and the box says so.
        await form.locator("button.comment-add").click()
        await expect(form.locator(".comment-problem")).to_contain_text("Write your comment")
        await expect(pending(card)).to_have_count(0)

        # A comment on the links of one task, one on the title of a story, one on a whole item.
        await form.locator("select.comment-field").select_option(label="Links")
        await form.locator("textarea.comment-text").fill(
            "Does this really have to wait for the supplier?"
        )
        await form.locator("button.comment-add").click()
        await expect(form).to_have_count(0)
        await say_comment(card, refs["s2"], "Name the stations.", "Title")
        await say_comment(card, refs["t3"], "Why is this the smallest?")

        # Each is said to be not sent yet, with the words it is about as the server will keep them.
        mine = pending(card, refs["t2"])
        await expect(mine).to_have_count(1)
        await expect(mine).to_contain_text("Not sent yet")
        await expect(mine.locator("blockquote.comment-quote")).to_have_text(
            f"needs_first {refs['t1']}"
        )
        await expect(mine.locator(".comment-body")).to_have_text(
            "Does this really have to wait for the supplier?"
        )
        await expect(mine).to_have_attribute("data-field", "links")
        await expect(pending(card, refs["s2"]).locator("blockquote")).to_have_text("Weather covers")
        await expect(pending(card, refs["t3"]).locator("blockquote")).to_have_text("Order covers")
        # A comment on the whole of an item has no part: the attribute is not there.
        assert await pending(card, refs["t3"]).get_attribute("data-field") is None
        # The bar says how many are unsent, in words that are a count and nothing more.
        await expect(card.locator("#review-bar .decision-state")).to_have_text("3 unsent")
        await expect(card.locator("p.change-unsent-line")).to_have_count(0)
        await expect(card.locator("details.change-unsent")).not_to_have_attribute("open", "")
        await expect(card.locator("details.change-unsent summary")).to_have_text(
            "3 unsent comments"
        )
        # Each unsent comment is listed, and its entry takes the focus to the comment on its line.
        await card.locator("details.change-unsent summary").click()
        listed = card.locator("details.change-unsent button.unsent-go")
        await expect(listed).to_have_count(3)
        await listed.first.click()
        await expect(pending(card, refs["t2"]).locator("button.comment-edit")).to_be_focused()

        # A comment is changed in the same box, filled, and taken away with Remove.
        await pending(card, refs["s2"]).locator("button.comment-edit").click()
        box = card.locator(f"form.comment-form[data-ref='{refs['s2']}']")
        assert await box.locator("textarea.comment-text").input_value() == "Name the stations."
        assert await box.locator("select.comment-field").input_value() == "title"
        await expect(box.locator("button.comment-add")).to_have_text("Save comment")
        await expect(pending(card, refs["s2"])).to_have_count(0)
        await box.locator("select.comment-field").select_option(label="Details")
        await box.locator("textarea.comment-text").fill("Name the stations and say how many bikes.")
        await box.locator("button.comment-add").click()
        await expect(pending(card, refs["s2"]).locator(".comment-body")).to_have_text(
            "Name the stations and say how many bikes."
        )
        await expect(pending(card, refs["s2"])).to_have_attribute("data-field", "body")
        await expect(pending(card, refs["s2"]).locator("blockquote")).to_have_text("none")
        await pending(card, refs["t3"]).locator("button.comment-remove").click()
        await expect(pending(card)).to_have_count(2)
        await expect(card.locator("#review-bar .decision-state")).to_have_text("2 unsent")

        # Cancel and Escape leave nothing behind.
        await card.locator(f"button.diff-comment[data-ref='{refs['s1']}']").click()
        await card.locator(f"form.comment-form[data-ref='{refs['s1']}'] textarea").fill(
            "Half a thought"
        )
        await card.locator(
            f"form.comment-form[data-ref='{refs['s1']}'] button.comment-cancel"
        ).click()
        await expect(card.locator("form.comment-form")).to_have_count(0)
        await card.locator(f"button.diff-comment[data-ref='{refs['s1']}']").click()
        await card.locator(f"form.comment-form[data-ref='{refs['s1']}'] textarea").press("Escape")
        await expect(card.locator("form.comment-form")).to_have_count(0)
        await expect(pending(card)).to_have_count(2)

        # They live in the page, not at the server: no feedback and no request was made.
        assert (await read(agent, number))["feedback"] == []
        feed = (await ok(agent, "changes_since", limit=200))["items"]
        assert not [e for e in feed if e["action"] == "plan_changes_requested"]
        assert w.problems == []


# -- asking for changes ----------------------------------------------------------------

SUMMARY = "Cover the busiest stations first."
NOTE = "Named the stations, described the fitting and dropped the covers order."


async def three_comments(card: Locator, refs: dict[str, str]) -> None:
    await say_comment(card, refs["s2"], "Name the stations.", "Title")
    await say_comment(card, refs["t2"], "Say how the bikes are fitted.", "Details")
    await say_comment(card, refs["t3"], "Why is this the smallest?")


async def request_in_page(page: Page, card: Locator, summary: str) -> None:
    """What a person does with the dialog: open it, say what should change, send it."""
    await card.locator("button.needs-changes").click()
    dialog = page.locator("#changes-dialog")
    await dialog.locator("#changes-summary").fill(summary)
    await dialog.locator("button[type=submit]").click()
    await expect(dialog).to_have_count(0, timeout=SLOW)


async def test_request_changes_sends_exactly_the_comments_and_the_plan_waits_for_its_agent() -> (
    None
):
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent, title="Winter plan")
        page = await open_needs(w)
        card = in_card(page, number)
        await expect(item_lines(card)).to_have_count(len(SPEC), timeout=SLOW)
        await three_comments(card, refs)
        quoted = [
            (await pending(card, refs[key]).locator("blockquote").text_content() or "")
            for key in ("s2", "t2", "t3")
        ]
        assert quoted == ["Weather covers", "none", "Order covers"]

        # What was written stays in the page when it is drawn again: news of something else, the
        # split view, and going to an item and coming back.
        await card.evaluate("el => { el.dataset.probe = 'before' }")
        other = await ok(agent, "create_node", actor="x", kind="epic", title="Unrelated")
        await ok(agent, "update_node", node=other["ref"], expected_version=1, title="Unrelated one")
        await page.wait_for_function(
            "() => !document.querySelector('#review-page').dataset.probe",
            timeout=SLOW,
        )
        card = in_card(page, number)
        await expect(pending(card)).to_have_count(3)
        await page.locator("#review-view button[data-view='split']").click()
        await expect(pending(card)).to_have_count(3)
        await page.locator("#review-view button[data-view='unified']").click()
        await card.locator(f"a.diff-item[data-ref='{refs['t1']}']").click()
        await on_page(page, "item")
        await page.locator("#back-to-view").click()
        await on_page(page, "review")
        card = in_card(page, number)
        await expect(pending(card)).to_have_count(3, timeout=SLOW)
        await expect(card.locator("details.change-unsent summary")).to_have_text(
            "3 unsent comments"
        )

        # The dialog opens empty, and lists the comments it will send, read only, in order.
        await card.locator("button.needs-changes").click()
        dialog = page.locator("#changes-dialog")
        await expect(dialog).to_be_visible()
        assert await dialog.locator("#changes-summary").input_value() == ""
        listed = dialog.locator("ul.changes-comments li")
        await expect(listed).to_have_count(3)
        assert [
            await (one.locator(".changes-comment-item")).text_content()
            for one in await listed.all()
        ] == [
            code(refs["s2"]),
            code(refs["t2"]),
            code(refs["t3"]),
        ]
        assert [
            await one.locator("blockquote").text_content() for one in await listed.all()
        ] == quoted
        await expect(dialog.locator("textarea, input")).to_have_count(1)
        # Cancel changes nothing and keeps the comments.
        await dialog.get_by_role("button", name="Cancel").click()
        await expect(dialog).to_have_count(0)
        await expect(pending(card)).to_have_count(3)

        # A request with no summary is refused by the server in its words, in the dialog, with
        # everything still there.
        words = as_person(
            owner,
            project,
            "request_plan_changes",
            plan=number,
            revision=1,
            summary="  ",
            comments=[],
        ).body["message"]
        await card.locator("button.needs-changes").click()
        await dialog.locator("button[type=submit]").click()
        await expect(dialog.locator("form > .problem")).to_contain_text(words)
        await expect(dialog.locator("ul.changes-comments li")).to_have_count(3)
        assert (await read(agent, number))["feedback"] == []
        await dialog.locator("#changes-summary").fill(SUMMARY)
        await dialog.locator("button[type=submit]").click()
        await expect(dialog).to_have_count(0, timeout=SLOW)

        # The plan is the agent's turn now: the page moves on to the list, the plan is in the
        # group of plans that wait for their agent, and the sidebar has nothing to count.
        await expect(card).to_have_count(0, timeout=SLOW)
        await on_page(page, "needs")
        await expect(page.locator("#needs-body li.needs-row")).to_have_count(0)
        group = page.locator("details.agent-group")
        await expect(group.locator("summary .group-count")).to_have_text("1")
        await group.locator("summary").click()
        entry = group.locator(f"li.waiting-row[data-plan='{number}']")
        await expect(entry.locator(".change-kind .item-code")).to_have_text(f"Plan {number}")
        await expect(entry.locator("li[data-fact=by] .actor-name")).to_have_text("Priya Raman")
        link = entry.locator("a.plan-link")
        await expect(link).to_have_text("Winter plan")
        await expect(link).to_have_attribute("href", f"#/review?change=plan:{number}")
        await expect(page.locator("#sidebar-needs-count")).to_be_hidden()
        await expect(
            page.locator("#notices .notice", has_text=f"Asked for changes to Plan {number}.")
        ).to_be_visible()

        # The server has exactly what the page showed, said by the person who was signed in.
        given_to_agent = await read(agent, number)
        assert (given_to_agent["state"], given_to_agent["revision"]) == ("changes_requested", 1)
        (given,) = given_to_agent["feedback"]
        assert (given["by"], given["by_kind"], given["revision"], given["summary"]) == (
            "Priya Raman",
            "person",
            1,
            SUMMARY,
        )
        assert [(c["item"], c["field"], c["text"], c["quoted"]) for c in given["comments"]] == [
            (refs["s2"], "title", "Name the stations.", "Weather covers"),
            (refs["t2"], "body", "Say how the bikes are fitted.", "none"),
            (refs["t3"], None, "Why is this the smallest?", "Order covers"),
        ]
        assert [c["quoted"] for c in given["comments"]] == quoted
        # Nothing was freed or dropped: every item is held by the plan.
        for ref in refs.values():
            assert (await ok(agent, "get_node", node=ref))["held_by"] == [f"plan:{number}"]
        assert (await ok(agent, "next_work", peek=True))["found"] is False
        # The title is a link to the plan's page.
        await link.click()
        await on_page(page, "review")
        await expect(page.locator(".plan-state")).to_have_text("Waiting for the agent")
        assert w.problems == []


# -- the revision ---------------------------------------------------------------------------


async def thread_follows_its_line(card: Locator, thread: Locator, ref: str) -> bool:
    """Whether a row of conversation sits under the line of the item it is about, before any other
    item's line."""
    found = await thread.evaluate(
        """(row, ref) => {
            let above = row.previousElementSibling;
            while (above && !above.classList.contains('diff-item-line')) {
                above = above.previousElementSibling;
            }
            return !!above && above.dataset.ref === ref;
        }""",
        ref,
    )
    return bool(found)


async def test_a_revision_comes_back_as_what_changed_with_earlier_comments_under_their_lines() -> (
    None
):
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent, title="Winter plan")
        page = await open_needs(w)
        card = in_card(page, number)
        await expect(item_lines(card)).to_have_count(len(SPEC), timeout=SLOW)
        await three_comments(card, refs)
        await request_in_page(page, card, SUMMARY)
        await on_page(page, "needs")
        await expect(page.locator("details.agent-group")).to_be_visible(timeout=SLOW)

        # The agent revises: a story's title, a task's details and size, one task out, one added.
        # It is the same plan, back on the list as revision 2, with no reload.
        sent = await a_revision(
            agent,
            number,
            refs,
            NOTE,
            s2={"title": "Weather covers for the four busiest stations"},
            t2={"body": "Fit the bikes.\nOne at a time.", "size": "l"},
            without=["t3"],
            extra=[
                {
                    "key": "t4",
                    "kind": "task",
                    "title": "Measure the four stations",
                    "parent": "s2",
                    "size": "s",
                }
            ],
        )
        assert (sent["plan"], sent["revision"]) == (number, 2)
        listed = card_of(page, f"plan:{number}")
        await expect(listed.locator(".chip.change-revision")).to_have_text(
            "Revision 2", timeout=SLOW
        )
        await expect(listed).to_have_attribute("data-revision", "2")
        page = await open_needs(w, number)
        card = in_card(page, number)
        await expect(card.locator(".chip.change-revision")).to_have_text("Revision 2", timeout=SLOW)
        await expect(card.locator("#review-diff")).to_have_attribute("data-revision", "2")
        revision = card.locator("li.plan-revision[data-revision='2']")
        await expect(revision.locator("p.plan-revision-note")).to_have_text(NOTE)
        server = await read(agent, number)
        assert (server["state"], server["revision"], server["shown"]) == ("proposed", 2, 2)
        added = [c["ref"] for c in server["changes"] if c["change"] == "added"]
        removed = [c["ref"] for c in server["changes"] if c["change"] == "removed"]
        changed = [c["ref"] for c in server["changes"] if c["change"] == "changed"]
        assert (len(added), len(removed), len(changed)) == (1, 1, 2) and removed == [refs["t3"]]

        # What changed is the way to read it: a line for each item that is new (+), gone (-) or
        # different (~), equal to the server's list, and a fold for the rest.
        diff = card.locator(".change-diff")
        await expect(diff).to_have_attribute("data-mode", "delta")
        await expect(diff.locator(".diff-hunk").first).to_have_text(
            "Revision 2: 1 added, 2 changed, 1 removed"
        )
        assert await refs_of(sign_lines(card, "+")) == added
        assert await refs_of(sign_lines(card, "-")) == removed
        assert await refs_of(sign_lines(card, "~")) == changed
        unchanged = len(server["tree"]) - len(added) - len(changed)
        fold = card.locator("button.diff-fold[data-fold='unchanged']")
        await expect(fold).to_have_text(f"{unchanged} more items, unchanged")
        # Each changed part is a line of what it was and a line of what it is.
        parts = {c["ref"]: c["fields"] for c in server["changes"] if c["change"] == "changed"}
        for ref, fields in parts.items():
            found = await card.locator(
                f".diff-line[data-ref='{ref}'][data-part]:not([data-sign='.'])"
            ).evaluate_all("els => [...new Set(els.map(e => e.dataset.part))]")
            assert sorted(found) == sorted(fields), (ref, found, fields)
        title_ref = refs["s2"]
        lines = card.locator(f".diff-line[data-ref='{title_ref}'][data-part='title']")
        assert [await words_in(one) for one in await lines.all()] == [
            "Title: Weather covers",
            "Title: Weather covers for the four busiest stations",
        ]
        assert [await one.get_attribute("data-sign") for one in await lines.all()] == ["-", "+"]
        # An added item is the whole line as in the first read, with its details when it has them.
        await expect(sign_lines(card, "+")).to_contain_text("Measure the four stations")
        await expect(sign_lines(card, "-")).to_contain_text("Order covers")

        # What the person asked for before sits under the lines it was about, every comment of every
        # earlier feedback, and the one about the item that is gone under its removed line.
        (feedback,) = server["feedback"]
        threads = card.locator("div.diff-thread")
        await expect(threads).to_have_count(3)
        for index, comment in enumerate(feedback["comments"]):
            thread = card.locator(
                f"div.diff-thread[data-feedback='{feedback['id']}'][data-comment='{index}']"
            )
            await expect(thread).to_have_attribute("data-ref", comment["item"])
            await expect(thread.locator("blockquote")).to_have_text(comment["quoted"])
            await expect(thread.locator(".comment-body")).to_have_text(comment["text"])
            await expect(thread.locator(".thread-by")).to_contain_text("You, on revision 1")
            assert await thread_follows_its_line(card, thread, comment["item"])
        gone = card.locator(f"div.diff-thread[data-ref='{refs['t3']}']")
        assert await thread_follows_its_line(card, gone, refs["t3"])
        await expect(card.locator(f".diff-item-line[data-ref='{refs['t3']}']")).to_have_attribute(
            "data-sign", "-"
        )
        # The feedback itself is above the diff, open for the last one, with its summary in full.
        box = card.locator(f"section.change-feedback details[data-feedback='{feedback['id']}']")
        await expect(box).to_have_attribute("open", "")
        await expect(box.locator("summary")).to_contain_text("Your feedback on revision 1")
        await expect(box.locator("summary .feedback-count")).to_have_text("3 comments")
        await expect(box.locator(".feedback-summary")).to_have_text(SUMMARY)

        # Comments can be written on a changed line, and on an added one, but not on a removed one.
        await expect(sign_lines(card, "~").locator("button.diff-comment")).to_have_count(2)
        await expect(sign_lines(card, "+").locator("button.diff-comment")).to_have_count(1)
        await expect(sign_lines(card, "-").locator("button.diff-comment")).to_have_count(0)

        # Marking it viewed (from the list's own mark) leaves the diff, threads and the feedback
        # where they are on this page.
        await card.locator("input.change-viewed").check()
        await expect(card.locator(".change-diff")).to_be_visible()
        await expect(card.locator("section.change-feedback")).to_be_visible()
        await expect(box.locator(".feedback-summary")).to_be_visible()
        await card.locator("input.change-viewed").uncheck()
        await expect(card.locator("div.diff-thread").first).to_be_visible()

        # The whole plan is the tree of revision 2 exactly (the removed item is not in it), the
        # earlier comments are under their lines, and the one whose item is gone is listed.
        toggle = card.locator("div.segmented#review-mode")
        await expect(toggle.locator("button[data-mode='delta']")).to_have_attribute(
            "aria-pressed", "true"
        )
        await toggle.locator("button[data-mode='whole']").click()
        await expect(card.locator(".change-diff")).to_have_attribute("data-mode", "whole")
        assert await refs_of(sign_lines(card, "+")) == [one["ref"] for one in server["tree"]]
        await expect(sign_lines(card, "-")).to_have_count(0)
        await expect(card.locator("div.diff-thread")).to_have_count(2)
        orphan = card.locator(f"li.feedback-orphan[data-ref='{refs['t3']}']")
        await expect(orphan).to_contain_text("Why is this the smallest?")
        await toggle.locator("button[data-mode='delta']").click()
        await expect(card.locator(".change-diff")).to_have_attribute("data-mode", "delta")
        # One press on the fold of what did not change opens the whole plan.
        await card.locator("button.diff-fold[data-fold='unchanged']").click()
        await expect(card.locator(".change-diff")).to_have_attribute("data-mode", "whole")
        assert await refs_of(sign_lines(card, "+")) == [one["ref"] for one in server["tree"]]

        # The decision names revision 2 and is the signed-in person's. The removed item stays
        # dropped and is never freed; the rest is free to take.
        await card.locator("button.needs-yes").click()
        await expect(card).to_have_count(0, timeout=SLOW)
        decided = await read(agent, number)
        assert (decided["state"], decided["revision"]) == ("approved", 2)
        assert (decided["decided_by"], decided["decided_at"] is not None) == ("Priya Raman", True)
        feed = (await ok(agent, "changes_since", limit=200))["items"]
        (approval,) = [e for e in feed if e["action"] == "plan_approved"]
        assert approval["actor"] == "Priya Raman" and approval["after"]["revision"] == 2
        assert (await ok(agent, "get_node", node=refs["t3"]))["status"] == "dropped"
        free = {n["ref"] for n in (await ok(agent, "ready_work", limit=200))["items"]}
        assert refs["t3"] not in free and refs["t1"] in free and refs["s2"] in free
        await refused(agent, "claim_node", node=refs["t3"])
        assert w.problems == []


async def words_in(line: Locator) -> str:
    """What a line says, without the "added: " a screen reader hears first."""
    return SIGN_WORDS.sub("", (await line.locator(".diff-code").text_content()) or "").strip()


async def test_a_revision_over_unsent_comments_says_so_and_resets_viewed() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent, title="Winter plan")
        page = await open_needs(w)
        card = in_card(page, number)
        await expect(item_lines(card)).to_have_count(len(SPEC), timeout=SLOW)
        await say_comment(card, refs["s2"], "Name the stations.", "Title")
        await say_comment(card, refs["t2"], "Say how the bikes are fitted.", "Details")
        await card.locator("input.change-viewed").check()
        await expect(card.locator("input.change-viewed")).to_be_checked()

        # The agent revises the plan while the person reads it (no request is needed for that).
        title = "Weather covers for the four busiest stations"
        await a_revision(agent, number, refs, NOTE, s2={"title": title})
        card = in_card(page, number)
        await expect(card.locator(".chip.change-revision")).to_have_text("Revision 2", timeout=SLOW)
        lost = card.locator("p.change-lost")
        await expect(lost.locator("strong")).to_have_text("Revision 2 arrived")
        await expect(lost.locator("span")).to_have_text("Not kept: 2 unsent comments")
        # What they said is behind a closed disclosure, so that they can be written again.
        said = card.locator("details.change-lost-list")
        await expect(said).not_to_have_attribute("open", "")
        await said.locator("summary").click()
        assert await texts(said.locator("li")) == [
            f"{code(refs['s2'])} (title): Name the stations.",
            f"{code(refs['t2'])} (details): Say how the bikes are fitted.",
        ]
        await expect(pending(card)).to_have_count(0)
        await expect(card.locator("details.change-unsent")).to_have_count(0)
        # They were never sent, and the earlier "Viewed" is gone: the revision is new to read.
        assert (await read(agent, number))["feedback"] == []
        await expect(card.locator("input.change-viewed")).not_to_be_checked()
        await expect(card.locator(".chip.change-changed")).to_have_text("Changed since viewed")
        await expect(card.locator(".change-diff")).to_be_visible()

        # It is said once: dismissing it takes it away, and so does leaving the page.
        await card.locator(".change-lost-box button.notice-close").click()
        await expect(lost).to_have_count(0)
        # Only what the revision changed has a line to comment on: the whole plan has all of them.
        await say_comment(card, refs["s2"], "Is this the right supplier?")
        await a_revision(agent, number, refs, "Again.", s2={"title": "Covers for four stations"})
        card = in_card(page, number)
        await expect(card.locator(".chip.change-revision")).to_have_text("Revision 3", timeout=SLOW)
        await expect(card.locator("p.change-lost strong")).to_have_text("Revision 3 arrived")
        await expect(card.locator("p.change-lost span")).to_have_text("Not kept: 1 unsent comment")
        await w.go("#/activity")
        await on_page(page, "activity")
        page = await open_needs(w, number)
        await expect(in_card(page, number).locator(".chip.change-revision")).to_have_text(
            "Revision 3", timeout=SLOW
        )
        await expect(page.locator("p.change-lost")).to_have_count(0)
        assert w.problems == []


# -- a decision on a moved on revision --------------------------------------------------


async def test_a_decision_on_a_moved_on_revision_is_refused_in_words_and_read_again() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent, title="Winter plan")
        page = await open_needs(w)
        card = in_card(page, number)
        await expect(item_lines(card)).to_have_count(len(SPEC), timeout=SLOW)
        # The board hears of nothing from now on, so the revision arrives behind its back.
        await hold_back_news(page)
        await a_revision(agent, number, refs, "Renamed.", s2={"title": "Four busiest stations"})
        await expect(card.locator(".chip.change-revision")).to_have_count(0)
        stale = as_person(
            owner, project, "approve_plan", plan=number, approved=True, revision=1
        ).body["message"]
        assert "is at revision 2 now, and you reviewed revision 1" in stale
        await card.locator("button.needs-yes").click()
        # The server's words are beside the buttons, nothing was decided, and the page has read the
        # plan again at once (the news is still held back): the card is revision 2.
        problem_line = card.locator("#review-bar p.decision-problem")
        await expect(problem_line).to_contain_text(stale)
        await expect(card.locator(".chip.change-revision")).to_have_text("Revision 2", timeout=SLOW)
        await expect(problem_line).to_contain_text(stale)
        assert (await read(agent, number))["state"] == "proposed"
        await expect(card.locator(".change-diff")).to_have_attribute("data-mode", "delta")

        # A request for changes is refused the same way, in the dialog, and the card catches up.
        await card.locator("button.needs-changes").click()
        dialog = page.locator("#changes-dialog")
        await a_revision(agent, number, refs, "Again.", s2={"title": "Covers for four stations"})
        await dialog.locator("#changes-summary").fill("Smaller, please.")
        await dialog.locator("button[type=submit]").click()
        late = as_person(
            owner,
            project,
            "request_plan_changes",
            plan=number,
            revision=2,
            summary="Smaller, please.",
            comments=[],
        ).body["message"]
        assert "is at revision 3 now, and you reviewed revision 2" in late
        await expect(dialog.locator("form > .problem")).to_contain_text(late)
        await expect(card.locator(".chip.change-revision")).to_have_text("Revision 3", timeout=SLOW)
        await dialog.get_by_role("button", name="Cancel").click()
        again = await read(agent, number)
        assert (again["state"], again["revision"], again["feedback"]) == ("proposed", 3, [])
        # A rejection of an old revision is refused as well.
        await hold_back_news(page)
        await a_revision(agent, number, refs, "Once more.", s2={"title": "Covers for 4 stations"})
        await card.locator("button.needs-no").click()
        await page.locator("#reject-reason").fill("No.")
        await page.locator("#reject-dialog button[type=submit]").click()
        await expect(page.locator("#reject-dialog form > .problem")).to_contain_text(
            "is at revision 4 now, and you reviewed revision 3"
        )
        assert (await read(agent, number))["state"] == "proposed"
        assert w.problems == []


# -- a plan changed outside a revision ------------------------------------------------------


async def test_a_plan_changed_outside_a_revision_says_so_and_approval_is_refused_in_words() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent, title="Winter plan")
        page = await open_needs(w)
        card = in_card(page, number)
        await expect(item_lines(card)).to_have_count(len(SPEC), timeout=SLOW)
        await expect(card.locator("section.change-drift")).to_have_count(0)
        node = await ok(agent, "get_node", node=refs["t1"])
        await ok(
            agent,
            "update_node",
            node=refs["t1"],
            expected_version=node["version"],
            title="Choose another supplier",
        )
        drift = card.locator("section.change-drift")
        await expect(drift).to_be_visible(timeout=SLOW)
        await expect(drift).to_have_attribute("role", "alert")
        assert (await read(agent, number))["drift"] == [{"ref": refs["t1"], "fields": ["title"]}]
        await expect(drift.locator("p").first).to_have_text(
            f"Changed since revision 1, outside a revision: {code(refs['t1'])} (title)."
        )
        await expect(drift.locator("a.drift-item")).to_have_attribute("data-ref", refs["t1"])
        # Nothing explains what to do: the buttons are the choices.
        await expect(drift.locator("p")).to_have_count(1)
        # Approve is not disabled: the server refuses it, and its words are shown beside it.
        approve = card.locator("button.needs-yes")
        await expect(approve).to_be_enabled()
        words = as_person(
            owner, project, "approve_plan", plan=number, approved=True, revision=1
        ).body["message"]
        assert "was changed after revision 1 was submitted" in words
        await approve.click()
        problem = card.locator("#review-bar p.decision-problem")
        await expect(problem).to_contain_text("was changed after revision 1 was submitted")
        plain((await problem.text_content()) or "", words)
        assert (await read(agent, number))["state"] == "proposed"
        await expect(card.locator("section.change-drift")).to_be_visible()

        # A revision brings the plan in line with what a person read, and then it can be approved.
        await a_revision(agent, number, refs, "Put the supplier back.")
        await expect(card.locator(".chip.change-revision")).to_have_text("Revision 2", timeout=SLOW)
        await expect(card.locator("section.change-drift")).to_have_count(0)
        await expect(card.locator("#review-bar p.decision-problem")).to_have_text("")
        await card.locator("button.needs-yes").click()
        await expect(card).to_have_count(0, timeout=SLOW)
        assert (await read(agent, number))["state"] == "approved"
        assert w.problems == []


# -- the plan's own page --------------------------------------------------------------------

T4 = {"key": "t4", "kind": "task", "title": "Measure the four stations", "parent": "s2"}
TITLE = "Weather covers for the four busiest stations"


async def three_revisions(
    agent: Client, owner: Person, project: Project
) -> tuple[int, dict[str, str]]:
    """A plan with three revisions and feedback on the first two: asked for changes, revised (a
    title, an item taken out, one added), asked again about the new one, revised again."""
    number, refs = await drafted(agent, title="Winter plan")
    asked(
        project,
        owner,
        number,
        1,
        SUMMARY,
        [
            {"item": refs["s2"], "field": "title", "text": "Name the stations."},
            {"item": refs["t3"], "text": "Drop this one."},
        ],
    )
    first = await a_revision(
        agent,
        number,
        refs,
        NOTE,
        s2={"title": TITLE},
        without=["t3"],
        extra=[{**T4, "size": "s"}],
    )
    refs["t4"] = first["created"][0]
    asked(
        project,
        owner,
        number,
        2,
        "Size the new task.",
        [{"item": refs["t4"], "field": "size", "text": "Is small right?"}],
    )
    await a_revision(
        agent,
        number,
        refs,
        "Made it medium.",
        s2={"title": TITLE},
        without=["t3"],
        extra=[{**T4, "ref": refs["t4"], "size": "m"}],
    )
    return number, refs


async def test_the_plans_page_reads_every_revision_as_it_was_submitted_and_decides_nothing() -> (
    None
):
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await three_revisions(agent, owner, project)
        page = w.page
        await w.open(f"#/review?change=plan:{number}")
        await on_page(page, "review")
        current = await read(agent, number)
        assert (current["state"], current["revision"]) == ("proposed", 3)

        # Its title, its code in the kind chip, and the way back to the list.
        await expect(page.locator("h1#review-title")).to_have_text("Winter plan")
        await expect(page.locator(".change-kind .item-code")).to_have_text(f"Plan {number}")
        await expect(page.locator("a.plan-review-link")).to_have_count(0)
        select = page.locator("select#review-revision")
        assert await texts(select.locator("option")) == [
            "Revision 1",
            "Revision 2",
            "Revision 3 (current)",
        ]
        assert await select.input_value() == "3"
        assert await crumbs_of(page) == ["Needs a person", f"Plan {number}"]
        # The back arrow goes to the page of what needs a person when nothing came before.
        await expect(page.locator("#review-back")).to_have_attribute("href", "#/needs")
        # Every revision is listed with who made it and what they said.
        authors = page.locator("li.plan-revision .plan-revision-author")
        await expect(authors).to_have_count(3)

        # Every revision, as the server reads it: lines equal to `tree` (whole) or to `changes`.
        for revision in (1, 2, 3):
            await select.select_option(str(revision))
            await expect(page.locator("#review-diff")).to_have_attribute(
                "data-revision", str(revision)
            )
            if revision < 3:
                assert (await page.evaluate("location.hash")).endswith(f"&revision={revision}")
            else:
                assert "revision=" not in await page.evaluate("location.hash")
            server = await read(agent, number, revision=revision)
            assert server["shown"] == revision
            mine = next(r for r in server["revisions"] if r["revision"] == revision)
            entry = page.locator(f"li.plan-revision[data-revision='{revision}']")
            await expect(entry.locator(".plan-revision-author")).to_contain_text(
                f"Revision {revision} by tests"
            )
            if mine["note"]:
                await expect(entry.locator(".plan-revision-note")).to_have_text(mine["note"])
            else:
                await expect(entry.locator(".plan-revision-note")).to_have_count(0)
            diff = page.locator("#review-diff")
            if revision == 1:
                await expect(page.locator("#review-mode")).to_have_count(0)
                await expect(diff).to_have_attribute("data-mode", "whole")
                assert await refs_of(sign_lines(diff, "+")) == [t["ref"] for t in server["tree"]]
            else:
                await expect(diff).to_have_attribute("data-mode", "delta")
                await expect(page.locator("#review-mode button[data-mode='delta']")).to_have_text(
                    "Changes"
                )
                for sign, way in (("+", "added"), ("-", "removed"), ("~", "changed")):
                    assert await refs_of(sign_lines(diff, sign)) == [
                        c["ref"] for c in server["changes"] if c["change"] == way
                    ], (revision, sign)
                await page.locator("#review-mode button[data-mode='whole']").click()
                await expect(diff).to_have_attribute("data-mode", "whole")
                assert await refs_of(sign_lines(diff, "+")) == [t["ref"] for t in server["tree"]]
                await page.locator("#review-mode button[data-mode='delta']").click()
                await expect(diff).to_have_attribute("data-mode", "delta")
            # The feedback given on this revision: its summary, and each comment under its line (or
            # in a list when the item is not in the revision).
            if revision < 3:
                given = [f for f in server["feedback"] if f["revision"] == revision]
                boxes = page.locator("section.change-feedback details.feedback-item")
                await expect(boxes).to_have_count(len(given))
                for one in given:
                    box = page.locator(f"details.feedback-item[data-feedback='{one['id']}']")
                    await expect(box).to_have_attribute("open", "")
                    await expect(box.locator(".feedback-summary")).to_have_text(one["summary"])
                for one in given:
                    for index, comment in enumerate(one["comments"]):
                        thread = page.locator(
                            f"div.diff-thread[data-feedback='{one['id']}'][data-comment='{index}']"
                        )
                        inside = comment["item"] in {t["ref"] for t in server["tree"]}
                        if inside and (
                            revision == 1
                            or comment["item"] in {c["ref"] for c in server["changes"]}
                        ):
                            await expect(thread.locator("blockquote")).to_have_text(
                                comment["quoted"]
                            )
                            await expect(thread.locator(".comment-body")).to_have_text(
                                comment["text"]
                            )
                # An earlier revision decides and writes nothing.
                for control in (
                    "button.verdict",
                    "button.diff-comment",
                    "form.comment-form",
                    "details.change-unsent",
                    "#review-bar",
                ):
                    await expect(page.locator(f"#review-page {control}")).to_have_count(0)
            # Checks are for the current revision of a plan that waits only.
            await expect(page.locator("section.change-checks")).to_have_count(
                1 if revision == 3 else 0
            )

        # The current revision has the bar and a button to comment on each line.
        await expect(page.locator("#review-bar button.verdict")).to_have_count(3)
        # An item is a link to its page; the back arrow goes to where the person came from.
        await select.select_option("3")
        await page.locator(f"#review-diff a.diff-item[data-ref='{refs['t4']}']").first.click()
        await on_page(page, "item")
        await page.locator("#back-to-view").click()
        await on_page(page, "review")
        await expect(select).to_have_value("3")
        assert w.problems == []


async def crumbs_of(page: Page) -> list[str]:
    return await crumbs(page)


async def test_a_plan_that_was_decided_says_who_and_why_and_one_that_is_not_there_says_so() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, _ = await drafted(agent, title="Winter plan")
        rejected = as_person(
            owner,
            project,
            "approve_plan",
            plan=number,
            approved=False,
            reason="Not this winter.\nPlease come back in spring.",
            revision=1,
        )
        assert rejected.status == 200, rejected.body
        page = w.page
        await w.open(f"#/review?change=plan:{number}")
        await on_page(page, "review")
        await expect(page.locator(".plan-state")).to_have_text("Rejected")
        await expect(page.locator(".plan-decided")).to_contain_text("Rejected by Priya Raman")
        await expect(page.locator(".plan-reason")).to_have_text(
            "Reason Not this winter.\nPlease come back in spring."
        )
        # A rejected plan is not for review: no bar, and no checks.
        await expect(page.locator("#review-bar, button.verdict")).to_have_count(0)
        await expect(page.locator("section.change-checks")).to_have_count(0)
        # Its lines are what was submitted, and nothing is held any more.
        server = await read(agent, number)
        assert server["state"] == "rejected"
        assert await refs_of(sign_lines(page.locator("#review-diff"), "+")) == [
            t["ref"] for t in server["tree"]
        ]

        # A plan that does not exist is said in one short line; so is no change at all.
        await w.go("#/review?change=plan:9999")
        await expect(page.locator("#review-missing")).to_have_text(
            "That change is not here", timeout=SLOW
        )
        await w.go("#/review")
        await expect(page.locator("#review-missing")).to_have_text("That change is not here")
        assert w.problems == []


# -- a viewer -------------------------------------------------------------------------------

WRITES = (
    "button.diff-comment",
    "form.comment-form",
    "div.diff-comment-pending",
    "details.change-unsent",
    "button.needs-changes",
    "button.needs-yes",
    "button.needs-no",
)


async def test_a_viewer_reads_the_whole_conversation_and_has_no_write_control() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    viewer = join_project(project, "viewer", "Vera Viewer")
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=None, person=viewer, name=None) as w,
    ):
        number, refs = await drafted(agent, title="Winter plan")
        asked(
            project,
            owner,
            number,
            1,
            SUMMARY,
            [
                {"item": refs["s2"], "field": "title", "text": "Name the stations."},
                {"item": refs["t3"], "text": "Drop this one."},
            ],
        )
        await a_revision(
            agent,
            number,
            refs,
            NOTE,
            s2={"title": TITLE},
            without=["t3"],
            extra=[{**T4, "size": "s"}],
        )
        waiting_number, _ = await drafted(agent, title="Spring tyre check")
        asked(project, owner, waiting_number, 1, "Smaller, please.")
        page = await open_needs(w)
        card = in_card(page, number)
        server = await read(agent, number)

        # Everything a person who can write reads: the revision, the note, what changed, what was
        # asked for before under its lines, and the plans that wait for their agent.
        await expect(card.locator(".chip.change-revision")).to_have_text("Revision 2", timeout=SLOW)
        await expect(
            card.locator("li.plan-revision[data-revision='2'] p.plan-revision-note")
        ).to_have_text(NOTE)
        assert await refs_of(sign_lines(card, "~")) == [
            c["ref"] for c in server["changes"] if c["change"] == "changed"
        ]
        await expect(card.locator("div.diff-thread")).to_have_count(2)
        await expect(card.locator("section.change-feedback .feedback-summary")).to_have_text(
            SUMMARY
        )
        await card.locator("#review-mode button[data-mode='whole']").click()
        await expect(card.locator(".change-diff")).to_have_attribute("data-mode", "whole")
        assert await refs_of(sign_lines(card, "+")) == [t["ref"] for t in server["tree"]]
        # None of the controls that write, and no button that decides.
        for control in WRITES:
            await expect(card.locator(control)).to_have_count(0)
        await expect(card.locator(DECIDING)).to_have_count(0)
        await expect(card.locator("#review-bar")).to_have_count(0)

        # The plans that wait for their agent are in a group of the list, and read the same.
        await page.locator("#review-back").click()
        await on_page(page, "needs")
        group = page.locator("details.agent-group")
        await group.locator("summary").click()
        entry = group.locator(f"li.waiting-row[data-plan='{waiting_number}']")
        await expect(entry.locator("a.plan-link")).to_have_text("Spring tyre check")
        await entry.locator("a.plan-link").click()
        await on_page(page, "review")
        await expect(page.locator(".plan-state")).to_have_text("Waiting for the agent")
        for control in WRITES:
            await expect(page.locator(f"#review-page {control}")).to_have_count(0)
        for tool, arguments in (
            ("request_plan_changes", {"summary": "No."}),
            ("approve_plan", {"approved": True}),
        ):
            refusal = as_person(viewer, project, tool, plan=number, revision=2, **arguments)
            assert refusal.status in (400, 403), (tool, refusal.body)
        after = await read(agent, number)
        assert (after["state"], after["revision"], len(after["feedback"])) == ("proposed", 2, 1)
        assert w.problems == []


# -- the history ----------------------------------------------------------------------------


async def test_the_history_says_revisions_requests_and_decisions_and_links_the_plan() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await three_revisions(agent, owner, project)
        held_number, held = await drafted(agent, title="Held plan")
        approved = as_person(owner, project, "approve_plan", plan=number, approved=True, revision=3)
        assert approved.status == 200, approved.body
        page = w.page
        await w.open("#/activity")
        await on_page(page, "activity")
        await expect(page.locator("#activity-count")).not_to_have_text(
            re.compile(r"^(Reading the changes…)?$"), timeout=SLOW
        )
        sentences = {
            "plan_changes_requested": [
                f"asked for changes to revision 1 of Plan {number}: {SUMMARY[:-1]} (2 comments).",
                f"asked for changes to revision 2 of Plan {number}: Size the new task (1 comment).",
            ],
            "plan_revised": [
                f"sent revision 2 of Plan {number} (removed 1): {NOTE}",
                f"sent revision 3 of Plan {number}: Made it medium.",
            ],
            "plan_approved": [f"approved revision 3 of Plan {number}."],
        }
        for action, wanted in sentences.items():
            rows = page.locator(f"li.event.action-{action}")
            await expect(rows).to_have_count(len(wanted))
            # Newest first.
            got = [
                (await one.locator(".event-words").text_content() or "").strip()
                for one in await rows.all()
            ]
            assert got == list(reversed(wanted)), (action, got)
        # The snapshot a revision holds is never printed, and the code of the plan is a link.
        assert "snapshot" not in (
            await page.locator("#activity-list, main#page").first.text_content() or ""
        )
        link = page.locator("li.event.action-plan_revised a.plan-link").first
        await expect(link).to_have_attribute("href", f"#/review?change=plan:{number}")
        await link.click()
        await on_page(page, "review")
        await expect(page.locator(".plan-state")).to_have_text("Approved")
        await expect(page.locator(".plan-decided")).to_contain_text("Approved by Priya Raman")

        # Held work says what holds it, and the plan's code in that card is a link to its page.
        await open_item(w, held["t1"], "board")
        chip = page.locator("#item-page .held-chip")
        await chip.hover()
        tip = page.locator(".hovercard:visible")
        await expect(tip).to_contain_text(f"Plan {held_number}")
        await tip.locator("a.plan-link").click()
        await on_page(page, "review")
        await expect(page.locator("h1#review-title")).to_have_text("Held plan")
        await expect(page.locator(".change-kind .item-code")).to_have_text(f"Plan {held_number}")
        assert w.problems == []


# -- the keyboard ---------------------------------------------------------------------------


async def test_the_conversation_can_be_had_with_the_keyboard_alone_and_no_key_decides() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await drafted(agent, title="Winter plan")
        page = await open_needs(w)
        card = in_card(page, number)
        await expect(item_lines(card)).to_have_count(len(SPEC), timeout=SLOW)
        press = page.keyboard.press

        # The comment button of a line is reached and pressed with the keyboard; the box takes the
        # focus, keys that mean something to the page are only letters in it, and Ctrl+Enter adds.
        button = card.locator(f"button.diff-comment[data-ref='{refs['t2']}']")
        await button.focus()
        await press("Enter")
        field = card.locator(f"form.comment-form[data-ref='{refs['t2']}'] textarea")
        await expect(field).to_be_focused()
        await page.keyboard.type("jk so, a and r are only letters here")
        await expect(page.locator("#review-view button[data-view='unified']")).to_have_attribute(
            "aria-pressed", "true"
        )
        await press("Control+Enter")
        await expect(pending(card, refs["t2"])).to_have_count(1)
        await expect(button).to_be_focused()
        # Escape leaves the box without keeping anything, and the focus goes back to the line.
        await press("Enter")
        await expect(field).to_be_focused()
        await press("Escape")
        await expect(card.locator("form.comment-form")).to_have_count(0)
        await expect(button).to_be_focused()
        await expect(pending(card)).to_have_count(1)

        # The keys of the page only move the focus to the buttons: none of them presses one.
        await card.locator("#review-title").focus()
        await press("a")
        await expect(card.locator("button.needs-yes")).to_be_focused()
        await press("r")
        await expect(card.locator("button.needs-no")).to_be_focused()
        await press("Tab")
        assert (await read(agent, number))["state"] == "proposed"

        # The dialog is opened and left with the keyboard. Leaving it sends nothing.
        await card.locator("button.needs-changes").focus()
        await press("Enter")
        dialog = page.locator("#changes-dialog")
        await expect(dialog.locator("#changes-summary")).to_be_focused()
        await page.keyboard.type("Smaller")
        await press("Escape")
        await expect(dialog).to_have_count(0)
        assert (await read(agent, number))["feedback"] == []
        await expect(pending(card)).to_have_count(1)

        # What changed or the whole plan: the choice is two buttons, one of them pressed.
        await a_revision(agent, number, refs, "Renamed.", s2={"title": TITLE})
        card = in_card(page, number)
        whole = card.locator("#review-mode button[data-mode='whole']")
        await expect(whole).to_have_attribute("aria-pressed", "false", timeout=SLOW)
        await whole.focus()
        await press("Enter")
        await expect(card.locator(".change-diff")).to_have_attribute("data-mode", "whole")
        await expect(card.locator("#review-mode button[data-mode='whole']")).to_have_attribute(
            "aria-pressed", "true"
        )
        await expect(card.locator("#review-mode button[data-mode='whole']")).to_be_focused()
        assert w.problems == []


# -- a narrow window ------------------------------------------------------------------------


async def test_the_conversation_and_the_plans_page_fit_390_pixels() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs = await three_revisions(agent, owner, project)
        waiting, _ = await drafted(agent, title="Spring tyre check")
        asked(project, owner, waiting, 1, "Smaller, please.")
        page = w.page
        await page.set_viewport_size(NARROW)
        await open_needs(w)
        card = in_card(page, number)
        await expect(card.locator(".chip.change-revision")).to_have_text("Revision 3", timeout=SLOW)
        # Revision 3 changed one item (the earlier comments were about revision 2 and are under it);
        # write a comment under its line, with the box open, and the dialog over it all.
        await say_comment(
            card, refs["t4"], "A long comment " + "that goes on " * 12, "Acceptance criteria"
        )
        await card.locator(f"button.diff-comment[data-ref='{refs['t4']}']").click()
        await expect(card.locator("form.comment-form")).to_be_visible()
        await no_overflow(page)
        await card.locator("#review-mode button[data-mode='whole']").click()
        await no_overflow(page)
        await card.locator("button.needs-changes").click()
        await expect(page.locator("#changes-dialog")).to_be_visible()
        await no_overflow(page)
        await page.locator("#changes-dialog").get_by_role("button", name="Cancel").click()
        # The plans that wait for their agent are in the list, and fit as well.
        await page.locator("#review-back").click()
        await on_page(page, "needs")
        await page.locator("details.agent-group summary").click()
        await expect(page.locator("details.agent-group li.waiting-row")).to_be_visible()
        await no_overflow(page)

        # The plan's page at every revision.
        await w.go(f"#/review?change=plan:{number}")
        await on_page(page, "review")
        for revision in (1, 2, 3):
            await page.locator("select#review-revision").select_option(str(revision))
            await expect(page.locator("#review-diff")).to_have_attribute(
                "data-revision", str(revision)
            )
            await no_overflow(page)
        assert w.problems == []


# -- both themes ----------------------------------------------------------------------------


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("screen", [SCREENS[0], SCREENS[2]], ids=lambda screen: screen.name)
async def test_every_part_of_the_conversation_reads_in_both_themes_at_390_and_1280(
    screen: Screen, theme: str
) -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        number, refs = await drafted(agent, title="Winter plan")
        spring, _ = await drafted(agent, title="Spring tyre check")
        asked(project, owner, spring, 1, "Smaller, please.")
        async with layout_context(project.address, screen, theme) as (w, context):
            scratch = await blank_page(context)
            page = w.page
            await page.goto(f"{project.address}#/needs")
            await expect(page.locator("#needs-body li.needs-row").first).to_be_visible(timeout=SLOW)
            await open_review(page, f"plan:{number}")
            card = in_card(page, number)
            await expect(item_lines(card)).to_have_count(len(SPEC), timeout=SLOW)
            # A comment that will not be kept, a request, a revision, and then the scene: the
            # notice of what was not kept, feedback and threads, `~` lines with their parts, a
            # comment not sent yet and a box to write one, and a plan changed outside a revision.
            await say_comment(card, refs["s2"], "Name the stations.", "Title")
            asked(
                project,
                owner,
                number,
                1,
                SUMMARY,
                [
                    {"item": refs["s2"], "field": "title", "text": "Name the stations."},
                    {"item": refs["t2"], "field": "body", "text": "Say how the bikes are fitted."},
                ],
            )
            await a_revision(
                agent,
                number,
                refs,
                NOTE,
                s2={"title": TITLE},
                t2={"body": "Fit the bikes.\nOne at a time.", "size": "l"},
            )
            card = in_card(page, number)
            await expect(card.locator("p.change-lost")).to_be_visible(timeout=SLOW)
            await say_comment(card, refs["t2"], "Why is this large?", "Size")
            await card.locator(f"button.diff-comment[data-ref='{refs['s2']}']").click()
            node = await ok(agent, "get_node", node=refs["t1"])
            await ok(
                agent,
                "update_node",
                node=refs["t1"],
                expected_version=node["version"],
                title="Choose another supplier",
            )
            await expect(card.locator("section.change-drift")).to_be_visible(timeout=SLOW)
            await card.locator("details.change-lost-list summary").click()
            for part in (
                ".chip.change-revision",
                "#review-mode",
                "p.change-lost",
                "section.change-feedback details[open]",
                ".diff-line[data-sign='~']",
                ".diff-part[data-sign='-']",
                ".diff-part[data-sign='+']",
                "div.diff-thread",
                "div.diff-comment-pending",
                "form.comment-form",
                "section.change-drift",
                ".decision-state",
                "li.plan-revision",
            ):
                await expect(card.locator(part).first).to_be_visible()
            await settle(page)
            found = await contrast(w, scratch)
            assert found.checked >= 50, found.checked
            assert found.found == [], found.lines()
            # An earlier revision, where the person's comments are threads.
            await page.goto(f"{project.address}#/review?change=plan:{number}&revision=1")
            await expect(page.locator("div.diff-thread").first).to_be_visible(timeout=SLOW)
            await settle(page)
            found = await contrast(w, scratch)
            assert found.found == [], found.lines()
            # And the plan that waits for its agent, in its group of the list and on its page.
            await page.goto(f"{project.address}#/needs")
            await page.locator("details.agent-group summary").click()
            await expect(page.locator("details.agent-group li.waiting-row")).to_be_visible()
            await settle(page)
            found = await contrast(w, scratch)
            assert found.found == [], found.lines()
            await page.goto(f"{project.address}#/review?change=plan:{spring}")
            await expect(page.locator(".plan-state")).to_be_visible(timeout=SLOW)
            await settle(page)
            found = await contrast(w, scratch)
            assert found.found == [], found.lines()
            assert w.problems == []
