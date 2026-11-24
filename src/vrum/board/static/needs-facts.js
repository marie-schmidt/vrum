// The facts of a change, as a row of chips (M52): who is waiting, for how long, which revision, what an answer does to the work, how much
// the decision moves, how many warnings there are to weigh and, where the change is about work that has one, its size. A card and the
// review page draw the same chips. A fact that does not apply to a kind is not drawn; none of them is a sentence.

import { waitedWords, effectChip } from './asks.js';
import { h, plural } from './dom.js';
import { icon } from './icons.js';
import { actorLabel, itemSizeChip } from './labels.js';
import { warnWords, worstOf } from './needs-assist.js';
import { store } from './store.js';

// What the decision is about, in a few words: "Frees 7", "Moves 3", "2 open", "2 notes", "Ran out ×2". Null when there is nothing to count.
export function impactWords(change) {
  const { item } = change;
  // A plan that is decided, or waits for its agent, frees nothing now.
  if (change.look === 'plan') return item.plan.state === 'proposed' ? `Frees ${item.plan.epics + item.plan.stories + item.plan.tasks}` : null;
  if (change.look === 'placement') return `Moves ${item.placement.items.length}`;
  if (change.look === 'check') return `${item.count} open`;
  if (change.look === 'note') return plural(item.count, 'note');
  if (change.look === 'lapse') return `Ran out \u00d7${item.count}`;
  return null;
}

const fact = (name, ...content) => h('li', { class: 'chip change-fact', dataset: { fact: name } }, ...content);

// The row of chips. `asOf` is the server's own time, so how long something has waited never depends on this browser's clock.
export function factsOf(change, asOf) {
  const { item } = change;
  const out = [];
  if (item.waiting) out.push(fact('by', actorLabel(item.waiting, item.waiting_kind)));
  // A plan that was decided is waiting for nobody.
  const decided = change.look === 'plan' && (item.plan.state === 'approved' || item.plan.state === 'rejected');
  if (!decided) out.push(fact('waiting', icon('clock', { size: 13 }), h('time', { class: 'needs-time', datetime: item.waiting_since, title: item.waiting_since }, waitedWords(item.waiting_since, asOf))));
  if (change.look === 'plan' && item.plan.revision > 1) out.push(h('li', { class: 'chip change-fact change-revision', dataset: { fact: 'revision', revision: item.plan.revision } }, `Revision ${item.plan.revision}`));
  if (item.ask) out.push(h('li', { class: 'change-fact change-fact-holder', dataset: { fact: 'effect' } }, effectChip(item.ask)));
  const impact = impactWords(change);
  if (impact) out.push(fact('impact', impact));
  if (change.warn && change.warn.total) out.push(h('li', { class: 'chip change-fact change-warning', dataset: { fact: 'warnings', state: worstOf(change.warn) } }, icon('alert', { size: 13 }), warnWords(change.warn)));
  const node = change.look === 'plan' || change.look === 'placement' ? null : store.nodes.get(item.node);
  const size = node ? itemSizeChip(node, { labelled: true }) : null;
  if (size) out.push(h('li', { class: 'change-fact change-fact-holder', dataset: { fact: 'size' } }, size));
  return h('ul', { class: 'plain change-facts' }, out);
}
