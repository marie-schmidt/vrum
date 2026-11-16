// Talking to the server. The token is typed once per browser session and kept only in that
// session's storage. It is sent in the Authorization header and never put in an address or a
// cookie.

const KEY = 'vrum.token';

// A request the server turned down, with its words (they are the same as an agent is told).
export class Refusal extends Error {}
// The token is missing or wrong.
export class NotSignedIn extends Refusal {}

let token = sessionStorage.getItem(KEY) || '';

export function signedIn() {
  return token !== '';
}

export function setToken(value) {
  token = value.trim();
  if (token) sessionStorage.setItem(KEY, token);
  else sessionStorage.removeItem(KEY);
}

export function signOut() {
  setToken('');
}

async function send(path, options) {
  let response;
  try {
    response = await fetch(path, {
      cache: 'no-store',
      ...options,
      // With no token there is no header at all, so the server says that none was sent.
      headers: { ...(options && options.headers), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
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
  if (response.status === 401) {
    const words = body && body.error && body.error.message;
    throw new NotSignedIn(words || 'The token is not right, so nothing was done.');
  }
  if (!response.ok) {
    throw new Refusal(
      (body && body.message) || `The server answered with an error (${response.status}).`,
    );
  }
  return body;
}

// Call a tool, as an agent would, and return what it returned.
export function call(tool, args) {
  return send(`/board/api/tool/${tool}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args || {}),
  });
}

// Try a tool that only reads, as the Tools page does. The server decides from its own tool list
// which tools those are, and turns any other away.
export function tryTool(tool, args) {
  return send(`/board/api/try/${tool}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args || {}),
  });
}

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
