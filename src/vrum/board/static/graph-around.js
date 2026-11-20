// The Around view of the Graph page: one item in the middle of a small labelled picture, and what is
// joined to it in groups around it (part of, contains, needs first, blocks, acceptance criteria, notes,
// project-wide notes, earlier notes kept for the reason, and what is related or alike), each line of
// the picture saying why it is here. It is the graph made for the question "what do we know about
// this?", and what it shows is what the `recall` tool answers for the same item, so a person and an
// agent read the same things. The picture has the whole width of the page. A neighbour that is work is
// looked around in its turn; an acceptance criterion or a note opens its page, where its details and
// the Agent view are.
//
// It follows the view contract of the Graph page (graph.js): mount(element, api), update(api),
// refresh(events) and unmount(). Its choices are in the address: `reach` (1 or 2 steps) and `wide=1`
// (the project-wide notes shown).

import { Refusal } from './api.js';
import { can } from './ctx.js';
import { h, plural, put, s } from './dom.js';
import { GROUPS, attention, clip, epicSummaries, fetchAround, groupsFor, isEmptyGroup, isEnded, isUnconfirmed, lateDays, progressOf, resolve, upTitle, waitingFor } from './graph-model.js';
import { keyChip, viewKey } from './graph-key.js';
import { chip, codeSpan, dueChip, standingChips, vars } from './graph-parts.js';
import { icon } from './icons.js';
import { REQUIREMENT_STATUS, STATUS, actorLabel, decidedWords, kindIcon, kindLabel, noteStatusWords, priorityChip, statusChip, statusIcon } from './labels.js';
import { restOf } from './store.js';

export const KEYS = ['reach', 'wide'];

let root = null;
let api = null;
let fetched = null; // { ref, answer }: what was read for the item on show
let problem = '';
let reading = 0;
let expanded = {}; // which "Show more" lists are open
let watcher = null;

const SLOT = { nw: [1, 'top'], n: [2, 'top'], ne: [3, 'top'], w: [1, 'mid'], e: [3, 'mid'], sw: [1, 'bot'], s: [2, 'bot'], se: [3, 'bot'] };

const reach = () => (api.params.get('reach') === '2' ? 2 : 1);
const wideOpen = () => api.params.get('wide') === '1';
const set = (changes) => api.setParams(changes, { replace: true });
const via = (entry) => (entry.via ? resolve(entry.via)?.code : null);

// -- the page's life -------------------------------------------------------------------------------

export function mount(element, next) {
  root = element;
  api = next;
  fetched = null;
  problem = '';
  expanded = {};
  put(root, h('div', { class: 'graph-around-host', id: 'graph-around' }));
  load();
}

export function update(next) {
  const before = api;
  api = next;
  if (!root) return;
  if (before.focus !== next.focus) load();
  else draw();
}

export function refresh() {
  if (root) load({ quietly: true });
}

export function unmount() {
  reading += 1;
  watcher?.disconnect();
  watcher = null;
  root = null;
  api = null;
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
  keepFocus(() => {
    watcher?.disconnect();
    watcher = null;
    put(host, body());
  });
}

function body() {
  if (!api.focus) return emptyState();
  const item = resolve(api.focus);
  if (!item) return h('section', { class: 'graph-panel graph-state', role: 'status' }, h('h2', {}, 'That item is not there'), h('p', { class: 'quiet' }, 'It may have been removed, or the address may be wrong. Find another in the box above, or clear it to look at the whole project.'));
  if (problem) {
    return h('section', { class: 'graph-panel graph-state', role: 'alert' }, h('h2', {}, 'Could not read what is around it'), h('p', { class: 'problem' }, problem), h('button', { type: 'button', class: 'small', id: 'graph-around-retry', onclick: () => load() }, 'Try again'));
  }
  if (!fetched || fetched.ref !== item.ref) return h('p', { class: 'loading', id: 'graph-around-loading', role: 'status' }, 'Looking around…');
  const G = groupsFor(item, reach(), fetched.answer);
  return h('div', { class: 'graph-around', 'data-focus': item.ref }, egoPanel(item, G));
}

// -- with nothing chosen ---------------------------------------------------------------------------

// Around needs one thing to look around, so with none chosen it is an overview of choices: the epics
// as cards (how far along each is, what is under it, what is linked to it), then what needs
// attention. One click on a card looks around that item.
function emptyState() {
  const needing = attention();
  const summaries = epicSummaries();
  const lookAround = () => h('span', { class: 'ac-go' }, icon('around', { size: 14 }), 'Look around it');

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
          : h('span', { class: 'ec-progress quiet' }, 'No tasks yet'),
        h('span', { class: 'ec-counts' }, `${plural(summary.stories, 'story', 'stories')} · ${plural(summary.tasks, 'task')}`),
        h('span', { class: 'ec-linked' }, linked ? [icon('diamond', { size: 14 }), linked] : h('span', { class: 'quiet' }, 'No notes linked')),
        lookAround(),
      ),
    );
  };

  const attentionCard = (item) => {
    const late = item.work ? lateDays(item.record) : 0;
    return h(
      'li',
      {},
      h(
        'button',
        { type: 'button', class: 'graph-attention-card', 'data-ref': item.ref, onclick: () => api.setFocus(item.ref) },
        h('span', { class: 'ac-top' }, statusIcon(item.record.status, 16), codeSpan(item.code), statusChip(item.record.status), late > 0 ? chip('overdue', icon('clock', { size: 14 }), `Overdue ${plural(late, 'day')}`) : null),
        h('span', { class: 'ac-title' }, item.title),
        lookAround(),
      ),
    );
  };

  return h(
    'section',
    { class: 'graph-overview', id: 'graph-around-empty', 'aria-labelledby': 'graph-empty-h' },
    h('div', { class: 'graph-panel graph-overview-head' }, h('div', { class: 'graph-overview-art', 'aria-hidden': 'true' }, icon('around', { size: 28 })), h('div', {}, h('h2', { id: 'graph-empty-h' }, 'Choose something to look around'), h('p', { class: 'quiet' }, `${summaries.length ? 'Start from an epic below, or find' : 'Find'} anything in the box above.`))),
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
    const late = lateDays(node);
    return h(
      'button',
      {
        type: 'button',
        class: `graph-node n-work s-${node.status}${entry.via ? ' is-via' : ''}`,
        'data-ref': item.ref,
        'data-fk': `n:${item.ref}`,
        title: item.title,
        'aria-label': `${item.code} ${item.title}. ${STATUS[node.status].label}${late > 0 ? `, overdue ${plural(late, 'day')}` : ''}. Press Enter to look around it.`,
        onclick: () => api.setFocus(item.ref),
      },
      h('span', { class: 'n-ico' }, statusIcon(node.status, 16)),
      h('span', { class: 'n-body' }, h('span', { class: 'n-line' }, codeSpan(item.code), entry.score ? h('span', { class: 'n-score' }, `alike ${entry.score.toFixed(2)}`) : null, late > 0 ? h('span', { class: 'n-flag late' }, icon('clock', { size: 12 }), `${late} d late`) : null), h('span', { class: 'n-title' }, item.title), through ? h('span', { class: 'n-via' }, `via ${through}`) : null),
    );
  }
  const ended = isEnded(item);
  const unconfirmed = isUnconfirmed(item);
  const record = item.record;
  const standing = item.kind === 'rule' ? requirementWords(record.status) : ended ? noteStatusWords(record.status) : record.status === 'confirmed' ? `Confirmed${record.confirmed_by ? ` by ${record.confirmed_by}` : ''}` : 'Not confirmed yet';
  let flag;
  if (item.kind === 'rule') flag = record.status === 'dropped' ? h('span', { class: 'n-badge ended' }, 'Dropped') : record.status === 'met' ? h('span', { class: 'n-badge ok' }, icon('req_met', { size: 12 }), 'Met') : null;
  else if (ended) flag = h('span', { class: 'n-badge ended' }, noteStatusWords(record.status));
  else if (unconfirmed) flag = h('span', { class: 'n-badge warn' }, 'Unconfirmed');
  else flag = h('span', { class: 'n-badge ok' }, icon('confirmed', { size: 12 }), 'Confirmed');
  return h(
    'button',
    {
      type: 'button',
      class: `graph-node n-${item.kind}${ended ? ' is-ended' : ''}${unconfirmed ? ' is-unconfirmed' : ''}${entry.via ? ' is-via' : ''}`,
      'data-ref': item.ref,
      'data-fk': `n:${item.ref}`,
      title: item.title,
      'aria-label': `${kindLabel(item.kind)} ${record.id}. ${item.title}. ${standing}. Press Enter to open it.`,
      onclick: () => api.open(item.ref),
    },
    h('span', { class: 'n-ico' }, kindIcon(item.kind, 16)),
    h('span', { class: 'n-body' }, h('span', { class: 'n-line' }, codeSpan(item.code), item.kind === 'note' && record.project_wide ? h('span', { class: 'n-badge info' }, 'Project-wide') : null, flag), h('span', { class: 'n-title' }, clip(item.title, 78)), through ? h('span', { class: 'n-via' }, `via ${through}`) : null),
  );
}

const requirementWords = (status) => (REQUIREMENT_STATUS[status] || { label: status }).label;

function limited(id, entries, limit) {
  const open = Boolean(expanded[id]);
  const folded = entries.length > limit + 1 && !open;
  const list = h('ul', { class: `graph-gbox-list plain${open && entries.length > limit + 1 ? ' scroll' : ''}` }, (folded ? entries.slice(0, limit) : entries).map((entry) => h('li', {}, nodeFor(entry))));
  if (open && entries.length > limit + 1) {
    list.setAttribute('tabindex', '0');
    list.setAttribute('role', 'region');
    list.setAttribute('aria-label', 'All of them. It scrolls.');
  }
  const toggle = entries.length > limit + 1 ? h('button', { type: 'button', class: 'graph-more small ghost', 'aria-expanded': String(open), 'data-fk': `more:${id}`, onclick: () => { expanded[id] = !open; draw(); } }, open ? 'Show fewer' : `Show ${entries.length - limit} more`) : null;
  return [list, toggle];
}

function wideRow(G) {
  const open = wideOpen();
  const words = `${plural(G.wide.length, G.wideMoved ? 'other project-wide note' : 'project-wide note')} apply`;
  return [
    h(
      'button',
      { type: 'button', class: 'graph-node n-wide', id: 'graph-wide-toggle', 'aria-expanded': String(open), 'data-fk': 'wide', onclick: () => set({ wide: open ? null : '1' }), 'aria-label': `${words} here. ${open ? 'Hide them' : 'Show them'}.` },
      h('span', { class: 'n-ico' }, icon('graph', { size: 16 })),
      h('span', { class: 'n-body' }, h('span', { class: 'n-title strong' }, words), h('span', { class: 'n-via' }, 'to every item in the project')),
      h('span', { class: 'n-more' }, icon(open ? 'chevron_up' : 'chevron_down', { size: 14 }), open ? 'Hide' : 'Show'),
    ),
    open ? h('ul', { class: 'graph-gbox-list plain scroll', tabindex: '0', role: 'region', 'aria-label': 'Project-wide notes. It scrolls.' }, G.wide.map((entry) => h('li', {}, nodeFor(entry)))) : null,
  ];
}

const earlier = (entries) => (entries.length ? [h('p', { class: 'graph-gbox-sub' }, 'Earlier, kept for the reason'), h('ul', { class: 'graph-gbox-list plain' }, entries.map((entry) => h('li', {}, nodeFor(entry))))] : null);

function groupBox(def, G, item) {
  let count;
  let content;
  if (def.id === 'notes') {
    count = G.notes.length + G.wide.length + G.past.length;
    content = [G.notes.length ? limited('notes', G.notes, 4) : null, G.wide.length ? wideRow(G) : null, earlier(G.past)];
  } else {
    count = G[def.id].length;
    content = limited(def.id, G[def.id], 5);
  }
  const title = def.id === 'up' ? upTitle(item.kind) : def.title;
  return h(
    'section',
    { class: `graph-gbox g-${def.id}`, 'aria-labelledby': `graph-gb-${def.id}`, 'data-group': def.id },
    h('header', { class: 'graph-gbox-head' }, icon(def.icon, { size: 16 }), h('h3', { id: `graph-gb-${def.id}` }, title), h('span', { class: 'graph-gbox-count', 'aria-label': plural(count, 'item') }, count)),
    content,
  );
}

// -- the chosen item, in the middle -------------------------------------------------------------------

function hubCard(item) {
  const record = item.record;
  const common = [h('div', { class: 'hub-top' }, h('span', { class: 'kind-label' }, kindIcon(item.kind, 16), kindLabel(item.kind)), codeSpan(item.code), item.work ? h('span', { class: 'hub-state' }, statusChip(record.status)) : null)];
  let more;
  if (item.work) {
    const waits = waitingFor(record);
    const progress = progressOf(record);
    more = [
      h('h3', { class: 'hub-title' }, item.title),
      h('div', { class: 'hub-meta' }, priorityChip(record.priority), dueChip(record), record.claimed_by ? chip('held', 'Held by ', actorLabel(record.claimed_by, record.claimed_by_kind)) : null, waits.length ? chip('waiting', icon('clock', { size: 14 }), `Waiting for ${waits.slice(0, 2).map((ref) => resolve(ref).code).join(', ')}${waits.length > 2 ? '…' : ''}`) : null),
      progress && item.kind !== 'task'
        ? h('div', { class: 'hub-progress' }, h('span', { class: 'meter', role: 'img', 'aria-label': `${progress.done} of ${progress.total} tasks done` }, h('span', { css: { width: `${Math.round((progress.done / progress.total) * 100)}%` } })), h('span', { class: 'quiet' }, `${progress.done} of ${plural(progress.total, 'task')} done`))
        : null,
    ];
  } else if (item.kind === 'rule') {
    more = [h('h3', { class: 'hub-title' }, item.title), h('div', { class: 'hub-meta' }, standingChips(item), record.node ? null : chip('warn', icon('alert', { size: 14 }), 'Not placed')), record.body_preview || record.body ? h('p', { class: 'hub-text quiet' }, clip(record.body_preview || record.body, 220)) : null];
  } else {
    more = [
      h('h3', { class: 'hub-title' }, item.title),
      h('div', { class: 'hub-meta' }, standingChips(item), record.project_wide ? chip('info', icon('graph', { size: 14 }), 'Project-wide') : null),
      record.decided_by ? h('p', { class: 'hub-text quiet' }, `${decidedWords(record)}.`) : null,
      restOf(record) ? h('p', { class: 'hub-text' }, clip(restOf(record), 240)) : null,
      h('p', { class: 'hub-text quiet' }, h('strong', {}, 'Why. '), clip(record.why, 200)),
    ];
  }
  const also = item.work
    ? [h('button', { type: 'button', class: 'small ghost', id: 'graph-hub-structure', onclick: () => api.setParams({ tab: 'structure' }) }, icon('structure', { size: 14 }), 'In Structure'), h('button', { type: 'button', class: 'small ghost', id: 'graph-hub-order', onclick: () => api.setParams({ tab: 'order' }) }, icon('order', { size: 14 }), 'In Order')]
    : [];
  return h(
    'article',
    { class: `graph-hub${item.work ? ` s-${record.status}` : ''}`, id: 'graph-hub', 'aria-label': `The chosen item: ${item.code} ${item.title}`, 'data-ref': item.ref },
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

// What the lines of the picture mean, behind the view's Key button.
const aroundKey = () => viewKey({ label: 'How to read the lines', groups: [{ label: 'Lines', chips: LEGEND.map((entry) => keyChip(legendSample(entry), entry.label)) }] });

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

// The lines are drawn from where the groups really are on the page, so the picture follows the
// width of the window: from the hub to the edge of each group that is there.
function drawSpokes(ego, svg, hub, plan) {
  if (!ego.isConnected || getComputedStyle(svg).display === 'none') return;
  const box = ego.getBoundingClientRect();
  if (!box.width) return;
  svg.setAttribute('width', round(box.width));
  svg.setAttribute('height', round(box.height));
  svg.setAttribute('viewBox', `0 0 ${round(box.width)} ${round(box.height)}`);
  const rel = (element) => {
    const r = element.getBoundingClientRect();
    return { l: r.left - box.left, t: r.top - box.top, r: r.right - box.left, b: r.bottom - box.top, cx: (r.left + r.right) / 2 - box.left, cy: (r.top + r.bottom) / 2 - box.top };
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

function egoPanel(item, G) {
  const ego = h('div', { class: 'graph-ego' });
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
    const box = groupBox(def, G, item);
    const [col, band] = SLOT[def.slot];
    vars(box, { '--col': col, '--row': row[band] });
    box.dataset.slot = def.slot;
    ego.append(box);
    plan.push({ slot: def.slot, element: box, def });
  }
  if (!botUsed && !topUsed) ego.classList.add('ego-flat');
  const panel = h(
    'section',
    { class: 'graph-panel graph-ego-panel', 'aria-labelledby': 'graph-ego-h' },
    h('div', { class: 'graph-panel-head' }, h('div', {}, h('h2', { id: 'graph-ego-h' }, `Around ${item.work ? item.code : `${kindLabel(item.kind)} ${item.code}`}`)), h('div', { class: 'graph-panel-tools' }, item.work || item.kind === 'rule' ? reachControl() : null, aroundKey())),
    ego,
    empty.length ? h('p', { class: 'graph-also' }, h('span', { class: 'quiet' }, 'Checked, nothing found: '), empty.join(' · ')) : null,
    G.hints.length ? h('p', { class: 'graph-also quiet', id: 'graph-index-note' }, `Alike in meaning: ${G.hints[0]}`) : null,
  );
  requestAnimationFrame(() => drawSpokes(ego, svg, hub, plan));
  if (typeof ResizeObserver !== 'undefined') {
    watcher = new ResizeObserver(() => drawSpokes(ego, svg, hub, plan));
    watcher.observe(ego);
  }
  return panel;
}
