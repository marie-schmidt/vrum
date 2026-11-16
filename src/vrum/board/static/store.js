// What the board knows: the work (nodes and the links between them), and, once a page asks for
// them, the rules, decisions, facts and beliefs. It is a copy of what the server holds, fetched
// with the same tools an agent uses and refreshed from the same change feed. The server is the
// only place anything is kept.

import { Refusal, call, callAll, exported, get } from './api.js';

export const store = {
  nodes: new Map(), // id -> node
  links: [], // { id, source_id, target_id, link_type }
  rules: null, // loaded when a page asks for them
  decisions: null,
  memories: null,
  overlaps: null, // pairs of stories the index thinks may be the same
  overlapNote: null, // why there are none, when the index could not be used
  info: { poll_seconds: 5, language_model: null },
  index: null, // what the index can do now, and how many items are waiting
  cursor: 0, // the newest change event seen
};

const listeners = new Set();

// fn({ events }) is called when the server's data changed, and fn({ index }) when the index did.
export function onChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit(change) {
  listeners.forEach((fn) => fn(change));
}

// Pairs of stories the index thinks may be the same. Without an index there are none, and the
// server's words say why.
async function loadOverlaps() {
  const pairs = [];
  let offset = 0;
  try {
    while (offset !== null && offset !== undefined) {
      const page = await call('find_overlaps', { limit: 200, offset });
      pairs.push(...page.items);
      offset = page.next_offset;
    }
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    store.overlapNote = error.message;
    return [];
  }
  store.overlapNote = null;
  return pairs;
}

const LOADERS = {
  rules: () => callAll('list_rules', { detail: true }),
  decisions: () => callAll('list_decisions', {}),
  memories: () => callAll('list_memories', {}),
  overlaps: loadOverlaps,
};

// Load what a page needs beyond the work itself, and keep it fresh from then on.
export async function ensure(name) {
  if (store[name] === null) store[name] = await LOADERS[name]();
}

async function reload() {
  const [nodes, links] = await Promise.all([callAll('list_nodes', {}), exported('links')]);
  store.nodes = new Map(nodes.map((node) => [node.id, node]));
  store.links = links;
  for (const name of Object.keys(LOADERS)) {
    if (store[name] !== null) store[name] = await LOADERS[name]();
  }
}

async function refreshIndex() {
  const index = await get('/board/api/index');
  const changed =
    !store.index ||
    store.index.waiting_to_index !== index.waiting_to_index ||
    store.index.meaning !== index.meaning;
  store.index = index;
  if (changed) emit({ index });
}

export async function start() {
  store.info = await get('/board/api/info');
  const head = await call('changes_since', { after_event_id: 0, limit: 1 });
  store.cursor = head.latest_event_id;
  await reload();
  await refreshIndex();
}

// Ask what changed since the last look. When something did, load the data again and say so.
// Used by the timer, and straight after the board's own change so that it shows at once.
export async function sync() {
  const events = [];
  let after = store.cursor;
  let latest = after;
  for (;;) {
    const page = await call('changes_since', { after_event_id: after, limit: 200 });
    events.push(...page.items);
    latest = page.latest_event_id;
    if (page.next_after_id === null || page.next_after_id === undefined) break;
    after = page.next_after_id;
  }
  store.cursor = Math.max(store.cursor, latest);
  await refreshIndex();
  if (events.length) {
    await reload();
    emit({ events });
  }
  return events;
}

let timer = null;

export function keepSynced(onTrouble) {
  stopSyncing();
  const tick = async () => {
    try {
      await sync();
    } catch (error) {
      onTrouble(error);
    }
    timer = setTimeout(tick, store.info.poll_seconds * 1000);
  };
  timer = setTimeout(tick, store.info.poll_seconds * 1000);
}

export function stopSyncing() {
  if (timer !== null) clearTimeout(timer);
  timer = null;
}

// -- reading the work ---------------------------------------------------------------------------

export const nodeOf = (id) => store.nodes.get(id);
export const epics = () => [...store.nodes.values()].filter((node) => node.kind === 'epic');
export const childrenOf = (id) =>
  [...store.nodes.values()].filter((node) => node.parent_id === id);

export function epicOf(node) {
  let current = node;
  while (current && current.kind !== 'epic') current = store.nodes.get(current.parent_id);
  return current || null;
}

export function titleOf(id) {
  const node = store.nodes.get(id);
  return node ? node.title : `item ${id}`;
}
