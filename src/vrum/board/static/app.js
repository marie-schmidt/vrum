// The board's shell: signing in, the person's projects, the sidebar and top bar, the address, the
// drawer and the clock that asks the server what changed.
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
import { SETTINGS, VIEWS, actor, addressOf, setActor, ui } from './ctx.js';
import { drawerIndex, drawerName, hideDrawer, refreshDrawer, searchAgain, showDrawer } from './drawer.js';
import { clear, h } from './dom.js';
import { showNoProject, showProjects, showSignIn } from './entry.js';
import { icon } from './icons.js';
import * as access from './access.js';
import * as graph from './graph.js';
import * as kanban from './kanban.js';
import * as panel from './panel.js';
import { refreshReferences } from './references.js';
import * as roadmap from './roadmap.js';
import { keepLayout } from './shell.js';
import { hideSidebar, showSidebar, updateSidebar, updateWho } from './sidebar.js';
import * as status from './status.js';
import { ensureContext, keepSynced, onChange, start, stopSyncing, store } from './store.js';
import { keepSwitcher } from './switcher.js';
import { setConnection, setIndexText, showEntryTop, showTop, updateTop } from './topbar.js';
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
const TITLES = Object.fromEntries(Object.entries(PAGES).map(([name, page]) => [name, page.title]));
const app = document.getElementById('app');
const main = document.getElementById('page');
const dialogs = document.getElementById('dialogs');
// The skip link goes to the page without changing the address (which would make the Board of it).
document.querySelector('.skip').addEventListener('click', (event) => {
  event.preventDefault();
  main.focus();
});
let mounted = null; // the page on screen
let running = false; // a project is open and loaded
let session = null; // who is signed in
let project = null; // the project that is open
let projectList = []; // the person's projects, for the switcher

keepLayout();

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
  updateTop(PAGES[page].title);
  updateSidebar();
  document.title = `${PAGES[page].title} · ${project.name} · Vrum`;
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

// -- the shell --------------------------------------------------------------------------------------

function indexText() {
  const index = store.index;
  if (!index) return 'Index: checking';
  if (index.meaning === 'not_configured') return 'Search by meaning is not set up';
  if (index.meaning === 'index_not_ready') {
    return `Index not built yet: ${index.waiting_to_index} waiting`;
  }
  return `Index: ${index.waiting_to_index} waiting`;
}

async function leaveBoard() {
  try {
    await signOut();
  } finally {
    location.assign('/board');
  }
}

// Enter in the box of the top bar opens the search drawer with those words.
function openSearch(text) {
  const before = location.hash;
  ui.navigate({ drawer: 'search', search: text || null, search_type: null, search_status: null, search_epic: null, dups: null });
  // The same words again ask again.
  if (location.hash === before) searchAgain();
}

const toggleActivity = () => ui.navigate({ drawer: drawerName(ui.params) === 'activity' ? null : 'activity' });

// What is at the top, and no sidebar, before a project is open.
function entryShell() {
  hideSidebar();
  app.dataset.shell = 'entry';
  showEntryTop({ session, leaveBoard });
}

function projectShell() {
  app.dataset.shell = 'project';
  showSidebar({ session, project, projectList, views: VIEWS, settings: SETTINGS, titles: TITLES, leaveBoard, askName, toggleActivity });
  showTop({ project, indexText, openSearch, openJump });
}

onChange((change) => {
  if (change.index) {
    setIndexText(indexText());
    if (mounted && mounted.index) mounted.index();
    drawerIndex();
  }
  if (change.events && mounted) {
    updateSidebar();
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
  setConnection(trouble);
  const note = document.getElementById('connection-note');
  note.hidden = !trouble;
  if (trouble) {
    note.replaceChildren(icon('alert', { size: 16 }), h('span', {}, 'The board cannot reach the server, so what it shows may be out of date. It is trying again.'));
  } else {
    note.replaceChildren();
  }
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
          updateWho();
        },
      },
      h('div', { class: 'field' }, h('label', { for: 'name' }, 'Your name'), field),
      h('div', { class: 'actions' }, h('button', { type: 'button', onclick: () => box.close() }, 'Cancel'), h('button', { type: 'submit', class: 'primary' }, 'Save')),
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
  for (const open of dialogs.querySelectorAll('dialog')) open.close();
  setProject(null);
  entryShell();
  showConnection(false);
  showSignIn(main, { onSignedIn: () => location.reload(), message });
}

// Load everything the open project holds and start the clock.
async function begin() {
  await start();
  running = true;
  projectShell();
  keepSynced(connectionTrouble, () => showConnection(false));
  route();
  // The Graph's number in the sidebar counts the requirements, decisions and facts too, so they
  // are read now (the quick switcher and the Graph want them as well).
  ensureContext().then(updateSidebar).catch(() => {});
}

async function openProject(number) {
  setProject(number);
  try {
    project = await projectInfo(number);
    projectList = await projects();
  } catch (error) {
    setProject(null);
    if (!(error instanceof Refusal) || error instanceof NotSignedIn) throw error;
    entryShell();
    showNoProject(main, error.message);
    return;
  }
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

// The board could not start: say so where the page goes, and offer to try again.
function unavailable(message) {
  stopSyncing();
  clear(main).append(
    h(
      'section',
      { id: 'board-unavailable', class: 'unavailable', role: 'alert' },
      h('h1', {}, 'The board could not start'),
      h('p', {}, message),
      h('button', { type: 'button', id: 'retry', class: 'primary', onclick: () => location.reload() }, 'Try again'),
    ),
  );
}

(async () => {
  readAddress();
  route();
  // While the session and the project load, the page already has its brand and the appearance control.
  showEntryTop({ session: null, leaveBoard });
  try {
    session = await loadSession();
    if (session === null) {
      leave('');
      return;
    }
    if (inAddress === null) {
      entryShell();
      await showProjects(main, session);
      return;
    }
    await openProject(inAddress);
  } catch (error) {
    if (error instanceof NotSignedIn) {
      leave('Your session has ended. Sign in again.');
      return;
    }
    if (app.dataset.shell === 'loading') entryShell();
    unavailable(error instanceof Refusal ? error.message : 'The board could not start. Reload the page to try again.');
  }
})();
