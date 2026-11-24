// The page of one epic, story, task, acceptance criterion or note (#/item?item=story:12): all of
// its details at once on one page that scrolls, the same ones an agent reads and edited the same way.
// An epic and a story also have a table of what is under them (an epic's stories with how far each has
// got, a story's tasks), an epic, story or task has its Links (links.js), and the three have previous and
// next among their siblings, so that a person can go from one to the next without going back to a list.
// What concerns an item (acceptance criteria, notes) is drawn by the card (card.js) and by records.js as
// tables across the whole width of the page. The Agent view button in the header opens what an agent
// receives for the item (agent-view.js), for everyone, a viewer too.
//
// The tables of work are read from the store, which the clock keeps current, so they follow the work
// as agents change it; the details are the card's, and a draft typed in them is never drawn over.

import { openAgentView } from './agent-view.js';
import { Refusal } from './api.js';
import { renderCard } from './card.js';
import { backToView, can, itemLink, openItem, ui } from './ctx.js';
import { openCreate } from './create.js';
import { opens } from './entries.js';
import { announce, day, h, put } from './dom.js';
import { icon } from './icons.js';
import { KIND, PRIORITIES, STATUS, STATUSES, actorLabel, byRef, codeOf, dueChip, isNodeKind, kindIcon, nodeCode, parseItemRef, pointsWords, refSlug, sizeChip } from './labels.js';
import { linksSection } from './links.js';
import { codeFor } from './references.js';
import { loadingView, pageTitle, rememberFocus, renderNote, renderRule, stateView } from './records.js';
import { priorityChip, sizeControl, statusChip } from './status-menu.js';
import { childrenOf, epics, nodeOf, store, sync, titleOf } from './store.js';

// What each kind has under it, and the words for it. A task has no children.
const UNDER = {
  epic: { kind: 'story', one: 'story', many: 'stories', heading: 'Stories' },
  story: { kind: 'task', one: 'task', many: 'tasks', heading: 'Tasks' },
};

// The ids of the table of what is under an item.
const CHILDREN_IDS = { section: 'item-children', title: 'item-children-title', count: 'item-count', region: 'item-region', table: 'item-table' };

// -- the columns and how they sort ------------------------------------------------------------------

// Urgent first, and the statuses in the order work moves through them.
const PRIORITY_ORDER = Object.fromEntries([...PRIORITIES].reverse().map((name, at) => [name, at]));
const STATUS_ORDER = Object.fromEntries(STATUSES.map((name, at) => [name, at]));

const words = (a, b) => String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
const share = (node) => node.progress_done / node.progress_total;

// A row's size: a task's own, which a person changes in place; a story with tasks adds up its tasks'; an epic's
// stories show what they add up to, or their own when they have no tasks. Nothing when there is none.
function sizeCell(node) {
  if (node.kind === 'task') return sizeControl(node, { onChanged: redrawTables, compact: true });
  return sizeChip(node, { rollup: node.progress_total > 0 });
}

// A column is its header, what makes one row sort before another (`order`), which rows have nothing
// to sort by and so always come last (`none`), and how its cell is drawn. `only` keeps a column to
// the table of one kind of item. A cell with nothing to say is empty.
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
          : null,
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
    cell: (node) => h('td', { class: 'cell-priority' }, node.priority !== 'normal' ? priorityChip(node, { onChanged: redrawTables, compact: true }) : null),
  },
  {
    key: 'held',
    label: 'Held by',
    none: (node) => !node.claimed_by,
    order: (a, b) => words(a.claimed_by, b.claimed_by),
    cell: (node) => h('td', { class: 'cell-held' }, node.claimed_by ? actorLabel(node.claimed_by, node.claimed_by_kind) : null),
  },
  {
    key: 'due',
    label: 'Due',
    none: (node) => !node.target_on,
    order: (a, b) => (day(a.target_on) < day(b.target_on) ? -1 : day(a.target_on) > day(b.target_on) ? 1 : 0),
    cell: (node) => h('td', { class: 'cell-due' }, dueChip(node)),
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
        h(
          'ul',
          { class: 'plain waiting-list' },
          node.waiting_on.map((ref) =>
            h('li', {}, itemLink(ref, { class: 'waiting-link', title: `${codeOf(ref)} ${titleOf(ref)}`, 'data-focus-key': `waiting:${node.ref}:${ref}` }, h('span', { class: 'item-code' }, codeOf(ref)), ` ${titleOf(ref)}`)),
          ),
        ),
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
let current = null; // { ref, kind, from, state, parts, signature, headSignature, pagerSignature } for the item on show
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
    heading: h('h2', { id: ids.title }),
    count: h('span', { class: 'item-count', id: ids.count }),
    breakdown: h('span', { class: 'item-breakdown' }),
    add: h('span', { class: 'item-add' }),
    region: h('div', { class: 'table-scroll item-region', id: ids.region, role: 'region', tabindex: 0 }),
  };
  parts.section = h(
    'section',
    { class: 'item-children', id: ids.section, 'aria-labelledby': ids.title },
    h('header', { class: 'item-children-head' }, parts.heading, parts.count, parts.breakdown, parts.add),
    parts.region,
  );
  return parts;
}

// The page is a header that says what the item is and how it stands (its properties in one row), with the way to
// move to its neighbours, and the details, which the card or the record's page draws and lays out for a page: one
// column of sections, each under the other. In the header, `edits` is where the card or the record's page puts what
// a person may do to the item, and the Agent view button after it is there for everyone, since it only reads.
function skeleton(ref, kind) {
  const edits = h('span', { class: 'item-edits' });
  const parts = {
    title: h('h1', { id: 'item-title', tabindex: -1 }),
    pager: isNodeKind(kind) ? h('nav', { class: 'item-pager', id: 'item-pager' }) : null,
    edits,
    actions: h('div', { class: 'page-actions item-actions' }, edits, h('button', { type: 'button', id: 'item-agent-view', onclick: () => openAgentView(ref) }, icon('agent', { size: 16 }), 'Agent view')),
    summary: h('div', { class: 'card-summary' }),
    details: h('section', { class: 'item-details', id: 'item-details', 'aria-labelledby': 'item-details-title' }),
  };
  if (UNDER[kind]) parts.table = tableSection(CHILDREN_IDS);
  if (isNodeKind(kind)) parts.links = linksSection();
  parts.children = parts.table ? parts.table.section : null;
  put(
    root,
    h(
      'div',
      { class: 'item-page', id: 'item-page', dataset: { ref, kind } },
      h('header', { class: 'page-header item-head' }, h('div', { class: 'page-title' }, parts.title), h('div', { class: 'item-tools' }, parts.pager, parts.actions), parts.summary),
      h('h2', { class: 'sr-only', id: 'item-details-title' }, `Details of ${codeFor(kind, parseItemRef(ref).number)}`),
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

// -- previous and next --------------------------------------------------------------------------------

// Its siblings in the order the server lists them: the epics of the project, the stories of an epic,
// the tasks of a story, each by number.
const siblingsOf = (node) => (node.kind === 'epic' ? epics() : childrenOf(node.parent).sort((a, b) => a.number - b.number));

// A pager button is the neighbour's code and, where there is room, its name cut with an ellipsis; its whole name is in the
// tooltip and the spoken name.
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
    other ? h('span', { class: 'pager-name' }, other.title) : null,
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
  const scope = node.kind === 'epic' ? 'epics in this project' : `${KIND_WORDS[node.kind] === 'story' ? 'stories' : 'tasks'} in ${codeOf(node.parent)} ${titleOf(node.parent)}`;
  const restore = rememberFocus(parts.pager);
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

function row(node, columns) {
  const code = nodeCode(node.kind, node.number);
  return h(
    'tr',
    { id: `row-${refSlug(node.ref)}`, dataset: { ref: node.ref, kind: node.kind, number: node.number, status: node.status }, onclick: opens(node.ref) },
    columns.map((column) => column.cell(node, code)),
  );
}

function table(node, rows) {
  // A column that no row has anything in (every priority Normal, nobody holds anything, no due day, nothing waiting) is left
  // out: an empty column is noise. Size stays, because it is also how an item is given one.
  const blank = (column) => (column.key === 'priority' ? rows.every((one) => one.priority === 'normal') : ['held', 'due', 'waiting'].includes(column.key) && rows.every((one) => column.none(one)));
  const columns = COLUMNS.filter((column) => (!column.only || column.only === node.kind) && !blank(column));
  return h(
    'table',
    { class: 'item-table', id: CHILDREN_IDS.table, 'aria-describedby': CHILDREN_IDS.count },
    h('caption', { class: 'sr-only' }, `${UNDER[node.kind].heading} in ${nodeCode(node.kind, node.number)} ${node.title}`),
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
  const label = `${under.heading} in ${nodeCode(node.kind, node.number)} ${node.title}`;
  put(view.heading, under.heading);
  put(view.count, rows.length ? String(rows.length) : null);
  put(view.breakdown, rows.length > 1 ? breakdown(rows) : null);
  view.region.setAttribute('aria-label', label);
  // Drawn again only when what it shows, or the way it is sorted, has changed.
  const signature = JSON.stringify([
    node.ref,
    sorting,
    can('write'),
    rows.map((one) => [one.ref, one.version, one.title, one.status, one.priority, one.size, one.points_total, one.unsized, one.claimed_by, one.claimed_by_kind, one.target_on, one.overdue, one.waiting_on.map((ref) => [ref, titleOf(ref)]), one.progress_done, one.progress_total]),
  ]);
  if (signature === current.signature) return;
  current.signature = signature;
  const restore = rememberFocus(parts.children);
  // With nothing under it the section is its heading and the way to add one; a person who cannot add one is not shown it.
  parts.children.hidden = rows.length === 0 && !can('write');
  put(view.add, addButton(node));
  view.region.hidden = rows.length === 0;
  put(view.region, rows.length ? table(node, rows) : null);
  restore();
}

function drawChildren() {
  const node = nodeOf(current.ref);
  if (!node) return;
  if (UNDER[node.kind]) drawTable(node);
  current.parts.links?.draw(node);
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
  say(`${KIND[kind].label} ${codeFor(kind, parseItemRef(ref).number)} is no longer there.`, { refusal: false });
}

// What the page asks the card, or the record's page, to draw it with.
const placesOf = (parts, state) => ({
  title: parts.title,
  summary: parts.summary,
  actions: parts.edits,
  table: parts.children,
  links: parts.links?.element,
  drawn: () => {
    if (!current || current.state !== state) return;
    // The card has just put what it read into the store: the header and the tables follow it.
    if (isNodeKind(current.kind)) drawAll();
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
  current = { ref, kind: parsed.kind, from: ui.from, state, parts, signature: null, headSignature: null, pagerSignature: null };
  const node = isNodeKind(parsed.kind);
  if (node) drawAll();
  else put(parts.title, pageTitle(parsed.kind, codeFor(parsed.kind, parsed.number), ''));
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
