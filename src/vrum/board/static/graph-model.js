// What the Graph page works out from what the board already holds (the store) and from the tools an
// agent uses: what the project holds to choose from (the epics with what is under them, the records),
// how a person names an item, what needs attention, and what is around one item, grouped the way
// recall groups it. Nothing here draws or keeps a page of its own.
//
// An item is named by its reference: epic:3, story:12, task:40, rule:3 or note:7. A
// person reads S-12 for work and #7 for the rest.

import { Refusal, call } from './api.js';
import { toDay, todayDay } from './dates.js';
import { decidedWords, inForce, isNodeKind, nodeCode, parseItemRef } from './labels.js';
import { ensure, headlineOf, store } from './store.js';

// Where items sort: epics, stories, tasks, acceptance criteria and notes, each by number.
const KIND_RANK = { epic: 0, story: 1, task: 2, rule: 3, note: 4 };
export function byRef(a, b) {
  const [x, y] = [parseItemRef(a), parseItemRef(b)];
  return KIND_RANK[x.kind] - KIND_RANK[y.kind] || x.number - y.number;
}

export const ended = (status) => status === 'done' || status === 'dropped';

export const clip = (text, size) => {
  const one = String(text ?? '').replace(/\s+/g, ' ').trim();
  return one.length > size ? `${one.slice(0, size - 1).trimEnd()}…` : one;
};

// The words of a kind inside a sentence: "this story", "this acceptance criterion".
export const KIND_NOUN = { epic: 'epic', story: 'story', task: 'task', rule: 'acceptance criterion', note: 'note' };

// -- reading the store, fast ---------------------------------------------------------------------

// A cache for each list the store replaces whole when it reloads, so a draw of a hundred rows does
// not search a list a hundred times.
const cached = new WeakMap();
function once(source, make) {
  if (!source) return make();
  if (!cached.has(source)) cached.set(source, make());
  return cached.get(source);
}

const byId = (records) => once(records, () => new Map((records || []).map((record) => [record.id, record])));
const kidsOf = () =>
  once(store.nodes, () => {
    const map = new Map();
    for (const node of store.nodes.values()) {
      if (node.parent) map.set(node.parent, [...(map.get(node.parent) || []), node]);
    }
    return map;
  });
const needMaps = () =>
  once(store.links, () => {
    const needs = new Map(); // a needs b first: a -> [b]
    const needed = new Map(); // b -> [a]
    const other = new Map(); // ref -> [{ type, ref, outgoing }]
    const push = (map, key, value) => map.set(key, [...(map.get(key) || []), value]);
    for (const link of store.links) {
      if (link.link_type === 'needs_first') {
        push(needs, link.source, link.target);
        push(needed, link.target, link.source);
      } else {
        push(other, link.source, { type: link.link_type, ref: link.target, outgoing: true });
        push(other, link.target, { type: link.link_type, ref: link.source, outgoing: false });
      }
    }
    return { needs, needed, other };
  });

export const childrenOf = (ref) => kidsOf().get(ref) || [];
export const waitingFor = (node) => (node.waiting_on || []).filter((ref) => store.nodes.get(ref) && !ended(store.nodes.get(ref).status));

export function lateDays(node) {
  if (!node || !node.overdue || !node.target_on || ended(node.status)) return 0;
  return Math.max(1, todayDay() - toDay(node.target_on));
}

// Any of the five kinds by reference: { ref, kind, number, code, title, work, record }, or null when
// there is no such item (or the acceptance criteria and notes are not read yet).
export function resolve(ref) {
  const found = parseItemRef(ref);
  if (!found) return null;
  const { kind, number } = found;
  if (isNodeKind(kind)) {
    const node = store.nodes.get(found.ref);
    return node ? { ref: found.ref, kind, number, code: nodeCode(kind, number), title: node.title, work: true, record: node } : null;
  }
  const table = { rule: store.rules, note: store.notes }[kind];
  const record = byId(table).get(number);
  if (!record) return null;
  return { ref: found.ref, kind, number, code: `#${number}`, title: kind === 'note' ? clip(headlineOf(record), 120) : record.title, work: false, record };
}

// An acceptance criterion that is dropped, a note that is replaced or withdrawn.
export const isEnded = (item) => (item.kind === 'rule' ? item.record.status === 'dropped' : !item.work && !inForce(item.record));
export const isUnconfirmed = (item) => item.kind === 'note' && item.record.status === 'unconfirmed';

export const progressOf = (node) => (node.progress_total ? { done: node.progress_done, total: node.progress_total } : null);

// -- what needs attention ------------------------------------------------------------------------

const PRIORITY_WEIGHT = { low: 0, normal: 1, high: 2, urgent: 3 };

// Work that is blocked or overdue, the most urgent first: where a person is most likely to want to look.
export function attention(limit = 5) {
  return [...store.nodes.values()]
    .filter((node) => node.kind !== 'epic' && !ended(node.status) && (node.status === 'blocked' || lateDays(node) > 0))
    .map((node) => ({ node, score: (node.status === 'blocked' ? 4 : 0) + PRIORITY_WEIGHT[node.priority] * 1.5 + Math.min(lateDays(node), 30) / 10 + (node.kind === 'task' ? 0.2 : 0) }))
    .sort((a, b) => b.score - a.score || byRef(a.node.ref, b.node.ref))
    .slice(0, limit)
    .map(({ node }) => resolve(node.ref));
}

// -- browsing: what the project holds, to choose from ----------------------------------------------

// The epics, each with what is under it: how many stories and tasks, how many of them are blocked or
// late, how far along it is, and how many notes in force are linked to the epic or to
// anything in it. One pass over the store, in epic order.
export function epicSummaries() {
  const summaries = new Map();
  for (const node of store.nodes.values()) {
    if (node.kind === 'epic') {
      summaries.set(node.ref, { ref: node.ref, node, code: nodeCode('epic', node.number), title: node.title, stories: 0, tasks: 0, blocked: 0, late: 0, progress: progressOf(node), under: new Set(), linked: new Map() });
    }
  }
  const epicOfNode = (node) => {
    let up = node;
    while (up && up.kind !== 'epic') up = store.nodes.get(up.parent);
    return up || null;
  };
  for (const node of store.nodes.values()) {
    const top = node.kind === 'epic' ? null : summaries.get(epicOfNode(node)?.ref);
    if (!top) continue;
    top.under.add(node.ref);
    if (node.kind === 'story') top.stories += 1;
    else top.tasks += 1;
    if (node.status === 'blocked') top.blocked += 1;
    if (lateDays(node) > 0) top.late += 1;
  }
  for (const record of store.notes || []) {
    if (!inForce(record)) continue;
    for (const ref of record.nodes || []) {
      const top = summaries.get(epicOfNode(store.nodes.get(ref))?.ref);
      if (top) top.linked.set(record.id, record);
    }
  }
  return [...summaries.values()]
    .sort((a, b) => a.node.number - b.node.number)
    .map(({ under, linked, ...rest }) => ({ ...rest, notes: linked.size }));
}

// -- what is around one item ---------------------------------------------------------------------

// "decided by Priya on 2026-10-02", for the end of a reason.
const decidedPhrase = (note) => (note.decided_by ? decidedWords(note).replace(/^Decided/, 'decided') : '');
const sentence = (text) => (text ? `${text.charAt(0).toUpperCase()}${text.slice(1)}` : text);

// What recall says about an item, for the kinds recall works for, and what the index finds alike,
// for all of them. A refusal (no embedding service, say) is kept as a note, not thrown: Around still
// has everything that is linked exactly.
export async function fetchAround(item) {
  const asked = { ...(item.kind === 'rule' ? { rule_id: item.number } : { node: item.ref }), limit: 200, with_similar: true };
  const likeness = { work: { node: item.ref, types: [item.kind] }, rule: { rule_id: item.number, types: ['rule'] }, note: { note_id: item.number, types: ['note'] } }[item.work ? 'work' : item.kind];
  const readsRecall = item.work || item.kind === 'rule';
  const [recalled, alike] = await Promise.all([
    readsRecall ? call('recall', asked) : Promise.resolve(null),
    call('similar_items', { ...likeness, limit: 5 }).catch((error) => {
      if (!(error instanceof Refusal)) throw error;
      return { items: [], index: { meaning: 'unavailable', note: error.message } };
    }),
    ensure('references').catch(() => {}),
  ]);
  return { recalled, alike };
}

export const GROUPS = [
  { id: 'up', slot: 'n', icon: 'hier', line: 'hier' },
  { id: 'down', slot: 's', title: 'Contains', icon: 'hier', line: 'hier' },
  { id: 'needs', slot: 'w', title: 'Needs first', icon: 'arrow_right', line: 'needs', arrow: 'in' },
  { id: 'blocks', slot: 'e', title: 'Blocks', icon: 'lock', line: 'needs', arrow: 'out' },
  { id: 'reqs', slot: 'nw', title: 'Acceptance criteria', icon: 'rule', line: 'ctx', end: 'square' },
  { id: 'sim', slot: 'ne', title: 'Related and similar', icon: 'link', line: 'sim' },
  { id: 'notes', slot: 'sw', title: 'Notes', icon: 'diamond', line: 'ctx', end: 'diamond' },
];

export const upTitle = (kind) => (kind === 'rule' ? 'Placed on' : kind === 'note' ? 'Applies to' : 'Part of');

// The entry of one item in a group: `via` names the item it is reached through (at reach 2), `why`
// says in words why it is here.
const entry = (ref, why, extra = {}) => ({ ref, why, via: null, ...extra });

export function groupsFor(item, reach, answer) {
  const G = { up: [], down: [], needs: [], blocks: [], reqs: [], notes: [], wide: [], past: [], sim: [], wideMoved: 0, hints: [] };
  const seen = new Set();
  const add = (list, ref, why, extra) => {
    if (seen.has(`${list}:${ref}`) || !resolve(ref)) return null;
    seen.add(`${list}:${ref}`);
    const made = entry(ref, why, extra);
    G[list].push(made);
    return made;
  };
  const noun = KIND_NOUN[item.kind];
  const { recalled, alike } = answer;
  const linked = new Set();

  if (item.work) {
    const node = item.record;
    if (node.parent) {
      const parent = store.nodes.get(node.parent);
      add('up', node.parent, `The ${parent.kind} this ${noun} is part of`);
      if (reach > 1 && parent.parent) add('up', parent.parent, `The ${store.nodes.get(parent.parent).kind} that ${nodeCode(parent.kind, parent.number)} is part of`, { via: parent.ref });
    }
    for (const child of childrenOf(item.ref)) {
      add('down', child.ref, `Part of this ${noun}`);
      if (reach > 1) for (const grand of childrenOf(child.ref)) add('down', grand.ref, `Part of ${nodeCode(child.kind, child.number)}`, { via: child.ref });
    }
    const { needs, needed } = needMaps();
    const walk = (map, list, say) => {
      let frontier = [item.ref];
      for (let step = 1; step <= reach; step += 1) {
        const next = [];
        for (const from of frontier) {
          for (const ref of map.get(from) || []) {
            if (add(list, ref, say(step, from), { via: step > 1 ? from : null })) next.push(ref);
          }
        }
        frontier = next;
      }
    };
    walk(needs, 'needs', (step, from) => (step === 1 ? `This ${noun} needs it first` : `${resolve(from).code} needs it first`));
    walk(needed, 'blocks', (step, from) => (step === 1 ? `It needs this ${noun} first` : `It needs ${resolve(from).code} first`));
    // The acceptance criteria on the item itself. At reach 2 those on what is under an epic or a story, and
    // on the story above a task, are added too, each marked "via" the item they are on.
    for (const rule of store.rules || []) {
      const on = rule.node ? store.nodes.get(rule.node) : null;
      if (!on) continue;
      const code = resolve(on.ref).code;
      if (on.ref === item.ref) add('reqs', `rule:${rule.id}`, `Placed on this ${noun}`);
      else if (reach > 1 && item.kind === 'epic' && (on.kind === 'task' ? store.nodes.get(on.parent)?.parent : on.parent) === item.ref) add('reqs', `rule:${rule.id}`, `Placed on ${code}, under this epic`, { via: on.ref });
      else if (reach > 1 && item.kind === 'story' && on.parent === item.ref) add('reqs', `rule:${rule.id}`, `Placed on ${code}, a task of this story`, { via: on.ref });
      else if (reach > 1 && item.kind === 'task' && on.ref === node.parent) add('reqs', `rule:${rule.id}`, `Placed on ${code}, the story this task is part of`, { via: on.ref });
    }
  } else if (item.kind === 'rule') {
    const on = item.record.node && store.nodes.get(item.record.node);
    if (on) {
      add('up', on.ref, `The ${on.kind} this acceptance criterion is placed on`);
      const above = on.parent && store.nodes.get(on.parent);
      if (reach > 1 && above) add('up', above.ref, `The ${above.kind} that ${nodeCode(on.kind, on.number)} is part of`, { via: on.ref });
    }
  } else {
    const record = item.record;
    for (const ref of [...(record.nodes || [])].sort(byRef)) add('up', ref, 'The work this note applies to');
    for (const id of record.rule_ids || []) add('reqs', `rule:${id}`, 'An acceptance criterion this note applies to');
  }

  // Notes. What recall returns is exactly what an agent is told for this item: what is
  // linked to this item, and what comes down to it from the story and epic above it (marked "via"
  // the item it is linked to). At reach 2 what is linked to the children is added from the store,
  // also marked "via".
  const scope = [];
  if (reach > 1 && item.work) {
    if (item.record.parent) scope.push(item.record.parent);
    for (const child of childrenOf(item.ref)) scope.push(child.ref);
  } else if (reach > 1 && item.kind === 'rule' && item.record.node) scope.push(item.record.node);

  // The server writes "epic:3"; the board writes E-3.
  const codes = (text) => text.replace(/\b(epic|story|task):(\d+)\b/g, (_, kind, number) => nodeCode(kind, Number(number)));
  const noteWhy = (record, serverWhy) => {
    const base = sentence(codes(String(serverWhy || '')).replace(/\bthis (node|rule)\b/, `this ${noun}`).replace(/\bnote (\d+)/, 'note #$1'));
    const by = inForce(record) ? decidedPhrase(record) : '';
    return [base, by].filter(Boolean).join('; ');
  };
  if (recalled) {
    const wideNames = (record) => (record.nodes || []).some((ref) => ref === item.ref || scope.includes(ref));
    for (const { note, why, inherited_from: from } of recalled.notes.items) add('notes', `note:${note.id}`, noteWhy(note, why), { via: from || null });
    const wideHere = recalled.project_wide_notes.items.filter(({ note }) => wideNames(note));
    const fold = wideHere.length > 0 && wideHere.length <= 3;
    for (const { note, why } of recalled.project_wide_notes.items) {
      if (fold && wideHere.some((one) => one.note.id === note.id)) {
        add('notes', `note:${note.id}`, ['Project-wide, and linked to this ' + noun, decidedPhrase(note)].filter(Boolean).join('; '));
        G.wideMoved += 1;
      } else {
        add('wide', `note:${note.id}`, [sentence(why), decidedPhrase(note)].filter(Boolean).join('; '));
      }
    }
    for (const { note, why, inherited_from: from } of recalled.past_notes.items) add('past', `note:${note.id}`, noteWhy(note, why), { via: from || null });
    // The acceptance criteria that apply to the item: its own and, for a task, its story's (an epic has
    // none of its own; at reach 2 those on what is under it are added above).
    for (const { criterion, inherited_from: from } of recalled.acceptance_criteria?.items || []) {
      add('reqs', `rule:${criterion.id}`, from ? `Placed on ${resolve(from).code}, the story this ${noun} is part of` : `Placed on this ${noun}`, { via: from || null });
    }
  }
  const place = (record, via) => {
    const word = `Linked to ${resolve(via).code}`;
    if (record.project_wide && inForce(record)) return;
    if (inForce(record)) add('notes', `note:${record.id}`, [word, decidedPhrase(record)].filter(Boolean).join('; '), { via });
    else add('past', `note:${record.id}`, `${word}. ${record.status === 'superseded' ? `Replaced by note #${record.superseded_by_id}` : `Withdrawn: ${record.status_reason}`}; kept so the reason is not lost`, { via });
  };
  for (const via of scope) {
    for (const record of store.notes || []) if ((record.nodes || []).includes(via)) place(record, via);
  }

  // A note looked at directly: what it replaced, what replaced it, and what it is about or is about it.
  if (item.kind === 'note') {
    const record = item.record;
    for (const id of record.supersedes_ids || []) add('past', `note:${id}`, 'This note replaced it; kept so the reason is not lost');
    if (record.superseded_by_id) add(inForce(byId(store.notes).get(record.superseded_by_id) || {}) ? 'notes' : 'past', `note:${record.superseded_by_id}`, 'It replaced this note');
    for (const id of record.note_ids || []) {
      const other = byId(store.notes).get(id);
      if (other) add(inForce(other) ? 'notes' : 'past', `note:${id}`, 'A note this note is about');
    }
    for (const other of store.notes || []) if ((other.note_ids || []).includes(record.id)) add(inForce(other) ? 'notes' : 'past', `note:${other.id}`, 'This note is about it');
  }

  // Related and similar: links of a person's making, texts that name each other, and what the index
  // finds alike (suggestions only, and said so).
  if (item.work) {
    const kin = new Set([item.record.parent, ...childrenOf(item.ref).map((child) => child.ref)]);
    for (const { type, ref, outgoing } of needMaps().other.get(item.ref) || []) {
      const say = { relates_to: 'Linked as related', duplicates: 'Marked as a duplicate', supersedes: outgoing ? 'This one supersedes it' : 'It supersedes this one' }[type];
      if (add('sim', ref, say, { link: type })) linked.add(ref);
    }
    for (const ref of kin) linked.add(ref);
  }
  for (const mention of store.references || []) {
    if (!mention.target.exists) continue;
    const [from, to] = [mention.source.ref, mention.target.ref];
    if (from !== item.ref && to !== item.ref) continue;
    const other = from === item.ref ? to : from;
    if (other === item.ref || (item.work && resolve(other)?.work && (store.nodes.get(other)?.parent === item.ref || item.record.parent === other))) continue;
    if (add('sim', other, from === item.ref ? 'Named in the text of this item' : 'Its text names this item', { link: 'mentions' })) linked.add(other);
  }
  for (const found of [...(alike?.items || []), ...(recalled?.similar?.items || [])]) {
    if (linked.has(found.ref) || found.ref === item.ref) continue;
    const label = found.similarity === null || found.similarity === undefined ? 'matches by words' : `The index finds it ${found.similarity.toFixed(2)} alike; nobody linked it`;
    add('sim', found.ref, label, { score: found.similarity ?? null });
  }
  const hints = [alike?.index, recalled?.similar?.index].filter((index) => index && index.meaning !== 'used' && index.meaning !== 'not_requested');
  if (hints.length) G.hints.push(hints[0].note);
  return G;
}

export const isEmptyGroup = (id, G) => (id === 'notes' ? !(G.notes.length || G.wide.length || G.past.length) : !G[id].length);
