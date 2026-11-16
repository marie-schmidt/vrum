// The Graph page: the project's work and context, as a map or as a list. Work shows the hierarchy
// and the dependencies; Context shows requirements, decisions and facts and the work they concern;
// Everything shows both. The map and the list are two presentations of the same scope, drawn from
// what the board has already loaded (the store), so they show the same records and the same
// relationships and the page keeps no data of its own. Choosing a record, in either one, shows it in
// the shared detail panel. The map is SVG made here (nothing is loaded from the internet): nodes in
// rows with their words inside (layered.js, graph-nodes.js), or, for a scope too big to label, small
// tiles placed by a force layout (layout.js).

import { Refusal } from './api.js';
import { ui } from './ctx.js';
import { announce, clear, h, plural, put, s } from './dom.js';
import { filterBar, loadRows } from './filterbar.js';
import { createContextList } from './context-list.js';
import { openCreate } from './create.js';
import {
  DOT_LIMIT,
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
import { describeNode, drawNode, labelledBox, metricsOf, tileBox } from './graph-nodes.js';
import { icon } from './icons.js';
import { STATUS, STATUSES, kindIcon } from './labels.js';
import { freeRoute, layered, plannedRoute, straightRoute } from './layered.js';
import { createLayout } from './layout.js';
import { openRecord } from './records.js';
import { ensure, epics } from './store.js';
import { createWorkList } from './work-list.js';

const layout = createLayout();

// A map labels every node while it has at most this many, and no more than this many in one row;
// a bigger one draws small tiles and says the words when one is pointed at.
const MOST_LABELLED = 60;
const MOST_ACROSS = 12;
const LEAST_ZOOM = 0.05;
const MOST_ZOOM = 4;

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
let loaded = false; // the first draw has finished
let lastShape = '';
let scrolledTo = null; // the record the list was last brought to
let inList = false; // the list was the last thing drawn
let appliedQ = ''; // the work filter the filter box last took from the address
let problem = ''; // what the server said when something could not be loaded
let dotEls = new Map();
let edgeEls = [];
let mode = 'layered'; // how the map is laid out: in rows with labels, or as tiles
let plan = null; // the rows' plan: where every line runs
let boxes = new Map(); // key -> { w, h, ... }: the size and the words of each node
let metrics = null;
let modeNote = ''; // what the person is told when the map is laid out as tiles
let hovered = null;
let touched = false; // the person moved or zoomed the map since it was last fitted
let resizer = null;

// -- the page -------------------------------------------------------------------------------------

export function mount(element, initial) {
  root = element;
  params = initial;
  cfg = settings(params);
  appliedQ = cfg.q;
  lastShape = '';
  scrolledTo = null;
  settled = false;
  loaded = false;
  hovered = null;
  build();
  draw();
}

export function unmount() {
  generation += 1;
  if (frame) cancelAnimationFrame(frame);
  frame = 0;
  lists?.context.destroy();
  lists = null;
  resizer?.disconnect();
  resizer = null;
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

function check(name, label, checked, onchange, hint) {
  return h('label', { class: 'check', title: hint || null }, h('input', { type: 'checkbox', name, checked, onchange }), label);
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
    { id: group, class: 'segmented', role: 'group', 'aria-label': label },
    names.map(([value, words]) => h('button', { type: 'button', id: `${prefix}-${value}`, 'data-value': value, 'aria-pressed': 'false', onclick: () => change(value) }, words)),
  );
}

const field = (label, control, className = '') => h('div', { class: `gm-field ${className}`.trim() }, h('label', { for: control.id }, label), control);

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
    h('option', { value: '' }, 'Whole scope'),
    [1, 2, 3].map((n) => h('option', { value: n }, `${n} ${n === 1 ? 'step' : 'steps'} around the chosen`)),
  );
  const colour = h(
    'select',
    { id: 'colour', onchange: () => ui.navigate({ colour: colour.value === 'kind' ? null : colour.value }) },
    h('option', { value: 'kind' }, 'Kind'),
    h('option', { value: 'status' }, 'Status'),
    h('option', { value: 'epic' }, 'Epic'),
  );
  bar = filterBar({ value: params.get('q') || '', onSubmit: (text) => ui.navigate({ q: text || null }) });

  const options = h(
    'form',
    { class: 'gm-options', onsubmit: (event) => event.preventDefault(), 'aria-label': 'What the graph shows' },
    field('Reach', reach),
    field('Colour by', colour, 'map-only'),
    h(
      'div',
      { class: 'gm-checks' },
      h('span', { class: 'map-only' }, check('ended', 'Show ended', false, (event) => ui.navigate({ ended: event.target.checked ? '1' : null }), 'Show replaced, withdrawn and dropped records')),
      check('mentions', 'References', false, (event) => ui.navigate({ mentions: event.target.checked ? '1' : null }), 'Draw a line for each [[reference]] written in the text of a record'),
    ),
  );

  const filters = h(
    'details',
    { id: 'filters', class: 'gm-filters' },
    h('summary', {}, icon('filter', { size: 14 }), 'Filters: kinds, statuses and lines'),
    h(
      'div',
      { class: 'gm-filter-sets' },
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
    { id: 'graph-svg', role: 'group', 'aria-label': 'The map. Tab moves between the nodes; Enter chooses one and P pins it. The list shows the same records as text.' },
    s('defs', {}, marker('arrow-needs', 'needs'), marker('arrow-sup', 'sup')),
    s('g', { id: 'viewport' }, s('g', { id: 'labels' }), s('g', { id: 'edges' }), s('g', { id: 'dots' })),
  );
  svg.addEventListener('pointerdown', pointerDown);
  svg.addEventListener(
    'wheel',
    (event) => {
      // Scrolling the page over the map must still scroll the page: only a pinch (or Ctrl or ⌘ with
      // the wheel) zooms.
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      zoomAt(event.clientX, event.clientY, event.deltaY < 0 ? 1.15 : 1 / 1.15);
    },
    { passive: false },
  );
  const canvas = h(
    'div',
    { class: 'gm-canvas', id: 'graph-canvas', tabindex: 0, role: 'region', 'aria-label': 'The map. Arrow keys move it, plus and minus zoom it, 0 fits it.', 'aria-describedby': 'graph-hint' },
    svg,
    h('div', { class: 'gm-tip', id: 'graph-tip', 'aria-hidden': 'true', hidden: true }),
    h('div', { class: 'gm-state gm-empty', id: 'graph-empty', hidden: true }),
    h('p', { class: 'gm-state loading', id: 'graph-loading', role: 'status' }, 'Loading the graph'),
  );
  // The map is fitted again when its room changes (the panel opens, the window is resized), unless
  // the person has moved it: then it stays as they left it.
  if (typeof ResizeObserver !== 'undefined') {
    resizer = new ResizeObserver(() => {
      if (root && cfg.as === 'map' && !touched && canvas.clientWidth) fit();
    });
    resizer.observe(canvas);
  }
  canvas.addEventListener('keydown', canvasKey);
  canvas.addEventListener('focusin', (event) => {
    const dot = event.target.closest?.('.dot');
    if (dot) reveal(dot);
    showTip(dot?.dataset.key || null);
  });
  canvas.addEventListener('focusout', () => showTip(null));
  canvas.addEventListener('pointerover', (event) => {
    hovered = event.target.closest?.('.dot')?.dataset.key || null;
    showTip(hovered);
  });
  canvas.addEventListener('pointerleave', () => {
    hovered = null;
    showTip(null);
  });

  lists = { work: createWorkList(), context: createContextList() };
  const workCount = h('span', { class: 'gv-count', id: 'work-list-count', role: 'status' });
  parts = {
    views,
    presentations,
    epicSelect,
    reach,
    colour,
    svg,
    canvas,
    viewport: svg.querySelector('#viewport'),
    labelsG: svg.querySelector('#labels'),
    edgesG: svg.querySelector('#edges'),
    dotsG: svg.querySelector('#dots'),
    tip: canvas.querySelector('#graph-tip'),
    empty: canvas.querySelector('#graph-empty'),
    loading: canvas.querySelector('#graph-loading'),
    notice: h('div', { class: 'gm-notice', id: 'graph-notice', role: 'status' }),
    scope: h('span', { class: 'gm-scope', id: 'graph-scope', role: 'status' }),
    caption: h('p', { class: 'gm-caption', id: 'graph-caption' }),
    legend: h('ul', { class: 'gm-legend', id: 'graph-legend', 'aria-label': 'What the lines and marks mean' }),
    zoomNote: h('span', { class: 'gm-zoom-note', hidden: true }, ' Zoomed far out: press plus to read the labels.'),
    zoomLevel: h('output', { class: 'gm-zoom-level', id: 'zoom-level', 'aria-label': 'Zoom' }, '100%'),
    release: h('button', { type: 'button', class: 'small', id: 'release', onclick: releasePins }, 'Release pins'),
    chosen: h('section', { class: 'gm-chosen', id: 'graph-chosen', 'aria-label': 'The chosen record' }),
    neighbours: h('section', { class: 'gm-neighbours', id: 'graph-neighbours', 'aria-label': 'What the chosen record is joined to' }),
    map: h('div', { class: 'gm-map', id: 'graph-map' }),
    list: h('div', { class: 'gv-list gm-list', id: 'graph-list' }),
    workCount,
    workSection: h(
      'section',
      { class: 'gv-section gm-work', id: 'work-list-section', 'aria-labelledby': 'work-list-heading' },
      h('header', { class: 'gv-section-head' }, h('h2', { id: 'work-list-heading' }, 'Work'), workCount, h('p', { class: 'quiet gm-section-hint' }, 'Epics, stories and tasks, with what each needs first. Choose one to open it.')),
      lists.work.element,
    ),
    epicSignature: null,
  };
  const stage = h(
    'section',
    { class: 'gm-stage', 'aria-label': 'The map' },
    parts.caption,
    canvas,
    h(
      'div',
      { class: 'gm-foot' },
      parts.legend,
      h(
        'div',
        { class: 'gm-controls' },
        h(
          'div',
          { class: 'gm-zoom', role: 'group', 'aria-label': 'Zoom' },
          h('button', { type: 'button', class: 'ghost icon-only small', 'aria-label': 'Zoom out', onclick: () => zoomAt(null, null, 1 / 1.3) }, icon('normal', { size: 16 })),
          parts.zoomLevel,
          h('button', { type: 'button', class: 'ghost icon-only small', 'aria-label': 'Zoom in', onclick: () => zoomAt(null, null, 1.3) }, icon('plus', { size: 16 })),
        ),
        h('button', { type: 'button', class: 'small', id: 'fit', onclick: () => fit() }, 'Fit'),
        parts.release,
      ),
    ),
    h('p', { class: 'gm-hint quiet', id: 'graph-hint' }, 'Drag the background to move the map. Arrow keys move it; plus and minus zoom; 0 fits it. Drag a node to pin it where you put it.', parts.zoomNote),
  );
  parts.map.append(stage, h('div', { class: 'gm-selected' }, parts.chosen, parts.neighbours));
  parts.list.append(parts.workSection, lists.context.element);
  clear(root).append(
    h(
      'div',
      { class: 'graph-page', id: 'graph-page' },
      h(
        'header',
        { class: 'page-header gm-head' },
        h('div', { class: 'gm-heading' }, h('h1', {}, 'Graph'), h('p', { class: 'gm-subtitle' }, 'Follow the connections. Keep the reason beside the work.')),
        h('button', { type: 'button', class: 'primary', id: 'graph-new-item', onclick: newItem }, icon('plus', { size: 16 }), 'New item'),
      ),
      h('div', { class: 'gm-bar' }, views, field('Epic', epicSelect, 'gm-epic'), h('div', { class: 'gm-view-end' }, parts.scope, presentations)),
      h('div', { class: 'gm-tools' }, h('div', { class: 'gm-filter' }, bar.form, bar.help, bar.problem), options, filters),
      parts.notice,
      parts.map,
      parts.list,
    ),
  );
}

function newItem() {
  if (cfg.epic) openCreate({ kind: 'story', parent: cfg.epic });
  else openCreate({ kind: 'epic' });
}

function marker(id, kind) {
  // In the map's own units, so that an arrow is as big as the node it points at, at every zoom.
  return s('marker', { id, viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '11', markerHeight: '11', markerUnits: 'userSpaceOnUse', orient: 'auto-start-reverse' }, s('path', { d: 'M0,0 L10,5 L0,10 z', class: `arrow arrow-${kind}` }));
}

// What each kind of line is called in the legend, and the order they are listed in. The two kinds
// of context line are one entry.
const LEGEND = [
  ['hierarchy', 'Hierarchy'],
  ['needs_first', 'Needs first'],
  ['concerns', 'Context link'],
  ['supersedes', 'Supersedes'],
  ['replaces', 'Replaces'],
  ['relates_to', 'Relates to'],
  ['duplicates', 'Duplicates'],
  ['mention', 'Reference'],
  ['collapsed', 'Links between epics'],
];

function drawLegend() {
  const present = new Set(drawn.edges.map((edge) => (edge.type === 'placed' ? 'concerns' : edge.type)));
  const sample = (type) => {
    const attrs = { class: 'edge', 'data-type': type, d: 'M1 5 H31' };
    if (type === 'needs_first') attrs['marker-end'] = 'url(#arrow-needs)';
    if (type === 'supersedes' || type === 'replaces') attrs['marker-end'] = 'url(#arrow-sup)';
    return s('svg', { class: 'gm-sample', width: 34, height: 10, 'aria-hidden': 'true' }, s('path', attrs));
  };
  const items = LEGEND.filter(([type]) => present.has(type)).map(([type, words]) => h('li', {}, sample(type), words));
  const dots = [...drawn.dots.values()];
  if (mode === 'layered' && dots.some((dot) => dot.confirmation === 'unconfirmed')) {
    items.push(h('li', {}, s('svg', { class: 'gm-sample', width: 22, height: 14, 'aria-hidden': 'true' }, s('rect', { class: 'sample-shape dashed', x: 1, y: 1, width: 20, height: 12, rx: 3 })), 'Unconfirmed'));
  }
  if (dots.some((dot) => dot.ended || (dot.isNode && dot.status === 'dropped'))) {
    items.push(h('li', {}, s('svg', { class: 'gm-sample', width: 22, height: 14, 'aria-hidden': 'true' }, s('rect', { class: 'sample-shape faded', x: 1, y: 1, width: 20, height: 12, rx: 3 }), s('path', { class: 'sample-strike', d: 'M4 7 H18' })), 'Ended'));
  }
  if (mode === 'compact') {
    for (const type of Object.keys(TYPES)) if (dots.some((dot) => dot.type === type)) items.push(h('li', { class: 'kind' }, kindIcon(type, 14), TYPES[type].label));
  }
  put(parts.legend, items);
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
  modeNote = '';
  if (map) drawMap();
  else drawList();
  drawNotice();
  parts.loading.hidden = true;
  loaded = true;
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

const SCOPE_WORDS = { work: 'Work', context: 'Context', everything: 'Work + context' };

// What the person is told about what is shown: the scope and how much there is in it, anything the
// map had to do about its size, anything that could not be loaded, and a chosen record that the
// scope leaves out.
function drawNotice() {
  const chosen = chosenKey(cfg);
  const missing = chosen && all.dots.has(chosen) && !shown.dots.has(chosen);
  const map = cfg.as === 'map';
  const count = map ? (drawn.collapsed ? all.dots.size : drawn.dots.size) : shown.dots.size;
  put(parts.scope, problem ? 'Selected scope · not loaded' : `Selected scope · ${plural(count, 'record')}`);
  const where = cfg.scope === 'epic' && cfg.epic ? all.dots.get(`node:${cfg.epic}`)?.title || 'This epic' : cfg.scope === 'local' && chosen ? `Around ${all.dots.get(chosen)?.title || 'the chosen record'}` : 'All epics';
  put(parts.caption, h('span', { class: 'gm-where' }, `${where} / ${SCOPE_WORDS[cfg.view]}`), map && !shown.collapsed && drawn.dots.size ? h('span', { class: 'gm-counts' }, `${plural(drawn.dots.size, 'record')} · ${plural(drawn.edges.length, 'line')}`) : null);
  // An empty map says why in the middle of the map, so the notice does not say it twice.
  const words = [map && !drawn.dots.size ? '' : shown.notice, modeNote].filter(Boolean).join(' ');
  parts.notice.classList.toggle('refusal', Boolean(problem));
  put(
    parts.notice,
    words ? h('span', {}, words) : null,
    problem ? h('span', { class: 'gm-problem' }, ' ', problem, ' ', h('button', { type: 'button', class: 'link', id: 'graph-retry', onclick: draw }, 'Try again')) : null,
    missing
      ? [' ', `The chosen ${TYPES[all.dots.get(chosen).type].label.toLowerCase()} is outside this scope. `, h('button', { type: 'button', class: 'link', id: 'show-whole-project', onclick: () => ui.navigate({ epic: null, scope: null, q: null }) }, 'Show the whole project')]
      : null,
  );
  parts.notice.hidden = !parts.notice.hasChildNodes();
}

// What the map says when there is nothing to draw: why, and the next thing to do.
function drawEmpty() {
  const none = cfg.as === 'map' && drawn.dots.size === 0;
  parts.empty.hidden = !none;
  parts.canvas.classList.toggle('is-empty', none);
  if (!none) return;
  const chosen = chosenKey(cfg);
  const button = (words, onclick, primary = false) => h('button', { type: 'button', class: primary ? 'primary' : '', id: 'graph-empty-action', onclick }, words);
  let title;
  let step;
  let action = null;
  if (problem) {
    title = 'The graph could not be loaded';
    step = 'There is nothing to draw until the records load. Check the connection, then try again.';
    action = button('Try again', draw);
  } else if (cfg.scope === 'local' && (!chosen || !all.dots.has(chosen))) {
    title = 'No record chosen';
    step = 'Choose a record in the list, or on the map of the whole scope, to see what is around it.';
    action = button('Show the whole scope', () => ui.navigate({ scope: null }));
  } else if (cfg.q && !shown.dots.size) {
    title = 'Nothing matches the filter';
    step = 'Change the words of the filter, or clear it to see all the work again.';
    action = button('Clear the filter', () => ui.navigate({ q: null }));
  } else if (!all.dots.size) {
    title = cfg.view === 'context' ? 'No context in this project yet' : 'No work in this project yet';
    step = cfg.view === 'context' ? 'Record a fact or a decision from the list, or from the panel of a piece of work. It will appear here, joined to what it concerns.' : 'Create an epic with New item, or ask an agent to make a plan. It will appear here as soon as it exists.';
    action = cfg.view === 'context' ? button('Open the list', () => ui.navigate({ as: 'list' })) : button('New item', newItem, true);
  } else if (all.dots.size > DOT_LIMIT && !showsWork(cfg)) {
    title = 'Too many records to draw at once';
    step = shown.notice;
    action = button('Open the list', () => ui.navigate({ as: 'list' }));
  } else if (!shown.dots.size) {
    title = cfg.view === 'context' ? 'No context in this scope yet' : 'No work in this scope yet';
    step = cfg.scope === 'epic' ? 'Choose another epic, or show the whole project.' : 'Change what is shown, or show the whole project.';
    action = button('Show the whole project', () => ui.navigate({ epic: null, scope: null, q: null }));
  } else {
    title = 'Everything here has ended';
    step = 'Replaced, withdrawn and dropped records are left out of the map. Show them, or open the list.';
    action = button('Show ended', () => ui.navigate({ ended: '1' }));
  }
  put(parts.empty, h('h2', {}, title), h('p', {}, step), action);
}

// -- the list -------------------------------------------------------------------------------------

function drawList() {
  const ctx = { cfg, all, shown };
  parts.workSection.hidden = !showsWork(cfg);
  lists.context.element.hidden = !showsContext(cfg);
  if (showsWork(cfg)) {
    lists.work.draw(ctx);
    const rows = [...shown.dots.values()].filter((dot) => dot.isNode).length;
    parts.workCount.textContent = String(rows);
  }
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

const epicIndex = (dot) => epics().sort((a, b) => a.id - b.id).findIndex((epic) => epic.id === dot.epicId);

// Which way the map is laid out. Few enough nodes, and not too many in a row: rows with every node
// labelled. Otherwise tiles, with the force layout, and a word about it.
function chooseLayout(dots) {
  metrics = metricsOf(parts.svg);
  const info = new Map(dots.map((dot) => [dot.key, describeNode(dot, cfg, epicIndex)]));
  plan = null;
  if (dots.length <= MOST_LABELLED || drawn.collapsed) {
    const labelled = new Map(dots.map((dot) => [dot.key, labelledBox(dot, info.get(dot.key), metrics)]));
    const rows = layered(dots, drawn.edges, (key) => labelled.get(key), { unit: metrics.u });
    if (drawn.collapsed || rows.widest <= MOST_ACROSS) {
      mode = 'layered';
      plan = rows;
      boxes = labelled;
      return;
    }
  }
  mode = 'compact';
  boxes = new Map(dots.map((dot) => [dot.key, tileBox(dot, info.get(dot.key), metrics)]));
  modeNote = `This map is too big to label every record, so each one is a small icon, placed near what it is joined to. Point at one, or Tab to it, to read it; the list shows the same records as text.`;
}

function drawMap() {
  inList = false;
  const dots = [...drawn.dots.values()];
  chooseLayout(dots);
  layout.fix(mode === 'layered');
  layout.set(dots, drawn.edges);
  if (mode === 'layered') layout.assign(plan.positions);
  else layout.settle(dots.length > 400 ? 120 : 260);
  renderMap();
  const shape = [mode, cfg.view, cfg.scope, drawn.epicId, cfg.depth, cfg.q, cfg.scope === 'local' ? chosenKey(cfg) : '', drawn.collapsed, dots.length > 0].join('|');
  if (shape !== lastShape) {
    lastShape = shape;
    fit();
  }
  place();
  drawEmpty();
  drawLegend();
  parts.svg.dataset.ready = 'true';
  parts.svg.dataset.layout = mode;
  if (layout.active()) animate();
  else parts.svg.dataset.settled = 'true';
}

function describe(dot) {
  const detail = dot.collapsed ? `${TYPES[dot.type].label} ${dot.id}: ${dot.title}, with ${plural(dot.count, 'item')} under it` : wordsFor(dot);
  return `${detail}${layout.isPinned(dot.key) ? ' Pinned.' : ''}`;
}

function edgeWords(edge) {
  if (edge.type === 'collapsed') return `${plural(edge.count, 'link')} between`;
  return EDGE[edge.type].label;
}

function dotClass(dot, chosen) {
  const info = boxes.get(dot.key).info;
  return [
    'dot',
    `type-${dot.type}`,
    mode === 'compact' ? 'tile' : null,
    info.fill ? `fill-${info.fill}` : null,
    `kind-${info.tone}`,
    dot.key === chosen ? 'chosen' : null,
    dot.outside ? 'outside' : null,
    layout.isPinned(dot.key) ? 'pinned' : null,
    dot.ended ? 'ended' : null,
    dot.isNode && dot.status === 'dropped' ? 'dropped' : null,
    dot.confirmation || null,
  ]
    .filter(Boolean)
    .join(' ');
}

function renderMap() {
  const focused = document.activeElement?.dataset?.key;
  const chosen = chosenKey(cfg);
  const dots = [...drawn.dots.values()];
  parts.edgesG.replaceChildren();
  parts.dotsG.replaceChildren();
  edgeEls = drawn.edges.map((edge) => {
    const attrs = { class: 'edge', 'data-type': edge.type, 'data-source': edge.source, 'data-target': edge.target };
    if (edge.type === 'needs_first') attrs['marker-end'] = 'url(#arrow-needs)';
    if (edge.type === 'supersedes' || edge.type === 'replaces') attrs['marker-end'] = 'url(#arrow-sup)';
    const line = s('path', attrs, s('title', {}, `${drawn.dots.get(edge.source).title} ${edgeWords(edge)} ${drawn.dots.get(edge.target).title}`));
    parts.edgesG.append(line);
    return { edge, line, label: null };
  });
  dotEls = new Map();
  for (const dot of dots) {
    const element = s(
      'g',
      {
        class: dotClass(dot, chosen),
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
      drawNode(dot, boxes.get(dot.key), metrics),
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
  put(
    parts.labelsG,
    mode === 'layered' && plan ? plan.labels.map((label) => s('text', { class: 'group-label', x: label.x, y: label.y }, label.text)) : null,
  );
  markEdges();
  if (focused) dotEls.get(focused)?.focus();
  parts.svg.dataset.dots = String(dots.length);
  parts.svg.dataset.collapsed = drawn.collapsed ? 'true' : 'false';
  drawSide();
}

const boxOf = (key) => {
  const at = layout.at(key);
  const box = boxes.get(key);
  return at && box ? { x: at.x, y: at.y, w: box.w, h: box.h } : null;
};

const where = (key) => {
  const at = layout.at(key);
  const planned = plan?.positions.get(key);
  return at && planned && Math.abs(at.x - planned.x) < 0.5 && Math.abs(at.y - planned.y) < 0.5;
};

function place() {
  for (const [key, element] of dotEls) {
    const p = layout.at(key);
    if (p) element.setAttribute('transform', `translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})`);
  }
  edgeEls.forEach(({ edge, line, label }, index) => {
    const a = boxOf(edge.source);
    const b = boxOf(edge.target);
    if (!a || !b) return;
    let route;
    if (mode === 'compact') route = straightRoute(a, b);
    else if (plan.routes[index] && where(edge.source) && where(edge.target)) route = plannedRoute(plan.routes[index], a, b);
    else route = freeRoute(a, b);
    line.setAttribute('d', route.d);
    if (label) {
      label.setAttribute('x', route.mid.x.toFixed(1));
      label.setAttribute('y', (route.mid.y - 4).toFixed(1));
    }
  });
  placeTip();
}

// The lines of the chosen record are marked; a line between two epics of a project too big to draw
// says how many links it stands for.
function markEdges() {
  const chosen = chosenKey(cfg);
  parts.edgesG.querySelectorAll('.edge-label').forEach((label) => label.remove());
  for (const entry of edgeEls) {
    const near = entry.edge.source === chosen || entry.edge.target === chosen;
    entry.line.classList.toggle('near', near);
    entry.label = null;
    if (entry.edge.type === 'collapsed') {
      entry.label = s('text', { class: 'edge-label', 'text-anchor': 'middle' }, plural(entry.edge.count, 'link'));
      parts.edgesG.append(entry.label);
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

// -- the words of a tile, when it is pointed at --------------------------------------------------------

function showTip(key) {
  const wanted = mode === 'compact' ? key || hovered || chosenKey(cfg) : null;
  const dot = wanted ? drawn.dots.get(wanted) : null;
  parts.tip.hidden = !dot;
  parts.tip.dataset.key = dot ? wanted : '';
  if (!dot) return;
  put(parts.tip, h('strong', {}, `${TYPES[dot.type].label} ${dot.id}`), ' ', dot.title);
  placeTip();
}

function placeTip() {
  const key = parts.tip.dataset.key;
  const at = key ? layout.at(key) : null;
  if (!at) return;
  const box = boxes.get(key);
  parts.tip.style.left = `${(zoom.x + at.x * zoom.k).toFixed(1)}px`;
  parts.tip.style.top = `${(zoom.y + (at.y - box.h / 2) * zoom.k).toFixed(1)}px`;
}

// -- zoom and pan -----------------------------------------------------------------------------------

function applyZoom() {
  parts.viewport.setAttribute('transform', `translate(${zoom.x.toFixed(1)} ${zoom.y.toFixed(1)}) scale(${zoom.k.toFixed(3)})`);
  parts.zoomLevel.textContent = `${Math.round(zoom.k * 100)}%`;
  parts.zoomNote.hidden = mode !== 'layered' || zoom.k >= 0.5;
  placeTip();
}

// The box that holds every node and label of the map, with room around it.
function extent() {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const [key, box] of boxes) {
    const p = layout.at(key);
    if (!p) continue;
    x0 = Math.min(x0, p.x - box.w / 2);
    x1 = Math.max(x1, p.x + box.w / 2);
    y0 = Math.min(y0, p.y - box.h / 2);
    y1 = Math.max(y1, p.y + box.h / 2);
  }
  for (const label of mode === 'layered' && plan ? plan.labels : []) {
    x0 = Math.min(x0, label.x);
    y0 = Math.min(y0, label.y - 14);
  }
  if (x0 === Infinity) return { x0: -100, y0: -100, x1: 100, y1: 100 };
  const room = 40 * (metrics?.u || 1);
  return { x0: x0 - room, y0: y0 - room * (mode === 'layered' ? 1.5 : 1), x1: x1 + room, y1: y1 + room };
}

function fit() {
  const box = parts.svg.getBoundingClientRect();
  const { x0, y0, x1, y1 } = extent();
  const width = box.width || 800;
  const height = box.height || 500;
  const k = Math.min(Math.max(Math.min(width / (x1 - x0), height / (y1 - y0)), LEAST_ZOOM), 1);
  zoom = { k, x: width / 2 - ((x0 + x1) / 2) * k, y: height / 2 - ((y0 + y1) / 2) * k };
  touched = false;
  applyZoom();
}

function zoomAt(clientX, clientY, factor) {
  const box = parts.svg.getBoundingClientRect();
  const px = clientX === null ? box.width / 2 : clientX - box.left;
  const py = clientY === null ? box.height / 2 : clientY - box.top;
  const k = Math.min(MOST_ZOOM, Math.max(LEAST_ZOOM, zoom.k * factor));
  zoom = { k, x: px - ((px - zoom.x) / zoom.k) * k, y: py - ((py - zoom.y) / zoom.k) * k };
  touched = true;
  applyZoom();
}

// The keyboard moves and zooms the map: arrows (Shift for bigger steps), plus and minus, 0 to fit.
function canvasKey(event) {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  const step = event.shiftKey ? 240 : 60;
  const moves = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
  if (moves[event.key]) {
    zoom = { ...zoom, x: zoom.x + moves[event.key][0], y: zoom.y + moves[event.key][1] };
    touched = true;
    applyZoom();
  } else if (event.key === '+' || event.key === '=') zoomAt(null, null, 1.3);
  else if (event.key === '-' || event.key === '_') zoomAt(null, null, 1 / 1.3);
  else if (event.key === '0') fit();
  else return;
  event.preventDefault();
}

// A node the keyboard reached is brought into view if it is off the edge of the map.
function reveal(element) {
  const room = parts.svg.getBoundingClientRect();
  const node = element.getBoundingClientRect();
  const margin = 24;
  let dx = 0;
  let dy = 0;
  if (node.left < room.left + margin) dx = room.left + margin - node.left;
  else if (node.right > room.right - margin) dx = room.right - margin - node.right;
  if (node.top < room.top + margin) dy = room.top + margin - node.top;
  else if (node.bottom > room.bottom - margin) dy = room.bottom - margin - node.bottom;
  if (!dx && !dy) return;
  zoom = { ...zoom, x: zoom.x + dx, y: zoom.y + dy };
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
      touched = true;
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
  place();
  drawSide();
}

function releasePins() {
  layout.releaseAll();
  // In rows, letting go puts each node back in its row.
  if (mode === 'layered' && plan) layout.assign(plan.positions);
  dotEls.forEach((element, key) => {
    element.classList.remove('pinned');
    element.setAttribute('aria-label', describe(drawn.dots.get(key)));
  });
  animate();
  place();
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
    markEdges();
    place();
    showTip(null);
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
    h(
      'button',
      { type: 'button', class: 'neighbour small', 'data-key': dot.key, 'data-relation': entry.edge.type, onclick: () => choose(dot.key) },
      kindIcon(dot.type, 14),
      h('span', { class: 'neighbour-words' }, h('strong', {}, relationWords(entry)), ` ${TYPES[dot.type].label} #${dot.id} ${dot.title}`),
    ),
  );
}

// Under the map: the chosen record, which the shared panel shows in full, and what it is joined to,
// as buttons that choose the other end. The same things the lines say, as words.
function drawSide() {
  const key = chosenKey(cfg);
  const dot = key ? all.dots.get(key) : null;
  clear(parts.chosen);
  clear(parts.neighbours);
  parts.release.disabled = layout.pinnedCount() === 0;
  if (!dot) {
    parts.chosen.append(h('h2', {}, 'Chosen record'), h('p', { class: 'quiet' }, 'Choose a node to see what it is joined to. It opens in the panel.'));
    return;
  }
  parts.chosen.append(
    h('h2', {}, 'Chosen record'),
    h('p', { class: 'gm-chosen-title' }, kindIcon(dot.type, 14), h('strong', {}, `${TYPES[dot.type].label} #${dot.id}`), ` ${dot.title}`),
    h('button', { type: 'button', class: 'small', id: 'pin', onclick: () => togglePin(dot.key) }, layout.isPinned(dot.key) ? 'Unpin this node' : 'Pin this node in place'),
  );
  const near = neighboursOf(dot.key, all.edges, all.dots);
  parts.neighbours.append(
    h('h2', {}, `Joined to (${near.length})`),
    near.length ? h('ul', { class: 'gm-near' }, near.map(neighbourButton)) : h('p', { class: 'quiet' }, 'Nothing is joined to it with the lines that are switched on.'),
  );
}
