// The Lists page's list of work: the hierarchy of epics, stories and tasks as a tree that opens and
// closes, with each item's status, what it needs first and what needs it, and who holds it.
// The title of every item is a link to its own page (a plain click goes there, a click with a
// modifier key opens it the way a link does), a click on the rest of its row and Enter on a row
// do the same. It draws what the Lists page hands it (the scope the epic and the filter choose) and keeps
// nothing of its own but which branches are closed. The record chosen in the address (`sel`) is marked.

import { itemLink, openItem } from './ctx.js';
import { h, plural } from './dom.js';
import { icon } from './icons.js';
import { KIND, byRef, codeOf, itemSizeChip, kindIcon, nodeCode, progressChip } from './labels.js';
import { statusChip } from './status-menu.js';
import { store } from './store.js';
import { byDot } from './lists-data.js';
import { connectionsDetails, heldChip, openDueChip } from './list-parts.js';

// The tree is one stop of the keyboard, the row in use. The status chip of a row is the next stop after
// the row, and is a stop only while its row is.
function stopOf(item, on) {
  item.setAttribute('tabindex', on ? '0' : '-1');
  item.querySelector(':scope > .gv-row > .chip[data-menu]')?.setAttribute('tabindex', on ? '0' : '-1');
}

// The status of a row, as a chip that changes it.
function stateChip(node) {
  const chip = statusChip(node);
  if (chip instanceof HTMLButtonElement) chip.setAttribute('tabindex', '-1');
  return chip;
}

export function createWorkList() {
  const closed = new Set(); // refs of the branches the person closed
  const tree = h('ul', { class: 'gv-tree', id: 'work-tree', role: 'tree', 'aria-label': 'Epics, stories and tasks' });
  let current = null; // what the page last handed over
  let chosen = null;

  // A row that takes the focus (by a click, or the keys) becomes the stop of the tree, and its chip the stop after it.
  tree.addEventListener('focusin', (event) => {
    const item = event.target.closest?.('li[role=treeitem]');
    if (item && event.target === item) tree.querySelectorAll('li[role=treeitem]').forEach((other) => stopOf(other, other === item));
  });

  const select = (node) => openItem(node.ref);

  function relation(node, type, outgoing) {
    const refs = store.links
      .filter((link) => link.link_type === type && (outgoing ? link.source === node.ref : link.target === node.ref))
      .map((link) => (outgoing ? link.target : link.source));
    return [...new Set(refs)].sort(byRef);
  }

  function ties(node) {
    const needs = relation(node, 'needs_first', true);
    const needed = relation(node, 'needs_first', false);
    const part = (className, words, refs) =>
      refs.length
        ? h(
            'span',
            { class: `gv-ties ${className}`, dataset: { refs: refs.join(',') } },
            `${words} `,
            refs.map((ref, at) => {
              const other = store.nodes.get(ref);
              return [at ? ', ' : '', itemLink(ref, { class: 'gv-link', tabindex: -1, 'data-other': ref }, other ? `${codeOf(ref)} ${other.title}${other.status === 'done' ? ' (done)' : ''}` : codeOf(ref))];
            }),
          )
        : null;
    return [part('needs', 'Needs first:', needs), part('needed-by', 'Needed first by:', needed)];
  }

  // How many of its tasks are done, in words and as a bar of exactly that share.
  function progress(node, total) {
    const share = total ? Math.round((node.progress_done / total) * 100) : 0;
    return h('span', { class: 'gv-progress' }, `${node.progress_done} of ${plural(total, 'task')} done`, h('span', { class: 'meter', 'aria-hidden': 'true' }, h('span', { css: { width: `${share}%` } })));
  }

  // What else the item is joined to in this preset (what relates to it, the acceptance criteria and notes
  // that concern it, the references that name it). The hierarchy is
  // the tree, and what it needs first is said on the row.
  function joined(dot) {
    const details = connectionsDetails(dot, current, { skip: ['hierarchy', 'needs_first'] });
    details?.querySelector('summary').setAttribute('tabindex', '-1');
    return details;
  }

  // The title of an item is a link to its page.
  function titleOf(node) {
    return itemLink(node.ref, { class: 'gv-title', tabindex: -1 }, node.title);
  }

  // Its size, in the column before its status.
  function sizeOf(node) {
    const chip = itemSizeChip(node);
    chip?.classList.add('gv-size');
    return chip;
  }

  function branch(dot, level, kids) {
    const node = dot.record;
    const children = kids(node.ref);
    const parent = children.length > 0;
    const open = parent && !closed.has(node.ref);
    const total = node.progress_total;
    const key = node.ref;
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
        'aria-label': `${KIND[node.kind].label} ${nodeCode(node.kind, node.number)}: ${node.title}. ${node.status.replace('_', ' ')}.${total !== null ? ` ${node.progress_done} of ${total} tasks done.` : ''}${node.claimed_by ? ` Held by ${node.claimed_by}.` : ''}`,
        dataset: { key, ref: node.ref, kind: node.kind, number: node.number, status: node.status },
        onkeydown: (event) => walk(event, node, parent, open),
      },
      h(
        'div',
        { class: 'gv-row', onclick: (event) => { if (!event.target.closest('button, a, summary, details')) select(node); } },
        parent
          ? h('button', { type: 'button', class: 'gv-twisty', tabindex: -1, 'aria-label': `${open ? 'Close' : 'Open'} ${node.title}`, onclick: () => setOpen(node.ref, !open) }, icon(open ? 'chevron_down' : 'chevron_right', { size: 16 }))
          : h('span', { class: 'gv-twisty none' }),
        h('span', { class: 'gv-kind', title: KIND[node.kind].label }, kindIcon(node.kind, 14), h('span', { class: 'sr-only' }, KIND[node.kind].label)),
        h('span', { class: 'gv-label' }, h('span', { class: 'id' }, `${nodeCode(node.kind, node.number)} `), titleOf(node)),
        sizeOf(node),
        stateChip(node),
        total !== null ? progress(node, total) : null,
        h('div', { class: 'gv-ties-line' }, progressChip(node), openDueChip(node), heldChip(node), ties(node)),
        joined(dot),
      ),
      parent ? h('ul', { role: 'group', hidden: !open }, children.map((child) => branch(child, level + 1, kids))) : null,
    );
    return item;
  }

  // Why the tree is empty, and what to do next.
  function emptyRow(ctx, nothing) {
    let title = 'No work in this scope yet';
    let step = 'Choose another epic, or show the whole project.';
    if (nothing) {
      title = 'No work in this project yet';
      step = 'Create an epic with New item at the top of the page, or ask an agent to make a plan.';
    } else if (ctx.shown.notice) {
      title = ctx.shown.notice;
      step = 'Change the filter or the scope above.';
    }
    return h('li', { role: 'none', class: 'gv-empty' }, h('strong', {}, title), step);
  }

  function draw(ctx) {
    current = ctx;
    chosen = ctx.cfg.sel?.key ?? null;
    const hadFocus = tree.contains(document.activeElement);
    // A status chip that was just used is where the focus returns to, not the row that was the stop.
    const chipKey = hadFocus ? document.activeElement.closest?.('.chip[data-focus-key]')?.dataset.focusKey : null;
    const focused = tree.querySelector('li[tabindex="0"]')?.dataset.key;
    const dots = [...ctx.shown.dots.values()].filter((dot) => dot.isNode);
    const present = new Set(dots.map((dot) => dot.key));
    const kidsOf = new Map();
    for (const dot of dots) {
      const parent = present.has(dot.record.parent) ? dot.record.parent : null;
      if (!kidsOf.has(parent)) kidsOf.set(parent, []);
      kidsOf.get(parent).push(dot);
    }
    const kids = (parent) => (kidsOf.get(parent) || []).sort(byDot);
    const rows = kids(null).map((dot) => branch(dot, 1, kids));
    const nothing = ![...ctx.all.dots.values()].some((dot) => dot.isNode);
    tree.replaceChildren(...(rows.length ? rows : [emptyRow(ctx, nothing)]));
    const stops = [...tree.querySelectorAll('li[role=treeitem]')];
    const wanted = (focused && tree.querySelector(`li[data-key="${focused}"]`)) || (chosen && tree.querySelector(`li[data-key="${chosen}"]`)) || stops[0];
    stops.forEach((item) => stopOf(item, item === wanted));
    // A redraw keeps the focus where it was, and does not take it from anything outside the tree.
    const chip = chipKey ? [...tree.querySelectorAll('.chip[data-focus-key]')].find((one) => one.dataset.focusKey === chipKey) : null;
    if (chip) chip.focus();
    else if (hadFocus && wanted) wanted.focus();
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
    item.querySelector(':scope > .gv-row > .gv-connections')?.setAttribute('open', '');
    if (!tree.contains(document.activeElement)) {
      tree.querySelectorAll('li[role=treeitem]').forEach((other) => stopOf(other, other === item));
    }
    return item;
  }

  function setOpen(ref, open) {
    if (open) closed.delete(ref);
    else closed.add(ref);
    if (current) draw(current);
  }

  const visible = () => [...tree.querySelectorAll('li[role=treeitem]')].filter((item) => !item.parentElement.closest('ul[hidden]'));

  function focusItem(item) {
    tree.querySelectorAll('li[role=treeitem]').forEach((other) => stopOf(other, other === item));
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
      if (parent && !open) setOpen(node.ref, true);
      else if (parent) focusItem(list[at + 1]);
    } else if (key === 'ArrowLeft') {
      if (parent && open) setOpen(node.ref, false);
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
