// The coverage report (#/coverage): the acceptance criteria that are placed nowhere, and the stories that have
// none (on the story or on any of its tasks), as two tables across the whole page. It only reads, and the buttons
// in the rows are the same operations the acceptance criterion pages and dialogs use (put one on a story or
// task, add one to a story), so a refusal reads as it does for an agent. The page reads the report again whenever
// the server has news.

import { Refusal, call } from './api.js';
import { h, plural, put } from './dom.js';
import { dataTable, opens, ruleStatusChip } from './entries.js';
import { refuse } from './forms.js';
import { icon } from './icons.js';
import { codeOf, criteriaCount, kindIcon, ruleCode } from './labels.js';
import { can, itemLink } from './ctx.js';
import { pageHeader } from './page.js';
import { loadingView, placeRequirementDialog, requirementDialog } from './records.js';
import { nodeLink } from './references.js';
import { titleOf } from './store.js';

const quiet = (text) => h('span', { class: 'quiet' }, text);
const section = (name, args) => call('coverage_report', { show: name, limit: 200, ...args });

export const coverageWords = ({ unplacedRules, storiesWithoutRules }) =>
  `${criteriaCount(unplacedRules)} ${unplacedRules === 1 ? 'is' : 'are'} placed nowhere. ${plural(storiesWithoutRules, 'story', 'stories')} ${storiesWithoutRules === 1 ? 'has' : 'have'} no acceptance criterion.`;

let root = null;
let generation = 0;
let drawn = null; // the places of the page that is on show

// Acceptance criteria placed nowhere: each with the way to put it on a story or task.
function unplacedTable(rules) {
  return dataTable({
    caption: 'Acceptance criteria placed nowhere',
    className: 'coverage-table',
    columns: [
      {
        key: 'id',
        label: 'Criterion',
        cell: (rule) => h('th', { scope: 'row', class: 'cell-id' }, itemLink(`rule:${rule.id}`, { class: 'item-code-link', 'data-focus-key': `open:rule:${rule.id}` }, kindIcon('rule', 14), h('span', { class: 'item-code' }, ruleCode(rule.id)))),
      },
      { key: 'title', label: 'Title', cell: (rule) => h('td', { class: 'cell-clamp cell-title' }, h('span', { class: 'clamp', title: rule.title }, itemLink(`rule:${rule.id}`, { class: 'record-title', tabindex: -1 }, rule.title))) },
      { key: 'status', label: 'Status', cell: (rule) => h('td', { class: 'cell-status' }, ruleStatusChip(rule)) },
      ...(can('write')
        ? [
            {
              key: 'place',
              label: h('span', { class: 'sr-only' }, 'Action'),
              cell: (rule) => h('td', { class: 'cell-action' }, h('button', { type: 'button', class: 'small put-requirement', 'data-focus-key': `put:${rule.id}`, onclick: () => placeRequirementDialog(rule) }, 'Put on a story or task…')),
            },
          ]
        : []),
    ],
    rows: rules,
    row: (rule) => ({ class: 'record-line', dataset: { rule: rule.id, kind: 'rule', ref: `rule:${rule.id}`, status: rule.status }, onclick: opens(`rule:${rule.id}`) }),
  });
}

// Stories with no acceptance criterion: each with the way to write one on it.
function bareTable(stories) {
  return dataTable({
    caption: 'Stories with no acceptance criterion',
    className: 'coverage-table',
    columns: [
      {
        key: 'id',
        label: 'Story',
        cell: (story) => h('th', { scope: 'row', class: 'cell-id' }, itemLink(story.ref, { class: 'item-code-link', 'data-focus-key': `open:${story.ref}` }, kindIcon('story', 14), h('span', { class: 'item-code' }, codeOf(story.ref)))),
      },
      { key: 'title', label: 'Title', cell: (story) => h('td', { class: 'cell-clamp cell-title' }, h('span', { class: 'clamp', title: story.title }, itemLink(story.ref, { class: 'record-title', tabindex: -1 }, story.title))) },
      { key: 'epic', label: 'Epic', cell: (story) => h('td', { class: 'cell-clamp cell-held' }, h('span', { class: 'clamp', title: titleOf(story.parent) }, story.parent ? nodeLink(story.parent) : null)) },
      ...(can('write')
        ? [
            {
              key: 'add',
              label: h('span', { class: 'sr-only' }, 'Action'),
              cell: (story) =>
                h('td', { class: 'cell-action' }, h('button', { type: 'button', class: 'small add-requirement', 'data-focus-key': `add:${story.ref}`, onclick: () => requirementDialog({ node: story.ref, onDone: () => load() }) }, icon('plus', { size: 14 }), 'Add an acceptance criterion…')),
            },
          ]
        : []),
    ],
    rows: stories,
    row: (story) => ({ class: 'record-line', dataset: { story: story.ref, dropStory: story.ref, kind: 'story', ref: story.ref }, onclick: opens(story.ref) }),
  });
}

// A heading with how many there are, the table, and a button for the rest when there are more than
// a page. `read` asks for the next page and gives { rows, next }.
function listSection({ id, part, title, total, more, none, make, first, read }) {
  const table = first.length ? make(first) : null;
  const rest = h('p', { class: 'recall-more' });
  let next = more;
  let have = first.length;
  const button = () =>
    put(
      rest,
      next !== null && next !== undefined && have < total
        ? h(
            'button',
            {
              type: 'button',
              class: 'show-more small',
              onclick: async () => {
                try {
                  const page = await read(next);
                  table.add(page.rows);
                  have += page.rows.length;
                  next = page.next;
                  button();
                } catch (error) {
                  if (!(error instanceof Refusal)) throw error;
                  refuse(rest, error.message);
                }
              },
            },
            `Show more (${total - have} not shown)`,
          )
        : null,
    );
  button();
  return h(
    'div',
    { id },
    h('section', { class: 'part coverage-part', dataset: { part } }, h('h2', {}, title, h('span', { class: 'heading-count' }, ` (${total})`)), table ? table.element : h('p', { class: 'quiet none' }, none), rest),
  );
}

async function draw(mine) {
  const report = await call('coverage_report', { show: 'both', limit: 200 });
  if (mine !== generation) return;
  const { summary, lists } = drawn;
  put(summary, coverageWords({ unplacedRules: report.unplaced_rule_count, storiesWithoutRules: report.story_without_rules_count }));
  summary.dataset.unplaced = String(report.unplaced_rule_count);
  summary.dataset.storiesWithout = String(report.story_without_rules_count);
  const keep = document.activeElement && lists.contains(document.activeElement) ? document.activeElement.dataset.focusKey : null;
  put(
    lists,
    listSection({
      id: 'unplaced-rules',
      part: 'unplaced-requirements',
      title: 'Acceptance criteria placed nowhere',
      total: report.unplaced_rule_count,
      more: report.next_unplaced_after_id,
      none: 'Every acceptance criterion is placed.',
      make: unplacedTable,
      first: report.unplaced_rules,
      read: async (after) => {
        const page = await section('unplaced_rules', { unplaced_after_id: after });
        return { rows: page.unplaced_rules, next: page.next_unplaced_after_id };
      },
    }),
    listSection({
      id: 'stories-without-rules',
      part: 'stories-without-requirements',
      title: 'Stories with no acceptance criterion',
      total: report.story_without_rules_count,
      more: report.next_stories_after,
      none: 'Every story has an acceptance criterion.',
      make: bareTable,
      first: report.stories_without_rules,
      read: async (after) => {
        const page = await section('stories_without_rules', { stories_after: after });
        return { rows: page.stories_without_rules, next: page.next_stories_after };
      },
    }),
  );
  if (keep) [...lists.querySelectorAll('[data-focus-key]')].find((one) => one.dataset.focusKey === keep)?.focus();
}

async function load() {
  const mine = ++generation;
  try {
    await draw(mine);
  } catch (error) {
    if (mine !== generation) return;
    if (!(error instanceof Refusal)) throw error;
    put(drawn.summary);
    put(drawn.lists, h('p', { class: 'notice refusal', role: 'alert' }, icon('alert', { size: 16 }), h('span', {}, error.message)));
  }
}

export function mount(element) {
  root = element;
  const summary = h('p', { class: 'sr-only', id: 'coverage-summary', role: 'status' });
  const lists = h('div', { class: 'coverage-lists' }, loadingView());
  drawn = { summary, lists };
  put(
    root,
    h(
      'div',
      { class: 'coverage-page item-page', id: 'coverage-page' },
      pageHeader('Coverage'),
      summary,
      lists,
    ),
  );
  load();
}

export function unmount() {
  generation += 1;
  root = null;
  drawn = null;
}

export function update() {}

// News: the report is read again, which is how an acceptance criterion put on a story leaves the first table.
export async function refresh() {
  if (!drawn) return;
  await load();
}
