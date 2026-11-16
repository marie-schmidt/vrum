"""M17's responsive and accessible proof (SPEC, Proof 4), against the running containers in a
real headless Chromium: at 390, 768, 1280 and 1600 CSS pixels and at 200% zoom (a window half as
wide with twice the pixels), in Light and in Dark, on every screen of the board. The page never
scrolls sideways, what is wider than the window scrolls or pans in a region of its own that a
keyboard can reach, long words wrap or can be read, the selected record and the menu are always
reachable, targets are big enough and do not sit on each other, every control has a name, the
headings and landmarks are in order, the words and the signs are readable (the pixels behind each
word are compared with its colour), the focus can be seen wherever it goes, and the work can be
done with the keyboard alone.

The audits run in the page (tests/board_layout.py), because that is where the sizes and the colours
are. Every screen of a size and a theme is visited once and the findings are read by the tests of
each part of the proof, so a failure names the screen, the size and the theme."""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_helpers import SLOW, board_url, made
from tests.board_layout import (
    BY_NAME,
    SCREENS,
    THEMES,
    Screen,
    focus_ring,
    focused,
    frames,
    layout_page,
    open_address,
    settle,
    start_of_page,
    stops,
    tab_to,
    the_world,
    tour,
    visit,
)
from tests.helpers import connect, ok
from tests.world import make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

screens = pytest.mark.parametrize("screen", SCREENS, ids=[screen.name for screen in SCREENS])
themes = pytest.mark.parametrize("theme", THEMES)


def report(found: dict[str, list[str]]) -> str:
    """The findings, each said once with the screens it was found on."""
    where: dict[str, list[str]] = {}
    for name, lines in found.items():
        for line in lines:
            where.setdefault(line, []).append(name)
    return "\n".join(f"{line}  [on {', '.join(names)}]" for line, names in where.items())


# -- 1. nothing makes the window scroll sideways ----------------------------------------------


@screens
@themes
async def test_no_screen_makes_the_window_scroll_sideways(
    server_url: str, screen: Screen, theme: str
) -> None:
    visited = await tour(server_url, screen, theme)
    assert {"board", "graph-work-map", "roadmap", "panel-decision", "access", "projects"} <= set(
        visited.audits
    ) and {"sign-in", "register", "new-item-dialog"} <= set(visited.audits)
    found: dict[str, list[str]] = {}
    for name, audit in visited.audits.items():
        s = audit.structure
        assert s.theme == theme, f"{name} was drawn in {s.theme}"
        wide = s["overflow"]
        if wide["doc"] > wide["width"] or wide["body"] > wide["width"] or wide["offenders"]:
            found[name] = [
                f"the page is {wide['doc']} (body {wide['body']}) wide in a window of "
                f"{wide['width']}: {wide['offenders'][:6]}"
            ]
    assert not found, report(found)
    assert not visited.problems, visited.problems


# -- 2. what is wider than the window scrolls in a region of its own -------------------------


@screens
@themes
async def test_whatever_scrolls_is_a_named_region_that_the_keyboard_can_reach(
    server_url: str, screen: Screen, theme: str
) -> None:
    visited = await tour(server_url, screen, theme)
    found: dict[str, list[str]] = {}
    for name, audit in visited.audits.items():
        for region in audit.structure["regions"]:
            if not region["reachable"]:
                found.setdefault(name, []).append(f"{region['desc']} scrolls and cannot be reached")
            if region["role"] == "region" and not region["name"]:
                found.setdefault(name, []).append(f"{region['desc']} is a region with no name")
    assert not found, report(found)
    # The Board and the Roadmap are wider than a window up to desktop size; they scroll inside
    # their own regions, which say what they are.
    board = [r for r in visited.audits["board"].structure["regions"] if r["id"] == "board"]
    roadmap = [r for r in visited.audits["roadmap"].structure["regions"] if r["id"] == "rm-scroll"]
    if screen.width <= 1280:
        assert board and board[0]["x"] and board[0]["role"] == "region" and board[0]["tab"]
    assert roadmap and roadmap[0]["x"] and roadmap[0]["role"] == "region" and roadmap[0]["tab"]


# -- 3. long words wrap, scroll, or can be read in full ---------------------------------------


@screens
@themes
async def test_long_words_wrap_or_scroll_and_are_never_clipped_without_a_way_to_read_them(
    server_url: str, screen: Screen, theme: str
) -> None:
    visited = await tour(server_url, screen, theme)
    found: dict[str, list[str]] = {}
    for name, audit in visited.audits.items():
        for line in audit.structure["spills"]:
            found.setdefault(name, []).append(f"text pokes out of its box: {line}")
        for line in audit.structure["clipped"]:
            found.setdefault(name, []).append(f"text is cut off with no full text to read: {line}")
    assert not found, report(found)


# -- 4. targets are big enough, and do not sit on each other -----------------------------------


@screens
@themes
async def test_targets_are_24_pixels_or_have_room_and_controls_do_not_overlap(
    server_url: str, screen: Screen, theme: str
) -> None:
    visited = await tour(server_url, screen, theme)
    found: dict[str, list[str]] = {}
    for name, audit in visited.audits.items():
        for small in audit.structure["small"]:
            found.setdefault(name, []).append(f"target {small['w']}x{small['h']}: {small['desc']}")
        for first, second, area in audit.structure["overlaps"]:
            found.setdefault(name, []).append(f"{first} overlaps {second} by {area} px")
    assert not found, report(found)


# -- 5. names, icons, headings and landmarks ---------------------------------------------------


@screens
@themes
async def test_every_control_has_a_name_icons_are_hidden_and_headings_and_landmarks_are_in_order(
    server_url: str, screen: Screen, theme: str
) -> None:
    visited = await tour(server_url, screen, theme)
    found: dict[str, list[str]] = {}
    for name, audit in visited.audits.items():
        s = audit.structure
        for line in s["unnamed"]:
            found.setdefault(name, []).append(f"no accessible name: {line}")
        for line in s["icons"]:
            found.setdefault(name, []).append(f"icon neither hidden nor named: {line}")
        for line in s["glyphs"]:
            found.setdefault(name, []).append(f"a letter of a font used as an icon: {line}")
        for line in s["skips"]:
            found.setdefault(name, []).append(f"heading level skipped: {line}")
        for line in s["unlabelled"]:
            found.setdefault(name, []).append(f"landmark with no name: {line}")
        if s["h1"] != 1:
            found.setdefault(name, []).append(f"{s['h1']} h1 headings")
        if s["mains"] != 1:
            found.setdefault(name, []).append(f"{s['mains']} main landmarks")
        if name not in {"sign-in", "register", "projects", "no-such-project"} and s["navs"] < 2:
            found.setdefault(name, []).append(f"{s['navs']} navigation landmarks")
    assert not found, report(found)


# -- 6. contrast -------------------------------------------------------------------------------


@screens
@themes
async def test_every_word_icon_and_field_border_has_the_contrast_it_needs(
    server_url: str, screen: Screen, theme: str
) -> None:
    visited = await tour(server_url, screen, theme)
    found: dict[str, list[str]] = {}
    for name, audit in visited.audits.items():
        assert audit.contrast.checked >= 10, (
            f"{name}: only {audit.contrast.checked} things looked at"
        )
        if audit.contrast.found:
            found[name] = audit.contrast.lines()
    assert not found, report(found)


# -- 7. the selected record and the menu are reachable on every width --------------------------


async def goto(w: Any, address: str, ready: str) -> None:
    """Change the address (the way a link does) and wait for the screen it names."""
    await w.page.evaluate("(hash) => { location.hash = hash; }", address)
    await w.page.wait_for_selector(ready, state="attached", timeout=SLOW)
    await settle(w.page)


@screens
async def test_the_menu_is_reachable_through_its_button_on_a_narrow_window(
    server_url: str, screen: Screen
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, "light") as w:
        page = w.page
        await open_address(w, "#/board")
        await settle(page)
        toggle = page.locator("#sidebar-toggle")
        nav = page.locator("#main-nav")
        if not screen.narrow:
            await expect(toggle).to_be_hidden()
            await expect(nav).to_be_visible()
            for place in ("board", "graph", "roadmap"):
                await expect(nav.locator(f"a[data-page={place}]")).to_be_visible()
            return
        await expect(nav).to_be_hidden()
        await expect(toggle).to_be_visible()
        box = await toggle.bounding_box()
        assert box is not None and box["width"] >= 44 and box["height"] >= 44, box
        assert await toggle.get_attribute("aria-expanded") == "false"
        await toggle.click()
        await expect(nav).to_be_visible()
        await frames(page)
        assert await toggle.get_attribute("aria-expanded") == "true"
        for place in ("board", "graph", "roadmap"):
            link = nav.locator(f"a[data-page={place}]")
            await expect(link).to_be_visible()
            link_box = await link.bounding_box()
            assert link_box is not None and link_box["height"] >= 44, (place, link_box)
            assert link_box["x"] >= 0 and link_box["x"] + link_box["width"] <= screen.width
        await expect(page.locator("#sidebar-close")).to_be_visible()
        for reachable in ("#activity-open", "#settings-menu", "#sign-out", "#project-switcher"):
            await expect(page.locator(reachable)).to_be_visible()
        # Escape closes it and gives the focus back to the button.
        await page.keyboard.press("Escape")
        await expect(nav).to_be_hidden()
        assert (await focused(page)).startswith("button#sidebar-toggle")
        # Choosing a place closes it and shows that place.
        await toggle.click()
        await nav.locator("a[data-page=roadmap]").click()
        await expect(page.locator(".roadmap-page")).to_be_visible()
        await expect(nav).to_be_hidden()


RECORDS = [
    ("epic", "E3", ".lane[data-epic='{id}'] .lane-title", "#/board"),
    ("story", "S5", ".card[data-id='{id}']", "#/board"),
    ("task", "D1", ".card[data-id='{id}']", "#/board"),
    (
        "requirement",
        "R3",
        "#graph-list [data-key='rule:{id}'] .gv-open",
        "#/graph?view=context&as=list",
    ),
    (
        "decision",
        "X2",
        "#graph-list [data-key='decision:{id}'] .record-title",
        "#/graph?view=context&as=list",
    ),
    (
        "fact",
        "Y2",
        "#graph-list [data-key='fact:{id}'] .record-title",
        "#/graph?view=context&as=list",
    ),
]


@screens
@themes
async def test_every_kind_of_record_opens_in_a_panel_that_is_whole_and_can_be_closed(
    server_url: str, screen: Screen, theme: str
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, theme) as w:
        page = w.page
        await open_address(w, "#/board")
        await settle(page)
        for kind, name, opener, address in RECORDS:
            await goto(w, address, ".card[data-id], #graph-list .gv-record")
            await page.locator(opener.format(id=world[name])).click()
            panel = page.locator("#panel")
            await expect(panel.locator(".detail")).to_be_visible(timeout=SLOW)
            await settle(page)
            where = f"{kind} at {screen.name} in {theme}"
            box = await panel.bounding_box()
            assert box is not None, where
            assert box["x"] >= -0.5 and box["x"] + box["width"] <= screen.width + 0.5, (where, box)
            if screen.phone:
                assert box["width"] >= screen.width - 1, (
                    where,
                    "a sheet over the whole window",
                    box,
                )
            close = panel.locator(".detail-head .close")
            await expect(close).to_be_visible()
            shut = await close.bounding_box()
            assert shut is not None and shut["width"] >= 24 and shut["height"] >= 24, (where, shut)
            assert (
                shut["x"] >= box["x"] - 0.5
                and shut["x"] + shut["width"] <= box["x"] + box["width"] + 0.5
            )
            assert shut["y"] >= 0 and shut["y"] + shut["height"] <= screen.height, (where, shut)
            # The whole title is in the panel, wrapped inside it, and so are its sections.
            fits = await panel.evaluate(
                """(p) => {
                    const title = p.querySelector('.detail-title');
                    const parts = [...p.querySelectorAll('.part')];
                    p.scrollTop = p.scrollHeight;
                    const end = p.getBoundingClientRect();
                    const rest = parts.length ? parts[parts.length - 1] : null;
                    const last = rest ? rest.getBoundingClientRect() : null;
                    const reached = !last || last.bottom <= end.bottom + 1;
                    p.scrollTop = 0;
                    return {
                      title: title.textContent,
                      wideTitle: title.scrollWidth > title.clientWidth + 1,
                      tallTitle: title.scrollHeight > title.clientHeight + 1,
                      sideways: p.scrollWidth > p.clientWidth + 1, reached,
                      page: document.documentElement.scrollWidth <= innerWidth };
                }"""
            )
            assert not fits["wideTitle"] and not fits["tallTitle"], (where, fits)
            assert not fits["sideways"] and fits["reached"] and fits["page"], (where, fits)
            await close.click()
            await expect(panel).to_be_hidden()
            assert "sel=" not in page.url


# -- 8. what is wider than the window scrolls or pans in its own region ------------------------


def view_of(transform: str) -> tuple[float, float, float]:
    """The map's pan and zoom, from the transform of its viewport."""
    found = re.fullmatch(r"translate\((-?[\d.]+) (-?[\d.]+)\) scale\(([\d.]+)\)", transform.strip())
    assert found, transform
    return float(found.group(1)), float(found.group(2)), float(found.group(3))


async def is_sideways_free(page: Page) -> bool:
    return bool(
        await page.evaluate(
            "() => document.documentElement.scrollWidth <= innerWidth && window.scrollX === 0"
        )
    )


@screens
async def test_the_board_the_map_the_timeline_and_the_panel_scroll_or_pan_in_their_own_regions(
    server_url: str, screen: Screen
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, "light") as w:
        page = w.page
        await open_address(w, "#/board")
        await settle(page)

        # The Board: its columns scroll inside the region that is named and can be focused.
        board = page.locator("#board")
        assert await board.get_attribute("role") == "region"
        assert await board.get_attribute("tabindex") == "0"
        assert (await board.get_attribute("aria-label") or "").strip()
        room = await board.evaluate("(b) => [b.scrollWidth, b.clientWidth]")
        hint = page.locator(".scroll-hint")
        if room[0] > room[1] + 1:
            await expect(hint).to_be_visible()
            assert await page.locator(".board-frame").get_attribute("data-overflow") == "true"
            await board.focus()
            await page.keyboard.press("ArrowRight")
            await page.wait_for_function("() => document.getElementById('board').scrollLeft > 0")
            await expect(page.locator(".board-frame")).to_have_attribute("data-left", "true")
            await page.keyboard.press("ArrowLeft")
            await page.wait_for_function("() => document.getElementById('board').scrollLeft === 0")
            await page.get_by_role("button", name="Scroll the columns right").click()
            await page.wait_for_function("() => document.getElementById('board').scrollLeft > 0")
            await page.get_by_role("button", name="Scroll the columns left").click()
            await page.wait_for_function("() => document.getElementById('board').scrollLeft === 0")
            await expect(page.locator(".board-frame")).to_have_attribute("data-right", "true")
        else:
            await expect(hint).to_be_hidden()
        assert await is_sideways_free(page)

        # The map: the keyboard pans and zooms it, and 0 brings it back.
        await goto(w, f"#/graph?view=work&as=map&epic={world['E1']}", "svg[data-ready=true]")
        canvas = page.locator("#graph-canvas")
        assert await canvas.get_attribute("role") == "region"
        assert await canvas.get_attribute("tabindex") == "0"
        assert (await canvas.get_attribute("aria-label") or "").strip()
        await canvas.focus()
        view = page.locator("svg#graph-svg #viewport")
        start = view_of(await view.get_attribute("transform") or "")
        await page.keyboard.press("ArrowLeft")
        moved = view_of(await view.get_attribute("transform") or "")
        assert moved[0] - start[0] == pytest.approx(60, abs=0.2) and moved[1] == start[1]
        await page.keyboard.press("ArrowDown")
        moved = view_of(await view.get_attribute("transform") or "")
        assert moved[1] - start[1] == pytest.approx(-60, abs=0.2)
        await page.keyboard.press("Shift+ArrowRight")
        moved = view_of(await view.get_attribute("transform") or "")
        assert moved[0] - start[0] == pytest.approx(60 - 240, abs=0.5)
        await page.keyboard.press("+")
        zoomed = view_of(await view.get_attribute("transform") or "")
        assert zoomed[2] == pytest.approx(start[2] * 1.3, rel=0.01)
        shown = await page.locator("#zoom-level").inner_text()
        assert abs(int(shown.rstrip("%")) - zoomed[2] * 100) <= 1, shown
        await page.keyboard.press("0")
        back = view_of(await view.get_attribute("transform") or "")
        assert back == pytest.approx(start, abs=0.5)
        assert await is_sideways_free(page)

        # The timeline: it scrolls sideways and down inside its own region.
        await goto(w, "#/roadmap", ".rm-bar")
        line = page.locator("#rm-scroll")
        assert await line.get_attribute("role") == "region"
        assert await line.get_attribute("tabindex") == "0"
        assert (await line.get_attribute("aria-label") or "").strip()
        wide = await line.evaluate(
            "(e) => [e.scrollWidth, e.clientWidth, e.scrollHeight, e.clientHeight]"
        )
        assert wide[0] > wide[1], "the timeline is wider than its region"
        earlier = page.get_by_role("button", name="Scroll the timeline to earlier days")
        later = page.get_by_role("button", name="Scroll the timeline to later days")
        before = await line.evaluate("(e) => e.scrollLeft")
        at_end = await line.evaluate("(e) => e.scrollLeft + e.clientWidth >= e.scrollWidth - 1")
        # Each button moves the timeline in its own direction, from wherever it was left.
        await (earlier if at_end else later).click()
        if at_end:
            await page.wait_for_function(
                "(was) => document.getElementById('rm-scroll').scrollLeft < was", arg=before
            )
        else:
            await page.wait_for_function(
                "(was) => document.getElementById('rm-scroll').scrollLeft > was", arg=before
            )
        await page.get_by_role("button", name="Today").click()
        if wide[2] > wide[3] + 1:
            await line.focus()
            await page.keyboard.press("PageDown")
            await page.wait_for_function("() => document.getElementById('rm-scroll').scrollTop > 0")
        assert await is_sideways_free(page)

        # The panel: a long record is read by scrolling the panel, not the page.
        await goto(w, f"#/board?sel=decision:{world['X2']}", "#panel .detail")
        panel = page.locator("#panel")
        room = await panel.evaluate("(p) => [p.scrollHeight, p.clientHeight]")
        if room[0] > room[1] + 1:
            box = await panel.bounding_box()
            assert box is not None
            await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            await page.mouse.wheel(0, 300)
            await page.wait_for_function("() => document.getElementById('panel').scrollTop > 0")
            assert await page.evaluate("() => window.scrollY") == 0 or screen.phone
        assert await is_sideways_free(page)


# -- 9. the work is done with the keyboard alone -----------------------------------------------


@asynccontextmanager
async def own_project(server_url: str) -> AsyncIterator[tuple[Client, str]]:
    """A project of its own with a task in it, so that a change made by keyboard is read back
    through MCP without disturbing what other tests look at."""
    project = make_project(register(), "Keyboard project")
    async with connect(server_url, project.key) as client:
        yield client, board_url(server_url, project)


async def read(client: Client, node: int) -> dict[str, Any]:
    return await ok(client, "get_node", node_id=node)


async def settled_on(client: Client, node: int, **wanted: Any) -> dict[str, Any]:
    """The node as MCP reads it once it has the wanted fields (a change by key is sent after a
    short while)."""
    for _ in range(60):
        found = await read(client, node)
        if all(found[name] == value for name, value in wanted.items()):
            return found
        await anyio.sleep(0.25)
    raise AssertionError(f"#{node} never became {wanted}: {await read(client, node)}")


@screens
async def test_the_graph_is_reached_and_its_view_and_presentation_chosen_with_the_keyboard(
    server_url: str, screen: Screen
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, "light") as w:
        page = w.page
        await open_address(w, "#/board")
        await settle(page)
        await start_of_page(page)
        if screen.narrow:
            await tab_to(page, "#sidebar-toggle")
            await page.keyboard.press("Enter")
            await expect(page.locator("#main-nav")).to_be_visible()
            assert (await focused(page)).startswith("button#sidebar-close")
        await tab_to(page, "#main-nav a[data-page=graph]")
        await page.keyboard.press("Enter")
        await expect(page.locator("#graph-page")).to_be_visible()
        assert "#/graph" in page.url
        await expect(page.locator("#main-nav a[data-page=graph]")).to_have_attribute(
            "aria-current", "page"
        )
        await expect(page.locator("svg#graph-svg")).to_have_attribute("data-ready", "true")
        # Work, Context and Everything are buttons in the order of Tab; Space chooses one.
        await tab_to(page, "#graph-view-context")
        await page.keyboard.press("Space")
        await expect(page.locator("#graph-view-context")).to_have_attribute("aria-pressed", "true")
        await expect(page.locator("#graph-page")).to_have_attribute("data-view", "context")
        await tab_to(page, "#graph-as-list")
        await page.keyboard.press("Enter")
        await expect(page.locator("#graph-page")).to_have_attribute("data-as", "list")
        await expect(page.locator("#graph-list")).to_be_visible()
        await expect(page.locator("#graph-map")).to_be_hidden()
        assert (await focused(page)).startswith("button#graph-as-list")
        # And back, with Shift+Tab to the buttons the other way.
        await tab_to(page, "#graph-as-map", back=True)
        await page.keyboard.press("Enter")
        await expect(page.locator("#graph-page")).to_have_attribute("data-as", "map")
        await expect(page.locator("svg#graph-svg")).to_have_attribute("data-ready", "true")
        # A node of the map is chosen with Enter, and opens the shared panel.
        await tab_to(page, "svg#graph-svg .dot")
        await page.keyboard.press("Enter")
        await expect(page.locator("#panel .detail")).to_be_visible()


@screens
async def test_search_is_opened_typed_and_a_result_opened_and_closed_with_the_keyboard(
    server_url: str, screen: Screen
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, "light") as w:
        page = w.page
        await open_address(w, "#/board")
        await settle(page)
        await start_of_page(page)
        await tab_to(page, "#global-search")
        await page.keyboard.type("card")
        await page.keyboard.press("Enter")
        drawer = page.locator("#drawer")
        await expect(drawer).to_be_visible()
        await expect(drawer.locator("#search-results li.result").first).to_be_visible(timeout=SLOW)
        await tab_to(page, "#search-results li.result a.result-title")
        link_text = (await page.evaluate("document.activeElement.textContent")).strip()
        await page.keyboard.press("Enter")
        await expect(page.locator("#panel .detail")).to_be_visible()
        assert link_text.split(" ", 1)[1][:8] in await page.locator("#card-title").inner_text()
        # The focus went into the panel, and its close button, the next stop, gives it back.
        assert await page.evaluate(
            "document.getElementById('panel').contains(document.activeElement)"
        )
        await tab_to(page, "#panel .detail-head .close")
        await page.keyboard.press("Enter")
        await expect(page.locator("#panel")).to_be_hidden()
        assert not (await focused(page)).startswith("body"), (
            "closing the panel opened from a search result lets the focus fall to the page"
        )
        # Escape closes the drawer from inside it and gives the focus back to where it came from.
        await page.keyboard.press("Escape")
        await expect(drawer).to_be_hidden()
        assert (await focused(page)).split()[0].split("#")[0] in {"input", "button"}


@screens
async def test_a_title_is_edited_in_the_panel_and_saved_with_the_keyboard(
    server_url: str, screen: Screen
) -> None:
    async with own_project(server_url) as (client, board):
        epic = await made(client, "epic", "Keys")
        story = await made(client, "story", "Keyed story", epic)
        task = await made(client, "task", "Old title", story)
        async with layout_page(board, screen, "light") as w:
            page = w.page
            await open_address(w, "#/board")
            await expect(page.locator(f".card[data-id='{task}']")).to_be_visible(timeout=SLOW)
            await start_of_page(page)
            await tab_to(page, f".card[data-id='{task}']")
            await page.keyboard.press("Enter")
            await expect(page.locator("#card-title")).to_contain_text("Old title", timeout=SLOW)
            await tab_to(page, "#edit-title")
            await page.keyboard.press("ControlOrMeta+a")
            await page.keyboard.type("A new title from the keyboard")
            await tab_to(page, "#panel form.edit button[type=submit]")
            await page.keyboard.press("Enter")
            await settled_on(client, task, title="A new title from the keyboard")
            await expect(page.locator("#card-title")).to_contain_text(
                "A new title from the keyboard"
            )
            await expect(page.locator(f".card[data-id='{task}'] .card-title")).to_have_text(
                "A new title from the keyboard"
            )


@screens
async def test_a_dialog_is_opened_and_cancelled_with_the_keyboard_and_the_focus_comes_back(
    server_url: str, screen: Screen
) -> None:
    async with (
        own_project(server_url) as (client, board),
        layout_page(board, screen, "light") as w,
    ):
        page = w.page
        await open_address(w, "#/board")
        await settle(page)
        await start_of_page(page)
        await tab_to(page, "#new-item")
        await page.keyboard.press("Enter")
        dialog = page.locator("dialog.modal[open]")
        await expect(dialog).to_be_visible()
        assert (await focused(page)).startswith("input#new-title")
        await page.keyboard.type("A draft that is never made")
        # The page behind a modal dialog is out of reach: Tab goes round inside it (or out to
        # the browser, which is the body), and never to the board.
        for _ in range(24):
            await page.keyboard.press("Tab")
            assert await page.evaluate(
                "() => !!document.activeElement.closest('dialog')"
                " || document.activeElement === document.body"
            ), await focused(page)
        await page.keyboard.press("Escape")
        await expect(dialog).to_be_hidden()
        assert (await focused(page)).startswith("button#new-item")
        # Again, and this time through the Cancel button.
        await page.keyboard.press("Enter")
        await expect(dialog).to_be_visible()
        await tab_to(page, "dialog.modal .actions button:not(.primary)")
        assert (await page.evaluate("document.activeElement.textContent")).strip() == "Cancel"
        await page.keyboard.press("Enter")
        await expect(dialog).to_be_hidden()
        assert (await focused(page)).startswith("button#new-item")
        page_of = await ok(client, "list_nodes", limit=1)
        assert page_of["total"] == 0


@screens
async def test_a_status_is_changed_through_the_move_menu_with_the_keyboard(
    server_url: str, screen: Screen
) -> None:
    async with own_project(server_url) as (client, board):
        epic = await made(client, "epic", "Keys")
        story = await made(client, "story", "Keyed story", epic)
        task = await made(client, "task", "Move me", story)
        async with layout_page(board, screen, "light") as w:
            page = w.page
            await open_address(w, "#/board")
            await expect(page.locator(f".card[data-id='{task}']")).to_be_visible(timeout=SLOW)
            await start_of_page(page)
            await tab_to(page, f".card[data-id='{task}'] button.move")
            await page.keyboard.press("Enter")
            menu = page.locator(".move-menu")
            await expect(menu).to_be_visible()
            assert await page.evaluate("document.activeElement.getAttribute('role')") == "menuitem"
            for _ in range(6):
                if await page.evaluate("document.activeElement.dataset.status") == "in_progress":
                    break
                await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            await settled_on(client, task, status="in_progress")
            await expect(
                page.locator(f".cell[data-status='in_progress'] .card[data-id='{task}']")
            ).to_be_visible(timeout=SLOW)
            # The focus is still on the Move button after the board is drawn again; a menu opened
            # there and left with Escape changes nothing and gives the focus back.
            assert (await focused(page)).startswith(f"button Move #{task}"), await focused(page)
            await page.keyboard.press("Enter")
            await expect(menu).to_be_visible()
            await page.keyboard.press("Escape")
            await expect(menu).to_be_hidden()
            assert (await focused(page)).startswith(f"button Move #{task}")
            assert (await read(client, task))["status"] == "in_progress"


async def type_date(page: Page, iso: str) -> None:
    """Type a day into the date field that has the focus, with the keyboard, in the order the
    browser writes a date (year first or month first: the field says which by what it makes).
    A year takes more than four digits, so Tab moves on from it to the next part."""
    year, month, day = iso.split("-")
    for order in ((year, month, day), (month, day, year)):
        for at, part in enumerate(order):
            await page.keyboard.type(part)
            if part == year and at < 2:
                await page.keyboard.press("Tab")
        if await page.evaluate("() => document.activeElement.value") == iso:
            return
        await page.evaluate(
            "() => { const e = document.activeElement; e.value = ''; e.blur(); e.focus(); }"
        )
    raise AssertionError(f"a date field would not take {iso} typed: {await focused(page)}")


@screens
async def test_dates_are_scheduled_through_the_tray_the_panel_and_the_bar_without_dragging(
    server_url: str, screen: Screen
) -> None:
    async with own_project(server_url) as (client, board):
        epic = await made(client, "epic", "Keys")
        story = await made(client, "story", "Keyed story", epic)
        tray = await made(client, "task", "Dated in the tray", story)
        panel = await made(client, "task", "Dated in the panel", story)
        async with layout_page(board, screen, "light") as w:
            page = w.page
            # The tray of the Roadmap: two dates typed into their fields, then Set dates.
            await open_address(w, "#/roadmap")
            await expect(page.locator(f".tray-item[data-id='{tray}']")).to_be_visible(timeout=SLOW)
            await start_of_page(page)
            await tab_to(page, f"input[aria-label='Start date of #{tray}']")
            await type_date(page, "2027-12-01")
            await tab_to(page, f"input[aria-label='Target date of #{tray}']")
            await type_date(page, "2027-12-15")
            await tab_to(page, f".tray-item[data-id='{tray}'] button[type=submit]")
            await page.keyboard.press("Enter")
            await settled_on(client, tray, start_on="2027-12-01", target_on="2027-12-15")
            bar = f".rm-bar[data-id='{tray}']"
            await expect(page.locator(bar)).to_be_visible(timeout=SLOW)
            # The bar moves with the arrow keys (a day each; Shift for a week), and is sent once.
            await start_of_page(page)
            await tab_to(page, bar, limit=400)
            await page.keyboard.press("ArrowRight")
            await page.keyboard.press("ArrowRight")
            await page.keyboard.press("Shift+ArrowRight")
            await settled_on(client, tray, start_on="2027-12-10", target_on="2027-12-24")
            await page.keyboard.press("Alt+ArrowLeft")
            await settled_on(client, tray, start_on="2027-12-09", target_on="2027-12-24")
            # The panel: its date fields.
            await open_address(w, f"#/board?sel=node:{panel}")
            await expect(page.locator("#card-title")).to_contain_text(
                "Dated in the panel", timeout=SLOW
            )
            await tab_to(page, "#edit-start")
            await type_date(page, "2028-01-02")
            await tab_to(page, "#edit-target")
            await type_date(page, "2028-01-15")
            await tab_to(page, "#panel form.edit button[type=submit]")
            await page.keyboard.press("Enter")
            await settled_on(client, panel, start_on="2028-01-02", target_on="2028-01-15")


# -- 10. the focus can be seen wherever it goes ------------------------------------------------

FOCUS_SCREENS = [BY_NAME["1280"], BY_NAME["768"], BY_NAME["390"], BY_NAME["zoom200"]]


@pytest.mark.parametrize("screen", FOCUS_SCREENS, ids=[screen.name for screen in FOCUS_SCREENS])
@themes
async def test_the_focus_is_visible_on_every_control_the_keyboard_reaches(
    server_url: str, screen: Screen, theme: str
) -> None:
    world = await the_world(server_url)
    names = [
        "board",
        "graph-work-map",
        "graph-everything-list",
        "roadmap",
        "panel-task",
        "panel-decision",
        "search",
        "activity",
        "access",
        "tools",
        "status",
    ]
    async with layout_page(world.board, screen, theme) as w:
        page = w.page
        await open_address(w)
        found: list[str] = []
        reached: set[str] = set()

        async def walk(name: str, presses: int) -> None:
            for _ in range(presses):
                await page.keyboard.press("Tab")
                ring = await focus_ring(page)
                if ring["skip"]:
                    continue
                reached.add(ring["desc"])
                if not ring["ok"]:
                    found.append(f"{name}: {ring['desc']} shows {ring['why']}")

        for stop in [s for s in stops(world) if s.name in names]:
            await visit(w, stop)
            await start_of_page(page)
            await walk(stop.name, 45)
        # The menu behind its button on a narrow window, and a dialog.
        await visit(w, stops(world)[0])
        await start_of_page(page)
        if screen.narrow:
            await page.locator("#sidebar-toggle").click()
            await frames(page)
            await walk("the sidebar", 30)
            await page.locator("#sidebar-close").click()
            await frames(page)
        await page.locator("#new-item").click()
        await page.wait_for_selector("dialog.modal[open]")
        await walk("the New item dialog", 12)
        assert len(reached) > 60, f"the keyboard reached only {len(reached)} controls"
        assert not found, "\n".join(sorted(set(found)))


# -- 11. touch targets and the map's nodes -------------------------------------------------------


async def test_on_a_touch_screen_the_controls_that_ask_for_it_are_44_pixels(
    server_url: str,
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, BY_NAME["390"], "light", touch=True) as w:
        page = w.page
        await open_address(w, "#/board")
        await settle(page)
        assert await page.evaluate("() => matchMedia('(pointer: coarse)').matches")
        small: list[str] = []
        for selector in (
            "#sidebar-toggle",
            "button.jump",
            ".global-search",
            ".card .move",
            ".lane-toggle",
            ".add-story",
            ".attention-item",
            "form.filter button",
            ".scroll-hint button",
        ):
            places = page.locator(selector)
            assert await places.count(), selector
            for at in range(min(await places.count(), 4)):
                if not await places.nth(at).is_visible():
                    continue
                box = await places.nth(at).bounding_box()
                if box is None or min(box["width"], box["height"]) < 43.5:
                    shown = box and f"{box['width']:.0f}x{box['height']:.0f}"
                    small.append(f"{selector} is {shown}, and a touch target is 44x44")
        assert not small, "\n".join(sorted(set(small)))
        await page.locator("#sidebar-toggle").click()
        await frames(page)
        for selector in ("#sidebar-close", "#main-nav a", "#activity-open", "#settings-menu"):
            places = page.locator(selector)
            for at in range(await places.count()):
                box = await places.nth(at).bounding_box()
                assert box is not None and box["height"] >= 43.5, (selector, box)


@pytest.mark.parametrize("screen", [BY_NAME["390"], BY_NAME["1280"]], ids=["390", "1280"])
async def test_the_nodes_of_the_map_are_24_pixels_or_more_at_100_percent_zoom(
    server_url: str, screen: Screen
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, "light") as w:
        page = w.page
        await open_address(w, "#/board")
        small: list[str] = []
        for address in (
            f"#/graph?view=everything&as=map&epic={world['E1']}",
            "#/graph?view=everything&as=map",
        ):
            await goto(w, address, "svg[data-ready=true]")
            await page.locator("#graph-canvas").focus()
            for _ in range(24):
                if await page.evaluate(
                    "() => parseInt(document.getElementById('zoom-level').textContent, 10) >= 100"
                ):
                    break
                await page.keyboard.press("+")
            nodes = page.locator("svg#graph-svg .dot .shape")
            for at in range(await nodes.count()):
                box = await nodes.nth(at).bounding_box()
                assert box is not None
                if min(box["width"], box["height"]) < 23.5:
                    small.append(f"{address} node {at}: {box['width']:.0f}x{box['height']:.0f}")
        assert not small, small[:6]


# -- 12. reduced motion ---------------------------------------------------------------------------

MOTION = """
() => {
  const seconds = (text) => text.split(',').map((part) => {
    const v = parseFloat(part);
    return part.trim().endsWith('ms') ? v / 1000 : v;
  });
  const slow = [];
  const look = (e, pseudo) => {
    const s = getComputedStyle(e, pseudo);
    const longest = Math.max(...seconds(s.transitionDuration), ...seconds(s.animationDuration));
    const moves = s.animationName.split(',').some((name) => name.trim() !== 'none');
    const endless = moves && s.animationIterationCount.split(',').some(
      (count) => count.trim() === 'infinite' || parseFloat(count) > 1);
    if (longest > 0.002 || endless) {
      const id = e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (pseudo || '');
      slow.push(id + ' ' + s.transitionDuration + ' / ' + s.animationDuration);
    }
  };
  for (const e of document.querySelectorAll('*')) {
    look(e, null); look(e, '::before'); look(e, '::after');
  }
  const spinner = document.createElement('p');
  spinner.className = 'loading';
  spinner.textContent = 'Reading';
  document.getElementById('page').append(spinner);
  const running = document.getAnimations({ subtree: true }).map((a) => {
    const t = a.effect.getComputedTiming();
    return [a.animationName || a.transitionProperty, t.duration, t.iterations];
  }).filter(([, duration, count]) => duration > 2 || count > 1);
  spinner.remove();
  return { slow: [...new Set(slow)].slice(0, 12), running,
    reduce: matchMedia('(prefers-reduced-motion: reduce)').matches,
    scroll: getComputedStyle(document.documentElement).scrollBehavior };
}
"""


@themes
async def test_with_reduced_motion_no_animation_or_transition_runs_and_the_spinner_does_not_spin(
    server_url: str, theme: str
) -> None:
    world = await the_world(server_url)
    names = ["board", "graph-work-map", "roadmap", "panel-task", "search", "activity", "access"]
    async with layout_page(world.board, BY_NAME["1280"], theme, reduced_motion=True) as w:
        page = w.page
        await open_address(w)
        found: dict[str, list[str]] = {}
        for stop in [s for s in stops(world) if s.name in names]:
            await visit(w, stop)
            seen = await page.evaluate(MOTION)
            assert seen["reduce"] and seen["scroll"] == "auto", seen
            if seen["slow"] or seen["running"]:
                found[stop.name] = [*seen["slow"], *map(str, seen["running"])]
        assert not found, report(found)
        # Scrolling by the Board's own buttons is a jump, not a slide.
        await visit(w, stops(world)[0])
        jumped = await page.evaluate(
            """() => {
                const board = document.getElementById('board');
                [...document.querySelectorAll('.scroll-hint button')].pop().click();
                return board.scrollLeft;
            }"""
        )
        assert jumped > 0, "the columns slid instead of jumping"
    # The same check, with motion allowed, sees the spinner turn: so the check can fail.
    async with layout_page(world.board, BY_NAME["1280"], theme) as w:
        await open_address(w, "#/board")
        seen = await w.page.evaluate(MOTION)
        assert not seen["reduce"]
        assert seen["running"] and seen["slow"], seen
