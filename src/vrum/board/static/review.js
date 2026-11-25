// The review page (#/review?change=<id>): one change of the page of what needs a person, full screen (M52). Its address names the change
// (`plan:9`, `placement:12`, `ask:522`, `check:task:160`, `note:task:160`, `lapse:task:160`, `gate:5`) and, for a plan, optionally the revision
// to read (`revision=2`), how to read it (`mode=whole`) and the layout of the lines (`view=split`). The way of grouping the person was shown on
// the list is kept in `group=` so that previous and next follow the order they saw.
//
// The page is: the header (what it is, its title, its facts, Viewed, the way to the work's own page and previous and next), a plan's checks, the
// change itself (a plan or a move as a diff, a question, the checks of the work, each note, what a refused approval waits for, the claims that
// ran out), the context of the work, the conversation (a plan's revisions and feedback, a question's asker) and, kept in view at the bottom, the
// same bar the card has (decision-bar.js). The choices that act on the change (Changes or Whole plan, Unified or Split) are here and work for
// every layout; the list has none.
//
// It reads a plan that waits for no person too (approved, rejected, or waiting for its agent) read only, every revision readable. A change that
// is not in the list any more is "That change is not here". Nothing on this page decides anything except the bar, and no key does.

import { Refusal, call, callAll } from './api.js';
import { can, itemLink, stepBack, ui, viewLink } from './ctx.js';
import { h, moment, plural, put } from './dom.js';
import { icon } from './icons.js';
import { actorLabel, codeOf, planCode } from './labels.js';
import { changeOf, changesOf } from './changes.js';
import { decisionBar } from './decision-bar.js';
import { keyButton } from './key-button.js';
import { moveDiff, planDiff, loading } from './needs-diffs.js';
import { factsOf, kindChip, levelChip, reviewChoices, titleNodes } from './needs-cards.js';
import { aboutPlan, isPlanNews, newsAbout, readPlanWhole, rereadPlans, watchDetails } from './needs-details.js';
import { keepReviewKeys, reviewKeysContent } from './needs-keys.js';
import { planAssist } from './needs-assist.js';
import { byOf, draftOf, drawSaid, fingerprintOf, judge, orderOf, prepare, said, titleOfRef, viewOf } from './needs-model.js';
import { currentNeeds, onNeeds, readNeeds } from './needs-state.js';
import { deltaDiff } from './plan-diffs.js';
import { checksBlock, driftBlock, feedbackSections, hasLine, lostBlock } from './plan-blocks.js';
import { dismissLost, explainProblem, forgetLost, lostOf, noticeRevision, pendingOf, settleProblem } from './plan-comments.js';
import { planNotes } from './plan-notes.js';
import { loadingView, rememberFocus } from './records.js';
import { contextSection } from './review-context.js';
import { otherBody } from './review-changes.js';
import { ensure, onChange } from './store.js';

let root = null; // the element the page is drawn in
let state = null; // what the page is about and what was read for it
let generation = 0; // a read that is answered after the page moved on is dropped
let stops = [];
let next = null; // the change to go to once this one is decided

const SOLO = /^(plan|placement|ask|gate):([1-9][0-9]*)$/;
const OF_WORK = /^(check|note|lapse):((?:story|task):[1-9][0-9]*)$/;

// The change an address names: { kind, number or ref }, or null.
function parseChange(id) {
  const solo = SOLO.exec(id);
  if (solo) return { kind: solo[1], number: Number(solo[2]) };
  const work = OF_WORK.exec(id);
  return work ? { kind: work[1], ref: work[2] } : null;
}

const intOf = (text) => (text && /^[1-9][0-9]*$/.test(text) ? Number(text) : null);

const STATE_WORDS = { proposed: 'Waiting for a person', changes_requested: 'Waiting for the agent', approved: 'Approved', rejected: 'Rejected' };
const STATE_ICONS = { proposed: 'needs', changes_requested: 'clock', approved: 'check', rejected: 'close' };

// The list a person came from, as the back arrow goes to it.
const listAddress = () => {
  const back = stepBack();
  return back.hash.startsWith('#/needs') ? back.hash : `#/needs${state && state.by !== 'level' ? `?group=${state.by}` : ''}`;
};

// A page with only something to say: the change is not here.
function say(words) {
  put(
    root,
    h(
      'div',
      { class: 'item-page review-page review-state-page', id: 'review-page', dataset: { missing: 'true' } },
      h('p', { class: 'state-text', id: 'review-missing', tabindex: -1 }, words),
      h('p', { class: 'item-back' }, h('a', { id: 'review-back', class: 'action-link', href: listAddress(), 'aria-label': 'Back to Needs a person' }, icon('arrow_left', { size: 16 }), 'Needs a person')),
    ),
  );
}

// -- reading ---------------------------------------------------------------------------------------------------

// What the work a change is about holds, for its context: the item itself and its events.
async function readWork(node) {
  const read = await call('get_node', { node });
  const history = await callAll('node_history', { node });
  return { node: read, history };
}

// Reads what the page is about: the list (to find the change and its place in the order), the plan whole for a plan, and the work for any
// other change. A change the server will not show, or that is not in the list, leaves the page saying so.
async function load(params, { quiet = false } = {}) {
  generation += 1;
  const mine = generation;
  const id = params.get('change') || '';
  const parsed = parseChange(id);
  const kept = state && state.id === id ? state : null;
  state = {
    id,
    parsed,
    params,
    by: byOf(params.get('group')),
    revision: intOf(params.get('revision')),
    list: kept ? kept.list : null,
    plan: null,
    work: null,
    opened: kept ? kept.opened : new Set(),
    loaded: false,
    deciding: false,
    neighbour: null,
    shown: null,
  };
  if (!parsed) {
    say('That change is not here');
    return;
  }
  if (!quiet) put(root, loadingView());
  try {
    state.list = await currentNeeds();
    await ensure('rules').catch(() => {});
    if (parsed.kind === 'plan') {
      state.plan = await readPlanWhole(parsed.number, state.revision);
    } else {
      const change = changesOf(state.list, titleOfRef).find((one) => one.id === id);
      if (change) state.work = await readWork(change.item.node);
    }
  } catch (error) {
    if (mine !== generation) return;
    if (!(error instanceof Refusal)) throw error;
    say('That change is not here');
    return;
  }
  if (mine !== generation) return;
  state.loaded = true;
  draw();
}

// -- the pieces ---------------------------------------------------------------------------------------------------

// A choice between two ways of seeing the same change, drawn as one control: the pressed one is the one in force. It changes the address
// (replacing the entry, so that the back arrow is not a step through every choice).
function segmented(id, label, choices, current, attribute, onPick) {
  return h(
    'div',
    { class: 'segmented review-choice', id, role: 'group', 'aria-label': label },
    choices.map(([name, words, extra]) =>
      h('button', { type: 'button', class: `small${extra ? ` ${extra}` : ''}`, 'aria-pressed': String(name === current), dataset: { [attribute]: name, focusKey: `${id}:${name}` }, onclick: () => onPick(name) }, words),
    ),
  );
}

const choose = (changes) => ui.navigate(changes, { replace: true });

// The select of revisions of a plan.
function revisionSelect(plan) {
  const revisions = plan.revisions ?? [];
  if (revisions.length < 2) return null;
  const pick = h(
    'select',
    { id: 'review-revision', 'aria-label': 'Revision', dataset: { focusKey: 'review-revision' }, onchange: () => choose({ revision: Number(pick.value) === plan.revision ? null : pick.value, mode: null }) },
    revisions.map((one) => h('option', { value: String(one.revision) }, `Revision ${one.revision}${one.revision === plan.revision ? ' (current)' : ''}`)),
  );
  pick.value = String(plan.shown);
  return pick;
}

// What the plan says about itself when no person has to decide it: its state, who decided it and why.
function planState(plan) {
  const decided = plan.decided_by ? h('span', { class: 'plan-decided' }, `${STATE_WORDS[plan.state]} by `, actorLabel(plan.decided_by, 'person'), `, ${moment(plan.decided_at)}`) : null;
  return [
    h('span', { class: 'chip plan-state', dataset: { state: plan.state } }, icon(STATE_ICONS[plan.state] ?? 'info', { size: 14 }), STATE_WORDS[plan.state] ?? plan.state),
    decided,
    plan.reason ? h('p', { class: 'plan-reason', dataset: { words: 'theirs' } }, h('strong', {}, plan.state === 'rejected' ? 'Reason ' : 'Said '), plan.reason) : null,
  ];
}

// How the plan is read now: what changed since the revision before (the way to read a revised plan) unless the address asks for the whole.
const modeOf = () => (state.plan && state.plan.changes && state.params.get('mode') !== 'whole' ? 'delta' : 'whole');
const splitNow = () => state.params.get('view') === 'split';

// Goes to a comment that is not sent: the fold that hides its line opens, and its Edit button takes the focus.
function reveal(id) {
  const target = root?.querySelector(`.diff-comment-pending[data-comment="${id}"]`);
  if (!target) return;
  if (target.dataset.closed) root.querySelector(`button.diff-fold[data-fold="${CSS.escape(target.dataset.closed)}"]`)?.click();
  target.scrollIntoView({ block: 'center' });
  target.querySelector('.comment-edit')?.focus({ preventScroll: true });
}

// The conversation of a plan: every revision with who made it and what they said changed, what was asked for in feedback, and the comments
// not sent yet.
function planConversation(plan, feedback) {
  const revisions = plan.revisions ?? [];
  const mine = pendingOf(plan.id, plan.revision);
  const list = revisions.length
    ? h(
        'ol',
        { class: 'plain plan-revisions', 'aria-label': 'Revisions' },
        revisions.map((one) =>
          h(
            'li',
            { class: 'plan-revision', dataset: { revision: one.revision, shown: String(one.revision === plan.shown) } },
            h('p', { class: 'plan-revision-author' }, `Revision ${one.revision} by `, actorLabel(one.by, one.by_kind), `, ${moment(one.at)}`),
            one.note ? h('p', { class: 'plan-revision-note', dataset: { words: 'theirs' } }, one.note) : null,
          ),
        ),
      )
    : null;
  const unsent = mine.length
    ? h(
        'details',
        { class: 'change-unsent' },
        h('summary', {}, icon('chevron_right', { size: 14, className: 'disclosure-chevron' }), plural(mine.length, 'unsent comment')),
        h(
          'ul',
          { class: 'plain unsent-comments' },
          mine.map((one) =>
            h(
              'li',
              { dataset: { comment: one.id, ref: one.item } },
              h('button', { type: 'button', class: 'link unsent-go', dataset: { comment: one.id, focusKey: `unsent:${one.id}`, words: 'theirs' }, onclick: () => reveal(one.id) }, `${codeOf(one.item)}: ${one.text.length > 80 ? `${one.text.slice(0, 79)}\u2026` : one.text}`),
            ),
          ),
        ),
      )
    : null;
  const sections = feedbackSections(feedback, { revised: plan.revision > 1, openAll: plan.shown !== plan.revision, hasLine: (ref) => hasLine(plan, plan.changes ? modeOf() : 'whole', ref) });
  if (!list && !sections && !unsent) return null;
  return h('section', { class: 'review-part review-conversation', dataset: { part: 'conversation' } }, h('h2', { class: 'review-part-title' }, 'Conversation'), list, sections, unsent);
}

// The conversation of a question: who asked it. (An answered question is in no row of the list.)
function askConversation(change) {
  const { ask } = change.item;
  if (!ask) return null;
  return h(
    'section',
    { class: 'review-part review-conversation', dataset: { part: 'conversation' } },
    h('h2', { class: 'review-part-title' }, 'Conversation'),
    h('p', { class: 'conversation-asked' }, 'Asked by ', actorLabel(ask.asked_by, ask.asked_by_kind), `, ${moment(ask.asked_at)}`),
  );
}

// Previous and next in the order of the list, with the place in it.
function steppers(order, at, by) {
  const step = (id, words, key, mark) => {
    if (!key) return h('span', { id, class: 'review-step off', 'aria-disabled': 'true', 'aria-label': words }, icon(mark, { size: 16 }));
    return viewLink('review', { change: key, revision: null, mode: null, group: by === 'level' ? null : by }, { id, class: 'review-step', 'aria-label': words, title: words, dataset: { focusKey: id } }, icon(mark, { size: 16 }));
  };
  return h(
    'nav',
    { class: 'review-steps', 'aria-label': 'Changes in the list' },
    step('review-prev', 'Previous change', order[at - 1], 'chevron_left'),
    h('span', { id: 'review-position', class: 'review-position' }, `${at + 1} of ${order.length}`),
    step('review-next', 'Next change', order[at + 1], 'chevron_right'),
  );
}

// -- the page -------------------------------------------------------------------------------------------------------

// The change of this page: the row of the list, or for a plan that waits for no person (and so is in no row) a change made from the plan.
function findChange(changes) {
  const found = changes.find((one) => one.id === state.id);
  if (found) return found;
  if (state.parsed.kind !== 'plan' || !state.plan) return null;
  const plan = state.plan;
  return changeOf(
    {
      kind: 'plan',
      node: plan.items?.[0] ?? '',
      title: plan.title,
      reason: '',
      waiting_since: plan.revised_at,
      waiting: plan.revised_by,
      waiting_kind: plan.revised_by_kind,
      count: plan.epics + plan.stories + plan.tasks,
      ask: null,
      plan,
      notes: [],
      gate: null,
      placement: null,
      level: 'plan',
      decision: 'approve',
      topic: null,
    },
    titleOfRef,
  );
}

// What happens when a bar's verb is pressed and decided: go to the next change, or back to the list when there is none.
const hooksFor = (change, redraw) => ({
  say(lines) {
    if (!lines.length) return;
    said.lines = lines;
    drawSaid(root.querySelector('#review-said'));
  },
  stood(words) {
    said.problems = [...said.problems, words];
    drawSaid(root.querySelector('#review-said'));
  },
  draft: draftOf,
  redraw,
  pressed() {
    state.deciding = true;
    next = state.neighbour;
  },
  decided() {
    const to = next;
    next = null;
    state.deciding = false;
    if (to) ui.navigate({ change: to, revision: null, mode: null }, { page: 'review', replace: true });
    else ui.navigate({ change: null, revision: null, mode: null, view: null, group: state.by === 'level' ? null : state.by }, { page: 'needs', replace: true });
  },
  refused() {
    state.deciding = false;
    next = null;
  },
  // A plan was refused (it may have moved on while it was read): read the list and the plan again at once, not at the next news.
  async reread() {
    const plan = change.item.plan ? change.item.plan.id : null;
    if (plan !== null) explainProblem(plan);
    rereadPlans();
    try {
      await readNeeds();
      if (plan !== null) state.plan = await readPlanWhole(plan, state.revision);
    } catch (error) {
      if (!(error instanceof Refusal)) throw error;
    }
    draw();
    if (plan !== null) settleProblem(plan);
  },
  // Changes were asked for: the plan is the agent's turn now, and the decision takes the page on.
  sentChanges() {},
});

// Marks the change viewed (or takes the mark off), as the list does, so that "Reviewed N of M" there follows.
function setViewed(change, entry, on) {
  const view = viewOf(change.key);
  view.viewed = on ? fingerprintOf(change, entry) : null;
  view.changed = false;
  draw();
}

// Draws the whole page from what was read. It is drawn again, never patched: when news comes, when an answer to a read arrives, when the
// address changes; what the person did (viewed, a comment being typed, the focus) is kept.
function draw() {
  if (!root || !state || !state.loaded) return;
  const prepared = prepare(state.list, (one) => one.id === state.id || one.look === 'placement');
  const change = findChange(prepared.changes);
  if (!change) {
    if (!state.deciding) say('That change is not here');
    return;
  }
  const inList = prepared.changes.some((one) => one.id === change.id);
  const entry = inList ? prepared.entries.get(change.key) : null;
  const restore = rememberFocus(root);
  const isPlan = change.look === 'plan';
  const plan = isPlan ? state.plan : null;
  if (isPlan && inList && plan && plan.shown === plan.revision) noticeRevision(plan.id, plan.revision);
  const view = inList ? judge(change, entry) : null;

  // The order of the list as the person was shown it, and this change's place in it.
  const order = orderOf(prepared.changes, state.by);
  const at = order.indexOf(change.key);
  const fromList = /^#\/(needs|review)\b/.test(ui.previous ?? '');
  state.neighbour = at >= 0 ? (order[at + 1] ?? order[at - 1] ?? null) : null;
  const hooks = hooksFor(change, (focusKey = null) => {
    draw();
    if (focusKey) root.querySelector(`[data-focus-key="${CSS.escape(focusKey)}"]`)?.focus();
  });

  // The plan as it is read here: what the checks and flags say, and whether a person can write on it and decide it.
  const current = !plan || plan.shown === plan.revision;
  const decidable = can('write') && inList && current && (!plan || plan.state === 'proposed');
  const assist = isPlan ? planAssist({ data: plan }) : change.assist;
  if (isPlan) {
    change.assist = assist;
    change.warn = assist.warn;
  }
  const split = splitNow();

  // -- the change itself
  const fold = { open: (key) => state.opened.has(key), onOpen: (key) => state.opened.add(key) };
  let body;
  let mode = null;
  if (isPlan) {
    mode = modeOf();
    const feedback = current ? (plan.feedback ?? []) : (plan.feedback ?? []).filter((one) => one.revision === plan.shown);
    const notes = planNotes({ plan: plan.id, revision: plan.shown, items: plan.tree, feedback, editable: decidable, asOf: state.list.as_of, hooks });
    const lines = mode === 'delta' ? deltaDiff(plan, fold, assist, notes, () => choose({ mode: 'whole' }), split) : planDiff({ state: 'ready', data: plan }, fold, assist, split, notes);
    const lost = lostOf(plan.id);
    body = [
      current && lost ? lostBlock(lost, { onDismiss: () => { dismissLost(plan.id); draw(); } }) : null,
      current ? driftBlock(plan.shown, plan.drift, { advice: null }) : null,
      h('div', { class: 'change-diff', id: 'review-diff', role: 'group', 'aria-label': `Revision ${plan.shown} of ${planCode(plan.id)}`, dataset: { mode, view: split ? 'split' : 'unified', revision: plan.shown } }, lines),
    ];
  } else if (change.look === 'placement') {
    const reading = change.assist && change.assist.reading;
    body = [h('div', { class: 'change-diff', id: 'review-diff', role: 'group', 'aria-label': `The change in ${change.name}`, dataset: { view: split ? 'split' : 'unified' } }, reading ? [loading('Reading…')] : moveDiff(change, fold, change.assist, split))];
  } else {
    body = [h('div', { class: 'review-body', id: 'review-body', dataset: { kind: change.look } }, otherBody(change, { entry, node: state.work?.node, history: state.work?.history ?? [], hooks }))];
  }

  // -- the choices that act on this change
  const showsLines = isPlan || change.look === 'placement';
  const toggles = showsLines
    ? h(
        'div',
        { class: 'review-toggles' },
        isPlan ? revisionSelect(plan) : null,
        isPlan && plan.changes ? segmented('review-mode', 'How to read the plan', [['delta', 'Changes', 'mode-pick'], ['whole', 'Whole plan', 'mode-pick']], mode, 'mode', (picked) => choose({ mode: picked === 'delta' ? null : 'whole' })) : null,
        segmented('review-view', 'Layout of the lines', [['unified', 'Unified'], ['split', 'Split']], split ? 'split' : 'unified', 'view', (picked) => choose({ view: picked === 'split' ? 'split' : null })),
      )
    : null;

  // -- the parts
  const checks = isPlan && current && assist.checks ? h('section', { class: 'review-part', dataset: { part: 'checks' } }, h('h2', { class: 'review-part-title' }, 'Checks'), checksBlock(change, assist)) : null;
  const changes = h('section', { class: 'review-part', dataset: { part: 'changes' } }, h('h2', { class: 'review-part-title' }, 'Changes'), toggles, body);
  const context = isPlan || !state.work ? null : contextSection(change, state.work.node, state.work.history, { skip: change.look === 'check' ? ['definition'] : [] });
  const feedbackFor = isPlan ? (current ? (plan.feedback ?? []) : (plan.feedback ?? []).filter((one) => one.revision === plan.shown)) : [];
  const conversation = isPlan ? planConversation(plan, feedbackFor) : askConversation(change);

  // -- the bar, kept in view
  const bar = decidable ? decisionBar(change, hooks, isPlan ? { state: 'ready', data: plan } : entry) : null;

  // -- the header
  const steps = fromList && at >= 0 ? steppers(order, at, state.by) : null;
  const viewed = inList
    ? h('label', { class: 'change-viewed-label' }, h('input', { type: 'checkbox', class: 'change-viewed', 'aria-label': `Viewed ${change.name}`, id: 'review-viewed', dataset: { focusKey: 'review-viewed' }, checked: view.viewed !== null, onchange: (event) => setViewed(change, entry, event.currentTarget.checked) }), icon('viewed', { size: 14 }), h('span', {}, 'Viewed'))
    : null;
  const head = h(
    'header',
    { class: 'review-head' },
    h('a', { id: 'review-back', class: 'review-back ghost icon-only', href: listAddress(), 'aria-label': 'Back to Needs a person', title: 'Back to Needs a person' }, icon('arrow_left', { size: 18 })),
    h(
      'div',
      { class: 'review-headline' },
      h('p', { class: 'review-lead' }, kindChip(change), levelChip(change.level), isPlan && !inList ? planState(plan) : null),
      h('h1', { class: 'review-title', id: 'review-title', tabindex: -1 }, titleNodes(change)),
      factsOf(change, state.list.as_of),
    ),
    h(
      'div',
      { class: 'review-tools' },
      view && view.changed && view.viewed === null ? h('span', { class: 'chip change-changed', role: 'status' }, 'Changed since viewed') : null,
      viewed,
      isPlan || !change.item.node ? null : itemLink(change.item.node, { id: 'review-item', class: 'action-link small', dataset: { focusKey: 'review-item' } }, `Open ${codeOf(change.item.node)}`),
      steps,
      keyButton(reviewKeysContent(), { id: 'review-keys-button', label: 'Keys' }),
    ),
  );

  put(
    root,
    h(
      'div',
      { class: 'item-page review-page', id: 'review-page', dataset: { kind: change.item.kind, change: change.id, level: change.level, decision: change.decision, view: split ? 'split' : 'unified', mode, inList: String(inList) } },
      head,
      h('div', { class: 'needs-said-box', id: 'review-said' }),
      checks,
      changes,
      context,
      conversation,
      bar ? h('div', { class: 'review-bar', id: 'review-bar' }, bar) : null,
    ),
  );
  drawSaid(root.querySelector('#review-said'));
  state.shown = { change, entry, inList, plan, order, at };
  restore();
}

// -- the keys ----------------------------------------------------------------------------------------------------------

function goTo(key) {
  if (!key) return false;
  ui.navigate({ change: key, revision: null, mode: null }, { page: 'review', replace: true });
  return true;
}

const keysFor = () => ({
  next: () => Boolean(state && state.shown) && goTo(state.shown.order[state.shown.at + 1]),
  previous: () => Boolean(state && state.shown) && state.shown.at > 0 && goTo(state.shown.order[state.shown.at - 1]),
  back() {
    location.hash = listAddress();
    return true;
  },
  toggleSplit() {
    if (!state || !state.shown || !(state.shown.plan || state.shown.change.look === 'placement')) return false;
    choose({ view: splitNow() ? null : 'split' });
    return true;
  },
  toggleMode() {
    if (!state || !state.shown || !state.shown.plan || !state.shown.plan.changes) return false;
    choose({ mode: modeOf() === 'delta' ? 'whole' : null });
    return true;
  },
  toggleViewed() {
    if (!state || !state.shown || !state.shown.inList) return false;
    const { change, entry } = state.shown;
    setViewed(change, entry, viewOf(change.key).viewed === null);
    return true;
  },
  focusVerdict(which) {
    const verdicts = [...root.querySelectorAll('button.verdict')].filter((one) => !one.disabled);
    const target = which === 'approve' ? verdicts[0] : (verdicts.find((one) => one.dataset.verb === 'reject') ?? (verdicts.length > 1 ? verdicts[verdicts.length - 1] : null));
    target?.focus();
    return Boolean(target);
  },
  focusComment() {
    const control = root.querySelector('button.decision-comment, button.diff-comment');
    control?.focus();
    return Boolean(control);
  },
});

// -- the page's life ------------------------------------------------------------------------------------------------------

export function mount(element, params) {
  root = element;
  state = null;
  next = null;
  let waiting = 0;
  watchDetails(() => {
    if (waiting) return;
    waiting = setTimeout(() => {
      waiting = 0;
      draw();
    }, 0);
  });
  stops = [
    onNeeds((list) => {
      if (!state) return;
      state.list = list;
      draw();
    }),
    onChange((change) => {
      if (change.events) refresh(change.events);
    }),
    keepReviewKeys(keysFor()),
    () => clearTimeout(waiting),
  ];
  load(params);
}

export function unmount() {
  stops.forEach((stop) => stop());
  stops = [];
  watchDetails(() => {});
  forgetLost();
  generation += 1;
  root = null;
  state = null;
}

// The address changed under the page: another change or revision is read; a different way of reading the same one is drawn again.
export function update(params) {
  if (!root) return;
  const now = { id: params.get('change') || '', revision: intOf(params.get('revision')) };
  if (!state || now.id !== state.id || now.revision !== state.revision) {
    load(params, { quiet: Boolean(state && state.id === now.id) });
    return;
  }
  state.params = params;
  state.by = byOf(params.get('group'));
  draw();
}

// News about the plan (a revision, a request, a decision) or about work the change is about: what was read is read again, quietly. The page
// follows the news; the list it came from is drawn from the same answer (needs-state.js).
export function refresh(events) {
  if (!root || !state || !state.loaded) return;
  newsAbout(events);
  if (state.parsed && state.parsed.kind === 'plan' && state.plan) {
    const refs = new Set([...state.plan.tree.map((one) => one.ref), ...(state.plan.previous ?? []).map((one) => one.ref)]);
    const touched = events.some((event) => aboutPlan(event, state.parsed.number) || refs.has(event.node) || refs.has(event.other_node) || (event.rule_id !== null && event.rule_id !== undefined) || isPlanNews(event));
    if (!touched) return;
    const mine = generation;
    readPlanWhole(state.parsed.number, state.revision).then(
      (plan) => {
        if (mine !== generation || !state) return;
        state.plan = plan;
        draw();
      },
      (error) => {
        if (!(error instanceof Refusal)) throw error;
      },
    );
  } else if (state.shown && state.shown.change.item.node) {
    const node = state.shown.change.item.node;
    if (!events.some((event) => event.node === node || event.other_node === node)) return;
    const mine = generation;
    readWork(node).then(
      (work) => {
        if (mine !== generation || !state) return;
        state.work = work;
        draw();
      },
      (error) => {
        if (!(error instanceof Refusal)) throw error;
      },
    );
  }
}
