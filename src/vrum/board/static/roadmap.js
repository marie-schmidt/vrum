// The Roadmap: the work as bars on a timeline, from start date to target date. Dragging a bar moves
// both dates and dragging an end moves one; a bar can also be moved with the keyboard (the arrow
// keys, a week with Shift) and its dates are fields on its card. Every change is update_node, or
// update_nodes for several, with the version the bar was drawn at, so the server decides and a bar
// that was changed meanwhile is refused and goes back. `needs_first` links are arrows from what
// is needed to what waits. Items with no dates wait in a tray above, and dragging one onto the
// timeline gives it dates.

import { Refusal, call } from './api.js';
import { actor, notify, ui } from './ctx.js';
import { dayOfMonth, fromDay, monthName, monthOf, monthStart, nextMonth, toDay, todayDay, weekday, yearOf } from './dates.js';
import { h, plural, put, s } from './dom.js';
import { filterBar, loadRows } from './filterbar.js';
import { icon } from './icons.js';
import { KIND, PRIORITIES, PRIORITY, STATUS, STATUSES, byRef, codeOf, kindIcon, nodeCode, parseNodeRef, statusIcon } from './labels.js';
import { epicOf, store, sync } from './store.js';

const ROW = 30;
const MIN_ROWS = 4; // an empty timeline keeps this many rows, so there is room to drop an item
const SCALE = 56;
const MIN_BAR = 8;
const SHORT_BAR = 26; // a bar narrower than this has no room for its icon and words
const NEW_LENGTH_DAYS = 7; // an item dropped on the timeline gets a week
const ZOOMS = { month: { ppd: 10, label: 'Month' }, quarter: { ppd: 4, label: 'Quarter' }, year: { ppd: 1.2, label: 'Year' } };
const GROUPS = [['epic', 'Epic'], ['status', 'Status'], ['priority', 'Priority'], ['claimed', 'Who holds it']];

let root = null;
let params = new URLSearchParams();
let bar = null; // the filter box
let rows = []; // what the filter found
let picked = new Set(); // refs of the bars chosen to be moved together
let parts = {};
let geometry = null; // where everything is, for the pointer and the arrows
let busy = 0; // changes in flight
let drag = null; // a bar being dragged
let keys = null; // arrow keys pressed, not yet sent
let trayDragged = ''; // the ref of the item being dragged out of the tray
let generation = 0;
let wantToday = true;

// -- the page -------------------------------------------------------------------------------------

export function mount(element, initial) {
  root = element;
  params = initial;
  picked = new Set();
  rows = [];
  trayDrafts = new Map();
  trayDecided = false;
  wantToday = true;
  bar = filterBar({ value: params.get('q') || '', onSubmit: (text) => ui.navigate({ q: text || null }) });
  const group = h('select', { id: 'group', onchange: () => ui.navigate({ group: group.value === 'epic' ? null : group.value }) }, GROUPS.map(([value, label]) => h('option', { value }, label)));
  const zooms = h(
    'div',
    { class: 'zoom-choice segmented', role: 'group', 'aria-label': 'Zoom' },
    Object.entries(ZOOMS).map(([key, zoom]) => h('button', { type: 'button', dataset: { zoom: key }, onclick: () => ui.navigate({ zoom: key === 'month' ? null : key }, { replace: true }) }, zoom.label)),
  );
  parts = {
    group,
    zooms,
    summary: h('p', { class: 'count', id: 'count', role: 'status' }),
    tray: h('details', { class: 'rm-tray', id: 'tray' }),
    scroll: h('div', { class: 'rm-scroll', id: 'rm-scroll', role: 'region', tabindex: 0, 'aria-label': 'Timeline, scrolls sideways and down' }, h('p', { class: 'loading', role: 'status' }, 'Reading the timeline…')),
  };
  const pan = (way) => parts.scroll.scrollBy({ left: way * Math.max(parts.scroll.clientWidth * 0.6, 160), behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  put(
    root,
    h(
      'div',
      { class: 'roadmap-page' },
      h('header', { class: 'page-header' }, h('div', { class: 'page-title' }, h('h1', {}, 'Roadmap'), h('p', { class: 'page-sub' }, 'Work on a timeline: when it starts, when it is due, and what waits for what.'))),
      h(
        'div',
        { class: 'rm-toolbar' },
        bar.form,
        h('div', { class: 'rm-group-choice' }, h('label', { for: 'group' }, 'Group by'), group),
        zooms,
        h(
          'div',
          { class: 'rm-pan' },
          h('button', { type: 'button', id: 'today', onclick: scrollToToday }, icon('calendar', { size: 16 }), 'Today'),
          h('button', { type: 'button', class: 'icon-only flip', 'aria-label': 'Scroll the timeline to earlier days', onclick: () => pan(-1) }, icon('chevron_right', { size: 16 })),
          h('button', { type: 'button', class: 'icon-only', 'aria-label': 'Scroll the timeline to later days', onclick: () => pan(1) }, icon('chevron_right', { size: 16 })),
        ),
      ),
      bar.problem,
      h(
        'div',
        { class: 'rm-notes' },
        bar.help,
        h(
          'details',
          { class: 'filter-help rm-help' },
          h('summary', {}, icon('chevron_right', { size: 14 }), 'How to read and change the timeline'),
          h(
            'p',
            {},
            'A bar runs from its start date to its target date. Drag a bar to move both dates, or an end to move one. With a bar focused, the left and right arrows move it a day (Shift: a week); Alt moves only its start and Ctrl only its end; Space chooses it to move together with others, and Enter opens it. A light dashed bar is worked out from what is under it. An arrow runs from what is needed first to what waits for it. Drag the empty timeline to move along it.',
          ),
        ),
        h('details', { class: 'filter-help rm-key' }, h('summary', {}, icon('chevron_right', { size: 14 }), 'Key to the bars'), legend()),
        parts.summary,
      ),
      parts.tray,
      parts.scroll,
    ),
  );
  load();
}

// What a bar looks like for each state: its icon and its shape, as well as its colour.
function legend() {
  return h(
    'ul',
    { class: 'rm-legend', 'aria-label': 'What the bars show' },
    STATUSES.map((status) => h('li', {}, h('span', { class: `rm-swatch status-${status}` }, statusIcon(status, 12)), STATUS[status].label)),
    h('li', {}, h('span', { class: 'rm-swatch worked-out' }), 'Worked out from what is under it'),
    h('li', {}, h('span', { class: 'rm-swatch overdue' }, icon('alert', { size: 12 })), 'Overdue'),
  );
}

export function unmount() {
  generation += 1;
  if (keys) clearTimeout(keys.timer);
  keys = null;
  root = null;
}

export function update(next) {
  const before = params;
  params = next;
  if (!root) return;
  if ((next.get('q') || '') !== (before.get('q') || '')) {
    picked = new Set();
    load();
  } else if ((next.get('zoom') || 'month') !== (before.get('zoom') || 'month')) {
    wantToday = true;
    draw();
  } else if ((next.get('group') || '') !== (before.get('group') || '')) {
    draw();
  }
  // Nothing else in the address (the drawer, the selected record) changes the timeline, so it is
  // left as it is, with its scroll, its chosen bars and what is being dragged.
}

export function refresh() {
  if (!root || busy || keys || drag) return;
  load();
}

async function load() {
  const mine = ++generation;
  const found = await loadRows(params.get('q') || '', bar);
  if (mine !== generation || !root) return;
  if (found !== null) rows = found;
  const here = new Set(rows.map((node) => node.ref));
  for (const ref of [...picked]) if (!here.has(ref)) picked.delete(ref);
  draw();
}

// -- what goes where --------------------------------------------------------------------------------

// The days a node is drawn over: its own dates, or what its children's add up to. Null when it has
// none, and then it waits in the tray.
function spanOf(node) {
  const first = node.span_start;
  const last = node.span_end;
  if (!first && !last) return null;
  let start = toDay(first ?? last);
  let end = toDay(last ?? first);
  if (start > end) [start, end] = [end, start];
  return { s: start, e: end, editable: !node.span_worked_out };
}

function groupOf(node, mode) {
  if (mode === 'status') return { key: node.status, label: STATUS[node.status].label, order: STATUSES.indexOf(node.status) };
  if (mode === 'priority') return { key: node.priority, label: `${PRIORITY[node.priority].label} priority`, order: 3 - PRIORITIES.indexOf(node.priority) };
  if (mode === 'claimed') return { key: node.claimed_by || '', label: node.claimed_by ? `Held by ${node.claimed_by}` : 'Held by nobody', order: node.claimed_by ? 0 : 1 };
  const epic = epicOf(node);
  return { key: epic.ref, label: `Epic ${nodeCode(epic.kind, epic.number)} ${epic.title}`, order: epic.number };
}

// Where a node sorts among its epic's rows: by its story's number (an epic first), a story before its tasks.
const storyOf = (node) => (node.kind === 'story' ? node.number : node.kind === 'task' ? parseNodeRef(node.parent).number : 0);

function inOrder(items, mode) {
  return [...items].sort((a, b) => {
    if (mode === 'epic') {
      const [x, y] = [storyOf(a.node), storyOf(b.node)];
      if (x !== y) return x - y;
      if (a.node.kind !== b.node.kind) return a.node.kind === 'story' || a.node.kind === 'epic' ? -1 : 1;
    }
    return a.s - b.s || byRef(a.node.ref, b.node.ref);
  });
}

function layoutRows(dated, mode) {
  const groups = new Map();
  for (const item of dated) {
    const found = groupOf(item.node, mode);
    if (!groups.has(found.key)) groups.set(found.key, { ...found, items: [] });
    groups.get(found.key).items.push(item);
  }
  const list = [];
  for (const group of [...groups.values()].sort((a, b) => a.order - b.order || a.label.localeCompare(b.label))) {
    list.push({ type: 'group', label: `${group.label} (${group.items.length})` });
    for (const item of inOrder(group.items, mode)) list.push({ type: 'item', ...item });
  }
  return list;
}

// -- drawing --------------------------------------------------------------------------------------

function draw() {
  if (!root) return;
  const zoom = ZOOMS[params.get('zoom')] ? params.get('zoom') : 'month';
  const { ppd } = ZOOMS[zoom];
  const mode = GROUPS.some(([value]) => value === params.get('group')) ? params.get('group') : 'epic';
  const today = todayDay();
  const focusedBar = document.activeElement?.closest?.('.rm-bar')?.dataset.ref;
  const scrolled = { left: parts.scroll.scrollLeft, top: parts.scroll.scrollTop };
  parts.scroll.classList.remove('moving');
  bar.setValue(params.get('q') || '');
  parts.group.value = mode;
  parts.zooms.querySelectorAll('button').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.zoom === zoom)));

  const dated = [];
  const undated = [];
  for (const node of rows) {
    const span = spanOf(node);
    if (span) dated.push({ node, ...span });
    else undated.push(node);
  }
  const low = Math.min(today, ...dated.map((item) => item.s));
  const high = Math.max(today, ...dated.map((item) => item.e));
  const origin = monthStart(low - 14);
  const end = Math.max(nextMonth(high + 45) - 1, origin + 120);
  const width = (end - origin + 1) * ppd;

  const list = layoutRows(dated, mode);
  const items = new Map();
  list.forEach((entry, at) => {
    if (entry.type === 'item') items.set(entry.node.ref, { ...entry, y: at * ROW, left: (entry.s - origin) * ppd, width: Math.max((entry.e - entry.s + 1) * ppd, MIN_BAR) });
  });
  geometry = { origin, end, ppd, items, today };

  const rowsHigh = Math.max(list.length, MIN_ROWS) * ROW;
  const labels = h('div', { class: 'rm-labels' }, h('div', { class: 'rm-corner', css: { height: `${SCALE}px` } }, 'Item'));
  const area = h('div', { class: 'rm-rows', css: { height: `${rowsHigh}px` } });
  const scale = h('div', { class: 'rm-scale', css: { height: `${SCALE}px`, width: `${width}px` } });
  const track = h('div', { class: 'rm-track', css: { width: `${width}px` }, dataset: { origin: fromDay(origin), ppd: String(ppd), today: fromDay(today), zoom } }, scale, area);

  drawScale(scale, origin, end, zoom, ppd, today);
  for (let month = origin; month <= end; month = nextMonth(month)) area.append(h('div', { class: 'rm-gridline', css: { left: `${(month - origin) * ppd}px` } }));
  area.append(h('div', { class: 'rm-today', 'aria-hidden': 'true', css: { left: `${(today - origin) * ppd}px` } }));

  list.forEach((entry, at) => {
    if (entry.type === 'group') {
      labels.append(h('div', { class: 'rm-group', title: entry.label, css: { height: `${ROW}px` } }, h('span', { class: 'rm-group-text' }, entry.label)));
      area.append(h('div', { class: 'rm-band', css: { top: `${at * ROW}px`, height: `${ROW}px` } }));
      return;
    }
    labels.append(labelFor(entry, mode));
    area.append(...barFor(entry, items.get(entry.node.ref)));
  });
  // A timeline with nothing on it is still somewhere to drop an item from the tray.
  if (list.length < MIN_ROWS) labels.append(h('div', { class: 'rm-spacer', css: { height: `${(MIN_ROWS - list.length) * ROW}px` } }));
  if (!dated.length) area.append(h('p', { class: 'rm-drop-hint' }, rows.length ? 'Nothing here has dates yet. Drag an item from the tray onto the timeline, or give it dates in the tray.' : 'Nothing to show. Make some work on the Board, or clear the filter.'));
  area.append(arrows(items, width, rowsHigh));
  area.addEventListener('dragover', (event) => {
    if (trayDragged) {
      event.preventDefault();
      area.classList.add('over');
    }
  });
  area.addEventListener('dragleave', () => area.classList.remove('over'));
  area.addEventListener('drop', (event) => {
    event.preventDefault();
    area.classList.remove('over');
    const ref = trayDragged;
    trayDragged = '';
    const node = store.nodes.get(ref);
    if (!node) return;
    const first = geometry.origin + Math.floor((event.clientX - area.getBoundingClientRect().left) / geometry.ppd);
    changeDates(node, { start_on: fromDay(first), target_on: fromDay(first + NEW_LENGTH_DAYS - 1) }, 'Gave');
  });
  area.addEventListener('pointerdown', pointerDown);
  scale.addEventListener('pointerdown', startPan);

  parts.scroll.replaceChildren(h('div', { class: 'rm-inner' }, labels, track));
  parts.scroll.scrollLeft = scrolled.left;
  parts.scroll.scrollTop = scrolled.top;
  if (wantToday) {
    wantToday = false;
    scrollToStart();
  }
  drawTray(undated, dated.length);
  parts.summary.textContent = `${plural(dated.length, 'item')} on the timeline, ${undated.length} undated${params.get('q') ? ', for this filter' : ''}${picked.size ? `; ${picked.size} chosen to move together` : ''}.`;
  if (focusedBar) parts.scroll.querySelector(`.rm-bar[data-ref="${focusedBar}"]`)?.focus();
  parts.scroll.dataset.ready = 'true';
}

function drawScale(scale, origin, end, zoom, ppd, today) {
  const tick = (day, text, line) => h('span', { class: `rm-tick ${line}`, css: { left: `${(day - origin) * ppd + 4}px` } }, text);
  for (let month = origin; month <= end; month = nextMonth(month)) {
    const quarter = Math.floor(monthOf(month) / 3) + 1;
    const first = monthOf(month) === 0;
    if (zoom === 'month') scale.append(tick(month, `${monthName(month)} ${yearOf(month)}`, 'top'));
    else if (zoom === 'quarter') {
      if (monthOf(month) % 3 === 0 || month === origin) scale.append(tick(month, `${yearOf(month)} Q${quarter}`, 'top'));
      scale.append(tick(month, monthName(month), 'bottom'));
    } else {
      if (first || month === origin) scale.append(tick(month, String(yearOf(month)), 'top'));
      if (monthOf(month) % 3 === 0 || month === origin) scale.append(tick(month, `Q${quarter}`, 'bottom'));
    }
  }
  if (zoom === 'month') {
    for (let day = origin; day <= end; day += 1) if (weekday(day) === 1) scale.append(tick(day, String(dayOfMonth(day)), 'bottom'));
  }
  scale.append(h('span', { class: 'rm-today-label', css: { left: `${(today - origin) * ppd}px` } }, 'Today'));
}

function marks(node, editable) {
  const found = [];
  if (node.overdue) found.push('Overdue');
  if (node.schedule_conflicts.length) found.push('Schedule conflict');
  if (!editable) found.push('Worked out from what is under it');
  return found;
}

function labelFor(entry, mode) {
  const { node } = entry;
  const code = nodeCode(node.kind, node.number);
  const depth = mode === 'epic' ? (node.kind === 'epic' ? 0 : node.kind === 'story' ? 1 : 2) : 0;
  return h(
    'div',
    { class: `rm-label kind-${node.kind}`, css: { height: `${ROW}px`, paddingLeft: `${8 + depth * 14}px` }, dataset: { ref: node.ref, kind: node.kind } },
    h('span', { class: 'rm-kind', 'aria-hidden': 'true' }, kindIcon(node.kind, 14)),
    h('button', { type: 'button', class: 'link', title: `${KIND[node.kind].label} ${code} ${node.title}`, onclick: () => ui.navigate({ sel: node.ref, panel: null }) }, h('span', { class: 'id mono' }, code), ` ${node.title}`),
    node.overdue ? h('span', { class: 'chip danger overdue', title: 'Overdue' }, icon('alert', { size: 12 }), h('span', { class: 'sr-only' }, 'Overdue')) : null,
    node.schedule_conflicts.length
      ? h('span', { class: 'chip warn conflict', title: `It starts before ${node.schedule_conflicts.map(codeOf).join(', ')} is due` }, icon('alert', { size: 12 }), h('span', { class: 'sr-only' }, 'Schedule conflict'))
      : null,
  );
}

function barFor(entry, spot) {
  const { node, s: first, e: last, editable } = entry;
  const found = marks(node, editable);
  const code = nodeCode(node.kind, node.number);
  const words = `${KIND[node.kind].label} ${node.number}: ${node.title}. ${fromDay(first)} to ${fromDay(last)}. ${STATUS[node.status].label}.${found.length ? ` ${found.join('. ')}.` : ''}`;
  const chosen = picked.has(node.ref);
  // A bar with no room for its icon and words shows neither: the row's label says what it is, and
  // its status icon stands beside it, so nothing is drawn over the bar.
  const short = spot.width < SHORT_BAR;
  const el = h(
    'div',
    {
      class: ['rm-bar', `kind-${node.kind}`, `status-${node.status}`, editable ? '' : 'worked-out', short ? 'short' : '', node.overdue ? 'overdue' : '', node.schedule_conflicts.length ? 'conflict' : '', chosen ? 'picked' : ''].filter(Boolean).join(' '),
      role: 'button',
      tabindex: 0,
      'aria-pressed': String(chosen),
      'aria-label': words,
      title: words,
      css: { left: `${spot.left}px`, width: `${spot.width}px`, top: `${spot.y + 4}px`, height: `${ROW - 8}px` },
      dataset: { ref: node.ref, kind: node.kind, start: fromDay(first), end: fromDay(last), workedOut: String(!editable) },
      onkeydown: barKey,
    },
    editable ? h('span', { class: 'rm-handle start', dataset: { handle: 'start' } }) : null,
    short ? null : h('span', { class: 'rm-icon', 'aria-hidden': 'true' }, statusIcon(node.status, 13)),
    short ? null : h('span', { class: 'rm-text' }, `${code} ${node.title}`),
    editable ? h('span', { class: 'rm-handle end', dataset: { handle: 'end' } }) : null,
  );
  const said = found.filter((text) => text !== 'Worked out from what is under it');
  const note =
    short || said.length
      ? [
          h(
            'span',
            { class: `rm-note status-${node.status}${node.overdue ? ' overdue' : ''}`, css: { left: `${spot.left + spot.width + 4}px`, top: `${spot.y + 4}px` } },
            short ? statusIcon(node.status, 13) : null,
            node.overdue || node.schedule_conflicts.length ? icon('alert', { size: 12 }) : null,
            said.join(' · '),
          ),
        ]
      : [];
  return [el, ...note];
}

// An arrow from what is needed first to the item that waits for it, between bars that are drawn.
function arrows(items, width, height) {
  const svg = s('svg', { class: 'rm-deps', width, height, 'aria-hidden': 'true' }, s('defs', {}, marker('rm-arrow', 'rm-arrow'), marker('rm-arrow-conflict', 'rm-arrow conflict')));
  for (const link of store.links) {
    if (link.link_type !== 'needs_first') continue;
    const waiting = items.get(link.source);
    const first = items.get(link.target);
    if (!waiting || !first) continue;
    const clash = waiting.node.schedule_conflicts.includes(first.node.ref);
    const [x1, y1] = [first.left + first.width, first.y + ROW / 2];
    const [x2, y2] = [waiting.left, waiting.y + ROW / 2];
    let d;
    if (x2 >= x1 + 16) d = `M${x1},${y1} H${x1 + 8} V${y2} H${x2}`;
    else {
      const lane = y2 > y1 ? y2 - ROW / 2 : y2 + ROW / 2;
      d = `M${x1},${y1} H${x1 + 8} V${lane} H${x2 - 8} V${y2} H${x2}`;
    }
    svg.append(
      s(
        'path',
        { class: `dep${clash ? ' conflict' : ''}`, d, fill: 'none', 'marker-end': `url(#${clash ? 'rm-arrow-conflict' : 'rm-arrow'})`, 'data-from': first.node.ref, 'data-to': waiting.node.ref },
        s('title', {}, `${codeOf(first.node.ref)} ${first.node.title} is needed first by ${codeOf(waiting.node.ref)} ${waiting.node.title}${clash ? ': it is planned to start before that is due' : ''}`),
      ),
    );
  }
  return svg;
}

// The arrowhead takes its colour from the style sheet (the class), so it follows the theme.
function marker(id, className) {
  return s('marker', { id, class: className, viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse' }, s('path', { d: 'M0,0 L10,5 L0,10 z' }));
}

function scrollToToday() {
  if (!geometry) return;
  const labels = parts.scroll.querySelector('.rm-labels')?.offsetWidth ?? 0;
  const at = labels + (geometry.today - geometry.origin) * geometry.ppd;
  parts.scroll.scrollLeft = Math.max(0, at - parts.scroll.clientWidth / 3);
}

// Where the timeline opens: at today, or at the first item when nothing is anywhere near today, so
// that it never opens on months of empty space.
function scrollToStart() {
  if (!geometry || !geometry.items.size) {
    scrollToToday();
    return;
  }
  const { today, origin, ppd, items } = geometry;
  const labels = parts.scroll.querySelector('.rm-labels')?.offsetWidth ?? 0;
  const days = (parts.scroll.clientWidth - labels) / ppd;
  const spans = [...items.values()];
  if (spans.some((item) => item.e >= today - days / 3 && item.s <= today + (days * 2) / 3)) scrollToToday();
  else parts.scroll.scrollLeft = Math.max(0, (Math.min(...spans.map((item) => item.s)) - origin) * ppd - 16);
}

// -- the tray of undated items ---------------------------------------------------------------------

// What was typed in the tray's date boxes and not sent yet, kept across the redraws that news causes.
let trayDrafts = new Map();
// The tray opens by itself only when there is nothing on the timeline to look at and something
// waits in it; after that it stays as the person left it.
let trayDecided = false;

function drawTray(undated, onTimeline) {
  if (!trayDecided) {
    parts.tray.open = onTimeline === 0 && undated.length > 0;
    trayDecided = true;
  }
  const open = parts.tray.open;
  // With nothing undated the tray has nothing to hold: the count above the timeline says so.
  parts.tray.hidden = undated.length === 0;
  const here = new Set(undated.map((node) => node.ref));
  for (const ref of [...trayDrafts.keys()]) if (!here.has(ref)) trayDrafts.delete(ref);
  put(
    parts.tray,
    h('summary', {}, icon('chevron_right', { size: 14 }), h('span', { class: 'tray-title' }, 'Undated'), h('span', { class: 'chip' }, String(undated.length)), h('span', { class: 'tray-sub' }, 'Drag one onto the timeline, or give it dates here')),
    undated.length
      ? h(
          'ul',
          { class: 'plain tray-list' },
          undated.map((node) => {
            const code = nodeCode(node.kind, node.number);
            const draft = trayDrafts.get(node.ref) || { start: '', target: '' };
            trayDrafts.set(node.ref, draft);
            const start = h('input', { type: 'date', value: draft.start, 'aria-label': `Start date of ${code}`, oninput: () => { draft.start = start.value; } });
            const target = h('input', { type: 'date', value: draft.target, 'aria-label': `Target date of ${code}`, oninput: () => { draft.target = target.value; } });
            return h(
              'li',
              { class: 'tray-item', dataset: { ref: node.ref } },
              h('span', { class: 'tray-drag', draggable: 'true', title: 'Drag onto the timeline', ondragstart: (event) => { trayDragged = node.ref; event.dataTransfer.setData('application/x-vrum-node', node.ref); event.dataTransfer.setData('text/plain', code); event.dataTransfer.effectAllowed = 'move'; }, ondragend: () => { trayDragged = ''; } }, kindIcon(node.kind, 14), h('span', { class: 'id mono' }, code), h('span', { class: 'tray-name' }, node.title)),
              h(
                'form',
                {
                  class: 'tray-form',
                  onsubmit: (event) => {
                    event.preventDefault();
                    if (!start.value && !target.value) {
                      notify('Give a start date, a target date or both.', { refusal: true });
                      return;
                    }
                    changeDates(node, { ...(start.value ? { start_on: start.value } : {}), ...(target.value ? { target_on: target.value } : {}) }, 'Gave');
                  },
                },
                h('span', { class: 'tray-field' }, h('span', { 'aria-hidden': 'true' }, 'Start'), start),
                h('span', { class: 'tray-field' }, h('span', { 'aria-hidden': 'true' }, 'Due'), target),
                h('button', { type: 'submit', class: 'small' }, 'Set dates'),
              ),
            );
          }),
        )
      : h('p', { class: 'quiet tray-none' }, rows.length ? 'Everything here has dates, or has children that do.' : 'There is no work to show yet.'),
  );
  parts.tray.open = open;
}

// -- moving bars ------------------------------------------------------------------------------------

function datesAfter(item, mode, days) {
  let { s: first, e: last } = item;
  if (mode === 'move') {
    first += days;
    last += days;
  } else if (mode === 'start') first += days;
  else last += days;
  return { first, last };
}

// What to send for one item. Its own dates are what move: a bar that shows only one date moves that one.
function changeFor(item, mode, days) {
  const { node } = item;
  if (mode === 'move') {
    return {
      ...(node.start_on ? { start_on: fromDay(toDay(node.start_on) + days) } : {}),
      ...(node.target_on ? { target_on: fromDay(toDay(node.target_on) + days) } : {}),
    };
  }
  return mode === 'start' ? { start_on: fromDay(item.s + days) } : { target_on: fromDay(item.e + days) };
}

function targetsFor(ref, mode) {
  if (mode === 'move' && picked.has(ref)) return [...picked].filter((other) => geometry.items.get(other)?.editable);
  return geometry.items.get(ref)?.editable ? [ref] : [];
}

function preview(refs, mode, days) {
  const { origin, ppd } = geometry;
  for (const ref of refs) {
    const item = geometry.items.get(ref);
    const el = parts.scroll.querySelector(`.rm-bar[data-ref="${ref}"]`);
    if (!item || !el) continue;
    const { first, last } = datesAfter(item, mode, days);
    const [from, to] = first <= last ? [first, last] : [last, first];
    el.style.left = `${(from - origin) * ppd}px`;
    el.style.width = `${Math.max((to - from + 1) * ppd, MIN_BAR)}px`;
    el.classList.toggle('backwards', first > last);
    el.dataset.preview = `${fromDay(first)} to ${fromDay(last)}`;
  }
  parts.scroll.classList.add('moving');
}

async function settle() {
  try {
    await sync();
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
  }
}

async function commit(refs, mode, days) {
  const items = refs.map((ref) => geometry.items.get(ref)).filter(Boolean);
  if (!items.length) return;
  busy += 1;
  try {
    if (items.length === 1) {
      const { node } = items[0];
      const change = changeFor(items[0], mode, days);
      try {
        await call('update_node', { actor: actor(), node: node.ref, expected_version: node.version, ...change });
        notify(`Moved ${nodeCode(node.kind, node.number)}: ${describe(change)}.`);
      } catch (error) {
        if (!(error instanceof Refusal)) throw error;
        notify(error.message, { refusal: true });
      }
    } else {
      const changes = items.map((item) => ({ node: item.node.ref, expected_version: item.node.version, ...changeFor(item, mode, days) }));
      try {
        const outcome = await call('update_nodes', { actor: actor(), changes });
        for (const result of outcome.results.filter((one) => !one.applied)) notify(`${codeOf(result.node)} was not moved. ${result.refusal}`, { refusal: true });
        if (outcome.applied) notify(`Moved ${plural(outcome.applied, 'item')}${outcome.refused ? `; ${outcome.refused} refused` : ''}.`);
      } catch (error) {
        if (!(error instanceof Refusal)) throw error;
        notify(error.message, { refusal: true });
      }
    }
    await settle();
  } finally {
    busy -= 1;
  }
  parts.scroll.classList.remove('moving');
  await load();
}

function describe(change) {
  return [change.start_on ? `starts ${change.start_on}` : null, change.target_on ? `due ${change.target_on}` : null].filter(Boolean).join(', ');
}

// Dates given to one item (from the tray, or by dropping it on the timeline).
async function changeDates(node, change, verb) {
  busy += 1;
  try {
    await call('update_node', { actor: actor(), node: node.ref, expected_version: node.version, ...change });
    notify(`${verb} ${nodeCode(node.kind, node.number)} dates: ${describe(change)}.`);
    await settle();
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
  } finally {
    busy -= 1;
  }
  await load();
}

// Dragging the empty timeline moves along it, as the scroll bars would (a mouse only: a finger
// already pans it).
function startPan(event) {
  if (event.button !== 0 || event.pointerType !== 'mouse') return;
  const view = parts.scroll;
  const from = { x: event.clientX, y: event.clientY, left: view.scrollLeft, top: view.scrollTop };
  view.classList.add('panning');
  view.setPointerCapture(event.pointerId);
  const move = (next) => {
    view.scrollLeft = from.left - (next.clientX - from.x);
    view.scrollTop = from.top - (next.clientY - from.y);
  };
  const up = () => {
    view.classList.remove('panning');
    view.removeEventListener('pointermove', move);
    view.removeEventListener('pointerup', up);
    view.removeEventListener('pointercancel', up);
  };
  view.addEventListener('pointermove', move);
  view.addEventListener('pointerup', up);
  view.addEventListener('pointercancel', up);
}

function pointerDown(event) {
  const el = event.target.closest('.rm-bar');
  if (!el) {
    startPan(event);
    return;
  }
  if (event.button !== 0 || !geometry) return;
  const ref = el.dataset.ref;
  const item = geometry.items.get(ref);
  if (!item) return;
  const mode = item.editable ? event.target.closest('.rm-handle')?.dataset.handle || 'move' : 'click';
  const started = { ref, mode, x: event.clientX, days: 0, moved: false, together: event.shiftKey || event.ctrlKey || event.metaKey };
  drag = started;
  el.setPointerCapture(event.pointerId);
  const move = (next) => {
    const across = next.clientX - started.x;
    if (!started.moved && Math.abs(across) < 4) return;
    started.moved = true;
    if (mode === 'click') return;
    started.days = Math.round(across / geometry.ppd);
    preview(targetsFor(ref, mode), mode, started.days);
  };
  const up = () => {
    el.removeEventListener('pointermove', move);
    el.removeEventListener('pointerup', up);
    el.removeEventListener('pointercancel', up);
    drag = null;
    if (!started.moved) {
      if (started.together) togglePicked(ref);
      else ui.navigate({ sel: ref, panel: null });
    } else if (started.days !== 0 && mode !== 'click') {
      commit(targetsFor(ref, mode), mode, started.days);
    } else {
      draw();
    }
  };
  el.addEventListener('pointermove', move);
  el.addEventListener('pointerup', up);
  el.addEventListener('pointercancel', up);
}

function togglePicked(ref) {
  if (picked.has(ref)) picked.delete(ref);
  else picked.add(ref);
  draw();
}

// -- the keyboard -----------------------------------------------------------------------------------

function barKey(event) {
  const el = event.currentTarget;
  const ref = el.dataset.ref;
  const item = geometry?.items.get(ref);
  if (!item) return;
  const key = event.key;
  if (key === 'Enter') ui.navigate({ sel: ref, panel: null });
  else if (key === ' ') togglePicked(ref);
  else if (key === 'Escape') {
    picked = new Set();
    draw();
  } else if ((key === 'ArrowLeft' || key === 'ArrowRight') && item.editable) {
    const mode = event.altKey ? 'start' : event.ctrlKey || event.metaKey ? 'end' : 'move';
    shiftBy(ref, mode, (key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 7 : 1));
  } else if (key === 'ArrowUp' || key === 'ArrowDown') {
    const bars = [...parts.scroll.querySelectorAll('.rm-bar')];
    bars[bars.indexOf(el) + (key === 'ArrowDown' ? 1 : -1)]?.focus();
  } else return;
  event.preventDefault();
}

// Arrow keys add up for a moment, and are sent as one change.
function shiftBy(ref, mode, days) {
  if (keys && (keys.ref !== ref || keys.mode !== mode)) flushKeys();
  if (!keys) keys = { ref, mode, days: 0, refs: targetsFor(ref, mode), timer: 0 };
  keys.days += days;
  preview(keys.refs, mode, keys.days);
  clearTimeout(keys.timer);
  keys.timer = setTimeout(flushKeys, 700);
}

function flushKeys() {
  const sent = keys;
  keys = null;
  if (!sent) return;
  clearTimeout(sent.timer);
  if (sent.days !== 0) commit(sent.refs, sent.mode, sent.days);
  else draw();
}
