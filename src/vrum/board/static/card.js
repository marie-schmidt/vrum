// The detail of one epic, story or task: everything about it, and the ways to change it, with its
// requirements, the facts and decisions that concern it, and its history. Every change is the same
// operation an agent would call, with the version the person saw, so a change made meanwhile by an
// agent is refused in the same words and nothing typed is lost.

import { Refusal, call, callAll } from './api.js';
import { actor, notify } from './ctx.js';
import { clear, day, h, moment, plural } from './dom.js';
import { parentChoices, openCreate } from './create.js';
import { KIND, LINK, PRIORITIES, PRIORITY, STATUS, SETTABLE, kindChip, priorityChip, statusChip } from './labels.js';
import { renderMarkdown } from './markdown.js';
import { askRecall, recallBlock } from './recall.js';
import { closeButton, explainStale, historySection, recordButtons, rememberFocus, requirementDialog, requirementRow, section, watchEdits } from './records.js';
import { withReferences } from './reference-picker.js';
import { fetchMentions, itemLink, loadTargets, mentionsList } from './references.js';
import { childrenOf, store, sync } from './store.js';

export { itemLink };

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
    for (const [field, input, before] of [['start_on', start, day(node.start_on)], ['target_on', target, day(node.target_on)]]) {
      if (input.value === before) continue;
      if (input.value) args[field] = input.value;
      else args[`clear_${field}`] = true;
    }
    if (Object.keys(args).length === 3) {
      problem.textContent = 'Nothing has been changed yet, so there is nothing to save.';
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

  const field = (label, input, id) => h('div', { class: 'field' }, h('label', { for: id }, label), input);
  return h(
    'form',
    { class: 'edit', onsubmit: submit },
    field('Title', title, 'edit-title'),
    field('Details', body, 'edit-body'),
    h('div', { class: 'row' }, field('Status', status, 'edit-status'), field('Priority', priority, 'edit-priority')),
    h('div', { class: 'row' }, field('Start', start, 'edit-start'), field('Target', target, 'edit-target')),
    problem,
    h('div', { class: 'buttons' }, h('button', { type: 'submit', class: 'primary' }, 'Save changes')),
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
      class: 'inline-form',
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
    choice,
    h('button', { type: 'submit' }, 'Move'),
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
        { class: 'links' },
        items.map((item) => {
          const other = item.source_id === node.id ? item.target_id : item.source_id;
          return h(
            'li',
            { 'data-type': item.link_type },
            h('span', { class: 'relation' }, phrase(item, node.id)),
            ' ',
            itemLink(other),
            ' ',
            h(
              'button',
              {
                type: 'button',
                class: 'link remove-link',
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
  const target = h('input', { id: 'link-target', type: 'text', list: 'link-options', autocomplete: 'off', placeholder: 'an id such as #12, or a title' });
  const problem = h('div', { class: 'problem', role: 'alert' });
  const form = h(
    'form',
    {
      class: 'inline-form',
      onsubmit: async (event) => {
        event.preventDefault();
        problem.replaceChildren();
        const found = /^#?(\d+)/.exec(target.value.trim());
        if (!found) {
          problem.textContent = 'Choose the other item: type its id (such as #12) or pick it from the list.';
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
          problem.textContent = error.message;
        }
      },
    },
    h('label', { for: 'link-relation' }, 'Add a link: this item'),
    relation,
    target,
    h('datalist', { id: 'link-options' }, options),
    h('button', { type: 'submit' }, 'Add'),
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
      h('p', {}, h('button', { type: 'button', class: 'add-requirement', onclick: () => requirementDialog({ story: node.id }) }, 'Add a requirement…')),
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
      { class: 'plain' },
      result.items.map((item) =>
        h('li', { 'data-overlap': item.id }, itemLink(item.id), ` ${KIND[item.item_type]?.label || item.item_type}, ${item.similarity === null ? 'by words' : `${item.similarity.toFixed(2)} alike`}`),
      ),
    ),
  );
}

// -- the whole card -----------------------------------------------------------------------------

// Fills `root` with the detail of node `id`. Returns what the panel needs to know about it.
export async function renderCard(root, id, state = { id, version: null, dirty: false }, force = true) {
  const seen = state.touches || 0;
  state.skipped = false;
  state.reload = (now = false) => renderCard(root, id, state, now);
  // The card stays as it is while the new one is fetched, so nothing flickers.
  if (!root.querySelector('.card-detail')) clear(root).append(h('p', { class: 'quiet' }, 'Loading…'));
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
  const dates = [];
  if (node.span_worked_out) dates.push(`Worked out from what is under it: ${day(node.span_start)} to ${day(node.span_end)}.`);
  const marks = [];
  if (node.overdue) marks.push(h('span', { class: 'chip overdue' }, '! Overdue'));
  if (node.schedule_conflicts.length) {
    marks.push(h('span', { class: 'chip conflict' }, `! Schedule conflict with ${node.schedule_conflicts.map((other) => `#${other}`).join(', ')}`));
  }
  const claim = node.claimed_by
    ? h('p', {}, `Held by ${node.claimed_by} until ${moment(node.claim_expires_at)}.`)
    : node.lapsed_claim_by
      ? h('p', { class: 'quiet' }, `${node.lapsed_claim_by}’s claim ran out on ${moment(node.lapsed_claim_at)} with no handoff note.`)
      : null;
  const waiting = node.waiting_on.length
    ? h('p', {}, 'Waiting for ', node.waiting_on.map((other, position) => [position ? ', ' : '', itemLink(other)]))
    : null;
  const last = node.last_handoff;

  const view = h(
    'article',
    { class: 'card-detail', 'data-id': node.id, 'data-kind': node.kind },
    h(
      'header',
      {},
      h('div', { class: 'chips' }, kindChip(node.kind), statusChip(node.status), priorityChip(node.priority), marks),
      h('h2', { id: 'card-title', tabindex: -1 }, `#${node.id} ${node.title}`),
      closeButton('Close the details'),
    ),
    node.parent_id !== null ? h('p', {}, `In ${KIND[store.nodes.get(node.parent_id)?.kind || 'epic'].label.toLowerCase()} `, itemLink(node.parent_id)) : null,
    node.progress_total !== null
      ? h('p', {}, `Progress: ${node.progress_done} of ${plural(node.progress_total, 'task')} done`, h('progress', { max: Math.max(node.progress_total, 1), value: node.progress_done, 'aria-label': 'Tasks done' }))
      : null,
    h('p', { class: 'dates' }, `Start ${day(node.start_on) || 'not set'}. Target ${day(node.target_on) || 'not set'}. `, dates),
    claim,
    waiting,
    last ? section('Last handoff note', 'handoff', h('p', {}, `${last.actor}, ${moment(last.at)}, ${last.outcome}: ${last.note}`)) : null,
    section('Details and changes', 'edit', h('div', { class: 'body-text' }, node.body ? renderMarkdown(node.body) : h('span', { class: 'quiet' }, 'No details.')), editForm(node, state), moveForm(node, state)),
    node.kind !== 'task'
      ? h('p', {}, h('button', { type: 'button', class: 'add-child', onclick: () => openCreate({ kind: node.kind === 'epic' ? 'story' : 'task', parent: node.id }) }, node.kind === 'epic' ? 'Add a story' : 'Add a task'))
      : null,
    linksSection(node, links, state),
    requirementsSection(node),
    section(
      'Facts and decisions',
      'context',
      recordButtons({ node_ids: [node.id] }),
      recallBlock({ node_id: id }, recalled, { withSimilar: Boolean(state.withSimilar), onSimilar: (on) => (state.withSimilar = on) }),
    ),
    section(`Mentioned in (${mentions.length})`, 'mentions', mentionsList(mentions)),
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
