// The filter box that the Board, the Roadmap and Lists share. The text is the
// same filter language that the agents' list of work takes as `query`, and it is the server that
// reads it: the page only sends it, shows what the server answers, and keeps the text in the address
// (`q`). The help is a hover card on the small "i" at the end of the box (hovercard.js). A page may
// add a sentence of its own to it (`note`).

import { Refusal, callAll } from './api.js';
import { h } from './dom.js';
import { hoverCard } from './hovercard.js';
import { icon } from './icons.js';
import { store } from './store.js';

// Every form the language has: [what to type, what it does, examples].
const FORMS = [
  ['Words', 'search the title and details. Quote a phrase.', ['invoice "reset password"']],
  ['field:value', 'narrows by a field: kind, status, priority, size, claimed, parent, epic, story, number, start, target, is or no.', ['kind:task', 'size:m,l']],
  ['A comma', 'means any of these values.', ['status:ready,in_progress']],
  ['OR', 'starts a second group. Terms side by side must all hold.', ['priority:urgent OR is:overdue']],
  ['A leading -', 'leaves the matches out.', ['-status:done']],
  ['no:', 'finds items where a field is empty: start, target, claimed, parent, body (no details), criteria (no acceptance criteria), size or waiting.', ['no:target', 'no:size', 'no:criteria']],
  ['Dates', 'take a day or a range. Leave one end open.', ['target:2026-11-01..2026-12-31']],
  ['is:', 'takes overdue, conflict, waiting, claimed, lapsed, held or unverified (blocked, held by nobody and waiting for a verifier). held is work kept back by a gate or a plan that waits for approval.', ['is:overdue', 'is:held', 'is:unverified']],
  ['claimed and waiting', 'alone mean held by someone, and waiting on something.', ['-claimed', 'waiting']],
  ['epic: and story:', 'take everything under it, by the number in its code (E-3 is epic:3). parent: is only what is directly under it, and number: goes with kind:.', ['epic:3', 'kind:story number:12']],
];

const TRY = [
  ['kind:task status:ready -claimed priority:high', 'Ready, high priority, nobody holds it'],
  ['epic:3 status:blocked,in_progress', 'Running or stuck under E-3'],
  ['target:2026-12-01..2026-12-31 -claimed', 'Due in December, nobody holds it'],
];

// What the "i" shows: the forms as a tidy list with an example each, then a few whole filters to start from.
function helpContent(note) {
  return [
    h('p', { class: 'hovercard-title' }, 'How to filter'),
    h('p', {}, 'Type words and field:value terms, then press Enter or Apply. Agents use the same words when they list work.'),
    h(
      'dl',
      { class: 'filter-forms' },
      FORMS.map(([lead, what, examples]) => [
        h('dt', {}, h('strong', {}, lead), ' ', what),
        h('dd', {}, examples.map((text) => h('code', {}, text))),
      ]),
    ),
    h('p', { class: 'hovercard-title' }, 'Try these'),
    h('dl', { class: 'filter-forms filter-tries' }, TRY.map(([text, what]) => [h('dt', {}, h('code', {}, text)), h('dd', {}, what)])),
    note ? h('p', { class: 'filter-note' }, note) : null,
  ];
}

export function filterBar({ value, onSubmit, note }) {
  const input = h('input', {
    type: 'search',
    id: 'filter',
    name: 'q',
    value,
    autocomplete: 'off',
    spellcheck: 'false',
    placeholder: 'Filter, e.g. is:overdue',
    'aria-describedby': 'filter-problem',
  });
  const info = h('button', { type: 'button', id: 'filter-info', class: 'filter-info', 'aria-label': 'How to filter' }, icon('info', { size: 16 }));
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
    h('label', { for: 'filter', class: 'sr-only' }, 'Filter work'),
    h('span', { class: 'filter-field' }, icon('filter', { size: 16 }), input, info, hoverCard(info, helpContent(note), { size: 'wide' })),
    h('button', { type: 'submit' }, 'Apply'),
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
      icon('clear', { size: 16 }),
      'Clear',
    ),
  );
  return {
    form,
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
    // The server's own words: its examples of the filter language (epic:3) are not references to items.
    bar.setProblem(error.raw);
    return null;
  }
}
