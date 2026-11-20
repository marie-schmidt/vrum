// The header every settings page opens with: the page's title, and what can be done there on the
// right. The Board and the Roadmap draw the same pattern for themselves (board.css styles the class).
// A title has no sentence under it: the page says what it is for by what it shows.

import { h } from './dom.js';

export function pageHeader(title, ...actions) {
  return h(
    'header',
    { class: 'page-header' },
    h('div', { class: 'page-title' }, h('h1', {}, title)),
    actions.length ? h('div', { class: 'page-actions' }, actions) : null,
  );
}
