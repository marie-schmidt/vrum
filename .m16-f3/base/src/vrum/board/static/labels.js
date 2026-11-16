// The words and signs the board uses, in one place, so that every page says the same thing and
// nothing is told by colour alone: each status, kind and link type has a word and a sign.

import { h } from './dom.js';

export const STATUSES = ['new', 'ready', 'in_progress', 'blocked', 'done', 'dropped'];
// What a person can set. Ready is worked out from the links and cannot be set.
export const SETTABLE = ['new', 'in_progress', 'blocked', 'done', 'dropped'];

export const STATUS = {
  new: { label: 'New', sign: '○', hint: 'Not started, and waiting for something that is not done' },
  ready: { label: 'Ready', sign: '◎', hint: 'Not started, and everything it needs first is done' },
  in_progress: { label: 'In progress', sign: '▶', hint: 'Someone is working on it' },
  blocked: { label: 'Blocked', sign: '■', hint: 'Stopped by something outside it' },
  done: { label: 'Done', sign: '✓', hint: 'Finished' },
  dropped: { label: 'Dropped', sign: '✕', hint: 'Will not be done' },
};

export const KIND = {
  epic: { label: 'Epic', sign: '◆' },
  story: { label: 'Story', sign: '●' },
  task: { label: 'Task', sign: '▪' },
  rule: { label: 'Rule', sign: '▤' },
  decision: { label: 'Decision', sign: '⚑' },
  principle: { label: 'Principle', sign: '⚐' },
  fact: { label: 'Fact', sign: '◈' },
  belief: { label: 'Belief', sign: '◇' },
};

export const PRIORITIES = ['low', 'normal', 'high', 'urgent'];
export const PRIORITY = {
  low: { label: 'Low', sign: '▽' },
  normal: { label: 'Normal', sign: '–' },
  high: { label: 'High', sign: '△' },
  urgent: { label: 'Urgent', sign: '▲' },
};

// How each link reads from one end and from the other.
export const LINK_TYPES = ['needs_first', 'supersedes', 'duplicates', 'relates_to'];
export const LINK = {
  needs_first: { label: 'needs first', back: 'is needed first by', line: 'solid thick with an arrow' },
  supersedes: { label: 'supersedes', back: 'is superseded by', line: 'long dashes with an arrow' },
  duplicates: { label: 'duplicates', back: 'duplicates', line: 'dots' },
  relates_to: { label: 'relates to', back: 'relates to', line: 'short dashes' },
};

export function statusChip(status) {
  const known = STATUS[status] || { label: status, sign: '?' };
  return h(
    'span',
    { class: `chip status status-${status}`, title: known.hint, 'data-status': status },
    h('span', { 'aria-hidden': 'true' }, known.sign),
    ' ',
    known.label,
  );
}

export function kindChip(kind) {
  const known = KIND[kind] || { label: kind, sign: '?' };
  return h(
    'span',
    { class: `chip kind kind-${kind}` },
    h('span', { 'aria-hidden': 'true' }, known.sign),
    ' ',
    known.label,
  );
}

export function priorityChip(priority) {
  const known = PRIORITY[priority] || { label: priority, sign: '' };
  return h(
    'span',
    { class: `chip priority priority-${priority}` },
    h('span', { 'aria-hidden': 'true' }, known.sign),
    ' ',
    known.label,
  );
}

// What a status column says about itself.
export function columnNote(status) {
  return status === 'ready' ? 'worked out from the links: nothing can be dropped here' : STATUS[status].hint;
}
