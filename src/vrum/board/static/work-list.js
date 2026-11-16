// The Graph's list of work: the hierarchy of epics, stories and tasks as a tree that opens and
// closes, with each item's status, what it needs first and what needs it, and who holds it.
// Choosing an item shows it in the shared detail panel. It draws what the Graph page hands it (the
// same scope the map draws) and keeps nothing of its own but which branches are closed.

import { h, moment, plural } from './dom.js';
import { kindChip, statusChip } from './labels.js';
import { openRecord } from './records.js';
import { store } from './store.js';
import { keyOf } from './graph-data.js';

const byId = (a, b) => a.id - b.id;

export function createWorkList() {
  const closed = new Set(); // ids of the branches the person closed
  const tree = h('ul', { class: 'gv-tree', id: 'work-tree', role: 'tree', 'aria-label': 'Epics, stories and tasks' });
  let current = null; // what the page last handed over
  let chosen = null;

  const select = (node) => openRecord('node', node.id);

  function relation(node, type, outgoing) {
    const ids = store.links
      .filter((link) => link.link_type === type && (outgoing ? link.source_id === node.id : link.target_id === node.id))
      .map((link) => (outgoing ? link.target_id : link.source_id));
    return [...new Set(ids)].sort((a, b) => a - b);
  }

  function ties(node) {
    const needs = relation(node, 'needs_first', true);
    const needed = relation(node, 'needs_first', false);
    const part = (className, words, ids) =>
      ids.length
        ? h(
            'span',
            { class: `gv-ties ${className}`, dataset: { ids: ids.join(',') } },
            `${words} `,
            ids.map((id, at) => {
              const other = store.nodes.get(id);
              return [at ? ', ' : '', h('button', { type: 'button', class: 'link gv-link', tabindex: -1, 'data-key': keyOf('node', id), onclick: () => openRecord('node', id) }, other ? `#${id} ${other.title}${other.status === 'done' ? ' (done)' : ''}` : `#${id}`)];
            }),
          )
        : null;
    return [part('needs', 'Needs first:', needs), part('needed-by', 'Needed first by:', needed)];
  }

  function claim(node) {
    if (node.claimed_by) return h('span', { class: 'gv-claim' }, `Held by ${node.claimed_by} until ${moment(node.claim_expires_at)}`);
    if (node.lapsed_claim_by) return h('span', { class: 'gv-claim lapsed' }, `${node.lapsed_claim_by}’s claim ran out`);
    return null;
  }

  function branch(dot, level, kids) {
    const node = dot.record;
    const children = kids(node.id);
    const parent = children.length > 0;
    const open = parent && !closed.has(node.id);
    const total = node.progress_total;
    const key = keyOf('node', node.id);
    const item = h(
      'li',
      {
        role: 'treeitem',
        class: `gv-node type-${node.kind}${dot.outside ? ' outside' : ''}${key === chosen ? ' selected' : ''}`,
        tabindex: -1,
        'aria-level': level,
        'aria-expanded': parent ? String(open) : null,
        'aria-selected': key === chosen ? 'true' : 'false',
        'aria-current': key === chosen ? 'true' : null,
        'aria-label': `${node.kind[0].toUpperCase()}${node.kind.slice(1)} ${node.id}: ${node.title}. ${node.status.replace('_', ' ')}.${total !== null ? ` ${node.progress_done} of ${total} tasks done.` : ''}${node.claimed_by ? ` Held by ${node.claimed_by}.` : ''}`,
        dataset: { key, kind: 'node', type: node.kind, id: node.id, status: node.status },
        onkeydown: (event) => walk(event, node, parent, open),
      },
      h(
        'div',
        { class: 'gv-row', onclick: (event) => { if (!event.target.closest('button, a')) select(node); } },
        parent
          ? h('button', { type: 'button', class: 'gv-twisty', tabindex: -1, 'aria-label': `${open ? 'Close' : 'Open'} ${node.title}`, onclick: () => setOpen(node.id, !open) }, h('span', { 'aria-hidden': 'true' }, open ? '▾' : '▸'))
          : h('span', { class: 'gv-twisty none' }),
        kindChip(node.kind),
        h('span', { class: 'gv-label' }, h('span', { class: 'id' }, `#${node.id} `), node.title),
        statusChip(node.status),
        total !== null ? h('span', { class: 'gv-progress' }, `${node.progress_done} of ${plural(total, 'task')} done`, h('progress', { max: Math.max(total, 1), value: node.progress_done, 'aria-label': 'Tasks done' })) : null,
        claim(node),
        h('div', { class: 'gv-ties-line' }, ties(node)),
      ),
      parent ? h('ul', { role: 'group', hidden: !open }, children.map((child) => branch(child, level + 1, kids))) : null,
    );
    return item;
  }

  function draw(ctx) {
    current = ctx;
    chosen = ctx.cfg.sel?.key ?? null;
    const hadFocus = tree.contains(document.activeElement);
    const focused = tree.querySelector('li[tabindex="0"]')?.dataset.key;
    const dots = [...ctx.shown.dots.values()].filter((dot) => dot.isNode);
    const present = new Set(dots.map((dot) => dot.id));
    const kidsOf = new Map();
    for (const dot of dots) {
      const parent = present.has(dot.record.parent_id) ? dot.record.parent_id : null;
      if (!kidsOf.has(parent)) kidsOf.set(parent, []);
      kidsOf.get(parent).push(dot);
    }
    const kids = (parent) => (kidsOf.get(parent) || []).sort((a, b) => byId(a, b));
    const rows = kids(null).map((dot) => branch(dot, 1, kids));
    tree.replaceChildren(...(rows.length ? rows : [h('li', { role: 'none', class: 'gv-empty' }, ctx.shown.notice || 'There is no work here yet. Create an epic on the Board, or ask an agent to make a plan.')]));
    const stops = [...tree.querySelectorAll('li[role=treeitem]')];
    const wanted = (focused && tree.querySelector(`li[data-key="${focused}"]`)) || (chosen && tree.querySelector(`li[data-key="${chosen}"]`)) || stops[0];
    stops.forEach((item) => item.setAttribute('tabindex', item === wanted ? '0' : '-1'));
    // A redraw keeps the focus where it was, and does not take it from anything outside the tree.
    if (hadFocus && wanted) wanted.focus();
  }

  // Only the choice changed: mark it again, and make it the one stop of the tree.
  function mark(key) {
    chosen = key;
    for (const item of tree.querySelectorAll('li.selected')) {
      item.classList.remove('selected');
      item.setAttribute('aria-selected', 'false');
      item.removeAttribute('aria-current');
    }
    const item = key ? tree.querySelector(`li[data-key="${key}"]`) : null;
    if (!item) return null;
    item.classList.add('selected');
    item.setAttribute('aria-selected', 'true');
    item.setAttribute('aria-current', 'true');
    return item;
  }

  function setOpen(id, open) {
    if (open) closed.delete(id);
    else closed.add(id);
    if (current) draw(current);
  }

  const visible = () => [...tree.querySelectorAll('li[role=treeitem]')].filter((item) => !item.parentElement.closest('ul[hidden]'));

  function focusItem(item) {
    tree.querySelectorAll('li[role=treeitem]').forEach((other) => other.setAttribute('tabindex', other === item ? '0' : '-1'));
    item.focus();
  }

  function walk(event, node, parent, open) {
    if (event.target !== event.currentTarget) return;
    event.stopPropagation();
    const here = event.currentTarget;
    const list = visible();
    const at = list.indexOf(here);
    const key = event.key;
    if (key === 'ArrowDown') focusItem(list[Math.min(at + 1, list.length - 1)]);
    else if (key === 'ArrowUp') focusItem(list[Math.max(at - 1, 0)]);
    else if (key === 'Home') focusItem(list[0]);
    else if (key === 'End') focusItem(list[list.length - 1]);
    else if (key === 'ArrowRight') {
      if (parent && !open) setOpen(node.id, true);
      else if (parent) focusItem(list[at + 1]);
    } else if (key === 'ArrowLeft') {
      if (parent && open) setOpen(node.id, false);
      else {
        const up = here.parentElement.closest('li[role=treeitem]');
        if (up) focusItem(up);
      }
    } else if (key === 'Enter' || key === ' ') select(node);
    else return;
    event.preventDefault();
  }

  return { element: tree, draw, select: mark };
}
