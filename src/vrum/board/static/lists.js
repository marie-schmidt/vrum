// The Lists page: everything in the project as a list you can filter. A kind switch chooses what is
// listed, and says how many of each the scope holds: Work (the epics, stories and tasks as an outline
// that opens and closes), Acceptance criteria, or Notes (one table, with a filter for where each note
// stands). The filter box takes the same filter language as the Board and the
// Roadmap (`q`) and the epic box narrows to one epic (`epic`); both also narrow the acceptance criteria
// and notes to those joined to that work. Each list has its own filters (status,
// words) and the actions the record's page has. A row opens the page of its record
// (from=lists), so the way back is this page as it was left.
//
// The page keeps no data of its own: it draws what the board already holds (the store) through
// lists-data.js. Its choices are in the address: `view` (work, requirements or notes; anything else is
// work), `epic`, `q`, `mentions=1` (the lines a [[reference]] makes, written under each row) and
// `sel=<ref>` (a record to mark and bring into view).

import { Refusal } from './api.js';
import { can, ui } from './ctx.js';
import { clear, h, put } from './dom.js';
import { filterBar, loadRows } from './filterbar.js';
import { createContextList } from './context-list.js';
import { openCreate } from './create.js';
import { icon } from './icons.js';
import { nodeCode } from './labels.js';
import { LIST_KINDS, LIST_WORDS, chosenKey, epicNumber, needs, scopeCounts, settings, showsContext, showsWork, universe, view } from './lists-data.js';
import { ensure, epics } from './store.js';
import { createWorkList } from './work-list.js';

let root = null;
let params = new URLSearchParams();
let cfg = settings(params);
let all = { dots: new Map(), edges: [] }; // everything the kind of list allows
let shown = { dots: new Map(), edges: [], notice: '' }; // what the scope keeps
let parts = {}; // the elements
let lists = null; // the work list and the context list, kept while the page is open
let bar = null; // the work filter
let generation = 0;
let appliedQ = ''; // the filter the box last took from the address
let scrolledTo = null; // the record the list was last brought to
let problem = ''; // what the server said when something could not be loaded

// -- the page -------------------------------------------------------------------------------------

export function mount(element, initial) {
  root = element;
  params = initial;
  cfg = settings(params);
  appliedQ = cfg.q;
  scrolledTo = null;
  build();
  draw();
}

export function unmount() {
  generation += 1;
  lists?.context.destroy();
  lists = null;
  root = null;
}

// Only the choice changed (the address got a new `sel`): mark it again, and draw nothing else.
// Anything else the address holds draws it again.
export function update(next) {
  params = next;
  if (!root) return;
  const before = cfg;
  cfg = settings(params);
  if (shapeKey(before) === shapeKey(cfg) && parts.loaded) {
    syncControls();
    markChoice();
  } else {
    draw();
  }
}

export function refresh() {
  if (root && !lists.context.busy()) draw();
}

const shapeKey = (c) => [c.view, c.scope, c.epic, c.q, c.mentions].join('|');

function check(name, label, checked, onchange, hint) {
  return h('label', { class: 'check', title: hint || null }, h('input', { type: 'checkbox', name, checked, onchange }), label);
}

function build() {
  const counts = Object.fromEntries(LIST_KINDS.map((name) => [name, h('span', { class: 'lists-count', id: `lists-count-${name}`, hidden: true })]));
  const views = h(
    'div',
    { id: 'lists-views', class: 'segmented', role: 'group', 'aria-label': 'What to list' },
    LIST_KINDS.map((name) =>
      h('button', { type: 'button', id: `lists-view-${name}`, 'data-value': name, 'aria-pressed': 'false', onclick: () => ui.navigate({ view: name }) }, LIST_WORDS[name], counts[name]),
    ),
  );
  const epicSelect = h('select', { id: 'lists-epic', 'aria-label': 'Epic', onchange: () => ui.navigate({ epic: epicSelect.value || null }) });
  bar = filterBar({ value: params.get('q') || '', onSubmit: (text) => ui.navigate({ q: text || null }) });
  lists = { work: createWorkList(), context: createContextList() };
  parts = {
    views,
    counts,
    epicSelect,
    epicSignature: null,
    loaded: false,
    notice: h('div', { class: 'lists-notice', id: 'lists-notice', role: 'status' }),
    loading: h('p', { class: 'loading', id: 'lists-loading', role: 'status' }, 'Loading…'),
    workSection: h(
      'section',
      { class: 'gv-section lists-work', id: 'work-list-section', 'aria-labelledby': 'work-list-heading' },
      h('h2', { class: 'sr-only', id: 'work-list-heading' }, 'Work'),
      lists.work.element,
    ),
  };
  const options = h('form', { class: 'lists-options', onsubmit: (event) => event.preventDefault(), 'aria-label': 'What the lists show' }, check('mentions', 'References', false, (event) => ui.navigate({ mentions: event.target.checked ? '1' : null }), 'Show, under each row, what its text refers to'));
  clear(root).append(
    h(
      'div',
      { class: 'lists-page', id: 'lists-page' },
      // The top bar names the page, so the h1 is only for assistive technology; a header row is drawn only when there is an action.
      can('write')
        ? h(
            'header',
            { class: 'page-header actions-only lists-head' },
            h('h1', { class: 'sr-only' }, 'Lists'),
            h('div', { class: 'page-actions' }, h('button', { type: 'button', class: 'primary', id: 'lists-new-item', onclick: newItem }, icon('plus', { size: 16 }), 'New item')),
          )
        : h('h1', { class: 'sr-only' }, 'Lists'),
      h(
        'div',
        { class: 'lists-toolbar' },
        views,
        h('div', { class: 'lists-field lists-epic' }, h('label', { for: 'lists-epic' }, 'Epic'), epicSelect),
        bar.form,
        bar.problem,
        h('div', { class: 'lists-meta' }, options),
      ),
      parts.notice,
      parts.loading,
      h('div', { class: 'lists-body gv-list', id: 'lists-list' }, parts.workSection, lists.context.element),
    ),
  );
}

function newItem() {
  if (cfg.epic) openCreate({ kind: 'story', parent: cfg.epic });
  else openCreate({});
}

// -- working out what to draw ---------------------------------------------------------------------

// The work the filter asks for, as the server reads it, or null for no filter (or one it could not
// read: its words are under the filter box).
async function matchedWork(text) {
  if (!text) {
    bar.setProblem('');
    return null;
  }
  const rows = await loadRows(text, bar);
  return rows ? new Set(rows.map((row) => row.ref)) : null;
}

async function draw() {
  const mine = ++generation;
  cfg = settings(params);
  syncControls();
  problem = '';
  try {
    for (const name of needs(cfg)) await ensure(name);
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    problem = error.message;
  }
  const matched = await matchedWork(cfg.q);
  if (mine !== generation || !root) return;
  all = universe(cfg);
  shown = view(cfg, all, matched);
  const ctx = { cfg, all, shown };
  parts.workSection.hidden = !showsWork(cfg);
  lists.context.element.hidden = !showsContext(cfg);
  if (showsWork(cfg)) lists.work.draw(ctx);
  if (showsContext(cfg)) lists.context.draw(ctx);
  drawCounts(scopeCounts(cfg, matched));
  scrollToChoice();
  drawNotice();
  parts.loading.hidden = true;
  parts.loaded = true;
}

// How many of each kind the scope holds, on the kind switch. Drawn only once it is known: the acceptance criteria and
// the notes are not, while they could not be loaded.
function drawCounts(counts) {
  for (const name of LIST_KINDS) {
    const known = name === 'work' || !problem;
    const element = parts.counts[name];
    element.hidden = !known;
    element.textContent = known ? String(counts[name]) : '';
    if (known) element.dataset.count = String(counts[name]);
    else delete element.dataset.count;
  }
}

function syncControls() {
  for (const button of parts.views.querySelectorAll('button')) button.setAttribute('aria-pressed', String(button.dataset.value === cfg.view));
  const choices = epics();
  const signature = choices.map((epic) => `${epic.ref}:${epic.title}`).join('|');
  if (signature !== parts.epicSignature) {
    parts.epicSignature = signature;
    parts.epicSelect.replaceChildren(h('option', { value: '' }, 'All epics'), ...choices.map((epic) => h('option', { value: epic.number }, `${nodeCode(epic.kind, epic.number)} ${epic.title}`)));
  }
  parts.epicSelect.value = cfg.epic ? String(epicNumber(cfg.epic)) : '';
  // The filter box takes what the address says only when the address changed, so that words
  // typed and not yet applied are still there after switching the kind of list.
  if (cfg.q !== appliedQ) {
    appliedQ = cfg.q;
    bar.setValue(cfg.q);
  }
  root.querySelector('[name="mentions"]').checked = cfg.mentions;
  const page = root.querySelector('#lists-page');
  page.dataset.view = cfg.view;
}

// What the person is told: how much there is in the scope, anything that could not be loaded, and a
// chosen record that the scope leaves out.
function drawNotice() {
  const chosen = chosenKey(cfg);
  const missing = chosen && all.dots.has(chosen) && !shown.dots.has(chosen);
  parts.notice.classList.toggle('refusal', Boolean(problem));
  put(
    parts.notice,
    shown.notice ? h('span', {}, shown.notice) : null,
    problem ? h('span', { class: 'lists-problem' }, problem, ' ', h('button', { type: 'button', class: 'small', id: 'lists-retry', onclick: draw }, icon('reset', { size: 14 }), 'Try again')) : null,
    missing ? ['The chosen record is outside this scope. ', h('button', { type: 'button', class: 'small', id: 'lists-show-whole-project', onclick: () => ui.navigate({ epic: null, q: null }) }, icon('clear', { size: 14 }), 'Show the whole project')] : null,
  );
  parts.notice.hidden = !parts.notice.hasChildNodes();
}

// -- the chosen record --------------------------------------------------------------------------------

// Marks the chosen record again, without drawing anything.
function markChoice() {
  const key = chosenKey(cfg);
  if (showsWork(cfg)) lists.work.select(key);
  if (showsContext(cfg)) lists.context.select(key);
  scrollToChoice();
  drawNotice();
}

// Brings the chosen record into view once, when it is a new choice, not on every refresh.
function scrollToChoice() {
  const key = chosenKey(cfg);
  if (key === scrolledTo) return;
  scrolledTo = key;
  const item = key ? root.querySelector(`#lists-list [data-key="${key}"]`) : null;
  item?.scrollIntoView({ block: 'nearest' });
}
