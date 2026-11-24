// The dialogs for notes and acceptance criteria: add a note, replace, withdraw, change what a note concerns,
// confirm or un-confirm, and write or place an acceptance criterion. Each sends one operation of an agent, and
// shows the server's answer in its own words, with everything typed kept. Nothing is edited: a change
// is a new note that replaces the old one, which stays as a record. records.js re-exports what other
// pages call.

import { Refusal, call } from './api.js';
import { account, notify, openItem } from './ctx.js';
import { dateField } from './datepicker.js';
import { h, plural } from './dom.js';
import { clip, dialogForm, field, fieldRow, openDialog, pickedKind, pickedRef, picker, refuse, wordsOfRef } from './forms.js';
import { holderPicker } from './holder-picker.js';
import { REQUIREMENT_STATUS, REQUIREMENT_STATUSES, STATUS, kindIcon, noteCode, ruleCode } from './labels.js';
import { withReferences } from './reference-picker.js';
import { loadTargets } from './references.js';
import { headlineOf, sync, titleOf } from './store.js';

const named = (control, id) => {
  control.id = id;
  return control;
};
// A note or an acceptance criterion in a sentence or a heading: its name in quotes, cut when long.
const quoted = (text, size = 80) => `“${clip(text, size)}”`;
const noteName = (record, size) => quoted(headlineOf(record), size);
// The titles the pickers offer come from the store; a dialog still opens when they cannot be read.
const titlesIfPossible = () => loadTargets().catch(() => {});
const text = (id, name, value = '') => named(h('input', { type: 'text', name, value, autocomplete: 'off' }), id);
const area = (id, name, rows, value = '') => named(withReferences(h('textarea', { name, rows, value })), id);
const date = (id, name) => named(h('input', { name }), id);

// What a note concerns, as the picker holds it: an epic, story or task by its reference (`nodes`), a
// acceptance criterion or another note by its id.
const refsOf = (value) => [
  ...(value.nodes || []).map((ref) => ({ type: 'node', id: ref })),
  ...(value.rule_ids || []).map((id) => ({ type: 'rule', id })),
  ...(value.note_ids || []).map((id) => ({ type: 'note', id })),
];

// A person who confirmed a note has to be named, with what they said, for it to be changed.
function saidBy(records, prefix) {
  const by = text(`${prefix}-said-by`, 'said_by');
  const quote = area(`${prefix}-quote`, 'quote', 2);
  const needed = records.some((record) => record.status === 'confirmed');
  return {
    element: h(
      'details',
      { class: 'said-by', open: needed },
      h('summary', {}, needed ? 'Confirming person and their words' : 'A person\u2019s word (confirmed notes)'),
      field('Who says it should change', by),
      field('Their words, or where', quote),
    ),
    value: () => ({ ...(by.value.trim() ? { said_by: by.value } : {}), ...(quote.value.trim() ? { quote: quote.value } : {}) }),
  };
}

const projectWide = (id, checked) => {
  const box = named(h('input', { type: 'checkbox', name: 'project_wide', checked: Boolean(checked) }), id);
  return {
    box,
    element: h('label', { class: 'check', for: id }, box, ' Applies throughout the project'),
  };
};

// Who decided it and when: two optional fields. A note nobody decided leaves them empty, and nothing is sent
// for them. A name alone is enough (the server takes today for the day); a day with no name is refused.
function decidedPart(prefix, { by = '', on = '' } = {}) {
  const who = text(`${prefix}-by`, 'decided_by', by);
  const when = date(`${prefix}-on`, 'decided_on');
  if (on) when.value = String(on).slice(0, 10);
  return {
    element: fieldRow(
      field('Decided by (optional)', who),
      field('Decided on', dateField(when, 'Decided on'), 'Leave empty for today.'),
    ),
    value: () => ({ ...(who.value.trim() ? { decided_by: who.value } : {}), ...(when.value ? { decided_on: when.value } : {}) }),
  };
}

// What a change answers with: the note it made. Adding a note answers with the note; replacing answers
// with the change, whose `replacement` is the new note.
const madeNote = (result) => result.replacement || result;

const replacingBlock = (records) =>
  h(
    'div',
    { class: 'replacing' },
    h('p', {}, h('strong', {}, `Replacing ${plural(records.length, 'note')}.`), ` ${records.length === 1 ? 'It is' : 'They are'} kept and marked replaced.`),
    h('ul', { class: 'plain' }, records.map((record) => h('li', { dataset: { replacing: record.id } }, `${noteCode(record.id)}: ${clip(headlineOf(record), 120)}`))),
  );

// -- adding a note -----------------------------------------------------------------------------------

// `prefill`: { text, why, decided_by, decided_on, project_wide, nodes, rule_ids, note_ids, onDone } to
// start from (`nodes` are references).
export async function addNoteDialog(prefill = {}) {
  await titlesIfPossible();
  const body = area('add-note-text', 'text', 4, prefill.text || '');
  const why = area('add-note-why', 'why', 3, prefill.why || '');
  const decided = decidedPart('add-note', { by: prefill.decided_by, on: prefill.decided_on });
  const wide = projectWide('add-note-project-wide', prefill.project_wide);
  const links = picker({ label: 'It applies to', kinds: ['node', 'rule', 'note'], chosen: refsOf(prefill) });
  const { form } = dialogForm({
    fields: [
      field('What is known or decided', body, 'The first line is what lists show.'),
      field('Why', why, 'Where it came from, or why it was chosen.'),
      decided.element,
      wide.element,
      links.element,
    ],
    submit: 'Add the note',
    work: async () => {
      const made = madeNote(await call('add_note', { text: body.value, why: why.value, ...decided.value(), project_wide: wide.box.checked, ...links.value() }));
      box.close();
      notify(`Added the note ${noteName(made)}. It is unconfirmed until a person confirms it.`);
      await sync();
      prefill.onDone?.(made);
    },
  });
  const box = openDialog('Add a note', [form], { wide: true });
  body.focus();
  return box;
}

// -- replacing --------------------------------------------------------------------------------------

// `record` is one note, or an array of notes to replace with one new note.
export async function supersedeDialog(record, { onDone = null } = {}) {
  const records = Array.isArray(record) ? record : [record];
  const first = records[0];
  await titlesIfPossible();
  const keep = named(h('input', { type: 'checkbox', name: 'keep_links', checked: true }), 'supersede-keep-links');
  const said = saidBy(records, 'supersede');
  const body = area('supersede-text', 'text', 4, records.length === 1 ? first.text : '');
  const why = area('supersede-why', 'why', 3);
  const decidedBefore = records.find((one) => one.decided_by);
  // A note someone decided is replaced by one that the person replacing it now decides.
  const decided = decidedPart('supersede', { by: decidedBefore ? account.name : '' });
  const wide = projectWide('supersede-project-wide', records.some((one) => one.project_wide));
  const links = picker({ label: 'It also applies to', kinds: ['node', 'rule', 'note'] });
  const { form } = dialogForm({
    fields: [
      replacingBlock(records),
      field('What is known or decided now', body, 'The first line is what lists show.'),
      field('Why it changed', why, 'Where the new words came from, or why they were chosen.'),
      decided.element,
      wide.element,
      links.element,
      h('label', { class: 'check', for: 'supersede-keep-links' }, keep, ` The new note applies to what the old ${records.length === 1 ? 'one' : 'ones'} did`),
      said.element,
    ],
    submit: 'Replace with this note',
    work: async () => {
      const made = madeNote(
        await call('supersede_note', {
          replaces: records.map((item) => item.id),
          text: body.value,
          why: why.value,
          ...decided.value(),
          project_wide: wide.box.checked,
          keep_links: keep.checked,
          ...links.value(),
          ...said.value(),
        }),
      );
      box.close();
      notify(`Added the note ${noteName(made)}, which replaces ${records.map((item) => noteName(item, 60)).join(', ')}. It is unconfirmed until a person confirms it.`);
      await sync();
      onDone?.(made);
    },
  });
  const box = openDialog(`Replace ${plural(records.length, 'note')} with a new note`, [form], { wide: true });
  body.focus();
  return box;
}

// -- withdrawing -------------------------------------------------------------------------------------

// `record` is one note, or an array of notes to withdraw together.
export async function withdrawDialog(record, { onDone = null } = {}) {
  const records = Array.isArray(record) ? record : [record];
  const reason = area('withdraw-reason', 'reason', 3);
  const said = saidBy(records, 'withdraw');
  const { form } = dialogForm({
    fields: [
      h('ul', { class: 'plain' }, records.map((item) => h('li', { dataset: { withdrawing: item.id, words: 'theirs' } }, `${noteCode(item.id)}: ${clip(headlineOf(item), 120)}`))),
      field('Why it is withdrawn', reason),
      said.element,
    ],
    submit: 'Withdraw',
    work: async () => {
      const result = await call('withdraw_note', { note_ids: records.map((item) => item.id), reason: reason.value, ...said.value() });
      box.close();
      notify(`Withdrew ${records.map((item) => noteName(item, 60)).join(', ')}.`);
      await sync();
      onDone?.(result);
    },
  });
  const box = openDialog(`Withdraw ${plural(records.length, 'note')}`, [form], { wide: true });
  reason.focus();
  return box;
}

// -- what a note concerns ------------------------------------------------------------------------------

// Adds and takes out what a note concerns. The dialog stays open, so that several links can be
// changed, and reads the note again after each change.
export async function linkDialog(record) {
  await titlesIfPossible();
  let current = record;
  const links = picker({ label: 'Add what it applies to', kinds: ['node', 'rule', 'note'] });
  const shown = h('ul', { class: 'plain concerns-list' });
  const problem = h('p', { class: 'problem', role: 'alert' });
  const name = (ref) => wordsOfRef(ref.type, ref.id);
  const LISTS = { node: 'nodes', rule: 'rule_ids', note: 'note_ids' };
  const draw = () => {
    const refs = refsOf(current);
    shown.replaceChildren(
      ...(refs.length
        ? refs.map((ref) =>
            h(
              'li',
              { dataset: { ref: pickedRef(ref) } },
              kindIcon(pickedKind(ref), 16),
              h('span', { class: 'concern-name', title: name(ref) }, clip(name(ref), 90)),
              h('button', { type: 'button', class: 'unlink small', 'aria-label': `Remove ${name(ref)}`, onclick: () => change('unlink_note', { [LISTS[ref.type]]: [ref.id] }) }, 'Remove'),
            ),
          )
        : [h('li', { class: 'quiet' }, 'None yet.')]),
    );
  };
  const change = async (tool, ids) => {
    problem.replaceChildren();
    if (!Object.keys(ids).length) {
      refuse(problem, 'Choose what to add first: type a code such as S-12, or pick from the list.');
      return;
    }
    try {
      await call(tool, { note_id: record.id, ...ids });
      notify(tool === 'link_note' ? 'Added.' : 'Removed.');
      current = await call('get_note', { note_id: record.id });
      if (tool === 'link_note') links.reset();
      await sync();
      draw();
    } catch (error) {
      if (!(error instanceof Refusal)) throw error;
      refuse(problem, error.message);
    }
  };
  draw();
  const box = openDialog(`What ${noteName(record)} applies to`, [
    shown,
    links.element,
    problem,
    h('div', { class: 'actions' }, h('button', { type: 'button', id: 'concerns-done', onclick: () => box.close() }, 'Done'), h('button', { type: 'button', class: 'primary', id: 'concerns-add', onclick: () => change('link_note', links.value()) }, 'Add')),
  ]);
  box.querySelector('input[type=text]')?.focus();
  return box;
}

// -- confirming and un-confirming ----------------------------------------------------------------------

// `confirmation` is the state being chosen: confirmed asks for the person who reviewed it (the one
// signed in, to start with) and, if wished, their words; unconfirmed asks for who says so and their
// words, both. `onDone(result)` runs after the server accepted it, `onClose` whenever the dialog
// goes (so that a dropdown can show the truth again).
export function confirmDialog(record, confirmation, { onDone = null, onClose = null } = {}) {
  const confirming = confirmation === 'confirmed';
  const person = text('confirm-person', 'person', account.name);
  const quote = area('confirm-quote', 'quote', 3);
  const { form } = dialogForm({
    fields: [
      h('p', { dataset: { words: 'theirs' } }, h('strong', {}, `${noteCode(record.id)}: ${clip(headlineOf(record), 140)}`)),
      field(confirming ? 'The person who reviewed it' : 'The person who says so', person),
      field(confirming ? 'Their words (optional)' : 'Their words', quote),
    ],
    submit: confirming ? 'Confirm' : 'Un-confirm',
    work: async () => {
      const made = await call('set_confirmation', {
        note_id: record.id,
        confirmation,
        person: person.value,
        ...(confirming && !quote.value.trim() ? {} : { quote: quote.value }),
      });
      box.close();
      notify(confirming ? `${noteName(record)} is confirmed by ${person.value}.` : `${noteName(record)} is unconfirmed again.`);
      await sync();
      onDone?.(made);
    },
  });
  const box = openDialog([`${confirming ? 'Confirm' : 'Un-confirm'} `, h('span', { dataset: { words: 'theirs' } }, noteName(record))], [form]);
  if (onClose) box.addEventListener('close', onClose);
  person.focus();
  return box;
}

// -- writing an acceptance criterion ---------------------------------------------------------------------

// Writes an acceptance criterion (a rule), or changes one. `node` is the reference of the story or task a
// new one goes on (without it, the dialog asks, and the person may leave it for later); `rule` is the one
// to change, at the version the person saw.
// `onDone(criterion)` runs after; a new one with no `onDone` is opened on its own page.
export function requirementDialog({ rule = null, node = null, onDone = null } = {}) {
  const title = text('requirement-title', 'title', rule?.title || '');
  const body = area('requirement-body', 'body', 6, rule?.body || '');
  const status = named(h('select', { name: 'status', value: rule?.status || 'open' }, REQUIREMENT_STATUSES.map((value) => h('option', { value }, REQUIREMENT_STATUS[value].label))), 'requirement-status');
  const where = rule || node ? null : holderPicker({ label: 'Story or task (optional)' });
  const { form } = dialogForm({
    fields: [
      !rule && node ? h('p', { class: 'quiet', id: 'requirement-on' }, `It goes on ${quoted(titleOf(node))}.`) : null,
      field('Title', title),
      field('Details', body),
      fieldRow(field('Status', status)),
      where ? where.element : null,
    ].filter(Boolean),
    submit: rule ? 'Save changes' : 'Add the acceptance criterion',
    work: async () => {
      let made;
      if (!rule) {
        made = await call('create_rule', { title: title.value, body: body.value, status: status.value, node: node ?? where.value() });
        notify(`Added the acceptance criterion ${quoted(made.title)}.`);
      } else {
        const args = { rule_id: rule.id, expected_version: rule.version };
        if (title.value !== rule.title) args.title = title.value;
        if (body.value !== (rule.body || '')) args.body = body.value;
        if (status.value !== rule.status) args.status = status.value;
        if (Object.keys(args).length === 2) throw new Refusal('Nothing has been changed yet, so there is nothing to save.');
        made = await call('update_rule', args);
        notify(`Saved ${quoted(made.title)}.`);
      }
      box.close();
      await sync();
      if (!rule && !onDone) openItem(`rule:${made.id}`);
      onDone?.(made);
    },
  });
  const box = openDialog(rule ? `Edit ${quoted(rule.title)}` : 'Add an acceptance criterion', [form], { wide: Boolean(where) });
  title.focus();
  return box;
}

// Puts an acceptance criterion that is placed nowhere on a story or a task, or moves one from the
// story or task it is on to another.
export function placeRequirementDialog(rule, { onDone = null } = {}) {
  const placing = rule.node === null || rule.node === undefined;
  const where = holderPicker({ label: 'Story or task' });
  const here = placing ? 'It is not placed yet.' : `It is on ${quoted(titleOf(rule.node))}.`;
  const { form } = dialogForm({
    fields: [
      h('p', {}, h('strong', {}, `${ruleCode(rule.id)}: ${rule.title}`)),
      h('p', { class: 'quiet' }, here),
      where.element,
    ],
    submit: placing ? 'Put it there' : 'Move it there',
    work: async () => {
      const target = where.value();
      if (!target) throw new Refusal('Choose the story or task first.');
      const made = await call('place_rule', { rule_id: rule.id, expected_version: rule.version, node: target });
      box.close();
      notify(`${placing ? 'Put' : 'Moved'} ${quoted(rule.title)} ${placing ? 'on' : 'to'} ${quoted(titleOf(target))}.`);
      await sync();
      onDone?.(made);
    },
  });
  const box = openDialog(placing ? `Put ${quoted(rule.title)} on a story or task` : `Move ${quoted(rule.title)} to another story or task`, [form], { wide: true });
  where.focus();
  return box;
}

// -- why work is blocked or dropped ----------------------------------------------------------------------

// What a person is asked when they mark a story or task blocked or dropped: one field, "Why", left empty if they
// have nothing to say. `save(why)` makes the change, with `why` null when nothing was typed (the server keeps a reason
// as a handoff note), and throws the server's refusal, which is shown under the field with the words kept.
// `onDone()` runs after a change was made, `onCancel()` when the dialog is closed with nothing saved.
export function whyDialog(node, status, { save, onDone = null, onCancel = null }) {
  const word = STATUS[status].label.toLowerCase();
  const why = named(h('textarea', { name: 'why', rows: 4 }), 'why-text');
  let saved = false;
  const { form } = dialogForm({
    fields: [field('Why', why)],
    submit: `Mark as ${word}`,
    work: async () => {
      await save(why.value.trim() ? why.value : null);
      saved = true;
      box.close();
      await onDone?.();
    },
  });
  const box = openDialog(`Mark ${quoted(node.title)} as ${word}`, [form]);
  box.addEventListener('close', () => {
    if (!saved) onCancel?.();
  });
  why.focus();
  return box;
}
