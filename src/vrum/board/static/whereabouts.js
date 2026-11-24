// Where a piece of work is, and which run is doing it (M41). A claim, a handoff note and a finished piece of work may say, in
// optional words, the repository, the branch, the commit and the pull request the work lives in, and a run label and a client
// name (`run-2026-11-22-a`, `claude-code`). They are what the holder says: Vrum never reads a checkout or asks the host of a
// repository. The run label and the client name are only a label to tell several runs of one agent apart: who did something is
// still the name of the key, and nothing here is ever drawn as a person or an agent.
//
// Every page draws these words through this file, so they look the same everywhere, and nothing at all is drawn when nothing
// was said. Every function takes the object that carries the six fields (a claim, a handoff, an event's `after`, a node's
// `where`) and gives back null (or an empty list) when none of them is there. Text is only ever put in as text. An address
// becomes a link only when it is plainly an `https` address (a pull request one only when its path is exactly
// /OWNER/REPO/pull/NUMBER) and opens in a new tab; any other address is shown as the text it is. A branch and a run label are
// shown as plain words, never as code, and a commit is its first seven characters with the whole hash in its tooltip.

import { h } from './dom.js';
import { icon } from './icons.js';

export const WHERE_FIELDS = ['repository', 'branch', 'commit', 'pull_request', 'run_label', 'client_name'];

const PULL_REQUEST_PATH = /^\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/([1-9][0-9]*)$/;

// The address as a URL when it is a plain `https` address: a host, no login, no query, no fragment and no backslash or space
// (a tab or a line break would be taken out by the parser, so they are refused first). With `pullRequest` its path must be
// exactly /OWNER/REPO/pull/NUMBER and it has no port. Otherwise null, and the address is not made a link.
export function plainHttps(address, { pullRequest = false } = {}) {
  const text = String(address ?? '');
  if (!/^https:\/\//i.test(text) || /[\s\\?#]/.test(text)) return null;
  let url;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if (url.protocol !== 'https:' || !url.hostname || url.username || url.password || url.search || url.hash) return null;
  if (pullRequest && (url.port || !PULL_REQUEST_PATH.test(url.pathname))) return null;
  return url;
}

// What was said: an object with only the words that are there, or null when there are none.
export function saidOf(source) {
  if (!source) return null;
  const said = {};
  for (const field of WHERE_FIELDS) {
    const word = typeof source[field] === 'string' ? source[field].trim() : '';
    if (word) said[field] = word;
  }
  return Object.keys(said).length ? said : null;
}

export const shortCommit = (commit) => String(commit).slice(0, 7);

const link = (href, children, props = {}) => h('a', { class: 'external where-link', href, target: '_blank', rel: 'noopener noreferrer', ...props }, children);

// The repository as the words a person reads (github.com/OWNER/REPO) and, when it is a plain address, where it goes.
function repositoryOf(address) {
  const url = plainHttps(address);
  return url ? { text: `${url.host}${url.pathname.replace(/\/+$/, '')}`, href: url.href } : { text: address, href: null };
}

// The pull request as "Pull request 12" and, when it is a plain pull request address, where it goes. Any other text is shown as it is.
function pullRequestOf(address) {
  const url = plainHttps(address, { pullRequest: true });
  return url ? { number: PULL_REQUEST_PATH.exec(url.pathname)[1], href: url.href } : { number: null, href: null };
}

// What the run is called: "run-2026-11-22-a (claude-code)", or whichever of the two was said.
export function runWords(source) {
  const said = saidOf(source);
  if (!said || (!said.run_label && !said.client_name)) return '';
  if (said.run_label && said.client_name) return `${said.run_label} (${said.client_name})`;
  return said.run_label || said.client_name;
}

// A small part of the place line: a quiet sign, a word for a screen reader and the words.
const part = (kind, name, sign, ...children) => h('span', { class: `where-part where-${kind}` }, icon(sign, { size: 13 }), h('span', { class: 'sr-only' }, `${name}: `), ...children);
const dot = () => h('span', { class: 'where-dot', 'aria-hidden': 'true' }, '·');

// The place of the work on one line: "repository · branch · commit · pull request", each only when it was said.
export function whereLine(source) {
  const said = saidOf(source);
  if (!said) return null;
  const parts = [];
  if (said.repository) {
    const found = repositoryOf(said.repository);
    parts.push(part('repository', 'Repository', 'link', found.href ? link(found.href, found.text, { title: said.repository }) : h('span', { class: 'where-text', title: 'Repository' }, found.text)));
  }
  if (said.branch) parts.push(part('branch', 'Branch', 'branch', h('span', { class: 'where-text', title: 'Branch' }, said.branch)));
  if (said.commit) parts.push(part('commit', 'Commit', 'commit', h('span', { class: 'where-text mono', title: `Commit ${said.commit}` }, shortCommit(said.commit))));
  if (said.pull_request) {
    const found = pullRequestOf(said.pull_request);
    parts.push(part('pull-request', 'Pull request', 'pull_request', found.href ? link(found.href, `Pull request ${found.number}`, { title: said.pull_request }) : h('span', { class: 'where-text', title: 'Pull request' }, said.pull_request)));
  }
  if (!parts.length) return null;
  return h('span', { class: 'where-line' }, parts.flatMap((one, at) => (at ? [dot(), one] : [one])));
}

// Which run it is, on one line: "run-2026-11-22-a (claude-code)". An audit label, drawn quietly and never as a person.
export function runLine(source) {
  const words = runWords(source);
  if (!words) return null;
  return h('span', { class: 'where-run', title: 'Run label and client, as the holder said them' }, icon('agent', { size: 13 }), h('span', { class: 'sr-only' }, 'Run: '), h('span', { class: 'where-text' }, words));
}

// Both lines, the run and then the place, as the one block a page puts where it says where the work is. Null when nothing was said.
export function whereBlock(source) {
  const run = runLine(source);
  const place = whereLine(source);
  if (!run && !place) return null;
  return h('div', { class: 'where' }, run ? h('div', { class: 'where-row' }, run) : null, place ? h('div', { class: 'where-row' }, place) : null);
}

// The same words as the end of a sentence of the history: ", on branch feature/x, at commit a1b2c3d, in pull request 12, in run
// run-1 (claude-code)". An empty list when nothing was said, so that a sentence with no words reads as it always did. The words
// are whole: nothing is cut.
export function whereWords(source) {
  const said = saidOf(source);
  if (!said) return [];
  const words = [];
  if (said.repository) {
    const found = repositoryOf(said.repository);
    words.push(', in repository ', found.href ? link(found.href, found.text) : found.text);
  }
  if (said.branch) words.push(`, on branch ${said.branch}`);
  if (said.commit) words.push(', at commit ', h('span', { class: 'mono', title: `Commit ${said.commit}` }, shortCommit(said.commit)));
  if (said.pull_request) {
    const found = pullRequestOf(said.pull_request);
    words.push(', in ', found.href ? link(found.href, `pull request ${found.number}`) : `pull request ${said.pull_request}`);
  }
  const run = runWords(said);
  if (run) words.push(said.run_label ? `, in run ${run}` : `, with ${run}`);
  return words;
}
