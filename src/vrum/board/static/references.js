// References between items: `[[node:12]]`, `[[rule:3]]`, `[[decision:7]]` and `[[fact:5]]` written
// in any text. A reference is only text. Here it is shown as a link with the item's title as it is
// now (so it follows a rename), or plainly as missing, and "Mentioned in" lists the items that
// refer to one item, as `find_mentions` says. Nothing is kept for them. A link goes to the item in
// the shared detail panel, on whatever page the person is on (`sel=kind:id`).

import { callPaged } from './api.js';
import { recordLink, ui } from './ctx.js';
import { h } from './dom.js';
import { KIND, STATUS } from './labels.js';
import { ensure, store, titleOf } from './store.js';

// The same pattern as the server's (src/vrum/references.py); a test keeps the two equal.
export const REFERENCE_PATTERN = String.raw`\[\[(node|rule|decision|fact):([1-9][0-9]{0,17})\]\]`;

export const refText = (kind, id) => `[[${kind}:${id}]]`;

// What each kind of record is called and signed on the board. A rule is a "Requirement" to a person.
const RECORD_WORDS = {
  rule: { label: 'Requirement', sign: '▤' },
  decision: { label: 'Decision', sign: '⚑' },
  fact: { label: 'Fact', sign: '◈' },
};
export const kindWords = (type) => RECORD_WORDS[type] || KIND[type] || { label: type, sign: '?' };

// How an item's status reads: a work item's, a requirement's, or a record's.
const OTHER_STATUS = {
  active: 'In force',
  superseded: 'Replaced',
  withdrawn: 'Withdrawn',
  held: 'Held',
  settled: 'Settled',
  dropped: 'Dropped',
};
export const statusWords = (status) => OTHER_STATUS[status] || STATUS[status]?.label || status;

// What a reference can name needs the requirements, decisions and facts, so that one to a
// requirement is not taken for missing just because the page has not asked for them yet. They are
// read only when something needs them (a text that names one, the pick-list, the quick switcher).
export function loadTargets() {
  return Promise.all([ensure('rules'), ensure('decisions'), ensure('facts')]);
}

// Reads them, if any of these texts names something that is not a node (nodes are always held).
export function needTargets(...texts) {
  const names = new RegExp(REFERENCE_PATTERN.replace('(node|rule|', '(rule|'));
  return texts.some((text) => names.test(String(text ?? ''))) ? loadTargets() : Promise.resolve();
}

const maps = new WeakMap();
function byId(list) {
  let found = maps.get(list);
  if (!found) {
    found = new Map(list.map((item) => [item.id, item]));
    maps.set(list, found);
  }
  return found;
}

const clip = (text, size = 60) => {
  const one = String(text).replace(/\s+/g, ' ').trim();
  return one.length > size ? `${one.slice(0, size - 1)}…` : one;
};

// What is at the other end of a reference now: whether it exists, what it is and what it is called.
export function resolve(kind, id) {
  const number = Number(id);
  if (kind === 'node') {
    const node = store.nodes.get(number);
    return node ? { exists: true, type: node.kind, title: node.title, status: node.status } : { exists: false };
  }
  const list = kind === 'rule' ? store.rules : kind === 'decision' ? store.decisions : store.facts;
  if (!list) return { exists: true, type: kind, title: null, unknown: true };
  const found = byId(list).get(number);
  if (!found) return { exists: false };
  return { exists: true, type: kind, title: kind === 'fact' ? clip(found.text) : found.title, status: found.status };
}

// Where a link to an item goes: the same page the person is on, with the item chosen in the panel.
export function hrefFor(kind, id) {
  const params = new URLSearchParams(ui.params);
  params.set('sel', `${kind}:${id}`);
  params.delete('panel');
  return `#/${ui.page}?${params.toString().replace(/%3A/gi, ':').replace(/%2C/gi, ',')}`;
}

// The kind an item shows as (epic, story, task, rule, decision or fact) is what find_mentions
// says; this gives the kind a reference to it is written with.
export const referenceKindOf = (type) => (['epic', 'story', 'task'].includes(type) ? 'node' : type);

// A link to an epic, story or task, written as its id and its title now. A plain click shows it in
// the detail panel with the rest of the address as it is at that moment.
export const itemLink = (id) => recordLink('node', id, { class: 'item-link', dataset: { id } }, `#${id} ${titleOf(id)}`);

// A link to any record, with words of the caller's choosing.
export const openLink = (kind, id, words) => recordLink(kind, id, { class: 'record-link', dataset: { record: `${kind}:${id}` } }, words);

function labelOf(kind, id, found) {
  const sign = kindWords(found.type).sign;
  if (found.title === null) return `${sign} ${kind} ${id}`.trim();
  if (kind === 'node') return `${sign} #${id} ${found.title}`.trim();
  return `${sign} ${kindWords(found.type).label} #${id}: ${found.title}`.trim();
}

// One reference as it is shown: a link with the current title, or plainly missing.
export function renderReference(kind, id) {
  const found = resolve(kind, id);
  const data = { kind, id: String(id) };
  if (!found.exists) {
    return h('span', { class: 'reference missing', dataset: data, title: `Nothing of that kind has the id ${id}` }, `${refText(kind, id)} (missing)`);
  }
  return recordLink(kind, id, { class: 'reference', dataset: data }, labelOf(kind, id, found));
}

// A title changed, or an item came or went: show every reference on the page as it is now.
export function refreshReferences(root = document) {
  for (const element of root.querySelectorAll('.reference')) {
    const fresh = renderReference(element.dataset.kind, element.dataset.id);
    if (fresh.className !== element.className || fresh.textContent !== element.textContent || fresh.getAttribute('href') !== element.getAttribute('href')) {
      element.replaceWith(fresh);
    }
  }
}

// -- who mentions what ---------------------------------------------------------------------------

const ARGUMENT = { node: 'node_id', rule: 'rule_id', decision: 'decision_id', fact: 'fact_id' };

// Every mention of one item, or, with no item, every reference written anywhere, as find_mentions
// gives them, page after page.
export async function fetchMentions(kind = null, id = null) {
  const { items } = await callPaged('find_mentions', kind ? { [ARGUMENT[kind]]: id } : {});
  return items;
}

const WHERE = { details: 'its details', text: 'its text', reason: 'its reason', source: 'its source' };

function sourceLink(source) {
  const kind = referenceKindOf(source.item_type);
  const label = `${kindWords(source.item_type).label} #${source.id}: ${clip(source.title, 70)}`;
  return recordLink(kind, source.id, { class: 'mention-link', dataset: { kind, id: String(source.id) } }, label);
}

export function mentionsList(items) {
  if (!items.length) return h('p', { class: 'quiet none' }, 'Nothing refers to it.');
  return h(
    'ul',
    { class: 'plain mentions' },
    items.map((mention) =>
      h(
        'li',
        { class: 'mention', dataset: { source: `${mention.source.item_type}:${mention.source.id}` } },
        sourceLink(mention.source),
        ' ',
        h('span', { class: 'quiet' }, `(${statusWords(mention.source.status)}) in ${mention.fields.map((field) => WHERE[field] || field).join(' and ')}`),
        h('div', { class: 'quiet snippet' }, `…${mention.snippet.trim()}…`),
      ),
    ),
  );
}

// "Mentioned in" for an item in a list: closed, and read from the server when it is first opened.
export function mentionedInDetails(kind, id) {
  const body = h('div', { class: 'mentions-body' });
  const summary = h('summary', {}, 'Mentioned in');
  const details = h('details', { class: 'mentioned-in', dataset: { kind, id: String(id) } }, summary, body);
  let loaded = false;
  details.addEventListener('toggle', async () => {
    if (!details.open || loaded) return;
    loaded = true;
    body.replaceChildren(h('p', { class: 'quiet' }, 'Looking…'));
    try {
      const items = await fetchMentions(kind, id);
      summary.textContent = `Mentioned in (${items.length})`;
      body.replaceChildren(mentionsList(items));
    } catch (error) {
      loaded = false;
      body.replaceChildren(h('p', { class: 'problem', role: 'alert' }, error.message));
    }
  });
  return details;
}
