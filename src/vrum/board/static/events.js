// What happened, in plain words. One change event of the history becomes a sentence: who did what
// to which item, with the item named by its kind, id and current title and linked to where it is
// shown (the detail panel). Used by the Activity drawer. Every event the server can write is
// covered, and one it does not know is shown by its name rather than hidden.

import { h, moment } from './dom.js';
import { recordLink } from './ctx.js';
import { renderMarkdown } from './markdown.js';
import { KIND, LINK, STATUS } from './labels.js';
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

// A named item as a link: "story #12 “Log in”", "requirement 4 “Short codes”", "fact 2 “…”".
function named(kind, id) {
  if (id === null || id === undefined) return 'an item';
  let words;
  if (kind === 'node') {
    const found = store.nodes.get(id);
    words = found ? `${KIND[found.kind].label.toLowerCase()} #${id} “${clip(found.title)}”` : `item #${id}`;
  } else if (kind === 'rule') {
    const found = (store.rules || []).find((item) => item.id === id);
    words = found && found.title ? `requirement ${id} “${clip(found.title)}”` : `requirement ${id}`;
  } else if (kind === 'decision') {
    const found = (store.decisions || []).find((item) => item.id === id);
    words = found ? `decision ${id} “${clip(found.title)}”` : `decision ${id}`;
  } else {
    const found = (store.facts || []).find((item) => item.id === id);
    words = found ? `fact ${id} “${clip(found.text)}”` : `fact ${id}`;
  }
  return recordLink(kind, id, { class: 'item-link', dataset: { kind, id: String(id) } }, words);
}

const node = (id) => named('node', id);
const rule = (id) => named('rule', id);
const decision = (id) => named('decision', id);
const fact = (id) => named('fact', id);

const present = (value) => value !== null && value !== undefined;

// Whatever the event names as its subject, whichever kind of thing that is. A fact or decision
// event may also name what it concerns, so the record comes before what it is linked to.
function subject(event) {
  if (present(event.fact_id)) return fact(event.fact_id);
  if (present(event.decision_id)) return decision(event.decision_id);
  if (present(event.rule_id)) return rule(event.rule_id);
  return node(event.node_id);
}

// What a link or a concern points at: the node, requirement or decision the event is also about.
function target(event) {
  if (present(event.node_id)) return node(event.node_id);
  if (present(event.rule_id)) return rule(event.rule_id);
  if (present(event.decision_id)) return decision(event.decision_id);
  return 'an item';
}

const FIELD = { title: 'title', body: 'details', status: 'status', priority: 'priority', start_on: 'start day', target_on: 'target day', parent_id: 'parent', story_id: 'story' };
const value = (key, text) => {
  if (text === null || text === undefined || text === '') return 'empty';
  if (key === 'status') return STATUS[text]?.label?.toLowerCase() || text;
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

function story(id) {
  return id === null || id === undefined ? 'no story' : node(id);
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
      return ['created ', node(event.node_id)];
    case 'update':
      return ['changed ', node(event.node_id), `: ${changes(event)}`];
    case 'move':
      return ['moved ', node(event.node_id), ' from ', node(b.parent_id), ' to ', node(a.parent_id)];
    case 'link_added':
    case 'link_removed': {
      const verb = LINK[(event.action === 'link_added' ? a : b).link_type]?.label || 'is linked to';
      return [event.action === 'link_added' ? 'added a link: ' : 'removed a link: ', node(event.node_id), ` ${verb} `, node(event.other_node_id)];
    }
    case 'claimed':
      return ['took ', node(event.node_id), ` until ${moment(a.expires_at)}`];
    case 'claim_renewed':
      return ['kept ', node(event.node_id), ` for longer, until ${moment(a.expires_at)}`];
    case 'claim_released':
      return ['let go of ', node(event.node_id)];
    case 'handoff':
      return ['finished their work on ', node(event.node_id), ` (${a.outcome}) and left this handoff note`];
    case 'rule_created':
      return ['wrote ', rule(event.rule_id), event.node_id === null ? ', in no story yet' : [', in ', node(event.node_id)]];
    case 'rule_updated':
      return ['changed ', rule(event.rule_id), `: ${changes(event)}`];
    case 'rule_placed':
      return ['put ', rule(event.rule_id), ' in ', node(a.story_id)];
    case 'rule_moved':
      return ['moved ', rule(event.rule_id), ' from ', story(b.story_id), ' to ', story(a.story_id)];
    default:
      if (FACT_ACTIONS.includes(event.action)) return describeRecord('fact', event, a);
      if (DECISION_ACTIONS.includes(event.action)) return describeRecord('decision', event, a);
      return [`${event.action.replace(/_/g, ' ')} `, subject(event)];
  }
}

// The row of the feed: when, who, what, and a handoff note in full.
export function eventRow(event) {
  return h(
    'li',
    { class: `event action-${event.action}`, dataset: { event: event.id, action: event.action, actor: event.actor, kind: kindOfAction(event.action) } },
    h('span', { class: 'quiet event-time' }, `${moment(event.at)} `),
    h('strong', { class: 'event-actor' }, event.actor),
    ' ',
    h('span', { class: 'event-words' }, ...describe(event), '.'),
    event.action === 'handoff' ? h('div', { class: 'handoff-note' }, renderMarkdown(event.after.note || '', { className: 'markdown' })) : null,
  );
}

// Whether an event is about the item a person is following: `item` is { kind, id } or null.
export function aboutItem(event, item) {
  if (!item) return true;
  if (item.kind === 'node') return event.node_id === item.id || event.other_node_id === item.id;
  if (item.kind === 'rule') return event.rule_id === item.id;
  if (item.kind === 'decision') return event.decision_id === item.id;
  return event.fact_id === item.id;
}
