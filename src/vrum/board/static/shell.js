// How the pieces of the shell sit together on different windows: the height the top bar takes (for
// what sticks under it), and the drawer and the detail panel when they open over the page as sheets
// instead of beside it. The columns and the sheets themselves are CSS (board.css); this is what CSS
// cannot do: close a sheet on a click on the dimmed page, and on Escape.

import { ui } from './ctx.js';

// Below this the sidebar is off to the side and both the drawer and the panel are sheets.
const NARROW = matchMedia('(max-width: 1099.98px)');
// Between this and a wide window the drawer is a sheet only while the panel is open too.
const MIDDLE = matchMedia('(min-width: 1100px) and (max-width: 1499.98px)');

export function keepLayout() {
  const top = document.getElementById('top');
  const layout = document.getElementById('layout');
  // The bar sticks to the top except on a phone, where it scrolls away. What sticks under it starts
  // below where it really ends.
  const publish = () => {
    const sticks = getComputedStyle(top).position === 'sticky';
    document.documentElement.style.setProperty('--top-now', `${sticks ? Math.round(top.getBoundingClientRect().height) : 0}px`);
  };
  new ResizeObserver(publish).observe(top);
  matchMedia('(min-width: 700px)').addEventListener('change', publish);
  publish();

  // The sheet in front: the panel over the drawer on a narrow window; on a middle-sized one the
  // drawer is the only sheet, and it is over the panel.
  const front = () => {
    if (NARROW.matches) return layout.classList.contains('with-panel') ? 'panel' : layout.classList.contains('with-drawer') ? 'drawer' : null;
    return MIDDLE.matches && layout.classList.contains('with-panel') && layout.classList.contains('with-drawer') ? 'drawer' : null;
  };
  const close = () => {
    const sheet = front();
    if (sheet === 'panel') ui.navigate({ sel: null, panel: null });
    else if (sheet === 'drawer') ui.navigate({ drawer: null });
    return sheet !== null;
  };
  document.getElementById('sheet-scrim').addEventListener('click', close);
  // Escape closes a sheet when nothing else took it (a dialog, a menu, the drawer's own handler).
  // Typing in a field is never cut short by it.
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || event.defaultPrevented || document.querySelector('dialog[open]')) return;
    if (event.target instanceof Element && event.target.closest('input, textarea, select, [contenteditable]')) return;
    if (close()) event.preventDefault();
  });
}
