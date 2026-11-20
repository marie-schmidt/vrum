// What the Structure and Order views of the Graph share: the work read from the store as a
// small model (children, progress, what waits on what), the links of one item, and the few
// pieces of page they are all built from (a toggle, a segmented choice). Nothing here keeps
// anything: every call reads the store as it is now.

import { h, s as element } from './dom.js';
import { todayDay, toDay } from './dates.js';
import { icon } from './icons.js';
import { KIND_ORDER, nodeCode } from './labels.js';
import { store } from './store.js';

// An SVG element that is never painted by the browser's own defaults, which are black: a picture starts
// as a wireframe in the text colour (shapes unfilled, text filled, an arrowhead filled) and the style sheet
// (graph-explore-2.css) draws it properly, so a style sheet that is late or missing leaves no black blobs.
// A rule of the style sheet always wins over these attributes.
export function s(tag, props, ...children) {
  const unpainted = {
    svg: { fill: 'none', stroke: 'currentColor' },
    text: { fill: 'currentColor', stroke: 'none' },
    path: props && /arrow/.test(props.class || '') ? { fill: 'currentColor', stroke: 'none' } : {},
  }[tag];
  return element(tag, { ...unpainted, ...props }, ...children);
}

export const codeOf = (node) => nodeCode(node.kind, node.number);
export const clip = (text, size) => {
  const one = String(text ?? '').replace(/\s+/g, ' ').trim();
  return one.length > size ? `${one.slice(0, size - 1).trimEnd()}…` : one;
};

// Words cut into at most `lines` lines of `max` characters; the last line ends in … if there is more.
export function wrap(text, max, lines) {
  const out = [];
  let line = '';
  for (const word of String(text).split(/\s+/)) {
    if (line && `${line} ${word}`.length > max) {
      out.push(line);
      line = word;
    } else {
      line = `${line} ${word}`.trim();
    }
  }
  if (line) out.push(line);
  if (out.length <= lines) return out;
  const keep = out.slice(0, lines);
  keep[lines - 1] = clip(out.slice(lines - 1).join(' '), max);
  return keep;
}

// -- the work ------------------------------------------------------------------------------------

// The epics, stories and tasks as they are in the store now. `kids` lists the children of a node in
// the order the server gives (by number); an epic's and a story's progress come from the server.
export function workModel() {
  const list = [...store.nodes.values()].sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || a.number - b.number);
  const kids = new Map();
  for (const node of list) {
    if (node.parent === null || !store.nodes.has(node.parent)) continue;
    if (!kids.has(node.parent)) kids.set(node.parent, []);
    kids.get(node.parent).push(node);
  }
  const today = todayDay();
  const model = {
    list,
    epics: list.filter((node) => node.kind === 'epic'),
    kids: (ref) => kids.get(ref) || [],
    node: (ref) => store.nodes.get(ref),
    epicOf(ref) {
      let node = store.nodes.get(ref);
      while (node && node.kind !== 'epic') node = store.nodes.get(node.parent);
      return node || null;
    },
    // Every task under a node (the node itself for a task).
    tasksUnder(ref) {
      const node = store.nodes.get(ref);
      if (!node) return [];
      if (node.kind === 'task') return [node];
      return model.kids(ref).flatMap((kid) => model.tasksUnder(kid.ref));
    },
    progress(ref) {
      const node = store.nodes.get(ref);
      if (!node) return { done: 0, total: 0 };
      if (node.kind === 'task') return { done: node.status === 'done' ? 1 : 0, total: node.status === 'dropped' ? 0 : 1 };
      return { done: node.progress_done || 0, total: node.progress_total || 0 };
    },
    // Days past the day it was due, 0 when it is not late.
    late(ref) {
      const node = store.nodes.get(ref);
      return node && node.overdue && node.target_on ? Math.max(1, today - toDay(node.target_on)) : 0;
    },
    waitingOn: (ref) => (store.nodes.get(ref)?.waiting_on || []).filter((one) => store.nodes.has(one)),
    // The refs of everything under a node, the node included.
    subtree(ref) {
      const set = new Set([ref]);
      for (const kid of model.kids(ref)) for (const one of model.subtree(kid.ref)) set.add(one);
      return set;
    },
  };
  return model;
}

// -- links ---------------------------------------------------------------------------------------

// Every link of one item, and the references in texts between work, as { type, dir, other }. `out`
// means this item is the source (it needs the other first, supersedes it, names it).
export function linksOf(ref) {
  const rows = [];
  for (const link of store.links) {
    if (!store.nodes.has(link.source) || !store.nodes.has(link.target)) continue;
    if (link.source === ref) rows.push({ type: link.link_type, dir: 'out', other: link.target });
    else if (link.target === ref) rows.push({ type: link.link_type, dir: 'in', other: link.source });
  }
  const seen = new Set();
  for (const mention of store.references || []) {
    if (!mention.target?.exists) continue;
    const [from, to] = [mention.source.ref, mention.target.ref];
    if (!store.nodes.has(from) || !store.nodes.has(to)) continue;
    if (store.nodes.get(from).parent === to || store.nodes.get(to).parent === from) continue;
    const other = from === ref ? to : to === ref ? from : null;
    if (!other || seen.has(`${from}>${to}`)) continue;
    seen.add(`${from}>${to}`);
    rows.push({ type: 'mention', dir: from === ref ? 'out' : 'in', other });
  }
  return rows;
}

// -- small pieces of page ------------------------------------------------------------------------

export const code = (text) => h('span', { class: 'graph-x-code mono' }, text);

export function dueChip(model, ref) {
  const node = model.node(ref);
  if (!node || !node.target_on) return null;
  const late = model.late(ref);
  return late > 0
    ? h('span', { class: 'chip warn' }, icon('clock', { size: 14 }), `Overdue ${late} ${late === 1 ? 'day' : 'days'}`)
    : h('span', { class: 'chip' }, icon('calendar', { size: 14 }), `Due ${node.target_on}`);
}

export function toggle(label, iconName, pressed, onclick, props = {}) {
  return h(
    'button',
    { class: 'small', type: 'button', 'aria-pressed': pressed ? 'true' : 'false', onclick, ...props },
    iconName ? icon(iconName, { size: 14 }) : null,
    label,
  );
}

// One choice out of a few. options: [[value, text, title]].
export function segment(label, aria, options, current, pick, key) {
  return h(
    'div',
    { class: 'graph-x-control' },
    h('span', { class: 'graph-x-control-label' }, label),
    h(
      'div',
      { class: 'segmented', role: 'group', 'aria-label': aria },
      options.map(([value, text, title]) =>
        h('button', { type: 'button', title, 'aria-pressed': current === value ? 'true' : 'false', 'data-choice': `${key}:${value}`, onclick: () => pick(value) }, text),
      ),
    ),
  );
}

// A message in the middle of a view: what is the matter and what to do about it.
export function viewMessage(title, words, ...more) {
  return h('section', { class: 'graph-x-panel graph-x-empty' }, h('h2', {}, title), h('p', { class: 'quiet' }, words), ...more);
}
