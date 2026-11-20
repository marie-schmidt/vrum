/* The proposed Graph page, as a clickable mockup. Classic script, no library, no network.
   Everything on the page is drawn from window.VRUM_DEMO (data.js): the same facts feed the diagram,
   the list and the agent view, which is the point of the proposal. */
(function () {
  'use strict';

  const D = window.VRUM_DEMO;
  const NS = 'http://www.w3.org/2000/svg';
  const TWO_PI = Math.PI * 2;

  /* ===== 1. small helpers ================================================================================ */

  function add(node, kids) {
    for (const k of kids.flat(Infinity)) {
      if (k === null || k === undefined || k === false) continue;
      node.append(k.nodeType ? k : document.createTextNode(String(k)));
    }
  }
  function build(node, attrs, kids) {
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : String(v));
    }
    add(node, kids);
    return node;
  }
  const h = (tag, attrs, ...kids) => build(document.createElement(tag), attrs, kids);
  const sv = (tag, attrs, ...kids) => build(document.createElementNS(NS, tag), attrs, kids);
  const put = (el, ...kids) => {
    el.replaceChildren();
    add(el, kids);
    return el;
  };
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const trunc = (s, n) => (s.length <= n ? s : s.slice(0, n - 1).trimEnd() + '\u2026');
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + 's'}`;
  const polar = (r, a) => [r * Math.cos(a), r * Math.sin(a)];
  const r1 = (n) => Math.round(n * 10) / 10;
  const uniq = (list) => Array.from(new Set(list));
  const sum = (list) => list.reduce((a, b) => a + b, 0);

  // Words wrapped to at most `lines` lines of `max` characters; the last line is cut with an ellipsis.
  function wrap(text, max, lines) {
    const words = text.split(/\s+/);
    const out = [];
    let line = '';
    for (const w of words) {
      if ((line + ' ' + w).trim().length > max && line) {
        out.push(line);
        line = w;
      } else {
        line = (line + ' ' + w).trim();
      }
    }
    if (line) out.push(line);
    if (out.length > lines) {
      const keep = out.slice(0, lines);
      keep[lines - 1] = trunc(out.slice(lines - 1).join(' '), max);
      return keep;
    }
    return out;
  }

  let toastTimer = null;
  function toast(message) {
    const t = $('#toast');
    t.textContent = message;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), 3400);
  }
  const NOT_HERE = 'Only the Graph page is part of this mockup.';

  /* ===== 2. icons (the real set, plus a few the proposal needs, marked "new") ============================== */

  const ln = (x1, y1, x2, y2) => ['line', { x1, y1, x2, y2 }];
  const ci = (cx, cy, r, extra) => ['circle', Object.assign({ cx, cy, r }, extra || {})];
  const pa = (d, extra) => ['path', Object.assign({ d }, extra || {})];
  const rc = (x, y, width, height, rx) => ['rect', { x, y, width, height, rx: rx === undefined ? 2 : rx }];

  const ICONS = {
    board: [rc(3, 3, 18, 18), ln(9, 3, 9, 21), ln(15, 3, 15, 21)],
    graph: [ci(18, 5, 3), ci(6, 12, 3), ci(18, 19, 3), ln(8.6, 13.5, 15.4, 17.5), ln(15.4, 6.5, 8.6, 10.5)],
    roadmap: [rc(3, 4, 9, 4, 1), rc(8, 10, 9, 4, 1), rc(13, 16, 8, 4, 1)],
    activity: [pa('M22 12h-4l-3 9L9 3l-3 9H2')],
    settings: [ln(4, 6, 14, 6), ln(18, 6, 20, 6), ci(16, 6, 2), ln(4, 12, 6, 12), ln(10, 12, 20, 12), ci(8, 12, 2), ln(4, 18, 14, 18), ln(18, 18, 20, 18), ci(16, 18, 2)],
    search: [ci(11, 11, 7), ln(21, 21, 16.65, 16.65)],
    menu: [ln(4, 6, 20, 6), ln(4, 12, 20, 12), ln(4, 18, 20, 18)],
    filter: [ln(3, 6, 21, 6), ln(6, 12, 18, 12), ln(10, 18, 14, 18)],
    epic: [pa('M12 2 2 7l10 5 10-5z'), pa('M2 17l10 5 10-5'), pa('M2 12l10 5 10-5')],
    story: [rc(5, 3, 14, 18), ln(9, 8, 15, 8), ln(9, 12, 15, 12), ln(9, 16, 12.5, 16)],
    task: [rc(4, 4, 16, 16, 3), pa('M8.5 12.2l2.4 2.4 4.6-5')],
    rule: [pa('M9 3h6v3H9z'), rc(5, 5, 14, 16), pa('M9 14l2 2 4-4')],
    decision: [pa('M5 21V4'), pa('M5 4h12l-2.5 4 2.5 4H5')],
    fact: [pa('M12 3l9 9-9 9-9-9z'), ci(12, 12, 1.2, { fill: 'currentColor' })],
    new: [ci(12, 12, 9)],
    ready: [ci(12, 12, 9), ci(12, 12, 3, { fill: 'currentColor' })],
    in_progress: [ci(12, 12, 9), pa('M12 3a9 9 0 0 1 0 18z', { fill: 'currentColor' })],
    blocked: [ci(12, 12, 9), ln(5.6, 5.6, 18.4, 18.4)],
    done: [ci(12, 12, 9), pa('M8 12.4l2.8 2.8L16.4 9.2')],
    dropped: [ci(12, 12, 9), ln(8, 12, 16, 12)],
    low: [pa('M12 5v14'), pa('M19 12l-7 7-7-7')],
    normal: [ln(5, 12, 19, 12)],
    high: [pa('M12 19V5'), pa('M5 12l7-7 7 7')],
    urgent: [pa('M17 11l-5-5-5 5'), pa('M17 18l-5-5-5 5')],
    plus: [ln(12, 5, 12, 19), ln(5, 12, 19, 12)],
    close: [ln(18, 6, 6, 18), ln(6, 6, 18, 18)],
    check: [pa('M20 6L9 17l-5-5')],
    chevron_down: [pa('M6 9l6 6 6-6')],
    chevron_up: [pa('M18 15l-6-6-6 6')],
    chevron_right: [pa('M9 6l6 6-6 6')],
    arrow_right: [ln(5, 12, 19, 12), pa('M13 6l6 6-6 6')],
    link: [pa('M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71'), pa('M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71')],
    lock: [rc(5, 11, 14, 10), pa('M8 11V7a4 4 0 0 1 8 0v4')],
    user: [ci(12, 8, 4), pa('M4 21a8 8 0 0 1 16 0')],
    calendar: [rc(3, 5, 18, 16), ln(16, 3, 16, 7), ln(8, 3, 8, 7), ln(3, 11, 21, 11)],
    clock: [ci(12, 12, 9), pa('M12 7v5l3 2')],
    alert: [ci(12, 12, 9), ln(12, 8, 12, 13), ln(12, 16.5, 12, 16.6)],
    info: [ci(12, 12, 9), ln(12, 11, 12, 16.5), ln(12, 7.6, 12, 7.7)],
    confirmed: [ci(12, 12, 9), pa('M8 12.4l2.8 2.8L16.4 9.2')],
    unconfirmed: [ci(12, 12, 9, { 'stroke-dasharray': '3 3' })],
    sun: [ci(12, 12, 4), ln(12, 2, 12, 4), ln(12, 20, 12, 22), ln(4.9, 4.9, 6.3, 6.3), ln(17.7, 17.7, 19.1, 19.1), ln(2, 12, 4, 12), ln(20, 12, 22, 12), ln(4.9, 19.1, 6.3, 17.7), ln(17.7, 6.3, 19.1, 4.9)],
    moon: [pa('M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z')],
    monitor: [rc(2, 3, 20, 14), ln(8, 21, 16, 21), ln(12, 17, 12, 21)],
    // new in this proposal
    arrow_left: [ln(19, 12, 5, 12), pa('M11 6l-6 6 6 6')],
    agent: [pa('M8 8l-4 4 4 4'), pa('M16 8l4 4-4 4'), ln(13.5, 6, 10.5, 18)],
    hier: [ci(12, 5, 2.2), ci(6, 19, 2.2), ci(18, 19, 2.2), pa('M12 7.2v4.3M12 11.5H6v5.3M12 11.5h6v5.3')],
    around: [ci(12, 12, 3), ci(12, 4, 1.6), ci(20, 12, 1.6), ci(12, 20, 1.6), ci(4, 12, 1.6)],
    structure: [ci(12, 12, 2.4), pa('M12 9.6V4M14 13.4l4.6 3.2M10 13.4l-4.6 3.2'), ci(12, 3, 1.5), ci(19.5, 17.5, 1.5), ci(4.5, 17.5, 1.5)],
    order: [rc(3, 4, 7, 5, 1.5), rc(14, 15, 7, 5, 1.5), pa('M10 6.5h2.5a2.5 2.5 0 0 1 2.5 2.5v6')],
    timeline: [ln(3, 12, 21, 12), ci(7, 12, 2), ci(13, 12, 2), ci(18, 12, 2)],
    compare: [rc(3, 5, 8, 14, 2), rc(13, 5, 8, 14, 2)],
    notes: [pa('M5 4h14v12H9l-4 4z'), ln(9, 9, 15, 9), ln(9, 12, 13, 12)],
  };
  const KIND_ICON = { epic: 'epic', story: 'story', task: 'task', rule: 'rule', decision: 'decision', fact: 'fact' };

  function icon(name, size, cls, label) {
    const parts = ICONS[name] || ICONS.info;
    const svg = sv('svg', {
      class: 'icon icon-' + name + (cls ? ' ' + cls : ''),
      width: size || 16,
      height: size || 16,
      viewBox: '0 0 24 24',
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': '1.75',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
      focusable: 'false',
      role: label ? 'img' : null,
      'aria-label': label || null,
      'aria-hidden': label ? null : 'true',
    });
    parts.forEach(([tag, attrs]) => svg.append(sv(tag, attrs)));
    return svg;
  }
  $$('i[data-icon]').forEach((i) => i.replaceWith(icon(i.dataset.icon, Number(i.dataset.size) || 16, i.className)));

  /* ===== 3. the model ====================================================================================== */

  const todayMs = Date.parse(D.today + 'T00:00:00Z');
  const DAY = 86400000;
  const byCode = new Map(D.nodes.map((n) => [n.code, n]));
  const epics = D.nodes.filter((n) => n.kind === 'epic');
  const kidMap = new Map();
  D.nodes.forEach((n) => {
    if (n.parent) {
      if (!kidMap.has(n.parent)) kidMap.set(n.parent, []);
      kidMap.get(n.parent).push(n);
    }
  });
  const kidsOf = (code) => kidMap.get(code) || [];
  const epicOf = (n) => {
    let x = n;
    while (x.parent) x = byCode.get(x.parent);
    return x;
  };
  const pushTo = (m, k, v) => {
    if (!m.has(k)) m.set(k, []);
    m.get(k).push(v);
  };
  const needsMap = new Map(); // a needs b first: a -> [b]
  const neededBy = new Map(); // b -> [a]
  const relMap = new Map(); // code -> [{type, other, dir}]
  D.links.forEach(([type, a, b]) => {
    if (type === 'needs') {
      pushTo(needsMap, a, b);
      pushTo(neededBy, b, a);
    }
    pushTo(relMap, a, { type, other: b, dir: 'out' });
    pushTo(relMap, b, { type, other: a, dir: 'in' });
  });
  const decById = new Map(D.decisions.map((d) => [d.id, d]));
  // A text that names its own parent or child says nothing the hierarchy does not: only the others are references.
  const mentionPairs = D.mentions.filter(([a, b]) => byCode.get(a).parent !== b && byCode.get(b).parent !== a);
  const mentionMap = new Map(); // code -> [{other, dir}]
  mentionPairs.forEach(([a, b]) => { pushTo(mentionMap, a, { other: b, dir: 'out' }); pushTo(mentionMap, b, { other: a, dir: 'in' }); });
  const factById = new Map(D.facts.map((f) => [f.id, f]));
  const ruleById = new Map(D.rules.map((r) => [r.id, r]));

  const isEnded = (status) => status === 'done' || status === 'dropped';
  const lateDays = (n) => (n.due && !isEnded(n.status) ? Math.round((todayMs - Date.parse(n.due + 'T00:00:00Z')) / DAY) : 0);
  const waitingOn = (n) => (needsMap.get(n.code) || []).filter((c) => !isEnded(byCode.get(c).status));
  // The status the board would show: a new item with nothing unfinished before it is Ready.
  const shown = (n) => (n.status === 'new' ? (waitingOn(n).length ? 'new' : 'ready') : n.status);
  const tasksUnder = (n) => (n.kind === 'task' ? [n] : kidsOf(n.code).flatMap((k) => tasksUnder(k)));
  function progress(n) {
    const live = tasksUnder(n).filter((t) => t.status !== 'dropped');
    return { done: live.filter((t) => t.status === 'done').length, total: live.length };
  }
  const refOf = (code) => ({ E: 'epic', S: 'story', T: 'task' })[code[0]] + ':' + code.slice(2);
  const hasLink = (a, b) => (relMap.get(a) || []).some((r) => r.other === b);
  const KIND_LABEL = { epic: 'Epic', story: 'Story', task: 'Task', rule: 'Requirement', decision: 'Decision', fact: 'Fact' };
  const STATUS_LABEL = { new: 'New', ready: 'Ready', in_progress: 'In progress', blocked: 'Blocked', done: 'Done', dropped: 'Dropped' };
  const PRIORITY_WEIGHT = { low: 0, normal: 1, high: 2, urgent: 3 };

  // Work that needs attention: blocked or overdue, the most urgent first.
  function attentionItems() {
    return D.nodes
      .filter((n) => n.kind !== 'epic' && !isEnded(n.status) && (n.status === 'blocked' || lateDays(n) > 0))
      .map((n) => ({ node: n, score: (n.status === 'blocked' ? 4 : 0) + PRIORITY_WEIGHT[n.priority] * 1.5 + Math.min(lateDays(n), 30) / 10 + (n.kind === 'task' ? 0.2 : 0) }))
      .sort((a, b) => b.score - a.score)
      .slice(0, 5);
  }

  /* ===== 4. state and the page's address ================================================================== */

  const state = {
    view: 'around',
    focus: 'E-1',
    trail: ['E-1'],
    reach: 1,
    agent: false,
    peek: null,
    expanded: {},
    wideOpen: false,
    tree: new Set(),
    open: null, // structure: the one epic being focused on; null is the whole project
    sel: null, // structure: the chosen item (any epic, story or task)
    zoom: 1, // the one-epic view's own zoom and pan
    pan: { x: 0, y: 0 },
    wp: { level: 'tasks', color: 'status', labels: 'zoom', links: false, k: 1, x: 0, y: 0 }, // structure: the whole project
    order: { sel: null, only: false, blocked: false },
    time: { kind: 'all', since: false, about: false, sel: null, all: false },
    checkup: null,
    mapMode: 'work',
    mapSel: null,
    notes: false,
    compare: false,
  };

  function readHash() {
    if (!location.hash || location.hash.length < 2) return;
    const p = new URLSearchParams(location.hash.slice(1));
    if (p.has('view')) state.view = p.get('view');
    if (p.has('focus')) {
      const f = p.get('focus');
      state.focus = byCode.has(f) ? f : null;
      state.trail = state.focus ? [state.focus] : [];
      if (state.focus) {
        const k = byCode.get(state.focus).kind;
        state.sel = state.focus;
        state.order.sel = k === 'epic' ? null : state.focus;
      }
    }
    if (p.has('trail')) state.trail = p.get('trail').split(',').filter((c) => byCode.has(c));
    if (p.has('reach')) state.reach = p.get('reach') === '2' ? 2 : 1;
    if (p.get('agent') === '1') state.agent = true;
    if (p.has('open')) state.open = byCode.has(p.get('open')) ? p.get('open') : null;
    if (p.has('sel')) state.sel = byCode.has(p.get('sel')) ? p.get('sel') : null;
    if (p.has('lv') && ['epics', 'stories', 'tasks'].includes(p.get('lv'))) state.wp.level = p.get('lv');
    if (p.get('col') === 'epic') state.wp.color = 'epic';
    if (p.has('lab') && ['off', 'zoom', 'always'].includes(p.get('lab'))) state.wp.labels = p.get('lab');
    if (p.get('xl') === '1') state.wp.links = true;
    if (p.has('k')) state.wp.k = Math.min(8, Math.max(0.6, Number(p.get('k')) || 1));
    if (p.has('at') && byCode.has(p.get('at'))) state.wp.at = p.get('at');
    if (p.has('osel')) state.order.sel = byCode.has(p.get('osel')) ? p.get('osel') : null;
    if (p.get('only') === '1') state.order.only = true;
    if (p.get('blocked') === '1') state.order.blocked = true;
    if (p.has('checkup')) state.checkup = p.get('checkup');
    if (p.has('map')) state.mapMode = p.get('map');
    if (p.get('notes') === '1') state.notes = true;
    if (p.get('compare') === '1') state.compare = true;
    if (p.get('wide') === '1') state.wideOpen = true;
    if (p.has('peek')) state.peek = p.get('peek');
    if (p.get('since') === '1') state.time.since = true;
    if (p.has('tsel')) state.time.sel = p.get('tsel');
    if (p.has('tkind')) state.time.kind = p.get('tkind');
  }
  function writeHash() {
    const p = new URLSearchParams();
    p.set('view', state.view);
    p.set('focus', state.focus || 'none');
    if (state.trail.length > 1) p.set('trail', state.trail.join(','));
    if (state.reach === 2) p.set('reach', '2');
    if (state.agent) p.set('agent', '1');
    if (state.open) p.set('open', state.open);
    if (state.sel) p.set('sel', state.sel);
    if (state.wp.level !== 'tasks') p.set('lv', state.wp.level);
    if (state.wp.color !== 'status') p.set('col', state.wp.color);
    if (state.wp.labels !== 'zoom') p.set('lab', state.wp.labels);
    if (state.wp.links) p.set('xl', '1');
    if (state.order.sel) p.set('osel', state.order.sel);
    if (state.order.only) p.set('only', '1');
    if (state.order.blocked) p.set('blocked', '1');
    if (state.checkup) p.set('checkup', state.checkup);
    if (state.mapMode !== 'work') p.set('map', state.mapMode);
    if (state.notes) p.set('notes', '1');
    if (state.compare) p.set('compare', '1');
    if (state.wideOpen) p.set('wide', '1');
    if (state.peek) p.set('peek', state.peek);
    if (state.time.since) p.set('since', '1');
    if (state.time.sel) p.set('tsel', state.time.sel);
    if (state.time.kind !== 'all') p.set('tkind', state.time.kind);
    try {
      history.replaceState(null, '', '#' + p.toString());
    } catch (e) {
      /* some browsers refuse this on file:// pages; the page still works */
    }
  }

  // The one-epic view is left when the chosen thing is in another epic; the whole project has room for everything.
  function leaveEpicFor(n) {
    if (state.open && epicOf(n).code !== state.open) state.open = null;
  }

  // Choosing a thing to look around. `reset` starts a new trail; otherwise the trail grows.
  function setFocus(code, opts) {
    const o = opts || {};
    const n = byCode.get(code);
    if (!n) return;
    if (o.reset) state.trail = [code];
    else if (state.trail[state.trail.length - 1] !== code) state.trail.push(code);
    if (state.trail.length > 8) state.trail.shift();
    state.focus = code;
    state.peek = null;
    leaveEpicFor(n);
    state.sel = code;
    state.order.sel = n.kind === 'epic' ? null : code;
    state.order.only = false;
    state.zoom = 1;
    state.pan = { x: 0, y: 0 };
    state.wp.at = code;
    if (o.view) state.view = o.view;
    if (o.closeCheckup) state.checkup = null;
    renderAll(o.focusKey);
    if (o.scroll) $('#focusbar').scrollIntoView({ block: 'start', behavior: 'smooth' });
  }
  function goBack() {
    if (state.trail.length < 2) return;
    state.trail.pop();
    const code = state.trail[state.trail.length - 1];
    const n = byCode.get(code);
    state.focus = code;
    state.peek = null;
    leaveEpicFor(n);
    state.sel = code;
    state.order.sel = n.kind === 'epic' ? null : code;
    renderAll('back');
  }
  function clearFocus() {
    state.focus = null;
    state.trail = [];
    state.peek = null;
    state.sel = null;
    state.order.sel = null;
    renderAll('focus-input');
  }
  function jumpTrail(i) {
    state.trail = state.trail.slice(0, i + 1);
    const code = state.trail[i];
    const n = byCode.get(code);
    state.focus = code;
    state.peek = null;
    leaveEpicFor(n);
    state.sel = code;
    state.order.sel = n.kind === 'epic' ? null : code;
    renderAll();
  }

  /* ===== 5. small shared pieces: chips, status marks ======================================================= */

  const chip = (cls, ...kids) => h('span', { class: 'chip ' + cls }, ...kids);
  const statusChip = (st) => chip('status status-' + st, icon(st, 14), STATUS_LABEL[st]);
  const priorityChip = (p) => chip('priority priority-' + p, icon(p, 14), cap(p));
  function dueChip(n) {
    if (!n.due) return null;
    const late = lateDays(n);
    return late > 0 ? chip('overdue', icon('clock', 14), `Overdue ${plural(late, 'day')}`) : chip('due', icon('calendar', 14), 'Due ' + n.due);
  }
  function stateChips(rec, kind) {
    const out = [];
    if (rec.state === 'in force') out.push(chip('info', 'In force'));
    else if (rec.state === 'replaced') out.push(chip('ended', `Replaced by ${kind} #${rec.replacedBy}`));
    else out.push(chip('ended', 'Withdrawn'));
    if (rec.state === 'in force') {
      out.push(rec.confirmation ? chip('confirmed', icon('confirmed', 14), 'Confirmed by ' + rec.confirmation.by) : chip('unconfirmed', icon('unconfirmed', 14), 'Unconfirmed'));
    }
    return out;
  }
  const codeText = (c) => h('span', { class: 'code mono' }, c);
  const kindOf = (code) => byCode.get(code).kind;

  /* ===== 6. the shell: sidebar, top bar, appearance ======================================================== */

  function buildAppearance() {
    const g = $('#appearance');
    [['light', 'Light', 'sun'], ['dark', 'Dark', 'moon'], ['system', 'System', 'monitor']].forEach(([v, label, ic]) => {
      const input = h('input', {
        type: 'radio', name: 'appearance', value: v, id: 'ap-' + v, 'data-appearance': v,
        onchange: () => window.vrumTheme && window.vrumTheme.set(v),
      });
      g.append(h('label', { class: 'appearance-option', for: 'ap-' + v }, input, icon(ic, 14), h('span', {}, label)));
    });
    const show = () => {
      const now = window.vrumTheme ? window.vrumTheme.choice() : 'system';
      $$('input', g).forEach((i) => { i.checked = i.value === now; });
    };
    show();
    window.addEventListener('vrum-theme', show);
  }

  function treeItem(n) {
    const stories = n.kind === 'epic' ? kidsOf(n.code) : [];
    const open = state.tree.has(n.code);
    return h('li', { class: 'tree-item kind-' + n.kind, 'data-level': n.kind === 'epic' ? 1 : 2 },
      h('div', { class: 'tree-row' },
        stories.length
          ? h('button', {
              class: 'tree-twisty', type: 'button', 'aria-expanded': open ? 'true' : 'false',
              'aria-label': `${open ? 'Hide' : 'Show'} the stories of ${n.code}`,
              onclick: () => { if (open) state.tree.delete(n.code); else state.tree.add(n.code); renderSidebar(); },
            }, icon(open ? 'chevron_down' : 'chevron_right', 14))
          : h('span', { class: 'tree-twisty none', 'aria-hidden': 'true' }),
        h('button', {
          class: 'tree-link', type: 'button', 'data-fk': 'tree:' + n.code,
          'aria-current': state.focus === n.code ? 'true' : null,
          title: `${n.code} ${n.title}`,
          onclick: () => setFocus(n.code, { reset: true, focusKey: 'tree:' + n.code }),
        }, h('span', { class: 'tree-code' }, n.code), h('span', { class: 'tree-title' }, n.title))),
      open && stories.length ? h('ul', { class: 'tree-group plain' }, stories.map(treeItem)) : null);
  }
  function renderSidebar() {
    put($('#epic-tree'), epics.map(treeItem));
  }

  function openSidebar(open) {
    const sb = $('#sidebar');
    sb.classList.toggle('open', open);
    $('#sidebar-scrim').hidden = !open;
    $('#sidebar-toggle').setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) $('#sidebar-close').focus();
  }

  /* ===== 7. the focus bar ================================================================================== */

  function normaliseCode(q) {
    const m = /^(epic|story|task|e|s|t)\s*[:#-]?\s*0*(\d+)$/i.exec(q.trim());
    if (!m) return null;
    return ({ e: 'E', s: 'S', t: 'T', epic: 'E', story: 'S', task: 'T' })[m[1].toLowerCase()] + '-' + m[2];
  }
  function findItems(q) {
    const code = normaliseCode(q);
    if (code && byCode.has(code)) return [byCode.get(code)].concat(D.nodes.filter((n) => n.code !== code && n.code.startsWith(code)).slice(0, 7));
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    return D.nodes
      .filter((n) => words.every((w) => n.title.toLowerCase().includes(w) || n.code.toLowerCase() === w))
      .sort((a, b) => (a.title.toLowerCase().startsWith(words[0]) ? 0 : 1) - (b.title.toLowerCase().startsWith(words[0]) ? 0 : 1))
      .slice(0, 8);
  }

  let suppressOpen = false;
  function comboBox() {
    const input = h('input', {
      id: 'focus-search', type: 'text', role: 'combobox', autocomplete: 'off', spellcheck: 'false',
      'aria-expanded': 'false', 'aria-controls': 'focus-list', 'aria-autocomplete': 'list',
      'aria-label': 'Choose what to look around: an item code such as S-12, or words from its title',
      placeholder: 'An item: a code such as S-12, or words from its title', 'data-fk': 'focus-input',
    });
    const list = h('ul', { id: 'focus-list', class: 'combo-list', role: 'listbox', 'aria-label': 'Matching items', hidden: true });
    let options = [];
    let active = -1;
    const close = () => {
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
      active = -1;
    };
    const choose = (n) => {
      input.value = '';
      close();
      setFocus(n.code, { reset: true, focusKey: 'focus-input' });
    };
    const mark = () => {
      options.forEach((o, i) => o.el.setAttribute('aria-selected', i === active ? 'true' : 'false'));
      if (active >= 0) {
        input.setAttribute('aria-activedescendant', options[active].el.id);
        options[active].el.scrollIntoView({ block: 'nearest' });
      }
    };
    const optionEl = (n, i) => {
      const li = h('li', { role: 'option', id: 'focus-opt-' + i, class: 'combo-option', 'aria-selected': 'false' },
        icon(KIND_ICON[n.kind], 16), h('span', { class: 'code mono' }, n.code), h('span', { class: 'combo-title' }, n.title),
        h('span', { class: 'combo-state' }, statusChip(shown(n))));
      li.addEventListener('mousedown', (e) => e.preventDefault());
      li.addEventListener('click', () => choose(n));
      return li;
    };
    const show = () => {
      const q = input.value.trim();
      options = [];
      const rows = [];
      if (!q) {
        rows.push(h('li', { role: 'presentation', class: 'combo-head' }, 'Needs attention'));
        attentionItems().forEach(({ node }) => { const o = { node, el: optionEl(node, options.length) }; options.push(o); rows.push(o.el); });
      } else {
        const found = findItems(q);
        if (!found.length) rows.push(h('li', { role: 'presentation', class: 'combo-none' }, `Nothing matches "${q}". Try a code such as T-24, or a word from the title.`));
        else rows.push(h('li', { role: 'presentation', class: 'combo-head' }, plural(found.length, 'match', 'matches')));
        found.forEach((node) => { const o = { node, el: optionEl(node, options.length) }; options.push(o); rows.push(o.el); });
      }
      put(list, rows);
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      active = options.length && q ? 0 : -1;
      mark();
    };
    input.addEventListener('input', show);
    input.addEventListener('focus', () => { if (suppressOpen) suppressOpen = false; else show(); });
    input.addEventListener('click', () => { if (list.hidden) show(); });
    input.addEventListener('blur', () => setTimeout(close, 120));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); if (list.hidden) show(); else { active = Math.min(options.length - 1, active + 1); mark(); } }
      else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(0, active - 1); mark(); }
      else if (e.key === 'Enter') { if (!list.hidden && options[active >= 0 ? active : 0]) { e.preventDefault(); choose(options[active >= 0 ? active : 0].node); } }
      else if (e.key === 'Escape') { if (!list.hidden) { e.preventDefault(); close(); } }
    });
    return h('div', { class: 'combo' }, icon('search', 16, 'combo-icon'), input, list);
  }

  function renderFocusBar() {
    const bar = $('#focusbar');
    const n = state.focus ? byCode.get(state.focus) : null;
    const typed = $('#focus-search');
    const keep = typed && typed.value;
    const combo = comboBox();
    const chipEl = n
      ? h('div', { class: 'focus-chip', role: 'group', 'aria-label': 'Chosen item' },
          icon(KIND_ICON[n.kind], 16), h('span', { class: 'code mono' }, n.code), h('span', { class: 'focus-chip-title' }, n.title),
          h('button', { class: 'ghost icon-only small', type: 'button', 'aria-label': `Stop looking around ${n.code}`, title: 'Clear', 'data-fk': 'focus-clear', onclick: clearFocus }, icon('close', 14)))
      : h('p', { class: 'focus-none quiet' }, 'Nothing chosen yet.');
    const trail = state.trail.length > 1
      ? h('nav', { class: 'trail', 'aria-label': 'Where you have been' },
          h('button', { class: 'small', type: 'button', 'data-fk': 'back', onclick: goBack }, icon('arrow_left', 14), 'Back'),
          h('ol', {}, state.trail.map((c, i) => h('li', {},
            i === state.trail.length - 1
              ? h('span', { 'aria-current': 'true', class: 'trail-here' }, c)
              : h('button', { class: 'link', type: 'button', onclick: () => jumpTrail(i), title: byCode.get(c).title }, c)))))
      : null;
    put(bar,
      h('div', { class: 'focus-row' }, h('label', { class: 'focus-label', for: 'focus-search' }, 'Show me what is around'), combo, chipEl),
      trail);
    if (keep) $('#focus-search').value = keep;
  }

  /* ===== 8. tabs =========================================================================================== */

  const TABS = [
    { id: 'around', name: 'Around', ask: 'What do we know and what was decided about this?', icon: 'around' },
    { id: 'structure', name: 'Structure', ask: 'How does it all fit together?', icon: 'structure' },
    { id: 'order', name: 'Order', ask: 'What must come first, and what does this block?', icon: 'order' },
    { id: 'timeline', name: 'Timeline', ask: 'What changed, and what was replaced?', icon: 'timeline' },
  ];
  function setView(id, focusKey) {
    state.view = id;
    state.checkup = state.checkup === 'map' ? null : state.checkup;
    renderAll(focusKey);
  }
  function renderTabs() {
    const bar = $('#tabs');
    put(bar, TABS.map((t, i) => h('button', {
      class: 'tab', type: 'button', role: 'tab', id: 'tab-' + t.id, 'data-fk': 'tab:' + t.id,
      'aria-selected': state.view === t.id ? 'true' : 'false', 'aria-controls': 'view',
      tabindex: state.view === t.id ? '0' : '-1',
      onclick: () => setView(t.id, 'tab:' + t.id),
      onkeydown: (e) => {
        const k = e.key;
        if (!['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(k)) return;
        e.preventDefault();
        const j = k === 'Home' ? 0 : k === 'End' ? TABS.length - 1 : (i + (k === 'ArrowRight' ? 1 : -1) + TABS.length) % TABS.length;
        setView(TABS[j].id, 'tab:' + TABS[j].id);
      },
    }, icon(t.icon, 18), h('span', { class: 'tab-text' }, h('span', { class: 'tab-name' }, t.name), h('span', { class: 'tab-ask' }, t.ask)))));
    const view = $('#view');
    view.setAttribute('aria-labelledby', 'tab-' + state.view);
  }

  /* ===== 9. the render loop ================================================================================ */

  const observers = [];
  function watch(el, fn) {
    if (!window.ResizeObserver) return;
    const ro = new ResizeObserver(fn);
    ro.observe(el);
    observers.push(ro);
  }

  function renderView() {
    const view = $('#view');
    view.replaceChildren();
    if (state.view === 'around') viewAround(view);
    else if (state.view === 'structure') viewStructure(view);
    else if (state.view === 'order') viewOrder(view);
    else viewTimeline(view);
  }

  function renderAll(focusKey) {
    const active = document.activeElement;
    const key = focusKey || (active && active.getAttribute && active.getAttribute('data-fk'));
    const y = window.scrollY;
    observers.splice(0).forEach((o) => o.disconnect());
    renderSidebar();
    renderFocusBar();
    renderCheckup();
    renderTabs();
    renderView();
    renderDock();
    writeHash();
    if (key) {
      const again = document.querySelector(`[data-fk="${key}"]`);
      if (again) {
        if (key === 'focus-input') suppressOpen = true;
        again.focus({ preventScroll: true });
      }
    }
    window.scrollTo(0, y);
  }

  /* ===== 10. empty state =================================================================================== */

  function emptyState(root, what) {
    const lead = {
      around: 'Choose an item in the box above, or start from something that needs attention. The page shows what is around one thing at a time, so it never has to draw everything at once.',
      order: 'The order of work is drawn for one item at a time: its chain of "must come first". Choose an item above, or start from something that needs attention.',
    }[what];
    root.append(h('section', { class: 'panel empty-focus' },
      h('div', { class: 'empty-art', 'aria-hidden': 'true' }, icon('around', 36)),
      h('h2', {}, 'Nothing chosen yet'),
      h('p', { class: 'quiet empty-lead' }, lead),
      h('h3', { class: 'attention-h' }, 'Needs attention'),
      h('ul', { class: 'attention-cards plain' }, attentionItems().map(({ node }) => {
        const st = shown(node);
        const late = lateDays(node);
        return h('li', {}, h('button', { class: 'attention-card', type: 'button', 'data-fk': 'att:' + node.code, onclick: () => setFocus(node.code, { reset: true }) },
          h('span', { class: 'ac-top' }, icon(st, 16), codeText(node.code), statusChip(st), late > 0 ? chip('overdue', icon('clock', 14), `Overdue ${plural(late, 'day')}`) : null),
          h('span', { class: 'ac-title' }, node.title),
          h('span', { class: 'ac-go' }, 'See what is around it', icon('arrow_right', 14))));
      }))));
  }

  /* ===== 11. Around: what surrounds one item =============================================================== */

  function around(code, reach) {
    const n = byCode.get(code);
    const G = { up: [], down: [], needs: [], blocks: [], reqs: [], dec: [], wide: [], past: [], facts: [], pastFacts: [], sim: [] };
    const seen = new Set(['work:' + code]);
    const work = (c, via, why, extra) => {
      const key = 'work:' + c;
      if (seen.has(key)) return null;
      seen.add(key);
      return Object.assign({ type: 'work', key, code: c, via: via || null, why }, extra || {});
    };
    const keep = (list, item) => { if (item) list.push(item); };
    const word = n.kind;

    if (n.parent) {
      const p = byCode.get(n.parent);
      keep(G.up, work(p.code, null, `The ${p.kind} this ${word} is part of`));
      if (reach > 1 && p.parent) keep(G.up, work(p.parent, p.code, `The ${byCode.get(p.parent).kind} that ${p.code} is part of`));
    }
    kidsOf(code).forEach((c) => {
      keep(G.down, work(c.code, null, `Part of this ${word}`));
      if (reach > 1) kidsOf(c.code).forEach((g) => keep(G.down, work(g.code, c.code, `Part of ${c.code}`)));
    });

    const walk = (start, map, out, say) => {
      let frontier = [start];
      for (let step = 1; step <= reach; step += 1) {
        const next = [];
        frontier.forEach((c) => (map.get(c) || []).forEach((x) => {
          const it = work(x, step > 1 ? c : null, say(step, c));
          if (it) { out.push(it); next.push(x); }
        }));
        frontier = next;
      }
    };
    walk(code, needsMap, G.needs, (step, c) => (step === 1 ? `This ${word} needs it first` : `${c} needs it first`));
    walk(code, neededBy, G.blocks, (step, c) => (step === 1 ? `It needs this ${word} first` : `It needs ${c} first`));

    const reqFrom = (story, via) => D.rules.filter((r) => r.story === story).forEach((r) => G.reqs.push({
      type: 'rule', key: 'rule:' + r.id, id: r.id, via,
      why: via ? `Placed in ${via}, ${via === n.parent ? 'the story this task is part of' : 'a story under this epic'}` : 'Placed in this story',
    }));
    if (n.kind === 'story') reqFrom(code, null);
    if (reach > 1 && n.kind === 'epic') kidsOf(code).forEach((s) => reqFrom(s.code, s.code));
    if (reach > 1 && n.kind === 'task') reqFrom(n.parent, n.parent);

    const scope = [{ c: code, via: null }];
    if (reach > 1) {
      if (n.parent) scope.push({ c: n.parent, via: n.parent });
      kidsOf(code).forEach((k) => scope.push({ c: k.code, via: k.code }));
    }
    const linkedTo = (via) => (via ? `Linked to ${via}` : `Linked to this ${word}`);
    // A project-wide decision that names this very item is shown with the item's own decisions; when it names
    // several (as the nine ground rules name the platform epic) they stay folded in the project-wide count.
    const wideHere = D.decisions.filter((d) => d.wide && d.state === 'in force' && scope.some((s) => d.on.includes(s.c)));
    const foldHere = wideHere.length > 0 && wideHere.length <= 3;
    G.wideMoved = 0;
    D.decisions.forEach((d) => {
      const hit = scope.find((s) => d.on.includes(s.c));
      const by = `decided by ${d.by} on ${d.decided}`;
      const base = { type: 'decision', key: 'decision:' + d.id, id: d.id, via: hit ? hit.via : null };
      if (d.wide && d.state === 'in force') {
        if (foldHere && hit) {
          G.dec.push(Object.assign(base, { why: `Project-wide, and linked to ${hit.via ? hit.via : 'this ' + word}, ${by}` }));
          G.wideMoved += 1;
        } else G.wide.push(Object.assign(base, { via: null, why: `Applies to the whole project, ${by}` }));
      }
      else if (!hit) return;
      else if (d.state === 'in force') G.dec.push(Object.assign(base, { why: `${linkedTo(hit.via)}, ${by}` }));
      else if (d.state === 'replaced') G.past.push(Object.assign(base, { why: `${linkedTo(hit.via)}. Replaced by decision #${d.replacedBy} on ${d.ended}; kept so the reason is not lost` }));
      else G.past.push(Object.assign(base, { why: `${linkedTo(hit.via)}. Withdrawn on ${d.ended}: ${d.withdrawnWhy}` }));
    });
    D.facts.forEach((f) => {
      const hit = scope.find((s) => f.on.includes(s.c));
      if (!hit) return;
      const base = { type: 'fact', key: 'fact:' + f.id, id: f.id, via: hit.via };
      if (f.state === 'in force') G.facts.push(Object.assign(base, { why: `${linkedTo(hit.via)}. Source: ${f.source}` }));
      else if (f.state === 'replaced') G.pastFacts.push(Object.assign(base, { why: `${linkedTo(hit.via)}. Replaced by fact #${f.replacedBy} on ${f.changed}` }));
      else G.pastFacts.push(Object.assign(base, { why: `${linkedTo(hit.via)}. Withdrawn on ${f.changed}: ${f.withdrawnWhy}` }));
    });

    (relMap.get(code) || []).filter((r) => r.type !== 'needs').forEach((r) => {
      const say = { relates: 'Linked as related', duplicates: 'Marked as a duplicate', supersedes: r.dir === 'out' ? 'This one supersedes it' : 'It supersedes this one' }[r.type];
      keep(G.sim, work(r.other, null, say, { link: r.type }));
    });
    D.similar.forEach((s) => {
      const other = s.a === code ? s.b : s.b === code ? s.a : null;
      if (other && !hasLink(code, other)) keep(G.sim, work(other, null, `The index finds it ${s.score.toFixed(2)} alike; not linked`, { score: s.score }));
    });
    (mentionMap.get(code) || []).forEach((m) => keep(G.sim, work(m.other, null, m.dir === 'out' ? 'Named in the text of this item' : 'Its text names this item', { link: 'mentions' })));
    return G;
  }

  const GROUPS = [
    { id: 'up', slot: 'n', title: 'Part of', icon: 'hier', line: 'hier' },
    { id: 'down', slot: 's', title: 'Contains', icon: 'hier', line: 'hier' },
    { id: 'needs', slot: 'w', title: 'Needs first', icon: 'arrow_right', line: 'needs', arrow: 'in' },
    { id: 'blocks', slot: 'e', title: 'Blocks', icon: 'lock', line: 'needs', arrow: 'out' },
    { id: 'reqs', slot: 'nw', title: 'Requirements', icon: 'rule', line: 'ctx', end: 'square' },
    { id: 'sim', slot: 'ne', title: 'Related and similar', icon: 'link', line: 'sim' },
    { id: 'dec', slot: 'sw', title: 'Decisions', icon: 'decision', line: 'ctx', end: 'flag' },
    { id: 'facts', slot: 'se', title: 'Facts', icon: 'fact', line: 'ctx', end: 'diamond' },
  ];
  const SLOT = { nw: [1, 'top'], n: [2, 'top'], ne: [3, 'top'], w: [1, 'mid'], e: [3, 'mid'], sw: [1, 'bot'], s: [2, 'bot'], se: [3, 'bot'] };
  const isEmptyGroup = (id, G) => (id === 'dec' ? !(G.dec.length || G.wide.length || G.past.length) : id === 'facts' ? !(G.facts.length || G.pastFacts.length) : !G[id].length);

  function peek(key) {
    state.peek = state.peek === key ? null : key;
    renderAll('n:' + key);
  }

  // One neighbour as a button: a status mark, its code, its title, and what is special about it.
  function nodeFor(item) {
    if (item.type === 'work') {
      const n = byCode.get(item.code);
      const st = shown(n);
      const late = lateDays(n);
      return h('button', {
        class: `node n-work s-${st}` + (item.via ? ' is-via' : ''), type: 'button', 'data-fk': 'n:' + item.key, title: n.title,
        'aria-label': `${n.code} ${n.title}. ${STATUS_LABEL[st]}${late > 0 ? `, overdue ${plural(late, 'day')}` : ''}. Press Enter to look around it.`,
        onclick: () => setFocus(n.code),
      },
      h('span', { class: 'n-ico' }, icon(st, 16)),
      h('span', { class: 'n-body' },
        h('span', { class: 'n-line' }, codeText(n.code),
          item.score ? h('span', { class: 'n-score' }, 'alike ' + item.score.toFixed(2)) : null,
          late > 0 ? h('span', { class: 'n-flag late' }, icon('clock', 12), `${late} d late`) : null),
        h('span', { class: 'n-title' }, n.title),
        item.via ? h('span', { class: 'n-via' }, 'via ' + item.via) : null));
    }
    const rec = item.type === 'decision' ? decById.get(item.id) : item.type === 'fact' ? factById.get(item.id) : ruleById.get(item.id);
    const ended = item.type === 'rule' ? rec.status === 'dropped' : rec.state !== 'in force';
    const unconfirmed = item.type !== 'rule' && !ended && !rec.confirmation;
    const title = item.type === 'fact' ? rec.text : rec.title;
    const label = `${KIND_LABEL[item.type]} ${rec.id}. ${title}. ${item.type === 'rule' ? cap(rec.status) : ended ? cap(rec.state) : rec.confirmation ? 'Confirmed by ' + rec.confirmation.by : 'Not confirmed yet'}. Press Enter to read it.`;
    let flag = null;
    if (item.type === 'rule') flag = rec.status === 'dropped' ? h('span', { class: 'n-badge ended' }, 'Dropped') : null;
    else if (ended) flag = h('span', { class: 'n-badge ended' }, cap(rec.state));
    else if (unconfirmed) flag = h('span', { class: 'n-badge warn' }, 'Unconfirmed');
    else flag = h('span', { class: 'n-badge ok' }, icon('confirmed', 12), 'Confirmed');
    return h('button', {
      class: `node n-${item.type}` + (ended ? ' is-ended' : '') + (unconfirmed ? ' is-unconfirmed' : '') + (state.peek === item.key ? ' is-peek' : '') + (item.via ? ' is-via' : ''),
      type: 'button', 'data-fk': 'n:' + item.key, 'aria-label': label, 'aria-pressed': state.peek === item.key ? 'true' : 'false', title: title,
      onclick: () => peek(item.key),
    },
    h('span', { class: 'n-ico' }, icon(KIND_ICON[item.type], 16)),
    h('span', { class: 'n-body' },
      h('span', { class: 'n-line' }, codeText('#' + rec.id), item.type === 'decision' && rec.wide ? h('span', { class: 'n-badge info' }, 'Project-wide') : null, flag),
      h('span', { class: 'n-title' }, trunc(title, 78)),
      item.via ? h('span', { class: 'n-via' }, 'via ' + item.via) : null));
  }

  function limitedList(id, items, limit) {
    const open = !!state.expanded[id];
    const folded = items.length > limit + 1 && !open;
    const shownItems = folded ? items.slice(0, limit) : items;
    const ul = h('ul', { class: 'gbox-list plain' + (open && items.length > limit + 1 ? ' scroll' : '') }, shownItems.map((it) => h('li', {}, nodeFor(it))));
    if (open && items.length > limit + 1) { ul.setAttribute('tabindex', '0'); ul.setAttribute('role', 'region'); ul.setAttribute('aria-label', 'All of them, scrolls'); }
    const toggle = items.length > limit + 1
      ? h('button', { class: 'more small ghost', type: 'button', 'aria-expanded': open ? 'true' : 'false', 'data-fk': 'more:' + id, onclick: () => { state.expanded[id] = !open; renderAll('more:' + id); } },
          open ? 'Show fewer' : `Show ${items.length - limit} more`)
      : null;
    return [ul, toggle];
  }

  function wideRow(G) {
    const open = state.wideOpen;
    return [
      h('button', {
        class: 'node n-wide', type: 'button', 'aria-expanded': open ? 'true' : 'false', 'data-fk': 'wide',
        onclick: () => { state.wideOpen = !open; renderAll('wide'); },
        'aria-label': `${plural(G.wide.length, G.wideMoved ? 'other project-wide decision' : 'project-wide decision')} apply here. ${open ? 'Hide them' : 'Show them'}.`,
      }, h('span', { class: 'n-ico' }, icon('decision', 16)),
        h('span', { class: 'n-body' }, h('span', { class: 'n-title strong' }, `${G.wide.length} ${G.wideMoved ? 'other ' : ''}project-wide decisions apply`), h('span', { class: 'n-via' }, 'to every item in the project')),
        h('span', { class: 'n-more' }, icon(open ? 'chevron_up' : 'chevron_down', 14), open ? 'Hide' : 'Show')),
      open ? h('ul', { class: 'gbox-list plain scroll', tabindex: '0', role: 'region', 'aria-label': 'Project-wide decisions, scrolls' }, G.wide.map((it) => h('li', {}, nodeFor(it)))) : null,
    ];
  }

  function groupBox(def, G, n) {
    let count;
    let body;
    if (def.id === 'dec') {
      count = G.dec.length + G.wide.length + G.past.length;
      body = [
        G.dec.length ? limitedList('dec', G.dec, 4) : null,
        G.wide.length ? wideRow(G) : null,
        G.past.length ? [h('p', { class: 'gbox-sub' }, 'Earlier, kept for the reason'), h('ul', { class: 'gbox-list plain' }, G.past.map((it) => h('li', {}, nodeFor(it))))] : null,
      ];
    } else if (def.id === 'facts') {
      count = G.facts.length + G.pastFacts.length;
      body = [
        G.facts.length ? limitedList('facts', G.facts, 4) : null,
        G.pastFacts.length ? [h('p', { class: 'gbox-sub' }, 'Earlier, kept for the reason'), h('ul', { class: 'gbox-list plain' }, G.pastFacts.map((it) => h('li', {}, nodeFor(it))))] : null,
      ];
    } else {
      count = G[def.id].length;
      body = limitedList(def.id, G[def.id], 5);
    }
    const title = def.title;
    return h('section', { class: `gbox g-${def.id}`, 'aria-labelledby': 'gb-' + def.id },
      h('header', { class: 'gbox-head' }, icon(def.icon, 16), h('h3', { id: 'gb-' + def.id }, title), h('span', { class: 'gbox-count', 'aria-label': plural(count, 'item') }, count)),
      body);
  }

  function hubCard(n) {
    const st = shown(n);
    const p = progress(n);
    const waits = waitingOn(n);
    return h('article', { class: `hub s-${st}`, 'aria-label': `The chosen item: ${n.code} ${n.title}` },
      h('div', { class: 'hub-top' },
        h('span', { class: 'kind-label' }, icon(KIND_ICON[n.kind], 16), KIND_LABEL[n.kind]),
        codeText(n.code),
        statusChip(st)),
      h('h3', { class: 'hub-title' }, n.title),
      h('div', { class: 'hub-meta' },
        priorityChip(n.priority),
        dueChip(n),
        n.held ? chip('held', icon('user', 14), 'Held by ' + n.held) : null,
        waits.length ? chip('waiting', icon('clock', 14), 'Waiting for ' + waits.slice(0, 2).join(', ') + (waits.length > 2 ? '...' : '')) : null),
      p.total && n.kind !== 'task'
        ? h('div', { class: 'hub-progress' }, h('span', { class: 'meter', role: 'img', 'aria-label': `${p.done} of ${p.total} tasks done` }, h('span', { style: `width:${Math.round((p.done / p.total) * 100)}%` })), h('span', { class: 'quiet' }, `${p.done} of ${plural(p.total, 'task')} done`))
        : null,
      h('div', { class: 'hub-actions' },
        h('button', { class: 'small', type: 'button', onclick: () => toast('Opens the item page in the real app') }, 'Open page', icon('arrow_right', 14)),
        h('button', { class: 'small ghost', type: 'button', onclick: () => { state.open = null; state.sel = n.code; state.wp.at = n.code; setView('structure', null); } }, icon('structure', 14), 'In Structure'),
        h('button', { class: 'small ghost', type: 'button', onclick: () => { state.order.sel = n.kind === 'epic' ? null : n.code; setView('order', null); } }, icon('order', 14), 'In Order')));
  }

  const LEGEND = [
    { cls: 'hier', label: 'Part of, contains' },
    { cls: 'needs', label: 'Needs first (arrow shows the order)', arrow: true },
    { cls: 'sim', label: 'Related, similar, duplicate' },
    { cls: 'ctx-square', label: 'Requirement' },
    { cls: 'ctx-flag', label: 'Decision' },
    { cls: 'ctx-diamond', label: 'Fact' },
  ];
  function legendSample(item) {
    const svg = sv('svg', { class: 'legend-sample', width: 44, height: 14, viewBox: '0 0 44 14', 'aria-hidden': 'true' });
    const type = item.cls.split('-')[0];
    svg.append(sv('line', { class: `spoke spoke-${type === 'ctx' ? 'ctx' : type}`, x1: 2, y1: 7, x2: item.arrow ? 36 : 36, y2: 7 }));
    if (item.arrow) svg.append(sv('path', { class: 'mk mk-arrow', d: 'M34 3.5 L42 7 L34 10.5 z' }));
    if (type === 'ctx') svg.append(endShape(item.cls.split('-')[1], 38, 7));
    return svg;
  }
  function endShape(kind, x, y) {
    if (kind === 'square') return sv('rect', { class: 'mk mk-square', x: x - 3.5, y: y - 3.5, width: 7, height: 7, rx: 1 });
    if (kind === 'flag') return sv('path', { class: 'mk mk-flag', d: `M${x - 3} ${y - 4} L${x + 5} ${y} L${x - 3} ${y + 4} z` });
    return sv('path', { class: 'mk mk-diamond', d: `M${x} ${y - 4.5} L${x + 4.5} ${y} L${x} ${y + 4.5} L${x - 4.5} ${y} z` });
  }
  function egoDefs() {
    const m = (id, w, extra, ...kids) => sv('marker', Object.assign({ id, viewBox: '0 0 10 10', markerWidth: w, markerHeight: w, orient: 'auto', markerUnits: 'userSpaceOnUse' }, extra), ...kids);
    return sv('defs', {},
      m('ego-arrow', 12, { refX: 9, refY: 5 }, sv('path', { class: 'mk mk-arrow', d: 'M0 1 L10 5 L0 9 z' })),
      m('ego-end-square', 8, { refX: 8, refY: 5 }, sv('rect', { class: 'mk mk-square', x: 1, y: 1, width: 8, height: 8, rx: 1 })),
      m('ego-end-flag', 10, { refX: 9, refY: 5 }, sv('path', { class: 'mk mk-flag', d: 'M0 0 L10 5 L0 10 z' })),
      m('ego-end-diamond', 10, { refX: 8, refY: 5 }, sv('path', { class: 'mk mk-diamond', d: 'M5 0 L10 5 L5 10 L0 5 z' })));
  }

  function drawSpokes(ego, svg, hub, plan) {
    if (getComputedStyle(svg).display === 'none') return;
    const box = ego.getBoundingClientRect();
    if (!box.width) return;
    svg.setAttribute('width', r1(box.width));
    svg.setAttribute('height', r1(box.height));
    svg.setAttribute('viewBox', `0 0 ${r1(box.width)} ${r1(box.height)}`);
    const rel = (el) => {
      const r = el.getBoundingClientRect();
      return { l: r.left - box.left, t: r.top - box.top, r: r.right - box.left, b: r.bottom - box.top, cx: (r.left + r.right) / 2 - box.left, cy: (r.top + r.bottom) / 2 - box.top };
    };
    const H = rel(hub);
    const g = sv('g');
    plan.forEach(({ slot, el, def }) => {
      const B = rel(el);
      let a;
      let b;
      if (slot === 'n') { a = [H.cx, H.t]; b = [H.cx, B.b]; }
      else if (slot === 's') { a = [H.cx, H.b]; b = [H.cx, B.t]; }
      else if (slot === 'w') { a = [H.l, H.cy]; b = [B.r, H.cy]; }
      else if (slot === 'e') { a = [H.r, H.cy]; b = [B.l, H.cy]; }
      else if (slot === 'nw') { a = [H.l, H.t]; b = [B.r, B.b]; }
      else if (slot === 'ne') { a = [H.r, H.t]; b = [B.l, B.b]; }
      else if (slot === 'sw') { a = [H.l, H.b]; b = [B.r, B.t]; }
      else { a = [H.r, H.b]; b = [B.l, B.t]; }
      let from = a;
      let to = b;
      let end = null;
      if (def.line === 'needs') { end = 'url(#ego-arrow)'; if (def.arrow === 'in') { from = b; to = a; } }
      else if (def.line === 'ctx') end = `url(#ego-end-${def.end})`;
      g.append(sv('path', { class: `spoke spoke-${def.line} spoke-${def.id}`, d: `M${r1(from[0])} ${r1(from[1])} L${r1(to[0])} ${r1(to[1])}`, 'marker-end': end }));
      g.append(sv('circle', { class: 'port', cx: r1(a[0]), cy: r1(a[1]), r: 3.5 }));
    });
    put(svg, egoDefs(), g);
  }

  function egoPanel(n, G) {
    const ego = h('div', { class: 'ego' });
    const svg = sv('svg', { class: 'ego-lines', 'aria-hidden': 'true', focusable: 'false' });
    const used = (id) => !isEmptyGroup(id, G);
    const topUsed = ['nw', 'n', 'ne'].some((s) => used(GROUPS.find((g) => g.slot === s).id));
    const botUsed = ['sw', 's', 'se'].some((s) => used(GROUPS.find((g) => g.slot === s).id));
    const rowOf = { top: 1, mid: topUsed ? 2 : 1, bot: (topUsed ? 2 : 1) + 1 };
    const hub = hubCard(n);
    hub.style.setProperty('--col', 2);
    hub.style.setProperty('--row', rowOf.mid);
    ego.append(svg, hub);
    const plan = [];
    const empty = [];
    const order = ['up', 'needs', 'blocks', 'reqs', 'sim', 'dec', 'facts', 'down'];
    order.forEach((id) => {
      const def = GROUPS.find((g) => g.id === id);
      if (!used(id)) { empty.push(def); return; }
      const box = groupBox(def, G, n);
      const [col, band] = SLOT[def.slot];
      box.style.setProperty('--col', col);
      box.style.setProperty('--row', rowOf[band]);
      box.dataset.slot = def.slot;
      ego.append(box);
      plan.push({ slot: def.slot, el: box, def });
    });
    if (!botUsed && !topUsed) ego.classList.add('ego-flat');
    const emptyNames = empty.map((d) => (d.id === 'up' && n.kind === 'epic' ? 'Part of (this is the top level)' : d.title));
    const panel = h('section', { class: 'panel ego-panel', 'aria-labelledby': 'ego-h' },
      h('div', { class: 'panel-head' },
        h('div', {}, h('h2', { id: 'ego-h' }, `Around ${n.code}`), h('p', { class: 'quiet' }, state.reach === 1 ? 'What is directly joined to this item.' : 'What is joined to it, and what is joined to those.')),
        reachControl()),
      ego,
      emptyNames.length ? h('p', { class: 'ego-also' }, h('span', { class: 'quiet' }, 'Checked, nothing found: '), emptyNames.join(' \u00b7 ')) : null,
      h('ul', { class: 'legend plain', 'aria-label': 'How to read the lines' }, LEGEND.map((it) => h('li', {}, legendSample(it), h('span', {}, it.label)))));
    requestAnimationFrame(() => drawSpokes(ego, svg, hub, plan));
    watch(ego, () => drawSpokes(ego, svg, hub, plan));
    return panel;
  }

  function reachControl() {
    return h('div', { class: 'reach', role: 'group', 'aria-label': 'How far to look' },
      h('span', { class: 'reach-label' }, 'Reach'),
      h('div', { class: 'segmented' }, [1, 2].map((r) => h('button', {
        type: 'button', 'aria-pressed': state.reach === r ? 'true' : 'false', 'data-fk': 'reach:' + r,
        onclick: () => { state.reach = r; renderAll('reach:' + r); },
      }, plural(r, 'step')))));
  }

  /* ----- the list beside the diagram, and the agent's view of the same facts ----- */

  const LIST_GROUPS = [
    ['up', 'Part of'], ['down', 'Contains'], ['needs', 'Needs first'], ['blocks', 'Blocks'], ['reqs', 'Requirements'],
    ['dec', 'Decisions in force'], ['wide', 'Project-wide decisions'], ['facts', 'Facts'], ['sim', 'Related and similar'],
    ['past', 'Earlier decisions'], ['pastFacts', 'Earlier facts'],
  ];
  const LIST_ICON = { up: 'hier', down: 'hier', needs: 'arrow_right', blocks: 'lock', reqs: 'rule', dec: 'decision', wide: 'decision', facts: 'fact', sim: 'link', past: 'decision', pastFacts: 'fact' };

  function listRow(item) {
    let icn;
    let code;
    let title;
    let chips = [];
    let ended = false;
    let onclick;
    if (item.type === 'work') {
      const n = byCode.get(item.code);
      const st = shown(n);
      icn = icon(st, 16);
      code = n.code;
      title = n.title;
      chips = [statusChip(st), dueChip(n)];
      ended = st === 'dropped';
      onclick = () => setFocus(n.code);
    } else if (item.type === 'rule') {
      const r = ruleById.get(item.id);
      icn = icon('rule', 16);
      code = '#' + r.id;
      title = r.title;
      chips = [chip(r.status === 'dropped' ? 'ended' : r.status === 'settled' ? 'ok' : 'info', cap(r.status))];
      ended = r.status === 'dropped';
      onclick = () => peek(item.key);
    } else {
      const rec = item.type === 'decision' ? decById.get(item.id) : factById.get(item.id);
      icn = icon(KIND_ICON[item.type], 16);
      code = '#' + rec.id;
      title = item.type === 'decision' ? rec.title : rec.text;
      chips = stateChips(rec, item.type);
      ended = rec.state !== 'in force';
      onclick = () => peek(item.key);
    }
    return h('li', { class: 'lrow' + (ended ? ' is-ended' : '') + (state.peek === item.key ? ' is-peek' : '') },
      h('button', { class: 'lrow-main', type: 'button', 'data-fk': 'l:' + item.key, onclick, 'aria-pressed': item.type === 'work' ? null : state.peek === item.key ? 'true' : 'false' },
        icn, codeText(code), h('span', { class: 'lrow-title' }, title)),
      h('span', { class: 'lrow-chips' }, chips),
      h('p', { class: 'lrow-why' }, item.via ? `${item.why} (via ${item.via})` : item.why));
  }

  function peekCard() {
    const [type, idText] = state.peek.split(':');
    const id = Number(idText);
    const close = h('button', { class: 'ghost icon-only small', type: 'button', 'aria-label': 'Close this record', onclick: () => { state.peek = null; renderAll(); } }, icon('close', 14));
    const appliesTo = (on, wide) => h('div', { class: 'peek-applies' },
      h('h4', {}, 'Applies to'),
      h('div', { class: 'chip-row' },
        wide ? chip('info', icon('graph', 14), 'The whole project') : null,
        on.map((c) => h('button', { class: 'chip chip-link', type: 'button', title: byCode.get(c).title, onclick: () => setFocus(c, { reset: false }) }, icon(KIND_ICON[byCode.get(c).kind], 14), c))));
    if (type === 'rule') {
      const r = ruleById.get(id);
      return h('section', { class: 'peek', 'aria-label': 'Requirement ' + id },
        h('div', { class: 'peek-head' }, h('span', { class: 'kind-label' }, icon('rule', 16), `Requirement #${id}`), close),
        h('h3', {}, r.title),
        h('div', { class: 'chip-row' }, chip(r.status === 'dropped' ? 'ended' : r.status === 'settled' ? 'ok' : 'info', cap(r.status))),
        r.story ? appliesTo([r.story], false) : h('p', { class: 'quiet' }, 'Not placed in any story yet.'));
    }
    const rec = type === 'decision' ? decById.get(id) : factById.get(id);
    if (type === 'decision') {
      return h('section', { class: 'peek', 'aria-label': 'Decision ' + id },
        h('div', { class: 'peek-head' }, h('span', { class: 'kind-label' }, icon('decision', 16), `Decision #${id}`), close),
        h('h3', { class: rec.state !== 'in force' ? 'is-ended' : '' }, rec.title),
        h('div', { class: 'chip-row' }, rec.wide ? chip('info', icon('graph', 14), 'Project-wide') : null, stateChips(rec, 'decision')),
        h('p', {}, rec.text),
        h('p', { class: 'quiet' }, h('strong', {}, 'Reason. '), rec.reason),
        h('p', { class: 'quiet' }, `Decided by ${rec.by} on ${rec.decided}.`, rec.confirmation ? ` Confirmed by ${rec.confirmation.by}: "${rec.confirmation.why}"` : '', rec.history ? ' ' + rec.history : '', rec.withdrawnWhy ? ' Withdrawn: ' + rec.withdrawnWhy : '', rec.replaces ? ` Replaces decision #${rec.replaces}.` : ''),
        appliesTo(rec.on, rec.wide));
    }
    return h('section', { class: 'peek', 'aria-label': 'Fact ' + id },
      h('div', { class: 'peek-head' }, h('span', { class: 'kind-label' }, icon('fact', 16), `Fact #${id}`), close),
      h('h3', { class: rec.state !== 'in force' ? 'is-ended' : '' }, rec.text),
      h('div', { class: 'chip-row' }, stateChips(rec, 'fact')),
      h('p', { class: 'quiet' }, h('strong', {}, 'Source. '), rec.source),
      h('p', { class: 'quiet' }, `Recorded ${rec.recorded}.`, rec.confirmation ? ` Confirmed by ${rec.confirmation.by}: "${rec.confirmation.why}"` : '', rec.history ? ' ' + rec.history : '', rec.withdrawnWhy ? ' Withdrawn: ' + rec.withdrawnWhy : '', rec.why ? ' Why it replaces fact #' + rec.replaces + ': ' + rec.why : ''),
      appliesTo(rec.on, false));
  }

  function peopleList(n, G) {
    const total = sum(LIST_GROUPS.map(([id]) => G[id].length));
    const LIM = 6;
    const rowsOf = (id, items) => {
      const open = !!state.expanded['l:' + id];
      const folded = items.length > LIM + 1 && !open;
      return [
        h('ul', { class: 'plain lrows' }, (folded ? items.slice(0, LIM) : items).map(listRow)),
        items.length > LIM + 1 ? h('button', { class: 'small ghost lmore', type: 'button', 'aria-expanded': open ? 'true' : 'false', 'data-fk': 'lmore:' + id, onclick: () => { state.expanded['l:' + id] = !open; renderAll('lmore:' + id); } }, open ? 'Show fewer' : `Show ${items.length - LIM} more`) : null,
      ];
    };
    const blocks = LIST_GROUPS.map(([id, rawTitle]) => {
      const items = G[id];
      const title = id === 'wide' && G.wideMoved ? 'Other project-wide decisions' : rawTitle;
      if (!items.length) return null;
      if (id === 'wide') {
        return h('section', { class: 'lgroup' },
          h('h3', { class: 'lgroup-h' }, icon(LIST_ICON[id], 16), title, h('span', { class: 'gbox-count' }, items.length),
            h('button', { class: 'small ghost lgroup-toggle', type: 'button', 'aria-expanded': state.wideOpen ? 'true' : 'false', 'data-fk': 'lwide', onclick: () => { state.wideOpen = !state.wideOpen; renderAll('lwide'); } }, state.wideOpen ? 'Hide' : 'Show')),
          state.wideOpen ? rowsOf(id, items) : h('p', { class: 'quiet lgroup-note' }, 'Apply to everything in the project, so they are shown once, not on every item.'));
      }
      return h('section', { class: 'lgroup' },
        h('h3', { class: 'lgroup-h' }, icon(LIST_ICON[id], 16), title, h('span', { class: 'gbox-count' }, items.length)),
        rowsOf(id, items));
    });
    return h('div', {}, total ? blocks : h('p', { class: 'empty' }, 'Nothing is joined to this item yet.'));
  }

  const idSpan = (text) => text.split(/((?:epic|story|task|rule|decision|fact):\d+)/).map((part, i) => (i % 2 ? h('span', { class: 'ag-id' }, part) : part));
  function agentText(n, G) {
    const p = progress(n);
    const q = (s) => `"${trunc(s, 74)}"`;
    const L = [];
    const section = (key, items, say) => {
      if (!items.length) { L.push([0, key, ' []']); return; }
      L.push([0, key, ` (${items.length})`]);
      items.slice(0, 6).forEach((it) => say(it));
      if (items.length > 6) L.push([1, null, `... and ${items.length - 6} more`]);
    };
    const nodeLine = (it) => {
      const m = byCode.get(it.code);
      L.push([1, null, `- ${refOf(m.code)} ${q(m.title)} [${shown(m)}${lateDays(m) > 0 ? ', overdue' : ''}]` + (it.score ? ` alike=${it.score.toFixed(2)}` : '')]);
      L.push([2, null, `why: ${it.why}`]);
    };
    const decLine = (it) => {
      const d = decById.get(it.id);
      L.push([1, null, `- decision:${d.id} ${q(d.title)} [${d.state}${d.state === 'in force' ? ', ' + (d.confirmation ? 'confirmed by ' + d.confirmation.by : 'unconfirmed') : ''}]`]);
      L.push([2, null, `why: ${it.why}`]);
    };
    const factLine = (it) => {
      const f = factById.get(it.id);
      L.push([1, null, `- fact:${f.id} ${q(f.text)} [${f.state}${f.state === 'in force' ? ', ' + (f.confirmation ? 'confirmed by ' + f.confirmation.by : 'unconfirmed') : ''}]`]);
      L.push([2, null, `why: ${it.why}`]);
    };
    L.push([0, null, `recall(ref="${refOf(n.code)}", reach=${state.reach}, similar=true)`]);
    L.push([0, 'item', ` ${refOf(n.code)} ${q(n.title)}`]);
    L.push([1, 'status', ` ${shown(n)}   priority: ${n.priority}   due: ${n.due || 'none'}` + (p.total && n.kind !== 'task' ? `   progress: ${p.done}/${p.total} tasks` : '')]);
    section('part_of', G.up, nodeLine);
    section('contains', G.down, nodeLine);
    section('needs_first', G.needs, nodeLine);
    section('blocks', G.blocks, nodeLine);
    section('requirements', G.reqs, (it) => { const r = ruleById.get(it.id); L.push([1, null, `- rule:${r.id} ${q(r.title)} [${r.status}]`]); L.push([2, null, `why: ${it.why}`]); });
    section('active_decisions', G.dec, decLine);
    section('project_wide_decisions', G.wide, decLine);
    section('facts', G.facts, factLine);
    section('past_decisions', G.past, decLine);
    section('past_facts', G.pastFacts, factLine);
    section('similar', G.sim, nodeLine);
    return L;
  }
  function agentBlock(n, G) {
    const lines = agentText(n, G);
    return h('div', { class: 'agent', tabindex: '0', role: 'region', 'aria-label': 'What an agent receives from recall for ' + n.code },
      lines.map(([depth, key, rest]) => h('div', { class: 'ag-line', style: `--d:${depth}` }, key ? [h('span', { class: 'ag-key' }, key + ':'), idSpan(rest)] : idSpan(rest))));
  }

  function listPanel(n, G) {
    return h('section', { class: 'panel list-panel', 'aria-labelledby': 'list-h' },
      h('div', { class: 'panel-head' },
        h('div', {}, h('h2', { id: 'list-h' }, state.agent ? 'What an agent receives' : 'The same facts, in words'),
          h('p', { class: 'quiet' }, state.agent ? 'The compact text recall returns, for the same item and reach.' : 'Each line says why it is here.')),
        h('div', { class: 'segmented', role: 'group', 'aria-label': 'Who is reading' },
          h('button', { type: 'button', 'aria-pressed': state.agent ? 'false' : 'true', 'data-fk': 'mode:people', onclick: () => { state.agent = false; renderAll('mode:people'); } }, icon('user', 14), 'People'),
          h('button', { type: 'button', 'aria-pressed': state.agent ? 'true' : 'false', 'data-fk': 'mode:agent', onclick: () => { state.agent = true; renderAll('mode:agent'); } }, icon('agent', 14), 'Agent view'))),
      state.peek && !state.agent ? peekCard() : null,
      state.agent ? agentBlock(n, G) : peopleList(n, G));
  }

  function viewAround(root) {
    if (!state.focus) { emptyState(root, 'around'); return; }
    const n = byCode.get(state.focus);
    const G = around(n.code, state.reach);
    root.append(h('div', { class: 'around' }, egoPanel(n, G), listPanel(n, G)));
  }

  /* ===== 12. Structure: the radial tree ==================================================================== */

  const PRIORITY_R = { low: 5, normal: 6.5, high: 8, urgent: 9.5 };

  // A status mark drawn from the board's own status icons, centred on 0,0 with radius r.
  function statusMark(st, r) {
    const k = r / 9;
    const g = sv('g', { class: 'lf-mark lf-' + st, transform: `scale(${r1(k)}) translate(-12 -12)`, 'stroke-width': r1(1.7 / k) });
    g.append(sv('circle', { class: 'lf-bg', cx: 12, cy: 12, r: 9 }));
    (ICONS[st] || ICONS.new).forEach(([tag, attrs]) => g.append(sv(tag, attrs)));
    return g;
  }

  function layoutEpic(epic) {
    const stories = kidsOf(epic.code);
    const groups = stories.map((s) => {
      const t = kidsOf(s.code);
      return { story: s, leaves: t.length ? t : [s], leafStory: !t.length };
    });
    if (!groups.length) return { groups, empty: true, pos: new Map(), R2: 150 };
    const MIN = 2.6;
    const GAP = 0.6;
    const PITCH = 25;
    const weights = groups.map((g) => (g.leafStory ? 1 : Math.max(g.leaves.length, MIN)) + GAP);
    const W = sum(weights);
    const R2 = Math.max(150, (W * PITCH) / TWO_PI);
    const a = TWO_PI / W;
    const rS = R2 * 0.55;
    const pos = new Map();
    let start = -Math.PI / 2;
    groups.forEach((g, i) => {
      const span = weights[i] * a;
      g.a0 = start;
      g.a1 = start + span;
      g.mid = start + span / 2;
      g.leaves.forEach((leaf, j) => {
        const ang = g.mid + (j - (g.leaves.length - 1) / 2) * a;
        pos.set(leaf.code, { x: R2 * Math.cos(ang), y: R2 * Math.sin(ang), ang, r: R2, leaf: true });
      });
      if (!g.leafStory) pos.set(g.story.code, { x: rS * Math.cos(g.mid), y: rS * Math.sin(g.mid), ang: g.mid, r: rS, leaf: false });
      start += span;
    });
    return { groups, R2, rS, a, pos, empty: false };
  }

  const angDelta = (a, b) => ((((b - a + Math.PI) % TWO_PI) + TWO_PI) % TWO_PI) - Math.PI;
  const sector = (r0, r2, a0, a1) => {
    const [x0, y0] = polar(r0, a0); const [x1, y1] = polar(r0, a1); const [x2, y2] = polar(r2, a1); const [x3, y3] = polar(r2, a0);
    const big = a1 - a0 > Math.PI ? 1 : 0;
    return `M${r1(x0)} ${r1(y0)} A${r0} ${r0} 0 ${big} 1 ${r1(x1)} ${r1(y1)} L${r1(x2)} ${r1(y2)} A${r2} ${r2} 0 ${big} 0 ${r1(x3)} ${r1(y3)} Z`;
  };
  const treeCurve = (from, to) => {
    const mr = (from.r + to.r) / 2;
    const [c1x, c1y] = polar(mr, from.ang);
    const [c2x, c2y] = polar(mr, to.ang);
    return `M${r1(from.x)} ${r1(from.y)} C${r1(c1x)} ${r1(c1y)} ${r1(c2x)} ${r1(c2y)} ${r1(to.x)} ${r1(to.y)}`;
  };

  function crossLinks(code, L) {
    // Every link of the chosen item, split into those drawn inside this epic and those that leave it.
    const rows = [];
    (relMap.get(code) || []).forEach((r) => rows.push({ type: r.type, dir: r.dir, other: r.other }));
    (mentionMap.get(code) || []).forEach((m) => rows.push({ type: 'mentions', dir: m.dir, other: m.other }));
    D.similar.forEach((s) => {
      const other = s.a === code ? s.b : s.b === code ? s.a : null;
      if (other && !hasLink(code, other)) rows.push({ type: 'alike', dir: 'out', other, score: s.score });
    });
    return rows.map((r) => Object.assign(r, { inside: !L || L.pos.has(r.other) }));
  }
  const LINK_TITLE = {
    'needs:out': 'Needs first', 'needs:in': 'Blocks', 'duplicates:out': 'Duplicates', 'duplicates:in': 'Duplicates',
    'supersedes:out': 'Supersedes', 'supersedes:in': 'Superseded by', 'relates:out': 'Related', 'relates:in': 'Related', 'alike:out': 'Alike (from the index, not linked)',
    'mentions:out': 'Names in its text', 'mentions:in': 'Named in the text of',
  };
  const LINK_ORDER = ['Needs first', 'Blocks', 'Duplicates', 'Supersedes', 'Superseded by', 'Related', 'Names in its text', 'Named in the text of', 'Alike (from the index, not linked)'];

  function structureDefs() {
    return sv('defs', {},
      sv('marker', { id: 'st-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 11, markerHeight: 11, orient: 'auto', markerUnits: 'userSpaceOnUse' }, sv('path', { class: 'mk mk-arrow', d: 'M0 1 L10 5 L0 9 z' })));
  }

  function leafNode(node, p, sel, linkedSet) {
    const st = shown(node);
    const isStoryLeaf = node.kind === 'story';
    const r = isStoryLeaf ? 8 : PRIORITY_R[node.priority];
    const late = lateDays(node);
    const g = sv('g', {
      class: 'lf' + (sel === node.code ? ' sel' : '') + (linkedSet.has(node.code) ? ' linked' : '') + (isStoryLeaf ? ' lf-story' : ''),
      transform: `translate(${r1(p.x)} ${r1(p.y)})`, tabindex: '0', role: 'button', 'data-code': node.code, 'data-fk': 'lf:' + node.code,
      'aria-pressed': sel === node.code ? 'true' : 'false',
      'aria-label': `${node.code} ${node.title}. ${STATUS_LABEL[st]}${late > 0 ? `, overdue ${plural(late, 'day')}` : ''}. Press Enter to see what it is linked to.`,
    });
    g.append(sv('circle', { class: 'lf-hit', r: 13 }));
    if (isStoryLeaf) {
      g.append(sv('rect', { class: 'lf-sq', x: -r, y: -r, width: r * 2, height: r * 2, rx: 3 }));
      const m = statusMark(st, 5.2);
      g.append(m);
    } else {
      g.append(statusMark(st, r));
    }
    if (late > 0) g.append(sv('circle', { class: 'lf-late', cx: r * 0.8, cy: -r * 0.8, r: 3.2 }));
    g.append(sv('circle', { class: 'lf-ring', r: r + 4 }));
    return g;
  }

  function drawEpicSvg(svgEl, epic, L, mode) {
    const sel = epicSel(epic);
    const links = sel ? crossLinks(sel, L) : [];
    const linkedSet = new Set(links.filter((l) => l.inside).map((l) => l.other));
    const labelMax = 30;
    const g = sv('g', { class: 'vp', transform: `translate(${r1(state.pan.x)} ${r1(state.pan.y)}) scale(${state.zoom})` });
    const selStory = sel && byCode.get(sel).kind === 'story' ? sel : null;

    // bands behind each story's wedge
    const bands = sv('g', { class: 'bands' });
    L.groups.forEach((gr, i) => {
      const on = selStory === gr.story.code || (sel && gr.leaves.some((x) => x.code === sel));
      bands.append(sv('path', { class: 'band' + (i % 2 ? ' alt' : '') + (on ? ' on' : ''), d: sector(L.rS + 20, L.R2 + 16, gr.a0 + 0.004, gr.a1 - 0.004) }));
    });
    g.append(bands);

    // the tree, drawn first so nothing sits under a line
    const tree = sv('g', { class: 'tree' });
    const centre = { x: 0, y: 0, r: 0, ang: 0 };
    L.groups.forEach((gr) => {
      if (gr.leafStory) {
        const p = L.pos.get(gr.story.code);
        tree.append(sv('line', { class: 'tl', x1: 0, y1: 0, x2: r1(p.x), y2: r1(p.y) }));
        return;
      }
      const sp = L.pos.get(gr.story.code);
      tree.append(sv('line', { class: 'tl', x1: 0, y1: 0, x2: r1(sp.x), y2: r1(sp.y) }));
      gr.leaves.forEach((leaf) => tree.append(sv('path', { class: 'tl', d: treeCurve(sp, L.pos.get(leaf.code)) })));
    });
    g.append(tree);

    // the cross-links of the chosen item only
    if (sel && L.pos.has(sel)) {
      const from = L.pos.get(sel);
      const x = sv('g', { class: 'cross' });
      links.filter((l) => l.inside).forEach((l) => {
        const to = L.pos.get(l.other);
        const d = angDelta(from.ang, to.ang);
        const mid = from.ang + d / 2;
        const base = (from.r + to.r) / 2;
        const rc = Math.max(0.1, 1 - 0.9 * Math.pow(Math.abs(d) / Math.PI, 0.65)) * base;
        const [cx, cy] = polar(rc, mid);
        const trim = (p, dist) => {
          const vx = cx - p.x; const vy = cy - p.y; const len = Math.hypot(vx, vy) || 1;
          return [p.x + (vx / len) * dist, p.y + (vy / len) * dist];
        };
        const rf = from.leaf ? 11 : 17;
        const rt = to.leaf ? 11 : 17;
        // the arrow points the way the work flows: from what comes first to what waits
        let a = from; let b = to; let ra = rf; let rb = rt; let arrow = false;
        if (l.type === 'needs') { arrow = true; if (l.dir === 'out') { a = to; b = from; ra = rt; rb = rf; } }
        else if (l.type === 'supersedes') { arrow = true; if (l.dir === 'in') { a = to; b = from; ra = rt; rb = rf; } }
        const [ax, ay] = trim(a, ra);
        const [bx, by] = trim(b, rb + (arrow ? 3 : 0));
        x.append(sv('path', { class: 'xl xl-' + l.type, d: `M${r1(ax)} ${r1(ay)} Q${r1(cx)} ${r1(cy)} ${r1(bx)} ${r1(by)}`, 'marker-end': arrow ? 'url(#st-arrow)' : null }));
      });
      g.append(x);
    }

    // stories and the centre
    const nodes = sv('g', { class: 'nodes' });
    L.groups.forEach((gr) => {
      if (gr.leafStory) return;
      const s = gr.story;
      const p = L.pos.get(s.code);
      const st = shown(s);
      const pr = progress(s);
      const C = 2 * Math.PI * 17;
      const sg = sv('g', {
        class: 'sn' + (sel === s.code ? ' sel' : '') + (linkedSet.has(s.code) ? ' linked' : '') + ` s-${st}`, transform: `translate(${r1(p.x)} ${r1(p.y)})`,
        tabindex: '0', role: 'button', 'data-code': s.code, 'data-fk': 'lf:' + s.code, 'aria-pressed': sel === s.code ? 'true' : 'false',
        'aria-label': `${s.code} ${s.title}. ${STATUS_LABEL[st]}, ${pr.done} of ${plural(pr.total, 'task')} done. Press Enter to see its tasks.`,
      });
      sg.append(sv('circle', { class: 'sn-ring', r: 21 }));
      sg.append(sv('circle', { class: 'sn-track', r: 17 }));
      if (pr.total) sg.append(sv('circle', { class: 'sn-fill', r: 17, 'stroke-dasharray': `${r1((C * pr.done) / pr.total)} ${r1(C)}`, transform: 'rotate(-90)' }));
      sg.append(sv('circle', { class: 'sn-body', r: 14 }));
      sg.append(sv('text', { class: 'sn-code', 'text-anchor': 'middle', dy: '0.34em' }, s.code));
      nodes.append(sg);
    });
    const ep = progress(epic);
    const eg = sv('g', { class: 'en' });
    const EC = 2 * Math.PI * 41;
    eg.append(sv('circle', { class: 'en-track', r: 41 }));
    if (ep.total) eg.append(sv('circle', { class: 'en-fill', r: 41, 'stroke-dasharray': `${r1((EC * ep.done) / ep.total)} ${r1(EC)}`, transform: 'rotate(-90)' }));
    eg.append(sv('circle', { class: 'en-body', r: 36 }));
    eg.append(sv('text', { class: 'en-code', 'text-anchor': 'middle', y: -3 }, epic.code));
    eg.append(sv('text', { class: 'en-pct', 'text-anchor': 'middle', y: 14 }, ep.total ? `${Math.round((100 * ep.done) / ep.total)}%` : ''));
    nodes.append(eg);
    L.groups.forEach((gr) => gr.leaves.forEach((leaf) => nodes.append(leafNode(leaf, L.pos.get(leaf.code), sel, linkedSet))));
    g.append(nodes);

    // labels last, so they sit over the lines and never under a node
    const labels = sv('g', { class: 'labels' });
    L.groups.forEach((gr) => gr.leaves.forEach((leaf) => {
      const p = L.pos.get(leaf.code);
      const deg = (p.ang * 180) / Math.PI;
      const flip = Math.cos(p.ang) < -0.0001;
      const off = (leaf.kind === 'story' ? 8 : PRIORITY_R[leaf.priority]) + 9;
      const text = mode === 'full' ? trunc(`${leaf.code} ${leaf.title}`, labelMax) : leaf.code;
      labels.append(sv('g', { class: 'lb' + (sel === leaf.code ? ' sel' : '') + (linkedSet.has(leaf.code) ? ' linked' : ''), transform: `rotate(${r1(deg)}) translate(${r1(p.r + off)} 0)`, 'aria-hidden': 'true' },
        sv('text', { class: 'lb-text', transform: flip ? 'rotate(180)' : null, 'text-anchor': flip ? 'end' : 'start', dy: '0.34em' }, text)));
    }));
    g.append(labels);
    put(svgEl, structureDefs(), g);
    svgEl.classList.toggle('has-sel', !!sel);
  }

  function epicExtent(L, mode) {
    const labelLen = mode === 'full' ? 30 * 6.3 : 28;
    let minX = -60; let maxX = 60; let minY = -60; let maxY = 60;
    L.pos.forEach((p) => {
      if (!p.leaf) return;
      const end = polar(p.r + 18 + labelLen, p.ang);
      const side = polar(8, p.ang + Math.PI / 2);
      [end, [end[0] + side[0], end[1] + side[1]], [end[0] - side[0], end[1] - side[1]], polar(p.r + 18, p.ang)].forEach(([x, y]) => {
        minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
      });
    });
    return { x: minX - 14, y: minY - 14, w: maxX - minX + 28, h: maxY - minY + 28 + 40 };
  }

  function linkGroupsFor(code, L) {
    const rows = crossLinks(code, L);
    const groups = new Map();
    rows.forEach((r) => {
      const title = LINK_TITLE[`${r.type}:${r.dir}`];
      if (!groups.has(title)) groups.set(title, []);
      groups.get(title).push(r);
    });
    return LINK_ORDER.filter((t) => groups.has(t)).map((t) => [t, groups.get(t)]);
  }

  function openElsewhere(code) {
    const n = byCode.get(code);
    state.open = epicOf(n).code;
    state.sel = code;
    renderAll('lf:' + code);
  }

  function linkRow(r, L) {
    const n = byCode.get(r.other);
    const st = shown(n);
    const other = epicOf(n);
    return h('li', { class: 'lrow' },
      h('button', { class: 'lrow-main', type: 'button', 'data-fk': 'sl:' + r.other, onclick: () => (r.inside ? (state.sel = r.other, renderAll('sl:' + r.other)) : openElsewhere(r.other)) },
        icon(st, 16), codeText(r.other), h('span', { class: 'lrow-title' }, n.title)),
      h('span', { class: 'lrow-chips' }, statusChip(st), r.score ? chip('info', 'alike ' + r.score.toFixed(2)) : null, r.inside ? null : chip('outside', icon('epic', 14), other.code + ' ' + trunc(other.title, 22))),
      h('p', { class: 'lrow-why' }, r.inside ? 'In this epic: it is highlighted on the tree.' : `In another epic, ${other.code}. Choosing it opens that epic.`));
  }

  // In the one-epic view the epic is the middle of the picture, so only something inside it can be the chosen item.
  const epicSel = (epic) => (state.sel && state.sel !== epic.code && subtree(epic).has(state.sel) ? state.sel : null);

  function structurePanel(epic, L) {
    const sel = epicSel(epic) ? byCode.get(state.sel) : null;
    const p = progress(epic);
    const head = h('div', { class: 'sp-head' },
      h('div', { class: 'peek-head' }, h('span', { class: 'kind-label' }, icon('epic', 16), 'Epic'), codeText(epic.code), statusChip(shown(epic))),
      h('h3', {}, epic.title),
      p.total ? h('div', { class: 'hub-progress' }, h('span', { class: 'meter', role: 'img', 'aria-label': `${p.done} of ${p.total} tasks done` }, h('span', { style: `width:${Math.round((p.done / p.total) * 100)}%` })), h('span', { class: 'quiet' }, `${p.done} of ${plural(p.total, 'task')} done`)) : h('p', { class: 'quiet' }, 'No tasks yet.'),
      h('div', { class: 'hub-actions' }, h('button', { class: 'small', type: 'button', onclick: () => setFocus(epic.code, { reset: true, view: 'around' }) }, icon('around', 14), `Around ${epic.code}`)));
    let body;
    if (!sel) {
      body = h('div', {},
        h('h4', { class: 'sp-h' }, 'Stories'),
        L.groups.length
          ? h('ul', { class: 'plain lrows' }, L.groups.map((gr) => {
              const s = gr.story; const st = shown(s); const pr = progress(s);
              return h('li', { class: 'lrow' },
                h('button', { class: 'lrow-main', type: 'button', 'data-fk': 'sr:' + s.code, onclick: () => { state.sel = s.code; renderAll('sr:' + s.code); } }, icon(st, 16), codeText(s.code), h('span', { class: 'lrow-title' }, s.title)),
                h('span', { class: 'lrow-chips' }, pr.total ? [h('span', { class: 'meter small', role: 'img', 'aria-label': `${pr.done} of ${pr.total} tasks done` }, h('span', { style: `width:${Math.round((pr.done / pr.total) * 100)}%` })), h('span', { class: 'quiet' }, `${pr.done}/${pr.total}`)] : chip('', 'No tasks'), dueChip(s)));
            }))
          : h('p', { class: 'empty' }, 'Nothing is planned in this epic yet, so there is nothing to draw. The epic has no stories.'),
        h('p', { class: 'quiet sp-hint' }, 'Choose a task on the tree to see what it needs, what needs it, and what it duplicates. Those lines are drawn for the chosen task only.'));
    } else {
      const st = shown(sel);
      const late = lateDays(sel);
      const card = h('div', { class: 'sel-card' },
        h('div', { class: 'peek-head' }, h('span', { class: 'kind-label' }, icon(KIND_ICON[sel.kind], 16), KIND_LABEL[sel.kind]), codeText(sel.code), statusChip(st),
          h('button', { class: 'ghost icon-only small', type: 'button', 'aria-label': 'Clear the choice', 'data-fk': 'sel-clear', onclick: () => { state.sel = null; renderAll('sel-clear'); } }, icon('close', 14))),
        h('h3', {}, sel.title),
        h('div', { class: 'hub-meta' }, priorityChip(sel.priority), dueChip(sel), sel.held ? chip('held', icon('user', 14), 'Held by ' + sel.held) : null, late > 0 ? null : null),
        h('div', { class: 'hub-actions' },
          h('button', { class: 'small', type: 'button', onclick: () => setFocus(sel.code, { reset: false, view: 'around' }) }, icon('around', 14), 'Show around'),
          h('button', { class: 'small ghost', type: 'button', onclick: () => toast('Opens the item page in the real app') }, 'Open page', icon('arrow_right', 14))));
      const linkGroups = linkGroupsFor(sel.code, L);
      const own = sel.kind === 'story' && kidsOf(sel.code).length
        ? h('div', {}, h('h4', { class: 'sp-h' }, `Tasks in ${sel.code}`),
            h('ul', { class: 'plain lrows' }, kidsOf(sel.code).map((t) => {
              const ts = shown(t);
              return h('li', { class: 'lrow' + (ts === 'dropped' ? ' is-ended' : '') }, h('button', { class: 'lrow-main', type: 'button', 'data-fk': 'sl:' + t.code, onclick: () => { state.sel = t.code; renderAll('sl:' + t.code); } }, icon(ts, 16), codeText(t.code), h('span', { class: 'lrow-title' }, t.title)),
                h('span', { class: 'lrow-chips' }, statusChip(ts), dueChip(t)));
            })))
        : null;
      body = h('div', {}, card, own,
        linkGroups.length
          ? linkGroups.map(([title, rows]) => h('section', { class: 'lgroup' }, h('h4', { class: 'sp-h' }, title, h('span', { class: 'gbox-count' }, rows.length)), h('ul', { class: 'plain lrows' }, rows.map((r) => linkRow(r, L)))))
          : h('p', { class: 'quiet sp-hint' }, `${sel.code} is not linked to anything else, so nothing else is highlighted.`));
    }
    const key = h('div', { class: 'sp-key' },
      h('h4', { class: 'sp-h' }, 'How to read the tree'),
      h('ul', { class: 'plain key-list' },
        ['new', 'ready', 'in_progress', 'blocked', 'done', 'dropped'].map((s) => h('li', {}, icon(s, 14, 'ks-' + s), STATUS_LABEL[s])),
        h('li', { class: 'key-wide' }, h('span', { class: 'late-dot' }), 'Overdue')),
      h('p', { class: 'quiet' }, 'The epic is in the middle, its stories in the ring, and the tasks are the leaves on the outside. A bigger leaf is a higher priority; the ring around a story shows how much of it is done. Square leaves are stories with no tasks yet.'));
    return h('aside', { class: 'panel struct-panel', 'aria-label': 'Details of the chosen item' }, head, body, key);
  }

  function epicLevel(root, epic) {
    const L = layoutEpic(epic);
    const canvasInner = h('div', { class: 'canvas-inner' });
    const scroller = h('div', { class: 'canvas-scroll', tabindex: '0', role: 'region', 'aria-label': 'The tree of ' + epic.code + '. It scrolls sideways on a small screen.' }, canvasInner);
    const tip = h('div', { class: 'tip', hidden: true, role: 'presentation' });
    const svgEl = sv('svg', { class: 'struct-svg', role: 'group', 'aria-label': `Radial tree of ${epic.code}: stories in a ring, tasks on the outside` });
    const crumbs = h('nav', { class: 'canvas-crumbs', 'aria-label': 'Where you are in the structure' },
      h('button', { class: 'link', type: 'button', 'data-fk': 'crumb-project', onclick: () => { state.open = null; state.sel = epic.code; renderAll('crumb-project'); } }, 'Whole project'),
      icon('chevron_right', 14),
      h('span', { 'aria-current': 'true', class: 'crumb-here' }, `${epic.code} ${trunc(epic.title, 34)}`));
    const zoomBtn = (label, aria, fn, ic) => h('button', { class: 'small icon-only', type: 'button', 'aria-label': aria, title: aria, onclick: fn }, ic || label);
    const controls = h('div', { class: 'canvas-zoom', role: 'group', 'aria-label': 'Zoom' },
      zoomBtn('-', 'Zoom out', () => { state.zoom = Math.max(0.6, r1(state.zoom - 0.2)); redraw(); }, '\u2212'),
      h('button', { class: 'small', type: 'button', 'aria-label': 'Fit the whole tree in view', onclick: () => { state.zoom = 1; state.pan = { x: 0, y: 0 }; redraw(); } }, 'Fit'),
      zoomBtn('+', 'Zoom in', () => { state.zoom = Math.min(2.4, r1(state.zoom + 0.2)); redraw(); }, '+'));
    const noteHolder = h('div', { class: 'canvas-note-holder' });
    canvasInner.append(svgEl, tip);
    const canvas = h('div', { class: 'canvas' + (epicSel(epic) ? ' has-sel' : '') }, scroller, crumbs, controls, noteHolder);
    const panelHead = h('div', { class: 'panel-head' },
      h('div', {}, h('h2', { id: 'struct-h' }, `Focus on ${epic.code}`), h('p', { class: 'quiet' }, 'One epic at a time: its stories around it, every task as a leaf on the outside, and the names shown because there is room. Nothing crosses.')));
    const main = h('section', { class: 'panel struct-main', 'aria-labelledby': 'struct-h' }, panelHead, canvas);
    root.append(h('div', { class: 'structure' }, main, structurePanel(epic, L)));

    if (L.empty) {
      controls.remove();
      put(svgEl);
      svgEl.setAttribute('viewBox', '-340 -170 680 340');
      svgEl.append(sv('circle', { class: 'en-track', r: 41 }), sv('circle', { class: 'en-body', r: 36 }), sv('text', { class: 'en-code', 'text-anchor': 'middle', y: 4 }, epic.code),
        sv('text', { class: 'empty-note', 'text-anchor': 'middle', y: 78 }, 'Nothing is planned yet: no stories, so no leaves'));
      return;
    }
    let lastW = 0;
    const redraw = () => {
      const width = Math.max(canvasInner.clientWidth || 700, 560);
      const mode0 = 'full';
      const ext0 = epicExtent(L, mode0);
      const scale = Math.min(width / ext0.w, 1.4);
      const mode = scale >= 0.78 ? 'full' : 'code';
      const ext = epicExtent(L, mode);
      svgEl.setAttribute('viewBox', `${r1(ext.x)} ${r1(ext.y)} ${r1(ext.w)} ${r1(ext.h)}`);
      drawEpicSvg(svgEl, epic, L, mode);
      canvas.classList.toggle('has-sel', !!epicSel(epic));
      // what leaves this epic is listed on the canvas too, so it is not only in the side list
      const chosen = epicSel(epic);
      const outside = chosen ? crossLinks(chosen, L).filter((l) => !l.inside) : [];
      put(noteHolder, outside.length
        ? h('div', { class: 'canvas-note' }, h('span', { class: 'quiet' }, `${chosen} is also linked outside this epic:`),
            outside.map((l) => h('button', { class: 'chip chip-link', type: 'button', title: byCode.get(l.other).title, onclick: () => openElsewhere(l.other) }, icon('arrow_right', 14), `${l.other} in ${epicOf(byCode.get(l.other)).code}`)))
        : null);
      wire();
    };
    const showTip = (el) => {
      const code = el.getAttribute('data-code');
      const n = byCode.get(code);
      const st = shown(n);
      const late = lateDays(n);
      const r = el.getBoundingClientRect();
      const c = canvasInner.getBoundingClientRect();
      put(tip, h('div', { class: 'tip-top' }, icon(st, 14), codeText(n.code), h('span', {}, STATUS_LABEL[st])), h('div', { class: 'tip-title' }, n.title), n.due ? h('div', { class: 'quiet' }, late > 0 ? `Overdue ${plural(late, 'day')} (due ${n.due})` : `Due ${n.due}`) : null);
      tip.hidden = false;
      tip.style.left = `${Math.round(r.left - c.left + r.width / 2)}px`;
      tip.style.top = `${Math.round(r.top - c.top - 8)}px`;
    };
    const wire = () => {
      $$('.lf, .sn', svgEl).forEach((el) => {
        const code = el.getAttribute('data-code');
        const pick = () => { state.sel = state.sel === code ? null : code; renderAll('lf:' + code); };
        el.addEventListener('click', (e) => { e.stopPropagation(); pick(); });
        el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
        el.addEventListener('pointerenter', () => showTip(el));
        el.addEventListener('pointerleave', () => { tip.hidden = true; });
        el.addEventListener('focus', () => showTip(el));
        el.addEventListener('blur', () => { tip.hidden = true; });
      });
      let drag = null;
      svgEl.onpointerdown = (e) => { if (e.target.closest('.lf, .sn')) return; drag = { x: e.clientX - state.pan.x, y: e.clientY - state.pan.y }; svgEl.setPointerCapture(e.pointerId); svgEl.classList.add('grabbing'); };
      svgEl.onpointermove = (e) => { if (!drag) return; state.pan = { x: e.clientX - drag.x, y: e.clientY - drag.y }; $('.vp', svgEl).setAttribute('transform', `translate(${r1(state.pan.x)} ${r1(state.pan.y)}) scale(${state.zoom})`); };
      svgEl.onpointerup = () => { drag = null; svgEl.classList.remove('grabbing'); };
    };
    redraw();
    lastW = canvasInner.clientWidth;
    // on a small screen the tree is wider than the window: start with its middle in view
    if (scroller.scrollWidth > scroller.clientWidth) scroller.scrollLeft = (scroller.scrollWidth - scroller.clientWidth) / 2;
    watch(canvasInner, () => { const w = canvasInner.clientWidth; if (Math.abs(w - lastW) > 10) { lastW = w; redraw(); } });
  }

  /* ===== 12b. Structure: the whole project, as dandelions joined in a tree ================================ */

  const RANK = { epic: 0, story: 1, task: 2 };
  const LEVEL_MAX = { epics: 0, stories: 1, tasks: 2 };
  const TASK_DOT = { low: 3.2, normal: 4, high: 4.8, urgent: 5.6 };
  const epicIndex = new Map(epics.map((e, i) => [e.code, i + 1]));

  // A small random source, so that "random" looks the same on every visit.
  function seeded(seed) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  const wpCache = {};
  // Where everything goes. Each story is a dandelion (its tasks fan out away from it), each epic a bunch of
  // dandelions around a hub, and the epics sit round the project like a tree seen from above. Every cluster is
  // worked out in a frame where the way to its parent is angle 0 and then turned to face its parent, so the lines
  // up the tree come in through a gap that is kept clear, and clusters are spaced by their radii: nothing overlaps
  // and no hierarchy line can cross another. Seeded, so the picture is identical on every visit.
  function wpLayout(level) {
    if (wpCache[level]) return wpCache[level];
    const rnd = seeded(20261117);
    const maxRank = LEVEL_MAX[level];
    const rEpic = maxRank > 0 ? 25 : 32;
    const rStory = maxRank > 1 ? 9 : 13;
    const rProj = 50;
    const rot = (x, y, a) => [x * Math.cos(a) - y * Math.sin(a), x * Math.sin(a) + y * Math.cos(a)];

    const storyCluster = (s) => {
      const tasks = maxRank > 1 ? kidsOf(s.code) : [];
      const n = tasks.length;
      if (!n) return { node: s, R: rStory + 5, dots: [] };
      const gap = 0.5;
      const fan = Math.min(TWO_PI - 2 * gap, (n - 1) * 0.85);
      const step = n > 1 ? fan / (n - 1) : 0;
      const d = Math.max(rStory + TASK_DOT.urgent + 5, n > 1 ? 13.6 / step : 0);
      const dots = tasks.map((t, i) => {
        const a = Math.PI + (n > 1 ? -fan / 2 + i * step : 0) + (rnd() - 0.5) * step * 0.1;
        const dist = d + (rnd() - 0.5) * 2;
        return { node: t, x: dist * Math.cos(a), y: dist * Math.sin(a), r: TASK_DOT[t.priority] };
      });
      return { node: s, R: Math.max(...dots.map((o) => Math.hypot(o.x, o.y) + o.r)) + 4, dots };
    };

    const epicCluster = (e) => {
      const stories = maxRank > 0 ? kidsOf(e.code) : [];
      if (!stories.length) return { node: e, R: rEpic + 8, items: [] };
      const cl = stories.map(storyCluster);
      const R = cl.map((c) => c.R);
      const m = cl.length;
      const G = 9;
      const gap = 0.55;
      const span = TWO_PI - 2 * gap;
      const arcs = (dist) => R.slice(0, -1).map((r, i) => 2 * Math.asin(Math.min(1, (r + R[i + 1] + G) / (2 * dist))));
      let dist = Math.max(rEpic + Math.max(...R) + G, (R[0] + 6) / Math.sin(gap), (R[m - 1] + 6) / Math.sin(gap));
      while (sum(arcs(dist)) > span) dist += 1;
      const gaps = arcs(dist);
      const total = sum(gaps);
      const target = Math.min(span, Math.max(total, (m - 1) * 0.95));
      const f = total ? target / total : 0;
      let a = Math.PI - target / 2;
      const items = cl.map((c, i) => {
        if (i) a += gaps[i - 1] * f;
        return { cl: c, x: dist * Math.cos(a), y: dist * Math.sin(a) };
      });
      return { node: e, R: dist + Math.max(...R), items };
    };

    // The epics sit round the project on a wide ellipse (the picture is wider than tall): each as far from the
    // next as their sizes need, then spread evenly.
    const clusters = epics.map(epicCluster);
    const Re = clusters.map((c) => c.R);
    const n = Re.length;
    const GE = 30;
    const need = Re.map((r, i) => r + Re[(i + 1) % n] + GE);
    const stepFrom = (a, b, t, dist) => {
      const x0 = a * Math.cos(t);
      const y0 = b * Math.sin(t);
      let u = t;
      while (u - t < Math.PI) {
        u += 0.004;
        if (Math.hypot(a * Math.cos(u) - x0, b * Math.sin(u) - y0) >= dist) return u - t;
      }
      return null;
    };
    const RHO = 1.3;
    const around = (b, needs) => {
      const a = RHO * b;
      const steps = [];
      let t = -Math.PI / 2;
      for (let i = 0; i < n; i += 1) {
        const dt = stepFrom(a, b, t, needs[i]);
        if (dt === null) return null;
        steps.push(dt);
        t += dt;
      }
      return sum(steps) <= TWO_PI ? steps : null;
    };

    const build = (needs) => {
      let b = rProj + Math.max(...Re);
      let steps = around(b, needs);
      while (!steps) { b += 2; steps = around(b, needs); }
      const aEll = RHO * b;
      const f = TWO_PI / sum(steps);
      const nodes = [];
      const edges = [];
      const pos = new Map();
      const clusterR = new Map();
      const put1 = (node, x, y, r, parent) => {
        const o = { code: node.code, kind: node.kind, x, y, r, parent, epic: epicIndex.get(epicOf(node).code) - 1 };
        nodes.push(o);
        pos.set(node.code, o);
        edges.push([parent, node.code]);
        return o;
      };
      let t = -Math.PI / 2;
      clusters.forEach((c, i) => {
        if (i) t += steps[i - 1] * f;
        const ex = aEll * Math.cos(t);
        const ey = b * Math.sin(t);
        put1(c.node, ex, ey, rEpic, 'root');
        clusterR.set(c.node.code, c.R);
        const phi = Math.atan2(-ey, -ex);
        c.items.forEach((it) => {
          const [lx, ly] = rot(it.x, it.y, phi);
          const sx = ex + lx;
          const sy = ey + ly;
          put1(it.cl.node, sx, sy, rStory, c.node.code);
          const psi = Math.atan2(ey - sy, ex - sx);
          it.cl.dots.forEach((dot) => {
            const [dx, dy] = rot(dot.x, dot.y, psi);
            put1(dot.node, sx + dx, sy + dy, dot.r, it.cl.node.code);
          });
        });
      });
      return { nodes, edges, pos, clusterR };
    };

    // A line from the project to one epic must not brush the dots of another: where it does, those two
    // neighbourhoods are given more room and the whole thing is laid out again.
    const brushes = (built) => {
      const out = [];
      epics.forEach((e, i) => {
        const hub = built.pos.get(e.code);
        const dx = hub.x;
        const dy = hub.y;
        const len2 = dx * dx + dy * dy;
        built.nodes.forEach((o) => {
          if (o.epic === i) return;
          const t = Math.max(0, Math.min(1, (o.x * dx + o.y * dy) / len2));
          if (Math.hypot(o.x - t * dx, o.y - t * dy) < o.r + 7) out.push([o.epic, i]);
        });
      });
      return out;
    };
    const needs = need.slice();
    let built = build(needs);
    for (let pass = 0; pass < 120; pass += 1) {
      const bad = brushes(built);
      if (!bad.length) break;
      bad.forEach(([v, s]) => [v, (v + n - 1) % n, s, (s + n - 1) % n].forEach((i) => { needs[i] += 4; }));
      built = build(needs);
    }
    const { nodes, edges, pos, clusterR } = built;
    const bb = { x0: -rProj, y0: -rProj, x1: rProj, y1: rProj };
    nodes.forEach((o) => {
      bb.x0 = Math.min(bb.x0, o.x - o.r); bb.x1 = Math.max(bb.x1, o.x + o.r);
      bb.y0 = Math.min(bb.y0, o.y - o.r); bb.y1 = Math.max(bb.y1, o.y + o.r);
    });
    wpCache[level] = { nodes, edges, pos, clusterR, bounds: bb, rProj, rEpic };
    return wpCache[level];
  }

  const liftTo = (code, level) => {
    let n = byCode.get(code);
    while (RANK[n.kind] > LEVEL_MAX[level]) n = byCode.get(n.parent);
    return n.code;
  };
  const visKids = (code, level) => kidsOf(code).filter((k) => RANK[k.kind] <= LEVEL_MAX[level]);

  // Every link and reference of one item, with the other end lifted to what the level shows.
  function wpPartners(code, level) {
    const seen = new Set();
    return crossLinks(code, null).map((r) => Object.assign({}, r, { other: liftTo(r.other, level) })).filter((r) => {
      const key = r.type + ':' + r.other;
      if (r.other === code || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  // All the links between work, as [type, from, to] with the arrow (if any) pointing the way the work flows.
  function wpAllLinks(level) {
    const out = new Map();
    const add = (type, a, b) => {
      const x = liftTo(a, level);
      const y = liftTo(b, level);
      if (x === y) return;
      const key = type + ':' + x + ':' + y;
      if (!out.has(key)) out.set(key, { type, from: x, to: y });
    };
    D.links.forEach(([type, a, b]) => {
      if (type === 'needs') add('needs', b, a);
      else if (type === 'relates') add('relates', a, b);
      else if (type === 'duplicates') add('duplicates', a, b);
      else add('supersedes', a, b);
    });
    mentionPairs.forEach(([a, b]) => add('mentions', a, b));
    return Array.from(out.values());
  }

  const LABEL_MODES = [['off', 'Off', 'No names on the picture; hover a dot or hub to read it'], ['zoom', 'On zoom', 'Names appear as you zoom in'], ['always', 'Always', 'As many names as fit']];
  const LEVELS = [['epics', 'Epics', 'Show the epics only'], ['stories', '+ Stories', 'Show epics and stories'], ['tasks', '+ Tasks', 'Show epics, stories and tasks']];

  function focusEpic(code, key) {
    state.open = code;
    state.sel = null;
    state.focus = code;
    state.trail = [code];
    state.zoom = 1;
    state.pan = { x: 0, y: 0 };
    renderAll(key || null);
  }

  function chooseNode(code, key) {
    state.sel = state.sel === code ? null : code;
    state.wp.at = code;
    renderAll(key);
  }

  function wpTip(code) {
    const n = byCode.get(code);
    const st = shown(n);
    const late = lateDays(n);
    const p = progress(n);
    let line = null;
    if (n.kind === 'epic') line = `${plural(kidsOf(n.code).length, 'story', 'stories')} \u00b7 ${plural(p.total, 'task')} \u00b7 ${p.total ? Math.round((100 * p.done) / p.total) : 0}% done`;
    else if (n.kind === 'story') line = `${plural(p.total, 'task')}, ${p.done} done`;
    else line = `${cap(n.priority)} priority` + (n.due ? (late > 0 ? ` \u00b7 overdue ${plural(late, 'day')}` : ` \u00b7 due ${n.due}`) : '');
    return [
      h('div', { class: 'tip-top' }, icon(st, 14), codeText(n.code), h('span', {}, `${KIND_LABEL[n.kind]} \u00b7 ${STATUS_LABEL[st]}`)),
      h('div', { class: 'tip-title' }, n.title),
      h('div', { class: 'quiet' }, line),
      n.held ? h('div', { class: 'quiet' }, 'Held by ' + n.held) : null,
    ];
  }

  function wholeProject(root) {
    const W = state.wp;
    const view = W;
    const L = wpLayout(W.level);
    const sel = state.sel ? liftTo(state.sel, W.level) : null;
    if (sel !== state.sel) state.sel = sel;
    const partners = sel ? wpPartners(sel, W.level) : [];
    const partnerSet = new Set(partners.map((p) => p.other));
    const pathSet = new Set();
    if (sel) {
      let up = byCode.get(sel);
      while (up) { pathSet.add(up.code); up = up.parent ? byCode.get(up.parent) : null; }
      const down = (c) => visKids(c, W.level).forEach((k) => { pathSet.add(k.code); down(k.code); });
      down(sel);
    }
    const lit = new Set([...pathSet, ...partnerSet]);
    const vb = { x: L.bounds.x0 - 30, y: L.bounds.y0 - 30, w: L.bounds.x1 - L.bounds.x0 + 60, h: L.bounds.y1 - L.bounds.y0 + 60 };
    let hover = null;

    /* -- the picture -- */
    const svgEl = sv('svg', {
      class: 'wp-svg' + (W.color === 'epic' ? ' by-epic' : '') + (sel ? ' has-sel' : ''),
      viewBox: `${r1(vb.x)} ${r1(vb.y)} ${r1(vb.w)} ${r1(vb.h)}`, role: 'group',
      'aria-label': `The whole project: ${plural(epics.length, 'epic')}${W.level !== 'epics' ? ', their stories' : ''}${W.level === 'tasks' ? ' and tasks' : ''}, joined as a tree`,
    });
    const defs = sv('defs', {},
      sv('marker', { id: 'wp-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 8, markerHeight: 8, orient: 'auto', markerUnits: 'userSpaceOnUse' }, sv('path', { class: 'mk mk-arrow', d: 'M0 1 L10 5 L0 9 z' })));
    const vp = sv('g', { class: 'vp' });
    const hier = sv('g', { class: 'hier' });
    const cross = sv('g', { class: 'cross' });
    const nodesG = sv('g', { class: 'wnodes' });
    const labelG = sv('g', { class: 'wlabels', 'aria-hidden': 'true' });

    L.edges.forEach(([from, to]) => {
      const a = from === 'root' ? { x: 0, y: 0, r: L.rProj } : L.pos.get(from);
      const b = L.pos.get(to);
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const len = Math.hypot(dx, dy) || 1;
      const on = pathSet.has(to) && (from === 'root' || pathSet.has(from));
      hier.append(sv('line', { class: 'tl' + (on ? ' lit' : ''), x1: r1(a.x + (dx / len) * a.r), y1: r1(a.y + (dy / len) * a.r), x2: r1(b.x - (dx / len) * b.r), y2: r1(b.y - (dy / len) * b.r) }));
    });

    // the links between work: all of them when asked, and always the chosen item's
    const bend = (p, q) => {
      const mx = (p.x + q.x) / 2;
      const my = (p.y + q.y) / 2;
      const nx = -(q.y - p.y);
      const ny = q.x - p.x;
      const k = 0.2;
      const c1 = [mx + nx * k, my + ny * k];
      const c2 = [mx - nx * k, my - ny * k];
      return Math.hypot(...c1) < Math.hypot(...c2) ? c1 : c2;
    };
    const arrowTypes = ['needs', 'supersedes'];
    const drawLink = (type, from, to, cls) => {
      const p = L.pos.get(from);
      const q = L.pos.get(to);
      if (!p || !q) return;
      const [cx, cy] = bend(p, q);
      const trim = (o, tx, ty, extra) => {
        const vx = tx - o.x; const vy = ty - o.y; const l = Math.hypot(vx, vy) || 1;
        return [o.x + (vx / l) * (o.r + extra), o.y + (vy / l) * (o.r + extra)];
      };
      const arrow = arrowTypes.includes(type);
      const [x1, y1] = trim(p, cx, cy, 1.5);
      const [x2, y2] = trim(q, cx, cy, arrow ? 2.5 : 1.5);
      cross.append(sv('path', { class: `xl xl-${type} ${cls}`, d: `M${r1(x1)} ${r1(y1)} Q${r1(cx)} ${r1(cy)} ${r1(x2)} ${r1(y2)}`, 'marker-end': arrow ? 'url(#wp-arrow)' : null }));
    };
    const chosenLinks = new Set();
    if (sel) {
      crossLinks(sel, null).forEach((r) => {
        const other = liftTo(r.other, W.level);
        if (other === sel) return;
        let type = r.type === 'alike' ? 'alike' : r.type;
        let from = sel;
        let to = other;
        if (type === 'needs' && r.dir === 'out') { from = other; to = sel; }
        else if (type === 'supersedes' && r.dir === 'in') { from = other; to = sel; }
        else if (type === 'mentions' && r.dir === 'in') { from = other; to = sel; }
        chosenLinks.add(`${type}:${from}:${to}`);
        chosenLinks.add(`${type}:${to}:${from}`);
        drawLink(type === 'duplicates' ? 'duplicates' : type, from, to, 'chosen');
      });
    }
    if (W.links) {
      wpAllLinks(W.level).forEach((l) => { if (!chosenLinks.has(`${l.type}:${l.from}:${l.to}`)) drawLink(l.type, l.from, l.to, 'all'); });
    }
    cross.setAttribute('class', 'cross' + (W.links ? ' all' : ''));

    // the nodes: the project hub, then tasks first so that hubs sit on top
    nodesG.append(sv('g', { class: 'wn-project' }, sv('circle', { class: 'ph-ring', r: L.rProj + 7 }), sv('circle', { class: 'ph-body', r: L.rProj })));
    const order = L.nodes.slice().sort((x, y) => RANK[y.kind] - RANK[x.kind]);
    order.forEach((o) => {
      const node = byCode.get(o.code);
      const st = shown(node);
      const late = lateDays(node);
      const p = progress(node);
      const hub = o.kind !== 'task';
      const g = sv('g', {
        class: `wn k-${o.kind} s-${st} ep-${epicIndex.get(epicOf(node).code)}` + (late > 0 ? ' late' : '') + (sel === o.code ? ' sel' : '') + (partnerSet.has(o.code) ? ' linked' : '') + (lit.has(o.code) ? ' lit' : ''),
        transform: `translate(${r1(o.x)} ${r1(o.y)})`, tabindex: o.kind === 'task' ? '-1' : '0', role: 'button', 'data-code': o.code, 'data-fk': 'wn:' + o.code,
        'aria-pressed': sel === o.code ? 'true' : 'false',
        'aria-label': `${o.code} ${node.title}. ${KIND_LABEL[o.kind]}, ${STATUS_LABEL[st]}${late > 0 ? `, overdue ${plural(late, 'day')}` : ''}${hub ? `, ${p.done} of ${plural(p.total, 'task')} done` : ''}. ${o.kind === 'epic' ? 'Press Enter to focus on this epic, Space to choose it.' : 'Press Enter to choose it and see what it is linked to.'}`,
      });
      g.append(sv('circle', { class: 'wn-hit', r: Math.max(o.r + 3, 7) }));
      if (hub && p.total && (o.kind === 'epic' || W.level !== 'tasks')) {
        const rr = o.r + 3.6;
        const C = TWO_PI * rr;
        g.append(sv('circle', { class: 'wn-track', r: rr }));
        g.append(sv('circle', { class: 'wn-fill', r: rr, 'stroke-dasharray': `${r1((C * p.done) / p.total)} ${r1(C)}`, transform: 'rotate(-90)' }));
      }
      g.append(sv('circle', { class: 'wn-body', r: o.r }));
      if (st === 'blocked' || late > 0) g.append(sv('circle', { class: 'wn-flag ' + (st === 'blocked' ? 'danger' : 'warn'), r: o.r + (hub ? (o.kind === 'epic' ? 9 : 7) : 2.3) }));
      g.append(sv('circle', { class: 'wn-ring', r: o.r + (hub ? (o.kind === 'epic' ? 11 : 9) : 6) }));
      nodesG.append(g);
    });
    vp.append(hier, cross, nodesG, labelG);
    svgEl.append(defs, vp);

    /* -- the names: a fixed size on the screen, placed so that none overlaps another -- */
    const charW = 6.5;
    const monoW = 7.5;
    const textW = (lines) => Math.max(...lines.map((l) => {
      const code = /^[EST]-\d+/.exec(l);
      return code ? code[0].length * monoW + (l.length - code[0].length) * charW : l.length * charW;
    }));
    const drawLabels = () => {
      const ctm = svgEl.getScreenCTM();
      if (!ctm) return;
      const s = ctm.a;
      const k = view.k;
      const inv = 1 / (s * k);
      const box = svgEl.getBoundingClientRect();
      const px = (wx) => (wx * k + view.x) * s + ctm.e - box.left;
      const py = (wy) => (wy * k + view.y) * s + ctm.f - box.top;
      const activeEpics = new Set();
      // choosing or hovering a hub (not a single task) brings up the codes of the stories round it
      if (sel && byCode.get(sel).kind !== 'task') activeEpics.add(epicOf(byCode.get(sel)).code);
      if (hover && byCode.get(hover).kind !== 'task') activeEpics.add(epicOf(byCode.get(hover)).code);
      const cands = [];
      const dirOf = (o) => {
        const par = o.parent === 'root' ? { x: 0, y: 0 } : L.pos.get(o.parent);
        const dx = o.x - par.x;
        const dy = o.y - par.y;
        const l = Math.hypot(dx, dy) || 1;
        return [dx / l, dy / l];
      };
      const mode = W.labels;
      const named = new Set(sel ? [sel, ...partnerSet] : []);
      const circles = L.nodes.map((o) => ({ x: px(o.x), y: py(o.y), r: o.r * s * k + 2 }));
      circles.push({ x: px(0), y: py(0), r: L.rProj * s * k + 8 });
      const hitsCircle = (b) => circles.some((c) => {
        if (c.x < b[0] - 60 || c.x > b[2] + 60) return false;
        const nx = Math.max(b[0], Math.min(c.x, b[2]));
        const ny = Math.max(b[1], Math.min(c.y, b[3]));
        return Math.hypot(c.x - nx, c.y - ny) < c.r;
      });
      if (mode !== 'off') {
        L.nodes.forEach((o) => {
          if (o.kind === 'epic') return;
          const node = byCode.get(o.code);
          let [ux, uy] = dirOf(o);
          const isStory = o.kind === 'story';
          // a story's tasks fan out away from it, so its name goes on the line to its epic, where there is a gap
          const nameOnStem = isStory && LEVEL_MAX[W.level] > 1 && kidsOf(o.code).length > 0;
          if (nameOnStem) { ux = -ux; uy = -uy; }
          let lines = null;
          let prio = 3;
          if (named.has(o.code)) { lines = wrap(`${o.code} ${node.title}`, 24, 2); prio = 1; }
          else if (mode === 'always') { lines = [isStory ? trunc(`${o.code} ${node.title}`, 26) : o.code]; prio = isStory ? 2 : 4; }
          else if (isStory) {
            if (k >= 3.2) lines = [trunc(`${o.code} ${node.title}`, 30)];
            else if (k >= 1.8 || activeEpics.has(epicOf(node).code)) lines = [o.code];
            prio = 2;
          } else if (k >= 5.5) lines = [trunc(`${o.code} ${node.title}`, 30)];
          else if (k >= 3.6) lines = [o.code];
          else if (activeEpics.has(epicOf(node).code) && k >= 2.4) lines = [o.code];
          if (lines) cands.push({ code: o.code, prio, o, ux, uy, lines });
        });
      }
      cands.sort((a, b) => a.prio - b.prio);
      const placed = [];
      const out = [];
      const lineH = 14.5;
      const overlaps = (b) => placed.some((q) => b[0] < q[2] + 2 && b[2] > q[0] - 2 && b[1] < q[3] + 2 && b[3] > q[1] - 2);
      // the picture's own buttons are in the way of a name, too
      const ctl = controls.getBoundingClientRect();
      placed.push([ctl.left - box.left - 4, ctl.top - box.top - 4, ctl.right - box.left + 4, ctl.bottom - box.top + 4]);

      // An epic's name sits as near to its hub as there is clear room: on the line to the project, or a little to
      // either side of it. The middle of the picture is calm, so the name needs no margin round the outside.
      if (mode !== 'off') {
        const hubPx = { x: px(0), y: py(0) };
        const spokes = epics.map((e) => ({ code: e.code, x1: hubPx.x, y1: hubPx.y, x2: px(L.pos.get(e.code).x), y2: py(L.pos.get(e.code).y) }));
        const segHitsBox = (sg, b) => {
          const inside = (x, y) => x >= b[0] && x <= b[2] && y >= b[1] && y <= b[3];
          if (inside(sg.x1, sg.y1) || inside(sg.x2, sg.y2)) return true;
          const edges = [[b[0], b[1], b[2], b[1]], [b[2], b[1], b[2], b[3]], [b[2], b[3], b[0], b[3]], [b[0], b[3], b[0], b[1]]];
          const ccw = (ax, ay, bx, by, cx, cy) => (cy - ay) * (bx - ax) > (by - ay) * (cx - ax);
          return edges.some(([x3, y3, x4, y4]) => ccw(sg.x1, sg.y1, x3, y3, x4, y4) !== ccw(sg.x2, sg.y2, x3, y3, x4, y4) && ccw(sg.x1, sg.y1, sg.x2, sg.y2, x3, y3) !== ccw(sg.x1, sg.y1, sg.x2, sg.y2, x4, y4));
        };
        epics.forEach((e) => {
          const o = L.pos.get(e.code);
          const hx = px(o.x);
          const hy = py(o.y);
          if (hx < -30 || hx > box.width + 30 || hy < -30 || hy > box.height + 30) return; // its hub is not on the screen
          const dist = Math.hypot(o.x, o.y);
          const base = Math.atan2(-o.y, -o.x);
          // the whole name if there is room, a narrower one if not, and the code alone as a last resort
          const variants = [wrap(`${e.code} ${e.title}`, 16, 3), wrap(`${e.code} ${e.title}`, 11, 4), [e.code]];
          let best = null;
          let lines = variants[0];
          for (const v of variants) {
            const w = textW(v);
            const hh = v.length * lineH;
            [0, 0.6, -0.6, 1.2, -1.2, 1.8, -1.8, Math.PI].forEach((turn) => {
              const ux = Math.cos(base + turn);
              const uy = Math.sin(base + turn);
              for (let t = o.r + 12; t < Math.max(dist - L.rProj - 6, o.r + 40); t += 7) {
                const cx = px(o.x + ux * t);
                const cy = py(o.y + uy * t);
                if (Math.hypot(cx - hx, cy - hy) > 190) break;
                const b = [cx - w / 2, cy - hh / 2, cx + w / 2, cy + hh / 2];
                if (b[0] < 4 || b[2] > box.width - 4 || b[1] < 4 || b[3] > box.height - 4) continue;
                if (overlaps(b) || hitsCircle(b)) continue;
                const crossings = spokes.filter((sg) => sg.code !== e.code && segHitsBox(sg, b)).length;
                const cost = t + Math.abs(turn) * 10 + crossings * 30;
                if (!best || cost < best.cost) best = { cost, b, ux, uy, t };
                break;
              }
            });
            if (best) { lines = v; break; }
          }
          if (!best) return;
          placed.push(best.b);
          out.push({ c: { code: e.code, o, ux: best.ux, uy: best.uy, lines, epic: true, along: best.t }, anchor: 'middle', first: -(lines.length - 1) * lineH / 2 + 4.2 });
        });
      }

      // A name sits just beyond its dot, away from its parent. The chosen item and what it is linked to may also
      // sit a little to either side, or further out, if that is where there is room.
      const settle = (c, turns, gaps, clear) => {
        const w = textW(c.lines);
        const hh = c.lines.length * lineH;
        const rpx = c.o.r * s * k;
        const base = Math.atan2(c.uy, c.ux);
        let best = null;
        turns.forEach((turn) => {
          const ux = Math.cos(base + turn);
          const uy = Math.sin(base + turn);
          const reach = (Math.abs(ux) * w) / 2 + (Math.abs(uy) * hh) / 2;
          for (const gp of gaps) {
            const dist = rpx + gp + reach;
            const cx = px(c.o.x) + ux * dist;
            const cy = py(c.o.y) + uy * dist;
            const b = [cx - w / 2, cy - hh / 2, cx + w / 2, cy + hh / 2];
            if (b[0] < 2 || b[2] > box.width - 2 || b[1] < 2 || b[3] > box.height - 2) continue;
            if (overlaps(b) || (clear && hitsCircle(b))) continue;
            const cost = gp + Math.abs(turn) * 10;
            if (!best || cost < best.cost) best = { cost, b, ux, uy, dist };
            break;
          }
        });
        return best;
      };
      cands.forEach((c) => {
        const ax = px(c.o.x);
        const ay = py(c.o.y);
        if (ax < -40 || ax > box.width + 40 || ay < -40 || ay > box.height + 40) return;
        const named1 = c.prio === 1;
        let best = named1
          ? settle(c, [0, 0.6, -0.6, 1.2, -1.2, 1.8, -1.8, Math.PI], [4, 12, 24, 40], true) || settle(c, [0, 0.6, -0.6, 1.2, -1.2, Math.PI], [4, 16, 32], false)
          : settle(c, c.o.kind === 'story' ? [0, 0.5, -0.5, 1, -1] : [0], [4], true);
        if (!best && !named1 && c.lines[0].length > 5) {
          // no clear room for the whole name: the code alone, which is short enough to fit
          c = Object.assign({}, c, { lines: [c.code] });
          best = settle(c, c.o.kind === 'story' ? [0, 0.5, -0.5, 1, -1] : [0], [4], true);
        }
        if (!best) return;
        placed.push(best.b);
        out.push({ c: Object.assign({}, c, { ux: best.ux, uy: best.uy, dist: best.dist }), anchor: 'middle', first: -(c.lines.length - 1) * lineH / 2 + 4.2 });
      });
      put(labelG);
      out.forEach(({ c, anchor, first }) => {
        const wx = c.epic ? c.o.x + c.ux * c.along : c.o.x + c.ux * c.dist * inv;
        const wy = c.epic ? c.o.y + c.uy * c.along : c.o.y + c.uy * c.dist * inv;
        const g = sv('g', { class: 'wl' + (c.epic ? ' wl-epic' : '') + (named.has(c.code) ? ' wl-lit' : ''), transform: `translate(${r1(wx)} ${r1(wy)}) scale(${inv})` });
        c.lines.forEach((line, i) => {
          const t = sv('text', { x: 0, y: r1(first + i * lineH), 'text-anchor': anchor });
          if (i === 0 && /^[EST]-\d+ /.test(line)) { const sp = line.indexOf(' '); t.append(sv('tspan', { class: 'wl-code' }, line.slice(0, sp)), line.slice(sp)); }
          else if (i === 0 && /^[EST]-\d+$/.test(line)) t.append(sv('tspan', { class: 'wl-code' }, line));
          else t.append(line);
          g.append(t);
        });
        labelG.append(g);
      });
      // epics keep their code inside the hub, whatever the labels are set to, and the project its name
      epics.forEach((e) => {
        const o = L.pos.get(e.code);
        const fs = Math.max(7, Math.min(12, (o.r * s * k * 2) / 3.8));
        const g = sv('g', { class: 'wl wl-hub', transform: `translate(${r1(o.x)} ${r1(o.y)}) scale(${inv})` });
        const hubText = sv('text', { x: 0, y: r1(fs * 0.36), 'text-anchor': 'middle' }, e.code);
        hubText.style.fontSize = `${r1(fs)}px`;
        g.append(hubText);
        labelG.append(g);
      });
      const pf = Math.max(8, Math.min(12, (L.rProj * s * k * 2) / 8.4));
      const pg = sv('g', { class: 'wl wl-project', transform: `scale(${inv})` });
      [['Riverside', -pf * 0.1], ['Bike Share', pf * 1.1]].forEach(([word, y]) => {
        const t = sv('text', { x: 0, y: r1(y), 'text-anchor': 'middle' }, word);
        t.style.fontSize = `${r1(pf)}px`;
        pg.append(t);
      });
      labelG.append(pg);
    };

    const applyView = () => {
      vp.setAttribute('transform', `translate(${r1(view.x)} ${r1(view.y)}) scale(${r1(view.k * 1000) / 1000})`);
      drawLabels();
    };

    /* -- the picture's parts around it: tool bar, zoom, tip, legend -- */
    const seg = (label, aria, options, current, pick, fk) => h('div', { class: 'wp-ctl' },
      h('span', { class: 'wp-label' }, label),
      h('div', { class: 'segmented', role: 'group', 'aria-label': aria },
        options.map(([v, text, title]) => h('button', { type: 'button', title, 'aria-pressed': current === v ? 'true' : 'false', 'data-fk': fk + v, onclick: () => pick(v) }, text))));
    const toolbar = h('div', { class: 'wp-toolbar' },
      seg('Show', 'Which levels to draw', LEVELS, W.level, (v) => { W.level = v; W.k = 1; W.x = 0; W.y = 0; renderAll('lv:' + v); }, 'lv:'),
      seg('Colour by', 'What the colour means', [['status', 'Status', 'Colour by where the work stands'], ['epic', 'Epic', 'Colour by the epic it belongs to']], W.color, (v) => { W.color = v; renderAll('col:' + v); }, 'col:'),
      seg('Labels', 'When to show names', LABEL_MODES, W.labels, (v) => { W.labels = v; renderAll('lab:' + v); }, 'lab:'),
      h('button', { class: 'small toggle', type: 'button', 'aria-pressed': W.links ? 'true' : 'false', 'data-fk': 'xl-toggle', title: 'Needs first, duplicates, related and references, as soft curves', onclick: () => { W.links = !W.links; renderAll('xl-toggle'); } }, icon('link', 14), 'Show links between items'));

    const tip = h('div', { class: 'tip', hidden: true, role: 'presentation' });
    const canvasInner = h('div', { class: 'wp-inner' }, svgEl, tip);
    const zoomAround = (f, cxv, cyv) => {
      const k2 = Math.min(8, Math.max(0.6, view.k * f));
      view.x = cxv - (cxv - view.x) * (k2 / view.k);
      view.y = cyv - (cyv - view.y) * (k2 / view.k);
      view.k = k2;
      applyView();
    };
    const centre = { x: vb.x + vb.w / 2, y: vb.y + vb.h / 2 };
    const centreOn = (code, k) => {
      const o = L.pos.get(code);
      if (!o) return;
      view.k = k;
      view.x = centre.x - o.x * k;
      view.y = centre.y - o.y * k;
      applyView();
    };
    const zoomBtn = (aria, fn, text) => h('button', { class: 'small icon-only', type: 'button', 'aria-label': aria, title: aria, onclick: fn }, text);
    const controls = h('div', { class: 'canvas-zoom', role: 'group', 'aria-label': 'Zoom and move' },
      sel ? h('button', { class: 'small', type: 'button', 'data-fk': 'zoom-chosen', onclick: () => centreOn(sel, Math.max(view.k, RANK[byCode.get(sel).kind] === 2 ? 4.2 : 2.4)) }, icon('search', 14), 'Zoom to chosen') : null,
      zoomBtn('Zoom out', () => zoomAround(1 / 1.4, centre.x, centre.y), '\u2212'),
      h('button', { class: 'small', type: 'button', 'aria-label': 'Fit the whole project in view', onclick: () => { view.k = 1; view.x = 0; view.y = 0; applyView(); } }, 'Fit'),
      zoomBtn('Zoom in', () => zoomAround(1.4, centre.x, centre.y), '+'));
    const hint = h('p', { class: 'canvas-hint quiet' }, icon('info', 14), 'Scroll to zoom, drag to move. Click a dot to choose it. Double-click, or press Enter on, an epic to focus on it.');
    const canvas = h('div', { class: 'canvas wp-canvas' }, canvasInner, controls);

    const statusLegend = ['new', 'ready', 'in_progress', 'blocked', 'done', 'dropped'].map((st) => h('li', {}, icon(st, 14, 'ks-' + st), STATUS_LABEL[st]));
    const epicLegend = epics.map((e) => h('li', {}, h('button', { class: 'legend-btn', type: 'button', title: `${e.code} ${e.title}`, 'data-fk': 'lg:' + e.code, onclick: () => chooseNode(e.code, 'lg:' + e.code) }, h('span', { class: 'swatch ep-' + epicIndex.get(e.code) }), h('span', { class: 'code mono' }, e.code), h('span', {}, trunc(e.title, 18)))));
    const linkLegend = [['needs', 'Needs first (arrow shows the order)'], ['relates', 'Related'], ['duplicates', 'Duplicate'], ['supersedes', 'Supersedes'], ['mentions', 'Named in text']].map(([t, label]) => h('li', {}, legendLine(t), label));
    const legend = h('div', { class: 'wp-legends' },
      h('ul', { class: 'legend plain', 'aria-label': W.color === 'epic' ? 'Colour by epic' : 'Colour by status' }, W.color === 'epic' ? epicLegend : statusLegend,
        h('li', {}, h('span', { class: 'dot-key' }), 'Bigger dot, higher priority'),
        h('li', {}, h('span', { class: 'late-dot' }), 'Overdue')),
      (W.links || sel) ? h('ul', { class: 'legend plain', 'aria-label': 'How to read the lines' }, linkLegend) : null);

    const levelWords = { epics: 'the 12 epics', stories: 'the epics and their 53 stories', tasks: 'all 245 items: 12 epics, 53 stories and 180 tasks' }[W.level];
    const main = h('section', { class: 'panel struct-main', 'aria-labelledby': 'struct-h' },
      h('div', { class: 'panel-head' }, h('div', {}, h('h2', { id: 'struct-h' }, 'Structure of the whole project'),
        h('p', { class: 'quiet' }, `${cap(levelWords)} at once. The hierarchy is the skeleton: each epic is a hub, each story a smaller hub, each task a dot around its story. Links between items are hidden until you ask for them or choose an item.`))),
      toolbar, canvas, hint, legend);
    root.append(h('div', { class: 'structure whole' }, main, wpPanel(sel, partners)));

    /* -- wiring -- */
    let lastClick = state.lastClick || { code: null, t: 0 };
    const showTip = (el) => {
      const code = el.getAttribute('data-code');
      const r = el.querySelector('.wn-body').getBoundingClientRect();
      const c = canvasInner.getBoundingClientRect();
      put(tip, wpTip(code));
      tip.hidden = false;
      const left = Math.min(Math.max(r.left - c.left + r.width / 2, 130), c.width - 130);
      tip.style.left = `${Math.round(left)}px`;
      const above = r.top - c.top > 120;
      tip.classList.toggle('below', !above);
      tip.style.top = `${Math.round(above ? r.top - c.top - 10 : r.bottom - c.top + 10)}px`;
    };
    const setHover = (code, el) => {
      const was = hover;
      hover = code;
      if (code) showTip(el); else tip.hidden = true;
      const wasEpic = was ? epicOf(byCode.get(was)).code : null;
      const nowEpic = code ? epicOf(byCode.get(code)).code : null;
      if (wasEpic !== nowEpic && W.labels === 'zoom') drawLabels();
    };
    $$('.wn', svgEl).forEach((el) => {
      const code = el.getAttribute('data-code');
      const kind = byCode.get(code).kind;
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const now = Date.now();
        if (kind === 'epic' && lastClick.code === code && now - lastClick.t < 450) { state.lastClick = null; focusEpic(code, 'wn:' + code); return; }
        lastClick = { code, t: now };
        state.lastClick = lastClick;
        chooseNode(code, 'wn:' + code);
      });
      el.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); if (kind === 'epic') focusEpic(code, 'wn:' + code); else chooseNode(code, 'wn:' + code); }
        else if (e.key === ' ') { e.preventDefault(); chooseNode(code, 'wn:' + code); }
        else if (e.key === 'Escape' && state.sel) { e.preventDefault(); state.sel = null; renderAll('wn:' + code); }
        else if (['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp'].includes(e.key)) {
          const n = byCode.get(code);
          let target = null;
          if (e.key === 'ArrowDown') target = (visKids(code, W.level)[0] || {}).code;
          else if (e.key === 'ArrowUp') target = n.parent;
          else {
            const sibs = n.parent ? visKids(n.parent, W.level) : epics;
            const i = sibs.findIndex((x) => x.code === code);
            target = (sibs[(i + (e.key === 'ArrowRight' ? 1 : -1) + sibs.length) % sibs.length] || {}).code;
          }
          const el2 = target && svgEl.querySelector(`.wn[data-code="${target}"]`);
          if (el2) { e.preventDefault(); el2.focus(); }
        }
      });
      el.addEventListener('pointerenter', () => setHover(code, el));
      el.addEventListener('pointerleave', () => setHover(null, el));
      el.addEventListener('focus', () => setHover(code, el));
      el.addEventListener('blur', () => setHover(null, el));
    });
    let drag = null;
    svgEl.addEventListener('pointerdown', (e) => {
      if (e.target.closest('.wn') || e.button !== 0) return;
      drag = { sx: e.clientX, sy: e.clientY, x: view.x, y: view.y, moved: false };
      svgEl.setPointerCapture(e.pointerId);
      svgEl.classList.add('grabbing');
    });
    svgEl.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const s = svgEl.getScreenCTM().a;
      if (Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) > 4) drag.moved = true;
      view.x = drag.x + (e.clientX - drag.sx) / s;
      view.y = drag.y + (e.clientY - drag.sy) / s;
      vp.setAttribute('transform', `translate(${r1(view.x)} ${r1(view.y)}) scale(${r1(view.k * 1000) / 1000})`);
    });
    svgEl.addEventListener('pointerup', () => {
      if (!drag) return;
      const moved = drag.moved;
      drag = null;
      svgEl.classList.remove('grabbing');
      if (moved) drawLabels();
      else if (state.sel) { state.sel = null; renderAll(null); }
    });
    svgEl.addEventListener('wheel', (e) => {
      e.preventDefault();
      const ctm = svgEl.getScreenCTM();
      const pt = svgEl.createSVGPoint();
      pt.x = e.clientX; pt.y = e.clientY;
      const u = pt.matrixTransform(ctm.inverse());
      zoomAround(Math.exp(-e.deltaY * 0.0016), u.x, u.y);
    }, { passive: false });

    if (view.k > 1 && view.x === 0 && view.y === 0 && (sel || W.at)) centreOn(sel || W.at, view.k);
    else applyView();
    watch(canvasInner, () => drawLabels());
  }

  // A short drawing of one kind of line, for the key.
  function legendLine(type) {
    const svg = sv('svg', { class: 'legend-sample', width: 40, height: 12, viewBox: '0 0 40 12', 'aria-hidden': 'true' });
    svg.append(sv('line', { class: `xl xl-${type} key`, x1: 2, y1: 6, x2: type === 'needs' || type === 'supersedes' ? 32 : 38, y2: 6 }));
    if (type === 'needs' || type === 'supersedes') svg.append(sv('path', { class: 'mk mk-arrow', d: 'M31 2.5 L39 6 L31 9.5 z' }));
    return svg;
  }

  function wpPanel(sel, partners) {
    const W = state.wp;
    const body = h('aside', { class: 'panel struct-panel', 'aria-label': 'Details of the chosen item' });
    const status = ['new', 'ready', 'in_progress', 'blocked', 'done', 'dropped'];
    const key = h('div', { class: 'sp-key' },
      h('h4', { class: 'sp-h' }, 'How to read the picture'),
      h('p', { class: 'quiet' }, 'Hubs are epics (large) and stories (small); the dots around a story are its tasks, a bigger dot meaning a higher priority. A ring around a hub shows how much of it is done. Names appear as you zoom in; hover anything to read it.'));
    if (!sel) {
      const tasks = D.nodes.filter((n) => n.kind === 'task');
      const tally = status.map((st) => [st, tasks.filter((t) => shown(t) === st).length]).filter(([, n]) => n);
      put(body,
        h('div', { class: 'sp-head' }, h('h3', {}, 'Whole project'), h('p', { class: 'quiet' }, `${plural(epics.length, 'epic')}, ${plural(D.nodes.filter((n) => n.kind === 'story').length, 'story', 'stories')} and ${plural(tasks.length, 'task')}.`),
          h('div', { class: 'chip-row' }, tally.map(([st, n]) => chip('status status-' + st, icon(st, 14), `${n} ${STATUS_LABEL[st].toLowerCase()}`)))),
        h('h4', { class: 'sp-h' }, 'Epics'),
        h('ul', { class: 'plain lrows' }, epics.map((e) => {
          const p = progress(e);
          return h('li', { class: 'lrow' },
            h('button', { class: 'lrow-main', type: 'button', 'data-fk': 'er:' + e.code, onclick: () => chooseNode(e.code, 'er:' + e.code) }, icon(shown(e), 16), codeText(e.code), h('span', { class: 'lrow-title' }, e.title)),
            h('span', { class: 'lrow-chips' },
              p.total ? [h('span', { class: 'meter small', role: 'img', 'aria-label': `${p.done} of ${p.total} tasks done` }, h('span', { style: `width:${Math.round((p.done / p.total) * 100)}%` })), h('span', { class: 'quiet' }, `${p.done}/${p.total}`)] : chip('', 'No stories yet'),
              h('button', { class: 'small ghost', type: 'button', 'data-fk': 'ef:' + e.code, onclick: () => focusEpic(e.code, 'ef:' + e.code) }, icon('structure', 14), 'Focus')));
        })),
        key);
      return body;
    }
    const n = byCode.get(sel);
    const st = shown(n);
    const late = lateDays(n);
    const kids = visKids(sel, W.level);
    const groups = new Map();
    partners.forEach((r) => {
      const title = LINK_TITLE[`${r.type}:${r.dir}`];
      if (!groups.has(title)) groups.set(title, []);
      groups.get(title).push(r);
    });
    const linkGroups = LINK_ORDER.filter((t) => groups.has(t)).map((t) => [t, groups.get(t)]);
    const row = (code, extra, why) => {
      const m = byCode.get(code);
      const ms = shown(m);
      return h('li', { class: 'lrow' + (ms === 'dropped' ? ' is-ended' : '') },
        h('button', { class: 'lrow-main', type: 'button', 'data-fk': 'sl:' + code, onclick: () => { state.sel = code; state.wp.at = code; renderAll('sl:' + code); } }, icon(ms, 16), codeText(code), h('span', { class: 'lrow-title' }, m.title)),
        h('span', { class: 'lrow-chips' }, statusChip(ms), dueChip(m), extra),
        why ? h('p', { class: 'lrow-why' }, why) : null);
    };
    const p = progress(n);
    put(body,
      h('div', { class: 'sel-card' },
        h('div', { class: 'peek-head' }, h('span', { class: 'kind-label' }, icon(KIND_ICON[n.kind], 16), KIND_LABEL[n.kind]), codeText(n.code), statusChip(st),
          h('button', { class: 'ghost icon-only small', type: 'button', 'aria-label': 'Clear the choice', 'data-fk': 'sel-clear', onclick: () => { state.sel = null; renderAll('sel-clear'); } }, icon('close', 14))),
        h('h3', {}, n.title),
        h('div', { class: 'hub-meta' }, priorityChip(n.priority), dueChip(n), n.held ? chip('held', icon('user', 14), 'Held by ' + n.held) : null),
        p.total && n.kind !== 'task' ? h('div', { class: 'hub-progress' }, h('span', { class: 'meter', role: 'img', 'aria-label': `${p.done} of ${p.total} tasks done` }, h('span', { style: `width:${Math.round((p.done / p.total) * 100)}%` })), h('span', { class: 'quiet' }, `${p.done} of ${plural(p.total, 'task')} done`)) : null,
        h('div', { class: 'hub-actions' },
          n.kind === 'epic' ? h('button', { class: 'small primary', type: 'button', 'data-fk': 'focus-epic', onclick: () => focusEpic(n.code, 'focus-epic') }, icon('structure', 14), 'Focus on this epic') : null,
          h('button', { class: 'small', type: 'button', onclick: () => toast('Opens the item page in the real app') }, 'Open page', icon('arrow_right', 14)),
          h('button', { class: 'small', type: 'button', onclick: () => setFocus(n.code, { view: 'around' }) }, icon('around', 14), 'Show around'))),
      kids.length ? h('section', { class: 'lgroup' }, h('h4', { class: 'sp-h' }, n.kind === 'epic' ? `Stories in ${n.code}` : `Tasks in ${n.code}`, h('span', { class: 'gbox-count' }, kids.length)),
        h('ul', { class: 'plain lrows' }, kids.map((k) => row(k.code, k.kind === 'story' ? (progress(k).total ? chip('', `${progress(k).done}/${progress(k).total}`) : null) : null)))) : null,
      linkGroups.length
        ? linkGroups.map(([title, rows]) => h('section', { class: 'lgroup' }, h('h4', { class: 'sp-h' }, title, h('span', { class: 'gbox-count' }, rows.length)),
            h('ul', { class: 'plain lrows' }, rows.map((r) => row(r.other, r.score ? chip('info', 'alike ' + r.score.toFixed(2)) : null, null)))))
        : h('p', { class: 'quiet sp-hint' }, n.kind === 'task' ? `${n.code} is not linked to anything else, so nothing else is highlighted.` : 'Nothing outside it is linked to it. Its own stories, tasks and parents are lit on the picture.'),
      key);
    return body;
  }

  function viewStructure(root) {
    if (state.open && byCode.has(state.open)) {
      if (state.sel && state.sel !== state.open && !subtree(byCode.get(state.open)).has(state.sel)) state.sel = null;
      epicLevel(root, byCode.get(state.open));
    } else wholeProject(root);
  }

  /* ===== 13. Order: the chain of "must come first" ======================================================== */

  const needsEdges = D.links.filter((l) => l[0] === 'needs').map((l) => ({ dep: l[1], pre: l[2] }));
  const CHAINS = (() => {
    const parent = new Map();
    const find = (x) => { let y = x; while (parent.get(y) !== y) { parent.set(y, parent.get(parent.get(y))); y = parent.get(y); } return y; };
    needsEdges.forEach(({ dep, pre }) => {
      [dep, pre].forEach((x) => { if (!parent.has(x)) parent.set(x, x); });
      parent.set(find(dep), find(pre));
    });
    const comps = new Map();
    parent.forEach((_, x) => { const r = find(x); if (!comps.has(r)) comps.set(r, []); comps.get(r).push(x); });
    return Array.from(comps.values());
  })();
  const codeOrder = (a, b) => 'EST'.indexOf(a[0]) - 'EST'.indexOf(b[0]) || Number(a.slice(2)) - Number(b.slice(2));
  const subtree = (n) => { const set = new Set([n.code]); kidsOf(n.code).forEach((k) => subtree(k).forEach((c) => set.add(c))); return set; };

  function orderModel(focus, onlyCode) {
    const scope = subtree(focus);
    let comps = CHAINS.filter((c) => c.some((x) => scope.has(x)));
    if (onlyCode) comps = comps.filter((c) => c.includes(onlyCode));
    const out = comps.map((codes) => {
      const set = new Set(codes);
      const edges = needsEdges.filter((e) => set.has(e.dep));
      const layer = new Map();
      const lay = (c) => {
        if (layer.has(c)) return layer.get(c);
        const pres = edges.filter((e) => e.dep === c).map((e) => e.pre);
        const v = pres.length ? 1 + Math.max(...pres.map(lay)) : 0;
        layer.set(c, v);
        return v;
      };
      codes.forEach(lay);
      const maxL = Math.max(...layer.values());
      const cols = Array.from({ length: maxL + 1 }, () => []);
      codes.slice().sort(codeOrder).forEach((c) => cols[layer.get(c)].push(c));
      const idx = new Map();
      cols[0].forEach((c, i) => idx.set(c, i));
      for (let l = 1; l <= maxL; l += 1) {
        const bary = (c) => { const ps = edges.filter((e) => e.dep === c).map((e) => idx.get(e.pre)).filter((v) => v !== undefined); return ps.length ? sum(ps) / ps.length : 0; };
        cols[l].sort((a, b) => bary(a) - bary(b) || codeOrder(a, b));
        cols[l].forEach((c, i) => idx.set(c, i));
      }
      return { codes, edges, layer, cols, size: codes.length };
    });
    out.sort((a, b) => b.size - a.size);
    return { comps: out, scope };
  }
  const downstream = (code, edges, set) => {
    edges.filter((e) => e.pre === code).forEach((e) => { if (!set.has(e.dep)) { set.add(e.dep); downstream(e.dep, edges, set); } });
    return set;
  };
  const upstream = (code, edges, set) => {
    edges.filter((e) => e.dep === code).forEach((e) => { if (!set.has(e.pre)) { set.add(e.pre); upstream(e.pre, edges, set); } });
    return set;
  };

  function orderNode(code, M, hot, pathSet, x, y) {
    const n = byCode.get(code);
    const st = shown(n);
    const late = lateDays(n);
    const outside = !M.scope.has(code);
    const O = state.order;
    const dim = O.blocked && !hot.has(code);
    const cls = ['onode', 's-' + st, outside ? 'outside' : '', O.sel === code ? 'sel' : '', dim ? 'dim' : '', O.blocked && hot.has(code) ? 'hot' : '', pathSet && pathSet.has(code) ? 'on-path' : ''].filter(Boolean).join(' ');
    const waits = waitingOn(n);
    return h('button', {
      class: cls, type: 'button', 'data-fk': 'on:' + code, style: `left:${x}px;top:${y}px`, title: n.title,
      'aria-pressed': O.sel === code ? 'true' : 'false',
      'aria-label': `${n.code} ${n.title}. ${STATUS_LABEL[st]}${late > 0 ? `, overdue ${plural(late, 'day')}` : ''}${waits.length ? `, waiting for ${waits.join(', ')}` : ''}${outside ? ', outside the chosen scope' : ''}. Press Enter to see its chain.`,
      onclick: () => { state.order.sel = O.sel === code ? null : code; renderAll('on:' + code); },
    },
    h('span', { class: 'on-top' }, icon(st, 15), codeText(code),
      outside ? h('span', { class: 'on-epic' }, epicOf(n).code) : null,
      late > 0 ? h('span', { class: 'n-flag late' }, icon('clock', 12), `${late} d late`) : null),
    h('span', { class: 'on-title' }, n.title));
  }

  function chainRows(code, model) {
    const edges = model.comps.flatMap((c) => c.edges);
    const comp = model.comps.find((c) => c.codes.includes(code));
    if (!comp) return { pre: [], post: [] };
    const depth = (c, map, dir) => {
      // distance from the chosen item along the chain
      const dist = new Map([[c, 0]]);
      let frontier = [c];
      while (frontier.length) {
        const next = [];
        frontier.forEach((x) => edges.filter((e) => (dir === 'pre' ? e.dep === x : e.pre === x)).forEach((e) => {
          const y = dir === 'pre' ? e.pre : e.dep;
          if (!dist.has(y)) { dist.set(y, dist.get(x) + 1); next.push(y); }
        }));
        frontier = next;
      }
      dist.delete(c);
      return Array.from(dist.entries()).sort((a, b) => a[1] - b[1] || codeOrder(a[0], b[0]));
    };
    return { pre: depth(code, null, 'pre'), post: depth(code, null, 'post') };
  }

  function chainRow(code, dist, dir) {
    const n = byCode.get(code);
    const st = shown(n);
    return h('li', { class: 'lrow' },
      h('button', { class: 'lrow-main', type: 'button', 'data-fk': 'cr:' + code, onclick: () => { state.order.sel = code; renderAll('cr:' + code); } }, icon(st, 16), codeText(code), h('span', { class: 'lrow-title' }, n.title)),
      h('span', { class: 'lrow-chips' }, statusChip(st), dueChip(n)),
      h('p', { class: 'lrow-why' }, dist === 1 ? (dir === 'pre' ? 'Must be finished directly before it' : 'Waits directly on it') : (dir === 'pre' ? `Must be finished earlier in the chain, ${dist} steps back` : `Waits on it further along, ${dist} steps on`)));
  }

  function viewOrder(root) {
    if (!state.focus) { emptyState(root, 'order'); return; }
    const focus = byCode.get(state.focus);
    const O = state.order;
    const full = orderModel(focus, null);
    if (O.sel && !full.comps.some((c) => c.codes.includes(O.sel))) O.sel = null;
    if (!full.comps.length) {
      const others = CHAINS.slice().sort((a, b) => b.length - a.length).slice(0, 4).map((c) => c.slice().sort(codeOrder)[0]);
      root.append(h('section', { class: 'panel empty-focus' },
        h('div', { class: 'empty-art', 'aria-hidden': 'true' }, icon('order', 36)),
        h('h2', {}, `Nothing in ${focus.code} waits on anything`),
        h('p', { class: 'quiet empty-lead' }, `There are no "needs first" links on ${focus.code}${kidsOf(focus.code).length ? ' or anything inside it' : ''}, so there is no chain to draw. Chains exist where one piece of work cannot start before another is finished.`),
        h('h3', { class: 'attention-h' }, 'Chains elsewhere'),
        h('div', { class: 'chip-row center' }, others.map((c) => h('button', { class: 'chip chip-link', type: 'button', title: byCode.get(c).title, onclick: () => setFocus(epicOf(byCode.get(c)).code, { reset: true }) }, icon('order', 14), `${c} in ${epicOf(byCode.get(c)).code}`)))));
      return;
    }
    const M = O.only && O.sel ? orderModel(focus, O.sel) : full;
    const allEdges = M.comps.flatMap((c) => c.edges);
    const blockedNow = M.comps.flatMap((c) => c.codes).filter((c) => shown(byCode.get(c)) === 'blocked');
    const hot = new Set(blockedNow);
    blockedNow.forEach((c) => downstream(c, allEdges, hot));
    const pathSet = O.sel ? new Set([O.sel]) : null;
    if (O.sel) { upstream(O.sel, allEdges, pathSet); downstream(O.sel, allEdges, pathSet); }

    const NW = 180;
    const NH = 74;
    const XG = 50;
    const YG = 12;
    const LABEL = 26;
    const GAPC = 22;
    let y0 = 0;
    let maxW = 0;
    const pos = new Map();
    const labels = [];
    M.comps.forEach((c, i) => {
      const rows = Math.max(...c.cols.map((col) => col.length));
      c.cols.forEach((col, l) => col.forEach((code, r) => pos.set(code, { x: l * (NW + XG), y: y0 + LABEL + r * (NH + YG) })));
      labels.push({ i, y: y0, c });
      maxW = Math.max(maxW, c.cols.length * (NW + XG) - XG);
      y0 += LABEL + rows * (NH + YG) - YG + GAPC;
    });
    const totalH = Math.max(y0 - GAPC, 80);
    const svg = sv('svg', { class: 'order-lines', width: maxW, height: totalH, viewBox: `0 0 ${maxW} ${totalH}`, 'aria-hidden': 'true', focusable: 'false' },
      sv('defs', {}, sv('marker', { id: 'or-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 11, markerHeight: 11, orient: 'auto', markerUnits: 'userSpaceOnUse' }, sv('path', { class: 'mk mk-arrow', d: 'M0 1 L10 5 L0 9 z' }))));
    allEdges.forEach((e) => {
      const a = pos.get(e.pre); const b = pos.get(e.dep);
      const x1 = a.x + NW; const y1 = a.y + NH / 2; const x2 = b.x; const y2 = b.y + NH / 2;
      const d = Math.max(30, (x2 - x1) * 0.5);
      const pre = byCode.get(e.pre);
      const on = O.blocked ? hot.has(e.pre) && hot.has(e.dep) : false;
      svg.append(sv('path', {
        class: 'oe' + (isEnded(pre.status) ? ' ended' : '') + (shown(pre) === 'blocked' ? ' blocked' : '') + (O.blocked ? (on ? ' hot' : ' dim') : '') + (pathSet && pathSet.has(e.pre) && pathSet.has(e.dep) ? ' on-path' : ''),
        d: `M${x1} ${y1} C${x1 + d} ${y1} ${x2 - d} ${y2} ${x2 - 2} ${y2}`, 'marker-end': 'url(#or-arrow)',
      }));
    });
    const canvas = h('div', { class: 'order-canvas', style: `width:${maxW}px;height:${totalH}px` }, svg);
    labels.forEach(({ i, y, c }) => {
      const b = c.codes.filter((x) => shown(byCode.get(x)) === 'blocked').length;
      const late = c.codes.filter((x) => lateDays(byCode.get(x)) > 0).length;
      canvas.append(h('p', { class: 'chain-label', style: `top:${y}px` }, h('strong', {}, `Chain ${i + 1}`), ` \u00b7 ${plural(c.size, 'item')}`, b ? ` \u00b7 ${b} blocked` : '', late ? ` \u00b7 ${late} overdue` : ''));
    });
    M.comps.forEach((c) => c.codes.forEach((code) => { const p = pos.get(code); canvas.append(orderNode(code, M, hot, pathSet, p.x, p.y)); }));

    const items = full.comps.flatMap((c) => c.codes);
    const summary = h('div', { class: 'order-summary' },
      chip('', plural(full.comps.length, 'chain'), ` \u00b7 ${plural(items.length, 'item')}`),
      items.filter((c) => shown(byCode.get(c)) === 'blocked').length ? chip('blocked', icon('blocked', 14), `${items.filter((c) => shown(byCode.get(c)) === 'blocked').length} blocked`) : null,
      items.filter((c) => lateDays(byCode.get(c)) > 0).length ? chip('overdue', icon('clock', 14), `${items.filter((c) => lateDays(byCode.get(c)) > 0).length} overdue`) : null,
      chip('ok', icon('done', 14), `${items.filter((c) => byCode.get(c).status === 'done').length} done`));

    const toggles = h('div', { class: 'order-toggles' },
      h('button', {
        class: 'small toggle', type: 'button', 'aria-pressed': O.only ? 'true' : 'false', 'data-fk': 'or-only', disabled: !O.sel,
        title: O.sel ? '' : 'Choose an item in the chain first', onclick: () => { O.only = !O.only; renderAll('or-only'); },
      }, icon('filter', 14), 'Show only this chain'),
      h('button', { class: 'small toggle', type: 'button', 'aria-pressed': O.blocked ? 'true' : 'false', 'data-fk': 'or-blocked', onclick: () => { O.blocked = !O.blocked; renderAll('or-blocked'); } }, icon('blocked', 14), 'Highlight what is blocked'),
      !O.sel ? h('span', { class: 'quiet' }, 'Choose an item to follow its chain.') : null);

    // the list beside the chain
    let side;
    if (O.sel) {
      const sel = byCode.get(O.sel);
      const rows = chainRows(O.sel, full);
      const st = shown(sel);
      side = h('aside', { class: 'panel struct-panel', 'aria-label': 'The chain through the chosen item' },
        h('div', { class: 'sp-head' },
          h('div', { class: 'peek-head' }, h('span', { class: 'kind-label' }, icon(KIND_ICON[sel.kind], 16), KIND_LABEL[sel.kind]), codeText(sel.code), statusChip(st),
            h('button', { class: 'ghost icon-only small', type: 'button', 'aria-label': 'Clear the choice', 'data-fk': 'or-clear', onclick: () => { O.sel = null; O.only = false; renderAll('or-clear'); } }, icon('close', 14))),
          h('h3', {}, sel.title),
          h('div', { class: 'hub-meta' }, priorityChip(sel.priority), dueChip(sel), waitingOn(sel).length ? chip('waiting', icon('clock', 14), 'Waiting for ' + waitingOn(sel).join(', ')) : null),
          h('div', { class: 'hub-actions' }, h('button', { class: 'small', type: 'button', onclick: () => setFocus(sel.code, { view: 'around' }) }, icon('around', 14), 'Show around'))),
        h('section', { class: 'lgroup' }, h('h4', { class: 'sp-h' }, 'Needs first', h('span', { class: 'gbox-count' }, rows.pre.length)),
          rows.pre.length ? h('ul', { class: 'plain lrows' }, rows.pre.map(([c, d]) => chainRow(c, d, 'pre'))) : h('p', { class: 'quiet sp-hint' }, 'Nothing. It can start now.')),
        h('section', { class: 'lgroup' }, h('h4', { class: 'sp-h' }, 'Blocks', h('span', { class: 'gbox-count' }, rows.post.length)),
          rows.post.length ? h('ul', { class: 'plain lrows' }, rows.post.map(([c, d]) => chainRow(c, d, 'post'))) : h('p', { class: 'quiet sp-hint' }, 'Nothing waits on it.')));
    } else {
      const layers = [];
      full.comps.forEach((c) => c.cols.forEach((col, l) => { layers[l] = (layers[l] || []).concat(col); }));
      side = h('aside', { class: 'panel struct-panel', 'aria-label': 'The same chains as a list' },
        h('div', { class: 'sp-head' }, h('h3', {}, 'The same chains, as a list'), h('p', { class: 'quiet' }, 'Layer by layer: what can start now, then what has to wait for it.')),
        layers.map((codes, l) => h('section', { class: 'lgroup' },
          h('h4', { class: 'sp-h' }, l === 0 ? 'Start here: nothing comes before' : `Then, layer ${l + 1}`, h('span', { class: 'gbox-count' }, codes.length)),
          h('ul', { class: 'plain lrows' }, codes.slice().sort(codeOrder).map((code) => {
            const n = byCode.get(code);
            const st = shown(n);
            const waits = waitingOn(n);
            return h('li', { class: 'lrow' }, h('button', { class: 'lrow-main', type: 'button', 'data-fk': 'cr:' + code, onclick: () => { O.sel = code; renderAll('cr:' + code); } }, icon(st, 16), codeText(code), h('span', { class: 'lrow-title' }, n.title)),
              h('span', { class: 'lrow-chips' }, statusChip(st), dueChip(n)),
              h('p', { class: 'lrow-why' }, waits.length ? `Waiting for ${waits.join(', ')}` : l === 0 ? 'Nothing in the way' : 'Everything before it is finished'));
          })))));
    }

    const region = h('div', { class: 'order-scroll', tabindex: '0', role: 'region', 'aria-label': 'The order of work, left to right. It scrolls sideways when it is wide.' }, canvas);
    const hint = h('p', { class: 'scroll-hint quiet', hidden: true }, icon('arrow_right', 14), 'This chain is wider than the page: scroll sideways to see the rest.');
    requestAnimationFrame(() => { hint.hidden = !(region.scrollWidth > region.clientWidth + 4); });
    watch(region, () => { hint.hidden = !(region.scrollWidth > region.clientWidth + 4); });
    root.append(h('div', { class: 'structure order' },
      h('section', { class: 'panel struct-main', 'aria-labelledby': 'order-h' },
        h('div', { class: 'panel-head' },
          h('div', {}, h('h2', { id: 'order-h' }, `Order of work in ${focus.code}`),
            h('p', { class: 'quiet' }, `Left to right: what must come first, then what waits for it. ${O.only ? 'Only the chain through the chosen item is shown.' : 'Only "needs first" links are drawn here; the others are in Around.'}`))),
        summary, toggles, region, hint,
        h('ul', { class: 'legend plain', 'aria-label': 'How to read the chain' },
          h('li', {}, icon('blocked', 14, 'ks-blocked'), 'Blocked'), h('li', {}, icon('in_progress', 14, 'ks-in_progress'), 'In progress'), h('li', {}, icon('done', 14, 'ks-done'), 'Done'),
          h('li', {}, icon('clock', 14, 'ks-late'), 'Overdue'), h('li', {}, h('span', { class: 'dash-box' }), 'Outside the chosen scope'))),
      side));
  }

  /* ===== 14. Timeline: decisions and facts by date ========================================================= */

  function timelineEntries() {
    const out = [];
    D.decisions.forEach((d) => out.push({ key: 'decision:' + d.id, type: 'decision', id: d.id, date: d.decided, changed: d.changed, rec: d, title: d.title, on: d.on, wide: d.wide }));
    D.facts.forEach((f) => out.push({ key: 'fact:' + f.id, type: 'fact', id: f.id, date: f.recorded, changed: f.changed, rec: f, title: f.text, on: f.on, wide: false }));
    return out;
  }
  const monthName = (iso) => new Date(iso + 'T00:00:00Z').toLocaleString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' });
  const tlSelect = (key) => { state.time.sel = state.time.sel === key ? null : key; renderAll('tl:' + key); };

  function timelineAxis(entries) {
    const W = 920;
    const t0 = Date.parse('2026-07-24T00:00:00Z');
    const t1 = todayMs + 4 * DAY;
    const xOf = (iso) => 34 + ((Date.parse(iso + 'T00:00:00Z') - t0) / (t1 - t0)) * (W - 68);
    const place = (type) => {
      const list = entries.filter((e) => e.type === type).map((e) => ({ e, x: xOf(e.date) })).sort((a, b) => a.x - b.x);
      const last = [];
      list.forEach((it) => {
        let l = 0;
        while (last[l] !== undefined && it.x - last[l] < 28) l += 1;
        last[l] = it.x;
        it.lane = l;
      });
      return { list, lanes: last.length };
    };
    const D1 = place('decision');
    const F1 = place('fact');
    const axisY = 64 + Math.max(D1.lanes, 1) * 30;
    const Hh = axisY + 56 + Math.max(F1.lanes, 1) * 30;
    const decs = D1.list;
    const facts = F1.list;
    decs.forEach((it) => { it.y = axisY - 30 - it.lane * 30; });
    facts.forEach((it) => { it.y = axisY + 30 + it.lane * 30; });
    const svg = sv('svg', { class: 'time-axis', viewBox: `0 0 ${W} ${Hh}`, role: 'group', 'aria-label': 'Decisions above the line and facts below it, by date' });
    // since your last visit
    const xs = xOf(D.lastLooked);
    svg.append(sv('rect', { class: 'since-band', x: r1(xs), y: 12, width: r1(xOf(D.today) - xs), height: Hh - 24, rx: 6 }));
    svg.append(sv('text', { class: 'axis-note', x: W - 16, y: 30, 'text-anchor': 'end' }, `Since your last visit (${D.lastLooked}) \u2192`));
    // months
    for (let m = new Date('2026-08-01T00:00:00Z'); m.getTime() < t1; m.setUTCMonth(m.getUTCMonth() + 1)) {
      const iso = m.toISOString().slice(0, 10);
      const x = xOf(iso);
      svg.append(sv('line', { class: 'axis-tick', x1: r1(x), y1: axisY - 6, x2: r1(x), y2: axisY + 6 }));
      svg.append(sv('text', { class: 'axis-month', x: r1(x + 5), y: axisY + 4 }, m.toLocaleString('en-GB', { month: 'short', timeZone: 'UTC' })));
    }
    svg.append(sv('line', { class: 'axis-line', x1: 20, y1: axisY, x2: W - 20, y2: axisY }));
    const tx = xOf(D.today);
    svg.append(sv('line', { class: 'axis-today', x1: r1(tx), y1: 36, x2: r1(tx), y2: Hh - 14 }));
    svg.append(sv('text', { class: 'axis-today-label', x: r1(tx - 6), y: Hh - 18, 'text-anchor': 'end' }, 'Today'));
    svg.append(sv('text', { class: 'axis-side', x: 22, y: 44 }, 'Decisions'));
    svg.append(sv('text', { class: 'axis-side', x: 22, y: Hh - 18 }, 'Facts'));
    const at = new Map();
    decs.concat(facts).forEach((it) => at.set(it.e.key, it));

    // replaced-by arcs
    decs.concat(facts).forEach((it) => {
      const rb = it.e.rec.replacedBy;
      if (!rb) return;
      const to = at.get(it.e.type + ':' + rb);
      if (!to) return;
      const dir = it.e.type === 'decision' ? -1 : 1;
      const top = dir < 0 ? Math.min(it.y, to.y) : Math.max(it.y, to.y);
      const cy = top + dir * 34;
      svg.append(sv('path', { class: 'arc', d: `M${r1(it.x)} ${r1(it.y + dir * 12)} Q${r1((it.x + to.x) / 2)} ${r1(cy)} ${r1(to.x - 6)} ${r1(to.y + dir * 12)}`, 'marker-end': 'url(#tl-arrow)' }));
      svg.append(sv('text', { class: 'arc-label', x: r1((it.x + to.x) / 2), y: r1(cy + (dir < 0 ? -4 : 12)), 'text-anchor': 'middle' }, 'replaced'));
    });
    svg.append(sv('defs', {}, sv('marker', { id: 'tl-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 10, markerHeight: 10, orient: 'auto', markerUnits: 'userSpaceOnUse' }, sv('path', { class: 'mk mk-arrow', d: 'M0 1 L10 5 L0 9 z' }))));

    decs.concat(facts).forEach((it) => {
      const e = it.e;
      const rec = e.rec;
      const ended = rec.state !== 'in force';
      const un = !ended && !rec.confirmation;
      const since = e.changed >= D.lastLooked;
      const g = sv('g', {
        class: `tm tm-${e.type}` + (ended ? ' tm-ended' : '') + (un ? ' tm-unconf' : '') + (state.time.sel === e.key ? ' sel' : '') + (since ? ' tm-since' : ''),
        transform: `translate(${r1(it.x)} ${r1(it.y)})`, tabindex: '0', role: 'button', 'data-fk': 'tl:' + e.key, 'aria-pressed': state.time.sel === e.key ? 'true' : 'false',
        'aria-label': `${KIND_LABEL[e.type]} ${e.id}, ${e.date}: ${trunc(e.title, 60)}. ${ended ? cap(rec.state) : rec.confirmation ? 'Confirmed' : 'Unconfirmed'}.`,
      });
      g.append(sv('line', { class: 'stem', x1: 0, y1: it.y < axisY ? 12 : -12, x2: 0, y2: axisY - it.y }));
      g.append(sv('circle', { class: 'tm-hit', r: 16 }));
      g.append(sv('rect', { class: 'tm-box', x: -12, y: -12, width: 24, height: 24, rx: 7 }));
      const ic = icon(KIND_ICON[e.type], 14);
      ic.setAttribute('x', -7); ic.setAttribute('y', -7);
      g.append(ic);
      if (ended) g.append(sv('line', { class: 'tm-strike', x1: -10, y1: 10, x2: 10, y2: -10 }));
      g.append(sv('title', {}, `${KIND_LABEL[e.type]} #${e.id}: ${trunc(e.title, 70)}`));
      g.addEventListener('click', () => tlSelect(e.key));
      g.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); tlSelect(e.key); } });
      svg.append(g);
    });
    return svg;
  }

  function timelineRow(e, all) {
    const rec = e.rec;
    const ended = rec.state !== 'in force';
    const sel = state.time.sel === e.key;
    const replaced = rec.replacedBy ? all.get(e.type + ':' + rec.replacedBy) : null;
    const replaces = rec.replaces ? all.get(e.type + ':' + rec.replaces) : null;
    return h('li', { class: 'tl-item' + (ended ? ' is-ended' : '') + (sel ? ' is-sel' : ''), id: 'tl-' + e.type + '-' + e.id },
      h('div', { class: 'tl-date mono' }, e.date),
      h('div', { class: 'tl-body' },
        h('button', { class: 'tl-main', type: 'button', 'aria-pressed': sel ? 'true' : 'false', 'data-fk': 'tlr:' + e.key, onclick: () => tlSelect(e.key) },
          icon(KIND_ICON[e.type], 16), h('span', { class: 'kind-label' }, `${KIND_LABEL[e.type]} #${e.id}`), h('span', { class: 'tl-title' }, e.title)),
        h('div', { class: 'chip-row' }, stateChips(rec, e.type), e.wide ? chip('info', icon('graph', 14), 'Project-wide') : null,
          e.changed >= D.lastLooked ? chip('accent', 'Changed since your last visit') : null),
        replaced ? h('p', { class: 'tl-link' }, icon('arrow_right', 14), 'Replaced by ', h('button', { class: 'link', type: 'button', onclick: () => tlSelect(replaced.key) }, `${e.type} #${replaced.id}`), ` on ${rec.changed}`) : null,
        replaces ? h('p', { class: 'tl-link' }, icon('arrow_left', 14), 'Replaces ', h('button', { class: 'link', type: 'button', onclick: () => tlSelect(replaces.key) }, `${e.type} #${replaces.id}`)) : null,
        h('p', { class: 'tl-text' }, e.type === 'decision' ? rec.text : `Source: ${rec.source}`),
        h('div', { class: 'chip-row' }, h('span', { class: 'quiet' }, 'Applies to'), e.wide ? chip('info', 'every item') : null,
          !e.on.length && !e.wide ? h('span', { class: 'quiet' }, 'not linked to any item yet') : null,
          e.on.slice(0, 6).map((c) => h('button', { class: 'chip chip-link', type: 'button', title: byCode.get(c).title, onclick: () => setFocus(c, { reset: true, view: 'around' }) }, c)),
          e.on.length > 6 ? chip('', `+${e.on.length - 6}`) : null)));
  }

  function timelineSide(sel, entries) {
    if (!sel) {
      const decs = D.decisions;
      const facts = D.facts;
      const row = (label, list) => h('li', {}, h('span', {}, label), h('span', { class: 'mono' }, `${list.filter((r) => r.state === 'in force').length} in force \u00b7 ${list.filter((r) => r.state === 'in force' && !r.confirmation).length} unconfirmed \u00b7 ${list.filter((r) => r.state === 'replaced').length} replaced \u00b7 ${list.filter((r) => r.state === 'withdrawn').length} withdrawn`));
      return h('aside', { class: 'panel struct-panel', 'aria-label': 'What a chosen record concerns' },
        h('div', { class: 'sp-head' }, h('h3', {}, 'What each one concerns'), h('p', { class: 'quiet' }, 'Choose a decision or a fact on the line or in the list to see the work it applies to.')),
        h('ul', { class: 'plain tally' }, row('Decisions', decs), row('Facts', facts)),
        h('p', { class: 'quiet sp-hint' }, `${entries.length} shown. Dates of decisions are as recorded; the demo records every fact on one day, so the fact dates here are illustrative.`));
    }
    const e = sel;
    const rec = e.rec;
    const rows = e.on.map((c) => { const n = byCode.get(c); const st = shown(n); return h('li', { class: 'lrow' },
      h('button', { class: 'lrow-main', type: 'button', onclick: () => setFocus(c, { reset: true, view: 'around' }) }, icon(st, 16), codeText(c), h('span', { class: 'lrow-title' }, n.title)),
      h('span', { class: 'lrow-chips' }, statusChip(st), dueChip(n))); });
    return h('aside', { class: 'panel struct-panel', 'aria-label': 'What the chosen record concerns' },
      h('div', { class: 'sp-head' },
        h('div', { class: 'peek-head' }, h('span', { class: 'kind-label' }, icon(KIND_ICON[e.type], 16), `${KIND_LABEL[e.type]} #${e.id}`), h('span', { class: 'mono quiet' }, e.date),
          h('button', { class: 'ghost icon-only small', type: 'button', 'aria-label': 'Clear the choice', 'data-fk': 'tl-clear', onclick: () => { state.time.sel = null; renderAll('tl-clear'); } }, icon('close', 14))),
        h('h3', { class: rec.state !== 'in force' ? 'is-ended' : '' }, e.title),
        h('div', { class: 'chip-row' }, stateChips(rec, e.type), e.wide ? chip('info', icon('graph', 14), 'Project-wide') : null),
        e.type === 'decision' ? [h('p', {}, rec.text), h('p', { class: 'quiet' }, h('strong', {}, 'Reason. '), rec.reason), h('p', { class: 'quiet' }, `Decided by ${rec.by}.`, rec.confirmation ? ` Confirmed by ${rec.confirmation.by}: "${rec.confirmation.why}"` : '', rec.history ? ' ' + rec.history : '', rec.withdrawnWhy ? ' Withdrawn: ' + rec.withdrawnWhy : '')]
          : [h('p', { class: 'quiet' }, h('strong', {}, 'Source. '), rec.source), h('p', { class: 'quiet' }, rec.confirmation ? `Confirmed by ${rec.confirmation.by}: "${rec.confirmation.why}"` : '', rec.history ? ' ' + rec.history : '', rec.withdrawnWhy ? ' Withdrawn: ' + rec.withdrawnWhy : '', rec.why ? ' ' + rec.why : '')]),
      h('section', { class: 'lgroup' }, h('h4', { class: 'sp-h' }, 'Applies to', h('span', { class: 'gbox-count' }, e.wide ? `every item + ${e.on.length}` : e.on.length)),
        e.wide ? h('p', { class: 'quiet sp-hint' }, 'It applies to the whole project. These are the items it is also linked to directly:') : null,
        h('ul', { class: 'plain lrows' }, rows)));
  }

  function viewTimeline(root) {
    const T = state.time;
    const focus = state.focus ? byCode.get(state.focus) : null;
    const everything = timelineEntries();
    const all = new Map(everything.map((e) => [e.key, e]));
    let entries = everything.slice();
    if (T.kind !== 'all') entries = entries.filter((e) => e.type === T.kind);
    if (T.since) entries = entries.filter((e) => e.changed >= D.lastLooked);
    if (T.about && focus) {
      const scope = subtree(focus);
      let up = focus;
      while (up.parent) { up = byCode.get(up.parent); scope.add(up.code); }
      entries = entries.filter((e) => e.wide || e.on.some((c) => scope.has(c)));
    }
    entries.sort((a, b) => b.date.localeCompare(a.date) || b.id - a.id);
    const sinceCount = everything.filter((e) => e.changed >= D.lastLooked).length;
    const sel = T.sel ? all.get(T.sel) : null;

    const toolbar = h('div', { class: 'time-toolbar' },
      h('div', { class: 'segmented', role: 'group', 'aria-label': 'What to show' },
        [['all', 'Both'], ['decision', 'Decisions'], ['fact', 'Facts']].map(([v, l]) => h('button', { type: 'button', 'aria-pressed': T.kind === v ? 'true' : 'false', 'data-fk': 'tk:' + v, onclick: () => { T.kind = v; renderAll('tk:' + v); } }, l))),
      h('button', { class: 'small toggle', type: 'button', 'aria-pressed': T.since ? 'true' : 'false', 'data-fk': 'tl-since', onclick: () => { T.since = !T.since; renderAll('tl-since'); } }, icon('clock', 14), `Since I last looked (${sinceCount})`),
      h('button', { class: 'small toggle', type: 'button', 'aria-pressed': T.about && focus ? 'true' : 'false', 'data-fk': 'tl-about', disabled: !focus, title: focus ? '' : 'Choose an item above first', onclick: () => { T.about = !T.about; renderAll('tl-about'); } }, icon('around', 14), focus ? `Only about ${focus.code}` : 'Only about the chosen item'),
      h('span', { class: 'quiet time-count' }, `${entries.length} of ${everything.length} shown`));

    const shownRows = T.all ? entries : entries.slice(0, 10);
    const grouped = [];
    shownRows.forEach((e) => { const m = monthName(e.date); const last = grouped[grouped.length - 1]; if (last && last[0] === m) last[1].push(e); else grouped.push([m, [e]]); });
    const list = entries.length
      ? h('div', { class: 'tl-list' },
          grouped.map(([m, rows]) => h('section', {}, h('h3', { class: 'tl-month' }, m), h('ol', { class: 'plain tl-rows' }, rows.map((e) => timelineRow(e, all))))),
          entries.length > 10 ? h('button', { class: 'small', type: 'button', 'data-fk': 'tl-all', onclick: () => { T.all = !T.all; renderAll('tl-all'); } }, T.all ? 'Show only the newest 10' : `Show ${entries.length - 10} older`) : null)
      : h('p', { class: 'empty' }, 'Nothing matches. Try another filter.');

    const axisHolder = h('div', { class: 'time-scroll', tabindex: '0', role: 'region', 'aria-label': 'The timeline. It scrolls sideways on a small screen.' }, h('div', { class: 'time-inner' }, timelineAxis(entries)));
    root.append(h('div', { class: 'timeline-wrap' },
      h('section', { class: 'panel', 'aria-labelledby': 'time-h' },
        h('div', { class: 'panel-head' }, h('div', {}, h('h2', { id: 'time-h' }, 'Decisions and facts over time'), h('p', { class: 'quiet' }, 'What was decided or learned, when, and what replaced what. Struck through and faded means it no longer holds.'))),
        toolbar, axisHolder,
        h('ul', { class: 'legend plain', 'aria-label': 'How to read the timeline' },
          h('li', {}, h('span', { class: 'tm-key tm-conf' }), 'In force, confirmed'), h('li', {}, h('span', { class: 'tm-key tm-unconf' }), 'In force, unconfirmed'), h('li', {}, h('span', { class: 'tm-key tm-ended' }), 'Replaced or withdrawn'), h('li', {}, h('span', { class: 'tm-key tm-since' }), 'Changed since your last visit'))),
      h('div', { class: 'structure timeline' },
        h('section', { class: 'panel struct-main', 'aria-label': 'The records, newest first' }, list),
        timelineSide(sel, entries))));
  }

  /* ===== 15. Check-up: counts that open lists, and the whole project as one bubble per epic ================== */

  function checkups() {
    const reqNo = D.rules.filter((r) => !r.story);
    const withReq = new Set(D.rules.filter((r) => r.story).map((r) => r.story));
    const storiesNo = D.nodes.filter((n) => n.kind === 'story' && !withReq.has(n.code));
    const unconf = D.decisions.filter((d) => d.state === 'in force' && !d.confirmation && d.on.some((c) => byCode.get(c).status === 'in_progress'));
    const dups = D.similar.filter((s) => s.score >= 0.6 && !hasLink(s.a, s.b));
    const replacedLinked = D.decisions.filter((d) => d.state !== 'in force' && d.on.some((c) => !isEnded(byCode.get(c).status)));
    return {
      reqs: { items: reqNo, label: 'requirements in no story', icon: 'rule', lead: 'Requirements that are written down but not placed in any story, so nobody is asked to build them.' },
      stories: { items: storiesNo, label: 'stories with no requirement', icon: 'story', lead: 'Stories that say nothing about what done means. Some are fine; this is the list to look through.' },
      unconf: { items: unconf, label: 'unconfirmed decisions on work in progress', icon: 'unconfirmed', lead: 'Decisions that apply to work someone is doing now, but that no person has confirmed.' },
      dups: { items: dups, label: 'possible duplicates', icon: 'link', lead: 'Pairs the index finds alike and nobody has linked. Marking one as a duplicate is one click in the real app.' },
      replaced: { items: replacedLinked, label: 'replaced decisions on open work', icon: 'decision', lead: 'Decisions that were replaced or withdrawn but are still linked to work that is not finished, so the work may be following an old rule.' },
    };
  }

  function renderCheckup() {
    const C = checkups();
    const strip = $('#checkup');
    put(strip,
      h('h2', { class: 'attention-title', id: 'checkup-h' }, 'Check-up'),
      h('ul', { class: 'attention-list', 'aria-labelledby': 'checkup-h' },
        Object.entries(C).map(([id, c]) => h('li', {}, h('button', {
          class: 'attention-item' + (state.checkup === id ? ' open' : ''), type: 'button', 'aria-expanded': state.checkup === id ? 'true' : 'false', 'aria-controls': 'checkup-panel', 'data-fk': 'cu:' + id,
          onclick: () => { state.checkup = state.checkup === id ? null : id; renderAll('cu:' + id); },
        }, icon(c.icon, 14), h('span', { class: 'number' }, c.items.length), c.label))),
        h('li', {}, h('button', {
          class: 'attention-item map' + (state.checkup === 'map' ? ' open' : ''), type: 'button', 'aria-expanded': state.checkup === 'map' ? 'true' : 'false', 'aria-controls': 'checkup-panel', 'data-fk': 'cu:map',
          onclick: () => { state.checkup = state.checkup === 'map' ? null : 'map'; renderAll('cu:map'); },
        }, icon('graph', 14), 'Whole project (for checking)'))));
    const panel = $('#checkup-panel');
    panel.hidden = !state.checkup;
    panel.replaceChildren();
    if (!state.checkup) return;
    const close = h('button', { class: 'small ghost', type: 'button', 'data-fk': 'cu-close', onclick: () => { const id = state.checkup; state.checkup = null; renderAll('cu:' + id); } }, icon('close', 14), 'Close');
    if (state.checkup === 'map') {
      panel.append(h('div', { class: 'panel-head' }, h('div', {}, h('h2', {}, 'Whole project, for checking'), h('p', { class: 'quiet' }, 'One bubble for each epic, joined by how many links run between them. It is a map to check from, not to work from.')), close), epicMap());
      return;
    }
    const c = C[state.checkup];
    panel.append(h('div', { class: 'panel-head' }, h('div', {}, h('h2', {}, `${c.items.length} ${c.label}`), h('p', { class: 'quiet' }, c.lead)), close), checkList(state.checkup, c.items));
  }

  function checkList(id, items) {
    const asRow = (node, extra) => {
      const st = shown(node);
      return h('li', { class: 'lrow' },
        h('button', { class: 'lrow-main', type: 'button', onclick: () => setFocus(node.code, { reset: true, view: 'around', closeCheckup: true, scroll: true }) }, icon(st, 16), codeText(node.code), h('span', { class: 'lrow-title' }, node.title)),
        h('span', { class: 'lrow-chips' }, statusChip(st), chip('outside', icon('epic', 14), epicOf(node).code), extra));
    };
    if (id === 'reqs') {
      return h('ul', { class: 'plain lrows check-rows' }, items.map((r) => h('li', { class: 'lrow' },
        h('span', { class: 'lrow-main static' }, icon('rule', 16), codeText('#' + r.id), h('span', { class: 'lrow-title' }, r.title)),
        h('span', { class: 'lrow-chips' }, chip('info', cap(r.status)), h('button', { class: 'small', type: 'button', onclick: () => toast('Placing a requirement is done on the Requirements page of the real app') }, 'Place in a story...')))));
    }
    if (id === 'stories') {
      const open = !!state.expanded.storiesAll;
      const rows = open ? items : items.slice(0, 8);
      return h('div', {},
        h('ul', { class: 'plain lrows check-rows' }, rows.map((n) => asRow(n))),
        items.length > 8 ? h('button', { class: 'small', type: 'button', 'data-fk': 'cu-more', onclick: () => { state.expanded.storiesAll = !open; renderAll('cu-more'); } }, open ? 'Show fewer' : `Show all ${items.length}`) : null);
    }
    if (id === 'unconf' || id === 'replaced') {
      return h('ul', { class: 'plain lrows check-rows' }, items.map((d) => h('li', { class: 'lrow' + (d.state !== 'in force' ? ' is-ended' : '') },
        h('button', { class: 'lrow-main', type: 'button', onclick: () => { state.view = 'timeline'; state.time.sel = 'decision:' + d.id; state.checkup = null; renderAll('tl:decision:' + d.id); } }, icon('decision', 16), codeText('#' + d.id), h('span', { class: 'lrow-title' }, d.title)),
        h('span', { class: 'lrow-chips' }, stateChips(d, 'decision'), d.wide ? chip('info', 'Project-wide') : null),
        h('p', { class: 'lrow-why' }, id === 'unconf'
          ? `Touches work in progress: ${d.on.filter((c) => byCode.get(c).status === 'in_progress').slice(0, 4).join(', ')}. Nobody has confirmed it.`
          : `${d.state === 'replaced' ? `Replaced by decision #${d.replacedBy}` : 'Withdrawn'}, but still linked to open work: ${d.on.filter((c) => !isEnded(byCode.get(c).status)).join(', ')}.`))));
    }
    return h('ul', { class: 'plain pairs' }, items.map((s) => {
      const a = byCode.get(s.a); const b = byCode.get(s.b);
      const side = (n) => h('div', { class: 'pair-side' },
        h('button', { class: 'lrow-main', type: 'button', onclick: () => setFocus(n.code, { reset: true, view: 'around', closeCheckup: true, scroll: true }) }, icon(shown(n), 16), codeText(n.code), h('span', { class: 'lrow-title' }, n.title)),
        h('div', { class: 'chip-row' }, statusChip(shown(n)), chip('outside', icon('epic', 14), epicOf(n).code)));
      return h('li', { class: 'pair' }, side(a), h('div', { class: 'pair-mid' }, h('span', { class: 'pair-score' }, s.score.toFixed(2)), h('span', { class: 'quiet' }, 'alike'),
        h('button', { class: 'small', type: 'button', onclick: () => toast('Marking as a duplicate adds one link in the real app') }, 'Mark as duplicate')), side(b));
    }));
  }

  function epicEdges(mode) {
    const m = new Map();
    const bump = (a, b) => { if (a === b) return; const k = [a, b].sort(codeOrder).join('|'); m.set(k, (m.get(k) || 0) + 1); };
    if (mode === 'work') D.links.forEach(([, a, b]) => bump(epicOf(byCode.get(a)).code, epicOf(byCode.get(b)).code));
    else D.decisions.concat(D.facts).forEach((rec) => {
      const es = uniq(rec.on.map((c) => epicOf(byCode.get(c)).code));
      for (let i = 0; i < es.length; i += 1) for (let j = i + 1; j < es.length; j += 1) bump(es[i], es[j]);
    });
    return m;
  }

  function epicMap() {
    const mode = state.mapMode;
    const edges = epicEdges(mode);
    const W = 760;
    const Hh = 470;
    const cx = W / 2;
    const cy = Hh / 2;
    const R = 160;
    const pos = new Map();
    epics.forEach((e, i) => {
      const t = (i / epics.length) * TWO_PI;
      pos.set(e.code, { x: cx + R * Math.sin(t), y: cy - R * Math.cos(t), t, r: 13 + 2.6 * Math.sqrt(tasksUnder(e).length) });
    });
    const selCode = state.mapSel;
    const svg = sv('svg', { class: 'epic-map', viewBox: `0 0 ${W} ${Hh}`, role: 'group', 'aria-label': 'Each epic as one bubble, joined by the number of links between them' });
    const lines = sv('g', {});
    edges.forEach((n, k) => {
      const [a, b] = k.split('|');
      const p = pos.get(a); const q = pos.get(b);
      const on = !selCode || a === selCode || b === selCode;
      lines.append(sv('line', { class: 'me' + (on ? '' : ' dim') + (selCode && on ? ' hot' : ''), x1: r1(p.x), y1: r1(p.y), x2: r1(q.x), y2: r1(q.y), 'stroke-width': r1(1.4 + n * 1.5) }));
    });
    const badges = sv('g', {});
    edges.forEach((n, k) => {
      const [a, b] = k.split('|');
      if (n < 2) return;
      const p = pos.get(a); const q = pos.get(b);
      const on = !selCode || a === selCode || b === selCode;
      badges.append(sv('g', { class: 'me-badge' + (on ? '' : ' dim'), transform: `translate(${r1((p.x + q.x) / 2)} ${r1((p.y + q.y) / 2)})` }, sv('circle', { r: 9 }), sv('text', { 'text-anchor': 'middle', dy: '0.34em' }, String(n))));
    });
    const bubbles = sv('g', {});
    epics.forEach((e) => {
      const p = pos.get(e.code);
      const linked = [...edges.keys()].some((k) => k.split('|').includes(e.code));
      const right = Math.sin(p.t) >= 0;
      const g = sv('g', {
        class: 'bubble' + (selCode === e.code ? ' sel' : '') + (linked ? '' : ' alone'), transform: `translate(${r1(p.x)} ${r1(p.y)})`, tabindex: '0', role: 'button', 'data-fk': 'mb:' + e.code, 'aria-pressed': selCode === e.code ? 'true' : 'false',
        'aria-label': `${e.code} ${e.title}. ${linked ? 'Joined to other epics.' : 'Not joined to any other epic.'} Press Enter to see what it is joined to.`,
      });
      g.append(sv('circle', { class: 'bb', r: r1(p.r) }));
      g.append(sv('text', { class: 'bb-code', 'text-anchor': 'middle', dy: '0.34em' }, e.code.replace('E-', '')));
      const lx = Math.sin(p.t) * (p.r + 10);
      const ly = -Math.cos(p.t) * (p.r + 10);
      const top = Math.abs(Math.sin(p.t)) < 0.3;
      g.append(sv('text', { class: 'bb-label', x: r1(lx), y: r1(ly), 'text-anchor': top ? 'middle' : right ? 'start' : 'end', dy: top ? (Math.cos(p.t) > 0 ? '-0.4em' : '1em') : '0.34em' }, `${e.code} ${trunc(e.title, 24)}`));
      const pick = () => { state.mapSel = selCode === e.code ? null : e.code; renderAll('mb:' + e.code); };
      g.addEventListener('click', pick);
      g.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); pick(); } });
      bubbles.append(g);
    });
    svg.append(lines, badges, bubbles);

    const side = h('div', { class: 'map-side' });
    if (selCode) {
      const e = byCode.get(selCode);
      const partners = [...edges.entries()].filter(([k]) => k.split('|').includes(selCode)).map(([k, n]) => [k.split('|').find((c) => c !== selCode), n]).sort((a, b) => b[1] - a[1]);
      put(side,
        h('h3', {}, `${e.code} ${e.title}`),
        h('p', { class: 'quiet' }, `${plural(tasksUnder(e).length, 'task')} \u00b7 ${plural(kidsOf(e.code).length, 'story', 'stories')}`),
        partners.length
          ? h('ul', { class: 'plain lrows' }, partners.map(([c, n]) => h('li', { class: 'lrow' }, h('button', { class: 'lrow-main', type: 'button', onclick: () => { state.mapSel = c; renderAll('mb:' + c); } }, icon('epic', 16), codeText(c), h('span', { class: 'lrow-title' }, byCode.get(c).title)), h('span', { class: 'lrow-chips' }, chip('info', plural(n, mode === 'work' ? 'link' : 'record'))))))
          : h('p', { class: 'quiet' }, mode === 'work' ? 'No link runs from this epic to another epic.' : 'No decision or fact concerns this epic and another one.'),
        h('div', { class: 'hub-actions' }, h('button', { class: 'small', type: 'button', onclick: () => setFocus(e.code, { reset: true, view: 'around', closeCheckup: true, scroll: true }) }, icon('around', 14), `Around ${e.code}`)));
    } else {
      const total = sum([...edges.values()]);
      const alone = epics.filter((e) => ![...edges.keys()].some((k) => k.split('|').includes(e.code)));
      put(side,
        h('h3', {}, mode === 'work' ? `${total} links between epics` : `${total} shared records between epics`),
        h('p', { class: 'quiet' }, 'A line is thicker the more links it stands for. Choose a bubble to see what it is joined to.'),
        alone.length ? h('p', {}, h('strong', {}, 'Not joined to anything: '), alone.map((e) => e.code).join(', '), '.') : null,
        h('p', { class: 'quiet' }, mode === 'work' ? `Only ${total} of the ${D.links.length} links in the project leave their epic, so the epics are mostly independent. That is something a 245 dot map cannot show.` : 'Decisions and facts that name work in more than one epic.'));
    }
    return h('div', { class: 'map-wrap' },
      h('div', {},
        h('div', { class: 'segmented', role: 'group', 'aria-label': 'What the lines count' },
          [['work', 'Links between work'], ['records', 'Shared decisions and facts']].map(([v, l]) => h('button', { type: 'button', 'aria-pressed': mode === v ? 'true' : 'false', 'data-fk': 'mm:' + v, onclick: () => { state.mapMode = v; state.mapSel = null; renderAll('mm:' + v); } }, l))),
        h('div', { class: 'time-scroll', tabindex: '0', role: 'region', 'aria-label': 'Map of epics. It scrolls sideways on a small screen.' }, h('div', { class: 'map-inner' }, svg))),
      side);
  }

  /* ===== 16. the floating dock: design notes and "today's map" ============================================= */

  const NOTES = {
    around: {
      title: 'Why Around looks like this',
      body: 'A map of everything answers no question; one item and what touches it does. The same groups an agent gets from recall (decisions in force, project-wide decisions, facts, earlier ones, and why each is shown) are drawn as labelled groups around the item, each with its own line style, and written out again beside it. A person and an agent read the same facts.',
      asks: 'Plain question: "What do we know and what was decided about this?"',
      tool: 'Mirrors: recall',
    },
    structure: {
      title: 'Why Structure looks like Obsidian\'s folder structure',
      body: 'The hierarchy is the skeleton: each epic is a hub, each story a smaller hub, each task a small dot around its story, and the hubs are joined in a tree, so all 245 items are visible at once without a ball. Links between items would cross the tree, so they are off until you ask (like hiding manual links), and always drawn for the item you choose. Names follow Obsidian\'s text fade threshold: epics always, stories and tasks as you zoom in, any item on hover. "Focus on one epic" gives the radial view with every name, because there is room.',
      asks: 'Plain question: "How does it all fit together?"',
      tool: 'Mirrors: hierarchy and progress (list_nodes, node_state)',
    },
    order: {
      title: 'Why Order is a chain, left to right',
      body: 'Dependencies only mean something where there is an order, so they get their own picture: what must come first on the left, what waits on the right. Blocked is red and overdue carries a clock, so what is stuck stands out; filtering to one chain keeps the picture small. The same chain is a list beside it.',
      asks: 'Plain question: "What must come first, and what does this block?"',
      tool: 'Mirrors: readiness, waiting_on and the needs first links',
    },
    timeline: {
      title: 'Why Timeline lists decisions and facts by date',
      body: 'Decisions and facts are records that are replaced, not edited, so their history is the story: what was decided, what replaced it, and what nobody has confirmed. A date line with status badges, "replaced by" arrows and a "since I last looked" filter answers it without a graph.',
      asks: 'Plain question: "What changed, and what was replaced?"',
      tool: 'Mirrors: history, supersession (decision_history, fact_history)',
    },
    checkup: {
      title: 'Why the Check-up is counts',
      body: 'The one thing a whole-project map is good for is spotting what is missing or odd. So it is five counts that open lists, and the old map survives only as one bubble per epic, labelled "for checking", instead of 245 dots.',
      asks: 'Plain question: "What looks the same, conflicting or forgotten?"',
      tool: 'Mirrors: coverage_report, find_overlaps, list_decisions, list_nodes',
    },
  };

  function hairball() {
    let seed = 11;
    const rnd = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
    const gauss = () => (rnd() + rnd() + rnd() + rnd() - 2) / 2;
    const pts = [];
    epics.forEach((e, i) => {
      const t = (i / epics.length) * TWO_PI;
      const n = tasksUnder(e).length + kidsOf(e.code).length + 1;
      const ox = 95 * Math.cos(t);
      const oy = 72 * Math.sin(t);
      for (let k = 0; k < n; k += 1) pts.push([180 + ox + gauss() * 105, 140 + oy + gauss() * 82]);
    });
    const lines = [];
    for (let i = 1; i < pts.length; i += 1) {
      let best = 0; let bd = 1e9;
      for (let j = Math.max(0, i - 40); j < i; j += 1) { const d = Math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]) + rnd() * 30; if (d < bd) { bd = d; best = j; } }
      lines.push([i, best]);
    }
    while (lines.length < 271) { const a = Math.floor(rnd() * pts.length); const b = Math.floor(rnd() * pts.length); if (a !== b) lines.push([a, b]); }
    const svg = sv('svg', { class: 'hairball', viewBox: '0 0 360 280', role: 'img', 'aria-label': 'Today\'s map: a ball of 245 small dots joined by 271 lines that cross each other, with no labels' });
    const g = sv('g', { class: 'hb-lines' });
    lines.forEach(([a, b]) => g.append(sv('line', { x1: r1(pts[a][0]), y1: r1(pts[a][1]), x2: r1(pts[b][0]), y2: r1(pts[b][1]) })));
    const d = sv('g', { class: 'hb-dots' });
    pts.forEach((p) => d.append(sv('circle', { cx: r1(p[0]), cy: r1(p[1]), r: 2.2 })));
    svg.append(g, d);
    return svg;
  }

  function renderDock() {
    const dock = $('#dock');
    const key = state.checkup ? 'checkup' : state.view;
    const note = NOTES[key];
    const closeAll = (which) => { state[which] = false; renderAll(which === 'notes' ? 'dock-notes' : 'dock-compare'); };
    put(dock,
      state.compare ? h('section', { class: 'dock-panel compare', 'aria-label': 'Today\'s map, for contrast' },
        h('div', { class: 'peek-head' }, h('h2', {}, 'Today\'s map, for contrast'), h('button', { class: 'ghost icon-only small', type: 'button', 'aria-label': 'Close the comparison', onclick: () => closeAll('compare') }, icon('close', 14))),
        hairball(),
        h('p', { class: 'quiet' }, '245 records, 271 lines, no labels. Every line crosses several others, and you point at a dot to read it. The proposal replaces it with the four views and the check-up above.')) : null,
      state.notes ? h('section', { class: 'dock-panel notes', 'aria-label': 'Design notes' },
        h('div', { class: 'peek-head' }, h('h2', {}, note.title), h('button', { class: 'ghost icon-only small', type: 'button', 'aria-label': 'Close the design notes', onclick: () => closeAll('notes') }, icon('close', 14))),
        h('p', {}, note.body),
        h('p', { class: 'notes-ask' }, note.asks),
        h('p', { class: 'quiet mono notes-tool' }, note.tool)) : null,
      h('div', { class: 'dock-buttons' },
        h('button', { class: 'toggle', type: 'button', 'aria-pressed': state.notes ? 'true' : 'false', 'data-fk': 'dock-notes', onclick: () => { state.notes = !state.notes; renderAll('dock-notes'); } }, icon('notes', 16), 'Design notes'),
        h('button', { class: 'toggle', type: 'button', 'aria-label': 'Compare: today\'s map', 'aria-pressed': state.compare ? 'true' : 'false', 'data-fk': 'dock-compare', onclick: () => { state.compare = !state.compare; renderAll('dock-compare'); } }, icon('compare', 16), h('span', {}, 'Compare', h('span', { class: 'dock-long' }, ': today\'s map')))));
  }

  /* ===== 17. start ======================================================================================== */

  function buildPage() {
    const page = $('#page');
    put(page,
      h('header', { class: 'page-header' },
        h('div', { class: 'page-title' }, h('h1', {}, 'Graph'), chip('accent proposal', 'Proposed'), h('p', { class: 'page-sub' }, 'See what is around one thing, how it fits, what comes first, and what changed.')),
        h('div', { class: 'page-actions' }, h('button', { class: 'primary', type: 'button', onclick: () => toast(NOT_HERE) }, icon('plus', 16), 'New item'))),
      h('section', { id: 'focusbar', class: 'focusbar', 'aria-label': 'Choose what to look at' }),
      h('section', { id: 'checkup', class: 'attention checkup', 'aria-label': 'Check-up' }),
      h('div', { id: 'checkup-panel', class: 'panel checkup-panel', hidden: true, role: 'region', 'aria-label': 'Check-up details' }),
      h('div', { id: 'tabs', class: 'tabs', role: 'tablist', 'aria-label': 'Ways to look' }),
      h('div', { id: 'view', class: 'view', role: 'tabpanel' }));
  }

  function init() {
    readHash();
    buildPage();
    buildAppearance();
    $$('[data-stub]').forEach((el) => el.addEventListener('click', (e) => { e.preventDefault(); toast(NOT_HERE); }));
    $('#sidebar-toggle').addEventListener('click', () => openSidebar(true));
    $('#sidebar-close').addEventListener('click', () => { openSidebar(false); $('#sidebar-toggle').focus(); });
    $('#sidebar-scrim').addEventListener('click', () => openSidebar(false));
    $('#top-search').addEventListener('submit', (e) => {
      e.preventDefault();
      const q = $('#global-search').value;
      const box = $('#focus-search');
      box.value = q;
      box.focus();
      box.dispatchEvent(new Event('input'));
    });
    const jump = () => { const b = $('#focus-search'); b.scrollIntoView({ block: 'center' }); b.focus(); b.select(); };
    $('#jump').addEventListener('click', jump);
    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); jump(); }
      else if (e.key === 'Escape') {
        if ($('#sidebar').classList.contains('open')) openSidebar(false);
        else if (state.notes || state.compare) { state.notes = false; state.compare = false; renderAll(); }
      }
    });
    window.matchMedia('(min-width: 1100px)').addEventListener('change', (m) => { if (m.matches) openSidebar(false); });
    renderAll();
  }
  init();

})();
