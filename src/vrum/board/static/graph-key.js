// The key of a Graph picture: what its marks mean, folded behind the Key button that sits beside the
// view's controls (key-button.js: a hover card that opens on a pointer's rest, on focus and on a tap),
// so the picture is the first thing on the page at every width.
//
// Used by Structure (the whole project and one epic), Around and Order. Nothing here reads the store: a
// view says what its picture draws and the key says no more than that. The marks of the whole project's
// map are drawn with the map's own classes, so a key swatch and the circle it explains cannot differ.

import { h } from './dom.js';
import { keyButton } from './key-button.js';
import { STATUS, STATUSES, statusIcon } from './labels.js';
import { s } from './graph-shared.js';

let counter = 0;

// One chip of the key: a mark and what it means. `mark` is an element or text; `className` lets the
// style sheet colour the mark (for the status marks, graph-x-key-<status>).
export const keyChip = (mark, words, { className = '', title = null } = {}) =>
  h('li', { class: `graph-key-chip${className ? ` ${className}` : ''}`, title }, mark, h('span', {}, words));

// The six statuses, each with the same sign the board draws for it.
export const statusChips = (statuses = STATUSES) => statuses.map((status) => keyChip(statusIcon(status, 14), STATUS[status].label, { className: `graph-x-key-${status}` }));

// A small picture for a mark of the whole project's map, drawn with the map's own classes: one shape
// for each of `shapes` ({ kind: 'epic' | 'story' | 'task', status, x, r, arc }).
function swatch(width, shapes) {
  const svg = s('svg', { class: 'graph-key-swatch', width, height: 22, viewBox: `0 0 ${width} 22`, 'aria-hidden': 'true', focusable: 'false' });
  for (const { kind, status = null, x, r, arc = null } of shapes) {
    const g = s('g', { class: `graph-st-node is-${kind}${status ? ` s-${status}` : ''}`, transform: `translate(${x} 11)` });
    if (arc !== null) {
      const ring = r + 3.6;
      const C = Math.PI * 2 * ring;
      g.append(s('circle', { class: 'graph-st-track', r: ring }), s('circle', { class: 'graph-st-fill', r: ring, 'stroke-dasharray': `${Math.round(C * arc * 10) / 10} ${Math.round(C * 10) / 10}`, transform: 'rotate(-90)' }));
    }
    g.append(s('circle', { class: 'graph-st-body', r }));
    svg.append(g);
  }
  return svg;
}

// The shapes of the map: an epic with its arc, a story, a task.
export const shapeChips = ({ stories = true, tasks = true } = {}) =>
  [
    keyChip(swatch(24, [{ kind: 'epic', x: 12, r: 5, arc: 0.6 }]), 'Epic, with a green arc for how much is done'),
    stories ? keyChip(swatch(18, [{ kind: 'story', x: 9, r: 4.5 }]), 'Story') : null,
    tasks ? keyChip(swatch(14, [{ kind: 'task', x: 7, r: 3.4 }]), 'Task') : null,
  ].filter(Boolean);

// A status as the whole map draws it, a ring for an epic or a story and a dot for a task, with how many
// of the finest level drawn are in it. `counts` is { status: number }; a status with none is left out,
// because the map has no mark of that colour.
export const mapStatusChips = (counts, finest = 'tasks') =>
  STATUSES.filter((status) => counts[status]).map((status) =>
    keyChip(
      swatch(34, [
        { kind: 'epic', status, x: 8, r: 5 },
        { kind: 'task', status, x: 26, r: 3.6 },
      ]),
      [STATUS[status].label, ' ', h('span', { class: 'graph-key-count' }, counts[status])],
      { title: `${counts[status]} ${counts[status] === 1 ? finest.replace(/s$/, '') : finest} ${STATUS[status].label.toLowerCase()}` },
    ),
  );

// A red dot where work is overdue (or a dashed ring, as the whole map draws it); a pair of dots,
// the second bigger.
export const lateChip = (words = 'Overdue', shape = 'dot') => keyChip(h('span', { class: `graph-x-late${shape === 'ring' ? ' is-ring' : ''}` }), words);
export const biggerChip = (words) => keyChip(h('span', { class: 'graph-x-bigger' }), words);

// A ring that is part full, for how much of something is done.
export function ringChip(words) {
  const svg = s('svg', { class: 'graph-key-ring', width: 16, height: 16, viewBox: '0 0 16 16', 'aria-hidden': 'true' });
  svg.append(s('circle', { class: 'graph-key-ring-track', cx: 8, cy: 8, r: 6 }), s('circle', { class: 'graph-key-ring-fill', cx: 8, cy: 8, r: 6, 'stroke-dasharray': '17 38', transform: 'rotate(-90 8 8)' }));
  return keyChip(svg, words);
}

// A filled dot and a hollow one: started and not started, where the colour says something else.
export function startedChip() {
  const svg = s('svg', { class: 'graph-key-dots', width: 28, height: 12, viewBox: '0 0 28 12', 'aria-hidden': 'true' });
  svg.append(s('circle', { class: 'graph-key-dot is-full', cx: 6, cy: 6, r: 4 }), s('circle', { class: 'graph-key-dot', cx: 21, cy: 6, r: 4 }));
  return keyChip(svg, 'Filled: started. Hollow: not started');
}

// A short line of the kind a link is drawn with, with an arrowhead where the order matters.
function lineChip(type, words, title = null) {
  const arrow = type === 'needs_first' || type === 'supersedes';
  const svg = s('svg', { class: 'graph-x-sample', width: 40, height: 12, viewBox: '0 0 40 12', 'aria-hidden': 'true' });
  svg.append(s('line', { class: `graph-st-link t-${type} is-key`, x1: 2, y1: 6, x2: arrow ? 32 : 38, y2: 6 }));
  if (arrow) svg.append(s('path', { class: 'graph-st-arrow', d: 'M31 2.5 L39 6 L31 9.5 z' }));
  return keyChip(svg, words, { title });
}

// The kinds of link of the Structure picture.
const LINK_KEY = [
  ['needs_first', 'Needs first (the arrow shows the order)'],
  ['relates_to', 'Related'],
  ['duplicates', 'Duplicate'],
  ['supersedes', 'Supersedes'],
  ['discovered_from', 'Discovered from (where work was found; nothing waits for it)'],
  ['mention', 'Named in a text'],
];
export const linkChips = () => LINK_KEY.map(([type, words]) => lineChip(type, words));

// The key itself, as the Key button of the view. `groups` are lists of chips, each { label, chips }: the
// label heads the list.
export function viewKey({ groups, label = 'How to read this picture' }) {
  const body = h(
    'div',
    { class: 'graph-key-body', role: 'group', 'aria-label': label },
    groups
      .filter((group) => group && group.chips.length)
      .map((group) => {
        counter += 1;
        const id = `graph-key-title-${counter}`;
        return h('section', { class: 'graph-key-group' }, h('h3', { class: 'graph-key-title', id }, group.label), h('ul', { class: 'graph-key-chips plain', 'aria-labelledby': id }, group.chips));
      }),
  );
  return keyButton(body, { id: 'graph-key-button' });
}
