// Where the dots go when the Graph shows a hierarchy: in rows, the epics on top, the stories below
// them and the tasks below those, then the context (requirements, then the decisions and facts that
// concern them) below the work. Pieces that are not joined to each other (each epic with what is
// under it) are laid out on their own and set side by side. Within a piece the order of a row is the
// one that crosses the fewest lines, and each dot is put as near as it can be to what it is joined
// to, never closer to its neighbours than room for both. A line between two rows that are not next
// to each other passes through a lane kept for it in every row between, so no line runs through a
// dot it does not join. Dots joined to nothing are put in a grid underneath. Nothing here draws: it
// works out positions and routes from the sizes it is given. Dots are ordered as everywhere else
// (epics, stories, tasks, then requirements, decisions and facts, each by number: `byDot`).

import { TYPE_ORDER, byDot } from './graph-data.js';

const WORK_ROW = { epic: 0, story: 1, task: 2 };
const ROW_GAP = 68; // between one row and the next, where the lines run
const BAND_GAP = 24; // more, between the work and the context under it
const GAP = 24; // between two dots of a row
const PIECE_GAP = 44; // between two pieces that are not joined
const NEXT_GAP = 64; // between two neighbours that a line joins
const LANE = 14; // the width kept for a line that passes through a row
const MOST_LEVELS = 5; // how many rows of context lie below one another at most
const SWEEPS = 14;
const PASSES = 14;

const before = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];

// Positions and routes for these dots and lines. `size(key)` gives { w, h } of a dot. The result has
// the centre of each dot (`positions`), how each line runs (`routes`, by the line's place in
// `edges`), a few labels, and the widest row. `unit` scales every gap with the text.
export function layered(dots, edges, size, { unit = 1 } = {}) {
  const u = unit;
  const known = new Map(dots.map((dot) => [dot.key, dot]));
  const live = [];
  edges.forEach((edge, index) => {
    if (edge.source !== edge.target && known.has(edge.source) && known.has(edge.target)) live.push({ edge, index });
  });
  const joined = new Set(live.flatMap(({ edge }) => [edge.source, edge.target]));
  const main = dots.filter((dot) => joined.has(dot.key));
  const loose = dots.filter((dot) => !joined.has(dot.key)).sort(byDot);

  // -- rows: work by its kind, context below the work, below the context it concerns ------------
  const present = [...new Set(main.filter((dot) => dot.isNode).map((dot) => WORK_ROW[dot.type] ?? 2))].sort((a, b) => a - b);
  const workRows = present.length;
  const rank = new Map();
  for (const dot of main) if (dot.isNode) rank.set(dot.key, present.indexOf(WORK_ROW[dot.type] ?? 2));
  const level = new Map(main.filter((dot) => !dot.isNode).map((dot) => [dot.key, 0]));
  for (let pass = 0; pass < MOST_LEVELS; pass += 1) {
    let changed = false;
    for (const { edge } of live) {
      if (edge.type !== 'concerns' && edge.type !== 'placed') continue;
      if (!level.has(edge.source) || !level.has(edge.target)) continue;
      const want = Math.min(MOST_LEVELS, level.get(edge.target) + 1);
      if (level.get(edge.source) < want) {
        level.set(edge.source, want);
        changed = true;
      }
    }
    if (!changed) break;
  }
  for (const [key, at] of level) rank.set(key, workRows + at);
  const rows = main.length ? Math.max(...rank.values()) + 1 : 0;
  const nodes = new Map();
  for (const dot of [...main].sort(byDot)) {
    const { w, h } = size(dot.key);
    nodes.set(dot.key, { key: dot.key, dummy: false, w, h, rank: rank.get(dot.key), x: 0, up: [], down: [] });
  }

  // -- the lines between rows, with a lane in each row a line passes through -----------------------
  const chains = new Map();
  const lanes = [];
  const flats = [];
  const join = (upper, lower, weight) => {
    upper.down.push({ node: lower, weight });
    lower.up.push({ node: upper, weight });
  };
  for (const { edge, index } of live) {
    const a = nodes.get(edge.source);
    const b = nodes.get(edge.target);
    if (a.rank === b.rank) {
      flats.push({ index, a, b });
      continue;
    }
    const [upper, lower] = a.rank < b.rank ? [a, b] : [b, a];
    const long = lower.rank - upper.rank > 1;
    const weight = long ? 4 : edge.type === 'hierarchy' ? 3 : 1;
    const chain = [];
    let previous = upper;
    for (let at = upper.rank + 1; at < lower.rank; at += 1) {
      const lane = { key: `lane:${index}:${at}`, dummy: true, w: LANE * u, h: 0, rank: at, x: 0, up: [], down: [], of: edge.source };
      lanes.push(lane);
      chain.push(lane);
      join(previous, lane, weight);
      previous = lane;
    }
    join(previous, lower, weight);
    chains.set(index, { upper, lower, chain });
  }

  // -- pieces: what is joined, directly or not, is laid out together -------------------------------
  const parent = new Map([...nodes.keys()].map((key) => [key, key]));
  const find = (key) => {
    let at = key;
    while (parent.get(at) !== at) {
      parent.set(at, parent.get(parent.get(at)));
      at = parent.get(at);
    }
    return at;
  };
  for (const { edge } of live) parent.set(find(edge.source), find(edge.target));
  const pieces = new Map();
  const into = (node, key) => {
    if (!pieces.has(key)) pieces.set(key, { layers: Array.from({ length: rows }, () => []), first: null });
    const piece = pieces.get(key);
    piece.layers[node.rank].push(node);
    node.layer = piece.layers[node.rank];
    node.piece = piece;
    if (!node.dummy) {
      const dot = known.get(node.key);
      const rankKey = [node.rank, TYPE_ORDER[dot.type] ?? 9, dot.number];
      if (!piece.first || before(rankKey, piece.first) < 0) piece.first = rankKey;
    }
  };
  for (const node of nodes.values()) into(node, find(node.key));
  for (const lane of lanes) into(lane, find(lane.of));
  const ordered = [...pieces.values()].sort((a, b) => before(a.first, b.first));

  // -- the order in each row of a piece: fewest crossings, by sweeping down and up ----------------
  const crossings = (layers) => {
    let total = 0;
    for (let at = 0; at + 1 < rows; at += 1) {
      const pairs = [];
      layers[at].forEach((node, from) => node.down.forEach((entry) => pairs.push([from, layers[at + 1].indexOf(entry.node)])));
      for (let i = 0; i < pairs.length; i += 1) {
        for (let j = i + 1; j < pairs.length; j += 1) {
          if ((pairs[i][0] - pairs[j][0]) * (pairs[i][1] - pairs[j][1]) < 0) total += 1;
        }
      }
    }
    return total;
  };
  const sweep = (layers, at, towards) => {
    const near = layers[at - towards];
    const value = (node, index) => {
      const others = (towards === 1 ? node.up : node.down).map((entry) => near.indexOf(entry.node));
      return others.length ? others.reduce((sum, place) => sum + place, 0) / others.length : index;
    };
    const keyed = layers[at].map((node, index) => ({ node, index, value: value(node, index) }));
    keyed.sort((a, b) => a.value - b.value || a.index - b.index);
    layers[at] = keyed.map((entry) => entry.node);
  };
  const orderRows = (piece) => {
    const { layers } = piece;
    let best = layers.map((layer) => [...layer]);
    let fewest = crossings(layers);
    for (let round = 0; round < SWEEPS && fewest > 0; round += 1) {
      if (round % 2 === 0) for (let at = 1; at < rows; at += 1) sweep(layers, at, 1);
      else for (let at = rows - 2; at >= 0; at -= 1) sweep(layers, at, -1);
      const now = crossings(layers);
      if (now < fewest) {
        fewest = now;
        best = layers.map((layer) => [...layer]);
      }
    }
    best.forEach((layer, at) => {
      layers[at] = layer;
      for (const node of layer) node.layer = layer;
    });
  };

  // -- across: each dot as near as it can be to what it joins, never closer than its neighbours --
  const pairKey = (a, b) => (a.key < b.key ? `${a.key}|${b.key}` : `${b.key}|${a.key}`);
  const nextTo = new Set(flats.map(({ a, b }) => pairKey(a, b)));
  const apart = (a, b) => {
    if (a.dummy && b.dummy) return (a.w + b.w) / 2 + 4 * u;
    if (a.dummy || b.dummy) return (a.w + b.w) / 2 + 10 * u;
    return (a.w + b.w) / 2 + (nextTo.has(pairKey(a, b)) ? NEXT_GAP : GAP) * u;
  };
  // The row's own best positions: nearest to the weighted mean of what each dot joins, with the
  // order kept and room kept (least squares, solved by merging dots that would overlap).
  const settle = (layer) => {
    const offsets = [];
    layer.forEach((node, index) => offsets.push(index ? offsets[index - 1] + apart(layer[index - 1], node) : 0));
    const blocks = [];
    layer.forEach((node, index) => {
      const others = [...node.up, ...node.down];
      const weight = others.reduce((sum, entry) => sum + entry.weight, 0);
      const value = weight ? others.reduce((sum, entry) => sum + entry.weight * entry.node.x, 0) / weight : node.x;
      const strength = weight || 0.01;
      blocks.push({ sum: (value - offsets[index]) * strength, weight: strength, from: index, to: index });
      while (blocks.length > 1) {
        const [before, last] = blocks.slice(-2);
        if (before.sum / before.weight <= last.sum / last.weight) break;
        blocks.splice(-2, 2, { sum: before.sum + last.sum, weight: before.weight + last.weight, from: before.from, to: last.to });
      }
    });
    for (const block of blocks) {
      for (let index = block.from; index <= block.to; index += 1) layer[index].x = block.sum / block.weight + offsets[index];
    }
  };
  const placeAcross = (piece) => {
    const { layers } = piece;
    for (const layer of layers) {
      let at = 0;
      layer.forEach((node, index) => {
        if (index) at += apart(layer[index - 1], node);
        node.x = at;
      });
    }
    for (let pass = 0; pass < PASSES; pass += 1) {
      for (let at = 0; at < rows; at += 1) settle(layers[at]);
      for (let at = rows - 1; at >= 0; at -= 1) settle(layers[at]);
    }
    const every = layers.flat();
    const left = Math.min(...every.map((node) => node.x - node.w / 2));
    for (const node of every) node.x -= left;
    piece.width = Math.max(...every.map((node) => node.x + node.w / 2));
  };
  let cursor = 0;
  for (const piece of ordered) {
    orderRows(piece);
    placeAcross(piece);
    for (const node of piece.layers.flat()) node.x += cursor;
    cursor += piece.width + PIECE_GAP * u;
  }
  const mainWidth = Math.max(0, cursor - PIECE_GAP * u);

  // -- down: a row as tall as its tallest dot; dots are level at the top ---------------------------
  const tops = [];
  const heights = Array.from({ length: rows }, (_, at) => Math.max(0, ...[...nodes.values()].filter((node) => node.rank === at).map((node) => node.h)));
  let y = 0;
  heights.forEach((high, at) => {
    tops[at] = y;
    y += high + ROW_GAP * u + (workRows && at === workRows - 1 && rows > workRows ? BAND_GAP * u : 0);
  });
  const positions = new Map();
  for (const node of nodes.values()) positions.set(node.key, { x: node.x, y: tops[node.rank] + node.h / 2 });

  // -- where each line leaves and enters a dot: spread along its edge, in the order of what it joins
  const ports = new Map(); // `${edge index}:${key}` -> offset from the dot's centre
  const spread = (node, ends) => {
    ends.sort((a, b) => a.other.x - b.other.x);
    const span = ends.length > 1 ? Math.min(node.w - 28 * u, (ends.length - 1) * 18 * u) : 0;
    ends.forEach((end, at) => ports.set(`${end.index}:${node.key}`, ends.length > 1 ? -span / 2 + (at * span) / (ends.length - 1) : 0));
  };
  const below = new Map();
  const above = new Map();
  const note = (map, node, end) => {
    if (!map.has(node)) map.set(node, []);
    map.get(node).push(end);
  };
  for (const [index, { upper, lower, chain }] of chains) {
    note(below, upper, { index, other: chain[0] || lower });
    note(above, lower, { index, other: chain[chain.length - 1] || upper });
  }
  const order = (node) => node.layer.indexOf(node);
  for (const { index, a, b } of flats) {
    if (Math.abs(order(a) - order(b)) <= 1) continue;
    note(above, a, { index, other: b });
    note(above, b, { index, other: a });
  }
  for (const [node, ends] of below) spread(node, ends);
  for (const [node, ends] of above) spread(node, ends);

  // -- how each line runs ----------------------------------------------------------------------------
  const routes = [];
  for (const [index, { upper, lower, chain }] of chains) {
    routes[index] = {
      kind: 'down',
      reversed: edges[index].source === lower.key,
      upperOffset: ports.get(`${index}:${upper.key}`) ?? 0,
      lowerOffset: ports.get(`${index}:${lower.key}`) ?? 0,
      lanes: chain.map((lane) => ({ x: lane.x, top: tops[lane.rank], bottom: tops[lane.rank] + heights[lane.rank] })),
    };
  }
  for (const { index, a, b } of flats) {
    const gap = Math.abs(order(a) - order(b));
    const [first, second] = order(a) < order(b) ? [a, b] : [b, a];
    routes[index] = {
      kind: 'flat',
      near: gap <= 1,
      reversed: edges[index].source === second.key,
      firstOffset: ports.get(`${index}:${first.key}`) ?? 0,
      secondOffset: ports.get(`${index}:${second.key}`) ?? 0,
      rise: Math.min(64, 22 + 10 * (gap - 1)) * u,
    };
  }

  // -- what is joined to nothing, in a grid below ---------------------------------------------------
  const labels = [];
  if (loose.length) {
    const sizes = new Map(loose.map((dot) => [dot.key, size(dot.key)]));
    const cell = Math.max(...[...sizes.values()].map((entry) => entry.w)) + GAP * u;
    const wanted = Math.max(mainWidth, 3 * cell);
    const columns = Math.max(1, Math.min(8, Math.floor((wanted + GAP * u) / cell)));
    let top = rows ? y - ROW_GAP * u + 56 * u : 0;
    if (rows) labels.push({ x: 0, y: top - 18 * u, text: `Not joined to anything (${loose.length})` });
    for (let from = 0; from < loose.length; from += columns) {
      const line = loose.slice(from, from + columns);
      const high = Math.max(...line.map((dot) => sizes.get(dot.key).h));
      const width = line.length * cell - GAP * u;
      const start = rows ? 0 : Math.max(0, (wanted - width) / 2);
      line.forEach((dot, at) => positions.set(dot.key, { x: start + at * cell + sizes.get(dot.key).w / 2, y: top + sizes.get(dot.key).h / 2 }));
      top += high + GAP * u;
    }
  }

  const widest = Math.max(0, ...ordered.flatMap((piece) => piece.layers.map((layer) => layer.filter((node) => !node.dummy).length)));
  return { positions, routes, labels, widest, rows };
}

// -- drawing a line: smooth curves that leave and arrive straight ------------------------------------

const rounded = (n) => Math.round(n * 10) / 10;
const point = (x, y) => `${rounded(x)} ${rounded(y)}`;

// A curve from `a` to `b` that leaves and arrives vertically (or, with `across`, horizontally).
function curve(a, b, across = false) {
  if (across) {
    const mid = (a.x + b.x) / 2;
    return `C ${point(mid, a.y)} ${point(mid, b.y)} ${point(b.x, b.y)}`;
  }
  const mid = (a.y + b.y) / 2;
  return `C ${point(a.x, mid)} ${point(b.x, mid)} ${point(b.x, b.y)}`;
}

// A path made of these pieces ({ curve, across, from, to }), forwards or backwards, with a point
// about half way along it for a label.
function assemble(pieces, reversed) {
  const ordered = reversed ? [...pieces].reverse().map((piece) => ({ ...piece, from: piece.to, to: piece.from })) : pieces;
  let d = `M ${point(ordered[0].from.x, ordered[0].from.y)}`;
  for (const piece of ordered) d += piece.curve ? ` ${curve(piece.from, piece.to, piece.across)}` : ` L ${point(piece.to.x, piece.to.y)}`;
  const middle = pieces[Math.floor(pieces.length / 2)];
  return { d, mid: { x: (middle.from.x + middle.to.x) / 2, y: (middle.from.y + middle.to.y) / 2 } };
}

// Between two dots that were put where nobody planned (the user dragged one): out of the sides that
// face each other. A box is { x, y, w, h } with x and y its centre.
export function freeRoute(a, b) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const upright = Math.abs(dy) * (a.w + b.w) >= Math.abs(dx) * (a.h + b.h) * 0.6 || Math.abs(dx) < (a.w + b.w) / 2;
  if (upright) {
    const down = dy >= 0 ? 1 : -1;
    return assemble([{ curve: true, from: { x: a.x, y: a.y + (down * a.h) / 2 }, to: { x: b.x, y: b.y - (down * b.h) / 2 } }], false);
  }
  const right = dx >= 0 ? 1 : -1;
  return assemble([{ curve: true, across: true, from: { x: a.x + (right * a.w) / 2, y: a.y }, to: { x: b.x - (right * b.w) / 2, y: b.y } }], false);
}

// A straight line between two boxes, from edge to edge, for the small dots of a large graph.
export function straightRoute(a, b) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const reach = (box) => Math.min(Math.abs(dx) < 0.001 ? Infinity : box.w / 2 / Math.abs(dx), Math.abs(dy) < 0.001 ? Infinity : box.h / 2 / Math.abs(dy));
  const from = { x: a.x + dx * reach(a), y: a.y + dy * reach(a) };
  const to = { x: b.x - dx * reach(b), y: b.y - dy * reach(b) };
  return { d: `M ${point(from.x, from.y)} L ${point(to.x, to.y)}`, mid: { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 } };
}

// The planned route of a line, from where its two dots are now (`a` is its source, `b` its target).
export function plannedRoute(route, a, b) {
  if (route.kind === 'down') {
    const upper = route.reversed ? b : a;
    const lower = route.reversed ? a : b;
    const pieces = [];
    let from = { x: upper.x + route.upperOffset, y: upper.y + upper.h / 2 };
    for (const lane of route.lanes) {
      pieces.push({ curve: true, from, to: { x: lane.x, y: lane.top } });
      pieces.push({ from: { x: lane.x, y: lane.top }, to: { x: lane.x, y: lane.bottom } });
      from = { x: lane.x, y: lane.bottom };
    }
    pieces.push({ curve: true, from, to: { x: lower.x + route.lowerOffset, y: lower.y - lower.h / 2 } });
    return assemble(pieces, route.reversed);
  }
  const first = route.reversed ? b : a;
  const second = route.reversed ? a : b;
  if (route.near) {
    const top = Math.max(first.y - first.h / 2, second.y - second.h / 2) + 8;
    const bottom = Math.min(first.y + first.h / 2, second.y + second.h / 2) - 8;
    const y = bottom >= top ? Math.min(bottom, Math.max(top, (first.y + second.y) / 2)) : (first.y + second.y) / 2;
    return assemble([{ from: { x: first.x + first.w / 2, y }, to: { x: second.x - second.w / 2, y } }], route.reversed);
  }
  const start = { x: first.x + route.firstOffset, y: first.y - first.h / 2 };
  const end = { x: second.x + route.secondOffset, y: second.y - second.h / 2 };
  const peak = Math.min(start.y, end.y) - route.rise / 0.75;
  const forward = `M ${point(start.x, start.y)} C ${point(start.x, peak)} ${point(end.x, peak)} ${point(end.x, end.y)}`;
  const backward = `M ${point(end.x, end.y)} C ${point(end.x, peak)} ${point(start.x, peak)} ${point(start.x, start.y)}`;
  return { d: route.reversed ? backward : forward, mid: { x: (start.x + end.x) / 2, y: Math.min(start.y, end.y) - route.rise } };
}
