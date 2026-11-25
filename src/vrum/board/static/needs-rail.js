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

// The word of a level ("Task") ONLY when the list is not already grouped by level and the code does not say it already ("Q-13", "S-26" in
// Topic/Decision groups; never "TASK T-35" or "NOTE S-26" inside a Tasks or Notes group).
const levelWord = (change, by) => {
  if (by === 'level') return null;
  if (/^(?:Plan|Proposal|Gate) \d/.test(change.code)) return null;
  if ((change.level === 'story' && change.code.startsWith('S-')) || (change.level === 'task' && change.code.startsWith('T-')) || (change.level === 'epic' && change.code.startsWith('E-'))) return null;
  return LEVEL_WORDS[change.level] ?? change.level;
};

// One change of the rail: its kind icon (so questions, checks, stalled claims and gates look distinct at a glance), its human title, a quiet
// code and optional level word, a sign when it has something to weigh and a tick once viewed.
function entry(change, by, isViewed, go) {
  const done = isViewed(change);
  const weighed = Boolean(change.warn && change.warn.total);
  const level = levelWord(change, by);
  const link = h(
    'a',
    { class: 'rail-change', href: '#/needs', dataset: { change: change.id, key: change.key, kind: change.look, level: change.level, viewed: String(done), warning: weighed ? 'true' : null, focusKey: `rail:${change.key}` } },
    h('span', { class: 'rail-kind-icon', 'aria-hidden': 'true' }, icon(change.icon, { size: 13 })),
    h('span', { class: 'rail-title-text', dataset: { words: 'theirs' } }, shortTitle(change)),
    weighed ? h('span', { class: 'rail-warning' }, icon('alert', { size: 14 }), h('span', { class: 'sr-only' }, 'has something to weigh')) : null,
    done ? h('span', { class: 'rail-viewed' }, icon('check', { size: 14 }), h('span', { class: 'sr-only' }, 'viewed')) : null,
    h('span', { class: 'rail-meta' }, level ? h('span', { class: 'rail-level' }, level) : null, h('span', { class: 'rail-code item-code' }, change.code)),
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
    viewLink(
      'review',
      { change: `plan:${plan.id}` },
      { class: 'rail-change', dataset: { plan: plan.id } },
      h('span', { class: 'rail-kind-icon', 'aria-hidden': 'true' }, icon('plan', { size: 13 })),
      h('span', { class: 'rail-title-text', dataset: { words: 'theirs' } }, plan.title),
      h('span', { class: 'rail-meta' }, h('span', { class: 'rail-code item-code' }, `Plan ${plan.id}`)),
    ),
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
      const list = h('ul', { class: 'plain rail-list' }, section.plans ? section.plans.map(waitingEntry) : section.changes.map((change) => entry(change, section.by, isViewed, go)));
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
          section.changes.map((change) => h('option', { value: change.key }, `${[shortTitle(change), `(${change.code})`].filter(Boolean).join(' ')}${change.warn && change.warn.total ? ' (has something to weigh)' : ''}${isViewed(change) ? ' (viewed)' : ''}`)),
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
