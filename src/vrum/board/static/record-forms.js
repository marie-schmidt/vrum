// The dialogs for facts, decisions and requirements: record, replace, withdraw, change what a record
// concerns, confirm or un-confirm, and write or place a requirement. Each sends one operation of
// an agent, with the person's typed name, and shows the server's answer in its own words, with
// everything typed kept. Nothing is edited: a change is a new record that replaces the old one,
// which stays as a record. records.js re-exports what other pages call.

import { Refusal, call } from './api.js';
import { actor, notify } from './ctx.js';
import { h, plural } from './dom.js';
import { parentChoices } from './create.js';
import { recordKind } from './entries.js';
import { clip, dialogForm, field, fieldRow, openDialog, picker, refuse } from './forms.js';
import { kindIcon } from './labels.js';
import { withReferences } from './reference-picker.js';
import { kindWords, loadTargets } from './references.js';
import { store, sync, titleOf } from './store.js';

const named = (control, id) => {
  control.id = id;
  return control;
};
// The titles the pickers offer come from the store; a dialog still opens when they cannot be read.
const titlesIfPossible = () => loadTargets().catch(() => {});
const text = (id, name, value = '') => named(h('input', { type: 'text', name, value, autocomplete: 'off' }), id);
const area = (id, name, rows, value = '') => named(withReferences(h('textarea', { name, rows, value })), id);
const date = (id, name) => named(h('input', { type: 'date', name }), id);

const refsOf = (value) => [
  ...(value.node_ids || []).map((id) => ({ type: 'node', id })),
  ...(value.rule_ids || []).map((id) => ({ type: 'rule', id })),
  ...(value.decision_ids || []).map((id) => ({ type: 'decision', id })),
];

// A person who confirmed a record has to be named, with what they said, for it to be changed.
function saidBy(records, prefix) {
  const by = text(`${prefix}-said-by`, 'said_by');
  const quote = area(`${prefix}-quote`, 'quote', 2);
  const needed = records.some((record) => record.confirmation === 'confirmed');
  return {
    element: h(
      'details',
      { class: 'said-by', open: needed },
      h('summary', {}, needed ? 'A person confirmed this: who now says it should change, and their words' : 'Needed only to change a record a person has confirmed'),
      field('Who says it should change', by),
      field('What they said, or where', quote),
    ),
    value: () => ({ ...(by.value.trim() ? { said_by: by.value } : {}), ...(quote.value.trim() ? { quote: quote.value } : {}) }),
  };
}

const projectWide = (id, checked) => {
  const box = named(h('input', { type: 'checkbox', name: 'project_wide', checked: Boolean(checked) }), id);
  return {
    box,
    element: h('label', { class: 'check', for: id }, box, ' Applies throughout the project, not only to what it is linked to'),
  };
};

const replacingBlock = (records, kind) =>
  h(
    'div',
    { class: 'replacing' },
    h('p', {}, h('strong', {}, `Replacing ${plural(records.length, kind)}.`), ' They are kept, marked replaced, and point to the new one. Nothing is edited.'),
    h('ul', { class: 'plain' }, records.map((record) => h('li', { dataset: { replacing: record.id } }, `#${record.id}: ${clip(kind === 'decision' ? record.title : record.text, 120)}`))),
  );

// -- recording a fact ------------------------------------------------------------------------------

// `prefill`: { text, source, node_ids, rule_ids, decision_ids, onDone } to start from.
export async function recordFactDialog(prefill = {}) {
  await titlesIfPossible();
  const body = area('record-fact-text', 'text', 4, prefill.text || '');
  const source = text('record-fact-source', 'source', prefill.source || '');
  const links = picker({ label: 'It concerns', kinds: ['node', 'rule', 'decision'], chosen: refsOf(prefill) });
  const { form } = dialogForm({
    fields: [
      h('p', { class: 'quiet' }, 'Recording a fact does not confirm it: it stays Unconfirmed until a person reviews it.'),
      field('What is known, in plain words', body),
      field('Where it came from', source, 'Who said it, which document, what was observed. Never blank.'),
      links.element,
    ],
    submit: 'Record the fact',
    work: async () => {
      const made = await call('retain_fact', { actor: actor(), text: body.value, source: source.value, ...links.value() });
      box.close();
      notify(`Recorded fact #${made.id}. It is unconfirmed until a person confirms it.`);
      await sync();
      prefill.onDone?.(made);
    },
  });
  const box = openDialog('Record a fact', [form], { wide: true });
  body.focus();
  return box;
}

// -- recording a decision --------------------------------------------------------------------------

// `prefill`: { title, text, reason, decided_by, project_wide, node_ids, rule_ids, onDone }.
export async function recordDecisionDialog(prefill = {}) {
  await titlesIfPossible();
  const title = text('record-decision-title', 'title', prefill.title || '');
  const body = area('record-decision-text', 'text', 4, prefill.text || '');
  const reason = area('record-decision-reason', 'reason', 3, prefill.reason || '');
  const by = text('record-decision-by', 'decided_by', prefill.decided_by || actor());
  const on = date('record-decision-on', 'decided_on');
  const wide = projectWide('record-decision-project-wide', prefill.project_wide);
  const links = picker({ label: 'It affects', kinds: ['node', 'rule'], chosen: refsOf(prefill) });
  const { form } = dialogForm({
    fields: [
      h('p', { class: 'quiet' }, 'Recording a decision does not confirm it: it stays Unconfirmed until a person reviews it.'),
      field('Title', title),
      field('What is decided', body),
      field('Why', reason),
      fieldRow(field('Decided by (the person)', by, 'Your own name is recorded separately, as the one who typed it.'), field('Decided on', on, 'Leave empty for today.')),
      wide.element,
      links.element,
    ],
    submit: 'Record the decision',
    work: async () => {
      const made = await call('record_decision', {
        actor: actor(),
        title: title.value,
        text: body.value,
        reason: reason.value,
        decided_by: by.value,
        ...(on.value ? { decided_on: on.value } : {}),
        project_wide: wide.box.checked,
        ...links.value(),
      });
      box.close();
      notify(`Recorded decision #${made.id}, “${made.title}”. It is unconfirmed until a person confirms it.`);
      await sync();
      prefill.onDone?.(made);
    },
  });
  const box = openDialog('Record a decision', [form], { wide: true });
  title.focus();
  return box;
}

// -- replacing --------------------------------------------------------------------------------------

// `record` is one fact or decision, or an array of facts to replace with one new fact.
export async function supersedeDialog(record, { onDone = null } = {}) {
  const records = Array.isArray(record) ? record : [record];
  const kind = recordKind(records[0]);
  await titlesIfPossible();
  const first = records[0];
  const keep = named(h('input', { type: 'checkbox', name: 'keep_links', checked: true }), 'supersede-keep-links');
  const said = saidBy(records, 'supersede');
  const reason = area('supersede-reason', 'reason', 3);
  const links = picker({ label: 'It also concerns', kinds: kind === 'decision' ? ['node', 'rule'] : ['node', 'rule', 'decision'] });
  let fields;
  let send;
  let firstField;
  if (kind === 'decision') {
    const title = text('supersede-title', 'title', first.title);
    const body = area('supersede-text', 'text', 4, first.text);
    const by = text('supersede-by', 'decided_by', actor());
    const on = date('supersede-on', 'decided_on');
    const wide = projectWide('supersede-project-wide', first.project_wide);
    fields = [
      field('Title', title),
      field('What is decided now', body),
      field('Why it changed', reason),
      fieldRow(field('Decided by (the person)', by), field('Decided on', on, 'Leave empty for today.')),
      wide.element,
    ];
    send = () =>
      call('supersede_decision', {
        actor: actor(),
        decision_id: first.id,
        title: title.value,
        text: body.value,
        reason: reason.value,
        decided_by: by.value,
        ...(on.value ? { decided_on: on.value } : {}),
        project_wide: wide.box.checked,
        keep_links: keep.checked,
        ...links.value(),
        ...said.value(),
      });
    firstField = title;
  } else {
    const body = area('supersede-text', 'text', 4, records.length === 1 ? first.text : '');
    const source = text('supersede-source', 'source', records.length === 1 ? first.source || '' : '');
    fields = [field('What is known now, in plain words', body), field('Where it came from', source, 'Who said it, which document, what was observed. Never blank.'), field('Why it is being replaced', reason)];
    send = () =>
      call('supersede_fact', {
        actor: actor(),
        replaces: records.map((item) => item.id),
        reason: reason.value,
        text: body.value,
        source: source.value,
        keep_links: keep.checked,
        ...links.value(),
        ...said.value(),
      });
    firstField = body;
  }
  const { form } = dialogForm({
    fields: [
      replacingBlock(records, kind),
      ...fields,
      links.element,
      h('label', { class: 'check', for: 'supersede-keep-links' }, keep, ` The new ${kind} concerns what the old ${records.length === 1 ? 'one' : 'ones'} did`),
      said.element,
    ],
    submit: `Replace with this ${kind}`,
    work: async () => {
      const made = await send();
      box.close();
      notify(`Recorded ${kind} #${made.id}, which replaces ${records.map((item) => `#${item.id}`).join(', ')}. It is unconfirmed until a person confirms it.`);
      await sync();
      onDone?.(made);
    },
  });
  const box = openDialog(`Replace ${plural(records.length, kind)} with a new ${kind}`, [form], { wide: true });
  firstField.focus();
  return box;
}

// -- withdrawing -------------------------------------------------------------------------------------

// `record` is one fact or decision, or an array of facts to withdraw together.
export async function withdrawDialog(record, { onDone = null } = {}) {
  const records = Array.isArray(record) ? record : [record];
  const kind = recordKind(records[0]);
  const reason = area('withdraw-reason', 'reason', 3);
  const said = saidBy(records, 'withdraw');
  const { form } = dialogForm({
    fields: [
      h('p', {}, h('strong', {}, `Withdraw ${plural(records.length, kind)}: ${records.length === 1 ? 'it' : 'they'} should not be relied on.`), ' Nothing is deleted. The record stays, marked withdrawn, with this reason.'),
      h('ul', { class: 'plain' }, records.map((item) => h('li', { dataset: { withdrawing: item.id } }, `#${item.id}: ${clip(kind === 'decision' ? item.title : item.text, 120)}`))),
      field('Why it is withdrawn', reason),
      said.element,
    ],
    submit: 'Withdraw',
    work: async () => {
      const result =
        kind === 'decision'
          ? await call('withdraw_decision', { actor: actor(), decision_id: records[0].id, reason: reason.value, ...said.value() })
          : await call('withdraw_fact', { actor: actor(), fact_ids: records.map((item) => item.id), reason: reason.value, ...said.value() });
      box.close();
      notify(`Withdrew ${kind} ${records.map((item) => `#${item.id}`).join(', ')}.`);
      await sync();
      onDone?.(result);
    },
  });
  const box = openDialog(`Withdraw ${plural(records.length, kind)}`, [form], { wide: true });
  reason.focus();
  return box;
}

// -- what a record concerns ----------------------------------------------------------------------------

// Adds and takes out what a fact or a decision concerns. The dialog stays open, so that several
// links can be changed, and reads the record again after each change.
export async function linkDialog(record) {
  const kind = recordKind(record);
  await titlesIfPossible();
  const tools = kind === 'decision' ? { add: 'link_decision', remove: 'unlink_decision', get: 'get_decision', key: 'decision_id' } : { add: 'link_fact', remove: 'unlink_fact', get: 'get_fact', key: 'fact_id' };
  let current = record;
  const links = picker({ label: 'Add what it concerns', kinds: kind === 'decision' ? ['node', 'rule'] : ['node', 'rule', 'decision'] });
  const shown = h('ul', { class: 'plain concerns-list' });
  const problem = h('p', { class: 'problem', role: 'alert' });
  const name = (ref) => {
    if (ref.type === 'node') return `#${ref.id} ${titleOf(ref.id)}`;
    const list = (ref.type === 'rule' ? store.rules : store.decisions) || [];
    const item = list.find((entry) => entry.id === ref.id);
    return `${ref.type === 'rule' ? 'requirement' : 'decision'} #${ref.id}${item ? ` ${clip(item.title, 60)}` : ''}`;
  };
  const kindOf = (ref) => (ref.type === 'node' ? store.nodes.get(ref.id)?.kind || 'story' : ref.type);
  const draw = () => {
    const refs = refsOf(current);
    shown.replaceChildren(
      ...(refs.length
        ? refs.map((ref) =>
            h(
              'li',
              { dataset: { ref: `${ref.type}:${ref.id}` } },
              kindIcon(kindOf(ref), 16),
              h('span', { class: 'concern-name' }, name(ref)),
              h('button', { type: 'button', class: 'unlink small', 'aria-label': `Take out ${name(ref)}`, onclick: () => change(tools.remove, { [`${ref.type}_ids`]: [ref.id] }) }, 'Take out'),
            ),
          )
        : [h('li', { class: 'quiet' }, 'It concerns nothing yet.')]),
    );
  };
  const change = async (tool, ids) => {
    problem.replaceChildren();
    if (!Object.keys(ids).length) {
      refuse(problem, 'Choose what to add first: type an id such as #12, or pick from the list.');
      return;
    }
    try {
      await call(tool, { actor: actor(), [tools.key]: record.id, ...ids });
      notify(tool === tools.add ? 'Added what it concerns.' : 'Took it out.');
      current = await call(tools.get, { [tools.key]: record.id });
      if (tool === tools.add) links.reset();
      await sync();
      draw();
    } catch (error) {
      if (!(error instanceof Refusal)) throw error;
      refuse(problem, error.message);
    }
  };
  draw();
  const box = openDialog(`What ${kindWords(kind).label.toLowerCase()} #${record.id} concerns`, [
    h('p', { class: 'quiet' }, 'Recall finds it for these items. Only exact links count: nothing is inferred.'),
    shown,
    links.element,
    problem,
    h('div', { class: 'actions' }, h('button', { type: 'button', id: 'concerns-done', onclick: () => box.close() }, 'Done'), h('button', { type: 'button', class: 'primary', id: 'concerns-add', onclick: () => change(tools.add, links.value()) }, 'Add')),
  ]);
  box.querySelector('input[type=text]')?.focus();
  return box;
}

// -- confirming and un-confirming ----------------------------------------------------------------------

// `confirmation` is the state being chosen: confirmed asks for the person who reviewed it (the one
// typing, to start with) and, if wished, their words; unconfirmed asks for who says so and their
// words, both. `onDone(result)` runs after the server accepted it, `onClose` whenever the dialog
// goes (so that a dropdown can show the truth again).
export function confirmDialog(record, confirmation, { onDone = null, onClose = null } = {}) {
  const kind = recordKind(record);
  const confirming = confirmation === 'confirmed';
  const person = text('confirm-person', 'person', confirming ? actor() : '');
  const quote = area('confirm-quote', 'quote', 3);
  const label = kindWords(kind).label;
  const { form } = dialogForm({
    fields: [
      h('p', {}, h('strong', {}, `${label} #${record.id}: ${clip(kind === 'decision' ? record.title : record.text, 140)}`)),
      confirming
        ? h('p', { class: 'quiet' }, `Confirming says that a named person has reviewed this ${kind}. It does not mean Vrum checked that it is true. Your own name is recorded separately, as the one who made the change.`)
        : h('p', { class: 'quiet' }, `Un-confirming says that ${record.confirmed_by || 'the person who confirmed it'} no longer stands behind it, or never should have. Name the person who says so, and what they said. The earlier confirmation stays in its history.`),
      field(confirming ? 'The person who reviewed it' : 'The person who says so', person),
      field(confirming ? 'What they said, or where (optional)' : 'What they said, in their words', quote),
    ],
    submit: confirming ? 'Confirm' : 'Un-confirm',
    work: async () => {
      const made = await call('set_confirmation', {
        actor: actor(),
        kind,
        record_id: record.id,
        confirmation,
        person: person.value,
        ...(confirming && !quote.value.trim() ? {} : { quote: quote.value }),
      });
      box.close();
      notify(confirming ? `${label} #${record.id} is confirmed by ${person.value}.` : `${label} #${record.id} is unconfirmed again.`);
      await sync();
      onDone?.(made);
    },
  });
  const box = openDialog(`${confirming ? 'Confirm' : 'Un-confirm'} ${kind} #${record.id}`, [form]);
  if (onClose) box.addEventListener('close', onClose);
  person.focus();
  return box;
}

// -- writing a requirement ------------------------------------------------------------------------------

const REQUIREMENT_STATUSES = [
  ['held', 'Held'],
  ['settled', 'Settled'],
  ['dropped', 'Dropped'],
];

// Writes a requirement (a rule), or changes one. `story` is where a new one goes, to start with;
// `rule` is the one to change, at the version the person saw. `onDone(requirement)` runs after.
export function requirementDialog({ rule = null, story = null, onDone = null } = {}) {
  const title = text('requirement-title', 'title', rule?.title || '');
  const body = area('requirement-body', 'body', 6, rule?.body || '');
  const status = named(h('select', { name: 'status', value: rule?.status || 'held' }, REQUIREMENT_STATUSES.map(([value, label]) => h('option', { value }, label))), 'requirement-status');
  const where = named(h('select', { name: 'story' }, h('option', { value: '' }, 'Not in a story yet (place it later)'), parentChoices('task')), 'requirement-story');
  if (story !== null) where.value = String(story);
  const { form } = dialogForm({
    fields: [
      field('The requirement, stated briefly', title),
      field('Details, or where it came from', body),
      fieldRow(field('Status', status), rule ? null : field('Story', where)),
    ],
    submit: rule ? 'Save changes' : 'Create the requirement',
    work: async () => {
      let made;
      if (!rule) {
        made = await call('create_rule', { actor: actor(), title: title.value, body: body.value, status: status.value, story_id: where.value ? Number(where.value) : null });
        notify(`Created requirement #${made.id}, “${made.title}”.`);
      } else {
        const args = { actor: actor(), rule_id: rule.id, expected_version: rule.version };
        if (title.value !== rule.title) args.title = title.value;
        if (body.value !== (rule.body || '')) args.body = body.value;
        if (status.value !== rule.status) args.status = status.value;
        if (Object.keys(args).length === 3) throw new Refusal('Nothing has been changed yet, so there is nothing to save.');
        made = await call('update_rule', args);
        notify(`Saved requirement #${rule.id}.`);
      }
      box.close();
      await sync();
      onDone?.(made);
    },
  });
  const box = openDialog(rule ? `Edit requirement #${rule.id}` : 'New requirement', [form]);
  title.focus();
  return box;
}

// Puts a requirement that is in no story into one, or moves one from its story to another.
export function placeRequirementDialog(rule, { onDone = null } = {}) {
  const placing = rule.story_id === null || rule.story_id === undefined;
  const where = named(h('select', { name: 'story' }, parentChoices('task')), 'requirement-target');
  const here = placing ? 'It is in no story yet.' : `It is in story #${rule.story_id} ${titleOf(rule.story_id)}.`;
  const { form } = dialogForm({
    fields: [
      h('p', {}, h('strong', {}, `Requirement #${rule.id}: ${rule.title}`)),
      h('p', { class: 'quiet' }, `${here} A requirement is in at most one story.${placing ? '' : ' Moving takes it from its story to another in one step.'}`),
      field('Story', where),
    ],
    submit: placing ? 'Put in the story' : 'Move to the story',
    work: async () => {
      if (!where.value) throw new Refusal('Choose the story first.');
      const made = await call(placing ? 'place_rule' : 'move_rule', {
        actor: actor(),
        rule_id: rule.id,
        expected_version: rule.version,
        ...(placing ? { story_id: Number(where.value) } : { new_story_id: Number(where.value) }),
      });
      box.close();
      notify(`${placing ? 'Put' : 'Moved'} requirement #${rule.id} ${placing ? 'in' : 'to'} story #${where.value}.`);
      await sync();
      onDone?.(made);
    },
  });
  const box = openDialog(placing ? `Put requirement #${rule.id} in a story` : `Move requirement #${rule.id} to another story`, [form]);
  where.focus();
  return box;
}
