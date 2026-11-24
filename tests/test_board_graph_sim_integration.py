"""M38: the physics of the Structure map (src/vrum/board/static/graph-sim.js), in a real headless
browser and with no containers.

graph-sim.js is a pure module (it takes the tree as data and gives back coordinates), so the page
here is a blank one on a made-up origin that serves the two modules from disk (an ES module needs
an http origin) and builds trees the size of the demo project (12 epics, 53 stories, 180 tasks)
and of a larger one (12 epics, about 480 nodes). Every proof is a relation, not a coordinate:
nothing overlaps, epics are at different distances, leaves are outermost, a dragged node pulls its
neighbours and the map comes back, the map is the same on every run, the four force sliders do
what they say and Reset returns to the same map, a glide ends on its target, and a refresh of the
data does not reshuffle the map."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from playwright.async_api import Page, Route, async_playwright

from vrum.board import STATIC

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

ORIGIN = "http://sim.test"
SIZES = ("demo", "large")

# The demo project's hierarchy: an epic's priority and, for each story, its priority followed by
# the priority of each of its tasks (l low, n normal, h high, u urgent).
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

HARNESS = r"""
import { createSim } from './graph-sim.js';
import { wholeLayout, seeded } from './graph-radial.js';

const WORD = { l: 'low', n: 'normal', h: 'high', u: 'urgent' };
const PRIORITIES = ['low', 'normal', 'normal', 'normal', 'high', 'urgent'];
const DEMO = __DEMO__;

const demoTree = () => {
  let s = 0;
  let t = 0;
  return DEMO.map(([p, stories], e) => ({
    ref: `epic:${e + 1}`,
    priority: WORD[p],
    stories: stories.map((text) => {
      s += 1;
      return {
        ref: `story:${s}`,
        priority: WORD[text[0]],
        tasks: [...text.slice(1)].map((c) => ({ ref: `task:${(t += 1)}`, priority: WORD[c] })),
      };
    }),
  }));
};

const syntheticTree = (seed) => {
  const rnd = seeded(seed);
  const int = (a, b) => a + Math.floor(rnd() * (b - a + 1));
  let s = 0;
  let t = 0;
  return Array.from({ length: 12 }, (_, e) => ({
    ref: `epic:${e + 1}`,
    priority: PRIORITIES[int(0, 5)],
    stories: Array.from({ length: int(4, 15) }, () => {
      const count = rnd() < 0.08 ? 0 : int(3, 5);
      return {
        ref: `story:${(s += 1)}`,
        priority: PRIORITIES[int(0, 5)],
        tasks: Array.from({ length: count }, () => ({ ref: `task:${(t += 1)}`, priority: PRIORITIES[int(0, 5)] })),
      };
    }),
  }));
};

const treeOf = (name) => (name === 'demo' ? demoTree() : syntheticTree(11));

// The spec the page builds from the tidy layout.
const specOf = (tree) => {
  const lay = wholeLayout(tree, 'tasks');
  const epics = tree.map((e) => e.ref);
  const nodes = [{ id: 'root', kind: 'project', r: lay.rProj, epic: 'root', parent: null, x: 0, y: 0 }];
  for (const o of lay.nodes) nodes.push({ id: o.ref, kind: o.kind, r: o.r, epic: epics[o.epic], parent: o.parent, x: o.x, y: o.y });
  return { nodes, bounds: lay.bounds };
};

const area = (b) => (b.x1 - b.x0) * (b.y1 - b.y0);
const boundsOf = (nodes) => {
  const b = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
  for (const o of nodes) {
    b.x0 = Math.min(b.x0, o.x - o.r);
    b.x1 = Math.max(b.x1, o.x + o.r);
    b.y0 = Math.min(b.y0, o.y - o.r);
    b.y1 = Math.max(b.y1, o.y + o.r);
  }
  return b;
};
const mean = (list) => list.reduce((a, b) => a + b, 0) / (list.length || 1);
const dist = (n) => Math.hypot(n.x, n.y);
const make = (name) => {
  const spec = specOf(treeOf(name));
  const sim = createSim({ nodes: spec.nodes.map((n) => ({ ...n })), options: {} });
  return { spec, sim };
};
const positions = (sim) => sim.nodes.map((n) => [n.x, n.y]);
const snapshot = (sim) => new Map(sim.nodes.map((n) => [n.id, { x: n.x, y: n.y }]));
const farthest = (sim, from) => Math.max(...sim.nodes.map((n) => Math.hypot(n.x - from.get(n.id).x, n.y - from.get(n.id).y)));
const finite = (sim) => sim.nodes.every((n) => [n.x, n.y, n.vx, n.vy].every(Number.isFinite));
const run = (sim, limit = 1000) => {
  let k = 0;
  while (k < limit && sim.tick()) k += 1;
  return k;
};
const centroid = (sim) => {
  const moving = sim.nodes.filter((n) => n.kind !== 'project');
  return [mean(moving.map((n) => n.x)), mean(moving.map((n) => n.y))];
};

// The least gap between the drawn dots of any two nodes (negative: they overlap) and how many pairs overlap.
const overlaps = (nodes) => {
  let worst = Infinity;
  let count = 0;
  for (let i = 0; i < nodes.length; i += 1) {
    for (let j = i + 1; j < nodes.length; j += 1) {
      const gap = Math.hypot(nodes[i].x - nodes[j].x, nodes[i].y - nodes[j].y) - nodes[i].r - nodes[j].r;
      if (gap < worst) worst = gap;
      if (gap < 0) count += 1;
    }
  }
  return { worst, count };
};

// Each epic's whole sub-tree as a disc: its centre of mass and its furthest member.
const discs = (nodes) => {
  const groups = new Map();
  for (const n of nodes) {
    if (n.kind === 'project') continue;
    if (!groups.has(n.epic)) groups.set(n.epic, []);
    groups.get(n.epic).push(n);
  }
  return [...groups.values()].map((members) => {
    const cx = mean(members.map((n) => n.x));
    const cy = mean(members.map((n) => n.y));
    return { cx, cy, R: Math.max(...members.map((n) => Math.hypot(n.x - cx, n.y - cy) + n.r)) };
  });
};

const nearestEpicIsOwn = (sim) => {
  const epics = sim.nodes.filter((n) => n.kind === 'epic');
  const leaves = sim.nodes.filter((n) => n.kind === 'story' || n.kind === 'task');
  const own = leaves.filter((n) => {
    let best = null;
    let nearest = Infinity;
    for (const e of epics) {
      const d = Math.hypot(n.x - e.x, n.y - e.y);
      if (d < nearest) {
        nearest = d;
        best = e;
      }
    }
    return best.id === n.epic;
  });
  return own.length / (leaves.length || 1);
};

const H = {};

H.settled = (name) => {
  const { spec, sim } = make(name);
  sim.settle(160);
  const by = sim.byId;
  const epics = sim.nodes.filter((n) => n.kind === 'epic');
  const stories = sim.nodes.filter((n) => n.kind === 'story');
  const tasks = sim.nodes.filter((n) => n.kind === 'task');
  const ed = epics.map(dist);
  const ds = discs(sim.nodes);
  let discGap = Infinity;
  for (let i = 0; i < ds.length; i += 1) {
    for (let j = i + 1; j < ds.length; j += 1) discGap = Math.min(discGap, Math.hypot(ds[i].cx - ds[j].cx, ds[i].cy - ds[j].cy) - ds[i].R - ds[j].R);
  }
  const ov = overlaps(sim.nodes);
  const restingAfterSettle = sim.resting && sim.alpha < 0.001;
  // After a reheat the map must come to rest within 200 ticks and stay where it was.
  const home = snapshot(sim);
  sim.reheat(0.3);
  const ticks = run(sim);
  return {
    nodes: sim.nodes.length,
    overlapping: ov.count,
    leastGap: ov.worst,
    cv: Math.sqrt(mean(ed.map((d) => (d - mean(ed)) ** 2))) / mean(ed),
    meanEpic: mean(ed),
    meanStory: mean(stories.map(dist)),
    meanTask: mean(tasks.map(dist)),
    tasksFartherThanStory: tasks.filter((t) => dist(t) > dist(by.get(t.parent))).length / tasks.length,
    ownEpicNearest: nearestEpicIsOwn(sim),
    discGap,
    areaRatio: area(boundsOf(sim.nodes)) / area(spec.bounds),
    restingAfterSettle,
    ticksToRest: ticks,
    movedByReheat: farthest(sim, home),
    finite: finite(sim),
  };
};

// Grab a node, move it 150 units over 30 ticks, hold, let go, and run to rest.
H.drag = (name, kind, pick, direction) => {
  const { sim } = make(name);
  sim.settle(160);
  const home = snapshot(sim);
  const list = sim.nodes.filter((n) => n.kind === kind);
  const node = list[Math.floor((list.length * pick) / 100)];
  const [ux, uy] = [Math.cos(direction), Math.sin(direction)];
  const from = home.get(node.id);
  sim.grab(node.id, from.x, from.y);
  for (let k = 1; k <= 30; k += 1) {
    sim.moveTo(node.id, from.x + (150 * ux * k) / 30, from.y + (150 * uy * k) / 30);
    sim.tick();
  }
  for (let k = 0; k < 10; k += 1) sim.tick();
  const point = { x: node.x, y: node.y };
  const closer = (id) => {
    const n = sim.byId.get(id);
    const was = home.get(id);
    return Math.hypot(was.x - point.x, was.y - point.y) - Math.hypot(n.x - point.x, n.y - point.y);
  };
  const moved = (id) => Math.hypot(sim.byId.get(id).x - home.get(id).x, sim.byId.get(id).y - home.get(id).y);
  const others = sim.nodes.filter((n) => n.kind === 'epic' && n.id !== node.epic && n.id !== node.id);
  others.sort((a, b) => Math.hypot(a.x - point.x, a.y - point.y) - Math.hypot(b.x - point.x, b.y - point.y));
  const family = sim.nodes.filter((n) => n.parent === node.parent && n.id !== node.id).map((n) => n.id);
  const whileHeld = {
    pinnedAt: Math.hypot(node.x - from.x, node.y - from.y),
    parentCloser: kind === 'task' ? closer(node.parent) : null,
    siblingsCloser: kind === 'task' && family.length ? mean(family.map(closer)) : null,
    ownTreeCloser: mean(sim.nodes.filter((n) => n.epic === node.epic && n.id !== node.id).map((n) => closer(n.id))),
    nearEpicsMoved: mean(others.slice(0, 2).map((e) => moved(e.id))),
    farEpicsMoved: mean(others.slice(-4).map((e) => moved(e.id))),
    resting: sim.resting,
    finite: finite(sim),
  };
  sim.release(node.id);
  const duringReturn = sim.resting;
  const ticks = run(sim);
  return {
    ...whileHeld,
    restingWhileReturning: duringReturn,
    ticksToRest: ticks,
    restingAfter: sim.resting,
    draggedFromHome: Math.hypot(node.x - from.x, node.y - from.y),
    farthestFromHome: farthest(sim, home),
    overlapsAfter: overlaps(sim.nodes).count,
  };
};

// Two identical runs, with a drag in the middle, give bit-identical positions; nothing wanders.
H.sameTwice = (name) => {
  const once = () => {
    const { sim } = make(name);
    sim.settle(160);
    const [cx, cy] = centroid(sim);
    const task = sim.nodes.filter((n) => n.kind === 'task')[20];
    sim.grab(task.id, task.x, task.y);
    for (let k = 1; k <= 20; k += 1) {
      sim.moveTo(task.id, task.x + 4 * k, task.y - 3 * k);
      sim.tick();
    }
    sim.release(task.id);
    run(sim);
    sim.reheat(0.3);
    let drift = 0;
    while (sim.tick()) {
      const [x, y] = centroid(sim);
      drift = Math.max(drift, Math.hypot(x - cx, y - cy));
    }
    return { positions: JSON.stringify(positions(sim)), drift };
  };
  const a = once();
  const b = once();
  return { identical: a.positions === b.positions, drift: Math.max(a.drift, b.drift) };
};

H.sliders = (name) => {
  const { sim } = make(name);
  sim.settle(160);
  const home = snapshot(sim);
  const size = () => area(boundsOf(sim.nodes));
  const links = () => mean(sim.nodes.filter((n) => n.parent).map((n) => Math.hypot(n.x - sim.byId.get(n.parent).x, n.y - sim.byId.get(n.parent).y)));
  const base = { area: size(), link: links() };
  const out = { base };
  for (const [key, value] of [['repel', 3], ['centre', 3], ['distance', 2], ['repel', 0.2], ['centre', 0.2]]) {
    sim.setForces({ [key]: value });
    const ticks = run(sim);
    out[`${key}${value}`] = { area: size() / base.area, link: links() / base.link, ticks, overlapping: overlaps(sim.nodes).count, finite: finite(sim) };
    sim.resetForces();
    const back = run(sim);
    out[`${key}${value}`].resetTicks = back;
    out[`${key}${value}`].resetOff = farthest(sim, home);
    out[`${key}${value}`].resetResting = sim.resting;
  }
  sim.setForces({ repel: 3, centre: 0.5 });
  run(sim);
  sim.resetForces();
  run(sim);
  out.mixedResetOff = farthest(sim, home);
  return out;
};

H.glide = (name) => {
  const { spec, sim } = make(name);
  sim.settle(160);
  const target = new Map(spec.nodes.map((n) => [n.id, { x: n.x, y: n.y }]));
  const tidyAlready = new Set();
  for (let i = 0; i < spec.nodes.length; i += 1) {
    for (let j = i + 1; j < spec.nodes.length; j += 1) {
      const a = spec.nodes[i];
      const b = spec.nodes[j];
      if (Math.hypot(a.x - b.x, a.y - b.y) < a.r + b.r) tidyAlready.add(`${a.id}|${b.id}`);
    }
  }
  sim.glideTo(target, 36);
  let frames = 0;
  let worst = 0;
  const midway = sim.resting;
  while (sim.tick()) {
    frames += 1;
    const nodes = sim.nodes;
    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        const dip = nodes[i].r + nodes[j].r - Math.hypot(nodes[i].x - nodes[j].x, nodes[i].y - nodes[j].y);
        if (dip > 0.01 && !tidyAlready.has(`${nodes[i].id}|${nodes[j].id}`)) worst = Math.max(worst, dip);
      }
    }
  }
  let miss = 0;
  for (const n of sim.nodes) miss = Math.max(miss, Math.hypot(n.x - target.get(n.id).x, n.y - target.get(n.id).y));
  const home = sim.home;
  let homeMiss = 0;
  for (const n of sim.nodes) homeMiss = Math.max(homeMiss, Math.hypot(home.get(n.id).x - target.get(n.id).x, home.get(n.id).y - target.get(n.id).y));
  const restingAtEnd = sim.resting;
  // A grab in the middle of a glide stops it where it is and the map runs again.
  const edge = sim.nodes[5];
  sim.glideTo(snapshot(sim), 36);
  sim.tick();
  sim.grab(edge.id, edge.x, edge.y);
  const awakeAfterGrab = !sim.resting;
  // A glide of one frame is a jump (reduced motion): there and at rest at once.
  sim.glideTo(target, 1);
  let jumpMiss = 0;
  for (const n of sim.nodes) jumpMiss = Math.max(jumpMiss, Math.hypot(n.x - target.get(n.id).x, n.y - target.get(n.id).y));
  return { restingWhileGliding: midway, frames: frames + 1, miss, homeMiss, worstNewOverlap: worst, restingAtEnd, awakeAfterGrab, jumpResting: sim.resting, jumpMiss, tidyOverlaps: tidyAlready.size };
};

H.refresh = (name) => {
  const { sim } = make(name);
  sim.settle(160);
  const before = snapshot(sim);
  const same = sim.nodes.map((n) => ({ id: n.id, kind: n.kind, r: n.r, epic: n.epic, parent: n.parent }));
  sim.addOrUpdate(same);
  const restingAfterSame = sim.resting;
  const epic = sim.nodes.find((n) => n.kind === 'epic');
  sim.addOrUpdate([
    { id: 'story:new', kind: 'story', r: 9, epic: epic.id, parent: epic.id },
    { id: 'task:new', kind: 'task', r: 4, epic: epic.id, parent: 'story:new' },
  ]);
  const wokeUp = !sim.resting;
  const entered = sim.byId.get('story:new');
  const enteredNear = Math.hypot(entered.x - epic.x, entered.y - epic.y);
  const ticks = run(sim);
  const grown = sim.nodes.length;
  let moved = 0;
  for (const [id, was] of before) moved = Math.max(moved, Math.hypot(sim.byId.get(id).x - was.x, sim.byId.get(id).y - was.y));
  const overlappingAfterAdd = overlaps(sim.nodes).count;
  sim.remove(['story:new', 'task:new']);
  run(sim);
  return {
    restingAfterSame,
    wokeUp,
    enteredNear,
    ticks,
    grown,
    nodesAfterRemove: sim.nodes.length,
    originalNodes: before.size,
    oldNodesMoved: moved,
    overlappingAfterAdd,
    overlappingAfterRemove: overlaps(sim.nodes).count,
    finite: finite(sim),
    resting: sim.resting,
  };
};

H.odd = () => {
  const project = { id: 'root', kind: 'project', r: 50, epic: 'root', parent: null, x: 0, y: 0 };
  const empty = createSim({ nodes: [] });
  empty.settle();
  const emptyTick = empty.tick();
  const lone = createSim({ nodes: [{ ...project }] });
  lone.settle();
  const odd = createSim({
    nodes: [
      { ...project },
      { id: 'e1', kind: 'epic', r: 25, epic: 'e1', parent: 'root', x: 100, y: 100 },
      { id: 'e2', kind: 'epic', r: 25, epic: 'e2', parent: 'root', x: 100, y: 100 },
      { id: 's1', kind: 'story', r: 9, epic: 'e1', parent: 'e1', x: 100, y: 100 },
      { id: 't1', kind: 'task', r: 0, epic: 'e1', parent: 'nobody', x: 100, y: 100 },
      { id: 't2', kind: 'task', r: NaN, epic: 'e1', parent: 's1', x: NaN, y: undefined },
    ],
  });
  odd.settle();
  odd.grab('t1', 5, 5);
  odd.moveTo('t1', 8, 8);
  for (let k = 0; k < 20; k += 1) odd.tick();
  odd.release('t1');
  run(odd);
  odd.glideTo(new Map([['e1', { x: 300, y: 300 }]]), 5);
  run(odd);
  return {
    emptyResting: empty.resting,
    emptyTick,
    loneAt: [lone.nodes[0].x, lone.nodes[0].y],
    loneResting: lone.resting,
    oddFinite: finite(odd),
    oddOverlapping: overlaps(odd.nodes.filter((n) => n.r > 0)).count,
    projectAt: [odd.byId.get('root').x, odd.byId.get('root').y],
    oddResting: odd.resting,
  };
};

window.H = H;
"""  # noqa: E501

INDEX = """<!doctype html><html><head><meta charset="utf-8"><title>graph-sim</title></head><body>
<script type="module">__HARNESS__
window.ready = true;</script></body></html>"""


async def serve(route: Route) -> None:
    path = route.request.url.removeprefix(ORIGIN)
    if path in ("", "/"):
        harness = HARNESS.replace("__DEMO__", json.dumps(DEMO))
        await route.fulfill(
            status=200, content_type="text/html", body=INDEX.replace("__HARNESS__", harness)
        )
        return
    file = STATIC / path.lstrip("/")
    if path in ("/graph-sim.js", "/graph-radial.js") and file.is_file():
        await route.fulfill(
            status=200, content_type="text/javascript", body=file.read_text(encoding="utf-8")
        )
        return
    await route.fulfill(status=404, body="not found")


@asynccontextmanager
async def sim_page() -> AsyncIterator[Page]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            problems: list[str] = []
            page.on("pageerror", lambda error: problems.append(str(error)))
            page.on(
                "console",
                lambda message: problems.append(message.text) if message.type == "error" else None,
            )
            await page.route(f"{ORIGIN}/**", serve)
            await page.goto(f"{ORIGIN}/")
            await page.wait_for_function("window.ready === true")
            yield page
            assert problems == []
        finally:
            await browser.close()


async def ask(page: Page, call: str, *args: Any) -> dict[str, Any]:
    result: dict[str, Any] = await page.evaluate(f"(args) => window.H.{call}(...args)", list(args))
    return result


def test_the_simulation_is_pure() -> None:
    """Like graph-radial.js it takes data and gives coordinates: no page, no clock, no
    `Math.random`, so the same tree gives the same map on every visit."""
    source = (STATIC / "graph-sim.js").read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("//"))
    for name in (
        "Math.random",
        "Date.now",
        "performance.now",
        "document",
        "window",
        "requestAnimationFrame",
        "setTimeout",
        "setInterval",
        "localStorage",
        "import ",
    ):
        assert name not in code, name


@pytest.mark.parametrize("size", SIZES)
async def test_a_settled_map_is_shy_spread_and_quiet(size: str) -> None:
    async with sim_page() as page:
        m = await ask(page, "settled", size)
    assert m["finite"]
    # shy: no two dots touch
    assert m["overlapping"] == 0
    assert m["leastGap"] > 0
    # epics are at different distances from the middle (the tidy layout has all at one distance)
    assert m["cv"] >= 0.15
    # leaves outermost, without putting epics on one ring
    assert m["tasksFartherThanStory"] >= 0.9
    assert m["meanEpic"] < m["meanStory"] < m["meanTask"]
    # sub-trees stay together and apart
    assert m["ownEpicNearest"] >= 0.95
    assert m["discGap"] > 0
    # the page keeps a similar zoom
    assert m["areaRatio"] <= 1.3
    # it comes to rest, and a reheat comes to rest again within 200 ticks and puts everything back
    assert m["restingAfterSettle"]
    assert m["ticksToRest"] <= 200
    assert m["movedByReheat"] < 5


@pytest.mark.parametrize("size", SIZES)
async def test_a_grabbed_node_pulls_its_neighbours_and_everything_comes_back(size: str) -> None:
    async with sim_page() as page:
        tasks = [
            await ask(page, "drag", size, "task", pick, angle)
            for pick, angle in ((10, 0.4), (35, 2.1), (60, 3.7), (85, 5.2))
        ]
        epics = [
            await ask(page, "drag", size, "epic", pick, angle)
            for pick, angle in ((15, 1.0), (55, 4.0))
        ]
    for held in tasks + epics:
        assert held["finite"] and not held["resting"]
        assert held["pinnedAt"] == pytest.approx(150, abs=0.01)
        # let go: it eases back (not at rest at once), then every node is where it was
        assert not held["restingWhileReturning"]
        assert held["ticksToRest"] <= 200
        assert held["restingAfter"]
        assert held["draggedFromHome"] < 3
        assert held["farthestFromHome"] < 5
        assert held["overlapsAfter"] == 0
    # the story and the other tasks of a dragged task follow it; far epics move less than near ones
    assert sum(1 for t in tasks if t["parentCloser"] > 2) >= 3
    assert sum(t["parentCloser"] for t in tasks) / len(tasks) > 5
    assert sum(t["siblingsCloser"] for t in tasks) / len(tasks) > 2
    # the epic's own stories and tasks come with a dragged epic
    assert sum(e["ownTreeCloser"] for e in epics) / len(epics) > 10
    everyone = tasks + epics
    near = sum(held["nearEpicsMoved"] for held in everyone) / len(everyone)
    far = sum(held["farEpicsMoved"] for held in everyone) / len(everyone)
    assert near > 0
    assert far < 0.75 * near


@pytest.mark.parametrize("size", SIZES)
async def test_the_map_is_the_same_every_time_and_does_not_drift(size: str) -> None:
    async with sim_page() as page:
        m = await ask(page, "sameTwice", size)
    assert m["identical"]
    assert m["drift"] < 1


@pytest.mark.parametrize("size", SIZES)
async def test_the_force_sliders_do_what_they_say_and_reset_returns_the_same_map(
    size: str,
) -> None:
    async with sim_page() as page:
        m = await ask(page, "sliders", size)
    for key in ("repel3", "centre3", "distance2", "repel0.2", "centre0.2"):
        assert m[key]["finite"]
        assert m[key]["overlapping"] == 0
        assert m[key]["ticks"] <= 400
        assert m[key]["resetOff"] < 1
        assert m[key]["resetResting"]
    assert m["repel3"]["area"] > 1.15
    assert m["centre3"]["area"] < 0.95
    assert m["distance2"]["link"] > 1.3
    assert m["repel0.2"]["area"] < 1
    assert m["centre0.2"]["area"] > 1
    assert m["mixedResetOff"] < 1


@pytest.mark.parametrize("size", SIZES)
async def test_a_glide_ends_on_its_target_and_no_dots_meet_on_the_way(size: str) -> None:
    async with sim_page() as page:
        m = await ask(page, "glide", size)
    assert not m["restingWhileGliding"]
    assert m["frames"] == 36
    assert m["miss"] < 0.01
    assert m["homeMiss"] < 0.01
    assert m["worstNewOverlap"] < 0.05
    assert m["restingAtEnd"]
    # a grab in the middle of a glide stops it, and the map runs again
    assert m["awakeAfterGrab"]
    assert m["jumpResting"]
    assert m["jumpMiss"] < 0.01


@pytest.mark.parametrize("size", SIZES)
async def test_a_data_refresh_does_not_reshuffle_the_map(size: str) -> None:
    async with sim_page() as page:
        m = await ask(page, "refresh", size)
    assert m["restingAfterSame"]
    assert m["wokeUp"]
    assert m["enteredNear"] < 80
    assert m["ticks"] <= 200
    assert m["grown"] == m["originalNodes"] + 2
    assert m["oldNodesMoved"] < 40
    assert m["overlappingAfterAdd"] == 0
    assert m["nodesAfterRemove"] == m["originalNodes"]
    assert m["overlappingAfterRemove"] == 0
    assert m["finite"]
    assert m["resting"]


async def test_no_node_ever_goes_missing_or_not_a_number() -> None:
    async with sim_page() as page:
        m = await ask(page, "odd")
    assert m["emptyResting"] and m["emptyTick"] is False
    assert m["loneAt"] == [0, 0] and m["loneResting"]
    assert m["oddFinite"]
    assert m["oddOverlapping"] == 0
    assert m["projectAt"] == [0, 0]
    assert m["oddResting"]
