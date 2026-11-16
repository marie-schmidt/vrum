// What the graph view draws, worked out from the store: every item and every connection that the
// switches allow (the "universe"), then the part of it that the chosen scope shows. Nothing here
// draws or keeps anything.

import { epicOf, epics, store } from './store.js';

// The most dots drawn at once. Above it the whole graph shows one dot for each epic.
export const DOT_LIMIT = 1000;

export const SCOPES = ['epic', 'all', 'local'];
export const EXTRAS = ['rules', 'decisions', 'facts', 'beliefs'];
export const COLOURS = ['kind', 'status', 'epic'];

// How each kind of line reads, and which switch turns it off.
export const EDGE = {
  hierarchy: { label: 'contains', group: 'contains' },
  needs_first: { label: 'needs first', group: 'needs_first' },
  supersedes: { label: 'supersedes', group: 'supersedes' },
  duplicates: { label: 'duplicates', group: 'duplicates' },
  relates_to: { label: 'relates to', group: 'relates_to' },
  placed: { label: 'rule of', group: 'about' },
  concerns: { label: 'concerns', group: 'about' },
  evidence: { label: 'rests on', group: 'about' },
  overlap: { label: 'might be the same as', group: 'overlap' },
  mention: { label: 'mentions', group: 'mention' },
  collapsed: { label: 'links between', group: 'collapsed' },
};
export const LINK_SWITCHES = [
  ['contains', 'Contains (epic, story, task)'],
  ['needs_first', 'Needs first'],
  ['supersedes', 'Supersedes'],
  ['duplicates', 'Duplicates'],
  ['relates_to', 'Relates to'],
  ['about', 'Rules, decisions, facts and beliefs and what they are about'],
];

const list = (params, name) => new Set((params.get(name) || '').split(',').filter(Boolean));
const pick = (value, allowed, fallback) => (allowed.includes(value) ? value : fallback);

// The choices the address holds.
export function settings(params) {
  const depth = Number(params.get('depth')) || 1;
  return {
    scope: pick(params.get('scope'), SCOPES, 'epic'),
    epic: Number(params.get('epic')) || null,
    depth: Math.min(3, Math.max(1, depth)),
    show: list(params, 'show'),
    hideKind: list(params, 'hide_kind'),
    hideStatus: list(params, 'hide_status'),
    hideLink: list(params, 'hide_link'),
    colour: pick(params.get('colour'), COLOURS, 'kind'),
    overlaps: params.get('overlaps') === '1',
    mentions: params.get('mentions') === '1',
    card: Number(params.get('card')) || null,
    sel: params.get('sel') || null,
  };
}

// What has to be loaded for these settings, besides the work itself.
export function needs(cfg) {
  const wanted = [];
  if (cfg.show.has('rules')) wanted.push('rules');
  if (cfg.show.has('decisions')) wanted.push('decisions');
  if (cfg.show.has('facts') || cfg.show.has('beliefs')) wanted.push('memories');
  if (cfg.overlaps) wanted.push('overlaps');
  if (cfg.mentions) wanted.push('references');
  return wanted;
}

const nodeKey = (id) => `n:${id}`;

// Every dot and line the switches allow, wherever it is in the graph.
export function universe(cfg) {
  const dots = new Map();
  const edges = [];
  const add = (dot) => dots.set(dot.key, dot);

  for (const node of store.nodes.values()) {
    if (cfg.hideKind.has(node.kind) || cfg.hideStatus.has(node.status)) continue;
    add({ key: nodeKey(node.id), type: node.kind, id: node.id, title: node.title, status: node.status, epicId: epicOf(node)?.id ?? null, isNode: true });
  }
  if (cfg.show.has('rules') && store.rules) {
    for (const rule of store.rules.filter((item) => item.status !== 'dropped')) {
      add({ key: `r:${rule.id}`, type: 'rule', id: rule.id, title: rule.title, status: rule.status, epicId: epicOf(store.nodes.get(rule.story_id))?.id ?? null, text: rule.body_preview || rule.body || '' });
    }
  }
  if (cfg.show.has('decisions') && store.decisions) {
    for (const decision of store.decisions.filter((item) => item.status === 'active')) {
      add({ key: `d:${decision.id}`, type: decision.kind, id: decision.id, title: decision.title, status: decision.status, text: `${decision.text} Why: ${decision.reason}`, by: decision.decided_by });
    }
  }
  if (store.memories) {
    for (const memory of store.memories.filter((item) => item.status === 'active')) {
      const wanted = memory.type === 'fact' ? cfg.show.has('facts') : cfg.show.has('beliefs');
      if (!wanted) continue;
      add({ key: `m:${memory.id}`, type: memory.type, id: memory.id, title: memory.text.length > 60 ? `${memory.text.slice(0, 57)}…` : memory.text, status: memory.stale ? 'stale' : memory.status, text: memory.text, origin: memory.origin, by: memory.recorded_by, stale: memory.stale });
    }
  }

  const edge = (type, from, to, extra) => {
    if (!dots.has(from) || !dots.has(to) || cfg.hideLink.has(EDGE[type].group)) return;
    edges.push({ type, source: from, target: to, ...extra });
  };
  for (const node of store.nodes.values()) {
    if (node.parent_id !== null) edge('hierarchy', nodeKey(node.parent_id), nodeKey(node.id));
  }
  for (const link of store.links) edge(link.link_type, nodeKey(link.source_id), nodeKey(link.target_id));
  for (const dot of dots.values()) {
    if (dot.type === 'rule') {
      const rule = store.rules.find((item) => item.id === dot.id);
      if (rule.story_id !== null) edge('placed', dot.key, nodeKey(rule.story_id));
    }
  }
  for (const decision of store.decisions || []) {
    for (const id of decision.node_ids) edge('concerns', `d:${decision.id}`, nodeKey(id));
    for (const id of decision.rule_ids) edge('concerns', `d:${decision.id}`, `r:${id}`);
  }
  for (const memory of store.memories || []) {
    const from = `m:${memory.id}`;
    for (const id of memory.node_ids) edge('concerns', from, nodeKey(id));
    for (const id of memory.rule_ids) edge('concerns', from, `r:${id}`);
    for (const id of memory.decision_ids) edge('concerns', from, `d:${id}`);
    for (const item of memory.evidence) edge('evidence', from, item.type === 'fact' ? `m:${item.id}` : `d:${item.id}`);
  }
  if (cfg.overlaps) {
    for (const pair of store.overlaps || []) {
      edge('overlap', nodeKey(pair.first.id), nodeKey(pair.second.id), { similarity: pair.similarity });
    }
  }
  if (cfg.mentions) {
    // The line runs from the item whose text holds the reference to the item it names. Only
    // references to things that exist, between dots that are drawn, make a line.
    const keyOf = (type, id) => (['epic', 'story', 'task', 'node'].includes(type) ? nodeKey(id) : type === 'rule' ? `r:${id}` : ['decision', 'principle'].includes(type) ? `d:${id}` : `m:${id}`);
    const seen = new Set();
    for (const mention of store.references || []) {
      if (!mention.target.exists) continue;
      const from = keyOf(mention.source.item_type, mention.source.id);
      const to = keyOf(mention.target.kind, mention.target.id);
      if (seen.has(`${from}>${to}`)) continue;
      seen.add(`${from}>${to}`);
      edge('mention', from, to);
    }
  }
  return { dots, edges };
}

// The dots a dot is joined to, with how, in the order they are listed beside the drawing.
export function neighboursOf(key, edges, dots) {
  const found = [];
  for (const edge of edges) {
    if (edge.type === 'overlap') continue;
    const outgoing = edge.source === key;
    if (!outgoing && edge.target !== key) continue;
    const other = dots.get(outgoing ? edge.target : edge.source);
    if (!other) continue;
    found.push({ dot: other, edge, outgoing });
  }
  return found.sort((a, b) => a.edge.type.localeCompare(b.edge.type) || a.dot.id - b.dot.id);
}

// The key of the dot the person chose: a node by its card, anything else by `sel`.
export function chosenKey(cfg) {
  if (cfg.card) return nodeKey(cfg.card);
  return cfg.sel;
}

function within(all, keys, extraRounds) {
  const keep = new Set(keys);
  const touching = (dot) => !dot.isNode;
  for (let round = 0; round < extraRounds; round += 1) {
    for (const edge of all.edges) {
      if (edge.type === 'overlap') continue;
      const [a, b] = [edge.source, edge.target];
      if (keep.has(a) && !keep.has(b) && touching(all.dots.get(b))) keep.add(b);
      else if (keep.has(b) && !keep.has(a) && touching(all.dots.get(a))) keep.add(a);
    }
  }
  return keep;
}

function collapse(all) {
  const counts = new Map();
  for (const dot of all.dots.values()) {
    if (dot.isNode && dot.epicId !== null) counts.set(dot.epicId, (counts.get(dot.epicId) || 0) + 1);
  }
  const dots = new Map();
  for (const epic of epics()) {
    if (!all.dots.has(nodeKey(epic.id))) continue;
    dots.set(nodeKey(epic.id), { ...all.dots.get(nodeKey(epic.id)), collapsed: true, count: (counts.get(epic.id) || 1) - 1 });
  }
  const between = new Map();
  for (const edge of all.edges) {
    const [a, b] = [all.dots.get(edge.source), all.dots.get(edge.target)];
    if (edge.type === 'hierarchy' || !a?.isNode || !b?.isNode || a.epicId === b.epicId) continue;
    const pair = [a.epicId, b.epicId].sort((x, y) => x - y).join('-');
    const found = between.get(pair) || { type: 'collapsed', source: nodeKey(a.epicId), target: nodeKey(b.epicId), count: 0 };
    found.count += 1;
    between.set(pair, found);
  }
  return { dots, edges: [...between.values()] };
}

// The part of the universe to draw. `notice` says anything the person has to be told about it.
export function view(cfg, all) {
  const chosen = chosenKey(cfg);
  if (cfg.scope === 'all') {
    if (all.dots.size > DOT_LIMIT) {
      const shown = collapse(all);
      return {
        ...shown,
        collapsed: true,
        notice: `Everything has ${all.dots.size.toLocaleString()} items, more than the ${DOT_LIMIT.toLocaleString()} dots that can be drawn at once, so each epic is one dot. Choose an epic’s dot to open that epic.`,
      };
    }
    return { dots: all.dots, edges: all.edges, collapsed: false, notice: '' };
  }
  if (cfg.scope === 'local') {
    if (!chosen || !all.dots.has(chosen)) {
      return { dots: new Map(), edges: [], collapsed: false, notice: 'Choose an item, a dot or one from the list, to see what is around it.' };
    }
    const reached = new Set([chosen]);
    let frontier = [chosen];
    for (let step = 0; step < cfg.depth; step += 1) {
      const next = [];
      for (const edge of all.edges) {
        if (edge.type === 'overlap') continue;
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
  const epicList = epics().sort((a, b) => a.id - b.id);
  const epicId = cfg.epic && store.nodes.get(cfg.epic)?.kind === 'epic' ? cfg.epic : epicList[0]?.id ?? null;
  if (epicId === null) return { dots: new Map(), edges: [], collapsed: false, notice: 'There is nothing to draw yet.' };
  const inside = [...all.dots.values()].filter((dot) => dot.isNode && dot.epicId === epicId).map((dot) => dot.key);
  const keep = within(all, inside, 2);
  const outside = new Set();
  for (const edge of all.edges) {
    if (edge.type === 'hierarchy') continue;
    for (const [a, b] of [[edge.source, edge.target], [edge.target, edge.source]]) {
      if (keep.has(a) && !keep.has(b) && all.dots.get(b).isNode && all.dots.get(a).isNode) outside.add(b);
    }
  }
  const shown = subset(all, new Set([...keep, ...outside]), '');
  for (const key of outside) shown.dots.get(key).outside = true;
  shown.epicId = epicId;
  return shown;
}

function subset(all, keys, notice) {
  const dots = new Map([...all.dots].filter(([key]) => keys.has(key)));
  const edges = all.edges.filter((edge) => keys.has(edge.source) && keys.has(edge.target));
  return { dots, edges, collapsed: false, notice };
}
