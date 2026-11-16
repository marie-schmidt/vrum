// What the Graph's context list sections share: the frame of a section (heading, count, buttons
// and filters), the way a record's connections are written out, and the text filter.

import { h } from './dom.js';
import { TYPES, neighboursOf, relationWords } from './graph-data.js';
import { openRecord } from './records.js';

let counter = 0;
const uid = (prefix) => `${prefix}-${(counter += 1)}`;

// One part of the context list: a heading with a count and its buttons, a row of filters that act
// in the browser over the whole list the board holds, and the place the rows go. `controls` is a
// list of [name, label, element]; the element is built by the caller so it keeps its own value.
export function sectionFrame({ name, title, buttons = [], controls = [], onFilter }) {
  const count = h('span', { class: 'gv-count', id: `ctx-${name}-count`, role: 'status' });
  const body = h('div', { class: 'gv-body', id: `ctx-${name}-body` });
  const filters = h(
    'form',
    { class: 'gv-filters', role: 'search', 'aria-label': `Filter the ${title.toLowerCase()}`, onsubmit: (event) => event.preventDefault() },
    controls.map(([field, label, control]) => {
      control.id = `ctx-${name}-${field}`;
      control.addEventListener(control.tagName === 'INPUT' ? 'input' : 'change', onFilter);
      return h('div', { class: 'gv-field' }, h('label', { for: control.id }, label), control);
    }),
  );
  const element = h(
    'section',
    { class: 'gv-section', id: `ctx-${name}`, 'aria-labelledby': `ctx-${name}-heading`, dataset: { section: name } },
    h('header', { class: 'gv-section-head' }, h('h2', { id: `ctx-${name}-heading` }, title), count, h('span', { class: 'spacer' }), ...buttons),
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
// already says another way.
export function connectionsDetails(dot, ctx, { skip = [] } = {}) {
  const near = neighboursOf(dot.key, ctx.all.edges, ctx.all.dots).filter((entry) => !skip.includes(entry.edge.type));
  if (!near.length) return null;
  const chosen = ctx.cfg.sel?.key === dot.key;
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
          { dataset: { key: entry.dot.key, relation: entry.edge.type } },
          h('strong', {}, relationWords(entry)),
          ' ',
          h(
            'button',
            { type: 'button', class: 'link gv-link', id: uid('conn'), onclick: () => openRecord(entry.dot.kind, entry.dot.id) },
            `${TYPES[entry.dot.type].label} #${entry.dot.id} ${entry.dot.title}`,
          ),
        ),
      ),
    ),
  );
}

// Moves between the rows of a list with the arrow keys, from the one button of each row that the
// list offers as its way in (the "Open" button), so a long list is not a long run of Tab stops.
export function walkRows(list) {
  list.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    const here = event.target.closest?.('.gv-open');
    if (!here) return;
    const rows = [...list.querySelectorAll('.gv-open')];
    const next = rows[rows.indexOf(here) + (event.key === 'ArrowDown' ? 1 : -1)];
    if (!next) return;
    event.preventDefault();
    next.focus();
  });
}
