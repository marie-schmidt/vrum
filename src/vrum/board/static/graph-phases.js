// The phase view of Order: every piece of work of an epic that is still to do, one phase at a time. A
// phase is what can be done once the work it waits for is done (phase 1 can start now). With sizes, a
// phase breaks into batches that each fit a points budget, and the work with no size is listed apart,
// never guessed. The work is grouped by story, because one story is one shared context. The plan itself
// is graph-plan.js; this file draws it.

import { itemLink } from './ctx.js';
import { h, plural } from './dom.js';
import { icon } from './icons.js';
import { dueChip, kindIcon, pointsOf, sizeChip, statusChip } from './labels.js';
import { byStory, runsOf, sumPoints } from './graph-plan.js';
import { code, codeOf } from './graph-shared.js';

// What a piece of work waits for, as buttons that go to its phase when it is in this plan, and as links
// to its page when it is not (another epic, or a story). Each says what it waits for by its code and its name.
function waitsFor(ctx, node) {
  const refs = ctx.plan.waits.get(node.ref) || [];
  if (!refs.length) return null;
  const { model } = ctx;
  const epic = model.epicOf(node.ref);
  const chips = refs.map((ref) => {
    const found = model.node(ref);
    const words = [code(codeOf(found)), h('span', { class: 'graph-or-wait-name' }, found.title)];
    const phase = ctx.plan.phaseOf.get(ref);
    if (phase) {
      return h('button', { class: 'graph-or-wait', type: 'button', title: `${codeOf(found)} ${found.title}. In phase ${phase}`, 'data-fk': `w:${node.ref}:${ref}`, onclick: () => ctx.jump(ref, phase) }, words);
    }
    const other = model.epicOf(ref);
    const away = other && epic && other.ref !== epic.ref;
    return itemLink(ref, { class: 'graph-or-wait', title: `${codeOf(found)} ${found.title}${away ? `. In ${codeOf(other)} ${other.title}, outside this epic` : ''}`, 'data-fk': `w:${node.ref}:${ref}` }, words, away ? h('span', { class: 'graph-or-wait-in' }, `in ${codeOf(other)} ${other.title}`) : null);
  });
  return h('p', { class: 'graph-or-waits' }, h('span', { class: 'graph-or-waits-label' }, 'Waits for'), chips);
}

// One piece of work: its code and title (a link to its page), where it stands, its size and due date,
// and what it waits for. Finished work in the Done fold says neither that it is done (the fold does) nor when it was due.
export function itemRow(ctx, node) {
  const picked = ctx.highlight === node.ref;
  const finished = ctx.finished && node.status === 'done';
  return h(
    'li',
    { class: `graph-or-item s-${node.status}${node.status === 'dropped' ? ' is-ended' : ''}${picked ? ' is-sel' : ''}`, 'data-ref': node.ref, 'aria-current': picked ? 'true' : null },
    itemLink(node.ref, { class: 'graph-or-item-link', 'data-fk': `r:${node.ref}` }, code(codeOf(node)), h('span', { class: 'graph-or-item-title' }, node.title)),
    h('span', { class: 'graph-or-item-meta' }, finished ? null : statusChip(node.status), sizeChip(node), node.status === 'done' || node.status === 'dropped' ? null : dueChip(node, { size: 14, days: true })),
    waitsFor(ctx, node),
  );
}

// Work with the same story together, each story under its own heading.
export function storyGroups(ctx, nodes, level = 4) {
  const { model } = ctx;
  return byStory(model, nodes).map((group) => {
    const story = group.story;
    const head = story
      ? itemLink(story.ref, { class: 'graph-or-story-link', 'data-fk': `s:${story.ref}` }, kindIcon('story', 14), code(codeOf(story)), h('span', { class: 'graph-or-story-title' }, story.title))
      : h('span', { class: 'graph-or-story-link' }, 'Stories with no tasks');
    return h(
      'section',
      { class: 'graph-or-story', 'data-story': story ? story.ref : 'none' },
      h(`h${level}`, { class: `graph-or-story-head${story && ctx.highlight === story.ref ? ' is-sel' : ''}` }, head),
      h('ul', { class: 'graph-or-items plain' }, group.items.map((node) => itemRow(ctx, node))),
    );
  });
}

// The work that is done, folded: a heading that is a button.
export function doneGroup(ctx, { open, toggle }) {
  const { done } = ctx.plan;
  if (!done.length) return null;
  return h(
    'section',
    { class: 'graph-or-done', id: 'graph-or-done' },
    h(
      'h3',
      { class: 'graph-or-fold-head' },
      h('button', { class: 'graph-or-fold', type: 'button', id: 'graph-or-done-toggle', 'data-fk': 'done', 'aria-expanded': String(open), 'aria-controls': 'graph-or-done-body', onclick: toggle }, icon(open ? 'chevron_down' : 'chevron_right', { size: 16 }), `Done (${done.length})`),
    ),
    open ? h('div', { class: 'graph-or-done-body', id: 'graph-or-done-body' }, storyGroups({ ...ctx, finished: true }, done)) : null,
  );
}

// "6 items, 14 points" for a phase, or just the items when nothing has a size.
function phaseWords(nodes, sized) {
  const items = plural(nodes.length, 'item');
  if (!sized) return items;
  const withSize = nodes.filter((node) => pointsOf(node) !== null);
  const without = nodes.length - withSize.length;
  return `${items}, ${plural(sumPoints(withSize, pointsOf), 'point')}${without ? `, ${without} not sized` : ''}`;
}

// One phase: its heading, and its work, in batches when there are sizes.
export function phaseSection(ctx, index) {
  const nodes = ctx.plan.phases[index - 1];
  const { model, sized, budget } = ctx;
  let body;
  if (sized) {
    const withSize = nodes.filter((node) => pointsOf(node) !== null);
    const without = nodes.filter((node) => pointsOf(node) === null);
    const runs = runsOf(model, withSize, budget, pointsOf);
    body = [
      runs.map((run, i) =>
        h(
          'section',
          { class: 'graph-or-batch', 'data-batch': i + 1, 'data-points': run.points },
          h('h4', { class: 'graph-or-batch-head' }, `Batch ${i + 1}: ${plural(run.points, 'point')}, ${plural(run.items.length, 'item')}`, run.big ? h('span', { class: 'chip graph-or-big' }, 'Bigger than a run') : null),
          storyGroups(ctx, run.items, 5),
        ),
      ),
      without.length ? h('section', { class: 'graph-or-batch graph-or-unsized', id: 'graph-or-unsized' }, h('h4', { class: 'graph-or-batch-head' }, `Not sized yet (${without.length})`), storyGroups(ctx, without, 5)) : null,
    ];
  } else {
    body = storyGroups(ctx, nodes);
  }
  return h(
    'section',
    { class: 'graph-or-phase', id: 'graph-or-phase', 'data-phase': index, 'aria-labelledby': 'graph-or-phase-title' },
    h('h3', { class: 'graph-or-phase-head', id: 'graph-or-phase-title' }, h('span', { class: 'sr-only' }, `Phase ${index} `), h('span', { class: 'graph-or-phase-words' }, phaseWords(nodes, sized))),
    body,
  );
}
