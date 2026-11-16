"""M16 proof 3 against the running containers: a day's work with only Board, Graph and Roadmap as
primary destinations, driven in a real headless browser next to a real MCP client. An epic, a
story and a task are made on the Board, a requirement is placed on the story in the panel, ready
work is claimed through MCP (`next_work`), the claim and the handoff after `finish_work` are seen
in the Activity drawer, the work's context (facts and decisions) and history are read in the
panel and compared with what the agent was told, and the work is scheduled on the Roadmap. What
the server refuses is refused in its own words, compared with the same call through MCP. (A task
or story made with no parent is refused in test_board_integration.py, which compares it too.)"""

import datetime
import re

import pytest
from playwright.async_api import Locator, Page, expect

from tests.board_helpers import (
    SLOW,
    browser_page,
    cell,
    create_in_browser,
    drag_bar,
    on,
    plain,
    roadmap_ids,
    timeline,
)
from tests.helpers import connect, make_decision, make_fact, ok, refused, unique_word
from tests.world import make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

ACTOR = "Ada Lovelace"


async def record_ids(panel: Locator, section: str) -> list[int]:
    """The ids of the facts or decisions in one section of the recall the panel shows."""
    numbers: list[int] = await panel.locator(f"[data-section={section}] li.record").evaluate_all(
        "els => els.map(e => Number(e.dataset.record.split(':')[1]))"
    )
    return numbers


async def tile(page: Page, key: str) -> int:
    link = page.locator(f"#attention a.attention-item[data-key={key}]")
    await expect(link).to_be_visible(timeout=SLOW)
    return int(await link.get_attribute("data-value") or "-1")


async def test_a_day_of_work_through_the_board_the_graph_and_the_roadmap() -> None:
    word = unique_word()
    project = make_project(register(), f"Daily {word}")
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=ACTOR) as w,
    ):
        page = w.page

        # -- the Board: plan the work ------------------------------------------------------------
        await w.open("")
        await expect(page.locator("h1")).to_have_text("Board", timeout=SLOW)
        epic = await create_in_browser(page, "epic", f"Billing {word}")
        story = await create_in_browser(page, "story", f"Invoices {word}", epic)
        task = await create_in_browser(page, "task", f"Template {word}", story)
        later = await create_in_browser(page, "task", f"Send them {word}", story)
        # The ids and fields the board shows are the ones MCP holds.
        listed = {n["id"]: n for n in (await ok(agent, "list_nodes", limit=200))["items"]}
        assert set(listed) == {epic, story, task, later}
        assert [listed[i]["kind"] for i in (epic, story, task, later)] == [
            "epic", "story", "task", "task",
        ]  # fmt: skip
        assert (listed[story]["parent_id"], listed[task]["parent_id"]) == (epic, story)
        for node_id in (story, task, later):
            await expect(page.locator(f".card[data-id='{node_id}']")).to_have_attribute(
                "data-status", listed[node_id]["status"]
            )
        assert {listed[i]["title"] for i in listed} == {
            f"Billing {word}", f"Invoices {word}", f"Template {word}", f"Send them {word}",
        }  # fmt: skip
        assert (await tile(page, "bare")) == 1  # the story has no requirement yet

        # -- a requirement placed on the story, in the panel -------------------------------------
        await page.locator(f".card[data-id='{story}']").click()
        await expect(page.locator("#card-title")).to_contain_text(f"#{story} ", timeout=SLOW)
        await page.locator("#panel button.add-requirement").click()
        dialog = page.locator("dialog.modal")
        await expect(dialog.locator("#requirement-story")).to_have_value(str(story))
        await dialog.locator("#requirement-title").fill(f"Every invoice shows its VAT {word}")
        await dialog.get_by_role("button", name="Create the requirement").click()
        row = page.locator("#panel [data-part=requirements] li.requirement")
        await expect(row).to_have_count(1, timeout=SLOW)
        await expect(row).to_contain_text(f"Every invoice shows its VAT {word}")
        rule_id = int(await row.get_attribute("data-rule") or 0)
        rule = await ok(agent, "get_rule", rule_id=rule_id)
        assert (rule["story_id"], rule["title"]) == (story, f"Every invoice shows its VAT {word}")
        assert (await ok(agent, "rule_history", rule_id=rule_id))["items"][0]["actor"] == ACTOR
        await expect(page.locator("#attention a[data-key=bare]")).to_have_attribute(
            "data-value", "0", timeout=SLOW
        )
        # The task and the epic read the same requirement where the work is.
        await page.locator(f".card[data-id='{task}']").click()
        await expect(page.locator("#card-title")).to_contain_text(f"#{task} ", timeout=SLOW)
        requirements = page.locator("#panel [data-part=requirements] li.requirement")
        await expect(requirements).to_have_attribute("data-rule", str(rule_id))

        # -- what an agent knows, and what the panel shows of it ----------------------------------
        fact = await make_fact(agent, f"VAT is 19 percent {word}", source="The tax office site",
                               node_ids=[task])  # fmt: skip
        decision = await make_decision(agent, f"Round per line {word}", node_ids=[task])
        wide = await make_decision(agent, f"Always in euros {word}", project_wide=True)

        # -- claim ready work through MCP ---------------------------------------------------------
        taken = await ok(agent, "next_work", actor="agent-keyed")
        assert taken["work"]["id"] == task and taken["claim"] is not None
        briefing = taken["context"]
        assert [r["id"] for r in briefing["rules"]] == [rule_id]

        # The claim is on the card, in the panel and in the Activity drawer.
        board_card = page.locator(f".card[data-id='{task}']")
        await expect(board_card).to_contain_text("Held by agent-keyed", timeout=SLOW)
        await expect(page.locator("#panel .card-detail")).to_contain_text(
            "Held by agent-keyed", timeout=SLOW
        )
        await page.locator("#activity-open").click()
        drawer = page.locator("#drawer[data-drawer=activity]")
        await expect(drawer).to_be_visible(timeout=SLOW)
        claim = drawer.locator(f"#claims-live .claim[data-node='{task}']")
        await expect(claim).to_have_attribute("data-actor", "agent-keyed", timeout=SLOW)
        await expect(claim).to_have_attribute("data-live", "1")
        taking = drawer.locator("#feed li.event[data-action=claimed]", has_text=f"#{task}")
        await expect(taking.first).to_contain_text("agent-keyed", timeout=SLOW)
        working = await ok(agent, "who_is_working")
        mine = next(a for a in working["agents"] if a["actor"] == "agent-keyed")
        assert [held["node_id"] for held in mine["holding"]] == [task]
        await expect(drawer.locator("#agents .agent[data-actor=agent-keyed]")).to_contain_text(
            f"#{task}"
        )

        # The panel holds the very context the agent was briefed with, and the recall for the item.
        panel = page.locator("#panel")
        await expect(panel.locator("[data-section=facts] li.record")).to_have_count(1)
        recalled = await ok(agent, "recall", node_id=task, limit=50)

        def held(section: str) -> list[int]:
            return [
                (item.get("fact") or item["decision"])["id"] for item in recalled[section]["items"]
            ]

        assert held("facts") == [fact["id"]] == [f["id"] for f in briefing["facts"]]
        assert held("active_decisions") == [decision["id"]]
        assert [d["id"] for d in briefing["active_decisions"]] == [decision["id"]]
        assert wide["id"] in held("project_wide_decisions")
        assert wide["id"] in [d["id"] for d in briefing["project_wide_decisions"]]
        assert await record_ids(panel, "facts") == held("facts")
        assert await record_ids(panel, "active_decisions") == held("active_decisions")
        assert await record_ids(panel, "project_wide_decisions") == held("project_wide_decisions")
        row = panel.locator(f"li.record[data-record='fact:{fact['id']}']")
        await expect(row).to_have_attribute("data-confirmation", "unconfirmed")
        await expect(row).to_contain_text("Source: The tax office site")
        await expect(row).to_contain_text("Not confirmed")

        # -- the handoff: finish the work and see it in the drawer, the panel and the history ----
        await ok(
            agent,
            "finish_work",
            actor="agent-keyed",
            node_id=task,
            outcome="done",
            note=f"Built the template; VAT is a field, not a constant {word}.",
        )
        handoff = drawer.locator("#feed li.event[data-action=handoff]", has_text=f"#{task}")
        await expect(handoff.first).to_contain_text("agent-keyed", timeout=SLOW)
        await expect(handoff.first).to_contain_text(f"VAT is a field, not a constant {word}")
        await expect(claim).to_have_count(0, timeout=SLOW)
        await expect(cell(page, epic, "done").locator(f".card[data-id='{task}']")).to_be_visible(
            timeout=SLOW
        )
        last = panel.locator("[data-part=handoff]")
        await expect(last).to_contain_text("agent-keyed", timeout=SLOW)
        await expect(last).to_contain_text(f"VAT is a field, not a constant {word}")
        history = (await ok(agent, "node_history", node_id=task, limit=200))["items"]
        assert {"create", "claimed", "handoff"} <= {e["action"] for e in history}
        shown = await panel.locator("[data-part=history] li").evaluate_all(
            "els => els.map(e => Number(e.dataset.event))"
        )
        assert sorted(shown) == sorted(e["id"] for e in history)
        assert shown == sorted(shown, reverse=True)  # newest first
        assert (await ok(agent, "get_node", node_id=task))["status"] == "done"

        # -- the Graph: the same work and context, in the same panel -------------------------------
        await page.locator("#drawer-close").click()
        await page.locator("#main-nav a[data-page=graph]").click()
        await expect(page.locator("#graph-page")).to_be_visible(timeout=SLOW)
        assert f"sel=node:{task}" in page.url
        await expect(page.locator("#card-title")).to_contain_text(f"#{task} ")
        dot = page.locator(f".dot[data-key='node:{task}']")
        await expect(dot).to_have_attribute("data-status", "done", timeout=SLOW)
        await expect(dot).to_have_class(re.compile(r"\bchosen\b"))
        await page.locator("#graph-view-context").click()
        for key in (f"fact:{fact['id']}", f"decision:{decision['id']}", f"rule:{rule_id}"):
            await expect(page.locator(f".dot[data-key='{key}']")).to_be_visible(timeout=SLOW)

        # -- a dependency that would be a cycle, and a stale edit, refused in the server's words --
        await ok(
            agent, "add_link", actor="a", source_id=later, target_id=task, link_type="needs_first"
        )
        said = await refused(
            agent, "add_link", actor=ACTOR, source_id=task, target_id=later, link_type="needs_first"
        )
        await w.go(f"#/board?epic={epic}&sel=node:{task}")
        await expect(page.locator("#card-title")).to_contain_text(f"#{task} ", timeout=SLOW)
        await page.locator("#link-relation").select_option("needs_first:out")
        await page.locator("#link-target").fill(f"#{later}")
        await page.get_by_role("button", name="Add", exact=True).click()
        problem = page.locator("#panel .inline-form .problem").last
        await expect(problem).not_to_be_empty(timeout=SLOW)
        plain(await problem.inner_text(), said)

        await w.go(f"#/board?epic={epic}&sel=node:{later}")
        await expect(page.locator("#card-title")).to_contain_text(f"#{later} ", timeout=SLOW)
        title = page.locator("#edit-title")
        await title.fill(f"My title {word}")
        version = (await ok(agent, "get_node", node_id=later))["version"]
        await ok(
            agent, "update_node", actor="agent-keyed", node_id=later, expected_version=version,
            title=f"Agent title {word}",
        )  # fmt: skip
        said = await refused(
            agent, "update_node", actor=ACTOR, node_id=later, expected_version=version, title="x"
        )
        await page.get_by_role("button", name="Save changes").click()
        problem = page.locator("#panel .edit .problem .refusal-text")
        await expect(problem).to_be_visible(timeout=SLOW)
        plain(await problem.inner_text(), said)
        assert await title.input_value() == f"My title {word}"
        assert (await ok(agent, "get_node", node_id=later))["title"] == f"Agent title {word}"

        # -- a claim another agent holds cannot be taken, and the drawer says what MCP says -------
        await ok(agent, "claim_node", actor="agent-b", node_id=later)
        said = await refused(agent, "claim_node", actor=ACTOR, node_id=later)
        await page.locator("#activity-open").click()
        await expect(page.locator("#drawer[data-drawer=activity]")).to_be_visible(timeout=SLOW)
        await page.locator("#claim-id").fill(f"#{later}")
        await page.locator("#claim-by-id-go").click()
        refusal = page.locator(".notice.refusal .notice-text").first
        await expect(refusal).to_be_visible(timeout=SLOW)
        plain(await refusal.inner_text(), said)
        assert "agent-b" in said
        assert (await ok(agent, "get_node", node_id=later))["claimed_by"] == "agent-b"
        await ok(agent, "release_claim", actor="agent-b", node_id=later, note="Not today.")
        await page.locator("#drawer-close").click()

        # -- the Roadmap: schedule the work ----------------------------------------------
        await page.locator("#main-nav a[data-page=roadmap]").click()
        await expect(page.locator("h1")).to_have_text("Roadmap", timeout=SLOW)
        await w.go(f"#/roadmap?q=epic:{epic}&sel=node:{task}")
        await expect(page.locator(f".tray-item[data-id='{later}']")).to_be_visible(timeout=SLOW)
        await expect(page.locator("#card-title")).to_contain_text(f"#{task} ", timeout=SLOW)
        line = await timeline(page)
        # With nothing dated there is no timeline to drop on, so the first task gets its dates in
        # the panel, and the second is dropped on the timeline, ten days after the first starts.
        first_start = line["today"] + datetime.timedelta(days=2)
        await page.locator("#edit-start").fill(first_start.isoformat())
        await page.locator("#edit-target").fill((first_start + datetime.timedelta(6)).isoformat())
        await page.get_by_role("button", name="Save changes").click()
        await expect(page.locator(f".rm-bar[data-id='{task}']")).to_be_visible(timeout=SLOW)
        at = ((line["today"] - line["origin"]).days + 12) * line["ppd"]
        await page.drag_and_drop(
            f".tray-item[data-id='{later}'] .tray-drag",
            ".rm-rows",
            target_position={"x": at + line["ppd"] / 2, "y": 60},
        )
        await expect(page.locator(f".rm-bar[data-id='{later}']")).to_be_visible(timeout=SLOW)
        scheduled = {i: await ok(agent, "get_node", node_id=i) for i in (task, later)}
        for node_id in (task, later):
            node = scheduled[node_id]
            bar = page.locator(f".rm-bar[data-id='{node_id}']")
            assert await bar.get_attribute("data-start") == node["start_on"]
            assert await bar.get_attribute("data-end") == node["target_on"]
            changes = [
                e
                for e in (await ok(agent, "node_history", node_id=node_id, limit=200))["items"]
                if e["action"] == "update" and "start_on" in e["after"]
            ]
            assert [(e["actor"], e["before"]["start_on"]) for e in changes] == [(ACTOR, None)]
        assert on(scheduled[later]["start_on"]) - on(
            scheduled[task]["start_on"]
        ) == datetime.timedelta(10)
        # The link the agent made is an arrow from what is needed first to what waits.
        arrow = page.locator(f"path.dep[data-from='{task}'][data-to='{later}']")
        await expect(arrow).to_have_count(1)
        assert await roadmap_ids(page, 4, 0) == {epic, story, task, later}

        # A start dragged past the target date is refused in the server's words, as MCP says it.
        node = scheduled[later]
        too_late = (on(node["start_on"]) + datetime.timedelta(days=30)).isoformat()
        said = await refused(
            agent, "update_node", actor=ACTOR, node_id=later,
            expected_version=node["version"], start_on=too_late,
        )  # fmt: skip
        await drag_bar(page, later, 30, "start")
        refusal = page.locator(".notice.refusal .notice-text", has_text="Work cannot start after")
        await expect(refusal.first).to_be_visible(timeout=SLOW)
        plain(await refusal.first.inner_text(), said)
        assert (await ok(agent, "get_node", node_id=later))["start_on"] == node["start_on"]
        assert w.problems == [] or all("status of 400" in p for p in w.problems), w.problems
