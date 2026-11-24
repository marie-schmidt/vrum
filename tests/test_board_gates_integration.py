"""M47 on the board: gates, held work, plans that wait for approval and the history's sentences.

Driven in a real headless browser and compared with what a real MCP client reads (see ./check.sh).
A gate holds a story or task until a time comes or a person approves; the item's page has a Gates
section (the server's own words for each gate, Add a gate and Lift for people who can write), the
strip and the cards say that work is held with one small clock whose hover card names each hold,
and a refused approval is a row on the Needs a person page. A plan drafted by an agent is ONE row
there, with Approve and Reject (a reason is needed): approving frees all its work, rejecting drops
it with the reason on each item. A viewer sees all of it and no control, and the server refuses a
viewer. The history and the Activity feed say each of these in plain words. Every test makes an
account and a project of its own."""

import datetime
import re
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Page, ViewportSize, expect

from tests.board_helpers import (
    SLOW,
    browser_page,
    card,
    nothing_open,
    on_page,
    open_item,
    part,
    plain,
    strip_labels,
)
from tests.helpers import connect, make_tree, ok, refused
from tests.test_board_needs_integration import DECIDING, EMPTY, no_overflow, row, sees_the_list
from tests.test_checks_integration import as_person, words_of
from tests.world import HARNESS_AGENT, Person, Project, join_project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NARROW: ViewportSize = {"width": 390, "height": 900}
PLAN = [
    {"key": "e", "kind": "epic", "title": "Launch the checkout"},
    {"key": "s", "kind": "story", "title": "Pay by card", "parent": "e", "size": "m"},
    {"key": "t1", "kind": "task", "title": "Build the form", "parent": "s", "size": "s"},
    {"key": "t2", "kind": "task", "title": "Call the provider", "parent": "s", "size": "s"},
    {"key": "t3", "kind": "task", "title": "Test with a card", "parent": "s", "size": "s"},
    {"key": "t4", "kind": "task", "title": "Write the receipt", "parent": "s", "size": "s"},
    {"key": "t5", "kind": "task", "title": "Ship it", "parent": "s", "size": "s"},
]


def in_days(days: int) -> str:
    """A day a few days ahead, as the dialog's field takes it."""
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def person_call(project: Project, who: Person, tool: str, **arguments: Any) -> Any:
    return as_person(who, project, tool, **arguments)


async def gates_of(agent: Client, ref: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = (await ok(agent, "get_node", node=ref))["gates"]
    return found


async def add_in_dialog(page: Page, *, kind: str, **fields: str) -> Any:
    """Open the Add a gate dialog on the item's page, choose what it waits for, fill the fields
    and send; returns the dialog (still open when the server refused)."""
    await part(page, "gates").locator("#add-gate").click()
    dialog = page.locator("dialog#add-gate-dialog[open]")
    await expect(dialog).to_be_visible()
    await dialog.locator("#gate-kind").select_option(kind)
    for name, value in fields.items():
        await dialog.locator(f"#gate-{name}").fill(value)
    await dialog.locator("button[type=submit]").click()
    return dialog


# -- gates ----------------------------------------------


async def test_a_gate_is_added_from_a_dialog_and_held_work_is_drawn_held_everywhere() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic, story, task = await make_tree(agent)
        page = w.page
        await open_item(w, task["ref"], "board")
        gates = part(page, "gates")

        # With no gate there is only the small button: no entry, no count, no chip.
        await expect(gates).to_be_visible(timeout=SLOW)
        await expect(gates.locator("#add-gate")).to_have_text("Add a gate…")
        await expect(gates.locator(".gate-item")).to_have_count(0)
        await expect(gates.locator(".heading-count")).to_have_count(0)
        await expect(page.locator("#item-page .held-chip")).to_have_count(0)
        assert "On hold" not in await strip_labels(page)
        # An epic is held by nothing, and has no way to add a gate.
        await open_item(w, epic["ref"], "board")
        await expect(part(page, "gates")).to_have_count(0)
        await open_item(w, task["ref"], "board")

        # The dialog opens empty with nothing chosen, and names what is missing before the server
        # is asked.
        await gates.locator("#add-gate").click()
        dialog = page.locator("dialog#add-gate-dialog[open]")
        await expect(dialog).to_be_visible()
        await nothing_open(page)
        await expect(dialog.locator("#gate-kind")).to_have_value("")
        await expect(dialog.locator("#gate-timer-fields")).to_be_hidden()
        await expect(dialog.locator("#gate-approval-fields")).to_be_hidden()
        await dialog.locator("button[type=submit]").click()
        await expect(dialog.locator(".problem")).to_have_text("Choose what it waits for.")

        # A timer: the calendar field and an optional time; the server's refusal of a past day is
        # shown in the dialog in its words, and nothing is held.
        await dialog.locator("#gate-kind").select_option("timer")
        await expect(dialog.locator("#gate-timer-fields")).to_be_visible()
        await expect(dialog.locator("#gate-day")).to_have_value("")
        await expect(dialog.locator("#gate-time")).to_have_value("")
        await expect(dialog.locator("#gate-timer-fields button.dp-open")).to_be_visible()
        await dialog.locator("#gate-day").fill("2001-01-01")
        await dialog.locator("button[type=submit]").click()
        problem = dialog.locator(".problem")
        await expect(problem).to_contain_text("already passed", timeout=SLOW)
        said = words_of(
            await refused(
                agent, "add_gate", node=task["ref"], kind="timer", not_before="2001-01-01"
            )
        )
        plain((await problem.text_content()) or "", said)
        assert await gates_of(agent, task["ref"]) == []
        # A time with no day is the server's to refuse too.
        await dialog.locator("#gate-day").fill("")
        await dialog.locator("#gate-time").fill("09:30")
        await dialog.locator("button[type=submit]").click()
        await expect(problem).to_contain_text("YYYY-MM-DD", timeout=SLOW)
        await dialog.locator("#gate-time").fill("")

        # A day ahead holds it.
        await dialog.locator("#gate-day").fill(in_days(30))
        await dialog.locator("button[type=submit]").click()
        await expect(page.locator("dialog#add-gate-dialog")).to_have_count(0, timeout=SLOW)
        (gate,) = await gates_of(agent, task["ref"])
        assert gate["kind"] == "timer" and gate["state"] == "waiting"
        assert gate["added_by"] == owner.name and gate["added_by_kind"] == "person"
        held = await ok(agent, "get_node", node=task["ref"])
        assert held["held_by"] == [f"gate:{gate['id']}"] and held["status"] == "new"

        # The section: its count, the gate's number and the server's words, who added it and when,
        # and Lift.
        item = gates.locator(f".gate-item[data-gate='{gate['id']}']")
        await expect(item).to_be_visible(timeout=SLOW)
        await expect(gates.locator("h3 .heading-count")).to_have_text("1")
        await expect(item.locator(".gate-code")).to_have_text(f"Gate {gate['id']}")
        await expect(item.locator(".gate-words")).to_have_text(gate["words"])
        assert gate["words"].startswith("Waits until ")
        await expect(item).to_have_attribute("data-state", "waiting")
        await expect(item.locator(".gate-by .actor-person")).to_contain_text(owner.name)
        await expect(item.locator("button.lift-gate")).to_have_text("Lift…")
        # The strip says it with one small icon-only clock, and its hover card names the hold.
        chip = page.locator("#item-page .state-strip .held-chip")
        await expect(chip).to_have_count(1)
        await expect(chip).to_have_attribute("aria-label", f"Held: Gate {gate['id']}")
        assert await chip.text_content() == ""
        await chip.hover()
        tip = page.locator(".hovercard:visible")
        await expect(tip).to_contain_text(f"Gate {gate['id']}")
        await expect(tip).to_contain_text(gate["words"], timeout=SLOW)
        await page.keyboard.press("Escape")

        # On the Board the card carries the same clock, and the same card.
        await w.go("#/board")
        await on_page(page, "board")
        mark = card(page, task["ref"]).locator(".card-waiting")
        await expect(mark).to_have_attribute("aria-label", f"Held: Gate {gate['id']}", timeout=SLOW)
        await mark.hover()
        await expect(page.locator(".hovercard:visible")).to_contain_text(
            gate["words"], timeout=SLOW
        )
        await page.keyboard.press("Escape")
        assert w.problems == []


async def test_lifting_a_gate_is_one_final_decision_with_an_optional_reason() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, _, task = await make_tree(agent)
        timer = await ok(agent, "add_gate", node=task["ref"], kind="timer", not_before=in_days(40))
        page = w.page
        await open_item(w, task["ref"], "board")
        item = part(page, "gates").locator(f".gate-item[data-gate='{timer['id']}']")
        # Lift is reached and pressed with the keyboard alone.
        await item.locator("button.lift-gate").focus()
        await page.keyboard.press("Enter")
        dialog = page.locator("dialog#lift-gate[open]")
        await expect(dialog).to_be_visible()
        await nothing_open(page)
        await expect(dialog.locator("h2")).to_have_text(f"Lift Gate {timer['id']}")
        await expect(dialog.locator("#lift-reason")).to_have_value("")
        await expect(dialog.locator(".lift-words")).to_have_count(0)
        await dialog.locator("#lift-reason").fill("The date moved")
        await dialog.locator("button[type=submit]").click()
        await expect(page.locator("dialog#lift-gate")).to_have_count(0, timeout=SLOW)

        (lifted,) = await gates_of(agent, task["ref"])
        assert lifted["state"] == "removed" and lifted["removed_by"] == owner.name
        assert lifted["removed_reason"] == "The date moved"
        assert (await ok(agent, "get_node", node=task["ref"]))["held_by"] == []
        # The gate stays in the list as what it was, in the server's words, with no control, and
        # the clock is gone.
        await expect(item).to_have_attribute("data-state", "removed", timeout=SLOW)
        await expect(item.locator(".gate-words")).to_have_text(lifted["words"])
        assert lifted["words"] == f"Lifted by {owner.name}: The date moved"
        await expect(item.locator("button")).to_have_count(0)
        await expect(page.locator("#item-page .held-chip")).to_have_count(0)
        # An agent cannot lift one, and a second lift is the server's to refuse.
        assert "not allowed to use" in words_of(
            await refused(agent, "remove_gate", gate=timer["id"])
        )
        again = person_call(project, owner, "remove_gate", gate=timer["id"])
        assert again.status == 400 and "already lifted" in again.body["message"]
        assert w.problems == []


async def test_an_approval_gate_opens_on_a_yes_and_a_refusal_is_lifted_on_the_needs_page() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, story, task = await make_tree(agent)
        other = await ok(
            agent, "create_node", actor="x", kind="task", title="Second task", parent=story["ref"]
        )
        page = w.page
        await open_item(w, task["ref"], "board")
        dialog = await add_in_dialog(page, kind="approval", question="May we touch billing?")
        await expect(page.locator("dialog#add-gate-dialog")).to_have_count(0, timeout=SLOW)
        (gate,) = await gates_of(agent, task["ref"])
        assert gate["kind"] == "approval" and gate["state"] == "waiting" and gate["ask"]
        assert gate["words"] == "Waiting for a person to approve: May we touch billing?"
        item = part(page, "gates").locator(f".gate-item[data-gate='{gate['id']}']")
        await expect(item.locator(".gate-words")).to_have_text(gate["words"], timeout=SLOW)
        await expect(item.locator(".gate-by")).to_contain_text(f"Asked as Q-{gate['ask']}")
        # An approval with no question is refused by the server, in the dialog.
        await expect(dialog).to_be_hidden()
        await part(page, "gates").locator("#add-gate").click()
        second = page.locator("dialog#add-gate-dialog[open]")
        await second.locator("#gate-kind").select_option("approval")
        await expect(second.locator("#gate-approval-fields")).to_be_visible()
        await expect(second.locator("#gate-question")).to_have_value("")
        await second.locator("button[type=submit]").click()
        await expect(second.locator(".problem")).to_contain_text("needs question", timeout=SLOW)
        await second.locator("button", has_text="Cancel").click()

        # It is a question on the Needs a person page, answered there; yes opens the gate and
        # frees the work.
        await w.go("#/needs")
        await on_page(page, "needs")
        await sees_the_list(page, agent)
        asked = row(page, "ask", task["ref"])
        await expect(asked.locator(".change-kind-word")).to_have_text("Approval")
        await expect(asked.locator(".preview-question")).to_have_text("May we touch billing?")
        await expect(asked.locator("button.verdict")).to_have_text(["Approve", "Reject…"])
        await asked.locator("button.verdict[data-answer='yes']").click()
        await expect(asked).to_have_count(0, timeout=SLOW)
        opened = (await gates_of(agent, task["ref"]))[0]
        assert opened["state"] == "open" and opened["answered_by"] == owner.name
        assert (await ok(agent, "get_node", node=task["ref"]))["held_by"] == []

        # A second gate that a person refuses stays in the way and is a row of its own: the item
        # as a link, the gate's words and a button that lifts it.
        refusing = await ok(
            agent, "add_gate", node=other["ref"], kind="approval", question="Ship on Friday?"
        )
        await expect(row(page, "ask", other["ref"])).to_be_visible(timeout=SLOW)
        await row(page, "ask", other["ref"]).locator("button.verdict[data-verb=reject]").click()
        await page.locator("dialog#reject-dialog textarea").fill("Not on a Friday")
        await page.locator("dialog#reject-dialog button[type=submit]").click()
        refused_row = row(page, "gate", other["ref"])
        await expect(refused_row).to_be_visible(timeout=SLOW)
        await sees_the_list(page, agent)
        await expect(refused_row.locator(".change-kind-word")).to_have_text("Refused approval")
        await expect(refused_row.locator("a.change-title")).to_have_text("Second task")
        stuck = (await gates_of(agent, other["ref"]))[0]
        assert stuck["state"] == "refused" and stuck["id"] == refusing["id"]
        await expect(refused_row.locator(".change-kind .item-code")).to_have_text(
            f"Gate {stuck['id']}"
        )
        await expect(refused_row.locator(".needs-gate-words")).to_contain_text(
            stuck["words"], timeout=SLOW
        )
        # The gate is the line that lifting it takes away, under the item it holds.
        await expect(
            refused_row.locator(".diff-line[data-sign='-'] .needs-gate-words")
        ).to_have_count(1)
        await expect(refused_row.locator(".change-consequence")).to_have_count(0)
        await refused_row.locator("button.needs-lift").focus()
        await expect(page.locator(".hovercard:visible")).to_contain_text("Frees the work again")
        await refused_row.locator("button.needs-lift").click()
        lift = page.locator("dialog#lift-gate[open]")
        await expect(lift).to_be_visible()
        await expect(lift.locator("#lift-reason")).to_have_value("")
        await lift.locator("#lift-reason").fill("Asked again")
        await lift.locator("button[type=submit]").click()
        await expect(page.locator("dialog#lift-gate")).to_have_count(0, timeout=SLOW)
        await expect(refused_row).to_have_count(0, timeout=SLOW)
        assert (await ok(agent, "get_node", node=other["ref"]))["held_by"] == []
        assert (await gates_of(agent, other["ref"]))[0]["removed_reason"] == "Asked again"
        await expect(page.locator("#needs-body")).to_have_text(EMPTY, timeout=SLOW)
        assert w.problems == []


async def test_a_viewer_sees_gates_and_held_work_and_no_control_and_the_server_refuses_them() -> (
    None
):
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer", "Vera Viewer")
    async with connect(project.mcp_url, project.key) as agent:
        _, story, task = await make_tree(agent)
        quiet = await ok(
            agent, "create_node", actor="x", kind="task", title="No gate", parent=story["ref"]
        )
        gate = await ok(agent, "add_gate", node=task["ref"], kind="timer", not_before=in_days(30))
        async with browser_page(project.address, token=None, person=viewer, name=None) as w:
            page = w.page
            await open_item(w, task["ref"], "board")
            item = part(page, "gates").locator(f".gate-item[data-gate='{gate['id']}']")
            await expect(item.locator(".gate-words")).to_have_text(gate["words"], timeout=SLOW)
            await expect(part(page, "gates").locator("button")).to_have_count(0)
            await expect(page.locator("#item-page .held-chip")).to_have_count(1)
            # With no gate the section is not there at all for a viewer.
            await open_item(w, quiet["ref"], "board")
            await expect(part(page, "gates")).to_have_count(0)
            assert w.problems == []
        # Whatever a page offers, the server refuses a viewer.
        for call in (
            person_call(project, viewer, "remove_gate", gate=gate["id"]),
            person_call(
                project, viewer, "add_gate", node=quiet["ref"], kind="timer", not_before=in_days(9)
            ),
        ):
            assert call.status in (400, 403), call.body
        assert (await gates_of(agent, task["ref"]))[0]["state"] == "waiting"
        assert (await ok(agent, "get_node", node=quiet["ref"]))["held_by"] == []


async def test_a_timer_gate_that_comes_due_opens_and_the_open_page_follows() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, _, task = await make_tree(agent)
        due = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=10)
        gate = await ok(
            agent,
            "add_gate",
            node=task["ref"],
            kind="timer",
            not_before=due.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        page = w.page
        await open_item(w, task["ref"], "board")
        item = part(page, "gates").locator(f".gate-item[data-gate='{gate['id']}']")
        await expect(item).to_have_attribute("data-state", "waiting", timeout=SLOW)
        await expect(page.locator("#item-page .held-chip")).to_have_count(1)
        # Nothing says it opened: the page reads the item again when the time has come.
        await expect(item).to_have_attribute("data-state", "open", timeout=45_000)
        await expect(item.locator(".gate-words")).to_have_text(re.compile(r"^Opened at "))
        await expect(item.locator("button")).to_have_count(0)
        await expect(page.locator("#item-page .held-chip")).to_have_count(0)
        assert (await ok(agent, "get_node", node=task["ref"]))["held_by"] == []
        assert w.problems == []


# -- plans ----------------------------------------------


async def drafted(agent: Client, title: str = "Checkout plan") -> dict[str, Any]:
    made: dict[str, Any] = await ok(agent, "create_plan", items=PLAN, draft=True, title=title)
    return made


async def test_a_drafted_plan_is_one_row_that_is_approved_and_its_work_is_free() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        made = await drafted(agent)
        refs = [c["ref"] for c in made["created"]]
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        wanted = await sees_the_list(page, agent)
        assert wanted["total"] == 1 and wanted["groups"][0]["title"] == "Plans waiting for approval"
        group = page.locator("section.needs-group[data-group='plan']")
        await expect(group.locator("h2.group-title")).to_have_text("Plans")
        await expect(group.locator(".group-count")).to_have_text("1")
        await expect(page.locator("#sidebar-needs-count")).to_have_text("1")

        plan = row(page, "plan", refs[0])
        await expect(plan.locator(".change-kind .item-code")).to_have_text(f"Plan {made['plan']}")
        await expect(plan.locator("a.change-title")).to_have_text("Checkout plan")
        await expect(plan.locator("li[data-fact=by] .actor-name")).to_have_text(HARNESS_AGENT)
        await expect(plan.locator("li[data-fact=impact]")).to_have_text("Frees 7")
        # The card is a preview: the first lines of the plan and how many more there are. Every
        # item is a line of the diff on its review page, in the order the server read them.
        await expect(plan.locator(".diff-item-line")).to_have_count(6, timeout=SLOW)
        await expect(plan.locator("button.diff-fold")).to_have_text("1 more item")
        await expect(plan.locator(DECIDING)).to_have_text(
            ["Approve", "Request changes…", "Reject…"]
        )
        await no_overflow(page)
        await plan.locator("a.change-open").click()
        lines = page.locator("#review-diff .diff-line[data-sign='+']:not([data-part])")
        await expect(lines).to_have_count(7, timeout=SLOW)
        assert await lines.evaluate_all("els => els.map(e => e.dataset.ref)") == refs
        await expect(lines.first.locator(".item-code")).to_have_text(f"E-{refs[0].split(':')[1]}")
        await expect(lines.first).to_contain_text("Launch the checkout")
        await expect(page.locator("#review-diff .diff-hunk")).to_have_text(
            "1 epic, 1 story, 5 tasks added"
        )
        await page.locator("#review-back").click()
        await on_page(page, "needs")

        # Its work is held, and approving it is one decision by the person who presses it.
        for ref in refs:
            assert (await ok(agent, "get_node", node=ref))["held_by"] == [f"plan:{made['plan']}"]
        await plan.locator("button.needs-yes").click()
        await expect(plan).to_have_count(0, timeout=SLOW)
        await expect(page.locator("#needs-body")).to_have_text(EMPTY, timeout=SLOW)
        decided = (await ok(agent, "list_plans", status="approved"))["items"][0]
        assert decided["id"] == made["plan"] and decided["decided_by"] == "Priya Raman"
        for ref in refs:
            assert (await ok(agent, "get_node", node=ref))["held_by"] == []
        assert (await ok(agent, "next_work"))["found"] is True
        assert w.problems == []


async def test_rejecting_a_plan_needs_a_reason_and_drops_all_of_it_with_the_reason_on_each() -> (
    None
):
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        made = await drafted(agent, "Wrong scope plan")
        refs = [c["ref"] for c in made["created"]]
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        plan = row(page, "plan", refs[0])
        await expect(plan).to_be_visible(timeout=SLOW)
        await plan.locator("button.needs-no").click()
        dialog = page.locator("dialog#reject-dialog[open]")
        await expect(dialog).to_be_visible()
        await nothing_open(page)
        await expect(dialog.locator("h2")).to_have_text(f"Reject Plan {made['plan']}")
        await expect(dialog.locator("#reject-reason")).to_have_value("")
        await expect(dialog.locator(".reject-words")).to_have_count(0)
        # No reason: the server's refusal, in its words, in the dialog; nothing changed.
        await dialog.locator("button[type=submit]").click()
        problem = dialog.locator(".problem")
        await expect(problem).to_contain_text("needs a reason", timeout=SLOW)
        blank = person_call(project, owner, "approve_plan", plan=made["plan"], approved=False)
        plain((await problem.text_content()) or "", blank.body["message"])
        assert (await ok(agent, "list_plans", status="proposed"))["total"] == 1
        await dialog.locator("#reject-reason").fill("Wrong scope")
        await dialog.locator("button[type=submit]").click()
        await expect(page.locator("dialog#reject-dialog")).to_have_count(0, timeout=SLOW)
        await expect(plan).to_have_count(0, timeout=SLOW)

        rejected = (await ok(agent, "list_plans", status="rejected"))["items"][0]
        assert (rejected["id"], rejected["reason"]) == (made["plan"], "Wrong scope")
        for ref in refs:
            node = await ok(agent, "get_node", node=ref)
            assert node["status"] == "dropped"
            assert node["last_handoff"]["note"] == "The plan was rejected: Wrong scope"
            assert node["last_handoff"]["actor"] == owner.name
        assert w.problems == []


async def test_a_viewer_sees_the_plan_row_and_no_button_and_an_editor_decides_as_themselves() -> (
    None
):
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer", "Vera Viewer")
    editor = join_project(project, "editor", "Edith Editor")
    async with connect(project.mcp_url, project.key) as agent:
        made = await drafted(agent)
        first = made["created"][0]["ref"]
        async with browser_page(project.address, token=None, person=viewer, name=None) as w:
            page = w.page
            await w.open("#/needs")
            await on_page(page, "needs")
            await sees_the_list(page, agent)
            await expect(row(page, "plan", first).locator("a.change-title")).to_have_text(
                "Checkout plan"
            )
            await expect(page.locator(f"#needs-body li.needs-row {DECIDING}")).to_have_count(0)
            # The whole diff is there for a viewer, on the review page, and no bar.
            await row(page, "plan", first).locator("a.change-open").click()
            await expect(
                page.locator("#review-diff .diff-line[data-sign='+']:not([data-part])")
            ).to_have_count(7, timeout=SLOW)
            await expect(page.locator("#review-bar, button.verdict")).to_have_count(0)
            assert w.problems == []
        refusal = person_call(project, viewer, "approve_plan", plan=made["plan"], approved=True)
        assert refusal.status in (400, 403), refusal.body
        assert (await ok(agent, "list_plans", status="proposed"))["total"] == 1
        async with browser_page(project.address, token=None, person=editor, name=None) as w:
            page = w.page
            await w.open("#/needs")
            await on_page(page, "needs")
            await row(page, "plan", first).locator("button.needs-yes").click()
            await expect(row(page, "plan", first)).to_have_count(0, timeout=SLOW)
            assert w.problems == []
        assert (await ok(agent, "list_plans", status="approved"))["items"][0][
            "decided_by"
        ] == "Edith Editor"


async def test_held_work_by_a_plan_is_drawn_held_and_the_rows_fit_a_narrow_window() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        made = await drafted(agent)
        task = next(c["ref"] for c in made["created"] if c["kind"] == "task")
        page = w.page
        await open_item(w, task, "board")
        chip = page.locator("#item-page .state-strip .held-chip")
        await expect(chip).to_have_attribute(
            "aria-label", f"Held: Plan {made['plan']}", timeout=SLOW
        )
        await chip.hover()
        tip = page.locator(".hovercard:visible")
        await expect(tip).to_contain_text(f"Plan {made['plan']}")
        await expect(tip).to_contain_text("Waits for a person to approve the plan")
        await page.keyboard.press("Escape")
        # Held work is not offered as ready anywhere: not on the server, and not in a filter.
        assert (await ok(agent, "list_nodes", query="is:held"))["total"] == 7
        await page.set_viewport_size(NARROW)
        # The item's page, with its hold and its section, fits the window too.
        await open_item(w, task, "board")
        await expect(page.locator("#item-page .held-chip")).to_have_count(1, timeout=SLOW)
        await no_overflow(page)
        await w.go("#/needs")
        await on_page(page, "needs")
        await expect(row(page, "plan", made["created"][0]["ref"])).to_be_visible(timeout=SLOW)
        await no_overflow(page)
        # The buttons are reached with the keyboard alone.
        await row(page, "plan", made["created"][0]["ref"]).locator("button.needs-yes").focus()
        await page.keyboard.press("Tab")
        await expect(
            row(page, "plan", made["created"][0]["ref"]).locator("button.needs-changes")
        ).to_be_focused()
        await page.keyboard.press("Tab")
        await expect(
            row(page, "plan", made["created"][0]["ref"]).locator("button.needs-no")
        ).to_be_focused()
        assert w.problems == []


# -- the history and the feed ----------------------------------------------


async def test_the_history_and_the_feed_say_gates_plans_and_templates_in_plain_words() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, story, task = await make_tree(agent)
        timer = await ok(agent, "add_gate", node=task["ref"], kind="timer", not_before=in_days(30))
        approval = await ok(
            agent, "add_gate", node=story["ref"], kind="approval", question="May we go?"
        )
        assert (
            person_call(
                project, owner, "remove_gate", gate=timer["id"], reason="The date moved"
            ).status
            == 200
        )
        made = await drafted(agent, "Winter fleet")
        assert (
            person_call(project, owner, "approve_plan", plan=made["plan"], approved=True).status
            == 200
        )
        again = await drafted(agent, "Summer fleet")
        assert (
            person_call(
                project, owner, "approve_plan", plan=again["plan"], approved=False, reason="Too big"
            ).status
            == 200
        )
        template = {"items": [{"key": "a", "kind": "epic", "title": "An epic"}]}
        assert (
            person_call(
                project, owner, "save_plan_template", name="a-template", template=template
            ).status
            == 200
        )
        assert person_call(project, owner, "delete_plan_template", name="a-template").status == 200
        page = w.page

        # The gate shows on the item it holds, in plain words, and a plan or template on no item.
        await open_item(w, task["ref"], "board")
        history = part(page, "history")
        added = history.locator("li[data-action='gate_added']")
        await expect(added).to_have_count(1, timeout=SLOW)
        await expect(added.locator(".event-words")).to_have_text(
            re.compile(
                rf"^added Gate {timer['id']} to task T-\d+ “A task”: waits until "
                r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC\.$"
            )
        )
        lifted = history.locator("li[data-action='gate_removed']")
        await expect(lifted.locator(".event-words")).to_have_text(
            re.compile(rf"^lifted Gate {timer['id']} on task T-\d+ “A task”: The date moved\.$")
        )
        await open_item(w, story["ref"], "board")
        approval_line = part(page, "history").locator("li[data-action='gate_added'] .event-words")
        await expect(approval_line).to_have_text(
            re.compile(
                rf"^added Gate {approval['id']} to story S-\d+ “A story”: waits for a person to "
                rf"approve it \(Q-{approval['ask']}\)\.$"
            ),
            timeout=SLOW,
        )
        await expect(part(page, "history").locator("li[data-action^='plan_']")).to_have_count(0)

        # The feed has all of it, under its own filter.
        await w.go("#/activity?act_kind=gates")
        await expect(page.locator("#feed .event").first).to_be_visible(timeout=SLOW)
        words = await page.locator("#feed .event .event-words").all_inner_texts()
        joined = "\n".join(words)
        for wanted in (
            rf"added Gate {timer['id']} to task T-\d+ “A task”: waits until",
            rf"added Gate {approval['id']} to story S-\d+ “A story”: waits for a person to "
            r"approve it",
            rf"lifted Gate {timer['id']} on task T-\d+ “A task”: The date moved",
            rf"proposed Plan {made['plan']}, 1 epic, 1 story and 5 tasks: Winter fleet",
            rf"approved Plan {made['plan']}",
            rf"proposed Plan {again['plan']}, 1 epic, 1 story and 5 tasks: Summer fleet",
            rf"rejected Plan {again['plan']}: Too big",
            r"saved the plan template a-template",
            r"deleted the plan template a-template",
        ):
            assert re.search(wanted, joined), (wanted, joined)
        # The filter has its name, and nothing that is not about gates, plans or templates is in it.
        options = await page.locator("#a-kind option").all_inner_texts()
        assert "Gates, plans and templates" in options and "Suggested moves" in options
        kinds = await page.locator("#feed .event").evaluate_all(
            "els => els.map(e => e.dataset.kind)"
        )
        assert set(kinds) == {"gates"}, kinds
        # The history sentences carry no "#5" and no "Ask 5".
        assert not re.search(r"#\d|\bAsk \d", joined), joined
        assert w.problems == []


async def test_the_server_refuses_what_the_page_would_not_offer_in_its_own_words() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, _, task = await make_tree(agent)
        page = w.page
        await open_item(w, task["ref"], "board")
        dialog = await add_in_dialog(page, kind="timer", day=in_days(365 * 4))
        problem = dialog.locator(".problem")
        await expect(problem).to_contain_text("three years", timeout=SLOW)
        said = words_of(
            await refused(
                agent, "add_gate", node=task["ref"], kind="timer", not_before=in_days(365 * 4)
            )
        )
        plain((await problem.text_content()) or "", said)
        assert await gates_of(agent, task["ref"]) == []
        # A finished task cannot be held: the way to add a gate is not offered.
        await dialog.locator("button", has_text="Cancel").click()
        done = await ok(agent, "claim_node", node=task["ref"])
        assert done
        await ok(agent, "finish_work", node=task["ref"], outcome="done", note="Done.")
        await open_item(w, task["ref"], "board")
        await expect(part(page, "gates")).to_have_count(0, timeout=SLOW)
        assert w.problems == []
