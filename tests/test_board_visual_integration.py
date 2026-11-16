"""M17's visual-quality proof (SPEC, Proof 2, the part that can be measured), against the running
containers in a real headless Chromium, in Light and in Dark, with real records of varied lengths
made through MCP (long titles, a title with no place to break, a long source, dense and sparse
columns, an epic with nothing in it, an epic that is all done). Screenshots alone prove nothing
here: the sizes and places of what the page draws are read from the page and compared with each
other, with what MCP says, and with the structure of the approved references (docs/design/m17).

Set VRUM_LAYOUT_SHOTS to a folder outside the repository to also keep screenshots to look at."""

import re
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import expect

from tests.board_helpers import SLOW
from tests.board_layout import (
    BY_NAME,
    OLD_GLYPHS,
    SHOTS,
    THEMES,
    UNBROKEN,
    World,
    cards,
    frames,
    graph,
    layout_page,
    open_address,
    settle,
    shoot,
    stops,
    the_world,
    visit,
)
from tests.helpers import all_nodes, connect, ok

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

themes = pytest.mark.parametrize("theme", THEMES)
WIDE = BY_NAME["1600"]
PHONE = BY_NAME["390"]
TOLERANCE = 1.0  # CSS pixels: two edges that are meant to line up differ by no more than this
PHONE_SCREENS = {"board", "graph-work-map", "graph-context-list", "roadmap", "panel-decision"}


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


# -- 2. headers, toolbars and panel fields line up with what is under them -------------------

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
    attention: left(document.querySelector('#attention')),
    toolbar: left(document.querySelector('.board-toolbar')),
    notes: left(document.querySelector('.board-notes')),
    frame: left(document.querySelector('.board-frame')),
  };
  const heights = [...document.querySelectorAll(
    '.board-toolbar input, .board-toolbar select, .board-toolbar button')]
    .filter((e) => e.checkVisibility())
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


PANEL_ALIGNMENT = """
() => [...document.querySelectorAll('#panel dl.detail-grid')].map((grid) => {
  const rows = [...grid.querySelectorAll(':scope > dt')].map((dt) => {
    const dd = dt.nextElementSibling;
    const d = dt.getBoundingClientRect(); const v = dd.getBoundingClientRect();
    const walker = document.createTreeWalker(dd, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let first = v;
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (n.nodeType === 1 && ['SELECT', 'INPUT', 'BUTTON'].includes(n.tagName)) {
        first = n.getBoundingClientRect(); break;
      }
      if (n.nodeType === 3 && n.nodeValue.trim()) {
        const r = document.createRange(); r.selectNodeContents(n);
        first = r.getClientRects()[0] || v; break;
      }
    }
    const label = document.createRange(); label.selectNodeContents(dt);
    const l = label.getClientRects()[0] || d;
    return { dt: d.left, dd: v.left, name: dt.textContent,
      drift: Math.abs((first.top + first.height / 2) - (l.top + l.height / 2)),
      stacked: v.top >= d.bottom - 1 && v.left <= d.left + 1 };
  });
  return rows;
})
"""


@pytest.mark.parametrize("screen", [WIDE, BY_NAME["1280"]], ids=["1600", "1280"])
@themes
async def test_panel_labels_and_values_line_up_in_columns_and_on_one_line(
    server_url: str, theme: str, screen: Any
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, theme) as w:
        await open_address(w, "#/board")
        for name in ("D1", "S2", "X1", "Y1"):
            kind = (
                "decision" if name.startswith("X") else "fact" if name.startswith("Y") else "node"
            )
            await w.page.evaluate(
                "(h) => { location.hash = h; }", f"#/board?sel={kind}:{world[name]}"
            )
            await w.page.wait_for_selector("#panel dl.detail-grid", state="attached", timeout=SLOW)
            await settle(w.page)
            grids = await w.page.evaluate(PANEL_ALIGNMENT)
            assert grids, name
            for rows in grids:
                assert rows
                assert (
                    max(row["dt"] for row in rows) - min(row["dt"] for row in rows) <= TOLERANCE
                ), rows
                assert (
                    max(row["dd"] for row in rows) - min(row["dd"] for row in rows) <= TOLERANCE
                ), rows
                for row in rows:
                    assert row["stacked"] or row["drift"] <= 4, (name, row)


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
    id: Number(owner.dataset.epic || owner.dataset.id),
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


async def truth(client: Client) -> dict[int, tuple[int, int]]:
    """Tasks done out of tasks that are not dropped, for every epic and story, counted from the
    tasks themselves (and checked against what list_nodes says of each)."""
    nodes = await all_nodes(client)
    by_id = {node["id"]: node for node in nodes}
    found: dict[int, tuple[int, int]] = {}
    for node in nodes:
        if node["kind"] == "task":
            continue
        tasks = [
            task
            for task in nodes
            if task["kind"] == "task"
            and task["status"] != "dropped"
            and (
                task["parent_id"] == node["id"]
                if node["kind"] == "story"
                else by_id[task["parent_id"]]["parent_id"] == node["id"]
                and by_id[task["parent_id"]]["status"] != "dropped"
            )
        ]
        done = sum(1 for task in tasks if task["status"] == "done")
        assert (node["progress_done"], node["progress_total"]) == (done, len(tasks)), node
        found[node["id"]] = (done, len(tasks))
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
        lanes = {meter["id"]: meter for meter in meters if meter["kind"] == "lane"}
        assert set(lanes) == {world[name] for name in ("E1", "E2", "E3", "E4", "E5", "E6")}
        for ident, meter in {
            **lanes,
            **{m["id"]: m for m in meters if m["kind"] == "card"},
        }.items():
            done, total = real[ident]
            where = (meter["kind"], ident, meter)
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
        # The epic list and the Roadmap or the panel say the same numbers.
        await w.page.evaluate("(h) => { location.hash = h; }", f"#/board?sel=node:{world['E5']}")
        await w.page.wait_for_selector("#panel .progress .meter", timeout=SLOW)
        panel = await w.page.locator("#panel .progress").inner_text()
        assert "2 of 2" in panel


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
        # The cards under it are not pushed over by it: a long title waits where it is.
        assert found[world["D3"]]["note"] is not None


# -- 6. the map: labels inside their nodes, no collisions ------------------------------------


def meets(first: dict[str, float], second: dict[str, float]) -> bool:
    """Whether two boxes (l, t, r, b) share more than a hairline."""
    wide = min(first["r"], second["r"]) - max(first["l"], second["l"])
    high = min(first["b"], second["b"]) - max(first["t"], second["t"])
    return wide > 0.5 and high > 0.5


async def open_map(w: Any, world: World, address: str, *, panel: str = "") -> dict[str, Any]:
    await w.page.evaluate("(h) => { location.hash = h; }", f"{address}{panel}")
    await w.page.wait_for_selector("svg#graph-svg[data-ready=true]", timeout=SLOW)
    await settle(w.page)
    return await graph(w.page)


@pytest.mark.parametrize("screen", [WIDE, BY_NAME["1280"]], ids=["1600", "1280"])
@themes
async def test_node_labels_stay_inside_their_nodes_and_nodes_and_lines_do_not_collide(
    server_url: str, theme: str, screen: Any
) -> None:
    world = await the_world(server_url)
    scopes = [
        ("E1 work", f"#/graph?view=work&as=map&epic={world['E1']}", "layered"),
        ("E1 context", f"#/graph?view=context&as=map&epic={world['E1']}", "layered"),
        ("E1 everything", f"#/graph?view=everything&as=map&epic={world['E1']}", "layered"),
        ("E2 everything", f"#/graph?view=everything&as=map&epic={world['E2']}", "layered"),
        ("E3 everything", f"#/graph?view=everything&as=map&epic={world['E3']}", "layered"),
        ("E5 work", f"#/graph?view=work&as=map&epic={world['E5']}", "layered"),
        ("all epics", "#/graph?view=everything&as=map", "compact"),
    ]
    async with layout_page(world.board, screen, theme) as w:
        await open_address(w, "#/board")
        problems: list[str] = []
        cut: set[str] = set()
        for name, address, mode in scopes:
            drawn = await open_map(w, world, address)
            assert drawn["layout"] == mode, (name, drawn["layout"])
            dots = drawn["dots"]
            assert len(dots) >= 3, name
            canvas = drawn["furniture"]["canvas"]
            for dot in dots:
                inside = dot["local"]
                for part in dot["inside"]:
                    if (
                        part["x"] < inside["x"] - 1
                        or part["y"] < inside["y"] - 1
                        or part["x"] + part["w"] > inside["x"] + inside["w"] + 1
                        or part["y"] + part["h"] > inside["y"] + inside["h"] + 1
                    ):
                        problems.append(
                            f"{name}: {part['what']!r} leaves its node {dot['label'][:40]}"
                        )
                # What is cut short with an ellipsis is read in full in the panel (checked below).
                words = [part["what"] for part in dot["inside"] if str(part["what"]).endswith("…")]
                if words:
                    cut.add(dot["key"])
                shape = dot["shape"]
                if not (
                    shape["l"] >= canvas["l"] - 1
                    and shape["r"] <= canvas["r"] + 1
                    and shape["t"] >= canvas["t"] - 1
                    and shape["b"] <= canvas["b"] + 1
                ):
                    problems.append(f"{name}: {dot['label'][:40]} is outside the map after Fit")
            # No two nodes overlap.
            for at, first in enumerate(dots):
                for second in dots[at + 1 :]:
                    one, other = first["at"], second["at"]
                    if meets(
                        {
                            "l": one["x"],
                            "t": one["y"],
                            "r": one["x"] + one["w"],
                            "b": one["y"] + one["h"],
                        },
                        {
                            "l": other["x"],
                            "t": other["y"],
                            "r": other["x"] + other["w"],
                            "b": other["y"] + other["h"],
                        },
                    ):
                        problems.append(
                            f"{name}: {first['label'][:30]} overlaps {second['label'][:30]}"
                        )
            # No line runs through a node that is not one of its two ends.
            if mode == "layered":
                for edge in drawn["edges"]:
                    ends = {edge["source"], edge["target"]}
                    for other in dots:
                        if other["key"] in ends:
                            continue
                        box = other["at"]
                        through = [
                            point
                            for point in edge["points"]
                            if box["x"] + 1 < point[0] < box["x"] + box["w"] - 1
                            and box["y"] + 1 < point[1] < box["y"] + box["h"] - 1
                        ]
                        if through:
                            problems.append(
                                f"{name}: the {edge['type']} line {edge['source']} "
                                f"to {edge['target']} runs through {other['label'][:30]}"
                            )
            # The legend and the zoom are under the map, not on it, and do not touch each other.
            furniture = drawn["furniture"]
            for part in ("legend", "zoom", "controls", "hint"):
                box = furniture[part]
                if box is not None and box["t"] < canvas["b"] - 0.5:
                    problems.append(f"{name}: the {part} is on the map")
            if (
                furniture["legend"]
                and furniture["controls"]
                and meets(furniture["legend"], furniture["controls"])
            ):
                problems.append(f"{name}: the legend and the zoom controls overlap")
            # Zoomed far out, the page says so, and says how to read the labels; a map of a few
            # nodes is fitted at a size that can be read without that.
            zoom = float(drawn["transform"].split("scale(")[1].rstrip(")"))
            if mode == "layered" and zoom < 0.5:
                await expect(w.page.locator(".gm-zoom-note")).to_be_visible()
            if name in {"E2 everything", "E5 work"}:
                assert zoom >= 0.9, f"{name} is fitted at {zoom:.2f}: its words are too small"
        assert not problems, "\n".join(problems)
        # A node whose words are cut short opens a panel that has the whole of them.
        assert f"fact:{world['Y2']}" in cut, "the long fact is drawn cut short"
        await open_map(w, world, f"#/graph?view=everything&as=map&epic={world['E3']}")
        await w.page.locator(f"svg#graph-svg .dot[data-key='fact:{world['Y2']}']").focus()
        await w.page.keyboard.press("Enter")
        await expect(w.page.locator("#panel .record-text")).to_contain_text(UNBROKEN, timeout=SLOW)


@pytest.mark.parametrize("screen", [WIDE, BY_NAME["1280"]], ids=["1600", "1280"])
@themes
async def test_the_panel_beside_the_map_does_not_cover_the_map_or_its_furniture(
    server_url: str, theme: str, screen: Any
) -> None:
    world = await the_world(server_url)
    async with layout_page(world.board, screen, theme) as w:
        await open_address(w, "#/board")
        drawn = await open_map(
            w,
            world,
            f"#/graph?view=everything&as=map&epic={world['E1']}",
            panel=f"&sel=decision:{world['X1']}",
        )
        await w.page.wait_for_selector("#panel .detail", timeout=SLOW)
        await settle(w.page)
        drawn = await graph(w.page)
        furniture = drawn["furniture"]
        panel = furniture["panel"]
        assert panel is not None
        for part in ("canvas", "legend", "zoom", "controls", "caption"):
            box = furniture[part]
            assert box is not None, part
            assert not meets(box, panel), f"the {part} is under the panel: {box} {panel}"
        for dot in drawn["dots"]:
            assert not meets(dot["shape"], panel), dot["label"]
        # The chosen node is marked, and its record is what the panel shows.
        chosen = w.page.locator(f"svg#graph-svg .dot.chosen[data-key='decision:{world['X1']}']")
        await expect(chosen).to_have_count(1)
        assert "Claims expire" in await w.page.locator("#record-title").inner_text()


# -- 7. the structure of the approved references, with the real numbers ----------------------


@themes
async def test_the_board_has_the_structure_of_the_reference_with_the_real_numbers(
    server_url: str, theme: str
) -> None:
    world = await the_world(server_url)
    async with connect(server_url, world.project.key) as client:
        nodes = await all_nodes(client)
        held = (await ok(client, "list_rules", status="held", limit=1))["total"]
        settled = (await ok(client, "list_rules", status="settled", limit=1))["total"]
        decisions = (await ok(client, "list_decisions", status="active", limit=1))["total"]
        facts = (await ok(client, "list_facts", status="active", limit=1))["total"]
    work = [node for node in nodes if node["kind"] != "epic"]
    expected_counts = [
        str(len(work)),
        str(len(nodes) + held + settled + decisions + facts),
        str(sum(1 for node in nodes if node["span_start"] or node["span_end"])),
    ]
    epics = sorted((node for node in nodes if node["kind"] == "epic"), key=lambda n: n["id"])
    by_status = {
        status: 0 for status in ("new", "ready", "in_progress", "blocked", "done", "dropped")
    }
    for node in work:
        by_status[node["status"]] += 1
    assert sum(by_status.values()) == len(work) and by_status["ready"] > 10
    async with layout_page(world.board, WIDE, theme) as w:
        page = w.page
        await open_address(w, "#/board")
        await settle(page)
        await shoot(page, f"board-{theme}")
        # The sidebar: the three views, each with an icon, its word and its real count.
        links = page.locator("#main-nav a[data-page]")
        assert await links.count() == 3
        words = [await links.nth(at).locator(".side-label").inner_text() for at in range(3)]
        assert words == ["Board", "Graph", "Roadmap"]
        counts = [await links.nth(at).locator(".nav-count").inner_text() for at in range(3)]
        assert counts == expected_counts, (counts, expected_counts)
        for at, name in enumerate(("board", "graph", "roadmap")):
            assert await links.nth(at).locator(f"svg.icon-{name}").count() == 1
        await expect(links.nth(0)).to_have_attribute("aria-current", "page")
        await expect(links.nth(1)).not_to_have_attribute("aria-current", "page")
        # Its Epics are the real epics, in order, each with its layered icon.
        assert await page.locator("#epics-heading").text_content() == "Epics"
        listed = page.locator("#epic-list a[data-epic]")
        assert [
            await listed.nth(at).get_attribute("data-epic") for at in range(await listed.count())
        ] == [str(epic["id"]) for epic in epics]
        assert [await listed.nth(at).inner_text() for at in range(await listed.count())] == [
            epic["title"] for epic in epics
        ]
        assert await page.locator("#epic-list svg.icon-epic").count() == len(epics)
        # At its foot: Activity, Settings, and the appearance control with the theme chosen.
        await expect(page.locator("#sidebar #activity-open")).to_be_visible()
        await expect(page.locator("#sidebar #settings-menu")).to_be_visible()
        choices = page.locator("#sidebar [role=radiogroup][aria-label=Appearance] input")
        assert [await choices.nth(at).get_attribute("data-appearance") for at in range(3)] == [
            "light",
            "dark",
            "system",
        ]
        await expect(page.locator(f"#sidebar input[data-appearance={theme}]")).to_be_checked()
        # The top: where you are, and the search with its shortcut.
        crumbs = [text.strip() for text in await page.locator(".crumbs li").all_inner_texts()]
        assert crumbs == ["Projects", world.project.name, "Board"], crumbs
        assert (
            await page.locator("#global-search").get_attribute("placeholder")
            == "Search work & context…"
        )
        assert await page.locator("#switcher-open kbd").inner_text() in {"Ctrl K", "⌘K"}
        # The page: its name, what it is for, and its one main action with a plus.
        assert await page.locator(".board-page h1").inner_text() == "Board"
        assert (await page.locator(".board-page .page-sub").inner_text()).strip()
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
        assert "computed" in await heads.nth(1).inner_text()
        # A lane for each epic, with its progress and its Add story button.
        lanes = page.locator(".lane")
        assert await lanes.count() == len(epics)
        for epic in epics:
            lane = page.locator(f".lane[data-epic='{epic['id']}']")
            assert await lane.locator(".lane-title").inner_text() == epic["title"]
            assert await lane.locator(".lane-head .id").inner_text() == f"#{epic['id']}"
            assert await lane.locator(".add-story").count() == 1
            assert "task" in await lane.locator(".progress-text").inner_text()
        # The Graph: the three views, the epic, Map and List, the legend, the zoom, and the panel.
        await w.page.evaluate(
            "(h) => { location.hash = h; }",
            f"#/graph?view=everything&as=map&epic={world['E1']}&sel=decision:{world['X1']}",
        )
        await page.wait_for_selector("svg#graph-svg[data-ready=true]", timeout=SLOW)
        await page.wait_for_selector("#panel .detail", timeout=SLOW)
        await settle(page)
        await shoot(page, f"graph-panel-{theme}")
        assert await page.locator(".graph-page h1").inner_text() == "Graph"
        assert [t.strip() for t in await page.locator("#graph-views button").all_inner_texts()] == [
            "Work",
            "Context",
            "Everything",
        ]
        await expect(page.locator("#graph-view-everything")).to_have_attribute(
            "aria-pressed", "true"
        )
        assert [
            t.strip() for t in await page.locator("#graph-presentations button").all_inner_texts()
        ] == [
            "Map",
            "List",
        ]
        assert await page.locator("#graph-epic").evaluate(
            "(s) => s.selectedOptions[0].textContent"
        ) == (f"#{world['E1']} Run and see it all")
        legend = await page.locator("#graph-legend").inner_text()
        assert "Hierarchy" in legend and "Context link" in legend
        assert re.fullmatch(r"\d+%", await page.locator("#zoom-level").inner_text())
        assert await page.get_by_role("button", name="Zoom in").count() == 1
        assert await page.get_by_role("button", name="Zoom out").count() == 1
        drawn = await graph(page)
        assert re.search(
            rf"\b{len(drawn['dots'])} records?\b", await page.locator("#graph-scope").inner_text()
        )
        # The panel is what MCP says of the decision: kind, number, state, title, text, reason.
        async with connect(server_url, world.project.key) as client:
            record = await ok(client, "get_decision", decision_id=world["X1"])
        label = (await page.locator("#panel .detail-label").inner_text()).strip().lower()
        assert label.endswith("decision") and f"#{world['X1']}" in label, label
        assert (await page.locator("#record-title").inner_text()).strip().endswith(record["title"])
        assert (
            "Confirmed"
            in await page.locator("#panel .chips, #panel .record-chips").first.inner_text()
        )
        assert record["text"] in await page.locator("#panel [data-part=record]").inner_text()
        assert record["reason"] in await page.locator("#panel [data-part=reason]").inner_text()
        assert await page.locator("#panel .detail-label svg.icon").count() == 1
        rows = [
            text.strip()
            for text in await page.locator("#panel dl.detail-grid dt").all_inner_texts()
        ]
        assert rows[:2] == ["Confirmation", "Applies to"], rows


# -- 8. every screen can be looked at, in both themes -----------------------------------------


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
        await page.evaluate("(h) => { location.hash = h; }", "#/graph?view=context&as=list")
        await page.wait_for_selector("#record-fact", timeout=SLOW)
        await page.locator("#record-fact").click()
        await page.wait_for_selector("dialog.modal[open]")
        await shoot(page, f"dialog-record-fact-{theme}")
        await page.keyboard.press("Escape")
        await visit(w, stops(world)[0])
        await page.locator(".card .move").first.click()
        await page.wait_for_selector(".move-menu")
        await shoot(page, f"menu-move-{theme}")
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
