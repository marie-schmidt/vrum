// The Search page (#/search): the box in the top bar goes here, and a result opens the record's own
// page. Each result says how it matched (exact words, meaning or both) and why, shows a snippet
// with the matched words marked, and its parent and status as the graph holds them now. The counts
// by kind, status and epic are worked out from the whole list of results, and filtering by them
// keeps the server's order. The page says how many items wait to be indexed, since results by
// meaning can lag where results by words never do, and says in the server's words why meaning
// results are missing when the embedding service is not there. Possible duplicates is the second
// tab of the same page (duplicates.js). It keeps nothing: the search and the filters are
// in the address (search, search_type, search_status, search_epic, and dups with its
// own choices). A search always uses exact words and meaning together.

import { Refusal, callPaged } from './api.js';
import { itemLink, ui } from './ctx.js';
import { clear, h, plural, put } from './dom.js';
import * as duplicates from './duplicates.js';
import { icon } from './icons.js';
import { KIND, anyStatusIcon, codeOf, kindIcon, nodeCode, nodeRef, parseNodeRef, plainNoteStatus, plainRequirementStatus, statusChip, statusLabel } from './labels.js';
import { pageHeader } from './page.js';
import { epicOf, store } from './store.js';

const FIRST = 50;
const MATCHED = { words: 'Exact words', meaning: 'Meaning (a suggestion)', both: 'Words and meaning' };
const TYPES = ['epic', 'story', 'task', 'rule', 'note'];
const WORK = ['epic', 'story', 'task'];

let root = null;
let params = new URLSearchParams();
let parts = {};
let inDuplicates = null; // which tab is on show: true for possible duplicates, false for results
let results = null; // { items, first } for the query in the address
let shown = FIRST;
let generation = 0;
let loadedFor = '';

const askedOf = (from) => (from.get('search') || '').trim();

// -- the page -------------------------------------------------------------------------------------

export function mount(element, initial) {
  root = element;
  params = initial;
  inDuplicates = null;
  results = null;
  parts = {
    tabs: h(
      'div',
      { class: 'page-tabs', role: 'group', 'aria-label': 'What to do' },
      h('button', { type: 'button', id: 'search-tab-results', onclick: () => ui.navigate({ dups: null }) }, icon('search', { size: 16 }), 'Search'),
      h('button', { type: 'button', id: 'search-tab-dups', onclick: () => ui.navigate({ dups: '1' }) }, icon('link', { size: 16 }), 'Possible duplicates'),
    ),
    view: h('div', { id: 'search-view', class: 'search-view' }),
  };
  parts.page = h('div', { class: 'search-page' }, pageHeader('Search'), parts.tabs, parts.view);
  put(root, parts.page);
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
  // The same words only redraw: what is typed in the box and not asked yet stays.
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

// The same words asked again (the service may be back, or something was saved). The shell calls
// it when the top bar asks for the words the page already holds.
export function again() {
  if (!root) return;
  if (inDuplicates) duplicates.again();
  else load();
}

// Where the keyboard goes when the page opens: the box for a new search, the title otherwise.
export function focus() {
  const target = inDuplicates || (params.get('search') || '').trim() ? parts.page?.querySelector('h1') : parts.box;
  if (target) {
    if (target.tagName === 'H1') target.tabIndex = -1;
    target.focus();
  }
}

// Which of the two tabs the address asks for: the one that is on show is torn down first.
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
  parts.page.dataset.mode = wanted ? 'duplicates' : 'results';
  if (wanted) {
    clear(parts.view);
    duplicates.mount(parts.view, params);
    return;
  }
  buildResults();
  drawWaiting();
  load();
}

// -- the results tab ------------------------------------------------------------------------------

function buildResults() {
  shown = FIRST;
  loadedFor = '';
  const box = h('input', { id: 'search-box', name: 'search', type: 'search', autocomplete: 'off', value: params.get('search') || '', 'aria-label': 'What to look for', placeholder: 'Words, a phrase in quotes, or what you mean' });
  Object.assign(parts, {
    box,
    waiting: h('p', { class: 'index-line', id: 'search-waiting', role: 'status' }),
    note: h('p', { class: 'index-note', id: 'search-note' }),
    problem: h('p', { class: 'problem', role: 'alert', id: 'search-problem' }),
    summary: h('p', { class: 'count', id: 'search-count', role: 'status' }),
    clearFilters: h('button', { type: 'button', class: 'small', id: 'search-clear-filters', hidden: true, onclick: () => ui.navigate({ search_type: null, search_status: null, search_epic: null }) }, icon('clear', { size: 14 }), 'Clear the filters'),
    facets: h('div', { class: 'facets', id: 'search-facets' }),
    list: h('div', { id: 'search-results', tabindex: '-1' }),
    // Between the form and the results there are as many buttons as there are filters.
    skip: h('button', { type: 'button', class: 'skip-filters', hidden: true, onclick: () => parts.list.focus() }, 'Skip the filters and go to the results'),
  });
  put(
    parts.view,
    h(
      'form',
      {
        class: 'search-form',
        role: 'search',
        onsubmit: (event) => {
          event.preventDefault();
          const asked = box.value.trim();
          ui.navigate({ search: asked || null, search_type: null, search_status: null, search_epic: null });
          // The same words again ask again.
          if (asked === (params.get('search') || '').trim()) load();
        },
      },
      h('div', { class: 'search-field' }, icon('search', { size: 16 }), box),
      h('button', { type: 'submit', class: 'primary', id: 'search-go' }, 'Search'),
    ),
    h('div', { class: 'search-notices' }, parts.waiting, parts.note, parts.problem),
    parts.skip,
    parts.facets,
    h('div', { class: 'result-bar' }, parts.summary, parts.clearFilters),
    parts.list,
  );
}

function drawWaiting() {
  const waiting = store.index ? store.index.waiting_to_index : null;
  put(parts.waiting, waiting ? [icon('clock', { size: 14 }), h('span', {}, `${plural(waiting, 'item')} waiting to be indexed`)] : null);
  parts.waiting.dataset.waiting = String(waiting ?? '');
}

// Said under the box when meaning could not be used: the server's own words. When it was used, or was not
// asked for, there is nothing to say (each result says how it matched).
function drawNote(text, meaning = 'used') {
  const fine = meaning === 'used' || meaning === 'not_requested';
  put(parts.note, text && !fine ? [icon('alert', { size: 14 }), h('span', {}, text)] : null);
}

// A quiet block in place of results: nothing asked yet, nothing found, or reading.
const hush = (glyph, ...lines) => h('div', { class: 'empty search-empty' }, icon(glyph, { size: 20 }), lines.map((line) => h('p', {}, line)));

// -- asking the server ----------------------------------------------------------------------------

async function load(quiet = false) {
  const mine = ++generation;
  const query = (params.get('search') || '').trim();
  parts.box.value = params.get('search') || '';
  loadedFor = askedOf(params);
  if (!query) {
    results = null;
    parts.problem.textContent = '';
    drawNote('');
    parts.summary.textContent = '';
    parts.clearFilters.hidden = true;
    parts.skip.hidden = true;
    clear(parts.facets);
    put(parts.list, hush('search', 'Type something to look for.'));
    return;
  }
  if (!quiet) {
    parts.summary.textContent = 'Searching…';
    if (!results) put(parts.list, h('p', { class: 'loading', role: 'status' }, 'Searching…'));
  }
  let found;
  try {
    found = await callPaged('search', { query });
  } catch (error) {
    if (mine !== generation || !root) return;
    if (!(error instanceof Refusal)) throw error;
    results = null;
    parts.problem.textContent = error.message;
    parts.summary.textContent = '';
    parts.clearFilters.hidden = true;
    parts.skip.hidden = true;
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

// The number of the epic an item of the graph is in: an epic, story or task by the graph, a
// an acceptance criterion by its story or task. The address (search_epic) carries that number.
function epicOfResult(item) {
  if (WORK.includes(item.item_type)) return epicOf(store.nodes.get(item.ref))?.number ?? null;
  if (item.item_type === 'rule' && item.parent) return epicOf(store.nodes.get(item.parent))?.number ?? null;
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

  parts.summary.textContent = `${plural(kept.length, 'result')}${kept.length === all.length ? '' : ` of ${all.length}`}`;
  parts.clearFilters.hidden = !(type || status || epic);
  parts.skip.hidden = false;
  put(
    parts.facets,
    facet('type', 'Kind', count(all.filter((item) => fits(item, 'type')), (item) => item.item_type), (value) => KIND[value]?.label || value, TYPES, type),
    facet('status', 'Status', count(all.filter((item) => fits(item, 'status')), (item) => item.status), statusLabel, null, status),
    facet('epic', 'Epic', count(all.filter((item) => fits(item, 'epic')), (item) => epicOfItem.get(item)), (value) => `${nodeCode('epic', value)} ${store.nodes.get(nodeRef('epic', value))?.title || ''}`.trim(), null, epic ? String(epic) : '', epic ? 'Notes belong to no epic, so they are left out while an epic is chosen.' : null),
  );
  const visible = kept.slice(0, shown);
  put(
    parts.list,
    kept.length
      ? resultTable(visible)
      : all.length
        ? h('div', { class: 'empty search-empty' }, icon('filter', { size: 20 }), h('p', {}, 'Nothing matches these filters.'), h('button', { type: 'button', class: 'small', onclick: () => ui.navigate({ search_type: null, search_status: null, search_epic: null }) }, `Show all ${plural(all.length, 'result')}`))
        : hush('search', 'Nothing matches. Try other words, or another way to search.'),
    kept.length > shown ? h('p', { class: 'more' }, h('button', { type: 'button', id: 'show-more', onclick: () => { shown += FIRST; draw(); } }, `Show ${Math.min(FIRST, kept.length - shown)} more`), h('span', { class: 'quiet' }, `${visible.length} of ${kept.length} shown`)) : null,
  );
}

// A group of filter buttons with their counts. Choosing one again takes it off. The address holds
// the choice as search_type, search_status or search_epic.
function facet(name, label, counts, words, order, current, note = null) {
  const key = `search_${name}`;
  const keys = order ? order.filter((one) => counts.has(one)) : [...counts.keys()].sort((a, b) => (typeof a === 'number' ? a - b : String(a).localeCompare(String(b))));
  const glyph = (one) => (name === 'type' ? kindIcon(one, 14) : name === 'status' ? anyStatusIcon(one, 14) : name === 'epic' ? icon('epic', { size: 14 }) : null);
  const button = (one, total, on) =>
    h(
      'button',
      {
        type: 'button',
        class: `facet-button${on ? ' on' : ''}`,
        'aria-pressed': String(on),
        title: name === 'epic' ? words(one) : null,
        dataset: { value: String(one), count: total },
        onclick: () => ui.navigate({ [key]: on ? null : one }),
      },
      glyph(one),
      h('span', { class: 'facet-label' }, words(one)),
      ' ',
      h('span', { class: 'facet-count' }, `(${total})`),
    );
  return h(
    'fieldset',
    { class: 'facet', dataset: { facet: name } },
    h('legend', {}, label),
    h(
      'div',
      { class: 'facet-buttons' },
      keys.length ? null : h('span', { class: 'quiet' }, 'None'),
      keys.map((one) => button(one, counts.get(one), String(one) === String(current))),
      current && !keys.map(String).includes(String(current)) ? button(name === 'epic' ? Number(current) : current, 0, true) : null,
    ),
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

// The code a person reads for a result: S-12 for work, #3 for an acceptance criterion or a note.
const codeOfResult = (item) => {
  const at = parseNodeRef(item.ref);
  return at ? nodeCode(at.kind, at.number) : `#${item.id}`;
};

// The results as a table: the kind, the item, how it matched, its status, and why with a snippet.
// On a narrow page each row stacks into a card (pages.css), so the same elements serve both.
function resultTable(items) {
  const column = (name, label) => h('th', { scope: 'col', class: `col-${name}` }, label);
  return h(
    'div',
    { class: 'result-table' },
    h(
      'table',
      { class: 'results' },
      h('caption', { class: 'sr-only' }, 'Search results, most relevant first'),
      h('thead', {}, h('tr', {}, column('type', 'Type'), column('item', 'Item'), column('matched', 'Matched'), column('status', 'Status'), column('why', 'Why it matched'))),
      h('tbody', {}, items.map(resultRow)),
    ),
  );
}

function resultRow(item) {
  const parentKind = item.parent ? parseNodeRef(item.parent)?.kind : null;
  const work = WORK.includes(item.item_type);
  const title = itemLink(item.ref, { class: 'result-title' }, h('span', { class: 'result-code' }, codeOfResult(item)), ' ', item.title);
  return h(
    'tr',
    {
      class: `result matched-${item.matched}`,
      dataset: { type: item.item_type, ref: item.ref, matched: item.matched, ...(work ? {} : { id: item.id }) },
      // A click anywhere on the row opens the page, unless it was on a link or a button of its own
      // or ended a selection of the text; the keyboard and screen readers use the title link.
      onclick: (event) => {
        if (event.target.closest('a, button') || String(getSelection()).length) return;
        title.click();
      },
    },
    h('td', { class: 'cell-type' }, h('span', { class: `chip kind kind-${item.item_type}` }, kindIcon(item.item_type, 14), KIND[item.item_type]?.label || item.item_type)),
    h(
      'td',
      { class: 'cell-item' },
      title,
      item.parent ? h('p', { class: 'result-parent' }, 'in ', itemLink(item.parent, {}, `${KIND[parentKind]?.label || parentKind} ${codeOf(item.parent)} ${store.nodes.get(item.parent)?.title ?? ''}`.trim())) : null,
    ),
    h('td', { class: 'cell-matched' }, h('span', { class: `chip matched matched-${item.matched}` }, MATCHED[item.matched] || item.matched)),
    h('td', { class: 'cell-status' }, work ? statusChip(item.status) : item.item_type === 'note' ? plainNoteStatus(item.status) : plainRequirementStatus(item.status)),
    h('td', { class: 'cell-why' }, h('p', { class: 'snippet' }, ...snippet(item.snippet)), h('p', { class: 'quiet why' }, item.why)),
  );
}
