// The Graph's list of work: the hierarchy of epics, stories and tasks as a tree that opens and
// closes, with each item's status, what it needs first and what needs it, and who holds it.
// Choosing an item shows it in the shared detail panel. The title of an epic or a story is a link to
// its own page (a plain click goes there, a click with a modifier key opens it the way a link
// does), and a small "Quick look" button beside it shows it in the panel; a task's title opens the
// panel. It draws what the Graph page hands it (the same scope the map draws) and keeps nothing of
// its own but which branches are closed.

import { itemLink } from './ctx.js';
import { h, moment, plural } from './dom.js';
import { icon } from './icons.js';
import { KIND, byRef, codeOf, kindIcon, nodeCode, parseNodeRef, statusChip } from './labels.js';
import { openRecord } from './records.js';
import { store } from './store.js';
import { byDot } from './graph-data.js';
import { connectionsDetails } from './list-parts.js';

export function createWorkList() {
  const closed = new Set(); // refs of the branches the person closed
  const tree = h('ul', { class: 'gv-tree', id: 'work-tree', role: 'tree', 'aria-label': 'Epics, stories and tasks' });
  let current = null; // what the page last handed over
  let chosen = null;

  const select = (node) => openRecord(node.kind, node.number);
  const showRef = (ref) => {
    const { kind, number } = parseNodeRef(ref);
    openRecord(kind, number);
  };

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
              return [at ? ', ' : '', h('button', { type: 'button', class: 'link gv-link', tabindex: -1, 'data-other': ref, onclick: () => showRef(ref) }, other ? `${codeOf(ref)} ${other.title}${other.status === 'done' ? ' (done)' : ''}` : codeOf(ref))];
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

  function claim(node) {
    if (node.claimed_by) return h('span', { class: 'gv-claim' }, `Held by ${node.claimed_by} until ${moment(node.claim_expires_at)}`);
    if (node.lapsed_claim_by) return h('span', { class: 'gv-claim lapsed' }, `${node.lapsed_claim_by}’s claim ran out`);
    return null;
  }

  // What else the item is joined to in this preset (what relates to it, the requirements, decisions
  // and facts that concern it, the references that name it), as the map draws it. The hierarchy is
  // the tree, and what it needs first is said on the row.
  function joined(dot) {
    const details = connectionsDetails(dot, current, { skip: ['hierarchy', 'needs_first'] });
    details?.querySelector('summary').setAttribute('tabindex', '-1');
    return details;
  }

  // The title of an epic or story is a link to its page, with a quick look at it in the panel
  // beside it; a task's title is plain, and a click on its row shows it in the panel.
  function titleOf(node) {
    if (node.kind === 'task') return node.title;
    const code = nodeCode(node.kind, node.number);
    return [
      itemLink(node.ref, { class: 'gv-title', tabindex: -1 }, node.title),
      h('button', { type: 'button', class: 'gv-quick ghost icon-only small', tabindex: -1, title: 'Quick look', 'aria-label': `Quick look at ${code}`, onclick: () => select(node) }, icon('search', { size: 14 })),
    ];
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
        'aria-label': `${KIND[node.kind].label} ${node.number}: ${node.title}. ${node.status.replace('_', ' ')}.${total !== null ? ` ${node.progress_done} of ${total} tasks done.` : ''}${node.claimed_by ? ` Held by ${node.claimed_by}.` : ''}`,
        dataset: { key, ref: node.ref, kind: node.kind, number: node.number, status: node.status },
        onkeydown: (event) => walk(event, node, parent, open),
      },
      h(
        'div',
        { class: 'gv-row', onclick: (event) => { if (!event.target.closest('button, a, summary, details')) select(node); } },
        parent
          ? h('button', { type: 'button', class: 'gv-twisty', tabindex: -1, 'aria-label': `${open ? 'Close' : 'Open'} ${node.title}`, onclick: () => setOpen(node.ref, !open) }, icon(open ? 'chevron_down' : 'chevron_right', { size: 16 }))
          : h('span', { class: 'gv-twisty none' }),
        h('span', { class: 'gv-kind' }, kindIcon(node.kind, 14), KIND[node.kind].label),
        h('span', { class: 'gv-label' }, h('span', { class: 'id' }, `${nodeCode(node.kind, node.number)} `), titleOf(node)),
        statusChip(node.status),
        total !== null ? progress(node, total) : null,
        h('div', { class: 'gv-ties-line' }, claim(node), ties(node)),
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
    item.querySelector(':scope > .gv-row > .gv-connections')?.setAttribute('open', '');
    if (!tree.contains(document.activeElement)) {
      tree.querySelectorAll('li[role=treeitem]').forEach((other) => other.setAttribute('tabindex', other === item ? '0' : '-1'));
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
