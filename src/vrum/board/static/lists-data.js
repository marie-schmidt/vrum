// What the Lists page shows, worked out from the store: every dot and line the kind of list allows
// (the "universe"), then the part of it that the scope shows. The work list writes the work out as a
// tree and the context list writes out the acceptance criteria and the notes, so both show the same
// records and the same relationships. Nothing here draws or keeps anything.
//
// A record is named the way the address names it: epic:3, story:12 or task:40 (the ref the server
// sends), rule:3 (shown as an acceptance criterion) or note:7. That name is the key of its dot and of its
// row. Every dot has a `key`, a `kind` (which is also its `type`), a `number` (its number within
// its kind; an acceptance criterion's or a note's own `id`), the `title` a person reads (a note's is its
// headline) and a `code` to read before it (S-12 for work, AC-3 for an acceptance criterion, N-7 for a
// note). A piece of work also has its `ref`; the others keep their `id`.

import { parseSel as readSel } from './ctx.js';
import { KIND_ORDER, inForce, noteCode, nodeCode, nodeRef, parseNodeRef, ruleCode } from './labels.js';
import { epicOf, headlineOf, store } from './store.js';

// The kinds of list: the work as an outline, and the two kinds of context.
export const LIST_KINDS = ['work', 'requirements', 'notes'];
export const LIST_WORDS = { work: 'Work', requirements: 'Acceptance criteria', notes: 'Notes' };
// Which kinds of context record each kind of list holds.
const CONTEXT_OF = {
  work: [],
  requirements: ['rule'],
  notes: ['note'],
};
// The old "Context" list held every kind of context; the one place that still links to it
// (the Board's acceptance criteria placed nowhere) means the acceptance criteria.
const OLD_NAMES = { context: 'requirements' };

// Where dots sort: epics, stories, tasks, then acceptance criteria and notes, each by number.
export const TYPE_ORDER = { ...KIND_ORDER, rule: 3, note: 4 };
export const byDot = (a, b) => TYPE_ORDER[a.type] - TYPE_ORDER[b.type] || a.number - b.number;

// How each kind of line reads from one end and from the other.
export const EDGE = {
  hierarchy: { label: 'contains', back: 'is in' },
  needs_first: { label: 'needs first', back: 'is needed first by' },
  supersedes: { label: 'supersedes', back: 'is superseded by' },
  duplicates: { label: 'duplicates', back: 'duplicates' },
  relates_to: { label: 'relates to', back: 'relates to' },
  discovered_from: { label: 'discovered from', back: 'led to' },
  placed: { label: 'is an acceptance criterion of', back: 'has the acceptance criterion' },
  concerns: { label: 'concerns', back: 'is concerned by' },
  replaces: { label: 'replaces', back: 'is replaced by' },
  mention: { label: 'mentions', back: 'is mentioned by' },
};

export const keyOf = (kind, number) => `${kind}:${number}`;

// The number of an epic's ref, which is what `epic=` in the address carries.
export const epicNumber = (ref) => parseNodeRef(ref)?.number ?? null;

// `sel` in the address: epic:3, story:12, task:40, rule:3 or note:7.
function parseSel(text) {
  const found = readSel(text);
  return found && { ...found, key: keyOf(found.kind, found.number) };
}

// The choices the address holds. With an epic in it the scope is that epic (`epic` is its ref; the
// address carries its number), and otherwise it is the whole project. `work` and `context` say which
// kinds of record the list draws, and `hideKind` is the kinds of context it does not hold. A `view` that
// is not one of the lists (the old Everything, say) is the work list.
export function settings(params) {
  const number = Number(params.get('epic')) || null;
  const epic = number ? nodeRef('epic', number) : null;
  const known = epic && store.nodes.get(epic)?.kind === 'epic' ? epic : null;
  const asked = OLD_NAMES[params.get('view')] || params.get('view');
  const view = LIST_KINDS.includes(asked) ? asked : 'work';
  return {
    view,
    work: view === 'work',
    context: view !== 'work',
    scope: known ? 'epic' : 'all',
    epic: known,
    q: (params.get('q') || '').trim(),
    hideKind: new Set(['rule', 'note'].filter((type) => !CONTEXT_OF[view].includes(type))),
    mentions: params.get('mentions') === '1',
    sel: parseSel(params.get('sel')),
  };
}

export const showsWork = (cfg) => cfg.work;
export const showsContext = (cfg) => cfg.context;

// What has to be loaded for these settings, besides the work itself. The acceptance criteria and the notes are
// always read, so that the kind switch can say how many of each the scope holds.
export function needs(cfg) {
  const wanted = ['rules', 'notes'];
  if (cfg.mentions) wanted.push('references');
  return wanted;
}

const clip = (text, size) => {
  const one = String(text ?? '').replace(/\s+/g, ' ').trim();
  return one.length > size ? `${one.slice(0, size - 1)}…` : one;
};

// Every dot and line the kind of list allows, wherever it is in the project.
export function universe(cfg) {
  const dots = new Map();
  const edges = [];
  const add = (dot) => dots.set(dot.key, dot);
  const work = showsWork(cfg);
  const context = showsContext(cfg);

  for (const node of store.nodes.values()) {
    add({ key: node.ref, kind: node.kind, type: node.kind, ref: node.ref, number: node.number, code: nodeCode(node.kind, node.number), title: node.title, status: node.status, epic: epicOf(node)?.ref ?? null, isNode: true, ended: false, record: node });
  }
  if (context) {
    if (!cfg.hideKind.has('rule')) {
      for (const rule of store.rules || []) {
        const holder = rule.node === null ? null : store.nodes.get(rule.node);
        add({ key: keyOf('rule', rule.id), kind: 'rule', type: 'rule', id: rule.id, number: rule.id, code: ruleCode(rule.id), title: rule.title, status: rule.status, epic: holder ? epicOf(holder)?.ref ?? null : null, ended: rule.status === 'dropped', text: rule.body_preview || rule.body || '', record: rule });
      }
    }
    if (!cfg.hideKind.has('note')) {
      for (const note of store.notes || []) {
        add({ key: keyOf('note', note.id), kind: 'note', type: 'note', id: note.id, number: note.id, code: noteCode(note.id), title: clip(headlineOf(note), 60), status: note.status, epic: null, ended: !inForce(note), projectWide: Boolean(note.project_wide), text: `${note.text} Why: ${note.why}`, record: note });
      }
    }
  }

  const edge = (type, from, to, extra) => {
    if (!dots.has(from) || !dots.has(to)) return;
    edges.push({ type, source: from, target: to, ...extra });
  };
  if (work) {
    for (const node of store.nodes.values()) {
      if (node.parent !== null) edge('hierarchy', node.parent, node.ref);
    }
    for (const link of store.links) edge(link.link_type, link.source, link.target);
  }
  if (context) {
    for (const rule of store.rules || []) {
      if (rule.node !== null) edge('placed', keyOf('rule', rule.id), rule.node);
    }
    for (const record of store.notes || []) {
      const from = keyOf('note', record.id);
      for (const ref of record.nodes || []) edge('concerns', from, ref);
      for (const id of record.rule_ids || []) edge('concerns', from, keyOf('rule', id));
      for (const id of record.note_ids || []) edge('concerns', from, keyOf('note', id));
      const older = new Set(record.supersedes_ids || []);
      for (const id of older) edge('replaces', from, keyOf('note', id));
      if (record.superseded_by_id && !(store.notes || []).some((other) => other.id === record.superseded_by_id && (other.supersedes_ids || []).includes(record.id))) {
        edge('replaces', keyOf('note', record.superseded_by_id), from);
      }
    }
  }
  if (cfg.mentions) {
    // The line runs from the record whose text holds the reference to the record it names. Only
    // references to things that exist, between dots that are there, make a line.
    const seen = new Set();
    for (const mention of store.references || []) {
      if (!mention.target.exists) continue;
      const [from, to] = [mention.source.ref, mention.target.ref];
      if (seen.has(`${from}>${to}`)) continue;
      seen.add(`${from}>${to}`);
      edge('mention', from, to);
    }
  }
  return { dots, edges };
}

// The dots a dot is joined to, with how, in the order they are listed under a row of the list.
export function neighboursOf(key, edges, dots) {
  const found = [];
  for (const edge of edges) {
    const outgoing = edge.source === key;
    if (!outgoing && edge.target !== key) continue;
    const other = dots.get(outgoing ? edge.target : edge.source);
    if (!other) continue;
    found.push({ dot: other, edge, outgoing });
  }
  return found.sort((a, b) => a.edge.type.localeCompare(b.edge.type) || byDot(a.dot, b.dot));
}

export const relationWords = ({ edge, outgoing }) => (outgoing ? EDGE[edge.type].label : EDGE[edge.type].back);

// The key of the dot the person chose.
export const chosenKey = (cfg) => cfg.sel?.key ?? null;

function subset(all, keys, notice, extra = {}) {
  const dots = new Map([...all.dots].filter(([key]) => keys.has(key)));
  const edges = all.edges.filter((edge) => keys.has(edge.source) && keys.has(edge.target));
  return { dots, edges, notice, epic: null, ...extra };
}

// Which records of context are in scope. A record is in when what it is joined to (through other
// records, too) includes work that is in scope, or includes no work at all: an acceptance criterion placed
// nowhere, a note about nothing yet, and a note that applies throughout the project
// are never hidden by the scope, since nothing else could lead a person to them.
function contextInScope(all, insideKeys, everything) {
  const records = [...all.dots.values()].filter((dot) => !dot.isNode);
  const parent = new Map(records.map((dot) => [dot.key, dot.key]));
  const root = (key) => {
    let at = key;
    while (parent.get(at) !== at) {
      parent.set(at, parent.get(parent.get(at)));
      at = parent.get(at);
    }
    return at;
  };
  const touched = new Map(); // root -> the work its records are joined to
  for (const edge of all.edges) {
    const [a, b] = [all.dots.get(edge.source), all.dots.get(edge.target)];
    if (!a || !b || (a.isNode && b.isNode)) continue;
    if (!a.isNode && !b.isNode) parent.set(root(a.key), root(b.key));
  }
  for (const edge of all.edges) {
    const [a, b] = [all.dots.get(edge.source), all.dots.get(edge.target)];
    if (!a || !b || a.isNode === b.isNode) continue;
    const [record, node] = a.isNode ? [b, a] : [a, b];
    const at = root(record.key);
    if (!touched.has(at)) touched.set(at, new Set());
    touched.get(at).add(node.key);
  }
  const keep = new Set();
  for (const dot of records) {
    const work = touched.get(root(dot.key));
    const loose = !work || work.size === 0;
    const unplaced = dot.type === 'rule' && dot.record.node === null;
    if (everything || loose || unplaced || dot.projectWide || [...work].some((key) => insideKeys.has(key))) keep.add(dot.key);
  }
  return keep;
}

// The part of the universe to show. `matched` is the set of work refs a work filter (`q`) asked
// for, or null for no filter. `notice` says anything the person has to be told about it.
export function view(cfg, all, matched = null) {
  const work = showsWork(cfg);
  const unfiltered = cfg.scope === 'all' && !matched;
  const nodes = [...all.dots.values()].filter((dot) => dot.isNode);
  const inside = new Set(
    nodes.filter((dot) => (cfg.scope === 'all' || dot.epic === cfg.epic) && (!matched || matched.has(dot.key))).map((dot) => dot.key),
  );
  const keep = new Set(work ? inside : []);
  const outside = new Set();
  if (work && matched) {
    // The branches above what matched stay, so that a match is still seen in its place.
    for (const key of inside) {
      let up = all.dots.get(key);
      while (up && up.record.parent !== null) {
        const parentKey = up.record.parent;
        if (!all.dots.has(parentKey)) break;
        if (!inside.has(parentKey)) outside.add(parentKey);
        up = all.dots.get(parentKey);
      }
    }
  }
  if (showsContext(cfg)) {
    for (const key of contextInScope(all, inside, unfiltered)) keep.add(key);
  }
  for (const edge of all.edges) {
    if (edge.type === 'hierarchy') continue;
    for (const [a, b] of [[edge.source, edge.target], [edge.target, edge.source]]) {
      const there = all.dots.get(b);
      if (keep.has(a) && !keep.has(b) && there.isNode) {
        if (inside.has(b)) keep.add(b);
        else outside.add(b);
      }
    }
  }
  const shown = subset(all, new Set([...keep, ...outside]), '', { epic: cfg.epic });
  for (const key of outside) if (!keep.has(key)) shown.dots.get(key).outside = true;
  if (!shown.dots.size) {
    shown.notice = matched ? 'Nothing matches the filter.' : all.dots.size ? 'There is nothing in this scope.' : 'There is nothing to show yet.';
  }
  return shown;
}

// How many of each kind of record the scope holds (the epic and the filter, but not the status or the words a
// list has of its own), whichever list is showing, so that the kind switch can say it. The work the filter does
// not match but that is drawn only to show a match in its place is not counted.
export function scopeCounts(cfg, matched = null) {
  const wide = { ...cfg, work: true, context: true, hideKind: new Set(), mentions: false };
  const shown = view(wide, universe(wide), matched);
  const count = { work: 0, requirements: 0, notes: 0 };
  for (const dot of shown.dots.values()) {
    if (dot.isNode) count.work += dot.outside ? 0 : 1;
    else count[dot.type === 'rule' ? 'requirements' : 'notes'] += 1;
  }
  return count;
}
