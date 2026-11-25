// What the page of what needs a person and the review page both need to know about the list,
// grouping, and in-page review state (Viewed marks, drafts, leftover decision notices), kept in
// one place so both pages always agree.

import { LEVEL_ICONS, changesOf, hash } from './changes.js';
import { h, put } from './dom.js';
import { icon } from './icons.js';
import { moveAssist, planAssist } from './needs-assist.js';
import { detailOf, keepOnly } from './needs-details.js';
import { store } from './store.js';

// -- grouping (M52) ------------------------------------------------------------------------------------------

export const BY = ['level', 'topic', 'decision'];
export const BY_WORDS = { level: 'Level', topic: 'Topic', decision: 'Decision' };

const LEVEL_TITLES = { plan: 'Plans', epic: 'Epics', story: 'Stories', task: 'Tasks', note: 'Notes' };
const LEVELS = ['plan', 'epic', 'story', 'task', 'note'];

const DECISION_TITLES = { approve: 'To approve', answer: 'To answer', look_into: 'To look into' };
const DECISION_ICONS = { approve: 'confirmed', answer: 'ask', look_into: 'viewed' };
const DECISIONS = ['approve', 'answer', 'look_into'];

export const byOf = (asked) => (BY.includes(asked) ? asked : 'level');

const since = (change) => Date.parse(change.item.waiting_since);

export function groupsOf(changes, by) {
  if (by === 'decision') {
    return DECISIONS.map((id) => ({ by, id, title: DECISION_TITLES[id], icon: DECISION_ICONS[id], basis: null, changes: changes.filter((one) => one.decision === id) })).filter((one) => one.changes.length);
  }
  if (by === 'topic') return topicGroups(changes);
  return LEVELS.map((id) => ({ by: 'level', id, title: LEVEL_TITLES[id], icon: LEVEL_ICONS[id], basis: null, changes: changes.filter((one) => one.level === id) })).filter((one) => one.changes.length);
}

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

// -- preparation and ordering --------------------------------------------------------------------------------

export const titleOfRef = (ref) => store.nodes.get(ref)?.title ?? '';

export function prepare(list, wanted = () => true) {
  const changes = changesOf(list, titleOfRef);
  keepOnly(changes);
  const entries = new Map();
  for (const change of changes) {
    if (!wanted(change)) {
      entries.set(change.key, null);
      continue;
    }
    const entry = detailOf(change);
    entries.set(change.key, entry);
    if (change.look === 'plan') {
      change.assist = planAssist(entry);
      change.warn = change.assist.warn;
    } else if (change.look === 'placement') {
      const assist = moveAssist(entry);
      change.assist = assist;
      change.warn = assist.warn;
      change.routine = assist.routine;
    }
  }
  return { changes, entries };
}

export function sectionsOf(changes, by) {
  return { groups: groupsOf(changes.filter((change) => !change.routine), by), routine: changes.filter((change) => change.routine) };
}

export function orderOf(changes, by) {
  const { groups, routine } = sectionsOf(changes, by);
  return [...groups.flatMap((group) => group.changes), ...routine].map((change) => change.key);
}

// -- in-memory review state (Viewed marks, drafts, and leftover notices) -------------------------------------

const views = new Map();

export const viewOf = (key) => {
  if (!views.has(key)) views.set(key, { viewed: null, changed: false, collapsed: false, open: new Set() });
  return views.get(key);
};

export function keepViews(keys) {
  const live = new Set(keys);
  for (const key of [...views.keys()]) if (!live.has(key)) views.delete(key);
}

export const fingerprintOf = (change, entry) => hash(JSON.stringify({ ...change.item, topic: null })) + (entry && entry.hash ? entry.hash : '');

export function judge(change, entry) {
  const view = viewOf(change.key);
  const reading = entry !== null && entry !== undefined && entry.state === 'loading' && !entry.data;
  if (!reading && view.viewed !== null && view.viewed !== fingerprintOf(change, entry)) {
    view.viewed = null;
    view.changed = true;
    view.collapsed = false;
  }
  return view;
}

const drafts = new Map();

export const draftOf = (key) => {
  if (!drafts.has(key)) drafts.set(key, { open: false, text: '', remember: false });
  return drafts.get(key);
};

export const forgetDrafts = () => drafts.clear();

export const said = { lines: [], problems: [] };

export function forgetSaid() {
  said.lines = [];
  said.problems = [];
}

export function drawSaid(box) {
  if (!box) return;
  if (!said.lines.length && !said.problems.length) {
    put(box);
    return;
  }
  put(
    box,
    h(
      'div',
      { class: 'notice needs-said', role: 'status' },
      icon('info', { size: 16 }),
      h('div', { class: 'needs-said-text' }, said.lines.map((line) => h('p', { dataset: { words: 'theirs' } }, line)), said.problems.map((line) => h('p', { class: 'problem', role: 'alert' }, line))),
      h(
        'button',
        {
          type: 'button',
          class: 'ghost icon-only small notice-close',
          onclick: () => {
            forgetSaid();
            drawSaid(box);
          },
        },
        icon('close', { size: 14 }),
        h('span', { class: 'sr-only' }, 'Dismiss'),
      ),
    ),
  );
}
