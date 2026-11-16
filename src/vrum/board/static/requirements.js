// The Graph's requirements: every requirement in scope, grouped by the story it is in, with the
// ones that are in no story first, and the coverage of the stories. A person can write one, change
// it, and put it in a story or move it to another, by dragging it onto a story or from a dialog.
// Each of those is the same operation an agent calls, at the version the person saw, so the
// server decides and a refusal reads as it does for an agent. The list keeps no data of its own.

import { Refusal, call } from './api.js';
import { actor, hrefTo, notify, ui } from './ctx.js';
import { h, plural, put } from './dom.js';
import { renderMarkdown } from './markdown.js';
import { mentionedInDetails } from './references.js';
import { requirementDialog, openRecord, placeRequirementDialog } from './records.js';
import { epicOf, store, sync } from './store.js';
import { TYPES } from './graph-data.js';
import { connectionsDetails, matchesText, sectionFrame, selectOf, textInput, walkRows } from './list-parts.js';

const STATUS_WORDS = { held: ['Held', 'A requirement that stands'], settled: ['Settled', 'Agreed and done with'], dropped: ['Dropped', 'No longer a requirement'] };
const STATUS_FILTER = [
  ['live', 'Held and settled'],
  ['held', 'Held'],
  ['settled', 'Settled'],
  ['dropped', 'Dropped'],
  ['all', 'All'],
];
const PLACEMENT_FILTER = [
  ['', 'In a story or not'],
  ['unplaced', 'In no story'],
];

const byId = (a, b) => a.id - b.id;

// How the requirements stand against the stories: what the coverage report says, worked out from
// the board's copy so that it is always the same as the lists.
function coverage() {
  const live = (store.rules || []).filter((rule) => rule.status !== 'dropped');
  const covered = new Set(live.filter((rule) => rule.story_id !== null).map((rule) => rule.story_id));
  return {
    unplaced: live.filter((rule) => rule.story_id === null).length,
    stories: [...store.nodes.values()].filter((node) => node.kind === 'story' && node.status !== 'dropped' && !covered.has(node.id)).sort(byId),
  };
}

export function createRequirements() {
  const state = { status: 'live', placement: '', text: '' };
  let last = null; // what the page last handed over
  let dragging = null; // the requirement being dragged
  const status = selectOf(STATUS_FILTER, state.status);
  const placement = selectOf(PLACEMENT_FILTER, state.placement);
  const text = textInput('Words in a requirement');
  const frame = sectionFrame({
    name: 'requirements',
    title: 'Requirements',
    buttons: [h('button', { type: 'button', id: 'new-requirement', class: 'primary', onclick: () => requirementDialog({}) }, 'New requirement')],
    controls: [
      ['status', 'Status', status],
      ['placement', 'Placed', placement],
      ['text', 'Find', text],
    ],
    onFilter: () => {
      state.status = status.value;
      state.placement = placement.value;
      state.text = text.value;
      if (last) draw(last);
    },
  });
  const summary = h('p', { class: 'gv-summary', id: 'requirements-summary', role: 'status' });
  const unplaced = h('div', { id: 'unplaced-group' });
  const groups = h('div', { id: 'requirement-groups' });
  const without = h('details', { class: 'gv-without', id: 'stories-without-requirements' });
  frame.body.append(summary, unplaced, groups, without);
  walkRows(frame.element);

  // -- the rows --------------------------------------------------------------------------------

  function row(dot, ctx) {
    const rule = dot.record;
    const [word, hint] = STATUS_WORDS[rule.status] || [rule.status, ''];
    const chosen = ctx.cfg.sel?.key === dot.key;
    return h(
      'li',
      {
        class: `gv-record requirement status-${rule.status}${chosen ? ' selected' : ''}`,
        draggable: 'true',
        'aria-current': chosen ? 'true' : null,
        dataset: { key: dot.key, kind: 'rule', type: 'rule', id: rule.id, status: rule.status, story: rule.story_id ?? '' },
        onclick: (event) => {
          if (!event.target.closest('button, a, summary, details, select, input')) openRecord('rule', rule.id);
        },
        ondragstart: (event) => {
          dragging = rule.id;
          event.dataTransfer.setData('application/x-vrum-rule', String(rule.id));
          event.dataTransfer.setData('text/plain', `requirement ${rule.id}`);
          event.dataTransfer.effectAllowed = 'move';
        },
        ondragend: () => {
          dragging = null;
          document.querySelectorAll('.over').forEach((el) => el.classList.remove('over'));
        },
      },
      h(
        'div',
        { class: 'gv-record-head' },
        h('span', { class: 'chip kind kind-rule' }, h('span', { 'aria-hidden': 'true' }, TYPES.rule.sign), ` Requirement #${rule.id}`),
        h('span', { class: `chip rule-status status-${rule.status}`, title: hint }, word),
        h('button', { type: 'button', class: 'link gv-open', onclick: () => openRecord('rule', rule.id) }, rule.title),
      ),
      rule.body ? renderMarkdown(rule.body, { className: 'markdown entry-text' }) : null,
      h(
        'div',
        { class: 'gv-actions' },
        h('button', { type: 'button', class: 'edit-requirement', onclick: () => requirementDialog({ rule }) }, 'Edit'),
        h('button', { type: 'button', class: 'put-requirement', onclick: () => placeRequirementDialog(rule) }, rule.story_id === null ? 'Put in a story…' : 'Move to another story…'),
      ),
      mentionedInDetails('rule', rule.id),
      connectionsDetails(dot, ctx),
    );
  }

  function keeps(rule) {
    if (state.status === 'live' && rule.status === 'dropped') return false;
    if (!['live', 'all'].includes(state.status) && rule.status !== state.status) return false;
    if (state.placement === 'unplaced' && rule.story_id !== null) return false;
    return matchesText(state.text, rule.id, rule.title, rule.body ?? rule.body_preview, rule.status);
  }

  // -- drawing ---------------------------------------------------------------------------------

  function draw(ctx) {
    last = ctx;
    const hadFocus = frame.body.contains(document.activeElement) ? document.activeElement.closest('[data-key]')?.dataset.key : null;
    const inScope = [...ctx.shown.dots.values()].filter((dot) => dot.type === 'rule');
    const kept = inScope.filter((dot) => keeps(dot.record));
    frame.setCount(kept.length, inScope.length);

    const cover = coverage();
    summary.replaceChildren(
      `${plural(cover.unplaced, 'requirement')} ${cover.unplaced === 1 ? 'is' : 'are'} in no story. ${plural(cover.stories.length, 'story', 'stories')} ${cover.stories.length === 1 ? 'has' : 'have'} no requirement. `,
      h('a', { class: 'gv-coverage-link', id: 'show-coverage', href: hrefTo(ui.page, { ...Object.fromEntries(ui.params), panel: 'coverage', sel: null }) }, 'See the coverage report'),
    );
    summary.dataset.unplaced = String(cover.unplaced);
    summary.dataset.storiesWithout = String(cover.stories.length);

    const loose = kept.filter((dot) => dot.record.story_id === null).sort((a, b) => b.id - a.id); // the newest on top
    const inStory = new Map();
    for (const dot of kept.filter((item) => item.record.story_id !== null).sort((a, b) => a.id - b.id)) {
      if (!inStory.has(dot.record.story_id)) inStory.set(dot.record.story_id, []);
      inStory.get(dot.record.story_id).push(dot);
    }
    const order = (id) => [epicOf(store.nodes.get(id) || { parent_id: null })?.id ?? 0, id];
    const stories = [...inStory.keys()].sort((a, b) => order(a)[0] - order(b)[0] || a - b);

    unplaced.replaceChildren(
      h(
        'section',
        { class: 'gv-group unplaced', dataset: { group: 'unplaced' } },
        h('h3', {}, `Not in any story (${loose.length})`),
        loose.length ? h('ul', { class: 'plain gv-rows' }, loose.map((dot) => row(dot, ctx))) : h('p', { class: 'quiet' }, cover.unplaced ? 'None match the filters.' : 'Every requirement is in a story.'),
      ),
    );
    put(
      groups,
      stories.map((id) => {
        const story = store.nodes.get(id);
        const epic = story ? epicOf(story) : null;
        return h(
          'section',
          { class: 'gv-group', dataset: { group: id } },
          h(
            'h3',
            { class: 'gv-group-head', dataset: { dropStory: id } },
            h('span', { 'aria-hidden': 'true' }, `${TYPES.story.sign} `),
            h('button', { type: 'button', class: 'link', onclick: () => openRecord('node', id) }, story ? `#${id} ${story.title}` : `Story #${id}`),
            epic && epic.id !== id ? h('span', { class: 'quiet' }, ` in epic #${epic.id} ${epic.title}`) : null,
            h('span', { class: 'quiet' }, ` (${plural(inStory.get(id).length, 'requirement')})`),
          ),
          h('ul', { class: 'plain gv-rows' }, inStory.get(id).map((dot) => row(dot, ctx))),
        );
      }),
      !stories.length && !loose.length ? h('p', { class: 'quiet gv-none' }, inScope.length ? 'No requirement matches the filters.' : 'There are no requirements here yet. Write one with New requirement.') : null,
    );

    const open = without.open;
    put(
      without,
      h('summary', {}, `Stories with no requirement (${cover.stories.length})`),
      cover.stories.length
        ? h(
            'ul',
            { class: 'plain' },
            cover.stories.slice(0, 100).map((story) =>
              h(
                'li',
                { class: 'gv-bare-story', dataset: { story: story.id, dropStory: story.id } },
                h('button', { type: 'button', class: 'link', onclick: () => openRecord('node', story.id) }, `#${story.id} ${story.title}`),
                ' ',
                h('button', { type: 'button', class: 'add-requirement', onclick: () => requirementDialog({ story: story.id }) }, 'Add a requirement'),
              ),
            ),
          )
        : h('p', { class: 'quiet' }, 'Every story has a requirement.'),
      cover.stories.length > 100 ? h('p', { class: 'quiet' }, `${cover.stories.length - 100} more: the coverage report lists them all.`) : null,
    );
    without.open = open;
    if (hadFocus) frame.body.querySelector(`[data-key="${hadFocus}"] .gv-open`)?.focus();
  }

  function mark(key) {
    for (const item of frame.body.querySelectorAll('.gv-record.selected')) {
      item.classList.remove('selected');
      item.removeAttribute('aria-current');
    }
    const item = key ? frame.body.querySelector(`.gv-record[data-key="${key}"]`) : null;
    item?.classList.add('selected');
    item?.setAttribute('aria-current', 'true');
    return item;
  }

  // -- dragging a requirement onto a story --------------------------------------------------------

  // Listening on the whole page, because a story to drop on is also in the coverage report, which
  // is in the side panel.
  const dropTarget = (event) => (dragging ? event.target.closest?.('[data-drop-story]') : null);
  const over = (event) => {
    const target = dropTarget(event);
    if (!target) return;
    event.preventDefault();
    target.classList.add('over');
  };
  const leave = (event) => dropTarget(event)?.classList.remove('over');
  const dropped = async (event) => {
    const target = dropTarget(event);
    if (!target) return;
    event.preventDefault();
    target.classList.remove('over');
    const rule = (store.rules || []).find((item) => item.id === dragging);
    dragging = null;
    if (rule) await dropIn(rule, Number(target.dataset.dropStory));
  };
  document.addEventListener('dragover', over);
  document.addEventListener('dragleave', leave);
  document.addEventListener('drop', dropped);

  return {
    element: frame.element,
    draw,
    select: mark,
    busy: () => dragging !== null,
    destroy() {
      document.removeEventListener('dragover', over);
      document.removeEventListener('dragleave', leave);
      document.removeEventListener('drop', dropped);
    },
  };
}

// -- placing by dragging ----------------------------------------------------------------------------

// A requirement in no story is placed; one that is in a story is moved, in one step. The same two
// operations the dialog (placeRequirementDialog) and an agent use.
async function dropIn(rule, story) {
  if (rule.story_id === story) return;
  const placing = rule.story_id === null;
  try {
    await call(placing ? 'place_rule' : 'move_rule', {
      actor: actor(),
      rule_id: rule.id,
      expected_version: rule.version,
      ...(placing ? { story_id: story } : { new_story_id: story }),
    });
    notify(`${placing ? 'Put' : 'Moved'} requirement #${rule.id} ${placing ? 'in' : 'to'} story #${story}.`);
    await sync();
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
  }
}
