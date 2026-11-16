// Search, in the drawer: the box in the top bar opens it, and a result opens the ordinary detail
// panel (`sel=kind:id`) without closing it. Each result says how it matched (exact words, meaning
// or both) and why, shows a snippet with the matched words marked, and its parent and status as
// the graph holds them now. The counts by kind, status and epic are worked out from the whole list
// of results, and filtering by them keeps the server's order. The drawer says how many items wait
// to be indexed, since results by meaning can lag where results by words never do, and says in
// the server's words why meaning results are missing when the embedding service is not there.
// Possible duplicates is a second mode of the same drawer (duplicates.js). It keeps nothing: the
// search, the mode and the filters are in the address (search, search_mode, search_type,
// search_status, search_epic, and dups with its own choices).

import { Refusal, callPaged } from './api.js';
import { recordLink, ui } from './ctx.js';
import { clear, h, plural, put } from './dom.js';
import * as duplicates from './duplicates.js';
import { icon } from './icons.js';
import { KIND, STATUS, kindIcon, statusChip, statusIcon } from './labels.js';
import { epicOf, store } from './store.js';

const FIRST = 50;
const MODES = [
  ['both', 'Words and meaning'],
  ['words', 'Exact words only'],
  ['meaning', 'Meaning only'],
];
const MATCHED = { words: 'Exact words', meaning: 'Meaning (a suggestion)', both: 'Words and meaning' };
const TYPES = ['epic', 'story', 'task', 'rule', 'decision', 'fact'];

let root = null;
let params = new URLSearchParams();
let parts = {};
let inDuplicates = null; // which view is on show: true for possible duplicates, false for results
let results = null; // { items, first } for the query in the address
let shown = FIRST;
let generation = 0;
let loadedFor = '';

const modeOf = (from) => (MODES.some(([value]) => value === from.get('search_mode')) ? from.get('search_mode') : 'both');
const askedOf = (from) => `${from.get('search') || ''}|${from.get('search_mode') || 'both'}`;

// -- the drawer -----------------------------------------------------------------------------------

export function mount(element, initial) {
  root = element;
  params = initial;
  inDuplicates = null;
  results = null;
  parts = {
    tabs: h(
      'div',
      { class: 'drawer-tabs segmented', role: 'group', 'aria-label': 'What to do' },
      h('button', { type: 'button', id: 'search-tab-results', onclick: () => ui.navigate({ dups: null }) }, icon('search', { size: 16 }), 'Search'),
      h('button', { type: 'button', id: 'search-tab-dups', onclick: () => ui.navigate({ dups: '1' }) }, icon('link', { size: 16 }), 'Possible duplicates'),
    ),
    view: h('div', { id: 'search-view' }),
  };
  put(root, parts.tabs, parts.view);
  showView();
}

export function unmount() {
  generation += 1;
  duplicates.unmount();
  root = null;
  results = null;
  parts = {};
}

export function update(next) {
  params = next;
  if (!root) return;
  if ((next.get('dups') === '1') !== inDuplicates) {
    showView();
    return;
  }
  if (inDuplicates) {
    duplicates.update(next);
    return;
  }
  // The same words and mode only redraw: what is typed in the box and not asked yet stays.
  if (askedOf(next) !== loadedFor) {
    shown = FIRST;
    load();
  } else draw();
}

export function refresh(events) {
  if (!root) return;
  if (inDuplicates) duplicates.refresh(events);
  else if ((params.get('search') || '').trim()) load(true);
}

// The count of items waiting to be indexed follows the server as it is, not as the search saw it.
export function index() {
  if (root && !inDuplicates) drawWaiting();
}

// The same words asked again (the service may be back, or something was saved).
export function again() {
  if (!root) return;
  if (inDuplicates) duplicates.again();
  else load();
}

export function focus() {
  const target = inDuplicates || (params.get('search') || '').trim() ? document.getElementById('drawer-title') : parts.box;
  if (target) target.focus();
}

// Which of the two views the address asks for: the one that is on show is torn down first.
function showView() {
  const wanted = params.get('dups') === '1';
  if (inDuplicates === true) duplicates.unmount();
  generation += 1;
  inDuplicates = wanted;
  for (const [id, on] of [['search-tab-results', !wanted], ['search-tab-dups', wanted]]) {
    const button = parts.tabs.querySelector(`#${id}`);
    button.setAttribute('aria-pressed', String(on));
    button.classList.toggle('on', on);
  }
  root.parentElement.dataset.mode = wanted ? 'duplicates' : 'results';
  if (wanted) {
    clear(parts.view);
    duplicates.mount(parts.view, params);
    return;
  }
  buildResults();
  drawWaiting();
  load();
}

// -- the results view -----------------------------------------------------------------------------

function buildResults() {
  shown = FIRST;
  loadedFor = '';
  const box = h('input', { id: 'search-box', name: 'search', type: 'search', autocomplete: 'off', value: params.get('search') || '', 'aria-label': 'What to look for', placeholder: 'Words, a phrase in quotes, or what you mean' });
  const mode = h('select', { id: 'search-mode', 'aria-label': 'How to search' }, MODES.map(([value, label]) => h('option', { value }, label)));
  mode.value = modeOf(params);
  Object.assign(parts, {
    box,
    mode,
    waiting: h('p', { class: 'index-line', id: 'search-waiting', role: 'status' }),
    note: h('p', { class: 'index-note', id: 'search-note' }),
    problem: h('p', { class: 'problem', role: 'alert', id: 'search-problem' }),
    summary: h('p', { class: 'count', id: 'search-count', role: 'status' }),
    facets: h('div', { class: 'facets', id: 'search-facets' }),
    list: h('div', { id: 'search-results' }),
  });
  put(
    parts.view,
    h(
      'form',
      {
        class: 'search-form drawer-form',
        role: 'search',
        onsubmit: (event) => {
          event.preventDefault();
          const asked = `${box.value.trim()}|${mode.value}`;
          ui.navigate({ search: box.value.trim() || null, search_mode: mode.value === 'both' ? null : mode.value, search_type: null, search_status: null, search_epic: null });
          // The same words again ask again.
          if (asked === `${(params.get('search') || '').trim()}|${modeOf(params)}`) load();
        },
      },
      h('div', { class: 'search-field' }, icon('search', { size: 16 }), box),
      h('div', { class: 'search-row' }, mode, h('button', { type: 'submit', class: 'primary', id: 'search-go' }, 'Search')),
    ),
    parts.waiting,
    parts.note,
    parts.problem,
    parts.summary,
    parts.facets,
    parts.list,
  );
}

function drawWaiting() {
  const waiting = store.index ? store.index.waiting_to_index : null;
  put(
    parts.waiting,
    icon(waiting ? 'clock' : 'info', { size: 14 }),
    h(
      'span',
      {},
      waiting === null
        ? 'Checking the index…'
        : `${plural(waiting, 'item')} waiting to be indexed. ${waiting ? 'Matches by meaning may lag behind them; matches by exact words never do.' : 'Matches by meaning are up to date.'}`,
    ),
  );
  parts.waiting.dataset.waiting = String(waiting ?? '');
}

// Said under the box when the index has something to say: the server's own words. It is a warning
// when meaning could not be used, and only information when it was used or not asked for.
function drawNote(text, meaning = 'used') {
  const fine = meaning === 'used' || meaning === 'not_requested';
  parts.note.className = `index-note ${fine ? 'info' : 'warn'}`;
  put(parts.note, text ? [icon(fine ? 'info' : 'alert', { size: 14 }), h('span', {}, text)] : null);
}

// A quiet block in place of results: nothing asked yet, nothing found, or reading.
const hush = (glyph, ...words) => h('div', { class: 'empty drawer-empty' }, icon(glyph, { size: 20 }), h('p', {}, ...words));

// -- asking the server ----------------------------------------------------------------------------

async function load(quiet = false) {
  const mine = ++generation;
  const query = (params.get('search') || '').trim();
  const mode = modeOf(params);
  parts.box.value = params.get('search') || '';
  parts.mode.value = mode;
  loadedFor = askedOf(params);
  if (!query) {
    results = null;
    parts.problem.textContent = '';
    drawNote('');
    parts.summary.textContent = '';
    clear(parts.facets);
    put(parts.list, hush('search', 'Type something to look for. Exact words are found the moment something is saved.'));
    return;
  }
  if (!quiet) {
    parts.summary.textContent = 'Searching…';
    if (!results) put(parts.list, h('p', { class: 'loading', role: 'status' }, 'Searching…'));
  }
  let found;
  try {
    found = await callPaged('search', { query, mode });
  } catch (error) {
    if (mine !== generation || !root) return;
    if (!(error instanceof Refusal)) throw error;
    results = null;
    parts.problem.textContent = error.message;
    parts.summary.textContent = '';
    clear(parts.facets);
    clear(parts.list);
    return;
  }
  if (mine !== generation || !root) return;
  results = found;
  parts.problem.textContent = '';
  drawNote(found.first.index.note, found.first.index.meaning);
  draw();
}

// -- what the filters keep ----------------------------------------------------------------------------

// The epic an item of the graph is in: an epic, story or task by the graph, a requirement by its story.
function epicOfResult(item) {
  if (['epic', 'story', 'task'].includes(item.item_type)) return epicOf(store.nodes.get(item.id))?.id ?? null;
  if (item.item_type === 'rule' && item.parent_id !== null) return epicOf(store.nodes.get(item.parent_id))?.id ?? null;
  return null;
}

function count(items, key) {
  const found = new Map();
  for (const item of items) {
    const value = key(item);
    if (value !== null && value !== undefined) found.set(value, (found.get(value) || 0) + 1);
  }
  return found;
}

const capital = (word) => word[0].toUpperCase() + word.slice(1);

function draw() {
  if (!root || inDuplicates || !results) return;
  const all = results.items;
  const type = params.get('search_type') || '';
  const status = params.get('search_status') || '';
  const epic = Number(params.get('search_epic')) || null;
  const epicOfItem = new Map(all.map((item) => [item, epicOfResult(item)]));
  const fits = (item, skip = '') =>
    (skip === 'type' || !type || item.item_type === type) &&
    (skip === 'status' || !status || item.status === status) &&
    (skip === 'epic' || !epic || epicOfItem.get(item) === epic);
  const kept = all.filter((item) => fits(item));

  parts.summary.textContent = `${plural(kept.length, 'result')}${kept.length === all.length ? '' : ` of ${all.length}`}.`;
  put(
    parts.facets,
    facet('type', 'Kind', count(all.filter((item) => fits(item, 'type')), (item) => item.item_type), (value) => KIND[value]?.label || value, TYPES, type),
    facet('status', 'Status', count(all.filter((item) => fits(item, 'status')), (item) => item.status), (value) => STATUS[value]?.label || capital(value), null, status),
    facet('epic', 'Epic', count(all.filter((item) => fits(item, 'epic')), (item) => epicOfItem.get(item)), (value) => `#${value} ${store.nodes.get(value)?.title || ''}`.trim(), null, epic ? String(epic) : '', epic ? 'Decisions and facts belong to no epic, so they are left out while an epic is chosen.' : null),
  );
  const visible = kept.slice(0, shown);
  put(
    parts.list,
    kept.length
      ? h('ul', { class: 'plain results' }, visible.map(resultRow))
      : all.length
        ? h('div', { class: 'empty drawer-empty' }, icon('filter', { size: 20 }), h('p', {}, 'Nothing matches these filters.'), h('button', { type: 'button', class: 'small', onclick: () => ui.navigate({ search_type: null, search_status: null, search_epic: null }) }, `Show all ${plural(all.length, 'result')}`))
        : hush('search', 'Nothing matches. Try other words, or another way to search.'),
    kept.length > shown ? h('p', { class: 'more' }, h('button', { type: 'button', id: 'show-more', onclick: () => { shown += FIRST; draw(); } }, `Show ${Math.min(FIRST, kept.length - shown)} more`)) : null,
  );
}

// A group of filter buttons with their counts. Choosing one again takes it off. The address holds
// the choice as search_type, search_status or search_epic.
function facet(name, label, counts, words, order, current, note = null) {
  const key = `search_${name}`;
  const keys = order ? order.filter((one) => counts.has(one)) : [...counts.keys()].sort((a, b) => (typeof a === 'number' ? a - b : String(a).localeCompare(String(b))));
  const glyph = (one) => (name === 'type' ? kindIcon(one, 14) : name === 'status' && STATUS[one] ? statusIcon(one, 14) : name === 'epic' ? icon('epic', { size: 14 }) : null);
  return h(
    'fieldset',
    { class: 'facet', dataset: { facet: name } },
    h('legend', {}, label),
    keys.length ? null : h('span', { class: 'quiet' }, 'none'),
    keys.map((one) =>
      h(
        'button',
        {
          type: 'button',
          class: `facet-button${String(one) === String(current) ? ' on' : ''}`,
          'aria-pressed': String(String(one) === String(current)),
          dataset: { value: String(one), count: counts.get(one) },
          onclick: () => ui.navigate({ [key]: String(one) === String(current) ? null : one }),
        },
        glyph(one),
        `${words(one)} (${counts.get(one)})`,
      ),
    ),
    current && !keys.map(String).includes(String(current)) ? h('button', { type: 'button', class: 'facet-button on', 'aria-pressed': 'true', onclick: () => ui.navigate({ [key]: null }) }, `${words(name === 'epic' ? Number(current) : current)} (0)`) : null,
    note ? h('p', { class: 'quiet' }, note) : null,
  );
}

// -- one result ---------------------------------------------------------------------------------------

// The snippet with the matched words (between « and ») marked, as text only.
function snippet(text) {
  const out = [];
  const pattern = /«([^»]*)»/g;
  let at = 0;
  for (const found of text.matchAll(pattern)) {
    if (found.index > at) out.push(text.slice(at, found.index));
    out.push(h('mark', {}, found[1]));
    at = found.index + found[0].length;
  }
  if (at < text.length) out.push(text.slice(at));
  return out;
}

// The kind a record is shown with in the address: a node for an epic, story or task.
const recordKind = (type) => (['epic', 'story', 'task'].includes(type) ? 'node' : type);

function resultRow(item) {
  const parent = item.parent_id !== null ? `${KIND[item.parent_kind]?.label || item.parent_kind} #${item.parent_id} ${item.parent_title || ''}`.trim() : null;
  const record = item.item_type === 'decision' || item.item_type === 'fact';
  const work = ['epic', 'story', 'task'].includes(item.item_type);
  return h(
    'li',
    { class: `result matched-${item.matched}`, dataset: { type: item.item_type, id: item.id, matched: item.matched } },
    h(
      'div',
      { class: 'entry-head' },
      h('span', { class: `chip kind kind-${item.item_type}` }, kindIcon(item.item_type, 14), KIND[item.item_type]?.label || item.item_type),
      work ? statusChip(item.status) : h('span', { class: `chip status status-${item.status}` }, STATUS[item.status]?.label || capital(item.status)),
      item.confirmation === 'confirmed' ? h('span', { class: 'chip confirmed' }, icon('confirmed', { size: 14 }), 'Confirmed') : null,
      item.confirmation === 'unconfirmed' ? h('span', { class: 'chip unconfirmed' }, icon('unconfirmed', { size: 14 }), 'Unconfirmed') : null,
      h('span', { class: `chip matched matched-${item.matched}` }, MATCHED[item.matched] || item.matched),
      item.suggestion ? h('span', { class: 'chip suggestion' }, 'a suggestion') : null,
    ),
    recordLink(recordKind(item.item_type), item.id, { class: 'result-title' }, `#${item.id} ${item.title}`),
    parent ? h('span', { class: 'result-parent' }, ` in ${parent}`) : null,
    record && item.label ? h('p', { class: 'quiet record-label' }, item.label) : null,
    h('p', { class: 'snippet' }, ...snippet(item.snippet)),
    h('p', { class: 'quiet why' }, item.why),
  );
}
