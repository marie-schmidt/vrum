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
