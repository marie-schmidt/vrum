// The one way a page keeps its "how to read this" out of the way: a small Key button that opens a
// hover card with the marks and what they mean, on a pointer's rest, on focus and on a tap. The
// Roadmap, the Graph and the Board all use it, so a key looks and works the same everywhere.
//
//   keyButton(content, { id })   content is anything `h` takes as children; returns the element to
//                                put in the page (the button, with its card right after it).

import { h } from './dom.js';
import { hoverCard } from './hovercard.js';
import { icon } from './icons.js';

export function keyButton(content, { id = null, label = 'Key' } = {}) {
  const trigger = h('button', { type: 'button', id, class: 'small key-button' }, icon('info', { size: 14 }), label);
  return h('span', { class: 'key-holder' }, trigger, hoverCard(trigger, content, { size: 'wide' }));
}
