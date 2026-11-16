// The board's shell: sign-in, the person's name, the top bar, the address, and the clock that asks
// the server what changed.

import { NotSignedIn, setToken, signOut, signedIn } from './api.js';
import { actor, notify, setActor, ui } from './ctx.js';
import { clear, h } from './dom.js';
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
  status: { title: 'Status', view: status },
};
const top = document.getElementById('top');
const main = document.getElementById('page');
const dialogs = document.getElementById('dialogs');
let mounted = null; // the page on screen
let running = false; // signed in and loaded

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
    h('a', { href: '#/home', class: 'brand' }, h('img', { src: '/board/brand/vrum-logo.svg', alt: 'vrum', height: 28 })),
    h('nav', { 'aria-label': 'Pages' }, links),
    h('span', { class: 'spacer' }),
    h('button', { type: 'button', id: 'switcher-open', class: 'link', title: 'Ctrl+K, or ⌘K', onclick: () => openJump() }, 'Jump to…'),
    h('span', { id: 'connection', class: 'chip', hidden: true }, 'Cannot reach the server: trying again'),
    h('span', { id: 'index-state', class: 'chip', title: 'Items that are new or changed and not yet searchable by meaning' }, indexText()),
    h(
      'button',
      { type: 'button', id: 'who', class: 'link', onclick: () => askName(true) },
      `You are ${actor() || '(no name)'}: change`,
    ),
    h('button', { type: 'button', id: 'sign-out', class: 'link', onclick: leave }, 'Sign out'),
  );
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
  if (error instanceof NotSignedIn) {
    leave();
    return;
  }
  const chip = document.getElementById('connection');
  if (chip) chip.hidden = false;
}

// -- signing in and the person's name --------------------------------------------------------------

function dialog(title, ...body) {
  const box = h('dialog', { class: 'modal', 'aria-labelledby': 'dialog-title' }, h('h2', { id: 'dialog-title' }, title), ...body);
  dialogs.append(box);
  box.addEventListener('close', () => box.remove());
  return box;
}

function askToken(message) {
  const problem = h('p', { class: 'problem', role: 'alert' }, message || '');
  const field = h('input', { type: 'password', name: 'token', id: 'token', autocomplete: 'off', 'aria-describedby': 'token-help' });
  const box = dialog(
    'Sign in',
    h('p', { id: 'token-help' }, 'Type the token of this server (VRUM_TOKEN in its .env file). It is kept only until this browser tab is closed.'),
    h(
      'form',
      {
        method: 'dialog',
        onsubmit: async (event) => {
          event.preventDefault();
          setToken(field.value);
          try {
            await begin();
            box.close();
          } catch (error) {
            problem.textContent = error.message;
            field.select();
          }
        },
      },
      h('label', { for: 'token' }, 'Token'),
      field,
      problem,
      h('button', { type: 'submit', class: 'primary' }, 'Sign in'),
    ),
  );
  box.addEventListener('cancel', (event) => event.preventDefault());
  box.showModal();
  field.focus();
}

function askName(canCancel) {
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
      canCancel ? h('button', { type: 'button', onclick: () => box.close() }, 'Cancel') : null,
    ),
  );
  if (!canCancel) box.addEventListener('cancel', (event) => event.preventDefault());
  box.showModal();
  field.select();
}

function leave() {
  stopSyncing();
  signOut();
  running = false;
  if (mounted) mounted.unmount();
  mounted = null;
  clear(main);
  clear(top);
  panel.hide();
  askToken('');
}

// Load everything and start the clock. Throws when the server turns the token down.
async function begin() {
  await start();
  running = true;
  keepSynced(connectionTrouble);
  route();
  if (!actor()) askName(false);
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
  if (!signedIn()) {
    askToken('');
    return;
  }
  try {
    await begin();
  } catch (error) {
    askToken(error.message);
  }
})();
