// The board's icons: one set of SVG outlines on a 24 by 24 grid, drawn with the same stroke so they
// look alike at every size, and coloured by the text around them (currentColor). They are built
// with the same helper as the rest of the page, so nothing is parsed as markup. An icon is
// decoration unless it is given a label: the words next to it say the same thing.

import { s } from './dom.js';

const line = (x1, y1, x2, y2) => ['line', { x1, y1, x2, y2 }];
const circle = (cx, cy, r, extra = {}) => ['circle', { cx, cy, r, ...extra }];
const path = (d, extra = {}) => ['path', { d, ...extra }];
const rect = (x, y, width, height, rx = 2) => ['rect', { x, y, width, height, rx }];

export const ICONS = {
  // Where things are.
  board: [rect(3, 3, 18, 18), line(9, 3, 9, 21), line(15, 3, 15, 21)],
  graph: [circle(18, 5, 3), circle(6, 12, 3), circle(18, 19, 3), line(8.6, 13.5, 15.4, 17.5), line(15.4, 6.5, 8.6, 10.5)],
  roadmap: [rect(3, 4, 9, 4, 1), rect(8, 10, 9, 4, 1), rect(13, 16, 8, 4, 1)],
  activity: [path('M22 12h-4l-3 9L9 3l-3 9H2')],
  settings: [line(4, 6, 14, 6), line(18, 6, 20, 6), circle(16, 6, 2), line(4, 12, 6, 12), line(10, 12, 20, 12), circle(8, 12, 2), line(4, 18, 14, 18), line(18, 18, 20, 18), circle(16, 18, 2)],
  search: [circle(11, 11, 7), line(21, 21, 16.65, 16.65)],
  menu: [line(4, 6, 20, 6), line(4, 12, 20, 12), line(4, 18, 20, 18)],
  filter: [line(3, 6, 21, 6), line(6, 12, 18, 12), line(10, 18, 14, 18)],

  // What things are. The epic is a stack of layers; each kind has its own outline.
  epic: [path('M12 2 2 7l10 5 10-5z'), path('M2 17l10 5 10-5'), path('M2 12l10 5 10-5')],
  story: [rect(5, 3, 14, 18), line(9, 8, 15, 8), line(9, 12, 15, 12), line(9, 16, 12.5, 16)],
  task: [rect(4, 4, 16, 16, 3), path('M8.5 12.2l2.4 2.4 4.6-5')],
  rule: [path('M9 3h6v3H9z'), rect(5, 5, 14, 16), path('M9 14l2 2 4-4')],
  decision: [path('M5 21V4'), path('M5 4h12l-2.5 4 2.5 4H5')],
  fact: [path('M12 3l9 9-9 9-9-9z'), circle(12, 12, 1.2, { fill: 'currentColor' })],

  // Where work stands. Each has a different outline, not only a different colour.
  new: [circle(12, 12, 9)],
  ready: [circle(12, 12, 9), circle(12, 12, 3, { fill: 'currentColor' })],
  in_progress: [circle(12, 12, 9), path('M12 3a9 9 0 0 1 0 18z', { fill: 'currentColor' })],
  blocked: [circle(12, 12, 9), line(5.6, 5.6, 18.4, 18.4)],
  done: [circle(12, 12, 9), path('M8 12.4l2.8 2.8L16.4 9.2')],
  dropped: [circle(12, 12, 9), line(8, 12, 16, 12)],

  // Priority.
  low: [path('M12 5v14'), path('M19 12l-7 7-7-7')],
  normal: [line(5, 12, 19, 12)],
  high: [path('M12 19V5'), path('M5 12l7-7 7 7')],
  urgent: [path('M17 11l-5-5-5 5'), path('M17 18l-5-5-5 5')],

  // Marks.
  plus: [line(12, 5, 12, 19), line(5, 12, 19, 12)],
  close: [line(18, 6, 6, 18), line(6, 6, 18, 18)],
  check: [path('M20 6L9 17l-5-5')],
  chevron_down: [path('M6 9l6 6 6-6')],
  chevron_right: [path('M9 6l6 6-6 6')],
  arrow_right: [line(5, 12, 19, 12), path('M13 6l6 6-6 6')],
  link: [path('M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71'), path('M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71')],
  lock: [rect(5, 11, 14, 10), path('M8 11V7a4 4 0 0 1 8 0v4')],
  user: [circle(12, 8, 4), path('M4 21a8 8 0 0 1 16 0')],
  key: [circle(8, 15, 4), line(11, 12, 21, 2), line(17, 6, 20, 9)],
  calendar: [rect(3, 5, 18, 16), line(16, 3, 16, 7), line(8, 3, 8, 7), line(3, 11, 21, 11)],
  clock: [circle(12, 12, 9), path('M12 7v5l3 2')],
  alert: [circle(12, 12, 9), line(12, 8, 12, 13), line(12, 16.5, 12, 16.6)],
  info: [circle(12, 12, 9), line(12, 11, 12, 16.5), line(12, 7.6, 12, 7.7)],
  confirmed: [circle(12, 12, 9), path('M8 12.4l2.8 2.8L16.4 9.2')],
  unconfirmed: [circle(12, 12, 9, { 'stroke-dasharray': '3 3' })],

  // Appearance.
  sun: [circle(12, 12, 4), line(12, 2, 12, 4), line(12, 20, 12, 22), line(4.9, 4.9, 6.3, 6.3), line(17.7, 17.7, 19.1, 19.1), line(2, 12, 4, 12), line(20, 12, 22, 12), line(4.9, 19.1, 6.3, 17.7), line(17.7, 6.3, 19.1, 4.9)],
  moon: [path('M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z')],
  monitor: [rect(2, 3, 20, 14), line(8, 21, 16, 21), line(12, 17, 12, 21)],
};

export const hasIcon = (name) => Object.hasOwn(ICONS, name);

// An icon as an element. `size` is in pixels. Without a `label` it is hidden from screen readers.
export function icon(name, { size = 16, label = null, className = '' } = {}) {
  const parts = ICONS[name] || ICONS.info;
  const props = {
    class: `icon icon-${name}${className ? ` ${className}` : ''}`,
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    'stroke-width': '1.75',
    'stroke-linecap': 'round',
    'stroke-linejoin': 'round',
    focusable: 'false',
  };
  if (label) {
    props.role = 'img';
    props['aria-label'] = label;
  } else {
    props['aria-hidden'] = 'true';
  }
  return s('svg', props, parts.map(([tag, attrs]) => s(tag, attrs)));
}
