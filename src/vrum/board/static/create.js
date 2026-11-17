// Making a new epic, story or task, and the list of parents to choose from. A parent is named by its
// reference (epic:3, story:12) everywhere here: in the options, in `openCreate` and in what is sent.

import { call } from './api.js';
import { actor, notify, ui } from './ctx.js';
import { h } from './dom.js';
import { dialogForm, field, fieldRow, openDialog } from './forms.js';
import { KIND, PRIORITIES, PRIORITY, codeOf, nodeWords } from './labels.js';
import { withReferences } from './reference-picker.js';
import { epics, store, sync } from './store.js';

const byTitle = (a, b) => a.title.localeCompare(b.title) || a.number - b.number;

// The <option>s a node of this kind can be put under: epics for a story, stories (grouped by their
// epic) for a task. Nothing for an epic. The value of an option is the parent's reference and
// `selected` is a reference too.
export function parentChoices(kind, selected = null) {
  if (kind === 'story') {
    return epics()
      .sort(byTitle)
      .map((epic) => h('option', { value: epic.ref, selected: epic.ref === selected }, `${codeOf(epic.ref)} ${epic.title}`));
  }
  if (kind === 'task') {
    return epics()
      .sort(byTitle)
      .map((epic) => {
        const stories = [...store.nodes.values()].filter((node) => node.parent === epic.ref).sort(byTitle);
        if (!stories.length) return null;
        return h(
          'optgroup',
          { label: `${codeOf(epic.ref)} ${epic.title}` },
          stories.map((story) => h('option', { value: story.ref, selected: story.ref === selected }, `${codeOf(story.ref)} ${story.title}`)),
        );
      });
  }
  return [];
}

// Opens the dialog for a new item. `parent` is the reference of the epic or story it starts under.
export function openCreate({ kind = 'epic', parent = null } = {}) {
  const kindField = h(
    'select',
    { id: 'new-kind', name: 'kind', value: kind },
    ['epic', 'story', 'task'].map((name) => h('option', { value: name }, KIND[name].label)),
  );
  const parentField = h('select', { id: 'new-parent', name: 'parent' });
  const parentRow = field('Belongs to', parentField);
  const title = h('input', { id: 'new-title', name: 'title', type: 'text', autocomplete: 'off' });
  const body = withReferences(h('textarea', { id: 'new-body', name: 'body', rows: 4 }));
  const priority = h(
    'select',
    { id: 'new-priority', name: 'priority', value: 'normal' },
    PRIORITIES.map((name) => h('option', { value: name }, PRIORITY[name].label)),
  );
  const start = h('input', { id: 'new-start', name: 'start_on', type: 'date' });
  const target = h('input', { id: 'new-target', name: 'target_on', type: 'date' });

  const fillParents = (selected) => {
    const wanted = kindField.value;
    parentRow.hidden = wanted === 'epic';
    parentRow.firstChild.textContent = wanted === 'task' ? 'In story' : 'In epic';
    parentField.replaceChildren(...parentChoices(wanted, selected).filter(Boolean));
    if (selected !== null) parentField.value = selected;
  };
  kindField.addEventListener('change', () => fillParents(null));
  fillParents(parent);

  const { form } = dialogForm({
    fields: [fieldRow(field('What is it', kindField), parentRow), field('Title', title), field('Details', body), fieldRow(field('Priority', priority), field('Start', start), field('Target', target))],
    submit: 'Create',
    work: async () => {
      const wanted = kindField.value;
      const args = {
        actor: actor(),
        kind: wanted,
        title: title.value,
        body: body.value,
        priority: priority.value,
        start_on: start.value || null,
        target_on: target.value || null,
      };
      if (wanted !== 'epic') args.parent = parentField.value || null;
      const made = await call('create_node', args);
      box.close();
      await sync();
      const similar = (made.similar || []).map((item) => `${codeOf(item.ref)} ${item.title}`);
      notify(
        `Created ${nodeWords(made.ref)}, “${made.title}”.` +
          (similar.length ? ` It looks like: ${similar.join('; ')}.` : ''),
      );
      ui.navigate({ sel: made.ref, panel: null });
    },
  });
  const box = openDialog('New item', [form]);
  title.focus();
}
