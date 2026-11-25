// The Around view of the Graph page: one item in the middle of a small labelled picture, and what is
// joined to it in groups around it (part of, contains, needs first, blocks, acceptance criteria, notes,
// project-wide notes, earlier notes kept for the reason, and what is related or alike), each line of
// the picture saying why it is here. It is the graph made for the question "what do we know about
// this?", and what it shows is what the `recall` tool answers for the same item, so a person and an
// agent read the same things. The picture has the whole width of the page. A neighbour that is work is
// looked around in its turn; an acceptance criterion or a note opens its page, where its details and
// the Agent view are.
//
// The picture fills the page down to the bottom of the window (graph-explore.css) and fits it without
// the page scrolling. On a tab wide enough for the 3 x 3 figure the cards are one line per entry and a
// group shows three entries (two, then one, when the window is too short for that), and the figure is
// then scaled uniformly, never above 1 and never so far that its smallest text is under 11 px. What
// room is left over is shared between the rows, so the picture reaches the bottom of its panel. The
// lines are drawn from the scaled positions. "Show N more" makes that group grow where it is, to all
// of its entries; the figure is laid out again and fitted again, so it is scaled down (as far as
// OPEN_SMALLEST) to keep everything in view and back up when a group is closed. Past that the picture
// scrolls inside its panel. Fit, + and - read it closely (the picture is panned by dragging or
// scrolling when it is larger than its panel); the zoom lives in this module while the page is open.
// A narrow tab keeps one column of groups that grows and scrolls with the page.
//
// It follows the view contract of the Graph page (graph.js): mount(element, api), update(api),
// refresh(events) and unmount(). Its choices are in the address: `reach` (1 or 2 steps) and `wide=1`
// (the project-wide notes shown).

import { Refusal } from './api.js';
import { can } from './ctx.js';
import { lateDaysOf } from './dates.js';
import { h, plural, put, s } from './dom.js';
import { GROUPS, attention, clip, ended, epicSummaries, fetchAround, groupsFor, isEmptyGroup, isEnded, isUnconfirmed, nameOf, named, progressOf, resolve, upTitle, waitingFor } from './graph-model.js';
import { keyChip, viewKey } from './graph-key.js';
import { icon } from './icons.js';
import { REQUIREMENT_STATUS, STATUS, actorLabel, decidedWords, dueChip, kindIcon, kindLabel, noteStatusWords, plainNoteStatus, plainRequirementStatus, priorityChip, statusChip, statusIcon } from './labels.js';
import { plainText } from './markdown.js';

function vars(element, values) {
  for (const [name, value] of Object.entries(values)) element.style.setProperty(name, String(value));
  return element;
}
const codeSpan = (text) => h('span', { class: 'graph-code' }, text);
const chip = (className, ...children) => h('span', { class: `chip ${className}`.trim() }, ...children);
function standingChips(item) {
  const record = item.record;
  if (item.work) return [statusChip(record.status), dueChip(record, { size: 14, days: true })];
  if (item.kind === 'rule') return [plainRequirementStatus(record.status)];
  const shown = plainNoteStatus(record.status);
  if (record.status === 'unconfirmed') shown.classList.add('unconfirmed');
  if (record.status === 'superseded' || record.status === 'withdrawn') shown.classList.add('ended');
  if (record.status === 'superseded' && record.superseded_by_id) shown.title = `Replaced by ${nameOf(`note:${record.superseded_by_id}`)}`;
  return [shown];
}
import { restOf } from './store.js';

export const KEYS = ['reach', 'wide'];

let root = null;
let api = null;
let fetched = null; // { ref, answer }: what was read for the item on show
let problem = '';
let reading = 0;
let expanded = {}; // which "Show more" lists are open
let figure = null; // the picture on show: its elements and how it was fitted
let zoom = 1; // how much closer than the fit the person has gone (1 is the fit)
let easeFrom = null; // { ref, scale }: where the picture was when a group was opened or closed
let sizeWatcher = null;
let sizeFrame = 0;

// The figure is fitted when the tab is at least this wide; below it the groups are one column.
const NARROW = 720;
// Text is 12 px at its smallest, so 0.92 is the least a figure is scaled to (11 px) while every group
// is closed. With groups open the person has asked for more than fits at that size and the figure is
// scaled down to OPEN_SMALLEST; past that it scrolls inside its panel.
const SMALLEST = 0.92;
const OPEN_SMALLEST = 0.45;
// The figures to try, from the roomiest to the tightest: how many entries a group shows before "Show N
// more", and whether the words of an entry may run to two lines.
const TIERS = [
  { limit: 3, wrap: true },
  { limit: 3, wrap: false },
  { limit: 2, wrap: false },
  { limit: 1, wrap: false },
];
const ZOOM_MOST = 3;
const ZOOM_STEP = 1.25;

const SLOT = { nw: [1, 'top'], n: [2, 'top'], ne: [3, 'top'], w: [1, 'mid'], e: [3, 'mid'], sw: [1, 'bot'], s: [2, 'bot'], se: [3, 'bot'] };

const reach = () => (api.params.get('reach') === '2' ? 2 : 1);
const wideOpen = () => api.params.get('wide') === '1';
const set = (changes) => api.setParams(changes, { replace: true });
// The item a neighbour was reached through, by its code and title, cut with an ellipsis when it is long.
const via = (entry) => {
  const through = entry.via ? resolve(entry.via) : null;
  return through ? clip(named(through), 52) : null;
};

// -- the page's life -------------------------------------------------------------------------------

export function mount(element, next) {
  root = element;
  api = next;
  fetched = null;
  problem = '';
  expanded = {};
  figure = null;
  easeFrom = null;
  zoom = 1;
  put(root, h('div', { class: 'graph-around-host', id: 'graph-around' }));
  if (typeof ResizeObserver !== 'undefined') sizeWatcher = new ResizeObserver(sizeChanged);
  window.addEventListener('resize', sizeChanged);
  load();
}

export function update(next) {
  const before = api;
  api = next;
  if (!root) return;
  if (before.focus !== next.focus) {
    zoom = 1;
    expanded = {};
    easeFrom = null;
    load();
  } else draw();
}

export function refresh() {
  if (root) load({ quietly: true });
}

export function unmount() {
  reading += 1;
  sizeWatcher?.disconnect();
  sizeWatcher = null;
  window.removeEventListener('resize', sizeChanged);
  cancelAnimationFrame(sizeFrame);
  sizeFrame = 0;
  root = null;
  api = null;
  figure = null;
}

// The figure is fitted again when the room its picture has is not what it was fitted to (fitting does
// not change that room: the picture is the part of the panel that takes what the rest leaves).
function sizeChanged() {
  if (!root || sizeFrame) return;
  sizeFrame = requestAnimationFrame(() => {
    sizeFrame = 0;
    const f = figure;
    if (!root || !f) return;
    const moved = root.clientWidth !== f.width || (!f.stacked && (f.view.clientWidth !== f.W || f.view.clientHeight !== f.room));
    if (moved) draw();
  });
}

// Read what recall and the index say about the item on show, then draw it.
async function load({ quietly = false } = {}) {
  const mine = ++reading;
  const item = api.focus ? resolve(api.focus) : null;
  if (!item) {
    fetched = null;
    problem = '';
    draw();
    return;
  }
  if (!quietly || fetched?.ref !== item.ref) {
    fetched = null;
    problem = '';
  }
  draw();
  try {
    const answer = await fetchAround(item);
    if (mine !== reading || !root) return;
    fetched = { ref: item.ref, answer };
    problem = '';
  } catch (error) {
    if (mine !== reading || !root) return;
    if (!(error instanceof Refusal)) throw error;
    problem = error.message;
  }
  draw();
}

// What had the keyboard focus inside the view, found again after the view is drawn again.
function keepFocus(make) {
  const marked = document.activeElement?.closest?.('[data-fk]');
  const key = root.contains(marked) ? marked.dataset.fk : null;
  make();
  if (key) root.querySelector(`[data-fk="${CSS.escape(key)}"]`)?.focus({ preventScroll: true });
}

function draw() {
  if (!root) return;
  const host = root.querySelector('#graph-around');
  const scrolled = figure?.view.isConnected ? { left: figure.view.scrollLeft, top: figure.view.scrollTop } : null;
  keepFocus(() => {
    figure = null;
    const state = body();
    if (state) put(host, state);
    else paint(host, resolve(api.focus));
    if (scrolled && figure?.view.classList.contains('is-scroll')) {
      figure.view.scrollLeft = scrolled.left;
      figure.view.scrollTop = scrolled.top;
    }
    easeFrom = null;
  });
  if (sizeWatcher) {
    sizeWatcher.disconnect();
    sizeWatcher.observe(root);
    if (figure && !figure.stacked) sizeWatcher.observe(figure.view);
  }
}

// What is on the page when there is no picture to draw: the overview, a missing item, a problem, or
// the wait for what recall says.
function body() {
  if (!api.focus) return emptyState();
  const item = resolve(api.focus);
  if (!item) return h('section', { class: 'graph-panel graph-state', role: 'status' }, h('h2', {}, 'That item is not there'), h('p', { class: 'quiet' }, 'It may have been removed, or the address may be wrong. Find another in the box above, or clear it to look at the whole project.'));
  if (problem) {
    return h('section', { class: 'graph-panel graph-state', role: 'alert' }, h('h2', {}, 'Could not read what is around it'), h('p', { class: 'problem' }, problem), h('button', { type: 'button', class: 'small', id: 'graph-around-retry', onclick: () => load() }, 'Try again'));
  }
  if (!fetched || fetched.ref !== item.ref) return h('p', { class: 'loading', id: 'graph-around-loading', role: 'status' }, 'Looking around…');
  return null;
}

// The picture, drawn with as many entries per group as the window has room for: the roomiest figure
// that fits at no less than the smallest text, else the next tighter one. When groups are open, the
// figure is drawn again with them open at the same number of entries for the others, and fitted as far
// down as OPEN_SMALLEST.
function paint(host, item) {
  const G = groupsFor(item, reach(), fetched.answer);
  const show = (at, open) => {
    figure = makeFigure(item, G, TIERS[at], open);
    put(host, h('div', { class: 'graph-around', 'data-focus': item.ref }, figure.panel));
  };
  if (root.clientWidth < NARROW) {
    show(0, true);
    fit(figure, 0, true);
    return;
  }
  let at = 0;
  while (at < TIERS.length) {
    show(at, false);
    if (fit(figure, SMALLEST, at === TIERS.length - 1)) break;
    at += 1;
  }
  if (GROUPS.some((def) => isOpen(def.id, G))) {
    show(at, true);
    fit(figure, OPEN_SMALLEST, true);
  }
  ease(figure);
}

// A group opened or closed: the picture goes from the scale it had to the one it has now, in a short
// ease. (It is a new element each time it is drawn, so it is put back at the old scale first.)
function ease(f) {
  const from = easeFrom;
  easeFrom = null;
  const to = f.k * zoom;
  if (!from || from.ref !== f.item.ref || Math.abs(from.scale - to) < 0.005 || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  f.ego.style.setProperty('--k', String(from.scale));
  f.ego.getBoundingClientRect();
  f.ego.classList.add('is-easing');
  f.ego.addEventListener('transitionend', () => f.ego.classList.remove('is-easing'), { once: true });
  f.ego.style.setProperty('--k', String(to));
}

// Remember how big the picture is, for the ease that follows the next drawing.
function easeFromHere() {
  easeFrom = figure && !figure.stacked ? { ref: figure.item.ref, scale: figure.k * zoom } : null;
}

// -- with nothing chosen ---------------------------------------------------------------------------

// Around needs one thing to look around, so with none chosen it is an overview of choices: the epics
// as cards (how far along each is, what is under it, what is linked to it), then what needs
// attention. One click on a card looks around that item.
function emptyState() {
  const needing = attention();
  const summaries = epicSummaries();

  const epicCard = (summary) => {
    const { progress } = summary;
    const linked = summary.notes ? plural(summary.notes, 'note') : null;
    return h(
      'li',
      {},
      h(
        'button',
        { type: 'button', class: 'graph-epic-card', 'data-ref': summary.ref, 'aria-label': `${summary.code} ${summary.title}. ${plural(summary.stories, 'story', 'stories')}, ${plural(summary.tasks, 'task')}${summary.blocked ? `, ${summary.blocked} blocked` : ''}. Look around it.`, onclick: () => api.setFocus(summary.ref) },
        h('span', { class: 'ec-top' }, kindIcon('epic', 16), codeSpan(summary.code), summary.blocked ? chip('status-blocked', icon('blocked', { size: 14 }), `${summary.blocked} blocked`) : null),
        h('span', { class: 'ec-title' }, summary.title),
        progress
          ? h('span', { class: 'ec-progress' }, h('span', { class: 'meter', role: 'img', 'aria-label': `${progress.done} of ${progress.total} tasks done` }, h('span', { css: { width: `${Math.round((progress.done / progress.total) * 100)}%` } })), h('span', { class: 'quiet' }, `${progress.done} of ${plural(progress.total, 'task')} done`))
          : null,
        h('span', { class: 'ec-counts' }, `${plural(summary.stories, 'story', 'stories')} · ${plural(summary.tasks, 'task')}`),
        linked ? h('span', { class: 'ec-linked' }, icon('diamond', { size: 14 }), linked) : null,
      ),
    );
  };

  const attentionCard = (item) => {
    const late = item.work ? lateDaysOf(item.record) : 0;
    return h(
      'li',
      {},
      h(
        'button',
        { type: 'button', class: 'graph-attention-card', 'data-ref': item.ref, onclick: () => api.setFocus(item.ref) },
        h('span', { class: 'ac-top' }, statusIcon(item.record.status, 16), codeSpan(item.code), statusChip(item.record.status), late > 0 ? dueChip(item.record, { size: 14, days: true }) : null),
        h('span', { class: 'ac-title' }, item.title),
      ),
    );
  };

  return h(
    'section',
    { class: 'graph-overview', id: 'graph-around-empty', 'aria-labelledby': 'graph-empty-h' },
    h('div', { class: 'graph-panel graph-overview-head' }, h('div', { class: 'graph-overview-art', 'aria-hidden': 'true' }, icon('around', { size: 28 })), h('h2', { id: 'graph-empty-h' }, 'Choose something to look around')),
    summaries.length
      ? h('section', { class: 'graph-panel graph-overview-part', 'aria-labelledby': 'graph-overview-epics' }, h('h3', { class: 'graph-small-heading', id: 'graph-overview-epics' }, 'Epics'), h('ul', { class: 'graph-epic-cards plain' }, summaries.map(epicCard)))
      : h('p', { class: 'graph-panel quiet graph-overview-none' }, `There is no work in this project yet.${can('write') ? ' Add an epic with New item.' : ''}`),
    needing.length ? h('section', { class: 'graph-panel graph-overview-part', 'aria-labelledby': 'graph-overview-attention' }, h('h3', { class: 'graph-small-heading', id: 'graph-overview-attention' }, 'Needs attention'), h('ul', { class: 'graph-attention-cards plain' }, needing.map(attentionCard))) : null,
  );
}

// -- one neighbour, as a button ------------------------------------------------------------------

function nodeFor(entry) {
  const item = resolve(entry.ref);
  if (!item) return null;
  const through = via(entry);
  if (item.work) {
    const node = item.record;
    const late = lateDaysOf(node);
    return h(
      'button',
      {
        type: 'button',
        class: `graph-node n-work s-${node.status}${entry.via ? ' is-via' : ''}`,
        'data-ref': item.ref,
        'data-fk': `n:${item.ref}`,
        title: item.title,
        'aria-label': `${named(item)}. ${STATUS[node.status].label}${late > 0 ? `, overdue ${plural(late, 'day')}` : ''}. Press Enter to look around it.`,
        onclick: () => api.setFocus(item.ref),
      },
      h('span', { class: 'n-ico' }, statusIcon(node.status, 16)),
      h(
        'span',
        { class: 'n-body' },
        h('span', { class: 'n-line' }, codeSpan(item.code), entry.score ? h('span', { class: 'n-score' }, `alike ${entry.score.toFixed(2)}`) : null, late > 0 ? dueChip(node, { size: 12, days: true }) : null),
        h('span', { class: 'n-title' }, item.title),
        through ? h('span', { class: 'n-via' }, `via ${through}`) : null,
      ),
    );
  }
  const ended = isEnded(item);
  const unconfirmed = isUnconfirmed(item);
  const record = item.record;
  const standing = item.kind === 'rule' ? requirementWords(record.status) : ended ? noteStatusWords(record.status) : record.status === 'confirmed' ? `Confirmed${record.confirmed_by ? ` by ${record.confirmed_by}` : ''}` : 'Not confirmed yet';
  // A mark with no words of its own is named by its tooltip, and the button's label says it too.
  const mark = (className, name, words) => h('span', { class: `n-badge ${className}`, title: words, 'aria-hidden': 'true' }, icon(name, { size: 14 }));
  let flag;
  if (item.kind === 'rule') flag = record.status === 'dropped' ? h('span', { class: 'n-badge ended' }, 'Dropped') : record.status === 'met' ? mark('ok', 'req_met', 'Met') : null;
  else if (ended) flag = h('span', { class: 'n-badge ended' }, noteStatusWords(record.status));
  else if (unconfirmed) flag = mark('warn', 'unconfirmed', standing);
  else flag = mark('ok', 'confirmed', standing);
  return h(
    'button',
    {
      type: 'button',
      class: `graph-node n-${item.kind}${ended ? ' is-ended' : ''}${unconfirmed ? ' is-unconfirmed' : ''}${entry.via ? ' is-via' : ''}`,
      'data-ref': item.ref,
      'data-fk': `n:${item.ref}`,
      title: item.title,
      'aria-label': `${named(item)}. ${standing}. Press Enter to open it.`,
      onclick: () => api.open(item.ref),
    },
    h('span', { class: 'n-ico' }, kindIcon(item.kind, 16)),
    h(
      'span',
      { class: 'n-body' },
      h('span', { class: 'n-line' }, codeSpan(item.code), flag, item.kind === 'note' && record.project_wide ? mark('info', 'graph', 'Project-wide') : null),
      h('span', { class: 'n-title' }, clip(item.title, 78)),
      through ? h('span', { class: 'n-via' }, `via ${through}`) : null,
    ),
  );
}

const requirementWords = (status) => (REQUIREMENT_STATUS[status] || { label: status }).label;

// A group is open when its "Show N more" was pressed; the notes are also open while the project-wide
// ones are shown.
const isOpen = (id, G) => Boolean(expanded[id]) || (id === 'notes' && G.wide.length > 0 && wideOpen());

function closeGroup(id, G) {
  easeFromHere();
  expanded[id] = false;
  if (id === 'notes' && G.wide.length && wideOpen()) set({ wide: null });
  else draw();
}

// A group opens where it is, to all of its entries, and any number may be open at once.
function openGroup(id) {
  easeFromHere();
  expanded = { ...expanded, [id]: true };
  draw();
}

// The entries of a group: its first `limit` (all of them, when only one would be left out), then "Show N
// more", which opens the whole list. `open` is the whole list.
function entriesOf(id, entries, extra, limit, open, G) {
  const more = entries.length > limit + 1 || extra > 0;
  const shown = open || entries.length <= limit + 1 ? entries : entries.slice(0, limit);
  const list = h('ul', { class: 'graph-gbox-list plain' }, shown.map((entry) => h('li', {}, nodeFor(entry))));
  const toggle = more
    ? h('button', { type: 'button', class: 'graph-more small ghost', 'aria-expanded': String(open), 'data-fk': `more:${id}`, onclick: () => (open ? closeGroup(id, G) : openGroup(id)) }, open ? 'Show fewer' : `Show ${entries.length - shown.length + extra} more`)
    : null;
  return [entries.length ? list : null, toggle];
}

function wideRow(G, open) {
  const words = `${plural(G.wide.length, G.wideMoved ? 'other project-wide note' : 'project-wide note')} apply`;
  return [
    h(
      'button',
      { type: 'button', class: 'graph-node n-wide', id: 'graph-wide-toggle', title: `${words} to every item in the project`, 'aria-expanded': String(open), 'data-fk': 'wide', onclick: () => {
          easeFromHere();
          set({ wide: open ? null : '1' });
        }, 'aria-label': `${words} here. ${open ? 'Hide them' : 'Show them'}.` },
      h('span', { class: 'n-ico' }, icon('graph', { size: 14 })),
      h('span', { class: 'n-body' }, h('span', { class: 'n-title strong' }, words)),
      h('span', { class: 'n-more' }, icon(open ? 'chevron_up' : 'chevron_down', { size: 14 }), open ? 'Hide' : 'Show'),
    ),
    open ? h('ul', { class: 'graph-gbox-list plain', 'aria-label': 'Project-wide notes' }, G.wide.map((entry) => h('li', {}, nodeFor(entry)))) : null,
  ];
}

const earlier = (entries) => (entries.length ? [h('p', { class: 'graph-gbox-sub' }, 'Earlier, kept for the reason'), h('ul', { class: 'graph-gbox-list plain' }, entries.map((entry) => h('li', {}, nodeFor(entry))))] : null);

// One group. Closed, it is `limit` entries and a line for the rest; `open` is the whole of it, which
// includes the earlier notes of the notes group.
function groupBox(def, G, item, limit, open) {
  let count;
  let content;
  if (def.id === 'notes') {
    count = G.notes.length + G.wide.length + G.past.length;
    content = [entriesOf('notes', G.notes, G.past.length, limit, open, G), G.wide.length ? wideRow(G, open && wideOpen()) : null, open ? earlier(G.past) : null];
  } else {
    count = G[def.id].length;
    content = entriesOf(def.id, G[def.id], 0, limit, open, G);
  }
  const title = def.id === 'up' ? upTitle(item.kind) : def.title;
  return h(
    'section',
    { class: `graph-gbox g-${def.id}${open ? ' is-open' : ''}`, 'aria-labelledby': `graph-gb-${def.id}`, 'data-group': def.id, onkeydown: open ? (event) => event.key === 'Escape' && (event.stopPropagation(), closeGroup(def.id, G)) : null },
    h('header', { class: 'graph-gbox-head' }, icon(def.icon, { size: 16 }), h('h3', { id: `graph-gb-${def.id}` }, title), h('span', { class: 'graph-gbox-count', 'aria-label': plural(count, 'item') }, count)),
    content,
  );
}

// -- the chosen item, in the middle -------------------------------------------------------------------

function hubCard(item) {
  const record = item.record;
  const kind = kindLabel(item.kind);
  const important = item.work && (record.priority === 'high' || record.priority === 'urgent');
  const common = [h('div', { class: 'hub-top' }, h('span', { class: 'kind-label', role: 'img', title: kind, 'aria-label': kind }, kindIcon(item.kind, 16)), codeSpan(item.code), important ? priorityChip(record.priority) : null, item.work ? h('span', { class: 'hub-state' }, statusChip(record.status)) : null)];
  let more;
  if (item.work) {
    const waits = waitingFor(record);
    const progress = progressOf(record);
    more = [
      h('h3', { class: 'hub-title', title: item.title }, item.title),
      h(
        'div',
        { class: 'hub-meta' },
        ended(record.status) ? null : dueChip(record, { size: 14, days: true }),
        record.claimed_by ? chip('held', 'Held by ', actorLabel(record.claimed_by, record.claimed_by_kind)) : null,
        waits.length ? chip('waiting', icon('clock', { size: 14 }), `Waiting for ${waits.slice(0, 2).map((ref) => clip(named(resolve(ref)), 32)).join(', ')}${waits.length > 2 ? '…' : ''}`) : null,
        progress && item.kind !== 'task'
          ? h('span', { class: 'hub-progress' }, h('span', { class: 'meter', role: 'img', 'aria-label': `${progress.done} of ${progress.total} tasks done` }, h('span', { css: { width: `${Math.round((progress.done / progress.total) * 100)}%` } })), h('span', { class: 'quiet' }, `${progress.done} of ${plural(progress.total, 'task')} done`))
          : null,
      ),
    ];
  } else if (item.kind === 'rule') {
    more = [h('h3', { class: 'hub-title' }, item.title), h('div', { class: 'hub-meta' }, standingChips(item), record.node ? null : chip('warn', icon('alert', { size: 14 }), 'Not placed')), record.body_preview || record.body ? h('p', { class: 'hub-text quiet' }, clip(plainText(record.body_preview || record.body), 220)) : null];
  } else {
    more = [
      h('h3', { class: 'hub-title' }, item.title),
      h('div', { class: 'hub-meta' }, standingChips(item), record.project_wide ? chip('info', icon('graph', { size: 14 }), 'Project-wide') : null),
      record.decided_by ? h('p', { class: 'hub-text quiet' }, `${decidedWords(record)}.`) : null,
      restOf(record) ? h('p', { class: 'hub-text' }, clip(plainText(restOf(record)), 240)) : null,
      h('p', { class: 'hub-text quiet' }, h('strong', {}, 'Why. '), clip(plainText(record.why), 200)),
    ];
  }
  const also = item.work
    ? [h('button', { type: 'button', class: 'small ghost', id: 'graph-hub-structure', onclick: () => api.setParams({ tab: 'structure' }) }, icon('structure', { size: 14 }), 'In Structure'), h('button', { type: 'button', class: 'small ghost', id: 'graph-hub-order', onclick: () => api.setParams({ tab: 'order' }) }, icon('order', { size: 14 }), 'In Order')]
    : [];
  return h(
    'article',
    { class: `graph-hub${item.work ? ` s-${record.status}` : ''}`, id: 'graph-hub', 'aria-label': `The chosen item: ${named(item)}`, 'data-ref': item.ref },
    common,
    more,
    h('div', { class: 'hub-actions' }, h('button', { type: 'button', class: 'small', id: 'graph-hub-open', onclick: () => api.open(item.ref) }, 'Open page', icon('arrow_right', { size: 14 })), also),
  );
}

// -- the lines: a different shape for each kind of relation, not only a different colour -------------------

const LEGEND = [
  { type: 'hier', label: 'Part of, contains' },
  { type: 'needs', label: 'Needs first (arrow shows the order)', arrow: true },
  { type: 'sim', label: 'Related, similar, duplicate' },
  { type: 'ctx', end: 'square', label: 'Acceptance criterion' },
  { type: 'ctx', end: 'diamond', label: 'Note' },
];

function endShape(kind, x, y) {
  if (kind === 'square') return s('rect', { class: 'mk mk-square', x: x - 3.5, y: y - 3.5, width: 7, height: 7, rx: 1 });
  return s('path', { class: 'mk mk-diamond', d: `M${x} ${y - 4.5} L${x + 4.5} ${y} L${x} ${y + 4.5} L${x - 4.5} ${y} z` });
}

function legendSample(entry) {
  const svg = s('svg', { class: 'graph-legend-sample', width: 44, height: 14, viewBox: '0 0 44 14', 'aria-hidden': 'true' });
  svg.append(s('line', { class: `spoke spoke-${entry.type}`, x1: 2, y1: 7, x2: 36, y2: 7 }));
  if (entry.arrow) svg.append(s('path', { class: 'mk mk-arrow', d: 'M34 3.5 L42 7 L34 10.5 z' }));
  if (entry.end) svg.append(endShape(entry.end, 38, 7));
  return svg;
}

// What the lines of the picture and the marks on a note mean, behind the view's Key button.
const aroundKey = () =>
  viewKey({
    label: 'How to read the picture',
    groups: [
      { label: 'Lines', chips: LEGEND.map((entry) => keyChip(legendSample(entry), entry.label)) },
      {
        label: 'Notes',
        chips: [
          keyChip(h('span', { class: 'n-badge ok' }, icon('confirmed', { size: 14 })), 'Confirmed'),
          keyChip(h('span', { class: 'n-badge warn' }, icon('unconfirmed', { size: 14 })), 'Not confirmed yet, dashed outline'),
          keyChip(h('span', { class: 'n-badge info' }, icon('graph', { size: 14 })), 'Project-wide'),
        ],
      },
    ],
  });

function lineDefs() {
  const marker = (id, size, extra, ...kids) => s('marker', { id, viewBox: '0 0 10 10', markerWidth: size, markerHeight: size, orient: 'auto', markerUnits: 'userSpaceOnUse', ...extra }, ...kids);
  return s(
    'defs',
    {},
    marker('graph-arrow', 12, { refX: 9, refY: 5 }, s('path', { class: 'mk mk-arrow', d: 'M0 1 L10 5 L0 9 z' })),
    marker('graph-end-square', 8, { refX: 8, refY: 5 }, s('rect', { class: 'mk mk-square', x: 1, y: 1, width: 8, height: 8, rx: 1 })),
    marker('graph-end-diamond', 10, { refX: 8, refY: 5 }, s('path', { class: 'mk mk-diamond', d: 'M5 0 L10 5 L5 10 L0 5 z' })),
  );
}

const round = (number) => Math.round(number * 10) / 10;

// The lines are drawn from where the groups are in the figure's own layout (before it is scaled), from
// the hub to the edge of each group that is there.
function drawSpokes({ ego, svg, hub, plan }) {
  const width = ego.offsetWidth;
  const height = ego.offsetHeight;
  svg.setAttribute('width', width);
  svg.setAttribute('height', height);
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  const rel = (element) => {
    const l = element.offsetLeft;
    const t = element.offsetTop;
    const r = l + element.offsetWidth;
    const b = t + element.offsetHeight;
    return { l, t, r, b, cx: (l + r) / 2, cy: (t + b) / 2 };
  };
  const H = rel(hub);
  const lines = s('g');
  for (const { slot, element, def } of plan) {
    const B = rel(element);
    const ends = {
      n: [[H.cx, H.t], [H.cx, B.b]],
      s: [[H.cx, H.b], [H.cx, B.t]],
      w: [[H.l, H.cy], [B.r, H.cy]],
      e: [[H.r, H.cy], [B.l, H.cy]],
      nw: [[H.l, H.t], [B.r, B.b]],
      ne: [[H.r, H.t], [B.l, B.b]],
      sw: [[H.l, H.b], [B.r, B.t]],
      se: [[H.r, H.b], [B.l, B.t]],
    }[slot];
    const [a, b] = ends;
    let [from, to] = [a, b];
    let end = null;
    if (def.line === 'needs') {
      end = 'url(#graph-arrow)';
      if (def.arrow === 'in') [from, to] = [b, a];
    } else if (def.line === 'ctx') end = `url(#graph-end-${def.end})`;
    lines.append(s('path', { class: `spoke spoke-${def.line} spoke-${def.id}`, d: `M${round(from[0])} ${round(from[1])} L${round(to[0])} ${round(to[1])}`, 'marker-end': end }));
    lines.append(s('circle', { class: 'port', cx: round(a[0]), cy: round(a[1]), r: 3.5 }));
  }
  put(svg, lineDefs(), lines);
}

function reachControl() {
  return h(
    'div',
    { class: 'graph-reach', role: 'group', 'aria-label': 'How far to look' },
    h('span', { class: 'graph-reach-label' }, 'Reach'),
    h('div', { class: 'segmented' }, [1, 2].map((steps) => h('button', { type: 'button', id: `graph-reach-${steps}`, 'aria-pressed': String(reach() === steps), 'data-fk': `reach:${steps}`, onclick: () => set({ reach: steps === 1 ? null : steps }) }, plural(steps, 'step')))),
  );
}

function zoomControl() {
  const small = (id, label, text, go) => h('button', { class: 'small icon-only', type: 'button', id, title: label, 'aria-label': label, 'data-fk': id, onclick: go }, text);
  const out = small('graph-ego-zoom-out', 'Zoom out', '\u2212', () => setZoom(zoom / ZOOM_STEP));
  const into = small('graph-ego-zoom-in', 'Zoom in', '+', () => setZoom(zoom * ZOOM_STEP));
  const fitButton = h('button', { class: 'small', type: 'button', id: 'graph-ego-fit', 'aria-label': 'Fit the whole picture in view', 'data-fk': 'graph-ego-fit', onclick: () => setZoom(1) }, 'Fit');
  return { out, into, element: h('div', { class: 'graph-ego-zoom', role: 'group', 'aria-label': 'Zoom' }, out, fitButton, into) };
}

// The figure with its parts: the chosen item in the middle of the grid, a box for each group that has
// something in it, and the lines behind them. `tier` says how many entries a group shows before its
// "Show N more" and whether an entry's words may run to two lines; `open` draws the groups whose "Show
// N more" was pressed in full, in their places.
function makeFigure(item, G, tier, open) {
  const { limit, wrap } = tier;
  const stacked = root.clientWidth < NARROW;
  const ego = h('div', { class: `graph-ego${wrap ? ' is-wrap' : ''}` });
  const svg = s('svg', { class: 'graph-ego-lines', 'aria-hidden': 'true', focusable: 'false' });
  const used = (id) => !isEmptyGroup(id, G);
  // A slot may have no group (there is none in the south east), and then nothing is in it.
  const slotUsed = (slot) => {
    const def = GROUPS.find((one) => one.slot === slot);
    return Boolean(def) && used(def.id);
  };
  const topUsed = ['nw', 'n', 'ne'].some(slotUsed);
  const botUsed = ['sw', 's', 'se'].some(slotUsed);
  const row = { top: 1, mid: topUsed ? 2 : 1, bot: (topUsed ? 2 : 1) + 1 };
  const hub = hubCard(item);
  vars(hub, { '--col': 2, '--row': row.mid });
  ego.append(svg, hub);
  const plan = [];
  const empty = [];
  for (const id of ['up', 'needs', 'blocks', 'reqs', 'sim', 'notes', 'down']) {
    const def = GROUPS.find((one) => one.id === id);
    if (!used(id)) {
      empty.push(id === 'up' && item.kind === 'epic' ? 'Part of (this is the top level)' : id === 'up' ? upTitle(item.kind) : def.title);
      continue;
    }
    const box = groupBox(def, G, item, limit, open && isOpen(id, G));
    const [col, band] = SLOT[def.slot];
    vars(box, { '--col': col, '--row': row[band] });
    box.dataset.slot = def.slot;
    ego.append(box);
    plan.push({ slot: def.slot, element: box, def });
  }
  if (!botUsed && !topUsed) ego.classList.add('ego-flat');
  const stage = h('div', { class: 'graph-ego-stage' }, ego);
  const view = h('div', { class: 'graph-ego-view' }, stage);
  const zoomer = stacked ? null : zoomControl();
  const checked = `Checked, nothing found: ${empty.join(' · ')}`;
  const panel = h(
    'section',
    { class: `graph-panel graph-ego-panel${stacked ? '' : ' is-fit'}`, 'aria-labelledby': 'graph-ego-h' },
    h(
      'div',
      { class: 'graph-panel-head' },
      h('h2', { id: 'graph-ego-h', class: 'sr-only' }, `Around ${named(item)}`),
      empty.length ? h('p', { class: 'graph-also', title: checked }, h('span', { class: 'quiet' }, 'Checked, nothing found: '), empty.join(' · ')) : null,
      h('div', { class: 'graph-panel-tools' }, item.work || item.kind === 'rule' ? reachControl() : null, aroundKey(), zoomer?.element),
    ),
    view,
    G.hints.length ? h('p', { class: 'graph-also quiet', id: 'graph-index-note' }, `Alike in meaning: ${G.hints[0]}`) : null,
  );
  if (!stacked) panning(view);
  return { item, stacked, panel, view, stage, ego, svg, hub, plan, zoomer, width: 0, k: 1 };
}

// Scale the figure to its panel. It is laid out as wide as the panel is at that scale, so a smaller
// figure is also a wider one and its height changes only a little; when the panel is taller than the
// figure, what is left over is shared between the rows. Answers whether to keep this figure: false when
// it would have to be smaller than `floor` and a tighter one is left to try (`last`: there is none,
// so it is kept at `floor` and scrolls inside its panel).
function fit(f, floor, last) {
  f.width = root.clientWidth;
  if (f.stacked) return true;
  const { view, ego } = f;
  const room = view.clientHeight;
  const lay = (scale, wide) => {
    ego.style.setProperty('--lw', `${wide / scale}px`);
    ego.style.setProperty('--fill', '0px');
    return ego.offsetHeight;
  };
  let W = view.clientWidth;
  for (let pass = 0; pass < 2; pass += 1) {
    let k = 1;
    let H = lay(k, W);
    if (H > room) {
      k = Math.max(room / H, 0.01);
      H = lay(k, W);
    }
    if (k < floor) {
      if (!last) return false;
      k = floor;
      H = lay(k, W);
    }
    if (H * k < room) {
      ego.style.setProperty('--fill', `${room / k}px`);
      H = ego.offsetHeight;
    }
    Object.assign(f, { k, W, H, room });
    sized(f);
    // A scroll bar that takes room from the panel (not on a system whose bars float) narrows it.
    if (view.clientWidth === W) break;
    W = view.clientWidth;
  }
  drawSpokes(f);
  return true;
}

// The size of the picture at the zoom it has: its stage, whether it is larger than its panel (then it
// scrolls), and the buttons.
function sized(f) {
  const { view, stage, ego, k, W, H, room } = f;
  const scale = k * zoom;
  stage.style.setProperty('--sw', `${W * zoom}px`);
  stage.style.setProperty('--sh', `${H * scale}px`);
  ego.style.setProperty('--k', String(scale));
  const scrolls = zoom > 1 || H * k > room + 1;
  view.classList.toggle('is-zoomed', zoom > 1);
  view.classList.toggle('is-scroll', scrolls);
  if (scrolls) {
    view.setAttribute('tabindex', '0');
    view.setAttribute('role', 'region');
    view.setAttribute('aria-label', 'The picture. It scrolls.');
  } else {
    view.removeAttribute('tabindex');
    view.removeAttribute('role');
    view.removeAttribute('aria-label');
  }
  f.zoomer.out.disabled = zoom <= 1;
  f.zoomer.into.disabled = zoom >= ZOOM_MOST;
}

// Go closer or back to the fit, keeping the middle of what is shown where it is.
function setZoom(next) {
  const f = figure;
  if (!f || f.stacked) return;
  const target = Math.min(ZOOM_MOST, Math.max(1, next));
  if (Math.abs(target - zoom) < 0.001) return;
  const { view } = f;
  const x = (view.scrollLeft + view.clientWidth / 2) / (f.W * zoom);
  const y = (view.scrollTop + view.clientHeight / 2) / (f.H * f.k * zoom);
  zoom = target;
  sized(f);
  view.scrollLeft = x * f.W * zoom - view.clientWidth / 2;
  view.scrollTop = y * f.H * f.k * zoom - view.clientHeight / 2;
}

// Dragging the empty part of a picture that scrolls moves it; a scroll or the arrow keys do too.
function panning(view) {
  let start = null;
  view.addEventListener('pointerdown', (event) => {
    if (!view.classList.contains('is-scroll') || event.button !== 0 || event.target.closest('button, a, input, select, textarea')) return;
    start = { x: event.clientX, y: event.clientY, left: view.scrollLeft, top: view.scrollTop };
    view.setPointerCapture(event.pointerId);
    view.classList.add('is-panning');
  });
  view.addEventListener('pointermove', (event) => {
    if (!start) return;
    view.scrollLeft = start.left - (event.clientX - start.x);
    view.scrollTop = start.top - (event.clientY - start.y);
  });
  const end = () => {
    start = null;
    view.classList.remove('is-panning');
  };
  view.addEventListener('pointerup', end);
  view.addEventListener('pointercancel', end);
}
