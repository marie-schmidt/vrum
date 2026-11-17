// The words and signs the board uses, in one place, so that every page says the same thing and
// nothing is told by colour alone: each status, kind and link type has a word and an icon. The
// `sign` is the same thing as plain text, for the few places that can only hold text (an option in
// a list, say); everywhere else the `icon` is drawn from icons.js.

import { h } from './dom.js';
import { icon } from './icons.js';

export const STATUSES = ['new', 'ready', 'in_progress', 'blocked', 'done', 'dropped'];
// What a person can set. Ready is worked out from the links and cannot be set.
export const SETTABLE = ['new', 'in_progress', 'blocked', 'done', 'dropped'];

export const STATUS = {
  new: { label: 'New', sign: '○', icon: 'new', hint: 'Not started, and waiting for something that is not done' },
  ready: { label: 'Ready', sign: '◎', icon: 'ready', hint: 'Not started, and everything it needs first is done' },
  in_progress: { label: 'In progress', sign: '▶', icon: 'in_progress', hint: 'Someone is working on it' },
  blocked: { label: 'Blocked', sign: '■', icon: 'blocked', hint: 'Stopped by something outside it' },
  done: { label: 'Done', sign: '✓', icon: 'done', hint: 'Finished' },
  dropped: { label: 'Dropped', sign: '✕', icon: 'dropped', hint: 'Will not be done' },
};

export const KIND = {
  epic: { label: 'Epic', sign: '◆', icon: 'epic' },
  story: { label: 'Story', sign: '●', icon: 'story' },
  task: { label: 'Task', sign: '▪', icon: 'task' },
  rule: { label: 'Requirement', sign: '▤', icon: 'rule' },
  decision: { label: 'Decision', sign: '⚑', icon: 'decision' },
  fact: { label: 'Fact', sign: '◈', icon: 'fact' },
};

export const PRIORITIES = ['low', 'normal', 'high', 'urgent'];
export const PRIORITY = {
  low: { label: 'Low', sign: '▽', icon: 'low' },
  normal: { label: 'Normal', sign: '–', icon: 'normal' },
  high: { label: 'High', sign: '△', icon: 'high' },
  urgent: { label: 'Urgent', sign: '▲', icon: 'urgent' },
};

// How each link reads from one end and from the other.
export const LINK_TYPES = ['needs_first', 'supersedes', 'duplicates', 'relates_to'];
export const LINK = {
  needs_first: { label: 'needs first', back: 'is needed first by', line: 'solid thick with an arrow' },
  supersedes: { label: 'supersedes', back: 'is superseded by', line: 'long dashes with an arrow' },
  duplicates: { label: 'duplicates', back: 'duplicates', line: 'dots' },
  relates_to: { label: 'relates to', back: 'relates to', line: 'short dashes' },
};

// The icon of a kind of thing, a status or a priority, 16 pixels unless told otherwise.
export const kindIcon = (kind, size = 16) => icon((KIND[kind] || {}).icon || 'info', { size });

// An epic, story or task is named by its kind and its number within that kind: epic:3, story:12,
// task:40 (the `ref` the server sends). A person sees it as E-3, S-12 or T-40. Requirements,
// decisions and facts keep their own numbers and are written "#7" beside their kind.
const LETTER = { epic: 'E', story: 'S', task: 'T' };
export const nodeCode = (kind, number) => `${LETTER[kind]}-${number}`;
export const nodeRef = (kind, number) => `${kind}:${number}`;
export function parseNodeRef(ref) {
  const found = /^(epic|story|task):([1-9][0-9]{0,17})$/.exec(String(ref));
  return found ? { kind: found[1], number: Number(found[2]) } : null;
}
// The code a person reads for a reference ("S-12"), or the text itself when it is not one.
export function codeOf(ref) {
  const parsed = parseNodeRef(ref);
  return parsed ? nodeCode(parsed.kind, parsed.number) : String(ref);
}
// Where a reference sorts: epics, then stories, then tasks, each by number.
export const KIND_ORDER = { epic: 0, story: 1, task: 2 };
export function byRef(a, b) {
  const x = parseNodeRef(a);
  const y = parseNodeRef(b);
  return KIND_ORDER[x.kind] - KIND_ORDER[y.kind] || x.number - y.number;
}
export const NODE_KINDS = ['epic', 'story', 'task'];
export const isNodeKind = (kind) => NODE_KINDS.includes(kind);
// What a person reads for any of the six kinds: S-12 for an epic, story or task, #7 for the rest.
export const itemCode = (kind, number) => (isNodeKind(kind) ? nodeCode(kind, number) : `#${number}`);
// An epic, story or task in a sentence, in words: "story 12".
export function nodeWords(ref) {
  const parsed = parseNodeRef(ref);
  return parsed ? `${KIND[parsed.kind].label.toLowerCase()} ${parsed.number}` : String(ref);
}
// An element id made from a reference: story:12 becomes story-12.
export const refSlug = (ref) => String(ref).replace(':', '-');
// Any of the six kinds as the address and the text name it (story:12, rule:3): { kind, number, id, ref }
// (`id` is the number again, for the kinds that are still called by an id), or null.
export function parseItemRef(text) {
  const found = /^(epic|story|task|rule|decision|fact):([1-9][0-9]{0,17})$/.exec(String(text ?? ''));
  return found ? { kind: found[1], number: Number(found[2]), id: Number(found[2]), ref: found[0] } : null;
}
const NODE_LETTERS = { e: 'epic', s: 'story', t: 'task' };
// What a person types for an epic, story or task: S-12, s12, story 12, story:12 (a title may follow,
// as in the pick-lists). Returns its reference, or null.
export function readNodeRef(text) {
  const found = /^(epic|story|task|e|s|t)\s*[:#-]?\s*0*([1-9][0-9]{0,17})(?=\s|$)/i.exec(String(text ?? '').trim());
  if (!found) return null;
  const word = found[1].toLowerCase();
  return nodeRef(NODE_LETTERS[word] || word, found[2]);
}
export const statusIcon = (status, size = 16) => icon((STATUS[status] || {}).icon || 'info', { size });
export const priorityIcon = (priority, size = 16) => icon((PRIORITY[priority] || {}).icon || 'normal', { size });

export function statusChip(status) {
  const known = STATUS[status] || { label: status };
  return h(
    'span',
    { class: `chip status status-${status}`, title: known.hint, 'data-status': status },
    statusIcon(status, 14),
    known.label,
  );
}

export function kindChip(kind) {
  const known = KIND[kind] || { label: kind };
  return h('span', { class: `chip kind kind-${kind}` }, kindIcon(kind, 14), known.label);
}

export function priorityChip(priority) {
  const known = PRIORITY[priority] || { label: priority };
  return h('span', { class: `chip priority priority-${priority}` }, priorityIcon(priority, 14), known.label);
}

// What a status column says about itself.
export function columnNote(status) {
  return status === 'ready' ? 'worked out from the links: nothing can be dropped here' : STATUS[status].hint;
}
