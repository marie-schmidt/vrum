// What the Graph page shows, worked out from the store: every dot and line the preset and the
// switches allow (the "universe"), then the part of it that the scope shows. The map draws it and
// the list writes it out, so both show the same records and the same relationships. Nothing here
// draws or keeps anything.
//
// A record is named the way the address names it: node:12 (an epic, story or task), rule:3 (shown
// as a requirement), decision:7 or fact:5. That name is the key of its dot and of its row.

import { parseSel as readSel } from './ctx.js';
import { epicOf, epics, store } from './store.js';

// The most dots drawn at once. Above it a whole project shows one dot for each epic.
export const DOT_LIMIT = 1000;

export const PRESETS = ['work', 'context', 'everything'];
export const PRESET_WORDS = { work: 'Work', context: 'Context', everything: 'Everything' };
export const PRESENTATIONS = ['map', 'list'];
export const COLOURS = ['kind', 'status', 'epic'];

// What each kind of dot is called and drawn as. `kind` is what the address calls it.
export const TYPES = {
  epic: { label: 'Epic', plural: 'Epics', sign: '■', kind: 'node' },
  story: { label: 'Story', plural: 'Stories', sign: '●', kind: 'node' },
  task: { label: 'Task', plural: 'Tasks', sign: '◆', kind: 'node' },
  rule: { label: 'Requirement', plural: 'Requirements', sign: '▲', kind: 'rule' },
  decision: { label: 'Decision', plural: 'Decisions', sign: '⬢', kind: 'decision' },
  fact: { label: 'Fact', plural: 'Facts', sign: '▪', kind: 'fact' },
};

// How each kind of line reads from one end and from the other, and which switch turns it off.
export const EDGE = {
  hierarchy: { label: 'contains', back: 'is in', group: 'contains' },
  needs_first: { label: 'needs first', back: 'is needed first by', group: 'needs_first' },
  supersedes: { label: 'supersedes', back: 'is superseded by', group: 'supersedes' },
  duplicates: { label: 'duplicates', back: 'duplicates', group: 'duplicates' },
  relates_to: { label: 'relates to', back: 'relates to', group: 'relates_to' },
  placed: { label: 'is a requirement of', back: 'has the requirement', group: 'about' },
  concerns: { label: 'concerns', back: 'is concerned by', group: 'about' },
  replaces: { label: 'replaces', back: 'is replaced by', group: 'replaces' },
  mention: { label: 'mentions', back: 'is mentioned by', group: 'mention' },
  collapsed: { label: 'links between', back: 'links between', group: 'collapsed' },
};
export const LINK_SWITCHES = [
  ['contains', 'Contains (epic, story, task)'],
  ['needs_first', 'Needs first'],
  ['supersedes', 'Supersedes'],
  ['duplicates', 'Duplicates'],
  ['relates_to', 'Relates to'],
  ['about', 'Requirements, decisions and facts, and what they concern'],
  ['replaces', 'What replaced what'],
];

const list = (params, name) => new Set((params.get(name) || '').split(',').filter(Boolean));
const pick = (value, allowed, fallback) => (allowed.includes(value) ? value : fallback);
export const keyOf = (kind, id) => `${kind}:${id}`;

// `sel` in the address: node:12, rule:3, decision:7 or fact:5.
function parseSel(text) {
  const found = readSel(text);
  return found && { ...found, key: keyOf(found.kind, found.id) };
}

// The choices the address holds. With an epic in it the scope is that epic, with scope=local it
// is what is around the chosen record, and otherwise it is the whole project.
export function settings(params) {
  const depth = Number(params.get('depth')) || 1;
  const epic = Number(params.get('epic')) || null;
  const known = epic && store.nodes.get(epic)?.kind === 'epic' ? epic : null;
  return {
    view: pick(params.get('view'), PRESETS, 'work'),
    as: pick(params.get('as'), PRESENTATIONS, 'map'),
    scope: params.get('scope') === 'local' ? 'local' : known ? 'epic' : 'all',
    epic: known,
    depth: Math.min(3, Math.max(1, depth)),
    q: (params.get('q') || '').trim(),
    hideKind: list(params, 'hide_kind'),
    hideStatus: list(params, 'hide_status'),
    hideLink: list(params, 'hide_link'),
    colour: pick(params.get('colour'), COLOURS, 'kind'),
    mentions: params.get('mentions') === '1',
    ended: params.get('ended') === '1',
    sel: parseSel(params.get('sel')),
  };
}

export const showsWork = (cfg) => cfg.view !== 'context';
export const showsContext = (cfg) => cfg.view !== 'work';

// What has to be loaded for these settings, besides the work itself.
export function needs(cfg) {
  const wanted = [];
  if (showsContext(cfg)) wanted.push('rules', 'decisions', 'facts');
  if (cfg.mentions) wanted.push('references');
  return wanted;
}

export const confirmationOf = (record) =>
  record.confirmation || (record.confirmed_by && record.confirmed_at ? 'confirmed' : 'unconfirmed');

const clip = (text, size) => {
  const one = String(text ?? '').replace(/\s+/g, ' ').trim();
  return one.length > size ? `${one.slice(0, size - 1)}…` : one;
};

// The words an assistive technology reads for a dot, and a row's tooltip.
export function wordsFor(dot) {
  const what = `${TYPES[dot.type].label} ${dot.id}: ${dot.title}`;
  if (dot.type === 'decision' || dot.type === 'fact') {
    const record = dot.record;
    const sure = dot.confirmation === 'confirmed' ? `Confirmed${record.confirmed_by ? ` by ${record.confirmed_by}` : ''}` : 'Unconfirmed';
    const life = record.status === 'active' ? 'In force' : record.status === 'superseded' ? 'Replaced' : 'Withdrawn';
    return `${what}. ${sure}. ${life}.${dot.projectWide ? ' Applies throughout the project.' : ''}`;
  }
  if (dot.type === 'rule') return `${what}. ${dot.status === 'held' ? 'Held' : dot.status === 'settled' ? 'Settled' : 'Dropped'}.${dot.record.story_id === null ? ' In no story.' : ''}`;
  return `${what}. ${dot.status.replace('_', ' ')}.`;
}

// Every dot and line the preset and the switches allow, wherever it is in the project.
export function universe(cfg) {
  const dots = new Map();
  const edges = [];
  const add = (dot) => dots.set(dot.key, dot);
  const work = showsWork(cfg);
  const context = showsContext(cfg);

  for (const node of store.nodes.values()) {
    if (cfg.hideKind.has(node.kind) || cfg.hideStatus.has(node.status)) continue;
    add({ key: keyOf('node', node.id), kind: 'node', type: node.kind, id: node.id, title: node.title, status: node.status, epicId: epicOf(node)?.id ?? null, isNode: true, ended: false, record: node });
  }
  if (context) {
    if (!cfg.hideKind.has('rule')) {
      for (const rule of store.rules || []) {
        const story = rule.story_id === null ? null : store.nodes.get(rule.story_id);
        add({ key: keyOf('rule', rule.id), kind: 'rule', type: 'rule', id: rule.id, title: rule.title, status: rule.status, epicId: story ? epicOf(story)?.id ?? null : null, ended: rule.status === 'dropped', text: rule.body_preview || rule.body || '', record: rule });
      }
    }
    if (!cfg.hideKind.has('decision')) {
      for (const decision of store.decisions || []) {
        add({ key: keyOf('decision', decision.id), kind: 'decision', type: 'decision', id: decision.id, title: decision.title, status: decision.status, epicId: null, ended: decision.status !== 'active', confirmation: confirmationOf(decision), projectWide: Boolean(decision.project_wide), text: `${decision.text} Why: ${decision.reason}`, record: decision });
      }
    }
    if (!cfg.hideKind.has('fact')) {
      for (const fact of store.facts || []) {
        add({ key: keyOf('fact', fact.id), kind: 'fact', type: 'fact', id: fact.id, title: clip(fact.text, 60), status: fact.status, epicId: null, ended: fact.status !== 'active', confirmation: confirmationOf(fact), text: fact.text, record: fact });
      }
    }
  }

  const edge = (type, from, to, extra) => {
    if (!dots.has(from) || !dots.has(to) || cfg.hideLink.has(EDGE[type].group)) return;
    edges.push({ type, source: from, target: to, ...extra });
  };
  if (work) {
    for (const node of store.nodes.values()) {
      if (node.parent_id !== null) edge('hierarchy', keyOf('node', node.parent_id), keyOf('node', node.id));
    }
    for (const link of store.links) edge(link.link_type, keyOf('node', link.source_id), keyOf('node', link.target_id));
  }
  if (context) {
    for (const rule of store.rules || []) {
      if (rule.story_id !== null) edge('placed', keyOf('rule', rule.id), keyOf('node', rule.story_id));
    }
    for (const [kind, records] of [['decision', store.decisions], ['fact', store.facts]]) {
      for (const record of records || []) {
        const from = keyOf(kind, record.id);
        for (const id of record.node_ids || []) edge('concerns', from, keyOf('node', id));
        for (const id of record.rule_ids || []) edge('concerns', from, keyOf('rule', id));
        for (const id of record.decision_ids || []) edge('concerns', from, keyOf('decision', id));
        const older = new Set(record.supersedes_ids || []);
        for (const id of older) edge('replaces', from, keyOf(kind, id));
        if (record.superseded_by_id && !(store[`${kind}s`] || []).some((other) => other.id === record.superseded_by_id && (other.supersedes_ids || []).includes(record.id))) {
          edge('replaces', keyOf(kind, record.superseded_by_id), from);
        }
      }
    }
  }
  if (cfg.mentions) {
    // The line runs from the record whose text holds the reference to the record it names. Only
    // references to things that exist, between dots that are drawn, make a line.
    const kindOf = (type) => (['epic', 'story', 'task', 'node'].includes(type) ? 'node' : type);
    const seen = new Set();
    for (const mention of store.references || []) {
      if (!mention.target.exists) continue;
      const from = keyOf(kindOf(mention.source.item_type), mention.source.id);
      const to = keyOf(kindOf(mention.target.kind), mention.target.id);
      if (seen.has(`${from}>${to}`)) continue;
      seen.add(`${from}>${to}`);
      edge('mention', from, to);
    }
  }
  return { dots, edges };
}

// The dots a dot is joined to, with how, in the order they are listed beside the drawing and
// under a row of the list.
export function neighboursOf(key, edges, dots) {
  const found = [];
  for (const edge of edges) {
    const outgoing = edge.source === key;
    if (!outgoing && edge.target !== key) continue;
    const other = dots.get(outgoing ? edge.target : edge.source);
    if (!other) continue;
    found.push({ dot: other, edge, outgoing });
  }
  return found.sort((a, b) => a.edge.type.localeCompare(b.edge.type) || a.dot.id - b.dot.id);
}

export const relationWords = ({ edge, outgoing }) => (outgoing ? EDGE[edge.type].label : EDGE[edge.type].back);

// The key of the dot the person chose.
export const chosenKey = (cfg) => cfg.sel?.key ?? null;

function subset(all, keys, notice, extra = {}) {
  const dots = new Map([...all.dots].filter(([key]) => keys.has(key)));
  const edges = all.edges.filter((edge) => keys.has(edge.source) && keys.has(edge.target));
  return { dots, edges, collapsed: false, notice, epicId: null, ...extra };
}

// One dot for each epic, with the number of dots under it, and one line between two epics
// whatever joins them.
function collapse(all) {
  const counts = new Map();
  for (const dot of all.dots.values()) {
    if (dot.isNode && dot.epicId !== null) counts.set(dot.epicId, (counts.get(dot.epicId) || 0) + 1);
  }
  const dots = new Map();
  for (const epic of epics()) {
    const key = keyOf('node', epic.id);
    if (!all.dots.has(key)) continue;
    dots.set(key, { ...all.dots.get(key), collapsed: true, count: (counts.get(epic.id) || 1) - 1 });
  }
  const between = new Map();
  for (const edge of all.edges) {
    const [a, b] = [all.dots.get(edge.source), all.dots.get(edge.target)];
    if (edge.type === 'hierarchy' || !a?.isNode || !b?.isNode || a.epicId === b.epicId) continue;
    const pair = [a.epicId, b.epicId].sort((x, y) => x - y).join('-');
    const found = between.get(pair) || { type: 'collapsed', source: keyOf('node', a.epicId), target: keyOf('node', b.epicId), count: 0 };
    found.count += 1;
    between.set(pair, found);
  }
  return { dots, edges: [...between.values()] };
}

// Which records of context are in scope. A record is in when what it is joined to (through other
// records, too) includes work that is in scope, or includes no work at all: a requirement in no
// story, a fact or decision about nothing yet, and a decision that applies throughout the project
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
    const unplaced = dot.type === 'rule' && dot.record.story_id === null;
    if (everything || loose || unplaced || dot.projectWide || [...work].some((key) => insideKeys.has(key))) keep.add(dot.key);
  }
  return keep;
}

// The part of the universe to show. `matched` is the set of work ids a work filter (`q`) asked
// for, or null for no filter. `notice` says anything the person has to be told about it.
export function view(cfg, all, matched = null) {
  const work = showsWork(cfg);
  const chosen = chosenKey(cfg);
  if (cfg.scope === 'local') {
    if (!chosen || !all.dots.has(chosen)) {
      return { dots: new Map(), edges: [], collapsed: false, notice: 'Choose a record, a dot or one from the list, to see what is around it.', epicId: null };
    }
    const reached = new Set([chosen]);
    let frontier = [chosen];
    for (let step = 0; step < cfg.depth; step += 1) {
      const next = [];
      for (const edge of all.edges) {
        for (const [from, to] of [[edge.source, edge.target], [edge.target, edge.source]]) {
          if (frontier.includes(from) && !reached.has(to)) {
            reached.add(to);
            next.push(to);
          }
        }
      }
      frontier = next;
    }
    return subset(all, reached, `Around ${all.dots.get(chosen).title}, ${cfg.depth} ${cfg.depth === 1 ? 'step' : 'steps'} out.`);
  }

  const unfiltered = cfg.scope === 'all' && !matched;
  if (unfiltered && all.dots.size > DOT_LIMIT) {
    if (!work) {
      return { dots: new Map(), edges: [], collapsed: false, notice: `There are ${all.dots.size.toLocaleString()} records, more than the ${DOT_LIMIT.toLocaleString()} dots that can be drawn at once. Choose an epic, or use the list.`, epicId: null };
    }
    return { ...collapse(all), collapsed: true, notice: `This project has ${all.dots.size.toLocaleString()} dots, more than the ${DOT_LIMIT.toLocaleString()} that can be drawn at once, so each epic is one dot. Choose an epic’s dot to open that epic, or use the list.`, epicId: null };
  }

  const nodes = [...all.dots.values()].filter((dot) => dot.isNode);
  const inside = new Set(
    nodes.filter((dot) => (cfg.scope === 'all' || dot.epicId === cfg.epic) && (!matched || matched.has(dot.id))).map((dot) => dot.key),
  );
  const keep = new Set(work ? inside : []);
  const outside = new Set();
  if (work && matched) {
    // The branches above what matched stay, so that a match is still seen in its place.
    for (const key of inside) {
      let up = all.dots.get(key);
      while (up && up.record.parent_id !== null) {
        const parentKey = keyOf('node', up.record.parent_id);
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
  const shown = subset(all, new Set([...keep, ...outside]), '', { epicId: cfg.epic });
  for (const key of outside) if (!keep.has(key)) shown.dots.get(key).outside = true;
  if (!shown.dots.size) {
    shown.notice = matched ? 'Nothing matches the filter.' : all.dots.size ? 'There is nothing in this scope.' : 'There is nothing to show yet.';
  }
  return shown;
}

// What the map draws: the shown dots without the ones that have ended (a replaced or withdrawn
// decision or fact, a dropped requirement), unless asked for. The chosen one is always drawn.
export function drawable(cfg, shown) {
  if (cfg.ended || shown.collapsed) return shown;
  const chosen = chosenKey(cfg);
  const keep = new Set([...shown.dots].filter(([key, dot]) => !dot.ended || key === chosen).map(([key]) => key));
  if (keep.size === shown.dots.size) return shown;
  return subset(shown, keep, shown.notice, { epicId: shown.epicId });
}
