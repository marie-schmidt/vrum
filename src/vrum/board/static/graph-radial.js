// Where everything goes on the Structure map. Two layouts, both worked out from the hierarchy alone
// (epics, their stories, their tasks) and the same on every visit:
//
//  - the whole project: each story is a little dandelion (its tasks fan out away from it), each epic a
//    bunch of dandelions round a hub, and the epics sit round the project like a tree seen from above.
//    Every cluster is worked out with the way to its parent at angle 0 and then turned to face its
//    parent, so the lines up the tree come in through a gap that is kept clear. Clusters are spaced by
//    their radii, so nothing overlaps and no hierarchy line crosses another.
//  - one epic: the epic in the middle, its stories in a ring, and every task as a leaf on the outer
//    ring, each with a slot of its own.
//
// Nothing here draws or reads the page: it takes the tree and gives back coordinates.

const TWO_PI = Math.PI * 2;
const sum = (list) => list.reduce((a, b) => a + b, 0);
const max = (list, floor) => (list.length ? Math.max(...list) : floor);

export const RANK = { epic: 0, story: 1, task: 2 };
export const LEVEL_MAX = { epics: 0, stories: 1, tasks: 2 };
export const TASK_DOT = { low: 3.2, normal: 4, high: 4.8, urgent: 5.6 };
export const LEAF_DOT = { low: 5, normal: 6.5, high: 8, urgent: 9.5 };

// The hierarchy as plain data: [{ ref, priority, stories: [{ ref, priority, tasks: [{ ref, priority }] }] }].
export function treeOf(model) {
  const leaf = (node) => ({ ref: node.ref, priority: node.priority });
  return model.epics.map((epic) => ({
    ...leaf(epic),
    stories: model.kids(epic.ref).map((story) => ({ ...leaf(story), tasks: model.kids(story.ref).map(leaf) })),
  }));
}

// What the layout depends on, to know when it has to be worked out again.
export const shapeOf = (tree) =>
  tree.map((epic) => `${epic.ref}:${epic.priority}[${epic.stories.map((story) => `${story.ref}:${story.priority}(${story.tasks.map((task) => `${task.ref}:${task.priority}`).join(',')})`).join(',')}]`).join(';');

// A small random source, so that "random" looks the same on every visit.
export function seeded(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rotate = (x, y, a) => [x * Math.cos(a) - y * Math.sin(a), x * Math.sin(a) + y * Math.cos(a)];

const cache = new Map();

// The whole project at one level (epics, stories or tasks): { nodes, edges, pos, bounds, rProj }. A node
// is { ref, kind, x, y, r, parent, epic } (`epic` is the place of its epic in the list, which colours it);
// an edge is [parent, ref], the parent being 'root' for an epic.
export function wholeLayout(tree, level) {
  const key = `${level}|${shapeOf(tree)}`;
  if (cache.has(key)) return cache.get(key);
  const found = lay(tree, level);
  cache.clear();
  cache.set(key, found);
  return found;
}

// How big a hub is drawn at each level: from the smallest to the biggest by how many tasks hang under
// it, as Obsidian sizes a note by its links. [least, most] in the map's own units.
const HUB_SIZE = {
  epics: { epic: [26, 38] },
  stories: { epic: [20, 30], story: [10, 14.5] },
  tasks: { epic: [19, 31], story: [7, 11] },
};
const tasksUnder = (node) => (node.stories ? sum(node.stories.map((story) => story.tasks.length)) : node.tasks.length);

// The radius of every epic and story, by the square root of its tasks against the busiest of its kind.
export function hubRadii(tree, level) {
  const sizes = HUB_SIZE[level];
  const found = new Map();
  const give = (nodes, [least, most]) => {
    const peak = max(nodes.map(tasksUnder), 0);
    for (const node of nodes) found.set(node.ref, Math.round((least + (most - least) * Math.sqrt(peak ? tasksUnder(node) / peak : 0)) * 10) / 10);
  };
  give(tree, sizes.epic);
  if (sizes.story) give(tree.flatMap((epic) => epic.stories), sizes.story);
  return found;
}

function lay(tree, level) {
  const rnd = seeded(20261117);
  const maxRank = LEVEL_MAX[level];
  const radius = hubRadii(tree, level);
  const rProj = 50;

  const storyCluster = (story) => {
    const tasks = maxRank > 1 ? story.tasks : [];
    const n = tasks.length;
    const rStory = radius.get(story.ref);
    if (!n) return { node: story, r: rStory, R: rStory + 5, dots: [] };
    const gap = 0.5;
    const fan = Math.min(TWO_PI - 2 * gap, (n - 1) * 0.85);
    const step = n > 1 ? fan / (n - 1) : 0;
    const d = Math.max(rStory + TASK_DOT.urgent + 5, n > 1 ? 13.6 / step : 0);
    const dots = tasks.map((task, i) => {
      const a = Math.PI + (n > 1 ? -fan / 2 + i * step : 0) + (rnd() - 0.5) * step * 0.1;
      const dist = d + (rnd() - 0.5) * 2;
      return { node: task, x: dist * Math.cos(a), y: dist * Math.sin(a), r: TASK_DOT[task.priority] || TASK_DOT.normal };
    });
    return { node: story, r: rStory, R: max(dots.map((o) => Math.hypot(o.x, o.y) + o.r), 0) + 4, dots };
  };

  const epicCluster = (epic) => {
    const stories = maxRank > 0 ? epic.stories : [];
    const rEpic = radius.get(epic.ref);
    if (!stories.length) return { node: epic, r: rEpic, R: rEpic + 8, items: [] };
    const cl = stories.map(storyCluster);
    const R = cl.map((c) => c.R);
    const m = cl.length;
    const G = 9;
    const gap = 0.55;
    const span = TWO_PI - 2 * gap;
    const arcs = (dist) => R.slice(0, -1).map((r, i) => 2 * Math.asin(Math.min(1, (r + R[i + 1] + G) / (2 * dist))));
    let dist = Math.max(rEpic + max(R, 0) + G, (R[0] + 6) / Math.sin(gap), (R[m - 1] + 6) / Math.sin(gap));
    while (sum(arcs(dist)) > span) dist += Math.max(1, dist * 0.01);
    const gaps = arcs(dist);
    const total = sum(gaps);
    const target = Math.min(span, Math.max(total, (m - 1) * 0.95));
    const f = total ? target / total : 0;
    let a = Math.PI - target / 2;
    const items = cl.map((c, i) => {
      if (i) a += gaps[i - 1] * f;
      return { cl: c, x: dist * Math.cos(a), y: dist * Math.sin(a) };
    });
    return { node: epic, r: rEpic, R: dist + max(R, 0), items };
  };

  const clusters = tree.map(epicCluster);
  const n = clusters.length;
  const rEpic = max(clusters.map((c) => c.r), 0);
  if (!n) return { nodes: [], edges: [], pos: new Map(), bounds: { x0: -rProj, y0: -rProj, x1: rProj, y1: rProj }, rProj, rEpic };
  const Re = clusters.map((c) => c.R);

  // The epics sit round the project on a wide ellipse (the picture is wider than tall): each as far from
  // the next as their sizes need, then spread evenly.
  const GE = 30;
  const need = Re.map((r, i) => r + Re[(i + 1) % n] + GE);
  const stepFrom = (a, b, t, dist) => {
    const x0 = a * Math.cos(t);
    const y0 = b * Math.sin(t);
    let u = t;
    while (u - t < Math.PI) {
      u += 0.004;
      if (Math.hypot(a * Math.cos(u) - x0, b * Math.sin(u) - y0) >= dist) return u - t;
    }
    return null;
  };
  const RHO = 1.3;
  const around = (b, needs) => {
    const a = RHO * b;
    const steps = [];
    let t = -Math.PI / 2;
    for (let i = 0; i < n; i += 1) {
      const dt = stepFrom(a, b, t, needs[i]);
      if (dt === null) return null;
      steps.push(dt);
      t += dt;
    }
    return sum(steps) <= TWO_PI ? steps : null;
  };

  const build = (needs) => {
    let b = rProj + max(Re, 0);
    let steps = around(b, needs);
    while (!steps) {
      b += Math.max(2, b * 0.01);
      steps = around(b, needs);
    }
    const aEll = RHO * b;
    const f = TWO_PI / sum(steps);
    const nodes = [];
    const edges = [];
    const pos = new Map();
    const put = (node, kind, x, y, r, parent, epic) => {
      const o = { ref: node.ref, kind, x, y, r, parent, epic };
      nodes.push(o);
      pos.set(node.ref, o);
      edges.push([parent, node.ref]);
      return o;
    };
    let t = -Math.PI / 2;
    clusters.forEach((c, i) => {
      if (i) t += steps[i - 1] * f;
      const ex = aEll * Math.cos(t);
      const ey = b * Math.sin(t);
      put(c.node, 'epic', ex, ey, c.r, 'root', i);
      const phi = Math.atan2(-ey, -ex);
      c.items.forEach((it) => {
        const [lx, ly] = rotate(it.x, it.y, phi);
        const sx = ex + lx;
        const sy = ey + ly;
        put(it.cl.node, 'story', sx, sy, it.cl.r, c.node.ref, i);
        const psi = Math.atan2(ey - sy, ex - sx);
        it.cl.dots.forEach((dot) => {
          const [dx, dy] = rotate(dot.x, dot.y, psi);
          put(dot.node, 'task', sx + dx, sy + dy, dot.r, it.cl.node.ref, i);
        });
      });
    });
    return { nodes, edges, pos };
  };

  // A line from the project to one epic must not brush the dots of another: where it does, those two
  // neighbourhoods are given more room and the whole thing is laid out again.
  const brushes = (built) => {
    const out = [];
    tree.forEach((epic, i) => {
      const hub = built.pos.get(epic.ref);
      const len2 = hub.x * hub.x + hub.y * hub.y;
      for (const o of built.nodes) {
        if (o.epic === i) continue;
        const t = Math.max(0, Math.min(1, (o.x * hub.x + o.y * hub.y) / len2));
        if (Math.hypot(o.x - t * hub.x, o.y - t * hub.y) < o.r + 7) out.push([o.epic, i]);
      }
    });
    return out;
  };
  const needs = need.slice();
  let built = build(needs);
  for (let pass = 0; pass < 120; pass += 1) {
    const bad = brushes(built);
    if (!bad.length) break;
    bad.forEach(([v, s]) => [v, (v + n - 1) % n, s, (s + n - 1) % n].forEach((i) => { needs[i] += 4; }));
    built = build(needs);
  }
  const bounds = { x0: -rProj, y0: -rProj, x1: rProj, y1: rProj };
  for (const o of built.nodes) {
    bounds.x0 = Math.min(bounds.x0, o.x - o.r);
    bounds.x1 = Math.max(bounds.x1, o.x + o.r);
    bounds.y0 = Math.min(bounds.y0, o.y - o.r);
    bounds.y1 = Math.max(bounds.y1, o.y + o.r);
  }
  return { ...built, bounds, rProj, rEpic };
}

// -- one epic ------------------------------------------------------------------------------------

// The epic in the middle, its stories in a ring and its tasks on the outer ring. A story with no tasks is
// itself a leaf on the outer ring. Every leaf gets a slot of its own, so the lines never cross.
export function epicRadial(epic) {
  const groups = epic.stories.map((story) => ({ story, leaves: story.tasks.length ? story.tasks : [story], leafStory: !story.tasks.length }));
  if (!groups.length) return { groups, empty: true, pos: new Map(), R2: 150 };
  const MIN = 2.6;
  const GAP = 0.6;
  const PITCH = 25;
  const weights = groups.map((g) => (g.leafStory ? 1 : Math.max(g.leaves.length, MIN)) + GAP);
  const W = sum(weights);
  const R2 = Math.max(150, (W * PITCH) / TWO_PI);
  const a = TWO_PI / W;
  const rS = R2 * 0.55;
  const pos = new Map();
  let start = -Math.PI / 2;
  groups.forEach((g, i) => {
    const span = weights[i] * a;
    g.a0 = start;
    g.a1 = start + span;
    g.mid = start + span / 2;
    g.leaves.forEach((leaf, j) => {
      const ang = g.mid + (j - (g.leaves.length - 1) / 2) * a;
      pos.set(leaf.ref, { x: R2 * Math.cos(ang), y: R2 * Math.sin(ang), ang, r: R2, leaf: true });
    });
    if (!g.leafStory) pos.set(g.story.ref, { x: rS * Math.cos(g.mid), y: rS * Math.sin(g.mid), ang: g.mid, r: rS, leaf: false });
    start += span;
  });
  return { groups, R2, rS, a, pos, empty: false };
}

export const polar = (r, a) => [r * Math.cos(a), r * Math.sin(a)];
export const angleBetween = (a, b) => ((((b - a + Math.PI) % TWO_PI) + TWO_PI) % TWO_PI) - Math.PI;

// The path of a wedge of the ring between two radii.
export function sector(r0, r2, a0, a1) {
  const [x0, y0] = polar(r0, a0);
  const [x1, y1] = polar(r0, a1);
  const [x2, y2] = polar(r2, a1);
  const [x3, y3] = polar(r2, a0);
  const big = a1 - a0 > Math.PI ? 1 : 0;
  const f = (n) => Math.round(n * 10) / 10;
  return `M${f(x0)} ${f(y0)} A${r0} ${r0} 0 ${big} 1 ${f(x1)} ${f(y1)} L${f(x2)} ${f(y2)} A${r2} ${r2} 0 ${big} 0 ${f(x3)} ${f(y3)} Z`;
}

// A curve from a story to one of its leaves that bends round the middle.
export function treeCurve(from, to) {
  const f = (n) => Math.round(n * 10) / 10;
  const mr = (from.r + to.r) / 2;
  const [c1x, c1y] = polar(mr, from.ang);
  const [c2x, c2y] = polar(mr, to.ang);
  return `M${f(from.x)} ${f(from.y)} C${f(c1x)} ${f(c1y)} ${f(c2x)} ${f(c2y)} ${f(to.x)} ${f(to.y)}`;
}

// How far a one-epic picture reaches, with room for the names on the outer ring: { x, y, w, h }.
export function epicExtent(layout, full) {
  const labelLen = full ? 30 * 6.3 : 14 * 6.5;
  let minX = -60;
  let maxX = 60;
  let minY = -60;
  let maxY = 60;
  layout.pos.forEach((p) => {
    if (!p.leaf) return;
    const end = polar(p.r + 18 + labelLen, p.ang);
    const side = polar(8, p.ang + Math.PI / 2);
    for (const [x, y] of [end, [end[0] + side[0], end[1] + side[1]], [end[0] - side[0], end[1] - side[1]], polar(p.r + 18, p.ang)]) {
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minY = Math.min(minY, y);
      maxY = Math.max(maxY, y);
    }
  });
  return { x: minX - 14, y: minY - 14, w: maxX - minX + 28, h: maxY - minY + 28 + 40 };
}
