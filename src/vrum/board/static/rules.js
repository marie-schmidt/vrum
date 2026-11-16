// The Rules page: every rule, grouped by the story that owns it, with the rules that are in no
// story first and the coverage report below. A person can write a rule, change it, and put it in a
// story or move it, by dragging it onto a story or from a dialog. Each of those is the same
// operation an agent calls, at the version the person saw, so the server decides and a refusal
// reads as it does for an agent. The page keeps no data of its own.

import { Refusal, call } from './api.js';
import { actor, notify, ui } from './ctx.js';
import { clear, h, plural } from './dom.js';
import { itemLink } from './card.js';
import { coverageWords, drawCoverage } from './coverage.js';
import { parentChoices } from './create.js';
import { dialogForm, field, openDialog } from './forms.js';
import { KIND } from './labels.js';
import { ensure, epicOf, store, sync, titleOf } from './store.js';

const RULE_STATUSES = [
  ['held', 'Held', 'A requirement that stands'],
  ['settled', 'Settled', 'Agreed and done with'],
  ['dropped', 'Dropped', 'No longer a requirement'],
];
const WORDS = Object.fromEntries(RULE_STATUSES.map(([value, label, hint]) => [value, { label, hint }]));

let root = null;
let params = new URLSearchParams();
let parts = {};
let dragging = null; // the rule being dragged
let generation = 0;
let shownRule = null; // the rule the address pointed at, once scrolled to

const byId = (a, b) => a.id - b.id;

// -- the page -------------------------------------------------------------------------------------

export async function mount(element, initial) {
  root = element;
  params = initial;
  shownRule = null;
  parts = {
    summary: h('p', { class: 'count', id: 'rules-summary', role: 'status' }),
    unplaced: h('div', { id: 'unplaced-group' }),
    groups: h('div', { id: 'rule-groups' }),
    coverage: h('section', { id: 'coverage-report', class: 'part' }),
    dropped: h('input', { type: 'checkbox', id: 'show-dropped', checked: params.get('dropped') === '1', onchange: () => ui.navigate({ dropped: parts.dropped.checked ? '1' : null }) }),
  };
  parts.coverageBody = h('div', { class: 'coverage-lists' });
  parts.coverage.append(
    h('h2', { id: 'coverage-heading' }, 'Coverage report'),
    h('p', { class: 'quiet' }, 'What is not matched up yet: rules that are in no story and stories that have no rule. Dropped rules and dropped stories are left out. A story with no rule can be dropped on, too.'),
    parts.coverageBody,
  );
  clear(root).append(
    h(
      'div',
      { class: 'toolbar' },
      h('h1', {}, 'Rules'),
      h('button', { type: 'button', id: 'new-rule', class: 'primary', onclick: () => ruleDialog(null) }, 'New rule'),
      h('label', { class: 'check', for: 'show-dropped' }, parts.dropped, ' Show dropped rules'),
      h('p', { class: 'help' }, 'Each requirement is in exactly one story. Drag a rule onto a story to put it there, or choose Put in a story (a rule in no story is placed, one that is in a story is moved). Open a rule’s recall to see what an agent would be told about it.'),
    ),
    parts.summary,
    parts.unplaced,
    parts.groups,
    parts.coverage,
  );
  root.addEventListener('dragover', dragOver);
  root.addEventListener('dragleave', dragLeave);
  root.addEventListener('drop', dropped);
  await ensure('rules');
  await load();
}

export function unmount() {
  generation += 1;
  root.removeEventListener('dragover', dragOver);
  root.removeEventListener('dragleave', dragLeave);
  root.removeEventListener('drop', dropped);
  root = null;
  dragging = null;
}

export function update(next) {
  const before = params;
  params = next;
  if (!root) return;
  parts.dropped.checked = params.get('dropped') === '1';
  if (params.get('rule') !== before.get('rule')) shownRule = null;
  draw();
}

export function refresh() {
  if (!root || dragging) return;
  load();
}

async function load() {
  const mine = ++generation;
  await ensure('rules');
  const counts = await drawCoverage(parts.coverageBody);
  if (mine !== generation || !root) return;
  parts.summary.replaceChildren(coverageWords(counts), ' ', h('a', { href: '#coverage-heading' }, 'See the coverage report'));
  draw();
}

// -- drawing --------------------------------------------------------------------------------------

function draw() {
  if (!root) return;
  const showDropped = params.get('dropped') === '1';
  const rules = (store.rules || []).filter((rule) => showDropped || rule.status !== 'dropped').sort(byId);
  const unplaced = rules.filter((rule) => rule.story_id === null).reverse(); // the newest on top
  const inStory = new Map();
  for (const rule of rules.filter((item) => item.story_id !== null)) {
    if (!inStory.has(rule.story_id)) inStory.set(rule.story_id, []);
    inStory.get(rule.story_id).push(rule);
  }
  const order = (id) => {
    const story = store.nodes.get(id);
    return [epicOf(story || { parent_id: null })?.id ?? 0, id];
  };
  const stories = [...inStory.keys()].sort((a, b) => order(a)[0] - order(b)[0] || a - b);

  clear(parts.unplaced).append(
    h(
      'section',
      { class: 'rule-group unplaced', dataset: { group: 'unplaced' } },
      h('h2', {}, `Not in any story (${unplaced.length})`),
      unplaced.length
        ? h('ul', { class: 'plain rules' }, unplaced.map(ruleRow))
        : h('p', { class: 'quiet' }, 'Every rule is in a story.'),
    ),
  );
  clear(parts.groups).append(
    ...(stories.length
      ? stories.map((id) => {
          const story = store.nodes.get(id);
          const epic = story ? epicOf(story) : null;
          return h(
            'section',
            { class: 'rule-group', dataset: { group: id } },
            h(
              'h2',
              { class: 'group-head', dataset: { dropStory: id } },
              h('span', { 'aria-hidden': 'true' }, `${KIND.story.sign} `),
              itemLink(id),
              epic && epic.id !== id ? h('span', { class: 'quiet' }, ` in epic #${epic.id} ${epic.title}`) : null,
              h('span', { class: 'quiet' }, ` (${plural(inStory.get(id).length, 'rule')})`),
            ),
            h('ul', { class: 'plain rules' }, inStory.get(id).map(ruleRow)),
          );
        })
      : [h('p', { class: 'quiet' }, 'No story has a rule yet.')]),
  );
  const wanted = params.get('rule');
  if (wanted && shownRule !== wanted) {
    const target = root.querySelector(`li.rule[data-rule="${CSS.escape(wanted)}"]`);
    if (target) {
      shownRule = wanted;
      target.scrollIntoView({ block: 'center' });
    }
  }
}

function ruleRow(rule) {
  const focus = params.get('rule') === String(rule.id);
  const row = h(
    'li',
    {
      class: `rule status-${rule.status}${focus ? ' highlight' : ''}`,
      draggable: 'true',
      dataset: { rule: rule.id, status: rule.status, story: rule.story_id ?? '' },
      ondragstart: (event) => {
        dragging = rule.id;
        event.dataTransfer.setData('application/x-vrum-rule', String(rule.id));
        event.dataTransfer.setData('text/plain', `rule ${rule.id}`);
        event.dataTransfer.effectAllowed = 'move';
      },
      ondragend: () => {
        dragging = null;
        root?.querySelectorAll('.over').forEach((el) => el.classList.remove('over'));
      },
    },
    h(
      'div',
      { class: 'entry-head' },
      h('span', { class: 'chip kind kind-rule' }, h('span', { 'aria-hidden': 'true' }, KIND.rule.sign), ` Rule #${rule.id}`),
      h('span', { class: `chip rule-status status-${rule.status}`, title: WORDS[rule.status].hint }, WORDS[rule.status].label),
      h('strong', { class: 'rule-title' }, rule.title),
    ),
    rule.body ? h('p', { class: 'entry-text' }, rule.body) : null,
    h(
      'div',
      { class: 'buttons' },
      h('button', { type: 'button', class: 'edit-rule', onclick: () => ruleDialog(rule) }, 'Edit'),
      h('button', { type: 'button', class: 'put-rule', onclick: () => storyDialog(rule) }, 'Put in a story…'),
      h('a', { class: 'recall-link', href: `#/memory?recall=rule:${rule.id}` }, 'What an agent is told (recall)'),
    ),
  );
  return row;
}

// -- dragging a rule onto a story ------------------------------------------------------------------

function target(event) {
  return dragging ? event.target.closest?.('[data-drop-story]') : null;
}

function dragOver(event) {
  const over = target(event);
  if (!over) return;
  event.preventDefault();
  over.classList.add('over');
}

function dragLeave(event) {
  target(event)?.classList.remove('over');
}

async function dropped(event) {
  const over = target(event);
  if (!over) return;
  event.preventDefault();
  over.classList.remove('over');
  const rule = (store.rules || []).find((item) => item.id === dragging);
  const story = Number(over.dataset.dropStory);
  dragging = null;
  if (!rule || rule.story_id === story) return;
  // A rule in no story is placed; one that is in a story is moved, in one step.
  const placing = rule.story_id === null;
  try {
    await call(placing ? 'place_rule' : 'move_rule', {
      actor: actor(),
      rule_id: rule.id,
      expected_version: rule.version,
      ...(placing ? { story_id: story } : { new_story_id: story }),
    });
    notify(`${placing ? 'Put' : 'Moved'} rule #${rule.id} ${placing ? 'in' : 'to'} story #${story}.`);
  } catch (error) {
    notify(error.message, { refusal: true });
  }
  await sync();
  load();
}

// -- writing and changing a rule -------------------------------------------------------------------

function statusSelect(value) {
  return h('select', { value }, RULE_STATUSES.map(([name, label]) => h('option', { value: name }, label)));
}

function ruleDialog(rule) {
  const title = h('input', { type: 'text', value: rule?.title || '', autocomplete: 'off', id: 'rule-title' });
  const body = h('textarea', { rows: 6, value: rule?.body || '', id: 'rule-body' });
  const status = statusSelect(rule?.status || 'held');
  status.id = 'rule-status';
  const story = h('select', { id: 'rule-story' }, h('option', { value: '' }, 'Not in a story yet (place it later)'), parentChoices('task'));
  const version = rule?.version;
  const { form } = dialogForm({
    fields: [
      field('The rule, stated briefly', title),
      field('Details, or where it came from', body),
      h('div', { class: 'row' }, field('Status', status), rule ? null : field('Story', story)),
    ],
    submit: rule ? 'Save changes' : 'Create rule',
    work: async () => {
      if (!rule) {
        const made = await call('create_rule', {
          actor: actor(),
          title: title.value,
          body: body.value,
          status: status.value,
          story_id: story.value ? Number(story.value) : null,
        });
        box.close();
        notify(`Created rule #${made.id}, “${made.title}”.`);
        await sync();
        ui.navigate({ rule: made.id });
        return;
      }
      const args = { actor: actor(), rule_id: rule.id, expected_version: version };
      if (title.value !== rule.title) args.title = title.value;
      if (body.value !== (rule.body || '')) args.body = body.value;
      if (status.value !== rule.status) args.status = status.value;
      if (Object.keys(args).length === 3) {
        throw new Refusal('Nothing has been changed yet, so there is nothing to save.');
      }
      await call('update_rule', args);
      box.close();
      notify(`Saved rule #${rule.id}.`);
      await sync();
    },
  });
  const box = openDialog(rule ? `Edit rule #${rule.id}` : 'New rule', [form]);
  title.focus();
}

function storyDialog(rule) {
  const story = h('select', { id: 'rule-target' }, parentChoices('task'));
  const send = (tool, field_) => async () => {
    if (!story.value) throw new Refusal('Choose the story first.');
    await call(tool, { actor: actor(), rule_id: rule.id, expected_version: rule.version, [field_]: Number(story.value) });
    box.close();
    notify(`${tool === 'place_rule' ? 'Put' : 'Moved'} rule #${rule.id} ${tool === 'place_rule' ? 'in' : 'to'} story #${story.value}.`);
    await sync();
  };
  const here = rule.story_id === null ? 'It is in no story yet.' : `It is in story #${rule.story_id} ${titleOf(rule.story_id)}.`;
  const { form } = dialogForm({
    fields: [
      h('p', {}, h('strong', {}, `Rule #${rule.id}: ${rule.title}`)),
      h('p', { class: 'quiet' }, `${here} A rule is in at most one story. Place puts a rule that is in no story into one; Move takes a rule from its story to another in one step.`),
      field('Story', story),
    ],
    submit: 'Place in story',
    work: send('place_rule', 'story_id'),
    also: [{ label: 'Move to story', work: send('move_rule', 'new_story_id') }],
  });
  const box = openDialog(`Put rule #${rule.id} in a story`, [form]);
  story.focus();
}
