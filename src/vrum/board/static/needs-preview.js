// The short preview on a card of the page of what needs a person (M52): enough of a change to judge whether to open it, never all of
// it. A plan is its first few items as added lines, a suggested move its first few moved items, a question its words and the answers on
// offer, a check the checks still to tick, notes their first lines, a refused approval the line that lifting it would remove, and a claim
// that ran out the work it is about. At most six lines of a diff are shown and then one fold that says how much is behind it (pressing it
// shows the rest in place); nothing is cut off without saying how much. The review page (review.js) shows the whole change.
//
// A row of several checks or several notes has a bar under each of them (decision-bar.js): the bar of the row would not say which.

import { itemLink } from './ctx.js';
import { h, plural } from './dom.js';
import { codeOf, noteCode, parseNodeRef, kindIcon } from './labels.js';
import { barsPerLine, checkBar, noteBar, openChecks, notesOf } from './decision-bar.js';
import { offeredList } from './asks.js';
import { diffLine, foldOver, hunk, openable } from './diff.js';
import { addsWords, aboutLines, childrenOf, depthsOf, itemText, loading, planLines, problem } from './needs-diffs.js';
import { itemSpec, lookupOf, moveSpec, newEpicSpec, noteSpec, opening } from './change-dialog.js';
import { store } from './store.js';

// The most lines of a diff a preview shows before its fold.
const PREVIEW_LINES = 6;

const NO_FOLD = { open: () => false, onOpen: () => {} };
const titleOf = (ref, fallback = '') => store.nodes.get(ref)?.title ?? fallback;
const reading = () => [loading('Reading…')];

// The lines of a list, the first PREVIEW_LINES of them unless it is opened, and the fold that says how many more there are.
function limited(lines, { expanded, onMore, word }) {
  if (expanded || lines.length <= PREVIEW_LINES) return lines;
  return [...lines.slice(0, PREVIEW_LINES), foldOver('preview', plural(lines.length - PREVIEW_LINES, word), 0, onMore)];
}

function planPreview(change, entry, ctx) {
  const { plan } = change.item;
  const data = entry && entry.data && entry.data.revision === plan.revision ? entry.data : null;
  if (!data) return entry && entry.state === 'failed' ? [problem(`The plan could not be read: ${entry.error}`)] : reading();
  const { tree, context } = data;
  const children = childrenOf(tree);
  const depthOf = depthsOf(tree, context);
  const lookup = lookupOf(tree, context);
  const lines = tree.map((one) => planLines(one, { depth: depthOf(one), flags: change.assist.flags.get(one.ref) ?? [], children: children.get(one.ref) || 0, fold: NO_FOLD, details: false, open: (item) => itemSpec('added', item, { lookup }) })[0]);
  return [hunk(addsWords(tree)), ...limited(lines, { ...ctx, word: 'more item' })];
}

function placementPreview(change, ctx) {
  if (change.assist.reading) return reading();
  const move = change.item.placement;
  const here = (ref) => itemLink(ref, { class: 'item-link', dataset: { ref } }, h('span', { class: 'item-code' }, codeOf(ref)), ' ', h('span', { dataset: { words: 'theirs' } }, titleOf(ref)));
  // A line that opens a dialog is a button and holds no link: the old parent in it is plain text.
  const from = (one) => (one.from_parent ? h('span', { class: 'diff-from quiet' }, 'from ', h('span', { class: 'item-link', dataset: { ref: one.from_parent } }, h('span', { class: 'item-code' }, codeOf(one.from_parent)), ' ', h('span', { dataset: { words: 'theirs' } }, titleOf(one.from_parent)))) : null);
  const lines = [];
  if (!move.parent && move.new_epic) {
    lines.push(diffLine('+', [kindIcon('epic', 14), ' ', h('strong', { class: 'diff-new-epic', dataset: { words: 'theirs' } }, move.new_epic.title)], { depth: 0, className: 'diff-item-line', data: { kind: 'epic', newEpic: 'true', focusKey: 'diff-line:+:new-epic' }, opens: opening(newEpicSpec(move.new_epic)) }));
  }
  for (const one of move.items) lines.push(diffLine('+', [itemText(one.ref, titleOf(one.ref, one.title)), from(one)], { ref: one.ref, depth: move.parent ? 0 : 1, className: 'diff-item-line', data: { kind: parseNodeRef(one.ref)?.kind, focusKey: `diff-line:+:${one.ref}` }, opens: opening(moveSpec(one, move)) }));
  return [hunk(move.parent ? here(move.parent) : 'A new epic'), ...limited(lines, { ...ctx, word: 'more line' })];
}

function askPreview(change) {
  const { ask } = change.item;
  return [h('p', { class: 'preview-question', dataset: { words: 'theirs' } }, ask.question), offeredList(ask)];
}

// One line of the checks a person has to tick, with its bar under it when there are several.
function checksPreview(change, entry, ctx) {
  if (!entry || (entry.state === 'loading' && !entry.data)) return reading();
  if (!entry.data) return [problem(`The checks could not be read: ${entry.error}`)];
  const open = openChecks(entry);
  const several = barsPerLine(change, entry);
  return [
    h(
      'ul',
      { class: 'plain preview-checks' },
      open.map((check) => h('li', { class: 'preview-check', dataset: { item: check.item } }, h('span', { class: 'diff-check-words', dataset: { words: 'theirs' } }, check.text), several ? checkBar(change, check, ctx.hooks) : null)),
    ),
  ];
}

// A note's first line: its code and subject. It is a button that shows the note as a record, so what it holds is plain text.
function noteLine(note) {
  const subject = String(note.text).replace(/^\s+/, '').split('\n')[0] || note.headline;
  const line = h('p', { class: 'preview-note-line', dataset: { focusKey: `diff-line:+:note:${note.id}` } }, h('span', { class: 'item-link', dataset: { note: note.id } }, h('span', { class: 'item-code' }, noteCode(note.id))), ' ', h('strong', { class: 'diff-subject', dataset: { words: 'theirs' } }, subject));
  openable(line, opening(noteSpec(note)));
  return line;
}

// Each note's first line, with its bar under it when there are several.
function notesPreview(change, entry, ctx) {
  if (!entry || (entry.state === 'loading' && !entry.data)) return reading();
  if (!entry.data) return [problem(`The notes could not be read: ${entry.error}`)];
  const several = barsPerLine(change, entry);
  return [
    h(
      'ul',
      { class: 'plain preview-notes' },
      notesOf(entry).map((note) => h('li', { class: 'preview-note', dataset: { note: note.id } }, noteLine(note), several ? noteBar(change, note, ctx.hooks) : null)),
    ),
  ];
}

// The line that lifting a refused approval would remove, under the work it holds.
function gatePreview(change) {
  const { gate } = change.item;
  if (!gate) return aboutLines(change.item).slice(-1);
  return [diffLine('-', h('span', { class: 'needs-gate-words', dataset: { gate: gate.id, words: 'theirs' } }, gate.words), { ref: change.item.node, depth: 0, className: 'diff-gate', data: { gate: gate.id } })];
}

// What the card shows of a change, as a list of elements (possibly none). `ctx`: `entry` (what was read for it), `expanded` (the fold
// was pressed), `onMore()` (draw the rest) and `hooks` (for the bars under the lines of several checks or notes).
export function previewOf(change, ctx) {
  switch (change.look) {
    case 'plan':
      return planPreview(change, ctx.entry, ctx);
    case 'placement':
      return placementPreview(change, ctx);
    case 'question':
    case 'approval':
      return askPreview(change);
    case 'check':
      return checksPreview(change, ctx.entry, ctx);
    case 'note':
      return notesPreview(change, ctx.entry, ctx);
    case 'gate':
      return gatePreview(change);
    default:
      return aboutLines(change.item).slice(-1);
  }
}
