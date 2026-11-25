// The page of what needs a person (#/needs): an inbox of changes. Everything only a person can settle, which the server works out and this page
// only draws (`needs_a_person`), is one compact card (needs-cards.js): what it is, the level of the work it is about, its facts, a short
// preview and the one bar every kind of change ends with (decision-bar.js). The whole change, its context and its conversation are on the
// review page (review.js), which a card's title and Open lead to.
//
// The cards are in groups, by what the person chooses with "Group by" (kept in the address as `group=`, Level saying nothing): by LEVEL (plans,
// epics, stories, tasks, notes), by TOPIC (what the changes are about) or by DECISION (to approve, to answer, to look into). The server says
// every row's level, topic and decision and this page only puts the rows in the groups those words name, so a count of the groups adds up to the
// rows and nothing is lost or drawn twice. The moves the server calls routine are a closed group of their own, and so are the plans that
// wait for their agent, in every way of grouping (needs-groups.js, needs-waiting.js).
//
// The page is how much was viewed ("Reviewed 3 of 16"), the choice of grouping, a rail of every change by group (a drop-down on a narrow window)
// and the cards. Viewed is the person's own mark, kept in the page's memory only (never in the browser's storage: it lives until the board is
// reloaded) and taken off by itself when the change changes. The keys move and open and never decide. With nothing to review the page says so.
//
// The list is the same answer the number in the sidebar is drawn from (needs-state.js), so they agree, and it is drawn again whenever the board
// hears news that can change it, so another person's answer or an agent's new question shows at once.

import { Refusal } from './api.js';
import { ui } from './ctx.js';
import { h, put } from './dom.js';
import { hoverCard } from './hovercard.js';
import { icon } from './icons.js';
import { keyButton } from './key-button.js';
import { cardOf } from './needs-cards.js';
import { indexChanged, isPlanNews, newsAbout, rereadPlans, watchDetails } from './needs-details.js';
import { keepKeys, keysContent } from './needs-keys.js';
import { BY, BY_WORDS, byOf, draftOf, drawSaid, fingerprintOf, forgetDrafts, judge, keepViews, prepare, said, sectionsOf, viewOf } from './needs-model.js';
import { fillRail } from './needs-rail.js';
import { currentNeeds, onNeeds, readNeeds } from './needs-state.js';
import { agentGroup, readWaitingQuietly } from './needs-waiting.js';
import { pageHeader } from './page.js';
import { explainProblem, forgetLost, noticeRevision, settleProblem } from './plan-comments.js';
import { loadingView, rememberFocus } from './records.js';
import { onChange } from './store.js';

let drawn = null; // the places of the page on show, and what was drawn last
let stops = [];

// Where the focus goes after a decision: the next change. `next` is remembered when a button is pressed, `after` once the server said yes.
let next = null;
let after = null;
// Whether the closed groups are open, kept while the page is drawn again (and the rail's own entries, which are separate).
let routineOpen = false;
let agentOpen = false;
let hideViewed = false;
const railOpen = { routine: false, agent: false };
let waitingPlans = null; // the plans that wait for their agent to revise them, or null before they are read

// What the person has done with each change (viewed, folded), the words typed into a comment and what a decision left undone are kept
const drawSaidHere = () => drawSaid(drawn?.said);

// -- moving on after a decision ----------------------------------------------------------------------------

const neighbourOf = (key) => {
  // A card in a group that is closed cannot take the focus.
  const cards = (drawn?.cards ?? []).filter((card) => card.li.checkVisibility());
  const at = cards.findIndex((card) => card.change.key === key);
  if (at < 0) return null;
  const near = cards[at + 1] ?? cards[at - 1];
  return near ? near.change.key : null;
};

// Once a decision is taken and its change has gone from the list, the focus goes to the change that was next (or the one before it,
// for the last), and to the page when nothing is left.
function moveOn() {
  if (!drawn || !after || drawn.byKey.has(after.gone)) return;
  const target = after.to ? drawn.byKey.get(after.to) : null;
  after = null;
  if (target) target.head.focus();
  else (drawn.body.querySelector('#needs-empty') || document.getElementById('page'))?.focus();
}

const hooksFor = (change) => ({
  say(lines) {
    if (!lines.length) return;
    said.lines = lines;
    drawSaidHere();
  },
  stood(words) {
    said.problems = [...said.problems, words];
    drawSaidHere();
  },
  draft: draftOf,
  // Draws the page again, and puts the focus on the control with this key (the field that just opened, say).
  redraw: (focusKey = null) => {
    render();
    if (focusKey && drawn) drawn.layout.querySelector(`[data-focus-key="${CSS.escape(focusKey)}"]`)?.focus();
  },
  pressed: () => {
    next = { gone: change.key, to: neighbourOf(change.key) };
  },
  decided: () => {
    after = { ...(next && next.gone === change.key ? next : { gone: change.key, to: null }), until: Date.now() + 5000 };
    next = null;
    moveOn();
  },
  refused: () => {
    next = null;
  },
  // A plan was refused (it may have moved on while it was read): read the list and the plans again at once, not at the next news.
  reread: async () => {
    const plan = change.item.plan ? change.item.plan.id : null;
    if (plan !== null) explainProblem(plan);
    rereadPlans();
    try {
      await readNeeds();
    } catch (error) {
      if (!(error instanceof Refusal)) throw error;
    }
    if (plan !== null) settleProblem(plan);
  },
  // Changes were asked for: the plan is the agent's turn now, so the group of plans that wait for their agent is read again.
  sentChanges: () => readWaiting(),
});

// -- the page ----------------------------------------------------------------------------------------------------

// What a group's heading says it is: its icon, and for a topic whose rows were put together by how alike their work is, the icon with the
// reason in a hover card, so that a person sees why unlike epics are together.
function groupMark(group) {
  const mark = icon(group.icon, { size: 16 });
  if (group.basis !== 'meaning') return mark;
  const holder = h('span', { class: 'group-basis', tabindex: 0, role: 'img', 'aria-label': 'Alike in meaning', dataset: { basis: 'meaning' } }, mark);
  return h('span', { class: 'chip-holder' }, holder, hoverCard(holder, h('p', { class: 'verdict-hover' }, 'Alike in meaning')));
}

function groupSection(group, cards, by) {
  return h(
    'section',
    { class: 'needs-group', dataset: { by, group: group.id } },
    h('header', { class: 'group-head' }, groupMark(group), h('h2', { class: 'group-title', dataset: { words: group.by === 'topic' ? 'theirs' : null } }, group.title), h('span', { class: 'group-count' }, String(cards.length))),
    h('ul', { class: 'plain needs-rows', 'aria-label': group.title }, cards.map((card) => card.li)),
  );
}

// The group of routine moves: closed until it is opened, with how many there are, and, inside, every one of them as a whole card. It is a plain
// `details`, so the keyboard and a screen reader open it as they open any other.
function routineSection(cards, by) {
  const box = h(
    'details',
    {
      class: 'routine-group',
      open: routineOpen,
      ontoggle: () => {
        routineOpen = box.open;
      },
    },
    h(
      'summary',
      {
        class: 'routine-head',
        dataset: { count: String(cards.length), focusKey: 'routine-head' },
        // The state is kept when the summary is pressed, not when the browser later says it toggled, so a page drawn again in between does not forget it.
        onclick: () => {
          routineOpen = !box.open;
        },
      },
      icon('chevron_right', { size: 16, className: 'routine-chevron' }),
      icon('move', { size: 16 }),
      h('h2', { class: 'routine-title group-title' }, 'Routine moves'),
      h('span', { class: 'group-count' }, String(cards.length)),
    ),
    h('ul', { class: 'plain needs-rows', 'aria-label': 'Routine moves' }, cards.map((card) => card.li)),
  );
  return { box, section: h('section', { class: 'needs-group', dataset: { by, group: 'routine' } }, box) };
}

// Where j and k stop, in the order the page is drawn: every card that can be reached, and the summary of the group of routine moves
// (which is where they stop while it is closed, and which `o` opens and closes).
function keyStops() {
  if (!drawn) return [];
  const found = [];
  let met = false;
  for (const card of drawn.cards) {
    if (card.change.routine && drawn.routineBox) {
      if (!met) {
        met = true;
        const box = drawn.routineBox;
        const summary = box.querySelector('summary');
        found.push({
          li: summary,
          head: summary,
          change: null,
          verdicts: () => [],
          toggleCollapsed: () => {
            routineOpen = !box.open;
            box.open = routineOpen;
          },
          toggleViewed: () => {},
        });
      }
      if (!drawn.routineBox.open) continue;
    }
    found.push(card);
  }
  return found;
}

const listen = (target, type, fn) => {
  target.addEventListener(type, fn);
  return () => target.removeEventListener(type, fn);
};

// -- the plans that wait for their agent ---------------------------------------------------------------------

// Reads them and draws the page again when they changed. A read the server will not answer leaves the group as it was.
async function readWaiting() {
  const found = await readWaitingQuietly();
  if (!drawn || found === null) return;
  const was = JSON.stringify(waitingPlans);
  waitingPlans = found;
  if (JSON.stringify(found) !== was) render();
}

// Goes to a change: its card scrolls into view and its header takes the focus. A change in the closed group of routine moves opens it.
function go(key) {
  const card = drawn?.byKey.get(key);
  if (!card) return;
  if (card.change.routine && drawn.routineBox && !drawn.routineBox.open) {
    routineOpen = true;
    drawn.routineBox.open = true;
  }
  card.li.scrollIntoView({ block: 'start' });
  card.head.focus({ preventScroll: true });
}

// Opens a change's review page, in the order the person sees now.
const openChange = (stop) => ui.navigate({ change: stop.change.id, group: drawn.by === 'level' ? null : drawn.by }, { page: 'review' });

// Jumps to the next unviewed change (starting after the focused card, or the first unviewed overall).
function jumpNextUnreviewed() {
  if (!drawn) return;
  const unviewed = drawn.cards.filter((card) => viewOf(card.change.key).viewed === null);
  if (!unviewed.length) return;
  const active = document.activeElement;
  const currentAt = drawn.cards.findIndex((card) => card.li.contains(active));
  const target = (currentAt >= 0 ? drawn.cards.slice(currentAt + 1).find((card) => viewOf(card.change.key).viewed === null) : null) ?? unviewed[0];
  go(target.change.key);
}

// Hides viewed cards when Hide viewed is active, keeping groups tidy.
function applyHideViewed() {
  if (!drawn) return;
  for (const card of drawn.cards) card.li.hidden = hideViewed && viewOf(card.change.key).viewed !== null;
  for (const section of drawn.body.querySelectorAll('section.needs-group:not([data-group="agent"])')) {
    const rows = [...section.querySelectorAll('li.needs-row')];
    section.hidden = rows.length > 0 && rows.every((row) => row.hidden);
  }
}

// How much was viewed, and the rail, from what is drawn and what the person has marked.
function chrome() {
  if (!drawn || !drawn.list) return;
  const { changes } = drawn;
  const seen = changes.filter((change) => viewOf(change.key).viewed !== null).length;
  if (!seen && hideViewed) hideViewed = false;
  drawn.overview.hidden = !changes.length;
  drawn.rail.hidden = !changes.length;
  drawn.railBox.hidden = !changes.length;
  put(drawn.reviewed, `Reviewed ${seen} of ${changes.length}`);
  drawn.bar.style.width = changes.length ? `${Math.round((100 * seen) / changes.length)}%` : '0%';
  drawn.overview.dataset.reviewed = String(seen);
  drawn.hideViewedBtn.hidden = seen === 0;
  drawn.hideViewedBtn.setAttribute('aria-pressed', String(hideViewed));
  drawn.nextUnreviewedBtn.hidden = seen === 0 || seen >= changes.length;
  applyHideViewed();
  drawn.groupSelect.value = drawn.by;
  fillRail(drawn.rail, drawn.railSelect, drawn.railSections, {
    isViewed: (change) => viewOf(change.key).viewed !== null,
    go,
    openOf: (id) => railOpen[id],
    toggled: (id, open) => {
      railOpen[id] = open;
    },
  });
}

// Draws the whole page from the list and from what was read for it. It is drawn again, never patched: when news comes, when an answer
// to a read arrives, and so on; what the person did (viewed, folded, a comment being typed, the focus) is kept.
function render() {
  if (!drawn || !drawn.list) return;
  const { list, by } = drawn;
  const { changes, entries } = prepare(list);
  keepViews(changes.map((change) => change.key));

  put(drawn.summary, list.total ? `${list.total} waiting` : 'Nothing waiting');
  drawn.summary.dataset.total = String(list.total);
  const restore = rememberFocus(drawn.layout);

  // A plan whose revision is newer than the one the page drew last: the comments written for the old one are not kept (and it says so on its
  // review page).
  for (const change of changes) if (change.look === 'plan') noticeRevision(change.item.plan.id, change.item.plan.revision);

  const cards = [];
  const byKey = new Map();
  const build = (change) => {
    const entry = entries.get(change.key);
    const view = judge(change, entry);
    const card = cardOf(change, { view, fp: fingerprintOf(change, entry), entry, asOf: list.as_of, hooks: hooksFor(change), onChrome: chrome, by });
    cards.push(card);
    byKey.set(change.key, card);
    return card;
  };

  const { groups, routine } = sectionsOf(changes, by);
  const sections = [];
  const railSections = [];
  for (const group of groups) {
    sections.push(groupSection(group, group.changes.map(build), by));
    railSections.push({ id: group.id, by, title: group.title, icon: group.icon, count: group.changes.length, changes: group.changes });
  }
  let routineBox = null;
  if (routine.length) {
    const made = routineSection(routine.map(build), by);
    routineBox = made.box;
    sections.push(made.section);
    railSections.push({ id: 'routine', by, title: 'Routine moves', icon: 'move', count: routine.length, changes: routine, shut: true });
  }
  const waiting = agentGroup(waitingPlans, list.as_of, { open: agentOpen, toggled: (open) => { agentOpen = open; }, by });
  if (waiting) {
    sections.push(waiting.section);
    railSections.push({ id: 'agent', by, title: 'Waiting for their agent', icon: 'clock', count: waitingPlans.length, changes: [], plans: waitingPlans, shut: true });
  }
  Object.assign(drawn, { changes, cards, byKey, routineBox, railSections });

  put(
    drawn.body,
    list.total || waiting
      ? [sections, list.left_out ? h('p', { id: 'needs-left-out', class: 'quiet needs-left-out' }, `${list.left_out} more not shown`) : null]
      : h('p', { id: 'needs-empty', class: 'needs-empty', tabindex: -1 }, 'Nothing to review'),
  );
  chrome();
  // A decision that was taken has a moment to be heard of; after that the focus is the person's own again.
  if (after && Date.now() > after.until) after = null;
  if (after && !byKey.has(after.gone)) moveOn();
  else restore();
}

// Draws an answer of the server. The same answer is drawn once: a page that is opened while a read is on its way is told of it twice.
function draw(list) {
  if (!drawn || list === drawn.list) return;
  drawn.list = list;
  render();
}

function failed(error) {
  if (!(error instanceof Refusal)) throw error;
  if (drawn) put(drawn.body, h('p', { class: 'notice refusal', role: 'alert' }, icon('alert', { size: 16 }), h('span', {}, error.message)));
}

export function mount(element, params) {
  const summary = h('p', { class: 'sr-only', id: 'needs-summary', role: 'status' });
  const saidBox = h('div', { class: 'needs-said-box', id: 'needs-said' });
  const reviewed = h('p', { class: 'needs-reviewed', id: 'needs-reviewed' });
  const bar = h('span', { class: 'needs-progress-bar' });
  const by = byOf(params ? params.get('group') : null);
  const groupSelect = h('select', { id: 'needs-group', onchange: () => ui.navigate({ group: groupSelect.value === 'level' ? null : groupSelect.value }) }, BY.map((one) => h('option', { value: one }, BY_WORDS[one])));
  groupSelect.value = by;
  const hideViewedBtn = h(
    'button',
    {
      type: 'button',
      id: 'needs-hide-viewed',
      class: 'small ghost needs-triage',
      'aria-pressed': 'false',
      hidden: true,
      onclick: () => {
        hideViewed = !hideViewed;
        chrome();
      },
    },
    'Hide viewed',
  );
  const nextUnreviewedBtn = h(
    'button',
    {
      type: 'button',
      id: 'needs-next-unreviewed',
      class: 'small ghost needs-triage',
      hidden: true,
      onclick: jumpNextUnreviewed,
    },
    'Next unreviewed',
  );
  const overview = h(
    'div',
    { class: 'needs-overview', id: 'needs-overview', hidden: true },
    reviewed,
    h('span', { class: 'needs-progress', 'aria-hidden': 'true' }, bar),
    hideViewedBtn,
    nextUnreviewedBtn,
    h('label', { class: 'needs-group-choice', for: 'needs-group' }, h('span', {}, 'Group by'), groupSelect),
    keyButton(keysContent(), { id: 'needs-keys-button', label: 'Keys' }),
  );
  const rail = h('nav', { class: 'needs-rail', id: 'needs-rail', 'aria-label': 'Changes', hidden: true });
  const railSelect = h('select', { id: 'needs-rail-select', 'aria-label': 'Go to a change' });
  const railBox = h('div', { class: 'needs-rail-box', hidden: true }, railSelect);
  const body = h('div', { class: 'needs-body', id: 'needs-body' }, loadingView());
  const layout = h('div', { class: 'needs-layout' }, railBox, rail, body);
  drawn = { summary, said: saidBox, overview, reviewed, bar, hideViewedBtn, nextUnreviewedBtn, groupSelect, by, rail, railSelect, railBox, body, layout, routineBox: null, railSections: [], list: null, changes: [], cards: [], byKey: new Map() };
  forgetDrafts();
  next = null;
  after = null;
  put(element, h('div', { class: 'needs-page item-page', id: 'needs-page' }, pageHeader('Needs a person'), summary, saidBox, overview, layout));
  drawSaidHere();
  let waiting = 0;
  watchDetails(() => {
    if (waiting) return;
    waiting = setTimeout(() => {
      waiting = 0;
      render();
    }, 0);
  });
  stops = [
    onNeeds(draw),
    onChange((change) => {
      if (change.events) newsAbout(change.events);
      if (change.events && change.events.some(isPlanNews)) readWaiting();
      if (change.index) indexChanged();
    }),
    keepKeys({ stops: keyStops, open: openChange }),
    () => clearTimeout(waiting),
  ];
  currentNeeds().then(draw, failed);
  readWaiting();
}

export function unmount() {
  stops.forEach((stop) => stop());
  stops = [];
  watchDetails(() => {});
  // What was said about comments that were not kept is said once: it goes with the page. The plans that wait are read again when it is opened.
  forgetLost();
  hideViewed = false;
  waitingPlans = null;
  drawn = null;
}

// The address changed under the page: the way of grouping it asks for.
export function update(params) {
  if (!drawn) return;
  const asked = byOf(params.get('group'));
  if (asked === drawn.by) return;
  drawn.by = asked;
  render();
}

// The list is read again by the board whenever news can change it (needs-state.js) and drawn here, so there is nothing to do.
export function refresh() {}
