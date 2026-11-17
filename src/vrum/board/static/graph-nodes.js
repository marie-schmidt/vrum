// What a node of the map looks like: its size from its words, what it says about itself (kind and
// number, state, title) and the SVG that draws it. A node is a rounded box with the kind and number
// on top, its state at the right of that line, and the title inside, wrapped onto at most three
// lines. A graph too big for that draws each node as a small tile with the kind's icon instead; the
// page then shows the words when the tile is pointed at. Colours come from classes (graph-views.css),
// never from here, so both themes follow the tokens.

import { plural, s } from './dom.js';
import { icon } from './icons.js';
import { STATUS } from './labels.js';
import { TYPES } from './graph-data.js';

const MOST_LINES = 3;
const EPIC_TONES = ['info', 'accent', 'ok', 'warn', 'neutral', 'danger'];
const STATUS_TONE = { new: 'neutral', ready: 'ok', in_progress: 'accent', blocked: 'warn', done: 'ok', dropped: 'neutral' };
const RULE_STATE = {
  held: { icon: 'new', word: 'Held', tone: 'neutral' },
  settled: { icon: 'done', word: 'Settled', tone: 'ok' },
  dropped: { icon: 'dropped', word: 'Dropped', tone: 'neutral' },
};

let canvas = null;

// The sizes of text on the map, from the page's own type scale (so a larger text size makes larger
// nodes) and the font it is set in.
export function metricsOf(svg) {
  const style = getComputedStyle(svg);
  const base = parseFloat(style.fontSize) || 13;
  return { base, family: style.fontFamily || 'sans-serif', u: base / 13, kind: base * 0.85, line: Math.round(base * 1.4) };
}

function width(m, text, px, weight = 500) {
  canvas ||= document.createElement('canvas').getContext('2d');
  canvas.font = `${weight} ${px}px ${m.family}`;
  return canvas.measureText(text).width * 1.04 + 1;
}

// Pieces of a word that has to be broken because it is wider than the node.
function pieces(m, word, room) {
  const found = [];
  let rest = word;
  while (rest.length) {
    let take = rest.length;
    while (take > 1 && width(m, rest.slice(0, take), m.base) > room) take -= 1;
    found.push(rest.slice(0, take));
    rest = rest.slice(take);
  }
  return found;
}

// The title cut into lines that fit `room`, at most `MOST_LINES`; the last ends in … if there is more.
function wrap(m, title, room) {
  const lines = [];
  let line = '';
  const push = () => {
    if (line) lines.push(line);
    line = '';
  };
  for (const word of title.replace(/\s+/g, ' ').trim().split(' ')) {
    for (const piece of width(m, word, m.base) > room ? pieces(m, word, room) : [word]) {
      const joined = line ? `${line} ${piece}` : piece;
      if (width(m, joined, m.base) <= room) line = joined;
      else {
        push();
        line = piece;
      }
    }
  }
  push();
  if (lines.length <= MOST_LINES) return lines.length ? lines : [''];
  let last = lines.slice(MOST_LINES - 1).join(' ');
  while (last.length > 1 && width(m, `${last}…`, m.base) > room) last = last.slice(0, -1);
  return [...lines.slice(0, MOST_LINES - 1), `${last.trimEnd()}…`];
}

// What a node says about itself, and which tones the page colours it with.
export function describeNode(dot, cfg, epicIndex) {
  const record = dot.record;
  let state;
  if (dot.collapsed) state = { icon: null, word: plural(dot.count, 'item'), tone: 'neutral' };
  else if (dot.isNode) state = { icon: STATUS[dot.status].icon, word: STATUS[dot.status].label, tone: STATUS_TONE[dot.status] };
  else if (dot.type === 'rule') state = RULE_STATE[dot.status] || RULE_STATE.held;
  else if (dot.ended) state = { icon: 'dropped', word: record.status === 'superseded' ? 'Replaced' : 'Withdrawn', tone: 'neutral' };
  else if (dot.confirmation === 'confirmed') state = { icon: 'confirmed', word: 'Confirmed', tone: 'ok' };
  else state = { icon: 'unconfirmed', word: 'Unconfirmed', tone: 'warn' };
  let fill = null;
  if (cfg.colour === 'status') fill = state.tone;
  else if (cfg.colour === 'epic') fill = epicIndex(dot) < 0 ? 'neutral' : EPIC_TONES[epicIndex(dot) % EPIC_TONES.length];
  return { kind: TYPES[dot.type].label, state, fill, tone: dot.isNode ? 'neutral' : 'accent', struck: dot.ended || dot.status === 'dropped' };
}

// The box of a node with its words: width and height in the map's own units, and what to draw.
export function labelledBox(dot, info, m) {
  const u = m.u;
  const pad = 12 * u;
  const head = `${info.kind.toUpperCase()} · ${dot.isNode ? dot.code : dot.number}`;
  const headWidth = 18 * u + head.length * m.kind * 0.68;
  const stateWidth = info.state ? (info.state.icon ? 18 * u : 0) + width(m, info.state.word, m.kind, 600) + 14 * u : 0;
  const wanted = width(m, dot.title, m.base) + 2 * pad;
  const w = Math.round(Math.max(Math.min(Math.max(wanted, 156 * u), 232 * u), headWidth + stateWidth + 2 * pad));
  const lines = wrap(m, dot.title, w - 2 * pad);
  const h = Math.round(2 * pad + m.kind * 1.3 + 8 * u + lines.length * m.line - 0.15 * m.base);
  return { w, h, lines, head, pad, compact: false, info };
}

// A small tile, for a graph with too many nodes to label.
export function tileBox(dot, info, m) {
  const size = Math.round(34 * m.u);
  return { w: size, h: size, compact: true, info };
}

function placed(element, x, y) {
  element.setAttribute('x', String(x));
  element.setAttribute('y', String(y));
  return element;
}

// The parts of a node, centred on its own origin.
export function drawNode(dot, box, m) {
  const { w, h, info } = box;
  const rect = s('rect', { class: 'shape', x: -w / 2, y: -h / 2, width: w, height: h, rx: Math.round(8 * m.u) });
  const pin = s('circle', { class: 'pin-mark', cx: w / 2 - 4 * m.u, cy: -h / 2 + 4 * m.u, r: 5 * m.u });
  if (box.compact) {
    const tile = Math.round(16 * m.u);
    return [rect, placed(icon(dot.type, { size: tile, className: 'kind-icon' }), -tile / 2, -tile / 2), info.struck ? s('path', { class: 'strike', d: `M${-w / 2 + 6 * m.u},${h / 2 - 6 * m.u} L${w / 2 - 6 * m.u},${-h / 2 + 6 * m.u}` }) : null, pin];
  }
  const left = -w / 2 + box.pad;
  const top = -h / 2 + box.pad;
  const size = Math.round(14 * m.u);
  const parts = [rect];
  parts.push(placed(icon(dot.type, { size, className: 'kind-icon' }), left, top));
  parts.push(s('text', { class: 'kind-text', x: left + 18 * m.u, y: top + m.kind * 0.95 }, box.head));
  const { state } = info;
  if (state) {
    const right = w / 2 - box.pad;
    const room = width(m, state.word, m.kind, 600);
    parts.push(s('text', { class: `state-text tone-${state.tone}`, x: right, y: top + m.kind * 0.95, 'text-anchor': 'end' }, state.word));
    if (state.icon) parts.push(placed(icon(state.icon, { size, className: `state-icon tone-${state.tone}` }), right - room - 4 * m.u - size, top - 0.5 * m.u));
  }
  const first = top + m.kind * 1.3 + 8 * m.u + m.base * 0.95;
  parts.push(
    s(
      'text',
      { class: 'label', x: left, y: first },
      box.lines.map((line, at) => s('tspan', { x: left, dy: at ? m.line : 0 }, line)),
    ),
  );
  parts.push(pin);
  return parts;
}
