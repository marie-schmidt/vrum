// Notes and acceptance criteria as the board shows them. This is where other pages get the operations on
// them (`openRecord`, the dialogs, `confirmationNote`), and where the page of a
// acceptance criterion or a note is drawn into the item page: its status, all its fields, what it concerns, who
// mentions it and its history. Every change is the operation an
// agent would call, at the version the person saw, so a change made meanwhile is refused in the
// same words and nothing typed is lost.

import { Refusal, call, callAll } from './api.js';
import { can, notify, openItem } from './ctx.js';
import { clear, day, h, moment, put } from './dom.js';
import { concernsTable, confirmationWords, lifecycleLines, noteStatusChip, projectWideChip, recordTitle, replacementRows, replacementTable, ruleStatusChip } from './entries.js';
import { describe, whatChanged } from './events.js';
import { clip, dialogForm, field as formField, openDialog, refuse } from './forms.js';
import { icon } from './icons.js';
import { REQUIREMENT_STATUS, REQUIREMENT_STATUSES, actorLabel, inForce, kindIcon, noteCode, ruleCode } from './labels.js';
import { renderMarkdown } from './markdown.js';
import { askRecall, askSimilar, knownRefs, recallBlock, recallTotal, similarSection } from './recall.js';
import { addNoteDialog, linkDialog, placeRequirementDialog, requirementDialog, supersedeDialog, withdrawDialog } from './record-forms.js';
import { withReferences } from './reference-picker.js';
import { fetchMentions, loadTargets, mentionsList } from './references.js';
import { store, sync } from './store.js';

export { addNoteDialog, linkDialog, placeRequirementDialog, requirementDialog, ruleStatusChip, supersedeDialog, withdrawDialog };

// -- what other pages call ---------------------------------------------------------------------------

// Goes to the page of one record, in whatever page the person is. `kind` is the real kind (epic,
// story, task, rule or note) and `number` its number.
export function openRecord(kind, number) {
  openItem(`${kind}:${number}`);
}

// What a note says about its confirmation: who confirmed it and when (or who took it back) and what the person said. The status chip
// beside the title is what changes it (status-menu.js): its menu opens the dialog that asks who reviewed it and, to
// un-confirm, their words. `history` is the note's events, which hold those words.
function confirmationNote(record, history = []) {
  const active = inForce(record);
  const last = [...history].reverse().find((event) => event.action === 'note_confirmed' || event.action === 'note_unconfirmed') || null;
  const words = last?.after?.quote;
  const note = h('p', { class: 'quiet confirmation-note' }, confirmationWords(record, last));
  const said = words ? h('div', { class: 'confirmation-said' }, h('span', { class: 'quiet' }, last.action === 'note_confirmed' ? 'What they said' : 'What was said'), h('blockquote', {}, words)) : null;
  return h('div', { class: 'confirmation-control', dataset: { record: `note:${record.id}`, status: record.status, active: String(active) } }, note, said);
}

// -- pieces the record pages share ---------------------------------------------------------------------

// A section of a page: a heading, and what it holds.
export const section = (title, key, ...content) => h('section', { class: 'part', dataset: { part: key } }, h('h3', {}, title), content);

// The same, with what can be done to it in the row of the heading (the button that adds a note,
// say).
export const sectionWith = (title, key, actions, ...content) =>
  h('section', { class: 'part', dataset: { part: key } }, h('div', { class: 'part-head' }, h('h3', {}, title), actions), content);

// Whether the details have any words: only a blank is empty.
export const hasDetails = (body) => Boolean(body && body.trim());

// An empty field: a dashed box that says Empty, with the way to fill it (`actions`: a button or a link,
// none for a person who cannot change it). Every field that can be empty is drawn this way, so that an
// empty one is as plain to see as a full one.
export const emptyField = (...actions) => h('div', { class: 'details-empty', dataset: { empty: 'true' } }, h('span', { class: 'chip empty-chip' }, 'Empty'), actions);

// The details of an item or an acceptance criterion: what is written, read as Markdown; while `editing`, the
// form is in its place and the text is not drawn twice. The Edit button is in the page's header. The body
// of the page needs no heading of its own.
// With nothing written the section is an empty field, with a button that opens the editor when `onAdd`
// is given (for a person who can change it).
export function detailsSection({ body, form, editing, extras = [], onAdd = null }) {
  const read = hasDetails(body)
    ? h('div', { class: 'body-text' }, renderMarkdown(body))
    : emptyField(onAdd ? h('button', { type: 'button', class: 'small add-details', onclick: onAdd }, icon('plus', { size: 14 }), 'Add details') : null);
  return h('section', { class: 'part details-part', dataset: { part: 'edit' } }, h('h3', { class: 'sr-only' }, 'Details'), editing ? form : read, ...extras);
}

// The words of an Edit button that is in a page's header.
export const editButton = (onclick, id = null) => h('button', { type: 'button', class: 'edit-details', id, 'aria-label': 'Edit details', onclick }, 'Edit');

// Holds the details section of an item or an acceptance criterion. `build(editing)` makes it. Starting or
// ending an edit draws only that section again, asking the server nothing and leaving the rest of
// the page as it is, and puts the focus where the person goes next (the first field, or back on
// the Edit button). `after(editing)` is for whatever else changes with it, such as the header. `set`
// takes the selector of the field to put the focus in when it is not the first one.
export function detailsHolder(state, build, after = () => {}) {
  let current = build(Boolean(state.editing));
  return {
    get element() {
      return current;
    },
    set(editing, focus = 'input[type="text"]') {
      state.editing = editing;
      const fresh = build(editing);
      current.replaceWith(fresh);
      current = fresh;
      after(editing);
      (editing ? fresh.querySelector(focus) : document.querySelector('.edit-details'))?.focus();
    },
  };
}

// A heading with how many there are, as a quiet number after it: "Notes 13". Nothing is added to a heading with none.
export const countTitle = (title, count) => [title, count ? h('span', { class: 'heading-count' }, String(count)) : null];

// The properties of an item as a row of small groups, each with its label over its value, for the head
// of a page, where there is room to read them across. A group whose value is empty is left out.
// A row may carry `hideLabel` for a group whose value says all (the label is then for a screen reader).
export function stateStrip(rows) {
  const shown = rows.filter((row) => row && row[1] !== null && row[1] !== undefined && row[1] !== false);
  if (!shown.length) return null;
  return h(
    'dl',
    { class: 'state-strip' },
    shown.map(([label, value, { hideLabel = false, ...props } = {}]) => h('div', { class: 'state-item' }, h('dt', { class: hideLabel ? 'sr-only' : null }, label), h('dd', props, value))),
  );
}

// What a page says when it has only something to say: that the server said no, or that the item is
// gone.
export function stateView(message, { refusal = false } = {}) {
  return h(
    'div',
    { class: 'detail-state' },
    refusal ? h('p', { class: 'notice refusal', role: 'alert' }, icon('alert', { size: 16 }), h('span', {}, message)) : h('p', { class: 'state-text' }, message),
  );
}

export const loadingView = () => h('div', { class: 'detail-state' }, h('p', { class: 'loading', role: 'status' }, 'Loading…'));

const button = (label, onclick, cls = '', mark = null) => h('button', { type: 'button', class: `small ${cls}`.trim(), onclick }, mark ? icon(mark, { size: 14 }) : null, label);
// The buttons in a page's header are the size of its Edit button, not the small ones of a section.
const headerButton = (label, onclick, cls = '') => h('button', { type: 'button', class: cls, onclick }, label);

// Whatever has the focus inside the page when it is drawn again gets it back afterwards, so a
// keyboard user is not thrown out by news from the server. Call it before the old content goes
// and call what it returns once the new content is in.
export function rememberFocus(root) {
  const element = document.activeElement;
  let key = null;
  if (element && element !== root && root.contains(element)) {
    if (element.dataset.focusKey) key = `[data-focus-key="${CSS.escape(element.dataset.focusKey)}"]`;
    else if (element.id && !/^(confirmation-|f|similar-)\d+$/.test(element.id)) key = `#${CSS.escape(element.id)}`;
    else if (element.classList.length) key = `${element.localName}.${CSS.escape(element.classList[0])}`;
  }
  return () => {
    if (key) root.querySelector(key)?.focus();
  };
}

// Anything typed or chosen on the page from now on is not replaced by a fresh copy of it.
export function watchEdits(view, state) {
  const touch = () => {
    state.dirty = true;
    state.touches = (state.touches || 0) + 1;
  };
  view.addEventListener('input', touch);
  view.addEventListener('change', touch);
}

// Say what the server said and, when the item is no longer the one the person read, offer the
// current version. What was typed stays where it is until they choose. `currentVersion` reads the
// version the server holds now.
export async function explainStale(error, place, state, currentVersion) {
  refuse(place, error.message);
  place.querySelector('.problem-text').classList.add('refusal-text');
  const now = await currentVersion().catch(() => null);
  if (now !== null && now !== state.version) {
    place.append(
      h(
        'button',
        {
          type: 'button',
          class: 'link load-current',
          onclick: () => {
            state.dirty = false;
            state.reload(true);
          },
        },
        `Load the current version (${now}), and lose what I typed`,
      ),
    );
  }
}

// What happened to one item, newest first, in plain words (the words are the Activity page's). A
// change that the sentence cannot say whole (the details, a long title) has "Show what changed",
// with the values before and after in full. When only the newest ten are drawn, the row that
// made the item is still drawn, last. `opened` is the set of event ids whose disclosure is open,
// kept by the page so that it stays open when the page is drawn again.
export function historySection(events, { opened = new Set() } = {}) {
  const newest = [...events].reverse();
  const row = (event) =>
    h(
      'li',
      { dataset: { event: event.id, action: event.action } },
      h('div', { class: 'history-what' }, h('strong', {}, actorLabel(event.actor, event.actor_kind)), ' ', h('span', { class: 'event-words' }, describe(event), '.'), event.after && event.after.version !== undefined ? h('span', { class: 'quiet' }, ` Version ${event.after.version}.`) : null),
      h('div', { class: 'history-when quiet' }, moment(event.at)),
      whatChanged(event, { open: opened.has(event.id), onToggle: (now) => (now ? opened.add(event.id) : opened.delete(event.id)) }),
      event.action === 'handoff' && event.after?.note ? h('div', { class: 'handoff-note' }, renderMarkdown(event.after.note)) : null,
    );
  const cut = newest.length > 10;
  const first = newest[newest.length - 1];
  const shown = cut ? [...newest.slice(0, 10), first] : newest;
  const list = h('ul', { class: 'plain history' }, shown.map(row));
  if (cut) list.lastElementChild.classList.add('history-after-gap');
  const more = cut
    ? h(
        'button',
        {
          type: 'button',
          class: 'small show-all-history',
          onclick: (event) => {
            list.replaceChildren(...newest.map(row));
            event.currentTarget.remove();
          },
        },
        `Show all ${newest.length}`,
      )
    : null;
  return section(countTitle('History', events.length), 'history', list, more);
}

// "Release" a claim the person holds, with the note whoever takes the work next will read: the same
// operation as on the Activity page. `work` is { node, kind, title }; `done` runs after the server
// said yes and the board has heard of it.
export function releaseDialog(work, done = async () => {}) {
  const note = h('textarea', { id: 'release-note', name: 'note', rows: 4 });
  const { form } = dialogForm({
    fields: [h('p', {}, `Release “${work.title}”.`), formField('Handoff note', note, 'What was done, how far it got, and what is left.')],
    submit: 'Release it',
    work: async () => {
      await call('release_claim', { node: work.node, note: note.value });
      form.closest('dialog')?.close();
      notify(`Released “${work.title}”.`);
      await sync();
      await done();
    },
  });
  openDialog('Release a claim', [form]);
  note.focus();
}

// "Add a note" about whatever is open: the dialog starts with it chosen.
function recordButtons(concern) {
  if (!can('write')) return null;
  return h('div', { class: 'actions-row record-buttons' }, button('Add a note…', () => addNoteDialog(concern), 'add-note', 'plus'));
}

// What is known about whatever is open: the button that adds to it is in the heading's row, and
// `recalled` (the recall block) is what the server says. With no note and no way to add one, there is no section.
export function contextSection(concern, recalled, count = 0) {
  const add = recordButtons(concern);
  if (!count && !add) return null;
  return sectionWith(countTitle('Notes', count), 'context', add, count ? recalled : null);
}

// Who mentions it: a section only when somebody does.
export const mentionsSection = (mentions) => (mentions.length ? section(countTitle('Mentioned in', mentions.length), 'mentions', mentionsList(mentions)) : null);

// The page's own title: the kind's icon, its code and the words.
export const pageTitle = (kind, code, words) => [kindIcon(kind, 22), h('span', { class: 'item-code' }, code), ' ', words];

// -- a note ----------------------------------------------------------------------------------------------------

// Replace and withdraw: what can be done to a note that is in force (what it concerns is changed
// in its own section).
function recordActions(record, state) {
  if (!inForce(record) || !can('write')) return null;
  const done = () => state.reload(true);
  return [headerButton('Replace…', () => supersedeDialog(record, { onDone: done }), 'supersede'), headerButton('Withdraw…', () => withdrawDialog(record, { onDone: done }), 'withdraw')];
}

// A note's first line is its title; the rest of its text is the card's "What it says". When that line is too long, or
// has lines, emphasis, a link or a reference to show, the title is cut and the card has the whole text, so that
// nothing is left out.
const sayableAsTitle = (text) => clip(text, 140) === text.trim() && !/[\n*_`[\]~<>#]|https?:\/\//.test(text);
function splitText(text) {
  const lines = String(text).split('\n');
  const at = Math.max(0, lines.findIndex((line) => line.trim() !== ''));
  const first = lines[at] ?? '';
  return sayableAsTitle(first) ? { title: first.trim(), rest: lines.slice(at + 1).join('\n').trim() } : { title: null, rest: text };
}

// Fills `root` with a note. The page's own places are in `options`: `title` (the heading), `summary` (its
// status and who decided it), `actions` (what can be done to it).
export async function renderNote(root, id, state = { dirty: false }, options = {}) {
  state.skipped = false;
  state.reload = () => renderNote(root, id, state, options);
  if (!root.querySelector('.record-detail')) put(root, loadingView());
  const [record, history, mentions, similar] = await Promise.all([call('get_note', { note_id: id }), callAll('note_history', { note_id: id }), fetchMentions('note', id), askSimilar({ note_id: id }), loadTargets()]);
  // The page has moved on to something else while this was being fetched.
  if (state.dead) return state;
  state.version = null;
  state.dirty = false;
  // What it concerns, so that news about one of those (a rename, say) draws the page again.
  state.concerns = { node: record.nodes || [], rule: record.rule_ids || [], note: record.note_ids || [] };
  const said = splitText(record.text);
  const active = inForce(record);
  const concernCount = (record.project_wide ? 1 : 0) + state.concerns.node.length + state.concerns.rule.length + state.concerns.note.length;
  const replaces = replacementRows(record);
  // What the person has already been told about is not suggested: what it concerns, and what it replaced or was replaced by.
  const known = new Set([...state.concerns.node, ...state.concerns.rule.map((one) => `rule:${one}`), ...state.concerns.note.map((one) => `note:${one}`), ...replaces.map((row) => `note:${row.id}`)]);
  // Only what has something to say about who stood behind it: a confirmed note, one that ended after being confirmed,
  // and one whose confirmation was taken back. An unconfirmed note is told by its chip alone.
  const confirmation = Boolean(record.confirmed_by) || history.some((event) => event.action === 'note_unconfirmed');

  put(options.title, pageTitle('note', noteCode(record.id), said.title || recordTitle(record)));
  // A chip that was just used keeps the focus when the header is drawn again.
  const restoreChips = rememberFocus(options.summary);
  put(
    options.summary,
    stateStrip([
      ['Status', noteStatusChip(record, { onChanged: () => state.reload() }), { class: 'status' }],
      ['Scope', projectWideChip(record)],
      ['Decided by', record.decided_by ? `${record.decided_by}${record.decided_on ? ` on ${day(record.decided_on)}` : ''}` : null, { class: 'decided' }],
    ]),
  );
  restoreChips();
  put(options.actions, recordActions(record, state));

  const ended = active
    ? null
    : h(
        'section',
        { class: 'part lifecycle-note', dataset: { part: 'lifecycle' } },
        h('h3', {}, record.status === 'withdrawn' ? 'Withdrawn' : 'Replaced'),
        ...lifecycleLines(record, { replaces: false }),
        h('p', { class: 'quiet', id: 'record-ended' }, 'It is no longer in force, so it cannot be replaced, withdrawn or changed. Everything that happened to it is in its history.'),
      );
  const main = h(
    'div',
    { class: 'card-main' },
    said.rest ? section('What it says', 'record', renderMarkdown(said.rest, { className: 'markdown record-text' })) : null,
    section('Why', 'why', h('div', { class: 'reason' }, renderMarkdown(record.why, { className: 'markdown record-why' }))),
  );
  const changeConcerns = active && can('write') ? button('Change what it applies to…', () => linkDialog(record), 'change-concerns', 'link') : null;
  const view = h(
    'article',
    { class: 'record-detail card-page', dataset: { record: `note:${record.id}`, kind: 'note', status: record.status, projectWide: String(Boolean(record.project_wide)) } },
    ended,
    confirmation
      ? h('div', { class: 'card-columns' }, main, h('div', { class: 'card-side' }, h('section', { class: 'part', dataset: { part: 'confirmation' } }, h('h3', {}, 'Confirmation'), confirmationNote(record, history))))
      : main,
    concernCount || changeConcerns ? sectionWith(countTitle('Applies to', concernCount), 'concerns', changeConcerns, concernCount ? concernsTable(record, { caption: 'What it applies to' }).element : null) : null,
    replaces.length ? section(countTitle('Replaces and replaced by', replaces.length), 'replaces', replacementTable(record, { caption: 'What it replaced and what replaced it' }).element) : null,
    mentionsSection(mentions),
    similarSection(similar, known),
    historySection(history, { opened: (state.opened = state.opened || new Set()) }),
  );
  const restoreFocus = rememberFocus(root);
  clear(root).append(view);
  restoreFocus();
  state.focus = () => document.getElementById('item-title')?.focus();
  return state;
}

// -- an acceptance criterion ----------------------------------------------------------------------------------

function ruleForm(rule, state, cancel) {
  const title = h('input', { id: 'edit-title', type: 'text', value: rule.title, autocomplete: 'off' });
  const body = withReferences(h('textarea', { id: 'edit-body', rows: 6, value: rule.body || '' }));
  const status = h('select', { id: 'edit-status', value: rule.status }, REQUIREMENT_STATUSES.map((value) => h('option', { value }, REQUIREMENT_STATUS[value].label)));
  const problem = h('div', { class: 'problem', role: 'alert' });
  const field = (label, input, id, wide = true) => h('div', { class: `field${wide ? ' wide' : ''}` }, h('label', { for: id }, label), input);
  return h(
    'form',
    {
      class: 'edit form-grid',
      onsubmit: async (event) => {
        event.preventDefault();
        problem.replaceChildren();
        const args = { rule_id: rule.id, expected_version: state.version };
        if (title.value !== rule.title) args.title = title.value;
        if (body.value !== (rule.body || '')) args.body = body.value;
        if (status.value !== rule.status) args.status = status.value;
        if (Object.keys(args).length === 2) {
          refuse(problem, 'Nothing has been changed yet, so there is nothing to save.');
          return;
        }
        try {
          await call('update_rule', args);
          state.dirty = false;
          state.editing = false;
          notify(`Saved “${args.title ?? rule.title}”.`);
          await sync();
          await state.reload(true);
        } catch (error) {
          if (!(error instanceof Refusal)) throw error;
          await explainStale(error, problem, state, async () => (await call('get_rule', { rule_id: rule.id })).version);
        }
      },
    },
    field('Title', title, 'edit-title'),
    field('Details', body, 'edit-body'),
    field('Status', status, 'edit-status', false),
    problem,
    h('div', { class: 'form-actions' }, h('button', { type: 'button', class: 'cancel-edit', onclick: cancel }, 'Cancel'), h('button', { type: 'submit', class: 'primary' }, 'Save changes')),
  );
}

// Fills `root` with an acceptance criterion (a rule); `options` are the page's places, as for a note. Its edit
// form is kept as typed while the page is refreshed, like an epic's, story's or task's.
export async function renderRule(root, id, state = { dirty: false }, options = {}, force = true) {
  const seen = state.touches || 0;
  state.skipped = false;
  state.open = state.open || {};
  state.reload = (now = false) => renderRule(root, id, state, options, now);
  if (!root.querySelector('.record-detail')) put(root, loadingView());
  const [rule, recalled, history, mentions, similar] = await Promise.all([
    call('get_rule', { rule_id: id }),
    askRecall({ rule_id: id }),
    callAll('rule_history', { rule_id: id }),
    fetchMentions('rule', id),
    askSimilar({ rule_id: id }),
    loadTargets(),
  ]);
  if (state.dead) return state;
  if (!force && (state.touches || 0) !== seen) {
    state.skipped = true;
    return state;
  }
  state.version = rule.version;
  state.dirty = false;
  const unplaced = rule.node === null || rule.node === undefined;
  const onNode = unplaced ? null : store.nodes.get(rule.node);
  const story = onNode && onNode.kind === 'task' ? store.nodes.get(onNode.parent) : onNode;
  // What the person has already been told about is not suggested: the story or task it is on, and what is above it.
  const known = knownRefs(recalled, [rule.node, onNode?.parent, story?.parent]);

  // On a narrow page the button keeps to its first word, so that the buttons of the header stay on one row.
  const placeWords = unplaced ? 'Put on a story or task…' : 'Move to another story or task…';
  const placeButton = can('write')
    ? h(
        'button',
        { type: 'button', class: 'place-requirement', 'aria-label': placeWords, title: placeWords, onclick: () => placeRequirementDialog(rule) },
        h('span', { class: 'long-label' }, placeWords),
        h('span', { class: 'short-label', 'aria-hidden': 'true' }, unplaced ? 'Put on…' : 'Move…'),
      )
    : null;
  const headerEdit = can('write') ? editButton(() => holder.set(true), 'edit-open') : null;
  const holder = detailsHolder(
    state,
    (editing) =>
      detailsSection({
        body: rule.body,
        form: ruleForm(rule, state, () => {
          state.dirty = false;
          holder.set(false);
        }),
        editing,
        onAdd: can('write') ? () => holder.set(true, '#edit-body') : null,
      }),
    (editing) => put(options.actions, editing ? null : [headerEdit, placeButton]),
  );

  put(options.title, pageTitle('rule', ruleCode(rule.id), rule.title));
  // What the chip changed is at a new version, which the form has to be saved at; the page is drawn again
  // unless something is typed in it.
  const changed = (result) => {
    state.version = result.version;
    if (!state.dirty) state.reload(true);
  };
  const restoreChips = rememberFocus(options.summary);
  // Its status is the whole of it: the story or task it is on is in the path above the page. One placed nowhere says so.
  put(
    options.summary,
    stateStrip([
      ['Status', ruleStatusChip(rule, { version: () => state.version, onChanged: changed }), { class: 'status' }],
      unplaced ? ['On', h('span', { class: 'quiet' }, 'Not placed yet'), { class: 'in-story' }] : null,
    ]),
  );
  restoreChips();
  put(options.actions, state.editing ? null : [headerEdit, placeButton]);

  const view = h(
    'article',
    { class: 'record-detail rule-detail card-page', dataset: { record: `rule:${rule.id}`, kind: 'rule', status: rule.status, node: rule.node ?? '' } },
    h('div', { class: 'card-main' }, holder.element),
    contextSection(
      { rule_ids: [rule.id] },
      recallBlock({ rule_id: id }, recalled, { open: state.open, onOpen: (name, now) => (state.open[name] = now) }),
      recallTotal(recalled),
    ),
    mentionsSection(mentions),
    similarSection(similar, known),
    historySection(history, { opened: (state.opened = state.opened || new Set()) }),
  );
  const restoreFocus = rememberFocus(root);
  clear(root).append(view);
  restoreFocus();
  watchEdits(view, state);
  state.focus = () => document.getElementById('item-title')?.focus();
  return state;
}
