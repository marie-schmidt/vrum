// Choosing the story or task an acceptance criterion goes on: a box to type in and, under it, the
// stories and tasks of the project grouped by their epic, narrowing as words are typed (any word of
// a code or a title). An epic cannot hold one, so an epic is only a heading. It reads the board's copy
// of the work and gives back the reference (story:12 or task:40), or null while nothing is chosen.
// The list is closed when the picker appears (and when the box takes the focus): it opens when the person
// clicks the box, types in it or presses Down, and closes when a row is chosen, on Escape and when the focus leaves.

import { h, put } from './dom.js';
import { icon } from './icons.js';
import { codeOf, kindIcon, refSlug } from './labels.js';
import { epics, store } from './store.js';
import { uid } from './forms.js';

const byNumber = (a, b) => a.number - b.number;

// The epics with their stories, and each story with its tasks, as the store holds them now.
function groups() {
  const nodes = [...store.nodes.values()];
  return epics().map((epic) => ({
    epic,
    stories: nodes
      .filter((node) => node.kind === 'story' && node.parent === epic.ref)
      .sort(byNumber)
      .map((story) => ({ story, tasks: nodes.filter((node) => node.kind === 'task' && node.parent === story.ref).sort(byNumber) })),
  }));
}

const wordsOf = (node) => `${codeOf(node.ref)} ${node.kind} ${node.title}`.toLowerCase();

// `chosen` is the reference to start with. `label` names the field.
export function holderPicker({ chosen = null, label = 'Goes on' } = {}) {
  let picked = chosen;
  let active = null; // the reference lit by the arrow keys
  let open = false; // whether the list is showing
  const listId = uid('holder-list-');
  const input = h('input', {
    id: uid('holder-'),
    type: 'text',
    role: 'combobox',
    autocomplete: 'off',
    'aria-autocomplete': 'list',
    'aria-expanded': 'false',
    'aria-controls': listId,
    placeholder: 'A title word, or a code such as S-12',
  });
  const list = h('div', { id: listId, class: 'holder-list', role: 'listbox', 'aria-label': `${label}: stories and tasks`, hidden: true });
  const chosenLine = h('p', { class: 'holder-chosen', role: 'status' });
  let options = []; // the rows drawn, in order

  const optionRow = (node, { context = false } = {}) => {
    const on = picked === node.ref;
    const row = h(
      'div',
      {
        id: `${listId}-${refSlug(node.ref)}`,
        role: 'option',
        class: `holder-option holder-${node.kind}${context ? ' holder-context' : ''}${active === node.ref ? ' lit' : ''}`,
        'aria-selected': String(on),
        dataset: { ref: node.ref },
        // The mouse never takes the focus from the box, so the typed words stay.
        onmousedown: (event) => event.preventDefault(),
        onclick: () => pick(node.ref),
      },
      kindIcon(node.kind, 14),
      h('span', { class: 'item-code' }, codeOf(node.ref)),
      h('span', { class: 'holder-title' }, node.title),
      on ? icon('check', { size: 16, className: 'holder-check' }) : null,
    );
    options.push({ ref: node.ref, row });
    return row;
  };

  function draw() {
    const words = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const fits = (node) => words.every((word) => wordsOf(node).includes(word));
    options = [];
    const shown = [];
    for (const { epic, stories } of groups()) {
      const rows = [];
      for (const { story, tasks } of stories) {
        const tasksShown = tasks.filter(fits);
        const storyFits = fits(story);
        if (!storyFits && !tasksShown.length) continue;
        rows.push(optionRow(story, { context: !storyFits }), ...tasksShown.map((task) => optionRow(task)));
      }
      if (!rows.length) continue;
      const heading = h('div', { class: 'holder-epic', id: `${listId}-epic-${epic.number}`, 'aria-hidden': 'true' }, kindIcon('epic', 14), h('span', { class: 'item-code' }, codeOf(epic.ref)), h('span', { class: 'holder-title' }, epic.title));
      shown.push(h('div', { role: 'group', class: 'holder-group', 'aria-labelledby': heading.id }, heading, rows));
    }
    put(list, shown.length ? shown : h('p', { class: 'holder-none quiet' }, words.length ? 'No story or task matches.' : 'There is no story or task yet.'));
    if (active && !options.some((one) => one.ref === active)) active = null;
    if (open && active) input.setAttribute('aria-activedescendant', `${listId}-${refSlug(active)}`);
    else input.removeAttribute('aria-activedescendant');
    list.hidden = !open;
    input.setAttribute('aria-expanded', String(open));
    const node = picked ? store.nodes.get(picked) : null;
    put(chosenLine, node ? [icon('check', { size: 14 }), `On ${codeOf(node.ref)} ${node.title}`] : null);
    chosenLine.hidden = !node;
  }

  // Scrolls the list, and nothing else, so that the row is in sight.
  function reveal(ref, where) {
    const row = list.querySelector(`[data-ref="${ref}"]`);
    if (!row) return;
    const top = row.offsetTop;
    const bottom = top + row.offsetHeight;
    if (where === 'middle') list.scrollTop = top - (list.clientHeight - row.offsetHeight) / 2;
    else if (top < list.scrollTop) list.scrollTop = top;
    else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
  }

  function pick(ref) {
    picked = ref;
    active = ref;
    open = false;
    draw();
  }

  // The list opens only on the person's action, with the chosen row (if any) in sight.
  function openList() {
    if (open) return;
    open = true;
    draw();
    if (picked) reveal(picked, 'middle');
  }

  function closeList() {
    if (!open) return;
    open = false;
    draw();
  }

  function move(step) {
    if (!options.length) return;
    const at = options.findIndex((one) => one.ref === active);
    active = options[(at + step + options.length) % options.length].ref;
    draw();
    reveal(active, 'nearest');
  }

  input.addEventListener('click', openList);
  input.addEventListener('input', () => {
    active = null;
    open = true;
    draw();
  });
  input.addEventListener('blur', closeList);
  // The mouse never takes the focus from the box (not even on the list's scroll bar), so the typed words stay.
  list.addEventListener('mousedown', (event) => event.preventDefault());
  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      openList();
      move(event.key === 'ArrowDown' ? 1 : -1);
    } else if (event.key === 'Escape' && open) {
      // Escape closes the list first, and only then the dialog.
      event.preventDefault();
      event.stopPropagation();
      closeList();
    } else if (event.key === 'Enter') {
      // Enter chooses the lit row, and never sends the form.
      event.preventDefault();
      if (!open) openList();
      else if (active) pick(active);
      else if (options.length === 1) pick(options[0].ref);
    }
  });
  draw();

  const element = h('div', { class: 'field holder-picker' }, h('label', { for: input.id }, label), input, chosenLine, list);
  return {
    element,
    input,
    value: () => picked,
    // Once the dialog is on the page: the box takes the focus, and nothing opens.
    focus() {
      input.focus();
    },
  };
}
