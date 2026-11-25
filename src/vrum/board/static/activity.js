// The Activity page (#/activity): everything that happens, newest first, in plain words, and who is
// working on what. A toggle under the title chooses one of three views, and only the chosen one is drawn,
// across the whole width (`tab=changes|working|free` in the address; Changes when there is none):
//   Changes   the server's change feed (the newest changes first, and older ones when asked for),
//             filtered by who, what kind of change, which item and when; the actor and the item are
//             asked of the server and the kind and the days are worked out here on what was read. A run
//             of ticks of the definition of done by one actor on one item is one row (events.js). The
//             filters sit behind a Filter button, open whenever one is set.
//   Working   `who_is_working`: a block for each person or agent, holding their claims in force with a
//             countdown to the lease, the claims that ran out on them, and the last handoff note they
//             left, with buttons to renew or release the claims the person signed in holds (the same
//             operations an agent calls). The countdowns tick only while this view is shown.
//   Free      the ready work nobody holds, ten at a time, with buttons to take it as the person signed
//             in, and a box to claim an item by a word of its title or by its code.
// An item is named by a word of its title (or its code) in the "About" box and in the box to claim by, each
// with a short list of what matches. The filters are in the address, with the prefix act_ (act_actor,
// act_kind, act_item, act_from, act_to), and they stay when the view is changed.

import { Refusal, call } from './api.js';
import { account, can, isMe, itemLink, notify, ui } from './ctx.js';
import { dateField } from './datepicker.js';
import { clear, h, moment, plural, put } from './dom.js';
import { CHANGE_KINDS, aboutItem, checksRow, dayKey, dayLabel, eventRow, foldRuns, kindOfAction } from './events.js';
import { icon } from './icons.js';
import { actorLabel, codeOf, isNodeKind, itemCode, kindIcon, nodeRef, parseItemRef } from './labels.js';
import { plainText } from './markdown.js';
import { releaseDialog } from './records.js';
import { describeItem, ensureContext, store, sync } from './store.js';
import { findItems } from './switcher.js';
import { whereBlock } from './whereabouts.js';

const PAGE = 100;
const WANT = 50; // rows to show before a filter on the kind or the days stops reading back
const MOST_PAGES = 40; // and the most pages it reads for one look, so it cannot run away
const DEFAULT_LEASE = 900;
const FREE_PAGE = 10; // free work is read this many at a time, and "Show more" reads this many more
const MOST_FREE = 200; // the most one read can give
const FILTERS = ['act_actor', 'act_kind', 'act_item', 'act_from', 'act_to'];
// The three views: what the address calls them, the words on the toggle, the whole phrase for the view (its tooltip),
// and the words a screen reader hears before the count (they hold the visible words, so the button's name has them).
const TABS = [
  ['changes', 'Changes', 'Changes, newest first', 'Changes'],
  ['working', 'Working', 'Who is working on what', 'Who is working on what'],
  ['free', 'Free to take', 'Work nobody holds, ready to be taken', 'Free to take'],
];

let root = null;
let params = new URLSearchParams();
let parts = {};
let events = []; // what has been read, newest first
let older = null; // the event to read back from, or null when nothing older is left
let working = null; // the last answer of who_is_working
let free = []; // ready work nobody holds
let freeTotal = 0; // how much there is in all, of which `free` is the first of
let freeLimit = FREE_PAGE; // how many of it to read
const opened = new Set(); // the changes whose "Show what changed" is open, kept across a redraw
let skew = 0; // the server's clock minus this one's, in milliseconds
let loadedFor = '';
let shownFilters = ''; // the filters the boxes were last filled from
let generation = 0; // the feed that is wanted now
let life = 0; // this showing of the page, so that a late answer for an earlier one is dropped
let ticker = null;
let refetching = false;
let loadingOlder = false; // an older page is being read, so "Show older changes" waits
let drawn = null; // what the claims were last drawn from
let typed = { lease: String(DEFAULT_LEASE), code: '', ref: null, problem: '' }; // what is in its boxes, kept across a redraw (`ref`: the item picked from the list for the box to claim by; `problem`: what the box last refused, in words)

// -- the filters --------------------------------------------------------------------------------------

// An item as typed or in the address: its title, or a code (S-12, AC-3, N-7, which may be followed by the
// title, as the boxes show it), or still a reference (story:12, rule:3, note:7). A piece of work comes back
// as { ref }, an acceptance criterion or a note as { kind, id }, and anything else, or a title that more than
// one item has, as null. A bare number names nothing, since each kind is numbered on its own.
const LETTERS = { e: 'epic', s: 'story', t: 'task' };

const itemFrom = ({ kind, number }) => (isNodeKind(kind) ? { ref: nodeRef(kind, number) } : { kind, id: number });
const refOf = (item) => item.ref ?? `${item.kind}:${item.id}`;

// The items whose title is exactly this (any case).
const itemsTitled = (words) => findItems(words).items.filter((item) => item.title.trim().toLowerCase() === words);

function parseItem(text) {
  const words = String(text || '').trim().toLowerCase();
  const code = /^([est])-(\d+)(?:\s.*)?$/.exec(words);
  if (code) return { ref: nodeRef(LETTERS[code[1]], Number(code[2])) };
  const record = /^(ac|n)-(\d+)(?:\s.*)?$/.exec(words);
  if (record) return { kind: record[1] === 'n' ? 'note' : 'rule', id: Number(record[2]) };
  const reference = /^(epic|story|task|rule|requirement|acceptance criterion|criterion|note)\s*:?\s*#?(\d+)$/.exec(words);
  if (!reference) {
    const titled = words ? itemsTitled(words) : [];
    return titled.length === 1 ? itemFrom(titled[0]) : null;
  }
  const number = Number(reference[2]);
  if (['epic', 'story', 'task'].includes(reference[1])) return { ref: nodeRef(reference[1], number) };
  return { kind: reference[1] === 'note' ? 'note' : 'rule', id: number };
}

// The same item from its reference (story:12, rule:3), as parseItem gives it.
const itemOfRef = (ref) => {
  const found = parseItemRef(ref);
  return found ? itemFrom(found) : null;
};

// What to tell a person whose words name no item, or more than one.
const itemHelp = (text) =>
  itemsTitled(text.trim().toLowerCase()).length > 1
    ? 'More than one item has that title. Choose the one you mean from the list.'
    : 'No item has that title or code. Type a word of its title, or a code such as S-12, AC-3 or N-7, and choose it from the list.';

// An item as a box holds it: its code and then its title (a note's title is its headline).
const codeAndTitle = (kind, number, title) => `${itemCode(kind, number)} ${title}`;

// What a box for an item holds for the item the address (or the person) names: its code and title, or
// the words as they were when it is not one the board knows.
function textOfItem(text) {
  const item = parseItem(text);
  const found = item && describeItem(refOf(item));
  return found ? codeAndTitle(found.kind, found.number, found.kind === 'note' ? plainText(found.title) : found.title) : text;
}

// What is in the About box, as the address keeps it: the reference of the item it names, or the
// words as typed.
function aboutValue() {
  const text = parts.item.value.trim();
  if (!text) return null;
  const item = parts.item.dataset.ref ? itemOfRef(parts.item.dataset.ref) : parseItem(text);
  return item ? refOf(item) : text;
}

// A short list of the items a word of a title (or a code) finds, hanging under a text box. Choosing one puts
// its code and title in the box and says which item it was (`onPick`), so two items of one title can be told
// apart by their codes and by what each is under. `work` leaves out acceptance criteria and notes. The arrow
// keys and Enter work in the box while the list is open; Escape closes it.
function itemList(input, { work = false, onPick }) {
  const list = h('ul', { class: 'plain item-suggest', role: 'listbox', hidden: true, id: `${input.id}-list` });
  let shown = [];
  let active = -1;
  const setActive = (at) => {
    active = at;
    [...list.children].forEach((option, i) => option.setAttribute('aria-selected', String(i === at)));
    if (at >= 0) input.setAttribute('aria-activedescendant', `${input.id}-option-${at}`);
    else input.removeAttribute('aria-activedescendant');
  };
  const close = () => {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    setActive(-1);
  };
  // What tells two items of one name apart: the story or epic a piece of work is under, the item a criterion is on.
  const under = (item) => {
    const record = describeItem(`${item.kind}:${item.number}`);
    return record?.parent ? store.nodes.get(record.parent)?.title || null : null;
  };
  const pick = (item) => {
    input.value = codeAndTitle(item.kind, item.number, item.title);
    close();
    onPick(item);
    input.focus();
  };
  const open = () => {
    const typed = input.value.trim();
    shown = typed ? findItems(typed).items.filter((item) => !work || isNodeKind(item.kind)).slice(0, 8) : [];
    put(
      list,
      shown.map((item, at) => {
        const context = under(item);
        return h(
          'li',
          { class: 'item-suggest-option', role: 'option', id: `${input.id}-option-${at}`, 'aria-selected': 'false', dataset: { ref: `${item.kind}:${item.number}` }, onmousedown: (event) => event.preventDefault(), onclick: () => pick(item) },
          kindIcon(item.kind, 14),
          h('span', { class: 'item-suggest-code' }, itemCode(item.kind, item.number)),
          h('span', { class: 'item-suggest-title', title: item.title }, item.title),
          context ? h('span', { class: 'item-suggest-context', title: context }, context) : null,
        );
      }),
    );
    list.hidden = !shown.length;
    input.setAttribute('aria-expanded', String(shown.length > 0));
    setActive(-1);
  };
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-controls', list.id);
  input.setAttribute('aria-expanded', 'false');
  input.addEventListener('input', open);
  input.addEventListener('blur', close);
  input.addEventListener('keydown', (event) => {
    if (list.hidden) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((active + (event.key === 'ArrowDown' ? 1 : -1) + shown.length) % shown.length);
    } else if (event.key === 'Enter' && active >= 0) {
      event.preventDefault();
      pick(shown[active]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  });
  return list;
}

function filters() {
  const text = (params.get('act_item') || '').trim();
  return {
    actor: (params.get('act_actor') || '').trim(),
    kind: params.get('act_kind') || '',
    text,
    item: parseItem(text),
    from: params.get('act_from') || '',
    to: params.get('act_to') || '',
  };
}

const filterKey = () => FILTERS.map((name) => params.get(name) || '').join('|');
const serverKey = (f) => `${f.actor}|${f.text}`;

// What the server is asked: the actor and the one item to follow.
function serverArguments(f) {
  const item = f.item ? (f.item.ref ? { node: f.item.ref } : { [`${f.item.kind}_id`]: f.item.id }) : {};
  return { ...(f.actor ? { actor: f.actor } : {}), ...item };
}

function keeps(event, f) {
  if (f.actor && event.actor !== f.actor) return false;
  if (!aboutItem(event, f.item)) return false;
  if (f.kind && kindOfAction(event.action) !== f.kind) return false;
  const day = String(event.at).slice(0, 10);
  if (f.from && day < f.from) return false;
  if (f.to && day > f.to) return false;
  return true;
}

// -- the page -------------------------------------------------------------------------------------

export async function mount(element, initial) {
  root = element;
  life += 1;
  params = initial;
  events = [];
  older = null;
  loadingOlder = false;
  working = null;
  drawn = null;
  typed = { lease: String(DEFAULT_LEASE), code: '', ref: null, problem: '' };
  free = [];
  freeTotal = 0;
  freeLimit = FREE_PAGE;
  opened.clear();
  loadedFor = '';
  const control = (name, tag, props = {}, ...children) => h(tag, { id: `a-${name}`, name, ...props }, ...children);
  parts = {
    actor: control('actor', 'input', { type: 'text', list: 'a-who', autocomplete: 'off', placeholder: 'a name' }),
    kind: control('kind', 'select', {}, [['', 'Everything'], ...CHANGE_KINDS.map(([value, label]) => [value, label])].map(([value, label]) => h('option', { value }, label))),
    item: control('item', 'input', { type: 'text', autocomplete: 'off', placeholder: 'A word of a title, or a code such as S-12 or AC-3' }),
    from: control('from', 'input'),
    to: control('to', 'input'),
    who: h('datalist', { id: 'a-who' }),
    problem: h('p', { class: 'problem', role: 'alert', id: 'activity-problem' }),
    itemProblem: h('p', { class: 'problem', role: 'alert', id: 'activity-item-problem' }),
    summary: h('p', { class: 'count', id: 'activity-count', role: 'status' }),
    feed: h('div', { id: 'feed' }),
    agents: h('div', { id: 'agents' }, h('p', { class: 'loading', role: 'status' }, 'Reading the claims…')),
    freeBody: h('div', { id: 'free-body' }, h('p', { class: 'loading', role: 'status' }, 'Reading the claims…')),
    filterCount: h('span', { class: 'filter-count', hidden: true }),
  };
  // Typing after choosing from the list names whatever the words name, not the item that was chosen.
  parts.item.addEventListener('input', () => delete parts.item.dataset.ref);
  parts.itemList = itemList(parts.item, { onPick: (item) => { parts.item.dataset.ref = `${item.kind}:${item.number}`; } });
  // The toggle under the title: one choice out of three, real buttons the arrow keys move between.
  const tabButtons = TABS.map(([id, label, whole]) =>
    h('button', { type: 'button', id: `activity-tab-${id}`, title: whole, 'aria-pressed': 'false', dataset: { tab: id }, onclick: () => ui.navigate({ tab: id === 'changes' ? null : id }) }, h('span', { class: 'tab-name' }, label), h('span', { class: 'tab-count', hidden: true })),
  );
  parts.tabButtons = Object.fromEntries(tabButtons.map((button) => [button.dataset.tab, button]));
  parts.tabs = h(
    'div',
    {
      class: 'segmented activity-tabs',
      id: 'activity-tabs',
      role: 'group',
      'aria-label': 'What to look at',
      onkeydown: (event) => {
        const at = tabButtons.indexOf(document.activeElement);
        const to = at < 0 ? null : { ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: tabButtons.length - 1 }[event.key];
        if (to === null || to === undefined) return;
        event.preventDefault();
        tabButtons[(to + tabButtons.length) % tabButtons.length].focus();
      },
    },
    tabButtons,
  );
  parts.filterToggle = h(
    'button',
    { type: 'button', id: 'activity-filter-toggle', class: 'filter-toggle', 'aria-expanded': 'false', 'aria-controls': 'activity-filter', onclick: () => showFilters(parts.form.hidden) },
    icon('filter', { size: 16 }),
    'Filter',
    parts.filterCount,
  );
  const label = (name, text) => h('label', { for: `a-${name}` }, text);
  const labelled = (name, text, control, ...more) => h('div', { class: `field act-${name}` }, label(name, text), control, ...more);
  const form = h(
    'form',
    {
      class: 'filter-form activity-filter',
      id: 'activity-filter',
      role: 'search',
      hidden: true,
      onsubmit: (event) => {
        event.preventDefault();
        ui.navigate({
          act_actor: parts.actor.value.trim() || null,
          act_kind: parts.kind.value || null,
          act_item: aboutValue(),
          act_from: parts.from.value || null,
          act_to: parts.to.value || null,
        });
      },
    },
    h(
      'div',
      { class: 'act-fields' },
      labelled('actor', 'Who', parts.actor, parts.who),
      labelled('kind', 'Kind of change', parts.kind),
      labelled('item', 'About', h('div', { class: 'suggest-host' }, parts.item, parts.itemList)),
      labelled('from', 'From day (UTC)', dateField(parts.from, 'From day')),
      labelled('to', 'To day (UTC)', dateField(parts.to, 'To day')),
      h(
        'div',
        { class: 'buttons act-buttons' },
        h('button', { type: 'submit', id: 'apply-activity' }, 'Apply'),
        h('button', { type: 'button', id: 'clear-activity', onclick: () => ui.navigate(Object.fromEntries(FILTERS.map((name) => [name, null]))) }, icon('clear', { size: 16 }), 'Clear'),
      ),
    ),
  );
  parts.form = form;
  // The three views are built once; only the chosen one is on the page (see showTab).
  parts.views = {
    changes: h(
      'section',
      { id: 'feed-section', class: 'activity-feed', 'aria-labelledby': 'activity-h-changes' },
      h('h2', { class: 'sr-only', id: 'activity-h-changes' }, 'Changes, newest first'),
      h('div', { class: 'activity-feed-head' }, parts.summary, parts.filterToggle),
      form,
      parts.problem,
      parts.itemProblem,
      parts.feed,
    ),
    working: h('section', { id: 'working', class: 'activity-working', 'aria-labelledby': 'activity-h-working' }, h('h2', { class: 'sr-only', id: 'activity-h-working' }, 'Who is working on what'), parts.agents),
    free: h('section', { id: 'free', class: 'activity-free', 'aria-labelledby': 'activity-h-free' }, h('h2', { class: 'sr-only', id: 'activity-h-free' }, 'Free to take'), parts.freeBody),
  };
  parts.view = h('div', { class: 'activity-view', id: 'activity-view' });
  put(root, h('div', { class: 'activity-page' }, h('h1', { class: 'sr-only' }, 'Activity'), h('div', { class: 'activity-switch' }, parts.tabs), parts.view));
  showTab();
  fillForm();
  showFilters(activeFilters() > 0);
  try {
    await ensureContext();
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
  }
  if (!root) return;
  // The acceptance criteria and notes can be named now: an About box that still holds what the address says shows its code and title.
  if (parts.item.value === (params.get('act_item') || '').trim()) showAbout();
  loadFeed(true);
  loadWorking();
}

export function unmount() {
  generation += 1;
  life += 1;
  root = null;
  setTicker(false);
}

// Only the chosen view is on the page, the toggle shows which, and the countdowns tick only while
// Working is the one shown.
const tabOf = () => (TABS.some(([id]) => id === params.get('tab')) ? params.get('tab') : 'changes');

function showTab() {
  const tab = tabOf();
  put(parts.view, parts.views[tab]);
  for (const [id, button] of Object.entries(parts.tabButtons)) button.setAttribute('aria-pressed', String(id === tab));
  setTicker(tab === 'working');
}

// A quiet count on a toggle, only when it is known and above zero. Working counts the people and agents who
// hold something now, Free to take the work nobody holds.
function syncCounts() {
  const holding = working ? working.agents.filter((agent) => agent.holding.length > 0).length : 0;
  const counts = { working: holding, free: freeTotal };
  for (const [id, number] of Object.entries(counts)) {
    const button = parts.tabButtons[id];
    const count = button.querySelector('.tab-count');
    count.textContent = String(number);
    count.hidden = !(number > 0);
    const named = TABS.find(([key]) => key === id)[3];
    if (number > 0) button.setAttribute('aria-label', `${named}: ${number}`);
    else button.removeAttribute('aria-label');
  }
}

function setTicker(on) {
  if (on && ticker === null) {
    tick();
    ticker = setInterval(tick, 1000);
  } else if (!on && ticker !== null) {
    clearInterval(ticker);
    ticker = null;
  }
}

export function update(next) {
  params = next;
  if (!root) return;
  showTab();
  // The boxes are filled again only when the filters in the address changed, so that what is
  // typed in them and not applied yet stays while the rest of the address changes. A filter that
  // arrives in the address opens the boxes.
  if (filterKey() !== shownFilters) {
    fillForm();
    if (activeFilters() > 0) showFilters(true);
  }
  const f = filters();
  if (serverKey(f) !== loadedFor) loadFeed(true);
  else drawFeed();
}

// News from the server: new changes join the top of the feed at once, and who is working on what
// is read again.
export function refresh(news) {
  if (!root || loadedFor === '') return;
  const f = filters();
  const have = new Set(events.map((event) => event.id));
  for (const event of news) {
    if (!have.has(event.id) && keeps(event, { ...f, kind: '', from: '', to: '' })) events.unshift(event);
  }
  drawFeed();
  loadWorking();
}

function fillForm() {
  shownFilters = filterKey();
  parts.actor.value = params.get('act_actor') || '';
  parts.kind.value = params.get('act_kind') || '';
  showAbout();
  parts.from.value = params.get('act_from') || '';
  parts.to.value = params.get('act_to') || '';
  syncFilterButton();
}

// The About box holds the code and title of the item the address names (what the address says, when it names none).
function showAbout() {
  const text = params.get('act_item') || '';
  const item = parseItem(text);
  parts.item.value = textOfItem(text);
  if (item) parts.item.dataset.ref = refOf(item);
  else delete parts.item.dataset.ref;
}

// How many of the five filters are set in the address.
const activeFilters = () => FILTERS.filter((name) => params.get(name)).length;

// The Filter button says how many are set (nothing when none is), and the boxes are open whenever one is.
function syncFilterButton() {
  const set = activeFilters();
  parts.filterCount.textContent = String(set);
  parts.filterCount.hidden = set === 0;
  parts.filterToggle.classList.toggle('has-filters', set > 0);
}

function showFilters(open) {
  parts.form.hidden = !open;
  parts.filterToggle.setAttribute('aria-expanded', String(open));
}

// -- the feed -----------------------------------------------------------------------------------------

async function readBack(before = null) {
  const f = filters();
  return call('changes_since', { newest: true, limit: PAGE, ...serverArguments(f), ...(before ? { before_event_id: before } : {}) });
}

// Read pages back until there is enough to show, or nothing older is left, or the days asked for
// have been passed.
async function fill(want) {
  const mine = generation;
  const f = filters();
  let pages = 0;
  while (older !== null && pages < MOST_PAGES) {
    const shown = events.filter((event) => keeps(event, f)).length;
    const oldest = events.length ? String(events[events.length - 1].at).slice(0, 10) : '';
    if (shown >= want || (f.from && oldest && oldest < f.from)) return;
    const page = await readBack(older);
    if (mine !== generation || !root) return;
    // A page that was read twice must never be drawn twice: an event is kept once, by its id.
    const have = new Set(events.map((event) => event.id));
    events.push(...[...page.items].reverse().filter((event) => !have.has(event.id)));
    older = page.next_before_id;
    pages += 1;
  }
}

async function loadFeed(fresh) {
  const mine = ++generation;
  const f = filters();
  loadedFor = serverKey(f);
  if (fresh) {
    parts.summary.textContent = 'Reading the changes…';
    if (!events.length) put(parts.feed, h('p', { class: 'loading', role: 'status' }, 'Reading the changes…'));
  }
  parts.problem.textContent = '';
  try {
    const page = await readBack();
    if (mine !== generation || !root) return;
    events = [...page.items].reverse();
    older = page.next_before_id;
    await fill(WANT);
  } catch (error) {
    if (mine !== generation || !root) return;
    if (!(error instanceof Refusal)) throw error;
    events = [];
    older = null;
    parts.problem.textContent = error.message;
    clear(parts.feed);
    parts.summary.textContent = '';
    return;
  }
  if (mine !== generation || !root) return;
  drawFeed();
}

// "Show older changes": one read at a time. A second press while the first is still being read does
// nothing (the button is disabled meanwhile), so a page is never added twice.
async function showOlder() {
  if (loadingOlder) return;
  loadingOlder = true;
  const mine = generation;
  const before = events.length;
  const f = filters();
  const shown = events.filter((event) => keeps(event, f)).length;
  parts.feed.querySelector('#show-older')?.setAttribute('disabled', '');
  try {
    await fill(shown + 25);
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    parts.problem.textContent = error.message;
  } finally {
    loadingOlder = false;
  }
  // Other filters were asked for in the meantime: this feed is not the one that was wanted.
  if (!root || mine !== generation) return;
  if (events.length === before) older = null;
  drawFeed();
}

function updateWho() {
  const known = new Set([...(working ? working.agents.map((agent) => agent.actor) : []), ...events.map((event) => event.actor)]);
  put(parts.who, [...known].sort().map((name) => h('option', { value: name })));
}

function drawFeed() {
  const f = filters();
  // Each event once, by its id, whatever was read.
  const seen = new Set();
  const rows = events.filter((event) => keeps(event, f) && !seen.has(event.id) && seen.add(event.id));
  parts.itemProblem.textContent = f.text && !f.item ? itemHelp(f.text) : '';
  updateWho();
  parts.summary.textContent = `${plural(rows.length, 'change')}${older !== null ? '. Older changes have not been read yet' : ''}`;
  // The day is said once above the rows of it, so that each row needs only the time. A run of ticks is one row.
  const lines = [];
  let day = null;
  for (const item of foldRuns(rows)) {
    const first = item.run ? item.run[0] : item.event;
    if (dayKey(first.at) !== day) {
      day = dayKey(first.at);
      lines.push(h('li', { class: 'feed-day' }, h('h3', {}, dayLabel(first.at))));
    }
    lines.push(item.run ? checksRow(item.run, { opened }) : eventRow(item.event, { opened }));
  }
  put(
    parts.feed,
    rows.length ? h('ul', { class: 'plain feed' }, lines) : emptyFeed(),
    older !== null ? h('p', { class: 'more' }, h('button', { type: 'button', id: 'show-older', disabled: loadingOlder, onclick: showOlder }, 'Show older changes')) : null,
  );
}

// Nothing to list: the page says why, and what to do about it, in a block rather than a bare line.
function emptyFeed() {
  const filtered = FILTERS.some((name) => params.get(name));
  const unread = events.length > 0 || older !== null;
  const words = filtered
    ? unread
      ? 'None of the changes read so far match these filters.'
      : 'No changes match these filters.'
    : 'Nothing has happened in this project yet.';
  return h(
    'div',
    { class: 'empty feed-empty' },
    icon(filtered ? 'filter' : 'activity', { size: 20 }),
    h('p', {}, words),
    filtered ? h('button', { type: 'button', class: 'small', onclick: () => ui.navigate(Object.fromEntries(FILTERS.map((name) => [name, null]))) }, icon('clear', { size: 14 }), 'Clear the filters') : null,
  );
}

// -- who is working on what ---------------------------------------------------------------------------

async function loadWorking() {
  const mine = life;
  try {
    const [answer, ready] = await Promise.all([call('who_is_working', {}), call('ready_work', { limit: freeLimit })]);
    if (!root || mine !== life) return;
    working = answer;
    free = ready.items;
    freeTotal = ready.total ?? ready.items.length;
    skew = Date.parse(answer.as_of) - Date.now();
    // Drawn again only when something in it changed, so that what a person is typing in it stays.
    const seen = JSON.stringify([answer.agents, ready.items.map((node) => node.ref), freeTotal, account.name]);
    if (seen !== drawn) {
      drawn = seen;
      drawWorking();
    }
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    if (root) {
      put(parts.agents, h('p', { class: 'problem', role: 'alert' }, error.message));
      put(parts.freeBody, h('p', { class: 'problem', role: 'alert' }, error.message));
    }
  }
}

// How long is left, or how long ago it ran out, in words.
function span(ms) {
  const seconds = Math.round(Math.abs(ms) / 1000);
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)} h ${Math.floor((seconds % 3600) / 60)} min`;
  if (seconds >= 60) return `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
  return `${seconds} s`;
}

function countdown(expires, live) {
  return h('span', { class: `countdown${live ? '' : ' lapsed'}`, dataset: { expires, live: live ? '1' : '0' } }, remaining(expires, live));
}

const remaining = (expires, live) => {
  const ms = Date.parse(expires) - (Date.now() + skew);
  return live ? (ms > 0 ? `${span(ms)} left` : 'ran out just now') : `ran out ${span(ms)} ago`;
};

// Once a second: the countdowns count, and a claim that has run out is read again, because
// running out writes no change.
function tick() {
  let due = false;
  for (const element of root?.querySelectorAll('.countdown') || []) {
    const live = element.dataset.live === '1';
    element.textContent = remaining(element.dataset.expires, live);
    if (live && Date.parse(element.dataset.expires) <= Date.now() + skew) due = true;
  }
  if (due && !refetching) {
    refetching = true;
    loadWorking().finally(() => {
      refetching = false;
    });
  }
}

const STATE = { working: 'Working', lapsed: 'Lapsed', finished: 'Left a handoff note' };

// A piece of work in a sentence: its title in curly quotes, or "the item" when the board does not know it.
const quoted = (ref) => {
  const found = store.nodes.get(ref);
  return found ? `“${found.title}”` : 'the item';
};

// A piece of work as a link to its page: "S-12 “Log in”". The code says which kind it is.
function workLink(ref, title) {
  return itemLink(ref, { class: 'item-link', dataset: { ref } }, `${codeOf(ref)} “${title}”`);
}

const nodeLink = (work) => workLink(work.node, work.title);

async function attempt(job, done) {
  try {
    await job();
    notify(done);
    await sync();
    await loadWorking();
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
  }
}

function claimRow(agent, work, live) {
  // Only the one who holds a claim can renew or release it, so the buttons are there only for the
  // person's own.
  const mine = isMe(agent.actor, agent.kind);
  return h(
    'li',
    { class: `claim${live ? '' : ' lapsed'}`, dataset: { node: work.node, actor: agent.actor, actorKind: agent.kind || '', live: live ? '1' : '0' } },
    h('div', { class: 'claim-main' }, nodeLink(work)),
    h('div', { class: 'claim-meta' }, h('span', { class: 'claim-time' }, icon(live ? 'clock' : 'alert', { size: 13 }), countdown(work.expires_at, live))),
    mine && can('write')
      ? h(
          'span',
          { class: 'buttons' },
          live ? h('button', { type: 'button', class: 'renew small', onclick: () => attempt(() => call('renew_claim', { node: work.node }), `Kept ${quoted(work.node)} for longer.`) }, 'Renew') : null,
          h('button', { type: 'button', class: 'release small', onclick: () => releaseDialog(work, loadWorking) }, 'Release…'),
        )
      : null,
    whereBlock(work),
  );
}

function drawWorking() {
  if (!root || !working) return;
  const writes = can('write');
  const lease = h('input', { type: 'number', id: 'lease', min: 1, max: 86400, step: 1, value: typed.lease, oninput: () => { typed.lease = lease.value; } });
  const claimProblem = h('p', { class: 'problem', id: 'claim-problem', role: 'alert' }, typed.problem);
  const claimId = h('input', { type: 'text', id: 'claim-id', autocomplete: 'off', placeholder: 'A word of a title, or a code such as T-4', value: typed.code, oninput: () => { typed.code = claimId.value; typed.ref = null; typed.problem = ''; claimProblem.textContent = ''; } });
  const claimList = itemList(claimId, { work: true, onPick: (item) => { typed.code = claimId.value; typed.ref = `${item.kind}:${item.number}`; } });
  put(parts.agents, working.agents.length ? h('ul', { class: 'plain agents' }, working.agents.map(agentRow)) : emptyBlock('in_progress', 'Nobody has taken work yet.'));
  syncCounts();
  put(
    parts.freeBody,
    h(
      'div',
      { class: 'free-wrap' },
      writes ? h('p', { class: 'quiet', id: 'claiming-as' }, 'Claiming as ', actorLabel(account.name, 'person')) : null,
      writes
        ? h(
            'div',
            { class: 'free-controls' },
            h('div', { class: 'field lease-field' }, h('label', { for: 'lease' }, 'Claim lasts (seconds)'), lease),
            h(
              'form',
              {
                id: 'claim-by-id',
                class: 'claim-by-id',
                onsubmit: (event) => {
                  event.preventDefault();
                  const text = claimId.value.trim();
                  // The item picked from the list, or what the words name (a title, or still a reference or a code).
                  const item = typed.ref ? itemOfRef(typed.ref) : parseItem(text);
                  if (!item?.ref) {
                    // Said here, by the box, and what was typed stays.
                    typed.problem = item ? 'Only an epic, a story or a task can be claimed. Choose one from the list.' : text ? itemHelp(text) : 'Choose what to claim: type a word of its title, or a code, and choose it from the list.';
                    claimProblem.textContent = typed.problem;
                    return;
                  }
                  typed.problem = '';
                  claimProblem.textContent = '';
                  attempt(() => call('claim_node', { node: item.ref, lease_seconds: Number(lease.value) }), `Took ${quoted(item.ref)}.`);
                },
              },
              h('div', { class: 'field' }, h('label', { for: 'claim-id' }, 'Claim by code or title'), h('div', { class: 'claim-by-id-row' }, h('div', { class: 'suggest-host' }, claimId, claimList), h('button', { type: 'submit', id: 'claim-by-id-go' }, 'Claim')), claimProblem),
            ),
          )
        : null,
      free.length
        ? h(
            'ul',
            { class: 'plain claims free' },
            free.map((node) =>
              h(
                'li',
                { class: 'claim', dataset: { node: node.ref } },
                h('div', { class: 'claim-main' }, workLink(node.ref, node.title)),
                writes ? h('button', { type: 'button', class: 'take small', onclick: () => attempt(() => call('claim_node', { node: node.ref, lease_seconds: Number(lease.value) }), `Took ${quoted(node.ref)}.`) }, 'Claim') : null,
              ),
            ),
          )
        : emptyBlock('check', 'Nothing is free to take.'),
      free.length < freeTotal && freeLimit < MOST_FREE
        ? h(
            'p',
            { class: 'more' },
            h(
              'button',
              {
                type: 'button',
                id: 'show-more-free',
                class: 'small',
                onclick: () => {
                  freeLimit = Math.min(freeLimit + FREE_PAGE, MOST_FREE);
                  loadWorking();
                },
              },
              `Show ${Math.min(FREE_PAGE, freeTotal - free.length)} more`,
            ),
          )
        : null,
    ),
  );
  updateWho();
}

const STATE_ICONS = { working: 'in_progress', lapsed: 'alert', finished: 'done' };

// A view with nothing in it says so in a block, as the feed does, rather than in a bare line.
const emptyBlock = (name, words) => h('div', { class: 'empty feed-empty' }, icon(name, { size: 20 }), h('p', {}, words));

// One person or agent: who they are and what state they are in, the claims they hold (in force first, then the
// ones that ran out on them) and the last handoff note they left.
function agentRow(agent) {
  const note = agent.last_handoff;
  const claims = [...agent.holding.map((work) => claimRow(agent, work, true)), ...agent.lapsed.map((work) => claimRow(agent, work, false))];
  return h(
    'li',
    { class: `agent state-${agent.state}`, dataset: { actor: agent.actor, actorKind: agent.kind || '', state: agent.state } },
    h('div', { class: 'agent-line' }, actorLabel(agent.actor, agent.kind), h('span', { class: `chip state state-${agent.state}` }, icon(STATE_ICONS[agent.state] || 'info', { size: 13 }), STATE[agent.state])),
    claims.length ? h('ul', { class: 'plain claims' }, claims) : null,
    note
      ? h(
          'div',
          { class: 'agent-handoff-block' },
          h('p', { class: 'quiet agent-handoff' }, 'Last handoff: ', note.outcome, note.node ? [' on ', store.nodes.has(note.node) ? workLink(note.node, store.nodes.get(note.node).title) : itemLink(note.node, { class: 'item-link', dataset: { ref: note.node } }, codeOf(note.node))] : null, `, ${moment(note.at)}.`),
          whereBlock(note),
        )
      : null,
  );
}
