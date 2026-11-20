// The plan behind the Order tab of the Graph: which piece of work can start when, and how a phase
// breaks into runs that fit a points budget. Pure functions of the work and its "needs first" links,
// with no page in them, so the same answer can be checked without one.
//
// The pieces of work are the leaves: a task, or a story that has no tasks. A prerequisite that is done
// or dropped never delays anything, and one outside the chosen epic never does either. A link between
// bigger things (a story that needs another story first) counts for the leaves under them.

export const BUDGETS = [5, 8, 13, 21];
export const DEFAULT_BUDGET = 8;

export const isOpen = (node) => node.status !== 'done' && node.status !== 'dropped';

// The pieces of work under a node: the node itself for a task or a story with no tasks.
export function leavesOf(model, ref) {
  const node = model.node(ref);
  if (!node) return [];
  if (node.kind === 'task') return [node];
  const kids = model.kids(ref);
  if (node.kind === 'story' && !kids.length) return [node];
  return kids.flatMap((kid) => leavesOf(model, kid.ref));
}

// The story a task belongs to (a story with no tasks stands alone: null).
export const storyOf = (model, node) => (node.kind === 'task' ? model.node(node.parent) || null : null);

// Pieces of work in the order they are shown: by story, then by number; stories with no tasks last.
export function inPlace(model, nodes) {
  const place = (node) => {
    const story = storyOf(model, node);
    return story ? story.number : Infinity;
  };
  return nodes.slice().sort((a, b) => place(a) - place(b) || a.number - b.number);
}

// The pieces of work with the same story together, in order: [{ story, items }]. The stories with no
// tasks go together in one group with `story: null`, last.
export function byStory(model, nodes) {
  const groups = new Map();
  for (const node of inPlace(model, nodes)) {
    const story = storyOf(model, node);
    const key = story ? story.ref : '';
    if (!groups.has(key)) groups.set(key, { story, items: [] });
    groups.get(key).items.push(node);
  }
  return [...groups.values()];
}

// The plan of a set of pieces of work. `links` are the project's links ({ source, target, link_type }):
// `source` needs `target` first. Returns
//   open, done, dropped   the pieces of work by where they stand
//   phases                [[node]], phase 1 first, each in the order it is shown
//   phaseOf               ref -> its phase (open work only)
//   waits                 ref -> [ref]: what an open piece of work still waits for, as the links name it
//   before                ref -> [ref]: the open pieces of work of this plan that it has to wait for
//   linked                the refs of this plan's work that a "needs first" link names, either way
export function planOf(model, leaves, links) {
  const open = leaves.filter(isOpen);
  const openSet = new Set(open.map((node) => node.ref));
  const inPlan = new Set(leaves.map((node) => node.ref));
  const memo = new Map();
  const under = (ref) => {
    if (!memo.has(ref)) memo.set(ref, leavesOf(model, ref).map((node) => node.ref));
    return memo.get(ref);
  };
  const before = new Map(open.map((node) => [node.ref, new Set()]));
  const waits = new Map(open.map((node) => [node.ref, []]));
  const linked = new Set();
  for (const link of links) {
    if (link.link_type !== 'needs_first') continue;
    const [dep, pre] = [model.node(link.source), model.node(link.target)];
    if (!dep || !pre) continue;
    for (const node of [dep, pre]) if (inPlan.has(node.ref)) linked.add(node.ref);
    const prerequisites = under(pre.ref);
    for (const ref of under(dep.ref)) {
      if (!openSet.has(ref)) continue;
      if (isOpen(pre) && pre.ref !== ref && !waits.get(ref).includes(pre.ref)) waits.get(ref).push(pre.ref);
      for (const other of prerequisites) if (other !== ref && openSet.has(other)) before.get(ref).add(other);
    }
  }
  const phaseOf = new Map();
  const visiting = new Set();
  const phase = (ref) => {
    if (phaseOf.has(ref)) return phaseOf.get(ref);
    if (visiting.has(ref)) return 1;
    visiting.add(ref);
    let value = 1;
    for (const other of before.get(ref)) value = Math.max(value, 1 + phase(other));
    visiting.delete(ref);
    phaseOf.set(ref, value);
    return value;
  };
  open.forEach((node) => phase(node.ref));
  const phases = Array.from({ length: Math.max(0, ...phaseOf.values()) }, () => []);
  for (const node of inPlace(model, open)) phases[phaseOf.get(node.ref) - 1].push(node);
  return {
    open,
    done: leaves.filter((node) => node.status === 'done'),
    dropped: leaves.filter((node) => node.status === 'dropped'),
    phases,
    phaseOf,
    waits,
    before: new Map([...before].map(([ref, set]) => [ref, [...set]])),
    linked,
  };
}

export const sumPoints = (nodes, pointsOf) => nodes.reduce((total, node) => total + (pointsOf(node) || 0), 0);

// One phase's sized work cut into runs that fit `budget` points, filling in story order. A story's work
// stays together when it fits in one run; work bigger than the budget is a run of its own.
// Returns [{ items, points, big }].
export function runsOf(model, sized, budget, pointsOf) {
  const runs = [];
  let run = null;
  const flush = () => {
    if (run && run.items.length) runs.push(run);
    run = null;
  };
  const add = (node) => {
    if (!run) run = { items: [], points: 0, big: false };
    run.items.push(node);
    run.points += pointsOf(node);
  };
  for (const group of byStory(model, sized)) {
    const total = sumPoints(group.items, pointsOf);
    if (run && run.points + total <= budget) {
      group.items.forEach(add);
    } else if (total <= budget) {
      flush();
      group.items.forEach(add);
    } else {
      for (const node of group.items) {
        const points = pointsOf(node);
        if (points > budget) {
          flush();
          runs.push({ items: [node], points, big: true });
        } else {
          if (run && run.points + points > budget) flush();
          add(node);
        }
      }
    }
  }
  flush();
  return runs;
}
