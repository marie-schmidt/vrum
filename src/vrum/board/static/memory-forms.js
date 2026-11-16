// The forms of the Memory page: record a decision, a fact or a belief, replace one with a newer one,
// retract, confirm, promote a belief to a decision, change what something concerns, and check what
// is being written against every belief when a language model is set up. Each form sends one
// operation of an agent, with the person's typed name, and shows the server's answer in its own
// words. Nothing is edited: a change is a new record that replaces the old one.

import { call } from './api.js';
import { actor, notify } from './ctx.js';
import { h, plural } from './dom.js';
import { clip, dialogForm, evidencePicker, field, openDialog, picker } from './forms.js';
import { store, sync } from './store.js';

const KIND_OPTIONS = [
  ['decision', 'Decision: something decided about this work'],
  ['principle', 'Principle: a standing guideline that applies everywhere'],
];

const select = (name, options, value) =>
  h('select', { name, value }, options.map(([option, label]) => h('option', { value: option }, label)));
const text = (name, value = '') => h('input', { type: 'text', name, value, autocomplete: 'off' });
const area = (name, rows, value = '') => h('textarea', { name, rows, value });
const confirmedBeliefs = (items) => items.filter((item) => item.type === 'belief' && item.confirmed_by);
const refsOf = (value) => [
  ...(value.node_ids || []).map((id) => ({ type: 'node', id })),
  ...(value.rule_ids || []).map((id) => ({ type: 'rule', id })),
  ...(value.decision_ids || []).map((id) => ({ type: 'decision', id })),
];
const evidenceRefs = (value) => [
  ...(value.evidence_fact_ids || []).map((id) => ({ type: 'fact', id })),
  ...(value.evidence_decision_ids || []).map((id) => ({ type: 'decision', id })),
];

// -- recording a decision, or replacing one --------------------------------------------------------

export function recordDecision({ replacing = null } = {}) {
  const kind = select('kind', KIND_OPTIONS, replacing?.kind || 'decision');
  const title = text('title', replacing?.title || '');
  const body = area('text', 4, replacing?.text || '');
  const reason = area('reason', 3);
  const by = text('decided_by', actor());
  const on = h('input', { type: 'date', name: 'decided_on' });
  const links = picker({ label: replacing ? 'It also affects' : 'It affects', kinds: ['node', 'rule'] });
  const keep = replacing ? h('input', { type: 'checkbox', name: 'keep_links', checked: true }) : null;
  const { form } = dialogForm({
    fields: [
      replacing ? h('p', { class: 'quiet' }, `This records a new decision that replaces decision #${replacing.id}. The old one is kept, marked superseded, and points to the new one. Nothing is edited.`) : null,
      field('Kind', kind),
      field('Title', title),
      field('What is decided', body),
      field(replacing ? 'Why it changed' : 'Why', reason),
      h('div', { class: 'row' }, field('Decided by (the person)', by, 'Your own name is recorded separately, as the one who typed it.'), field('Decided on', on, 'Leave empty for today.')),
      links.element,
      keep ? h('label', { class: 'check' }, keep, ' The new decision affects what the old one did') : null,
    ],
    submit: replacing ? 'Replace the decision' : 'Record the decision',
    work: async () => {
      const args = { actor: actor(), kind: kind.value, title: title.value, text: body.value, reason: reason.value, decided_by: by.value, ...(on.value ? { decided_on: on.value } : {}), ...links.value() };
      const made = replacing
        ? await call('supersede_decision', { ...args, decision_id: replacing.id, keep_links: keep.checked })
        : await call('record_decision', args);
      box.close();
      notify(replacing ? `Replaced decision #${replacing.id} with #${made.id}.` : `Recorded ${made.kind} #${made.id}, “${made.title}”.`);
      await sync();
    },
  });
  const box = openDialog(replacing ? `Replace decision #${replacing.id}` : 'Record a decision', [form], { wide: true });
  title.focus();
}

// -- recording a fact or a belief, or replacing some -----------------------------------------------

function replacingBlock(items, type) {
  return h(
    'div',
    { class: 'replacing' },
    h('p', {}, h('strong', {}, `Replacing ${plural(items.length, type)}.`), ' They are kept, marked superseded, and point to the new one. Nothing is edited.'),
    h('ul', { class: 'plain' }, items.map((item) => h('li', { dataset: { replacing: item.id } }, `#${item.id}: ${clip(item.text, 120)}`))),
  );
}

// A person who confirmed a belief has to be named, and what they said, to change it.
function saidBy(items) {
  const by = text('said_by');
  const quote = area('quote', 2);
  const needed = confirmedBeliefs(items).length > 0;
  return {
    element: h(
      'details',
      { class: 'said-by', open: needed },
      h('summary', {}, needed ? 'A person confirmed a belief here: who changed their mind, and what they said' : 'Needed only to change a belief a person has confirmed'),
      field('Who changed their mind', by),
      field('What they said, or where', quote),
    ),
    value: () => ({ ...(by.value.trim() ? { said_by: by.value } : {}), ...(quote.value.trim() ? { quote: quote.value } : {}) }),
  };
}

// `replacing`: the facts, or the beliefs, that the new one replaces. `draft`: what to start from
// when it is not the old text (a belief written in another form). `fromCheck`: this form was
// opened from a check's proposal, so it does not offer a check of its own. `onRecorded`: called
// once the new one is recorded. When a language model is set up, the form offers to check what is
// being written against every belief.
export function recordMemory(type, { replacing = [], draft = null, fromCheck = false, onRecorded = null, onChange = null } = {}) {
  const belief = type === 'belief';
  const first = replacing[0];
  const body = area('text', 4, draft?.text ?? (replacing.length === 1 ? first.text : ''));
  const source = text('source', !belief && replacing.length === 1 ? first.source || '' : '');
  const kind = select('kind', [['world', 'World: true about the project, its tools or its domain'], ['experience', 'Experience: something done or observed']], !belief && first ? first.kind || 'world' : 'world');
  const happened = h('input', { type: 'date', name: 'happened_on' });
  const stillValid = replacing.flatMap((item) => (item.evidence || []).filter((entry) => entry.status === 'active').map((entry) => ({ type: entry.type, id: entry.id })));
  const evidence = belief ? evidencePicker({ chosen: draft?.evidence ?? stillValid }) : null;
  const links = picker({ label: replacing.length ? 'It also concerns' : 'It concerns', kinds: belief ? ['node', 'rule'] : ['node', 'rule', 'decision'], chosen: draft?.concerns || [] });
  const reason = replacing.length ? area('reason', 3) : null;
  const keep = replacing.length ? h('input', { type: 'checkbox', name: 'keep_links', checked: true }) : null;
  const said = replacing.length && belief ? saidBy(replacing) : null;
  const checking = store.info.language_model && !fromCheck
    ? checkPanel(type, () => body.value, () => ({ text: body.value, evidence: evidenceRefs(evidence ? evidence.value() : {}), concerns: refsOf(links.value()) }), { onReplaced: belief ? () => box.close() : null })
    : null;
  const evidenceArgs = () => {
    const value = evidence.value();
    return { evidence_fact_ids: value.evidence_fact_ids, evidence_decision_ids: value.evidence_decision_ids };
  };
  const { form } = dialogForm({
    fields: [
      replacing.length ? replacingBlock(replacing, type) : null,
      field(belief ? 'The conclusion, in plain words' : 'What is known, in plain words', body),
      belief ? evidence.element : h('div', { class: 'row' }, field('Where it came from', source, 'Who said it, which document, what was observed. Never blank.'), field('Kind', kind)),
      belief ? null : field('The day it was so', happened, 'Leave empty when it is not known.'),
      links.element,
      replacing.length ? field('Why they are being replaced', reason) : null,
      keep ? h('label', { class: 'check' }, keep, ' The new one concerns what the old ones did') : null,
      said ? said.element : null,
      checking ? checking.element : null,
    ],
    submit: replacing.length ? `Replace with this ${type}` : `Record the ${type}`,
    work: async () => {
      const common = { actor: actor(), text: body.value, ...links.value() };
      let made;
      let change = null;
      if (replacing.length) {
        change = await call('supersede_memory', {
          ...common,
          replaces: replacing.map((item) => item.id),
          reason: reason.value,
          keep_links: keep.checked,
          ...(belief ? { ...evidenceArgs(), ...said.value() } : { source: source.value, kind: kind.value, ...(happened.value ? { happened_on: happened.value } : {}) }),
        });
        made = change.replacement;
        notify(`Recorded ${type} #${made.id}, which replaces ${replacing.map((item) => `#${item.id}`).join(', ')}.`);
      } else if (belief) {
        made = await call('record_belief', { ...common, ...evidenceArgs() });
        notify(`Recorded belief #${made.id}. It is a suggestion, not a decision.`);
      } else {
        made = await call('retain_fact', { ...common, source: source.value, kind: kind.value, ...(happened.value ? { happened_on: happened.value } : {}) });
        notify(`Recorded fact #${made.id}.`);
      }
      box.close();
      onRecorded?.();
      await sync();
      if (change) onChange?.(change);
    },
  });
  const heading = replacing.length ? `Replace ${plural(replacing.length, type)} with a new ${type}` : `Record a ${type}`;
  const box = openDialog(heading, [form], { wide: true });
  body.focus();
  return box;
}

// -- retracting ------------------------------------------------------------------------------------

export function retract(items, { onDone = () => {} } = {}) {
  const reason = area('reason', 3);
  const facts = items.filter((item) => item.type === 'fact');
  const dependents = facts.length ? h('input', { type: 'checkbox', name: 'with_dependent_beliefs' }) : null;
  const said = saidBy(items);
  const { form } = dialogForm({
    fields: [
      h('p', {}, h('strong', {}, `Retract ${plural(items.length, items[0].type)}: they are wrong.`), ' Nothing is deleted. They stay as a record, marked retracted, with this reason.'),
      h('ul', { class: 'plain' }, items.map((item) => h('li', { dataset: { retracting: item.id } }, `#${item.id}: ${clip(item.text, 120)}`))),
      field('Why they are wrong', reason),
      dependents ? h('label', { class: 'check' }, dependents, ' Also retract every belief that rests on a fact being retracted (otherwise those beliefs are only shown as stale)') : null,
      said.element,
    ],
    submit: 'Retract',
    work: async () => {
      const change = await call('retract_memory', {
        actor: actor(),
        memory_ids: items.map((item) => item.id),
        reason: reason.value,
        with_dependent_beliefs: dependents ? dependents.checked : false,
        ...said.value(),
      });
      box.close();
      notify(`Retracted ${items.map((item) => `#${item.id}`).join(', ')}.`);
      await sync();
      onDone(change, items);
    },
  });
  const box = openDialog(`Retract ${plural(items.length, items[0].type)}`, [form], { wide: true });
  reason.focus();
  return box;
}

// -- confirming and promoting a belief -------------------------------------------------------------

export function confirmBelief(belief) {
  const by = text('confirmed_by', actor());
  const { form } = dialogForm({
    fields: [
      h('p', {}, h('strong', {}, `Belief #${belief.id}: ${clip(belief.text, 140)}`)),
      h('p', { class: 'quiet' }, 'Confirming says that a named person has looked at this belief. It stays a suggestion: it is not a decision, and it changes nothing in the graph.'),
      field('The person who looked at it', by),
    ],
    submit: 'Confirm',
    work: async () => {
      await call('confirm_belief', { actor: actor(), memory_id: belief.id, confirmed_by: by.value });
      box.close();
      notify(`Belief #${belief.id} is confirmed by ${by.value}.`);
      await sync();
    },
  });
  const box = openDialog(`Confirm belief #${belief.id}`, [form]);
  by.focus();
}

export function promoteBelief(belief) {
  const kind = select('kind', KIND_OPTIONS, 'decision');
  const title = text('title', clip(belief.text, 80));
  const body = area('text', 4, belief.text);
  const reason = area('reason', 3);
  const by = text('decided_by', actor());
  const on = h('input', { type: 'date', name: 'decided_on' });
  const { form } = dialogForm({
    fields: [
      h('p', { class: 'quiet' }, `A person makes belief #${belief.id} a decision. The decision concerns what the belief concerned and keeps a link back to it; the belief itself is not changed.`),
      field('Kind', kind),
      field('Title', title),
      field('What is decided', body),
      field('Why the person decided this', reason),
      h('div', { class: 'row' }, field('Decided by (the person)', by), field('Decided on', on, 'Leave empty for today.')),
    ],
    submit: 'Make it a decision',
    work: async () => {
      const made = await call('promote_belief', { actor: actor(), memory_id: belief.id, kind: kind.value, title: title.value, text: body.value, reason: reason.value, decided_by: by.value, ...(on.value ? { decided_on: on.value } : {}) });
      box.close();
      notify(`Belief #${belief.id} is now ${made.kind} #${made.id}, decided by ${made.decided_by}.`);
      await sync();
    },
  });
  const box = openDialog(`Make belief #${belief.id} a decision`, [form], { wide: true });
  title.focus();
}

// -- what something concerns -----------------------------------------------------------------------

// `type` is decision, fact or belief; the dialog stays open so that several links can be changed.
export function changeConcerns(type, id) {
  const decision = type === 'decision';
  const table = () => (decision ? store.decisions : store.memories) || [];
  const current = () => table().find((entry) => entry.id === id);
  const tools = decision ? { add: 'link_decision', remove: 'unlink_decision', key: 'decision_id' } : { add: 'link_memory', remove: 'unlink_memory', key: 'memory_id' };
  const kinds = type === 'fact' ? ['node', 'rule', 'decision'] : ['node', 'rule'];
  const links = picker({ label: 'Add what it concerns', kinds });
  const shown = h('ul', { class: 'plain concerns-list' });
  const problem = h('p', { class: 'problem', role: 'alert' });
  const draw = () => {
    const entry = current();
    const refs = entry ? refsOf({ node_ids: entry.node_ids, rule_ids: entry.rule_ids, decision_ids: entry.decision_ids }) : [];
    shown.replaceChildren(
      ...(refs.length
        ? refs.map((ref) => {
            const name = ref.type === 'node' ? `#${ref.id} ${store.nodes.get(ref.id)?.title || ''}` : `${ref.type} ${ref.id}`;
            return h('li', { dataset: { ref: `${ref.type}:${ref.id}` } }, `${name} `, h('button', { type: 'button', class: 'unlink', onclick: () => change(tools.remove, { [`${ref.type}_ids`]: [ref.id] }) }, 'Take out'));
          })
        : [h('li', { class: 'quiet' }, 'It concerns nothing yet.')]),
    );
  };
  const change = async (tool, ids) => {
    problem.textContent = '';
    try {
      await call(tool, { actor: actor(), [tools.key]: id, ...ids });
      notify(tool === tools.add ? 'Added what it concerns.' : 'Took it out.');
      await sync();
      draw();
    } catch (error) {
      problem.textContent = error.message;
    }
  };
  const add = h('button', { type: 'button', class: 'primary', onclick: () => change(tools.add, links.value()) }, 'Add');
  draw();
  const box = openDialog(`What ${type} #${id} concerns`, [
    h('p', { class: 'quiet' }, 'Recall finds it for these items. Only exact links count: nothing is inferred.'),
    shown,
    links.element,
    problem,
    h('div', { class: 'buttons' }, add, h('button', { type: 'button', onclick: () => box.close() }, 'Done')),
  ]);
  box.querySelector('input[type=text]')?.focus();
}

// -- checking against every belief -----------------------------------------------------------------

// Asks the language model, a batch of beliefs at a time, which beliefs a statement contradicts. It
// shows how far the check has got, and then each proposal with the words that clash. Nothing is
// changed by a proposal: each has a button to replace the belief or to retract it, and those open
// the same forms as everywhere else.
function checkPanel(type, statement, draft, { onReplaced }) {
  const progress = h('progress', { max: 1, value: 0, hidden: true });
  const status = h('p', { class: 'check-progress', role: 'status' });
  const proposals = h('ul', { class: 'plain proposals' });
  const problem = h('p', { class: 'problem', role: 'alert' });
  const button = h(
    'button',
    {
      type: 'button',
      class: 'check-beliefs',
      onclick: async () => {
        problem.textContent = '';
        proposals.replaceChildren();
        button.disabled = true;
        progress.hidden = false;
        try {
          await runCheck(statement(), { progress, status, proposals, draft, onReplaced });
        } catch (error) {
          problem.textContent = error.message;
          status.textContent = '';
        } finally {
          button.disabled = false;
        }
      },
    },
    'Check it against every belief',
  );
  return {
    element: h(
      'div',
      { class: 'check-panel', dataset: { type } },
      h('p', { class: 'quiet' }, `A language model (${store.info.language_model}) reads each belief in turn and proposes the ones this ${type} contradicts. It only proposes: you decide.`),
      button,
      h('div', { class: 'check-status' }, status, progress),
      problem,
      proposals,
    ),
  };
}

async function runCheck(statement, { progress, status, proposals, draft, onReplaced }) {
  let after = null;
  let found = 0;
  let total = 0;
  do {
    const result = await call('find_contradictions', { actor: actor(), statement, scope: 'all', batch: 25, after_id: after });
    total = result.active_beliefs;
    progress.max = Math.max(total, 1);
    progress.value = result.looked_at;
    status.textContent = `Looked at ${result.looked_at} of ${plural(total, 'belief')}…`;
    for (const proposal of result.proposals) {
      found += 1;
      proposals.append(proposalItem(proposal, draft, onReplaced));
    }
    after = result.next_after_id;
  } while (after !== null && after !== undefined);
  progress.value = progress.max;
  status.textContent = `Looked at ${total} of ${plural(total, 'belief')}. ${found ? `${plural(found, 'proposal')}.` : 'None of them clashes with it.'}`;
}

function proposalItem(proposal, draft, onReplaced) {
  const belief = (store.memories || []).find((entry) => entry.id === proposal.belief_id);
  const done = h('span', { class: 'done-note', role: 'status' });
  const li = h(
    'li',
    { class: 'proposal', dataset: { belief: proposal.belief_id } },
    h('strong', {}, `Belief #${proposal.belief_id}`),
    proposal.stale ? h('span', { class: 'chip stale' }, '! Stale') : null,
    proposal.confirmed_by ? h('span', { class: 'chip confirmed' }, `✓ Confirmed by ${proposal.confirmed_by}`) : null,
    h('p', { class: 'entry-text' }, proposal.belief_text),
    h('p', { class: 'quote' }, h('span', { class: 'quiet' }, 'The belief says: '), `“${proposal.belief_quote}”`),
    h('p', { class: 'quote' }, h('span', { class: 'quiet' }, 'What you are writing says: '), `“${proposal.statement_quote}”`),
    h('p', {}, h('span', { class: 'quiet' }, 'Why they clash: '), proposal.reason),
    belief
      ? h(
          'div',
          { class: 'buttons' },
          h('button', { type: 'button', class: 'replace-belief', onclick: () => recordMemory('belief', { replacing: [belief], draft: draft(), fromCheck: true, onRecorded: onReplaced }) }, 'Replace it…'),
          h('button', { type: 'button', class: 'retract-belief', onclick: () => retract([belief], { onDone: () => { done.textContent = 'Retracted.'; } }) }, 'Retract it…'),
          done,
        )
      : h('p', { class: 'quiet' }, 'This belief is no longer in the list. Close this and look again.'),
  );
  return li;
}
