// What recall says for one item or requirement, in the same sections and with the same reasons as
// the `recall` tool gives an agent: decisions in force, project-wide decisions, facts, and the
// past ones that were replaced or withdrawn, and, when asked for, what is alike in meaning but not
// linked. The board asks the server the very same question, so it cannot disagree with it. Every
// entry shows whether a person confirmed it and whether it is still in force.

import { Refusal, call } from './api.js';
import { recordLink } from './ctx.js';
import { h, plural, put } from './dom.js';
import { recordEntry } from './entries.js';
import { uid } from './forms.js';
import { kindWords, referenceKindOf } from './references.js';

const PAGE = 50;

// The sections of a recall, in the order the server gives them, and what each is called.
const SECTIONS = [
  ['active_decisions', 'Decisions in force', 'What people decided about this, and is still in force.'],
  ['project_wide_decisions', 'Project-wide decisions', 'Decisions that apply throughout the project, whether or not they are linked to this.'],
  ['facts', 'Facts', 'What is known about it, with where each fact came from and whether a person confirmed it.'],
  ['past_decisions', 'Past decisions', 'Replaced or withdrawn. Shown so that a change can be seen: none of them is in force.'],
  ['past_facts', 'Past facts', 'Replaced or withdrawn. None of them is in force.'],
];

// `target` is { node_id } or { rule_id }.
export const askRecall = (target, extra = {}) => call('recall', { ...target, limit: PAGE, ...extra });

const recordOf = (item) => item.decision || item.fact || null;

function listSection([name, title, note], entries, ask, hideEmpty) {
  const items = (entries?.items || []).filter(recordOf);
  if (hideEmpty && !items.length) return null;
  const list = h('ul', { class: 'plain entries' });
  const more = h('p', {});
  const draw = (page) => {
    for (const item of page) if (recordOf(item)) list.append(recordEntry(recordOf(item), { why: item.why }));
  };
  draw(items);
  let after = entries?.next_after_id ?? null;
  let have = items.length;
  const count = entries?.count ?? 0;
  const button = () => {
    put(
      more,
      after !== null && after !== undefined
        ? h(
            'button',
            {
              type: 'button',
              class: 'show-more',
              onclick: async () => {
                const next = await ask({ show: name, after_id: after });
                draw(next[name].items);
                have += next[name].items.length;
                after = next[name].next_after_id;
                button();
              },
            },
            `Show more (${count - have} not shown)`,
          )
        : null,
    );
  };
  button();
  return h(
    'section',
    { class: 'recall-section', dataset: { section: name, count } },
    h('h4', {}, `${title} (${count})`),
    h('p', { class: 'quiet' }, note),
    items.length ? list : h('p', { class: 'quiet none' }, 'None.'),
    more,
  );
}

// What is alike in meaning but not linked: suggestions from the index, never records of this item.
function similarSection(similar) {
  if (!similar) return null;
  const body = similar.items.length
    ? h(
        'ul',
        { class: 'plain' },
        similar.items.map((item) =>
          h(
            'li',
            { class: 'similar-item', dataset: { similar: `${item.item_type}:${item.id}` } },
            recordLink(referenceKindOf(item.item_type), item.id, { class: 'item-link' }, `${kindWords(item.item_type).label} #${item.id}: ${item.title}`),
            ` ${item.similarity === null || item.similarity === undefined ? 'by words' : `${item.similarity.toFixed(2)} alike`}`,
          ),
        ),
      )
    : h('p', { class: 'quiet none' }, similar.index?.meaning && similar.index.meaning !== 'used' ? similar.index.note : 'Nothing in the index looks like this.');
  return h(
    'section',
    { class: 'recall-section', dataset: { section: 'similar', count: similar.count ?? similar.items.length } },
    h('h4', {}, `Alike in meaning, not linked (${similar.count ?? similar.items.length})`),
    h('p', { class: 'quiet' }, 'Suggestions from the index. Nobody linked these to this item.'),
    body,
  );
}

// The sections of one recall answer. `ask(extra)` reads another page of one section. `hideEmpty`
// leaves out the sections with nothing in them, as the detail panel does.
export function recallSections(data, ask, { hideEmpty = true } = {}) {
  return [...SECTIONS.map((entry) => listSection(entry, data[entry[0]], ask, hideEmpty)), similarSection(data.similar)].filter(Boolean);
}

export const recallTotal = (data) => SECTIONS.reduce((sum, [name]) => sum + (data[name]?.count ?? 0), 0);

// Everything the panel shows for a recall: the sections, a line saying how many entries there are,
// and a switch for the entries that are alike in meaning. The switch reads again by itself and
// never touches anything else on the panel, so a draft typed there is safe.
export function recallBlock(target, data, { withSimilar = false, onSimilar = () => {} } = {}) {
  const body = h('div', { class: 'recall-sections' });
  const summary = h('p', { class: 'recall-heading quiet', role: 'status' });
  const problem = h('p', { class: 'problem', role: 'alert' });
  let similar = withSimilar;
  const extra = (more = {}) => ({ ...more, ...(similar ? { with_similar: true } : {}) });
  const draw = (answer) => {
    const total = recallTotal(answer);
    put(summary, `What an agent calling recall for this is told: ${plural(total, 'entry', 'entries')}. Only exact links are followed, and nothing is inferred.`);
    const sections = recallSections(answer, (more) => askRecall(target, extra(more)));
    put(body, sections.length ? sections : h('p', { class: 'quiet none' }, 'Nothing is recorded for it yet.'));
  };
  const toggleId = uid('similar-');
  const toggle = h('input', {
    type: 'checkbox',
    id: toggleId,
    checked: withSimilar,
    onchange: async (event) => {
      // Choosing what to look at is not an unsaved edit of the item.
      event.stopPropagation();
      problem.textContent = '';
      similar = toggle.checked;
      onSimilar(similar);
      try {
        draw(await askRecall(target, extra()));
      } catch (error) {
        if (!(error instanceof Refusal)) throw error;
        problem.textContent = error.message;
      }
    },
  });
  draw(data);
  return h(
    'div',
    { class: 'recall-block', dataset: { recall: Object.keys(target)[0] } },
    summary,
    h('label', { class: 'check', for: toggleId }, toggle, ' Also show what is alike in meaning but not linked'),
    problem,
    body,
  );
}
