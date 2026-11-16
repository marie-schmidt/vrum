// What the Rules and Memory pages share: dialogs, labelled fields, a picker for the items a record
// concerns and a picker for the evidence of a belief. Nothing here decides what is allowed: every
// form sends what the person typed and shows the server's answer in its own words, so no field is
// marked as required (the browser would refuse a blank before the server could say why).

import { Refusal } from './api.js';
import { h } from './dom.js';
import { store, titleOf } from './store.js';

let counter = 0;
export const uid = (prefix = 'f') => `${prefix}${(counter += 1)}`;

// A label, its control and a help line, joined so that a screen reader reads them together.
export function field(label, control, help = null) {
  if (!control.id) control.id = uid();
  const note = help ? h('p', { class: 'quiet', id: `${control.id}-help` }, help) : null;
  if (note) control.setAttribute('aria-describedby', note.id);
  return h('div', { class: 'field' }, h('label', { for: control.id }, label), control, note);
}

export const clip = (text, size = 90) => {
  const one = String(text).replace(/\s+/g, ' ').trim();
  return one.length > size ? `${one.slice(0, size - 1)}…` : one;
};

// A modal dialog that removes itself when it closes. `content` is what goes under the heading.
export function openDialog(title, content, { wide = false } = {}) {
  const heading = h('h2', { id: uid('dialog-') }, title);
  const box = h('dialog', { class: `modal${wide ? ' wide' : ''}`, 'aria-labelledby': heading.id }, heading, ...content);
  box.addEventListener('close', () => box.remove());
  document.getElementById('dialogs').append(box);
  box.showModal();
  return box;
}

// A form that does `work` when it is sent (and `also` for any further buttons, each with its own
// work). A refusal is shown under the fields, in the server's words, and the dialog stays open
// with everything typed; anything else goes on to the console.
export function dialogForm({ fields, submit, cancel = null, work, also = [] }) {
  const problem = h('p', { class: 'problem', role: 'alert' });
  const buttons = [];
  const guarded = async (job) => {
    problem.textContent = '';
    buttons.forEach((button) => { button.disabled = true; });
    try {
      await job();
    } catch (error) {
      if (!(error instanceof Refusal)) throw error;
      problem.textContent = error.message;
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  };
  const send = h('button', { type: 'submit', class: 'primary' }, submit);
  buttons.push(send);
  const more = also.map(({ label, work: job }) => {
    const button = h('button', { type: 'button', onclick: () => guarded(job) }, label);
    buttons.push(button);
    return button;
  });
  const form = h(
    'form',
    {
      method: 'dialog',
      novalidate: true,
      onsubmit: (event) => {
        event.preventDefault();
        guarded(work);
      },
    },
    ...fields,
    problem,
    h('div', { class: 'buttons' }, send, ...more, h('button', { type: 'button', onclick: () => form.closest('dialog')?.close() }, cancel || 'Cancel')),
  );
  return { form, problem };
}

// -- what a record concerns ------------------------------------------------------------------------

// The way an item is named in a picker: #12 for an epic, story or task, "rule 3", "decision 5".
const refText = {
  node: (id) => `#${id}`,
  rule: (id) => `rule ${id}`,
  decision: (id) => `decision ${id}`,
};

function titleOfRef(type, id) {
  if (type === 'node') return titleOf(id);
  const list = type === 'rule' ? store.rules : store.decisions;
  const found = (list || []).find((item) => item.id === id);
  return found ? found.title : `${type} ${id}`;
}

export function parseRef(text, kinds) {
  const typed = text.trim();
  let found = /^rule\s*#?\s*(\d+)/i.exec(typed);
  if (found && kinds.includes('rule')) return { type: 'rule', id: Number(found[1]) };
  found = /^decision\s*#?\s*(\d+)/i.exec(typed);
  if (found && kinds.includes('decision')) return { type: 'decision', id: Number(found[1]) };
  found = /^#?(\d+)/.exec(typed);
  if (found && kinds.includes('node')) return { type: 'node', id: Number(found[1]) };
  return null;
}

// A box to type or pick an item in, an Add button, and the items chosen so far with a way to take
// each one out again. `kinds` says what can be added: node, rule, decision.
export function picker({ label, kinds = ['node', 'rule'], chosen = [], help = null }) {
  const refs = new Map();
  const listId = uid('picker-list-');
  const options = [];
  if (kinds.includes('node')) for (const node of store.nodes.values()) options.push(`#${node.id} ${node.title}`);
  if (kinds.includes('rule')) for (const rule of store.rules || []) options.push(`rule ${rule.id} ${rule.title}`);
  if (kinds.includes('decision')) for (const decision of store.decisions || []) options.push(`decision ${decision.id} ${decision.title}`);
  const input = h('input', { type: 'text', list: listId, autocomplete: 'off', 'aria-label': `${label}: type an id or pick from the list`, placeholder: 'an id such as #12, or a title' });
  const problem = h('p', { class: 'problem', role: 'alert' });
  const shown = h('ul', { class: 'chips plain' });

  const draw = () =>
    shown.replaceChildren(
      ...[...refs.values()].map((ref) =>
        h(
          'li',
          { class: 'chip', dataset: { ref: `${ref.type}:${ref.id}` } },
          `${refText[ref.type](ref.id)} ${clip(titleOfRef(ref.type, ref.id), 40)} `,
          h('button', { type: 'button', class: 'link', 'aria-label': `Take out ${refText[ref.type](ref.id)}`, onclick: () => { refs.delete(`${ref.type}:${ref.id}`); draw(); } }, '✕'),
        ),
      ),
    );
  const add = () => {
    problem.textContent = '';
    if (!input.value.trim()) return;
    const ref = parseRef(input.value, kinds);
    if (!ref) {
      problem.textContent = 'Type an id such as #12 (rule 3 for a rule, decision 5 for a decision), or pick from the list.';
      return;
    }
    refs.set(`${ref.type}:${ref.id}`, ref);
    input.value = '';
    draw();
  };
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      add();
    }
  });
  for (const ref of chosen) refs.set(`${ref.type}:${ref.id}`, ref);
  draw();

  const element = h(
    'div',
    { class: 'field picker' },
    h('label', { for: (input.id = uid()) }, label),
    h('div', { class: 'inline-form' }, input, h('button', { type: 'button', onclick: add }, 'Add')),
    h('datalist', { id: listId }, options.map((value) => h('option', { value }))),
    shown,
    help ? h('p', { class: 'quiet' }, help) : null,
    problem,
  );
  return {
    element,
    // What to send: a list for each kind, and only when something was chosen.
    value() {
      add();
      const pick = (type) => [...refs.values()].filter((ref) => ref.type === type).map((ref) => ref.id);
      const out = {};
      if (kinds.includes('node') && pick('node').length) out.node_ids = pick('node');
      if (kinds.includes('rule') && pick('rule').length) out.rule_ids = pick('rule');
      if (kinds.includes('decision') && pick('decision').length) out.decision_ids = pick('decision');
      return out;
    },
  };
}

// -- the evidence of a belief ----------------------------------------------------------------------

// The facts and decisions that are in force, to tick the ones a belief rests on. A belief can rest
// on nothing else (not on another belief), and the server checks that again.
export function evidencePicker({ chosen = [] } = {}) {
  const wanted = new Set(chosen.map((item) => `${item.type}:${item.id}`));
  const facts = (store.memories || []).filter((memory) => memory.type === 'fact' && memory.status === 'active');
  const decisions = (store.decisions || []).filter((decision) => decision.status === 'active');
  const boxes = [];
  const row = (type, id, words) => {
    const box = h('input', { type: 'checkbox', value: `${type}:${id}`, checked: wanted.has(`${type}:${id}`) });
    boxes.push(box);
    return h('li', { dataset: { evidence: `${type}:${id}` } }, h('label', { class: 'check' }, box, ` ${words}`));
  };
  const items = [
    ...decisions.map((decision) => row('decision', decision.id, `${decision.kind === 'principle' ? 'Principle' : 'Decision'} #${decision.id}: ${clip(decision.title, 80)}`)),
    ...facts.map((fact) => row('fact', fact.id, `Fact #${fact.id}: ${clip(fact.text, 80)}`)),
  ];
  const find = h('input', { type: 'search', autocomplete: 'off', 'aria-label': 'Narrow the list of evidence', placeholder: 'narrow the list' });
  const list = h('ul', { class: 'plain evidence-list' }, items.length ? items : h('li', { class: 'quiet' }, 'There are no facts or decisions in force to rest on yet. Record one first.'));
  find.addEventListener('input', () => {
    const needle = find.value.trim().toLowerCase();
    for (const li of list.querySelectorAll('li')) li.hidden = Boolean(needle) && !li.textContent.toLowerCase().includes(needle);
  });
  return {
    element: h('div', { class: 'field picker', role: 'group', 'aria-label': 'Evidence' }, h('label', { for: (find.id = uid()) }, 'Evidence: the facts and decisions it rests on'), find, list),
    value() {
      const ticked = boxes.filter((box) => box.checked).map((box) => box.value.split(':'));
      const out = {};
      const factIds = ticked.filter(([type]) => type === 'fact').map(([, id]) => Number(id));
      const decisionIds = ticked.filter(([type]) => type === 'decision').map(([, id]) => Number(id));
      if (factIds.length) out.evidence_fact_ids = factIds;
      if (decisionIds.length) out.evidence_decision_ids = decisionIds;
      return out;
    },
  };
}
