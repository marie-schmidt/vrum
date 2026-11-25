// What a person has done in the review of a plan and not sent (M51, M53), kept in the page's memory and never in the browser's storage, so
// that it lives until the board is reloaded: the comments written on the lines of a plan, the items approved as they are (M53), the
// words typed into a comment that is not added yet, which way a plan is being read (what changed, or the whole plan) and the last
// refusal of a decision. A comment or an approval names the revision it was made on, because the server keeps it with that revision and
// refuses a request for another: when a newer revision arrives, the unsent ones of the old one are not kept and the plan says so once.
// An item cannot be both approved and commented on in one request (the server refuses it), so the page never holds both.
//
// Nothing here draws anything or asks the server for anything except `changesDialog`, which sends
// pending review comments with a request for changes.

import { Refusal, call } from './api.js';
import { h } from './dom.js';
import { dialogForm, field, openDialog } from './forms.js';
import { codeOf } from './labels.js';

// The parts of an item a comment can be about, and what the person calls each. The first is the whole item (no field).
export const COMMENT_FIELDS = [
  ['title', 'Title'],
  ['body', 'Details'],
  ['size', 'Size'],
  ['priority', 'Priority'],
  ['start_on', 'Start day'],
  ['target_on', 'Due day'],
  ['parent', 'Where it hangs'],
  ['links', 'Links'],
  ['criteria', 'Acceptance criteria'],
];
export const FIELD_WORDS = Object.fromEntries(COMMENT_FIELDS);

// The words a comment is about as the server will keep them: the item's title for a comment on the whole item, the text of the part
// otherwise. These are the server's own rules, so the unsent comment shows what the sent one will say.
export function quotedOf(item, field) {
  if (!field) return String(item.title);
  if (field === 'links') return item.links.map((one) => `${one.link_type} ${one.target}`).join('; ') || 'no links';
  if (field === 'criteria') return item.criteria.map((one) => one.title).join('; ') || 'no acceptance criteria';
  const value = item[field];
  return value === null || value === undefined || value === '' ? 'none' : String(value);
}

const pending = new Map(); // `${plan}:${revision}` -> [{ id, item, field, text, quoted }], in the order they were added
const approvals = new Map(); // `${plan}:${revision}` -> the references of the items approved as they are and not sent
const composers = new Map(); // `${plan}:${revision}:${item}` -> { open, field, text, editing, problem }
const lost = new Map(); // plan -> { from, to, comments: [{ item, field, text }] }
const seen = new Map(); // plan -> the newest revision the page has drawn
const problems = new Map(); // plan -> { words, rereading }: the server's words for the last decision it refused
let serial = 0;

const keyOf = (plan, revision) => `${plan}:${revision}`;

// -- the comments that are not sent -----------------------------------------------------------------------

export const pendingOf = (plan, revision) => {
  const key = keyOf(plan, revision);
  if (!pending.has(key)) pending.set(key, []);
  return pending.get(key);
};

export function addPending(plan, revision, { item, field, text, quoted }) {
  serial += 1;
  pendingOf(plan, revision).push({ id: serial, item, field: field || null, text, quoted });
  // A comment asks for a change: it takes the item's approval back.
  approvedOf(plan, revision).delete(item);
  return serial;
}

// -- the approvals that are not sent -------------------------------------------------------------------------

export const approvedOf = (plan, revision) => {
  const key = keyOf(plan, revision);
  if (!approvals.has(key)) approvals.set(key, new Set());
  return approvals.get(key);
};

// Whether an item cannot be approved now: it has a comment that is not sent.
export const hasPendingComment = (plan, revision, item) => pendingOf(plan, revision).some((one) => one.item === item);

// Approves an item as it is, or takes that back. An item with a comment waiting is not approved (returns false and changes nothing).
export function toggleApproved(plan, revision, item) {
  const set = approvedOf(plan, revision);
  if (set.has(item)) {
    set.delete(item);
    return false;
  }
  if (hasPendingComment(plan, revision, item)) return false;
  set.add(item);
  return true;
}

export function changePending(plan, revision, id, { field, text, quoted }) {
  const found = pendingOf(plan, revision).find((one) => one.id === id);
  if (found) Object.assign(found, { field: field || null, text, quoted });
}

export function removePending(plan, revision, id) {
  const list = pendingOf(plan, revision);
  const at = list.findIndex((one) => one.id === id);
  if (at >= 0) list.splice(at, 1);
}

// Sent: what was written or approved for any revision of the plan is gone, and so is what was being typed.
export function clearPending(plan) {
  for (const key of [...pending.keys()]) if (key.startsWith(`${plan}:`)) pending.delete(key);
  for (const key of [...approvals.keys()]) if (key.startsWith(`${plan}:`)) approvals.delete(key);
  for (const key of [...composers.keys()]) if (key.startsWith(`${plan}:`)) composers.delete(key);
}

// A comment being written or changed under a line: its words and whether it is open are kept while the page is drawn again.
export function composerOf(plan, revision, item) {
  const key = `${keyOf(plan, revision)}:${item}`;
  if (!composers.has(key)) composers.set(key, { open: false, field: '', text: '', editing: null, problem: '' });
  return composers.get(key);
}

export const closeComposer = (state) => Object.assign(state, { open: false, field: '', text: '', editing: null, problem: '' });

// -- a revision that arrives under the person -------------------------------------------------------------

// Called with the revision the page is about to draw for a plan. When it is newer than the one drawn before and comments or approvals
// were made for an older one, they are not kept (the server would refuse them: they name a revision); what they said is kept in a
// notice so that they can be written again.
export function noticeRevision(plan, revision) {
  const before = seen.get(plan);
  seen.set(plan, revision);
  if (before === undefined || revision <= before) return;
  // The last refusal was about the card that is replaced, unless the page is reading the plan again because of it.
  if (problems.has(plan) && !problems.get(plan).rereading) problems.delete(plan);
  const older = ([key]) => key.startsWith(`${plan}:`) && Number(key.split(':')[1]) < revision;
  const old = [...pending.entries()].filter(older);
  const oldApproved = [...approvals.entries()].filter(older);
  const comments = old.flatMap(([, list]) => list.map(({ item, field, text }) => ({ item, field, text })));
  const approved = oldApproved.flatMap(([, set]) => [...set]);
  for (const [key] of old) pending.delete(key);
  for (const [key] of oldApproved) approvals.delete(key);
  for (const key of [...composers.keys()]) if (key.startsWith(`${plan}:`) && Number(key.split(':')[1]) < revision) composers.delete(key);
  if (comments.length || approved.length) {
    const from = [...old.filter(([, list]) => list.length), ...oldApproved.filter(([, set]) => set.size)].map(([key]) => Number(key.split(':')[1]));
    lost.set(plan, { from: Math.max(...from), to: revision, comments, approved });
  }
}

export const lostOf = (plan) => lost.get(plan) ?? null;
export const dismissLost = (plan) => lost.delete(plan);
// The notices of lost comments are said once: leaving the page ends them.
export const forgetLost = () => lost.clear();

// -- which of the feedback sections the person has open ---------------------------------------------------------

const feedbackOpen = new Map(); // feedback number -> whether the person has it open
export const isFeedbackOpen = (id, byDefault) => (feedbackOpen.has(id) ? feedbackOpen.get(id) : byDefault);
export const rememberFeedbackOpen = (id, open) => feedbackOpen.set(id, open);

// -- what the last decision was told ------------------------------------------------------------------------

export const problemOf = (plan) => problems.get(plan)?.words ?? '';
export function setProblem(plan, words) {
  if (words) problems.set(plan, { words, rereading: false });
  else problems.delete(plan);
}

// A refusal is followed at once by a read of the plan: a revision that comes with that read is what the words are about (the person
// pressed a button of one that had moved on), so they stay beside the buttons. A revision that comes later, with the news, is a new
// card, and the words are not about it. `settleProblem` ends the read.
export function explainProblem(plan) {
  const one = problems.get(plan);
  if (one) one.rereading = true;
}
export function settleProblem(plan) {
  const one = problems.get(plan);
  if (one) one.rereading = false;
}

// -- what was said before, by line ----------------------------------------------------------------------------

// The comments of earlier feedback, by the item each is about, oldest first: [{ feedback, index, revision, by, by_kind, at, comment }].
export function threadsOf(feedback) {
  const found = new Map();
  for (const one of feedback) {
    one.comments.forEach((comment, index) => {
      if (!found.has(comment.item)) found.set(comment.item, []);
      found.get(comment.item).push({ feedback: one.id, index, revision: one.revision, by: one.by, by_kind: one.by_kind, at: one.at, comment });
    });
  }
  return found;
}

// -- request changes dialog -----------------------------------------------------------------------------------

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

export function changesDialog({ plan, code, revision, comments, approved = [], onSent, onRefused }) {
  const summary = h('textarea', { id: 'changes-summary', rows: 4, autocomplete: 'off' });
  const { form } = dialogForm({
    fields: [field('What should change', summary), approvedList(approved), commentsList(comments)],
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
