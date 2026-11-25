// What the page of what needs a person (needs.js) and the review page (review.js) are made of: a list of changes. Every decision the
// server lists is one change, with its kind (a plan, a suggested move, a question, an approval, a check, notes, a refused approval or a
// claim that ran out), what the server says about it (`level`, `decision`, `topic`: the board never works those out) and the words
// and signs that name it. This file only works out, from the server's list, what each change is. It draws nothing and asks the
// server for nothing (needs-details.js reads what a change needs to be shown whole).

import { askCode, codeOf, gateCode, planCode, proposalCode } from './labels.js';

// How each kind of change is named and what it is drawn with: its plain word and its icon.
const SHAPES = {
  plan: { icon: 'plan', words: 'Plan' },
  placement: { icon: 'move', words: 'Move' },
  question: { icon: 'ask', words: 'Question' },
  approval: { icon: 'confirmed', words: 'Approval' },
  check: { icon: 'checklist', words: 'Check' },
  note: { icon: 'diamond', words: 'Notes' },
  gate: { icon: 'gate', words: 'Refused approval' },
  lapse: { icon: 'clock', words: 'Stalled claim' },
};
const OTHER = { icon: 'needs', words: 'Needs a person' };

// A short fingerprint of text (FNV-1a), to tell whether what the server sent for a change is what it was when someone looked.
export function hash(text) {
  let value = 0x811c9dc5;
  for (let at = 0; at < text.length; at += 1) {
    value ^= text.charCodeAt(at);
    value = Math.imul(value, 0x01000193) >>> 0;
  }
  return value.toString(16).padStart(8, '0');
}

// The look of a row of the server's list: plan, placement, question, approval, check, note, gate or lapse.
function lookOf(item) {
  if (item.kind === 'ask') return item.ask && item.ask.kind === 'approval' ? 'approval' : 'question';
  return item.kind;
}

// What the person calls the change: Plan 1402, Proposal 7, Q-12, Gate 5, or, for what is only about an item, the item's code.
function codeFor(item, look) {
  if (look === 'plan') return planCode(item.plan.id);
  if (look === 'placement') return proposalCode(item.placement.id);
  if (look === 'question' || look === 'approval') return askCode(item.ask.id);
  if (look === 'gate' && item.gate) return gateCode(item.gate.id);
  return codeOf(item.node);
}

// The unique name of a change, which is also its address on the review page: `plan:1402`, `placement:7`, `ask:12`, `gate:5`, and the kind
// and the item for the others (`check:task:40`).
function idFor(item, look) {
  if (look === 'plan') return `plan:${item.plan.id}`;
  if (look === 'placement') return `placement:${item.placement.id}`;
  if (look === 'question' || look === 'approval') return `ask:${item.ask.id}`;
  if (look === 'gate' && item.gate) return `gate:${item.gate.id}`;
  return `${item.kind}:${item.node}`;
}

// What a suggested move is called: the work it moves and where it would go. "Move S-4, S-9 and 2 more under E-3".
function codesWords(refs) {
  const codes = refs.map(codeOf);
  if (codes.length > 3) return `${codes.slice(0, 2).join(', ')} and ${codes.length - 2} more`;
  if (codes.length === 3) return `${codes[0]}, ${codes[1]} and ${codes[2]}`;
  return codes.join(' and ');
}

// The title a change is shown under: a plan's, what a proposal moves and where to, and for everything else the work it is about.
function titleFor(item, look, titleOfRef) {
  if (look === 'plan') return item.plan.title;
  if (look === 'placement') {
    const move = item.placement;
    const where = move.parent ? `under ${codeOf(move.parent)} ${move.parent_title ?? titleOfRef(move.parent)}`.trim() : `under a new epic, ${move.new_epic?.title ?? ''}`;
    return `Move ${codesWords(move.items.map((one) => one.ref))} ${where}`;
  }
  return item.title;
}

// What a screen reader or a label calls it: the code, and what kind of change it is where the code alone does not say.
function nameFor(look, code) {
  if (look === 'check') return `Check on ${code}`;
  if (look === 'note') return `Notes on ${code}`;
  if (look === 'lapse') return `Lapsed claim on ${code}`;
  return code;
}

// One change of the list. `titleOfRef(ref)` finds the title of an epic, story or task the board knows.
export function changeOf(item, titleOfRef = () => '') {
  const look = lookOf(item);
  const shape = SHAPES[look] || OTHER;
  const code = codeFor(item, look);
  const id = idFor(item, look);
  return {
    id,
    key: id,
    look,
    item,
    // What the server says, drawn as it is: how strategic the row is, what a person is asked to do, and what it is about.
    level: item.level,
    decision: item.decision,
    topic: item.topic ?? null,
    icon: shape.icon,
    words: shape.words,
    code,
    title: titleFor(item, look, titleOfRef),
    name: nameFor(look, code),
    // What the server says to help review it (needs-assist.js), set once it has been read: whether it is a routine move, and what
    // there is to weigh.
    routine: false,
    warn: null,
    assist: null,
  };
}

// Every change of the server's list, in its order: the groups as the server sorted them, and the rows of each.
export function changesOf(list, titleOfRef) {
  return list.groups.flatMap((group) => group.items.map((item) => changeOf(item, titleOfRef)));
}

// The words of a level and its icon (a plan and a note are levels of their own, as the server says).
export const LEVEL_WORDS = { plan: 'Plan', epic: 'Epic', story: 'Story', task: 'Task', note: 'Note' };
export const LEVEL_ICONS = { plan: 'plan', epic: 'epic', story: 'story', task: 'task', note: 'diamond' };
