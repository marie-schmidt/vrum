// The Kanban board: one column for each status, one lane for each epic. A card is a story or a task.
// Dragging a card to another column of its lane changes its status, and every card has a menu that
// does the same from the keyboard. Both ask the server, which decides; the board shows its answer.
// Above it is a strip of what needs attention (each number is the answer of the same filter or
// report an agent can ask for, and a link to the cards it counts), and a filter box (`q`, the same
// filter language as list_nodes) that keeps only the cards it asks for. A card opens in the
// detail panel (`sel=node:<id>`).

import { Refusal, call } from './api.js';
import { actor, notify, parseSel, ui, viewLink } from './ctx.js';
import { clear, day, h, moment, put } from './dom.js';
import { openCreate } from './create.js';
import { filterBar, loadRows } from './filterbar.js';
import { KIND, SETTABLE, STATUS, STATUSES, columnNote, kindChip, priorityChip, statusChip } from './labels.js';
import { epicOf, epics, store, sync, titleOf } from './store.js';

// What "ready work nobody holds" means: the filter that gives exactly the tool's answer.
const FREE = 'kind:story,task status:ready -claimed';

let root = null;
let body = null; // where the lanes are drawn
let strip = null; // where the numbers that need attention are
let bar = null; // the filter box
let found = null; // the ids the filter asks for, or null when there is no filter
let params = new URLSearchParams();
let dragging = false;
let looking = 0; // the filter being read
let counting = 0; // the numbers being read
let clock = null;

const PRIORITY_ORDER = { urgent: 0, high: 1, normal: 2, low: 3 };
const byPriority = (a, b) => PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority] || a.id - b.id;

export function mount(element, initial) {
  root = element;
  params = initial;
  found = null;
  bar = filterBar({ value: params.get('q') || '', onSubmit: (text) => ui.navigate({ q: text || null }) });
  clear(root).append(
    (strip = h('section', { id: 'attention', class: 'attention', 'aria-label': 'What needs attention' }, h('p', { class: 'quiet' }, 'Reading the numbers…'))),
    toolbar(),
    bar.help,
    bar.problem,
    (body = h('div', { class: 'board', id: 'board' })),
  );
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
  looking += 1;
  counting += 1;
  root = null;
  body = null;
  strip = null;
}

export function update(next) {
  const before = params;
  params = next;
  if (!root) return;
  const select = root.querySelector('#epic-filter');
  if (select) select.value = params.get('epic') || '';
  if ((next.get('q') || '') !== (before.get('q') || '')) {
    bar.setValue(params.get('q') || '');
    look();
  } else if ((next.get('epic') || '') !== (before.get('epic') || '')) draw();
  else markSelected();
}

export function refresh() {
  if (!root || dragging) return;
  const select = root.querySelector('#epic-filter');
  if (select) fillEpicChoices(select);
  if (params.get('q')) look();
  else draw();
  count();
}

// -- the top of the page ---------------------------------------------------------------------------

function fillEpicChoices(select) {
  const chosen = params.get('epic') || '';
  select.replaceChildren(
    h('option', { value: '' }, 'All epics'),
    ...epics().map((epic) => h('option', { value: epic.id }, `#${epic.id} ${epic.title}`)),
  );
  select.value = chosen;
}

function toolbar() {
  const select = h('select', { id: 'epic-filter', onchange: () => ui.navigate({ epic: select.value || null }) });
  fillEpicChoices(select);
  return h(
    'div',
    { class: 'toolbar' },
    h('h1', {}, 'Board'),
    h('label', { for: 'epic-filter' }, 'Show'),
    select,
    bar.form,
    h('button', { type: 'button', class: 'primary', id: 'new-item', onclick: () => openCreate({ kind: 'epic' }) }, 'New item'),
    h('button', { type: 'button', id: 'show-coverage', onclick: () => ui.navigate({ panel: 'coverage', sel: null }) }, 'Coverage'),
    h(
      'p',
      { class: 'help' },
      'Drag a card to another column of its own epic to change its status, or use its Move menu. Ready is worked out from the links, so nothing can be dropped there.',
    ),
  );
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
    viewLink(page, choices, { class: `attention-item${value === 0 ? ' none' : ''}`, dataset: { key, value: String(value) } }, h('span', { class: 'number' }, String(value)), h('span', { class: 'label' }, ` ${label}`)),
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
    bar.setProblem('');
    draw();
    return;
  }
  const rows = await loadRows(text, bar);
  if (mine !== looking || !root) return;
  if (rows !== null) found = new Set(rows.map((node) => node.id));
  draw();
}

// -- drawing the lanes --------------------------------------------------------------------------

function draw() {
  if (!body) return;
  const focused = document.activeElement?.dataset?.focusKey;
  const only = params.get('epic');
  const kept = (node) => found === null || found.has(node.id);
  const inEpic = new Map();
  for (const node of store.nodes.values()) {
    if (node.kind === 'epic') continue;
    const epic = epicOf(node);
    if (epic) inEpic.set(epic.id, [...(inEpic.get(epic.id) || []), node]);
  }
  const lanes = epics().filter((epic) => (!only || String(epic.id) === only) && (found === null || kept(epic) || (inEpic.get(epic.id) || []).some(kept)));
  body.replaceChildren(
    columnHeads(),
    ...lanes.map((epic) => lane(epic, (inEpic.get(epic.id) || []).filter(kept))),
    ...(lanes.length
      ? []
      : [h('p', { class: 'empty', id: 'board-empty' }, only ? 'That epic is not there, or nothing in it matches the filter.' : found !== null ? 'Nothing matches this filter.' : 'Nothing here yet. Create an epic, or ask an agent to make a plan.')]),
  );
  markSelected();
  if (focused) body.querySelector(`[data-focus-key="${focused}"]`)?.focus();
}

function columnHeads() {
  return h(
    'div',
    { class: 'column-heads', role: 'presentation' },
    STATUSES.map((status) =>
      h(
        'div',
        { class: `column-head status-${status}`, 'data-status': status },
        h('strong', {}, h('span', { 'aria-hidden': 'true' }, STATUS[status].sign), ' ', STATUS[status].label),
        h('span', { class: 'note' }, columnNote(status)),
      ),
    ),
  );
}

function lane(epic, items) {
  const total = epic.progress_total ?? 0;
  const head = h(
    'header',
    { class: 'lane-head' },
    kindChip('epic'),
    h('button', { type: 'button', class: 'lane-title link', 'data-focus-key': epic.id, onclick: () => ui.navigate({ sel: `node:${epic.id}`, panel: null }) }, `#${epic.id} ${epic.title}`),
    statusChip(epic.status),
    priorityChip(epic.priority),
    h('span', { class: 'progress-line' }, `${epic.progress_done ?? 0} of ${total} ${total === 1 ? 'task' : 'tasks'} done`, h('progress', { max: Math.max(total, 1), value: epic.progress_done ?? 0, 'aria-label': `Tasks done in ${epic.title}` })),
    epic.target_on ? h('span', { class: 'chip' }, `Due ${day(epic.target_on)}`) : null,
    epic.overdue ? h('span', { class: 'chip overdue' }, '! Overdue') : null,
    h('button', { type: 'button', class: 'add-story', onclick: () => openCreate({ kind: 'story', parent: epic.id }) }, 'Add story'),
  );
  const cells = h(
    'div',
    { class: 'cells' },
    STATUSES.map((status) => cell(epic, status, items.filter((node) => node.status === status).sort(byPriority))),
  );
  return h('section', { class: 'lane', 'aria-label': `Epic ${epic.title}`, 'data-epic': epic.id }, head, cells);
}

function cell(epic, status, cards) {
  const place = h(
    'div',
    { class: `cell status-${status}`, role: 'group', 'aria-label': `${STATUS[status].label} in ${epic.title}`, 'data-status': status, 'data-epic': epic.id },
    h('ul', { class: 'cards' }, cards.map((node) => h('li', {}, card(node)))),
  );
  place.addEventListener('dragover', (event) => {
    const id = Number(event.dataTransfer?.types.includes('application/x-vrum-node') ? dragged : 0);
    if (id && epicOf(store.nodes.get(id))?.id === epic.id) {
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

let dragged = 0;

function firstBlocker(node) {
  if (!node.waiting_on.length) return null;
  const [first, ...rest] = node.waiting_on;
  return h('div', { class: 'waiting' }, `Waiting for #${first} ${titleOf(first)}`, rest.length ? ` and ${rest.length} more` : '');
}

function card(node) {
  const parent = store.nodes.get(node.parent_id);
  const claim = node.claimed_by
    ? h('div', { class: 'claim' }, `Held by ${node.claimed_by} until ${moment(node.claim_expires_at)}`)
    : node.lapsed_claim_by
      ? h('div', { class: 'claim lapsed' }, `${node.lapsed_claim_by}’s claim ran out`)
      : null;
  const progress =
    node.progress_total !== null
      ? h('div', { class: 'progress-line' }, `${node.progress_done} of ${node.progress_total} ${node.progress_total === 1 ? 'task' : 'tasks'} done`, h('progress', { max: Math.max(node.progress_total, 1), value: node.progress_done, 'aria-label': 'Tasks done' }))
      : null;
  const element = h(
    'article',
    {
      class: `card kind-${node.kind} status-${node.status}`,
      tabindex: 0,
      draggable: 'true',
      dataset: { id: node.id, kind: node.kind, status: node.status, focusKey: node.id },
      'aria-label': `${KIND[node.kind].label} ${node.id}: ${node.title}. ${STATUS[node.status].label}.`,
      onclick: (event) => {
        if (!event.target.closest('button')) ui.navigate({ sel: `node:${node.id}`, panel: null });
      },
      onkeydown: (event) => {
        if (event.target === element && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault();
          ui.navigate({ sel: `node:${node.id}`, panel: null });
        }
      },
      ondragstart: (event) => {
        dragged = node.id;
        dragging = true;
        event.dataTransfer.setData('application/x-vrum-node', String(node.id));
        event.dataTransfer.setData('text/plain', `#${node.id}`);
        event.dataTransfer.effectAllowed = 'move';
        element.classList.add('dragging');
      },
      ondragend: () => {
        dragging = false;
        element.classList.remove('dragging');
      },
    },
    h('div', { class: 'card-top' }, kindChip(node.kind), h('span', { class: 'id' }, `#${node.id}`)),
    h('h4', { class: 'card-title' }, node.title),
    parent && node.kind === 'task' ? h('div', { class: 'quiet' }, `in #${parent.id} ${parent.title}`) : null,
    h('div', { class: 'chips' }, priorityChip(node.priority), node.target_on ? h('span', { class: 'chip' }, `Due ${day(node.target_on)}`) : null, node.overdue ? h('span', { class: 'chip overdue' }, '! Overdue') : null, node.schedule_conflicts.length ? h('span', { class: 'chip conflict', title: `Planned to start before ${node.schedule_conflicts.map((id) => `#${id}`).join(', ')} is due` }, '! Schedule conflict') : null),
    progress,
    claim,
    firstBlocker(node),
    h('button', { type: 'button', class: 'move', 'aria-haspopup': 'menu', 'aria-label': `Move #${node.id} to another column`, onclick: (event) => openMenu(node, event.currentTarget) }, 'Move…'),
  );
  return element;
}

function markSelected() {
  if (!body) return;
  const chosen = parseSel(params.get('sel'));
  body.querySelectorAll('.card.selected, .lane.selected').forEach((el) => el.classList.remove('selected'));
  if (!chosen || chosen.kind !== 'node') return;
  body.querySelector(`.card[data-id="${chosen.id}"]`)?.classList.add('selected');
  body.querySelector(`.lane[data-epic="${chosen.id}"]`)?.classList.add('selected');
}

// -- changing a status ------------------------------------------------------------------------------

async function setStatus(node, status) {
  try {
    await call('update_node', { actor: actor(), node_id: node.id, expected_version: node.version, status });
    notify(`#${node.id} is now ${STATUS[status].label.toLowerCase()}.`);
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
  if (restore) opener.focus();
}

function outside(event) {
  if (menu && !menu.element.contains(event.target)) closeMenu();
}

function openMenu(node, opener) {
  closeMenu();
  // A ready card is a new one that waits for nothing, so "new" would change nothing.
  const choices = SETTABLE.filter((status) => status !== node.status && !(node.status === 'ready' && status === 'new'));
  const element = h(
    'div',
    { class: 'menu', role: 'menu', 'aria-label': `Move #${node.id}` },
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
        `${STATUS[status].sign} ${STATUS[status].label}`,
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
  element.querySelector('[role=menuitem]').focus();
  document.addEventListener('click', outside, true);
}
