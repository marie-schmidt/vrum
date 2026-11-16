// The Overlaps page: pairs of stories (or epics, or rules) in different epics that look like the same
// thing, with their scores from the index, most alike first, and the two items side by side. A person
// can change the threshold, and can mark a pair as `duplicates` or `relates_to` with one click, which
// is the same add_link an agent calls. Nothing is ever linked unless a person clicks.

import { Refusal, call, callAll } from './api.js';
import { actor, notify, ui } from './ctx.js';
import { clear, h, plural, put } from './dom.js';
import { KIND, LINK, STATUS } from './labels.js';
import { renderMarkdown } from './markdown.js';
import { hrefFor, needTargets } from './references.js';
import { ensure, store, sync } from './store.js';

const PAGE = 20;
const TYPES = [
  ['story', 'Stories'],
  ['epic', 'Epics'],
  ['rule', 'Rules'],
];

let root = null;
let params = new URLSearchParams();
let parts = {};
let pairs = []; // what has been read so far
let total = 0;
let nextOffset = null;
let threshold = null; // what the server used
let bodies = new Map(); // "type:id" -> details, for the side-by-side view
let generation = 0;
let loadedFor = '';

const keyOf = (item) => `${item.item_type}:${item.id}`;

// -- the page -------------------------------------------------------------------------------------

export function mount(element, initial) {
  root = element;
  params = initial;
  pairs = [];
  loadedFor = '';
  const type = h('select', { id: 'overlap-type', 'aria-label': 'What to compare', onchange: () => ui.navigate({ type: type.value === 'story' ? null : type.value }) }, TYPES.map(([value, label]) => h('option', { value }, label)));
  const min = h('input', { id: 'overlap-min', type: 'number', min: 0, max: 1, step: 0.01, 'aria-describedby': 'overlap-help' });
  const epics = h('input', { id: 'overlap-epics', type: 'text', value: params.get('epics') || '', placeholder: 'such as 3, 7', 'aria-describedby': 'overlap-help' });
  parts = {
    type,
    min,
    epics,
    summary: h('p', { class: 'count', id: 'count', role: 'status' }),
    note: h('p', { class: 'quiet', id: 'overlap-note' }),
    problem: h('p', { class: 'problem', role: 'alert', id: 'overlap-problem' }),
    list: h('div', { id: 'pairs' }),
  };
  put(root,
    h(
      'div',
      { class: 'toolbar' },
      h('h1', {}, 'Overlaps'),
      h(
        'form',
        {
          class: 'filter',
          onsubmit: (event) => {
            event.preventDefault();
            ui.navigate({ min: min.value === '' ? null : min.value, epics: epics.value.trim() || null });
          },
        },
        h('label', { for: 'overlap-type' }, 'Compare'),
        type,
        h('label', { for: 'overlap-min' }, 'How alike they must be (0 to 1)'),
        min,
        h('label', { for: 'overlap-epics' }, 'Only pairs with something in epics (ids)'),
        epics,
        h('button', { type: 'submit', id: 'apply-threshold' }, 'Apply'),
        h('button', { type: 'button', id: 'reset-threshold', onclick: () => ui.navigate({ min: null, epics: null }) }, 'Start again'),
      ),
      h('p', { class: 'help', id: 'overlap-help' }, 'Pairs in different epics that look like the same thing, most alike first. The threshold starts at the server’s setting, and the epics box leaves out every pair that has nothing in the epics you name. This only reports: nothing is linked unless you click one of the buttons on a pair.'),
    ),
    parts.problem,
    parts.summary,
    parts.note,
    parts.list,
  );
  load(true);
}

export function unmount() {
  generation += 1;
  root = null;
}

const asking = (from) => `${from.get('type') || 'story'}|${from.get('min') || ''}|${from.get('epics') || ''}`;

export function update(next) {
  params = next;
  if (!root) return;
  if (asking(next) !== loadedFor) load(true);
}

export function refresh() {
  if (root) load(false);
}

// -- reading from the server ---------------------------------------------------------------------

const itemType = () => (TYPES.some(([value]) => value === params.get('type')) ? params.get('type') : 'story');

// Numbers go as numbers; anything else goes as it is, so that the server says what is wrong with it.
const epicIds = () => (params.get('epics') || '').split(/[\s,]+/).filter(Boolean).map((id) => (/^\d+$/.test(id) ? Number(id) : id));

async function read(offset) {
  const asked = params.get('min');
  const epics = epicIds();
  return call('find_overlaps', {
    item_type: itemType(),
    limit: PAGE,
    offset,
    ...(asked !== null && asked !== '' ? { min_similarity: Number(asked) } : {}),
    ...(epics.length ? { epic_ids: epics } : {}),
  });
}

// The words of the items in these pairs, for the side-by-side view: the nodes with their details in
// one filtered read, and the rules from the rules the board already holds.
async function describe(items) {
  const wanted = items.filter((item) => !bodies.has(keyOf(item)));
  const nodes = wanted.filter((item) => item.item_type !== 'rule').map((item) => item.id);
  if (nodes.length) {
    for (const node of await callAll('list_nodes', { query: `id:${nodes.join(',')}`, detail: true })) bodies.set(`${node.kind}:${node.id}`, node.body || '');
  }
  if (wanted.some((item) => item.item_type === 'rule')) {
    await ensure('rules');
    for (const rule of store.rules) bodies.set(`rule:${rule.id}`, rule.body || '');
  }
}

async function load(fresh) {
  const mine = ++generation;
  const type = itemType();
  loadedFor = asking(params);
  parts.type.value = type;
  if (fresh) parts.epics.value = params.get('epics') || '';
  if (fresh) {
    parts.summary.textContent = 'Looking…';
    bodies = new Map();
  }
  try {
    const first = await read(0);
    if (mine !== generation || !root) return;
    // A refresh keeps as many pairs as are already on the page.
    const keep = fresh ? PAGE : Math.max(PAGE, pairs.length);
    let page = first;
    const all = [...first.items];
    while (all.length < keep && page.next_offset !== null && page.next_offset !== undefined) {
      page = await read(page.next_offset);
      all.push(...page.items);
    }
    if (mine !== generation || !root) return;
    pairs = all;
    total = first.total;
    nextOffset = page.next_offset ?? null;
    threshold = first.threshold;
    parts.problem.textContent = '';
    parts.note.textContent = first.index.note;
    if (fresh) parts.min.value = params.get('min') ?? String(threshold);
    await describe(all.flatMap((pair) => [pair.first, pair.second]));
    await needTargets(...bodies.values());
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
  const page = await read(nextOffset);
  if (mine !== generation || !root) return;
  pairs = [...pairs, ...page.items];
  nextOffset = page.next_offset ?? null;
  await describe(page.items.flatMap((pair) => [pair.first, pair.second]));
  draw();
}

// -- drawing --------------------------------------------------------------------------------------

function draw() {
  parts.summary.textContent = total
    ? `${plural(total, 'pair')} at ${threshold} or more alike${pairs.length < total ? `; ${pairs.length} shown` : ''}.`
    : `No pairs are ${threshold} or more alike.`;
  put(parts.list,
    total ? h('ul', { class: 'plain pairs' }, pairs.map(pairRow)) : null,
    nextOffset !== null ? h('p', {}, h('button', { type: 'button', id: 'show-more', onclick: more }, 'Show more pairs')) : null,
  );
}

function side(item) {
  const text = bodies.get(keyOf(item));
  const kind = item.item_type === 'rule' ? 'rule' : 'node';
  return h(
    'div',
    { class: 'side', dataset: { type: item.item_type, id: item.id } },
    h('div', { class: 'entry-head' }, h('span', { class: `chip kind kind-${item.item_type}` }, h('span', { 'aria-hidden': 'true' }, KIND[item.item_type].sign), ` ${KIND[item.item_type].label} #${item.id}`), h('span', { class: 'chip' }, STATUS[item.status]?.label || item.status)),
    h('h3', {}, h('a', { href: hrefFor(kind, item.id) }, item.title)),
    item.epic_id !== null ? h('p', { class: 'quiet' }, `In epic #${item.epic_id} ${item.epic_title || ''}`.trim()) : null,
    text ? renderMarkdown(text) : h('p', { class: 'quiet' }, 'No details.'),
  );
}

async function link(pair, type) {
  try {
    await call('add_link', { actor: actor(), source_id: pair.first.id, target_id: pair.second.id, link_type: type });
    notify(`Marked #${pair.first.id} and #${pair.second.id} as “${LINK[type].label}”.`);
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
  }
  await sync();
  load(false);
}

function pairRow(pair) {
  const linkable = pair.first.item_type !== 'rule';
  const buttons = linkable
    ? ['duplicates', 'relates_to'].map((type) =>
        h(
          'button',
          {
            type: 'button',
            class: `mark mark-${type}`,
            disabled: pair.already_linked.includes(type),
            onclick: () => link(pair, type),
          },
          pair.already_linked.includes(type) ? `Already marked: ${LINK[type].label}` : type === 'duplicates' ? 'Mark as duplicates' : 'Mark as relates to',
        ),
      )
    : [h('span', { class: 'quiet' }, 'Rules cannot be linked to each other. Open both to compare them.')];
  return h(
    'li',
    { class: 'pair', dataset: { first: pair.first.id, second: pair.second.id, similarity: pair.similarity } },
    h('p', { class: 'score' }, h('strong', {}, pair.similarity.toFixed(3)), ' alike', pair.already_linked.length ? h('span', { class: 'chip' }, `already linked: ${pair.already_linked.map((type) => LINK[type]?.label || type).join(', ')}`) : null),
    h('div', { class: 'sides' }, side(pair.first), side(pair.second)),
    h('div', { class: 'buttons' }, ...buttons),
  );
}
