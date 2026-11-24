// What the board knows: the work (nodes and the links between them), and, once a page asks for
// them, the acceptance criteria (rules) and the notes. It is a copy of what the server holds,
// fetched with the same tools an agent uses and refreshed from the same change feed. The server is
// the only place anything is kept.

import { call, callAll, callPaged, exported, get, indexState, useNames } from './api.js';
import { isNodeKind, noteCode, nodeCode, ruleCode } from './labels.js';

const listeners = new Set();

// fn({ events }) is called when the server's data changed, fn({ index }) when the index did, and
// fn({ node: 'story:12' }) when a page puts a newer copy of one item into the store itself (after a
// save, say) without waiting for the next poll.
export function onChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit(change) {
  listeners.forEach((fn) => fn(change));
}

// The work, by reference. A page that writes an item into it (`store.nodes.set(ref, node)`) is
// heard: the path, the tab's title and the navigator follow at once.
class Nodes extends Map {
  constructor(nodes) {
    super();
    for (const node of nodes) super.set(node.ref, node);
  }

  set(ref, node) {
    const before = this.get(ref);
    super.set(ref, node);
    if (before !== node) emit({ node: ref });
    return this;
  }
}

export const store = {
  nodes: new Nodes([]), // ref (story:12) -> node
  links: [], // { id, source, target, link_type }
  rules: null, // loaded when a page asks for them
  notes: null,
  references: null, // every [[reference]] written in any text, loaded when the graph asks
  info: { poll_seconds: 5 },
  index: null, // what the index can do now, and how many items are waiting
  cursor: 0, // the newest change event seen
};

const LOADERS = {
  rules: () => callAll('list_rules', { detail: true }),
  notes: () => callAll('list_notes', {}),
  references: async () => (await callPaged('find_mentions', {})).items,
};

// Load what a page needs beyond the work itself, and keep it fresh from then on.
export async function ensure(name) {
  if (store[name] === null) store[name] = await LOADERS[name]();
}

// The acceptance criteria and the notes, for what names them (the quick switcher, the feed, a reference in
// a text).
export const ensureContext = () => Promise.all([ensure('rules'), ensure('notes')]);

async function reload() {
  const [nodes, links] = await Promise.all([callAll('list_nodes', {}), exported('links')]);
  store.nodes = new Nodes(nodes);
  store.links = links;
  for (const name of Object.keys(LOADERS)) {
    if (store[name] !== null) store[name] = await LOADERS[name]();
  }
}

// The work as it is now. A claim runs out with no change event, so a page that shows claims asks
// again when one is due to, instead of waiting for news that never comes.
export async function refreshWork() {
  const nodes = await callAll('list_nodes', {});
  store.nodes = new Nodes(nodes);
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
let waking = null;

// How soon to ask again after a check-in failed. One failure is usually a tab that was asleep or a slow moment, so
// the board asks again at once and says it cannot reach the server only when it keeps failing (onTrouble is told
// how many in a row).
const RETRY_MS = 3000;

export function keepSynced(onTrouble, onWell = () => {}) {
  stopSyncing();
  let failures = 0;
  const tick = async () => {
    let wait = store.info.poll_seconds * 1000;
    try {
      await sync();
      failures = 0;
      onWell();
    } catch (error) {
      failures += 1;
      onTrouble(error, failures);
      wait = Math.min(wait, RETRY_MS);
    }
    clearTimeout(timer);
    timer = setTimeout(tick, wait);
  };
  timer = setTimeout(tick, store.info.poll_seconds * 1000);
  // A tab that comes back to the front checks at once, instead of showing a note about the time it was away.
  waking = () => {
    if (document.visibilityState === 'visible' && timer !== null) {
      clearTimeout(timer);
      timer = setTimeout(tick, 0);
    }
  };
  document.addEventListener('visibilitychange', waking);
}

export function stopSyncing() {
  if (timer !== null) clearTimeout(timer);
  timer = null;
  if (waking !== null) document.removeEventListener('visibilitychange', waking);
  waking = null;
}

// -- reading the work ---------------------------------------------------------------------------

// Everything is named by its reference (story:12). Epics, then stories, then tasks, each by number,
// is the order the server gives and the order everything here keeps.
export const nodeOf = (ref) => store.nodes.get(ref);
export const epics = () => [...store.nodes.values()].filter((node) => node.kind === 'epic').sort((a, b) => a.number - b.number);
export const childrenOf = (ref) => [...store.nodes.values()].filter((node) => node.parent === ref);

export function epicOf(node) {
  let current = node;
  while (current && current.kind !== 'epic') current = store.nodes.get(current.parent);
  return current || null;
}

export function titleOf(ref) {
  const node = store.nodes.get(ref);
  return node ? node.title : 'not found';
}

const oneLine = (text, size = 80) => {
  const line = String(text).replace(/\s+/g, ' ').trim();
  return line.length > size ? `${line.slice(0, size - 1)}…` : line;
};

// What a note is called in a list or a path: the server's headline (the first line of its text), or,
// for one that has none, its text on one line.
export const headlineOf = (note) => note.headline || oneLine(note.text);

// What follows the first line of a note's text, on one line: empty when the text is only that line.
export function restOf(note) {
  const lines = String(note.text ?? '').split('\n');
  const first = lines.findIndex((line) => line.trim() !== '');
  return first < 0 ? '' : lines.slice(first + 1).join(' ').replace(/\s+/g, ' ').trim();
}

// What the path, the browser's tab and a refusal call any of the five kinds of record: { kind, number, name,
// title, parent }. `name` is the code written before the title ("S-12", "AC-3", "N-7"); a note's title is its
// headline (the first line of its text). `parent` is the story or task an acceptance criterion is on (the only
// kind that has one besides the work itself). Null when the record is not known (not found, or the acceptance
// criteria and notes are not read yet).
export function describeItem(ref) {
  const found = /^(epic|story|task|rule|note):([1-9][0-9]*)$/.exec(String(ref ?? ''));
  if (!found) return null;
  const kind = found[1];
  const number = Number(found[2]);
  if (isNodeKind(kind)) {
    const node = store.nodes.get(ref);
    return node ? { kind, number, name: nodeCode(kind, number), title: node.title, parent: node.parent || null } : null;
  }
  const record = (store[{ rule: 'rules', note: 'notes' }[kind]] || []).find((one) => one.id === number);
  if (!record) return null;
  return {
    kind,
    number,
    name: kind === 'rule' ? ruleCode(number) : noteCode(number),
    title: kind === 'note' ? headlineOf(record) : record.title,
    parent: kind === 'rule' ? record.node || null : null,
  };
}

// What a sentence that names a record by its reference should say instead (see api.js namesIn).
useNames(describeItem);
