// The sidebar of an open project: the brand, the project switcher, the three views with how much
// each holds, the epics, and, at the foot, Activity, the Settings menu, the appearance control and
// the account. On a narrow window it is off to the left and opens over the page (the button in the
// top bar), closing again on Escape, on a click outside it, or when a place is chosen.
//
// The numbers are real and cheap, read from what the board already holds:
//   Board    stories and tasks (what the Board draws in its lanes; epics are the lanes themselves)
//   Graph    work items, plus the requirements, decisions and facts still in force (the
//            "Everything" view's scope). It waits for the context to be read, and shows nothing
//            until it has been.
//   Roadmap  items with a date of their own or one worked out from their children (what it draws;
//            the rest wait in its tray)

import { appearanceControl } from './appearance.js';
import { wordmark } from './brand.js';
import { actor, ui, viewLink } from './ctx.js';
import { h, plural, put } from './dom.js';
import { icon } from './icons.js';
import { epics, store } from './store.js';

const aside = document.getElementById('sidebar');
const scrim = document.getElementById('sidebar-scrim');
const column = document.getElementById('main-column');
const NARROW = matchMedia('(max-width: 1099.98px)');

const SETTINGS_ICONS = { access: 'key', tools: 'settings', status: 'info' };

let parts = null; // the elements of the open project's sidebar
let opener = null; // what had the focus before it opened, to give it back

// -- opening and closing on a narrow window -------------------------------------------------------------

export const sidebarIsOpen = () => aside.classList.contains('open');

export function openSidebar() {
  if (aside.hidden || sidebarIsOpen()) return;
  opener = document.activeElement;
  aside.classList.add('open');
  scrim.hidden = false;
  column.inert = true;
  document.getElementById('sidebar-toggle')?.setAttribute('aria-expanded', 'true');
  document.getElementById('sidebar-close')?.focus();
}

export function closeSidebar({ restore = true } = {}) {
  if (!sidebarIsOpen()) return;
  aside.classList.remove('open');
  scrim.hidden = true;
  column.inert = false;
  document.getElementById('sidebar-toggle')?.setAttribute('aria-expanded', 'false');
  const back = opener;
  opener = null;
  if (restore && back && document.contains(back)) back.focus();
}

scrim.addEventListener('click', () => closeSidebar());
aside.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && sidebarIsOpen() && !event.defaultPrevented) {
    event.preventDefault();
    closeSidebar();
  }
});
// A window that grows wide again shows the sidebar as a column, so nothing is left half open.
NARROW.addEventListener('change', () => {
  if (!NARROW.matches) closeSidebar({ restore: false });
});

// -- the Settings menu ----------------------------------------------------------------------------------------

// A button that opens a short list of links, with the keyboard behaviour of a menu (Enter, Space or
// an arrow opens it, the arrows, Home and End move, Escape closes it).
function settingsMenu(settings, titles) {
  const button = h(
    'button',
    { type: 'button', id: 'settings-menu', class: 'side-item', 'aria-haspopup': 'menu', 'aria-expanded': 'false', 'aria-controls': 'settings-list' },
    icon('settings', { size: 18 }),
    h('span', { class: 'side-label' }, 'Settings & tools'),
    icon('chevron_down', { size: 14, className: 'side-chevron' }),
  );
  const items = settings.map((name) =>
    viewLink(name, {}, { id: `settings-${name}`, role: 'menuitem', tabindex: '-1', dataset: { page: name } }, icon(SETTINGS_ICONS[name], { size: 16 }), titles[name]),
  );
  const list = h('div', { id: 'settings-list', class: 'menu menu-list', role: 'menu', 'aria-label': 'Settings', hidden: true }, items);
  const wrap = h('div', { class: 'menu-wrap' }, button, list);
  const outside = (event) => {
    if (!wrap.contains(event.target)) close();
  };
  function open(last = false) {
    list.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    document.addEventListener('click', outside, true);
    (last ? items[items.length - 1] : items[0]).focus();
  }
  function close(restore = false) {
    list.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    document.removeEventListener('click', outside, true);
    if (restore) button.focus();
  }
  button.addEventListener('click', () => (list.hidden ? open() : close()));
  button.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      open(event.key === 'ArrowUp');
    } else if (event.key === 'Escape' && !list.hidden) {
      event.preventDefault();
      close(true);
    }
  });
  list.addEventListener('keydown', (event) => {
    const at = items.indexOf(document.activeElement);
    const go = (to) => {
      event.preventDefault();
      items[(to + items.length) % items.length].focus();
    };
    if (event.key === 'ArrowDown') go(at + 1);
    else if (event.key === 'ArrowUp') go(at - 1);
    else if (event.key === 'Home') go(0);
    else if (event.key === 'End') go(items.length - 1);
    else if (event.key === ' ' && at >= 0) {
      event.preventDefault();
      items[at].click();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close(true);
    } else if (event.key === 'Tab') close();
  });
  list.addEventListener('click', () => close());
  wrap.addEventListener('focusout', (event) => {
    if (!list.hidden && !wrap.contains(event.relatedTarget)) close();
  });
  return { wrap, button };
}

// -- building it ---------------------------------------------------------------------------------------------

const initial = (name) => ([...String(name).trim()][0] || '?').toUpperCase();

// The project switcher is a real select: choosing another project loads that project's board.
function projectSwitch(project, projectList) {
  const choose = h(
    'select',
    {
      id: 'project-switcher',
      title: project.name,
      onchange: () => location.assign(choose.value ? `/board/p/${choose.value}#/board` : '/board'),
    },
    projectList.map((one) => h('option', { value: String(one.id) }, one.name)),
    h('option', { value: '' }, 'All projects…'),
  );
  choose.value = String(project.id);
  return h(
    'div',
    { class: 'project-switch' },
    h('span', { class: 'avatar', 'aria-hidden': 'true' }, initial(project.name)),
    h('div', { class: 'project-text' }, h('label', { for: 'project-switcher' }, 'Project'), choose),
    icon('chevron_down', { size: 14, className: 'side-chevron' }),
  );
}

function navLink(page, iconName, title) {
  const count = h('span', { class: 'nav-count', hidden: true });
  const link = viewLink(page, {}, { class: 'side-item', dataset: { page } }, icon(iconName, { size: 18 }), h('span', { class: 'side-label' }, title), count);
  return { link, count };
}

// What is built once for an open project: the pieces that change are filled in by updateSidebar.
export function showSidebar({ session, project, projectList, views, settings, titles, leaveBoard, askName, toggleActivity }) {
  const nav = views.map((page) => navLink(page, page, titles[page]));
  const epicList = h('ul', { id: 'epic-list', class: 'plain side-epic-list' });
  const none = h('p', { class: 'quiet side-note', hidden: true }, 'No epics yet. They are listed here as they are made.');
  const activity = h(
    'button',
    { type: 'button', id: 'activity-open', class: 'side-item', 'aria-controls': 'drawer', 'aria-expanded': 'false', title: 'Who is working on what, and what changed', onclick: toggleActivity },
    icon('activity', { size: 18 }),
    h('span', { class: 'side-label' }, 'Activity'),
  );
  const menu = settingsMenu(settings, titles);
  const who = h('button', { type: 'button', id: 'who', class: 'link', onclick: askName });
  parts = { nav, epicList, epicItems: new Map(), none, activity, settings: menu.button, who };
  put(
    aside,
    h(
      'div',
      { class: 'side-head' },
      h('a', { href: '#/board', class: 'brand', 'aria-label': 'vrum, the Board', onclick: () => closeSidebar({ restore: false }) }, wordmark(30)),
      h('button', { type: 'button', id: 'sidebar-close', class: 'ghost icon-only', 'aria-label': 'Close the menu', onclick: () => closeSidebar() }, icon('close', { size: 18 })),
    ),
    projectSwitch(project, projectList),
    h('nav', { id: 'main-nav', 'aria-label': 'Views' }, nav.map((item) => item.link)),
    h('section', { class: 'side-epics', 'aria-labelledby': 'epics-heading' }, h('h2', { id: 'epics-heading', class: 'side-heading' }, 'Epics'), none, epicList),
    h(
      'div',
      { class: 'side-foot' },
      activity,
      menu.wrap,
      appearanceControl(),
      h(
        'div',
        { class: 'account-block' },
        h('span', { class: 'avatar small', 'aria-hidden': 'true' }, initial(session.account.name)),
        h('span', { id: 'account', class: 'account-name', title: session.account.email }, session.account.name),
        h('button', { type: 'button', id: 'sign-out', class: 'small', onclick: leaveBoard }, 'Sign out'),
        who,
      ),
    ),
  );
  aside.hidden = false;
  updateWho();
  updateSidebar();
}

export function hideSidebar() {
  closeSidebar({ restore: false });
  aside.hidden = true;
  aside.replaceChildren();
  parts = null;
}

export function updateWho() {
  if (parts) parts.who.textContent = `Recorded as ${actor() || '(no name)'}: change`;
}

// Closing it after a place is chosen, on a narrow window. A link to a page puts the focus on the
// page; the Activity button leaves it to the drawer, which takes it as it opens.
aside.addEventListener('click', (event) => {
  if (!NARROW.matches || !sidebarIsOpen()) return;
  const link = event.target.closest('a[href]');
  const button = event.target.closest('#activity-open');
  if (!link && !button) return;
  closeSidebar({ restore: false });
  if (link) document.getElementById('page').focus({ preventScroll: true });
});

// -- what changes ----------------------------------------------------------------------------------------------

const inForce = (list) => (list || []).filter((record) => record.status === 'active').length;

// How much each view holds, or null where it is not known yet.
export function counts() {
  const nodes = [...store.nodes.values()];
  const loaded = store.rules !== null && store.decisions !== null && store.facts !== null;
  return {
    board: nodes.filter((node) => node.kind !== 'epic').length,
    graph: loaded ? nodes.length + (store.rules || []).filter((rule) => rule.status !== 'dropped').length + inForce(store.decisions) + inForce(store.facts) : null,
    roadmap: nodes.filter((node) => node.span_start || node.span_end).length,
  };
}

const MEANING = {
  board: (n) => `${plural(n, 'story', 'stories')} and tasks on the Board`,
  graph: (n) => `${plural(n, 'work item')} and records in force in the Graph`,
  roadmap: (n) => `${plural(n, 'item')} with dates on the Roadmap`,
};

// The epics are kept as they are and only changed where they differ, so that a link that has the
// focus (a keyboard user who just chose it) is not replaced under them.
function drawEpics() {
  const found = epics();
  const chosen = ui.page === 'board' ? ui.params.get('epic') : null;
  const list = parts.epicList;
  const made = parts.epicItems;
  for (const [id, item] of made) {
    if (!found.some((epic) => epic.id === id)) {
      item.li.remove();
      made.delete(id);
    }
  }
  found.forEach((epic, at) => {
    let item = made.get(epic.id);
    if (!item) {
      const label = h('span', { class: 'side-label' });
      const link = viewLink('board', { epic: epic.id }, { class: 'side-item epic-link', dataset: { epic: epic.id } }, icon('epic', { size: 16 }), label);
      item = { li: h('li', {}, link), link, label };
      made.set(epic.id, item);
    }
    item.label.textContent = epic.title;
    item.link.title = `#${epic.id} ${epic.title}`;
    if (String(epic.id) === chosen) item.link.setAttribute('aria-current', 'true');
    else item.link.removeAttribute('aria-current');
    if (list.children[at] !== item.li) list.insertBefore(item.li, list.children[at] || null);
  });
  parts.none.hidden = found.length > 0;
}

// Marks where the person is and refreshes the numbers and the epics.
export function updateSidebar() {
  if (!parts) return;
  const now = counts();
  for (const { link, count } of parts.nav) {
    const page = link.dataset.page;
    const on = page === ui.page;
    link.classList.toggle('current', on);
    if (on) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
    const number = now[page];
    count.hidden = number === null;
    if (number !== null) {
      count.textContent = String(number);
      count.title = MEANING[page](number);
    }
  }
  const settingsOn = ['access', 'tools', 'status'].includes(ui.page);
  parts.settings.classList.toggle('current', settingsOn);
  if (settingsOn) parts.settings.setAttribute('aria-current', 'page');
  else parts.settings.removeAttribute('aria-current');
  const drawer = ui.params.get('drawer');
  parts.activity.setAttribute('aria-expanded', String(drawer === 'activity'));
  parts.activity.classList.toggle('current', drawer === 'activity');
  drawEpics();
}
