// The appearance control: Light, Dark or System, as a group of three radio buttons (before a project is open: the top of
// the sign-in and project pages) or as three radio items of the account menu (a project is open). Choosing one is
// remembered in this browser by theme.js (never with any credential) and changes only the colours: nothing is reloaded,
// and a filter, a selection or an unsaved edit stays as it is.

import { h } from './dom.js';
import { icon } from './icons.js';

const CHOICES = [
  { value: 'light', label: 'Light', icon: 'sun' },
  { value: 'dark', label: 'Dark', icon: 'moon' },
  { value: 'system', label: 'System', icon: 'monitor' },
];

let counter = 0;

// A control for the page it is on (the top of the pages before a project is open). Each follows
// the choice made in any other that is on screen.
export function appearanceControl() {
  counter += 1;
  const name = `appearance-${counter}`;
  const group = h('div', { class: 'appearance', role: 'radiogroup', 'aria-label': 'Appearance' });
  const inputs = CHOICES.map((choice) => {
    const input = h('input', {
      type: 'radio',
      name,
      value: choice.value,
      id: `${name}-${choice.value}`,
      'data-appearance': choice.value,
      onchange: () => window.vrumTheme.set(choice.value),
    });
    group.append(
      h('label', { class: 'appearance-option', for: input.id, title: choice.label }, input, icon(choice.icon, { size: 14 }), h('span', { class: 'appearance-label' }, choice.label)),
    );
    return input;
  });
  const show = () => {
    const now = window.vrumTheme.choice();
    for (const input of inputs) input.checked = input.value === now;
  };
  show();
  window.addEventListener('vrum-theme', show);
  return group;
}

// The same three choices as the radio items of the account menu in the sidebar (sidebar.js), where the appearance
// lives once a project is open: Light, Dark and System, each a `menuitemradio` that follows the choice made anywhere
// else. Choosing one is remembered by theme.js exactly as the group above does; the menu closes itself.
export function appearanceMenuItems() {
  const items = CHOICES.map((choice) =>
    h(
      'button',
      {
        type: 'button',
        id: `account-appearance-${choice.value}`,
        class: 'menu-radio',
        role: 'menuitemradio',
        tabindex: '-1',
        'aria-checked': 'false',
        'data-appearance': choice.value,
        onclick: () => window.vrumTheme.set(choice.value),
      },
      icon(choice.icon, { size: 16 }),
      h('span', { class: 'menu-radio-label' }, choice.label),
      icon('check', { size: 14, className: 'menu-radio-check' }),
    ),
  );
  const show = () => {
    const now = window.vrumTheme.choice();
    for (const item of items) item.setAttribute('aria-checked', String(item.dataset.appearance === now));
  };
  show();
  window.addEventListener('vrum-theme', show);
  return items;
}
