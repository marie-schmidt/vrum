// The filter box that the Table and the Roadmap share. The text is the same filter language that
// list_nodes takes as `query`, and it is the server that reads it: the page only sends it, shows
// what the server answers, and keeps the text in the address (`q`).

import { Refusal, callAll } from './api.js';
import { h } from './dom.js';
import { store } from './store.js';

const EXAMPLES = [
  ['kind:task status:ready,in_progress -claimed priority:high OR kind:story', 'ready or running high-priority tasks that nobody holds, and every story'],
  ['is:overdue', 'what is overdue'],
  ['target:2026-11-01..2026-12-31', 'due in that range (leave one end off for “from” or “until”)'],
  ['no:target', 'with no target date; no:start, no:claimed, no:parent and no:body work too'],
  ['claimed:ann,bob', 'held by Ann or Bob; claimed on its own means held by anyone'],
  ['invoice -draft', 'words in the title or body; quote a phrase to keep it together'],
  ['epic:12', 'in epic 12 (also story:, parent: and id:)'],
];

export function filterBar({ value, onSubmit }) {
  const input = h('input', {
    type: 'search',
    id: 'filter',
    name: 'q',
    value,
    autocomplete: 'off',
    spellcheck: 'false',
    placeholder: 'kind:task status:ready -claimed OR is:overdue',
    'aria-describedby': 'filter-problem',
  });
  const problem = h('p', { class: 'problem', id: 'filter-problem', role: 'alert' });
  const form = h(
    'form',
    {
      class: 'filter',
      role: 'search',
      onsubmit: (event) => {
        event.preventDefault();
        onSubmit(input.value.trim());
      },
    },
    h('label', { for: 'filter' }, 'Filter'),
    input,
    h('button', { type: 'submit', class: 'primary' }, 'Apply'),
    h(
      'button',
      {
        type: 'button',
        id: 'clear-filter',
        onclick: () => {
          input.value = '';
          onSubmit('');
        },
      },
      'Clear',
    ),
  );
  const help = h(
    'details',
    { class: 'filter-help' },
    h('summary', {}, 'How to filter'),
    h(
      'p',
      {},
      'Type words and field:value terms. Terms side by side must all hold; OR between groups means either group; a leading - leaves matches out; a comma means any of these values. The fields are kind, status, priority, claimed, parent, epic, story, id, start, target, is and no. Agents use the same text as the query of list_nodes.',
    ),
    h(
      'ul',
      { class: 'plain' },
      EXAMPLES.map(([text, meaning]) =>
        h(
          'li',
          {},
          h('button', { type: 'button', class: 'link example', onclick: () => { input.value = text; onSubmit(text); } }, h('code', {}, text)),
          ` ${meaning}`,
        ),
      ),
    ),
  );
  return {
    form,
    help,
    problem,
    input,
    setProblem(text) {
      problem.textContent = text || '';
    },
    setValue(text) {
      if (document.activeElement !== input) input.value = text;
    },
  };
}

// The nodes the text asks for, from the server, or null with the server's words under the filter
// box when it cannot be understood. An empty filter is every node, which the board already has.
export async function loadRows(text, bar) {
  try {
    const rows = text ? await callAll('list_nodes', { query: text }) : [...store.nodes.values()];
    bar.setProblem('');
    return rows;
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    bar.setProblem(error.message);
    return null;
  }
}
