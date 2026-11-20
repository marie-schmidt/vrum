// What recall says for one item or acceptance criterion, in the same sections and with the same reasons as
// the `recall` tool gives an agent: the notes in force (the decided ones first), the project-wide
// notes, the earlier ones that were replaced or withdrawn, and, when asked for, what is alike in
// meaning but not linked. The board asks the server the very same question, so it cannot disagree
// with it. Each section is a table across the whole width of the page, and every row shows whether a
// person confirmed the note and whether it is still in force.

import { call } from './api.js';
import { itemLink } from './ctx.js';
import { h, put } from './dom.js';
import { recordTitle, recordsTable } from './entries.js';
import { icon } from './icons.js';
import { isNodeKind, itemCode, kindIcon, noteStatusWords, parseItemRef } from './labels.js';
import { kindWords } from './references.js';

const PAGE = 50;
const FEW = 5; // a section with fewer entries than this is open until the person closes it

// The tables of a recall, in the order the server names them, and what each is called.
const TABLES = {
  notes: { title: 'Notes in force' },
  past_notes: { title: 'Earlier notes' },
  project_wide_notes: { title: 'Project-wide notes' },
};
const NAMES = Object.keys(TABLES);

// `target` is { node } (an epic, story or task by its reference) or { rule_id }.
export const askRecall = (target, extra = {}) => call('recall', { ...target, limit: PAGE, with_similar: true, ...extra });

// A note as a row: the note itself, with the reason recall gives for its being there (`why_here`,
// since a note has a `why` of its own) and where it came down from.
const asRow = (item) => ({ ...item.note, why_here: item.why, inherited_from: item.inherited_from || null });

// One section of the answer as a table, with the way to read the rest when there are more than a
// page. A row carries the reason recall gives for it (`why_here`).
function tableSection(name, entries, ask, { heading = true } = {}) {
  const { title } = TABLES[name];
  const found = (entries?.items || []).filter((item) => item.note).map(asRow);
  const count = entries?.count ?? 0;
  const table = recordsTable(found, { caption: `${title} (${count})` });
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
    heading ? h('h4', {}, title, h('span', { class: 'heading-count' }, ` (${count})`)) : null,
    table.element,
    more,
  );
}

// What is alike in meaning but not linked: suggestions from the index, never records of this item.
function similarSection(similar) {
  if (!similar) return null;
  const body = similar.items.length
    ? h(
        'ul',
        { class: 'plain similar-list' },
        similar.items.map((item) => {
          const { kind, number } = parseItemRef(item.ref);
          const name = isNodeKind(kind) ? `${itemCode(kind, number)} ${item.title}` : `${kindWords(kind).label} #${number}: ${item.title}`;
          return h(
            'li',
            { class: 'similar-item', dataset: { similar: item.ref } },
            kindIcon(kind, 14),
            itemLink(item.ref, { class: 'item-link' }, name),
            h('span', { class: 'quiet' }, `${item.similarity === null || item.similarity === undefined ? 'by words' : `${item.similarity.toFixed(2)} alike`}`),
          );
        }),
      )
    : h('p', { class: 'quiet none' }, similar.index?.meaning && similar.index.meaning !== 'used' ? similar.index.note : 'None found.');
  return h(
    'section',
    { class: 'recall-section', dataset: { section: 'similar', count: similar.count ?? similar.items.length } },
    h('h4', {}, 'Alike in meaning, not linked', h('span', { class: 'heading-count' }, ` (${similar.count ?? similar.items.length})`)),
    body,
  );
}

export const recallTotal = (data) => NAMES.reduce((sum, name) => sum + (data[name]?.count ?? 0), 0);

// Everything a page shows for a recall: a line saying how many entries there are, a switch for the
// entries that are alike in meaning, and the tables. Notes that are linked come first (the decided
// ones first), with the earlier ones in an "Earlier" section; the project-wide notes are in a
// section that is closed when something is linked here (they apply anyway) and open when
// nothing is, so that the page is not empty of what applies. A section with fewer than five entries
// is open to begin with, and a closed one shows the first line of the first three. The switch reads again by itself and
// never touches anything else on the page, so a draft typed there is safe. `open` holds the
// sections the person opened or closed, and `onOpen(name, open)` says when they do.
export function recallBlock(target, data, { open = {}, onOpen = () => {} } = {}) {
  const body = h('div', { class: 'recall-sections' });
  const draw = (answer) => {
    const ask = (more) => askRecall(target, more);
    const linked = answer.notes?.count ?? 0;
    // A section that has nothing is not drawn at all.
    const table = (name) => (answer[name]?.count ? tableSection(name, answer[name], ask) : null);
    // A section that is closed says what is in it: the first line of each of the first few notes.
    const preview = (name) => {
      const lines = (answer[name].items || []).filter((item) => item.note).slice(0, 3);
      const rest = answer[name].count - lines.length;
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
      if (!answer[name]?.count) return null;
      const { title } = TABLES[name];
      const disclosure = h(
        'details',
        { class: 'recall-disclosure', dataset: { disclosure: name }, open: name in open ? open[name] : byDefault || answer[name].count < FEW },
        h('summary', {}, icon('chevron_right', { size: 14, className: 'disclosure-chevron' }), title, h('span', { class: 'heading-count' }, ` (${answer[name].count})`), preview(name)),
        tableSection(name, answer[name], ask, { heading: false }),
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
    const parts = [
      table('notes'),
      closed('past_notes', false),
      closed('project_wide_notes', linked === 0),
      similarSection(answer.similar),
    ].filter(Boolean);
    put(body, parts.length ? parts : h('p', { class: 'quiet none' }, 'None yet.'));
  };
  draw(data);
  return h('div', { class: 'recall-block', dataset: { recall: Object.keys(target)[0] } }, body);
}
