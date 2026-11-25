"""M52 on the board: pressing a red, green or changed line shows the work as the screen a person
would normally use for it.

A line a plan added opens the creation layout with every value new (a green `+`); a line a revision
changed opens the edit layout with each unchanged field plain and each changed field as two rows,
the old value red (`-`) and the new one green (`+`); a line a revision removed opens the same layout
with every value red; a line of a suggested move opens the edit layout of that work with its parent
old and new; a note opens the layout of a recorded note. The dialog changes nothing, its values
equal what the tools say, and the line is a button that Enter, Space and a click all open.

Driven in a real headless browser and compared with what a real MCP client reads (`list_plans`,
`get_node`, `get_note`). Every test makes an account and a project of its own."""

import re
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Locator, Page, expect

from tests.board_helpers import SLOW, browser_page, on_page, open_review, sentences
from tests.board_layout import SCREENS, Screen, blank_page, contrast, layout_context, settle
from tests.helpers import connect, ok
from tests.test_board_needs_integration import no_overflow
from tests.test_board_plan_review_integration import a_revision, asked
from tests.test_board_review_assist_integration import a_proposal
from tests.test_board_standard_review_integration import (
    NARROW,
    card,
    change_of,
    full_world,
    open_routine,
    rows,
    the_list,
)
from tests.test_placement_integration import a_sorted_project
from tests.test_plan_review_integration import drafted, read
from tests.world import join_project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

DIALOG = "dialog#change-dialog[open]"
KIND_WORDS = {"epic": "Epic", "story": "Story", "task": "Task"}
ORDER = [
    "kind",
    "title",
    "body",
    "parent",
    "size",
    "priority",
    "start_on",
    "target_on",
    "links",
    "criteria",
]


def code(ref: str) -> str:
    kind, number = ref.split(":")
    return f"{kind[0].upper()}-{number}"


def words(text: str) -> str:
    """What a value says: one line, without the "new: " a screen reader hears first."""
    return re.sub(r"^(new|removed|was|now): ", "", " ".join(text.split()))


async def fields_of(dialog: Locator) -> list[dict[str, Any]]:
    """Every field the dialog draws, in order: its label, its state and each of its rows with the
    sign in the gutter and the words."""
    found: list[dict[str, Any]] = await dialog.evaluate(
        """(box) => [...box.querySelectorAll('.change-field')].map((field) => ({
            field: field.dataset.field,
            state: field.dataset.state,
            label: field.querySelector('.change-label').textContent,
            rows: [...field.querySelectorAll('.change-value')].map((row) => ({
                sign: row.dataset.sign ?? null,
                gutter: row.querySelector('.change-sign')?.textContent ?? null,
                text: row.querySelector('.change-body').innerText,
                theirs: [...row.querySelectorAll('[data-words="theirs"]')].length > 0,
            })),
        }))"""
    )
    for one in found:
        for row in one["rows"]:
            row["text"] = words(row["text"])
    return found


def said(found: list[dict[str, Any]], field: str) -> list[tuple[str | None, str]]:
    (one,) = [one for one in found if one["field"] == field]
    return [(row["sign"], row["text"]) for row in one["rows"]]


async def open_by(line: Locator, how: str = "click") -> Locator:
    """Press a line the way a person does and give the dialog that opens."""
    page = line.page
    if how == "click":
        await line.locator(".diff-item, .diff-field, .diff-new-epic").first.click()
    else:
        await line.focus()
        await page.keyboard.press(how)
    dialog = page.locator(DIALOG)
    await expect(dialog).to_be_visible()
    # A moved item is read when the dialog opens: its fields come a moment later.
    await expect(dialog.locator(".change-field").first).to_be_visible()
    return dialog


async def shut(dialog: Locator, line: Locator) -> None:
    """Escape closes it, and the focus is back on the line."""
    await dialog.page.keyboard.press("Escape")
    await expect(dialog.page.locator("dialog#change-dialog")).to_have_count(0)
    await expect(line).to_be_focused()


async def read_only(dialog: Locator, ref: str | None) -> None:
    """Nothing in it can change anything: no field, no button but Close, and a link to the item's
    own page."""
    assert await dialog.locator("input, select, textarea, [contenteditable]").count() == 0
    assert await dialog.locator("button").all_text_contents() == ["Close"]
    link = dialog.locator("a")
    if ref is None:
        await expect(link).to_have_count(0)
    else:
        await expect(link).to_have_text(f"Open {code(ref)}")
        await expect(link).to_have_attribute("href", re.compile(rf"item={ref}"))


# -- a plan ------------------------------------------------------------------------------------


async def a_rich_plan(agent: Client) -> tuple[int, str, str]:
    """A plan under an epic that exists, with a story that has everything an item can have, and two
    tasks, one of which needs the other first and one has an acceptance criterion."""
    epic = await ok(agent, "create_node", actor="x", kind="epic", title="Harbour renewal")
    made = await ok(
        agent,
        "create_plan",
        draft=True,
        title="Heated docks",
        items=[
            {
                "key": "s",
                "kind": "story",
                "title": "Order heated docks",
                "parent": epic["ref"],
                "size": "l",
                "priority": "high",
                "body": "Two docks at the quay.\n\nSee **the survey** first.",
                "start_on": "2026-12-01",
                "target_on": "2026-12-31",
            },
            {"key": "t", "kind": "task", "title": "Pick a supplier", "parent": "s", "size": "xs"},
            {"key": "u", "kind": "task", "title": "Place the order", "parent": "s"},
        ],
        links=[{"source": "u", "target": "t", "link_type": "needs_first"}],
        rules=[{"title": "A supplier is picked", "body": "Three quotes.", "node": "t"}],
    )
    return int(made["plan"]), epic["ref"], "Harbour renewal"


def expected_fields(item: dict[str, Any], title_of: Any) -> list[tuple[str, str]]:
    """What the creation layout draws for a plan's item, from what `list_plans` says: only the
    fields with something in them, in the order of the New item form, each as its words."""
    out = [("kind", KIND_WORDS[item["kind"]]), ("title", item["title"])]
    if item["body"].strip():
        out.append(("body", item["body"]))
    if item["parent"]:
        above = KIND_WORDS[item["parent"].split(":")[0]]
        out.append(("parent", f"{above} {code(item['parent'])} {title_of(item['parent'])}"))
    if item["size"]:
        out.append(("size", f"Size {item['size'].upper()}"))
    if item["priority"] != "normal":
        out.append(("priority", item["priority"].capitalize()))
    if item["start_on"]:
        out.append(("start_on", item["start_on"]))
    if item["target_on"]:
        out.append(("target_on", item["target_on"]))
    if item["links"]:
        out.append(
            (
                "links",
                " ".join(
                    f"{code(one['target'])} {title_of(one['target'])}" for one in item["links"]
                ),
            )
        )
    if item["criteria"]:
        out.append(
            (
                "criteria",
                " ".join(
                    f"AC-{one['id']} {one['title']}" + (f" {one['body']}" if one["body"] else "")
                    for one in item["criteria"]
                ),
            )
        )
    return out


def titles(served: dict[str, Any]) -> Any:
    known = {one["ref"]: one["title"] for one in [*served["tree"], *served["context"]]}
    return lambda ref: known.get(ref, "")


async def token_colour(page: Page, token: str) -> str:
    """The colour a token paints, as the browser writes it."""
    found: str = await page.evaluate(
        """(token) => {
            const probe = document.createElement('div');
            probe.style.background = `var(${token})`;
            document.body.append(probe);
            const colour = getComputedStyle(probe).backgroundColor;
            probe.remove();
            return colour;
        }""",
        token,
    )
    return found


async def paint(locator: Locator) -> str:
    found: str = await locator.evaluate("(el) => getComputedStyle(el).backgroundColor")
    return found


async def test_a_green_line_of_a_plan_opens_the_creation_layout_with_every_value_new() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, epic, _ = await a_rich_plan(agent)
        served = await read(agent, number)
        title_of = titles(served)
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await open_review(page, f"plan:{number}")
        review = page.locator("#review-diff")
        await expect(review.locator(".diff-item-line[data-sign='+']")).to_have_count(3)
        add_bg = await token_colour(page, "--diff-add-bg")
        add_gutter = await token_colour(page, "--diff-add-gutter")

        for item in served["tree"]:
            line = review.locator(f".diff-item-line[data-sign='+'][data-ref='{item['ref']}']")
            # The line is the control: a button that opens a dialog, with a short name, and what
            # it holds is plain text, so there is no link in it.
            await expect(line).to_have_attribute("role", "button")
            await expect(line).to_have_attribute("tabindex", "0")
            await expect(line).to_have_attribute("aria-haspopup", "dialog")
            await expect(line).to_have_attribute("aria-label", f"Show {code(item['ref'])} as new")
            assert await line.locator("a").count() == 0
            assert await line.evaluate("(el) => getComputedStyle(el).cursor") == "pointer"
            dialog = await open_by(line)
            await expect(dialog).to_have_attribute("data-mode", "added")
            await expect(dialog).to_have_attribute("data-ref", item["ref"])
            await expect(dialog.locator("h2")).to_have_text(f"New {item['kind']}")
            await expect(dialog.locator(".change-dialog-about .item-code")).to_have_text(
                code(item["ref"])
            )
            found = await fields_of(dialog)
            wanted = expected_fields(item, title_of)
            assert [one["field"] for one in found] == [name for name, _ in wanted]
            assert [one["field"] for one in found] == [n for n in ORDER if n in dict(wanted)]
            for name, text in wanted:
                rows_ = said(found, name)
                assert [sign for sign, _ in rows_] == ["+"], (item["ref"], name)
                if name == "body":
                    assert "Two docks at the quay." in rows_[0][1] and "the survey" in rows_[0][1]
                else:
                    assert rows_[0][1] == text, (item["ref"], name, rows_)
            # Every value is new: a green `+` in the gutter, and the added colours behind it.
            assert all(one["state"] == "new" for one in found)
            assert all(row["gutter"] == "+" for one in found for row in one["rows"])
            sign = dialog.locator(".change-value .change-sign").first
            body = dialog.locator(".change-value .change-body").first
            assert await paint(sign) == add_gutter and await paint(body) == add_bg
            # Somebody's words are marked as theirs, and nothing in it can be changed.
            assert any(
                row["theirs"] for one in found if one["field"] == "title" for row in one["rows"]
            )
            await read_only(dialog, item["ref"])
            await shut(dialog, line)

        # What the plan hangs under is context: not red or green, and it keeps its link.
        context = review.locator(f".diff-context[data-ref='{epic}']")
        assert await context.get_attribute("role") is None
        await expect(context.locator("a.item-link")).to_have_count(1)
        assert w.problems == []


async def test_enter_space_and_a_click_all_open_it_and_the_comment_button_does_not() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, _, _ = await a_rich_plan(agent)
        served = await read(agent, number)
        ref = served["tree"][1]["ref"]
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await open_review(page, f"plan:{number}")
        line = page.locator(f"#review-diff .diff-item-line[data-sign='+'][data-ref='{ref}']")
        for how in ("click", "Enter", "Space"):
            dialog = await open_by(line, how)
            await expect(dialog).to_have_attribute("data-ref", ref)
            await shut(dialog, line)
        # Close is a button too: it closes the dialog and gives the focus back.
        dialog = await open_by(line, "Enter")
        await dialog.locator("button#change-close").click()
        await expect(page.locator("dialog#change-dialog")).to_have_count(0)
        await expect(line).to_be_focused()
        # The comment button of the line is its own control, next to the line: it writes a comment
        # and opens nothing.
        await page.locator(f"button.diff-comment[data-ref='{ref}']").click()
        await expect(page.locator("form.comment-form")).to_be_visible()
        await expect(page.locator("dialog#change-dialog")).to_have_count(0)
        # A key that is not Enter or Space does nothing on it.
        await line.focus()
        await page.keyboard.press("a")
        await expect(page.locator("dialog#change-dialog")).to_have_count(0)
        # Open goes to the item's own page and leaves the dialog behind.
        dialog = await open_by(line, "Enter")
        await dialog.locator("a#change-open").click()
        await on_page(page, "item")
        assert w.problems == []


# -- no control inside a control ----------------------------------------------------------------

NESTED = """() => [...document.querySelectorAll('button, [role=button]')]
    .filter((outer) => outer.querySelector('button, [role=button]'))
    .map((e) => `${e.tagName} ${e.className} ${e.getAttribute('aria-label') ?? ''}`)"""


async def nested_controls(page: Page) -> list[str]:
    """Every button, or element that is one, that has another inside it."""
    found: list[str] = await page.evaluate(NESTED)
    return found


async def no_control_in_a_control(page: Page, *, lines: int, comments: bool) -> None:
    """The page as it is now has no button inside a button, and (so the check means something) the
    lines that are buttons are there and the comment buttons are there when they should be."""
    await page.wait_for_function(
        "(lines) => document.querySelectorAll('.diff-line[data-ref][role=button]').length >= lines",
        arg=lines,
        timeout=SLOW,
    )
    assert await nested_controls(page) == []
    if comments:
        assert await page.locator("button.diff-comment[data-ref]").count() >= 1
        # The comment button is next to its line, never in it.
        assert await page.locator(".diff-line button.diff-comment").count() == 0
    else:
        assert await page.locator("button.diff-comment").count() == 0


async def test_no_button_holds_a_button_on_the_list_or_the_review_page_with_comments_on() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, _, _ = await a_rich_plan(agent)
        served = await read(agent, number)
        refs = await a_sorted_project(agent)
        moved = await a_proposal(agent, [refs["loose_pay"]], refs["billing"], "Invoices.")
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await open_routine(page)
        await expect(card(page, f"plan:{number}").locator(".diff-item-line")).to_have_count(3)
        await no_control_in_a_control(page, lines=4, comments=False)
        await open_review(page, f"plan:{number}")
        await expect(page.locator("#review-diff .diff-item-line")).to_have_count(3)
        await no_control_in_a_control(page, lines=3, comments=True)
        # Each item line has one comment button, and it is the next control after the line.
        for item in served["tree"]:
            line = page.locator(f"#review-diff .diff-item-line[data-ref='{item['ref']}']")
            button = page.locator(f"#review-diff button.diff-comment[data-ref='{item['ref']}']")
            await expect(button).to_have_count(1)
            await line.focus()
            for _ in range(8):
                await page.keyboard.press("Tab")
                found: dict[str, Any] = await page.evaluate(
                    """() => ({
                        button: document.activeElement.matches('button.diff-comment'),
                        ref: document.activeElement.dataset.ref ?? null,
                    })"""
                )
                if found["button"]:
                    break
            assert found == {"button": True, "ref": item["ref"]}, found
            # It sits over the right edge of its line.
            box, shown = await button.bounding_box(), await line.bounding_box()
            assert box is not None and shown is not None
            assert box["x"] + box["width"] <= shown["x"] + shown["width"] + 0.5
            assert box["x"] >= shown["x"] + shown["width"] / 2
            assert (
                shown["y"] - 1 <= box["y"]
                and box["y"] + box["height"] <= shown["y"] + shown["height"] + 1
            )
        await page.locator("#review-view button[data-view=split]").click()
        await expect(page.locator("#review-diff .diff-side")).to_have_count(2)
        await no_control_in_a_control(page, lines=3, comments=True)
        # A comment box open under a line is no button holding a button either.
        await page.locator(
            f"button.diff-comment[data-ref='{served['tree'][0]['ref']}']"
        ).first.click()
        await expect(page.locator("form.comment-form")).to_be_visible()
        await no_control_in_a_control(page, lines=3, comments=True)
        # The moves: the list and the page are the same.
        await w.open("#/needs")
        await on_page(page, "needs")
        await open_routine(page)
        await no_control_in_a_control(page, lines=4, comments=False)
        await open_review(page, f"placement:{moved}")
        await no_control_in_a_control(page, lines=2, comments=False)
        await page.locator("#review-view button[data-view=split]").click()
        await expect(page.locator("#review-diff .diff-side")).to_have_count(2)
        await no_control_in_a_control(page, lines=2, comments=False)
        assert w.problems == []


async def test_no_button_holds_a_button_for_a_revised_plan_and_for_a_viewer() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    viewer = join_project(project, "viewer", "Vera Viewer")
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=None, person=viewer, name=None) as w,
        browser_page(project.address, token=project.key, name=None) as mine,
    ):
        number, _, _ = await a_revised_plan(project, owner, agent)
        # The person who can write: Changes, Whole plan, Split.
        page = mine.page
        await mine.open("#/needs")
        await on_page(page, "needs")
        await open_review(page, f"plan:{number}")
        review = page.locator("#review-diff")
        await expect(review).to_have_attribute("data-mode", "delta")
        await no_control_in_a_control(page, lines=4, comments=True)
        await page.locator("#review-view button[data-view=split]").click()
        await expect(review.locator(".diff-side")).to_have_count(2)
        await no_control_in_a_control(page, lines=4, comments=True)
        await page.locator("#review-mode button[data-mode=whole]").click()
        await expect(review).to_have_attribute("data-mode", "whole")
        await no_control_in_a_control(page, lines=4, comments=True)
        # The viewer reads the same lines as buttons, with no comment button at all.
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await no_control_in_a_control(page, lines=3, comments=False)
        await open_review(page, f"plan:{number}")
        await no_control_in_a_control(page, lines=4, comments=False)
        await page.locator("#review-view button[data-view=split]").click()
        await expect(page.locator("#review-diff .diff-side")).to_have_count(2)
        await no_control_in_a_control(page, lines=4, comments=False)
        await page.locator("#review-mode button[data-mode=whole]").click()
        await expect(page.locator("#review-diff")).to_have_attribute("data-mode", "whole")
        await no_control_in_a_control(page, lines=4, comments=False)
        # And with a dialog open on it.
        line = page.locator("#review-diff .diff-item-line[data-sign='+']").first
        dialog = await open_by(line, "Enter")
        assert await nested_controls(page) == []
        await shut(dialog, line)
        assert w.problems == [] and mine.problems == []


# -- a revision ------------------------------------------------------------------------------------


async def a_revised_plan(
    project: Any, owner: Any, agent: Client
) -> tuple[int, dict[str, str], dict[str, Any]]:
    """The winter plan, read by a person who asked for changes, then revised: a story's title, a
    task's details and size, one task out and one added."""
    number, refs = await drafted(agent, title="Winter plan")
    asked(project, owner, number, 1, "Please tidy it.")
    await a_revision(
        agent,
        number,
        refs,
        "Tidied.",
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
    return number, refs, await read(agent, number)


async def test_a_changed_line_shows_what_a_field_was_in_red_and_what_it_is_in_green() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs, server = await a_revised_plan(project, owner, agent)
        title_of = titles(server)
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await open_review(page, f"plan:{number}")
        review = page.locator("#review-diff")
        await expect(review).to_have_attribute("data-mode", "delta")
        remove_bg = await token_colour(page, "--diff-remove-bg")
        add_bg = await token_colour(page, "--diff-add-bg")
        changed = {c["ref"]: c for c in server["changes"] if c["change"] == "changed"}
        assert set(changed) == {refs["s2"], refs["t2"]}

        for ref, entry in changed.items():
            item = {one["ref"]: one for one in server["tree"]}[ref]
            line = review.locator(f".diff-item-line[data-sign='changed'][data-ref='{ref}']")
            await expect(line).to_have_attribute("aria-label", f"Show {code(ref)} changes")
            dialog = await open_by(line)
            await expect(dialog).to_have_attribute("data-mode", "changed")
            await expect(dialog).to_have_attribute("data-ref", ref)
            await expect(dialog.locator("h2")).to_have_text(f"Edit {item['kind']}")
            found = await fields_of(dialog)
            for one in found:
                # A field that did not change is plain: one row, no sign. One that changed is two:
                # what it was, red with a `-`, then what it is, green with a `+`.
                if one["field"] in entry["fields"]:
                    assert one["state"] == "changed"
                    assert [row["gutter"] for row in one["rows"]] == ["-", "+"]
                else:
                    assert one["state"] == "same"
                    assert [row["gutter"] for row in one["rows"]] == [None]
            wanted = dict(expected_fields(item, title_of))
            for name in entry["fields"]:
                signs = said(found, name)
                assert [sign for sign, _ in signs] == ["-", "+"]
                if name == "body":
                    assert "Fit the bikes." in signs[1][1] and "One at a time." in signs[1][1]
                    assert signs[0][1] == "none"
                else:
                    assert signs[1][1] == wanted[name], (name, signs)
            for name in (n for n in wanted if n not in entry["fields"] and n != "body"):
                assert said(found, name) == [(None, wanted[name])], name
            # The old values are what the server says they were.
            if "title" in entry["fields"]:
                assert said(found, "title")[0][1] == entry["before"]["title"]
            if "size" in entry["fields"]:
                assert said(found, "size")[0][1] == f"Size {entry['before']['size'].upper()}"
                assert said(found, "size")[1][1] == f"Size {item['size'].upper()}"
            # Red and green, with the sign as well.
            old = dialog.locator(".change-value[data-sign='-'] .change-body").first
            new = dialog.locator(".change-value[data-sign='+'] .change-body").first
            assert await paint(old) == remove_bg and await paint(new) == add_bg
            await read_only(dialog, ref)
            await shut(dialog, line)
            # Each line of a changed part opens the very same dialog.
            part = entry["fields"][0]
            for sign in ("-", "+"):
                part_line = review.locator(
                    f".diff-line.diff-part[data-ref='{ref}'][data-part='{part}'][data-sign='{sign}']"
                )
                await expect(part_line).to_have_attribute("role", "button")
                dialog = await open_by(part_line)
                await expect(dialog).to_have_attribute("data-mode", "changed")
                await expect(dialog).to_have_attribute("data-ref", ref)
                assert [one["field"] for one in await fields_of(dialog)] == [
                    one["field"] for one in found
                ]
                await shut(dialog, part_line)
        assert w.problems == []


async def test_a_removed_line_shows_every_value_red_and_an_added_one_every_value_green() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, refs, server = await a_revised_plan(project, owner, agent)
        before = await read(agent, number, revision=1)
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await open_review(page, f"plan:{number}")
        review = page.locator("#review-diff")
        remove_bg = await token_colour(page, "--diff-remove-bg")

        gone = {one["ref"]: one for one in before["tree"]}[refs["t3"]]
        line = review.locator(f".diff-item-line[data-sign='-'][data-ref='{refs['t3']}']")
        await expect(line).to_have_attribute("aria-label", f"Show {code(refs['t3'])} as removed")
        dialog = await open_by(line)
        await expect(dialog).to_have_attribute("data-mode", "removed")
        await expect(dialog.locator("h2")).to_have_text("Removed task")
        found = await fields_of(dialog)
        assert all(one["state"] == "removed" for one in found)
        assert all(row["gutter"] == "-" for one in found for row in one["rows"])
        wanted = expected_fields(gone, titles(before))
        assert [(one["field"], one["rows"][0]["text"]) for one in found] == wanted
        body = dialog.locator(".change-value .change-body").first
        assert await paint(body) == remove_bg
        await read_only(dialog, refs["t3"])
        await shut(dialog, line)

        new = [c for c in server["changes"] if c["change"] == "added"]
        assert len(new) == 1
        added = {one["ref"]: one for one in server["tree"]}[new[0]["ref"]]
        line = review.locator(f".diff-item-line[data-sign='+'][data-ref='{added['ref']}']")
        dialog = await open_by(line)
        await expect(dialog).to_have_attribute("data-mode", "added")
        found = await fields_of(dialog)
        assert [
            (one["field"], one["rows"][0]["text"]) for one in found if one["field"] != "body"
        ] == [pair for pair in expected_fields(added, titles(server)) if pair[0] != "body"]
        await shut(dialog, line)
        # An item that did not change has no line to press: the plan page folds it.
        assert await review.locator(f".diff-item-line[data-ref='{refs['t1']}']").count() == 0
        assert w.problems == []


# -- a suggested move ----------------------------------------------------------------------------


async def test_a_line_of_a_suggested_move_shows_the_work_with_its_old_and_new_parent() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        refs = await a_sorted_project(agent)
        number = await a_proposal(agent, [refs["loose_pay"]], refs["billing"], "Invoices.")
        move = (await ok(agent, "list_placements", status="proposed"))["items"][0]
        (one,) = move["items"]
        node = await ok(agent, "get_node", node=one["ref"])
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        change = f"placement:{number}"
        await open_routine(page)
        # On the card, too, a line of the preview opens it.
        preview = card(page, change).locator(".diff-item-line[data-sign='+']")
        await expect(preview).to_have_attribute("role", "button")
        dialog = await open_by(preview)
        await expect(dialog).to_have_attribute("data-mode", "moved")
        await shut(dialog, preview)
        await open_review(page, change)
        review = page.locator("#review-diff")
        remove_bg = await token_colour(page, "--diff-remove-bg")
        add_bg = await token_colour(page, "--diff-add-bg")
        for sign in ("+", "-"):
            line = review.locator(f".diff-item-line[data-sign='{sign}'][data-ref='{one['ref']}']")
            await expect(line).to_have_attribute("aria-label", f"Show {code(one['ref'])} move")
            assert await line.locator("a").count() == 0
            dialog = await open_by(line)
            await expect(dialog).to_have_attribute("data-mode", "moved")
            await expect(dialog).to_have_attribute("data-ref", one["ref"])
            await expect(dialog.locator("h2")).to_have_text("Move story")
            found = await fields_of(dialog)
            parent = said(found, "parent")
            assert [sign_ for sign_, _ in parent] == ["-", "+"]
            old_parent = one["from_parent"]
            assert parent[0][1] == f"Epic {code(old_parent)} Inbox"
            assert parent[1][1] == f"Epic {code(move['parent'])} Billing"
            # Everything else is the work as it is, plain.
            assert said(found, "title") == [(None, node["title"])]
            assert said(found, "kind") == [(None, "Story")]
            assert [one["field"] for one in found if one["state"] == "changed"] == ["parent"]
            old = dialog.locator(".change-value[data-sign='-'] .change-body")
            new = dialog.locator(".change-value[data-sign='+'] .change-body")
            assert await paint(old) == remove_bg and await paint(new) == add_bg
            await read_only(dialog, one["ref"])
            await shut(dialog, line)
        assert w.problems == []


async def test_a_move_into_a_new_epic_shows_the_new_epic_by_its_title_and_the_epic_itself() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        refs = await a_sorted_project(agent)
        made = await ok(
            agent,
            "propose_placement",
            items=[refs["loose_pay"], refs["loose_mail"]],
            new_epic={"title": "Money and messages", "body": "Everything that is sent or paid."},
            reason="Two themes.",
        )
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await open_review(page, f"placement:{made['id']}")
        review = page.locator("#review-diff")
        epic_line = review.locator(".diff-item-line[data-new-epic='true'][data-sign='+']")
        await expect(epic_line).to_have_attribute("aria-label", "Show the new epic")
        dialog = await open_by(epic_line)
        await expect(dialog).to_have_attribute("data-mode", "added")
        await expect(dialog.locator("h2")).to_have_text("New epic")
        found = await fields_of(dialog)
        assert said(found, "title") == [("+", "Money and messages")]
        assert said(found, "kind") == [("+", "Epic")]
        assert said(found, "body") == [("+", "Everything that is sent or paid.")]
        # It has no number yet, so it has no page and no link.
        await read_only(dialog, None)
        await shut(dialog, epic_line)
        line = review.locator(f".diff-item-line[data-sign='+'][data-ref='{refs['loose_pay']}']")
        dialog = await open_by(line)
        found = await fields_of(dialog)
        parent = said(found, "parent")
        assert parent[1] == ("+", "Epic Money and messages")
        assert parent[0][1].endswith("Inbox")
        await shut(dialog, line)
        assert w.problems == []


# -- a note --------------------------------------------------------------------------------------


async def test_a_note_line_on_a_card_opens_the_note_as_a_record_with_every_value_new() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        world = await full_world(agent, project, owner)
        served = await rows(agent)
        (row,) = [r for r in served if r["kind"] == "note"]
        page = w.page
        await the_list(page, w, agent)
        await open_routine(page)
        notes = card(page, change_of(row)).locator("p.preview-note-line")
        assert await notes.count() >= 1
        line = notes.first
        number = int(str(await line.evaluate("(el) => el.parentElement.dataset.note")))
        note = await ok(agent, "get_note", note_id=number)
        await expect(line).to_have_attribute("role", "button")
        await expect(line).to_have_attribute("aria-label", f"Show N-{number} as new")
        assert await line.locator("a").count() == 0
        dialog = await open_by(line, "Enter")
        await expect(dialog).to_have_attribute("data-mode", "added")
        await expect(dialog).to_have_attribute("data-ref", f"note:{number}")
        await expect(dialog.locator("h2")).to_have_text("New note")
        found = await fields_of(dialog)
        assert said(found, "text") == [("+", words(note["text"]))]
        assert said(found, "why") == [("+", words(note["why"]))]
        if note["decided_by"]:
            assert said(found, "decided")[0][1].startswith(note["decided_by"])
        concerns = said(found, "concerns")[0][1]
        for ref in note["nodes"]:
            assert code(ref) in concerns
        assert all(one["state"] == "new" for one in found)
        await read_only(dialog, f"note:{number}")
        assert (await dialog.locator("a").get_attribute("href") or "").endswith(
            f"note:{number}&from=needs"
        )
        await shut(dialog, line)
        assert world.plan > 0 and w.problems == []


# -- on the card ---------------------------------------------------------------------------------


async def test_the_lines_of_a_cards_preview_open_the_same_dialog_and_a_title_still_opens_it() -> (
    None
):
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, _, _ = await a_rich_plan(agent)
        served = await read(agent, number)
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        plan = card(page, f"plan:{number}")
        lines = plan.locator(".diff-item-line[data-sign='+']")
        await expect(lines).to_have_count(3, timeout=SLOW)
        for item in served["tree"]:
            line = plan.locator(f".diff-item-line[data-ref='{item['ref']}']")
            await expect(line).to_have_attribute("aria-label", f"Show {code(item['ref'])} as new")
            dialog = await open_by(line, "Enter")
            await expect(dialog).to_have_attribute("data-ref", item["ref"])
            found = await fields_of(dialog)
            assert [one["field"] for one in found] == [
                n for n, _ in expected_fields(item, titles(served))
            ]
            await shut(dialog, line)
        # The card's own title is still the link to its review page.
        await plan.locator("a.change-title").click()
        await on_page(page, "review")
        assert w.problems == []


async def test_a_plan_with_many_items_opens_every_line() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic = await ok(agent, "create_node", actor="x", kind="epic", title="Big")
        items: list[dict[str, Any]] = [
            {"key": "a", "kind": "story", "title": "First story", "parent": epic["ref"]},
            {"key": "b", "kind": "story", "title": "Second story", "parent": epic["ref"]},
        ]
        for at in range(1, 51):
            items.append(
                {
                    "key": f"t{at}",
                    "kind": "task",
                    "title": f"Task number {at}",
                    "parent": "a" if at % 2 else "b",
                }
            )
        made = await ok(agent, "create_plan", draft=True, title="A big plan", items=items)
        number = int(made["plan"])
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await open_review(page, f"plan:{number}")
        review = page.locator("#review-diff")
        while await review.locator("button.diff-fold").count():
            await review.locator("button.diff-fold").first.click()
        lines = review.locator(".diff-item-line[data-sign='+']:visible")
        await expect(lines).to_have_count(52)
        refs = await lines.evaluate_all("els => els.map((e) => e.dataset.ref)")
        assert len(set(refs)) == 52
        for ref in refs:
            line = review.locator(f".diff-item-line[data-sign='+'][data-ref='{ref}']")
            await line.scroll_into_view_if_needed()
            dialog = await open_by(line, "Enter")
            await expect(dialog).to_have_attribute("data-ref", ref)
            await shut(dialog, line)
        assert w.problems == []


# -- who can open it, and how it reads -------------------------------------------------------------


async def test_a_viewer_opens_the_same_dialog_and_it_has_nothing_that_writes() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    viewer = join_project(project, "viewer", "Vera Viewer")
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=None, person=viewer, name=None) as w,
    ):
        number, _, _ = await a_rich_plan(agent)
        served = await read(agent, number)
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await open_review(page, f"plan:{number}")
        review = page.locator("#review-diff")
        await expect(review.locator("button.diff-comment")).to_have_count(0)
        item = served["tree"][0]
        line = review.locator(f".diff-item-line[data-sign='+'][data-ref='{item['ref']}']")
        dialog = await open_by(line, "Space")
        found = await fields_of(dialog)
        assert [(one["field"]) for one in found] == [
            n for n, _ in expected_fields(item, titles(served))
        ]
        await read_only(dialog, item["ref"])
        await shut(dialog, line)
        # Somebody else's feedback on a revised plan has their name in it, which is theirs: a
        # viewer reads the page and it has no sentence of the board's own.
        revised, _, _ = await a_revised_plan(project, owner, agent)
        await w.open("#/needs")
        await on_page(page, "needs")
        await open_review(page, f"plan:{revised}")
        await expect(page.locator("section.change-feedback .feedback-title")).to_have_text(
            "Priya Raman’s feedback on revision 1"
        )
        assert await sentences(page) == []
        assert w.problems == []


async def test_no_dialog_of_a_line_explains_itself_and_a_long_title_is_theirs() -> None:
    owner = register()
    project = make_project(owner)
    long_title = "Order the heated docks for the northern quay before the first frost arrives"
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic = await ok(agent, "create_node", actor="x", kind="epic", title="Harbour renewal")
        made = await ok(
            agent,
            "create_plan",
            draft=True,
            title="Long words",
            items=[
                {
                    "key": "s",
                    "kind": "story",
                    "title": long_title,
                    "parent": epic["ref"],
                    "size": "m",
                    "body": "A long sentence about the quay that says more than six words.",
                }
            ],
            rules=[
                {
                    "title": "Every dock keeps its water from freezing over in winter",
                    "body": "Checked by the harbour master on the first cold night.",
                    "node": "s",
                }
            ],
        )
        number = int(made["plan"])
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await open_review(page, f"plan:{number}")
        ref = (await read(agent, number))["tree"][0]["ref"]
        line = page.locator(f"#review-diff .diff-item-line[data-sign='+'][data-ref='{ref}']")
        dialog = await open_by(line)
        await expect(dialog.locator("[data-field=title]")).to_contain_text(long_title)
        assert await sentences(page, [DIALOG, "#review-page"]) == []
        for heading in await dialog.locator("h2").all_text_contents():
            assert len(heading.split()) <= 3
        await shut(dialog, line)
        assert w.problems == []


async def test_the_dialog_fits_a_phone_scrolls_and_has_no_sideways_scroll() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        number, _, _ = await a_rich_plan(agent)
        served = await read(agent, number)
        page = w.page
        await page.set_viewport_size(NARROW)
        await w.open("#/needs")
        await on_page(page, "needs")
        await open_review(page, f"plan:{number}")
        for item in served["tree"]:
            line = page.locator(
                f"#review-diff .diff-item-line[data-sign='+'][data-ref='{item['ref']}']"
            )
            dialog = await open_by(line, "Enter")
            box = await dialog.bounding_box()
            assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= 390 + 0.5
            sideways = await dialog.evaluate("(el) => el.scrollWidth - el.clientWidth")
            assert sideways <= 1, sideways
            # What does not fit the height scrolls inside the dialog, and Close is reachable.
            await dialog.locator("button#change-close").scroll_into_view_if_needed()
            await expect(dialog.locator("button#change-close")).to_be_in_viewport()
            await shut(dialog, line)
        await no_overflow(page)
        assert w.problems == []


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("screen", [SCREENS[0], SCREENS[2]], ids=lambda screen: screen.name)
async def test_every_dialog_of_a_line_reads_in_both_themes_and_never_by_colour_alone(
    screen: Screen, theme: str
) -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        number, refs, server = await a_revised_plan(project, owner, agent)
        rich, _, _ = await a_rich_plan(agent)
        async with layout_context(project.address, screen, theme) as (w, context):
            scratch = await blank_page(context)
            page = w.page
            await page.goto(f"{project.address}#/needs")
            await expect(page.locator("#needs-body li.needs-row").first).to_be_visible(timeout=SLOW)
            checked = 0
            added = next(c["ref"] for c in server["changes"] if c["change"] == "added")
            for plan, kind, ref in (
                (number, "changed", refs["s2"]),
                (number, "-", refs["t3"]),
                (number, "+", added),
                (rich, "+", "task:"),
            ):
                await page.goto(f"{project.address}#/needs")
                await expect(page.locator("#needs-body li.needs-row").first).to_be_visible(
                    timeout=SLOW
                )
                await open_review(page, f"plan:{plan}")
                lines = page.locator(f"#review-diff .diff-item-line[data-sign='{kind}']")
                line = lines.filter(has=page.locator(f"[data-ref^='{ref}']")).first
                if await line.count() == 0:
                    line = lines.first
                dialog = await open_by(line, "Enter")
                await settle(page)
                found = await contrast(w, scratch)
                assert found.found == [], found.lines()
                assert found.checked >= 5, found.checked
                checked += 1
                await shut(dialog, line)
            assert checked == 4 and w.problems == []


async def test_the_second_item_of_a_proposal_set_opens_too() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        refs = await a_sorted_project(agent)
        number = await a_proposal(
            agent, [refs["loose_pay"], refs["loose_mail"]], refs["billing"], "Money and mail."
        )
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await open_review(page, f"placement:{number}")
        review = page.locator("#review-diff")
        await page.locator("#review-view button[data-view=split]").click()
        await expect(review.locator(".diff-side")).to_have_count(2)
        for ref in (refs["loose_pay"], refs["loose_mail"]):
            for side, sign in (("old", "-"), ("new", "+")):
                line = review.locator(
                    f".diff-side[data-side='{side}'] "
                    f".diff-item-line[data-sign='{sign}'][data-ref='{ref}']"
                )
                dialog = await open_by(line, "Enter")
                await expect(dialog).to_have_attribute("data-ref", ref)
                await expect(dialog).to_have_attribute("data-mode", "moved")
                await shut(dialog, line)
        assert w.problems == []
