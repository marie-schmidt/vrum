// What the pages share: who is using the board, where they are, and how to tell them something.
// app.js fills in `navigate` when the board starts.

import { h } from './dom.js';
import { icon } from './icons.js';
import { itemCode, parseItemRef } from './labels.js';

export const ui = {
  page: 'board',
  // What the signed-in person is in the open project: owner, editor or viewer (see `can`).
  role: 'owner',
  params: new URLSearchParams(),
  // The page the person is "in": a view, Search, Activity or the coverage report. On such a page it
  // is that page; on the page of a record it is the `from` of the address (the Board when there is
  // none), so a record reached from the Graph, and every record reached from it, stays in the Graph.
  from: 'board',
  // The full address of the last view, Search, Activity or coverage page the person was on, as it
  // was left (its filter, scope and search). Kept in memory: after a reload it is empty.
  lastView: null,
  // The address of the page the person was on just before this one (`#/graph?...`), or null when
  // they arrived by an address. It is what the back arrow of a record's page goes to.
  previous: null,
  // Change the address after the #: navigate({ epic: '3', q: null }) sets one and removes the
  // other. `replace` keeps the browser's back button for something else. `page` goes to another
  // page, taking along what the views share (see carriedTo).
  navigate: () => {},
  // Sign out and go to the sign-in page; app.js fills it in when the board starts.
  signOut: () => {},
};

// The four views the work is seen in, and the pages of settings.
export const VIEWS = ['board', 'graph', 'roadmap', 'lists'];
export const SETTINGS = ['users', 'definition', 'tools', 'status'];
// The pages a record page can say it was reached from, and what each is called.
export const FROM_PAGES = [...VIEWS, 'search', 'activity', 'coverage'];
export const FROM_TITLES = { board: 'Board', graph: 'Graph', roadmap: 'Roadmap', lists: 'Lists', search: 'Search', activity: 'Activity', coverage: 'Coverage' };

// What an address keeps when the person goes to another page: between the four views, the work
// filter and the epic. Nothing else goes along: a page's own choices (the search, the activity
// filters, the Graph's focus) belong to that page, and the way back to it is `lastView`.
const VIEW_KEYS = ['q', 'epic'];

function carriedTo(page) {
  const params = new URLSearchParams();
  if (!VIEWS.includes(page) || !VIEWS.includes(ui.page)) return params;
  for (const key of VIEW_KEYS) if (ui.params.has(key)) params.set(key, ui.params.get(key));
  return params;
}

const readable = (params) => params.toString().replace(/%3A/gi, ':').replace(/%2C/gi, ',');

// The address of a page after these changes to what the person has now: on the page they are on,
// all of it; on another, what carriedTo keeps. A null or empty choice removes it. Going to another
// record from the page of one starts the address over (the page's own choices were about the
// record it leaves).
export function addressOf(page, changes = {}) {
  const another = page === 'item' && 'item' in changes && changes.item !== ui.params.get('item');
  const params = page === ui.page && !another ? new URLSearchParams(ui.params) : carriedTo(page);
  for (const [key, value] of Object.entries(changes)) {
    if (value === null || value === undefined || value === '') params.delete(key);
    else params.set(key, String(value));
  }
  const query = readable(params);
  return `#/${page}${query ? `?${query}` : ''}`;
}

// The address of a page with only these choices, as a link: hrefTo('board', { q: 'status:blocked' }).
// An empty choice is left out, and colons and commas stay readable, as in the address bar.
export function hrefTo(page, choices = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(choices)) {
    if (value !== null && value !== undefined && value !== '') params.set(key, String(value));
  }
  const query = readable(params);
  return `#/${page}${query ? `?${query}` : ''}`;
}

const plainClick = (event) => event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey;

// A link to a page with these choices. Its address is the plain one (hrefTo), so that it reads
// the same everywhere; a plain click goes there with what the views share kept, as navigate does.
export function viewLink(page, choices, props, ...children) {
  return h(
    'a',
    {
      ...props,
      href: hrefTo(page, choices),
      onclick: (event) => {
        if (!plainClick(event)) return;
        event.preventDefault();
        ui.navigate(choices, { page });
      },
    },
    ...children,
  );
}

// The same thing under the name the record pages use.
export const pageLink = viewLink;

// The record in an address, from `item=story:12` (or the Graph's `sel=story:12`): { kind, number,
// id, ref }, or null. The kinds are epic, story, task, rule and note; `id` is the number
// again, for the kinds that are still called by an id (an acceptance criterion, a note).
export const parseSel = (text) => parseItemRef(text);

// The address of the page of one record: #/item?item=story:12&from=graph. `ref` is epic:3,
// story:12, task:40, rule:3 or note:7; `from` is the page the person is in.
export const itemAddress = (ref) => hrefTo('item', { item: ref, from: ui.from });

// A link to the page of a record. A plain click goes there, in the same page; a click with a
// modifier key opens it the way a link does.
export function itemLink(ref, props, ...children) {
  return h(
    'a',
    {
      ...props,
      href: itemAddress(ref),
      onclick: (event) => {
        if (!plainClick(event)) return;
        event.preventDefault();
        openItem(ref);
      },
    },
    ...children,
  );
}

// Go to the page of a record.
export const openItem = (ref) => ui.navigate({ item: ref, from: ui.from }, { page: 'item' });

// Where "Back to the Board" goes from a record page: the view the person is in, as they left it,
// or the plain page when they arrived by an address (after a reload, say). `label` is that page's
// name ("Board", "Graph", "Roadmap", "Search", "Activity", "Coverage").
export function backToView() {
  const label = FROM_TITLES[ui.from] || FROM_TITLES.board;
  const kept = ui.lastView && ui.lastView.split('?')[0] === `#/${ui.from}` ? ui.lastView : null;
  return { label, hash: kept || `#/${FROM_PAGES.includes(ui.from) ? ui.from : 'board'}` };
}

const STEP_NAMES = { ...FROM_TITLES, users: 'Users', definition: 'Definition of done', tools: 'Tools', status: 'Status' };

// The page one step back, which the back arrow goes to: where the person was just before, as they
// left it, named by its page (Graph, Search, Users) or, for a record, by its code (S-12). With no
// step before (the address was loaded fresh) it is the view the record was reached from.
export function stepBack() {
  const step = ui.previous;
  if (!step) {
    const view = backToView();
    return { hash: view.hash, words: `Back to ${view.label}`, onTrail: false };
  }
  const [page, query = ''] = step.replace(/^#\/?/, '').split('?');
  const found = page === 'item' ? parseItemRef(new URLSearchParams(query).get('item')) : null;
  const name = found ? itemCode(found.kind, found.number) : STEP_NAMES[page] || FROM_TITLES.board;
  return { hash: step, words: `Back to ${name}`, onTrail: true };
}

// Who is signed in: { id, email, name } and, from the server, whether the address is verified
// (`email_verified`) and whether the account has a password (`has_password`: an account made by a
// provider has none). app.js fills it in when the board starts, and the Users page changes the name in
// place, so that every page that says it reads the same one. The server decides who a change is
// recorded as; this is only what the board shows.
export const account = { id: null, email: '', name: '' };

// Whether the person may do something here, from their role: 'write' is changing the work and what
// is known about it (an owner and an editor may, a viewer may not), 'manage' is who is in the project
// and its agent keys (only the owner). The server decides every request in the same way: this only
// keeps the board from offering what it would refuse.
export const can = (action) => (action === 'write' ? ui.role !== 'viewer' : ui.role === 'owner');

// Whether a name with its kind (from a claim or a history line) is the signed-in person.
export const isMe = (name, kind) => kind === 'person' && name === account.name;

// A message under the top bar. A refusal says what the server said, in its words, and stays until
// it is dismissed; anything else goes away by itself.
export function notify(message, { refusal = false, detail = null } = {}) {
  const box = document.getElementById('notices');
  if (!box) return;
  const notice = h(
    'div',
    { class: `notice ${refusal ? 'refusal' : 'done'}`, role: refusal ? 'alert' : 'status' },
    icon(refusal ? 'alert' : 'check', { size: 16, className: 'notice-icon' }),
    h('strong', {}, refusal ? 'Not done. ' : ''),
    h('span', { class: 'notice-text' }, message),
    detail,
    h(
      'button',
      { type: 'button', class: 'ghost icon-only small notice-close', onclick: () => notice.remove() },
      icon('close', { size: 14 }),
      h('span', { class: 'sr-only' }, 'Dismiss'),
    ),
  );
  box.prepend(notice);
  while (box.children.length > 3) box.lastElementChild.remove();
  if (!refusal) setTimeout(() => notice.remove(), 6000);
}
