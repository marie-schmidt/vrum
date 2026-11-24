// The notice of what a decision left undone (needs-views.js `said`), drawn the same on the page of what needs a person and on the review page:
// the server's own words, kept until the person dismisses it.

import { h, put } from './dom.js';
import { icon } from './icons.js';
import { forgetSaid, said } from './needs-views.js';

// Draws the notice in `box`, or leaves the box empty when there is nothing to say.
export function drawSaid(box) {
  if (!box) return;
  if (!said.lines.length && !said.problems.length) {
    put(box);
    return;
  }
  put(
    box,
    h(
      'div',
      { class: 'notice needs-said', role: 'status' },
      icon('info', { size: 16 }),
      h('div', { class: 'needs-said-text' }, said.lines.map((line) => h('p', { dataset: { words: 'theirs' } }, line)), said.problems.map((line) => h('p', { class: 'problem', role: 'alert' }, line))),
      h(
        'button',
        {
          type: 'button',
          class: 'ghost icon-only small notice-close',
          onclick: () => {
            forgetSaid();
            drawSaid(box);
          },
        },
        icon('close', { size: 14 }),
        h('span', { class: 'sr-only' }, 'Dismiss'),
      ),
    ),
  );
}
