// What the record dialogs share: dialogs, labelled fields and a picker for the items a record
// concerns. Nothing here decides what is allowed: every form sends what the person typed and shows
// the server's answer in its own words, so no field is marked as required (the browser would refuse
// a blank before the server could say why).

import { Refusal } from './api.js';
import { h } from './dom.js';
import { icon } from './icons.js';
import { SIZES, SIZE_POINTS, SIZE_WORDS, codeOf, kindIcon, noteCode, parseNodeRef, readNodeRef, ruleCode } from './labels.js';
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

// A modal dialog that removes itself when it closes. `content` is what goes under the heading. Opening it opens nothing else: the
// browser puts the focus on the first control it can, and when that is a select (whose menu a key press would open) the focus
// goes to the first plain text field instead, or to the dialog itself when there is none.
export function openDialog(title, content, { wide = false } = {}) {
  // The heading is words, or a list of words and nodes when it quotes somebody's title (which is marked as theirs).
  const heading = h('h2', { id: uid('dialog-') }, ...(Array.isArray(title) ? title : [title]));
  const box = h('dialog', { class: `modal${wide ? ' wide' : ''}`, 'aria-labelledby': heading.id, tabindex: -1 }, heading, ...content);
  box.addEventListener('close', () => box.remove());
  document.getElementById('dialogs').append(box);
  box.showModal();
  if (document.activeElement instanceof HTMLSelectElement && box.contains(document.activeElement)) {
    (box.querySelector('textarea, input:not([type=checkbox], [type=radio], [type=hidden], [type=button], [type=submit])') || box).focus();
  }
  return box;
}

// The empty first option of a select that asks for a decision: it is what the select shows until the person chooses, in a
// quiet look (the `choose` class), so that a menu never starts with an answer already picked.
export const chooseOption = (words) => h('option', { value: '', class: 'choose' }, words);

// An input's list of suggestions (`list`) is given to it only when the person acts on it (a press, an arrow key or typing), so
// that focusing it, as a dialog does when it opens, can never show the suggestions.
export function suggestOnAction(input, listId) {
  const give = () => input.setAttribute('list', listId);
  for (const type of ['pointerdown', 'keydown', 'input']) input.addEventListener(type, give, { once: true });
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
// The reference a chosen item is known by on the page: story:12, rule:3, note:5.
export const pickedRef = (item) => (item.type === 'node' ? item.id : `${item.type}:${item.id}`);

// The kind of what was chosen (epic, story, task, rule or note), for its icon.
export const pickedKind = (item) => (item.type === 'node' ? parseNodeRef(item.id)?.kind || 'story' : item.type);

function titleOfRef(type, id) {
  if (type === 'node') return titleOf(id);
  const list = type === 'rule' ? store.rules : store.notes;
  const found = (list || []).find((item) => item.id === id);
  if (!found) return '';
  return type === 'note' ? headlineOf(found) : found.title;
}

// What a chosen item is called in a list or a chip: its code, then its name (S-12 Unlock by typing the bike number,
// AC-3 A rider can have only one open ride, N-7 The apps support the last two major versions).
export function wordsOfRef(type, id) {
  const code = type === 'node' ? codeOf(id) : type === 'rule' ? ruleCode(id) : noteCode(id);
  return [code, titleOfRef(type, id)].filter(Boolean).join(' ');
}

// What a person is offered to pick from, each as { type, id, kind, title, label }: the label is the code and the name.
// `kinds` is what can be offered (node, rule, note) and `except` an epic, story or task to leave out.
export function choicesOf(kinds, { except = null } = {}) {
  const found = [];
  if (kinds.includes('node')) {
    for (const node of store.nodes.values()) if (node.ref !== except) found.push({ type: 'node', id: node.ref, kind: node.kind, title: node.title });
  }
  if (kinds.includes('rule')) for (const rule of store.rules || []) found.push({ type: 'rule', id: rule.id, kind: 'rule', title: rule.title });
  if (kinds.includes('note')) for (const note of store.notes || []) found.push({ type: 'note', id: note.id, kind: 'note', title: clip(headlineOf(note), 90) });
  return found.map((one) => ({ ...one, label: `${one.type === 'node' ? codeOf(one.id) : one.type === 'rule' ? ruleCode(one.id) : noteCode(one.id)} ${one.title}` }));
}

// The item a typed or picked label or name stands for. 'several' when more than one has that name, and null when none does.
export function findChoice(text, choices) {
  const typed = text.trim().toLowerCase();
  if (!typed) return null;
  for (const test of [(one) => one.label.toLowerCase() === typed, (one) => one.title.toLowerCase() === typed]) {
    const found = choices.filter(test);
    if (found.length === 1) return found[0];
    if (found.length > 1) return 'several';
  }
  return null;
}

// What was typed as a code or in words: S-12 or story 12 for an epic, story or task, AC-3 ("criterion 3" or "rule 3") for an
// acceptance criterion and N-5 ("note 5") for a note. A bare number names nothing, since each kind is numbered on its own.
export function parseRef(text, kinds) {
  const typed = text.trim();
  let found = /^(?:rule|requirement|(?:acceptance\s+)?criterion|ac)\s*[:#-]?\s*#?(\d+)/i.exec(typed);
  if (found && kinds.includes('rule')) return { type: 'rule', id: Number(found[1]) };
  found = /^(?:note|n)\s*[:#-]?\s*#?(\d+)/i.exec(typed);
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
  const choices = choicesOf(kinds);
  const input = h('input', { type: 'text', autocomplete: 'off', 'aria-label': `${label}: type a code or pick from the list`, placeholder: 'a code such as S-12, or a title' });
  suggestOnAction(input, listId);
  const problem = h('p', { class: 'problem', role: 'alert' });
  const shown = h('ul', { class: 'chips plain picker-chosen', 'aria-label': `${label}: chosen` });

  const draw = () =>
    shown.replaceChildren(
      ...[...refs.values()].map((ref) => {
        const words = wordsOfRef(ref.type, ref.id);
        return h(
          'li',
          { class: 'chip', title: words, dataset: { ref: pickedRef(ref) } },
          kindIcon(pickedKind(ref), 14),
          h('span', { class: 'chip-text' }, clip(words, 60)),
          h('button', { type: 'button', class: 'chip-remove ghost icon-only small', 'aria-label': `Remove ${words}`, onclick: () => { refs.delete(pickedRef(ref)); draw(); } }, icon('close', { size: 12 })),
        );
      }),
    );
  const add = () => {
    problem.replaceChildren();
    if (!input.value.trim()) return;
    const named = findChoice(input.value, choices);
    if (named === 'several') {
      refuse(problem, 'More than one item has that name. Pick one from the list.');
      return;
    }
    const ref = named ? { type: named.type, id: named.id } : parseRef(input.value, kinds);
    if (!ref) {
      refuse(problem, 'Type a code such as S-12 (AC-3 for an acceptance criterion, N-5 for a note), or pick from the list.');
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
    h('datalist', { id: listId }, choices.map((one) => h('option', { value: one.label }))),
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
