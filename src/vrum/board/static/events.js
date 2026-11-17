// What happened, in plain words. One change event of the history becomes a sentence: who did what
// to which item, with the item named by its code and current title and linked to where it is
// shown (the detail panel): "story S-12 “Log in”", "requirement 4 “Short codes”". Used by the
// Activity drawer. Every event the server can write is covered, and one it does not know is shown
// by its name rather than hidden. An event names a piece of work by `node` and `other_node` (refs
// such as story:12); requirements, decisions and facts keep their own ids.

import { h, moment } from './dom.js';
import { icon } from './icons.js';
import { recordLink } from './ctx.js';
import { renderMarkdown } from './markdown.js';
import { KIND, LINK, STATUS, codeOf, nodeCode, parseNodeRef } from './labels.js';
import { store } from './store.js';

const clip = (text, size = 60) => {
  const one = String(text ?? '').replace(/\s+/g, ' ').trim();
  return one.length > size ? `${one.slice(0, size - 1)}…` : one;
};

const FACT_ACTIONS = ['fact_recorded', 'fact_superseded', 'fact_withdrawn', 'fact_linked', 'fact_unlinked', 'fact_confirmed', 'fact_unconfirmed'];
const DECISION_ACTIONS = ['decision_recorded', 'decision_superseded', 'decision_withdrawn', 'decision_linked', 'decision_unlinked', 'decision_confirmed', 'decision_unconfirmed'];

// What can be filtered by: the actions of the history in the groups a person thinks in.
export const CHANGE_KINDS = [
  ['work', 'Work items', ['create', 'update', 'move']],
  ['links', 'Links', ['link_added', 'link_removed']],
  ['claims', 'Claims', ['claimed', 'claim_renewed', 'claim_released']],
  ['handoffs', 'Handoff notes', ['handoff']],
  ['requirements', 'Requirements', ['rule_created', 'rule_updated', 'rule_placed', 'rule_moved']],
  ['decisions', 'Decisions', DECISION_ACTIONS],
  ['facts', 'Facts', FACT_ACTIONS],
];

export const kindOfAction = (action) => (CHANGE_KINDS.find(([, , actions]) => actions.includes(action)) || [])[0] || 'other';

// The icon of each kind of change in the feed.
const CHANGE_ICONS = { work: 'task', links: 'link', claims: 'lock', handoffs: 'done', requirements: 'rule', decisions: 'decision', facts: 'fact', other: 'info' };

// A named item as a link: "story S-12 “Log in”", "requirement 4 “Short codes”", "fact 2 “…”".
const link = (kind, number, words, attributes) => recordLink(kind, number, { class: 'item-link', dataset: { kind, ...attributes } }, words);

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
  return record('rule', id, found && found.title ? `requirement ${id} “${clip(found.title)}”` : `requirement ${id}`);
}

function decision(id) {
  const found = (store.decisions || []).find((item) => item.id === id);
  return record('decision', id, found ? `decision ${id} “${clip(found.title)}”` : `decision ${id}`);
}

function fact(id) {
  const found = (store.facts || []).find((item) => item.id === id);
  return record('fact', id, found ? `fact ${id} “${clip(found.text)}”` : `fact ${id}`);
}

const present = (value) => value !== null && value !== undefined;

// Whatever the event names as its subject, whichever kind of thing that is. A fact or decision
// event may also name what it concerns, so the record comes before what it is linked to.
function subject(event) {
  if (present(event.fact_id)) return fact(event.fact_id);
  if (present(event.decision_id)) return decision(event.decision_id);
  if (present(event.rule_id)) return rule(event.rule_id);
  return node(event.node);
}

// What a link or a concern points at: the node, requirement or decision the event is also about.
function target(event) {
  if (present(event.node)) return node(event.node);
  if (present(event.rule_id)) return rule(event.rule_id);
  if (present(event.decision_id)) return decision(event.decision_id);
  return 'an item';
}

const FIELD = { title: 'title', body: 'details', status: 'status', priority: 'priority', start_on: 'start day', target_on: 'target day', parent: 'parent', story: 'story' };
const value = (key, text) => {
  if (text === null || text === undefined || text === '') return 'empty';
  if (key === 'status') return STATUS[text]?.label?.toLowerCase() || text;
  if (key === 'parent' || key === 'story') return codeOf(text);
  return `“${clip(text, 40)}”`;
};

// "status: new → done; title: “a” → “b”; the details".
function changes(event) {
  const keys = Object.keys(event.after).filter((key) => key !== 'version');
  return keys
    .map((key) => {
      const name = FIELD[key] || key.replace(/_/g, ' ');
      if (key === 'body') return 'the details';
      const before = event.before && key in event.before ? `${value(key, event.before[key])} → ` : '';
      return `${name}: ${before}${value(key, event.after[key])}`;
    })
    .join('; ');
}

function story(ref) {
  return ref === null || ref === undefined ? 'no story' : node(ref);
}

// Why a record ended, and the person's own words when it needed them.
const why = (a) => {
  const text = a.status_reason ?? a.reason;
  return text ? `: ${clip(text, 80)}` : '';
};
const said = (a) => {
  const who = a.said_by ?? a.person;
  if (!who && !a.quote) return '';
  return `, on the word of ${who || 'a person'}${a.quote ? `: “${clip(a.quote, 100)}”` : ''}`;
};

// A fact or a decision: recorded, replaced, withdrawn, linked, unlinked, confirmed, unconfirmed.
function describeRecord(kind, event, a) {
  const name = kind === 'fact' ? fact : decision;
  const self = name(kind === 'fact' ? event.fact_id : event.decision_id);
  switch (event.action.slice(kind.length + 1)) {
    case 'recorded':
      return ['recorded ', self, kind === 'fact' ? (a.source ? `, source: ${clip(a.source, 70)}` : '') : `, decided by ${a.decided_by}${a.project_wide ? ', for the whole project' : ''}`];
    case 'superseded':
      return ['replaced ', self, ' with ', name(a.superseded_by_id), why(a), said(a)];
    case 'withdrawn':
      return ['withdrew ', self, why(a), said(a)];
    case 'linked':
      return ['linked ', self, ' to ', target(event)];
    case 'unlinked':
      return ['unlinked ', self, ' from ', target(event)];
    case 'confirmed':
      return [a.confirmed_by && a.confirmed_by !== event.actor ? `recorded that ${a.confirmed_by} confirmed ` : 'confirmed ', self];
    default:
      return ['took back the confirmation of ', self, said(a)];
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
      return ['changed ', node(event.node), `: ${changes(event)}`];
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
      return ['finished their work on ', node(event.node), ` (${a.outcome}) and left this handoff note`];
    case 'rule_created':
      return ['wrote ', rule(event.rule_id), present(event.node) ? [', in ', node(event.node)] : ', in no story yet'];
    case 'rule_updated':
      return ['changed ', rule(event.rule_id), `: ${changes(event)}`];
    case 'rule_placed':
      return ['put ', rule(event.rule_id), ' in ', node(a.story)];
    case 'rule_moved':
      return ['moved ', rule(event.rule_id), ' from ', story(b.story), ' to ', story(a.story)];
    default:
      if (FACT_ACTIONS.includes(event.action)) return describeRecord('fact', event, a);
      if (DECISION_ACTIONS.includes(event.action)) return describeRecord('decision', event, a);
      return [`${event.action.replace(/_/g, ' ')} `, subject(event)];
  }
}

// The row of the feed: an icon for the kind of change, who, what, when, and a handoff note in full.
export function eventRow(event) {
  const kind = kindOfAction(event.action);
  const label = (CHANGE_KINDS.find(([value]) => value === kind) || [])[1] || 'Other change';
  return h(
    'li',
    { class: `event action-${event.action}`, dataset: { event: event.id, action: event.action, actor: event.actor, kind } },
    h('span', { class: `event-icon kind-${kind}`, title: label }, icon(CHANGE_ICONS[kind] || 'info', { size: 14 })),
    h(
      'div',
      { class: 'event-body' },
      h('p', { class: 'event-line' }, h('strong', { class: 'event-actor' }, event.actor), ' ', h('span', { class: 'event-words' }, ...describe(event), '.')),
      h('time', { class: 'quiet event-time', datetime: event.at }, moment(event.at)),
      event.action === 'handoff' ? h('div', { class: 'handoff-note' }, renderMarkdown(event.after.note || '', { className: 'markdown' })) : null,
    ),
  );
}

// Whether an event is about the item a person is following: `item` is { ref } for a piece of work,
// { kind, id } for a requirement, decision or fact, or null.
export function aboutItem(event, item) {
  if (!item) return true;
  if (item.ref) return event.node === item.ref || event.other_node === item.ref;
  if (item.kind === 'rule') return event.rule_id === item.id;
  if (item.kind === 'decision') return event.decision_id === item.id;
  return event.fact_id === item.id;
}
