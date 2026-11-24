// The bar at the top of the Graph page: what the tabs below look at. It is a search box in the look of the
// filter box of the Board, the Roadmap and Lists, that finds an epic, story, task, acceptance criterion or note by a
// word of its title or by its code (S-12, AC-3, N-7). It has no filter language. A short
// list of what it finds hangs under the box from the body, so no column and no edge of the window cuts it
// off. With the box clicked and empty the list is the epics, so there is a way to browse. The item chosen is
// shown in the box (its icon, code and title), with a small x to look at the whole project again. Beside the
// box, the way back through the items looked at (Back and the trail); under it, only when the address names
// something that is not there, a line that says so. Choosing only says so (`choose(ref)`, `clear()`); what
// the page does about it is the page's business.
//
// The box is a combobox that opens only on the person's action (a click, typing, Down or Enter): the arrow
// keys move through the list, Enter chooses, Escape closes it (and then
// clears what was typed), Tab leaves it.

import { announce, h, plural, put } from './dom.js';
import { named, resolve } from './graph-model.js';
import { icon } from './icons.js';
import { REQUIREMENT_STATUS, STATUS, inForce, isNodeKind, itemCode, kindIcon, kindLabel, noteStatusWords } from './labels.js';
import { store } from './store.js';
import { findItems } from './switcher.js';

const PLACEHOLDER = 'Find an epic, story, task, acceptance criterion or note to look at';
const MOST_FOUND = 10; // what a typed word shows
const MOST_EPICS = 30; // what the empty box lists
const GAP = 4; // between the box and its list
const EDGE = 8; // the list keeps this far from the edge of the window
const LEAST_ROOM = 96; // a list never gets smaller than this, whatever the keyboard leaves

// The word that says an item is no longer live, or null: done or dropped work, a dropped acceptance criterion,
// a replaced or withdrawn note.
function endedWord(kind, record) {
  if (!record) return null;
  if (isNodeKind(kind)) return record.status === 'done' || record.status === 'dropped' ? STATUS[record.status].label : null;
  if (kind === 'rule') return record.status === 'dropped' ? REQUIREMENT_STATUS.dropped.label : null;
  return inForce(record) ? null : noteStatusWords(record.status);
}

// What tells two items of one name apart: the story or epic a piece of work is under, the item a
// criterion is placed on.
function contextOf(kind, record) {
  const holder = isNodeKind(kind) ? record?.parent : kind === 'rule' ? record?.node : null;
  return (holder && store.nodes.get(holder)?.title) || null;
}

// One step of the trail: its code, then its title, which is cut with an ellipsis when it is long.
const trailStep = (entry) => [h('span', { class: 'graph-code' }, entry.code), h('span', { class: 'graph-trail-name' }, entry.title)];

function entryOf({ kind, number, title }) {
  const ref = `${kind}:${number}`;
  const found = resolve(ref);
  return { ref, kind, number, code: itemCode(kind, number), title: found ? found.title : title, context: contextOf(kind, found?.record), ended: endedWord(kind, found?.record) };
}

const epicEntries = () =>
  [...store.nodes.values()]
    .filter((node) => node.kind === 'epic')
    .sort((a, b) => a.number - b.number)
    .map((node) => entryOf({ kind: 'epic', number: node.number, title: node.title }));

export function createFocusBar({ choose, clear, back, jump }) {
  let chosenRef = null; // the item being looked at
  let isOpen = false;
  let entries = []; // what the list shows
  let active = 0; // which of them the arrow keys are on
  let frame = 0;
  const touch = matchMedia('(pointer: coarse)');

  const input = h('input', {
    type: 'text',
    id: 'graph-find-input',
    class: 'graph-find-input',
    role: 'combobox',
    'aria-autocomplete': 'list',
    'aria-haspopup': 'listbox',
    'aria-expanded': 'false',
    'aria-controls': 'graph-find-list',
    'aria-labelledby': 'graph-find-label',
    'aria-describedby': 'graph-find-now',
    autocomplete: 'off',
    autocapitalize: 'off',
    autocorrect: 'off',
    spellcheck: 'false',
    enterkeyhint: 'go',
    placeholder: PLACEHOLDER,
  });
  const kindSlot = h('span', { class: 'graph-find-kind' });
  const lead = h('span', { class: 'graph-find-lead', 'aria-hidden': 'true' }, icon('search', { size: 16 }), kindSlot);
  const shown = h('span', { class: 'graph-find-chosen', id: 'graph-find-chosen', 'aria-hidden': 'true', hidden: true });
  const stop = h('button', { type: 'button', id: 'graph-focus-clear', class: 'graph-find-clear', hidden: true, onmousedown: (event) => event.preventDefault(), onclick: () => stopClicked() }, icon('close', { size: 14 }));
  const now = h('span', { id: 'graph-find-now', class: 'sr-only' });
  const field = h('span', { class: 'filter-field graph-find-field' }, lead, input, shown, stop);
  const form = h(
    'form',
    { id: 'graph-find', class: 'filter graph-find', role: 'search', onsubmit: (event) => event.preventDefault() },
    h('label', { id: 'graph-find-label', for: 'graph-find-input', class: 'sr-only' }, 'Find an item to look at'),
    now,
    field,
  );
  const heading = h('div', { class: 'graph-find-heading', id: 'graph-find-heading' });
  const list = h('div', { class: 'graph-find-list', id: 'graph-find-list', role: 'listbox', 'aria-labelledby': 'graph-find-heading' });
  const none = h('p', { class: 'graph-find-none', id: 'graph-find-none' });
  const foot = h('p', { class: 'graph-find-foot', id: 'graph-find-foot' });
  const popup = h('div', { class: 'graph-find-popup', id: 'graph-find-popup', onmousedown: (event) => event.preventDefault() }, heading, list, none, foot);
  const problem = h('p', { class: 'problem graph-find-problem', id: 'graph-focus-problem', role: 'status', hidden: true });
  const trail = h('div', { class: 'graph-focus-trail' });
  const element = h('section', { id: 'graph-focus', class: 'graph-focus', 'aria-label': 'What to look at' }, h('div', { class: 'graph-focus-row' }, form, trail, problem));

  // -- the list -------------------------------------------------------------------------------------

  // What the list holds for what is in the box: the epics when it is empty, otherwise what the words find.
  function found() {
    const typed = input.value.trim();
    if (!typed) {
      const epics = epicEntries();
      return { heading: epics.length ? 'Epics' : '', entries: epics.slice(0, MOST_EPICS), total: epics.length, nothing: 'There are no epics yet. Add one on the Board.', more: 'Type a word or a code to find the rest.' };
    }
    const { items } = findItems(typed);
    return {
      heading: items.length ? plural(items.length, 'match', 'matches') : '',
      entries: items.slice(0, MOST_FOUND).map(entryOf),
      total: items.length,
      nothing: `Nothing matches "${typed}". Try a word of the title, or a code such as S-12, AC-3 or N-7.`,
      more: 'Type more to narrow it.',
    };
  }

  function optionFor(entry, at) {
    const done = entry.ended;
    return h(
      'div',
      {
        id: `graph-find-option-${at}`,
        role: 'option',
        class: `graph-find-option${done ? ' is-ended' : ''}`,
        'aria-selected': 'false',
        dataset: { ref: entry.ref, chosen: entry.ref === chosenRef ? 'true' : null },
        onmousedown: (event) => event.preventDefault(),
        onclick: () => pick(entry),
        onmousemove: () => setActive(at, { scroll: false }),
      },
      kindIcon(entry.kind, 16),
      h('span', { class: 'graph-code graph-find-code' }, entry.code),
      h('span', { class: 'graph-find-title', title: entry.title }, entry.title),
      entry.context ? h('span', { class: 'graph-find-context', title: entry.context }, entry.context) : null,
      done ? h('span', { class: 'graph-find-ended' }, done) : null,
      entry.ref === chosenRef ? h('span', { class: 'graph-find-check' }, icon('check', { size: 14 })) : null,
    );
  }

  function draw({ first = false } = {}) {
    const result = found();
    entries = result.entries;
    if (first) active = Math.max(0, entries.findIndex((entry) => entry.ref === chosenRef));
    active = Math.min(active, Math.max(0, entries.length - 1));
    heading.textContent = result.heading;
    heading.hidden = !entries.length;
    list.hidden = !entries.length;
    put(list, entries.map(optionFor));
    none.textContent = entries.length ? '' : result.nothing;
    none.hidden = Boolean(entries.length);
    foot.textContent = result.total > entries.length ? `Showing the first ${entries.length} of ${result.total}. ${result.more}` : '';
    foot.hidden = !foot.textContent;
    setActive(active, { scroll: true });
    if (input.value.trim()) announce(entries.length ? `${plural(result.total, 'match', 'matches')}` : 'Nothing matches');
  }

  function setActive(at, { scroll }) {
    active = at;
    [...list.children].forEach((option, index) => {
      const on = index === at;
      option.setAttribute('aria-selected', String(on));
      if (on) option.dataset.active = 'true';
      else delete option.dataset.active;
      if (on && scroll) option.scrollIntoView({ block: 'nearest' });
    });
    if (isOpen && entries.length) input.setAttribute('aria-activedescendant', `graph-find-option-${at}`);
    else input.removeAttribute('aria-activedescendant');
  }

  // Below the box when the list fits there (or when there is more room below than above), otherwise above, never
  // outside what the person can see (the on-screen keyboard takes room on a phone), and a list taller than the
  // room scrolls inside itself.
  function place() {
    if (!isOpen) return;
    const box = field.getBoundingClientRect();
    const view = window.visualViewport;
    const [left, top, width, height] = view ? [view.offsetLeft, view.offsetTop, view.width, view.height] : [0, 0, document.documentElement.clientWidth, document.documentElement.clientHeight];
    // A box that has been scrolled out of sight takes its list with it.
    if (box.bottom < top || box.top > top + height) {
      closeList();
      return;
    }
    const kept = list.scrollTop;
    popup.style.maxHeight = '';
    popup.style.width = `${Math.round(Math.min(Math.max(box.width, 288), width - 2 * EDGE))}px`;
    const natural = popup.offsetHeight;
    const below = top + height - box.bottom - GAP - EDGE;
    const above = box.top - top - GAP - EDGE;
    const under = natural <= below || below >= above;
    const room = Math.max(LEAST_ROOM, under ? below : above);
    if (natural > room) popup.style.maxHeight = `${Math.round(room)}px`;
    list.scrollTop = kept;
    const used = Math.min(natural, room);
    popup.style.top = `${Math.round(under ? box.bottom + GAP : box.top - GAP - used)}px`;
    popup.style.left = `${Math.round(Math.min(Math.max(box.left, left + EDGE), Math.max(left + EDGE, left + width - popup.offsetWidth - EDGE)))}px`;
    popup.dataset.placement = under ? 'below' : 'above';
  }

  // The list follows its box when the page moves or changes size, but not when the list itself scrolls.
  const follow = (event) => {
    if (event?.target instanceof Node && popup.contains(event.target)) return;
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(place);
  };

  function openList({ first = false } = {}) {
    if (!isOpen) {
      isOpen = true;
      input.setAttribute('aria-expanded', 'true');
      document.body.append(popup);
      window.addEventListener('resize', follow);
      window.addEventListener('scroll', follow, true);
      window.visualViewport?.addEventListener('resize', follow);
      window.visualViewport?.addEventListener('scroll', follow);
    }
    draw({ first });
    place();
  }

  function closeList() {
    if (!isOpen) return;
    isOpen = false;
    cancelAnimationFrame(frame);
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    popup.remove();
    window.removeEventListener('resize', follow);
    window.removeEventListener('scroll', follow, true);
    window.visualViewport?.removeEventListener('resize', follow);
    window.visualViewport?.removeEventListener('scroll', follow);
  }

  // -- what the box does ------------------------------------------------------------------------------

  // The box shows the chosen item until something is typed, and the x is there while there is anything to clear.
  function sync() {
    form.classList.toggle('is-typing', input.value !== '');
    stop.hidden = !(chosenRef || input.value || form.classList.contains('is-missing'));
    stop.setAttribute('aria-label', input.value ? 'Clear what you typed' : chosenRef ? `Stop looking at ${itemName(chosenRef)}. Look at the whole project` : 'Look at the whole project');
    stop.title = input.value ? 'Clear what you typed' : 'Look at the whole project';
  }

  const itemName = (ref) => {
    const item = resolve(ref);
    return item ? `${kindLabel(item.kind)} ${named(item)}` : 'the item';
  };

  function pick(entry) {
    closeList();
    input.value = '';
    sync();
    // A phone's keyboard would cover the page that has just changed.
    if (touch.matches) input.blur();
    choose(entry.ref);
  }

  function stopClicked() {
    if (input.value) {
      input.value = '';
      sync();
      if (isOpen) draw();
      input.focus();
      return;
    }
    closeList();
    clear();
  }

  function move(step) {
    if (!entries.length) return;
    setActive((active + step + entries.length) % entries.length, { scroll: true });
  }

  // The list opens when the person clicks the box, types, or presses Down or Enter in it, never because the box
  // was given the keyboard (a Tab, or the page handing it back after a change).
  input.addEventListener('click', () => {
    if (!isOpen) openList({ first: !input.value });
  });
  input.addEventListener('input', () => {
    active = 0;
    sync();
    openList();
  });
  input.addEventListener('blur', () => closeList());
  // What was typed is forgotten when the focus leaves the box for good, not when it only moves on to the x.
  form.addEventListener('focusout', (event) => {
    if (form.contains(event.relatedTarget)) return;
    closeList();
    if (input.value) {
      input.value = '';
      sync();
    }
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!isOpen) openList({ first: !input.value });
      else move(event.key === 'ArrowDown' ? 1 : -1);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (!isOpen) openList({ first: !input.value });
      else if (entries[active]) pick(entries[active]);
    } else if (event.key === 'Escape') {
      if (isOpen) {
        event.preventDefault();
        event.stopPropagation();
        closeList();
      } else if (input.value) {
        event.preventDefault();
        event.stopPropagation();
        input.value = '';
        sync();
      }
    }
  });

  // -- what the page tells it -------------------------------------------------------------------------

  // `item` is the chosen item, or null; `path` the items looked at, oldest first, the last one the
  // chosen; `missing` says an address named something that is not there.
  function update({ item, path, missing }) {
    const holding = [stop, ...trail.querySelectorAll('button')].some((one) => one === document.activeElement);
    chosenRef = item ? item.ref : null;
    form.classList.toggle('has-item', Boolean(item));
    form.classList.toggle('is-missing', missing);
    put(kindSlot, item ? kindIcon(item.kind, 16) : missing ? icon('alert', { size: 16 }) : null);
    put(shown, item ? [h('span', { class: 'graph-code' }, item.code), h('span', { class: 'graph-find-chosen-title' }, item.title)] : missing ? h('span', { class: 'graph-find-chosen-title' }, 'Not found') : null);
    shown.hidden = !(item || missing);
    if (item) shown.dataset.ref = item.ref;
    else delete shown.dataset.ref;
    // The tooltip is on the field, so that the pointer anywhere over the box (the words drawn over the input are cut
    // short in a narrow window) is told the whole name.
    field.title = item ? named(item) : '';
    now.textContent = item ? `Looking at ${itemName(item.ref)}.` : missing ? 'What the address names was not found.' : 'Looking at the whole project.';
    sync();
    put(
      trail,
      path.length > 1
        ? h(
            'nav',
            { class: 'graph-trail', id: 'graph-trail', 'aria-label': 'Where you have been' },
            h('button', { type: 'button', class: 'small', id: 'graph-back', onclick: back }, icon('arrow_left', { size: 14 }), 'Back'),
            h(
              'ol',
              { class: 'plain' },
              path.map((entry, index) =>
                h('li', {}, index === path.length - 1 ? h('span', { class: 'graph-trail-here', title: named(entry), 'aria-current': 'true' }, trailStep(entry)) : h('button', { type: 'button', class: 'link', title: named(entry), onclick: () => jump(index) }, trailStep(entry))),
              ),
            ),
          )
        : null,
    );
    problem.textContent = missing ? 'There is nothing with that name. Find something else, or clear the box to look at the whole project.' : '';
    problem.hidden = !missing;
    if (isOpen) draw();
    // What had the focus is gone (the x, a Back with nothing more to go back to): the box takes it.
    if (holding && ((stop.hidden && document.activeElement === stop) || !element.contains(document.activeElement))) input.focus({ preventScroll: true });
  }

  function destroy() {
    closeList();
  }

  return { element, update, input, destroy };
}
