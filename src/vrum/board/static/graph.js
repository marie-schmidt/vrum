// The graph view: the same work drawn as dots and lines. It reads what the board has already
// loaded, so it shows the same items the Kanban does, and it keeps no data of its own. The drawing
// is SVG made here; nothing is loaded from the internet.

import { ui } from './ctx.js';
import { announce, clear, h, plural, s } from './dom.js';
import { EDGE, EXTRAS, LINK_SWITCHES, chosenKey, needs, neighboursOf, settings, universe, view } from './graph-data.js';
import { KIND, STATUS, STATUSES } from './labels.js';
import { createLayout } from './layout.js';
import { ensure, store } from './store.js';

const layout = createLayout();
const EXTRA_LABELS = { rules: 'Rules', decisions: 'Decisions and principles', facts: 'Facts', beliefs: 'Beliefs' };
const STATUS_FILL = { new: '#e9e2d2', ready: '#fffdf8', in_progress: '#c8321a', blocked: '#8a5a00', done: '#2f6b4f', dropped: '#cbc2ae', active: '#e9e2d2', held: '#e9e2d2', settled: '#fffdf8', stale: '#8a5a00' };
const KIND_FILL = { epic: '#16130f', story: '#c8321a', task: '#fffdf8', rule: '#e9e2d2', decision: '#f0c9bb', principle: '#f0c9bb', fact: '#dce8df', belief: '#fffdf8' };
const EPIC_FILLS = ['#c8321a', '#2f6b4f', '#8a5a00', '#3b5b92', '#7a3b7a', '#16130f', '#5b8a8a', '#a0522d'];

let root = null;
let params = new URLSearchParams();
let cfg = settings(params);
let all = { dots: new Map(), edges: [] };
let shown = { dots: new Map(), edges: [], collapsed: false, notice: '' };
let parts = {}; // the elements
let zoom = { x: 0, y: 0, k: 1 };
let frame = 0;
let generation = 0;
let lastShape = '';
let dotEls = new Map();
let edgeEls = [];

// -- the page -------------------------------------------------------------------------------------

export function mount(element, initial) {
  root = element;
  params = initial;
  lastShape = '';
  build();
  draw();
}

export function unmount() {
  generation += 1;
  if (frame) cancelAnimationFrame(frame);
  frame = 0;
  root = null;
}

export function update(next) {
  params = next;
  if (root) draw();
}

export function refresh() {
  if (root) draw();
}

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

function build() {
  const scope = h(
    'select',
    { id: 'scope', onchange: () => ui.navigate({ scope: scope.value === 'epic' ? null : scope.value }) },
    h('option', { value: 'epic' }, 'One epic'),
    h('option', { value: 'all' }, 'Everything'),
    h('option', { value: 'local' }, 'Around the chosen item'),
  );
  const epicSelect = h('select', { id: 'graph-epic', onchange: () => ui.navigate({ epic: epicSelect.value || null }) });
  const depth = h(
    'select',
    { id: 'depth', onchange: () => ui.navigate({ depth: depth.value === '1' ? null : depth.value }) },
    [1, 2, 3].map((n) => h('option', { value: n }, `${n} ${n === 1 ? 'step' : 'steps'}`)),
  );
  const colour = h(
    'select',
    { id: 'colour', onchange: () => ui.navigate({ colour: colour.value === 'kind' ? null : colour.value }) },
    h('option', { value: 'kind' }, 'Kind'),
    h('option', { value: 'status' }, 'Status'),
    h('option', { value: 'epic' }, 'Epic'),
  );

  const controls = h(
    'form',
    { class: 'graph-controls', onsubmit: (event) => event.preventDefault(), 'aria-label': 'What the graph shows' },
    h('div', { class: 'field' }, h('label', { for: 'scope' }, 'Show'), scope),
    h('div', { class: 'field', id: 'epic-field' }, h('label', { for: 'graph-epic' }, 'Epic'), epicSelect),
    h('div', { class: 'field', id: 'depth-field' }, h('label', { for: 'depth' }, 'Reach'), depth),
    h('div', { class: 'field' }, h('label', { for: 'colour' }, 'Colour by'), colour),
    h(
      'fieldset',
      {},
      h('legend', {}, 'Also show'),
      EXTRAS.map((name) => check(`show-${name}`, EXTRA_LABELS[name], false, (event) => toggle('show', name, event.target.checked))),
      check('overlaps', 'Suggested overlaps', false, (event) => ui.navigate({ overlaps: event.target.checked ? '1' : null })),
    ),
    h(
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
          ['epic', 'story', 'task'].map((name) => check(`kind-${name}`, KIND[name].label, true, (event) => toggle('hide_kind', name, !event.target.checked))),
        ),
        h(
          'fieldset',
          {},
          h('legend', {}, 'Statuses'),
          STATUSES.map((name) => check(`status-${name}`, STATUS[name].label, true, (event) => toggle('hide_status', name, !event.target.checked))),
        ),
        h(
          'fieldset',
          {},
          h('legend', {}, 'Lines'),
          LINK_SWITCHES.map(([name, label]) => check(`link-${name}`, label, true, (event) => toggle('hide_link', name, !event.target.checked))),
        ),
      ),
    ),
  );

  const svg = s(
    'svg',
    { id: 'graph-svg', role: 'group', 'aria-label': 'The graph. Use the lists beside it to choose items from the keyboard.' },
    s('defs', {}, marker('arrow-needs', '#c8321a'), marker('arrow-sup', '#4a443b')),
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
  const filter = h('input', { id: 'graph-find', type: 'search', 'aria-label': 'Find an item in the list', placeholder: 'Find an item…', autocomplete: 'off', oninput: drawItems });
  parts = {
    scope,
    epicSelect,
    depth,
    colour,
    svg,
    viewport: svg.querySelector('#viewport'),
    edgesG: svg.querySelector('#edges'),
    dotsG: svg.querySelector('#dots'),
    notice: h('p', { class: 'graph-notice', id: 'graph-notice', role: 'status' }),
    chosen: h('section', { id: 'graph-chosen', 'aria-label': 'The chosen item' }),
    neighbours: h('section', { id: 'graph-neighbours', 'aria-label': 'Neighbours of the chosen item' }),
    items: h('ul', { id: 'graph-items', class: 'listbox' }),
    filter,
  };
  clear(root).append(
    h(
      'div',
      { class: 'graph-page' },
      h('div', { class: 'toolbar' }, h('h1', {}, 'Graph')),
      controls,
      parts.notice,
      h(
        'div',
        { class: 'graph-body' },
        stage,
        h(
          'div',
          { class: 'graph-side' },
          parts.chosen,
          parts.neighbours,
          h('section', {}, h('h3', {}, 'Items in view'), h('label', { for: 'graph-find', class: 'sr-only' }, 'Find an item'), filter, parts.items),
          legend(),
        ),
      ),
    ),
  );
}

function marker(id, fill) {
  return s('marker', { id, viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse' }, s('path', { d: 'M0,0 L10,5 L0,10 z', fill }));
}

function legend() {
  const line = (type) => s('svg', { width: 34, height: 10, 'aria-hidden': 'true' }, s('line', { class: 'edge', 'data-type': type, x1: 1, y1: 5, x2: 33, y2: 5 }));
  return h(
    'section',
    { class: 'legend', 'aria-label': 'What the shapes and lines mean' },
    h('span', {}, '■ epic'),
    h('span', {}, '● story'),
    h('span', {}, '◆ task'),
    h('span', {}, '▲ rule'),
    h('span', {}, '⬢ decision or principle'),
    h('span', {}, '▪ fact'),
    h('span', {}, '▢ belief (a suggestion)'),
    ['hierarchy', 'needs_first', 'supersedes', 'duplicates', 'relates_to', 'concerns', 'overlap'].map((type) => h('span', {}, line(type), ` ${EDGE[type].label}`)),
  );
}

// -- working out what to draw ---------------------------------------------------------------------

async function draw() {
  const mine = ++generation;
  cfg = settings(params);
  syncControls();
  for (const name of needs(cfg)) await ensure(name);
  if (mine !== generation || !root) return;
  all = universe(cfg);
  shown = view(cfg, all);
  const dots = [...shown.dots.values()];
  layout.set(dots, shown.edges);
  layout.settle(dots.length > 400 ? 120 : 260);
  render();
  const shape = [cfg.scope, shown.epicId, cfg.depth, chosenKey(cfg), shown.collapsed, dots.length > 0].join('|');
  if (shape !== lastShape) {
    lastShape = shape;
    fit();
  }
  place();
  parts.svg.dataset.ready = 'true';
  if (layout.active()) animate();
  else parts.svg.dataset.settled = 'true';
}

function syncControls() {
  parts.scope.value = cfg.scope;
  const epics = [...store.nodes.values()].filter((node) => node.kind === 'epic').sort((a, b) => a.id - b.id);
  parts.epicSelect.replaceChildren(...epics.map((epic) => h('option', { value: epic.id }, `#${epic.id} ${epic.title}`)));
  parts.epicSelect.value = String(cfg.epic && store.nodes.get(cfg.epic)?.kind === 'epic' ? cfg.epic : (epics[0]?.id ?? ''));
  parts.depth.value = String(cfg.depth);
  parts.colour.value = cfg.colour;
  root.querySelector('#epic-field').hidden = cfg.scope !== 'epic';
  root.querySelector('#depth-field').hidden = cfg.scope !== 'local';
  for (const name of EXTRAS) root.querySelector(`[name="show-${name}"]`).checked = cfg.show.has(name);
  root.querySelector('[name="overlaps"]').checked = cfg.overlaps;
  for (const name of ['epic', 'story', 'task']) root.querySelector(`[name="kind-${name}"]`).checked = !cfg.hideKind.has(name);
  for (const name of STATUSES) root.querySelector(`[name="status-${name}"]`).checked = !cfg.hideStatus.has(name);
  for (const [name] of LINK_SWITCHES) root.querySelector(`[name="link-${name}"]`).checked = !cfg.hideLink.has(name);
  if (cfg.hideKind.size || cfg.hideStatus.size || cfg.hideLink.size) root.querySelector('#filters').open = true;
}

// -- drawing --------------------------------------------------------------------------------------

function fillOf(dot) {
  if (cfg.colour === 'status') return STATUS_FILL[dot.status] || '#fffdf8';
  if (cfg.colour === 'epic') {
    const epics = [...store.nodes.values()].filter((node) => node.kind === 'epic').sort((a, b) => a.id - b.id);
    const at = epics.findIndex((epic) => epic.id === dot.epicId);
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
    case 'principle':
      return s('polygon', { ...base, points: '-10,0 -5,-9 5,-9 10,0 5,9 -5,9', class: `shape ${dot.type}` });
    case 'fact':
      return s('rect', { ...base, x: -7, y: -7, width: 14, height: 14 });
    default:
      return s('rect', { ...base, x: -8, y: -8, width: 16, height: 16, rx: 4, 'stroke-dasharray': '3 2', class: 'shape belief' });
  }
}

function describe(dot) {
  const what = KIND[dot.type]?.label || dot.type;
  const status = STATUS[dot.status]?.label || dot.status;
  const detail = dot.collapsed ? `, with ${plural(dot.count, 'item')} under it` : `. ${status}`;
  return `${what} ${dot.id}: ${dot.title}${detail}${layout.isPinned(dot.key) ? '. Pinned' : ''}`;
}

function edgeWords(edge) {
  if (edge.type === 'collapsed') return `${plural(edge.count, 'link')} between`;
  if (edge.type === 'overlap') return `might be the same, ${edge.similarity.toFixed(2)} (a suggestion from the index)`;
  return EDGE[edge.type].label;
}

function render() {
  const focused = document.activeElement?.dataset?.key;
  const chosen = chosenKey(cfg);
  const dots = [...shown.dots.values()];
  const labelled = dots.length <= 60;
  parts.edgesG.replaceChildren();
  parts.dotsG.replaceChildren();
  edgeEls = shown.edges.map((edge) => {
    const text = edgeWords(edge);
    const attrs = { class: 'edge', 'data-type': edge.type, 'data-source': edge.source, 'data-target': edge.target };
    if (edge.type === 'needs_first') attrs['marker-end'] = 'url(#arrow-needs)';
    if (edge.type === 'supersedes') attrs['marker-end'] = 'url(#arrow-sup)';
    const line = s('line', attrs, s('title', {}, `${shown.dots.get(edge.source).title} ${text} ${shown.dots.get(edge.target).title}`));
    const showLabel = edge.type !== 'hierarchy' && (labelled || edge.source === chosen || edge.target === chosen || edge.type === 'overlap' || edge.type === 'collapsed');
    const label = showLabel ? s('text', { class: 'edge-label', 'text-anchor': 'middle' }, text) : null;
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
        class: `dot type-${dot.type}${dot.key === chosen ? ' chosen' : ''}${dot.outside ? ' outside' : ''}${layout.isPinned(dot.key) ? ' pinned' : ''}`,
        tabindex: 0,
        role: 'button',
        'aria-label': describe(dot),
        'aria-pressed': dot.key === chosen ? 'true' : 'false',
        'data-key': dot.key,
        'data-id': dot.id,
        'data-type': dot.type,
        'data-status': dot.status,
        'data-outside': dot.outside ? 'true' : null,
        'data-collapsed': dot.collapsed ? 'true' : null,
      },
      s('title', {}, describe(dot)),
      shapeOf(dot),
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
  const overlapNote = cfg.overlaps && store.overlapNote ? ` Suggested overlaps are not available: ${store.overlapNote}` : '';
  const counts = shown.collapsed ? '' : `${plural(dots.length, 'dot')}, ${plural(shown.edges.length, 'line')}.`;
  parts.notice.textContent = [shown.notice, counts].filter(Boolean).join(' ') + overlapNote;
  parts.svg.dataset.dots = String(dots.length);
  parts.svg.dataset.collapsed = shown.collapsed ? 'true' : 'false';
  drawLists();
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
    const back = edge.type === 'needs_first' || edge.type === 'supersedes' ? 14 : 0;
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
      drawLists();
    }
  };
  parts.svg.addEventListener('pointermove', move);
  parts.svg.addEventListener('pointerup', up);
  parts.svg.addEventListener('pointercancel', up);
}

function togglePin(key) {
  const pinned = layout.togglePin(key);
  dotEls.get(key)?.classList.toggle('pinned', pinned);
  announce(pinned ? 'Pinned.' : 'Let go.');
  animate();
  drawLists();
}

function releasePins() {
  layout.releaseAll();
  dotEls.forEach((element) => element.classList.remove('pinned'));
  animate();
  drawLists();
}

// -- choosing ---------------------------------------------------------------------------------------

function choose(key) {
  const dot = shown.dots.get(key) || all.dots.get(key);
  if (!dot) return;
  announce(`Chosen: ${dot.title}`);
  if (dot.collapsed) {
    ui.navigate({ scope: null, epic: dot.id, card: dot.id, sel: null, panel: null });
  } else if (dot.isNode) {
    const moveTo = cfg.scope === 'epic' && dot.epicId && dot.epicId !== shown.epicId ? { epic: dot.epicId } : {};
    ui.navigate({ card: dot.id, sel: null, panel: null, ...moveTo });
  } else {
    ui.navigate({ sel: key, card: null });
  }
}

function neighbourButton(entry) {
  const { dot, edge, outgoing } = entry;
  const relation =
    edge.type === 'hierarchy'
      ? outgoing ? 'contains' : 'is in'
      : edge.type === 'needs_first'
        ? outgoing ? 'needs first' : 'is needed first by'
        : edge.type === 'supersedes'
          ? outgoing ? 'supersedes' : 'is superseded by'
          : EDGE[edge.type].label;
  return h(
    'li',
    {},
    h('button', { type: 'button', class: 'neighbour', 'data-key': dot.key, onclick: () => choose(dot.key) }, h('strong', {}, relation), ` ${KIND[dot.type].label} #${dot.id} ${dot.title}`),
  );
}

function drawLists() {
  const key = chosenKey(cfg);
  const dot = key ? all.dots.get(key) : null;
  clear(parts.chosen);
  clear(parts.neighbours);
  if (!dot) {
    parts.chosen.append(h('h3', {}, 'Chosen item'), h('p', { class: 'quiet' }, 'Choose a dot, or an item from the list, to see its neighbours here.'));
  } else {
    const details = [];
    if (dot.text) details.push(h('p', {}, dot.text));
    if (dot.origin) {
      details.push(h('p', { class: 'quiet' }, `Origin: ${dot.origin}. Recorded by ${dot.by}.${dot.type === 'belief' ? ' A suggestion, not a decision.' : ''}${dot.stale ? ' Stale: its evidence changed.' : ''}`));
    }
    if (dot.type === 'decision' || dot.type === 'principle') details.push(h('p', { class: 'quiet' }, `Decided by ${dot.by}.`));
    // A rule, decision, fact or belief has no card: its page is where it can be read in full and changed.
    const pages = {
      rule: ['Rules', `#/rules?rule=${dot.id}`],
      decision: ['Memory', `#/memory?entry=decision:${dot.id}`],
      principle: ['Memory', `#/memory?entry=decision:${dot.id}`],
      fact: ['Memory', `#/memory?entry=fact:${dot.id}`],
      belief: ['Memory', `#/memory?entry=belief:${dot.id}`],
    };
    if (pages[dot.type]) details.push(h('p', {}, h('a', { class: 'open-entry', href: pages[dot.type][1] }, `Open it on the ${pages[dot.type][0]} page`)));
    parts.chosen.append(
      h('h3', {}, 'Chosen item'),
      h('p', {}, h('strong', {}, `${KIND[dot.type].label} #${dot.id}`), ` ${dot.title}`),
      ...details,
      h('button', { type: 'button', id: 'pin', onclick: () => togglePin(dot.key) }, layout.isPinned(dot.key) ? 'Unpin this dot' : 'Pin this dot'),
    );
    const near = neighboursOf(dot.key, all.edges, all.dots);
    parts.neighbours.append(
      h('h3', {}, `Neighbours (${near.length})`),
      near.length ? h('ul', {}, near.map(neighbourButton)) : h('p', { class: 'quiet' }, 'Nothing is connected to it with the lines that are switched on.'),
    );
  }
  drawItems();
}

function drawItems() {
  if (!parts.items) return;
  const source = shown.dots.size || cfg.scope !== 'local' ? shown.dots : all.dots;
  const wanted = parts.filter.value.trim().toLowerCase();
  const chosen = chosenKey(cfg);
  const matching = [...source.values()].filter((dot) => !wanted || `${dot.id} ${dot.title} ${KIND[dot.type].label}`.toLowerCase().includes(wanted));
  const most = 500;
  parts.items.replaceChildren(
    ...matching.slice(0, most).map((dot) =>
      h('li', {}, h('button', { type: 'button', class: 'item', 'data-key': dot.key, 'aria-pressed': dot.key === chosen ? 'true' : 'false', onclick: () => choose(dot.key) }, `${KIND[dot.type].label} #${dot.id} ${dot.title}`)),
    ),
    ...(matching.length > most ? [h('li', { class: 'quiet' }, `${matching.length - most} more: type to narrow the list.`)] : []),
  );
}
