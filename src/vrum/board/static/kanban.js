// The Kanban board: one column for each status, one lane for each epic. A card is a story or a task.
// Dragging a card to another column of its lane changes its status, and every card has a menu that
// does the same from the keyboard. Both ask the server, which decides; the board shows its answer.
// Above it is a row of what needs attention (each number is the answer of the same filter or
// report an agent can ask for, and a link to the cards it counts), and a filter box (`q`, the same
// filter language as list_nodes) that keeps only the cards it asks for. A card opens in the
// detail panel (`sel=<ref>`, such as `sel=story:12`). The columns scroll sideways inside the board's own region when
// the window is too narrow for them, so the page itself never does.

import { Refusal, call } from './api.js';
import { actor, notify, parseSel, ui, viewLink } from './ctx.js';
import { day, h, moment, plural, put } from './dom.js';
import { openCreate } from './create.js';
import { filterBar, loadRows } from './filterbar.js';
import { icon } from './icons.js';
import { KIND, SETTABLE, STATUS, STATUSES, byRef, codeOf, columnNote, kindIcon, nodeCode, priorityChip, statusChip, statusIcon } from './labels.js';
import { epicOf, epics, store, sync, titleOf } from './store.js';

// What "ready work nobody holds" means: the filter that gives exactly the tool's answer.
const FREE = 'kind:story,task status:ready -claimed';
const TILE_ICONS = { free: 'ready', blocked: 'blocked', overdue: 'clock', lapsed: 'alert', unplaced: 'rule', bare: 'story' };
const DRAG_HELP = 'Drag a card to another column of its own epic to change its status, or use its Move menu. Ready is worked out from the links, so nothing can be dropped there.';

let root = null;
let frame = null; // the board with its fading edges
let hint = null; // the arrows that scroll the columns, shown only while some of them are out of sight
let body = null; // the region with the columns, which scrolls sideways
let strip = null; // where the numbers that need attention are
let bar = null; // the filter box
let parts = {}; // the count and the epic choice
let found = null; // the refs the filter asks for, or null when there is no filter
let reading = false; // the filter is being read for the first time
let params = new URLSearchParams();
let dragging = false;
let looking = 0; // the filter being read
let counting = 0; // the numbers being read
let clock = null;
let watch = null;
const collapsed = new Set(); // the refs of the epics whose lanes are folded away

const PRIORITY_ORDER = { urgent: 0, high: 1, normal: 2, low: 3 };
const byPriority = (a, b) => PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority] || byRef(a.ref, b.ref);

// An id for an element built from a ref: the colon becomes a dash.
const dashed = (ref) => ref.replace(':', '-');

export function mount(element, initial) {
  root = element;
  params = initial;
  found = null;
  reading = Boolean(params.get('q'));
  bar = filterBar({ value: params.get('q') || '', onSubmit: (text) => ui.navigate({ q: text || null }) });
  const select = h('select', { id: 'epic-filter', onchange: () => ui.navigate({ epic: select.value || null }) });
  fillEpicChoices(select);
  parts = { select, count: h('p', { class: 'board-count', id: 'board-count' }) };
  strip = h('section', { id: 'attention', class: 'attention', 'aria-label': 'What needs attention' }, h('p', { class: 'attention-note' }, 'Reading the numbers…'));
  body = h('div', { class: 'board', id: 'board', role: 'region', tabindex: 0, 'aria-label': 'Board columns, one lane for each epic', onscroll: seeScroll, ondragover: autoscroll });
  hint = scrollHint();
  frame = h('div', { class: 'board-frame' }, h('div', { class: 'board-viewport' }, body));
  put(
    root,
    h(
      'div',
      { class: 'board-page' },
      header(),
      strip,
      h('div', { class: 'board-toolbar' }, bar.form, h('div', { class: 'board-select' }, h('label', { for: 'epic-filter', class: 'sr-only' }, 'Epic'), select), parts.count),
      bar.problem,
      h('div', { class: 'board-notes' }, bar.help, h('p', { class: 'board-help', title: DRAG_HELP }, DRAG_HELP), hint),
      frame,
    ),
  );
  watch = new ResizeObserver(seeScroll);
  watch.observe(body);
  draw();
  look();
  count();
  // A claim that runs out or a day that ends changes a number with nothing written, so the
  // numbers are read again every so often as well as when something changes.
  clock = setInterval(count, Math.max(10, store.info.poll_seconds * 3) * 1000);
}

export function unmount() {
  closeMenu();
  clearInterval(clock);
  clock = null;
  watch?.disconnect();
  watch = null;
  looking += 1;
  counting += 1;
  root = null;
  frame = null;
  hint = null;
  body = null;
  strip = null;
  parts = {};
}

export function update(next) {
  const before = params;
  params = next;
  if (!root) return;
  parts.select.value = params.get('epic') || '';
  if ((next.get('q') || '') !== (before.get('q') || '')) {
    bar.setValue(params.get('q') || '');
    look();
  } else if ((next.get('epic') || '') !== (before.get('epic') || '')) draw();
  else markSelected();
}

export function refresh() {
  if (!root || dragging) return;
  fillEpicChoices(parts.select);
  if (params.get('q')) look();
  else draw();
  count();
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
    h('div', { class: 'page-title' }, h('h1', {}, 'Board'), h('p', { class: 'page-sub' }, 'The work, its dependencies, and what can move next.')),
    h(
      'div',
      { class: 'page-actions' },
      h('button', { type: 'button', id: 'show-coverage', onclick: () => ui.navigate({ panel: 'coverage', sel: null }) }, icon('rule', { size: 16 }), 'Coverage'),
      h('button', { type: 'button', class: 'primary', id: 'new-item', onclick: () => openCreate({ kind: 'epic' }) }, icon('plus', { size: 16 }), 'New item'),
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

// -- what needs attention ---------------------------------------------------------------------------

// How many items answer a filter, as list_nodes counts them.
const howMany = async (query) => (await call('list_nodes', { query, limit: 1 })).total;

async function count() {
  const mine = ++counting;
  try {
    const [free, blocked, overdue, lapsed, coverage] = await Promise.all([
      howMany(FREE),
      howMany('status:blocked'),
      howMany('is:overdue'),
      howMany('is:lapsed'),
      call('coverage_report', { show: 'both', limit: 1 }),
    ]);
    if (mine !== counting || !root) return;
    drawStrip({ free, blocked, overdue, lapsed, unplaced: coverage.unplaced_rule_count, bare: coverage.story_without_rules_count });
  } catch (error) {
    if (mine !== counting || !root) return;
    if (!(error instanceof Refusal)) throw error;
    put(strip, h('p', { class: 'problem', role: 'alert' }, error.message));
  }
}

function tile(key, label, value, page, choices) {
  return h(
    'li',
    {},
    viewLink(
      page,
      choices,
      { class: `attention-item${value === 0 ? ' none' : ''}`, dataset: { key, value: String(value) } },
      icon(TILE_ICONS[key], { size: 14 }),
      h('span', { class: 'number' }, String(value)),
      h('span', { class: 'label' }, label),
    ),
  );
}

function drawStrip(numbers) {
  const here = { epic: null, sel: null, panel: null };
  put(
    strip,
    h('h2', { class: 'attention-title' }, 'Needs attention'),
    h(
      'ul',
      { class: 'plain attention-list' },
      tile('free', 'ready work nobody holds', numbers.free, 'board', { ...here, q: FREE }),
      tile('blocked', 'blocked', numbers.blocked, 'board', { ...here, q: 'status:blocked' }),
      tile('overdue', 'overdue', numbers.overdue, 'board', { ...here, q: 'is:overdue' }),
      tile('lapsed', 'claims that ran out with no handoff note', numbers.lapsed, 'board', { ...here, q: 'is:lapsed' }),
      tile('unplaced', 'requirements without a story', numbers.unplaced, 'graph', { view: 'context', as: 'list', q: null, epic: null, panel: null }),
      tile('bare', 'stories with no requirement', numbers.bare, 'board', { q: null, epic: null, sel: null, panel: 'coverage' }),
    ),
  );
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
  const kept = (node) => found === null || found.has(node.ref);
  const inEpic = new Map();
  for (const node of store.nodes.values()) {
    if (node.kind === 'epic') continue;
    const epic = epicOf(node);
    if (epic) inEpic.set(epic.ref, [...(inEpic.get(epic.ref) || []), node]);
  }
  const lanes = epics()
    .filter((epic) => (!only || String(epic.number) === only) && (found === null || kept(epic) || (inEpic.get(epic.ref) || []).some(kept)))
    .map((epic) => [epic, (inEpic.get(epic.ref) || []).filter(kept)]);
  const counts = Object.fromEntries(STATUSES.map((status) => [status, 0]));
  let total = 0;
  for (const [, items] of lanes) {
    for (const node of items) counts[node.status] += 1;
    total += items.length;
  }
  parts.count.textContent = `${plural(lanes.length, 'epic')} · ${total === 1 ? '1 story or task' : `${total} stories & tasks`}`;
  if (reading) put(body, h('p', { class: 'loading', id: 'board-loading', role: 'status' }, 'Reading the filter…'));
  else if (!lanes.length) put(body, emptyState(only));
  else put(body, h('div', { class: 'board-inner' }, columnHeads(counts), ...lanes.map(([epic, items]) => lane(epic, items))));
  markSelected();
  seeScroll();
  if (focused) body.querySelector(`[data-focus-key="${focused}"]`)?.focus();
}

function emptyState(only) {
  const [title, more] = only
    ? ['That epic is not there, or nothing in it matches the filter.', 'Choose another epic, or clear the filter.']
    : found !== null
      ? ['Nothing matches this filter.', 'Change the words, or clear the filter to see everything.']
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
          h('button', { type: 'button', class: 'primary', id: 'empty-new-epic', onclick: () => openCreate({ kind: 'epic' }) }, icon('plus', { size: 16 }), 'Create the first epic'),
          viewLink('access', {}, { id: 'empty-connect-agent' }, 'Connect an agent'),
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
        status === 'ready' ? h('span', { class: 'note' }, 'computed') : null,
        status === 'ready' ? h('span', { class: 'sr-only' }, ` (${columnNote(status)})`) : null,
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

function lane(epic, items) {
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
    h('button', { type: 'button', class: 'lane-title', 'data-focus-key': epic.ref, onclick: () => ui.navigate({ sel: epic.ref, panel: null }) }, epic.title),
    h('span', { class: 'id mono' }, code),
    h(
      'span',
      { class: 'lane-meta' },
      statusChip(epic.status),
      epic.priority !== 'normal' ? priorityChip(epic.priority) : null,
      epic.target_on ? h('span', { class: 'chip due' }, icon('calendar', { size: 13 }), `Due ${day(epic.target_on)}`) : null,
      epic.overdue ? h('span', { class: 'chip danger overdue' }, icon('alert', { size: 13 }), 'Overdue') : null,
    ),
    h('span', { class: 'lane-progress' }, h('span', { class: 'progress-text' }, tasksDone(done, total)), meter(done, total, `Tasks done in ${epic.title}`)),
    h('button', { type: 'button', class: 'add-story ghost small', onclick: () => openCreate({ kind: 'story', parent: epic.ref }) }, icon('plus', { size: 14 }), 'Add story'),
  );
  const cells = items.length
    ? h('div', { class: 'cells', id: cellsId, hidden: !open }, STATUSES.map((status) => cell(epic, status, items.filter((node) => node.status === status).sort(byPriority))))
    : h('p', { class: 'lane-empty', id: cellsId, hidden: !open }, found === null ? 'No stories or tasks yet.' : 'No story or task in this epic matches the filter.');
  return h('section', { class: 'lane', 'aria-label': `Epic ${epic.title}`, 'data-epic': epic.ref, 'data-ref': epic.ref }, head, cells);
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

// -- a card ---------------------------------------------------------------------------------------

let dragged = ''; // the ref of the card being dragged

const note = (kind, glyph, ...words) => h('p', { class: `card-note ${kind}` }, icon(glyph, { size: 14 }), h('span', {}, ...words));

function firstBlocker(node) {
  if (!node.waiting_on.length) return null;
  const [first, ...rest] = node.waiting_on;
  return note('waiting', 'blocked', `Waiting for ${codeOf(first)} ${titleOf(first)}${rest.length ? ` and ${rest.length} more` : ''}`);
}

function claimNote(node) {
  if (node.claimed_by) return note('claim', 'user', `Held by ${node.claimed_by} until ${moment(node.claim_expires_at)}`);
  if (node.lapsed_claim_by) return note('claim lapsed', 'alert', `${node.lapsed_claim_by}’s claim ran out`);
  return null;
}

function card(node) {
  const parent = store.nodes.get(node.parent);
  const code = nodeCode(node.kind, node.number);
  const context =
    node.progress_total !== null
      ? h('div', { class: 'card-context progress-line' }, h('span', {}, tasksDone(node.progress_done, node.progress_total)), meter(node.progress_done, node.progress_total, 'Tasks done'))
      : parent
        ? h('p', { class: 'card-context' }, 'Under: ', h('span', { class: 'id mono' }, nodeCode(parent.kind, parent.number)), ` ${parent.title}`)
        : h('p', { class: 'card-context' });
  const element = h(
    'article',
    {
      class: `card kind-${node.kind} status-${node.status}`,
      tabindex: 0,
      draggable: 'true',
      dataset: { ref: node.ref, kind: node.kind, number: node.number, status: node.status, focusKey: node.ref },
      'aria-label': `${KIND[node.kind].label} ${node.number}: ${node.title}. ${STATUS[node.status].label}.`,
      onclick: (event) => {
        if (!event.target.closest('button')) ui.navigate({ sel: node.ref, panel: null });
      },
      onkeydown: (event) => {
        if (event.target === element && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault();
          ui.navigate({ sel: node.ref, panel: null });
        }
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
      h('span', { class: 'id mono' }, code),
      h('span', { class: 'card-kind' }, kindIcon(node.kind, 13), KIND[node.kind].label),
      h(
        'button',
        { type: 'button', class: 'move small icon-only ghost', title: 'Move to another column', 'aria-haspopup': 'menu', 'aria-label': `Move ${code} to another column`, 'data-focus-key': `move-${node.ref}`, onclick: (event) => openMenu(node, event.currentTarget) },
        icon('chevron_down', { size: 16 }),
      ),
    ),
    h('h3', { class: 'card-title' }, node.title),
    h(
      'div',
      { class: 'chips card-chips' },
      priorityChip(node.priority),
      node.target_on ? h('span', { class: 'chip due' }, icon('calendar', { size: 13 }), `Due ${day(node.target_on)}`) : null,
      node.overdue ? h('span', { class: 'chip danger overdue' }, icon('alert', { size: 13 }), 'Overdue') : null,
      node.schedule_conflicts.length
        ? h('span', { class: 'chip warn conflict', title: `Planned to start before ${node.schedule_conflicts.map(codeOf).join(', ')} is due` }, icon('alert', { size: 13 }), 'Schedule conflict')
        : null,
      node.status === 'done' || node.status === 'dropped' ? statusChip(node.status) : null,
    ),
    context,
    claimNote(node),
    firstBlocker(node),
  );
  return element;
}

function markSelected() {
  if (!body) return;
  const chosen = parseSel(params.get('sel'));
  body.querySelectorAll('.card.selected, .lane.selected').forEach((el) => el.classList.remove('selected'));
  if (!chosen) return;
  body.querySelector(`.card[data-ref="${chosen.ref}"]`)?.classList.add('selected');
  body.querySelector(`.lane[data-ref="${chosen.ref}"]`)?.classList.add('selected');
}

// -- changing a status ------------------------------------------------------------------------------

async function setStatus(node, status) {
  try {
    await call('update_node', { actor: actor(), node: node.ref, expected_version: node.version, status });
    notify(`${nodeCode(node.kind, node.number)} is now ${STATUS[status].label.toLowerCase()}.`);
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
  }
  await sync();
  refresh();
}

let menu = null;

function closeMenu(restore = false) {
  if (!menu) return;
  const { element, opener } = menu;
  element.remove();
  menu = null;
  document.removeEventListener('click', outside, true);
  window.removeEventListener('scroll', dismiss, true);
  window.removeEventListener('resize', dismiss);
  if (restore) opener.focus();
}

// Scrolling or resizing moves what the menu hangs from, so it goes away (without taking the focus).
function dismiss() {
  closeMenu();
}

function outside(event) {
  if (menu && !menu.element.contains(event.target)) closeMenu();
}

// The menu floats at the button, over whatever scrolls, so the region of the board cannot clip it.
function placeMenu(element, opener) {
  const at = opener.getBoundingClientRect();
  const { offsetWidth: width, offsetHeight: height } = element;
  const below = at.bottom + 4 + height <= window.innerHeight - 8;
  element.style.left = `${Math.max(8, Math.min(at.left, window.innerWidth - width - 8))}px`;
  element.style.top = `${Math.max(8, below ? at.bottom + 4 : at.top - height - 4)}px`;
}

function openMenu(node, opener) {
  closeMenu();
  // A ready card is a new one that waits for nothing, so "new" would change nothing.
  const choices = SETTABLE.filter((status) => status !== node.status && !(node.status === 'ready' && status === 'new'));
  const element = h(
    'div',
    { class: 'menu move-menu', role: 'menu', 'aria-label': `Move ${nodeCode(node.kind, node.number)}` },
    choices.map((status) =>
      h(
        'button',
        {
          type: 'button',
          role: 'menuitem',
          dataset: { status },
          onclick: () => {
            closeMenu(true);
            setStatus(node, status);
          },
        },
        statusIcon(status, 16),
        STATUS[status].label,
      ),
    ),
    h('p', { class: 'quiet' }, 'Ready is worked out from the links and cannot be chosen.'),
  );
  element.addEventListener('keydown', (event) => {
    const items = [...element.querySelectorAll('[role=menuitem]')];
    const at = items.indexOf(document.activeElement);
    if (event.key === 'Escape') {
      event.preventDefault();
      closeMenu(true);
    } else if (event.key === 'ArrowDown') {
      event.preventDefault();
      items[(at + 1) % items.length].focus();
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      items[(at - 1 + items.length) % items.length].focus();
    }
  });
  opener.after(element);
  menu = { element, opener };
  placeMenu(element, opener);
  element.querySelector('[role=menuitem]').focus({ preventScroll: true });
  document.addEventListener('click', outside, true);
  window.addEventListener('scroll', dismiss, true);
  window.addEventListener('resize', dismiss);
}
