// The header every settings page opens with. The top bar already names the page, so the page's own title is
// there only for a screen reader and for landmarks: the `h1` is hidden (`sr-only`) and nothing visible repeats it.
// What is drawn is what can be done on the page, on the right; a page with nothing to do draws no header row at
// all, so its content starts at the top of the page. The Board, the Lists and the Roadmap do the same for themselves
// (board.css styles the class).

import { h } from './dom.js';

export function pageHeader(title, ...actions) {
  const heading = h('h1', { class: 'sr-only' }, title);
  if (!actions.length) return heading;
  return h('header', { class: 'page-header actions-only' }, heading, h('div', { class: 'page-actions' }, actions));
}
