// The board's shell: signing in, the person's projects, the sidebar and top bar, the address and the
// clock that asks the server what changed.
//
// A project's board lives at /board/p/<number>, with the page and its choices after the #. The
// server decides whether the person owns the project, on every request. Moving to another project
// is a whole new page load, so nothing of the project left can be drawn in the one entered.
//
// Everything is a page, and a record never opens beside the page: the four views are the work
// itself (Board, Graph, Roadmap, Lists); Search, Activity, Needs a person (what only a person can settle) and the
// acceptance criteria coverage report are pages
// of their own, and so is every epic, story, task, acceptance criterion and note:
// #/item?item=story:12&from=graph, and every change that waits for a person has its own review page: #/review?change=plan:3, read from the page of
// what needs a person and back to it. `from` is the page the person is in (a view, Search, Activity or
// coverage), so the path and the sidebar keep it while they move from record to record. The
// settings pages (the users and agent keys, the definition of done, the plan templates, the tools, the status and the export) are reached from the Settings
// menu, and the person's own Account page from the account menu at the foot of the sidebar. The address holds all of it:
// #/search?search=login, #/board?q=status:blocked.

import {
  NotInProject,
  NotSignedIn,
  Refusal,
  Stale,
  loadSession,
  projectInfo,
  projects,
  setProject,
  signOut,
} from './api.js';
import './select.js';
import { FROM_PAGES, SETTINGS, VIEWS, account, addressOf, notify, ui } from './ctx.js';
import { clear, h } from './dom.js';
import { showNoProject, showProjects, showSignIn } from './entry.js';
import { INVITE_INTRO, showInvite } from './invite.js';
import { icon } from './icons.js';
import { showReset, showVerify } from './mail-pages.js';
import * as accountPage from './account.js';
import * as activity from './activity.js';
import * as coverage from './coverage.js';
import * as definition from './definition.js';
import * as graph from './graph.js';
import * as item from './item.js';
import * as kanban from './kanban.js';
import * as lists from './lists.js';
import * as needs from './needs.js';
import * as review from './review.js';
import { refreshReferences } from './references.js';
import * as roadmap from './roadmap.js';
import * as search from './search.js';
import { hideSidebar, showSidebar, updateSidebar } from './sidebar.js';
import { problemWords, providerName, takeSsoResult } from './sso.js';
import * as status from './status.js';
import { ensureContext, keepSynced, onChange, start, stopSyncing, store } from './store.js';
import { keepSwitcher } from './switcher.js';
import * as templates from './templates.js';
import { setConnection, setIndexText, showEntryTop, showTop, updateTop } from './topbar.js';
import * as tools from './tools.js';
import * as users from './users.js';

// Every page there is. Anything else in the address is the Board. The page of a record is in no
// menu: the address names the record (#/item?item=story:12&from=board). The Account page is in the
// account menu only.
const PAGES = {
  board: { title: 'Board', view: kanban },
  graph: { title: 'Graph', view: graph },
  roadmap: { title: 'Roadmap', view: roadmap },
  lists: { title: 'Lists', view: lists },
  search: { title: 'Search', view: search },
  activity: { title: 'Activity', view: activity },
  needs: { title: 'Needs a person', view: needs },
  coverage: { title: 'Coverage', view: coverage },
  users: { title: 'Users', view: users },
  definition: { title: 'Definition of done', view: definition },
  templates: { title: 'Plan templates', view: templates },
  tools: { title: 'Tools', view: tools },
  status: { title: 'Status and export', view: status },
  account: { title: 'Account', view: accountPage },
  item: { title: 'Item', view: item },
  review: { title: 'Review', view: review },
};
const TITLES = Object.fromEntries(Object.entries(PAGES).map(([name, page]) => [name, page.title]));
const app = document.getElementById('app');
const main = document.getElementById('page');
const dialogs = document.getElementById('dialogs');

// Publishes the sticky top bar height to CSS (`--top-now`) and marks pointer vs keyboard input
// so focus rings show only for keyboard navigation (base.css).
function keepLayout() {
  const top = document.getElementById('top');
  const publish = () => {
    const sticks = getComputedStyle(top).position === 'sticky';
    document.documentElement.style.setProperty('--top-now', `${sticks ? Math.round(top.getBoundingClientRect().height) : 0}px`);
  };
  new ResizeObserver(publish).observe(top);
  matchMedia('(min-width: 700px)').addEventListener('change', publish);
  publish();
  const root = document.documentElement;
  const KEYS = new Set(['Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End']);
  addEventListener('pointerdown', () => { root.dataset.input = 'pointer'; }, true);
  addEventListener('keydown', (event) => { if (KEYS.has(event.key)) delete root.dataset.input; }, true);
}
// The skip link goes to the page without changing the address (which would make the Board of it).
document.querySelector('.skip').addEventListener('click', (event) => {
  event.preventDefault();
  main.focus();
});
let mounted = null; // the page on screen
let shownItem = null; // the record on the page of a record, to know when another one is shown
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

// The invitation in the address, if any: /board/invite/<token>.
const inviteToken = (() => {
  const found = location.pathname.match(/^\/board\/invite\/([^/]+)\/?$/);
  return found ? decodeURIComponent(found[1]) : null;
})();

// The page an email's link opens, if any: /board/reset/<token> (choose a new password) or
// /board/verify/<token> (confirm an address). The page takes the token out of the address.
const mailLink = (() => {
  const found = location.pathname.match(/^\/board\/(reset|verify)\/([^/]+)\/?$/);
  return found ? { kind: found[1], token: decodeURIComponent(found[2]) } : null;
})();

// -- the address ----------------------------------------------------------------------------------

// What the address says: the page, and its choices. A choice that means nothing any more (the old
// side panel's `sel` and `panel`, and the drawer's `drawer`) is left out; `sel` is kept on Lists,
// where it is the chosen record.
function readAddress() {
  const [name, query = ''] = location.hash.replace(/^#\/?/, '').split('?');
  const known = Object.hasOwn(PAGES, name);
  const params = new URLSearchParams(known ? query : '');
  let stale = false;
  for (const key of name === 'lists' ? ['drawer', 'panel'] : ['drawer', 'panel', 'sel']) {
    if (params.has(key)) {
      params.delete(key);
      stale = true;
    }
  }
  return { page: known ? name : 'board', known, params, stale };
}

ui.navigate = (changes, { replace = false, page = ui.page } = {}) => {
  const hash = addressOf(page, changes);
  if (hash === location.hash) return;
  // The address is handled a moment after it is set. Remember what it holds at once, so that a
  // second change made before then builds on this one instead of dropping it.
  if (page === ui.page) ui.params = new URLSearchParams(hash.split('?')[1] ?? '');
  // A replaced entry takes the place of the one on show, so it keeps that one's step before it.
  if (replace) {
    replacing = true;
    replacedStep = history.state?.step ?? null;
    location.replace(hash);
  } else location.hash = hash;
};

// What the top bar and the browser's tab say about where the person is. On the page of a record
// both name the record, so they follow a rename.
function drawTop() {
  document.title = `${updateTop(PAGES[ui.page].title)} · ${project.name} · Vrum`;
}

function route() {
  const { page, known, params, stale } = readAddress();
  const changedPage = page !== ui.page || mounted === null;
  ui.page = page;
  ui.params = params;
  if (page === 'item') ui.from = FROM_PAGES.includes(params.get('from')) ? params.get('from') : 'board';
  else if (page === 'review') ui.from = 'needs';
  else if (FROM_PAGES.includes(page)) ui.from = page;
  // A page that is not there (an old address, a mistake) is the Board, and the address says so; a
  // choice that means nothing is taken out of it.
  if (running && (!known || stale)) history.replaceState(history.state, '', `${location.pathname}${location.search}${addressOf(page)}`);
  // The way back from a record page goes to the page it was reached from, as it was left.
  if (FROM_PAGES.includes(page)) ui.lastView = addressOf(page);
  if (!running) return;
  // Which entry of the browser's history this is, and how far the one just left was scrolled.
  const left = entry;
  if (left) scrolled.set(left, farDown);
  entry = stampEntry();
  const returning = entry !== left ? scrolled.get(entry) : undefined;
  drawTop();
  updateSidebar();
  const record = page === 'item' ? params.get('item') : null;
  const another = record !== shownItem;
  shownItem = record;
  if (changedPage) {
    if (mounted) mounted.unmount();
    clear(main);
    mounted = PAGES[page].view;
    mounted.mount(main, params);
  } else {
    mounted.update(params);
  }
  // A new page, or another record, starts at its top. If what was clicked is gone with the old
  // page, the focus goes to the page, so that the keyboard carries on from there.
  if (changedPage || another) {
    window.scrollTo(0, 0);
    if (!document.activeElement || document.activeElement === document.body) main.focus({ preventScroll: true });
    // Back and Forward bring the person to the place they left, not to the top.
    if (returning) keepScroll(returning);
  }
}

// -- where the page was scrolled to ------------------------------------------------------------------

// The browser cannot put a page back where it was, since the page is drawn after its data arrives,
// so the board remembers how far each entry of the history was scrolled and goes there again when
// the person comes back to it, as soon as the page is long enough.
history.scrollRestoration = 'manual';
const scrolled = new Map(); // history entry -> how far its page was scrolled
let entry = null; // the history entry on show
let farDown = 0; // how far the page on show is scrolled
let stopKeeping = null;
addEventListener('scroll', () => (farDown = window.scrollY), { passive: true });

// -- the step before -------------------------------------------------------------------------------

// Every entry of the history is stamped, as it is first shown, with the address of the page the
// person came from: its `step`. That is what the back arrow of a record's page goes to, so it is
// always one step back and never a guess. It is kept in the entry itself, so it survives a reload
// and nothing is stored elsewhere. An entry reached by an address typed or loaded fresh has none.
let shownAddress = null; // the address of the entry on show, which the next entry came from
let replacing = false; // the next entry replaces the one on show instead of following it
let replacedStep = null;

function stampEntry() {
  if (!history.state?.entry) {
    const step = replacing ? replacedStep : shownAddress;
    history.replaceState({ ...history.state, entry: Math.random().toString(36).slice(2), step }, '');
  }
  replacing = false;
  shownAddress = location.hash;
  ui.previous = history.state.step || null;
  return history.state.entry;
}

function keepScroll(far) {
  stopKeeping?.();
  const settled = { times: 0 };
  const until = Date.now() + 4000;
  const events = ['wheel', 'touchstart', 'keydown', 'mousedown'];
  const stop = () => {
    clearInterval(timer);
    for (const type of events) removeEventListener(type, stop, true);
    stopKeeping = null;
  };
  // Drawn a little at a time, so it goes on being put right for a moment after the page is long enough.
  const timer = setInterval(() => {
    const room = document.documentElement.scrollHeight - window.innerHeight;
    if (room >= far) {
      window.scrollTo(0, far);
      if (++settled.times >= 3) stop();
    } else if (Date.now() > until) {
      window.scrollTo(0, room);
      stop();
    }
  }, 100);
  for (const type of events) addEventListener(type, stop, { capture: true, passive: true });
  stopKeeping = stop;
}

// -- the shell --------------------------------------------------------------------------------------

// What the chip in the top bar says about the search index, or null when there is nothing to say: the
// index is up to date (nothing waiting) and the embedding service answers, or it is not read yet.
// The Status page has the whole account.
function indexText() {
  const index = store.index;
  if (!index) return null;
  if (index.meaning === 'not_configured') return 'Search by meaning is not set up';
  if (index.meaning === 'service_unreachable') {
    return `Search by meaning is not answering${index.waiting_to_index > 0 ? `: ${index.waiting_to_index} waiting` : ''}`;
  }
  if (index.meaning === 'index_not_ready') {
    return `Index not built yet: ${index.waiting_to_index} waiting`;
  }
  return index.waiting_to_index > 0 ? `Index: ${index.waiting_to_index} waiting` : null;
}

async function leaveBoard() {
  try {
    await signOut();
  } finally {
    location.assign('/board');
  }
}
ui.signOut = leaveBoard;

// Enter in the box of the top bar goes to the Search page with those words.
function openSearch(text) {
  const before = location.hash;
  ui.navigate({ search: text || null, search_type: null, search_status: null, search_epic: null, dups: null }, { page: 'search' });
  // The same words again ask again.
  if (location.hash === before && mounted && mounted.again) mounted.again();
}

// What is at the top, and no sidebar, before a project is open.
function entryShell() {
  hideSidebar();
  app.dataset.shell = 'entry';
  showEntryTop({ session, leaveBoard });
}

function projectShell() {
  app.dataset.shell = 'project';
  showSidebar({ session, project, projectList, views: VIEWS, settings: SETTINGS, titles: TITLES, leaveBoard });
  showTop({ indexText, openSearch });
}

onChange((change) => {
  if (change.index) {
    setIndexText(indexText());
    if (mounted && mounted.index) mounted.index();
  }
  // A page put a newer copy of one item into the store (after a save, say): the path, the tab's
  // title and the navigator say so at once. Both draw only what changed.
  if (change.node && running) {
    drawTop();
    updateSidebar();
  }
  if (change.events && mounted) {
    drawTop();
    updateSidebar();
    mounted.refresh(change.events);
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

function connectionTrouble(error, failures = 1) {
  if (error instanceof Stale) return;
  if (error instanceof NotSignedIn) {
    leave('Your session has ended. Sign in again.');
    return;
  }
  if (error instanceof NotInProject) {
    leaveProject(error.message);
    return;
  }
  // The first failure is asked again at once (see keepSynced), and nothing is said while the tab is in the back: a
  // sleeping tab's request is often cut off, which is not the server being gone.
  if (failures < 2 || document.visibilityState === 'hidden') return;
  showConnection(true);
}

// -- getting in and out ------------------------------------------------------------------------------

// Take the open project off the screen: its pages, its clock, its dialogs.
function closeProject() {
  stopSyncing();
  running = false;
  if (mounted) mounted.unmount();
  mounted = null;
  shownItem = null;
  clear(main);
  for (const open of dialogs.querySelectorAll('dialog')) open.close();
  setProject(null);
  entryShell();
  showConnection(false);
}

// The person is not in the project any more: say so, and where to go instead.
function leaveProject(message) {
  closeProject();
  showNoProject(main, message);
}

// Show the sign-in page, with a word about why when there is one. `notice` is what a provider's
// sign-in came back with.
function leave(message = '', notice = '') {
  session = null;
  closeProject();
  showSignIn(main, { onSignedIn: () => location.reload(), message, notice });
}

// Load everything the open project holds and start the clock.
async function begin() {
  await start();
  running = true;
  projectShell();
  keepSynced(connectionTrouble, () => showConnection(false));
  route();
  // The path of an acceptance criterion or a note, and the quick switcher, want them read, so they are
  // read now.
  ensureContext()
    .then(() => drawTop())
    .catch(() => {});
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
  ui.role = project.role;
  app.dataset.role = project.role;
  await begin();
}

addEventListener('hashchange', route);

// Ctrl+K (or ⌘K) opens the quick switcher on every page.
const jumpTo = (page) => ({ name: page, title: PAGES[page].title, go: () => ui.navigate({}, { page }) });
keepSwitcher(
  [...VIEWS.map(jumpTo), jumpTo('search'), jumpTo('activity'), jumpTo('needs'), jumpTo('coverage'), ...SETTINGS.map(jumpTo)],
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

// A provider has just sent a signed-in person back with its answer: they were connecting it to their
// account, so it is said on the Account page, where they started. (A person who is not signed in is
// told on the sign-in page instead.)
function tellSso(result) {
  if (result === null) return;
  if (running) ui.navigate({}, { page: 'account', replace: true });
  if ('error' in result) notify(problemWords(result.error, result.provider), { refusal: true });
  else notify(result.provider ? `${providerName(result.provider)} is now connected to your account.` : 'That sign-in is now connected to your account.');
}

(async () => {
  // What a provider said when it sent the person back, read once and taken out of the address.
  const sso = takeSsoResult();
  route();
  // While the session and the project load, the page already has its brand and the appearance control.
  showEntryTop({ session: null, leaveBoard });
  try {
    session = await loadSession();
    // The provider's name is known only now, from the server's list.
    const ssoProblem = sso !== null && 'error' in sso ? problemWords(sso.error, sso.provider) : '';
    // A link from an email works signed in or out, so it is answered before the sign-in page.
    if (mailLink !== null) {
      entryShell();
      if (mailLink.kind === 'reset') await showReset(main, mailLink.token);
      else await showVerify(main, mailLink.token, { signedIn: session !== null });
      return;
    }
    if (session === null) {
      // An invitation's link asks the person to sign in or register first, and brings them back to it.
      if (inviteToken !== null) {
        entryShell();
        showSignIn(main, { onSignedIn: () => location.reload(), intro: INVITE_INTRO, notice: ssoProblem });
        return;
      }
      leave('', ssoProblem);
      return;
    }
    Object.assign(account, session.account);
    if (inviteToken !== null) {
      entryShell();
      await showInvite(main, inviteToken);
      return;
    }
    if (inAddress === null) {
      entryShell();
      await showProjects(main, session);
      tellSso(sso);
      return;
    }
    await openProject(inAddress);
    tellSso(sso);
  } catch (error) {
    if (error instanceof NotSignedIn) {
      leave('Your session has ended. Sign in again.');
      return;
    }
    if (app.dataset.shell === 'loading') entryShell();
    unavailable(error instanceof Refusal ? error.message : 'The board could not start. Reload the page to try again.');
  }
})();
