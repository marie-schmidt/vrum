// What the pages share: who is using the board, where they are, and how to tell them something.
// app.js fills in `navigate` when the board starts.

import { h } from './dom.js';
import { icon } from './icons.js';
import { parseItemRef } from './labels.js';

export const ui = {
  page: 'board',
  params: new URLSearchParams(),
  // Change the address after the #: navigate({ sel: 'story:12', epic: null }) sets one and removes
  // the other. `replace` keeps the browser's back button for something else. `page` goes to
  // another view, taking along what the views share (see carriedTo).
  navigate: () => {},
};

const NAME_KEY = 'vrum.actor';

// The three views the work is seen in, and the pages of settings.
export const VIEWS = ['board', 'graph', 'roadmap'];
export const SETTINGS = ['access', 'tools', 'status'];

// What an address keeps when the person goes to another page: the drawer with its own choices
// (search, duplicates and activity) everywhere, and, between the three views, the selected record
// and the work filter and epic. The item of the item page (`item`) is kept nowhere: it names that
// page.
const DRAWER_KEYS = ['drawer', 'search', 'search_mode', 'search_type', 'search_status', 'search_epic', 'dups', 'dup_type', 'dup_min', 'dup_epics', 'dup_unlinked', 'act_actor', 'act_kind', 'act_item', 'act_from', 'act_to'];
const VIEW_KEYS = ['sel', 'q', 'epic'];

function carriedTo(page) {
  // The selected record, filter and epic belong to the three views, so they go only between them.
  const keys = VIEWS.includes(page) && VIEWS.includes(ui.page) ? [...DRAWER_KEYS, ...VIEW_KEYS] : DRAWER_KEYS;
  const params = new URLSearchParams();
  for (const key of keys) if (ui.params.has(key)) params.set(key, ui.params.get(key));
  return params;
}

const readable = (params) => params.toString().replace(/%3A/gi, ':').replace(/%2C/gi, ',');

// The address of a page after these changes to what the person has now: on the page they are on,
// all of it; on another, what carriedTo keeps. A null or empty choice removes it.
export function addressOf(page, changes = {}) {
  const params = page === ui.page ? new URLSearchParams(ui.params) : carriedTo(page);
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

// The record in the shared detail panel, from `sel=story:12`: { kind, number, id, ref }, or null.
// The kinds are epic, story, task, rule, decision and fact; `id` is the number again, for the
// kinds that are still called by an id (a requirement, a decision, a fact).
export const parseSel = (text) => parseItemRef(text);

// A link that shows a record in the detail panel on the page the person is on, leaving the rest of
// the address (the drawer, the filter, the scope) as it is. `kind` is the real kind (story, rule)
// and `number` its number. A plain click changes the address without a page load; a click with a
// modifier key opens it the way a link does.
export function recordLink(kind, number, props, ...children) {
  return h(
    'a',
    {
      ...props,
      href: addressOf(ui.page, { sel: `${kind}:${number}`, panel: null }),
      onclick: (event) => {
        if (!plainClick(event)) return;
        event.preventDefault();
        ui.navigate({ sel: `${kind}:${number}`, panel: null });
      },
    },
    ...children,
  );
}

// The page of one epic, story or task: #/item?item=story:12. `item` is not carried to another
// page, and is not kept when another page is chosen.
export const itemAddress = (ref) => hrefTo('item', { item: ref });

// A link to the page of an epic, story or task. A plain click goes there keeping the drawer and
// dropping the quick look (`sel`) of the page it leaves.
export function itemLink(ref, props, ...children) {
  return h(
    'a',
    {
      ...props,
      href: itemAddress(ref),
      onclick: (event) => {
        if (!plainClick(event)) return;
        event.preventDefault();
        ui.navigate({ item: ref, sel: null, panel: null }, { page: 'item' });
      },
    },
    ...children,
  );
}

// The name every change carries. Kept in this browser; whoever types it is who the history says.
export function actor() {
  return localStorage.getItem(NAME_KEY) || '';
}

export function setActor(name) {
  localStorage.setItem(NAME_KEY, name.trim());
}

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
      { type: 'button', class: 'link', onclick: () => notice.remove() },
      'Dismiss',
    ),
  );
  box.prepend(notice);
  while (box.children.length > 3) box.lastElementChild.remove();
  if (!refusal) setTimeout(() => notice.remove(), 6000);
}
