// What the person has done on the page of what needs a person and on the review page, kept in one place so that the two agree (M52): which
// changes they marked Viewed (and the fingerprint of what they saw), the words typed into a comment of a decision, and what a decision
// left undone. All of it lives in the page's memory and never in the browser's storage: a reload forgets it, and what changed under a
// Viewed mark takes the mark off by itself.

import { hash } from './changes.js';

// Viewed: the fingerprint of what the person saw, or null; that the change changed since they looked; whether its preview is folded and whether
// it was opened out.
const views = new Map();

export const viewOf = (key) => {
  if (!views.has(key)) views.set(key, { viewed: null, changed: false, collapsed: false, open: new Set() });
  return views.get(key);
};

// Forgets what no change of the list needs any more.
export function keepViews(keys) {
  const live = new Set(keys);
  for (const key of [...views.keys()]) if (!live.has(key)) views.delete(key);
}

// What the server sent for a change and what was read for it, as a short fingerprint. The topic is the server's grouping of the rows, which
// changes as other rows come and go: it is not what was looked at.
export const fingerprintOf = (change, entry) => hash(JSON.stringify({ ...change.item, topic: null })) + (entry && entry.hash ? entry.hash : '');

// A change whose mark no longer matches what the server sent: the mark comes off, and the change says so. A change that is still being
// read (the page was just opened) is not judged until it has been.
export function judge(change, entry) {
  const view = viewOf(change.key);
  const reading = entry !== null && entry !== undefined && entry.state === 'loading' && !entry.data;
  if (!reading && view.viewed !== null && view.viewed !== fingerprintOf(change, entry)) {
    view.viewed = null;
    view.changed = true;
    view.collapsed = false;
  }
  return view;
}

// What is being typed in the comment of a decision, kept while a page is drawn again: `ask:12` or `check:task:4:abcd1234` ->
// { open, text, remember }.
const drafts = new Map();

export const draftOf = (key) => {
  if (!drafts.has(key)) drafts.set(key, { open: false, text: '', remember: false });
  return drafts.get(key);
};

export const forgetDrafts = () => drafts.clear();

// What a decision left undone, or could not do afterwards: an acceptance that left an item where it was says which (the server's reason beside
// it), and an answer that stood but could not be kept as a decision says so in the server's words. Said once and kept until the person goes away
// or dismisses it.
export const said = { lines: [], problems: [] };

export function forgetSaid() {
  said.lines = [];
  said.problems = [];
}
