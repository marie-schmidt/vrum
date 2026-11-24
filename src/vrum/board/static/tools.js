// Tools: the reference of every MCP tool. The tools are made from the server's own live tool list
// (name, description, input fields, whether it changes anything, whether agents are offered it), so that
// it cannot drift from the code, and are grouped by the areas the server names only so that they are
// easier to find. A client can take only so many tools, so an agent is offered a chosen set (a
// quiet chip on each tool says which) and the rest are done on the board, or by a key whose owner
// allows them; a switch shows either kind alone. A key that runs code is given two tools of its
// own (the server marks them `code_mode`): they are neither offered to agents nor board only, so
// they wear a chip of their own and are counted by their own switch. What the list cannot know,
// the board adds from its map: the area of each tool, and where on the board a person does the
// same thing, or that it has no page and why. A tool that only reads has a "Try it" form built
// from its input fields; the server decides which tools those are. How to connect an agent, and
// its keys, are on the Users page.

import { Refusal, get, tryTool } from './api.js';
import { ui, viewLink } from './ctx.js';
import { h, plural, put } from './dom.js';
import { refuse } from './forms.js';
import { icon } from './icons.js';
import { pageHeader } from './page.js';

let root = null;
let params = new URLSearchParams();
let parts = {};
let listing = null; // what the server said about its tools
let generation = 0;
let folded = new Set(); // the groups a person has folded away; every group starts open

// Which tools the list shows: all of them, the ones agents are offered, the rest, or the two that a key that runs code
// is given. A tool is in exactly one of the last three. The switch for the last is there only when the server has such tools.
const forCode = (tool) => Boolean(tool.code_mode);
const SHOWS = [
  { value: 'all', label: 'All tools', matches: () => true },
  { value: 'offered', label: 'Offered to agents', matches: (tool) => !forCode(tool) && Boolean(tool.offered_to_agents) },
  { value: 'board', label: 'Board and administration only', matches: (tool) => !forCode(tool) && !tool.offered_to_agents },
  { value: 'code', label: 'For keys that run code', matches: forCode, onlyWhenAny: true },
];
const showOf = (query) => SHOWS.find(({ value }) => value === query.get('show')) || SHOWS[0];

// -- the page -------------------------------------------------------------------------------------

export async function mount(element, initial) {
  root = element;
  params = initial;
  listing = null;
  folded = new Set();
  const mine = ++generation;
  const search = h('input', { type: 'search', id: 'tool-search', name: 'q', value: params.get('q') || '', autocomplete: 'off', placeholder: 'Name, description or page', 'aria-label': 'Search the tools' });
  const clear = h('button', { type: 'button', id: 'clear-tool-search', hidden: !search.value, onclick: () => ui.navigate({ q: null }) }, icon('clear', { size: 16 }), 'Clear');
  search.addEventListener('input', () => {
    clear.hidden = !search.value;
  });
  parts = {
    search,
    clear,
    summary: h('p', { class: 'count tools-lead', id: 'count', role: 'status' }),
    show: h(
      'div',
      { class: 'segmented tools-show', id: 'tools-show', role: 'group', 'aria-label': 'Which tools to list', hidden: true },
      SHOWS.map(({ value, label }) => h('button', { type: 'button', id: `tools-show-${value}`, dataset: { show: value }, 'aria-pressed': 'false', onclick: () => ui.navigate({ show: value === 'all' ? null : value }) }, label)),
    ),
    problem: h('p', { class: 'problem', role: 'alert', id: 'tools-problem' }),
    list: h('div', { id: 'tool-list' }, h('p', { class: 'loading', role: 'status' }, 'Loading the tools…')),
  };
  const form = h(
    'form',
    {
      class: 'filter tools-search',
      role: 'search',
      onsubmit: (event) => {
        event.preventDefault();
        ui.navigate({ q: search.value.trim() || null });
      },
    },
    h('label', { for: 'tool-search', class: 'sr-only' }, 'Search'),
    icon('search', { size: 16, className: 'tools-search-icon' }),
    search,
    clear,
  );
  put(
    root,
    h('div', { class: 'settings-page tools-page' }, [
      pageHeader('Tools', viewLink('users', {}, { id: 'tools-agent-keys', class: 'action-link' }, icon('key', { size: 16 }), 'Agent keys')),
      parts.problem,
      h('section', { id: 'tools-section', class: 'settings-section tools-section', 'aria-labelledby': 'tools-title' }, h('h2', { id: 'tools-title', class: 'sr-only' }, 'Tools'), parts.summary, form, parts.show, parts.list),
    ]),
  );
  try {
    const found = await get('/board/api/tools');
    if (mine !== generation || !root) return;
    listing = found;
  } catch (error) {
    if (mine !== generation || !root) return;
    if (!(error instanceof Refusal)) throw error;
    refuse(parts.problem, error.message);
    parts.list.replaceChildren();
    return;
  }
  draw();
}

export function unmount() {
  generation += 1;
  root = null;
}

export function update(next) {
  const before = params;
  params = next;
  if (!root || !listing) return;
  // Only a new search or a different switch changes the list: leaving the page alone keeps open forms and typed fields.
  const searched = (next.get('q') || '') !== (before.get('q') || '');
  if (!searched && showOf(next) === showOf(before)) return;
  // Changing only the switch must not wipe words typed in the box and not yet searched for.
  if (searched) {
    parts.search.value = params.get('q') || '';
    parts.clear.hidden = !parts.search.value;
  }
  draw();
}

export function refresh() {}

// -- the list -----------------------------------------------------------------------------------------

function words(tool) {
  const offer = forCode(tool) ? 'for keys that run code' : tool.offered_to_agents ? 'offered to agents' : 'board and administration only';
  return `${tool.name} ${tool.area || ''} ${tool.description} ${tool.where || ''} ${tool.agents_only || ''} ${offer}`.toLowerCase();
}

function draw() {
  const asked = (params.get('q') || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
  const show = showOf(params);
  const all = listing.tools;
  const shown = all.filter((tool) => show.matches(tool) && asked.every((word) => words(tool).includes(word)));
  parts.summary.textContent = shown.length !== all.length ? `${plural(shown.length, 'tool')} shown` : '';
  parts.show.hidden = false;
  for (const button of parts.show.querySelectorAll('button')) {
    const choice = SHOWS.find(({ value }) => value === button.dataset.show);
    const count = all.filter(choice.matches).length;
    button.hidden = Boolean(choice.onlyWhenAny) && count === 0;
    button.setAttribute('aria-pressed', String(choice === show));
    put(button, `${choice.label} `, h('span', { class: 'tools-show-count' }, String(count)));
  }
  const areas = [...listing.areas, ...new Set(shown.filter((tool) => !listing.areas.includes(tool.area)).map((tool) => tool.area))];
  put(
    parts.list,
    areas.map((area) => {
      const inArea = shown.filter((tool) => tool.area === area).sort((a, b) => Number(Boolean(a.changes_something)) - Number(Boolean(b.changes_something)) || a.name.localeCompare(b.name));
      return inArea.length ? group(area, inArea, asked.length > 0) : null;
    }),
    shown.length ? null : h('p', { class: 'empty' }, 'No tool matches.'),
  );
}

// One group of tools: a quiet sub-heading with how many, which folds away. While a search is
// narrowing the list every group is open, so that nothing it found is hidden.
function group(area, inArea, searching) {
  const name = area || 'Not yet placed';
  const id = `tools-group-${name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
  const open = searching || !folded.has(name);
  const body = h('div', { class: 'tools', id, hidden: !open }, inArea.map(toolEntry));
  const toggle = h(
    'button',
    {
      type: 'button',
      class: 'group-toggle',
      'aria-expanded': String(open),
      'aria-controls': id,
      onclick: () => {
        const opening = body.hidden;
        body.hidden = !opening;
        if (opening) folded.delete(name);
        else folded.add(name);
        toggle.setAttribute('aria-expanded', String(opening));
      },
    },
    icon('chevron_right', { size: 14, className: 'group-chevron' }),
    h('span', { class: 'group-name' }, name),
    ' ',
    h('span', { class: 'group-count' }, plural(inArea.length, 'tool')),
  );
  return h('section', { class: 'tool-area', dataset: { area: area || 'unclassified' } }, h('h3', { class: 'tool-group-title' }, toggle), body);
}

// -- one tool -----------------------------------------------------------------------------------------

// What a field takes, in words, from its description in the schema.
function typeOf(spec) {
  if (spec.enum) return spec.enum.map((value) => JSON.stringify(value)).join(' or ');
  if (spec.anyOf) {
    const kinds = spec.anyOf.filter((option) => option.type !== 'null').map(typeOf);
    return kinds.join(' or ') || 'anything';
  }
  if (spec.type === 'array') return `a list of ${spec.items ? typeOf(spec.items) : 'values'}`;
  return { integer: 'a whole number', number: 'a number', string: 'text', boolean: 'true or false', object: 'an object' }[spec.type] || spec.type || 'anything';
}

function fieldsOf(tool) {
  const properties = tool.input_schema.properties || {};
  const required = new Set(tool.input_schema.required || []);
  return Object.entries(properties).map(([name, spec]) => ({ name, spec, required: required.has(name), type: typeOf(spec) }));
}

function fieldTable(fields, toolName) {
  return h(
    'div',
    { class: 'table-scroll', role: 'region', tabindex: '0', 'aria-label': `Fields of ${toolName}` },
    h(
      'table',
      { class: 'fields' },
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'Field'), h('th', { scope: 'col' }, 'Takes'), h('th', { scope: 'col' }, 'What it is'))),
      h(
        'tbody',
        {},
        fields.map(({ name, spec, required, type }) =>
          h('tr', { dataset: { field: name } }, h('th', { scope: 'row' }, h('code', {}, name), required ? h('span', { class: 'chip' }, 'needed') : null), h('td', {}, type), h('td', {}, `${spec.description || ''}${'default' in spec && spec.default !== null ? ` (Starts as ${JSON.stringify(spec.default)}.)` : ''}`)),
        ),
      ),
    ),
  );
}

function whereOnTheBoard(tool) {
  if (tool.agents_only) return h('p', { class: 'where agents' }, h('span', { class: 'chip' }, 'Not on the board'), ` ${tool.agents_only}`);
  if (!tool.where) return h('p', { class: 'where problem' }, 'This tool has not been placed on the board or marked as having no page there yet.');
  // Where on the board a tool is done is always a page: the three views, Search, Activity, the
  // coverage report, or a settings page.
  return h('p', { class: 'where' }, h('span', { class: 'chip info' }, 'On the board'), ' ', viewLink(tool.page, {}, {}, tool.where));
}

// Whether agents are offered the tool: a word and a sign, in a quiet chip, so that colour says nothing alone. The two
// tools of a key that runs code are not offered to agents in general, so they say who they are for instead.
function offerChip(tool) {
  if (forCode(tool)) return h('span', { class: 'chip tool-offer for-code', dataset: { offered: '0', code: '1' } }, icon('agent', { size: 14 }), 'For keys that run code');
  return tool.offered_to_agents
    ? h('span', { class: 'chip tool-offer offered', dataset: { offered: '1' } }, icon('bot', { size: 14 }), 'Offered to agents')
    : h('span', { class: 'chip tool-offer board-only', dataset: { offered: '0' } }, icon('board', { size: 14 }), 'Board and administration only');
}

function toolEntry(tool) {
  const fields = fieldsOf(tool);
  const [first, ...rest] = tool.description.split(/\n\s*\n/);
  return h(
    'article',
    { class: 'tool', id: `tool-entry-${tool.name}`, dataset: { name: tool.name, changes: tool.changes_something ? '1' : '0', agents: tool.agents_only ? '1' : '0', offered: tool.offered_to_agents && !forCode(tool) ? '1' : '0', code: forCode(tool) ? '1' : '0' } },
    h(
      'div',
      { class: 'tool-head' },
      h('h4', { class: 'tool-name' }, h('code', {}, tool.name)),
      h('div', { class: 'tool-chips' }, h('span', { class: `chip ${tool.changes_something ? 'warn changes' : 'ok reads'}` }, tool.changes_something ? 'Changes something' : 'Only reads'), offerChip(tool)),
    ),
    h(
      'div',
      { class: 'tool-body' },
      h('p', { class: 'first' }, first.replace(/\s+/g, ' ')),
      rest.length ? h('details', { class: 'more' }, h('summary', {}, 'The whole description'), rest.map((paragraph) => h('p', {}, paragraph.replace(/\s+/g, ' ')))) : null,
      whereOnTheBoard(tool),
      tool.people_only ? h('p', { class: 'offer-note' }, 'Only a person can do this: no agent key can be given it.') : null,
      forCode(tool) ? h('p', { class: 'offer-note' }, 'Only a key that runs code is given this tool. How a key calls Vrum is chosen on the Users page.') : null,
      fields.length ? h('details', { class: 'fields-list' }, h('summary', {}, `Fields (${fields.length})`), fieldTable(fields, tool.name)) : null,
      tool.changes_something ? null : tryIt(tool, fields),
    ),
  );
}

// -- try it ---------------------------------------------------------------------------------------------

// A control for one field, and how to read what was typed into it.
function control(name, spec) {
  const id = `try-${name}-${Math.random().toString(36).slice(2, 8)}`;
  const options = spec.anyOf ? spec.anyOf.filter((option) => option.type !== 'null') : [spec];
  const kind = (options[0] && options[0].type) || 'string';
  const hint = 'default' in spec && spec.default !== null ? `starts as ${JSON.stringify(spec.default)}` : '';
  if (kind === 'boolean') {
    const select = h('select', { id, name }, ['', 'true', 'false'].map((value) => h('option', { value }, value || '(leave out)')));
    return { element: select, read: () => (select.value === '' ? undefined : select.value === 'true'), id };
  }
  if (kind === 'integer' || kind === 'number') {
    const input = h('input', { id, name, type: 'number', step: kind === 'integer' ? 1 : 'any', placeholder: hint });
    return { element: input, read: () => (input.value === '' ? undefined : Number(input.value)), id };
  }
  if (kind === 'array') {
    const numbers = options[0].items && ['integer', 'number'].includes(options[0].items.type);
    const input = h('input', { id, name, type: 'text', placeholder: 'values separated by commas' });
    return { element: input, read: () => (input.value.trim() === '' ? undefined : input.value.split(',').map((part) => (numbers ? Number(part) : part.trim()))), id };
  }
  if (kind === 'object') {
    const area = h('textarea', { id, name, rows: 3, placeholder: 'JSON' });
    return {
      element: area,
      read: () => {
        if (area.value.trim() === '') return undefined;
        try {
          return JSON.parse(area.value);
        } catch {
          throw new Refusal(`${name} has to be JSON, and what was typed is not.`);
        }
      },
      id,
    };
  }
  const input = h('input', { id, name, type: 'text', placeholder: hint, autocomplete: 'off' });
  return { element: input, read: () => (input.value === '' ? undefined : input.value), id };
}

function tryIt(tool, fields) {
  const controls = fields.map((item) => ({ item, ...control(item.name, item.spec) }));
  const result = h('pre', { class: 'result', hidden: true, tabindex: '0', 'aria-label': `What ${tool.name} returned` });
  const problem = h('p', { class: 'problem', role: 'alert' });
  const run = h('button', { type: 'submit', class: 'primary try-run' }, 'Run');
  const form = h(
    'form',
    {
      class: 'try-form',
      novalidate: true,
      onsubmit: async (event) => {
        event.preventDefault();
        problem.replaceChildren();
        result.hidden = true;
        run.disabled = true;
        try {
          const args = {};
          for (const { item, read } of controls) {
            const value = read();
            if (value !== undefined) args[item.name] = value;
          }
          const answer = await tryTool(tool.name, args);
          result.textContent = JSON.stringify(answer, null, 2);
          result.hidden = false;
        } catch (error) {
          if (!(error instanceof Refusal)) throw error;
          refuse(problem, error.message);
        } finally {
          run.disabled = false;
        }
      },
    },
    controls.map(({ item, element, id }) => h('div', { class: 'field' }, h('label', { for: id }, `${item.name}${item.required ? ' (needed)' : ''}`), element, item.spec.description ? h('p', { class: 'quiet', id: `${id}-help` }, item.spec.description) : null)),
    h('div', { class: 'actions' }, run),
    problem,
    result,
  );
  return h('details', { class: 'try' }, h('summary', {}, 'Try it'), form);
}
