// The rail of the page of what needs a person: every group the page draws, with the same count, and an entry for each change in it (its
// level in a word, its code and a short title, a sign when it has something to weigh and a tick once it has been viewed), so that a
// person sees the whole list at a glance and goes to any change. Choosing one goes to its card and puts the focus on its header. On a
// window narrower than 1100 pixels the rail is a drop-down instead (the CSS shows one or the other: both are filled here), because a
// column beside the changes would take their room.
//
// The routine moves and the plans that wait for their agent are groups of their own in every way of grouping, collapsed like on the page.

import { h, put } from './dom.js';
import { icon } from './icons.js';
import { LEVEL_WORDS } from './changes.js';
import { viewLink } from './ctx.js';

// A change's short title in the rail: the same title the card has.
const shortTitle = (change) => change.title;

// The word of a level ("Task") before a code that says nothing of it ("Q-13"); a code that already names what it is ("Plan 68",
// "Proposal 64", "Gate 5") needs no word before it.
const levelWord = (change) => (/^[A-Za-z]+ \d/.test(change.code) ? null : (LEVEL_WORDS[change.level] ?? change.level));

// One change of the rail: its level, its code, a short title, a sign when it has something to weigh and a tick once viewed.
function entry(change, isViewed, go) {
  const done = isViewed(change);
  const weighed = Boolean(change.warn && change.warn.total);
  const link = h(
    'a',
    { class: 'rail-change', href: '#/needs', dataset: { change: change.id, key: change.key, level: change.level, viewed: String(done), warning: weighed ? 'true' : null, focusKey: `rail:${change.key}` } },
    levelWord(change) ? h('span', { class: 'rail-level' }, levelWord(change)) : null,
    h('span', { class: 'rail-code item-code' }, change.code),
    h('span', { class: 'rail-title-text', dataset: { words: 'theirs' } }, shortTitle(change)),
    weighed ? h('span', { class: 'rail-warning' }, icon('alert', { size: 14 }), h('span', { class: 'sr-only' }, 'has something to weigh')) : null,
    done ? h('span', { class: 'rail-viewed' }, icon('check', { size: 14 }), h('span', { class: 'sr-only' }, 'viewed')) : null,
  );
  link.addEventListener('click', (event) => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button) return;
    event.preventDefault();
    go(change.key);
  });
  return h('li', {}, link);
}

// A plan that waits for its agent, in the rail: a link to its review page.
function waitingEntry(plan) {
  return h(
    'li',
    {},
    viewLink('review', { change: `plan:${plan.id}` }, { class: 'rail-change', dataset: { plan: plan.id } }, h('span', { class: 'rail-code item-code' }, `Plan ${plan.id}`), h('span', { class: 'rail-title-text', dataset: { words: 'theirs' } }, plan.title)),
  );
}

const head = (section) => h('span', { class: 'rail-group-name' }, icon(section.icon, { size: 14 }), h('span', { class: 'rail-group-title', dataset: { words: section.by === 'topic' ? 'theirs' : null } }, section.title), h('span', { class: 'group-count' }, String(section.count)));

// Fills the rail and the drop-down. `sections` is [{ id, by, title, icon, count, changes, plans?, shut? }] in the page's order: `changes`
// are the changes of a group (the routine moves and the ordinary groups), `plans` the plans that wait for their agent; `shut` says the
// group is collapsed. `isViewed(change)` says whether a tick is drawn; `go(key)` goes to a change; `toggled(id, open)` is told when a
// collapsed group is opened or closed.
export function fillRail(nav, select, sections, { isViewed, go, openOf, toggled }) {
  const total = sections.reduce((sum, one) => sum + (one.plans ? 0 : one.changes.length), 0);
  put(
    nav,
    h('h2', { class: 'rail-title' }, icon('diff', { size: 16 }), 'Changes', h('span', { class: 'heading-count' }, String(total))),
    sections.map((section) => {
      const list = h('ul', { class: 'plain rail-list' }, section.plans ? section.plans.map(waitingEntry) : section.changes.map((change) => entry(change, isViewed, go)));
      if (section.shut) {
        const box = h(
          'details',
          { class: `rail-routine rail-${section.id}`, open: openOf(section.id), ontoggle: () => toggled(section.id, box.open) },
          h('summary', { class: 'rail-routine-head', dataset: { count: String(section.count), focusKey: `rail-${section.id}` }, onclick: () => toggled(section.id, !box.open) }, head(section)),
          list,
        );
        return h('div', { class: 'rail-group', dataset: { by: section.by, group: section.id } }, box);
      }
      return h('div', { class: 'rail-group', dataset: { by: section.by, group: section.id } }, h('p', { class: 'rail-group-head' }, head(section)), list);
    }),
  );
  select.replaceChildren(
    h('option', { value: '' }, `Changes (${total})`),
    ...sections
      .filter((section) => !section.plans)
      .map((section) =>
        h(
          'optgroup',
          { label: `${section.title} (${section.count})` },
          section.changes.map((change) => h('option', { value: change.key }, `${[levelWord(change), change.code, shortTitle(change)].filter(Boolean).join(' ')}${change.warn && change.warn.total ? ' (has something to weigh)' : ''}${isViewed(change) ? ' (viewed)' : ''}`)),
        ),
      ),
  );
  select.value = '';
  select.onchange = () => {
    const key = select.value;
    select.value = '';
    if (key) go(key);
  };
}
