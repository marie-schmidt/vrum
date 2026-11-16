// The recall preview: choose an item or a rule and see exactly what `recall` returns for it, in the
// same sections and with the same reasons, so that a person sees what an agent would be told. The
// page asks the server the very same question, so it cannot disagree with it.

import { call } from './api.js';
import { ui } from './ctx.js';
import { h, plural } from './dom.js';
import { decisionEntry, memoryEntry } from './entries.js';
import { parseRef } from './forms.js';
import { store } from './store.js';

const PAGE = 50;

// The sections of a recall, in the order the server gives them, and what each is called.
const SECTIONS = [
  ['active_decisions', 'Decisions in force', 'decision', 'What people decided about this item, and what is still in force.'],
  ['active_principles', 'Principles in force', 'decision', 'Principles apply to everything, so every recall has them.'],
  ['facts', 'Facts', 'memory', 'What is known about it, with where each fact came from.'],
  ['beliefs', 'Beliefs: suggestions, not decisions', 'memory', 'Conclusions drawn from evidence. Weigh them; do not obey them.'],
  ['stale_beliefs', 'Stale beliefs: their evidence changed', 'memory', 'Not in force: read what changed, then revise or retract each one.'],
  ['superseded_decisions', 'Decisions that were replaced', 'decision', 'Shown so that a change can be seen. No longer in force.'],
  ['past_memories', 'Facts and beliefs that were replaced or retracted', 'memory', 'With what replaced them, or why they were wrong.'],
];

export function recallPreview() {
  const kind = h('select', { id: 'recall-kind', name: 'kind' }, h('option', { value: 'node' }, 'Epic, story or task'), h('option', { value: 'rule' }, 'Rule'));
  const which = h('input', { id: 'recall-which', name: 'which', type: 'text', list: 'recall-options', autocomplete: 'off', placeholder: 'an id such as #12, or pick from the list' });
  const options = h('datalist', { id: 'recall-options' });
  const problem = h('p', { class: 'problem', role: 'alert', id: 'recall-problem' });
  const result = h('div', { id: 'recall-result' });
  let shown = null; // the question being shown, "node:12" or "rule:3"
  let generation = 0;

  const fillOptions = () => {
    const nodes = [...store.nodes.values()].map((node) => `#${node.id} ${node.title}`);
    const rules = (store.rules || []).map((rule) => `rule ${rule.id} ${rule.title}`);
    options.replaceChildren(...[...nodes, ...rules].map((value) => h('option', { value })));
  };

  const form = h(
    'form',
    {
      class: 'inline-form',
      id: 'recall-form',
      onsubmit: (event) => {
        event.preventDefault();
        problem.textContent = '';
        const text = which.value.trim();
        const ref = parseRef(kind.value === 'rule' && !/^rule/i.test(text) ? `rule ${text}` : text, ['node', 'rule']);
        if (!ref) {
          problem.textContent = 'Choose an item by its id (such as #12) or a rule (such as rule 3), or pick one from the list.';
          return;
        }
        ui.navigate({ recall: `${ref.type === 'rule' ? 'rule' : 'node'}:${ref.id}` });
      },
    },
    h('label', { for: 'recall-kind' }, 'Recall for'),
    kind,
    which,
    options,
    h('button', { type: 'submit', id: 'recall-show' }, 'Show'),
    h('button', { type: 'button', id: 'recall-clear', onclick: () => ui.navigate({ recall: null }) }, 'Clear'),
  );

  const section = ([name, title, type, note], data, ask) => {
    const entries = data[name];
    const list = h('ul', { class: 'plain entries' });
    const more = h('p', {});
    const draw = (items) => {
      for (const item of items) list.append(type === 'decision' ? decisionEntry(item.decision, { reason: item.why }) : memoryEntry(item.memory, { reason: item.why }));
    };
    draw(entries.items);
    let after = entries.next_after_id;
    let have = entries.items.length;
    const button = () => {
      more.replaceChildren(
        ...(after !== null && after !== undefined
          ? [
              h(
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
                `Show more (${entries.count - have} not shown)`,
              ),
            ]
          : []),
      );
    };
    button();
    return h(
      'section',
      { class: 'recall-section', dataset: { section: name, count: entries.count } },
      h('h3', {}, `${title} (${entries.count})`),
      h('p', { class: 'quiet' }, note),
      entries.items.length ? list : h('p', { class: 'quiet none' }, 'None.'),
      more,
    );
  };

  async function show(spec) {
    const mine = ++generation;
    shown = spec;
    problem.textContent = '';
    if (!spec) {
      result.replaceChildren();
      return;
    }
    const [type, id] = spec.split(':');
    if (document.activeElement !== which) {
      kind.value = type === 'rule' ? 'rule' : 'node';
      which.value = type === 'rule' ? `rule ${id}` : `#${id}`;
    }
    fillOptions();
    const ask = (extra = {}) => call('recall', { [type === 'rule' ? 'rule_id' : 'node_id']: Number(id), limit: PAGE, ...extra });
    let data;
    try {
      data = await ask();
    } catch (error) {
      if (mine !== generation) return;
      result.replaceChildren();
      problem.textContent = error.message;
      return;
    }
    if (mine !== generation) return;
    const name = type === 'rule' ? (store.rules || []).find((rule) => rule.id === Number(id))?.title : store.nodes.get(Number(id))?.title;
    const total = SECTIONS.reduce((sum, [section_]) => sum + data[section_].count, 0);
    result.replaceChildren(
      h('p', { class: 'recall-heading', role: 'status' }, `What an agent calling recall for ${type === 'rule' ? 'rule' : 'item'} #${id}${name ? `, “${name}”,` : ''} is told: ${plural(total, 'entry', 'entries')}.`),
      h('p', { class: 'quiet' }, 'Only exact links are followed: what is linked to the parent or the children of this item is not included, and nothing is inferred.'),
      ...SECTIONS.map((entry) => section(entry, data, ask)),
    );
  }

  const element = h(
    'details',
    { class: 'recall-preview', id: 'recall-preview' },
    h('summary', {}, 'Recall preview: what an agent would be told'),
    h('p', { class: 'quiet' }, 'Choose an epic, a story, a task or a rule. This is the answer of the recall tool for it, in its own sections and with its own reasons.'),
    form,
    problem,
    result,
  );
  return {
    element,
    show,
    current: () => shown,
    // Show the question again, because something changed on the server.
    refresh: () => (shown ? show(shown) : null),
  };
}
