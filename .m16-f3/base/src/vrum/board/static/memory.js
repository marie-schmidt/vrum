// The Memory page: everything the project remembers, in four lists (principles pinned first, then
// decisions, facts and beliefs), newest first, with where each came from and who recorded or
// decided it. A person can record, replace, retract, confirm and promote from here. Each of those
// is the operation an agent calls (see memory-forms.js); the page keeps no data of its own and only
// shows what the server holds. A recall preview shows what an agent would be told for one item.

import { notify, ui } from './ctx.js';
import { clear, h, plural } from './dom.js';
import { decisionEntry, entryLink, memoryEntry } from './entries.js';
import { clip, parseRef } from './forms.js';
import { changeConcerns, confirmBelief, promoteBelief, recordDecision, recordMemory, retract } from './memory-forms.js';
import { recallPreview } from './recall.js';
import { ensure, store } from './store.js';

const FIRST = 100;
const KIND_CHOICES = [['', 'Everything'], ['decision', 'Decisions'], ['principle', 'Principles'], ['fact', 'Facts'], ['belief', 'Beliefs']];
const STATUS_CHOICES = [['', 'Everything'], ['active', 'In force'], ['superseded', 'Superseded'], ['retracted', 'Retracted'], ['stale', 'Stale beliefs'], ['unconfirmed', 'Beliefs waiting to be confirmed']];
const ORIGIN_CHOICES = [['', 'Any origin'], ['stated', 'Stated by a person or an agent'], ['extracted', 'Proposed by a language model'], ['derived', 'Drawn up by a language model']];
const FILTERS = ['kind', 'status', 'origin', 'by', 'from', 'to', 'concerns'];

let root = null;
let params = new URLSearchParams();
let parts = {};
let recall = null;
let chosen = new Map(); // id -> type, for the facts and beliefs ticked to be changed together
let shown = new Map(); // section -> how many entries are drawn
let focused = null; // the entry the address pointed at, once scrolled to

const newest = (a, b) => String(b.recorded_at).localeCompare(String(a.recorded_at)) || b.id - a.id;
const dayOf = (value) => String(value || '').slice(0, 10);

// -- the page -------------------------------------------------------------------------------------

export async function mount(element, initial) {
  root = element;
  params = initial;
  chosen = new Map();
  shown = new Map();
  focused = null;
  recall = recallPreview();
  const control = (name, tag, props = {}, ...children) => h(tag, { id: `f-${name}`, name, ...props }, ...children);
  const choices = (name, list) => control(name, 'select', {}, list.map(([value, label]) => h('option', { value }, label)));
  parts = {
    kind: choices('kind', KIND_CHOICES),
    status: choices('status', STATUS_CHOICES),
    origin: choices('origin', ORIGIN_CHOICES),
    by: control('by', 'input', { type: 'text', list: 'f-who', autocomplete: 'off', placeholder: 'a name' }),
    from: control('from', 'input', { type: 'date' }),
    to: control('to', 'input', { type: 'date' }),
    concerns: control('concerns', 'input', { type: 'text', list: 'f-things', autocomplete: 'off', placeholder: '#12 or rule 3' }),
    who: h('datalist', { id: 'f-who' }),
    things: h('datalist', { id: 'f-things' }),
    problem: h('p', { class: 'problem', role: 'alert', id: 'filter-problem' }),
    summary: h('p', { class: 'count', id: 'count', role: 'status' }),
    bulk: h('form', { class: 'bulk', id: 'bulk', hidden: true }),
    result: h('div', { id: 'change-result' }),
    sections: h('div', { id: 'memory-sections' }),
  };
  const label = (name, text) => h('label', { for: `f-${name}` }, text);
  const filter = h(
    'form',
    {
      class: 'filter memory-filter',
      id: 'memory-filter',
      role: 'search',
      onsubmit: (event) => {
        event.preventDefault();
        ui.navigate(Object.fromEntries(FILTERS.map((name) => [name, parts[name].value || null])));
      },
    },
    label('kind', 'Show'), parts.kind,
    label('status', 'Status'), parts.status,
    label('origin', 'Origin'), parts.origin,
    label('by', 'Recorded or decided by'), parts.by, parts.who,
    label('from', 'From'), parts.from,
    label('to', 'to'), parts.to,
    label('concerns', 'Concerns'), parts.concerns, parts.things,
    h('button', { type: 'submit', class: 'primary', id: 'apply-filter' }, 'Apply'),
    h('button', { type: 'button', id: 'clear-filter', onclick: () => ui.navigate(Object.fromEntries(FILTERS.map((name) => [name, null]))) }, 'Clear'),
  );
  clear(root).append(
    h(
      'div',
      { class: 'toolbar' },
      h('h1', {}, 'Memory'),
      h('button', { type: 'button', id: 'record-decision', class: 'primary', onclick: () => recordDecision() }, 'Record a decision'),
      h('button', { type: 'button', id: 'record-fact', onclick: () => recordMemory('fact', { onChange: showChange }) }, 'Record a fact'),
      h('button', { type: 'button', id: 'record-belief', onclick: () => recordMemory('belief', { onChange: showChange }) }, 'Record a belief'),
      h('p', { class: 'help' }, 'Decisions are what people decided. Facts are what is known, each with its source. Beliefs are conclusions drawn from evidence: only ever suggestions, never decisions. Nothing here is edited: a change records a new item that replaces the old one, which stays as a record.'),
    ),
    recall.element,
    filter,
    parts.problem,
    parts.summary,
    parts.bulk,
    parts.result,
    parts.sections,
  );
  await Promise.all([ensure('rules'), ensure('decisions'), ensure('memories')]);
  if (!root) return;
  fillForm();
  draw();
  openRecall();
}

export function unmount() {
  root = null;
  recall = null;
}

export function update(next) {
  const before = params;
  params = next;
  if (!root) return;
  if (FILTERS.some((name) => (next.get(name) || '') !== (before.get(name) || ''))) {
    shown = new Map();
    fillForm();
  }
  if (next.get('entry') !== before.get('entry')) focused = null;
  draw();
  if ((next.get('recall') || '') !== (before.get('recall') || '')) openRecall();
}

export function refresh() {
  if (!root) return;
  draw();
  recall.refresh();
}

function fillForm() {
  for (const name of FILTERS) parts[name].value = params.get(name) || '';
}

function openRecall() {
  const spec = params.get('recall');
  if (spec) recall.element.open = true;
  recall.show(spec);
}

// -- what the filters keep ----------------------------------------------------------------------------

function filters() {
  const text = (params.get('concerns') || '').trim();
  const ref = text ? parseRef(text, ['node', 'rule']) : null;
  return {
    kind: params.get('kind') || '',
    status: params.get('status') || '',
    origin: params.get('origin') || '',
    by: (params.get('by') || '').trim().toLowerCase(),
    from: params.get('from') || '',
    to: params.get('to') || '',
    concerns: ref,
    badConcerns: text !== '' && ref === null,
  };
}

// `type` is decision (a decision or a principle), fact or belief.
function keeps(entry, type, f) {
  if (f.kind === 'decision' && !(type === 'decision' && entry.kind === 'decision')) return false;
  if (f.kind === 'principle' && !(type === 'decision' && entry.kind === 'principle')) return false;
  if ((f.kind === 'fact' || f.kind === 'belief') && type !== f.kind) return false;
  if (f.status === 'stale') {
    if (!(type === 'belief' && entry.stale)) return false;
  } else if (f.status === 'unconfirmed') {
    // A belief in force that no person has confirmed and whose evidence has not changed.
    if (!(type === 'belief' && entry.status === 'active' && !entry.stale && !entry.confirmed_by)) return false;
  } else if (f.status && entry.status !== f.status) return false;
  if (f.origin && (type === 'decision' ? 'stated' : entry.origin) !== f.origin) return false;
  if (f.by && (type === 'decision' ? entry.decided_by : entry.recorded_by).toLowerCase() !== f.by) return false;
  const when = type === 'decision' ? dayOf(entry.decided_on) : dayOf(entry.recorded_at);
  if (f.from && when < f.from) return false;
  if (f.to && when > f.to) return false;
  if (f.concerns) {
    const list = f.concerns.type === 'rule' ? entry.rule_ids : entry.node_ids;
    if (!list.includes(f.concerns.id)) return false;
  }
  return true;
}

// -- drawing --------------------------------------------------------------------------------------

function draw() {
  if (!root) return;
  const f = filters();
  parts.problem.textContent = f.badConcerns ? 'Concerns takes an id such as #12, or rule 3. That filter is not applied.' : '';
  const decisions = (store.decisions || []).filter((entry) => keeps(entry, 'decision', f)).sort(newest);
  const memories = store.memories || [];
  const facts = memories.filter((entry) => entry.type === 'fact' && keeps(entry, 'fact', f)).sort(newest);
  const beliefs = memories.filter((entry) => entry.type === 'belief' && keeps(entry, 'belief', f)).sort(newest);
  const principles = decisions.filter((entry) => entry.kind === 'principle');
  const plain = decisions.filter((entry) => entry.kind === 'decision');
  const stale = beliefs.filter((entry) => entry.stale);
  const rest = beliefs.filter((entry) => !entry.stale);

  for (const [id, type] of [...chosen]) {
    const found = memories.find((entry) => entry.id === id);
    if (!found || found.status !== 'active' || found.type !== type) chosen.delete(id);
  }
  parts.who.replaceChildren(...[...new Set([...(store.decisions || []).map((entry) => entry.decided_by), ...memories.map((entry) => entry.recorded_by)])].sort().map((name) => h('option', { value: name })));
  parts.things.replaceChildren(
    ...[...store.nodes.values()].map((node) => h('option', { value: `#${node.id} ${node.title}` })),
    ...(store.rules || []).map((rule) => h('option', { value: `rule ${rule.id} ${rule.title}` })),
  );
  const total = principles.length + plain.length + facts.length + beliefs.length;
  parts.summary.textContent = total
    ? `${plural(total, 'entry', 'entries')}: ${plural(principles.length, 'principle')}, ${plural(plain.length, 'decision')}, ${plural(facts.length, 'fact')}, ${plural(beliefs.length, 'belief')}${stale.length ? ` (${stale.length} stale)` : ''}.`
    : (store.decisions || []).length + memories.length
      ? 'Nothing matches these filters.'
      : 'Nothing is remembered yet. Record a decision, a fact or a belief, or let an agent do it.';

  clear(parts.sections).append(
    block('principles', 'Principles', principles, decisionRow, 'Standing guidelines that apply everywhere. They stay at the top.'),
    block('decisions', 'Decisions', plain, decisionRow, 'What people decided about this work, newest first.'),
    block('facts', 'Facts', facts, factRow, 'What is known, each with its source and where it came from.'),
    beliefBlock(stale, rest),
  );
  drawBulk();
  const wanted = params.get('entry');
  if (wanted && focused !== wanted) {
    const target = root.querySelector(`[data-entry="${CSS.escape(wanted)}"]`);
    if (target) {
      focused = wanted;
      target.classList.add('highlight');
      target.scrollIntoView({ block: 'center' });
    }
  }
}

function block(id, title, items, render, note) {
  const count = shown.get(id) || FIRST;
  const more = items.length > count;
  return h(
    'section',
    { class: 'memory-section', id, dataset: { section: id, count: items.length } },
    h('h2', {}, `${title} (${items.length})`),
    h('p', { class: 'quiet' }, note),
    items.length ? h('ul', { class: 'plain entries' }, items.slice(0, count).map(render)) : h('p', { class: 'quiet none' }, 'None.'),
    more ? h('p', {}, h('button', { type: 'button', class: 'show-more', onclick: () => { shown.set(id, count + FIRST); draw(); } }, `Show ${Math.min(FIRST, items.length - count)} more`)) : null,
  );
}

function beliefBlock(stale, rest) {
  const count = shown.get('beliefs') || FIRST;
  const more = rest.length > count;
  return h(
    'section',
    { class: 'memory-section', id: 'beliefs', dataset: { section: 'beliefs', count: stale.length + rest.length } },
    h('h2', {}, `Beliefs (${stale.length + rest.length})`),
    h('p', { class: 'quiet' }, 'Conclusions drawn from evidence. Every belief is a suggestion: it is not a decision and changes nothing in the graph.'),
    stale.length
      ? h(
          'div',
          { class: 'stale-group', id: 'stale-beliefs' },
          h('h3', {}, `Stale: their evidence changed (${stale.length})`),
          h('p', { class: 'quiet' }, 'Not in force until someone reads what changed, then replaces or retracts each one.'),
          h('ul', { class: 'plain entries' }, stale.map(beliefRow)),
        )
      : null,
    stale.length ? h('h3', {}, `Other beliefs (${rest.length})`) : null,
    rest.length ? h('ul', { class: 'plain entries' }, rest.slice(0, count).map(beliefRow)) : stale.length ? null : h('p', { class: 'quiet none' }, 'None.'),
    more ? h('p', {}, h('button', { type: 'button', class: 'show-more', onclick: () => { shown.set('beliefs', count + FIRST); draw(); } }, `Show ${Math.min(FIRST, rest.length - count)} more`)) : null,
  );
}

const button = (label, onclick, cls = '') => h('button', { type: 'button', class: cls, onclick }, label);

function decisionRow(decision) {
  const active = decision.status === 'active';
  return decisionEntry(decision, {
    actions: active
      ? h('div', { class: 'buttons' }, button('Replace with a new decision…', () => recordDecision({ replacing: decision }), 'supersede'), button('What it affects…', () => changeConcerns('decision', decision.id), 'concerns-button'))
      : null,
  });
}

function chooseBox(memory) {
  if (memory.status !== 'active') return null;
  const box = h('input', {
    type: 'checkbox',
    class: 'choose',
    checked: chosen.has(memory.id),
    'aria-label': `Choose ${memory.type} #${memory.id} to change together with others`,
    onchange: () => {
      if (box.checked) chosen.set(memory.id, memory.type);
      else chosen.delete(memory.id);
      drawBulk();
    },
  });
  return h('label', { class: 'check choose-label' }, box, ' Choose, to change with others');
}

function factRow(fact) {
  const active = fact.status === 'active';
  return memoryEntry(fact, {
    choose: chooseBox(fact),
    actions: active
      ? h(
          'div',
          { class: 'buttons' },
          button('Replace with a newer fact…', () => recordMemory('fact', { replacing: [fact], onChange: showChange }), 'supersede'),
          button('Retract…', () => retract([fact], { onDone: showChange }), 'retract'),
          button('What it concerns…', () => changeConcerns('fact', fact.id), 'concerns-button'),
        )
      : null,
  });
}

function beliefRow(belief) {
  const active = belief.status === 'active';
  return memoryEntry(belief, {
    choose: chooseBox(belief),
    actions: active
      ? h(
          'div',
          { class: 'buttons' },
          belief.stale || belief.confirmed_by ? null : button('Confirm…', () => confirmBelief(belief), 'confirm'),
          belief.stale ? null : button('Make it a decision…', () => promoteBelief(belief), 'promote'),
          button('Replace with a new belief…', () => recordMemory('belief', { replacing: [belief], onChange: showChange }), 'supersede'),
          button('Retract…', () => retract([belief], { onDone: showChange }), 'retract'),
          button('What it concerns…', () => changeConcerns('belief', belief.id), 'concerns-button'),
        )
      : null,
  });
}

// -- changing several at once -----------------------------------------------------------------------

function chosenItems() {
  return [...chosen.keys()].map((id) => (store.memories || []).find((entry) => entry.id === id)).filter(Boolean);
}

function drawBulk() {
  const form = parts.bulk;
  form.hidden = chosen.size === 0;
  if (chosen.size === 0) {
    clear(form);
    return;
  }
  form.onsubmit = (event) => event.preventDefault();
  clear(form).append(
    h('strong', { id: 'bulk-count' }, `${plural(chosen.size, 'item')} chosen`),
    button(
      'Replace with one new item…',
      () => {
        const items = chosenItems();
        const types = new Set(items.map((item) => item.type));
        if (types.size > 1) {
          notify('Choose only facts, or only beliefs, to replace together: a new fact replaces facts and a new belief replaces beliefs.', { refusal: true });
          return;
        }
        recordMemory([...types][0], { replacing: items, onChange: showChange });
      },
      'bulk-replace',
    ),
    button('Retract…', () => retract(chosenItems(), { onDone: showChange }), 'bulk-retract'),
    button('Choose none', () => { chosen = new Map(); draw(); }, 'bulk-none'),
  );
}

// -- what a change led to ---------------------------------------------------------------------------

// After a retraction or a replacement: the beliefs that went stale because of it, and the
// decisions that were made from a belief that is no longer held. Nothing here was changed by the
// server: each is for a person to look at, with the way to retract it or replace it from here.
function showChange(change) {
  if (!root) return;
  const stale = (change.stale_belief_ids || []).map((id) => (store.memories || []).find((entry) => entry.id === id)).filter((entry) => entry && entry.status === 'active');
  const promoted = change.promoted_decision_ids || [];
  const along = change.dependent_beliefs_retracted_ids || [];
  if (!stale.length && !promoted.length && !along.length) {
    clear(parts.result);
    return;
  }
  const dismiss = () => clear(parts.result);
  clear(parts.result).append(
    h(
      'div',
      { class: 'notice change-result', role: 'status' },
      along.length ? h('p', {}, `Also retracted, because they rested on what was retracted: ${along.map((id) => `#${id}`).join(', ')}.`) : null,
      stale.length
        ? h(
            'div',
            { class: 'stale-offer' },
            h('p', {}, h('strong', {}, `${plural(stale.length, 'belief')} rest${stale.length === 1 ? 's' : ''} on what changed and ${stale.length === 1 ? 'is' : 'are'} now stale:`)),
            h('ul', { class: 'plain' }, stale.map((belief) => h('li', { dataset: { stale: belief.id } }, entryLink('belief', belief.id, `Belief #${belief.id}`), `: ${clip(belief.text, 100)}`))),
            h(
              'div',
              { class: 'buttons' },
              button('Retract these beliefs…', () => retract(stale, { onDone: showChange }), 'offer-retract'),
              button('Replace them with one new belief…', () => recordMemory('belief', { replacing: stale, onChange: showChange }), 'offer-replace'),
            ),
          )
        : null,
      promoted.length ? h('p', {}, `A person made decisions from a belief that is no longer held: ${promoted.map((id) => `#${id}`).join(', ')}. They are not changed. Replace one with a new decision if it no longer stands.`) : null,
      button('Dismiss', dismiss, 'link dismiss-result'),
    ),
  );
}
