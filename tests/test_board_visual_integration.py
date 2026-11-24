"""M17's visual-quality proof (SPEC, Proof 2, the part that can be measured), carried through M20,
against the running containers in a real headless Chromium, in Light and in Dark, with real
records of varied lengths made through MCP (long titles, a title with no place to break, a long
source, dense and sparse columns, an epic with nothing in it, an epic that is all done). Screenshots
alone prove nothing here: the sizes and places of what the page draws are read from the page and
compared with each other, with what MCP says, and with the structure of the approved references
(docs/design/m17).

Nothing opens beside a page any more: every epic, story, task, criterion, decision and fact has a
page of its own, and what concerns an item (criteria, decisions, facts) is a table across the
whole width of that page, one line to a row. The owner's own case, an epic with nine long
project-wide decisions and linked facts, is made here with real calls and measured.

Set VRUM_LAYOUT_SHOTS to a folder outside the repository to also keep screenshots to look at."""

import math
import re
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import expect

from tests.board_helpers import SLOW, board_url, made, ref_slug
from tests.board_layout import (
    BY_NAME,
    OLD_GLYPHS,
    SHOTS,
    THEMES,
    UNBROKEN,
    Point,
    Screen,
    Stop,
    World,
    cards,
    crossing,
    distance_to_segment,
    frames,
    layout_page,
    open_address,
    rested,
    settle,
    shoot,
    stops,
    structure_map,
    the_world,
    visit,
)
from tests.helpers import (
    all_nodes,
    code_of,
    connect,
    make_decision,
    make_fact,
    ok,
    ref_from_code,
)
from tests.world import make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

themes = pytest.mark.parametrize("theme", THEMES)
WIDE = BY_NAME["1600"]
PHONE = BY_NAME["390"]
TOLERANCE = 1.0  # CSS pixels: two edges that are meant to line up differ by no more than this
KIND_NAMES = ["epic", "story", "task"]

PHONE_SCREENS = {
    "board",
    "graph-work-map",
    "graph-context-list",
    "roadmap",
    "item-story",
    "item-decision",
}


def page_address(ref: str, came_from: str = "board") -> str:
    """The address of the page of one epic, story, task, criterion, decision or fact, reached from
    one of the pages a person can be in."""
    return f"#/item?item={ref}&from={came_from}"


async def go_to(w: Any, address: str, ready: str) -> None:
    """Change the address, as a link does, and wait for what the page it names draws."""
    await w.page.evaluate("(hash) => { location.hash = hash; }", address)
    await w.page.wait_for_selector(ready, state="attached", timeout=SLOW)
    await settle(w.page)


# -- 1. cards: one size, aligned rows, aligned columns -------------------------------------


@pytest.mark.parametrize("screen", [WIDE, PHONE], ids=["1600", "390"])
@themes
async def test_comparable_cards_have_one_height_whether_their_column_is_sparse_or_dense(
    server_url: str, theme: str, screen: Any
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, theme) as w:
        await open_address(w, "#/board")
        await settle(w.page)
        found = await cards(w.page)
        same = [world[name] for name in ("A1", "A2", "A3", "A4", "A5", "A6", "A7")]
        dense, lone = same[:5], same[5:]
        for ident in dense:
            assert found[ident]["siblings"] >= 8, "a dense column"
            assert found[ident]["cell"] == "ready"
        for ident in lone:
            assert found[ident]["siblings"] == 1, "a lone card in its column"
        heights = {ident: round(found[ident]["box"]["h"], 2) for ident in same}
        assert max(heights.values()) - min(heights.values()) < 0.5, heights
        for ident in lone:
            card = found[ident]
            # The row of the lane is as tall as its tallest column, and the lone card does not
            # stretch to fill it.
            assert card["cellBox"]["h"] > card["box"]["h"] + 20, (
                ident,
                card["cellBox"],
                card["box"],
            )
        # The rows inside the card are in the same places in every card of the same kind.
        for part in ("top", "title", "chips", "context"):
            tops = {round(found[ident][part]["top"], 1) for ident in same}
            assert len(tops) == 1, (part, tops)
        paddings = {card["padding"] for card in found.values()}
        radii = {card["radius"] for card in found.values()}
        assert len(paddings) == 1 and len(radii) == 1, (paddings, radii)
        # In a lane the first card of every column starts where the others do.
        lanes: dict[Any, dict[Any, Any]] = {}
        for card in found.values():
            first = lanes.setdefault(card["epic"], {})
            if card["cell"] not in first or card["box"]["t"] < first[card["cell"]]["box"]["t"]:
                first[card["cell"]] = card
        for epic, firsts in lanes.items():
            tops = {round(card["box"]["t"], 1) for card in firsts.values()}
            assert len(tops) == 1, (epic, tops)
            for card in firsts.values():
                assert abs(card["box"]["t"] - card["cellBox"]["t"]) < 0.5
        # And the gutters between cards of a column are all the same.
        gaps = await w.page.evaluate(
            """() => [...document.querySelectorAll('.board .cards')].flatMap((list) => {
                const boxes = [...list.querySelectorAll('.card')]
                    .map((c) => c.getBoundingClientRect());
                return boxes.slice(1).map(
                    (b, at) => Math.round((b.top - boxes[at].bottom) * 10) / 10);
            })"""
        )
        assert gaps and len(set(gaps)) == 1, set(gaps)


# -- 2. headers, toolbars and the strip of a page line up with what is under them ------------

LEFTS = """
() => {
  const left = (e) => e ? Math.round(e.getBoundingClientRect().left * 10) / 10 : null;
  const heads = Object.fromEntries([...document.querySelectorAll('.column-head')]
    .map((e) => [e.dataset.status, left(e)]));
  const lanes = [...document.querySelectorAll('.lane')].filter((l) => l.querySelector('.cells')
    && !l.querySelector('.cells').hidden).map((lane) => ({
      epic: lane.dataset.epic,
      cells: Object.fromEntries([...lane.querySelectorAll('.cell')]
        .map((e) => [e.dataset.status, left(e)])),
      head: left(lane.querySelector('.lane-head')),
    }));
  const page = {
    title: left(document.querySelector('.board-page .page-title h1')),
    toolbar: left(document.querySelector('.board-toolbar')),
    notes: left(document.querySelector('.board-notes')),
    frame: left(document.querySelector('.board-frame')),
  };
  const heights = [...document.querySelectorAll(
    '.board-toolbar input, .board-toolbar select, .board-toolbar button,'
    + ' .board-toolbar .segmented')]
    .filter((e) => e.checkVisibility() && !e.closest('.segmented button')
      && !e.classList.contains('filter-info'))
    .map((e) => [e.id || e.textContent.trim(), e.getBoundingClientRect().height]);
  return { heads, lanes, page, heights };
}
"""


@pytest.mark.parametrize("screen", [WIDE, PHONE], ids=["1600", "390"])
@themes
async def test_column_headers_line_up_with_their_cards_and_the_page_header_with_the_toolbar(
    server_url: str, theme: str, screen: Any
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, theme) as w:
        await open_address(w, "#/board")
        await settle(w.page)
        found = await w.page.evaluate(LEFTS)
        assert found["lanes"], "lanes"
        for lane in found["lanes"]:
            for status, left in lane["cells"].items():
                assert abs(left - found["heads"][status]) <= TOLERANCE, (
                    lane["epic"],
                    status,
                    left,
                    found,
                )
        edges = {name: left for name, left in found["page"].items() if left is not None}
        assert max(edges.values()) - min(edges.values()) <= TOLERANCE, edges
        tall = {name: round(height) for name, height in found["heights"]}
        assert len(set(tall.values())) == 1, f"controls of the toolbar are not one height: {tall}"


HEADER = """
() => {
  const rect = (e) => {
    if (!e || !e.checkVisibility()) return null;
    const r = e.getBoundingClientRect();
    return { l: r.left, t: r.top, r: r.right, b: r.bottom };
  };
  const facts = [...document.querySelectorAll('.card-summary .state-item')].map((fact) => ({
    name: fact.querySelector('dt').textContent, box: rect(fact),
    dt: rect(fact.querySelector('dt')), dd: rect(fact.querySelector('dd')) }));
  return {
    title: rect(document.querySelector('.item-head .page-title')),
    heading: rect(document.querySelector('#item-title')),
    summary: rect(document.querySelector('.card-summary')),
    details: rect(document.querySelector('#item-details')),
    tools: rect(document.querySelector('.item-tools')),
    pager: rect(document.querySelector('#item-pager')),
    actions: rect(document.querySelector('.item-actions')),
    chips: [...document.querySelectorAll('.card-summary .chip')].map(rect),
    facts };
}
"""

KINDS_ON_SHOW = [
    ("E1", "epic"),
    ("S2", "story"),
    ("D1", "task"),
    ("R1", "rule"),
    ("X1", "note"),
    ("Y1", "note"),
]


@pytest.mark.parametrize("screen", [WIDE, BY_NAME["768"], PHONE], ids=["1600", "768", "390"])
@themes
async def test_the_header_of_every_kind_of_page_lines_up_with_its_strip_and_its_sections(
    server_url: str, theme: str, screen: Any
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, theme) as w:
        page = w.page
        await open_address(w, "#/board")
        for name, kind in KINDS_ON_SHOW:
            await go_to(w, page_address(world[name]), f"#item-page[data-kind={kind}] #item-title")
            await settle(page)
            found = await page.evaluate(HEADER)
            where = (name, kind, screen.name)
            for part in ("title", "summary", "details", "tools"):
                assert found[part] is not None, (*where, part)
            # No list of sections stands between the strip and the details any more.
            await expect(page.locator(".item-nav, .item-jump")).to_have_count(0)
            # One page: the title, the strip and the details start at the same left edge, and the
            # strip is as wide as the details.
            lefts = {part: round(found[part]["l"], 1) for part in ("title", "summary", "details")}
            assert max(lefts.values()) - min(lefts.values()) <= TOLERANCE, (*where, lefts)
            rights = {part: round(found[part]["r"], 1) for part in ("summary", "details")}
            assert max(rights.values()) - min(rights.values()) <= TOLERANCE, (*where, rights)
            summary = found["summary"]
            # Everything in the strip is inside it, a label stands over its value, and the facts
            # that share a line share their top.
            inside = [chip for chip in found["chips"] if chip] + [
                fact["box"] for fact in found["facts"]
            ]
            assert inside, where
            for box in inside:
                assert box["l"] >= summary["l"] - 0.5 and box["r"] <= summary["r"] + 0.5, (
                    *where,
                    box,
                )
                assert box["t"] >= summary["t"] - 0.5 and box["b"] <= summary["b"] + 0.5, (
                    *where,
                    box,
                )
            for fact in found["facts"]:
                assert abs(fact["dt"]["l"] - fact["dd"]["l"]) <= 0.5, (*where, fact)
                assert fact["dd"]["t"] >= fact["dt"]["b"] - 1, (*where, fact)
            for first in found["facts"]:
                for second in found["facts"]:
                    if abs(first["box"]["t"] - second["box"]["t"]) < 4:
                        assert abs(first["dt"]["t"] - second["dt"]["t"]) <= TOLERANCE, (
                            *where,
                            first,
                            second,
                        )
            # The way to move on and to change it are at the right of the title when there is
            # room, level with each other and ending where the strip ends; on a phone they are
            # under the title, at its left.
            tools, title = found["tools"], found["title"]
            if screen.phone:
                assert abs(tools["l"] - title["l"]) <= TOLERANCE, (*where, tools, title)
                assert tools["t"] >= title["b"] - 0.5, (*where, tools, title)
            else:
                assert tools["l"] >= title["r"] - 0.5, (
                    *where,
                    "tools beside the title",
                    tools,
                    title,
                )
                assert abs(tools["r"] - summary["r"]) <= TOLERANCE, (*where, tools, summary)
                assert abs(tools["t"] - title["t"]) <= 4, (*where, tools, title)
            if found["pager"] and found["actions"]:
                # Level with each other: the same middle, whatever each one's height.
                pager_middle = (found["pager"]["t"] + found["pager"]["b"]) / 2
                actions_middle = (found["actions"]["t"] + found["actions"]["b"]) / 2
                assert abs(pager_middle - actions_middle) <= 2, (*where, found)
                assert found["pager"]["r"] <= found["actions"]["l"] + 0.5, (*where, found)
            assert (found["pager"] is not None) == (kind in {"epic", "story", "task"}), where
            for box in (tools, title, summary):
                assert box["l"] >= -0.5 and box["r"] <= screen.width + 0.5, (*where, box)


# -- 3. icons are drawn, and none is a letter of a font -------------------------------------


@pytest.mark.parametrize("screen", [WIDE, PHONE], ids=["1600", "390"])
@themes
async def test_every_icon_is_drawn_with_a_size_and_the_epic_is_a_stack_of_layers(
    server_url: str, theme: str, screen: Any
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, theme) as w:
        await open_address(w, "#/board")
        for stop in [s for s in stops(world) if s.group != "entry"]:
            await visit(w, stop)
            icons = await w.page.evaluate(
                """() => [...document.querySelectorAll('svg.icon')]
                    .filter((e) => e.checkVisibility())
                    .map((e) => { const r = e.getBoundingClientRect();
                      // The map draws its icons at its own zoom; their size is their attribute.
                      const own = e.closest('svg#graph-svg');
                      return { name: [...e.classList].find((c) => c.startsWith('icon-')) || '?',
                        w: own ? Number(e.getAttribute('width')) : r.width,
                        h: own ? Number(e.getAttribute('height')) : r.height,
                        shapes: e.querySelectorAll('path, line, circle, rect').length }; })"""
            )
            assert len(icons) > 5, stop.name
            empty = [icon for icon in icons if icon["w"] < 8 or icon["h"] < 8 or not icon["shapes"]]
            assert not empty, (stop.name, empty[:5])
            text = await w.page.evaluate("() => document.body.innerText")
            used = sorted({glyph for glyph in OLD_GLYPHS if glyph in text})
            assert not used, f"{stop.name} writes {used} as if they were icons"
        await visit(w, stops(world)[0])
        layers = await w.page.evaluate(
            "() => [...document.querySelectorAll('svg.icon-epic')]"
            ".filter((e) => e.checkVisibility())"
            ".map((e) => e.querySelectorAll('path').length)"
        )
        assert layers and set(layers) == {3}, "an epic is drawn as three layers"
        assert OLD_GLYPHS not in await w.page.locator(".board").inner_text()


# -- 4. progress is the real numbers ---------------------------------------------------------

METERS = """
() => [...document.querySelectorAll('.meter')].filter((m) => m.checkVisibility()).map((m) => {
  const fill = m.firstElementChild;
  const track = m.getBoundingClientRect(); const bar = fill.getBoundingClientRect();
  const owner = m.closest('.lane, .card');
  return { kind: owner.classList.contains('lane') ? 'lane' : 'card',
    ref: owner.dataset.ref,
    text: (m.parentElement.querySelector('.progress-text')
      || m.parentElement.firstElementChild || {}).textContent,
    now: Number(m.getAttribute('aria-valuenow')), max: Number(m.getAttribute('aria-valuemax')),
    role: m.getAttribute('role'), label: m.getAttribute('aria-label'),
    ratio: track.width ? bar.width / track.width : 0, empty: bar.width === 0,
    fillColour: getComputedStyle(fill).backgroundColor,
    trackColour: getComputedStyle(m).backgroundColor };
})
"""


def contrast_of(first: str, second: str) -> float:
    def channels(css: str) -> list[float]:
        found = re.findall(r"[\d.]+", css)
        return [float(value) for value in found[:3]]

    def luminance(rgb: list[float]) -> float:
        lin = [
            (v / 255) / 12.92 if v / 255 <= 0.03928 else (((v / 255) + 0.055) / 1.055) ** 2.4
            for v in rgb
        ]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    a, b = luminance(channels(first)), luminance(channels(second))
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


async def truth(client: Client) -> dict[str, tuple[int, int]]:
    """Tasks done out of tasks that are not dropped, for every epic and story, counted from the
    tasks themselves (and checked against what list_nodes says of each)."""
    nodes = await all_nodes(client)
    by_ref = {node["ref"]: node for node in nodes}
    found: dict[str, tuple[int, int]] = {}
    for node in nodes:
        if node["kind"] == "task":
            continue
        tasks = [
            task
            for task in nodes
            if task["kind"] == "task"
            and task["status"] != "dropped"
            and (
                task["parent"] == node["ref"]
                if node["kind"] == "story"
                else by_ref[task["parent"]]["parent"] == node["ref"]
                and by_ref[task["parent"]]["status"] != "dropped"
            )
        ]
        done = sum(1 for task in tasks if task["status"] == "done")
        assert (node["progress_done"], node["progress_total"]) == (done, len(tasks)), node
        found[node["ref"]] = (done, len(tasks))
    return found


@pytest.mark.parametrize("screen", [WIDE, PHONE], ids=["1600", "390"])
@themes
async def test_progress_bars_show_the_real_done_and_total_and_an_empty_epic_shows_an_empty_bar(
    server_url: str, theme: str, screen: Any
) -> None:
    world = await the_world(server_url)
    async with connect(server_url, world.project.key) as client:
        real = await truth(client)
    assert real[world["E4"]] == (0, 0) and real[world["E5"]] == (2, 2)
    async with layout_page(world.board, screen, theme) as w:
        await open_address(w, "#/board")
        await settle(w.page)
        meters = await w.page.evaluate(METERS)
        lanes = {meter["ref"]: meter for meter in meters if meter["kind"] == "lane"}
        assert set(lanes) == {world[name] for name in ("E1", "E2", "E3", "E4", "E5", "E6")}
        for ref, meter in {
            **lanes,
            **{m["ref"]: m for m in meters if m["kind"] == "card"},
        }.items():
            done, total = real[ref]
            where = (meter["kind"], ref, meter)
            assert meter["role"] == "progressbar" and meter["label"], where
            assert (meter["now"], meter["max"]) == (done, max(total, 1)), where
            assert meter["text"] and meter["text"].startswith(f"{done} of {total} task"), where
            # The filled part is the share that is done, to the whole percent the page rounds to.
            assert abs(meter["ratio"] - (done / total if total else 0)) <= 0.0051, where
            if total == 0 or done == 0:
                assert meter["empty"], where
            assert meter["fillColour"] != meter["trackColour"], where
            assert contrast_of(meter["fillColour"], meter["trackColour"]) >= 3, where
        assert lanes[world["E4"]]["empty"] and lanes[world["E4"]]["text"].startswith("0 of 0 tasks")
        assert lanes[world["E5"]]["ratio"] == pytest.approx(1)
        assert 0 < lanes[world["E1"]]["ratio"] < 1 or real[world["E1"]][0] == 0
        # The page of an epic or a story says the same numbers as its lane or card, in words and
        # in its bar, and the table of what is under it says them for each child. An epic with no
        # tasks has nothing to say about progress, so its strip has no row and no empty bar.
        for name in ("E1", "E4", "E5", "S2", "S5"):
            ref = world[name]
            done, total = real[ref]
            if not total:
                await go_to(w, page_address(ref), f"#item-page[data-ref='{ref}'] .card-summary")
                await expect(w.page.locator("#item-page .card-summary .meter")).to_have_count(0)
                await expect(
                    w.page.locator("#item-page .card-summary .progress-item")
                ).to_have_count(0)
                continue
            await go_to(w, page_address(ref), f"#item-page[data-ref='{ref}'] .card-summary .meter")
            strip = w.page.locator("#item-page .card-summary .progress")
            assert (await strip.inner_text()).startswith(f"{done} of {total} task"), name
            assert "done" in await strip.inner_text(), name
            bar = strip.locator(".meter")
            assert await bar.get_attribute("role") == "progressbar", name
            assert await bar.get_attribute("aria-valuenow") == str(done), name
            assert await bar.get_attribute("aria-valuemax") == str(total), name
            shown = await bar.evaluate(
                "(m) => m.firstElementChild.getBoundingClientRect().width"
                " / m.getBoundingClientRect().width"
            )
            assert abs(shown - (done / total if total else 0)) <= 0.0051, (name, shown)
        for name in ("E1", "E3"):
            await go_to(w, page_address(world[name]), "#item-table tbody tr")
            for row in await w.page.locator("#item-table tbody tr").all():
                child = await row.get_attribute("data-ref")
                assert child is not None
                done, total = real[child]
                text = row.locator(".item-progress-text")
                if total:
                    assert (await text.inner_text()) == f"{done} of {total} done", (name, child)
                else:
                    await expect(text).to_have_count(0)


# -- 5. text is not clipped and does not run into what is beside it ------------------------


@pytest.mark.parametrize("screen", [WIDE, BY_NAME["1280"], PHONE], ids=["1600", "1280", "390"])
@themes
async def test_no_card_text_is_clipped_or_runs_into_its_priority_context_or_notes(
    server_url: str, theme: str, screen: Any
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, theme) as w:
        await open_address(w, "#/board")
        await settle(w.page)
        found = await cards(w.page)
        assert len(found) >= 20
        for ident, card in found.items():
            where = (ident, card["titleText"][:30])
            text, box = card["text"], card["box"]
            assert text["l"] >= box["l"] - 0.5 and text["r"] <= box["r"] + 0.5, where
            assert text["t"] >= box["t"] - 0.5 and text["b"] <= box["b"] + 0.5, where
            for part in ("top", "chips", "context", "note"):
                other = card[part]
                if other is None or not other["h"]:
                    continue
                if part == "top":
                    assert text["t"] >= other["b"] - 0.5, (where, part)
                else:
                    assert text["b"] <= other["t"] + 0.5, (where, part, text, other)
            # The whole title is in the card: nothing is cut off, and no ellipsis hides it.
            assert not card["titleClipped"] and card["ellipsis"] != "ellipsis", where
            assert card["titleText"] in card["label"], where
        # A long title makes its card taller instead of being cut.
        longest = found[world["D1"]]["box"]["h"]
        assert longest > found[world["A1"]]["box"]["h"] + 40
        # The cards under it are not pushed over by it: a long title waits where it is (and a card
        # that waits for it says so with its Waiting chip).
        assert found[world["D3"]]["chips"] is not None


# -- 6. the Structure map: no touching dots, few crossing lines, names that keep clear ----------


def meets(first: dict[str, float], second: dict[str, float]) -> bool:
    """Whether two boxes (l, t, r, b) share more than a hairline."""
    wide = min(first["r"], second["r"]) - max(first["l"], second["l"])
    high = min(first["b"], second["b"]) - max(first["t"], second["t"])
    return wide > 0.5 and high > 0.5


def covers_dot(box: dict[str, float], dot: dict[str, Any], depth: float | None = None) -> bool:
    """Whether a box (l, t, r, b) lies on a dot, a circle: the nearest point of the box is more
    than `depth` inside it. A name may touch the rim of a dot (the box of its text is a little
    wider than its letters, and the name of a hub leans on the hub it names), but it may not sit
    on the dot."""
    nearest_x = min(max(dot["x"], box["l"]), box["r"])
    nearest_y = min(max(dot["y"], box["t"]), box["b"])
    allowed = max(0.5, min(4.0, dot["r"] / 2)) if depth is None else depth
    return bool(math.hypot(dot["x"] - nearest_x, dot["y"] - nearest_y) < dot["r"] - allowed)


async def open_structure(w: Any, address: str) -> dict[str, Any]:
    """Open the Graph's Structure tab at an address (from another page, so that it is drawn afresh),
    wait until its dots and names are drawn and the live map has come to rest, and give the map as
    it is on the screen."""
    await w.page.evaluate("(hash) => { location.hash = hash; }", "#/board")
    await w.page.wait_for_selector(".card[data-ref]", timeout=SLOW)
    await w.page.evaluate("(hash) => { location.hash = hash; }", address)
    await w.page.wait_for_selector(
        "#graph-view[data-tab=structure] svg.graph-st-svg g.graph-st-node", timeout=SLOW
    )
    await w.page.wait_for_selector(".graph-st-names .graph-st-name", state="attached", timeout=SLOW)
    await rested(w.page)
    await settle(w.page)
    return await structure_map(w.page)


def line_ends(segment: tuple[Point, Point], dots: list[dict[str, Any]]) -> list[str]:
    """The two dots a line of the tree joins (the project, which is no dot of the list, is
    'root'): a line is drawn from the rim of one to the rim of the other."""
    ends = []
    for end in segment:
        rim, near = min(
            (abs(math.hypot(dot["x"] - end[0], dot["y"] - end[1]) - dot["r"]), dot["ref"])
            for dot in dots
        )
        ends.append(near if rim < 1.5 else "root")
    return ends


# The map is a live layout that has come to rest, not a drawing with room for every line: lines of
# the tree can cross and can run behind a sibling task of the same story, but only a few do. These
# are what the project below measures (no crossing, and 7 lines of 45 behind a sibling at 1600 and
# 1280), with room.
MOST_CROSSINGS = 2
MOST_BEHIND_A_SIBLING = 0.25


@pytest.mark.parametrize("screen", [WIDE, BY_NAME["1280"]], ids=["1600", "1280"])
@themes
async def test_the_structure_map_has_no_touching_dots_few_crossing_lines_and_names_that_keep_clear(
    server_url: str, theme: str, screen: Any
) -> None:
    world = await the_world(server_url)
    async with connect(server_url, world.project.key) as client:
        nodes = await all_nodes(client)
    parents = {one["ref"]: one["parent"] for one in nodes}
    by_kind = {kind: sum(1 for one in nodes if one["kind"] == kind) for kind in KIND_NAMES}
    scopes = [
        ("the whole project", "#/graph?tab=structure", KIND_NAMES),
        ("epics and stories", "#/graph?level=stories", KIND_NAMES[:2]),
        ("epics only", "#/graph?level=epics", KIND_NAMES[:1]),
        ("every name", "#/graph?labels=always", KIND_NAMES),
        ("with the links", "#/graph?links=1", KIND_NAMES),
        ("coloured by epic", "#/graph?colour=epic", KIND_NAMES),
        ("an item chosen", f"#/graph?focus={world['S2']}", KIND_NAMES),
    ]
    async with layout_page(world.board, screen, theme) as w:
        await open_address(w, "#/board")
        problems: list[str] = []
        for name, address, kinds in scopes:
            drawn = await open_structure(w, address)
            dots, lines, names = drawn["nodes"], drawn["lines"], drawn["names"]
            canvas = drawn["furniture"]["canvas"]
            assert canvas is not None, name
            # Every item of the levels shown is one dot, and nothing else is.
            assert sorted(dot["ref"] for dot in dots) == sorted(
                one["ref"] for one in nodes if one["kind"] in kinds
            ), name
            assert len(dots) == sum(by_kind[kind] for kind in kinds), name
            for dot in dots:
                inside = (
                    dot["x"] - dot["r"] >= canvas["l"] - 1
                    and dot["x"] + dot["r"] <= canvas["r"] + 1
                    and dot["y"] - dot["r"] >= canvas["t"] - 1
                    and dot["y"] + dot["r"] <= canvas["b"] + 1
                )
                if not inside:
                    problems.append(f"{name}: {dot['ref']} is outside the map after Fit")
            # No two dots touch.
            for at, first in enumerate(dots):
                for second in dots[at + 1 :]:
                    gap = math.hypot(first["x"] - second["x"], first["y"] - second["y"])
                    if gap < first["r"] + second["r"] - 0.5:
                        problems.append(f"{name}: {first['ref']} touches {second['ref']}")
            # Few lines of the tree cross another; none runs over an epic or a story, and one
            # runs over a task only when the task is a sibling on the story it leaves from.
            segments = [(tuple(line["from"]), tuple(line["to"])) for line in lines]
            assert len(segments) == len(dots), name
            crossings = 0
            behind = 0
            for at, first_line in enumerate(segments):
                crossings += sum(
                    crossing(first_line, second_line) for second_line in segments[at + 1 :]
                )
                ends = line_ends(first_line, dots)
                for dot in dots:
                    if distance_to_segment((dot["x"], dot["y"]), first_line) >= dot["r"] - 1.5:
                        continue
                    if dot["kind"] != "task" or parents[dot["ref"]] not in ends:
                        problems.append(f"{name}: a line of the tree runs over {dot['ref']}")
                    else:
                        behind += 1
            if crossings > MOST_CROSSINGS:
                problems.append(f"{name}: {crossings} lines of the tree cross")
            if behind > MOST_BEHIND_A_SIBLING * len(segments):
                problems.append(f"{name}: {behind} lines run behind a sibling task")
            # The names are inside the map, keep clear of each other, and cover no dot but the
            # hub they are written in.
            for named in names:
                if not (
                    named["l"] >= canvas["l"] - 1
                    and named["r"] <= canvas["r"] + 1
                    and named["t"] >= canvas["t"] - 1
                    and named["b"] <= canvas["b"] + 1
                ):
                    problems.append(f"{name}: the name {named['text']!r} leaves the map")
            for at, first_name in enumerate(names):
                for second_name in names[at + 1 :]:
                    if meets(first_name, second_name):
                        problems.append(
                            f"{name}: the names {first_name['text'][:20]!r} and "
                            f"{second_name['text'][:20]!r} overlap"
                        )
                if first_name["hub"] or first_name["project"]:
                    continue
                for dot in dots:
                    named_here = first_name["text"].startswith(code_of(dot["ref"]))
                    if covers_dot(first_name, dot, dot["r"] / 5 if named_here else None):
                        problems.append(
                            f"{name}: the name {first_name['text'][:20]!r} covers {dot['ref']}: "
                            f"{first_name} on {dot}"
                        )
            # The key, the toolbar and the zoom buttons are not on the dots, and the page does not
            # scroll sideways.
            for part in ("key", "toolbar"):
                box = drawn["furniture"][part]
                if box is not None and box["b"] > canvas["t"] + 0.5:
                    problems.append(f"{name}: the {part} is on the map")
            zoom = drawn["furniture"]["zoom"]
            assert zoom is not None, name
            # The zoom buttons float over a corner of the map, so a small task dot may pass under
            # them (the map can be moved); a hub, which is what the eye looks for first, may not.
            for dot in dots:
                if dot["kind"] != "task" and covers_dot(zoom, dot):
                    problems.append(f"{name}: the zoom buttons {zoom} cover {dot}")
            if drawn["scroll"][0] > drawn["scroll"][1]:
                problems.append(f"{name}: the page scrolls sideways")
        assert not problems, "\n".join(problems)


@pytest.mark.parametrize("screen", [WIDE, BY_NAME["1280"], PHONE], ids=["1600", "1280", "390"])
@themes
async def test_the_item_looked_at_is_marked_on_the_map_and_nothing_sits_beside_the_map(
    server_url: str, theme: str, screen: Any
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, theme) as w:
        await open_address(w, "#/board")
        whole = (await open_structure(w, "#/graph"))["furniture"]
        drawn = await open_structure(w, f"#/graph?focus={world['S2']}")
        furniture = drawn["furniture"]
        main = furniture["main"]
        assert main is not None and whole["main"] is not None, furniture
        # The map has the whole width of the page, with an item looked at or not: no card beside it
        # and none under it.
        await expect(w.page.locator(".graph-x-side")).to_have_count(0)
        for part in ("canvas", "zoom", "toolbar", "key"):
            other = furniture[part]
            assert other is not None and whole[part] is not None, part
            assert other["l"] >= main["l"] - 0.5 and other["r"] <= main["r"] + 0.5, (part, other)
            if part in ("canvas", "toolbar"):
                for edge in ("l", "r"):
                    assert abs(other[edge] - whole[part][edge]) <= TOLERANCE, (part, other)
        assert main["l"] >= -0.5 and main["r"] <= screen.width + 0.5, main
        assert drawn["scroll"][0] <= drawn["scroll"][1], "the page scrolls sideways"
        # The dot of the item is marked and the map can be moved to it.
        await expect(
            w.page.locator(f"g.graph-st-node.is-sel[data-ref='{world['S2']}']")
        ).to_have_count(1)
        await expect(w.page.locator("[data-action=zoom-chosen]")).to_be_visible()
        # A note made of long words lights the work it concerns, and the map is still as wide as
        # the page; its own page has all of the words.
        await open_structure(w, f"#/graph?focus={world['Y2']}")
        await expect(w.page.locator(".graph-x-side")).to_have_count(0)
        assert (await w.page.evaluate("[document.documentElement.scrollWidth, innerWidth]"))[
            0
        ] <= screen.width
        await go_to(w, f"#/graph?tab=around&focus={world['Y2']}", "#graph-hub-open")
        await w.page.locator("#graph-hub-open").click()
        await expect(w.page.locator("#item-page")).to_have_attribute(
            "data-ref", world["Y2"], timeout=SLOW
        )
        assert w.page.url.endswith(page_address(world["Y2"], "graph")), w.page.url
        await expect(w.page.locator("#item-page")).to_contain_text(UNBROKEN)


# -- 7. the structure of the approved references, with the real numbers ----------------------


@themes
async def test_the_board_has_the_structure_of_the_reference_with_the_real_numbers(
    server_url: str, theme: str
) -> None:
    world = await the_world(server_url)
    async with connect(server_url, world.project.key) as client:
        nodes = await all_nodes(client)
    work = [node for node in nodes if node["kind"] != "epic"]
    epics = sorted((node for node in nodes if node["kind"] == "epic"), key=lambda n: n["number"])
    statuses = ("new", "ready", "in_progress", "blocked", "done", "dropped")

    def by_status_of(kinds: tuple[str, ...]) -> dict[str, int]:
        found = dict.fromkeys(statuses, 0)
        for node in nodes:
            if node["kind"] in kinds:
                found[node["status"]] += 1
        return found

    by_status = by_status_of(("task",))
    both = by_status_of(("story", "task"))
    assert sum(both.values()) == len(work) and both["ready"] > 10
    async with layout_page(world.board, WIDE, theme) as w:
        page = w.page
        await open_address(w, "#/board")
        await settle(page)
        await shoot(page, f"board-{theme}")
        # The sidebar: the four views, each with an icon and its word, and no number beside any.
        views = ("board", "graph", "roadmap", "lists")
        links = page.locator("#main-nav a[data-page]")
        assert await links.count() == 4
        words = [await links.nth(at).locator(".side-label").inner_text() for at in range(4)]
        assert words == ["Board", "Graph", "Roadmap", "Lists"]
        await expect(page.locator("#main-nav .nav-count")).to_have_count(0)
        for at, name in enumerate(views):
            assert await links.nth(at).locator(f"svg.icon-{name}").count() == 1
        await expect(links.nth(0)).to_have_attribute("aria-current", "page")
        for at in (1, 2, 3):
            await expect(links.nth(at)).not_to_have_attribute("aria-current", "page")
        # Its Epics are a tree of the real epics, in order, each with its code and its whole title
        # (cut with an ellipsis, and complete in the link's title), and a chevron for the ones
        # that have stories. Nothing under an epic is drawn until it is opened.
        assert await page.locator("#epics-heading").text_content() == "Epics"
        tree = page.locator("ul#epic-list[role=tree]")
        assert (await tree.get_attribute("aria-label") or "").strip()
        listed = tree.locator("a.tree-link[data-ref]")
        assert [
            await listed.nth(at).get_attribute("data-ref") for at in range(await listed.count())
        ] == [epic["ref"] for epic in epics]
        for at, epic in enumerate(epics):
            link = listed.nth(at)
            assert await link.locator(".tree-title").inner_text() == epic["title"]
            assert await link.locator(".tree-code").inner_text() == f"E-{epic['number']}"
            assert await link.get_attribute("title") == f"E-{epic['number']} {epic['title']}"
            has_stories = any(node["parent"] == epic["ref"] for node in nodes)
            row = tree.locator(f"li[role=treeitem][data-key='{epic['ref']}']")
            assert await row.locator(":scope > .tree-row button.tree-twisty").count() == int(
                has_stories
            )
            assert await row.get_attribute("aria-expanded") == ("false" if has_stories else None)
        assert await tree.locator("li[aria-level='2']").count() == 0
        # At its foot: Activity, Settings and the account menu. The look of the board is chosen
        # in the account menu, once, and nowhere else on the page.
        await expect(page.locator("#sidebar #activity-open")).to_be_visible()
        await expect(page.locator("#sidebar #settings-menu")).to_be_visible()
        await expect(page.locator("#sidebar #account")).to_be_visible()
        assert await page.locator("#sidebar .appearance").count() == 0
        assert await page.locator("#top .appearance, #top [data-appearance]").count() == 0
        await page.locator("#account").click()
        choices = page.locator("#account-list [role=menuitemradio]")
        assert [await choices.nth(at).get_attribute("data-appearance") for at in range(3)] == [
            "light",
            "dark",
            "system",
        ]
        await expect(page.locator(f"#account-appearance-{theme}")).to_have_attribute(
            "aria-checked", "true"
        )
        await page.keyboard.press("Escape")
        # The top: where you are (the view, and nothing before it), and the search with the key of
        # the quick switcher beside it (the quick switcher has no button of its own).
        crumbs = [text.strip() for text in await page.locator(".crumbs li").all_inner_texts()]
        assert crumbs == ["Board"], crumbs
        assert (
            await page.locator("#global-search").get_attribute("placeholder")
            == "Search the project…"
        )
        assert await page.locator("#switcher-open").count() == 0
        assert await page.locator("#search-hint kbd").text_content() in {"Ctrl K", "⌘K"}
        # The page: its name is for a screen reader only (the top bar says it, so nothing on the
        # page repeats it), and its one main action has a plus.
        board_title = page.locator(".board-page h1")
        assert await board_title.inner_text() == "Board"
        assert "sr-only" in (await board_title.get_attribute("class") or "")
        assert await page.locator(".board-page .page-sub, .board-page .page-title").count() == 0
        new = page.locator("#new-item")
        assert "primary" in (await new.get_attribute("class") or "")
        assert (
            await new.locator("svg.icon-plus").count() == 1
            and (await new.inner_text()).strip() == "New item"
        )
        # The columns, in the order of the reference, with a sign, a word and the real count.
        heads = page.locator(".column-head")
        assert [await heads.nth(at).get_attribute("data-status") for at in range(6)] == list(
            by_status
        )
        for at, status in enumerate(by_status):
            head = heads.nth(at)
            assert await head.locator("svg.icon").count() == 1
            assert await head.locator(".column-count").inner_text() == str(by_status[status]), (
                status
            )
        assert await heads.nth(1).get_attribute("data-status") == "ready"
        assert "computed" not in await heads.nth(1).inner_text()
        # The Show switch: Tasks (the default), Stories or Both, and the counts follow it.
        assert [t.strip() for t in await page.locator("#board-show button").all_inner_texts()] == [
            "Tasks",
            "Stories",
            "Both",
        ]
        await page.locator("#board-show-both").click()
        for status in statuses:
            await expect(
                page.locator(f".column-head[data-status={status}] .column-count")
            ).to_have_text(str(both[status]), timeout=SLOW)
        # A lane for each epic, with its progress and its Add story button.
        lanes = page.locator(".lane")
        assert await lanes.count() == len(epics)
        for epic in epics:
            lane = page.locator(f".lane[data-ref='{epic['ref']}']")
            assert await lane.locator(".lane-title").inner_text() == epic["title"]
            assert await lane.locator(".lane-head .id").inner_text() == f"E-{epic['number']}"
            assert await lane.locator(".add-story").count() == 1
            assert "task" in await lane.locator(".progress-text").inner_text()
        # The Graph: its three tabs with Structure first, the dots of the whole project, the key
        # and the zoom buttons, and the item looked at marked on the map.
        await w.page.evaluate("(h) => { location.hash = h; }", f"#/graph?focus={world['S2']}")
        await page.wait_for_selector(
            "#graph-view[data-tab=structure] svg.graph-st-svg g.graph-st-node", timeout=SLOW
        )
        await expect(
            page.locator(f"g.graph-st-node.is-sel[data-ref='{world['S2']}']")
        ).to_have_count(1)
        await rested(page)
        await settle(page)
        await shoot(page, f"graph-chosen-{theme}")
        graph_title = page.locator("#page h1")
        assert await graph_title.inner_text() == "Graph"
        assert "sr-only" in (await graph_title.get_attribute("class") or "")
        await expect(page.locator(".graph-title")).to_have_count(0)
        assert [
            t.strip() for t in await page.locator("#graph-tabs .graph-tab-name").all_inner_texts()
        ] == [
            "Structure",
            "Around",
            "Order",
        ]
        await expect(page.locator("#graph-tab-structure")).to_have_attribute(
            "aria-selected", "true"
        )
        assert (
            await page.locator("#graph-st-title").inner_text() == "Structure of the whole project"
        )
        assert await page.get_by_role("button", name="Zoom in").count() == 1
        assert await page.get_by_role("button", name="Zoom out").count() == 1
        assert await page.get_by_role("button", name="Fit the whole project in view").count() == 1
        # The toolbar ends with Forces (its panel closed when the page opens) and Tidy.
        await expect(page.locator("#graph-st-forces")).to_have_attribute("aria-expanded", "false")
        await expect(page.locator("#graph-st-forces-panel")).to_be_hidden()
        await expect(page.locator("#graph-st-tidy")).to_be_visible()
        drawn = await structure_map(page)
        assert len(drawn["nodes"]) == len(nodes)
        # A note is not a dot: Around draws it in the middle and opens its page, which is what MCP
        # says of the note: kind, number, state, text, reason, and the list of its sections.
        async with connect(server_url, world.project.key) as client:
            record = await ok(client, "get_note", note_id=world.number("X1"))
        await w.page.evaluate(
            "(h) => { location.hash = h; }", f"#/graph?tab=around&focus={world['X1']}"
        )
        await expect(page.locator("#graph-hub")).to_have_attribute(
            "data-ref", world["X1"], timeout=SLOW
        )
        await page.locator("#graph-hub-open").click()
        await expect(page.locator("#item-page")).to_have_attribute(
            "data-ref", world["X1"], timeout=SLOW
        )
        await expect(page.locator("#item-title")).to_contain_text(record["headline"])
        label = " ".join((await page.locator("#item-title").inner_text()).split()).lower()
        assert label.startswith(f"n-{world.number('X1')} "), label
        assert await page.locator("#item-title svg.icon").count() == 1
        strip = await page.locator(".card-summary").first.inner_text()
        assert "Confirmed" in strip, strip
        assert (
            record["text"].split("\n\n")[-1]
            in await page.locator("#item-details [data-part=record]").inner_text()
        )
        assert record["why"] in await page.locator("#item-details [data-part=why]").inner_text()
        # The sections follow one another down the page, with no list of them to jump about in.
        await expect(page.locator(".item-nav, .item-jump")).to_have_count(0)
        parts: list[str] = await page.locator("#item-details [data-part]").evaluate_all(
            "els => els.map((e) => e.dataset.part)"
        )
        assert parts[:4] == ["record", "why", "confirmation", "concerns"], parts
        # The strip says only what is not on the page already: where the note stands, and who
        # decided it; who recorded it and when are in its history.
        facts = [
            text.strip()
            for text in await page.locator(".card-summary .state-item dt").all_text_contents()
        ]
        assert facts[0] == "Status" and "Recorded by" not in facts, facts


# -- 8. the page of an item: its real children in a table, its details beside each other ----

TABLE = """
() => [...document.querySelectorAll('#item-table tbody tr')].map((tr) => {
  const text = (selector) => {
    const e = tr.querySelector(selector);
    return e ? e.textContent.replace(/\\s+/g, ' ').trim() : null;
  };
  return { ref: tr.dataset.ref, id: tr.id, code: text('th.cell-item'), title: text('td.cell-title'),
    status: text('td.cell-status'), held: text('td.cell-held'),
    priorityChips: tr.querySelectorAll('td.cell-priority .chip').length,
    priorityTitle: (tr.querySelector('td.cell-priority .chip') || {}).title || null,
    due: text('td.cell-due'), overdue: !!tr.querySelector('.chip.due.overdue'),
    dueChips: tr.querySelectorAll('td.cell-due .chip').length,
    waiting: [...tr.querySelectorAll('td.cell-waiting a')]
      .map((a) => [a.textContent.trim(), a.getAttribute('href')]),
    waitingText: text('td.cell-waiting'),
    progress: text('.item-progress-text'), progressCell: text('td.cell-progress'),
    link: tr.querySelector('a.item-code-link').getAttribute('href'),
    titleLink: tr.querySelector('td.cell-title a').getAttribute('href'),
    quickLooks: tr.querySelectorAll('button.quick-look').length };
})
"""

# Where each word of a cell is drawn: a cell whose words are on two lines has one that wraps.
# `skip` names the cells that may hold several lines (a title is meant to wrap).
WRAPPED = """
(args) => {
  const found = [];
  for (const row of document.querySelectorAll(args.rows)) {
    for (const cell of row.children) {
      if (args.skip && cell.matches(args.skip)) continue;
      const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (!node.nodeValue.trim()) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        const tops = [...range.getClientRects()].filter((r) => r.width > 0 && r.height > 0)
          .map((r) => r.top).sort((a, b) => a - b);
        if (tops.some((top, at) => at && top - tops[at - 1] > 6)) {
          found.push((row.dataset.ref || row.id) + ' ' + cell.className + ': '
            + node.nodeValue.trim().slice(0, 40));
        }
      }
    }
  }
  return found;
}
"""

PAGE_BOXES = """
() => {
  const box = (selector) => {
    const e = document.querySelector(selector);
    if (!e || !e.checkVisibility()) return null;
    const r = e.getBoundingClientRect();
    return { l: r.left, t: r.top, r: r.right, b: r.bottom };
  };
  return { title: box('#item-title'), details: box('#item-details'), main: box('.card-main'),
    summary: box('.card-summary'), children: box('#item-children'),
    region: box('#item-region'), links: box('#item-links'),
    requirements: box('#item-details [data-part=requirements]'),
    definition: box('#item-details [data-part=definition]'),
    context: box('#item-details [data-part=context]'),
    history: box('#item-details [data-part=history]') };
}
"""

STATUS_WORDS = {
    "new": "New",
    "ready": "Ready",
    "in_progress": "In progress",
    "blocked": "Blocked",
    "done": "Done",
    "dropped": "Dropped",
}


COLUMNS = {
    "epic": [
        "Item",
        "Title",
        "Status",
        "Progress",
        "Size",
        "Priority",
        "Held by",
        "Due",
        "Waiting for",
    ],
    "story": ["Item", "Title", "Status", "Size", "Priority", "Held by", "Due", "Waiting for"],
}


def drawn_columns(kind: str, children: list[dict[str, Any]]) -> list[str]:
    """The columns of a table of children that some row has a value in: the rest are not drawn."""
    empty = {
        "Priority": all(child["priority"] == "normal" for child in children),
        "Held by": not any(child["claimed_by"] for child in children),
        "Due": not any(child["target_on"] for child in children),
        "Waiting for": not any(child["waiting_on"] for child in children),
    }
    return [name for name in COLUMNS[kind] if not empty.get(name, False)]


def item_stop(world: World, name: str, ready: str = "#item-table tbody tr") -> Stop:
    return Stop(name, page_address(world[name]), ready, "item")


@pytest.mark.parametrize("screen", [WIDE, BY_NAME["768"], PHONE], ids=["1600", "768", "390"])
@themes
async def test_an_items_page_names_it_by_its_code_and_tables_its_real_children(
    server_url: str, theme: str, screen: Any
) -> None:
    world = await the_world(server_url)
    truth: dict[str, tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]] = {}
    async with connect(server_url, world.project.key) as client:
        titles = {one["ref"]: one["title"] for one in await all_nodes(client)}
        for name in ("E1", "E3", "S2", "S5"):
            node = await ok(client, "get_node", node=world[name])
            parent = await ok(client, "get_node", node=node["parent"]) if node["parent"] else None
            children = await all_nodes(client, parent=world[name])
            truth[name] = (node, sorted(children, key=lambda child: child["number"]), parent)
    assert len(truth["E1"][1]) == 2 and len(truth["S2"][1]) >= 5
    async with layout_page(world.board, screen, theme) as w:
        page = w.page
        await open_address(w, "#/board")
        for name, (node, children, _parent) in truth.items():
            await visit(w, item_stop(world, name))
            ref, under = node["ref"], "stories" if node["kind"] == "epic" else "tasks"
            # What it is, by its code and its title; nothing sits under the title.
            title = " ".join((await page.locator("#item-title").inner_text()).split())
            assert title == f"{code_of(ref)} {node['title']}", title
            assert await page.locator("#item-what").count() == 0
            # What is under it: how many, said the same way in the count, the heading and the
            # table's caption (which names what it is in, by its code and its name).
            heading = await page.locator("#item-children-title").inner_text()
            assert heading == under.capitalize(), heading
            assert await page.locator("#item-count").inner_text() == str(len(children))
            assert await page.locator("#item-table caption").inner_text() == (
                f"{heading} in {code_of(ref)} {node['title']}"
            )
            heads: list[str] = await page.locator("#item-table thead th button.sort").evaluate_all(
                "els => els.map((e) => e.textContent.trim())"
            )
            assert heads == drawn_columns(node["kind"], children), heads
            # The rows are what MCP lists as its children, by number, each with its own numbers. A
            # cell with nothing to say is empty: no "Nobody", "Not set" or "Nothing", a priority
            # only when it is not normal (as a small sign with its words in its title) and a due
            # day as the one chip that is red when the work is late.
            rows = await page.evaluate(TABLE)
            assert [row["ref"] for row in rows] == [child["ref"] for child in children]
            for row, child in zip(rows, children, strict=True):
                where = (name, child["ref"], row)
                assert row["id"] == f"row-{ref_slug(child['ref'])}", where
                assert row["code"] == code_of(child["ref"]), where
                assert row["title"] == child["title"], where
                assert row["status"] == STATUS_WORDS[child["status"]], where
                assert row["priorityChips"] == int(child["priority"] != "normal"), where
                if child["priority"] != "normal":
                    assert row["priorityTitle"] == f"{child['priority'].capitalize()} priority", (
                        where
                    )
                assert row["held"] == (
                    (child["claimed_by"] or "") if "Held by" in heads else None
                ), where
                if child["target_on"]:
                    assert row["due"] == f"Due {str(child['target_on'])[:10]}", where
                else:
                    assert row["due"] in ("", None) and row["dueChips"] == 0, where
                assert row["overdue"] == bool(child["overdue"]), where
                # What it waits for is each thing's code and then its name, one to a line.
                assert [text for text, _ in row["waiting"]] == [
                    f"{code_of(other)} {titles[other]}" for other in child["waiting_on"]
                ], where
                assert (row["waitingText"] in ("", None)) == (not child["waiting_on"]), where
                for text, href in row["waiting"]:
                    assert href == page_address(ref_from_code(text.split(" ", 1)[0])), where
                # A row opens a page, by its code and by its title; nothing opens beside it.
                assert row["link"] == page_address(child["ref"]), where
                assert row["titleLink"] == page_address(child["ref"]), where
                assert row["quickLooks"] == 0, where
                if node["kind"] == "epic":
                    done, total = child["progress_done"], child["progress_total"]
                    assert row["progress"] == (f"{done} of {total} done" if total else None), where
                    assert (row["progressCell"] == "") == (not total), where
            # The words of every cell but the title stay on one line, however the title is made:
            # a word is never broken to make room for a long one.
            wrapped = await page.evaluate(
                WRAPPED, {"rows": "#item-table tbody tr", "skip": "td.cell-title"}
            )
            assert not wrapped, (name, wrapped)
    held = [child for child in truth["S2"][1] if child["claimed_by"]]
    waiting = [child for child in truth["S2"][1] if child["waiting_on"]]
    assert held and waiting, "the story has a held task and one that waits"


@pytest.mark.parametrize("screen", [WIDE, BY_NAME["768"], PHONE], ids=["1600", "768", "390"])
@themes
async def test_an_items_page_lays_its_details_out_to_the_width_and_lines_up_with_its_table(
    server_url: str, theme: str, screen: Any
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, theme) as w:
        page = w.page
        await open_address(w, "#/board")
        for name in ("S2", "S5", "E1"):
            await visit(w, item_stop(world, name))
            await shoot(page, f"item-{name}-{theme}-{screen.name}")
            found = await page.evaluate(PAGE_BOXES)
            for part in ("title", "details", "main", "children", "region", "links"):
                assert found[part] is not None, (name, part)
            main = found["main"]
            # One column at every width: no side column, and the sheet of details is as wide as
            # the page's content.
            await expect(page.locator(".card-side, .card-columns")).to_have_count(0)
            assert abs(main["l"] - found["details"]["l"]) <= TOLERANCE, (name, main)
            assert abs(main["r"] - found["details"]["r"]) <= TOLERANCE, (name, main)
            # One page: the title, the details and the table start at the same left edge, and
            # the table's region is as wide as they are.
            lefts = {part: round(found[part]["l"], 1) for part in ("title", "details", "children")}
            assert max(lefts.values()) - min(lefts.values()) <= TOLERANCE, (name, lefts)
            assert abs(found["region"]["r"] - found["details"]["r"]) <= TOLERANCE, (name, found)
            # The sheet of details comes first, the table of what is under the item under it,
            # never beside it, and then what concerns the item, each across the whole width.
            assert found["children"]["t"] >= main["b"] - 0.5, (name, found)
            # An epic holds no acceptance criteria and no definition of done: the sections that are
            # there follow one another down the page, in the order of the page.
            order = [
                part
                for part in (
                    "children",
                    "links",
                    "requirements",
                    "definition",
                    "context",
                    "history",
                )
                if found[part] is not None
            ]
            assert order[:2] == ["children", "links"] and order[-2:] == ["context", "history"], (
                name,
                order,
            )
            assert (name == "E1") == ("requirements" not in order), (name, order)
            for before, after in zip(order, order[1:], strict=False):
                assert found[after]["t"] >= found[before]["b"] - 0.5, (name, before, after, found)
            for part in order[1:]:
                assert abs(found[part]["l"] - found["details"]["l"]) <= TOLERANCE, (name, part)
                assert abs(found[part]["r"] - found["details"]["r"]) <= TOLERANCE, (name, part)
            # Nothing of the page is outside the window.
            for part, box in found.items():
                if box is not None:
                    assert box["l"] >= -0.5 and box["r"] <= screen.width + 0.5, (name, part, box)


@pytest.mark.parametrize("screen", [WIDE, PHONE], ids=["1600", "390"])
@themes
async def test_an_items_page_with_nothing_under_it_says_so_and_offers_the_first_one(
    server_url: str, theme: str, screen: Any
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, theme) as w:
        page = w.page
        await open_address(w, "#/board")
        ref = world["E4"]
        await visit(w, item_stop(world, "E4", "#item-children #new-child"))
        # With nothing under it the section is its heading and the way to add one: no count, no
        # box that says "No stories yet", and no table.
        await expect(page.locator("#item-children-title")).to_have_text("Stories")
        assert await page.locator("#item-count").inner_text() == ""
        await expect(page.locator("#item-empty")).to_have_count(0)
        await expect(page.locator("#item-region")).to_be_hidden()
        assert await page.locator("#item-table").count() == 0
        # The one button that adds a story under this epic is there; it opens the form for a story
        # in this epic.
        add = page.locator("#new-child")
        assert (await add.inner_text()).strip() == "New story"
        assert "small" in (await add.get_attribute("class") or "")
        assert "primary" not in (await add.get_attribute("class") or "")
        await add.click()
        dialog = page.locator("dialog.modal[open]")
        await expect(dialog).to_be_visible()
        assert await dialog.locator("#new-kind").input_value() == "story"
        assert await dialog.locator("#new-parent").input_value() == ref
        await page.keyboard.press("Escape")
        await expect(dialog).to_be_hidden()
        # A task has no table at all and no band that says which story it is part of: the
        # breadcrumb names its epic and story, and links to them.
        await visit(w, item_stop(world, "D1", "#item-links"))
        assert await page.locator("#item-table").count() == 0
        await expect(page.locator("#item-part, #item-part-title")).to_have_count(0)
        async with connect(server_url, world.project.key) as client:
            story = await ok(client, "get_node", node=world["S5"])
        crumb = page.locator(f"nav.crumbs li.crumb-item a[href='{page_address(world['S5'])}']")
        await expect(crumb).to_have_attribute("title", f"{code_of(world['S5'])} {story['title']}")
        # The step is its code and its name (the name is cut when there is no room), and no
        # label of its own repeats what it says.
        await expect(crumb.locator(".crumb-code")).to_have_text(code_of(world["S5"]))
        await expect(crumb.locator(".crumb-name")).to_have_text(story["title"])
        assert await crumb.get_attribute("aria-label") is None
        href = await crumb.get_attribute("href")
        assert href is not None and href == page_address(world["S5"]), href


# -- 9. the owner's case: nine long project-wide decisions and linked facts under one epic ----

OWNER_NOTE = (
    "Every decision here is long on purpose, so that its statement cannot fit on one line: "
)


class OwnerCase:
    """An epic that has what the owner's own project has: nine long project-wide decisions, some
    notes and decisions linked to it (one decision replaced and one note withdrawn), a requirement
    in its story, and a second epic that has nothing linked to it. It is a project of its own, so
    that nothing the other tests count changes."""

    def __init__(self, board: str, key: str) -> None:
        self.board = board
        self.key = key
        self.epic = ""
        self.empty_epic = ""
        self.story = ""
        self.wide: list[int] = []
        self.decisions: list[int] = []
        self.notes: list[int] = []
        self.replaced = 0
        self.withdrawn = 0


_owner_cases: dict[str, OwnerCase] = {}


async def the_owner_case(server_url: str) -> OwnerCase:
    if server_url not in _owner_cases:
        project = make_project(register(name="Owner case"), "Owner case project")
        case = OwnerCase(board_url(server_url, project), project.key)
        async with connect(server_url, project.key) as client:
            case.epic = await made(client, "epic", "An epic with a lot of context")
            case.empty_epic = await made(client, "epic", "An epic with nothing linked to it")
            case.story = await made(
                client, "story", "A story with an acceptance criterion", case.epic
            )
            await made(client, "task", "A task in it", case.story)
            await ok(client, "create_rule", actor="ann", title="Keep the history", node=case.story)
            for number in range(1, 10):
                title = (
                    UNBROKEN
                    if number == 4
                    else f"Project-wide decision {number}: " + "a title " * 12
                )
                made_one = await make_decision(
                    client,
                    title,
                    text=OWNER_NOTE * 4 + str(number),
                    reason="Because " * 40,
                    project_wide=True,
                )
                case.wide.append(made_one["id"])
            for number in range(1, 4):
                made_one = await make_decision(
                    client,
                    f"Linked decision {number}: " + "a title " * 12,
                    text=OWNER_NOTE * 3,
                    reason="Because " * 40,
                    nodes=[case.epic],
                )
                case.decisions.append(made_one["id"])
            old = await make_decision(
                client, "A decision that was replaced " * 4, text=OWNER_NOTE, nodes=[case.epic]
            )
            case.replaced = old["id"]
            replacement = await ok(
                client,
                "supersede_note",
                replaces=[old["id"]],
                text="Its replacement " * 4 + "\n\n" + OWNER_NOTE * 2,
                why="It changed",
                decided_by="Marie",
                keep_links=True,
            )
            case.decisions.append(replacement["replacement"]["id"])
            for number in range(1, 5):
                made_one = await make_fact(
                    client,
                    OWNER_NOTE * 3 + str(number),
                    source="https://example.test/sources/" + "s" * 120,
                    nodes=[case.epic],
                )
                case.notes.append(made_one["id"])
            wrong = await make_fact(client, "A note that was wrong " * 6, nodes=[case.epic])
            case.withdrawn = wrong["id"]
            await ok(client, "withdraw_note", note_ids=[wrong["id"]], reason="Wrong")
        _owner_cases[server_url] = case
    return _owner_cases[server_url]


OWNER = """
() => {
  const rect = (e) => {
    if (!e || !e.checkVisibility()) return null;
    const r = e.getBoundingClientRect();
    return { l: r.left, t: r.top, r: r.right, b: r.bottom, h: r.height };
  };
  const regions = [...document.querySelectorAll('.record-region, .item-region')]
    .filter((e) => e.checkVisibility())
    .map((region) => {
      const table = region.querySelector('table');
      const outer = region.closest('details') || region;
      const heads = [...table.querySelectorAll('thead th')];
      const body = [...table.querySelectorAll('tbody tr')];
      return { label: region.getAttribute('aria-label') || region.id,
        role: region.getAttribute('role'),
        tab: region.getAttribute('tabindex'), kind: table.className,
        outer: rect(outer), region: rect(region), table: rect(table),
        scroll: [region.scrollWidth, region.clientWidth],
        columns: heads.map((th, at) => ({ name: th.textContent.trim(),
          width: th.getBoundingClientRect().width,
          filled: body.filter((tr) => tr.children[at]
            && tr.children[at].textContent.trim()).length })),
        rows: body.map((tr) => ({ ref: tr.dataset.ref,
          h: tr.getBoundingClientRect().height, ended: tr.classList.contains('ended') })),
        clamps: [...table.querySelectorAll('.clamp')].map((c) => ({
          cut: c.scrollWidth > c.clientWidth + 1 || c.scrollHeight > c.clientHeight + 1,
          title: c.getAttribute('title') || '' })) };
    });
  const disclosures = Object.fromEntries(
    [...document.querySelectorAll('details.recall-disclosure')].map((d) => [d.dataset.disclosure,
      { open: d.open, summary: d.querySelector('summary').textContent.trim() }]));
  return { regions, disclosures, details: rect(document.querySelector('#item-details')),
    page: rect(document.querySelector('#item-page')),
    scroll: [document.documentElement.scrollWidth, innerWidth] };
}
"""


def ids_of(items: list[dict[str, Any]]) -> list[int]:
    return [item["note"]["id"] for item in items]


@pytest.mark.parametrize(
    "screen",
    [WIDE, BY_NAME["1280"], BY_NAME["768"], PHONE, BY_NAME["zoom200"]],
    ids=["1600", "1280", "768", "390", "zoom200"],
)
@themes
async def test_an_epic_with_nine_long_project_wide_notes_has_full_width_one_line_tables(
    server_url: str, theme: str, screen: Screen
) -> None:
    case = await the_owner_case(server_url)
    async with connect(server_url, case.key) as client:
        recalled = await ok(client, "recall", node=case.epic, limit=50)
        long_note = await ok(client, "get_note", note_id=case.notes[0])
    # What an agent is told for this epic: the linked ones, the project-wide ones, the ended.
    assert sorted(ids_of(recalled["notes"]["items"])) == sorted(case.decisions + case.notes)
    assert sorted(ids_of(recalled["project_wide_notes"]["items"])) == sorted(case.wide)
    assert sorted(ids_of(recalled["past_notes"]["items"])) == sorted(
        [case.replaced, case.withdrawn]
    )
    async with layout_page(case.board, screen, theme) as w:
        page = w.page
        await open_address(w, "#/board")
        await go_to(
            w,
            page_address(case.epic),
            f"#item-page[data-ref='{case.epic}'] [data-part=context] .recall-section",
        )
        context = page.locator("#item-details [data-part=context]")
        await expect(context.locator(".recall-section[data-section=notes]")).to_be_visible()
        shown = await page.evaluate(OWNER)
        # The project-wide notes apply to every epic: this one has entries of its own, so they
        # wait closed behind a line that says how many there are. The earlier ones are fewer than
        # five, so they are open.
        wide = shown["disclosures"]["project_wide_notes"]
        assert wide["open"] is False and re.match(r"Project-wide notes\s*9", wide["summary"]), wide
        assert shown["disclosures"]["past_notes"]["open"] is True, shown["disclosures"]
        labels = [region["label"] for region in shown["regions"]]
        assert any(label.startswith("Notes in force") for label in labels), labels
        assert not any(label.startswith("Project-wide") for label in labels), labels
        await page.locator("details[data-disclosure=project_wide_notes] > summary").click()
        await settle(page)
        shown = await page.evaluate(OWNER)
        wide_rows = [r for r in shown["regions"] if r["label"].startswith("Project-wide")]
        assert len(wide_rows) == 1 and len(wide_rows[0]["rows"]) == 9, wide_rows
        assert sorted(int(row["ref"].split(":")[1]) for row in wide_rows[0]["rows"]) == sorted(
            case.wide
        )
        # Every table of the page, the table of the stories and the tables of notes, is as wide as
        # the page's content. An epic holds no acceptance criteria, so none is drawn.
        page_box = shown["details"]
        assert page_box is not None
        assert len(shown["regions"]) >= 4, [region["label"] for region in shown["regions"]]
        for region in shown["regions"]:
            where = (screen.name, region["label"])
            assert region["role"] == "region" and region["tab"] == "0", where
            assert abs(region["outer"]["l"] - page_box["l"]) <= TOLERANCE, (*where, region["outer"])
            assert abs(region["outer"]["r"] - page_box["r"]) <= TOLERANCE, (*where, region["outer"])
            # The table is at least as wide as its box, so there is no empty strip beside it.
            assert (
                region["table"]["r"] - region["table"]["l"]
                >= region["region"]["r"] - region["region"]["l"] - 2.5
            ), (*where, region)
            if screen.width >= 1100:
                assert region["scroll"][0] <= region["scroll"][1] + 1, (*where, "scrolls", region)
            # A row holds at most three lines of a long text, with its whole in the cell's title or
            # behind "Show whole text".
            heights = {round(row["h"]) for row in region["rows"]}
            assert region["rows"], where
            assert max(heights) < 130, (*where, heights)
            # No dead column: each column of a table of notes has something in a cell, and when
            # the table is not scrolling, only the column that carries the words (the title, what
            # it says) takes more than a third of the table, so no column is blank space. The
            # table of the stories under the epic is left out of the first rule: a cell with
            # nothing to say (a priority that is normal, nobody holding it, no due day) is blank
            # by design, so a column of them is empty when the work has none of that to say.
            if not region["label"].startswith(("Stories in", "Tasks in")):
                for column in region["columns"]:
                    assert column["filled"] > 0, (*where, "an empty column", column)
            if region["scroll"][0] <= region["scroll"][1] + 1:
                table_width = region["table"]["r"] - region["table"]["l"]
                for column in region["columns"]:
                    if column["name"] not in {"Title", "What it says", "Item", "Applies to"}:
                        assert column["width"] <= table_width / 3, (*where, "a wide column", column)
        assert shown["scroll"][0] <= shown["scroll"][1], f"the page is {shown['scroll']} wide"
        # A cell that wraps is cut at three lines, which the row height above bounds.
        # What does not fit is cut with an ellipsis, and its whole is the cell's title, so that
        # nothing is lost to the eye: a row says all of what MCP has.
        cut = [clamp for region in shown["regions"] for clamp in region["clamps"] if clamp["cut"]]
        assert cut, "nothing is cut, so the rows were not as long as the owner's"
        assert all(clamp["title"].strip() for clamp in cut), cut[:3]
        first_note = page.locator(f"tr#record-note-{case.notes[0]} .cell-title .clamp")
        assert await first_note.get_attribute("title") == (
            f"{long_note['text']}\n\nWhy: {long_note['why']}"
        )
        # The ended ones are in their own closed table and look ended.
        ended = [row for region in shown["regions"] for row in region["rows"] if row["ended"]]
        assert len(ended) == 2, ended


@pytest.mark.parametrize("screen", [WIDE, PHONE], ids=["1600", "390"])
@themes
async def test_an_epic_with_nothing_linked_shows_the_project_wide_notes_open_and_in_full(
    server_url: str, theme: str, screen: Screen
) -> None:
    case = await the_owner_case(server_url)
    async with layout_page(case.board, screen, theme) as w:
        page = w.page
        await open_address(w, "#/board")
        await go_to(
            w,
            page_address(case.empty_epic),
            f"#item-page[data-ref='{case.empty_epic}'] [data-part=context] .recall-section",
        )
        shown = await page.evaluate(OWNER)
        assert shown["disclosures"]["project_wide_notes"]["open"] is True, shown["disclosures"]
        rows = [region for region in shown["regions"] if region["label"].startswith("Project-wide")]
        assert len(rows) == 1 and len(rows[0]["rows"]) == 9, rows
        assert abs(rows[0]["outer"]["l"] - shown["details"]["l"]) <= TOLERANCE
        assert abs(rows[0]["outer"]["r"] - shown["details"]["r"]) <= TOLERANCE
        assert len({round(row["h"]) for row in rows[0]["rows"]}) <= 2
        assert not [c for c in shown["regions"] if c["label"].startswith("Notes in force")]
        assert shown["scroll"][0] <= shown["scroll"][1]


# -- 10. every screen can be looked at, in both themes ----------------------------------------


@themes
async def test_every_screen_is_drawn_and_can_be_kept_as_a_screenshot_to_look_at(
    server_url: str, theme: str
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, WIDE, theme) as w:
        page = w.page
        await open_address(w, "#/board")
        for stop in stops(world):
            await visit(w, stop)
            assert await page.locator("#page, .entry").first.inner_text(), stop.name
            await shoot(page, f"{stop.name}-{theme}")
        # Representative dialogs and menus.
        await visit(w, stops(world)[0])
        await page.locator("#new-item").click()
        await page.wait_for_selector("dialog.modal[open]")
        await shoot(page, f"dialog-new-item-{theme}")
        await page.keyboard.press("Escape")
        await page.evaluate("(h) => { location.hash = h; }", "#/lists?view=notes")
        await page.wait_for_selector("#add-note", timeout=SLOW)
        await page.locator("#add-note").click()
        await page.wait_for_selector("dialog.modal[open]")
        await shoot(page, f"dialog-add-note-{theme}")
        await page.keyboard.press("Escape")
        await visit(w, stops(world)[0])
    # What a phone shows: the Board, the Graph, the Roadmap, a record, and the menu.
    async with layout_page(world.board, PHONE, theme) as w:
        page = w.page
        await open_address(w, "#/board")
        for stop in [s for s in stops(world) if s.name in PHONE_SCREENS]:
            await visit(w, stop)
            await shoot(page, f"phone-{stop.name}-{theme}")
        await visit(w, stops(world)[0])
        await page.locator("#sidebar-toggle").click()
        await page.wait_for_selector("#sidebar.open")
        await frames(page)
        await shoot(page, f"phone-sidebar-{theme}")
    async with layout_page(world.board, WIDE, theme, signed_in=False) as w:
        await w.page.goto(f"{w.origin}/board")
        await w.page.wait_for_selector("#sign-in-form")
        await shoot(w.page, f"sign-in-{theme}")
        await w.page.locator("#show-register").click()
        await w.page.wait_for_selector("#register-form")
        await shoot(w.page, f"register-{theme}")
    assert SHOTS is None or any(SHOTS.glob(f"*-{theme}.png"))
