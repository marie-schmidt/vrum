// References between items: `[[story:12]]`, `[[rule:3]]`, `[[decision:7]]` and the like, written in
// any text. The kinds are epic, story, task, rule, decision and fact. A reference is only text.
// Here it is shown as a link with the item's title as it is now (so it follows a rename), or
// plainly as missing, and "Mentioned in" lists the items that refer to one item, as
// `find_mentions` says. Nothing is kept for them. A link goes to the item in the shared detail
// panel, on whatever page the person is on (`sel=kind:number`).

import { callPaged } from './api.js';
import { itemLink, recordLink, ui } from './ctx.js';
import { h } from './dom.js';
import { icon } from './icons.js';
import { KIND, STATUS, isNodeKind, itemCode, kindIcon, nodeRef, parseItemRef } from './labels.js';
import { ensure, store, titleOf } from './store.js';

// The same pattern as the server's (src/vrum/references.py); a test keeps the two equal.
export const REFERENCE_PATTERN = String.raw`\[\[(epic|story|task|rule|decision|fact):([1-9][0-9]{0,17})\]\]`;

export const refText = (kind, number) => `[[${kind}:${number}]]`;

// What each kind of item is called on the board. A rule is a "Requirement" to a person.
export const kindWords = (type) => KIND[type] || { label: type };

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

// Reads them, if any of these texts names something that is not held already (epics, stories and
// tasks always are).
export function needTargets(...texts) {
  const names = new RegExp(REFERENCE_PATTERN.replace('(epic|story|task|rule|', '(rule|'));
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
// `kind` is epic, story, task, rule, decision or fact, and `number` the number the text gives.
export function resolve(kind, number) {
  const id = Number(number);
  if (isNodeKind(kind)) {
    const node = store.nodes.get(nodeRef(kind, id));
    return node ? { exists: true, type: node.kind, title: node.title, status: node.status } : { exists: false };
  }
  const list = kind === 'rule' ? store.rules : kind === 'decision' ? store.decisions : store.facts;
  if (!list) return { exists: true, type: kind, title: null, unknown: true };
  const found = byId(list).get(id);
  if (!found) return { exists: false };
  return { exists: true, type: kind, title: kind === 'fact' ? clip(found.text) : found.title, status: found.status };
}

// Where a link to an item goes: the same page the person is on, with the item chosen in the panel.
export function hrefFor(kind, number) {
  const params = new URLSearchParams(ui.params);
  params.set('sel', `${kind}:${number}`);
  params.delete('panel');
  return `#/${ui.page}?${params.toString().replace(/%3A/gi, ':').replace(/%2C/gi, ',')}`;
}

// A link to an epic, story or task, written as its code and its title now (S-12 Title). A plain
// click shows it in the detail panel with the rest of the address as it is at that moment; with
// `page` it goes to the item's own page instead.
export function nodeLink(ref, { page = false } = {}) {
  const parsed = parseItemRef(ref);
  const words = `${itemCode(parsed.kind, parsed.number)} ${titleOf(ref)}`;
  const props = { class: 'item-link', dataset: { ref } };
  return page ? itemLink(ref, props, words) : recordLink(parsed.kind, parsed.number, props, words);
}

// A link to any record, with words of the caller's choosing and, if wished, the icon of its kind.
export const openLink = (kind, number, words, mark = null) => recordLink(kind, number, { class: 'record-link', dataset: { record: `${kind}:${number}` } }, mark, words);

function labelOf(kind, number, found) {
  const mark = kindIcon(found.type, 14);
  if (found.title === null) return [mark, `${kind} ${number}`];
  if (isNodeKind(kind)) return [mark, `${itemCode(kind, number)} ${found.title}`];
  return [mark, `${kindWords(found.type).label} #${number}: ${found.title}`];
}

// The hooks a reference carries: its kind and number, its reference (story:12), and for a
// requirement, decision or fact its id.
function hooks(kind, number) {
  return isNodeKind(kind) ? { kind, number: String(number), ref: nodeRef(kind, number) } : { kind, number: String(number), id: String(number), ref: `${kind}:${number}` };
}

// One reference as it is shown: a link with the current title, or plainly missing.
export function renderReference(kind, number) {
  const found = resolve(kind, number);
  const data = hooks(kind, number);
  if (!found.exists) {
    return h('span', { class: 'reference missing', dataset: data, title: `There is no ${isNodeKind(kind) ? `${kind} ${number}` : `${kindWords(kind).label.toLowerCase()} #${number}`}` }, icon('alert', { size: 14 }), `${refText(kind, number)} (missing)`);
  }
  return recordLink(kind, number, { class: 'reference', dataset: data }, labelOf(kind, number, found));
}

// A title changed, or an item came or went: show every reference on the page as it is now.
export function refreshReferences(root = document) {
  for (const element of root.querySelectorAll('.reference')) {
    const fresh = renderReference(element.dataset.kind, element.dataset.number);
    if (fresh.className !== element.className || fresh.textContent !== element.textContent || fresh.getAttribute('href') !== element.getAttribute('href')) {
      element.replaceWith(fresh);
    }
  }
}

// -- who mentions what ---------------------------------------------------------------------------

const ARGUMENT = { rule: 'rule_id', decision: 'decision_id', fact: 'fact_id' };

// Every mention of one item, or, with no item, every reference written anywhere, as find_mentions
// gives them, page after page. `kind` is the real kind (story, rule, ...) and `number` its number.
export async function fetchMentions(kind = null, number = null) {
  const target = kind === null ? {} : isNodeKind(kind) ? { node: nodeRef(kind, number) } : { [ARGUMENT[kind]]: number };
  const { items } = await callPaged('find_mentions', target);
  return items;
}

const WHERE = { details: 'its details', text: 'its text', reason: 'its reason', source: 'its source' };

function sourceLink(source) {
  const { kind, number } = parseItemRef(source.ref);
  const label = isNodeKind(kind) ? `${itemCode(kind, number)} ${clip(source.title, 70)}` : `${kindWords(kind).label} #${number}: ${clip(source.title, 70)}`;
  return recordLink(kind, number, { class: 'mention-link', dataset: hooks(kind, number) }, kindIcon(kind, 14), label);
}

export function mentionsList(items) {
  if (!items.length) return h('p', { class: 'quiet none' }, 'Nothing refers to it.');
  return h(
    'ul',
    { class: 'plain mentions' },
    items.map((mention) =>
      h(
        'li',
        { class: 'mention', dataset: { source: mention.source.ref } },
        sourceLink(mention.source),
        ' ',
        h('span', { class: 'quiet' }, `(${statusWords(mention.source.status)}) in ${mention.fields.map((field) => WHERE[field] || field).join(' and ')}`),
        h('div', { class: 'quiet snippet' }, `…${mention.snippet.trim()}…`),
      ),
    ),
  );
}

// "Mentioned in" for an item in a list: closed, and read from the server when it is first opened.
export function mentionedInDetails(kind, number) {
  const body = h('div', { class: 'mentions-body' });
  const summary = h('summary', {}, 'Mentioned in');
  const details = h('details', { class: 'mentioned-in', dataset: hooks(kind, number) }, summary, body);
  let loaded = false;
  details.addEventListener('toggle', async () => {
    if (!details.open || loaded) return;
    loaded = true;
    body.replaceChildren(h('p', { class: 'quiet' }, 'Looking…'));
    try {
      const items = await fetchMentions(kind, number);
      summary.textContent = `Mentioned in (${items.length})`;
      body.replaceChildren(mentionsList(items));
    } catch (error) {
      loaded = false;
      body.replaceChildren(h('p', { class: 'problem', role: 'alert' }, error.message));
    }
  });
  return details;
}
