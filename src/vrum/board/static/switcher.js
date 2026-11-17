// The quick switcher: one key combination (Ctrl+K, or ⌘K) on every view opens a box that jumps to
// an item by a word of its title or by its code (`S-12`, `story 12`, `story:12`, `requirement 3`,
// `decision:7`, `fact 5`; a bare number finds every kind that has it), and to any view, the search
// and the activity drawers, and the settings pages. It only navigates: it changes nothing. An item
// opens in the detail panel (`sel=`) of the view the person is on.

import { ui } from './ctx.js';
import { h } from './dom.js';
import { KIND, isNodeKind, itemCode, kindIcon } from './labels.js';
import { ensureContext, store } from './store.js';

const SHOWN = 10;
const ORDER = ['epic', 'story', 'task', 'rule', 'decision', 'fact'];
const LETTERS = { e: 'epic', s: 'story', t: 'task' };

const clip = (text, size = 70) => {
  const one = String(text).replace(/\s+/g, ' ').trim();
  return one.length > size ? `${one.slice(0, size - 1)}…` : one;
};

// Every item the switcher can go to: { kind, number, title }, the number being the id of a
// requirement, a decision or a fact.
function items() {
  const found = [];
  for (const node of store.nodes.values()) found.push({ kind: node.kind, number: node.number, title: node.title, created: node.created_at || '' });
  for (const rule of store.rules || []) found.push({ kind: 'rule', number: rule.id, title: rule.title });
  for (const decision of store.decisions || []) found.push({ kind: 'decision', number: decision.id, title: decision.title });
  for (const fact of store.facts || []) found.push({ kind: 'fact', number: fact.id, title: clip(fact.text) });
  return found;
}

// What a typed start finds: pages first, then items. A code finds the item with it (S-12, story 12,
// requirement 3); a bare number finds the items of every kind that has it. `pages` is a list of
// { name, title }.
export function find(query, pages) {
  const typed = query.trim().toLowerCase();
  const pageHits = (words) => pages.filter(({ title }) => words.every((word) => title.toLowerCase().includes(word))).map(({ name, title }) => ({ page: name, title }));
  if (typed === '') {
    const newest = items().filter((item) => isNodeKind(item.kind)).sort((a, b) => b.created.localeCompare(a.created) || b.number - a.number).slice(0, 6);
    return [...pageHits([]), ...newest].slice(0, SHOWN + pages.length);
  }
  const byCode = /^(epic|story|task|rule|requirement|decision|fact|[est])\s*[:#-]?\s*#?(\d+)$/.exec(typed);
  if (byCode) {
    const word = byCode[1];
    const kind = LETTERS[word] || (word === 'requirement' ? 'rule' : word);
    return items().filter((item) => item.kind === kind && String(item.number) === byCode[2]);
  }
  const byNumber = /^#?(\d+)$/.exec(typed);
  if (byNumber) {
    const exact = items().filter((item) => String(item.number) === byNumber[1]);
    return exact.sort((a, b) => ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind));
  }
  const words = typed.split(/\s+/);
  const matching = items().filter((item) => words.every((word) => `${KIND[item.kind].label} ${itemCode(item.kind, item.number)} ${item.title}`.toLowerCase().includes(word)));
  const order = (item) => (item.title.toLowerCase().startsWith(typed) ? 0 : 1);
  matching.sort((a, b) => order(a) - order(b) || ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind) || b.number - a.number);
  return [...pageHits(words), ...matching].slice(0, SHOWN);
}

export function openSwitcher(pages) {
  if (document.getElementById('switcher')) return;
  let hits = [];
  let active = 0;
  const input = h('input', { id: 'switcher-input', type: 'text', role: 'combobox', autocomplete: 'off', 'aria-expanded': 'true', 'aria-controls': 'switcher-list', 'aria-label': 'Jump to a view, or to an item by a word of its title or by its code', placeholder: 'A word of a title, a code such as S-12 or requirement 3, or a view' });
  const list = h('ul', { id: 'switcher-list', class: 'plain switcher-list', role: 'listbox', 'aria-label': 'Where to jump' });
  const box = h(
    'dialog',
    { id: 'switcher', class: 'modal switcher', 'aria-label': 'Jump to' },
    h('h2', {}, 'Jump to'),
    h('p', { class: 'quiet' }, 'Type a word of an item’s title, or its code (S-12, T-40, requirement 3, decision 7, fact 5), or the name of a view. Enter goes there, Escape closes this.'),
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
      ui.navigate({ sel: `${hit.kind}:${hit.number}`, panel: null });
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
                ? h('span', { class: 'switcher-item' }, h('span', { class: 'chip' }, 'View'), ` ${hit.title}`)
                : h('span', { class: 'switcher-item' }, kindIcon(hit.kind, 14), h('code', {}, isNodeKind(hit.kind) ? itemCode(hit.kind, hit.number) : `${KIND[hit.kind].label.toLowerCase()} ${hit.number}`), ` ${KIND[hit.kind].label}: ${hit.title}`),
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
  // Requirements, decisions and facts are offered too, as soon as they have been read.
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
  return () => openSwitcher(pages);
}
