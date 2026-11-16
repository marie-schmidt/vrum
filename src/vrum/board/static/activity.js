// Activity: everything that happens, newest first, in plain words, and beside it who is working on
// what. The feed is the server's change feed (the newest changes first, and older ones when asked
// for), filtered by who, what kind of change, which item and when; the actor and the item are
// asked of the server and the kind and the days are worked out here on what was read. The right
// side is `who_is_working`: every claim in force with a countdown to its lease, the claims that ran
// out, each person's or agent's state, and buttons to take, renew or release work under the name
// the person typed, which are the same operations an agent calls.

import { Refusal, call } from './api.js';
import { actor, notify, ui } from './ctx.js';
import { clear, h, moment, plural, put } from './dom.js';
import { CHANGE_KINDS, aboutItem, eventRow, kindOfAction } from './events.js';
import { dialogForm, field, openDialog } from './forms.js';
import { hrefFor, loadTargets } from './references.js';
import { store, sync } from './store.js';

const PAGE = 100;
const WANT = 50; // rows to show before a filter on the kind or the days stops reading back
const MOST_PAGES = 40; // and the most pages it reads for one look, so it cannot run away
const DEFAULT_LEASE = 900;

let root = null;
let params = new URLSearchParams();
let parts = {};
let events = []; // what has been read, newest first
let older = null; // the event to read back from, or null when nothing older is left
let working = null; // the last answer of who_is_working
let free = []; // ready work nobody holds
let skew = 0; // the server's clock minus this one's, in milliseconds
let loadedFor = '';
let generation = 0;
let ticker = null;
let refetching = false;
let drawn = null; // what the side was last drawn from
let typed = { lease: String(DEFAULT_LEASE), id: '' }; // what is in its boxes, kept across a redraw

// -- the filters --------------------------------------------------------------------------------------

// An item as typed or in the address: 12, #12, node:12, rule 3, decision:7, fact 5, belief 2.
export function parseItem(text) {
  const found = /^(node|rule|decision|fact|belief)?\s*[:#]?\s*#?(\d+)$/i.exec(String(text || '').trim());
  return found ? { kind: (found[1] || 'node').toLowerCase(), id: Number(found[2]) } : null;
}

const ITEM_ARGUMENT = { node: 'node_id', rule: 'rule_id', decision: 'decision_id', fact: 'memory_id', belief: 'memory_id' };

function filters() {
  const text = (params.get('item') || '').trim();
  return {
    actor: (params.get('actor') || '').trim(),
    kind: params.get('kind') || '',
    text,
    item: parseItem(text),
    from: params.get('from') || '',
    to: params.get('to') || '',
  };
}

const serverKey = (f) => `${f.actor}|${f.text}`;

// What the server is asked: the actor and the one item to follow.
function serverArguments(f) {
  return { ...(f.actor ? { actor: f.actor } : {}), ...(f.item ? { [ITEM_ARGUMENT[f.item.kind]]: f.item.id } : {}) };
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
  params = initial;
  events = [];
  older = null;
  working = null;
  drawn = null;
  typed = { lease: String(DEFAULT_LEASE), id: '' };
  free = [];
  loadedFor = '';
  const control = (name, tag, props = {}, ...children) => h(tag, { id: `a-${name}`, name, ...props }, ...children);
  parts = {
    actor: control('actor', 'input', { type: 'text', list: 'a-who', autocomplete: 'off', placeholder: 'a name' }),
    kind: control('kind', 'select', {}, [['', 'Everything'], ...CHANGE_KINDS.map(([value, label]) => [value, label])].map(([value, label]) => h('option', { value }, label))),
    item: control('item', 'input', { type: 'text', autocomplete: 'off', placeholder: '#12, rule 3, decision 7, fact 5' }),
    from: control('from', 'input', { type: 'date' }),
    to: control('to', 'input', { type: 'date' }),
    who: h('datalist', { id: 'a-who' }),
    problem: h('p', { class: 'problem', role: 'alert', id: 'activity-problem' }),
    summary: h('p', { class: 'count', id: 'count', role: 'status' }),
    feed: h('div', { id: 'feed' }),
    side: h('aside', { id: 'working', 'aria-label': 'Who is working on what' }),
  };
  const label = (name, text) => h('label', { for: `a-${name}` }, text);
  const form = h(
    'form',
    {
      class: 'filter activity-filter',
      id: 'activity-filter',
      role: 'search',
      onsubmit: (event) => {
        event.preventDefault();
        ui.navigate({ actor: parts.actor.value.trim() || null, kind: parts.kind.value || null, item: parts.item.value.trim() || null, from: parts.from.value || null, to: parts.to.value || null });
      },
    },
    label('actor', 'Who'),
    parts.actor,
    parts.who,
    label('kind', 'Kind of change'),
    parts.kind,
    label('item', 'About'),
    parts.item,
    label('from', 'From day'),
    parts.from,
    label('to', 'To day'),
    parts.to,
    h('button', { type: 'submit', class: 'primary', id: 'apply-activity' }, 'Apply'),
    h('button', { type: 'button', id: 'clear-activity', onclick: () => ui.navigate({ actor: null, kind: null, item: null, from: null, to: null }) }, 'Clear'),
    h('span', { class: 'quiet' }, 'Days are UTC days.'),
  );
  put(
    root,
    h('h1', {}, 'Activity'),
    form,
    parts.problem,
    h('div', { class: 'activity-layout' }, h('section', { id: 'feed-section', 'aria-label': 'Changes, newest first' }, parts.summary, parts.feed), parts.side),
  );
  fillForm();
  await loadTargets();
  if (!root) return;
  loadFeed(true);
  loadWorking();
  ticker = setInterval(tick, 1000);
}

export function unmount() {
  generation += 1;
  root = null;
  clearInterval(ticker);
  ticker = null;
}

export function update(next) {
  params = next;
  if (!root) return;
  fillForm();
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
  parts.actor.value = params.get('actor') || '';
  parts.kind.value = params.get('kind') || '';
  parts.item.value = params.get('item') || '';
  parts.from.value = params.get('from') || '';
  parts.to.value = params.get('to') || '';
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
    events.push(...[...page.items].reverse());
    older = page.next_before_id;
    pages += 1;
  }
}

async function loadFeed(fresh) {
  const mine = ++generation;
  const f = filters();
  loadedFor = serverKey(f);
  if (fresh) parts.summary.textContent = 'Reading the changes…';
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

async function showOlder() {
  const before = events.length;
  const f = filters();
  const shown = events.filter((event) => keeps(event, f)).length;
  try {
    await fill(shown + 25);
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    parts.problem.textContent = error.message;
  }
  if (root && events.length === before) older = null;
  if (root) drawFeed();
}

function updateWho() {
  const known = new Set([...(working ? working.agents.map((agent) => agent.actor) : []), ...events.map((event) => event.actor)]);
  put(parts.who, [...known].sort().map((name) => h('option', { value: name })));
}

function drawFeed() {
  const f = filters();
  const rows = events.filter((event) => keeps(event, f));
  updateWho();
  parts.summary.textContent = rows.length
    ? `${plural(rows.length, 'change')}, newest first${older !== null ? '. Older changes have not been read yet' : ''}.`
    : events.length || older !== null
      ? `Nothing read so far matches these filters${older !== null ? ', and there are older changes to look through' : ''}.`
      : 'Nothing has happened yet.';
  put(
    parts.feed,
    rows.length ? h('ul', { class: 'plain feed' }, rows.map(eventRow)) : null,
    older !== null ? h('p', {}, h('button', { type: 'button', id: 'show-older', onclick: showOlder }, 'Show older changes')) : null,
  );
}

// -- who is working on what ---------------------------------------------------------------------------

async function loadWorking() {
  try {
    const [answer, ready] = await Promise.all([call('who_is_working', {}), call('ready_work', { limit: 10 })]);
    if (!root) return;
    working = answer;
    free = ready.items;
    skew = Date.parse(answer.as_of) - Date.now();
    // Drawn again only when something in it changed, so that what a person is typing in it stays.
    const seen = JSON.stringify([answer.agents, ready.items.map((node) => node.id), actor()]);
    if (seen !== drawn) {
      drawn = seen;
      drawWorking();
    }
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    if (root) put(parts.side, h('h2', {}, 'Who is working on what'), h('p', { class: 'problem', role: 'alert' }, error.message));
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

const STATE = { working: 'Working', lapsed: 'Lapsed', finished: 'Finished, with a handoff note' };

function nodeLink(work) {
  return h('a', { class: 'item-link', href: hrefFor('node', work.node_id), dataset: { id: work.node_id } }, `${work.kind} #${work.node_id} “${work.title}”`);
}

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

function releaseDialog(work, holder) {
  const note = h('textarea', { id: 'release-note', name: 'note', rows: 4 });
  const { form } = dialogForm({
    fields: [h('p', {}, `Release ${work.kind} #${work.node_id} “${work.title}” as ${actor()}. The note is what whoever takes it next will read. ${holder !== actor() ? `It is held by ${holder}, so only ${holder} can release it.` : ''}`), field('Handoff note', note, 'What was done, how far it got, and what is left.')],
    submit: 'Release it',
    work: async () => {
      await call('release_claim', { actor: actor(), node_id: work.node_id, note: note.value });
      form.closest('dialog')?.close();
      notify(`Released #${work.node_id}.`);
      await sync();
      await loadWorking();
    },
  });
  openDialog('Release a claim', [form]);
  note.focus();
}

function claimRow(agent, work, live) {
  return h(
    'li',
    { class: `claim${live ? '' : ' lapsed'}`, dataset: { node: work.node_id, actor: agent.actor, live: live ? '1' : '0' } },
    nodeLink(work),
    ' ',
    h('span', { class: 'holder' }, `held by ${agent.actor}`),
    ' ',
    countdown(work.expires_at, live),
    h(
      'span',
      { class: 'buttons' },
      live ? h('button', { type: 'button', class: 'renew', onclick: () => attempt(() => call('renew_claim', { actor: actor(), node_id: work.node_id }), `Kept #${work.node_id} for longer.`) }, 'Renew') : null,
      h('button', { type: 'button', class: 'release', onclick: () => releaseDialog(work, agent.actor) }, 'Release…'),
    ),
  );
}

function drawWorking() {
  if (!root || !working) return;
  const live = working.agents.flatMap((agent) => agent.holding.map((work) => [agent, work]));
  const lapsed = working.agents.flatMap((agent) => agent.lapsed.map((work) => [agent, work]));
  const lease = h('input', { type: 'number', id: 'lease', min: 1, max: 86400, step: 1, value: typed.lease, 'aria-describedby': 'lease-help', oninput: () => { typed.lease = lease.value; } });
  const claimId = h('input', { type: 'text', id: 'claim-id', autocomplete: 'off', placeholder: '#12', value: typed.id, oninput: () => { typed.id = claimId.value; } });
  put(
    parts.side,
    h('h2', {}, 'Who is working on what'),
    h('p', { class: 'quiet', id: 'acting-as' }, actor() ? `Buttons act as ${actor()}.` : 'Give your name first (top right): the buttons act under it.'),
    h('section', { id: 'claims-live' }, h('h3', {}, `Claims in force (${live.length})`), live.length ? h('ul', { class: 'plain claims' }, live.map(([agent, work]) => claimRow(agent, work, true))) : h('p', { class: 'quiet' }, 'Nobody holds anything right now.')),
    lapsed.length ? h('section', { id: 'claims-lapsed' }, h('h3', {}, `Claims that ran out (${lapsed.length})`), h('p', { class: 'quiet' }, 'These ran out with no handoff note. Anyone can take the work again; the holder can still release it with a note.'), h('ul', { class: 'plain claims' }, lapsed.map(([agent, work]) => claimRow(agent, work, false)))) : null,
    h(
      'section',
      { id: 'agents' },
      h('h3', {}, `People and agents (${working.agents.length})`),
      working.agents.length
        ? h('ul', { class: 'plain agents' }, working.agents.map(agentRow))
        : h('p', { class: 'quiet' }, 'Nobody has taken work yet.'),
    ),
    h(
      'section',
      { id: 'free' },
      h('h3', {}, `Free to take (${free.length}${free.length === 10 ? ' or more' : ''})`),
      h('p', { class: 'quiet', id: 'lease-help' }, 'Ready work that nobody holds, oldest first. A claim lasts this many seconds unless it is renewed:'),
      h('label', { for: 'lease' }, 'Lease in seconds'),
      lease,
      h(
        'form',
        {
          id: 'claim-by-id',
          class: 'claim-by-id',
          onsubmit: (event) => {
            event.preventDefault();
            const text = claimId.value.trim().replace(/^#/, '');
            const id = /^\d+$/.test(text) ? Number(text) : text;
            attempt(() => call('claim_node', { actor: actor(), node_id: id, lease_seconds: Number(lease.value) }), `Took #${text}.`);
          },
        },
        h('label', { for: 'claim-id' }, 'Or take an item by its id'),
        claimId,
        h('button', { type: 'submit', id: 'claim-by-id-go' }, 'Claim'),
      ),
      free.length
        ? h(
            'ul',
            { class: 'plain claims free' },
            free.map((node) =>
              h(
                'li',
                { class: 'claim', dataset: { node: node.id } },
                h('a', { class: 'item-link', href: hrefFor('node', node.id) }, `${node.kind} #${node.id} “${node.title}”`),
                h('button', { type: 'button', class: 'take', onclick: () => attempt(() => call('claim_node', { actor: actor(), node_id: node.id, lease_seconds: Number(lease.value) }), `Took #${node.id}.`) }, 'Claim'),
              ),
            ),
          )
        : h('p', { class: 'quiet' }, 'No ready work is free.'),
    ),
  );
  updateWho();
}

function agentRow(agent) {
  const note = agent.last_handoff;
  return h(
    'li',
    { class: `agent state-${agent.state}`, dataset: { actor: agent.actor, state: agent.state } },
    h('strong', {}, agent.actor),
    ' ',
    h('span', { class: `chip state state-${agent.state}` }, STATE[agent.state]),
    agent.state === 'working' ? h('span', { class: 'quiet' }, ` on ${agent.holding.map((work) => `#${work.node_id}`).join(', ')}`) : null,
    agent.state === 'lapsed' ? h('span', { class: 'quiet' }, ` on ${agent.lapsed.map((work) => `#${work.node_id}`).join(', ')}`) : null,
    note ? h('div', { class: 'quiet' }, `Last handoff: ${note.outcome}${note.node_id ? ` on #${note.node_id}` : ''}, ${moment(note.at)}.`) : null,
  );
}
