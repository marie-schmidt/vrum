"""M17's responsive and accessible proof (SPEC, Proof 4), carried through M20 (SPEC M20, Proof 7),
against the running containers in a real headless Chromium: at 390, 768, 1280 and 1600 CSS pixels
and at 200% zoom (a window half as wide with twice the pixels), in Light and in Dark, on every
screen of the board: the three views, the page of each kind of record, Search, Activity, the
coverage report, the settings pages and the sidebar's tree. Nothing opens beside a page. The page
never scrolls sideways, what is wider than the window scrolls or pans in a region of its own that a
keyboard can reach, long words wrap or can be read, the menu, its tree and the page of every record
are always reachable, targets are big enough and do not sit on each other, every control has a
name, the headings and landmarks are in order, the words and the signs are readable (the pixels
behind each word are compared with its colour), the focus can be seen wherever it goes, and the
work can be done with the keyboard alone.

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

from tests.board_helpers import (
    SLOW,
    board_url,
    crumbs,
    item_page,
    item_rows,
    made,
    no_panel_or_drawer,
    pager,
    ref_slug,
    tree_expand,
)
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
from tests.helpers import code_of, connect, ok
from tests.world import make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

screens = pytest.mark.parametrize("screen", SCREENS, ids=[screen.name for screen in SCREENS])
themes = pytest.mark.parametrize("theme", THEMES)

# Every screen the tour must have looked at, whatever else it adds: the three views, the page of
# each kind of record, the coverage report, Search with its second tab, Activity, the settings
# pages, the pages without a project, and the states (the sidebar's tree opened, a dialog) of one.
TOURED = {
    "board",
    "graph-work-map",
    "graph-around",
    "graph-order",
    "lists-work",
    "graph-context-list",
    "roadmap",
    "item-epic",
    "item-story",
    "item-task",
    "item-requirement",
    "item-decision",
    "item-fact",
    "coverage",
    "search",
    "search-duplicates",
    "activity",
    "access",
    "tools",
    "status",
    "projects",
    "sign-in",
    "register",
    "new-item-dialog",
    "navigator-open",
}


def page_address(ref: str, came_from: str = "board") -> str:
    """The address of the page of one epic, story, task, criterion, decision or fact, reached from
    one of the pages a person can be in."""
    return f"#/item?item={ref}&from={came_from}"


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
    world = await the_world(server_url)
    visited = await tour(server_url, screen, theme)
    assert set(visited.audits) >= TOURED, TOURED - set(visited.audits)
    assert {stop.name for stop in stops(world)} <= set(visited.audits)
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
    # So do the tables of a page: the table of what is under an epic and the tables of what
    # concerns it are boxes of their own (named, and in the order of Tab, which the loop above
    # checked for every one that scrolls). A window too narrow for the columns scrolls them; a wide
    # one shows them whole.
    for name in ("item-epic", "item-story", "item-decision", "item-fact"):
        tables = [
            region
            for region in visited.audits[name].structure["regions"]
            if region["id"] == "item-region" or "record-region" in region["desc"]
        ]
        if screen.width >= 1280:
            assert not tables, (name, "a table scrolls although there is room", tables)
    item_region = [
        region
        for region in visited.audits["item-epic"].structure["regions"]
        if region["id"] == "item-region"
    ]
    if screen.width <= 768:
        assert item_region and item_region[0]["x"], "the table of stories fits a narrow window?"


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
        # A page that says only that the project is not there has a heading, a line and a link.
        enough = 5 if name == "no-such-project" else 10
        assert audit.contrast.checked >= enough, (
            f"{name}: only {audit.contrast.checked} things looked at"
        )
        if audit.contrast.found:
            found[name] = audit.contrast.lines()
    assert not found, report(found)


# -- 7. the menu, its tree and the page of every record are reachable on every width -----------


async def goto(w: Any, address: str, ready: str) -> None:
    """Change the address (the way a link does) and wait for the screen it names."""
    await w.page.evaluate("(hash) => { location.hash = hash; }", address)
    await w.page.wait_for_selector(ready, state="attached", timeout=SLOW)
    await settle(w.page)


async def arrived(page: Page, ref: str) -> None:
    """The page of a record is on show and has finished drawing: when it has, it puts the focus on
    its title (a keyboard user who tabs on before that has the focus taken back)."""
    await expect(item_page(page)).to_have_attribute("data-ref", ref, timeout=SLOW)
    await expect(page.locator("#item-title")).to_be_focused(timeout=SLOW)


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


@screens
async def test_the_sidebars_tree_scrolls_in_its_own_region_and_opens_the_page_of_a_title(
    server_url: str, screen: Screen
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, "light") as w:
        page = w.page
        await open_address(w, "#/board")
        await settle(page)
        # Every epic is a row; opening an epic and its stories draws their rows, and only those.
        assert await page.locator("#epic-list > li[role=treeitem]").count() == 6
        for name in ("E1", "S1", "S2", "E6", "S7", "E3"):
            await tree_expand(page, world[name])
        await frames(page)
        tree = page.locator("#epic-list")
        found = await tree.evaluate(
            """(t, lastKey) => {
                const box = t.getBoundingClientRect();
                const side = document.getElementById('sidebar').getBoundingClientRect();
                const rows = [...t.querySelectorAll('.tree-row')]
                    .map((r) => r.getBoundingClientRect());
                const links = [...t.querySelectorAll('a.tree-link')];
                const last = t.querySelector(`li[data-key="${lastKey}"] .tree-row`);
                t.scrollTop = t.scrollHeight;
                const end = last.getBoundingClientRect();
                const reached = end.bottom <= t.getBoundingClientRect().bottom + 1
                    && end.top >= box.top - 1;
                const page = window.scrollY;
                t.scrollTop = 0;
                return { scrolls: t.scrollHeight > t.clientHeight + 1,
                    overflow: getComputedStyle(t).overflowY,
                    sidebarBottom: side.bottom, window: innerHeight, rows: rows.length,
                    shortest: Math.min(...rows.map((r) => r.height)),
                    stop: t.querySelectorAll('li[tabindex="0"]').length,
                    cut: links.filter((a) => a.querySelector('.tree-title').scrollWidth
                        > a.querySelector('.tree-title').clientWidth + 1
                        && !a.getAttribute('title')).length,
                    sideways: [...t.querySelectorAll('.tree-row')].filter((r) => r.scrollWidth
                        > r.clientWidth + 1).length,
                    reached, page };
            }""",
            world["W13"],
        )
        # The tree holds more than its place in the sidebar, so it scrolls there: the sidebar
        # does not grow and the page does not move; it is one stop for the keyboard.
        assert found["scrolls"] and found["overflow"] in {"auto", "scroll"}, found
        assert found["sidebarBottom"] <= found["window"] + 0.5, found
        assert found["rows"] >= 30 and found["reached"] and found["page"] == 0, found
        assert found["stop"] == 1, "the tree is one stop for Tab"
        assert found["shortest"] >= 24 and (found["shortest"] >= 39.5 or not screen.narrow), found
        assert found["cut"] == 0 and found["sideways"] == 0, found
        # A long title is cut with an ellipsis, and the whole of it is the link's title.
        long = page.locator(f"#epic-list a.tree-link[data-ref='{world['E3']}']")
        assert (await long.get_attribute("title") or "").endswith(
            "Long titles must wrap and never clip their meaning."
        )
        # A title opens that item's page, in the view the person is in; the narrow sheet closes,
        # the item is marked as the current one and its epic and story stay open.
        await page.locator(f"#epic-list a.tree-link[data-ref='{world['B1']}']").click()
        await expect(item_page(page)).to_have_attribute("data-ref", world["B1"], timeout=SLOW)
        assert page.url.endswith(page_address(world["B1"])), page.url
        if screen.narrow:
            await expect(page.locator("#sidebar.open")).to_have_count(0)
            await page.locator("#sidebar-toggle").click()
        current = page.locator("#epic-list a.tree-link[aria-current=page]")
        await expect(current).to_have_count(1)
        assert await current.get_attribute("data-ref") == world["B1"]
        for ref in (world["S2"], world["E1"]):
            await expect(page.locator(f"#epic-list li[data-key='{ref}']")).to_have_attribute(
                "aria-expanded", "true"
            )
        await expect(page.locator("#main-nav a[data-page=board]")).to_have_attribute(
            "aria-current", "page"
        )


RECORDS = [
    ("epic", "E3", ".lane[data-ref='{ref}'] a.lane-title", "#/board", "board"),
    ("story", "S5", ".card[data-ref='{ref}'] h3.card-title a", "#/board?show=both", "board"),
    ("task", "D1", ".card[data-ref='{ref}'] h3.card-title a", "#/board", "board"),
    (
        "rule",
        "R3",
        "li.gv-record[data-key='{ref}'] .gv-open",
        "#/lists?view=requirements",
        "lists",
    ),
    (
        "decision",
        "X2",
        "tr.record-line[data-ref='{ref}'] a.record-title",
        "#/lists?view=notes",
        "lists",
    ),
    (
        "note",
        "Y2",
        "tr.record-line[data-ref='{ref}'] a.record-title",
        "#/lists?view=notes",
        "lists",
    ),
]

BOXES = """
() => {
  const rect = (e) => {
    if (!e || !e.checkVisibility()) return null;
    const r = e.getBoundingClientRect();
    return { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height };
  };
  const title = document.getElementById('item-title');
  const crumbs = document.querySelector('nav.crumbs');
  const last = document.querySelector('nav.crumbs li:last-child');
  const sections = [...document.querySelectorAll('#item-details [data-part], #item-children')];
  return {
    page: rect(document.getElementById('item-page')),
    title: rect(title),
    titleCut: title.scrollWidth > title.clientWidth + 1
      || title.scrollHeight > title.clientHeight + 1,
    crumbs: rect(crumbs), last: rect(last),
    actions: [...document.querySelectorAll('.item-tools button, .item-tools a')]
      .filter((e) => e.checkVisibility()).map(rect),
    jumps: [...document.querySelectorAll('.item-nav .item-jump')].length,
    sections: sections.length,
    docWidth: document.documentElement.scrollWidth, window: innerWidth,
    scroll: [window.scrollY, document.documentElement.scrollHeight, innerHeight],
    panels: document.querySelectorAll('#panel, #drawer').length,
  };
}
"""


@screens
@themes
async def test_every_kind_of_record_opens_on_a_page_of_its_own_that_is_whole(
    server_url: str, screen: Screen, theme: str
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, theme) as w:
        page = w.page
        await open_address(w, "#/board")
        await settle(page)
        for kind, name, opener, address, came_from in RECORDS:
            await goto(w, address, ".card[data-ref], li.gv-record, tr.record-line")
            # The first line of a title, where a finger or a pointer lands on a title that wraps.
            await page.locator(opener.format(ref=world[name])).click(position={"x": 6, "y": 6})
            await expect(item_page(page)).to_have_attribute("data-ref", world[name], timeout=SLOW)
            await expect(page.locator("#item-details article")).to_be_visible(timeout=SLOW)
            await settle(page)
            where = f"{kind} at {screen.name} in {theme}"
            assert page.url.endswith(page_address(world[name], came_from)), (where, page.url)
            await no_panel_or_drawer(page)
            found = await page.evaluate(BOXES)
            # A page, not a sheet over one: it is as wide as the window leaves it, in one column
            # on a phone, and nothing of it is outside the window.
            box = found["page"]
            assert box["l"] >= -0.5 and box["r"] <= screen.width + 0.5, (where, box)
            if screen.phone:
                assert box["w"] >= screen.width - 48, (where, "a page over the whole window", box)
            assert found["docWidth"] <= found["window"], (where, "the page scrolls sideways")
            assert found["panels"] == 0, where
            # Its whole title is there, wrapped inside the page.
            assert not found["titleCut"], (where, found["title"])
            assert found["title"]["l"] >= box["l"] - 0.5 and found["title"]["r"] <= box["r"] + 0.5
            # The way back is where the person came from: the breadcrumb ends with the record and
            # fits in the top bar, and the view it names is the one the person was in.
            trail = await crumbs(page)
            assert trail[-1] and trail[2].lower().startswith(came_from), (where, trail)
            assert found["crumbs"]["r"] <= screen.width + 0.5, (where, found["crumbs"])
            assert found["last"] is not None and found["last"]["r"] <= screen.width + 0.5, where
            # Its actions are inside the window and big enough; an epic, story or task also has
            # its previous and next, with where it stands among its siblings.
            assert found["actions"], where
            for action in found["actions"]:
                assert action["l"] >= -0.5 and action["r"] <= screen.width + 0.5, (where, action)
                assert action["w"] >= 24 and action["h"] >= 24, (where, action)
            if kind in {"epic", "story", "task"}:
                moves = await pager(page)
                assert moves["position"] and moves["previous"] and moves["next"], (where, moves)
            # Its sections are listed, and the page is read by scrolling the page: no box that
            # holds it scrolls up and down on its own (a table may scroll sideways, in its region).
            assert found["jumps"] >= 4 and found["sections"] >= 4, (where, found)
            boxes = await page.evaluate(
                """() => {
                    const found = [];
                    for (let e = document.getElementById('item-page'); e && e !== document.body;
                         e = e.parentElement) {
                        const s = getComputedStyle(e);
                        if (['auto', 'scroll'].includes(s.overflowY)
                            && e.scrollHeight > e.clientHeight + 1) {
                            found.push(e.tagName.toLowerCase() + (e.id ? '#' + e.id : ''));
                        }
                    }
                    return found;
                }"""
            )
            assert not boxes, (where, "the page is held in a box that scrolls", boxes)
            # The view crumb goes back to the view, as it was left.
            await page.locator("nav.crumbs li.crumb-view a").click()
            await expect(page.locator("main#page h1").first).to_be_visible(timeout=SLOW)
            assert f"#/{came_from}" in page.url, (where, page.url)


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
async def test_the_board_the_map_the_timeline_and_a_long_record_scroll_or_pan_in_their_own_places(
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

        # The map: its buttons zoom it in and out and fit it, its nodes are reached one by one with
        # the arrow keys, and what it shows never makes the page scroll sideways.
        await goto(
            w,
            f"#/graph?scope={world['E1']}",
            "#graph-view svg.graph-rd-svg",
        )
        await goto(w, "#/graph", "#graph-view svg.graph-st-svg")
        view = page.locator("svg.graph-st-svg .graph-st-vp")
        start = view_of(await view.get_attribute("transform") or "")
        assert start[2] == pytest.approx(1, abs=0.01)
        await page.get_by_role("button", name="Zoom in").click()
        zoomed = view_of(await view.get_attribute("transform") or "")
        assert zoomed[2] == pytest.approx(start[2] * 1.4, rel=0.01)
        await page.get_by_role("button", name="Zoom out").click()
        back = view_of(await view.get_attribute("transform") or "")
        assert back[2] == pytest.approx(start[2], rel=0.01)
        await page.get_by_role("button", name="Zoom in").click()
        await page.get_by_role("button", name="Fit the whole project in view").click()
        fitted = view_of(await view.get_attribute("transform") or "")
        assert fitted == pytest.approx(start, abs=0.5)
        # One node has the Tab stop, and the arrow keys go from one to the next.
        first = page.locator("svg.graph-st-svg .graph-st-node[tabindex='0']")
        await expect(first).to_have_count(1)
        await first.focus()
        before = await page.evaluate("document.activeElement.getAttribute('data-ref')")
        await page.keyboard.press("ArrowRight")
        after = await page.evaluate("document.activeElement.getAttribute('data-ref')")
        assert before and after and before != after, (before, after)
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

        # A long record is read by scrolling the page: the page of a decision is taller than the
        # window, nothing on it is a box that scrolls up and down, and the mouse wheel moves it.
        await goto(w, page_address(world["X2"]), "#item-page[data-kind=note] #item-title")
        await settle(page)
        high = await page.evaluate("() => [document.documentElement.scrollHeight, innerHeight]")
        if high[0] > high[1] + 1:
            await page.mouse.move(screen.width / 2, screen.height / 2)
            await page.mouse.wheel(0, 300)
            await page.wait_for_function("() => window.scrollY > 0")
        assert await is_sideways_free(page)


@screens
async def test_the_tables_of_a_page_scroll_in_regions_of_their_own_and_the_page_never_does(
    server_url: str, screen: Screen
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, "light") as w:
        page = w.page
        await open_address(w, "#/board")
        # The epic with the long title and a story made of one long word.
        await goto(w, page_address(world["E3"]), "#item-table tbody tr")
        region = page.locator("#item-region")
        assert await region.get_attribute("role") == "region"
        assert await region.get_attribute("tabindex") == "0"
        assert (await region.get_attribute("aria-label") or "").strip()
        title = await page.locator("#item-title").evaluate(
            "(h) => [h.scrollWidth <= h.clientWidth + 1, h.getBoundingClientRect().right]"
        )
        assert title[0] and title[1] <= screen.width + 0.5, title
        room = await region.evaluate("(e) => [e.scrollWidth, e.clientWidth]")
        # Its columns need room (about 830 pixels): a window with less scrolls the table in its
        # region, which the keyboard pans, and a window with more shows all of it.
        if screen.width <= 768:
            assert room[0] > room[1] + 1, room
            await region.focus()
            await page.keyboard.press("ArrowRight")
            await page.wait_for_function(
                "() => document.getElementById('item-region').scrollLeft > 0"
            )
            await page.keyboard.press("ArrowLeft")
            await page.wait_for_function(
                "() => document.getElementById('item-region').scrollLeft === 0"
            )
        else:
            assert room[0] <= room[1] + 1, room
        assert await is_sideways_free(page)
        # The same for a story whose tasks hold the long words, and for a task, which has a table
        # of what it needs first and what needs it.
        await goto(w, page_address(world["S5"]), "#item-table tbody tr")
        assert await is_sideways_free(page)
        await goto(w, page_address(world["D1"]), "#item-part-title")
        assert await is_sideways_free(page)
        await expect(page.locator("#item-table")).to_have_count(0)
        # What concerns a story (its notes, the project-wide ones too) is in tables of their own,
        # each a named region: the window scrolls none of them when there is room for their
        # columns, and a narrow one scrolls each in its region, never the page.
        await goto(w, page_address(world["S2"]), "#item-page [data-part=context] .recall-section")
        await page.locator("details[data-disclosure=project_wide_notes] > summary").click()
        await settle(page)
        regions = page.locator("#item-details .record-region")
        count = await regions.count()
        assert count >= 2, "the story has notes and project-wide notes to show"
        for at in range(count):
            one = regions.nth(at)
            if not await one.is_visible():
                continue
            label = (await one.get_attribute("aria-label") or "").strip()
            assert label and await one.get_attribute("role") == "region", label
            assert await one.get_attribute("tabindex") == "0", label
            room = await one.evaluate("(e) => [e.scrollWidth, e.clientWidth]")
            if screen.width >= 1100:
                assert room[0] <= room[1] + 1, (label, room)
            elif room[0] > room[1] + 1:
                await one.focus()
                await page.keyboard.press("ArrowRight")
                await page.wait_for_function(
                    "(e) => e.scrollLeft > 0", arg=await one.element_handle()
                )
                await page.keyboard.press("ArrowLeft")
                await page.wait_for_function(
                    "(e) => e.scrollLeft === 0", arg=await one.element_handle()
                )
            assert await is_sideways_free(page), label


# -- 9. the work is done with the keyboard alone -----------------------------------------------


@asynccontextmanager
async def own_project(server_url: str) -> AsyncIterator[tuple[Client, str]]:
    """A project of its own with a task in it, so that a change made by keyboard is read back
    through MCP without disturbing what other tests look at."""
    project = make_project(register(), "Keyboard project")
    async with connect(server_url, project.key) as client:
        yield client, board_url(server_url, project)


async def read(client: Client, node: str) -> dict[str, Any]:
    return await ok(client, "get_node", node=node)


async def settled_on(client: Client, node: str, **wanted: Any) -> dict[str, Any]:
    """The node as MCP reads it once it has the wanted fields (a change by key is sent after a
    short while)."""
    for _ in range(60):
        found = await read(client, node)
        if all(found[name] == value for name, value in wanted.items()):
            return found
        await anyio.sleep(0.25)
    raise AssertionError(f"{node} never became {wanted}: {await read(client, node)}")


@screens
async def test_the_graph_is_reached_and_its_tab_and_a_node_opened_with_the_keyboard(
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
        await expect(page.locator("#graph-explore")).to_be_visible()
        assert "#/graph" in page.url
        await expect(page.locator("#main-nav a[data-page=graph]")).to_have_attribute(
            "aria-current", "page"
        )
        await expect(page.locator("svg.graph-st-svg")).to_be_visible(timeout=SLOW)
        # The tabs are one stop of Tab; the arrow keys choose among them, and Home and End go to
        # the first and the last.
        await tab_to(page, "#graph-tabs [role=tab][aria-selected=true]")
        assert (await focused(page)).startswith("button#graph-tab-structure")
        await page.keyboard.press("ArrowRight")
        await expect(page.locator("#graph-tab-around")).to_have_attribute("aria-selected", "true")
        await expect(page.locator("#graph-view")).to_have_attribute("data-tab", "around")
        assert (await focused(page)).startswith("button#graph-tab-around")
        await page.keyboard.press("End")
        await expect(page.locator("#graph-tab-order")).to_have_attribute("aria-selected", "true")
        await page.keyboard.press("Home")
        await expect(page.locator("#graph-tab-structure")).to_have_attribute(
            "aria-selected", "true"
        )
        await expect(page.locator("svg.graph-st-svg")).to_be_visible(timeout=SLOW)
        # A node of the map opens the page of its record with Enter (an epic, a story or a task:
        # there is no card to choose it into), and the breadcrumb, reached by the keyboard too,
        # leads back to the Graph as it was left.
        await tab_to(page, "svg.graph-st-svg .graph-st-node")
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("ArrowDown")
        key = await page.evaluate("document.activeElement.getAttribute('data-ref')")
        assert key, key
        await page.keyboard.press("Enter")
        await arrived(page, key)
        assert page.url.endswith(f"#/item?item={key}&from=graph"), page.url
        await start_of_page(page)
        await tab_to(page, "nav.crumbs li.crumb-view a")
        await page.keyboard.press("Enter")
        await expect(page.locator("#graph-explore")).to_be_visible(timeout=SLOW)
        await expect(page.locator("svg.graph-st-svg")).to_be_visible(timeout=SLOW)
        assert "#/graph" in page.url and "#/item" not in page.url, page.url


@screens
async def test_search_is_opened_typed_and_a_result_opened_and_gone_back_from_with_the_keyboard(
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
        await expect(page.locator("#search-results tr.result").first).to_be_visible(timeout=SLOW)
        assert "#/search?search=card" in page.url, page.url
        await no_panel_or_drawer(page)
        await tab_to(page, "#search-results tr.result a.result-title")
        link_text = (await page.evaluate("document.activeElement.textContent")).strip()
        await page.keyboard.press("Enter")
        await expect(item_page(page)).to_be_visible(timeout=SLOW)
        await expect(page.locator("#item-title")).to_be_focused(timeout=SLOW)
        assert page.url.endswith("&from=search"), page.url
        assert link_text.split(" ", 1)[1][:8] in await page.locator("#item-title").inner_text()
        # The breadcrumb names the page the person was in, and leads back to the results as they
        # were: the same words, the same list.
        assert (await crumbs(page))[2] == "Search"
        await start_of_page(page)
        await tab_to(page, "nav.crumbs li.crumb-view a")
        await page.keyboard.press("Enter")
        await expect(page.locator("#search-box")).to_have_value("card", timeout=SLOW)
        await expect(page.locator("#search-results tr.result").first).to_be_visible(timeout=SLOW)
        assert not (await focused(page)).startswith("body"), "the focus fell to the page"


@screens
async def test_a_title_is_edited_on_the_page_and_saved_with_the_keyboard(
    server_url: str, screen: Screen
) -> None:
    async with own_project(server_url) as (client, board):
        epic = await made(client, "epic", "Keys")
        story = await made(client, "story", "Keyed story", epic)
        task = await made(client, "task", "Old title", story)
        async with layout_page(board, screen, "light") as w:
            page = w.page
            await open_address(w, "#/board")
            await expect(page.locator(f".card[data-ref='{task}']")).to_be_visible(timeout=SLOW)
            await start_of_page(page)
            # The title of a card is the link to its page; the card itself is not a stop.
            await tab_to(page, f".card[data-ref='{task}'] h3.card-title a")
            await page.keyboard.press("Enter")
            await arrived(page, task)
            await expect(page.locator("#item-title")).to_contain_text("Old title")
            await tab_to(page, "#item-page button.edit-details")
            await page.keyboard.press("Enter")
            await expect(page.locator("#edit-title")).to_be_focused()
            await page.keyboard.press("ControlOrMeta+a")
            await page.keyboard.type("A new title from the keyboard")
            await tab_to(page, "#item-page form.edit button[type=submit]")
            await page.keyboard.press("Enter")
            await settled_on(client, task, title="A new title from the keyboard")
            await expect(page.locator("#item-title")).to_contain_text(
                "A new title from the keyboard"
            )
            # The breadcrumb leads back to the Board, whose card says the same.
            await start_of_page(page)
            await tab_to(page, "nav.crumbs li.crumb-view a")
            await page.keyboard.press("Enter")
            await expect(page.locator(f".card[data-ref='{task}'] .card-title")).to_have_text(
                "A new title from the keyboard", timeout=SLOW
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
async def test_an_items_page_is_sorted_and_walked_from_task_to_story_to_epic_with_the_keyboard(
    server_url: str, screen: Screen
) -> None:
    async with own_project(server_url) as (client, board):
        epic = await made(client, "epic", "Keys")
        other_epic = await made(client, "epic", "More keys")
        story = await made(client, "story", "Keyed story", epic)
        zebra = await made(client, "task", "Zebra", story)
        apple = await made(client, "task", "Apple", story, priority="urgent")
        async with layout_page(board, screen, "light") as w:
            page = w.page
            await open_address(w, page_address(story))
            await expect(page.locator("#item-table tbody tr")).to_have_count(2, timeout=SLOW)
            assert await item_rows(page) == [zebra, apple], "by number, to begin with"
            await expect(page.locator("#item-title")).to_have_text(
                re.compile(rf"^{code_of(story)}\s+Keyed story$")
            )
            # A column header is a button: Enter sorts by it, again reverses it; the focus stays.
            title = page.locator("th.col-title")
            await start_of_page(page)
            await tab_to(page, "#item-table button.sort[data-sort=title]", limit=250)
            await page.keyboard.press("Enter")
            await expect(title).to_have_attribute("aria-sort", "ascending")
            assert await item_rows(page) == [apple, zebra]
            assert (await focused(page)).startswith("button")
            assert await page.evaluate("document.activeElement.dataset.sort") == "title"
            await page.keyboard.press("Enter")
            await expect(title).to_have_attribute("aria-sort", "descending")
            assert await item_rows(page) == [zebra, apple]
            # The priority column puts the urgent task first.
            await tab_to(page, "#item-table button.sort[data-sort=priority]")
            await page.keyboard.press("Enter")
            await expect(page.locator("th.col-priority")).to_have_attribute(
                "aria-sort", "ascending"
            )
            assert await item_rows(page) == [apple, zebra]
            # A row has nothing to open beside the page: its code is the link to that task's page,
            # which has the focus on its title and says which story it is part of. The only other
            # buttons are the status and priority chips, which open a menu and not a record.
            assert (
                await page.locator("#item-table button").evaluate_all(
                    "els => els.filter((e) => !e.classList.contains('sort')"
                    " && !e.classList.contains('chip')).length"
                )
                == 0
            )
            await tab_to(page, f"#row-{ref_slug(zebra)} a.item-code-link")
            await page.keyboard.press("Enter")
            await arrived(page, zebra)
            await expect(page.locator("#item-title")).to_have_text(
                re.compile(rf"^{code_of(zebra)}\s+Zebra$")
            )
            await expect(page.locator("#item-part-title")).to_contain_text(code_of(story))
            # Previous and next: the keys ] and [ walk the tasks of the story, and the buttons are
            # stops of Tab that say where the page stands among them.
            moves = await pager(page)
            assert moves["position"] == "1 of 2", moves
            assert moves["previous"]["disabled"] and not moves["next"]["disabled"], moves
            await page.keyboard.press("]")
            await arrived(page, apple)
            assert (await pager(page))["position"] == "2 of 2"
            await page.keyboard.press("[")
            await arrived(page, zebra)
            await start_of_page(page)
            await tab_to(page, "#item-next")
            await page.keyboard.press("Enter")
            await arrived(page, apple)
            assert (await pager(page))["next"]["disabled"], "no next task after the last one"
            # Up: the link to the story, then to its epic; and across the epics with the same keys.
            await start_of_page(page)
            await tab_to(page, "#item-part-title a")
            await page.keyboard.press("Enter")
            await arrived(page, story)
            await start_of_page(page)
            await tab_to(page, "nav.crumbs li.crumb-item a")
            await page.keyboard.press("Enter")
            await arrived(page, epic)
            assert (await crumbs(page))[3:] == [code_of(epic)]
            await page.keyboard.press("]")
            await arrived(page, other_epic)
            assert (await crumbs(page))[3:] == [code_of(other_epic)]
            await page.keyboard.press("[")
            await arrived(page, epic)


async def type_date(page: Page, iso: str) -> None:
    """Type a day into the date field that has the focus, with the keyboard: the field is a text
    field that holds the day as YYYY-MM-DD, so what was there is selected and typed over."""
    await page.evaluate("() => document.activeElement.select()")
    await page.keyboard.type(iso)
    assert await page.evaluate("() => document.activeElement.value") == iso, await focused(page)


@screens
async def test_dates_are_changed_through_the_page_and_the_bar_without_dragging(
    server_url: str, screen: Screen
) -> None:
    async with own_project(server_url) as (client, board):
        epic = await made(client, "epic", "Keys")
        story = await made(client, "story", "Keyed story", epic)
        keyed = await made(
            client, "task", "Dated on the bar", story, start_on="2027-12-01", target_on="2027-12-15"
        )
        panel = await made(client, "task", "Dated in the page", story)
        async with layout_page(board, screen, "light") as w:
            page = w.page
            await open_address(w, "#/roadmap")
            bar = f".rm-bar[data-ref='{keyed}']"
            await expect(page.locator(bar)).to_be_visible(timeout=SLOW)
            # The bar moves with the arrow keys (a day each; Shift for a week), and is sent once.
            await start_of_page(page)
            await tab_to(page, bar, limit=400)
            await page.keyboard.press("ArrowRight")
            await page.keyboard.press("ArrowRight")
            await page.keyboard.press("Shift+ArrowRight")
            await settled_on(client, keyed, start_on="2027-12-10", target_on="2027-12-24")
            await page.keyboard.press("Alt+ArrowLeft")
            await settled_on(client, keyed, start_on="2027-12-09", target_on="2027-12-24")
            # The page of the task: its date fields.
            await open_address(w, page_address(panel))
            await expect(page.locator("#item-title")).to_contain_text(
                "Dated in the page", timeout=SLOW
            )
            await tab_to(page, "#item-page button.edit-details")
            await page.keyboard.press("Enter")
            await expect(page.locator("#edit-title")).to_be_focused()
            await tab_to(page, "#edit-start")
            await type_date(page, "2028-01-02")
            await tab_to(page, "#edit-target")
            await type_date(page, "2028-01-15")
            await tab_to(page, "#item-page form.edit button[type=submit]")
            await page.keyboard.press("Enter")
            await settled_on(client, panel, start_on="2028-01-02", target_on="2028-01-15")


# -- 10. the focus can be seen wherever it goes ------------------------------------------------

FOCUS_SCREENS = [BY_NAME["1280"], BY_NAME["768"], BY_NAME["390"], BY_NAME["zoom200"]]

# A node of the Structure map shows the keyboard's focus as a ring drawn inside the SVG (the audit
# of `focus_ring` knows outlines, shadows and the old map's dots): is it drawn, 2 pixels or more,
# and at 3:1 against what is behind the map?
STRUCTURE_RING = r"""() => {
  const node = document.activeElement;
  const ring = node.querySelector('.graph-st-ring');
  if (!ring) return { ok: false, why: 'no ring drawn in the node' };
  const style = getComputedStyle(ring);
  const channels = (text) => (text.match(/[\d.]+/g) || []).map(Number);
  const stroke = channels(style.stroke);
  let behind = null;
  for (let a = ring.parentElement; a && !behind; a = a.parentElement) {
    const c = channels(getComputedStyle(a).backgroundColor);
    if (c.length >= 3 && (c.length === 3 || c[3] > 0.99)) behind = c;
  }
  if (stroke.length < 3 || !behind) {
    return { ok: false, why: 'ring colour unreadable ' + style.stroke };
  }
  const light = (c) => {
    const [r, g, b] = c.slice(0, 3).map((v) => {
      const x = v / 255;
      return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const [hi, lo] = [light(stroke), light(behind)].sort((x, y) => y - x);
  const ratio = (hi + 0.05) / (lo + 0.05);
  const width = parseFloat(style.strokeWidth);
  return { ok: Number(style.opacity) >= 1 && width >= 2 && ratio >= 3,
    why: 'ring ' + style.strokeWidth + ' at opacity ' + style.opacity + ', '
      + ratio.toFixed(2) + ':1' };
}"""


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
        "item-epic",
        "item-story",
        "item-task",
        "item-requirement",
        "item-decision",
        "item-fact",
        "coverage",
        "search",
        "search-duplicates",
        "activity",
        "access",
        "tools",
        "status",
    ]
    # The page of an item is long: its details come before its tables, so the walk starts at its
    # title (the top bar and the menu are walked on the other screens) and goes on to the tables.
    presses = {
        "item-epic": 85,
        "item-story": 85,
        "item-task": 45,
        "item-requirement": 45,
        "item-decision": 45,
        "item-fact": 45,
    }
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
                if not ring["ok"] and ring["desc"].startswith("g.graph-st-node"):
                    ring = {**ring, **await page.evaluate(STRUCTURE_RING)}
                reached.add(ring["desc"])
                if not ring["ok"]:
                    found.append(f"{name}: {ring['desc']} shows {ring['why']}")

        for stop in [s for s in stops(world) if s.name in names]:
            await visit(w, stop)
            if stop.name in presses:
                await page.locator("#item-title").focus()
            else:
                await start_of_page(page)
            await walk(stop.name, presses.get(stop.name, 45))
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
        await page.keyboard.press("Escape")
        # The sidebar's tree is one stop, and the arrow keys move the focus inside it: the row
        # that has it shows a ring wherever it is, an epic, a story or a task.
        for ref in (world["E1"], world["S2"]):
            await tree_expand(page, ref)
        await frames(page)
        await page.locator("#epic-list li[tabindex='0']").focus()
        for _ in range(9):
            await page.keyboard.press("ArrowDown")
            ring = await focus_ring(page)
            assert not ring["skip"] and ring["desc"].startswith("li"), ring
            reached.add(ring["desc"])
            if not ring["ok"]:
                found.append(f"the tree: {ring['desc']} shows {ring['why']}")
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
            ".global-search",
            ".card .fold",
            ".lane-toggle",
            ".add-story",
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
        # The page of an item: the way to its neighbours and the list of its sections are as big
        # as a finger, and so is the way to open the page of a chosen record on the Graph.
        for address, selectors in (
            # An epic in the middle has both neighbours (the end of a row has a control that is
            # off and holds only an arrow), and Edit is as big as they are.
            (
                page_address(world["E3"]),
                ("#item-prev", "#item-next", ".item-jump", "button.edit-details"),
            ),
            (page_address(world["X1"]), (".item-jump",)),
            (
                f"#/graph?tab=around&focus={world['D1']}",
                ("#graph-hub-open",),
            ),
        ):
            await goto(w, address, "#item-page #item-title, #graph-hub[data-ref]")
            for selector in selectors:
                places = page.locator(selector)
                assert await places.count(), selector
                for at in range(min(await places.count(), 4)):
                    box = await places.nth(at).bounding_box()
                    if box is None or min(box["width"], box["height"]) < 43.5:
                        shown = box and f"{box['width']:.0f}x{box['height']:.0f}"
                        small.append(f"{selector} is {shown}, and a touch target is 44x44")
        assert not small, "\n".join(sorted(set(small)))
        await goto(w, "#/board", ".card[data-ref]")
        await page.locator("#sidebar-toggle").click()
        await frames(page)
        for selector in ("#sidebar-close", "#main-nav a", "#activity-open", "#settings-menu"):
            places = page.locator(selector)
            for at in range(await places.count()):
                box = await places.nth(at).bounding_box()
                assert box is not None and box["height"] >= 43.5, (selector, box)
        # The tree of epics: a row, and the chevron that opens it, are big enough to be hit.
        for ref in (world["E1"], world["S2"]):
            await tree_expand(page, ref)
        for selector, least in (("#epic-list .tree-row", 10), ("#epic-list button.tree-twisty", 5)):
            places = page.locator(selector)
            assert await places.count() >= least, selector
            for at in range(await places.count()):
                box = await places.nth(at).bounding_box()
                assert box is not None and min(box["width"], box["height"]) >= 31.5, (
                    selector,
                    box,
                )


@pytest.mark.parametrize("screen", [BY_NAME["390"], BY_NAME["1280"]], ids=["390", "1280"])
async def test_the_nodes_of_the_structure_map_can_be_zoomed_to_24_pixels_or_more(
    server_url: str, screen: Screen
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, "light") as w:
        page = w.page
        await open_address(w, "#/board")
        await goto(w, "#/graph", "#graph-view svg.graph-st-svg")
        smallest = """() => {
            const nodes = document.querySelectorAll('svg.graph-st-svg .graph-st-node');
            const sizes = [...nodes].map((n) => {
                const box = (n.querySelector('.graph-st-body') || n).getBoundingClientRect();
                return Math.min(box.width, box.height);
            });
            return Math.min(...sizes);
        }"""
        assert (
            await page.evaluate(
                "document.querySelectorAll('svg.graph-st-svg .graph-st-node').length"
            )
            >= 20
        )
        # The map starts fitted, with everything in view; its zoom buttons make every node a
        # target of 24 pixels or more before they stop.
        for _ in range(24):
            if await page.evaluate(smallest) >= 23.5:
                break
            await page.get_by_role("button", name="Zoom in").click()
        assert await page.evaluate(smallest) >= 23.5, world.board


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
    names = [
        "board",
        "graph-work-map",
        "roadmap",
        "item-story",
        "item-decision",
        "coverage",
        "search",
        "activity",
        "access",
    ]
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
