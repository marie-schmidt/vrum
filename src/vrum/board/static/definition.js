// The definition of done (#/definition): one text for the whole project that says what every story or task
// has to satisfy, on top of its own acceptance criteria. It is shown on the page of every story and task and
// told to an agent before it finishes work. It is read as Markdown, with an Edit button for a person who can
// change the project; with nothing written it is an empty field with the way to fill it. Saving is the
// operation an agent cannot do (only a person changes it), at the version the person saw, so a change made
// meanwhile is refused in the server's words and what was typed stays.

import { Refusal, call } from './api.js';
import { checkItems, contextOf } from './checks.js';
import { can, notify } from './ctx.js';
import { h, moment, plural, put } from './dom.js';
import { refuse } from './forms.js';
import { icon } from './icons.js';
import { keyButton } from './key-button.js';
import { actorLabel, personOnlyChip } from './labels.js';
import { renderMarkdown } from './markdown.js';
import { pageHeader } from './page.js';
import { withReferences } from './reference-picker.js';
import { emptyField, explainStale, hasDetails, loadingView } from './records.js';
import { loadTargets } from './references.js';
import { sync } from './store.js';

let root = null;
let generation = 0;
let places = null; // the parts of the page on show
let state = null; // { definition, editing, version, dirty, reload }

const read = () => call('get_definition_of_done', {});

// Who changed it last, and when: nothing while it has never been written.
const lastChanged = (definition) =>
  definition.updated_at ? h('p', { class: 'quiet definition-meta' }, 'Last changed by ', actorLabel(definition.updated_by, definition.updated_by_kind), `, ${moment(definition.updated_at)}`) : null;

// "6 checks", "1 check".
const checksWords = (count) => plural(count, 'check');

// On how many stories and tasks a check is ticked.
const ticksWords = (count) => (count ? `ticked on ${plural(count, 'story or task', 'stories and tasks')}` : 'not ticked yet');

// What the text being typed holds: how many checks, and what changing it would cost. A line that is reworded or
// taken out is a check that no longer exists, and the ticks it had stop counting.
function preview(text) {
  const items = checkItems(text);
  const now = new Set(items);
  const lost = (state.definition.items || []).filter((one) => !now.has(one.text) && one.ticks > 0);
  const ticks = lost.reduce((sum, one) => sum + one.ticks, 0);
  return [
    text.trim() ? h('p', { class: 'quiet definition-count', id: 'definition-count' }, items.length ? checksWords(items.length) : 'No checks yet. Start a line with a dash to make one.') : null,
    ticks ? h('p', { class: 'notice definition-lost', id: 'definition-lost', role: 'status' }, icon('alert', { size: 16 }), h('span', {}, `${plural(lost.length, 'line')} you changed or removed ${lost.length === 1 ? 'has' : 'have'} ${plural(ticks, 'tick')}. Those ticks stop counting.`)) : null,
  ];
}

function form() {
  const body = withReferences(h('textarea', { id: 'definition-body', rows: 12, value: state.definition.body, placeholder: 'One line per check, starting with a dash:\n- Tests pass\n- Tried on a phone' }));
  const problem = h('div', { class: 'problem', role: 'alert' });
  const counted = h('div', { class: 'definition-preview', 'aria-live': 'polite' }, preview(body.value));
  const stop = () => {
    state.dirty = false;
    state.editing = false;
    draw();
    document.getElementById('edit-definition')?.focus();
  };
  body.addEventListener('input', () => {
    state.dirty = true;
    put(counted, preview(body.value));
  });
  return h(
    'form',
    {
      class: 'edit definition-form',
      onsubmit: async (event) => {
        event.preventDefault();
        problem.replaceChildren();
        try {
          const saved = await call('set_definition_of_done', { body: body.value, expected_version: state.version });
          notify('Saved the definition of done.');
          state.definition = saved;
          state.version = saved.version;
          state.dirty = false;
          state.editing = false;
          await sync();
          draw();
          document.getElementById('edit-definition')?.focus();
        } catch (error) {
          if (!(error instanceof Refusal)) throw error;
          await explainStale(error, problem, state, async () => (await read()).version);
        }
      },
    },
    h('div', { class: 'field' }, h('label', { for: 'definition-body', class: 'sr-only' }, 'Definition of done'), body),
    counted,
    problem,
    h('div', { class: 'form-actions' }, h('button', { type: 'button', class: 'cancel-edit', onclick: stop }, 'Cancel'), h('button', { type: 'submit', class: 'primary' }, 'Save changes')),
  );
}

function open() {
  state.editing = true;
  draw();
  document.getElementById('definition-body')?.focus();
}

// The text as it stands: what is not a check, as written, and the checks with the number of stories and tasks each
// is ticked on. A text with no list lines has no checks, and says how to make one.
function reading(definition) {
  const items = definition.items || [];
  const context = contextOf(definition.body);
  return [
    context ? h('div', { class: 'body-text definition-text' }, renderMarkdown(context)) : null,
    items.length
      ? h(
          'section',
          { class: 'definition-checks', 'aria-labelledby': 'definition-checks-title' },
          h('h2', { id: 'definition-checks-title' }, checksWords(items.length)),
          h(
            'ul',
            { class: 'plain check-ticks' },
            items.map((one) =>
              h(
                'li',
                { dataset: { item: one.item, ticks: String(one.ticks), personOnly: String(Boolean(one.person_only)) } },
                h('span', { class: 'check-ticks-main' }, h('span', { class: 'check-ticks-text' }, one.text), one.person_only ? personOnlyChip() : null),
                h('span', { class: 'quiet check-ticks-count' }, ticksWords(one.ticks)),
              ),
            ),
          ),
        )
      : h('p', { class: 'quiet definition-count', id: 'definition-count' }, 'No checks yet. Start a line with a dash to make one.'),
  ];
}

// What the Key button opens: how a line is written, and what the one that only a person can judge looks like.
function keyContent() {
  const row = (example, ...words) => h('li', {}, h('code', { class: 'dk-example' }, example), h('span', { class: 'dk-words' }, ...words));
  return h(
    'div',
    { class: 'dk-card' },
    h('p', { class: 'hovercard-title' }, 'Checks'),
    h('ul', { class: 'plain dk-list' }, row('- Tests pass', 'A check on every story and task'), row('- [person] Looks right on a phone', personOnlyChip(), 'Only a person can tick it')),
  );
}

function draw() {
  const { definition } = state;
  const writes = can('write');
  const written = hasDetails(definition.body);
  const edit = writes && written && !state.editing ? h('button', { type: 'button', id: 'edit-definition', class: 'edit-details', 'aria-label': 'Edit the definition of done', onclick: open }, 'Edit') : null;
  put(places.edit, edit);
  put(
    places.body,
    state.editing
      ? form()
      : written
        ? reading(definition)
        : emptyField(writes ? h('button', { type: 'button', id: 'add-definition', class: 'small add-details', onclick: open }, icon('plus', { size: 14 }), 'Add a definition of done') : null),
    state.editing ? null : lastChanged(definition),
  );
}

async function load(mine, { keepEditing = false } = {}) {
  try {
    const [definition] = await Promise.all([read(), loadTargets().catch(() => {})]);
    if (mine !== generation) return;
    // What is being typed is never drawn over: the save is refused if the text changed meanwhile.
    if (keepEditing && state.editing) return;
    state.definition = definition;
    state.version = definition.version;
    draw();
  } catch (error) {
    if (mine !== generation) return;
    if (!(error instanceof Refusal)) throw error;
    put(places.body, h('p', { class: 'notice refusal', role: 'alert' }, icon('alert', { size: 16 }), h('span', {}, error.message)));
  }
}

export function mount(element) {
  root = element;
  const edit = h('span', { class: 'definition-edit' });
  const actions = h('div', { class: 'definition-actions' }, keyButton(keyContent(), { id: 'definition-key-button' }), edit);
  const body = h('div', { class: 'definition-body', id: 'definition-field' }, loadingView());
  places = { edit, body };
  state = { definition: { body: '', version: 1, updated_at: null, updated_by: null, updated_by_kind: null }, editing: false, version: 1, dirty: false };
  state.reload = () => load(++generation);
  put(root, h('div', { class: 'settings-page definition-page', id: 'definition-page' }, pageHeader('Definition of done', actions), h('section', { class: 'settings-section' }, body)));
  state.reload();
}

export function unmount() {
  generation += 1;
  root = null;
  places = null;
  state = null;
}

export function update() {}

export function refresh() {
  if (state) load(++generation, { keepEditing: true });
}
