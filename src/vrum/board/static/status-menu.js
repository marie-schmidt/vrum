// Where something stands, as a button that opens a menu to change it. Every chip that says a status, a
// priority or where a note stands is built here, so that the same thing can be done wherever it is read:
// click it, or press Enter, Space or the Down key on it, and choose. A chip is the same chip it always
// was (the same classes, the same words) with a small chevron after it; a person who can only look at the
// project gets the plain chip and nothing else. The start and the target of an epic, story or task are
// built here too, as buttons that open the calendar instead of a menu (`dateChip`).
//
// What a choice does is the operation an agent would call, at the version the page shows, so the server
// decides: the chip is drawn again only after it said yes, and a refusal is shown in its own words.
// A note has one status (Unconfirmed, Confirmed, Replaced or Withdrawn) and is never edited, so the menu of
// the chip of a note in force offers what can be done to a record instead: confirm or un-confirm it, replace
// it, withdraw it, or change what it concerns, each in the dialog the note's page has. The chip of a note
// that has ended has no menu.
//
// `menuButton` is the menu itself. It draws in the shared look of the board's dropdowns (controls.css), and
// is a menu in the sense of the keyboard: Up and Down, Home and End move, Enter and Space choose, letters
// find a row, Escape closes it and gives the focus back, and a press outside, a scroll or a resize closes
// it. It hangs from the page's own top level (or from the dialog the chip is in), so no table or sidebar can
// cut it off, and it opens above its chip when there is no room below.

import { Refusal, call } from './api.js';
import { can, notify } from './ctx.js';
import { calendarButton } from './datepicker.js';
import { day, h } from './dom.js';
import { icon } from './icons.js';
import { PRIORITIES, PRIORITY, REQUIREMENT_STATUS, REQUIREMENT_STATUSES, SETTABLE, SIZES, SIZE_POINTS, SIZE_WORDS, STATUS, nodeCode, noteStatusLook, pointsWords, priorityIcon, requirementStatusLook, sizeWords, statusIcon } from './labels.js';
import { store, sync } from './store.js';

const EDGE = 8; // What is kept free between the menu and the edge of the window, in pixels.
const GAP = 4; // What is left between the menu and its chip, in pixels.
const TYPING_MS = 700;
const MIN_WIDTH = 176;
const SETTLE_MS = 200; // How long after it opens a scroll that began before is not taken for the page moving.

// The menu that is open, if one is.
let current = null;
let counter = 0;

const plainClick = (event) => event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey;
const chosenAt = (rows) => rows.findIndex((row) => row.option.checked);

// -- the menu -----------------------------------------------------------------------------------------------

// One row of the menu: { value, label, mark (an icon), hint (text on the right), checked, disabled, tone,
// href and run (a link that goes to a page) }. `{ separator: true }` is a line between groups, and
// `{ note: 'words', mark }` is a line of explanation that is read and never chosen. A menu whose rows have
// a `checked` is a choice of one value, and the one that is `checked` is the value now.
function draw(state, options) {
  const { list } = state;
  const notes = [];
  state.rows = [];
  const parts = options.map((option) => {
    if (option.separator) return h('div', { role: 'separator', class: 'vmenu-separator' });
    if (option.note) {
      const note = h('div', { id: `${list.id}-note-${notes.length}`, class: 'vmenu-note' }, option.mark || null, h('span', {}, option.note));
      notes.push(note.id);
      return note;
    }
    const choice = typeof option.checked === 'boolean';
    const props = {
      id: `${list.id}-${state.rows.length}`,
      role: choice ? 'menuitemradio' : 'menuitem',
      'aria-checked': choice ? String(option.checked) : null,
      'aria-disabled': option.disabled ? 'true' : null,
      class: `vselect-option vmenu-option${option.tone ? ` tone-${option.tone}` : ''}`,
      tabindex: -1,
      dataset: { value: option.value ?? '' },
    };
    const inside = [option.mark ? h('span', { class: 'vmenu-mark' }, option.mark) : null, h('span', { class: 'vselect-text' }, option.label), option.hint ? h('span', { class: 'vselect-hint' }, option.hint) : null, option.checked ? icon('check', { size: 16 }) : null];
    const element = option.href ? h('a', { ...props, href: option.href }, ...inside) : h('div', props, ...inside);
    state.rows.push({ option, element, disabled: Boolean(option.disabled) });
    return element;
  });
  list.replaceChildren(...parts);
  if (notes.length) list.setAttribute('aria-describedby', notes.join(' '));
}

// The row that has the focus: it is lit, and the keyboard goes on from it.
function setActive(state, index, { scroll = true } = {}) {
  state.rows[state.active]?.element.removeAttribute('data-active');
  state.active = index;
  const row = state.rows[index];
  if (!row) return;
  row.element.setAttribute('data-active', 'true');
  row.element.focus({ preventScroll: true });
  if (scroll) row.element.scrollIntoView({ block: 'nearest' });
}

// Where the menu goes: under its chip, as wide as that at least, and above it when there is not enough room
// below. It never leaves the window; a long list scrolls inside itself.
function place(state) {
  const { trigger, list } = state;
  const box = trigger.getBoundingClientRect();
  const width = document.documentElement.clientWidth;
  const height = window.innerHeight;
  list.style.minWidth = `${Math.max(Math.ceil(box.width), MIN_WIDTH)}px`;
  list.style.maxHeight = '';
  list.style.left = '0px';
  list.style.top = '0px';
  const natural = list.offsetHeight;
  const below = height - box.bottom - GAP - EDGE;
  const above = box.top - GAP - EDGE;
  const under = natural <= below || below >= above;
  const room = Math.max(0, under ? below : above);
  if (natural > room) list.style.maxHeight = `${room}px`;
  const shown = Math.min(natural, room);
  list.style.top = `${Math.round(under ? box.bottom + GAP : box.top - GAP - shown)}px`;
  list.style.left = `${Math.round(Math.min(Math.max(box.left, EDGE), Math.max(EDGE, width - list.offsetWidth - EDGE)))}px`;
  list.dataset.placement = under ? 'below' : 'above';
  state.box = [box.left, box.top, box.width, box.height].join();
}

function open(trigger, options, onPick, label) {
  close();
  counter += 1;
  const list = h('div', { id: `vmenu-${counter}`, class: 'vselect-list vmenu', role: 'menu', tabindex: -1, 'aria-label': label || null, dataset: { size: 'compact', menu: trigger.dataset.menu || 'menu' } });
  const state = { trigger, list, onPick, rows: [], active: -1, typed: '', typedAt: 0, pointer: null, box: '', openedAt: performance.now() };
  draw(state, typeof options === 'function' ? options() : options);
  listen(state);
  // While a dialog is open the rest of the page cannot be reached, so the menu goes inside it.
  (trigger.closest('dialog[open]') || document.body).append(list);
  current = state;
  trigger.setAttribute('aria-expanded', 'true');
  trigger.setAttribute('aria-controls', list.id);
  place(state);
  const start = chosenAt(state.rows);
  const first = start >= 0 && !state.rows[start].disabled ? start : usable(state, 0, 1);
  if (first >= 0) setActive(state, first);
  else list.focus({ preventScroll: true });
  // The menu goes with its chip: it follows it when the page moves it, and closes when the page draws it again.
  const follow = () => {
    if (current !== state) return;
    if (!trigger.isConnected || !trigger.getClientRects().length) {
      close();
      return;
    }
    const box = trigger.getBoundingClientRect();
    if ([box.left, box.top, box.width, box.height].join() !== state.box) place(state);
    state.frame = requestAnimationFrame(follow);
  };
  state.frame = requestAnimationFrame(follow);
}

function close({ refocus = false } = {}) {
  const state = current;
  if (!state) return;
  current = null;
  cancelAnimationFrame(state.frame);
  state.list.remove();
  const { trigger } = state;
  trigger.setAttribute('aria-expanded', 'false');
  trigger.removeAttribute('aria-controls');
  if (refocus && trigger.isConnected) trigger.focus();
}

// A choice: the menu closes and the focus goes back to the chip, then the work is done. The value it already
// has is not a change. A refusal is said in the server's words.
async function choose(state, index) {
  const row = state.rows[index];
  if (!row || row.disabled) return;
  const { trigger, onPick } = state;
  close({ refocus: true });
  if (row.option.checked) return;
  trigger.setAttribute('aria-busy', 'true');
  try {
    await (row.option.run || onPick)(row.option);
  } catch (error) {
    await refused(error);
  } finally {
    trigger.removeAttribute('aria-busy');
  }
}

// What a change the server refused shows: its own words and, for a change made at an old version, what the
// page does about it. The page then shows what is true now, so that the next try is made at the version the
// server has.
async function refused(error) {
  if (!(error instanceof Refusal)) throw error;
  const stale = /is at version \d+, but you sent version \d+/.test(error.message);
  notify(error.message, { refusal: true, detail: stale ? h('span', { class: 'quiet' }, 'The page now shows the current version. Choose again if you still want this.') : null });
  await sync().catch(() => {});
}

// -- moving through it -------------------------------------------------------------------------------------

// The first row that can be chosen from `from` on, going `step` ways, or -1.
function usable(state, from, step) {
  for (let at = from; at >= 0 && at < state.rows.length; at += step) if (!state.rows[at].disabled) return at;
  return -1;
}

function step(state, direction) {
  const count = state.rows.length;
  for (let moved = 1; moved <= count; moved += 1) {
    const at = (((state.active + direction * moved) % count) + count) % count;
    if (!state.rows[at].disabled) {
      setActive(state, at);
      return;
    }
  }
}

// Letters typed light the next row that starts with them; the same letter again goes on to the next one.
function typeAhead(state, key) {
  const now = Date.now();
  state.typed = now - state.typedAt > TYPING_MS ? key : state.typed + key;
  state.typedAt = now;
  const typed = state.typed.toLowerCase();
  const again = [...typed].every((letter) => letter === typed[0]);
  const needle = again ? typed[0] : typed;
  const from = again ? state.active + 1 : state.active;
  const count = state.rows.length;
  for (let moved = 0; moved < count; moved += 1) {
    const at = (from + moved + count) % count;
    if (!state.rows[at].disabled && state.rows[at].option.label.toLowerCase().startsWith(needle)) {
      setActive(state, at);
      return;
    }
  }
}

function onKeyDown(state, event) {
  const { key } = event;
  const typing = state.typed && Date.now() - state.typedAt <= TYPING_MS;
  const take = () => {
    event.preventDefault();
    event.stopPropagation();
  };
  if (key === 'Escape') {
    take();
    close({ refocus: true });
  } else if (key === 'Tab') close({ refocus: true });
  else if (key === 'ArrowDown') {
    take();
    step(state, 1);
  } else if (key === 'ArrowUp') {
    take();
    step(state, -1);
  } else if (key === 'Home' || key === 'PageUp') {
    take();
    const at = usable(state, 0, 1);
    if (at >= 0) setActive(state, at);
  } else if (key === 'End' || key === 'PageDown') {
    take();
    const at = usable(state, state.rows.length - 1, -1);
    if (at >= 0) setActive(state, at);
  } else if (key === 'Enter' || (key === ' ' && !typing)) {
    take();
    if (state.active >= 0) choose(state, state.active);
  } else if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    take();
    typeAhead(state, key);
  }
}

// Inside the menu the pointer lights a row and a click chooses it. A press is kept from the page, so the
// focus stays in the menu. A pointer that has not moved lights nothing: the keys moving the light must not
// hand it back to the row beneath a pointer that is standing still.
function listen(state) {
  const { list } = state;
  const rowAt = (event) => state.rows.findIndex((row) => row.element === event.target.closest('.vmenu-option'));
  list.addEventListener('keydown', (event) => onKeyDown(state, event));
  list.addEventListener('mousedown', (event) => event.preventDefault());
  list.addEventListener('pointermove', (event) => {
    const moved = event.clientX !== state.pointer?.[0] || event.clientY !== state.pointer?.[1];
    state.pointer = [event.clientX, event.clientY];
    const at = rowAt(event);
    if (moved && at >= 0 && at !== state.active && !state.rows[at].disabled) setActive(state, at, { scroll: false });
  });
  list.addEventListener('click', (event) => {
    event.stopPropagation();
    const at = rowAt(event);
    if (at < 0) return;
    // A link opened with a modifier key is the browser's to open.
    if (state.rows[at].option.href && !plainClick(event)) {
      close();
      return;
    }
    event.preventDefault();
    choose(state, at);
  });
  // Focus leaving the menu for somewhere else (another window, say) closes it.
  list.addEventListener('focusout', (event) => {
    if (current === state && !list.contains(event.relatedTarget) && event.relatedTarget !== state.trigger) close();
  });
}

// Makes `trigger` (a <button>) open a menu. `options` are the rows, or a function that makes them when the
// menu opens, so that they say what is true then. `onPick(option)` runs after a row is chosen; a row of its
// own `run` is called instead. `label` names the menu.
export function menuButton(trigger, options, onPick, { label = '' } = {}) {
  trigger.setAttribute('aria-haspopup', 'menu');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.addEventListener('click', (event) => {
    // The chip is in a row, a card or a heading that does something when it is pressed: this is not that.
    event.preventDefault();
    event.stopPropagation();
    if (current?.trigger === trigger) close({ refocus: true });
    else open(trigger, options, onPick, label);
  });
  trigger.addEventListener('keydown', (event) => {
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && !event.altKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      event.stopPropagation();
      if (current?.trigger !== trigger) open(trigger, options, onPick, label);
    }
  });
  return trigger;
}

// A press anywhere else closes the menu, and so does the page moving under it.
window.addEventListener(
  'pointerdown',
  (event) => {
    if (current && !current.list.contains(event.target) && !current.trigger.contains(event.target)) close();
  },
  true,
);
window.addEventListener(
  'scroll',
  (event) => {
    // A scroll that was already under way when the menu opened (the page scrolled the chip into view, or a
    // finger's momentum) is not the page moving away from it.
    if (current && !current.list.contains(event.target) && performance.now() - current.openedAt > SETTLE_MS) close();
  },
  true,
);
window.addEventListener('resize', () => close());
window.addEventListener('blur', () => close());

// -- chips ------------------------------------------------------------------------------------------------------

const chevron = () => icon('chevron_down', { size: 12, className: 'chip-chevron' });

// A chip: a plain <span> when the person can only look (or there is nothing to choose), otherwise a <button>
// with the same classes and a chevron, wired to its menu. `props.class` is the chip's own classes and
// `props['data-menu']` says which menu. The button is named by what it changes and what it is now, which
// includes the words it shows; the menu by what it changes (`label`).
function chip(props, content, { options, onPick, label, name, focusKey }) {
  if (!can('write') || !options) {
    const { 'data-menu': _menu, ...plain } = props;
    return h('span', plain, ...content);
  }
  const trigger = h('button', { ...props, type: 'button', class: `${props.class} menu-chip`, 'aria-label': name, 'data-focus-key': focusKey }, ...content, chevron());
  return menuButton(trigger, options, onPick, { label });
}

// After the server said yes, the chip is drawn again from what it answered, in the same place and with the
// focus if it had it. A page that draws the item again does the same, so this only has to be right.
function redrawn(trigger, make) {
  if (!trigger.isConnected) return;
  const focused = document.activeElement === trigger;
  const fresh = make();
  trigger.replaceWith(fresh);
  if (focused) fresh.focus();
}

// The change of a work item: the operation an agent calls, at the version the page shows. The page's own
// copy of the work takes the answer at once and then asks what else changed.
async function changeNode(node, args, said, { version, onChanged, redraw }) {
  const result = await call('update_node', { node: node.ref, expected_version: version(), ...args });
  notify(said);
  redraw(result);
  const held = store.nodes.get(node.ref);
  if (held && (result.version ?? 0) >= (held.version ?? 0)) store.nodes.set(node.ref, { ...held, ...result });
  await sync();
  onChanged?.(result);
}

// -- status and priority of epics, stories and tasks ----------------------------------------------------------------

// Ready cannot be chosen: the server works it out from what the item waits for, so it is shown as the value
// it has and explained, and the others are the ones that can be set. Choosing New for something that is
// ready changes nothing (it is new already, with nothing to wait for), so it says nothing.
function statusRows(node) {
  const rows = SETTABLE.map((name) => ({ value: name, label: STATUS[name].label, mark: statusIcon(name, 16), tone: `status-${name}`, checked: node.status === name }));
  if (node.status !== 'ready') return rows;
  return [{ value: 'ready', label: STATUS.ready.label, mark: statusIcon('ready', 16), tone: 'status-ready', checked: true, disabled: true }, ...rows, { separator: true }, { note: 'Ready is worked out from what it waits for.' }];
}

// `node` is an epic, story or task as the server sends it (`ref`, `status`, `version`). `onChanged(node)` runs
// after the change was made; `version()` says which version the page has when it is not the one in `node`.
export function statusChip(node, { onChanged = null, version = () => node.version } = {}) {
  const known = STATUS[node.status] || { label: node.status };
  const props = { class: `chip status status-${node.status}`, title: known.hint, 'data-status': node.status, 'data-menu': 'status' };
  const content = [statusIcon(node.status, 14), known.label];
  const trigger = chip(props, content, {
    options: () => statusRows(node),
    label: 'Status',
    name: `Status: ${known.label}`,
    focusKey: `status:${node.ref}`,
    onPick: async ({ value }) => {
      if (value === 'new' && node.status === 'ready') return;
      const change = (extra = {}) =>
        changeNode(node, { status: value, ...extra }, `${nodeCode(node.kind, node.number)} is now ${STATUS[value].label.toLowerCase()}.`, {
          version,
          onChanged,
          redraw: (result) => redrawn(trigger, () => statusChip({ ...node, ...result }, { onChanged, version })),
        });
      if (value !== 'blocked' && value !== 'dropped') {
        await change();
        return;
      }
      // Blocked and dropped ask for a reason in a dialog of their own, which makes the change; the menu is done once it is open.
      (await dialogs()).whyDialog(node, value, {
        save: async (why) => {
          try {
            await change(why ? { why } : {});
          } catch (error) {
            if (error instanceof Refusal) await sync().catch(() => {});
            throw error;
          }
        },
      });
    },
  });
  return trigger;
}

export function priorityChip(node, { onChanged = null, version = () => node.version } = {}) {
  const known = PRIORITY[node.priority] || { label: node.priority };
  const props = { class: `chip priority priority-${node.priority}`, 'data-priority': node.priority, 'data-menu': 'priority' };
  const content = [priorityIcon(node.priority, 14), known.label];
  const trigger = chip(props, content, {
    options: () => [...PRIORITIES].reverse().map((name) => ({ value: name, label: PRIORITY[name].label, mark: priorityIcon(name, 16), tone: `priority-${name}`, checked: node.priority === name })),
    label: 'Priority',
    name: `Priority: ${known.label}`,
    focusKey: `priority:${node.ref}`,
    onPick: async ({ value }) => {
      await changeNode(node, { priority: value }, `${nodeCode(node.kind, node.number)} priority is now ${PRIORITY[value].label.toLowerCase()}.`, {
        version,
        onChanged,
        redraw: (result) => redrawn(trigger, () => priorityChip({ ...node, ...result }, { onChanged, version })),
      });
    },
  });
  return trigger;
}

// -- size of stories and tasks -----------------------------------------------------------------------------------------

// How big a story or task is: a chip with its letter and words ("Medium, 3 points"), or the dashed "Not set"
// when it has none, which is also the way to give it one. The menu offers the five sizes and "No size", and a
// choice is saved at once. An epic has none of its own (it adds up what is in it), so this is for stories and
// tasks; a person who can only look gets the plain chip or the words "Not set".
export function sizeControl(node, { onChanged = null, version = () => node.version, compact = false } = {}) {
  const has = Object.hasOwn(SIZE_POINTS, node.size);
  const words = has ? sizeWords(node) : 'Not set';
  // In a table cell it is the letter, with the words for a tooltip.
  const props = { class: `chip size ${has ? `size-${node.size}` : 'size-none'}`, title: compact ? words : null, 'data-size': has ? node.size : 'none', 'data-menu': 'size' };
  const content = compact ? [has ? node.size.toUpperCase() : 'Not set'] : [icon('size', { size: 14 }), words];
  const trigger = chip(props, content, {
    options: () => [
      ...SIZES.map((name) => ({ value: name, label: SIZE_WORDS[name], mark: h('span', { class: 'size-mark' }, name.toUpperCase()), hint: pointsWords(SIZE_POINTS[name]), checked: node.size === name })),
      { separator: true },
      { value: '', label: 'No size', mark: icon('dropped', { size: 16 }), checked: !has },
    ],
    label: 'Size',
    name: `Size: ${words.toLowerCase()}`,
    focusKey: `size:${node.ref}`,
    onPick: async ({ value }) => {
      const code = nodeCode(node.kind, node.number);
      await changeNode(node, value ? { size: value } : { clear_size: true }, value ? `${code} is now sized ${SIZE_WORDS[value].toLowerCase()}.` : `Took the size off ${code}.`, {
        version,
        onChanged,
        redraw: (result) => redrawn(trigger, () => sizeControl({ ...node, ...result }, { onChanged, version, compact })),
      });
    },
  });
  return trigger;
}

// -- dates of epics, stories and tasks --------------------------------------------------------------------------------

const DATE_WORDS = { start_on: 'Start', target_on: 'Target' };

// The start or the target of an epic, story or task (`name` is `start_on` or `target_on`): the day, or "Not set".
// For a person who can write it is a button that opens the calendar (datepicker.js), and a day chosen there is
// saved at once, by the operation an agent calls, at the version the page shows. Clear asks the server to take
// the date away. The server refuses a start after the target, and a target before the start, in its own words,
// and the date stays as it was. A person who can only look gets the text.
export function dateChip(node, name, { onChanged = null, version = () => node.version } = {}) {
  const words = DATE_WORDS[name];
  const value = day(node[name]);
  const shown = value || h('span', { class: 'quiet' }, 'Not set');
  if (!can('write')) return shown;
  const trigger = h('button', { type: 'button', class: 'date-value', 'aria-label': `${words}: ${value || 'Not set'}`, dataset: { field: name, focusKey: `date:${node.ref}:${name}` } }, shown, icon('calendar', { size: 14, className: 'date-mark' }));
  const save = async (picked) => {
    if (picked === value) return;
    const code = nodeCode(node.kind, node.number);
    trigger.setAttribute('aria-busy', 'true');
    try {
      await changeNode(node, picked ? { [name]: picked } : { [`clear_${name}`]: true }, picked ? `${code} ${words.toLowerCase()} is now ${picked}.` : `${code} ${words.toLowerCase()} date cleared.`, {
        version,
        onChanged,
        redraw: (result) => redrawn(trigger, () => dateChip({ ...node, ...result }, name, { onChanged, version })),
      });
    } catch (error) {
      await refused(error);
    } finally {
      trigger.removeAttribute('aria-busy');
    }
  };
  return calendarButton(trigger, () => ({ value, label: words, onPick: save, onClear: () => save('') }));
}

// -- acceptance criteria -------------------------------------------------------------------------------------------------

// Open (it stands and is still to be met), Met, Dropped. Nothing sets Met by itself: a person or an agent does.
export function requirementStatusChip(rule, { onChanged = null, version = () => rule.version } = {}) {
  const look = requirementStatusLook(rule.status);
  const trigger = chip({ ...look.props, 'data-menu': 'rule-status' }, look.content, {
    options: () => REQUIREMENT_STATUSES.map((value) => ({ value, label: REQUIREMENT_STATUS[value].label, mark: icon(REQUIREMENT_STATUS[value].icon, { size: 16 }), tone: `rule-${value}`, checked: rule.status === value })),
    label: 'Status',
    name: `Status: ${look.words}`,
    focusKey: `status:rule:${rule.id}`,
    onPick: async ({ value }) => {
      const result = await call('update_rule', { rule_id: rule.id, expected_version: version(), status: value });
      notify(`Acceptance criterion #${rule.id} is now ${REQUIREMENT_STATUS[value].label.toLowerCase()}.`);
      redrawn(trigger, () => requirementStatusChip({ ...rule, ...result }, { onChanged, version }));
      await sync();
      onChanged?.(result);
    },
  });
  return trigger;
}

// -- notes ---------------------------------------------------------------------------------------------------------------

// The dialogs are loaded when one is wanted: the page of a record, which has them, is already loaded by then.
const dialogs = () => import('./record-forms.js');

// What can be done with a note in force, from where it stands: confirm it or take the confirmation back, replace
// it, withdraw it, or change what it concerns (a note is never edited). Confirming and un-confirming open the
// dialog that asks which person reviewed it and, to un-confirm, what they said.
function noteRows(record, onChanged) {
  const confirmed = record.status === 'confirmed';
  return [
    confirmed
      ? { value: 'unconfirm', label: 'Un-confirm…', mark: icon('unconfirmed', { size: 16 }), tone: 'unconfirmed', run: async () => (await dialogs()).confirmDialog(record, 'unconfirmed', { onDone: onChanged }) }
      : { value: 'confirm', label: 'Confirm…', mark: icon('confirmed', { size: 16 }), tone: 'confirmed', run: async () => (await dialogs()).confirmDialog(record, 'confirmed', { onDone: onChanged }) },
    { separator: true },
    { value: 'replace', label: 'Replace…', mark: icon('arrow_right', { size: 16 }), run: async () => (await dialogs()).supersedeDialog(record, { onDone: onChanged }) },
    { value: 'withdraw', label: 'Withdraw…', mark: icon('dropped', { size: 16 }), tone: 'danger', run: async () => (await dialogs()).withdrawDialog(record, { onDone: onChanged }) },
    { separator: true },
    { value: 'concerns', label: 'Change what it applies to…', mark: icon('link', { size: 16 }), run: async () => (await dialogs()).linkDialog(record) },
  ];
}

// Where a note stands: Unconfirmed, Confirmed, Replaced or Withdrawn. Only a note in force can be changed, and
// only by a person who can write to the project, so any other chip is a plain one. The tooltip of a confirmed
// note says who confirmed it and when.
export function noteStatusChip(record, { onChanged = null } = {}) {
  const look = noteStatusLook(record.status);
  const props = { ...look.props, 'data-menu': 'note-status' };
  if (record.status === 'confirmed' && record.confirmed_by) props.title = `Confirmed by ${record.confirmed_by}${record.confirmed_at ? `, ${day(record.confirmed_at)}` : ''}`;
  const inForce = record.status === 'unconfirmed' || record.status === 'confirmed';
  return chip(props, look.content, {
    options: inForce ? () => noteRows(record, onChanged) : null,
    label: 'Status',
    name: `Status: ${look.words}`,
    focusKey: `status:note:${record.id}`,
    onPick: () => {},
  });
}
