// The parts of a plan's card and page that are about the conversation (M51), apart from the lines of the diff (plan-diffs.js,
// plan-notes.js): the choice between what changed and the whole plan, what was asked for in earlier feedback, a plan that was changed
// outside a revision, and the notice that a newer revision arrived under comments that were not sent. Every word of the plan is the
// server's; what is added here is only how it is said.

import { itemLink, isMe } from './ctx.js';
import { h, plural } from './dom.js';
import { icon } from './icons.js';
import { codeOf, kindIcon, parseNodeRef, statusLabel } from './labels.js';
import { STATE_WORDS, nearPercent } from './needs-assist.js';
import { FIELD_WORDS, isFeedbackOpen, rememberFeedbackOpen } from './plan-comments.js';
import { store } from './store.js';

// -- what was asked for before ----------------------------------------------------------------------------

// Whether an item has a line in the way the plan is read now: it is in the plan, or, when reading what changed, this revision took it out
// (and its line as it was is drawn).
export function hasLine(data, mode, ref) {
  if (data.tree.some((one) => one.ref === ref)) return true;
  return mode === 'delta' && (data.changes ?? []).some((one) => one.change === 'removed' && one.ref === ref);
}

const whoWords = (by, kind) => (isMe(by, kind) ? 'Your' : `${by}\u2019s`);

// A comment that has no line to sit under in the way the plan is read now (its item is not in the plan any more), listed under its
// feedback so that nothing a person said is out of sight.
function orphanItem(comment, index) {
  return h(
    'li',
    { class: 'feedback-orphan', dataset: { ref: comment.item, comment: index, field: comment.field } },
    h(
      'p',
      {},
      itemLink(comment.item, { class: 'item-link', dataset: { ref: comment.item } }, codeOf(comment.item)),
      h('span', { class: 'quiet' }, ' \u00b7 Not in the plan'),
      h('span', { class: 'quiet' }, ` \u00b7 About ${comment.field ? `the ${FIELD_WORDS[comment.field].toLowerCase()}` : 'the whole item'}`),
    ),
    h('blockquote', { class: 'comment-quote', dataset: { words: 'theirs' } }, comment.quoted),
    h('p', { class: 'comment-body', dataset: { words: 'theirs' } }, comment.text),
  );
}

// One section for all the feedback given: a closed `details` for each, with its summary in full and how many comments it had. The last is
// open when the plan was revised, since it is what the revision should have answered (`openAll` opens every one, on the plan's own page).
// `hasLine(ref)` says whether an item has a line
// in the way the plan is read now: a comment that has none is listed with its feedback.
export function feedbackSections(feedback, { revised, hasLine, openAll = false }) {
  if (!feedback.length) return null;
  return h(
    'section',
    { class: 'change-feedback', 'aria-label': 'What was asked for before' },
    feedback.map((one, at) => {
      const last = at === feedback.length - 1;
      const open = isFeedbackOpen(one.id, openAll || (revised && last));
      const box = h(
        'details',
        { class: 'feedback-item', open, dataset: { feedback: one.id, revision: one.revision } },
        h(
          'summary',
          {},
          icon('chevron_right', { size: 14, className: 'disclosure-chevron' }),
          h('span', { class: 'feedback-title' }, h('span', isMe(one.by, one.by_kind) ? {} : { dataset: { words: 'theirs' } }, whoWords(one.by, one.by_kind)), ` feedback on revision ${one.revision}`),
          one.comments.length ? h('span', { class: 'feedback-count quiet' }, plural(one.comments.length, 'comment')) : null,
        ),
        h('p', { class: 'feedback-summary', dataset: { words: 'theirs' } }, one.summary),
        (() => {
          const stray = one.comments.map((comment, index) => [comment, index]).filter(([comment]) => !hasLine(comment.item));
          return stray.length ? h('ul', { class: 'plain feedback-orphans' }, stray.map(([comment, index]) => orphanItem(comment, index))) : null;
        })(),
      );
      // Setting `open` while drawing also fires a toggle: only a change of it is the person's choice.
      let known = box.open;
      box.addEventListener('toggle', () => {
        if (box.open === known) return;
        known = box.open;
        rememberFeedbackOpen(one.id, known);
      });
      return box;
    }),
  );
}

// -- a plan changed outside a revision -------------------------------------------------------------------------

const PART_WORDS = { ...Object.fromEntries(Object.entries(FIELD_WORDS).map(([name, words]) => [name, words.toLowerCase()])), body: 'details', dropped: 'dropped', missing: 'missing' };

// "Changed since revision 2, outside a revision: T-12 (title), T-13 (dropped)". The server refuses to approve a plan in this state, and says
// so in its own words when asked to; this says why first. `advice` is what a person can do about it.
export function driftBlock(revision, drift, { advice }) {
  if (!drift || !drift.length) return null;
  return h(
    'section',
    { class: 'change-drift', role: 'alert', 'aria-label': 'Changed outside a revision' },
    icon('alert', { size: 16 }),
    h(
      'div',
      { class: 'drift-words' },
      h(
        'p',
        {},
        `Changed since revision ${revision}, outside a revision: `,
        drift.map((one, at) => [at ? ', ' : null, itemLink(one.ref, { class: 'item-link drift-item', dataset: { ref: one.ref, fields: one.fields.join(' ') } }, codeOf(one.ref)), ` (${one.fields.map((name) => PART_WORDS[name] ?? name).join(', ')})`]),
        '.',
      ),
      advice ? h('p', { class: 'quiet' }, advice) : null,
    ),
  );
}

// -- a revision arrived under comments that were not sent ---------------------------------------------------------

// "Revision 2 arrived. Not kept: 3 unsent comments, 2 unsent approvals", with what they said behind a closed disclosure so that they can be
// written again, and a way to dismiss it.
export function lostBlock(lost, { onDismiss }) {
  const count = lost.comments.length;
  const approved = lost.approved ?? [];
  const kept = [count ? plural(count, 'unsent comment') : null, approved.length ? plural(approved.length, 'unsent approval') : null].filter(Boolean);
  return h(
    'div',
    { class: 'change-lost-box', role: 'status' },
    h(
      'div',
      { class: 'change-lost-text' },
      h(
        'p',
        { class: 'change-lost' },
        h('strong', {}, `Revision ${lost.to} arrived`),
        ' ',
        h('span', {}, 'Not kept: '),
        kept.map((part, at) => [at ? ', ' : null, h('span', {}, part)]),
      ),
      h(
        'details',
        { class: 'change-lost-list' },
        h('summary', {}, icon('chevron_right', { size: 14, className: 'disclosure-chevron' }), 'What they said'),
        h(
          'ul',
          { class: 'plain lost-comments' },
          lost.comments.map((one) => h('li', {}, h('strong', {}, codeOf(one.item)), one.field ? ` (${FIELD_WORDS[one.field].toLowerCase()})` : '', ': ', h('span', { dataset: { words: 'theirs' } }, one.text))),
          approved.length ? h('li', { class: 'lost-approved' }, h('strong', {}, 'Approved'), ': ', approved.map(codeOf).join(', ')) : null,
        ),
      ),
    ),
    h('button', { type: 'button', class: 'ghost icon-only small notice-close', onclick: onDismiss }, icon('close', { size: 14 }), h('span', { class: 'sr-only' }, 'Dismiss')),
  );
}

// -- checks of a waiting plan (M50) ---------------------------------------------------------------------------

const CHECK_LETTERS = { E: 'epic', S: 'story', T: 'task' };
const CHECK_SIGNS = { warning: 'alert', failed: 'blocked', unavailable: 'info' };

function linkedCodes(words) {
  const out = [];
  let from = 0;
  for (const found of words.matchAll(/\b([EST])-([1-9][0-9]{0,17})\b/g)) {
    const ref = `${CHECK_LETTERS[found[1]]}:${found[2]}`;
    out.push(words.slice(from, found.index));
    out.push(store.nodes.has(ref) ? itemLink(ref, { class: 'item-link', dataset: { ref } }, found[0]) : found[0]);
    from = found.index + found[0].length;
  }
  out.push(words.slice(from));
  return out;
}

function relatedItem(one) {
  const found = parseNodeRef(one.ref);
  return h(
    'li',
    { class: 'check-related-item', dataset: { ref: one.ref, similarity: one.similarity ?? null } },
    itemLink(one.ref, { class: 'item-link', dataset: { ref: one.ref } }, found ? kindIcon(found.kind, 14) : null, h('span', { class: 'item-code' }, codeOf(one.ref)), ' ', h('span', { dataset: { words: 'theirs' } }, one.title)),
    h('span', { class: 'check-related-status quiet' }, statusLabel(one.status)),
    one.similarity === null || one.similarity === undefined ? null : h('span', { class: 'check-related-similarity' }, `${nearPercent(one.similarity)}% alike`),
  );
}

function checkFinding(one) {
  return h(
    'li',
    { class: 'check-finding', dataset: { items: one.items.join(' ') } },
    h('p', { class: 'check-finding-words', dataset: { words: 'theirs' } }, linkedCodes(one.words)),
    one.related.length ? h('ul', { class: 'plain check-related' }, one.related.map(relatedItem)) : null,
  );
}

function checkLine(check) {
  return h(
    'li',
    { class: 'check-line', dataset: { check: check.kind, state: check.state } },
    h('p', { class: 'check-head' }, icon(CHECK_SIGNS[check.state] ?? 'info', { size: 16 }), h('span', { class: 'check-state' }, STATE_WORDS[check.state] ?? check.state), h('span', { class: 'check-words', dataset: { words: 'theirs' } }, linkedCodes(check.words))),
    check.findings.length ? h('ul', { class: 'plain check-findings' }, check.findings.map(checkFinding)) : null,
  );
}

export function checksBlock(change, assist) {
  if (!assist.checks) return null;
  return h(
    'section',
    { class: 'change-checks', 'aria-label': `Checks on ${change.code}`, dataset: { open: String(assist.open.length) } },
    assist.open.length ? h('ul', { class: 'plain check-lines' }, assist.open.map(checkLine)) : null,
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
          icon(CHECK_SIGNS[check.state] ?? 'info', { size: 14 }),
          h('span', { class: 'card-check-words', dataset: { words: 'theirs' } }, linkedCodes(check.words)),
        ),
      ),
    ),
  );
}
