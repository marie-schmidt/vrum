// The quick switcher: one key combination (Ctrl+K, or ⌘K) on every view opens a box that jumps to
// an item by a word of its title or by its code (`S-12`, `story 12`, `story:12`, `criterion 3`,
// `note:7`, `note 7`; a bare number finds every kind that has it), and to any view, Search,
// Activity, the coverage report and the settings pages. It only navigates: it changes nothing.
// Every place it goes to is a page: an item opens its own page, in the page the person is in.

import { openItem } from './ctx.js';
import { h } from './dom.js';
import { icon } from './icons.js';
import { isNodeKind, itemCode, kindIcon, kindLabel } from './labels.js';
import { ensureContext, headlineOf, store } from './store.js';

const SHOWN = 10;
const ORDER = ['epic', 'story', 'task', 'rule', 'note'];
const LETTERS = { e: 'epic', s: 'story', t: 'task' };
// The sign each view has in the sidebar and the Settings menu.
const VIEW_ICONS = { board: 'board', graph: 'graph', roadmap: 'roadmap', lists: 'lists', search: 'search', activity: 'activity', coverage: 'rule', users: 'person', definition: 'checklist', tools: 'settings', status: 'info' };

const clip = (text, size = 70) => {
  const one = String(text).replace(/\s+/g, ' ').trim();
  return one.length > size ? `${one.slice(0, size - 1)}…` : one;
};

// Every item the switcher can go to: { kind, number, title }, the number being the id of a
// acceptance criterion or a note.
function items() {
  const found = [];
  for (const node of store.nodes.values()) found.push({ kind: node.kind, number: node.number, title: node.title, created: node.created_at || '' });
  for (const rule of store.rules || []) found.push({ kind: 'rule', number: rule.id, title: rule.title });
  for (const note of store.notes || []) found.push({ kind: 'note', number: note.id, title: clip(headlineOf(note)), record: note });
  return found;
}

// What a typed start finds among the items (never the views). A code finds the item with it (S-12,
// story 12, criterion 3); a bare number finds the items of every kind that has it; anything else is
// words that must all be in the kind's name, the code or the title. `exact` says it was a code or a
// number. Used by the
// quick switcher and by the Graph's box for choosing what to look at.
export function findItems(query) {
  const typed = query.trim().toLowerCase();
  const byCode = /^(epic|story|task|rule|acceptance criterion|criterion|requirement|note|[est])\s*[:#-]?\s*#?(\d+)$/.exec(typed);
  if (byCode) {
    const word = byCode[1];
    const kind = LETTERS[word] || (['requirement', 'criterion', 'acceptance criterion'].includes(word) ? 'rule' : word);
    return { exact: true, items: items().filter((item) => item.kind === kind && String(item.number) === byCode[2]) };
  }
  const byNumber = /^#?(\d+)$/.exec(typed);
  if (byNumber) {
    const exact = items().filter((item) => String(item.number) === byNumber[1]);
    return { exact: true, items: exact.sort((a, b) => ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind)) };
  }
  const words = typed.split(/\s+/);
  const matching = items().filter((item) => words.every((word) => `${kindLabel(item.kind)} ${itemCode(item.kind, item.number)} ${item.title}`.toLowerCase().includes(word)));
  const order = (item) => (item.title.toLowerCase().startsWith(typed) ? 0 : 1);
  matching.sort((a, b) => order(a) - order(b) || ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind) || b.number - a.number);
  return { exact: false, items: matching };
}

// What a typed start finds: views first, then items. `pages` is a list of { name, title }.
export function find(query, pages) {
  const typed = query.trim().toLowerCase();
  const pageHits = (words) => pages.filter(({ title }) => words.every((word) => title.toLowerCase().includes(word))).map(({ name, title }) => ({ page: name, title }));
  if (typed === '') {
    const newest = items().filter((item) => isNodeKind(item.kind)).sort((a, b) => b.created.localeCompare(a.created) || b.number - a.number).slice(0, 6);
    return [...pageHits([]), ...newest].slice(0, SHOWN + pages.length);
  }
  const { exact, items: found } = findItems(typed);
  if (exact) return found;
  return [...pageHits(typed.split(/\s+/)), ...found].slice(0, SHOWN);
}

export function openSwitcher(pages) {
  if (document.getElementById('switcher')) return;
  let hits = [];
  let active = 0;
  const input = h('input', { id: 'switcher-input', type: 'text', role: 'combobox', autocomplete: 'off', 'aria-expanded': 'true', 'aria-controls': 'switcher-list', 'aria-label': 'Jump to a view, or to an item by a word of its title or by its code', placeholder: 'A word of a title, a code such as S-12 or criterion 3, or a view' });
  const list = h('ul', { id: 'switcher-list', class: 'plain switcher-list', role: 'listbox', 'aria-label': 'Where to jump' });
  const box = h(
    'dialog',
    { id: 'switcher', class: 'modal switcher', 'aria-label': 'Jump to' },
    h('h2', {}, 'Jump to'),
    input,
    list,
  );
  box.addEventListener('close', () => box.remove());

  const go = (hit) => {
    box.close();
    if (hit.page) {
      const spec = pages.find(({ name }) => name === hit.page);
      spec.go();
    } else {
      openItem(`${hit.kind}:${hit.number}`);
    }
  };
  const draw = () => {
    hits = find(input.value, pages);
    active = Math.min(active, Math.max(0, hits.length - 1));
    list.replaceChildren(
      ...(hits.length
        ? hits.map((hit, at) =>
            h(
              'li',
              {
                id: `switcher-option-${at}`,
                role: 'option',
                class: 'switcher-option',
                'aria-selected': String(at === active),
                dataset: hit.page ? { page: hit.page } : { ref: `${hit.kind}:${hit.number}` },
                onmousedown: (event) => {
                  event.preventDefault();
                  go(hit);
                },
              },
              hit.page
                ? h('span', { class: 'switcher-item' }, icon(VIEW_ICONS[hit.page] || 'info', { size: 14 }), ` ${hit.title}`)
                : h('span', { class: 'switcher-item' }, kindIcon(hit.kind, 14), h('code', {}, itemCode(hit.kind, hit.number)), ` ${isNodeKind(hit.kind) ? '' : `${kindLabel(hit.kind)}: `}${hit.title}`),
            ),
          )
        : [h('li', { class: 'quiet', role: 'presentation' }, 'Nothing matches.')]),
    );
    if (hits.length) input.setAttribute('aria-activedescendant', `switcher-option-${active}`);
    else input.removeAttribute('aria-activedescendant');
  };
  input.addEventListener('input', () => {
    active = 0;
    draw();
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (hits.length) active = (active + (event.key === 'ArrowDown' ? 1 : -1) + hits.length) % hits.length;
      draw();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (hits[active]) go(hits[active]);
    }
  });
  document.getElementById('dialogs').append(box);
  box.showModal();
  draw();
  input.focus();
  // Acceptance criteria and notes are offered too, as soon as they have been read.
  ensureContext().then(() => {
    if (document.contains(box)) draw();
  }).catch(() => {});
}

// Ctrl+K or ⌘K opens it on every view, while the person is signed in.
export function keepSwitcher(pages, running) {
  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'k') {
      if (!running()) return;
      event.preventDefault();
      openSwitcher(pages);
    }
  });
}
