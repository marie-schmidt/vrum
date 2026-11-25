// What is beside a line of a plan to say yes or ask for a change, and what a person already said about it (M53): the controls in the slot next
// to the line (who last changed it and in which revision, approve it as it is, ask for a change) and the chip at the end of the line that says
// what is carried from earlier (approved, asked, changed since). Nothing here asks the server for anything or keeps anything: the server's
// `blame` (list_plans) is what is carried, plan-comments.js is what is not sent yet, and plan-notes.js puts them on the lines.
//
// A control is an icon or a few characters with a hover card that says what it does in a phrase, so that no sentence is drawn on the page.
// A state is an icon and a word, never a colour alone.

import { h, moment } from './dom.js';
import { hoverCard } from './hovercard.js';
import { icon } from './icons.js';
import { actorLabel, codeOf } from './labels.js';
import { approvedOf } from './plan-comments.js';

const STATE_WORDS = { approved: 'Approved', asked: 'Asked', changed: 'Changed' };
const STATE_ICONS = { approved: 'check', asked: 'comment', changed: 'diff' };

// A control of the slot with its hover card, which follows it. A disabled button hears no pointer, so when the button is disabled the card
// belongs to the holder around it, which does: the card still opens and closes with the pointer and says why.
function withCard(button, content, { disabled = false } = {}) {
  const holder = h('span', { class: 'slot-control' }, button);
  holder.append(hoverCard(disabled ? holder : button, h('div', { class: 'verdict-hover' }, content), { closeOnPress: true, passive: true }));
  return holder;
}

// Approve an item as it is, or, on a line that was taken out, agree that it goes. Pressed while the approval waits to be sent. It cannot be
// pressed while the item has a comment that is not sent: the page never sends both, and the server refuses them together.
export function approveButton({ ref, removed, pressed, blocked, onToggle }) {
  const code = codeOf(ref);
  const button = h(
    'button',
    {
      type: 'button',
      class: 'ghost icon-only small diff-approve',
      'aria-label': removed ? `Agree with removing ${code}` : `Approve ${code}`,
      'aria-pressed': String(pressed),
      disabled: blocked,
      dataset: { ref, focusKey: `approve:${ref}` },
      onclick: onToggle,
    },
    icon('check', { size: 14 }),
  );
  const words = blocked ? 'Has an unsent comment' : pressed ? (removed ? 'Agreed, not sent yet' : 'Approved, not sent yet') : removed ? 'Agree it goes' : 'Approve as it is';
  return withCard(button, h('p', {}, words), { disabled: blocked });
}

// Ask for a change to an item: it opens the box under the line.
export function commentButton({ ref, onOpen }) {
  const code = codeOf(ref);
  const button = h('button', { type: 'button', class: 'ghost icon-only small diff-comment', 'aria-label': `Comment on ${code}`, dataset: { ref, focusKey: `comment:${ref}` }, onclick: onOpen }, icon('comment', { size: 14 }));
  return withCard(button, h('p', {}, 'Request a change'));
}

// Who last changed an item and in which revision, like the revision beside a line in git's blame: "r2", or "removed in r2" for a line the
// revision took out. Pressing it reads the plan as of that revision. The hover card has the author, the time and the revision's own note.
export function blameChip(blame, { onPress }) {
  const { ref, revision, change } = blame;
  const button = h(
    'button',
    {
      type: 'button',
      class: 'ghost small blame',
      'aria-label': `View ${codeOf(ref)} as of revision ${revision}`,
      dataset: { ref, revision: String(revision), change, focusKey: `blame:${ref}` },
      onclick: onPress,
    },
    change === 'removed' ? `removed in r${revision}` : `r${revision}`,
  );
  const content = [
    h('p', {}, `Revision ${revision} by `, actorLabel(blame.by, blame.by_kind, { card: false }), ', ', h('time', { datetime: blame.at, title: blame.at }, moment(blame.at))),
    blame.note ? h('p', { class: 'blame-note', dataset: { words: 'theirs' } }, blame.note) : null,
  ];
  return withCard(button, content);
}

// What is carried for an item, or waiting to be sent, at the end of its line: an icon and a word, and in a hover card who said it and when.
// Returns the chip and its card (to be placed one after the other), or nothing when nobody has said anything.
export function reviewChip({ ref, state, review, pending }) {
  const chip = h('span', { class: 'line-review', tabindex: 0, dataset: { ref, state, pending: pending ? 'true' : null } }, icon(STATE_ICONS[state], { size: 13 }), h('span', { class: 'line-review-word' }, STATE_WORDS[state]));
  const said = pending
    ? h('p', {}, 'Not sent yet')
    : h('p', {}, actorLabel(review.by, 'person', { card: false }), `, on revision ${review.revision}`, ' \u00b7 ', h('time', { datetime: review.at, title: review.at }, moment(review.at)));
  return [chip, hoverCard(chip, h('div', { class: 'verdict-hover' }, said), { passive: true })];
}

// -- what is carried, by item -----------------------------------------------------------------------------------

export const reviewsOf = (blame) => new Map((blame ?? []).map((one) => [one.ref, one]));

// The state an item shows: waiting to be sent wins over what is carried. `null` for an item nobody has said anything about.
export function stateOf(ref, { reviews, approved }) {
  if (approved.has(ref)) return { state: 'approved', pending: true, review: null };
  const carried = reviews.get(ref)?.review;
  return carried && carried.state !== 'none' ? { state: carried.state, pending: false, review: carried } : null;
}

// The items of a revision that are approved (carried or waiting to be sent), by reference, and how many of the revision's own items they are
// (an item the revision took out is approved too, but is not one of its items).
export function approvedSummary(data, plan) {
  const waiting = approvedOf(plan, data.shown);
  const refs = new Set();
  for (const one of data.blame ?? []) if (one.review.state === 'approved') refs.add(one.ref);
  for (const ref of waiting) refs.add(ref);
  const inside = new Set(data.tree.map((one) => one.ref));
  return { refs, count: [...refs].filter((ref) => inside.has(ref)).length, total: data.tree.length };
}
