// The chain pictures of Order: the "needs first" links between epics, stories and tasks, drawn left to
// right in layers, what can start now on the left and what has to wait for it to the right of it. One
// chain is drawn at a time (graph-order.js steps through them). Blocked and overdue work is marked,
// `blocked=1` lights up what is blocked and what waits on it, and the chain through an item is
// highlighted while the pointer or the keyboard is on its box, and for the item the address picks.
// A box opens the item's page.

import { h, plural } from './dom.js';
import { icon } from './icons.js';
import { KIND, STATUS, dueChip, statusIcon } from './labels.js';
import { code, codeOf, s } from './graph-shared.js';

// The box of one piece of work, and the room between boxes.
const NW = 190;
const NH = 78;
const XG = 58;
const YG = 14;
const DUMMY = 10;

// Epics, then stories, then tasks, each in the order it was made.
const byCreation = (model) => (a, b) => {
  const x = model.node(a);
  const y = model.node(b);
  return ['epic', 'story', 'task'].indexOf(x.kind) - ['epic', 'story', 'task'].indexOf(y.kind) || x.number - y.number;
};

// Every "needs first" link between work, as { dep, pre }: `dep` cannot start before `pre` is done.
function needsEdges(model, links) {
  const edges = [];
  for (const link of links) {
    if (link.link_type !== 'needs_first' || !model.node(link.source) || !model.node(link.target)) continue;
    edges.push({ dep: link.source, pre: link.target });
  }
  return edges;
}

// The pieces of work joined by "needs first" links, each as { refs, edges }.
export function chainsOf(model, links) {
  const edges = needsEdges(model, links);
  const parent = new Map();
  const find = (x) => {
    let y = x;
    while (parent.get(y) !== y) {
      parent.set(y, parent.get(parent.get(y)));
      y = parent.get(y);
    }
    return y;
  };
  for (const { dep, pre } of edges) {
    for (const x of [dep, pre]) if (!parent.has(x)) parent.set(x, x);
    parent.set(find(dep), find(pre));
  }
  const groups = new Map();
  for (const x of parent.keys()) {
    const r = find(x);
    if (!groups.has(r)) groups.set(r, { refs: [], edges: [] });
    groups.get(r).refs.push(x);
  }
  for (const edge of edges) groups.get(find(edge.dep)).edges.push(edge);
  const order = byCreation(model);
  return [...groups.values()].map((chain) => ({ ...chain, refs: chain.refs.sort(order) }));
}

const walk = (start, edges, from, to) => {
  const seen = new Set();
  const go = (ref) => {
    for (const edge of edges) {
      if (edge[from] === ref && !seen.has(edge[to])) {
        seen.add(edge[to]);
        go(edge[to]);
      }
    }
  };
  go(start);
  return seen;
};
const downstream = (ref, edges) => walk(ref, edges, 'pre', 'dep');
const upstream = (ref, edges) => walk(ref, edges, 'dep', 'pre');

// The values made to rise, as little changed as can be (pool adjacent violators).
function nondecreasing(values) {
  const blocks = [];
  for (const v of values) {
    blocks.push({ sum: v, n: 1 });
    while (blocks.length > 1 && blocks[blocks.length - 2].sum / blocks[blocks.length - 2].n > blocks[blocks.length - 1].sum / blocks[blocks.length - 1].n) {
      const last = blocks.pop();
      const before = blocks.pop();
      blocks.push({ sum: before.sum + last.sum, n: before.n + last.n });
    }
  }
  return blocks.flatMap((b) => Array(b.n).fill(b.sum / b.n));
}

// Where each piece of one chain goes: a layer for each step (what comes first is on the left), an order
// within the layer that crosses the fewest lines, and a line that is long enough to pass a layer runs
// through a little slot kept for it there, so it never goes behind a box. Returns the boxes with their
// place, the routes of the lines, and the size of the picture.
function layoutChain(refs, edges, order) {
  const pres = new Map(refs.map((r) => [r, []]));
  for (const e of edges) pres.get(e.dep).push(e.pre);
  const layer = new Map();
  const visiting = new Set();
  const lay = (r) => {
    if (layer.has(r)) return layer.get(r);
    if (visiting.has(r)) return 0;
    visiting.add(r);
    const v = pres.get(r).length ? 1 + Math.max(...pres.get(r).map(lay)) : 0;
    visiting.delete(r);
    layer.set(r, v);
    return v;
  };
  refs.forEach(lay);
  const last = Math.max(0, ...layer.values());
  const cols = Array.from({ length: last + 1 }, () => []);
  const items = new Map();
  for (const r of refs.slice().sort(order)) {
    const item = { id: r, ref: r, col: layer.get(r), h: NH };
    items.set(r, item);
    cols[item.col].push(item);
  }
  const up = new Map();
  const down = new Map();
  const join = (a, b) => {
    if (!up.has(b)) up.set(b, []);
    if (!down.has(a)) down.set(a, []);
    up.get(b).push(a);
    down.get(a).push(b);
  };
  const routes = edges.map((e, i) => {
    const from = layer.get(e.pre);
    const to = layer.get(e.dep);
    const path = [e.pre];
    let before = e.pre;
    for (let l = from + 1; l < to; l += 1) {
      const slot = { id: `~${i}:${l}`, ref: null, col: l, h: DUMMY };
      items.set(slot.id, slot);
      cols[l].push(slot);
      join(before, slot.id);
      path.push(slot.id);
      before = slot.id;
    }
    if (to > from) join(before, e.dep);
    path.push(e.dep);
    return { pre: e.pre, dep: e.dep, path };
  });

  const at = new Map();
  const reindex = () => cols.forEach((col) => col.forEach((item, i) => at.set(item.id, i)));
  reindex();
  const mean = (list) => list.reduce((a, b) => a + b, 0) / list.length;
  const sweep = (col, near) => {
    const key = new Map(col.map((item) => [item.id, (near.get(item.id) || []).length ? mean(near.get(item.id).map((n) => at.get(n))) : at.get(item.id)]));
    col.sort((a, b) => key.get(a.id) - key.get(b.id) || at.get(a.id) - at.get(b.id));
    reindex();
  };
  for (let pass = 0; pass < 4; pass += 1) {
    for (let l = 1; l <= last; l += 1) sweep(cols[l], up);
    for (let l = last - 1; l >= 0; l -= 1) sweep(cols[l], down);
  }

  // Heights: each layer stacked, then pulled towards the middle of what it joins, order kept.
  const y = new Map();
  for (const col of cols) {
    let top = 0;
    for (const item of col) {
      y.set(item.id, top);
      top += item.h + YG;
    }
  }
  const middle = (id) => y.get(id) + items.get(id).h / 2;
  const settle = (col, near) => {
    const wanted = col.map((item) => ((near.get(item.id) || []).length ? mean(near.get(item.id).map(middle)) : middle(item.id)));
    let off = 0;
    const q = col.map((item, i) => {
      const v = wanted[i] - item.h / 2 - off;
      off += item.h + YG;
      return v;
    });
    const fit = nondecreasing(q);
    off = 0;
    col.forEach((item, i) => {
      y.set(item.id, fit[i] + off);
      off += item.h + YG;
    });
  };
  for (let pass = 0; pass < 6; pass += 1) {
    for (let l = 1; l <= last; l += 1) settle(cols[l], up);
    for (let l = last - 1; l >= 0; l -= 1) settle(cols[l], down);
  }
  const top = Math.min(...[...y.values()]);
  const place = new Map();
  let height = 0;
  for (const item of items.values()) {
    const py = y.get(item.id) - top;
    place.set(item.id, { x: item.col * (NW + XG), y: py, h: item.h, dummy: item.ref === null, col: item.col, row: at.get(item.id) });
    height = Math.max(height, py + item.h);
  }
  return { cols, place, routes, layer, width: (last + 1) * NW + last * XG, height, up, down };
}

// A short stretch of the line a chain is drawn with, for the key.
export function orderSample(extra) {
  const svg = s('svg', { class: 'graph-x-sample', width: 40, height: 12, viewBox: '0 0 40 12', 'aria-hidden': 'true' });
  svg.append(s('line', { class: `graph-or-edge ${extra}`.trim(), x1: 2, y1: 6, x2: 31, y2: 6 }), s('path', { class: `graph-or-arrowhead ${extra}`.trim(), d: 'M30 2.5 L38 6 L30 9.5 z' }));
  return svg;
}

// One chain, drawn: its boxes and lines, scrolling sideways when it is wider than the page. A box opens
// the item's page. While the pointer is on a box, or the keyboard is in it, the chain through it is lit
// and the rest dimmed, with no redrawing (`light`); with the pointer and the keyboard away, what the
// address asks for is lit instead.
//   scope   the refs of everything in the chosen epic; a box outside it is dashed
//   pick    the item whose chain is lit, if it is in this chain
//   blocked light up what is blocked and what waits on it
//   open    opens the page of an item
// Returns { element, edges, path, outside, stop } (`stop` undoes what the drawing watches).
export function chainPicture(model, chain, { scope, pick, blocked, open }) {
  const L = layoutChain(chain.refs, chain.edges, byCreation(model));
  const edges = chain.edges;
  const wide = L.width;
  const total = Math.max(L.height, 80);
  const blockedNow = chain.refs.filter((r) => model.node(r).status === 'blocked');
  const hot = new Set(blockedNow);
  for (const r of blockedNow) for (const d of downstream(r, edges)) hot.add(d);
  const picked = pick && chain.refs.includes(pick) ? pick : null;
  const through = (ref) => new Set([ref, ...upstream(ref, edges), ...downstream(ref, edges)]);
  const path = picked ? through(picked) : null;
  const outside = chain.refs.filter((r) => !scope.has(r));

  const svg = s('svg', { class: 'graph-or-lines', width: wide, height: total, viewBox: `0 0 ${wide} ${total}`, 'aria-hidden': 'true', focusable: 'false' },
    s('defs', {}, s('marker', { id: 'graph-or-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 11, markerHeight: 11, orient: 'auto', markerUnits: 'userSpaceOnUse' }, s('path', { class: 'graph-or-arrowhead', d: 'M0 1 L10 5 L0 9 z' }))));
  for (const route of L.routes) {
    const points = route.path.map((id, k) => {
      const p = L.place.get(id);
      const lastStep = k === route.path.length - 1;
      const first = k === 0;
      const x = p.dummy ? p.x + NW / 2 : first ? p.x + NW : lastStep ? p.x - 2 : p.x;
      return [x, p.y + p.h / 2];
    });
    let d = `M${points[0][0]} ${points[0][1]}`;
    for (let k = 1; k < points.length; k += 1) {
      const [x1, y1] = points[k - 1];
      const [x2, y2] = points[k];
      const dx = Math.max(24, (x2 - x1) * 0.5);
      d += ` C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`;
    }
    const pre = model.node(route.pre);
    const both = blocked ? hot.has(route.pre) && hot.has(route.dep) : false;
    svg.append(s('path', {
      class: `graph-or-edge${pre.status === 'done' ? ' is-ended' : ''}${pre.status === 'blocked' ? ' is-blocked' : ''}${blocked ? (both ? ' is-hot' : ' is-dim') : ''}${path && path.has(route.pre) && path.has(route.dep) ? ' is-on-path' : ''}`,
      d,
      'data-pre': route.pre,
      'data-dep': route.dep,
      'marker-end': 'url(#graph-or-arrow)',
    }));
  }
  const canvas = h('div', { class: 'graph-or-canvas', css: { width: `${wide}px`, height: `${total}px` } }, svg);

  // The chain through `ref` is lit and the rest dimmed, without drawing anything again; with none, the
  // picture is as it was drawn (what the address picks lit, what `blocked` marks dimmed).
  const light = (ref) => {
    const lit = ref ? through(ref) : path;
    for (const box of canvas.querySelectorAll('.graph-or-node')) {
      const here = box.dataset.ref;
      box.classList.toggle('is-on-path', Boolean(lit && lit.has(here)));
      box.classList.toggle('is-dim', ref ? !lit.has(here) : blocked && !hot.has(here));
    }
    for (const line of svg.querySelectorAll('.graph-or-edge')) {
      const { pre, dep } = line.dataset;
      const on = Boolean(lit && lit.has(pre) && lit.has(dep));
      line.classList.toggle('is-on-path', on);
      line.classList.toggle('is-dim', ref ? !on : blocked && !(hot.has(pre) && hot.has(dep)));
    }
  };
  // The pointer wins over the keyboard while it is on a box, and the keyboard's box is lit again when it leaves.
  let pointed = null;
  let focused = null;
  const hooks = {
    point: (ref) => { pointed = ref; light(pointed || focused); },
    focus: (ref) => { focused = ref; light(pointed || focused); },
  };
  for (const [id, p] of L.place) {
    if (p.dummy) continue;
    canvas.append(nodeBox(model, canvas, { ref: id, x: p.x, y: p.y, col: p.col }, { pick: picked, hot, path, blocked, outside: !scope.has(id), grid: L, open, hooks }));
  }
  const region = h('div', { class: 'graph-or-scroll', tabindex: '0', role: 'region', 'aria-label': 'The order of work, left to right. It scrolls sideways when it is wide.' }, canvas);
  const hint = h('p', { class: 'graph-x-hint quiet', hidden: true }, icon('arrow_right', { size: 14 }), 'This chain is wider than the page: scroll sideways to see the rest.');
  const showHint = () => { hint.hidden = !(region.scrollWidth > region.clientWidth + 4); };
  const observer = new ResizeObserver(showHint);
  observer.observe(region);
  requestAnimationFrame(showHint);
  return { element: h('div', { class: 'graph-or-picture' }, hint, region), edges, path, outside, stop: () => observer.disconnect() };
}

// One box of the chain: its status sign, code and title, how late it is, and which epic it is in (its code
// and title) when that is outside what was chosen.
function nodeBox(model, canvas, box, { pick, hot, path, blocked, outside, grid, open, hooks }) {
  const node = model.node(box.ref);
  const late = model.late(box.ref);
  const dim = blocked && !hot.has(box.ref);
  const waits = model.waitingOn(box.ref);
  const epic = model.epicOf(box.ref);
  const go = (direction) => {
    const list = (direction === 'left' ? grid.up : grid.down).get(box.ref) || [];
    return list.find((id) => !String(id).startsWith('~'));
  };
  return h(
    'button',
    {
      class: `graph-or-node s-${node.status}${outside ? ' is-outside' : ''}${pick === box.ref ? ' is-sel' : ''}${dim ? ' is-dim' : ''}${blocked && hot.has(box.ref) ? ' is-hot' : ''}${path && path.has(box.ref) ? ' is-on-path' : ''}`,
      type: 'button',
      'data-ref': box.ref,
      'data-fk': `b:${box.ref}`,
      css: { left: `${box.x}px`, top: `${box.y}px` },
      title: node.title,
      'aria-current': pick === box.ref ? 'true' : null,
      'aria-label': `${KIND[node.kind].label} ${codeOf(node)}: ${node.title}. ${STATUS[node.status].label}${late > 0 ? `, overdue ${plural(late, 'day')}` : ''}${waits.length ? `, waiting for ${waits.map((r) => `${codeOf(model.node(r))} ${model.node(r).title}`).join(', ')}` : ''}${outside ? `, outside the chosen epic${epic ? `, in ${codeOf(epic)} ${epic.title}` : ''}` : ''}. Press Enter to open it.`,
      onclick: () => open(box.ref),
      onpointerenter: () => hooks.point(box.ref),
      onpointerleave: () => hooks.point(null),
      onfocus: () => hooks.focus(box.ref),
      onblur: () => hooks.focus(null),
      onkeydown: (event) => {
        const next = { ArrowLeft: go('left'), ArrowRight: go('right') }[event.key];
        const around = grid.cols[box.col].filter((item) => item.ref !== null);
        const here = around.findIndex((item) => item.ref === box.ref);
        const sideways = { ArrowUp: around[here - 1]?.ref, ArrowDown: around[here + 1]?.ref }[event.key];
        const target = next || sideways;
        if (!target) return;
        const other = canvas.querySelector(`.graph-or-node[data-ref="${target}"]`);
        if (other) {
          event.preventDefault();
          other.focus();
        }
      },
    },
    h('span', { class: 'graph-or-node-top' }, statusIcon(node.status, 15), code(codeOf(node)), outside && epic ? h('span', { class: 'graph-or-epic', title: `${codeOf(epic)} ${epic.title}` }, `${codeOf(epic)} ${epic.title}`) : null, late > 0 ? dueChip(node, { size: 12, days: true }) : null),
    h('span', { class: 'graph-or-node-title' }, node.title),
  );
}
