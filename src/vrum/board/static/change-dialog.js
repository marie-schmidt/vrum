// What pressing a red, green or changed line of a diff shows (M52): the screen the person would normally use for that work, read only, with
// what is new in it. A line a plan added opens the creation layout with every value new (a green `+`); a line a revision changed opens
// the edit layout with each unchanged field plain and each changed field as two rows, the old value red (`-`) and the new one green (`+`);
// a line a revision removed opens the same layout with every value red; a line of a suggested move opens the edit layout of that work with
// its parent as the old value and the new one; a note opens the layout of a recorded note. A field with nothing in it is not drawn, so a
// default says nothing. The dialog changes nothing: its values are only words, the item's own page (the link in it) is where it is edited.
//
// What the dialog shows comes from the lines' own data (the plan's items, what a revision changed and what it was) or, for a moved item,
// from `get_node`; nothing is worked out here. The sign in the gutter is the meaning and the colour only helps.

import { call } from './api.js';
import { itemLink } from './ctx.js';
import { day, h, put } from './dom.js';
import { openDialog } from './forms.js';
import { icon } from './icons.js';
import { LINK, codeOf, kindIcon, kindLabel, noteCode, parseNodeRef, priorityChip, ruleCode, sizeChip } from './labels.js';
import { renderMarkdown } from './markdown.js';
import { store } from './store.js';

const HEADING = { added: 'New', changed: 'Edit', removed: 'Removed', moved: 'Move' };
const SIGN_WORDS = {
  added: { '+': 'new' },
  removed: { '-': 'removed' },
  changed: { '-': 'was', '+': 'now' },
  moved: { '-': 'was', '+': 'now' },
};

// -- the words of one value --------------------------------------------------------------------------------------

const quiet = (words) => h('span', { class: 'quiet' }, words);
const textOf = (value) => h('span', { class: 'change-text', dataset: { words: 'theirs' } }, String(value));
const levelChip = (kind) => h('span', { class: 'chip level-chip', dataset: { level: kind } }, kindIcon(kind, 13), kindLabel(kind));

function markdown(value) {
  const body = renderMarkdown(String(value), { className: 'markdown change-markdown' });
  body.dataset.words = 'theirs';
  return body;
}

// An item named by its level, code and title. `value` is a reference, or `{ newEpic: title }` for the epic a move would make.
function itemWords(value, { lookup }) {
  if (value && typeof value === 'object') return h('span', { class: 'change-parent', dataset: { ref: '' } }, levelChip('epic'), ' ', textOf(value.newEpic));
  const found = parseNodeRef(value);
  const title = lookup(value);
  return h('span', { class: 'change-parent', dataset: { ref: value } }, found ? [levelChip(found.kind), ' '] : null, h('span', { class: 'item-code' }, codeOf(value)), title ? [' ', textOf(title)] : null);
}

const dayWords = (value) => h('span', { class: 'change-day' }, icon('calendar', { size: 14 }), day(value));

function linksWords(links, { lookup, typed }) {
  return h(
    'ul',
    { class: 'plain change-links' },
    links.map((one) =>
      h('li', { dataset: { type: one.type, target: one.target } }, typed ? [quiet(LINK[one.type]?.label ?? one.type), ' '] : null, h('span', { class: 'item-code' }, codeOf(one.target)), lookup(one.target) ? [' ', textOf(lookup(one.target))] : null),
    ),
  );
}

function criteriaWords(criteria) {
  return h(
    'ul',
    { class: 'plain change-criteria' },
    criteria.map((one) => h('li', { dataset: { rule: String(one.id) } }, icon('rule', { size: 14 }), ' ', h('span', { class: 'item-code' }, ruleCode(one.id)), ' ', textOf(one.title), String(one.body ?? '').trim() ? markdown(one.body) : null)),
  );
}

const blank = (value) => value === null || value === undefined || String(value).trim() === '';

// -- the layouts ---------------------------------------------------------------------------------------------------

// The creation and edit layout of an epic, story or task, in the order the forms have them. `empty` says a value says nothing (and is not
// drawn); `draw` makes its words; `label` is its label, which may depend on the work and on every value shown.
const ITEM_FIELDS = [
  { key: 'kind', label: () => 'Kind', empty: () => false, draw: (value) => h('span', { class: 'chip kind-chip', dataset: { kind: value } }, kindIcon(value, 14), kindLabel(value)) },
  { key: 'title', label: () => 'Title', empty: blank, draw: textOf },
  { key: 'body', label: () => 'Details', empty: blank, draw: markdown },
  { key: 'parent', label: (shape) => (shape.kind === 'task' ? 'In story' : 'In epic'), empty: (value) => !value, draw: itemWords },
  { key: 'size', label: () => 'Size', empty: (value) => !value, draw: (value, ctx) => sizeChip({ kind: ctx.shape.kind, size: value, progress_total: 0 }, { labelled: true }) ?? quiet(String(value)) },
  { key: 'priority', label: () => 'Priority', empty: (value) => !value || value === 'normal', draw: (value) => priorityChip(value) },
  { key: 'start_on', label: () => 'Start day', empty: blank, draw: dayWords },
  { key: 'target_on', label: () => 'Target day', empty: blank, draw: dayWords },
  { key: 'links', label: (shape, lists) => (lists.every((list) => list.every((one) => one.type === 'needs_first')) ? 'Needs first' : 'Links'), empty: (value) => !value.length, draw: linksWords },
  { key: 'criteria', label: () => 'Acceptance criteria', empty: (value) => !value.length, draw: criteriaWords },
];

// The layout of a recorded note.
const NOTE_FIELDS = [
  { key: 'text', label: () => 'What is known or decided', empty: blank, draw: (value) => h('div', { class: 'note-text change-note-text', dataset: { words: 'theirs' } }, value) },
  { key: 'why', label: () => 'Why', empty: blank, draw: textOf },
  { key: 'decided', label: () => 'Decided by', empty: (value) => !value, draw: (value) => textOf(`${value.by}${value.on ? `, ${day(value.on)}` : ''}`) },
  { key: 'project_wide', label: () => 'Project-wide', empty: (value) => !value, draw: () => h('span', { class: 'chip', dataset: { projectWide: 'true' } }, 'Project-wide') },
  {
    key: 'concerns',
    label: () => 'It applies to',
    empty: (value) => !value.length,
    draw: (value, { lookup }) => h('ul', { class: 'plain change-links' }, value.map((one) => h('li', { dataset: { target: one.ref } }, h('span', { class: 'item-code' }, one.code), lookup(one.ref) ? [' ', textOf(lookup(one.ref))] : null))),
  },
];

// -- the shapes the dialog is given ---------------------------------------------------------------------------------

// The values of an item as a plan lists them (`list_plans`: ref, kind, title, body, priority, size, start_on, target_on, parent, links,
// criteria).
export const shapeOfItem = (one) => ({
  kind: one.kind,
  title: one.title,
  body: one.body ?? '',
  parent: one.parent ?? null,
  size: one.size ?? null,
  priority: one.priority ?? 'normal',
  start_on: one.start_on ?? null,
  target_on: one.target_on ?? null,
  links: (one.links ?? []).map((link) => ({ type: link.link_type, target: link.target })),
  criteria: (one.criteria ?? []).map((criterion) => ({ id: criterion.id, title: criterion.title, body: criterion.body ?? '' })),
});

// What `get_node` says, which has no links and no criteria of its own to show.
const shapeOfNode = (node) => ({ ...shapeOfItem(node), links: [], criteria: [] });

// The parts of an item as a revision compared them, as they were: a link as "type ref", a criterion as [id, title, body].
function wasValue(key, value) {
  if (key === 'links') return value.map((text) => ({ type: String(text).split(' ')[0], target: String(text).split(' ')[1] }));
  if (key === 'criteria') return value.map(([id, title, body]) => ({ id, title, body }));
  return value;
}

// The titles of the items a plan names, then of what the board knows.
export function lookupOf(...lists) {
  const found = new Map();
  for (const list of lists) for (const one of list || []) if (!found.has(one.ref)) found.set(one.ref, one.title);
  return (ref) => found.get(ref) ?? store.nodes.get(ref)?.title ?? '';
}

const noneLookup = (ref) => store.nodes.get(ref)?.title ?? '';

// A line a plan added or a revision removed: every value of the item is new, or every value is gone.
export const itemSpec = (mode, one, { lookup = noneLookup } = {}) => ({ mode, ref: one.ref, kind: one.kind, shape: shapeOfItem(one), lookup });

// A line a revision changed: the item as it is now and each part that changed as it was.
export function changedSpec(one, entry, { lookup = noneLookup } = {}) {
  const was = {};
  for (const key of entry.fields) was[key] = wasValue(key, entry.before[key]);
  return { mode: 'changed', ref: one.ref, kind: one.kind, shape: shapeOfItem(one), changed: [...entry.fields], was, lookup };
}

// A line of a suggested move: the item with its parent as it was and as the move would leave it. The item is read when the dialog opens.
export function moveSpec(one, move) {
  const parent = move.parent ?? { newEpic: move.new_epic?.title ?? '' };
  return {
    mode: 'moved',
    ref: one.ref,
    kind: parseNodeRef(one.ref)?.kind ?? 'story',
    changed: ['parent'],
    was: { parent: one.from_parent ?? null },
    lookup: noneLookup,
    load: async () => ({ ...shapeOfNode(await call('get_node', { node: one.ref })), parent }),
  };
}

// The line of the epic a suggested move would make: it has no number yet, so it has no page.
export const newEpicSpec = (epic) => ({ mode: 'added', ref: null, kind: 'epic', shape: shapeOfItem({ kind: 'epic', title: epic.title, body: epic.body ?? '' }), lookup: noneLookup });

// A note, as a record of its own.
export function noteSpec(note) {
  const named = (ref, code) => ({ ref, code });
  return {
    mode: 'added',
    ref: `note:${note.id}`,
    kind: 'note',
    fields: NOTE_FIELDS,
    shape: {
      text: note.text,
      why: note.why,
      decided: note.decided_by ? { by: note.decided_by, on: note.decided_on } : null,
      project_wide: Boolean(note.project_wide),
      concerns: [...note.nodes.map((ref) => named(ref, codeOf(ref))), ...note.rule_ids.map((id) => named(`rule:${id}`, ruleCode(id))), ...note.note_ids.map((id) => named(`note:${id}`, noteCode(id)))],
    },
    lookup: noneLookup,
  };
}

// -- the dialog ---------------------------------------------------------------------------------------------------------

// The code of what a line is about: an epic, story or task, a note, or (for the epic a move would make) nothing yet.
const codeFor = (spec) => (!spec.ref ? null : spec.ref.startsWith('note:') ? noteCode(Number(spec.ref.slice(5))) : codeOf(spec.ref));

// The words an assistive technology hears for a line that opens this dialog: "Show T-17 as new".
export function openLabel(spec) {
  const code = codeFor(spec) ?? 'the new epic';
  if (spec.mode === 'added') return spec.ref ? `Show ${code} as new` : 'Show the new epic';
  if (spec.mode === 'removed') return `Show ${code} as removed`;
  if (spec.mode === 'moved') return `Show ${code} move`;
  return `Show ${code} changes`;
}

// The rows of one field: its label, and one value, or two (the old red, the new green) when it changed.
function fieldRow(definition, { mode, shape, was, changed, lookup }) {
  const now = shape[definition.key];
  const isChanged = changed.includes(definition.key);
  const none = (value) => (definition.key === 'priority' ? !value : definition.empty(value));
  let shown;
  let state;
  if (mode === 'added' || mode === 'removed') {
    if (definition.empty(now)) return null;
    shown = [[mode === 'added' ? '+' : '-', now]];
    state = mode === 'added' ? 'new' : 'removed';
  } else if (isChanged) {
    shown = [['-', was[definition.key]], ['+', now]];
    state = 'changed';
  } else {
    if (definition.empty(now)) return null;
    shown = [[null, now]];
    state = 'same';
  }
  const label = definition.label(shape, shown.map(([, value]) => value));
  const ctx = { shape, lookup, typed: label !== 'Needs first' };
  return h(
    'div',
    { class: 'change-field', dataset: { field: definition.key, state } },
    h('dt', { class: 'change-label' }, label),
    h(
      'dd',
      { class: 'change-values' },
      shown.map(([sign, value]) =>
        h(
          'div',
          { class: 'change-value', dataset: { sign } },
          sign ? h('span', { class: 'change-sign', 'aria-hidden': 'true' }, sign) : null,
          h('div', { class: 'change-body' }, sign ? h('span', { class: 'sr-only' }, `${SIGN_WORDS[mode][sign]}: `) : null, none(value) ? quiet('none') : definition.draw(value, ctx)),
        ),
      ),
    ),
  );
}

function fieldsOf(spec) {
  const fields = spec.fields ?? ITEM_FIELDS;
  const given = { mode: spec.mode, shape: spec.shape, was: spec.was ?? {}, changed: spec.changed ?? [], lookup: spec.lookup };
  return h('dl', { class: 'change-fields' }, fields.map((definition) => fieldRow(definition, given)));
}

const problemOf = (words) => h('p', { class: 'diff-problem', role: 'alert' }, icon('alert', { size: 16 }), h('span', {}, words));

// Opens the dialog for a line. `spec` is one of the specs above; `trigger` is the line, which gets the focus back when the dialog closes.
export function openChange(spec, trigger = null) {
  const code = codeFor(spec);
  const body = h('div', { class: 'change-dialog-body' });
  const about = h('p', { class: 'change-dialog-about' }, kindIcon(spec.kind, 16), code ? h('span', { class: 'item-code' }, code) : null);
  let leaving = false;
  const open = spec.ref ? itemLink(spec.ref, { class: 'action-link small', id: 'change-open' }, `Open ${code}`) : null;
  const close = h('button', { type: 'button', class: 'small', id: 'change-close', autofocus: true, onclick: () => box.close() }, 'Close');
  // A heading is words only: what it is and what kind of work.
  const box = openDialog(`${HEADING[spec.mode]} ${spec.kind}`, [about, body, h('div', { class: 'actions' }, open, close)]);
  box.id = 'change-dialog';
  box.classList.add('change-dialog');
  box.dataset.mode = spec.mode;
  box.dataset.kind = spec.kind;
  if (spec.ref) box.dataset.ref = spec.ref;
  // Going to the item's page leaves the dialog behind, and the focus is the page's.
  open?.addEventListener('click', (event) => {
    if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    leaving = true;
    box.close();
  });
  box.addEventListener('close', () => {
    if (leaving) return;
    const key = trigger?.dataset?.focusKey;
    const back = trigger?.isConnected ? trigger : key ? document.querySelector(`[data-focus-key="${CSS.escape(key)}"]`) : null;
    back?.focus();
  });
  if (!spec.load) {
    put(body, fieldsOf(spec));
    return box;
  }
  put(body, h('p', { class: 'loading quiet' }, 'Reading…'));
  spec
    .load()
    .then((shape) => {
      if (box.isConnected) put(body, fieldsOf({ ...spec, shape }));
    })
    .catch((error) => {
      if (box.isConnected) put(body, problemOf(error?.message ?? 'It could not be read.'));
    });
  return box;
}

// What a diff line needs to open this dialog: the words of its label and what to do when it is pressed.
export const opening = (spec) => ({ label: openLabel(spec), open: (line) => openChange(spec, line) });
