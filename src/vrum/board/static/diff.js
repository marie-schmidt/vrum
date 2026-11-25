// The pieces a change is drawn from, in the manner of a diff: a hunk row (a quiet label such as "1 epic, 2 stories, 4 tasks added"), lines with a
// sign in a gutter (`+` added, `-` removed, `.` there already, for context), the raw text of a body as added lines, and the folds
// that hide what is too long to read in one go. A fold always says how much is behind it, and one press opens all of it: nothing is
// ever cut off. The text of a body is shown as it was written, not as Markdown: it is a diff, and a diff shows what is in the file.
//
// What is folded is in the page all the same (marked `data-closed`, which hides it) so that the page, a search in it and a test
// can see everything the change holds; opening a fold only takes the mark off. Which folds are open is the card's to remember.

import { h } from './dom.js';
import { icon } from './icons.js';

// How many lines of a body are shown before "Show N more lines".
const MOST_LINES = 6;

const WORDS = { '+': 'added', '-': 'removed', '.': 'existing', '~': 'changed' };

// A hunk row: a quiet label in the colours of a hunk, in plain words (no marks around it). `content` is its words (a link may be among them).
export const hunk = (...content) => h('div', { class: 'diff-hunk' }, ...content);

// One line of a change. The sign is in the gutter and is the meaning; the colour behind it only helps, and a screen reader is told
// "added", "removed", "changed" or "existing" first. `ref` is the item the line is about (a body or a criterion names its item); `part` says
// what kind of line it is when it is not the item itself ("body" or "criteria"); `closed` is the key of the fold that hides it.
export function diffLine(sign, content, { ref = null, part = null, depth = 0, closed = null, className = '', data = {}, opens = null } = {}) {
  const line = h(
    'div',
    { class: `diff-line${className ? ` ${className}` : ''}`, dataset: { sign, ref, part, depth: String(Math.min(depth, 5)), closed, ...data } },
    h('span', { class: 'diff-sign', 'aria-hidden': 'true' }, sign),
    h('div', { class: 'diff-code' }, h('span', { class: 'sr-only' }, `${WORDS[sign]}: `), content),
  );
  if (opens) openable(line, opens);
  return line;
}

let counted = 0;

// A red, green or changed line is the control that shows the work as the screen a person would use for it (change-dialog.js). It is a
// button, so what it holds cannot be a link: the code and title in it are plain text. What it holds that is a control of its own (the comment
// button) keeps working and does not open the dialog. `opens`: `label` (what an assistive technology hears) and `open(line)`.
export function openable(line, { label, open }) {
  const code = line.querySelector('.diff-code');
  line.classList.add('diff-openable');
  line.setAttribute('role', 'button');
  line.tabIndex = 0;
  line.setAttribute('aria-haspopup', 'dialog');
  line.setAttribute('aria-label', label);
  // The label is short; what the line says (its words, size and flags) is its description.
  if (code) {
    code.id = `diff-line-${(counted += 1)}`;
    line.setAttribute('aria-describedby', code.id);
  }
  line.addEventListener('click', (event) => {
    const inner = event.target instanceof Element ? event.target.closest('a, button, input, select, textarea') : null;
    if (inner && inner !== line && line.contains(inner)) return;
    open(line);
  });
  line.addEventListener('keydown', (event) => {
    if (event.target !== line || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    open(line);
  });
}

// The row of a fold: a button that says what is behind it and opens all of it. Opening takes the marks off what it hides, in the
// change it is in, and is remembered by `view` so that the page being drawn again does not close it.
function foldRow(key, words, depth, onOpen) {
  const button = h('button', { type: 'button', class: 'diff-fold', 'aria-expanded': 'false', dataset: { fold: key, focusKey: `fold:${key}` } }, words);
  button.addEventListener('click', () => {
    const root = button.closest('.change-diff');
    if (root) for (const hidden of root.querySelectorAll('[data-closed]')) if (hidden.dataset.closed === key) hidden.removeAttribute('data-closed');
    for (const hidden of (root || document).querySelectorAll('[data-clipped]')) if (hidden.dataset.clipped === key) hidden.removeAttribute('data-clipped');
    onOpen(key);
    button.closest('.diff-foldrow')?.remove();
  });
  return h('div', { class: 'diff-foldrow', dataset: { depth: String(Math.min(depth, 5)) } }, h('span', { class: 'diff-sign', 'aria-hidden': 'true' }, icon('chevron_down', { size: 14 })), h('div', { class: 'diff-code' }, button));
}

// The lines of a text, raw, as added lines under an item. The first MOST_LINES are shown; the rest are folded behind
// "Show N more lines". `open(key)` says whether a fold is already open. Returns the elements, in order.
export function textLines(text, { ref, part, depth, key, open, onOpen, sign = '+', closed = null, data = {} }) {
  const lines = String(text).replace(/\s+$/, '').split('\n');
  const line = (one, clipped) => {
    const element = diffLine(sign, h('span', { dataset: { words: 'theirs' } }, one), { ref, part, depth, closed, className: 'diff-text', data });
    if (clipped) element.dataset.clipped = key;
    return element;
  };
  if (lines.length <= MOST_LINES || open(key)) return lines.map((one) => line(one, false));
  const shown = lines.slice(0, MOST_LINES).map((one) => line(one, false));
  const row = foldRow(key, `Show ${lines.length - MOST_LINES} more lines`, depth, onOpen);
  if (closed) row.dataset.closed = closed;
  return [...shown, row, ...lines.slice(MOST_LINES).map((one) => line(one, true))];
}

// A fold over whole lines that are folded away (a story's tasks in a large plan): the row says how many are behind it.
export const foldOver = (key, words, depth, onOpen) => foldRow(key, words, depth, onOpen);

// The split view of a change that has a before and an after: two columns side by side, the old state and the new. The same lines are
// drawn as in the unified view, each on the side it belongs to: nothing is left out and nothing is added to what is added or removed.
// `empty` says what a side with nothing on it means.
export function sides(before, after, empty) {
  const side = (name, heading, lines, said) =>
    h('div', { class: 'diff-side', dataset: { side: name } }, h('h4', { class: 'diff-side-head' }, heading), lines.length ? lines : h('p', { class: 'diff-empty quiet' }, said));
  return h('div', { class: 'diff-sides' }, side('old', 'Before', before, empty), side('new', 'After', after, 'Nothing'));
}
