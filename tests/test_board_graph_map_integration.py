"""M38: how the living Structure map of the Graph moves, in a real headless browser against the real
containers (see ./check.sh). The map is a force simulation (graph-sim.js, whose physics is tested
on its own in test_board_graph_sim_integration.py) drawn by graph-map.js, so no coordinate is a
constant: every test waits for `data-resting="true"` on the svg and asserts relations (dots do not
overlap, epics are at different distances, a task is farther out than its story, a dragged dot
pulls what hangs on it and lets the far epics alone, and everything comes back when it is let
go) or measures a number against a margin. The world is a project of its own with the demo's shape
(12 epics, 53 stories, 180 tasks) so that the numbers mean what they mean on the demo.
test_board_graph_integration.py has what the Structure map draws, compared with the tools."""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_context import fresh_project
from tests.board_helpers import SLOW, Watched, browser_page, go, made
from tests.helpers import ok

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

MAP = "#graph-view[data-tab=structure] svg.graph-st-svg"
WORD = {"l": "low", "n": "normal", "h": "high", "u": "urgent"}

# The demo project's hierarchy: an epic's priority and, for each story, its priority followed by the
# priority of each of its tasks (l low, n normal, h high, u urgent).
DEMO = [
    ("h", ["hhnnnnnh", "huhnul", "nnnnnln", "nnnl", "hnnhnhl", "hnhnl", "n", "nhn", "nnnn"]),
    ("h", ["hnnnhnhhnnlnl", "nnn", "nnnnnn", "llll", "hnhnn", "nnnnl"]),
    ("n", ["nnnl", "hnhhnnl", "nnnnl", "nnnl", "lll"]),
    ("h", ["uhhhnhhn", "nhnnnl", "nnnl", "nnnl", "n", "hhnnn"]),
    ("n", ["hhnnnln", "l", "nnnn", "nnnl", "hhh"]),
    ("h", ["nnnnl", "unhhnnn", "nnnl", "lll", "nnnnnn", "lll", "nnn"]),
    ("u", ["uhunnl", "llll", "hhh", "n"]),
    ("n", ["nnnhl", "nnnnn", "l"]),
    ("h", ["hnnnn", "nnnl", "nnhn"]),
    ("n", ["nnnn", "n", "lll", "nnnn"]),
    ("n", ["n"]),
    ("l", []),
]

# Every dot as [x, y, radius] in the map's own units, from its transform (the project is the disc
# in the middle). Positions are read from the page, and are the same units the simulation uses.
PLACES = r"""() => {
  const svg = document.querySelector('svg.graph-st-svg');
  const found = {};
  for (const g of svg.querySelectorAll('g.graph-st-node')) {
    const at = /translate\(([-\d.e]+) ([-\d.e]+)\)/.exec(g.getAttribute('transform') || '');
    found[g.dataset.ref] = [at ? Number(at[1]) : 0, at ? Number(at[2]) : 0,
      Number(g.querySelector('.graph-st-body').getAttribute('r'))];
  }
  found.root = [0, 0, Number(svg.querySelector('.graph-st-project-body').getAttribute('r'))];
  return found;
}"""

# How many of the tree's own lines cross another one (a line from a parent to what hangs on it, as
# drawn: the end points are on the edges of the dots, so lines that share a dot do not cross).
CROSSINGS = r"""() => {
  const lines = [...document.querySelectorAll('svg.graph-st-svg .graph-st-line')]
    .map((l) => ['x1', 'y1', 'x2', 'y2'].map((a) => Number(l.getAttribute(a))));
  const side = (ax, ay, bx, by, cx, cy) => (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
  const apart = (p, q, a, b) => side(...p, ...q, ...a) * side(...p, ...q, ...b) < 0;
  let count = 0;
  for (let i = 0; i < lines.length; i += 1) {
    const [p, q] = [lines[i].slice(0, 2), lines[i].slice(2)];
    for (let j = i + 1; j < lines.length; j += 1) {
      const [a, b] = [lines[j].slice(0, 2), lines[j].slice(2)];
      if (apart(p, q, a, b) && apart(a, b, p, q)) count += 1;
    }
  }
  return count;
}"""

# The length of the tree's own lines as drawn, in map units.
LINE_LENGTHS = r"""() => [...document.querySelectorAll('svg.graph-st-svg .graph-st-line')]
  .map((l) => {
    const [x1, y1, x2, y2] = ['x1', 'y1', 'x2', 'y2'].map((a) => Number(l.getAttribute(a)));
    return Math.hypot(x2 - x1, y2 - y1);
  })"""

# What the tidy layout (graph-radial.js, the page's own module) puts each dot at, for a tree.
TIDY = """async (tree) => {
  const { wholeLayout } = await import('/board/graph-radial.js');
  const found = wholeLayout(tree, 'tasks').pos;
  return Object.fromEntries([...found].map(([ref, p]) => [ref, [p.x, p.y]]));
}"""

# How many dots are outside the picture.
OUTSIDE = r"""() => {
  const svg = document.querySelector('svg.graph-st-svg');
  const box = svg.getBoundingClientRect();
  let count = 0;
  for (const g of svg.querySelectorAll('g.graph-st-node')) {
    const r = g.querySelector('.graph-st-body').getBoundingClientRect();
    const x = r.left + r.width / 2;
    const y = r.top + r.height / 2;
    if (x < box.left || x > box.right || y < box.top || y > box.bottom) count += 1;
  }
  return count;
}"""

# The transform of every dot, by reference: where the map draws it, to the tenth of a unit.
TRANSFORMS = "els => Object.fromEntries(els.map(e => [e.dataset.ref, e.getAttribute('transform')]))"

# Before the page's own scripts run: how often a frame is asked for, and every value
# `data-resting` has had (the first one is what the map was when it was first in the page, with
# where each dot was then).
WATCH = r"""
window.__frames = 0;
const ask = window.requestAnimationFrame.bind(window);
window.requestAnimationFrame = (callback) => { window.__frames += 1; return ask(callback); };
window.__rest = [];
window.__first = null;
new MutationObserver((records) => {
  for (const record of records) {
    if (record.type === 'attributes') {
      window.__rest.push(record.target.getAttribute('data-resting'));
    }
  }
  const svg = document.querySelector('svg.graph-st-svg');
  if (svg && !window.__first) {
    const dots = [...svg.querySelectorAll('g.graph-st-node')];
    window.__first = { resting: svg.getAttribute('data-resting'),
      at: Object.fromEntries(dots.map((g) => [g.dataset.ref, g.getAttribute('transform')])) };
  }
}).observe(document, {
  attributes: true, attributeFilter: ['data-resting'], childList: true, subtree: true });
"""

Places = dict[str, list[float]]

# The most of the tree's lines that may cross another on the settled live map of the demo's shape:
# it settles with 8 crossings (an epic's line to the project passing another epic's dots) and the
# limit leaves room for the simulation's tuning.
CROSSINGS_MOST = 14


@dataclass
class Demo:
    """The made project: each item's reference by its plan key, and what hangs from what."""

    ref: dict[str, str]
    parent: dict[str, str]
    epics: list[str]
    stories: list[str]
    tasks: list[str]
    priority: dict[str, str]

    def kids(self, ref: str) -> list[str]:
        return [one for one, up in self.parent.items() if up == ref]

    def epic_of(self, ref: str) -> str:
        while ref not in self.epics:
            ref = self.parent[ref]
        return ref

    def tree(self) -> list[dict[str, Any]]:
        """The hierarchy as graph-radial.js takes it."""
        return [
            {
                "ref": epic,
                "priority": self.priority[epic],
                "stories": [
                    {
                        "ref": story,
                        "priority": self.priority[story],
                        "tasks": [
                            {"ref": task, "priority": self.priority[task]}
                            for task in self.kids(story)
                        ],
                    }
                    for story in self.kids(epic)
                ],
            }
            for epic in self.epics
        ]


async def demo_world(client: Client, word: str) -> Demo:
    """A project of the demo's shape: 12 epics, 53 stories and 180 tasks, with the demo's
    priorities, and one link of the kind that lights a partner, between the first task of the first
    epic and the first task of the second (the plan keys `t1-1-1` and `t2-1-1`)."""
    items: list[dict[str, Any]] = []
    for e, (priority, stories) in enumerate(DEMO, start=1):
        items.append(
            {
                "key": f"e{e}",
                "kind": "epic",
                "title": f"Epic {e} {word}",
                "priority": WORD[priority],
            }
        )
        for s, text in enumerate(stories, start=1):
            items.append(
                {
                    "key": f"s{e}-{s}",
                    "kind": "story",
                    "title": f"Story {e}-{s} {word}",
                    "parent": f"e{e}",
                    "priority": WORD[text[0]],
                }
            )
            for t, letter in enumerate(text[1:], start=1):
                items.append(
                    {
                        "key": f"t{e}-{s}-{t}",
                        "kind": "task",
                        "title": f"Task {e}-{s}-{t} {word}",
                        "parent": f"s{e}-{s}",
                        "priority": WORD[letter],
                    }
                )
    links = [{"source": "t1-1-1", "target": "t2-1-1", "link_type": "relates_to"}]
    made_ = await ok(client, "create_plan", items=items, links=links)
    ref = {one["key"]: one["ref"] for one in made_["created"]}
    parents = {ref[one["key"]]: ref[one["parent"]] for one in items if "parent" in one}
    return Demo(
        ref=ref,
        parent=parents,
        epics=[ref[one["key"]] for one in items if one["kind"] == "epic"],
        stories=[ref[one["key"]] for one in items if one["kind"] == "story"],
        tasks=[ref[one["key"]] for one in items if one["kind"] == "task"],
        priority={ref[one["key"]]: one["priority"] for one in items},
    )


# -- opening, waiting, reading --------------------------------------------------------------------


async def open_map(w: Watched, address: str = "#/graph") -> None:
    """Open the Graph on its Structure map, loaded afresh, and wait until the map is drawn, the
    page has stopped asking for more, and the map is at rest."""
    await go(w.page, w.board, address)
    await expect(w.page.locator(MAP)).to_be_visible(timeout=SLOW)
    await w.page.wait_for_load_state("networkidle")
    await at_rest(w.page)


async def at_rest(page: Page) -> None:
    """Wait until the map says it rests. Whatever touches the map says it is moving at once, in the
    same turn of the event loop, so this is never a look before the map has noticed."""
    await expect(page.locator(MAP)).to_have_attribute("data-resting", "true", timeout=SLOW)


async def places(page: Page) -> Places:
    found: Places = await page.evaluate(PLACES)
    return found


def far(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def drift(before: Places, after: Places, refs: list[str] | None = None) -> dict[str, float]:
    """How far each dot (of `refs`, by default all that are in both) is from where it was."""
    return {ref: far(before[ref], after[ref]) for ref in (refs or before) if ref in after}


def least_gap(found: Places) -> float:
    """The least space between the drawn discs of any two dots, the project's included (negative
    when two overlap)."""
    items = list(found.values())
    return min(far(a, b) - a[2] - b[2] for index, a in enumerate(items) for b in items[index + 1 :])


async def screen_of(page: Page, ref: str) -> tuple[float, float]:
    """Where a dot is in the window, the middle of its disc."""
    box = await page.locator(
        f"{MAP} g.graph-st-node[data-ref='{ref}'] .graph-st-body"
    ).bounding_box()
    assert box is not None, ref
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


async def scale_of(page: Page, ref: str, radius: float) -> float:
    """Screen pixels for one unit of the map, from the width of a disc."""
    box = await page.locator(
        f"{MAP} g.graph-st-node[data-ref='{ref}'] .graph-st-body"
    ).bounding_box()
    assert box is not None
    return box["width"] / (2 * radius)


async def rest_log(page: Page) -> list[str]:
    log: list[str] = await page.evaluate("window.__rest")
    return log


async def clear_log(page: Page) -> None:
    await page.evaluate("window.__rest.length = 0")


async def frames(page: Page) -> int:
    count: int = await page.evaluate("window.__frames")
    return count


# -- at rest ------------------------------------------------------------------------------------


async def test_the_map_is_at_rest_before_its_first_paint_and_then_runs_nothing_and_keeps_its_shape(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        demo = await demo_world(client, "rest")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await page.add_init_script(WATCH)
            await open_map(w)
            # The map was already at rest, and every dot where it still is, the first time it was in
            # the page: it is worked out before it is drawn, so nothing jumps after the first paint.
            first = await page.evaluate("window.__first")
            assert first["resting"] == "true", first["resting"]
            now: dict[str, str] = await page.locator(f"{MAP} g.graph-st-node").evaluate_all(
                TRANSFORMS
            )
            assert first["at"] == now
            assert await rest_log(page) == []

            # Nothing runs while it rests: not one frame is asked for in two seconds.
            asked = await frames(page)
            await page.wait_for_timeout(2000)
            assert await frames(page) == asked
            assert await rest_log(page) == []

            # The shape of the settled map, as relations. Nothing overlaps, the project's disc
            # included, and the epics are at different distances from the middle (the tidy layout
            # puts them all on one ring).
            found = await places(page)
            assert len(found) == 1 + len(demo.epics) + len(demo.stories) + len(demo.tasks)
            assert least_gap(found) > 0.5, least_gap(found)
            reach = [math.hypot(*found[epic][:2]) for epic in demo.epics]
            mean = sum(reach) / len(reach)
            spread = math.sqrt(sum((one - mean) ** 2 for one in reach) / len(reach)) / mean
            assert spread >= 0.15, (spread, reach)

            # The leaves are outermost: nearly every task is farther out than its own story, and
            # on average epics are nearer the middle than stories, and stories than tasks.
            def out(ref: str) -> float:
                return math.hypot(*found[ref][:2])

            farther = [out(task) > out(demo.parent[task]) for task in demo.tasks]
            assert sum(farther) / len(farther) >= 0.9, sum(farther) / len(farther)
            average = [
                sum(out(ref) for ref in group) / len(group)
                for group in (demo.epics, demo.stories, demo.tasks)
            ]
            assert average[0] < average[1] < average[2], average

            # Each epic's sub-tree is its own place: its dots are nearer their own epic than any
            # other, and the discs of two sub-trees (their centre and furthest dot) are apart.
            own = []
            for ref in [*demo.stories, *demo.tasks]:
                nearest = min(demo.epics, key=lambda epic: far(found[ref], found[epic]))
                own.append(nearest == demo.epic_of(ref))
            assert sum(own) / len(own) >= 0.95, sum(own) / len(own)
            discs = []
            for epic in demo.epics:
                members = [
                    epic,
                    *[r for r in [*demo.stories, *demo.tasks] if demo.epic_of(r) == epic],
                ]
                cx = sum(found[r][0] for r in members) / len(members)
                cy = sum(found[r][1] for r in members) / len(members)
                discs.append((cx, cy, max(far(found[r], [cx, cy]) + found[r][2] for r in members)))
            for index, one in enumerate(discs):
                for other in discs[index + 1 :]:
                    assert far(one[:2], other[:2]) > one[2] + other[2], (one, other)

            # The tree's lines may cross where one epic's line to the middle passes another's dots,
            # but only a few do; the tidy layout (see the Tidy test) has none.
            crossings = await page.evaluate(CROSSINGS)
            assert crossings <= CROSSINGS_MOST, crossings

            # Two loads of the same data give the same map: three dots (an epic, a story and a
            # task) are where they were, to the tenth of a unit the page draws them at.
            keep = [demo.epics[3], demo.stories[20], demo.tasks[90]]
            marks = {ref: now[ref] for ref in keep}
            await open_map(w)
            again: dict[str, str] = await page.locator(f"{MAP} g.graph-st-node").evaluate_all(
                TRANSFORMS
            )
            assert {ref: again[ref] for ref in keep} == marks
            assert w.problems == [], w.problems


# -- dragging -----------------------------------------------------------------------------------


async def press_and_move(page: Page, ref: str, found: Places, dx: float, dy: float) -> None:
    """Press on a dot and move the pointer by (dx, dy) map units in thirty moves, with the button
    still down."""
    scale = await scale_of(page, ref, found[ref][2])
    x, y = await screen_of(page, ref)
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + dx * scale, y + dy * scale, steps=30)


def toward_the_middle(found: Places, ref: str, units: float) -> tuple[float, float]:
    """A step of `units` map units from a dot straight toward the middle of the map."""
    x, y = found[ref][:2]
    length = math.hypot(x, y)
    return -x / length * units, -y / length * units


async def test_dragging_a_task_pulls_what_hangs_on_it_and_the_far_epics_barely_move_and_it_returns(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        demo = await demo_world(client, "taskdrag")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await page.add_init_script(WATCH)
            await open_map(w)
            before = await places(page)
            task = demo.tasks[3]
            story = demo.parent[task]
            siblings = [one for one in demo.kids(story) if one != task]
            assert len(siblings) >= 3
            epic = demo.epic_of(task)
            others = sorted(
                (one for one in demo.epics if one != epic),
                key=lambda one: far(before[one], before[task]),
            )
            dx, dy = toward_the_middle(before, task, 150)
            goal = [before[task][0] + dx, before[task][1] + dy]

            # Held 150 units away, the dot is where the pointer is, its story and its other tasks
            # have come toward it, the nearest epic has given way a little, and the farthest epic
            # has hardly moved. A dot being dragged shows no card.
            await clear_log(page)
            await press_and_move(page, task, before, dx, dy)
            await expect(page.locator(MAP)).to_have_attribute("data-resting", "false")
            await expect(page.locator(".graph-st-inner .graph-x-tip")).to_be_hidden()
            await page.wait_for_timeout(700)
            held = await places(page)
            assert far(held[task], goal) <= 3, (held[task], goal)
            moved = drift(before, held)
            assert far(held[story], goal) < far(before[story], goal) - 8, moved[story]
            closer = [far(held[one], goal) < far(before[one], goal) for one in siblings]
            assert sum(closer) >= len(siblings) / 2, closer
            assert moved[others[-1]] < 10 and moved[others[-1]] < moved[story] / 2, (
                moved[others[-1]],
                moved[story],
            )
            assert moved[others[-1]] < moved[others[0]] + 1, (moved[others[-1]], moved[others[0]])

            # Let go: it has not opened anything, the map moves for a while and rests, and every
            # dot is back where it was, the dragged one within three units.
            await page.mouse.up()
            await at_rest(page)
            await expect(page.locator("#item-page")).to_have_count(0)
            log = await rest_log(page)
            assert log[-1] == "true" and "false" in log, log
            after = await places(page)
            back = drift(before, after)
            assert back[task] <= 3, back[task]
            assert max(back.values()) <= 5, max(back.items(), key=lambda one: one[1])
            assert least_gap(after) > 0.5
            assert w.problems == [], w.problems


async def test_dragging_an_epic_takes_its_whole_sub_tree_and_escape_cancels_a_drag(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        demo = await demo_world(client, "epicdrag")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await page.add_init_script(WATCH)
            await open_map(w)
            before = await places(page)
            epic = demo.epics[4]
            inside = [ref for ref in [*demo.stories, *demo.tasks] if demo.epic_of(ref) == epic]
            far_epics = sorted(demo.epics, key=lambda one: far(before[one], before[epic]))[-3:]
            elsewhere = [
                ref for ref in [*demo.stories, *demo.tasks] if demo.epic_of(ref) in far_epics
            ]
            # Sideways round the middle, so that it does not run into the project's disc.
            x, y = before[epic][:2]
            length = math.hypot(x, y)
            dx, dy = -y / length * 150, x / length * 150

            await press_and_move(page, epic, before, dx, dy)
            await page.wait_for_timeout(700)
            held = await places(page)
            assert far(held[epic], [x + dx, y + dy]) <= 3, (held[epic], x + dx, y + dy)
            moved = drift(before, held)
            own = sum(moved[ref] for ref in inside) / len(inside)
            away = sum(moved[ref] for ref in elsewhere) / len(elsewhere)
            assert own >= 15 and away <= 8 and away < own / 3, (own, away)
            await page.mouse.up()
            await at_rest(page)
            back = drift(before, await places(page))
            assert max(back.values()) <= 5, max(back.items(), key=lambda one: one[1])
            await expect(page.locator("#item-page")).to_have_count(0)

            # Escape lets go of a dot that is held: it goes home at once, the pointer moving on
            # makes no difference, and letting go of the button afterwards opens nothing.
            task = demo.tasks[40]
            dx, dy = toward_the_middle(before, task, 120)
            await press_and_move(page, task, before, dx, dy)
            await page.wait_for_timeout(300)
            assert far((await places(page))[task], before[task]) > 60
            await page.keyboard.press("Escape")
            await page.mouse.move(40, 40, steps=10)
            await at_rest(page)
            after = await places(page)
            assert drift(before, after)[task] <= 3
            await page.mouse.up()
            await expect(page.locator("#item-page")).to_have_count(0)
            assert drift(before, await places(page))[task] <= 3
            assert w.problems == [], w.problems


async def test_a_press_that_hardly_moves_opens_the_item_and_one_that_drags_does_not(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        demo = await demo_world(client, "press")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await open_map(w)
            before = await places(page)
            task = demo.tasks[12]
            # Under five pixels is a click: the item's page opens, and the map is where it was.
            x, y = await screen_of(page, task)
            await page.mouse.move(x, y)
            await page.mouse.down()
            await page.mouse.move(x + 2, y + 2)
            await page.mouse.up()
            await expect(page.locator(f"#item-page[data-ref='{task}']")).to_be_visible(timeout=SLOW)
            await page.go_back()
            await at_rest(page)
            assert max(drift(before, await places(page)).values()) <= 1
            # Twelve pixels is a drag: it opens nothing, even when the button is let go over it.
            x, y = await screen_of(page, task)
            await page.mouse.move(x, y)
            await page.mouse.down()
            await page.mouse.move(x + 12, y, steps=6)
            await page.mouse.move(x, y, steps=6)
            await page.mouse.up()
            await expect(page.locator("#item-page")).to_have_count(0)
            await at_rest(page)
            assert w.problems == [], w.problems


async def test_the_browser_never_selects_text_on_the_map_whatever_the_pointer_does(
    server_url: str,
) -> None:
    """A quick second press, a drag that follows it and a selection that something else starts and
    stretches over the picture would paint the map and its card in the selection colour (seen in
    WebKit). The map lets none of it stand."""
    async with fresh_project(server_url) as (client, board, project):
        demo = await demo_world(client, "select")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await open_map(w)
            before = await places(page)
            task = demo.tasks[5]
            x, y = await screen_of(page, task)
            chosen = "() => { const s = getSelection(); return s ? s.toString() : ''; }"

            # Press, move out and back, let go, and press again at once and drag: word by word
            # selection is what a second press would start anywhere else on a page.
            await page.mouse.move(x, y)
            await page.mouse.down()
            await page.mouse.move(x + 14, y + 14, steps=4)
            await page.mouse.move(x, y, steps=4)
            await page.mouse.up()
            await page.mouse.down()
            await page.mouse.move(x - 90, y + 60, steps=20)
            assert await page.evaluate(chosen) == ""

            # A selection put on the picture from outside it, while a dot is held, is let go.
            await page.evaluate(
                """() => { const r = document.createRange();
                  r.selectNodeContents(document.querySelector('svg.graph-st-svg'));
                  const s = getSelection(); s.removeAllRanges(); s.addRange(r); }"""
            )
            await expect(page.locator(MAP)).to_have_attribute("data-resting", "false")
            assert await page.evaluate("() => getSelection().isCollapsed")
            await page.mouse.up()
            await at_rest(page)
            assert max(drift(before, await places(page)).values()) <= 5

            # Two and three presses on the empty picture select nothing either.
            box = await page.locator(MAP).bounding_box()
            assert box is not None
            spot = (box["x"] + 30, box["y"] + 30)
            await page.mouse.dblclick(*spot)
            assert await page.evaluate(chosen) == ""
            await page.mouse.click(*spot, click_count=3)
            assert await page.evaluate(chosen) == ""
            assert w.problems == [], w.problems


# -- reduced motion -----------------------------------------------------------------------------


async def test_with_reduced_motion_the_map_rests_at_once_cannot_be_dragged_and_still_opens_items(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        demo = await demo_world(client, "calm")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await page.emulate_media(reduced_motion="reduce")
            await page.add_init_script(WATCH)
            await open_map(w)
            # It is worked out once, to rest: it was never moving, from its first paint on.
            first = await page.evaluate("window.__first")
            assert first["resting"] == "true"
            assert "false" not in await rest_log(page)
            before = await places(page)
            assert least_gap(before) > 0.5

            # A drag of a hundred and fifty units moves nothing and opens nothing.
            task = demo.tasks[3]
            dx, dy = toward_the_middle(before, task, 150)
            await press_and_move(page, task, before, dx, dy)
            await page.wait_for_timeout(300)
            assert max(drift(before, await places(page)).values()) == 0
            await page.mouse.up()
            await expect(page.locator("#item-page")).to_have_count(0)
            assert "false" not in await rest_log(page)

            # Enter and a click still open the item.
            node = page.locator(f"{MAP} g.graph-st-node[data-ref='{task}']")
            await node.focus()
            await page.keyboard.press("Enter")
            await expect(page.locator(f"#item-page[data-ref='{task}']")).to_be_visible(timeout=SLOW)
            await page.go_back()
            await at_rest(page)
            x, y = await screen_of(page, task)
            await page.mouse.click(x, y)
            await expect(page.locator(f"#item-page[data-ref='{task}']")).to_be_visible(timeout=SLOW)
            await page.go_back()
            await at_rest(page)

            # Tidy jumps to the tidy layout, with no motion in between.
            await clear_log(page)
            await page.locator("#graph-st-tidy").click()
            await at_rest(page)
            tidy = await page.evaluate(TIDY, demo.tree())
            now = await places(page)
            assert max(far(now[ref], tidy[ref]) for ref in tidy) <= 0.5
            assert "false" not in await rest_log(page)
            assert w.problems == [], w.problems


# -- Tidy and the Forces ------------------------------------------------------------------------


def span(found: Places) -> float:
    """The area of the box round every dot: how big the map has settled."""
    xs = [one[0] + side * one[2] for one in found.values() for side in (-1, 1)]
    ys = [one[1] + side * one[2] for one in found.values() for side in (-1, 1)]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


async def mean_line(page: Page) -> float:
    """How long the tree's lines are, on average, in map units."""
    lengths: list[float] = await page.evaluate(LINE_LENGTHS)
    return sum(lengths) / len(lengths)


async def test_tidy_glides_to_the_tidy_layout_and_rests_where_no_line_crosses_another(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        demo = await demo_world(client, "tidy")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await page.add_init_script(WATCH)
            await open_map(w)
            before = await places(page)
            await clear_log(page)
            await page.locator("#graph-st-tidy").click()
            await expect(page.locator(MAP)).to_have_attribute("data-resting", "false")
            await at_rest(page)
            log = await rest_log(page)
            assert log[-1] == "true" and "false" in log, log
            # It ends on what the tidy layout of graph-radial.js says, to the tenth of a unit the
            # page draws, which is a layout of no crossing lines and no touching dots, and which
            # is not where the live map had settled.
            tidy = await page.evaluate(TIDY, demo.tree())
            now = await places(page)
            assert len(tidy) == len(now) - 1
            assert max(far(now[ref], tidy[ref]) for ref in tidy) <= 0.5
            assert await page.evaluate(CROSSINGS) == 0
            assert least_gap(now) > 0.5
            assert max(drift(before, now).values()) > 20
            # Resting means resting: nothing asks for another frame.
            asked = await page.evaluate("window.__frames")
            await page.wait_for_timeout(600)
            assert await page.evaluate("window.__frames") == asked
            assert w.problems == [], w.problems


async def test_the_forces_panel_opens_closed_and_each_slider_tunes_the_map_and_reset_undoes_it(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        await demo_world(client, "forces")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await page.add_init_script(WATCH)
            await open_map(w)
            button = page.locator("#graph-st-forces")
            panel = page.locator("#graph-st-forces-panel")
            # Closed when the Graph opens; four sliders from 0.2 to 3, each at 1, each with its
            # own label and its value written quietly beside it.
            await expect(button).to_have_attribute("aria-expanded", "false")
            await expect(panel).to_be_hidden()
            await button.click()
            await expect(button).to_have_attribute("aria-expanded", "true")
            await expect(panel).to_be_visible()
            sliders = {
                "centre": "Centre",
                "repel": "Repel",
                "link": "Link force",
                "distance": "Link distance",
            }
            for key, label in sliders.items():
                slider = page.locator(f"#graph-st-force-{key}")
                await expect(page.locator(f"label[for=graph-st-force-{key}]")).to_have_text(label)
                for name, value in (("min", "0.2"), ("max", "3"), ("step", "0.1")):
                    await expect(slider).to_have_attribute(name, value)
                await expect(slider).to_have_value("1")
                await expect(page.locator(f"output[for=graph-st-force-{key}]")).to_have_text("1.0")
            # Escape shuts it and gives the keyboard back to the button, and the values are not in
            # the address.
            await page.keyboard.press("Escape")
            await expect(panel).to_be_hidden()
            await expect(button).to_be_focused()
            await button.click()
            await expect(panel).to_be_visible()
            assert "force" not in page.url, page.url

            settled = await places(page)
            size, line = span(settled), await mean_line(page)

            async def tune(key: str, value: str) -> tuple[float, float]:
                """Move one slider, wait for the map to rest, and say how much bigger the map is
                than the settled one and how much longer its lines are."""
                await clear_log(page)
                await page.locator(f"#graph-st-force-{key}").fill(value)
                await expect(page.locator(f"output[for=graph-st-force-{key}]")).to_have_text(
                    f"{float(value):.1f}"
                )
                await at_rest(page)
                log = await rest_log(page)
                assert log[-1] == "true" and "false" in log, (key, log)
                found = await places(page)
                assert least_gap(found) > 0, key
                return span(found) / size, await mean_line(page) / line

            async def reset() -> None:
                await page.locator("#graph-st-forces-reset").click()
                await at_rest(page)
                assert max(drift(settled, await places(page)).values()) <= 1
                for key in sliders:
                    await expect(page.locator(f"#graph-st-force-{key}")).to_have_value("1")
                    await expect(page.locator(f"output[for=graph-st-force-{key}]")).to_have_text(
                        "1.0"
                    )

            # Repel up spreads the map out, and down packs it; Centre up pulls it in, and down lets
            # it go; Link distance makes the lines longer; Link force up makes them shorter. Reset
            # brings back the very map that was settled.
            assert (await tune("repel", "3"))[0] > 1.4
            await reset()
            assert (await tune("repel", "0.2"))[0] < 0.95
            await reset()
            assert (await tune("centre", "3"))[0] < 0.9
            await reset()
            assert (await tune("centre", "0.2"))[0] > 1.3
            await reset()
            assert (await tune("distance", "2"))[1] > 1.4
            await reset()
            assert (await tune("link", "3"))[1] < 0.98
            await reset()
            assert w.problems == [], w.problems


# -- pointing, zooming, panning and the keyboard ------------------------------------------------


async def shows(page: Page, refs: Sequence[str], opacity: str) -> None:
    """Every one of these dots is drawn at this opacity (the fade of pointing at a dot is eased,
    so this waits for it to end)."""
    for ref in refs:
        await expect(page.locator(f"{MAP} g.graph-st-node[data-ref='{ref}']")).to_have_css(
            "opacity", opacity
        )


async def test_pointing_at_a_dot_keeps_it_and_what_it_is_tied_to_in_full_and_fades_the_rest(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        demo = await demo_world(client, "hover")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await open_map(w)
            task, partner = demo.ref["t1-1-1"], demo.ref["t2-1-1"]
            story, epic = demo.parent[task], demo.epic_of(task)
            sibling = demo.kids(story)[1]
            stranger = demo.ref["t5-1-1"]
            svg = page.locator(MAP)
            await expect(svg).not_to_have_class(re.compile(r"is-hovering"))
            await shows(page, [task, sibling, stranger], "1")

            # A task: itself, the story it hangs from and the task it is linked to stay in full;
            # its sibling, a task of another epic and its epic fade. Its name is always shown.
            x, y = await screen_of(page, task)
            await page.mouse.move(x, y)
            await expect(svg).to_have_class(re.compile(r"is-hovering"))
            await shows(page, [task, story, partner], "1")
            await shows(page, [sibling, stranger, epic], "0.2")
            await expect(
                page.locator(f"{MAP} .graph-st-labels .graph-st-name.is-lit")
            ).to_contain_text("Task 1-1-1 hover")
            await expect(page.locator(".graph-st-inner .graph-x-tip")).to_be_visible()

            # An epic: itself and its stories in full, its tasks and the other epics faded.
            x, y = await screen_of(page, epic)
            await page.mouse.move(x, y)
            await shows(page, [epic, *demo.kids(epic)], "1")
            await shows(page, [task, stranger, demo.epics[5]], "0.2")

            # Moving off lets everything come back, and the keyboard does what the pointer does.
            await page.mouse.move(2, 2)
            await expect(svg).not_to_have_class(re.compile(r"is-hovering"))
            await shows(page, [task, sibling, stranger], "1")
            await page.locator(f"{MAP} g.graph-st-node[data-ref='{story}']").focus()
            await shows(page, [story, task, epic], "1")
            await shows(page, [stranger], "0.2")
            assert w.problems == [], w.problems


def fixed_point(
    was: list[tuple[float, float]], now: list[tuple[float, float]]
) -> tuple[float, float]:
    """The one point of the window that a zoom left where it was, worked out from where two dots
    were and are (a zoom about a point sends every dot p to c + s (p - c))."""
    s = math.hypot(now[0][0] - now[1][0], now[0][1] - now[1][1]) / math.hypot(
        was[0][0] - was[1][0], was[0][1] - was[1][1]
    )
    assert abs(1 - s) > 0.05, "the picture did not zoom"
    return (
        (now[0][0] - s * was[0][0]) / (1 - s),
        (now[0][1] - s * was[0][1]) / (1 - s),
    )


async def empty_spot(page: Page) -> tuple[float, float]:
    """A point of the map's svg with no dot, line or button under it."""
    spot: list[float] | None = await page.evaluate(
        """() => {
          const box = document.querySelector('svg.graph-st-svg').getBoundingClientRect();
          for (let fx = 0.04; fx < 0.5; fx += 0.04) {
            for (const [x, y] of [[box.left + box.width * fx, box.top + box.height * 0.5],
                [box.right - box.width * fx, box.top + box.height * 0.9]]) {
              const hit = document.elementFromPoint(x, y);
              if (hit && hit.closest('svg.graph-st-svg') && !hit.closest('.graph-st-node')
                && hit.tagName !== 'line') return [x, y];
            }
          }
          return null;
        }"""
    )
    assert spot is not None
    return spot[0], spot[1]


async def test_the_wheel_zooms_about_the_cursor_zooming_out_never_loses_the_map_and_a_drag_pans(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        demo = await demo_world(client, "zoom")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await open_map(w)
            start = await places(page)
            svg = page.locator(MAP)

            # Zoom in with the wheel over a dot: the dot stays under the cursor (to a few pixels)
            # and the picture grows by what the wheel says, and the map rests again afterwards.
            task = demo.tasks[100]
            x, y = await screen_of(page, task)
            home = (x, y)
            wide = await scale_of(page, task, start[task][2])
            await page.mouse.move(x, y)
            await page.mouse.wheel(0, -240)
            await expect(svg).to_have_attribute("data-resting", "false")
            await at_rest(page)
            x2, y2 = await screen_of(page, task)
            assert math.hypot(x2 - x, y2 - y) <= 3, (x, y, x2, y2)
            grown = await scale_of(page, task, start[task][2]) / wide
            assert grown == pytest.approx(math.exp(0.48), rel=0.1), grown
            assert drift(start, await places(page)) == {ref: 0 for ref in start}

            # Fit brings the whole project back to where it was first shown.
            await page.get_by_role("button", name="Fit the whole project in view").click()
            await at_rest(page)
            assert await scale_of(page, task, start[task][2]) == pytest.approx(wide, rel=0.02)
            x2, y2 = await screen_of(page, task)
            assert math.hypot(x2 - home[0], y2 - home[1]) <= 3, ((x2, y2), home)

            # Zooming out is about the middle of the picture and not about the cursor, so the map
            # never drifts away: wheeling out with the cursor in a corner makes the map smaller
            # and leaves it where it was, every dot still in the picture.
            frame = await svg.bounding_box()
            assert frame is not None
            centre = (frame["x"] + frame["width"] / 2, frame["y"] + frame["height"] / 2)
            watched = [task, demo.epics[0]]
            was = [await screen_of(page, ref) for ref in watched]
            await page.mouse.move(frame["x"] + 30, frame["y"] + 30)
            for _ in range(4):
                await page.mouse.wheel(0, 300)
                await at_rest(page)
            assert await scale_of(page, task, start[task][2]) < wide * 0.8
            assert await page.evaluate(OUTSIDE) == 0
            now = [await screen_of(page, ref) for ref in watched]
            fixed = fixed_point(was, now)
            assert far(fixed, centre) <= 4, (fixed, centre)

            # Zoomed in on the dot farthest from the middle, the wheel takes it out again about
            # the middle of the picture too.
            corner = max(demo.tasks, key=lambda ref: math.hypot(*start[ref][:2]))
            for _ in range(3):
                x, y = await screen_of(page, corner)
                await page.mouse.move(x, y)
                await page.mouse.wheel(0, -240)
                await at_rest(page)
            watched = [corner, demo.epics[0]]
            was = [await screen_of(page, ref) for ref in watched]
            await page.mouse.wheel(0, 300)
            await at_rest(page)
            now = [await screen_of(page, ref) for ref in watched]
            fixed = fixed_point(was, now)
            assert far(fixed, centre) <= 4, (fixed, centre)
            await page.get_by_role("button", name="Fit the whole project in view").click()
            await at_rest(page)

            # A drag of the background moves every dot by what the pointer moved, and nothing in the
            # map itself: a pointer that rests before it is let go leaves no inertia behind.
            before = {ref: await screen_of(page, ref) for ref in (task, corner, demo.epics[0])}
            at = await places(page)
            x0, y0 = await empty_spot(page)
            await page.mouse.move(x0, y0)
            await page.mouse.down()
            await page.mouse.move(x0 + 80, y0 + 50, steps=10)
            await page.wait_for_timeout(200)
            await page.mouse.up()
            await at_rest(page)
            for ref, (bx, by) in before.items():
                cx, cy = await screen_of(page, ref)
                assert (round(cx - bx), round(cy - by)) == (80, 50), (ref, cx - bx, cy - by)
            assert drift(at, await places(page)) == {ref: 0 for ref in at}
            assert w.problems == [], w.problems


async def test_the_arrow_keys_walk_the_tree_and_enter_opens_the_item(server_url: str) -> None:
    async with fresh_project(server_url) as (client, board, project):
        demo = await demo_world(client, "keys")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await open_map(w)
            epic = demo.epics[0]
            story, next_story = demo.kids(epic)[:2]
            task = demo.kids(story)[0]
            # One dot takes the keyboard when the map is tabbed into: the first epic.
            await expect(page.locator(f"{MAP} g.graph-st-node[tabindex='0']")).to_have_attribute(
                "data-ref", epic
            )
            await expect(page.locator(f"{MAP} g.graph-st-node[tabindex='0']")).to_have_count(1)

            async def on() -> str:
                focused: str = await page.evaluate("document.activeElement.dataset.ref")
                return focused

            await page.locator(f"{MAP} g.graph-st-node[data-ref='{epic}']").focus()
            walk = (
                ("ArrowDown", story),  # into the first story
                ("ArrowRight", next_story),  # to the next of its siblings
                ("ArrowLeft", story),
                ("ArrowDown", task),  # into its first task
                ("ArrowUp", story),  # up to what it hangs from
                ("ArrowUp", epic),
                ("ArrowLeft", demo.epics[-1]),  # round to the last epic
                ("ArrowRight", epic),
            )
            for key, wanted in walk:
                await page.keyboard.press(key)
                assert await on() == wanted, (key, wanted)
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("ArrowDown")
            assert await on() == task
            await page.keyboard.press("Enter")
            await expect(page.locator(f"#item-page[data-ref='{task}']")).to_be_visible(timeout=SLOW)
            assert w.problems == [], w.problems


async def test_a_task_added_by_another_actor_appears_as_one_dot_and_the_others_hardly_move(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        demo = await demo_world(client, "news")
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await open_map(w)
            before = await places(page)
            dots = page.locator(f"{MAP} g.graph-st-node")
            total = await dots.count()
            await page.evaluate("document.querySelector('svg.graph-st-svg').dataset.mine = '1'")
            story = demo.stories[10]
            new = await made(client, "task", "A task an agent added while the map was open", story)
            await expect(dots).to_have_count(total + 1, timeout=2 * SLOW)
            await at_rest(page)
            after = await places(page)
            # The same picture, changed in place: one more dot, beside its story, and the others
            # are where they were, give or take a few units.
            assert await page.locator("svg.graph-st-svg[data-mine='1']").count() == 1
            assert set(after) - set(before) == {new}
            assert max(drift(before, after).values()) <= 6, max(drift(before, after).items())
            assert far(after[new], after[story]) <= 60
            assert least_gap(after) > 0.5
            assert w.problems == [], w.problems
