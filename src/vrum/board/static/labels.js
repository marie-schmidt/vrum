// The words and signs the board uses, in one place, so that every page says the same thing and
// nothing is told by colour alone: each status, kind and link type has a word and an icon. The
// `sign` is the same thing as plain text, for the few places that can only hold text (an option in
// a list, say); everywhere else the `icon` is drawn from icons.js.

import { day, h } from './dom.js';
import { icon } from './icons.js';

export const STATUSES = ['new', 'ready', 'in_progress', 'blocked', 'done', 'dropped'];

// T-shirt sizes of a story or task (how much effort and how much context it takes), smallest first,
// and the points each counts for when sizes are added up. The server holds the same table.
export const SIZES = ['xs', 's', 'm', 'l', 'xl'];
export const SIZE_POINTS = { xs: 1, s: 2, m: 3, l: 5, xl: 8 };
export const SIZE_WORDS = { xs: 'Extra small', s: 'Small', m: 'Medium', l: 'Large', xl: 'Extra large' };
// The points of one item's own size, or null when it has none.
export const pointsOf = (node) => (node && Object.hasOwn(SIZE_POINTS, node.size) ? SIZE_POINTS[node.size] : null);
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
  rule: { label: 'Acceptance criterion', sign: '▤', icon: 'rule' },
  note: { label: 'Note', sign: '◈', icon: 'diamond' },
};

// What a kind is called.
export const kindLabel = (kind) => (KIND[kind] || { label: kind }).label;

// A note has one status. Unconfirmed and Confirmed are the two ways it can be in force; Replaced (stored as
// `superseded`) and Withdrawn are the two ways it can have ended, and an ended note never changes again.
// `tone` is the colour class of its chip (base.css); the sign is the same thing as plain text.
export const NOTE_STATUSES = ['unconfirmed', 'confirmed', 'superseded', 'withdrawn'];
export const NOTE_STATUS = {
  unconfirmed: { label: 'Unconfirmed', sign: '◌', icon: 'unconfirmed', tone: 'warn', hint: 'No person has reviewed it yet' },
  confirmed: { label: 'Confirmed', sign: '✓', icon: 'confirmed', tone: 'ok', hint: 'A person reviewed it and stands behind it' },
  superseded: { label: 'Replaced', sign: '→', icon: 'arrow_right', tone: '', hint: 'A newer note replaced it' },
  withdrawn: { label: 'Withdrawn', sign: '✕', icon: 'dropped', tone: 'danger', hint: 'It should not be relied on' },
};
export const noteStatusWords = (status) => (NOTE_STATUS[status] || { label: status }).label;
// A note counts while it is unconfirmed or confirmed; once it is replaced or withdrawn it is kept as history.
export const inForce = (note) => note.status === 'unconfirmed' || note.status === 'confirmed';

// An acceptance criterion (a rule, to the server) is Open (it stands and is still to be met), Met, or
// Dropped. Its signs are boxes where a note's are circles, so that the two are told apart by shape as
// well as by word.
export const REQUIREMENT_STATUSES = ['open', 'met', 'dropped'];
export const REQUIREMENT_STATUS = {
  open: { label: 'Open', sign: '☐', icon: 'req_open', tone: 'info', hint: 'It stands and is still to be met' },
  met: { label: 'Met', sign: '☑', icon: 'req_met', tone: 'ok', hint: 'It has been met' },
  dropped: { label: 'Dropped', sign: '✕', icon: 'req_dropped', tone: '', hint: 'It no longer applies' },
};

// "3 acceptance criteria", "1 acceptance criterion".
export const criteriaCount = (count) => `${count} ${count === 1 ? 'acceptance criterion' : 'acceptance criteria'}`;

// "Decided by Priya on 2 Oct 2026" for a note that names who decided it, or nothing. It is a detail of a
// note, not a kind of note.
export const decidedWords = (note) => (note && note.decided_by ? `Decided by ${note.decided_by}${note.decided_on ? ` on ${day(note.decided_on)}` : ''}` : null);

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

// The icon of a kind of thing, 16 pixels unless told otherwise.
export const kindIcon = (kind, size = 16) => icon((KIND[kind] || {}).icon || 'info', { size });

// An epic, story or task is named by its kind and its number within that kind: epic:3, story:12,
// task:40 (the `ref` the server sends). A person sees it as E-3, S-12 or T-40. Acceptance criteria and
// notes keep their own numbers and are written "#7" beside their kind.
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
// What a person reads for any of the five kinds: S-12 for an epic, story or task, #7 for the rest.
export const itemCode = (kind, number) => (isNodeKind(kind) ? nodeCode(kind, number) : `#${number}`);
// An epic, story or task in a sentence, in words: "story 12".
export function nodeWords(ref) {
  const parsed = parseNodeRef(ref);
  return parsed ? `${KIND[parsed.kind].label.toLowerCase()} ${parsed.number}` : String(ref);
}
// An element id made from a reference: story:12 becomes story-12.
export const refSlug = (ref) => String(ref).replace(':', '-');
// Any of the five kinds as the address and the text name it (story:12, rule:3, note:7): { kind, number,
// id, ref } (`id` is the number again, for the kinds that are still called by an id), or null.
export function parseItemRef(text) {
  const found = /^(epic|story|task|rule|note):([1-9][0-9]{0,17})$/.exec(String(text ?? ''));
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
// How any status reads, whichever kind of item it belongs to: a work item's, an acceptance criterion's or a note's
// (search results, mentions, the status facet). `dropped` is the same word for work and acceptance criteria.
export const statusLabel = (status) => STATUS[status]?.label || NOTE_STATUS[status]?.label || REQUIREMENT_STATUS[status]?.label || status;
export const anyStatusIcon = (status, size = 16) => icon(STATUS[status]?.icon || NOTE_STATUS[status]?.icon || REQUIREMENT_STATUS[status]?.icon || 'info', { size });

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

// How a note's status and an acceptance criterion's status look as a chip: one place for the classes, the hooks, the
// sign and the word, so that a plain chip here and the chip a person can change (status-menu.js) are the same.
export function noteStatusLook(status) {
  const known = NOTE_STATUS[status] || { label: status, icon: 'info', tone: '', hint: '' };
  return { props: { class: `chip note-status note-status-${status} ${known.tone}`.trim(), title: known.hint, 'data-status': status }, content: [icon(known.icon, { size: 14 }), known.label], words: known.label };
}

export function requirementStatusLook(status) {
  const known = REQUIREMENT_STATUS[status] || { label: status, icon: 'info', tone: '', hint: '' };
  return { props: { class: `chip rule-status rule-status-${status} ${known.tone}`.trim(), title: known.hint, 'data-status': status }, content: [icon(known.icon, { size: 14 }), known.label], words: known.label };
}

// The chips for a place that only reads (a search result, a mention): no menu.
export const plainNoteStatus = (status) => h('span', noteStatusLook(status).props, ...noteStatusLook(status).content);
export const plainRequirementStatus = (status) => h('span', requirementStatusLook(status).props, ...requirementStatusLook(status).content);

export function priorityChip(priority) {
  const known = PRIORITY[priority] || { label: priority };
  return h('span', { class: `chip priority priority-${priority}` }, priorityIcon(priority, 14), known.label);
}

// How many of an item's own acceptance criteria are met: "2/3 criteria", left out when it has none. All met
// is said by the colour and, for those who do not see it, by the fuller words in its tooltip.
export function criteriaChip(node, { size = 13 } = {}) {
  if (!node.criteria_total) return null;
  const done = node.criteria_met === node.criteria_total;
  return h(
    'span',
    { class: `chip criteria${done ? ' ok' : ''}`, title: `${node.criteria_met} of ${criteriaCount(node.criteria_total)} met` },
    icon('rule', { size }),
    `${node.criteria_met}/${node.criteria_total} criteria`,
  );
}

// "3 points", "1 point".
export const pointsWords = (points) => `${points} ${points === 1 ? 'point' : 'points'}`;

// What a size is in words: "Medium, 3 points" for one item's own; with `rollup`, what a story or epic adds up to,
// "14 points, 3 not sized". Empty when there is nothing to say.
export function sizeWords(node, { rollup = false } = {}) {
  if (!node) return '';
  if (rollup) {
    const total = node.points_total || 0;
    const unsized = node.unsized || 0;
    return [total ? pointsWords(total) : null, unsized ? `${unsized} not sized` : null].filter(Boolean).join(', ');
  }
  return Object.hasOwn(SIZE_POINTS, node.size) ? `${SIZE_WORDS[node.size]}, ${pointsWords(SIZE_POINTS[node.size])}` : '';
}

// Whether an item's size is what it adds up to: an epic's, and a story's while it has tasks.
export const addsUp = (node) => node.kind === 'epic' || (node.kind === 'story' && node.progress_total > 0);

// The size of one story or task as a chip: its letter, with "Medium, 3 points" as its tooltip. With
// `rollup` it is what a story or epic adds up to ("14 pts", tooltip "14 points, 3 not sized"). Null when
// there is nothing to say; the item's own page has the control that says "not set".
export function sizeChip(node, { rollup = false } = {}) {
  const words = sizeWords(node, { rollup });
  if (!words) return null;
  if (rollup) {
    const total = node.points_total || 0;
    return h('span', { class: `chip size size-rollup${total ? '' : ' size-none'}`, title: words, 'data-points': String(total) }, icon('size', 13), total ? `${total} pts` : 'Not sized');
  }
  return h('span', { class: `chip size size-${node.size}`, title: words, 'data-size': node.size }, node.size.toUpperCase());
}

// The size chip an item carries where work is listed: a task's own, a story's own while it has no tasks, and what a
// story with tasks or an epic adds up to.
export const itemSizeChip = (node) => sizeChip(node, { rollup: addsUp(node) });

// Whether a card says it has no size: the very rule of the `no:size` filter, for work that is still open. A story or
// task with none of its own, apart from a story that has tasks (it adds up theirs). Never an epic, and never work that
// is done or dropped.
export const needsSize = (node) => node.kind !== 'epic' && node.status !== 'done' && node.status !== 'dropped' && !Object.hasOwn(SIZE_POINTS, node.size) && !addsUp(node);

// The faint chip of work with no size yet, in the place of a size chip. It is not a button: the item's own page
// has the control that gives it one.
export const noSizeChip = (node) => (needsSize(node) ? h('span', { class: 'chip no-size', dataset: { noSize: 'true' } }, 'No size') : null);

// A check of the definition of done that only a person can judge. The same chip wherever such a check is drawn.
export const personOnlyChip = ({ size = 13 } = {}) => h('span', { class: 'chip person-only' }, icon('lock', { size }), 'Person only');

// How many of the checks of the definition of done are ticked on a story or task: "4/6 checks". Left out when
// none is ticked or there are no checks. All ticked is the green one, and on finished work it is left out too
// (`quietWhenDone`), since done is the column it sits in.
export function checksChip(node, { size = 13, quietWhenDone = false } = {}) {
  const total = node.checks_total || 0;
  const ticked = node.checks_ticked || 0;
  if (!total || !ticked) return null;
  const all = ticked === total;
  if (all && quietWhenDone && node.status === 'done') return null;
  return h(
    'span',
    { class: `chip checks${all ? ' ok' : ''}`, title: `${ticked} of ${total} ${total === 1 ? 'check' : 'checks'} ticked`, 'data-checks': `${ticked}/${total}` },
    icon('checklist', { size }),
    `${ticked}/${total} ${total === 1 ? 'check' : 'checks'}`,
  );
}

// Who did something, or who holds it: the name with a person or an agent sign before it, so that a
// person and an agent are told apart in every history, claim and record. `kind` comes from the
// server, which knows it from the credential; a kind it does not know (history imported from an
// older export is `unknown`) or none draws the name alone. The sign is decoration: the whole label
// is read as "Lena Okafor, a person".
const ACTORS = { person: { icon: 'person', words: 'a person' }, agent: { icon: 'bot', words: 'an agent' } };

export function actorLabel(name, kind, { size = 14 } = {}) {
  const known = Object.hasOwn(ACTORS, kind) ? ACTORS[kind] : null;
  if (!known) return h('span', { class: 'actor' }, name);
  const words = `${name}, ${known.words}`;
  return h('span', { class: `actor actor-${kind}`, role: 'img', 'aria-label': words, title: words, dataset: { actorKind: kind } }, icon(known.icon, { size }), h('span', { class: 'actor-name' }, name));
}

// What a person can be in a project, and what it lets them do, in plain words.
export const ROLES = {
  owner: { label: 'Owner', can: 'Everything, including who is in the project and its agent keys.' },
  editor: { label: 'Editor', can: 'Can read and change everything in the project, but not who is in it or its agent keys.' },
  viewer: { label: 'Viewer', can: 'Can look at everything and change nothing.' },
};
export const roleWords = (role) => (ROLES[role] || { label: role }).label;
// "an editor", "a viewer", "the owner".
export const asRole = (role) => (role === 'owner' ? 'the owner' : `${/^[aeiou]/i.test(roleWords(role)) ? 'an' : 'a'} ${roleWords(role).toLowerCase()}`);
export const roleChip = (role) => h('span', { class: `chip role role-${role}` }, roleWords(role));
