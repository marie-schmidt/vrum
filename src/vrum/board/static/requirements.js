// The Lists page's acceptance criteria: every one in scope, grouped by the story or task it is on, with
// the ones that are placed nowhere first, and the coverage of the stories. A person can write one, change
// it, and put it on a story or task or move it to another, by dragging it onto a story or task or from a
// dialog. Each of those is the same operation an agent calls, at the version the person saw, so the
// server decides and a refusal reads as it does for an agent. The list keeps no data of its own.

import { Refusal, call } from './api.js';
import { coverageWords } from './coverage.js';
import { can, hrefTo, notify } from './ctx.js';
import { h, plural, put } from './dom.js';
import { icon } from './icons.js';
import { codeOf, criteriaCount, kindIcon, parseNodeRef, ruleCode } from './labels.js';
import { renderMarkdown } from './markdown.js';
import { mentionedInDetails } from './references.js';
import { requirementDialog, openRecord, placeRequirementDialog, ruleStatusChip } from './records.js';
import { epicOf, store, sync, titleOf } from './store.js';
import { connectionsDetails, matchesText, sectionFrame, selectOf, textInput, walkRows } from './list-parts.js';

const STATUS_FILTER = [
  ['live', 'Open and met'],
  ['open', 'Open'],
  ['met', 'Met'],
  ['dropped', 'Dropped'],
  ['all', 'All'],
];
const PLACEMENT_FILTER = [
  ['', 'Placed or not'],
  ['unplaced', 'Not placed'],
];

// Stories in the order of their numbers.
const numberOf = (ref) => parseNodeRef(ref)?.number ?? 0;
const byStory = (a, b) => numberOf(a.ref) - numberOf(b.ref);

// Where a story or a task sorts: by its epic, then its story, a story before its tasks.
function orderOf(ref) {
  const node = store.nodes.get(ref);
  if (!node) return [0, 0, 0, 0];
  const story = node.kind === 'task' ? store.nodes.get(node.parent) : node;
  const epic = story ? epicOf(story) : null;
  return [epic?.number ?? 0, story?.number ?? 0, node.kind === 'task' ? 1 : 0, node.kind === 'task' ? node.number : 0];
}
const byPlace = (a, b) => {
  const [x, y] = [orderOf(a), orderOf(b)];
  return x.reduce((found, one, at) => found || one - y[at], 0);
};

// How the acceptance criteria stand against the stories: what the coverage report says, worked out from
// the board's copy so that it is always the same as the lists. A story is covered when it, or one of its
// tasks, holds one that is not dropped.
function coverage() {
  const live = (store.rules || []).filter((rule) => rule.status !== 'dropped');
  const covered = new Set();
  for (const rule of live) {
    const node = rule.node === null ? null : store.nodes.get(rule.node);
    if (node) covered.add(node.kind === 'task' ? node.parent : node.ref);
  }
  return {
    unplaced: live.filter((rule) => rule.node === null).length,
    stories: [...store.nodes.values()].filter((node) => node.kind === 'story' && node.status !== 'dropped' && !covered.has(node.ref)).sort(byStory),
  };
}

export function createRequirements() {
  const state = { status: 'live', placement: '', text: '' };
  let last = null; // what the page last handed over
  let dragging = null; // the acceptance criterion being dragged
  const status = selectOf(STATUS_FILTER, state.status);
  const placement = selectOf(PLACEMENT_FILTER, state.placement);
  const text = textInput('Words in a criterion');
  const frame = sectionFrame({
    name: 'requirements',
    title: 'Acceptance criteria',
    buttons: can('write') ? [h('button', { type: 'button', id: 'new-requirement', class: 'small', onclick: () => requirementDialog({}) }, icon('plus', { size: 14 }), 'Add an acceptance criterion…')] : [],
    controls: [
      ['status', 'Status', status],
      ['placement', 'Placed', placement],
      ['text', 'Find', text],
    ],
    onFilter: () => {
      state.status = status.value;
      state.placement = placement.value;
      state.text = text.value;
      if (last) draw(last);
    },
  });
  const summary = h('p', { class: 'gv-summary ctx-summary', id: 'requirements-summary', role: 'status' });
  const leftOut = h('p', { class: 'ctx-left-out quiet', id: 'requirements-left-out' });
  const unplaced = h('div', { id: 'unplaced-group' });
  const groups = h('div', { id: 'requirement-groups' });
  const without = h('details', { class: 'gv-without', id: 'stories-without-requirements' });
  frame.body.append(summary, leftOut, unplaced, groups, without);
  walkRows(frame.element);

  // -- the rows --------------------------------------------------------------------------------

  function row(dot, ctx) {
    const rule = dot.record;
    const chosen = ctx.cfg.sel?.key === dot.key;
    return h(
      'li',
      {
        class: `gv-record ctx-row requirement status-${rule.status}${chosen ? ' selected' : ''}`,
        draggable: can('write') ? 'true' : null,
        'aria-current': chosen ? 'true' : null,
        dataset: { key: dot.key, kind: 'rule', type: 'rule', id: rule.id, status: rule.status, node: rule.node ?? '' },
        onclick: (event) => {
          if (!event.target.closest('button, a, summary, details, select, input')) openRecord('rule', rule.id);
        },
        ondragstart: (event) => {
          dragging = rule.id;
          event.dataTransfer.setData('application/x-vrum-rule', String(rule.id));
          event.dataTransfer.setData('text/plain', `${ruleCode(rule.id)} ${rule.title}`);
          event.dataTransfer.effectAllowed = 'move';
        },
        ondragend: () => {
          dragging = null;
          document.querySelectorAll('.over').forEach((el) => el.classList.remove('over'));
        },
      },
      h('div', { class: 'record-chips' }, h('span', { class: 'chip kind kind-rule' }, kindIcon('rule', 14), ruleCode(rule.id)), ruleStatusChip(rule)),
      h('button', { type: 'button', class: 'link gv-open', onclick: () => openRecord('rule', rule.id) }, rule.title),
      rule.body ? renderMarkdown(rule.body, { className: 'markdown entry-text row-text' }) : null,
      can('write')
        ? h(
            'div',
            { class: 'row-actions' },
            h('button', { type: 'button', class: 'edit-requirement small', onclick: () => requirementDialog({ rule }) }, 'Edit'),
            h('button', { type: 'button', class: 'put-requirement small', onclick: () => placeRequirementDialog(rule) }, rule.node === null ? 'Put on a story or task…' : 'Move to another story or task…'),
          )
        : null,
      mentionedInDetails('rule', rule.id),
      connectionsDetails(dot, ctx),
    );
  }

  // What the filters other than the status keep.
  const fits = (rule) => (state.placement !== 'unplaced' || rule.node === null) && matchesText(state.text, rule.id, rule.title, rule.body ?? rule.body_preview, rule.status);

  function keeps(rule) {
    if (state.status === 'live' && rule.status === 'dropped') return false;
    if (!['live', 'all'].includes(state.status) && rule.status !== state.status) return false;
    return fits(rule);
  }

  // -- drawing ---------------------------------------------------------------------------------

  function draw(ctx) {
    last = ctx;
    const hadFocus = frame.body.contains(document.activeElement) ? document.activeElement.closest('[data-key]')?.dataset.key : null;
    const inScope = [...ctx.shown.dots.values()].filter((dot) => dot.type === 'rule');
    const kept = inScope.filter((dot) => keeps(dot.record));
    frame.setCount(kept.length, inScope.length);

    // What the default of showing the open and met ones leaves out is said, with the way to see it.
    const dropped = state.status === 'live' ? inScope.filter((dot) => dot.record.status === 'dropped' && fits(dot.record)).length : 0;
    put(
      leftOut,
      dropped ? `${plural(dropped, 'dropped acceptance criterion', 'dropped acceptance criteria')} ${dropped === 1 ? 'is' : 'are'} not shown. ` : null,
      dropped
        ? h(
            'button',
            {
              type: 'button',
              class: 'link show-left-out',
              onclick: () => {
                state.status = 'all';
                status.value = 'all';
                if (last) draw(last);
              },
            },
            dropped === 1 ? 'Show it.' : 'Show them.',
          )
        : null,
    );
    leftOut.hidden = dropped === 0;

    const cover = coverage();
    summary.replaceChildren(
      icon('info', { size: 16 }),
      h('span', {}, `${coverageWords({ unplacedRules: cover.unplaced, storiesWithoutRules: cover.stories.length })} `, h('a', { class: 'gv-coverage-link', id: 'show-coverage', href: hrefTo('coverage') }, 'See the coverage report')),
    );
    summary.dataset.unplaced = String(cover.unplaced);
    summary.dataset.storiesWithout = String(cover.stories.length);

    const loose = kept.filter((dot) => dot.record.node === null).sort((a, b) => b.record.id - a.record.id); // the newest on top
    const onNode = new Map();
    for (const dot of kept.filter((item) => item.record.node !== null).sort((a, b) => a.record.id - b.record.id)) {
      if (!onNode.has(dot.record.node)) onNode.set(dot.record.node, []);
      onNode.get(dot.record.node).push(dot);
    }
    const holders = [...onNode.keys()].sort(byPlace);

    unplaced.replaceChildren(
      ...(inScope.length || cover.unplaced
        ? [
            h(
              'section',
              { class: 'gv-group unplaced', dataset: { group: 'unplaced' } },
              h('h3', {}, `Not placed (${loose.length})`),
              loose.length ? h('ul', { class: 'plain ctx-rows' }, loose.map((dot) => row(dot, ctx))) : h('p', { class: 'quiet' }, cover.unplaced ? 'None match the filters.' : 'Every acceptance criterion is placed.'),
            ),
          ]
        : []),
    );
    put(
      groups,
      holders.map((ref) => {
        const node = store.nodes.get(ref);
        // A story says its epic, a task its story.
        const above = node?.parent ? store.nodes.get(node.parent) : null;
        const { kind, number } = parseNodeRef(ref);
        return h(
          'section',
          { class: 'gv-group', dataset: { group: ref } },
          h(
            'h3',
            { class: 'gv-group-head', dataset: { dropNode: ref } },
            kindIcon(kind, 16),
            h('button', { type: 'button', class: 'link', onclick: () => openRecord(kind, number) }, node ? `${codeOf(ref)} ${node.title}` : `${kind === 'task' ? 'Task' : 'Story'} ${codeOf(ref)}`),
            above ? h('span', { class: 'quiet' }, `in ${kind === 'task' ? 'story' : 'epic'} ${codeOf(above.ref)} ${above.title}`) : null,
            h('span', { class: 'quiet' }, `(${criteriaCount(onNode.get(ref).length)})`),
          ),
          h('ul', { class: 'plain ctx-rows' }, onNode.get(ref).map((dot) => row(dot, ctx))),
        );
      }),
      !holders.length && !loose.length ? h('p', { class: `${inScope.length ? 'quiet' : 'empty'} ctx-none` }, inScope.length ? 'No acceptance criterion matches the filters.' : 'None yet.') : null,
    );

    const open = without.open;
    put(
      without,
      h('summary', {}, `Stories with no acceptance criterion (${cover.stories.length})`),
      cover.stories.length
        ? h(
            'ul',
            { class: 'plain bare-stories' },
            cover.stories.slice(0, 100).map((story) =>
              h(
                'li',
                { class: 'gv-bare-story', dataset: { story: story.ref, dropNode: story.ref } },
                kindIcon('story', 14),
                h('button', { type: 'button', class: 'link', onclick: () => openRecord('story', story.number) }, `${codeOf(story.ref)} ${story.title}`),
                can('write') ? h('button', { type: 'button', class: 'add-requirement small', onclick: () => requirementDialog({ node: story.ref }) }, icon('plus', { size: 14 }), 'Add an acceptance criterion…') : null,
              ),
            ),
          )
        : h('p', { class: 'quiet' }, 'Every story has an acceptance criterion.'),
      cover.stories.length > 100 ? h('p', { class: 'quiet' }, `${cover.stories.length - 100} more: the coverage report lists them all.`) : null,
    );
    without.open = open;
    if (hadFocus) frame.body.querySelector(`[data-key="${hadFocus}"] .gv-open`)?.focus();
  }

  function mark(key) {
    for (const item of frame.body.querySelectorAll('.gv-record.selected')) {
      item.classList.remove('selected');
      item.removeAttribute('aria-current');
    }
    const item = key ? frame.body.querySelector(`.gv-record[data-key="${key}"]`) : null;
    item?.classList.add('selected');
    item?.setAttribute('aria-current', 'true');
    return item;
  }

  // -- dragging an acceptance criterion onto a story or task ----------------------------------------------

  // Listening on the whole page, so that any story or task shown on it can be dropped on.
  const dropTarget = (event) => (dragging ? event.target.closest?.('[data-drop-node]') : null);
  const over = (event) => {
    const target = dropTarget(event);
    if (!target) return;
    event.preventDefault();
    target.classList.add('over');
  };
  const leave = (event) => dropTarget(event)?.classList.remove('over');
  const dropped = async (event) => {
    const target = dropTarget(event);
    if (!target) return;
    event.preventDefault();
    target.classList.remove('over');
    const rule = (store.rules || []).find((item) => item.id === dragging);
    dragging = null;
    if (rule) await dropIn(rule, target.dataset.dropNode);
  };
  document.addEventListener('dragover', over);
  document.addEventListener('dragleave', leave);
  document.addEventListener('drop', dropped);

  return {
    element: frame.element,
    draw,
    select: mark,
    busy: () => dragging !== null,
    destroy() {
      document.removeEventListener('dragover', over);
      document.removeEventListener('dragleave', leave);
      document.removeEventListener('drop', dropped);
    },
  };
}

// -- placing by dragging ----------------------------------------------------------------------------

// An acceptance criterion placed nowhere is put on a story or task; one that is on one is moved, in one
// step. It is the one operation the dialog (placeRequirementDialog) and an agent use. `node` is the
// reference of the story or task.
async function dropIn(rule, node) {
  if (rule.node === node) return;
  const placing = rule.node === null;
  try {
    await call('place_rule', { rule_id: rule.id, expected_version: rule.version, node });
    notify(`${placing ? 'Put' : 'Moved'} “${rule.title}” ${placing ? 'on' : 'to'} “${titleOf(node)}”.`);
    await sync();
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
  }
}
