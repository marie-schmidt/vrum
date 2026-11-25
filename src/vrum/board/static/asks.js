// Asks (M45): a question an agent put to a person about a story or task, and how a person answers it. An ask is a question,
// optionally with the answers on offer (or a yes or no approval), and says whether the work waited for it or stayed free. It
// has one answer, given by a person, who is named, with a comment if they added one; neither is ever edited, so a later reader
// sees exactly what was asked and what was said.
//
// This is the one place an ask is drawn and answered: the page of what needs a person puts `askControls` in the row of an
// ask, and the item page's Questions section draws each ask with `askItem`, which uses the same controls, so an answer is
// given the same way and refused in the same words wherever it is given. The page never answers for a person and never
// picks an answer for them: nothing here is chosen until they press it. A person who can only look sees the question and
// the answers on offer as text, with no button.

import { Refusal, call } from './api.js';
import { can, notify } from './ctx.js';
import { announce, h, moment, plural, put } from './dom.js';
import { dialogForm, field, openDialog, refuse } from './forms.js';
import { hoverCard } from './hovercard.js';
import { askCode, actorLabel } from './labels.js';
import { sync } from './store.js';

// How long something has waited, in the largest whole unit: "3 days", "5 hours", "12 minutes". `asOf` is the server's own time for
// the answer that said when it began, never this browser's clock, so a clock that is wrong makes no difference.
export function waitedWords(since, asOf) {
  const ms = Date.parse(asOf) - Date.parse(since);
  if (!(ms >= 60000)) return 'less than a minute';
  const minutes = Math.floor(ms / 60000);
  if (minutes < 60) return plural(minutes, 'minute');
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? plural(hours, 'hour') : plural(Math.floor(hours / 24), 'day');
}

// What an ask did to the work, as the short words of a chip, and what each means as the chip's hover card says it. It waited for the
// answer (or still does), it was left free to take, or it is the approval a gate holds the work for.
const EFFECT_WORDS = { blocks: 'Blocks work', parks: 'Work stays free', gates: 'Gates work' };
export const effectHover = (ask) =>
  ask.effect === 'parks'
    ? 'The work was released and stays free to take.'
    : ask.effect === 'gates'
      ? 'A yes opens the gate. Any other answer leaves it refused.'
      : ask.answered
        ? 'The work waited for this answer.'
        : 'The work waits for this answer.';

// The chip that says it, with its meaning in a hover card (it opens on the keyboard's focus too).
export function effectChip(ask) {
  const chip = h('span', { class: 'chip ask-effect', tabindex: 0, dataset: { effect: ask.effect } }, EFFECT_WORDS[ask.effect] ?? ask.effect);
  return h('span', { class: 'chip-holder' }, chip, hoverCard(chip, h('p', { class: 'verdict-hover' }, effectHover(ask))));
}

// An answer as a button says it: yes and no are written with a capital, anything else as the agent offered it.
const choiceWords = (ask, choice) => (ask.kind === 'approval' ? choice.charAt(0).toUpperCase() + choice.slice(1) : choice);

// The answers an agent offered, as a quiet list, for a person who cannot answer and as a preview of the question.
export const offeredList = (ask) =>
  ask.choices ? h('ul', { class: 'plain ask-offered quiet', 'aria-label': 'Answers on offer' }, ask.choices.map((choice) => h('li', { dataset: { answer: choice, words: 'theirs' } }, choiceWords(ask, choice)))) : null;

// The dialog of a question with no choices: the answer in words and, if wished, a comment. It opens empty. `send` gives the
// answer and what was said beside it, and a refusal of the server stays in the dialog, in its words.
export function answerDialog(ask, send) {
  const answer = h('textarea', { id: 'ask-answer', rows: 4, autocomplete: 'off' });
  const comment = h('input', { id: 'ask-answer-comment', type: 'text', autocomplete: 'off' });
  const { form } = dialogForm({
    fields: [h('p', { class: 'ask-question', dataset: { words: 'theirs' } }, ask.question), field('Your answer', answer), field('Comment (optional)', comment)],
    submit: 'Answer',
    work: async () => {
      await send(answer.value, comment.value);
      box.close();
    },
  });
  const box = openDialog(`Answer ${askCode(ask.id)}`, [form]);
  box.id = 'answer-ask';
  return box;
}

// The way to answer an ask in place: its choices as buttons (an approval is Yes and No), or an Answer button for a question
// with none, and "Add a comment" to say more with the answer. Pressing a choice is the answer: nothing is picked for the person.
// `draft` is { open, text }, kept by whoever draws this so that a comment being typed survives the page being drawn again.
// `onAnswered(ask)` is called after the server took the answer; a refusal is said in its own words beside the buttons and nothing
// is changed. `onAsk()` is called when an answer is about to be sent and `onRefused()` when the server refused it, for a page that
// moves on to the next thing once an answer is taken. Null for a person who can only look.
function askControls(ask, { onAnswered = () => {}, onAsk = () => {}, onRefused = () => {}, draft = { open: false, text: '' } } = {}) {
  if (!can('write')) return null;
  const problem = h('p', { class: 'problem ask-problem', role: 'alert' });
  const buttons = [];
  const key = `ask:${ask.id}`;

  async function give(answer, comment) {
    const said = comment.trim();
    onAsk();
    let result;
    try {
      result = await call('answer_ask', { ask: ask.id, answer, ...(said ? { comment: said } : {}) });
    } catch (error) {
      if (error instanceof Refusal) onRefused();
      throw error;
    }
    notify(`Answered ${askCode(ask.id)} with ${result.answer}.`);
    announce(`Answered ${askCode(ask.id)} with ${result.answer}`);
    draft.open = false;
    draft.text = '';
    // The answer is taken whether or not the board can hear of it at once: it is heard at the next look.
    await sync().catch(() => {});
    onAnswered(result);
  }

  async function pick(answer, button) {
    problem.replaceChildren();
    buttons.forEach((one) => { one.disabled = true; });
    try {
      await give(answer, draft.text);
    } catch (error) {
      if (!(error instanceof Refusal)) throw error;
      refuse(problem, error.message);
      button.focus();
    } finally {
      buttons.forEach((one) => { one.disabled = false; });
    }
  }

  const first = h('div', { class: 'ask-choices' });
  if (ask.choices) {
    for (const choice of ask.choices) {
      const button = h('button', { type: 'button', class: 'small ask-choice', dataset: { answer: choice, focusKey: `${key}:${choice}` }, onclick: () => pick(choice, button) }, choiceWords(ask, choice));
      buttons.push(button);
      first.append(button);
    }
  } else {
    const button = h('button', { type: 'button', class: 'small ask-answer-open', dataset: { focusKey: `${key}:answer` }, onclick: () => answerDialog(ask, give) }, 'Answer…');
    first.append(button);
  }

  // A comment goes with the answer a choice makes. A question with no choices has its comment in its dialog.
  const comment = h('div', { class: 'ask-comment' });
  if (ask.choices) {
    const drawComment = () => {
      if (!draft.open) {
        put(comment, h('button', { type: 'button', class: 'small ask-comment-open', dataset: { focusKey: `${key}:comment-open` }, onclick: () => { draft.open = true; drawComment(); comment.querySelector('input')?.focus(); } }, 'Add a comment'));
        return;
      }
      const input = h('input', { type: 'text', class: 'ask-comment-field', autocomplete: 'off', 'aria-label': `Comment on ${askCode(ask.id)} (optional)`, value: draft.text, dataset: { focusKey: `${key}:comment` }, oninput: () => { draft.text = input.value; } });
      put(comment, input);
    };
    drawComment();
  }

  return h('div', { class: 'ask-controls', dataset: { ask: ask.id } }, first, comment, problem);
}

// One ask on the page of an item: its number and question in full, who asked and when and what it did to the work, then the
// answer (who gave it, when, the words and the comment, whole) or, while it is open, the way to answer it. `options` are the
// answer controls' (`onAnswered`, `draft`).
export function askItem(ask, options = {}) {
  const writes = can('write');
  const offered = !ask.answered && !writes ? offeredList(ask) : null;
  return h(
    'li',
    { class: `ask-item${ask.answered ? ' answered' : ' open'}`, dataset: { ask: ask.id, status: ask.answered ? 'answered' : 'open', effect: ask.effect, node: ask.node } },
    h('p', { class: 'ask-head' }, h('span', { class: 'item-code ask-code' }, askCode(ask.id)), h('span', { class: 'ask-question', dataset: { words: 'theirs' } }, ask.question)),
    h('p', { class: 'ask-by quiet' }, 'Asked by ', actorLabel(ask.asked_by, ask.asked_by_kind), `, ${moment(ask.asked_at)} `, effectChip(ask)),
    offered,
    ask.answered
      ? h(
          'div',
          { class: 'ask-answered' },
          h('p', { class: 'ask-answer' }, h('span', { class: 'ask-label' }, 'Answer: '), h('strong', { class: 'ask-answer-text' }, ask.answer)),
          ask.comment ? h('p', { class: 'ask-comment-text' }, h('span', { class: 'ask-label' }, 'Comment: '), ask.comment) : null,
          h('p', { class: 'ask-by quiet' }, 'Answered by ', actorLabel(ask.answered_by, 'person'), `, ${moment(ask.answered_at)}.`),
        )
      : askControls(ask, options),
  );
}
