// What a revision changed in a plan, as a diff (M51): the way to read a revised plan, since the person already read the revision before.
// Items the revision added are `+` lines as in the whole plan, items it took out are `-` lines as they were, and an item that is in
// both revisions but differs sits on a neutral modified header row (`changed`, with a dot in the gutter and a short badge naming the
// changed field or count) with, under it, each part that changed: a `-` line with what it was and a `+` line with what it is. Items that
// did not change are not drawn, except an item that something was said about (a comment from before, one not sent yet): it is drawn as a
// line that stays, with what was said under it, so that the person can see whether the agent answered it. What is left out is counted by a
// fold, and one press opens the whole plan.

import { day, h, plural } from './dom.js';
import { LINK, PRIORITY, SIZE_POINTS, SIZE_WORDS, byRef, codeOf, parseNodeRef, ruleCode } from './labels.js';
import { diffLine, foldApproved, foldOver, hunk, sides, textLines } from './diff.js';
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

// "Revision 2: 1 added, 2 changed, 1 removed": the hunk row of a revision read as what it changed.
export function deltaHeader(data) {
  const kinds = { added: 0, changed: 0, removed: 0 };
  for (const one of data.changes) kinds[one.change] += 1;
  const said = Object.entries(kinds)
    .filter(([, count]) => count > 0)
    .map(([way, count]) => `${count} ${way}`);
  return hunk(`Revision ${data.shown}`, ': ', said.length ? said.flatMap((one, at) => (at ? [', ', one] : [one])) : 'no item changed');
}

const NO_FOLD = { open: () => false, onOpen: () => {} };

// A compact before/after pair for a card preview: one red `-` line and one green `+` line per changed field, clipping long multiline
// details to their first line so the card stays scannable while still showing a real GitHub-style before/after diff.
function compactPartLines(part, ref, depth, old, now, { lookup, spec }) {
  const oneLine = (value) => {
    const raw = String(value ?? '').trim();
    if (!raw) return quiet('none');
    const lines = raw.split('\n');
    const first = lines[0].trim();
    return lines.length > 1 ? `${first} \u2026` : first;
  };
  const label = () => h('span', { class: 'diff-field' }, `${FIELD_WORDS[part]}:`);
  const opened = (sign) => ({ data: { focusKey: `diff-line:${sign}:${ref}:${part}` }, opens: opening(spec) });
  const render = (sign, value) =>
    diffLine(sign, [label(), ' ', h('span', { dataset: { words: 'theirs' } }, part === 'body' ? oneLine(value) : valueWords(part, value, lookup))], { ref, part, depth, className: 'diff-part', ...opened(sign) });
  return [render('-', old), render('+', now)];
}

// What a revision changed, for a card's preview: the hunk row and, for each item the revision added, changed or took out, its own line
// (`+`, `changed` or `-`, as on the review page) with the controls of `notes` beside it and, for a changed item, crisp red `-` and green `+`
// before/after lines for the fields that changed, as a list of groups of elements (one per item) so the card shows up to six items before folding.
export function deltaPreview(data, assist, notes) {
  const { tree, changes, previous } = data;
  const now = new Map(tree.map((one) => [one.ref, one]));
  const before = new Map((previous ?? []).map((one) => [one.ref, one]));
  const children = childrenOf(tree);
  const lookup = (ref) => now.get(ref)?.title ?? before.get(ref)?.title ?? store.nodes.get(ref)?.title ?? '';
  const groups = [];
  for (const entry of [...changes].sort((a, b) => byRef(a.ref, b.ref))) {
    const kind = parseNodeRef(entry.ref)?.kind;
    const depth = kind === 'epic' ? 0 : kind === 'story' ? 1 : 2;
    const mine = now.get(entry.ref);
    const common = { depth, flags: assist.flags.get(entry.ref) ?? [], children: children.get(entry.ref) || 0, fold: NO_FOLD, notes, details: false };
    if (entry.change === 'added' && mine) groups.push(planLines(mine, { ...common, sign: '+', open: (item) => itemSpec('added', item, { lookup }) }));
    else if (entry.change === 'removed' && before.has(entry.ref)) {
      groups.push(planLines(before.get(entry.ref), { ...common, sign: '-', children: 0, flags: [], open: (item) => itemSpec('removed', item, { lookup: (ref) => before.get(ref)?.title ?? lookup(ref) }) }));
    } else if (entry.change === 'changed' && mine) {
      const spec = changedSpec(mine, entry, { lookup });
      const was = entry.before;
      const is = nowValues(mine);
      const parts = PARTS.filter((one) => entry.fields.includes(one)).flatMap((part) => compactPartLines(part, entry.ref, depth + 1, was[part], is[part], { lookup, spec }));
      groups.push([...planLines(mine, { ...common, sign: 'changed', open: () => spec }), ...parts]);
    }
  }
  return { header: deltaHeader(data), groups };
}

// The revision's changes as lines. `data` is what was read for the revision shown (the plan with its tree, changes and, when something
// was taken out, `previous`), `assist` its checks' flags and `notes` what is said on the lines. `showWhole()` opens the whole plan,
// which the fold of the items that did not change asks for. In the split layout the old column holds the line as it was (what was
// removed, what each changed part was, and the item's own line as a line that stays) and the new column the line as it is (what was
// added, the item as it is now, what each changed part is, and what is said on the lines): the same signed lines, each on its side.
// `hidden` is the set of approved items whose lines are folded away, or null.
export function deltaDiff(data, fold, assist, notes, showWhole, split = false, hidden = null) {
  const { tree, changes, previous } = data;
  const now = new Map(tree.map((one) => [one.ref, one]));
  const before = new Map((previous ?? []).map((one) => [one.ref, one]));
  const children = childrenOf(tree);
  const lookup = (ref) => now.get(ref)?.title ?? before.get(ref)?.title ?? store.nodes.get(ref)?.title ?? '';
  const header = deltaHeader(data);
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
      // Taken out: the line as it was, with what was said about it before under it. A person can still agree it goes, or ask for it back.
      const old = before.get(entry.ref);
      if (old) emit('old', planLines(old, { ...common, sign: '-', details: false, children: 0, flags: [], open: (item) => itemSpec('removed', item, { lookup: (ref) => before.get(ref)?.title ?? lookup(ref) }) }));
    } else if (entry.change === 'changed' && mine) {
      shown += 1;
      const spec = changedSpec(mine, entry, { lookup });
      emit('new', planLines(mine, { ...common, sign: 'changed', details: false, open: () => spec }));
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
  // Hide approved: the lines of the approved items, folded behind one row for each run of them, in each column.
  const folded = (side, lines) => (hidden ? foldApproved(lines, hidden, { ...fold, side }) : lines);
  return split ? [header, sides(folded('old', oldSide), folded('new', newSide), 'Nothing')] : [header, ...folded('new', unified)];
}
