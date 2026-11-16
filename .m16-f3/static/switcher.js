// The quick switcher: one key combination (Ctrl+K, or ⌘K) on every page opens a box that jumps to an
// item by a word of its title or by its id (`12`, `#12`, `node:12`, `rule 3`, `decision:7`, `fact 5`,
// `belief 2`), and to any page. It only navigates: it changes nothing.

import { h } from './dom.js';
import { KIND } from './labels.js';
import { hrefFor, loadTargets, referenceKindOf } from './references.js';
import { store } from './store.js';

const SHOWN = 10;

const clip = (text, size = 70) => {
  const one = String(text).replace(/\s+/g, ' ').trim();
  return one.length > size ? `${one.slice(0, size - 1)}…` : one;
};

function items() {
  const found = [];
  for (const node of store.nodes.values()) found.push({ ref: 'node', id: node.id, type: node.kind, title: node.title });
  for (const rule of store.rules || []) found.push({ ref: 'rule', id: rule.id, type: 'rule', title: rule.title });
  for (const decision of store.decisions || []) found.push({ ref: 'decision', id: decision.id, type: decision.kind, title: decision.title });
  for (const memory of store.memories || []) found.push({ ref: memory.type, id: memory.id, type: memory.type, title: clip(memory.text) });
  return found;
}

// What a typed start finds: pages first, then items. An id finds the item with it, whichever kind.
export function find(query, pages) {
  const typed = query.trim().toLowerCase();
  const pageHits = (words) => pages.filter(([, title]) => words.every((word) => title.toLowerCase().includes(word))).map(([name, title]) => ({ page: name, title }));
  if (typed === '') {
    const newest = items().filter((item) => item.ref === 'node').sort((a, b) => b.id - a.id).slice(0, 6);
    return [...pageHits([]), ...newest].slice(0, SHOWN + pages.length);
  }
  const byRef = /^(node|rule|decision|fact|belief)\s*:?\s*#?(\d+)$/.exec(typed);
  if (byRef) return items().filter((item) => item.ref === byRef[1] && String(item.id) === byRef[2]);
  const byId = /^#?(\d+)$/.exec(typed);
  if (byId) {
    const exact = items().filter((item) => String(item.id) === byId[1]);
    const order = ['node', 'rule', 'decision', 'fact', 'belief'];
    return exact.sort((a, b) => order.indexOf(a.ref) - order.indexOf(b.ref));
  }
  const words = typed.split(/\s+/);
  const matching = items().filter((item) => words.every((word) => `${item.type} ${item.title}`.toLowerCase().includes(word)));
  const order = (item) => (item.title.toLowerCase().startsWith(typed) ? 0 : 1);
  matching.sort((a, b) => order(a) - order(b) || a.ref.localeCompare(b.ref) || b.id - a.id);
  return [...pageHits(words), ...matching].slice(0, SHOWN);
}

const hrefOf = (hit) => (hit.page ? `#/${hit.page}` : hrefFor(referenceKindOf(hit.type), hit.id));

export function openSwitcher(pages) {
  if (document.getElementById('switcher')) return;
  let hits = [];
  let active = 0;
  const input = h('input', { id: 'switcher-input', type: 'text', role: 'combobox', autocomplete: 'off', 'aria-expanded': 'true', 'aria-controls': 'switcher-list', 'aria-label': 'Jump to a page, or to an item by a word of its title or by its id', placeholder: 'A word of a title, an id such as 12 or rule 3, or a page' });
  const list = h('ul', { id: 'switcher-list', class: 'plain switcher-list', role: 'listbox', 'aria-label': 'Where to jump' });
  const box = h(
    'dialog',
    { id: 'switcher', class: 'modal switcher', 'aria-label': 'Jump to' },
    h('h2', {}, 'Jump to'),
    h('p', { class: 'quiet' }, 'Type a word of an item’s title, or its id (12, rule 3, decision 7), or the name of a page. Enter goes there, Escape closes this.'),
    input,
    list,
  );
  box.addEventListener('close', () => box.remove());

  const go = (hit) => {
    box.close();
    const href = hrefOf(hit);
    if (href !== location.hash) location.hash = href;
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
                dataset: hit.page ? { page: hit.page } : { ref: `${hit.ref}:${hit.id}` },
                onmousedown: (event) => {
                  event.preventDefault();
                  go(hit);
                },
              },
              hit.page
                ? h('span', {}, h('span', { class: 'chip' }, 'Page'), ` ${hit.title}`)
                : h('span', {}, h('span', { 'aria-hidden': 'true' }, `${KIND[hit.type]?.sign || ''} `), h('code', {}, hit.ref === 'node' ? `#${hit.id}` : `${hit.ref} ${hit.id}`), ` ${KIND[hit.type]?.label || hit.type}: ${hit.title}`),
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
  // Rules, decisions, facts and beliefs are offered too, as soon as they have been read.
  loadTargets().then(() => {
    if (document.contains(box)) draw();
  }).catch(() => {});
}

// Ctrl+K or ⌘K opens it on every page, while the person is signed in.
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
