// The top bar. With a project open it holds the way back (the breadcrumbs), the search box (with a
// hint of the quick switcher's key) and what the server and the index are doing (only when there is
// something to say); before one is open it holds the brand, the account and the appearance control (a
// project's appearance is in the account menu at the foot of the sidebar). On a narrow window it also
// holds the button that opens the sidebar.
//
// The breadcrumbs start at the view, as the person is "in" a view: on a view, Search, Activity or the
// coverage report, just its name; on the page of a record, `Board / E-3 Platform foundations / S-12 Audit
// log / T-40 Log screen`, the view first (a link back to it as it was left), then the epic and story above
// the record, each a link to its page, and the record last, every one by its code and its name (the name is
// cut with an ellipsis, the whole of it is the tooltip). An acceptance criterion is under the story or task it
// is on (`... / S-12 Audit log / AC-3 A rider can have only one open ride`); a note is directly under the view
// (`... / Board / N-7 The apps support the last two versions`). The project is in the sidebar's switcher, which
// also reaches the list of projects.

import { appearanceControl } from './appearance.js';
import { wordmark } from './brand.js';
import { backToView, can, itemLink, recordCode, stepBack, ui } from './ctx.js';
import { h, put } from './dom.js';
import { icon } from './icons.js';
import { changeCode, parseItemRef } from './labels.js';
import { openSidebar } from './sidebar.js';
import { describeItem, store } from './store.js';

const top = document.getElementById('top');
let bar = null; // the parts of the top bar of an open project

const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

// A plain click on the arrow is the browser's own Back, so the page one step before comes back
// exactly as it was left, with its scroll. A click that means a new tab or window (a modifier key or
// the middle button) follows the link, and with no step before the link is the way.
function stepBackOnClick(event) {
  if (!ui.previous || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  history.back();
}

export function showTop({ indexText, openSearch }) {
  const search = h('input', { id: 'global-search', type: 'search', name: 'search', autocomplete: 'off', 'aria-label': 'Search work, acceptance criteria and notes', placeholder: 'Search the project…' });
  const list = h('ol', {});
  const crumbs = h('nav', { class: 'crumbs', 'aria-label': 'Breadcrumb' }, list);
  const text = indexText();
  bar = {
    list,
    crumbs,
    drawn: '', // the path as last drawn, so that it is drawn again only when it changes
    back: h('a', { id: 'back-to-view', class: 'back-link', href: '#/board', hidden: true, onclick: stepBackOnClick }, icon('arrow_left', { size: 18 })),
    search,
    typed: '', // the words of the address that the box was last set from
    index: h('span', { id: 'index-state', class: 'chip', title: 'Items that are new or changed and not yet searchable by meaning', hidden: text === null }, icon('search', { size: 14 }), text),
    connection: h('span', { id: 'connection', class: 'chip warn', hidden: true }, icon('alert', { size: 14 }), 'Cannot reach the server: trying again'),
  };
  top.replaceChildren(
    h('button', { type: 'button', id: 'sidebar-toggle', class: 'ghost icon-only', 'aria-controls': 'sidebar', 'aria-expanded': 'false', 'aria-label': 'Open the menu', onclick: openSidebar }, icon('menu', { size: 20 })),
    bar.back,
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
      // The box is the icon, the words and the hint. Enter asks: there is no button.
      h(
        'div',
        { class: 'global-search-field' },
        icon('search', { size: 16, className: 'global-search-icon' }),
        search,
        // The quick switcher has no button: its key is said here, and the hint gives way when the box is in use.
        h('span', { class: 'search-hint', id: 'search-hint', 'aria-hidden': 'true' }, 'or ', h('kbd', {}, mac ? '⌘K' : 'Ctrl K')),
      ),
    ),
    h('span', { class: 'top-chips' }, can('write') ? null : h('span', { id: 'view-only', class: 'chip info view-only', title: 'You can look at everything in this project, but not change it.' }, icon('lock', { size: 14 }), 'View only'), bar.connection, bar.index),
  );
}

// What a record is called in the path when it is not known (it was not found, or the acceptance criteria
// and notes are not read yet): its code.
function nameOfUnknown(ref) {
  const found = parseItemRef(ref);
  return found ? recordCode(found.kind, found.number) : 'Not found';
}

// The records above one on its page, from the top: the epic, then the story (for an acceptance criterion, the
// story or task it is on, and what is above that).
function above(found) {
  const refs = [];
  for (let up = found.parent; up; up = store.nodes.get(up)?.parent) refs.unshift(up);
  return refs.map((ref) => ({ ref, found: describeItem(ref) })).filter((one) => one.found);
}

// The steps of the path for the page the address names: [{ kind, text, ref?, href? }], the last of
// them the page itself. A record's path starts at the view it was reached from, and every record in it
// is its code and its name (E-3 Platform foundations); the whole of it is the tooltip.
function pathOf(title) {
  // A change's review page: the page of what needs a person it is read from, then the change by its code.
  if (ui.page === 'review') {
    const back = backToView();
    return [{ kind: 'view', text: back.label, href: back.hash }, { kind: 'page', text: changeCode(ui.params.get('change')) ?? title }];
  }
  if (ui.page !== 'item') return [{ kind: 'page', text: title }];
  const back = backToView();
  const steps = [{ kind: 'view', text: back.label, href: back.hash }];
  const ref = ui.params.get('item');
  const found = describeItem(ref);
  if (!found) return [...steps, { kind: 'page', text: nameOfUnknown(ref), item: true }];
  const step = (one) => ({ code: one.name, text: one.title, full: `${one.name} ${one.title}` });
  for (const { ref: up, found: parent } of above(found)) steps.push({ kind: 'item', ref: up, ...step(parent) });
  return [...steps, { kind: 'page', item: true, ...step(found) }];
}

// A record in the path: its code, then its name, which is cut with an ellipsis when it does not fit.
const nameOnly = (step) => [step.code ? h('span', { class: 'crumb-code' }, step.code) : null, h('span', { class: 'crumb-name' }, step.text)];

function stepElement(step) {
  if (step.kind === 'view') return h('li', { class: 'crumb-view' }, h('a', { href: step.href, title: `Back to the ${step.text}, as you left it` }, step.text));
  if (step.kind === 'item') return h('li', { class: 'crumb-item' }, itemLink(step.ref, { title: step.full }, ...nameOnly(step)));
  if (step.item) return h('li', { id: 'crumb-page', class: 'crumb-item', title: step.full || null, 'aria-current': 'page' }, ...nameOnly(step));
  return h('li', { id: 'crumb-page', 'aria-current': 'page' }, step.text);
}

// What the address decides: the path, and the words in the search box (which follow the address on
// the Search page unless they are being typed in). It gives back what the browser's tab should call
// the page: the record, or the page's title.
export function updateTop(title) {
  if (!bar) return title;
  const path = pathOf(title);
  const now = JSON.stringify(path);
  if (now !== bar.drawn) {
    bar.drawn = now;
    put(bar.list, path.map(stepElement));
  }
  bar.crumbs.classList.toggle('has-trail', ui.page === 'item');
  // On the page of a record a back arrow at the left of the bar goes one step back: to the page the
  // person was on just before, as they left it (its filter, its scroll). With no step before it
  // goes to the view the record was reached from. (The review page has its own arrow in its header.)
  const trail = ui.page === 'item';
  bar.back.hidden = !trail;
  if (trail) {
    const back = stepBack();
    bar.back.href = back.hash;
    bar.back.title = back.words;
    bar.back.setAttribute('aria-label', back.words);
    bar.back.dataset.step = back.onTrail ? 'previous' : 'view';
  }
  const words = ui.page === 'search' ? ui.params.get('search') || '' : '';
  if (words !== bar.typed) {
    bar.typed = words;
    if (document.activeElement !== bar.search) bar.search.value = words;
  }
  const last = path[path.length - 1];
  return last.full || last.text;
}

// What the index has to say, or null when it has nothing a person needs (nothing waiting): the chip
// is then left out of the bar altogether.
export function setIndexText(text) {
  if (!bar) return;
  bar.index.hidden = text === null;
  put(bar.index, icon('search', { size: 14 }), text);
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
          h('span', { class: 'avatar small', 'aria-hidden': 'true' }, icon('person', { size: 16 })),
          h('span', { id: 'account', class: 'account-name', title: session.account.email }, session.account.name),
          h('button', { type: 'button', id: 'sign-out', class: 'small', onclick: leaveBoard }, 'Sign out'),
        )
      : null,
    appearanceControl(),
  );
}
