// What the pages share: who is using the board, where they are, and how to tell them something.
// app.js fills in `navigate` when the board starts.

import { h } from './dom.js';

export const ui = {
  page: 'board',
  params: new URLSearchParams(),
  // Change the address after the #: navigate({ sel: 'node:12', epic: null }) sets one and removes
  // the other. `replace` keeps the browser's back button for something else.
  navigate: () => {},
};

const NAME_KEY = 'vrum.actor';

// The address of a page with these choices, as a link: hrefTo('board', { q: 'status:blocked' }).
// An empty choice is left out, and colons and commas stay readable, as in the address bar.
export function hrefTo(page, choices = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(choices)) {
    if (value !== null && value !== undefined && value !== '') params.set(key, String(value));
  }
  const query = params.toString().replace(/%3A/gi, ':').replace(/%2C/gi, ',');
  return `#/${page}${query ? `?${query}` : ''}`;
}

// The record in the shared detail panel, from `sel=node:12`: { kind, id }, or null. The kinds are
// node, rule, decision and fact.
export function parseSel(text) {
  const found = /^(node|rule|decision|fact):([1-9]\d*)$/.exec(String(text || ''));
  return found ? { kind: found[1], id: Number(found[2]) } : null;
}

// A link that shows a record in the detail panel on the page the person is on, leaving the rest of
// the address (the drawer, the filter, the scope) as it is. A plain click changes the address
// without a page load; a click with a modifier key opens it the way a link does.
export function recordLink(kind, id, props, ...children) {
  const params = new URLSearchParams(ui.params);
  params.set('sel', `${kind}:${id}`);
  params.delete('panel');
  const query = params.toString().replace(/%3A/gi, ':').replace(/%2C/gi, ',');
  return h(
    'a',
    {
      ...props,
      href: `#/${ui.page}?${query}`,
      onclick: (event) => {
        if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        ui.navigate({ sel: `${kind}:${id}`, panel: null });
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
