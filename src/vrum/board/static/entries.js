// How a decision, a principle, a fact or a belief is shown. The Memory page and the recall preview
// both use these, so an entry reads the same wherever it is. Everything is put in as text.

import { h, day, moment, plural } from './dom.js';
import { itemLink } from './card.js';
import { clip } from './forms.js';
import { KIND } from './labels.js';
import { store } from './store.js';

const ORIGIN_WORDS = {
  stated: 'Stated by a person or an agent',
  extracted: 'Proposed by a language model from a quoted passage',
  derived: 'Drawn up by a language model from evidence',
};
const STATUS_WORDS = { active: 'In force', superseded: 'Superseded', retracted: 'Retracted' };

export const ruleLink = (id) => h('a', { href: `#/rules?rule=${id}`, class: 'item-link', dataset: { rule: id } }, `rule ${id}`);

// A link to an entry on the Memory page. It clears the filters, so that it can never point at an
// entry that the filters hide.
export const entryLink = (type, id, text = null) =>
  h('a', { href: `#/memory?entry=${type}:${id}`, class: 'entry-link', dataset: { goto: `${type}:${id}` } }, text || `${type} #${id}`);

const joined = (nodes) => nodes.flatMap((node, at) => (at ? [', ', node] : [node]));

// What an item concerns, as links: the epics, stories and tasks, the rules, and for a fact the
// decisions.
function concerns(item) {
  const found = [
    ...item.node_ids.map((id) => itemLink(id)),
    ...item.rule_ids.map((id) => ruleLink(id)),
    ...(item.decision_ids || []).map((id) => entryLink('decision', id)),
  ];
  return found.length ? h('p', { class: 'concerns' }, h('span', { class: 'quiet' }, 'Concerns: '), joined(found)) : h('p', { class: 'quiet concerns' }, 'Concerns nothing yet.');
}

// The chain of what replaced what, ending in what is in force now (or in what was retracted).
function chainOf(item, table, type) {
  if (item.status === 'active') return null;
  if (item.status === 'retracted') return h('p', { class: 'chain quiet' }, 'Retracted: nothing replaced it.');
  const byId = new Map((table || []).map((entry) => [entry.id, entry]));
  const steps = [];
  const seen = new Set([item.id]);
  let current = item;
  while (current && current.superseded_by_id !== null && current.superseded_by_id !== undefined && !seen.has(current.superseded_by_id)) {
    const next = byId.get(current.superseded_by_id);
    seen.add(current.superseded_by_id);
    steps.push(entryLink(type, current.superseded_by_id, `#${current.superseded_by_id}`));
    current = next;
  }
  const last = current && current !== item ? current : null;
  const end = last ? (last.status === 'active' ? ' (in force now)' : ` (${last.status})`) : '';
  return h(
    'p',
    { class: 'chain' },
    h('span', { class: 'quiet' }, 'Replaced by '),
    steps.flatMap((step, at) => (at ? [' → ', step] : [step])),
    end,
  );
}

const statusChip = (status) => h('span', { class: `chip entry-status status-${status}` }, STATUS_WORDS[status] || status);

const why = (text) => (text ? h('p', { class: 'why' }, h('span', { class: 'quiet' }, 'Why it is here: '), h('span', { class: 'why-text' }, text)) : null);

export function decisionEntry(decision, { reason = null, actions = null, choose = null } = {}) {
  const principle = decision.kind === 'principle';
  const replaced = (decision.supersedes_ids || []).length ? h('p', { class: 'quiet' }, 'Replaced: ', joined(decision.supersedes_ids.map((id) => entryLink('decision', id, `#${id}`)))) : null;
  return h(
    'li',
    { class: `entry decision${decision.status === 'superseded' ? ' replaced' : ''}`, id: `entry-decision-${decision.id}`, dataset: { entry: `decision:${decision.id}`, decision: decision.id, status: decision.status, kind: decision.kind } },
    h(
      'div',
      { class: 'entry-head' },
      h('span', { class: `chip kind kind-${decision.kind}` }, h('span', { 'aria-hidden': 'true' }, KIND[decision.kind].sign), ` ${KIND[decision.kind].label} #${decision.id}`),
      statusChip(decision.status),
      principle ? h('span', { class: 'chip' }, 'Applies everywhere') : null,
      decision.promoted_from_id ? h('span', { class: 'chip' }, 'Promoted from belief ', entryLink('belief', decision.promoted_from_id, `#${decision.promoted_from_id}`)) : null,
    ),
    h('h4', { class: 'entry-title' }, decision.title),
    h('p', { class: 'entry-text' }, decision.text),
    h('p', { class: 'reason' }, h('span', { class: 'quiet' }, 'Why: '), decision.reason),
    h('p', { class: 'quiet who' }, `Decided by ${decision.decided_by} on ${day(decision.decided_on)}.`),
    concerns(decision),
    replaced,
    chainOf(decision, store.decisions, 'decision'),
    why(reason),
    choose,
    actions,
  );
}

function evidenceLine(evidence) {
  const type = evidence.type === 'decision' ? 'decision' : 'fact';
  const table = type === 'decision' ? store.decisions : store.memories;
  const found = (table || []).find((entry) => entry.id === evidence.id);
  const words = found ? clip(type === 'decision' ? found.title : found.text, 70) : '';
  return h(
    'li',
    { class: `evidence${evidence.status === 'active' ? '' : ' changed'}`, dataset: { evidence: `${type}:${evidence.id}`, status: evidence.status } },
    entryLink(type, evidence.id, `${type === 'decision' ? 'Decision' : 'Fact'} #${evidence.id}`),
    words ? `: ${words}` : '',
    ' ',
    evidence.status === 'active' ? h('span', { class: 'chip' }, 'in force') : h('span', { class: 'chip stale' }, `! ${evidence.status}${evidence.reason ? `: ${evidence.reason}` : ''}`),
  );
}

export function memoryEntry(memory, { reason = null, actions = null, choose = null } = {}) {
  const belief = memory.type === 'belief';
  const modelMade = memory.origin !== 'stated';
  const replaced = (memory.supersedes_ids || []).length ? h('p', { class: 'quiet' }, 'Replaced: ', joined(memory.supersedes_ids.map((id) => entryLink(memory.type, id, `#${id}`)))) : null;
  return h(
    'li',
    { class: `entry ${memory.type}${memory.status === 'active' ? '' : ' replaced'}${memory.stale ? ' stale' : ''}`, id: `entry-${memory.type}-${memory.id}`, dataset: { entry: `${memory.type}:${memory.id}`, memory: memory.id, type: memory.type, status: memory.status, stale: String(memory.stale), origin: memory.origin } },
    h(
      'div',
      { class: 'entry-head' },
      h('span', { class: `chip kind kind-${memory.type}` }, h('span', { 'aria-hidden': 'true' }, KIND[memory.type].sign), ` ${KIND[memory.type].label} #${memory.id}${memory.kind ? ` (${memory.kind})` : ''}`),
      belief ? h('span', { class: 'chip suggestion' }, '◇ Suggestion, not a decision') : null,
      memory.status === 'active' ? null : statusChip(memory.status),
      memory.stale ? h('span', { class: 'chip stale' }, '! Stale: its evidence changed') : null,
      modelMade ? h('span', { class: 'chip model' }, memory.origin === 'derived' ? 'Drawn up by a language model' : 'Proposed by a language model') : null,
      belief && memory.confirmed_by ? h('span', { class: 'chip confirmed' }, `✓ Confirmed by ${memory.confirmed_by}`) : null,
    ),
    h('p', { class: 'entry-text' }, memory.text),
    h(
      'p',
      { class: 'quiet who' },
      `${ORIGIN_WORDS[memory.origin] || memory.origin}. Recorded by ${memory.recorded_by} on ${day(memory.recorded_at)}.`,
      belief ? '' : ` Source: ${memory.source}.`,
      memory.happened_on ? ` It was so on ${day(memory.happened_on)}.` : '',
    ),
    belief
      ? h(
          'div',
          { class: 'evidence-block' },
          h('p', { class: 'quiet' }, `Rests on ${plural(memory.evidence_count, 'piece')} of evidence:`),
          h('ul', { class: 'plain evidence-list' }, memory.evidence.map(evidenceLine)),
          memory.confirmed_by ? h('p', { class: 'quiet' }, `Confirmed by ${memory.confirmed_by} on ${moment(memory.confirmed_at)}.`) : h('p', { class: 'quiet' }, memory.status === 'active' ? 'Nobody has confirmed it.' : ''),
          memory.promoted_decision_ids.length ? h('p', { class: 'quiet' }, 'Made into decisions: ', joined(memory.promoted_decision_ids.map((id) => entryLink('decision', id, `#${id}`)))) : null,
        )
      : null,
    memory.stale ? h('p', { class: 'stale-why' }, h('strong', {}, 'Which evidence changed: '), memory.stale_because.join(' ')) : null,
    memory.status_reason ? h('p', { class: 'quiet' }, `${memory.status === 'retracted' ? 'Retracted' : 'Replaced'} because: ${memory.status_reason}`) : null,
    concerns(memory),
    replaced,
    chainOf(memory, store.memories, memory.type),
    why(reason),
    choose,
    actions,
  );
}
