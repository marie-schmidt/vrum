// The board's shell: signing in, the person's projects, the top bar, the address, and the clock
// that asks the server what changed.
//
// A project's board lives at /board/p/<number>, with the page and its choices after the #. The
// server decides whether the person owns the project, on every request. Moving to another project
// is a whole new page load, so nothing of the project left can be drawn in the one entered.

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
import { actor, notify, setActor, ui } from './ctx.js';
import { clear, h } from './dom.js';
import { showNoProject, showProjects, showSignIn } from './entry.js';
import * as access from './access.js';
import * as activity from './activity.js';
import * as graph from './graph.js';
import * as home from './home.js';
import * as kanban from './kanban.js';
import * as memory from './memory.js';
import * as outline from './outline.js';
import * as overlaps from './overlaps.js';
import * as panel from './panel.js';
import { refreshReferences } from './references.js';
import * as roadmap from './roadmap.js';
import * as rules from './rules.js';
import * as search from './search.js';
import * as status from './status.js';
import { keepSynced, onChange, start, stopSyncing, store } from './store.js';
import { keepSwitcher } from './switcher.js';
import * as table from './table.js';
import * as tools from './tools.js';

const PAGES = {
  home: { title: 'Home', view: home },
  board: { title: 'Board', view: kanban },
  graph: { title: 'Graph', view: graph },
  roadmap: { title: 'Roadmap', view: roadmap },
  table: { title: 'Table', view: table },
  outline: { title: 'Outline', view: outline },
  rules: { title: 'Rules', view: rules },
  memory: { title: 'Memory', view: memory },
  search: { title: 'Search', view: search },
  overlaps: { title: 'Overlaps', view: overlaps },
  activity: { title: 'Activity', view: activity },
  tools: { title: 'Tools', view: tools },
  access: { title: 'Agent access', view: access },
  status: { title: 'Status', view: status },
};
const top = document.getElementById('top');
const main = document.getElementById('page');
const dialogs = document.getElementById('dialogs');
let mounted = null; // the page on screen
let running = false; // a project is open and loaded
let session = null; // who is signed in
let project = null; // the project that is open
let projectList = []; // the person's projects, for the switcher

// The project in the address, if any: /board/p/12.
const inAddress = (() => {
  const found = location.pathname.match(/^\/board\/p\/(\d+)\/?$/);
  return found ? Number(found[1]) : null;
})();

// -- the address ----------------------------------------------------------------------------------

function readAddress() {
  const [name, query = ''] = location.hash.replace(/^#\/?/, '').split('?');
  return { page: PAGES[name] ? name : 'home', params: new URLSearchParams(query) };
}

ui.navigate = (changes, { replace = false, page = ui.page } = {}) => {
  const params = new URLSearchParams(page === ui.page ? ui.params : '');
  for (const [key, value] of Object.entries(changes)) {
    if (value === null || value === undefined || value === '') params.delete(key);
    else params.set(key, String(value));
  }
  const query = params.toString().replace(/%3A/gi, ':').replace(/%2C/gi, ',');
  const hash = `#/${page}${query ? `?${query}` : ''}`;
  if (hash === location.hash) return;
  if (replace) location.replace(hash);
  else location.hash = hash;
};

function route() {
  const { page, params } = readAddress();
  const changedPage = page !== ui.page || mounted === null;
  ui.page = page;
  ui.params = params;
  if (!running) return;
  drawTop();
  if (changedPage) {
    if (mounted) mounted.unmount();
    clear(main);
    mounted = PAGES[page].view;
    mounted.mount(main, params);
  } else {
    mounted.update(params);
  }
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
      onchange: () => location.assign(choose.value ? `/board/p/${choose.value}#/home` : '/board'),
    },
    projectList.map((one) => h('option', { value: String(one.id) }, one.name)),
    h('option', { value: '' }, 'All projects…'),
  );
  choose.value = String(project.id);
  return choose;
}

function drawTop() {
  const links = Object.entries(PAGES).map(([name, page]) =>
    h(
      'a',
      {
        href: `#/${name}`,
        'aria-current': name === ui.page ? 'page' : null,
        class: name === ui.page ? 'current' : null,
      },
      page.title,
    ),
  );
  clear(top).append(
    brand('#/home'),
    switcher(),
    h('nav', { 'aria-label': 'Pages' }, links),
    h('span', { class: 'spacer' }),
    h('button', { type: 'button', id: 'switcher-open', class: 'link', title: 'Ctrl+K, or ⌘K', onclick: () => openJump() }, 'Jump to…'),
    h('span', { id: 'connection', class: 'chip', hidden: true }, 'Cannot reach the server: trying again'),
    h('span', { id: 'index-state', class: 'chip', title: 'Items that are new or changed and not yet searchable by meaning' }, indexText()),
    h(
      'button',
      { type: 'button', id: 'who', class: 'link', onclick: () => askName() },
      `You are ${actor() || '(no name)'}: change`,
    ),
    ...accountBar(),
  );
}

// What is at the top before a project is open.
function drawEntryTop() {
  clear(top).append(brand('/board'), h('span', { class: 'spacer' }), ...(session ? accountBar() : []));
}

onChange((change) => {
  if (change.index) {
    const chip = document.getElementById('index-state');
    if (chip) chip.textContent = indexText();
    if (mounted && mounted.index) mounted.index();
  }
  if (change.events && mounted) {
    mounted.refresh(change.events);
    panel.refresh(change.events);
    // A title changed, or an item came or went: every reference on the page says so at once.
    refreshReferences(document);
  }
});

function connectionTrouble(error) {
  if (error instanceof Stale) return;
  if (error instanceof NotSignedIn) {
    leave('Your session has ended. Sign in again.');
    return;
  }
  const chip = document.getElementById('connection');
  if (chip) chip.hidden = false;
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
          drawTop();
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
  panel.hide();
  setProject(null);
  drawEntryTop();
  showSignIn(main, { onSignedIn: () => location.reload(), message });
}

// Load everything the open project holds and start the clock.
async function begin() {
  await start();
  running = true;
  keepSynced(connectionTrouble);
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
const openJump = keepSwitcher(
  Object.entries(PAGES).map(([name, page]) => [name, page.title]),
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
