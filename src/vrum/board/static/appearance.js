// The appearance control: Light, Dark or System, as a group of three radio buttons. Choosing one
// is remembered in this browser by theme.js (never with any credential) and changes only the
// colours: nothing is reloaded, and a filter, a selection or an unsaved edit stays as it is.

import { h } from './dom.js';
import { icon } from './icons.js';

const CHOICES = [
  { value: 'light', label: 'Light', icon: 'sun' },
  { value: 'dark', label: 'Dark', icon: 'moon' },
  { value: 'system', label: 'System', icon: 'monitor' },
];

let counter = 0;

// A control for the page it is on (the top bar, before and after a project is open). Each follows
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
