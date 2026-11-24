// The change itself, on the review page, for every kind that is not a diff of lines (M52): a question in full with the answers on offer, the
// checks of the definition of done with the ones a person must tick open, each note whole, what a refused approval waits for and who refused
// it, and the claims that ran out. (A plan and a suggested move are diffs: needs-diffs.js, plan-diffs.js.) Everything is read only: the
// decision is the bar's, and a row of several notes or checks has a bar under each of them (decision-bar.js).

import { offeredList } from './asks.js';
import { itemLink } from './ctx.js';
import { checkBar, barsPerLine, noteBar, notesOf, openChecks } from './decision-bar.js';
import { day, h, moment } from './dom.js';
import { icon } from './icons.js';
import { actorLabel, checkChip, codeOf, noteCode, plainNoteStatus, ruleCode, gateCode, askCode } from './labels.js';
import { aboutLines, loading, problem } from './needs-diffs.js';
import { store } from './store.js';

const reading = () => [loading('Reading…')];

// A labelled fact: `dt` and `dd`, left out when there is nothing to say.
const fact = (label, value, theirs = false) => (value === null || value === undefined || value === false || value === '' ? null : h('div', { class: 'review-fact' }, h('dt', {}, label), h('dd', theirs ? { dataset: { words: 'theirs' } } : {}, value)));
const facts = (...rows) => {
  const shown = rows.filter(Boolean);
  return shown.length ? h('dl', { class: 'review-facts' }, shown) : null;
};

// -- a question ---------------------------------------------------------------------------------------------

function askBody(change) {
  const { ask } = change.item;
  return [h('div', { class: 'review-ask', dataset: { ask: ask.id } }, h('p', { class: 'review-question', dataset: { words: 'theirs' } }, ask.question), offeredList(ask))];
}

// -- the checks of the work -----------------------------------------------------------------------------------

// Every check of the definition of done: the ones only a person can tick and nobody has are open (and said so by their weight and their
// chip), the ones that are ticked say who reported them and how.
function checkBody(change, entry, hooks) {
  if (!entry || (entry.state === 'loading' && !entry.data)) return reading();
  if (!entry.data) return [problem(`The checks could not be read: ${entry.error}`)];
  const several = barsPerLine(change, entry);
  const open = new Set(openChecks(entry).map((one) => one.item));
  return [
    h(
      'ul',
      { class: 'plain review-checks', 'aria-label': 'Definition of done' },
      entry.data.checks.map((check) => {
        const waiting = open.has(check.item);
        return h(
          'li',
          { class: 'review-check', dataset: { item: check.item, checked: String(check.checked), waiting: waiting ? 'true' : null } },
          icon(check.checked ? 'req_met' : 'req_open', { size: 14 }),
          ' ',
          h('span', { class: waiting ? 'diff-check-words waiting' : 'diff-check-words', dataset: { words: 'theirs' } }, check.text),
          ' ',
          checkChip(check, { size: 13 }),
          check.checked ? h('span', { class: 'diff-evidence quiet' }, ' ', actorLabel(check.checked_by, check.checked_by_kind), check.evidence ? [': ', h('span', { dataset: { words: 'theirs' } }, check.evidence)] : null) : null,
          waiting && several ? checkBar(change, check, hooks) : null,
        );
      }),
    ),
  ];
}

// -- notes ---------------------------------------------------------------------------------------------------

// What a note concerns, as links: the work, the acceptance criteria and the other notes.
function concerns(note) {
  const links = [
    ...note.nodes.map((ref) => itemLink(ref, { class: 'item-link', dataset: { ref } }, h('span', { class: 'item-code' }, codeOf(ref)), ' ', h('span', { dataset: { words: 'theirs' } }, store.nodes.get(ref)?.title ?? ''))),
    ...note.rule_ids.map((id) => itemLink(`rule:${id}`, { class: 'item-link', dataset: { rule: id } }, h('span', { class: 'item-code' }, ruleCode(id)))),
    ...note.note_ids.map((id) => itemLink(`note:${id}`, { class: 'item-link', dataset: { note: id } }, h('span', { class: 'item-code' }, noteCode(id)))),
  ];
  return links.length ? h('ul', { class: 'plain review-concerns' }, links.map((one) => h('li', {}, one))) : null;
}

// Each note whole: its text, why it is so, who decided it (or that nobody did), who recorded it and when, what it concerns, and whether it
// applies throughout the project.
function noteBody(change, entry, hooks) {
  if (!entry || (entry.state === 'loading' && !entry.data)) return reading();
  if (!entry.data) return [problem(`The notes could not be read: ${entry.error}`)];
  const several = barsPerLine(change, entry);
  return notesOf(entry).map((note) =>
    h(
      'div',
      { class: 'change-note review-note', dataset: { note: note.id } },
      h('p', { class: 'note-head' }, itemLink(`note:${note.id}`, { class: 'item-link diff-note-link', dataset: { note: note.id } }, h('span', { class: 'item-code' }, noteCode(note.id))), ' ', plainNoteStatus(note.status), note.project_wide ? h('span', { class: 'chip', dataset: { projectWide: 'true' } }, 'Project-wide') : null),
      h('div', { class: 'note-text', dataset: { words: 'theirs' } }, note.text),
      facts(
        fact('Why', note.why, true),
        fact('Decided by', note.decided_by ? `${note.decided_by}${note.decided_on ? `, ${day(note.decided_on)}` : ''}` : 'Nobody'),
        fact('Recorded', [actorLabel(note.recorded_by, note.recorded_by_kind), `, ${moment(note.recorded_at)}`]),
        fact('Concerns', concerns(note)),
      ),
      several ? noteBar(change, note, hooks) : null,
    ),
  );
}

// -- a refused approval ----------------------------------------------------------------------------------------

// What the gate waits for (the server's words), who refused it and what they said, and who added the gate.
function gateBody(change, node) {
  const { gate } = change.item;
  if (!gate) return aboutLines(change.item);
  const ask = node && Array.isArray(node.asks) ? node.asks.find((one) => one.id === gate.ask) : null;
  return [
    h(
      'div',
      { class: 'review-gate', dataset: { gate: gate.id } },
      h('p', { class: 'needs-gate-words', dataset: { words: 'theirs' } }, h('span', { class: 'item-code gate-code' }, gateCode(gate.id)), ' ', gate.words),
      facts(
        fact('Question', gate.question, true),
        fact('Answer', gate.answer, true),
        fact('Refused by', gate.answered_by ? actorLabel(gate.answered_by, 'person') : null),
        fact('Comment', ask && ask.comment ? ask.comment : null, true),
        fact('Asked as', gate.ask === null || gate.ask === undefined ? null : itemLink(change.item.node, { class: 'item-link' }, askCode(gate.ask))),
        fact('Gate added', [actorLabel(gate.added_by, gate.added_by_kind), `, ${moment(gate.added_at)}`]),
      ),
    ),
  ];
}

// -- a claim that ran out ----------------------------------------------------------------------------------------

// The claims that ran out: each holder that lost it and when it ran out, the newest first, and the claim that is lapsed now.
function lapseBody(change, node, history) {
  const taken = history.filter((event) => event.action === 'claimed' && event.before).map((event) => ({ actor: event.before.actor, kind: event.before.actor_kind, ranOut: event.before.expires_at }));
  if (node && node.lapsed_claim_by) taken.push({ actor: node.lapsed_claim_by, kind: node.lapsed_claim_by_kind, ranOut: node.lapsed_claim_at });
  return [
    h(
      'ul',
      { class: 'plain review-claims', 'aria-label': 'Claims that ran out' },
      taken.reverse().map((one) => h('li', { class: 'review-claim', dataset: { actor: one.actor } }, actorLabel(one.actor, one.kind), h('span', { class: 'quiet' }, ` ran out ${moment(one.ranOut)}`))),
    ),
  ];
}

// The body of the change for a kind that is not a plan or a suggested move. `ctx`: `entry` (what was read), `node` (get_node of the work),
// `history` (its events, oldest first) and `hooks` (for the bars under several notes or checks).
export function otherBody(change, ctx) {
  switch (change.look) {
    case 'question':
    case 'approval':
      return askBody(change);
    case 'check':
      return checkBody(change, ctx.entry, ctx.hooks);
    case 'note':
      return noteBody(change, ctx.entry, ctx.hooks);
    case 'gate':
      return gateBody(change, ctx.node);
    default:
      return lapseBody(change, ctx.node, ctx.history);
  }
}
