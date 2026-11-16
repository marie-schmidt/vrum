// What happened, in plain words. One change event of the history becomes a sentence: who did what
// to which item, with the item named by its kind, id and current title and linked to where it is
// shown. Used by the Activity page. Every event the server can write is covered, and one it does
// not know is shown by its name rather than hidden.

import { h, moment } from './dom.js';
import { renderMarkdown } from './markdown.js';
import { KIND, LINK, STATUS } from './labels.js';
import { hrefFor, resolve } from './references.js';
import { store } from './store.js';

const clip = (text, size = 60) => {
  const one = String(text ?? '').replace(/\s+/g, ' ').trim();
  return one.length > size ? `${one.slice(0, size - 1)}…` : one;
};

// What can be filtered by: the actions of the history in the groups a person thinks in.
export const CHANGE_KINDS = [
  ['work', 'Work items', ['create', 'update', 'move']],
  ['links', 'Links', ['link_added', 'link_removed']],
  ['claims', 'Claims', ['claimed', 'claim_renewed', 'claim_released']],
  ['handoffs', 'Handoff notes', ['handoff']],
  ['rules', 'Rules', ['rule_created', 'rule_updated', 'rule_placed', 'rule_moved']],
  ['decisions', 'Decisions', ['decision_recorded', 'decision_superseded', 'decision_linked', 'decision_unlinked']],
  ['memory', 'Facts and beliefs', ['memory_recorded', 'memory_superseded', 'memory_retracted', 'memory_linked', 'memory_unlinked', 'memory_confirmed', 'memory_promoted']],
  ['model', 'Language model calls', ['model_call']],
];

export const kindOfAction = (action) => (CHANGE_KINDS.find(([, , actions]) => actions.includes(action)) || [])[0] || 'other';

// A named item as a link: "story #12 “Log in”", "rule 4 “Short codes”", "belief 2 “…”".
function named(kind, id) {
  if (id === null || id === undefined) return 'an item';
  let words;
  let linkKind = kind;
  if (kind === 'node') {
    const found = resolve('node', id);
    words = found.exists ? `${KIND[found.type].label.toLowerCase()} #${id} “${clip(found.title)}”` : `item #${id}`;
  } else if (kind === 'rule') {
    const found = resolve('rule', id);
    words = found.exists && found.title ? `rule ${id} “${clip(found.title)}”` : `rule ${id}`;
  } else if (kind === 'decision') {
    const found = (store.decisions || []).find((item) => item.id === id);
    words = found ? `${found.kind} ${id} “${clip(found.title)}”` : `decision ${id}`;
  } else {
    const found = (store.memories || []).find((item) => item.id === id);
    linkKind = found ? found.type : 'fact';
    words = found ? `${found.type} ${id} “${clip(found.text)}”` : `fact or belief ${id}`;
  }
  return h('a', { class: 'item-link', href: hrefFor(linkKind, id), dataset: { kind: linkKind, id: String(id) } }, words);
}

const node = (id) => named('node', id);
const rule = (id) => named('rule', id);
const decision = (id) => named('decision', id);
const memory = (id) => named('memory', id);

// Whatever the event names as its subject, whichever kind of thing that is.
function subject(event) {
  if (event.rule_id !== null && event.rule_id !== undefined) return rule(event.rule_id);
  if (event.decision_id !== null && event.decision_id !== undefined) return decision(event.decision_id);
  if (event.memory_id !== null && event.memory_id !== undefined) return memory(event.memory_id);
  return node(event.node_id);
}

// What a link or a concern points at: the node, rule or decision the event is also about.
function target(event) {
  if (event.node_id !== null && event.node_id !== undefined) return node(event.node_id);
  if (event.rule_id !== null && event.rule_id !== undefined) return rule(event.rule_id);
  if (event.decision_id !== null && event.decision_id !== undefined) return decision(event.decision_id);
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
    case 'decision_recorded':
      return ['recorded ', decision(event.decision_id), `, decided by ${a.decided_by}`];
    case 'decision_superseded':
      return ['replaced ', decision(event.decision_id), ' with ', decision(a.superseded_by_id)];
    case 'decision_linked':
      return ['linked ', decision(event.decision_id), ' to ', target(event)];
    case 'decision_unlinked':
      return ['unlinked ', decision(event.decision_id), ' from ', target(event)];
    case 'memory_recorded':
      return ['recorded ', memory(event.memory_id), a.origin && a.origin !== 'stated' ? ` (${a.origin === 'extracted' ? 'proposed' : 'drawn up'} by a language model)` : ''];
    case 'memory_superseded':
      return ['replaced ', memory(event.memory_id), ' with ', memory(a.superseded_by_id), a.reason ? `: ${clip(a.reason, 80)}` : ''];
    case 'memory_retracted':
      return ['retracted ', memory(event.memory_id), a.reason ? `: ${clip(a.reason, 80)}` : ''];
    case 'memory_linked':
      return ['linked ', memory(event.memory_id), ' to ', target(event)];
    case 'memory_unlinked':
      return ['unlinked ', memory(event.memory_id), ' from ', target(event)];
    case 'memory_confirmed':
      return [a.confirmed_by && a.confirmed_by !== event.actor ? `recorded that ${a.confirmed_by} confirmed ` : 'confirmed ', memory(event.memory_id)];
    case 'memory_promoted':
      return ['made ', memory(event.memory_id), ' a decision: ', decision(a.decision_id), `, decided by ${a.decided_by}`];
    case 'model_call':
      return [`asked the language model${a.model ? ` (${a.model})` : ''}${a.asked ? `: “${clip(a.asked, 100)}”` : ''}${a.used ? `, which looked at ${a.used.length} item${a.used.length === 1 ? '' : 's'}` : ''}`];
    default:
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
  return event.memory_id === item.id;
}
