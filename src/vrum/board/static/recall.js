// What recall says for one item or acceptance criterion, in the same sections and with the same reasons as
// the `recall` tool gives an agent: the notes in force (the decided ones first), the project-wide
// notes and the earlier ones that were replaced or withdrawn. The board asks the server the very same
// question, so it cannot disagree with it. Each section is a table across the whole width of the page,
// and every row shows whether a person confirmed the note and whether it is still in force.
//
// The page's one "Similar" section is here too: what is alike in meaning to the item and is not already
// on its page, for every kind of item.

import { Refusal, call } from './api.js';
import { itemLink } from './ctx.js';
import { h, put } from './dom.js';
import { recordTitle, recordsTable } from './entries.js';
import { icon } from './icons.js';
import { kindIcon, noteStatusWords, parseItemRef } from './labels.js';
import { codeFor } from './references.js';

const PAGE = 50;
const FEW = 5; // a section with fewer entries than this is open until the person closes it
const SHOWN_SIMILAR = 5; // the most alike items the page shows
const SIMILAR = 8; // the most alike items the page asks for, some of which it already shows elsewhere
const ENDED = new Set(['superseded', 'withdrawn', 'dropped']);

// The tables of a recall, in the order the server names them, and what each is called.
const TABLES = {
  notes: { title: 'Notes in force' },
  past_notes: { title: 'Earlier notes' },
  project_wide_notes: { title: 'Project-wide notes' },
};
const NAMES = Object.keys(TABLES);

// `target` is { node } (an epic, story or task by its reference) or { rule_id }.
export const askRecall = (target, extra = {}) => call('recall', { ...target, limit: PAGE, ...extra });

// What is alike in meaning to one item: `target` is { node }, { rule_id } or { note_id }. When the server cannot say
// (search by meaning is not set up) there is nothing to show, and no section.
export const askSimilar = async (target) => {
  try {
    return (await call('similar_items', { ...target, limit: SIMILAR })).items;
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    return [];
  }
};

// A note as a row: the note itself, with the reason recall gives for its being there (`why_here`,
// since a note has a `why` of its own) and where it came down from.
const asRow = (item) => ({ ...item.note, why_here: item.why, inherited_from: item.inherited_from || null });

// One section of the answer as a table, with the way to read the rest when there are more than a
// page. A row carries the reason recall gives for it (`why_here`).
function tableSection(name, entries, ask, { heading = true } = {}) {
  const { title } = TABLES[name];
  const found = (entries?.items || []).filter((item) => item.note).map(asRow);
  const count = entries?.count ?? 0;
  const table = recordsTable(found, { caption: `${title} ${count}` });
  const more = h('p', { class: 'recall-more' });
  let after = entries?.next_after_id ?? null;
  let have = found.length;
  const button = () =>
    put(
      more,
      after !== null && after !== undefined
        ? h(
            'button',
            {
              type: 'button',
              class: 'show-more small',
              onclick: async () => {
                const next = await ask({ show: name, after_id: after });
                table.add(next[name].items.filter((item) => item.note).map(asRow));
                have += next[name].items.length;
                after = next[name].next_after_id;
                button();
              },
            },
            `Show ${count - have} more`,
          )
        : null,
    );
  button();
  return h(
    'section',
    { class: 'recall-section', dataset: { section: name, count } },
    heading ? h('h4', {}, title, h('span', { class: 'heading-count' }, String(count))) : null,
    table.element,
    more,
  );
}

// What is alike in meaning to the item and is not on its page already: suggestions from the index, never records of
// the item. One section for every kind of page, drawn only when there is something to suggest; each row says how
// alike it is. `known` holds the references the page already shows (the item's parents and children, what it is
// linked to, the notes it has), which are not suggestions.
export function similarSection(found, known = new Set()) {
  const items = (found || []).filter((item) => !known.has(item.ref) && !ENDED.has(item.status)).slice(0, SHOWN_SIMILAR);
  if (!items.length) return null;
  return h(
    'section',
    { class: 'part', dataset: { part: 'similar', count: items.length } },
    h('h3', {}, 'Similar', h('span', { class: 'heading-count' }, String(items.length))),
    h(
      'ul',
      { class: 'plain similar-list' },
      items.map((item) => {
        const { kind, number } = parseItemRef(item.ref);
        return h(
          'li',
          { class: 'similar-item', dataset: { similar: item.ref } },
          kindIcon(kind, 14),
          itemLink(item.ref, { class: 'item-link', title: `${codeFor(kind, number)} ${item.title}` }, `${codeFor(kind, number)} ${item.title}`),
          h('span', { class: 'quiet' }, item.similarity === null || item.similarity === undefined ? 'by words' : `${item.similarity.toFixed(2)} alike`),
        );
      }),
    ),
  );
}

export const recallTotal = (data) => NAMES.reduce((sum, name) => sum + (data[name]?.count ?? 0), 0);

// The references of the notes a recall lists, with the others the page shows (`extra`): what Similar leaves out.
export function knownRefs(recalled, extra = []) {
  const known = new Set(extra.filter(Boolean));
  for (const name of NAMES) for (const item of recalled?.[name]?.items || []) if (item.note) known.add(`note:${item.note.id}`);
  return known;
}

// Everything a page shows for a recall, or nothing when it holds no note: the tables, and no switch or line
// saying there is none. Notes that are linked come first (the decided
// ones first), with the earlier ones in an "Earlier" section; the project-wide notes are in a
// section that is closed when something is linked here (they apply anyway) and open when
// nothing is, so that the page is not empty of what applies. A section with fewer than five entries
// is open to begin with, and a closed one shows the first line of the first three. `open` holds the
// sections the person opened or closed, and `onOpen(name, open)` says when they do.
export function recallBlock(target, data, { open = {}, onOpen = () => {} } = {}) {
  if (!recallTotal(data)) return null;
  const body = h('div', { class: 'recall-sections' });
  const ask = (more) => askRecall(target, more);
  const linked = data.notes?.count ?? 0;
  // A section that has nothing is not drawn at all. The notes in force have a heading of their own only when other groups
  // follow, since the page's heading already says how many there are.
  const others = ['past_notes', 'project_wide_notes'].some((name) => data[name]?.count);
  const table = (name) => (data[name]?.count ? tableSection(name, data[name], ask, { heading: others }) : null);
  // A section that is closed says what is in it: the first line of each of the first few notes.
  const preview = (name) => {
    const lines = (data[name].items || []).filter((item) => item.note).slice(0, 3);
    const rest = data[name].count - lines.length;
    return h(
      'span',
      { class: 'disclosure-lines' },
      lines.map((item) => {
        const line = `${name === 'past_notes' ? `${noteStatusWords(item.note.status)}: ` : ''}${recordTitle(item.note)}`;
        return h('span', { class: 'disclosure-line', title: line }, line);
      }),
      rest > 0 ? h('span', { class: 'disclosure-line quiet' }, `and ${rest} more`) : null,
    );
  };
  // Few entries are open to begin with: a closed section would hide what an agent is told.
  const closed = (name, byDefault) => {
    if (!data[name]?.count) return null;
    const { title } = TABLES[name];
    const disclosure = h(
      'details',
      { class: 'recall-disclosure', dataset: { disclosure: name }, open: name in open ? open[name] : byDefault || data[name].count < FEW },
      h('summary', {}, icon('chevron_right', { size: 14, className: 'disclosure-chevron' }), title, h('span', { class: 'heading-count' }, String(data[name].count)), preview(name)),
      tableSection(name, data[name], ask, { heading: false }),
    );
    // Setting `open` while drawing also fires a toggle: only a change of it is the person's choice.
    let known = disclosure.open;
    disclosure.addEventListener('toggle', () => {
      if (disclosure.open === known) return;
      known = disclosure.open;
      onOpen(name, known);
    });
    return disclosure;
  };
  put(body, [table('notes'), closed('past_notes', false), closed('project_wide_notes', linked === 0)].filter(Boolean));
  return h('div', { class: 'recall-block', dataset: { recall: Object.keys(target)[0] } }, body);
}
