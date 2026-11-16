// Tools: the reference of every MCP tool, made from the server's own live tool list (name,
// description, input fields, whether it changes anything), so that it cannot drift from the code.
// What the list cannot know, the board adds from its map: the area of each tool, and where on the
// board a person does the same thing, or that it is for agents only and why. A tool that only reads
// has a "Try it" form built from its input fields; the server decides which tools those are.

import { Refusal, get, tryTool } from './api.js';
import { hrefTo, ui } from './ctx.js';
import { h, plural, put } from './dom.js';

let root = null;
let params = new URLSearchParams();
let parts = {};
let listing = null; // what the server said about its tools
let generation = 0;

// -- the page -------------------------------------------------------------------------------------

export async function mount(element, initial) {
  root = element;
  params = initial;
  listing = null;
  const mine = ++generation;
  const search = h('input', { type: 'search', id: 'tool-search', name: 'q', value: params.get('q') || '', autocomplete: 'off', placeholder: 'a word of a name, a description or a page', 'aria-label': 'Search the tools' });
  parts = {
    search,
    summary: h('p', { class: 'count', id: 'count', role: 'status' }),
    problem: h('p', { class: 'problem', role: 'alert', id: 'tools-problem' }),
    connect: h('section', { id: 'connecting' }),
    list: h('div', { id: 'tool-list' }),
  };
  put(
    root,
    h('h1', {}, 'Tools'),
    h('p', { class: 'help' }, 'Everything an agent can do on this server, as the server lists it now. Each tool says where on the board a person does the same thing, or why it is for agents only.'),
    parts.problem,
    parts.connect,
    h(
      'form',
      {
        class: 'filter',
        role: 'search',
        onsubmit: (event) => {
          event.preventDefault();
          ui.navigate({ q: search.value.trim() || null });
        },
      },
      h('label', { for: 'tool-search' }, 'Search'),
      search,
      h('button', { type: 'submit', class: 'primary' }, 'Search'),
      h('button', { type: 'button', id: 'clear-tool-search', onclick: () => ui.navigate({ q: null }) }, 'Clear'),
    ),
    parts.summary,
    parts.list,
  );
  try {
    const found = await get('/board/api/tools');
    if (mine !== generation || !root) return;
    listing = found;
  } catch (error) {
    if (mine !== generation || !root) return;
    if (!(error instanceof Refusal)) throw error;
    parts.problem.textContent = error.message;
    return;
  }
  drawConnect();
  draw();
}

export function unmount() {
  generation += 1;
  root = null;
}

export function update(next) {
  params = next;
  if (!root || !listing) return;
  parts.search.value = params.get('q') || '';
  draw();
}

export function refresh() {}

// -- connecting ---------------------------------------------------------------------------------------

function drawConnect() {
  const address = `${location.origin}${listing.mcp_path}`;
  const command = `claude mcp add --transport http vrum ${address} --header "Authorization: Bearer $VRUM_TOKEN"`;
  const file = JSON.stringify({ mcpServers: { vrum: { type: 'http', url: address, headers: { Authorization: 'Bearer ${VRUM_TOKEN}' } } } }, null, 2);
  put(
    parts.connect,
    h('h2', {}, 'Connecting an agent'),
    h('dl', { class: 'connect' }, h('dt', {}, 'The endpoint'), h('dd', {}, h('code', { id: 'endpoint' }, address)), h('dt', {}, 'The token'), h('dd', {}, 'Send it with every request, in a header: ', h('code', {}, 'Authorization: Bearer <the token>'), '. The token is VRUM_TOKEN in the .env file of this server. It is never shown here, and never put in a file you commit: let the client read it from the environment.')),
    h('details', { id: 'example' }, h('summary', {}, 'An example client configuration'), h('p', {}, 'For Claude Code:'), h('pre', {}, h('code', {}, command)), h('p', {}, 'Or a project file (.mcp.json), which holds no token because each client reads VRUM_TOKEN from the environment:'), h('pre', {}, h('code', {}, file))),
  );
}

// -- the list -----------------------------------------------------------------------------------------

function words(tool) {
  return `${tool.name} ${tool.area || ''} ${tool.description} ${tool.where || ''} ${tool.agents_only || ''}`.toLowerCase();
}

function draw() {
  const asked = (params.get('q') || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
  const all = listing.tools;
  const shown = all.filter((tool) => asked.every((word) => words(tool).includes(word)));
  const reading = all.filter((tool) => !tool.changes_something).length;
  const agents = all.filter((tool) => tool.agents_only).length;
  parts.summary.textContent = `${plural(all.length, 'tool')}: ${reading} only read, ${all.length - reading} change something, ${agents} for agents only.${shown.length !== all.length ? ` ${plural(shown.length, 'tool')} match.` : ''}`;
  const areas = [...listing.areas, ...new Set(shown.filter((tool) => !listing.areas.includes(tool.area)).map((tool) => tool.area))];
  put(
    parts.list,
    areas.map((area) => {
      const inArea = shown.filter((tool) => tool.area === area).sort((a, b) => a.name.localeCompare(b.name));
      return inArea.length ? h('section', { class: 'tool-area', dataset: { area: area || 'unclassified' } }, h('h2', {}, `${area || 'Not yet placed'} (${inArea.length})`), h('div', { class: 'tools' }, inArea.map(toolEntry))) : null;
    }),
    shown.length ? null : h('p', { class: 'quiet' }, 'No tool matches. The search looks at names, descriptions, areas and where on the board.'),
  );
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

function fieldTable(fields) {
  if (!fields.length) return h('p', { class: 'quiet' }, 'It takes no fields.');
  return h(
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
  );
}

function whereOnTheBoard(tool) {
  if (tool.agents_only) return h('p', { class: 'where agents' }, h('span', { class: 'chip' }, 'For agents only'), ` ${tool.agents_only}`);
  if (!tool.where) return h('p', { class: 'where problem' }, 'This tool has not been placed on the board or marked as for agents only yet.');
  return h('p', { class: 'where' }, h('span', { class: 'chip' }, 'On the board'), ' ', h('a', { href: hrefTo(tool.page) }, tool.where));
}

function toolEntry(tool) {
  const fields = fieldsOf(tool);
  const [first, ...rest] = tool.description.split(/\n\s*\n/);
  return h(
    'article',
    { class: 'tool', id: `tool-entry-${tool.name}`, dataset: { name: tool.name, changes: tool.changes_something ? '1' : '0', agents: tool.agents_only ? '1' : '0' } },
    h('h3', {}, h('code', {}, tool.name), ' ', h('span', { class: `chip ${tool.changes_something ? 'changes' : 'reads'}` }, tool.changes_something ? 'Changes something' : 'Only reads')),
    h('p', { class: 'first' }, first.replace(/\s+/g, ' ')),
    rest.length ? h('details', { class: 'more' }, h('summary', {}, 'The whole description'), rest.map((paragraph) => h('p', {}, paragraph.replace(/\s+/g, ' ')))) : null,
    whereOnTheBoard(tool),
    h('details', { class: 'fields-list' }, h('summary', {}, `Fields (${fields.length})`), fieldTable(fields)),
    tool.changes_something ? null : tryIt(tool, fields),
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
  const result = h('pre', { class: 'result', hidden: true, 'aria-label': `What ${tool.name} returned` });
  const problem = h('p', { class: 'problem', role: 'alert' });
  const run = h('button', { type: 'submit', class: 'primary try-run' }, 'Try it');
  const form = h(
    'form',
    {
      class: 'try-form',
      novalidate: true,
      onsubmit: async (event) => {
        event.preventDefault();
        problem.textContent = '';
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
          problem.textContent = error.message;
        } finally {
          run.disabled = false;
        }
      },
    },
    controls.map(({ item, element, id }) => h('div', { class: 'field' }, h('label', { for: id }, `${item.name}${item.required ? ' (needed)' : ''}`), element, item.spec.description ? h('p', { class: 'quiet', id: `${id}-help` }, item.spec.description) : null)),
    h('div', { class: 'buttons' }, run),
    problem,
    result,
  );
  return h('details', { class: 'try' }, h('summary', {}, 'Try it'), h('p', { class: 'quiet' }, 'This only reads: it shows what the tool returns, and changes nothing.'), form);
}
