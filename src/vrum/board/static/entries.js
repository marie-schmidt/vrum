// How a note and an acceptance criterion are shown. The record pages, the recall tables and the lists all use
// these pieces, so a record reads the same wherever it is. A note has one status (Unconfirmed, Confirmed,
// Replaced or Withdrawn), always said in words and with a sign from the record's own `status`: an
// unconfirmed note is never shown as confirmed, and a replaced or withdrawn one is visibly ended. A note
// that names who decided it says so quietly, in a detail of its own. Everything is put in as text.
//
// What concerns an item is shown as tables that use the whole width of the page: one row for each
// record, whose words wrap to three lines (and, when they do not all fit, a "Show whole text" button puts
// all of them in the row), and a row opens the page of the record it is about.

import { itemLink, openItem } from './ctx.js';
import { day, h, moment } from './dom.js';
import { clip, plainText } from './forms.js';
import { icon } from './icons.js';
import { actorLabel, codeOf, decidedWords, inForce, itemCode, kindIcon, noteStatusWords, parseNodeRef, plainRequirementStatus } from './labels.js';
import { inline } from './markdown.js';
import { kindWords, nodeLink, openLink } from './references.js';
import { noteStatusChip as noteStatusMenu, requirementStatusChip, statusChip } from './status-menu.js';
import { headlineOf, restOf, store, titleOf } from './store.js';

// What to call a note in a list: the first line of its text.
export const recordTitle = (record) => clip(plainText(headlineOf(record)), 140);

const tableOf = (kind) => (kind === 'note' ? store.notes : store.rules) || [];
const byId = (kind, id) => tableOf(kind).find((entry) => entry.id === id) || null;

export const kindChip = (record) => h('span', { class: 'chip kind kind-note' }, kindIcon('note', 14), `Note #${record.id}`);

// The chip that says where a note stands. It is a button that opens a menu (status-menu.js) for a person who
// can change the project, and the plain chip for one who cannot or for a note that has ended. `onChanged`
// runs after a change was made.
export const noteStatusChip = (record, options = {}) => noteStatusMenu(record, options);

export const projectWideChip = (record) =>
  record.project_wide ? h('span', { class: 'chip project-wide', title: 'It applies throughout the project' }, icon('graph', { size: 14 }), 'Project-wide') : null;

// The chips every note carries, in one place so that none is left out. A note's own page says
// what it is in its title, so it asks for the others only.
export const recordChips = (record, { kind = true, onChanged = null } = {}) =>
  h('div', { class: 'record-chips' }, kind ? kindChip(record) : null, projectWideChip(record), noteStatusChip(record, { onChanged }));

// What a note says about who stood behind it, in words. A note that ended keeps the confirmation it had.
// `last` is the newest event that confirmed or un-confirmed it, when the page has the history: a
// confirmation that was taken back is said so, with who took it back.
export function confirmationWords(record, last = null) {
  const by = record.confirmed_by ? `${record.confirmed_by}${record.confirmed_at ? ` on ${moment(record.confirmed_at)}` : ''}` : null;
  const takenBack = !record.confirmed_by && last && last.action === 'note_unconfirmed' ? `${last.after?.said_by || last.actor} on ${moment(last.at)}` : null;
  if (record.status === 'confirmed') return `Confirmed by ${by}.`;
  if (inForce(record)) return takenBack ? `Confirmation was taken back by ${takenBack}.` : 'Not confirmed: no person has reviewed it yet.';
  if (by) return `It was confirmed by ${by}, before it ${record.status === 'superseded' ? 'was replaced' : 'was withdrawn'}.`;
  return takenBack ? `Its confirmation was taken back by ${takenBack}.` : 'It was never confirmed.';
}

// How an acceptance criterion stands, as a chip: open, met or dropped, each with its own sign.
export const ruleStatusChip = (rule, options = {}) => requirementStatusChip(rule, options);

export const requirementKindChip = (rule) => h('span', { class: 'chip kind kind-rule' }, kindIcon('rule', 14), `Criterion #${rule.id}`);

const joined = (nodes) => nodes.flatMap((node, at) => (at ? [', ', node] : [node]));

// How it ended, and what replaced what: the chain ends in what is in force now. `replaces` is
// false where a table already lists what the note replaced.
export function lifecycleLines(record, { replaces = true } = {}) {
  const lines = [];
  if (replaces && (record.supersedes_ids || []).length) {
    lines.push(h('p', { class: 'quiet replaces' }, `Replaces note${record.supersedes_ids.length === 1 ? '' : 's'} `, joined(record.supersedes_ids.map((id) => openLink('note', id, `#${id}`))), '.'));
  }
  if (record.status === 'superseded') {
    const steps = [];
    const seen = new Set([record.id]);
    let current = record;
    while (current && current.superseded_by_id && !seen.has(current.superseded_by_id)) {
      seen.add(current.superseded_by_id);
      steps.push(openLink('note', current.superseded_by_id, `#${current.superseded_by_id}`));
      current = byId('note', current.superseded_by_id);
    }
    const last = current && current !== record ? current : null;
    const end = last ? ` (${noteStatusWords(last.status).toLowerCase()})` : '';
    lines.push(h('p', { class: 'chain' }, h('strong', {}, 'Replaced'), ' by ', joined(steps), end, '.'));
  }
  if (!inForce(record) && record.status_reason) {
    lines.push(h('p', { class: 'ended-reason' }, h('strong', {}, record.status === 'withdrawn' ? 'Withdrawn: ' : 'Why it was replaced: '), ...inline(record.status_reason)));
  } else if (record.status === 'superseded' && byId('note', record.superseded_by_id)?.why) {
    // A replaced note has no reason of its own: the note that replaced it says why.
    lines.push(h('p', { class: 'ended-reason' }, h('strong', {}, 'Why it was replaced: '), ...inline(byId('note', record.superseded_by_id).why)));
  } else if (record.status === 'withdrawn') {
    lines.push(h('p', { class: 'ended-reason' }, h('strong', {}, 'Withdrawn.')));
  }
  return lines;
}

// -- tables --------------------------------------------------------------------------------------------

// A row opens the page of what it is about when it is pressed anywhere but on a link or a control,
// and not when the person was selecting some of its words.
export const opens = (ref) => (event) => {
  if (event.target.closest('a, button, select, input, textarea, summary, label')) return;
  if (window.getSelection()?.toString()) return;
  openItem(ref);
};

// A table that uses the whole width of its box and scrolls inside it when the window is too narrow
// for its columns. `columns` is [{ key, label, cell(row) }], `row(item)` the properties of a row (its
// `data-` hooks), and `rows` the items. It gives back the box, and `add(more)` for rows that come
// later (a table that is read page by page).
export function dataTable({ caption, className = '', columns, rows, row }) {
  const body = h('tbody');
  const add = (more) => {
    for (const item of more) body.append(h('tr', row(item), columns.map((column) => column.cell(item))));
  };
  add(rows);
  const element = h(
    'div',
    { class: 'table-scroll record-region', role: 'region', tabindex: 0, 'aria-label': caption },
    h(
      'table',
      { class: `record-table ${className}`.trim() },
      h('caption', { class: 'sr-only' }, caption),
      h('thead', {}, h('tr', {}, columns.map((column) => h('th', { scope: 'col', class: `col-${column.key}` }, column.label)))),
      body,
    ),
  );
  return { element, add };
}

// The first cell of a row: the kind's icon and the record's number or code, a link to its page.
const idCell = (kind, number, ref) =>
  h('th', { scope: 'row', class: 'cell-id' }, itemLink(ref, { class: 'item-code-link', 'data-focus-key': `open:${ref}` }, kindIcon(kind, 14), h('span', { class: 'item-code' }, itemCode(kind, number))));

// A cell whose words wrap to at most three lines. When they do not all fit, a quiet "Show whole text"
// puts all of them in the cell (as they were written, lines and all), so that nothing is left to
// a tooltip: a keyboard or a finger reaches it as well. `full` is that whole text, and the cell's title.
// Whether the cell holds more than it shows is known only once it is laid out, and again whenever its
// width changes, so the button (and, once it is pressed, the whole text) is made then and not before.
const fulls = new WeakMap();
function wholeToggle(short) {
  const toggle = h('button', { type: 'button', class: 'clamp-toggle', 'aria-expanded': 'false' }, 'Show whole text');
  toggle.addEventListener('click', () => {
    const open = toggle.getAttribute('aria-expanded') !== 'true';
    short.hidden = open;
    short.parentElement.querySelector('.clamp-whole')?.remove();
    if (open) short.after(h('div', { class: 'clamp-whole' }, fulls.get(short)));
    toggle.setAttribute('aria-expanded', String(open));
    toggle.textContent = open ? 'Show less' : 'Show whole text';
  });
  return toggle;
}
const clipping = new ResizeObserver((entries) => {
  for (const { target } of entries) {
    const cell = target.parentElement;
    if (!cell) continue;
    const toggle = cell.querySelector('.clamp-toggle');
    const clipped = target.hidden || target.scrollHeight > target.clientHeight + 1;
    if (clipped && !toggle) {
      const fresh = wholeToggle(target);
      const from = cell.querySelector('.record-from');
      if (from) from.before(fresh);
      else cell.append(fresh);
    } else if (!clipped && toggle) toggle.remove();
  }
});
function clampCell(cls, full, ...content) {
  const short = h('div', { class: 'clamp clamp-wrap', title: full }, ...content);
  fulls.set(short, full);
  const cell = h('td', { class: `cell-clamp cell-wrap ${cls}` }, short);
  // Only the column of words has the button: it takes whatever width the others leave, so a button
  // appearing in it moves nothing sideways. In a narrow column of codes it would widen the column, which
  // would unclip the words, which would take the button away again, and so on.
  if (cls.split(' ').includes('cell-title')) clipping.observe(short);
  return cell;
}
// A note that is not linked to this item itself but comes down from the epic or story above it
// (recall says which): the cell says where from, under its title.
const withFrom = (cell, record) => {
  if (record.inherited_from) cell.append(h('span', { class: 'record-from', title: `From ${codeOf(record.inherited_from)} ${titleOf(record.inherited_from)}` }, 'From ', nodeLink(record.inherited_from)));
  return cell;
};
const plainCell = (cls, ...content) => h('td', { class: cls }, ...content);
const dateCell = (value) => plainCell('cell-on', value ? h('span', { class: 'item-date' }, day(value)) : null);
const quiet = (text) => h('span', { class: 'quiet' }, text);

// What a note concerns, one item to a name: { type, ref, short, full }.
function concernedItems(record) {
  return [
    ...(record.nodes || []).map((ref) => ({ type: parseNodeRef(ref)?.kind || 'story', ref, short: codeOf(ref), full: `${codeOf(ref)} ${titleOf(ref)}` })),
    ...(record.rule_ids || []).map((id) => ({ type: 'rule', ref: `rule:${id}`, short: `Criterion #${id}`, full: `Criterion #${id} ${byId('rule', id)?.title || ''}`.trim() })),
    ...(record.note_ids || []).map((id) => {
      const other = byId('note', id);
      return { type: 'note', ref: `note:${id}`, short: `Note #${id}`, full: `Note #${id} ${other ? headlineOf(other) : ''}`.trim(), record: other };
    }),
  ];
}

// "Applies to" in a list: the whole project, and the codes of what it is linked to.
function appliesCell(record) {
  const found = concernedItems(record);
  const names = [record.project_wide ? 'Whole project' : null, ...found.map((one) => one.short)].filter(Boolean);
  const full = [record.project_wide ? 'The whole project' : null, ...found.map((one) => one.full)].filter(Boolean);
  return clampCell('cell-applies', full.join('\n'), names.length ? names.join(', ') : quiet('Nothing yet'));
}

// What is under the first line of a note's text, or, when there is no more, its why: the grey words
// after the headline in a table.
function detailOf(record) {
  const rest = plainText(restOf(record));
  return rest || `Why: ${plainText(record.why || '')}`;
}

// Who decided a note and when, as a quiet detail of its row: the words are whole in the cell's title.
const decidedCell = (record) =>
  clampCell('cell-decided', decidedWords(record) || '', record.decided_by ? `${record.decided_by}${record.decided_on ? `, ${day(record.decided_on)}` : ''}` : null);

// The properties of the row of a note: its hooks, and what opens it. `why` is the reason recall
// gives for its being there.
const recordLine = (record) => ({
  class: `record-line ${inForce(record) ? '' : 'ended'}`.trim(),
  id: `record-note-${record.id}`,
  dataset: {
    kind: 'note',
    id: record.id,
    ref: `note:${record.id}`,
    record: `note:${record.id}`,
    status: record.status,
    projectWide: String(Boolean(record.project_wide)),
    why: record.why_here || null,
    from: record.inherited_from || null,
  },
  onclick: opens(`note:${record.id}`),
});

const noteColumns = [
  { key: 'id', label: 'Note', cell: (record) => idCell('note', record.id, `note:${record.id}`) },
  {
    key: 'text',
    label: 'What it says',
    cell: (record) =>
      withFrom(clampCell('cell-title', `${record.text}\n\nWhy: ${record.why || ''}`, itemLink(`note:${record.id}`, { class: 'record-title' }, recordTitle(record)), h('span', { class: 'clamp-more' }, detailOf(record))), record),
  },
  { key: 'status', label: 'Status', cell: (record) => plainCell('cell-status', noteStatusChip(record)) },
  { key: 'applies', label: 'Applies to', cell: appliesCell },
  { key: 'decided', label: 'Decided by', cell: decidedCell },
  { key: 'by', label: 'Recorded by', cell: (record) => plainCell('cell-by', actorLabel(record.recorded_by, record.recorded_by_kind)) },
  { key: 'on', label: 'Recorded', cell: (record) => dateCell(record.recorded_at) },
];

// Notes as a table. A note may carry `why_here`, the reason recall gives for it, which is kept on its
// row as `data-why`, and `inherited_from`.
export function recordsTable(records, { caption }) {
  return dataTable({ caption, className: 'notes-table', columns: noteColumns, rows: records, row: recordLine });
}

// -- acceptance criteria ---------------------------------------------------------------------------------

// Acceptance criteria as a table. `onItem` adds the story or task each one is on, for a page where they
// are not all on the same one. `plain` draws the status as a chip that changes nothing, for criteria that
// are only read where they are shown (the ones a task gets from its story).
export function requirementsTable(rules, { caption, onItem = true, plain = false }) {
  const columns = [
    { key: 'id', label: 'Criterion', cell: (rule) => idCell('rule', rule.id, `rule:${rule.id}`) },
    {
      key: 'title',
      label: 'Title',
      cell: (rule) => clampCell('cell-title', rule.body ? `${rule.title}\n\n${rule.body}` : rule.title, itemLink(`rule:${rule.id}`, { class: 'record-title' }, rule.title), rule.body ? h('span', { class: 'clamp-more' }, plainText(rule.body)) : null),
    },
    { key: 'status', label: 'Status', cell: (rule) => plainCell('cell-status', plain ? plainRequirementStatus(rule.status) : ruleStatusChip(rule)) },
    ...(onItem
      ? [
          {
            key: 'story',
            label: 'On',
            cell: (rule) => clampCell('cell-story', rule.node ? `${codeOf(rule.node)} ${titleOf(rule.node)}` : 'Not placed yet', rule.node ? nodeLink(rule.node) : quiet('Not placed yet')),
          },
        ]
      : []),
  ];
  return dataTable({
    caption,
    className: 'requirements-table',
    columns,
    rows: rules,
    row: (rule) => ({
      class: `record-line status-${rule.status}`,
      id: `record-rule-${rule.id}`,
      dataset: { kind: 'rule', id: rule.id, ref: `rule:${rule.id}`, record: `rule:${rule.id}`, rule: rule.id, status: rule.status, node: rule.node ?? '' },
      onclick: opens(`rule:${rule.id}`),
    }),
  });
}

// -- what a note concerns, and what replaced what -----------------------------------------------------------

// The status of something a note concerns, in its own words and sign.
function statusOf(type, ref) {
  if (type === 'rule') {
    const rule = byId('rule', Number(ref.split(':')[1]));
    return rule ? ruleStatusChip(rule) : null;
  }
  if (type === 'note') {
    const note = byId('note', Number(ref.split(':')[1]));
    return note ? noteStatusChip(note) : null;
  }
  const node = store.nodes.get(ref);
  return node ? statusChip(node) : null;
}

// Everything a note concerns as a table: the whole project (for a project-wide note), then the
// epics, stories and tasks, the acceptance criteria and the notes it is about. Each row links to the page
// of what it names.
export function concernsTable(record, { caption }) {
  const rows = [
    ...(record.project_wide ? [{ type: 'project', ref: null, short: 'The whole project', full: 'The whole project' }] : []),
    ...concernedItems(record),
  ];
  const kindName = (row) => (row.type === 'project' ? 'Project' : kindWords(row.type).label);
  return dataTable({
    caption,
    className: 'concerns-table',
    columns: [
      { key: 'type', label: 'Kind', cell: (row) => plainCell('cell-kind', h('span', { class: 'kind-cell' }, row.type === 'project' ? icon('graph', { size: 14 }) : kindIcon(row.type, 14), kindName(row))) },
      {
        key: 'item',
        label: 'Item',
        cell: (row) =>
          clampCell('cell-title', row.full, row.ref ? itemLink(row.ref, { class: 'record-title', 'data-focus-key': `open:${row.ref}` }, row.full) : h('span', { class: 'record-title' }, 'Every epic, story and task in the project')),
      },
      { key: 'status', label: 'Status', cell: (row) => plainCell('cell-status', row.ref ? statusOf(row.type, row.ref) : null) },
    ],
    rows,
    row: (row) => ({
      class: 'record-line',
      dataset: { kind: row.type, ref: row.ref || 'project' },
      onclick: row.ref ? opens(row.ref) : null,
    }),
  });
}

// What a note replaced and what replaced it, as rows: the ones it replaced, then the one that
// replaced it and, after it, the ones that replaced that in turn, ending in what is in force now.
export function replacementRows(record) {
  const rows = (record.supersedes_ids || []).map((id) => ({ how: 'Replaces', id, record: byId('note', id) }));
  const seen = new Set([record.id]);
  let current = record;
  let first = true;
  while (current && current.superseded_by_id && !seen.has(current.superseded_by_id)) {
    const id = current.superseded_by_id;
    seen.add(id);
    current = byId('note', id);
    rows.push({ how: first ? 'Replaced by' : 'Then replaced by', id, record: current });
    first = false;
  }
  return rows;
}

export function replacementTable(record, { caption }) {
  const columns = [
    { key: 'relation', label: 'Relation', cell: (row) => plainCell('cell-relation', row.how) },
    { key: 'id', label: 'Note', cell: (row) => idCell('note', row.id, `note:${row.id}`) },
    {
      key: 'text',
      label: 'What it says',
      cell: (row) => (row.record ? clampCell('cell-title', recordTitle(row.record), itemLink(`note:${row.id}`, { class: 'record-title' }, recordTitle(row.record))) : plainCell('cell-title', quiet('Not shown'))),
    },
    { key: 'status', label: 'Status', cell: (row) => plainCell('cell-status', row.record ? noteStatusChip(row.record) : null) },
    { key: 'on', label: 'Recorded', cell: (row) => dateCell(row.record ? row.record.recorded_at : null) },
  ];
  return dataTable({
    caption,
    className: 'replacement-table',
    columns,
    rows: replacementRows(record),
    row: (row) => ({ class: 'record-line', dataset: { kind: 'note', id: row.id, ref: `note:${row.id}`, relation: row.how }, onclick: opens(`note:${row.id}`) }),
  });
}
