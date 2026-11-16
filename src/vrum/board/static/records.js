// Facts, decisions and requirements as the board shows them. This is where other pages get the
// operations on them (`openRecord`, the dialogs, `confirmationControl`, `recordRow`), and where the
// shared detail panel draws a requirement, a decision or a fact: all its fields, its confirmation
// and lifecycle, what it concerns, who mentions it and its history. Every change is the operation
// an agent would call, at the version the person saw, so a change made meanwhile is refused in the
// same words and nothing typed is lost.

import { Refusal, call, callAll } from './api.js';
import { actor, notify, recordLink, ui } from './ctx.js';
import { clear, day, h, moment } from './dom.js';
import { concerns, confirmationWords, describedBy, isActive, lifecycleLines, recordBody, recordChips, recordKind, recordTitle } from './entries.js';
import { describe } from './events.js';
import { clip, uid } from './forms.js';
import { renderMarkdown } from './markdown.js';
import { recallBlock, askRecall } from './recall.js';
import { confirmDialog, linkDialog, placeRequirementDialog, recordDecisionDialog, recordFactDialog, requirementDialog, supersedeDialog, withdrawDialog } from './record-forms.js';
import { withReferences } from './reference-picker.js';
import { fetchMentions, itemLink, kindWords, loadTargets, mentionsList, statusWords } from './references.js';
import { sync } from './store.js';

export { linkDialog, placeRequirementDialog, recordDecisionDialog, recordFactDialog, requirementDialog, supersedeDialog, withdrawDialog };

// -- what other pages call ---------------------------------------------------------------------------

// Shows one item in the shared detail panel, on whatever page the person is on. `kind` is node,
// rule, decision or fact.
export function openRecord(kind, id) {
  ui.navigate({ sel: `${kind}:${id}`, panel: null });
}

// The Unconfirmed / Confirmed dropdown of a fact or a decision, with who confirmed it and when.
// Choosing another value asks for the person (and their words) in a dialog and calls the same
// operation an agent does; the dropdown shows the truth again if the dialog is left. It is
// disabled, with the reason, for a record that is no longer in force. `onDone(result)` runs after
// the server accepted a change.
export function confirmationControl(record, onDone = null) {
  const kind = recordKind(record);
  const current = record.confirmation === 'confirmed' ? 'confirmed' : 'unconfirmed';
  const id = uid('confirmation-');
  const active = isActive(record);
  const select = h(
    'select',
    { id, class: 'confirmation-select', name: 'confirmation', value: current, disabled: !active, 'aria-describedby': `${id}-note` },
    h('option', { value: 'unconfirmed' }, 'Unconfirmed'),
    h('option', { value: 'confirmed' }, 'Confirmed'),
  );
  select.addEventListener('change', () => {
    const wanted = select.value;
    if (wanted === current) return;
    confirmDialog(record, wanted, {
      onDone,
      onClose: () => {
        select.value = current;
      },
    });
  });
  const ended = record.status === 'superseded' ? 'replaced' : 'withdrawn';
  const note = h(
    'p',
    { class: 'quiet confirmation-note', id: `${id}-note` },
    confirmationWords(record),
    active ? '' : ` Only a record in force can be confirmed or un-confirmed: this one was ${ended}${current === 'confirmed' ? ', so it keeps the confirmation it had' : ''}.`,
  );
  return h('div', { class: 'confirmation-control', dataset: { record: `${kind}:${record.id}`, confirmation: current, active: String(active) } }, h('label', { for: id }, 'Confirmation'), ' ', select, note);
}

// One row of a list of facts and decisions: what it is, its title or text, whether a person
// confirmed it, and whether it is still in force (a replaced or withdrawn one is drawn as ended).
// `actions` and `choose` are for the page that lists them.
export function recordRow(record, { actions = null, choose = null } = {}) {
  const kind = recordKind(record);
  const description = describedBy(record);
  const second =
    kind === 'decision'
      ? `Decided by ${record.decided_by} on ${day(record.decided_on)}.`
      : `Source: ${clip(record.source || '', 90)}. Recorded by ${record.recorded_by}.`;
  return h(
    'li',
    {
      class: `record record-row ${kind}${isActive(record) ? '' : ' ended'}`,
      id: `record-${kind}-${record.id}`,
      'aria-describedby': description.id,
      dataset: { record: `${kind}:${record.id}`, kind, status: record.status, confirmation: record.confirmation === 'confirmed' ? 'confirmed' : 'unconfirmed', projectWide: String(Boolean(record.project_wide)) },
    },
    description.note,
    recordChips(record),
    recordLink(kind, record.id, { class: 'record-title' }, recordTitle(record)),
    h('p', { class: 'quiet record-second' }, second),
    choose,
    actions,
  );
}

// -- pieces the detail panel's pages share --------------------------------------------------------------

export const section = (title, key, ...content) => h('section', { class: 'part', dataset: { part: key } }, h('h3', {}, title), content);

export const closeButton = (label) => h('button', { type: 'button', class: 'close', 'aria-label': label, onclick: () => ui.navigate({ sel: null }) }, '✕');

const button = (label, onclick, cls = '') => h('button', { type: 'button', class: cls, onclick }, label);

// Whatever has the focus inside the panel when it is drawn again gets it back afterwards, so a
// keyboard user is not thrown out by news from the server. Call it before the old content goes
// and call what it returns once the new content is in.
export function rememberFocus(root) {
  const element = document.activeElement;
  let key = null;
  if (element && element !== root && root.contains(element)) {
    if (element.id && !/^(confirmation-|f)\d+$/.test(element.id)) key = `#${CSS.escape(element.id)}`;
    else if (element.classList.length) key = `${element.localName}.${CSS.escape(element.classList[0])}`;
  }
  return () => {
    if (key) root.querySelector(key)?.focus();
  };
}

// Anything typed or chosen in the panel from now on is not replaced by a fresh copy of it.
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
  place.replaceChildren(h('span', { class: 'refusal-text' }, error.message));
  const now = await currentVersion().catch(() => null);
  if (now !== null && now !== state.version) {
    place.append(
      ' ',
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

// What happened to one item, newest first, in plain words (the words are the Activity drawer's).
export function historySection(events) {
  const newest = [...events].reverse();
  const row = (event) =>
    h(
      'li',
      { dataset: { event: event.id, action: event.action } },
      h('span', { class: 'quiet' }, `${moment(event.at)} `),
      h('strong', {}, event.actor),
      ' ',
      h('span', { class: 'event-words' }, describe(event), '.'),
      event.after && event.after.version !== undefined ? h('span', { class: 'quiet' }, ` Version ${event.after.version}.`) : null,
      event.action === 'handoff' && event.after?.note ? h('div', { class: 'handoff-note' }, renderMarkdown(event.after.note)) : null,
    );
  const list = h('ul', { class: 'plain history' }, newest.slice(0, 10).map(row));
  const more =
    newest.length > 10
      ? h(
          'button',
          {
            type: 'button',
            class: 'link show-all-history',
            onclick: (event) => {
              list.replaceChildren(...newest.map(row));
              event.target.remove();
            },
          },
          `Show all ${newest.length}`,
        )
      : null;
  return section('History', 'history', list, more);
}

// A requirement in a list: its status, its title (which opens it in the panel) and what can be
// done to it. The version it is changed at is the one it was drawn with.
export function requirementRow(rule) {
  return h(
    'li',
    { class: `requirement status-${rule.status}`, dataset: { rule: rule.id, status: rule.status, story: rule.story_id ?? '' } },
    h(
      'div',
      { class: 'record-chips' },
      h('span', { class: 'chip kind kind-rule' }, h('span', { 'aria-hidden': 'true' }, kindWords('rule').sign), ` Requirement #${rule.id}`),
      h('span', { class: `chip rule-status status-${rule.status}` }, statusWords(rule.status)),
      recordLink('rule', rule.id, { class: 'record-title' }, rule.title),
    ),
    h(
      'div',
      { class: 'buttons' },
      button('Edit…', () => requirementDialog({ rule }), 'edit-requirement'),
      button(rule.story_id === null || rule.story_id === undefined ? 'Put in a story…' : 'Move to another story…', () => placeRequirementDialog(rule), 'place-requirement'),
    ),
  );
}

// "Record a fact" and "Record a decision" about whatever is open: the dialogs start with it chosen.
export function recordButtons(concern) {
  return h(
    'div',
    { class: 'buttons record-buttons' },
    button('Record a fact…', () => recordFactDialog(concern), 'record-fact'),
    button('Record a decision…', () => recordDecisionDialog(concern), 'record-decision'),
  );
}

// -- a fact or a decision ----------------------------------------------------------------------------------

const TOOLS = {
  fact: { get: 'get_fact', history: 'fact_history', key: 'fact_id' },
  decision: { get: 'get_decision', history: 'decision_history', key: 'decision_id' },
};

function recordActions(record, state) {
  if (!isActive(record)) {
    return h('p', { class: 'quiet', id: 'record-ended' }, 'It is no longer in force, so it cannot be replaced, withdrawn or changed. Everything that happened to it is in its history.');
  }
  const done = () => state.reload(true);
  return h(
    'div',
    { class: 'buttons record-actions' },
    button('Replace…', () => supersedeDialog(record, { onDone: done }), 'supersede'),
    button('Withdraw…', () => withdrawDialog(record, { onDone: done }), 'withdraw'),
    button('Change what it concerns…', () => linkDialog(record), 'link'),
  );
}

// Fills `root` with a fact or a decision, `kind` being fact or decision. `state` is the panel's.
export async function renderRecord(root, kind, id, state = { dirty: false }) {
  state.skipped = false;
  state.reload = () => renderRecord(root, kind, id, state);
  if (!root.querySelector('.record-detail')) clear(root).append(h('p', { class: 'quiet' }, 'Loading…'));
  const tools = TOOLS[kind];
  const [record, history, mentions] = await Promise.all([call(tools.get, { [tools.key]: id }), callAll(tools.history, { [tools.key]: id }), fetchMentions(kind, id), loadTargets()]);
  // The panel has moved on to something else while this was being fetched.
  if (state.dead) return state;
  state.version = null;
  state.dirty = false;
  // What it concerns, so that news about one of those (a rename, say) redraws the panel.
  state.concerns = { node: record.node_ids || [], rule: record.rule_ids || [], decision: record.decision_ids || [] };
  const words = kindWords(kind);
  const view = h(
    'article',
    { class: 'record-detail', dataset: { record: `${kind}:${record.id}`, kind, status: record.status, confirmation: record.confirmation === 'confirmed' ? 'confirmed' : 'unconfirmed', projectWide: String(Boolean(record.project_wide)) } },
    h(
      'header',
      {},
      recordChips(record),
      h('h2', { id: 'record-title', tabindex: -1 }, kind === 'decision' ? `${words.label} #${record.id}: ${record.title}` : `${words.label} #${record.id}`),
      closeButton(`Close the ${words.label.toLowerCase()}`),
    ),
    h('div', { class: 'part', dataset: { part: 'confirmation' } }, confirmationControl(record, () => state.reload())),
    section(kind === 'decision' ? 'The decision' : 'The fact', 'record', recordBody(record)),
    lifecycleLines(record).length ? section('Lifecycle', 'lifecycle', lifecycleLines(record)) : null,
    section('What it concerns', 'concerns', concerns(record)),
    h('div', { class: 'part', dataset: { part: 'actions' } }, recordActions(record, state)),
    section(`Mentioned in (${mentions.length})`, 'mentions', mentionsList(mentions)),
    historySection(history),
  );
  const restoreFocus = rememberFocus(root);
  clear(root).append(view);
  restoreFocus();
  state.focus = () => root.querySelector('#record-title').focus();
  return state;
}

// -- a requirement --------------------------------------------------------------------------------------------

const RULE_STATUSES = [
  ['held', 'Held'],
  ['settled', 'Settled'],
  ['dropped', 'Dropped'],
];

function ruleForm(rule, state) {
  const title = h('input', { id: 'edit-title', type: 'text', value: rule.title, autocomplete: 'off' });
  const body = withReferences(h('textarea', { id: 'edit-body', rows: 6, value: rule.body || '' }));
  const status = h('select', { id: 'edit-status', value: rule.status }, RULE_STATUSES.map(([value, label]) => h('option', { value }, label)));
  const problem = h('div', { class: 'problem', role: 'alert' });
  const field = (label, input, id) => h('div', { class: 'field' }, h('label', { for: id }, label), input);
  return h(
    'form',
    {
      class: 'edit',
      onsubmit: async (event) => {
        event.preventDefault();
        problem.replaceChildren();
        const args = { actor: actor(), rule_id: rule.id, expected_version: state.version };
        if (title.value !== rule.title) args.title = title.value;
        if (body.value !== (rule.body || '')) args.body = body.value;
        if (status.value !== rule.status) args.status = status.value;
        if (Object.keys(args).length === 3) {
          problem.textContent = 'Nothing has been changed yet, so there is nothing to save.';
          return;
        }
        try {
          await call('update_rule', args);
          state.dirty = false;
          notify(`Saved requirement #${rule.id}.`);
          await sync();
        } catch (error) {
          if (!(error instanceof Refusal)) throw error;
          await explainStale(error, problem, state, async () => (await call('get_rule', { rule_id: rule.id })).version);
        }
      },
    },
    field('Title', title, 'edit-title'),
    field('Details', body, 'edit-body'),
    field('Status', status, 'edit-status'),
    problem,
    h('div', { class: 'buttons' }, h('button', { type: 'submit', class: 'primary' }, 'Save changes')),
  );
}

// Fills `root` with a requirement (a rule). Its edit form is kept as typed while the panel is
// refreshed, like an epic's, story's or task's.
export async function renderRule(root, id, state = { dirty: false }, force = true) {
  const seen = state.touches || 0;
  state.skipped = false;
  state.reload = (now = false) => renderRule(root, id, state, now);
  if (!root.querySelector('.record-detail')) clear(root).append(h('p', { class: 'quiet' }, 'Loading…'));
  const [rule, recalled, history, mentions] = await Promise.all([
    call('get_rule', { rule_id: id }),
    askRecall({ rule_id: id }, state.withSimilar ? { with_similar: true } : {}),
    callAll('rule_history', { rule_id: id }),
    fetchMentions('rule', id),
    loadTargets(),
  ]);
  if (state.dead) return state;
  if (!force && (state.touches || 0) !== seen) {
    state.skipped = true;
    return state;
  }
  state.version = rule.version;
  state.dirty = false;
  const view = h(
    'article',
    { class: 'record-detail rule-detail', dataset: { record: `rule:${rule.id}`, kind: 'rule', status: rule.status, story: rule.story_id ?? '' } },
    h(
      'header',
      {},
      h('div', { class: 'chips' }, h('span', { class: 'chip kind kind-rule' }, h('span', { 'aria-hidden': 'true' }, kindWords('rule').sign), ` Requirement #${rule.id}`), h('span', { class: `chip rule-status status-${rule.status}` }, statusWords(rule.status))),
      h('h2', { id: 'record-title', tabindex: -1 }, `#${rule.id} ${rule.title}`),
      closeButton('Close the requirement'),
    ),
    rule.story_id !== null && rule.story_id !== undefined ? h('p', { class: 'in-story' }, 'In story ', itemLink(rule.story_id)) : h('p', { class: 'in-story quiet' }, 'It is in no story yet.'),
    h('p', {}, button(rule.story_id === null || rule.story_id === undefined ? 'Put in a story…' : 'Move to another story…', () => placeRequirementDialog(rule), 'place-requirement')),
    section('Details and changes', 'edit', h('div', { class: 'body-text' }, rule.body ? renderMarkdown(rule.body) : h('span', { class: 'quiet' }, 'No details.')), ruleForm(rule, state)),
    section('Facts and decisions', 'context', recordButtons({ rule_ids: [rule.id] }), recallBlock({ rule_id: id }, recalled, { withSimilar: Boolean(state.withSimilar), onSimilar: (on) => (state.withSimilar = on) })),
    section(`Mentioned in (${mentions.length})`, 'mentions', mentionsList(mentions)),
    historySection(history),
  );
  const restoreFocus = rememberFocus(root);
  clear(root).append(view);
  restoreFocus();
  watchEdits(view, state);
  state.focus = () => root.querySelector('#record-title').focus();
  return state;
}
