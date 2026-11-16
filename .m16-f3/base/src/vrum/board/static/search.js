// The Search page: one box over the server's `search`. Each result says how it matched (exact words,
// meaning or both) and why, shows a snippet with the matched words marked, and its parent and status
// as the graph holds them now. The counts by kind, status and epic are worked out from the whole
// list of results, and filtering by them keeps the server's order. The page says how many items
// wait to be indexed, since results by meaning can lag where results by words never do. It keeps
// nothing: the search, the mode and the filters are in the address.

import { Refusal, callPaged } from './api.js';
import { ui } from './ctx.js';
import { clear, h, plural, put } from './dom.js';
import { askBox } from './ask.js';
import { KIND, STATUS } from './labels.js';
import { hrefFor, referenceKindOf } from './references.js';
import { epicOf, store } from './store.js';

const FIRST = 50;
const MODES = [
  ['both', 'Words and meaning'],
  ['words', 'Exact words only'],
  ['meaning', 'Meaning only'],
];
const MATCHED = { words: 'Exact words', meaning: 'Meaning (a suggestion)', both: 'Words and meaning' };
const TYPES = ['epic', 'story', 'task', 'rule', 'decision', 'principle', 'fact', 'belief'];

let root = null;
let params = new URLSearchParams();
let parts = {};
let results = null; // { items, first } for the query in the address
let shown = FIRST;
let generation = 0;
let loadedFor = '';

// -- the page -------------------------------------------------------------------------------------

export function mount(element, initial) {
  root = element;
  params = initial;
  results = null;
  loadedFor = '';
  shown = FIRST;
  const box = h('input', { id: 'search-box', name: 'q', type: 'search', autocomplete: 'off', value: params.get('q') || '', 'aria-label': 'What to look for', placeholder: 'Words, a phrase in quotes, or what you mean' });
  const mode = h('select', { id: 'search-mode', 'aria-label': 'How to search' }, MODES.map(([value, label]) => h('option', { value }, label)));
  mode.value = MODES.some(([value]) => value === params.get('mode')) ? params.get('mode') : 'both';
  parts = {
    box,
    mode,
    waiting: h('p', { class: 'waiting', id: 'waiting', role: 'status' }),
    note: h('p', { class: 'quiet', id: 'search-note' }),
    problem: h('p', { class: 'problem', role: 'alert', id: 'search-problem' }),
    summary: h('p', { class: 'count', id: 'count', role: 'status' }),
    facets: h('div', { class: 'facets', id: 'facets' }),
    list: h('div', { id: 'results' }),
  };
  put(root,
    h(
      'div',
      { class: 'toolbar' },
      h('h1', {}, 'Search'),
      h(
        'form',
        {
          class: 'filter search-form',
          role: 'search',
          onsubmit: (event) => {
            event.preventDefault();
            const asked = `${box.value.trim()}|${mode.value}`;
            ui.navigate({ q: box.value.trim() || null, mode: mode.value === 'both' ? null : mode.value, type: null, status: null, epic: null });
            // The same words again ask again (the service may be back, or something was saved).
            if (asked === `${(params.get('q') || '').trim()}|${params.get('mode') || 'both'}`) load();
          },
        },
        box,
        mode,
        h('button', { type: 'submit', class: 'primary', id: 'search-go' }, 'Search'),
      ),
    ),
    parts.waiting,
    parts.note,
    parts.problem,
    parts.summary,
    parts.facets,
    parts.list,
    askBox(),
  );
  drawWaiting();
  load();
}

export function unmount() {
  generation += 1;
  root = null;
}

export function update(next) {
  params = next;
  if (!root) return;
  const wanted = `${next.get('q') || ''}|${next.get('mode') || 'both'}`;
  if (wanted !== loadedFor) {
    shown = FIRST;
    load();
  } else draw();
}

export function refresh() {
  if (root && (params.get('q') || '').trim()) load(true);
}

// The count of items waiting to be indexed follows the server as it is, not as the search saw it.
export function index() {
  if (root) drawWaiting();
}

function drawWaiting() {
  const waiting = store.index ? store.index.waiting_to_index : null;
  parts.waiting.textContent =
    waiting === null
      ? 'Checking the index…'
      : `${plural(waiting, 'item')} waiting to be indexed. ${waiting ? 'Matches by meaning may lag behind them; matches by exact words never do.' : 'Matches by meaning are up to date.'}`;
  parts.waiting.dataset.waiting = String(waiting ?? '');
}

// -- asking the server ----------------------------------------------------------------------------

async function load(quiet = false) {
  const mine = ++generation;
  const query = (params.get('q') || '').trim();
  const mode = MODES.some(([value]) => value === params.get('mode')) ? params.get('mode') : 'both';
  parts.box.value = params.get('q') || '';
  parts.mode.value = mode;
  loadedFor = `${params.get('q') || ''}|${params.get('mode') || 'both'}`;
  if (!query) {
    results = null;
    parts.problem.textContent = '';
    parts.note.textContent = '';
    parts.summary.textContent = 'Type something to look for. Exact words are found the moment something is saved.';
    clear(parts.facets);
    clear(parts.list);
    return;
  }
  if (!quiet) parts.summary.textContent = 'Searching…';
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
  parts.note.textContent = found.first.index.note;
  draw();
}

// -- what the filters keep ----------------------------------------------------------------------------

// The epic an item of the graph is in: an epic, story or task by the graph, a rule by its story.
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

function draw() {
  if (!root || !results) return;
  const all = results.items;
  const type = params.get('type') || '';
  const status = params.get('status') || '';
  const epic = Number(params.get('epic')) || null;
  const epicOfItem = new Map(all.map((item) => [item, epicOfResult(item)]));
  const fits = (item, skip = '') =>
    (skip === 'type' || !type || item.item_type === type) &&
    (skip === 'status' || !status || item.status === status) &&
    (skip === 'epic' || !epic || epicOfItem.get(item) === epic);
  const kept = all.filter((item) => fits(item));

  parts.summary.textContent = `${plural(kept.length, 'result')}${kept.length === all.length ? '' : ` of ${all.length}`}.`;
  put(parts.facets,
    facet('type', 'Kind', count(all.filter((item) => fits(item, 'type')), (item) => item.item_type), (value) => KIND[value]?.label || value, TYPES, type),
    facet('status', 'Status', count(all.filter((item) => fits(item, 'status')), (item) => item.status), (value) => STATUS[value]?.label || value[0].toUpperCase() + value.slice(1), null, status),
    facet('epic', 'Epic', count(all.filter((item) => fits(item, 'epic')), (item) => epicOfItem.get(item)), (value) => `#${value} ${store.nodes.get(value)?.title || ''}`.trim(), null, epic ? String(epic) : '', epic ? 'Decisions, facts and beliefs belong to no epic, so they are left out while an epic is chosen.' : null),
  );
  const visible = kept.slice(0, shown);
  put(parts.list,
    kept.length
      ? h('ul', { class: 'plain results' }, visible.map(resultRow))
      : h('p', { class: 'quiet none' }, 'Nothing matches.'),
    kept.length > shown ? h('p', {}, h('button', { type: 'button', id: 'show-more', onclick: () => { shown += FIRST; draw(); } }, `Show ${Math.min(FIRST, kept.length - shown)} more`)) : null,
  );
}

// A group of filter buttons with their counts. Choosing one again takes it off.
function facet(name, label, counts, words, order, current, note = null) {
  const keys = order ? order.filter((key) => counts.has(key)) : [...counts.keys()].sort((a, b) => (typeof a === 'number' ? a - b : String(a).localeCompare(String(b))));
  return h(
    'fieldset',
    { class: 'facet', dataset: { facet: name } },
    h('legend', {}, label),
    keys.length ? null : h('span', { class: 'quiet' }, 'none'),
    keys.map((key) =>
      h(
        'button',
        {
          type: 'button',
          class: `facet-button${String(key) === String(current) ? ' on' : ''}`,
          'aria-pressed': String(String(key) === String(current)),
          dataset: { value: String(key), count: counts.get(key) },
          onclick: () => ui.navigate({ [name]: String(key) === String(current) ? null : key }),
        },
        `${words(key)} (${counts.get(key)})`,
      ),
    ),
    current && !keys.map(String).includes(String(current)) ? h('button', { type: 'button', class: 'facet-button on', onclick: () => ui.navigate({ [name]: null }) }, `${words(name === 'epic' ? Number(current) : current)} (0)`) : null,
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

function resultRow(item) {
  const kind = referenceKindOf(item.item_type);
  const parent = item.parent_id !== null ? `${KIND[item.parent_kind]?.label || item.parent_kind} #${item.parent_id} ${item.parent_title || ''}`.trim() : null;
  return h(
    'li',
    { class: `result matched-${item.matched}`, dataset: { type: item.item_type, id: item.id, matched: item.matched } },
    h(
      'div',
      { class: 'entry-head' },
      h('span', { class: `chip kind kind-${item.item_type}` }, h('span', { 'aria-hidden': 'true' }, KIND[item.item_type]?.sign || ''), ` ${KIND[item.item_type]?.label || item.item_type}`),
      h('span', { class: `chip status status-${item.status}` }, STATUS[item.status]?.label || item.status),
      h('span', { class: `chip matched matched-${item.matched}` }, MATCHED[item.matched] || item.matched),
      item.suggestion ? h('span', { class: 'chip suggestion' }, 'a suggestion') : null,
      item.label ? h('span', { class: 'chip model' }, item.label) : null,
    ),
    h('a', { class: 'result-title', href: hrefFor(kind, item.id) }, `#${item.id} ${item.title}`),
    parent ? h('span', { class: 'quiet' }, ` in ${parent}`) : null,
    h('p', { class: 'snippet' }, ...snippet(item.snippet)),
    h('p', { class: 'quiet why' }, item.why),
  );
}
