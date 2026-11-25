// What the page of what needs a person and the review page both need to know about the list, worked out in one place so that they cannot
// disagree: the changes of the server's list with what was read for each (a plan's checks, a suggested move's scores, the checks of a
// work item, the text of a note), whether a move is routine and what there is to weigh, and the order the changes are shown in for a way of
// grouping. The review page's previous and next follow that order.

import { changesOf } from './changes.js';
import { moveAssist, planAssist } from './needs-assist.js';
import { detailOf, keepOnly } from './needs-details.js';
import { groupsOf } from './needs-groups.js';
import { approvedSummary } from './plan-verdicts.js';
import { store } from './store.js';

export const titleOfRef = (ref) => store.nodes.get(ref)?.title ?? '';

// The changes of a list, each with what the server says to help review it. Starts the reads this takes (needs-details.js) and forgets
// what no change needs any more. `entries` is what was read, by the change's key. `wanted(change)` says which changes to read for (all, for
// the page of the list; the review page reads only for the change it shows and the suggested moves, whose reading says which are routine).
export function prepare(list, wanted = () => true) {
  const changes = changesOf(list, titleOfRef);
  keepOnly(changes);
  const entries = new Map();
  for (const change of changes) {
    if (!wanted(change)) {
      entries.set(change.key, null);
      continue;
    }
    const entry = detailOf(change);
    entries.set(change.key, entry);
    if (change.look === 'plan') {
      change.assist = planAssist(entry);
      change.warn = change.assist.warn;
      change.review = entry && entry.data ? approvedSummary(entry.data, change.item.plan.id) : null;
    } else if (change.look === 'placement') {
      const assist = moveAssist(entry);
      change.assist = assist;
      change.warn = assist.warn;
      change.routine = assist.routine;
    }
  }
  return { changes, entries };
}

// The groups a way of grouping makes of the changes, and the routine moves, which are one group of their own in every way: the groups
// in the order they are shown, with the routine group last.
export function sectionsOf(changes, by) {
  return { groups: groupsOf(changes.filter((change) => !change.routine), by), routine: changes.filter((change) => change.routine) };
}

// Every change in the order the page shows them (the groups, then the routine moves), as their keys.
export function orderOf(changes, by) {
  const { groups, routine } = sectionsOf(changes, by);
  return [...groups.flatMap((group) => group.changes), ...routine].map((change) => change.key);
}
