// The Roadmap: the work as bars on a timeline, from start date to target date. Dragging a bar moves
// both dates and dragging an end moves one; a bar can also be moved with the keyboard (the arrow
// keys, a week with Shift) and its dates are fields on the item's page. A click on a bar, Enter on
// it, and the label of its row open that page. Every change is update_node, or
// update_nodes for several, with the version the bar was drawn at, so the server decides and a bar
// that was changed meanwhile is refused and goes back. `needs_first` links are arrows from what
// is needed to what waits. Only work with dates (its own, or worked out from what is under it) is
// drawn here; the rest is on the Board and in Lists.
//
// The timeline can be measured. A ruler runs along the top (a cell for each day when zoomed in) and a
// very faint grid behind the rows is one day wide and one item high, so a bar covers its start day
// through its target day, cell for cell. Work that is late has a hatched tail from the end of its bar
// through today, a cell for each day it is late, and the number of days beside it.

import { Refusal, call, namesIn } from './api.js';
import { can, itemLink, notify, openItem, ui, viewLink } from './ctx.js';
import { dayOfMonth, fromDay, lateDaysOf, monthName, monthOf, monthStart, nextMonth, toDay, todayDay, weekday, yearOf } from './dates.js';
import { h, plural, put, s } from './dom.js';
import { filterBar, loadRows } from './filterbar.js';
import { hoverCard } from './hovercard.js';
import { icon } from './icons.js';
import { keyButton } from './key-button.js';
import { KIND, PRIORITIES, PRIORITY, STATUS, STATUSES, addsUp, byRef, codeOf, kindIcon, nodeCode, parseNodeRef, sizeWords, statusIcon } from './labels.js';
import { beforeDue } from './list-parts.js';
import { epicOf, store, sync, titleOf } from './store.js';

const ROW = 38;
const BAR_INSET = 5; // a bar is this far from the top and the bottom of its row
const MIN_ROWS = 4; // an empty timeline keeps this many rows, so there is room for its hint
const SCALE = 56; // the ruler: months and years above, days below
const MIN_BAR = 8;
const SHORT_BAR = 72; // a bar narrower than this has no room for its words
const TINY_BAR = 26; // a bar narrower than this has no room for handles of the usual size
// Pixels for each day. It opens on Month, which shows about five weeks of a wide window, so that the bars are big enough to read.
const ZOOMS = { month: { ppd: 22, label: 'Month' }, quarter: { ppd: 7, label: 'Quarter' }, year: { ppd: 2, label: 'Year' } };
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
let generation = 0;
let wantToday = true;
let sizeWatch = null; // sees the timeline's box change size, to know whether it can still be panned

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
    { class: 'zoom-choice segmented', role: 'group', 'aria-label': 'Zoom' },
    Object.entries(ZOOMS).map(([key, zoom]) => h('button', { type: 'button', dataset: { zoom: key }, onclick: () => ui.navigate({ zoom: key === 'month' ? null : key }, { replace: true }) }, zoom.label)),
  );
  parts = {
    group,
    zooms,
    summary: h('p', { class: 'count', id: 'count', role: 'status' }),
    scroll: h('div', { class: 'rm-scroll', id: 'rm-scroll', role: 'region', tabindex: 0, 'aria-label': 'Timeline, scrolls sideways and down' }, h('p', { class: 'loading', role: 'status' }, 'Reading the timeline…')),
  };
  const pan = (way) => parts.scroll.scrollBy({ left: way * Math.max(parts.scroll.clientWidth * 0.6, 160), behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  sizeWatch = new ResizeObserver(markPannable);
  sizeWatch.observe(parts.scroll);
  put(
    root,
    h(
      'div',
      { class: 'roadmap-page' },
      // The top bar names the page, so the h1 is only for assistive technology; the Roadmap has no action, so no header row.
      h('h1', { class: 'sr-only' }, 'Roadmap'),
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
        keyButton(keyContent(), { id: 'rm-key-button' }),
        parts.summary,
      ),
      parts.scroll,
    ),
  );
  load();
}

// What the Key card holds (key-button.js). Every mark is drawn with the classes the timeline itself uses (the same
// swatch rules as the bars, the same arrow lines), so the key cannot drift from the real look. What only a person who
// may change things can do is told only to them.
function keyContent() {
  const mark = (...inside) => h('span', { class: 'rm-key-mark', 'aria-hidden': 'true' }, ...inside);
  const wide = (...inside) => h('span', { class: 'rm-key-mark rm-key-line', 'aria-hidden': 'true' }, ...inside);
  const swatch = (classes, ...inside) => mark(h('span', { class: `rm-swatch ${classes}` }, ...inside));
  const line = (conflict) =>
    h(
      'span',
      { class: 'rm-key-mark rm-key-line', 'aria-hidden': 'true' },
      s(
        'svg',
        { class: 'rm-key-dep', width: 40, height: 16, viewBox: '0 0 40 16' },
        s('defs', {}, marker(conflict ? 'rm-key-arrow-conflict' : 'rm-key-arrow', conflict ? 'rm-arrow conflict' : 'rm-arrow')),
        s('path', { class: `dep${conflict ? ' conflict' : ''}`, d: 'M1,12 H9 V4 H36', fill: 'none', 'marker-end': `url(#${conflict ? 'rm-key-arrow-conflict' : 'rm-key-arrow'})` }),
      ),
    );
  const item = (picture, ...words) => h('li', {}, picture, h('span', {}, ...words));
  const group = (title, className, ...items) => h('div', { class: `rm-key-group ${className}` }, h('h3', {}, title), h('ul', { class: 'plain' }, items));
  const kind = (name) => mark(h('span', { class: 'rm-kind' }, kindIcon(name, 14)));
  // The small mark a row's label carries for a schedule conflict (lateness is the bar's own tail and note).
  const rowMark = (classes) => mark(h('span', { class: `chip ${classes}` }, icon('alert', { size: 12 })));
  return h(
    'div',
    { class: 'rm-key-card' },
    group('Status', 'is-status', STATUSES.map((status) => item(swatch(`status-${status}`, statusIcon(status, 12)), h('strong', {}, STATUS[status].label), ' ', h('span', { class: 'quiet' }, STATUS[status].hint)))),
    h(
      'div',
      { class: 'rm-key-column' },
      group(
        'Lines',
        '',
        item(mark(h('span', { class: 'rm-key-today' })), 'Today'),
        item(line(false), 'Needs first: runs from what is needed to what waits for it'),
        item(line(true), 'Planned too early: this work is planned to start before the work it needs is due'),
      ),
      group(
        'Rows',
        '',
        item(kind('epic'), 'Epic, in bold'),
        item(kind('story'), 'Story'),
        item(kind('task'), 'Task'),
        item(rowMark('warn conflict'), 'Mark at the end of a row: schedule conflict'),
      ),
    ),
    h(
      'div',
      { class: 'rm-key-column' },
      group(
        'Time',
        '',
        item(wide(h('span', { class: 'rm-key-days' }, h('span', { class: 'rm-swatch' }))), 'One cell is one day: a bar covers its start day to its due day'),
        item(mark(h('span', { class: 'rm-key-weekend' })), 'Weekend'),
        item(wide(h('span', { class: 'rm-key-late' }, h('span', { class: 'rm-swatch status-in_progress' }), h('span', { class: 'rm-late' }))), 'Late: a hatched cell for each day since the due day, up to today'),
      ),
      group(
        'On a bar',
        '',
        item(swatch('worked-out'), 'Dates worked out from what is under it'),
        item(swatch('conflict', icon('alert', { size: 12 })), 'Schedule conflict: starts before something it needs is due'),
        item(mark(h('span', { class: 'rm-key-icon status-in_progress' }, statusIcon('in_progress', 13))), 'Status icon beside a bar too short for its words'),
        can('write') ? item(swatch('picked'), 'Chosen to move together (Shift-click or Space)') : null,
        can('write') ? item(swatch('handles', h('span', { class: 'rm-handle start' }), h('span', { class: 'rm-handle end' })), 'Drag a bar to move it, an end to change one date') : null,
      ),
      can('write')
        ? group('With the keyboard', '', h('li', {}, 'Arrow keys move a bar a day, Shift a week'), h('li', {}, 'Alt moves only its start, Ctrl only its end'), h('li', {}, 'Space chooses a bar, Enter opens its page'))
        : null,
    ),
  );
}

export function unmount() {
  generation += 1;
  if (keys) clearTimeout(keys.timer);
  keys = null;
  sizeWatch?.disconnect();
  sizeWatch = null;
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
  // Nothing else in the address changes the timeline, so it is left as it is, with its scroll, its
  // chosen bars and what is being dragged.
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
// none, and then it is not drawn.
function spanOf(node) {
  const first = node.span_start;
  const last = node.span_end;
  if (!first && !last) return null;
  let start = toDay(first ?? last);
  let end = toDay(last ?? first);
  if (start > end) [start, end] = [end, start];
  return { s: start, e: end, editable: !node.span_worked_out };
}

const dayText = (day, withYear) => `${dayOfMonth(day)} ${monthName(day)}${withYear ? ` ${yearOf(day)}` : ''}`;

// "5 days: 3 Dec to 7 Dec": what a bar covers, counting its first and its last day. The year is said when it is not this one.
function spanWords(first, last, today) {
  const withYear = yearOf(first) !== yearOf(today) || yearOf(last) !== yearOf(today);
  return `${plural(last - first + 1, 'day')}: ${first === last ? dayText(first, withYear) : `${dayText(first, withYear)} to ${dayText(last, withYear)}`}`;
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
    // Grouped by epic, the epic's own row (the first of its group) is the heading, so no row says it twice. A group
    // whose epic is not drawn (a filter left it out) keeps its heading.
    if (!(mode === 'epic' && group.items.some((item) => item.node.kind === 'epic'))) list.push({ type: 'group', label: group.label, count: group.items.length });
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
  for (const node of rows) {
    const span = spanOf(node);
    if (span) dated.push({ node, ...span });
  }
  // With no work at all there is no timeline to draw: say why, and what to do next.
  if (!rows.length) {
    geometry = null;
    put(
      parts.scroll,
      h(
        'div',
        { class: 'empty rm-empty', id: 'rm-empty' },
        icon('roadmap', { size: 24 }),
        h('p', {}, h('strong', {}, params.get('q') ? 'Nothing matches this filter.' : 'No work to show yet.'), ' ', params.get('q') ? 'Change the words, or clear the filter.' : 'Work with dates is drawn here as bars. Make some on the Board first.'),
        params.get('q') ? null : viewLink('board', {}, { class: 'link-button' }, 'Go to the Board'),
      ),
    );
    sayCount(0, 0);
    markPannable();
    parts.scroll.dataset.ready = 'true';
    return;
  }
  const low = Math.min(today, ...dated.map((item) => item.s));
  const high = Math.max(today, ...dated.map((item) => item.e));
  const origin = monthStart(low - 14);
  // The timeline runs to the edge of the window at least, so the ruler is never cut short beside empty space.
  const reach = origin + Math.ceil(parts.scroll.clientWidth / ppd);
  const end = Math.max(nextMonth(high + 45) - 1, nextMonth(Math.max(origin + 120, reach)) - 1);
  const width = (end - origin + 1) * ppd;

  const list = layoutRows(dated, mode);
  const items = new Map();
  list.forEach((entry, at) => {
    if (entry.type === 'item') items.set(entry.node.ref, { ...entry, y: at * ROW, left: (entry.s - origin) * ppd, width: Math.max((entry.e - entry.s + 1) * ppd, MIN_BAR), late: lateDaysOf(entry.node) });
  });
  geometry = { origin, end, ppd, items, today };

  const rowsHigh = Math.max(list.length, MIN_ROWS) * ROW;
  const labels = h('div', { class: 'rm-labels' }, h('div', { class: 'rm-corner', css: { height: `${SCALE}px` } }, 'Item'));
  const area = h('div', { class: 'rm-rows', css: { minHeight: `${rowsHigh}px` } });
  const scale = h('div', { class: 'rm-scale', css: { height: `${SCALE}px`, width: `${width}px` } });
  const track = h('div', { class: 'rm-track', css: { width: `${width}px` }, dataset: { origin: fromDay(origin), ppd: String(ppd), today: fromDay(today), zoom } }, scale, area);
  // What the style sheet draws the grid and the ruler's cells from: a day, a week, a weekend and a row, and where the
  // first Saturday and the first Monday fall from the left edge. The rows and the grid go on below the last row, to the
  // bottom of the box.
  const inner = h('div', { class: 'rm-inner' }, labels, track);
  inner.style.setProperty('--rm-row', `${ROW}px`);
  track.style.setProperty('--rm-day', `${ppd}px`);
  track.style.setProperty('--rm-week', `${7 * ppd}px`);
  track.style.setProperty('--rm-weekend', `${2 * ppd}px`);
  track.style.setProperty('--rm-saturday', `${((6 - weekday(origin) + 7) % 7) * ppd}px`);
  track.style.setProperty('--rm-monday', `${((1 - weekday(origin) + 7) % 7) * ppd}px`);

  drawScale(scale, origin, end, zoom, ppd, today);
  for (let month = origin; month <= end; month = nextMonth(month)) area.append(h('div', { class: 'rm-gridline', css: { left: `${(month - origin) * ppd}px` } }));
  // Zoomed in, a line at the start of each week, a little firmer than a day's, helps to read where a bar begins.
  if (zoom === 'month') for (let day = origin; day <= end; day += 1) if (weekday(day) === 1) area.append(h('div', { class: 'rm-gridline week', css: { left: `${(day - origin) * ppd}px` } }));
  // Today is a cell like any other (as wide as a day, though never thinner than its line), so a late tail ends where it ends.
  area.append(h('div', { class: 'rm-today', 'aria-hidden': 'true', css: { left: `${(today - origin) * ppd}px`, width: `${Math.max(ppd, 2)}px` } }));

  list.forEach((entry, at) => {
    if (entry.type === 'group') {
      // The name may be cut short with an ellipsis; the count after it never is.
      labels.append(
        h('div', { class: 'rm-group', title: `${entry.label} (${entry.count})`, css: { height: `${ROW}px`, lineHeight: `${ROW - 1}px` } }, h('span', { class: 'rm-group-text' }, entry.label), h('span', { class: 'rm-group-count' }, ` (${entry.count})`)),
      );
      area.append(h('div', { class: 'rm-band', css: { top: `${at * ROW}px`, height: `${ROW}px` } }));
      return;
    }
    labels.append(labelFor(entry, mode));
    // Grouped by epic, the epic's own row heads its group, with the band a group heading has.
    if (mode === 'epic' && entry.node.kind === 'epic') area.append(h('div', { class: 'rm-band', css: { top: `${at * ROW}px`, height: `${ROW}px` } }));
    area.append(...barFor(entry, items.get(entry.node.ref)));
  });
  labels.append(h('div', { class: 'rm-fill', 'aria-hidden': 'true' }));
  if (!dated.length) area.append(h('p', { class: 'rm-hint' }, 'Nothing here has dates yet. Give work a start or due date on its page and it appears here.'));
  area.append(arrows(items, width, rowsHigh));
  area.addEventListener('pointerdown', pointerDown);
  scale.addEventListener('pointerdown', startPan);

  parts.scroll.replaceChildren(inner);
  parts.scroll.scrollLeft = scrolled.left;
  parts.scroll.scrollTop = scrolled.top;
  if (wantToday) {
    wantToday = false;
    scrollToStart();
  }
  markPannable();
  sayCount(dated.length, rows.length - dated.length);
  if (focusedBar) parts.scroll.querySelector(`.rm-bar[data-ref="${focusedBar}"]`)?.focus();
  parts.scroll.dataset.ready = 'true';
}

// The empty grid shows the hand of a drag only while dragging it can move the timeline.
function markPannable() {
  const view = parts.scroll;
  view.classList.toggle('pannable', view.scrollWidth > view.clientWidth + 1 || view.scrollHeight > view.clientHeight + 1);
}

// The line under the controls: how many items are drawn, and how many are not because they have no
// dates, with the way to them (the Lists, filtered to the work with no dates). A filter that joins
// alternatives with OR cannot be added to, so then the list is of all the work with no dates.
function sayCount(drawn, undated) {
  const filter = params.get('q') || '';
  const query = [/\bOR\b/.test(filter) ? '' : filter, 'no:start no:target'].filter(Boolean).join(' ');
  put(
    parts.summary,
    `${plural(drawn, 'item')} on the timeline${filter ? ', for this filter' : ''}${picked.size ? `; ${picked.size} chosen to move together` : ''}.`,
    undated ? [` ${undated === 1 ? '1 has' : `${undated} have`} no dates and ${undated === 1 ? 'is' : 'are'} not drawn: `, viewLink('lists', { q: query }, { id: 'rm-undated' }, 'see them in Lists'), '.'] : null,
  );
}

// The ruler. At Month it is a cell for each day with its number, today's a pill; at Quarter a tick for each day, taller
// on Mondays and on the first of a month, with the number on Mondays; at Year only a tick for each week and each
// month, since a tick a day would be mud. The months (the years, at Year) are named in the tier above, and a name
// stays in view while any of its stretch is.
function drawScale(scale, origin, end, zoom, ppd, today) {
  const at = (day) => (day - origin) * ppd;
  const stretch = (from, until, words) => h('span', { class: 'rm-period', css: { left: `${at(from)}px`, width: `${at(until) - at(from)}px` } }, h('span', { class: 'rm-period-name' }, words));
  const name = (day, words) => h('span', { class: 'rm-tick', css: { left: `${at(day) + 3}px` } }, words);
  const mark = (day, kind) => h('span', { class: `rm-mark ${kind}`, css: { left: `${at(day)}px` } });
  const days = h('div', { class: 'rm-days', 'aria-hidden': 'true' });
  // A label that would sit under today's mark is left out, so the mark is never covered.
  const covered = (centre) => Math.abs(centre - (at(today) + ppd / 2)) < (zoom === 'year' ? 36 : 20);
  for (let month = origin; month <= end; month = nextMonth(month)) {
    if (zoom === 'year') {
      if (monthOf(month) === 0 || month === origin) {
        let until = nextMonth(month);
        while (monthOf(until) !== 0) until = nextMonth(until);
        scale.append(stretch(month, until, String(yearOf(month))));
      }
      days.append(mark(month, monthOf(month) % 3 === 0 ? 'quarter' : 'month'));
      if (!covered(at(month) + 16)) days.append(name(month, monthName(month)));
    } else {
      scale.append(stretch(month, nextMonth(month), `${monthName(month)} ${yearOf(month)}`));
      if (zoom === 'quarter') days.append(mark(month, 'month'));
    }
  }
  if (zoom === 'month') {
    for (let day = origin; day <= end; day += 1) days.append(h('span', { class: day === today ? 'rm-day today' : 'rm-day', dataset: { day: fromDay(day) } }, String(dayOfMonth(day))));
  } else {
    if (zoom === 'quarter') {
      for (let day = origin; day <= end; day += 1) {
        if (weekday(day) !== 1) continue;
        if (dayOfMonth(day) !== 1) days.append(mark(day, 'monday'));
        if (!covered(at(day) + 10)) days.append(name(day, String(dayOfMonth(day))));
      }
    }
    days.append(h('span', { class: 'rm-today-label', css: { left: `${at(today) + ppd / 2}px` } }, zoom === 'year' ? 'Today' : String(dayOfMonth(today))));
  }
  scale.append(days);
}

// The hover card of a status symbol: the status and what it means, in the words of the Board's key (labels.js).
const statusCard = (symbol, status) => hoverCard(symbol, [h('p', { class: 'hovercard-title' }, STATUS[status].label), h('p', {}, STATUS[status].hint)]);

function marks(node, editable, late) {
  const found = [];
  if (late) found.push(`${plural(late, 'day')} late`);
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
    { class: `rm-label kind-${node.kind}${mode === 'epic' && node.kind === 'epic' ? ' heads' : ''}`, css: { height: `${ROW}px`, paddingLeft: `${8 + depth * 14}px` }, dataset: { ref: node.ref, kind: node.kind } },
    h('span', { class: 'rm-kind', 'aria-hidden': 'true' }, kindIcon(node.kind, 14)),
    itemLink(node.ref, { class: 'rm-open', title: `${KIND[node.kind].label} ${code} ${node.title}` }, h('span', { class: 'id mono' }, code), ` ${node.title}`),
    node.schedule_conflicts.length
      ? h('span', { class: 'chip warn conflict', title: `It starts ${beforeDue(node.schedule_conflicts)}` }, icon('alert', { size: 12 }), h('span', { class: 'sr-only' }, 'Schedule conflict'))
      : null,
  );
}

function barFor(entry, spot) {
  const { node, s: first, e: last, editable } = entry;
  const { late } = spot;
  const found = marks(node, editable, late);
  const code = nodeCode(node.kind, node.number);
  const sized = sizeWords(node, { rollup: addsUp(node) });
  const words = `${KIND[node.kind].label} ${code}: ${node.title}. ${spanWords(first, last, geometry.today)}. ${STATUS[node.status].label}.${sized ? ` Size: ${sized}.` : ''}${found.length ? ` ${found.join('. ')}.` : ''}`;
  const chosen = picked.has(node.ref);
  // A bar with no room for its icon and words shows neither: the row's label says what it is, and
  // its status icon stands beside it, so nothing is drawn over the bar.
  const short = spot.width < SHORT_BAR;
  const free = movable(spot);
  const top = `${spot.y + BAR_INSET}px`;
  const height = `${ROW - 2 * BAR_INSET}px`;
  // The status symbol inside a bar has the hover card. Its empty title keeps the bar's own tooltip from showing over it too.
  const symbol = short ? null : h('span', { class: 'rm-icon', title: '', 'aria-hidden': 'true', dataset: { status: node.status } }, statusIcon(node.status, 13));
  const el = h(
    'div',
    {
      class: ['rm-bar', `kind-${node.kind}`, `status-${node.status}`, editable ? '' : 'worked-out', free ? 'movable' : '', short ? 'short' : '', spot.width < TINY_BAR ? 'tiny' : '', late ? 'late' : '', node.schedule_conflicts.length ? 'conflict' : '', chosen ? 'picked' : ''].filter(Boolean).join(' '),
      role: 'button',
      tabindex: 0,
      'aria-pressed': String(chosen),
      'aria-label': words,
      title: words,
      css: { left: `${spot.left}px`, width: `${spot.width}px`, top, height },
      dataset: { ref: node.ref, kind: node.kind, start: fromDay(first), end: fromDay(last), workedOut: String(!editable), late: String(late) },
      onkeydown: barKey,
    },
    free ? h('span', { class: 'rm-handle start', dataset: { handle: 'start' } }) : null,
    symbol,
    symbol ? statusCard(symbol, node.status) : null,
    short ? null : h('span', { class: 'rm-text' }, `${code} ${node.title}`),
    free ? h('span', { class: 'rm-handle end', dataset: { handle: 'end' } }) : null,
  );
  // Late work: a hatched tail from the end of the bar through today, a cell for each day, and the days said at its end.
  const barEnd = spot.left + spot.width;
  const todayEnd = (geometry.today + 1 - geometry.origin) * geometry.ppd;
  const tail = late && todayEnd > barEnd ? [h('span', { class: 'rm-late', 'aria-hidden': 'true', dataset: { ref: node.ref, late: String(late) }, css: { left: `${barEnd}px`, width: `${todayEnd - barEnd}px`, top, height } })] : [];
  const said = found.filter((text) => text !== 'Worked out from what is under it');
  const beside = `${Math.max(barEnd, late ? todayEnd : 0) + 4}px`;
  const note =
    short || said.length
      ? [
          h(
            'span',
            { class: `rm-note status-${node.status}${late ? ' overdue' : ''}`, 'aria-hidden': 'true', dataset: { ref: node.ref }, css: { left: beside, top, height } },
            short ? statusIcon(node.status, 13) : null,
            node.schedule_conflicts.length ? icon('alert', { size: 12 }) : null,
            said.join(' · '),
          ),
        ]
      : [];
  // The hover card of the symbol beside a short bar hangs from a transparent box over the symbol, so the note keeps only its own words.
  const over = short ? h('span', { class: 'rm-note-status', 'aria-hidden': 'true', dataset: { ref: node.ref, status: node.status }, css: { left: beside, top, height } }) : null;
  return [el, ...tail, ...note, ...(over ? [over, statusCard(over, node.status)] : [])];
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
  // Today a third of the way across what is in view beside the labels, so the days just gone, where
  // what is overdue is, are in view too, however narrow the window is.
  const labels = parts.scroll.querySelector('.rm-labels')?.offsetWidth ?? 0;
  const at = (geometry.today - geometry.origin) * geometry.ppd;
  parts.scroll.scrollLeft = Math.max(0, at - (parts.scroll.clientWidth - labels) / 3);
}

// Where the timeline opens: at today, or, when no work is anywhere near today, at the work nearest to
// it, so that it never opens on empty space.
function scrollToStart() {
  if (!geometry || !geometry.items.size) {
    scrollToToday();
    return;
  }
  const { today, origin, ppd, items } = geometry;
  const labels = parts.scroll.querySelector('.rm-labels')?.offsetWidth ?? 0;
  const days = (parts.scroll.clientWidth - labels) / ppd;
  const spans = [...items.values()];
  if (spans.some((item) => item.e >= today - days / 3 && item.s <= today + (days * 2) / 3)) {
    scrollToToday();
    return;
  }
  const away = (item) => (item.s > today ? item.s - today : today - item.e);
  const nearest = spans.reduce((best, item) => (away(item) < away(best) ? item : best));
  parts.scroll.scrollLeft = Math.max(0, (nearest.s - origin) * ppd - 16);
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
  if (mode === 'move' && picked.has(ref)) return [...picked].filter((other) => movable(geometry.items.get(other)));
  return movable(geometry.items.get(ref)) ? [ref] : [];
}

// A bar whose own dates can be changed by this person: it has dates of its own, and they may write.
const movable = (item) => Boolean(item?.editable) && can('write');

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
    // What stands beside the bar (its late tail and its marks) would be left behind, so it is put away until the change is made.
    for (const beside of parts.scroll.querySelectorAll(`.rm-late[data-ref="${ref}"], .rm-note[data-ref="${ref}"], .rm-note-status[data-ref="${ref}"]`)) beside.hidden = true;
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
        await call('update_node', { node: node.ref, expected_version: node.version, ...change });
        notify(`Moved “${node.title}”: ${describe(change)}.`);
      } catch (error) {
        if (!(error instanceof Refusal)) throw error;
        notify(error.message, { refusal: true });
      }
    } else {
      const changes = items.map((item) => ({ node: item.node.ref, expected_version: item.node.version, ...changeFor(item, mode, days) }));
      try {
        const outcome = await call('update_nodes', { changes });
        for (const result of outcome.results.filter((one) => !one.applied)) notify(`“${titleOf(result.node)}” was not moved. ${namesIn(result.refusal)}`, { refusal: true });
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
    // Marks beside a bar (its note and its late tail) are not the empty timeline: a drag that starts on one does nothing.
    if (!event.target.closest('.rm-note, .rm-note-status, .rm-late')) startPan(event);
    return;
  }
  if (event.button !== 0 || !geometry) return;
  const ref = el.dataset.ref;
  const item = geometry.items.get(ref);
  if (!item) return;
  const mode = movable(item) ? event.target.closest('.rm-handle')?.dataset.handle || 'move' : 'click';
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
      else openItem(ref);
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
  if (key === 'Enter') openItem(ref);
  else if (key === ' ') togglePicked(ref);
  else if (key === 'Escape') {
    picked = new Set();
    draw();
  } else if ((key === 'ArrowLeft' || key === 'ArrowRight') && movable(item)) {
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
