// The details of one epic, story or task, as the item page draws them: everything about it, and the
// ways to change it, with its acceptance criteria and the definition of done (a story or a task), the
// notes that concern it, and its history. Every change is the same operation an agent would call, with
// the version the person saw,
// so a change made meanwhile by an agent is refused in the same words and nothing typed is lost.
//
// The details are read as text first, with an Edit button in the page's header: the form for the
// title, the words, the status, the priority and the dates opens in their place, with Save changes
// and Cancel, so the same words are never on the page twice.
//
// item.js owns the page: its title, the line saying what the item is, the table of what is under it
// and the places in its header for the state of the item (`options.summary`) and the Edit button
// (`options.actions`), which the card fills. The card lays its sections out in a main column and a
// side column when the page is wide, puts the page's table (`options.table`) after them, and gives
// the acceptance criteria, the notes and the history the whole width, as tables.

import { Refusal, call, callAll } from './api.js';
import { can, isMe, notify, viewLink } from './ctx.js';
import { dateField } from './datepicker.js';
import { clear, day, h, moment, plural, put } from './dom.js';
import { parentChoices } from './create.js';
import { requirementsTable } from './entries.js';
import { field, refuse, sizeSelect } from './forms.js';
import { icon } from './icons.js';
import { KIND, LINK, PRIORITIES, PRIORITY, STATUS, SETTABLE, actorLabel, addsUp, codeOf, nodeWords, pointsWords, readNodeRef } from './labels.js';
import { renderMarkdown } from './markdown.js';
import { askRecall, recallBlock, recallTotal } from './recall.js';
import { contextSection, countTitle, detailsHolder, detailsSection, editButton, emptyField, explainStale, hasDetails, historySection, loadingView, madeAndChanged, mentionsSection, quietSection, releaseDialog, rememberFocus, requirementDialog, sectionWith, section, stateStrip, watchEdits } from './records.js';
import { withReferences } from './reference-picker.js';
import { fetchMentions, loadTargets, nodeLink } from './references.js';
import { checksSection, tickedWords } from './checks.js';
import { dateChip, priorityChip, sizeControl, statusChip } from './status-menu.js';
import { ensure, store, sync } from './store.js';

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
      notify(`Saved ${nodeWords(node.ref)}.`);
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
  const choice = h('select', { id: 'move-parent', value: node.parent }, parentChoices(node.kind, node.parent).filter(Boolean));
  const problem = h('div', { class: 'problem', role: 'alert' });
  return h(
    'form',
    {
      class: 'inline-form move',
      onsubmit: async (event) => {
        event.preventDefault();
        problem.replaceChildren();
        try {
          await call('move_node', { node: node.ref, expected_version: state.version, new_parent: choice.value });
          notify(`Moved ${nodeWords(node.ref)} under ${nodeWords(choice.value)}.`);
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

// -- links --------------------------------------------------------------------------------------

function phrase(item, ref) {
  const kind = LINK[item.link_type];
  return item.source === ref ? kind.label : kind.back;
}

function removeLinkButton(node, item, other) {
  return h(
    'button',
    {
      type: 'button',
      class: 'small remove-link',
      'aria-label': `Remove the link: ${phrase(item, node.ref)} ${codeOf(other)}`,
      onclick: async () => {
        try {
          await call('remove_link', { source: item.source, target: item.target, link_type: item.link_type });
          notify('Removed the link.');
          await sync();
        } catch (error) {
          if (!(error instanceof Refusal)) throw error;
          notify(error.message, { refusal: true });
        }
      },
    },
    'Remove',
  );
}

// The links of the item as a small table (how it is tied, to what, and a way to remove it), and the
// form to add one. A person who can only look sees the table without either.
function linksSection(node, items, state, history = []) {
  const writes = can('write');
  // Who made a link and when is in its history: shown as the row's tooltip, and whole in History.
  const madeBy = (item) => [...history].reverse().find((event) => event.action === 'link_added' && event.node === item.source && event.other_node === item.target && event.after?.link_type === item.link_type);
  const list = items.length
    ? h(
        'table',
        { class: 'links-table links' },
        h('caption', { class: 'sr-only' }, `Links of ${codeOf(node.ref)}`),
        h(
          'tbody',
          {},
          items.map((item) => {
            const other = item.source === node.ref ? item.target : item.source;
            const made = madeBy(item);
            return h(
              'tr',
              { dataset: { type: item.link_type, ref: other }, title: made ? `Added by ${made.actor}, ${moment(made.at)}` : `Added ${moment(item.created_at)}` },
              h('th', { scope: 'row', class: 'relation' }, phrase(item, node.ref)),
              h('td', { class: 'cell-link' }, nodeLink(other)),
              writes ? h('td', { class: 'cell-remove' }, removeLinkButton(node, item, other)) : null,
            );
          }),
        ),
      )
    : h('p', { class: 'quiet none' }, 'None yet.');
  if (!writes) return section(countTitle('Links', items.length), 'links', list);

  const relation = h(
    'select',
    { id: 'link-relation' },
    h('option', { value: 'needs_first:out' }, 'needs first'),
    h('option', { value: 'needs_first:in' }, 'is needed first by'),
    h('option', { value: 'supersedes:out' }, 'supersedes'),
    h('option', { value: 'supersedes:in' }, 'is superseded by'),
    h('option', { value: 'duplicates:out' }, 'duplicates'),
    h('option', { value: 'relates_to:out' }, 'relates to'),
  );
  const options = [...store.nodes.values()]
    .filter((other) => other.ref !== node.ref)
    .map((other) => h('option', { value: `${codeOf(other.ref)} ${other.title}` }));
  const target = h('input', { id: 'link-target', type: 'text', list: 'link-options', autocomplete: 'off', placeholder: 'a code such as S-12, or a title', 'aria-label': 'The other item: a code such as S-12, or a title' });
  const problem = h('div', { class: 'problem', role: 'alert' });
  const form = h(
    'form',
    {
      class: 'inline-form add-link',
      onsubmit: async (event) => {
        event.preventDefault();
        problem.replaceChildren();
        const other = readNodeRef(target.value);
        if (!other) {
          refuse(problem, 'Choose the other item: type its code (such as S-12) or pick it from the list.');
          return;
        }
        const [type, direction] = relation.value.split(':');
        const ends = direction === 'out' ? [node.ref, other] : [other, node.ref];
        try {
          await call('add_link', { source: ends[0], target: ends[1], link_type: type });
          notify('Added the link.');
          target.value = '';
          state.dirty = false;
          await sync();
        } catch (error) {
          if (!(error instanceof Refusal)) throw error;
          refuse(problem, error.message);
        }
      },
    },
    h('label', { for: 'link-relation' }, 'Add a link: this item'),
    h('div', { class: 'inline-row' }, relation),
    h('div', { class: 'inline-row' }, target, h('button', { type: 'submit' }, 'Add')),
    h('datalist', { id: 'link-options' }, options),
    problem,
  );
  return section(countTitle('Links', items.length), 'links', list, form);
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

// What the list of sections says beside "Acceptance criteria": how many are met out of how many apply.
function criteriaBadge(rules) {
  const live = rules.filter((rule) => rule.status !== 'dropped');
  return live.length ? `${live.filter((rule) => rule.status === 'met').length}/${live.length}` : String(rules.length);
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
    requirementsTable(rules, { caption: `Acceptance criteria of ${codeOf(story.ref)}`, onItem: false, plain: true }).element,
  );
}

// The page's section of them, with the way to add one. With none on the item it is an empty field with the
// button inside it, like the details.
function criteriaSection(node, state) {
  const rules = criteriaOn(node.ref);
  const add = can('write')
    ? h('button', { type: 'button', class: 'small add-requirement', onclick: () => requirementDialog({ node: node.ref, onDone: () => state.reload(true) }) }, icon('plus', { size: 14 }), 'Add an acceptance criterion…')
    : null;
  const title = 'Acceptance criteria';
  return sectionWith(
    countTitle(title, rules.length),
    'requirements',
    rules.length ? h('div', { class: 'part-tools' }, h('span', { class: 'quiet criteria-met' }, metWords(rules)), add) : null,
    rules.length ? requirementsTable(rules, { caption: `${title} of ${codeOf(node.ref)}`, onItem: false }).element : emptyField(add),
    fromStory(node),
  );
}

// -- the definition of done --------------------------------------------------------------------------

// The project's definition of done, read only: it is changed once, in Settings, and holds for every story and
// task. With nothing written it is an empty field with the way to Settings.
function definitionSection(definition) {
  const written = hasDetails(definition.body);
  const edit = can('write')
    ? viewLink('definition', {}, { class: 'action-link small edit-definition' }, written ? 'Edit in Settings' : 'Set it in Settings')
    : null;
  return sectionWith('Definition of done', 'definition', written ? edit : null, written ? h('div', { class: 'body-text' }, renderMarkdown(definition.body)) : emptyField(edit));
}

// -- what else looks like it -----------------------------------------------------------------------

function overlapSection(result) {
  if (!result) return null;
  if (result.index.meaning !== 'used') {
    return section('Might be the same as', 'overlaps', h('p', { class: 'quiet' }, result.index.note));
  }
  if (!result.items.length) return quietSection(countTitle('Might be the same as', 0), 'overlaps', h('p', { class: 'quiet none' }, 'None found.'));
  return section(
    countTitle('Might be the same as', result.items.length),
    'overlaps',
    h(
      'ul',
      { class: 'plain overlaps' },
      result.items.map((item) =>
        h('li', { 'data-overlap': item.ref }, nodeLink(item.ref), h('span', { class: 'quiet' }, `${KIND[item.item_type]?.label || item.item_type}, ${item.similarity === null ? 'by words' : `${item.similarity.toFixed(2)} alike`}`)),
      ),
    ),
  );
}

// -- handoff notes ---------------------------------------------------------------------------------

const SHOWN_NOTES = 5; // more than this many handoff notes start with the newest few, and Show all

// One handoff note: who left it, when, how the work ended, and the words in full.
const handoffItem = ({ actor, actor_kind: kind, at, outcome, note }, ...before) =>
  h(
    'li',
    { class: 'handoff-item', dataset: { outcome } },
    ...before,
    h('p', { class: 'handoff-by' }, actorLabel(actor, kind), `, ${moment(at)}, ${outcome}:`),
    note ? renderMarkdown(note, { className: 'markdown handoff-text' }) : null,
  );

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

const progressRow = (node) => {
  const done = node.progress_done;
  const total = node.progress_total;
  return h(
    'span',
    { class: 'progress' },
    `${done} of ${plural(total, 'task')} done`,
    h('span', { class: 'meter', role: 'progressbar', 'aria-label': 'Tasks done', 'aria-valuemin': 0, 'aria-valuemax': total, 'aria-valuenow': done }, h('span', { css: { width: `${total ? Math.round((done / total) * 100) : 0}%` } })),
  );
};

// What an epic, or a story with tasks, adds up to: its points, how many are done as a small meter, and how many items
// in it have no size yet. Nothing when there is nothing to add up.
function pointsRollup(node) {
  const total = node.points_total || 0;
  const done = node.points_done || 0;
  const unsized = node.unsized || 0;
  if (!total && !unsized) return null;
  return h(
    'span',
    { class: 'points-rollup', dataset: { points: String(total) } },
    h('span', { class: 'points-total' }, pointsWords(total)),
    total ? h('span', { class: 'meter', role: 'progressbar', 'aria-label': 'Points done', 'aria-valuemin': 0, 'aria-valuemax': total, 'aria-valuenow': done }, h('span', { css: { width: `${Math.round((done / total) * 100)}%` } })) : null,
    total ? h('span', { class: 'quiet points-done' }, `${done} done`) : null,
    unsized ? h('span', { class: 'chip size size-none points-unsized' }, `${unsized} not sized`) : null,
  );
}

// Fills `root` with the details of the epic, story or task `ref` (story:12) and returns what the page
// needs to know about it. The page's own places are in `options`: `summary` (the state of the item),
// `actions` (the Edit button), `table` (what is under the item, drawn between the details and the
// acceptance criteria) and `drawn(counts)`, called after each drawing with how many entries each section
// has, for the page's list of sections. The focus goes to the page's title (`#item-title`).
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
  const wantsSimilar = node.kind !== 'task';
  // An epic holds no acceptance criteria. A story or a task has them, and the project's definition of done.
  const holdsCriteria = node.kind !== 'epic';
  // What this item needs first: each one's last handoff note is read here, since an agent taking the
  // work is told them. The board's own copy of the links says which.
  const needed = (store.links || []).filter((link) => link.link_type === 'needs_first' && link.source === ref).map((link) => link.target);
  const [links, recalled, history, similar, mentions, prerequisites, , , definition] = await Promise.all([
    callAll('list_links', { node: ref }),
    askRecall({ node: ref }),
    callAll('node_history', { node: ref }),
    wantsSimilar
      ? call('similar_items', { node: ref, types: [node.kind], limit: 5 }).catch((error) => {
          if (!(error instanceof Refusal)) throw error;
          return { index: { meaning: 'unavailable', note: error.message }, items: [] };
        })
      : Promise.resolve(null),
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
  const marks = [];
  if (node.overdue) marks.push(h('span', { class: 'chip overdue danger' }, icon('alert', { size: 14 }), 'Overdue'));
  if (node.schedule_conflicts.length) {
    marks.push(h('span', { class: 'chip conflict warn' }, icon('alert', { size: 14 }), `Schedule conflict with ${node.schedule_conflicts.map(codeOf).join(', ')}`));
  }
  // When the claim began is the newest time its holder took the item, and the holder is the only one who
  // can let it go: a person's own claim has the button here, as on the Activity page.
  const took = node.claimed_by ? [...history].reverse().find((event) => event.action === 'claimed' && event.actor === node.claimed_by) : null;
  const mine = Boolean(node.claimed_by) && isMe(node.claimed_by, node.claimed_by_kind) && can('write');
  const claim = node.claimed_by
    ? [
        'Held by ',
        actorLabel(node.claimed_by, node.claimed_by_kind),
        ` until ${moment(node.claim_expires_at)}`,
        took ? h('span', { class: 'quiet' }, `, took it ${moment(took.at)}`) : null,
        '.',
        mine
          ? h('button', { type: 'button', class: 'small release-claim', onclick: () => releaseDialog({ node: ref, kind: node.kind, title: node.title }, () => state.reload(true)) }, 'Release…')
          : h('span', { class: 'claim-help quiet' }, node.claimed_by_kind === 'agent' ? 'Only it can release it.' : `Only ${node.claimed_by} can release it.`),
      ]
    : node.lapsed_claim_by
      ? [actorLabel(node.lapsed_claim_by, node.lapsed_claim_by_kind), `’s claim ran out on ${moment(node.lapsed_claim_at)} with no handoff note.`]
      : null;
  const claimClass = node.claimed_by ? 'claim' : node.lapsed_claim_by ? 'claim lapsed' : 'claim quiet';
  const waiting = node.waiting_on.length ? node.waiting_on.map((other, position) => [position ? ', ' : '', nodeLink(other)]) : null;

  // The status and the priority are chips that change them. What they changed is at a new version, which the
  // edit form has to be saved at; the card is drawn again unless something is typed in it.
  const changed = (result) => {
    state.version = result.version;
    if (!state.dirty) state.reload(true);
  };
  const chipOptions = { version: () => state.version, onChanged: changed };
  const chips = h('div', { class: 'chips' }, statusChip(node, chipOptions), priorityChip(node, chipOptions), marks);
  const progress = node.progress_total !== null ? progressRow(node) : null;
  // A task's size is its own; a story with tasks and an epic add up what is in them, which is said in points. A story
  // that has tasks keeps a size of its own out of sight until it has one (the tasks are counted instead of it).
  const adds = addsUp(node);
  const size = node.kind === 'epic' || (adds && !node.size) ? null : sizeControl(node, chipOptions);
  const points = adds ? pointsRollup(node) : null;
  const handoff = handoffsSection(history);
  const fromNeeded = neededHandoffs(prerequisites);

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

  // A chip that was just used keeps the focus when the header is drawn again.
  const restoreChips = rememberFocus(options.summary);
  put(
    options.summary,
    chips,
    stateStrip([
      ['Progress', progress],
      ['Size', size, { class: 'size' }],
      ['Points', points, { class: 'points' }],
      ...(can('write') || node.start_on || node.target_on
        ? [
            ['Start', dateChip(node, 'start_on', chipOptions), { class: 'start' }],
            ['Target', dateChip(node, 'target_on', chipOptions), { class: 'target' }],
          ]
        : [['Dates', h('span', { class: 'quiet' }, 'Not set'), { class: 'dates' }]]),
      ['Claim', claim, { class: claimClass }],
      ['Waiting for', waiting],
    ]),
    stateStrip(madeAndChanged(node, history), 'state-meta'),
    worked ? h('p', { class: 'quiet card-note worked-out' }, worked) : null,
  );
  restoreChips();
  put(options.actions, state.editing ? null : headerEdit);

  // The page's table is put into the new card as it is, which takes the focus out of it, so what has the focus
  // is noted before.
  const restoreFocus = rememberFocus(root);
  // The definition of done is a list of checks to tick when its text has list lines, and a text otherwise.
  const checklist =
    holdsCriteria && Array.isArray(node.checks) && node.checks.length
      ? checksSection({
          node,
          definition,
          state,
          onChange: ({ ticked, total }) => {
            state.counts.definition = `${ticked}/${total}`;
            state.countTitles.definition = tickedWords(ticked, total);
            options.drawn?.(state.counts);
          },
        })
      : null;
  const view = h(
    'article',
    { class: 'card-detail detail card-page', dataset: { ref: node.ref, kind: node.kind, number: node.number } },
    h(
      'div',
      { class: 'card-columns' },
      h('div', { class: 'card-main' }, holder.element, handoff, fromNeeded, mentionsSection(mentions), overlapSection(similar)),
      h('div', { class: 'card-side' }, linksSection(node, links, state, history)),
    ),
    options.table || null,
    holdsCriteria ? criteriaSection(node, state) : null,
    holdsCriteria ? (checklist ? checklist.element : definitionSection(definition)) : null,
    contextSection(
      { nodes: [node.ref] },
      recallBlock({ node: ref }, recalled, { open: state.open, onOpen: (name, now) => (state.open[name] = now) }),
      recallTotal(recalled),
    ),
    historySection(history, { opened: (state.opened = state.opened || new Set()) }),
  );
  const own = holdsCriteria ? criteriaOn(node.ref) : [];
  const ticked = checklist ? node.checks.filter((check) => check.checked).length : 0;
  state.counts = {
    edit: hasDetails(node.body) ? null : 'empty',
    ...(holdsCriteria ? { requirements: own.length ? criteriaBadge(own) : 'empty', definition: checklist ? `${ticked}/${node.checks.length}` : hasDetails(definition.body) ? null : 'empty' } : {}),
    context: recallTotal(recalled),
    links: links.length,
    mentions: mentions.length,
    history: history.length,
  };
  state.countTitles = { ...(own.length ? { requirements: `${metWords(own)}` } : {}), ...(checklist ? { definition: tickedWords(ticked, node.checks.length) } : {}) };
  clear(root).append(view);
  restoreFocus();
  watchEdits(view, state);
  options.drawn?.(state.counts);
  state.focus = () => document.getElementById('item-title')?.focus();
  return state;
}
