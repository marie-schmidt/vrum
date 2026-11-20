// The sidebar of an open project: the brand, the project switcher, the four views with how much
// each holds, the navigator (the epics, and under them stories and tasks), and, at the foot,
// Activity, the Settings menu and the account. On a narrow window it is off
// to the left and opens over the page (the button in the top bar), closing again on Escape, on a
// click outside it, or when a place is chosen.
//
// The numbers are real and cheap, read from what the board already holds:
//   Board    stories and tasks (what the Board draws in its lanes; epics are the lanes themselves)
//   Graph    work items, plus the acceptance criteria and the notes still in force (what it can
//            look around). It waits for the context to be read, and shows nothing until it has been.
//   Roadmap  items with a date of their own or one worked out from their children (what it draws)
//   Lists    the same as the Graph: every work item, and the acceptance criteria and notes
//            still in force (what "Everything" lists)
//   Activity the items someone holds right now (a claim that has not run out)
//
// The navigator is a tree: every epic, a chevron to open its stories, and on each story a chevron
// to open its tasks. Only what is open is drawn. A title opens that record's page; the record
// being looked at is marked and the ones above it are opened for it. It scrolls on its own, so it
// never makes the sidebar longer.

import { wordmark } from './brand.js';
import { SETTINGS, itemAddress, itemLink, openItem, ui, viewLink } from './ctx.js';
import { h, plural, put } from './dom.js';
import { icon } from './icons.js';
import { KIND, inForce, nodeCode, roleWords } from './labels.js';
import { describeItem, store } from './store.js';

const aside = document.getElementById('sidebar');
const scrim = document.getElementById('sidebar-scrim');
const column = document.getElementById('main-column');
const NARROW = matchMedia('(max-width: 1099.98px)');

const SETTINGS_ICONS = { users: 'person', definition: 'checklist', tools: 'settings', status: 'info' };

let parts = null; // the elements of the open project's sidebar
let opener = null; // what had the focus before it opened, to give it back

// -- opening and closing on a narrow window -------------------------------------------------------------

export const sidebarIsOpen = () => aside.classList.contains('open');

export function openSidebar() {
  if (aside.hidden || sidebarIsOpen()) return;
  opener = document.activeElement;
  aside.classList.add('open');
  scrim.hidden = false;
  column.inert = true;
  document.getElementById('sidebar-toggle')?.setAttribute('aria-expanded', 'true');
  document.getElementById('sidebar-close')?.focus();
}

export function closeSidebar({ restore = true } = {}) {
  if (!sidebarIsOpen()) return;
  aside.classList.remove('open');
  scrim.hidden = true;
  column.inert = false;
  document.getElementById('sidebar-toggle')?.setAttribute('aria-expanded', 'false');
  const back = opener;
  opener = null;
  if (restore && back && document.contains(back)) back.focus();
}

scrim.addEventListener('click', () => closeSidebar());
aside.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && sidebarIsOpen() && !event.defaultPrevented) {
    event.preventDefault();
    closeSidebar();
  }
});
// A window that grows wide again shows the sidebar as a column, so nothing is left half open.
NARROW.addEventListener('change', () => {
  if (!NARROW.matches) closeSidebar({ restore: false });
});

// -- the Settings menu ----------------------------------------------------------------------------------------

// A button that opens a short list of links, with the keyboard behaviour of a menu (Enter, Space or
// an arrow opens it, the arrows, Home and End move, Escape closes it).
function settingsMenu(settings, titles) {
  const button = h(
    'button',
    { type: 'button', id: 'settings-menu', class: 'side-item', 'aria-haspopup': 'menu', 'aria-expanded': 'false', 'aria-controls': 'settings-list' },
    icon('settings', { size: 18 }),
    h('span', { class: 'side-label' }, 'Settings and tools'),
    icon('chevron_down', { size: 14, className: 'side-chevron' }),
  );
  const items = settings.map((name) =>
    viewLink(name, {}, { id: `settings-${name}`, role: 'menuitem', tabindex: '-1', dataset: { page: name } }, icon(SETTINGS_ICONS[name], { size: 16 }), titles[name]),
  );
  const list = h('div', { id: 'settings-list', class: 'menu menu-list', role: 'menu', 'aria-label': 'Settings and tools', hidden: true }, items);
  const wrap = h('div', { class: 'menu-wrap' }, button, list);
  const outside = (event) => {
    if (!wrap.contains(event.target)) close();
  };
  function open(last = false) {
    list.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    document.addEventListener('click', outside, true);
    (last ? items[items.length - 1] : items[0]).focus();
  }
  function close(restore = false) {
    list.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    document.removeEventListener('click', outside, true);
    if (restore) button.focus();
  }
  button.addEventListener('click', () => (list.hidden ? open() : close()));
  button.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      open(event.key === 'ArrowUp');
    } else if (event.key === 'Escape' && !list.hidden) {
      event.preventDefault();
      close(true);
    }
  });
  list.addEventListener('keydown', (event) => {
    const at = items.indexOf(document.activeElement);
    const go = (to) => {
      event.preventDefault();
      items[(to + items.length) % items.length].focus();
    };
    if (event.key === 'ArrowDown') go(at + 1);
    else if (event.key === 'ArrowUp') go(at - 1);
    else if (event.key === 'Home') go(0);
    else if (event.key === 'End') go(items.length - 1);
    else if (event.key === ' ' && at >= 0) {
      event.preventDefault();
      items[at].click();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close(true);
    } else if (event.key === 'Tab') close();
  });
  list.addEventListener('click', () => close());
  wrap.addEventListener('focusout', (event) => {
    if (!list.hidden && !wrap.contains(event.relatedTarget)) close();
  });
  return { wrap, button };
}

// -- building it ---------------------------------------------------------------------------------------------

const initial = (name) => ([...String(name).trim()][0] || '?').toUpperCase();

// The project switcher is a real select: choosing another project loads that project's board.
function projectSwitch(project, projectList) {
  const choose = h(
    'select',
    {
      id: 'project-switcher',
      title: project.name,
      onchange: () => location.assign(choose.value ? `/board/p/${choose.value}#/board` : '/board'),
    },
    projectList.map((one) => h('option', { value: String(one.id), dataset: { hint: roleWords(one.role) } }, one.name)),
    h('option', { value: '' }, 'All projects…'),
  );
  choose.value = String(project.id);
  return h(
    'div',
    { class: 'project-switch', dataset: { selectAnchor: '' } },
    h('span', { class: 'avatar', 'aria-hidden': 'true' }, initial(project.name)),
    h('div', { class: 'project-text' }, h('div', { class: 'project-label' }, h('label', { for: 'project-switcher' }, 'Project'), h('span', { id: 'project-role', class: 'project-role' }, roleWords(project.role))), choose),
    icon('chevron_down', { size: 14, className: 'side-chevron' }),
  );
}

function navLink(page, iconName, title) {
  const count = h('span', { class: 'nav-count', hidden: true });
  const link = viewLink(page, {}, { class: 'side-item', dataset: { page } }, icon(iconName, { size: 18 }), h('span', { class: 'side-label' }, title), count);
  return { link, count };
}

// What is built once for an open project: the pieces that change are filled in by updateSidebar.
export function showSidebar({ session, project, projectList, views, settings, titles, leaveBoard }) {
  const nav = views.map((page) => navLink(page, page, titles[page]));
  const tree = h('ul', { id: 'epic-list', class: 'plain side-tree', role: 'tree', 'aria-label': 'Epics, stories and tasks' });
  const none = h('p', { class: 'quiet side-note', hidden: true }, 'No epics yet.');
  const activityCount = h('span', { id: 'sidebar-activity-count', class: 'nav-count', hidden: true });
  const activity = viewLink('activity', {}, { id: 'activity-open', class: 'side-item', title: 'Who is working on what, and what changed', dataset: { page: 'activity' } }, icon('activity', { size: 18 }), h('span', { class: 'side-label' }, 'Activity'), activityCount);
  const menu = settingsMenu(settings, titles);
  const accountName = h('span', { class: 'account-name' }, session.account.name);
  parts = { nav, tree, none, activity, activityCount, settings: menu.button, accountName };
  put(
    aside,
    h(
      'div',
      { class: 'side-head' },
      h('a', { href: '#/board', class: 'brand', 'aria-label': 'vrum, the Board', onclick: () => closeSidebar({ restore: false }) }, wordmark(30)),
      h('button', { type: 'button', id: 'sidebar-close', class: 'ghost icon-only', 'aria-label': 'Close the menu', onclick: () => closeSidebar() }, icon('close', { size: 18 })),
    ),
    projectSwitch(project, projectList),
    h('nav', { id: 'main-nav', 'aria-label': 'Views' }, nav.map((item) => item.link)),
    h('section', { class: 'side-epics', 'aria-labelledby': 'epics-heading' }, h('h2', { id: 'epics-heading', class: 'side-heading' }, 'Epics'), none, tree),
    h(
      'div',
      { class: 'side-foot' },
      activity,
      menu.wrap,
      h(
        'div',
        { class: 'account-block' },
        viewLink('users', {}, { id: 'account', class: 'account-link', title: `${session.account.email}. Your account.` }, h('span', { class: 'avatar small', 'aria-hidden': 'true' }, icon('person', { size: 16 })), accountName),
        h('button', { type: 'button', id: 'sign-out', class: 'small', onclick: leaveBoard }, 'Sign out'),
      ),
    ),
  );
  aside.hidden = false;
  updateSidebar();
}

export function hideSidebar() {
  closeSidebar({ restore: false });
  aside.hidden = true;
  aside.replaceChildren();
  parts = null;
  epicTree.reset();
  clearTimeout(claimTimer);
}

// The name the person is shown as, after they changed it on the Users page.
export function updateAccount(name) {
  if (parts) parts.accountName.textContent = name;
}

// Closing it after a place is chosen, on a narrow window. A link to a page puts the focus on the page.
aside.addEventListener('click', (event) => {
  if (!NARROW.matches || !sidebarIsOpen()) return;
  if (!event.target.closest('a[href]')) return;
  closeSidebar({ restore: false });
  document.getElementById('page').focus({ preventScroll: true });
});

// -- the navigator -------------------------------------------------------------------------------------------

const epicTree = (() => {
  const opened = new Set(); // refs of the branches that are open
  let revealed = null; // the record whose ancestors were last opened
  let drawn = ''; // what the tree last drew, to draw it again only when that changes

  const nameOf = (node) => `${KIND[node.kind].label} ${node.number}: ${node.title}`;

  // Every epic, and under each story and task by number; a node is listed under its parent.
  function childIndex() {
    const index = new Map();
    for (const node of store.nodes.values()) {
      const key = node.kind === 'epic' ? '' : node.parent;
      if (!index.has(key)) index.set(key, []);
      index.get(key).push(node);
    }
    for (const list of index.values()) list.sort((a, b) => a.number - b.number);
    return index;
  }

  // The epic and story a record is in, from the nearest up (an acceptance criterion is on its story or task).
  function above(ref) {
    const found = describeItem(ref);
    const list = [];
    for (let up = found && found.parent; up; up = store.nodes.get(up)?.parent) list.push(up);
    return list;
  }

  function setOpen(ref, open) {
    if (open) opened.add(ref);
    else opened.delete(ref);
    draw();
  }

  // The rows that can be reached by the arrows: every one drawn (a closed branch draws no rows).
  const reachable = (tree) => [...tree.querySelectorAll('li[role=treeitem]')];

  function focusItem(tree, item) {
    for (const other of tree.querySelectorAll('li[role=treeitem][tabindex="0"]')) other.setAttribute('tabindex', '-1');
    item.setAttribute('tabindex', '0');
    item.focus();
  }

  const TREE_KEYS = new Set(['ArrowDown', 'ArrowUp', 'ArrowRight', 'ArrowLeft', 'Home', 'End', 'Enter', ' ']);

  function walk(event, node, hasKids, open) {
    const here = event.currentTarget;
    // The keys of a link or a button that has the focus keep their own meaning (Enter follows the link).
    if (event.target.closest('li[role=treeitem]') !== here) return;
    if ((event.key === 'Enter' || event.key === ' ') && event.target !== here) return;
    // Only the keys the tree uses are taken; any other (such as the page's [ and ]) goes on to the page.
    if (!TREE_KEYS.has(event.key)) return;
    event.stopPropagation();
    const tree = parts.tree;
    const list = reachable(tree);
    const at = list.indexOf(here);
    const key = event.key;
    if (key === 'ArrowDown') focusItem(tree, list[Math.min(at + 1, list.length - 1)]);
    else if (key === 'ArrowUp') focusItem(tree, list[Math.max(at - 1, 0)]);
    else if (key === 'Home') focusItem(tree, list[0]);
    else if (key === 'End') focusItem(tree, list[list.length - 1]);
    else if (key === 'ArrowRight') {
      if (hasKids && !open) setOpen(node.ref, true);
      else if (hasKids) focusItem(tree, list[at + 1]);
    } else if (key === 'ArrowLeft') {
      if (hasKids && open) setOpen(node.ref, false);
      else {
        const up = here.parentElement.closest('li[role=treeitem]');
        if (up) focusItem(tree, up);
      }
    } else if (key === 'Enter' || key === ' ') {
      openItem(node.ref);
      // On a narrow window the sidebar covers the page, so choosing a record closes it, as a click does.
      if (NARROW.matches) closeSidebar({ restore: false });
    } else return;
    event.preventDefault();
  }

  // One row and, when it is open, its children.
  function branch(node, level, at, size, index, current) {
    const kids = index.get(node.ref) || [];
    const hasKids = kids.length > 0;
    const open = hasKids && opened.has(node.ref);
    const code = nodeCode(node.kind, node.number);
    const link = itemLink(
      node.ref,
      { class: 'tree-link', tabindex: -1, title: `${code} ${node.title}`, 'aria-current': node.ref === current ? 'page' : null, dataset: { ref: node.ref } },
      h('span', { class: 'tree-code' }, code),
      h('span', { class: 'tree-title' }, node.title),
    );
    const twisty = hasKids
      ? h(
          'button',
          { type: 'button', class: 'tree-twisty', tabindex: -1, 'aria-label': `${open ? 'Collapse' : 'Expand'} ${code}`, title: `${open ? 'Hide' : 'Show'} ${node.kind === 'epic' ? 'the stories' : 'the tasks'} of ${code}`, onclick: () => setOpen(node.ref, !open) },
          icon(open ? 'chevron_down' : 'chevron_right', { size: 14 }),
        )
      : h('span', { class: 'tree-twisty none', 'aria-hidden': 'true' });
    return h(
      'li',
      {
        role: 'treeitem',
        class: `tree-item kind-${node.kind}`,
        tabindex: -1,
        'aria-level': level,
        'aria-setsize': size,
        'aria-posinset': at + 1,
        'aria-expanded': hasKids ? String(open) : null,
        'aria-selected': String(node.ref === current),
        'aria-label': nameOf(node),
        dataset: { key: node.ref },
        onkeydown: (event) => walk(event, node, hasKids, open),
      },
      h('div', { class: 'tree-row' }, twisty, link),
      open ? h('ul', { role: 'group', class: 'plain tree-group' }, kids.map((kid, i) => branch(kid, level + 1, i, kids.length, index, current))) : null,
    );
  }

  // The reasons to draw again: what is open, what is there, what is current.
  const signature = (index, current) => {
    const out = [current || ''];
    const walkRows = (list, level) => {
      for (const node of list) {
        const kids = index.get(node.ref) || [];
        out.push(`${level}|${node.ref}|${node.title}|${kids.length > 0}|${opened.has(node.ref)}`);
        if (kids.length > 0 && opened.has(node.ref)) walkRows(kids, level + 1);
      }
    };
    walkRows(index.get('') || [], 1);
    return out.join('\n');
  };

  // Scroll the tree so that the row is in view, without moving anything outside it. When the row
  // is a story or a task, its epic is kept in view too if there is room for both, so the person
  // sees where in the project they are.
  function show(row) {
    const tree = parts.tree;
    const box = tree.getBoundingClientRect();
    const at = row.getBoundingClientRect();
    const epic = row.closest('ul[role=tree] > li[role=treeitem]')?.querySelector(':scope > .tree-row');
    const head = epic && epic !== row ? epic.getBoundingClientRect() : null;
    if (head && at.bottom - head.top <= box.height - 8) tree.scrollTop += head.top - box.top - 4;
    else if (at.top < box.top) tree.scrollTop -= box.top - at.top + 4;
    else if (at.bottom > box.bottom) tree.scrollTop += at.bottom - box.bottom + 4;
  }

  function draw() {
    if (!parts) return;
    const tree = parts.tree;
    const index = childIndex();
    const onItem = ui.page === 'item' ? ui.params.get('item') : null;
    const current = onItem && store.nodes.has(onItem) ? onItem : null;
    let reveal = false;
    if (onItem === null) revealed = null;
    else if (revealed !== onItem && describeItem(onItem)) {
      for (const ref of above(onItem)) opened.add(ref);
      revealed = onItem;
      reveal = true;
    }
    for (const ref of [...opened]) if (!store.nodes.has(ref)) opened.delete(ref);
    parts.none.hidden = (index.get('') || []).length > 0;
    // The links keep the page the person is in (`from`), which changes without the tree changing.
    for (const link of tree.querySelectorAll('a.tree-link')) link.setAttribute('href', itemAddress(link.dataset.ref));
    const now = signature(index, current);
    if (now !== drawn) {
      // Drawing it again keeps the scroll and the focus where they were.
      const heldBy = tree.contains(document.activeElement) ? document.activeElement.closest('li[role=treeitem]')?.dataset.key : null;
      const scroll = tree.scrollTop;
      const roots = index.get('') || [];
      tree.replaceChildren(...roots.map((node, at) => branch(node, 1, at, roots.length, index, current)));
      drawn = now;
      tree.scrollTop = scroll;
      const items = reachable(tree);
      const stop = (heldBy && tree.querySelector(`li[data-key="${heldBy}"]`)) || (current && tree.querySelector(`li[data-key="${current}"]`)) || items[0];
      for (const item of items) item.setAttribute('tabindex', item === stop ? '0' : '-1');
      if (heldBy && stop) stop.focus({ preventScroll: true });
    }
    if (reveal) {
      const near = current || above(onItem)[0];
      const target = near ? tree.querySelector(`li[data-key="${near}"]`) : null;
      if (target) show(target.querySelector(':scope > .tree-row'));
    }
  }

  return {
    draw,
    reset() {
      opened.clear();
      revealed = null;
      drawn = '';
    },
  };
})();

// -- what changes ----------------------------------------------------------------------------------------------

// How much each view holds, or null where it is not known yet.
export function counts() {
  const nodes = [...store.nodes.values()];
  const loaded = store.rules !== null && store.notes !== null;
  const everything = loaded ? nodes.length + (store.rules || []).filter((rule) => rule.status !== 'dropped').length + (store.notes || []).filter(inForce).length : null;
  return {
    board: nodes.filter((node) => node.kind !== 'epic').length,
    graph: everything,
    roadmap: nodes.filter((node) => node.span_start || node.span_end).length,
    lists: everything,
  };
}

const MEANING = {
  board: (n) => `${plural(n, 'story', 'stories')} and tasks on the Board`,
  graph: (n) => `${plural(n, 'work item')} and records in force in the Graph`,
  roadmap: (n) => `${plural(n, 'item')} with dates on the Roadmap`,
  lists: (n) => `${plural(n, 'work item')} and records in force in Lists`,
};

let claimTimer = null;

// The items someone holds now. A claim runs out without any news, so the number is worked out again
// when the next one is due to.
function drawClaims() {
  clearTimeout(claimTimer);
  const now = Date.now();
  const held = [...store.nodes.values()].filter((node) => node.claimed_by && Date.parse(node.claim_expires_at) > now);
  const { activityCount } = parts;
  activityCount.hidden = held.length === 0;
  activityCount.textContent = String(held.length);
  activityCount.title = `${plural(held.length, 'item')} held by someone right now`;
  if (held.length) {
    const next = Math.min(...held.map((node) => Date.parse(node.claim_expires_at)));
    claimTimer = setTimeout(() => updateSidebar(), Math.min(next - now + 1000, 2 ** 30));
  }
}

// Marks where the person is and refreshes the numbers and the navigator. On the page of a record
// the sidebar stays in the page the record was reached from, so that view is the one marked.
export function updateSidebar() {
  if (!parts) return;
  const now = counts();
  const here = ui.page === 'item' ? ui.from : ui.page;
  for (const { link, count } of parts.nav) {
    const page = link.dataset.page;
    const on = page === here;
    link.classList.toggle('current', on);
    if (on) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
    const number = now[page];
    count.hidden = number === null;
    if (number !== null) {
      count.textContent = String(number);
      count.title = MEANING[page](number);
    }
  }
  const settingsOn = SETTINGS.includes(ui.page);
  parts.settings.classList.toggle('current', settingsOn);
  if (settingsOn) parts.settings.setAttribute('aria-current', 'page');
  else parts.settings.removeAttribute('aria-current');
  const activityOn = here === 'activity';
  parts.activity.classList.toggle('current', activityOn);
  if (activityOn) parts.activity.setAttribute('aria-current', 'page');
  else parts.activity.removeAttribute('aria-current');
  drawClaims();
  epicTree.draw();
}
