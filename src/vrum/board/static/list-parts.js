// What the Graph's context list sections share: the frame of a section (heading, count, buttons
// and filters), the way a record's connections are written out, and the text filter.

import { h } from './dom.js';
import { neighboursOf, relationWords } from './graph-data.js';
import { KIND, isNodeKind, itemCode, kindIcon } from './labels.js';
import { openRecord } from './records.js';

// One part of the context list: a heading with a count and its buttons, a row of filters that act
// in the browser over the whole list the board holds, and the place the rows go. `controls` is a
// list of [name, label, element]; the element is built by the caller so it keeps its own value.
export function sectionFrame({ name, title, buttons = [], controls = [], onFilter }) {
  const count = h('span', { class: 'ctx-count', id: `ctx-${name}-count`, role: 'status' });
  const body = h('div', { class: 'ctx-body', id: `ctx-${name}-body` });
  const filters = h(
    'form',
    { class: 'ctx-filters', role: 'search', 'aria-label': `Filter the ${title.toLowerCase()}`, onsubmit: (event) => event.preventDefault() },
    controls.map(([field, label, control]) => {
      control.id = `ctx-${name}-${field}`;
      control.addEventListener(control.tagName === 'INPUT' ? 'input' : 'change', onFilter);
      return h('div', { class: 'ctx-field' }, h('label', { for: control.id }, label), control);
    }),
  );
  const element = h(
    'section',
    { class: 'ctx-section', id: `ctx-${name}`, 'aria-labelledby': `ctx-${name}-heading`, dataset: { section: name } },
    h('header', { class: 'ctx-head' }, h('h2', { id: `ctx-${name}-heading` }, title), count, h('span', { class: 'spacer' }), ...buttons),
    controls.length ? filters : null,
    body,
  );
  return {
    element,
    body,
    // "3 of 7": how many rows the filters keep, out of how many the scope holds.
    setCount(shown, total) {
      count.textContent = shown === total ? `${total}` : `${shown} of ${total}`;
      count.dataset.shown = String(shown);
      count.dataset.total = String(total);
    },
  };
}

export function selectOf(options, value) {
  return h('select', { value }, options.map(([name, label]) => h('option', { value: name }, label)));
}

export const textInput = (placeholder) => h('input', { type: 'search', autocomplete: 'off', placeholder, 'aria-label': placeholder });

// Whether any of these pieces of text holds every word of what was typed.
export function matchesText(typed, ...pieces) {
  const words = typed.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = pieces.filter((piece) => piece !== null && piece !== undefined).join(' ').toLowerCase();
  return words.every((word) => hay.includes(word));
}

// What a record is joined to, as words and buttons that show the other record: the same joins the
// map draws as lines, so the list says what the map shows. `skip` leaves out kinds of line the row
// already says another way. The other end is opened by its real kind and number: a node's record
// has a `number`, a requirement's, decision's and fact's an `id`.
export function connectionsDetails(dot, ctx, { skip = [] } = {}) {
  const near = neighboursOf(dot.key, ctx.all.edges, ctx.all.dots).filter((entry) => !skip.includes(entry.edge.type));
  if (!near.length) return null;
  const chosen = ctx.cfg.sel?.key === dot.key;
  const numberOf = (other) => other.record?.number ?? other.record?.id ?? other.id;
  const nameOf = (other) => (isNodeKind(other.type) ? `${itemCode(other.type, numberOf(other))} ${other.title}` : `${KIND[other.type].label} #${numberOf(other)} ${other.title}`);
  return h(
    'details',
    { class: 'gv-connections', open: chosen, dataset: { for: dot.key } },
    h('summary', {}, `Connections (${near.length})`),
    h(
      'ul',
      { class: 'plain' },
      near.map((entry) =>
        h(
          'li',
          { dataset: { other: entry.dot.key, relation: entry.edge.type } },
          h('strong', {}, relationWords(entry)),
          h(
            'button',
            { type: 'button', class: 'link gv-link', onclick: () => openRecord(entry.dot.type, numberOf(entry.dot)) },
            kindIcon(entry.dot.type, 14),
            nameOf(entry.dot),
          ),
        ),
      ),
    ),
  );
}

// Moves between the rows of a list with the arrow keys, from the one control of each row that is
// its way in (its title), so a long list is not a long run of Tab stops.
export function walkRows(list) {
  list.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    const here = event.target.closest?.('.gv-open, .record-title');
    if (!here) return;
    const rows = [...list.querySelectorAll('.gv-open, .record-title')];
    const next = rows[rows.indexOf(here) + (event.key === 'ArrowDown' ? 1 : -1)];
    if (!next) return;
    event.preventDefault();
    next.focus();
  });
}
