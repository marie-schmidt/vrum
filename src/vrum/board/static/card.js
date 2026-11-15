// The card of one epic, story or task: everything about it, and the ways to change it. Every
// change is the same operation an agent would call, with the version the person saw, so a change
// made meanwhile by an agent is refused in the same words and nothing typed is lost.

import { Refusal, call, callAll } from './api.js';
import { actor, notify, ui } from './ctx.js';
import { h, clear, day, moment, plural } from './dom.js';
import { parentChoices, openCreate } from './create.js';
import { KIND, LINK, LINK_TYPES, PRIORITIES, PRIORITY, STATUS, SETTABLE, kindChip, priorityChip, statusChip } from './labels.js';
import { store, sync, titleOf } from './store.js';

const link = (id) =>
  h('a', { href: `#/${ui.page}?${withCard(id)}`, class: 'item-link', 'data-id': id }, `#${id} ${titleOf(id)}`);

function withCard(id) {
  const params = new URLSearchParams(ui.params);
  params.set('card', String(id));
  params.delete('panel');
  return params.toString();
}

// -- the form -------------------------------------------------------------------------------------

function editForm(node, state) {
  const title = h('input', { id: 'edit-title', type: 'text', value: node.title, autocomplete: 'off' });
  const body = h('textarea', { id: 'edit-body', rows: 6, value: node.body || '' });
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

// Say what the server said and, when the card is no longer the one the person read, offer the
// current version. What was typed stays where it is until they choose.
async function explain(error, place, node, state) {
  place.replaceChildren(h('span', { class: 'refusal-text' }, error.message));
  const now = await call('get_node', { node_id: node.id }).catch(() => null);
  if (now && now.version !== state.version) {
    place.append(
      ' ',
      h(
        'button',
        {
          type: 'button',
          class: 'link load-current',
          onclick: () => {
            state.dirty = false;
            state.reload(true);
          },
        },
        `Load the current version (${now.version}), and lose what I typed`,
      ),
    );
  }
}

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
            link(other),
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

// -- what is known about it ---------------------------------------------------------------------

function origin(item) {
  const words = { stated: 'stated by a person or agent', extracted: 'proposed by a language model from a quoted passage', derived: 'drawn up by a language model from evidence' };
  return words[item.origin] || item.origin;
}

function decisionItem(entry) {
  const decision = entry.decision;
  return h(
    'li',
    { 'data-decision': decision.id },
    h('strong', {}, `${KIND[decision.kind].label} #${decision.id}: ${decision.title}`),
    h('div', { class: 'quiet' }, `Decided by ${decision.decided_by} on ${day(decision.decided_on)}.`),
    h('div', {}, decision.text),
    h('div', { class: 'quiet' }, `Why: ${decision.reason}`),
  );
}

function memoryItem(entry) {
  const memory = entry.memory;
  const belief = memory.type === 'belief';
  return h(
    'li',
    { 'data-memory': memory.id, class: belief ? 'belief' : 'fact' },
    h(
      'strong',
      {},
      belief ? h('span', { class: 'chip suggestion' }, '◇ Suggestion') : h('span', { class: 'chip' }, '◈ Fact'),
      ' ',
      `#${memory.id}`,
      memory.stale ? h('span', { class: 'chip stale' }, '! Stale') : null,
    ),
    h('div', {}, memory.text),
    h(
      'div',
      { class: 'quiet' },
      `Origin: ${memory.origin}, ${origin(memory)}. Recorded by ${memory.recorded_by}.`,
      belief ? ` Rests on ${plural(memory.evidence_count, 'piece')} of evidence.` : memory.source ? ` Source: ${memory.source}.` : '',
      memory.confirmed_by ? ` Confirmed by ${memory.confirmed_by}.` : belief ? ' Nobody has confirmed it.' : '',
    ),
    memory.stale ? h('div', { class: 'quiet' }, `Stale: ${memory.stale_because.join(' ')}`) : null,
  );
}

function recallSections(recalled) {
  const parts = [];
  const add = (title, section, render, note) => {
    if (!section.items.length) return;
    const more = section.count > section.items.length ? h('p', { class: 'quiet' }, `${section.count - section.items.length} more not shown.`) : null;
    parts.push(section_(title, h('ul', { class: 'plain' }, section.items.map(render)), more, note));
  };
  add('Decisions in force', recalled.active_decisions, decisionItem);
  add('Principles in force', recalled.active_principles, decisionItem, 'Principles apply to everything.');
  add('Facts', recalled.facts, memoryItem);
  add('Beliefs: suggestions, not decisions', recalled.beliefs, memoryItem);
  add('Stale beliefs: their evidence changed', recalled.stale_beliefs, memoryItem);
  return parts;
}

function section_(title, ...content) {
  return h('section', { class: 'part' }, h('h3', {}, title), content);
}
const section = (title, key, ...content) => h('section', { class: 'part', 'data-part': key }, h('h3', {}, title), content);

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
        h('li', { 'data-overlap': item.id }, link(item.id), ` ${KIND[item.item_type]?.label || item.item_type}, ${item.similarity === null ? 'by words' : `${item.similarity.toFixed(2)} alike`}`),
      ),
    ),
  );
}

function describe(event) {
  const changed = Object.keys(event.after).filter((key) => key !== 'version');
  const words = {
    create: 'created it',
    update: `changed ${changed.map((key) => `${key.replace('_', ' ')} (${event.before && key in event.before ? `${event.before[key] ?? 'empty'} → ` : ''}${event.after[key] ?? 'empty'})`).join(', ')}`,
    move: `moved it to #${event.after.parent_id}${event.before ? ` from #${event.before.parent_id}` : ''}`,
    link_added: 'added a link',
    link_removed: 'removed a link',
    claimed: 'took it',
    claim_released: 'let go of it',
    handoff: `finished with a handoff note (${event.after.outcome})`,
  };
  return words[event.action] || event.action.replace(/_/g, ' ');
}

function historySection(events) {
  const newest = [...events].reverse();
  const shown = newest.slice(0, 10);
  const row = (event) =>
    h('li', { 'data-event': event.id }, h('span', { class: 'quiet' }, `${moment(event.at)} `), h('strong', {}, event.actor), ` ${describe(event)}. `, h('span', { class: 'quiet' }, `Version ${event.after.version ?? ''}`.trim()));
  const list = h('ul', { class: 'plain history' }, shown.map(row));
  const more =
    newest.length > shown.length
      ? h('button', { type: 'button', class: 'link', onclick: (event) => { list.replaceChildren(...newest.map(row)); event.target.remove(); } }, `Show all ${newest.length}`)
      : null;
  return section('History', 'history', list, more);
}

// -- the whole card -----------------------------------------------------------------------------

// Fills `root` with the card of node `id`. Returns what the panel needs to know about it.
export async function renderCard(root, id, state = { id, version: null, dirty: false }, force = true) {
  const seen = state.touches || 0;
  state.skipped = false;
  state.reload = (now = false) => renderCard(root, id, state, now);
  // The card stays as it is while the new one is fetched, so nothing flickers.
  if (!root.querySelector('.card-detail')) clear(root).append(h('p', { class: 'quiet' }, 'Loading…'));
  const node = await call('get_node', { node_id: id });
  const wantsSimilar = node.kind !== 'task';
  const [links, recalled, history, rules, similar] = await Promise.all([
    callAll('list_links', { node_id: id }),
    call('recall', { node_id: id }),
    callAll('node_history', { node_id: id }),
    node.kind === 'story' ? callAll('list_rules', { story_id: id }) : Promise.resolve(null),
    wantsSimilar
      ? call('similar_items', { node_id: id, types: [node.kind], limit: 5 }).catch((error) => {
          if (!(error instanceof Refusal)) throw error;
          return { index: { meaning: 'unavailable', note: error.message }, items: [] };
        })
      : Promise.resolve(null),
  ]);

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
    ? h('p', {}, 'Waiting for ', node.waiting_on.map((other, position) => [position ? ', ' : '', link(other)]))
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
      h('button', { type: 'button', class: 'close', 'aria-label': 'Close the card', onclick: () => ui.navigate({ card: null }) }, '✕'),
    ),
    node.parent_id !== null ? h('p', {}, `In ${KIND[store.nodes.get(node.parent_id)?.kind || 'epic'].label.toLowerCase()} `, link(node.parent_id)) : null,
    node.progress_total !== null
      ? h('p', {}, `Progress: ${node.progress_done} of ${plural(node.progress_total, 'task')} done`, h('progress', { max: Math.max(node.progress_total, 1), value: node.progress_done, 'aria-label': 'Tasks done' }))
      : null,
    h('p', { class: 'dates' }, `Start ${day(node.start_on) || 'not set'}. Target ${day(node.target_on) || 'not set'}. `, dates),
    claim,
    waiting,
    last ? section('Last handoff note', 'handoff', h('p', {}, `${last.actor}, ${moment(last.at)}, ${last.outcome}: ${last.note}`)) : null,
    section('Details and changes', 'edit', h('div', { class: 'body-text' }, node.body || h('span', { class: 'quiet' }, 'No details.')), editForm(node, state), moveForm(node, state)),
    node.kind !== 'task'
      ? h('p', {}, h('button', { type: 'button', class: 'add-child', onclick: () => openCreate({ kind: node.kind === 'epic' ? 'story' : 'task', parent: node.id }) }, node.kind === 'epic' ? 'Add a story' : 'Add a task'))
      : null,
    linksSection(node, links, state),
    rules
      ? section('Rules in this story', 'rules', rules.length ? h('ul', { class: 'plain' }, rules.map((rule) => h('li', { 'data-rule': rule.id }, `Rule #${rule.id} (${rule.status}): ${rule.title}`))) : h('p', { class: 'quiet' }, 'No rules.'))
      : null,
    recallSections(recalled),
    overlapSection(node, similar),
    historySection(history),
  );
  clear(root).append(view);
  // Anything typed or chosen in the card from now on is not replaced by a fresh copy of it.
  view.addEventListener('input', () => {
    state.dirty = true;
    state.touches = (state.touches || 0) + 1;
  });
  view.addEventListener('change', () => {
    state.dirty = true;
    state.touches = (state.touches || 0) + 1;
  });
  const heading = root.querySelector('#card-title');
  state.focus = () => heading.focus();
  return state;
}

export { link as itemLink };
