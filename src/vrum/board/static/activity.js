// The Activity page (#/activity): everything that happens, newest first, in plain words, and beside
// it who is working on what. The feed is the server's change feed (the newest changes first, and
// older ones when asked for), filtered by who, what kind of change, which item and when; the
// actor and the item are asked of the server and the kind and the days are worked out here on what
// was read. The claims are `who_is_working`: every claim in force with a countdown to its lease,
// the claims that ran out, each person's or agent's state, and buttons to take work as the person
// signed in and to renew or release the claims they hold, which are the same operations an agent
// calls. The filters
// are in the address, with the prefix act_ (act_actor, act_kind, act_item, act_from, act_to).

import { Refusal, call } from './api.js';
import { account, can, isMe, itemLink, notify, ui } from './ctx.js';
import { dateField } from './datepicker.js';
import { clear, h, moment, plural, put } from './dom.js';
import { CHANGE_KINDS, aboutItem, dayKey, dayLabel, eventRow, kindOfAction } from './events.js';
import { icon } from './icons.js';
import { actorLabel, codeOf, kindIcon, nodeRef } from './labels.js';
import { pageHeader } from './page.js';
import { releaseDialog } from './records.js';
import { ensureContext, sync } from './store.js';

const PAGE = 100;
const WANT = 50; // rows to show before a filter on the kind or the days stops reading back
const MOST_PAGES = 40; // and the most pages it reads for one look, so it cannot run away
const DEFAULT_LEASE = 900;
const FREE_PAGE = 10; // free work is read this many at a time, and "Show more" reads this many more
const MOST_FREE = 200; // the most one read can give
const FILTERS = ['act_actor', 'act_kind', 'act_item', 'act_from', 'act_to'];
// On a phone the claims fold away; on a wide window they sit beside the changes (see mount).
const NARROW = matchMedia('(max-width: 699.98px)');
const WIDE = matchMedia('(min-width: 1280px)');

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
let keepOpen = () => {}; // the claims cannot be folded on a wide window
let refetching = false;
let loadingOlder = false; // an older page is being read, so "Show older changes" waits
let drawn = null; // what the claims were last drawn from
let typed = { lease: String(DEFAULT_LEASE), code: '' }; // what is in its boxes, kept across a redraw

// -- the filters --------------------------------------------------------------------------------------

// An item as typed or in the address: S-12, story:12, story 12, criterion 3, rule:3, note:7,
// note 7. A piece of work comes back as { ref }, an acceptance criterion or a note as { kind, id },
// and anything else as null. A bare number names nothing, since each kind is numbered on its own.
const LETTERS = { e: 'epic', s: 'story', t: 'task' };

export function parseItem(text) {
  const words = String(text || '').trim().toLowerCase();
  const code = /^([est])-(\d+)$/.exec(words);
  if (code) return { ref: nodeRef(LETTERS[code[1]], Number(code[2])) };
  const named = /^(epic|story|task|rule|requirement|acceptance criterion|criterion|note)\s*:?\s*#?(\d+)$/.exec(words);
  if (!named) return null;
  const number = Number(named[2]);
  if (['epic', 'story', 'task'].includes(named[1])) return { ref: nodeRef(named[1], number) };
  return { kind: named[1] === 'note' ? 'note' : 'rule', id: number };
}

const ITEM_HELP = 'Name an item by its code or its kind and number, such as S-12, story 12, criterion 3 or note 7.';

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
  typed = { lease: String(DEFAULT_LEASE), code: '' };
  free = [];
  freeTotal = 0;
  freeLimit = FREE_PAGE;
  opened.clear();
  loadedFor = '';
  const control = (name, tag, props = {}, ...children) => h(tag, { id: `a-${name}`, name, ...props }, ...children);
  parts = {
    actor: control('actor', 'input', { type: 'text', list: 'a-who', autocomplete: 'off', placeholder: 'a name' }),
    kind: control('kind', 'select', {}, [['', 'Everything'], ...CHANGE_KINDS.map(([value, label]) => [value, label])].map(([value, label]) => h('option', { value }, label))),
    item: control('item', 'input', { type: 'text', autocomplete: 'off', placeholder: 'S-12, criterion 3, note 7' }),
    from: control('from', 'input'),
    to: control('to', 'input'),
    who: h('datalist', { id: 'a-who' }),
    problem: h('p', { class: 'problem', role: 'alert', id: 'activity-problem' }),
    itemProblem: h('p', { class: 'problem', role: 'alert', id: 'activity-item-problem' }),
    summary: h('p', { class: 'count', id: 'activity-count', role: 'status' }),
    feed: h('div', { id: 'feed' }),
    sideCount: h('span', { class: 'side-count quiet' }),
    sideBody: h('div', { class: 'working-body' }),
  };
  // On a phone the claims start folded away, so that the changes are not a screen further down; on
  // a wide window they sit beside the changes and cannot be folded.
  const fold = h('summary', {}, icon('chevron_right', { size: 16, className: 'fold-chevron' }), h('h2', {}, 'Who is working on what'), parts.sideCount);
  parts.side = h('details', { id: 'working', class: 'activity-side', open: !NARROW.matches }, fold, parts.sideBody);
  keepOpen = () => {
    if (WIDE.matches) parts.side.open = true;
    // Folding does nothing there, so the keyboard passes it by.
    fold.tabIndex = WIDE.matches ? -1 : 0;
  };
  parts.side.addEventListener('toggle', keepOpen);
  WIDE.addEventListener('change', keepOpen);
  keepOpen();
  const label = (name, text) => h('label', { for: `a-${name}` }, text);
  const labelled = (name, text, control, ...more) => h('div', { class: `field act-${name}` }, label(name, text), control, ...more);
  const form = h(
    'form',
    {
      class: 'filter-form activity-filter',
      id: 'activity-filter',
      role: 'search',
      onsubmit: (event) => {
        event.preventDefault();
        ui.navigate({
          act_actor: parts.actor.value.trim() || null,
          act_kind: parts.kind.value || null,
          act_item: parts.item.value.trim() || null,
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
      labelled('item', 'About', parts.item),
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
  put(parts.sideBody, h('p', { class: 'loading', role: 'status' }, 'Reading the claims…'));
  put(
    root,
    h(
      'div',
      { class: 'activity-page' },
      pageHeader('Activity'),
      h(
        'div',
        { class: 'activity-layout' },
        h('section', { id: 'feed-section', class: 'activity-feed', 'aria-label': 'Changes, newest first' }, h('h2', {}, 'Changes'), form, parts.problem, parts.itemProblem, parts.summary, parts.feed),
        parts.side,
      ),
    ),
  );
  fillForm();
  try {
    await ensureContext();
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
  }
  if (!root) return;
  loadFeed(true);
  loadWorking();
  ticker = setInterval(tick, 1000);
}

export function unmount() {
  generation += 1;
  life += 1;
  root = null;
  clearInterval(ticker);
  ticker = null;
  WIDE.removeEventListener('change', keepOpen);
}

export function update(next) {
  params = next;
  if (!root) return;
  // The boxes are filled again only when the filters in the address changed, so that what is
  // typed in them and not applied yet stays while the rest of the address changes.
  if (filterKey() !== shownFilters) fillForm();
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
  parts.item.value = params.get('act_item') || '';
  parts.from.value = params.get('act_from') || '';
  parts.to.value = params.get('act_to') || '';
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
  parts.itemProblem.textContent = f.text && !f.item ? ITEM_HELP : '';
  updateWho();
  parts.summary.textContent = `${plural(rows.length, 'change')}${older !== null ? '. Older changes have not been read yet' : ''}`;
  // The day is said once above the rows of it, so that each row needs only the time.
  const lines = [];
  let day = null;
  for (const event of rows) {
    if (dayKey(event.at) !== day) {
      day = dayKey(event.at);
      lines.push(h('li', { class: 'feed-day' }, h('h3', {}, dayLabel(event.at))));
    }
    lines.push(eventRow(event, { opened }));
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
    if (root) put(parts.sideBody, h('p', { class: 'problem', role: 'alert' }, error.message));
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

// A piece of work as a link to its page: "story S-12 “Log in”".
function workLink(ref, kind, title) {
  return itemLink(ref, { class: 'item-link', dataset: { ref } }, `${kind} ${codeOf(ref)} “${title}”`);
}

const nodeLink = (work) => workLink(work.node, work.kind, work.title);

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
    h('div', { class: 'claim-main' }, kindIcon(work.kind, 14), nodeLink(work)),
    h(
      'div',
      { class: 'claim-meta' },
      h('span', { class: 'holder' }, 'held by ', actorLabel(agent.actor, agent.kind, { size: 13 })),
      h('span', { class: 'claim-time' }, icon(live ? 'clock' : 'alert', { size: 13 }), countdown(work.expires_at, live)),
    ),
    mine && can('write')
      ? h(
          'span',
          { class: 'buttons' },
          live ? h('button', { type: 'button', class: 'renew small', onclick: () => attempt(() => call('renew_claim', { node: work.node }), `Kept ${codeOf(work.node)} for longer.`) }, 'Renew') : null,
          h('button', { type: 'button', class: 'release small', onclick: () => releaseDialog(work, loadWorking) }, 'Release…'),
        )
      : null,
  );
}

function drawWorking() {
  if (!root || !working) return;
  const writes = can('write');
  const live = working.agents.flatMap((agent) => agent.holding.map((work) => [agent, work]));
  const lapsed = working.agents.flatMap((agent) => agent.lapsed.map((work) => [agent, work]));
  const lease = h('input', { type: 'number', id: 'lease', min: 1, max: 86400, step: 1, value: typed.lease, oninput: () => { typed.lease = lease.value; } });
  const claimId = h('input', { type: 'text', id: 'claim-id', autocomplete: 'off', placeholder: 'S-12', value: typed.code, oninput: () => { typed.code = claimId.value; } });
  parts.sideCount.textContent = plural(live.length, 'claim');
  put(
    parts.sideBody,
    h('section', { id: 'claims-live' }, h('h3', {}, `Claims in force (${live.length})`), live.length ? h('ul', { class: 'plain claims' }, live.map(([agent, work]) => claimRow(agent, work, true))) : h('p', { class: 'quiet' }, 'No claims in force.')),
    lapsed.length ? h('section', { id: 'claims-lapsed' }, h('h3', {}, `Claims that ran out (${lapsed.length})`), h('ul', { class: 'plain claims' }, lapsed.map(([agent, work]) => claimRow(agent, work, false)))) : null,
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
      h('h3', {}, `Free to take (${freeTotal})`),
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
                  // A code or a ref is sent as the ref; anything else as typed, so that the server says what is wrong with it.
                  const ref = parseItem(text)?.ref ?? text;
                  attempt(() => call('claim_node', { node: ref, lease_seconds: Number(lease.value) }), `Took ${codeOf(ref)}.`);
                },
              },
              h('div', { class: 'field' }, h('label', { for: 'claim-id' }, 'Claim by code'), h('div', { class: 'claim-by-id-row' }, claimId, h('button', { type: 'submit', id: 'claim-by-id-go' }, 'Claim'))),
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
                h('div', { class: 'claim-main' }, kindIcon(node.kind, 14), workLink(node.ref, node.kind, node.title)),
                writes ? h('button', { type: 'button', class: 'take small', onclick: () => attempt(() => call('claim_node', { node: node.ref, lease_seconds: Number(lease.value) }), `Took ${codeOf(node.ref)}.`) }, 'Claim') : null,
              ),
            ),
          )
        : h('p', { class: 'quiet' }, 'Nothing is free to take.'),
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

function agentRow(agent) {
  const note = agent.last_handoff;
  return h(
    'li',
    { class: `agent state-${agent.state}`, dataset: { actor: agent.actor, actorKind: agent.kind || '', state: agent.state } },
    h('div', { class: 'agent-line' }, actorLabel(agent.actor, agent.kind), h('span', { class: `chip state state-${agent.state}` }, icon(STATE_ICONS[agent.state] || 'info', { size: 13 }), STATE[agent.state])),
    agent.state === 'working' ? h('span', { class: 'quiet' }, `On ${agent.holding.map((work) => codeOf(work.node)).join(', ')}`) : null,
    agent.state === 'lapsed' ? h('span', { class: 'quiet' }, `On ${agent.lapsed.map((work) => codeOf(work.node)).join(', ')}`) : null,
    note ? h('div', { class: 'quiet' }, `Last handoff: ${note.outcome}${note.node ? ` on ${codeOf(note.node)}` : ''}, ${moment(note.at)}.`) : null,
  );
}
