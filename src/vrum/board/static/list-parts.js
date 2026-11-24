// What the Lists page's context list sections share: the frame of a section (the bar of filters, its
// count and buttons), the way a record's connections are written out, the text filter, and the one chip that
// says who holds a piece of work (the Board's cards and the work list draw it the same).

import { itemLink } from './ctx.js';
import { h, moment } from './dom.js';
import { icon } from './icons.js';
import { neighboursOf, relationWords } from './lists-data.js';
import { actorLabel, codeOf, dueChip, kindIcon } from './labels.js';
import { titleOf } from './store.js';

// "before T-4 Slot picker is due", for work planned to start before what it needs first is finished.
export const beforeDue = (refs) => `before ${refs.map((ref) => `${codeOf(ref)} ${titleOf(ref)}`).join(', ')} ${refs.length === 1 ? 'is' : 'are'} due`;

// Who holds an item, as one small chip: the holder's sign and name, and until when in its tooltip. A claim that has
// run out is the same chip with an alert sign, and says so in its tooltip. Null when nobody has held it.
export function heldChip(node) {
  if (node.claimed_by) {
    const words = `Held by ${node.claimed_by} until ${moment(node.claim_expires_at)}`;
    const who = actorLabel(node.claimed_by, node.claimed_by_kind);
    who.removeAttribute('title'); // the chip's own tooltip is the one to read
    // A group, not an image: the name inside opens the person's card, and an image hides what is inside it from a screen reader.
    return h('span', { class: 'chip held', role: 'group', title: words, 'aria-label': words, dataset: { heldBy: node.claimed_by } }, who);
  }
  if (node.lapsed_claim_by) {
    const words = `${node.lapsed_claim_by}’s claim ran out`;
    return h(
      'span',
      { class: 'chip warn held lapsed', role: 'img', title: words, 'aria-label': words, dataset: { heldBy: node.lapsed_claim_by } },
      icon('alert', { size: 13 }),
      h('span', { class: 'actor-name' }, node.lapsed_claim_by),
    );
  }
  return null;
}

// The due day of work that is still to do, as the one due chip (red when it is late). Finished and dropped work has no
// use for it: it sits in its own column or list, and its page has the day.
export const openDueChip = (node) => (node.status === 'done' || node.status === 'dropped' ? null : dueChip(node));

// One part of the context list: a bar with its filters (which act in the browser over the whole list the board
// holds), how many rows they keep and its buttons, and the place the rows go. The part's heading is for a screen
// reader: the kind switch above the list already says which list this is. `controls` is a list of
// [name, label, element]; the element is built by the caller so it keeps its own value.
export function sectionFrame({ name, title, buttons = [], controls = [], onFilter }) {
  const count = h('span', { class: 'ctx-count', id: `ctx-${name}-count`, role: 'status', hidden: true });
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
    h('h2', { class: 'sr-only', id: `ctx-${name}-heading` }, title),
    h('div', { class: 'ctx-bar' }, controls.length ? filters : null, count, ...buttons),
    body,
  );
  return {
    element,
    body,
    // "3 of 7 shown": how many rows the filters keep, out of how many the scope holds. Said only while they keep fewer.
    setCount(shown, total) {
      count.textContent = shown === total ? '' : `${shown} of ${total} shown`;
      count.hidden = shown === total;
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

// What a record is joined to, as words and links to the page of the other record. `skip` leaves out kinds of line the
// row already says another way. The other end is named by its code and title (S-12 Slot picker, AC-3 A rider
// can have only one open ride, N-7 and the headline of the note).
export function connectionsDetails(dot, ctx, { skip = [] } = {}) {
  const near = neighboursOf(dot.key, ctx.all.edges, ctx.all.dots).filter((entry) => !skip.includes(entry.edge.type));
  if (!near.length) return null;
  const chosen = ctx.cfg.sel?.key === dot.key;
  const nameOf = (other) => `${other.code} ${other.title}`;
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
          itemLink(entry.dot.key, { class: 'gv-link' }, kindIcon(entry.dot.type, 14), nameOf(entry.dot)),
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
