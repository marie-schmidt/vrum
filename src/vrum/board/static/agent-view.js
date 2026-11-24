// The Agent view: what an agent receives for one item, in a dialog on the item's page. It is the call an
// agent makes for the item and the answer it gets, as short lines, each item with its code and its name. For an epic,
// story, task or acceptance criterion that is the answer of the recall tool as it came, and, for a piece of work that says
// where it is (its branch, commit and pull request, and which run), the same line the item's page has; a note is read with its own
// tool, and its record is what the agent gets. A person reads the same things on the page itself, in tables.

import { Refusal, call, namesIn } from './api.js';
import { h, put } from './dom.js';
import { clip, openDialog, refuse } from './forms.js';
import { askCode, parseItemRef } from './labels.js';
import { codeFor } from './references.js';
import { describeItem, store } from './store.js';
import { whereBlock } from './whereabouts.js';

const quote = (text, size = 80) => `"${clip(text, size)}"`;
// Someone's own words, whole: what an agent is told of a question and its answer is never cut.
const said = (text) => `"${String(text).replace(/\s+/g, ' ').trim()}"`;
// What the lines name is picked out: the references of the call (story:12) and the codes (S-12, AC-3, N-7).
const idSpans = (text) => String(text).split(/((?:epic|story|task|rule|note):\d+|\b(?:AC|[ESTNQ])-\d+\b)/).map((part, index) => (index % 2 ? h('span', { class: 'ag-id' }, part) : part));

// How an item is named in the heading and in the lines: its code, then its name (S-12 Unlock by typing the bike number).
const codeOfRef = (ref) => {
  const { kind, number } = parseItemRef(ref);
  return codeFor(kind, number);
};
const nameOf = (ref) => [codeOfRef(ref), describeItem(ref)?.title].filter(Boolean).join(' ');
// The same with the name in quotes, for a line of the answer: S-12 "Unlock by typing the bike number".
const lineOf = (ref) => {
  const title = describeItem(ref)?.title;
  return title ? `${codeOfRef(ref)} ${quote(title)}` : codeOfRef(ref);
};

// What the agent is asked and told, as [depth, key, rest] lines. `item` is what get_node says about a piece of work: where it is
// (`where`) and the questions put to a person about it (`asks`), which only an item that has any shows. `rest` is text, or an
// element for a line that holds one.
function agentLines(found, answer, item = { where: null, asks: [] }) {
  const lines = [];
  const say = (depth, key, rest) => lines.push([depth, key, rest]);
  if (found.kind === 'note') {
    const record = answer;
    say(0, null, `get_note(note_id=${found.number})`);
    // What a note names is told by code and name.
    const names = { nodes: (ref) => lineOf(ref), rule_ids: (id) => lineOf(`rule:${id}`), note_ids: (id) => lineOf(`note:${id}`), supersedes_ids: (id) => lineOf(`note:${id}`), superseded_by_id: (id) => lineOf(`note:${id}`) };
    const fields = ['headline', 'text', 'why', 'decided_by', 'decided_on', 'project_wide', 'status', 'confirmed_by', 'superseded_by_id', 'supersedes_ids', 'status_reason', 'nodes', 'rule_ids', 'note_ids'];
    for (const field of fields) {
      const value = record[field];
      if (value === null || value === undefined || (Array.isArray(value) && !value.length && field !== 'nodes')) continue;
      const shown = Object.hasOwn(names, field) ? (Array.isArray(value) ? value : [value]).map((one) => names[field](one)).join(', ') : typeof value === 'string' ? quote(value, 120) : String(value);
      say(1, field, ` ${Array.isArray(value) ? `[${shown}]` : shown}`);
    }
    say(0, null, '');
    say(0, null, 'recall reads one epic, story, task or acceptance criterion; for a note the agent reads the note itself, and what links to it.');
    return lines;
  }
  const recalled = answer;
  const asked = found.kind === 'rule' ? `recall(rule_id=${found.number}, limit=200, with_similar=true)` : `recall(node="${found.ref}", limit=200, with_similar=true)`;
  say(0, null, asked);
  const section = (name, entries, write) => {
    if (!entries || !entries.count) {
      say(0, name, ' []');
      return;
    }
    say(0, name, ` (${entries.count})`);
    for (const entry of entries.items.slice(0, 6)) write(entry);
    if (entries.count > 6) say(1, null, `… ${entries.count - 6} more`);
  };
  const noteLine = ({ note, why }) => {
    say(1, null, `- ${codeFor('note', note.id)} ${quote(note.headline || note.text)} [${note.status}]`);
    say(2, null, `why: ${namesIn(why)}`);
  };
  section('notes', recalled.notes, noteLine);
  section('project_wide_notes', recalled.project_wide_notes, noteLine);
  section('past_notes', recalled.past_notes, noteLine);
  const criterionLine = ({ criterion, why }) => {
    say(1, null, `- ${codeFor('rule', criterion.id)} ${quote(criterion.title)} [${criterion.status}]`);
    say(2, null, `why: ${namesIn(why)}`);
  };
  section('acceptance_criteria', recalled.acceptance_criteria, criterionLine);
  if (recalled.similar) {
    section('similar', recalled.similar, (similar) => {
      say(1, null, `- ${codeOfRef(similar.ref)} ${quote(similar.title)}${similar.similarity === null || similar.similarity === undefined ? '' : ` alike=${similar.similarity.toFixed(2)}`}`);
    });
  }
  const block = whereBlock(item.where);
  if (block || item.asks.length) {
    say(0, null, '');
    say(0, null, `get_node(node="${found.ref}")`);
  }
  if (block) say(1, 'where', block);
  if (item.asks.length) {
    say(1, 'asks', ` (${item.asks.length})`);
    for (const ask of item.asks) {
      say(2, null, `- ${askCode(ask.id)} ${said(ask.question)} [${ask.answered ? 'answered' : 'open'}${ask.effect === 'parks' ? ', work stayed free' : ''}]`);
      if (ask.choices) say(3, 'choices', ` [${ask.choices.join(', ')}]`);
      if (ask.answered) {
        say(3, 'answer', ` ${said(ask.answer)}${ask.comment ? ` comment: ${said(ask.comment)}` : ''} (${ask.answered_by})`);
      }
    }
  }
  // What work was found while doing is in an agent's briefing when it takes the item (next_work: context.discovered_from).
  const found_in = found.kind === 'epic' ? [] : (store.links || []).filter((link) => link.link_type === 'discovered_from' && link.source === found.ref).map((link) => link.target);
  if (found_in.length) {
    say(0, null, '');
    say(0, null, 'next_work briefing, when an agent takes it');
    say(1, 'discovered_from', ` (${found_in.length})`);
    for (const ref of found_in) say(2, null, `- ${lineOf(ref)}${store.nodes.get(ref) ? ` [${store.nodes.get(ref).status}]` : ''}`);
  }
  say(0, null, '');
  say(0, null, 'Only exact links are followed. What is linked to this item and to the story and epic above it is here (each entry says which); what is linked to its children is not: an agent recalls those too if they matter.');
  return lines;
}

function agentBlock(found, answer, item) {
  const text = (rest) => (rest instanceof Node ? [' ', rest] : idSpans(rest));
  return h(
    'div',
    { class: 'agent-view-text', id: 'agent-view-text', tabindex: '0', role: 'region', 'aria-label': `What an agent receives for ${nameOf(found.ref)}` },
    agentLines(found, answer, item).map(([depth, key, rest]) => {
      const line = h('div', { class: 'ag-line' }, key ? [h('span', { class: 'ag-key' }, `${key}:`), text(rest)] : text(rest));
      line.style.setProperty('--d', String(depth));
      return line;
    }),
  );
}

const read = (found) => {
  if (found.kind === 'note') return call('get_note', { note_id: found.number });
  return call('recall', { ...(found.kind === 'rule' ? { rule_id: found.number } : { node: found.ref }), limit: 200, with_similar: true });
};

// What a piece of work says about where it is and what was asked of a person, as an agent reads them with the item itself
// (get_node's `where` and `asks`). An acceptance criterion and a note have none, and a refusal here only leaves the lines out: the
// recall above says why the dialog could not read the item.
const readItem = (found) =>
  found.kind === 'epic' || found.kind === 'story' || found.kind === 'task'
    ? call('get_node', { node: found.ref }).then((item) => ({ where: item.where || null, asks: item.asks || [] }), () => ({ where: null, asks: [] }))
    : Promise.resolve({ where: null, asks: [] });

// Opens the dialog for an epic, story, task, acceptance criterion or note (epic:3, rule:3, note:7). It reads at once and shows
// what the server answers, or what it refused with.
export function openAgentView(ref) {
  const found = parseItemRef(ref);
  if (!found) return null;
  const body = h('div', { class: 'agent-view-body' }, h('p', { class: 'loading', role: 'status' }, 'Reading what an agent receives…'));
  const close = h('button', { type: 'button', id: 'agent-view-close', autofocus: true, onclick: () => box.close() }, 'Close');
  const box = openDialog(`What an agent receives for ${nameOf(ref)}`, [body, h('div', { class: 'actions' }, close)], { wide: true });
  box.id = 'agent-view';
  box.classList.add('agent-view');
  Promise.all([read(found), readItem(found)]).then(
    ([answer, item]) => {
      if (box.isConnected) put(body, agentBlock(found, answer, item));
    },
    (error) => {
      if (!(error instanceof Refusal)) throw error;
      if (!box.isConnected) return;
      const problem = h('p', { class: 'problem', role: 'alert' });
      refuse(problem, error.message);
      put(body, problem);
    },
  );
  return box;
}
