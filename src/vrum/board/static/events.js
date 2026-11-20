// What happened, in plain words. One change event of the history becomes a sentence: who did what
// to which item, with the item named by its code and current title and linked to its page:
// "story S-12 “Log in”", "acceptance criterion 4 “Short codes”". Used by the Activity page and by the
// histories on the record pages. Every event the server can write is covered, and one it does not
// know is shown by its name rather than hidden. An event names a piece of work by `node` and
// `other_node` (refs such as story:12); acceptance criteria and notes keep their own ids (`rule_id`,
// `note_id`, and `other_note_id` for the note at the other end of a link).

import { h, moment } from './dom.js';
import { icon } from './icons.js';
import { itemLink } from './ctx.js';
import { renderMarkdown } from './markdown.js';
import { KIND, LINK, PRIORITY, SIZE_WORDS, actorLabel, codeOf, nodeCode, parseNodeRef, statusLabel } from './labels.js';
import { headlineOf, store } from './store.js';

const clip = (text, size = 60) => {
  const one = String(text ?? '').replace(/\s+/g, ' ').trim();
  return one.length > size ? `${one.slice(0, size - 1)}…` : one;
};

// Someone's own words in a sentence, whole and on one line: the sentence wraps, so nothing is cut.
// A closing full stop is left off, since the sentence adds its own.
const whole = (text) =>
  String(text ?? '')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\.+$/, '');

const NOTE_ACTIONS = ['note_recorded', 'note_superseded', 'note_withdrawn', 'note_linked', 'note_unlinked', 'note_confirmed', 'note_unconfirmed'];
// A check of the definition of done ticked on a story or task, or unticked. Neither changes the item itself.
const CHECK_ACTIONS = ['check_ticked', 'check_unticked'];
// How much of what was said about how a check was done the sentence holds; the rest is in "Show what changed".
const EVIDENCE_IN_SENTENCE = 100;

// What can be filtered by: the actions of the history in the groups a person thinks in.
export const CHANGE_KINDS = [
  ['work', 'Work items', ['create', 'update', 'move']],
  ['links', 'Links', ['link_added', 'link_removed']],
  ['claims', 'Claims', ['claimed', 'claim_renewed', 'claim_released']],
  ['handoffs', 'Handoff notes', ['handoff']],
  ['requirements', 'Acceptance criteria', ['rule_created', 'rule_updated', 'rule_placed', 'rule_moved']],
  ['definition', 'Definition of done', ['definition_of_done_changed']],
  ['checks', 'Checks', CHECK_ACTIONS],
  ['notes', 'Notes', NOTE_ACTIONS],
];

export const kindOfAction = (action) => (CHANGE_KINDS.find(([, , actions]) => actions.includes(action)) || [])[0] || 'other';

// The icon of each kind of change in the feed.
const CHANGE_ICONS = { work: 'task', links: 'link', claims: 'lock', handoffs: 'done', requirements: 'rule', definition: 'checklist', checks: 'checklist', notes: 'diamond', other: 'info' };

// A named item as a link: "story S-12 “Log in”", "acceptance criterion 4 “Short codes”", "note 2 “…”".
const link = (kind, number, words, attributes) => itemLink(`${kind}:${number}`, { class: 'item-link', dataset: { kind, ...attributes } }, words);

// A piece of work, from its ref (story:12).
function node(ref) {
  if (ref === null || ref === undefined) return 'an item';
  const at = parseNodeRef(ref);
  if (!at) return `an item (${ref})`;
  const found = store.nodes.get(ref);
  const code = nodeCode(at.kind, at.number);
  const words = found ? `${KIND[at.kind].label.toLowerCase()} ${code} “${clip(found.title)}”` : `${at.kind} ${code}`;
  return link(at.kind, at.number, words, { ref, number: String(at.number) });
}

const record = (kind, id, words) => (id === null || id === undefined ? 'an item' : link(kind, id, words, { id: String(id) }));

function rule(id) {
  const found = (store.rules || []).find((item) => item.id === id);
  return record('rule', id, found && found.title ? `acceptance criterion ${id} “${clip(found.title)}”` : `acceptance criterion ${id}`);
}

// A note, named "note 7 “first line”".
function note(id) {
  const found = (store.notes || []).find((item) => item.id === id);
  return record('note', id, found ? `note ${id} “${clip(headlineOf(found))}”` : `note ${id}`);
}

const present = (value) => value !== null && value !== undefined;

// Whatever the event names as its subject, whichever kind of thing that is. A note event may also
// name what it concerns, so the note comes before what it is linked to.
function subject(event) {
  if (present(event.note_id)) return note(event.note_id);
  if (present(event.rule_id)) return rule(event.rule_id);
  return node(event.node);
}

// What a link or a concern points at: the node, acceptance criterion or note the event is also about.
function target(event) {
  if (present(event.node)) return node(event.node);
  if (present(event.rule_id)) return rule(event.rule_id);
  if (present(event.other_note_id)) return note(event.other_note_id);
  return 'an item';
}

const FIELD = { title: 'title', body: 'details', status: 'status', priority: 'priority', size: 'size', start_on: 'start day', target_on: 'target day', parent: 'parent', node: 'placed on' };
const value = (key, text) => {
  if (text === null || text === undefined || text === '') return 'empty';
  if (key === 'status') return statusLabel(text).toLowerCase();
  if (key === 'size') return (SIZE_WORDS[text] || text).toLowerCase();
  if (key === 'parent' || key === 'node') return codeOf(text);
  return `“${clip(text, 40)}”`;
};

const changedKeys = (event) => Object.keys(event.after || {}).filter((key) => key !== 'version');

// "status: new → done; title: “a” → “b”; the details".
function changes(event) {
  return changedKeys(event)
    .map((key) => {
      const name = FIELD[key] || key.replace(/_/g, ' ');
      if (key === 'body') return 'the details';
      const before = event.before && key in event.before ? `${value(key, event.before[key])} → ` : '';
      return `${name}: ${before}${value(key, event.after[key])}`;
    })
    .join('; ');
}

// -- what changed, in full -----------------------------------------------------------------------------

// A field's value as a person reads it when it is shown whole: words and lines as they were written.
// Empty is null, so that it is shown as empty rather than as nothing.
function wholeValue(key, text) {
  if (text === null || text === undefined || text === '') return null;
  if (key === 'status') return statusLabel(text);
  if (key === 'priority') return PRIORITY[text]?.label || text;
  if (key === 'size') return SIZE_WORDS[text] || text;
  if (key === 'parent' || key === 'node') return codeOf(text);
  return String(text);
}

// Whether the sentence leaves something out: the details are never in it, and a title is cut when it
// is long. A tick or untick leaves out what is said of how it was checked when that is long or has lines.
const evidenceCut = (text) => Boolean(text) && clip(text, EVIDENCE_IN_SENTENCE) !== String(text).trim();
const sentenceLeavesOut = (event) =>
  CHECK_ACTIONS.includes(event.action)
    ? [event.before?.evidence, event.after?.evidence].some(evidenceCut)
    : changedKeys(event).some((key) => key === 'body' || (key === 'title' && [event.before?.title, event.after.title].some((text) => clip(text, 40) !== String(text ?? ''))));

// What a person reads of a tick in full: how it was checked, and for a tick that replaces one or an untick, what it was.
function checkFields(event, side) {
  const field = (name, ...pair) => h('div', { class: 'change-field', dataset: { field: 'evidence' } }, h('h4', {}, name), h('div', { class: 'change-pair' }, pair));
  const was = event.before?.evidence ?? null;
  const now = event.after?.evidence ?? null;
  if (event.action === 'check_unticked') return [field('Why it was unticked', side('evidence', 'Before', was), side('evidence', 'After', now))];
  return [was === null ? field('How it was checked', side('evidence', 'After', now)) : field('How it was checked', side('evidence', 'Before', was), side('evidence', 'After', now))];
}

// The "Show what changed" disclosure of an update: every changed field with its value before and
// after, whole (Markdown is not drawn: the words are shown as they were written). Only a change
// whose sentence leaves something out has one. `open` and `onToggle(now)` keep a disclosure the
// person opened open when the list it is in is drawn again.
export function whatChanged(event, { open = false, onToggle = () => {} } = {}) {
  if (!['update', 'rule_updated', 'definition_of_done_changed', ...CHECK_ACTIONS].includes(event.action) || !sentenceLeavesOut(event)) return null;
  const side = (key, label, text) => {
    const shown = wholeValue(key, text);
    return h('div', { class: `change-side ${label.toLowerCase()}` }, h('span', { class: 'change-label' }, label), shown === null ? h('span', { class: 'quiet change-empty' }, 'Empty') : h('div', { class: 'change-text' }, shown));
  };
  const fields = CHECK_ACTIONS.includes(event.action)
    ? checkFields(event, side)
    : changedKeys(event).map((key) => {
        const name = event.action === 'definition_of_done_changed' ? 'definition of done' : FIELD[key] || key.replace(/_/g, ' ');
        const had = event.before && key in event.before;
        return h(
          'div',
          { class: 'change-field', dataset: { field: key } },
          h('h4', {}, name.charAt(0).toUpperCase() + name.slice(1)),
          h('div', { class: 'change-pair' }, had ? side(key, 'Before', event.before[key]) : h('div', { class: 'change-side before' }, h('span', { class: 'change-label' }, 'Before'), h('span', { class: 'quiet change-empty' }, 'Not kept')), side(key, 'After', event.after[key])),
        );
      });
  const disclosure = h(
    'details',
    { class: 'what-changed', open },
    h('summary', {}, icon('chevron_right', { size: 14, className: 'disclosure-chevron' }), 'Show what changed'),
    h('div', { class: 'change-fields' }, fields),
  );
  // Setting `open` while drawing also fires a toggle: only a change of it is the person's choice.
  let known = disclosure.open;
  disclosure.addEventListener('toggle', () => {
    if (disclosure.open === known) return;
    known = disclosure.open;
    onToggle(known);
  });
  return disclosure;
}

// A piece of work or an acceptance criterion's holder, for a sentence: "no story or task" when it is on none.
function holder(ref) {
  return ref === null || ref === undefined ? 'no story or task' : node(ref);
}

// Why a record ended, and the person's own words when it needed them. Both are whole: the sentence
// wraps.
const why = (a) => {
  const text = a.status_reason ?? a.reason;
  return text ? `: ${whole(text)}` : '';
};
const said = (a) => {
  const who = a.said_by ?? a.person;
  if (!who && !a.quote) return '';
  return `, on the word of ${who || 'a person'}${a.quote ? `: “${whole(a.quote)}”` : ''}`;
};

// A note: recorded, replaced, withdrawn, linked, unlinked, confirmed, unconfirmed.
function describeNote(event, a) {
  const self = note(event.note_id);
  switch (event.action.slice('note_'.length)) {
    case 'recorded':
      return ['recorded ', self, a.decided_by ? `, decided by ${a.decided_by}` : a.why ? `, why: ${whole(a.why)}` : '', a.project_wide ? ', for the whole project' : ''];
    case 'superseded':
      return ['replaced ', self, ' with ', note(a.superseded_by_id), why(a), said(a)];
    case 'withdrawn':
      return ['withdrew ', self, why(a), said(a)];
    case 'linked':
      return ['linked ', self, ' to ', target(event)];
    case 'unlinked':
      return ['unlinked ', self, ' from ', target(event)];
    case 'confirmed':
      return [a.confirmed_by && a.confirmed_by !== event.actor ? `recorded that ${a.confirmed_by} confirmed ` : 'confirmed ', self, a.quote ? `, saying: “${whole(a.quote)}”` : ''];
    default:
      return ['took back the confirmation of ', self, said(a)];
  }
}

// A tick or an untick: the check, in the words of the definition of done, and how it was checked (or why it was
// unticked) when it was said, as much of it as a sentence holds.
const checkWords = (check) => `“${whole(check.text)}”`;
const evidenceWords = (check) => (check.evidence ? `: ${clip(whole(check.evidence), EVIDENCE_IN_SENTENCE)}` : '');

// An update that only changes the size has its own words: "sized task T-3 as Medium".
const sizeOnly = (event) => {
  const keys = changedKeys(event);
  return keys.length === 1 && keys[0] === 'size';
};

function describeSize(event) {
  const was = event.before?.size;
  const now = event.after?.size;
  if (!now) return ['took the size off ', node(event.node)];
  if (!was) return ['sized ', node(event.node), ` as ${SIZE_WORDS[now] || now}`];
  return ['changed the size of ', node(event.node), ` from ${SIZE_WORDS[was] || was} to ${SIZE_WORDS[now] || now}`];
}

// The sentence, without the actor: an array of text and links.
export function describe(event) {
  const a = event.after || {};
  const b = event.before || {};
  switch (event.action) {
    case 'create':
      return ['created ', node(event.node)];
    case 'update':
      return sizeOnly(event) ? describeSize(event) : ['changed ', node(event.node), `: ${changes(event)}`];
    case 'move':
      return ['moved ', node(event.node), ' from ', node(b.parent), ' to ', node(a.parent)];
    case 'link_added':
    case 'link_removed': {
      const verb = LINK[(event.action === 'link_added' ? a : b).link_type]?.label || 'is linked to';
      return [event.action === 'link_added' ? 'added a link: ' : 'removed a link: ', node(event.node), ` ${verb} `, node(event.other_node)];
    }
    case 'claimed':
      return ['took ', node(event.node), ` until ${moment(a.expires_at)}`];
    case 'claim_renewed':
      return ['kept ', node(event.node), ` for longer, until ${moment(a.expires_at)}`];
    case 'claim_released':
      return ['let go of ', node(event.node)];
    case 'handoff':
      if (a.outcome === 'released') return ['handed ', node(event.node), ' back and left this handoff note'];
      // Blocked and dropped are also how a person marks work with a reason, so neither says the work was finished.
      if (a.outcome === 'blocked' || a.outcome === 'dropped') return ['marked ', node(event.node), ` as ${a.outcome} and left this handoff note`];
      return ['finished their work on ', node(event.node), ` (${a.outcome}) and left this handoff note`];
    case 'rule_created':
      return ['wrote ', rule(event.rule_id), present(event.node) ? [', on ', node(event.node)] : ', not placed yet'];
    case 'rule_updated':
      return ['changed ', rule(event.rule_id), `: ${changes(event)}`];
    case 'rule_placed':
      return ['put ', rule(event.rule_id), ' on ', node(a.node)];
    case 'rule_moved':
      return ['moved ', rule(event.rule_id), ' from ', holder(b.node), ' to ', holder(a.node)];
    case 'definition_of_done_changed':
      return ['changed the definition of done'];
    case 'check_ticked':
      return [b.item ? 'changed how ' : 'ticked ', checkWords(a), b.item ? ' was checked on ' : ' on ', node(event.node), evidenceWords(a)];
    case 'check_unticked':
      return ['unticked ', checkWords(b), ' on ', node(event.node), evidenceWords(a)];
    default:
      if (NOTE_ACTIONS.includes(event.action)) return describeNote(event, a);
      return [`${event.action.replace(/_/g, ' ')} `, subject(event)];
  }
}

// The row of the feed: an icon for the kind of change, who, what, when, and a handoff note in full.
// The time is the clock time; the day is said once for all the rows of it (see dayLabel), and the
// time's own tooltip gives the whole moment. `opened` is the set of event ids whose "Show what
// changed" the person has open, kept by the page so that a redraw leaves them open.
export function eventRow(event, { opened = new Set() } = {}) {
  const kind = kindOfAction(event.action);
  const label = (CHANGE_KINDS.find(([value]) => value === kind) || [])[1] || 'Other change';
  return h(
    'li',
    { class: `event action-${event.action}`, dataset: { event: event.id, action: event.action, actor: event.actor, actorKind: event.actor_kind || '', kind } },
    h('span', { class: `event-icon kind-${kind}`, title: label }, icon(CHANGE_ICONS[kind] || 'info', { size: 14 })),
    h('strong', { class: 'event-actor' }, actorLabel(event.actor, event.actor_kind)),
    ' ',
    h(
      'div',
      { class: 'event-body' },
      h('p', { class: 'event-line' }, h('span', { class: 'event-words' }, ...describe(event), '.')),
      whatChanged(event, { open: opened.has(event.id), onToggle: (now) => (now ? opened.add(event.id) : opened.delete(event.id)) }),
      event.action === 'handoff' ? h('div', { class: 'handoff-note' }, renderMarkdown(event.after.note || '', { className: 'markdown' })) : null,
    ),
    ' ',
    h('time', { class: 'quiet event-time', datetime: event.at, title: moment(event.at) }, clock(event.at)),
  );
}

const asDate = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
};

// The time of day, in the person's own time.
function clock(value) {
  const date = asDate(value);
  return date ? date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) : String(value);
}

// The person's own day of a moment, to tell where one day's rows end and the next begin.
export const dayKey = (value) => {
  const date = asDate(value);
  return date ? `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}` : String(value);
};

// The day in words: Today, Yesterday, then the weekday and date.
export function dayLabel(value) {
  const date = asDate(value);
  if (!date) return String(value);
  const now = new Date();
  const earlier = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  if (dayKey(date) === dayKey(now)) return 'Today';
  if (dayKey(date) === dayKey(earlier)) return 'Yesterday';
  return date.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', ...(date.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }) });
}

// Whether an event is about the item a person is following: `item` is { ref } for a piece of work,
// { kind, id } for an acceptance criterion or a note, or null.
export function aboutItem(event, item) {
  if (!item) return true;
  if (item.ref) return event.node === item.ref || event.other_node === item.ref;
  if (item.kind === 'rule') return event.rule_id === item.id;
  return event.note_id === item.id || event.other_note_id === item.id;
}
