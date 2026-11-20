// The pick-list a text box offers when a person types `[[`: items to refer to, narrowed as they
// type (by code, as in S-12 or story:12, by a number, or by a word of the title), chosen with the
// arrow keys and Enter or with the mouse, and written into the text as `[[story:12]]`. It only
// writes text. A reference creates no link, so choosing one changes nothing in the graph until the
// text is saved.

import { h } from './dom.js';
import { isNodeKind, itemCode, kindIcon, kindLabel } from './labels.js';
import { loadTargets, refText } from './references.js';
import { headlineOf, store } from './store.js';

const SHOWN = 8;
let counter = 0;
const KINDS = ['epic', 'story', 'task', 'rule', 'note'];
const LETTERS = { e: 'epic', s: 'story', t: 'task' };

const clip = (text, size = 70) => {
  const one = String(text).replace(/\s+/g, ' ').trim();
  return one.length > size ? `${one.slice(0, size - 1)}…` : one;
};

// Every item a reference can name: { kind, number, title }, the number being the id for a
// acceptance criterion or a note.
function everything() {
  const found = [];
  for (const node of store.nodes.values()) found.push({ kind: node.kind, number: node.number, title: node.title, created: node.created_at || '' });
  for (const rule of store.rules || []) found.push({ kind: 'rule', number: rule.id, title: rule.title });
  for (const note of store.notes || []) found.push({ kind: 'note', number: note.id, title: clip(headlineOf(note)) });
  return found;
}

// The items a typed start offers: nothing typed gives the newest epics, stories and tasks, `rule:`,
// `story 12` or `S-12` narrows by kind and number, a number is a number of any kind, and words
// must all be in the title.
export function offers(query) {
  const typed = query.trim().toLowerCase();
  const all = everything();
  const byKind = /^(epic|story|task|rule|note)\s*:?\s*#?(\d*)$/.exec(typed);
  const byCode = /^([est])(?:-(\d*)|(\d+))$/.exec(typed);
  let found;
  let digits = '';
  if (typed === '') found = all.filter((item) => isNodeKind(item.kind)).sort((a, b) => b.created.localeCompare(a.created) || b.number - a.number);
  else if (byKind || byCode) {
    const kind = byKind ? byKind[1] : LETTERS[byCode[1]];
    digits = byKind ? byKind[2] : byCode[2] ?? byCode[3];
    found = all.filter((item) => item.kind === kind && String(item.number).startsWith(digits));
  } else if (/^#?\d+$/.test(typed)) {
    digits = typed.replace('#', '');
    found = all.filter((item) => String(item.number).startsWith(digits));
  } else {
    const words = typed.split(/\s+/);
    found = all.filter((item) => {
      const hay = `${kindLabel(item.kind)} ${item.kind} ${itemCode(item.kind, item.number)} ${item.title}`.toLowerCase();
      return words.every((word) => hay.includes(word));
    });
  }
  const exact = (item) => (digits !== '' && String(item.number) === digits ? 0 : 1);
  return found.sort((a, b) => exact(a) - exact(b) || KINDS.indexOf(a.kind) - KINDS.indexOf(b.kind) || a.title.length - b.title.length).slice(0, SHOWN);
}

// Gives a text box (a textarea or an input) the pick-list. Returns the box.
export function withReferences(control) {
  const list = h('ul', { class: 'ref-picker', role: 'listbox', hidden: true, id: `ref-picker-${(counter += 1)}`, 'aria-label': 'Items to refer to' });
  let options = [];
  let active = -1;
  let from = -1;
  let asked = false;
  control.setAttribute('aria-autocomplete', 'list');
  control.setAttribute('aria-controls', list.id);
  control.setAttribute('aria-expanded', 'false');

  const hide = () => {
    list.hidden = true;
    active = -1;
    control.setAttribute('aria-expanded', 'false');
    control.removeAttribute('aria-activedescendant');
  };
  const mark = () => {
    [...list.children].forEach((li, at) => li.setAttribute('aria-selected', String(at === active)));
    if (active >= 0) control.setAttribute('aria-activedescendant', `${list.id}-${active}`);
    else control.removeAttribute('aria-activedescendant');
  };
  const choose = (at) => {
    const item = options[at];
    if (!item) return;
    const caret = control.selectionStart;
    const text = refText(item.kind, item.number);
    control.value = control.value.slice(0, from) + text + control.value.slice(caret);
    const end = from + text.length;
    control.setSelectionRange(end, end);
    hide();
    control.dispatchEvent(new Event('input', { bubbles: true }));
    control.focus();
  };
  const open = () => {
    const before = control.value.slice(0, control.selectionStart);
    const start = before.lastIndexOf('[[');
    const typed = start < 0 ? '' : before.slice(start);
    if (start < 0 || typed.includes(']]') || typed.includes('\n')) return hide();
    // The acceptance criteria and notes are read the first time a `[[` is typed, and the
    // list is drawn again with them.
    if (!asked) {
      asked = true;
      loadTargets()
        .then(() => {
          if (document.activeElement === control) open();
        })
        .catch(() => {
          asked = false;
        });
    }
    from = start;
    options = offers(typed.slice(2));
    if (!options.length) return hide();
    list.replaceChildren(
      ...options.map((item, at) =>
        h(
          'li',
          {
            id: `${list.id}-${at}`,
            role: 'option',
            class: 'ref-option',
            dataset: { ref: refText(item.kind, item.number) },
            // A press, so that the box does not lose its focus before the choice is made.
            onmousedown: (event) => {
              event.preventDefault();
              choose(at);
            },
          },
          kindIcon(item.kind, 14),
          h('code', {}, refText(item.kind, item.number)),
          h('span', { class: 'ref-title' }, item.title),
        ),
      ),
    );
    if (!list.isConnected) control.after(list);
    list.hidden = false;
    active = 0;
    control.setAttribute('aria-expanded', 'true');
    mark();
  };

  control.addEventListener('input', open);
  control.addEventListener('click', open);
  control.addEventListener('blur', () => setTimeout(hide, 120));
  control.addEventListener('keyup', (event) => {
    if (!['ArrowUp', 'ArrowDown', 'Enter', 'Escape', 'Tab'].includes(event.key)) open();
  });
  control.addEventListener('keydown', (event) => {
    if (list.hidden) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      active = (active + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
      mark();
    } else if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault();
      choose(active < 0 ? 0 : active);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      hide();
    }
  });
  return control;
}
