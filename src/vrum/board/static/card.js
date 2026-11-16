// The detail of one epic, story or task: everything about it, and the ways to change it, with its
// requirements, the facts and decisions that concern it, and its history. Every change is the same
// operation an agent would call, with the version the person saw, so a change made meanwhile by an
// agent is refused in the same words and nothing typed is lost.

import { Refusal, call, callAll } from './api.js';
import { actor, notify } from './ctx.js';
import { clear, day, h, moment, plural, put } from './dom.js';
import { parentChoices, openCreate } from './create.js';
import { field, refuse } from './forms.js';
import { icon } from './icons.js';
import { KIND, LINK, PRIORITIES, PRIORITY, STATUS, SETTABLE, priorityChip, statusChip } from './labels.js';
import { renderMarkdown } from './markdown.js';
import { askRecall, recallBlock } from './recall.js';
import { closeButton, countTitle, detailGrid, detailHead, explainStale, historySection, loadingView, recordButtons, rememberFocus, requirementDialog, requirementRow, section, watchEdits } from './records.js';
import { withReferences } from './reference-picker.js';
import { fetchMentions, itemLink, loadTargets, mentionsList } from './references.js';
import { childrenOf, store, sync } from './store.js';

export { itemLink };

const wide = (element) => {
  element.classList.add('wide');
  return element;
};

// -- the form -------------------------------------------------------------------------------------

function editForm(node, state) {
  const title = h('input', { id: 'edit-title', type: 'text', value: node.title, autocomplete: 'off' });
  const body = withReferences(h('textarea', { id: 'edit-body', rows: 6, value: node.body || '' }));
  const status = h(
    'select',
    { id: 'edit-status', value: node.status },
    node.status === 'ready' ? h('option', { value: 'ready', disabled: true }, 'Ready (worked out from the links)') : null,
    SETTABLE.map((name) => h('option', { value: name }, STATUS[name].label)),
  );
  const priority = h(
    'select',
    { id: 'edit-priority', value: node.priority },
    PRIORITIES.map((name) => h('option', { value: name }, PRIORITY[name].label)),
  );
  const start = h('input', { id: 'edit-start', type: 'date', value: day(node.start_on) });
  const target = h('input', { id: 'edit-target', type: 'date', value: day(node.target_on) });
  const problem = h('div', { class: 'problem', role: 'alert' });

  const submit = async (event) => {
    event.preventDefault();
    problem.replaceChildren();
    const args = { actor: actor(), node_id: node.id, expected_version: state.version };
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
      notify(`Saved ${KIND[node.kind].label.toLowerCase()} #${node.id}.`);
      await sync();
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
    h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'primary' }, 'Save changes')),
  );
}

const explain = (error, place, node, state) => explainStale(error, place, state, async () => (await call('get_node', { node_id: node.id })).version);

// -- parent -------------------------------------------------------------------------------------

function moveForm(node, state) {
  if (node.kind === 'epic') return null;
  const choice = h('select', { id: 'move-parent', value: node.parent_id }, parentChoices(node.kind, node.parent_id).filter(Boolean));
  const problem = h('div', { class: 'problem', role: 'alert' });
  return h(
    'form',
    {
      class: 'inline-form move',
      onsubmit: async (event) => {
        event.preventDefault();
        problem.replaceChildren();
        try {
          await call('move_node', { actor: actor(), node_id: node.id, expected_version: state.version, new_parent_id: Number(choice.value) });
          notify(`Moved #${node.id} under #${choice.value}.`);
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

function phrase(item, id) {
  const kind = LINK[item.link_type];
  return item.source_id === id ? kind.label : kind.back;
}

function linksSection(node, items, state) {
  const list = items.length
    ? h(
        'ul',
        { class: 'plain links' },
        items.map((item) => {
          const other = item.source_id === node.id ? item.target_id : item.source_id;
          return h(
            'li',
            { 'data-type': item.link_type },
            h('span', { class: 'relation' }, phrase(item, node.id)),
            itemLink(other),
            h(
              'button',
              {
                type: 'button',
                class: 'ghost small remove-link',
                'aria-label': `Remove the link: ${phrase(item, node.id)} #${other}`,
                onclick: async () => {
                  try {
                    await call('remove_link', { actor: actor(), source_id: item.source_id, target_id: item.target_id, link_type: item.link_type });
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
    { id: 'link-relation' },
    h('option', { value: 'needs_first:out' }, 'needs first'),
    h('option', { value: 'needs_first:in' }, 'is needed first by'),
    h('option', { value: 'supersedes:out' }, 'supersedes'),
    h('option', { value: 'supersedes:in' }, 'is superseded by'),
    h('option', { value: 'duplicates:out' }, 'duplicates'),
    h('option', { value: 'relates_to:out' }, 'relates to'),
  );
  const options = [...store.nodes.values()]
    .filter((other) => other.id !== node.id)
    .map((other) => h('option', { value: `#${other.id} ${other.title}` }));
  const target = h('input', { id: 'link-target', type: 'text', list: 'link-options', autocomplete: 'off', placeholder: 'an id such as #12, or a title', 'aria-label': 'The other item: an id such as #12, or a title' });
  const problem = h('div', { class: 'problem', role: 'alert' });
  const form = h(
    'form',
    {
      class: 'inline-form add-link',
      onsubmit: async (event) => {
        event.preventDefault();
        problem.replaceChildren();
        const found = /^#?(\d+)/.exec(target.value.trim());
        if (!found) {
          refuse(problem, 'Choose the other item: type its id (such as #12) or pick it from the list.');
          return;
        }
        const other = Number(found[1]);
        const [type, direction] = relation.value.split(':');
        const ends = direction === 'out' ? [node.id, other] : [other, node.id];
        try {
          await call('add_link', { actor: actor(), source_id: ends[0], target_id: ends[1], link_type: type });
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
  return section('Links', 'links', list, form);
}

// -- requirements -------------------------------------------------------------------------------

// The requirements of a story are the ones in it. An epic shows those of its stories and a task
// those of its story, so that what has to hold for the work is read where the work is.
function requirementsSection(node) {
  const rules = store.rules || [];
  const inStory = (id) => rules.filter((rule) => rule.story_id === id);
  const list = (items) => h('ul', { class: 'plain requirements' }, items.map(requirementRow));
  if (node.kind === 'story') {
    const own = inStory(node.id);
    return section(
      'Requirements',
      'requirements',
      own.length ? list(own) : h('p', { class: 'quiet none' }, 'No requirements in this story.'),
      h('div', { class: 'panel-actions' }, h('button', { type: 'button', class: 'small add-requirement', onclick: () => requirementDialog({ story: node.id }) }, icon('plus', { size: 14 }), 'Add a requirement…')),
    );
  }
  const stories = node.kind === 'epic' ? childrenOf(node.id) : [store.nodes.get(node.parent_id)].filter(Boolean);
  const groups = stories.map((story) => [story, inStory(story.id)]).filter(([, own]) => own.length);
  return section(
    node.kind === 'epic' ? 'Requirements in its stories' : 'Requirements of its story',
    'requirements',
    groups.length
      ? groups.map(([story, own]) => h('div', { class: 'requirement-group', dataset: { story: story.id } }, h('h4', {}, itemLink(story.id)), list(own)))
      : h('p', { class: 'quiet none' }, node.kind === 'epic' ? 'None of its stories has a requirement yet.' : 'Its story has no requirements yet.'),
  );
}

// -- what else looks like it -----------------------------------------------------------------------

function overlapSection(node, result) {
  if (!result) return null;
  if (result.index.meaning !== 'used') {
    return section('Might be the same as', 'overlaps', h('p', { class: 'quiet' }, result.index.note));
  }
  if (!result.items.length) return section('Might be the same as', 'overlaps', h('p', { class: 'quiet' }, 'Nothing in the index looks like this item.'));
  return section(
    'Might be the same as (suggestions from the index)',
    'overlaps',
    h(
      'ul',
      { class: 'plain overlaps' },
      result.items.map((item) =>
        h('li', { 'data-overlap': item.id }, itemLink(item.id), h('span', { class: 'quiet' }, `${KIND[item.item_type]?.label || item.item_type}, ${item.similarity === null ? 'by words' : `${item.similarity.toFixed(2)} alike`}`)),
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

// Fills `root` with the detail of node `id`. Returns what the panel needs to know about it.
export async function renderCard(root, id, state = { id, version: null, dirty: false }, force = true) {
  const seen = state.touches || 0;
  state.skipped = false;
  state.reload = (now = false) => renderCard(root, id, state, now);
  // The card stays as it is while the new one is fetched, so nothing flickers.
  if (!root.querySelector('.card-detail')) put(root, loadingView());
  const node = await call('get_node', { node_id: id });
  const wantsSimilar = node.kind !== 'task';
  const [links, recalled, history, similar, mentions] = await Promise.all([
    callAll('list_links', { node_id: id }),
    askRecall({ node_id: id }, state.withSimilar ? { with_similar: true } : {}),
    callAll('node_history', { node_id: id }),
    wantsSimilar
      ? call('similar_items', { node_id: id, types: [node.kind], limit: 5 }).catch((error) => {
          if (!(error instanceof Refusal)) throw error;
          return { index: { meaning: 'unavailable', note: error.message }, items: [] };
        })
      : Promise.resolve(null),
    fetchMentions('node', id),
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
  const worked = node.span_worked_out ? `Worked out from what is under it: ${day(node.span_start)} to ${day(node.span_end)}.` : null;
  const marks = [];
  if (node.overdue) marks.push(h('span', { class: 'chip overdue danger' }, icon('alert', { size: 14 }), 'Overdue'));
  if (node.schedule_conflicts.length) {
    marks.push(h('span', { class: 'chip conflict warn' }, icon('alert', { size: 14 }), `Schedule conflict with ${node.schedule_conflicts.map((other) => `#${other}`).join(', ')}`));
  }
  const claim = node.claimed_by
    ? `Held by ${node.claimed_by} until ${moment(node.claim_expires_at)}.`
    : node.lapsed_claim_by
      ? `${node.lapsed_claim_by}’s claim ran out on ${moment(node.lapsed_claim_at)} with no handoff note.`
      : null;
  const waiting = node.waiting_on.length ? node.waiting_on.map((other, position) => [position ? ', ' : '', itemLink(other)]) : null;
  const last = node.last_handoff;
  const parentKind = KIND[store.nodes.get(node.parent_id)?.kind || 'epic'].label.toLowerCase();

  const view = h(
    'article',
    { class: 'card-detail detail', 'data-id': node.id, 'data-kind': node.kind },
    detailHead({
      kind: node.kind,
      id: node.id,
      chips: h('div', { class: 'chips' }, statusChip(node.status), priorityChip(node.priority), marks),
      title: [node.title],
      titleId: 'card-title',
      close: closeButton('Close the details'),
    }),
    node.parent_id !== null ? h('p', { class: 'card-parent' }, `In ${parentKind} `, itemLink(node.parent_id)) : null,
    detailGrid([
      ['Progress', node.progress_total !== null ? progressRow(node) : null],
      ['Dates', [`Start ${day(node.start_on) || 'not set'}. Target ${day(node.target_on) || 'not set'}. `, worked], { class: 'dates' }],
      ['Claim', claim, { class: node.claimed_by ? 'claim' : 'claim quiet' }],
      ['Waiting for', waiting],
    ]),
    node.kind !== 'task'
      ? h('div', { class: 'panel-actions' }, h('button', { type: 'button', class: 'small add-child', onclick: () => openCreate({ kind: node.kind === 'epic' ? 'story' : 'task', parent: node.id }) }, icon('plus', { size: 14 }), node.kind === 'epic' ? 'Add a story' : 'Add a task'))
      : null,
    last ? section('Last handoff note', 'handoff', h('p', {}, `${last.actor}, ${moment(last.at)}, ${last.outcome}: ${last.note}`)) : null,
    section('Details', 'edit', h('div', { class: 'body-text' }, node.body ? renderMarkdown(node.body) : h('span', { class: 'quiet' }, 'No details.')), editForm(node, state), moveForm(node, state)),
    linksSection(node, links, state),
    requirementsSection(node),
    section(
      'Facts and decisions',
      'context',
      recordButtons({ node_ids: [node.id] }),
      recallBlock({ node_id: id }, recalled, { withSimilar: Boolean(state.withSimilar), onSimilar: (on) => (state.withSimilar = on) }),
    ),
    section(countTitle('Mentioned in', mentions.length), 'mentions', mentionsList(mentions)),
    overlapSection(node, similar),
    historySection(history),
  );
  const restoreFocus = rememberFocus(root);
  clear(root).append(view);
  restoreFocus();
  watchEdits(view, state);
  const heading = root.querySelector('#card-title');
  state.focus = () => heading.focus();
  return state;
}
