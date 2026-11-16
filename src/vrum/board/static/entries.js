// How a fact or a decision is shown. The detail panel, the recall sections and the lists all use
// these pieces, so a record reads the same wherever it is. Confirmation and lifecycle are always
// said in words from the record's own fields: an unconfirmed record is never shown as confirmed,
// and a replaced or withdrawn one is visibly ended. Everything is put in as text.

import { day, h, moment } from './dom.js';
import { clip, uid } from './forms.js';
import { icon } from './icons.js';
import { kindIcon } from './labels.js';
import { renderMarkdown } from './markdown.js';
import { itemLink, kindWords, openLink } from './references.js';
import { store } from './store.js';

export const LIFECYCLE = { active: 'In force', superseded: 'Replaced', withdrawn: 'Withdrawn' };
// How each lifecycle looks: its own icon, and a tone that says "this one counts" or "it does not".
const LIFECYCLE_LOOK = { active: ['ready', 'info'], superseded: ['arrow_right', ''], withdrawn: ['dropped', 'danger'] };

// A fact or a decision, from its fields (a decision has a reason, a fact has a source).
export const recordKind = (record) =>
  record.kind === 'fact' || record.kind === 'decision' ? record.kind : 'reason' in record ? 'decision' : 'fact';

export const isActive = (record) => record.status === 'active';

// What to call it in a list: a decision's title, a fact's text.
export const recordTitle = (record) => (recordKind(record) === 'decision' ? record.title : clip(record.text, 140));

const tableOf = (kind) => (kind === 'decision' ? store.decisions : store.facts) || [];

export function kindChip(record) {
  const kind = recordKind(record);
  return h('span', { class: `chip kind kind-${kind}` }, kindIcon(kind, 14), `${kindWords(kind).label} #${record.id}`);
}

export function lifecycleChip(record) {
  const [mark, tone] = LIFECYCLE_LOOK[record.status] || ['info', ''];
  return h('span', { class: `chip lifecycle lifecycle-${record.status} ${tone}`.trim(), dataset: { lifecycle: record.status } }, icon(mark, { size: 14 }), LIFECYCLE[record.status] || record.status);
}

export function confirmationChip(record) {
  const confirmed = record.confirmation === 'confirmed';
  return h(
    'span',
    { class: `chip confirmation confirmation-${confirmed ? 'confirmed' : 'unconfirmed'} ${confirmed ? 'confirmed' : 'unconfirmed'}`, dataset: { confirmation: confirmed ? 'confirmed' : 'unconfirmed' } },
    icon(confirmed ? 'confirmed' : 'unconfirmed', { size: 14 }),
    confirmed ? `Confirmed by ${record.confirmed_by}, ${day(record.confirmed_at)}` : 'Unconfirmed',
  );
}

export const projectWideChip = (record) =>
  record.project_wide ? h('span', { class: 'chip project-wide', title: 'It applies throughout the project, not only to what it is linked to' }, icon('graph', { size: 14 }), 'Project-wide') : null;

// The chips every record carries, in one place so that none is left out. The detail panel says what
// kind it is in its own heading, so it asks for the others only.
export const recordChips = (record, { kind = true } = {}) =>
  h('div', { class: 'record-chips' }, kind ? kindChip(record) : null, projectWideChip(record), lifecycleChip(record), confirmationChip(record));

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
  return openLink('rule', id, rule ? `requirement #${id} ${clip(rule.title, 50)}` : `requirement #${id}`);
}

function decisionLink(id) {
  const decision = (store.decisions || []).find((item) => item.id === id);
  return openLink('decision', id, decision ? `decision #${id} ${clip(decision.title, 50)}` : `decision #${id}`);
}

// What it concerns, as links: the epics, stories and tasks, the requirements, and for a fact the
// decisions. Each with the icon of its kind, for the lines of the detail panel.
const concernedItems = (record) => [
  ...(record.node_ids || []).map((id) => [store.nodes.get(id)?.kind || 'story', itemLink(id)]),
  ...(record.rule_ids || []).map((id) => ['rule', ruleLink(id)]),
  ...(record.decision_ids || []).map((id) => ['decision', decisionLink(id)]),
];

// A short line: "Concerns: a, b".
export function concerns(record) {
  const found = concernedItems(record).map(([, link]) => link);
  return found.length
    ? h('p', { class: 'concerns' }, h('span', { class: 'quiet' }, 'Concerns: '), joined(found))
    : h('p', { class: 'quiet concerns' }, 'Concerns nothing yet.');
}

// The same as a list, one item to a line, and the words for a project-wide decision. A panel's
// "Applies to".
export function concernsList(record) {
  const found = concernedItems(record);
  const wide = record.project_wide ? h('li', { class: 'concern-wide' }, icon('graph', { size: 14 }), 'The whole project') : null;
  if (!found.length && !wide) return h('p', { class: 'quiet concerns' }, 'Nothing yet.');
  return h('ul', { class: 'concern-list' }, wide, found.map(([kind, link]) => h('li', {}, kindIcon(kind, 14), link)));
}

// How it ended, and what replaced what: the chain ends in what is in force now.
export function lifecycleLines(record) {
  const kind = recordKind(record);
  const words = kindWords(kind).label.toLowerCase();
  const lines = [];
  if ((record.supersedes_ids || []).length) {
    lines.push(h('p', { class: 'quiet replaces' }, `Replaces ${words}${record.supersedes_ids.length === 1 ? '' : 's'} `, joined(record.supersedes_ids.map((id) => openLink(kind, id, `#${id}`))), '.'));
  }
  if (record.status === 'superseded') {
    const steps = [];
    const seen = new Set([record.id]);
    const byId = new Map(tableOf(kind).map((entry) => [entry.id, entry]));
    let current = record;
    while (current && current.superseded_by_id && !seen.has(current.superseded_by_id)) {
      seen.add(current.superseded_by_id);
      steps.push(openLink(kind, current.superseded_by_id, `#${current.superseded_by_id}`));
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
      renderMarkdown(record.text, { className: 'markdown record-text' }),
      h('div', { class: 'reason' }, h('span', { class: 'quiet' }, 'Why: '), renderMarkdown(record.reason, { className: 'markdown inline-markdown record-reason' })),
      h('p', { class: 'quiet who' }, `Decided by ${record.decided_by} on ${day(record.decided_on)}. Recorded ${moment(record.recorded_at)}.`),
    ];
  }
  return [
    renderMarkdown(record.text, { className: 'markdown record-text' }),
    h('div', { class: 'quiet source' }, 'Source: ', renderMarkdown(record.source, { className: 'markdown inline-markdown record-source' })),
    h('p', { class: 'quiet who' }, `Recorded by ${record.recorded_by} on ${moment(record.recorded_at)}.`),
  ];
}

// Who put a record in, and when, one line each: the Attribution of the detail panel. A decision does
// not say who typed it, only when. A date is kept in one piece.
export function attribution(record) {
  if (recordKind(record) === 'decision') {
    return [h('span', { class: 'who' }, `Decided by ${record.decided_by} on `, h('span', { class: 'nowrap' }, day(record.decided_on)), '.'), h('span', { class: 'who' }, `Recorded ${moment(record.recorded_at)}.`)];
  }
  return h('span', { class: 'who' }, `Recorded by ${record.recorded_by} on ${moment(record.recorded_at)}.`);
}

// One fact or decision, whole, as a list item. `why` is the reason recall gives for it; `actions`
// is what a page adds below it.
export function recordEntry(record, { why = null, actions = null } = {}) {
  const kind = recordKind(record);
  const description = describedBy(record);
  return h(
    'li',
    {
      class: `record ${kind}${isActive(record) ? '' : ' ended'}`,
      id: `record-${kind}-${record.id}`,
      'aria-describedby': description.id,
      dataset: { record: `${kind}:${record.id}`, kind, status: record.status, confirmation: record.confirmation === 'confirmed' ? 'confirmed' : 'unconfirmed', projectWide: String(Boolean(record.project_wide)) },
    },
    description.note,
    recordChips(record),
    kind === 'decision' ? h('h4', { class: 'record-heading' }, openLink('decision', record.id, record.title)) : null,
    ...recordBody(record),
    h('p', { class: 'confirmation-words quiet' }, confirmationWords(record)),
    ...lifecycleLines(record),
    concerns(record),
    kind === 'fact' ? h('p', { class: 'open-record' }, openLink('fact', record.id, 'Open fact')) : null,
    whyLine(why),
    actions,
  );
}
