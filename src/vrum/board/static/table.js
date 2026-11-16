// The Table: one row for each epic, story or task that the filter finds, to sort, group and edit.
// The filter text is the one list_nodes takes as `query`, so the rows are what an agent would be
// given. A cell is edited in place; several rows are changed at once through update_nodes, each
// row as its own versioned change, at the version it had when it was chosen.

import { Refusal, call } from './api.js';
import { actor, notify, ui } from './ctx.js';
import { clear, day, h, moment, plural } from './dom.js';
import { filterBar, loadRows } from './filterbar.js';
import { KIND, PRIORITIES, PRIORITY, SETTABLE, STATUS, STATUSES, kindChip } from './labels.js';
import { epicOf, store, sync, titleOf } from './store.js';

const COLUMNS = [
  ['kind', 'Kind'],
  ['title', 'Title'],
  ['parent', 'In'],
  ['status', 'Status'],
  ['priority', 'Priority'],
  ['claimed', 'Held by'],
  ['waiting', 'Waiting for'],
  ['progress', 'Progress'],
  ['start', 'Start'],
  ['target', 'Target'],
  ['updated', 'Last change'],
];
const GROUPS = [
  ['', 'No grouping'],
  ['epic', 'Epic'],
  ['story', 'Story'],
  ['status', 'Status'],
  ['priority', 'Priority'],
  ['claimed', 'Who holds it'],
];
const KIND_ORDER = { epic: 0, story: 1, task: 2 };
const FIRST_ROWS = 300;

let root = null;
let params = new URLSearchParams();
let bar = null; // the filter box
let rows = []; // what the filter found
let selected = new Map(); // id -> the version it had when it was chosen
let shown = FIRST_ROWS;
let busy = 0; // changes of our own in flight: the page does not reload under them
let generation = 0;
let parts = {};

// -- the page -------------------------------------------------------------------------------------

export function mount(element, initial) {
  root = element;
  params = initial;
  selected = new Map();
  written.clear();
  shown = FIRST_ROWS;
  rows = [];
  bar = filterBar({ value: params.get('q') || '', onSubmit: (text) => ui.navigate({ q: text || null }) });
  const group = h(
    'select',
    { id: 'group', onchange: () => ui.navigate({ group: group.value || null }) },
    GROUPS.map(([value, label]) => h('option', { value }, label)),
  );
  parts = {
    group,
    count: h('p', { class: 'count', id: 'count', role: 'status' }),
    bulk: h('form', { class: 'bulk', id: 'bulk', hidden: true }),
    result: h('div', { id: 'bulk-result' }),
    wrap: h('div', { class: 'table-wrap' }),
    banner: h('div', { id: 'table-banner' }),
  };
  clear(root).append(
    h('div', { class: 'toolbar' }, h('h1', {}, 'Table'), bar.form, h('label', { for: 'group' }, 'Group by'), group),
    bar.help,
    bar.problem,
    parts.count,
    parts.banner,
    parts.bulk,
    parts.result,
    parts.wrap,
  );
  load();
}

export function unmount() {
  generation += 1;
  root = null;
}

export function update(next) {
  const before = params;
  params = next;
  if (!root) return;
  if ((next.get('q') || '') !== (before.get('q') || '')) {
    selected = new Map();
    shown = FIRST_ROWS;
    load();
  } else {
    draw();
  }
}

// Something changed on the server. Show it, unless the person is typing in a cell.
export function refresh() {
  if (!root || busy) return;
  if (typing()) return warnTyping();
  load();
}

function warnTyping() {
  clear(parts.banner).append(
    h(
      'div',
      { class: 'notice changed', role: 'status' },
      'The rows changed while you were typing, and what you typed is still here. ',
      h('button', { type: 'button', class: 'link', onclick: () => load(true) }, 'Load the current rows'),
    ),
  );
}

function typing() {
  const field = document.activeElement;
  return Boolean(
    field && parts.wrap.contains(field) && field.matches('input[type=text], input[type=date]') && field.value !== field.dataset.original,
  );
}

async function load(force = false) {
  const mine = ++generation;
  const found = await loadRows(params.get('q') || '', bar);
  if (mine !== generation || !root) return;
  if (!force && typing()) return warnTyping();
  if (found !== null) rows = found;
  else if (!rows.length) rows = [];
  clear(parts.banner);
  draw();
}

// -- sorting and grouping ---------------------------------------------------------------------------

function sortValue(node, key) {
  switch (key) {
    case 'id': return node.id;
    case 'kind': return KIND_ORDER[node.kind];
    case 'title': return node.title.toLowerCase();
    case 'parent': return node.parent_id === null ? null : titleOf(node.parent_id).toLowerCase();
    case 'status': return STATUSES.indexOf(node.status);
    case 'priority': return PRIORITIES.indexOf(node.priority);
    case 'claimed': return node.claimed_by ? node.claimed_by.toLowerCase() : null;
    case 'waiting': return node.waiting_on.length;
    case 'progress': return node.progress_total ? node.progress_done / node.progress_total : null;
    case 'start': return node.start_on;
    case 'target': return node.target_on;
    case 'updated': return node.updated_at;
    default: return node.id;
  }
}

function sorted(list) {
  const raw = params.get('sort') || '';
  const key = raw.replace(/^-/, '') || 'id';
  const sign = raw.startsWith('-') ? -1 : 1;
  return [...list].sort((a, b) => {
    const [x, y] = [sortValue(a, key), sortValue(b, key)];
    if (x === null && y !== null) return 1;
    if (y === null && x !== null) return -1;
    if (x === y) return a.id - b.id;
    return (x < y ? -1 : 1) * sign;
  });
}

function groupOf(node, mode) {
  if (mode === 'epic') {
    const epic = epicOf(node);
    return { key: String(epic.id), label: `Epic #${epic.id} ${epic.title}`, order: epic.id };
  }
  if (mode === 'story') {
    if (node.kind === 'epic') return { key: 'none', label: 'Epics, which are in no story', order: 0 };
    const id = node.kind === 'story' ? node.id : node.parent_id;
    return { key: String(id), label: `Story #${id} ${titleOf(id)}`, order: id };
  }
  if (mode === 'status') return { key: node.status, label: STATUS[node.status].label, order: STATUSES.indexOf(node.status) };
  if (mode === 'priority') return { key: node.priority, label: `${PRIORITY[node.priority].label} priority`, order: 3 - PRIORITIES.indexOf(node.priority) };
  const who = node.claimed_by;
  return { key: who || '', label: who ? `Held by ${who}` : 'Held by nobody', order: who ? 0 : 1 };
}

function grouped(list) {
  const mode = params.get('group') || '';
  if (!mode) return [{ key: '', label: '', members: list }];
  const groups = new Map();
  for (const node of list) {
    const found = groupOf(node, mode);
    if (!groups.has(found.key)) groups.set(found.key, { ...found, members: [] });
    groups.get(found.key).members.push(node);
  }
  return [...groups.values()].sort((a, b) => a.order - b.order || a.label.localeCompare(b.label));
}

// -- drawing --------------------------------------------------------------------------------------

function sortHeading(key, label) {
  const raw = params.get('sort') || '';
  const here = raw.replace(/^-/, '') === key;
  const direction = here ? (raw.startsWith('-') ? 'descending' : 'ascending') : 'none';
  const next = !here ? key : raw.startsWith('-') ? null : `-${key}`;
  return h(
    'th',
    { scope: 'col', 'aria-sort': direction, dataset: { column: key } },
    h(
      'button',
      { type: 'button', class: 'sort', onclick: () => ui.navigate({ sort: next }) },
      label,
      h('span', { 'aria-hidden': 'true' }, direction === 'ascending' ? ' ▲' : direction === 'descending' ? ' ▼' : ''),
    ),
  );
}

function draw() {
  if (!root) return;
  const focused = document.activeElement?.dataset?.focusKey;
  const here = new Map(rows.map((node) => [node.id, node]));
  for (const id of [...selected.keys()]) if (!here.has(id)) selected.delete(id);
  const ordered = sorted(rows);
  const visible = new Set(ordered.slice(0, shown).map((node) => node.id));
  const query = params.get('q') || '';
  parts.count.textContent = `${plural(rows.length, 'item')} ${query ? 'match this filter' : 'in all'}${rows.length > shown ? `; the first ${shown} are shown` : ''}.`;
  parts.group.value = params.get('group') || '';
  bar.setValue(query);

  const all = h('input', {
    type: 'checkbox',
    'aria-label': 'Choose every row shown',
    checked: visible.size > 0 && [...visible].every((id) => selected.has(id)),
    onchange: () => {
      for (const id of visible) {
        if (all.checked) selected.set(id, here.get(id).version);
        else selected.delete(id);
      }
      draw();
    },
  });
  const head = h('thead', {}, h('tr', {}, h('th', { scope: 'col', class: 'pick' }, all), COLUMNS.map(([key, label]) => sortHeading(key, label))));
  const bodies = grouped(ordered.filter((node) => visible.has(node.id))).map((group) =>
    h(
      'tbody',
      { class: 'group', dataset: { group: group.key } },
      group.label ? h('tr', { class: 'group-head' }, h('th', { colspan: COLUMNS.length + 1, scope: 'colgroup' }, `${group.label} (${group.members.length})`)) : null,
      group.members.map(rowFor),
    ),
  );
  const more =
    rows.length > shown
      ? [h('p', {}, h('button', { type: 'button', id: 'show-more', onclick: () => { shown += FIRST_ROWS; draw(); } }, `Show ${Math.min(FIRST_ROWS, rows.length - shown)} more`))]
      : [];
  clear(parts.wrap).append(
    rows.length ? h('table', { class: 'nodes', id: 'nodes-table' }, head, bodies) : h('p', { class: 'empty' }, query ? 'No item matches this filter.' : 'Nothing here yet.'),
    ...more,
  );
  drawBulk();
  if (focused) parts.wrap.querySelector(`[data-focus-key="${focused}"]`)?.focus();
}

function field(node, what, control) {
  control.dataset.focusKey = `${what}-${node.id}`;
  return control;
}

function rowFor(node) {
  const choose = h('input', {
    type: 'checkbox',
    'aria-label': `Choose #${node.id} ${node.title}`,
    checked: selected.has(node.id),
    onchange: (event) => {
      if (event.target.checked) selected.set(node.id, node.version);
      else selected.delete(node.id);
      tr.classList.toggle('selected', event.target.checked);
      drawBulk();
    },
  });
  const title = h('input', { type: 'text', value: node.title, 'aria-label': `Title of #${node.id}`, dataset: { original: node.title }, onchange: (event) => save(node, { title: event.target.value }, 'the title', event.target) });
  const status = h(
    'select',
    { 'aria-label': `Status of #${node.id}`, onchange: (event) => save(node, { status: event.target.value }, 'the status') },
    node.status === 'ready' ? h('option', { value: 'ready', selected: true, disabled: true }, 'Ready (worked out)') : null,
    SETTABLE.map((name) => h('option', { value: name, selected: name === node.status }, STATUS[name].label)),
  );
  const priority = h(
    'select',
    { 'aria-label': `Priority of #${node.id}`, onchange: (event) => save(node, { priority: event.target.value }, 'the priority') },
    PRIORITIES.map((name) => h('option', { value: name, selected: name === node.priority }, PRIORITY[name].label)),
  );
  const date = (key, label) => {
    const input = h('input', { type: 'date', value: day(node[`${key}_on`]), 'aria-label': `${label} date of #${node.id}`, dataset: { original: day(node[`${key}_on`]) } });
    input.addEventListener('change', () => save(node, input.value ? { [`${key}_on`]: input.value } : { [`clear_${key}_on`]: true }, `the ${label.toLowerCase()} date`, input));
    return input;
  };
  const parent = node.parent_id === null ? '' : h('a', { href: `#/${ui.page}?${link(node.parent_id)}` }, `#${node.parent_id} ${titleOf(node.parent_id)}`);
  const tr = h(
    'tr',
    { class: `item kind-${node.kind}${selected.has(node.id) ? ' selected' : ''}`, dataset: { id: node.id, kind: node.kind, status: node.status, priority: node.priority } },
    h('td', { class: 'pick' }, field(node, 'pick', choose)),
    h('td', { class: 'kind' }, kindChip(node.kind)),
    h('td', { class: 'title' }, h('a', { class: 'open', href: `#/${ui.page}?${link(node.id)}`, 'aria-label': `Open #${node.id}` }, `#${node.id}`), ' ', field(node, 'title', title)),
    h('td', { class: 'parent' }, parent),
    h('td', { class: 'status' }, field(node, 'status', status), node.overdue ? h('span', { class: 'chip overdue' }, '! Overdue') : null),
    h('td', { class: 'priority' }, field(node, 'priority', priority)),
    h('td', { class: 'claimed' }, node.claimed_by ? `${node.claimed_by}` : node.lapsed_claim_by ? h('span', { class: 'quiet' }, `${node.lapsed_claim_by} (ran out)`) : ''),
    h('td', { class: 'waiting' }, node.waiting_on.map((id, position) => [position ? ', ' : '', h('a', { href: `#/${ui.page}?${link(id)}` }, `#${id}`)])),
    h('td', { class: 'progress' }, node.progress_total !== null ? `${node.progress_done}/${node.progress_total}` : ''),
    h('td', { class: 'start' }, field(node, 'start', date('start', 'Start'))),
    h('td', { class: 'target' }, field(node, 'target', date('target', 'Target')), node.schedule_conflicts.length ? h('span', { class: 'chip conflict' }, '! Conflict') : null),
    h('td', { class: 'updated' }, moment(node.updated_at)),
  );
  return tr;
}

function link(id) {
  const next = new URLSearchParams(ui.params);
  next.set('card', String(id));
  next.delete('panel');
  return next.toString();
}

// -- changing one cell ------------------------------------------------------------------------------

// Saves go one after another, so that each is sent at the version the one before it made.
let queue = Promise.resolve();
const written = new Map(); // id -> the version this page's own last change gave it

function save(node, change, what, input = null) {
  const turn = queue.then(() => saveNow(node, change, what, input));
  queue = turn.catch(() => {});
  return turn;
}

async function saveNow(node, change, what, input) {
  busy += 1;
  try {
    // A row drawn again after our own change may still hold the older version; ours is newer.
    const version = Math.max(node.version, written.get(node.id) ?? 0);
    const saved = await call('update_node', { actor: actor(), node_id: node.id, expected_version: version, ...change });
    written.set(node.id, saved.version);
    Object.assign(node, saved);
    if (input) input.dataset.original = input.value;
    notify(`Saved ${what} of #${node.id}.`);
    await sync();
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    if (input) input.value = input.dataset.original;
    notify(error.message, { refusal: true });
  } finally {
    busy -= 1;
  }
  // Show what the server holds now, unless the person has started typing in another cell.
  refresh();
}

// -- the bulk change ----------------------------------------------------------------------------

function drawBulk() {
  const form = parts.bulk;
  form.hidden = selected.size === 0;
  if (selected.size === 0) return clear(form);
  const priority = h('select', { id: 'bulk-priority', 'aria-label': 'Priority to set' }, h('option', { value: '' }, 'Priority: leave as it is'), PRIORITIES.map((name) => h('option', { value: name }, PRIORITY[name].label)));
  const status = h('select', { id: 'bulk-status', 'aria-label': 'Status to set' }, h('option', { value: '' }, 'Status: leave as it is'), SETTABLE.map((name) => h('option', { value: name }, STATUS[name].label)));
  form.onsubmit = (event) => {
    event.preventDefault();
    applyBulk(priority.value, status.value);
  };
  return clear(form).append(
    h('strong', { id: 'bulk-count' }, `${plural(selected.size, 'row')} chosen`),
    priority,
    status,
    h('button', { type: 'submit', class: 'primary', id: 'bulk-apply' }, `Change ${plural(selected.size, 'row')}`),
    h('button', { type: 'button', onclick: () => { selected = new Map(); draw(); } }, 'Choose none'),
  );
}

async function applyBulk(priority, status) {
  if (!priority && !status) {
    notify('Choose a priority or a status to set.', { refusal: true });
    return;
  }
  const changes = [...selected].map(([id, version]) => ({
    node_id: id,
    expected_version: version,
    ...(priority ? { priority } : {}),
    ...(status ? { status } : {}),
  }));
  busy += 1;
  let outcome;
  try {
    outcome = await call('update_nodes', { actor: actor(), changes });
  } catch (error) {
    busy -= 1;
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
    return;
  }
  const byId = new Map(rows.map((node) => [node.id, node]));
  const line = (result) =>
    h(
      'li',
      { dataset: { id: result.node_id, applied: String(result.applied) } },
      h('strong', {}, `#${result.node_id} ${byId.get(result.node_id)?.title ?? ''}: `),
      result.applied ? 'changed' : h('span', { class: 'refusal-text' }, `not changed. ${result.refusal}`),
    );
  clear(parts.result).append(
    h(
      'div',
      { class: `notice bulk-result${outcome.refused ? ' refusal' : ''}`, role: 'status' },
      h('span', { class: 'summary' }, `${outcome.applied} changed, ${outcome.refused} refused.`),
      h('ul', { class: 'plain' }, outcome.results.map(line)),
      h('button', { type: 'button', class: 'link', onclick: () => clear(parts.result) }, 'Dismiss'),
    ),
  );
  selected = new Map();
  try {
    await sync();
  } finally {
    busy -= 1;
  }
  await load();
}
