// How a fact or a decision is shown. The detail panel, the recall sections and the lists all use
// these pieces, so a record reads the same wherever it is. Confirmation and lifecycle are always
// said in words from the record's own fields: an unconfirmed record is never shown as confirmed,
// and a replaced or withdrawn one is visibly ended. Everything is put in as text.

import { day, h, moment } from './dom.js';
import { clip, uid } from './forms.js';
import { renderMarkdown } from './markdown.js';
import { itemLink, kindWords, recordLink } from './references.js';
import { store } from './store.js';

export const LIFECYCLE = { active: 'In force', superseded: 'Replaced', withdrawn: 'Withdrawn' };

// A fact or a decision, from its fields (a decision has a reason, a fact has a source).
export const recordKind = (record) =>
  record.kind === 'fact' || record.kind === 'decision' ? record.kind : 'reason' in record ? 'decision' : 'fact';

export const isActive = (record) => record.status === 'active';

// What to call it in a list: a decision's title, a fact's text.
export const recordTitle = (record) => (recordKind(record) === 'decision' ? record.title : clip(record.text, 140));

const tableOf = (kind) => (kind === 'decision' ? store.decisions : store.facts) || [];

export function kindChip(record) {
  const kind = recordKind(record);
  const words = kindWords(kind);
  return h('span', { class: `chip kind kind-${kind}` }, h('span', { 'aria-hidden': 'true' }, words.sign), ` ${words.label} #${record.id}`);
}

export const lifecycleChip = (record) =>
  h('span', { class: `chip lifecycle lifecycle-${record.status}`, dataset: { lifecycle: record.status } }, LIFECYCLE[record.status] || record.status);

export function confirmationChip(record) {
  const confirmed = record.confirmation === 'confirmed';
  return h(
    'span',
    { class: `chip confirmation confirmation-${confirmed ? 'confirmed' : 'unconfirmed'}`, dataset: { confirmation: confirmed ? 'confirmed' : 'unconfirmed' } },
    h('span', { 'aria-hidden': 'true' }, confirmed ? '✓' : '○'),
    confirmed ? ` Confirmed by ${record.confirmed_by}, ${day(record.confirmed_at)}` : ' Unconfirmed',
  );
}

export const projectWideChip = (record) =>
  record.project_wide ? h('span', { class: 'chip project-wide', title: 'It applies throughout the project, not only to what it is linked to' }, '◎ Project-wide') : null;

// The chips every record carries, in one place so that none is left out.
export const recordChips = (record) =>
  h('div', { class: 'record-chips entry-head' }, kindChip(record), projectWideChip(record), lifecycleChip(record), confirmationChip(record));

export function confirmationWords(record) {
  if (record.confirmation === 'confirmed') return `Confirmed by ${record.confirmed_by} on ${moment(record.confirmed_at)}.`;
  return isActive(record) ? 'Not confirmed: no person has reviewed it yet.' : 'It was never confirmed.';
}

// The plain-words label the server gives, for a screen reader to read as the description.
export function describedBy(record) {
  const note = h('span', { class: 'sr-only', id: uid('record-label-') }, record.label || '');
  return { note, id: note.id };
}

const joined = (nodes) => nodes.flatMap((node, at) => (at ? [', ', node] : [node]));

function ruleLink(id) {
  const rule = (store.rules || []).find((item) => item.id === id);
  return recordLink('rule', id, rule ? `requirement #${id} ${clip(rule.title, 50)}` : `requirement #${id}`);
}

function decisionLink(id) {
  const decision = (store.decisions || []).find((item) => item.id === id);
  return recordLink('decision', id, decision ? `decision #${id} ${clip(decision.title, 50)}` : `decision #${id}`);
}

// What it concerns, as links: the epics, stories and tasks, the requirements, and for a fact the
// decisions.
export function concerns(record) {
  const found = [
    ...(record.node_ids || []).map((id) => itemLink(id)),
    ...(record.rule_ids || []).map(ruleLink),
    ...(record.decision_ids || []).map(decisionLink),
  ];
  return found.length
    ? h('p', { class: 'concerns' }, h('span', { class: 'quiet' }, 'Concerns: '), joined(found))
    : h('p', { class: 'quiet concerns' }, 'Concerns nothing yet.');
}

// How it ended, and what replaced what: the chain ends in what is in force now.
export function lifecycleLines(record) {
  const kind = recordKind(record);
  const words = kindWords(kind).label.toLowerCase();
  const lines = [];
  if ((record.supersedes_ids || []).length) {
    lines.push(h('p', { class: 'quiet replaces' }, `Replaces ${words}${record.supersedes_ids.length === 1 ? '' : 's'} `, joined(record.supersedes_ids.map((id) => recordLink(kind, id, `#${id}`))), '.'));
  }
  if (record.status === 'superseded') {
    const steps = [];
    const seen = new Set([record.id]);
    const byId = new Map(tableOf(kind).map((entry) => [entry.id, entry]));
    let current = record;
    while (current && current.superseded_by_id && !seen.has(current.superseded_by_id)) {
      seen.add(current.superseded_by_id);
      steps.push(recordLink(kind, current.superseded_by_id, `#${current.superseded_by_id}`));
      current = byId.get(current.superseded_by_id);
    }
    const last = current && current !== record ? current : null;
    const end = last ? (last.status === 'active' ? ' (in force now)' : ` (${(LIFECYCLE[last.status] || last.status).toLowerCase()})`) : '';
    lines.push(h('p', { class: 'chain' }, h('strong', {}, 'Replaced'), ' by ', joined(steps), end, '.'));
  }
  if (record.status !== 'active' && record.status_reason) {
    lines.push(h('p', { class: 'ended-reason' }, h('strong', {}, record.status === 'withdrawn' ? 'Withdrawn: ' : 'Why it was replaced: '), record.status_reason));
  } else if (record.status === 'withdrawn') {
    lines.push(h('p', { class: 'ended-reason' }, h('strong', {}, 'Withdrawn.')));
  }
  return lines;
}

const whyLine = (text) =>
  text ? h('p', { class: 'why' }, h('span', { class: 'quiet' }, 'Why it is here: '), h('span', { class: 'why-text' }, text)) : null;

// The words of the record itself: a decision's text and reason and who decided it, or a fact's text
// and source and who recorded it.
export function recordBody(record) {
  if (recordKind(record) === 'decision') {
    return [
      renderMarkdown(record.text, { className: 'markdown entry-text record-text' }),
      h('div', { class: 'reason' }, h('span', { class: 'quiet' }, 'Why: '), renderMarkdown(record.reason, { className: 'markdown inline-markdown record-reason' })),
      h('p', { class: 'quiet who' }, `Decided by ${record.decided_by} on ${day(record.decided_on)}. Recorded ${moment(record.recorded_at)}.`),
    ];
  }
  return [
    renderMarkdown(record.text, { className: 'markdown entry-text record-text' }),
    h('div', { class: 'quiet source' }, 'Source: ', renderMarkdown(record.source, { className: 'markdown inline-markdown record-source' })),
    h('p', { class: 'quiet who' }, `Recorded by ${record.recorded_by} on ${moment(record.recorded_at)}.`),
  ];
}

// One fact or decision, whole, as a list item. `why` is the reason recall gives for it; `actions`
// is what a page adds below it.
export function recordEntry(record, { why = null, actions = null } = {}) {
  const kind = recordKind(record);
  const description = describedBy(record);
  return h(
    'li',
    {
      class: `record entry ${kind}${isActive(record) ? '' : ' ended'}`,
      id: `record-${kind}-${record.id}`,
      'aria-describedby': description.id,
      dataset: { record: `${kind}:${record.id}`, kind, status: record.status, confirmation: record.confirmation === 'confirmed' ? 'confirmed' : 'unconfirmed', projectWide: String(Boolean(record.project_wide)) },
    },
    description.note,
    recordChips(record),
    kind === 'decision' ? h('h4', { class: 'entry-title' }, recordLink('decision', record.id, record.title)) : null,
    ...recordBody(record),
    h('p', { class: 'confirmation-words quiet' }, confirmationWords(record)),
    ...lifecycleLines(record),
    concerns(record),
    kind === 'fact' ? h('p', { class: 'open-record' }, recordLink('fact', record.id, 'Open fact')) : null,
    whyLine(why),
    actions,
  );
}
