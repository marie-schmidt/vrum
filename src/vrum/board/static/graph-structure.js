// The Structure view of the Graph: how the work fits together, drawn as Obsidian draws its graph. The
// whole project is a living map (graph-map.js, on the physics of graph-sim.js): each epic a big disc,
// each story a smaller one, each task a small dot, nothing but the hierarchy joined by lines. You can
// grab any dot and it eases back; names are shown only where there is room and as you zoom in; the
// links between items (needs first, duplicates, related, references) are drawn for the item looked at
// only, or for all when asked. The "Scope" choice in the toolbar swaps the whole project for the radial
// view of one epic, with every name. There is no card or side column: a click or Enter on any dot opens
// that item's page.
//
// What is looked at is the Graph's focus (`focus=` in the address, chosen in the box at the top), so
// the other tabs follow it; here it is marked on the picture with its links. Its own choices are in the
// address too: `scope` (project, or an epic's reference), `level` (epics, stories, tasks), `labels`
// (off, zoom, always), `colour` (status, epic) and `links=1`. The zoom and the place of the picture and
// the Forces sliders are not (they are kept while the board is open). Everything is read from the store.

import { projectId, projectInfo } from './api.js';
import { h, put } from './dom.js';
import { ICONS, icon } from './icons.js';
import { parseItemRef } from './labels.js';
import { LEAF_DOT, angleBetween, epicExtent, epicRadial, polar, sector, treeCurve, treeOf } from './graph-radial.js';
import { biggerChip, keyChip, lateChip, linkChips, mapStatusChips, ringChip, shapeChips, startedChip, statusChips, viewKey } from './graph-key.js';
import { closeForces, createForces, forceValues, forcesAreDefault } from './graph-forces.js';
import { createMap, hueOf, tipContent, words } from './graph-map.js';
import { clip, code, codeOf, linksOf, s, segment, toggle, viewMessage, workModel, wrap } from './graph-shared.js';
import { ensure, store } from './store.js';

// The address keys that are this view's own, which are forgotten when another tab is chosen.
export const KEYS = ['scope', 'level', 'labels', 'colour', 'links'];

const LABEL_MODES = [
  ['off', 'Off', 'No names on the picture; point at a dot or hub to read it'],
  ['zoom', 'On zoom', 'Names appear as you zoom in'],
  ['always', 'Always', 'As many names as fit'],
];
const LEVELS = [
  ['epics', 'Epics', 'Show the epics only'],
  ['stories', '+ Stories', 'Show epics and stories'],
  ['tasks', '+ Tasks', 'Show epics, stories and tasks'],
];
const COLOURS = [
  ['status', 'Status', 'Colour by where the work stands'],
  ['epic', 'Epic', 'Colour by the epic it belongs to'],
];
// A project this big shows its stories first, and the tasks when asked.
const MOST_DRAWN = 1500;
const R1 = (n) => Math.round(n * 10) / 10;

let root = null;
let api = null;
let stop = []; // what a drawing has to undo when the next one replaces it
let name = null; // the project's name, for the middle of the picture
let loadedRefs = false;
let live = null; // the living map while it is on the page: { level, map, forces, signatures, section, bar, key }
const ring = { zoom: 1, x: 0, y: 0, epic: null }; // the zoom and place of one epic

// -- the page ------------------------------------------------------------------------------------

export function mount(element, nextApi) {
  root = element;
  api = nextApi;
  closeForces();
  render();
  if (!loadedRefs) {
    loadedRefs = true;
    // The references written in texts are links too; they come a moment after the picture.
    ensure('references').then(() => root && render()).catch(() => {});
  }
  if (name === null) {
    projectInfo(projectId()).then((found) => { name = found.name; if (root) render(); }).catch(() => { name = ''; });
  }
}

export function update(nextApi) {
  api = nextApi;
  if (root) render();
}

export function refresh() {
  if (root) render();
}

export function unmount() {
  undo();
  dropLive();
  root = null;
  api = null;
}

function undo() {
  for (const fn of stop) fn();
  stop = [];
}

function dropLive() {
  if (!live) return;
  live.map.destroy();
  live.forces.dispose();
  live = null;
}

const pick = (value, allowed, fallback) => (allowed.includes(value) ? value : fallback);

function settings(model) {
  const p = api.params;
  const asked = p.get('scope');
  const epic = asked && asked !== 'project' && model.node(asked)?.kind === 'epic' ? asked : null;
  return {
    epic,
    labels: pick(p.get('labels'), LABEL_MODES.map(([v]) => v), 'zoom'),
    colour: pick(p.get('colour'), COLOURS.map(([v]) => v), 'status'),
    level: pick(p.get('level'), LEVELS.map(([v]) => v), model.list.length > MOST_DRAWN ? 'stories' : 'tasks'),
    links: p.get('links') === '1',
  };
}

// A choice that only changes how the picture looks keeps the browser's back button for something else.
const setLook = (changes) => api.setParams(changes, { replace: true });
// The Key's epic buttons put an epic on the picture by making it what the Graph looks at (again to let go).
const mark = (ref) => (ref === api.focus ? api.setParams({ focus: null }) : api.setFocus(ref));

// The one choice of what is drawn: the whole project, or one epic with every name. An epic that does
// not hold what the Graph is looking at takes the look with it, since the picture could not show it.
function lookAt(model, epic) {
  const select = h(
    'select',
    {
      id: 'graph-st-scope',
      'data-choice': 'scope',
      onchange: () => {
        const ref = select.value;
        if (!ref) return setLook({ scope: null });
        const apart = api.focus && model.node(api.focus) && !model.subtree(ref).has(api.focus);
        return setLook(apart ? { scope: ref, focus: ref, trail: null } : { scope: ref });
      },
    },
    h('option', { value: '' }, 'Whole project'),
    model.epics.map((one) => h('option', { value: one.ref }, `${codeOf(one)} ${clip(one.title, 48)}`)),
  );
  select.value = epic || '';
  return h('div', { class: 'graph-x-control graph-st-look' }, h('label', { class: 'graph-x-control-label', for: 'graph-st-scope' }, 'Scope'), select);
}

// An acceptance criterion or a note in focus is not work, so nothing is marked on the whole map as
// the item looked at; the work it concerns is lit instead. Empty when the focus is not one of those, or
// the records are not loaded.
function concernedBy(model, ref) {
  const found = ref ? parseItemRef(ref) : null;
  if (!found || !['rule', 'note'].includes(found.kind)) return [];
  const record = (store[{ rule: 'rules', note: 'notes' }[found.kind]] || []).find((one) => one.id === found.id);
  if (!record) return [];
  return (found.kind === 'rule' ? [record.node] : record.nodes || []).filter((one) => one && model.node(one));
}

function render() {
  const keep = rememberFocus();
  // The work is read again for each drawing: it is cheap, and the store may have changed.
  const model = workModel();
  if (!model.list.length) {
    undo();
    dropLive();
    put(root, h('div', { class: 'graph-xv' }, viewMessage('Nothing is planned yet', 'The Structure map draws epics, stories and tasks. When the first epic is made, it appears here.')));
    return;
  }
  const cfg = settings(model);
  let chosen = api.focus && model.node(api.focus) ? api.focus : null;
  let epic = cfg.epic;
  if (epic && chosen && !model.subtree(epic).has(chosen)) {
    // The thing looked at is in another epic: the whole project has room for it, and the address follows.
    epic = null;
    api.setParams({ scope: null }, { replace: true });
  }
  if (epic && chosen === epic) chosen = null;
  undo();
  if (epic) {
    dropLive();
    put(root, h('div', { class: 'graph-xv' }, epicPage(model, epic, chosen)));
  } else showWhole(model, cfg, chosen);
  restoreFocus(keep);
}

// Where the keyboard was, so that a drawing that replaces the elements does not lose it.
function rememberFocus() {
  const at = document.activeElement;
  if (!at || !root || !root.contains(at)) return null;
  const node = at.closest('[data-ref]');
  if (node && at.closest('svg')) return { selector: `svg [data-ref="${node.getAttribute('data-ref')}"]` };
  const choice = at.closest('[data-choice]');
  return choice ? { selector: `[data-choice="${choice.getAttribute('data-choice')}"]` } : null;
}

function restoreFocus(keep) {
  if (keep) root.querySelector(keep.selector)?.focus({ preventScroll: true });
}

// -- shared parts of both pictures -----------------------------------------------------------------

// The status mark of a leaf, drawn from the board's own status icons, centred on 0,0 with radius r.
function statusMark(status, r) {
  const k = r / 9;
  const g = s('g', { class: `graph-rd-mark s-${status}`, transform: `scale(${R1(k)}) translate(-12 -12)`, 'stroke-width': R1(1.7 / k) });
  g.append(s('circle', { class: 'graph-rd-bg', cx: 12, cy: 12, r: 9 }));
  for (const [tag, attrs] of ICONS[status] || ICONS.new) g.append(s(tag, attrs));
  return g;
}

// A node opens its page on a click, Enter or Space.
function opensItsPage(el, ref) {
  el.addEventListener('click', (e) => {
    e.stopPropagation();
    api.open(ref);
  });
  el.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    api.open(ref);
  });
}

// -- the whole project -------------------------------------------------------------------------

// How many of the finest level drawn are in each status: the dots of the map, counted.
function countsOf(model, tree, level) {
  const counts = {};
  const add = (ref) => { counts[model.node(ref).status] = (counts[model.node(ref).status] || 0) + 1; };
  for (const epic of tree) {
    if (level === 'epics') add(epic.ref);
    for (const story of epic.stories) {
      if (level === 'stories') add(story.ref);
      if (level === 'tasks') story.tasks.forEach((task) => add(task.ref));
    }
  }
  return counts;
}

function keyOf(model, tree, cfg, chosen) {
  const epicLegend = model.epics.map((epic, i) =>
    h('li', { class: 'graph-key-chip is-button' }, h('button', { class: `graph-x-legend-button ${hueOf(i)}`, type: 'button', title: `${codeOf(epic)} ${epic.title}`, onclick: () => mark(epic.ref) }, h('span', { class: 'graph-x-swatch' }), code(codeOf(epic)), h('span', { class: 'graph-x-chip-name' }, epic.title))),
  );
  return viewKey({
    label: 'How to read the map',
    groups: [
      { label: 'Shapes', chips: [...shapeChips({ stories: cfg.level !== 'epics', tasks: cfg.level === 'tasks' }), biggerChip(cfg.level === 'epics' ? 'Bigger epic: more tasks under it' : 'Bigger dot: more tasks under it, or a higher priority'), lateChip('Dashed red ring: overdue', 'ring'), keyChip(h('span', { class: 'graph-x-late is-ring is-danger' }), 'Solid red ring: blocked')] },
      cfg.colour === 'epic' ? { label: 'Colour by epic', chips: [...epicLegend, startedChip()] } : { label: 'Colour is the status', chips: mapStatusChips(countsOf(model, tree, cfg.level), cfg.level) },
      cfg.links || chosen ? { label: 'Lines', chips: linkChips() } : null,
    ],
  });
}

function barOf(model, cfg) {
  return h(
    'div',
    { class: 'graph-st-toolbar' },
    lookAt(model, null),
    segment('Show', 'Which levels to draw', LEVELS, cfg.level, (v) => setLook({ level: v === 'tasks' && model.list.length <= MOST_DRAWN ? null : v }), 'level'),
    segment('Colour by', 'What the colour means', COLOURS, cfg.colour, (v) => setLook({ colour: v === 'status' ? null : v }), 'colour'),
    segment('Labels', 'When to show names', LABEL_MODES, cfg.labels, (v) => setLook({ labels: v === 'zoom' ? null : v }), 'labels'),
    toggle('Show links between items', 'link', cfg.links, () => setLook({ links: cfg.links ? null : '1' }), { title: 'Needs first, duplicates, related and references, as soft curves', 'data-choice': 'links' }),
    live.forces.el,
    h('button', { id: 'graph-st-tidy', class: 'small', type: 'button', onclick: () => live.map.tidy() }, icon('structure', { size: 14 }), 'Tidy'),
    live.key,
  );
}

// What each part of the page shows, to know when it has to be drawn again: the toolbar depends on the
// choices and the epics, the key on the colour, the links, what is looked at and how many dots are in each status.
function signaturesOf(model, tree, cfg, chosen) {
  const epics = model.epics.map((epic) => [epic.ref, epic.title]);
  return {
    bar: JSON.stringify([cfg.colour, cfg.labels, cfg.links, epics]),
    key: JSON.stringify([cfg.colour, cfg.links, Boolean(chosen), countsOf(model, tree, cfg.level), epics]),
  };
}

// The whole project. The picture is made once and then changed in place, so that a refresh of the work
// never moves a dot or interrupts a drag; only a choice of level makes a new one.
function showWhole(model, cfg, chosen) {
  const tree = treeOf(model);
  const inputs = { api, model, tree, cfg, chosen, concerned: concernedBy(model, api.focus), name };
  const now = signaturesOf(model, tree, cfg, chosen);
  if (live && live.level === cfg.level && root.contains(live.section)) {
    live.map.update(inputs);
    if (now.key !== live.signatures.key) {
      const next = keyOf(model, tree, cfg, chosen);
      live.key.replaceWith(next);
      live.key = next;
    }
    if (now.bar !== live.signatures.bar) {
      const next = barOf(model, cfg);
      live.bar.replaceWith(next);
      live.bar = next;
    }
    live.signatures = now;
    return;
  }
  dropLive();
  const forces = createForces({ change: (values) => live.map.setForces(values), reset: () => live.map.resetForces() });
  live = { level: cfg.level, map: createMap({ ...inputs, forces: forcesAreDefault() ? null : forceValues() }), forces, signatures: now };
  live.key = keyOf(model, tree, cfg, chosen);
  live.bar = barOf(model, cfg);
  live.section = h('section', { class: 'graph-x-panel graph-st-main', 'aria-labelledby': 'graph-st-title' }, h('h2', { id: 'graph-st-title', class: 'sr-only' }, 'Structure of the whole project'), live.bar, live.map.el);
  put(root, h('div', { class: 'graph-xv' }, live.section));
}

// Calls fn whenever the element's size changes; undone when the next drawing replaces this one.
function watch(el, fn) {
  const observer = new ResizeObserver(() => fn());
  observer.observe(el);
  stop.push(() => observer.disconnect());
}

// -- one epic --------------------------------------------------------------------------------------

function epicPage(model, epicRef, chosen) {
  const epic = model.node(epicRef);
  const tree = treeOf(model).find((one) => one.ref === epicRef);
  const L = epicRadial(tree);
  if (ring.epic !== epicRef) {
    ring.epic = epicRef;
    ring.zoom = 1;
    ring.x = 0;
    ring.y = 0;
  }
  const inside = (ref) => L.pos.has(ref);
  const sel = chosen && inside(chosen) ? chosen : null;
  const rows = sel ? linksOf(sel).map((row) => ({ ...row, inside: inside(row.other) })) : [];
  const linked = new Set(rows.filter((row) => row.inside).map((row) => row.other));
  const selStory = sel && model.node(sel).kind === 'story' ? sel : null;

  const svg = s('svg', { class: 'graph-rd-svg', role: 'group', 'aria-label': `Radial tree of ${codeOf(epic)} ${epic.title}: stories in a ring, tasks on the outside` });
  const tip = h('div', { class: 'graph-x-tip', hidden: true, role: 'presentation' });
  const inner = h('div', { class: 'graph-rd-inner' }, svg, tip);
  const scroller = h('div', { class: 'graph-rd-scroll', tabindex: '0', role: 'region', 'aria-label': `The tree of ${codeOf(epic)} ${epic.title}. It scrolls sideways on a small screen.` }, inner);
  const note = h('div', { class: 'graph-rd-note-holder' });
  const small = (label, fn, text) => h('button', { class: 'small icon-only', type: 'button', 'aria-label': label, title: label, onclick: fn }, text);
  const controls = h(
    'div',
    { class: 'graph-st-zoom', role: 'group', 'aria-label': 'Zoom' },
    small('Zoom out', () => { ring.zoom = Math.max(0.6, R1(ring.zoom - 0.2)); redraw(); }, '\u2212'),
    h('button', { class: 'small', type: 'button', 'aria-label': 'Fit the whole tree in view', onclick: () => { ring.zoom = 1; ring.x = 0; ring.y = 0; redraw(); } }, 'Fit'),
    small('Zoom in', () => { ring.zoom = Math.min(2.4, R1(ring.zoom + 0.2)); redraw(); }, '+'),
  );
  const canvas = h('div', { class: `graph-x-canvas graph-rd-canvas${sel ? ' has-sel' : ''}` }, controls, scroller, note);
  const hasSquare = L.groups.some((gr) => gr.leafStory);
  const key = L.empty
    ? null
    : viewKey({
        label: 'How to read the tree',
        groups: [
          { label: 'The leaves', chips: [...statusChips(), biggerChip('Bigger leaf, higher priority'), ringChip('Ring round a story: how much is done'), hasSquare ? keyChip(h('span', { class: 'graph-key-square' }), 'Square: a story with no tasks yet') : null, lateChip('Red dot: overdue')].filter(Boolean) },
          rows.some((row) => row.inside) ? { label: 'Lines', chips: linkChips() } : null,
        ],
      });
  const toolbar = h('div', { class: 'graph-st-toolbar' }, lookAt(model, epicRef), key);
  const main = h(
    'section',
    { class: 'graph-x-panel graph-st-main', 'aria-labelledby': 'graph-st-title' },
    h('h2', { id: 'graph-st-title', class: 'sr-only' }, `Focus on ${codeOf(epic)} ${epic.title}`),
    toolbar,
    canvas,
  );

  // The epic's code and name and, when it has work, how much of it is done, inside its hub.
  const hubWords = (percent) => {
    const lines = wrap(epic.title, 11, 2);
    const block = 14 + lines.length * 10.5 + (percent ? 12 : 0);
    const top = -block / 2;
    const g = s('g', { class: 'graph-rd-epic-words' });
    g.append(s('text', { class: 'graph-rd-epic-code', 'text-anchor': 'middle', y: R1(top + 11) }, codeOf(epic)));
    lines.forEach((line, i) => g.append(s('text', { class: 'graph-rd-epic-name', 'text-anchor': 'middle', y: R1(top + 23 + i * 10.5) }, line)));
    if (percent) g.append(s('text', { class: 'graph-rd-epic-pct', 'text-anchor': 'middle', y: R1(top + block - 2) }, percent));
    return g;
  };

  if (L.empty) {
    controls.remove();
    svg.setAttribute('viewBox', '-340 -170 680 340');
    const hub = s(
      'g',
      { class: 'graph-rd-epic', tabindex: '0', role: 'button', 'data-ref': epicRef, 'aria-label': `${words(model, epicRef)} Press Enter to open it.` },
      s('circle', { class: 'graph-rd-ring', r: 50 }),
      s('circle', { class: 'graph-rd-epic-track', r: 45 }),
      s('circle', { class: 'graph-rd-epic-body', r: 40 }),
      hubWords(''),
    );
    opensItsPage(hub, epicRef);
    svg.append(hub, s('text', { class: 'graph-rd-empty', 'text-anchor': 'middle', y: 78 }, 'Nothing is planned yet: no stories, so no leaves'));
    return main;
  }

  const labelMax = 30;
  const drawTree = (full) => {
    const g = s('g', { class: 'graph-rd-vp', transform: `translate(${R1(ring.x)} ${R1(ring.y)}) scale(${ring.zoom})` });
    const bands = s('g', { class: 'graph-rd-bands' });
    L.groups.forEach((gr, i) => {
      const on = selStory === gr.story.ref || (sel && gr.leaves.some((x) => x.ref === sel));
      bands.append(s('path', { class: `graph-rd-band${i % 2 ? ' is-alt' : ''}${on ? ' is-on' : ''}`, d: sector(L.rS + 20, L.R2 + 16, gr.a0 + 0.004, gr.a1 - 0.004) }));
    });
    g.append(bands);
    const lines = s('g', { class: 'graph-rd-lines' });
    for (const gr of L.groups) {
      const sp = L.pos.get(gr.story.ref);
      lines.append(s('line', { class: 'graph-rd-line', x1: 0, y1: 0, x2: R1(sp.x), y2: R1(sp.y) }));
      if (gr.leafStory) continue;
      for (const leaf of gr.leaves) lines.append(s('path', { class: 'graph-rd-line', d: treeCurve(sp, L.pos.get(leaf.ref)) }));
    }
    g.append(lines);

    // The links of the item looked at only, curved round the middle.
    if (sel) {
      const from = L.pos.get(sel);
      const x = s('g', { class: 'graph-rd-links' });
      for (const row of rows.filter((r) => r.inside)) {
        const to = L.pos.get(row.other);
        const d = angleBetween(from.ang, to.ang);
        const mid = from.ang + d / 2;
        const base = (from.r + to.r) / 2;
        const rc = Math.max(0.1, 1 - 0.9 * Math.pow(Math.abs(d) / Math.PI, 0.65)) * base;
        const [cx, cy] = polar(rc, mid);
        const trim = (p, dist) => {
          const vx = cx - p.x;
          const vy = cy - p.y;
          const len = Math.hypot(vx, vy) || 1;
          return [p.x + (vx / len) * dist, p.y + (vy / len) * dist];
        };
        let a = from;
        let b = to;
        let ra = from.leaf ? 11 : 17;
        let rb = to.leaf ? 11 : 17;
        const arrow = row.type === 'needs_first' || row.type === 'supersedes';
        const swap = (row.type === 'needs_first' && row.dir === 'out') || (row.type === 'supersedes' && row.dir === 'in');
        if (swap) { a = to; b = from; [ra, rb] = [rb, ra]; }
        const [ax, ay] = trim(a, ra);
        const [bx, by] = trim(b, rb + (arrow ? 3 : 0));
        x.append(s('path', { class: `graph-st-link t-${row.type} is-chosen`, d: `M${R1(ax)} ${R1(ay)} Q${R1(cx)} ${R1(cy)} ${R1(bx)} ${R1(by)}`, 'marker-end': arrow ? 'url(#graph-rd-arrow)' : null }));
      }
      g.append(x);
    }

    const nodes = s('g', { class: 'graph-rd-nodes' });
    for (const gr of L.groups) {
      if (gr.leafStory) continue;
      const story = model.node(gr.story.ref);
      const p = L.pos.get(story.ref);
      const pr = model.progress(story.ref);
      const C = 2 * Math.PI * 17;
      const sg = s(
        'g',
        {
          class: `graph-rd-story s-${story.status}${sel === story.ref ? ' is-sel' : ''}${linked.has(story.ref) ? ' is-linked' : ''}`,
          transform: `translate(${R1(p.x)} ${R1(p.y)})`,
          tabindex: story.ref === (sel || rovingStart()) ? '0' : '-1',
          role: 'button',
          'data-ref': story.ref,
          'aria-current': sel === story.ref ? 'true' : null,
          'aria-label': `${words(model, story.ref)} Press Enter to open it.`,
        },
        s('circle', { class: 'graph-rd-ring', r: 21 }),
        s('circle', { class: 'graph-rd-track', r: 17 }),
      );
      if (pr.total) sg.append(s('circle', { class: 'graph-rd-fill', r: 17, 'stroke-dasharray': `${R1((C * pr.done) / pr.total)} ${R1(C)}`, transform: 'rotate(-90)' }));
      sg.append(s('circle', { class: 'graph-rd-body', r: 14 }), s('text', { class: 'graph-rd-code', 'text-anchor': 'middle', dy: '0.34em' }, codeOf(story)));
      nodes.append(sg);
    }
    const ep = model.progress(epicRef);
    const EC = 2 * Math.PI * 45;
    const eg = s(
      'g',
      { class: 'graph-rd-epic', tabindex: '-1', role: 'button', 'data-ref': epicRef, 'aria-label': `${words(model, epicRef)} Press Enter to open it.` },
      s('circle', { class: 'graph-rd-ring', r: 50 }),
      s('circle', { class: 'graph-rd-epic-track', r: 45 }),
    );
    if (ep.total) eg.append(s('circle', { class: 'graph-rd-epic-fill', r: 45, 'stroke-dasharray': `${R1((EC * ep.done) / ep.total)} ${R1(EC)}`, transform: 'rotate(-90)' }));
    eg.append(s('circle', { class: 'graph-rd-epic-body', r: 40 }), hubWords(ep.total ? `${Math.round((100 * ep.done) / ep.total)}%` : ''));
    nodes.append(eg);
    for (const gr of L.groups) {
      for (const leafOf of gr.leaves) {
        const leaf = model.node(leafOf.ref);
        const p = L.pos.get(leaf.ref);
        const storyLeaf = leaf.kind === 'story';
        const r = storyLeaf ? 8 : LEAF_DOT[leaf.priority] || LEAF_DOT.normal;
        const late = model.late(leaf.ref);
        const lg = s(
          'g',
          {
            class: `graph-rd-leaf${sel === leaf.ref ? ' is-sel' : ''}${linked.has(leaf.ref) ? ' is-linked' : ''}${storyLeaf ? ' is-story' : ''}`,
            transform: `translate(${R1(p.x)} ${R1(p.y)})`,
            tabindex: leaf.ref === (sel || rovingStart()) ? '0' : '-1',
            role: 'button',
            'data-ref': leaf.ref,
            'aria-current': sel === leaf.ref ? 'true' : null,
            'aria-label': `${words(model, leaf.ref)} Press Enter to open it.`,
          },
          s('circle', { class: 'graph-rd-hit', r: 13 }),
        );
        if (storyLeaf) lg.append(s('rect', { class: 'graph-rd-square', x: -r, y: -r, width: r * 2, height: r * 2, rx: 3 }), statusMark(leaf.status, 5.2));
        else lg.append(statusMark(leaf.status, r));
        if (late > 0) lg.append(s('circle', { class: 'graph-rd-late', cx: r * 0.8, cy: -r * 0.8, r: 3.2 }));
        lg.append(s('circle', { class: 'graph-rd-ring', r: r + 4 }));
        nodes.append(lg);
      }
    }
    g.append(nodes);

    // The names of the stories go in the room round each one: toward the middle first, then to either
    // side, then outward, wherever a name clears the hub, the other stories and the other names.
    const storyNames = s('g', { class: 'graph-rd-story-names', 'aria-hidden': 'true' });
    const stories = L.groups.filter((gr) => !gr.leafStory).sort((a, b) => Number(linked.has(b.story.ref) || sel === b.story.ref) - Number(linked.has(a.story.ref) || sel === a.story.ref));
    const obstacles = [{ x: 0, y: 0, r: 54 }, ...stories.map((gr) => ({ x: L.pos.get(gr.story.ref).x, y: L.pos.get(gr.story.ref).y, r: 23 }))];
    const taken = [];
    const clear = (b) =>
      [[b[0], b[1]], [b[2], b[1]], [b[0], b[3]], [b[2], b[3]]].every(([x, y]) => Math.hypot(x, y) <= L.R2 - 16) &&
      !taken.some((q) => b[0] < q[2] + 3 && b[2] > q[0] - 3 && b[1] < q[3] + 1 && b[3] > q[1] - 1) &&
      !obstacles.some((c) => Math.hypot(c.x - Math.max(b[0], Math.min(c.x, b[2])), c.y - Math.max(b[1], Math.min(c.y, b[3]))) < c.r);
    for (const gr of stories) {
      const p = L.pos.get(gr.story.ref);
      const title = model.node(gr.story.ref).title;
      let found = null;
      for (const lines of full ? [wrap(title, 13, 3), wrap(title, 10, 3), [clip(title, 10)]] : [wrap(title, 10, 3), [clip(title, 10)]]) {
        const w = Math.max(...lines.map((line) => line.length)) * 6.4;
        const hh = lines.length * 13;
        for (const turn of [0, 0.5, -0.5, 1, -1, 1.5, -1.5, 2.1, -2.1, 2.7, -2.7, Math.PI]) {
          const ux = Math.cos(p.ang + Math.PI + turn);
          const uy = Math.sin(p.ang + Math.PI + turn);
          const reach = (Math.abs(ux) * w) / 2 + (Math.abs(uy) * hh) / 2;
          for (const gap of [3, 10, 20]) {
            const cx = p.x + ux * (21 + gap + reach);
            const cy = p.y + uy * (21 + gap + reach);
            const b = [cx - w / 2, cy - hh / 2, cx + w / 2, cy + hh / 2];
            if (!found && clear(b)) found = { lines, cx, cy, b };
          }
        }
        if (found) break;
      }
      if (!found) continue;
      taken.push(found.b);
      const name = s('g', { class: `graph-rd-story-name${sel === gr.story.ref ? ' is-sel' : ''}${linked.has(gr.story.ref) ? ' is-linked' : ''}`, transform: `translate(${R1(found.cx)} ${R1(found.cy)})` });
      found.lines.forEach((line, i) => name.append(s('text', { x: 0, y: R1((i - (found.lines.length - 1) / 2) * 13 + 4), 'text-anchor': 'middle' }, line)));
      storyNames.append(name);
    }
    g.append(storyNames);

    // Names last, so they sit over the lines and never under a node.
    const labels = s('g', { class: 'graph-rd-labels', 'aria-hidden': 'true' });
    for (const gr of L.groups) {
      for (const leafOf of gr.leaves) {
        const leaf = model.node(leafOf.ref);
        const p = L.pos.get(leaf.ref);
        const deg = (p.ang * 180) / Math.PI;
        const flip = Math.cos(p.ang) < -0.0001;
        const off = (leaf.kind === 'story' ? 8 : LEAF_DOT[leaf.priority] || LEAF_DOT.normal) + 9;
        const text = clip(`${codeOf(leaf)} ${leaf.title}`, full ? labelMax : 14);
        labels.append(
          s(
            'g',
            { class: `graph-rd-label${sel === leaf.ref ? ' is-sel' : ''}${linked.has(leaf.ref) ? ' is-linked' : ''}`, transform: `rotate(${R1(deg)}) translate(${R1(p.r + off)} 0)` },
            s('text', { class: 'graph-rd-label-text', transform: flip ? 'rotate(180)' : null, 'text-anchor': flip ? 'end' : 'start', dy: '0.34em' }, text),
          ),
        );
      }
    }
    g.append(labels);
    put(svg, s('defs', {}, s('marker', { id: 'graph-rd-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 11, markerHeight: 11, orient: 'auto', markerUnits: 'userSpaceOnUse' }, s('path', { class: 'graph-st-arrow', d: 'M0 1 L10 5 L0 9 z' }))), g);
    svg.classList.toggle('has-sel', Boolean(sel));
  };
  const rovingStart = () => L.groups[0]?.story.ref;

  let lastWidth = 0;
  const redraw = () => {
    const width = Math.max(inner.clientWidth || 700, 560);
    const ext0 = epicExtent(L, true);
    const full = Math.min(width / ext0.w, 1.4) >= 0.78;
    const ext = epicExtent(L, full);
    svg.setAttribute('viewBox', `${R1(ext.x)} ${R1(ext.y)} ${R1(ext.w)} ${R1(ext.h)}`);
    drawTree(full);
    const outside = sel ? rows.filter((row) => !row.inside) : [];
    put(
      note,
      outside.length
        ? h(
            'div',
            { class: 'graph-rd-note' },
            h('span', { class: 'quiet' }, `${codeOf(model.node(sel))} ${clip(model.node(sel).title, 40)} is also linked outside this epic:`),
            outside.map((row) => {
              const other = model.epicOf(row.other);
              const found = model.node(row.other);
              return h('button', { class: 'chip graph-x-chip-link', type: 'button', title: `${codeOf(found)} ${found.title}, in ${codeOf(other)} ${other.title}`, onclick: () => api.setParams({ scope: other.ref, focus: row.other }) }, icon('arrow_right', { size: 14 }), code(codeOf(found)), h('span', { class: 'graph-x-chip-name' }, `${clip(found.title, 32)} in ${codeOf(other)} ${clip(other.title, 24)}`));
            }),
          )
        : null,
    );
    wire();
  };
  const showTip = (el) => {
    const ref = el.getAttribute('data-ref');
    const r = el.getBoundingClientRect();
    const c = inner.getBoundingClientRect();
    put(tip, tipContent(model, ref));
    tip.hidden = false;
    tip.style.left = `${Math.round(r.left - c.left + r.width / 2)}px`;
    tip.style.top = `${Math.round(r.top - c.top - 8)}px`;
  };
  const wire = () => {
    // Round the ring in the order of the tree: the epic, its stories, then each story's tasks.
    const order = [epicRef, ...L.groups.flatMap((gr) => (gr.leafStory ? [gr.story.ref] : [gr.story.ref, ...gr.leaves.map((leaf) => leaf.ref)]))];
    for (const el of svg.querySelectorAll('.graph-rd-epic, .graph-rd-story, .graph-rd-leaf')) {
      const ref = el.getAttribute('data-ref');
      opensItsPage(el, ref);
      el.addEventListener('keydown', (e) => {
        if (['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp'].includes(e.key)) {
          const at = order.indexOf(ref);
          const step = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : -1;
          const next = svg.querySelector(`[data-ref="${order[(at + step + order.length) % order.length]}"]`);
          if (next) {
            e.preventDefault();
            el.setAttribute('tabindex', '-1');
            next.setAttribute('tabindex', '0');
            next.focus();
          }
        }
      });
      el.addEventListener('pointerenter', () => showTip(el));
      el.addEventListener('pointerleave', () => { tip.hidden = true; });
      el.addEventListener('focus', () => showTip(el));
      el.addEventListener('blur', () => { tip.hidden = true; });
    }
    let drag = null;
    svg.onpointerdown = (e) => {
      if (e.target.closest('.graph-rd-epic, .graph-rd-story, .graph-rd-leaf') || e.button !== 0) return;
      drag = { x: e.clientX - ring.x, y: e.clientY - ring.y };
      svg.setPointerCapture(e.pointerId);
      svg.classList.add('is-grabbing');
    };
    svg.onpointermove = (e) => {
      if (!drag) return;
      ring.x = e.clientX - drag.x;
      ring.y = e.clientY - drag.y;
      svg.querySelector('.graph-rd-vp').setAttribute('transform', `translate(${R1(ring.x)} ${R1(ring.y)}) scale(${ring.zoom})`);
    };
    svg.onpointerup = () => { drag = null; svg.classList.remove('is-grabbing'); };
  };
  redraw();
  lastWidth = inner.clientWidth;
  // On a small screen the tree is wider than the window: start with its middle in view.
  if (scroller.scrollWidth > scroller.clientWidth) scroller.scrollLeft = (scroller.scrollWidth - scroller.clientWidth) / 2;
  watch(inner, () => {
    const w = inner.clientWidth;
    if (Math.abs(w - lastWidth) > 10) {
      lastWidth = w;
      redraw();
    }
  });
  return main;
}
