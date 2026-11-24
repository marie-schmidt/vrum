"""M47 on the board: the Plan templates page.

Driven in a real headless browser and compared with what a real MCP client reads (see ./check.sh).
The page lists exactly the templates `list_plan_templates` holds (name, version, what it makes, its
variables), reads one whole, saves a new one or a new version (the server checks it and every
problem it lists is shown in the dialog in its words), makes a plan from one (a field for each
variable, its default filled in, optionally as a draft that waits for a person to approve it) and
deletes one. A viewer sees the list and can read one, and the server refuses a viewer's save,
delete and plan. Every test makes an account and a project of its own."""

import json
import re
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Page, ViewportSize, expect

from tests.board_helpers import SLOW, browser_page, nothing_open, on_page, plain
from tests.helpers import connect, ok, refused
from tests.test_board_needs_integration import no_overflow, row
from tests.test_checks_integration import as_person, words_of
from tests.world import Person, Project, join_project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NARROW: ViewportSize = {"width": 390, "height": 900}
RELEASE: dict[str, Any] = {
    "description": "Everything a release needs",
    "variables": {
        "version": {"description": "The version", "pattern": "^[0-9.]+$", "max_length": 20},
        "channel": {"choices": ["stable", "beta"], "default": "stable"},
        "team": {"required": False, "default": "the team"},
        "epic": {"type": "ref", "kind": "epic", "description": "Where the release goes"},
    },
    "items": [
        {"key": "prep", "kind": "story", "title": "Prepare {{version}}", "parent": "{{epic}}"},
        {"key": "tag", "kind": "task", "title": "Tag {{version}}", "parent": "prep", "size": "xs"},
        {"key": "ship", "kind": "task", "title": "Ship to {{channel}}", "parent": "prep"},
    ],
    "links": [{"source": "ship", "target": "tag", "link_type": "needs_first"}],
}
CHECKLIST: dict[str, Any] = {
    "description": "A new station",
    "variables": {"station": {"description": "The station's name"}},
    "items": [
        {"key": "epic", "kind": "epic", "title": "Open {{station}}"},
        {
            "key": "story",
            "kind": "story",
            "title": "Install the dock at {{station}}",
            "parent": "epic",
        },
    ],
}
BAD: dict[str, Any] = {
    "variables": {
        "Bad-Name": {},
        "pat": {"pattern": "(a+)+$"},
        "choice": {"choices": ["a"], "default": "b"},
    },
    "items": [{"key": "a", "kind": "epic", "title": "{{missing}} and {{Version}}"}],
}


def person_call(project: Project, who: Person, tool: str, **arguments: Any) -> Any:
    return as_person(who, project, tool, **arguments)


async def save(agent: Client, name: str, template: dict[str, Any]) -> dict[str, Any]:
    saved: dict[str, Any] = await ok(agent, "save_plan_template", name=name, template=template)
    return saved


def made_words(epics: int, stories: int, tasks: int) -> str:
    """What a plan holds, as the page says it: "1 epic, 2 stories and 4 tasks", leaving out a kind
    that has none."""
    parts = [
        f"{n} {one if n == 1 else many}"
        for n, one, many in (
            (epics, "epic", "epics"),
            (stories, "story", "stories"),
            (tasks, "task", "tasks"),
        )
        if n
    ]
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def entry(page: Page, name: str) -> Any:
    return page.locator(f"#templates li.template[data-name='{name}']")


async def listed(agent: Client) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = (await ok(agent, "list_plan_templates"))["items"]
    return found


# -- the list ----------------------------------------------


async def test_an_empty_page_says_what_a_template_is_and_the_list_is_the_servers() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        page = w.page
        await w.open("#/templates")
        await on_page(page, "templates")
        # One sentence and a button that makes a template: nothing is shipped in the page.
        empty = page.locator("#templates-empty")
        await expect(empty).to_be_visible(timeout=SLOW)
        await expect(empty).to_contain_text("A plan template is a plan you can make again")
        await expect(empty.locator("p")).to_have_count(1)
        await expect(page.locator("#new-template")).to_have_text("New template…")
        await expect(page.locator("#templates li")).to_have_count(0)
        # It is in the Settings menu, after the definition of done, and its link is marked.
        await expect(page.locator("#settings-menu")).to_have_attribute("aria-current", "page")
        await page.locator("#settings-menu").click()
        labels = await page.locator("#settings-list [role=menuitem]").all_inner_texts()
        assert labels == [
            "Users",
            "Definition of done",
            "Plan templates",
            "Tools",
            "Status and export",
        ]
        await page.keyboard.press("Escape")

        await save(agent, "release-checklist", RELEASE)
        await save(agent, "station-launch", CHECKLIST)
        await save(agent, "station-launch", {**CHECKLIST, "description": "A new station, again"})
        served = await listed(agent)
        assert [t["name"] for t in served] == ["release-checklist", "station-launch"]
        assert served[1]["version"] == 2
        await w.go("#/board")
        await w.go("#/templates")
        await expect(page.locator("#templates li.template")).to_have_count(2, timeout=SLOW)
        names = await page.locator("#templates li.template").evaluate_all(
            "els => els.map(e => e.dataset.name)"
        )
        assert names == [t["name"] for t in served]
        for info in served:
            one = entry(page, info["name"])
            await expect(one.locator(".template-name")).to_have_text(info["name"])
            await expect(one.locator(".template-version")).to_have_text(
                f"Version {info['version']}"
            )
            await expect(one.locator(".template-description")).to_have_text(info["description"])
            counts = info["counts"]
            await expect(one.locator(".template-counts")).to_have_text(
                f"Makes {made_words(counts['epics'], counts['stories'], counts['tasks'])}."
            )
            chips = await one.locator(".template-variables .variable").all_inner_texts()
            assert chips == list(info["variables"])
            await expect(one.locator(".template-saved .actor-agent")).to_have_count(1)
        # A variable's tooltip says what it is for and what it takes.
        version = entry(page, "release-checklist").locator("[data-variable='version']")
        title = await version.get_attribute("title") or ""
        assert "The version" in title and "required" in title
        channel = entry(page, "release-checklist").locator("[data-variable='channel']")
        assert "one of stable, beta" in (await channel.get_attribute("title") or "")
        await expect(page.locator("#templates-empty")).to_have_count(0)
        await no_overflow(page)

        # Read whole: exactly the text the server keeps, as text.
        await entry(page, "release-checklist").locator("button.view-template-open").click()
        dialog = page.locator("dialog#view-template[open]")
        await expect(dialog).to_be_visible()
        text = await dialog.locator("#template-json").text_content(timeout=SLOW)
        whole = (await ok(agent, "list_plan_templates", name="release-checklist"))["items"][0]
        assert json.loads(text or "") == whole["template"]
        await dialog.locator("button", has_text="Close").click()
        await expect(page.locator("dialog#view-template")).to_have_count(0)
        assert w.problems == []


async def test_a_viewer_sees_the_list_and_reads_a_template_and_nothing_else() -> None:
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer", "Vera Viewer")
    async with connect(project.mcp_url, project.key) as agent:
        await save(agent, "release-checklist", RELEASE)
        async with browser_page(project.address, token=None, person=viewer, name=None) as w:
            page = w.page
            await w.open("#/templates")
            await on_page(page, "templates")
            one = entry(page, "release-checklist")
            await expect(one).to_be_visible(timeout=SLOW)
            await expect(page.locator("#new-template")).to_have_count(0)
            await expect(one.locator("button")).to_have_text(["View"])
            await one.locator("button.view-template-open").click()
            await expect(page.locator("#template-json")).to_contain_text(
                "Prepare {{version}}", timeout=SLOW
            )
            assert w.problems == []
        for call in (
            person_call(project, viewer, "save_plan_template", name="x", template=CHECKLIST),
            person_call(project, viewer, "delete_plan_template", name="release-checklist"),
            person_call(
                project,
                viewer,
                "create_plan",
                template="release-checklist",
                values={"version": "1"},
            ),
        ):
            assert call.status in (400, 403), call.body
        assert [t["name"] for t in await listed(agent)] == ["release-checklist"]


# -- saving ----------------------------------------------


async def test_a_bad_template_lists_every_problem_and_a_good_one_is_saved_and_changed() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        page = w.page
        await w.open("#/templates")
        await on_page(page, "templates")
        await page.locator("#new-template").click()
        dialog = page.locator("dialog#save-template[open]")
        await expect(dialog).to_be_visible()
        await nothing_open(page)
        await expect(dialog.locator("#template-name")).to_have_value("")
        await expect(dialog.locator("#template-text")).to_have_value("")
        # The page only reads that it is JSON; everything else is the server's.
        await dialog.locator("#template-name").fill("station-launch")
        await dialog.locator("#template-text").fill("{ not json")
        await dialog.locator("button[type=submit]").click()
        problem = dialog.locator(".problem")
        await expect(problem).to_contain_text("That is not JSON")
        await expect(problem).to_contain_text("Nothing was saved.")
        # A template with several things wrong: every problem is listed, in the server's words.
        await dialog.locator("#template-text").fill(json.dumps(BAD))
        await dialog.locator("button[type=submit]").click()
        await expect(problem).to_contain_text("because of", timeout=SLOW)
        shown = (await problem.text_content()) or ""
        said = words_of(
            await refused(agent, "save_plan_template", name="station-launch", template=BAD)
        )
        plain(shown, said)
        numbered = re.findall(r"\b\d+\. ", said)
        assert len(numbered) >= 4, said
        for wanted in ("Bad-Name", "{{missing}}", "{{Version}}"):
            assert wanted in shown, (wanted, shown)
        assert await listed(agent) == []
        # Fixed, it is saved; the dialog closes and the list has it.
        await dialog.locator("#template-text").fill(json.dumps(CHECKLIST))
        await dialog.locator("button[type=submit]").click()
        await expect(page.locator("dialog#save-template")).to_have_count(0, timeout=SLOW)
        one = entry(page, "station-launch")
        await expect(one).to_be_visible(timeout=SLOW)
        await expect(one.locator(".template-version")).to_have_text("Version 1")
        (info,) = await listed(agent)
        assert info["saved_by"] == "Priya Raman" and info["saved_by_kind"] == "person"
        await expect(one.locator(".template-saved .actor-person")).to_contain_text("Priya Raman")

        # Changing one reads its whole text first, and saving the same name is a second version.
        await one.locator("button.edit-template-open").click()
        change = page.locator("dialog#save-template[open]")
        await expect(change.locator("h2")).to_have_text("Change the plan template station-launch")
        await expect(change.locator("#template-name")).to_have_value("station-launch")
        whole = (await ok(agent, "list_plan_templates", name="station-launch"))["items"][0]
        text = await change.locator("#template-text").input_value()
        assert json.loads(text) == whole["template"]
        edited = json.loads(text)
        edited["description"] = "A new station, with a dock"
        await change.locator("#template-text").fill(json.dumps(edited))
        await change.locator("button[type=submit]").click()
        await expect(page.locator("dialog#save-template")).to_have_count(0, timeout=SLOW)
        await expect(one.locator(".template-version")).to_have_text("Version 2", timeout=SLOW)
        await expect(one.locator(".template-description")).to_have_text(
            "A new station, with a dock"
        )
        assert (await listed(agent))[0]["version"] == 2
        assert w.problems == []


# -- making a plan ----------------------------------------------


async def test_a_plan_is_made_from_a_template_with_each_variable_in_its_own_field() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic = await ok(agent, "create_node", actor="x", kind="epic", title="Releases")
        await ok(agent, "create_node", actor="x", kind="epic", title="Other work")
        await save(agent, "release-checklist", RELEASE)
        page = w.page
        await w.open("#/templates")
        await on_page(page, "templates")
        await entry(page, "release-checklist").locator("button.make-plan-open").click()
        dialog = page.locator("dialog#make-plan[open]")
        await expect(dialog).to_be_visible(timeout=SLOW)
        await nothing_open(page)
        await expect(dialog.locator("h2")).to_have_text("Make a plan from release-checklist")
        # A field for each variable, in the template's order, with what it is in words: required or
        # not, its default filled in, a select for choices and one of the epics for a reference.
        labels = await dialog.locator("label[for^='var-']").all_inner_texts()
        order = list((await listed(agent))[0]["variables"])
        assert sorted(order) == ["channel", "epic", "team", "version"]
        optional = {"channel", "team"}
        assert labels == [
            f"{name} ({'optional' if name in optional else 'required'})" for name in order
        ]
        await expect(dialog.locator("#var-version")).to_have_value("")
        await expect(dialog.locator("#var-channel")).to_have_value("stable")
        assert await dialog.locator("#var-channel option").all_inner_texts() == ["stable", "beta"]
        await expect(dialog.locator("#var-team")).to_have_value("the team")
        await expect(dialog.locator("#var-epic")).to_have_value("")
        options = await dialog.locator("#var-epic option").all_inner_texts()
        assert options[0] == "Choose an epic" and len(options) == 3
        assert any("Releases" in o for o in options) and not any(
            "story" in o.lower() for o in options
        )
        await expect(dialog.locator("#plan-title")).to_have_value("")
        await expect(dialog.locator("#plan-draft")).not_to_be_checked()
        await expect(dialog.locator(".hint").first).to_be_visible()

        # Values that do not fit: every problem the server finds is in the dialog, in its words.
        await dialog.locator("#var-version").fill("two")
        await dialog.locator("button[type=submit]").click()
        problem = dialog.locator(".problem")
        await expect(problem).to_contain_text("does not fit its pattern", timeout=SLOW)
        await expect(problem).to_contain_text("epic")
        shown = (await problem.text_content()) or ""
        said = words_of(
            await refused(
                agent, "create_plan", template="release-checklist", values={"version": "two"}
            )
        )
        plain(shown, said)
        assert (await ok(agent, "list_nodes", kind="story"))["total"] == 0

        await dialog.locator("#var-version").fill("2.1")
        await dialog.locator("#var-epic").select_option(epic["ref"])
        await dialog.locator("button[type=submit]").click()
        made = dialog.locator("#template-made")
        await expect(made).to_be_visible(timeout=SLOW)
        await expect(made.locator(".template-made-words")).to_have_text("Made 1 story and 2 tasks.")
        stories = (await ok(agent, "list_nodes", kind="story"))["items"]
        assert [s["title"] for s in stories] == ["Prepare 2.1"] and stories[0]["parent"] == epic[
            "ref"
        ]
        tasks = (await ok(agent, "list_nodes", kind="task"))["items"]
        assert sorted(t["title"] for t in tasks) == ["Ship to stable", "Tag 2.1"]
        await expect(made.locator("li a.item-link")).to_have_count(1)
        await expect(made.locator("li a.item-link .item-code")).to_have_text(
            f"S-{stories[0]['number']}"
        )
        # The form is out of the way; Close ends it; the new story is on the Board.
        await expect(dialog.locator("form")).to_be_hidden()
        await expect(dialog.locator("#template-made-needs")).to_have_count(0)
        await made.locator("li a.item-link").click()
        await expect(page.locator("dialog#make-plan")).to_have_count(0)
        await expect(page.locator("#item-page")).to_be_visible(timeout=SLOW)
        assert w.problems == []


async def test_a_plan_made_as_a_draft_waits_on_the_needs_page_as_one_row() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic = await ok(agent, "create_node", actor="x", kind="epic", title="Releases")
        await save(agent, "release-checklist", RELEASE)
        page = w.page
        await w.open("#/templates")
        await on_page(page, "templates")
        await entry(page, "release-checklist").locator("button.make-plan-open").click()
        dialog = page.locator("dialog#make-plan[open]")
        await dialog.locator("#var-version").fill("3.0")
        await dialog.locator("#var-channel").select_option("beta")
        await dialog.locator("#var-epic").select_option(epic["ref"])
        await dialog.locator("#plan-title").fill("Release 3.0 plan")
        await dialog.locator("#plan-draft").check()
        await dialog.locator("button[type=submit]").click()
        made = dialog.locator("#template-made")
        await expect(made).to_be_visible(timeout=SLOW)
        plans = (await ok(agent, "list_plans", status="proposed"))["items"]
        assert [p["title"] for p in plans] == ["Release 3.0 plan"]
        await expect(made.locator(".template-made-draft")).to_contain_text(
            f"Plan {plans[0]['id']} waits for a person to approve it."
        )
        story = (await ok(agent, "list_nodes", kind="story"))["items"][0]
        assert story["title"] == "Prepare 3.0" and story["held_by"] == [f"plan:{plans[0]['id']}"]
        tasks = (await ok(agent, "list_nodes", kind="task"))["items"]
        assert "Ship to beta" in [t["title"] for t in tasks]
        # The way to the page where a person decides.
        await made.locator("#template-made-needs").click()
        await on_page(page, "needs")
        proposal = row(page, "plan", plans[0]["items"][0])
        await expect(proposal.locator(".change-title")).to_have_text(
            "Release 3.0 plan", timeout=SLOW
        )
        await expect(proposal.locator(".change-kind .needs-code")).to_have_text(
            f"Plan {plans[0]['id']}"
        )
        await expect(page.locator("dialog#make-plan")).to_have_count(0)
        assert w.problems == []


# -- deleting ----------------------------------------------


async def test_deleting_a_template_asks_first_and_what_was_made_from_it_stays() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        await save(agent, "station-launch", CHECKLIST)
        await save(
            agent,
            "release-checklist",
            {"variables": {}, "items": [{"key": "e", "kind": "epic", "title": "Release"}]},
        )
        made = await ok(
            agent, "create_plan", template="station-launch", values={"station": "North"}
        )
        page = w.page
        await w.open("#/templates")
        await on_page(page, "templates")
        await entry(page, "station-launch").locator("button.delete-template-open").click()
        dialog = page.locator("dialog#delete-template[open]")
        await expect(dialog).to_be_visible()
        await nothing_open(page)
        await expect(dialog.locator("h2")).to_have_text("Delete station-launch")
        await expect(dialog).to_contain_text("Plans already made from it stay as they are")
        # Cancelling changes nothing.
        await dialog.locator("button", has_text="Cancel").click()
        assert len(await listed(agent)) == 2
        await entry(page, "station-launch").locator("button.delete-template-open").click()
        await page.locator("dialog#delete-template[open] button[type=submit]").click()
        await expect(page.locator("dialog#delete-template")).to_have_count(0, timeout=SLOW)
        await expect(entry(page, "station-launch")).to_have_count(0, timeout=SLOW)
        assert [t["name"] for t in await listed(agent)] == ["release-checklist"]
        epics = (await ok(agent, "list_nodes", kind="epic"))["items"]
        assert [e["title"] for e in epics] == ["Open North"] and made["counts"]["epic"] == 1
        # The last one: the page says what a template is again.
        await entry(page, "release-checklist").locator("button.delete-template-open").click()
        await page.locator("dialog#delete-template[open] button[type=submit]").click()
        await expect(page.locator("#templates-empty")).to_be_visible(timeout=SLOW)
        assert w.problems == []


async def test_the_page_fits_a_narrow_window_and_its_buttons_are_reached_with_the_keyboard() -> (
    None
):
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        await save(agent, "release-checklist", RELEASE)
        page = w.page
        await page.set_viewport_size(NARROW)
        await w.open("#/templates")
        await on_page(page, "templates")
        await expect(entry(page, "release-checklist")).to_be_visible(timeout=SLOW)
        await no_overflow(page)
        make = entry(page, "release-checklist").locator("button.make-plan-open")
        await make.focus()
        await page.keyboard.press("Tab")
        await expect(
            entry(page, "release-checklist").locator("button.view-template-open")
        ).to_be_focused()
        await page.keyboard.press("Tab")
        await expect(
            entry(page, "release-checklist").locator("button.edit-template-open")
        ).to_be_focused()
        await page.keyboard.press("Tab")
        await expect(
            entry(page, "release-checklist").locator("button.delete-template-open")
        ).to_be_focused()
        # A long refusal wraps inside the dialog instead of widening anything.
        await make.click()
        dialog = page.locator("dialog#make-plan[open]")
        await dialog.locator("button[type=submit]").click()
        await expect(dialog.locator(".problem")).to_contain_text("required", timeout=SLOW)
        box = await dialog.bounding_box()
        assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= NARROW["width"] + 1
        assert w.problems == []
