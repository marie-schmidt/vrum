// The Forces control of the Structure map, as in Obsidian's graph view: a button that opens a small panel
// with four sliders that scale the simulation's forces, and a Reset. The values are kept while the board
// is open (they are not in the address and not stored), so the same ones are there when the map is
// drawn again; the panel itself is closed whenever the Graph is opened.

import { h } from './dom.js';
import { icon } from './icons.js';

const FIELDS = [
  ['centre', 'Centre'],
  ['repel', 'Repel'],
  ['link', 'Link force'],
  ['distance', 'Link distance'],
];

const values = { centre: 1, repel: 1, link: 1, distance: 1 };
let open = false;

export const forceValues = () => ({ ...values });
export const forcesAreDefault = () => FIELDS.every(([key]) => values[key] === 1);
export const closeForces = () => { open = false; };

const shown = (value) => value.toFixed(1);

// `change(values)` is told every time a slider moves, `reset()` when Reset is pressed.
export function createForces({ change, reset }) {
  const outputs = {};
  const inputs = {};
  const button = h('button', { id: 'graph-st-forces', class: 'small', type: 'button', 'aria-expanded': 'false', 'aria-controls': 'graph-st-forces-panel' }, icon('settings', { size: 14 }), 'Forces');
  const rows = FIELDS.map(([key, label]) => {
    const id = `graph-st-force-${key}`;
    const input = h('input', { id, type: 'range', min: '0.2', max: '3', step: '0.1', value: String(values[key]) });
    const output = h('output', { class: 'graph-forces-value', for: id }, shown(values[key]));
    input.addEventListener('input', () => {
      values[key] = Number(input.value);
      output.textContent = shown(values[key]);
      change(forceValues());
    });
    inputs[key] = input;
    outputs[key] = output;
    return h('div', { class: 'graph-forces-row' }, h('label', { for: id }, label), output, input);
  });
  const again = h('button', { id: 'graph-st-forces-reset', class: 'small', type: 'button' }, icon('reset', { size: 14 }), 'Reset');
  const panel = h('div', { id: 'graph-st-forces-panel', class: 'graph-forces-panel', role: 'group', 'aria-label': 'Forces', hidden: true }, rows, h('div', { class: 'graph-forces-foot' }, again));
  const el = h('span', { class: 'graph-forces' }, button, panel);

  // The panel hangs under its button and keeps inside the window.
  const place = () => {
    panel.style.left = '0px';
    const over = panel.getBoundingClientRect().right - (window.innerWidth - 8);
    if (over > 0) panel.style.left = `${-over}px`;
  };
  const outside = (e) => { if (!el.contains(e.target)) show(false); };
  function show(on) {
    open = on;
    panel.hidden = !on;
    button.setAttribute('aria-expanded', String(on));
    if (on) {
      place();
      document.addEventListener('pointerdown', outside, true);
    } else document.removeEventListener('pointerdown', outside, true);
  }
  button.addEventListener('click', () => show(!open));
  el.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !open) return;
    e.stopPropagation();
    show(false);
    button.focus();
  });
  again.addEventListener('click', () => {
    for (const [key] of FIELDS) {
      values[key] = 1;
      inputs[key].value = '1';
      outputs[key].textContent = shown(1);
    }
    reset();
  });
  const onResize = () => { if (open) place(); };
  window.addEventListener('resize', onResize);
  if (open) show(true);

  return {
    el,
    dispose() {
      document.removeEventListener('pointerdown', outside, true);
      window.removeEventListener('resize', onResize);
    },
  };
}
