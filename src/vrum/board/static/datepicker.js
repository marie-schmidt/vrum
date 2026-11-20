// Vrum's own calendar, for every place a day is chosen. It opens from a button, hangs from the page's own top
// level (or from the dialog the button is in), so that no table, card or dialog can cut it off, and opens
// above the button when there is no room below. It never leaves the window.
//
// A day is a whole number of UTC days (dates.js), so a day never shifts with the person's time zone, and
// "Today" is the UTC day, the one the Roadmap and the overdue count use. The week starts on Monday, as the
// Roadmap's does. The month shows six weeks; the days of the months on either side are quiet and can be
// chosen too.
//
// What a choice does is the caller's: `onPick(day)` gets the day as YYYY-MM-DD, after the calendar has closed
// and the focus is back on the button, and `onClear()` (only offered when the caller can leave a day empty)
// the same. Nothing is checked here: the server decides what a day may be.
//
// With the keyboard the grid of days is one stop in the Tab order. Inside it the arrow keys move a day
// (left and right) or a week (up and down), Home and End go to the first and last day of the week, PageUp and
// PageDown to the month before and after (with Shift, the year), Enter and Space choose, and Escape closes
// the calendar and nothing else (a dialog around it stays open). Tab goes round the calendar's own buttons.
//
// `calendarButton` makes a button open the calendar (the header of an item's page uses it directly), and
// `dateField` is the text field with that button at its end that the forms use: the day is typed as
// YYYY-MM-DD or chosen, and the field's value is always that text.

import { addMonths, dayOfMonth, fromDay, longMonthName, monthStart, parseDay, todayDay, weekday, weekdayName, yearOf } from './dates.js';
import { h, put } from './dom.js';
import { icon } from './icons.js';

const EDGE = 8; // What is kept free between the calendar and the edge of the window, in pixels.
const GAP = 4; // What is left between the calendar and its button, in pixels.
const SETTLE_MS = 200; // How long after it opens a scroll that began before is not taken for the page moving.
const MONDAY = 4; // The day number of a Monday (1970-01-05), to name the columns from.
const DAYS = [0, 1, 2, 3, 4, 5, 6]; // The columns of a week.
const WEEKS = [0, 1, 2, 3, 4, 5]; // The rows of a month.

// The calendar that is open, if one is.
let current = null;
let counter = 0;

const words = (day) => `${weekdayName(day)} ${dayOfMonth(day)} ${longMonthName(day)} ${yearOf(day)}`;
// How many days the first of the week (Monday) is before `day`.
const column = (day) => (weekday(day) + 6) % 7;

// -- drawing -------------------------------------------------------------------------------------------

function cell(state, day, month) {
  const picked = day === state.selected;
  const button = h(
    'button',
    {
      type: 'button',
      class: `dp-day${monthStart(day) === month ? '' : ' dp-outside'}`,
      tabindex: day === state.active ? 0 : -1,
      'aria-label': words(day),
      'aria-current': day === state.today ? 'date' : null,
      dataset: { day: fromDay(day) },
      onclick: () => pick(state, fromDay(day)),
    },
    String(dayOfMonth(day)),
  );
  return h('div', { role: 'gridcell', 'aria-selected': String(picked) }, button);
}

// The month that holds the day the keyboard is on, as six weeks of seven days.
function draw(state) {
  const month = monthStart(state.active);
  const origin = month - column(month);
  put(state.title, `${longMonthName(month)} ${yearOf(month)}`);
  put(
    state.weeks,
    WEEKS.map((week) =>
      h(
        'div',
        { role: 'row', class: 'dp-row' },
        DAYS.map((at) => cell(state, origin + week * 7 + at, month)),
      ),
    ),
  );
}

const focusDay = (state) => state.weeks.querySelector('.dp-day[tabindex="0"]')?.focus({ preventScroll: true });

// -- where it goes -------------------------------------------------------------------------------------

// Under its anchor and starting at its left edge, above it when there is not enough room below, and as low as
// the window allows when there is not enough room on either side. It is inside the window on every side; a
// window too low for the whole calendar scrolls it inside itself.
function place(state) {
  const { anchor, box } = state;
  const rect = anchor.getBoundingClientRect();
  const width = document.documentElement.clientWidth;
  const height = window.innerHeight;
  box.style.maxHeight = '';
  box.style.left = '0px';
  box.style.top = '0px';
  const room = Math.max(0, height - 2 * EDGE);
  const natural = box.offsetHeight;
  const shown = Math.min(natural, room);
  if (natural > room) box.style.maxHeight = `${room}px`;
  let top = rect.bottom + GAP;
  if (top + shown > height - EDGE) {
    const above = rect.top - GAP - shown;
    top = above >= EDGE ? above : height - EDGE - shown;
  }
  box.style.top = `${Math.round(top)}px`;
  box.style.left = `${Math.round(Math.min(Math.max(rect.left, EDGE), Math.max(EDGE, width - box.offsetWidth - EDGE)))}px`;
  box.dataset.placement = top >= rect.bottom ? 'below' : 'above';
  state.rect = [rect.left, rect.top, rect.width, rect.height].join();
}

function open(trigger, { value = '', label = '', anchor = trigger, onPick, onClear = null }) {
  close();
  counter += 1;
  const selected = parseDay(value);
  const today = todayDay();
  const state = { trigger, anchor, key: anchor === trigger ? trigger.dataset.focusKey : null, onPick, onClear, selected, today, active: selected ?? today, rect: '', openedAt: performance.now(), frame: 0 };
  const id = `datepicker-${counter}`;
  const step = (count) => {
    state.active = addMonths(state.active, count);
    draw(state);
    if (!state.box.contains(document.activeElement)) focusDay(state);
  };
  state.title = h('div', { id: `${id}-title`, class: 'dp-title', 'aria-live': 'polite' });
  state.weeks = h('div', { class: 'dp-weeks' });
  const heading = h(
    'div',
    { class: 'dp-head' },
    h('button', { type: 'button', class: 'dp-step', 'aria-label': 'Previous month', onclick: () => step(-1) }, icon('chevron_left', { size: 16 })),
    state.title,
    h('button', { type: 'button', class: 'dp-step', 'aria-label': 'Next month', onclick: () => step(1) }, icon('chevron_right', { size: 16 })),
  );
  const weekdays = h(
    'div',
    { role: 'row', class: 'dp-row dp-weekdays' },
    DAYS.map((at) => h('div', { role: 'columnheader', class: 'dp-weekday', 'aria-label': weekdayName(MONDAY + at) }, weekdayName(MONDAY + at).slice(0, 2))),
  );
  const foot = h(
    'div',
    { class: 'dp-foot' },
    h('button', { type: 'button', class: 'small dp-today', title: `Today is ${fromDay(today)} (UTC)`, onclick: () => pick(state, fromDay(today)) }, 'Today'),
    onClear ? h('button', { type: 'button', class: 'small dp-clear', disabled: !value, onclick: () => clearDay(state) }, 'Clear') : null,
  );
  state.box = h(
    'div',
    { id, class: 'dp', role: 'dialog', 'aria-modal': 'true', 'aria-label': label ? `Choose a day for ${label}` : 'Choose a day', tabindex: -1 },
    heading,
    h('div', { role: 'grid', class: 'dp-grid', 'aria-labelledby': state.title.id }, weekdays, state.weeks),
    foot,
  );
  draw(state);
  listen(state);
  // While a dialog is open the rest of the page cannot be reached, so the calendar goes inside it.
  (trigger.closest('dialog[open]') || document.body).append(state.box);
  current = state;
  trigger.setAttribute('aria-expanded', 'true');
  trigger.setAttribute('aria-controls', id);
  place(state);
  focusDay(state);
  // The calendar goes with its button: it follows it when the page moves it. When the page draws the button
  // again (news of a change redraws the header of an item's page) it goes on from the new one, if it is the
  // same control, as the page names it (`data-focus-key`); otherwise it closes.
  const follow = () => {
    if (current !== state) return;
    if (!state.trigger.isConnected) {
      const same = state.key && document.querySelector(`[data-focus-key="${CSS.escape(state.key)}"]`);
      if (!same) {
        close();
        return;
      }
      state.trigger = same;
      state.anchor = same;
      same.setAttribute('aria-expanded', 'true');
      same.setAttribute('aria-controls', id);
      place(state);
    }
    if (!state.trigger.getClientRects().length) {
      close();
      return;
    }
    const rect = state.anchor.getBoundingClientRect();
    if ([rect.left, rect.top, rect.width, rect.height].join() !== state.rect) place(state);
    state.frame = requestAnimationFrame(follow);
  };
  state.frame = requestAnimationFrame(follow);
}

function close({ refocus = false } = {}) {
  const state = current;
  if (!state) return;
  current = null;
  cancelAnimationFrame(state.frame);
  state.box.remove();
  const { trigger } = state;
  trigger.setAttribute('aria-expanded', 'false');
  trigger.removeAttribute('aria-controls');
  if (refocus && trigger.isConnected) trigger.focus();
}

// A choice: the calendar closes and the focus goes back to the button, then the work is done.
function pick(state, text) {
  close({ refocus: true });
  return state.onPick(text);
}

function clearDay(state) {
  close({ refocus: true });
  return state.onClear();
}

// -- the keyboard and the pointer ----------------------------------------------------------------------

// Where an arrow, Home, End, PageUp or PageDown on the grid goes from `day`, or null for any other key.
function target(day, event) {
  switch (event.key) {
    case 'ArrowLeft':
      return day - 1;
    case 'ArrowRight':
      return day + 1;
    case 'ArrowUp':
      return day - 7;
    case 'ArrowDown':
      return day + 7;
    case 'Home':
      return day - column(day);
    case 'End':
      return day - column(day) + 6;
    case 'PageUp':
      return addMonths(day, event.shiftKey ? -12 : -1);
    case 'PageDown':
      return addMonths(day, event.shiftKey ? 12 : 1);
    default:
      return null;
  }
}

function onKeyDown(state, event) {
  const { key } = event;
  // The page's own keys (the [ and ] of an item's page, Ctrl+K) are not for the calendar.
  event.stopPropagation();
  if (key === 'Escape') {
    event.preventDefault();
    close({ refocus: true });
  } else if (key === 'Tab') {
    // Tab goes round the calendar's own buttons, by hand, so that it does the same in a browser whose Tab key
    // skips buttons.
    const stops = [...state.box.querySelectorAll('button')].filter((button) => !button.disabled && button.tabIndex >= 0);
    const at = stops.indexOf(document.activeElement);
    const next = at < 0 ? (event.shiftKey ? stops.length - 1 : 0) : (at + (event.shiftKey ? stops.length - 1 : 1)) % stops.length;
    event.preventDefault();
    stops[next].focus();
  } else if (event.target.classList.contains('dp-day') && !event.ctrlKey && !event.metaKey && !event.altKey) {
    const to = target(state.active, event);
    if (to === null || (event.shiftKey && !key.startsWith('Page'))) return;
    event.preventDefault();
    state.active = to;
    draw(state);
    focusDay(state);
  }
}

function listen(state) {
  const { box } = state;
  box.addEventListener('keydown', (event) => onKeyDown(state, event));
  // A press is kept from the page, so the focus stays in the calendar.
  box.addEventListener('mousedown', (event) => event.preventDefault());
  // Focus leaving for another element (not just being lost when a day is drawn again) closes it.
  box.addEventListener('focusout', (event) => {
    if (current === state && event.relatedTarget && !box.contains(event.relatedTarget) && event.relatedTarget !== state.trigger) close();
  });
}

// A press anywhere else closes the calendar, and so does the page moving under it.
window.addEventListener(
  'pointerdown',
  (event) => {
    if (current && !current.box.contains(event.target) && !current.trigger.contains(event.target)) close();
  },
  true,
);
window.addEventListener(
  'scroll',
  (event) => {
    if (current && !current.box.contains(event.target) && performance.now() - current.openedAt > SETTLE_MS) close();
  },
  true,
);
window.addEventListener('resize', () => close());
window.addEventListener('blur', () => close());

// -- what the pages use --------------------------------------------------------------------------------

// Makes `trigger` (a <button>) open the calendar. `options()` is asked when it opens, so that it says what is
// true then: { value (YYYY-MM-DD, or anything else for no day), label (what is being chosen), anchor (the
// element the calendar hangs from, the button by default), onPick(day), onClear (leave it out when the day
// cannot be emptied) }.
export function calendarButton(trigger, options) {
  trigger.setAttribute('aria-haspopup', 'dialog');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.addEventListener('click', () => {
    if (current?.trigger === trigger) close({ refocus: true });
    else open(trigger, options());
  });
  return trigger;
}

const write = (input, text) => {
  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
};

// A text field for a day, with the calendar's button at its end. `input` is the field as the page made it
// (its id, name and value); it becomes a plain text field holding YYYY-MM-DD, nothing in the browser
// checks it, and what is typed is sent as it is. `label` names what is chosen. Choosing in the calendar
// writes the day into the field and tells the page the field changed, as typing would. The answer is the box
// to put on the page in the field's place.
export function dateField(input, label) {
  input.type = 'text';
  input.autocomplete = 'off';
  input.spellcheck = false;
  if (!input.placeholder) input.placeholder = 'YYYY-MM-DD';
  input.classList.add('date-input');
  const button = h('button', { type: 'button', class: 'dp-open', 'aria-label': `Choose a day for ${label}` }, icon('calendar', { size: 16 }));
  const box = h('div', { class: 'date-field' }, input, button);
  calendarButton(button, () => ({ value: input.value.trim(), label, anchor: box, onPick: (text) => write(input, text), onClear: () => write(input, '') }));
  return box;
}
