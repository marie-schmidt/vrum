// What a change needs to be shown whole, beyond the row of the server's list (changes.js): a plan is read with every item it made
// (`list_plans` with its number: the details, size, dates, links and acceptance criteria of each, the existing items it hangs
// under or points at, and its three checks), the proposals to move work are read together (`list_placements`: how each moved
// item scores for the parent it would go under, and whether the proposal is routine), the checks of the item for a check, and the
// text of each note for a note. Each is read once, kept while the
// page is open, and read again when the board hears news about an item it holds, so that what is drawn is never older than the
// last news. A read that is on its way shows the answer before it, so a page that is drawn again does not go blank.
//
// Nothing here decides anything or keeps anything past the page: it is forgotten when the person leaves it.

import { Refusal, call, callAll } from './api.js';
import { hash } from './changes.js';
import { forgetSnapshots, readPlanWhole } from './plan-read.js';

const entries = new Map(); // key -> { state: 'loading' | 'ready' | 'failed', data, hash, refs, error, token }
let wake = () => {}; // told when any answer arrives: the page draws again

// What to read for a change, or null when the row says it all.
function wanted(change) {
  const { item } = change;
  if (change.look === 'plan') return { key: `plan:${item.plan.id}`, refs: [...item.plan.items], read: () => readPlan(item.plan.id) };
  // Every waiting proposal is read in one go: the server ranks them together and one read is cheap.
  if (change.look === 'placement') return { key: 'placements', refs: placementRefs(item.placement), read: readPlacements };
  if (change.look === 'check') return { key: `check:${item.node}`, refs: [item.node], read: () => readChecks(item.node) };
  if (change.look === 'note') return { key: `note:${item.node}:${item.notes.join(',')}`, refs: [item.node], read: () => readNotes(item.notes) };
  return null;
}

async function readPlan(plan) {
  const found = await readPlanWhole(plan);
  return { data: found, refs: [...found.tree.map((one) => one.ref), ...found.context.map((one) => one.ref)] };
}

// What a proposal is about: the work it moves, where it is now and the parent it would go under.
const placementRefs = (move) => [...move.items.flatMap((one) => [one.ref, one.from_parent]), move.parent].filter(Boolean);

async function readPlacements() {
  const found = await callAll('list_placements', { status: 'proposed' });
  const byId = new Map(found.map((move) => [move.id, move]));
  return { data: byId, refs: found.flatMap(placementRefs) };
}

async function readChecks(node) {
  const read = await call('get_node', { node });
  return { data: { checks: read.checks, status: read.status }, refs: [node] };
}

async function readNotes(ids) {
  const notes = await Promise.all(ids.map((id) => call('get_note', { note_id: id })));
  return { data: { notes }, refs: [] };
}

async function load(key, entry, read) {
  entry.token += 1;
  const mine = entry.token;
  try {
    const done = await read();
    if (mine !== entry.token) return;
    entry.state = 'ready';
    entry.data = done.data;
    entry.hash = hash(JSON.stringify(done.data));
    entry.error = null;
    if (done.refs.length) entry.refs = new Set(done.refs);
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    if (mine !== entry.token) return;
    entry.state = entry.data ? 'ready' : 'failed';
    entry.error = error.message;
  }
  wake();
}

// The reading for a change: its state, and what it holds once it is read. Starts the read the first time it is asked for. Null for a
// change that needs none.
export function detailOf(change) {
  const need = wanted(change);
  if (!need) return null;
  let entry = entries.get(need.key);
  if (!entry) {
    entry = { state: 'loading', data: null, hash: '', refs: new Set(need.refs), error: null, token: 0, read: need.read };
    entries.set(need.key, entry);
    load(need.key, entry, need.read);
  }
  if (change.look !== 'placement') return entry;
  // One proposal out of the shared read, with a fingerprint of its own: another proposal changing must not take the mark off this one.
  const proposal = entry.data ? (entry.data.get(change.item.placement.id) ?? null) : null;
  return { state: entry.state, error: entry.error, data: entry.data ? { proposal } : null, hash: proposal ? hash(JSON.stringify(proposal)) : '' };
}

// News about work: read again what holds an item it names (or, for a criterion, every plan: the event does not say which item it is on).
// News about a plan itself (a revision, a request for changes, a decision) names no item, only the plan.
export function newsAbout(events) {
  for (const [key, entry] of entries) {
    const touched = events.some(
      (event) =>
        entry.refs.has(event.node) ||
        entry.refs.has(event.other_node) ||
        (key.startsWith('plan:') && ((event.rule_id !== null && event.rule_id !== undefined) || aboutPlan(event, Number(key.slice(5))))),
    );
    if (touched) load(key, entry, entry.read);
  }
}

const PLAN_NEWS = new Set(['plan_proposed', 'plan_revised', 'plan_changes_requested', 'plan_approved', 'plan_rejected']);

export const isPlanNews = (event) => PLAN_NEWS.has(event.action);

// Whether an event is about this plan: its own proposal, or one that names it.
export const aboutPlan = (event, plan) => isPlanNews(event) && (event.action === 'plan_proposed' ? event.id === plan : event.after?.plan === plan);

// A decision was refused, or the person otherwise has reason to think the plan has moved on: read the plans again now, not at the next news.
export function rereadPlans() {
  for (const [key, entry] of entries) if (key.startsWith('plan:')) load(key, entry, entry.read);
}

// The search index changed (an item was indexed, or search by meaning came or went): what a duplicate check and a score say may be
// different now, so the plans and the proposals are read again.
export function indexChanged() {
  for (const [key, entry] of entries) if (key.startsWith('plan:') || key === 'placements') load(key, entry, entry.read);
}

// Forget what no change on the page needs any more.
export function keepOnly(changes) {
  const live = new Set(changes.map(wanted).filter(Boolean).map((one) => one.key));
  for (const key of entries.keys()) if (!live.has(key)) entries.delete(key);
}

// Who to tell when an answer arrives, and clear everything (the page was opened or left).
export function watchDetails(fn) {
  entries.clear();
  forgetSnapshots();
  wake = fn;
}
