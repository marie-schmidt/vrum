"""M16 proof 3, as M20 has it, against the running containers: a day's work with only Board, Graph
and Roadmap as primary destinations, driven in a real headless browser next to a real MCP client.
An epic, a story and a task are made on the Board, a criterion is added to the story on the
story's page, ready work is claimed through MCP (`next_work`), the claim and the handoff after
`finish_work` are seen on the Activity page, the work's context (notes, one of them decided by a
person) and history are read on the task's page and compared with what the agent was told, and the
work is scheduled on the Roadmap. Nothing opens at the side of a page: every record has its own
page, and each page keeps the view the person came from. What the server refuses is refused in its
own words, compared with the same call through MCP. (A task or story made with no parent is refused
in test_board_integration.py, which compares it too.)"""

import datetime
import re

import pytest
from playwright.async_api import Page, expect

from tests.board_helpers import (
    SLOW,
    Watched,
    browser_page,
    cell,
    create_in_browser,
    crumbs,
    drag_bar,
    no_panel_or_drawer,
    on,
    on_page,
    open_editor,
    open_item,
    plain,
    roadmap_ids,
    sidebar_current,
    timeline,
)
from tests.helpers import (
    code_of,
    connect,
    connect_as,
    make_note,
    number_of,
    ok,
    refused,
    unique_word,
)
from tests.world import make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

ACTOR = "Ada Lovelace"


async def create_on_board(w: Watched, kind: str, title: str, parent: str | None = None) -> str:
    """Make an item with the Board's own form. The form's button is on the Board, and it goes to the
    page of the new item, so each one starts from the Board again."""
    await w.go("#/board")
    await on_page(w.page, "board")
    return await create_in_browser(w.page, kind, title, parent)


async def shown_ids(page: Page, section: str) -> list[int]:
    """The ids of the notes in one section of the recall the page shows (notes, project_wide_notes
    or past_notes), in the order drawn."""
    ids: list[int] = await page.locator(
        f"#item-page [data-section={section}] tr.record-line"
    ).evaluate_all("els => els.map(e => Number(e.dataset.id))")
    return ids


async def test_a_day_of_work_through_the_board_the_graph_and_the_roadmap() -> None:
    word = unique_word()
    project = make_project(register(name=ACTOR), f"Daily {word}")
    async with (
        connect(project.mcp_url, project.key) as agent,
        connect_as("agent-keyed", project) as keyed,
        connect_as("agent-b", project) as bee,
        browser_page(project.address, token=project.key, name=ACTOR) as w,
    ):
        page = w.page

        # -- the Board: plan the work ------------------------------------------------------------
        await w.open("")
        await expect(page.locator("h1")).to_have_text("Board", timeout=SLOW)
        epic = await create_on_board(w, "epic", f"Billing {word}")
        story = await create_on_board(w, "story", f"Invoices {word}", epic)
        task = await create_on_board(w, "task", f"Template {word}", story)
        later = await create_on_board(w, "task", f"Send them {word}", story)
        # The references and fields the board shows are the ones MCP holds: the three kinds are
        # numbered apart, so the first epic, the first story and the first task all have number 1.
        listed = {n["ref"]: n for n in (await ok(agent, "list_nodes", limit=200))["items"]}
        assert set(listed) == {epic, story, task, later}
        assert [listed[i]["kind"] for i in (epic, story, task, later)] == [
            "epic", "story", "task", "task",
        ]  # fmt: skip
        assert [listed[i]["number"] for i in (epic, story, task, later)] == [1, 1, 1, 2]
        assert (epic, story, task, later) == ("epic:1", "story:1", "task:1", "task:2")
        assert (listed[story]["parent"], listed[task]["parent"]) == (epic, story)
        # The Board shows the tasks, and the stories too when it is asked to (`show=both`).
        await w.go("#/board?show=both")
        for ref in (story, task, later):
            await expect(page.locator(f".card[data-ref='{ref}']")).to_have_attribute(
                "data-status", listed[ref]["status"], timeout=SLOW
            )
        assert {listed[i]["title"] for i in listed} == {
            f"Billing {word}", f"Invoices {word}", f"Template {word}", f"Send them {word}",
        }  # fmt: skip
        # The story has no acceptance criterion yet, as the coverage report says.
        before = await ok(agent, "coverage_report", show="both", limit=1)
        assert before["story_without_rules_count"] == 1

        # -- an acceptance criterion added to the story, on the story's page -----------------------
        await page.locator(f".card[data-ref='{story}'] h3.card-title a").click()
        await expect(page.locator("#item-title")).to_contain_text(
            f"{code_of(story)} ", timeout=SLOW
        )
        await no_panel_or_drawer(page)
        # With none the section is an empty field, with the way to add one inside it.
        await expect(
            page.locator("#item-page [data-part=requirements] .details-empty")
        ).to_be_visible(timeout=SLOW)
        await page.locator("#item-page button.add-requirement").click()
        dialog = page.locator("dialog.modal")
        await expect(dialog.locator("#requirement-on")).to_contain_text(
            f"It goes on {code_of(story)}"
        )
        await dialog.locator("#requirement-title").fill(f"Every invoice shows its VAT {word}")
        await dialog.get_by_role("button", name="Add the acceptance criterion").click()
        # It stays on the story's page, which lists it at once in its table, still in the Board.
        requirement = page.locator("#item-page [data-part=requirements] tr.record-line")
        await expect(requirement).to_have_count(1, timeout=SLOW)
        await expect(requirement).to_contain_text(f"Every invoice shows its VAT {word}")
        rule_id = int(await requirement.get_attribute("data-rule") or 0)
        rule = await ok(agent, "get_rule", rule_id=rule_id)
        assert (rule["node"], rule["title"]) == (story, f"Every invoice shows its VAT {word}")
        assert (await ok(agent, "rule_history", rule_id=rule_id))["items"][0]["actor"] == ACTOR
        await expect(page.locator("#item-page .criteria-met")).to_have_text("0 of 1 met")
        # Its own page is a click away, in the Board, and says where it is.
        await requirement.locator("a.record-title").click()
        await expect(page.locator("#item-page[data-kind=rule]")).to_be_visible(timeout=SLOW)
        await expect(page.locator("#item-title")).to_contain_text(
            f"Acceptance criterion #{rule_id} Every invoice shows its VAT {word}"
        )
        # The path names the epic, the story and the acceptance criterion by their codes alone;
        # the titles are in each step's tooltip.
        assert (await crumbs(page))[2:] == [
            "Board", code_of(epic), code_of(story), f"AC-{rule_id}",
        ]  # fmt: skip
        assert await page.locator("#crumb-page").get_attribute("title") == (
            f"AC-{rule_id} Every invoice shows its VAT {word}"
        )
        # The story's page lists it in its table of acceptance criteria, which opens it again.
        await page.locator("#item-page .state-strip .in-story a").click()
        await expect(page.locator("#item-page")).to_have_attribute("data-ref", story, timeout=SLOW)
        await expect(requirement).to_have_count(1, timeout=SLOW)
        assert int(await requirement.get_attribute("data-rule") or 0) == rule_id
        await w.go("#/board")
        # Every story now has an acceptance criterion, as the coverage report says.
        covered = await ok(agent, "coverage_report", show="both", limit=1)
        assert covered["story_without_rules_count"] == 0
        # The task, on its own page, reads the same acceptance criterion where the work is: its
        # story's, in a group of its own, and it has none of its own to start with.
        await page.locator(f".card[data-ref='{task}'] h3.card-title a").click()
        await expect(page.locator("#item-title")).to_contain_text(f"{code_of(task)} ", timeout=SLOW)
        requirements = page.locator(
            "#item-page [data-part=requirements] [data-group=from-story] tr.record-line"
        )
        await expect(requirements).to_have_attribute("data-rule", str(rule_id), timeout=SLOW)
        await expect(
            page.locator("#item-page [data-part=requirements] .details-empty")
        ).to_be_visible()

        # -- what an agent knows, and what the task's page shows of it ---------------------------
        note = await make_note(
            keyed, f"VAT is 19 percent {word}", "The tax office site", nodes=[task]
        )
        decision = await make_note(
            keyed, f"Round per line {word}", decided_by="Marie", nodes=[task]
        )
        wide = await make_note(
            keyed, f"Always in euros {word}", decided_by="Marie", project_wide=True
        )

        # -- claim ready work through MCP ---------------------------------------------------------
        taken = await ok(keyed, "next_work")
        assert taken["work"]["ref"] == task and taken["claim"] is not None
        briefing = taken["context"]
        assert [r["id"] for r in briefing["acceptance_criteria"]] == [rule_id]

        # The claim is on the task's page, which was open and has not been reloaded.
        await expect(page.locator("#item-page .card-summary")).to_contain_text(
            "Held by agent-keyed", timeout=SLOW
        )
        # The page holds the very context the agent was briefed with, and the recall for the item:
        # the notes that are linked, the decided one first, and the ones that apply to everything.
        await expect(page.locator("#item-page [data-section=notes] tr.record-line")).to_have_count(
            2, timeout=SLOW
        )
        recalled = await ok(agent, "recall", node=task, limit=50)

        def held(section: str) -> list[int]:
            return [item["note"]["id"] for item in recalled[section]["items"]]

        assert held("notes") == [decision["id"], note["id"]]
        assert held("notes") == [n["id"] for n in briefing["notes"]]
        assert held("project_wide_notes") == [wide["id"]]
        assert [n["id"] for n in briefing["project_wide_notes"]] == [wide["id"]]
        assert await shown_ids(page, "notes") == held("notes")
        assert await shown_ids(page, "project_wide_notes") == held("project_wide_notes")
        row = page.locator(f"#item-page tr.record-line[data-ref='note:{note['id']}']")
        await expect(row).to_have_attribute("data-status", "unconfirmed")
        await expect(row.locator("td.cell-title")).to_contain_text("Why: The tax office site")
        await expect(row.locator(".chip.note-status")).to_have_text("Unconfirmed")
        await expect(
            page.locator(
                f"#item-page tr.record-line[data-ref='note:{decision['id']}'] td.cell-decided"
            )
        ).to_contain_text("Marie")

        # The Board, one click back in the path, shows the claim on the card.
        await page.locator("nav.crumbs .crumb-view a").click()
        board_card = page.locator(f".card[data-ref='{task}']")
        await expect(board_card).to_contain_text("Held by agent-keyed", timeout=SLOW)

        # -- the Activity page: the claim, who holds it, and the feed ------------------------------
        await page.locator("#activity-open").click()
        await on_page(page, "activity")
        assert await sidebar_current(page) == ["activity"]
        claim = page.locator(f"#claims-live .claim[data-node='{task}']")
        await expect(claim).to_have_attribute("data-actor", "agent-keyed", timeout=SLOW)
        await expect(claim).to_have_attribute("data-live", "1")
        taking = page.locator(
            "#feed li.event[data-action=claimed]", has=page.locator(f"a[data-ref='{task}']")
        )
        await expect(taking.first).to_contain_text("agent-keyed", timeout=SLOW)
        # The item is named by its code and its title, as everywhere a person reads it.
        await expect(taking.first).to_contain_text(f"task {code_of(task)} “Template {word}”")
        working = await ok(agent, "who_is_working")
        mine = next(a for a in working["agents"] if a["actor"] == "agent-keyed")
        assert [held_["node"] for held_ in mine["holding"]] == [task]
        await expect(page.locator("#agents .agent[data-actor=agent-keyed]")).to_contain_text(
            code_of(task)
        )

        # -- the handoff: finish the work and see it in the feed, on the Board and on the page ----
        await ok(
            keyed,
            "finish_work",
            node=task,
            outcome="done",
            note=f"Built the template; VAT is a field, not a constant {word}.",
        )
        handoff = page.locator(
            "#feed li.event[data-action=handoff]", has=page.locator(f"a[data-ref='{task}']")
        )
        await expect(handoff.first).to_contain_text("agent-keyed", timeout=SLOW)
        await expect(handoff.first).to_contain_text(f"VAT is a field, not a constant {word}")
        await expect(claim).to_have_count(0, timeout=SLOW)
        await page.locator("#main-nav a[data-page=board]").click()
        await expect(cell(page, epic, "done").locator(f".card[data-ref='{task}']")).to_be_visible(
            timeout=SLOW
        )
        # A line of the feed opens the page of the item it is about, which stays "in" Activity.
        await page.locator("#activity-open").click()
        await on_page(page, "activity")
        await expect(handoff.first).to_be_visible(timeout=SLOW)
        await handoff.first.locator(f"a[data-ref='{task}']").click()
        await expect(page.locator("#item-page")).to_have_attribute("data-ref", task, timeout=SLOW)
        assert "from=activity" in page.url
        assert (await crumbs(page))[2] == "Activity"
        assert await sidebar_current(page) == ["activity"]
        last = page.locator("#item-page [data-part=handoff]")
        await expect(last).to_contain_text("agent-keyed", timeout=SLOW)
        await expect(last).to_contain_text(f"VAT is a field, not a constant {word}")
        history = (await ok(agent, "node_history", node=task, limit=200))["items"]
        assert {"create", "claimed", "handoff"} <= {e["action"] for e in history}
        shown = await page.locator("#item-page [data-part=history] li").evaluate_all(
            "els => els.map(e => Number(e.dataset.event))"
        )
        assert sorted(shown) == sorted(e["id"] for e in history)
        assert shown == sorted(shown, reverse=True)  # newest first
        assert (await ok(agent, "get_node", node=task))["status"] == "done"

        # -- the Graph: look around the task, the same work and context, and the page of it -------
        await page.locator("#main-nav a[data-page=graph]").click()
        await expect(page.locator("#graph-explore")).to_be_visible(timeout=SLOW)
        await w.go(f"#/graph?tab=around&focus={task}")
        hub = page.locator("#graph-hub")
        await expect(hub).to_have_attribute("data-ref", task, timeout=SLOW)
        await expect(hub).to_have_class(re.compile(r"\bs-done\b"))
        # What is joined to the task is drawn around it, as recall says it.
        for key in (f"note:{note['id']}", f"note:{decision['id']}", f"rule:{rule_id}"):
            await expect(
                page.locator(f".graph-around button.graph-node[data-ref='{key}']")
            ).to_be_visible(timeout=SLOW)
        # The page of the task is one press away, and the way back is the Graph as it was left.
        await page.locator("#graph-hub-open").click()
        await expect(page.locator("#item-page")).to_have_attribute("data-ref", task, timeout=SLOW)
        assert page.url.endswith(f"#/item?item={task}&from=graph")
        assert (await crumbs(page))[2] == "Graph" and await sidebar_current(page) == ["graph"]
        await page.locator("nav.crumbs .crumb-view a").click()
        await expect(page.locator("#graph-explore")).to_be_visible(timeout=SLOW)
        assert page.url.endswith(f"#/graph?tab=around&focus={task}")

        # -- a dependency that would be a cycle, and a stale edit, refused in the server's words --
        await ok(agent, "add_link", source=later, target=task, link_type="needs_first")
        said = await refused(agent, "add_link", source=task, target=later, link_type="needs_first")
        await open_item(w, task)
        await page.locator("#link-relation").select_option("needs_first:out")
        await page.locator("#link-target").fill(code_of(later))
        await page.get_by_role("button", name="Add", exact=True).click()
        problem = page.locator("#item-page form.add-link .problem")
        await expect(problem).not_to_be_empty(timeout=SLOW)
        plain(await problem.inner_text(), said)

        await open_item(w, later)
        title = page.locator("#edit-title")
        await open_editor(page)
        await title.fill(f"My title {word}")
        version = (await ok(agent, "get_node", node=later))["version"]
        await ok(
            keyed, "update_node", node=later, expected_version=version,
            title=f"Agent title {word}",
        )  # fmt: skip
        said = await refused(agent, "update_node", node=later, expected_version=version, title="x")
        await page.get_by_role("button", name="Save changes").click()
        problem = page.locator("#item-page form.edit .problem .refusal-text")
        await expect(problem).to_be_visible(timeout=SLOW)
        plain(await problem.inner_text(), said)
        assert await title.input_value() == f"My title {word}"
        assert (await ok(agent, "get_node", node=later))["title"] == f"Agent title {word}"

        # -- a claim another agent holds cannot be taken, and the Activity page says what MCP says
        await ok(bee, "claim_node", node=later)
        said = await refused(agent, "claim_node", node=later)
        await page.locator("#activity-open").click()
        await on_page(page, "activity")
        await page.locator("#claim-id").fill(code_of(later))
        await page.locator("#claim-by-id-go").click()
        refusal = page.locator(".notice.refusal .notice-text").first
        await expect(refusal).to_be_visible(timeout=SLOW)
        plain(await refusal.inner_text(), said)
        assert "agent-b" in said
        assert (await ok(agent, "get_node", node=later))["claimed_by"] == "agent-b"
        await ok(bee, "release_claim", node=later, note="Not today.")
        # A refusal stays over the top of the page until it is dismissed, as a person would do.
        await page.locator(".notice.refusal button", has_text="Dismiss").click()
        await expect(page.locator(".notice.refusal")).to_have_count(0)
        # -- the Roadmap: schedule the work ----------------------------------------------
        await page.locator("#main-nav a[data-page=roadmap]").click()
        await on_page(page, "roadmap")
        await w.go(f"#/roadmap?q=epic:{number_of(epic)}")
        # Nothing is dated yet, so nothing is drawn, and the page says how work gets here.
        await expect(page.locator("#count")).to_contain_text(
            "0 items on the timeline", timeout=SLOW
        )
        await expect(page.locator(".rm-hint")).to_contain_text("Give work a start or due date")
        assert await page.locator(".rm-bar").count() == 0
        line = await timeline(page)
        first_start = line["today"] + datetime.timedelta(days=2)
        # Each task gets its dates on its page (the second ten days after the first), and the path
        # leads back to the Roadmap as it was left, scoped to the epic, with the task drawn.
        for ref, start in ((task, first_start), (later, first_start + datetime.timedelta(10))):
            await open_item(w, ref, "roadmap")
            assert page.url.endswith(f"#/item?item={ref}&from=roadmap")
            await open_editor(page)
            await page.locator("#edit-start").fill(start.isoformat())
            await page.locator("#edit-target").fill((start + datetime.timedelta(6)).isoformat())
            await page.get_by_role("button", name="Save changes").click()
            await expect(page.locator("#item-page .card-summary")).to_contain_text(
                start.isoformat(), timeout=SLOW
            )
            await page.locator("nav.crumbs .crumb-view a").click()
            await on_page(page, "roadmap")
            assert page.url.endswith(f"#/roadmap?q=epic:{number_of(epic)}")
            await expect(page.locator(f".rm-bar[data-ref='{ref}']")).to_be_visible(timeout=SLOW)
        scheduled = {i: await ok(agent, "get_node", node=i) for i in (task, later)}
        for ref in (task, later):
            node = scheduled[ref]
            bar = page.locator(f".rm-bar[data-ref='{ref}']")
            assert await bar.get_attribute("data-start") == node["start_on"]
            assert await bar.get_attribute("data-end") == node["target_on"]
            changes = [
                e
                for e in (await ok(agent, "node_history", node=ref, limit=200))["items"]
                if e["action"] == "update" and "start_on" in e["after"]
            ]
            assert [(e["actor"], e["before"]["start_on"]) for e in changes] == [(ACTOR, None)]
        assert on(scheduled[later]["start_on"]) - on(
            scheduled[task]["start_on"]
        ) == datetime.timedelta(10)
        # The link the agent made is an arrow from what is needed first to what waits.
        arrow = page.locator(f"path.dep[data-from='{task}'][data-to='{later}']")
        await expect(arrow).to_have_count(1)
        assert await roadmap_ids(page, 4) == {epic, story, task, later}

        # A start dragged past the target date is refused in the server's words, as MCP says it.
        node = scheduled[later]
        too_late = (on(node["start_on"]) + datetime.timedelta(days=8)).isoformat()
        said = await refused(
            agent, "update_node", node=later,
            expected_version=node["version"], start_on=too_late,
        )  # fmt: skip
        await drag_bar(page, later, 8, "start")
        refusal = page.locator(".notice.refusal .notice-text", has_text="Work cannot start after")
        await expect(refusal.first).to_be_visible(timeout=SLOW)
        plain(await refusal.first.inner_text(), said)
        assert (await ok(agent, "get_node", node=later))["start_on"] == node["start_on"]
        assert w.problems == [] or all("status of 400" in p for p in w.problems), w.problems
