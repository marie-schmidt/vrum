// Home: what needs attention, at a glance. Each number is a link to the page that shows exactly
// those items, and each is the answer to the question an agent would ask the server the same way
// (the work through the filter language, the coverage report, the lists of decisions and beliefs,
// the overlap finder, the index and the server's own status). Nothing here is kept: a number is
// read when the page opens, when something changes, and every few seconds, because a claim that
// runs out or a day that ends changes a number with nothing written.

import { Refusal, call } from './api.js';
import { hrefTo, ui } from './ctx.js';
import { fromDay, todayDay } from './dates.js';
import { clear, h, plural, put } from './dom.js';
import { KIND, STATUS, STATUSES } from './labels.js';
import { ensure, epicOf, epics, store } from './store.js';

// What "ready work nobody holds" means: the filter that gives exactly the tool's answer.
const FREE = 'kind:story,task status:ready -claimed';

let root = null;
let generation = 0;
let timer = null;
let numbers = null; // what was read last

// -- the page -------------------------------------------------------------------------------------

export function mount(element) {
  root = element;
  numbers = null;
  put(
    root,
    h('h1', {}, 'Home'),
    h('p', { class: 'help' }, 'What needs attention. Each number is a link to the page that shows exactly those items.'),
    h('p', { class: 'problem', role: 'alert', id: 'home-problem' }),
    h('div', { id: 'home-body' }, h('p', { class: 'quiet' }, 'Reading the numbers…')),
  );
  load();
  timer = setInterval(() => load(), Math.max(10, store.info.poll_seconds * 3) * 1000);
}

export function unmount() {
  generation += 1;
  root = null;
  clearInterval(timer);
  timer = null;
}

export function update() {}

export function refresh() {
  if (root) load();
}

export function index() {
  if (root && numbers) draw();
}

// -- reading ------------------------------------------------------------------------------------------

// How many items answer a filter, as list_nodes counts them.
const total = async (query) => (await call('list_nodes', { query, limit: 1 })).total;

async function load() {
  const mine = ++generation;
  try {
    const [free, blocked, overdue, lapsed, coverage, health] = await Promise.all([
      total(FREE),
      total('status:blocked'),
      total('is:overdue'),
      total('is:lapsed'),
      call('coverage_report', { show: 'both', limit: 1 }),
      call('server_status', {}).then(
        (status) => ({ status }),
        (error) => {
          if (!(error instanceof Refusal)) throw error;
          return { problem: error.message };
        },
      ),
      ensure('decisions'),
      ensure('memories'),
      ensure('overlaps'),
    ]);
    if (mine !== generation || !root) return;
    numbers = { free, blocked, overdue, lapsed, coverage, health };
    root.querySelector('#home-problem').textContent = '';
    draw();
  } catch (error) {
    if (mine !== generation || !root) return;
    if (!(error instanceof Refusal)) throw error;
    root.querySelector('#home-problem').textContent = error.message;
  }
}

// -- drawing ------------------------------------------------------------------------------------------

// The first day of "this week": today and the six days before it, by the UTC day the database uses.
const weekStart = () => fromDay(todayDay() - 6);

function tile(key, label, value, href, note = null) {
  return h(
    'li',
    {},
    h(
      'a',
      { class: `tile${value === 0 ? ' none' : ''}`, href, dataset: { key, value: value === null ? '' : String(value) } },
      h('span', { class: 'number' }, value === null ? '–' : String(value)),
      h('span', { class: 'label' }, label),
      note ? h('span', { class: 'quiet note' }, note) : null,
    ),
  );
}

function group(title, ...tiles) {
  return h('section', { class: 'tile-group' }, h('h2', {}, title), h('ul', { class: 'tiles plain' }, ...tiles));
}

function draw() {
  const { free, blocked, overdue, lapsed, coverage, health } = numbers;
  const memories = store.memories || [];
  const stale = memories.filter((item) => item.type === 'belief' && item.stale).length;
  const unconfirmed = memories.filter((item) => item.type === 'belief' && item.status === 'active' && !item.stale && !item.confirmed_by).length;
  const start = weekStart();
  const decisions = (store.decisions || []).filter((item) => item.kind === 'decision' && String(item.decided_on).slice(0, 10) >= start).length;
  const overlapsKnown = store.overlapNote === null;
  const pairs = overlapsKnown ? (store.overlaps || []).filter((pair) => pair.already_linked.length === 0).length : null;
  const waiting = store.index ? store.index.waiting_to_index : null;
  const indexNote = store.index && store.index.meaning !== 'used' ? (store.index.meaning === 'not_configured' ? 'No embedding service is set up.' : 'The index is not built yet.') : null;

  put(
    root.querySelector('#home-body'),
    group(
      'Work',
      tile('free', 'Ready work that nobody holds', free, hrefTo('table', { q: FREE })),
      tile('blocked', 'Blocked', blocked, hrefTo('table', { q: 'status:blocked' })),
      tile('overdue', 'Overdue', overdue, hrefTo('table', { q: 'is:overdue' })),
      tile('lapsed', 'Claims that ran out with no handoff note', lapsed, hrefTo('table', { q: 'is:lapsed' })),
    ),
    group(
      'Rules',
      tile('unplaced', 'Rules in no story', coverage.unplaced_rule_count, hrefTo('rules')),
      tile('no-rules', 'Stories with no rule', coverage.story_without_rules_count, hrefTo('rules')),
    ),
    group(
      'Memory',
      tile('decisions', 'Decisions made in the last 7 days', decisions, hrefTo('memory', { kind: 'decision', from: start }), `since ${start}`),
      tile('stale', 'Stale beliefs', stale, hrefTo('memory', { kind: 'belief', status: 'stale' })),
      tile('unconfirmed', 'Beliefs waiting for a person to confirm them', unconfirmed, hrefTo('memory', { kind: 'belief', status: 'unconfirmed' })),
    ),
    group(
      'Finding and the server',
      tile('overlaps', 'Overlap candidates waiting for a look', pairs, hrefTo('overlaps', { unlinked: 1 }), overlapsKnown ? null : 'The index is not ready, so none can be found.'),
      tile('waiting', 'Items waiting to be indexed', waiting, hrefTo('status'), indexNote),
      healthTile(health),
    ),
    epicsSection(),
  );
}

function healthTile(health) {
  if (health.problem) {
    return h('li', {}, h('a', { class: 'tile bad', href: hrefTo('status'), dataset: { key: 'health', value: 'problem' } }, h('span', { class: 'number' }, 'Problem'), h('span', { class: 'label' }, 'Server health'), h('span', { class: 'note' }, health.problem)));
  }
  const { vrum_version: vrum, postgres_version: postgres, pgvector_version: vector } = health.status;
  return h(
    'li',
    {},
    h(
      'a',
      { class: 'tile good', href: hrefTo('status'), dataset: { key: 'health', value: 'ok' } },
      h('span', { class: 'number' }, 'Healthy'),
      h('span', { class: 'label' }, 'Server health'),
      h('span', { class: 'quiet note' }, `Vrum ${vrum}, PostgreSQL ${postgres.split(' ')[0]}, pgvector ${vector}`),
    ),
  );
}

// -- the epics ----------------------------------------------------------------------------------------

function countsOf(epic) {
  const counts = Object.fromEntries(STATUSES.map((status) => [status, 0]));
  for (const node of store.nodes.values()) {
    if (node.kind !== 'epic' && epicOf(node)?.id === epic.id) counts[node.status] += 1;
  }
  return counts;
}

function epicsSection() {
  const all = epics().sort((a, b) => a.id - b.id);
  if (!all.length) {
    return h('section', { id: 'epics' }, h('h2', {}, 'Epics'), h('p', { class: 'quiet' }, 'There are no epics yet. Make one on the Board page, or let an agent plan.'));
  }
  return h(
    'section',
    { id: 'epics' },
    h('h2', {}, 'Epics'),
    h('p', { class: 'help' }, 'The stories and tasks of each epic by status. A number links to the Table page filtered to exactly those.'),
    h('ul', { class: 'plain epic-list' }, all.map(epicRow)),
  );
}

function epicRow(epic) {
  const counts = countsOf(epic);
  const sum = STATUSES.reduce((all, status) => all + counts[status], 0);
  return h(
    'li',
    { class: 'epic-row', dataset: { epic: epic.id } },
    h('h3', {}, h('a', { href: hrefTo(ui.page, { card: epic.id }) }, `${KIND.epic.sign} #${epic.id} ${epic.title}`), h('span', { class: 'quiet' }, ` ${sum ? plural(sum, 'story or task', 'stories and tasks') : 'no stories or tasks yet'}`)),
    sum ? h('div', { class: 'bar', role: 'img', 'aria-label': STATUSES.map((status) => `${counts[status]} ${STATUS[status].label.toLowerCase()}`).join(', ') }, STATUSES.filter((status) => counts[status]).map((status) => h('span', { class: `seg status-${status}`, css: { width: `${(counts[status] / sum) * 100}%` } }))) : null,
    h(
      'ul',
      { class: 'status-counts plain' },
      STATUSES.map((status) =>
        h(
          'li',
          {},
          h(
            'a',
            { class: `status-link status-${status}${counts[status] ? '' : ' none'}`, href: hrefTo('table', { q: `epic:${epic.id} kind:story,task status:${status}` }), dataset: { epic: epic.id, status, value: counts[status] } },
            h('span', { class: 'number' }, String(counts[status])),
            h('span', { class: 'label' }, ` ${STATUS[status].label}`),
          ),
        ),
      ),
    ),
  );
}
