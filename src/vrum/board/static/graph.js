// The Graph page: the project's work and context, as a map of dots and lines or as a list. Work
// shows the hierarchy and the dependencies; Context shows requirements, decisions and facts and the
// work they concern; Everything shows both. The map and the list are two presentations of the
// same scope, drawn from what the board has already loaded (the store), so they show the same
// records and the same relationships and the page keeps no data of its own. Choosing a record, in
// either one, shows it in the shared detail panel. The drawing is SVG made here; nothing is loaded
// from the internet.

import { Refusal } from './api.js';
import { ui } from './ctx.js';
import { announce, clear, h, plural, put, s } from './dom.js';
import { filterBar, loadRows } from './filterbar.js';
import { createContextList } from './context-list.js';
import {
  EDGE,
  LINK_SWITCHES,
  PRESENTATIONS,
  PRESETS,
  PRESET_WORDS,
  TYPES,
  chosenKey,
  drawable,
  needs,
  neighboursOf,
  relationWords,
  settings,
  showsContext,
  showsWork,
  universe,
  view,
  wordsFor,
} from './graph-data.js';
import { STATUS, STATUSES } from './labels.js';
import { createLayout } from './layout.js';
import { openRecord } from './records.js';
import { ensure, epics, store } from './store.js';
import { createWorkList } from './work-list.js';

const layout = createLayout();
const STATUS_FILL = { new: '#e9e2d2', ready: '#fffdf8', in_progress: '#c8321a', blocked: '#8a5a00', done: '#2f6b4f', dropped: '#cbc2ae', held: '#e9e2d2', settled: '#fffdf8', active: '#e9e2d2', superseded: '#cbc2ae', withdrawn: '#cbc2ae' };
const KIND_FILL = { epic: '#16130f', story: '#c8321a', task: '#fffdf8', rule: '#e9e2d2', decision: '#f0c9bb', fact: '#dce8df' };
const EPIC_FILLS = ['#c8321a', '#2f6b4f', '#8a5a00', '#3b5b92', '#7a3b7a', '#16130f', '#5b8a8a', '#a0522d'];

let root = null;
let params = new URLSearchParams();
let cfg = settings(params);
let all = { dots: new Map(), edges: [] }; // everything the preset allows
let shown = { dots: new Map(), edges: [], collapsed: false, notice: '' }; // what the scope keeps
let drawn = shown; // what the map draws of it
let parts = {}; // the elements
let lists = null; // the work list and the context list, kept while the page is open
let bar = null; // the work filter
let zoom = { x: 0, y: 0, k: 1 };
let frame = 0;
let generation = 0;
let settled = false; // a draw has finished and none is under way
let lastShape = '';
let scrolledTo = null; // the record the list was last brought to
let inList = false; // the list was the last thing drawn
let appliedQ = ''; // the work filter the filter box last took from the address
let problem = ''; // what the server said when something could not be loaded
let dotEls = new Map();
let edgeEls = [];

// -- the page -------------------------------------------------------------------------------------

export function mount(element, initial) {
  root = element;
  params = initial;
  cfg = settings(params);
  appliedQ = cfg.q;
  lastShape = '';
  scrolledTo = null;
  settled = false;
  build();
  draw();
}

export function unmount() {
  generation += 1;
  if (frame) cancelAnimationFrame(frame);
  frame = 0;
  lists?.context.destroy();
  lists = null;
  root = null;
}

// Only the choice changed (the address got a new `sel`): mark it again, and draw nothing else.
// Anything else the address holds draws it again.
export function update(next) {
  params = next;
  if (!root) return;
  const before = cfg;
  cfg = settings(params);
  const endedChoice = [before.sel, cfg.sel].some((sel) => sel && all.dots.get(sel.key)?.ended && !cfg.ended);
  if (settled && shapeKey(before) === shapeKey(cfg) && !endedChoice) {
    syncControls();
    markChoice();
  } else {
    draw();
  }
}

export function refresh() {
  if (root && !lists.context.busy()) draw();
}

// What the drawing depends on, apart from the choice (which only matters in the local scope).
const shapeKey = (c) =>
  [c.view, c.as, c.scope, c.epic, c.depth, c.q, c.colour, c.mentions, c.ended, [...c.hideKind].sort(), [...c.hideStatus].sort(), [...c.hideLink].sort(), c.scope === 'local' ? c.sel?.key : ''].join('|');

function check(name, label, checked, onchange) {
  return h('label', { class: 'check' }, h('input', { type: 'checkbox', name, checked, onchange }), ` ${label}`);
}

function listOf(name) {
  return new Set((params.get(name) || '').split(',').filter(Boolean));
}

function toggle(name, value, on) {
  const now = listOf(name);
  if (on) now.add(value);
  else now.delete(value);
  ui.navigate({ [name]: [...now].sort().join(',') || null });
}

// A set of buttons of which one is chosen, in the address: they are plain buttons, so the keyboard
// works as it does for any button. The group is `group`, and each button is `${prefix}-${value}`.
function segmented(group, prefix, label, names, change) {
  return h(
    'div',
    { id: group, class: 'gv-seg', role: 'group', 'aria-label': label },
    names.map(([value, words]) => h('button', { type: 'button', id: `${prefix}-${value}`, 'data-value': value, 'aria-pressed': 'false', onclick: () => change(value) }, words)),
  );
}

function build() {
  const views = segmented('graph-views', 'graph-view', 'What to show', PRESETS.map((name) => [name, PRESET_WORDS[name]]), (value) => ui.navigate({ view: value }));
  const presentations = segmented('graph-presentations', 'graph-as', 'How to show it', PRESENTATIONS.map((name) => [name, name === 'map' ? 'Map' : 'List']), (value) => ui.navigate({ as: value }));

  const epicSelect = h('select', { id: 'graph-epic', onchange: () => ui.navigate({ epic: epicSelect.value || null, scope: null }) });
  const reach = h(
    'select',
    {
      id: 'graph-reach',
      onchange: () => ui.navigate({ scope: reach.value ? 'local' : null, depth: reach.value && reach.value !== '1' ? reach.value : null }),
    },
    h('option', { value: '' }, 'The whole scope'),
    [1, 2, 3].map((n) => h('option', { value: n }, `${n} ${n === 1 ? 'step' : 'steps'} around the chosen record`)),
  );
  const colour = h(
    'select',
    { id: 'colour', onchange: () => ui.navigate({ colour: colour.value === 'kind' ? null : colour.value }) },
    h('option', { value: 'kind' }, 'Kind'),
    h('option', { value: 'status' }, 'Status'),
    h('option', { value: 'epic' }, 'Epic'),
  );
  bar = filterBar({ value: params.get('q') || '', onSubmit: (text) => ui.navigate({ q: text || null }) });

  const controls = h(
    'form',
    { class: 'graph-controls', onsubmit: (event) => event.preventDefault(), 'aria-label': 'What the graph shows' },
    h('div', { class: 'field' }, h('label', { for: 'graph-epic' }, 'Epic'), epicSelect),
    h('div', { class: 'field' }, h('label', { for: 'graph-reach' }, 'Reach'), reach),
    h('div', { class: 'field map-only' }, h('label', { for: 'colour' }, 'Colour by'), colour),
    h(
      'fieldset',
      {},
      h('legend', {}, 'Also'),
      h('span', { class: 'map-only' }, check('ended', 'Show replaced, withdrawn and dropped', false, (event) => ui.navigate({ ended: event.target.checked ? '1' : null }))),
      check('mentions', 'References: show [[references]] written in the text', false, (event) => ui.navigate({ mentions: event.target.checked ? '1' : null })),
    ),
  );
  const filters = h(
    'details',
    { id: 'filters', class: 'filters' },
    h('summary', {}, 'Filters: kinds, statuses and lines'),
    h(
      'div',
      { class: 'graph-controls' },
      h(
        'fieldset',
        {},
        h('legend', {}, 'Kinds'),
        Object.entries(TYPES).map(([name, type]) => check(`kind-${name}`, type.label, true, (event) => toggle('hide_kind', name, !event.target.checked))),
      ),
      h(
        'fieldset',
        {},
        h('legend', {}, 'Work statuses'),
        STATUSES.map((name) => check(`status-${name}`, STATUS[name].label, true, (event) => toggle('hide_status', name, !event.target.checked))),
      ),
      h(
        'fieldset',
        {},
        h('legend', {}, 'Lines'),
        LINK_SWITCHES.map(([name, label]) => check(`link-${name}`, label, true, (event) => toggle('hide_link', name, !event.target.checked))),
      ),
    ),
  );

  const svg = s(
    'svg',
    { id: 'graph-svg', role: 'group', 'aria-label': 'The map. Tab moves between the dots; Enter chooses one and P pins it. The list shows the same records as text.' },
    s('defs', {}, marker('arrow-needs', 'needs'), marker('arrow-sup', 'sup')),
    s('g', { id: 'viewport' }, s('g', { id: 'edges' }), s('g', { id: 'dots' })),
  );
  svg.addEventListener('pointerdown', pointerDown);
  svg.addEventListener(
    'wheel',
    (event) => {
      event.preventDefault();
      zoomAt(event.clientX, event.clientY, event.deltaY < 0 ? 1.15 : 1 / 1.15);
    },
    { passive: false },
  );
  const stage = h(
    'div',
    { class: 'graph-stage' },
    svg,
    h(
      'div',
      { class: 'zoom' },
      h('button', { type: 'button', 'aria-label': 'Zoom in', onclick: () => zoomAt(null, null, 1.3) }, '+'),
      h('button', { type: 'button', 'aria-label': 'Zoom out', onclick: () => zoomAt(null, null, 1 / 1.3) }, '−'),
      h('button', { type: 'button', id: 'fit', onclick: () => fit() }, 'Fit'),
      h('button', { type: 'button', id: 'release', onclick: releasePins }, 'Release pins'),
    ),
  );

  lists = { work: createWorkList(), context: createContextList() };
  parts = {
    views,
    presentations,
    epicSelect,
    reach,
    colour,
    svg,
    viewport: svg.querySelector('#viewport'),
    edgesG: svg.querySelector('#edges'),
    dotsG: svg.querySelector('#dots'),
    notice: h('p', { class: 'graph-notice', id: 'graph-notice', role: 'status' }),
    chosen: h('section', { id: 'graph-chosen', 'aria-label': 'The chosen record' }),
    neighbours: h('section', { id: 'graph-neighbours', 'aria-label': 'What the chosen record is joined to' }),
    map: h('div', { class: 'graph-body', id: 'graph-map' }),
    list: h('div', { class: 'gv-list', id: 'graph-list' }),
    workSection: h('section', { class: 'gv-section', id: 'work-list-section', 'aria-labelledby': 'work-list-heading' }, h('header', { class: 'gv-section-head' }, h('h2', { id: 'work-list-heading' }, 'Work')), lists.work.element),
    epicSignature: '',
  };
  parts.map.append(stage, h('div', { class: 'graph-side' }, parts.chosen, parts.neighbours, legend()));
  parts.list.append(parts.workSection, lists.context.element);
  clear(root).append(
    h(
      'div',
      { class: 'graph-page', id: 'graph-page' },
      h(
        'div',
        { class: 'toolbar' },
        h('h1', {}, 'Graph'),
        views,
        presentations,
        h('p', { class: 'help' }, 'Work is the epics, stories and tasks and what needs what. Context is the requirements, decisions and facts and the work they concern. Everything is both. The map draws them as dots and lines; the list writes the same thing out, to read and to change.'),
      ),
      controls,
      h('div', { class: 'gv-work-filter' }, bar.form, bar.help, bar.problem),
      filters,
      parts.notice,
      parts.map,
      parts.list,
    ),
  );
}

function marker(id, kind) {
  return s('marker', { id, viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse' }, s('path', { d: 'M0,0 L10,5 L0,10 z', class: `arrow arrow-${kind}` }));
}

function legend() {
  const line = (type) => s('svg', { width: 34, height: 10, 'aria-hidden': 'true' }, s('line', { class: 'edge', 'data-type': type, x1: 1, y1: 5, x2: 33, y2: 5 }));
  return h(
    'section',
    { class: 'legend', 'aria-label': 'What the shapes and lines mean' },
    Object.values(TYPES).map((type) => h('span', {}, `${type.sign} ${type.label.toLowerCase()}`)),
    h('span', {}, '✓ confirmed by a person'),
    h('span', {}, '┄ dashed outline: unconfirmed'),
    h('span', {}, '✕ crossed out: replaced, withdrawn or dropped'),
    ['hierarchy', 'needs_first', 'supersedes', 'duplicates', 'relates_to', 'placed', 'concerns', 'replaces', 'mention'].map((type) => h('span', {}, line(type), ` ${EDGE[type].label}`)),
  );
}

// -- working out what to draw ---------------------------------------------------------------------

// The work the filter asks for, as the server reads it, or null for no filter (or one it could not
// read: its words are under the filter box).
async function matchedWork(text) {
  if (!text) {
    bar.setProblem('');
    return null;
  }
  const rows = await loadRows(text, bar);
  return rows ? new Set(rows.map((row) => row.id)) : null;
}

async function draw() {
  const mine = ++generation;
  settled = false;
  cfg = settings(params);
  syncControls();
  problem = '';
  try {
    for (const name of needs(cfg)) await ensure(name);
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    problem = error.message;
  }
  const matched = await matchedWork(cfg.q);
  if (mine !== generation || !root) return;
  all = universe(cfg);
  shown = view(cfg, all, matched);
  drawn = drawable(cfg, shown);
  const map = cfg.as === 'map';
  parts.map.hidden = !map;
  parts.list.hidden = map;
  if (map) drawMap();
  else drawList();
  drawNotice();
  settled = true;
}

function syncControls() {
  for (const group of [parts.views, parts.presentations]) {
    const current = group === parts.views ? cfg.view : cfg.as;
    for (const button of group.querySelectorAll('button')) button.setAttribute('aria-pressed', String(button.dataset.value === current));
  }
  const choices = epics().sort((a, b) => a.id - b.id);
  const signature = choices.map((epic) => `${epic.id}:${epic.title}`).join('|');
  if (signature !== parts.epicSignature) {
    parts.epicSignature = signature;
    parts.epicSelect.replaceChildren(h('option', { value: '' }, 'All epics'), ...choices.map((epic) => h('option', { value: epic.id }, `#${epic.id} ${epic.title}`)));
  }
  parts.epicSelect.value = cfg.epic ? String(cfg.epic) : '';
  parts.reach.value = cfg.scope === 'local' ? String(cfg.depth) : '';
  parts.colour.value = cfg.colour;
  // The filter box takes what the address says only when the address changed, so that words
  // typed and not yet applied are still there after switching preset or presentation.
  if (cfg.q !== appliedQ) {
    appliedQ = cfg.q;
    bar.setValue(cfg.q);
  }
  root.querySelector('[name="ended"]').checked = cfg.ended;
  root.querySelector('[name="mentions"]').checked = cfg.mentions;
  for (const name of Object.keys(TYPES)) root.querySelector(`[name="kind-${name}"]`).checked = !cfg.hideKind.has(name);
  for (const name of STATUSES) root.querySelector(`[name="status-${name}"]`).checked = !cfg.hideStatus.has(name);
  for (const [name] of LINK_SWITCHES) root.querySelector(`[name="link-${name}"]`).checked = !cfg.hideLink.has(name);
  if (cfg.hideKind.size || cfg.hideStatus.size || cfg.hideLink.size) root.querySelector('#filters').open = true;
  for (const element of root.querySelectorAll('.map-only')) element.hidden = cfg.as !== 'map';
  const page = root.querySelector('#graph-page');
  page.dataset.view = cfg.view;
  page.dataset.as = cfg.as;
}

// What the person is told about what is shown: what the scope says, how much there is, anything
// that could not be loaded, and a chosen record that the scope leaves out.
function drawNotice() {
  const chosen = chosenKey(cfg);
  const missing = chosen && all.dots.has(chosen) && !shown.dots.has(chosen);
  const counts = cfg.as === 'map' && !shown.collapsed ? `${plural(drawn.dots.size, 'dot')}, ${plural(drawn.edges.length, 'line')}.` : '';
  put(
    parts.notice,
    [shown.notice, counts, problem].filter(Boolean).join(' '),
    missing
      ? [' ', `The chosen ${TYPES[all.dots.get(chosen).type].label.toLowerCase()} is outside this scope. `, h('button', { type: 'button', class: 'link', id: 'show-whole-project', onclick: () => ui.navigate({ epic: null, scope: null, q: null }) }, 'Show the whole project')]
      : null,
  );
}

// -- the list -------------------------------------------------------------------------------------

function drawList() {
  const ctx = { cfg, all, shown };
  parts.workSection.hidden = !showsWork(cfg);
  lists.context.element.hidden = !showsContext(cfg);
  if (showsWork(cfg)) lists.work.draw(ctx);
  if (showsContext(cfg)) lists.context.draw(ctx);
  // Coming to the list from the map, the chosen record is brought into view again.
  if (!inList) scrolledTo = null;
  inList = true;
  scrollToChoice();
}

// Brings the chosen record into view once, when it is a new choice, not on every refresh.
function scrollToChoice() {
  const key = chosenKey(cfg);
  if (key === scrolledTo) return;
  scrolledTo = key;
  const item = key ? root.querySelector(`#graph-list [data-key="${key}"]`) : null;
  item?.scrollIntoView({ block: 'nearest' });
}

// -- the map --------------------------------------------------------------------------------------

function drawMap() {
  inList = false;
  const dots = [...drawn.dots.values()];
  layout.set(dots, drawn.edges);
  layout.settle(dots.length > 400 ? 120 : 260);
  renderMap();
  const shape = [cfg.view, cfg.scope, drawn.epicId, cfg.depth, cfg.q, cfg.scope === 'local' ? chosenKey(cfg) : '', drawn.collapsed, dots.length > 0].join('|');
  if (shape !== lastShape) {
    lastShape = shape;
    fit();
  }
  place();
  parts.svg.dataset.ready = 'true';
  if (layout.active()) animate();
  else parts.svg.dataset.settled = 'true';
}

function fillOf(dot) {
  if (cfg.colour === 'status') {
    if (dot.type === 'decision' || dot.type === 'fact') return dot.ended ? STATUS_FILL.withdrawn : dot.confirmation === 'confirmed' ? KIND_FILL.fact : '#fffdf8';
    return STATUS_FILL[dot.status] || '#fffdf8';
  }
  if (cfg.colour === 'epic') {
    const choices = epics().sort((a, b) => a.id - b.id);
    const at = choices.findIndex((epic) => epic.id === dot.epicId);
    return at < 0 ? '#e9e2d2' : EPIC_FILLS[at % EPIC_FILLS.length];
  }
  return KIND_FILL[dot.type] || '#fffdf8';
}

function shapeOf(dot) {
  const base = { class: 'shape', fill: fillOf(dot) };
  if (dot.collapsed) return s('rect', { ...base, x: -20, y: -20, width: 40, height: 40, rx: 5 });
  switch (dot.type) {
    case 'epic':
      return s('rect', { ...base, x: -12, y: -12, width: 24, height: 24, rx: 3 });
    case 'story':
      return s('circle', { ...base, r: 10 });
    case 'task':
      return s('polygon', { ...base, points: '0,-8 8,0 0,8 -8,0' });
    case 'rule':
      return s('polygon', { ...base, points: '0,-10 10,8 -10,8' });
    case 'decision':
      return s('polygon', { ...base, points: '-10,0 -5,-9 5,-9 10,0 5,9 -5,9' });
    default:
      // A fact is a page with a folded corner.
      return s('polygon', { ...base, points: '-7,-8 3,-8 8,-3 8,8 -7,8' });
  }
}

function describe(dot) {
  const detail = dot.collapsed ? `${TYPES[dot.type].label} ${dot.id}: ${dot.title}, with ${plural(dot.count, 'item')} under it` : wordsFor(dot);
  return `${detail}${layout.isPinned(dot.key) ? ' Pinned.' : ''}`;
}

function edgeWords(edge) {
  if (edge.type === 'collapsed') return `${plural(edge.count, 'link')} between`;
  return EDGE[edge.type].label;
}

// The marks that say more than the shape does, so that nothing is told by colour alone: a tick for
// a record a person confirmed, a cross for one that has ended.
function marks(dot) {
  const found = [];
  if (dot.confirmation === 'confirmed') found.push(s('text', { class: 'mark confirmed-mark', x: 8, y: -7 }, '✓'));
  if (dot.ended) found.push(s('path', { class: 'mark ended-mark', d: 'M-9,-9 L9,9 M9,-9 L-9,9' }));
  return found;
}

function renderMap() {
  const focused = document.activeElement?.dataset?.key;
  const chosen = chosenKey(cfg);
  const dots = [...drawn.dots.values()];
  const labelled = dots.length <= 60;
  parts.edgesG.replaceChildren();
  parts.dotsG.replaceChildren();
  const quietLabels = new Set(['concerns', 'placed', 'mention', 'hierarchy']);
  edgeEls = drawn.edges.map((edge) => {
    const text = edgeWords(edge);
    const attrs = { class: 'edge', 'data-type': edge.type, 'data-source': edge.source, 'data-target': edge.target };
    if (edge.type === 'needs_first') attrs['marker-end'] = 'url(#arrow-needs)';
    if (edge.type === 'supersedes' || edge.type === 'replaces') attrs['marker-end'] = 'url(#arrow-sup)';
    const line = s('line', attrs, s('title', {}, `${drawn.dots.get(edge.source).title} ${text} ${drawn.dots.get(edge.target).title}`));
    const near = edge.source === chosen || edge.target === chosen;
    const showLabel = edge.type === 'collapsed' || near || (labelled && !quietLabels.has(edge.type));
    const label = showLabel && edge.type !== 'hierarchy' ? s('text', { class: 'edge-label', 'text-anchor': 'middle' }, text) : null;
    parts.edgesG.append(line);
    if (label) parts.edgesG.append(label);
    return { edge, line, label };
  });
  dotEls = new Map();
  for (const dot of dots) {
    const words = dot.title.length > 24 ? `${dot.title.slice(0, 23)}…` : dot.title;
    const element = s(
      'g',
      {
        class: `dot type-${dot.type}${dot.key === chosen ? ' chosen' : ''}${dot.outside ? ' outside' : ''}${layout.isPinned(dot.key) ? ' pinned' : ''}${dot.ended ? ' ended' : ''}${dot.confirmation ? ` ${dot.confirmation}` : ''}`,
        tabindex: 0,
        role: 'button',
        'aria-label': describe(dot),
        'aria-pressed': dot.key === chosen ? 'true' : 'false',
        'data-key': dot.key,
        'data-kind': dot.kind,
        'data-type': dot.type,
        'data-id': dot.id,
        'data-status': dot.status,
        'data-confirmation': dot.confirmation || null,
        'data-outside': dot.outside ? 'true' : null,
        'data-collapsed': dot.collapsed ? 'true' : null,
      },
      s('title', {}, describe(dot)),
      shapeOf(dot),
      ...marks(dot),
      labelled || dot.collapsed || dot.key === chosen ? s('text', { y: dot.collapsed ? 34 : 24, 'text-anchor': 'middle' }, dot.collapsed ? `${dot.title} (${dot.count})` : words) : null,
    );
    element.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        choose(dot.key);
      } else if (event.key === 'p' || event.key === 'P') {
        event.preventDefault();
        togglePin(dot.key);
      }
    });
    element.addEventListener('dblclick', () => togglePin(dot.key));
    dotEls.set(dot.key, element);
    parts.dotsG.append(element);
  }
  if (focused) dotEls.get(focused)?.focus();
  parts.svg.dataset.dots = String(dots.length);
  parts.svg.dataset.collapsed = drawn.collapsed ? 'true' : 'false';
  drawSide();
}

function place() {
  for (const [key, element] of dotEls) {
    const p = layout.at(key);
    if (p) element.setAttribute('transform', `translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})`);
  }
  for (const { edge, line, label } of edgeEls) {
    const a = layout.at(edge.source);
    const b = layout.at(edge.target);
    if (!a || !b) continue;
    const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    const back = ['needs_first', 'supersedes', 'replaces'].includes(edge.type) ? 14 : 0;
    line.setAttribute('x1', a.x.toFixed(1));
    line.setAttribute('y1', a.y.toFixed(1));
    line.setAttribute('x2', (b.x - ((b.x - a.x) / length) * back).toFixed(1));
    line.setAttribute('y2', (b.y - ((b.y - a.y) / length) * back).toFixed(1));
    if (label) {
      label.setAttribute('x', ((a.x + b.x) / 2).toFixed(1));
      label.setAttribute('y', ((a.y + b.y) / 2 - 3).toFixed(1));
    }
  }
}

function animate() {
  if (frame || !root) return;
  parts.svg.dataset.settled = 'false';
  const tick = () => {
    frame = 0;
    if (!root) return;
    const moving = layout.step();
    place();
    if (moving) frame = requestAnimationFrame(tick);
    else parts.svg.dataset.settled = 'true';
  };
  frame = requestAnimationFrame(tick);
}

// -- zoom and pan -----------------------------------------------------------------------------------

function applyZoom() {
  parts.viewport.setAttribute('transform', `translate(${zoom.x.toFixed(1)} ${zoom.y.toFixed(1)}) scale(${zoom.k.toFixed(3)})`);
}

function fit() {
  const box = parts.svg.getBoundingClientRect();
  const { x0, y0, x1, y1 } = layout.bounds();
  const width = box.width || 800;
  const height = box.height || 500;
  const k = Math.min(width / (x1 - x0 + 140), height / (y1 - y0 + 140), 1.6);
  zoom = { k, x: width / 2 - ((x0 + x1) / 2) * k, y: height / 2 - ((y0 + y1) / 2) * k };
  applyZoom();
}

function zoomAt(clientX, clientY, factor) {
  const box = parts.svg.getBoundingClientRect();
  const px = clientX === null ? box.width / 2 : clientX - box.left;
  const py = clientY === null ? box.height / 2 : clientY - box.top;
  const k = Math.min(4, Math.max(0.1, zoom.k * factor));
  zoom = { k, x: px - ((px - zoom.x) / zoom.k) * k, y: py - ((py - zoom.y) / zoom.k) * k };
  applyZoom();
}

function toGraph(clientX, clientY) {
  const box = parts.svg.getBoundingClientRect();
  return { x: (clientX - box.left - zoom.x) / zoom.k, y: (clientY - box.top - zoom.y) / zoom.k };
}

function pointerDown(event) {
  if (event.button !== 0) return;
  const target = event.target.closest('.dot');
  const start = { x: event.clientX, y: event.clientY };
  const origin = { ...zoom };
  let moved = false;
  parts.svg.setPointerCapture(event.pointerId);
  const move = (next) => {
    if (!moved && Math.hypot(next.clientX - start.x, next.clientY - start.y) < 4) return;
    moved = true;
    if (target) {
      const at = toGraph(next.clientX, next.clientY);
      layout.move(target.dataset.key, at.x, at.y);
      target.classList.add('pinned');
      animate();
      place();
    } else {
      zoom = { ...origin, x: origin.x + next.clientX - start.x, y: origin.y + next.clientY - start.y };
      applyZoom();
    }
  };
  const up = () => {
    parts.svg.removeEventListener('pointermove', move);
    parts.svg.removeEventListener('pointerup', up);
    parts.svg.removeEventListener('pointercancel', up);
    if (target && !moved) {
      choose(target.dataset.key);
    } else if (target && moved) {
      announce('Pinned where you put it. Double-click it, or press P on it, to let it go.');
      drawSide();
    }
  };
  parts.svg.addEventListener('pointermove', move);
  parts.svg.addEventListener('pointerup', up);
  parts.svg.addEventListener('pointercancel', up);
}

function togglePin(key) {
  const pinned = layout.togglePin(key);
  const dot = drawn.dots.get(key);
  dotEls.get(key)?.classList.toggle('pinned', pinned);
  if (dot) dotEls.get(key)?.setAttribute('aria-label', describe(dot));
  announce(pinned ? 'Pinned.' : 'Let go.');
  animate();
  drawSide();
}

function releasePins() {
  layout.releaseAll();
  dotEls.forEach((element, key) => {
    element.classList.remove('pinned');
    element.setAttribute('aria-label', describe(drawn.dots.get(key)));
  });
  animate();
  drawSide();
}

// -- choosing ---------------------------------------------------------------------------------------

function choose(key) {
  const dot = drawn.dots.get(key) || all.dots.get(key);
  if (!dot) return;
  announce(`Chosen: ${dot.title}`);
  if (dot.collapsed) {
    ui.navigate({ scope: null, epic: dot.id, sel: key, panel: null });
  } else if (cfg.scope === 'epic' && dot.epicId && dot.epicId !== shown.epicId) {
    // A dot of another epic, drawn because it is joined to this one: show that epic.
    ui.navigate({ epic: dot.epicId, sel: key, panel: null });
  } else {
    openRecord(dot.kind, dot.id);
  }
}

// Marks the chosen record again, in whichever presentation is showing, without drawing anything.
function markChoice() {
  const key = chosenKey(cfg);
  if (cfg.as === 'map') {
    for (const [dotKey, element] of dotEls) {
      element.classList.toggle('chosen', dotKey === key);
      element.setAttribute('aria-pressed', String(dotKey === key));
    }
    drawSide();
  } else {
    lists.work.select(key);
    lists.context.select(key);
    scrollToChoice();
  }
  drawNotice();
}

function neighbourButton(entry) {
  const { dot } = entry;
  return h(
    'li',
    {},
    h('button', { type: 'button', class: 'neighbour', 'data-key': dot.key, 'data-relation': entry.edge.type, onclick: () => choose(dot.key) }, h('strong', {}, relationWords(entry)), ` ${TYPES[dot.type].label} #${dot.id} ${dot.title}`),
  );
}

// Beside the map: the chosen record, which the shared panel shows in full, and what it is joined
// to, as buttons that choose the other end.
function drawSide() {
  const key = chosenKey(cfg);
  const dot = key ? all.dots.get(key) : null;
  clear(parts.chosen);
  clear(parts.neighbours);
  if (!dot) {
    parts.chosen.append(h('h3', {}, 'Chosen record'), h('p', { class: 'quiet' }, 'Choose a dot to see what it is joined to. It opens in the panel.'));
    return;
  }
  parts.chosen.append(
    h('h3', {}, 'Chosen record'),
    h('p', {}, h('strong', {}, `${TYPES[dot.type].label} #${dot.id}`), ` ${dot.title}`),
    h('button', { type: 'button', id: 'pin', onclick: () => togglePin(dot.key) }, layout.isPinned(dot.key) ? 'Unpin this dot' : 'Pin this dot'),
  );
  const near = neighboursOf(dot.key, all.edges, all.dots);
  parts.neighbours.append(
    h('h3', {}, `Joined to (${near.length})`),
    near.length ? h('ul', {}, near.map(neighbourButton)) : h('p', { class: 'quiet' }, 'Nothing is joined to it with the lines that are switched on.'),
  );
}
