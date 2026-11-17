// The coverage report: requirements that are in no story, and stories that have no requirement. It
// only reads. It is shown in the side panel (panel=coverage) and, in full, in Graph's list of
// requirements, so the lists are built here.

import { call } from './api.js';
import { ui } from './ctx.js';
import { clear, h, plural, put } from './dom.js';
import { kindIcon } from './labels.js';
import { closeButton, loadingView } from './records.js';
import { nodeLink, openLink } from './references.js';

function list(title, key, items, render, total, more) {
  return h(
    'section',
    { class: 'part', dataset: { part: key } },
    h('h3', {}, `${title} (${total})`),
    items.length ? h('ul', { class: 'plain coverage-list' }, items.map(render)) : h('p', { class: 'quiet' }, 'None.'),
    more,
  );
}

async function section(name, args) {
  return call('coverage_report', { show: name, limit: 200, ...args });
}

// The lists, drawn into `into`. A story with no requirement can be dropped on: data-drop-story
// marks it. Returns the counts, so that a page can say them in a sentence.
export async function drawCoverage(into) {
  const report = await call('coverage_report', { show: 'both', limit: 200 });
  const unplaced = [...report.unplaced_rules];
  const stories = [...report.stories_without_rules];
  const rules = h('div', { id: 'unplaced-rules' });
  const without = h('div', { id: 'stories-without-rules' });
  const drawRules = () =>
    rules.replaceChildren(
      list(
        'Requirements in no story',
        'unplaced-requirements',
        unplaced,
        (rule) => h('li', { 'data-rule': rule.id }, openLink('rule', rule.id, `Requirement #${rule.id} (${rule.status}): ${rule.title}`, kindIcon('rule', 14))),
        report.unplaced_rule_count,
        report.next_unplaced_after_id && unplaced.length < report.unplaced_rule_count
          ? h(
              'button',
              {
                type: 'button',
                class: 'small',
                onclick: async () => {
                  const more = await section('unplaced_rules', { unplaced_after_id: report.next_unplaced_after_id });
                  unplaced.push(...more.unplaced_rules);
                  report.next_unplaced_after_id = more.next_unplaced_after_id;
                  drawRules();
                },
              },
              'Show more',
            )
          : null,
      ),
    );
  const drawStories = () =>
    without.replaceChildren(
      list(
        'Stories with no requirement',
        'stories-without-requirements',
        stories,
        (story) => h('li', { 'data-story': story.ref, 'data-drop-story': story.ref }, kindIcon('story', 14), nodeLink(story.ref)),
        report.story_without_rules_count,
        report.next_stories_after && stories.length < report.story_without_rules_count
          ? h(
              'button',
              {
                type: 'button',
                class: 'small',
                onclick: async () => {
                  const more = await section('stories_without_rules', { stories_after: report.next_stories_after });
                  stories.push(...more.stories_without_rules);
                  report.next_stories_after = more.next_stories_after;
                  drawStories();
                },
              },
              'Show more',
            )
          : null,
      ),
    );
  drawRules();
  drawStories();
  into.replaceChildren(rules, without);
  return { unplacedRules: report.unplaced_rule_count, storiesWithoutRules: report.story_without_rules_count };
}

export const coverageWords = ({ unplacedRules, storiesWithoutRules }) =>
  `${plural(unplacedRules, 'requirement')} ${unplacedRules === 1 ? 'is' : 'are'} in no story. ${plural(storiesWithoutRules, 'story', 'stories')} ${storiesWithoutRules === 1 ? 'has' : 'have'} no requirement.`;

export async function renderCoverage(root) {
  if (!root.querySelector('.coverage')) put(root, loadingView());
  const lists = h('div', { class: 'coverage-lists' });
  const view = h(
    'article',
    { class: 'coverage detail' },
    h(
      'header',
      { class: 'detail-head' },
      h('p', { class: 'detail-label' }, kindIcon('rule', 14), h('span', {}, 'Requirements / report')),
      h('h2', { id: 'coverage-title', class: 'detail-title', tabindex: -1 }, 'Coverage'),
      closeButton('Close the coverage report', () => ui.navigate({ panel: null })),
    ),
    h('p', { class: 'quiet' }, 'What is not matched up yet. Dropped requirements and dropped stories are left out.'),
    lists,
  );
  await drawCoverage(lists);
  clear(root).append(view);
  return { focus: () => root.querySelector('#coverage-title').focus() };
}
