// The hover card: a small panel that says more about the thing it is next to (why a card is blocked,
// how to write a filter) without taking the place of it. It opens when the pointer rests on its
// trigger, when the trigger takes the keyboard's focus, and on a tap; it closes when the pointer
// leaves (the card itself counts as part of the trigger, so it can be read and its links used),
// when the focus leaves, on Escape, on a tap elsewhere and when a drag starts.
//
// The card hangs from the body (or from the dialog the trigger is in, so that it is not behind it)
// and is placed from the trigger's box on the screen, so no column that scrolls and no edge of the
// window can cut it off. While it is closed it waits, hidden, right after its trigger: the trigger
// names it with aria-describedby the whole time, so a screen reader reads it with the trigger.
// Only one card is open at once.

import { h } from './dom.js';

const GAP = 6; // between the trigger and the card
const EDGE = 8; // the card keeps this far from the edge of the window
const OPEN_AFTER = 120; // a pointer has to rest this long (milliseconds), so that passing over does not flash cards
const CLOSE_AFTER = 160; // and may take this long to cross from the trigger to the card
const TAP_GRACE = 300; // a tap that follows the card opening is the same gesture, not a second one

let counter = 0;
let current = null; // the card that is open: { trigger, card, timer, frame, box, opened, touched }

// Makes a card for `trigger` and returns it. Put it in the page right after the trigger:
// `h('span', {}, trigger, hoverCard(trigger, content))`. `content` is anything `h` takes as
// children. `size` is 'wide' for a card that holds a table.
export function hoverCard(trigger, content, { size = 'normal' } = {}) {
  counter += 1;
  const card = h('div', { id: `hovercard-${counter}`, class: 'hovercard', role: 'tooltip', hidden: true, dataset: { size } }, content);
  const state = { trigger, card, timer: 0, frame: 0, box: '', opened: 0, touched: false };
  trigger.setAttribute('aria-describedby', card.id);

  trigger.addEventListener('pointerdown', (event) => {
    state.touched = event.pointerType === 'touch';
  });
  trigger.addEventListener('pointerenter', (event) => {
    if (event.pointerType === 'touch') return;
    clearTimeout(state.timer);
    state.timer = setTimeout(() => open(state), OPEN_AFTER);
  });
  const leave = (event) => {
    if (event.pointerType === 'touch') return;
    clearTimeout(state.timer);
    state.timer = setTimeout(() => close(state), CLOSE_AFTER);
  };
  trigger.addEventListener('pointerleave', leave);
  card.addEventListener('pointerenter', () => clearTimeout(state.timer));
  card.addEventListener('pointerleave', leave);
  trigger.addEventListener('focus', () => open(state));
  // Focus that goes into the card (a click on a link in it) is not the focus leaving.
  trigger.addEventListener('blur', (event) => {
    if (!card.contains(event.relatedTarget)) close(state);
  });
  card.addEventListener('focusout', (event) => {
    if (event.relatedTarget !== trigger && !card.contains(event.relatedTarget)) close(state);
  });
  // A tap, or Enter on a button, shows or hides the card; a mouse click leaves it as hovering made it.
  trigger.addEventListener('click', (event) => {
    if (current !== state) open(state);
    else if ((state.touched || event.detail === 0) && performance.now() - state.opened > TAP_GRACE) close(state);
  });
  return card;
}

function open(state) {
  clearTimeout(state.timer);
  if (current === state) return;
  if (current) close(current);
  const { trigger, card } = state;
  if (!trigger.isConnected || !trigger.getClientRects().length) return;
  current = state;
  card.hidden = false;
  (trigger.closest('dialog[open]') || document.body).append(card);
  state.opened = performance.now();
  place(state);
  document.addEventListener('keydown', onKey, true);
  document.addEventListener('pointerdown', onOutside, true);
  document.addEventListener('dragstart', onOutside, true);
  state.frame = requestAnimationFrame(follow);
}

function close(state) {
  clearTimeout(state.timer);
  if (current !== state) return;
  current = null;
  cancelAnimationFrame(state.frame);
  document.removeEventListener('keydown', onKey, true);
  document.removeEventListener('pointerdown', onOutside, true);
  document.removeEventListener('dragstart', onOutside, true);
  state.card.hidden = true;
  // Back to its place beside the trigger, unless the page was drawn again and the trigger is gone.
  if (state.trigger.isConnected) state.trigger.after(state.card);
  else state.card.remove();
}

function onKey(event) {
  if (event.key === 'Escape' && current) close(current);
}

function onOutside(event) {
  if (current && !current.trigger.contains(event.target) && !current.card.contains(event.target)) close(current);
}

const boxOf = (trigger) => {
  const box = trigger.getBoundingClientRect();
  return [box.left, box.top, box.width, box.height].join();
};

// The card goes with its trigger: when the page moves it (a scroll, a resize, a new layout) or draws
// it again, the card closes, so it is never left hanging where its trigger was.
function follow() {
  if (!current) return;
  const state = current;
  const { trigger } = state;
  if (!trigger.isConnected || !trigger.getClientRects().length || boxOf(trigger) !== state.box) {
    close(state);
    return;
  }
  state.frame = requestAnimationFrame(follow);
}

// Below the trigger when it fits there (or when there is more room below than above), otherwise above,
// and never outside the window: a card taller than the room scrolls inside itself.
function place(state) {
  const { trigger, card } = state;
  const box = trigger.getBoundingClientRect();
  const { clientWidth: width, clientHeight: height } = document.documentElement;
  card.style.maxHeight = '';
  const natural = card.offsetHeight;
  const below = height - box.bottom - GAP - EDGE;
  const above = box.top - GAP - EDGE;
  const under = natural <= below || below >= above;
  const room = Math.max(0, under ? below : above);
  if (natural > room) card.style.maxHeight = `${room}px`;
  const shown = Math.min(natural, room);
  card.style.top = `${Math.round(under ? box.bottom + GAP : box.top - GAP - shown)}px`;
  card.style.left = `${Math.round(Math.min(Math.max(box.left, EDGE), Math.max(EDGE, width - card.offsetWidth - EDGE)))}px`;
  card.dataset.placement = under ? 'below' : 'above';
  state.box = boxOf(trigger);
}
