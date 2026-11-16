// The side panel: the card of the chosen item, or the coverage report. What it shows is in the
// address (card=12, or panel=coverage), so a link opens it.

import { Refusal } from './api.js';
import { h, clear } from './dom.js';
import { ui } from './ctx.js';
import { renderCard } from './card.js';
import { renderCoverage } from './coverage.js';
import { store } from './store.js';

const aside = document.getElementById('panel');
const layout = document.getElementById('layout');
let shown = null; // { key, state }
let opener = null; // what had the focus before the panel opened, to give it back
let generation = 0;

export function hide() {
  generation += 1;
  if (!shown) return;
  shown.state.dead = true;
  shown = null;
  aside.hidden = true;
  layout.classList.remove('with-panel');
  clear(aside);
  if (opener && document.contains(opener)) opener.focus();
  opener = null;
}

export async function show(params) {
  const id = params.get('card');
  const key = id ? `card:${id}` : params.get('panel') === 'coverage' ? 'coverage' : null;
  if (key === null) return hide();
  if (shown && shown.key === key) return;
  if (!shown) opener = document.activeElement;
  const mine = ++generation;
  // A card still being fetched for the one that was open must not be drawn over this one.
  if (shown) shown.state.dead = true;
  const state = {};
  shown = { key, state };
  aside.hidden = false;
  layout.classList.add('with-panel');
  clear(aside);
  try {
    if (id) await renderCard(aside, Number(id), state);
    else await renderCoverage(aside);
  } catch (error) {
    if (mine !== generation) return;
    if (!(error instanceof Refusal)) throw error;
    clear(aside).append(
      h('p', { class: 'problem', role: 'alert' }, error.message),
      h('button', { type: 'button', onclick: () => ui.navigate({ card: null, panel: null }) }, 'Close'),
    );
    return;
  }
  if (mine !== generation) return;
  if (state.focus) state.focus();
  else aside.querySelector('h2')?.focus();
}

// Whether the person is in the middle of typing in the panel, in which case nothing is replaced.
function typing(state) {
  const field = document.activeElement;
  const inside = field && aside.contains(field) && field.matches('input[type=text], textarea');
  return Boolean(state.dirty || (inside && field.value !== ''));
}

function banner(text, onLoad) {
  aside.querySelector('.changed')?.remove();
  aside.prepend(
    h(
      'div',
      { class: 'notice changed', role: 'status' },
      text,
      ' ',
      h('button', { type: 'button', class: 'link load-current', onclick: onLoad }, 'Load the current version'),
    ),
  );
}

// Something changed on the server. Show it, unless the person is typing: then offer it.
export async function refresh(events) {
  if (!shown) return;
  const { key, state } = shown;
  if (key === 'coverage') {
    await renderCoverage(aside);
    return;
  }
  const id = Number(key.split(':')[1]);
  if (!store.nodes.has(id)) {
    aside.querySelector('.changed')?.remove();
    clear(aside).append(h('p', { class: 'quiet' }, `Item #${id} is no longer there.`));
    return;
  }
  // An item that adds or drops a reference to this one in its words changes its "Mentioned in",
  // which is a list read from the server, not from the store.
  const mark = `[[node:${id}]]`;
  const rewording = (event) => JSON.stringify([event.before, event.after]).includes(mark);
  const touched = events.some(
    (event) => event.node_id === id || event.other_node_id === id || event.node_id === null || rewording(event),
  );
  if (!touched) return;
  if (typing(state)) {
    const who = events.find((event) => event.node_id === id);
    banner(`This item changed${who ? ` (${who.actor})` : ''} while you were typing, and what you typed is still here.`, () => state.reload(true));
    return;
  }
  await state.reload(false);
  if (state.skipped) {
    banner('This item changed while you were choosing something, and what you chose is still here.', () => state.reload(true));
  }
}
