// What waits for a person (the server's own list, `needs_a_person`), read in one place so that the page of it and the number in
// the sidebar can never disagree: both are drawn from the same answer. The list is read once when the board starts and again
// when the board hears news of a kind that can change it, never on a timer of its own, and a burst of news is one read: a read
// that is asked for while another is on its way waits for it and is then made once. Whoever draws the list says so with
// `onNeeds` and is given every answer.

import { Refusal, call } from './api.js';
import { onChange } from './store.js';

// The actions of the change feed that can add a row to the list or take one away. A claim that runs out is the one thing that
// changes it with no news at all, and the next read says so.
const CHANGES = new Set([
  'ask_raised',
  'ask_answered',
  'check_ticked',
  'check_unticked',
  'claimed',
  'claim_released',
  'handoff',
  'update',
  'definition_of_done_changed',
  'plan_proposed',
  'plan_revised',
  'plan_changes_requested',
  'plan_approved',
  'plan_rejected',
  'placement_proposed',
  'placement_accepted',
  'placement_rejected',
  'gate_added',
  'gate_removed',
]);
const changesIt = (event) => CHANGES.has(event.action) || event.action.startsWith('note_');

const listeners = new Set();
let last = null; // the newest answer, or null before the first
let running = null;
let queued = null;

// fn(list) is called with every answer: { as_of, total, groups, left_out }.
export function onNeeds(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export const needsTotal = () => (last ? last.total : null);

async function fetchNeeds() {
  last = await call('needs_a_person', {});
  listeners.forEach((fn) => fn(last));
  return last;
}

// The list as the server has it now. Each call gets a read that began after it was made.
export function readNeeds() {
  if (running === null) {
    running = fetchNeeds().finally(() => {
      running = null;
    });
    return running;
  }
  queued ??= running
    .then(
      () => undefined,
      () => undefined,
    )
    .then(() => {
      queued = null;
      return readNeeds();
    });
  return queued;
}

// Whether any of these events can change the list.
const changesNeeds = (events) => events.some(changesIt);

// The list the board knows now, for a page that opens: the read that is on its way, or the last answer, which is as current as the
// board's news (it is read again after every change that can alter it), or, before there is any, a read of its own.
export function currentNeeds() {
  if (running !== null) return running;
  return last !== null ? Promise.resolve(last) : readNeeds();
}

// Keep the list current: read it now and after news that can change it. Returns what stops it. A read that fails leaves the last
// answer: the board says on its own that it cannot reach the server.
export function watchNeeds() {
  last = null;
  const quietly = () =>
    readNeeds().catch((error) => {
      if (!(error instanceof Refusal)) throw error;
    });
  quietly();
  const stop = onChange((change) => {
    if (change.events && changesNeeds(change.events)) quietly();
  });
  return () => {
    stop();
    last = null;
  };
}
