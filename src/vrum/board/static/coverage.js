// The coverage report: rules that are in no story, and stories that have no rule. It only reads.
// It is shown in the side panel and, in full, on the Rules page, so the lists are built here.

import { call } from './api.js';
import { ui } from './ctx.js';
import { clear, h, plural } from './dom.js';
import { itemLink } from './card.js';

function list(title, items, render, total, more) {
  return h(
    'section',
    { class: 'part' },
    h('h3', {}, `${title} (${total})`),
    items.length ? h('ul', { class: 'plain' }, items.map(render)) : h('p', { class: 'quiet' }, 'None.'),
    more,
  );
}

async function section(root, name, args) {
  return call('coverage_report', { show: name, limit: 200, ...args });
}

// The lists, drawn into `into`. A story with no rule can be dropped on: data-drop-story marks it.
// Returns the counts, so that a page can say them in a sentence.
export async function drawCoverage(into) {
  const report = await call('coverage_report', { show: 'both', limit: 200 });
  const unplaced = [...report.unplaced_rules];
  const stories = [...report.stories_without_rules];
  const rules = h('div', { id: 'unplaced-rules' });
  const without = h('div', { id: 'stories-without-rules' });
  const drawRules = () =>
    rules.replaceChildren(
      list(
        'Rules in no story',
        unplaced,
        (rule) => h('li', { 'data-rule': rule.id }, `Rule #${rule.id} (${rule.status}): ${rule.title}`),
        report.unplaced_rule_count,
        report.next_unplaced_after_id && unplaced.length < report.unplaced_rule_count
          ? h('button', { type: 'button', onclick: async () => {
              const more = await section(into, 'unplaced_rules', { unplaced_after_id: report.next_unplaced_after_id });
              unplaced.push(...more.unplaced_rules);
              report.next_unplaced_after_id = more.next_unplaced_after_id;
              drawRules();
            } }, 'Show more')
          : null,
      ),
    );
  const drawStories = () =>
    without.replaceChildren(
      list(
        'Stories with no rule',
        stories,
        (story) => h('li', { 'data-story': story.id, 'data-drop-story': story.id }, itemLink(story.id)),
        report.story_without_rules_count,
        report.next_stories_after_id && stories.length < report.story_without_rules_count
          ? h('button', { type: 'button', onclick: async () => {
              const more = await section(into, 'stories_without_rules', { stories_after_id: report.next_stories_after_id });
              stories.push(...more.stories_without_rules);
              report.next_stories_after_id = more.next_stories_after_id;
              drawStories();
            } }, 'Show more')
          : null,
      ),
    );
  drawRules();
  drawStories();
  into.replaceChildren(rules, without);
  return { unplacedRules: report.unplaced_rule_count, storiesWithoutRules: report.story_without_rules_count };
}

export const coverageWords = ({ unplacedRules, storiesWithoutRules }) =>
  `${plural(unplacedRules, 'rule')} ${unplacedRules === 1 ? 'is' : 'are'} in no story. ${plural(storiesWithoutRules, 'story', 'stories')} ${storiesWithoutRules === 1 ? 'has' : 'have'} no rule.`;

export async function renderCoverage(root) {
  const lists = h('div', { class: 'coverage-lists' });
  const view = h(
    'article',
    { class: 'coverage' },
    h(
      'header',
      {},
      h('h2', { id: 'coverage-title', tabindex: -1 }, 'Coverage'),
      h('button', { type: 'button', class: 'close', 'aria-label': 'Close the coverage report', onclick: () => ui.navigate({ panel: null }) }, '✕'),
    ),
    h('p', { class: 'quiet' }, 'What is not matched up yet. Dropped rules and dropped stories are left out.'),
    lists,
  );
  await drawCoverage(lists);
  clear(root).append(view);
  return { focus: () => root.querySelector('#coverage-title').focus() };
}
