// Reading a plan whole, with its revisions, the feedback on them and what changed (`list_plans` with its number), for the card on the
// page of what needs a person and for the plan's own page. A plan that was revised also needs the revision before it when something
// was taken out of it: a removed item is not in the revision that removed it, so its line as it was comes from the one before. The
// revision before is what was submitted and never changes, so it is read once and kept while the board is open.

import { call } from './api.js';

const snapshots = new Map(); // `${plan}:${revision}` -> that revision's items

// What a revision held, as it was submitted.
async function snapshotOf(plan, revision) {
  const key = `${plan}:${revision}`;
  if (!snapshots.has(key)) snapshots.set(key, (await call('list_plans', { plan, revision })).items[0].tree);
  return snapshots.get(key);
}

// The plan as the server reads it (the current revision, or `revision`), and `previous`: the items of the revision before when it
// took something out, and null otherwise.
export async function readPlanWhole(plan, revision = null) {
  const found = (await call('list_plans', revision ? { plan, revision } : { plan })).items[0];
  const removed = (found.changes ?? []).some((one) => one.change === 'removed');
  const previous = found.shown > 1 && removed ? await snapshotOf(plan, found.shown - 1) : null;
  return { ...found, checks: found.checks ?? null, previous };
}

export const forgetSnapshots = () => snapshots.clear();
