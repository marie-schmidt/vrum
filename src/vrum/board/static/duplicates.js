// Possible duplicates, the second tab of the Search page (`dups=1`): pairs of stories (or epics, or
// tasks, or acceptance criteria) in different epics that look like the same thing, with their scores from the
// index, most alike first, as a table with the two items side by side. A person can change the
// threshold and the epics, and can mark a pair as `duplicates` or `relates_to` with one click,
// which is the same add_link an agent calls. Looking never writes: nothing is linked unless a
// person clicks one of the buttons of a pair, and one click makes exactly one link. The choices are
// in the address: dup_type, dup_min, dup_epics (epic numbers, such as 3,7) and dup_unlinked.

import { Refusal, call, callAll } from './api.js';
import { can, itemLink, notify, recordCode, ui } from './ctx.js';
import { clear, h, plural, put } from './dom.js';
import { icon } from './icons.js';
import { KIND, STATUS, codeOf, kindIcon, nodeCode, nodeRef, parseNodeRef, statusChip } from './labels.js';
import { renderMarkdown } from './markdown.js';
import { ensure, ensureContext, store, sync } from './store.js';

const PAGE = 20;
const TYPES = [
  ['story', 'Stories'],
  ['epic', 'Epics'],
  ['task', 'Tasks'],
  ['rule', 'Acceptance criteria'],
];
// How a pair is said to be linked, by the link's type.
const MARKS = { duplicates: 'duplicates', relates_to: 'related' };
const ASKING = ['dup_type', 'dup_min', 'dup_epics', 'dup_unlinked'];

let root = null;
let params = new URLSearchParams();
let parts = {};
let pairs = []; // what has been read so far
let total = 0;
let nextOffset = null;
let threshold = null; // what the server used
let bodies = new Map(); // ref -> details, for the side-by-side view
let rest = null; // the pairs not yet shown, when only the unmarked ones are listed
let generation = 0;
let loadedFor = '';
let opened = new Set(); // the items whose details were opened in full, kept across a redraw

// What a person reads for an item, before its name: S-12 for work, AC-3 for an acceptance criterion.
const codeOfItem = (item) => {
  const at = parseNodeRef(item.ref);
  return at ? nodeCode(at.kind, at.number) : recordCode(item.item_type, item.id);
};
const asking = (from) => ASKING.map((name) => from.get(name) || '').join('|');
const itemType = () => (TYPES.some(([value]) => value === params.get('dup_type')) ? params.get('dup_type') : 'story');

// -- the view -------------------------------------------------------------------------------------

export function mount(element, initial) {
  root = element;
  params = initial;
  pairs = [];
  loadedFor = '';
  opened = new Set();
  const type = h('select', { id: 'dup-type', 'aria-label': 'What to compare', onchange: () => ui.navigate({ dup_type: type.value === 'story' ? null : type.value }) }, TYPES.map(([value, label]) => h('option', { value }, label)));
  const min = h('input', { id: 'dup-min', type: 'number', min: 0, max: 1, step: 0.01 });
  const epics = h('input', { id: 'dup-epics', type: 'text', value: params.get('dup_epics') || '', placeholder: 'such as 3, 7' });
  const unlinked = h('input', { id: 'dup-unlinked', type: 'checkbox', checked: params.get('dup_unlinked') === '1', onchange: () => ui.navigate({ dup_unlinked: unlinked.checked ? '1' : null }) });
  parts = {
    type,
    min,
    epics,
    unlinked,
    summary: h('p', { class: 'count', id: 'dup-count', role: 'status' }),
    note: h('p', { class: 'index-note', id: 'dup-note' }),
    problem: h('p', { class: 'problem', role: 'alert', id: 'dup-problem' }),
    list: h('div', { id: 'dup-pairs' }),
  };
  put(
    root,
    h(
      'form',
      {
        class: 'dup-form',
        onsubmit: (event) => {
          event.preventDefault();
          ui.navigate({ dup_min: min.value === '' ? null : min.value, dup_epics: epics.value.trim() || null });
        },
      },
      h(
        'div',
        { class: 'dup-fields' },
        h('div', { class: 'field' }, h('label', { for: 'dup-type' }, 'Compare'), type),
        h('div', { class: 'field' }, h('label', { for: 'dup-min' }, 'How alike (0 to 1)'), min),
        h('div', { class: 'field dup-epics-field' }, h('label', { for: 'dup-epics' }, 'In epics'), epics),
        h(
          'div',
          { class: 'buttons' },
          h('button', { type: 'submit', id: 'dup-apply' }, 'Apply'),
          h('button', { type: 'button', id: 'dup-reset', onclick: () => ui.navigate({ dup_min: null, dup_epics: null, dup_unlinked: null }) }, icon('clear', { size: 16 }), 'Clear'),
        ),
      ),
      h('label', { class: 'check', for: 'dup-unlinked' }, unlinked, ' Only pairs nobody has marked yet'),
    ),
    parts.problem,
    parts.note,
    parts.summary,
    parts.list,
  );
  load(true);
}

export function unmount() {
  generation += 1;
  root = null;
  parts = {};
}

export function update(next) {
  params = next;
  if (!root) return;
  if (asking(next) !== loadedFor) load(true);
}

export function refresh() {
  if (root) load(false);
}

export function again() {
  if (root) load(true);
}

// -- reading from the server ---------------------------------------------------------------------

// An epic as typed (3, E-3 or epic:3) is sent as its ref; anything else goes as it is, so that the
// server says what is wrong with it.
const epicRefs = () =>
  (params.get('dup_epics') || '')
    .split(/[\s,]+/)
    .filter(Boolean)
    .map((text) => {
      const number = /^(?:e-)?(\d+)$/i.exec(text);
      return number ? nodeRef('epic', Number(number[1])) : text;
    });

async function read(offset, size = PAGE) {
  const asked = params.get('dup_min');
  const epics = epicRefs();
  return call('find_overlaps', {
    item_type: itemType(),
    limit: size,
    offset,
    ...(asked !== null && asked !== '' ? { min_similarity: Number(asked) } : {}),
    ...(epics.length ? { epics } : {}),
  });
}

// Every pair there is, for the view that leaves out the ones somebody has already marked.
async function readEvery() {
  const first = await read(0, 200);
  const every = [...first.items];
  let page = first;
  while (page.next_offset !== null && page.next_offset !== undefined) {
    page = await read(page.next_offset, 200);
    every.push(...page.items);
  }
  return { first, every };
}

// The words of the items in these pairs, for the side-by-side view: the work items with their
// details in one filtered read for each kind, and the acceptance criteria from those the board already holds.
async function describe(items) {
  const wanted = items.filter((item) => !bodies.has(item.ref));
  for (const type of ['epic', 'story', 'task']) {
    const numbers = wanted.filter((item) => item.item_type === type).map((item) => parseNodeRef(item.ref).number);
    if (!numbers.length) continue;
    for (const node of await callAll('list_nodes', { query: `kind:${type} number:${numbers.join(',')}`, detail: true })) bodies.set(node.ref, node.body || '');
  }
  if (wanted.some((item) => item.item_type === 'rule')) {
    await ensure('rules');
    for (const rule of store.rules) bodies.set(`rule:${rule.id}`, rule.body || '');
  }
  // What the texts refer to is named by the same lists, so a reference is not taken for missing.
  if ([...bodies.values()].some((text) => text.includes('[['))) await ensureContext();
}

async function load(fresh) {
  const mine = ++generation;
  const type = itemType();
  loadedFor = asking(params);
  parts.type.value = type;
  if (fresh) parts.epics.value = params.get('dup_epics') || '';
  if (fresh) {
    parts.summary.textContent = 'Looking…';
    if (!pairs.length) put(parts.list, h('p', { class: 'loading', role: 'status' }, 'Looking for pairs…'));
    bodies = new Map();
  }
  try {
    let first;
    let all;
    if (params.get('dup_unlinked') === '1') {
      // Every page is read and the pairs somebody has marked are left out; the rest are shown a
      // page at a time from what was read.
      const found = await readEvery();
      if (mine !== generation || !root) return;
      first = found.first;
      const open = found.every.filter((pair) => pair.already_linked.length === 0);
      const keep = fresh ? PAGE : Math.max(PAGE, pairs.length);
      all = open.slice(0, keep);
      rest = open.slice(keep);
      total = open.length;
      nextOffset = rest.length ? 1 : null;
    } else {
      rest = null;
      first = await read(0);
      if (mine !== generation || !root) return;
      // A refresh keeps as many pairs as are already on the page.
      const keep = fresh ? PAGE : Math.max(PAGE, pairs.length);
      let page = first;
      all = [...first.items];
      while (all.length < keep && page.next_offset !== null && page.next_offset !== undefined) {
        page = await read(page.next_offset);
        all.push(...page.items);
      }
      if (mine !== generation || !root) return;
      total = first.total;
      nextOffset = page.next_offset ?? null;
    }
    pairs = all;
    threshold = first.threshold;
    parts.problem.textContent = '';
    // Said only when there is something to know: meaning could not be used, or some items are not indexed yet.
    const { meaning, note, waiting_to_index: waiting } = first.index;
    const fine = meaning === 'used' || meaning === 'not_requested';
    parts.note.className = fine ? 'index-line' : 'index-note';
    put(
      parts.note,
      !fine && note
        ? [icon('alert', { size: 14 }), h('span', {}, note)]
        : fine && waiting
          ? [icon('clock', { size: 14 }), h('span', {}, `${plural(waiting, 'item')} waiting to be indexed, so not compared by meaning yet.`)]
          : null,
    );
    if (fresh) parts.min.value = params.get('dup_min') ?? String(threshold);
    await describe(all.flatMap((pair) => [pair.first, pair.second]));
  } catch (error) {
    if (mine !== generation || !root) return;
    if (!(error instanceof Refusal)) throw error;
    pairs = [];
    parts.problem.textContent = error.message;
    parts.summary.textContent = '';
    clear(parts.list);
    return;
  }
  if (mine !== generation || !root) return;
  draw();
}

async function more() {
  const mine = generation;
  let items;
  if (rest !== null) {
    items = rest.splice(0, PAGE);
    nextOffset = rest.length ? 1 : null;
  } else {
    const page = await read(nextOffset);
    if (mine !== generation || !root) return;
    items = page.items;
    nextOffset = page.next_offset ?? null;
  }
  pairs = [...pairs, ...items];
  await describe(items.flatMap((pair) => [pair.first, pair.second]));
  if (mine === generation && root) draw();
}

// -- drawing --------------------------------------------------------------------------------------

function draw() {
  const only = params.get('dup_unlinked') === '1';
  parts.summary.textContent = total
    ? `${plural(total, 'pair')} at ${threshold} or more alike${only ? ', none of them marked yet' : ''}${pairs.length < total ? `; ${pairs.length} shown` : ''}`
    : `No pairs are ${threshold} or more alike${only ? ' that nobody has marked yet' : ''}`;
  put(
    parts.list,
    total ? pairTable() : h('div', { class: 'empty search-empty' }, icon('link', { size: 20 }), h('p', {}, 'Nothing to compare at this level. Lower the number for “how alike” to see weaker pairs.')),
    nextOffset !== null ? h('p', { class: 'more' }, h('button', { type: 'button', id: 'dup-more', onclick: more }, 'Show more pairs')) : null,
  );
  // Details run a few lines at most; the ones that are longer can be opened in full.
  requestAnimationFrame(fitSides);
}

function pairTable() {
  const column = (name, label) => h('th', { scope: 'col', class: `col-${name}` }, label);
  return h(
    'div',
    { class: 'result-table pair-table' },
    h(
      'table',
      { class: 'pairs' },
      h('caption', { class: 'sr-only' }, 'Pairs that look alike, most alike first'),
      h('thead', {}, h('tr', {}, column('score', 'How alike'), column('first', 'One'), column('second', 'The other'), column('marks', 'What to do'))),
      h('tbody', {}, pairs.map((pair) => pairRow(pair))),
    ),
  );
}

// "Show all" and "Show less" for the details of one item. The button's name says whose details,
// since a page holds many of them.
const toggleName = (open, title) => `${open ? 'Show less' : 'Show all'} of ${title}`;

function toggleDetails(side, ref, button, title) {
  const open = side.classList.toggle('open');
  if (open) opened.add(ref);
  else opened.delete(ref);
  button.setAttribute('aria-expanded', String(open));
  button.setAttribute('aria-label', toggleName(open, title));
  button.textContent = open ? 'Show less' : 'Show all';
}

function detailsToggle(side, ref, title) {
  const open = side.classList.contains('open');
  const button = h('button', { type: 'button', class: 'link side-toggle', 'aria-expanded': String(open), 'aria-label': toggleName(open, title) }, open ? 'Show less' : 'Show all');
  button.addEventListener('click', () => toggleDetails(side, ref, button, title));
  return button;
}

function fitSides() {
  if (!root) return;
  for (const side of parts.list.querySelectorAll('.side')) {
    const body = side.querySelector('.side-body');
    if (body && !side.querySelector('.side-toggle') && body.scrollHeight > body.clientHeight + 2) side.append(detailsToggle(side, side.dataset.ref, side.querySelector('.side-title').textContent));
  }
}

function side(item) {
  const text = bodies.get(item.ref);
  const open = opened.has(item.ref);
  const element = h(
    'td',
    { class: `side${open ? ' open' : ''}`, dataset: { type: item.item_type, ref: item.ref } },
    h(
      'div',
      { class: 'pair-head' },
      h('span', { class: `chip kind kind-${item.item_type}` }, kindIcon(item.item_type, 14), `${KIND[item.item_type].label} ${codeOfItem(item)}`),
      STATUS[item.status] ? statusChip(item.status) : h('span', { class: 'chip' }, item.status),
    ),
    h('p', { class: 'side-title' }, itemLink(item.ref, {}, item.title)),
    item.epic ? h('p', { class: 'quiet side-epic' }, `In epic ${codeOf(item.epic)} ${store.nodes.get(item.epic)?.title ?? item.epic_title ?? ''}`.trim()) : null,
    h('div', { class: 'side-body' }, text ? renderMarkdown(text) : h('p', { class: 'quiet' }, 'No details.')),
  );
  if (open) element.append(detailsToggle(element, item.ref, item.title));
  return element;
}

// One click, one link: the same add_link an agent calls.
async function link(pair, type) {
  try {
    await call('add_link', { source: pair.first.ref, target: pair.second.ref, link_type: type });
    notify(`Marked “${pair.first.title}” and “${pair.second.title}” as ${MARKS[type]}.`);
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
  }
  await sync();
  if (root) load(false);
}

function pairRow(pair) {
  const linkable = pair.first.item_type !== 'rule' && can('write');
  // The two items are side by side in the row, so the buttons say what is done to them; the button's
  // tooltip and its name for a screen reader say which two items, in full.
  const both = `“${pair.first.title}” and “${pair.second.title}”`;
  const buttons = linkable
    ? Object.keys(MARKS).map((type) => {
        const marked = pair.already_linked.includes(type);
        const words = marked ? `Marked as ${MARKS[type]}` : `Mark as ${MARKS[type]}`;
        return h(
          'button',
          {
            type: 'button',
            class: `mark mark-${type}${type === 'duplicates' ? '' : ' secondary'}`,
            disabled: marked,
            title: `${words}: ${both}`,
            'aria-label': `${words}: ${both}`,
            onclick: () => link(pair, type),
          },
          icon(marked ? 'check' : 'link', { size: 14 }),
          words,
        );
      })
    : [h('span', { class: 'quiet' }, pair.first.item_type === 'rule' ? 'Acceptance criteria cannot be linked to each other. Open both to compare them.' : 'You can look at the pair but not change it.')];
  return h(
    'tr',
    { class: 'pair', dataset: { first: pair.first.ref, second: pair.second.ref, similarity: pair.similarity } },
    h(
      'td',
      { class: 'cell-score' },
      h(
        'div',
        { class: 'score' },
        h('strong', {}, pair.similarity.toFixed(3)),
        h('span', { class: 'meter', role: 'meter', 'aria-label': 'How alike', 'aria-valuemin': 0, 'aria-valuemax': 1, 'aria-valuenow': pair.similarity }, h('span', { css: { width: `${Math.round(pair.similarity * 100)}%` } })),
      ),
      pair.already_linked.length ? h('span', { class: 'chip' }, `Already linked: ${pair.already_linked.map((type) => MARKS[type] || type).join(', ')}`) : null,
    ),
    side(pair.first),
    side(pair.second),
    h('td', { class: 'cell-marks' }, h('div', { class: 'buttons' }, ...buttons)),
  );
}

