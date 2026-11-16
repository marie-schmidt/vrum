// Ask: a box that puts a question to the memory through `reflect` and shows the answer in words,
// labelled as generated and never stored, with the items it relied on as links. A person can then
// record a conclusion as a belief that rests on those items (the facts and decisions among them:
// a belief cannot rest on another belief), under their own typed name. Without a language model
// the box says so and does nothing.

import { Refusal, call } from './api.js';
import { actor, notify } from './ctx.js';
import { clear, h, plural } from './dom.js';
import { KIND } from './labels.js';
import { renderMarkdown } from './markdown.js';
import { hrefFor, referenceKindOf } from './references.js';
import { store, sync } from './store.js';

export function askBox() {
  const answer = h('div', { id: 'ask-answer', 'aria-live': 'polite' });
  if (!store.info.language_model) {
    return h(
      'section',
      { class: 'ask', id: 'ask' },
      h('h2', {}, 'Ask'),
      h('p', { class: 'quiet', id: 'ask-none' }, 'No language model is set up on this server, so there is nobody to answer a question. Search above still works, and so does recall. A server is given a model with LLM_BASE_URL and LLM_MODEL (see the README).'),
    );
  }
  const question = h('textarea', { id: 'ask-question', rows: 2, 'aria-describedby': 'ask-help', placeholder: 'A question about what the project knows and has decided' });
  const send = h('button', { type: 'submit', class: 'primary', id: 'ask-send' }, 'Ask');
  const form = h(
    'form',
    {
      class: 'ask-form',
      onsubmit: async (event) => {
        event.preventDefault();
        send.disabled = true;
        clear(answer).append(h('p', { class: 'quiet' }, 'Asking…'));
        try {
          const result = await call('reflect', { actor: actor(), question: question.value });
          clear(answer).append(...show(result));
        } catch (error) {
          if (!(error instanceof Refusal)) throw error;
          clear(answer).append(h('p', { class: 'problem', role: 'alert' }, error.message));
        } finally {
          send.disabled = false;
        }
      },
    },
    h('label', { for: 'ask-question' }, 'Your question'),
    question,
    h('div', { class: 'buttons' }, send),
  );
  return h(
    'section',
    { class: 'ask', id: 'ask' },
    h('h2', {}, 'Ask'),
    h('p', { class: 'quiet', id: 'ask-help' }, `The language model (${store.info.language_model}) answers from what the project has recorded, and only from that. Its answer is generated, is not stored, and is not a record until you record it yourself.`),
    form,
    answer,
  );
}

const hrefOf = (item) => hrefFor(referenceKindOf(item.item_type), item.id);

// What came back: the answer (or that the memory does not answer), what it relied on, and the offer.
function show(result) {
  const parts = [];
  parts.push(
    h(
      'div',
      { class: 'answer', id: 'answer', dataset: { answered: String(result.answered) } },
      h('p', { class: 'chips' }, h('span', { class: 'chip generated' }, 'Generated, not a record'), h('span', { class: 'chip model' }, result.model)),
      result.answered ? renderMarkdown(result.answer) : h('p', {}, 'The memory does not answer this question.'),
      h('p', { class: 'quiet' }, result.message),
    ),
  );
  if (result.search_note) parts.push(h('p', { class: 'quiet' }, result.search_note));
  if (result.relied_on.length) {
    parts.push(
      h(
        'div',
        { class: 'relied-on', id: 'relied-on' },
        h('h3', {}, `It relied on ${plural(result.relied_on.length, 'item')}`),
        h(
          'ul',
          { class: 'plain' },
          result.relied_on.map((item) =>
            h(
              'li',
              { dataset: { relied: `${item.item_type}:${item.id}` } },
              h('a', { class: 'relied-link', href: hrefOf(item) }, `${KIND[item.item_type]?.label || item.item_type} #${item.id}: ${item.title}`),
              ' ',
              item.suggestion ? h('span', { class: 'chip suggestion' }, 'a suggestion') : null,
            ),
          ),
        ),
      ),
    );
  }
  if (result.answered) parts.push(recordOffer(result));
  return parts;
}

// "Record this conclusion as a belief": the facts and decisions it relied on are its evidence.
function recordOffer(result) {
  const evidence = result.relied_on.filter((item) => item.item_type !== 'belief');
  const left = result.relied_on.length - evidence.length;
  if (!evidence.length) {
    return h('p', { class: 'quiet', id: 'record-none' }, 'It relied only on beliefs, and a belief cannot rest on another belief, so there is nothing to record this conclusion on. Record the facts it came from first.');
  }
  const text = h('textarea', { id: 'belief-text', rows: 3, value: result.answer });
  const boxes = evidence.map((item) => ({ item, box: h('input', { type: 'checkbox', checked: true, 'aria-label': `${item.item_type} #${item.id} is evidence` }) }));
  const problem = h('p', { class: 'problem', role: 'alert' });
  const done = h('p', { class: 'done-note', role: 'status' });
  const send = h('button', { type: 'submit', id: 'record-belief-from-answer' }, 'Record it as a belief');
  return h(
    'form',
    {
      class: 'record-offer',
      id: 'record-offer',
      onsubmit: async (event) => {
        event.preventDefault();
        problem.textContent = '';
        const ticked = boxes.filter(({ box }) => box.checked).map(({ item }) => item);
        send.disabled = true;
        try {
          const made = await call('record_belief', {
            actor: actor(),
            text: text.value,
            evidence_fact_ids: ticked.filter((item) => item.item_type === 'fact').map((item) => item.id),
            evidence_decision_ids: ticked.filter((item) => item.item_type === 'decision' || item.item_type === 'principle').map((item) => item.id),
          });
          done.replaceChildren('Recorded as ', h('a', { href: `#/memory?entry=belief:${made.id}`, id: 'recorded-belief' }, `belief #${made.id}`), '. It is a suggestion, not a decision.');
          notify(`Recorded belief #${made.id}.`);
          await sync();
        } catch (error) {
          if (!(error instanceof Refusal)) throw error;
          problem.textContent = error.message;
        } finally {
          send.disabled = false;
        }
      },
    },
    h('h3', {}, 'Record this conclusion as a belief'),
    h('p', { class: 'quiet' }, 'Edit the words if you like. The belief will rest on the facts and decisions ticked below, and will be recorded under your name as a suggestion.'),
    h('label', { for: 'belief-text' }, 'The belief'),
    text,
    h('fieldset', {}, h('legend', {}, 'Evidence'), boxes.map(({ item, box }) => h('label', { class: 'check' }, box, ` ${KIND[item.item_type]?.label || item.item_type} #${item.id}: ${item.title}`))),
    left ? h('p', { class: 'quiet' }, `${plural(left, 'belief')} it relied on cannot be evidence and is left out.`) : null,
    problem,
    h('div', { class: 'buttons' }, send),
    done,
  );
}
