// The Links section of an epic, story or task: every link it has, in both directions, as one table grouped by
// what the link means (needs first, needed by, relates to, duplicates, supersedes, discovered from, led to), with how the other item is
// doing. It is read from the board's own copy of the links and the work, which the clock keeps current, so it
// follows what agents do. A person who can write has Remove on each row and "Add link…" in the heading, which
// opens a small dialog; one who can only look sees the table, and nothing at all when there is no link.

import { Refusal, call } from './api.js';
import { can, itemLink, notify } from './ctx.js';
import { h, moment, put } from './dom.js';
import { opens } from './entries.js';
import { choicesOf, chooseOption, dialogForm, field, findChoice, openDialog, suggestOnAction } from './forms.js';
import { icon } from './icons.js';
import { LINK, actorLabel, byRef, codeOf, dueChip, kindIcon, readNodeRef } from './labels.js';
import { rememberFocus } from './records.js';
import { priorityChip, statusChip } from './status-menu.js';
import { nodeOf, store, sync, titleOf } from './store.js';

// The groups in the order they are read, with the words each is called. A link that reads the same from both ends
// (relates to, duplicates) is one group.
const GROUPS = [
  { key: 'needs_first:out', words: 'Needs first' },
  { key: 'needs_first:in', words: 'Needed by' },
  { key: 'relates_to', words: 'Relates to' },
  { key: 'duplicates', words: 'Duplicates' },
  { key: 'supersedes:out', words: 'Supersedes' },
  { key: 'supersedes:in', words: 'Superseded by' },
  { key: 'discovered_from:out', words: 'Discovered from' },
  { key: 'discovered_from:in', words: 'Led to' },
];
const ORDER = Object.fromEntries(GROUPS.map((group, at) => [group.key, at]));
const SYMMETRIC = new Set(['relates_to', 'duplicates']);

// What the add dialog offers: the type and which way round it reads from this item.
const RELATIONS = [
  ['needs_first:out', LINK.needs_first.label],
  ['needs_first:in', LINK.needs_first.back],
  ['supersedes:out', LINK.supersedes.label],
  ['supersedes:in', LINK.supersedes.back],
  ['duplicates:out', LINK.duplicates.label],
  ['relates_to:out', LINK.relates_to.label],
  ['discovered_from:out', LINK.discovered_from.label],
  ['discovered_from:in', LINK.discovered_from.back],
];

// What each link of the item says: { link, key, words, other, ref }, grouped and in order.
function linksOf(node) {
  const found = [];
  for (const link of store.links) {
    if (link.source !== node.ref && link.target !== node.ref) continue;
    const out = link.source === node.ref;
    const key = SYMMETRIC.has(link.link_type) ? link.link_type : `${link.link_type}:${out ? 'out' : 'in'}`;
    const ref = out ? link.target : link.source;
    found.push({ link, key, words: GROUPS[ORDER[key]].words, ref, other: nodeOf(ref) });
  }
  return found.sort((a, b) => ORDER[a.key] - ORDER[b.key] || byRef(a.ref, b.ref));
}

// How a link reads from this item, for a screen reader: "needs first “Write a log line for every staff change”".
const phrase = (item, node) => `${item.link.source === node.ref ? LINK[item.link.link_type].label : LINK[item.link.link_type].back} “${titleOf(item.ref)}”`;

async function removeLink(node, item) {
  try {
    await call('remove_link', { source: item.link.source, target: item.link.target, link_type: item.link.link_type });
    notify('Removed the link.');
    await sync();
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
  }
}

// The other item as a person typed it: its code and name as the list offers them, its code (S-12) or the whole of its
// title. 'several' when more than one has that title.
function readOther(text, node) {
  const found = findChoice(text, choicesOf(['node'], { except: node.ref }));
  if (found) return found === 'several' ? found : found.id;
  return readNodeRef(text.trim());
}

function addDialog(node) {
  const relation = h('select', { id: 'link-relation', name: 'relation' }, chooseOption('Choose how it is linked'), RELATIONS.map(([value, words]) => h('option', { value }, words)));
  const options = choicesOf(['node'], { except: node.ref }).map((other) => h('option', { value: other.label }));
  const target = h('input', { id: 'link-target', name: 'item', type: 'text', autocomplete: 'off' });
  suggestOnAction(target, 'link-options');
  const { form } = dialogForm({
    fields: [field('How it is linked', relation), field('The other item', target, 'A code such as S-12, or a title.'), h('datalist', { id: 'link-options' }, options)],
    submit: 'Add the link',
    work: async () => {
      if (!relation.value) throw new Refusal('Choose how it is linked.');
      const other = readOther(target.value, node);
      if (other === 'several') throw new Refusal('More than one item has that title. Type its code (such as S-12) or pick it from the list.');
      if (!other) throw new Refusal('Choose the other item: type its code (such as S-12) or pick it from the list.');
      const [type, direction] = relation.value.split(':');
      const ends = direction === 'in' ? [other, node.ref] : [node.ref, other];
      await call('add_link', { source: ends[0], target: ends[1], link_type: type });
      box.close();
      notify('Added the link.');
      await sync();
    },
  });
  const box = openDialog(`Add a link to “${node.title}”`, [form]);
  target.focus();
}

const quiet = (text) => h('span', { class: 'quiet' }, text);

// The section, to be put in the page where it belongs, and `draw(node)`, which draws it from the store.
export function linksSection() {
  const heading = h('h2', { id: 'item-links-title' }, 'Links');
  const count = h('span', { class: 'item-count', id: 'links-count' });
  const add = h('span', { class: 'item-add' });
  const region = h('div', { class: 'table-scroll item-region', id: 'links-region', role: 'region', tabindex: 0, 'aria-labelledby': 'item-links-title' });
  const element = h(
    'section',
    { class: 'item-children item-links', id: 'item-links', 'aria-labelledby': 'item-links-title', dataset: { part: 'links' } },
    h('header', { class: 'item-children-head' }, heading, count, add),
    region,
  );
  let signature = null;
  let shownNode = null;

  function row(item, node, first) {
    const { other, ref, link } = item;
    const writes = can('write');
    return h(
      'tr',
      { dataset: { type: link.link_type, ref, direction: link.source === node.ref ? 'out' : 'in' }, title: `Added ${moment(link.created_at)}`, onclick: opens(ref) },
      h('th', { scope: 'row', class: 'cell-relation' }, first ? [LINK[link.link_type].icon ? icon(LINK[link.link_type].icon, { size: 13, className: 'relation-icon' }) : null, item.words] : h('span', { class: 'sr-only' }, item.words)),
      h('td', { class: 'cell-code' }, itemLink(ref, { class: 'item-code-link', 'data-focus-key': `link:${ref}` }, kindIcon(other?.kind || ref.split(':')[0], 14), h('span', { class: 'item-code' }, codeOf(ref)))),
      h('td', { class: 'cell-title' }, itemLink(ref, { class: 'record-title', tabindex: -1 }, other ? other.title : titleOf(ref))),
      h('td', { class: 'cell-status' }, other ? statusChip(other, { onChanged: redraw }) : null),
      h('td', { class: 'cell-priority' }, other && other.priority !== 'normal' ? priorityChip(other, { onChanged: redraw, compact: true }) : null),
      h('td', { class: 'cell-held' }, other?.claimed_by ? actorLabel(other.claimed_by, other.claimed_by_kind) : null),
      h('td', { class: 'cell-due' }, other ? dueChip(other) : null),
      writes
        ? h(
            'td',
            { class: 'cell-remove' },
            h('button', { type: 'button', class: 'small remove-link', 'aria-label': `Remove the link: ${phrase(item, node)}`, 'data-focus-key': `remove:${link.id}`, onclick: () => removeLink(node, item) }, 'Remove'),
          )
        : null,
    );
  }

  function table(node, items) {
    const columns = [['relation', 'Relation'], ['code', 'Item'], ['title', 'Title'], ['status', 'Status'], ['priority', 'Priority'], ['held', 'Held by'], ['due', 'Due']];
    let last = null;
    return h(
      'table',
      { class: 'item-table links-table', id: 'links-table', 'aria-describedby': 'links-count' },
      h('caption', { class: 'sr-only' }, `Links of ${codeOf(node.ref)} ${node.title}`),
      h('thead', {}, h('tr', {}, [...columns.map(([key, label]) => h('th', { scope: 'col', class: `col-${key}` }, h('span', { class: 'col-label' }, label))), can('write') ? h('th', { scope: 'col', class: 'col-remove' }, h('span', { class: 'sr-only' }, 'Remove')) : null])),
      h(
        'tbody',
        {},
        items.map((item) => {
          const first = item.key !== last;
          last = item.key;
          return row(item, node, first);
        }),
      ),
    );
  }

  // Drawn again only when what it shows has changed.
  function draw(node) {
    shownNode = node;
    const items = linksOf(node);
    const writes = can('write');
    const next = JSON.stringify([node.ref, writes, items.map(({ link, other }) => [link.id, other && [other.version, other.title, other.status, other.priority, other.claimed_by, other.claimed_by_kind, other.target_on, other.overdue]])]);
    if (next === signature) return;
    signature = next;
    element.hidden = !items.length && !writes;
    const restore = rememberFocus(element);
    put(count, items.length ? String(items.length) : null);
    put(add, writes ? h('button', { type: 'button', id: 'add-link', class: 'small', 'data-focus-key': 'add-link', onclick: () => addDialog(shownNode) }, icon('plus', { size: 14 }), 'Add link…') : null);
    region.hidden = !items.length;
    put(region, items.length ? table(node, items) : null);
    restore();
  }

  function redraw() {
    signature = null;
    if (shownNode) draw(nodeOf(shownNode.ref) || shownNode);
  }

  return { element, heading, draw };
}
