// Where could it go? (M43): on the page of a story or task, a button that asks the server for the epics (for a story) or the stories (for a
// task) it is nearest to by meaning, best first, and shows the answer in place, under the item's links. Each suggestion has a score
// (0 to 1: compare the scores of one answer with each other), the items under it that the work most resembles (the reason), and, for
// a person who can write, Move here, which moves the item as the page already moves work. It is a suggestion and says so: the
// first one carries a Sure chip only when the server says the best is clearly ahead of the second, and even then it is a guess.
//
// Nothing is asked until the person presses the button, nothing moves until they press Move here, and the section closes and
// forgets when they leave the page. A server that cannot suggest (search by meaning is off, the service is down) says why in its
// own words and the section shows them. A viewer sees the suggestions and no Move button.

import { Refusal, call } from './api.js';
import { can, itemLink, notify } from './ctx.js';
import { h, plural, put } from './dom.js';
import { refuse } from './forms.js';
import { hoverCard } from './hovercard.js';
import { icon } from './icons.js';
import { codeOf, kindIcon } from './labels.js';
import { sync, titleOf } from './store.js';

const ORDINAL = ['th', 'st', 'nd', 'rd'];
// 1st, 2nd, 3rd, 4th, 11th.
function ordinal(number) {
  const rest = number % 100;
  return `${number}${ORDINAL[rest >= 11 && rest <= 13 ? 0 : number % 10 < 4 ? number % 10 : 0]}`;
}

const link = (ref, title) => itemLink(ref, { class: 'item-link', dataset: { ref } }, kindIcon(ref.split(':')[0], 14), h('span', { class: 'item-code' }, codeOf(ref)), ' ', title);

// What the Sure chip means, in its hover card.
const SURE_WORDS = 'It is clearly ahead of the next one: the best of the model’s guesses. Still a guess, so look at the reason.';

function sureChip() {
  const chip = h('span', { class: 'chip ok sure-chip', tabindex: 0 }, 'Sure');
  return [chip, hoverCard(chip, h('p', {}, SURE_WORDS))];
}

// The reason: the items under the parent that the work most resembles, each with how alike it is.
function because(suggestion) {
  if (!suggestion.because.length) return null;
  return h(
    'p',
    { class: 'suggestion-because quiet' },
    'Because it is like ',
    suggestion.because.map((one, at) => [at ? ', ' : '', link(one.ref, one.title), ` (${one.similarity.toFixed(2)})`]),
    '.',
  );
}

function suggestionItem(node, answer, suggestion, first, state) {
  const mine = state.suggest;
  const problem = h('p', { class: 'problem suggestion-problem', role: 'alert' });
  if (mine.refused?.ref === suggestion.ref) refuse(problem, mine.refused.message);
  const move = can('write')
    ? h('button', {
        type: 'button',
        class: 'small suggestion-move',
        dataset: { ref: suggestion.ref },
        'aria-label': `Move ${codeOf(node.ref)} under ${codeOf(suggestion.ref)} ${suggestion.title}`,
        onclick: async (event) => {
          const button = event.currentTarget;
          button.disabled = true;
          problem.replaceChildren();
          mine.refused = null;
          try {
            await call('move_node', { node: node.ref, expected_version: state.version, new_parent: suggestion.ref });
            notify(`Moved “${node.title}” under “${suggestion.title}”.`);
            state.suggest = null;
            await sync().catch(() => {});
            await state.reload(true);
          } catch (error) {
            if (!(error instanceof Refusal)) throw error;
            mine.refused = { ref: suggestion.ref, message: error.message };
            refuse(problem, error.message);
            button.disabled = false;
          }
        },
      }, 'Move here')
    : null;
  return h(
    'li',
    { class: 'suggestion', dataset: { ref: suggestion.ref, score: String(suggestion.score) } },
    h('p', { class: 'suggestion-head' }, link(suggestion.ref, suggestion.title), first && answer.sure ? sureChip() : null),
    h(
      'div',
      { class: 'score suggestion-score' },
      h('strong', {}, suggestion.score.toFixed(2)),
      h('span', { class: 'meter', role: 'meter', 'aria-label': `How near ${codeOf(suggestion.ref)} is`, 'aria-valuemin': 0, 'aria-valuemax': 1, 'aria-valuenow': suggestion.score }, h('span', { css: { width: `${Math.round(suggestion.score * 100)}%` } })),
    ),
    suggestion.holds ? h('p', { class: 'quiet suggestion-holds' }, `Holds ${plural(suggestion.holds, 'item')} already.`) : null,
    because(suggestion),
    move,
    problem,
  );
}

// Where it is now: its parent and, when the server could rank it, where that parent comes among the choices.
function nowWords(answer) {
  if (!answer.current_parent) return null;
  const rank = answer.current_rank ? ` It ranks ${ordinal(answer.current_rank)} of the choices.` : '';
  return h('p', { class: 'suggest-now quiet' }, 'Where it is now: ', link(answer.current_parent, titleOf(answer.current_parent)), `.${rank}`);
}

// The answer, drawn under the button.
function answerView(node, answer, state) {
  const kind = answer.parent_kind === 'epic' ? 'epic' : 'story';
  return [
    nowWords(answer),
    answer.suggestions.length
      ? h('ol', { class: 'plain suggestions', 'aria-label': `Where ${codeOf(node.ref)} could go: ${plural(answer.suggestions.length, kind)}, best first` }, answer.suggestions.map((one, at) => suggestionItem(node, answer, one, at === 0, state)))
      : h('p', { class: 'quiet suggest-none' }, `There is no ${kind} to suggest.`),
    answer.not_considered ? h('p', { class: 'quiet suggest-skipped' }, `${plural(answer.not_considered, kind)} ${answer.not_considered === 1 ? 'is' : 'are'} not indexed yet and ${answer.not_considered === 1 ? 'was' : 'were'} not ranked.`) : null,
  ];
}

// The section of a story's or task's page. `state.suggest` is what it knows between drawings of the card (open, the answer, a refusal);
// it is forgotten when the person leaves the page, which is when the card's state goes.
export function suggestSection(node, state) {
  const mine = (state.suggest ??= { open: false, loading: false, answer: null, problem: null });
  const body = h('div', { class: 'suggest-body', id: 'item-suggest-body', 'aria-live': 'polite' });
  const toggle = h(
    'button',
    {
      type: 'button',
      id: 'item-suggest-parent',
      class: 'small suggest-parent',
      'aria-expanded': String(mine.open),
      'aria-controls': body.id,
      dataset: { focusKey: 'suggest-parent' },
      onclick: () => (mine.open ? close() : open()),
    },
    icon('move', { size: 14 }),
    'Where could it go?',
  );
  const draw = () => {
    toggle.setAttribute('aria-expanded', String(mine.open));
    body.hidden = !mine.open;
    if (!mine.open) {
      put(body);
      return;
    }
    put(
      body,
      mine.loading ? h('p', { class: 'loading', role: 'status' }, 'Looking…') : null,
      mine.problem ? h('p', { class: 'notice refusal suggest-problem', role: 'alert' }, icon('alert', { size: 16 }), h('span', {}, mine.problem)) : null,
      mine.answer ? answerView(node, mine.answer, state) : null,
    );
  };
  mine.draw = draw;

  async function open() {
    state.suggest = mine;
    mine.open = true;
    mine.loading = true;
    mine.answer = null;
    mine.problem = null;
    mine.draw();
    try {
      const answer = await call('suggest_parent', { node: node.ref });
      if (state.suggest === mine) mine.answer = answer;
    } catch (error) {
      if (!(error instanceof Refusal)) throw error;
      if (state.suggest === mine) mine.problem = error.message;
    }
    if (state.suggest !== mine) return;
    mine.loading = false;
    mine.draw();
  }

  function close() {
    state.suggest = null;
    mine.open = false;
    mine.draw();
  }

  draw();
  return h('section', { class: 'part suggest', dataset: { part: 'suggest' } }, toggle, body);
}
