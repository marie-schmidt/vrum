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
import { clear, h, plural, s } from './dom.js';
import { filterBar, loadRows } from './filterbar.js';
import { KIND, PRIORITIES, PRIORITY, STATUS, STATUSES } from './labels.js';
import { epicOf, store, sync, titleOf } from './store.js';

const ROW = 30;
const LABEL = 290; // the width of the column of labels (see .rm-labels in board.css)
const SCALE = 56;
const MIN_BAR = 8;
const NEW_LENGTH_DAYS = 7; // an item dropped on the timeline gets a week
const ZOOMS = { month: { ppd: 10, label: 'Month' }, quarter: { ppd: 4, label: 'Quarter' }, year: { ppd: 1.2, label: 'Year' } };
const GROUPS = [['epic', 'Epic'], ['status', 'Status'], ['priority', 'Priority'], ['claimed', 'Who holds it']];

let root = null;
let params = new URLSearchParams();
let bar = null; // the filter box
let rows = []; // what the filter found
let picked = new Set(); // bars chosen to be moved together
let parts = {};
let geometry = null; // where everything is, for the pointer and the arrows
let busy = 0; // changes in flight
let drag = null; // a bar being dragged
let keys = null; // arrow keys pressed, not yet sent
let trayDragged = 0;
let generation = 0;
let wantToday = true;

// -- the page -------------------------------------------------------------------------------------

export function mount(element, initial) {
  root = element;
  params = initial;
  picked = new Set();
  rows = [];
  wantToday = true;
  bar = filterBar({ value: params.get('q') || '', onSubmit: (text) => ui.navigate({ q: text || null }) });
  const group = h('select', { id: 'group', onchange: () => ui.navigate({ group: group.value === 'epic' ? null : group.value }) }, GROUPS.map(([value, label]) => h('option', { value }, label)));
  const zooms = h(
    'div',
    { class: 'zoom-choice', role: 'group', 'aria-label': 'Zoom' },
    Object.entries(ZOOMS).map(([key, zoom]) => h('button', { type: 'button', dataset: { zoom: key }, onclick: () => ui.navigate({ zoom: key === 'month' ? null : key }, { replace: true }) }, zoom.label)),
  );
  parts = {
    group,
    zooms,
    summary: h('p', { class: 'count', id: 'count', role: 'status' }),
    tray: h('details', { class: 'rm-tray', id: 'tray', open: true }),
    scroll: h('div', { class: 'rm-scroll', id: 'rm-scroll' }),
  };
  clear(root).append(
    h('div', { class: 'toolbar' }, h('h1', {}, 'Roadmap'), bar.form, h('label', { for: 'group' }, 'Group by'), group, zooms, h('button', { type: 'button', id: 'today', onclick: scrollToToday }, 'Today')),
    bar.help,
    bar.problem,
    h(
      'p',
      { class: 'help' },
      'A bar runs from its start date to its target date. Drag a bar to move both dates, or an end to move one. With a bar focused, the left and right arrows move it a day (Shift: a week); Alt moves only its start and Ctrl only its end; Space chooses it to move together with others, and Enter opens it. A light dashed bar is worked out from what is under it. An arrow runs from what is needed first to what waits for it.',
    ),
    parts.summary,
    parts.tray,
    parts.scroll,
  );
  load();
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
  } else {
    if ((next.get('zoom') || 'month') !== (before.get('zoom') || 'month')) wantToday = true;
    draw();
  }
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
  const here = new Set(rows.map((node) => node.id));
  for (const id of [...picked]) if (!here.has(id)) picked.delete(id);
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
  return { key: String(epic.id), label: `Epic #${epic.id} ${epic.title}`, order: epic.id };
}

const storyOf = (node) => (node.kind === 'story' ? node.id : node.kind === 'task' ? node.parent_id : 0);

function inOrder(items, mode) {
  return [...items].sort((a, b) => {
    if (mode === 'epic') {
      const [x, y] = [storyOf(a.node), storyOf(b.node)];
      if (x !== y) return x - y;
      if (a.node.kind !== b.node.kind) return a.node.kind === 'story' || a.node.kind === 'epic' ? -1 : 1;
    }
    return a.s - b.s || a.node.id - b.node.id;
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
  const focusedBar = document.activeElement?.closest?.('.rm-bar')?.dataset.id;
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
    if (entry.type === 'item') items.set(entry.node.id, { ...entry, y: at * ROW, left: (entry.s - origin) * ppd, width: Math.max((entry.e - entry.s + 1) * ppd, MIN_BAR) });
  });
  geometry = { origin, end, ppd, items, today };

  const labels = h('div', { class: 'rm-labels' }, h('div', { class: 'rm-corner', css: { height: `${SCALE}px` } }, 'Item'));
  const area = h('div', { class: 'rm-rows', css: { height: `${list.length * ROW}px` } });
  const scale = h('div', { class: 'rm-scale', css: { height: `${SCALE}px`, width: `${width}px` } });
  const track = h('div', { class: 'rm-track', css: { width: `${width}px` }, dataset: { origin: fromDay(origin), ppd: String(ppd), today: fromDay(today), zoom } }, scale, area);

  drawScale(scale, origin, end, zoom, ppd, today);
  for (let month = origin; month <= end; month = nextMonth(month)) area.append(h('div', { class: 'rm-gridline', css: { left: `${(month - origin) * ppd}px` } }));
  area.append(h('div', { class: 'rm-today', 'aria-hidden': 'true', css: { left: `${(today - origin) * ppd}px` } }));

  list.forEach((entry, at) => {
    if (entry.type === 'group') {
      labels.append(h('div', { class: 'rm-group', css: { height: `${ROW}px` } }, entry.label));
      area.append(h('div', { class: 'rm-band', css: { top: `${at * ROW}px`, height: `${ROW}px` } }));
      return;
    }
    labels.append(labelFor(entry, mode));
    area.append(...barFor(entry, items.get(entry.node.id)));
  });
  area.append(arrows(items, width, list.length * ROW));
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
    const id = trayDragged;
    trayDragged = 0;
    const node = store.nodes.get(id);
    if (!node) return;
    const first = geometry.origin + Math.floor((event.clientX - area.getBoundingClientRect().left) / geometry.ppd);
    changeDates(node, { start_on: fromDay(first), target_on: fromDay(first + NEW_LENGTH_DAYS - 1) }, 'Gave');
  });
  area.addEventListener('pointerdown', pointerDown);

  parts.scroll.replaceChildren(h('div', { class: 'rm-inner' }, labels, track));
  parts.scroll.scrollLeft = scrolled.left;
  parts.scroll.scrollTop = scrolled.top;
  if (wantToday) {
    wantToday = false;
    scrollToToday();
  }
  drawTray(undated);
  parts.summary.textContent = `${plural(dated.length, 'item')} on the timeline, ${undated.length} undated${params.get('q') ? ', for this filter' : ''}${picked.size ? `; ${picked.size} chosen to move together` : ''}.`;
  if (focusedBar) parts.scroll.querySelector(`.rm-bar[data-id="${focusedBar}"]`)?.focus();
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
  const depth = mode === 'epic' ? (node.kind === 'epic' ? 0 : node.kind === 'story' ? 1 : 2) : 0;
  return h(
    'div',
    { class: `rm-label kind-${node.kind}`, css: { height: `${ROW}px`, paddingLeft: `${8 + depth * 14}px` }, dataset: { id: node.id, kind: node.kind } },
    h('span', { 'aria-hidden': 'true' }, `${KIND[node.kind].sign} `),
    h('button', { type: 'button', class: 'link', onclick: () => ui.navigate({ card: node.id, panel: null }) }, `#${node.id} ${node.title}`),
    node.overdue ? h('span', { class: 'chip overdue' }, '! Overdue') : null,
    node.schedule_conflicts.length ? h('span', { class: 'chip conflict', title: `It starts before ${node.schedule_conflicts.map((id) => `#${id}`).join(', ')} is due` }, '! Schedule conflict') : null,
  );
}

function barFor(entry, spot) {
  const { node, s: first, e: last, editable } = entry;
  const found = marks(node, editable);
  const words = `${KIND[node.kind].label} ${node.id}: ${node.title}. ${fromDay(first)} to ${fromDay(last)}. ${STATUS[node.status].label}.${found.length ? ` ${found.join('. ')}.` : ''}`;
  const chosen = picked.has(node.id);
  const el = h(
    'div',
    {
      class: ['rm-bar', `kind-${node.kind}`, `status-${node.status}`, editable ? '' : 'worked-out', node.overdue ? 'overdue' : '', node.schedule_conflicts.length ? 'conflict' : '', chosen ? 'picked' : ''].filter(Boolean).join(' '),
      role: 'button',
      tabindex: 0,
      'aria-pressed': String(chosen),
      'aria-label': words,
      title: words,
      css: { left: `${spot.left}px`, width: `${spot.width}px`, top: `${spot.y + 4}px`, height: `${ROW - 8}px` },
      dataset: { id: node.id, kind: node.kind, start: fromDay(first), end: fromDay(last), workedOut: String(!editable) },
      onkeydown: barKey,
    },
    editable ? h('span', { class: 'rm-handle start', dataset: { handle: 'start' } }) : null,
    h('span', { class: 'rm-sign', 'aria-hidden': 'true' }, STATUS[node.status].sign),
    h('span', { class: 'rm-text' }, node.title),
    editable ? h('span', { class: 'rm-handle end', dataset: { handle: 'end' } }) : null,
  );
  const note = found.length ? [h('span', { class: 'rm-note', css: { left: `${spot.left + spot.width + 6}px`, top: `${spot.y + 6}px` } }, found.map((text) => `${text === 'Worked out from what is under it' ? 'worked out' : `! ${text}`}`).join(' · '))] : [];
  return [el, ...note];
}

// An arrow from what is needed first to the item that waits for it, between bars that are drawn.
function arrows(items, width, height) {
  const svg = s('svg', { class: 'rm-deps', width, height, 'aria-hidden': 'true' }, s('defs', {}, marker('rm-arrow', '#16130f'), marker('rm-arrow-conflict', '#8a5a00')));
  for (const link of store.links) {
    if (link.link_type !== 'needs_first') continue;
    const waiting = items.get(link.source_id);
    const first = items.get(link.target_id);
    if (!waiting || !first) continue;
    const clash = waiting.node.schedule_conflicts.includes(first.node.id);
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
        { class: `dep${clash ? ' conflict' : ''}`, d, fill: 'none', 'marker-end': `url(#${clash ? 'rm-arrow-conflict' : 'rm-arrow'})`, 'data-from': first.node.id, 'data-to': waiting.node.id },
        s('title', {}, `#${first.node.id} ${first.node.title} is needed first by #${waiting.node.id} ${waiting.node.title}${clash ? ': it is planned to start before that is due' : ''}`),
      ),
    );
  }
  return svg;
}

function marker(id, fill) {
  return s('marker', { id, viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse' }, s('path', { d: 'M0,0 L10,5 L0,10 z', fill }));
}

function scrollToToday() {
  if (!geometry) return;
  const at = LABEL + (geometry.today - geometry.origin) * geometry.ppd;
  parts.scroll.scrollLeft = Math.max(0, at - parts.scroll.clientWidth / 3);
}

// -- the tray of undated items ---------------------------------------------------------------------

function drawTray(undated) {
  const open = parts.tray.open;
  clear(parts.tray).append(
    h('summary', {}, `Undated (${undated.length}): drag one onto the timeline, or give it dates here`),
    undated.length
      ? h(
          'ul',
          { class: 'plain tray-list' },
          undated.map((node) => {
            const start = h('input', { type: 'date', 'aria-label': `Start date of #${node.id}` });
            const target = h('input', { type: 'date', 'aria-label': `Target date of #${node.id}` });
            return h(
              'li',
              { class: 'tray-item', dataset: { id: node.id } },
              h('span', { class: 'tray-drag', draggable: 'true', title: 'Drag onto the timeline', ondragstart: (event) => { trayDragged = node.id; event.dataTransfer.setData('application/x-vrum-node', String(node.id)); event.dataTransfer.setData('text/plain', `#${node.id}`); event.dataTransfer.effectAllowed = 'move'; }, ondragend: () => { trayDragged = 0; } }, `${KIND[node.kind].sign} #${node.id} ${node.title}`),
              h(
                'form',
                {
                  class: 'inline-form',
                  onsubmit: (event) => {
                    event.preventDefault();
                    if (!start.value && !target.value) {
                      notify('Give a start date, a target date or both.', { refusal: true });
                      return;
                    }
                    changeDates(node, { ...(start.value ? { start_on: start.value } : {}), ...(target.value ? { target_on: target.value } : {}) }, 'Gave');
                  },
                },
                start,
                target,
                h('button', { type: 'submit' }, 'Set dates'),
              ),
            );
          }),
        )
      : h('p', { class: 'quiet' }, 'Everything here has dates, or has children that do.'),
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

function targetsFor(id, mode) {
  if (mode === 'move' && picked.has(id)) return [...picked].filter((other) => geometry.items.get(other)?.editable);
  return geometry.items.get(id)?.editable ? [id] : [];
}

function preview(ids, mode, days) {
  const { origin, ppd } = geometry;
  for (const id of ids) {
    const item = geometry.items.get(id);
    const el = parts.scroll.querySelector(`.rm-bar[data-id="${id}"]`);
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

async function commit(ids, mode, days) {
  const items = ids.map((id) => geometry.items.get(id)).filter(Boolean);
  if (!items.length) return;
  busy += 1;
  try {
    if (items.length === 1) {
      const { node } = items[0];
      const change = changeFor(items[0], mode, days);
      try {
        await call('update_node', { actor: actor(), node_id: node.id, expected_version: node.version, ...change });
        notify(`Moved #${node.id}: ${describe(change)}.`);
      } catch (error) {
        if (!(error instanceof Refusal)) throw error;
        notify(error.message, { refusal: true });
      }
    } else {
      const changes = items.map((item) => ({ node_id: item.node.id, expected_version: item.node.version, ...changeFor(item, mode, days) }));
      try {
        const outcome = await call('update_nodes', { actor: actor(), changes });
        for (const result of outcome.results.filter((one) => !one.applied)) notify(`#${result.node_id} was not moved. ${result.refusal}`, { refusal: true });
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
    await call('update_node', { actor: actor(), node_id: node.id, expected_version: node.version, ...change });
    notify(`${verb} #${node.id} dates: ${describe(change)}.`);
    await settle();
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
  } finally {
    busy -= 1;
  }
  await load();
}

function pointerDown(event) {
  const el = event.target.closest('.rm-bar');
  if (!el || event.button !== 0 || !geometry) return;
  const id = Number(el.dataset.id);
  const item = geometry.items.get(id);
  if (!item) return;
  const mode = item.editable ? event.target.closest('.rm-handle')?.dataset.handle || 'move' : 'click';
  const started = { id, mode, x: event.clientX, days: 0, moved: false, together: event.shiftKey || event.ctrlKey || event.metaKey };
  drag = started;
  el.setPointerCapture(event.pointerId);
  const move = (next) => {
    const across = next.clientX - started.x;
    if (!started.moved && Math.abs(across) < 4) return;
    started.moved = true;
    if (mode === 'click') return;
    started.days = Math.round(across / geometry.ppd);
    preview(targetsFor(id, mode), mode, started.days);
  };
  const up = () => {
    el.removeEventListener('pointermove', move);
    el.removeEventListener('pointerup', up);
    el.removeEventListener('pointercancel', up);
    drag = null;
    if (!started.moved) {
      if (started.together) togglePicked(id);
      else ui.navigate({ card: id, panel: null });
    } else if (started.days !== 0 && mode !== 'click') {
      commit(targetsFor(id, mode), mode, started.days);
    } else {
      draw();
    }
  };
  el.addEventListener('pointermove', move);
  el.addEventListener('pointerup', up);
  el.addEventListener('pointercancel', up);
}

function togglePicked(id) {
  if (picked.has(id)) picked.delete(id);
  else picked.add(id);
  draw();
}

// -- the keyboard -----------------------------------------------------------------------------------

function barKey(event) {
  const el = event.currentTarget;
  const id = Number(el.dataset.id);
  const item = geometry?.items.get(id);
  if (!item) return;
  const key = event.key;
  if (key === 'Enter') ui.navigate({ card: id, panel: null });
  else if (key === ' ') togglePicked(id);
  else if (key === 'Escape') {
    picked = new Set();
    draw();
  } else if ((key === 'ArrowLeft' || key === 'ArrowRight') && item.editable) {
    const mode = event.altKey ? 'start' : event.ctrlKey || event.metaKey ? 'end' : 'move';
    shiftBy(id, mode, (key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 7 : 1));
  } else if (key === 'ArrowUp' || key === 'ArrowDown') {
    const bars = [...parts.scroll.querySelectorAll('.rm-bar')];
    bars[bars.indexOf(el) + (key === 'ArrowDown' ? 1 : -1)]?.focus();
  } else return;
  event.preventDefault();
}

// Arrow keys add up for a moment, and are sent as one change.
function shiftBy(id, mode, days) {
  if (keys && (keys.id !== id || keys.mode !== mode)) flushKeys();
  if (!keys) keys = { id, mode, days: 0, ids: targetsFor(id, mode), timer: 0 };
  keys.days += days;
  preview(keys.ids, mode, keys.days);
  clearTimeout(keys.timer);
  keys.timer = setTimeout(flushKeys, 700);
}

function flushKeys() {
  const sent = keys;
  keys = null;
  if (!sent) return;
  clearTimeout(sent.timer);
  if (sent.days !== 0) commit(sent.ids, sent.mode, sent.days);
  else draw();
}
