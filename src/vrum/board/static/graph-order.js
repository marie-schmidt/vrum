// The Order view of the Graph: what must come first, and what waits for it, for every piece of work of
// one epic. By default it is a plan in phases (graph-phases.js, graph-plan.js): phase 1 is what can start
// now, each later phase is what becomes possible when the work before it is done, one phase on the page
// at a time with a stepper, and with sizes a phase breaks into batches that fit a points budget. The other
// way to look is chains (graph-chains.js): the "needs first" pictures, one chain at a time, and last the
// loose items that have no links; a box opens the item's page, and pointing at one lights its chain. The
// epic is the focus's epic; with nothing chosen it is the first epic the project has, and the page says
// so. A chosen story or task is highlighted in it.
//
// The address keys of this view: `mode=chains` (phases when there is none), `phase=<n>` and `chain=<n>`
// (the step shown; the first when there is none), `budget=<points>` (5, 8, 13 or 21; 8 when there is none),
// `blocked=1` (light up what is blocked in a chain) and `pick=<ref>` (the item highlighted, with its chain
// in chains mode; the "Waits for" buttons of a phase set it; the Graph's focus, which is the scope, is not
// changed by it). Everything is read from the store.

import { viewLink } from './ctx.js';
import { announce, h, plural, put } from './dom.js';
import { icon } from './icons.js';
import { SIZES, SIZE_POINTS, SIZE_WORDS, kindIcon, kindLabel, parseItemRef, pointsOf, pointsWords, sizeChip } from './labels.js';
import { chainPicture, chainsOf, orderSample } from './graph-chains.js';
import { keyChip, statusChips, viewKey } from './graph-key.js';
import { doneGroup, phaseSection, storyGroups } from './graph-phases.js';
import { BUDGETS, DEFAULT_BUDGET, inPlace, leavesOf, planOf, sumPoints } from './graph-plan.js';
import { clip, codeOf, toggle, viewMessage, workModel } from './graph-shared.js';
import { store } from './store.js';

export const KEYS = ['mode', 'phase', 'chain', 'budget', 'blocked', 'pick'];

let root = null;
let api = null;
let stop = [];
let doneOpen = false; // the folded group of finished work, kept while the page is open
let arrived = null; // the item a "waits for" button went to, which gets the keyboard once it is drawn

export function mount(element, nextApi) {
  root = element;
  api = nextApi;
  render();
}

export function update(nextApi) {
  api = nextApi;
  if (root) render();
}

export function refresh() {
  if (root) render();
}

export function unmount() {
  for (const fn of stop) fn();
  stop = [];
  root = null;
  api = null;
}

const setLook = (changes) => api.setParams(changes, { replace: true });
const step = (value) => (value <= 1 ? null : value);

// -- the page ------------------------------------------------------------------------------------

function render() {
  for (const fn of stop) fn();
  stop = [];
  const keep = rememberFocus();
  const model = workModel();
  const focusRef = api.focus;
  const parsed = focusRef ? parseItemRef(focusRef) : null;
  if (parsed && !model.node(focusRef) && ['rule', 'note'].includes(parsed.kind)) {
    put(root, h('div', { class: 'graph-xv' }, otherKind(model, focusRef, parsed)));
    return;
  }
  const focus = focusRef ? model.node(focusRef) : null;
  // With nothing chosen (or something that is not there), the first epic the project has.
  const epic = focus ? model.epicOf(focus.ref) : model.epics[0] || null;
  const byDefault = !focus;
  const missing = Boolean(focusRef && !focus);
  if (!epic) {
    put(root, h('div', { class: 'graph-xv' }, nothingToOrder()));
    return;
  }
  const lead = byDefault ? `${missing ? 'That item is not there, so this is' : 'This is'} the first epic.` : null;
  const leaves = leavesOf(model, epic.ref);
  if (!leaves.length) {
    put(root, h('div', { class: 'graph-xv' }, nothingHere(epic, lead)));
    return;
  }
  const plan = planOf(model, leaves, store.links);
  const scope = model.subtree(epic.ref);
  const asked = api.params.get('pick');
  const highlight = asked && scope.has(asked) ? asked : focus && focus.kind !== 'epic' ? focus.ref : null;
  const mode = api.params.get('mode') === 'chains' ? 'chains' : 'phases';
  const c = { model, plan, epic, scope, highlight };
  const view = mode === 'chains' ? chainsView(c) : phasesView(c);

  const toolbar = h('div', { class: 'graph-or-toolbar' }, modeSwitch(mode), view.controls, view.key);
  const main = h(
    'section',
    { class: 'graph-x-panel graph-or-main', 'aria-labelledby': 'graph-or-title', onkeydown: view.arrows },
    h('div', { class: 'graph-x-head' }, h('h2', { id: 'graph-or-title' }, `Order of work in ${codeOf(epic)} ${clip(epic.title, 60)}`), lead ? h('p', { class: 'quiet', id: 'graph-or-lead' }, lead) : null),
    toolbar,
    h('div', { class: 'graph-or-summary' }, view.summary),
    view.content,
  );
  put(root, h('div', { class: 'graph-xv' }, h('div', { class: 'graph-or', 'data-mode': mode }, main)));
  if (view.fix) setLook(view.fix);
  restoreFocus(keep);
}

const MODES = [
  ['phases', 'Phases', 'What can start now, then what follows, one phase at a time'],
  ['chains', 'Chains', 'The "needs first" chains, one at a time, then the items with no links'],
];

function modeSwitch(mode) {
  return h(
    'div',
    { class: 'segmented', id: 'graph-or-mode', role: 'group', 'aria-label': 'How to order the work' },
    MODES.map(([value, text, title]) =>
      h('button', { type: 'button', title, 'aria-pressed': String(mode === value), 'data-choice': `mode:${value}`, 'data-fk': `mode:${value}`, onclick: () => setLook({ mode: value === 'phases' ? null : value }) }, text),
    ),
  );
}

// `< Phase 2 of 5 >`: real buttons, and the arrow keys go with them (see `arrowsFor`).
function stepper(label, index, count, go) {
  return h(
    'div',
    { class: 'graph-or-stepper', id: 'graph-or-stepper', role: 'group', 'aria-label': 'Step through the work' },
    h('button', { class: 'small icon-only', id: 'graph-or-prev', type: 'button', 'aria-label': 'Previous', 'data-fk': 'prev', disabled: index <= 1, onclick: () => go(index - 1) }, icon('chevron_left', { size: 16 })),
    h('span', { class: 'graph-or-step', id: 'graph-or-step' }, label),
    h('button', { class: 'small icon-only', id: 'graph-or-next', type: 'button', 'aria-label': 'Next', 'data-fk': 'next', disabled: index >= count, onclick: () => go(index + 1) }, icon('chevron_right', { size: 16 })),
  );
}

// The arrow keys step, anywhere on the panel except where an arrow already means something else.
function arrowsFor(index, count, go) {
  return (event) => {
    const by = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (!by || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (event.target.closest('input, textarea, select, .graph-or-scroll')) return;
    const next = index + by;
    if (next < 1 || next > count) return;
    event.preventDefault();
    go(next);
  };
}

const chip = (...children) => h('span', { class: 'chip' }, ...children);

// -- phases -------------------------------------------------------------------------------------------

function phasesView({ model, plan, highlight }) {
  const count = plan.phases.length;
  const sized = plan.open.some((node) => pointsOf(node) !== null);
  const budget = BUDGETS.includes(Number(api.params.get('budget'))) ? Number(api.params.get('budget')) : DEFAULT_BUDGET;
  const phaseOf = (ref) => {
    if (plan.phaseOf.has(ref)) return plan.phaseOf.get(ref);
    const under = leavesOf(model, ref).map((node) => plan.phaseOf.get(node.ref)).filter(Boolean);
    return under.length ? Math.min(...under) : 1;
  };
  let phase = 1;
  let fix = null;
  if (count) {
    const asked = Number.parseInt(api.params.get('phase'), 10);
    phase = Number.isInteger(asked) ? Math.min(Math.max(asked, 1), count) : highlight ? phaseOf(highlight) : 1;
    if (api.params.has('phase') && String(phase) !== api.params.get('phase')) fix = { phase: step(phase) };
  }
  const go = (next) => {
    announce(`Phase ${next} of ${count}, ${plural(plan.phases[next - 1].length, 'item')}`);
    setLook({ phase: step(next), pick: null });
  };
  const ctx = {
    model,
    plan,
    highlight,
    sized,
    budget,
    jump: (ref, to) => {
      arrived = ref;
      setLook({ phase: step(to), pick: ref });
    },
  };
  const controls = [];
  if (count) controls.push(stepper(`Phase ${phase} of ${count}`, phase, count, go));
  if (count && sized) {
    const select = h(
      'select',
      { id: 'graph-or-budget', 'data-fk': 'budget', value: String(budget), onchange: () => setLook({ budget: Number(select.value) === DEFAULT_BUDGET ? null : select.value }) },
      BUDGETS.map((points) => h('option', { value: String(points) }, `${points} points`)),
    );
    controls.push(h('div', { class: 'graph-x-control' }, h('label', { class: 'graph-x-control-label', for: 'graph-or-budget' }, 'Fit a run of up to'), select));
  }
  const unsized = plan.open.filter((node) => pointsOf(node) === null).length;
  const summary = [
    chip(`${plan.open.length} to do`),
    plan.done.length ? h('span', { class: 'chip ok' }, icon('done', { size: 14 }), `${plan.done.length} done`) : null,
    plan.dropped.length ? chip(`${plan.dropped.length} dropped not shown`) : null,
    sized ? chip(plural(sumPoints(plan.open, pointsOf), 'point')) : null,
    sized && unsized ? chip(`${unsized} not sized`) : null,
  ];
  // The group of finished work opens by itself when the item being looked for is in it.
  const doneOpenNow = doneOpen || plan.done.some((node) => node.ref === highlight);
  const content = h(
    'div',
    { class: 'graph-or-phases' },
    doneGroup(ctx, { open: doneOpenNow, toggle: () => { doneOpen = !doneOpenNow; render(); } }),
    count ? phaseSection(ctx, phase) : h('p', { class: 'graph-x-note quiet', id: 'graph-or-nothing' }, 'Nothing is left to do here.'),
  );
  const key = viewKey({
    label: 'How to read the phases',
    groups: [
      {
        label: 'Phases',
        chips: [
          keyChip(h('span', { class: 'graph-or-key-mark' }, '1'), 'Phase 1 can start now'),
          keyChip(h('span', { class: 'graph-or-key-mark' }, '2'), 'A later phase starts when the work it waits for is done'),
          sized ? keyChip(h('span', { class: 'graph-or-key-mark' }, 'B'), 'A batch is the work of a phase that fits one run') : null,
        ].filter(Boolean),
      },
      sized ? { label: 'Sizes', chips: SIZES.map((size) => keyChip(sizeChip({ size }), `${SIZE_WORDS[size]}, ${pointsWords(SIZE_POINTS[size])}`)) } : null,
      { label: 'Marks', chips: [...statusChips(), keyChip(icon('clock', { size: 14 }), 'Overdue: how many days late', { className: 'graph-key-late' }), sized ? keyChip(h('span', { class: 'chip graph-or-big' }, 'Bigger than a run'), 'Bigger than the budget, so a batch of its own') : null].filter(Boolean) },
    ],
  });
  return { controls, key, summary, content, fix, arrows: count ? arrowsFor(phase, count, go) : null };
}

// -- chains -------------------------------------------------------------------------------------------

function chainsView({ model, plan, scope, highlight }) {
  const chains = chainsOf(model, store.links)
    .filter((chain) => chain.refs.some((ref) => scope.has(ref)))
    .sort((a, b) => b.refs.length - a.refs.length);
  const loose = inPlace(model, [...plan.done, ...plan.open].filter((node) => !plan.linked.has(node.ref)));
  const stops = chains.length + (loose.length ? 1 : 0);
  const blocked = api.params.get('blocked') === '1';
  let at = 1;
  let fix = null;
  if (stops) {
    const asked = Number.parseInt(api.params.get('chain'), 10);
    const home = highlight ? chains.findIndex((chain) => chain.refs.includes(highlight)) : -1;
    const inLoose = highlight && loose.some((node) => node.ref === highlight);
    at = Number.isInteger(asked) ? Math.min(Math.max(asked, 1), stops) : home >= 0 ? home + 1 : inLoose ? stops : 1;
    if (api.params.has('chain') && String(at) !== api.params.get('chain')) fix = { chain: step(at) };
  }
  const go = (next) => {
    announce(next > chains.length ? `Loose items, ${loose.length}` : `Chain ${next} of ${chains.length}, ${plural(chains[next - 1].refs.length, 'item')}`);
    setLook({ chain: step(next), pick: null });
  };
  const onLoose = stops > 0 && at > chains.length;
  const chain = stops && !onLoose ? chains[at - 1] : null;
  const controls = [];
  if (stops) controls.push(stepper(onLoose ? `Loose items (${loose.length})` : `Chain ${at} of ${chains.length}`, at, stops, go));
  if (chain) controls.push(toggle('Highlight what is blocked', 'blocked', blocked, () => setLook({ blocked: blocked ? null : '1' }), { 'data-choice': 'blocked' }));

  let content;
  let picture = null;
  const summary = [chip(plural(chains.length, 'chain')), loose.length ? chip(`${plural(loose.length, 'loose item')}`) : null];
  if (chain) {
    picture = chainPicture(model, chain, { scope, pick: highlight, blocked, open: api.open });
    stop.push(picture.stop);
    const count = (test) => chain.refs.filter((ref) => test(model.node(ref), ref)).length;
    const blockedCount = count((node) => node.status === 'blocked');
    const lateCount = count((_, ref) => model.late(ref) > 0);
    summary.push(
      chip(plural(chain.refs.length, 'item'), ' in this chain'),
      blockedCount ? h('span', { class: 'chip status status-blocked' }, icon('blocked', { size: 14 }), `${blockedCount} blocked`) : null,
      lateCount ? h('span', { class: 'chip warn' }, icon('clock', { size: 14 }), `${lateCount} overdue`) : null,
      h('span', { class: 'chip ok' }, icon('done', { size: 14 }), `${count((node) => node.status === 'done')} done`),
    );
    content = h('section', { class: 'graph-or-chain', id: 'graph-or-chain', 'data-stop': at, 'aria-label': `Chain ${at} of ${chains.length}` }, picture.element);
  } else if (onLoose) {
    const ctx = { model, plan, highlight, jump: () => {} };
    content = h('section', { class: 'graph-or-loose', id: 'graph-or-loose', 'aria-labelledby': 'graph-or-loose-title' }, h('h3', { class: 'graph-or-phase-head', id: 'graph-or-loose-title' }, `Loose items (${loose.length})`), storyGroups(ctx, loose));
  } else {
    content = h('p', { class: 'graph-x-note quiet' }, 'Nothing is left to order here.');
  }
  const key = viewKey({
    label: 'How to read the chains',
    groups: [
      {
        label: 'Boxes and lines',
        chips: [
          ...statusChips(),
          keyChip(icon('clock', { size: 14 }), 'Overdue: how many days late', { className: 'graph-key-late' }),
          picture && picture.outside.length ? keyChip(h('span', { class: 'graph-or-dash-box' }), 'Dashed box: outside this epic') : null,
          chain && blocked ? keyChip(orderSample('is-hot'), 'Red: blocked work and what waits on it') : null,
          picture ? keyChip(orderSample('is-on-path'), 'Highlighted: what it needs first and what waits for it') : null,
        ].filter(Boolean),
      },
    ],
  });
  return { controls, key, summary, content, fix, arrows: stops ? arrowsFor(at, stops, go) : null };
}

// -- where the keyboard was -------------------------------------------------------------------------------

function rememberFocus() {
  const at = document.activeElement;
  if (!at || !root || !root.contains(at)) return null;
  const found = at.closest('[data-fk]');
  return found ? found.getAttribute('data-fk') : null;
}

function restoreFocus(keep) {
  if (arrived) {
    const target = root.querySelector(`[data-fk="${CSS.escape(`r:${arrived}`)}"]`);
    arrived = null;
    if (target) {
      target.scrollIntoView({ block: 'center' });
      target.focus({ preventScroll: true });
      return;
    }
  }
  if (!keep) return;
  let found = root.querySelector(`[data-fk="${CSS.escape(keep)}"]`);
  // A button that is now disabled (the last step) hands the keyboard to the one beside it.
  if (found && found.disabled) found = root.querySelector(`[data-fk="${keep === 'next' ? 'prev' : 'next'}"]:not(:disabled)`);
  if (found) found.focus({ preventScroll: true });
}

// -- when there is nothing to show --------------------------------------------------------------------

// The focus is an acceptance criterion or a note: order is about work, so the work it concerns is offered.
function otherKind(model, ref, parsed) {
  const record = (store[{ rule: 'rules', note: 'notes' }[parsed.kind]] || []).find((one) => one.id === parsed.id);
  const concerned = parsed.kind === 'rule' ? (record?.node ? [record.node] : []) : record?.nodes || [];
  return viewMessage(
    `${kindLabel(parsed.kind)} ${parsed.number} is not work`,
    'Order follows "needs first" links between epics, stories and tasks. Find one of those in the box above and see what must come first.',
    concerned.length
      ? [h('p', { class: 'graph-x-h' }, 'It concerns'), h('div', { class: 'graph-x-chips is-centred' }, concerned.map((one) => (model.node(one) ? h('button', { class: 'chip graph-x-chip-link', type: 'button', title: model.node(one).title, onclick: () => api.setFocus(one) }, kindIcon(model.node(one).kind, 14), codeOf(model.node(one))) : null)))]
      : null,
  );
}

// There is no work in the project, so nothing to order.
function nothingToOrder() {
  return viewMessage(
    'There is nothing to order yet',
    'Order puts the work of an epic in phases, so there has to be some work first. Make an epic on the Board, then say what has to be finished before what.',
    h('div', { class: 'graph-actions' }, viewLink('board', {}, { class: 'link-button' }, 'Go to the Board')),
  );
}

// The epic has no stories or tasks, so there is no work in it to order.
function nothingHere(epic, lead) {
  return viewMessage(
    `Nothing in ${codeOf(epic)} to order yet`,
    `${lead ? `${lead} ` : ''}It has no stories or tasks.`,
    h('div', { class: 'graph-actions' }, viewLink('board', {}, { class: 'link-button' }, 'Go to the Board')),
  );
}
