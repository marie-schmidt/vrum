// Users: who is in this project, and how each of them shows in its history. One page in three
// sections.
//
//   Account  the signed-in person: the name they are shown as, their email (which cannot be changed
//            yet, and which the server can email a link to confirm when it can send email), their
//            password (or, when the server offers other ways to sign in, their sign-in methods:
//            the password and each provider, connected or not, in methods.js), and signing out.
//   Members  the people of the project, their roles and the invitations (members.js).
//   Agents   the project's agent identities. An agent is a key: the key's name is the name the
//            history shows for whatever uses it, so one key for each agent says which agent did
//            what. A key is shown once, when it is made, and only a hash of it is kept, so a lost
//            key is revoked and replaced. Revoking takes effect on the agent's next request. A key
//            has the standard tools Vrum offers agents, or only the ones the owner ticks, which may
//            be fewer or include tools that are done on the board (key-tools.js), chosen when it is
//            made and changed later from the key list, from the agent's next request on. Only the
//            owner makes, changes and revokes keys. How to connect an agent is the last block of
//            this section.
//
// What is recorded under which name is decided by the server from who is signed in or which key is
// used: nothing on this page, or any other, lets anyone type a name for the history.

import { Refusal, Stale, changeKeyTools, changePassword, createKey, currentAccount, keys, mailOn, renameAccount, revokeKey, sendVerification } from './api.js';
import { copyButton } from './copy.js';
import { account, can, notify } from './ctx.js';
import { h, moment, put } from './dom.js';
import { dialogForm, field, openDialog, refuse } from './forms.js';
import { icon } from './icons.js';
import { allowance, forgetToolListing, keyTools, toolListing } from './key-tools.js';
import { actorLabel } from './labels.js';
import * as members from './members.js';
import { forgetMethods, methodsControl, showsMethods } from './methods.js';
import { pageHeader } from './page.js';
import { updateAccount } from './sidebar.js';

let root = null;
let parts = {};
let found = [];
let fresh = null; // the key just made, which is shown until the person leaves the page
let listing = null; // what the server says about its tools, for what a key may use
let picker = null; // the picker of the form that makes a key
let sentTo = ''; // the address a verification email was just sent to, said under the Email setting
let generation = 0;

const address = () => `${location.origin}/mcp`;

export async function mount(element) {
  root = element;
  fresh = null;
  found = [];
  listing = null;
  picker = null;
  sentTo = '';
  forgetToolListing();
  const mine = ++generation;
  parts = {
    account: h('section', { id: 'users-account', class: 'settings-section users-section', 'aria-labelledby': 'users-account-title' }),
    members: h('section', { id: 'users-members', class: 'settings-section users-section', 'aria-labelledby': 'users-members-title' }),
    agents: h('section', { id: 'users-agents', class: 'settings-section users-section', 'aria-labelledby': 'users-agents-title' }),
    keys: h('div', { id: 'key-list' }, h('p', { class: 'loading', role: 'status' }, 'Loading the keys…')),
    fresh: h('section', { id: 'new-key', class: 'key-reveal', 'aria-labelledby': 'new-key-title', hidden: true }),
  };
  drawAccount();
  drawAgents();
  put(
    root,
    h('div', { class: 'settings-page users-page' }, [
      pageHeader('Users'),
      parts.account,
      parts.members,
      parts.agents,
    ]),
  );
  // The address may have been confirmed in another tab or on a phone: look again when the person comes back to this one.
  document.addEventListener('visibilitychange', comeBack);
  rereadAccount();
  await Promise.all([members.mount(parts.members), can('manage') ? loadKeys(mine) : null]);
}

export function unmount() {
  generation += 1;
  forgetMethods();
  document.removeEventListener('visibilitychange', comeBack);
  root = null;
  fresh = null;
  parts = {};
  found = [];
  listing = null;
  picker = null;
  members.unmount();
}

export function update() {}

// An agent's work is news, and a key that was just used shows when: look at the keys again. Who is in
// the project may have changed too.
export function refresh() {
  if (!root) return;
  members.refresh();
  if (can('manage')) loadKeys(generation).catch(() => {});
}

// -- the account -----------------------------------------------------------------------------------------

// One line of settings: what it is and what it is for on the left, the thing to do on the right.
function setting(id, title, hint, ...control) {
  return h(
    'div',
    { class: 'setting', id },
    h('div', { class: 'setting-text' }, title, hint ? h('p', { class: 'hint' }, hint) : null),
    h('div', { class: 'setting-control' }, ...control),
  );
}

const settingTitle = (text, props = {}) => h('p', { class: 'setting-title', ...props }, text);

// The Email setting. The address cannot be changed yet. When the server can send email it also says
// whether the address is confirmed, and offers to email a link that confirms it. Without email
// there is no way to confirm an address, so nothing about confirming is drawn.
function emailSetting() {
  const verified = account.email_verified === true;
  const mailing = mailOn();
  const chip = verified
    ? h('span', { class: 'chip ok email-state', id: 'email-state', dataset: { state: 'verified' } }, icon('check', { size: 14 }), 'Verified')
    : mailing
      ? h('span', { class: 'chip warn email-state', id: 'email-state', dataset: { state: 'unverified' } }, icon('alert', { size: 14 }), 'Not verified yet')
      : null;
  const problem = h('p', { class: 'problem', role: 'alert', id: 'verify-problem' });
  const note = h('p', { class: 'mail-note', id: 'verify-note', role: 'status' });
  if (sentTo) put(note, icon('check', { size: 16 }), h('span', {}, sentTo));
  const send = h(
    'button',
    {
      type: 'button',
      id: 'send-verification',
      onclick: async () => {
        problem.replaceChildren();
        send.disabled = true;
        try {
          const answer = await sendVerification();
          sentTo = answer.message;
          put(note, icon('check', { size: 16 }), h('span', {}, sentTo));
          put(send, icon('mail', { size: 16 }), 'Send it again');
        } catch (error) {
          if (!(error instanceof Refusal)) throw error;
          refuse(problem, error.message);
          // Already confirmed in another tab: the answer says so, and the setting should too.
          rereadAccount();
        } finally {
          send.disabled = false;
        }
      },
    },
    icon('mail', { size: 16 }),
    sentTo ? 'Send it again' : 'Send verification email',
  );
  return setting(
    'setting-email',
    settingTitle('Email'),
    null,
    h('p', { class: 'setting-value email-line' }, h('span', { id: 'account-email-shown' }, account.email), chip),
    mailing && !verified ? send : null,
    note,
    problem,
  );
}

// Draw the Email setting again, with what the server says now about the address.
async function rereadAccount() {
  if (!mailOn()) return;
  const mine = generation;
  try {
    const now = await currentAccount();
    if (mine !== generation || !root || !now || (now.email_verified === true) === (account.email_verified === true)) return;
    account.email_verified = now.email_verified;
    sentTo = '';
    root.querySelector('#setting-email')?.replaceWith(emailSetting());
  } catch {
    // Not being able to ask changes nothing on the page: it is asked again when the person comes back.
  }
}

const comeBack = () => {
  if (document.visibilityState === 'visible') rereadAccount();
};

function drawAccount() {
  const name = h('input', { type: 'text', id: 'account-display-name', name: 'name', autocomplete: 'name', value: account.name });
  const problem = h('p', { class: 'problem', role: 'alert', id: 'account-name-problem' });
  const save = h('button', { type: 'submit', class: 'primary', id: 'save-name' }, 'Save');
  const rename = async (event) => {
    event.preventDefault();
    problem.replaceChildren();
    save.disabled = true;
    try {
      const made = await renameAccount(name.value);
      account.name = made.name;
      name.value = made.name;
      updateAccount(made.name);
      notify(`You are shown as “${made.name}” from now on.`);
    } catch (error) {
      if (!(error instanceof Refusal)) throw error;
      refuse(problem, error.message);
    } finally {
      save.disabled = false;
    }
  };
  put(
    parts.account,
    h('h2', { id: 'users-account-title' }, 'Account'),
    h('div', { class: 'settings-list' }, [
      setting(
        'setting-name',
        h('label', { for: 'account-display-name', class: 'setting-title' }, 'Name'),
        null,
        h('form', { id: 'name-form', class: 'setting-form', novalidate: true, onsubmit: rename }, name, save),
        problem,
      ),
      emailSetting(),
      // With another way to sign in (or an account that has no password) the password is one of the
      // sign-in methods; otherwise it is the one setting it has always been.
      showsMethods()
        ? setting('setting-methods', settingTitle('Sign-in methods'), null, methodsControl(passwordDialog))
        : setting('setting-password', settingTitle('Password'), null, h('button', { type: 'button', id: 'change-password', onclick: passwordDialog }, icon('lock', { size: 16 }), 'Change password…')),
    ]),
  );
}

function passwordDialog() {
  const current = h('input', { type: 'password', id: 'password-current', name: 'current', autocomplete: 'current-password' });
  const next = h('input', { type: 'password', id: 'password-new', name: 'new', autocomplete: 'new-password' });
  const again = h('input', { type: 'password', id: 'password-again', name: 'again', autocomplete: 'new-password' });
  const { form } = dialogForm({
    fields: [
      field('Current password', current),
      field('New password', next, 'At least 10 characters.'),
      field('The new password again', again),
    ],
    submit: 'Change password',
    work: async () => {
      if (next.value !== again.value) throw new Refusal('The two new passwords are not the same. Nothing was changed.');
      await changePassword(current.value, next.value);
      form.closest('dialog')?.close();
      notify('Your password was changed. Your other sessions have ended.');
    },
  });
  openDialog('Change your password', [form]);
  current.focus();
}

// -- the agents --------------------------------------------------------------------------------------------

// Only the owner makes and revokes agent keys, so anyone else is told so, and still sees how an agent
// connects (a key is what they would be given).
function connectBlock() {
  const endpoint = h('code', { id: 'mcp-address' }, address());
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
  );
}

function drawAgents() {
  if (!can('manage')) {
    put(
      parts.agents,
      h('h2', { id: 'users-agents-title' }, 'Agents'),
      h('p', { id: 'keys-owner-only', class: 'help' }, 'Only the project’s owner makes, changes and revokes agent keys.'),
      connectBlock(),
    );
    return;
  }
  const name = h('input', { type: 'text', id: 'key-name', name: 'name', autocomplete: 'off', placeholder: 'review-agent' });
  const problem = h('p', { class: 'problem', role: 'alert', id: 'key-problem' });
  // The key cannot be made until the list of tools has come, since what it may do is part of making it.
  const submit = h('button', { type: 'submit', class: 'primary', id: 'create-key', disabled: true }, icon('key', { size: 16 }), 'Create key');
  parts.picker = h('div', { id: 'key-tools-slot' }, h('p', { class: 'loading', role: 'status' }, 'Loading the tools…'));
  parts.submit = submit;
  put(
    parts.agents,
    h('h2', { id: 'users-agents-title' }, 'Agents'),
    parts.keys,
    h(
      'div',
      { class: 'subsection', id: 'make-key' },
      h('h3', { id: 'make-key-title' }, 'New agent key'),
      h(
        'form',
        {
          id: 'key-form',
          class: 'make-key-form',
          novalidate: true,
          'aria-labelledby': 'make-key-title',
          onsubmit: async (event) => {
            event.preventDefault();
            if (!picker) return;
            problem.replaceChildren();
            submit.disabled = true;
            try {
              const made = await createKey(name.value, picker.value());
              fresh = made;
              name.value = '';
              picker.reset();
              await loadKeys(generation);
              drawFresh();
            } catch (error) {
              if (!(error instanceof Refusal)) throw error;
              refuse(problem, error.message);
            } finally {
              submit.disabled = !picker;
            }
          },
        },
        field('Agent name', name),
        h(
          'div',
          { class: 'key-step', role: 'group', 'aria-labelledby': 'key-tools-title' },
          h('h4', { id: 'key-tools-title' }, 'What may this agent do?'),
          parts.picker,
        ),
        submit,
        problem,
      ),
      parts.fresh,
    ),
    connectBlock(),
  );
}

// The picker of the form that makes a key, once the server's tool list is here. It is made once, so
// that a change in the keys never loses what has been chosen.
function drawPicker(failure) {
  if (!root || !parts.picker || picker) return;
  if (failure) {
    const failed = h('p', { class: 'problem', role: 'alert' });
    refuse(failed, failure instanceof Refusal ? failure.message : 'The list of tools could not be read. Nothing was changed.');
    put(parts.picker, failed, h('button', { type: 'button', id: 'key-tools-retry', onclick: () => loadKeys(generation).catch(() => {}) }, 'Try again'));
    return;
  }
  picker = keyTools({ list: listing, prefix: 'key-tools' });
  put(parts.picker, picker.element);
  parts.submit.disabled = false;
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
    h('details', { open: true, id: 'key-example' }, h('summary', {}, 'Connecting an agent with it'), h('pre', { tabindex: '0' }, h('code', {}, command))),
  );
  parts.fresh.tabIndex = -1;
  parts.fresh.focus();
}

async function loadKeys(mine) {
  let list;
  let tools;
  try {
    [list, tools] = await Promise.all([keys(), toolListing().then((got) => ({ got }), (error) => ({ error }))]);
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
  listing = tools.got || null;
  drawKeys();
  if (listing) drawPicker();
  else if (!(tools.error instanceof Stale)) drawPicker(tools.error);
}

// A key's allowance in the dialog the owner changes it in. It is saved with the server's own checks,
// so a refusal is shown here, in its words, and nothing is lost.
function allowanceDialog(key) {
  if (!listing) {
    notify('The list of tools has not come yet. Try again in a moment.', { refusal: true });
    return;
  }
  const choice = keyTools({ list: listing, tools: key.tools, prefix: 'key-tools-change', open: true });
  const { form } = dialogForm({
    fields: [choice.element],
    submit: 'Save',
    work: async () => {
      await changeKeyTools(key.id, choice.value());
      form.closest('dialog')?.close();
      notify('Saved. It applies from the agent’s next request.');
      await loadKeys(generation);
    },
  });
  const box = openDialog(`What may “${key.name}” do?`, [form], { wide: true });
  box.id = 'change-tools-dialog';
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

function keyRow(key) {
  const live = key.revoked_at === null;
  const may = allowance(listing, key.tools);
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
        h('span', { class: 'chip key-allowance', role: 'img', 'aria-label': `May do: ${may.label}`, title: 'What this key may do', dataset: { allowance: may.choice } }, may.label),
      ),
      h('span', { class: 'quiet' }, `Created ${moment(key.created_at)}; ${key.last_used_at ? `last used ${moment(key.last_used_at)}` : 'never used'}`),
    ),
    h(
      'div',
      { class: 'key-side' },
      live ? h('span', { class: 'chip ok status-active' }, icon('check', { size: 14 }), 'Active') : h('span', { class: 'chip warn status-revoked' }, icon('lock', { size: 14 }), `Revoked ${moment(key.revoked_at)}`),
      live ? h('button', { type: 'button', class: 'change-tools small', onclick: () => allowanceDialog(key) }, 'Change what it may do…') : null,
      live ? h('button', { type: 'button', class: 'revoke small danger', onclick: () => revokeDialog(key) }, 'Revoke…') : null,
    ),
  );
}

function drawKeys() {
  // The ones in force first: they are what the owner looks for. Revoked ones stay below, as history.
  const ordered = [...found].sort((a, b) => Number(a.revoked_at !== null) - Number(b.revoked_at !== null));
  put(
    parts.keys,
    h('h3', { id: 'keys-title' }, 'Agent keys', h('span', { class: 'heading-count' }, ` (${found.length})`)),
    found.length ? h('ul', { id: 'keys', class: 'plain keys', 'aria-labelledby': 'keys-title' }, ordered.map(keyRow)) : h('p', { id: 'no-keys', class: 'empty' }, 'No agent keys yet.'),
  );
}
