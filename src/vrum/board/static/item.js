// The full page of one epic, story or task (#/item?item=story:12): all of its details at once, the
// same ones the detail panel shows and edited the same way, a table of what is under it, and a list of
// the sections that is always in reach. A story's table holds its tasks, an epic's its stories with
// how far each has got, and a task's the work it needs first and the work that needs it. A task also
// says, in a card above its details, which story it is part of. The tables are read from the store,
// which the clock keeps current, so they follow the work as agents change it; the details are the
// card's, and a draft typed in them is never drawn over.

import { Refusal } from './api.js';
import { renderCard } from './card.js';
import { itemLink, ui, viewLink } from './ctx.js';
import { openCreate } from './create.js';
import { announce, day, h, plural, put } from './dom.js';
import { icon } from './icons.js';
import { KIND, PRIORITIES, STATUS, STATUSES, byRef, codeOf, kindIcon, nodeCode, parseNodeRef, priorityChip, refSlug, statusChip } from './labels.js';
import { stateView } from './records.js';
import { childrenOf, nodeOf, store, sync, titleOf } from './store.js';

// What each kind has under it, and the words for it. A task has no children: its table is its
// relations.
const UNDER = {
  epic: { kind: 'story', one: 'story', many: 'stories', heading: 'Stories' },
  story: { kind: 'task', one: 'task', many: 'tasks', heading: 'Tasks' },
};

// The ids of the two kinds of table section: what is under an item, and what a task is tied to.
const CHILDREN_IDS = { section: 'item-children', title: 'item-children-title', count: 'item-count', region: 'item-region', empty: 'item-empty', table: 'item-table' };
const RELATION_IDS = { section: 'item-relations', title: 'item-relations-title', count: 'relations-count', region: 'relations-region', empty: 'relations-empty', table: 'relations-table' };

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

// A task's table: how it is tied to the row, then the same columns that say how that work is doing.
const RELATION_COLUMNS = [
  { key: 'relation', label: 'Relation', cell: (_node, _code, how) => h('td', { class: 'cell-relation' }, how) },
  ...['item', 'title', 'status', 'priority', 'held', 'due'].map((key) => COLUMNS.find((column) => column.key === key)),
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
let current = null; // { ref, kind, state, parts, signature, headSignature, navSignature, tableCount } for the item on show
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

// A heading, how many, the way to add one, and the table in a box that scrolls (or a line saying
// there is nothing yet).
function tableSection(ids) {
  const parts = {
    heading: h('h2', { id: ids.title, tabindex: -1 }),
    count: h('span', { class: 'item-count', id: ids.count }),
    breakdown: h('span', { class: 'item-breakdown' }),
    add: h('span', { class: 'item-add' }),
    region: h('div', { class: 'table-scroll item-region', id: ids.region, role: 'region', tabindex: 0 }),
    empty: h('div', { class: 'empty item-empty', id: ids.empty }),
  };
  parts.section = h(
    'section',
    { class: 'item-children', id: ids.section, 'aria-labelledby': ids.title },
    h('header', { class: 'item-children-head' }, parts.heading, parts.count, parts.breakdown, parts.add),
    parts.region,
    parts.empty,
  );
  return parts;
}

// The page is a header that says what the item is and how it stands, a list of its sections, the
// card (which draws the details and lays them out for a page, with no heading of its own) and,
// under it, the table. A task also has a card saying what it is part of, above its details.
function skeleton(ref, kind) {
  const parts = {
    title: h('h1', { id: 'item-title', tabindex: -1 }),
    what: h('p', { class: 'page-sub', id: 'item-what' }),
    actions: h('div', { class: 'page-actions item-actions' }),
    summary: h('div', { class: 'card-summary' }),
    nav: h('nav', { class: 'item-nav', 'aria-label': 'Sections of this page' }),
    details: h('section', { class: 'item-details', id: 'item-details', 'aria-labelledby': 'item-details-title' }),
  };
  if (UNDER[kind]) Object.assign(parts, { table: tableSection(CHILDREN_IDS) });
  else Object.assign(parts, { table: tableSection(RELATION_IDS), part: h('section', { class: 'item-part', id: 'item-part', 'aria-labelledby': 'item-part-title' }) });
  parts.children = parts.table.section;
  put(
    root,
    h(
      'div',
      { class: 'item-page', id: 'item-page', dataset: { ref, kind } },
      h('header', { class: 'page-header item-head' }, h('div', { class: 'page-title' }, parts.title, parts.what), parts.actions, parts.summary),
      parts.nav,
      parts.part || null,
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
  put(parts.title, kindIcon(node.kind, 22), h('span', { class: 'item-code' }, nodeCode(node.kind, node.number)), ' ', node.title);
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

// -- the list of sections -----------------------------------------------------------------------------

// Where each section is, by the name its `data-part` has in the card, in the order a person meets
// them; the table, which is outside the card, comes last.
const SECTIONS = [
  ['edit', 'Details'],
  ['requirements', 'Requirements'],
  ['links', 'Links'],
  ['context', 'Facts and decisions'],
  ['history', 'History'],
];

const targetOf = (key) => (key === 'table' ? current.parts.children : current.parts.details.querySelector(`[data-part="${key}"]`));

function jump(key) {
  if (!current) return;
  const target = targetOf(key);
  if (!target) return;
  const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  target.scrollIntoView({ block: 'start', behavior: calm ? 'auto' : 'smooth' });
  const heading = target.querySelector('h2, h3');
  if (heading) {
    heading.setAttribute('tabindex', '-1');
    heading.focus({ preventScroll: true });
  }
}

// The list of sections says which one is in view: the last whose top has gone up to the list.
let looking = false;
function markCurrent() {
  looking = false;
  if (!current) return;
  const buttons = [...current.parts.nav.querySelectorAll('.item-jump')];
  if (!buttons.length) return;
  const line = current.parts.nav.getBoundingClientRect().bottom + 24;
  const atEnd = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
  let here = buttons[0];
  let nearest = -Infinity;
  for (const button of buttons) {
    const top = targetOf(button.dataset.jump)?.getBoundingClientRect().top;
    // The sections of the side column start beside the ones of the main column, so the nearest one
    // above the line is the one in view, not the last in the list.
    if (top !== undefined && top <= line && top > nearest) {
      here = button;
      nearest = top;
    }
  }
  if (atEnd) here = buttons[buttons.length - 1];
  for (const button of buttons) {
    if (button === here) button.setAttribute('aria-current', 'location');
    else button.removeAttribute('aria-current');
  }
}

function onScroll() {
  if (looking) return;
  looking = true;
  window.requestAnimationFrame(markCurrent);
}

// The counts come from the card (what each of its sections holds) and from the table.
function drawNav() {
  if (!current || !current.state.counts) return;
  const { parts, state } = current;
  const node = nodeOf(current.ref);
  const label = node ? (UNDER[node.kind]?.heading ?? 'Relations') : 'Table';
  const items = [...SECTIONS.map(([key, name]) => [key, name, state.counts[key] ?? null]), ['table', label, current.tableCount]];
  const signature = JSON.stringify(items);
  if (signature === current.navSignature) return;
  current.navSignature = signature;
  const restore = keepFocus(parts.nav);
  put(
    parts.nav,
    h(
      'ul',
      { class: 'plain' },
      items.map(([key, name, count]) =>
        h(
          'li',
          {},
          h('button', { type: 'button', class: 'item-jump', 'data-jump': key, 'data-focus-key': `jump:${key}`, onclick: () => jump(key) }, name, count ? h('span', { class: 'item-jump-count' }, String(count)) : null),
        ),
      ),
    ),
  );
  restore();
  markCurrent();
}

// -- the table ----------------------------------------------------------------------------------------

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

function row(node, columns, how = null) {
  const code = nodeCode(node.kind, node.number);
  return h(
    'tr',
    { id: `row-${refSlug(node.ref)}`, dataset: { ref: node.ref, kind: node.kind, number: node.number, status: node.status } },
    columns.map((column) => column.cell(node, code, how)),
    h('td', { class: 'cell-look' }, lookButton(node, code)),
  );
}

function table(node, rows) {
  const columns = COLUMNS.filter((column) => !column.only || column.only === node.kind);
  return h(
    'table',
    { class: 'item-table', id: CHILDREN_IDS.table, 'aria-describedby': CHILDREN_IDS.count },
    h('caption', { class: 'sr-only' }, `${UNDER[node.kind].heading} in ${nodeCode(node.kind, node.number)}`),
    h('thead', {}, h('tr', {}, columns.map(headerCell), h('th', { scope: 'col', class: 'col-look' }, h('span', { class: 'sr-only' }, 'Quick look')))),
    h('tbody', {}, sortedRows(rows, sorting).map((one) => row(one, columns))),
  );
}

// A task's table is not sorted by the person: what it needs first, then what needs it.
function relationTable(node, relations) {
  return h(
    'table',
    { class: 'item-table relation-table', id: RELATION_IDS.table, 'aria-describedby': RELATION_IDS.count },
    h('caption', { class: 'sr-only' }, `Relations of ${nodeCode(node.kind, node.number)}`),
    h(
      'thead',
      {},
      h(
        'tr',
        {},
        RELATION_COLUMNS.map((column) => h('th', { scope: 'col', class: `col-${column.key}` }, h('span', { class: 'col-label' }, column.label))),
        h('th', { scope: 'col', class: 'col-look' }, h('span', { class: 'sr-only' }, 'Quick look')),
      ),
    ),
    h('tbody', {}, relations.map(({ how, node: other }) => row(other, RELATION_COLUMNS, how))),
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

// How the rows are doing, in a few words: "2 done, 1 in progress, 1 blocked".
function breakdown(rows) {
  const counts = new Map();
  for (const one of rows) counts.set(one.status, (counts.get(one.status) || 0) + 1);
  return ['done', 'in_progress', 'blocked', 'dropped']
    .filter((status) => counts.has(status))
    .map((status) => `${counts.get(status)} ${STATUS[status].label.toLowerCase()}`)
    .join(', ');
}

function drawTable(node) {
  const { parts } = current;
  const view = parts.table;
  const under = UNDER[node.kind];
  const rows = childrenOf(node.ref);
  const label = `${under.heading} in ${nodeCode(node.kind, node.number)}`;
  put(view.heading, label);
  put(view.count, plural(rows.length, under.one, under.many));
  put(view.breakdown, rows.length > 1 ? breakdown(rows) : null);
  view.region.setAttribute('aria-label', label);
  if (current.tableCount !== rows.length) {
    current.tableCount = rows.length;
    drawNav();
  }
  // Drawn again only when what it shows, or the way it is sorted, has changed.
  const signature = JSON.stringify([
    node.ref,
    sorting,
    rows.map((one) => [one.ref, one.title, one.status, one.priority, one.claimed_by, one.target_on, one.overdue, one.waiting_on.map((ref) => [ref, titleOf(ref)]), one.progress_done, one.progress_total]),
  ]);
  if (signature === current.signature) return;
  current.signature = signature;
  const restore = keepFocus(parts.children);
  put(view.add, rows.length ? addButton(node, false) : null);
  view.region.hidden = rows.length === 0;
  view.empty.hidden = rows.length > 0;
  if (rows.length) {
    put(view.region, table(node, rows));
    put(view.empty);
  } else {
    put(view.region);
    put(view.empty, h('p', { class: 'empty-text' }, h('strong', {}, `No ${under.many} yet.`)), addButton(node, true));
  }
  restore();
}

// What a task needs first, and what needs it, from the links the store holds.
function relationsOf(node) {
  const found = [];
  for (const link of store.links) {
    if (link.link_type !== 'needs_first') continue;
    if (link.source === node.ref) found.push({ how: 'Needs first', node: nodeOf(link.target) });
    else if (link.target === node.ref) found.push({ how: 'Needed by', node: nodeOf(link.source) });
  }
  return found.filter((one) => one.node).sort((a, b) => (a.how === b.how ? a.node.number - b.node.number : a.how === 'Needs first' ? -1 : 1));
}

function drawRelations(node) {
  const { parts } = current;
  const view = parts.table;
  const relations = relationsOf(node);
  const label = `Relations of ${nodeCode(node.kind, node.number)}`;
  put(view.heading, label);
  put(view.count, plural(relations.length, 'relation'));
  view.region.setAttribute('aria-label', label);
  if (current.tableCount !== relations.length) {
    current.tableCount = relations.length;
    drawNav();
  }
  const signature = JSON.stringify([node.ref, relations.map(({ how, node: other }) => [how, other.ref, other.title, other.status, other.priority, other.claimed_by, other.target_on, other.overdue])]);
  if (signature === current.signature) return;
  current.signature = signature;
  const restore = keepFocus(parts.children);
  view.region.hidden = relations.length === 0;
  view.empty.hidden = relations.length > 0;
  if (relations.length) {
    put(view.region, relationTable(node, relations));
    put(view.empty);
  } else {
    put(view.region);
    put(
      view.empty,
      h('p', { class: 'empty-text' }, h('strong', {}, 'Nothing is needed first, and nothing waits for it.')),
      h('button', { type: 'button', class: 'small', 'data-focus-key': 'add-link', onclick: () => document.getElementById('link-target')?.focus() }, icon('link', { size: 14 }), 'Add a link'),
    );
  }
  restore();
}

// A task says what it is part of: its story, the epic that is in, and how far the story has got.
function drawPart(node) {
  const { parts } = current;
  const story = nodeOf(node.parent);
  const epic = story ? nodeOf(story.parent) : null;
  const signature = JSON.stringify([node.parent, story?.title, story?.progress_done, story?.progress_total, epic?.ref, epic?.title]);
  if (signature === current.partSignature) return;
  current.partSignature = signature;
  parts.part.hidden = !node.parent;
  put(
    parts.part,
    h(
      'div',
      { class: 'item-part-main' },
      node.parent ? h('h2', { id: 'item-part-title' }, h('span', { class: 'item-part-label' }, 'Part of '), itemLink(node.parent, { 'data-focus-key': 'part-story' }, `${codeOf(node.parent)} ${titleOf(node.parent)}`)) : null,
      story
        ? h(
            'p',
            { class: 'quiet item-part-note' },
            epic ? ['In ', itemLink(epic.ref, {}, `${codeOf(epic.ref)} ${epic.title}`), '. '] : null,
            story.progress_total ? `${story.progress_done} of ${plural(story.progress_total, 'task')} in the story are done.` : null,
          )
        : null,
    ),
    story?.progress_total ? h('span', { class: 'meter item-part-meter', 'aria-hidden': 'true' }, h('span', { css: { width: `${Math.round((story.progress_done / story.progress_total) * 100)}%` } })) : null,
  );
}

function drawChildren() {
  const node = nodeOf(current.ref);
  if (!node) return;
  if (UNDER[node.kind]) drawTable(node);
  else {
    drawPart(node);
    drawRelations(node);
  }
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
  current = { ref, kind: parsed.kind, state, parts, signature: null, headSignature: null, navSignature: null, partSignature: null, tableCount: null };
  drawAll();
  try {
    await renderCard(parts.details, ref, state, {
      page: true,
      summary: parts.summary,
      actions: parts.actions,
      drawn: () => {
        if (current && current.state === state) drawNav();
      },
    });
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
  window.addEventListener('scroll', onScroll, { passive: true });
  shownRef = undefined;
  current = null;
  const ref = params.get('item');
  if (ref === null) {
    shownRef = null;
    say(NEEDS_ITEM);
  } else open(ref);
}

export function unmount() {
  window.removeEventListener('scroll', onScroll);
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

// Something changed on the server. The tables are read from the store, which has already heard of
// it; the details are fetched again unless the person is typing in them.
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
