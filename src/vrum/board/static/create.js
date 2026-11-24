// Making a new epic, story or task, and the list of parents to choose from. A parent is named by its
// reference (epic:3, story:12) in the value of an option, in `openCreate` and in what is sent, and by its
// code and title where a person reads it.

import { Refusal, call } from './api.js';
import { notify, openItem } from './ctx.js';
import { dateField } from './datepicker.js';
import { h } from './dom.js';
import { dialogForm, field, fieldRow, openDialog, sizeSelect } from './forms.js';
import { KIND, PRIORITIES, PRIORITY, codeOf } from './labels.js';
import { withReferences } from './reference-picker.js';
import { epics, store, sync } from './store.js';

const byTitle = (a, b) => a.title.localeCompare(b.title) || a.number - b.number;

// The <option>s a node of this kind can be put under: epics for a story, stories (grouped by their
// epic) for a task. Nothing for an epic. The value of an option is the parent's reference and
// `selected` is a reference too. A choice is not made for the person: the first option is a quiet
// "Choose an epic" / "Choose a story" with the value "", which is what the select shows until a parent is
// given (`selected`) or picked.
export function parentChoices(kind, selected = null) {
  const choose = (words) => h('option', { value: '', selected: selected === null }, words);
  if (kind === 'story') {
    return [
      choose('Choose an epic'),
      ...epics()
        .sort(byTitle)
        .map((epic) => h('option', { value: epic.ref, selected: epic.ref === selected }, `${codeOf(epic.ref)} ${epic.title}`)),
    ];
  }
  if (kind === 'task') {
    return [
      choose('Choose a story'),
      ...epics()
        .sort(byTitle)
        .map((epic) => {
          const stories = [...store.nodes.values()].filter((node) => node.parent === epic.ref).sort(byTitle);
          if (!stories.length) return null;
          return h(
            'optgroup',
            { label: `${codeOf(epic.ref)} ${epic.title}` },
            stories.map((story) => h('option', { value: story.ref, selected: story.ref === selected }, `${codeOf(story.ref)} ${story.title}`)),
          );
        }),
    ];
  }
  return [];
}

// Opens the dialog for a new item. `kind` is the kind the button that opened it makes (a story under an epic's lane,
// a task under a story); the general "New item" button gives none, and the kind then starts as "Choose what to
// create". `parent` is the reference of the epic or story it starts under. A choice not given starts empty, and
// creating without it says what to choose.
export function openCreate({ kind = null, parent = null } = {}) {
  const kindField = h(
    'select',
    { id: 'new-kind', name: 'kind' },
    h('option', { value: '' }, 'Choose what to create'),
    ['epic', 'story', 'task'].map((name) => h('option', { value: name }, KIND[name].label)),
  );
  kindField.value = kind || '';
  const parentField = h('select', { id: 'new-parent', name: 'parent' });
  const parentRow = field('Belongs to', parentField);
  const title = h('input', { id: 'new-title', name: 'title', type: 'text', autocomplete: 'off' });
  const body = withReferences(h('textarea', { id: 'new-body', name: 'body', rows: 4 }));
  const priority = h(
    'select',
    { id: 'new-priority', name: 'priority', value: 'normal' },
    PRIORITIES.map((name) => h('option', { value: name }, PRIORITY[name].label)),
  );
  const start = h('input', { id: 'new-start', name: 'start_on' });
  const target = h('input', { id: 'new-target', name: 'target_on' });
  const size = sizeSelect({ id: 'new-size' });
  const sizeRow = field('Size', size);

  const fillParents = (selected) => {
    const wanted = kindField.value;
    const needsParent = wanted === 'story' || wanted === 'task';
    parentRow.hidden = !needsParent;
    sizeRow.hidden = !needsParent;
    parentRow.firstChild.textContent = wanted === 'task' ? 'In story' : 'In epic';
    parentField.replaceChildren(...parentChoices(wanted, selected).filter(Boolean));
    parentField.value = selected || '';
  };
  kindField.addEventListener('change', () => fillParents(null));
  fillParents(parent);

  const { form, problem } = dialogForm({
    fields: [fieldRow(field('Kind', kindField), parentRow), field('Title', title), field('Details', body), fieldRow(field('Priority', priority), sizeRow), fieldRow(field('Start', dateField(start, 'Start')), field('Target', dateField(target, 'Target')))],
    submit: 'Create',
    work: async () => {
      const wanted = kindField.value;
      if (!wanted) throw new Refusal('Choose what to create: an epic, a story or a task.');
      if (wanted !== 'epic' && !parentField.value) throw new Refusal(wanted === 'task' ? 'Choose the story this task is in.' : 'Choose the epic this story is in.');
      const args = {
        kind: wanted,
        title: title.value,
        body: body.value,
        priority: priority.value,
        start_on: start.value || null,
        target_on: target.value || null,
      };
      if (wanted !== 'epic') args.parent = parentField.value;
      if (wanted !== 'epic' && size.value) args.size = size.value;
      const made = await call('create_node', args);
      box.close();
      await sync();
      const similar = (made.similar || []).map((item) => `${codeOf(item.ref)} ${item.title}`);
      notify(
        `Created ${KIND[wanted].label.toLowerCase()} “${made.title}”.` +
          (similar.length ? ` It looks like: ${similar.join('; ')}.` : ''),
      );
      openItem(made.ref);
    },
  });
  // A refusal for a missing choice goes away once the choice is made.
  for (const choice of [kindField, parentField]) choice.addEventListener('change', () => problem.replaceChildren());
  const box = openDialog('New item', [form]);
  title.focus();
}
