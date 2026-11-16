// Making a new epic, story or task, and the list of parents to choose from.

import { call } from './api.js';
import { actor, notify, ui } from './ctx.js';
import { h } from './dom.js';
import { dialogForm, field, fieldRow, openDialog } from './forms.js';
import { KIND, PRIORITIES, PRIORITY } from './labels.js';
import { withReferences } from './reference-picker.js';
import { epics, store, sync } from './store.js';

const byTitle = (a, b) => a.title.localeCompare(b.title) || a.id - b.id;

// The <option>s a node of this kind can be put under: epics for a story, stories (grouped by their
// epic) for a task. Nothing for an epic.
export function parentChoices(kind, selected = null) {
  if (kind === 'story') {
    return epics()
      .sort(byTitle)
      .map((epic) => h('option', { value: epic.id, selected: epic.id === selected }, `#${epic.id} ${epic.title}`));
  }
  if (kind === 'task') {
    return epics()
      .sort(byTitle)
      .map((epic) => {
        const stories = [...store.nodes.values()].filter((node) => node.parent_id === epic.id).sort(byTitle);
        if (!stories.length) return null;
        return h(
          'optgroup',
          { label: `#${epic.id} ${epic.title}` },
          stories.map((story) => h('option', { value: story.id, selected: story.id === selected }, `#${story.id} ${story.title}`)),
        );
      });
  }
  return [];
}

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
    if (selected !== null) parentField.value = String(selected);
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
      if (wanted !== 'epic') args.parent_id = parentField.value ? Number(parentField.value) : null;
      const made = await call('create_node', args);
      box.close();
      await sync();
      const similar = (made.similar || []).map((item) => `#${item.id} ${item.title}`);
      notify(
        `Created ${KIND[made.kind].label.toLowerCase()} #${made.id}, “${made.title}”.` +
          (similar.length ? ` It looks like: ${similar.join('; ')}.` : ''),
      );
      ui.navigate({ sel: `node:${made.id}`, panel: null });
    },
  });
  const box = openDialog('New item', [form]);
  title.focus();
}
