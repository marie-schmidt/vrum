// How the page of what needs a person puts its changes in groups (M52). Three ways, chosen with "Group by" and kept in the address
// (`group=topic`; Level, the first, says nothing): by LEVEL (plans, epics, stories, tasks, notes: how strategic a change is, as the
// server says), by TOPIC (what the changes are about: the epic they are under, or a set of changes whose work is alike in meaning,
// as the server says) and by DECISION (to approve, to answer, to look into). The server decides every one of these, row by row
// (`level`, `topic`, `decision`); this file only puts rows in the groups those words name, in the server's order, so that no row is
// lost or drawn twice. It draws nothing.
//
// Two groups are the same in every way of grouping and are made by the page, not here: the routine moves (a closed group of their
// own) and the plans that wait for their agent.

import { LEVEL_ICONS } from './changes.js';

export const BY = ['level', 'topic', 'decision'];
export const BY_WORDS = { level: 'Level', topic: 'Topic', decision: 'Decision' };

export const LEVEL_TITLES = { plan: 'Plans', epic: 'Epics', story: 'Stories', task: 'Tasks', note: 'Notes' };
const LEVELS = ['plan', 'epic', 'story', 'task', 'note'];

export const DECISION_TITLES = { approve: 'To approve', answer: 'To answer', look_into: 'To look into' };
export const DECISION_ICONS = { approve: 'confirmed', answer: 'ask', look_into: 'viewed' };
const DECISIONS = ['approve', 'answer', 'look_into'];

// The way of grouping an address asks for: an unknown word is Level.
export const byOf = (asked) => (BY.includes(asked) ? asked : 'level');

const since = (change) => Date.parse(change.item.waiting_since);

// The groups of `changes` (in the server's order) for one way of grouping: [{ by, id, title, icon, basis, changes }], each with at
// least one change, in the order they are shown. `id` is what `data-group` says: a level, a decision, the server's topic id or `other`.
export function groupsOf(changes, by) {
  if (by === 'decision') {
    return DECISIONS.map((id) => ({ by, id, title: DECISION_TITLES[id], icon: DECISION_ICONS[id], basis: null, changes: changes.filter((one) => one.decision === id) })).filter((one) => one.changes.length);
  }
  if (by === 'topic') return topicGroups(changes);
  return LEVELS.map((id) => ({ by: 'level', id, title: LEVEL_TITLES[id], icon: LEVEL_ICONS[id], basis: null, changes: changes.filter((one) => one.level === id) })).filter((one) => one.changes.length);
}

// One group for each topic the server named, the one with the longest wait first, and a group "Other" last for the rows that are
// about nothing the project can place (the server's `topic` is null).
function topicGroups(changes) {
  const found = new Map();
  const other = [];
  for (const change of changes) {
    if (!change.topic) {
      other.push(change);
      continue;
    }
    if (!found.has(change.topic.id)) found.set(change.topic.id, { by: 'topic', id: change.topic.id, title: change.topic.label, icon: change.topic.basis === 'meaning' ? 'topic' : 'epic', basis: change.topic.basis, changes: [] });
    found.get(change.topic.id).changes.push(change);
  }
  const longest = (group) => Math.min(...group.changes.map(since));
  const ordered = [...found.values()].sort((a, b) => longest(a) - longest(b));
  if (other.length) ordered.push({ by: 'topic', id: 'other', title: 'Other', icon: 'needs', basis: null, changes: other });
  return ordered;
}
