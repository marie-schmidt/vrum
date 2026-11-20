// The Agent view: what an agent receives for one item, in a dialog on the item's page. It is the call an
// agent makes for the item and the answer it gets, as short lines. For an epic, story, task or acceptance
// criterion that is the answer of the recall tool as it came; a note is read with its own tool, and its
// record is what the agent gets. A person reads the same things on the page itself, in tables.

import { Refusal, call } from './api.js';
import { h, put } from './dom.js';
import { openDialog, refuse } from './forms.js';
import { clip } from './graph-model.js';
import { isNodeKind, kindLabel, nodeCode, parseItemRef } from './labels.js';

const quote = (text, size = 80) => `"${clip(text, size)}"`;
const idSpans = (text) => String(text).split(/((?:epic|story|task|rule|note):\d+)/).map((part, index) => (index % 2 ? h('span', { class: 'ag-id' }, part) : part));

// How the item is named in the heading: S-12 for work, "note #7" for the rest.
const nameOf = ({ kind, number }) => (isNodeKind(kind) ? nodeCode(kind, number) : `${kindLabel(kind).toLowerCase()} #${number}`);

// What the agent is asked and told, as [depth, key, rest] lines.
function agentLines(found, answer) {
  const lines = [];
  const say = (depth, key, rest) => lines.push([depth, key, rest]);
  if (found.kind === 'note') {
    const record = answer;
    say(0, null, `get_note(note_id=${found.number})`);
    const fields = ['headline', 'text', 'why', 'decided_by', 'decided_on', 'project_wide', 'status', 'confirmed_by', 'superseded_by_id', 'supersedes_ids', 'status_reason', 'nodes', 'rule_ids', 'note_ids'];
    for (const field of fields) {
      const value = record[field];
      if (value === null || value === undefined || (Array.isArray(value) && !value.length && field !== 'nodes')) continue;
      say(1, `${field}`, ` ${typeof value === 'string' ? quote(value, 120) : Array.isArray(value) ? `[${value.join(', ')}]` : String(value)}`);
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
    say(1, null, `- note:${note.id} ${quote(note.headline || note.text)} [${note.status}]`);
    say(2, null, `why: ${why}`);
  };
  section('notes', recalled.notes, noteLine);
  section('project_wide_notes', recalled.project_wide_notes, noteLine);
  section('past_notes', recalled.past_notes, noteLine);
  const criterionLine = ({ criterion, why }) => {
    say(1, null, `- rule:${criterion.id} ${quote(criterion.title)} [${criterion.status}]`);
    say(2, null, `why: ${why}`);
  };
  section('acceptance_criteria', recalled.acceptance_criteria, criterionLine);
  if (recalled.similar) {
    section('similar', recalled.similar, (similar) => {
      say(1, null, `- ${similar.ref} ${quote(similar.title)}${similar.similarity === null || similar.similarity === undefined ? '' : ` alike=${similar.similarity.toFixed(2)}`}`);
    });
  }
  say(0, null, '');
  say(0, null, 'Only exact links are followed. What is linked to this item and to the story and epic above it is here (each entry says which); what is linked to its children is not: an agent recalls those too if they matter.');
  return lines;
}

function agentBlock(found, answer) {
  return h(
    'div',
    { class: 'agent-view-text', id: 'agent-view-text', tabindex: '0', role: 'region', 'aria-label': `What an agent receives for ${nameOf(found)}` },
    agentLines(found, answer).map(([depth, key, rest]) => {
      const line = h('div', { class: 'ag-line' }, key ? [h('span', { class: 'ag-key' }, `${key}:`), idSpans(rest)] : idSpans(rest));
      line.style.setProperty('--d', String(depth));
      return line;
    }),
  );
}

const read = (found) => {
  if (found.kind === 'note') return call('get_note', { note_id: found.number });
  return call('recall', { ...(found.kind === 'rule' ? { rule_id: found.number } : { node: found.ref }), limit: 200, with_similar: true });
};

// Opens the dialog for an epic, story, task, acceptance criterion or note (epic:3, rule:3, note:7). It reads at once and shows
// what the server answers, or what it refused with.
export function openAgentView(ref) {
  const found = parseItemRef(ref);
  if (!found) return null;
  const body = h('div', { class: 'agent-view-body' }, h('p', { class: 'loading', role: 'status' }, 'Reading what an agent receives…'));
  const close = h('button', { type: 'button', id: 'agent-view-close', autofocus: true, onclick: () => box.close() }, 'Close');
  const box = openDialog(`What an agent receives for ${nameOf(found)}`, [body, h('div', { class: 'actions' }, close)], { wide: true });
  box.id = 'agent-view';
  box.classList.add('agent-view');
  read(found).then(
    (answer) => {
      if (box.isConnected) put(body, agentBlock(found, answer));
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
