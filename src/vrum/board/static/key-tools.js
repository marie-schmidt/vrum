// What may an agent key do? The picker the Users page shows when a key is made and again when the
// owner changes what a key may do. An agent is offered a chosen set of tools (the standard tools:
// the work loop, items, links, acceptance criteria, notes, recall and search, at most 25 so that a client
// such as Cursor can take them) and a key with no list of its own gets exactly those. The owner can
// instead tick the tools one by one: fewer, so that an agent has only what it needs, or more, such as
// the tools that move data, for a key that a script uses. The server decides and enforces it: the
// agent is not even told about a tool it may not use, and the picker only shows what the server's own
// tool list says.
//
// Five quick choices tick the matching boxes (Standard sends no list at all, so that a tool Vrum
// offers agents later is included; the others send the tools they tick), and ticking by hand moves
// the choice to Custom. Below them are the tools Vrum offers agents, in the areas the Tools page
// groups them by, each area with a box that ticks or clears the whole area, and a closed section of
// the tools that are not offered by default. A tool only people may use (the server says which) is
// shown but cannot be ticked, and is never counted.
//
//   keyTools({ list, tools, prefix, open, blank })  the picker: { element, value(), chosen(), reset(), choice() }. `list` is
//                                            what the server says about its tools, `tools` is what the
//                                            key may use now (null, the default, is the standard tools).
//                                            value() is null (the standard tools) or the sorted names ticked.
//                                            `blank` starts a NEW key with nothing chosen: no quick choice is
//                                            lit and chosen() is false until the person chooses or ticks.
//   allowance(list, tools)                   what a key may use, in words: { choice, label, count, total }.
//   keyCalls({ prefix, codeMode })           how a key calls Vrum, by running code or one tool at a time: { element, chosen(), value() }.
//                                            `codeMode` is null for a new key, which starts with neither chosen.
//   callingWords(codeMode)                   how a key's row says it.
//   keyVerifier({ prefix, verifier })        the box that marks a key a verifier's: { element, value() }.
//   keyExpiry({ prefix, expiresOn, expired }) the last day a key works, empty for a key that never expires: { element, value(), changed() }.
//   toolListing()                            the server's tool list, asked for once.
//
// The two tools of a key that runs code (the server marks them `code_mode`) are not in the picker or in any count: they
// are how a key calls Vrum, and a key is never allowed them by name.

import { get } from './api.js';
import { dateField } from './datepicker.js';
import { h, plural, put } from './dom.js';
import { uid } from './forms.js';
import { icon } from './icons.js';

// The areas of the Tools page that each quick choice adds the changing tools of. Every tool that only
// reads is in every choice.
const WORK_AREAS = ['Work', 'Links', 'Claims and the working loop'];
const RECORD_AREAS = ['Acceptance criteria', 'Notes'];
const NOT_PLACED = 'Not yet placed';
const OTHER = 'Not offered to agents by default';
const LATER = ' A tool added to Vrum later stays off until you allow it.';

const offered = (tool) => Boolean(tool.offered_to_agents);

// `label` is on the choice's button, `name` is how a key that has it is called in a list.
const CHOICES = [
  {
    key: 'standard',
    label: 'Standard',
    name: 'Standard',
    says: () => 'The tools Vrum offers agents: the work loop, items, links, acceptance criteria, notes, recall and search. New agent tools are included. Histories, moving data and confirming are done on the board.',
    allows: offered,
  },
  { key: 'read', label: 'Read only', name: 'Read only', says: () => `Looks at everything an agent is offered and changes nothing.${LATER}`, allows: (tool) => offered(tool) && !tool.changes_something },
  {
    key: 'work',
    label: 'Read and work',
    name: 'Read and work',
    says: () => `Reads, and creates and changes epics, stories and tasks, their links and the claims on them. It does not write acceptance criteria or notes.${LATER}`,
    allows: (tool) => offered(tool) && (!tool.changes_something || WORK_AREAS.includes(tool.area)),
  },
  {
    key: 'record',
    label: 'Read, work and record',
    name: 'Read, work and record',
    says: () => `Does all of that, and also writes acceptance criteria and notes.${LATER}`,
    allows: (tool) => offered(tool) && (!tool.changes_something || WORK_AREAS.includes(tool.area) || RECORD_AREAS.includes(tool.area)),
  },
  {
    key: 'everything',
    label: 'Everything, including administration',
    name: 'Everything',
    says: () => 'Every tool the server has today, including moving data and rebuilding the index, for a key that a backup script uses. A tool added later is not included.',
    allows: () => true,
  },
];
const CUSTOM = { key: 'custom', label: 'Custom', name: 'Custom', says: () => `Your own choice of tools.${LATER}` };

// -- the server's tool list ---------------------------------------------------------------------------------

let pending = null;

// The list is asked for once while the page is open; a failure is not kept, so the next call asks again.
export function toolListing() {
  if (!pending) {
    pending = get('/board/api/tools').catch((error) => {
      pending = null;
      throw error;
    });
  }
  return pending;
}

export function forgetToolListing() {
  pending = null;
}

// -- what a choice means --------------------------------------------------------------------------------------

// The two tools of a key that runs code are how it calls Vrum, not something it may do: they are never in what a key is allowed.
const grantable = (tool) => !tool.people_only && !tool.code_mode;
const same = (a, b) => a.size === b.size && [...a].every((name) => b.has(name));

// The names of the tools each quick choice allows. Only tools a key can be given are in them.
function setsOf(list) {
  const open = list.tools.filter(grantable);
  return Object.fromEntries(CHOICES.map(({ key, allows }) => [key, new Set(open.filter(allows).map((tool) => tool.name))]));
}

// The quick choice a set of tools is exactly, or custom. When two choices are the same tools, the first is meant.
function choiceOf(sets, picked) {
  return CHOICES.find(({ key }) => same(picked, sets[key]))?.key || 'custom';
}

// What a key may use, in words. `tools` is null for the standard tools, or the names it may use; a name
// the server no longer has is ignored, as the server ignores it. A list that is exactly the standard
// tools is still a list of its own: it does not follow the tools Vrum offers agents later.
export function allowance(list, tools) {
  const sets = list ? setsOf(list) : null;
  if (tools === null || tools === undefined) {
    const count = sets ? sets.standard.size : null;
    return { choice: 'standard', label: count === null ? 'Standard' : `Standard, ${plural(count, 'tool')}`, count, total: sets ? sets.everything.size : null };
  }
  if (!list) return { choice: 'custom', label: `Custom, ${plural(tools.length, 'tool')}`, count: tools.length, total: null };
  const have = new Set(tools.filter((name) => sets.everything.has(name)));
  const found = choiceOf(sets, have);
  const choice = found === 'standard' ? 'custom' : found;
  const name = [...CHOICES, CUSTOM].find((item) => item.key === choice).name;
  return { choice, label: `${name}, ${plural(have.size, 'tool')}`, count: have.size, total: sets.everything.size };
}

// -- how a key calls Vrum ---------------------------------------------------------------------------------------

// Every key has one way of calling Vrum. A key that calls it one tool at a time is given the tools it may use. A key that
// runs code is given two tools, one to look up what it may do and one to run a short script that does it, so that a
// whole job is one request. What the key may do (below) is the same either way.
const CALLS = [
  {
    value: 'code',
    label: 'By running code',
    says: () => ['It gets two tools, ', h('code', {}, 'explore'), ' and ', h('code', {}, 'run'), ', and writes a short script that does a whole job in one request.'],
  },
  { value: 'tools', label: 'One tool at a time', says: () => ['It gets each tool it may use as a tool of its own and calls them one at a time.'] },
];

// What a key's row says about how it calls Vrum.
export const callingWords = (codeMode) => (codeMode ? 'Runs code' : 'One tool at a time');

// Whether the key belongs to an agent that checks other agents' work: one box and one quiet line. Unchecked is what every key
// is unless the owner says otherwise, so a new key starts unchecked and says nothing more. value() is whether it is checked.
export function keyVerifier({ prefix = 'key-verifier', verifier = false }) {
  const box = h('input', { type: 'checkbox', id: prefix, name: prefix, checked: Boolean(verifier), 'aria-describedby': `${prefix}-help` });
  return {
    element: h(
      'div',
      { class: 'key-step key-verifier' },
      h('label', { class: 'check', for: prefix }, box, 'Verifies other agents’ work'),
      h('p', { class: 'quiet', id: `${prefix}-help` }, 'May tick the checks marked [verifier] or [github] on work it did not do itself.'),
    ),
    value: () => box.checked,
  };
}

// The last day a key works: a date field (YYYY-MM-DD, or chosen from the calendar) and one quiet line. Empty, which is what a new key
// starts with, is a key that never expires. `expiresOn` is the day the key has now, or null; a key that has one also gets a Clear
// button, and `expired` (the server's own answer) adds one sentence saying what to do about a key that has run out. value() is
// the day typed, or null when the field is empty; changed() is whether that differs from `expiresOn`. The server checks the day.
export function keyExpiry({ prefix = 'key-expires', expiresOn = null, expired = false }) {
  const input = h('input', { type: 'text', id: prefix, name: prefix, value: expiresOn || '', 'aria-describedby': `${prefix}-help` });
  const clear = expiresOn
    ? h(
        'button',
        {
          type: 'button',
          class: 'small key-expiry-clear',
          onclick: () => {
            input.value = '';
            input.focus();
          },
        },
        'Clear',
      )
    : null;
  const value = () => input.value.trim() || null;
  return {
    element: h(
      'div',
      { class: 'key-step key-expiry' },
      h('label', { for: prefix }, 'Expires'),
      h('div', { class: 'key-expiry-row' }, dateField(input, 'the last day the key works'), clear),
      h('p', { class: 'quiet', id: `${prefix}-help` }, 'The last day the key works, in UTC. Leave it empty for a key that never expires.'),
      expired ? h('p', { class: 'quiet key-expired-note', role: 'note' }, 'This key has expired, so the agent using it is refused. Choose a later day to extend it, or make a new key.') : null,
    ),
    value,
    changed: () => value() !== expiresOn,
  };
}

// The two ways, as radio buttons with a quiet line each. `codeMode` is null for a new key, which starts with neither chosen (how
// it calls is a decision, so nothing is lit when the dialog opens), or what an existing key has. value() is true for code and
// false for one tool at a time; chosen() is false until the person chooses.
export function keyCalls({ prefix = 'key-call', codeMode = null }) {
  const title = h('h3', { id: `${prefix}-title` }, 'How it calls Vrum');
  const inputs = CALLS.map(({ value }) => h('input', { type: 'radio', name: prefix, value, id: `${prefix}-${value}`, checked: codeMode === null ? false : (value === 'code') === codeMode }));
  const choices = CALLS.map(({ value, label, says }, at) =>
    h('label', { class: 'call-choice', for: inputs[at].id }, inputs[at], h('span', { class: 'call-choice-text' }, h('strong', {}, label), h('span', { class: 'quiet' }, says()))),
  );
  const picked = () => inputs.find((input) => input.checked);
  return {
    element: h('div', { class: 'key-step key-call', id: prefix, role: 'radiogroup', 'aria-labelledby': title.id }, title, h('div', { class: 'call-choices' }, choices)),
    chosen: () => Boolean(picked()),
    value: () => picked()?.value === 'code',
  };
}

// -- the picker --------------------------------------------------------------------------------------------------

const firstParagraph = (text) => String(text).split(/\n\s*\n/)[0].replace(/\s+/g, ' ').trim();

export function keyTools({ list, tools = null, prefix = 'key-tools', open = false, blank = false }) {
  const sets = setsOf(list);
  const total = sets.everything.size;
  // A new key starts with nothing chosen (`blank`): what an agent may do is a decision, so no quick choice is lit
  // until the person makes one. A key that already exists starts as what it has.
  let chosen = !blank;
  let picked = new Set(blank ? [] : tools === null ? sets.standard : tools.filter((name) => sets.everything.has(name)));
  let searching = false;
  const folded = new Set(); // the areas a person folded away
  const rows = []; // { tool, box, item, words }
  const groups = []; // { area, rows, element, body, box, count, fold }

  // A choice whose tools are exactly an earlier choice's is not shown: it would only be the same button twice.
  const distinct = CHOICES.filter(({ key }, at) => !CHOICES.slice(0, at).some((earlier) => same(sets[earlier.key], sets[key]))).map(({ key }) => key);

  // -- the quick choices
  const order = [...CHOICES, CUSTOM];
  const says = h('p', { class: 'kt-says', id: `${prefix}-says` });
  const buttons = new Map(
    order.map(({ key, label }) => [
      key,
      h('button', { type: 'button', role: 'radio', class: 'kt-choice-option', id: `${prefix}-choice-${key}`, dataset: { choice: key }, 'aria-checked': 'false', tabindex: -1, hidden: key === 'custom' || !distinct.includes(key), onclick: () => choose(key) }, label),
    ]),
  );
  const choices = h(
    'div',
    {
      class: 'segmented kt-choice',
      role: 'radiogroup',
      'aria-label': 'What the key may do',
      'aria-describedby': says.id,
      onkeydown: (event) => {
        const shown = order.map(({ key }) => key).filter((key) => !buttons.get(key).hidden);
        const at = shown.indexOf(event.target.dataset?.choice);
        const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
        const to = at < 0 ? null : step ? shown[(at + step + shown.length) % shown.length] : event.key === 'Home' ? shown[0] : event.key === 'End' ? shown[shown.length - 1] : null;
        if (!to) return;
        event.preventDefault();
        buttons.get(to).focus();
        choose(to);
      },
    },
    [...buttons.values()],
  );

  function choose(key) {
    if (key === 'custom') return;
    chosen = true;
    picked = new Set(sets[key]);
    for (const row of rows) row.box.checked = picked.has(row.tool.name);
    refresh();
  }

  // -- the count and the list's disclosure
  const count = h('p', { class: 'kt-count', id: `${prefix}-count`, role: 'status' });
  const panel = h('div', { class: 'kt-panel', id: `${prefix}-panel`, hidden: !open });
  const toggle = h('button', { type: 'button', class: 'small kt-toggle', id: `${prefix}-toggle`, 'aria-controls': panel.id, onclick: () => show(panel.hidden) });
  function show(on) {
    panel.hidden = !on;
    toggle.setAttribute('aria-expanded', String(on));
    put(toggle, icon('chevron_right', { size: 14, className: 'kt-toggle-chevron' }), on ? 'Hide the tools' : 'Choose tools one by one');
  }

  // -- the search
  const search = h('input', { type: 'search', class: 'kt-search', id: `${prefix}-search`, autocomplete: 'off', placeholder: 'Name, description or area', 'aria-label': 'Search the tools', oninput: filter });
  const clear = h('button', { type: 'button', class: 'kt-clear ghost icon-only small', 'aria-label': 'Clear the search', hidden: true, onclick: () => { search.value = ''; filter(); search.focus(); } }, icon('close', { size: 12 }));
  const none = h('p', { class: 'quiet kt-none', hidden: true }, 'No tool matches.');

  // -- the tools
  function toolRow(tool, withArea) {
    const nameId = uid('kt-name-');
    const markId = uid('kt-mark-');
    const descId = uid('kt-desc-');
    const full = firstParagraph(tool.description);
    const box = h('input', {
      type: 'checkbox',
      class: 'kt-tool-box',
      value: tool.name,
      disabled: Boolean(tool.people_only),
      checked: picked.has(tool.name),
      'aria-labelledby': nameId,
      'aria-describedby': `${markId} ${descId}`,
    });
    box.addEventListener('change', () => {
      chosen = true;
      if (box.checked) picked.add(tool.name);
      else picked.delete(tool.name);
      refresh();
    });
    const mark = tool.people_only
      ? h('span', { class: 'chip kt-mark people', id: markId }, icon('lock', { size: 14 }), 'Only people can do this')
      : h('span', { class: `chip kt-mark ${tool.changes_something ? 'warn' : 'ok'}`, id: markId }, tool.changes_something ? 'Changes something' : 'Only reads');
    const item = h(
      'li',
      { class: `kt-tool${tool.people_only ? ' people-only' : ''}`, dataset: { name: tool.name, changes: tool.changes_something ? '1' : '0', offered: offered(tool) ? '1' : '0' } },
      h(
        'label',
        { class: 'kt-tool-label' },
        box,
        h('span', { class: 'kt-tool-text' }, h('span', { class: 'kt-tool-line' }, h('code', { id: nameId }, tool.name), mark, withArea ? h('span', { class: 'chip kt-area' }, tool.area || NOT_PLACED) : null), h('span', { class: 'kt-tool-desc', id: descId, title: full }, full)),
      ),
    );
    const words = `${tool.name} ${tool.name.replace(/_/g, ' ')} ${tool.area || NOT_PLACED} ${full} ${tool.people_only ? 'only people' : tool.changes_something ? 'changes something' : 'only reads'} ${offered(tool) ? 'offered' : 'not offered'}`.toLowerCase();
    const row = { tool, box, item, words, usable: grantable(tool) };
    rows.push(row);
    return row;
  }

  // One area of the tools Vrum offers agents, or (`other`) the closed section of the rest.
  function addGroup(area, inArea, other = false) {
    if (!inArea.length) return null;
    const mine = inArea.map((tool) => toolRow(tool, other));
    const id = other ? `${prefix}-not-offered` : uid(`${prefix}-area-`);
    const nameId = `${id}-name`;
    const items = h('ul', { class: 'plain kt-tools', id: other ? `${id}-list` : id }, mine.map((row) => row.item));
    const body = other
      ? h('div', { class: 'kt-more', id }, h('p', { class: 'quiet kt-more-note' }, 'Histories, moving data, rebuilding the index and the other things that are done on the board. Ticking one gives the key more than the standard tools.'), items)
      : items;
    const box = other ? null : h('input', { type: 'checkbox', class: 'kt-group-box', 'aria-describedby': `${id}-count` });
    const countNote = h('span', { class: 'kt-group-count', id: `${id}-count` });
    const flip = () => {
      if (folded.has(area)) folded.delete(area);
      else folded.add(area);
      drawFold(group);
    };
    const fold = other
      ? h('button', { type: 'button', class: 'kt-fold kt-fold-named ghost small', 'aria-controls': id, onclick: flip }, icon('chevron_right', { size: 14, className: 'kt-fold-chevron' }), h('span', { class: 'kt-group-name', id: nameId }, area))
      : h('button', { type: 'button', class: 'kt-fold ghost icon-only small', 'aria-controls': id, 'aria-label': `Show or hide the ${area} tools`, onclick: flip }, icon('chevron_right', { size: 14, className: 'kt-fold-chevron' }));
    const element = h(
      'section',
      { class: `kt-group${other ? ' kt-group-other' : ''}`, dataset: { area: other ? 'not-offered' : area }, 'aria-labelledby': nameId },
      h('div', { class: 'kt-group-head' }, fold, other ? null : h('label', { class: 'kt-group-label' }, box, h('span', { class: 'kt-group-name', id: nameId }, area)), countNote),
      body,
    );
    const group = { area, rows: mine, element, body, box, count: countNote, fold };
    box?.addEventListener('change', () => {
      chosen = true;
      for (const row of inScope(group)) {
        if (box.checked) picked.add(row.tool.name);
        else picked.delete(row.tool.name);
        row.box.checked = box.checked;
      }
      refresh();
    });
    groups.push(group);
    return element;
  }

  // The tools of a group that a click on the group's box changes: the ones the search shows, that a key can be given.
  const inScope = (group) => group.rows.filter((row) => row.usable && !row.item.hidden);

  function drawFold(group) {
    const shown = searching || !folded.has(group.area);
    group.body.hidden = !shown;
    group.fold.setAttribute('aria-expanded', String(shown));
    group.fold.disabled = searching;
  }

  // The section of tools that are not offered starts closed, unless the key already has one of them.
  function closeOther() {
    folded.clear();
    if (!rows.some((row) => !offered(row.tool) && picked.has(row.tool.name))) folded.add(OTHER);
  }

  function filter() {
    const asked = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    searching = asked.length > 0;
    for (const row of rows) row.item.hidden = !asked.every((word) => row.words.includes(word));
    for (const group of groups) {
      group.element.hidden = group.rows.every((row) => row.item.hidden);
      drawFold(group);
    }
    none.hidden = rows.some((row) => !row.item.hidden);
    clear.hidden = !searching;
    refresh();
  }

  // Everything that shows what is ticked is drawn from `picked`, so that it cannot disagree with it.
  function refresh() {
    for (const group of groups) {
      const mine = group.rows.filter((row) => row.usable);
      if (group.box) {
        const scope = inScope(group);
        const on = scope.filter((row) => picked.has(row.tool.name)).length;
        group.box.checked = scope.length > 0 && on === scope.length;
        group.box.indeterminate = on > 0 && on < scope.length;
        group.box.disabled = scope.length === 0;
      }
      group.count.textContent = `${mine.filter((row) => picked.has(row.tool.name)).length} of ${mine.length} allowed`;
    }
    const choice = chosen ? choiceOf(sets, picked) : 'none';
    element.dataset.choice = choice;
    buttons.get('custom').hidden = choice !== 'custom';
    const shown = order.map(({ key }) => key).filter((key) => !buttons.get(key).hidden);
    for (const [key, button] of buttons) {
      button.setAttribute('aria-checked', String(key === choice));
      // One button is a tab stop: the lit one, or with nothing chosen yet the first.
      button.tabIndex = key === choice || (choice === 'none' && key === shown[0]) ? 0 : -1;
    }
    says.textContent = choice === 'none' ? 'Choose what this key may do.' : (choice === 'custom' ? CUSTOM : CHOICES.find((item) => item.key === choice)).says(sets);
    count.dataset.empty = String(chosen && picked.size === 0);
    count.textContent =
      choice === 'none'
        ? ''
        : picked.size === 0
          ? 'No tool is ticked. Tick at least one tool, or leave the key with the standard tools.'
          : choice === 'standard'
            ? `${plural(picked.size, 'tool')} (standard)`
            : choice === 'everything'
              ? `All ${plural(total, 'tool')}`
              : `${picked.size} of ${plural(total, 'tool')}`;
  }

  const byName = (a, b) => Number(Boolean(a.changes_something)) - Number(Boolean(b.changes_something)) || a.name.localeCompare(b.name);
  const usable = list.tools.filter((tool) => !tool.code_mode);
  const areas = [...list.areas, ...new Set(usable.map((tool) => tool.area || NOT_PLACED).filter((area) => !list.areas.includes(area)))];
  const rank = (tool) => Math.max(0, areas.indexOf(tool.area || NOT_PLACED));
  const element = h(
    'div',
    {
      class: 'key-tools',
      id: prefix,
      // Enter in a box must not send the form the picker is in.
      onkeydown: (event) => {
        if (event.key === 'Enter' && event.target instanceof HTMLInputElement) event.preventDefault();
      },
    },
    choices,
    says,
    h('div', { class: 'kt-summary' }, count, toggle),
    panel,
  );
  const mainGroups = areas.map((area) => addGroup(area, usable.filter((tool) => offered(tool) && (tool.area || NOT_PLACED) === area).sort(byName)));
  const otherGroup = addGroup(OTHER, usable.filter((tool) => !offered(tool)).sort((a, b) => rank(a) - rank(b) || byName(a, b)), true);
  put(panel, h('div', { class: 'kt-search-field' }, icon('search', { size: 16 }), search, clear), h('div', { class: 'kt-list' }, mainGroups, otherGroup), none);
  closeOther();
  show(open);
  filter();

  return {
    element,
    // What to send: null for the standard tools, or the sorted names of the tools ticked.
    value: () => (same(picked, sets.standard) ? null : [...picked].sort()),
    // Whether the person has chosen anything yet (always, for a key that exists).
    chosen: () => chosen,
    // Back to what a new key starts with: nothing chosen (or the standard tools for a key that exists), no search, the list closed again.
    reset() {
      chosen = !blank;
      picked = new Set(blank ? [] : sets.standard);
      for (const row of rows) row.box.checked = picked.has(row.tool.name);
      search.value = '';
      closeOther();
      show(open);
      filter();
    },
    choice: () => (chosen ? choiceOf(sets, picked) : 'none'),
  };
}
