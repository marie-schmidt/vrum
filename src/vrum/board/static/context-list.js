// The Graph's context list: the requirements, decisions and facts of the scope the Graph shows, as
// three sections of text, so that context can be read and managed without finding dots. Each
// section has filters that work over everything the board holds (status, confirmation, words), a
// count, and the buttons to record something. Each row says what the record is, whether a person
// confirmed it and whether it is still in force, and has the operations of the detail panel. The
// lists keep no data of their own: they draw the same dots and lines the map draws.

import { h } from './dom.js';
import { mentionedInDetails } from './references.js';
import { confirmationControl, linkDialog, recordDecisionDialog, recordFactDialog, recordRow, supersedeDialog, withdrawDialog } from './records.js';
import { createRequirements } from './requirements.js';
import { connectionsDetails, matchesText, sectionFrame, selectOf, textInput, walkRows } from './list-parts.js';

// How many rows a section draws before it offers more.
const FIRST = 100;

const LIFECYCLE_FILTER = [
  ['active', 'In force'],
  ['superseded', 'Replaced'],
  ['withdrawn', 'Withdrawn'],
  ['', 'All'],
];
const CONFIRMATION_FILTER = [
  ['', 'Confirmed or not'],
  ['confirmed', 'Confirmed'],
  ['unconfirmed', 'Unconfirmed'],
];

const KINDS = {
  decision: {
    name: 'decisions',
    title: 'Decisions',
    record: 'Record a decision',
    open: () => recordDecisionDialog({}),
    find: 'Words in a decision',
    none: 'There are no decisions here yet. Record one with Record a decision.',
    words: (decision) => [decision.id, decision.title, decision.text, decision.reason, decision.decided_by, decision.recorded_by],
  },
  fact: {
    name: 'facts',
    title: 'Facts',
    record: 'Record a fact',
    open: () => recordFactDialog({}),
    find: 'Words in a fact',
    none: 'There are no facts here yet. Record one with Record a fact.',
    words: (fact) => [fact.id, fact.text, fact.source, fact.recorded_by],
  },
};

// One section of decisions or of facts.
function recordSection(kind) {
  const spec = KINDS[kind];
  const state = { status: 'active', confirmation: '', text: '', limit: FIRST };
  let last = null;
  const status = selectOf(LIFECYCLE_FILTER, state.status);
  const confirmation = selectOf(CONFIRMATION_FILTER, state.confirmation);
  const text = textInput(spec.find);
  const frame = sectionFrame({
    name: spec.name,
    title: spec.title,
    buttons: [h('button', { type: 'button', id: `record-${kind}`, class: kind === 'decision' ? 'primary' : '', onclick: spec.open }, spec.record)],
    controls: [
      ['status', 'Status', status],
      ['confirmation', 'Confirmation', confirmation],
      ['text', 'Find', text],
    ],
    onFilter: () => {
      state.status = status.value;
      state.confirmation = confirmation.value;
      state.text = text.value;
      state.limit = FIRST;
      if (last) draw(last);
    },
  });
  const list = h('ul', { class: 'plain gv-rows', id: `ctx-${spec.name}-list` });
  frame.body.append(list);
  walkRows(frame.element);

  function row(dot, ctx) {
    const record = dot.record;
    const chosen = ctx.cfg.sel?.key === dot.key;
    const done = () => last && draw(last);
    const actions =
      record.status === 'active'
        ? h(
            'div',
            { class: 'gv-actions' },
            confirmationControl(record, done),
            h('button', { type: 'button', class: 'link-record', onclick: () => linkDialog(record) }, 'Link…'),
            h('button', { type: 'button', class: 'supersede-record', onclick: () => supersedeDialog(record, { onDone: done }) }, 'Replace…'),
            h('button', { type: 'button', class: 'withdraw-record', onclick: () => withdrawDialog(record, { onDone: done }) }, 'Withdraw…'),
          )
        : null;
    const item = recordRow(record, { actions });
    Object.assign(item.dataset, { key: dot.key, id: String(record.id) });
    item.classList.add('gv-record');
    item.classList.toggle('selected', chosen);
    if (chosen) item.setAttribute('aria-current', 'true');
    for (const extra of [mentionedInDetails(kind, record.id), connectionsDetails(dot, ctx)]) if (extra) item.append(extra);
    return item;
  }

  function keeps(record) {
    if (state.status && record.status !== state.status) return false;
    if (state.confirmation && (record.confirmation === 'confirmed' ? 'confirmed' : 'unconfirmed') !== state.confirmation) return false;
    return matchesText(state.text, ...spec.words(record));
  }

  function draw(ctx) {
    last = ctx;
    const focused = frame.body.contains(document.activeElement) ? document.activeElement.closest('[data-key]')?.dataset.key : null;
    const inScope = [...ctx.shown.dots.values()].filter((dot) => dot.type === kind);
    const kept = inScope.filter((dot) => keeps(dot.record)).sort((a, b) => b.id - a.id); // the newest on top
    frame.setCount(kept.length, inScope.length);
    // The chosen record is always among the rows drawn.
    const at = kept.findIndex((dot) => dot.key === ctx.cfg.sel?.key);
    if (at >= state.limit) state.limit = at + 1;
    const rows = kept.slice(0, state.limit).map((dot) => row(dot, ctx));
    const more = kept.length > state.limit ? [h('li', { class: 'gv-more' }, h('button', { type: 'button', class: 'show-more', onclick: () => { state.limit += FIRST; draw(last); } }, `Show ${Math.min(FIRST, kept.length - state.limit)} more of ${kept.length - state.limit} left`))] : [];
    list.replaceChildren(...(kept.length ? [...rows, ...more] : [h('li', { class: 'quiet gv-none' }, inScope.length ? 'None match the filters.' : spec.none)]));
    if (focused) list.querySelector(`[data-key="${focused}"] .record-title`)?.focus();
  }

  function mark(key) {
    for (const item of list.querySelectorAll('.gv-record.selected')) {
      item.classList.remove('selected');
      item.removeAttribute('aria-current');
    }
    const item = key ? list.querySelector(`.gv-record[data-key="${key}"]`) : null;
    item?.classList.add('selected');
    item?.setAttribute('aria-current', 'true');
    return item;
  }

  return { element: frame.element, draw, select: mark };
}

// The three sections together. `parts` is what each section is called by the kind of dot it lists.
export function createContextList() {
  const parts = { rule: createRequirements(), decision: recordSection('decision'), fact: recordSection('fact') };
  const element = h('div', { class: 'gv-context', id: 'context-list' }, parts.rule.element, parts.decision.element, parts.fact.element);
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
