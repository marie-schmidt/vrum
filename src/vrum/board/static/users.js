// Users: who is in this project, and how each of them shows in its history. One page in two
// sections.
//
//   Members  the people of the project, their roles and the invitations (members.js).
//   Webhooks (the owner only) the addresses that are sent a signed nudge when something needs someone (webhooks.js).
//   Agents   the project's agent identities. An agent is a key: the key's name is the name the
//            history shows for whatever uses it, so one key for each agent says which agent did
//            what. A key is shown once, when it is made, and only a hash of it is kept, so a lost
//            key is revoked and replaced. Revoking takes effect on the agent's next request. A key
//            has the standard tools Vrum offers agents, or only the ones the owner ticks, which may
//            be fewer or include tools that are done on the board (key-tools.js), chosen when it is
//            made and changed later from the key list, from the agent's next request on. A key also
//            has one way of calling Vrum: by running code, or one tool at a time. Both are chosen
//            when the key is made (neither is chosen when the dialog opens) and changed from the
//            same place later. A key may also be marked a verifier's (an agent that checks other
//            agents' work: it may tick the checks of the definition of done tagged [verifier] or
//            [github]); the box is unchecked when the dialog opens, and only a key that is one shows
//            a chip. A key may also have a last day it works (the Expires field, empty when the dialog
//            opens: a key with no day never expires and says nothing about it); a key with a day shows
//            one chip, and the danger chip instead once the server says it has expired, which the owner
//            ends by choosing a later day in the Change dialog. Only the owner makes, changes and
//            revokes keys. How to connect an
//            agent (the endpoint, the header and an example configuration) is the last block of
//            this section, and is said nowhere else.
//
// The person's own name, email and ways to sign in are on the Account page (account.js).
//
// What is recorded under which name is decided by the server from who is signed in or which key is
// used: nothing on this page, or any other, lets anyone type a name for the history.

import { Refusal, Stale, changeKey, createKey, keys, revokeKey } from './api.js';
import { copyButton } from './copy.js';
import { can, notify } from './ctx.js';
import { h, moment, put } from './dom.js';
import { dialogForm, field, openDialog, refuse } from './forms.js';
import { hoverCard } from './hovercard.js';
import { icon } from './icons.js';
import { allowance, callingWords, forgetToolListing, keyCalls, keyExpiry, keyTools, keyVerifier, toolListing } from './key-tools.js';
import { actorLabel, expiryChip, verifierChip } from './labels.js';
import * as members from './members.js';
import { pageHeader } from './page.js';
import * as webhooks from './webhooks.js';

let root = null;
let parts = {};
let found = [];
let fresh = null; // the key just made, which is shown until the person leaves the page
let listing = null; // what the server says about its tools, for what a key may use
let revokedOpen = false; // the folded list of revoked keys has been opened
let generation = 0;

const address = () => `${location.origin}/mcp`;

export async function mount(element) {
  root = element;
  fresh = null;
  found = [];
  listing = null;
  revokedOpen = false;
  forgetToolListing();
  const mine = ++generation;
  parts = {
    members: h('section', { id: 'users-members', class: 'settings-section users-section', 'aria-labelledby': 'users-members-title' }),
    agents: h('section', { id: 'users-agents', class: 'settings-section users-section', 'aria-labelledby': 'users-agents-title' }),
    // Webhooks are the owner's: anyone else has no such section and nothing is asked for them.
    webhooks: can('manage') ? h('section', { id: 'users-webhooks', class: 'settings-section users-section', 'aria-labelledby': 'users-webhooks-title' }) : null,
    keys: h('div', { id: 'key-list' }, h('p', { class: 'loading', role: 'status' }, 'Loading the keys…')),
    fresh: h('section', { id: 'new-key', class: 'key-reveal', 'aria-labelledby': 'new-key-title', hidden: true }),
  };
  drawAgents();
  put(root, h('div', { class: 'settings-page users-page' }, [pageHeader('Users'), parts.members, parts.agents, parts.webhooks]));
  await Promise.all([members.mount(parts.members), can('manage') ? loadKeys(mine) : null, parts.webhooks ? webhooks.mount(parts.webhooks) : null]);
}

export function unmount() {
  generation += 1;
  root = null;
  fresh = null;
  parts = {};
  found = [];
  listing = null;
  members.unmount();
  webhooks.unmount();
}

export function update() {}

// An agent's work is news, and a key that was just used shows when: look at the keys again. Who is in
// the project may have changed too.
export function refresh() {
  if (!root) return;
  members.refresh();
  if (can('manage')) {
    loadKeys(generation).catch(() => {});
    webhooks.refresh();
  }
}

// -- the agents --------------------------------------------------------------------------------------------

// Only the owner makes and revokes agent keys, so anyone else is told so, and still sees how an agent
// connects (a key is what they would be given). This is the one place the board says how to connect.
function connectBlock() {
  const endpoint = h('code', { id: 'mcp-address' }, address());
  const command = h('code', {}, `claude mcp add --transport http vrum ${address()} --header "Authorization: Bearer $VRUM_AGENT_KEY"`);
  const file = h('code', {}, JSON.stringify({ mcpServers: { vrum: { type: 'http', url: address(), headers: { Authorization: 'Bearer ${VRUM_AGENT_KEY}' } } } }, null, 2));
  return h(
    'div',
    { class: 'subsection', id: 'connect' },
    h('h3', { id: 'connect-title' }, 'Connect an agent'),
    h(
      'dl',
      { class: 'connect' },
      h('dt', {}, 'Endpoint'),
      h('dd', { class: 'endpoint-line' }, endpoint, copyButton(endpoint, 'Copy the endpoint address')),
      h('dt', {}, 'Header'),
      h('dd', {}, h('code', {}, 'Authorization: Bearer <the agent key>')),
    ),
    h(
      'details',
      { id: 'example' },
      h('summary', {}, 'An example client configuration'),
      h('div', { class: 'example-head' }, h('p', {}, 'Claude Code'), copyButton(command, 'Copy the command')),
      h('pre', {}, command),
      h('div', { class: 'example-head' }, h('p', {}, 'Project file (.mcp.json)'), copyButton(file, 'Copy the project file')),
      h('pre', {}, file),
    ),
  );
}

function drawAgents() {
  const owner = can('manage');
  put(
    parts.agents,
    h(
      'div',
      { class: 'section-head' },
      h('h2', { id: 'users-agents-title' }, 'Agents'),
      h('div', { class: 'section-actions', id: 'agents-actions' }, owner ? h('button', { type: 'button', id: 'key-open', class: 'primary', onclick: newKeyDialog }, icon('plus', { size: 16 }), 'New agent key') : null),
    ),
    owner ? [parts.fresh, parts.keys] : h('p', { id: 'keys-owner-only', class: 'help' }, 'Only the project’s owner makes, changes and revokes agent keys.'),
    connectBlock(),
  );
}

// The form that makes a key: its name, how it calls Vrum and what it may do. The key cannot be made until the
// list of tools has come, since what it may do is part of making it. Neither choice is made when the dialog
// opens, so each is refused in words, with the rest kept, until it is. The key made is shown once, above the list.
async function newKeyDialog() {
  try {
    listing = listing || (await toolListing());
  } catch (error) {
    if (error instanceof Stale) return;
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
    return;
  }
  const name = h('input', { type: 'text', id: 'key-name', name: 'name', autocomplete: 'off', placeholder: 'review-agent' });
  const calls = keyCalls({ prefix: 'key-call' });
  const choice = keyTools({ list: listing, prefix: 'key-tools', blank: true });
  const verifies = keyVerifier({ prefix: 'key-verifier' });
  const expires = keyExpiry({ prefix: 'key-expires' });
  const { form } = dialogForm({
    fields: [field('Agent name', name), calls.element, h('div', { class: 'key-step', role: 'group', 'aria-labelledby': 'key-tools-title' }, h('h3', { id: 'key-tools-title' }, 'What may this agent do?'), choice.element), verifies.element, expires.element],
    submit: 'Create key',
    work: async () => {
      if (!calls.chosen()) throw new Refusal('Choose how it calls Vrum.');
      if (!choice.chosen()) throw new Refusal('Choose what this agent may do.');
      const made = await createKey(name.value, choice.value(), calls.value(), verifies.value(), expires.value());
      form.closest('dialog')?.close();
      fresh = made;
      await loadKeys(generation);
      drawFresh();
    },
  });
  form.id = 'key-form';
  form.querySelector('button[type="submit"]').id = 'create-key';
  const box = openDialog('New agent key', [form], { wide: true });
  box.id = 'new-key-dialog';
  name.focus();
}

// The key just made. It is brought into view (it may be below the form on a small window) and read
// out, so that nobody misses that it is not shown again.
function drawFresh() {
  if (!root || !fresh) return;
  const secret = h('code', { id: 'key-secret', class: 'secret' }, fresh.secret);
  const command = `export VRUM_AGENT_KEY=${fresh.secret}\nclaude mcp add --transport http vrum ${address()} --header "Authorization: Bearer $VRUM_AGENT_KEY"`;
  const may = allowance(listing, fresh.key.tools);
  parts.fresh.hidden = false;
  put(
    parts.fresh,
    h('h4', { id: 'new-key-title' }, icon('key', { size: 18 }), `Your new key for “${fresh.key.name}”`),
    h('p', { class: 'help' }, icon('alert', { size: 16 }), 'Copy it now. It is not shown again.'),
    h('div', { class: 'secret-line' }, secret, copyButton(secret, 'Copy the key')),
    h(
      'p',
      { id: 'new-key-allowed', class: 'key-allowed' },
      'What it may do: ',
      h('strong', {}, may.label),
      '.',
    ),
    h('p', { id: 'new-key-calls', class: 'key-allowed' }, 'How it calls Vrum: ', h('strong', {}, callingWords(fresh.key.code_mode)), '.'),
    h('details', { open: true, id: 'key-example' }, h('summary', {}, 'Connecting an agent with it'), h('pre', { tabindex: '0' }, h('code', {}, command))),
  );
  parts.fresh.tabIndex = -1;
  parts.fresh.focus();
}

async function loadKeys(mine) {
  let list;
  let tools;
  try {
    // The keys are listed even when the list of tools cannot be read: it only says what each may do, and making a key asks for it again.
    [list, tools] = await Promise.all([keys(), toolListing().catch(() => null)]);
  } catch (error) {
    if (mine !== generation || !root || error instanceof Stale) return;
    if (!(error instanceof Refusal)) throw error;
    const failed = h('p', { class: 'problem', role: 'alert' });
    refuse(failed, error.message);
    put(parts.keys, failed);
    return;
  }
  if (mine !== generation || !root) return;
  found = list;
  listing = tools;
  drawKeys();
}

// How a key calls Vrum, what it may use, whether it verifies other agents' work and the last day it works, in the dialog the owner
// changes them in. Only what changed is sent. It is saved with the server's own checks, so a refusal is shown here, in its words, and
// nothing is lost.
function allowanceDialog(key) {
  if (!listing) {
    notify('The list of tools has not come yet. Try again in a moment.', { refusal: true });
    return;
  }
  const calls = keyCalls({ prefix: 'key-call-change', codeMode: Boolean(key.code_mode) });
  const choice = keyTools({ list: listing, tools: key.tools, prefix: 'key-tools-change' });
  const verifies = keyVerifier({ prefix: 'key-verifier-change', verifier: Boolean(key.verifier) });
  const expires = keyExpiry({ prefix: 'key-expires-change', expiresOn: key.expires_on, expired: key.expired });
  const { form } = dialogForm({
    fields: [calls.element, h('div', { class: 'key-step', role: 'group', 'aria-labelledby': 'key-tools-change-title' }, h('h3', { id: 'key-tools-change-title' }, 'What may this agent do?'), choice.element), verifies.element, expires.element],
    submit: 'Save',
    work: async () => {
      const changes = {};
      if (calls.value() !== Boolean(key.code_mode)) changes.code_mode = calls.value();
      if (verifies.value() !== Boolean(key.verifier)) changes.verifier = verifies.value();
      if (!sameTools(choice.value(), key.tools)) changes.tools = choice.value();
      if (expires.changed()) changes.expires_on = expires.value();
      // With nothing changed at all the tools are sent as they are, as saving this dialog always did.
      if (!Object.keys(changes).length) changes.tools = choice.value();
      await changeKey(key.id, changes);
      form.closest('dialog')?.close();
      notify('Saved. It applies from the agent’s next request.');
      await loadKeys(generation);
    },
  });
  const box = openDialog(`Change “${key.name}”`, [form], { wide: true });
  box.id = 'change-tools-dialog';
}

// Whether two allowances are the same: both the standard tools (null), or the same names.
function sameTools(a, b) {
  if (!a || !b) return !a && !b;
  const names = new Set(b);
  return a.length === names.size && a.every((name) => names.has(name));
}

function revokeDialog(key) {
  const { form } = dialogForm({
    fields: [h('p', {}, `Revoke “${key.name}” (${key.hint}…)? The agent using it is refused on its next request, including one that is connected now. This cannot be undone: make a new key for an agent that should keep working.`)],
    submit: 'Revoke key',
    work: async () => {
      await revokeKey(key.id);
      form.closest('dialog')?.close();
      notify(`Revoked “${key.name}”.`);
      await loadKeys(generation);
    },
  });
  openDialog('Revoke a key', [form]);
}

// The chip of a key whose agent checks other agents' work, with what that means in a hover card. Only such a key has one: a
// key that does not verify says nothing about it.
function verifierBadge() {
  const chip = verifierChip();
  chip.setAttribute('role', 'img');
  chip.setAttribute('aria-label', 'Verifier');
  chip.tabIndex = 0;
  chip.dataset.verifier = 'true';
  return [chip, hoverCard(chip, [h('p', { class: 'hovercard-title' }, 'Verifier'), h('p', {}, 'Verifies other agents’ work: it may tick the checks marked [verifier] or [github] on work it did not do itself.')])];
}

// One key: its name and the start of the key, then, quietly, how it calls Vrum and when it was last used. What it may
// do is said only when it is not the standard tools, which is what a key has unless the owner chose otherwise.
function keyRow(key) {
  const live = key.revoked_at === null;
  const may = allowance(listing, key.tools);
  const when = live ? (key.last_used_at ? `Last used ${moment(key.last_used_at)}` : 'Never used') : `Revoked ${moment(key.revoked_at)}`;
  return h(
    'li',
    { class: `key${live ? '' : ' revoked'}`, dataset: { key: key.id, state: live ? 'active' : 'revoked' } },
    h(
      'div',
      { class: 'key-main' },
      h(
        'div',
        { class: 'key-name' },
        h('strong', { class: 'label' }, actorLabel(key.name, 'agent', { size: 16 })),
        h('code', { class: 'hint-code' }, `${key.hint}…`),
        live && may.choice !== 'standard' ? h('span', { class: 'chip key-allowance', role: 'img', 'aria-label': `May do: ${may.label}`, title: 'What this key may do', dataset: { allowance: may.choice } }, may.label) : null,
        live && key.verifier ? verifierBadge() : null,
        live ? expiryChip(key) : null,
      ),
      h(
        'div',
        { class: 'key-facts' },
        h('span', { class: 'quiet key-calls', dataset: { calls: key.code_mode ? 'code' : 'tools' } }, callingWords(key.code_mode)),
        h('span', { class: 'quiet key-when', title: `Created ${moment(key.created_at)}` }, when),
      ),
    ),
    live
      ? h(
          'div',
          { class: 'key-side' },
          h('button', { type: 'button', class: 'change-tools small', title: 'Change what it may do, how it calls Vrum and when it expires', onclick: () => allowanceDialog(key) }, 'Change…'),
          h('button', { type: 'button', class: 'revoke small danger', onclick: () => revokeDialog(key) }, 'Revoke…'),
        )
      : null,
  );
}

function drawKeys() {
  // The ones in force are what the owner looks for. Revoked ones are history: they stay, folded, below.
  const live = found.filter((key) => key.revoked_at === null);
  const revoked = found.filter((key) => key.revoked_at !== null);
  const folded = h('details', { id: 'revoked-keys', class: 'revoked-keys', open: revokedOpen, ontoggle: (event) => { revokedOpen = event.target.open; } }, h('summary', {}, `Revoked keys (${revoked.length})`), h('ul', { id: 'revoked', class: 'plain keys' }, revoked.map(keyRow)));
  put(
    parts.keys,
    h('h3', { id: 'keys-title', class: 'sr-only' }, 'Agent keys'),
    live.length ? h('ul', { id: 'keys', class: 'plain keys', 'aria-labelledby': 'keys-title' }, live.map(keyRow)) : h('p', { id: 'no-keys', class: 'empty' }, found.length ? 'No agent key is in force.' : 'No agent keys yet.'),
    revoked.length ? folded : null,
  );
}
