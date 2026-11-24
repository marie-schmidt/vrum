// The title of a change, as what it is drawn from (M52): a plan's own title, the work's title for everything about one item, and for a
// suggested move the sentence "Move S-58 and S-59 under a new epic, Visitors and tourists" with the codes of the items in it as links to
// those items (a normal item link). The change's code is not part of its title: it is in the kind chip. Words somebody wrote (a title) are
// marked so.

import { itemLink } from './ctx.js';
import { h } from './dom.js';
import { codeOf } from './labels.js';
import { store } from './store.js';

const code = (ref) => itemLink(ref, { class: 'item-link', dataset: { ref } }, codeOf(ref));

// The title as a list of nodes and text to put in an element. `theirs` wraps what a person or agent wrote in a marked span.
export function titleNodes(change) {
  const theirs = (words) => h('span', { class: 'change-title-text', dataset: { words: 'theirs' } }, words);
  if (change.look !== 'placement') return [theirs(change.title)];
  const move = change.item.placement;
  const refs = move.items.map((one) => one.ref);
  const named = refs.length > 3 ? refs.slice(0, 2) : refs;
  const parts = ['Move '];
  named.forEach((ref, at) => {
    if (at) parts.push(refs.length <= 3 && at === named.length - 1 ? ' and ' : ', ');
    parts.push(code(ref));
  });
  if (refs.length > 3) parts.push(` and ${refs.length - 2} more`);
  parts.push(' under ');
  if (move.parent) parts.push(code(move.parent), ' ', theirs(move.parent_title ?? store.nodes.get(move.parent)?.title ?? ''));
  else parts.push('a new epic, ', theirs(move.new_epic?.title ?? ''));
  return parts;
}
