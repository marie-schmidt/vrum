// Accepting a suggested move in part (M53): which of the items a proposal moves the person leaves where they are. It is kept in the page's
// memory and never in the browser's storage, like the comments and approvals of a plan, until the proposal is decided; a proposal that
// changed (another set of items, another parent) starts over with every item included. Every item is included by default, and a decision
// that includes them all sends no list of items at all.

import { hash } from './changes.js';
import { h } from './dom.js';
import { hoverCard } from './hovercard.js';
import { icon } from './icons.js';
import { codeOf } from './labels.js';

const left = new Map(); // proposal -> { print, refs: the items left out }

// What identifies the proposal as it stands: its items, where each is now, and where they would go.
const printOf = (move) => hash(JSON.stringify([move.items.map((one) => [one.ref, one.from_parent]), move.parent, move.new_epic]));

// The items of a proposal that are left out (a Set; changing it is the person's choice, through `toggleLeft`).
export function leftOut(move) {
  const print = printOf(move);
  const found = left.get(move.id);
  if (found && found.print === print) return found.refs;
  const made = { print, refs: new Set() };
  left.set(move.id, made);
  return made.refs;
}

export function toggleLeft(move, ref) {
  const refs = leftOut(move);
  if (refs.has(ref)) refs.delete(ref);
  else refs.add(ref);
}

export const forgetPicks = (move) => left.delete(move.id);

// The items to move: every item that is not left out.
export const includedOf = (move) => {
  const refs = leftOut(move);
  return move.items.filter((one) => !refs.has(one.ref)).map((one) => one.ref);
};

// The control beside a moved item's line: a labelled toggle so a person sees at once they can keep or skip each item in the move. A person
// who cannot write has none. Its aria-label names the item; the visible word is Include or Skip.
export function includeControl(move, ref, { hooks, editable }) {
  if (!editable) return null;
  const included = !leftOut(move).has(ref);
  const code = codeOf(ref);
  const button = h(
    'button',
    {
      type: 'button',
      class: 'ghost small diff-include',
      'aria-label': included ? `Move ${code}` : `Leave ${code} where it is`,
      'aria-pressed': String(included),
      dataset: { ref, focusKey: `include:${ref}` },
      onclick: () => {
        toggleLeft(move, ref);
        hooks.redraw(`include:${ref}`);
      },
    },
    icon(included ? 'check' : 'close', { size: 13 }),
    h('span', { class: 'diff-include-word' }, included ? 'Include' : 'Skip'),
  );
  return h('span', { class: 'slot-control' }, button, hoverCard(button, h('div', { class: 'verdict-hover' }, h('p', {}, included ? 'Moves with the proposal' : 'Stays where it is')), { closeOnPress: true, passive: true }));
}

// The slot next to a moved item's line that holds the control, or nothing.
export function includeSlot(move, ref, options) {
  const control = includeControl(move, ref, options);
  return control ? h('div', { class: 'diff-comment-slot', dataset: { ref, controls: '1', sign: '+', include: 'true' } }, h('div', { class: 'slot-controls' }, control)) : null;
}
