// The Outline: the work as a tree of epics, stories and tasks that opens and closes, with the
// rules shown under their story and progress on every branch. A story or task is moved to a new
// parent by dragging it onto that parent, or with the Move dialog (the M key on the keyboard).
// Every move is move_node, so the server decides and says why when it refuses.

import { Refusal, call } from './api.js';
import { actor, notify, ui } from './ctx.js';
import { clear, h, plural } from './dom.js';
import { parentChoices } from './create.js';
import { KIND, STATUS, kindChip, statusChip } from './labels.js';
import { ensure, store, sync } from './store.js';

let root = null;
let params = new URLSearchParams();
let tree = null;
let dragged = 0;
let generation = 0;

const byId = (a, b) => a.id - b.id;

export function mount(element, initial) {
  root = element;
  params = initial;
  clear(root).append(
    h(
      'div',
      { class: 'toolbar' },
      h('h1', {}, 'Outline'),
      h('button', { type: 'button', id: 'expand-all', onclick: () => ui.navigate({ closed: null }, { replace: true }) }, 'Open all'),
      h('button', { type: 'button', id: 'collapse-all', onclick: collapseAll }, 'Close all'),
      h(
        'p',
        { class: 'help' },
        'Arrow keys walk the tree (left and right close and open a branch), Enter opens an item, and M moves a story or task. Or drag a story onto an epic, or a task onto a story.',
      ),
    ),
  );
  tree = h('ul', { class: 'outline', role: 'tree', 'aria-label': 'Epics, stories and tasks' });
  root.append(tree);
  draw();
}

export function unmount() {
  generation += 1;
  root = null;
  tree = null;
}

export function update(next) {
  params = next;
  if (root) draw();
}

export function refresh() {
  if (root) draw();
}

const closed = () => new Set((params.get('closed') || '').split(',').filter(Boolean));

function collapseAll() {
  const ids = [...store.nodes.values()].filter((node) => node.kind !== 'task').map((node) => node.id);
  ui.navigate({ closed: ids.join(',') || null }, { replace: true });
}

function setOpen(id, open) {
  const now = closed();
  if (open) now.delete(String(id));
  else now.add(String(id));
  ui.navigate({ closed: [...now].sort((a, b) => a - b).join(',') || null }, { replace: true });
}

// -- drawing --------------------------------------------------------------------------------------

async function draw() {
  const mine = ++generation;
  await ensure('rules');
  if (mine !== generation || !root) return;
  const hadFocus = tree.contains(document.activeElement);
  const focused = tree.querySelector('li[tabindex="0"]')?.dataset.key;
  const shut = closed();
  const nodes = [...store.nodes.values()];
  const kids = (parent) => nodes.filter((node) => node.parent_id === parent).sort(byId);
  const rulesOf = (story) => (store.rules || []).filter((rule) => rule.story_id === story && rule.status !== 'dropped').sort(byId);

  const items = kids(null).map((epic) => branch(epic, 1, shut, kids, rulesOf));
  const empty = items.length ? [] : [h('li', { role: 'none', class: 'empty' }, 'Nothing here yet. Create an epic on the Board, or ask an agent to make a plan.')];
  tree.replaceChildren(...items, ...empty);
  const stops = [...tree.querySelectorAll('li[role=treeitem]')];
  const wanted = (focused && tree.querySelector(`li[data-key="${focused}"]`)) || (params.get('card') && tree.querySelector(`li[data-key="n:${params.get('card')}"]`)) || stops[0];
  stops.forEach((item) => item.setAttribute('tabindex', item === wanted ? '0' : '-1'));
  // A redraw keeps the focus where it was, and does not take it from anything outside the tree.
  if (hadFocus && wanted) wanted.focus();
  markCurrent();
}

function branch(node, level, shut, kids, rulesOf) {
  const children = node.kind === 'epic' ? kids(node.id) : node.kind === 'story' ? kids(node.id) : [];
  const rules = node.kind === 'story' ? rulesOf(node.id) : [];
  const parent = children.length > 0 || rules.length > 0;
  const open = parent && !shut.has(String(node.id));
  const total = node.progress_total;
  const row = h(
    'div',
    {
      class: `row kind-${node.kind}`,
      draggable: node.kind === 'epic' ? null : 'true',
      ondragstart: (event) => {
        dragged = node.id;
        event.dataTransfer.setData('application/x-vrum-node', String(node.id));
        event.dataTransfer.setData('text/plain', `#${node.id}`);
        event.dataTransfer.effectAllowed = 'move';
        row.classList.add('dragging');
      },
      ondragend: () => row.classList.remove('dragging'),
      ondragover: (event) => {
        if (dragged && dragged !== node.id) {
          event.preventDefault();
          row.classList.add('over');
        }
      },
      ondragleave: () => row.classList.remove('over'),
      ondrop: (event) => {
        event.preventDefault();
        row.classList.remove('over');
        const moving = store.nodes.get(dragged);
        const id = dragged;
        dragged = 0;
        if (moving && id !== node.id) move(moving, node.id);
      },
      onclick: (event) => {
        if (!event.target.closest('button')) ui.navigate({ card: node.id, panel: null });
      },
    },
    parent
      ? h('button', { type: 'button', class: 'twisty', tabindex: -1, 'aria-label': `${open ? 'Close' : 'Open'} #${node.id}`, onclick: () => setOpen(node.id, !open) }, h('span', { 'aria-hidden': 'true' }, open ? '▾' : '▸'))
      : h('span', { class: 'twisty none' }),
    kindChip(node.kind),
    h('span', { class: 'label' }, h('span', { class: 'id' }, `#${node.id} `), node.title),
    statusChip(node.status),
    total !== null
      ? h('span', { class: 'progress-line' }, `${node.progress_done} of ${plural(total, 'task')} done`, h('progress', { max: Math.max(total, 1), value: node.progress_done, 'aria-label': 'Tasks done' }))
      : null,
    node.claimed_by ? h('span', { class: 'quiet' }, `held by ${node.claimed_by}`) : null,
    node.kind === 'epic' ? null : h('button', { type: 'button', class: 'move', tabindex: -1, 'aria-label': `Move #${node.id}`, onclick: () => openMove(node) }, 'Move…'),
  );
  const item = h(
    'li',
    {
      role: 'treeitem',
      class: `node kind-${node.kind}`,
      tabindex: -1,
      'aria-level': level,
      'aria-expanded': parent ? String(open) : null,
      'aria-label': `${KIND[node.kind].label} ${node.id}: ${node.title}. ${STATUS[node.status].label}.${total !== null ? ` ${node.progress_done} of ${total} tasks done.` : ''}`,
      dataset: { key: `n:${node.id}`, id: node.id, kind: node.kind },
      onkeydown: (event) => walk(event, node, parent, open),
    },
    row,
    parent
      ? h(
          'ul',
          { role: 'group', hidden: !open },
          rules.map((rule) =>
            h(
              'li',
              { role: 'treeitem', class: 'rule', tabindex: -1, 'aria-level': level + 1, 'aria-label': `Rule ${rule.id}: ${rule.title}. ${rule.status}.`, dataset: { key: `r:${rule.id}`, rule: rule.id }, onkeydown: (event) => walk(event, null, false, false) },
              h('div', { class: 'row kind-rule' }, h('span', { class: 'twisty none' }), h('span', { class: 'chip kind' }, h('span', { 'aria-hidden': 'true' }, KIND.rule.sign), ' Rule'), h('span', { class: 'label' }, h('span', { class: 'id' }, `#${rule.id} `), rule.title), h('span', { class: 'chip' }, rule.status)),
            ),
          ),
          children.map((child) => branch(child, level + 1, shut, kids, rulesOf)),
        )
      : null,
  );
  return item;
}

function markCurrent() {
  tree.querySelectorAll('li.current').forEach((item) => item.classList.remove('current'));
  const card = params.get('card');
  if (card) tree.querySelector(`li.node[data-id="${card}"]`)?.classList.add('current');
}

// -- the keyboard ---------------------------------------------------------------------------------

function visibleItems() {
  return [...tree.querySelectorAll('li[role=treeitem]')].filter((item) => !item.parentElement.closest('ul[hidden]'));
}

function focusItem(item) {
  tree.querySelectorAll('li[role=treeitem]').forEach((other) => other.setAttribute('tabindex', other === item ? '0' : '-1'));
  item.focus();
}

function walk(event, node, parent, open) {
  if (event.target !== event.currentTarget) return;
  event.stopPropagation();
  const here = event.currentTarget;
  const list = visibleItems();
  const at = list.indexOf(here);
  const key = event.key;
  if (key === 'ArrowDown') focusItem(list[Math.min(at + 1, list.length - 1)]);
  else if (key === 'ArrowUp') focusItem(list[Math.max(at - 1, 0)]);
  else if (key === 'Home') focusItem(list[0]);
  else if (key === 'End') focusItem(list[list.length - 1]);
  else if (key === 'ArrowRight' && node) {
    if (parent && !open) setOpen(node.id, true);
    else if (parent) focusItem(list[at + 1]);
  } else if (key === 'ArrowLeft') {
    if (node && parent && open) setOpen(node.id, false);
    else {
      const up = here.parentElement.closest('li[role=treeitem]');
      if (up) focusItem(up);
    }
  } else if (key === 'Enter' && node) ui.navigate({ card: node.id, panel: null });
  else if ((key === 'm' || key === 'M') && node && node.kind !== 'epic') openMove(node);
  else return;
  event.preventDefault();
}

// -- moving ---------------------------------------------------------------------------------------

async function move(node, parentId) {
  try {
    await call('move_node', { actor: actor(), node_id: node.id, expected_version: node.version, new_parent_id: parentId });
    notify(`Moved #${node.id} under #${parentId}.`);
    await sync();
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
  }
  draw();
}

function openMove(node) {
  const back = document.activeElement;
  const problem = h('p', { class: 'problem', role: 'alert' });
  const choice = h('select', { id: 'move-to', name: 'parent' }, parentChoices(node.kind, node.parent_id).filter(Boolean));
  choice.value = String(node.parent_id);
  const box = h(
    'dialog',
    { class: 'modal', 'aria-labelledby': 'move-heading' },
    h('h2', { id: 'move-heading' }, `Move #${node.id} ${node.title}`),
    h(
      'form',
      {
        method: 'dialog',
        onsubmit: async (event) => {
          event.preventDefault();
          problem.textContent = '';
          try {
            await call('move_node', { actor: actor(), node_id: node.id, expected_version: node.version, new_parent_id: Number(choice.value) });
            box.close();
            notify(`Moved #${node.id} under #${choice.value}.`);
            await sync();
            draw();
          } catch (error) {
            if (!(error instanceof Refusal)) throw error;
            problem.textContent = error.message;
          }
        },
      },
      h('div', { class: 'field' }, h('label', { for: 'move-to' }, node.kind === 'story' ? 'To epic' : 'To story'), choice),
      problem,
      h('div', { class: 'buttons' }, h('button', { type: 'submit', class: 'primary' }, 'Move'), h('button', { type: 'button', onclick: () => box.close() }, 'Cancel')),
    ),
  );
  box.addEventListener('close', () => {
    box.remove();
    if (back && document.contains(back)) back.focus();
  });
  document.getElementById('dialogs').append(box);
  box.showModal();
  choice.focus();
}
