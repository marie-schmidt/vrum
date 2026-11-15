// What the pages share: who is using the board, where they are, and how to tell them something.
// app.js fills in `navigate` when the board starts.

import { h } from './dom.js';

export const ui = {
  page: 'board',
  params: new URLSearchParams(),
  // Change the address after the #: navigate({ card: 12, epic: null }) sets one and removes the
  // other. `replace` keeps the browser's back button for something else.
  navigate: () => {},
};

const NAME_KEY = 'vrum.actor';

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
