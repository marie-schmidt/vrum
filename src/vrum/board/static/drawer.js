// The drawer: the column to the left of the page where Search and Activity open, on every view of
// a project. Which one is open is in the address (`drawer=search` or `drawer=activity`), only one
// is open at a time, and it stays mounted while the rest of the address changes, so that opening
// it or moving about the page never discards what was typed in it or in the detail panel on the
// other side of the page.

import { ui } from './ctx.js';
import { clear, h, put } from './dom.js';
import { icon } from './icons.js';
import * as activity from './activity.js';
import * as search from './search.js';

const DRAWERS = {
  search: { title: 'Search', icon: 'search', view: search },
  activity: { title: 'Activity', icon: 'activity', view: activity },
};

const aside = document.getElementById('drawer');
const layout = document.getElementById('layout');
let shown = null; // { name, view }
let opener = null; // what had the focus before the drawer opened, to give it back

// The drawer an address asks for, or null.
export const drawerName = (params) => (DRAWERS[params.get('drawer')] ? params.get('drawer') : null);

aside.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !event.defaultPrevented && shown) {
    event.preventDefault();
    ui.navigate({ drawer: null });
  }
});

export function hideDrawer() {
  if (!shown) return;
  const hadFocus = aside.contains(document.activeElement);
  shown.view.unmount();
  shown = null;
  clear(aside);
  aside.hidden = true;
  delete aside.dataset.drawer;
  delete aside.dataset.mode;
  aside.setAttribute('aria-label', 'Drawer');
  layout.classList.remove('with-drawer');
  if (hadFocus && opener && document.contains(opener)) opener.focus();
  opener = null;
}

// Open the drawer the address names, or update the one that is open, or close it.
export function showDrawer(params) {
  const name = drawerName(params);
  if (name === null) {
    hideDrawer();
    return;
  }
  if (shown && shown.name === name) {
    shown.view.update(params);
    return;
  }
  if (shown) {
    shown.view.unmount();
    shown = null;
  } else {
    opener = document.activeElement;
  }
  const { title, view } = DRAWERS[name];
  const body = h('div', { id: 'drawer-body', class: 'drawer-body' });
  put(
    aside,
    h(
      'header',
      { class: 'drawer-head' },
      h('span', { class: 'drawer-icon' }, icon(DRAWERS[name].icon, { size: 18 })),
      h('h2', { id: 'drawer-title', tabindex: '-1' }, title),
      h('button', { type: 'button', id: 'drawer-close', class: 'ghost icon-only', 'aria-label': `Close ${title.toLowerCase()}`, title: `Close ${title.toLowerCase()}`, onclick: () => ui.navigate({ drawer: null }) }, icon('close', { size: 18 })),
    ),
    body,
  );
  aside.dataset.drawer = name;
  delete aside.dataset.mode;
  aside.setAttribute('aria-label', `${title} drawer`);
  aside.hidden = false;
  layout.classList.add('with-drawer');
  shown = { name, view };
  Promise.resolve(view.mount(body, params)).then(() => {
    // Focus moves in unless the person has already gone somewhere else in the meantime.
    const now = document.activeElement;
    const free = now === opener || now === document.body || aside.contains(now);
    if (shown && shown.name === name && free) (view.focus || (() => document.getElementById('drawer-title')?.focus()))();
  });
}

// News from the server, and the state of the index, for the drawer that is open.
export function refreshDrawer(events) {
  if (shown) shown.view.refresh(events);
}

export function drawerIndex() {
  if (shown && shown.view.index) shown.view.index();
}

// The same words searched again, for a search that the address already holds.
export function searchAgain() {
  if (shown && shown.name === 'search') shown.view.again();
}
