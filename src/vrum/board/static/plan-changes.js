// The dialog that asks for changes to a plan (M51): what should change, in the person's words, and, read only, the comments on lines that
// were written and not sent yet, which go with it. It opens empty. Asking is not rejecting: the plan stays held, nothing is dropped
// or freed, and the agent sends a revision to read again. The request names the revision the person reviewed; the server refuses it, in
// its words, when the plan has moved on, and the words stay in the dialog with everything typed. Nothing is sent until the person presses
// the button of the dialog; cancelling changes nothing and keeps the comments.

import { Refusal, call } from './api.js';
import { dialogForm, field, openDialog } from './forms.js';
import { h } from './dom.js';
import { codeOf } from './labels.js';
import { FIELD_WORDS } from './plan-comments.js';

// The comments as the server takes them: the item, the part when it is about one, and the words.
const sendable = (comments) => comments.map(({ item, field: part, text }) => ({ item, ...(part ? { field: part } : {}), text }));

function commentsList(comments) {
  if (!comments.length) return h('p', { class: 'changes-no-comments quiet' }, 'No comments');
  return h(
    'div',
    { class: 'changes-comments-box' },
    h('p', { class: 'quiet' }, `Comments: ${comments.length}`),
    h(
      'ul',
      { class: 'plain changes-comments' },
      comments.map((one) =>
        h(
          'li',
          { dataset: { comment: one.id, ref: one.item, field: one.field } },
          h('strong', { class: 'changes-comment-item' }, codeOf(one.item)),
          h('span', { class: 'quiet' }, ` · ${one.field ? `the ${FIELD_WORDS[one.field].toLowerCase()}` : 'the whole item'}`),
          h('blockquote', { class: 'comment-quote', dataset: { words: 'theirs' } }, one.quoted),
          h('p', { class: 'comment-body', dataset: { words: 'theirs' } }, one.text),
        ),
      ),
    ),
  );
}

// The items approved as they are and not sent yet, as a labelled list: their codes and titles. Nothing is said about them: the label and the list
// are all (an item that was taken out and is agreed to go is in the list too).
function approvedList(approved) {
  if (!approved.length) return null;
  return h(
    'div',
    { class: 'changes-approved-box' },
    h('p', { class: 'quiet' }, `Approved: ${approved.length}`),
    h(
      'ul',
      { class: 'plain changes-approved' },
      approved.map((one) => h('li', { dataset: { ref: one.ref } }, h('strong', { class: 'changes-approved-item' }, codeOf(one.ref)), ' ', h('span', { dataset: { words: 'theirs' } }, one.title))),
    ),
  );
}

// `comments` are the pending ones of the revision and `approved` the items approved as they are (`{ ref, title }`), `onSent` runs once the
// server said yes, and `onRefused` when it did not (the page reads again at once, so that a person who read an old revision sees the new one).
export function changesDialog({ plan, code, revision, comments, approved = [], onSent, onRefused }) {
  const summary = h('textarea', { id: 'changes-summary', rows: 4, autocomplete: 'off' });
  const { form } = dialogForm({
    fields: [
      field('What should change', summary),
      approvedList(approved),
      commentsList(comments),
    ],
    submit: 'Ask for changes',
    work: async () => {
      try {
        await call('request_plan_changes', { plan, revision, summary: summary.value, comments: sendable(comments), ...(approved.length ? { approved: approved.map((one) => one.ref) } : {}) });
      } catch (error) {
        if (error instanceof Refusal) onRefused();
        throw error;
      }
      box.close();
      onSent();
    },
  });
  const box = openDialog(`Request changes to ${code}`, [form]);
  box.id = 'changes-dialog';
  summary.focus();
  return box;
}
