// The full page of one epic, story or task (#/item?item=story:12): all of its details at once, the
// same ones the detail panel shows and edited the same way, and under them a table of what is under
// it: a story's tasks, an epic's stories with how far each has got. A task has nothing under it, so
// the page says which story it is part of. The table is read from the store, which the clock keeps
// current, so it follows the work as agents change it; the details are the card's, and a draft
// typed in them is never drawn over.

import { Refusal } from './api.js';
import { renderCard } from './card.js';
import { itemLink, ui, viewLink } from './ctx.js';
import { openCreate } from './create.js';
import { announce, day, h, plural, put } from './dom.js';
import { icon } from './icons.js';
import { KIND, PRIORITIES, STATUSES, byRef, codeOf, kindIcon, nodeCode, parseNodeRef, priorityChip, refSlug, statusChip } from './labels.js';
import { stateView } from './records.js';
import { childrenOf, nodeOf, sync, titleOf } from './store.js';

// What each kind has under it, and the words for it.
const UNDER = {
  epic: { kind: 'story', one: 'story', many: 'stories', heading: 'Stories' },
  story: { kind: 'task', one: 'task', many: 'tasks', heading: 'Tasks' },
};

// -- the columns and how they sort ------------------------------------------------------------------

// Urgent first, and the statuses in the order work moves through them.
const PRIORITY_ORDER = Object.fromEntries([...PRIORITIES].reverse().map((name, at) => [name, at]));
const STATUS_ORDER = Object.fromEntries(STATUSES.map((name, at) => [name, at]));

const words = (a, b) => String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
const share = (node) => node.progress_done / node.progress_total;
const quiet = (text) => h('span', { class: 'quiet' }, text);

// A column is its header, what makes one row sort before another (`order`), which rows have nothing
// to sort by and so always come last (`none`), and how its cell is drawn. `only` keeps a column to
// the table of one kind of item.
const COLUMNS = [
  {
    key: 'item',
    label: 'Item',
    order: (a, b) => a.number - b.number,
    cell: (node, code) =>
      h('th', { scope: 'row', class: 'cell-item' }, itemLink(node.ref, { class: 'item-code-link', 'data-focus-key': `link:${node.ref}` }, kindIcon(node.kind, 14), h('span', { class: 'item-code' }, code))),
  },
  {
    key: 'title',
    label: 'Title',
    order: (a, b) => words(a.title, b.title),
    cell: (node) => h('td', { class: 'cell-title' }, node.title),
  },
  {
    key: 'status',
    label: 'Status',
    order: (a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status],
    cell: (node) => h('td', { class: 'cell-status' }, statusChip(node.status)),
  },
  {
    key: 'progress',
    label: 'Progress',
    only: 'epic',
    none: (node) => !node.progress_total,
    order: (a, b) => share(a) - share(b) || a.progress_total - b.progress_total,
    cell: (node) =>
      h(
        'td',
        { class: 'cell-progress' },
        node.progress_total
          ? h(
              'span',
              { class: 'item-progress' },
              h('span', { class: 'meter', 'aria-hidden': 'true' }, h('span', { css: { width: `${Math.round(share(node) * 100)}%` } })),
              h('span', { class: 'item-progress-text' }, `${node.progress_done} of ${node.progress_total} done`),
            )
          : quiet('No tasks'),
      ),
  },
  {
    key: 'priority',
    label: 'Priority',
    order: (a, b) => PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority],
    cell: (node) => h('td', { class: 'cell-priority' }, priorityChip(node.priority)),
  },
  {
    key: 'held',
    label: 'Held by',
    none: (node) => !node.claimed_by,
    order: (a, b) => words(a.claimed_by, b.claimed_by),
    cell: (node) => h('td', { class: 'cell-held' }, node.claimed_by || quiet('—')),
  },
  {
    key: 'due',
    label: 'Due',
    none: (node) => !node.target_on,
    order: (a, b) => (day(a.target_on) < day(b.target_on) ? -1 : day(a.target_on) > day(b.target_on) ? 1 : 0),
    cell: (node) =>
      h(
        'td',
        { class: 'cell-due' },
        node.target_on
          ? h('span', { class: 'item-due' }, h('span', { class: 'item-date' }, day(node.target_on)), node.overdue ? h('span', { class: 'chip danger overdue' }, icon('alert', { size: 13 }), 'Overdue') : null)
          : quiet('—'),
      ),
  },
  {
    key: 'waiting',
    label: 'Waiting for',
    none: (node) => node.waiting_on.length === 0,
    order: (a, b) => byRef(a.waiting_on[0], b.waiting_on[0]),
    cell: (node) =>
      h(
        'td',
        { class: 'cell-waiting' },
        node.waiting_on.length
          ? node.waiting_on.map((ref, at) => [at ? ', ' : '', itemLink(ref, { title: titleOf(ref), 'data-focus-key': `waiting:${node.ref}:${ref}` }, codeOf(ref))])
          : quiet('—'),
      ),
  },
];

// The rows in the order the person chose. A row with nothing in the column is last whichever way it
// goes, and rows that tie keep the order of their numbers.
function sortedRows(rows, sort) {
  const column = COLUMNS.find((one) => one.key === sort.key) || COLUMNS[0];
  const sign = sort.descending ? -1 : 1;
  const empty = (node) => Boolean(column.none?.(node));
  return [...rows].sort((a, b) => {
    if (empty(a) !== empty(b)) return empty(a) ? 1 : -1;
    return (empty(a) ? 0 : sign * column.order(a, b)) || a.number - b.number;
  });
}

// -- the page ---------------------------------------------------------------------------------------

let root = null; // the element the page is drawn in
let current = null; // { ref, kind, state, parts, signature, headSignature } for the item on show
let shownRef; // the item of the address the page was last opened for, whether or not it could be shown
let generation = 0;
let sorting = { key: 'item', descending: false };

// A page with only something to say: the address names nothing, or the item is gone.
function say(message, { refusal = true } = {}) {
  current = null;
  put(
    root,
    h(
      'div',
      { class: 'item-page item-state', id: 'item-page' },
      h('h1', {}, 'Item'),
      stateView(message, { refusal, close: 'Back to the Board', onClose: () => ui.navigate({}, { page: 'board' }) }),
      h('p', {}, viewLink('board', {}, { id: 'item-back' }, 'Back to the Board')),
    ),
  );
}

// The page is a heading that says what the item is, the card (which draws the details and lays them
// out for a page, with no heading of its own) and, under it, what is under the item.
function skeleton(ref, kind) {
  const under = UNDER[kind];
  const parts = {
    title: h('h1', { id: 'item-title', tabindex: -1 }),
    what: h('p', { class: 'page-sub', id: 'item-what' }),
    details: h('section', { class: 'item-details', id: 'item-details', 'aria-labelledby': 'item-details-title' }),
  };
  if (under) {
    parts.heading = h('h2', { id: 'item-children-title' });
    parts.count = h('span', { class: 'item-count', id: 'item-count' });
    parts.add = h('span', { class: 'item-add' });
    parts.region = h('div', { class: 'table-scroll item-region', id: 'item-region', role: 'region', tabindex: 0 });
    parts.empty = h('div', { class: 'empty item-empty', id: 'item-empty' });
    parts.children = h(
      'section',
      { class: 'item-children', id: 'item-children', 'aria-labelledby': 'item-children-title' },
      h('header', { class: 'item-children-head' }, parts.heading, parts.count, parts.add),
      parts.region,
      parts.empty,
    );
  } else {
    parts.children = h('section', { class: 'item-part', id: 'item-part', 'aria-labelledby': 'item-part-title' });
  }
  put(
    root,
    h(
      'div',
      { class: 'item-page', id: 'item-page', dataset: { ref, kind } },
      h('header', { class: 'page-header item-head' }, h('div', { class: 'page-title' }, parts.title, parts.what)),
      h('h2', { class: 'sr-only', id: 'item-details-title' }, `Details of ${codeOf(ref)}`),
      parts.details,
      parts.children,
    ),
  );
  return parts;
}

// What it is, in a line: an epic has no place, a story or a task says what it sits in.
function whatIs(node) {
  const label = KIND[node.kind].label;
  if (node.parent === null) return `${label} with ${plural(childrenOf(node.ref).length, 'story', 'stories')}`;
  return [`${label} in `, itemLink(node.parent, {}, `${codeOf(node.parent)} ${titleOf(node.parent)}`)];
}

// Drawn again only when it reads differently, so that news about something else never takes the
// focus off the link to the parent.
function drawHead(node) {
  const { parts } = current;
  const signature = JSON.stringify([node.title, node.parent, node.parent ? titleOf(node.parent) : childrenOf(node.ref).length]);
  if (signature === current.headSignature) return;
  current.headSignature = signature;
  put(parts.title, h('span', { class: 'item-code' }, nodeCode(node.kind, node.number)), ' ', node.title);
  put(parts.what, whatIs(node));
}

// Keeps the focus where it was inside `place` when that is drawn again, so that sorting, or news
// from the server, never throws a keyboard user out of the table.
function keepFocus(place) {
  const held = document.activeElement;
  const key = held && place.contains(held) ? held.closest('[data-focus-key]')?.dataset.focusKey : null;
  return () => {
    if (key) [...place.querySelectorAll('[data-focus-key]')].find((one) => one.dataset.focusKey === key)?.focus();
  };
}

function headerCell(column) {
  const here = sorting.key === column.key;
  return h(
    'th',
    { scope: 'col', class: `col-${column.key}`, 'aria-sort': here ? (sorting.descending ? 'descending' : 'ascending') : null },
    h('button', { type: 'button', class: 'sort', 'data-sort': column.key, 'data-focus-key': `sort:${column.key}`, onclick: () => sortBy(column) }, column.label, here ? icon('chevron_down', { size: 14, className: 'sort-mark' }) : null),
  );
}

function sortBy(column) {
  sorting = { key: column.key, descending: sorting.key === column.key ? !sorting.descending : false };
  announce(`Sorted by ${column.label}, ${sorting.descending ? 'descending' : 'ascending'}.`);
  drawChildren();
}

function lookButton(node, code) {
  return h(
    'button',
    { type: 'button', class: 'ghost icon-only small quick-look', 'aria-label': `Quick look at ${code}`, title: 'Quick look', 'data-focus-key': `look:${node.ref}`, onclick: () => ui.navigate({ sel: node.ref, panel: null }) },
    icon('search', { size: 16 }),
  );
}

function row(node, columns) {
  const code = nodeCode(node.kind, node.number);
  return h(
    'tr',
    { id: `row-${refSlug(node.ref)}`, dataset: { ref: node.ref, kind: node.kind, number: node.number, status: node.status } },
    columns.map((column) => column.cell(node, code)),
    h('td', { class: 'cell-look' }, lookButton(node, code)),
  );
}

function table(node, rows) {
  const columns = COLUMNS.filter((column) => !column.only || column.only === node.kind);
  return h(
    'table',
    { class: 'item-table', id: 'item-table', 'aria-describedby': 'item-count' },
    h('caption', { class: 'sr-only' }, `${UNDER[node.kind].heading} in ${nodeCode(node.kind, node.number)}`),
    h('thead', {}, h('tr', {}, columns.map(headerCell), h('th', { scope: 'col', class: 'col-look' }, h('span', { class: 'sr-only' }, 'Quick look')))),
    h('tbody', {}, sortedRows(rows, sorting).map((one) => row(one, columns))),
  );
}

function addButton(node, primary) {
  const under = UNDER[node.kind];
  return h(
    'button',
    { type: 'button', id: 'new-child', class: primary ? 'primary' : 'small', 'data-focus-key': 'new-child', onclick: () => openCreate({ kind: under.kind, parent: node.ref }) },
    icon('plus', { size: 14 }),
    `New ${under.one}`,
  );
}

function drawTable(node) {
  const { parts } = current;
  const under = UNDER[node.kind];
  const rows = childrenOf(node.ref);
  const label = `${under.heading} in ${nodeCode(node.kind, node.number)}`;
  put(parts.heading, label);
  put(parts.count, plural(rows.length, under.one, under.many));
  parts.region.setAttribute('aria-label', label);
  // Drawn again only when what it shows, or the way it is sorted, has changed.
  const signature = JSON.stringify([
    node.ref,
    sorting,
    rows.map((one) => [one.ref, one.title, one.status, one.priority, one.claimed_by, one.target_on, one.overdue, one.waiting_on.map((ref) => [ref, titleOf(ref)]), one.progress_done, one.progress_total]),
  ]);
  if (signature === current.signature) return;
  current.signature = signature;
  const restore = keepFocus(parts.children);
  put(parts.add, rows.length ? addButton(node, false) : null);
  parts.region.hidden = rows.length === 0;
  parts.empty.hidden = rows.length > 0;
  if (rows.length) {
    put(parts.region, table(node, rows));
    put(parts.empty);
  } else {
    put(parts.region);
    put(parts.empty, h('p', { class: 'empty-text' }, h('strong', {}, `No ${under.many} yet.`)), addButton(node, true));
  }
  restore();
}

// A task has no table: it says what it is part of.
function drawPart(node) {
  const { parts } = current;
  const story = nodeOf(node.parent);
  const epic = story ? nodeOf(story.parent) : null;
  const signature = JSON.stringify([node.parent, story?.title, story?.progress_done, story?.progress_total, epic?.ref, epic?.title]);
  if (signature === current.signature) return;
  current.signature = signature;
  put(
    parts.children,
    node.parent ? h('h2', { id: 'item-part-title' }, 'Part of ', itemLink(node.parent, { 'data-focus-key': 'part-story' }, `${codeOf(node.parent)} ${titleOf(node.parent)}`)) : null,
    story
      ? h(
          'p',
          { class: 'quiet item-part-note' },
          epic ? ['In ', itemLink(epic.ref, {}, `${codeOf(epic.ref)} ${epic.title}`), '. '] : null,
          story.progress_total ? `${story.progress_done} of ${plural(story.progress_total, 'task')} in the story are done.` : null,
        )
      : null,
  );
}

function drawChildren() {
  const node = nodeOf(current.ref);
  if (!node) return;
  if (UNDER[node.kind]) drawTable(node);
  else drawPart(node);
  markSelected();
}

// The row whose quick look is open in the panel.
function markSelected() {
  if (!current) return;
  const chosen = ui.params.get('sel');
  for (const one of current.parts.children.querySelectorAll('tbody tr')) {
    const mine = one.dataset.ref === chosen;
    one.classList.toggle('selected', mine);
    const link = one.querySelector('.item-code-link');
    if (mine) link?.setAttribute('aria-current', 'true');
    else link?.removeAttribute('aria-current');
  }
}

// Draws the title and what is under the item from the store; false when the store has no such item.
function drawAll() {
  const node = nodeOf(current.ref);
  if (!node) return false;
  drawHead(node);
  drawChildren();
  return true;
}

function gone(kind, ref) {
  say(`${KIND[kind].label} ${codeOf(ref)} is no longer there.`, { refusal: false });
}

async function open(ref) {
  const mine = ++generation;
  if (current) current.state.dead = true;
  if (shownRef !== ref) sorting = { key: 'item', descending: false };
  shownRef = ref;
  const parsed = parseNodeRef(ref);
  if (!parsed) {
    say('That address does not name an epic, story or task. It looks like #/item?item=story:12.');
    return;
  }
  const state = {};
  const parts = skeleton(ref, parsed.kind);
  current = { ref, kind: parsed.kind, state, parts, signature: null, headSignature: null };
  drawAll();
  try {
    await renderCard(parts.details, ref, state, { page: true, layout: 'wide' });
  } catch (error) {
    if (mine !== generation) return;
    if (!(error instanceof Refusal)) throw error;
    say(error.message);
    return;
  }
  if (mine !== generation) return;
  // The item is there, but this copy of the work has not heard of it yet (it was made a moment ago).
  if (!nodeOf(ref)) await sync().catch(() => {});
  if (mine !== generation) return;
  if (!drawAll()) {
    gone(parsed.kind, ref);
    return;
  }
  // The quick look takes the focus when it is open; otherwise the page's title does (the card
  // knows where it is), unless the person has meanwhile gone to type somewhere.
  const held = document.activeElement;
  if (!ui.params.get('sel') && !(held instanceof Element && held.matches('input, textarea, select'))) state.focus?.();
}

// -- keeping the details true -----------------------------------------------------------------------

const named = (value) => value !== null && value !== undefined;

// What an event says about the item on show: whether it names the item itself (so that the page can
// say who changed it), and whether the details may now read differently. They also read the items
// around it and the facts, decisions and requirements that concern it, and an event that names no
// item at all may be about any of them.
function relevance(event, node, around) {
  const direct = event.node === node.ref || event.other_node === node.ref;
  const rewording = JSON.stringify([event.before, event.after]).includes(`[[${node.ref}]]`);
  const context = named(event.fact_id) || named(event.decision_id) || named(event.rule_id) || !named(event.node);
  return { direct, touches: direct || rewording || context || around.has(event.node) || around.has(event.other_node) };
}

// Whether the person is in the middle of typing in the details, in which case nothing is replaced.
function typing(state, place) {
  const field = document.activeElement;
  const inside = field && place.contains(field) && field.matches('input[type=text], textarea');
  return Boolean(state.dirty || (inside && field.value !== ''));
}

function banner(text, onLoad) {
  const { details } = current.parts;
  details.querySelector('.changed')?.remove();
  details.prepend(
    h('div', { class: 'notice changed', role: 'status' }, icon('info', { size: 16 }), h('span', {}, text), h('button', { type: 'button', class: 'link load-current', onclick: onLoad }, 'Load the current version')),
  );
}

// -- what the shell calls ---------------------------------------------------------------------------

const NEEDS_ITEM = 'This page needs an item. The address looks like #/item?item=story:12.';

export function mount(element, params) {
  root = element;
  shownRef = undefined;
  current = null;
  const ref = params.get('item');
  if (ref === null) {
    shownRef = null;
    say(NEEDS_ITEM);
  } else open(ref);
}

export function unmount() {
  generation += 1;
  if (current) current.state.dead = true;
  current = null;
  root = null;
  shownRef = undefined;
}

export function update(params) {
  const ref = params.get('item');
  if (ref === null) {
    if (shownRef !== null) {
      shownRef = null;
      generation += 1;
      if (current) current.state.dead = true;
      say(NEEDS_ITEM);
    }
  } else if (ref !== shownRef) open(ref);
  else markSelected();
}

// Something changed on the server. The table is read from the store, which has already heard of it;
// the details are fetched again unless the person is typing in them.
export async function refresh(events) {
  if (!current) return;
  const { ref, kind, state, parts } = current;
  const node = nodeOf(ref);
  if (!node) {
    state.dead = true;
    gone(kind, ref);
    return;
  }
  drawAll();
  if (!state.reload) return;
  const around = new Set([node.parent, ...childrenOf(node.ref).map((one) => one.ref), ...node.waiting_on].filter(Boolean));
  const seen = events.map((event) => relevance(event, node, around));
  if (!seen.some((one) => one.touches)) return;
  if (typing(state, parts.details)) {
    const who = events.find((event, at) => seen[at].direct);
    banner(`${who ? `This item changed (${who.actor})` : 'Something that belongs with this item changed'} while you were typing, and what you typed is still here.`, () => state.reload(true));
    return;
  }
  try {
    await state.reload(false);
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    parts.details.querySelector('.item-problem')?.remove();
    parts.details.prepend(h('p', { class: 'notice refusal item-problem', role: 'alert' }, icon('alert', { size: 16 }), h('span', {}, error.message)));
    return;
  }
  if (state.skipped) banner('This item changed while you were choosing something, and what you chose is still here.', () => state.reload(true));
}
