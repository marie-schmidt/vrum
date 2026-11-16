// The Kanban board: one column for each status, one lane for each epic. A card is a story or a task.
// Dragging a card to another column of its lane changes its status, and every card has a menu that
// does the same from the keyboard. Both ask the server, which decides; the board shows its answer.

import { Refusal, call } from './api.js';
import { actor, notify, ui } from './ctx.js';
import { clear, day, h, moment } from './dom.js';
import { openCreate } from './create.js';
import { KIND, SETTABLE, STATUS, STATUSES, columnNote, kindChip, priorityChip, statusChip } from './labels.js';
import { epicOf, epics, store, sync, titleOf } from './store.js';

let root = null;
let body = null; // where the lanes are drawn
let params = new URLSearchParams();
let dragging = false;

const PRIORITY_ORDER = { urgent: 0, high: 1, normal: 2, low: 3 };
const byPriority = (a, b) => PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority] || a.id - b.id;

export function mount(element, initial) {
  root = element;
  params = initial;
  clear(root).append(toolbar(), (body = h('div', { class: 'board', id: 'board' })));
  draw();
}

export function unmount() {
  closeMenu();
  root = null;
  body = null;
}

export function update(next) {
  const filterChanged = (next.get('epic') || '') !== (params.get('epic') || '');
  params = next;
  if (!root) return;
  const select = root.querySelector('#epic-filter');
  if (select) select.value = params.get('epic') || '';
  if (filterChanged) draw();
  else markSelected();
}

export function refresh() {
  if (!root || dragging) return;
  const select = root.querySelector('#epic-filter');
  if (select) fillEpicChoices(select);
  draw();
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
    h('button', { type: 'button', class: 'primary', id: 'new-item', onclick: () => openCreate({ kind: 'epic' }) }, 'New item'),
    h('button', { type: 'button', id: 'show-coverage', onclick: () => ui.navigate({ panel: 'coverage', card: null }) }, 'Coverage'),
    h(
      'p',
      { class: 'help' },
      'Drag a card to another column of its own epic to change its status, or use its Move menu. Ready is worked out from the links, so nothing can be dropped there.',
    ),
  );
}

// -- drawing the lanes --------------------------------------------------------------------------

function draw() {
  if (!body) return;
  const focused = document.activeElement?.dataset?.focusKey;
  const only = params.get('epic');
  const lanes = epics().filter((epic) => !only || String(epic.id) === only);
  body.replaceChildren(
    columnHeads(),
    ...lanes.map(lane),
    ...(lanes.length
      ? []
      : [h('p', { class: 'empty' }, only ? 'That epic is not there.' : 'Nothing here yet. Create an epic, or ask an agent to make a plan.')]),
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

function lane(epic) {
  const items = [...store.nodes.values()].filter((node) => node.kind !== 'epic' && epicOf(node)?.id === epic.id);
  const total = epic.progress_total ?? 0;
  const head = h(
    'header',
    { class: 'lane-head' },
    kindChip('epic'),
    h('button', { type: 'button', class: 'lane-title link', 'data-focus-key': epic.id, onclick: () => ui.navigate({ card: epic.id, panel: null }) }, `#${epic.id} ${epic.title}`),
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
        if (!event.target.closest('button')) ui.navigate({ card: node.id, panel: null });
      },
      onkeydown: (event) => {
        if (event.target === element && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault();
          ui.navigate({ card: node.id, panel: null });
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
  const chosen = params.get('card');
  body.querySelectorAll('.card.selected').forEach((el) => el.classList.remove('selected'));
  if (chosen) body.querySelector(`.card[data-id="${chosen}"]`)?.classList.add('selected');
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
