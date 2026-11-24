"""M44 on the board: the `discovered_from` link.

Driven in a real headless browser and compared with what a real MCP client reads (see ./check.sh).
A fifth kind of link says that a piece of work was found while doing another. It is one way and
never makes anything wait, so the board draws it quietly and never as a dependency: it is in the
Add link dialog ("discovered from" and "led to"), in the Links section of both items, in the Graph
(a thin line of its own with no arrow, explained by the Key, and in Around as a neighbour) and in
the Agent view of the item that was found; the Roadmap draws no arrow for it and the Board no
waiting clock. Every test makes an account and a project of its own."""

from typing import Any

import pytest
from mcp import Client
from playwright.async_api import expect

from tests.board_helpers import (
    SLOW,
    bar_of,
    browser_page,
    card,
    go,
    nothing_open,
    on_page,
    open_item,
)
from tests.helpers import connect, make_tree, ok
from tests.world import join_project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

MAP = "#graph-view[data-tab=structure] svg.graph-st-svg"


async def a_pair(
    agent: Client,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Two tasks under one story: the second was found while doing the first."""
    _, story, first = await make_tree(agent)
    found = await ok(
        agent, "create_node", actor="x", kind="task", title="Found on the way", parent=story["ref"]
    )
    return story, first, found


async def test_the_add_link_dialog_offers_it_and_both_ends_name_it_in_the_links_section() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, first, found = await a_pair(agent)
        page = w.page
        await open_item(w, found["ref"], "board")
        await page.locator("#add-link").click()
        dialog = page.locator("dialog[open]")
        await expect(dialog).to_be_visible()
        await nothing_open(page)
        options = await dialog.locator("#link-relation option").all_inner_texts()
        assert options[0] == "Choose how it is linked"
        assert "discovered from" in options and "led to" in options
        # The task that was found says where it was found: it was discovered from the other.
        await dialog.locator("#link-relation").select_option("discovered_from:out")
        await dialog.locator("#link-target").fill(f"T-{first['number']}")
        await dialog.locator("button[type=submit]").click()
        await expect(page.locator("dialog[open]")).to_have_count(0, timeout=SLOW)
        links = (await ok(agent, "list_links", node=found["ref"]))["items"]
        assert [(one["link_type"], one["source"], one["target"]) for one in links] == [
            ("discovered_from", found["ref"], first["ref"])
        ]

        # The Links section of the one that was found: "Discovered from", with the other as a row.
        table = page.locator("#links-table")
        found_row = table.locator(
            f"tr[data-type=discovered_from][data-ref='{first['ref']}'][data-direction=out]"
        )
        await expect(found_row).to_have_count(1, timeout=SLOW)
        await expect(found_row.locator("th.cell-relation")).to_have_text("Discovered from")
        await expect(found_row.locator("th.cell-relation .relation-icon")).to_have_count(1)
        # And of the other, which it led to: the same link read from the other end.
        await open_item(w, first["ref"], "board")
        led = page.locator("#links-table").locator(
            f"tr[data-type=discovered_from][data-ref='{found['ref']}'][data-direction=in]"
        )
        await expect(led).to_have_count(1, timeout=SLOW)
        await expect(led.locator("th.cell-relation")).to_have_text("Led to")

        # Nothing waits for it: neither item is waiting, held or not ready, here and on the Board.
        for ref in (first["ref"], found["ref"]):
            node = await ok(agent, "get_node", node=ref)
            assert node["waiting_on"] == [] and node["status"] == "ready", node
        await w.go("#/board")
        await on_page(page, "board")
        await expect(card(page, found["ref"])).to_be_visible(timeout=SLOW)
        await expect(page.locator(".card-waiting")).to_have_count(0)

        # Removing it from either end is the same operation.
        await open_item(w, first["ref"], "board")
        await page.locator("#links-table button.remove-link").click()
        await expect(page.locator("#links-table")).to_have_count(0, timeout=SLOW)
        assert (await ok(agent, "list_links", node=found["ref"]))["items"] == []
        assert w.problems == []


async def test_the_other_direction_in_the_dialog_and_a_viewer_who_cannot_change_a_link() -> None:
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer", "Vera Viewer")
    async with connect(project.mcp_url, project.key) as agent:
        _, first, found = await a_pair(agent)
        async with browser_page(project.address, token=project.key, name=None) as w:
            page = w.page
            await open_item(w, first["ref"], "board")
            await page.locator("#add-link").click()
            dialog = page.locator("dialog[open]")
            await dialog.locator("#link-relation").select_option("discovered_from:in")
            await dialog.locator("#link-target").fill(f"T-{found['number']}")
            await dialog.locator("button[type=submit]").click()
            await expect(page.locator("dialog[open]")).to_have_count(0, timeout=SLOW)
            (link,) = (await ok(agent, "list_links", node=first["ref"]))["items"]
            # "led to" from the first means the other was discovered from it.
            assert (link["link_type"], link["source"], link["target"]) == (
                "discovered_from",
                found["ref"],
                first["ref"],
            )
            await expect(
                page.locator("#links-table tr[data-type=discovered_from] th.cell-relation")
            ).to_have_text("Led to", timeout=SLOW)
        async with browser_page(project.address, token=None, person=viewer, name=None) as w:
            page = w.page
            await open_item(w, first["ref"], "board")
            await expect(page.locator("#links-table tr[data-type=discovered_from]")).to_have_count(
                1, timeout=SLOW
            )
            await expect(page.locator("#links-table button")).to_have_count(0)
            await expect(page.locator("#add-link")).to_have_count(0)
            assert w.problems == []


async def test_the_agent_view_of_the_item_that_was_found_lists_where_it_came_from() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, first, found = await a_pair(agent)
        await ok(
            agent,
            "add_link",
            source=found["ref"],
            target=first["ref"],
            link_type="discovered_from",
        )
        # The briefing an agent gets when it takes the item names where it was found.
        taken = await ok(agent, "next_work", within_node=found["ref"])
        assert [b["ref"] for b in taken["context"]["discovered_from"]] == [first["ref"]]
        page = w.page
        await open_item(w, found["ref"], "board")
        await page.locator("#item-agent-view").click()
        view = page.locator("dialog#agent-view[open] #agent-view-text")
        await expect(view).to_contain_text("discovered_from:", timeout=SLOW)
        await expect(view).to_contain_text(f"T-{first['number']}")
        await expect(view).to_contain_text("(1)")
        assert w.problems == []
        # The item it came from was not found from anything: its view has no such line.
        await page.keyboard.press("Escape")
        await open_item(w, first["ref"], "board")
        await page.locator("#item-agent-view").click()
        other = page.locator("dialog#agent-view[open] #agent-view-text")
        await expect(other).to_be_visible(timeout=SLOW)
        await expect(other).not_to_contain_text("discovered_from")


async def test_the_graph_draws_it_thin_and_without_an_arrow_and_says_what_it_is_in_the_key() -> (
    None
):
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, first, found = await a_pair(agent)
        third = await ok(
            agent,
            "create_node",
            actor="x",
            kind="task",
            title="Needs the first",
            parent=first["parent"],
        )
        await ok(
            agent,
            "add_link",
            source=found["ref"],
            target=first["ref"],
            link_type="discovered_from",
        )
        await ok(
            agent, "add_link", source=third["ref"], target=first["ref"], link_type="needs_first"
        )
        page = w.page
        await go(page, w.board, "#/graph?links=1")
        await expect(page.locator(MAP)).to_have_attribute("data-resting", "true", timeout=SLOW)
        quiet = page.locator(f"{MAP} .graph-st-link.t-discovered_from")
        dependency = page.locator(f"{MAP} .graph-st-link.t-needs_first")
        await expect(quiet).to_have_count(1)
        await expect(dependency).to_have_count(1)
        # No arrow for what nothing waits for: the arrow belongs to the order of work.
        assert await quiet.get_attribute("marker-end") is None
        assert await dependency.get_attribute("marker-end")
        # It is thinner and quieter than a dependency, in shape and weight as well as colour.
        quiet_style = await quiet.evaluate(
            "e => { const s = getComputedStyle(e);"
            " return [parseFloat(s.strokeWidth), s.strokeDasharray]; }"
        )
        dependency_width = await dependency.evaluate(
            "e => parseFloat(getComputedStyle(e).strokeWidth)"
        )
        assert quiet_style[0] < dependency_width and quiet_style[1] != "none"
        # The live map moves its dots by the tree and not by this link. The picture only has to come
        # to rest and draw the same links.
        await page.locator("#graph-key-button").hover()
        key = page.locator(".hovercard:visible")
        await expect(key).to_contain_text("Discovered from")
        await expect(key).to_contain_text("nothing waits for it")
        await page.keyboard.press("Escape")

        # Looking at the one that was found lights the one it came from, with its own line.
        await go(page, w.board, f"#/graph?focus={found['ref']}")
        await expect(page.locator(MAP)).to_have_attribute("data-resting", "true", timeout=SLOW)
        chosen = page.locator(f"{MAP} .graph-st-link.is-chosen.t-discovered_from")
        await expect(chosen).to_have_count(1)
        assert await chosen.get_attribute("marker-end") is None
        lit = await page.locator(f"{MAP} g.graph-st-node.is-linked").evaluate_all(
            "els => els.map(e => e.dataset.ref)"
        )
        assert lit == [first["ref"]]

        # Around groups it with what is related, and says which way it runs.
        await go(page, w.board, f"#/graph?tab=around&focus={found['ref']}")
        neighbour = page.locator(
            f"#graph-view section[data-group=sim] button.graph-node[data-ref='{first['ref']}']"
        )
        await expect(neighbour).to_be_visible(timeout=SLOW)
        assert w.problems == []


async def test_the_roadmap_draws_an_arrow_for_a_dependency_and_none_for_this() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, story, first = await make_tree(agent)
        dates = {"start_on": "2026-12-01", "target_on": "2026-12-04"}
        later = await ok(
            agent,
            "create_node",
            actor="x",
            kind="task",
            title="Later",
            parent=story["ref"],
            **dates,
        )
        found = await ok(
            agent,
            "create_node",
            actor="x",
            kind="task",
            title="Found",
            parent=story["ref"],
            **dates,
        )
        await ok(
            agent, "update_node", node=first["ref"], expected_version=first["version"], **dates
        )
        await ok(
            agent,
            "add_link",
            source=found["ref"],
            target=first["ref"],
            link_type="discovered_from",
        )
        page = w.page
        await w.open("#/roadmap")
        await on_page(page, "roadmap")
        for ref in (first["ref"], later["ref"], found["ref"]):
            await expect(bar_of(page, ref)).to_be_visible(timeout=SLOW)
        # Only a dependency makes an arrow.
        await expect(page.locator(".rm-deps path.dep")).to_have_count(0)
        await ok(
            agent, "add_link", source=later["ref"], target=first["ref"], link_type="needs_first"
        )
        await expect(page.locator(".rm-deps path.dep")).to_have_count(1, timeout=SLOW)
        arrow = page.locator(".rm-deps path.dep").first
        assert await arrow.get_attribute("data-from") == first["ref"]
        assert await arrow.get_attribute("data-to") == later["ref"]
        assert w.problems == []


async def test_the_filter_help_names_held_work_and_the_link_is_not_held() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, first, found = await a_pair(agent)
        await ok(
            agent, "add_link", source=found["ref"], target=first["ref"], link_type="discovered_from"
        )
        assert (await ok(agent, "list_nodes", query="is:held"))["total"] == 0
        page = w.page
        await w.open("#/board")
        await on_page(page, "board")
        await page.locator("#filter-info").hover()
        help_card = page.locator(".hovercard:visible")
        await expect(help_card).to_contain_text("held", timeout=SLOW)
        await expect(help_card).to_contain_text("a gate or a plan that waits for approval")
        await page.keyboard.press("Escape")
        assert w.problems == []
