// The facts of a change, as a clean typographic PR byline (M52): who is waiting, for how long, which revision, approval progress,
// what the decision covers, and signals (effect, warnings, size) where they apply. Ordinary metadata stays plain inline prose separated
// by subtle dots; coloured chips are reserved for warnings, blockers and size. A fact that does not apply is left out.

import { waitedWords, effectChip } from './asks.js';
import { h, plural } from './dom.js';
import { icon } from './icons.js';
import { actorLabel, itemSizeChip } from './labels.js';
import { warnWords, worstOf } from './needs-assist.js';
import { store } from './store.js';

// What the decision covers, in plain words: "7 items", "4 items", "1 check open", "2 notes", "lapsed 2 times". Null when already said
// elsewhere (a plan with line approvals already shows its total in "4 of 7 approved"; a move of up to 3 items names them in its title).
export function impactWords(change) {
  const { item } = change;
  if (change.look === 'plan') {
    if (item.plan.state !== 'proposed') return null;
    if (change.review && change.review.count > 0) return null;
    return plural(item.plan.epics + item.plan.stories + item.plan.tasks, 'item');
  }
  if (change.look === 'placement') return item.placement.items.length > 3 ? plural(item.placement.items.length, 'item') : null;
  if (change.look === 'check') return `${plural(item.count, 'check')} open`;
  if (change.look === 'note') return plural(item.count, 'note');
  if (change.look === 'lapse') return `lapsed ${plural(item.count, 'time')}`;
  return null;
}

const fact = (name, ...content) => h('li', { class: 'change-fact', dataset: { fact: name } }, ...content);

// The byline row. `asOf` is the server's own time, so how long something has waited never depends on this browser's clock.
export function factsOf(change, asOf) {
  const { item } = change;
  const out = [];
  if (item.waiting) out.push(fact('by', actorLabel(item.waiting, item.waiting_kind)));
  // A plan that was decided is waiting for nobody.
  const decided = change.look === 'plan' && (item.plan.state === 'approved' || item.plan.state === 'rejected');
  if (!decided) out.push(fact('waiting', 'Waiting ', h('time', { class: 'needs-time', datetime: item.waiting_since, title: item.waiting_since }, waitedWords(item.waiting_since, asOf))));
  if (change.look === 'plan' && item.plan.revision > 1) out.push(h('li', { class: 'change-fact change-revision', dataset: { fact: 'revision', revision: item.plan.revision } }, `Revision ${item.plan.revision}`));
  // How much of the plan is approved (carried from earlier or waiting to be sent); left out when nothing is.
  if (change.look === 'plan' && change.review && change.review.count > 0) out.push(h('li', { class: 'change-fact change-review-count', dataset: { fact: 'review', approved: change.review.count, total: change.review.total } }, icon('check', { size: 13 }), `${change.review.count} of ${change.review.total} approved`));
  const impact = impactWords(change);
  if (impact) out.push(fact('impact', impact));
  if (item.ask) out.push(h('li', { class: 'change-fact change-fact-holder', dataset: { fact: 'effect' } }, effectChip(item.ask)));
  if (change.warn && change.warn.total) out.push(h('li', { class: 'chip change-fact change-warning', dataset: { fact: 'warnings', state: worstOf(change.warn) } }, icon('alert', { size: 13 }), warnWords(change.warn)));
  const node = change.look === 'plan' || change.look === 'placement' ? null : store.nodes.get(item.node);
  const size = node ? itemSizeChip(node, { labelled: true }) : null;
  if (size) out.push(h('li', { class: 'change-fact change-fact-holder', dataset: { fact: 'size' } }, size));
  return h('ul', { class: 'plain change-facts' }, out);
}
