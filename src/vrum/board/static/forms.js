// What the record dialogs share: dialogs, labelled fields and a picker for the items a record
// concerns. Nothing here decides what is allowed: every form sends what the person typed and shows
// the server's answer in its own words, so no field is marked as required (the browser would refuse
// a blank before the server could say why).

import { Refusal } from './api.js';
import { h } from './dom.js';
import { icon } from './icons.js';
import { SIZES, SIZE_POINTS, SIZE_WORDS, codeOf, readNodeRef } from './labels.js';
import { store, titleOf, headlineOf } from './store.js';

let counter = 0;
export const uid = (prefix = 'f') => `${prefix}${(counter += 1)}`;

// A label, its control and a help line, joined so that a screen reader reads them together. A date field is a
// box around its input (datepicker.js), and the label and the help belong to the input.
export function field(label, control, help = null) {
  const input = control.classList.contains('date-field') ? control.querySelector('input') : control;
  if (!input.id) input.id = uid();
  const note = help ? h('p', { class: 'hint', id: `${input.id}-help` }, help) : null;
  if (note) input.setAttribute('aria-describedby', note.id);
  return h('div', { class: 'field' }, h('label', { for: input.id }, label), control, note);
}

// Two or more fields side by side, which fall into one column when there is not room.
export const fieldRow = (...fields) => h('div', { class: 'form-row' }, ...fields);

// The choice of a size for a story or task: "No size" first, then the five. Its value is the size (xs to xl) or empty.
export function sizeSelect({ id, name = 'size', value = null }) {
  return h(
    'select',
    { id, name, value: value || '' },
    h('option', { value: '' }, 'No size'),
    SIZES.map((size) => h('option', { value: size }, `${SIZE_WORDS[size]}, ${SIZE_POINTS[size]} pts`)),
  );
}

// What a server said no with, in the place made for it, with a sign so that it is not only a colour.
export function refuse(place, message) {
  place.replaceChildren(icon('alert', { size: 16 }), h('span', { class: 'problem-text' }, message));
}

export const clip = (text, size = 90) => {
  const one = String(text).replace(/\s+/g, ' ').trim();
  return one.length > size ? `${one.slice(0, size - 1)}…` : one;
};

// The plain words a Markdown text says, on one line (see markdown.js).
export { plainText } from './markdown.js';

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
// with everything typed; anything else goes on to the console. The buttons are on the right, the
// one that does the work last.
export function dialogForm({ fields, submit, cancel = null, work, also = [] }) {
  const problem = h('p', { class: 'problem', role: 'alert' });
  const buttons = [];
  const guarded = async (job) => {
    problem.replaceChildren();
    buttons.forEach((button) => { button.disabled = true; });
    try {
      await job();
    } catch (error) {
      if (!(error instanceof Refusal)) throw error;
      refuse(problem, error.message);
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
    h('div', { class: 'actions' }, h('button', { type: 'button', onclick: () => form.closest('dialog')?.close() }, cancel || 'Cancel'), ...more, send),
  );
  return { form, problem };
}

// -- what a record concerns ------------------------------------------------------------------------

// What is chosen is { type, id }: `type` is node, rule or note, and `id` is the reference
// (story:12) of an epic, story or task and the number of an acceptance criterion or a note.
const refName = {
  node: (ref) => codeOf(ref),
  rule: (id) => `Criterion #${id}`,
  note: (id) => `Note #${id}`,
};
// The reference a chosen item is known by on the page: story:12, rule:3, note:5.
export const pickedRef = (item) => (item.type === 'node' ? item.id : `${item.type}:${item.id}`);

function titleOfRef(type, id) {
  if (type === 'node') return titleOf(id);
  const list = type === 'rule' ? store.rules : store.notes;
  const found = (list || []).find((item) => item.id === id);
  if (!found) return `${type === 'rule' ? 'Criterion' : 'Note'} #${id}`;
  return type === 'note' ? headlineOf(found) : found.title;
}

// What was typed or picked: S-12 or story 12 for an epic, story or task, "criterion 3" (or "rule 3")
// and "note 5". A bare number names nothing, since each kind is numbered on its own.
export function parseRef(text, kinds) {
  const typed = text.trim();
  let found = /^(?:rule|requirement|(?:acceptance\s+)?criterion)\s*[:#]?\s*#?(\d+)/i.exec(typed);
  if (found && kinds.includes('rule')) return { type: 'rule', id: Number(found[1]) };
  found = /^note\s*[:#]?\s*#?(\d+)/i.exec(typed);
  if (found && kinds.includes('note')) return { type: 'note', id: Number(found[1]) };
  const node = readNodeRef(typed);
  if (node && kinds.includes('node')) return { type: 'node', id: node };
  return null;
}

// A box to type or pick an item in, an Add button, and the items chosen so far with a way to take
// each one out again. `kinds` says what can be added: node, rule, note.
export function picker({ label, kinds = ['node', 'rule'], chosen = [], help = null }) {
  const refs = new Map();
  const listId = uid('picker-list-');
  const options = [];
  if (kinds.includes('node')) for (const node of store.nodes.values()) options.push(`${codeOf(node.ref)} ${node.title}`);
  if (kinds.includes('rule')) for (const rule of store.rules || []) options.push(`Criterion #${rule.id} ${rule.title}`);
  if (kinds.includes('note')) for (const note of store.notes || []) options.push(`Note #${note.id} ${headlineOf(note)}`);
  const input = h('input', { type: 'text', list: listId, autocomplete: 'off', 'aria-label': `${label}: type a code or pick from the list`, placeholder: 'a code such as S-12, or a title' });
  const problem = h('p', { class: 'problem', role: 'alert' });
  const shown = h('ul', { class: 'chips plain picker-chosen', 'aria-label': `${label}: chosen` });

  const draw = () =>
    shown.replaceChildren(
      ...[...refs.values()].map((ref) =>
        h(
          'li',
          { class: 'chip', dataset: { ref: pickedRef(ref) } },
          h('span', { class: 'chip-text' }, `${refName[ref.type](ref.id)} ${clip(titleOfRef(ref.type, ref.id), 40)}`),
          h('button', { type: 'button', class: 'chip-remove ghost icon-only small', 'aria-label': `Remove ${refName[ref.type](ref.id)}`, onclick: () => { refs.delete(pickedRef(ref)); draw(); } }, icon('close', { size: 12 })),
        ),
      ),
    );
  const add = () => {
    problem.replaceChildren();
    if (!input.value.trim()) return;
    const ref = parseRef(input.value, kinds);
    if (!ref) {
      refuse(problem, 'Type a code such as S-12 (criterion 3 for an acceptance criterion, note 5 for a note), or pick from the list.');
      return;
    }
    refs.set(pickedRef(ref), ref);
    input.value = '';
    draw();
  };
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      add();
    }
  });
  for (const ref of chosen) refs.set(pickedRef(ref), ref);
  draw();

  const element = h(
    'div',
    { class: 'field picker' },
    h('label', { for: (input.id = uid()) }, label),
    h('div', { class: 'picker-row' }, input, h('button', { type: 'button', onclick: add }, 'Add')),
    h('datalist', { id: listId }, options.map((value) => h('option', { value }))),
    shown,
    help ? h('p', { class: 'hint' }, help) : null,
    problem,
  );
  return {
    element,
    // Forget what was chosen, once it has been sent.
    reset() {
      refs.clear();
      input.value = '';
      problem.replaceChildren();
      draw();
    },
    // What to send: a list for each kind, and only when something was chosen: `nodes` (references),
    // `rule_ids` and `note_ids`.
    value() {
      add();
      const pick = (type) => [...refs.values()].filter((ref) => ref.type === type).map((ref) => ref.id);
      const out = {};
      if (kinds.includes('node') && pick('node').length) out.nodes = pick('node');
      if (kinds.includes('rule') && pick('rule').length) out.rule_ids = pick('rule');
      if (kinds.includes('note') && pick('note').length) out.note_ids = pick('note');
      return out;
    },
  };
}
