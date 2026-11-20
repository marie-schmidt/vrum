// The Graph page. It opens on "Structure", the whole project at once, and a search box at the top (the
// "Find an epic, story, task, acceptance criterion or note to look at" box, graph-focus.js) chooses what the tabs
// look at, so nothing has to be searched for first. Under it three ways to look, one question each:
//   Structure  how does it all fit together?                       (graph-structure.js)
//   Around     what do we know and what was decided about this?   (graph-around.js)
//   Order      what must come first, and what does this block?     (graph-order.js)
// This file is the shell: it reads the address, keeps the focus and the trail, and hands each tab its
// part of the page. (Everything as a list you can filter is its own view, Lists, in lists.js.)
//
// The address: `#/graph?tab=structure|around|order` (Structure when there is none or it is not one of
// these), `focus=<ref>` (any of epic:3, story:12, task:40, rule:3, note:7; none means the whole project),
// `trail=<refs>` (the items looked at, newest last) and the choices of each tab, which it keeps in the
// address and which are forgotten when the tab is left. An address from before the Timeline, the
// Check-up, the list beside Around and the Agent view of the Graph went (`tab=timeline`, `check=`,
// `chains=`, `agent=`, `peek=`) is put right without a word. No tab has a side panel: an item chosen in
// a picture opens its page, where its details and the Agent view are.
//
// Each tab is a module that follows one contract: mount(element, api), update(api), refresh(events) and
// unmount(), where api = { focus, params, setParams(changes, { replace }), setFocus(ref), open(ref),
// store, readOnly }. A module may also export KEYS, the address keys that are its own. A
// tab whose module is not there yet says so instead of breaking the page.

import { can, openItem, ui } from './ctx.js';
import { clear, h, put } from './dom.js';
import { openCreate } from './create.js';
import { createFocusBar } from './graph-focus.js';
import { resolve } from './graph-model.js';
import { icon } from './icons.js';
import { parseItemRef } from './labels.js';
import { ensureContext, store } from './store.js';

const TABS = [
  { id: 'structure', name: 'Structure', ask: 'How does it all fit together?', icon: 'structure', load: () => import('./graph-structure.js') },
  { id: 'around', name: 'Around', ask: 'What do we know and what was decided about this?', icon: 'around', load: () => import('./graph-around.js') },
  { id: 'order', name: 'Order', ask: 'What must come first, and what does this block?', icon: 'order', load: () => import('./graph-order.js') },
];
const IDS = TABS.map((tab) => tab.id);
const DEFAULT_TAB = 'structure';

// The address keys of each tab, until its module says (KEYS) which are its own.
const OWN = {
  structure: ['scope', 'level', 'labels', 'colour', 'links'],
  around: ['reach', 'wide'],
  order: ['mode', 'phase', 'chain', 'budget', 'blocked', 'pick'],
};
// What an old address may still carry: the Timeline's own choices, the keys of the Check-up and of
// "Chains in", Order's "only this chain" (a chain is shown on its own now), and Around's Agent view and
// opened record (the Agent view is on the page of an item, and a record opens its page).
const OLD_TIMELINE_KEYS = ['since', 'about', 'kind', 'rec', 'older', 'status', 'order'];
const OLD_KEYS = ['check', 'chains', 'only', 'agent', 'peek'];
const MOST_TRAIL = 8;

let root = null;
let params = new URLSearchParams();
let parts = null; // the elements
let focusBar = null;
let modules = new Map(); // tab -> its module, once loaded
let shown = null; // { tab, module }: the tab on screen
let generation = 0;
let pending = null; // the tab whose module is being loaded
let ready = false;

const tabOf = (query) => (IDS.includes(query.get('tab')) ? query.get('tab') : DEFAULT_TAB);
const keysOf = (tab) => modules.get(tab)?.KEYS || OWN[tab] || [];
const focusOf = (query) => (parseItemRef(query.get('focus')) ? query.get('focus') : null);

// What to take from an old address so that it reads as a plain one: a tab that is not there any more,
// with the Timeline's choices when it was that, and the keys of the strips that went.
function staleChanges(query) {
  const tab = query.get('tab');
  const changes = {};
  if (tab && !IDS.includes(tab)) {
    changes.tab = null;
    if (tab === 'timeline') for (const key of OLD_TIMELINE_KEYS) if (query.has(key)) changes[key] = null;
  }
  for (const key of OLD_KEYS) if (query.has(key)) changes[key] = null;
  return Object.keys(changes).length ? changes : null;
}

// The items looked at, oldest first, ending in the focus: what the address says if it is about this
// focus, otherwise just the focus.
function pathOf(query) {
  const focus = focusOf(query);
  if (!focus) return [];
  const said = (query.get('trail') || '').split(',').filter((ref) => parseItemRef(ref));
  return said.length && said[said.length - 1] === focus ? said.slice(-MOST_TRAIL) : [focus];
}

// What goes from the address when one tab is left for another: the keys that were the first's own.
const leaving = (from, to) => Object.fromEntries(keysOf(from).filter((key) => !keysOf(to).includes(key)).map((key) => [key, null]));

// -- the page's life -------------------------------------------------------------------------------

export function mount(element, initial) {
  root = element;
  params = initial;
  modules = new Map();
  shown = null;
  pending = null;
  ready = false;
  generation += 1;
  build();
  begin();
}

export function update(next) {
  params = next;
  if (root && ready) draw();
}

export function refresh(events) {
  if (!root || !ready) return;
  draw({ news: true });
  shown?.module.refresh(events);
}

export function unmount() {
  generation += 1;
  pending = null;
  shown?.module.unmount();
  shown = null;
  focusBar?.destroy();
  root = null;
  focusBar = null;
}

// The acceptance criteria and notes are needed to name any item, so they are read before the
// page is drawn.
async function begin() {
  const mine = generation;
  try {
    await ensureContext();
  } catch (error) {
    if (mine !== generation) return;
    put(parts.view, h('section', { class: 'graph-panel graph-state', role: 'alert' }, h('h2', {}, 'The Graph could not be loaded'), h('p', { class: 'problem' }, error.message || 'The board could not read the project.'), h('button', { type: 'button', class: 'small', onclick: () => location.reload() }, 'Try again')));
    return;
  }
  if (mine !== generation || !root) return;
  ready = true;
  draw();
}

// -- what the person does ------------------------------------------------------------------------------

// Go to another tab, forgetting the choices of the one left. The tab that opens by itself is not
// written in the address.
function toTab(tab, changes = {}) {
  ui.navigate({ ...leaving(tabOf(params), tab), tab: tab === DEFAULT_TAB ? null : tab, ...changes });
}

// Choose an item in the box at the top: the trail starts again from it.
function choose(ref) {
  ui.navigate({ focus: ref, trail: null });
}

// Choose an item from a view: the trail grows (a click on a neighbour, say).
function setFocus(ref) {
  const path = pathOf(params);
  const next = path[path.length - 1] === ref ? path : [...path, ref].slice(-MOST_TRAIL);
  ui.navigate({ focus: ref, trail: next.length > 1 ? next.join(',') : null });
}

function goBack() {
  const path = pathOf(params);
  if (path.length < 2) return;
  const next = path.slice(0, -1);
  ui.navigate({ focus: next[next.length - 1], trail: next.length > 1 ? next.join(',') : null });
}

function jumpTo(index) {
  const next = pathOf(params).slice(0, index + 1);
  ui.navigate({ focus: next[next.length - 1], trail: next.length > 1 ? next.join(',') : null });
}

function clearFocus() {
  ui.navigate({ focus: null, trail: null });
}

function newItem() {
  const focus = resolve(focusOf(params));
  if (focus?.kind === 'epic') openCreate({ kind: 'story', parent: focus.ref });
  else if (focus?.kind === 'story') openCreate({ kind: 'task', parent: focus.ref });
  else openCreate({ kind: 'epic' });
}

// -- the page ---------------------------------------------------------------------------------------------

function build() {
  focusBar = createFocusBar({ choose, clear: clearFocus, back: goBack, jump: jumpTo });
  const tabs = h(
    'div',
    { class: 'graph-tabs', role: 'tablist', 'aria-label': 'Ways to look at the project', id: 'graph-tabs' },
    TABS.map((tab, index) =>
      h(
        'button',
        {
          type: 'button',
          role: 'tab',
          id: `graph-tab-${tab.id}`,
          class: 'graph-tab',
          'data-tab': tab.id,
          'aria-selected': 'false',
          'aria-controls': 'graph-view',
          tabindex: '-1',
          onclick: () => toTab(tab.id),
          onkeydown: (event) => {
            const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
            const to = event.key === 'Home' ? 0 : event.key === 'End' ? TABS.length - 1 : step ? (index + step + TABS.length) % TABS.length : null;
            if (to === null) return;
            event.preventDefault();
            toTab(TABS[to].id);
            parts.tabs[TABS[to].id].focus();
          },
        },
        icon(tab.icon, { size: 18 }),
        h('span', { class: 'graph-tab-text' }, h('span', { class: 'graph-tab-name' }, tab.name), h('span', { class: 'graph-tab-ask' }, tab.ask)),
      ),
    ),
  );
  parts = {
    view: h('div', { id: 'graph-view', class: 'graph-view', role: 'tabpanel', tabindex: '-1' }, h('p', { class: 'loading', role: 'status' }, 'Loading the graph…')),
    tabs: Object.fromEntries([...tabs.querySelectorAll('button')].map((button) => [button.dataset.tab, button])),
    create: h('button', { type: 'button', class: 'primary', id: 'graph-create', onclick: newItem }, icon('plus', { size: 16 }), 'New item'),
    actions: h('div', { class: 'page-actions' }),
  };
  put(parts.actions, can('write') ? parts.create : null);
  clear(root).append(
    h(
      'div',
      { class: 'graph-shell', id: 'graph-explore' },
      h(
        'header',
        { class: 'graph-head' },
        h('div', { class: 'graph-title' }, h('h1', {}, 'Graph')),
        focusBar.element,
        parts.actions,
      ),
      tabs,
      parts.view,
    ),
  );
}

// What an address whose tab could not be loaded shows: a plain word, and the way to another tab.
function notBuilt(tab) {
  const known = TABS.find((entry) => entry.id === tab);
  const other = TABS.find((entry) => entry.id === (tab === DEFAULT_TAB ? 'around' : DEFAULT_TAB));
  return h(
    'section',
    { class: 'graph-panel graph-state', id: 'graph-not-built', role: 'alert' },
    h('div', { class: 'graph-empty-art', 'aria-hidden': 'true' }, icon(known.icon, { size: 36 })),
    h('h2', {}, `${known.name} could not be loaded`),
    h('p', { class: 'quiet' }, `The page for ${known.name} did not load. Try again, or go to ${other.name}.`),
    h('div', { class: 'graph-actions' }, h('button', { type: 'button', class: 'small', onclick: () => location.reload() }, 'Try again'), h('button', { type: 'button', class: 'small', onclick: () => toTab(other.id) }, icon(other.icon, { size: 14 }), `Go to ${other.name}`)),
  );
}

// The API a tab is given. It is made new each time, so a tab always sees the address as it is now.
function apiFor() {
  return {
    focus: focusOf(params),
    params,
    // A tab asking for another tab (`tab` in the changes) leaves its own choices behind.
    setParams: (changes, { replace = false } = {}) => {
      const asked = 'tab' in changes && IDS.includes(changes.tab) ? changes.tab : null;
      const to = asked && asked !== tabOf(params) ? asked : null;
      const next = asked === DEFAULT_TAB ? { ...changes, tab: null } : changes;
      ui.navigate(to ? { ...leaving(tabOf(params), to), ...next } : next, { replace });
    },
    setFocus,
    open: openItem,
    store,
    readOnly: false,
  };
}

async function showTab(tab) {
  const mine = ++generation;
  pending = tab;
  shown?.module.unmount();
  shown = null;
  put(parts.view, h('p', { class: 'loading', role: 'status' }, 'Loading the graph…'));
  let module = modules.get(tab);
  if (!module) {
    try {
      module = await TABS.find((entry) => entry.id === tab).load();
      modules.set(tab, module);
    } catch {
      if (mine !== generation || !root) return;
      pending = null;
      put(parts.view, notBuilt(tab));
      return;
    }
  }
  if (mine !== generation || !root) return;
  pending = null;
  clear(parts.view);
  shown = { tab, module };
  module.mount(parts.view, apiFor());
}

function draw({ news = false } = {}) {
  const tab = tabOf(params);
  const focus = focusOf(params);
  const item = focus ? resolve(focus) : null;
  const path = pathOf(params).map((ref) => resolve(ref)).filter(Boolean);
  focusBar.update({ item, path, missing: Boolean(focus && !item) });
  for (const [id, button] of Object.entries(parts.tabs)) {
    button.setAttribute('aria-selected', String(id === tab));
    button.tabIndex = id === tab ? 0 : -1;
  }
  parts.view.setAttribute('aria-labelledby', `graph-tab-${tab}`);
  parts.view.dataset.tab = tab;
  if (shown?.tab === tab) {
    if (!news) shown.module.update(apiFor());
  } else if (pending !== tab) {
    showTab(tab);
  }
  // An address from before the Timeline and the Check-up went is put right, with nothing said.
  const stale = news ? null : staleChanges(params);
  if (stale) ui.navigate(stale, { replace: true });
}
