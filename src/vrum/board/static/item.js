// The page of one epic, story, task, acceptance criterion or note (#/item?item=story:12): all of
// its details at once, the same ones an agent reads and edited the same way, and a list of its
// sections that is always in reach. An epic, a story and a task also have a table of what is under
// them (a story's tasks, an epic's stories with how far each has got, a task's relations), and
// previous and next among their siblings, so that a person can go from one to the next without going
// back to a list. What concerns an item (acceptance criteria, notes) is drawn by the card
// (card.js) and by records.js as tables across the whole width of the page. The Agent view button in the
// header opens what an agent receives for the item (agent-view.js), for everyone, a viewer too.
//
// The tables of work are read from the store, which the clock keeps current, so they follow the work
// as agents change it; the details are the card's, and a draft typed in them is never drawn over.

import { openAgentView } from './agent-view.js';
import { Refusal } from './api.js';
import { renderCard } from './card.js';
import { backToView, can, itemLink, openItem, ui } from './ctx.js';
import { openCreate } from './create.js';
import { opens } from './entries.js';
import { announce, day, h, plural, put } from './dom.js';
import { icon } from './icons.js';
import { KIND, PRIORITIES, STATUS, STATUSES, actorLabel, byRef, codeOf, isNodeKind, kindIcon, nodeCode, parseItemRef, pointsWords, refSlug, sizeChip } from './labels.js';
import { loadingView, pageTitle, renderNote, renderRule, stateView } from './records.js';
import { priorityChip, sizeControl, statusChip } from './status-menu.js';
import { childrenOf, epics, nodeOf, store, sync, titleOf } from './store.js';

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

// A row's size: a task's own, which a person changes in place; a story with tasks adds up its tasks'; an epic's
// stories show what they add up to, or their own when they have no tasks.
function sizeCell(node) {
  if (node.kind === 'task') return sizeControl(node, { onChanged: redrawTables, compact: true });
  return sizeChip(node, { rollup: node.progress_total > 0 }) || quiet('Not set');
}

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
    cell: (node) => h('td', { class: 'cell-title' }, itemLink(node.ref, { class: 'record-title', tabindex: -1 }, node.title)),
  },
  {
    key: 'status',
    label: 'Status',
    order: (a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status],
    cell: (node) => h('td', { class: 'cell-status' }, statusChip(node, { onChanged: redrawTables })),
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
    key: 'size',
    label: 'Size',
    none: (node) => !node.points_total,
    order: (a, b) => a.points_total - b.points_total,
    cell: (node) => h('td', { class: 'cell-size' }, sizeCell(node)),
    foot: (rows) => {
      const points = rows.reduce((sum, one) => sum + (one.points_total || 0), 0);
      const unsized = rows.reduce((sum, one) => sum + (one.unsized || 0), 0);
      return points || unsized
        ? h('td', { class: 'cell-size' }, h('span', { class: 'item-points' }, points ? pointsWords(points) : null, unsized ? h('span', { class: 'chip size size-none' }, `${unsized} not sized`) : null))
        : h('td', { class: 'cell-size' });
    },
  },
  {
    key: 'priority',
    label: 'Priority',
    order: (a, b) => PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority],
    cell: (node) => h('td', { class: 'cell-priority' }, priorityChip(node, { onChanged: redrawTables })),
  },
  {
    key: 'held',
    label: 'Held by',
    none: (node) => !node.claimed_by,
    order: (a, b) => words(a.claimed_by, b.claimed_by),
    cell: (node) => h('td', { class: 'cell-held' }, node.claimed_by ? actorLabel(node.claimed_by, node.claimed_by_kind) : quiet('Nobody')),
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
          : quiet('Not set'),
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
          : quiet('Nothing'),
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

// -- the sections of each kind of page ------------------------------------------------------------------

// Where each section is, by the name its `data-part` has on the page (the table of work, which is
// not in a part of its own, is `table`), in the order a person meets them.
const SECTIONS = {
  epic: [['edit', 'Details'], ['table', 'Stories'], ['context', 'Notes'], ['links', 'Links'], ['history', 'History']],
  story: [['edit', 'Details'], ['table', 'Tasks'], ['requirements', 'Acceptance criteria'], ['definition', 'Definition of done'], ['context', 'Notes'], ['links', 'Links'], ['history', 'History']],
  task: [['edit', 'Details'], ['table', 'Relations'], ['requirements', 'Acceptance criteria'], ['definition', 'Definition of done'], ['context', 'Notes'], ['links', 'Links'], ['history', 'History']],
  rule: [['edit', 'Details'], ['context', 'Notes'], ['mentions', 'Mentioned in'], ['history', 'History']],
  note: [['record', 'What it says'], ['why', 'Why'], ['confirmation', 'Confirmation'], ['concerns', 'Applies to'], ['replaces', 'Replaces and replaced by'], ['mentions', 'Mentioned in'], ['history', 'History']],
};

// -- the page ---------------------------------------------------------------------------------------

let root = null; // the element the page is drawn in
let current = null; // { ref, kind, from, state, parts, signature, headSignature, navSignature, pagerSignature, tableCount } for the item on show
let shownRef; // the item of the address the page was last opened for, whether or not it could be shown
let generation = 0;
let sorting = { key: 'item', descending: false };
let pagerFocus = null; // the pager button the person used, which gets the focus on the next item

const KIND_WORDS = { epic: 'epic', story: 'story', task: 'task', rule: 'acceptance criterion', note: 'note' };

// A page with only something to say: the address names nothing, or the item is gone. The way back
// is to the view the person was in.
function say(message, { refusal = true } = {}) {
  current = null;
  const back = backToView();
  put(
    root,
    h(
      'div',
      { class: 'item-page item-state', id: 'item-page' },
      h('h1', { id: 'item-title', tabindex: -1 }, 'Nothing to show here'),
      stateView(message, { refusal }),
      h('p', { class: 'item-back' }, h('a', { id: 'item-back', href: back.hash }, icon('chevron_right', { size: 14, className: 'flip' }), `Back to ${back.label}`)),
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

// The page is a header that says what the item is and how it stands, with the way to move to its
// neighbours, a list of its sections, and the details, which the card or the record's page draws
// and lays out for a page. A task also has a card saying what it is part of, above its details.
// In the header, `edits` is where the card or the record's page puts what a person may do to the item,
// and the Agent view button after it is there for everyone, since it only reads.
function skeleton(ref, kind) {
  const edits = h('span', { class: 'item-edits' });
  const parts = {
    title: h('h1', { id: 'item-title', tabindex: -1 }),
    what: isNodeKind(kind) ? null : h('p', { class: 'page-sub', id: 'item-what' }),
    pager: isNodeKind(kind) ? h('nav', { class: 'item-pager', id: 'item-pager' }) : null,
    edits,
    actions: h('div', { class: 'page-actions item-actions' }, edits, h('button', { type: 'button', id: 'item-agent-view', onclick: () => openAgentView(ref) }, icon('agent', { size: 16 }), 'Agent view')),
    summary: h('div', { class: 'card-summary' }),
    nav: h('nav', { class: 'item-nav', 'aria-label': 'Sections of this page' }),
    details: h('section', { class: 'item-details', id: 'item-details', 'aria-labelledby': 'item-details-title' }),
  };
  if (UNDER[kind]) parts.table = tableSection(CHILDREN_IDS);
  else if (kind === 'task') Object.assign(parts, { table: tableSection(RELATION_IDS), part: h('section', { class: 'item-part', id: 'item-part', 'aria-labelledby': 'item-part-title' }) });
  parts.children = parts.table ? parts.table.section : null;
  put(
    root,
    h(
      'div',
      { class: 'item-page', id: 'item-page', dataset: { ref, kind } },
      h('header', { class: 'page-header item-head' }, h('div', { class: 'page-title' }, parts.title, parts.what), h('div', { class: 'item-tools' }, parts.pager, parts.actions), parts.summary),
      parts.nav,
      parts.part || null,
      h('h2', { class: 'sr-only', id: 'item-details-title' }, `Details of ${isNodeKind(kind) ? codeOf(ref) : `${KIND[kind].label} #${parseItemRef(ref).number}`}`),
      parts.details,
    ),
  );
  return parts;
}

// Drawn again only when its title changes.
function drawHead(node) {
  const { parts } = current;
  const signature = JSON.stringify([node.title]);
  if (signature === current.headSignature) return;
  current.headSignature = signature;
  put(parts.title, pageTitle(node.kind, nodeCode(node.kind, node.number), node.title));
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

// -- previous and next --------------------------------------------------------------------------------

// Its siblings in the order the server lists them: the epics of the project, the stories of an epic,
// the tasks of a story, each by number.
const siblingsOf = (node) => (node.kind === 'epic' ? epics() : childrenOf(node.parent).sort((a, b) => a.number - b.number));

function pagerButton(direction, other, noun) {
  const previous = direction === 'previous';
  const code = other ? codeOf(other.ref) : null;
  const label = other ? `${previous ? 'Previous' : 'Next'} ${noun}: ${code} ${other.title}` : `No ${direction} ${noun}`;
  return h(
    'button',
    {
      type: 'button',
      id: previous ? 'item-prev' : 'item-next',
      class: `pager-button ${previous ? 'previous' : 'next'}`,
      disabled: !other,
      'aria-label': label,
      'aria-keyshortcuts': previous ? '[' : ']',
      title: other ? `${label} (${previous ? '[' : ']'})` : label,
      'data-focus-key': `pager:${direction}`,
      onclick: () => {
        pagerFocus = previous ? 'item-prev' : 'item-next';
        openItem(other.ref);
      },
    },
    previous ? icon('chevron_right', { size: 14, className: 'flip' }) : null,
    code ? h('span', { class: 'item-code' }, code) : null,
    previous ? null : icon('chevron_right', { size: 14 }),
  );
}

function drawPager(node) {
  const { parts } = current;
  if (!parts.pager) return;
  const all = siblingsOf(node);
  const at = all.findIndex((one) => one.ref === node.ref);
  const before = at > 0 ? all[at - 1] : null;
  const after = at >= 0 && at < all.length - 1 ? all[at + 1] : null;
  const signature = JSON.stringify([at, all.length, before?.ref, before?.title, after?.ref, after?.title, node.parent ? titleOf(node.parent) : '']);
  if (signature === current.pagerSignature) return;
  current.pagerSignature = signature;
  const noun = KIND_WORDS[node.kind];
  const scope = node.kind === 'epic' ? 'epics in this project' : `${KIND_WORDS[node.kind] === 'story' ? 'stories' : 'tasks'} in ${codeOf(node.parent)}`;
  const restore = keepFocus(parts.pager);
  parts.pager.setAttribute('aria-label', `Move between the ${scope}`);
  put(parts.pager, pagerButton('previous', before, noun), h('span', { class: 'item-position', id: 'item-position' }, at >= 0 ? `${at + 1} of ${all.length}` : ''), pagerButton('next', after, noun));
  restore();
}

// The [ and ] keys go to the previous and the next one, unless the person is typing or a dialog is open.
function onKey(event) {
  if (!current || event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key !== '[' && event.key !== ']') return;
  const target = event.target;
  if (target instanceof Element && target.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]')) return;
  if (document.querySelector('dialog[open]')) return;
  const button = document.getElementById(event.key === '[' ? 'item-prev' : 'item-next');
  if (!button || button.disabled) return;
  event.preventDefault();
  button.click();
}

// -- the list of sections -----------------------------------------------------------------------------

const targetOf = (key) => {
  if (!current) return null;
  const found = key === 'table' ? current.parts.children : current.parts.details.querySelector(`[data-part="${key}"]`);
  return found && found.isConnected ? found : null;
};

function jump(key) {
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
  const atEnd = window.scrollY > 0 && window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
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

// The counts come from the page's sections (what each holds) and from the table.
function drawNav() {
  if (!current || !current.state.counts) return;
  const { parts, state, kind } = current;
  const items = SECTIONS[kind].filter(([key]) => targetOf(key)).map(([key, name]) => [key, name, key === 'table' ? current.tableCount : (state.counts[key] ?? null), state.countTitles?.[key] ?? null]);
  const signature = JSON.stringify(items);
  if (signature === current.navSignature) return;
  current.navSignature = signature;
  const restore = keepFocus(parts.nav);
  put(
    parts.nav,
    h(
      'ul',
      { class: 'plain' },
      items.map(([key, name, count, says]) =>
        h(
          'li',
          {},
          h(
            'button',
            { type: 'button', class: 'item-jump', 'data-jump': key, 'data-focus-key': `jump:${key}`, onclick: () => jump(key) },
            name,
            count ? h('span', { class: 'item-jump-count', title: says }, String(count)) : null,
            count && says ? h('span', { class: 'sr-only' }, `, ${says}`) : null,
          ),
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

function row(node, columns, how = null) {
  const code = nodeCode(node.kind, node.number);
  return h(
    'tr',
    { id: `row-${refSlug(node.ref)}`, dataset: { ref: node.ref, kind: node.kind, number: node.number, status: node.status }, onclick: opens(node.ref) },
    columns.map((column) => column.cell(node, code, how)),
  );
}

function table(node, rows) {
  const columns = COLUMNS.filter((column) => !column.only || column.only === node.kind);
  return h(
    'table',
    { class: 'item-table', id: CHILDREN_IDS.table, 'aria-describedby': CHILDREN_IDS.count },
    h('caption', { class: 'sr-only' }, `${UNDER[node.kind].heading} in ${nodeCode(node.kind, node.number)}`),
    h('thead', {}, h('tr', {}, columns.map(headerCell))),
    h('tbody', {}, sortedRows(rows, sorting).map((one) => row(one, columns))),
    sizeFoot(columns, rows),
  );
}

// The line under the rows that adds up their sizes, when any of them has one or is waiting for one.
function sizeFoot(columns, rows) {
  const total = columns.find((column) => column.key === 'size').foot(rows);
  if (!total.firstChild) return null;
  return h('tfoot', {}, h('tr', { class: 'item-total' }, columns.map((column) => (column.key === 'size' ? total : column.key === 'item' ? h('th', { scope: 'row' }, 'Total') : h('td')))));
}

// A task's table is not sorted by the person: what it needs first, then what needs it.
function relationTable(node, relations) {
  return h(
    'table',
    { class: 'item-table relation-table', id: RELATION_IDS.table, 'aria-describedby': RELATION_IDS.count },
    h('caption', { class: 'sr-only' }, `Relations of ${nodeCode(node.kind, node.number)}`),
    h('thead', {}, h('tr', {}, RELATION_COLUMNS.map((column) => h('th', { scope: 'col', class: `col-${column.key}` }, h('span', { class: 'col-label' }, column.label))))),
    h('tbody', {}, relations.map(({ how, node: other }) => row(other, RELATION_COLUMNS, how))),
  );
}

function addButton(node) {
  if (!can('write')) return null;
  const under = UNDER[node.kind];
  return h(
    'button',
    { type: 'button', id: 'new-child', class: 'small', 'data-focus-key': 'new-child', onclick: () => openCreate({ kind: under.kind, parent: node.ref }) },
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
  put(view.heading, under.heading);
  put(view.count, `(${rows.length})`);
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
    rows.map((one) => [one.ref, one.version, one.title, one.status, one.priority, one.size, one.points_total, one.unsized, one.claimed_by, one.claimed_by_kind, one.target_on, one.overdue, one.waiting_on.map((ref) => [ref, titleOf(ref)]), one.progress_done, one.progress_total]),
  ]);
  if (signature === current.signature) return;
  current.signature = signature;
  const restore = keepFocus(parts.children);
  put(view.add, rows.length ? addButton(node) : null);
  view.region.hidden = rows.length === 0;
  view.empty.hidden = rows.length > 0;
  if (rows.length) {
    put(view.region, table(node, rows));
    put(view.empty);
  } else {
    put(view.region);
    put(view.empty, h('p', { class: 'empty-text' }, h('strong', {}, `No ${under.many} yet.`)), addButton(node));
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
  put(view.heading, 'Relations');
  put(view.count, `(${relations.length})`);
  view.region.setAttribute('aria-label', label);
  if (current.tableCount !== relations.length) {
    current.tableCount = relations.length;
    drawNav();
  }
  const signature = JSON.stringify([node.ref, relations.map(({ how, node: other }) => [how, other.ref, other.version, other.title, other.status, other.priority, other.claimed_by, other.claimed_by_kind, other.target_on, other.overdue])]);
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
      h('p', { class: 'empty-text' }, h('strong', {}, 'No relations yet.')),
      can('write') ? h('button', { type: 'button', class: 'small', 'data-focus-key': 'add-link', onclick: () => document.getElementById('link-target')?.focus() }, icon('link', { size: 14 }), 'Add a link') : null,
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
        ? h('p', { class: 'quiet item-part-note' }, epic ? ['In ', itemLink(epic.ref, {}, `${codeOf(epic.ref)} ${epic.title}`)] : null)
        : null,
    ),
    story?.progress_total
      ? h(
          'span',
          { class: 'item-part-progress' },
          h('span', { class: 'item-part-progress-text' }, `${story.progress_done} of ${plural(story.progress_total, 'task')} done`),
          h('span', { class: 'meter item-part-meter', 'aria-hidden': 'true' }, h('span', { css: { width: `${Math.round((story.progress_done / story.progress_total) * 100)}%` } })),
        )
      : null,
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
}

// What a chip in a table changed is already in the store: the tables read it again, whether or not the
// board has heard of the change yet.
function redrawTables() {
  if (!current) return;
  current.signature = null;
  drawChildren();
}

// Draws the title, the way to its neighbours and what is under the item from the store; false when
// the store has no such item.
function drawAll() {
  const node = nodeOf(current.ref);
  if (!node) return false;
  drawHead(node);
  drawPager(node);
  drawChildren();
  return true;
}

function gone(kind, ref) {
  say(`${KIND[kind].label} ${isNodeKind(kind) ? codeOf(ref) : `#${parseItemRef(ref).number}`} is no longer there.`, { refusal: false });
}

// What the page asks the card, or the record's page, to draw it with.
const placesOf = (parts, state) => ({
  title: parts.title,
  what: parts.what,
  summary: parts.summary,
  actions: parts.edits,
  table: parts.children,
  drawn: () => {
    if (!current || current.state !== state) return;
    // The card has just put what it read into the store: the header and the tables follow it.
    if (isNodeKind(current.kind)) drawAll();
    drawNav();
  },
});

const BAD_ADDRESS = 'That address does not name an epic, story, task, acceptance criterion or note.';

async function open(ref) {
  const mine = ++generation;
  if (current) current.state.dead = true;
  if (shownRef !== ref) sorting = { key: 'item', descending: false };
  if (shownRef !== undefined && shownRef !== ref) window.scrollTo(0, 0);
  shownRef = ref;
  const parsed = parseItemRef(ref);
  if (!parsed) {
    say(BAD_ADDRESS);
    return;
  }
  const state = { open: {} };
  const parts = skeleton(ref, parsed.kind);
  current = { ref, kind: parsed.kind, from: ui.from, state, parts, signature: null, headSignature: null, navSignature: null, pagerSignature: null, partSignature: null, tableCount: null };
  const node = isNodeKind(parsed.kind);
  if (node) drawAll();
  else put(parts.title, pageTitle(parsed.kind, `${KIND[parsed.kind].label} #${parsed.number}`, ''));
  put(parts.details, loadingView());
  const places = placesOf(parts, state);
  try {
    if (node) await renderCard(parts.details, ref, state, places);
    else if (parsed.kind === 'rule') await renderRule(parts.details, parsed.number, state, places);
    else await renderNote(parts.details, parsed.number, state, places);
  } catch (error) {
    if (mine !== generation) return;
    if (!(error instanceof Refusal)) throw error;
    say(error.message);
    return;
  }
  if (mine !== generation) return;
  if (node) {
    // The item is there, but this copy of the work has not heard of it yet (it was made a moment ago).
    if (!nodeOf(ref)) await sync().catch(() => {});
    if (mine !== generation) return;
    if (!drawAll()) {
      gone(parsed.kind, ref);
      return;
    }
  }
  // The page's title takes the focus, unless the person went on a pager button, or has meanwhile
  // gone to type somewhere.
  const held = document.activeElement;
  const pager = pagerFocus ? document.getElementById(pagerFocus) : null;
  pagerFocus = null;
  if (pager && !pager.disabled) pager.focus();
  else if (!(held instanceof Element && held.matches('input, textarea, select'))) state.focus?.();
}

// -- keeping the details true -----------------------------------------------------------------------

const named = (value) => value !== null && value !== undefined;

// What an event says about the item on show: whether it names the item itself (so that the page can
// say who changed it), and whether the page may now read differently. A work item also reads the items
// around it and the notes and acceptance criteria that concern it, and an event that names no item
// at all may be about any of them. A note reads what it concerns.
function relevance(event) {
  const { ref, kind, state, parts } = current;
  const { number } = parseItemRef(ref);
  const rewording = JSON.stringify([event.before, event.after]).includes(`[[${ref}]]`);
  if (isNodeKind(kind)) {
    const node = nodeOf(ref);
    const around = new Set([node.parent, ...childrenOf(ref).map((one) => one.ref), ...node.waiting_on].filter(Boolean));
    const direct = event.node === ref || event.other_node === ref;
    const context = named(event.note_id) || named(event.rule_id) || !named(event.node);
    return { direct, touches: direct || rewording || context || around.has(event.node) || around.has(event.other_node) };
  }
  if (kind === 'note') {
    const direct = event.note_id === number || event.other_note_id === number;
    // News about something it concerns changes how the page names it.
    const concerned = state.concerns
      ? (named(event.node) && state.concerns.node.includes(event.node)) ||
        (named(event.rule_id) && state.concerns.rule.includes(event.rule_id)) ||
        (named(event.note_id) && state.concerns.note.includes(event.note_id)) ||
        (named(event.other_note_id) && state.concerns.note.includes(event.other_note_id))
      : false;
    return { direct, touches: direct || rewording || concerned };
  }
  // An acceptance criterion reads the notes that concern it, and the story or task it is on.
  const direct = event.rule_id === number;
  const holder = parts.details.querySelector('.rule-detail')?.dataset.node || null;
  const context = named(event.note_id) || event.node === null;
  return { direct, touches: direct || rewording || context || (holder !== null && (event.node === holder || event.other_node === holder)) };
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

const NEEDS_ITEM = 'This page needs an item to show.';

export function mount(element, params) {
  root = element;
  window.addEventListener('scroll', onScroll, { passive: true });
  document.addEventListener('keydown', onKey);
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
  document.removeEventListener('keydown', onKey);
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
  } else if (ref !== shownRef || (current && current.from !== ui.from)) open(ref);
}

// Something changed on the server. The tables are read from the store, which has already heard of
// it; the details are fetched again unless the person is typing in them.
export async function refresh(events) {
  if (!current) return;
  const { ref, kind, state, parts } = current;
  if (isNodeKind(kind)) {
    if (!nodeOf(ref)) {
      state.dead = true;
      gone(kind, ref);
      return;
    }
    drawAll();
  }
  if (!state.reload) return;
  const seen = events.map((event) => relevance(event));
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
