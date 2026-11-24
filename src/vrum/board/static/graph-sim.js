// The physics of the Structure map: a small force simulation in the style of d3-force (velocity Verlet with a
// unit time step and a cooling `alpha`), pure like graph-radial.js: it takes the tree as data and gives back
// coordinates, touches no page and draws nothing. It uses no clock and no `Math.random`: the same input
// gives the same map on every visit.
//
// The forces: every link of the tree is a spring (an epic hangs from the project on a weak, long one, so
// each finds its own distance; stories and tasks on short, stiff ones), bigger dots repel harder and only
// within a reach, every dot is pulled gently to the middle, a soft collision keeps dots apart by their own
// radius plus a gap, a "lean" puts leaves farther out than what they hang from, and one force d3 does not
// have: each epic's whole sub-tree is a disc, and overlapping discs push each other apart by moving every
// member together. Nothing is saved anywhere: a node that was dragged is eased back to `home`, the place
// the map settled in, and the `home` of a map settled with the four sliders at 1 is what Reset returns to.

const ALPHA_MIN = 0.001;
const RANK = { project: 0, epic: 1, story: 2, task: 3 };

// By rank (project, epic, story, task). A link belongs to the child: it runs to the child's parent.
const LINK_DISTANCE = [0, 0, 52, 22];
const LINK_STRENGTH = [0, 0.12, 1, 0.9];
// An epic's link to the project is longer the more hangs from the epic (this much plus this much times
// the square root of the number of nodes below it).
const EPIC_LINK_BASE = 110;
const EPIC_LINK_PER = 12;
const CHARGE = [0, 260, 50, 14];
const REACH = [0, 320, 110, 60];
const GAP = [0, 6, 5, 4];
// A story must sit this much farther from the middle than its epic, a task than its story.
const LEAN = [0, 0, 20, 30];
const LEAN_STRENGTH = 0.7;
const CENTRE_STRENGTH = 0.02;
const COLLIDE_STRENGTH = 0.85;
const COLLIDE_ITERATIONS = 2;
const GROUP_GAP = 40;
const GROUP_STRENGTH = 0.8;
// The sliders: how a multiplier of 1 to 3 turns into force (repel is cubed like Obsidian's, the centre
// pull is squared, and the gap between sub-trees follows both).
const REPEL_POWER = 3;
const CENTRE_POWER = 2;
const GAP_POWER = 0.5;
const FORCES_HEAT = 0.3;
// Holding a node: the sim never rests (alpha target), and every node outside the held node's own sub-tree is
// anchored to its home, more the farther it is from the pointer.
const GRAB_ALPHA = 0.3;
const GRAB_TARGET = 0.12;
const ANCHOR = 6;
const ANCHOR_WEAKEST = 0.1;
const ANCHOR_NEAR = 80;
const ANCHOR_SPAN = 200;
// The pull toward home does not die away with alpha, so it is the strongest force as the map cools.
const HOME_FLOOR = 0.1;
const RETURN_FRAMES = 100;
const RESET_FRAMES = 60;
// While gliding, dots are kept this far apart at least (the tidy layout leaves at least 2.9 between dots).
const APART = 1;
const KEEP_SLACK = 8;
// A settle holds alpha at 1 for this part of its ticks and cools over the rest.
const SETTLE_HOT = 0.4;
const smooth = (t) => t * t * (3 - 2 * t);
const easeOut = (t) => 1 - (1 - t) ** 3;

const PAIR_CHARGE = CHARGE.map((a) => CHARGE.map((b) => Math.sqrt(a * b)));
const PAIR_REACH = REACH.map((a) => REACH.map((b) => Math.min(a, b)));

const CAP_CELLS = 360;

function lcg(seed) {
  let a = (seed >>> 0) || 1;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const num = (v, fallback = 0) => (Number.isFinite(v) ? v : fallback);
const rankOf = (kind) => (kind in RANK ? RANK[kind] : 3);

// A uniform grid over some of the nodes. Cells are dense (not hashed), so a query never sees one dot twice.
class Grid {
  constructor() {
    this.head = new Int32Array(0);
    this.next = new Int32Array(0);
    this.cell = 1;
    this.x0 = 0;
    this.y0 = 0;
    this.cols = 1;
    this.rows = 1;
  }

  build(list, count, X, Y, cell) {
    if (this.next.length < X.length) this.next = new Int32Array(X.length);
    let x0 = Infinity;
    let y0 = Infinity;
    let x1 = -Infinity;
    let y1 = -Infinity;
    for (let k = 0; k < count; k += 1) {
      const i = list[k];
      if (X[i] < x0) x0 = X[i];
      if (X[i] > x1) x1 = X[i];
      if (Y[i] < y0) y0 = Y[i];
      if (Y[i] > y1) y1 = Y[i];
    }
    if (!count) {
      x0 = 0;
      y0 = 0;
      x1 = 0;
      y1 = 0;
    }
    const size = Math.max(cell, (x1 - x0) / CAP_CELLS, (y1 - y0) / CAP_CELLS, 1e-6);
    this.cell = size;
    this.x0 = x0;
    this.y0 = y0;
    this.cols = Math.floor((x1 - x0) / size) + 1;
    this.rows = Math.floor((y1 - y0) / size) + 1;
    const cells = this.cols * this.rows;
    if (this.head.length < cells) this.head = new Int32Array(cells);
    this.head.fill(-1, 0, cells);
    for (let k = 0; k < count; k += 1) {
      const i = list[k];
      const c = Math.floor((Y[i] - y0) / size) * this.cols + Math.floor((X[i] - x0) / size);
      this.next[i] = this.head[c];
      this.head[c] = i;
    }
  }

  lo(v, v0, n) {
    const c = Math.floor((v - v0) / this.cell);
    return c < 0 ? 0 : c >= n ? n - 1 : c;
  }
}

export function createSim(spec = {}) {
  const opt = { alphaMin: ALPHA_MIN, alphaDecay: 0.035, velocityDecay: 0.4, homeStrength: 0.5, seed: 1, ...(spec.options || {}) };
  const rnd = lcg(opt.seed);
  const jiggle = () => (rnd() - 0.5) * 1e-4;

  const nodes = [];
  const byId = new Map();
  const mult = { centre: 1, repel: 1, link: 1, distance: 1 };
  const grabbed = new Map();
  let alpha = 1;
  let alphaTarget = 0;
  let glide = null;
  let homeReady = false;
  let carry = null;
  let pendingHome = true;
  let model = null;
  let dirty = true;

  for (const input of spec.nodes || []) adopt(input);

  function adopt(input) {
    input.kind = input.kind || 'task';
    input.r = Math.max(0, num(input.r));
    input.x = num(input.x, 0);
    input.y = num(input.y, 0);
    input.vx = 0;
    input.vy = 0;
    if (input.kind === 'project') {
      input.id = input.id ?? 'root';
      input.fx = 0;
      input.fy = 0;
      input.x = 0;
      input.y = 0;
    } else if (typeof input.fx !== 'number' || typeof input.fy !== 'number') {
      input.fx = null;
      input.fy = null;
    }
    nodes.push(input);
    byId.set(input.id, input);
    dirty = true;
  }

  // Everything a step needs, in flat arrays: rebuilt when the shape of the tree changes.
  function build() {
    const n = nodes.length;
    const index = new Map();
    nodes.forEach((node, i) => index.set(node.id, i));
    const m = {
      n,
      index,
      X: new Float64Array(n),
      Y: new Float64Array(n),
      VX: new Float64Array(n),
      VY: new Float64Array(n),
      FX: new Float64Array(n),
      FY: new Float64Array(n),
      HX: new Float64Array(n),
      HY: new Float64Array(n),
      DX: new Float64Array(n),
      DY: new Float64Array(n),
      homed: new Uint8Array(n),
      hasDefault: false,
      pinned: new Uint8Array(n),
      rank: new Uint8Array(n),
      r: new Float64Array(n),
      rad: new Float64Array(n),
      parent: new Int32Array(n).fill(-1),
      deg: new Int32Array(n),
      bias: new Float64Array(n),
      byRank: [[], [], [], []],
      lists: [],
      grids: [null, new Grid(), new Grid(), new Grid()],
      collideGrid: new Grid(),
      groups: [],
      group: new Int32Array(n),
    };
    nodes.forEach((node, i) => {
      m.rank[i] = rankOf(node.kind);
      m.r[i] = node.r;
      m.rad[i] = node.r + GAP[m.rank[i]];
      m.byRank[m.rank[i]].push(i);
      const p = node.parent == null ? undefined : index.get(node.parent);
      if (p !== undefined && p !== i) {
        m.parent[i] = p;
        m.deg[i] += 1;
        m.deg[p] += 1;
      }
    });
    for (let i = 0; i < n; i += 1) {
      const p = m.parent[i];
      if (p >= 0) m.bias[i] = m.deg[p] / (m.deg[p] + m.deg[i]);
    }
    m.lists = m.byRank.map((list) => Int32Array.from(list));
    let maxRad = 0;
    let maxDot = 1;
    for (let i = 0; i < n; i += 1) {
      if (m.rank[i] === 0) continue;
      if (m.rad[i] > maxRad) maxRad = m.rad[i];
      if (m.r[i] > maxDot) maxDot = m.r[i];
    }
    m.maxRad = maxRad;
    m.maxDot = maxDot;
    const groupIds = new Map();
    nodes.forEach((node, i) => {
      const id = node.epic ?? node.id;
      if (!groupIds.has(id)) {
        groupIds.set(id, m.groups.length);
        m.groups.push({ members: [], cx: 0, cy: 0, R: 0 });
      }
      const g = groupIds.get(id);
      m.group[i] = g;
      m.groups[g].members.push(i);
    });
    // An epic hangs from the project on a link that is longer the more hangs from the epic.
    m.rest = new Float64Array(n);
    for (let i = 0; i < n; i += 1) {
      const below = m.groups[m.group[i]].members.length - 1;
      m.rest[i] = m.rank[i] === 1 ? EPIC_LINK_BASE + EPIC_LINK_PER * Math.sqrt(below) : LINK_DISTANCE[m.rank[i]];
    }
    return m;
  }

  // When the shape of the tree changes, the nodes that stay keep their home, so a data refresh does not
  // reshuffle the map; the new ones have none until the map rests again.
  function ensure() {
    if (!dirty && model) return model;
    const kept = carry;
    carry = null;
    model = build();
    dirty = false;
    homeReady = false;
    pendingHome = true;
    if (kept) {
      nodes.forEach((node, i) => {
        const h = kept.get(node.id);
        if (!h) return;
        model.HX[i] = h.x;
        model.HY[i] = h.y;
        model.homed[i] = 1;
        homeReady = true;
      });
    }
    return model;
  }

  function keepHome() {
    if (!carry && homeReady && model && !dirty) carry = sim.home;
  }

  function gather(m) {
    for (let i = 0; i < m.n; i += 1) {
      const node = nodes[i];
      m.X[i] = node.x;
      m.Y[i] = node.y;
      m.VX[i] = node.vx;
      m.VY[i] = node.vy;
      const pin = typeof node.fx === 'number' && typeof node.fy === 'number';
      m.pinned[i] = pin ? 1 : 0;
      if (pin) {
        m.FX[i] = node.fx;
        m.FY[i] = node.fy;
      }
    }
  }

  function scatter(m) {
    for (let i = 0; i < m.n; i += 1) {
      const node = nodes[i];
      node.x = m.X[i];
      node.y = m.Y[i];
      node.vx = m.VX[i];
      node.vy = m.VY[i];
    }
  }

  // -- forces ------------------------------------------------------------------------------------------

  function links(m, a) {
    const { X, Y, VX, VY, parent, bias, rank } = m;
    const k = a * mult.link;
    for (let i = 0; i < m.n; i += 1) {
      const p = parent[i];
      if (p < 0) continue;
      let dx = X[i] + VX[i] - X[p] - VX[p];
      let dy = Y[i] + VY[i] - Y[p] - VY[p];
      if (dx === 0) dx = jiggle();
      if (dy === 0) dy = jiggle();
      const len = Math.sqrt(dx * dx + dy * dy);
      const f = ((len - m.rest[i] * mult.distance) / len) * k * LINK_STRENGTH[rank[i]];
      dx *= f;
      dy *= f;
      const b = bias[i];
      VX[i] -= dx * b;
      VY[i] -= dy * b;
      VX[p] += dx * (1 - b);
      VY[p] += dy * (1 - b);
    }
  }

  // The epics, stories and tasks (everything but the project), as one list of indices.
  function moving(m) {
    if (!m.all) m.all = Int32Array.from([...m.lists[1], ...m.lists[2], ...m.lists[3]]);
    return m.all;
  }

  function repel(m, a) {
    const { X, Y, VX, VY, lists, grids } = m;
    const scale = a * mult.repel ** REPEL_POWER;
    for (let s = 1; s <= 3; s += 1) grids[s].build(lists[s], lists[s].length, X, Y, REACH[s]);
    for (let r = 1; r <= 3; r += 1) {
      const mine = lists[r];
      for (let q = 0; q < mine.length; q += 1) {
        const i = mine[q];
        for (let s = r; s <= 3; s += 1) {
          const reach = PAIR_REACH[r][s];
          const k = PAIR_CHARGE[r][s] * scale;
          const g = grids[s];
          const cx0 = g.lo(X[i] - reach, g.x0, g.cols);
          const cx1 = g.lo(X[i] + reach, g.x0, g.cols);
          const cy0 = g.lo(Y[i] - reach, g.y0, g.rows);
          const cy1 = g.lo(Y[i] + reach, g.y0, g.rows);
          for (let cy = cy0; cy <= cy1; cy += 1) {
            for (let cx = cx0; cx <= cx1; cx += 1) {
              for (let j = g.head[cy * g.cols + cx]; j !== -1; j = g.next[j]) {
                if (s === r && j <= i) continue;
                let dx = X[j] - X[i];
                let dy = Y[j] - Y[i];
                let d2 = dx * dx + dy * dy;
                if (d2 >= reach * reach) continue;
                if (d2 < 1e-6) {
                  dx = jiggle();
                  dy = jiggle();
                  d2 = dx * dx + dy * dy;
                }
                const d = Math.max(1, Math.sqrt(d2));
                const f = (k * (1 / d - 1 / reach)) / d;
                VX[i] -= dx * f;
                VY[i] -= dy * f;
                VX[j] += dx * f;
                VY[j] += dy * f;
              }
            }
          }
        }
      }
    }
  }

  function centre(m, a) {
    const { X, Y, VX, VY, pinned } = m;
    const k = CENTRE_STRENGTH * mult.centre ** CENTRE_POWER * a;
    for (let i = 0; i < m.n; i += 1) {
      if (pinned[i]) continue;
      VX[i] -= X[i] * k;
      VY[i] -= Y[i] * k;
    }
  }

  // Leaves outermost: a story sits farther from the middle than its epic, a task farther than its story.
  function lean(m, a) {
    const { X, Y, VX, VY, parent, bias, rank } = m;
    for (let i = 0; i < m.n; i += 1) {
      const p = parent[i];
      if (p < 0 || rank[i] < 2) continue;
      const ri = Math.hypot(X[i], Y[i]);
      const rp = Math.hypot(X[p], Y[p]);
      const want = LEAN[rank[i]] - (ri - rp);
      if (want <= 0) continue;
      const f = want * LEAN_STRENGTH * a;
      const b = bias[i];
      if (ri > 1e-6) {
        VX[i] += (X[i] / ri) * f * b;
        VY[i] += (Y[i] / ri) * f * b;
      }
      if (rp > 1e-6) {
        VX[p] -= (X[p] / rp) * f * (1 - b);
        VY[p] -= (Y[p] / rp) * f * (1 - b);
      }
    }
  }

  // While something is held, the held node's own sub-tree is free and every other node is anchored to its
  // home, more the farther it is from the pointer, so a drag shakes its neighbourhood and not the whole map.
  function home(m, a) {
    const { X, Y, VX, VY, HX, HY, FX, FY, pinned, homed } = m;
    const base = opt.homeStrength * Math.max(a, HOME_FLOOR);
    const held = [];
    const heldGroups = new Set();
    for (const id of grabbed.keys()) {
      const i = m.index.get(id);
      if (i === undefined) continue;
      held.push(i);
      heldGroups.add(m.group[i]);
    }
    for (let i = 0; i < m.n; i += 1) {
      if (pinned[i] || !homed[i]) continue;
      let k = base;
      if (held.length) {
        if (heldGroups.has(m.group[i])) continue;
        let d = Infinity;
        for (const h of held) d = Math.min(d, Math.hypot(X[i] - FX[h], Y[i] - FY[h]));
        k *= ANCHOR * Math.min(1, Math.max(ANCHOR_WEAKEST, (d - ANCHOR_NEAR) / ANCHOR_SPAN));
      }
      VX[i] += (HX[i] - X[i]) * k;
      VY[i] += (HY[i] - Y[i]) * k;
    }
  }

  // A soft collision by radius plus a gap, not scaled by alpha, so that it keeps working as the map cools.
  function collide(m) {
    const { X, Y, VX, VY, rad, pinned, n } = m;
    const grid = m.collideGrid;
    const all = moving(m);
    if (!all.length) return;
    const fixed = m.lists[0];
    const PX = m.PX || (m.PX = new Float64Array(n));
    const PY = m.PY || (m.PY = new Float64Array(n));
    const cell = 2 * m.maxRad;
    for (let it = 0; it < COLLIDE_ITERATIONS; it += 1) {
      for (let i = 0; i < n; i += 1) {
        PX[i] = X[i] + VX[i];
        PY[i] = Y[i] + VY[i];
      }
      grid.build(all, all.length, PX, PY, cell);
      const g = grid;
      for (let q = 0; q < all.length; q += 1) {
        const i = all[q];
        const reach = rad[i] + m.maxRad;
        const cx0 = g.lo(PX[i] - reach, g.x0, g.cols);
        const cx1 = g.lo(PX[i] + reach, g.x0, g.cols);
        const cy0 = g.lo(PY[i] - reach, g.y0, g.rows);
        const cy1 = g.lo(PY[i] + reach, g.y0, g.rows);
        for (let cy = cy0; cy <= cy1; cy += 1) {
          for (let cx = cx0; cx <= cx1; cx += 1) {
            for (let j = g.head[cy * g.cols + cx]; j !== -1; j = g.next[j]) {
              if (j <= i) continue;
              pair(i, j);
            }
          }
        }
        for (let f = 0; f < fixed.length; f += 1) pair(i, fixed[f]);
      }
    }

    function pair(i, j) {
      let dx = X[i] + VX[i] - X[j] - VX[j];
      let dy = Y[i] + VY[i] - Y[j] - VY[j];
      const r = rad[i] + rad[j];
      let l = dx * dx + dy * dy;
      if (l >= r * r) return;
      if (dx === 0) {
        dx = jiggle();
        l += dx * dx;
      }
      if (dy === 0) {
        dy = jiggle();
        l += dy * dy;
      }
      l = Math.sqrt(l);
      const f = ((r - l) / l) * COLLIDE_STRENGTH;
      dx *= f;
      dy *= f;
      let wi = (rad[j] * rad[j]) / (rad[i] * rad[i] + rad[j] * rad[j]);
      if (pinned[j]) wi = 1;
      if (pinned[i]) wi = 0;
      VX[i] += dx * wi;
      VY[i] += dy * wi;
      VX[j] -= dx * (1 - wi);
      VY[j] -= dy * (1 - wi);
    }
  }

  // Each epic's whole sub-tree is a disc (its centre of mass and its furthest member). Discs that overlap
  // are pushed apart, every member getting the same velocity, the smaller sub-tree moving more, so the
  // total momentum is unchanged. A node held by the pointer is left out of its disc, so that carrying
  // one dot across the map does not shove whole sub-trees about.
  function subtrees(m) {
    const { X, Y, VX, VY, pinned, groups } = m;
    const gapScale = (mult.repel / mult.centre) ** GAP_POWER;
    for (const g of groups) {
      const ms = g.members;
      let free = 0;
      for (let k = 0; k < ms.length; k += 1) if (!pinned[ms[k]]) free += 1;
      g.fixed = free === 0;
      let sx = 0;
      let sy = 0;
      let counted = 0;
      for (let k = 0; k < ms.length; k += 1) {
        if (!g.fixed && pinned[ms[k]]) continue;
        sx += X[ms[k]];
        sy += Y[ms[k]];
        counted += 1;
      }
      g.cx = sx / counted;
      g.cy = sy / counted;
      let R = 0;
      for (let k = 0; k < ms.length; k += 1) {
        const i = ms[k];
        if (!g.fixed && pinned[i]) continue;
        const d = Math.hypot(X[i] - g.cx, Y[i] - g.cy) + m.r[i];
        if (d > R) R = d;
      }
      g.R = R;
    }
    for (let a = 0; a < groups.length; a += 1) {
      for (let b = a + 1; b < groups.length; b += 1) {
        const A = groups[a];
        const B = groups[b];
        if (A.fixed && B.fixed) continue;
        let dx = A.cx - B.cx;
        let dy = A.cy - B.cy;
        if (dx === 0 && dy === 0) {
          dx = jiggle();
          dy = jiggle();
        }
        const d = Math.hypot(dx, dy);
        const over = A.R + B.R + GROUP_GAP * gapScale - d;
        if (over <= 0) continue;
        const push = over * GROUP_STRENGTH;
        const na = A.members.length;
        const nb = B.members.length;
        const wa = A.fixed ? 0 : B.fixed ? 1 : nb / (na + nb);
        const wb = B.fixed ? 0 : A.fixed ? 1 : na / (na + nb);
        const ux = (dx / d) * push;
        const uy = (dy / d) * push;
        if (wa) for (let k = 0; k < na; k += 1) {
          VX[A.members[k]] += ux * wa;
          VY[A.members[k]] += uy * wa;
        }
        if (wb) for (let k = 0; k < nb; k += 1) {
          VX[B.members[k]] -= ux * wb;
          VY[B.members[k]] -= uy * wb;
        }
      }
    }
  }

  function integrate(m) {
    const { X, Y, VX, VY, FX, FY, pinned } = m;
    const keep = 1 - opt.velocityDecay;
    for (let i = 0; i < m.n; i += 1) {
      if (pinned[i]) {
        X[i] = FX[i];
        Y[i] = FY[i];
        VX[i] = 0;
        VY[i] = 0;
      } else {
        VX[i] *= keep;
        VY[i] *= keep;
        X[i] += VX[i];
        Y[i] += VY[i];
      }
    }
  }

  function physics(a) {
    const m = ensure();
    if (!m.n) return;
    gather(m);
    links(m, a);
    repel(m, a);
    centre(m, a);
    lean(m, a);
    if (homeReady) home(m, a);
    collide(m);
    subtrees(m);
    if (!grabbed.size) still(m);
    integrate(m);
    scatter(m);
  }

  // With nothing held, the map as a whole does not drift: the forces are not all equal and opposite
  // (a hub moves less than its leaves), so the average velocity is taken out.
  function still(m) {
    const { VX, VY, pinned } = m;
    let sx = 0;
    let sy = 0;
    let count = 0;
    for (let i = 0; i < m.n; i += 1) {
      if (pinned[i]) continue;
      sx += VX[i];
      sy += VY[i];
      count += 1;
    }
    if (!count) return;
    sx /= count;
    sy /= count;
    for (let i = 0; i < m.n; i += 1) {
      if (pinned[i]) continue;
      VX[i] -= sx;
      VY[i] -= sy;
    }
  }

  // -- state -------------------------------------------------------------------------------------------

  function isResting() {
    return alpha < opt.alphaMin && alphaTarget === 0 && grabbed.size === 0 && !glide;
  }

  function allDefault() {
    return mult.centre === 1 && mult.repel === 1 && mult.link === 1 && mult.distance === 1;
  }

  // `home` is where every node settled: the place the pull brings it back to. The place that was settled
  // with all four forces at 1 is kept apart, so that Reset can bring the map back to exactly it.
  function snapshot() {
    const m = ensure();
    for (let i = 0; i < m.n; i += 1) {
      m.HX[i] = nodes[i].x;
      m.HY[i] = nodes[i].y;
    }
    if (allDefault()) {
      m.DX.set(m.HX);
      m.DY.set(m.HY);
      m.hasDefault = true;
    }
    m.homed.fill(1);
    homeReady = true;
    pendingHome = false;
  }

  function restoreDefault() {
    const m = ensure();
    if (!m.hasDefault) return false;
    m.HX.set(m.DX);
    m.HY.set(m.DY);
    m.homed.fill(1);
    homeReady = true;
    pendingHome = false;
    return true;
  }

  function cool() {
    alpha = 0;
    for (const node of nodes) {
      node.vx = 0;
      node.vy = 0;
    }
    if (pendingHome) snapshot();
  }

  function invalidateHome() {
    homeReady = false;
    pendingHome = true;
    carry = null;
    if (model) model.homed.fill(0);
  }

  function wake(a) {
    glide = null;
    if (alpha < a) alpha = a;
  }

  // Let go of every grabbed node (a glide, a settle or a reset takes over from the pointer).
  function dropGrabs() {
    for (const [id, saved] of grabbed) {
      const node = byId.get(id);
      if (node) {
        node.fx = saved.fx;
        node.fy = saved.fy;
      }
    }
    grabbed.clear();
    alphaTarget = 0;
  }

  // Ease every node from where it is to `to` (x, y pairs by node index) with no forces; the glide ends on
  // the target exactly, resting, with that as the new home.
  function startGlide(to, frames, ease) {
    const m = ensure();
    const from = new Float64Array(m.n * 2);
    nodes.forEach((node, i) => {
      from[2 * i] = node.x;
      from[2 * i + 1] = node.y;
      node.vx = 0;
      node.vy = 0;
    });
    dropGrabs();
    glide = { k: 0, frames: Math.max(1, Math.floor(frames)), from, to, ease };
    if (glide.frames === 1) stepGlide();
  }

  function glideHome(frames, ease) {
    const m = ensure();
    const to = new Float64Array(m.n * 2);
    for (let i = 0; i < m.n; i += 1) {
      to[2 * i] = m.homed[i] ? m.HX[i] : nodes[i].x;
      to[2 * i + 1] = m.homed[i] ? m.HY[i] : nodes[i].y;
    }
    startGlide(to, frames, ease);
  }

  // The places settled with all four forces at 1, which Reset brings the map back to.
  function backToDefault() {
    if (!restoreDefault()) {
      invalidateHome();
      wake(0.3);
      return;
    }
    glideHome(RESET_FRAMES, smooth);
  }

  const sim = {
    nodes,
    byId,
    get alpha() {
      return alpha;
    },
    get resting() {
      return isResting() || !nodes.length;
    },
    get home() {
      const out = new Map();
      if (homeReady) model.HX.forEach((x, i) => out.set(nodes[i].id, { x, y: model.HY[i] }));
      return out;
    },

    tick() {
      if (sim.resting) return false;
      if (glide) return stepGlide();
      alpha += (alphaTarget - alpha) * opt.alphaDecay;
      physics(alpha);
      if (isResting()) cool();
      return !sim.resting;
    },

    // A quiet pre-run: alpha stays at 1 for the first part and then cools to the minimum on the last tick, so
    // the map has the time to settle and is at rest when it ends.
    settle(steps = 160) {
      glide = null;
      dropGrabs();
      homeReady = false;
      const hot = Math.floor(steps * SETTLE_HOT);
      const cooling = Math.max(1, steps - hot);
      const rate = opt.alphaMin ** (1 / cooling);
      alpha = 1;
      for (let k = 0; k < steps; k += 1) {
        if (k >= hot) alpha *= rate;
        physics(alpha);
      }
      pendingHome = true;
      cool();
    },

    grab(id, x, y) {
      const node = byId.get(id);
      if (!node || node.kind === 'project') return;
      if (!grabbed.has(id)) grabbed.set(id, { fx: node.fx, fy: node.fy });
      node.fx = x;
      node.fy = y;
      alphaTarget = GRAB_TARGET;
      wake(GRAB_ALPHA);
    },

    moveTo(id, x, y) {
      const node = byId.get(id);
      if (!node || !grabbed.has(id)) return;
      node.fx = x;
      node.fy = y;
    },

    release(id) {
      const node = byId.get(id);
      const saved = grabbed.get(id);
      if (!node || !saved) return;
      node.fx = saved.fx;
      node.fy = saved.fy;
      grabbed.delete(id);
      if (grabbed.size) return;
      alphaTarget = 0;
      if (homeReady) glideHome(RETURN_FRAMES, easeOut);
      else wake(GRAB_ALPHA);
    },

    reheat(a = 0.3) {
      wake(a);
    },

    setForces(values = {}) {
      for (const key of Object.keys(mult)) if (Number.isFinite(values[key])) mult[key] = values[key];
      if (allDefault()) {
        backToDefault();
        return;
      }
      invalidateHome();
      wake(FORCES_HEAT);
    },

    resetForces() {
      mult.centre = 1;
      mult.repel = 1;
      mult.link = 1;
      mult.distance = 1;
      backToDefault();
    },

    glideTo(positions, frames = 36) {
      const m = ensure();
      const to = new Float64Array(m.n * 2);
      nodes.forEach((node, i) => {
        const target = node.kind === 'project' ? null : positions.get(node.id);
        to[2 * i] = target ? num(target.x, node.x) : node.x;
        to[2 * i + 1] = target ? num(target.y, node.y) : node.y;
      });
      invalidateHome();
      startGlide(to, frames, smooth);
    },

    addOrUpdate(incoming) {
      keepHome();
      let changed = false;
      for (const input of incoming || []) {
        const node = byId.get(input.id);
        if (node) {
          const kind = input.kind || node.kind;
          const r = Math.max(0, num(input.r, node.r));
          const epic = input.epic ?? node.epic;
          const parent = input.parent === undefined ? node.parent : input.parent;
          if (kind !== node.kind || r !== node.r || epic !== node.epic || parent !== node.parent) changed = true;
          Object.assign(node, { kind, r, epic, parent });
        } else {
          const parent = input.parent == null ? null : byId.get(input.parent);
          const angle = rnd() * Math.PI * 2;
          const reach = (parent ? parent.r : 0) + Math.max(0, num(input.r)) + 5;
          input.x = (parent ? parent.x : 0) + Math.cos(angle) * reach;
          input.y = (parent ? parent.y : 0) + Math.sin(angle) * reach;
          adopt(input);
          changed = true;
        }
      }
      changedShape(changed);
    },

    remove(ids) {
      keepHome();
      const gone = new Set(ids);
      let changed = false;
      for (let i = nodes.length - 1; i >= 0; i -= 1) {
        if (gone.has(nodes[i].id) && nodes[i].kind !== 'project') {
          byId.delete(nodes[i].id);
          grabbed.delete(nodes[i].id);
          nodes.splice(i, 1);
          changed = true;
        }
      }
      if (changed && !grabbed.size) alphaTarget = 0;
      changedShape(changed);
    },
  };

  function changedShape(changed) {
    if (!changed) {
      carry = null;
      return;
    }
    dirty = true;
    pendingHome = true;
    wake(0.3);
  }

  // Overlaps are pushed apart while gliding (by the dots' own radii, with no gap), so a glide never
  // brings two dots together that are apart at the end.
  function stepGlide() {
    const m = ensure();
    glide.k += 1;
    const last = glide.k >= glide.frames;
    const e = glide.ease(glide.k / glide.frames);
    for (let i = 0; i < m.n; i += 1) {
      const node = nodes[i];
      node.x = last ? glide.to[2 * i] : glide.from[2 * i] + (glide.to[2 * i] - glide.from[2 * i]) * e;
      node.y = last ? glide.to[2 * i + 1] : glide.from[2 * i + 1] + (glide.to[2 * i + 1] - glide.from[2 * i + 1]) * e;
      node.vx = 0;
      node.vy = 0;
    }
    if (last) {
      glide = null;
      alpha = 0;
      snapshot();
      return false;
    }
    keepApart(m);
    return true;
  }

  // While gliding, overlaps are pushed apart (by the dots' own radii, with no gap), so a glide never brings
  // two dots together that are apart at the end. The first look is at every dot; after that only the dots
  // that were just moved are looked at again, so a glide with little to fix costs one look.
  function keepApart(m) {
    const all = moving(m);
    if (!all.length) return;
    gather(m);
    const { X, Y, r, pinned } = m;
    const grid = m.collideGrid;
    const fixed = m.lists[0];
    const touching = (i, j) => Math.hypot(X[i] - X[j], Y[i] - Y[j]) < r[i] + r[j] + APART;
    const reachOf = (i) => r[i] + m.maxDot + APART + KEEP_SLACK;
    const marked = m.marked || (m.marked = new Uint8Array(m.n));
    let active = all;
    for (let round = 0; round < 30 && active.length; round += 1) {
      // The grid is a little out of date after a push; the slack in the reach makes up for it.
      if (round % 3 === 0) grid.build(all, all.length, X, Y, 2 * m.maxDot + APART + KEEP_SLACK);
      const pairs = [];
      for (let q = 0; q < active.length; q += 1) {
        const i = active[q];
        const reach = reachOf(i);
        const cx0 = grid.lo(X[i] - reach, grid.x0, grid.cols);
        const cx1 = grid.lo(X[i] + reach, grid.x0, grid.cols);
        const cy0 = grid.lo(Y[i] - reach, grid.y0, grid.rows);
        const cy1 = grid.lo(Y[i] + reach, grid.y0, grid.rows);
        for (let cy = cy0; cy <= cy1; cy += 1) {
          for (let cx = cx0; cx <= cx1; cx += 1) {
            for (let j = grid.head[cy * grid.cols + cx]; j !== -1; j = grid.next[j]) {
              if (j !== i && (round > 0 || j > i) && touching(i, j)) pairs.push(i, j);
            }
          }
        }
        for (let f = 0; f < fixed.length; f += 1) if (touching(i, fixed[f])) pairs.push(i, fixed[f]);
      }
      if (!pairs.length) break;
      const moved = [];
      for (let it = 0; it < 3; it += 1) {
        let pushed = false;
        for (let p = 0; p < pairs.length; p += 2) {
          const i = pairs[p];
          const j = pairs[p + 1];
          let dx = X[i] - X[j];
          let dy = Y[i] - Y[j];
          let d = Math.hypot(dx, dy);
          const need = r[i] + r[j] + APART;
          if (d >= need) continue;
          if (d < 1e-9) {
            dx = jiggle();
            dy = jiggle();
            d = Math.hypot(dx, dy);
          }
          const push = (need - d) / d;
          let wi = (r[j] * r[j]) / (r[i] * r[i] + r[j] * r[j] || 1);
          if (pinned[j]) wi = 1;
          if (pinned[i]) wi = 0;
          X[i] += dx * push * wi;
          Y[i] += dy * push * wi;
          X[j] -= dx * push * (1 - wi);
          Y[j] -= dy * push * (1 - wi);
          pushed = true;
          for (const k of [i, j]) {
            if (!marked[k] && !pinned[k]) {
              marked[k] = 1;
              moved.push(k);
            }
          }
        }
        if (!pushed) break;
      }
      for (const k of moved) marked[k] = 0;
      active = Int32Array.from(moved);
    }
    for (let i = 0; i < m.n; i += 1) {
      nodes[i].x = X[i];
      nodes[i].y = Y[i];
    }
  }

  return sim;
}
