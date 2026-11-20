// Dropdowns. A native <select> opens a menu that belongs to the browser and the operating system,
// which a page cannot style, so every <select> on the board would look unlike the rest of it. This
// one module gives every <select> a menu that the board draws, in either theme, and changes nothing
// else about it: the <select> itself stays where it is, is what a form reads, what a label names and
// what `value`, `selectedIndex`, `disabled` and the change events belong to, and keeps the width and
// the place the style sheets give it. Only its menu is ours, so a value set by code, options built
// by code and a test that picks an option all go on working as they did.
//
// Nothing has to be called: the listeners are on the window, so a <select> added to the page later
// works at once. A <select> with a `data-native` attribute (or `multiple` or `size`, which are list
// boxes) keeps the browser's own menu. A wrapper with `data-select-anchor` is what the menu lines up
// with, for a <select> that has no edge of its own. The look is in controls.css.
//
// The menu is a select-only combobox: focus stays on the <select>, which names the open menu
// (`aria-controls`, `aria-expanded`) and the option that is lit (`aria-activedescendant`).

import { h } from './dom.js';
import { icon } from './icons.js';

const ENHANCED = 'select:not([data-native], [multiple], [size])';
const EDGE = 8; // What is kept free between the menu and the edge of the window, in pixels.
const GAP = 4; // What is left between the menu and what opened it, in pixels.
const TYPING_MS = 700;

// The menu that is open, if one is.
let current = null;
let counter = 0;

const enhanced = (select) => select instanceof HTMLSelectElement && select.matches(ENHANCED);
const textOf = (option) => (option.label || option.textContent).trim();

// What names the <select>, so that its menu has the same name.
function nameOf(select) {
  const own = select.getAttribute('aria-label');
  if (own) return own;
  const named = (select.getAttribute('aria-labelledby') || '')
    .split(/\s+/)
    .map((id) => document.getElementById(id)?.textContent.trim())
    .filter(Boolean);
  if (named.length) return named.join(' ');
  const label = select.labels?.[0];
  return label ? label.textContent.trim() : select.title || '';
}

// -- drawing the menu ------------------------------------------------------------------------------------------

// The rows of the menu, read from the options as they are now.
function draw(state) {
  const { select, list } = state;
  const rows = [];
  const row = (option) => {
    const chosen = option.selected;
    const element = h(
      'div',
      { id: `${list.id}-${rows.length}`, role: 'option', class: 'vselect-option', 'aria-selected': String(chosen), 'aria-disabled': option.matches(':disabled') ? 'true' : null },
      h('span', { class: 'vselect-text' }, textOf(option)),
      option.dataset.hint ? h('span', { class: 'vselect-hint' }, option.dataset.hint) : null,
      chosen ? icon('check', { size: 16 }) : null,
    );
    rows.push({ option, element, disabled: option.matches(':disabled') });
    return element;
  };
  const parts = [];
  for (const child of select.children) {
    if (child instanceof HTMLOptionElement && !child.hidden) parts.push(row(child));
    else if (child instanceof HTMLOptGroupElement) {
      const heading = h('div', { class: 'vselect-heading', 'aria-hidden': 'true', id: `${list.id}-group-${parts.length}` }, child.label);
      const inside = [...child.children].filter((one) => one instanceof HTMLOptionElement && !one.hidden).map(row);
      parts.push(h('div', { role: 'group', class: 'vselect-group', 'aria-labelledby': heading.id }, heading, inside));
    }
  }
  list.replaceChildren(...parts);
  state.rows = rows;
}

function setActive(state, index, { scroll = true } = {}) {
  const { rows, list, select } = state;
  rows[state.active]?.element.removeAttribute('data-active');
  state.active = index;
  const row = rows[index];
  if (!row) {
    select.removeAttribute('aria-activedescendant');
    return;
  }
  row.element.setAttribute('data-active', 'true');
  select.setAttribute('aria-activedescendant', row.element.id);
  if (!scroll) return;
  const top = row.element.offsetTop;
  const bottom = top + row.element.offsetHeight;
  if (index === 0) list.scrollTop = 0;
  else if (top < list.scrollTop) list.scrollTop = top;
  else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight + EDGE / 2;
}

// Where the menu goes: under what opened it, as wide as that at least, and above it when there is
// not enough room below. It never leaves the window; a long list scrolls inside itself.
function place(state) {
  const { anchor, list } = state;
  const box = anchor.getBoundingClientRect();
  const width = document.documentElement.clientWidth;
  const height = window.innerHeight;
  list.style.minWidth = `${Math.ceil(box.width)}px`;
  list.style.maxHeight = '';
  // Measured from the corner of the window, so the room it has to spread in does not depend on where it was last.
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

// -- opening and closing ------------------------------------------------------------------------------------------

function show(select, { touch = false } = {}) {
  close();
  counter += 1;
  const list = h('div', {
    id: `vselect-${counter}`,
    class: 'vselect-list',
    role: 'listbox',
    'aria-label': nameOf(select) || null,
    dataset: { for: select.id, size: parseFloat(getComputedStyle(select).fontSize) < 14 ? 'compact' : 'normal' },
  });
  const state = { select, touch, anchor: select.closest('[data-select-anchor]') || select, list, rows: [], active: -1, typed: '', typedAt: 0, box: '', pointer: null };
  listen(state);
  draw(state);
  // While a dialog is open the rest of the page cannot be reached, so the menu goes inside it.
  (select.closest('dialog[open]') || document.body).append(list);
  current = state;
  select.setAttribute('aria-expanded', 'true');
  select.setAttribute('aria-controls', list.id);
  place(state);
  const chosen = state.rows.findIndex((row) => row.option.selected);
  setActive(state, chosen >= 0 ? chosen : usable(state, 0, 1));
  // Options built or changed by code while the menu is open are drawn again.
  state.watch = new MutationObserver(() => {
    const kept = state.rows[state.active]?.option;
    draw(state);
    place(state);
    const at = state.rows.findIndex((row) => row.option === kept);
    state.active = -1;
    setActive(state, at >= 0 ? at : state.rows.findIndex((row) => row.option.selected));
  });
  state.watch.observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'hidden', 'label', 'selected'], characterData: true });
  // The menu goes with its <select>: it follows it when the page moves it, and closes when it is gone.
  const follow = () => {
    if (current !== state) return;
    if (!select.isConnected || select.disabled || !select.getClientRects().length) {
      close();
      return;
    }
    const box = state.anchor.getBoundingClientRect();
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
  state.watch.disconnect();
  state.list.remove();
  const { select } = state;
  select.removeAttribute('aria-expanded');
  select.removeAttribute('aria-controls');
  select.removeAttribute('aria-activedescendant');
  if (refocus && select.isConnected) select.focus();
}

// Picks an option the way a person does: the value changes and the page hears `input` and `change`,
// but only when it is another option than the one already chosen.
function choose(index) {
  const state = current;
  const row = state?.rows[index];
  if (!row || row.disabled) return;
  const { select, touch } = state;
  // A finger never gave the <select> the focus, so it is not given back: that would raise the system's own picker.
  close({ refocus: !touch });
  if (select.selectedIndex === row.option.index) return;
  select.selectedIndex = row.option.index;
  select.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
  select.dispatchEvent(new Event('change', { bubbles: true }));
}

// -- moving through it ---------------------------------------------------------------------------------------

// The first option that can be chosen from `from` on, going `step` ways, or -1.
function usable(state, from, step) {
  for (let at = from; at >= 0 && at < state.rows.length; at += step) if (!state.rows[at].disabled) return at;
  return -1;
}

function moveTo(state, index) {
  if (index >= 0) setActive(state, index);
}

// Letters typed light the next option that starts with them; the same letter again goes on to the
// next option that starts with it.
function typeAhead(state, key) {
  const now = Date.now();
  state.typed = now - state.typedAt > TYPING_MS ? key : state.typed + key;
  state.typedAt = now;
  const typed = state.typed.toLowerCase();
  const again = [...typed].every((letter) => letter === typed[0]);
  const needle = again ? typed[0] : typed;
  const from = again ? state.active + 1 : state.active;
  const count = state.rows.length;
  for (let step = 0; step < count; step += 1) {
    const at = (from + step + count) % count;
    if (!state.rows[at].disabled && textOf(state.rows[at].option).toLowerCase().startsWith(needle)) {
      setActive(state, at);
      return;
    }
  }
}

// -- what the person does ----------------------------------------------------------------------------------------

// A press on a <select> opens the board's menu in place of the browser's. The press is not passed
// on, so the browser's own menu stays shut, and the <select> is given the focus by hand.
function onMouseDown(event) {
  const select = event.target instanceof Element ? event.target.closest('select') : null;
  if (!enhanced(select) || select.disabled || event.button !== 0 || event.ctrlKey) return;
  event.preventDefault();
  select.focus();
  if (current?.select === select) close();
  else show(select);
}

// A tap is told from a scroll by how far the finger moved. It is opened here, and not from the mouse
// events that follow a tap, because stopping those is what keeps the system's own picker shut on a phone.
let tapped = null;

function onTouchStart(event) {
  const select = event.target instanceof Element ? event.target.closest('select') : null;
  const touch = event.touches[0];
  tapped = enhanced(select) && !select.disabled && event.touches.length === 1 ? { select, x: touch.clientX, y: touch.clientY } : null;
}

function onTouchEnd(event) {
  const start = tapped;
  tapped = null;
  const touch = event.changedTouches[0];
  if (!start || !event.cancelable || Math.hypot(touch.clientX - start.x, touch.clientY - start.y) > 10) return;
  event.preventDefault();
  if (current?.select === start.select) close();
  else show(start.select, { touch: true });
}

function onKeyDown(event) {
  const select = event.target instanceof HTMLSelectElement ? event.target : null;
  if (!current) {
    if (!enhanced(select) || select.disabled || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      show(select);
    }
    return;
  }
  if (select !== current.select) return;
  const state = current;
  const { key } = event;
  const typing = state.typed && Date.now() - state.typedAt <= TYPING_MS;
  const take = () => {
    event.preventDefault();
    event.stopImmediatePropagation();
  };
  if (key === 'Tab') close();
  else if (key === 'Escape') {
    take();
    close({ refocus: true });
  } else if (key === 'ArrowUp' && event.altKey) {
    take();
    choose(state.active);
  } else if (key === 'ArrowDown') {
    take();
    moveTo(state, usable(state, state.active + 1, 1));
  } else if (key === 'ArrowUp') {
    take();
    moveTo(state, usable(state, state.active - 1, -1));
  } else if (key === 'Home' || key === 'PageUp') {
    take();
    moveTo(state, usable(state, 0, 1));
  } else if (key === 'End' || key === 'PageDown') {
    take();
    moveTo(state, usable(state, state.rows.length - 1, -1));
  } else if (key === 'Enter' || (key === ' ' && !typing)) {
    take();
    choose(state.active);
  } else if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    take();
    typeAhead(state, key);
  }
}

// A press anywhere else closes the menu, and so does the page moving under it.
function onPointerDown(event) {
  if (current && event.target !== current.select && !current.list.contains(event.target)) close();
}

function onScroll(event) {
  if (current && !current.list.contains(event.target)) close();
}

// Inside the menu the pointer lights an option and a click picks it. A press is kept from the page,
// so the <select> keeps the focus. A pointer that has not moved lights nothing: the menu scrolling
// under it, or the keys moving the light, must not hand the light back to the row beneath it.
function listen(state) {
  const { list } = state;
  const rowAt = (event) => state.rows.findIndex((row) => row.element === event.target.closest('.vselect-option'));
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
    if (at >= 0) choose(at);
  });
}

window.addEventListener('mousedown', onMouseDown, true);
window.addEventListener('touchstart', onTouchStart, { capture: true, passive: true });
window.addEventListener('touchend', onTouchEnd, { capture: true, passive: false });
window.addEventListener('keydown', onKeyDown, true);
window.addEventListener('pointerdown', onPointerDown, true);
window.addEventListener('scroll', onScroll, true);
window.addEventListener('resize', () => close());
window.addEventListener('blur', () => close());
document.addEventListener('focusout', (event) => {
  if (current && event.target === current.select) close();
});
