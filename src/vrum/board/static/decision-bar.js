// The one bar every change on the page of what needs a person and on the review page ends with (M52), in one line and the same place for
// every kind: only the verbs that do something for that kind are drawn, and a person who can only look gets none.
//
//   plan                  Approve, Request changes…, Reject…           (approve: the person decides)
//   suggested move        Approve, Reject…
//   unconfirmed notes     Approve (confirm), Reject… (withdraw)          one bar for each note
//   check only a person   Approve (tick, and how it was checked)         can tick                          one bar for each check
//   question              its choices, or Approve and Reject… for a yes or no, or Answer…, and a comment   (answer)
//   refused approval      Lift the gate…, Drop the work…               (look into)
//   claim that ran out    Block it…, Drop it…
//
// What a verb does, whether it can be undone and what it leaves alone is its hover card, which opens on the keyboard's focus as well,
// so no sentence is drawn on the page. Every decision is the server's: the bar sends the person's choice (a plan's names the revision
// they were shown), shows the server's refusal in its words beside the bar and goes on only when the server said yes. No key decides.
//
// A question can also be kept as a decision: the answer is recorded as a note on the work, naming the person, after the answer stood.

import { Refusal, call } from './api.js';
import { answerDialog, effectHover } from './asks.js';
import { account, can, notify } from './ctx.js';
import { announce, h, plural } from './dom.js';
import { dialogForm, field, openDialog, refuse } from './forms.js';
import { liftDialog } from './gates.js';
import { hoverCard } from './hovercard.js';
import { icon } from './icons.js';
import { askCode, codeOf, gateCode, noteCode, parseNodeRef } from './labels.js';
import { approvedOf, changesDialog, clearPending, pendingOf, problemOf, setProblem } from './plan-comments.js';
import { forgetPicks, leftOut } from './placement-picks.js';
import { sync } from './store.js';

// The classes the older selectors know the verbs by.
const OLD_CLASSES = { approve: 'needs-yes', reject: 'needs-no', changes: 'needs-changes', lift: 'needs-lift' };
const VERB_WORDS = { approve: 'Approve', reject: 'Reject', changes: 'Request changes to', lift: 'Lift', drop: 'Drop', block: 'Block', answer: 'Answer' };

// -- dialogs: a heading and labelled fields ----------------------------------------------------------------

// A dialog that asks for a reason and sends it. It opens empty (or with `prefill`). The server's refusal (no reason, or a decision
// someone else made first) stays in the dialog, in its words.
function reasonDialog({ id, heading, label, submit, prefill = '', send }) {
  const reason = h('textarea', { id: `${id.replace('-dialog', '')}-reason`, rows: 4, autocomplete: 'off' });
  reason.value = prefill;
  const { form } = dialogForm({
    fields: [field(label, reason)],
    submit,
    work: async () => {
      await send(reason.value);
      box.close();
    },
  });
  const box = openDialog(heading, [form]);
  box.id = id;
  reason.focus();
  return box;
}

// -- one verb -----------------------------------------------------------------------------------------------

// The bar's own machinery: the buttons, the place the server's words go when it refuses, and how a verb runs. A verb that runs at once
// (`run`) is guarded: the buttons wait while the server answers, a refusal is shown in its words beside the bar and nothing else
// happens. A verb that opens a dialog (`open`) leaves the refusal to the dialog, which stays open with what was typed.
function makeBar({ kind, barKind, hooks, remember = null, onRefused = () => {}, disabled = false, label = null }) {
  const problem = h('p', { class: 'problem decision-problem needs-problem', role: 'alert' });
  if (remember && remember.read()) refuse(problem, remember.read());
  const buttons = [];
  const off = new Set(); // verbs that cannot be pressed now, whatever the bar is doing
  const lock = (on) => buttons.forEach((button) => { button.disabled = on || disabled || off.has(button); });
  const failed = (error) => {
    if (!(error instanceof Refusal)) throw error;
    hooks.refused();
    remember?.write(error.message);
    refuse(problem, error.message);
    onRefused();
  };

  // `spec`: verb (data-verb), label, code (for the label of the button), words (what the button says), hover (its hover card), `tone`
  // ('approve' green, 'danger' red-tinted, or null neutral), and either run() or open().
  function verb(spec) {
    const { name, words, code, hover, tone = null, answer = null, run = null, open = null, ariaLabel = null, extra = '', off: cannot = false } = spec;
    const toneClass = tone === 'approve' ? 'verdict-approve' : tone === 'danger' ? 'danger' : '';
    const button = h(
      'button',
      {
        type: 'button',
        class: `small verdict ${toneClass} ${OLD_CLASSES[name] ?? ''} ${extra}`.replace(/\s+/g, ' ').trim(),
        'aria-label': ariaLabel ?? `${VERB_WORDS[name]} ${code}`,
        dataset: { verb: name, answer, focusKey: `${code}:${name}${answer ? `:${answer}` : ''}` },
        onclick: async () => {
          if (open) {
            hooks.pressed();
            open();
            return;
          }
          problem.replaceChildren();
          remember?.write('');
          lock(true);
          hooks.pressed();
          try {
            await run();
            hooks.decided();
          } catch (error) {
            failed(error);
          } finally {
            lock(false);
          }
        },
      },
      words,
    );
    button.disabled = disabled || cannot;
    buttons.push(button);
    if (cannot) off.add(button);
    // A disabled button hears no pointer: its card belongs to the holder around it, which does.
    const holder = h('span', { class: 'verdict-holder' }, button);
    holder.append(hoverCard(disabled || cannot ? holder : button, h('p', { class: 'verdict-hover' }, hover), { closeOnPress: true }));
    return holder;
  }

  const dialogRefused = () => {
    hooks.refused();
    onRefused();
  };
  return { problem, verb, buttons, lock, failed, dialogRefused, kind, barKind, label };
}

// The bar and, after it, the place for the server's words. One element, so the page puts it in one place.
function wrap(bar, children, { state = null, comment = null } = {}) {
  return h(
    'div',
    { class: 'decision', dataset: { kind: bar.kind } },
    h('div', { class: 'decision-bar', dataset: { bar: bar.barKind, kind: bar.kind } }, children, state, comment ? comment.button : null),
    bar.problem,
    comment ? comment.box : null,
  );
}

// -- the comment on one line --------------------------------------------------------------------------------

// A comment goes with an answer (and "Keep as a decision" with it) or, on a check, says how it was checked. It is a labelled button
// beside the decision verbs; the box opens under (or above) the bar and keeps what was typed while the page is drawn again (`draft`: { open, text, remember }).
function commentControl({ code, draft, label, text: buttonText = 'Comment', keep = false, hooks }) {
  const toggle = h(
    'button',
    {
      type: 'button',
      class: 'small ghost decision-comment',
      'aria-label': label,
      'aria-expanded': String(draft.open),
      dataset: { focusKey: `${code}:comment`, hasText: draft.text.trim() ? 'true' : null },
      onclick: () => {
        draft.open = !draft.open;
        hooks.redraw(draft.open ? `${code}:comment-text` : `${code}:comment`);
      },
    },
    icon('comment', { size: 14 }),
    h('span', {}, buttonText),
  );
  const hovered = h('span', { class: 'verdict-holder' }, toggle, hoverCard(toggle, h('p', { class: 'verdict-hover' }, label), { closeOnPress: true }));
  let box = null;
  if (draft.open) {
    const text = h('textarea', {
      class: 'decision-comment-text',
      rows: 3,
      autocomplete: 'off',
      'aria-label': label,
      dataset: { focusKey: `${code}:comment-text` },
      oninput: () => {
        draft.text = text.value;
      },
    });
    text.value = draft.text;
    const remember = keep
      ? h(
          'label',
          { class: 'decision-keep', for: 'answer-remember' },
          h('input', {
            type: 'checkbox',
            id: 'answer-remember',
            checked: draft.remember,
            onchange: (event) => {
              draft.remember = event.currentTarget.checked;
            },
          }),
          'Keep as a decision',
        )
      : null;
    box = h(
      'div',
      {
        class: 'decision-comment-box',
        onkeydown: (event) => {
          if (event.key !== 'Escape') return;
          event.preventDefault();
          event.stopPropagation();
          draft.open = false;
          hooks.redraw(`${code}:comment`);
        },
      },
      text,
      remember,
    );
  }
  return { button: hovered, box };
}

// -- a plan -------------------------------------------------------------------------------------------------

const allOf = (count, noun) => (count === 1 ? `the ${noun}` : `all ${count} ${noun}s`);

function planBar(change, hooks, entry) {
  const { plan } = change.item;
  const total = plan.epics + plan.stories + plan.tasks;
  const code = change.code;
  const revision = plan.revision;
  // A card that still shows an older revision than its row (the plan has just been revised and is being read again) cannot be decided
  // until it shows what it is about.
  const reading = Boolean(entry && entry.data && entry.data.revision !== revision);
  const mine = pendingOf(plan.id, revision);
  const approvedNow = approvedOf(plan.id, revision);
  // The words of the items approved, for the dialog's list: what the plan holds and what the revision took out.
  const titles = new Map([...(entry?.data?.previous ?? []), ...(entry?.data?.tree ?? [])].map((one) => [one.ref, one.title]));
  const remember = { read: () => problemOf(plan.id), write: (said) => setProblem(plan.id, said) };
  const bar = makeBar({ kind: 'plan', barKind: 'approve', hooks, remember, onRefused: hooks.reread, disabled: reading });
  const them = total === 1 ? 'it' : 'each one';
  const approve = bar.verb({
    name: 'approve',
    words: 'Approve',
    code,
    tone: 'approve',
    hover: `Frees ${allOf(total, 'item')} for agents to take. Nothing is dropped. It cannot be undone.`,
    run: async () => {
      await call('approve_plan', { plan: plan.id, approved: true, revision });
      clearPending(plan.id);
      notify(`Approved ${code}.`);
      await sync().catch(() => {});
    },
  });
  const changes = bar.verb({
    name: 'changes',
    words: 'Request changes…',
    code,
    hover: 'Keeps the plan held. Nothing is dropped or freed. The agent sends a revision to read again.',
    ariaLabel: `Request changes to ${code}`,
    open: () =>
      changesDialog({
        plan: plan.id,
        code,
        revision,
        comments: [...pendingOf(plan.id, revision)],
        approved: [...approvedOf(plan.id, revision)].map((ref) => ({ ref, title: titles.get(ref) ?? '' })),
        onSent: () => {
          clearPending(plan.id);
          notify(`Asked for changes to ${code}.`);
          hooks.sentChanges();
          sync().catch(() => {});
          hooks.decided();
        },
        onRefused: hooks.reread,
      }),
  });
  const reject = bar.verb({
    name: 'reject',
    words: 'Reject…',
    code,
    tone: 'danger',
    hover: `Drops ${allOf(total, 'item')} and leaves your reason on ${them}. It cannot be undone.`,
    open: () =>
      reasonDialog({
        id: 'reject-dialog',
        heading: `Reject ${code}`,
        label: 'Reason (required)',
        submit: 'Reject',
        send: async (reason) => {
          try {
            await call('approve_plan', { plan: plan.id, approved: false, ...(reason.trim() ? { reason } : {}), revision });
          } catch (error) {
            if (error instanceof Refusal) bar.dialogRefused();
            throw error;
          }
          clearPending(plan.id);
          notify(`Rejected ${code}.`);
          await sync().catch(() => {});
          hooks.decided();
        },
      }),
  });
  // What is waiting to be sent, as facts: "2 approved, 3 unsent" (an approval and a comment go with Request changes only).
  const parts = [approvedNow.size ? `${approvedNow.size} approved` : null, mine.length ? `${mine.length} unsent` : null].filter(Boolean);
  const state = parts.length ? h('span', { class: 'decision-state quiet', dataset: { unsent: mine.length ? String(mine.length) : null, approved: approvedNow.size ? String(approvedNow.size) : null } }, parts.join(', ')) : null;
  return wrap(bar, [approve, changes, reject], { state });
}

// -- a suggested move ---------------------------------------------------------------------------------------

// What a proposal moves, by the kind of work it names: "3 stories", "2 tasks", "4 items".
function movedWords(move) {
  const kinds = new Set(move.items.map((one) => parseNodeRef(one.ref)?.kind));
  const noun = kinds.size === 1 ? [...kinds][0] : 'item';
  return plural(move.items.length, noun, noun === 'story' ? 'stories' : undefined);
}

function placementBar(change, hooks) {
  const move = change.item.placement;
  const code = change.code;
  const bar = makeBar({ kind: 'placement', barKind: 'approve', hooks });
  const where = move.parent ? `under ${codeOf(move.parent)}` : 'under it';
  // The items the person left where they are (the page keeps the choice until the proposal is decided): Approve moves the others.
  const kept = move.items.filter((one) => !leftOut(move).has(one.ref));
  const partial = kept.length < move.items.length;
  const moving = { items: kept };
  const them = kept.length === 1 ? 'it' : 'them';
  const approve = bar.verb({
    name: 'approve',
    words: 'Approve',
    code,
    tone: 'approve',
    off: kept.length === 0,
    hover: !kept.length
      ? 'Nothing to move'
      : move.parent
        ? `Moves ${movedWords(moving)} ${where}. Nothing else changes. You can move ${them} back by hand.`
        : `Makes a new epic, ${move.new_epic?.title ?? ''}, and moves ${movedWords(moving)} ${where}. You can move ${them} back by hand.`,
    run: async () => {
      // A list of items goes only when some are left out: all included is the proposal as it was made.
      const result = await call('decide_placement', { placement: move.id, accepted: true, ...(partial ? { items: kept.map((one) => one.ref) } : {}) });
      forgetPicks(move);
      notify(`Accepted ${code}.`);
      if (result.skipped.length) hooks.say(result.skipped.map((one) => `${codeOf(one.ref)} left where it is: ${one.why}`));
      await sync().catch(() => {});
    },
  });
  const reject = bar.verb({
    name: 'reject',
    words: 'Reject…',
    code,
    tone: 'danger',
    hover: 'Moves nothing. Your reason goes back to whoever proposed it. It cannot be undone.',
    open: () =>
      reasonDialog({
        id: 'reject-dialog',
        heading: `Reject ${code}`,
        label: 'Reason (required)',
        submit: 'Reject',
        send: async (reason) => {
          try {
            await call('decide_placement', { placement: move.id, accepted: false, ...(reason.trim() ? { reason } : {}) });
          } catch (error) {
            if (error instanceof Refusal) bar.dialogRefused();
            throw error;
          }
          notify(`Rejected ${code}.`);
          forgetPicks(move);
          await sync().catch(() => {});
          hooks.decided();
        },
      }),
  });
  // What the decision does now, as a fact, when some items are left out: "2 of 3 move".
  const state = partial ? h('span', { class: 'decision-state quiet', dataset: { moving: String(kept.length), of: String(move.items.length) } }, `${kept.length} of ${move.items.length} move`) : null;
  return wrap(bar, [approve, reject], { state });
}

// -- notes ----------------------------------------------------------------------------------------------------

// The bar of one note: Approve confirms it through the dialog the item's page opens (it names the signed-in person), Reject withdraws it
// with a reason (an unconfirmed note needs nobody's word to be withdrawn).
export function noteBar(change, note, hooks) {
  if (!can('write')) return null;
  const code = noteCode(note.id);
  const bar = makeBar({ kind: 'note', barKind: 'approve', hooks });
  const approve = bar.verb({
    name: 'approve',
    words: 'Approve',
    code,
    tone: 'approve',
    hover: 'Records you as the person who reviewed the note. It does not check that it is true. Changing it later needs your word.',
    open: async () => {
      let done = false;
      const { confirmDialog } = await import('./record-forms.js');
      confirmDialog(note, 'confirmed', {
        onDone: () => {
          done = true;
          hooks.decided();
        },
        onClose: () => {
          if (!done) hooks.refused();
        },
      });
    },
  });
  const reject = bar.verb({
    name: 'reject',
    words: 'Reject…',
    code,
    tone: 'danger',
    hover: 'Withdraws the note with your reason. It stays as a record marked withdrawn. It cannot be undone.',
    open: () =>
      reasonDialog({
        id: 'reject-dialog',
        heading: `Reject ${code}`,
        label: 'Reason (required)',
        submit: 'Reject',
        send: async (reason) => {
          try {
            await call('withdraw_note', { note_ids: [note.id], reason });
          } catch (error) {
            if (error instanceof Refusal) bar.dialogRefused();
            throw error;
          }
          notify(`Withdrew ${code}.`);
          await sync().catch(() => {});
          hooks.decided();
        },
      }),
  });
  return wrap(bar, [approve, reject]);
}

// -- a check ----------------------------------------------------------------------------------------------------

// The bar of one check only a person can tick: Approve ticks it as the signed-in person, and the comment says how it was checked.
export function checkBar(change, check, hooks) {
  if (!can('write')) return null;
  const node = change.item.node;
  const code = `${codeOf(node)}:${check.item}`;
  const bar = makeBar({ kind: 'check', barKind: 'approve', hooks });
  const draft = hooks.draft(`check:${node}:${check.item}`);
  const approve = bar.verb({
    name: 'approve',
    words: 'Approve',
    code: codeOf(node),
    tone: 'approve',
    ariaLabel: `Approve ${codeOf(node)}: ${check.text}`,
    hover: 'Ticks the check as you, with how you checked if you say. You can untick it on the item\u2019s page.',
    run: async () => {
      const how = draft.text.trim();
      await call('set_check', { node, item: check.item, checked: true, evidence: how || null });
      draft.open = false;
      draft.text = '';
      notify(`Ticked: ${check.text}`);
      announce(`Ticked: ${check.text}`);
      await sync().catch(() => {});
    },
  });
  const comment = commentControl({ code, draft, label: 'How you checked', text: 'Add note', hooks });
  return wrap(bar, [approve], { comment });
}

// -- a question -------------------------------------------------------------------------------------------------

// The answer and, when the person asked for it, the decision recorded after it. The answer is final and the note is a second call: when
// the note is refused the answer stands, and the page says so beside the bar.
async function giveAnswer(change, answer, comment, draft, hooks, problem) {
  const { ask } = change.item;
  const said = comment.trim();
  await call('answer_ask', { ask: ask.id, answer, ...(said ? { comment: said } : {}) });
  notify(`Answered ${askCode(ask.id)} with ${answer}.`);
  announce(`Answered ${askCode(ask.id)} with ${answer}`);
  const keep = draft.remember;
  Object.assign(draft, { open: false, text: '', remember: false });
  if (keep) {
    try {
      await call('add_note', {
        text: `Q: ${ask.question}\nA: ${answer}`,
        why: said || `Answered ${askCode(ask.id)}.`,
        decided_by: account.name,
        nodes: [change.item.node],
      });
      notify(`Kept ${askCode(ask.id)} as a decision.`);
    } catch (error) {
      if (!(error instanceof Refusal)) throw error;
      const words = `The answer stood, but the decision was not recorded: ${error.message}`;
      refuse(problem, words);
      hooks.stood(words);
    }
  }
  await sync().catch(() => {});
}

function askBar(change, hooks) {
  const { ask } = change.item;
  const code = change.code;
  const bar = makeBar({ kind: 'ask', barKind: 'answer', hooks });
  const draft = hooks.draft(`ask:${ask.id}`);
  const give = (answer) => giveAnswer(change, answer, draft.text, draft, hooks, bar.problem);
  const asker = ask.asked_by;
  const verbs = [];
  if (ask.kind === 'approval' && ask.choices) {
    const yes = ask.choices.find((one) => one.toLowerCase() === 'yes') ?? 'yes';
    const no = ask.choices.find((one) => one.toLowerCase() === 'no') ?? 'no';
    verbs.push(
      bar.verb({
        name: 'approve',
        words: 'Approve',
        code,
        tone: 'approve',
        answer: yes,
        hover: `Sends yes to ${asker}. ${ask.effect === 'gates' ? 'It opens the gate.' : effectHover(ask)} Your answer is final.`,
        run: () => give(yes),
      }),
      bar.verb({
        name: 'reject',
        words: 'Reject…',
        code,
        tone: 'danger',
        answer: no,
        hover: `Sends no to ${asker}. ${ask.effect === 'gates' ? 'The gate stays refused.' : effectHover(ask)} Your answer is final.`,
        open: () =>
          reasonDialog({
            id: 'reject-dialog',
            heading: `Reject ${code}`,
            label: 'Reason (optional)',
            submit: 'Reject',
            prefill: draft.text,
            send: async (reason) => {
              try {
                await giveAnswer(change, no, reason, draft, hooks, bar.problem);
              } catch (error) {
                if (error instanceof Refusal) bar.dialogRefused();
                throw error;
              }
              hooks.decided();
            },
          }),
      }),
    );
  } else if (ask.choices) {
    ask.choices.forEach((choice) => {
      verbs.push(
        bar.verb({
          name: 'answer',
          words: h('span', { dataset: { words: 'theirs' } }, choice),
          code,
          answer: choice,
          ariaLabel: `Answer ${code}: ${choice}`,
          hover: `Sends \u201c${choice}\u201d to ${asker}. ${effectHover(ask)} Your answer is final.`,
          run: () => give(choice),
        }),
      );
    });
  } else {
    verbs.push(
      bar.verb({
        name: 'answer',
        words: 'Answer…',
        code,
        hover: `Opens a box for your words and sends them to ${asker}. Your answer is final.`,
        open: () =>
          answerDialog(ask, async (answer, comment) => {
            try {
              await giveAnswer(change, answer, comment || draft.text, draft, hooks, bar.problem);
            } catch (error) {
              if (error instanceof Refusal) bar.dialogRefused();
              throw error;
            }
            hooks.decided();
          }),
      }),
    );
  }
  const comment = commentControl({ code, draft, label: 'Add a comment', text: 'Comment', keep: true, hooks });
  return wrap(bar, verbs, { comment });
}

// -- a refused approval, a claim that ran out ------------------------------------------------------------------

// Marks the work blocked or dropped with a reason, at the version the board read just now (a stale one is refused in the server's
// words and the page reads again).
async function markWork(node, status, why) {
  const read = await call('get_node', { node });
  await call('update_node', { node, expected_version: read.version, status, why });
}

function dropVerb(bar, change, hooks, { words, hover }) {
  const node = change.item.node;
  const code = codeOf(node);
  return bar.verb({
    name: 'drop',
    words,
    code,
    tone: 'danger',
    hover,
    open: () =>
      reasonDialog({
        id: 'drop-dialog',
        heading: `Drop ${code}`,
        label: 'Reason (required)',
        submit: 'Drop',
        send: async (reason) => {
          try {
            await markWork(node, 'dropped', reason);
          } catch (error) {
            if (error instanceof Refusal) {
              bar.dialogRefused();
              hooks.reread();
            }
            throw error;
          }
          notify(`Dropped ${code}.`);
          await sync().catch(() => {});
          hooks.decided();
        },
      }),
  });
}

function gateBar(change, hooks) {
  const { gate } = change.item;
  const bar = makeBar({ kind: 'gate', barKind: 'look_into', hooks });
  const lift = bar.verb({
    name: 'lift',
    words: 'Lift the gate…',
    code: gate ? gateCode(gate.id) : change.code,
    hover:
      gate && gate.kind === 'approval' && gate.answer === null && gate.ask !== null
        ? `Frees the work and closes the question it asked (${askCode(gate.ask)}). It cannot be undone.`
        : 'Frees the work again, unless something else still holds it. It cannot be undone.',
    open: () => liftDialog(gate, async () => hooks.decided()),
  });
  const drop = dropVerb(bar, change, hooks, { words: 'Drop the work…', hover: 'Marks the work dropped, with your reason as its note. You can change its status again.' });
  return wrap(bar, gate ? [lift, drop] : [drop]);
}

function lapseBar(change, hooks) {
  const node = change.item.node;
  const code = codeOf(node);
  const bar = makeBar({ kind: 'lapse', barKind: 'look_into', hooks });
  const block = bar.verb({
    name: 'block',
    words: 'Block it…',
    code,
    hover: 'Marks the work blocked, with your reason as its note. You can change its status again.',
    open: () =>
      reasonDialog({
        id: 'block-dialog',
        heading: `Block ${code}`,
        label: 'Reason (required)',
        submit: 'Block',
        send: async (reason) => {
          try {
            await markWork(node, 'blocked', reason);
          } catch (error) {
            if (error instanceof Refusal) {
              bar.dialogRefused();
              hooks.reread();
            }
            throw error;
          }
          notify(`Blocked ${code}.`);
          await sync().catch(() => {});
          hooks.decided();
        },
      }),
  });
  const drop = dropVerb(bar, change, hooks, { words: 'Drop it…', hover: 'Marks the work dropped, with your reason as its note. You can change its status again.' });
  return wrap(bar, [block, drop]);
}

// -- the bar of a change ------------------------------------------------------------------------------------------

// The open checks of a check row, and the notes of a note row, once they are read: what each of their bars is for.
export const openChecks = (entry) => (entry && entry.data ? entry.data.checks.filter((one) => !one.checked && one.person_only) : []);
export const notesOf = (entry) => (entry && entry.data ? entry.data.notes : []);

// The bar for the whole change, or null: for a person who can only look, for a change that is not read yet, and for a row of several
// notes or checks, whose bars are one under each line (noteBar, checkBar). `entry` is what was read for the change (needs-details.js).
export function decisionBar(change, hooks, entry = null) {
  if (!can('write')) return null;
  switch (change.look) {
    case 'plan':
      return planBar(change, hooks, entry);
    case 'placement':
      return placementBar(change, hooks);
    case 'question':
    case 'approval':
      return askBar(change, hooks);
    case 'gate':
      return gateBar(change, hooks);
    case 'lapse':
      return lapseBar(change, hooks);
    case 'check': {
      const open = openChecks(entry);
      return open.length === 1 ? checkBar(change, open[0], hooks) : null;
    }
    case 'note': {
      const notes = notesOf(entry);
      return notes.length === 1 ? noteBar(change, notes[0], hooks) : null;
    }
    default:
      return null;
  }
}

// Whether a bar is drawn for each line of the change (several checks or notes) and not once for the row.
export function barsPerLine(change, entry) {
  if (change.look === 'check') return openChecks(entry).length > 1;
  if (change.look === 'note') return notesOf(entry).length > 1;
  return false;
}
