// The context of a change on the review page (M52): what a person needs to know about the work it is about before deciding, read only.
// Where the work sits (the epic and story above it, each with its level), its status, priority and size, who holds it and where the work is
// (branch, commit, pull request), its details as read Markdown (clipped, with Show all), the acceptance criteria it has and its own story's,
// the checks of the definition of done with the ticks that exist, the handoff note it was left with and its last few changes. A part with
// nothing to show is not drawn. Nothing here can be changed: the decision is the bar's.

import { actorLabel, codeOf, itemSizeChip, plainRequirementStatus, priorityChip, ruleCode, statusChip, checkChip, kindIcon } from './labels.js';
import { itemLink } from './ctx.js';
import { h, moment } from './dom.js';
import { icon } from './icons.js';
import { describe } from './events.js';
import { renderMarkdown } from './markdown.js';
import { levelChip } from './needs-cards.js';
import { stateStrip } from './records.js';
import { store } from './store.js';
import { whereBlock } from './whereabouts.js';

const LONG_DETAILS = 600; // characters of details drawn before "Show all"

// The epic and story above the work, from the top, each as its level, code and title.
function pathOf(node) {
  const above = [];
  for (let up = node.parent; up; up = store.nodes.get(up)?.parent) above.unshift(up);
  const rows = above.map((ref) => store.nodes.get(ref)).filter(Boolean);
  if (!rows.length) return null;
  return h(
    'ol',
    { class: 'plain context-path', 'aria-label': 'Where it sits' },
    rows.map((one) => h('li', { dataset: { ref: one.ref, level: one.kind } }, levelChip(one.kind), itemLink(one.ref, { class: 'item-link', dataset: { ref: one.ref } }, h('span', { class: 'item-code' }, codeOf(one.ref)), ' ', h('span', { dataset: { words: 'theirs' } }, one.title)))),
  );
}

// The details, read as Markdown. Long ones are clipped and say so with a button that shows all of it.
function detailsOf(node) {
  if (!node.body || !node.body.trim()) return null;
  const body = renderMarkdown(node.body, { className: 'markdown context-body' });
  body.dataset.words = 'theirs';
  const long = node.body.length > LONG_DETAILS || node.body.split('\n').length > 10;
  const box = h('div', { class: 'context-details', dataset: { clipped: long ? 'true' : null } }, body);
  if (!long) return box;
  const more = h('button', {
    type: 'button',
    class: 'small ghost context-more',
    'aria-expanded': 'false',
    onclick: () => {
      const open = box.dataset.clipped !== 'true';
      if (open) box.dataset.clipped = 'true';
      else delete box.dataset.clipped;
      more.setAttribute('aria-expanded', String(!open));
      more.textContent = open ? 'Show all' : 'Show less';
    },
  }, 'Show all');
  return [box, more];
}

// The acceptance criteria on the work and, for a task, on its story, each with whether it is met.
function criteriaOf(node) {
  const here = (store.rules || []).filter((rule) => rule.node === node.ref).sort((a, b) => a.id - b.id);
  const story = node.kind === 'task' ? node.parent : null;
  const above = story ? (store.rules || []).filter((rule) => rule.node === story && rule.status !== 'dropped').sort((a, b) => a.id - b.id) : [];
  if (!here.length && !above.length) return null;
  const line = (rule, from) =>
    h('li', { class: 'context-criterion', dataset: { rule: rule.id, status: rule.status, from } }, plainRequirementStatus(rule.status), ' ', itemLink(`rule:${rule.id}`, { class: 'item-link', dataset: { rule: rule.id } }, h('span', { class: 'item-code' }, ruleCode(rule.id)), ' ', h('span', { dataset: { words: 'theirs' } }, rule.title)));
  return h('ul', { class: 'plain context-criteria', 'aria-label': 'Acceptance criteria' }, [...here.map((rule) => line(rule, null)), ...above.map((rule) => line(rule, 'story'))]);
}

// The checks of the definition of done: ticked or not, who reported each and how it was checked.
function checksOf(node) {
  const checks = Array.isArray(node.checks) ? node.checks : [];
  if (!checks.length) return null;
  return h(
    'ul',
    { class: 'plain context-checks', 'aria-label': 'Definition of done' },
    checks.map((check) =>
      h(
        'li',
        { class: 'context-check', dataset: { item: check.item, checked: String(check.checked), waiting: !check.checked && check.person_only ? 'true' : null } },
        icon(check.checked ? 'req_met' : 'req_open', { size: 14 }),
        ' ',
        h('span', { class: !check.checked && check.person_only ? 'diff-check-words waiting' : 'diff-check-words', dataset: { words: 'theirs' } }, check.text),
        ' ',
        checkChip(check, { size: 13 }),
        check.checked ? h('span', { class: 'diff-evidence quiet' }, ' ', actorLabel(check.checked_by, check.checked_by_kind), check.evidence ? [': ', h('span', { dataset: { words: 'theirs' } }, check.evidence)] : null) : null,
      ),
    ),
  );
}

// The handoff note the work was left with: who, when, how it ended, where the work was and the words.
function handoffOf(note) {
  if (!note) return null;
  let words = null;
  if (note.note) {
    words = renderMarkdown(note.note, { className: 'markdown handoff-text' });
    words.dataset.words = 'theirs';
  }
  return h('div', { class: 'context-handoff' }, h('p', { class: 'handoff-by' }, actorLabel(note.actor, note.actor_kind), `, ${moment(note.at)}, ${note.outcome}`), whereBlock(note), words);
}

// The last few changes of the work, newest first.
function recentOf(history) {
  if (!history.length) return null;
  return h(
    'ul',
    { class: 'plain context-history', 'aria-label': 'Last changes' },
    [...history]
      .slice(-5)
      .reverse()
      .map((event) => h('li', { dataset: { event: event.id, action: event.action } }, actorLabel(event.actor, event.actor_kind), ' ', h('span', { class: 'event-words', dataset: { words: 'theirs' } }, describe(event)), h('time', { class: 'quiet', datetime: event.at }, ` ${moment(event.at)}`))),
  );
}

// The part with its heading, or null when it has nothing. `key` is `data-part` of the part.
const part = (key, title, ...content) => (content.flat().some(Boolean) ? h('div', { class: 'context-part', dataset: { part: key } }, h('h3', {}, title), content) : null);

// The context of the work a change is about. `node` is what `get_node` read for it, `history` its events (oldest first); `skip` names parts
// that the change itself already shows (a check's own page shows the definition of done).
export function contextSection(change, node, history, { skip = [] } = {}) {
  if (!node) return null;
  const holder = node.claimed_by ? h('span', {}, actorLabel(node.claimed_by, node.claimed_by_kind), node.claim_expires_at ? h('span', { class: 'quiet' }, ` until ${moment(node.claim_expires_at)}`) : null) : null;
  const strip = stateStrip([
    ['Status', statusChip(node.status)],
    node.priority && node.priority !== 'normal' ? ['Priority', priorityChip(node.priority)] : null,
    ['Size', itemSizeChip(node, { labelled: true })],
    ['Held by', holder],
    node.where ? ['Where', whereBlock(node.where)] : null,
  ]);
  const body = [
    h('div', { class: 'context-about' }, kindIcon(node.kind, 16), itemLink(node.ref, { class: 'item-link', dataset: { ref: node.ref } }, h('span', { class: 'item-code' }, codeOf(node.ref)), ' ', h('span', { dataset: { words: 'theirs' } }, node.title))),
    pathOf(node),
    strip,
    part('details', 'Details', detailsOf(node)),
    part('criteria', 'Acceptance criteria', criteriaOf(node)),
    skip.includes('definition') ? null : part('definition', 'Definition of done', checksOf(node)),
    part('handoff', 'Handoff note', handoffOf(node.last_handoff)),
    part('history', 'Last changes', recentOf(history)),
  ];
  return h('section', { class: 'review-part review-context', dataset: { part: 'context', ref: node.ref } }, h('h2', { class: 'review-part-title' }, 'Context'), body);
}
