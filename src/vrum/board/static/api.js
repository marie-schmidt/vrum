// Talking to the server. A person is signed in by a session cookie that the page cannot read (the
// server sets it and the browser sends it). Every change carries the session's own secret in a
// header, which the server checks, so that another website cannot make this browser change
// anything. A project's data is always asked for by the project's number in the address, so an
// answer can only ever be for the project it was asked for.

import { askCode } from './labels.js';

// A request the server turned down, with its words (they are the same as an agent is told, except that
// an item the words name by its reference is named by what it is called: see namesIn). `raw` is what the
// server said, unchanged.
export class Refusal extends Error {
  constructor(message, options) {
    super(namesIn(message), options);
    this.raw = message;
  }
}
// Nobody is signed in, or the session ended.
export class NotSignedIn extends Refusal {}
// The person is not in the project (any more): the owner took them out while the board was open.
export class NotInProject extends Refusal {}
// An answer for a project the board has left. It is dropped and never drawn.
export class Stale extends Refusal {}
// The server could not be reached at all, as opposed to answering no.
export class Unreachable extends Refusal {}

let csrf = '';
let project = null; // the number of the project the board is on, or null
let mail = false; // whether the server can send email (it says so to everyone, signed in or not)
let providers = []; // the other ways to sign in that the server offers: { id, name, kind } (none unless it is set up)
let createAccounts = true; // whether a first sign-in with a provider may make a new account

// -- naming items for a person -------------------------------------------------------------------

// The server speaks to agents, who name an item by its reference (story:12, rule:3, note:7). A person
// is never told a reference: a sentence from the server is shown with each one replaced by the item's
// name. The store, which knows the names, hands its lookup over once (useNames); an item it does not
// know is "an item".
let describe = () => null;

export function useNames(lookup) {
  describe = lookup;
}

const KIND_OF_LETTER = { E: 'epic', S: 'story', T: 'task', AC: 'rule', N: 'note' };
const NUMBER = '[1-9][0-9]{0,17}';
// A reference as an agent writes it (story:12), the letter form of a code (S-12, AC-3, N-7) and the words (note 7).
// A quote around it goes with it, so the name is not quoted twice.
const REFERENCE = new RegExp(`(["'])?\\b(?:(epic|story|task|rule|note):(${NUMBER})|(AC|[ESTN])-(${NUMBER})|([Nn]ote|(?:[Aa]cceptance )?[Cc]riterion) (${NUMBER}))\\b\\1?`, 'g');
const NAME_ROOM = 90;
// An ask is not a record with a name: the server says "Ask 12", and a person reads Q-12.
const ASK = new RegExp(`\\b[Aa]sk (${NUMBER})\\b`, 'g');

export function namesIn(text) {
  if (typeof text !== 'string') return text;
  return text
    .replace(REFERENCE, (_all, _quote, word, number, letters, lettered, said, spoken) => {
      const kind = word || KIND_OF_LETTER[letters] || (/^[Nn]ote$/.test(said) ? 'note' : 'rule');
      const found = describe(`${kind}:${number || lettered || spoken}`);
      if (!found) return 'an item';
      const title = String(found.title).replace(/\s+/g, ' ').trim();
      return `“${title.length > NAME_ROOM ? `${title.slice(0, NAME_ROOM - 1)}…` : title}”`;
    })
    .replace(ASK, (_all, number) => askCode(number));
}

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
    throw new Unreachable('The board cannot reach the server. Check that it is running, then try again.');
  }
  let body = null;
  try {
    body = await response.json();
  } catch {
    // Not JSON: say what happened below.
  }
  // The person may have moved to another project while this was on its way.
  if (mine !== project) throw new Stale('That answer was for a project the board has left.');
  // A wrong name or password for a directory is answered 401 with a code of its own: it is a refusal to
  // show where it was typed, and says nothing about the session.
  if (response.status === 401 && !(body && body.code === 'bad_credentials')) {
    throw new NotSignedIn((body && body.message) || 'You are not signed in, or your session has ended.');
  }
  // A project's own reads and tools answer 404 only when the person is not in it: a tool that fails
  // for any other reason says so with a 400.
  if (response.status === 404 && /\/(tool|try)\/|\/index$/.test(path)) {
    throw new NotInProject((body && body.message) || 'That project is not one of yours.');
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
  mail = found.mail === true;
  providers = Array.isArray(found.providers) ? found.providers : [];
  createAccounts = found.sso_create_accounts !== false;
  if (!found.account) return null;
  csrf = found.csrf;
  return found;
}

// Whether the server can send email. Without it there is no way to reset a password by email, to
// send a verification or to email an invitation, so the board offers none of them.
export const mailOn = () => mail;

// The other ways to sign in the server offers, in the order it lists them. Without any, the board
// draws nothing about them.
export const providersOn = () => providers;
// Whether signing in with a provider for the first time may make an account.
export const createAccountsOn = () => createAccounts;

// The signed-in person as the server has them now (their email may have been verified in another
// tab since the page was drawn), or null.
export async function currentAccount() {
  const found = await send('/board/api/session', { method: 'GET' });
  return found.account || null;
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

// -- signing in with a provider ------------------------------------------------------------------------

// Ask where to send the browser to sign in with OpenID or GitHub (`intent` is signin, or link for a
// signed-in person connecting one to their account). Answers { url }; the page goes there. The way
// back is a redirect to `returnTo`, which the page reads in sso.js.
export const startProvider = (id, intent, returnTo) => post(`/board/api/auth/${encodeURIComponent(id)}/start`, returnTo ? { intent, return_to: returnTo } : { intent });
// Sign in with the name and password of a directory: the server checks them there. Same answer as signIn.
export const ldapSignIn = (username, password) => started('/board/api/auth/ldap', { username, password });
// How the signed-in person can sign in: { has_password, identities: [{ provider, name, display_name, email, linked_at }] }.
export const identities = () => send('/board/api/account/identities', { method: 'GET' });
// Connect a directory account to the signed-in person's account.
export const linkLdap = (username, password) => post('/board/api/account/identities/ldap', { username, password });
// Stop signing in with a provider. The server refuses to take away the last way in.
export const unlinkIdentity = (id) => send(`/board/api/account/identities/${encodeURIComponent(id)}`, { method: 'DELETE' });

// -- email: choosing a new password and confirming an address ---------------------------------------

// Ask for a link to choose a new password. The answer is the same words for an address that has an
// account and one that has not: { message }.
export const requestPasswordReset = (email) => post('/board/api/password-reset', { email });
// Whether the link from the email can still be used (the answer is { ok: true } or a refusal).
export const checkPasswordReset = (token) => send(`/board/api/password-reset/${encodeURIComponent(token)}`, { method: 'GET' });
export const resetPassword = (token, next) => post(`/board/api/password-reset/${encodeURIComponent(token)}`, { new: next });
// Email the signed-in person a link to confirm their address. Answers { message }.
export const sendVerification = () => post('/board/api/account/verify-email', {});
// Use the link from that email. Opening the page never uses it: this does. Answers { ok, email }.
export const verifyEmail = (token) => post(`/board/api/verify/${encodeURIComponent(token)}`, {});

// -- projects and keys ------------------------------------------------------------------------------

export const projects = async () => (await send('/board/api/projects', { method: 'GET' })).projects;
export const createProject = (name) => post('/board/api/projects', { name });
export const projectInfo = (id) => send(`/board/api/projects/${id}`, { method: 'GET' });
export const keys = async () => (await send(scoped('/keys'), { method: 'GET' })).keys;
// `tools` is the list of tool names the key may use, or null for the standard tools (the ones Vrum offers agents). `codeMode` is
// true for a key that calls Vrum by running code and false for one that calls the tools one at a time. `verifier` is true for a
// key whose agent checks other agents' work. `expiresOn` is the last day the key works (YYYY-MM-DD), or null for a key that never
// expires. Answers { key, secret }.
export const createKey = (name, tools = null, codeMode = false, verifier = false, expiresOn = null) => post(scoped('/keys'), { name, ...(tools ? { tools } : {}), code_mode: codeMode, verifier, ...(expiresOn ? { expires_on: expiresOn } : {}) });
// Change what a key may use, how it calls Vrum, whether it is a verifier's and the last day it works, from the agent's next request
// on. `changes` has `tools` (a list of names, or null for the standard tools), `code_mode`, `verifier` and/or `expires_on` (a day,
// or null for a key that never expires). Answers the key.
export const changeKey = async (id, changes) => (await send(scoped(`/keys/${id}`), { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(changes) })).key;
export const revokeKey = async (id) => (await send(scoped(`/keys/${id}`), { method: 'DELETE' })).key;

// -- the signed-in person's own account ---------------------------------------------------------------

// Change the name the person is shown as in every history. Answers with the account.
export const renameAccount = (name) => send('/board/api/account', { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify({ name }) });
// Change the password; the person's other sessions end.
export const changePassword = (current, next) => post('/board/api/account/password', { current, new: next });
// Set a first password for an account made by a provider, which has none to give.
export const setPassword = (next) => post('/board/api/account/password', { new: next });

// -- the people of a project and their invitations --------------------------------------------------

export const members = async () => (await send(scoped('/members'), { method: 'GET' })).members;
export const changeRole = async (accountId, role) => (await send(scoped(`/members/${accountId}`), { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify({ role }) })).member;
// Take a person out of the project; `me` is the signed-in person leaving it.
export const removeMember = async (accountId) => (await send(scoped(`/members/${accountId}`), { method: 'DELETE' })).member;
export const invites = async () => (await send(scoped('/invites'), { method: 'GET' })).invites;
// Answers { invite, token, emailed, email_problem }: the token is the one time the link can be made.
// With an address, the server also emails the link to it (only when it can send email).
export const createInvite = (role, email = '') => post(scoped('/invites'), email ? { role, email } : { role });
// Make a new link for the same invitation (the old one stops working) and email it again. Answers
// the same as createInvite.
export const resendInvite = (id) => post(scoped(`/invites/${id}/resend`), {});
export const revokeInvite = async (id) => (await send(scoped(`/invites/${id}`), { method: 'DELETE' })).invite;
// What the signed-in person would join with this invitation, and why they could not, if they could not.
export const previewInvite = async (token) => (await send(`/board/api/invites/${encodeURIComponent(token)}`, { method: 'GET' })).invite;
// Join the project the invitation is for. Answers the project, with the role the person has in it.
export const acceptInvite = async (token) => (await post(`/board/api/invites/${encodeURIComponent(token)}/accept`, {})).project;

// -- webhooks (the owner's) and email nudges (every member's) ------------------------------------------

// The project's webhooks, the kinds of news there are with the server's sentence for each, in the order to show them, and how many a
// project may have: { webhooks, kinds, most }. Only the owner may ask; the server refuses anyone else in words.
export const webhooks = () => send(scoped('/webhooks'), { method: 'GET' });
// Add a webhook for the chosen kinds of news. Answers { webhook, secret }: the secret is in this answer and nowhere else, ever.
export const addWebhook = (url, events) => post(scoped('/webhooks'), { url, events });
// Change the kinds a webhook is sent for (`events`) or turn it off or on (`enabled`). Answers the webhook.
export const changeWebhook = async (id, changes) => (await send(scoped(`/webhooks/${id}`), { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(changes) })).webhook;
export const deleteWebhook = (id) => send(scoped(`/webhooks/${id}`), { method: 'DELETE' });
// A new secret; the old one keeps signing for a day. Answers { webhook, secret }, the secret shown this once.
export const replaceWebhookSecret = (id) => post(scoped(`/webhooks/${id}/secret`), {});
// Send one message now, once, and say how it went: { delivered, status, words }. It can take up to ten seconds.
export const testWebhook = (id) => post(scoped(`/webhooks/${id}/test`), {});
// What was sent to a webhook, newest first.
export const webhookDeliveries = async (id, limit = 20) => (await send(scoped(`/webhooks/${id}/deliveries?limit=${limit}`), { method: 'GET' })).deliveries;
// The signed-in person's own email nudges in every project they are in, and what they may ask for: { nudges, kinds, mail,
// email_confirmed }.
export const nudges = () => send('/board/api/nudges', { method: 'GET' });
// Ask to be mailed about these kinds of news in one project (an empty list stops them). Answers { project, events }.
export const setNudges = async (projectNumber, events) => (await send(`/board/api/projects/${projectNumber}/nudges`, { method: 'PUT', headers: JSON_HEADERS, body: JSON.stringify({ events }) })).nudge;

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

// What does not belong to a project: how often to ask for news.
export function get(path) {
  return send(path, { method: 'GET' });
}

// The lists whose pages are cut by the reference of the last item (epics, stories and tasks);
// every other list is cut by the id of its last item.
const BY_REFERENCE = new Set(['list_nodes', 'ready_work']);

// Every item of a list that a tool gives a page at a time.
export async function callAll(tool, args) {
  const [cursor, next] = BY_REFERENCE.has(tool) ? ['after', 'next_after'] : ['after_id', 'next_after_id'];
  const items = [];
  let after = null;
  do {
    const page = await call(tool, { ...args, limit: 200, [cursor]: after });
    items.push(...page.items);
    after = page[next];
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

// Every item of one list of the export (links, rules or notes), which is the one
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
