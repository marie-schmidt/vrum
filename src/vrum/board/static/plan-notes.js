// What is said on the lines of a plan (M51), drawn under the line it is about, in the manner of the conversation under a line of a pull
// request: the comments of earlier feedback (what the person asked for before, with the words each was about as they were then, so that
// they can see whether the agent answered it), the comments written and not sent yet, and the box to write one. Each is a row of the
// diff with its own gutter, so a fold that hides a line hides what is said under it.
//
// Everything is the page's own memory (plan-comments.js) or the server's (the feedback); nothing is sent from here. A comment is only
// sent with the request for changes (plan-changes.js), and a person who cannot write sees what was said and has no control.

import { waitedWords } from './asks.js';
import { isMe } from './ctx.js';
import { h, moment, put } from './dom.js';
import { field } from './forms.js';
import { icon } from './icons.js';
import { actorLabel, codeOf } from './labels.js';
import { COMMENT_FIELDS, FIELD_WORDS, addPending, approvedOf, changePending, closeComposer, composerOf, hasPendingComment, pendingOf, quotedOf, removePending, threadsOf, toggleApproved } from './plan-comments.js';
import { approveButton, blameChip, commentButton, reviewChip, reviewsOf, stateOf } from './plan-verdicts.js';

const MOST_COMMENT = 2000; // what the server accepts for one comment

// What a comment is about, in words: "the whole item" or "the title".
const aboutWords = (field) => (field ? `the ${FIELD_WORDS[field].toLowerCase()}` : 'the whole item');

// Who said it, as the person reads it: "You" for their own words.
const whoWords = (by, kind) => (isMe(by, kind) ? 'You' : actorLabel(by, kind, { card: false }));

// When, from the server's clock when the page has it, and as a moment otherwise.
const whenWords = (at, asOf) => (asOf ? `${waitedWords(at, asOf)} ago` : moment(at));

// A row under a line: its own gutter with a speech bubble, then the words. `depth` is the line's, so that the row sits under it.
function row(kind, body, { depth, closed, dataset }) {
  return h(
    'div',
    { class: `diff-annot ${kind}`, dataset: { depth: String(Math.min(depth + 1, 5)), closed, ...dataset } },
    h('span', { class: 'diff-sign', 'aria-hidden': 'true' }, icon('comment', { size: 14 })),
    h('div', { class: 'diff-code' }, body),
  );
}

// One comment of earlier feedback: the words it was about, what was said, and by whom, on which revision and when.
export function threadRow(thread, { depth, closed = null, asOf = null }) {
  const { comment } = thread;
  return row(
    'diff-thread',
    [
      h('p', { class: 'thread-about quiet' }, `About ${aboutWords(comment.field)}`),
      h('blockquote', { class: 'comment-quote', dataset: { words: 'theirs' } }, comment.quoted),
      h('p', { class: 'comment-body', dataset: { words: 'theirs' } }, comment.text),
      h('p', { class: 'thread-by quiet' }, whoWords(thread.by, thread.by_kind), `, on revision ${thread.revision}`, ' · ', h('time', { datetime: thread.at, title: moment(thread.at) }, whenWords(thread.at, asOf))),
    ],
    { depth, closed, dataset: { feedback: thread.feedback, comment: thread.index, ref: comment.item, field: comment.field } },
  );
}

// The box to write a comment under a line, or to change one that is not sent yet. It is a form under the line, not a dialog: the
// person keeps reading the plan while they write. It opens empty; what is typed is kept while the page is drawn again. An item that a
// revision took out can only be asked for back as a whole (`wholeOnly`).
function composerRow(ref, item, state, { plan, revision, depth, closed, hooks, wholeOnly = false }) {
  const problem = h('p', { class: 'problem comment-problem', role: 'alert' });
  const show = (words) => {
    state.problem = words;
    put(problem, words ? [icon('alert', { size: 16 }), h('span', { class: 'problem-text' }, words)] : null);
  };
  show(state.problem);
  const choose = h(
    'select',
    { class: 'comment-field', value: state.field, dataset: { focusKey: `field:${ref}` }, onchange: () => (state.field = choose.value) },
    h('option', { value: '' }, 'The whole item'),
    wholeOnly ? null : COMMENT_FIELDS.map(([name, words]) => h('option', { value: name }, words)),
  );
  const text = h('textarea', {
    class: 'comment-text',
    rows: 3,
    maxlength: MOST_COMMENT,
    autocomplete: 'off',
    'aria-label': `Your comment on ${codeOf(ref)}`,
    value: state.text,
    dataset: { focusKey: `composer:${ref}` },
    oninput: () => {
      state.text = text.value;
      state.caret = text.selectionStart;
      if (state.problem) show('');
    },
  });
  // A page that is drawn again while the person types puts the cursor back where it was.
  text.addEventListener('focus', () => {
    const at = Math.min(state.caret ?? text.value.length, text.value.length);
    text.setSelectionRange(at, at);
  });
  for (const type of ['keyup', 'click']) text.addEventListener(type, () => (state.caret = text.selectionStart));
  const stop = () => {
    closeComposer(state);
    hooks.redraw(`comment:${ref}`);
  };
  const form = h(
    'form',
    {
      class: 'comment-form',
      novalidate: true,
      dataset: { ref },
      onsubmit: (event) => {
        event.preventDefault();
        const words = state.text.trim();
        if (!words) {
          show('Write your comment, or cancel.');
          return;
        }
        const chosen = state.field || null;
        const quoted = quotedOf(item, chosen);
        if (state.editing) changePending(plan, revision, state.editing, { field: chosen, text: words, quoted });
        else addPending(plan, revision, { item: ref, field: chosen, text: words, quoted });
        closeComposer(state);
        hooks.redraw(`comment:${ref}`);
      },
      onkeydown: (event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          stop();
        } else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
          event.preventDefault();
          form.requestSubmit();
        }
      },
    },
    field(`About ${codeOf(ref)}`, choose),
    field('Your comment', text),
    problem,
    h('div', { class: 'comment-buttons' }, h('button', { type: 'submit', class: 'small primary comment-add' }, state.editing ? 'Save comment' : 'Add comment'), h('button', { type: 'button', class: 'small comment-cancel', onclick: stop }, 'Cancel')),
  );
  return row('diff-composer', form, { depth, closed, dataset: { ref } });
}

// A comment that is written and not sent: it says so in words, shows the words it is about as the server will keep them and what was
// said, and can be changed or taken away until the request for changes is sent.
function pendingRow(one, { plan, revision, depth, closed, hooks }) {
  const state = composerOf(plan, revision, one.item);
  return row(
    'diff-comment-pending',
    [
      h('p', { class: 'pending-state' }, h('strong', {}, 'Not sent yet'), h('span', { class: 'quiet' }, ` · about ${aboutWords(one.field)}`)),
      h('blockquote', { class: 'comment-quote', dataset: { words: 'theirs' } }, one.quoted),
      h('p', { class: 'comment-body', dataset: { words: 'theirs' } }, one.text),
      h(
        'div',
        { class: 'comment-buttons' },
        h(
          'button',
          {
            type: 'button',
            class: 'small comment-edit',
            'aria-label': `Edit your comment on ${codeOf(one.item)}`,
            dataset: { focusKey: `edit:${one.id}` },
            onclick: () => {
              Object.assign(state, { open: true, editing: one.id, field: one.field ?? '', text: one.text, caret: one.text.length, problem: '' });
              hooks.redraw(`composer:${one.item}`);
            },
          },
          'Edit',
        ),
        h(
          'button',
          {
            type: 'button',
            class: 'small comment-remove',
            'aria-label': `Remove your comment on ${codeOf(one.item)}`,
            dataset: { focusKey: `remove:${one.id}` },
            onclick: () => {
              removePending(plan, revision, one.id);
              hooks.redraw(`comment:${one.item}`);
            },
          },
          'Remove',
        ),
      ),
    ],
    { depth, closed, dataset: { comment: one.id, ref: one.item, field: one.field } },
  );
}

// What can be said on the lines of one revision of a plan. `items` are the items of that revision by reference (so that the words a
// comment is about can be worked out), `gone` the items the revision took out as they were (they can be agreed to go or asked for back),
// `blame` the server's account of who last changed each line and what a person said about it, `feedback` the earlier feedback to show
// under the lines it was about, and `editable` whether the person can approve and write comments: only for a plan that waits for a person,
// and only for someone who can edit the project. `onBlame(revision)` reads the plan as of a revision. `compact` is a card's preview: the
// approve control and the carried state only, no comments, no blame and nothing under a line.
// Returns `controls(item)` (the controls of the slot next to an item's line), `review(item)` (what is carried or waiting, at the end of
// its line), `below(item, closed, depth)` (the rows under it), `has(ref)` (whether anything is said on it) and `threads` (the earlier
// comments by item).
export function planNotes({ plan, revision, items, gone = [], blame = null, feedback, editable, asOf = null, hooks, onBlame = null, compact = false }) {
  const threads = threadsOf(feedback);
  const byRef = new Map(items.map((one) => [one.ref, one]));
  const goneRefs = new Set(gone.map((one) => one.ref));
  const reviews = reviewsOf(blame);
  const waiting = approvedOf(plan, revision);
  const spoken = (ref) => (threads.get(ref) ?? []).map((one) => (depth, closed) => threadRow(one, { depth, closed, asOf }));
  const below = (one, closed, depth) => {
    if (compact) return [];
    const rows = spoken(one.ref).map((make) => make(depth, closed));
    if (!editable) return rows;
    const state = composerOf(plan, revision, one.ref);
    for (const comment of pendingOf(plan, revision)) {
      if (comment.item === one.ref && !(state.open && state.editing === comment.id)) rows.push(pendingRow(comment, { plan, revision, depth, closed, hooks }));
    }
    if (state.open) rows.push(composerRow(one.ref, one, state, { plan, revision, depth, closed, hooks, wholeOnly: goneRefs.has(one.ref) }));
    return rows;
  };
  return {
    threads,
    below,
    controls(one) {
      const made = [];
      const known = reviews.get(one.ref);
      if (!compact && known && onBlame) made.push(blameChip(known, { onPress: () => onBlame(known.revision) }));
      if (editable) {
        made.push(
          approveButton({
            ref: one.ref,
            removed: goneRefs.has(one.ref),
            pressed: waiting.has(one.ref),
            blocked: hasPendingComment(plan, revision, one.ref),
            onToggle: () => {
              toggleApproved(plan, revision, one.ref);
              hooks.redraw(`approve:${one.ref}`);
            },
          }),
        );
        if (!compact) made.push(commentButton({ ref: one.ref, onOpen: () => open(one.ref) }));
      }
      return made;
    },
    review(one) {
      const shown = stateOf(one.ref, { reviews, approved: waiting });
      return shown ? reviewChip({ ref: one.ref, ...shown }) : null;
    },
    has: (ref) => byRef.has(ref) && (threads.has(ref) || (editable && (hasPendingComment(plan, revision, ref) || composerOf(plan, revision, ref).open))),
  };

  // Opens the box under a line, empty; a box that is already open (or a comment being changed) is left as it is.
  function open(ref) {
    const state = composerOf(plan, revision, ref);
    if (!state.open) Object.assign(state, { open: true, editing: null, field: '', text: '', caret: 0, problem: '' });
    hooks.redraw(`composer:${ref}`);
  }
}
