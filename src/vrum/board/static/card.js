// The details of one epic, story or task: everything about it, and the ways to change it, with its
// requirements, the facts and decisions that concern it, and its history. Every change is the same
// operation an agent would call, with the version the person saw, so a change made meanwhile by an
// agent is refused in the same words and nothing typed is lost.
//
// The details are read as text first, with an Edit button: the form for the title, the words,
// the status, the priority and the dates opens in their place, with Save changes and Cancel, so the
// same words are never on the page twice.
//
// It is drawn in the shared detail panel, and, with `{ page: true }`, as the details of the item's own
// page (item.js): the same sections laid out in a main column and a side column when the page is
// wide. The page owns the title, the line saying what the item is, the table under the card and
// the place in its header for the state of the item (`options.summary`) and the Edit button
// (`options.actions`), which the card fills; so the card there has no heading, no close button and
// no link to itself.

import { Refusal, call, callAll } from './api.js';
import { actor, itemLink, notify, ui } from './ctx.js';
import { clear, day, h, moment, plural, put } from './dom.js';
import { parentChoices, openCreate } from './create.js';
import { field, refuse } from './forms.js';
import { icon } from './icons.js';
import { KIND, LINK, PRIORITIES, PRIORITY, STATUS, SETTABLE, codeOf, nodeCode, nodeWords, parseNodeRef, priorityChip, readNodeRef, statusChip } from './labels.js';
import { askRecall, recallBlock, recallTotal } from './recall.js';
import {
  closeButton,
  contextSection,
  detailGrid,
  detailHead,
  detailsHolder,
  detailsSection,
  editButton,
  explainStale,
  factsStrip,
  historySection,
  loadingView,
  mentionsSection,
  quietSection,
  rememberFocus,
  requirementDialog,
  requirementRow,
  section,
  sectionWith,
  watchEdits,
} from './records.js';
import { withReferences } from './reference-picker.js';
import { fetchMentions, loadTargets, nodeLink } from './references.js';
import { childrenOf, store, sync } from './store.js';

const wide = (element) => {
  element.classList.add('wide');
  return element;
};

// -- the form -------------------------------------------------------------------------------------

// `pre` is put in front of the ids of the fields, so that a card in the detail panel does not repeat
// the ids of the card on the item page beside it. `cancel` closes the form with nothing saved.
function editForm(node, state, pre, cancel) {
  const title = h('input', { id: `${pre}edit-title`, type: 'text', value: node.title, autocomplete: 'off' });
  const body = withReferences(h('textarea', { id: `${pre}edit-body`, rows: 6, value: node.body || '' }));
  const status = h(
    'select',
    { id: `${pre}edit-status`, value: node.status },
    node.status === 'ready' ? h('option', { value: 'ready', disabled: true }, 'Ready (automatic)') : null,
    SETTABLE.map((name) => h('option', { value: name }, STATUS[name].label)),
  );
  const priority = h(
    'select',
    { id: `${pre}edit-priority`, value: node.priority },
    PRIORITIES.map((name) => h('option', { value: name }, PRIORITY[name].label)),
  );
  const start = h('input', { id: `${pre}edit-start`, type: 'date', value: day(node.start_on) });
  const target = h('input', { id: `${pre}edit-target`, type: 'date', value: day(node.target_on) });
  const problem = h('div', { class: 'problem', role: 'alert' });

  const submit = async (event) => {
    event.preventDefault();
    problem.replaceChildren();
    const args = { actor: actor(), node: node.ref, expected_version: state.version };
    const was = { title: node.title, body: node.body || '', status: node.status, priority: node.priority };
    if (title.value !== was.title) args.title = title.value;
    if (body.value !== was.body) args.body = body.value;
    if (status.value !== was.status) args.status = status.value;
    if (priority.value !== was.priority) args.priority = priority.value;
    for (const [name, input, before] of [['start_on', start, day(node.start_on)], ['target_on', target, day(node.target_on)]]) {
      if (input.value === before) continue;
      if (input.value) args[name] = input.value;
      else args[`clear_${name}`] = true;
    }
    if (Object.keys(args).length === 3) {
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
    field('Start', start),
    field('Target', target),
    problem,
    h('div', { class: 'form-actions' }, h('button', { type: 'button', class: 'cancel-edit', onclick: cancel }, 'Cancel'), h('button', { type: 'submit', class: 'primary' }, 'Save changes')),
  );
}

const explain = (error, place, node, state) => explainStale(error, place, state, async () => (await call('get_node', { node: node.ref })).version);

// -- parent -------------------------------------------------------------------------------------

function moveForm(node, state, pre) {
  if (node.kind === 'epic') return null;
  const choice = h('select', { id: `${pre}move-parent`, value: node.parent }, parentChoices(node.kind, node.parent).filter(Boolean));
  const problem = h('div', { class: 'problem', role: 'alert' });
  return h(
    'form',
    {
      class: 'inline-form move',
      onsubmit: async (event) => {
        event.preventDefault();
        problem.replaceChildren();
        try {
          await call('move_node', { actor: actor(), node: node.ref, expected_version: state.version, new_parent: choice.value });
          notify(`Moved ${nodeWords(node.ref)} under ${nodeWords(choice.value)}.`);
          state.dirty = false;
          await sync();
        } catch (error) {
          if (!(error instanceof Refusal)) throw error;
          await explain(error, problem, node, state);
        }
      },
    },
    h('label', { for: `${pre}move-parent` }, node.kind === 'story' ? 'Move to epic' : 'Move to story'),
    h('div', { class: 'inline-row' }, choice, h('button', { type: 'submit' }, 'Move')),
    problem,
  );
}

// -- links --------------------------------------------------------------------------------------

function phrase(item, ref) {
  const kind = LINK[item.link_type];
  return item.source === ref ? kind.label : kind.back;
}

function linksSection(node, items, state, link, pre) {
  const list = items.length
    ? h(
        'ul',
        { class: 'plain links' },
        items.map((item) => {
          const other = item.source === node.ref ? item.target : item.source;
          return h(
            'li',
            { 'data-type': item.link_type },
            h('span', { class: 'relation' }, phrase(item, node.ref)),
            link(other),
            h(
              'button',
              {
                type: 'button',
                class: 'ghost small remove-link',
                'aria-label': `Remove the link: ${phrase(item, node.ref)} ${codeOf(other)}`,
                onclick: async () => {
                  try {
                    await call('remove_link', { actor: actor(), source: item.source, target: item.target, link_type: item.link_type });
                    notify('Removed the link.');
                    await sync();
                  } catch (error) {
                    if (!(error instanceof Refusal)) throw error;
                    notify(error.message, { refusal: true });
                  }
                },
              },
              'Remove',
            ),
          );
        }),
      )
    : h('p', { class: 'quiet' }, 'No links.');

  const relation = h(
    'select',
    { id: `${pre}link-relation` },
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
  const target = h('input', { id: `${pre}link-target`, type: 'text', list: `${pre}link-options`, autocomplete: 'off', placeholder: 'a code such as S-12, or a title', 'aria-label': 'The other item: a code such as S-12, or a title' });
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
          await call('add_link', { actor: actor(), source: ends[0], target: ends[1], link_type: type });
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
    h('label', { for: `${pre}link-relation` }, 'Add a link: this item'),
    h('div', { class: 'inline-row' }, relation),
    h('div', { class: 'inline-row' }, target, h('button', { type: 'submit' }, 'Add')),
    h('datalist', { id: `${pre}link-options` }, options),
    problem,
  );
  return section('Links', 'links', list, form);
}

// -- requirements -------------------------------------------------------------------------------

// The requirements of a story are the ones in it. An epic shows those of its stories and a task
// those of its story, so that what has to hold for the work is read where the work is.
const storiesOf = (node) => (node.kind === 'epic' ? childrenOf(node.ref) : node.kind === 'task' ? [store.nodes.get(node.parent)].filter(Boolean) : [node]);
const inStory = (ref) => (store.rules || []).filter((rule) => rule.story === ref);
const requirementCount = (node) => storiesOf(node).reduce((sum, story) => sum + inStory(story.ref).length, 0);

function requirementsSection(node, link) {
  const list = (items) => h('ul', { class: 'plain requirements' }, items.map(requirementRow));
  if (node.kind === 'story') {
    const own = inStory(node.ref);
    return sectionWith(
      'Requirements',
      'requirements',
      h('button', { type: 'button', class: 'small add-requirement', onclick: () => requirementDialog({ story: node.ref }) }, icon('plus', { size: 14 }), 'Add a requirement…'),
      own.length ? list(own) : h('p', { class: 'quiet none' }, 'No requirements in this story.'),
    );
  }
  const groups = storiesOf(node)
    .map((story) => [story, inStory(story.ref)])
    .filter(([, own]) => own.length);
  return section(
    node.kind === 'epic' ? 'Requirements in its stories' : 'Requirements of its story',
    'requirements',
    groups.length
      ? groups.map(([story, own]) => h('div', { class: 'requirement-group', dataset: { story: story.ref } }, h('h4', {}, link(story.ref)), list(own)))
      : h('p', { class: 'quiet none' }, node.kind === 'epic' ? 'None of its stories has a requirement yet.' : 'Its story has no requirements yet.'),
  );
}

// -- what else looks like it -----------------------------------------------------------------------

function overlapSection(node, result, link) {
  if (!result) return null;
  if (result.index.meaning !== 'used') {
    return section('Might be the same as', 'overlaps', h('p', { class: 'quiet' }, result.index.note));
  }
  if (!result.items.length) return quietSection('Might be the same as', 'overlaps', h('p', { class: 'quiet none' }, 'Nothing in the index looks like this item.'));
  return section(
    'Might be the same as (suggestions from the index)',
    'overlaps',
    h(
      'ul',
      { class: 'plain overlaps' },
      result.items.map((item) =>
        h('li', { 'data-overlap': item.ref }, link(item.ref), h('span', { class: 'quiet' }, `${KIND[item.item_type]?.label || item.item_type}, ${item.similarity === null ? 'by words' : `${item.similarity.toFixed(2)} alike`}`)),
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

// The way to the item's own page, in the header of the panel: an icon, and its words from 640 pixels.
const openFullPage = (ref) =>
  itemLink(ref, { class: 'open-page', 'aria-label': 'Open full page' }, icon('arrow_right', { size: 14 }), h('span', { class: 'open-page-text' }, 'Open full page'));

// Fills `root` with the detail of the epic, story or task `ref` (story:12) and returns what the
// panel needs to know about it. `options.page` draws it as the details of the item's own page: no
// heading, close button, link to the page or line saying where it sits (the page has them), the
// focus goes to the page's title (`#item-title`), and links to other items lead to their own pages.
// A page also gives the places in its header: `options.summary` for the state of the item and
// `options.actions` for the Edit button, and `options.drawn(counts)` is called after each drawing
// with how many entries each section has, for the page's list of sections.
// `options.force` (default true) is false for a redraw after news, which leaves the card as it is
// when the person started typing meanwhile.
export async function renderCard(root, ref, state = { ref, version: null, dirty: false }, options = {}) {
  const { page = false, force = true } = options;
  const seen = state.touches || 0;
  state.skipped = false;
  state.reload = (now = false) => renderCard(root, ref, state, { ...options, force: now });
  // The card stays as it is while the new one is fetched, so nothing flickers.
  if (!root.querySelector('.card-detail')) put(root, loadingView());
  const node = await call('get_node', { node: ref });
  const wantsSimilar = node.kind !== 'task';
  const [links, recalled, history, similar, mentions] = await Promise.all([
    callAll('list_links', { node: ref }),
    askRecall({ node: ref }, state.withSimilar ? { with_similar: true } : {}),
    callAll('node_history', { node: ref }),
    wantsSimilar
      ? call('similar_items', { node: ref, types: [node.kind], limit: 5 }).catch((error) => {
          if (!(error instanceof Refusal)) throw error;
          return { index: { meaning: 'unavailable', note: error.message }, items: [] };
        })
      : Promise.resolve(null),
    fetchMentions(node.kind, node.number),
    loadTargets(),
  ]);

  // The panel has moved on to another card while this one was being fetched.
  if (state.dead) return state;
  // Someone started typing or choosing in the card while this was being fetched: leave it be.
  if (!force && (state.touches || 0) !== seen) {
    state.skipped = true;
    return state;
  }
  state.version = node.version;
  state.dirty = false;
  // A card in the panel beside the item's page would repeat the page's ids.
  const pre = !page && ui.page === 'item' ? 'panel-' : '';
  const link = (other) => nodeLink(other, { page });
  const worked = node.span_worked_out
    ? `Worked out from what is under it: ${node.span_start && node.span_end ? `${day(node.span_start)} to ${day(node.span_end)}` : node.span_start ? `from ${day(node.span_start)}` : `until ${day(node.span_end)}`}.`
    : null;
  const marks = [];
  if (node.overdue) marks.push(h('span', { class: 'chip overdue danger' }, icon('alert', { size: 14 }), 'Overdue'));
  if (node.schedule_conflicts.length) {
    marks.push(h('span', { class: 'chip conflict warn' }, icon('alert', { size: 14 }), `Schedule conflict with ${node.schedule_conflicts.map(codeOf).join(', ')}`));
  }
  const claim = node.claimed_by
    ? `Held by ${node.claimed_by} until ${moment(node.claim_expires_at)}.`
    : node.lapsed_claim_by
      ? `${node.lapsed_claim_by}’s claim ran out on ${moment(node.lapsed_claim_at)} with no handoff note.`
      : null;
  const claimClass = node.claimed_by ? 'claim' : node.lapsed_claim_by ? 'claim lapsed' : 'claim quiet';
  const waiting = node.waiting_on.length ? node.waiting_on.map((other, position) => [position ? ', ' : '', link(other)]) : null;
  const last = node.last_handoff;
  const parentKind = node.parent === null ? null : KIND[parseNodeRef(node.parent).kind].label;

  const chips = h('div', { class: 'chips' }, statusChip(node.status), priorityChip(node.priority), marks);
  const progress = node.progress_total !== null ? progressRow(node) : null;
  const where = page || node.parent === null ? null : h('p', { class: 'card-parent' }, `In ${parentKind.toLowerCase()} `, link(node.parent));
  // On the item page the table under the card has its own way to add one.
  const addChild =
    node.kind !== 'task' && !page
      ? h('div', { class: 'panel-actions' }, h('button', { type: 'button', class: 'small add-child', onclick: () => openCreate({ kind: node.kind === 'epic' ? 'story' : 'task', parent: node.ref }) }, icon('plus', { size: 14 }), node.kind === 'epic' ? 'Add a story' : 'Add a task'))
      : null;
  const handoff = last ? section('Last handoff note', 'handoff', h('p', {}, `${last.actor}, ${moment(last.at)}, ${last.outcome}: ${last.note}`)) : null;

  const headerEdit = page ? editButton(() => holder.set(true), `${pre}edit-open`) : null;
  const holder = detailsHolder(
    state,
    (editing) =>
      detailsSection({
        body: node.body,
        form: editForm(node, state, pre, () => {
          state.dirty = false;
          holder.set(false);
        }),
        editing,
        edit: page ? null : () => holder.set(true),
        extras: [moveForm(node, state, pre)],
      }),
    (editing) => {
      if (!page || !options.actions) return;
      put(options.actions, editing ? null : headerEdit);
      if (!editing) headerEdit.focus();
    },
  );
  const linked = linksSection(node, links, state, link, pre);
  const requirements = requirementsSection(node, link);
  const context = contextSection(
    { nodes: [node.ref] },
    recallBlock({ node: ref }, recalled, { withSimilar: Boolean(state.withSimilar), onSimilar: (on) => (state.withSimilar = on) }),
  );
  const mentioned = mentionsSection(mentions);
  const overlaps = overlapSection(node, similar, link);
  const past = historySection(history);

  let view;
  if (page) {
    // The state of the item goes up into the page's header, with the Edit button.
    if (options.summary) {
      put(
        options.summary,
        chips,
        factsStrip([
          ['Progress', progress],
          ...(node.start_on || node.target_on
            ? [
                ['Start', day(node.start_on) || h('span', { class: 'quiet' }, 'Not set'), { class: 'start' }],
                ['Target', day(node.target_on) || h('span', { class: 'quiet' }, 'Not set'), { class: 'target' }],
              ]
            : [['Dates', h('span', { class: 'quiet' }, 'Not set'), { class: 'dates' }]]),
          ['Claim', claim, { class: claimClass }],
          ['Waiting for', waiting],
        ]),
        worked ? h('p', { class: 'quiet card-note worked-out' }, worked) : null,
      );
    }
    if (options.actions) put(options.actions, state.editing ? null : headerEdit);
    view = h(
      'article',
      { class: 'card-detail detail card-page', dataset: { ref: node.ref, kind: node.kind, number: node.number } },
      h('div', { class: 'card-columns' }, h('div', { class: 'card-main' }, handoff, holder.element, requirements), h('div', { class: 'card-side' }, linked, mentioned, overlaps)),
      context,
      past,
    );
  } else {
    const head = detailHead({
      kind: node.kind,
      code: nodeCode(node.kind, node.number),
      chips,
      title: [node.title],
      titleId: 'card-title',
      close: closeButton('Close the details'),
      actions: openFullPage(node.ref),
    });
    const summary = detailGrid([
      ['Progress', progress],
      ['Dates', [`Start ${day(node.start_on) || 'not set'}. Target ${day(node.target_on) || 'not set'}. `, worked], { class: 'dates' }],
      ['Claim', claim, { class: claimClass }],
      ['Waiting for', waiting],
    ]);
    view = h(
      'article',
      { class: 'card-detail detail', dataset: { ref: node.ref, kind: node.kind, number: node.number } },
      head,
      where,
      summary,
      addChild,
      handoff,
      holder.element,
      linked,
      requirements,
      context,
      mentioned,
      overlaps,
      past,
    );
  }
  state.counts = { requirements: requirementCount(node), context: recallTotal(recalled), links: links.length, mentions: mentions.length, history: history.length };
  const restoreFocus = rememberFocus(root);
  clear(root).append(view);
  restoreFocus();
  watchEdits(view, state);
  options.drawn?.(state.counts);
  // The panel's title, or, on the item's page, the page's own title, which is outside the card.
  state.focus = () => (page ? document.getElementById('item-title') : root.querySelector('#card-title'))?.focus();
  return state;
}
