// The body of a plan and of a suggested move, drawn as a diff (diff.js has the lines); the review page shows them whole. A plan is a tree
// of added lines under a hunk header, with the epic or story it hangs under as quiet lines that stay for context; a suggested move is
// the proposer's reason as its commit message, then the new parent with the work moved under it as added lines and the old parent with
// the same work as removed ones. Nothing is cut off: what is folded says how much is behind it. (The other kinds of change are drawn by
// review-changes.js.)
//
// What helps a person weigh a change is beside the lines: a flag beside each item of a plan that a check is about, the score of each
// moved item beside its line and, in the split layout, the old state and the new one side by side (the same lines, each on its side). The
// words are the server's and the board's own copy of the work (`store`); a flag or a score is evidence for a person and decides nothing.

import { itemLink } from './ctx.js';
import { day, h, plural } from './dom.js';
import { icon } from './icons.js';
import { LINK, codeOf, dueChip, kindIcon, noSizeChip, parseNodeRef, priorityChip, ruleCode, sizeChip, itemSizeChip, statusChip } from './labels.js';
import { diffLine, foldOver, hunk, sides, textLines } from './diff.js';
import { itemSpec, lookupOf, moveSpec, newEpicSpec, opening } from './change-dialog.js';
import { ordinal, percentOf, scoreLabel } from './needs-assist.js';
import { store } from './store.js';

// A plan with more items than this folds each story's tasks behind a line that says how many.
const FOLD_ABOVE = 40;

const titleOf = (ref, fallback = '') => store.nodes.get(ref)?.title ?? fallback;

// An item as a link with its kind, code and title.
const itemParts = (ref, title) => {
  const found = parseNodeRef(ref);
  return [found ? kindIcon(found.kind, 14) : null, h('span', { class: 'item-code' }, codeOf(ref)), ' ', h('span', { class: 'item-title', dataset: { words: 'theirs' } }, title)];
};

export const link = (ref, title, className = 'item-link diff-item', extra = {}) => itemLink(ref, { class: className, dataset: { ref, focusKey: `diff-item:${ref}`, ...extra } }, ...itemParts(ref, title));

// The same words as plain text, for a line that is a button itself and so cannot hold a link (diff.js): the page of the item is the link
// in the dialog the line opens.
export const itemText = (ref, title, className = 'item-link diff-item', extra = {}) => h('span', { class: className, dataset: { ref, ...extra } }, ...itemParts(ref, title));

export const meta = (...chips) => {
  const some = chips.flat().filter(Boolean);
  return some.length ? h('span', { class: 'diff-meta' }, some) : null;
};

export const problem = (words) => h('p', { class: 'diff-problem', role: 'alert' }, icon('alert', { size: 16 }), h('span', {}, words));
export const loading = (words) => h('p', { class: 'diff-loading quiet' }, words);

// -- what a change is about ----------------------------------------------------------------------------------

// The item a change is about, as context lines: its story above it when it is a task, then the item with its status and size.
export function aboutLines(item) {
  const node = store.nodes.get(item.node);
  const parent = node?.parent ? store.nodes.get(node.parent) : null;
  const lines = [];
  if (parent && parent.kind === 'story') lines.push(diffLine('.', [link(parent.ref, parent.title), meta(statusChip(parent.status))], { ref: parent.ref, depth: 0, className: 'diff-about' }));
  lines.push(
    diffLine('.', [link(item.node, node?.title ?? item.title, 'item-link needs-item diff-item', {}), node ? meta(statusChip(node.status), itemSizeChip(node, { labelled: true })) : null], {
      ref: item.node,
      depth: parent && parent.kind === 'story' ? 1 : 0,
      className: 'diff-about',
    }),
  );
  return lines;
}

// -- a plan --------------------------------------------------------------------------------------------------

// A flag beside an item that a check is about: the short words, and the server's whole sentence as its label. A flag never takes the place of
// what the line says; a colour is never its only cue.
function flagChip(flag) {
  return h('span', { class: 'diff-flag', role: 'img', 'aria-label': flag.label, dataset: { check: flag.check, state: flag.state } }, icon('alert', { size: 13 }), flag.text);
}

// What a plan's item carries on its line: its size (or that it has none), a priority that is not Normal, its dates, what it needs
// first and its other links, and how many acceptance criteria it has.
function planMeta(item, children, flags, { plain = false } = {}) {
  const node = { kind: item.kind, status: 'new', size: item.size, progress_total: children };
  // An item with no size says so once: the flag is the words, so the faint sign is left off.
  const unsized = flags.some((one) => one.check === 'unsized');
  const links = item.links.map((one) =>
    h(
      'span',
      { class: 'diff-link', dataset: { linkType: one.link_type, target: one.target } },
      one.link_type === 'needs_first' ? 'needs' : (LINK[one.link_type]?.label ?? one.link_type),
      ' ',
      plain ? h('span', { class: 'item-link', dataset: { ref: one.target } }, codeOf(one.target)) : itemLink(one.target, { class: 'item-link', dataset: { ref: one.target } }, codeOf(one.target)),
    ),
  );
  return meta(
    flags.map(flagChip),
    sizeChip(node, { labelled: true }) ?? (unsized ? null : noSizeChip(node)),
    item.priority !== 'normal' ? priorityChip(item.priority) : null,
    item.start_on ? h('span', { class: 'chip start' }, icon('calendar', { size: 13 }), `Starts ${day(item.start_on)}`) : null,
    item.target_on ? dueChip({ target_on: item.target_on }) : null,
    links,
    item.criteria.length ? h('span', { class: 'diff-count' }, `criteria ${item.criteria.length}`) : null,
  );
}

// What lines of a plan may carry besides the plan's own words (plan-notes.js): a button to comment on an item and what is said under
// it (earlier comments, comments not sent yet, the box to write one). A plan that is only read has none.
const NO_NOTES = { button: () => null, below: () => [] };

// One item as lines: its line with its sign, what is said under it, its details as raw lines of the same sign and its acceptance
// criteria. `closed` is the fold that hides it, if any; `details` false leaves out the details and the criteria (a removed item is
// only its line as it was). `open(one, sign)` gives what the line opens when it is pressed (change-dialog.js), or nothing: a line that
// stays (`.`) never opens anything and keeps its links.
export function planLines(one, { sign = '+', depth, closed = null, flags = [], children = 0, fold, notes = NO_NOTES, details = true, data = {}, open = null }) {
  const spec = open && sign !== '.' ? open(one, sign) : null;
  const made = [
    diffLine(sign, [(spec ? itemText : link)(one.ref, one.title), planMeta(one, children, flags, { plain: Boolean(spec) }), notes.button(one)], {
      ref: one.ref,
      depth,
      closed,
      className: 'diff-item-line',
      data: { kind: one.kind, size: one.size, priority: one.priority, start: one.start_on, target: one.target_on, ...(spec ? { focusKey: `diff-line:${sign}:${one.ref}` } : {}), ...data },
      opens: spec ? opening(spec) : null,
    }),
    ...notes.below(one, closed, depth),
  ];
  if (!details) return made;
  if (one.body.trim()) made.push(...textLines(one.body, { sign, ref: one.ref, part: 'body', depth: depth + 1, key: `lines:${one.ref}`, open: fold.open, onOpen: fold.onOpen, closed }));
  for (const criterion of one.criteria) {
    made.push(
      diffLine(sign, [icon('rule', { size: 14 }), ' ', itemLink(`rule:${criterion.id}`, { class: 'item-link diff-rule', dataset: { rule: criterion.id, focusKey: `diff-rule:${criterion.id}` } }, h('span', { class: 'item-code' }, ruleCode(criterion.id)), ' ', h('span', { dataset: { words: 'theirs' } }, criterion.title))], {
        ref: one.ref,
        part: 'criteria',
        depth: depth + 1,
        closed,
        className: 'diff-criterion',
        data: { rule: criterion.id },
      }),
    );
    if (criterion.body.trim()) made.push(...textLines(criterion.body, { sign, ref: one.ref, part: 'criteria', depth: depth + 2, key: `lines:rule:${criterion.id}`, open: fold.open, onOpen: fold.onOpen, closed, data: { rule: criterion.id } }));
  }
  return made;
}

// How deep each item of a plan is drawn: under the item it hangs under when that is in the plan, one level in under an existing item
// the plan hangs under, and otherwise at the left.
export function depthsOf(tree, context) {
  const inside = new Map(tree.map((one) => [one.ref, one]));
  const hangsUnder = new Set(context.filter((one) => one.why === 'parent').map((one) => one.ref));
  const depths = new Map();
  const depthOf = (one) => {
    if (!depths.has(one.ref)) depths.set(one.ref, inside.has(one.parent) ? depthOf(inside.get(one.parent)) + 1 : hangsUnder.has(one.parent) ? 1 : 0);
    return depths.get(one.ref);
  };
  return depthOf;
}

// How many children each item of a plan has in it, for the size of a story.
export function childrenOf(tree) {
  const children = new Map();
  for (const one of tree) if (one.parent) children.set(one.parent, (children.get(one.parent) || 0) + 1);
  return children;
}

// "1 epic, 2 stories, 4 tasks added": what a plan holds, for its hunk row. A list of pieces, so that each is its own text.
export function addsWords(tree) {
  const count = (kind) => tree.filter((one) => one.kind === kind).length;
  const added = [plural(count('epic'), 'epic'), plural(count('story'), 'story', 'stories'), plural(count('task'), 'task')].filter((one) => !one.startsWith('0 '));
  return [added.flatMap((one, at) => (at ? [', ', one] : [one])), ' added'];
}

// What exists already that the plan hangs under or points at: lines that stay. Made again each time it is asked for: the split layout
// draws them on both sides.
const existingLines = (context) =>
  context.map((one) =>
    diffLine('.', [link(one.ref, one.title), ' ', h('span', { class: 'diff-why quiet' }, one.why === 'parent' ? 'the plan hangs under it' : 'the plan points to it')], { ref: one.ref, depth: 0, className: 'diff-context', data: { context: one.why } }),
  );

// The whole of a plan: the hunk header, the existing items it hangs under or points at as lines that stay, and every item it made
// as an added line in the order the server gave (parents first), nested by depth, each with its details and criteria under it.
// `notes` is what is said on the lines (comments, and what was said before).
export function planDiff(entry, fold, assist, split, notes = NO_NOTES) {
  if (entry.state === 'failed') return [problem(`The whole plan could not be read: ${entry.error}`)];
  if (!entry.data) return [loading('Reading…')];
  const { tree, context } = entry.data;
  const children = childrenOf(tree);
  const depthOf = depthsOf(tree, context);
  const header = hunk(addsWords(tree));
  const out = [];
  const lookup = lookupOf(tree, context);
  const lines = (one, closed) => planLines(one, { depth: depthOf(one), closed, flags: assist.flags.get(one.ref) ?? [], children: children.get(one.ref) || 0, fold, notes, open: (item) => itemSpec('added', item, { lookup }) });

  const large = tree.length > FOLD_ABOVE;
  let at = 0;
  while (at < tree.length) {
    const one = tree[at];
    if (large && one.kind === 'task') {
      // A story's tasks, folded behind a line that says how many; the lines are in the page and a press opens them.
      let end = at;
      while (end < tree.length && tree[end].kind === 'task' && tree[end].parent === one.parent) end += 1;
      const run = tree.slice(at, end);
      const key = `tasks:${one.parent ?? 'none'}`;
      const shut = !fold.open(key);
      if (shut) out.push(foldOver(key, plural(run.length, 'more task'), depthOf(one), fold.onOpen));
      for (const task of run) out.push(...lines(task, shut ? key : null));
      at = end;
    } else {
      out.push(...lines(one, null));
      at += 1;
    }
  }
  if (!split) return [header, ...existingLines(context), ...out];
  return [sides(existingLines(context), [header, ...existingLines(context), ...out], 'Nothing')];
}

// -- a suggested move ----------------------------------------------------------------------------------------

// What a proposal would do, as a diff: its reason as the commit message, the new parent with its first two children for context
// and the work moved under it as added lines ("from E-9 Misc"), a new epic as an added line with its details, and the old
// parent or parents with the same work as removed lines. Beside each added line is how the moved item scores for the parent (the
// same score the parent suggestions give), where that parent ranks when it is not first, the lowest score marked, and anything the
// server says is worth weighing, in its words. A score is evidence for a person, never an approval.
export function moveDiff(change, fold, assist, split) {
  const move = change.item.placement;
  const moving = new Set(move.items.map((one) => one.ref));
  const titleFor = (one) => titleOf(one.ref, one.title);
  const here = (ref, plain = false) => (plain ? h('span', { class: 'item-link', dataset: { ref } }, h('span', { class: 'item-code' }, codeOf(ref)), ' ', h('span', { dataset: { words: 'theirs' } }, titleOf(ref))) : itemLink(ref, { class: 'item-link', dataset: { ref } }, h('span', { class: 'item-code' }, codeOf(ref)), ' ', h('span', { dataset: { words: 'theirs' } }, titleOf(ref))));
  // The lines that open a dialog are buttons and hold no link, so what they say of another item is plain text.
  const from = (one) => (one.from_parent ? h('span', { class: 'diff-from quiet' }, 'from ', here(one.from_parent, true)) : null);

  // What the score of one moved item says, as chips and words beside its line.
  const scored = (one) => {
    const score = assist.scores.get(one.ref);
    if (!score) return [];
    const chips = [];
    const words = [];
    if (score.similarity !== null && score.similarity !== undefined) {
      chips.push(h('span', { class: 'chip diff-score', role: 'img', 'aria-label': scoreLabel(score), dataset: { ref: one.ref, percent: String(percentOf(score.similarity)), rank: score.rank ?? null } }, `${percentOf(score.similarity)}%`));
      if (assist.marked.has(one.ref)) chips.push(h('span', { class: 'chip diff-lowest', dataset: { ref: one.ref } }, 'lowest'));
      if (score.rank && score.rank !== 1) {
        words.push(h('span', { class: 'diff-rank', dataset: { ref: one.ref } }, `ranks ${ordinal(score.rank)}`, score.best ? [', best is ', here(score.best, true)] : null));
      }
    }
    if (score.warning) {
      chips.push(h('span', { class: 'diff-flag', role: 'img', 'aria-label': `Warning: ${score.warning}`, dataset: { check: 'warning', state: 'warning', ref: one.ref } }, icon('alert', { size: 13 }), 'Warning'));
      words.push(h('span', { class: 'diff-flag-words', 'aria-hidden': 'true', dataset: { words: 'theirs' } }, score.warning));
    }
    if (score.note) words.push(h('span', { class: 'diff-score-note quiet', dataset: { ref: one.ref, words: 'theirs' } }, score.note));
    return [meta(chips), ...words];
  };
  // A line of a moved item opens the item as the move would leave it, whichever side of the move it is on.
  const opens = (one, sign) => ({ opens: opening(moveSpec(one, move)), data: { kind: parseNodeRef(one.ref)?.kind, focusKey: `diff-line:${sign}:${one.ref}` } });
  const added = (one, depth) => diffLine('+', [itemText(one.ref, titleFor(one)), from(one), ...scored(one)], { ref: one.ref, depth, className: 'diff-item-line', ...opens(one, '+') });
  const removed = (one) => diffLine('-', [itemText(one.ref, titleFor(one))], { ref: one.ref, depth: 0, className: 'diff-item-line', ...opens(one, '-') });

  // The reason, then why the server calls the move routine or not, and why there are no scores when there are none.
  const proposal = assist.proposal;
  const same = proposal && proposal.scores_note && proposal.scores_note === proposal.routine_why;
  const message = h(
    'div',
    { class: 'diff-message' },
    h('span', { class: 'diff-message-label quiet' }, 'Why'),
    h('p', { class: 'needs-move-reason', dataset: { words: 'theirs' } }, move.reason),
    proposal && proposal.routine_why ? h('p', { class: `change-routine-why quiet${same ? ' change-scores-note' : ''}`, dataset: { words: 'theirs' } }, proposal.routine_why) : null,
    proposal && proposal.scores_note && !same ? h('p', { class: 'change-scores-note quiet', dataset: { words: 'theirs' } }, proposal.scores_note) : null,
    assist.failed ? h('p', { class: 'diff-problem', role: 'alert' }, icon('alert', { size: 16 }), h('span', {}, `The scores could not be read: ${assist.failed}`)) : null,
  );

  const siblings = (parent) =>
    [...store.nodes.values()]
      .filter((node) => node.parent === parent && !moving.has(node.ref))
      .sort((a, b) => a.number - b.number)
      .slice(0, 2)
      .map((node) => diffLine('.', [link(node.ref, node.title)], { ref: node.ref, depth: 0, className: 'diff-context', data: { context: 'sibling' } }));

  // The new place: the parent with its first two children, or the new epic, and the work under it as added lines.
  const after = [];
  if (move.parent) {
    after.push(hunk(here(move.parent)));
    after.push(...siblings(move.parent));
    for (const one of move.items) after.push(added(one, 0));
  } else if (move.new_epic) {
    after.push(hunk('A new epic'));
    after.push(diffLine('+', [kindIcon('epic', 14), ' ', h('strong', { class: 'diff-new-epic', dataset: { words: 'theirs' } }, move.new_epic.title)], { depth: 0, className: 'diff-item-line', data: { kind: 'epic', newEpic: 'true', focusKey: 'diff-line:+:new-epic' }, opens: opening(newEpicSpec(move.new_epic)) }));
    if (move.new_epic.body?.trim()) after.push(...textLines(move.new_epic.body, { ref: null, part: 'body', depth: 1, key: 'lines:new-epic', open: fold.open, onOpen: fold.onOpen, data: { newEpic: 'true' } }));
    for (const one of move.items) after.push(added(one, 1));
  }

  // The old places, in the order the work is listed: each parent with the work taken from it as removed lines. The split layout also
  // shows the old parent's first two other children, as context, as it shows the new parent's.
  const before = [];
  for (const place of [...new Set(move.items.map((one) => one.from_parent ?? ''))]) {
    before.push(place ? hunk(here(place)) : hunk('No parent'));
    if (split && place) before.push(...siblings(place));
    for (const one of move.items.filter((each) => (each.from_parent ?? '') === place)) before.push(removed(one));
  }
  if (!split) return [message, ...after, ...before];
  return [message, sides(before, after, 'Nothing')];
}
