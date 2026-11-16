// The header every settings page opens with: the page's title, one line saying what it is for, and
// what can be done there on the right. The Board and the Roadmap draw the same pattern for
// themselves (board.css styles the class).

import { h } from './dom.js';

export function pageHeader(title, sub, ...actions) {
  return h(
    'header',
    { class: 'page-header' },
    h('div', { class: 'page-title' }, h('h1', {}, title), sub ? h('p', { class: 'page-sub' }, sub) : null),
    actions.length ? h('div', { class: 'page-actions' }, actions) : null,
  );
}
