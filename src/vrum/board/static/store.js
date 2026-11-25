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
// heard: the path, the tab's title and the navigator follow at once. Secondary indexes for epics
// and children are built lazily and cleared on any write so lookups stay O(1).
class Nodes extends Map {
  constructor(nodes) {
    super();
    this._epics = null;
    this._children = null;
    for (const node of nodes) super.set(node.ref, node);
  }

  _buildIndexes() {
    const epicList = [];
    const children = new Map();
    for (const node of this.values()) {
      if (node.kind === 'epic') epicList.push(node);
      if (node.parent) {
        let list = children.get(node.parent);
        if (!list) {
          list = [];
          children.set(node.parent, list);
        }
        list.push(node);
      }
    }
    epicList.sort((a, b) => a.number - b.number);
    this._epics = epicList;
    this._children = children;
  }

  allEpics() {
    if (this._epics === null) this._buildIndexes();
    return this._epics;
  }

  childrenOf(ref) {
    if (this._children === null) this._buildIndexes();
    return this._children.get(ref) || [];
  }

  set(ref, node) {
    const before = this.get(ref);
    super.set(ref, node);
    if (before !== node) {
      this._epics = null;
      this._children = null;
      emit({ node: ref });
    }
    return this;
  }
}

export const store = {
  nodes: new Nodes([]), // ref (story:12) -> node
  links: [], // { id, source, target, link_type }
  rules: null, // loaded when a page asks for them
  rulesById: new Map(),
  notes: null,
  notesById: new Map(),
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
const pending = new Map(); // deduplicates concurrent ensure(name) calls

function storeCollection(name, items) {
  store[name] = items;
  if (name === 'rules') store.rulesById = new Map(items.map((one) => [one.id, one]));
  else if (name === 'notes') store.notesById = new Map(items.map((one) => [one.id, one]));
}

// Load what a page needs beyond the work itself, and keep it fresh from then on.
export async function ensure(name) {
  if (store[name] !== null) return;
  let running = pending.get(name);
  if (!running) {
    running = LOADERS[name]()
      .then((items) => {
        storeCollection(name, items);
      })
      .finally(() => {
        pending.delete(name);
      });
    pending.set(name, running);
  }
  await running;
}

// The acceptance criteria and the notes, for what names them (the quick switcher, the feed, a reference in
// a text).
export const ensureContext = () => Promise.all([ensure('rules'), ensure('notes')]);

const NON_NODE_ACTIONS = new Set(['plan_template_saved', 'plan_template_deleted', 'placement_proposed', 'placement_rejected']);
const REF_ACTIONS = new Set(['create', 'update', 'rule_created', 'rule_updated', 'note_recorded']);
const affectsNodes = (event) => !event.action.startsWith('note_') && !NON_NODE_ACTIONS.has(event.action);
const affectsLinks = (event) => event.action === 'link_added' || event.action === 'link_removed';
const affectsRules = (event) => event.action.startsWith('rule_');
const affectsNotes = (event) => event.action.startsWith('note_');
const affectsRefs = (event) => REF_ACTIONS.has(event.action);

async function reload(events = null) {
  const all = events === null;
  const tasks = [];
  if (all || events.some(affectsNodes)) {
    tasks.push(
      callAll('list_nodes', {}).then((nodes) => {
        store.nodes = new Nodes(nodes);
      }),
    );
  }
  if (all || events.some(affectsLinks)) {
    tasks.push(
      exported('links').then((links) => {
        store.links = links;
      }),
    );
  }
  const checks = { rules: affectsRules, notes: affectsNotes, references: affectsRefs };
  for (const name of Object.keys(LOADERS)) {
    if (store[name] !== null && (all || events.some(checks[name]))) {
      tasks.push(
        LOADERS[name]().then((items) => {
          storeCollection(name, items);
        }),
      );
    }
  }
  if (tasks.length) await Promise.all(tasks);
}

function applyIndex(index) {
  const changed =
    !store.index ||
    store.index.waiting_to_index !== index.waiting_to_index ||
    store.index.meaning !== index.meaning;
  store.index = index;
  if (changed) emit({ index });
}

async function refreshIndex() {
  applyIndex(await indexState());
}

let idleIndexTicks = 0;
// When search by meaning is healthy and nothing is waiting or changed, re-check the index every
// second poll (10s) rather than hashing index_source on every 5s tick.
const INDEX_IDLE_EVERY = 2;

export async function start() {
  pending.clear();
  idleIndexTicks = 0;
  const [info, head, nodes, links, index] = await Promise.all([
    get('/board/api/info'),
    call('changes_since', { after_event_id: 0, limit: 1 }),
    callAll('list_nodes', {}),
    exported('links'),
    indexState(),
  ]);
  store.info = info;
  store.cursor = head.latest_event_id;
  store.nodes = new Nodes(nodes);
  store.links = links;
  applyIndex(index);
}

// Ask what changed since the last look. When something did, load only the collections affected and
// say so. Used by the timer, and straight after the board's own change so that it shows at once.
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
  const indexBusy = !store.index || store.index.waiting_to_index > 0 || store.index.meaning !== 'used';
  const checkIndex = events.length > 0 || indexBusy || ++idleIndexTicks >= INDEX_IDLE_EVERY;
  if (checkIndex) idleIndexTicks = 0;
  if (events.length) {
    await Promise.all([reload(events), refreshIndex()]);
    emit({ events });
  } else if (checkIndex) {
    await refreshIndex();
  }
  return events;
}

let timer = null;
let waking = null;
let syncing = false;

// How soon to ask again after a check-in failed. One failure is usually a tab that was asleep or a slow moment, so
// the board asks again at once and says it cannot reach the server only when it keeps failing (onTrouble is told
// how many in a row).
const RETRY_MS = 3000;

export function keepSynced(onTrouble, onWell = () => {}) {
  stopSyncing();
  syncing = true;
  let failures = 0;
  const schedule = (wait) => {
    clearTimeout(timer);
    timer = null;
    if (!syncing || document.visibilityState === 'hidden') return;
    timer = setTimeout(tick, wait);
  };
  const tick = async () => {
    timer = null;
    if (!syncing || document.visibilityState === 'hidden') return;
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
    schedule(wait);
  };
  schedule(store.info.poll_seconds * 1000);
  // Pause polling while hidden and check immediately when the tab returns to the front.
  waking = () => {
    if (!syncing) return;
    if (document.visibilityState === 'hidden') {
      clearTimeout(timer);
      timer = null;
    } else if (document.visibilityState === 'visible') {
      schedule(0);
    }
  };
  document.addEventListener('visibilitychange', waking);
}

export function stopSyncing() {
  syncing = false;
  if (timer !== null) clearTimeout(timer);
  timer = null;
  if (waking !== null) document.removeEventListener('visibilitychange', waking);
  waking = null;
}

// -- reading the work ---------------------------------------------------------------------------

// Everything is named by its reference (story:12). Epics, then stories, then tasks, each by number,
// is the order the server gives and the order everything here keeps.
export const nodeOf = (ref) => store.nodes.get(ref);
export const epics = () => [...store.nodes.allEpics()];
export const childrenOf = (ref) => [...store.nodes.childrenOf(ref)];

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
  const record = kind === 'rule' ? store.rulesById.get(number) : store.notesById.get(number);
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
