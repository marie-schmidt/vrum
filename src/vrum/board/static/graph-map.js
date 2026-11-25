// The Structure map of the whole project, alive, drawn after Obsidian's graph view: flat discs and thin
// lines that move a little as you handle them. The physics is graph-sim.js (a pure module that takes
// the tree and gives coordinates); this file draws it and takes the pointer.
//
// How it behaves:
//  - The tidy layout (graph-radial.js) is only the seed. The simulation is run to rest before the first
//    picture (more than about 400 dots are painted from the seed and settle on screen), so the picture
//    is the same on every visit and never jumps.
//  - It rests, and then nothing runs at all: no timer and no frame loop until a pointer, wheel, key or
//    slider touches the map. `data-resting` on the svg says which it is.
//  - A dot pressed and moved more than 5 px is grabbed: it follows the pointer, its neighbours follow it
//    and, let go, it eases back. A press that does not move is a click and opens the item. Nothing is saved.
//  - The background pans with inertia; the wheel zooms, eased, to the cursor when zooming in and about the
//    middle when zooming out; Fit, Tidy and the buttons ease too.
//  - Pointing at a dot (or giving it the keyboard's focus) keeps it, what it hangs from, what hangs from it
//    and what it is linked to at full strength and fades the rest.
//  - With reduced motion the map is worked out once and shown at rest: no loop, no easing, no inertia and
//    nothing to drag; a click, Enter, Tidy and Fit still work.
//
// A refresh of the data changes the picture in place: the colours and marks are restyled, a change of the
// tree's shape adds and removes dots without moving the others.

import { h, plural, put } from './dom.js';
import { KIND, PRIORITY, STATUS, actorLabel, statusIcon } from './labels.js';
import { LEVEL_MAX, RANK, shapeOf, wholeLayout } from './graph-radial.js';
import { createSim } from './graph-sim.js';
import { clip, code, codeOf, linksOf, s, wrap } from './graph-shared.js';
import { store } from './store.js';

const MOST_ZOOM = 8;
const LEAST_ZOOM = 0.6;
const HUES = 6;
const STEP = 1000 / 60; // one step of the simulation, in milliseconds
const NAMES_EVERY = 100; // while the map moves, its names are placed again this often
const DRAG_FROM = 5; // a press that moves less than this many pixels is a click
const SETTLE_ON_SCREEN = 400; // more dots than this are painted from the seed and settle where they are seen
const R1 = (n) => Math.round(n * 10) / 10;
const clampZoom = (k) => Math.min(MOST_ZOOM, Math.max(LEAST_ZOOM, k));

export const hueOf = (index) => `ep-${index % HUES}`;

// -- what a dot says about itself ------------------------------------------------------------------

export function words(model, ref) {
  const node = model.node(ref);
  const late = model.late(ref);
  const p = model.progress(ref);
  const bits = [`${KIND[node.kind].label} ${codeOf(node)}`, node.title, STATUS[node.status].label];
  if (late > 0) bits.push(`overdue ${plural(late, 'day')}`);
  if (node.kind !== 'task') bits.push(`${p.done} of ${plural(p.total, 'task')} done`);
  return `${bits[0]}: ${bits[1]}. ${bits.slice(2).join(', ')}.`;
}

// What a pointed-at node says about itself.
export function tipContent(model, ref) {
  const node = model.node(ref);
  const late = model.late(ref);
  const p = model.progress(ref);
  let line;
  if (node.kind === 'epic') {
    const stories = model.kids(ref).length;
    line = `${plural(stories, 'story', 'stories')} · ${plural(p.total, 'task')} · ${p.total ? Math.round((100 * p.done) / p.total) : 0}% done`;
  } else if (node.kind === 'story') {
    line = `${plural(p.total, 'task')}, ${p.done} done`;
  } else {
    line = `${PRIORITY[node.priority].label} priority${node.target_on ? (late > 0 ? ` · overdue ${plural(late, 'day')}` : ` · due ${node.target_on}`) : ''}`;
  }
  return [
    h('div', { class: 'graph-x-tip-top' }, statusIcon(node.status, 14), code(codeOf(node)), h('span', {}, `${KIND[node.kind].label} · ${STATUS[node.status].label}`)),
    h('div', { class: 'graph-x-tip-title' }, node.title),
    h('div', { class: 'quiet' }, line),
    node.claimed_by ? h('div', { class: 'quiet' }, 'Held by ', actorLabel(node.claimed_by, node.claimed_by_kind)) : null,
  ];
}

// The links of the item looked at, each with the other end lifted to something the picture shows.
function partnersOf(ref, lift) {
  const seen = new Set();
  return linksOf(ref)
    .map((row) => ({ ...row, other: lift(row.other) }))
    .filter((row) => {
      const key = `${row.type}:${row.dir}:${row.other}`;
      if (row.other === ref || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

// -- what the picture is drawn from ----------------------------------------------------------------

// The simulation's input for a tidy layout: the project pinned in the middle and every dot where the
// tidy layout puts it.
function specOf(layout, tree) {
  const epicRefs = tree.map((epic) => epic.ref);
  return [
    { id: 'root', kind: 'project', r: layout.rProj, epic: 'root', parent: null, x: 0, y: 0 },
    ...layout.nodes.map((o) => ({ id: o.ref, kind: o.kind, r: o.r, epic: epicRefs[o.epic], parent: o.parent, x: o.x, y: o.y })),
  ];
}

const shapeKey = (tree, level) => `${level}|${shapeOf(tree)}`;

// What is lit and what is looked at, from the page's choices: the item looked at, what it sits in, what
// sits in it and what it is linked to. An acceptance criterion or a note in focus lights the work it concerns.
function selectionOf(model, level, chosen, concernedRefs) {
  const lift = (ref) => {
    let node = model.node(ref);
    while (node && RANK[node.kind] > LEVEL_MAX[level]) node = model.node(node.parent);
    return node ? node.ref : ref;
  };
  const visibleKids = (ref) => model.kids(ref).filter((kid) => RANK[kid.kind] <= LEVEL_MAX[level]);
  const sel = chosen ? lift(chosen) : null;
  const partners = sel ? partnersOf(sel, lift) : [];
  const concerned = [...new Set(concernedRefs.map(lift))];
  const partnerSet = new Set(sel ? partners.map((row) => row.other) : concerned);
  const pathSet = new Set();
  if (sel) {
    for (let up = model.node(sel); up; up = up.parent ? model.node(up.parent) : null) pathSet.add(up.ref);
    const down = (ref) => visibleKids(ref).forEach((kid) => { pathSet.add(kid.ref); down(kid.ref); });
    down(sel);
  }
  for (const ref of concerned) for (let up = model.node(ref); up; up = up.parent ? model.node(up.parent) : null) pathSet.add(up.ref);
  return { lift, visibleKids, sel, partners, concerned, partnerSet, pathSet, lit: new Set([...pathSet, ...partnerSet]) };
}

// The arrow points the way the work flows: from what comes first to what waits.
function direct(type, dir, ref, other) {
  if ((type === 'needs_first' || type === 'supersedes') && dir === 'out') return type === 'needs_first' ? [other, ref] : [ref, other];
  if ((type === 'needs_first' || type === 'supersedes') && dir === 'in') return type === 'needs_first' ? [ref, other] : [other, ref];
  return dir === 'in' && type === 'mention' ? [other, ref] : [ref, other];
}

// The zoom and place of the picture, kept while the page is open (it is not in the address).
const view = { k: 1, x: 0, y: 0, level: null };

// -- the map ---------------------------------------------------------------------------------------

// `first` is { api, model, tree, cfg, chosen, concerned, name, forces }; the result is
// { el, update(next), tidy(), setForces(values), fit(), destroy() }.
export function createMap(first) {
  let st = first;
  const { level } = first.cfg;
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let reduced = motion.matches;
  const rank = LEVEL_MAX[level];

  let layout = wholeLayout(first.tree, level);
  let key = shapeKey(first.tree, level);
  let sim = null;
  let items = []; // one for each dot: { ref, kind, r, parent, epic, n (the simulation's node), g, ... }
  let byRef = new Map();
  let lines = []; // the tree's own lines: { el, a, b }
  let crosses = []; // the links between items that are drawn: { el, type, from, to }
  let hubNames = []; // the epic codes inside the hubs and the project's name, which follow their dot
  let selection = null;
  let hover = null;
  let hoverEl = null;
  let near = null;
  let roving = null;
  let meas = { sc: 1, e: 0, f: 0, w: 0, h: 0 };
  let vb = { x: -100, y: -100, w: 200, h: 200 };
  let shownView = '';
  let projectName = null;
  let lastSel = null;
  let touched = false; // the person has moved the view
  let autoFit = false; // a map that is painted from its seed is fitted once it has settled

  // Frame loop: it runs only while something moves.
  let raf = 0;
  let last = 0;
  let acc = 0;
  let namesAt = 0;
  let namesDirty = true;
  let moving = false;
  const goal = { k: 1, x: 0, y: 0 }; // where the zoom and place are easing to
  let inertia = null; // { vx, vy } in view units a millisecond
  let candidate = null; // a press on a dot: { ref, id, pointer, sx, sy, grabbed, ... }
  let pan = null;
  let justDragged = false;
  const pointers = new Set();

  const svg = s('svg', { class: 'graph-st-svg', role: 'group', 'data-resting': 'false' });
  const vp = s('g', { class: 'graph-st-vp' });
  const hier = s('g', { class: 'graph-st-hier' });
  const cross = s('g', { class: 'graph-st-cross' });
  const dots = s('g', { class: 'graph-st-nodes' });
  const names = s('g', { class: 'graph-st-names', 'aria-hidden': 'true' });
  const labelsG = s('g', { class: 'graph-st-labels' });
  const hubsG = s('g', { class: 'graph-st-hubs' });
  names.append(labelsG, hubsG);
  vp.append(hier, cross, dots, names);
  svg.append(s('defs', {}, s('marker', { id: 'graph-st-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 8, markerHeight: 8, orient: 'auto', markerUnits: 'userSpaceOnUse' }, s('path', { class: 'graph-st-arrow', d: 'M0 1 L10 5 L0 9 z' }))), vp);
  const tip = h('div', { class: 'graph-x-tip', hidden: true, role: 'presentation' });
  const inner = h('div', { class: 'graph-st-inner' }, svg, tip);
  const zoomButtons = h('div', { class: 'graph-st-zoom', role: 'group', 'aria-label': 'Zoom and move' });
  const el = h('div', { class: 'graph-x-canvas graph-st-canvas' }, inner, zoomButtons);
  const point = svg.createSVGPoint();

  // -- the simulation -------------------------------------------------------------------------------

  const restNow = () => {
    for (let guard = 0; !sim.resting && guard < 2000; guard += 1) sim.tick();
  };

  function start() {
    sim = createSim({ nodes: specOf(layout, st.tree) });
    if (st.forces) sim.setForces(st.forces);
    if (sim.nodes.length <= SETTLE_ON_SCREEN + 1 || reduced) sim.settle(160);
    if (reduced) restNow();
  }

  // The tree changed shape: dots come and go, and the others stay where they are.
  function reshape() {
    layout = wholeLayout(st.tree, level);
    key = shapeKey(st.tree, level);
    const spec = specOf(layout, st.tree);
    const keep = new Set(spec.map((n) => n.id));
    sim.remove(sim.nodes.filter((n) => !keep.has(n.id)).map((n) => n.id));
    sim.addOrUpdate(spec);
    if (reduced) restNow();
    build();
  }

  // -- drawing the dots -----------------------------------------------------------------------------

  // How far the dots reach, where they are or (given a map from ref to place) where they are going.
  const extents = (places = null) => {
    let x0 = Infinity;
    let y0 = Infinity;
    let x1 = -Infinity;
    let y1 = -Infinity;
    for (const n of sim.nodes) {
      const at = places?.get(n.id) || n;
      x0 = Math.min(x0, at.x - n.r - 12);
      x1 = Math.max(x1, at.x + n.r + 12);
      y0 = Math.min(y0, at.y - n.r - 12);
      y1 = Math.max(y1, at.y + n.r + 12);
    }
    return { x0, y0, x1, y1 };
  };

  // Where the view has to be for every dot to be in the picture.
  function fitView(places = null) {
    const b = extents(places);
    const k = clampZoom(Math.min(vb.w / (b.x1 - b.x0 + 60), vb.h / (b.y1 - b.y0 + 60)));
    return { k, x: vb.x + vb.w / 2 - ((b.x0 + b.x1) / 2) * k, y: vb.y + vb.h / 2 - ((b.y0 + b.y1) / 2) * k };
  }

  function build() {
    put(hier);
    put(dots);
    put(hubsG);
    lines = [];
    items = [];
    byRef = new Map();
    hubNames = [];
    const root = { ref: 'root', kind: 'project', r: layout.rProj, n: sim.byId.get('root') };
    dots.append(s('g', { class: 'graph-st-project' }, s('circle', { class: 'graph-st-project-body', r: layout.rProj })));
    for (const o of layout.nodes) {
      const it = { ref: o.ref, kind: o.kind, r: o.r, parent: o.parent, epic: o.epic, n: sim.byId.get(o.ref), hub: o.kind !== 'task', px: NaN, py: NaN };
      items.push(it);
      byRef.set(o.ref, it);
    }
    byRef.set('root', root);
    for (const [from, to] of layout.edges) {
      const a = byRef.get(from);
      const b = byRef.get(to);
      if (!a || !b) continue;
      const line = s('line', { class: 'graph-st-line' });
      hier.append(line);
      lines.push({ el: line, a, b, on: false });
    }
    // Tasks first, so that hubs sit on top.
    for (const it of items.slice().sort((x, y) => RANK[y.kind] - RANK[x.kind])) {
      const g = s('g', { class: 'graph-st-node', role: 'button', 'data-ref': it.ref, tabindex: '-1' }, s('circle', { class: 'graph-st-hit', r: Math.max(it.r + 3, 7) }));
      if (it.hub && (it.kind === 'epic' || level !== 'tasks')) {
        it.track = s('circle', { class: 'graph-st-track', r: it.r + 3.6 });
        it.fill = s('circle', { class: 'graph-st-fill', r: it.r + 3.6, transform: 'rotate(-90)' });
        g.append(it.track, it.fill);
      }
      g.append(s('circle', { class: 'graph-st-body', r: it.r }), s('circle', { class: 'graph-st-flag', r: it.r + (it.hub ? (it.kind === 'epic' ? 9 : 7) : 2.3) }), s('circle', { class: 'graph-st-ring', r: it.r + (it.hub ? (it.kind === 'epic' ? 11 : 9) : 6) }));
      it.g = g;
      wire(it);
      dots.append(g);
    }
    for (const it of items) {
      if (it.kind !== 'epic') continue;
      const text = s('text', { x: 0, 'text-anchor': 'middle' }, codeOf(st.model.node(it.ref)));
      const g = s('g', { class: 'graph-st-name is-hub' }, text);
      hubsG.append(g);
      hubNames.push({ it, g, text });
    }
    const projectText = s('g', { class: 'graph-st-name is-project' });
    hubsG.append(projectText);
    projectName = projectText;
    roving = null;
    hover = null;
    hoverEl = null;
    near = null;
    tip.hidden = true;
    paint();
    sizeHubs();
  }

  const fmt = (n) => (Math.round(n * 10) / 10).toString();

  // Where every dot, line and name that follows a dot is now.
  function paint() {
    for (const it of items) {
      const { x, y } = it.n;
      if (Math.abs(x - it.px) < 0.04 && Math.abs(y - it.py) < 0.04) continue;
      it.px = x;
      it.py = y;
      it.g.setAttribute('transform', `translate(${fmt(x)} ${fmt(y)})`);
    }
    for (const line of lines) {
      const a = line.a.n;
      const b = line.b.n;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const len = Math.hypot(dx, dy) || 1;
      const el = line.el;
      el.setAttribute('x1', fmt(a.x + (dx / len) * line.a.r));
      el.setAttribute('y1', fmt(a.y + (dy / len) * line.a.r));
      el.setAttribute('x2', fmt(b.x - (dx / len) * line.b.r));
      el.setAttribute('y2', fmt(b.y - (dy / len) * line.b.r));
    }
    for (const link of crosses) placeLink(link);
    placeHubs();
  }

  // The epic codes sit inside their hubs and the project's name in the middle, at a fixed size on the screen.
  function placeHubs() {
    const inv = 1 / (meas.sc * view.k);
    for (const { it, g } of hubNames) g.setAttribute('transform', `translate(${fmt(it.n.x)} ${fmt(it.n.y)}) scale(${R1(inv * 1000) / 1000})`);
    projectName.setAttribute('transform', `scale(${R1(inv * 1000) / 1000})`);
  }

  function sizeHubs() {
    const px = meas.sc * view.k;
    for (const { it, text } of hubNames) {
      const fs = Math.max(7, Math.min(12, (it.r * px * 2) / 3.8));
      text.style.fontSize = `${R1(fs)}px`;
      text.setAttribute('y', R1(fs * 0.36));
    }
    const lines2 = wrap(st.name || '', 11, 2);
    const pf = Math.min(12, (layout.rProj * px * 2 * 0.86) / (Math.max(...lines2.map((line) => line.length), 1) * 0.62));
    put(
      projectName,
      st.name && pf >= 7
        ? lines2.map((line, i) => {
            const text = s('text', { x: 0, y: R1((i - (lines2.length - 1) / 2) * pf * 1.2 + pf * 0.36), 'text-anchor': 'middle' }, line);
            text.style.fontSize = `${R1(pf)}px`;
            return text;
          })
        : null,
    );
  }

  // -- the links between items ----------------------------------------------------------------------

  function placeLink(link) {
    const p = byRef.get(link.from);
    const q = byRef.get(link.to);
    if (!p || !q) return;
    const [px, py, qx, qy] = [p.n.x, p.n.y, q.n.x, q.n.y];
    const mx = (px + qx) / 2;
    const my = (py + qy) / 2;
    const nx = -(qy - py);
    const ny = qx - px;
    const c1 = [mx + nx * 0.2, my + ny * 0.2];
    const c2 = [mx - nx * 0.2, my - ny * 0.2];
    const [cx, cy] = Math.hypot(...c1) < Math.hypot(...c2) ? c1 : c2;
    const trim = (o, ox, oy, extraR) => {
      const vx = cx - ox;
      const vy = cy - oy;
      const len = Math.hypot(vx, vy) || 1;
      return [ox + (vx / len) * (o.r + extraR), oy + (vy / len) * (o.r + extraR)];
    };
    const arrow = link.type === 'needs_first' || link.type === 'supersedes';
    const [x1, y1] = trim(p, px, py, 1.5);
    const [x2, y2] = trim(q, qx, qy, arrow ? 2.5 : 1.5);
    link.el.setAttribute('d', `M${fmt(x1)} ${fmt(y1)} Q${fmt(cx)} ${fmt(cy)} ${fmt(x2)} ${fmt(y2)}`);
  }

  // The links of the item looked at, and all of them when asked.
  function drawLinks() {
    const { model, cfg } = st;
    const { sel, partners, lift } = selection;
    crosses = [];
    put(cross);
    cross.classList.toggle('is-all', cfg.links);
    const add = (type, from, to, extra) => {
      if (!byRef.has(from) || !byRef.has(to)) return;
      const arrow = type === 'needs_first' || type === 'supersedes';
      const path = s('path', { class: `graph-st-link t-${type} ${extra}`, 'marker-end': arrow ? 'url(#graph-st-arrow)' : null });
      cross.append(path);
      const link = { el: path, type, from, to };
      crosses.push(link);
      placeLink(link);
    };
    const drawn = new Set();
    if (sel) {
      for (const row of partners) {
        const [from, to] = direct(row.type, row.dir, sel, row.other);
        drawn.add(`${row.type}:${from}:${to}`);
        drawn.add(`${row.type}:${to}:${from}`);
        add(row.type, from, to, 'is-chosen');
      }
    }
    if (!cfg.links) return;
    const all = new Map();
    const note = (type, a, b) => {
      const x = lift(a);
      const y = lift(b);
      if (x !== y) all.set(`${type}:${x}:${y}`, { type, from: x, to: y });
    };
    for (const link of store.links) {
      if (!model.node(link.source) || !model.node(link.target)) continue;
      if (link.link_type === 'needs_first') note('needs_first', link.target, link.source);
      else note(link.link_type, link.source, link.target);
    }
    for (const mention of store.references || []) {
      const [a, c] = [mention.source.ref, mention.target.ref];
      if (!mention.target.exists || !model.node(a) || !model.node(c)) continue;
      if (model.node(a).parent === c || model.node(c).parent === a) continue;
      note('mention', a, c);
    }
    for (const link of all.values()) if (!drawn.has(`${link.type}:${link.from}:${link.to}`)) add(link.type, link.from, link.to, 'is-all');
  }

  // -- the look of each dot, from the data ----------------------------------------------------------

  function restyle() {
    const { model, cfg, chosen, concerned: concernedRefs } = st;
    selection = selectionOf(model, level, chosen, concernedRefs);
    const { sel, concerned, partnerSet, pathSet, lit } = selection;
    svg.classList.toggle('is-by-epic', cfg.colour === 'epic');
    svg.classList.toggle('has-sel', Boolean(sel || concerned.length));
    svg.setAttribute('aria-label', `The whole project: ${plural(model.epics.length, 'epic')}${level !== 'epics' ? ', their stories' : ''}${level === 'tasks' ? ' and tasks' : ''}, joined as a tree`);
    for (const it of items) {
      const node = model.node(it.ref);
      const late = model.late(it.ref);
      const p = model.progress(it.ref);
      const arc = it.fill && p.total;
      it.g.setAttribute(
        'class',
        `graph-st-node is-${it.kind} s-${node.status} ${hueOf(it.epic)}${late > 0 ? ' is-late' : ''}${arc ? ' has-arc' : ''}${sel === it.ref ? ' is-sel' : ''}${partnerSet.has(it.ref) ? ' is-linked' : ''}${lit.has(it.ref) ? ' is-lit' : ''}${near?.has(it.ref) ? ' is-near' : ''}`,
      );
      it.g.setAttribute('aria-label', `${words(model, it.ref)} Press Enter to open it.`);
      if (sel === it.ref) it.g.setAttribute('aria-current', 'true');
      else it.g.removeAttribute('aria-current');
      if (arc) {
        const C = Math.PI * 2 * (it.r + 3.6);
        it.fill.setAttribute('stroke-dasharray', `${R1((C * p.done) / p.total)} ${R1(C)}`);
      }
    }
    for (const { it, g } of hubNames) g.setAttribute('class', `graph-st-name is-hub s-${model.node(it.ref).status}`);
    for (const line of lines) {
      line.on = pathSet.has(line.b.ref) && (line.a.ref === 'root' || pathSet.has(line.a.ref));
      line.el.classList.toggle('is-lit', line.on);
    }
    const first = model.epics.find((epic) => byRef.has(epic.ref))?.ref;
    if (sel && sel !== lastSel && byRef.has(sel)) setRoving(sel);
    else if (!roving || !byRef.has(roving)) setRoving(first || null);
    lastSel = sel;
    drawLinks();
    buttons();
    namesDirty = true;
  }

  function setRoving(ref) {
    if (roving && byRef.get(roving)) byRef.get(roving).g.setAttribute('tabindex', '-1');
    roving = ref;
    if (ref) byRef.get(ref).g.setAttribute('tabindex', '0');
  }

  const small = (label, fn, text) => h('button', { class: 'small icon-only', type: 'button', 'aria-label': label, title: label, onclick: fn }, text);

  function buttons() {
    const { sel } = selection;
    put(
      zoomButtons,
      sel && byRef.get(sel) ? h('button', { class: 'small', type: 'button', 'data-action': 'zoom-chosen', onclick: () => zoomToChosen(sel) }, 'Zoom to chosen') : null,
      small('Zoom out', () => zoomBy(1 / 1.4), '\u2212'),
      h('button', { class: 'small', type: 'button', 'aria-label': 'Fit the whole project in view', onclick: () => fit() }, 'Fit'),
      small('Zoom in', () => zoomBy(1.4), '+'),
    );
  }

  // -- the view: zoom, place, easing, inertia -------------------------------------------------------

  function applyView() {
    const next = `translate(${R1(view.x)} ${R1(view.y)}) scale(${Math.round(view.k * 1000) / 1000})`;
    if (next === shownView) return;
    shownView = next;
    vp.setAttribute('transform', next);
  }

  function setGoal(next, byPerson = true) {
    Object.assign(goal, next);
    inertia = null;
    touched = touched || byPerson;
    if (reduced) {
      Object.assign(view, goal);
      applyView();
      sizeHubs();
      placeHubs();
      namesDirty = true;
      wake();
      return;
    }
    wake(true);
  }

  // Zoom in about the cursor (a point in the picture's own units) and out about the middle, so that the
  // picture never drifts off.
  function zoomBy(factor, ux = vb.x + vb.w / 2, uy = vb.y + vb.h / 2) {
    const k2 = clampZoom(goal.k * factor);
    const f = k2 / goal.k;
    const ax = factor > 1 ? ux : vb.x + vb.w / 2;
    const ay = factor > 1 ? uy : vb.y + vb.h / 2;
    setGoal({ k: k2, x: ax - (ax - goal.x) * f, y: ay - (ay - goal.y) * f });
  }

  function fit() {
    setGoal(fitView());
  }

  function zoomToChosen(ref) {
    const it = byRef.get(ref);
    if (!it) return;
    const k = Math.max(goal.k, it.kind === 'task' ? 4.2 : 2.4);
    setGoal({ k, x: vb.x + vb.w / 2 - it.n.x * k, y: vb.y + vb.h / 2 - it.n.y * k });
  }

  // True while the zoom and place are still on their way.
  function easeView(dt) {
    if (inertia) {
      view.x += inertia.vx * dt;
      view.y += inertia.vy * dt;
      const slow = 0.5 ** (dt / 120);
      inertia.vx *= slow;
      inertia.vy *= slow;
      goal.x = view.x;
      goal.y = view.y;
      if (Math.hypot(inertia.vx, inertia.vy) < 0.004) inertia = null;
      return true;
    }
    const dk = goal.k - view.k;
    const dx = goal.x - view.x;
    const dy = goal.y - view.y;
    if (Math.abs(dk) < 0.0015 * view.k && Math.abs(dx) < 0.15 && Math.abs(dy) < 0.15) {
      if (dk || dx || dy) Object.assign(view, goal);
      return Boolean(dk || dx || dy);
    }
    const a = 1 - 0.8 ** (dt / STEP);
    view.k += dk * a;
    view.x += dx * a;
    view.y += dy * a;
    return true;
  }

  // -- the names ------------------------------------------------------------------------------------

  const CHAR = 6.5;
  const MONO = 7.5;
  const LINE = 14.5;
  const textWidth = (list) =>
    Math.max(...list.map((line) => {
      const found = /^[EST]-\d+/.exec(line);
      return found ? found[0].length * MONO + (line.length - found[0].length) * CHAR : line.length * CHAR;
    }));

  function measure() {
    const m = svg.getScreenCTM();
    if (!m) return false;
    const box = svg.getBoundingClientRect();
    meas = { sc: m.a, e: m.e - box.left, f: m.f - box.top, w: box.width, h: box.height };
    return true;
  }

  // Names have a fixed size on the screen and are placed so that none overlaps another, from where the
  // dots are now.
  function drawNames() {
    if (!measure()) return;
    const { model, cfg } = st;
    const { sel, partnerSet, concerned } = selection;
    const { sc } = meas;
    const k = view.k;
    const inv = 1 / (sc * k);
    const px = (wx) => (wx * k + view.x) * sc + meas.e;
    const py = (wy) => (wy * k + view.y) * sc + meas.f;
    const near2 = (x, y, pad) => x > -pad && x < meas.w + pad && y > -pad && y < meas.h + pad;
    const hubOf = (ref) => model.epicOf(ref).ref;
    const activeEpics = new Set();
    // Choosing or pointing at a hub (not a single task) brings up the names of the stories round it.
    if (sel && model.node(sel).kind !== 'task') activeEpics.add(hubOf(sel));
    if (hover && model.node(hover).kind !== 'task') activeEpics.add(hubOf(hover));
    const named = new Set(sel ? [sel, ...partnerSet] : concerned);
    if (hover) named.add(hover);
    const mode = cfg.labels;
    const dirOf = (it) => {
      const par = it.parent === 'root' ? { x: 0, y: 0 } : byRef.get(it.parent).n;
      const dx = it.n.x - par.x;
      const dy = it.n.y - par.y;
      const len = Math.hypot(dx, dy) || 1;
      return [dx / len, dy / len];
    };
    // Only the dots near the screen can be in the way of a name. A hub is wider than its body: its
    // arc and, when it is flagged, its ring are drawn outside it, and a name keeps off those too.
    const circles = [];
    const ringOf = (it) => {
      const flagged = model.node(it.ref).status === 'blocked' || model.late(it.ref) > 0;
      if (it.kind === 'task') return it.r + (flagged ? 3.3 : 0);
      return it.r + (flagged ? (it.kind === 'epic' ? 10 : 8) : 5.2);
    };
    for (const it of items) {
      const x = px(it.n.x);
      const y = py(it.n.y);
      if (near2(x, y, 160)) circles.push({ x, y, r: it.r * sc * k + 2, ring: ringOf(it) * sc * k + 2 });
    }
    circles.push({ x: px(0), y: py(0), r: layout.rProj * sc * k + 8, ring: layout.rProj * sc * k + 8 });
    // The small circles are kept in a grid, so that finding what is in the way of a name does not look at every dot.
    const CELL = 64;
    const cells = new Map();
    const big = [];
    let widest = 0;
    for (const c of circles) {
      if (Math.max(c.r, c.ring) > CELL) {
        big.push(c);
        continue;
      }
      widest = Math.max(widest, c.r, c.ring);
      const at = (Math.floor(c.x / CELL) + 2048) * 4096 + Math.floor(c.y / CELL) + 2048;
      if (cells.has(at)) cells.get(at).push(c);
      else cells.set(at, [c]);
    }
    const hitsCircle = (bx, size = 'r') => {
      const touches = (c) => {
        const nx = Math.max(bx[0], Math.min(c.x, bx[2]));
        const ny = Math.max(bx[1], Math.min(c.y, bx[3]));
        return Math.hypot(c.x - nx, c.y - ny) < c[size];
      };
      if (big.some(touches)) return true;
      for (let cx = Math.floor((bx[0] - widest) / CELL); cx <= Math.floor((bx[2] + widest) / CELL); cx += 1) {
        for (let cy = Math.floor((bx[1] - widest) / CELL); cy <= Math.floor((bx[3] + widest) / CELL); cy += 1) {
          if (cells.get((cx + 2048) * 4096 + cy + 2048)?.some(touches)) return true;
        }
      }
      return false;
    };
    const cands = [];
    if (mode !== 'off') {
      for (const it of items) {
        if (it.kind === 'epic') continue;
        if (!near2(px(it.n.x), py(it.n.y), 40)) continue;
        const node = model.node(it.ref);
        let [ux, uy] = dirOf(it);
        const isStory = it.kind === 'story';
        // A story's tasks fan out away from it, so its name goes on the line to its epic, where there is a gap.
        if (isStory && rank > 1 && model.kids(it.ref).length) {
          ux = -ux;
          uy = -uy;
        }
        let lines2 = null;
        let prio = 3;
        const full = `${codeOf(node)} ${node.title}`;
        if (named.has(it.ref)) {
          lines2 = wrap(full, 24, 2);
          prio = 1;
        } else if (mode === 'always') {
          lines2 = [clip(full, isStory ? 26 : 16)];
          prio = isStory ? 2 : 4;
        } else if (isStory) {
          if (k >= 3.2) lines2 = [clip(full, 30)];
          else if (k >= 1.8 || activeEpics.has(hubOf(it.ref))) lines2 = [clip(full, 20)];
          prio = 2;
        } else if (k >= 5.5) lines2 = [clip(full, 30)];
        else if (k >= 3.6) lines2 = [clip(full, 18)];
        else if (activeEpics.has(hubOf(it.ref)) && k >= 2.4) lines2 = [clip(full, 18)];
        if (lines2) cands.push({ ref: it.ref, prio, o: it, ux, uy, lines: lines2 });
      }
    }
    cands.sort((a, c) => a.prio - c.prio);
    const placed = [];
    const out = [];
    const overlaps = (bx) => placed.some((q) => bx[0] < q[2] + 2 && bx[2] > q[0] - 2 && bx[1] < q[3] + 2 && bx[3] > q[1] - 2);
    // The picture's own buttons are in the way of a name, too.
    const box = svg.getBoundingClientRect();
    const zoomBox = zoomButtons.getBoundingClientRect();
    placed.push([zoomBox.left - box.left - 4, zoomBox.top - box.top - 4, zoomBox.right - box.left + 4, zoomBox.bottom - box.top + 4]);

    // An epic's name sits as near to its hub as there is clear room: on the line to the project, or a
    // little to either side of it.
    if (mode !== 'off') {
      const centre = { x: px(0), y: py(0) };
      const spokes = model.epics.filter((epic) => byRef.has(epic.ref)).map((epic) => {
        const o = byRef.get(epic.ref).n;
        return { ref: epic.ref, x1: centre.x, y1: centre.y, x2: px(o.x), y2: py(o.y) };
      });
      const segHitsBox = (sg, bx) => {
        const inside = (x, y) => x >= bx[0] && x <= bx[2] && y >= bx[1] && y <= bx[3];
        if (inside(sg.x1, sg.y1) || inside(sg.x2, sg.y2)) return true;
        const edges = [[bx[0], bx[1], bx[2], bx[1]], [bx[2], bx[1], bx[2], bx[3]], [bx[2], bx[3], bx[0], bx[3]], [bx[0], bx[3], bx[0], bx[1]]];
        const ccw = (ax, ay, bx2, by, cx, cy) => (cy - ay) * (bx2 - ax) > (by - ay) * (cx - ax);
        return edges.some(([x3, y3, x4, y4]) => ccw(sg.x1, sg.y1, x3, y3, x4, y4) !== ccw(sg.x2, sg.y2, x3, y3, x4, y4) && ccw(sg.x1, sg.y1, sg.x2, sg.y2, x3, y3) !== ccw(sg.x1, sg.y1, sg.x2, sg.y2, x4, y4));
      };
      for (const epic of model.epics) {
        const it = byRef.get(epic.ref);
        if (!it) continue;
        const o = it.n;
        const hx = px(o.x);
        const hy = py(o.y);
        if (!near2(hx, hy, 30)) continue;
        const dist = Math.hypot(o.x, o.y);
        const base = Math.atan2(-o.y, -o.x);
        // The whole name if there is room, a narrower one if not, and a short cut of it as a last resort.
        const full = `${codeOf(epic)} ${epic.title}`;
        const variants = [wrap(full, 16, 3), wrap(full, 11, 4), [clip(full, 14)]];
        let best = null;
        let lines2 = variants[0];
        for (const v of variants) {
          const w = textWidth(v);
          const hh = v.length * LINE;
          for (const turn of [0, 0.6, -0.6, 1.2, -1.2, 1.8, -1.8, Math.PI]) {
            const ux = Math.cos(base + turn);
            const uy = Math.sin(base + turn);
            for (let t = it.r + 12; t < Math.max(dist - layout.rProj - 6, it.r + 40); t += 7) {
              const cx = px(o.x + ux * t);
              const cy = py(o.y + uy * t);
              if (Math.hypot(cx - hx, cy - hy) > 190) break;
              const bx = [cx - w / 2, cy - hh / 2, cx + w / 2, cy + hh / 2];
              if (bx[0] < 4 || bx[2] > meas.w - 4 || bx[1] < 4 || bx[3] > meas.h - 4) continue;
              if (overlaps(bx) || hitsCircle(bx, 'ring')) continue;
              const crossings = spokes.filter((sg) => sg.ref !== epic.ref && segHitsBox(sg, bx)).length;
              const cost = t + Math.abs(turn) * 10 + crossings * 30;
              if (!best || cost < best.cost) best = { cost, bx, ux, uy, t };
              break;
            }
          }
          if (best) {
            lines2 = v;
            break;
          }
        }
        if (!best) continue;
        placed.push(best.bx);
        out.push({ epic: true, ref: epic.ref, o: it, ux: best.ux, uy: best.uy, along: best.t, lines: lines2 });
      }
    }

    // A name sits just beyond its dot, away from its parent. The item looked at and what it is linked to
    // may also sit a little to either side, or further out, if that is where there is room.
    const settle = (c, turns, gaps, clear) => {
      const w = textWidth(c.lines);
      const hh = c.lines.length * LINE;
      const rpx = c.o.r * sc * k;
      const base = Math.atan2(c.uy, c.ux);
      let best = null;
      for (const turn of turns) {
        const ux = Math.cos(base + turn);
        const uy = Math.sin(base + turn);
        const reach = (Math.abs(ux) * w) / 2 + (Math.abs(uy) * hh) / 2;
        for (const gp of gaps) {
          const dist = rpx + gp + reach;
          const cx = px(c.o.n.x) + ux * dist;
          const cy = py(c.o.n.y) + uy * dist;
          const bx = [cx - w / 2, cy - hh / 2, cx + w / 2, cy + hh / 2];
          if (bx[0] < 2 || bx[2] > meas.w - 2 || bx[1] < 2 || bx[3] > meas.h - 2) continue;
          if (overlaps(bx) || (clear && hitsCircle(bx))) continue;
          const cost = gp + Math.abs(turn) * 10;
          if (!best || cost < best.cost) best = { cost, bx, ux, uy, dist };
          break;
        }
      }
      return best;
    };
    for (let c of cands) {
      const prime = c.prio === 1;
      const sideways = c.o.kind === 'story' ? [0, 0.5, -0.5, 1, -1] : [0];
      let best = prime
        ? settle(c, [0, 0.6, -0.6, 1.2, -1.2, 1.8, -1.8, Math.PI], [4, 12, 24, 40], true) || settle(c, [0, 0.6, -0.6, 1.2, -1.2, Math.PI], [4, 16, 32], false)
        : settle(c, sideways, [4], true);
      if (!best && !prime && c.lines[0].length > 12) {
        // No clear room for the whole name: its code and the start of its name, which is small enough to fit.
        const node = model.node(c.ref);
        c = { ...c, lines: [clip(`${codeOf(node)} ${node.title}`, 12)] };
        best = settle(c, sideways, [4], true);
      }
      if (!best) continue;
      placed.push(best.bx);
      out.push({ ...c, ux: best.ux, uy: best.uy, dist: best.dist });
    }

    put(labelsG);
    for (const c of out) {
      const wx = c.epic ? c.o.n.x + c.ux * c.along : c.o.n.x + c.ux * c.dist * inv;
      const wy = c.epic ? c.o.n.y + c.uy * c.along : c.o.n.y + c.uy * c.dist * inv;
      const lit = named.has(c.ref);
      const faded = near && !near.has(c.ref);
      const g = s('g', { class: `graph-st-name${c.epic ? ' is-epic' : ''}${lit ? ' is-lit' : ''}${faded ? ' is-faded' : ''}`, transform: `translate(${R1(wx)} ${R1(wy)}) scale(${inv})` });
      const first = (-(c.lines.length - 1) * LINE) / 2 + 4.2;
      c.lines.forEach((line, i) => {
        const text = s('text', { x: 0, y: R1(first + i * LINE), 'text-anchor': 'middle' });
        const found = /^([EST]-\d+)( |$)/.exec(i === 0 ? line : '');
        if (found) text.append(s('tspan', { class: 'graph-st-code' }, found[1]), line.slice(found[1].length));
        else text.append(line);
        g.append(text);
      });
      labelsG.append(g);
    }
    sizeHubs();
    placeHubs();
  }

  // -- the frame loop -------------------------------------------------------------------------------

  // Something is going to move (`moves`) or has to be redrawn: run the loop for as long as it takes.
  function wake(moves = false) {
    if (!sim) return;
    if (moves && !moving) {
      moving = true;
      svg.setAttribute('data-resting', 'false');
    }
    if (raf) return;
    last = 0;
    raf = requestAnimationFrame(frame);
  }

  function frame(now) {
    raf = 0;
    const dt = last ? Math.min(64, now - last) : STEP;
    last = now;
    let busy = false;
    if (!reduced && !sim.resting) {
      acc += dt;
      for (let steps = 0; acc >= STEP && steps < 3 && !sim.resting; steps += 1) {
        sim.tick();
        acc -= STEP;
      }
      if (acc > STEP * 3) acc = 0;
      paint();
      busy = !sim.resting;
    }
    const zoomed = view.k;
    if (easeView(dt)) busy = true;
    if (zoomed !== view.k) {
      sizeHubs();
      placeHubs();
      namesDirty = true;
    }
    applyView();
    if (hoverEl && !candidate?.grabbed) placeTip(hoverEl);
    if (!busy && autoFit) {
      // A big map was painted from its seed and has now settled: show all of it, unless the person has already moved the view.
      autoFit = false;
      if (!touched) {
        setGoal(fitView(), false);
        busy = !reduced;
      }
    }
    if (busy) {
      if (now - namesAt >= (items.length > 600 ? NAMES_EVERY * 2.5 : NAMES_EVERY)) {
        namesAt = now;
        drawNames();
        namesDirty = false;
      }
    } else if (namesDirty) {
      drawNames();
      namesDirty = false;
    }
    if (busy !== moving) {
      moving = busy;
      svg.setAttribute('data-resting', String(!busy));
    }
    if ((busy || namesDirty) && !raf) raf = requestAnimationFrame(frame);
    else if (!busy && !namesDirty) {
      last = 0;
      acc = 0;
    }
  }

  // -- hover and the tip ----------------------------------------------------------------------------

  function placeTip(target) {
    const r = target.querySelector('.graph-st-body').getBoundingClientRect();
    const c = inner.getBoundingClientRect();
    const left = Math.min(Math.max(r.left - c.left + r.width / 2, 130), Math.max(c.width - 130, 130));
    const above = r.top - c.top > 120;
    tip.classList.toggle('is-below', !above);
    tip.style.left = `${Math.round(left)}px`;
    tip.style.top = `${Math.round(above ? r.top - c.top - 10 : r.bottom - c.top + 10)}px`;
  }

  // What stays at full strength while a dot is pointed at: it, what it hangs from, what hangs from it and
  // what it is linked to.
  function nearOf(ref) {
    const { lift, visibleKids } = selection;
    const set = new Set([ref]);
    const parent = st.model.node(ref)?.parent;
    if (parent) set.add(parent);
    for (const kid of visibleKids(ref)) set.add(kid.ref);
    for (const row of partnersOf(ref, lift)) set.add(row.other);
    return set;
  }

  function setHover(ref, target) {
    if (ref === hover) return;
    hover = ref;
    hoverEl = ref ? target : null;
    near = ref ? nearOf(ref) : null;
    svg.classList.toggle('is-hovering', Boolean(ref));
    for (const it of items) it.g.classList.toggle('is-near', Boolean(near?.has(it.ref)));
    for (const line of lines) line.el.classList.toggle('is-near', Boolean(near && near.has(line.b.ref) && (line.a.ref === 'root' || near.has(line.a.ref))));
    for (const link of crosses) link.el.classList.toggle('is-near', Boolean(near && near.has(link.from) && near.has(link.to)));
    if (ref && !candidate?.grabbed) {
      put(tip, tipContent(st.model, ref));
      tip.hidden = false;
      placeTip(target);
    } else tip.hidden = true;
    namesDirty = true;
    wake();
  }

  // -- the pointer ----------------------------------------------------------------------------------

  // A point on the screen as a point of the picture's own units, before the zoom and place.
  function unitsOf(e) {
    const m = svg.getScreenCTM();
    point.x = e.clientX;
    point.y = e.clientY;
    const u = m ? point.matrixTransform(m.inverse()) : { x: 0, y: 0 };
    return { ux: u.x, uy: u.y, x: (u.x - view.x) / view.k, y: (u.y - view.y) / view.k };
  }

  function letGo() {
    document.removeEventListener('keydown', escape, true);
    const grabbed = Boolean(candidate?.grabbed);
    if (grabbed) {
      sim.release(candidate.ref);
      svg.classList.remove('is-dragging');
      justDragged = true;
    }
    candidate = null;
    wake(grabbed);
  }

  function escape(e) {
    if (e.key !== 'Escape' || !candidate) return;
    e.stopPropagation();
    letGo();
  }

  function wire(it) {
    const g = it.g;
    g.addEventListener('click', (e) => {
      e.stopPropagation();
      st.api.open(it.ref);
    });
    g.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        st.api.open(it.ref);
        return;
      }
      if (!['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp'].includes(e.key)) return;
      const { model } = st;
      const { visibleKids } = selection;
      let target = null;
      if (e.key === 'ArrowDown') target = visibleKids(it.ref)[0]?.ref;
      else if (e.key === 'ArrowUp') target = model.node(it.ref).parent;
      else {
        const node = model.node(it.ref);
        const sibs = node.parent ? visibleKids(node.parent) : model.epics;
        const at = sibs.findIndex((x) => x.ref === it.ref);
        target = sibs[(at + (e.key === 'ArrowRight' ? 1 : -1) + sibs.length) % sibs.length]?.ref;
      }
      const next = target && byRef.get(target);
      if (!next) return;
      e.preventDefault();
      setRoving(target);
      next.g.focus();
    });
    g.addEventListener('pointerenter', () => { if (!candidate?.grabbed) setHover(it.ref, g); });
    g.addEventListener('pointerleave', () => { if (!candidate?.grabbed && hoverEl === g && document.activeElement !== g) setHover(null, null); });
    g.addEventListener('focus', () => setHover(it.ref, g));
    g.addEventListener('blur', () => { if (!candidate?.grabbed) setHover(null, null); });
  }

  // A finger that starts on a dot drags it instead of scrolling the page; the tap that opens it is then ours to read (a prevented touch makes no click).
  svg.addEventListener('touchstart', (e) => {
    if (!reduced && e.target.closest('.graph-st-node')) e.preventDefault();
  }, { passive: false });

  // The browser's own text selection never starts on the picture: a quick second press selects a word there, and moving with
  // the button held stretches it over the hover card and the whole map. CSS alone is not enough in WebKit.
  inner.addEventListener('mousedown', (e) => { if (e.button === 0) e.preventDefault(); });
  inner.addEventListener('selectstart', (e) => e.preventDefault());
  inner.addEventListener('dragstart', (e) => e.preventDefault());
  // And if a selection reaches the picture anyway (it can start somewhere else and be stretched over it), it is let go.
  const noSelection = () => {
    const chosen = getSelection();
    if (chosen && !chosen.isCollapsed && (inner.contains(chosen.anchorNode) || inner.contains(chosen.focusNode))) chosen.removeAllRanges();
  };
  document.addEventListener('selectionchange', noSelection);

  svg.addEventListener('pointerdown', (e) => {
    justDragged = false;
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    pointers.add(e.pointerId);
    if (pointers.size > 1) {
      // A second pointer is not a drag of a dot.
      if (candidate) letGo();
      pan = null;
      return;
    }
    inertia = null;
    const dot = e.target.closest('.graph-st-node');
    if (dot) {
      if (reduced) return;
      const it = byRef.get(dot.getAttribute('data-ref'));
      const at = unitsOf(e);
      candidate = { ref: it.ref, pointer: e.pointerId, sx: e.clientX, sy: e.clientY, dx: it.n.x - at.x, dy: it.n.y - at.y, grabbed: false };
      return;
    }
    Object.assign(goal, view);
    touched = true;
    pan = { sx: e.clientX, sy: e.clientY, x: view.x, y: view.y, samples: [{ t: performance.now(), x: e.clientX, y: e.clientY }], moved: false };
    svg.setPointerCapture(e.pointerId);
    svg.classList.add('is-grabbing');
  });

  svg.addEventListener('pointermove', (e) => {
    if (candidate && e.pointerId === candidate.pointer) {
      if (!candidate.grabbed) {
        if (Math.hypot(e.clientX - candidate.sx, e.clientY - candidate.sy) <= DRAG_FROM) return;
        candidate.grabbed = true;
        svg.setPointerCapture(e.pointerId);
        svg.classList.add('is-dragging');
        document.addEventListener('keydown', escape, true);
        tip.hidden = true;
        const at = unitsOf(e);
        sim.grab(candidate.ref, at.x + candidate.dx, at.y + candidate.dy);
      } else {
        const at = unitsOf(e);
        sim.moveTo(candidate.ref, at.x + candidate.dx, at.y + candidate.dy);
      }
      wake(true);
      return;
    }
    if (!pan) return;
    const sc = meas.sc || svg.getScreenCTM().a;
    if (Math.abs(e.clientX - pan.sx) + Math.abs(e.clientY - pan.sy) > 4) pan.moved = true;
    view.x = pan.x + (e.clientX - pan.sx) / sc;
    view.y = pan.y + (e.clientY - pan.sy) / sc;
    goal.x = view.x;
    goal.y = view.y;
    const now = performance.now();
    pan.samples.push({ t: now, x: e.clientX, y: e.clientY });
    while (pan.samples.length > 2 && now - pan.samples[0].t > 100) pan.samples.shift();
    applyView();
  });

  const finish = (e) => {
    pointers.delete(e.pointerId);
    if (candidate && e.pointerId === candidate.pointer) {
      const wasGrabbed = candidate.grabbed;
      const tapped = !wasGrabbed && e.type === 'pointerup' && e.pointerType === 'touch' ? candidate.ref : null;
      letGo();
      if (tapped) st.api.open(tapped);
      if (wasGrabbed) {
        const under = document.elementFromPoint(e.clientX, e.clientY)?.closest?.('.graph-st-node');
        hover = null;
        setHover(under ? under.getAttribute('data-ref') : null, under);
      }
      return;
    }
    if (!pan) return;
    const { samples, moved } = pan;
    pan = null;
    svg.classList.remove('is-grabbing');
    if (!moved) return;
    namesDirty = true;
    const now = performance.now();
    const first = samples[0];
    const end = samples[samples.length - 1];
    // A pointer that rested a moment before it was let go leaves nothing to carry on with.
    if (!reduced && e.type === 'pointerup' && now - end.t <= 100 && end.t > first.t) {
      const sc = meas.sc || 1;
      const vx = (end.x - first.x) / (end.t - first.t) / sc;
      const vy = (end.y - first.y) / (end.t - first.t) / sc;
      if (Math.hypot(vx, vy) > 0.02) inertia = { vx, vy };
    }
    wake(Boolean(inertia));
  };
  // The release is heard wherever the pointer is let go, so a press that ends outside the map is not left half done.
  window.addEventListener('pointerup', finish);
  window.addEventListener('pointercancel', finish);
  // A press that was a drag opens nothing.
  svg.addEventListener('click', (e) => {
    if (!justDragged) return;
    justDragged = false;
    e.stopPropagation();
    e.preventDefault();
  }, true);
  svg.addEventListener('wheel', (e) => {
    e.preventDefault();
    const dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 100 : 1);
    const at = unitsOf(e);
    zoomBy(Math.exp(-dy * 0.002), at.ux, at.uy);
  }, { passive: false });

  // -- the whole map's life -------------------------------------------------------------------------

  const observer = new ResizeObserver(() => {
    measure();
    sizeHubs();
    namesDirty = true;
    wake();
  });
  const onMotion = () => {
    reduced = motion.matches;
    if (reduced) {
      restNow();
      Object.assign(view, goal);
      inertia = null;
      paint();
      applyView();
      sizeHubs();
      svg.setAttribute('data-resting', 'true');
    }
    namesDirty = true;
    wake();
  };
  motion.addEventListener('change', onMotion);

  start();
  {
    const b = extents();
    vb = { x: b.x0 - 30, y: b.y0 - 30, w: b.x1 - b.x0 + 60, h: b.y1 - b.y0 + 60 };
    svg.setAttribute('viewBox', `${R1(vb.x)} ${R1(vb.y)} ${R1(vb.w)} ${R1(vb.h)}`);
    touched = view.level === level;
    autoFit = !reduced && !sim.resting;
    if (view.level !== level) Object.assign(view, { k: 1, x: 0, y: 0, level });
    Object.assign(goal, view);
  }
  selection = selectionOf(first.model, level, first.chosen, first.concerned);
  build();
  restyle();
  observer.observe(inner);
  svg.setAttribute('data-resting', String(sim.resting));
  applyView();
  wake(!sim.resting);

  return {
    el,
    // The page's new choices and data: restyled in place, or the dots added and removed when the tree
    // changed shape.
    update(next) {
      st = { ...st, ...next };
      if (shapeKey(st.tree, level) !== key) reshape();
      restyle();
      sizeHubs();
      wake(!sim.resting);
    },
    tidy() {
      const pos = wholeLayout(st.tree, level).pos;
      sim.glideTo(pos, reduced ? 1 : 36);
      if (reduced) {
        restNow();
        paint();
        namesDirty = true;
      }
      setGoal(fitView(pos));
      wake(!reduced);
    },
    setForces(values) {
      sim.setForces(values);
      if (reduced) {
        restNow();
        paint();
        namesDirty = true;
      }
      wake(!reduced);
    },
    resetForces() {
      sim.resetForces();
      if (reduced) {
        restNow();
        paint();
        namesDirty = true;
      }
      wake(!reduced);
    },
    fit,
    destroy() {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      observer.disconnect();
      motion.removeEventListener('change', onMotion);
      document.removeEventListener('keydown', escape, true);
      document.removeEventListener('selectionchange', noSelection);
      window.removeEventListener('pointerup', finish);
      window.removeEventListener('pointercancel', finish);
      sim = null;
    },
  };
}
