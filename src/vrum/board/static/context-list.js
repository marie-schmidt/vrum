// The Lists page's context lists: the acceptance criteria and the notes of the scope the page shows, as two
// sections, so that context can be read and managed without finding dots. The notes are one table. Each
// section has filters that work over everything the board holds (status, words), a count, and the
// button to add something. Each row says what the record is, where it stands (a note has one status:
// Unconfirmed, Confirmed, Replaced or Withdrawn), links to its own page, and has the operations
// (confirm, link, replace, withdraw) that page has, in the menu of its status chip. The lists keep no
// data of their own: they draw the same dots and lines the work list draws.

import { can } from './ctx.js';
import { recordsTable } from './entries.js';
import { h, plural, put } from './dom.js';
import { icon } from './icons.js';
import { inForce } from './labels.js';
import { addNoteDialog } from './records.js';
import { createRequirements } from './requirements.js';
import { matchesText, sectionFrame, selectOf, textInput, walkRows } from './list-parts.js';

// How many rows a section draws before it offers more.
const FIRST = 100;

// The notes a status filter keeps: in force is unconfirmed and confirmed together.
const STATUS_FILTER = [
  ['all', 'All'],
  ['in_force', 'In force'],
  ['unconfirmed', 'Unconfirmed'],
  ['confirmed', 'Confirmed'],
  ['superseded', 'Replaced'],
  ['withdrawn', 'Withdrawn'],
];

// The section of notes: one table of all of them, newest first.
function notesSection() {
  const state = { status: 'in_force', text: '', limit: FIRST };
  let last = null;
  const status = selectOf(STATUS_FILTER, state.status);
  const text = textInput('Words in a note');
  const frame = sectionFrame({
    name: 'notes',
    title: 'Notes',
    buttons: can('write') ? [h('button', { type: 'button', id: 'add-note', class: 'small', onclick: () => addNoteDialog({}) }, icon('plus', { size: 14 }), 'Add a note…')] : [],
    controls: [
      ['status', 'Status', status],
      ['text', 'Find', text],
    ],
    onFilter: () => {
      state.status = status.value;
      state.text = text.value;
      state.limit = FIRST;
      if (last) draw(last);
    },
  });
  const list = h('div', { class: 'ctx-notes-list', id: 'ctx-notes-list' });
  const leftOut = h('p', { class: 'ctx-left-out quiet', id: 'notes-left-out' });
  frame.body.append(leftOut, list);
  walkRows(frame.element);

  // What the words kept in the filter box keep, whatever the status.
  const fitsWords = (record) => matchesText(state.text, record.id, record.text, record.why, record.decided_by, record.recorded_by);

  // What the filters keep.
  function keeps(record) {
    const wanted = state.status;
    const fits = wanted === 'all' || (wanted === 'in_force' ? inForce(record) : record.status === wanted);
    return fits && fitsWords(record);
  }

  function draw(ctx) {
    last = ctx;
    const focused = frame.body.contains(document.activeElement) ? document.activeElement.closest('[data-key]')?.dataset.key : null;
    const inScope = [...ctx.shown.dots.values()].filter((dot) => dot.type === 'note');
    const kept = inScope.filter((dot) => keeps(dot.record)).sort((a, b) => b.record.id - a.record.id); // the newest on top
    frame.setCount(kept.length, inScope.length);
    // What the default of showing the notes in force leaves out is said, with the way to see it.
    const ended = state.status === 'in_force' ? inScope.filter((dot) => !inForce(dot.record) && fitsWords(dot.record)).length : 0;
    put(
      leftOut,
      ended ? `${plural(ended, 'replaced or withdrawn note')} ${ended === 1 ? 'is' : 'are'} not shown. ` : null,
      ended
        ? h(
            'button',
            {
              type: 'button',
              class: 'link show-left-out',
              onclick: () => {
                state.status = 'all';
                status.value = 'all';
                state.limit = FIRST;
                if (last) draw(last);
              },
            },
            ended === 1 ? 'Show it.' : 'Show them.',
          )
        : null,
    );
    leftOut.hidden = ended === 0;
    // The chosen record is always among the rows drawn.
    const at = kept.findIndex((dot) => dot.key === ctx.cfg.sel?.key);
    if (at >= state.limit) state.limit = at + 1;
    if (!kept.length) {
      put(list, h('p', { class: 'empty ctx-none' }, inScope.length ? 'None match the filters.' : 'None yet.'));
      return;
    }
    const table = recordsTable(kept.slice(0, state.limit).map((dot) => dot.record), { caption: 'Notes' });
    for (const row of table.element.querySelectorAll('tbody tr')) {
      row.dataset.key = row.dataset.ref;
      row.classList.toggle('selected', row.dataset.ref === ctx.cfg.sel?.key);
      if (row.dataset.ref === ctx.cfg.sel?.key) row.setAttribute('aria-current', 'true');
    }
    const left = kept.length - state.limit;
    put(
      list,
      table.element,
      left > 0
        ? h('p', { class: 'ctx-more' }, h('button', { type: 'button', class: 'show-more small', onclick: () => { state.limit += FIRST; draw(last); } }, `Show ${Math.min(FIRST, left)} more`))
        : null,
    );
    if (focused) list.querySelector(`[data-key="${focused}"] .record-title`)?.focus();
  }

  function mark(key) {
    for (const row of list.querySelectorAll('tr.selected')) {
      row.classList.remove('selected');
      row.removeAttribute('aria-current');
    }
    const row = key ? list.querySelector(`tr[data-key="${key}"]`) : null;
    row?.classList.add('selected');
    row?.setAttribute('aria-current', 'true');
    return row;
  }

  return { element: frame.element, draw, select: mark };
}

// The two sections together. `parts` is what each section is called by the kind of dot it lists.
export function createContextList() {
  const parts = { rule: createRequirements(), note: notesSection() };
  const element = h('div', { class: 'gv-context', id: 'context-list' }, parts.rule.element, parts.note.element);
  return {
    element,
    draw(ctx) {
      for (const [type, part] of Object.entries(parts)) {
        part.element.hidden = ctx.cfg.hideKind.has(type);
        if (!part.element.hidden) part.draw(ctx);
      }
    },
    // The row of the chosen record, once it is marked, for the page to bring into view.
    select(key) {
      const found = Object.values(parts).map((part) => part.select(key)).find(Boolean);
      found?.querySelector('.gv-connections')?.setAttribute('open', '');
      return found || null;
    },
    busy: () => parts.rule.busy(),
    destroy: () => parts.rule.destroy(),
  };
}
