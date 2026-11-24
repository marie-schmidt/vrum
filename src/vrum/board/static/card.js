// The details of one epic, story or task, as the item page draws them: everything about it, and the
// ways to change it, with its acceptance criteria and the definition of done (a story or a task), the
// questions an agent put to a person about it, the notes that concern it, what is alike and its history. Every change is the same operation an agent
// would call, with the version the person saw,
// so a change made meanwhile by an agent is refused in the same words and nothing typed is lost.
//
// The details are read as text first, with an Edit button in the page's header: the form for the
// title, the words, the status, the priority and the dates opens in their place, with Save changes
// and Cancel, so the same words are never on the page twice.
//
// item.js owns the page: its title, the table of what is under the item, its Links section and the places
// in its header for the properties of the item (`options.summary`) and the Edit button (`options.actions`),
// which the card fills. The card lays its sections out in one column, one under the other: the details, the
// page's table and links (`options.table`, `options.links`), then the acceptance criteria, the definition of
// done, the notes and the history, as tables across the whole width.

import { askItem } from './asks.js';
import { Refusal, call, callAll } from './api.js';
import { can, isMe, notify, viewLink } from './ctx.js';
import { dateField } from './datepicker.js';
import { lateDaysOf } from './dates.js';
import { clear, day, h, moment, plural, put } from './dom.js';
import { parentChoices } from './create.js';
import { requirementsTable } from './entries.js';
import { field, refuse, sizeSelect } from './forms.js';
import { icon } from './icons.js';
import { PRIORITIES, PRIORITY, STATUS, SETTABLE, actorLabel, addsUp, codeOf, dueChip, pointsWords } from './labels.js';
import { renderMarkdown } from './markdown.js';
import { askRecall, askSimilar, knownRefs, recallBlock, recallTotal, similarSection } from './recall.js';
import { contextSection, countTitle, detailsHolder, detailsSection, editButton, emptyField, explainStale, hasDetails, historySection, loadingView, mentionsSection, releaseDialog, rememberFocus, requirementDialog, sectionWith, section, stateStrip, watchEdits } from './records.js';
import { withReferences } from './reference-picker.js';
import { fetchMentions, loadTargets, nodeLink } from './references.js';
import { checksSection } from './checks.js';
import { gatesSection, holdChip } from './gates.js';
import { suggestSection } from './suggestions.js';
import { dateChip, priorityChip, sizeControl, statusChip } from './status-menu.js';
import { childrenOf, ensure, store, sync, titleOf } from './store.js';
import { whereBlock } from './whereabouts.js';

const wide = (element) => {
  element.classList.add('wide');
  return element;
};

// -- the form -------------------------------------------------------------------------------------

// `cancel` closes the form with nothing saved.
function editForm(node, state, cancel) {
  const title = h('input', { id: 'edit-title', type: 'text', value: node.title, autocomplete: 'off' });
  const body = withReferences(h('textarea', { id: 'edit-body', rows: 6, value: node.body || '' }));
  const status = h(
    'select',
    { id: 'edit-status', value: node.status },
    node.status === 'ready' ? h('option', { value: 'ready', disabled: true }, 'Ready (automatic)') : null,
    SETTABLE.map((name) => h('option', { value: name }, STATUS[name].label)),
  );
  const priority = h(
    'select',
    { id: 'edit-priority', value: node.priority },
    PRIORITIES.map((name) => h('option', { value: name }, PRIORITY[name].label)),
  );
  const size = node.kind === 'epic' ? null : sizeSelect({ id: 'edit-size', value: node.size });
  // Blocked or dropped may be given a reason, which the server keeps as a handoff note. The field is there only while
  // the status is being changed to one of them.
  const why = h('textarea', { id: 'edit-why', rows: 3 });
  const whyField = wide(field('Why', why));
  const asksWhy = () => status.value !== node.status && (status.value === 'blocked' || status.value === 'dropped');
  const showWhy = () => {
    whyField.hidden = !asksWhy();
  };
  status.addEventListener('change', showWhy);
  showWhy();
  const start = h('input', { id: 'edit-start', value: day(node.start_on) });
  const target = h('input', { id: 'edit-target', value: day(node.target_on) });
  const problem = h('div', { class: 'problem', role: 'alert' });

  const submit = async (event) => {
    event.preventDefault();
    problem.replaceChildren();
    const args = { node: node.ref, expected_version: state.version };
    const was = { title: node.title, body: node.body || '', status: node.status, priority: node.priority };
    if (title.value !== was.title) args.title = title.value;
    if (body.value !== was.body) args.body = body.value;
    if (status.value !== was.status) args.status = status.value;
    if (asksWhy() && why.value.trim()) args.why = why.value;
    if (priority.value !== was.priority) args.priority = priority.value;
    if (size && size.value !== (node.size || '')) {
      if (size.value) args.size = size.value;
      else args.clear_size = true;
    }
    for (const [name, input, before] of [['start_on', start, day(node.start_on)], ['target_on', target, day(node.target_on)]]) {
      if (input.value === before) continue;
      if (input.value) args[name] = input.value;
      else args[`clear_${name}`] = true;
    }
    if (Object.keys(args).length === 2) {
      refuse(problem, 'Nothing has been changed yet, so there is nothing to save.');
      return;
    }
    try {
      await call('update_node', args);
      state.dirty = false;
      state.editing = false;
      notify(`Saved “${args.title ?? node.title}”.`);
      await sync();
      await state.reload(true);
    } catch (error) {
      if (!(error instanceof Refusal)) throw error;
      await explain(error, problem, node, state);
    }
  };

  return h(
    'form',
    { class: 'edit form-grid', onsubmit: submit },
    wide(field('Title', title)),
    wide(field('Details', body)),
    field('Status', status),
    field('Priority', priority),
    size ? field('Size', size) : null,
    field('Start', dateField(start, 'Start')),
    field('Target', dateField(target, 'Target')),
    whyField,
    problem,
    h('div', { class: 'form-actions' }, h('button', { type: 'button', class: 'cancel-edit', onclick: cancel }, 'Cancel'), h('button', { type: 'submit', class: 'primary' }, 'Save changes')),
  );
}

const explain = (error, place, node, state) => explainStale(error, place, state, async () => (await call('get_node', { node: node.ref })).version);

// -- parent -------------------------------------------------------------------------------------

function moveForm(node, state) {
  if (node.kind === 'epic') return null;
  // It starts empty ("Choose an epic" / "Choose a story"): where the item is now is in the path above the page, and a move is a
  // decision the person makes.
  const choice = h('select', { id: 'move-parent' }, parentChoices(node.kind, null).filter(Boolean));
  choice.querySelector('option[value=""]')?.classList.add('choose');
  const problem = h('div', { class: 'problem', role: 'alert' });
  return h(
    'form',
    {
      class: 'inline-form move',
      onsubmit: async (event) => {
        event.preventDefault();
        problem.replaceChildren();
        if (!choice.value) {
          refuse(problem, node.kind === 'story' ? 'Choose an epic.' : 'Choose a story.');
          return;
        }
        try {
          await call('move_node', { node: node.ref, expected_version: state.version, new_parent: choice.value });
          notify(`Moved “${node.title}” under “${titleOf(choice.value)}”.`);
          state.dirty = false;
          await sync();
        } catch (error) {
          if (!(error instanceof Refusal)) throw error;
          await explain(error, problem, node, state);
        }
      },
    },
    h('label', { for: 'move-parent' }, node.kind === 'story' ? 'Move to epic' : 'Move to story'),
    h('div', { class: 'inline-row' }, choice, h('button', { type: 'submit' }, 'Move')),
    problem,
  );
}

// -- acceptance criteria -------------------------------------------------------------------------

// The acceptance criteria of a story or a task are the ones on it, in the order they were written. A task
// also shows the ones on its story, since an agent taking the task is told both. An epic has none.
const criteriaOn = (ref) => (store.rules || []).filter((rule) => rule.node === ref).sort((a, b) => a.id - b.id);

// "2 of 3 met" for the ones that still apply (a dropped one is not counted, and is said when there is one).
function metWords(rules) {
  const live = rules.filter((rule) => rule.status !== 'dropped');
  const dropped = rules.length - live.length;
  const met = live.length ? `${live.filter((rule) => rule.status === 'met').length} of ${live.length} met` : '';
  return [met, dropped ? `${dropped} dropped` : ''].filter(Boolean).join(', ');
}

// The criteria a task has from its story: only read here, each a link to its own page.
function fromStory(node) {
  const story = node.kind === 'task' ? store.nodes.get(node.parent) : null;
  const rules = story ? criteriaOn(story.ref).filter((rule) => rule.status !== 'dropped') : [];
  if (!rules.length) return null;
  return h(
    'div',
    { class: 'criteria-group', dataset: { group: 'from-story' } },
    h('h4', {}, 'From its story ', nodeLink(story.ref)),
    requirementsTable(rules, { caption: `Acceptance criteria of ${codeOf(story.ref)} ${story.title}`, onItem: false, plain: true }).element,
  );
}

// The page's section of them, with the way to add one. With none on the item it is an empty field with the
// button inside it, like the details; for a person who cannot add one, and with none to read, there is no section.
// The heading says how many are met, which is what the count would say.
function criteriaSection(node, state) {
  const rules = criteriaOn(node.ref);
  const add = can('write')
    ? h('button', { type: 'button', class: 'small add-requirement', onclick: () => requirementDialog({ node: node.ref, onDone: () => state.reload(true) }) }, icon('plus', { size: 14 }), 'Add an acceptance criterion…')
    : null;
  const inherited = fromStory(node);
  if (!rules.length && !add && !inherited) return null;
  const title = 'Acceptance criteria';
  return sectionWith(
    countTitle(title, metWords(rules)),
    'requirements',
    rules.length ? add : null,
    rules.length ? requirementsTable(rules, { caption: `${title} of ${codeOf(node.ref)} ${node.title}`, onItem: false }).element : add ? emptyField(add) : null,
    inherited,
  );
}

// -- the definition of done --------------------------------------------------------------------------

// The project's definition of done, read only: it is changed once, in Settings, and holds for every story and
// task. With nothing written it is an empty field with the way to Settings, and for a person who cannot go there,
// no section.
function definitionSection(definition) {
  const written = hasDetails(definition.body);
  const edit = can('write')
    ? viewLink('definition', {}, { class: 'action-link small edit-definition' }, written ? 'Edit in Settings' : 'Set it in Settings')
    : null;
  if (!written && !edit) return null;
  return sectionWith('Definition of done', 'definition', written ? edit : null, written ? h('div', { class: 'body-text' }, renderMarkdown(definition.body)) : emptyField(edit));
}

// -- questions put to a person ----------------------------------------------------------------------------

// The asks an agent put to a person about this story or task, as the server sends them with the item (the open ones first, then the
// latest answered): the question, who asked and when, what it did to the work, and the answer, or the way to give it. An item with
// none has no section.
function asksSection(node, state) {
  const asks = Array.isArray(node.asks) ? node.asks : [];
  if (!asks.length) return null;
  state.drafts = state.drafts || new Map();
  const draftOf = (id) => {
    if (!state.drafts.has(id)) state.drafts.set(id, { open: false, text: '' });
    return state.drafts.get(id);
  };
  return section(
    countTitle('Questions', asks.length),
    'asks',
    h('ul', { class: 'plain asks', 'aria-label': `Questions about ${codeOf(node.ref)} ${node.title}` }, asks.map((ask) => askItem(ask, { draft: draftOf(ask.id), onAnswered: () => state.reload(true) }))),
  );
}

// -- handoff notes ---------------------------------------------------------------------------------

const SHOWN_NOTES = 5; // more than this many handoff notes start with the newest few, and Show all

// One handoff note: who left it, when, how the work ended, where the work was when it was said, and the words in full.
const handoffItem = (said, ...before) => {
  const { actor, actor_kind: kind, at, outcome, note } = said;
  return h(
    'li',
    { class: 'handoff-item', dataset: { outcome } },
    ...before,
    h('p', { class: 'handoff-by' }, actorLabel(actor, kind), `, ${moment(at)}, ${outcome}:`),
    whereBlock(said),
    note ? renderMarkdown(note, { className: 'markdown handoff-text' }) : null,
  );
};

// Every handoff note the item ever got, newest first: each time work on it ended, or a claim was released
// with a note. They are the events of its history (oldest first) that left a note.
function handoffsSection(history) {
  const notes = [...history].reverse().filter((event) => event.action === 'handoff').map((event) => ({ ...event.after, actor: event.actor, actor_kind: event.actor_kind, at: event.at }));
  if (!notes.length) return null;
  const list = h('ul', { class: 'plain handoffs' }, notes.slice(0, SHOWN_NOTES).map((note) => handoffItem(note)));
  const more =
    notes.length > SHOWN_NOTES
      ? h(
          'button',
          {
            type: 'button',
            class: 'small show-all-handoffs',
            onclick: (event) => {
              list.replaceChildren(...notes.map((note) => handoffItem(note)));
              event.currentTarget.remove();
            },
          },
          `Show all ${notes.length}`,
        )
      : null;
  return section(countTitle('Handoff notes', notes.length), 'handoff', list, more);
}

// The last handoff note of each thing this item needs first: the agent that takes it is told them, so
// a person sees them here. `prerequisites` are those items as read (a missing one is null).
function neededHandoffs(prerequisites) {
  const found = prerequisites.filter((other) => other && other.last_handoff);
  if (!found.length) return null;
  return section(
    countTitle('Handoff notes from what it needs first', found.length),
    'needs-handoff',
    h(
      'ul',
      { class: 'plain handoffs' },
      found.map((other) =>
        handoffItem(
          other.last_handoff,
          h('p', { class: 'handoff-for' }, nodeLink(other.ref), other.handoff_count > 1 ? h('span', { class: 'quiet' }, ` (${plural(other.handoff_count, 'note')} in all)`) : null),
        ),
      ),
    ),
  );
}

// -- the whole card -----------------------------------------------------------------------------

const meter = (done, total, label) =>
  h('span', { class: 'meter', role: 'progressbar', 'aria-label': label, 'aria-valuemin': 0, 'aria-valuemax': total, 'aria-valuenow': done }, h('span', { css: { width: `${total ? Math.round((done / total) * 100) : 0}%` } }));

// How far an epic, or a story with tasks, has got, as ONE meter: its points when anything in it has a size (and how many
// have none yet), otherwise its tasks. Nothing when there is nothing to add up.
function progressOf(node) {
  const total = node.points_total || 0;
  const done = node.points_done || 0;
  if (total) {
    const unsized = node.unsized || 0;
    return [
      'Points',
      h(
        'span',
        { class: 'points-rollup', dataset: { points: String(total) } },
        h('span', { class: 'points-total' }, `${done} of ${pointsWords(total)}`),
        meter(done, total, 'Points done'),
        unsized ? h('span', { class: 'chip size size-none points-unsized' }, `${unsized} not sized`) : null,
      ),
      { class: 'points' },
    ];
  }
  if (!node.progress_total) return null;
  return ['Progress', h('span', { class: 'progress' }, `${node.progress_done} of ${plural(node.progress_total, 'task')} done`, meter(node.progress_done, node.progress_total, 'Tasks done')), { class: 'progress-item' }];
}

// Who holds the item, with Release for a person's own claim (as on the Activity page); or, when a claim ran out with
// no handoff note, that it did.
function claimOf(node, state) {
  if (node.claimed_by) {
    const mine = isMe(node.claimed_by, node.claimed_by_kind) && can('write');
    return [
      'Held by',
      [
        actorLabel(node.claimed_by, node.claimed_by_kind),
        h('span', { class: 'quiet' }, ` until ${moment(node.claim_expires_at)}`),
        mine ? h('button', { type: 'button', class: 'small release-claim', onclick: () => releaseDialog({ node: node.ref, kind: node.kind, title: node.title }, () => state.reload(true)) }, 'Release…') : null,
      ],
      { class: 'claim' },
    ];
  }
  if (!node.lapsed_claim_by) return null;
  return ['Claim', [actorLabel(node.lapsed_claim_by, node.lapsed_claim_by_kind), `’s claim ran out on ${moment(node.lapsed_claim_at)} with no handoff note.`], { class: 'claim lapsed' }];
}

// Fills `root` with the details of the epic, story or task `ref` (story:12) and returns what the page
// needs to know about it. The page's own places are in `options`: `summary` (the properties of the item),
// `actions` (the Edit button), `table` (what is under the item) and `links` (its Links section), both drawn after
// the details, and `drawn()`, called after each drawing. The focus goes to the page's title (`#item-title`).
// `options.force` (default true) is false for a redraw after news, which leaves the card as it is
// when the person started typing meanwhile.
export async function renderCard(root, ref, state = { ref, version: null, dirty: false }, options = {}) {
  const { force = true } = options;
  const seen = state.touches || 0;
  state.skipped = false;
  state.open = state.open || {};
  state.reload = (now = false) => renderCard(root, ref, state, { ...options, force: now });
  // The card stays as it is while the new one is fetched, so nothing flickers.
  if (!root.querySelector('.card-detail')) put(root, loadingView());
  const node = await call('get_node', { node: ref });
  // An epic holds no acceptance criteria. A story or a task has them, and the project's definition of done.
  const holdsCriteria = node.kind !== 'epic';
  // What this item needs first: each one's last handoff note is read here, since an agent taking the
  // work is told them. The board's own copy of the links says which.
  const needed = (store.links || []).filter((link) => link.link_type === 'needs_first' && link.source === ref).map((link) => link.target);
  const [recalled, history, similar, mentions, prerequisites, , , definition] = await Promise.all([
    askRecall({ node: ref }),
    callAll('node_history', { node: ref }),
    askSimilar({ node: ref }),
    fetchMentions(node.kind, node.number),
    Promise.all(
      needed.map((other) =>
        call('get_node', { node: other }).catch((error) => {
          if (!(error instanceof Refusal)) throw error;
          return null;
        }),
      ),
    ),
    loadTargets(),
    holdsCriteria ? ensure('rules') : Promise.resolve(),
    holdsCriteria ? call('get_definition_of_done', {}) : Promise.resolve(null),
  ]);

  // The page has moved on to another item while this one was being fetched.
  if (state.dead) return state;
  // Someone started typing or choosing in the card while this was being fetched: leave it be.
  if (!force && (state.touches || 0) !== seen) {
    state.skipped = true;
    return state;
  }
  state.version = node.version;
  state.dirty = false;
  // What was just read is the newest the board knows of this item: the page's header, the tables
  // and the pager read the store, which only hears of a change at the next poll (or never, while
  // news is held back), so the store takes it too, unless it already holds a newer one.
  const held = store.nodes.get(ref);
  if (held && (node.version ?? 0) >= (held.version ?? 0)) store.nodes.set(ref, { ...held, ...node });
  const worked = node.span_worked_out
    ? `Worked out from what is under it: ${node.span_start && node.span_end ? `${day(node.span_start)} to ${day(node.span_end)}` : node.span_start ? `from ${day(node.span_start)}` : `until ${day(node.span_end)}`}.`
    : null;
  // The status and the priority are chips that change them. What they changed is at a new version, which the
  // edit form has to be saved at; the card is drawn again unless something is typed in it.
  const changed = (result) => {
    state.version = result.version;
    if (!state.dirty) state.reload(true);
  };
  const chipOptions = { version: () => state.version, onChanged: changed };
  // A task's size is its own; a story with tasks and an epic add up what is in them, which is the one meter. A story
  // that has tasks keeps a size of its own out of sight until it has one (the tasks are counted instead of it).
  const adds = addsUp(node);
  const size = node.kind === 'epic' || (adds && !node.size) ? null : sizeControl(node, chipOptions);
  const handoff = handoffsSection(history);
  const fromNeeded = neededHandoffs(prerequisites);
  // What the page already shows, which Similar leaves out: what is above the item and under it, what it is linked to and
  // the notes it has.
  const parent = store.nodes.get(node.parent);
  const known = knownRefs(recalled, [node.parent, parent?.parent, ...childrenOf(ref).map((child) => child.ref), ...(store.links || []).filter((link) => link.source === ref || link.target === ref).flatMap((link) => [link.source, link.target])]);
  const late = lateDaysOf(node);
  const conflict = node.schedule_conflicts.length
    ? h('span', { class: 'chip conflict warn' }, icon('alert', { size: 14 }), `Schedule conflict with ${node.schedule_conflicts.map((ref) => `${codeOf(ref)} ${titleOf(ref)}`).join(', ')}`)
    : null;

  const headerEdit = can('write') ? editButton(() => holder.set(true), 'edit-open') : null;
  const holder = detailsHolder(
    state,
    (editing) =>
      detailsSection({
        body: node.body,
        form: editForm(node, state, () => {
          state.dirty = false;
          holder.set(false);
        }),
        editing,
        extras: [can('write') ? moveForm(node, state) : null],
        onAdd: can('write') ? () => holder.set(true, '#edit-body') : null,
      }),
    (editing) => put(options.actions, editing ? null : headerEdit),
  );

  // The properties of the item, in one row and with only what has a value. A chip that was just used keeps the focus
  // when the row is drawn again.
  const restoreChips = rememberFocus(options.summary);
  put(
    options.summary,
    stateStrip([
      ['Status', statusChip(node, chipOptions), { class: 'status' }],
      ['Priority', priorityChip(node, chipOptions), { class: 'priority' }],
      ['Size', size, { class: 'size' }],
      adds ? progressOf(node) : null,
      can('write') || node.start_on ? ['Start', dateChip(node, 'start_on', chipOptions), { class: 'start' }] : null,
      can('write') || node.target_on ? ['Target', [dateChip(node, 'target_on', chipOptions), late ? dueChip(node, { days: true }) : null], { class: 'target' }] : null,
      claimOf(node, state),
      ['On hold', holdChip(node, node.gates), { class: 'held-by', hideLabel: true }],
      ['Where', whereBlock(node.where), { class: 'where-item' }],
      conflict ? ['Schedule', conflict, { class: 'schedule', hideLabel: true }] : null,
    ]),
    worked ? h('p', { class: 'quiet card-note worked-out' }, worked) : null,
  );
  restoreChips();
  put(options.actions, state.editing ? null : headerEdit);

  // The page's table is put into the new card as it is, which takes the focus out of it, so what has the focus
  // is noted before.
  const restoreFocus = rememberFocus(root);
  // The definition of done is a list of checks to tick when its text has list lines, and a text otherwise.
  const checklist = holdsCriteria && Array.isArray(node.checks) && node.checks.length ? checksSection({ node, definition, state }) : null;
  const view = h(
    'article',
    { class: 'card-detail detail card-page', dataset: { ref: node.ref, kind: node.kind, number: node.number } },
    h('div', { class: 'card-main' }, holder.element, mentionsSection(mentions)),
    asksSection(node, state),
    gatesSection(node, state),
    handoff,
    fromNeeded,
    options.table || null,
    options.links || null,
    node.kind === 'epic' ? null : suggestSection(node, state),
    holdsCriteria ? criteriaSection(node, state) : null,
    holdsCriteria ? (checklist ? checklist.element : definitionSection(definition)) : null,
    contextSection(
      { nodes: [node.ref] },
      recallBlock({ node: ref }, recalled, { open: state.open, onOpen: (name, now) => (state.open[name] = now) }),
      recallTotal(recalled),
    ),
    similarSection(similar, known),
    historySection(history, { opened: (state.opened = state.opened || new Set()) }),
  );
  clear(root).append(view);
  restoreFocus();
  watchEdits(view, state);
  options.drawn?.();
  state.focus = () => document.getElementById('item-title')?.focus();
  return state;
}
