// The shared detail panel: one epic, story, task, requirement, decision or fact, or the coverage
// report. What it shows is in the address (sel=node:12, sel=fact:5, or panel=coverage), so a link
// opens it on any page. The panel is mounted once and keeps its key while the page, the drawers or
// the Graph's presentation change, so a draft typed in it is never drawn over.

import { Refusal } from './api.js';
import { parseSel, ui } from './ctx.js';
import { clear, h } from './dom.js';
import { renderCard } from './card.js';
import { renderCoverage } from './coverage.js';
import { icon } from './icons.js';
import { loadingView, renderRecord, renderRule, stateView } from './records.js';
import { store } from './store.js';

const aside = document.getElementById('panel');
const layout = document.getElementById('layout');
let shown = null; // { key, state }
let opener = null; // what had the focus before the panel opened, to give it back
let openerAt = null; // where to look for it again if the page drew it anew: { region, href }
let lastOutside = null; // the last place outside the panel that had the focus: { element, at }
let generation = 0;

// A link the page may draw again (a search result, a row of a list) is found again by where it is
// and where it leads, so closing the panel still gives the focus back to it.
function whereIs(element) {
  const href = element instanceof Element ? element.getAttribute('href') : null;
  const region = element instanceof Element ? element.closest('#drawer, #page') : null;
  return href && region ? { region: region.id, href } : null;
}

// The drawer draws its list again before the panel opens, which takes the focus off the link that
// was used, so the place is noted as the focus arrives, not when the panel opens.
document.addEventListener('focusin', (event) => {
  if (!aside.contains(event.target)) lastOutside = { element: event.target, at: whereIs(event.target) };
});

function giveBackFocus() {
  if (opener && document.contains(opener)) opener.focus();
  else if (openerAt) document.getElementById(openerAt.region)?.querySelector(`a[href="${CSS.escape(openerAt.href)}"]`)?.focus();
  opener = null;
  openerAt = null;
}

export function hide() {
  generation += 1;
  if (!shown) return;
  shown.state.dead = true;
  shown = null;
  aside.hidden = true;
  layout.classList.remove('with-panel');
  clear(aside);
  giveBackFocus();
}

// A panel with only a message in it: the server's refusal, or that the address names nothing.
const closes = () => ui.navigate({ sel: null, panel: null });
const say = (message, refusal = true) => clear(aside).append(stateView(message, { refusal, onClose: closes }));

function draw(wanted, state) {
  if (wanted.kind === 'node') return renderCard(aside, wanted.id, state);
  if (wanted.kind === 'rule') return renderRule(aside, wanted.id, state);
  return renderRecord(aside, wanted.kind, wanted.id, state);
}

export async function show(params) {
  const typed = params.get('sel');
  const wanted = parseSel(typed);
  const key = wanted ? `${wanted.kind}:${wanted.id}` : typed ? `unknown:${typed}` : params.get('panel') === 'coverage' ? 'coverage' : null;
  if (key === null) return hide();
  if (shown && shown.key === key) return;
  if (!shown) {
    const held = document.activeElement;
    if (held && held !== document.body) {
      opener = held;
      openerAt = whereIs(held);
    } else if (lastOutside) {
      opener = lastOutside.element;
      openerAt = lastOutside.at;
    }
  }
  const mine = ++generation;
  // A detail still being fetched for the one that was open must not be drawn over this one.
  if (shown) shown.state.dead = true;
  const state = {};
  shown = { key, state };
  aside.hidden = false;
  layout.classList.add('with-panel');
  clear(aside).append(loadingView());
  if (key.startsWith('unknown:')) {
    say('That address does not name something the board can show. It looks like sel=node:12, sel=rule:3, sel=decision:7 or sel=fact:5.');
    return;
  }
  try {
    if (wanted) await draw(wanted, state);
    else await renderCoverage(aside);
  } catch (error) {
    if (mine !== generation) return;
    if (!(error instanceof Refusal)) throw error;
    say(error.message);
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
      icon('info', { size: 16 }),
      h('span', {}, text),
      h('button', { type: 'button', class: 'link load-current', onclick: onLoad }, 'Load the current version'),
    ),
  );
}

const named = (value) => value !== null && value !== undefined;

// What an event says about the open item. `direct` is that it names the item itself, so that the
// panel can say who changed it; `touches` is that the panel may now read differently.
function relevance(event, kind, id, state) {
  const mark = `[[${kind}:${id}]]`;
  const direct = kind === 'node' ? event.node_id === id || event.other_node_id === id : event[`${kind}_id`] === id;
  // An item that adds or drops a reference to this one in its words changes its "Mentioned in",
  // which is a list read from the server, not from the store.
  const rewording = JSON.stringify([event.before, event.after]).includes(mark);
  if (kind === 'fact' || kind === 'decision') {
    // News about something it concerns changes how the panel names it.
    const concerned = state.concerns
      ? (named(event.node_id) && state.concerns.node.includes(event.node_id)) ||
        (named(event.rule_id) && state.concerns.rule.includes(event.rule_id)) ||
        (named(event.decision_id) && state.concerns.decision.includes(event.decision_id))
      : false;
    return { direct, touches: direct || rewording || concerned };
  }
  // A requirement, an epic, a story or a task also reads the facts, decisions and requirements
  // that concern it, and an event that names no item at all may be about any of them.
  const story = kind === 'rule' ? Number(aside.querySelector('.rule-detail')?.dataset.story) : null;
  const context = named(event.fact_id) || named(event.decision_id) || named(event.rule_id) || event.node_id === null;
  return { direct, touches: direct || rewording || context || (story !== null && event.node_id === story) };
}

// Something changed on the server. Show it, unless the person is typing: then offer it.
export async function refresh(events) {
  if (!shown) return;
  const { key, state } = shown;
  if (key === 'coverage') {
    await renderCoverage(aside);
    return;
  }
  if (key.startsWith('unknown:')) return;
  const [kind, idText] = key.split(':');
  const id = Number(idText);
  if (kind === 'node' && !store.nodes.has(id)) {
    aside.querySelector('.changed')?.remove();
    say(`Item #${id} is no longer there.`, false);
    return;
  }
  const seen = events.map((event) => relevance(event, kind, id, state));
  if (!seen.some((one) => one.touches)) return;
  if (typing(state)) {
    const who = events.find((event, at) => seen[at].direct);
    banner(`${who ? `This item changed (${who.actor})` : 'Something that belongs with this item changed'} while you were typing, and what you typed is still here.`, () => state.reload(true));
    return;
  }
  try {
    await state.reload(false);
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    aside.querySelector('.panel-problem')?.remove();
    aside.prepend(h('p', { class: 'notice refusal panel-problem', role: 'alert' }, icon('alert', { size: 16 }), h('span', {}, error.message)));
    return;
  }
  if (state.skipped) {
    banner('This item changed while you were choosing something, and what you chose is still here.', () => state.reload(true));
  }
}
