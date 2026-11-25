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
  lists: [line(8, 6, 21, 6), line(8, 12, 21, 12), line(8, 18, 21, 18), circle(3.5, 6, 1, { fill: 'currentColor' }), circle(3.5, 12, 1, { fill: 'currentColor' }), circle(3.5, 18, 1, { fill: 'currentColor' })],
  activity: [path('M22 12h-4l-3 9L9 3l-3 9H2')],
  // What waits for a person: a person with a mark beside them. An ask is a question mark in a circle.
  needs: [circle(9, 7.5, 3.5), path('M2.5 20.5a6.5 6.5 0 0 1 10-5.5'), circle(18, 17, 4), line(18, 15.2, 18, 17.4), line(18, 19, 18, 19.1)],
  ask: [circle(12, 12, 9), path('M9.4 9.4a2.7 2.7 0 1 1 3.8 2.5c-.8.4-1.2 1-1.2 1.8'), line(12, 17, 12, 17.1)],
  // Work held for something outside the graph (a gate: two rails with bars across), a plan waiting to be approved (a clipboard),
  // a proposal to move work (arrows both ways), a plan template (a layout) and a link that says where work was found (a bent arrow).
  gate: [line(5, 3, 5, 21), line(19, 3, 19, 21), line(5, 8, 19, 8), line(5, 14, 19, 14)],
  plan: [rect(5, 4, 14, 17, 2), path('M9 4h6v3H9z'), line(9, 12, 15, 12), line(9, 16, 13, 16)],
  move: [line(12, 2, 12, 22), line(2, 12, 22, 12), path('M9 5l3-3 3 3'), path('M9 19l3 3 3-3'), path('M5 9l-3 3 3 3'), path('M19 9l3 3-3 3')],
  template: [rect(3, 3, 18, 18, 2), line(3, 9, 21, 9), line(9, 9, 9, 21)],
  discovered: [path('M4 4v6a4 4 0 0 0 4 4h10'), path('M14 10l4 4-4 4')],
  // A change to review (a square with a plus above a minus), an item modified in place (a solid dot) and what a reviewer marks as seen (an eye).
  diff: [rect(3, 3, 18, 18, 2), line(12, 6.5, 12, 12.5), line(9, 9.5, 15, 9.5), line(9, 16.5, 15, 16.5)],
  modified: [circle(12, 12, 4.5, { fill: 'currentColor' })],
  viewed: [path('M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z'), circle(12, 12, 3)],
  // A comment on a line of a plan: a speech bubble.
  comment: [path('M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z')],
  settings: [line(4, 6, 14, 6), line(18, 6, 20, 6), circle(16, 6, 2), line(4, 12, 6, 12), line(10, 12, 20, 12), circle(8, 12, 2), line(4, 18, 14, 18), line(18, 18, 20, 18), circle(16, 18, 2)],
  search: [circle(11, 11, 7), line(21, 21, 16.65, 16.65)],
  menu: [line(4, 6, 20, 6), line(4, 12, 20, 12), line(4, 18, 20, 18)],
  filter: [line(3, 6, 21, 6), line(6, 12, 18, 12), line(10, 18, 14, 18)],

  // What things are. The epic is a stack of layers; each kind has its own outline.
  epic: [path('M12 2 2 7l10 5 10-5z'), path('M2 17l10 5 10-5'), path('M2 12l10 5 10-5')],
  story: [rect(5, 3, 14, 18), line(9, 8, 15, 8), line(9, 12, 15, 12), line(9, 16, 12.5, 16)],
  task: [rect(4, 4, 16, 16, 3), path('M8.5 12.2l2.4 2.4 4.6-5')],
  rule: [path('M9 3h6v3H9z'), rect(5, 5, 14, 16), path('M9 14l2 2 4-4')],
  flag: [path('M5 21V4'), path('M5 4h12l-2.5 4 2.5 4H5')],
  diamond: [path('M12 3l9 9-9 9-9-9z'), circle(12, 12, 1.2, { fill: 'currentColor' })],

  // Where work stands. Each has a different outline, not only a different colour.
  new: [circle(12, 12, 9)],
  ready: [circle(12, 12, 9), circle(12, 12, 3, { fill: 'currentColor' })],
  in_progress: [circle(12, 12, 9), path('M12 3a9 9 0 0 1 0 18z', { fill: 'currentColor' })],
  blocked: [circle(12, 12, 9), line(5.6, 5.6, 18.4, 18.4)],
  done: [circle(12, 12, 9), path('M8 12.4l2.8 2.8L16.4 9.2')],
  dropped: [circle(12, 12, 9), line(8, 12, 16, 12)],

  // Where an acceptance criterion stands: a box instead of a circle, so that it is not taken for a note or for work.
  // Open is an empty box, met a ticked one, dropped a box with a bar through it.
  req_open: [rect(4, 5, 16, 16, 3)],
  req_met: [rect(4, 5, 16, 16, 3), path('M8 12.5l3 3L21 4.5')],
  req_dropped: [rect(4, 5, 16, 16, 3), line(8.5, 13, 15.5, 13)],
  // The definition of done: a list of ticked lines.
  checklist: [path('M3.5 6.5l1.8 1.8 3.2-3.6'), line(12, 6.5, 21, 6.5), path('M3.5 16.5l1.8 1.8 3.2-3.6'), line(12, 16.5, 21, 16.5)],
  // How big something is: a ruler.
  size: [rect(2, 7, 20, 10, 2), line(6.5, 7, 6.5, 11), line(10.5, 7, 10.5, 13), line(14.5, 7, 14.5, 11), line(18.5, 7, 18.5, 13)],

  // Priority.
  low: [path('M12 5v14'), path('M19 12l-7 7-7-7')],
  normal: [line(5, 12, 19, 12)],
  high: [path('M12 19V5'), path('M5 12l7-7 7 7')],
  urgent: [path('M17 11l-5-5-5 5'), path('M17 18l-5-5-5 5')],

  // Marks.
  plus: [line(12, 5, 12, 19), line(5, 12, 19, 12)],
  close: [line(18, 6, 6, 18), line(6, 6, 18, 18)],
  // A round x: wipes what was typed or chosen (Clear). Start again is a turn back.
  clear: [circle(12, 12, 9), line(15, 9, 9, 15), line(9, 9, 15, 15)],
  reset: [path('M1 4v6h6'), path('M3.51 15a9 9 0 1 0 2.13-9.36L1 10')],
  check: [path('M20 6L9 17l-5-5')],
  chevron_down: [path('M6 9l6 6 6-6')],
  chevron_left: [path('M15 6l-6 6 6 6')],
  chevron_right: [path('M9 6l6 6-6 6')],
  arrow_right: [line(5, 12, 19, 12), path('M13 6l6 6-6 6')],
  logout: [path('M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4'), path('M16 17l5-5-5-5'), line(21, 12, 9, 12)],
  link: [path('M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71'), path('M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71')],
  lock: [rect(5, 11, 14, 10), path('M8 11V7a4 4 0 0 1 8 0v4')],
  // A note left for whoever takes the work next: a page with a folded corner and some lines.
  handoff: [path('M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z'), path('M14 3v5h5'), line(9, 13, 15, 13), line(9, 17, 13, 17)],
  // Who did it: a person, or an agent (a small robot head), which differ in outline as well as in name.
  person: [circle(12, 8, 4), path('M4 21a8 8 0 0 1 16 0')],
  bot: [rect(4, 9, 16, 11, 3), line(12, 9, 12, 5.5), circle(12, 4, 1.2, { fill: 'currentColor' }), circle(9, 14, 1.1, { fill: 'currentColor' }), circle(15, 14, 1.1, { fill: 'currentColor' }), line(9.5, 17.2, 14.5, 17.2)],
  key: [circle(8, 15, 4), line(11, 12, 21, 2), line(17, 6, 20, 9)],
  mail: [rect(3, 5, 18, 14, 2), path('M3.5 7.5l8.5 6 8.5-6')],
  calendar: [rect(3, 5, 18, 16), line(16, 3, 16, 7), line(8, 3, 8, 7), line(3, 11, 21, 11)],
  clock: [circle(12, 12, 9), path('M12 7v5l3 2')],
  alert: [circle(12, 12, 9), line(12, 8, 12, 13), line(12, 16.5, 12, 16.6)],
  info: [circle(12, 12, 9), line(12, 11, 12, 16.5), line(12, 7.6, 12, 7.7)],
  confirmed: [circle(12, 12, 9), path('M8 12.4l2.8 2.8L16.4 9.2')],
  unconfirmed: [circle(12, 12, 9, { 'stroke-dasharray': '3 3' })],
  // The other ways to sign in: an identity service (a shield) and a code host (a branch). A directory
  // is the `hier` tree. They are plain shapes, not any company's logo.
  shield: [path('M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z'), path('M9 12l2.2 2.2L15.4 10')],
  branch: [line(6, 3, 6, 15), circle(18, 6, 3), circle(6, 18, 3), path('M18 9a9 9 0 0 1-9 9')],
  // Where the work is (whereabouts.js): a commit is a dot on a line, a pull request is two ends joined with an arrow into one.
  commit: [circle(12, 12, 3.5), line(3, 12, 8.5, 12), line(15.5, 12, 21, 12)],
  pull_request: [circle(6, 5, 2.5), circle(6, 19, 2.5), circle(18, 19, 2.5), line(6, 7.5, 6, 16.5), path('M18 16.5V9a2 2 0 0 0-2-2h-4'), path('M14.5 4.5L12 7l2.5 2.5')],

  // Appearance.
  sun: [circle(12, 12, 4), line(12, 2, 12, 4), line(12, 20, 12, 22), line(4.9, 4.9, 6.3, 6.3), line(17.7, 17.7, 19.1, 19.1), line(2, 12, 4, 12), line(20, 12, 22, 12), line(4.9, 19.1, 6.3, 17.7), line(17.7, 6.3, 19.1, 4.9)],
  moon: [path('M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z')],
  monitor: [rect(2, 3, 20, 14), line(8, 21, 16, 21), line(12, 17, 12, 21)],

  // The Graph: its three ways of looking, and the small marks its views use.
  around: [circle(12, 12, 3), circle(12, 4, 1.6), circle(20, 12, 1.6), circle(12, 20, 1.6), circle(4, 12, 1.6)],
  structure: [circle(12, 12, 2.4), path('M12 9.6V4M14 13.4l4.6 3.2M10 13.4l-4.6 3.2'), circle(12, 3, 1.5), circle(19.5, 17.5, 1.5), circle(4.5, 17.5, 1.5)],
  order: [rect(3, 4, 7, 5, 1.5), rect(14, 15, 7, 5, 1.5), path('M10 6.5h2.5a2.5 2.5 0 0 1 2.5 2.5v6')],
  arrow_left: [line(19, 12, 5, 12), path('M11 6l-6 6 6 6')],
  chevron_up: [path('M18 15l-6-6-6 6')],
  agent: [path('M8 8l-4 4 4 4'), path('M16 8l4 4-4 4'), line(13.5, 6, 10.5, 18)],
  hier: [circle(12, 5, 2.2), circle(6, 19, 2.2), circle(18, 19, 2.2), path('M12 7.2v4.3M12 11.5H6v5.3M12 11.5h6v5.3')],

  // Needs a person, grouped by topic: work that is alike in meaning, a few dots drawn together.
  topic: [circle(7, 8, 3), circle(17, 8, 3), circle(12, 17, 3), line(9.6, 9.5, 10.4, 14.6), line(14.4, 9.5, 13.6, 14.6), line(10, 8, 14, 8)],
};

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
