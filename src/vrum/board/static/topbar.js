// The top bar. With a project open it holds the way back (the breadcrumbs), the search box, the
// quick switcher's button and what the server and the index are doing; before one is open it holds
// the brand, the account and the appearance control. On a narrow window it also holds the button
// that opens the sidebar.

import { appearanceControl } from './appearance.js';
import { wordmark } from './brand.js';
import { itemLink, ui, viewLink } from './ctx.js';
import { h, put } from './dom.js';
import { icon } from './icons.js';
import { codeOf } from './labels.js';
import { openSidebar } from './sidebar.js';
import { trailOf } from './store.js';

const top = document.getElementById('top');
let bar = null; // the parts of the top bar of an open project

const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const initial = (name) => ([...String(name).trim()][0] || '?').toUpperCase();

export function showTop({ project, indexText, openSearch, openJump }) {
  const search = h('input', { id: 'global-search', type: 'search', name: 'search', autocomplete: 'off', 'aria-label': 'Search work, requirements, decisions and facts', placeholder: 'Search work & context…' });
  const page = h('li', { id: 'crumb-page', 'aria-current': 'page' });
  const crumbs = h(
    'nav',
    { class: 'crumbs', 'aria-label': 'Breadcrumb' },
    h(
      'ol',
      {},
      h('li', { class: 'crumb-root' }, h('a', { href: '/board' }, 'Projects')),
      h('li', { class: 'crumb-project' }, viewLink('board', {}, { title: project.name }, project.name)),
      page,
    ),
  );
  bar = {
    page,
    crumbs,
    trail: [], // the crumbs of the items above the one on the item page
    search,
    typed: '', // the words of the address that the box was last set from
    index: h('span', { id: 'index-state', class: 'chip', title: 'Items that are new or changed and not yet searchable by meaning' }, icon('search', { size: 12 }), indexText()),
    connection: h('span', { id: 'connection', class: 'chip warn', hidden: true }, icon('alert', { size: 12 }), 'Cannot reach the server: trying again'),
  };
  quietIndex();
  top.replaceChildren(
    h('button', { type: 'button', id: 'sidebar-toggle', class: 'ghost icon-only', 'aria-controls': 'sidebar', 'aria-expanded': 'false', 'aria-label': 'Open the menu', onclick: openSidebar }, icon('menu', { size: 20 })),
    crumbs,
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
      icon('search', { size: 16, className: 'global-search-icon' }),
      search,
      h('button', { type: 'submit', id: 'global-search-go', class: 'small' }, 'Search'),
    ),
    h(
      'button',
      { type: 'button', id: 'switcher-open', class: 'jump', title: 'Jump to a view or an item (Ctrl+K, or ⌘K)', onclick: openJump },
      icon('arrow_right', { size: 14 }),
      h('span', { class: 'jump-label' }, 'Jump to'),
      h('kbd', {}, mac ? '⌘K' : 'Ctrl K'),
    ),
    h('span', { class: 'top-chips' }, bar.connection, bar.index),
  );
}

// What the address decides: the page in the breadcrumbs, and the words in the search box (which
// follow the address unless they are being typed in). On the item page `item` is the reference in
// the address: the crumbs then read from the epic down to the item, each earlier one a link to its
// own page, and the item itself is the page.
export function updateTop(title, item = null) {
  if (!bar) return;
  for (const crumb of bar.trail) crumb.remove();
  bar.trail = [];
  const trail = item ? trailOf(item) : [];
  bar.crumbs.classList.toggle('has-trail', trail.length > 0);
  if (trail.length) {
    const named = (node) => `${codeOf(node.ref)} ${node.title}`;
    for (const node of trail.slice(0, -1)) {
      const crumb = h('li', { class: 'crumb-item' }, itemLink(node.ref, { title: named(node) }, named(node)));
      bar.page.before(crumb);
      bar.trail.push(crumb);
    }
    const last = trail[trail.length - 1];
    bar.page.className = 'crumb-item';
    bar.page.title = named(last);
    bar.page.textContent = named(last);
  } else {
    bar.page.className = '';
    bar.page.removeAttribute('title');
    bar.page.textContent = title;
  }
  const words = ui.params.get('search') || '';
  if (words !== bar.typed) {
    bar.typed = words;
    if (document.activeElement !== bar.search) bar.search.value = words;
  }
}

// An index with nothing waiting says nothing a person needs, so a narrow window leaves its chip out.
function quietIndex() {
  bar.index.toggleAttribute('data-quiet', /^Index: 0 waiting$/.test(bar.index.textContent));
}

export function setIndexText(text) {
  if (!bar) return;
  bar.index.replaceChildren(icon('search', { size: 12 }), text);
  quietIndex();
}

export function setConnection(trouble) {
  if (bar) bar.connection.hidden = !trouble;
}

// What is at the top before a project is open: the brand, who is signed in, and the appearance.
export function showEntryTop({ session, leaveBoard }) {
  bar = null;
  put(
    top,
    h('a', { href: '/board', class: 'brand', 'aria-label': 'vrum, your projects' }, wordmark(32)),
    h('span', { class: 'spacer' }),
    session
      ? h(
          'div',
          { class: 'account-block inline' },
          h('span', { class: 'avatar small', 'aria-hidden': 'true' }, initial(session.account.name)),
          h('span', { id: 'account', class: 'account-name', title: session.account.email }, session.account.name),
          h('button', { type: 'button', id: 'sign-out', class: 'small', onclick: leaveBoard }, 'Sign out'),
        )
      : null,
    appearanceControl(),
  );
}
