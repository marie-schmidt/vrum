// Gates (M47): work that waits for something outside the graph. A gate holds a story or task until a time comes (a timer) or until
// a person says yes (an approval, which is a question on the Needs a person page); a drafted plan holds all of its work until a
// person approves the plan. Held work is never ready: nothing hands it out and nothing can claim it. The server says what holds an
// item (`held_by`: `gate:5`, `plan:3`) and each gate in words (`gates[].words`); this page only draws those words and the
// controls a person may use: Add a gate on a story or task that is not finished, and Lift on a gate that is still in the way. Lifting
// is final and only a person can do it; nothing here opens, lifts or approves anything on its own.
//
// This file is also where a hold is drawn wherever work is: `holdChip` is the one small clock on the item's page, and
// `heldContent` is what the card on the Board adds to its waiting chip's hover card.

import { Refusal, call } from './api.js';
import { can, notify, viewLink } from './ctx.js';
import { dateField } from './datepicker.js';
import { h, moment } from './dom.js';
import { chooseOption, dialogForm, field, openDialog } from './forms.js';
import { hoverCard } from './hovercard.js';
import { icon } from './icons.js';
import { actorLabel, askCode, codeOf, gateCode, planCode } from './labels.js';
import { countTitle, sectionWith } from './records.js';
import { sync } from './store.js';

// -- what holds an item ---------------------------------------------------------------------------------

const HOLD = /^(gate|plan):([1-9][0-9]*)$/;

// The holds an item has, as the server sends them: `gate:5` and `plan:3`. None for an item that is free.
export const heldBy = (node) => (Array.isArray(node?.held_by) ? node.held_by : []);

// "Gate 5" or "Plan 3", from a hold's reference.
function holdCode(ref) {
  const found = HOLD.exec(String(ref));
  if (!found) return String(ref);
  return found[1] === 'gate' ? gateCode(found[2]) : planCode(found[2]);
}

// A hold as its code, and a plan's as a link to the plan's review page, where its revisions and feedback can be read.
function holdLink(ref) {
  const found = HOLD.exec(String(ref));
  if (!found || found[1] !== 'plan') return holdCode(ref);
  return viewLink('review', { change: `plan:${found[2]}` }, { class: 'plan-link', dataset: { plan: found[2] } }, holdCode(ref));
}

// What an item is held for, each hold on a line with its words: a gate's are the server's own (read from the item when the card
// opens, since the board's copy of the work only knows the hold's name), a plan's say that it waits for approval. `gates` are the
// item's gates when the page already has them. `load(update)` reads them, once, and places the card again.
export function heldContent(node, gates = null) {
  const known = new Map((gates || []).map((gate) => [`gate:${gate.id}`, gate]));
  const words = (ref) => (known.has(ref) ? known.get(ref).words : ref.startsWith('plan:') ? 'Waits for a person to approve the plan.' : '');
  const list = h(
    'ul',
    { class: 'plain hovercard-list holds-list' },
    heldBy(node).map((ref) => h('li', { dataset: { hold: ref } }, h('strong', { class: 'hold-code' }, holdLink(ref)), ' ', h('span', { class: 'hold-words' }, words(ref)))),
  );
  async function load(update) {
    if (known.size || !heldBy(node).some((ref) => ref.startsWith('gate:'))) return;
    try {
      const found = await call('get_node', { node: node.ref });
      for (const gate of found.gates || []) known.set(`gate:${gate.id}`, gate);
    } catch (error) {
      if (!(error instanceof Refusal)) throw error;
      return;
    }
    for (const line of list.children) line.querySelector('.hold-words').textContent = words(line.dataset.hold);
    update();
  }
  return { content: [h('p', { class: 'hovercard-title' }, 'Held'), list], load };
}

// The words that name a hold for a screen reader: "Held: Gate 5, Plan 3".
export const heldWords = (node) => `Held: ${heldBy(node).map(holdCode).join(', ')}`;

// The one small chip on the item's page for work that is held, with the card that says what holds it. Null for work that is free.
export function holdChip(node, gates = null) {
  const holds = heldBy(node);
  if (!holds.length) return null;
  const chip = h('span', { class: 'chip waiting held-chip', role: 'img', tabindex: 0, 'aria-label': heldWords(node), dataset: { held: holds.join(' ') } }, icon('clock', { size: 13 }));
  const held = heldContent(node, gates);
  const card = hoverCard(chip, held.content, { onOpen: ({ update }) => held.load(update) });
  return [chip, card];
}

// -- lifting a gate ---------------------------------------------------------------------------------------

// The dialog that lifts a gate, with an optional reason that is kept with it. It opens empty. A refusal of the server stays in the
// dialog in its words; `done` runs after the server said yes and the board has heard of it.
export function liftDialog(gate, done = async () => {}) {
  const reason = h('textarea', { id: 'lift-reason', rows: 3, autocomplete: 'off' });
  const { form } = dialogForm({
    fields: [field('Reason (optional)', reason)],
    submit: 'Lift the gate',
    work: async () => {
      await call('remove_gate', { gate: gate.id, ...(reason.value.trim() ? { reason: reason.value } : {}) });
      box.close();
      notify(`Lifted ${gateCode(gate.id)}.`);
      await sync().catch(() => {});
      await done();
    },
  });
  const box = openDialog(`Lift ${gateCode(gate.id)}`, [form]);
  box.id = 'lift-gate';
  reason.focus();
  return box;
}

// -- adding a gate ----------------------------------------------------------------------------------------

// The dialog that holds a story or task: until a time (a day, and the time of day if wished, UTC) or until a person approves (the
// question they are asked). It opens empty with nothing chosen. The server checks the time and the question and says what is wrong
// in its own words, in the dialog; nothing is checked here.
function addDialog(node, done) {
  const kind = h('select', { id: 'gate-kind' }, chooseOption('Choose what it waits for'), h('option', { value: 'timer' }, 'Wait until a time'), h('option', { value: 'approval' }, 'Wait for a person to approve'));
  const dayInput = h('input', { id: 'gate-day' });
  const timeInput = h('input', { id: 'gate-time', type: 'text', autocomplete: 'off', spellcheck: false });
  const question = h('textarea', { id: 'gate-question', rows: 3, autocomplete: 'off' });
  const timerFields = h('div', { class: 'gate-fields', id: 'gate-timer-fields', hidden: true }, field('Day', dateField(dayInput, 'the day it opens'), 'UTC. It opens at the start of that day unless you give a time.'), field('Time of day (optional)', timeInput, 'Hours and minutes, such as 09:30. UTC.'));
  const approvalFields = h('div', { class: 'gate-fields', id: 'gate-approval-fields', hidden: true }, field('What the person is asked to approve', question, 'It appears on the Needs a person page. A person says yes to open the gate.'));
  const show = () => {
    timerFields.hidden = kind.value !== 'timer';
    approvalFields.hidden = kind.value !== 'approval';
  };
  kind.addEventListener('change', show);
  const { form } = dialogForm({
    fields: [h('p', {}, `Hold “${node.title}” until something outside the work happens. Nothing can take it while it is held.`), field('What it waits for', kind), timerFields, approvalFields],
    submit: 'Add the gate',
    work: async () => {
      if (!kind.value) throw new Refusal('Choose what it waits for.');
      const args = { node: node.ref, kind: kind.value };
      if (kind.value === 'timer') args.not_before = [dayInput.value.trim(), timeInput.value.trim()].filter(Boolean).join('T');
      else args.question = question.value;
      const gate = await call('add_gate', args);
      box.close();
      notify(`Added ${gateCode(gate.id)} to “${node.title}”.`);
      await sync().catch(() => {});
      await done();
    },
  });
  const box = openDialog(`Add a gate to ${codeOf(node.ref)}`, [form]);
  box.id = 'add-gate-dialog';
  return box;
}

// -- the section of the item's page -------------------------------------------------------------------------

// A timer opens by itself, and nothing is said when it does, so the page reads the item again soon after the time comes. If the server's
// clock has not got there yet the gate is still waiting, and the page looks again in a few seconds. The timer is forgotten when the card is
// drawn again, and nothing is read once the person has left the page.
function reopenWhenDue(gates, state) {
  clearTimeout(state.gateTimer);
  const waits = gates.filter((gate) => gate.kind === 'timer' && gate.state === 'waiting' && gate.not_before).map((gate) => Date.parse(gate.not_before) - Date.now());
  if (!waits.length) return;
  const next = Math.min(...waits);
  if (!Number.isFinite(next) || next > 2 ** 31 - 2000) return;
  state.gateTimer = setTimeout(() => {
    if (!state.dead) state.reload(false).catch(() => {});
  }, next > 0 ? next + 1000 : 5000);
}

// One gate: its number, what it waits for and where it stands (the server's words), who added it and when, and, while it is still in
// the way (waiting, or refused and not yet lifted), Lift for a person who can write. A gate that is open or lifted has no control.
function gateItem(gate, node, state) {
  const inTheWay = gate.state === 'waiting' || gate.state === 'refused';
  return h(
    'li',
    { class: `gate-item ${gate.state}`, dataset: { gate: gate.id, state: gate.state, kind: gate.kind } },
    h('p', { class: 'gate-head' }, h('span', { class: 'item-code gate-code' }, gateCode(gate.id)), h('span', { class: 'gate-words' }, gate.words)),
    h('p', { class: 'gate-by quiet' }, 'Added by ', actorLabel(gate.added_by, gate.added_by_kind), `, ${moment(gate.added_at)}.`, gate.ask === null ? null : ` Asked as ${askCode(gate.ask)}.`),
    inTheWay && can('write')
      ? h('div', { class: 'gate-actions' }, h('button', { type: 'button', class: 'small lift-gate', 'aria-label': `Lift ${gateCode(gate.id)}`, dataset: { focusKey: `lift:${gate.id}` }, onclick: () => liftDialog(gate, () => state.reload(true)) }, 'Lift…'))
      : null,
  );
}

// The Gates section of a story's or task's page: each gate on it (the ones still in the way first), with a count, and Add a gate for a
// person who can write on work that is not finished. With no gate and no way to add one there is no section.
export function gatesSection(node, state) {
  const gates = Array.isArray(node.gates) ? node.gates : [];
  reopenWhenDue(gates, state);
  const live = node.kind !== 'epic' && node.status !== 'done' && node.status !== 'dropped';
  const add = can('write') && live ? h('button', { type: 'button', id: 'add-gate', class: 'small add-gate', dataset: { focusKey: 'add-gate' }, onclick: () => addDialog(node, () => state.reload(true)) }, icon('plus', { size: 14 }), 'Add a gate…') : null;
  if (!gates.length && !add) return null;
  return sectionWith(
    countTitle('Gates', gates.length),
    'gates',
    add,
    gates.length ? h('ul', { class: 'plain gates', 'aria-label': `Gates on ${codeOf(node.ref)} ${node.title}` }, gates.map((gate) => gateItem(gate, node, state))) : null,
  );
}
