// Talking to the server. A person is signed in by a session cookie that the page cannot read (the
// server sets it and the browser sends it). Every change carries the session's own secret in a
// header, which the server checks, so that another website cannot make this browser change
// anything. A project's data is always asked for by the project's number in the address, so an
// answer can only ever be for the project it was asked for.

// A request the server turned down, with its words (they are the same as an agent is told).
export class Refusal extends Error {}
// Nobody is signed in, or the session ended.
export class NotSignedIn extends Refusal {}
// An answer for a project the board has left. It is dropped and never drawn.
export class Stale extends Refusal {}

let csrf = '';
let project = null; // the number of the project the board is on, or null

export function setProject(id) {
  project = id;
}

export function projectId() {
  return project;
}

async function send(path, options) {
  const mine = project;
  const changing = options && options.method && options.method !== 'GET';
  let response;
  try {
    response = await fetch(path, {
      cache: 'no-store',
      ...options,
      headers: { ...(options && options.headers), ...(changing && csrf ? { 'X-Vrum-CSRF': csrf } : {}) },
    });
  } catch {
    throw new Refusal('The board cannot reach the server. Check that it is running, then try again.');
  }
  let body = null;
  try {
    body = await response.json();
  } catch {
    // Not JSON: say what happened below.
  }
  // The person may have moved to another project while this was on its way.
  if (mine !== project) throw new Stale('That answer was for a project the board has left.');
  if (response.status === 401) {
    throw new NotSignedIn((body && body.message) || 'You are not signed in, or your session has ended.');
  }
  if (!response.ok) {
    throw new Refusal(
      (body && body.message) || `The server answered with an error (${response.status}).`,
    );
  }
  return body;
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };
const post = (path, body) => send(path, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body || {}) });
const scoped = (path) => `/board/api/projects/${project}${path}`;

// -- who is signed in -----------------------------------------------------------------------------

// Who this browser is signed in as, or null when nobody is.
export async function loadSession() {
  const found = await send('/board/api/session', { method: 'GET' });
  if (!found.account) return null;
  csrf = found.csrf;
  return found;
}

async function started(path, body) {
  const found = await post(path, body);
  csrf = found.csrf;
  return found;
}

export const signIn = (email, password) => started('/board/api/session', { email, password });
export const register = (email, name, password) => started('/board/api/accounts', { email, name, password });

export async function signOut() {
  try {
    await send('/board/api/session', { method: 'DELETE' });
  } finally {
    csrf = '';
  }
}

// -- projects and keys ------------------------------------------------------------------------------

export const projects = async () => (await send('/board/api/projects', { method: 'GET' })).projects;
export const createProject = (name) => post('/board/api/projects', { name });
export const projectInfo = (id) => send(`/board/api/projects/${id}`, { method: 'GET' });
export const keys = async () => (await send(scoped('/keys'), { method: 'GET' })).keys;
export const createKey = (label) => post(scoped('/keys'), { label });
export const revokeKey = async (id) => (await send(scoped(`/keys/${id}`), { method: 'DELETE' })).key;

// -- a project's graph ---------------------------------------------------------------------------

// Call a tool, as an agent would, and return what it returned.
export function call(tool, args) {
  return post(scoped(`/tool/${tool}`), args);
}

// Try a tool that only reads, as the Tools page does. The server decides from its own tool list
// which tools those are, and turns any other away.
export function tryTool(tool, args) {
  return post(scoped(`/try/${tool}`), args);
}

// The state of the project's search index.
export function indexState() {
  return send(scoped('/index'), { method: 'GET' });
}

// What does not belong to a project: how often to ask for news, which language model there is.
export function get(path) {
  return send(path, { method: 'GET' });
}

// Every item of a list that a tool gives a page at a time.
export async function callAll(tool, args) {
  const items = [];
  let after = null;
  do {
    const page = await call(tool, { ...args, limit: 200, after_id: after });
    items.push(...page.items);
    after = page.next_after_id;
  } while (after !== null && after !== undefined);
  return items;
}

// Every item of a list that a tool gives with `offset` and `next_offset` (search, overlaps and
// mentions), page after page. `first` is the first page whole, for what it says besides the items
// (counts, the state of the index).
export async function callPaged(tool, args) {
  const items = [];
  let offset = 0;
  let first = null;
  for (;;) {
    const page = await call(tool, { ...args, limit: 200, offset });
    first = first || page;
    items.push(...page.items);
    if (page.next_offset === null || page.next_offset === undefined) return { items, first };
    offset = page.next_offset;
  }
}

// Every item of one list of the export (links, rules, decisions or memories), which is the one
// place that gives all the links at once.
export async function exported(section) {
  const items = [];
  let after = `${section}:0`;
  while (after) {
    const page = await call('export_graph', { after, limit: 200 });
    if (page.section !== section) break;
    items.push(...page.items);
    after = page.next_after;
  }
  return items;
}
