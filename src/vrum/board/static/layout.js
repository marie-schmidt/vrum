// Where the dots go: a small force layout of our own. Dots push each other apart, lines pull their
// two ends to a comfortable length, and everything is drawn gently towards the middle. A pinned
// dot stays where it was put. The positions are kept between draws so that a change on the server
// moves things a little instead of starting again.

const SPACING = 70; // how far apart unrelated dots like to be
const CELL = SPACING * 3; // dots further apart than this do not push each other
const COOLING = 0.965;
const SETTLED = 0.03;

export function createLayout() {
  const pos = new Map(); // key -> { x, y, vx, vy }
  const pinned = new Set();
  let dots = [];
  let edges = [];
  let alpha = 0;

  // Fixed starting places, so the same graph always starts the same way.
  const spiral = (index) => {
    const angle = index * 2.399963;
    const radius = 26 * Math.sqrt(index + 1);
    return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
  };

  function set(nextDots, nextEdges) {
    const keys = new Set(nextDots.map((dot) => dot.key));
    for (const key of [...pos.keys()]) {
      if (!keys.has(key)) pos.delete(key);
    }
    for (const key of [...pinned]) {
      if (!keys.has(key)) pinned.delete(key);
    }
    dots = nextDots;
    edges = nextEdges.filter((edge) => keys.has(edge.source) && keys.has(edge.target));
    const fresh = dots.filter((dot) => !pos.has(dot.key));
    // A new dot starts next to a neighbour that is already placed, if it has one.
    const placed = (key) => pos.get(key);
    fresh.forEach((dot, index) => {
      const joined = edges
        .map((edge) => (edge.source === dot.key ? edge.target : edge.target === dot.key ? edge.source : null))
        .find((other) => other !== null && placed(other));
      const base = joined ? placed(joined) : spiral(pos.size + index);
      const jitter = spiral(index + 3);
      pos.set(dot.key, { x: base.x + (joined ? jitter.x * 0.5 : 0), y: base.y + (joined ? jitter.y * 0.5 : 0), vx: 0, vy: 0 });
    });
    alpha = fresh.length ? 1 : Math.max(alpha, 0.3);
  }

  function step() {
    if (alpha < SETTLED) return false;
    const grid = new Map();
    for (const dot of dots) {
      const p = pos.get(dot.key);
      const cell = `${Math.floor(p.x / CELL)},${Math.floor(p.y / CELL)}`;
      if (!grid.has(cell)) grid.set(cell, []);
      grid.get(cell).push(p);
    }
    const force = new Map(dots.map((dot) => [dot.key, { x: 0, y: 0 }]));
    const keyOf = new Map(dots.map((dot) => [pos.get(dot.key), dot.key]));
    for (const dot of dots) {
      const p = pos.get(dot.key);
      const f = force.get(dot.key);
      const cx = Math.floor(p.x / CELL);
      const cy = Math.floor(p.y / CELL);
      for (let gx = cx - 1; gx <= cx + 1; gx += 1) {
        for (let gy = cy - 1; gy <= cy + 1; gy += 1) {
          for (const q of grid.get(`${gx},${gy}`) || []) {
            if (q === p) continue;
            let dx = p.x - q.x;
            let dy = p.y - q.y;
            let d2 = dx * dx + dy * dy;
            if (d2 === 0) {
              dx = (keyOf.get(q) < dot.key ? 1 : -1) * 0.5;
              dy = 0.5;
              d2 = 0.5;
            }
            if (d2 > CELL * CELL) continue;
            const push = (SPACING * SPACING) / d2;
            f.x += dx * push * 0.1;
            f.y += dy * push * 0.1;
          }
        }
      }
      // A light pull to the middle keeps separate pieces in the picture.
      f.x -= p.x * 0.004;
      f.y -= p.y * 0.004;
    }
    for (const edge of edges) {
      const a = pos.get(edge.source);
      const b = pos.get(edge.target);
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.max(Math.hypot(dx, dy), 0.01);
      const rest = edge.type === 'hierarchy' ? 55 : 95;
      const pull = ((d - rest) / d) * 0.08;
      force.get(edge.source).x += dx * pull;
      force.get(edge.source).y += dy * pull;
      force.get(edge.target).x -= dx * pull;
      force.get(edge.target).y -= dy * pull;
    }
    for (const dot of dots) {
      if (pinned.has(dot.key)) continue;
      const p = pos.get(dot.key);
      const f = force.get(dot.key);
      p.vx = (p.vx + f.x * alpha) * 0.62;
      p.vy = (p.vy + f.y * alpha) * 0.62;
      const speed = Math.hypot(p.vx, p.vy);
      if (speed > 30) {
        p.vx = (p.vx / speed) * 30;
        p.vy = (p.vy / speed) * 30;
      }
      p.x += p.vx;
      p.y += p.vy;
    }
    alpha *= COOLING;
    return true;
  }

  return {
    set,
    step,
    // Run until the picture has settled, or for at most `limit` steps.
    settle(limit) {
      for (let count = 0; count < limit && step(); count += 1);
    },
    at: (key) => pos.get(key),
    move(key, x, y) {
      const p = pos.get(key);
      if (!p) return;
      p.x = x;
      p.y = y;
      p.vx = 0;
      p.vy = 0;
      pinned.add(key);
      alpha = Math.max(alpha, 0.3);
    },
    isPinned: (key) => pinned.has(key),
    togglePin(key) {
      if (pinned.has(key)) pinned.delete(key);
      else pinned.add(key);
      alpha = Math.max(alpha, 0.3);
      return pinned.has(key);
    },
    releaseAll() {
      pinned.clear();
      alpha = Math.max(alpha, 0.5);
    },
    pinnedCount: () => pinned.size,
    active: () => alpha >= SETTLED,
    bounds() {
      const all = dots.map((dot) => pos.get(dot.key));
      if (!all.length) return { x0: -100, y0: -100, x1: 100, y1: 100 };
      return {
        x0: Math.min(...all.map((p) => p.x)),
        y0: Math.min(...all.map((p) => p.y)),
        x1: Math.max(...all.map((p) => p.x)),
        y1: Math.max(...all.map((p) => p.y)),
      };
    },
  };
}
