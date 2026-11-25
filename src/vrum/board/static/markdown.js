// The board's own small Markdown renderer. It makes headings, paragraphs, lists, emphasis, code,
// quotes and links to web addresses, and it makes them out of elements and text only: raw HTML is
// never read as HTML (a <script> or an <img> is shown as the characters it is), an image is shown
// as its text and never loaded, and a link is made only for an http or https address and opens in a
// new tab. `[[story:12]]` and the other references become links with the item's current title.

import { h } from './dom.js';

// -- inline: code, references, links, emphasis ------------------------------------------------------

let refPattern = null;
let formatReference = (kind, number) => `[[${kind}:${number}]]`;
let rules = null;

// Registered once by references.js so markdown.js does not import references.js in a cycle.
export function useReferences(pattern, render) {
  refPattern = pattern;
  formatReference = render;
  rules = null;
}

const inlineRules = () => (rules ??= [
  { name: 'code', re: /`([^`\n]+)`/g },
  ...(refPattern ? [{ name: 'reference', re: new RegExp(refPattern, 'g') }] : []),
  { name: 'image', re: /!\[([^\]\n]*)\]\(([^)\s]*)\)/g },
  { name: 'link', re: /\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g },
  { name: 'url', re: /https?:\/\/[^\s<>()[\]]+/g },
  { name: 'strong', re: /\*\*([^\s*](?:[^*\n]*[^\s*])?)\*\*|__([^\s_](?:[^_\n]*[^\s_])?)__/g },
  { name: 'em', re: /\*([^\s*](?:[^*\n]*[^\s*])?)\*|\b_([^\s_](?:[^_\n]*[^\s_])?)_\b/g },
  { name: 'del', re: /~~([^\s~](?:[^~\n]*[^\s~])?)~~/g },
]);

const external = (url, ...children) => h('a', { class: 'external', href: url, target: '_blank', rel: 'noopener noreferrer' }, ...children);

// The text as a list of nodes. `links` is false inside a link, which cannot hold another.
export function inline(text, links = true) {
  const out = [];
  let at = 0;
  while (at < text.length) {
    let best = null;
    for (const pattern of inlineRules()) {
      if (!links && (pattern.name === 'link' || pattern.name === 'url' || pattern.name === 'reference')) continue;
      pattern.re.lastIndex = at;
      const found = pattern.re.exec(text);
      if (found && (best === null || found.index < best.found.index)) best = { pattern, found };
    }
    if (best === null) {
      out.push(text.slice(at));
      break;
    }
    const { pattern, found } = best;
    if (found.index > at) out.push(text.slice(at, found.index));
    at = found.index + found[0].length;
    switch (pattern.name) {
      case 'code':
        out.push(h('code', {}, found[1]));
        break;
      case 'reference':
        out.push(formatReference(found[1], found[2]));
        break;
      case 'image':
        out.push(found[0]); // shown as the characters it is, and never loaded
        break;
      case 'link':
        out.push(external(found[2], ...inline(found[1], false)));
        break;
      case 'url': {
        const url = found[0].replace(/[.,;:!?'"]+$/, '');
        at -= found[0].length - url.length;
        out.push(external(url, url));
        break;
      }
      case 'strong':
        out.push(h('strong', {}, ...inline(found[1] ?? found[2], links)));
        break;
      case 'em':
        out.push(h('em', {}, ...inline(found[1] ?? found[2], links)));
        break;
      default:
        out.push(h('del', {}, ...inline(found[1], links)));
    }
  }
  return out;
}

// -- blocks: headings, lists, quotes, code, paragraphs --------------------------------------------------

const FENCE = /^\s*(```+|~~~+)\s*([\w+#.-]*)\s*$/;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const RULE = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const BLANK = /^\s*$/;

const startsBlock = (line) => FENCE.test(line) || HEADING.test(line) || QUOTE.test(line) || RULE.test(line) || ITEM.test(line);

function paragraph(lines) {
  const parts = [];
  lines.forEach((line, at) => {
    if (at) parts.push(h('br'));
    parts.push(...inline(line.trim()));
  });
  return h('p', {}, ...parts);
}

function list(lines, start) {
  const first = ITEM.exec(lines[start]);
  const base = first[1].length;
  const ordered = /\d/.test(first[2]);
  const items = [];
  let at = start;
  while (at < lines.length) {
    const found = ITEM.exec(lines[at]);
    if (!found || found[1].length < base || found[1].length > base + 1 || /\d/.test(found[2]) !== ordered) break;
    const content = [found[3]];
    const indent = found[1].length + found[2].length + 1;
    at += 1;
    // What is indented under the item belongs to it: its own lines, and a list inside it.
    while (at < lines.length) {
      const next = lines[at];
      if (BLANK.test(next)) {
        const after = lines.slice(at + 1).find((line) => !BLANK.test(line));
        if (after && /^\s/.test(after) && after.search(/\S/) > base + 1) {
          content.push('');
          at += 1;
          continue;
        }
        break;
      }
      if (next.search(/\S/) <= base + 1 && (ITEM.test(next) || startsBlock(next))) break;
      if (next.search(/\S/) <= base && !ITEM.test(next)) break;
      content.push(next.slice(Math.min(indent, next.search(/\S/))));
      at += 1;
    }
    items.push(content);
  }
  const rendered = items.map((content) => {
    const inner = blocks(content);
    // A tight item is its text, with no paragraph around it.
    const nodes = inner.length && inner[0].tagName === 'P' ? [...inner[0].childNodes, ...inner.slice(1)] : inner;
    return h('li', {}, ...nodes);
  });
  return { element: ordered ? h('ol', {}, ...rendered) : h('ul', {}, ...rendered), next: at };
}

export function blocks(lines) {
  const out = [];
  let at = 0;
  while (at < lines.length) {
    const line = lines[at];
    if (BLANK.test(line)) {
      at += 1;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const code = [];
      at += 1;
      while (at < lines.length && !new RegExp(`^\\s*${fence[1][0]}{${fence[1].length},}\\s*$`).test(lines[at])) {
        code.push(lines[at]);
        at += 1;
      }
      at += 1;
      out.push(h('pre', {}, h('code', fence[2] ? { class: 'language-code', dataset: { language: fence[2] } } : {}, code.join('\n'))));
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      out.push(h(`h${Math.min(6, heading[1].length + 3)}`, { class: 'md-heading' }, ...inline(heading[2])));
      at += 1;
      continue;
    }
    if (RULE.test(line)) {
      out.push(h('hr'));
      at += 1;
      continue;
    }
    if (QUOTE.test(line)) {
      const quoted = [];
      while (at < lines.length && QUOTE.test(lines[at])) {
        quoted.push(QUOTE.exec(lines[at])[1]);
        at += 1;
      }
      out.push(h('blockquote', {}, ...blocks(quoted)));
      continue;
    }
    if (ITEM.test(line)) {
      const made = list(lines, at);
      out.push(made.element);
      at = made.next;
      continue;
    }
    const gathered = [];
    while (at < lines.length && !BLANK.test(lines[at]) && (gathered.length === 0 || !startsBlock(lines[at]))) {
      gathered.push(lines[at]);
      at += 1;
    }
    out.push(paragraph(gathered));
  }
  return out;
}

// Markdown to a <div class="markdown">. Whatever it is given is only ever put in as text.
export function renderMarkdown(source, { className = 'markdown' } = {}) {
  const text = String(source ?? '').replace(/\r\n?/g, '\n').replace(/\x00/g, '');
  return h('div', { class: className }, ...blocks(text.split('\n')));
}

// What the Markdown says as plain words on one line, for a place that cannot show it as Markdown (a
// cell of a table, a title). It is what renderMarkdown shows, read back as text, so only the marks
// the renderer turns into formatting go: an unpaired or in-word `_` or `*` stays, a link is its words
// and a reference is its code and title. Only the start is read, which is all such a place shows.
const BLOCKS = new Set(['P', 'LI', 'UL', 'OL', 'PRE', 'BLOCKQUOTE', 'BR', 'HR', 'H4', 'H5', 'H6', 'DIV']);
export function plainText(source) {
  const words = [];
  const walk = (node) => {
    if (node.nodeType === Node.TEXT_NODE) words.push(node.data);
    else {
      if (BLOCKS.has(node.tagName)) words.push(' ');
      node.childNodes.forEach(walk);
      if (BLOCKS.has(node.tagName)) words.push(' ');
    }
  };
  walk(renderMarkdown(String(source ?? '').slice(0, 4000)));
  return words.join('').replace(/\s+/g, ' ').trim();
}
