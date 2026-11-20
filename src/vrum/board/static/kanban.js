// The Kanban board: one column for each status, one lane for each epic. A card is a story or a task,
// and the Show switch (`show` in the address) picks the level: tasks (the default), stories, each
// with the progress of its tasks, or both together. A `kind:` term in the filter decides the level
// itself. Dragging a card to another column of its lane changes its status, and every card has a
// menu that does the same from the keyboard. Both ask the server, which decides; the board shows
// its answer.
// Above it is a filter box (`q`, the same filter language as list_nodes) that keeps only the cards it
// asks for. A card that waits for other work shows one word, Waiting, and what it waits for is in a
// hover card; a card with a handoff note says Handoff. A card, and the title of an epic's lane, open the page of that item: the title is a
// link (the way in by keyboard), and the rest of the card does the same for a pointer. The columns
// scroll sideways inside the board's own region when the window is too narrow for them, so the page
// itself never does.

import { Refusal, call } from './api.js';
import { can, itemLink, notify, openItem, ui, viewLink } from './ctx.js';
import { day, h, moment, plural, put } from './dom.js';
import { openCreate } from './create.js';
import { filterBar, loadRows } from './filterbar.js';
import { hoverCard } from './hovercard.js';
import { icon } from './icons.js';
import { keyButton } from './key-button.js';
import { KIND, SIZES, SIZE_POINTS, STATUS, STATUSES, actorLabel, byRef, checksChip, codeOf, criteriaChip, itemSizeChip, kindIcon, noSizeChip, nodeCode, statusIcon } from './labels.js';
import { priorityChip, statusChip } from './status-menu.js';
import { epicOf, epics, store, sync, titleOf } from './store.js';

// The levels the Board can show. Tasks come first because that is the work people do; Stories is the
// planning view, each story with the progress of its tasks; Both is every story and task together.
const DEFAULT_LEVEL = 'tasks';
const LEVELS = {
  tasks: { label: 'Tasks', kinds: ['task'], hint: 'Show the tasks, each under its story' },
  stories: { label: 'Stories', kinds: ['story'], hint: 'Show the stories, each with the progress of its tasks' },
  both: { label: 'Both', kinds: ['story', 'task'], hint: 'Show stories and tasks together' },
};
const levelOf = (text) => (Object.hasOwn(LEVELS, text) ? text : DEFAULT_LEVEL);
const NONE_YET = { tasks: 'No tasks yet.', stories: 'No stories yet.', both: 'No stories or tasks yet.' };
const NONE_MATCH = { tasks: 'No task in this epic matches the filter.', stories: 'No story in this epic matches the filter.', both: 'No story or task in this epic matches the filter.' };
// A filter that names a kind (kind:story, -kind:task) says what it wants to see, so the switch gives way.
const NAMES_KIND = /(?:^|\s)-?kind:/i;

let root = null;
let frame = null; // the board with its fading edges
let hint = null; // the arrows that scroll the columns, shown only while some of them are out of sight
let body = null; // the region with the columns, which scrolls sideways
let bar = null; // the filter box
let parts = {}; // the epic choice and the Show switch
let found = null; // the refs the filter asks for, or null when there is no filter
let reading = false; // the filter is being read for the first time
let params = new URLSearchParams();
let shownLevel = DEFAULT_LEVEL; // the level the cards are drawn at now
let lastLevel = DEFAULT_LEVEL; // the level the person chose last, so that coming back to the Board finds it as left
let dragging = false;
let looking = 0; // the filter being read
let watch = null;
const collapsed = new Set(); // the refs of the epics whose lanes are folded away
const handedOff = new Set(); // the refs of the work that has a handoff note
let handoffsFrom = 0; // the news read for them so far
let learning = false;

const PRIORITY_ORDER = { urgent: 0, high: 1, normal: 2, low: 3 };
const byPriority = (a, b) => PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority] || byRef(a.ref, b.ref);

// An id for an element built from a ref: the colon becomes a dash.
const dashed = (ref) => ref.replace(':', '-');

export function mount(element, initial) {
  root = element;
  params = initial;
  // Coming back to the Board with nothing in the address about the level finds it as it was left,
  // and the address says so.
  if (!params.has('show') && lastLevel !== DEFAULT_LEVEL) {
    params = new URLSearchParams(initial);
    params.set('show', lastLevel);
    ui.navigate({ show: lastLevel }, { replace: true });
  }
  lastLevel = chosenLevel();
  found = null;
  reading = Boolean(params.get('q'));
  bar = filterBar({ value: params.get('q') || '', onSubmit: (text) => ui.navigate({ q: text || null }), note: FILTER_NOTE });
  const select = h('select', { id: 'epic-filter', onchange: () => ui.navigate({ epic: select.value || null }) });
  fillEpicChoices(select);
  const show = showSwitch();
  show.element.append(keyButton(keyContent(), { id: 'board-key-button' }));
  parts = {
    select,
    show: show.group,
    showNote: show.note,
    hidden: h('p', { class: 'board-hidden', id: 'board-hidden', role: 'status', hidden: true }),
  };
  body = h('div', { class: 'board', id: 'board', role: 'region', tabindex: 0, 'aria-label': 'Board columns, one lane for each epic', onscroll: seeScroll, ondragover: autoscroll });
  hint = scrollHint();
  frame = h('div', { class: 'board-frame' }, h('div', { class: 'board-viewport' }, body));
  put(
    root,
    h(
      'div',
      { class: 'board-page' },
      header(),
      h('div', { class: 'board-toolbar' }, bar.form, h('div', { class: 'board-select' }, h('label', { for: 'epic-filter', class: 'sr-only' }, 'Epic'), select), show.element),
      bar.problem,
      parts.hidden,
      h('div', { class: 'board-notes' }, hint),
      frame,
    ),
  );
  watch = new ResizeObserver(seeScroll);
  watch.observe(body);
  draw();
  look();
  learnHandoffs();
}

export function unmount() {
  watch?.disconnect();
  watch = null;
  looking += 1;
  root = null;
  frame = null;
  hint = null;
  body = null;
  parts = {};
}

export function update(next) {
  const before = params;
  params = next;
  if (!root) return;
  parts.select.value = params.get('epic') || '';
  lastLevel = chosenLevel();
  const sameLevel = levelOf(before.get('show')) === lastLevel;
  if ((next.get('q') || '') !== (before.get('q') || '')) {
    bar.setValue(params.get('q') || '');
    look();
  } else if (!sameLevel || (next.get('epic') || '') !== (before.get('epic') || '')) draw();
}

export function refresh(news) {
  if (!root || dragging) return;
  // The chips that call this pass what they changed, which is not news.
  if (Array.isArray(news)) for (const event of news) if (event.action === 'handoff' && event.node) handedOff.add(event.node);
  fillEpicChoices(parts.select);
  if (params.get('q')) look();
  else draw();
}

// A list of work does not say which items have a handoff note, so the change feed is read for them,
// once, from the start, and after that the news adds to it.
async function learnHandoffs() {
  if (learning) return;
  learning = true;
  try {
    let after = handoffsFrom;
    let latest = after;
    for (;;) {
      const page = await call('changes_since', { after_event_id: after, limit: 200 });
      for (const event of page.items) if (event.action === 'handoff' && event.node) handedOff.add(event.node);
      latest = page.latest_event_id;
      if (page.next_after_id === null || page.next_after_id === undefined) break;
      after = page.next_after_id;
    }
    handoffsFrom = Math.max(handoffsFrom, latest);
  } catch {
    // It only decorates the cards: without it they have no sign, and nothing else is wrong.
  } finally {
    learning = false;
  }
  if (root && !dragging) draw();
}

// -- the level ----------------------------------------------------------------------------------------

const FILTER_NOTE = 'The Show switch above the board chooses whether the cards are tasks, stories or both. A kind: term in the filter decides that itself.';

// The level the person chose, from the address (tasks when it says nothing).
const chosenLevel = () => levelOf(params.get('show'));

// The level the cards are drawn at, and whether the filter set it: a filter that names a kind shows
// the kinds it finds, so that it never lands on an empty board.
function levelNow() {
  if (!NAMES_KIND.test(params.get('q') || '')) return { level: chosenLevel(), forced: false };
  const kinds = new Set();
  for (const ref of found || []) kinds.add(store.nodes.get(ref)?.kind);
  const level = kinds.has('story') && kinds.has('task') ? 'both' : kinds.has('story') ? 'stories' : kinds.has('task') ? 'tasks' : chosenLevel();
  return { level, forced: true };
}

// The stories and tasks the filter finds that the level leaves out, in the epic chosen if there is one.
function leftOut(level, only) {
  const left = { story: 0, task: 0 };
  for (const ref of found || []) {
    const node = store.nodes.get(ref);
    if (!node || node.kind === 'epic' || LEVELS[level].kinds.includes(node.kind)) continue;
    if (only && String(epicOf(node)?.number) !== only) continue;
    left[node.kind] += 1;
  }
  return left;
}

function chooseLevel(value) {
  ui.navigate({ show: value === DEFAULT_LEVEL ? null : value }, { replace: true });
}

// "Show: Tasks | Stories | Both": one choice out of three, a radio group the arrow keys move around in.
function showSwitch() {
  const order = Object.keys(LEVELS);
  const note = h('span', { class: 'board-show-note', id: 'board-show-note', hidden: true }, 'The filter picks the kind of card.');
  const group = h(
    'div',
    {
      class: 'segmented board-show-choice',
      id: 'board-show',
      role: 'radiogroup',
      'aria-labelledby': 'board-show-label',
      'aria-describedby': 'board-show-note',
      onkeydown: (event) => {
        const at = order.indexOf(event.target.dataset?.value);
        const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
        const to = at < 0 ? null : step ? order[(at + step + order.length) % order.length] : event.key === 'Home' ? order[0] : event.key === 'End' ? order[order.length - 1] : null;
        if (!to) return;
        event.preventDefault();
        group.querySelector(`[data-value="${to}"]`).focus();
        chooseLevel(to);
      },
    },
    order.map((value) =>
      h('button', { type: 'button', role: 'radio', id: `board-show-${value}`, title: LEVELS[value].hint, dataset: { value }, 'aria-checked': 'false', tabindex: -1, onclick: () => chooseLevel(value) }, LEVELS[value].label),
    ),
  );
  const element = h('div', { class: 'board-show' }, h('span', { class: 'board-show-label', id: 'board-show-label' }, 'Show'), group, note);
  return { element, group, note };
}

// The switch shows the level the cards are at. While the filter decides it, the switch is for looking only.
function syncSwitch(level, forced) {
  if (!parts.show) return;
  for (const button of parts.show.querySelectorAll('button')) {
    const on = button.dataset.value === level;
    button.setAttribute('aria-checked', String(on));
    button.tabIndex = on ? 0 : -1;
    button.disabled = forced;
  }
  parts.show.setAttribute('aria-disabled', String(forced));
  parts.showNote.hidden = !forced;
}

// A line under the filter for matches the level leaves out, with the way to see them.
function sayLeftOut(level, left) {
  const kind = level === 'tasks' ? 'story' : level === 'stories' ? 'task' : null;
  const count = kind ? left[kind] : 0;
  put(
    parts.hidden,
    count
      ? [
          `${kind === 'story' ? plural(count, 'story', 'stories') : plural(count, 'task')} also ${count === 1 ? 'matches' : 'match'} this filter. `,
          h('button', { type: 'button', class: 'link', id: 'show-both', onclick: () => chooseLevel('both') }, 'Show stories & tasks'),
        ]
      : null,
  );
  parts.hidden.hidden = !count;
}

// -- the top of the page ---------------------------------------------------------------------------

function fillEpicChoices(select) {
  const chosen = params.get('epic') || '';
  select.replaceChildren(
    h('option', { value: '' }, 'All epics'),
    ...epics().map((epic) => h('option', { value: epic.number }, `${nodeCode(epic.kind, epic.number)} ${epic.title}`)),
  );
  select.value = chosen;
}

function header() {
  return h(
    'header',
    { class: 'page-header' },
    h('div', { class: 'page-title' }, h('h1', {}, 'Board')),
    h(
      'div',
      { class: 'page-actions' },
      viewLink('coverage', {}, { id: 'show-coverage', class: 'link-button' }, icon('rule', { size: 16 }), 'Coverage'),
      can('write') ? h('button', { type: 'button', class: 'primary', id: 'new-item', onclick: () => openCreate({ kind: 'epic' }) }, icon('plus', { size: 16 }), 'New item') : null,
    ),
  );
}

// The arrows beside the drag hint, shown only while some of the columns are out of sight. The words
// are for a screen reader and a tooltip: the fading edge of the board and the arrows say it too.
function scrollHint() {
  const go = (way) => body.scrollBy({ left: way * Math.max(body.clientWidth * 0.8, 200), behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  return h(
    'div',
    { class: 'scroll-hint', hidden: true, title: 'More columns are out of sight. Scroll sideways, or use the arrows.' },
    h('span', { class: 'sr-only' }, 'More columns are out of sight. Scroll sideways, or use the arrows.'),
    h('button', { type: 'button', class: 'small icon-only flip', 'aria-label': 'Scroll the columns left', onclick: () => go(-1) }, icon('chevron_right', { size: 16 })),
    h('button', { type: 'button', class: 'small icon-only', 'aria-label': 'Scroll the columns right', onclick: () => go(1) }, icon('chevron_right', { size: 16 })),
  );
}

// Dragging a card near the left or right edge scrolls the columns, so one out of sight can be reached.
function autoscroll(event) {
  if (!dragging || !body) return;
  const edge = body.getBoundingClientRect();
  if (event.clientX < edge.left + 64) body.scrollLeft -= 16;
  else if (event.clientX > edge.right - 64) body.scrollLeft += 16;
}

function seeScroll() {
  if (!body || !frame) return;
  const over = body.scrollWidth > body.clientWidth + 1;
  frame.dataset.overflow = String(over);
  if (hint) hint.hidden = !over;
  frame.dataset.left = String(over && body.scrollLeft > 1);
  frame.dataset.right = String(over && body.scrollLeft + body.clientWidth < body.scrollWidth - 1);
  // A lane's header stays as wide as what is in view, so its buttons never scroll out of reach.
  body.style.setProperty('--view-w', `${body.clientWidth}px`);
}

// -- the filter -----------------------------------------------------------------------------------------

// The cards the filter asks for, from the server, then the board is drawn from them.
async function look() {
  const mine = ++looking;
  const text = params.get('q') || '';
  if (!text) {
    found = null;
    reading = false;
    bar.setProblem('');
    draw();
    return;
  }
  const rows = await loadRows(text, bar);
  if (mine !== looking || !root) return;
  if (rows !== null) found = new Set(rows.map((node) => node.ref));
  reading = false;
  draw();
}

// -- drawing the lanes --------------------------------------------------------------------------

function draw() {
  if (!body) return;
  const focused = document.activeElement?.dataset?.focusKey;
  const only = params.get('epic');
  const { level, forced } = levelNow();
  shownLevel = level;
  const kinds = LEVELS[level].kinds;
  const kept = (node) => found === null || found.has(node.ref);
  const shown = (node) => kinds.includes(node.kind) && kept(node);
  const inEpic = new Map();
  for (const node of store.nodes.values()) {
    if (node.kind === 'epic') continue;
    const epic = epicOf(node);
    if (epic) inEpic.set(epic.ref, [...(inEpic.get(epic.ref) || []), node]);
  }
  const lanes = epics()
    .filter((epic) => (!only || String(epic.number) === only) && (found === null || kept(epic) || (inEpic.get(epic.ref) || []).some(shown)))
    .map((epic) => [epic, (inEpic.get(epic.ref) || []).filter(shown), inEpic.get(epic.ref) || []]);
  const counts = Object.fromEntries(STATUSES.map((status) => [status, 0]));
  let total = 0;
  for (const [, items] of lanes) {
    for (const node of items) counts[node.status] += 1;
    total += items.length;
  }
  const left = leftOut(level, only);
  syncSwitch(level, forced);
  sayLeftOut(level, left);
  if (reading) put(body, h('p', { class: 'loading', id: 'board-loading', role: 'status' }, 'Loading…'));
  else if (!lanes.length) put(body, emptyState(only, level, left));
  else put(body, h('div', { class: 'board-inner' }, columnHeads(counts), ...lanes.map(([epic, items, all]) => lane(epic, items, all))));
  seeScroll();
  if (focused) body.querySelector(`[data-focus-key="${focused}"]`)?.focus();
}

function emptyState(only, level, left) {
  const unseen = level === 'tasks' ? left.story : level === 'stories' ? left.task : 0;
  const [title, more] = only
    ? ['That epic is not there, or nothing in it matches the filter.', 'Choose another epic, or clear the filter.']
    : found !== null
      ? [
          unseen ? `No ${level === 'tasks' ? 'task' : 'story'} matches this filter.` : 'Nothing matches this filter.',
          'Change the words, or clear the filter to see everything.',
        ]
      : ['Nothing here yet.', 'Create an epic, or ask an agent to make a plan.'];
  // A project with no work at all is the first thing a new person sees: say what to do next.
  const first = !only && found === null;
  return h(
    'div',
    { class: 'empty', id: 'board-empty' },
    icon('epic', { size: 24 }),
    h('p', { class: 'empty-text' }, h('strong', {}, title), ' ', more),
    first
      ? h(
          'div',
          { class: 'empty-actions' },
          can('write') ? h('button', { type: 'button', class: 'primary', id: 'empty-new-epic', onclick: () => openCreate({ kind: 'epic' }) }, icon('plus', { size: 16 }), 'Create the first epic') : null,
          can('manage') ? viewLink('users', {}, { id: 'empty-connect-agent' }, 'Connect an agent') : null,
        )
      : null,
  );
}

function columnHeads(counts) {
  return h(
    'div',
    { class: 'column-heads', role: 'presentation' },
    STATUSES.map((status) =>
      h(
        'div',
        { class: `column-head status-${status}`, 'data-status': status, title: STATUS[status].hint },
        statusIcon(status, 16),
        h('strong', {}, STATUS[status].label),
        h('span', { class: 'column-count' }, String(counts[status])),
      ),
    ),
  );
}

// Progress as it is: the tasks done out of the tasks there are.
function meter(done, total, label) {
  const percent = total ? Math.round((done / total) * 100) : 0;
  return h(
    'span',
    { class: 'meter', role: 'progressbar', 'aria-label': label, 'aria-valuemin': 0, 'aria-valuemax': Math.max(total, 1), 'aria-valuenow': done },
    h('span', { css: { width: `${percent}%` } }),
  );
}

const tasksDone = (done, total) => `${done} of ${total} ${total === 1 ? 'task' : 'tasks'} done`;

function toggleLane(epic) {
  if (collapsed.has(epic.ref)) collapsed.delete(epic.ref);
  else collapsed.add(epic.ref);
  draw();
}

// What an epic's lane says when it has no card at this level. When the other level has some, the
// way to them is one click away.
function emptyLane(all) {
  if (found !== null) return [NONE_MATCH[shownLevel]];
  const other = shownLevel === 'tasks' ? 'story' : shownLevel === 'stories' ? 'task' : null;
  const count = other ? all.filter((node) => node.kind === other).length : 0;
  if (!count) return [NONE_YET[shownLevel]];
  const [words, to] = other === 'story' ? [plural(count, 'story', 'stories'), 'stories'] : [plural(count, 'task'), 'tasks'];
  return [`${NONE_YET[shownLevel]} `, h('button', { type: 'button', class: 'link', onclick: () => chooseLevel(to) }, `Show its ${words}`)];
}

function lane(epic, items, all) {
  const total = epic.progress_total ?? 0;
  const done = epic.progress_done ?? 0;
  const open = !collapsed.has(epic.ref);
  const cellsId = `lane-cells-${dashed(epic.ref)}`;
  const code = nodeCode(epic.kind, epic.number);
  const head = h(
    'header',
    { class: 'lane-head' },
    h(
      'button',
      {
        type: 'button',
        class: 'lane-toggle ghost icon-only small',
        'aria-expanded': String(open),
        'aria-controls': cellsId,
        'aria-label': `${open ? 'Fold away' : 'Show'} epic ${epic.title}`,
        'data-focus-key': `fold-${epic.ref}`,
        onclick: () => toggleLane(epic),
      },
      icon(open ? 'chevron_down' : 'chevron_right', { size: 16 }),
    ),
    h('span', { class: 'lane-icon' }, icon('epic', { size: 18 })),
    h('h2', { class: 'lane-heading' }, itemLink(epic.ref, { class: 'lane-title', 'data-focus-key': epic.ref }, epic.title)),
    itemLink(epic.ref, { class: 'id mono lane-code', tabindex: -1, 'aria-hidden': 'true' }, code),
    h(
      'span',
      { class: 'lane-meta' },
      statusChip(epic, { onChanged: refresh }),
      epic.priority !== 'normal' ? priorityChip(epic, { onChanged: refresh }) : null,
      epic.target_on ? h('span', { class: 'chip due' }, icon('calendar', { size: 13 }), `Due ${day(epic.target_on)}`) : null,
      epic.overdue ? h('span', { class: 'chip danger overdue' }, icon('alert', { size: 13 }), 'Overdue') : null,
      itemSizeChip(epic),
    ),
    h('span', { class: 'lane-progress' }, h('span', { class: 'progress-text' }, tasksDone(done, total)), meter(done, total, `Tasks done in ${epic.title}`)),
    can('write') ? h('button', { type: 'button', class: 'add-story small', onclick: () => openCreate({ kind: 'story', parent: epic.ref }) }, icon('plus', { size: 14 }), 'New story') : null,
  );
  const cells = items.length
    ? h('div', { class: 'cells', id: cellsId, hidden: !open }, STATUSES.map((status) => cell(epic, status, items.filter((node) => node.status === status).sort(byPriority))))
    : h('p', { class: 'lane-empty', id: cellsId, hidden: !open }, ...emptyLane(all));
  return h('section', { class: `lane${open ? '' : ' folded'}`, 'aria-label': `Epic ${epic.title}`, 'data-epic': epic.ref, 'data-ref': epic.ref }, head, cells);
}

function cell(epic, status, cards) {
  const place = h(
    'div',
    { class: `cell status-${status}`, role: 'group', 'aria-label': `${STATUS[status].label} in ${epic.title}`, 'data-status': status, 'data-epic': epic.ref },
    cards.length ? h('ul', { class: 'cards' }, cards.map((node) => h('li', {}, card(node)))) : h('p', { class: 'cell-empty sr-only' }, 'No items'),
  );
  place.addEventListener('dragover', (event) => {
    const ref = event.dataTransfer?.types.includes('application/x-vrum-node') ? dragged : '';
    if (ref && epicOf(store.nodes.get(ref))?.ref === epic.ref) {
      event.preventDefault();
      place.classList.add('over');
    }
  });
  place.addEventListener('dragleave', () => place.classList.remove('over'));
  place.addEventListener('drop', (event) => {
    event.preventDefault();
    place.classList.remove('over');
    const node = store.nodes.get(dragged);
    dragging = false;
    if (node && node.status !== status) setStatus(node, status);
    else if (node && node.status === status) refresh();
  });
  return place;
}

// -- the key ---------------------------------------------------------------------------------------------

// What the Key button opens: the six statuses and the signs a card can carry. The signs are the very chips the cards
// draw (same classes, same icons), so the key cannot drift from them.

function keyContent() {
  const item = (picture, ...words) => h('li', {}, h('span', { class: 'bk-mark', 'aria-hidden': 'true' }, picture), h('span', { class: 'bk-words' }, ...words));
  const group = (title, className, ...items) => h('div', { class: `bk-group ${className}` }, h('h3', {}, title), h('ul', { class: 'plain' }, items));
  const sizes = SIZES.map((name, at) => [at ? ', ' : '', h('span', { class: 'nowrap' }, `${name.toUpperCase()} ${SIZE_POINTS[name]}`)]);
  return h(
    'div',
    { class: 'bk-card' },
    group('Status', 'is-status', STATUSES.map((status) => item(h('span', { class: `bk-status status-${status}` }, statusIcon(status, 16)), h('strong', {}, STATUS[status].label), h('span', { class: 'quiet' }, STATUS[status].hint)))),
    group(
      'On a card',
      '',
      item(h('span', { class: 'chip waiting' }, icon('clock', { size: 13 }), 'Waiting'), 'Needs work that is not done yet'),
      item(h('span', { class: 'chip handoff-sign' }, icon('handoff', { size: 13 }), 'Handoff'), 'Has a handoff note'),
      item(h('span', { class: 'chip criteria' }, icon('rule', { size: 13 }), '2/3 criteria'), 'Acceptance criteria met'),
      item(h('span', { class: 'chip checks' }, icon('checklist', { size: 13 }), '4/6 checks'), 'Definition of done ticked'),
      item(h('span', { class: 'chip size size-m' }, 'M'), 'Size, in points: ', sizes),
      item(h('span', { class: 'chip size size-rollup' }, icon('size', { size: 13 }), '14 pts'), 'What a story or epic adds up to'),
      item(h('span', { class: 'chip no-size' }, 'No size'), 'Not sized yet'),
      item(h('span', { class: 'chip danger overdue' }, icon('alert', { size: 13 }), 'Overdue'), 'Past its due day'),
      item(h('span', { class: 'bk-lock' }, icon('lock', { size: 14 })), 'Held by someone, until the time shown'),
    ),
  );
}

// -- a card ---------------------------------------------------------------------------------------

let dragged = ''; // the ref of the card being dragged

const note = (kind, glyph, ...words) => h('p', { class: `card-note ${kind}` }, icon(glyph, { size: 14 }), h('span', {}, ...words));

// A card that waits for other work shows one word, Waiting, and what it waits for is in a hover card: on
// the pointer's hover, on the keyboard's focus and on a tap. (Blocked is a status, which a person sets;
// a card in the Blocked column says Blocked.) The chip is not a button (it does nothing but explain),
// so a click on it stays here and a drag that starts on it moves the card. A card that is done or
// dropped says so; the column it sits in is where it is changed.
function doneChip(node) {
  return h('span', { class: `chip status status-${node.status}`, title: STATUS[node.status].hint, 'data-status': node.status }, statusIcon(node.status, 14), STATUS[node.status].label);
}

function waitingChip(node) {
  if (!node.waiting_on.length) return null;
  const chip = h(
    'span',
    { class: 'chip waiting card-waiting', tabindex: 0, dataset: { focusKey: `waiting-${node.ref}` }, onclick: (event) => event.stopPropagation() },
    icon('clock', { size: 13 }),
    'Waiting',
  );
  const waits = node.waiting_on;
  const card = hoverCard(chip, [
    h('p', { class: 'hovercard-title' }, `Waiting for ${plural(waits.length, 'item')}`),
    h(
      'ul',
      { class: 'plain hovercard-list' },
      waits.map((ref) => h('li', {}, h('span', { class: 'id mono' }, codeOf(ref)), ' ', itemLink(ref, { tabindex: -1 }, titleOf(ref)))),
    ),
  ]);
  return [chip, card];
}

function claimNote(node) {
  if (node.claimed_by) return note('claim', 'lock', 'Held by ', actorLabel(node.claimed_by, node.claimed_by_kind), ` until ${moment(node.claim_expires_at)}`);
  if (node.lapsed_claim_by) return note('claim lapsed', 'alert', actorLabel(node.lapsed_claim_by, node.lapsed_claim_by_kind), '’s claim ran out');
  return null;
}

function card(node) {
  const parent = store.nodes.get(node.parent);
  const code = nodeCode(node.kind, node.number);
  const shut = folded.has(node.ref);
  // A story shows how many of its tasks are done, as a bar; with no tasks there is nothing to count.
  const context = node.progress_total
    ? h('div', { class: 'card-context progress-line' }, h('span', {}, tasksDone(node.progress_done, node.progress_total)), meter(node.progress_done, node.progress_total, 'Tasks done'))
    : node.progress_total === 0
      ? h('p', { class: 'card-context' }, 'No tasks yet')
      : parent
        ? h('p', { class: 'card-context' }, 'Under: ', h('span', { class: 'id mono' }, nodeCode(parent.kind, parent.number)), ` ${parent.title}`)
        : h('p', { class: 'card-context' });
  const element = h(
    'article',
    {
      class: `card kind-${node.kind} status-${node.status}${shut ? ' folded' : ''}`,
      draggable: can('write') ? 'true' : 'false',
      dataset: { ref: node.ref, kind: node.kind, number: node.number, status: node.status },
      'aria-label': `${KIND[node.kind].label} ${node.number}: ${node.title}. ${STATUS[node.status].label}.`,
      // The title is the link for the keyboard and for assistive technology; the rest of the card
      // does the same for a pointer, except where a click means something else.
      onclick: (event) => {
        const selected = getSelection();
        if (event.target.closest('a, button') || (selected && !selected.isCollapsed && element.contains(selected.anchorNode))) return;
        openItem(node.ref);
      },
      ondragstart: (event) => {
        dragged = node.ref;
        dragging = true;
        event.dataTransfer.setData('application/x-vrum-node', node.ref);
        event.dataTransfer.setData('text/plain', code);
        event.dataTransfer.effectAllowed = 'move';
        element.classList.add('dragging');
      },
      ondragend: () => {
        dragging = false;
        element.classList.remove('dragging');
      },
    },
    h(
      'div',
      { class: 'card-top' },
      h('span', { class: 'card-kind', title: KIND[node.kind].label }, kindIcon(node.kind, 14)),
      h('span', { class: 'id mono' }, code),
      h(
        'button',
        { type: 'button', class: 'fold small icon-only ghost', title: foldWords(code, shut), 'aria-label': foldWords(code, shut), 'aria-expanded': String(!shut), 'data-focus-key': `foldcard-${node.ref}`, onclick: (event) => {
          // The icon is replaced below, so the card could no longer tell that a button was clicked.
          event.stopPropagation();
          foldCard(node, element, event.currentTarget);
        } },
        icon(shut ? 'chevron_right' : 'chevron_down', { size: 16 }),
      ),
    ),
    h('h3', { class: 'card-title' }, itemLink(node.ref, { 'data-focus-key': node.ref, draggable: 'false' }, node.title)),
    h(
      'div',
      { class: 'chips card-chips' },
      priorityChip(node, { onChanged: refresh }),
      itemSizeChip(node),
      noSizeChip(node),
      node.target_on ? h('span', { class: 'chip due' }, icon('calendar', { size: 13 }), `Due ${day(node.target_on)}`) : null,
      node.overdue ? h('span', { class: 'chip danger overdue' }, icon('alert', { size: 13 }), 'Overdue') : null,
      node.schedule_conflicts.length
        ? h('span', { class: 'chip warn conflict', title: `Planned to start before ${node.schedule_conflicts.map(codeOf).join(', ')} is due` }, icon('alert', { size: 13 }), 'Schedule conflict')
        : null,
      node.status === 'done' || node.status === 'dropped' ? doneChip(node) : waitingChip(node),
      handedOff.has(node.ref) ? h('span', { class: 'chip handoff-sign', title: 'It has a handoff note: see its page' }, icon('handoff', { size: 13 }), 'Handoff') : null,
      criteriaChip(node),
      checksChip(node, { quietWhenDone: true }),
    ),
    context,
    claimNote(node),
  );
  return element;
}

// -- folding a card away ----------------------------------------------------------------------------

// The cards the person has folded down to their code and title. Kept across redraws (news, a
// status change), not across a reload.
const folded = new Set();

const foldWords = (code, shut) => `${shut ? 'Show' : 'Fold away'} ${code}`;

function foldCard(node, element, button) {
  const shut = !folded.has(node.ref);
  if (shut) folded.add(node.ref);
  else folded.delete(node.ref);
  const code = nodeCode(node.kind, node.number);
  element.classList.toggle('folded', shut);
  button.setAttribute('aria-expanded', String(!shut));
  button.setAttribute('aria-label', foldWords(code, shut));
  button.title = foldWords(code, shut);
  put(button, icon(shut ? 'chevron_right' : 'chevron_down', { size: 16 }));
}

// -- changing a status ------------------------------------------------------------------------------

async function setStatus(node, status) {
  // Blocked and dropped ask for a reason first, in a dialog that makes the change. Closing it leaves the card where it was.
  if (status === 'blocked' || status === 'dropped') {
    const { whyDialog } = await import('./record-forms.js');
    whyDialog(node, status, {
      save: async (why) => {
        try {
          await call('update_node', { node: node.ref, expected_version: node.version, status, ...(why ? { why } : {}) });
        } catch (error) {
          if (error instanceof Refusal) await sync().catch(() => {});
          throw error;
        }
        notify(`${nodeCode(node.kind, node.number)} is now ${STATUS[status].label.toLowerCase()}.`);
      },
      onDone: async () => {
        await sync();
        refresh();
      },
      onCancel: () => refresh(),
    });
    return;
  }
  try {
    await call('update_node', { node: node.ref, expected_version: node.version, status });
    notify(`${nodeCode(node.kind, node.number)} is now ${STATUS[status].label.toLowerCase()}.`);
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
  }
  await sync();
  refresh();
}

