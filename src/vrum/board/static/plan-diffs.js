// What a revision changed in a plan, as a diff (M51): the way to read a revised plan, since the person already read the revision before.
// Items the revision added are `+` lines as in the whole plan, items it took out are `-` lines as they were, and an item that is in
// both revisions but differs is a `~` line (the item as it is now) with, under it, each part that changed: a `-` line with what it was
// and a `+` line with what it is. Items that did not change are not drawn, except an item that something was said about (a comment
// from before, one not sent yet): it is drawn as a line that stays, with what was said under it, so that the person can see whether
// the agent answered it. What is left out is counted by a fold, and one press opens the whole plan.

import { day, h, plural } from './dom.js';
import { LINK, PRIORITY, SIZE_POINTS, SIZE_WORDS, byRef, codeOf, parseNodeRef, ruleCode } from './labels.js';
import { diffLine, foldOver, hunk, sides, textLines } from './diff.js';
import { changedSpec, itemSpec, opening } from './change-dialog.js';
import { childrenOf, link, planLines } from './needs-diffs.js';
import { FIELD_WORDS } from './plan-comments.js';
import { store } from './store.js';

// The parts of an item a revision can change, in the order the server lists them.
const PARTS = ['title', 'body', 'priority', 'size', 'start_on', 'target_on', 'parent', 'links', 'criteria'];

const quiet = (words) => h('span', { class: 'quiet' }, words);

// How a part is said as a value, for the line that says what it was and the one that says what it is. `lookup(ref)` finds the title of
// an item of the plan or of the revision before it.
function valueWords(part, value, lookup) {
  switch (part) {
    case 'priority':
      return PRIORITY[value]?.label ?? String(value);
    case 'size':
      return value ? `${SIZE_WORDS[value] ?? value}, ${SIZE_POINTS[value] ?? '?'} points` : quiet('no size');
    case 'start_on':
      return value ? day(value) : quiet('no start day');
    case 'target_on':
      return value ? day(value) : quiet('no due day');
    case 'parent': {
      if (!value) return quiet('nothing');
      const title = lookup(value);
      return `${codeOf(value)}${title ? ` ${title}` : ''}`;
    }
    case 'links':
      return value.length ? value.map(linkWords).join(', ') : quiet('no links');
    case 'criteria':
      return value.length ? value.map(criterionWords).join('; ') : quiet('no acceptance criteria');
    default:
      return value === null || value === undefined || value === '' ? quiet('none') : String(value);
  }
}

// "needs first T-5": a link as it is listed ("needs_first task:5").
function linkWords(text) {
  const [type, target] = String(text).split(' ');
  return `${LINK[type]?.label ?? type} ${codeOf(target)}`;
}

// "AC-3 Every bike passes the grip test: the whole of it, on one line".
function criterionWords([id, title, body]) {
  const said = String(body ?? '').replace(/\s+/g, ' ').trim();
  return `${ruleCode(id)} ${title}${said ? `: ${said}` : ''}`;
}

// The values of an item's parts as the server compares them: links as sorted words of "type ref", criteria as [id, title, body].
function nowValues(item) {
  return {
    title: item.title,
    body: item.body,
    priority: item.priority,
    size: item.size,
    start_on: item.start_on,
    target_on: item.target_on,
    parent: item.parent,
    links: item.links.map((one) => `${one.link_type} ${one.target}`).sort(),
    criteria: [...item.criteria].sort((a, b) => a.id - b.id).map((one) => [one.id, one.title, one.body]),
  };
}

// The lines of one changed part: what it was, then what it is. Details are lines of text, with the usual fold after six. Each line of a part
// opens the item as the revision changed it (`spec`, change-dialog.js), like the item's own line does.
function partLines(part, ref, depth, old, now, { lookup, fold, spec }) {
  // Made for each line: an element can be in one place only.
  const label = () => h('span', { class: 'diff-field' }, `${FIELD_WORDS[part]}:`);
  const opened = (sign) => ({ data: { focusKey: `diff-line:${sign}:${ref}:${part}` }, opens: opening(spec) });
  const one = (sign, value, key) => {
    if (part !== 'body') return [diffLine(sign, [label(), ' ', h('span', { dataset: { words: 'theirs' } }, valueWords(part, value, lookup))], { ref, part, depth, className: 'diff-part', ...opened(sign) })];
    const lines = [diffLine(sign, [label(), ' ', String(value ?? '').trim() ? null : quiet('none')], { ref, part, depth, className: 'diff-part', ...opened(sign) })];
    if (String(value ?? '').trim()) lines.push(...textLines(value, { sign, ref, part, depth: depth + 1, key, open: fold.open, onOpen: fold.onOpen }));
    return lines;
  };
  return { minus: one('-', old, `lines:old:${ref}`), plus: one('+', now, `lines:new:${ref}`) };
}

// The revision's changes as lines. `data` is what was read for the revision shown (the plan with its tree, changes and, when something
// was taken out, `previous`), `assist` its checks' flags and `notes` what is said on the lines. `showWhole()` opens the whole plan,
// which the fold of the items that did not change asks for. In the split layout the old column holds the line as it was (what was
// removed, what each changed part was, and the item's own line as a line that stays) and the new column the line as it is (what was
// added, the item as it is now, what each changed part is, and what is said on the lines): the same signed lines, each on its side.
export function deltaDiff(data, fold, assist, notes, showWhole, split = false) {
  const { tree, changes, previous } = data;
  const now = new Map(tree.map((one) => [one.ref, one]));
  const before = new Map((previous ?? []).map((one) => [one.ref, one]));
  const children = childrenOf(tree);
  const lookup = (ref) => now.get(ref)?.title ?? before.get(ref)?.title ?? store.nodes.get(ref)?.title ?? '';
  const kinds = { added: 0, changed: 0, removed: 0 };
  for (const one of changes) kinds[one.change] += 1;
  const said = Object.entries(kinds)
    .filter(([, count]) => count > 0)
    .map(([way, count]) => `${count} ${way}`);
  const header = hunk(`Revision ${data.shown}`, ': ', said.length ? said.flatMap((one, at) => (at ? [', ', one] : [one])) : 'no item changed');
  const unified = [];
  const oldSide = [];
  const newSide = [];
  const emit = (side, lines) => (split ? (side === 'old' ? oldSide : newSide) : unified).push(...lines);

  // Every item that has a line: the ones the revision changed, and the ones with something said on them.
  const drawn = new Map(changes.map((one) => [one.ref, one]));
  for (const one of tree) if (!drawn.has(one.ref) && notes.has(one.ref)) drawn.set(one.ref, { ref: one.ref, change: 'same', fields: [], before: {} });
  let shown = 0;
  for (const entry of [...drawn.values()].sort((a, b) => byRef(a.ref, b.ref))) {
    const kind = parseNodeRef(entry.ref)?.kind;
    const depth = kind === 'epic' ? 0 : kind === 'story' ? 1 : 2;
    const flags = assist.flags.get(entry.ref) ?? [];
    const mine = now.get(entry.ref);
    const common = { depth, flags, children: children.get(entry.ref) || 0, fold, notes };
    if (entry.change === 'added' && mine) {
      shown += 1;
      emit('new', planLines(mine, { ...common, sign: '+', open: (item) => itemSpec('added', item, { lookup }) }));
    } else if (entry.change === 'removed') {
      // Taken out: the line as it was. What was said about it before is under it; nothing can be said about it now.
      const old = before.get(entry.ref);
      if (old) emit('old', planLines(old, { ...common, sign: '-', details: false, notes: notes.removed, children: 0, flags: [], open: (item) => itemSpec('removed', item, { lookup }) }));
    } else if (entry.change === 'changed' && mine) {
      shown += 1;
      const spec = changedSpec(mine, entry, { lookup });
      emit('new', planLines(mine, { ...common, sign: '~', details: false, open: () => spec }));
      // The item as it was, for the old column to start the parts that changed from.
      if (split) emit('old', [diffLine('.', [link(entry.ref, entry.before?.title ?? mine.title)], { ref: entry.ref, part: 'before', depth, className: 'diff-before-head' })]);
      const was = entry.before;
      const is = nowValues(mine);
      for (const part of PARTS.filter((one) => entry.fields.includes(one))) {
        const { minus, plus } = partLines(part, entry.ref, depth + 1, was[part], is[part], { lookup, fold, spec });
        emit('old', minus);
        emit('new', plus);
      }
    } else if (mine) {
      shown += 1;
      emit('new', planLines(mine, { ...common, sign: '.', details: false, data: { unchanged: 'true' } }));
    }
  }
  const left = tree.length - shown;
  if (left > 0) emit('new', [foldOver('unchanged', `${plural(left, 'more item')}, unchanged`, 0, showWhole)]);
  return split ? [header, sides(oldSide, newSide, 'Nothing')] : [header, ...unified];
}
