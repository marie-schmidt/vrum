// The Structure view of the Graph: how the work fits together, drawn as the folder structure of an
// Obsidian graph. The whole project is a tree seen from above: each epic a hub, each story a smaller
// hub, each task a small dot around its story, nothing but the hierarchy joined by lines, so nothing
// crosses. Names are shown only where there is room and as you zoom in; the links between items (needs
// first, duplicates, related, references) are drawn for the item looked at only, or for all when asked.
// The "Look at" choice in the toolbar swaps the whole project for the radial view of one epic, with
// every name. There is no card or side column: a click or Enter on any dot opens that item's page.
//
// What is looked at is the Graph's focus (`focus=` in the address, chosen in the box at the top), so
// the other tabs follow it; here it is marked on the picture with its links. Its own choices are in the
// address too: `scope` (project, or an epic's reference), `level` (epics, stories, tasks), `labels`
// (off, zoom, always), `colour` (status, epic) and `links=1`. The zoom and the place of the picture are
// not (they are kept while the page is open). Everything is read from the store.

import { projectId, projectInfo } from './api.js';
import { h, plural, put } from './dom.js';
import { ICONS, icon } from './icons.js';
import { KIND, PRIORITY, STATUS, actorLabel, parseItemRef, statusIcon } from './labels.js';
import {
  LEAF_DOT,
  LEVEL_MAX,
  RANK,
  angleBetween,
  epicExtent,
  epicRadial,
  polar,
  sector,
  treeCurve,
  treeOf,
  wholeLayout,
} from './graph-radial.js';
import { biggerChip, keyChip, lateChip, linkChips, mapStatusChips, ringChip, shapeChips, startedChip, statusChips, viewKey } from './graph-key.js';
import { clip, code, codeOf, linksOf, s, segment, toggle, viewMessage, workModel, wrap } from './graph-shared.js';
import { ensure, store } from './store.js';

// The address keys that are this view's own, which are forgotten when another tab is chosen.
export const KEYS = ['scope', 'level', 'labels', 'colour', 'links'];

const LABEL_MODES = [
  ['off', 'Off', 'No names on the picture; point at a dot or hub to read it'],
  ['zoom', 'On zoom', 'Names appear as you zoom in'],
  ['always', 'Always', 'As many names as fit'],
];
const LEVELS = [
  ['epics', 'Epics', 'Show the epics only'],
  ['stories', '+ Stories', 'Show epics and stories'],
  ['tasks', '+ Tasks', 'Show epics, stories and tasks'],
];
const COLOURS = [
  ['status', 'Status', 'Colour by where the work stands'],
  ['epic', 'Epic', 'Colour by the epic it belongs to'],
];
// A project this big shows its stories first, and the tasks when asked.
const MOST_DRAWN = 1500;
const MOST_ZOOM = 8;
const LEAST_ZOOM = 0.6;
const HUES = 6;
const R1 = (n) => Math.round(n * 10) / 10;

let root = null;
let api = null;
let stop = []; // what a drawing has to undo when the next one replaces it
let name = null; // the project's name, for the middle of the picture
let loadedRefs = false;
const whole = { k: 1, x: 0, y: 0, level: null }; // the whole project's zoom and place
const ring = { zoom: 1, x: 0, y: 0, epic: null }; // the same for one epic

// -- the page ------------------------------------------------------------------------------------

export function mount(element, nextApi) {
  root = element;
  api = nextApi;
  render();
  if (!loadedRefs) {
    loadedRefs = true;
    // The references written in texts are links too; they come a moment after the picture.
    ensure('references').then(() => root && render()).catch(() => {});
  }
  if (name === null) {
    projectInfo(projectId()).then((found) => { name = found.name; if (root) render(); }).catch(() => { name = ''; });
  }
}

export function update(nextApi) {
  api = nextApi;
  if (root) render();
}

export function refresh() {
  if (root) render();
}

export function unmount() {
  undo();
  root = null;
  api = null;
}

function undo() {
  for (const fn of stop) fn();
  stop = [];
}

const pick = (value, allowed, fallback) => (allowed.includes(value) ? value : fallback);

function settings(model) {
  const p = api.params;
  const asked = p.get('scope');
  const epic = asked && asked !== 'project' && model.node(asked)?.kind === 'epic' ? asked : null;
  return {
    epic,
    labels: pick(p.get('labels'), LABEL_MODES.map(([v]) => v), 'zoom'),
    colour: pick(p.get('colour'), COLOURS.map(([v]) => v), 'status'),
    level: pick(p.get('level'), LEVELS.map(([v]) => v), model.list.length > MOST_DRAWN ? 'stories' : 'tasks'),
    links: p.get('links') === '1',
  };
}

// A choice that only changes how the picture looks keeps the browser's back button for something else.
const setLook = (changes) => api.setParams(changes, { replace: true });
// The Key's epic buttons put an epic on the picture by making it what the Graph looks at (again to let go).
const mark = (ref) => (ref === api.focus ? api.setParams({ focus: null }) : api.setFocus(ref));

// The one choice of what is drawn: the whole project, or one epic with every name. An epic that does
// not hold what the Graph is looking at takes the look with it, since the picture could not show it.
function lookAt(model, epic) {
  const select = h(
    'select',
    {
      id: 'graph-st-scope',
      'data-choice': 'scope',
      onchange: () => {
        const ref = select.value;
        if (!ref) return setLook({ scope: null });
        const apart = api.focus && model.node(api.focus) && !model.subtree(ref).has(api.focus);
        return setLook(apart ? { scope: ref, focus: ref, trail: null } : { scope: ref });
      },
    },
    h('option', { value: '' }, 'Whole project'),
    model.epics.map((one) => h('option', { value: one.ref }, `${codeOf(one)} ${clip(one.title, 48)}`)),
  );
  select.value = epic || '';
  return h('div', { class: 'graph-x-control graph-st-look' }, h('label', { class: 'graph-x-control-label', for: 'graph-st-scope' }, 'Look at'), select);
}

// An acceptance criterion or a note in focus is not work, so nothing is marked on the whole map as
// the item looked at; the work it concerns is lit instead. Empty when the focus is not one of those, or
// the records are not loaded.
function concernedBy(model, ref) {
  const found = ref ? parseItemRef(ref) : null;
  if (!found || !['rule', 'note'].includes(found.kind)) return [];
  const record = (store[{ rule: 'rules', note: 'notes' }[found.kind]] || []).find((one) => one.id === found.id);
  if (!record) return [];
  return (found.kind === 'rule' ? [record.node] : record.nodes || []).filter((one) => one && model.node(one));
}

function render() {
  undo();
  const keep = rememberFocus();
  // The work is read again for each drawing: it is cheap, and the store may have changed.
  const model = workModel();
  if (!model.list.length) {
    put(root, h('div', { class: 'graph-xv' }, viewMessage('Nothing is planned yet', 'The Structure map draws epics, stories and tasks. When the first epic is made, it appears here.')));
    return;
  }
  const cfg = settings(model);
  let chosen = api.focus && model.node(api.focus) ? api.focus : null;
  let epic = cfg.epic;
  if (epic && chosen && !model.subtree(epic).has(chosen)) {
    // The thing looked at is in another epic: the whole project has room for it, and the address follows.
    epic = null;
    api.setParams({ scope: null }, { replace: true });
  }
  if (epic && chosen === epic) chosen = null;
  const page = epic ? epicPage(model, epic, chosen) : wholePage(model, cfg, chosen, concernedBy(model, api.focus));
  put(root, h('div', { class: 'graph-xv' }, page));
  restoreFocus(keep);
}

// Where the keyboard was, so that a drawing that replaces the elements does not lose it.
function rememberFocus() {
  const at = document.activeElement;
  if (!at || !root || !root.contains(at)) return null;
  const node = at.closest('[data-ref]');
  if (node && at.closest('svg')) return { selector: `svg [data-ref="${node.getAttribute('data-ref')}"]` };
  const choice = at.closest('[data-choice]');
  return choice ? { selector: `[data-choice="${choice.getAttribute('data-choice')}"]` } : null;
}

function restoreFocus(keep) {
  if (keep) root.querySelector(keep.selector)?.focus({ preventScroll: true });
}

// -- shared parts of both pictures -----------------------------------------------------------------

// The status mark of a leaf, drawn from the board's own status icons, centred on 0,0 with radius r.
function statusMark(status, r) {
  const k = r / 9;
  const g = s('g', { class: `graph-rd-mark s-${status}`, transform: `scale(${R1(k)}) translate(-12 -12)`, 'stroke-width': R1(1.7 / k) });
  g.append(s('circle', { class: 'graph-rd-bg', cx: 12, cy: 12, r: 9 }));
  for (const [tag, attrs] of ICONS[status] || ICONS.new) g.append(s(tag, attrs));
  return g;
}

function words(model, ref) {
  const node = model.node(ref);
  const late = model.late(ref);
  const p = model.progress(ref);
  const bits = [`${KIND[node.kind].label} ${codeOf(node)}`, node.title, STATUS[node.status].label];
  if (late > 0) bits.push(`overdue ${plural(late, 'day')}`);
  if (node.kind !== 'task') bits.push(`${p.done} of ${plural(p.total, 'task')} done`);
  return `${bits[0]}: ${bits[1]}. ${bits.slice(2).join(', ')}.`;
}

// What a pointed-at node says about itself.
function tipContent(model, ref) {
  const node = model.node(ref);
  const late = model.late(ref);
  const p = model.progress(ref);
  let line;
  if (node.kind === 'epic') {
    const stories = model.kids(ref).length;
    line = `${plural(stories, 'story', 'stories')} · ${plural(p.total, 'task')} · ${p.total ? Math.round((100 * p.done) / p.total) : 0}% done`;
  } else if (node.kind === 'story') {
    line = `${plural(p.total, 'task')}, ${p.done} done`;
  } else {
    line = `${PRIORITY[node.priority].label} priority${node.target_on ? (late > 0 ? ` · overdue ${plural(late, 'day')}` : ` · due ${node.target_on}`) : ''}`;
  }
  return [
    h('div', { class: 'graph-x-tip-top' }, statusIcon(node.status, 14), code(codeOf(node)), h('span', {}, `${KIND[node.kind].label} · ${STATUS[node.status].label}`)),
    h('div', { class: 'graph-x-tip-title' }, node.title),
    h('div', { class: 'quiet' }, line),
    node.claimed_by ? h('div', { class: 'quiet' }, 'Held by ', actorLabel(node.claimed_by, node.claimed_by_kind)) : null,
  ];
}

// The links of the item looked at, each with the other end lifted to something the picture shows.
function partnersOf(ref, lift) {
  const seen = new Set();
  return linksOf(ref)
    .map((row) => ({ ...row, other: lift(row.other) }))
    .filter((row) => {
      const key = `${row.type}:${row.dir}:${row.other}`;
      if (row.other === ref || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

const hueOf = (index) => `ep-${index % HUES}`;

// A node opens its page on a click, Enter or Space.
function opensItsPage(el, ref) {
  el.addEventListener('click', (e) => {
    e.stopPropagation();
    api.open(ref);
  });
  el.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    api.open(ref);
  });
}

// -- the whole project -------------------------------------------------------------------------

function wholePage(model, cfg, chosen, concernedRefs) {
  const tree = treeOf(model);
  const layout = wholeLayout(tree, cfg.level);
  if (whole.level !== cfg.level) {
    whole.level = cfg.level;
    whole.k = 1;
    whole.x = 0;
    whole.y = 0;
  }
  const lift = (ref) => {
    let node = model.node(ref);
    while (node && RANK[node.kind] > LEVEL_MAX[cfg.level]) node = model.node(node.parent);
    return node ? node.ref : ref;
  };
  const visibleKids = (ref) => model.kids(ref).filter((kid) => RANK[kid.kind] <= LEVEL_MAX[cfg.level]);
  const sel = chosen ? lift(chosen) : null;
  const partners = sel ? partnersOf(sel, lift) : [];
  // What a record in focus concerns is lit like what an item looked at is linked to.
  const concerned = [...new Set(concernedRefs.map(lift))];
  const partnerSet = new Set(sel ? partners.map((row) => row.other) : concerned);
  // What is lit: the item looked at, what it sits in, what sits in it, and what it is linked to.
  const pathSet = new Set();
  if (sel) {
    let up = model.node(sel);
    while (up) {
      pathSet.add(up.ref);
      up = up.parent ? model.node(up.parent) : null;
    }
    const down = (ref) => visibleKids(ref).forEach((kid) => { pathSet.add(kid.ref); down(kid.ref); });
    down(sel);
  }
  for (const ref of concerned) {
    for (let up = model.node(ref); up; up = up.parent ? model.node(up.parent) : null) pathSet.add(up.ref);
  }
  const lit = new Set([...pathSet, ...partnerSet]);

  const b = layout.bounds;
  const vb = { x: b.x0 - 30, y: b.y0 - 30, w: b.x1 - b.x0 + 60, h: b.y1 - b.y0 + 60 };
  const rovingRef = sel || (model.epics[0] && model.epics[0].ref);
  let hover = null;

  const svg = s('svg', {
    class: `graph-st-svg${cfg.colour === 'epic' ? ' is-by-epic' : ''}${sel || concerned.length ? ' has-sel' : ''}`,
    viewBox: `${R1(vb.x)} ${R1(vb.y)} ${R1(vb.w)} ${R1(vb.h)}`,
    role: 'group',
    'aria-label': `The whole project: ${plural(model.epics.length, 'epic')}${cfg.level !== 'epics' ? ', their stories' : ''}${cfg.level === 'tasks' ? ' and tasks' : ''}, joined as a tree`,
  });
  const vp = s('g', { class: 'graph-st-vp' });
  const hier = s('g', { class: 'graph-st-hier' });
  const cross = s('g', { class: `graph-st-cross${cfg.links ? ' is-all' : ''}` });
  const nodes = s('g', { class: 'graph-st-nodes' });
  const names = s('g', { class: 'graph-st-names', 'aria-hidden': 'true' });
  svg.append(s('defs', {}, s('marker', { id: 'graph-st-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 8, markerHeight: 8, orient: 'auto', markerUnits: 'userSpaceOnUse' }, s('path', { class: 'graph-st-arrow', d: 'M0 1 L10 5 L0 9 z' }))), vp);

  // The hierarchy, the skeleton: a fine line from each parent to each child.
  for (const [from, to] of layout.edges) {
    const a = from === 'root' ? { x: 0, y: 0, r: layout.rProj } : layout.pos.get(from);
    const c = layout.pos.get(to);
    const dx = c.x - a.x;
    const dy = c.y - a.y;
    const len = Math.hypot(dx, dy) || 1;
    const on = pathSet.has(to) && (from === 'root' || pathSet.has(from));
    hier.append(s('line', { class: `graph-st-line${on ? ' is-lit' : ''}`, x1: R1(a.x + (dx / len) * a.r), y1: R1(a.y + (dy / len) * a.r), x2: R1(c.x - (dx / len) * c.r), y2: R1(c.y - (dy / len) * c.r) }));
  }

  // Links between work: all when asked, and those of the item looked at always.
  const bend = (p, q) => {
    const mx = (p.x + q.x) / 2;
    const my = (p.y + q.y) / 2;
    const nx = -(q.y - p.y);
    const ny = q.x - p.x;
    const c1 = [mx + nx * 0.2, my + ny * 0.2];
    const c2 = [mx - nx * 0.2, my - ny * 0.2];
    return Math.hypot(...c1) < Math.hypot(...c2) ? c1 : c2;
  };
  const drawLink = (type, from, to, extra) => {
    const p = layout.pos.get(from);
    const q = layout.pos.get(to);
    if (!p || !q) return;
    const [cx, cy] = bend(p, q);
    const trim = (o, extraR) => {
      const vx = cx - o.x;
      const vy = cy - o.y;
      const len = Math.hypot(vx, vy) || 1;
      return [o.x + (vx / len) * (o.r + extraR), o.y + (vy / len) * (o.r + extraR)];
    };
    const arrow = type === 'needs_first' || type === 'supersedes';
    const [x1, y1] = trim(p, 1.5);
    const [x2, y2] = trim(q, arrow ? 2.5 : 1.5);
    cross.append(s('path', { class: `graph-st-link t-${type} ${extra}`, d: `M${R1(x1)} ${R1(y1)} Q${R1(cx)} ${R1(cy)} ${R1(x2)} ${R1(y2)}`, 'marker-end': arrow ? 'url(#graph-st-arrow)' : null }));
  };
  // The arrow points the way the work flows: from what comes first to what waits.
  const direct = (type, dir, ref, other) => {
    if ((type === 'needs_first' || type === 'supersedes') && dir === 'out') return type === 'needs_first' ? [other, ref] : [ref, other];
    if ((type === 'needs_first' || type === 'supersedes') && dir === 'in') return type === 'needs_first' ? [ref, other] : [other, ref];
    return dir === 'in' && type === 'mention' ? [other, ref] : [ref, other];
  };
  const drawn = new Set();
  if (sel) {
    for (const row of partners) {
      const [from, to] = direct(row.type, row.dir, sel, row.other);
      drawn.add(`${row.type}:${from}:${to}`);
      drawn.add(`${row.type}:${to}:${from}`);
      drawLink(row.type, from, to, 'is-chosen');
    }
  }
  if (cfg.links) {
    const all = new Map();
    const add = (type, a, b) => {
      const x = lift(a);
      const y = lift(b);
      if (x === y) return;
      all.set(`${type}:${x}:${y}`, { type, from: x, to: y });
    };
    for (const link of store.links) {
      if (!model.node(link.source) || !model.node(link.target)) continue;
      if (link.link_type === 'needs_first') add('needs_first', link.target, link.source);
      else add(link.link_type, link.source, link.target);
    }
    for (const mention of store.references || []) {
      const [a, c] = [mention.source.ref, mention.target.ref];
      if (!mention.target.exists || !model.node(a) || !model.node(c)) continue;
      if (model.node(a).parent === c || model.node(c).parent === a) continue;
      add('mention', a, c);
    }
    for (const link of all.values()) if (!drawn.has(`${link.type}:${link.from}:${link.to}`)) drawLink(link.type, link.from, link.to, 'is-all');
  }

  // The nodes: tasks first, so that hubs sit on top.
  nodes.append(s('g', { class: 'graph-st-project' }, s('circle', { class: 'graph-st-project-ring', r: layout.rProj + 7 }), s('circle', { class: 'graph-st-project-body', r: layout.rProj })));
  const byRank = layout.nodes.slice().sort((x, y) => RANK[y.kind] - RANK[x.kind]);
  for (const o of byRank) {
    const node = model.node(o.ref);
    const late = model.late(o.ref);
    const p = model.progress(o.ref);
    const hub = o.kind !== 'task';
    const flagged = node.status === 'blocked' || late > 0;
    const g = s(
      'g',
      {
        class: `graph-st-node is-${o.kind} s-${node.status} ${hueOf(o.epic)}${late > 0 ? ' is-late' : ''}${sel === o.ref ? ' is-sel' : ''}${partnerSet.has(o.ref) ? ' is-linked' : ''}${lit.has(o.ref) ? ' is-lit' : ''}`,
        transform: `translate(${R1(o.x)} ${R1(o.y)})`,
        tabindex: o.ref === rovingRef ? '0' : '-1',
        role: 'button',
        'data-ref': o.ref,
        'aria-current': sel === o.ref ? 'true' : null,
        'aria-label': `${words(model, o.ref)} Press Enter to open it.`,
      },
      s('circle', { class: 'graph-st-hit', r: Math.max(o.r + 3, 7) }),
    );
    if (hub && p.total && (o.kind === 'epic' || cfg.level !== 'tasks')) {
      const rr = o.r + 3.6;
      const C = Math.PI * 2 * rr;
      g.append(s('circle', { class: 'graph-st-track', r: rr }), s('circle', { class: 'graph-st-fill', r: rr, 'stroke-dasharray': `${R1((C * p.done) / p.total)} ${R1(C)}`, transform: 'rotate(-90)' }));
    }
    g.append(s('circle', { class: 'graph-st-body', r: o.r }));
    if (flagged) g.append(s('circle', { class: `graph-st-flag ${node.status === 'blocked' ? 'is-danger' : 'is-warn'}`, r: o.r + (hub ? (o.kind === 'epic' ? 9 : 7) : 2.3) }));
    g.append(s('circle', { class: 'graph-st-ring', r: o.r + (hub ? (o.kind === 'epic' ? 11 : 9) : 6) }));
    nodes.append(g);
  }
  vp.append(hier, cross, nodes, names);

  // -- the names: a fixed size on the screen, placed so that none overlaps another --
  const CHAR = 6.5;
  const MONO = 7.5;
  const textWidth = (lines) =>
    Math.max(...lines.map((line) => {
      const found = /^[EST]-\d+/.exec(line);
      return found ? found[0].length * MONO + (line.length - found[0].length) * CHAR : line.length * CHAR;
    }));
  const LINE = 14.5;
  const hubOf = (ref) => model.epicOf(ref).ref;

  function drawNames() {
    const ctm = svg.getScreenCTM();
    if (!ctm) return;
    const sc = ctm.a;
    const k = whole.k;
    const inv = 1 / (sc * k);
    const box = svg.getBoundingClientRect();
    const px = (wx) => (wx * k + whole.x) * sc + ctm.e - box.left;
    const py = (wy) => (wy * k + whole.y) * sc + ctm.f - box.top;
    const near = (x, y, pad) => x > -pad && x < box.width + pad && y > -pad && y < box.height + pad;
    const activeEpics = new Set();
    // Choosing or pointing at a hub (not a single task) brings up the codes of the stories round it.
    if (sel && model.node(sel).kind !== 'task') activeEpics.add(hubOf(sel));
    if (hover && model.node(hover).kind !== 'task') activeEpics.add(hubOf(hover));
    const named = new Set(sel ? [sel, ...partnerSet] : concerned);
    const mode = cfg.labels;
    const dirOf = (o) => {
      const par = o.parent === 'root' ? { x: 0, y: 0 } : layout.pos.get(o.parent);
      const dx = o.x - par.x;
      const dy = o.y - par.y;
      const len = Math.hypot(dx, dy) || 1;
      return [dx / len, dy / len];
    };
    // Only the dots near the screen can be in the way of a name. A hub is wider than its body: its
    // arc and, when it is flagged, its ring are drawn outside it, and a name keeps off those too.
    const circles = [];
    const ringOf = (o) => {
      const flagged = model.node(o.ref).status === 'blocked' || model.late(o.ref) > 0;
      if (o.kind === 'task') return o.r + (flagged ? 3.3 : 0);
      return o.r + (flagged ? (o.kind === 'epic' ? 10 : 8) : 5.2);
    };
    for (const o of layout.nodes) {
      const x = px(o.x);
      const y = py(o.y);
      if (near(x, y, 160)) circles.push({ x, y, r: o.r * sc * k + 2, ring: ringOf(o) * sc * k + 2 });
    }
    circles.push({ x: px(0), y: py(0), r: layout.rProj * sc * k + 8, ring: layout.rProj * sc * k + 8 });
    const hitsCircle = (bx, size = 'r') => circles.some((c) => {
      if (c.x < bx[0] - 60 || c.x > bx[2] + 60) return false;
      const nx = Math.max(bx[0], Math.min(c.x, bx[2]));
      const ny = Math.max(bx[1], Math.min(c.y, bx[3]));
      return Math.hypot(c.x - nx, c.y - ny) < c[size];
    });
    const cands = [];
    if (mode !== 'off') {
      for (const o of layout.nodes) {
        if (o.kind === 'epic') continue;
        if (!near(px(o.x), py(o.y), 40)) continue;
        const node = model.node(o.ref);
        let [ux, uy] = dirOf(o);
        const isStory = o.kind === 'story';
        // A story's tasks fan out away from it, so its name goes on the line to its epic, where there is a gap.
        if (isStory && LEVEL_MAX[cfg.level] > 1 && model.kids(o.ref).length) {
          ux = -ux;
          uy = -uy;
        }
        let lines = null;
        let prio = 3;
        const full = `${codeOf(node)} ${node.title}`;
        if (named.has(o.ref)) {
          lines = wrap(full, 24, 2);
          prio = 1;
        } else if (mode === 'always') {
          lines = [isStory ? clip(full, 26) : codeOf(node)];
          prio = isStory ? 2 : 4;
        } else if (isStory) {
          if (k >= 3.2) lines = [clip(full, 30)];
          else if (k >= 1.8 || activeEpics.has(hubOf(o.ref))) lines = [codeOf(node)];
          prio = 2;
        } else if (k >= 5.5) lines = [clip(full, 30)];
        else if (k >= 3.6) lines = [codeOf(node)];
        else if (activeEpics.has(hubOf(o.ref)) && k >= 2.4) lines = [codeOf(node)];
        if (lines) cands.push({ ref: o.ref, prio, o, ux, uy, lines });
      }
    }
    cands.sort((a, c) => a.prio - c.prio);
    const placed = [];
    const out = [];
    const overlaps = (bx) => placed.some((q) => bx[0] < q[2] + 2 && bx[2] > q[0] - 2 && bx[1] < q[3] + 2 && bx[3] > q[1] - 2);
    // The picture's own buttons are in the way of a name, too.
    const zoomBox = zoomButtons.getBoundingClientRect();
    placed.push([zoomBox.left - box.left - 4, zoomBox.top - box.top - 4, zoomBox.right - box.left + 4, zoomBox.bottom - box.top + 4]);

    // An epic's name sits as near to its hub as there is clear room: on the line to the project, or a
    // little to either side of it.
    if (mode !== 'off') {
      const centre = { x: px(0), y: py(0) };
      const spokes = model.epics.map((epic) => {
        const o = layout.pos.get(epic.ref);
        return { ref: epic.ref, x1: centre.x, y1: centre.y, x2: px(o.x), y2: py(o.y) };
      });
      const segHitsBox = (sg, bx) => {
        const inside = (x, y) => x >= bx[0] && x <= bx[2] && y >= bx[1] && y <= bx[3];
        if (inside(sg.x1, sg.y1) || inside(sg.x2, sg.y2)) return true;
        const edges = [[bx[0], bx[1], bx[2], bx[1]], [bx[2], bx[1], bx[2], bx[3]], [bx[2], bx[3], bx[0], bx[3]], [bx[0], bx[3], bx[0], bx[1]]];
        const ccw = (ax, ay, bx2, by, cx, cy) => (cy - ay) * (bx2 - ax) > (by - ay) * (cx - ax);
        return edges.some(([x3, y3, x4, y4]) => ccw(sg.x1, sg.y1, x3, y3, x4, y4) !== ccw(sg.x2, sg.y2, x3, y3, x4, y4) && ccw(sg.x1, sg.y1, sg.x2, sg.y2, x3, y3) !== ccw(sg.x1, sg.y1, sg.x2, sg.y2, x4, y4));
      };
      for (const epic of model.epics) {
        const o = layout.pos.get(epic.ref);
        const hx = px(o.x);
        const hy = py(o.y);
        if (!near(hx, hy, 30)) continue;
        const dist = Math.hypot(o.x, o.y);
        const base = Math.atan2(-o.y, -o.x);
        const full = `${codeOf(epic)} ${epic.title}`;
        // The whole name if there is room, a narrower one if not, and the code alone as a last resort.
        const variants = [wrap(full, 16, 3), wrap(full, 11, 4), [codeOf(epic)]];
        let best = null;
        let lines = variants[0];
        for (const v of variants) {
          const w = textWidth(v);
          const hh = v.length * LINE;
          for (const turn of [0, 0.6, -0.6, 1.2, -1.2, 1.8, -1.8, Math.PI]) {
            const ux = Math.cos(base + turn);
            const uy = Math.sin(base + turn);
            for (let t = o.r + 12; t < Math.max(dist - layout.rProj - 6, o.r + 40); t += 7) {
              const cx = px(o.x + ux * t);
              const cy = py(o.y + uy * t);
              if (Math.hypot(cx - hx, cy - hy) > 190) break;
              const bx = [cx - w / 2, cy - hh / 2, cx + w / 2, cy + hh / 2];
              if (bx[0] < 4 || bx[2] > box.width - 4 || bx[1] < 4 || bx[3] > box.height - 4) continue;
              if (overlaps(bx) || hitsCircle(bx, 'ring')) continue;
              const crossings = spokes.filter((sg) => sg.ref !== epic.ref && segHitsBox(sg, bx)).length;
              const cost = t + Math.abs(turn) * 10 + crossings * 30;
              if (!best || cost < best.cost) best = { cost, bx, ux, uy, t };
              break;
            }
          }
          if (best) {
            lines = v;
            break;
          }
        }
        if (!best) continue;
        placed.push(best.bx);
        out.push({ epic: true, ref: epic.ref, o, ux: best.ux, uy: best.uy, along: best.t, lines });
      }
    }

    // A name sits just beyond its dot, away from its parent. The item looked at and what it is linked to
    // may also sit a little to either side, or further out, if that is where there is room.
    const settle = (c, turns, gaps, clear) => {
      const w = textWidth(c.lines);
      const hh = c.lines.length * LINE;
      const rpx = c.o.r * sc * k;
      const base = Math.atan2(c.uy, c.ux);
      let best = null;
      for (const turn of turns) {
        const ux = Math.cos(base + turn);
        const uy = Math.sin(base + turn);
        const reach = (Math.abs(ux) * w) / 2 + (Math.abs(uy) * hh) / 2;
        for (const gp of gaps) {
          const dist = rpx + gp + reach;
          const cx = px(c.o.x) + ux * dist;
          const cy = py(c.o.y) + uy * dist;
          const bx = [cx - w / 2, cy - hh / 2, cx + w / 2, cy + hh / 2];
          if (bx[0] < 2 || bx[2] > box.width - 2 || bx[1] < 2 || bx[3] > box.height - 2) continue;
          if (overlaps(bx) || (clear && hitsCircle(bx))) continue;
          const cost = gp + Math.abs(turn) * 10;
          if (!best || cost < best.cost) best = { cost, bx, ux, uy, dist };
          break;
        }
      }
      return best;
    };
    for (let c of cands) {
      const prime = c.prio === 1;
      const sideways = c.o.kind === 'story' ? [0, 0.5, -0.5, 1, -1] : [0];
      let best = prime
        ? settle(c, [0, 0.6, -0.6, 1.2, -1.2, 1.8, -1.8, Math.PI], [4, 12, 24, 40], true) || settle(c, [0, 0.6, -0.6, 1.2, -1.2, Math.PI], [4, 16, 32], false)
        : settle(c, sideways, [4], true);
      if (!best && !prime && c.lines[0].length > 5) {
        // No clear room for the whole name: the code alone, which is short enough to fit.
        c = { ...c, lines: [codeOf(model.node(c.ref))] };
        best = settle(c, sideways, [4], true);
      }
      if (!best) continue;
      placed.push(best.bx);
      out.push({ ...c, ux: best.ux, uy: best.uy, dist: best.dist });
    }

    put(names);
    for (const c of out) {
      const wx = c.epic ? c.o.x + c.ux * c.along : c.o.x + c.ux * c.dist * inv;
      const wy = c.epic ? c.o.y + c.uy * c.along : c.o.y + c.uy * c.dist * inv;
      const g = s('g', { class: `graph-st-name${c.epic ? ' is-epic' : ''}${named.has(c.ref) ? ' is-lit' : ''}`, transform: `translate(${R1(wx)} ${R1(wy)}) scale(${inv})` });
      const first = (-(c.lines.length - 1) * LINE) / 2 + 4.2;
      c.lines.forEach((line, i) => {
        const text = s('text', { x: 0, y: R1(first + i * LINE), 'text-anchor': 'middle' });
        const found = /^([EST]-\d+)( |$)/.exec(i === 0 ? line : '');
        if (found) text.append(s('tspan', { class: 'graph-st-code' }, found[1]), line.slice(found[1].length));
        else text.append(line);
        g.append(text);
      });
      names.append(g);
    }
    // Epics keep their code inside the hub, whatever the labels are set to, and the project its name.
    for (const epic of model.epics) {
      const o = layout.pos.get(epic.ref);
      const fs = Math.max(7, Math.min(12, (o.r * sc * k * 2) / 3.8));
      const g = s('g', { class: 'graph-st-name is-hub', transform: `translate(${R1(o.x)} ${R1(o.y)}) scale(${inv})` });
      const text = s('text', { x: 0, y: R1(fs * 0.36), 'text-anchor': 'middle' }, codeOf(epic));
      text.style.fontSize = `${R1(fs)}px`;
      g.append(text);
      names.append(g);
    }
    if (name) {
      const pf = Math.max(8, Math.min(12, (layout.rProj * sc * k * 2) / 8.4));
      const lines = wrap(name, 11, 2);
      const g = s('g', { class: 'graph-st-name is-project', transform: `scale(${inv})` });
      lines.forEach((line, i) => {
        const text = s('text', { x: 0, y: R1((i - (lines.length - 1) / 2) * pf * 1.2 + pf * 0.36), 'text-anchor': 'middle' }, line);
        text.style.fontSize = `${R1(pf)}px`;
        g.append(text);
      });
      names.append(g);
    }
  }

  // -- the parts round the picture --
  let frame = 0;
  const later = () => {
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; drawNames(); });
  };
  stop.push(() => frame && cancelAnimationFrame(frame));
  const applyView = () => {
    vp.setAttribute('transform', `translate(${R1(whole.x)} ${R1(whole.y)}) scale(${Math.round(whole.k * 1000) / 1000})`);
    later();
  };
  const zoomAround = (f, cx, cy) => {
    const k2 = Math.min(MOST_ZOOM, Math.max(LEAST_ZOOM, whole.k * f));
    whole.x = cx - (cx - whole.x) * (k2 / whole.k);
    whole.y = cy - (cy - whole.y) * (k2 / whole.k);
    whole.k = k2;
    applyView();
  };
  const middle = { x: vb.x + vb.w / 2, y: vb.y + vb.h / 2 };
  const centreOn = (ref, k) => {
    const o = layout.pos.get(ref);
    if (!o) return;
    whole.k = k;
    whole.x = middle.x - o.x * k;
    whole.y = middle.y - o.y * k;
    applyView();
  };
  const small = (label, fn, text) => h('button', { class: 'small icon-only', type: 'button', 'aria-label': label, title: label, onclick: fn }, text);
  const zoomButtons = h(
    'div',
    { class: 'graph-st-zoom', role: 'group', 'aria-label': 'Zoom and move' },
    sel ? h('button', { class: 'small', type: 'button', 'data-action': 'zoom-chosen', onclick: () => centreOn(sel, Math.max(whole.k, layout.pos.get(sel).kind === 'task' ? 4.2 : 2.4)) }, icon('search', { size: 14 }), 'Zoom to chosen') : null,
    small('Zoom out', () => zoomAround(1 / 1.4, middle.x, middle.y), '\u2212'),
    h('button', { class: 'small', type: 'button', 'aria-label': 'Fit the whole project in view', onclick: () => { whole.k = 1; whole.x = 0; whole.y = 0; applyView(); } }, 'Fit'),
    small('Zoom in', () => zoomAround(1.4, middle.x, middle.y), '+'),
  );
  const tip = h('div', { class: 'graph-x-tip', hidden: true, role: 'presentation' });
  const inner = h('div', { class: 'graph-st-inner' }, svg, tip);
  const canvas = h('div', { class: 'graph-x-canvas graph-st-canvas' }, inner, zoomButtons);

  const showTip = (el) => {
    const ref = el.getAttribute('data-ref');
    const r = el.querySelector('.graph-st-body').getBoundingClientRect();
    const c = inner.getBoundingClientRect();
    put(tip, tipContent(model, ref));
    tip.hidden = false;
    const left = Math.min(Math.max(r.left - c.left + r.width / 2, 130), Math.max(c.width - 130, 130));
    const above = r.top - c.top > 120;
    tip.classList.toggle('is-below', !above);
    tip.style.left = `${Math.round(left)}px`;
    tip.style.top = `${Math.round(above ? r.top - c.top - 10 : r.bottom - c.top + 10)}px`;
  };
  const setHover = (ref, el) => {
    const was = hover;
    hover = ref;
    if (ref) showTip(el);
    else tip.hidden = true;
    const wasEpic = was ? hubOf(was) : null;
    const nowEpic = ref ? hubOf(ref) : null;
    if (wasEpic !== nowEpic && cfg.labels === 'zoom') later();
  };
  const siblingsOf = (ref) => {
    const node = model.node(ref);
    return node.parent ? visibleKids(node.parent) : model.epics;
  };
  for (const el of svg.querySelectorAll('.graph-st-node')) {
    const ref = el.getAttribute('data-ref');
    opensItsPage(el, ref);
    el.addEventListener('keydown', (e) => {
      if (['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp'].includes(e.key)) {
        let target = null;
        if (e.key === 'ArrowDown') target = visibleKids(ref)[0]?.ref;
        else if (e.key === 'ArrowUp') target = model.node(ref).parent;
        else {
          const sibs = siblingsOf(ref);
          const at = sibs.findIndex((x) => x.ref === ref);
          target = sibs[(at + (e.key === 'ArrowRight' ? 1 : -1) + sibs.length) % sibs.length]?.ref;
        }
        const next = target && svg.querySelector(`.graph-st-node[data-ref="${target}"]`);
        if (next) {
          e.preventDefault();
          el.setAttribute('tabindex', '-1');
          next.setAttribute('tabindex', '0');
          next.focus();
        }
      }
    });
    el.addEventListener('pointerenter', () => setHover(ref, el));
    el.addEventListener('pointerleave', () => setHover(null, el));
    el.addEventListener('focus', () => setHover(ref, el));
    el.addEventListener('blur', () => setHover(null, el));
  }
  let drag = null;
  svg.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.graph-st-node') || e.button !== 0) return;
    drag = { sx: e.clientX, sy: e.clientY, x: whole.x, y: whole.y, moved: false };
    svg.setPointerCapture(e.pointerId);
    svg.classList.add('is-grabbing');
  });
  svg.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const sc = svg.getScreenCTM().a;
    if (Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) > 4) drag.moved = true;
    whole.x = drag.x + (e.clientX - drag.sx) / sc;
    whole.y = drag.y + (e.clientY - drag.sy) / sc;
    vp.setAttribute('transform', `translate(${R1(whole.x)} ${R1(whole.y)}) scale(${Math.round(whole.k * 1000) / 1000})`);
  });
  svg.addEventListener('pointerup', () => {
    if (!drag) return;
    const moved = drag.moved;
    drag = null;
    svg.classList.remove('is-grabbing');
    if (moved) later();
  });
  svg.addEventListener('pointercancel', () => { drag = null; svg.classList.remove('is-grabbing'); });
  svg.addEventListener('wheel', (e) => {
    e.preventDefault();
    const ctm = svg.getScreenCTM();
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const u = pt.matrixTransform(ctm.inverse());
    zoomAround(Math.exp(-e.deltaY * 0.0016), u.x, u.y);
  }, { passive: false });

  if (whole.k > 1 && whole.x === 0 && whole.y === 0 && sel) centreOn(sel, whole.k);
  else applyView();
  watch(inner, later);

  // -- the page round it --
  const epicLegend = model.epics.map((epic, i) =>
    h('li', { class: 'graph-key-chip is-button' }, h('button', { class: `graph-x-legend-button ${hueOf(i)}`, type: 'button', title: `${codeOf(epic)} ${epic.title}`, onclick: () => mark(epic.ref) }, h('span', { class: 'graph-x-swatch' }), codeOf(epic))),
  );
  // How many of the finest level drawn are in each status: the dots of the map, counted.
  const finest = { epics: 'epic', stories: 'story', tasks: 'task' }[cfg.level];
  const counts = {};
  for (const o of layout.nodes) if (o.kind === finest) counts[model.node(o.ref).status] = (counts[model.node(o.ref).status] || 0) + 1;
  const key = viewKey({
    label: 'How to read the map',
    groups: [
      { label: 'Shapes', chips: [...shapeChips({ stories: cfg.level !== 'epics', tasks: cfg.level === 'tasks' }), biggerChip('Bigger dot, higher priority'), lateChip('Amber ring: overdue', 'ring'), keyChip(h('span', { class: 'graph-x-late is-ring is-danger' }), 'Red ring: blocked')] },
      cfg.colour === 'epic' ? { label: 'Colour by epic', chips: [...epicLegend, startedChip()] } : { label: 'Colour is the status', chips: mapStatusChips(counts, cfg.level) },
      cfg.links || sel ? { label: 'Lines', chips: linkChips() } : null,
    ],
  });
  const toolbar = h(
    'div',
    { class: 'graph-st-toolbar' },
    lookAt(model, null),
    segment('Show', 'Which levels to draw', LEVELS, cfg.level, (v) => setLook({ level: v === 'tasks' && model.list.length <= MOST_DRAWN ? null : v }), 'level'),
    segment('Colour by', 'What the colour means', COLOURS, cfg.colour, (v) => setLook({ colour: v === 'status' ? null : v }), 'colour'),
    segment('Labels', 'When to show names', LABEL_MODES, cfg.labels, (v) => setLook({ labels: v === 'zoom' ? null : v }), 'labels'),
    toggle('Show links between items', 'link', cfg.links, () => setLook({ links: cfg.links ? null : '1' }), { title: 'Needs first, duplicates, related and references, as soft curves', 'data-choice': 'links' }),
    key,
  );
  return h(
    'section',
    { class: 'graph-x-panel graph-st-main', 'aria-labelledby': 'graph-st-title' },
    h('div', { class: 'graph-x-head' }, h('h2', { id: 'graph-st-title' }, 'Structure of the whole project')),
    toolbar,
    canvas,
  );
}

// Calls fn whenever the element's size changes; undone when the next drawing replaces this one.
function watch(el, fn) {
  const observer = new ResizeObserver(() => fn());
  observer.observe(el);
  stop.push(() => observer.disconnect());
}

// -- one epic --------------------------------------------------------------------------------------

function epicPage(model, epicRef, chosen) {
  const epic = model.node(epicRef);
  const tree = treeOf(model).find((one) => one.ref === epicRef);
  const L = epicRadial(tree);
  if (ring.epic !== epicRef) {
    ring.epic = epicRef;
    ring.zoom = 1;
    ring.x = 0;
    ring.y = 0;
  }
  const inside = (ref) => L.pos.has(ref);
  const sel = chosen && inside(chosen) ? chosen : null;
  const rows = sel ? linksOf(sel).map((row) => ({ ...row, inside: inside(row.other) })) : [];
  const linked = new Set(rows.filter((row) => row.inside).map((row) => row.other));
  const selStory = sel && model.node(sel).kind === 'story' ? sel : null;

  const svg = s('svg', { class: 'graph-rd-svg', role: 'group', 'aria-label': `Radial tree of ${codeOf(epic)}: stories in a ring, tasks on the outside` });
  const tip = h('div', { class: 'graph-x-tip', hidden: true, role: 'presentation' });
  const inner = h('div', { class: 'graph-rd-inner' }, svg, tip);
  const scroller = h('div', { class: 'graph-rd-scroll', tabindex: '0', role: 'region', 'aria-label': `The tree of ${codeOf(epic)}. It scrolls sideways on a small screen.` }, inner);
  const note = h('div', { class: 'graph-rd-note-holder' });
  const small = (label, fn, text) => h('button', { class: 'small icon-only', type: 'button', 'aria-label': label, title: label, onclick: fn }, text);
  const controls = h(
    'div',
    { class: 'graph-st-zoom', role: 'group', 'aria-label': 'Zoom' },
    small('Zoom out', () => { ring.zoom = Math.max(0.6, R1(ring.zoom - 0.2)); redraw(); }, '\u2212'),
    h('button', { class: 'small', type: 'button', 'aria-label': 'Fit the whole tree in view', onclick: () => { ring.zoom = 1; ring.x = 0; ring.y = 0; redraw(); } }, 'Fit'),
    small('Zoom in', () => { ring.zoom = Math.min(2.4, R1(ring.zoom + 0.2)); redraw(); }, '+'),
  );
  const canvas = h('div', { class: `graph-x-canvas graph-rd-canvas${sel ? ' has-sel' : ''}` }, controls, scroller, note);
  const hasSquare = L.groups.some((gr) => gr.leafStory);
  const key = L.empty
    ? null
    : viewKey({
        label: 'How to read the tree',
        groups: [
          { label: 'The leaves', chips: [...statusChips(), biggerChip('Bigger leaf, higher priority'), ringChip('Ring round a story: how much is done'), hasSquare ? keyChip(h('span', { class: 'graph-key-square' }), 'Square: a story with no tasks yet') : null, lateChip()].filter(Boolean) },
          rows.some((row) => row.inside) ? { label: 'Lines', chips: linkChips() } : null,
        ],
      });
  const toolbar = h('div', { class: 'graph-st-toolbar' }, lookAt(model, epicRef), key);
  const main = h(
    'section',
    { class: 'graph-x-panel graph-st-main', 'aria-labelledby': 'graph-st-title' },
    h('div', { class: 'graph-x-head' }, h('h2', { id: 'graph-st-title' }, `Focus on ${codeOf(epic)}`)),
    toolbar,
    canvas,
  );

  if (L.empty) {
    controls.remove();
    svg.setAttribute('viewBox', '-340 -170 680 340');
    const hub = s(
      'g',
      { class: 'graph-rd-epic', tabindex: '0', role: 'button', 'data-ref': epicRef, 'aria-label': `${words(model, epicRef)} Press Enter to open it.` },
      s('circle', { class: 'graph-rd-ring', r: 46 }),
      s('circle', { class: 'graph-rd-epic-track', r: 41 }),
      s('circle', { class: 'graph-rd-epic-body', r: 36 }),
      s('text', { class: 'graph-rd-epic-code', 'text-anchor': 'middle', y: 4 }, codeOf(epic)),
    );
    opensItsPage(hub, epicRef);
    svg.append(hub, s('text', { class: 'graph-rd-empty', 'text-anchor': 'middle', y: 78 }, 'Nothing is planned yet: no stories, so no leaves'));
    return main;
  }

  const labelMax = 30;
  const drawTree = (full) => {
    const g = s('g', { class: 'graph-rd-vp', transform: `translate(${R1(ring.x)} ${R1(ring.y)}) scale(${ring.zoom})` });
    const bands = s('g', { class: 'graph-rd-bands' });
    L.groups.forEach((gr, i) => {
      const on = selStory === gr.story.ref || (sel && gr.leaves.some((x) => x.ref === sel));
      bands.append(s('path', { class: `graph-rd-band${i % 2 ? ' is-alt' : ''}${on ? ' is-on' : ''}`, d: sector(L.rS + 20, L.R2 + 16, gr.a0 + 0.004, gr.a1 - 0.004) }));
    });
    g.append(bands);
    const lines = s('g', { class: 'graph-rd-lines' });
    for (const gr of L.groups) {
      const sp = L.pos.get(gr.story.ref);
      lines.append(s('line', { class: 'graph-rd-line', x1: 0, y1: 0, x2: R1(sp.x), y2: R1(sp.y) }));
      if (gr.leafStory) continue;
      for (const leaf of gr.leaves) lines.append(s('path', { class: 'graph-rd-line', d: treeCurve(sp, L.pos.get(leaf.ref)) }));
    }
    g.append(lines);

    // The links of the item looked at only, curved round the middle.
    if (sel) {
      const from = L.pos.get(sel);
      const x = s('g', { class: 'graph-rd-links' });
      for (const row of rows.filter((r) => r.inside)) {
        const to = L.pos.get(row.other);
        const d = angleBetween(from.ang, to.ang);
        const mid = from.ang + d / 2;
        const base = (from.r + to.r) / 2;
        const rc = Math.max(0.1, 1 - 0.9 * Math.pow(Math.abs(d) / Math.PI, 0.65)) * base;
        const [cx, cy] = polar(rc, mid);
        const trim = (p, dist) => {
          const vx = cx - p.x;
          const vy = cy - p.y;
          const len = Math.hypot(vx, vy) || 1;
          return [p.x + (vx / len) * dist, p.y + (vy / len) * dist];
        };
        let a = from;
        let b = to;
        let ra = from.leaf ? 11 : 17;
        let rb = to.leaf ? 11 : 17;
        const arrow = row.type === 'needs_first' || row.type === 'supersedes';
        const swap = (row.type === 'needs_first' && row.dir === 'out') || (row.type === 'supersedes' && row.dir === 'in');
        if (swap) { a = to; b = from; [ra, rb] = [rb, ra]; }
        const [ax, ay] = trim(a, ra);
        const [bx, by] = trim(b, rb + (arrow ? 3 : 0));
        x.append(s('path', { class: `graph-st-link t-${row.type} is-chosen`, d: `M${R1(ax)} ${R1(ay)} Q${R1(cx)} ${R1(cy)} ${R1(bx)} ${R1(by)}`, 'marker-end': arrow ? 'url(#graph-rd-arrow)' : null }));
      }
      g.append(x);
    }

    const nodes = s('g', { class: 'graph-rd-nodes' });
    for (const gr of L.groups) {
      if (gr.leafStory) continue;
      const story = model.node(gr.story.ref);
      const p = L.pos.get(story.ref);
      const pr = model.progress(story.ref);
      const C = 2 * Math.PI * 17;
      const sg = s(
        'g',
        {
          class: `graph-rd-story s-${story.status}${sel === story.ref ? ' is-sel' : ''}${linked.has(story.ref) ? ' is-linked' : ''}`,
          transform: `translate(${R1(p.x)} ${R1(p.y)})`,
          tabindex: story.ref === (sel || rovingStart()) ? '0' : '-1',
          role: 'button',
          'data-ref': story.ref,
          'aria-current': sel === story.ref ? 'true' : null,
          'aria-label': `${words(model, story.ref)} Press Enter to open it.`,
        },
        s('circle', { class: 'graph-rd-ring', r: 21 }),
        s('circle', { class: 'graph-rd-track', r: 17 }),
      );
      if (pr.total) sg.append(s('circle', { class: 'graph-rd-fill', r: 17, 'stroke-dasharray': `${R1((C * pr.done) / pr.total)} ${R1(C)}`, transform: 'rotate(-90)' }));
      sg.append(s('circle', { class: 'graph-rd-body', r: 14 }), s('text', { class: 'graph-rd-code', 'text-anchor': 'middle', dy: '0.34em' }, codeOf(story)));
      nodes.append(sg);
    }
    const ep = model.progress(epicRef);
    const EC = 2 * Math.PI * 41;
    const eg = s(
      'g',
      { class: 'graph-rd-epic', tabindex: '-1', role: 'button', 'data-ref': epicRef, 'aria-label': `${words(model, epicRef)} Press Enter to open it.` },
      s('circle', { class: 'graph-rd-ring', r: 46 }),
      s('circle', { class: 'graph-rd-epic-track', r: 41 }),
    );
    if (ep.total) eg.append(s('circle', { class: 'graph-rd-epic-fill', r: 41, 'stroke-dasharray': `${R1((EC * ep.done) / ep.total)} ${R1(EC)}`, transform: 'rotate(-90)' }));
    eg.append(s('circle', { class: 'graph-rd-epic-body', r: 36 }), s('text', { class: 'graph-rd-epic-code', 'text-anchor': 'middle', y: -3 }, codeOf(epic)), s('text', { class: 'graph-rd-epic-pct', 'text-anchor': 'middle', y: 14 }, ep.total ? `${Math.round((100 * ep.done) / ep.total)}%` : ''));
    nodes.append(eg);
    for (const gr of L.groups) {
      for (const leafOf of gr.leaves) {
        const leaf = model.node(leafOf.ref);
        const p = L.pos.get(leaf.ref);
        const storyLeaf = leaf.kind === 'story';
        const r = storyLeaf ? 8 : LEAF_DOT[leaf.priority] || LEAF_DOT.normal;
        const late = model.late(leaf.ref);
        const lg = s(
          'g',
          {
            class: `graph-rd-leaf${sel === leaf.ref ? ' is-sel' : ''}${linked.has(leaf.ref) ? ' is-linked' : ''}${storyLeaf ? ' is-story' : ''}`,
            transform: `translate(${R1(p.x)} ${R1(p.y)})`,
            tabindex: leaf.ref === (sel || rovingStart()) ? '0' : '-1',
            role: 'button',
            'data-ref': leaf.ref,
            'aria-current': sel === leaf.ref ? 'true' : null,
            'aria-label': `${words(model, leaf.ref)} Press Enter to open it.`,
          },
          s('circle', { class: 'graph-rd-hit', r: 13 }),
        );
        if (storyLeaf) lg.append(s('rect', { class: 'graph-rd-square', x: -r, y: -r, width: r * 2, height: r * 2, rx: 3 }), statusMark(leaf.status, 5.2));
        else lg.append(statusMark(leaf.status, r));
        if (late > 0) lg.append(s('circle', { class: 'graph-rd-late', cx: r * 0.8, cy: -r * 0.8, r: 3.2 }));
        lg.append(s('circle', { class: 'graph-rd-ring', r: r + 4 }));
        nodes.append(lg);
      }
    }
    g.append(nodes);

    // Names last, so they sit over the lines and never under a node.
    const labels = s('g', { class: 'graph-rd-labels', 'aria-hidden': 'true' });
    for (const gr of L.groups) {
      for (const leafOf of gr.leaves) {
        const leaf = model.node(leafOf.ref);
        const p = L.pos.get(leaf.ref);
        const deg = (p.ang * 180) / Math.PI;
        const flip = Math.cos(p.ang) < -0.0001;
        const off = (leaf.kind === 'story' ? 8 : LEAF_DOT[leaf.priority] || LEAF_DOT.normal) + 9;
        const text = full ? clip(`${codeOf(leaf)} ${leaf.title}`, labelMax) : codeOf(leaf);
        labels.append(
          s(
            'g',
            { class: `graph-rd-label${sel === leaf.ref ? ' is-sel' : ''}${linked.has(leaf.ref) ? ' is-linked' : ''}`, transform: `rotate(${R1(deg)}) translate(${R1(p.r + off)} 0)` },
            s('text', { class: 'graph-rd-label-text', transform: flip ? 'rotate(180)' : null, 'text-anchor': flip ? 'end' : 'start', dy: '0.34em' }, text),
          ),
        );
      }
    }
    g.append(labels);
    put(svg, s('defs', {}, s('marker', { id: 'graph-rd-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 11, markerHeight: 11, orient: 'auto', markerUnits: 'userSpaceOnUse' }, s('path', { class: 'graph-st-arrow', d: 'M0 1 L10 5 L0 9 z' }))), g);
    svg.classList.toggle('has-sel', Boolean(sel));
  };
  const rovingStart = () => L.groups[0]?.story.ref;

  let lastWidth = 0;
  const redraw = () => {
    const width = Math.max(inner.clientWidth || 700, 560);
    const ext0 = epicExtent(L, true);
    const full = Math.min(width / ext0.w, 1.4) >= 0.78;
    const ext = epicExtent(L, full);
    svg.setAttribute('viewBox', `${R1(ext.x)} ${R1(ext.y)} ${R1(ext.w)} ${R1(ext.h)}`);
    drawTree(full);
    const outside = sel ? rows.filter((row) => !row.inside) : [];
    put(
      note,
      outside.length
        ? h(
            'div',
            { class: 'graph-rd-note' },
            h('span', { class: 'quiet' }, `${codeOf(model.node(sel))} is also linked outside this epic:`),
            outside.map((row) => {
              const other = model.epicOf(row.other);
              return h('button', { class: 'chip graph-x-chip-link', type: 'button', title: model.node(row.other).title, onclick: () => api.setParams({ scope: other.ref, focus: row.other }) }, icon('arrow_right', { size: 14 }), `${codeOf(model.node(row.other))} in ${codeOf(other)}`);
            }),
          )
        : null,
    );
    wire();
  };
  const showTip = (el) => {
    const ref = el.getAttribute('data-ref');
    const r = el.getBoundingClientRect();
    const c = inner.getBoundingClientRect();
    put(tip, tipContent(model, ref));
    tip.hidden = false;
    tip.style.left = `${Math.round(r.left - c.left + r.width / 2)}px`;
    tip.style.top = `${Math.round(r.top - c.top - 8)}px`;
  };
  const wire = () => {
    // Round the ring in the order of the tree: the epic, its stories, then each story's tasks.
    const order = [epicRef, ...L.groups.flatMap((gr) => (gr.leafStory ? [gr.story.ref] : [gr.story.ref, ...gr.leaves.map((leaf) => leaf.ref)]))];
    for (const el of svg.querySelectorAll('.graph-rd-epic, .graph-rd-story, .graph-rd-leaf')) {
      const ref = el.getAttribute('data-ref');
      opensItsPage(el, ref);
      el.addEventListener('keydown', (e) => {
        if (['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp'].includes(e.key)) {
          const at = order.indexOf(ref);
          const step = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : -1;
          const next = svg.querySelector(`[data-ref="${order[(at + step + order.length) % order.length]}"]`);
          if (next) {
            e.preventDefault();
            el.setAttribute('tabindex', '-1');
            next.setAttribute('tabindex', '0');
            next.focus();
          }
        }
      });
      el.addEventListener('pointerenter', () => showTip(el));
      el.addEventListener('pointerleave', () => { tip.hidden = true; });
      el.addEventListener('focus', () => showTip(el));
      el.addEventListener('blur', () => { tip.hidden = true; });
    }
    let drag = null;
    svg.onpointerdown = (e) => {
      if (e.target.closest('.graph-rd-epic, .graph-rd-story, .graph-rd-leaf') || e.button !== 0) return;
      drag = { x: e.clientX - ring.x, y: e.clientY - ring.y };
      svg.setPointerCapture(e.pointerId);
      svg.classList.add('is-grabbing');
    };
    svg.onpointermove = (e) => {
      if (!drag) return;
      ring.x = e.clientX - drag.x;
      ring.y = e.clientY - drag.y;
      svg.querySelector('.graph-rd-vp').setAttribute('transform', `translate(${R1(ring.x)} ${R1(ring.y)}) scale(${ring.zoom})`);
    };
    svg.onpointerup = () => { drag = null; svg.classList.remove('is-grabbing'); };
  };
  redraw();
  lastWidth = inner.clientWidth;
  // On a small screen the tree is wider than the window: start with its middle in view.
  if (scroller.scrollWidth > scroller.clientWidth) scroller.scrollLeft = (scroller.scrollWidth - scroller.clientWidth) / 2;
  watch(inner, () => {
    const w = inner.clientWidth;
    if (Math.abs(w - lastWidth) > 10) {
      lastWidth = w;
      redraw();
    }
  });
  return main;
}
