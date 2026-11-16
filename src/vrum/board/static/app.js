// The board's shell: signing in, the person's projects, the top bar, the address, the drawer and
// the clock that asks the server what changed.
//
// A project's board lives at /board/p/<number>, with the page and its choices after the #. The
// server decides whether the person owns the project, on every request. Moving to another project
// is a whole new page load, so nothing of the project left can be drawn in the one entered.
//
// Three views are the work itself (Board, Graph, Roadmap). Search and Activity are a drawer to the
// left of the page that is open on every view, and the selected record is the detail panel to its
// right; the settings pages (agent keys, the tools, the status and the export) are reached from
// the Settings menu. The address holds all of it: #/board?drawer=search&search=login&sel=node:12.

import {
  NotSignedIn,
  Refusal,
  Stale,
  loadSession,
  projectInfo,
  projects,
  setProject,
  signOut,
} from './api.js';
import { SETTINGS, VIEWS, actor, addressOf, notify, setActor, ui, viewLink } from './ctx.js';
import { drawerIndex, drawerName, hideDrawer, refreshDrawer, searchAgain, showDrawer } from './drawer.js';
import { clear, h } from './dom.js';
import { showNoProject, showProjects, showSignIn } from './entry.js';
import * as access from './access.js';
import * as graph from './graph.js';
import * as kanban from './kanban.js';
import * as panel from './panel.js';
import { refreshReferences } from './references.js';
import * as roadmap from './roadmap.js';
import * as status from './status.js';
import { keepSynced, onChange, start, stopSyncing, store } from './store.js';
import { keepSwitcher } from './switcher.js';
import * as tools from './tools.js';

// Every page there is. Anything else in the address is the Board.
const PAGES = {
  board: { title: 'Board', view: kanban },
  graph: { title: 'Graph', view: graph },
  roadmap: { title: 'Roadmap', view: roadmap },
  access: { title: 'Agent access', view: access },
  tools: { title: 'Tools', view: tools },
  status: { title: 'Status and export', view: status },
};
const top = document.getElementById('top');
// The bar wraps onto more lines on a narrow window or with long names, and what sticks under it
// has to start below where it really ends, not below where it would end on one line.
new ResizeObserver(() => {
  document.documentElement.style.setProperty('--top-now', `${top.getBoundingClientRect().height}px`);
}).observe(top);
const main = document.getElementById('page');
const dialogs = document.getElementById('dialogs');
let mounted = null; // the page on screen
let running = false; // a project is open and loaded
let session = null; // who is signed in
let project = null; // the project that is open
let projectList = []; // the person's projects, for the switcher
let bar = null; // the parts of the top bar of an open project

// The project in the address, if any: /board/p/12.
const inAddress = (() => {
  const found = location.pathname.match(/^\/board\/p\/(\d+)\/?$/);
  return found ? Number(found[1]) : null;
})();

// -- the address ----------------------------------------------------------------------------------

function readAddress() {
  const [name, query = ''] = location.hash.replace(/^#\/?/, '').split('?');
  const known = Object.hasOwn(PAGES, name);
  return { page: known ? name : 'board', known, params: new URLSearchParams(known ? query : '') };
}

ui.navigate = (changes, { replace = false, page = ui.page } = {}) => {
  const hash = addressOf(page, changes);
  if (hash === location.hash) return;
  // The address is handled a moment after it is set. Remember what it holds at once, so that a
  // second change made before then builds on this one instead of dropping it.
  if (page === ui.page) ui.params = new URLSearchParams(hash.split('?')[1] ?? '');
  if (replace) location.replace(hash);
  else location.hash = hash;
};

function route() {
  const { page, known, params } = readAddress();
  // A page that is not there (an old address, a mistake) is the Board, and the address says so.
  if (running && !known) history.replaceState(null, '', `${location.pathname}${location.search}#/board`);
  const changedPage = page !== ui.page || mounted === null;
  ui.page = page;
  ui.params = params;
  if (!running) return;
  updateTop();
  if (changedPage) {
    if (mounted) mounted.unmount();
    clear(main);
    mounted = PAGES[page].view;
    mounted.mount(main, params);
  } else {
    mounted.update(params);
  }
  // The drawer and the detail panel are not remounted by anything but their own address: a
  // draft typed in either stays while the page, the presentation or the filter changes.
  showDrawer(params);
  panel.show(params);
}

// -- the top bar ----------------------------------------------------------------------------------

function indexText() {
  const index = store.index;
  if (!index) return 'Index: checking';
  if (index.meaning === 'not_configured') return 'Search by meaning is not set up';
  if (index.meaning === 'index_not_ready') {
    return `Index not built yet: ${index.waiting_to_index} waiting`;
  }
  return `Index: ${index.waiting_to_index} waiting`;
}

const brand = (href) => h('a', { href, class: 'brand' }, h('img', { src: '/board/brand/vrum-logo.svg', alt: 'vrum', height: 28 }));

async function leaveBoard() {
  try {
    await signOut();
  } finally {
    location.assign('/board');
  }
}

const accountBar = () => [
  h('span', { id: 'account', class: 'chip', title: session.account.email }, session.account.name),
  h('button', { type: 'button', id: 'sign-out', class: 'link', onclick: leaveBoard }, 'Sign out'),
];

// The project switcher is a real select: choosing another project loads that project's board.
function switcher() {
  const choose = h(
    'select',
    {
      id: 'project-switcher',
      'aria-label': 'Project',
      onchange: () => location.assign(choose.value ? `/board/p/${choose.value}#/board` : '/board'),
    },
    projectList.map((one) => h('option', { value: String(one.id) }, one.name)),
    h('option', { value: '' }, 'All projects…'),
  );
  choose.value = String(project.id);
  return choose;
}

// Enter in the box of the top bar opens the search drawer with those words.
function openSearch(text) {
  const before = location.hash;
  ui.navigate({ drawer: 'search', search: text || null, search_type: null, search_status: null, search_epic: null, dups: null });
  // The same words again ask again.
  if (location.hash === before) searchAgain();
}

const toggleActivity = () => ui.navigate({ drawer: drawerName(ui.params) === 'activity' ? null : 'activity' });

// The Settings menu: a button that opens a short list of links, with the keyboard behaviour of a
// menu (Enter, Space or an arrow opens it, the arrows, Home and End move, Escape closes it).
function settingsMenu() {
  const button = h('button', { type: 'button', id: 'settings-menu', 'aria-haspopup': 'menu', 'aria-expanded': 'false', 'aria-controls': 'settings-list' }, 'Settings ▾');
  const items = SETTINGS.map((name) => viewLink(name, {}, { id: `settings-${name}`, role: 'menuitem', tabindex: '-1', dataset: { page: name } }, PAGES[name].title));
  const list = h('div', { id: 'settings-list', class: 'menu-list', role: 'menu', 'aria-label': 'Settings', hidden: true }, items);
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
      close(true);
    } else if (event.key === 'Tab') close();
  });
  list.addEventListener('click', () => close());
  wrap.addEventListener('focusout', (event) => {
    if (!list.hidden && !wrap.contains(event.relatedTarget)) close();
  });
  return { wrap, button };
}

// The top bar of an open project, built once: what is typed in the search box stays while the
// address changes. updateTop sets what the address decides (the current view, the open drawer).
function buildTop() {
  const search = h('input', { id: 'global-search', type: 'search', name: 'search', autocomplete: 'off', 'aria-label': 'Search work, requirements, decisions and facts', placeholder: 'Search…' });
  const menu = settingsMenu();
  bar = {
    nav: h('nav', { 'aria-label': 'Views' }, VIEWS.map((name) => viewLink(name, {}, { dataset: { page: name } }, PAGES[name].title))),
    search,
    typed: '', // the words of the address that the box was last set from
    activity: h('button', { type: 'button', id: 'activity-open', 'aria-controls': 'drawer', 'aria-expanded': 'false', title: 'Who is working on what, and what changed', onclick: toggleActivity }, 'Activity'),
    settings: menu.button,
    who: h('button', { type: 'button', id: 'who', class: 'link', onclick: () => askName() }),
  };
  showWho();
  clear(top).append(
    brand('#/board'),
    switcher(),
    bar.nav,
    h(
      'form',
      {
        id: 'global-search-form',
        class: 'global-search',
        role: 'search',
        'aria-label': 'Search the project',
        onsubmit: (event) => {
          event.preventDefault();
          openSearch(search.value.trim());
        },
      },
      search,
      h('button', { type: 'submit', id: 'global-search-go' }, 'Search'),
    ),
    h('span', { class: 'spacer' }),
    bar.activity,
    menu.wrap,
    h('button', { type: 'button', id: 'switcher-open', class: 'link', title: 'Ctrl+K, or ⌘K', onclick: () => openJump() }, 'Jump to…'),
    h('span', { id: 'connection', class: 'chip', hidden: true }, 'Cannot reach the server: trying again'),
    h('span', { id: 'index-state', class: 'chip', title: 'Items that are new or changed and not yet searchable by meaning' }, indexText()),
    bar.who,
    ...accountBar(),
  );
}

const showWho = () => {
  if (bar) bar.who.textContent = `You are ${actor() || '(no name)'}: change`;
};

function updateTop() {
  if (!bar) return;
  const mark = (element, on) => {
    element.classList.toggle('current', on);
    if (on) element.setAttribute('aria-current', 'page');
    else element.removeAttribute('aria-current');
  };
  for (const link of bar.nav.querySelectorAll('a')) mark(link, link.dataset.page === ui.page);
  mark(bar.settings, SETTINGS.includes(ui.page));
  const drawer = drawerName(ui.params);
  bar.activity.setAttribute('aria-expanded', String(drawer === 'activity'));
  bar.activity.classList.toggle('current', drawer === 'activity');
  // The box follows the words of the address, unless it is being typed in.
  const words = ui.params.get('search') || '';
  if (words !== bar.typed) {
    bar.typed = words;
    if (document.activeElement !== bar.search) bar.search.value = words;
  }
}

// What is at the top before a project is open.
function drawEntryTop() {
  bar = null;
  clear(top).append(brand('/board'), h('span', { class: 'spacer' }), ...(session ? accountBar() : []));
}

onChange((change) => {
  if (change.index) {
    const chip = document.getElementById('index-state');
    if (chip) chip.textContent = indexText();
    if (mounted && mounted.index) mounted.index();
    drawerIndex();
  }
  if (change.events && mounted) {
    mounted.refresh(change.events);
    panel.refresh(change.events);
    refreshDrawer(change.events);
    // A title changed, or an item came or went: every reference on the page says so at once.
    refreshReferences(document);
  }
});

// Not being able to reach the server is said in the top bar and above the page, whichever view
// is open, and goes away again when the server answers.
function showConnection(trouble) {
  const chip = document.getElementById('connection');
  if (chip) chip.hidden = !trouble;
  const note = document.getElementById('connection-note');
  note.hidden = !trouble;
  note.textContent = trouble ? 'The board cannot reach the server, so what it shows may be out of date. It is trying again.' : '';
}

function connectionTrouble(error) {
  if (error instanceof Stale) return;
  if (error instanceof NotSignedIn) {
    leave('Your session has ended. Sign in again.');
    return;
  }
  showConnection(true);
}

// -- the person's name -----------------------------------------------------------------------------

function dialog(title, ...body) {
  const box = h('dialog', { class: 'modal', 'aria-labelledby': 'dialog-title' }, h('h2', { id: 'dialog-title' }, title), ...body);
  dialogs.append(box);
  box.addEventListener('close', () => box.remove());
  return box;
}

function askName() {
  const field = h('input', { type: 'text', name: 'name', id: 'name', value: actor(), autocomplete: 'nickname', required: true });
  const box = dialog(
    'Who are you?',
    h('p', {}, 'Every change you make is recorded with this name, next to the agents’ names. It is kept in this browser.'),
    h(
      'form',
      {
        method: 'dialog',
        onsubmit: (event) => {
          event.preventDefault();
          setActor(field.value);
          box.close();
          showWho();
        },
      },
      h('label', { for: 'name' }, 'Your name'),
      field,
      h('button', { type: 'submit', class: 'primary' }, 'Save'),
      h('button', { type: 'button', onclick: () => box.close() }, 'Cancel'),
    ),
  );
  box.showModal();
  field.select();
}

// -- getting in and out ------------------------------------------------------------------------------

// Show the sign-in page, with a word about why when there is one.
function leave(message = '') {
  stopSyncing();
  running = false;
  session = null;
  if (mounted) mounted.unmount();
  mounted = null;
  clear(main);
  hideDrawer();
  panel.hide();
  setProject(null);
  drawEntryTop();
  showConnection(false);
  showSignIn(main, { onSignedIn: () => location.reload(), message });
}

// Load everything the open project holds and start the clock.
async function begin() {
  await start();
  running = true;
  buildTop();
  keepSynced(connectionTrouble, () => showConnection(false));
  route();
}

async function openProject(number) {
  setProject(number);
  try {
    project = await projectInfo(number);
    projectList = await projects();
  } catch (error) {
    setProject(null);
    if (!(error instanceof Refusal) || error instanceof NotSignedIn) throw error;
    drawEntryTop();
    showNoProject(main, error.message);
    return;
  }
  document.title = `${project.name} · Vrum board`;
  if (!actor()) setActor(session.account.name);
  await begin();
}

addEventListener('hashchange', route);

// Ctrl+K opens the quick switcher on every page.
const jumpTo = (page) => ({ name: page, title: PAGES[page].title, go: () => ui.navigate({}, { page }) });
const openJump = keepSwitcher(
  [
    ...VIEWS.map(jumpTo),
    { name: 'search', title: 'Search', go: () => ui.navigate({ drawer: 'search' }) },
    { name: 'activity', title: 'Activity', go: () => ui.navigate({ drawer: 'activity' }) },
    ...SETTINGS.map(jumpTo),
  ],
  () => running,
);

(async () => {
  readAddress();
  route();
  try {
    session = await loadSession();
    if (session === null) {
      leave('');
      return;
    }
    if (inAddress === null) {
      drawEntryTop();
      await showProjects(main, session);
      return;
    }
    await openProject(inAddress);
  } catch (error) {
    if (error instanceof NotSignedIn) {
      leave('Your session has ended. Sign in again.');
      return;
    }
    clear(main);
    notify(error instanceof Refusal ? error.message : 'The board could not start. Reload the page to try again.', { refusal: true });
  }
})();
