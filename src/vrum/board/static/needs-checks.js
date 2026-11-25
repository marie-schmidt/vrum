// The checks of a plan that waits for approval (M50), drawn above its diff and never inside a fold: what the server found out about
// the plan before a person decides on it. There are three, always in this order: whether the plan makes work wait for itself (a
// circle), whether a story or task has no size, and whether an epic or story looks like work that already exists. Each says one plain
// sentence; each finding names the items it is about as links, and a possible duplicate names the existing work it resembles with how
// alike it is. A check that passed is not a line: all that passed are ONE quiet sentence. A check that could not be made (search by
// meaning is not possible now) is as visible as a warning and says why: it is not "no duplicates".
//
// A check never stops anything and never decides: Approve and Reject work exactly as they did. Nothing here guesses a size.

import { itemLink } from './ctx.js';
import { h } from './dom.js';
import { icon } from './icons.js';
import { codeOf, kindIcon, parseNodeRef, statusLabel } from './labels.js';
import { STATE_WORDS, nearPercent } from './needs-assist.js';
import { store } from './store.js';

const LETTERS = { E: 'epic', S: 'story', T: 'task' };
const SIGNS = { warning: 'alert', failed: 'blocked', unavailable: 'info' };

// The server's sentence with every code of an item that exists made a link to it, so that "T-187 has no size." can be followed.
function linked(words) {
  const out = [];
  let from = 0;
  for (const found of words.matchAll(/\b([EST])-([1-9][0-9]{0,17})\b/g)) {
    const ref = `${LETTERS[found[1]]}:${found[2]}`;
    out.push(words.slice(from, found.index));
    out.push(store.nodes.has(ref) ? itemLink(ref, { class: 'item-link', dataset: { ref } }, found[0]) : found[0]);
    from = found.index + found[0].length;
  }
  out.push(words.slice(from));
  return out;
}

// One existing item a possible duplicate resembles: its code and title as a link, where it stands and how alike it is.
function related(one) {
  const found = parseNodeRef(one.ref);
  return h(
    'li',
    { class: 'check-related-item', dataset: { ref: one.ref, similarity: one.similarity ?? null } },
    itemLink(one.ref, { class: 'item-link', dataset: { ref: one.ref } }, found ? kindIcon(found.kind, 14) : null, h('span', { class: 'item-code' }, codeOf(one.ref)), ' ', h('span', { dataset: { words: 'theirs' } }, one.title)),
    h('span', { class: 'check-related-status quiet' }, statusLabel(one.status)),
    one.similarity === null || one.similarity === undefined ? null : h('span', { class: 'check-related-similarity' }, `${nearPercent(one.similarity)}% alike`),
  );
}

function finding(one) {
  return h(
    'li',
    { class: 'check-finding', dataset: { items: one.items.join(' ') } },
    h('p', { class: 'check-finding-words', dataset: { words: 'theirs' } }, linked(one.words)),
    one.related.length ? h('ul', { class: 'plain check-related' }, one.related.map(related)) : null,
  );
}

function line(check) {
  return h(
    'li',
    { class: 'check-line', dataset: { check: check.kind, state: check.state } },
    h('p', { class: 'check-head' }, icon(SIGNS[check.state] ?? 'info', { size: 16 }), h('span', { class: 'check-state' }, STATE_WORDS[check.state] ?? check.state), h('span', { class: 'check-words', dataset: { words: 'theirs' } }, linked(check.words))),
    check.findings.length ? h('ul', { class: 'plain check-findings' }, check.findings.map(finding)) : null,
  );
}

// The block above a plan's diff. `assist` is what needs-assist.js worked out. Nothing for a plan with no checks to show.
export function checksBlock(change, assist) {
  if (!assist.checks) return null;
  return h(
    'section',
    { class: 'change-checks', 'aria-label': `Checks on ${change.code}`, dataset: { open: String(assist.open.length) } },
    assist.open.length ? h('ul', { class: 'plain check-lines' }, assist.open.map(line)) : null,
    assist.ok ? h('p', { class: 'check-ok quiet' }, icon('check', { size: 14 }), assist.ok.map((part) => h('span', { class: 'check-ok-part', dataset: { check: part } }, part))) : null,
  );
}

// A compact strip on a plan card right above Approve, so warnings are visible on the card itself before anyone presses Approve.
export function cardChecksStrip(change, assist) {
  if (!assist || !assist.open || !assist.open.length) return null;
  return h(
    'div',
    { class: 'card-checks', 'aria-label': `Warnings on ${change.code}` },
    h(
      'ul',
      { class: 'plain card-checks-list' },
      assist.open.map((check) =>
        h(
          'li',
          { class: 'card-check-row', dataset: { check: check.kind, state: check.state } },
          icon(SIGNS[check.state] ?? 'info', { size: 14 }),
          h('span', { class: 'card-check-words', dataset: { words: 'theirs' } }, linked(check.words)),
        ),
      ),
    ),
  );
}
