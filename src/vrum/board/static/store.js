// What the board knows: the work (nodes and the links between them), and, once a page asks for
// them, the requirements (rules), decisions and facts. It is a copy of what the server holds,
// fetched with the same tools an agent uses and refreshed from the same change feed. The server is
// the only place anything is kept.

import { call, callAll, callPaged, exported, get, indexState } from './api.js';

export const store = {
  nodes: new Map(), // id -> node
  links: [], // { id, source_id, target_id, link_type }
  rules: null, // loaded when a page asks for them
  decisions: null,
  facts: null,
  references: null, // every [[reference]] written in any text, loaded when the graph asks
  info: { poll_seconds: 5 },
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

const LOADERS = {
  rules: () => callAll('list_rules', { detail: true }),
  decisions: () => callAll('list_decisions', {}),
  facts: () => callAll('list_facts', {}),
  references: async () => (await callPaged('find_mentions', {})).items,
};

// Load what a page needs beyond the work itself, and keep it fresh from then on.
export async function ensure(name) {
  if (store[name] === null) store[name] = await LOADERS[name]();
}

// The requirements, decisions and facts, for what names them (the quick switcher, the feed, a
// reference in a text).
export const ensureContext = () => Promise.all([ensure('rules'), ensure('decisions'), ensure('facts')]);

async function reload() {
  const [nodes, links] = await Promise.all([callAll('list_nodes', {}), exported('links')]);
  store.nodes = new Map(nodes.map((node) => [node.id, node]));
  store.links = links;
  for (const name of Object.keys(LOADERS)) {
    if (store[name] !== null) store[name] = await LOADERS[name]();
  }
}

// The work as it is now. A claim runs out with no change event, so a page that shows claims asks
// again when one is due to, instead of waiting for news that never comes.
export async function refreshWork() {
  const nodes = await callAll('list_nodes', {});
  store.nodes = new Map(nodes.map((node) => [node.id, node]));
}

async function refreshIndex() {
  const index = await indexState();
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

export function keepSynced(onTrouble, onWell = () => {}) {
  stopSyncing();
  const tick = async () => {
    try {
      await sync();
      onWell();
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
