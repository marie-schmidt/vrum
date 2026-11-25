// References between items: `[[story:12]]`, `[[rule:3]]`, `[[note:7]]` and the like, written in
// any text. The kinds are epic, story, task, rule and note. A reference is only text.
// Here it is shown as a link with the item's title as it is now (so it follows a rename), or
// plainly as missing, and "Mentioned in" lists the items that refer to one item, as
// `find_mentions` says. Nothing is kept for them. A link goes to the page of the item it names,
// keeping the page the person is in (`from`).

import { callPaged } from './api.js';
import { itemLink } from './ctx.js';
import { h } from './dom.js';
import { icon } from './icons.js';
import { KIND, isNodeKind, itemCode, kindIcon, nodeRef, parseItemRef, statusLabel } from './labels.js';
import { plainText, useReferences } from './markdown.js';
import { ensure, headlineOf, store, titleOf } from './store.js';

// The same pattern as the server's (src/vrum/references.py); a test keeps the two equal.
export const REFERENCE_PATTERN = String.raw`\[\[(epic|story|task|rule|note):([1-9][0-9]{0,17})\]\]`;

export const refText = (kind, number) => `[[${kind}:${number}]]`;

// The code a person reads for any of the five kinds: E-1, S-4 and T-20 for work, AC-3 for an acceptance criterion and
// N-7 for a note.
export const codeFor = itemCode;

// A code and a name as one line of words: "S-4 Audit log of staff actions".
const withCode = (kind, number, name) => [codeFor(kind, number), name].filter(Boolean).join(' ');

// What each kind of item is called on the board. A rule is an "Acceptance criterion" to a person.
export const kindWords = (type) => KIND[type] || { label: type };

// What a reference can name needs the acceptance criteria and the notes, so that one to an acceptance
// criterion is not taken for missing just because the page has not asked for them yet. They are read only when
// something needs them (a text that names one, the pick-list, the quick switcher).
export function loadTargets() {
  return Promise.all([ensure('rules'), ensure('notes')]);
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

// The words of a title or a statement on one line, as plain text: a note's first line may be Markdown.
const clip = (text, size = 60) => {
  const one = plainText(text);
  return one.length > size ? `${one.slice(0, size - 1)}…` : one;
};

// What is at the other end of a reference now: whether it exists, what it is and what it is called.
// `kind` is epic, story, task, rule or note, and `number` the number the text gives.
export function resolve(kind, number) {
  const id = Number(number);
  if (isNodeKind(kind)) {
    const node = store.nodes.get(nodeRef(kind, id));
    return node ? { exists: true, type: node.kind, title: node.title, status: node.status } : { exists: false };
  }
  const list = kind === 'rule' ? store.rules : store.notes;
  if (!list) return { exists: true, type: kind, title: null, unknown: true };
  const found = byId(list).get(id);
  if (!found) return { exists: false };
  return { exists: true, type: kind, title: kind === 'note' ? clip(headlineOf(found)) : found.title, status: found.status, record: found };
}

// A text with each reference in it (`[[story:12]]`) replaced by what it names, as plain words, for a place that can only
// hold text, such as a tooltip: the code and the name in quotes. One that names nothing is "a missing story".
export function namesInText(text) {
  return String(text ?? '').replace(new RegExp(REFERENCE_PATTERN, 'g'), (_all, kind, number) => {
    const found = resolve(kind, number);
    if (!found.exists) return `a missing ${kindWords(kind).label.toLowerCase()}`;
    return withCode(kind, number, found.title === null ? '' : `“${found.title}”`);
  });
}

// A link to an epic, story or task, written as its code and its title now (S-12 Title), to its page.
export function nodeLink(ref) {
  const parsed = parseItemRef(ref);
  return itemLink(ref, { class: 'item-link', dataset: { ref } }, withCode(parsed.kind, parsed.number, titleOf(ref)));
}

// A link to the page of any record, with words of the caller's choosing and, if wished, the icon
// of its kind.
export const openLink = (kind, number, words, mark = null) => itemLink(`${kind}:${number}`, { class: 'record-link', dataset: { record: `${kind}:${number}` } }, mark, words);

// An acceptance criterion or a note that has not been read yet is its code for the moment; the references on the page are
// drawn again once it has.
function labelOf(kind, number, found) {
  const mark = kindIcon(found.type, 14);
  if (found.title === null) return [mark, codeFor(kind, number)];
  return [mark, withCode(kind, number, found.title)];
}

// The hooks a reference carries: its kind and number, its reference (story:12), and for an
// acceptance criterion or a note its id.
function hooks(kind, number) {
  return isNodeKind(kind) ? { kind, number: String(number), ref: nodeRef(kind, number) } : { kind, number: String(number), id: String(number), ref: `${kind}:${number}` };
}

// One reference as it is shown: a link with the current title, or plainly missing.
export function renderReference(kind, number) {
  const found = resolve(kind, number);
  const data = hooks(kind, number);
  if (!found.exists) {
    return h('span', { class: 'reference missing', dataset: data, title: `There is no ${codeFor(kind, number)}` }, icon('alert', { size: 14 }), `${refText(kind, number)} (missing)`);
  }
  return itemLink(`${kind}:${number}`, { class: 'reference', dataset: data }, labelOf(kind, number, found));
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

const ARGUMENT = { rule: 'rule_id', note: 'note_id' };

// Every mention of one item, or, with no item, every reference written anywhere, as find_mentions
// gives them, page after page. `kind` is the real kind (story, rule, ...) and `number` its number.
export async function fetchMentions(kind = null, number = null) {
  const target = kind === null ? {} : isNodeKind(kind) ? { node: nodeRef(kind, number) } : { [ARGUMENT[kind]]: number };
  const { items } = await callPaged('find_mentions', target);
  return items;
}

const WHERE = { details: 'its details', text: 'its text', why: 'its why' };

function sourceLink(source) {
  const { kind, number } = parseItemRef(source.ref);
  return itemLink(`${kind}:${number}`, { class: 'mention-link', title: withCode(kind, number, source.title), dataset: hooks(kind, number) }, kindIcon(kind, 14), withCode(kind, number, clip(source.title, 70)));
}

// The words of a snippet as they read, with Markdown marks gone and references as the items they name. A
// snippet is a cut of the text, so a reference cut in half at either end has nothing to name and is left out.
const snippetWords = (snippet) => plainText(String(snippet).replace(/\[\[[^\]]*$/, '').replace(/^[^[]*\]\]/, ''));

export function mentionsList(items) {
  if (!items.length) return h('p', { class: 'quiet none' }, 'None yet.');
  return h(
    'ul',
    { class: 'plain mentions' },
    items.map((mention) =>
      h(
        'li',
        { class: 'mention', dataset: { source: mention.source.ref } },
        sourceLink(mention.source),
        ' ',
        h('span', { class: 'quiet' }, `(${statusLabel(mention.source.status)}) in ${mention.fields.map((field) => WHERE[field] || field).join(' and ')}`),
        h('div', { class: 'quiet snippet' }, `…${snippetWords(mention.snippet)}…`),
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
    body.replaceChildren(h('p', { class: 'quiet' }, 'Loading…'));
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

// Register reference rendering with markdown.js without a circular import.
useReferences(REFERENCE_PATTERN, renderReference);
