// What happened, in plain words. One change event of the history becomes a sentence: who did what
// to which item, with the item named by its code and current title and linked to its page:
// "story S-12 “Log in”", "AC-4 “Short codes”", "N-7 “Use short codes”". Used by the Activity page and by the
// histories on the record pages. Every event the server can write is covered, and one it does not
// know is shown by its name rather than hidden. An event names a piece of work by `node` and
// `other_node` (refs such as story:12); acceptance criteria and notes keep their own ids (`rule_id`,
// `note_id`, and `other_note_id` for the note at the other end of a link). A run of ticks of a
// definition of done by one actor on one item is one row on the Activity page (`foldRuns`, `checksRow`).

import { h, moment, plural } from './dom.js';
import { icon } from './icons.js';
import { itemLink, viewLink } from './ctx.js';
import { renderMarkdown, plainText } from './markdown.js';
import { KIND, LINK, PRIORITY, SIZE_WORDS, actorLabel, askCode, codeOf, countsWords, gateCode, nodeCode, noteCode, parseNodeRef, planCode, proposalCode, ruleCode, statusLabel } from './labels.js';
import { headlineOf, store } from './store.js';
import { whereWords } from './whereabouts.js';

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
// A question an agent put to a person, and the person's answer.
const ASK_ACTIONS = ['ask_raised', 'ask_answered'];
// Work held for something outside the graph, plans that wait for a person's approval and the templates plans are made from. Plans
// and templates belong to no item, so they are in the feed of the project and in no item's history; a gate names its item.
const GATE_ACTIONS = ['gate_added', 'gate_removed', 'plan_proposed', 'plan_revised', 'plan_changes_requested', 'plan_approved', 'plan_rejected', 'plan_template_saved', 'plan_template_deleted'];
// A proposal to move work under another parent, and a person's decision on it.
const MOVE_ACTIONS = ['placement_proposed', 'placement_accepted', 'placement_rejected'];
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
  ['asks', 'Questions', ASK_ACTIONS],
  ['gates', 'Gates, plans and templates', GATE_ACTIONS],
  ['moves', 'Suggested moves', MOVE_ACTIONS],
];

export const kindOfAction = (action) => (CHANGE_KINDS.find(([, , actions]) => actions.includes(action)) || [])[0] || 'other';

// The icon of each kind of change in the feed.
const CHANGE_ICONS = { work: 'task', links: 'link', claims: 'lock', handoffs: 'done', requirements: 'rule', definition: 'checklist', checks: 'checklist', notes: 'diamond', asks: 'ask', gates: 'gate', moves: 'move', other: 'info' };

// A named item as a link: "story S-12 “Log in”", "AC-4 “Short codes are unique”", "N-2 “Use short codes”".
const link = (kind, number, words, attributes) => itemLink(`${kind}:${number}`, { class: 'item-link', dataset: { kind, ...attributes } }, words);

// A piece of work, from its ref (story:12): its kind, its code and its title.
function node(ref) {
  if (ref === null || ref === undefined) return 'an item';
  const at = parseNodeRef(ref);
  if (!at) return 'an item';
  const found = store.nodes.get(ref);
  const code = nodeCode(at.kind, at.number);
  const words = found ? `${KIND[at.kind].label.toLowerCase()} ${code} “${clip(found.title)}”` : `${at.kind} ${code}`;
  return link(at.kind, at.number, words, { ref, number: String(at.number) });
}

// A piece of work by its code and title, where a sentence only says which one and does not link it.
const titled = (ref) => {
  const found = ref === null || ref === undefined ? null : store.nodes.get(ref);
  return found ? `${codeOf(ref)} “${clip(found.title, 40)}”` : codeOf(ref);
};

const record = (kind, id, words) => (id === null || id === undefined ? 'an item' : link(kind, id, words, { id: String(id) }));

function rule(id) {
  const found = (store.rules || []).find((item) => item.id === id);
  return record('rule', id, found && found.title ? `${ruleCode(id)} “${clip(found.title)}”` : ruleCode(id));
}

// A note, named "N-7 “first line”", the first line of its text with a reference in it written as the name of what it points at.
function note(id) {
  const found = (store.notes || []).find((item) => item.id === id);
  return record('note', id, found ? `${noteCode(id)} “${clip(plainText(headlineOf(found)))}”` : noteCode(id));
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
  if (key === 'parent' || key === 'node') return titled(text);
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
  if (key === 'parent' || key === 'node') return store.nodes.has(text) ? `${codeOf(text)} ${store.nodes.get(text).title}` : codeOf(text);
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
  return foldable('Show what changed', h('div', { class: 'change-fields' }, fields), { open, onToggle });
}

// A "Show …" disclosure that tells the page when the person opens or closes it, so that a list drawn again
// leaves it as they had it. Setting `open` while drawing also fires a toggle: only a change of it is their choice.
function foldable(label, body, { open = false, onToggle = () => {}, className = '' } = {}) {
  const disclosure = h(
    'details',
    { class: `what-changed${className ? ` ${className}` : ''}`, open },
    h('summary', {}, icon('chevron_right', { size: 14, className: 'disclosure-chevron' }), label),
    body,
  );
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

// A question and its answer, whole. An ask's number is the id of the event that raised it, and an answer says which ask it is for.
const ask = (id) => h('span', { class: 'item-code ask-code' }, askCode(id));
const offer = (a) => (a.kind === 'approval' ? ' (approval: yes or no)' : a.choices?.length ? ` (choices: ${a.choices.join(', ')})` : '');

// An update that only changes the size has its own words: "sized task T-3 “Log in” as Medium".
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

// A few items as a sentence names them, each a link: "story S-4 “Pay”, story S-9 “Refund” and 2 more".
function itemList(refs, limit = 3) {
  const shown = refs.slice(0, limit);
  const rest = refs.length - shown.length;
  const parts = [];
  shown.forEach((ref, at) => {
    if (at) parts.push(at === shown.length - 1 && !rest ? ' and ' : ', ');
    parts.push(node(ref));
  });
  if (rest) parts.push(` and ${rest} more`);
  return parts;
}

// What a gate waits for, as its event says it (the server's own words for a gate that exists now are on the item's page). A timer's
// time is UTC, written the way the server writes it.
const utcWords = (text) => String(text).replace('T', ' ').replace(/Z$/, ' UTC');
const gateWaits = (a) => (a.kind === 'approval' ? ['waits for a person to approve it', a.ask === undefined || a.ask === null ? '' : ` (${askCode(a.ask)})`] : [`waits until ${utcWords(a.not_before)}`]);

// How many epics, stories and tasks a list of references holds.
function kindCounts(refs) {
  const count = (kind) => refs.filter((ref) => String(ref).startsWith(`${kind}:`)).length;
  return countsWords(count('epic'), count('story'), count('task'));
}

// Where a proposal would put work: under an existing parent or under a new epic.
const proposalWhere = (a) => (a.parent ? ['under ', node(a.parent)] : ['under a new epic, ', `“${whole(a.new_epic?.title)}”`]);

function describePlacement(event, a) {
  switch (event.action) {
    case 'placement_proposed':
      return ['proposed ', proposalCode(event.id), ': move ', itemList((a.items || []).map((one) => one.ref)), ' ', proposalWhere(a), `: ${whole(a.reason)}`];
    case 'placement_accepted': {
      const left = a.skipped || [];
      // When some items were not moved (left out, or moved or finished since), how many moved and how many did not.
      const counts = left.length ? ` (${(a.applied || []).length} moved, ${left.length} left out)` : '';
      return [
        'accepted ',
        proposalCode(a.placement),
        counts,
        a.applied?.length ? [': moved ', itemList(a.applied)] : '',
        a.epic ? [a.applied?.length ? ', in the new epic ' : ': made the new epic ', node(a.epic)] : '',
        left.length ? [a.applied?.length || a.epic ? '; left ' : ': left ', left.map((one, at) => [at ? ', ' : '', node(one.ref), ` where it is (${whole(one.why)})`])] : '',
        a.reason ? `, saying: “${whole(a.reason)}”` : '',
      ];
    }
    default:
      return ['rejected ', proposalCode(a.placement), a.reason ? `: ${whole(a.reason)}` : ''];
  }
}

// A gate, a plan or a plan template. Each plan and proposal is named by the id of the event that made it. A plan's code is a link to its
// page, where every revision can be read.
const planLink = (id) => viewLink('review', { change: `plan:${id}` }, { class: 'item-link plan-link', dataset: { plan: String(id) } }, planCode(id));

// "revision 2 of ", said only for a revision after the first: a default says nothing.
const ofRevision = (revision) => (revision > 1 ? `revision ${revision} of ` : '');

function describeGatesAndPlans(event, a) {
  switch (event.action) {
    case 'gate_added':
      return ['added ', gateCode(event.id), ' to ', node(event.node), ': ', gateWaits(a)];
    case 'gate_removed':
      return ['lifted ', gateCode(a.gate), ' on ', node(event.node), a.reason ? `: ${whole(a.reason)}` : ''];
    case 'plan_proposed':
      return ['proposed ', planLink(event.id), `, ${kindCounts(a.items || [])}: ${whole(a.title)}`];
    case 'plan_revised': {
      // What the revision says changed, whole; and only when something was taken out, how many. The snapshot it holds is never shown.
      const removed = (a.removed || []).length;
      return ['sent revision ', String(a.revision), ' of ', planLink(a.plan), removed ? ` (removed ${removed})` : '', a.note ? `: ${whole(a.note)}` : ''];
    }
    case 'plan_changes_requested': {
      // How many comments and approvals went with it; a count of nothing is left out.
      const said = (a.comments || []).length;
      const approved = (a.approved || []).length;
      const counts = [said ? plural(said, 'comment') : null, approved ? `${approved} approved` : null].filter(Boolean);
      return ['asked for changes to revision ', String(a.revision), ' of ', planLink(a.plan), `: ${whole(a.summary)}`, counts.length ? ` (${counts.join(', ')})` : ''];
    }
    case 'plan_approved':
      return ['approved ', ofRevision(a.revision), planLink(a.plan), a.reason ? `, saying: “${whole(a.reason)}”` : ''];
    case 'plan_rejected':
      return ['rejected ', ofRevision(a.revision), planLink(a.plan), a.reason ? `: ${whole(a.reason)}` : ''];
    case 'plan_template_saved':
      return ['saved the plan template ', whole(a.name)];
    default:
      return ['deleted the plan template ', whole(a.name)];
  }
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
      return ['took ', node(event.node), ` until ${moment(a.expires_at)}`, ...whereWords(a)];
    case 'claim_renewed':
      return ['kept ', node(event.node), ` for longer, until ${moment(a.expires_at)}`, ...whereWords(a)];
    case 'claim_released':
      return ['let go of ', node(event.node), ...whereWords(b)];
    case 'handoff':
      if (a.outcome === 'released') return ['handed ', node(event.node), ' back and left this handoff note', ...whereWords(a)];
      // Blocked and dropped are also how a person marks work with a reason, so neither says the work was finished.
      if (a.outcome === 'blocked' || a.outcome === 'dropped') return ['marked ', node(event.node), ` as ${a.outcome} and left this handoff note`, ...whereWords(a)];
      return ['finished their work on ', node(event.node), ` (${a.outcome}) and left this handoff note`, ...whereWords(a)];
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
    case 'ask_raised':
      return ['asked ', ask(event.id), ' about ', node(event.node), `: “${whole(a.question)}”${offer(a)}`];
    case 'ask_answered':
      return ['answered ', ask(a.ask), ' about ', node(event.node), ` with ${whole(a.answer)}`, a.comment ? `, saying: “${whole(a.comment)}”` : ''];
    default:
      if (NOTE_ACTIONS.includes(event.action)) return describeNote(event, a);
      if (GATE_ACTIONS.includes(event.action)) return describeGatesAndPlans(event, a);
      if (MOVE_ACTIONS.includes(event.action)) return describePlacement(event, a);
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

// -- a run of ticks, as one row ---------------------------------------------------------------------------

// Ticking a whole definition of done is seven events by one actor on one item in a row, which would be seven
// near-identical lines. Consecutive ticks (or consecutive unticks) by one actor on one item on one day become
// one run; everything else is a row of its own. `rows` is newest first, and what comes back is a list of
// `{ event }` and `{ run }` (a run is newest first too). A tick that changes how an item was checked is not a
// plain tick, so it stands alone and ends a run.
const isPlainCheck = (event) => event.action === 'check_unticked' || (event.action === 'check_ticked' && !event.before?.item);
const sameRun = (a, b) => a.action === b.action && a.actor === b.actor && (a.actor_kind || '') === (b.actor_kind || '') && a.node === b.node && dayKey(a.at) === dayKey(b.at);

export function foldRuns(rows) {
  const items = [];
  for (const event of rows) {
    const run = items[items.length - 1]?.run;
    if (isPlainCheck(event) && run && sameRun(run[0], event)) run.push(event);
    else items.push(isPlainCheck(event) ? { run: [event] } : { event });
  }
  // A run of one is an ordinary row.
  return items.map((item) => (item.run && item.run.length === 1 ? { event: item.run[0] } : item));
}

// The row of a run: "ticked 7 checks on task T-171 “Permits”", and behind "Show the checks" every tick with its
// words and, in full, how it was checked. The disclosure is kept by the id of the run's oldest event, which is
// what stays when newer ticks join it.
export function checksRow(run, { opened = new Set() } = {}) {
  const newest = run[0];
  const oldest = run[run.length - 1];
  const unticked = newest.action === 'check_unticked';
  const key = `run:${oldest.id}`;
  const checks = [...run].reverse().map((event) => {
    const words = whole((unticked ? event.before : event.after)?.text);
    const said = event.after?.evidence;
    return h(
      'li',
      { class: 'run-check', dataset: { event: event.id } },
      h('p', { class: 'run-check-words' }, `“${words}”`, h('time', { class: 'quiet run-check-time', datetime: event.at, title: moment(event.at) }, clock(event.at))),
      said ? h('div', { class: 'change-text run-check-evidence' }, unticked ? `Why it was unticked: ${said}` : String(said)) : null,
    );
  });
  const label = (CHANGE_KINDS.find(([value]) => value === 'checks') || [])[1];
  return h(
    'li',
    { class: `event event-run action-${newest.action}`, dataset: { event: newest.id, action: newest.action, actor: newest.actor, actorKind: newest.actor_kind || '', kind: 'checks', count: String(run.length) } },
    h('span', { class: 'event-icon kind-checks', title: label }, icon(CHANGE_ICONS.checks, { size: 14 })),
    h('strong', { class: 'event-actor' }, actorLabel(newest.actor, newest.actor_kind)),
    ' ',
    h(
      'div',
      { class: 'event-body' },
      h('p', { class: 'event-line' }, h('span', { class: 'event-words' }, unticked ? 'unticked ' : 'ticked ', `${run.length} checks on `, node(newest.node), '.')),
      foldable('Show the checks', h('ol', { class: 'plain run-checks' }, checks), { open: opened.has(key), onToggle: (now) => (now ? opened.add(key) : opened.delete(key)), className: 'checks-run' }),
    ),
    ' ',
    h('time', { class: 'quiet event-time', datetime: newest.at, title: `${moment(oldest.at)} to ${moment(newest.at)}` }, clock(newest.at)),
  );
}

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
