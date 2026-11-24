// Plan templates (#/templates): a template is a plan with holes, saved as data. It says which variables it takes (a line of text,
// one of a list of choices, or an epic or story that exists) and lists the epics, stories and tasks of the plan with {{name}}
// wherever a value goes; making a plan from it gives exactly the plan that writing it out by hand would, checked in the same way.
// This page lists the project's templates (read with `list_plan_templates`, the same answer an agent gets), and a person who can
// write makes a plan from one (a field for each variable, with its default filled in), reads one whole, saves a new one or a new
// version of one, and deletes one. A person who can only look sees the list and can read a template whole.
//
// The page never decides what a template may hold: it sends the name and the text as they were typed (it only reads that the text is
// JSON), and the server's refusal, which lists every problem, is shown in the dialog in its own words. A plan made as a draft waits for
// a person's approval on the Needs a person page, where the page says where to go.

import { Refusal, call } from './api.js';
import { can, itemLink, notify, viewLink } from './ctx.js';
import { h, moment, put } from './dom.js';
import { chooseOption, dialogForm, field, openDialog } from './forms.js';
import { icon } from './icons.js';
import { actorLabel, byRef, codeOf, countsWords, kindIcon, planCode } from './labels.js';
import { pageHeader } from './page.js';
import { loadingView } from './records.js';
import { store, sync } from './store.js';

let places = null; // the parts of the page on show
let generation = 0;
let state = null; // { templates }

const read = (name = null) => call('list_plan_templates', name ? { name } : {});

// A variable that has to be given: required, and no default to fall back on.
const needed = (variable) => variable.required && (variable.default === null || variable.default === undefined);

// How a variable is described in a chip's tooltip.
function variableWords(name, variable) {
  const what = variable.type === 'ref' ? `an ${variable.kind || 'epic or story'} that exists` : variable.choices?.length ? `one of ${variable.choices.join(', ')}` : 'a line of text';
  return [variable.description, `${name}: ${what}${needed(variable) ? ', required' : ''}`].filter(Boolean).join('. ');
}

// -- making a plan ---------------------------------------------------------------------------------------

// The epics and stories a variable of type ref can name, by their codes.
const refChoices = (variable) =>
  [...store.nodes.values()]
    .filter((node) => (variable.kind ? node.kind === variable.kind : node.kind === 'epic' || node.kind === 'story'))
    .sort((a, b) => byRef(a.ref, b.ref));

// The control for one variable: a select of the project's epics or stories for a reference, a select for a list of choices, and a text
// field otherwise. Its default is filled in; nothing else is chosen for the person.
function control(name, variable) {
  const id = `var-${name}`;
  if (variable.type === 'ref') {
    const word = variable.kind === 'story' ? 'a story' : variable.kind === 'epic' ? 'an epic' : 'an epic or story';
    const select = h('select', { id }, chooseOption(`Choose ${word}`), refChoices(variable).map((node) => h('option', { value: node.ref }, `${codeOf(node.ref)} ${node.title}`)));
    if (variable.default) select.value = variable.default;
    return select;
  }
  if (variable.choices?.length) {
    const select = h('select', { id }, variable.default ? null : chooseOption('Choose one'), variable.choices.map((choice) => h('option', { value: choice }, choice)));
    if (variable.default) select.value = variable.default;
    return select;
  }
  return h('input', { id, type: 'text', autocomplete: 'off', value: variable.default ?? '' });
}

// What was made, said briefly: how much, the epics and stories as links (a plan can be long, so tasks are only counted), and, for a
// draft, that it waits for a person to approve it, with the way to the page where they do.
function made(result, close) {
  const count = (kind) => result.created.filter((one) => one.kind === kind).length;
  const links = result.created.filter((one) => one.kind !== 'task');
  const follow = (link) => {
    link.addEventListener('click', close);
    return link;
  };
  return h(
    'div',
    { class: 'template-made', id: 'template-made', role: 'status' },
    h('p', { class: 'template-made-words' }, `Made ${countsWords(count('epic'), count('story'), count('task'))}.`),
    links.length
      ? h('ul', { class: 'plain template-made-items' }, links.map((one) => h('li', {}, follow(itemLink(one.ref, { class: 'item-link', dataset: { ref: one.ref } }, kindIcon(one.kind, 14), h('span', { class: 'item-code' }, codeOf(one.ref)), ' ', one.title)))))
      : null,
    result.draft
      ? h('p', { class: 'template-made-draft' }, `${planCode(result.plan)} waits for a person to approve it. `, follow(viewLink('needs', {}, { class: 'action-link', id: 'template-made-needs' }, 'Open Needs a person')), '.')
      : null,
  );
}

function makeDialog(info) {
  const names = Object.keys(info.variables);
  const controls = new Map(names.map((name) => [name, control(name, info.variables[name])]));
  const title = h('input', { id: 'plan-title', type: 'text', autocomplete: 'off' });
  const draft = h('input', { type: 'checkbox', id: 'plan-draft' });
  const { form } = dialogForm({
    fields: [
      info.description ? h('p', { class: 'template-about' }, info.description) : null,
      ...names.map((name) => {
        const variable = info.variables[name];
        return field(`${name} (${needed(variable) ? 'required' : 'optional'})`, controls.get(name), variable.description || null);
      }),
      field('Plan title (optional)', title, 'What a person sees when the plan waits for approval.'),
      h('div', { class: 'field' }, h('label', { class: 'check', for: 'plan-draft' }, draft, ' Wait for a person to approve the plan'), h('p', { class: 'hint' }, 'Nothing in it can be taken until a person approves it on the Needs a person page.')),
    ],
    submit: 'Make the plan',
    work: async () => {
      const values = {};
      for (const [name, one] of controls) if (one.value.trim()) values[name] = one.value;
      const result = await call('create_plan', { template: info.name, values, ...(draft.checked ? { draft: true } : {}), ...(title.value.trim() ? { title: title.value } : {}) });
      notify(`Made a plan from ${info.name}.`);
      await sync().catch(() => {});
      form.hidden = true;
      put(report, made(result, () => box.close()), h('div', { class: 'actions' }, h('button', { type: 'button', class: 'primary', id: 'template-made-close', onclick: () => box.close() }, 'Close')));
      document.getElementById('template-made-close')?.focus();
    },
  });
  const report = h('div', { class: 'template-report' });
  const box = openDialog(`Make a plan from ${info.name}`, [form, report]);
  box.id = 'make-plan';
  box.classList.add('template-dialog');
  return box;
}

// -- reading one -----------------------------------------------------------------------------------------

// A template whole, as the server keeps it, written out as text.
async function viewDialog(name) {
  const body = h('div', { class: 'template-view-body' }, h('p', { class: 'loading', role: 'status' }, 'Reading…'));
  const box = openDialog(`The plan template ${name}`, [body, h('div', { class: 'actions' }, h('button', { type: 'button', id: 'template-view-close', onclick: () => box.close() }, 'Close'))], { wide: true });
  box.id = 'view-template';
  try {
    const page = await read(name);
    const found = page.items[0];
    put(body, h('pre', { class: 'template-json', id: 'template-json', tabindex: 0, 'aria-label': `The text of ${name}` }, JSON.stringify(found.template, null, 2)));
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    put(body, h('p', { class: 'problem', role: 'alert' }, icon('alert', { size: 16 }), h('span', { class: 'problem-text' }, error.message)));
  }
}

// -- saving one --------------------------------------------------------------------------------------------

// The dialog that saves a template: its name, and its text as JSON. A new one opens empty; to change one, its whole text is read
// first. Saving a name again replaces it (every version stays in the history). The server checks everything and lists every problem.
function saveDialog({ name = '', text = '' } = {}, existing = false) {
  const nameInput = h('input', { id: 'template-name', type: 'text', autocomplete: 'off', spellcheck: false, value: name });
  const area = h('textarea', { id: 'template-text', rows: 16, autocomplete: 'off', spellcheck: false, value: text });
  const { form } = dialogForm({
    fields: [
      h('p', { class: 'template-about' }, existing ? 'Saving under the same name replaces the template. Every version stays in the history.' : 'A template is written as JSON: its description, its variables, and the items, links and acceptance criteria of the plan, with {{name}} wherever a variable goes.'),
      field('Name', nameInput, 'Lower case letters, digits, - and _, such as release-checklist.'),
      field('Template', area),
    ],
    submit: 'Save the template',
    work: async () => {
      let template;
      try {
        template = JSON.parse(area.value);
      } catch (error) {
        throw new Refusal(`That is not JSON: ${error.message} Nothing was saved.`);
      }
      const saved = await call('save_plan_template', { name: nameInput.value, template });
      box.close();
      notify(`Saved the plan template ${saved.name}.`);
      await sync().catch(() => {});
      await load(++generation);
    },
  });
  const box = openDialog(existing ? `Change the plan template ${name}` : 'New plan template', [form], { wide: true });
  box.id = 'save-template';
  box.classList.add('template-dialog');
  return box;
}

async function editTemplate(name) {
  try {
    const page = await read(name);
    saveDialog({ name, text: JSON.stringify(page.items[0].template, null, 2) }, true);
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
  }
}

// -- deleting one ------------------------------------------------------------------------------------------

function deleteDialog(name) {
  const { form } = dialogForm({
    fields: [h('p', {}, `Delete the plan template ${name}? Plans already made from it stay as they are, and every version of it stays in the history.`)],
    submit: 'Delete the template',
    work: async () => {
      await call('delete_plan_template', { name });
      box.close();
      notify(`Deleted the plan template ${name}.`);
      await sync().catch(() => {});
      await load(++generation);
    },
  });
  const box = openDialog(`Delete ${name}`, [form]);
  box.id = 'delete-template';
}

// -- the list ----------------------------------------------------------------------------------------------

function entry(info) {
  const writes = can('write');
  const names = Object.keys(info.variables);
  const counts = info.counts || {};
  const button = (label, id, onclick, aria) => h('button', { type: 'button', class: `small ${id}`, 'aria-label': `${aria} ${info.name}`, dataset: { focusKey: `${id}:${info.name}` }, onclick }, label);
  return h(
    'li',
    { class: 'template', dataset: { name: info.name, version: String(info.version) } },
    h('div', { class: 'template-head' }, h('h2', { class: 'template-name' }, info.name), h('span', { class: 'chip template-version' }, `Version ${info.version}`)),
    info.description ? h('p', { class: 'template-description' }, info.description) : null,
    h('p', { class: 'template-counts quiet' }, `Makes ${countsWords(counts.epics, counts.stories, counts.tasks)}.`),
    names.length ? h('ul', { class: 'plain chips template-variables', 'aria-label': `Variables of ${info.name}` }, names.map((name) => h('li', { class: 'chip variable', title: variableWords(name, info.variables[name]), dataset: { variable: name } }, name))) : null,
    h('p', { class: 'template-saved quiet' }, 'Saved by ', actorLabel(info.saved_by, info.saved_by_kind), `, ${moment(info.saved_at)}.`),
    h(
      'div',
      { class: 'template-actions' },
      writes ? button('Make a plan…', 'make-plan-open', () => makeDialog(info), 'Make a plan from') : null,
      button('View', 'view-template-open', () => viewDialog(info.name), 'View'),
      writes ? button('Change…', 'edit-template-open', () => editTemplate(info.name), 'Change') : null,
      writes ? button('Delete…', 'delete-template-open danger', () => deleteDialog(info.name), 'Delete') : null,
    ),
  );
}

function draw() {
  const { templates } = state;
  put(
    places.body,
    templates.length
      ? h('ul', { class: 'plain templates', id: 'templates', 'aria-label': 'Plan templates' }, templates.map(entry))
      : h('div', { class: 'templates-empty', id: 'templates-empty' }, h('p', {}, 'A plan template is a plan you can make again: epics, stories and tasks with variables, such as a version, that you fill in each time.')),
  );
}

async function load(mine) {
  try {
    const page = await read();
    if (mine !== generation || !state) return;
    state.templates = page.items;
    draw();
  } catch (error) {
    if (mine !== generation || !places) return;
    if (!(error instanceof Refusal)) throw error;
    put(places.body, h('p', { class: 'notice refusal', role: 'alert' }, icon('alert', { size: 16 }), h('span', {}, error.message)));
  }
}

export function mount(element) {
  const add = can('write') ? h('button', { type: 'button', id: 'new-template', onclick: () => saveDialog() }, icon('plus', { size: 14 }), 'New template…') : null;
  const body = h('div', { class: 'templates-body', id: 'templates-body' }, loadingView());
  places = { body };
  state = { templates: [] };
  put(element, h('div', { class: 'settings-page templates-page', id: 'templates-page' }, pageHeader('Plan templates', ...(add ? [add] : [])), h('section', { class: 'settings-section' }, body)));
  load(++generation);
}

export function unmount() {
  generation += 1;
  places = null;
  state = null;
}

export function update() {}

// A template saved or deleted by someone else shows at once.
export function refresh(events) {
  if (state && events.some((event) => event.action === 'plan_template_saved' || event.action === 'plan_template_deleted')) load(++generation);
}
